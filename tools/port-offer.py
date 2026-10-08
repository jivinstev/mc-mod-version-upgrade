#!/usr/bin/env python3
"""Show a fork's port to the mod's authors: a clean branch, the links, and a write-up -- nothing is sent.

    python3 tools/port-offer.py --repo <fork checkout> [--branch neoforge-1.21.1] [--base origin/main]
                                [--upstream owner/repo] [--work-dir DIR] [--push]

The last step of a fork port (.claude/skills/port-fork). A port branch carries the port commits plus OUR CI commit
(tools/port-ci.py), which the authors never asked for. This makes `<branch>-upstream`: the same branch without
the CI commit(s), so a compare view shows the authors only the port. Then it writes OFFER.md in the port's work
dir with:
  - the compare link in the fork (author's code -> the port), which is what to send people;
  - a "propose a PR" link on the AUTHOR's repository, pre-filled from the fork, which opens nothing by itself;
  - the diff's size (files and lines against the author's whole tree), the commits, and what was verified,
    read from the pipeline's own state.json (licence, Gate A/B, the author's build) -- never from prose;
  - a short message for the authors and a PR description, as drafts for a person to edit and send.

It never opens a PR, comments, or contacts anyone: proposing changes to someone else's repository is the
owner's decision. --push pushes the `-upstream` branch to the fork (origin) so the links resolve; without it
the command to run is printed. Standard library only (network only to read the fork's parent when --upstream
is not given).
"""
import argparse, json, os, pathlib, re, subprocess, sys

CI_FILES = {".github/workflows/port-ci.yml", ".github/port-install.json"}
MANIFEST = ".github/port-install.json"


def git(repo, *a, check=True):
    r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if check and r.returncode:
        sys.exit(f"git {' '.join(a)}: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def commits(repo, base, branch):
    """[(sha, subject, files)] oldest first."""
    out = []
    for sha in git(repo, "rev-list", "--reverse", f"{base}..{branch}").split():
        files = [f for f in git(repo, "show", "--format=", "--name-only", sha).splitlines() if f]
        out.append((sha, git(repo, "log", "-1", "--format=%s", sha), files))
    return out


def is_ci(files):
    return bool(files) and set(files) <= CI_FILES


def offer_tip(cs, repo=None):
    """The commit the authors should see: the branch without its CI commits. CI commits at the tip are just left
    off. A CI commit in the middle (a port fix landed after CI was added) needs the port commits after it replayed
    without it: each one keeps its own tree except the CI files, its author, dates and message, so the replay is
    the same commit on every run, and the port branch itself is never rewritten. -> (tip, port commits, CI)."""
    port = [c for c in cs if not is_ci(c[2])]
    ci = [c for c in cs if is_ci(c[2])]
    k = 0
    while k < len(cs) and not is_ci(cs[k][2]):
        k += 1
    if all(is_ci(c[2]) for c in cs[k:]):                    # no port commit after the first CI commit
        return (cs[k - 1][0] if k else None), cs[:k], cs[k:]
    if repo is None:
        raise SystemExit("a CI commit sits between port commits; pass the repository to replay them")
    parent = cs[k - 1][0] if k else git(repo, "rev-parse", f"{cs[0][0]}^")
    keep = parent
    idx = pathlib.Path(repo, git(repo, "rev-parse", "--git-dir"), "port-offer.index")   # .git may be a file
    env0 = dict(os.environ, GIT_INDEX_FILE=str(idx))
    def g(*a, env=None, inp=None):
        r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env or env0, input=inp)
        if r.returncode:
            sys.exit(f"git {' '.join(a)}: {r.stderr.strip()[:300]}")
        return r.stdout.strip()
    try:
        for sha, _subj, files in cs[k:]:
            if is_ci(files):
                continue
            g("read-tree", sha)
            for f in sorted(CI_FILES):                       # the CI files as they were before any CI commit
                blob = git(repo, "rev-parse", "--verify", "-q", f"{keep}:{f}", check=False)
                if blob:
                    g("update-index", "--add", "--cacheinfo", f"100644,{blob},{f}")
                else:
                    g("update-index", "--force-remove", f)
            tree = g("write-tree")
            meta = git(repo, "log", "-1", "--format=%an%x00%ae%x00%aI%x00%cn%x00%ce%x00%cI", sha).split("\0")
            msg = git(repo, "log", "-1", "--format=%B", sha)
            env = dict(os.environ, GIT_AUTHOR_NAME=meta[0], GIT_AUTHOR_EMAIL=meta[1], GIT_AUTHOR_DATE=meta[2],
                       GIT_COMMITTER_NAME=meta[3], GIT_COMMITTER_EMAIL=meta[4], GIT_COMMITTER_DATE=meta[5])
            parent = g("commit-tree", tree, "-p", parent, env=env, inp=msg + "\n")
    finally:
        idx.unlink(missing_ok=True)
    return parent, port, ci


