#!/usr/bin/env python3
"""Publish a fork port's PROMOTED release as an unofficial community version on Modrinth and CurseForge.

    port-publish.py setup   --repo <fork clone> --permission <url> [--branch neoforge-1.21.1]
    port-publish.py modrinth-create --repo .           # CI: create the Modrinth draft project (MODRINTH_TOKEN)
    port-publish.py publish --repo . --tag <tag> [--dry-run] [--manual]   # CI: upload one promoted release
    port-publish.py --self-check

The fork carries its publishing config as .github/port-publish.json (written by `setup`, reviewed, committed):

  {"schema": 1, "permission": "<url where the authors agreed>", "auto": true,
   "title": "...", "summary": "...", "body_file": ".github/port-publish/description.md", "icon": "<path in repo>",
   "license": "MIT", "categories": [...], "additional_categories": [...], "environment": "client_and_server",
   "ledger_issues": ["owner/fork#1", ...],
   "dependencies": [{"modrinth": "geckolib", "curseforge": "geckolib", "type": "required"},
                    {"port": "owner/other-fork", "type": "required"}],
   "modrinth": {"slug": "...", "asset": "_mr.jar"},
   "curseforge": {"project_id": 123456, "slug": "...", "asset": "_cf.jar"}}

A MULTI-VERSION fork (the opt-in `.multiversion/` layer, tools/make-fork-layer.py) also adds a "layer" section:

  "layer": {"branch": "neoforge-1.21.x", "targets": ["1.21.4", ...optional: default every green target],
            "released": ["1.21.8", ...targets whose full gates were run and recorded in their GATES.md]}

and then `publish` uploads ONE FILE PER GREEN TARGET (see publish_layer). Both are needed: the tag's commit must
carry `.multiversion/base.properties` AND the config must have the "layer" section. A single-version fork has
neither, and every code path below that is not named *layer* is byte-for-byte what it was.

`setup` writes that file and the project page from data the port already has (neoforge.mods.toml, the
author's own project on Modrinth, the tested dependencies in .github/port-install.json, the fix ledgers), and
prints the fields to paste into CurseForge's create-project form (CurseForge has no API for creating one).

`publish` REFUSES, and uploads nothing, unless every one of these holds:
  - the config names a permission link (a permissive licence makes it legal; the platforms and courtesy still
    want the authors' agreement on record);
  - the tag is a full Release (not a pre-release, not a draft) published by this fork's own CI, and the tag is
    an ancestor of the checked-out port branch;
  - every fix ledger the config lists has a column naming this tag and no S1 mode (crash, kick, hang, save
    damage, other mods broken) is open in it -- fix-ledger.py's own check;
  - each dependency that is another fork port already has the version this release was built against published
    on that platform.
It is idempotent: a platform this release is already published on (a marker in the release notes) is skipped,
so a re-run after a partial failure only does what is missing. After uploading it appends the links to the
release notes. Tokens come from the environment (MODRINTH_TOKEN, CURSEFORGE_TOKEN, GITHUB_TOKEN) -- never
arguments, never printed. A platform with no project yet (CurseForge before the person has created it) is
skipped with a notice, not failed. Standard library only.
"""
import argparse, datetime, importlib.util, json, os, pathlib, re, subprocess, sys, tomllib, urllib.error
import urllib.parse, urllib.request, uuid

ROOT = pathlib.Path(__file__).resolve().parent
CONFIG = ".github/port-publish.json"
BODY = ".github/port-publish/description.md"
MODRINTH = "https://api.modrinth.com/v2"
CURSEFORGE = "https://minecraft.curseforge.com/api"
GITHUB = "https://api.github.com"
UA = "mc-mod-version-upgrade port-publish (community port publisher)"
MARK = "<!-- port-publish:{platform}:{ref} -->"
MARK_RE = re.compile(r"<!-- port-publish:(modrinth|curseforge):([^ ]+) -->")


def _ledger_mod():
    spec = importlib.util.spec_from_file_location("fix_ledger", ROOT / "fix-ledger.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------- http

def http(method, url, token_header=None, token=None, data=None, headers=None, raw=False):
    h = {"User-Agent": UA, **(headers or {})}
    if token:
        h[token_header] = token
    if isinstance(data, (dict, list)):
        data, h["Content-Type"] = json.dumps(data).encode(), "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {url} -> HTTP {e.code}: {e.read()[:600].decode('utf-8', 'replace')}")
    return body if raw else (json.loads(body) if body.strip() else None)


def multipart(fields, files):
    """fields: name -> str; files: name -> (filename, bytes, content type)."""
    b = uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out += [f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n".encode(), v.encode(), b"\r\n"]
    for k, (fn, content, ct) in files.items():
        out += [f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fn}\"\r\n"
                f"Content-Type: {ct}\r\n\r\n".encode(), content, b"\r\n"]
    out.append(f"--{b}--\r\n".encode())
    return b"".join(out), {"Content-Type": f"multipart/form-data; boundary={b}"}


def gh(method, path, data=None, raw=False, accept=None):
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    h = {"Accept": accept or "application/vnd.github+json"}
    return http(method, GITHUB + path, "Authorization", f"Bearer {tok}" if tok else None, data, h, raw)


def modrinth(method, path, data=None, headers=None):
    return http(method, MODRINTH + path, "Authorization", os.environ.get("MODRINTH_TOKEN"), data, headers)


def curseforge(method, path, data=None, headers=None):
    return http(method, CURSEFORGE + path, "X-Api-Token", os.environ.get("CURSEFORGE_TOKEN"), data, headers)


# ---------------------------------------------------------------- config and repo facts

def load_config(repo):
    p = pathlib.Path(repo) / CONFIG
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)


def slug_of(repo):
    """owner/name of origin."""
    url = git(repo, "remote", "get-url", "origin").stdout.strip()
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?/?$", url)
    return os.environ.get("GITHUB_REPOSITORY") or (m.group(1) if m else None)


def mods_toml(repo, ref="HEAD"):
    for p in ("src/main/resources/META-INF/neoforge.mods.toml", "src/main/resources/META-INF/mods.toml"):
        r = git(repo, "show", f"{ref}:{p}")
        if r.returncode == 0:
            return tomllib.loads(r.stdout)
    raise SystemExit("no neoforge.mods.toml in the port branch")


