#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Give a fork-layer target its own build of every mod the fork depends on (issue #77).

    python3 tools/layer-deps.py resolve --repo DIR --target 1.21.4 [--port-checkout owner/repo=DIR ...]
                                [--port-release owner/repo@TAG ...] [--allow-undone-library] [--dry-run]
    python3 tools/layer-deps.py library-done --target 1.21.4 (--port-checkout owner/repo=DIR | --port-release owner/repo@TAG)
    python3 tools/layer-deps.py fetch --repo DIR --target 1.21.4
    python3 tools/layer-deps.py --self-check

A layer target compiles the authors' code against another Minecraft version, but the authors' build.gradle pins its
mods for the BASE version (`curse.maven:somelib-123:456`, `maven.modrinth:somelib:abc`, a sibling port's
`group:artifact:version` from mavenLocal). This finds each one's build for the target and writes the swaps into the
target's target.properties, between `# BEGIN layer-deps` and `# END layer-deps` (layer.gradle reads them):

  substitute.<group:module>=<group:module:version>   the dependency's build for this Minecraft
  exclude.<group:module>=<reason>                    no build for this Minecraft and not required: off the classpath

and a table of what it did into targets/<t>/DEPS.md. Without -Pmc the layer returns before reading either, so the
base build never sees them.

WHERE A BUILD IS LOOKED UP
  curse.maven   the CurseForge project's files for (neoforge, target) when CURSEFORGE_API_KEY is set; else the
                pinned base jar is fetched from cursemaven, its sha1 is looked up on Modrinth (the same file is often
                uploaded to both) and that Modrinth project's builds are used. The DEPS.md row names which.
  maven.modrinth  that Modrinth project's builds.
  a sibling port  the coordinate the fork's port-ci.yml puts into mavenLocal from a sibling port's release
                (`--dep-jar`). Its layer writes one jar per target (`<asset>-mc<t>.jar`); with
                --port-checkout owner/repo=DIR it is taken from DIR/build/mc<t>/libs (built with that checkout's
                .multiversion/build.sh <t> assemble when missing) and installed into ~/.m2 as version <v>-mc<t>.
  anything else (a `${...}` version, a maven the authors run) is left alone: the target's prop.* lines carry it.
  Newest release first, then beta, then alpha; the channel is recorded.

WHICH ONES MAY BE DROPPED
  The fork's .github/port-install.json says which mods a player must install (role "required") -- the tested set
  the port ships with. A required mod, or the sibling port, with no build for the target stops this with exit 15:
  the target cannot ship (the fork-versions skill: skip it, and let the next target jump over it with --direct).
  Every other dependency (role "extra", or a compileOnly/runtimeOnly one the manifest does not name: a dev-time mod)
  with no build is excluded, and the drop is recorded. An implementation/api/jarJar dependency the manifest does not name
  is taken as required (fail closed). A Modrinth pin may name the project's slug while the manifest's cdn URL carries its
  id: the slug is looked up (/v2/project/<slug>) to match. A fork without port-install.json treats every dependency
  as required.

  A sibling's `port_jar.` pin (written by `--port-release`) is kept by a later resolve that names that sibling with
  neither --port-checkout nor --port-release, as long as it pins the version port-ci.yml installs, and its jar is
  installed into ~/.m2 by the same sha256-checked download `fetch` does (skipped when ~/.m2 already holds it; a failed
  download or a mismatch keeps the pin and says NOT installed in its row). `--dry-run` writes
  nothing, and neither builds a sibling checkout nor fetches or installs a sibling jar: its row says what it would do.
  `fetch` (each layer CI job) fails on a sibling substitute with no pin -- one resolved from a local --port-checkout,
  which CI cannot build -- and prints the --port-release re-resolve to run.

LIBRARY FIRST
  A dependent's target T builds only against a sibling library's T that is DONE by tools/fork-port.py's one done list.
  First the jar: its GATES.md (ci-gates.py --mc, kept by fork-port.py only after a green full run) is for T, names a
  commit (not `+ uncommitted`), has a PASS row naming Gate C and no FAIL row, and no file that can change the jar
  changed between that commit and the one used (the checkout's HEAD plus its edited tracked files and every untracked
  file under .multiversion/; for --port-release the tag, by GitHub's compare API) -- other targets' directories and
  documents excepted, as port-publish.py's listed_green() judges a released middle target. Then the list itself: for
  a --port-checkout, fork-port.py's target_done(checkout, T) answers (no unreviewed STUBS.json entry, a complete
  REVIEW.md for its commit, TEST-ITEMS.md, the behaviour inventory accounted for, no DEPS.md "Development only" mark,
  an --allow-undone-parent parent done). For a --port-release the same list is read at the tag: REVIEW.md complete
  with an `ok` row and a note for every STUBS.json entry, every whole-file overlay, resource group and renames.tsv in
  the tag's tree (GitHub's tree API), its commit a hex id that is the tag or an ancestor of it with nothing under src/, the chain's targets/ or
  shared/ changed since; TEST-ITEMS.md; INVENTORY-CHECK.json with nothing unaccounted when the layer has
  INVENTORY.json; no "Development only" mark; an allowed undone parent done at the tag. A library target with no
  REVIEW.md is not done. A sibling named by --port-checkout or --port-release that is not done stops the resolve
  with exit 18 (dry run too; nothing is built, fetched or written but DEPS.md's reason). --allow-undone-library lets a
  --port-checkout through for development only -- DEPS.md records it under "Development only" -- never a release.
  `library-done` asks the same question alone. A pin kept from an earlier --port-release is not re-checked.

VERSION BRANCHES (tools/fork-branches.py). On a fork of stacked version branches the same commands run on the checkout
of branch neoforge-<t>: the block above goes into .port/<t>/branch.properties (.port/branch.gradle applies it) and
DEPS.md into .port/<t>/. A sibling library that is a version-branch fork releases each version from its own branch
(Port CI's tag <mod>-<ver>-mc<t>); --port-release takes the jar from that release, pinned by sha256, and the library's
done list is tools/fork-branches.py's, run on the tag's tree with the library's history (a bare clone under
~/.mmvu/cache/git/), so the release and a checkout answer alike; the library rule's own reason (no dependent recorded
yet) never blocks a dependent. A dependent never builds a sibling's checkout: --port-checkout copies the jar the checkout
built and pins it as `port_jar_dev.` -- development only, refused by the dependent's done check.

Exit codes: 0 resolved (written unless --dry-run); 15 a required dependency has no build for the target; 18 a sibling
library's target is not done (library-done: 0 done, 18 not); 2 bad input.
Standard library only.
"""
import argparse, hashlib, json, os, pathlib, re, shutil, subprocess, sys, tempfile, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools/mod-registry"))
sys.path.insert(0, str(ROOT / "tools"))
from gitbash import BASH   # noqa: E402  (Git Bash on Windows, where plain "bash" is WSL's launcher)
BEGIN, END = "# BEGIN layer-deps (tools/layer-deps.py)", "# END layer-deps"
MODRINTH = "https://api.modrinth.com/v2"
CURSEMAVEN = "https://cursemaven.com/curse/maven"
CHANNEL = {"release": 0, "beta": 1, "alpha": 2}
EXIT_REQUIRED = 15
EXIT_UNDONE = 18

DEP_LINE = re.compile(r"""^\s*(implementation|api|compileOnly|compileOnlyApi|runtimeOnly|localRuntime|localImplementation|"""
                      r"""modImplementation|modCompileOnly|modRuntimeOnly|jarJar)\s*\(?\s*["']([^"'\s]+)["']""")


# ---------------------------------------------------------------- pure helpers (covered by --self-check)

def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(re.sub(r"(^|\s)//.*$", "", ln) for ln in text.splitlines())


def gradle_deps(text):
    """[{conf, coord, group, module, version, kind}] for every quoted, fully pinned coordinate the build declares.
    kind: curse (curse.maven:<slug>-<projectId>:<fileId>), modrinth (maven.modrinth:<project>:<version>), maven.
    A coordinate with a `$` (a version from gradle.properties) is skipped: the target's prop.* lines own it."""
    out, seen = [], set()
    for ln in strip_comments(text).splitlines():
        m = DEP_LINE.match(ln)
        if not m or "$" in m.group(2):
            continue
        parts = m.group(2).split(":")
        if len(parts) < 3 or (parts[0], parts[1]) in seen:
            continue
        seen.add((parts[0], parts[1]))
        g, mod, v = parts[0], parts[1], parts[2]
        kind = "curse" if g == "curse.maven" else "modrinth" if g == "maven.modrinth" else "maven"
        d = {"conf": m.group(1), "coord": ":".join(parts[:3]), "group": g, "module": mod, "version": v, "kind": kind}
        if kind == "curse":
            cm = re.fullmatch(r"(.+)-(\d+)", mod)
            if not cm:
                continue
            d["cf_project"], d["cf_file"] = cm.group(2), v
        out.append(d)
    return out


def manifest_roles(manifest):
    """{'cf:<projectId>' | 'mr:<projectId>' | 'port:<owner/repo>': role} from port-install.json's files."""
    roles = {}
    for f in (manifest or {}).get("files", []):
        role, url = f.get("role"), f.get("url", "")
        if role == "self":
            continue
        if f.get("port"):
            roles[f"port:{f['port']}"] = role
        m = re.search(r"curseforge\.com/api/v1/mods/(\d+)/files/", url) or re.search(r"edge\.forgecdn\.net/files/", url)
        if m and m.groups():
            roles[f"cf:{m.group(1)}"] = role
        m = re.search(r"cdn\.modrinth\.com/data/([\w]+)/versions/", url)
        if m:
            roles[f"mr:{m.group(1)}"] = role
    return roles


DEP_JAR = re.compile(r'- name: ([\w.-]+) ([\w.+-]+) from the [^\n]* release into mavenLocal \(pinned by sha256\)\n'
                     r'        run: \|\n          mkdir -p "\$HOME/\.m2/repository/([^"]+)"\n'
                     r'          curl -fsSL -o "[^"]+" "https://github\.com/([^/"]+/[^/"]+)/releases/download/([^/"]+)/([^/"]+)"')


def port_deps(workflow):
    """{'group:artifact': {repo, tag, asset, version}} for every sibling port's released jar the fork's port-ci.yml
    installs into mavenLocal (tools/port-ci.py --dep-jar writes these steps)."""
    out = {}
    for m in DEP_JAR.finditer(workflow or ""):
        art, ver, path, repo, tag, asset = m.groups()
        g = path[:-len(f"/{art}/{ver}")].replace("/", ".")
        out[f"{g}:{art}"] = {"repo": repo, "tag": tag, "asset": asset, "version": ver}
    return out


def pick(files, target, loader="neoforge"):
    """The build to use for `target`: files whose game versions include it exactly and whose loaders include the
    loader; newest release, else newest beta, else newest alpha. None when there is none.
    files: [{id, mcs, loaders, channel, date}]"""
    ok = [f for f in files if target in f.get("mcs", []) and loader in [x.lower() for x in f.get("loaders", [])]]
    if not ok:
        return None
    return sorted(ok, key=lambda f: (CHANNEL.get(f.get("channel"), 3), _neg(f.get("date") or "")))[0]


def _neg(s):
    return "".join(chr(0x10FFFF - ord(c)) for c in s)


# ---------------------------------------------------------------- a version branch (tools/fork-branches.py)
#
# On a fork of stacked version branches, target T is the branch neoforge-T: its build keys (this block) live in
# .port/T/branch.properties and DEPS.md in .port/T/, the checkout being on that branch. A sibling library is a version
# branch fork too: its release for T is Port CI's per-version release on its neoforge-T branch (tag <mod>-<ver>-mcT,
# the jar named as its build names it); its done check is tools/fork-branches.py's, read at the tag. ISOLATION: a
# dependent never builds a sibling's checkout -- --port-checkout copies the jar the checkout already built and pins
# its sha256 as development only (port_jar_dev.), which the dependent's done check refuses.

def _fb():
    import importlib.util
    spec = importlib.util.spec_from_file_location("fork_branches_for_layer_deps", ROOT / "tools/fork-branches.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def branch_target(repo, target):
    """True when `repo` is checked out on version branch `target` (not a layer)."""
    repo = pathlib.Path(repo)
    if (repo / ".multiversion/base.properties").is_file() or not (repo / ".port" / target / "branch.properties").is_file():
        return False
    gp = repo / "gradle.properties"        # read here, not through fork-branches.py: a fork's CI kit carries this file only
    m = re.search(r"(?m)^\s*minecraft_version\s*=\s*(\S+)", gp.read_text(encoding="utf-8")) if gp.is_file() else None
    return bool(m) and m.group(1) == target


def target_dir(repo, target):
    repo = pathlib.Path(repo)
    return repo / ".port" / target if branch_target(repo, target) else repo / ".multiversion/targets" / target


def props_file(repo, target):
    d = target_dir(repo, target)
    return d / ("branch.properties" if d.parent.name == ".port" else "target.properties")


def key(coord):
    """`group:module` escaped as a java.util.Properties key (':' ends an unescaped key)."""
    return coord.replace(":", "\\:")


def render_block(rows):
    """The target.properties block: substitute./exclude. lines, one per resolved or dropped dependency."""
    lines = [BEGIN, "# per-target builds of the authors' pinned dependencies; DEPS.md says where each came from"]
    for r in rows:
        ga = f"{r['group']}:{r['module']}"
        if r.get("to"):
            lines.append(f"substitute.{key(ga)}={r['to']}")
        elif r.get("drop"):
            lines.append(f"exclude.{key(ga)}={r['drop']}")
        if r.get("pin"):            # a sibling port's released per-target jar: CI fetches it by this (`fetch`)
            lines.append(f"port_jar.{key(ga)}={r['pin']}")
        if r.get("pin_dev"):        # a jar copied from a sibling's checkout (version branches): development only
            lines.append(f"port_jar_dev.{key(ga)}={r['pin_dev']}")
    lines.append(END)
    return "\n".join(lines) + "\n"


def splice(props, block):
    """target.properties with its layer-deps block replaced (or appended)."""
    if BEGIN in props and END in props:
        a, b = props.index(BEGIN), props.index(END) + len(END)
        rest = props[b:].lstrip("\n")
        return props[:a] + block + rest
    return props.rstrip("\n") + "\n" + block


def render_md(target, rows, stop):
    out = [f"# Dependencies for Minecraft {target}", "",
           "Generated by `tools/layer-deps.py resolve`; re-run it rather than editing this file.", ""]
    if stop:
        out += [f"**Not buildable:** {stop}", ""]
    out += ["| dependency | role | base pin | for this target | from |", "|---|---|---|---|---|"]
    for r in rows:
        got = r.get("to") or ("dropped: " + r["drop"] if r.get("drop") else
                              "REFUSED: " + r["refused"] if r.get("refused") else "MISSING: " + r.get("missing", "?"))
        out.append(f"| `{r['group']}:{r['module']}` | {r['role']} | `{r['version']}` | {got} | "
                   f"{str(r.get('source', '')).replace('|', '/')} |")
    undone = [r for r in rows if r.get("undone")]
    if undone:
        out.append("")
    for r in undone:
        out.append(f"**Development only (`--allow-undone-library`):** `{r['group']}:{r['module']}` was built from a "
                   f"checkout whose {target} target is not done ({r['undone']}). Not for release: once the library's "
                   f"{target} is done and released, re-resolve with `--port-release`.")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- network lookups

def _get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "mc-mod-version-upgrade/layer-deps", **(headers or {})})
    try:
        import providers      # the migrator's CA handling; a fork's CI kit runs `fetch` without it
        ctx = providers.ssl_context()
    except ImportError:
        ctx = None
    with urllib.request.urlopen(req, timeout=120, context=ctx) as r:
        return r.read()


