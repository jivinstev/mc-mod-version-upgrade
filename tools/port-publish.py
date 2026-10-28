#!/usr/bin/env python3
"""Publish a fork port's PROMOTED release as an unofficial community version on Modrinth and CurseForge.

    port-publish.py setup   --repo <fork clone> --permission <url> [--branch neoforge-1.21.1]
    port-publish.py modrinth-create --repo .           # CI: create the Modrinth draft project (MODRINTH_TOKEN)
    port-publish.py publish --repo . --tag <tag> [--dry-run] [--manual] [--platform mr|cf|both]   # CI: one release
    port-publish.py --self-check

The fork carries its publishing config as .github/port-publish.json (written by `setup`, reviewed, committed):

  {"schema": 1, "permission": "<url where the authors agreed>", "auto": true,
   "title": "...", "summary": "...", "body_file": ".github/port-publish/description.md", "icon": "<path in repo>",
   "license": "MIT", "categories": [...], "additional_categories": [...], "environment": "client_and_server",
   "ledger_issues": ["owner/fork#1", ...],
   "dependencies": [{"modrinth": "geckolib", "curseforge": "geckolib", "type": "required"},
                    {"port": "owner/other-fork", "type": "required"}],
   "modrinth": {"slug": "...", "asset": "_mr.jar"},
   "curseforge": {"project_id": 123456, "slug": "...", "asset": "_cf.jar", "manual_release": false}}
   (manual_release: true uploads each CurseForge file held -- approved but NOT released to players until the author
   clicks Release on the website; for a port whose required library's file for the same version is still in review)

A version branch (tools/fork-branches.py) publishes each version's release from its own branch, the same way.

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
    on that platform (the pin of the workflow AT THE TAG, or the install manifest when it describes the tag);
  - a release built against a library pre-release that was not done when it was pinned (DEPS.md's "Library
    pre-release (not done)" mark) goes out only with that exact library release: each library jar the tag's
    workflow pins is now a promoted Release (a person played the pair), published on that platform, and still the
    asset the tag's build checked by sha256. The library's done record is the round's question, not publishing's.
PER PLATFORM: `--platform mr` (Modrinth) or `cf` (CurseForge) uploads to that one only; the default is both. Before
uploading, each platform asked is judged against the fork's known-issues ledger (tools/known-issues.py
blocks-publish: an open S1 at the release's Minecraft version that applies to that platform's jar), read from the
head branch (`port_branch` in a version branch's .port/<mc>/branch.properties, else the checked-out branch's
.port/KNOWN-ISSUES.json). A blocked platform is HELD BACK -- named with the S1s that hold it -- and the other one still
goes; a run where every platform asked is held back exits 1. A fork with no ledger is judged as before.
It is idempotent: a platform this release is already published on (a marker in the release notes) is skipped,
so a re-run after a partial failure only does what is missing. After uploading it appends the links to the
release notes. Tokens come from the environment (MODRINTH_TOKEN, CURSEFORGE_TOKEN, GITHUB_TOKEN) -- never
arguments, never printed. A platform with no project yet (CurseForge before the person has created it) is
skipped with a notice, not failed. Standard library only.
"""
import argparse, datetime, importlib.util, json, os, pathlib, re, subprocess, sys, urllib.error
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


def modrinth_v3(method, path, data=None):
    """Modrinth's v3 API: the only place content disclosures can be set (v2 has no field for them)."""
    return http(method, MODRINTH.replace("/v2", "/v3") + path, "Authorization", os.environ.get("MODRINTH_TOKEN"), data)


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
            try:
                import tomllib
            except ImportError:                 # Python < 3.11 (setup runs on the user's machine; CI never reads TOML)
                sys.path.insert(0, str(ROOT))
                import minitoml as tomllib
            return tomllib.loads(r.stdout)
    raise SystemExit("no neoforge.mods.toml in the port branch")


def minecraft_of(tag):
    m = re.search(r"-mc(\d+\.\d+(?:\.\d+)?)", tag)
    if not m:
        raise SystemExit(f"{tag}: no -mc<version> in the tag, so no game version to publish under")
    return m.group(1)


def version_of(tag, modid):
    v = re.sub(r"-mc\d+\.\d+(?:\.\d+)?", "+mc", tag[len(modid) + 1:] if tag.startswith(modid + "-") else tag)
    v = v.replace("+mc", f"+{minecraft_of(tag)}")
    if len(v) > 32:                      # Modrinth's version_number limit
        raise SystemExit(f"{tag}: version number {v!r} is over 32 characters")
    return v


# ---------------------------------------------------------------- the known-issues ledger, per platform (issue #135)

PLATFORM_KEY = {"modrinth": "mr", "curseforge": "cf"}