def minecraft_of(tag):
    m = re.search(r"-mc(\d+\.\d+(?:\.\d+)?)", tag)
    if not m:
        raise SystemExit(f"{tag}: no -mc<version> in the tag, so no game version to publish under")
    return m.group(1)


def version_of(tag, modid, mc=None):
    """mc: the Minecraft version to name (a layer target's); default the tag's own."""
    v = re.sub(r"-mc\d+\.\d+(?:\.\d+)?", "+mc", tag[len(modid) + 1:] if tag.startswith(modid + "-") else tag)
    v = v.replace("+mc", f"+{mc or minecraft_of(tag)}")
    if len(v) > 32:                      # Modrinth's version_number limit
        raise SystemExit(f"{tag}: version number {v!r} is over 32 characters")
    return v


# ---------------------------------------------------------------- gates

def ledger_errors(cfg, tag):
    fl, errs = _ledger_mod(), []
    for ref in cfg.get("ledger_issues", []):
        owner_repo, n = ref.split("#")
        body = gh("GET", f"/repos/{owner_repo}/issues/{n}")["body"] or ""
        try:
            led = fl.parse(body)
        except SystemExit:
            errs.append(f"{ref}: no fix ledger in the issue body"); continue
        errs += [f"{ref}: {e}" for e in fl.check(led, tag)]
    return errs


def pinned_dependency_tag(repo, dep_repo):
    """The release tag of dep_repo this port's CI builds against (its --dep-jar pin)."""
    wf = (pathlib.Path(repo) / ".github/workflows/port-ci.yml")
    if not wf.exists():
        return None
    m = re.search(rf"github\.com/{re.escape(dep_repo)}/releases/download/([^/\"]+)/", wf.read_text(encoding="utf-8"), re.I)
    return m.group(1) if m else None


def published(body):
    return {p: r for p, r in MARK_RE.findall(body or "")}


def gate(repo, cfg, tag, release, manual=False, layer_branch=None):
    """Every reason not to publish this release; empty means go. layer_branch: a multi-version release is tagged on
    the layer branch, not the port branch, so "built from this branch" is judged against origin/<layer_branch>, and
    the dependency check is per TARGET (publish_layer), not per release."""
    why = []
    if not cfg.get("permission", "").startswith("https://"):
        why.append("no permission link in the config -- publishing needs the authors' agreement on record")
    if not manual and not cfg.get("auto", False):
        why.append("auto is off in the config -- publish with the manual dispatch (publish = <tag>) instead")
    if release.get("draft"):
        why.append(f"{tag} is a draft")
    if release.get("prerelease"):
        why.append(f"{tag} is still a pre-release -- a person plays it and promotes it first")
    if (release.get("author") or {}).get("login") != "github-actions[bot]":
        why.append(f"{tag} was not published by this fork's CI")
    if layer_branch:
        if git(repo, "merge-base", "--is-ancestor", f"refs/tags/{tag}", f"refs/remotes/origin/{layer_branch}").returncode != 0:
            why.append(f"{tag} is not an ancestor of origin/{layer_branch} (the layer branch)")
    elif git(repo, "merge-base", "--is-ancestor", f"refs/tags/{tag}", "HEAD").returncode != 0:
        why.append(f"{tag} is not an ancestor of the checked-out branch")
    why += ledger_errors(cfg, tag)
    for d in ([] if layer_branch else cfg.get("dependencies", [])):
        if d.get("port") and d.get("type") == "required":
            dtag = pinned_dependency_tag(repo, d["port"])
            if not dtag:
                why.append(f"{d['port']}: no pinned release in port-ci.yml to check is published"); continue
            dbody = gh("GET", f"/repos/{d['port']}/releases/tags/{dtag}").get("body")
            for plat in platforms(cfg):
                if plat not in published(dbody):
                    why.append(f"{d['port']}@{dtag} is not published on {plat} yet -- publish the dependency first")
    return why


def platforms(cfg):
    out = []
    if cfg.get("modrinth", {}).get("slug"):
        out.append("modrinth")
    if cfg.get("curseforge", {}).get("project_id"):
        out.append("curseforge")
    return out


# ---------------------------------------------------------------- release notes

def notes(cfg, tag, release, repo_slug, mc=None):
    """The changelog players see. Deliberately says nothing about defects in the original: bugs are tracked in the
    fork's GitHub issues, and a public page listing the original's bugs reads as disparaging the authors whose
    work this is. Only differences a player would notice ("differences" in the config) are stated, neutrally."""
    body = release.get("body") or ""
    played = re.search(r"Played by @[^\n]*", body)
    out = [f"**Unofficial community port** of {cfg.get('original_name', 'the original mod')} to NeoForge "
           f"{mc or minecraft_of(tag)}, based on the original's version {version_of(tag, cfg.get('modid', '')).split('+')[0]}.", ""]
    diffs = cfg.get("differences", [])
    if diffs:
        out += ["Differences from the original:", *[f"- {d}" for d in diffs], ""]
    out += [f"Every build passes this port's checks (it builds, loads on a server and in a real client) and is "
            f"played by a person before release. {played.group(0) if played else ''}".strip(), "",
            f"Report problems with this port at https://github.com/{repo_slug}/issues, not to the original author.",
            f"Published with the original author's permission: {cfg['permission']}"]
    return "\n".join(out)


# The public pages and changelogs must not advertise the original's defects (see notes()). Enforced, not just
# remembered: publish and the Modrinth page sync refuse text that names defects or links numbered issues.
# Exempt: the authors' own words (the "Credits" and "The original" sections -- "Bug reports and fix suggestions:
# ...", "the blade lies broken") and the permission link, which is an issue URL by nature.
PUBLIC_WORDS = re.compile(r"\b(pre-existing|defects?|bugs?|bugfix|crash\w*|broken|disconnect\w*|hangs?|"
                          r"workaround|fixes|fixed)\b", re.I)