def hunk_sizes(diff_u0):
    """(hunks, one-line, at most three lines) from a `git diff -U0`: the SHAPE of a change, which a line count
    hides -- a port is mostly one-line edits, and that is what makes it reviewable."""
    sizes = [max(int(m.group(1) or 1), int(m.group(2) or 1))
             for m in re.finditer(r"(?m)^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@", diff_u0)]
    return len(sizes), sum(s <= 1 for s in sizes), sum(s <= 3 for s in sizes)


def gh_slug(url):
    m = re.search(r"github\.com[/:]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", url or "")
    return m.group(1) if m else None


def parent_of(fork):
    import urllib.request
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{fork}", headers={"User-Agent": "port-offer"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return (json.loads(r.read().decode("utf-8")).get("parent") or {}).get("full_name")
    except Exception:          # offline / rate-limited: the links that need it are left out, and say so
        return None


def ci_run(fork, branch):
    """The branch's newest completed port-ci run, from the GitHub API: (conclusion, url, sha), or None."""
    import urllib.request
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{fork}/actions/workflows/port-ci.yml/runs"
                                     f"?branch={branch}&status=completed&per_page=1", headers={"User-Agent": "port-offer"})
        with urllib.request.urlopen(req, timeout=20) as r:
            w = json.loads(r.read().decode("utf-8")).get("workflow_runs") or []
        return (w[0]["conclusion"], w[0]["html_url"], w[0]["head_sha"]) if w else None
    except Exception:
        return None


def ci_verified(run):
    if run and run[0] == "success":
        return [f"CI on the branch ({run[1]}): the authors' own build, Gate A, Gate B and Gate C (a real client "
                "under Xvfb, launch + spawn) all pass, with nothing fixed by the run."]
    return []


def _api(url):
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "port-offer"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def toml_deps(text):
    """[(modId, required?, versionRange)] from a neoforge.mods.toml, minus the platform."""
    out = []
    for block in re.split(r"(?m)^\s*\[\[dependencies\.[^\]]+\]\]", text)[1:]:
        kv = dict(re.findall(r'(?m)^\s*(\w+)\s*=\s*"?([^"\n#]*)"?', block))
        mid = kv.get("modId", "").strip()
        if not mid or mid in ("minecraft", "neoforge", "forge"):
            continue
        req = kv.get("type", "").strip().lower() == "required" or kv.get("mandatory", "").strip().lower() == "true"
        out.append((mid, req, kv.get("versionRange", "").strip()))
    return out