def _known_issues():
    spec = importlib.util.spec_from_file_location("known_issues_pp", ROOT / "known-issues.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def ledger_for(repo, mc):
    """(the known-issues ledger as a dict, or None; why it could not be read, or None). Read from the head branch: a
    version branch names it (port_branch in .port/<mc>/branch.properties) and the ledger is read from origin's copy
    (fetched when the checkout lacks it); otherwise the checkout is the head and its own file is read."""
    props = pathlib.Path(repo) / ".port" / mc / "branch.properties"
    head = None
    if props.is_file():
        m = re.search(r"(?m)^port_branch=(\S+)", props.read_text(encoding="utf-8", errors="replace"))
        head = m.group(1) if m else None
    if head:
        ref = f"refs/remotes/origin/{head}"
        if git(repo, "rev-parse", "--verify", "--quiet", ref).returncode != 0:
            git(repo, "fetch", "-q", "--depth", "1", "origin", f"+refs/heads/{head}:{ref}")
            if git(repo, "rev-parse", "--verify", "--quiet", ref).returncode != 0:
                return None, f"cannot read the head branch {head} to check its known-issues ledger"
        r = git(repo, "show", f"{ref}:.port/KNOWN-ISSUES.json")
        text = r.stdout if r.returncode == 0 else ""
        where = f"{head}:.port/KNOWN-ISSUES.json"
    else:
        f = pathlib.Path(repo) / ".port/KNOWN-ISSUES.json"
        text = f.read_text(encoding="utf-8") if f.is_file() else ""
        where = ".port/KNOWN-ISSUES.json"
    if not text.strip():
        return None, None
    try:
        return _known_issues().validate(json.loads(text)), None
    except Exception as e:                      # an unreadable ledger holds every platform back: never publish blind
        return None, f"the known-issues ledger {where} is not valid ({e})"


def held_back(repo, mc, plats):
    """{platform: why} for each platform of `plats` an open S1 in the ledger blocks (known-issues.py blocks-publish)."""
    data, err = ledger_for(repo, mc)
    if err:
        return {p: err for p in plats}
    if data is None:
        return {}
    ki, out = _known_issues(), {}
    for p in plats:
        hit = ki.blocks_publish(data, mc, PLATFORM_KEY[p])
        if hit:
            out[p] = (f"{len(hit)} open S1 at Minecraft {mc} against the {PLATFORM_KEY[p]} jar in the known-issues ledger: "
                      + "; ".join(f"{e['id']} {e['title']}" for e in hit))
    return out


def asked(plats, platform):
    """The configured platforms this run is asked for (--platform mr / cf; None or both: every one)."""
    return [p for p in plats if platform in (None, "both") or PLATFORM_KEY[p] == platform]


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


def pinned_dependency_tag(repo, dep_repo, tag=None):
    """The release tag of dep_repo that release `tag` goes out with. The install manifest (.github/port-install.json,
    refreshed when a release is promoted) records it when it describes `tag`: the dependency the person played the
    release with. Otherwise the --dep-jar pin of the workflow AT THE TAG -- the checked-out branch's moves on when
    keep-current re-pins the dependency to a newer pre-release, so a promoted release must not be judged by it."""
    man = pathlib.Path(repo) / ".github/port-install.json"
    if tag and man.exists():
        try:
            m = json.loads(man.read_text(encoding="utf-8"))
        except ValueError:
            m = {}
        own = next((f.get("url", "") for f in m.get("files", []) if f.get("role") == "self"), "")
        if f"/releases/download/{tag}/" in own:
            for f in m.get("files", []):
                if (f.get("port") or "").lower() == dep_repo.lower():
                    hit = re.search(r"/releases/download/([^/]+)/", f.get("url", ""))
                    if hit:
                        return hit.group(1)
    text = tag_workflow(repo, tag)
    if text is None:
        return None
    m = re.search(rf"github\.com/{re.escape(dep_repo)}/releases/download/([^/\"]+)/", text, re.I)
    return m.group(1) if m else None


def tag_workflow(repo, tag=None):
    """port-ci.yml as release `tag` was built with it (the workflow at the tag), else the checked-out one. The
    checked-out branch's pin moves on when keep-current re-pins a dependency; the jar was built against the tag's."""
    if tag:
        r = git(repo, "show", f"{tag}:.github/workflows/port-ci.yml")
        if r.returncode == 0 and (r.stdout or "").strip():
            return r.stdout
    wf = pathlib.Path(repo) / ".github/workflows/port-ci.yml"
    return wf.read_text(encoding="utf-8") if wf.exists() else None


PIN_URL = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/releases/download/([^/\"'\s]+)/([^\"'\s]+)")


def library_pins(text):
    """[(owner/repo, tag, asset, sha256 or None)]: the release jars a Port CI workflow installs into mavenLocal (its
    --dep-jar and --dep-jar-variant pins, each line followed by its sha256 check). The kit's download is not one."""
    lines, out = (text or "").splitlines(), set()
    for i, ln in enumerate(lines):
        if ".m2/repository" not in ln:
            continue
        for r, t, a in PIN_URL.findall(ln):
            sha = next((m.group(0) for l2 in lines[i:i + 3] for m in [re.search(r"\b[0-9a-f]{64}\b", l2)] if m), None)
            out.add((r, t, a, sha))
    return sorted(out, key=lambda x: (x[0], x[1], x[2], x[3] or ""))


def played_pair_errors(repo, cfg, tag):
    """A release built against a library PRE-RELEASE that was not done when it was pinned (DEPS.md's mark) goes out
    only with the exact library release it was played with: each library jar the workflow at the tag pins must be a
    promoted (a person played it) Release, published on every platform this one goes to, and still the asset the
    tag's build checked (its sha256). The library's own done record is the round's business, not publishing's."""
    why = []
    pins = library_pins(tag_workflow(repo, tag))
    if not pins:
        why.append(f"{tag} is built against a library pre-release (.port DEPS.md mark) but its port-ci.yml pins no "
                   "library release jar to check")
    for r, t, a, sha in pins:
        try:
            rel = gh("GET", f"/repos/{r}/releases/tags/{t}")
        except SystemExit:
            why.append(f"{r}@{t}: no such release -- {tag} was built against it"); continue
        if rel.get("prerelease") or rel.get("draft"):
            why.append(f"{r}@{t} is still a pre-release: {tag} was played with it, so promote that library release "
                       "first (a person played the pair)")
        for plat in platforms(cfg):
            if plat not in published(rel.get("body")):
                why.append(f"{r}@{t} is not published on {plat} yet -- publish the library release {tag} was played with first")
        asset = next((x for x in rel.get("assets", []) if x.get("name") == a), None)
        if asset is None:
            why.append(f"{r}@{t} no longer has {a}, the jar {tag} was built against")
        elif sha and asset.get("digest") and asset["digest"] != f"sha256:{sha}":
            why.append(f"{r}@{t}/{a} is not the jar {tag} was built against (sha256 {sha[:12]}..., the release now has "
                       f"{asset['digest'][7:19]}...)")
    return list(dict.fromkeys(why))


def published(body):
    return {p: r for p, r in MARK_RE.findall(body or "")}


def gate(repo, cfg, tag, release, manual=False):
    """Every reason not to publish this release; empty means go."""
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
    if git(repo, "merge-base", "--is-ancestor", f"refs/tags/{tag}", "HEAD").returncode != 0:
        why.append(f"{tag} is not an ancestor of the checked-out branch")
    why += ledger_errors(cfg, tag)
    m_ = re.search(r"-mc(\d[\d.]*?)(?:-r\d+)?$", tag)
    if m_:   # built against a library pre-release that was not done (layer-deps.py's / round.py's mark): the pair
        mc_ = m_.group(1)
        deps_ = git(repo, "show", f"{tag}:.port/{mc_}/DEPS.md")
        props_ = git(repo, "show", f"{tag}:.port/{mc_}/branch.properties")
        if (deps_.returncode == 0 and "**Library pre-release (not done):**" in deps_.stdout) or \
                (props_.returncode == 0 and re.search(r"(?m)^port_jar_prerelease\.", props_.stdout)):
            why += played_pair_errors(repo, cfg, tag)
    for d in cfg.get("dependencies", []):
        if d.get("port") and d.get("type") == "required":
            dtag = pinned_dependency_tag(repo, d["port"], tag)
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

def notes(cfg, tag, release, repo_slug):
    """The changelog players see. Deliberately says nothing about defects in the original: bugs are tracked in the
    fork's GitHub issues, and a public page listing the original's bugs reads as disparaging the authors whose
    work this is. Only differences a player would notice ("differences" in the config) are stated, neutrally."""
    body = release.get("body") or ""
    played = re.search(r"Played by @[^\n]*", body)
    out = [f"**Unofficial community port** of {cfg.get('original_name', 'the original mod')} to NeoForge "
           f"{minecraft_of(tag)}, based on the original's version {version_of(tag, cfg.get('modid', '')).split('+')[0]}.", ""]
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


def port_config(cfg, port):
    """A port dependency's own publish config. It lives on that fork's port branch, not its default branch (the
    authors' branch, which never carries it), so it is read from the branch this publish checked out: sibling
    ports share the port-branch name (neoforge-<mc>). No branch known (a detached checkout): the default branch."""
    ref = cfg.get("port_branch")
    q = f"?ref={urllib.parse.quote(ref, safe='')}" if ref else ""
    return json.loads(gh("GET", f"/repos/{port}/contents/{CONFIG}{q}", accept="application/vnd.github.raw", raw=True))


def checked_out_branch(repo):
    r = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    b = (r.stdout or "").strip() if r.returncode == 0 else ""
    return b if b and b != "HEAD" else None


def dep_entries(cfg, plat):
    out = []
    for d in cfg.get("dependencies", []):
        if d.get("port"):
            other = port_config(cfg, d["port"])
            ref = (other.get(plat) or {}).get("slug")
        else:
            ref = d.get(plat)
        if ref:
            out.append((ref, d.get("type", "required")))
    return out


def modrinth_version(cfg, tag, release, text, dry):
    mr = cfg["modrinth"]
    mc = minecraft_of(tag)
    proj = modrinth("GET", f"/project/{mr['slug']}")
    have = {v["version_number"] for v in modrinth("GET", f"/project/{proj['id']}/version") or []}
    number = version_of(tag, cfg.get("modid", ""))
    if number in have:
        return proj, {"id": "existing", "version_number": number}
    asset = pick_asset(release, mr.get("asset", ""))
    deps = [{"project_id": modrinth("GET", f"/project/{s}")["id"], "dependency_type": t} for s, t in dep_entries(cfg, "modrinth")]
    data = {"project_id": proj["id"], "name": f"{number} (NeoForge {mc})", "version_number": number,
            "changelog": text, "dependencies": deps, "game_versions": [mc], "version_type": "release",
            # Modrinth keeps the environment PER VERSION; a version created without one copies an earlier version's,
            # so the first one would be "unknown" and block the project's review checklist
            "environment": cfg.get("environment", "client_and_server"),
            "loaders": ["neoforge"], "featured": True, "file_parts": ["file"], "primary_file": "file"}
    if dry:
        print("modrinth version:", json.dumps({**data, "changelog": "<notes>", "file": asset["name"]}, indent=1))
        return proj, None
    body, h = multipart({"data": json.dumps(data)}, {"file": (asset["name"], download(asset), "application/java-archive")})
    return proj, modrinth("POST", "/version", body, h)


# Modrinth's environment (the config's "environment") as CurseForge's environment versions. CurseForge refuses a file
# with none ("You must select at least one version from the environment group of versions", errorCode 1021).
CF_SIDES = {"client_and_server": ("Client", "Server"), "singleplayer_only": ("Client", "Server"),
            "client_or_server": ("Client", "Server"), "client_or_server_prefers_both": ("Client", "Server"),
            "client_only": ("Client",), "client_only_server_optional": ("Client",),
            "server_only": ("Server",), "server_only_client_optional": ("Server",), "dedicated_server_only": ("Server",)}


def curseforge_ids(mc, environment="client_and_server"):
    types = curseforge("GET", "/game/version-types")
    vers = curseforge("GET", "/game/versions")
    tslug = {t["id"]: t["slug"] for t in types}
    mcid = [v["id"] for v in vers if v["name"] == mc and tslug.get(v["gameVersionTypeID"], "").startswith("minecraft-")]
    loader = [v["id"] for v in vers if v["name"] == "NeoForge" and tslug.get(v["gameVersionTypeID"]) == "modloader"]
    if len(mcid) != 1 or len(loader) != 1:
        raise SystemExit(f"CurseForge game versions: {len(mcid)} match Minecraft {mc}, {len(loader)} match NeoForge")
    sides = CF_SIDES.get(environment, ("Client", "Server"))
    env = []
    for side in sides:     # CurseForge's docs name them only by name ("Client", "Server"); prefer an environment type
        hits = [v for v in vers if v["name"] == side]
        if len(hits) > 1:
            hits = [v for v in hits if "environment" in tslug.get(v["gameVersionTypeID"], "")]
        if len(hits) != 1:
            raise SystemExit(f"CurseForge game versions: {len(hits)} environment versions named {side!r} "
                             f"(needed for environment {environment})")
        env.append(hits[0]["id"])
    return mcid + loader + env


def curseforge_file(cfg, tag, release, text, dry):
    cf = cfg["curseforge"]
    mc = minecraft_of(tag)
    asset = pick_asset(release, cf.get("asset", ""))
    kinds = {"required": "requiredDependency", "optional": "optionalDependency"}
    meta = {"changelog": text, "changelogType": "markdown", "displayName": f"{version_of(tag, cfg.get('modid', ''))} (NeoForge {mc})",
            "releaseType": "release"}
    rel = [{"slug": s, "type": kinds.get(t, "requiredDependency")} for s, t in dep_entries(cfg, "curseforge")]
    if rel:         # CurseForge rejects an empty list ("Array item count 0 is less than minimum count of 1")
        meta["relations"] = {"projects": rel}
    if cf.get("manual_release"):
        # approved, but not released until the author clicks Release on the website: its library's file for the same
        # Minecraft version may still be in CurseForge's manual review, and a live port without it does not load
        meta["isMarkedForManualRelease"] = True
    if dry:
        print("curseforge file:", json.dumps({**meta, "changelog": "<notes>", "file": asset["name"],
                                              "project_id": cf["project_id"]}, indent=1))
        return None
    meta["gameVersions"] = curseforge_ids(mc, cfg.get("environment", "client_and_server"))
    body, h = multipart({"metadata": json.dumps(meta)}, {"file": (asset["name"], download(asset), "application/java-archive")})
    return curseforge("POST", f"/projects/{cf['project_id']}/upload-file", body, h)



GATES_DOC = re.compile(r"^((?!src/)[^\n]*\.md|LICENSE[^/]*|\.github/.*|\.port/(?:[^/]+/)?KNOWN-ISSUES\.json)$")  # nothing in a jar
# (the last: the known-issues ledger, tools/known-issues.py -- a record of findings, never port code; the root
# README.md is a record through the first: any .md outside src/, while src/main/resources/README.md stays jar content;
# tools/fork-branches.py reads it)


# ---------------------------------------------------------------- commands

def publish(repo, tag, dry=False, manual=False, platform=None):
    cfg = load_config(repo)
    if not cfg:
        print(f"::notice::no {CONFIG}: this fork is not set up to publish (port-publish.py setup)"); return 0
    repo_slug = slug_of(repo)
    cfg["port_branch"] = checked_out_branch(repo)       # where a port dependency's own config is read (port_config)
    release = gh("GET", f"/repos/{repo_slug}/releases/tags/{tag}")
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
    want = asked(platforms(cfg), platform)
    held = held_back(repo, minecraft_of(tag), [p for p in want if p not in done])
    for plat in ("modrinth", "curseforge"):
        if plat not in platforms(cfg):
            print(f"::notice::{plat}: no project configured yet -- skipped"); continue
        if plat not in want:
            print(f"{plat}: not asked (--platform {platform}) -- skipped"); continue
        if plat in done:
            print(f"{plat}: already published ({done[plat]}) -- skipped"); continue
        if plat in held:
            print(f"::warning::{plat}: HELD BACK, not uploaded: {held[plat]}"); continue
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
            if p == "curseforge" and cfg["curseforge"].get("manual_release"):
                print(f"::notice::{tag} on CurseForge is HELD (curseforge.manual_release): once CurseForge approves it, "
                      "it stays hidden until you click Release on the file's page -- after the library's file for the "
                      "same Minecraft version is live")
    for m in missing:
        print(f"::error::{m}")
    if held and not links and all(p in held or p in done for p in want):
        print(f"::error::will not publish {tag}: every platform asked is held back by an open S1 (above)")
        return 1
    return 1 if missing else 0


def set_disclosures(cfg, project_id):
    """Modrinth's content disclosures (mandatory where they apply): the config's "disclosures", written by setup --
    a port is always a derivative work, and this tool's ports and pages are made with an AI assistant."""
    d = cfg.get("disclosures") or []
    if d:
        modrinth_v3("PATCH", f"/project/{project_id}/disclosures", {"set": d, "remove": []})
        print("modrinth disclosures set: " + ", ".join(x["type"] for x in d))


def disclosures_for(name, authors, mc, orig_link):
    """The disclosures every port made with this tool needs on Modrinth: derivative work (the original, linked) and
    AI-generated content (the port's code changes and the page text). The user reviews them in the config."""
    return [{"type": "derivative_work",
             "sources": [{"label": f"{name} by {authors}", "link": orig_link,
                          "note": f"An unofficial port of the original to NeoForge {mc}, with the original's content."}]},
            {"type": "ai_content", "uses": ["code", "text"],
             "note": f"The changes that port the original to NeoForge {mc}, and this page's text, were made with an AI "
                     "coding assistant and checked by automated gates and a person playing it. The mod's content, "
                     "art and design are the original author's."}]


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
        set_disclosures(cfg, p["id"])
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
    set_disclosures(cfg, p["id"])
    print(f"created the Modrinth DRAFT project {p['slug']} ({p['id']}): https://modrinth.com/mod/{p['slug']} -- "
          "it stays a draft until a version is uploaded and it is submitted for review on the website")
    return 0


def curseforge_name(name):
    """The name to type into CurseForge's create-project form. CurseForge moderation refuses a project name with game
    version numbers in it ("contains words or phrases that are not allowed: Game version numbers"), so the scope
    there is the loader, not the Minecraft version; the slug CurseForge derives from it goes into curseforge.slug."""
    return f"{name} - Community Port (NeoForge)"


def page_title(name, mc):
    """'<name> - Community Port (<mc>+)': the scope is in the title. Modrinth caps titles at 64 characters, so a
    long name is cut to fit rather than the scope dropped."""
    suffix = f" - Community Port ({mc}+)"
    return name + suffix if len(name + suffix) <= 64 else name[:64 - len(suffix)].rstrip() + suffix


def original_curseforge(name):
    """The original's CurseForge page, found by exact name through the registry tool (needs its CurseForge key);
    None when it cannot be found, and the page then links Modrinth and the source only."""
    r = subprocess.run([sys.executable, str(ROOT / "mod-registry" / "modreg.py"), "search", "--query", name],
                       capture_output=True, text=True, encoding="utf-8", check=False)
    try:
        hits = json.loads(r.stdout)["results"]
    except (ValueError, KeyError):
        return None
    for h in hits:
        if h.get("name", "").lower() == name.lower():
            for p in h.get("providers", []):
                if p.get("provider") == "curseforge" and p.get("url"):
                    return p["url"]
    return None


def original_credits_file(up):
    """(file name, URL) of the metadata file holding the original's credits, on the original repository's default
    branch -- the page says where its credits come from."""
    m = re.match(r"https://github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?/?$", up or "")
    if not m:
        return None
    tok = os.environ.get("GITHUB_TOKEN")
    for path in ("src/main/resources/META-INF/mods.toml", "src/main/resources/META-INF/neoforge.mods.toml",
                 "src/main/templates/META-INF/mods.toml", "src/main/templates/META-INF/neoforge.mods.toml"):
        try:
            f = http("GET", f"{GITHUB}/repos/{m.group(1)}/contents/{path}", "Authorization",
                     f"Bearer {tok}" if tok else None)
            return pathlib.Path(path).name, f["html_url"]
        except SystemExit:
            continue
    return None


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
    title = page_title(name, mc)
    orig_cf = original_curseforge(name)
    credits_src = original_credits_file(up)
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
           "environment": "client_and_server",
           "disclosures": disclosures_for(name, authors, mc, f"https://modrinth.com/mod/{orig['slug']}" if orig else up),
           "dependencies": deps,
           "modrinth": {"slug": f"{oslug}-neoforge", "asset": variant("_mr.jar")},
           "curseforge": {"project_id": None, "slug": f"{oslug}-neoforge", "asset": variant("_cf.jar")}}
    credits = str(mod.get("credits", "")).strip()
    where = [*([f"[Modrinth](https://modrinth.com/mod/{orig['slug']})"] if orig else []),
             *([f"[CurseForge]({orig_cf})"] if orig_cf else []), *([f"[source]({up})"] if up else [])]
    lines = [f"# {title}", "",
             *([f"**The original:** {name} by {authors} -- {' · '.join(where)}", ""] if where else []),
             f"**Unofficial.** A community-maintained port of **{name}** by **{authors}** to NeoForge {mc}. "
             f"It is not made or supported by {authors}: please report problems with this port "
             f"[on its own issue tracker](https://github.com/{fork}/issues), not to the original author.", "",
             f"**Scope:** NeoForge, Minecraft {mc} and newer. Builds are published for Minecraft {mc}.", "",
             f"Published with the original author's permission ({permission}), under the original "
             f"{cfg['license']} licence with its copyright notice kept.", "",
             "## The original", "",
             *([f"- {orig['description']}"] if orig else []),
             *([f"- Made for Forge {', '.join(orig['game_versions'][-1:])}."] if orig else []), "",
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
        src = f"[{credits_src[0]}]({credits_src[1]})" if credits_src else "mods.toml"
        lines += ["## Credits", "",
                  f"Exactly as {authors} wrote them in the original mod's own credits ({src}), unchanged:", "",
                  *[f"- {c.strip()}" for c in re.split(r"[\n,]", credits) if c.strip()], ""]
    lines += ["## Licence", "", f"{cfg['license']}, copyright {authors}. See LICENSE.txt in the source."]
    target = pathlib.Path(out) if out else repo
    (target / BODY).parent.mkdir(parents=True, exist_ok=True)
    (target / BODY).write_text("\n".join(lines) + "\n", encoding="utf-8")
    (target / CONFIG).write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {target / CONFIG} and {target / BODY} -- review both; curseforge.project_id is set after the "
          "project is created on the website")
    print("\nCurseForge create-project form (https://authors.curseforge.com, Create Project, Minecraft, Mods):")
    print(f"  Name:        {curseforge_name(name)}   (no version numbers: CurseForge refuses them in a name)\n  Summary:     {cfg['summary']}\n  Description: paste {BODY}\n"
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
    expect(page_title("Example Mod", "1.21.1") == "Example Mod - Community Port (1.21.1+)", "title carries the scope")
    expect(not re.search(r"\d+\.\d+", curseforge_name("Example Mod")), "the CurseForge name has no game version number")
    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        curseforge_file({"modid": "m", "curseforge": {"project_id": 1, "asset": ""}}, "m-1.0.0-mc1.21.1",
                        {"assets": [{"name": "m-1.0.0.jar"}]}, "notes", True)
    expect('"relations"' not in buf.getvalue(), "a mod with no dependencies sends no relations (CurseForge rejects an empty list)")
    expect(len(page_title("A Very Long Mod Name That Goes On And On Forever", "1.21.1")) <= 64
           and page_title("A Very Long Mod Name That Goes On And On Forever", "1.21.1").endswith("(1.21.1+)"),
           "a long title is cut to Modrinth's 64 characters, keeping the scope")
    expect(minecraft_of("examplemod-1.0.0-mc26.1.2-r2") == "26.1.2" and minecraft_of("examplemod-1.0.0-mc26.2") == "26.2",
           "minecraft from a 26.x tag")
    expect(version_of("examplemod-1.0.0-mc1.21.1-r2", "examplemod") == "1.0.0+1.21.1-r2", version_of("examplemod-1.0.0-mc1.21.1-r2", "examplemod"))
    expect(version_of("examplemod-1.1.2-mc1.21.1", "examplemod") == "1.1.2+1.21.1", "version, no suffix")
    expect(version_of("examplemod-1.1.1-fix-fix-mc1.21.1-r6", "examplemod") == "1.1.1-fix-fix+1.21.1-r6", "fix suffix")
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
        class RS:
            def __init__(self, rc, out): self.returncode, self.stdout = rc, out
        # a release built against a library pre-release that was not done (the mark at the tag) goes out with the exact
        # library release the pair was played with: promoted, published on the platform, the asset the build checked
        sha_ = "ab" * 32
        wf_ = ('          curl -fsSL -o "$HOME/.m2/repository/com/example/lib/1/lib-1.jar" '
               '"https://github.com/o/lib/releases/download/lib-1-mc1.21.1-r3/lib-1.jar"\n'
               f'          echo "{sha_}  $HOME/.m2/repository/com/example/lib/1/lib-1.jar" | sha256sum -c -\n'
               '          curl -fsSL -o "$RUNNER_TEMP/kit.tar.gz" "https://github.com/o/mmvu/releases/download/port-ci-kit-v9/k.tar.gz"\n')
        def pair_git(mark="deps", workflow=wf_):
            def g(repo, *a):
                if a[:1] == ("show",) and a[1].endswith("/DEPS.md"):
                    return RS(0, "**Library pre-release (not done):** `com.example:lib`\n") if mark == "deps" else RS(1, "")
                if a[:1] == ("show",) and a[1].endswith("/branch.properties"):
                    return RS(0, "port_jar_prerelease.com.example\\:lib=lib-1-mc1.21.1-r3 is a pre-release\n") \
                        if mark == "props" else RS(1, "")
                if a[:1] == ("show",) and a[1].endswith("/port-ci.yml"):
                    return RS(0, workflow) if workflow else RS(128, "")
                return RS(0, "")
            return g
        def lib_gh(lib):
            def g(method, path, data=None, raw=False, accept=None):
                return lib if path.endswith("/releases/tags/lib-1-mc1.21.1-r3") else fake_gh(method, path, data, raw, accept)
            return g
        promoted = {"prerelease": False, "body": "<!-- port-publish:modrinth:abc -->",
                    "assets": [{"name": "lib-1.jar", "digest": f"sha256:{sha_}"}]}
        expect(library_pins(wf_) == [("o/lib", "lib-1-mc1.21.1-r3", "lib-1.jar", sha_)],
               f"the library pins are the mavenLocal jars with their sha256, not the kit: {library_pins(wf_)}")
        def pair(mark="deps", lib=promoted, workflow=wf_):
            global git, gh
            saved_ = (git, gh)
            git, gh = pair_git(mark, workflow), lib_gh(lib)
            try:
                return gate(".", cfg, "m-2-mc1.21.1", good)
            finally:
                git, gh = saved_
        expect(pair() == [], f"the played pair goes out once the library release is promoted and published: {pair()}")
        expect(pair("props") == [], "the branch.properties key alone is judged the same way")
        why_pre = pair(lib={**promoted, "prerelease": True})
        expect(any("still a pre-release" in w for w in why_pre), f"the library release still a pre-release refuses: {why_pre}")
        why_pre = pair(lib={**promoted, "body": ""})
        expect(any("not published on modrinth" in w for w in why_pre), f"the library release unpublished refuses: {why_pre}")
        why_pre = pair(lib={**promoted, "assets": [{"name": "lib-1.jar", "digest": "sha256:" + "cd" * 32}]})
        expect(any("not the jar" in w for w in why_pre), f"a library asset that is not the jar the build checked refuses: {why_pre}")
        why_pre = pair(lib={**promoted, "assets": []})
        expect(any("no longer has" in w for w in why_pre), f"a library asset gone refuses: {why_pre}")
        why_pre = pair(workflow="")
        expect(any("pins no library release" in w for w in why_pre), f"the mark with no pin to check refuses: {why_pre}")
        expect(pair(mark=None, lib={**promoted, "prerelease": True}) == [],
               "no mark: the pair check does not run (the port dependency check judges the dependency)")
        expect(any("not published by" in w for w in gate(".", cfg, "m-2-mc1.21.1", {**good, "author": {"login": "x"}})), "foreign author refuses")
        expect(any("permission" in w for w in gate(".", {**cfg, "permission": ""}, "m-2-mc1.21.1", good)), "no permission refuses")
        expect(any("auto is off" in w for w in gate(".", {**cfg, "auto": False}, "m-2-mc1.21.1", good)), "auto off refuses unattended")
        expect(not any("auto is off" in w for w in gate(".", {**cfg, "auto": False}, "m-2-mc1.21.1", good, manual=True)), "manual ignores auto")
        dep = {**cfg, "dependencies": [{"port": "o/lib", "type": "required"}]}
        global pinned_dependency_tag
        real_pin = pinned_dependency_tag
        pinned_dependency_tag = lambda repo, d, tag=None: "lib-1-mc1.21.1"
        try:
            expect(any("not published on modrinth" in w for w in gate(".", dep, "m-2-mc1.21.1", good)), "unpublished dependency refuses")
        finally:
            pinned_dependency_tag = real_pin
        git = lambda repo, *a: R(1)
        expect(any("not an ancestor" in w for w in gate(".", cfg, "m-2-mc1.21.1", good)), "foreign tag refuses")
        # the dependency a release goes out with is the pin of the workflow AT THE TAG, not the branch's moved-on one
        import tempfile
        with tempfile.TemporaryDirectory() as d_:
            (pathlib.Path(d_) / ".github/workflows").mkdir(parents=True)
            (pathlib.Path(d_) / ".github/workflows/port-ci.yml").write_text(
                "x https://github.com/o/lib/releases/download/lib-1-mc1.21.1-r4/lib-1.jar\n", encoding="utf-8")
            git = lambda repo, *a: RS(0, "x https://github.com/o/lib/releases/download/lib-1-mc1.21.1-r3/lib-1.jar\n") \
                if a[:2] == ("show", "m-2-mc1.21.1-r3:.github/workflows/port-ci.yml") else RS(128, "")
            expect(pinned_dependency_tag(d_, "o/lib", "m-2-mc1.21.1-r3") == "lib-1-mc1.21.1-r3",
                   "a release is judged by the pin it was built with, at its tag")
            expect(pinned_dependency_tag(d_, "o/lib", "m-9-mc1.21.1") == "lib-1-mc1.21.1-r4"
                   and pinned_dependency_tag(d_, "o/lib") == "lib-1-mc1.21.1-r4", "no such tag here: the checked-out workflow")
    finally:
        gh, git = real_gh, real_git
    seen, real_gh0 = [], gh
    gh = lambda method, path, data=None, raw=False, accept=None: (seen.append(path), json.dumps({"curseforge": {"slug": "lib-cf"}}))[1]
    try:
        dc = {"dependencies": [{"port": "o/lib", "type": "required"}], "port_branch": "neoforge-1.21.1"}
        expect(dep_entries(dc, "curseforge") == [("lib-cf", "required")] and seen[-1].endswith("?ref=neoforge-1.21.1"),
               f"a port dependency's config is read from the port branch, not the default branch: {seen}")
        dep_entries({"dependencies": [{"port": "o/lib", "type": "required"}]}, "curseforge")
        expect("?ref=" not in seen[-1], "no branch known: the default branch, as before")
    finally:
        gh = real_gh0
    dsc = disclosures_for("Example Mod", "Someone", "1.21.1", "https://modrinth.com/mod/examplemod")
    expect([x["type"] for x in dsc] == ["derivative_work", "ai_content"] and dsc[0]["sources"][0]["link"].endswith("examplemod")
           and dsc[1]["uses"] == ["code", "text"], "setup's disclosures: derivative work (linked) and AI-generated code/text")
    global modrinth_v3
    sent, real_v3 = [], modrinth_v3
    modrinth_v3 = lambda method, path, data=None: sent.append((method, path, data))
    try:
        set_disclosures({"disclosures": dsc}, "PID")
        set_disclosures({}, "PID")
        expect(sent == [("PATCH", "/project/PID/disclosures", {"set": dsc, "remove": []})],
               f"disclosures go to the v3 endpoint once, and nothing is sent without any: {sent}")
    finally:
        modrinth_v3 = real_v3
    global curseforge
    real_cf = curseforge
    def fake_cf(with_env):
        def f(method, path, data=None, headers=None):
            if path == "/game/version-types":
                return [{"id": 1, "slug": "minecraft-1-21"}, {"id": 2, "slug": "modloader"}, {"id": 3, "slug": "environment"}, {"id": 4, "slug": "other"}]
            return [{"id": 10, "name": "1.21.1", "gameVersionTypeID": 1}, {"id": 11, "name": "NeoForge", "gameVersionTypeID": 2}] + (
                [{"id": 12, "name": "Client", "gameVersionTypeID": 3}, {"id": 13, "name": "Server", "gameVersionTypeID": 3},
                 {"id": 14, "name": "Client", "gameVersionTypeID": 4}] if with_env else [])   # a same-named version of another type
        return f
    try:
        curseforge = fake_cf(True)
        expect(curseforge_ids("1.21.1") == [10, 11, 12, 13], "CurseForge: Minecraft, NeoForge, and Client + Server for client_and_server")
        expect(curseforge_ids("1.21.1", "client_only") == [10, 11, 12], "CurseForge: a client-only mod is tagged Client only")
        curseforge = fake_cf(False)
        try:
            curseforge_ids("1.21.1"); expect(False, "no environment versions on CurseForge must stop the upload")
        except SystemExit as e:
            expect("environment versions" in str(e), f"the stop names the missing environment versions: {e}")
    finally:
        curseforge = real_cf
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        (pathlib.Path(d) / ".github/workflows").mkdir(parents=True)
        (pathlib.Path(d) / ".github/workflows/port-ci.yml").write_text(
            'curl -fsSL -o "x.jar" "https://github.com/o/lib/releases/download/lib-1.0-mc1.21.1-r4/lib_mr.jar"\n', encoding="utf-8")
        (pathlib.Path(d) / ".github/port-install.json").write_text(json.dumps({"files": [
            {"role": "self", "url": "https://github.com/o/mod/releases/download/mod-2.0-mc1.21.1/mod.jar"},
            {"role": "required", "port": "o/lib", "url": "https://github.com/o/lib/releases/download/lib-1.0-mc1.21.1-r3/lib_mr.jar"}]}),
            encoding="utf-8")
        expect(pinned_dependency_tag(d, "o/lib", "mod-2.0-mc1.21.1") == "lib-1.0-mc1.21.1-r3",
               "a promoted release is published with the dependency its manifest records, not a newer re-pin")
        expect(pinned_dependency_tag(d, "o/lib", "mod-2.1-mc1.21.1") == "lib-1.0-mc1.21.1-r4",
               "a release the manifest does not describe falls back to the workflow's pin")
        expect(pinned_dependency_tag(d, "o/lib") == "lib-1.0-mc1.21.1-r4", "no tag: the workflow's pin, as before")
    import contextlib, io
    for held in (False, True):
        buf = io.StringIO()
        cf_cfg = {"project_id": 1, "asset": ""}
        if held:
            cf_cfg["manual_release"] = True
        with contextlib.redirect_stdout(buf):
            curseforge_file({"modid": "m", "curseforge": cf_cfg}, "m-1.0.0-mc1.21.1", {"assets": [{"name": "m.jar"}]}, "n", True)
        expect(('"isMarkedForManualRelease": true' in buf.getvalue()) == held,
               f"curseforge.manual_release {held}: the upload {'is' if held else 'is not'} held for a manual release")
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
    p.add_argument("--platform", choices=("mr", "cf", "both"), default="both",
                   help="upload to Modrinth (mr) or CurseForge (cf) only (default both); a platform an open S1 in the "
                        "known-issues ledger blocks is held back either way")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if getattr(a, "repo", None):
        import importlib.util
        spec = importlib.util.spec_from_file_location("fork_kind_pp", pathlib.Path(__file__).resolve().parent / "fork-kind.py")
        fk = importlib.util.module_from_spec(spec); spec.loader.exec_module(fk)
        why = fk.refuse_layer(pathlib.Path(a.repo), "port-publish")
        if why:
            print(f"::error::{why}"); return 2
    if a.cmd == "setup":
        if not a.permission.startswith("https://"):
            sys.exit("--permission must be the https link where the authors agreed")
        return setup(a.repo, a.permission, a.branch, a.out, a.ledger_issue, a.difference)
    if a.cmd == "modrinth-create":
        return modrinth_create(a.repo)
    if a.cmd == "publish":
        return publish(a.repo, a.tag, a.dry_run, a.manual, None if a.platform == "both" else a.platform)
    ap.print_help(); return 2


if __name__ == "__main__":
    sys.exit(main())