def public_text_problems(text, permission=""):
    probs, section = [], ""
    for line in text.splitlines():
        if line.startswith("#"):
            section = line.lower()
        if "credits" in section or ("the original" in section and "differs" not in section):
            continue
        if re.search(r"/issues/\d+", line.replace(permission, "") if permission else line):
            probs.append(f"links a numbered issue: {line.strip()[:120]}")
        w = PUBLIC_WORDS.search(line)
        if w:
            probs.append(f"names a defect ('{w.group(0)}'): {line.strip()[:120]}")
    return probs


# ---------------------------------------------------------------- platforms

def pick_asset(release, suffix):
    jars = [a for a in release.get("assets", []) if a["name"].endswith(".jar")]
    hit = [a for a in jars if a["name"].endswith(suffix)] if suffix else jars
    if len(hit) != 1:
        raise SystemExit(f"asset '{suffix}' matches {len(hit)} of {[a['name'] for a in jars]} -- set modrinth/"
                         "curseforge \"asset\" to a suffix that picks exactly one jar")
    return hit[0]


def download(asset):
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    return http("GET", asset["url"], "Authorization", f"Bearer {tok}" if tok else None,
                headers={"Accept": "application/octet-stream"}, raw=True)


def dep_entries(cfg, plat):
    out = []
    for d in cfg.get("dependencies", []):
        if d.get("port"):
            other = json.loads(gh("GET", f"/repos/{d['port']}/contents/{CONFIG}",
                                  accept="application/vnd.github.raw", raw=True))
            ref = (other.get(plat) or {}).get("slug")
        else:
            ref = d.get(plat)
        if ref:
            out.append((ref, d.get("type", "required")))
    return out


def modrinth_version(cfg, tag, release, text, dry, mc=None, asset=None, dep_versions=None):
    """mc / asset / dep_versions are a layer target's (publish_layer); a single-version publish passes none."""
    mr = cfg["modrinth"]
    mc = mc or minecraft_of(tag)
    proj = modrinth("GET", f"/project/{mr['slug']}")
    have = {v["version_number"] for v in modrinth("GET", f"/project/{proj['id']}/version") or []}
    number = version_of(tag, cfg.get("modid", ""), mc)
    if number in have:
        return proj, {"id": "existing", "version_number": number}
    asset = asset or pick_asset(release, mr.get("asset", ""))
    deps = [{"project_id": modrinth("GET", f"/project/{s}")["id"], "dependency_type": t} for s, t in dep_entries(cfg, "modrinth")]
    for d in deps if dep_versions else []:        # a layer target pins the dependency's version built for the same Minecraft
        pinned = dep_versions.get(d["project_id"])
        if pinned:
            d["version_id"] = pinned
    data = {"project_id": proj["id"], "name": f"{number} (NeoForge {mc})", "version_number": number,
            "changelog": text, "dependencies": deps, "game_versions": [mc], "version_type": "release",
            "loaders": ["neoforge"], "featured": True, "file_parts": ["file"], "primary_file": "file"}
    if dry:
        print("modrinth version:", json.dumps({**data, "changelog": "<notes>", "file": asset["name"]}, indent=1))
        return proj, None
    body, h = multipart({"data": json.dumps(data)}, {"file": (asset["name"], download(asset), "application/java-archive")})
    return proj, modrinth("POST", "/version", body, h)


def curseforge_ids(mc):
    types = curseforge("GET", "/game/version-types")
    vers = curseforge("GET", "/game/versions")
    tslug = {t["id"]: t["slug"] for t in types}
    mcid = [v["id"] for v in vers if v["name"] == mc and tslug.get(v["gameVersionTypeID"], "").startswith("minecraft-")]
    loader = [v["id"] for v in vers if v["name"] == "NeoForge" and tslug.get(v["gameVersionTypeID"]) == "modloader"]
    if len(mcid) != 1 or len(loader) != 1:
        raise SystemExit(f"CurseForge game versions: {len(mcid)} match Minecraft {mc}, {len(loader)} match NeoForge")
    return mcid + loader


def curseforge_file(cfg, tag, release, text, dry, mc=None, asset=None):
    cf = cfg["curseforge"]
    mc = mc or minecraft_of(tag)
    asset = asset or pick_asset(release, cf.get("asset", ""))
    kinds = {"required": "requiredDependency", "optional": "optionalDependency"}
    meta = {"changelog": text, "changelogType": "markdown", "displayName": f"{version_of(tag, cfg.get('modid', ''), mc)} (NeoForge {mc})",
            "releaseType": "release",
            "relations": {"projects": [{"slug": s, "type": kinds.get(t, "requiredDependency")}
                                       for s, t in dep_entries(cfg, "curseforge")]}}
    if dry:
        print("curseforge file:", json.dumps({**meta, "changelog": "<notes>", "file": asset["name"],
                                              "project_id": cf["project_id"]}, indent=1))
        return None
    meta["gameVersions"] = curseforge_ids(mc)
    body, h = multipart({"metadata": json.dumps(meta)}, {"file": (asset["name"], download(asset), "application/java-archive")})
    return curseforge("POST", f"/projects/{cf['project_id']}/upload-file", body, h)



# ---------------------------------------------------------------- multi-version (layer) releases
# Opt-in twice over: the config has a "layer" section AND the tag's commit carries .multiversion/ (the layer
# branch). Nothing here runs for a single-version fork -- publish() asks layer_of() first and, on None, takes the
# unchanged single-version path.

GATES_DOC = re.compile(r"^((?!src/)[^\n]*\.md|LICENSE[^/]*|\.github/.*)$")     # nothing a player's jar contains


def mc_key(v):
    return tuple(int(x) if x.isdigit() else 0 for x in re.findall(r"\d+|[A-Za-z]+", v))


def props_of(text):
    out = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def layer_of(repo, tag, cfg):
    """None = a single-version publish. Else {'base': mc, 'targets': [mc, ... oldest first], 'branch': ..., 'commit': sha}."""
    lc = cfg.get("layer")
    if not lc:
        return None
    r = git(repo, "show", f"{tag}:.multiversion/base.properties")
    if r.returncode != 0:
        return None
    base = props_of(r.stdout).get("minecraft_version")
    ls = git(repo, "ls-tree", "-d", "--name-only", f"{tag}:.multiversion/targets/")
    targets = sorted((n.strip() for n in ls.stdout.splitlines() if n.strip()), key=mc_key) if ls.returncode == 0 else []
    return {"base": base, "targets": targets, "branch": lc.get("branch"),
            "commit": git(repo, "rev-parse", f"{tag}^{{commit}}").stdout.strip()}