def install_pin(ga, ver, value):
    """Install a port_jar pin ("<url> <sha256>") into ~/.m2 as ga:ver, the download checked against the sha256; a jar
    already there with that sha256 is not fetched again. -> (ok, message)."""
    url, sha = value.split()
    g, a = ga.split(":")
    have = pathlib.Path.home() / ".m2/repository" / g.replace(".", "/") / a / ver / f"{a}-{ver}.jar"
    if have.is_file() and hashlib.sha256(have.read_bytes()).hexdigest() == sha:
        return True, f"{ga}:{ver} already in mavenLocal (sha256 ok)"
    try:
        data = _get(url)
    except Exception as e:
        return False, f"{url}: {e}"
    got = hashlib.sha256(data).hexdigest()
    if got != sha:
        return False, f"{url} has sha256 {got}, the pin says {sha}"
    install_m2(ga, ver, data)
    return True, f"{ga}:{ver} <- {url} (sha256 ok)"


def install_m2(ga, ver, data):
    """Put a jar into ~/.m2 as group:artifact:ver (with a minimal POM), the way port-ci's --dep-jar step does."""
    g, a = ga.split(":")
    dest = pathlib.Path.home() / ".m2/repository" / g.replace(".", "/") / a / ver
    dest.mkdir(parents=True, exist_ok=True)
    (dest / f"{a}-{ver}.jar").write_bytes(data)
    (dest / f"{a}-{ver}.pom").write_text(f"<project><modelVersion>4.0.0</modelVersion><groupId>{g}</groupId>"
                                         f"<artifactId>{a}</artifactId><version>{ver}</version><packaging>jar</packaging>"
                                         f"</project>\n", encoding="utf-8")


def mr_files(project):
    vs = json.loads(_get(f"{MODRINTH}/project/{project}/version"))
    return [{"id": v["id"], "mcs": v.get("game_versions", []), "loaders": v.get("loaders", []),
             "channel": v.get("version_type"), "date": v.get("date_published"), "name": v.get("version_number")}
            for v in vs]


def mr_project_id(slug):
    """A Modrinth slug's project id (maven.modrinth accepts either; port-install.json's cdn URLs carry the id), or None."""
    try:
        return json.loads(_get(f"{MODRINTH}/project/{slug}"))["id"]
    except Exception:
        return None


def mr_project_of_sha1(sha1):
    try:
        return json.loads(_get(f"{MODRINTH}/version_file/{sha1}?algorithm=sha1"))["project_id"]
    except Exception:
        return None


def cf_provider():
    import providers
    p = providers.CurseForgeProvider()
    return p if p.available() else None


CF_CHANNEL = {1: "release", 2: "beta", 3: "alpha"}
CF_NEOFORGE = 6


def cf_files(cf, project, target):
    """The project's NeoForge files for exactly `target`, asked of CurseForge by filter: paging the whole file list
    stops at a few hundred files, and a mod that ships for every loader and version has thousands."""
    out, index = [], 0
    while True:
        res = cf._get(f"/mods/{project}/files?gameVersion={target}&modLoaderType={CF_NEOFORGE}&index={index}&pageSize=50")
        for f in res.get("data", []):
            gv = f.get("gameVersions", [])
            out.append({"id": str(f["id"]), "mcs": [g for g in gv if g[:1].isdigit()],
                        "loaders": [g.lower() for g in gv if not g[:1].isdigit()],
                        "channel": CF_CHANNEL.get(f.get("releaseType"), "alpha"),
                        "date": f.get("fileDate"), "name": f.get("fileName")})
        index += 50
        if not res.get("data") or index >= res.get("pagination", {}).get("totalCount", 0):
            return out


def resolve_curse(d, target, cf, cache):
    if cf is not None:
        f = pick(cf_files(cf, d["cf_project"], target), target)
        if f:
            return {"to": f"{d['group']}:{d['module']}:{f['id']}", "source": f"CurseForge file {f['id']} ({f['name']})"}
        return {"missing": f"no NeoForge {target} file on CurseForge project {d['cf_project']}", "source": "CurseForge"}
    url = f"{CURSEMAVEN}/{d['module']}/{d['version']}/{d['module']}-{d['version']}.jar"
    try:
        sha1 = hashlib.sha1(_get(url)).hexdigest()
    except Exception as e:
        return {"missing": f"base jar not fetchable from cursemaven ({e})", "source": "cursemaven"}
    proj = mr_project_of_sha1(sha1)
    if not proj:
        return {"missing": "the base jar is not on Modrinth and CURSEFORGE_API_KEY is not set (CurseForge not checked)",
                "source": "Modrinth (by sha1)"}
    return resolve_modrinth(proj, target, cache, via=f"Modrinth project {proj}, found by the base jar's sha1; CurseForge not checked")


def resolve_modrinth(project, target, cache, via=None):
    files = cache.setdefault(project, None) or mr_files(project)
    cache[project] = files
    f = pick(files, target)
    src = via or f"Modrinth project {project}"
    if not f:
        return {"missing": f"no NeoForge {target} version on Modrinth", "source": src}
    return {"to": f"maven.modrinth:{project}:{f['id']}", "source": f"{src}: {f['name']} ({f['channel']})"}