def registry_link(d, mc):
    """(label, page url, file url or None) for the version a build TESTED -- not the newest on the registry."""
    v, prov, pid = str(d.get("version") or ""), d.get("provider"), d.get("id")
    if prov == "curseforge" and v.isdigit():
        return (f"CurseForge file {v}", f"https://www.curseforge.com/projects/{pid}",
                f"https://www.curseforge.com/api/v1/mods/{pid}/files/{v}/download")
    if prov == "modrinth" and pid:
        rows = _api(f'https://api.modrinth.com/v2/project/{pid}/version?loaders=%5B%22neoforge%22%5D'
                    f'&game_versions=%5B%22{mc}%22%5D') or []
        hit = next((r for r in rows if r.get("version_number") == v), None) or \
            next((r for r in rows if v and v in (r.get("version_number") or "")), None)
        if hit:
            f = (hit.get("files") or [{}])[0]
            return (f.get("filename") or hit.get("version_number"),
                    f"https://modrinth.com/mod/{pid}/version/{hit['id']}", f.get("url"))
        return (f"version {v}", f"https://modrinth.com/mod/{pid}", None)
    return (f"version {v}" if v else "see the project", None, None)


def sibling_releases(repo, branch):
    """Ports of OTHER forks this one's CI installs from their releases (tools/port-ci.py --dep-jar): their exact
    release asset is what CI tested, so it is what to install."""
    wf = git(repo, "show", f"{branch}:.github/workflows/port-ci.yml", check=False)
    return [(m.group(1), m.group(2)) for m in re.finditer(
        r'curl -fsSL -o "[^"]*/([\w.+-]+\.jar)" "(https://github\.com/[^"]+/releases/download/[^"]+)"', wf)]


def is_port_tag(tag, modid, mc):
    """<modid>-<version>-mc<mc>, or a re-release of it (-r2, -r3: the port changed, the author's version did not)."""
    return bool(re.fullmatch(re.escape(modid) + r"-.+-mc" + re.escape(mc) + r"(?:-r\d+)?", tag))


def own_release(fork, modid, mc):
    rels = _api(f"https://api.github.com/repos/{fork}/releases?per_page=30") or []
    for r in rels:
        if is_port_tag(r.get("tag_name", ""), modid, mc):     # newest first: a -r2 re-release wins
            return r["html_url"], [(a["name"], a["browser_download_url"]) for a in r.get("assets", [])]
    return None, []