def layer_chain(repo, tag, t):
    out = []
    while t and t not in out:
        out.append(t)
        t = props_of(git(repo, "show", f"{tag}:.multiversion/targets/{t}/target.properties").stdout).get("inherits")
    return out


def latest_by_name(runs):
    best = {}
    for r in runs:
        n = r.get("name") or ""
        if n not in best or (r.get("id") or 0) > (best[n].get("id") or 0):
            best[n] = r
    return best


def ci_green(t, base, newest, runs):
    """(green, why) from the CI jobs that ran at the released commit. Only the two bookends have full gates in CI:
    the base ("gates (full)", "gates (minimal)") and the newest target ("layer gates (newest target T)"). A middle
    target is compile-only there, which is not a gate to publish on."""
    best = latest_by_name(runs)
    if t == base:
        mine = {n: r for n, r in best.items() if n.startswith("gates (")}
        if not mine:
            return False, "no 'gates (...)' check run at the released commit"
        bad = sorted(n for n, r in mine.items() if r.get("conclusion") != "success")
        return (not bad), (f"{', '.join(bad)} not green" if bad else "gates green at the released commit")
    if t == newest:
        n = f"layer gates (newest target {t})"
        r = best.get(n)
        if not r:
            return False, f"no '{n}' check run at the released commit"
        return r.get("conclusion") == "success", f"{n}: {r.get('conclusion') or r.get('status')}"
    return False, "a middle target: CI only compiles it (list it under layer.released once its full gates were run)"


def listed_green(t, gates_text, changed_since, chain):
    """(green, why) for a target listed under layer.released: its GATES.md (what ci-gates.py wrote for that target)
    must name the target, hold no FAIL row, and no file that could change its jar may have changed between the
    commit it was written at and the released one (documents, CI files and other targets' own directories excepted).
    changed_since: file names changed since that commit, or None when it is not in this clone."""
    if not gates_text:
        return False, f".multiversion/targets/{t}/GATES.md is missing at the released commit"
    head = gates_text.splitlines()[0]
    m = re.search(r"for Minecraft (\S+)", head)
    if not m or m.group(1) != t:
        return False, f"GATES.md is for Minecraft {m.group(1) if m else '?'}, not {t}"
    if re.search(r"\|\s*FAIL\s*\|", gates_text) or not re.search(r"\|\s*PASS\s*\|", gates_text):
        return False, "GATES.md has a FAIL row or no PASS row"
    if changed_since is None:
        return False, "the commit GATES.md was written at is not in this clone -- cannot prove it is current"
    mine = tuple(f".multiversion/targets/{c}/" for c in chain) + tuple(f".multiversion/shared/{c}/" for c in chain)
    stale = [f for f in changed_since if not GATES_DOC.match(f)
             and not (f.startswith((".multiversion/targets/", ".multiversion/shared/")) and not f.startswith(mine))]
    return (not stale), (f"{len(stale)} file(s) changed since its gates ran, e.g. {stale[0]}" if stale else
                         "its recorded gates are current at the released commit")


def check_runs(repo_slug, sha):
    out, page = [], 1
    while True:
        r = gh("GET", f"/repos/{repo_slug}/commits/{sha}/check-runs?per_page=100&page={page}")
        got = (r or {}).get("check_runs", [])
        out += got
        if len(got) < 100:
            return out
        page += 1


def target_green(repo, tag, info, t, lc, runs):
    green, why = ci_green(t, info["base"], info["targets"][-1] if info["targets"] else info["base"], runs)
    if green or t not in lc.get("released", []):
        return green, why
    gt = git(repo, "show", f"{tag}:.multiversion/targets/{t}/GATES.md")
    text = gt.stdout if gt.returncode == 0 else ""
    m = re.search(r"at `([0-9a-f]{7,40})`", text.splitlines()[0]) if text else None
    changed = None
    if m and git(repo, "cat-file", "-e", f"{m.group(1)}^{{commit}}").returncode == 0:
        d = git(repo, "diff", "--name-only", m.group(1), info["commit"])
        changed = [l for l in d.stdout.splitlines() if l] if d.returncode == 0 else None
    return listed_green(t, text, changed, layer_chain(repo, tag, t))


def layer_asset_suffix(suffix, t, base):
    """The asset-name ending of target t's file: the base target's jar is the unsuffixed one, every other target's is
    named "<name>-mc<t>.jar" by the layer build (templates/fork-layer/layer.gradle)."""
    if t == base:
        return suffix
    return (suffix[:-4] if suffix.endswith(".jar") else suffix) + f"-mc{t}.jar"


LAYER_NAMED = re.compile(r"-mc\d[\w.]*\.jar$")


def layer_pick_asset(release, suffix, t, base):
    """The release's one jar for target t, or None. The base target's jar is the one NOT named for a layer target."""
    jars = [a for a in release.get("assets", []) if a["name"].endswith(".jar")]
    want = layer_asset_suffix(suffix, t, base)
    hit = [a for a in jars if a["name"].endswith(want)] if want else jars
    if t == base:
        hit = [a for a in hit if not LAYER_NAMED.search(a["name"])]
    if len(hit) > 1:
        raise SystemExit(f"target {t}: asset ending '{want}' matches {len(hit)} of {[a['name'] for a in jars]}")
    return hit[0] if hit else None


def published_pairs(body, tag):
    """{(platform, minecraft)} a release says it is published for: a layer marker 'id@T', or the single-version
    marker (which is for the minecraft in the tag)."""
    out = set()
    for plat, ref in MARK_RE.findall(body or ""):
        out.add((plat, ref.split("@", 1)[1] if "@" in ref else minecraft_of(tag)))
    return out


def layer_dep_pins(repo, tag, t, cfg):
    """{dependency repo name: version number} this target pins, from its target.properties 'publish.dep.<repo name>'
    (the dependency's version built for the SAME Minecraft). Empty = the project-level dependency, unpinned."""
    props = props_of(git(repo, "show", f"{tag}:.multiversion/targets/{t}/target.properties").stdout)
    return {k[len("publish.dep."):]: v for k, v in props.items() if k.startswith("publish.dep.")}