def checkout_lock(co, target):
    """tools/layer-ci.py's CheckoutLock on a sibling checkout, with a copy of the environment: this process may be a
    child of the dependent's own lock holder, and that inheritance must neither let it into the sibling's lock nor leak."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("layer_ci", ROOT / "tools/layer-ci.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    env = dict(os.environ)
    env.pop(m.LOCK_ENV, None)
    return m.CheckoutLock(co, "layer-deps.py", target, env=env)


def resolve_port(ga, spec, target, checkouts):
    repo = spec["repo"]
    stem = spec["asset"][:-4]
    ver = f"{spec['version']}-mc{target}"
    co = checkouts.get(repo)
    if isinstance(co, str):         # --port-release owner/repo@tag: the sibling's released layer jar, pinned for CI
        url = f"https://github.com/{repo}/releases/download/{co}/{stem}-mc{target}.jar"
        try:
            data = _get(url)
        except Exception as e:
            # a version-branch library releases ONE version per tag (<mod>-<ver>-mc<t>), its jar named as its build
            # names it; only a tag of exactly that version may fall back to the plain name (a layer release's plain
            # jar is the base version's)
            if not co.endswith(f"-mc{target}"):
                return {"missing": f"{repo}'s release {co} has no {stem}-mc{target}.jar ({e})", "source": repo}
            url = f"https://github.com/{repo}/releases/download/{co}/{spec['asset']}"
            try:
                data = _get(url)
            except Exception as e2:
                return {"missing": f"{repo}'s release {co} has neither {stem}-mc{target}.jar nor {spec['asset']} ({e2})",
                        "source": repo}
        install_m2(ga, ver, data)
        sha = hashlib.sha256(data).hexdigest()
        return {"to": f"{ga}:{ver}", "pin": f"{url} {sha}", "source": f"{repo} release {co}: {stem}-mc{target}.jar (sha256 {sha[:16]}…)"}
    if not co:
        return {"missing": f"no --port-checkout {repo}=DIR or --port-release {repo}@TAG to take its {target} jar from",
                "source": repo}
    if not (co / ".multiversion").is_dir() and (co / ".port" / target / "branch.properties").exists():
        return checkout_jar(ga, spec, target, co, ver)
    jar = co / "build" / f"mc{target}" / "libs" / f"{stem}-mc{target}.jar"
    # build EVERY time, not only when the jar is missing: Gradle's up-to-date checks make it cheap when nothing changed,
    # and a jar left from before the sibling re-synced a fixed shared helper carries the old bug into this fork's gates
    # (measured: a dependency's stale render helper failed four of a dependent's Gate C runs after the fix was written)
    if not (co / ".multiversion" / "targets" / target / "target.properties").exists():
        return {"missing": f"{repo} has no {target} target", "source": repo}
    # `build`, not `assemble`: a mod's release variants (the asset the port's CI pins) are often Jar tasks the authors
    # hang off `build` only. Tests are the sibling's own CI's business, not this one's.
    # the sibling checkout's own run (its fork-port.py, its residue fixer's gates) holds build/.fork-port.lock: a
    # build there would rewrite the classes and jars that run is testing (measured: a dependent's resolve rebuilt
    # its sibling mid-gate and the sibling's Gate C failed on a class missing from its half-rewritten classes dir).
    # Then take the jar it already has, and say so; never build under another run.
    lock = checkout_lock(co, target)
    busy = lock.acquire()
    if busy:
        if jar.exists():
            sha = hashlib.sha256(jar.read_bytes()).hexdigest()[:16]
            install_m2(ga, ver, jar.read_bytes())
            return {"to": f"{ga}:{ver}", "source": f"{repo} layer jar {jar.name} (sha256 {sha}…) into mavenLocal, NOT "
                    f"rebuilt: its checkout is busy ({busy.split(':')[0]}); re-resolve once that run ends"}
        return {"missing": f"{repo}'s checkout is busy and has no {jar.name} yet: {busy}", "source": repo}
    log = co / "build" / f"mc{target}" / "layer-deps-build.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(log, "w", encoding="utf-8") as out:
            r = subprocess.run([BASH, str(co / ".multiversion/build.sh"), target, "build", "-x", "test", "--console=plain"],
                               cwd=co, stdout=out, stderr=subprocess.STDOUT)
    finally:
        lock.release()
    if r.returncode != 0 or not jar.exists():
        what = "failed" if r.returncode != 0 else f"succeeded but wrote no {jar.name}"
        return {"missing": f"{repo}'s {target} build {what} (log: {log})", "source": repo}
    install_m2(ga, ver, jar.read_bytes())
    sha = hashlib.sha256(jar.read_bytes()).hexdigest()[:16]
    return {"to": f"{ga}:{ver}", "source": f"{repo} layer jar {jar.name} (sha256 {sha}…) into mavenLocal"}


def checkout_jar(ga, spec, target, co, ver):
    """--port-checkout of a VERSION-BRANCH sibling: never build there (its checkout is another run's). Take the jar
    that checkout already built for `target` (it must be on that branch, its build/libs holding the release asset's
    name), copy it into mavenLocal, and pin its sha256 as development only: CI cannot fetch it and the dependent's
    done check refuses it until it is re-resolved against a release (--port-release)."""
    fb = _fb()
    if fb.checkout_version(co) != target:
        return {"missing": f"{spec['repo']}'s checkout {co} is not on {fb.branch_name(target)}", "source": spec["repo"]}
    jar = co / "build/libs" / spec["asset"]
    if not jar.is_file():
        return {"missing": f"{spec['repo']}'s checkout has no {jar.relative_to(co).as_posix()} (build it there first; a "
                           "dependent never builds a sibling's checkout)", "source": spec["repo"]}
    data = jar.read_bytes()
    install_m2(ga, ver, data)
    sha = hashlib.sha256(data).hexdigest()
    head = _git(co, "rev-parse", "--short", "HEAD").stdout.strip()
    return {"to": f"{ga}:{ver}", "pin_dev": f"{sha} {spec['repo']}@{head}",
            "source": f"{spec['repo']} checkout {head}: {spec['asset']} (sha256 {sha[:16]}...) copied into mavenLocal -- "
                      "development only, re-resolve with --port-release"}


# ---------------------------------------------------------------- library first (a sibling's target must be done)

GATES_HEAD = re.compile(r"at `([^`]+)` for Minecraft (\S+)")
NOT_JAR = {"GATES.md", "COST.md", "RESIDUE.md", "STUBS.json", "REVIEW.md", "TEST-ITEMS.md", "BEHAVIOUR.json", "DEPS.md",
           "INVENTORY.json", "INVENTORY-CHECK.json"}


def gates_record(text, target):
    """(ok, why, sha) for a sibling target's GATES.md (what ci-gates.py --mc writes and fork-port.py keeps only after a
    green full run): written for Minecraft <target>, at a commit (not `<sha> + uncommitted: ...`), a PASS row naming
    Gate C and no FAIL row."""
    if not text:
        return False, f"no .multiversion/targets/{target}/GATES.md (no green full gate run recorded)", None
    m = GATES_HEAD.search(text.splitlines()[0])
    if not m or m.group(2) != target:
        return False, f"its GATES.md is for Minecraft {m.group(2) if m else '?'}, not {target}", None
    if "uncommitted" in m.group(1):
        return False, f"its GATES.md certifies an edited tree ({m.group(1)}), not a commit", None
    rows = [ln for ln in text.splitlines() if ln.startswith("|")]
    if any(re.search(r"\|\s*FAIL\s*\|", ln) for ln in rows):
        return False, "its GATES.md has a FAIL row", None
    if not any("Gate C" in ln and re.search(r"\|\s*PASS\s*\|", ln) for ln in rows):
        return False, "its GATES.md records no green Gate C (A/B green is not done)", None
    return True, f"green Gate C at {m.group(1)}", m.group(1)


def open_stubs(text):
    """The number of a STUBS.json text's entries with no review (fork-port.py never reports such a target done)."""
    try:
        data = json.loads(text) if text else []
    except ValueError:
        return 0
    return sum(1 for e in data if isinstance(e, dict) and not e.get("reviewed")) if isinstance(data, list) else 0


def _not_jar(f):
    return f.rsplit("/", 1)[-1] in NOT_JAR


def _chain_of(props_of, target):
    """target, its `inherits`, theirs ..., plus every `shared_chain` version: whose files can change its jar."""
    out, t, extra = [], target, []
    while t and t not in out:
        out.append(t)
        p = props_of(t)
        extra += [x.strip() for x in p.get("shared_chain", "").split(",") if x.strip()]
        t = p.get("inherits")
    return out + [x for x in extra if x not in out]


def _props(text):
    out = {}
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _publish():
    import importlib.util
    spec = importlib.util.spec_from_file_location("port_publish_for_layer_deps", ROOT / "tools/port-publish.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _git(co, *a):
    # GIT_OPTIONAL_LOCKS=0: `git status` must not refresh (write) the sibling's index -- another run may hold its checkout
    return subprocess.run(["git", "-C", str(co), *a], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})


def _gates_current(target, gates, changed, chain):
    """(ok, why) for the jar: GATES.md a green Gate C for the target at a commit, and no file that can change the jar
    changed between that commit and the one used (changed None: cannot tell), as port-publish.py's listed_green()."""
    ok, why, _sha = gates_record(gates, target)
    if not ok:
        return False, why
    green, cur = _publish().listed_green(target, gates, [f for f in changed if not _not_jar(f)] if changed is not None
                                         else None, chain)
    return (True, why) if green else (False, f"{why}, but {cur}")


def _reasons(why, more=6):
    return "; ".join(why[:more]) + (f" (+{len(why) - more} more)" if len(why) > more else "")