def install_section(repo, tip, branch, fork, modid, mc, neo, variant=None, deps=None):
    """How to install THIS port on a real game, from what was tested: the NeoForge version the build names, this
    fork's release, and each dependency from the ported build (required/optional from its neoforge.mods.toml),
    at the version CI ran -- with the exact file, not the newest one. Never guessed: what cannot be resolved says so."""
    L = [f"Tested with Minecraft {mc} and NeoForge {neo}. Install that NeoForge "
         f"([installer](https://maven.neoforged.net/releases/net/neoforged/neoforge/{neo}/neoforge-{neo}-installer.jar)), "
         "then put these in the instance's `mods` folder. This numbered list is the whole minimal setup: CI's "
         "minimal pass runs exactly these and nothing else.", ""]
    mods_toml = git(repo, "show", f"{tip}:src/main/resources/META-INF/neoforge.mods.toml", check=False)
    own = re.search(r'(?s)\[\[mods\]\].*?modId\s*=\s*"([^"]+)"', mods_toml)   # release tags use the mod id
    ownid = own.group(1) if own else modid
    if "${" in ownid:
        ownid = modid
    man = {"schema": 1, "port": fork, "modid": ownid, "minecraft": mc, "neoforge": neo, "files": []}
    def add(role, mid, name, url, page=None, port=None):
        man["files"].append({k: v for k, v in (("role", role), ("modid", mid), ("name", name), ("url", url),
                                                ("page", page), ("port", port)) if v})
    rel, assets = own_release(fork, ownid, mc)
    if assets:
        pick = [a for a in assets if variant and variant[0] in a[0]] or assets
        add("self", ownid, pick[0][0], pick[0][1]) if (len(assets) == 1 or len(pick) == 1) else None
        if len(assets) == 1 or (variant and len(pick) == 1):
            L.append(f"1. **This mod:** [{pick[0][0]}]({pick[0][1]})"
                     + (f" -- {variant[1]}" if variant and len(assets) > 1 else ""))
        else:
            L.append(f"1. **This mod:** one of the jars on [the release]({rel}): "
                     + ", ".join(f"`{n}`" for n, _ in assets) + " (the build makes one per platform)")
    else:
        L.append("1. **This mod:** no release yet -- run the fork's CI with release=true first")
    toml = toml_deps(git(repo, "show", f"{tip}:src/main/resources/META-INF/neoforge.mods.toml", check=False))
    def norm(x):
        return re.sub(r"[^a-z0-9]", "", (x or "").lower())
    def build_dep(mid):          # the mods.toml id against the build's coordinates and registry ids
        for d in deps or []:
            if d.get("kind") == "mod" and d.get("toml_modid") == mid:
                return d
        return next((d for d in deps or [] if d.get("kind") == "mod" and (
            norm(mid) in norm(d.get("artifact")) or norm(mid) == norm(str(d.get("id"))))), None)
    sibs = sibling_releases(repo, branch)
    req, opt, n = [], [], 2
    for mid, required, rng in toml:
        d = build_dep(mid)
        sib = next(((a, u) for a, u in sibs if mid.replace("_", "") in a.replace("-", "").replace("_", "")), None)
        role = "required" if required else "optional"
        if sib:
            line = f"**{mid}** -- [{sib[1].rsplit('/', 1)[-1]}]({sib[1]}) (another of these ports; the exact file CI tested)"
            sp = re.match(r"https://github\.com/([^/]+/[^/]+)/releases/", sib[1])
            add(role, mid, sib[1].rsplit("/", 1)[-1], sib[1], port=sp.group(1) if sp else None)
        elif d:
            label, page, file = registry_link(d, mc)
            line = f"**{mid}** -- " + (f"[{label}]({file})" if file else label) + (f" ([project]({page}))" if page else "")
            add(role, mid, label, file, page)
        else:
            add(role, mid, None, None, f"https://modrinth.com/mods?q={mid}&g=categories:neoforge&v={mc}")
            line = (f"**{mid}** {rng} -- not in the build, so no tested version: [find a NeoForge {mc} build]"
                    f"(https://modrinth.com/mods?q={mid}&g=categories:neoforge&v={mc})")
        (req if required else opt).append(line)
    for line in req:
        L.append(f"{n}. {line}"); n += 1
    if opt:
        L += ["", "Optional (only for the integration with that mod):"] + [f"- {x}" for x in opt]
    # mods the build puts on the game's runtime that mods.toml does not name: usually a dependency OF one of the
    # above (an optional integration's own library), and CI ran with them -- so a player who adds that
    # integration needs them too
    named = {(d.get("group"), d.get("artifact")) for d in (build_dep(mid) for mid, _r, _g in toml) if d}
    extra, seen = [], set()
    for d in deps or []:
        key = (d.get("group"), d.get("artifact"))
        if d.get("kind") != "mod" or key in named or d.get("scope") in ("compileOnly",):
            continue
        if key in seen:
            continue
        seen.add(key)
        label, page, file = registry_link(d, mc)
        extra.append(f"- {d.get('artifact')} -- " + (f"[{label}]({file})" if file else label)
                     + (f" ([project]({page}))" if page else ""))
        add("extra", d.get("artifact"), label, file, page)
    if extra:
        L += ["", "Added in CI's full pass, though this mod does not declare them (the full setup that was also "
                  "tested; often an integration, or a library an optional mod above needs):"] + extra
    return L, man


