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
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=False)


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


def version_of(tag, modid):
    v = re.sub(r"-mc\d+\.\d+(?:\.\d+)?", "+mc", tag[len(modid) + 1:] if tag.startswith(modid + "-") else tag)
    return v.replace("+mc", f"+neoforge-{minecraft_of(tag)}")


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
    m = re.search(rf"github\.com/{re.escape(dep_repo)}/releases/download/([^/\"]+)/", wf.read_text(), re.I)
    return m.group(1) if m else None


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
    for d in cfg.get("dependencies", []):
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

def notes(cfg, tag, release, repo_slug):
    body = release.get("body") or ""
    played = re.search(r"Played by @[^\n]*", body)
    first = body.split("\n\n")[0].strip()
    rows = []
    fl = _ledger_mod()
    for ref in cfg.get("ledger_issues", []):
        owner_repo, n = ref.split("#")
        try:
            led = fl.parse(gh("GET", f"/repos/{owner_repo}/issues/{n}")["body"] or "")
        except SystemExit:
            continue
        for m in led.get("modes", []):
            for col, s in m.get("status", {}).items():
                if fl.column_matches(col, tag) and s.get("state") in ("fixed", "fixed-unconfirmed"):
                    rows.append(f"| {m.get('severity')} | {m.get('what', m.get('id'))[:140]} | "
                                f"[{ref.split('/')[-1]}](https://github.com/{owner_repo}/issues/{n}) |")
    out = [f"**Unofficial community port** to NeoForge {minecraft_of(tag)}. {first}" if not first.startswith(
        "**Unofficial") else first, ""]
    if rows:
        out += ["Fixed in this build (compared with the original):", "", "| severity | what | issue |",
                "|---|---|---|", *dict.fromkeys(rows), ""]
    out += [f"Every build passes this port's gates (it builds, loads on a server and in a real client) and is played "
            f"by a person before release. {played.group(0) if played else ''}".strip(), "",
            f"Release on GitHub: https://github.com/{repo_slug}/releases/tag/{tag} -- report problems with this "
            f"port at https://github.com/{repo_slug}/issues, not to the original author.",
            f"Published with the original author's permission: {cfg['permission']}"]
    return "\n".join(out)


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