def library_done_checkout(co, target):
    """Is a sibling checkout's target done at the commit its jar is built from? The jar check first (GATES.md current
    at HEAD plus its edited tracked files and every untracked file under .multiversion/ -- an untracked overlay is
    built into the jar), then THE done list: tools/fork-port.py's target_done(co, target), the same definition a
    `fork-port.py --check-done` in that checkout answers (a missing REVIEW.md is refused there). -> (done, why, notes)"""
    co = pathlib.Path(co)
    tdir = co / ".multiversion/targets" / target
    gp = tdir / "GATES.md"
    gates = gp.read_text(encoding="utf-8", errors="replace") if gp.is_file() else ""
    ok, why, sha = gates_record(gates, target)
    if not ok:
        return False, why, []
    sha = sha.split()[0]
    if _git(co, "cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
        changed = None
    elif _git(co, "merge-base", "--is-ancestor", sha, "HEAD").returncode != 0:
        return False, f"{why}, but {sha} is not an ancestor of the checkout's HEAD", []
    else:
        d = _git(co, "diff", "--name-only", sha, "HEAD")
        st = _git(co, "status", "--porcelain", "--untracked-files=no")
        new = _git(co, "ls-files", "--others", "--exclude-standard", "--", ".multiversion")
        changed = None if d.returncode or new.returncode else (
            [ln for ln in d.stdout.splitlines() if ln] + [ln[3:].strip() for ln in st.stdout.splitlines() if len(ln) > 3]
            + [ln for ln in new.stdout.splitlines() if ln])
    chain = _chain_of(lambda t: _props((co / ".multiversion/targets" / t / "target.properties").read_text(encoding="utf-8")
                                       if (co / ".multiversion/targets" / t / "target.properties").is_file() else ""), target)
    ok, why = _gates_current(target, gates, changed, chain)
    if not ok:
        return False, why, []
    fp = _fork_port()
    if fp is None or not hasattr(fp, "target_done"):
        return False, f"{why}, but tools/fork-port.py's target_done() could not be loaded: cannot judge the done list", []
    try:
        undone = fp.target_done(co, target, write=False)   # judging a sibling library: never write into its checkout
    except Exception as e:      # noqa: BLE001 -- fail closed: an unanswerable done check is not done
        return False, f"{why}, but fork-port.py's target_done() failed ({type(e).__name__}: {e})", []
    if undone:
        return False, f"{why}, but fork-port.py's done list says not done: {_reasons(undone)}", []
    rp = tdir / "REVIEW.md"
    commit = fp.parse_review(rp.read_text(encoding="utf-8", errors="replace"))[0].get("commit", "?") if rp.is_file() else "?"
    return True, why + ", current at the commit used", [f"fork-port.py target_done(): done (REVIEW.md commit {commit})"]


_FP = []


def _fork_port():
    """tools/fork-port.py as a module (target_done(), parse_review(), REVIEW_OUTPUTS: one definition), or None."""
    if not _FP:
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("fork_port_for_layer_deps", ROOT / "tools/fork-port.py")
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            _FP.append(m)
        except Exception:          # noqa: BLE001 -- callers fail closed
            return None
    return _FP[0]


HEX_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")


def release_overlays(paths, target):
    """The whole-file overlays target carries at a tag, from the tag's tree paths -- tools/layer-sync.py's
    definition: targets/<t>/java/<rel>.java for src/main/java/<rel>, targets/<t>/<set>/java/<rel>.java for
    src/<set>/java/<rel>, only when that authors' file exists. -> sorted overlay paths."""
    pre, have, out = f".multiversion/targets/{target}/", set(paths), []
    for p in paths:
        if not p.startswith(pre) or not p.endswith(".java"):
            continue
        parts = p[len(pre):].split("/")
        if parts[0] == "java" and len(parts) > 1:
            s, rel = "main", parts[1:]
        elif len(parts) > 2 and parts[1] == "java":
            s, rel = parts[0], parts[2:]
        else:
            continue
        if f"src/{s}/java/{'/'.join(rel)}" in have:
            out.append(p)
    return sorted(out)


def release_review_problems(text, items, fp):
    """fork-port.py's review_problems() for a REVIEW.md read at a tag: [] when complete, else one line each. items:
    [(kind, file)] the tag needs a verdict on (its STUBS.json entries, whole-file overlays, resource groups and
    renames.tsv)."""
    if text is None:
        return [f"no REVIEW.md: {len(items)} item(s) need an independent reviewer's verdict"]
    fields, rows = fp.parse_review(text)
    out = [f"REVIEW.md has no {k}" for k in fp.REVIEW_FIELDS if fields.get(k, "?") in ("", "?")]
    if fields.get("date", "?") not in ("", "?") and not re.match(r"^\d{4}-\d\d-\d\d\b", fields["date"]):
        out.append(f"REVIEW.md date {fields['date']!r} is not YYYY-MM-DD")
    c = fields.get("commit", "?")
    if c not in ("", "?") and not HEX_COMMIT.match(c):
        out.append(f"REVIEW.md commit {c!r} is not a commit id (7-40 hex digits): a name never goes stale")
    for kind, file in items:
        v = rows.get((kind, file))
        if not v or v[0] not in fp.VERDICTS:
            out.append(f"no verdict: {kind} {file}")
        elif v[0] != "ok":
            out.append(f"reviewer says not-ok: {kind} {file} -- {v[1][:200]}")
        elif not v[1] or v[1] == "?":
            out.append(f"a verdict without a note: {kind} {file}")
    return out


def library_done_release(repo, tag, target, get=None, _seen=None):
    """Is a sibling's target done at the commit its release `tag` was built from? fork-port.py's done list, read at
    the tag: GATES.md (green Gate C, current: the GitHub compare API from its commit to the tag), STUBS.json, REVIEW.md
    (complete; a row for every STUBS.json entry, every whole-file overlay, resource group and renames.tsv in the tag's
    tree -- the GitHub tree API, whose blob ids give the same digests a checkout's do;
    its commit a hex id that is the tag or an ancestor of it, with nothing under src/, the chain's targets/ or shared/
    changed between it and the tag), TEST-ITEMS.md, the inventory account (INVENTORY-CHECK.json, when the layer has
    INVENTORY.json), no DEPS.md "Development only" mark, and a parent added with --allow-undone-parent done too.
    raw.githubusercontent.com for files; GH_TOKEN/GITHUB_TOKEN is sent to the API when set. -> (done, why, notes)"""
    import urllib.error
    get = get or _get
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    auth = {"Authorization": f"Bearer {tok}"} if tok else None

    def raw(path):
        try:
            return get(f"https://raw.githubusercontent.com/{repo}/{tag}/{path}").decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    def compare(base):
        return json.loads(get(f"https://api.github.com/repos/{repo}/compare/{base}...{tag}", auth))
    fp = _fork_port()
    if fp is None or not hasattr(fp, "parse_review"):
        return False, f"at release {tag}: tools/fork-port.py could not be loaded: cannot judge the done list", []
    tdir = f".multiversion/targets/{target}"
    try:
        gates = raw(f"{tdir}/GATES.md") or ""
        ok, why, sha = gates_record(gates, target)
        if not ok:
            return False, f"at release {tag}: {why}", []
        review = raw(f"{tdir}/REVIEW.md")
        stubs = raw(f"{tdir}/STUBS.json") or ""
        props = {}
        chain = _chain_of(lambda t: props.setdefault(t, _props(raw(f".multiversion/targets/{t}/target.properties"))), target)
        cmp_ = compare(sha.split()[0])
        tree = json.loads(get(f"https://api.github.com/repos/{repo}/git/trees/{tag}?recursive=1", auth))
        test_items = raw(f"{tdir}/TEST-ITEMS.md")
        deps_md = raw(f"{tdir}/DEPS.md") or ""
        paths = [e.get("path", "") for e in tree.get("tree", []) if e.get("type") == "blob"]
        blobs = {e.get("path", ""): e.get("sha", "") for e in tree.get("tree", []) if e.get("type") == "blob"}
        inv_check = raw(f"{tdir}/INVENTORY-CHECK.json") if ".multiversion/INVENTORY.json" in paths else ""
        fields = fp.parse_review(review)[0] if review is not None else {}
        rc = fields.get("commit", "?")
        rcmp = compare(rc) if HEX_COMMIT.match(rc) else None
    except Exception as e:
        return False, f"could not read {repo}@{tag}'s {target} records ({e}): cannot prove its target is done", []
    if cmp_.get("status") not in ("ahead", "identical"):
        return False, f"at release {tag}: {why}, but {sha} is not an ancestor of the tag ({cmp_.get('status')})", []
    files = [f.get("filename", "") for f in cmp_.get("files", [])]
    ok, why = _gates_current(target, gates, None if len(files) >= 300 else files, chain)   # the API lists <= 300 files
    if not ok:
        return False, f"at release {tag}: {why}", []
    out = []
    if open_stubs(stubs):
        out.append(f"{open_stubs(stubs)} unreviewed stub signal(s) in STUBS.json")
    if tree.get("truncated"):
        out.append("the tag's tree is truncated (GitHub's tree API): cannot list its overlays to check REVIEW.md's rows")
    try:
        sdata = json.loads(stubs) if stubs else []
    except ValueError:
        sdata = []
    items = sorted({("stub", e["file"]) for e in sdata if isinstance(e, dict) and e.get("file")}
                   | {("overlay", p) for p in release_overlays(paths, target)}
                   # the target's resource groups and renames.tsv, each with its digest of the tree's blob ids: the
                   # same items fork-port.py review_items() gives a checkout
                   | (fp.review_file_items(tdir, [(p[len(tdir) + 1:], b) for p, b in blobs.items() if p.startswith(tdir + "/")])
                      if hasattr(fp, "review_file_items") else set()))
    out += release_review_problems(review, items, fp)
    if rcmp is not None:
        if rcmp.get("status") not in ("ahead", "identical"):
            out.append(f"REVIEW.md names commit {rc}, which is not the tag or an ancestor of it ({rcmp.get('status')})")
        else:
            rfiles = [f.get("filename", "") for f in rcmp.get("files", [])]
            scope = ("src/", ".multiversion/shared/") + tuple(f".multiversion/targets/{t}/" for t in chain)
            stale = sorted(f for f in rfiles if f.startswith(scope) and f.rsplit("/", 1)[-1] not in fp.REVIEW_OUTPUTS)
            if len(rfiles) >= 300:
                out.append(f"cannot tell what changed between the reviewed commit {rc} and the tag (300+ files)")
            elif stale:
                out.append(f"{len(stale)} file(s) changed between the reviewed commit {rc} and the tag, e.g. {stale[0]}: "
                           "re-review them and name the new commit")
    if test_items is None:
        out.append("manual-test items not generated (no TEST-ITEMS.md at the tag)")
    if inv_check is None:
        out.append("behaviour inventory: the layer has INVENTORY.json but the tag has no INVENTORY-CHECK.json for the target")
    elif inv_check:
        try:
            acc = json.loads(inv_check)
            if acc.get("unaccounted") != [] or acc.get("target") != target:
                out.append(f"behaviour inventory: {len(acc.get('unaccounted') or [])} item(s) unaccounted at the tag")
        except (ValueError, AttributeError):
            out.append("behaviour inventory: the tag's INVENTORY-CHECK.json cannot be read")
    if fp.UNDONE_LIBRARY_MARK in deps_md:
        out.append("built against a library target that is not done (--allow-undone-library, DEPS.md \"Development only\")")
    allowed = props.get(target, {}).get("allowed_undone_parent", "")
    seen = (_seen or set()) | {target}
    if allowed and allowed.split()[0] not in seen:
        p = allowed.split()[0]
        pd = library_done_release(repo, tag, p, get, seen)
        if not pd[0]:
            out.append(f"its parent {p} is still not done (added with --allow-undone-parent): {pd[1]}")
    if out:
        return False, f"at release {tag}: {why}, but the done list says not done: {_reasons(out)}", []
    return True, f"at release {tag}: {why}, current at the commit used", \
        [f"review recorded (REVIEW.md: {len(items)} item(s) ok, commit {rc}, at or under the tag)"]


def library_done(repo, co, target):
    """resolve()'s check for a sibling named by --port-checkout (co a Path) or --port-release (co a tag)."""
    if isinstance(co, str):
        if branch_release(repo, co, target):
            return library_done_branch_release(repo, co, target)
        return library_done_release(repo, co, target)
    if not (pathlib.Path(co) / ".multiversion").is_dir() and _fb().is_branch_fork(co, target):
        return library_done_branch(co, target)
    return library_done_checkout(co, target)


def _library_first(why):
    """The library's done reasons that block a dependent: all but the library rule's own (a dependent passing Gate C
    against the library's jar is what that rule waits for -- the dependent cannot wait for itself)."""
    fb = _fb()
    return [w for w in why if not w.startswith(fb.LIBRARY_PENDING)]


def library_done_branch(co, target):
    """A version-branch sibling checkout: tools/fork-branches.py target_done() on its neoforge-<target> branch (read
    from the branch's commit when the checkout is elsewhere), never writing. -> (done, why, notes)"""
    fb = _fb()
    why = _library_first(fb.target_done(co, target))
    if why:
        return False, f"fork-branches.py's done list says not done: {_reasons(why)}", []
    return True, f"version branch {fb.branch_name(target)} done", ["tools/fork-branches.py target_done(): done"]


def branch_release(repo, tag, target, get=None):
    """Is release `tag` of `repo` a version branch's (its tree holds .port/<target>/branch.properties)?"""
    import urllib.error
    get = get or _get
    try:
        get(f"https://raw.githubusercontent.com/{repo}/{tag}/.port/{target}/branch.properties")
        return True
    except urllib.error.HTTPError:
        return False
    except Exception:          # noqa: BLE001 -- unreachable: the layer path's own reads report it
        return False


GIT_URL_ENV = "LAYER_DEPS_GIT_URL"          # a format with {repo} (tests: a local path); default GitHub over https


def library_clone(repo):
    """A bare clone of `repo` under ~/.mmvu/cache/git/ (fetched each call): the library's history and tags, read
    without a checkout of anyone's."""
    url = os.environ.get(GIT_URL_ENV, "https://github.com/{repo}.git").format(repo=repo)
    d = pathlib.Path.home() / ".mmvu/cache/git" / repo.replace("/", "__")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    if not (d / "HEAD").is_file():
        d.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["git", "clone", "-q", "--bare", url, str(d)], capture_output=True, text=True, encoding="utf-8",
                           env=env)
        if r.returncode:
            return None, f"cannot clone {url}: {r.stderr.strip()[-200:]}"
    r = subprocess.run(["git", "-C", str(d), "fetch", "-q", "--prune", "origin", "+refs/heads/*:refs/heads/*",
                        "+refs/tags/*:refs/tags/*"], capture_output=True, text=True, encoding="utf-8", env=env)
    if r.returncode:
        return None, f"cannot fetch {url}: {r.stderr.strip()[-200:]}"
    return d, None


def library_done_branch_release(repo, tag, target):
    """A version-branch library's release: the same done list (tools/fork-branches.py target_done) run on the tag's
    tree with the library's own history -- so the release-side answer and the checkout's agree (issue #133, gap 1).
    -> (done, why, notes)"""
    fb = _fb()
    d, err = library_clone(repo)
    if d is None:
        return False, err, []
    c = _git(d, "rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}").stdout.strip()
    if not c:
        return False, f"{repo} has no tag {tag}", []
    with tempfile.TemporaryDirectory() as t:
        root = fb.export(d, c, pathlib.Path(t) / "tree")
        why = _library_first(fb.target_done(d, target, root=root, ref=c))
    if why:
        return False, f"its {target} at {tag} is not done: {_reasons(why)}", []
    return True, f"version branch {fb.branch_name(target)} done at {tag}", [f"release {tag} ({c[:12]})"]


# configurations that put a mod on the runtime classpath: one the manifest does not name is taken as required (fail
# closed -- a mod the jar needs at run time that silently fell off the classpath ships a broken target)
# (jarJar bundles the mod inside the jar, so it is there at run time too)
RUNTIME_CONFS = {"implementation", "api", "modImplementation", "jarJar"}