def layer_dep_versions(repo, tag, cfg, t):
    """({dependency project id: version id}, errors) for Modrinth: each port dependency this target pins
    (layer_dep_pins), resolved on the dependency's own Modrinth project. A pin the project lacks is an error."""
    pins, out, errs = layer_dep_pins(repo, tag, t, cfg), {}, []
    for d in cfg.get("dependencies", []):
        name = (d.get("port") or "").split("/")[-1]
        if not d.get("port") or not pins.get(name):
            continue
        other = json.loads(gh("GET", f"/repos/{d['port']}/contents/{CONFIG}", accept="application/vnd.github.raw", raw=True))
        slug = (other.get("modrinth") or {}).get("slug")
        hit = [v for v in (modrinth("GET", f"/project/{slug}/version") or []) if v["version_number"] == pins[name]]
        if hit:
            out[hit[0]["project_id"]] = hit[0]["id"]
        else:
            errs.append(f"target {t}: dependency {slug} has no Modrinth version {pins[name]} (publish.dep.{name})")
    return out, errs


def publish_layer(repo, tag, cfg, release, repo_slug, info, dry=False, manual=False):
    lc = cfg["layer"]
    if not info["branch"]:
        print(f"::error::will not publish {tag}: the config's \"layer\" section names no \"branch\" (the layer branch)")
        return 1
    why = gate(repo, cfg, tag, release, manual, layer_branch=info["branch"])
    if why:
        for w in why:
            print(f"::error::will not publish {tag}: {w}")
        return 1
    all_t = [info["base"]] + info["targets"]
    asked = lc.get("targets") or []
    unknown = [t for t in asked if t not in all_t]
    if unknown:
        print(f"::error::will not publish {tag}: layer.targets names {', '.join(unknown)}, which the layer does not have")
        return 1
    plats = platforms(cfg)
    suffix = {p: cfg[p].get("asset", "") for p in plats}
    cand = list(asked)
    if not asked:           # every target the release holds a file for
        cand = [t for t in all_t if any(layer_pick_asset(release, suffix[p], t, info["base"]) for p in plats)]
    if not cand:
        print(f"::error::will not publish {tag}: the release holds no jar for any target of the layer")
        return 1
    runs = check_runs(repo_slug, info["commit"])
    done = published_pairs(release.get("body"), tag)
    errors, plan, dvs = [], [], {}
    for t in sorted(cand, key=mc_key):
        green, gwhy = target_green(repo, tag, info, t, lc, runs)
        if not green:
            errors.append(f"target {t}: gates are not green at the released commit -- {gwhy}")
            continue
        assets = {}
        for p in plats:
            a = layer_pick_asset(release, suffix[p], t, info["base"])
            if a is None:
                errors.append(f"target {t}: no {p} asset (name ending '{layer_asset_suffix(suffix[p], t, info['base'])}') on {tag}")
            assets[p] = a
        for d in cfg.get("dependencies", []):
            if d.get("port") and d.get("type") == "required":
                dtag = pinned_dependency_tag(repo, d["port"])
                if not dtag:
                    errors.append(f"target {t}: {d['port']}: no pinned release in port-ci.yml to check is published"); continue
                have = published_pairs(gh("GET", f"/repos/{d['port']}/releases/tags/{dtag}").get("body"), dtag)
                for p in plats:
                    if (p, t) not in have:
                        errors.append(f"target {t}: {d['port']}@{dtag} is not published on {p} for Minecraft {t} -- publish the dependency for {t} first")
        if "modrinth" in plats:         # the dependency version built for the same Minecraft, resolved BEFORE any upload
            dvs[t], pin_errors = layer_dep_versions(repo, tag, cfg, t)
            errors += pin_errors
        plan.append((t, assets))
    for t, _ in plan:
        bad = public_text_problems(notes(cfg, tag, release, repo_slug, mc=t), cfg.get("permission", ""))
        errors += [f"target {t}: the release notes {b} -- edit \"differences\" in the config" for b in bad]
    if errors:
        for e in errors:
            print(f"::error::will not publish {tag}: {e}")
        return 1
    links, missing = [], []
    for t, assets in plan:
        text = notes(cfg, tag, release, repo_slug, mc=t)
        for plat in plats:
            if (plat, t) in done:
                print(f"{plat} {t}: already published -- skipped"); continue
            if not dry and not os.environ.get(f"{plat.upper()}_TOKEN"):
                missing.append(f"{plat} is configured but the {plat.upper()}_TOKEN secret is not set"); continue
            if plat == "modrinth":
                proj, v = modrinth_version(cfg, tag, release, text, dry, mc=t, asset=assets[plat], dep_versions=dvs[t])
                if v and v["id"] != "existing":
                    links.append((plat, f"{v['id']}@{t}", f"https://modrinth.com/mod/{proj['slug']}/version/{v['version_number']}"))
            else:
                f = curseforge_file(cfg, tag, release, text, dry, mc=t, asset=assets[plat])
                if f:
                    links.append((plat, f"{f.get('id')}@{t}", f"https://www.curseforge.com/minecraft/mc-mods/{cfg['curseforge']['slug']}/files/{f.get('id')}"))
    if links and not dry:
        add = "\n".join(f"Published on {p.title()} for Minecraft {r.split('@')[1]}: {u} {MARK.format(platform=p, ref=r)}" for p, r, u in links)
        gh("PATCH", f"/repos/{repo_slug}/releases/{release['id']}", {"body": (release.get("body") or "").rstrip() + "\n\n" + add})
        for p, r, u in links:
            print(f"published {tag} on {p} for Minecraft {r.split('@')[1]}: {u}")
    for m in missing:
        print(f"::error::{m}")
    return 1 if missing else 0


# ---------------------------------------------------------------- commands