def hash_files(man):
    """sha256 of every file the manifest links, so an installer verifies what it downloads is what CI tested."""
    import hashlib, urllib.request
    for f in man["files"]:
        if f.get("url") and not f.get("sha256"):
            try:
                req = urllib.request.Request(f["url"], headers={"User-Agent": "port-offer"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = r.read()
                f["sha256"] = hashlib.sha256(data).hexdigest()
                if not f.get("name") or f["name"].startswith(("CurseForge file", "version ")):
                    disp = r.headers.get("Content-Disposition") or ""
                    m = re.search(r'filename="?([^";]+)', disp)
                    f["name"] = m.group(1) if m else urllib.request.unquote(r.geturl().rsplit("/", 1)[-1])
            except Exception as e:
                f["error"] = f"{type(e).__name__}: {e}"[:200]
    return man


def verified(state):
    """What the pipeline CHECKED, from its state file -- one line each, nothing inferred."""
    out = []
    lic = (state.get("license") or {}).get("result") or {}
    if isinstance(lic, dict) and lic.get("licence"):
        out.append(f"Licence: {lic['licence']} ({', '.join(lic.get('file') or [])}); the port does not touch it.")
    g = (state.get("gates") or {})
    if g.get("status") == "done":
        r = g.get("result") or {}
        out.append(f"Gate A: mixin-config integrity, {r.get('gateA_tests', '?')} test(s) passed.")
        out.append("Gate B: a headless server loads the mod and its GameTests pass.")
    ab = state.get("author-build") or {}
    if ab.get("status") == "done":
        jars = (ab.get("result") or {}).get("jars") or []
        out.append(f"The authors' own `build` succeeds unchanged ({', '.join(jars) or 'jar built'}).")
    if (state.get("provenance") or {}).get("status") == "done":
        out.append("Provenance: no licence file touched, no copyright line dropped or added.")
    return out


def display_name(props, upstream, fork, modid):
    """What the authors call the mod: their mod_name, else their repository's name. Never the --modid, which
    is an id for lookups (a fleet run always passes one), and never a local folder name."""
    return props.get("mod_name") or (upstream or fork or "").split("/")[-1] or modid


def render(ctx):
    L = [f"# Offering the {ctx['name']} port to its authors", "",
         "Nothing here has been sent. Edit, then send it yourself (or don't).", "",
         "## Links", "",
         f"- **The diff** (author's code -> the port; send this): {ctx['compare']}",
         f"- The port branch, as the authors would clone it: {ctx['branch_url']}"]
    if ctx.get("pr_url"):
        L.append(f"- Propose it as a PR on their repository (opens a pre-filled form, nothing more): {ctx['pr_url']}")
    else:
        L.append("- (The authors' repository is unknown -- pass --upstream owner/repo for the PR link.)")
    if ctx.get("ci_url"):
        L += [f"- Our CI for the branch (gates on every push; not part of the offer): {ctx['ci_url']}",
              f"- Built JARs: {ctx['releases_url']}"]
    if ctx.get("install"):
        L += ["", "## How to install", ""] + ctx["install"]
    h, h1, h3 = ctx["hunks"]
    L += ["", "## Size of the change", "",
          f"{ctx['files']} of the {ctx['tracked']} files in the authors' tree changed, +{ctx['ins']} / -{ctx['dels']} "
          f"lines." + (f" That is {h} separate edits, of which {h1} ({100 * h1 // h}%) change a single line and "
                      f"{h3} ({100 * h3 // h}%) at most three." if h else ""), "",
          f"{len(ctx['commits'])} commit(s):", ""]
    L += [f"- `{s[:10]}` {subj}" for s, subj, _ in ctx["commits"]]
    if ctx["ci_commits"]:
        L += ["", f"Left out of the offer: {len(ctx['ci_commits'])} CI commit(s) of ours ("
              + ", ".join(f"`{s[:10]}`" for s, _, _ in ctx["ci_commits"]) + ")."]
    L += ["", "## Verified", ""] + [f"- {v}" for v in ctx["verified"] or ["(no pipeline state found -- say only what you checked)"]]
    L += ["- Not verified by any automated check: gameplay by a person."]
    facts = " ".join(ctx["verified"])
    proof = []
    if "authors' own" in facts:
        proof.append("builds with your own Gradle build")
    if "Gate B" in facts:
        proof.append("loads on a headless server with GameTests passing")
    if "Gate C" in facts:
        proof.append("starts a real client")
    msg = (f"Hi -- I ported {ctx['name']} to {ctx['target']} in a fork, keeping your layout and the smallest diff "
           f"I could ({ctx['files']} files, +{ctx['ins']}/-{ctx['dels']}). The whole change is here: {ctx['compare']}\n\n"
           + (f"It {', '.join(proof[:-1]) + ' and ' + proof[-1] if len(proof) > 1 else proof[0]}. " if proof else "")
           + "If it's useful, I'm happy to open a PR or adjust anything to how you'd like it done.")
    L += ["", "## Draft message to the authors", "", msg, "",
          "## Draft PR description (only if they ask for a PR)", "",
          f"Ports {ctx['name']} to {ctx['target']}, keeping the existing layout and the smallest diff that works.", "",
          "Checked:"] + [f"- {v}" for v in ctx["verified"]] + [
          "", "Not checked: gameplay by a person. The port's CI workflow is deliberately not included."]
    return "\n".join(L) + "\n"


def self_check():
    import tempfile
    ok = True
    ok &= display_name({"mod_name": "Some Mod"}, "a/repo", "me/repo", "sm") == "Some Mod"
    ok &= display_name({}, "a/SomeRepo", "me/somerepo", "sm") == "SomeRepo"          # --modid never wins
    ok &= is_port_tag("m-2.2.1-mc1.21.1-r2", "m", "1.21.1") and is_port_tag("m-2.2.1-mc1.21.1", "m", "1.21.1")
    ok &= not is_port_tag("m-2.2.1-mc1.21.10", "m", "1.21.1") and not is_port_tag("mx-1-mc1.21.1", "m", "1.21.1")
    with tempfile.TemporaryDirectory() as d:
        r = pathlib.Path(d)
        for a in (["init", "-q", "-b", "main"], ["config", "user.email", "t@e"], ["config", "user.name", "t"]):
            git(r, *a)
        (r / "A.java").write_text("a\n", encoding="utf-8"); (r / "B.java").write_text("b\n", encoding="utf-8")
        git(r, "add", "-A"); git(r, "commit", "-qm", "author")
        git(r, "checkout", "-qb", "neoforge-1.21.1")
        (r / "A.java").write_text("a2\n", encoding="utf-8"); git(r, "commit", "-qam", "Port")
        (r / ".github/workflows").mkdir(parents=True)
        (r / ".github/workflows/port-ci.yml").write_text("x\n", encoding="utf-8")
        git(r, "add", "-A"); git(r, "commit", "-qm", "CI")
        cs = commits(r, "main", "neoforge-1.21.1")
        tip, port, ci = offer_tip(cs)
        ok &= len(port) == 1 and len(ci) == 1 and tip == cs[0][0]
        ok &= "port-ci.yml" not in git(r, "diff", "--name-only", f"main..{tip}")
        # a port fix after the CI commit: replayed without the CI files, the same commit every time
        (r / "B.java").write_text("b2\n", encoding="utf-8"); git(r, "commit", "-qam", "late fix")
        cs2 = commits(r, "main", "neoforge-1.21.1")
        t2, p2, c2 = offer_tip(cs2, r)
        ok &= [x[1] for x in p2] == ["Port", "late fix"] and [x[1] for x in c2] == ["CI"]
        ok &= git(r, "diff", "--name-only", f"main..{t2}").split() == ["A.java", "B.java"]
        ok &= git(r, "log", "-1", "--format=%s%x00%an", t2) == "late fix\x00t" and offer_tip(cs2, r)[0] == t2
        ok &= git(r, "rev-parse", "neoforge-1.21.1") == cs2[-1][0]            # the port branch is untouched
    ok &= hunk_sizes("@@ -1 +1 @@\n@@ -3,2 +3,3 @@\n@@ -9,0 +10,8 @@\n") == (3, 1, 2)
    ok &= gh_slug("https://github.com/o/r.git") == "o/r" and gh_slug("git@github.com:o/r") == "o/r"
    st = {"license": {"result": {"licence": "MIT", "file": ["LICENSE"]}}, "gates": {"status": "done",
          "result": {"gateA_tests": 2}}, "author-build": {"status": "done", "result": {"jars": ["m-1.jar"]}}}
    v = verified(st)
    ok &= len(v) == 4 and "MIT" in v[0] and "m-1.jar" in v[3]
    ok &= verified({"gates": {"status": "failed"}}) == []
    ok &= toml_deps('[[dependencies.m]]\nmodId="minecraft"\n[[dependencies.m]]\nmodId="lib"\ntype="required"\nversionRange="[1,)"\n'
                    '[[dependencies.m]]\nmodId="opt"\ntype="optional"\n') == [("lib", True, "[1,)"), ("opt", False, "")]
    ok &= registry_link({"version": "123", "provider": "curseforge", "id": "9"}, "1.21.1")[2] \
        == "https://www.curseforge.com/api/v1/mods/9/files/123/download"
    ok &= ci_verified(("failure", "u", "x")) == [] and "Gate C" in ci_verified(("success", "u", "x"))[0]          # a failed gate is never reported as passed
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    if "--self-check" in sys.argv:
        return self_check()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", required=True); ap.add_argument("--branch")
    ap.add_argument("--base", help="the authors' ref (default: origin/HEAD)")
    ap.add_argument("--upstream", help="the authors' repository, owner/repo (default: the fork's GitHub parent)")
    ap.add_argument("--modid"); ap.add_argument("--work-dir"); ap.add_argument("--push", action="store_true")
    ap.add_argument("--trailer", action="append", default=[])
    ap.add_argument("--variant", metavar="SUBSTRING=WHY",
                    help="when the release has one jar per platform, the one to install and why (e.g. _mr=...)")
    a = ap.parse_args()
    repo = pathlib.Path(a.repo).resolve()
    branch = a.branch or git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    base = a.base or git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False) or "origin/main"
    fork = gh_slug(git(repo, "remote", "get-url", "origin"))
    if not fork:
        sys.exit("origin is not a GitHub repository")
    cs = commits(repo, base, branch)
    if not cs:
        sys.exit(f"{branch} has no commits over {base}")
    tip, port, ci = offer_tip(cs, repo)
    offer = branch if not ci else f"{branch}-upstream"
    if ci:
        cur = git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{offer}", check=False)
        if cur and cur != tip:
            git(repo, "branch", "-f", offer, tip)            # ours, regenerated from the port branch every run
        elif not cur:
            git(repo, "branch", offer, tip)
    base_sha = git(repo, "rev-parse", base)
    ins = dels = files = 0
    for l in git(repo, "diff", "--numstat", f"{base_sha}..{tip}").splitlines():
        p = l.split("\t")
        files += 1
        ins += int(p[0]) if p[0].isdigit() else 0
        dels += int(p[1]) if p[1].isdigit() else 0
    tracked = len(git(repo, "ls-tree", "-r", "--name-only", base_sha).splitlines())
    work = pathlib.Path(a.work_dir) if a.work_dir else pathlib.Path(
        os.path.expanduser(f"~/.mc-mod-upgrade/upstream/{repo.name}-{branch}"))
    state = {}
    if (work / "state.json").is_file():
        state = json.loads((work / "state.json").read_text(encoding="utf-8"))
    props = dict(re.findall(r"(?m)^\s*([\w.]+)\s*=\s*(.*?)\s*$",
                            git(repo, "show", f"{tip}:gradle.properties", check=False)))
    target = f"NeoForge {props['neo_version']} (Minecraft {props['minecraft_version']})" \
        if props.get("neo_version") and props.get("minecraft_version") else branch
    upstream = a.upstream or parent_of(fork)
    # the name the AUTHORS know it by: their mod_name, else their repository's name -- never a local folder name
    modid = a.modid or props.get("mod_id") or (upstream or fork).split("/")[1]
    name = display_name(props, upstream, fork, modid)
    up_default = "main"
    if upstream:
        up_default = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False).split("/", 1)[-1] or "main"
    ctx = {"modid": modid, "name": name, "target": target, "files": files, "ins": ins, "dels": dels, "tracked": tracked,
           "hunks": hunk_sizes(git(repo, "diff", "-U0", f"{base_sha}..{tip}")), "commits": port, "ci_commits": ci,
           "verified": verified(state) + (ci_verified(ci_run(fork, branch)) if ci else []),
           "compare": f"https://github.com/{fork}/compare/{base_sha[:12]}...{offer}",
           "branch_url": f"https://github.com/{fork}/tree/{offer}",
           "pr_url": (f"https://github.com/{upstream}/compare/{up_default}...{fork.replace('/', ':')}:{offer}?expand=1"
                      if upstream else None),
           "ci_url": (f"https://github.com/{fork}/actions/workflows/port-ci.yml?query=branch%3A{branch}" if ci else None),
           "releases_url": f"https://github.com/{fork}/releases"}
    try:
        sp = __import__("importlib.util").util.spec_from_file_location("port_deps", pathlib.Path(__file__).parent / "port-deps.py")
        pd = __import__("importlib.util").util.module_from_spec(sp); sys.modules["port_deps"] = pd; sp.loader.exec_module(pd)
        _c, dep_rep = pd.run(repo, props.get("minecraft_version", "1.21.1"), out=lambda *x: None)
        deps = dep_rep.get("dependencies") if isinstance(dep_rep, dict) else []
    except SystemExit:
        deps = []
    ctx["install"], man = install_section(repo, tip, branch, fork, props.get("mod_id") or modid,
                                     props.get("minecraft_version", ""), props.get("neo_version", ""),
                                     tuple(a.variant.split("=", 1)) if a.variant else None, deps)
    work.mkdir(parents=True, exist_ok=True)
    (work / "OFFER.md").write_text(render(ctx), encoding="utf-8")
    man = hash_files(man)
    mtext = json.dumps(man, indent=2) + "\n"
    (work / "port-install.json").write_text(mtext, encoding="utf-8")
    if ci and a.push:     # beside our CI file, never in the authors' offer; [skip ci]: data, not code
        cur = git(repo, "show", f"{branch}:{MANIFEST}", check=False)
        if cur.strip() != mtext.strip():
            if git(repo, "rev-parse", "--abbrev-ref", "HEAD") != branch:
                sys.exit(f"check out {branch} to commit {MANIFEST}")
            (repo / MANIFEST).parent.mkdir(parents=True, exist_ok=True)
            (repo / MANIFEST).write_text(mtext, encoding="utf-8")
            git(repo, "add", MANIFEST)
            git(repo, "commit", "-q", "-m", "Install manifest for this port [skip ci]\n\nThe exact files CI tested, "
                "with sha256, for tools/install-port.py in the migrator. Data only; not part of the port.\n"
                + "".join(f"\n{t}" for t in (a.trailer or [])))
            r = subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", branch], capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            if r.returncode:
                sys.exit(f"push of {MANIFEST} failed: {r.stderr.strip()[-300:]}")
    pushed = ""
    if ci and a.push:
        r = subprocess.run(["git", "-C", str(repo), "push", "-f", "origin", f"{offer}:refs/heads/{offer}"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode:
            sys.exit(f"push failed: {r.stderr.strip()[-300:]}")
        pushed = " (pushed)"
    print(f"port-offer: {offer}{pushed} -- {files}/{tracked} files, +{ins}/-{dels}, {len(port)} port commit(s), "
          f"{len(ci)} CI commit(s) left out")
    print(f"  diff: {ctx['compare']}")
    if ctx["pr_url"]:
        print(f"  propose a PR: {ctx['pr_url']}")
    if ci and not a.push:
        print(f"  not pushed -- the links resolve after: git -C {repo} push -f origin {offer}")
    print(f"  write-up: {work / 'OFFER.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