def resolve(repo, target, checkouts, lookups=None, dry_run=False, pins=None, allow_undone=False):
    """(rows, stop). lookups overrides the network (self-check). dry_run: a sibling checkout is not built or installed
    (its row says what would be). pins: {group:module: (substitute coord, port_jar value)} the target's block already
    holds -- a released sibling's pin is kept when no --port-checkout/--port-release names it this run.
    A sibling named by --port-checkout or --port-release is used only when its own `target` is done (library_done:
    a green Gate C recorded in its GATES.md at the commit used, then fork-port.py's done list); else its
    row is "refused" and the resolve stops -- unless allow_undone and it is a checkout, whose row then says
    "undone" (DEPS.md records the override)."""
    lk = {"curse": resolve_curse, "modrinth": resolve_modrinth, "port": resolve_port, "cf": cf_provider,
          "mr_id": mr_project_id, "pin_jar": install_pin, "library_done": library_done, **(lookups or {})}
    pins = pins or {}
    build = (repo / "build.gradle").read_text(encoding="utf-8")
    mpath = repo / ".github/port-install.json"
    manifest = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else None
    roles = manifest_roles(manifest)
    wf = repo / ".github/workflows/port-ci.yml"
    ports = port_deps(wf.read_text(encoding="utf-8") if wf.exists() else "")
    cf = lk["cf"]()
    cache, ids, rows, missing_required, refused = {}, {}, [], [], []
    for d in gradle_deps(build):
        ga = f"{d['group']}:{d['module']}"
        row = dict(d)
        unnamed = "required" if manifest is None or d["conf"] in RUNTIME_CONFS else "build-only"
        if ga in ports:
            spec = ports[ga]
            row["role"] = roles.get(f"port:{spec['repo']}", "required")
            pin = pins.get(ga)
            ver = f"{ga}:{spec['version']}-mc{target}"
            co = checkouts.get(spec["repo"])
            if co is not None:
                # library first: the dependent's target T builds only against a library T that is done
                done, why, notes = lk["library_done"](spec["repo"], co, target)
                if not done and isinstance(co, pathlib.Path) and allow_undone:
                    row["undone"] = why
                elif not done:
                    row.update({"refused": f"{spec['repo']}'s {target} target is not done: {why}", "source": spec["repo"]})
                    refused.append(f"{ga} ({row['refused']})")
                    rows.append(row)
                    continue
                else:
                    row["library"] = "; ".join([f"{target} done: {why}"] + notes)
            if spec["repo"] not in checkouts and pin and pin[0] == ver and pin[1]:
                # pinned by an earlier `resolve --port-release`, and nothing this run names the sibling: keep the pin
                row.update({"to": ver, "pin": pin[1], "source": f"{spec['repo']}: kept the earlier --port-release pin"})
                if not dry_run:
                    # the pin alone serves CI (layer-deps fetch); put the jar into this machine's mavenLocal too, so a
                    # fresh checkout's compile resolves the substitute -- same sha256-checked download as fetch
                    ok, msg = lk["pin_jar"](ga, ver.split(":", 2)[2], pin[1])
                    row["source"] += f"; {msg}" if ok else f"; NOT installed into mavenLocal: {msg}"
            elif dry_run and isinstance(checkouts.get(spec["repo"]), str):
                row.update({"to": ver, "source": f"{spec['repo']}: would fetch release {checkouts[spec['repo']]}'s "
                                                 f"{spec['asset'][:-4]}-mc{target}.jar, pin and install it (dry run)"})
            elif dry_run and isinstance(checkouts.get(spec["repo"]), pathlib.Path):
                co = checkouts[spec["repo"]]
                row.update({"to": ver, "source": f"{spec['repo']}: would build {target} in {co} and install it (dry run)"}
                           if (co / ".multiversion/targets" / target / "target.properties").exists()
                           else {"to": ver, "source": f"{spec['repo']}: would copy {co}'s built {spec['asset']} (version "
                                                      f"branch {target}; development only) into mavenLocal (dry run)"}
                           if (co / ".port" / target / "branch.properties").exists()
                           else {"missing": f"{spec['repo']} has no {target} target", "source": spec["repo"]})
            else:
                row.update(lk["port"](ga, spec, target, checkouts))
            if row.get("library") and row.get("to"):
                row["source"] = f"{row.get('source', '')}; library {row['library']}"
        elif d["kind"] == "curse":
            row["role"] = roles.get(f"cf:{d['cf_project']}", unnamed)
            row.update(lk["curse"](d, target, cf, cache))
        elif d["kind"] == "modrinth":
            # the build names a slug or an id; the manifest's cdn URL carries the id
            role = roles.get(f"mr:{d['module']}")
            if role is None and manifest is not None:
                if d["module"] not in ids:
                    ids[d["module"]] = lk["mr_id"](d["module"])
                role = roles.get(f"mr:{ids[d['module']]}") if ids[d["module"]] else None
            row["role"] = role or unnamed
            row.update(lk["modrinth"](d["module"], target, cache))
        else:
            continue
        if row.get("missing"):
            if row["role"] == "required":
                missing_required.append(f"{ga} ({row['missing']})")
            else:
                row["drop"] = f"no {target} build ({row['role']})"
        rows.append(row)
    stop = ("required dependencies with no build for " + target + ": " + "; ".join(missing_required)) if missing_required else None
    if refused:
        stop = "; ".join(x for x in ["library first: sibling library targets not done: " + "; ".join(refused) +
                                      " (finish and release the library's target first; --allow-undone-library is for "
                                      "development against a --port-checkout only)", stop] if x)
    return rows, stop


PIN_LINE = re.compile(r"(?m)^(substitute|port_jar)\.(.+?)=(.*?)\s*$")


def current_pins(repo, target):
    """{group:module: (substitute coordinate, port_jar value)} for each port_jar pin in the target's layer-deps block."""
    tp = props_file(repo, target)
    text = tp.read_text(encoding="utf-8") if tp.is_file() else ""
    if BEGIN not in text or END not in text:
        return {}
    got = {}
    for kind, k, v in PIN_LINE.findall(text[text.index(BEGIN):text.index(END)]):
        got.setdefault(k.replace("\\:", ":"), {})[kind] = v
    return {ga: (x.get("substitute"), x["port_jar"]) for ga, x in got.items() if x.get("port_jar")}


def write(repo, target, rows, stop):
    t = target_dir(repo, target)
    if not t.is_dir():
        return False
    (t / "DEPS.md").write_text(render_md(target, rows, stop), encoding="utf-8")
    if stop:
        return True
    p = props_file(repo, target)
    p.write_text(splice(p.read_text(encoding="utf-8"), render_block(rows)), encoding="utf-8")
    return True


def cmd_resolve(a):
    repo = pathlib.Path(a.repo).resolve()
    if not (repo / ".multiversion/base.properties").exists() and not branch_target(repo, a.target):
        print("layer-deps: no .multiversion/ in the fork (a single-version fork has nothing to resolve)")
        return 2
    checkouts = {}
    for s in a.port_checkout:
        k, _, v = s.partition("=")
        if not v:
            print(f"layer-deps: --port-checkout must be owner/repo=DIR, got {s!r}"); return 2
        checkouts[k] = pathlib.Path(v).expanduser().resolve()
    for s in getattr(a, "port_release", []):
        k, _, v = s.partition("@")
        if not v or not re.fullmatch(r"[\w.-]+/[\w.-]+", k) or not re.fullmatch(r"[\w.+-]+", v):
            print(f"layer-deps: --port-release must be owner/repo@TAG, got {s!r}"); return 2
        checkouts[k] = v
    rows, stop = resolve(repo, a.target, checkouts, dry_run=a.dry_run, pins=current_pins(repo, a.target),
                         allow_undone=getattr(a, "allow_undone_library", False))
    for r in rows:
        state = ("-> " + r["to"]) if r.get("to") else ("DROPPED " + r["drop"]) if r.get("drop") else \
                ("REFUSED " + r["refused"]) if r.get("refused") else ("MISSING " + r.get("missing", ""))
        print(f"layer-deps: {a.target} {r['group']}:{r['module']} [{r['role']}] {state}")
        if r.get("library"):
            print(f"layer-deps: {a.target} {r['group']}:{r['module']}: library {r['library']}")
        if r.get("undone"):
            print(f"layer-deps: {a.target} {r['group']}:{r['module']}: LIBRARY NOT DONE, used anyway "
                  f"(--allow-undone-library, recorded in DEPS.md): {r['undone']}")
    wrote = False if a.dry_run else write(repo, a.target, rows, stop)
    n_sub, n_drop = sum(1 for r in rows if r.get("to")), sum(1 for r in rows if r.get("drop"))
    if stop:
        print(f"layer-deps: STOP {stop}")
        return EXIT_UNDONE if any(r.get("refused") for r in rows) else EXIT_REQUIRED
    print(f"layer-deps: {a.target}: {n_sub} substituted, {n_drop} dropped" + (", written" if wrote else ", not written"))
    return 0


# ---------------------------------------------------------------- self-check