def publish(repo, tag, dry=False, manual=False):
    cfg = load_config(repo)
    if not cfg:
        print(f"::notice::no {CONFIG}: this fork is not set up to publish (port-publish.py setup)"); return 0
    repo_slug = slug_of(repo)
    release = gh("GET", f"/repos/{repo_slug}/releases/tags/{tag}")
    info = layer_of(repo, tag, cfg)
    if info:            # a multi-version release: one file per green target
        return publish_layer(repo, tag, cfg, release, repo_slug, info, dry, manual)
    why = gate(repo, cfg, tag, release, manual)
    if why:
        for w in why:
            print(f"::error::will not publish {tag}: {w}")
        return 1
    done = published(release.get("body"))
    text, links, missing = notes(cfg, tag, release, repo_slug), [], []
    bad = public_text_problems(text, cfg.get("permission", ""))
    if bad:
        for b in bad:
            print(f"::error::will not publish {tag}: the release notes {b} -- public text describes differences, "
                  "not the original's defects (edit \"differences\" in the config)")
        return 1
    for plat in ("modrinth", "curseforge"):
        if plat not in platforms(cfg):
            print(f"::notice::{plat}: no project configured yet -- skipped"); continue
        if plat in done:
            print(f"{plat}: already published ({done[plat]}) -- skipped"); continue
        tokvar = f"{plat.upper()}_TOKEN"
        if not dry and not os.environ.get(tokvar):
            missing.append(f"{plat} is configured but the {tokvar} secret is not set"); continue
        if plat == "modrinth":
            proj, v = modrinth_version(cfg, tag, release, text, dry)
            if v:
                links.append((plat, v["id"], f"https://modrinth.com/mod/{proj['slug']}/version/{v['version_number']}"))
        else:
            f = curseforge_file(cfg, tag, release, text, dry)
            if f:
                links.append((plat, str(f.get("id")), f"https://www.curseforge.com/minecraft/mc-mods/{cfg['curseforge']['slug']}/files/{f.get('id')}"))
    if links and not dry:
        add = "\n".join(f"Published on {p.title()}: {u} {MARK.format(platform=p, ref=r)}" for p, r, u in links)
        gh("PATCH", f"/repos/{repo_slug}/releases/{release['id']}", {"body": (release.get("body") or "").rstrip() + "\n\n" + add})
        for p, r, u in links:
            print(f"published {tag} on {p}: {u}")
    for m in missing:
        print(f"::error::{m}")
    return 1 if missing else 0


def modrinth_create(repo):
    cfg = load_config(repo)
    if not cfg or not cfg.get("modrinth", {}).get("slug"):
        sys.exit(f"no modrinth slug in {CONFIG}")
    if not cfg.get("permission", "").startswith("https://"):
        sys.exit("no permission link in the config -- will not create a project")
    body_md = (pathlib.Path(repo) / cfg.get("body_file", BODY)).read_text(encoding="utf-8")
    bad = public_text_problems(body_md, cfg["permission"]) + public_text_problems(cfg.get("summary", ""))
    if bad:
        sys.exit("will not put this page up: " + "; ".join(bad) + " -- public text describes differences, not the "
                 "original's defects")
    try:
        p = modrinth("GET", f"/project/{cfg['modrinth']['slug']}")
    except SystemExit as e:
        if "HTTP 404" not in str(e):
            raise
        p = None
    if p:                                   # exists: keep its page in step with the fork's files
        modrinth("PATCH", f"/project/{p['id']}", {"title": cfg["title"], "description": cfg["summary"], "body": body_md})
        print(f"modrinth project {p['slug']} ({p['id']}, status {p['status']}) exists -- title, summary and page "
              "updated from the fork's files")
        return 0
    data = {"project_type": "mod", "slug": cfg["modrinth"]["slug"], "title": cfg["title"], "description": cfg["summary"],
            "body": body_md, "categories": cfg.get("categories", []), "additional_categories": cfg.get("additional_categories", []),
            "client_side": "required", "server_side": "required", "license_id": cfg.get("license", "MIT"),
            "source_url": f"https://github.com/{slug_of(repo)}", "issues_url": f"https://github.com/{slug_of(repo)}/issues",
            "is_draft": True, "initial_versions": []}
    files = {}
    icon = cfg.get("icon")
    if icon:
        r = subprocess.run(["git", "-C", str(repo), "show", f"HEAD:{icon}"], capture_output=True, check=False)
        if r.returncode == 0:
            files["icon"] = (pathlib.Path(icon).name, r.stdout, "image/png")
    body, h = multipart({"data": json.dumps(data)}, files)
    p = modrinth("POST", "/project", body, h)
    print(f"created the Modrinth DRAFT project {p['slug']} ({p['id']}): https://modrinth.com/mod/{p['slug']} -- "
          "it stays a draft until a version is uploaded and it is submitted for review on the website")
    return 0