def modrinth_version(cfg, tag, release, text, dry):
    mr = cfg["modrinth"]
    proj = modrinth("GET", f"/project/{mr['slug']}")
    have = {v["version_number"] for v in modrinth("GET", f"/project/{proj['id']}/version") or []}
    number = version_of(tag, cfg.get("modid", ""))
    if number in have:
        return proj, {"id": "existing", "version_number": number}
    asset = pick_asset(release, mr.get("asset", ""))
    deps = [{"project_id": modrinth("GET", f"/project/{s}")["id"], "dependency_type": t} for s, t in dep_entries(cfg, "modrinth")]
    data = {"project_id": proj["id"], "name": f"{cfg['title']} {number}", "version_number": number,
            "changelog": text, "dependencies": deps, "game_versions": [minecraft_of(tag)], "version_type": "release",
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


def curseforge_file(cfg, tag, release, text, dry):
    cf = cfg["curseforge"]
    asset = pick_asset(release, cf.get("asset", ""))
    kinds = {"required": "requiredDependency", "optional": "optionalDependency"}
    meta = {"changelog": text, "changelogType": "markdown", "displayName": f"{cfg['title']} {version_of(tag, cfg.get('modid', ''))}",
            "releaseType": "release",
            "relations": {"projects": [{"slug": s, "type": kinds.get(t, "requiredDependency")}
                                       for s, t in dep_entries(cfg, "curseforge")]}}
    if dry:
        print("curseforge file:", json.dumps({**meta, "changelog": "<notes>", "file": asset["name"],
                                              "project_id": cf["project_id"]}, indent=1))
        return None
    meta["gameVersions"] = curseforge_ids(minecraft_of(tag))
    body, h = multipart({"metadata": json.dumps(meta)}, {"file": (asset["name"], download(asset), "application/java-archive")})
    return curseforge("POST", f"/projects/{cf['project_id']}/upload-file", body, h)


# ---------------------------------------------------------------- commands

def publish(repo, tag, dry=False, manual=False):
    cfg = load_config(repo)
    if not cfg:
        print(f"::notice::no {CONFIG}: this fork is not set up to publish (port-publish.py setup)"); return 0
    repo_slug = slug_of(repo)
    release = gh("GET", f"/repos/{repo_slug}/releases/tags/{tag}")
    why = gate(repo, cfg, tag, release, manual)
    if why:
        for w in why:
            print(f"::error::will not publish {tag}: {w}")
        return 1
    done = published(release.get("body"))
    text, links, missing = notes(cfg, tag, release, repo_slug), [], []
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
    try:
        p = modrinth("GET", f"/project/{cfg['modrinth']['slug']}")
        print(f"modrinth project {p['slug']} already exists ({p['id']}, status {p['status']}) -- nothing to create")
        return 0
    except SystemExit as e:
        if "HTTP 404" not in str(e):
            raise
    body_md = (pathlib.Path(repo) / cfg.get("body_file", BODY)).read_text(encoding="utf-8")
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


def setup(repo, permission, branch, out=None):
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
    for f in install.get("files", []):
        if f.get("role") != "required":
            continue
        if f.get("port"):
            deps.append({"port": f["port"], "type": "required"})
        else:
            deps.append({"modrinth": f["modid"], "curseforge": f["modid"], "type": "required"})
    for f in install.get("files", []):
        if f.get("role") == "extra" and f.get("modid", "").startswith(("geckolib",)):
            deps.append({"modrinth": "geckolib", "curseforge": "geckolib", "type": "optional"})
    fork = slug_of(repo)
    title = f"{name} (NeoForge {mc} community port)"
    oslug = (orig or {}).get("slug") or modid.replace("_", "-")
    jars = [a for a in ("_mr.jar", "_cf.jar")]
    cfg = {"schema": 1, "permission": permission, "auto": True, "modid": modid, "title": title,
           "summary": f"Unofficial NeoForge {mc} port of {name} by {authors}, maintained by the community with their permission.",
           "body_file": BODY, "icon": f"src/main/resources/{mod['logoFile']}" if mod.get("logoFile") else None,
           "license": toml.get("license", "MIT"), "categories": (orig or {}).get("categories", []),
           "additional_categories": (orig or {}).get("additional_categories", []),
           "ledger_issues": [], "dependencies": deps,
           "modrinth": {"slug": f"{oslug}-neoforge", "asset": "_mr.jar"},
           "curseforge": {"project_id": None, "slug": f"{oslug}-neoforge", "asset": "_cf.jar"}}
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
             "## What this port changes", "",
             f"- Runs on **NeoForge {mc}**.",
             "- Carries fixes for defects found while porting and playing it; each is tracked, with evidence, in "
             f"this port's issues: https://github.com/{fork}/issues?q=label%3A%22%22+is%3Aissue", "",
             "## Requirements", ""]
    for d in deps:
        lines.append(f"- {'Required' if d['type'] == 'required' else 'Optional'}: "
                     + (f"the community port of https://github.com/{d['port']}" if d.get("port") else d["modrinth"]))
    lines += ["", "## How releases are made", "",
              f"Every release is built by the original author's own build from https://github.com/{fork}, passes "
              "automated checks (it builds, loads on a dedicated server, runs in a real client), and is played by a "
              "person before it is published here.", ""]
    if credits:
        lines += ["## Credits", "", *[f"- {c.strip()}" for c in re.split(r"[\n,]", credits) if c.strip()], ""]
    lines += ["## Licence", "", f"{cfg['license']}, copyright {authors}. See LICENSE.txt in the source."]
    target = pathlib.Path(out) if out else repo
    (target / BODY).parent.mkdir(parents=True, exist_ok=True)
    (target / BODY).write_text("\n".join(lines).replace("?q=label%3A%22%22+is%3Aissue", "") + "\n", encoding="utf-8")
    (target / CONFIG).write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {target / CONFIG} and {target / BODY} -- review both: ledger_issues is empty until you list "
          "the issues carrying fix ledgers, and curseforge.project_id is set after the project is created")
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
    expect(minecraft_of("eca-1.1.9-mc1.21.1") == "1.21.1", "minecraft from tag")
    expect(version_of("eca-1.1.9-mc1.21.1-r2", "eca") == "1.1.9+neoforge-1.21.1-r2", version_of("eca-1.1.9-mc1.21.1-r2", "eca"))
    expect(version_of("the_last_sword-1.1.2-mc1.21.1", "the_last_sword") == "1.1.2+neoforge-1.21.1", "version, no suffix")
    body = "notes\n\nPublished on Modrinth: x " + MARK.format(platform="modrinth", ref="AbCd1234")
    expect(published(body) == {"modrinth": "AbCd1234"}, "markers parse")
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
    print("self-check:", "ok" if ok else "FAILED")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-check", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("setup"); s.add_argument("--repo", required=True); s.add_argument("--permission", required=True)
    s.add_argument("--branch", default="neoforge-1.21.1"); s.add_argument("--out", help="write here instead of into the clone")
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
        return setup(a.repo, a.permission, a.branch, a.out)
    if a.cmd == "modrinth-create":
        return modrinth_create(a.repo)
    if a.cmd == "publish":
        return publish(a.repo, a.tag, a.dry_run, a.manual)
    ap.print_help(); return 2


if __name__ == "__main__":
    sys.exit(main())