def self_check():
    bad = []

    def expect(c, msg):
        if not c:
            bad.append(msg)

    build = '''dependencies {
    implementation("com.example:lib:1.0") { transitive = false }
    implementation "curse.maven:somelib-111:222"
    implementation "curse.maven:extra-333:444"
    // implementation "curse.maven:commented-555:666"
    /* implementation "curse.maven:blockcommented-777:888" */
    compileOnly "maven.modrinth:devmod:abc123"
    implementation "net.neoforged:neoforge:${neo_version}"
    implementation "curse.maven:somelib-111:999"
}'''
    deps = gradle_deps(build)
    expect([d["coord"] for d in deps] == ["com.example:lib:1.0", "curse.maven:somelib-111:222", "curse.maven:extra-333:444",
                                          "maven.modrinth:devmod:abc123"], f"gradle_deps: {[d['coord'] for d in deps]}")
    expect(deps[1]["cf_project"] == "111" and deps[1]["cf_file"] == "222", "curse coordinate parsed")
    man = {"files": [{"role": "self", "url": "x"},
                     {"role": "required", "port": "owner/lib", "url": "https://github.com/owner/lib/releases/download/t/lib.jar"},
                     {"role": "required", "url": "https://www.curseforge.com/api/v1/mods/111/files/222/download"},
                     {"role": "extra", "url": "https://www.curseforge.com/api/v1/mods/333/files/444/download"}]}
    roles = manifest_roles(man)
    expect(roles == {"port:owner/lib": "required", "cf:111": "required", "cf:333": "extra"}, f"roles: {roles}")
    wf = ('      - name: lib 1.0 from the lib-1.0-mc1.21.1 release into mavenLocal (pinned by sha256)\n'
          '        run: |\n          mkdir -p "$HOME/.m2/repository/com/example/lib/1.0"\n'
          '          curl -fsSL -o "$HOME/.m2/repository/com/example/lib/1.0/lib-1.0.jar" '
          '"https://github.com/owner/lib/releases/download/lib-1.0-mc1.21.1/lib-1.0_a.jar"\n')
    expect(port_deps(wf) == {"com.example:lib": {"repo": "owner/lib", "tag": "lib-1.0-mc1.21.1", "asset": "lib-1.0_a.jar",
                                                 "version": "1.0"}}, f"port_deps: {port_deps(wf)}")
    files = [{"id": "a", "mcs": ["1.21.4"], "loaders": ["neoforge"], "channel": "beta", "date": "2026-03"},
             {"id": "b", "mcs": ["1.21.4"], "loaders": ["neoforge"], "channel": "release", "date": "2026-01"},
             {"id": "c", "mcs": ["1.21.4"], "loaders": ["neoforge"], "channel": "release", "date": "2026-02"},
             {"id": "d", "mcs": ["1.21.4"], "loaders": ["fabric"], "channel": "release", "date": "2026-09"},
             {"id": "e", "mcs": ["1.21.40"], "loaders": ["neoforge"], "channel": "release", "date": "2026-09"}]
    expect(pick(files, "1.21.4")["id"] == "c", "pick: newest release beats a newer beta, fabric and 1.21.40 ignored")
    expect(pick(files[:1], "1.21.4")["id"] == "a", "pick: a beta when no release")
    expect(pick(files, "1.21.5") is None, "pick: nothing for another version")
    blk = render_block([{"group": "curse.maven", "module": "somelib-111", "to": "maven.modrinth:p:v"},
                        {"group": "curse.maven", "module": "extra-333", "drop": "no 1.21.4 build (extra)"}])
    expect("substitute.curse.maven\\:somelib-111=maven.modrinth:p:v\n" in blk and
           "exclude.curse.maven\\:extra-333=no 1.21.4 build (extra)\n" in blk, f"render_block: {blk}")
    props = "prop.minecraft_version=1.21.4\n"
    once = splice(props, blk)
    expect(splice(once, blk) == once and once.startswith(props), "splice is idempotent and keeps the rest")
    expect(splice(once, render_block([])).count(BEGIN) == 1, "splice replaces, never stacks")

    # the whole resolve over a fixture fork, network replaced
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td)
        (repo / ".multiversion/targets/1.21.4").mkdir(parents=True)
        (repo / ".multiversion/base.properties").write_text("minecraft_version=1.21.1\n", encoding="utf-8")
        (repo / ".multiversion/targets/1.21.4/target.properties").write_text(props, encoding="utf-8")
        (repo / ".github/workflows").mkdir(parents=True)
        (repo / "build.gradle").write_text(build, encoding="utf-8")
        (repo / ".github/port-install.json").write_text(json.dumps(man), encoding="utf-8")
        (repo / ".github/workflows/port-ci.yml").write_text(wf, encoding="utf-8")
        have = {"somelib-111": True, "extra-333": False}
        lk = {"cf": lambda: None,
              "curse": lambda d, t, cf, c: ({"to": f"maven.modrinth:{d['module']}:v{t}", "source": "fake"} if have[d["module"]]
                                            else {"missing": "none", "source": "fake"}),
              "modrinth": lambda p, t, c: {"missing": "none", "source": "fake"},
              "port": lambda ga, s, t, co: {"to": f"{ga}:{s['version']}-mc{t}", "source": "fake"},
              "mr_id": lambda slug: None, "library_done": lambda r, co, t: (True, "fake done", [])}
        rows, stop = resolve(repo, "1.21.4", {}, lk)
        expect(stop is None, f"optional misses do not stop: {stop}")
        by = {r["module"]: r for r in rows}
        expect(by["lib"]["to"] == "com.example:lib:1.0-mc1.21.4" and by["lib"]["role"] == "required", "sibling port per target")
        expect(by["extra-333"].get("drop") and by["devmod"].get("drop") and by["devmod"]["role"] == "build-only",
               "an extra and a dev-only mod with no build are dropped")
        write(repo, "1.21.4", rows, stop)
        tp = (repo / ".multiversion/targets/1.21.4/target.properties").read_text(encoding="utf-8")
        expect("substitute.curse.maven\\:somelib-111=" in tp and "exclude.maven.modrinth\\:devmod=" in tp, f"written: {tp}")
        expect((repo / ".multiversion/targets/1.21.4/DEPS.md").exists(), "DEPS.md written")
        have["somelib-111"] = False
        rows, stop = resolve(repo, "1.21.4", {}, lk)
        expect(stop and "curse.maven:somelib-111" in stop, f"a required miss stops: {stop}")
        write(repo, "1.21.4", rows, stop)
        tp2 = (repo / ".multiversion/targets/1.21.4/target.properties").read_text(encoding="utf-8")
        expect(tp2 == tp, "a stopped resolve leaves target.properties as it was")
        # a sibling's --port-release pin survives a re-resolve that does not name the sibling (fork-port's setup); one
        # that names it, or a pin for another version, is resolved afresh
        have["somelib-111"] = True
        ported = []
        pinned = []
        lk_p = dict(lk, port=lambda ga, s, t, co: (ported.append(ga), {"to": f"{ga}:{s['version']}-mc{t}", "source": "fake"})[1],
                    pin_jar=lambda ga, v, val: (pinned.append((ga, v, val)), (True, "fake install"))[1])
        pin = ("com.example:lib:1.0-mc1.21.4", "https://github.com/owner/lib/releases/download/x/lib-1.0_a-mc1.21.4.jar " + "b" * 64)
        rows, stop = resolve(repo, "1.21.4", {}, lk_p, pins={"com.example:lib": pin})
        write(repo, "1.21.4", rows, stop)
        expect(not ported and current_pins(repo, "1.21.4") == {"com.example:lib": pin},
               f"an earlier port_jar pin is kept: {current_pins(repo, '1.21.4')} {ported}")
        expect(pinned == [("com.example:lib", "1.0-mc1.21.4", pin[1])], f"a kept pin is installed into mavenLocal: {pinned}")
        rows, stop = resolve(repo, "1.21.4", {"owner/lib": "newtag"}, lk_p, pins={"com.example:lib": pin})
        expect(ported == ["com.example:lib"], "a run that names the sibling resolves it afresh")
        rows, stop = resolve(repo, "1.21.4", {}, lk_p, pins={"com.example:lib": ("com.example:lib:0.9-mc1.21.4", pin[1])})
        expect(ported == ["com.example:lib"] * 2, "a pin for another version is not kept")
        rows, stop = resolve(repo, "1.21.4", {}, lk_p, pins={"com.example:lib": pin}, dry_run=True)
        expect(len(pinned) == 1 and rows[0].get("pin") == pin[1], "dry run: a kept pin is not installed")
        # --dry-run never builds or installs a sibling checkout: it says what it would do
        rows, stop = resolve(repo, "1.21.4", {"owner/lib": repo}, lk_p, dry_run=True)
        expect(len(ported) == 2 and stop is None and any("would build 1.21.4" in r.get("source", "") for r in rows),
               f"dry run: no sibling build: {rows}")
        # Modrinth: the build names a slug, the manifest's cdn URL the project id -- matched through the id lookup;
        # a runtime dependency the manifest does not name is required (fail closed), a compileOnly one is not
        man2 = dict(man, files=man["files"] + [{"role": "required",
                                                "url": "https://cdn.modrinth.com/data/AbCd1234/versions/v1/slugmod.jar"}])
        (repo / ".github/port-install.json").write_text(json.dumps(man2), encoding="utf-8")
        (repo / "build.gradle").write_text(build.replace('compileOnly "maven.modrinth:devmod:abc123"',
                                                         'compileOnly "maven.modrinth:devmod:abc123"\n'
                                                         '    implementation "maven.modrinth:slugmod:v1"\n'
                                                         '    implementation "curse.maven:unlisted-999:1"\n'
                                                         '    jarJar "curse.maven:bundled-888:1"'), encoding="utf-8")
        lk_m = dict(lk, curse=lambda d, t, cf, c: ({"to": "x:y:z", "source": "fake"} if d["module"] == "somelib-111"
                                                    else {"missing": "none", "source": "fake"}),
                    mr_id=lambda slug: {"slugmod": "AbCd1234"}.get(slug))
        rows, stop = resolve(repo, "1.21.4", {}, lk_m)
        by = {r["module"]: r for r in rows}
        expect(by["slugmod"]["role"] == "required" and stop and "slugmod" in stop,
               f"a slug-pinned required Modrinth mod with no build stops: {stop}")
        expect(by["unlisted-999"]["role"] == "required" and "unlisted-999" in stop and by["devmod"]["role"] == "build-only",
               f"an unlisted implementation dependency is required, a compileOnly one build-only: {stop}")
        expect(by["bundled-888"]["role"] == "required" and "bundled-888" in stop,
               f"an unlisted jarJar (bundled, so runtime) dependency is required: {stop}")
        (repo / "build.gradle").write_text(build, encoding="utf-8")
        (repo / ".github/port-install.json").write_text(json.dumps(man), encoding="utf-8")
        # library first: a sibling named by --port-checkout/--port-release whose target is not done is refused (the
        # resolve stops, nothing written but DEPS.md); --allow-undone-library lets a CHECKOUT through, recorded in DEPS.md
        ported.clear()
        lk_u = dict(lk_p, library_done=lambda r, co, t: (False, "no green Gate C", []))
        rows, stop = resolve(repo, "1.21.4", {"owner/lib": repo}, lk_u)
        by = {r["module"]: r for r in rows}
        expect(stop and stop.startswith("library first") and by["lib"].get("refused") and not ported,
               f"an undone sibling checkout is refused, not built: {stop}")
        rows, stop = resolve(repo, "1.21.4", {"owner/lib": "tag1"}, lk_u, allow_undone=True)
        expect(stop and "library first" in stop and not ported, f"--allow-undone-library does not pass a release: {stop}")
        rows, stop = resolve(repo, "1.21.4", {"owner/lib": repo}, lk_u, allow_undone=True)
        by = {r["module"]: r for r in rows}
        md = render_md("1.21.4", rows, stop)
        expect(stop is None and ported == ["com.example:lib"] and by["lib"].get("undone") == "no green Gate C"
               and "`--allow-undone-library`" in md and "com.example:lib" in md.split("Development only")[1],
               f"--allow-undone-library on a checkout: used, and DEPS.md says so: {stop} {md}")
        rows, stop = resolve(repo, "1.21.4", {"owner/lib": repo}, lk_p)
        by = {r["module"]: r for r in rows}
        expect(stop is None and "library 1.21.4 done: fake done" in by["lib"]["source"]
               and "Development only" not in render_md("1.21.4", rows, stop), f"a done library: used, and said so: {by['lib']}")
        # control: without a manifest every dependency is required
        (repo / ".github/port-install.json").unlink()
        rows, stop = resolve(repo, "1.21.4", {}, lk)
        expect(stop and "devmod" in stop, "no manifest: every dependency is required")
        # single-version fork: nothing to do, nothing written
        sv = pathlib.Path(td) / "sv"; sv.mkdir()
        (sv / "build.gradle").write_text(build, encoding="utf-8")
        r = cmd_resolve(argparse.Namespace(repo=str(sv), target="1.21.4", port_checkout=[], dry_run=False))
        expect(r == 2 and sorted(p.name for p in sv.iterdir()) == ["build.gradle"], "single-version fork untouched")
    # what "done" reads: GATES.md (green Gate C, for the target, at a commit) and REVIEW.md (complete when present)
    head = "Gates for `examplemod` at `abc1234` for Minecraft 1.21.4 (full environment) -- run by CI\n\n| check | result | detail |\n"
    gc = "| Gate C -- real client, `launch` | PASS | ok |\n"
    expect(gates_record(head + gc, "1.21.4")[0] and gates_record(head + gc, "1.21.4")[2] == "abc1234", "green Gate C: done")
    expect(not gates_record(head + "| Gate B -- server | PASS | ok |\n", "1.21.4")[0], "A/B only: not done")
    expect(not gates_record(head + gc + "| Gate C -- real client, `spawn` | FAIL | x |\n", "1.21.4")[0], "a FAIL row: not done")
    expect(not gates_record(head + gc, "1.21.5")[0] and not gates_record("", "1.21.4")[0], "another version / none: not done")
    expect(not gates_record(head.replace("abc1234", "abc1234 + uncommitted: a.java") + gc, "1.21.4")[0], "an edited tree: not done")
    rv_ok = ("- reviewer: a fresh agent\n- date: 2026-10-16\n- commit: abc1234\n\n| kind | file | verdict | note |\n"
             "|---|---|---|---|\n| stub | .multiversion/targets/1.21.4/java/A.java | ok | same effect, checked |\n")
    expect(open_stubs('[{"file": "a", "reviewed": null}, {"file": "b", "reviewed": "ok"}]') == 1 and open_stubs("") == 0,
           "open stubs counted")
    # a sibling checkout (a real git repo): judged by fork-port.py's own target_done() -- the one done list -- after
    # the jar check (GATES.md current at HEAD, edited tracked files and untracked .multiversion/ files included)
    with tempfile.TemporaryDirectory() as td:
        co = pathlib.Path(td)
        g = lambda *x: subprocess.run(["git", "-C", str(co), *x], capture_output=True, text=True, encoding="utf-8")
        g("init", "-q"); g("config", "user.email", "x@example.com"); g("config", "user.name", "x")
        t4 = co / ".multiversion/targets/1.21.4"; t4.mkdir(parents=True)
        (t4 / "target.properties").write_text("inherits=1.21.3\n", encoding="utf-8")
        (co / ".multiversion/targets/1.21.3").mkdir(parents=True)
        (co / ".multiversion/targets/1.21.3/target.properties").write_text("x=1\n", encoding="utf-8")
        (co / "src").mkdir(); (co / "src/A.java").write_text("class A {}\n", encoding="utf-8")
        g("add", "-A"); g("commit", "-qm", "port")
        sha = g("rev-parse", "--short", "HEAD").stdout.strip()
        expect(not library_done_checkout(co, "1.21.4")[0], "a checkout with no GATES.md: not done")
        (t4 / "GATES.md").write_text(head.replace("abc1234", sha) + gc, encoding="utf-8")
        g("add", "-A"); g("commit", "-qm", "gates")
        d0 = library_done_checkout(co, "1.21.4")
        expect(not d0[0] and "REVIEW.md" in d0[1],
               f"green Gate C but no REVIEW.md: not done (target_done refuses a missing review; no longer tolerated): {d0}")
        rv_co = rv_ok.split("\n\n|")[0].replace("abc1234", g("rev-parse", "--short", "HEAD").stdout.strip()) + \
            "\n\n| kind | file | verdict | note |\n|---|---|---|---|\n"
        (t4 / "REVIEW.md").write_text(rv_co, encoding="utf-8")
        d0 = library_done_checkout(co, "1.21.4")
        expect(not d0[0] and "TEST-ITEMS" in d0[1], f"no TEST-ITEMS.md: not done, as target_done says: {d0}")
        (t4 / "TEST-ITEMS.md").write_text("# Manual-test items\n\n- none\n", encoding="utf-8")
        g("add", "-A"); g("commit", "-qm", "review")
        d1 = library_done_checkout(co, "1.21.4")
        expect(d1[0], f"green Gate C, a complete review, the test items: done: {d1}")
        (t4 / "STUBS.json").write_text('[{"file": "x", "reviewed": null}]', encoding="utf-8")
        expect(not library_done_checkout(co, "1.21.4")[0], "an unreviewed stub signal: not done")
        (t4 / "STUBS.json").unlink()
        (t4 / "REVIEW.md").write_text(rv_co.replace("- reviewer: a fresh agent\n", ""), encoding="utf-8")
        expect(not library_done_checkout(co, "1.21.4")[0], "an incomplete REVIEW.md: not done")
        (t4 / "REVIEW.md").write_text(rv_co, encoding="utf-8")
        (t4 / "DEPS.md").write_text("# deps\n\n**Development only (`--allow-undone-library`):** com.example:x\n", encoding="utf-8")
        d0 = library_done_checkout(co, "1.21.4")
        expect(not d0[0] and "allow-undone-library" in d0[1],
               f"a library itself built against an undone library (DEPS.md \"Development only\"): not done: {d0}")
        (t4 / "DEPS.md").unlink()
        (co / ".multiversion/targets/1.21.3/java").mkdir(parents=True)
        (co / ".multiversion/targets/1.21.3/java/X.java").write_text("class X {}\n", encoding="utf-8")
        expect(not library_done_checkout(co, "1.21.4")[0], "an UNTRACKED overlay in the chain (built into the jar): not done")
        shutil.rmtree(co / ".multiversion/targets/1.21.3/java")
        expect(library_done_checkout(co, "1.21.4")[0], "back to done once the untracked overlay is gone")
        (co / "src/A.java").write_text("class A { int x; }\n", encoding="utf-8")
        expect(not library_done_checkout(co, "1.21.4")[0], "an edited tracked source since the gates: not done")
        g("commit", "-qam", "edit")
        expect(not library_done_checkout(co, "1.21.4")[0], "a committed source change since the gates: not done")
        g("reset", "-q", "--hard", "HEAD~1")
        (co / ".multiversion/targets/1.21.3/x.java").write_text("x", encoding="utf-8")
        g("add", "-A"); g("commit", "-qm", "parent overlay")
        expect(not library_done_checkout(co, "1.21.4")[0], "a change in an inherited target's dir: not done")
        g("reset", "-q", "--hard", "HEAD~1")
        (co / ".multiversion/targets/1.21.5").mkdir(parents=True)
        (co / ".multiversion/targets/1.21.5/x.java").write_text("x", encoding="utf-8")
        g("add", "-A"); g("commit", "-qm", "other target")
        expect(library_done_checkout(co, "1.21.4")[0], "a change in another target's dir: still done")
    # a sibling release: the same list read at the tag -- GATES.md, REVIEW.md rows against the overlay list of the
    # tag's tree (the GitHub tree API), the review commit against the tag, TEST-ITEMS.md, DEPS.md, the inventory
    # account, an allowed undone parent -- and the files changed from GitHub's compare API
    import urllib.error
    ovl = ".multiversion/targets/1.21.4/java/com/example/mod/A.java"
    rv_rel = ("- reviewer: a fresh agent\n- date: 2026-10-16\n- commit: def5678\n\n| kind | file | verdict | note |\n"
              f"|---|---|---|---|\n| overlay | {ovl} | ok | same effect, checked |\n")
    files = {".multiversion/targets/1.21.4/GATES.md": head + gc, ".multiversion/targets/1.21.4/target.properties": "x=1\n",
             ".multiversion/targets/1.21.4/REVIEW.md": rv_rel, ".multiversion/targets/1.21.4/TEST-ITEMS.md": "- none\n",
             ovl: "class A {}\n", "src/main/java/com/example/mod/A.java": "class A {}\n"}
    cmps = {"abc1234": {"status": "ahead", "files": [{"filename": "README.md"}]},
            "def5678": {"status": "ahead", "files": [{"filename": ".multiversion/targets/1.21.4/REVIEW.md"}]}}
    tree = {"truncated": False}
    seen = []
    blob = lambda text: hashlib.sha1(b"blob %d\0" % len(text.encode()) + text.encode()).hexdigest()   # a git blob id

    def fake(url, headers=None):
        seen.append(url)
        if "/compare/" in url:
            base = url.rsplit("/compare/", 1)[1].split("...", 1)[0]
            if base not in cmps:
                raise urllib.error.HTTPError(url, 404, "nf", None, None)
            return json.dumps(cmps[base]).encode()
        if "/git/trees/" in url:
            return json.dumps({**tree, "tree": [{"path": p, "type": "blob", "sha": blob(files[p])} for p in sorted(files)]}).encode()
        path = url.split("/tag9/", 1)[1]
        if path in files:
            return files[path].encode()
        raise urllib.error.HTTPError(url, 404, "nf", None, None)

    def rel_done(msg, want=True):
        r = library_done_release("owner/lib", "tag9", "1.21.4", fake)
        expect(r[0] == want, f"{msg}: {r}")
        return r
    rel_done("a release whose GATES.md is current at the tag, every overlay reviewed ok at an ancestor of the tag: done")
    expect(any(u.endswith("/compare/abc1234...tag9") for u in seen) and any("/git/trees/tag9" in u for u in seen),
           "the release check reads the compare API from GATES.md's commit and the tag's tree")
    cmps["abc1234"]["files"].append({"filename": "src/main/java/A.java"})
    rel_done("a release with a source change since the gates: not done", False)
    cmps["abc1234"].update(status="diverged", files=[])
    rel_done("GATES.md's commit not under the tag: not done", False)
    cmps["abc1234"]["status"] = "ahead"
    files[".multiversion/targets/1.21.4/STUBS.json"] = '[{"file": "x", "reviewed": null}]'
    rel_done("a release with an open stub: not done", False)
    del files[".multiversion/targets/1.21.4/STUBS.json"]
    files[".multiversion/targets/1.21.4/REVIEW.md"] = rv_rel.replace("| ok |", "| not-ok |")
    rel_done("a release with a not-ok REVIEW.md row: not done", False)
    del files[".multiversion/targets/1.21.4/REVIEW.md"]
    r = rel_done("a release with no REVIEW.md: not done (refused, not tolerated)", False)
    files[".multiversion/targets/1.21.4/REVIEW.md"] = rv_rel.replace(ovl, ".multiversion/targets/1.21.4/STUBS.json")
    r = rel_done("one ok row, but the tag's overlay has no row: not done", False)
    expect(ovl in r[1], f"the unrowed overlay is named: {r[1]}")
    files[".multiversion/targets/1.21.4/REVIEW.md"] = rv_rel.replace("def5678", "HEAD")
    rel_done("a review commit that is not a hex commit (HEAD never goes stale): not done", False)
    files[".multiversion/targets/1.21.4/REVIEW.md"] = rv_rel
    cmps["def5678"]["status"] = "diverged"
    rel_done("a review commit that is not an ancestor of the tag: not done", False)
    cmps["def5678"]["status"] = "identical"
    rel_done("a review commit equal to the tag: done")
    cmps["def5678"].update(status="ahead", files=[{"filename": "src/main/java/com/example/mod/A.java"}])
    rel_done("the authors' file an overlay replaces changed since the review: not done", False)
    cmps["def5678"]["files"] = [{"filename": ".multiversion/targets/1.21.5/java/B.java"}]
    rel_done("only another target changed since the review: done")
    del files[".multiversion/targets/1.21.4/TEST-ITEMS.md"]
    rel_done("a release with no TEST-ITEMS.md: not done", False)
    files[".multiversion/targets/1.21.4/TEST-ITEMS.md"] = "- none\n"
    files[".multiversion/targets/1.21.4/DEPS.md"] = "**Development only (`--allow-undone-library`):** x\n"
    rel_done("a release itself built against an undone library: not done", False)
    del files[".multiversion/targets/1.21.4/DEPS.md"]
    files[".multiversion/INVENTORY.json"] = "{}"
    rel_done("a layer with a behaviour inventory and no account at the tag: not done", False)
    files[".multiversion/targets/1.21.4/INVENTORY-CHECK.json"] = json.dumps({"target": "1.21.4", "unaccounted": ["x"]})
    rel_done("an account with unaccounted items: not done", False)
    files[".multiversion/targets/1.21.4/INVENTORY-CHECK.json"] = json.dumps({"target": "1.21.4", "unaccounted": []})
    rel_done("every inventory item accounted for at the tag: done")
    # the tag's resource overlays and renames.tsv need their rows too, with the digest a checkout's review_items gives
    egg = ".multiversion/targets/1.21.4/resources/assets/examplemod/items/egg.json"
    files[egg] = '{"model": {}}\n'; files[".multiversion/targets/1.21.4/renames.tsv"] = "a\tb\n"
    r = rel_done("a release with an unreviewed resource group and renames.tsv: not done", False)
    expect("no verdict: resources .multiversion/targets/1.21.4/resources/assets/examplemod/items @" in r[1]
           and "no verdict: renames .multiversion/targets/1.21.4/renames.tsv @" in r[1], f"both named: {r[1]}")
    fpm = _fork_port()
    with tempfile.TemporaryDirectory() as td:
        co = pathlib.Path(td); tdc = co / ".multiversion/targets/1.21.4"
        for p_ in (egg, ".multiversion/targets/1.21.4/renames.tsv"):
            (co / p_).parent.mkdir(parents=True, exist_ok=True); (co / p_).write_bytes(files[p_].encode())
        local = [x for x in fpm.review_items(co, tdc) if x[0] in ("resources", "renames")]
    files[".multiversion/targets/1.21.4/REVIEW.md"] = rv_rel + "".join(f"| {k} | {x} | ok | checked |\n" for k, x in local)
    rel_done("rows with a checkout's digests for the same bytes: done (one definition)")
    files[egg] = '{"model": {"tints": [1]}}\n'
    rel_done("a resource group changed at the tag: its old row does not count: not done", False)
    del files[egg]; del files[".multiversion/targets/1.21.4/renames.tsv"]
    files[".multiversion/targets/1.21.4/REVIEW.md"] = rv_rel
    files[".multiversion/targets/1.21.4/target.properties"] = "x=1\nallowed_undone_parent=1.21.3 (development)\n"
    files[".multiversion/targets/1.21.3/target.properties"] = "x=1\n"
    rel_done("a parent added with --allow-undone-parent that is still not done at the tag: not done", False)
    files[".multiversion/targets/1.21.4/target.properties"] = "x=1\n"
    tree["truncated"] = True
    rel_done("a truncated tree (the overlay list cannot be read whole): not done", False)
    tree["truncated"] = False
    del files[".multiversion/targets/1.21.4/GATES.md"]
    rel_done("a release with no GATES.md: not done", False)
    # a released sibling jar is pinned in the block, and `fetch` installs it only when the checksum matches
    blk2 = render_block([{"group": "com.example", "module": "lib", "to": "com.example:lib:1.0-mc1.21.4",
                          "pin": "https://github.com/owner/lib/releases/download/lib-1.0-mc1.21.1/lib-1.0_a-mc1.21.4.jar " + "a" * 64}])
    expect("port_jar.com.example\\:lib=https://github.com/owner/lib/releases/download/lib-1.0-mc1.21.1/lib-1.0_a-mc1.21.4.jar "
           + "a" * 64 in blk2, f"pin line: {blk2}")
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td); t = repo / ".multiversion/targets/1.21.4"; t.mkdir(parents=True)
        payload = b"jar bytes"
        good = hashlib.sha256(payload).hexdigest()
        (t / "target.properties").write_text(blk2.replace("a" * 64, good), encoding="utf-8")
        home, got = os.environ.get("HOME"), []
        profile = os.environ.get("USERPROFILE")   # Path.home() reads USERPROFILE on Windows, HOME elsewhere
        os.environ["HOME"] = os.environ["USERPROFILE"] = td
        real = globals()["_get"]
        globals()["_get"] = lambda url, headers=None: (got.append(url), payload)[1]
        try:
            rc = cmd_fetch(argparse.Namespace(repo=str(repo), target="1.21.4"))
            jar = pathlib.Path(td) / ".m2/repository/com/example/lib/1.0-mc1.21.4/lib-1.0-mc1.21.4.jar"
            expect(rc == 0 and jar.read_bytes() == payload and len(got) == 1, f"fetch installs a pinned jar ({rc})")
            (t / "target.properties").write_text(blk2, encoding="utf-8")       # the pin's sha256 is "aaa..."
            expect(cmd_fetch(argparse.Namespace(repo=str(repo), target="1.21.4")) == 1, "fetch refuses a checksum mismatch")
            (t / "target.properties").write_text("x=1\n", encoding="utf-8")
            expect(cmd_fetch(argparse.Namespace(repo=str(repo), target="1.21.4")) == 0, "no pin: fetch is a no-op")
            # a dependent fork (its port-ci.yml installs the sibling's release): a sibling substitute resolved from a
            # local checkout has no pin, and CI cannot build it -- fetch fails naming the re-resolve; pinned, it passes;
            # an unpinned NON-sibling substitute (a CurseForge build) is not fetch's business
            (repo / ".github/workflows").mkdir(parents=True)
            (repo / ".github/workflows/port-ci.yml").write_text(wf, encoding="utf-8")
            unp = render_block([{"group": "com.example", "module": "lib", "to": "com.example:lib:1.0-mc1.21.4"},
                                {"group": "curse.maven", "module": "somelib-111", "to": "curse.maven:somelib-111:5"}])
            (t / "target.properties").write_text(unp, encoding="utf-8")
            import contextlib, io
            buf, n0 = io.StringIO(), len(got)
            with contextlib.redirect_stdout(buf):
                rc = cmd_fetch(argparse.Namespace(repo=str(repo), target="1.21.4"))
            expect(rc == 1 and "--port-release owner/lib@" in buf.getvalue() and len(got) == n0,
                   f"an unpinned sibling substitute fails fetch, naming --port-release ({rc}: {buf.getvalue()})")
            expect(sibling_substitutes(repo, "1.21.4") == {"com.example:lib": "com.example:lib:1.0-mc1.21.4"},
                   f"sibling_substitutes: {sibling_substitutes(repo, '1.21.4')}")
            (t / "target.properties").write_text(render_block([{"group": "curse.maven", "module": "somelib-111",
                                                                "to": "curse.maven:somelib-111:5"}]), encoding="utf-8")
            expect(cmd_fetch(argparse.Namespace(repo=str(repo), target="1.21.4")) == 0, "a non-sibling substitute needs no pin")
            (t / "target.properties").write_text(blk2.replace("a" * 64, good), encoding="utf-8")
            expect(cmd_fetch(argparse.Namespace(repo=str(repo), target="1.21.4")) == 0 and len(got) == n0,
                   "a pinned sibling: fetch passes (already in mavenLocal, not fetched again)")
            # the shipped-jar gate boots the sibling's per-target jar beside the built one (ci-gates --mc --shipped)
            expect(sibling_jars(repo, "1.21.4") == [str(jar)], f"sibling_jars: {sibling_jars(repo, '1.21.4')}")
            expect(sibling_jars(repo, "1.21.5") == [], "no block for the target: no sibling jars")
            # resolve keeping a pin installs it the same way: verified download, none when mavenLocal already has it
            jar.unlink()
            ok1 = install_pin("com.example:lib", "1.0-mc1.21.4", "https://github.com/o/r/releases/download/t/x.jar " + good)
            ok2 = install_pin("com.example:lib", "1.0-mc1.21.4", "https://github.com/o/r/releases/download/t/x.jar " + good)
            jar.unlink()
            bad_ = install_pin("com.example:lib", "1.0-mc1.21.4", "https://github.com/o/r/releases/download/t/x.jar " + "a" * 64)
            expect(ok1[0] and ok2[0] and "already in mavenLocal" in ok2[1] and len(got) == 4 and not bad_[0]
                   and not jar.exists(), f"a kept pin installs sha256-checked, once; a mismatch installs nothing ({ok1} {ok2} {bad_} {got})")
        finally:
            globals()["_get"] = real
            if home is not None:
                os.environ["HOME"] = home
            if profile is not None:
                os.environ["USERPROFILE"] = profile
            else:
                os.environ.pop("USERPROFILE", None)
    # a sibling checkout another run holds is never built in: its existing jar is taken (and said so), none -> missing;
    # free, it is built under its lock, which is gone afterwards
    with tempfile.TemporaryDirectory() as td:
        home = os.environ.get("HOME")
        profile = os.environ.get("USERPROFILE")   # Path.home() reads USERPROFILE on Windows, HOME elsewhere
        os.environ["HOME"] = os.environ["USERPROFILE"] = td
        holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            co = pathlib.Path(td) / "sib"
            (co / ".multiversion/targets/1.21.4").mkdir(parents=True)
            (co / ".multiversion/targets/1.21.4/target.properties").write_text("x=1\n", encoding="utf-8")
            (co / ".multiversion/build.sh").write_text("mkdir -p build/mc$1/libs && echo built > build/mc$1/libs/lib-mc$1.jar "
                                                       "&& echo ran >> build/ran\n", encoding="utf-8")
            spec = {"repo": "o/sib", "asset": "lib.jar", "version": "1.0"}
            (co / "build").mkdir()
            (co / "build/.fork-port.lock").write_text(json.dumps({"pid": holder.pid, "tool": "fork-port.py"}), encoding="utf-8")
            r = resolve_port("com.example:lib", spec, "1.21.4", {"o/sib": co})
            expect("missing" in r and "busy" in r["missing"] and not (co / "build/ran").exists(),
                   f"a busy sibling with no jar is not built in: {r}")
            (co / "build/mc1.21.4/libs").mkdir(parents=True)
            (co / "build/mc1.21.4/libs/lib-mc1.21.4.jar").write_text("old", encoding="utf-8")
            r = resolve_port("com.example:lib", spec, "1.21.4", {"o/sib": co})
            expect(r.get("to") == "com.example:lib:1.0-mc1.21.4" and "NOT rebuilt" in r.get("source", "")
                   and not (co / "build/ran").exists(), f"a busy sibling's existing jar is taken, unbuilt: {r}")
            holder.kill(); holder.wait()
            r = resolve_port("com.example:lib", spec, "1.21.4", {"o/sib": co})
            expect(r.get("to") and "NOT rebuilt" not in r.get("source", "") and (co / "build/ran").exists()
                   and not (co / "build/.fork-port.lock").exists(), f"a free sibling is built under its lock, released: {r}")
        finally:
            holder.kill()
            if home is not None:
                os.environ["HOME"] = home
            if profile is not None:
                os.environ["USERPROFILE"] = profile
            else:
                os.environ.pop("USERPROFILE", None)
    if bad:
        print("layer-deps self-check: FAIL\n  " + "\n  ".join(bad))
        return 1
    print("layer-deps self-check: OK")
    return 0