def setup(repo, permission, branch, out=None, ledgers=(), differences=()):
    repo = pathlib.Path(repo).expanduser()
    ref = f"origin/{branch}"
    if git(repo, "rev-parse", "--verify", ref).returncode:
        ref = "HEAD"
    toml = mods_toml(repo, ref)
    mod = toml["mods"][0]
    modid, name = mod["modId"], mod.get("displayName", mod["modId"])
    install = json.loads(git(repo, "show", f"{ref}:.github/port-install.json").stdout or "{}")
    mc = install.get("minecraft", "1.21.1")
    orig = None
    disp = mod.get("displayURL", "")
    m = re.search(r"modrinth\.com/mod/([\w-]+)", disp)
    try:
        q = urllib.parse.quote(name)
        hits = http("GET", f"{MODRINTH}/search?query={q}&limit=5")["hits"]
        cand = [h for h in hits if h["title"].lower() == name.lower()] or ([{"slug": m.group(1)}] if m else [])
        if cand:
            orig = http("GET", f"{MODRINTH}/project/{cand[0]['slug']}")
    except SystemExit:
        pass
    authors = mod.get("authors", "the original authors")
    up = (orig or {}).get("source_url") or (disp if "github.com" in disp else None)
    deps = []
    by_modid = {f.get("modid"): f for f in install.get("files", [])}
    for d in toml.get("dependencies", {}).get(modid, []):
        dep, kind = d["modId"], d.get("type", "required")
        if dep in ("minecraft", "neoforge") or kind not in ("required", "optional"):
            continue
        if (by_modid.get(dep) or {}).get("port"):
            deps.append({"port": by_modid[dep]["port"], "type": kind}); continue
        try:
            http("GET", f"{MODRINTH}/project/{dep}")
            deps.append({"modrinth": dep, "curseforge": dep, "type": kind})
        except SystemExit:
            print(f"WARNING: dependency {dep} has no Modrinth project of that name -- set its slugs by hand")
            deps.append({"modrinth": None, "curseforge": None, "type": kind, "modid": dep})
    fork = slug_of(repo)
    title = f"{name} - Community Port"
    if len(title) > 64:
        title = f"{name[:50]} - Community Port"
    oslug = (orig or {}).get("slug") or modid.replace("_", "-")
    fork_slug = slug_of(repo)
    try:
        rels = http("GET", f"{GITHUB}/repos/{fork_slug}/releases?per_page=1")
        names = [a["name"] for a in rels[0]["assets"]] if rels else []
    except SystemExit:
        names = []
    variant = lambda sfx: sfx if any(n.endswith(sfx) for n in names) else ""
    cfg = {"schema": 1, "permission": permission, "auto": True, "modid": modid, "title": title,
           "summary": f"Unofficial NeoForge port of {name} by {authors} to newer Minecraft, maintained by the community "
                      "with the author's permission.",
           "body_file": BODY, "icon": f"src/main/resources/{mod['logoFile']}" if mod.get("logoFile") else None,
           "license": toml.get("license", "MIT"), "categories": (orig or {}).get("categories", []),
           "additional_categories": (orig or {}).get("additional_categories", []),
           "ledger_issues": list(ledgers), "differences": list(differences), "original_name": name,
           "dependencies": deps,
           "modrinth": {"slug": f"{oslug}-neoforge", "asset": variant("_mr.jar")},
           "curseforge": {"project_id": None, "slug": f"{oslug}-neoforge", "asset": variant("_cf.jar")}}
    credits = str(mod.get("credits", "")).strip()
    lines = [f"# {title}", "",
             f"**Unofficial.** A community-maintained port of **{name}** by **{authors}** to NeoForge {mc}. "
             f"It is not made or supported by {authors}: please report problems with this port "
             f"[on its own issue tracker](https://github.com/{fork}/issues), not to the original author.", "",
             f"Published with the original author's permission ({permission}), under the original "
             f"{cfg['license']} licence with its copyright notice kept.", "",
             "## The original", "",
             *([f"- {orig['description']}"] if orig else []),
             *([f"- Source: {up}"] if up else []),
             *([f"- On Modrinth: https://modrinth.com/mod/{orig['slug']} (Forge, {', '.join(orig['game_versions'][-1:])})"] if orig else []), "",
             "## How it differs from the original", "",
             f"- Runs on **NeoForge {mc}**.",
             *[f"- {d}" for d in differences], "",
             "## Requirements", ""]
    for d in deps:
        what = (f"the community port of https://github.com/{d['port']} (the same NeoForge {mc} port, published "
                "alongside this one)" if d.get("port") else
                f"[{d['modrinth']}](https://modrinth.com/mod/{d['modrinth']})" if d.get("modrinth") else d.get("modid"))
        lines.append(f"- {'Required' if d['type'] == 'required' else 'Optional'}: {what}")
    if not deps:
        lines.append(f"- Nothing beyond NeoForge {mc}.")
    lines += ["", "## How releases are made", "",
              f"Every release is built by the original author's own build from https://github.com/{fork}, passes "
              "automated checks (it builds, loads on a dedicated server, runs in a real client), and is played by a "
              "person before it is published here.", ""]
    if credits:
        lines += ["## Credits", "", *[f"- {c.strip()}" for c in re.split(r"[\n,]", credits) if c.strip()], ""]
    lines += ["## Licence", "", f"{cfg['license']}, copyright {authors}. See LICENSE.txt in the source."]
    target = pathlib.Path(out) if out else repo
    (target / BODY).parent.mkdir(parents=True, exist_ok=True)
    (target / BODY).write_text("\n".join(lines) + "\n", encoding="utf-8")
    (target / CONFIG).write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {target / CONFIG} and {target / BODY} -- review both; curseforge.project_id is set after the "
          "project is created on the website")
    print("\nCurseForge create-project form (https://authors.curseforge.com, Create Project, Minecraft, Mods):")
    print(f"  Name:        {title}\n  Summary:     {cfg['summary']}\n  Description: paste {BODY}\n"
          f"  License:     {cfg['license']}\n  Source URL:  https://github.com/{fork}\n  Issues URL:  https://github.com/{fork}/issues\n"
          f"  Logo:        {cfg['icon']}\n  Categories:  closest to {', '.join(cfg['categories'] + cfg['additional_categories'])}")
    return 0


# ---------------------------------------------------------------- self-check