PORT_JAR = re.compile(r"(?m)^port_jar\.(.+?)=(https://github\.com/[\w.-]+/[\w.-]+/releases/download/\S+\.jar) ([0-9a-f]{64})\s*$")


SUBSTITUTE = re.compile(r"(?m)^substitute\.(.+?)=(\S+)\s*$")


def sibling_substitutes(repo, target):
    """{group:module: substitute coordinate} for each sibling port the target's layer-deps block swaps to that port's
    per-target jar: a coordinate the fork's port-ci.yml installs from a sibling's release (`--dep-jar`), substituted with
    version <v>-mc<target> (resolve_port's form). A fork with no sibling port, or a target with no block: {}."""
    repo = pathlib.Path(repo)
    tp = props_file(repo, target)
    text = tp.read_text(encoding="utf-8") if tp.is_file() else ""
    if BEGIN not in text or END not in text:
        return {}
    wf = repo / ".github/workflows/port-ci.yml"
    ports = port_deps(wf.read_text(encoding="utf-8") if wf.is_file() else "")
    out = {}
    for k, coord in SUBSTITUTE.findall(text[text.index(BEGIN):text.index(END)]):
        ga = k.replace("\\:", ":")
        if ga in ports and coord == f"{ga}:{ports[ga]['version']}-mc{target}":
            out[ga] = coord
    return out