def self_check():
    ok = True
    def expect(c, msg):
        nonlocal ok
        if not c:
            ok = False; print("FAIL:", msg)
    expect(minecraft_of("examplemod-1.0.0-mc1.21.1") == "1.21.1", "minecraft from tag")
    expect(minecraft_of("examplemod-1.0.0-mc26.1.2-r2") == "26.1.2" and minecraft_of("examplemod-1.0.0-mc26.2") == "26.2",
           "minecraft from a 26.x tag")
    expect(sorted(["26.2", "1.21.11", "26.1.2"], key=mc_key) == ["1.21.11", "26.1.2", "26.2"], "26.x sorts after 1.21.11")
    expect(version_of("examplemod-1.0.0-mc1.21.1-r2", "examplemod") == "1.0.0+1.21.1-r2", version_of("examplemod-1.0.0-mc1.21.1-r2", "examplemod"))
    expect(version_of("the_last_sword-1.1.2-mc1.21.1", "the_last_sword") == "1.1.2+1.21.1", "version, no suffix")
    expect(version_of("the_last_sword-1.1.1-fix-fix-mc1.21.1-r6", "the_last_sword") == "1.1.1-fix-fix+1.21.1-r6", "fix suffix")
    body = "notes\n\nPublished on Modrinth: x " + MARK.format(platform="modrinth", ref="AbCd1234")
    expect(published(body) == {"modrinth": "AbCd1234"}, "markers parse")
    # public text: no defects of the original, no numbered issues; the authors' own credits are exempt
    expect(public_text_problems("- Pre-existing: force-removal aborts half-way") != [], "pre-existing is flagged")
    expect(public_text_problems("- [x](https://github.com/o/f/issues/3)") != [], "a numbered issue is flagged")
    expect(public_text_problems("## Credits\n- Bug reports and fix suggestions: someone") == [], "credits exempt")
    expect(public_text_problems("- Runs on **NeoForge 1.21.1**.\nReport problems at https://github.com/o/f/issues")
           == [], "a neutral page passes")
    nt = notes({"permission": "https://example.invalid/ok", "modid": "m", "original_name": "M",
                "differences": ["The test sword's lockdown mode leaves other mods running."]},
               "m-2-mc1.21.1", {"body": "x\n\nPlayed by @someone on 2026-10-12."}, "o/f")
    expect(public_text_problems(nt, "https://example.invalid/ok") == [] and "Differences from the original" in nt and "Played by @someone" in nt,
           f"notes are neutral and carry differences: {public_text_problems(nt, 'https://example.invalid/ok')}")
    expect(public_text_problems("Permission: https://github.com/a/b/issues/9", "https://github.com/a/b/issues/9") == [],
           "the permission link is exempt")
    expect(public_text_problems("## The original\n- the blade lies broken\n## How it differs from the original\n"
                                "- fixed a bug") != [], "the original's own words are exempt, our section is not")
    rel = {"assets": [{"name": "m-1_mr.jar"}, {"name": "m-1_cf.jar"}, {"name": "m-1-sources.txt"}]}
    expect(pick_asset(rel, "_mr.jar")["name"] == "m-1_mr.jar", "asset by suffix")
    try:
        pick_asset(rel, ""); expect(False, "two jars and no suffix must refuse")
    except SystemExit:
        pass
    # the gate, offline: every refusal reason fires, and a clean release passes
    global gh, git
    real_gh, real_git = gh, git
    fl = _ledger_mod()
    led = {"issue": "o/f#1", "title": "t", "modes": [{"id": "kick", "what": "players disconnected", "severity": "S1",
           "repro": "join", "status": {"m-1-mc1.21.1-r4 + d-1-mc1.21.1": {"state": "open"},
                                       "m-2-mc1.21.1": {"state": "fixed", "by": "ours abc"}}}]}
    issue_body = fl.render(led)
    def fake_gh(method, path, data=None, raw=False, accept=None):
        if path.endswith("/issues/1"):
            return {"body": issue_body}
        if "/releases/tags/" in path:
            return {"body": "dep notes"}
        raise AssertionError(path)
    class R:
        def __init__(self, rc): self.returncode, self.stdout = rc, ""
    gh, git = fake_gh, (lambda repo, *a: R(0))
    try:
        cfg = {"permission": "https://example.invalid/agreed", "auto": True, "ledger_issues": ["o/f#1"],
               "modrinth": {"slug": "m-neoforge"}}
        good = {"prerelease": False, "draft": False, "author": {"login": "github-actions[bot]"}}
        expect(gate(".", cfg, "m-2-mc1.21.1", good) == [], f"clean release passes: {gate('.', cfg, 'm-2-mc1.21.1', good)}")
        why = gate(".", cfg, "m-1-mc1.21.1-r4", good)
        expect(any("must not be released" in w for w in why), f"open S1 refuses: {why}")
        expect(any("names no column" in w for w in gate(".", cfg, "m-3-mc1.21.1", good)), "unknown tag refuses")
        expect(any("pre-release" in w for w in gate(".", cfg, "m-2-mc1.21.1", {**good, "prerelease": True})), "pre-release refuses")
        expect(any("not published by" in w for w in gate(".", cfg, "m-2-mc1.21.1", {**good, "author": {"login": "x"}})), "foreign author refuses")
        expect(any("permission" in w for w in gate(".", {**cfg, "permission": ""}, "m-2-mc1.21.1", good)), "no permission refuses")
        expect(any("auto is off" in w for w in gate(".", {**cfg, "auto": False}, "m-2-mc1.21.1", good)), "auto off refuses unattended")
        expect(not any("auto is off" in w for w in gate(".", {**cfg, "auto": False}, "m-2-mc1.21.1", good, manual=True)), "manual ignores auto")
        dep = {**cfg, "dependencies": [{"port": "o/lib", "type": "required"}]}
        global pinned_dependency_tag
        real_pin = pinned_dependency_tag
        pinned_dependency_tag = lambda repo, d: "lib-1-mc1.21.1"
        try:
            expect(any("not published on modrinth" in w for w in gate(".", dep, "m-2-mc1.21.1", good)), "unpublished dependency refuses")
        finally:
            pinned_dependency_tag = real_pin
        git = lambda repo, *a: R(1)
        expect(any("not an ancestor" in w for w in gate(".", cfg, "m-2-mc1.21.1", good)), "foreign tag refuses")
    finally:
        gh, git = real_gh, real_git
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-check", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("setup"); s.add_argument("--repo", required=True); s.add_argument("--permission", required=True)
    s.add_argument("--branch", default="neoforge-1.21.1")
    s.add_argument("--ledger-issue", action="append", default=[], help="owner/repo#n carrying a fix ledger; repeat "
                   "(gates publishing; never shown to players)")
    s.add_argument("--difference", action="append", default=[], help="a difference from the original a player "
                   "would notice, stated neutrally; repeat"); s.add_argument("--out", help="write here instead of into the clone")
    c = sub.add_parser("modrinth-create"); c.add_argument("--repo", default=".")
    p = sub.add_parser("publish"); p.add_argument("--repo", default="."); p.add_argument("--tag", required=True)
    p.add_argument("--dry-run", action="store_true"); p.add_argument("--manual", action="store_true",
                                                                     help="a person asked for this publish (ignores auto)")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if a.cmd == "setup":
        if not a.permission.startswith("https://"):
            sys.exit("--permission must be the https link where the authors agreed")
        return setup(a.repo, a.permission, a.branch, a.out, a.ledger_issue, a.difference)
    if a.cmd == "modrinth-create":
        return modrinth_create(a.repo)
    if a.cmd == "publish":
        return publish(a.repo, a.tag, a.dry_run, a.manual)
    ap.print_help(); return 2


if __name__ == "__main__":
    sys.exit(main())