def sibling_jars(repo, target, home=None):
    """The sibling ports' per-target jars in mavenLocal (what `fetch`, or a local resolve, installed) for the target's
    sibling substitutes: what a layer target's shipped-jar gate boots beside the built jar, as port-ci's `shipped jars`
    job passes the --dep-jar jars on the port branch. A jar not in mavenLocal is left out (shipped-gate then names the
    missing mod)."""
    home = pathlib.Path(home) if home else pathlib.Path.home()
    out = []
    for ga, coord in sibling_substitutes(repo, target).items():
        g, a, ver = coord.split(":", 2)
        jar = home / ".m2/repository" / g.replace(".", "/") / a / ver / f"{a}-{ver}.jar"
        if jar.is_file():
            out.append(str(jar))
    return out


def cmd_fetch(a):
    """CI: install every sibling-port jar the target's block pins (port_jar.<g:a>=<url> <sha256>) into ~/.m2, checked
    against its sha256, under the version its substitute. line names. Needs no CurseForge or Modrinth access; a
    target with no sibling substitute is a no-op. A sibling substitute with NO pin (resolved from a local
    --port-checkout) fails here, naming the re-resolve: CI has no checkout to build it from, and the build would
    otherwise stop later on an unresolvable dependency with no word of why. -> 0, or 1 on a checksum mismatch, a failed
    download or an unpinned sibling."""
    tp = props_file(a.repo, a.target)
    text = tp.read_text(encoding="utf-8") if tp.is_file() else ""
    pins = PORT_JAR.findall(text)
    pinned = {k.replace("\\:", ":") for k, _u, _s in pins}
    wf = pathlib.Path(a.repo) / ".github/workflows/port-ci.yml"
    ports = port_deps(wf.read_text(encoding="utf-8") if wf.is_file() else "")
    unpinned = [(ga, c) for ga, c in sibling_substitutes(a.repo, a.target).items() if ga not in pinned]
    for ga, coord in unpinned:
        print(f"layer-deps: fetch {a.target}: {ga} is substituted with {coord} (the sibling port's {a.target} jar) but "
              f"nothing pins where CI gets it (it was resolved from a local checkout). Re-resolve against the sibling's "
              f"layer release and commit the target: tools/layer-deps.py resolve --repo . --target {a.target} "
              f"--port-release {ports[ga]['repo']}@<its layer-release tag>")
    if unpinned:
        return 1
    for k, url, sha in pins:
        ga = k.replace("\\:", ":")
        m = re.search(r"(?m)^substitute\." + re.escape(k) + r"=" + re.escape(ga) + r":(\S+)\s*$", text)
        if not m:
            print(f"layer-deps: fetch {a.target}: {ga} is pinned but has no substitute line"); return 1
        ok, msg = install_pin(ga, m.group(1), f"{url} {sha}")
        print(f"layer-deps: fetch {a.target}: {msg}")
        if not ok:
            return 1
    if not pins:
        print(f"layer-deps: fetch {a.target}: nothing pinned")
    return 0


def cmd_library_done(a):
    """Exit 0 when the sibling's target is done, 18 when not, 2 on bad input: what fork-port.py (or a person) asks
    before a dependent's target starts."""
    if bool(a.port_checkout) == bool(a.port_release):
        print("layer-deps: library-done needs one of --port-checkout owner/repo=DIR or --port-release owner/repo@TAG")
        return 2
    if a.port_checkout:
        repo, _, d = a.port_checkout.partition("=")
        if not d:
            print(f"layer-deps: --port-checkout must be owner/repo=DIR, got {a.port_checkout!r}"); return 2
        co = pathlib.Path(d).expanduser().resolve()
    else:
        repo, _, co = a.port_release.partition("@")
        if not co:
            print(f"layer-deps: --port-release must be owner/repo@TAG, got {a.port_release!r}"); return 2
    done, why, notes = library_done(repo, co, a.target)
    print(f"layer-deps: library {repo} {a.target}: {'DONE' if done else 'NOT DONE'}: {why}")
    for n in notes:
        print(f"layer-deps: library {repo} {a.target}: note: {n}")
    return 0 if done else EXIT_UNDONE


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-check", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("resolve")
    r.add_argument("--repo", required=True)
    r.add_argument("--target", required=True)
    r.add_argument("--port-checkout", action="append", default=[], help="owner/repo=DIR: a sibling layer fork's checkout")
    r.add_argument("--port-release", action="append", default=[],
                   help="owner/repo@TAG: a sibling layer fork's release holding <asset>-mc<t>.jar; pinned (url + sha256) for CI")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--allow-undone-library", action="store_true",
                   help="development only: build against a --port-checkout sibling whose target is not done (recorded in DEPS.md)")
    ld = sub.add_parser("library-done", help="is a sibling library's target done? exit 0 yes, 18 no")
    ld.add_argument("--target", required=True)
    ld.add_argument("--port-checkout", help="owner/repo=DIR")
    ld.add_argument("--port-release", help="owner/repo@TAG")
    f = sub.add_parser("fetch")
    f.add_argument("--repo", required=True)
    f.add_argument("--target", required=True)
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if a.cmd == "resolve":
        return cmd_resolve(a)
    if a.cmd == "fetch":
        return cmd_fetch(a)
    if a.cmd == "library-done":
        return cmd_library_done(a)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
