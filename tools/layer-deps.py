#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Give a fork-layer target its own build of every mod the fork depends on (issue #77).

    python3 tools/layer-deps.py resolve --repo DIR --target 1.21.4 [--port-checkout owner/repo=DIR ...]
                                [--port-release owner/repo@TAG ...] [--allow-undone-library] [--dry-run]
                                [--bump DEP[=VERSION] ...] [--bump-all]
    python3 tools/layer-deps.py updates --repo DIR [--target MC] [--json]
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
                (`--dep-jar`). Its layer writes one jar per target (`<asset>-mc<t>.jar`); a version-branch
                library's release (a tag ending -mc<t>) holds the jars as its build names them, often per
                variant (`<name>-<ver>_mr.jar`): the asset is read from the release's own asset list, the one of
                the pinned asset's name and variant; with
                --port-checkout owner/repo=DIR it is taken from DIR/build/mc<t>/libs (built with that checkout's
                .multiversion/build.sh <t> assemble when missing) and installed into ~/.m2 as version <v>-mc<t>.
  anything else (a `${...}` version, a maven the authors run) is left alone: the target's prop.* lines carry it.
  Newest release first, then beta, then alpha; the channel is recorded.

A PIN IS KEPT; A BUMP IS EXPLICIT (every kind of fork alike: a deliberate change to single-version forks, decided by
the maintainer). A CurseForge/Modrinth dependency the block already pins (substitute.<g:m>=...) is kept exactly as it is
by every later resolve -- fork-port.py's setup, a cascade's, a person's -- with no lookup; only a dependency with no pin
yet (a first resolve) is looked up. `--bump DEP[=VERSION]` (DEP a group:module, a module or a CurseForge slug; VERSION a
file/version id or a whole group:module:version) moves one: to VERSION, else to the newest compatible build;
`--bump-all` moves every one. On a single-version fork (or a version-branch fork's port branch) the pins are
build.gradle's own coordinates and `resolve --bump` rewrites that coordinate in build.gradle, or the gradle.properties
key its version reads, and nothing else; without --bump there is nothing to resolve there (exit 2, as before).
`updates` lists, per Minecraft version the checkout builds, each pinned dependency with a newer compatible build -- what
a bump would take -- with the reason to look first (an alpha or beta, a major-version jump, a build that drops a
Minecraft version the fork builds, a CurseForge pin found on Modrinth only); the weekly plan (tools/weekly-plan.py deps)
lists the same per fork as ticked boxes, and the round applies the ticked ones once with --bump.

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
  A version-branch library's PRE-RELEASE whose version is not done is still taken when its gates are green at the tag
  (GATES.md: a green Gate C at a commit the tag carries): the library rule waits for a dependent's Gate C against the
  library's jar, so a dependent's pre-release may pin it ("Don't exceed your dependency"). DEPS.md marks it
  ("Library pre-release (not done)") and the dependent's version is then not done -- no full Release, no promotion --
  until the library's is and the pin is re-resolved. A full Release, or a pre-release with red gates, is refused.

VERSION BRANCHES (tools/fork-branches.py). On a fork of stacked version branches the same commands run on the checkout
of branch neoforge-<t>: the block above goes into .port/<t>/branch.properties (.port/branch.gradle applies it) and
DEPS.md into .port/<t>/. A sibling library that is a version-branch fork releases each version from its own branch
(Port CI's tag <mod>-<ver>-mc<t>); --port-release takes the jar from that release, pinned by sha256, and the library's
done list is tools/fork-branches.py's, run on the tag's tree with the library's history (a bare clone under
$MMVU_CACHE_DIR/git/, default ~/.mmvu/cache/git/), so the release and a checkout answer alike; the library rule's own reason (no dependent recorded
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
        if r.get("pin") and r.get("prerelease"):   # pinned to a library pre-release that is not done: kept with the pin
            lines.append(f"{PRERELEASE_KEY}{key(ga)}=" + " ".join(str(r["prerelease"]).split()))
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
    pre = [r for r in rows if r.get("prerelease")]
    if pre and not undone:
        out.append("")
    for r in pre:
        out.append(f"{PRERELEASE_MARK} `{r['group']}:{r['module']}` is pinned to a library pre-release whose {target} is "
                   f"not done ({r['prerelease']}). This version may cut its own pre-release with it (the pair is what a "
                   f"person plays); it is not done, and is neither promoted nor published, until the library's {target} "
                   f"is done: then re-resolve with `--port-release`.")
    return "\n".join(out) + "\n"


PRERELEASE_MARK = "**Library pre-release (not done):**"      # tools/fork-branches.py target_done() reads it


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


def port_version(version, target):
    """The coordinate version a sibling's jar for `target` is installed under: <version>-mc<target>. A version that
    already ends in -mc<target> (a version branch's workflow pins the version's own release, `1.1-mc1.21.8`) is not
    suffixed again (it was: `1.1-mc1.21.8-mc1.21.8`)."""
    return version if version.endswith(f"-mc{target}") else f"{version}-mc{target}"


def is_version_tag(tag, target):
    """Is `tag` a version branch's release tag for `target`: <mod>-<ver>-mc<target>, or a re-cut of it
    (<mod>-<ver>-mc<target>-r<N>, Port CI's count-up when the plain tag exists at another commit)?"""
    return bool(re.search(r"-mc" + re.escape(target) + r"(?:-r\d+)?$", tag or ""))


def recut_number(tag):
    """The re-cut number of a release tag: N for <...>-r<N>, 1 for the plain tag (the first cut)."""
    m = re.search(r"-r(\d+)$", tag or "")
    return int(m.group(1)) if m else 1


SIDE_JAR = re.compile(r"-(?:sources|javadoc|api|dev|slim)\.jar$")


def asset_shape(name):
    """A jar's name with its versions blanked: `lib-1.1.9_mr.jar`, `lib-1.1.8_mr-mc1.21.8.jar` -> `lib-#_mr.jar`; what
    stays is the mod's name and the variant (`_mr`, `_cf`, `-fabric`)."""
    return re.sub(r"\d+(?:\.\d+)+(?:[-+][\w.]*\d[\w.]*)?", "#", re.sub(r"-mc\d+(?:\.\d+)*", "", name))


def pick_release_asset(names, want, target):
    """The release asset that stands for `want` (the asset the workflow pins, often of an older release) among a
    version branch's release `names`: the same name, or <stem>-mc<target>.jar, or the one jar of the same shape
    (asset_shape: the mod's name and the variant honoured, so `_cf` never stands in for `_mr`, nor another mod's jar for
    this one's). A lone jar of another shape is no match. -> (name, None) or (None, why)."""
    jars = [n for n in names if n.endswith(".jar") and not SIDE_JAR.search(n)]
    stem = want[:-4]
    for n in (want, f"{stem}-mc{target}.jar"):
        if n in jars:
            return n, None
    same = [n for n in jars if asset_shape(n) == asset_shape(want)]
    if len(same) == 1:
        return same[0], None
    return None, (f"no asset of the release stands for {want} ({'several' if same else 'none'} of the same name and "
                  f"variant among {', '.join(jars) or 'no jar'})")


def release_assets(repo, tag, get=None):
    """([(name, download url)], None) of a GitHub release (the REST API; GH_TOKEN/GITHUB_TOKEN when set), or (None, the
    error) -- the caller falls back to the names it can guess and reports this error when those fail too."""
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    try:
        rel = json.loads((get or _get)(f"https://api.github.com/repos/{repo}/releases/tags/{tag}",
                                       {"Authorization": f"Bearer {tok}"} if tok else None))
        return [(x["name"], x.get("browser_download_url") or f"https://github.com/{repo}/releases/download/{tag}/{x['name']}")
                for x in rel.get("assets", []) if x.get("name")], None
    except Exception as e:     # noqa: BLE001 -- said, not swallowed
        return None, f"cannot list {repo}'s release {tag} ({type(e).__name__}: {e})"


def resolve_port(ga, spec, target, checkouts):
    repo = spec["repo"]
    stem = spec["asset"][:-4]
    ver = port_version(spec["version"], target)
    co = checkouts.get(repo)
    list_err = None
    if isinstance(co, str) and is_version_tag(co, target):
        # a version branch's release (<mod>-<ver>-mc<t>): its jars are named as the build names them, often with a
        # variant (lib-1.1.9_mr.jar); read the release's actual assets rather than guessing a name
        assets, list_err = release_assets(repo, co)
        if assets is not None:
            name, why = pick_release_asset([n for n, _u in assets], spec["asset"], target)
            if not name:
                return {"missing": f"{repo}'s release {co}: {why}", "source": repo}
            url = dict(assets)[name]
            try:
                data = _get(url)
            except Exception as e:
                return {"missing": f"{repo}'s release {co}: cannot download {name} ({e})", "source": repo}
            install_m2(ga, ver, data)
            sha = hashlib.sha256(data).hexdigest()
            return {"to": f"{ga}:{ver}", "pin": f"{url} {sha}", "source": f"{repo} release {co}: {name} (sha256 {sha[:16]}…)"}
    if isinstance(co, str):         # --port-release owner/repo@tag: the sibling's released layer jar, pinned for CI
        url = f"https://github.com/{repo}/releases/download/{co}/{stem}-mc{target}.jar"
        try:
            data = _get(url)
        except Exception as e:
            # a version-branch library releases ONE version per tag (<mod>-<ver>-mc<t>), its jar named as its build
            # names it; only a tag of exactly that version may fall back to the plain name (a layer release's plain
            # jar is the base version's)
            if not is_version_tag(co, target):
                return {"missing": f"{repo}'s release {co} has no {stem}-mc{target}.jar ({e})", "source": repo}
            url = f"https://github.com/{repo}/releases/download/{co}/{spec['asset']}"
            try:
                data = _get(url)
            except Exception as e2:
                return {"missing": f"{repo}'s release {co} has neither {stem}-mc{target}.jar nor {spec['asset']} ({e2})"
                                   + (f"; and {list_err}" if list_err else ""), "source": repo}
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
NOT_JAR = {"GATES.md", "GATES-RED.md", "COST.md", "RESIDUE.md", "STUBS.json", "REVIEW.md", "TEST-ITEMS.md", "BEHAVIOUR.json", "DEPS.md",
           "DRIFT.md", "INVENTORY.json", "INVENTORY-CHECK.json", "KNOWN-ISSUES.json"}


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


ROOT_RECORDS = ("README.md",)       # by path from the repository root: a README under src/ is jar content


def _not_jar(f):
    return f.rsplit("/", 1)[-1] in NOT_JAR or f in ROOT_RECORDS


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
        if raw(f"{tdir}/GATES-RED.md") is not None:
            return False, (f"at release {tag}: the last full gate run of {target} was red ({tdir}/GATES-RED.md): its "
                           "older green GATES.md does not count"), []
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


CACHE_ENV = "MMVU_CACHE_DIR"                # where library clones are cached; default ~/.mmvu/cache


def cache_dir():
    """The cache directory: $MMVU_CACHE_DIR, else ~/.mmvu/cache (the home directory as Python finds it on each OS)."""
    return pathlib.Path(os.environ.get(CACHE_ENV) or pathlib.Path.home() / ".mmvu/cache")


def library_clone(repo):
    """A bare clone of `repo` under <cache>/git/ ($MMVU_CACHE_DIR, default ~/.mmvu/cache), fetched each call from the
    URL as it is now (a cached clone's origin is re-pointed when the URL changed): the library's history and tags,
    read without a checkout of anyone's."""
    url = os.environ.get(GIT_URL_ENV, "https://github.com/{repo}.git").format(repo=repo)
    d = cache_dir() / "git" / repo.replace("/", "__")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    if not (d / "HEAD").is_file():
        d.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["git", "clone", "-q", "--bare", url, str(d)], capture_output=True, text=True, encoding="utf-8",
                           env=env)
        if r.returncode:
            return None, f"cannot clone {url}: {r.stderr.strip()[-200:]}"
    else:
        subprocess.run(["git", "-C", str(d), "remote", "set-url", "origin", url], capture_output=True, text=True,
                       encoding="utf-8", env=env)
    r = subprocess.run(["git", "-C", str(d), "fetch", "-q", "--prune", "origin", "+refs/heads/*:refs/heads/*",
                        "+refs/tags/*:refs/tags/*"], capture_output=True, text=True, encoding="utf-8", env=env)
    if r.returncode:
        return None, f"cannot fetch {url}: {r.stderr.strip()[-200:]}"
    return d, None


def gates_at_tag(repo, tag, target):
    """The gates item of the done list (tools/fork-branches.py gates_problems: a green Gate C in .port/<t>/GATES.md at a
    commit the tag carries, nothing jar-relevant changed since) read at release `tag`. -> [] or the problems."""
    fb = _fb()
    d, err = library_clone(repo)
    if d is None:
        return [err]
    c = _git(d, "rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}").stdout.strip()
    if not c:
        return [f"{repo} has no tag {tag}"]
    with tempfile.TemporaryDirectory() as t:
        root = fb.export(d, c, pathlib.Path(t) / "tree")
        return fb.gates_problems(d, root, target, c, False, fb.root_files(d, root, False))


def library_prerelease(repo, tag, target, get=None, gates=None):
    """May a dependent's PRE-RELEASE pin this library release although the library's version is not done? Yes when the
    release is a version branch's, a GitHub pre-release, and its gates are green at the tag (gates_at_tag). The library
    rule waits for a dependent's Gate C on the library's jar, and that Gate C needs the jar pinned first: this is how the
    pair is built and played ("Don't exceed your dependency"); done, a full Release and promotion still need the
    library's version done. -> (ok, why)."""
    import urllib.error
    get = get or _get
    if not branch_release(repo, tag, target, get):
        return False, f"{tag} is not a version branch's release"
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    try:
        rel = json.loads(get(f"https://api.github.com/repos/{repo}/releases/tags/{tag}",
                             {"Authorization": f"Bearer {tok}"} if tok else None))
    except (urllib.error.URLError, OSError, ValueError) as e:
        return False, f"cannot read {repo}'s release {tag} ({e})"
    if not rel.get("prerelease"):
        return False, f"{tag} is a full Release: a full Release of a library version needs that version done"
    why = (gates or gates_at_tag)(repo, tag, target)
    if why:
        return False, f"{tag} is a pre-release, but its gates are not green: {_reasons(why)}"
    return True, f"{tag} is a pre-release with green gates (GATES.md at the tag)"


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


PRERELEASE_KEY = "port_jar_prerelease."     # beside port_jar.<g:a>: that pin is a library pre-release not done yet


def resolve(repo, target, checkouts, lookups=None, dry_run=False, pins=None, allow_undone=False, prereleases=None,
            kept=None, bumps=None):
    """(rows, stop). lookups overrides the network (self-check). dry_run: a sibling checkout is not built or installed
    (its row says what would be). pins: {group:module: (substitute coord, port_jar value)} the target's block already
    holds -- a released sibling's pin is kept when no --port-checkout/--port-release names it this run.
    kept: {group:module: substitute coordinate} -- the registry (CurseForge/Modrinth) pins the block already holds, each
    kept as it is with no lookup: a resolve never moves a pinned dependency by itself. bumps: {group:module: coordinate
    or None} -- the explicit --bump/--bump-all: a coordinate is pinned as given, None re-picks the newest compatible
    build (the lookup a missing pin gets); "*" in it means every registry dependency.
    A sibling named by --port-checkout or --port-release is used only when its own `target` is done (library_done:
    a green Gate C recorded in its GATES.md at the commit used, then fork-port.py's done list); else its
    row is "refused" and the resolve stops -- unless allow_undone and it is a checkout, whose row then says
    "undone" (DEPS.md records the override)."""
    lk = {"curse": resolve_curse, "modrinth": resolve_modrinth, "port": resolve_port, "cf": cf_provider,
          "mr_id": mr_project_id, "pin_jar": install_pin, "library_done": library_done,
          "prerelease": library_prerelease, **(lookups or {})}
    pins = pins or {}
    build = (repo / "build.gradle").read_text(encoding="utf-8")
    mpath = repo / ".github/port-install.json"
    manifest = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else None
    roles = manifest_roles(manifest)
    wf = repo / ".github/workflows/port-ci.yml"
    ports = port_deps(wf.read_text(encoding="utf-8") if wf.exists() else "")
    kept, bumps = kept or {}, bumps or {}
    regs = [f"{d['group']}:{d['module']}" for d in gradle_deps(build) if d["kind"] in ("curse", "modrinth")]
    # CurseForge is asked only when some dependency is looked up (a resolve that keeps every pin needs no network)
    cf = lk["cf"]() if any(pin_choice(ga, kept, bumps)[0] in ("lookup", "bump") for ga in regs if ga not in ports) else None
    cache, ids, rows, missing_required, refused = {}, {}, [], [], []
    for d in gradle_deps(build):
        ga = f"{d['group']}:{d['module']}"
        row = dict(d)
        unnamed = "required" if manifest is None or d["conf"] in RUNTIME_CONFS else "build-only"
        if ga in ports:
            spec = ports[ga]
            row["role"] = roles.get(f"port:{spec['repo']}", "required")
            pin = pins.get(ga)
            ver = f"{ga}:{port_version(spec['version'], target)}"
            co = checkouts.get(spec["repo"])
            if co is not None:
                # library first: the dependent's target T builds only against a library T that is done
                done, why, notes = lk["library_done"](spec["repo"], co, target)
                pre = lk["prerelease"](spec["repo"], co, target) if not done and isinstance(co, str) else (False, "")
                was = (prereleases or {}).get(ga)
                if (not done and not pre[0] and was and isinstance(co, str) and pin and pin[0] == ver
                        and f"/releases/download/{co}/" in pin[1] and pre[1].startswith("cannot read")):
                    # the same release, already pinned as a library pre-release with green gates, re-named by a setup's
                    # re-resolve whose session cannot read GitHub's release API: the earlier check stands, said so
                    print(f"layer-deps: {target} {ga}: {co} kept as the library pre-release it was pinned as (the pin is "
                          f"unchanged; this run could not re-check it: {pre[1]})")
                    pre = (True, None)
                if not done and isinstance(co, pathlib.Path) and allow_undone:
                    row["undone"] = why
                elif not done and pre[0]:
                    # a dependent's pre-release may pin the library's pre-release; DEPS.md marks it, and the dependent's
                    # version is not done (no full Release, no promotion) until the library's is
                    row["prerelease"] = was if pre[1] is None else f"{pre[1]}; its {target} is not done: {why}"
                    row["library"] = f"{target} NOT done, pre-release pinned: {pre[1] or was}"
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
                if (prereleases or {}).get(ga):
                    # a kept pin keeps what it was: a library pre-release whose version was not done when it was pinned
                    row["prerelease"] = prereleases[ga]
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
        elif d["kind"] in ("curse", "modrinth") and pin_choice(ga, kept, bumps)[0] in ("keep", "to"):
            # pinned already (kept as it is), or bumped to an exact coordinate: no lookup, the pin is the build's
            how, to = pin_choice(ga, kept, bumps)
            was = deps_md_row(repo, target, ga) if how == "keep" else None
            if was and was[3] == to:
                # the row the earlier resolve wrote for this very build: its role and source, so DEPS.md stays as it was
                row.update({"role": was[1].strip("`") or unnamed, "to": to, "source": was[4]})
            else:
                row["role"] = registry_role(d, roles, unnamed, manifest, ids, lk)
                row.update({"to": to, "source": f"bumped explicitly (--bump): {kept.get(ga, d['coord'])} -> {to}" if how == "to"
                            else f"kept the pin (a resolve never moves a pinned dependency; `--bump {ga}` moves it)"})
        elif d["kind"] == "curse":
            row["role"] = roles.get(f"cf:{d['cf_project']}", unnamed)
            row.update(lk["curse"](d, target, cf, cache))
            if pin_choice(ga, kept, bumps)[0] == "bump":
                row["source"] = f"bumped (--bump): the newest compatible build; {row.get('source', '')}"
        elif d["kind"] == "modrinth":
            # the build names a slug or an id; the manifest's cdn URL carries the id
            role = roles.get(f"mr:{d['module']}")
            if role is None and manifest is not None:
                if d["module"] not in ids:
                    ids[d["module"]] = lk["mr_id"](d["module"])
                role = roles.get(f"mr:{ids[d['module']]}") if ids[d["module"]] else None
            row["role"] = role or unnamed
            row.update(lk["modrinth"](d["module"], target, cache))
            if pin_choice(ga, kept, bumps)[0] == "bump":
                row["source"] = f"bumped (--bump): the newest compatible build; {row.get('source', '')}"
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


def pin_choice(ga, kept, bumps):
    """What a resolve does with registry dependency `ga`: ("to", coord) bumped to an exact coordinate; ("bump", None)
    bumped to the newest compatible build (--bump GA, --bump-all); ("keep", coord) pinned already, kept as it is;
    ("lookup", None) not pinned yet: the newest compatible build, as a first resolve picks it."""
    if ga in bumps:
        return ("to", bumps[ga]) if bumps[ga] else ("bump", None)
    if "*" in bumps:
        return "bump", None
    if ga in kept:
        return "keep", kept[ga]
    return "lookup", None


def registry_role(d, roles, unnamed, manifest, ids, lk):
    """A CurseForge/Modrinth dependency's role from port-install.json (a Modrinth slug matched through its id)."""
    if d["kind"] == "curse":
        return roles.get(f"cf:{d['cf_project']}", unnamed)
    role = roles.get(f"mr:{d['module']}")
    if role is None and manifest is not None:
        if d["module"] not in ids:
            ids[d["module"]] = lk["mr_id"](d["module"])
        role = roles.get(f"mr:{ids[d['module']]}") if ids[d["module"]] else None
    return role or unnamed


def deps_md_row(repo, target, ga):
    """The cells [dependency, role, base pin, for this target, from] of `ga`'s row in the target's DEPS.md, or None: a
    kept pin takes its role and source from it, so a re-resolve that keeps every pin leaves DEPS.md as it was (and asks
    nothing of the network)."""
    md = target_dir(repo, target) / "DEPS.md"
    for line in (md.read_text(encoding="utf-8").splitlines() if md.is_file() else []):
        cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
        if len(cells) == 5 and cells[0] == f"`{ga}`":
            return cells
    return None


def current_registry_pins(repo, target):
    """{group:module: substitute coordinate} for each registry pin (a substitute. line with no port_jar pin: a CurseForge
    or Modrinth build) the target's layer-deps block holds."""
    tp = props_file(repo, target)
    text = tp.read_text(encoding="utf-8") if tp.is_file() else ""
    if BEGIN not in text or END not in text:
        return {}
    blk = text[text.index(BEGIN):text.index(END)]
    sibling = {k.replace("\\:", ":") for k in re.findall(r"(?m)^port_jar(?:_dev)?\.(.+?)=", blk)}
    return {k.replace("\\:", ":"): v for k, v in SUBSTITUTE.findall(blk) if k.replace("\\:", ":") not in sibling}


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


def current_prereleases(repo, target):
    """{group:module: why} for each pin of the block marked as a library pre-release (PRERELEASE_KEY); a pin the block
    does not mark but DEPS.md does (a block written before the key existed, or by hand) is taken from DEPS.md's
    "Library pre-release" line -- a re-resolve that keeps the pin keeps the mark (weekly round 2026-10-19: each
    fork-port.py setup's re-resolve dropped it, and the dependent read done)."""
    tp = props_file(repo, target)
    text = tp.read_text(encoding="utf-8") if tp.is_file() else ""
    if BEGIN not in text or END not in text:
        return {}
    got = {k.replace("\\:", ":"): v for k, v in
           re.findall(r"(?m)^" + re.escape(PRERELEASE_KEY) + r"(.+?)=(.*?)\s*$", text[text.index(BEGIN):text.index(END)])}
    md = target_dir(repo, target) / "DEPS.md"
    for ga, why in re.findall(re.escape(PRERELEASE_MARK) + r" `([^`]+)` is pinned to a library pre-release whose \S+ is "
                              r"not done \((.*?)\)\. This version may cut",
                              md.read_text(encoding="utf-8") if md.is_file() else ""):
        got.setdefault(ga, why)
    return got


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


def parse_bumps(specs, bump_all, deps):
    """--bump DEP[=COORD|=VERSION] (repeatable) and --bump-all -> {group:module: coordinate or None}, "*" for all. DEP is
    a group:module, a module or a CurseForge slug the build pins; VERSION alone keeps the pinned group:module. Raises
    ValueError naming a DEP the build does not pin."""
    out = {"*": None} if bump_all else {}
    for spec in specs or []:
        dep, _, ver = spec.partition("=")
        hit = [d for d in deps if dep in (f"{d['group']}:{d['module']}", d["module"])
               or (d["kind"] == "curse" and re.fullmatch(re.escape(dep) + r"-\d+", d["module"]))]
        if len(hit) != 1:
            raise ValueError(f"--bump {spec}: " + ("names more than one dependency" if hit else
                             "no CurseForge or Modrinth dependency of that name in build.gradle (one of: "
                             + ", ".join(f"{d['group']}:{d['module']}" for d in deps) + ")"))
        ga = f"{hit[0]['group']}:{hit[0]['module']}"
        out[ga] = (ver if ver.count(":") >= 2 else f"{ga}:{ver}") if ver else None
    return out


def cmd_resolve(a):
    repo = pathlib.Path(a.repo).resolve()
    if not (repo / ".multiversion/base.properties").exists() and not branch_target(repo, a.target):
        if getattr(a, "bump", None) or getattr(a, "bump_all", False):
            return cmd_bump_single(repo, a.target, a.bump, a.bump_all, a.dry_run)
        print("layer-deps: no .multiversion/ in the fork (a single-version fork has nothing to resolve; its pins are "
              "build.gradle's, moved only by --bump)")
        return 2
    build = (repo / "build.gradle").read_text(encoding="utf-8") if (repo / "build.gradle").is_file() else ""
    try:
        bumps = parse_bumps(getattr(a, "bump", []), getattr(a, "bump_all", False),
                            [d for d in gradle_deps(build) if d["kind"] in ("curse", "modrinth")])
    except ValueError as e:
        print(f"layer-deps: {e}"); return 2
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
                         allow_undone=getattr(a, "allow_undone_library", False),
                         prereleases=current_prereleases(repo, a.target), kept=current_registry_pins(repo, a.target),
                         bumps=bumps)
    for r in rows:
        state = ("-> " + r["to"]) if r.get("to") else ("DROPPED " + r["drop"]) if r.get("drop") else \
                ("REFUSED " + r["refused"]) if r.get("refused") else ("MISSING " + r.get("missing", ""))
        print(f"layer-deps: {a.target} {r['group']}:{r['module']} [{r['role']}] {state}")
        if r.get("library"):
            print(f"layer-deps: {a.target} {r['group']}:{r['module']}: library {r['library']}")
        if r.get("undone"):
            print(f"layer-deps: {a.target} {r['group']}:{r['module']}: LIBRARY NOT DONE, used anyway "
                  f"(--allow-undone-library, recorded in DEPS.md): {r['undone']}")
        if r.get("prerelease"):
            print(f"layer-deps: {a.target} {r['group']}:{r['module']}: LIBRARY PRE-RELEASE, not done: pinned for this "
                  f"version's pre-release only (recorded in DEPS.md; not done until the library's {a.target} is): "
                  f"{r['prerelease']}")
    wrote = False if a.dry_run else write(repo, a.target, rows, stop)
    n_sub, n_drop = sum(1 for r in rows if r.get("to")), sum(1 for r in rows if r.get("drop"))
    if stop:
        print(f"layer-deps: STOP {stop}")
        return EXIT_UNDONE if any(r.get("refused") for r in rows) else EXIT_REQUIRED
    print(f"layer-deps: {a.target}: {n_sub} substituted, {n_drop} dropped" + (", written" if wrote else ", not written"))
    return 0


# ---------------------------------------------------------------- explicit dependency updates (every kind of fork)
#
# A resolve never moves a pinned dependency (above); moving one is explicit: `resolve --bump DEP[=VERSION]` /
# `--bump-all`. The same rule holds on a single-version fork, whose pins are build.gradle's own coordinates (or the
# gradle.properties key a coordinate's version reads): there --bump rewrites that coordinate, and nothing else moves it.
# `updates` (and tools/weekly-plan.py's plan) lists, per Minecraft version a fork builds, each pinned CurseForge/Modrinth
# dependency that has a newer compatible build -- what a bump would take -- with the reason a person should look first:
# an alpha or beta, a major-version jump, a build that drops a Minecraft version the fork builds, or a provider switch.

def read_props_text(text):
    out = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "!")) and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def build_pins(build_text, props_text=""):
    """[{group, module, version, coord, kind, conf, src}] for each CurseForge/Modrinth coordinate build.gradle pins -- a
    literal one (src "literal"), or one whose version is a whole gradle.properties key (`${key}`/`$key`, src "prop:<key>",
    expanded). What a single-version fork builds against, and what --bump rewrites."""
    props = read_props_text(props_text)
    out, seen = [], set()
    for ln in strip_comments(build_text or "").splitlines():
        m = DEP_LINE.match(ln)
        if not m:
            continue
        parts = m.group(2).split(":")
        if len(parts) < 3 or parts[0] not in ("curse.maven", "maven.modrinth") or (parts[0], parts[1]) in seen:
            continue
        src = "literal"
        if "$" in parts[2]:
            km = re.fullmatch(r"\$\{?([\w.]+)\}?", parts[2])
            if not km or km.group(1) not in props or "$" in parts[0] + parts[1]:
                continue
            parts[2], src = props[km.group(1)], f"prop:{km.group(1)}"
        seen.add((parts[0], parts[1]))
        d = {"conf": m.group(1), "group": parts[0], "module": parts[1], "version": parts[2],
             "coord": ":".join(parts[:3]), "kind": "curse" if parts[0] == "curse.maven" else "modrinth", "src": src,
             "raw": m.group(2)}
        if d["kind"] == "curse":
            cm = re.fullmatch(r"(.+)-(\d+)", parts[1])
            if not cm:
                continue
            d["cf_project"] = cm.group(2)
        out.append(d)
    return out


def version_pins(build_text, props_text, branch_props_text=""):
    """{group:module: coordinate} the build of one Minecraft version uses: build.gradle's registry pins, each replaced by
    the version's layer-deps block substitute (a version branch's branch.properties) and left out when the block excludes
    it; a sibling port's pins (port_jar.) are not registry pins."""
    pins = {f"{d['group']}:{d['module']}": d["coord"] for d in build_pins(build_text, props_text)}
    text = branch_props_text or ""
    if BEGIN in text and END in text:
        blk = text[text.index(BEGIN):text.index(END)]
        sibling = {k.replace("\\:", ":") for k in re.findall(r"(?m)^port_jar(?:_dev)?\.(.+?)=", blk)}
        for k, v in SUBSTITUTE.findall(blk):
            ga = k.replace("\\:", ":")
            if ga not in sibling and v.split(":")[0] in ("curse.maven", "maven.modrinth"):
                pins[ga] = v
        for k in re.findall(r"(?m)^exclude\.(.+?)=", blk):
            pins.pop(k.replace("\\:", ":"), None)
    return pins


def _major(name, mcs):
    """The first number of a build's own version (its name with the Minecraft versions and loader words taken out)."""
    s = name or ""
    for mc in sorted({x for v in mcs for x in (v, ".".join(v.split(".")[:2]))}, key=len, reverse=True):
        s = re.sub(r"(?<![\d.])" + re.escape(mc) + r"(?![\d])", " ", s)
    s = re.sub(r"(?i)neoforge|forge|fabric|quilt|\bmc", " ", s)
    m = re.search(r"(?<![\w.])v?(\d+)(?:\.\d+)+", s) or re.search(r"(?<![\w.])v?(\d+)(?![\w])", s)
    return int(m.group(1)) if m else None


def mr_version_of_sha1(sha1):
    """The Modrinth version whose file has this sha1, as mr_files() rows have it, or None."""
    try:
        v = json.loads(_get(f"{MODRINTH}/version_file/{sha1}?algorithm=sha1"))
    except Exception:
        return None
    return {"project": v.get("project_id"), "id": v["id"], "mcs": v.get("game_versions", []), "loaders": v.get("loaders", []),
            "channel": v.get("version_type"), "date": v.get("date_published"), "name": v.get("version_number")}


def update_candidate(coord, mc, lk=None, cache=None):
    """(current build, newer build, its coordinate, provider switch?) for a pinned registry coordinate at Minecraft `mc`:
    the newest compatible build published after the pinned one (release first, then beta, then alpha, as pick() picks);
    newer None when there is none. Raises LookupError when it cannot tell (the plan says "could not check")."""
    lk = {"mr_files": mr_files, "cf": cf_provider, "cf_files": cf_files, "mr_sha1": mr_version_of_sha1,
          "curse_jar": lambda m, v: _get(f"{CURSEMAVEN}/{m}/{v}/{m}-{v}.jar"), **(lk or {})}
    cache = {} if cache is None else cache
    g, m, v = (coord.split(":") + ["", ""])[:3]
    switch = False
    if g == "maven.modrinth":
        files = cache.get(("mr", m)) or lk["mr_files"](m)
        cache[("mr", m)] = files
        cur = next((f for f in files if v in (f.get("id"), f.get("name"))), None)
        style = "name" if cur and v == cur.get("name") and v != cur.get("id") else "id"
        mk = lambda f: f"maven.modrinth:{m}:{f[style]}"
    elif g == "curse.maven":
        pid = (re.fullmatch(r"(.+)-(\d+)", m) or [None, None, None])[2]
        if not pid:
            raise LookupError(f"{coord}: not a cursemaven coordinate")
        cf = cache.get("cf", False)
        if cf is False:
            cf = cache["cf"] = lk["cf"]()
        if cf is not None:
            files = cache.get(("cf", pid, mc)) or lk["cf_files"](cf, pid, mc)
            cache[("cf", pid, mc)] = files
            cur = next((f for f in files if f.get("id") == v), None)
            mk = lambda f: f"curse.maven:{m}:{f['id']}"
        else:
            # no CURSEFORGE_API_KEY (the plan workflow has no secrets): the pinned jar's sha1 on Modrinth, as resolve does
            cur = lk["mr_sha1"](hashlib.sha1(lk["curse_jar"](m, v)).hexdigest())
            if not cur:
                raise LookupError("the pinned CurseForge file is not on Modrinth and CURSEFORGE_API_KEY is not set")
            files = cache.get(("mr", cur["project"])) or lk["mr_files"](cur["project"])
            cache[("mr", cur["project"])] = files
            mk, switch = (lambda f, p=cur["project"]: f"maven.modrinth:{p}:{f['id']}"), True
    else:
        raise LookupError(f"{coord}: not a CurseForge or Modrinth coordinate")
    if cur is None:
        raise LookupError(f"the pinned build {v} is not among the project's builds")
    newer = [f for f in files if (f.get("date") or "") > (cur.get("date") or "") and f.get("id") != cur.get("id")]
    new = pick(newer, mc)
    return cur, new, (mk(new) if new else None), switch


SAFE_COORD = re.compile(r"^[A-Za-z0-9._+:-]+$")     # what a coordinate may hold to be written into a command (and a box)


def dependency_updates(pins_by_mc, lk=None):
    """{"rows": [{"id", "mc", "ga", "from", "to", "from_name", "to_name", "channel", "default", "why"}],
    "unchecked": [{"mc", "ga", "coord", "why"}], "checked": n} for {mc: {group:module: coordinate}}. A row is ticked
    (default) unless the newer build is an alpha or beta, a major-version jump, drops a Minecraft version the fork builds
    that the pinned build serves, or switches provider. A lookup that fails is listed unchecked, never raised."""
    built = sorted(pins_by_mc, key=lambda v: tuple(int(x) for x in re.findall(r"\d+", v)))
    rows, unchecked, n, cache = [], [], 0, {}
    for mc in built:
        for ga, coord in sorted(pins_by_mc[mc].items()):
            n += 1
            try:
                if not SAFE_COORD.match(coord) or not SAFE_COORD.match(ga):
                    raise LookupError("the pinned coordinate holds characters a command must not carry")
                cur, new, to, switch = update_candidate(coord, mc, lk, cache)
                if to is not None and not SAFE_COORD.match(to):
                    # a registry version string is the publisher's: `$(...)` or a backtick would run in the round's
                    # --bump command (with its push credentials), so it is never offered
                    raise LookupError("the newer build's version holds characters a command must not carry "
                                      "(only A-Z a-z 0-9 . _ + : - are taken): bump it by hand if it is right")
            except Exception as e:      # noqa: BLE001 -- fail soft: "could not check", the plan goes on
                unchecked.append({"mc": mc, "ga": ga, "coord": coord if SAFE_COORD.match(coord) else "",
                                  "why": str(e)[:200] or type(e).__name__})
                continue
            if not new:
                continue
            why = []
            if new.get("channel") in ("alpha", "beta"):
                why.append(f"a {new['channel']}")
            a, b = _major(cur.get("name"), cur.get("mcs", []) + new.get("mcs", [])), \
                _major(new.get("name"), cur.get("mcs", []) + new.get("mcs", []))
            if a is not None and b is not None and b > a:
                why.append(f"a major-version jump ({cur.get('name')} -> {new.get('name')})")
            gone = [v for v in built if v in cur.get("mcs", []) and v not in new.get("mcs", [])]
            if gone:
                why.append(f"drops Minecraft {', '.join(gone)}, which the fork builds and the pinned build serves")
            if switch:
                why.append("found on Modrinth by the CurseForge jar's sha1 (no CURSEFORGE_API_KEY): the bump switches the "
                           "pin to a Modrinth coordinate")
            rows.append({"id": f"D{len(rows) + 1}", "mc": mc, "ga": ga, "from": coord, "to": to,
                         "from_name": cur.get("name"), "to_name": new.get("name"), "channel": new.get("channel"),
                         "default": not why, "why": "; ".join(why)})
    return {"rows": rows, "unchecked": unchecked, "checked": n}


def bump_command(mc, ga, to, checkout="<checkout of neoforge-{mc}>"):
    """The one command that applies a bump, the same for every kind of fork."""
    import shlex
    return (f"python3 tools/layer-deps.py resolve --repo {shlex.quote(checkout.format(mc=mc))} --target {shlex.quote(mc)} "
            f"--bump {shlex.quote(f'{ga}={to}')}")


def _dep_code_lines(text):
    """[(index, code part)] of each line of `text` (line endings kept) that is a dependency declaration outside any
    comment: the part before a `//`, never a line inside a /* */ block."""
    out, block = [], False
    for i, ln in enumerate(text.splitlines(True)):
        code = ln
        if block:
            end = code.find("*/")
            if end < 0:
                continue
            code, block = code[end + 2:], False
        code = re.sub(r"/\*.*?\*/", "", code)
        if "/*" in code:
            code, block = code[:code.index("/*")], True
        code = re.sub(r"(^|\s)//.*$", "", code.rstrip("\r\n"))
        if DEP_LINE.match(code):
            out.append((i, code))
    return out


def replace_coordinate(text, old, new):
    """`text` with the quoted coordinate `old` replaced by `new` on the dependency lines outside comments only, and only
    as a whole quoted token (`lib:1.2` never rewrites `lib:1.2.5`); every other byte, line endings included, kept."""
    lines = text.splitlines(True)
    tok = re.compile(r"""(["'])""" + re.escape(old) + r"""(["'])""")
    for i, code in _dep_code_lines(text):
        cut = len(code)
        lines[i] = tok.sub(lambda m: m.group(1) + new + m.group(2), lines[i][:cut]) + lines[i][cut:]
    return "".join(lines)


def cmd_bump_single(repo, target, specs, bump_all, dry_run=False, lk=None, say=print):
    """--bump on a single-version fork (or a version-branch fork's port branch): its pins are build.gradle's. Each named
    dependency's coordinate is rewritten -- in build.gradle when literal, else the gradle.properties key its version
    reads -- to the given version or the newest compatible build (update_candidate); nothing else is touched. A bump
    that changes the coordinate's group or module where the version is a gradle.properties key is refused (edit it by
    hand). -> 0, or 2 on bad input or a failed lookup."""
    bg, gp = repo / "build.gradle", repo / "gradle.properties"
    # bytes in and out: a CRLF build stays CRLF
    build = bg.read_bytes().decode("utf-8") if bg.is_file() else ""
    props = gp.read_bytes().decode("utf-8") if gp.is_file() else ""
    mc = read_props_text(props).get("minecraft_version")
    if mc and mc != target:
        say(f"layer-deps: {repo} builds Minecraft {mc}, not {target}: run --bump on the checkout of neoforge-{target}")
        return 2
    deps = build_pins(build, props)
    try:
        bumps = parse_bumps(specs, bump_all, deps)
    except ValueError as e:
        say(f"layer-deps: {e}"); return 2
    todo = [d for d in deps if f"{d['group']}:{d['module']}" in bumps or "*" in bumps]
    new_build, new_props, did = build, props, 0
    for d in todo:
        ga = f"{d['group']}:{d['module']}"
        to = bumps.get(ga)
        if not to:
            try:
                _cur, new, to, switch = update_candidate(d["coord"], target, lk)
            except Exception as e:      # noqa: BLE001
                say(f"layer-deps: {target} {ga}: cannot look up a newer build ({e}); pass --bump {ga}=<version>"); return 2
            if not to:
                say(f"layer-deps: {target} {ga}: {d['coord']} is the newest compatible build, kept"); continue
            if switch:
                say(f"layer-deps: {target} {ga}: the newer build was found on Modrinth only ({to}); a single-version "
                    f"pin keeps its provider: pass --bump {ga}=<CurseForge file id> (CURSEFORGE_API_KEY finds it)")
                return 2
        if to == d["coord"]:
            say(f"layer-deps: {target} {ga}: already {to}"); continue
        if d["src"].startswith("prop:"):
            key = d["src"][5:]
            if to.rsplit(":", 1)[0] != ga:
                say(f"layer-deps: {target} {ga}: its version is gradle.properties' {key}, and {to} changes the "
                    "coordinate itself: edit build.gradle by hand")
                return 2
            ref = re.compile(r"\$\{?" + re.escape(key) + r"(?![\w.])\}?")
            users = [c for _i, c in _dep_code_lines(build) if ref.search(c)]
            if len(users) > 1:
                say(f"layer-deps: {target} {ga}: its version is gradle.properties' {key}, which {len(users)} dependency "
                    f"lines read: bumping it would move the others too -- edit them by hand")
                return 2
            new_props = re.sub(r"(?m)^([ \t]*" + re.escape(key) + r"[ \t]*=[ \t]*)" + re.escape(d["version"])
                               + r"([ \t]*)(?=\r?$)", lambda x: x.group(1) + to.rsplit(":", 1)[1] + x.group(2), new_props)
        else:
            new_build = replace_coordinate(new_build, d["coord"], to)
        did += 1
        say(f"layer-deps: {target} {ga}: {d['coord']} -> {to} (bumped explicitly, single-version build)")
    if did and not dry_run:
        if new_build != build:
            bg.write_bytes(new_build.encode("utf-8"))
        if new_props != props:
            gp.write_bytes(new_props.encode("utf-8"))
    say(f"layer-deps: {target}: {did} bumped" + (" (dry run, not written)" if dry_run and did else ""))
    return 0


def checkout_pins(repo, target=None):
    """{mc: {group:module: coordinate}} for the checkout `repo`: its own Minecraft version's pins (a version branch's
    block applied; a layer's targets, each with its target.properties block, too)."""
    repo = pathlib.Path(repo)
    rd = lambda p: (repo / p).read_text(encoding="utf-8") if (repo / p).is_file() else ""
    build, props = rd("build.gradle"), rd("gradle.properties")
    mc = target or read_props_text(props).get("minecraft_version") or "?"
    out = {mc: version_pins(build, props, rd(f".port/{mc}/branch.properties"))}
    for tp in sorted((repo / ".multiversion/targets").glob("*/target.properties")):
        out[tp.parent.name] = version_pins(build, props, tp.read_text(encoding="utf-8"))
    return out


def cmd_updates(a):
    res = dependency_updates(checkout_pins(pathlib.Path(a.repo), a.target))
    if a.json:
        print(json.dumps(res, indent=1))
        return 0
    for r in res["rows"]:
        print(f"layer-deps: update {r['mc']} {r['ga']}: {r['from']} -> {r['to']} ({r['from_name']} -> {r['to_name']}, "
              f"{r['channel']})" + (f" -- look first: {r['why']}" if r["why"] else "") + f"\n    {bump_command(r['mc'], r['ga'], r['to'], a.repo)}")
    for u in res["unchecked"]:
        print(f"layer-deps: could not check {u['mc']} {u['ga']} ({u['coord']}): {u['why']}")
    print(f"layer-deps: {res['checked']} pinned registry dependenc{'y' if res['checked'] == 1 else 'ies'} checked, "
          f"{len(res['rows'])} with a newer compatible build, {len(res['unchecked'])} could not be checked")
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
              "mr_id": lambda slug: None, "library_done": lambda r, co, t: (True, "fake done", []),
              "prerelease": lambda r, co, t: (False, "not a pre-release (fake)")}
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
        # a kept pin that was a library pre-release stays marked (review of #147: a later resolve that did not name the
        # library dropped the DEPS.md mark, and the dependent read done)
        rows, stop = resolve(repo, "1.21.4", {}, lk_p, pins={"com.example:lib": pin})
        rows[[r["module"] for r in rows].index("lib")]["prerelease"] = "lib-1.0-mc1.21.4 is a pre-release; not done"
        write(repo, "1.21.4", rows, stop)
        pre_ = current_prereleases(repo, "1.21.4")
        rows, stop = resolve(repo, "1.21.4", {}, lk_p, pins=current_pins(repo, "1.21.4"), prereleases=pre_)
        write(repo, "1.21.4", rows, stop)
        md_ = (repo / ".multiversion/targets/1.21.4/DEPS.md").read_text(encoding="utf-8")
        expect(pre_ == {"com.example:lib": "lib-1.0-mc1.21.4 is a pre-release; not done"} and PRERELEASE_MARK in md_
               and current_prereleases(repo, "1.21.4") == pre_ and not ported,
               f"a kept pre-release pin keeps its mark in the block and in DEPS.md: {pre_} {md_}")
        rows, stop = resolve(repo, "1.21.4", {}, lk_p, pins=current_pins(repo, "1.21.4"))
        write(repo, "1.21.4", rows, stop)
        expect(not current_prereleases(repo, "1.21.4"), "control: without the carried mark it is gone")
        # weekly round 2026-10-19: each fork-port.py setup re-resolved and the "Library pre-release" mark went. (1) a
        # block that does not carry the key while DEPS.md marks the pin: the mark is read from DEPS.md
        mark = "lib-1.0-mc1.21.4 is a pre-release with green gates (GATES.md at the tag); its 1.21.4 is not done: x"
        rows, stop = resolve(repo, "1.21.4", {}, lk_p, pins={"com.example:lib": pin}, prereleases={"com.example:lib": mark})
        write(repo, "1.21.4", rows, stop)
        tpx = props_file(repo, "1.21.4")
        tpx.write_text("".join(ln for ln in tpx.read_text(encoding="utf-8").splitlines(True)
                               if not ln.startswith(PRERELEASE_KEY)), encoding="utf-8")
        expect(current_prereleases(repo, "1.21.4") == {"com.example:lib": mark},
               f"a pre-release mark only DEPS.md carries is read back: {current_prereleases(repo, '1.21.4')}")
        # (2) the same release named again by --port-release while the release API cannot be read: kept, marked, said
        pin_x = ("com.example:lib:1.0-mc1.21.4", "https://github.com/owner/lib/releases/download/x/lib-1.0_a-mc1.21.4.jar " + "b" * 64)
        lk_x = dict(lk_p, port=lk["port"], library_done=lambda r, co, t: (False, "no green Gate C", []),
                    prerelease=lambda r, co, t: (False, f"cannot read {r}'s release {co} (HTTP Error 403: Forbidden)"))
        rows, stop = resolve(repo, "1.21.4", {"owner/lib": "x"}, lk_x, pins={"com.example:lib": pin_x},
                             prereleases={"com.example:lib": mark})
        lib_row = [r for r in rows if r["module"] == "lib"][0]
        expect(stop is None and lib_row.get("prerelease") == mark and not lib_row.get("refused"),
               f"the same pre-release re-named when the API cannot be read: kept with its mark: {stop} {lib_row}")
        for why_, pins_, pre_x, co_ in (("a full Release", {"com.example:lib": pin_x}, {"com.example:lib": mark}, "x"),
                                        ("another tag", {"com.example:lib": pin_x}, {"com.example:lib": mark}, "y"),
                                        ("never marked", {"com.example:lib": pin_x}, {}, "x")):
            lk_y = dict(lk_x, prerelease=lambda r, co, t, w=why_: (False, f"{co} is {w}") if w == "a full Release"
                        else (False, f"cannot read {r}'s release {co} (HTTP Error 403)"))
            rows, stop = resolve(repo, "1.21.4", {"owner/lib": co_}, lk_y, pins=pins_, prereleases=pre_x)
            expect(stop and "library first" in stop, f"control ({why_}): refused as before: {stop}")
        del pinned[1:]                           # the kept-pin installs above are not the dry-run case's
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
        # --port-release of a version branch's release (weekly-round dry run, 2026-10-18): the coordinate is not suffixed
        # twice, and the jar is the release's own asset of the pinned name and variant, not <asset>-mc<t>.jar
        expect(port_version("1.1-mc1.21.8", "1.21.8") == "1.1-mc1.21.8" and port_version("1.1", "1.21.8") == "1.1-mc1.21.8"
               and port_version("1.1-mc1.21.4", "1.21.8") == "1.1-mc1.21.4-mc1.21.8",
               "port_version: -mc<t> once, a layer's plain version suffixed as before")
        rel = ["examplelib-1.2_mr.jar", "examplelib-1.2_cf.jar", "examplelib-1.2-sources.jar"]
        expect(pick_release_asset(rel, "examplelib-1.1_mr.jar", "1.21.8")[0] == "examplelib-1.2_mr.jar"
               and pick_release_asset(rel, "examplelib-1.1_cf.jar", "1.21.8")[0] == "examplelib-1.2_cf.jar"
               and pick_release_asset(rel, "examplelib-1.1.jar", "1.21.8")[0] is None
               and pick_release_asset(["examplelib-1.2.jar"], "examplelib-1.1.jar", "1.21.8")[0] == "examplelib-1.2.jar"
               and pick_release_asset(["examplelib-1.2_cf.jar"], "examplelib-1.1_mr.jar", "1.21.8")[0] is None
               and pick_release_asset(["otherlib-9.0.jar"], "examplelib-1.1_mr.jar", "1.21.8")[0] is None
               and pick_release_asset(["examplelib-1.2-mc1.21.8.jar"], "examplelib-1.1.jar", "1.21.8")[0]
               == "examplelib-1.2-mc1.21.8.jar",
               "pick_release_asset: the variant honoured; a bare name with two variants is ambiguous, never a guess")
        g_, m2 = globals(), []
        saved = {k: g_[k] for k in ("_get", "install_m2")}
        api = json.dumps({"assets": [{"name": n, "browser_download_url": f"https://dl.example/{n}"} for n in rel]}).encode()
        try:
            g_["_get"] = lambda url, h=None: api if "/releases/tags/" in url else url.rsplit("/", 1)[1].encode()
            g_["install_m2"] = lambda ga, ver, data: m2.append((ga, ver, data))
            spec = {"repo": "owner/lib", "asset": "examplelib-1.1_cf.jar", "version": "1.1-mc1.21.8"}
            got = resolve_port("com.example:lib", spec, "1.21.8", {"owner/lib": "examplelib-1.2-mc1.21.8"})
        finally:
            g_.update(saved)
        boom = lambda url, h=None: (_ for _ in ()).throw(OSError("blocked by the proxy"))
        al, ae = release_assets("owner/lib", "x-mc1.21.8", boom)
        expect(al is None and "blocked by the proxy" in ae, f"release_assets says why it could not list: {ae}")
        try:
            g_["_get"] = boom
            miss = resolve_port("com.example:lib", spec, "1.21.8", {"owner/lib": "examplelib-1.2-mc1.21.8"})
        finally:
            g_.update(saved)
        expect("blocked by the proxy" in miss.get("missing", "") and "cannot list" in miss.get("missing", ""),
               f"a release that cannot be listed or fetched names the real error: {miss}")
        expect(got.get("to") == "com.example:lib:1.1-mc1.21.8" and got.get("pin", "").startswith("https://dl.example/examplelib-1.2_cf.jar ")
               and m2 == [("com.example:lib", "1.1-mc1.21.8", b"examplelib-1.2_cf.jar")],
               f"--port-release of a version branch's release: its _cf asset, one -mc suffix: {got} {m2}")
        # a re-cut release tag (<mod>-<ver>-mc<t>-r<N>, Port CI's count-up; weekly round 2026-10-19: `--port-release
        # owner/lib@lib-1.1.9-mc26.3-r3` was read as a layer release and failed) is the same version branch's release
        expect(is_version_tag("examplelib-1.2-mc1.21.8", "1.21.8") and is_version_tag("examplelib-1.2-mc1.21.8-r3", "1.21.8")
               and not is_version_tag("examplelib-1.2-mc1.21.8-r3", "1.21.80") and not is_version_tag("x-mc1.21.8-rc1", "1.21.8")
               and not is_version_tag("examplelib-1.2", "1.21.8") and recut_number("x-mc1.21.8") == 1
               and recut_number("x-mc1.21.8-r3") == 3, "is_version_tag / recut_number: -mc<t> with an optional -r<N>")
        m2.clear()
        try:
            g_["_get"] = lambda url, h=None: api if "/releases/tags/" in url else url.rsplit("/", 1)[1].encode()
            g_["install_m2"] = lambda ga, ver, data: m2.append((ga, ver, data))
            got3 = resolve_port("com.example:lib", spec, "1.21.8", {"owner/lib": "examplelib-1.2-mc1.21.8-r3"})
            asked3 = []
            g_["_get"] = lambda url, h=None: (asked3.append(url), (_ for _ in ()).throw(OSError("blocked"))
                                              if "/releases/tags/" in url or url.endswith("-mc1.21.8.jar") else b"jar")[1]
            got3b = resolve_port("com.example:lib", spec, "1.21.8", {"owner/lib": "examplelib-1.2-mc1.21.8-r3"})
        finally:
            g_.update(saved)
        expect(got3.get("to") == "com.example:lib:1.1-mc1.21.8" and got3.get("pin", "").startswith("https://dl.example/examplelib-1.2_cf.jar ")
               and "examplelib-1.2-mc1.21.8-r3" in got3.get("source", ""),
               f"--port-release of a re-cut tag: the version branch's release, its own asset: {got3}")
        expect(got3b.get("to") == "com.example:lib:1.1-mc1.21.8" and asked3[-1].endswith("/examplelib-1.2-mc1.21.8-r3/examplelib-1.1_cf.jar"),
               f"a re-cut tag that cannot be listed falls back to the pinned asset name, as the plain tag does: {got3b} {asked3}")
        # the library/dependent loop (weekly-round dry run, 2026-10-18): a library version is done only once a dependent's
        # Gate C is recorded on its jar, and the dependent could not pin it until it was done. A version-branch library's
        # PRE-RELEASE with green gates is taken for the dependent's pre-release (marked in DEPS.md, the dependent's
        # version not done); a checkout, a full Release or red gates are still refused
        asked = []
        lk_pre = dict(lk_u, prerelease=lambda r, co, t: (asked.append(co), (True, f"{co} is a pre-release with green gates"))[1])
        ported.clear()
        rows, stop = resolve(repo, "1.21.4", {"owner/lib": "lib-1.0-mc1.21.4"}, lk_pre)
        by = {r["module"]: r for r in rows}
        md = render_md("1.21.4", rows, stop)
        expect(stop is None and ported == ["com.example:lib"] and by["lib"].get("prerelease") and not by["lib"].get("refused")
               and PRERELEASE_MARK in md and "Development only" not in md and "not done" in md,
               f"a library pre-release with green gates: pinned, and DEPS.md marks it: {stop} {md}")
        ported.clear()
        rows, stop = resolve(repo, "1.21.4", {"owner/lib": repo}, lk_pre)
        expect(stop and "library first" in stop and not ported and asked == ["lib-1.0-mc1.21.4"],
               f"a checkout is never a pre-release (not asked, refused): {stop} {asked}")
        rows, stop = resolve(repo, "1.21.4", {"owner/lib": "lib-1.0-mc1.21.4"},
                             dict(lk_u, prerelease=lambda r, co, t: (False, "a full Release")))
        expect(stop and "library first" in stop and not ported, f"no green pre-release: refused as before: {stop}")
        got = []
        fake_get = lambda url, h=None: (got.append(url), json.dumps({"prerelease": "/releases/tags/" in url and "pre" in url}).encode()
                                        if "/releases/tags/" in url else b"minecraft_version=1.21.4\n")[1]
        ok_, why_ = library_prerelease("owner/lib", "lib-pre-mc1.21.4", "1.21.4", fake_get, gates=lambda *a_: [])
        bad_, why_b = library_prerelease("owner/lib", "lib-1.0-mc1.21.4", "1.21.4", fake_get, gates=lambda *a_: [])
        red_, why_r = library_prerelease("owner/lib", "lib-pre-mc1.21.4", "1.21.4", fake_get,
                                         gates=lambda *a_: ["no green Gate C recorded in GATES.md"])
        expect(ok_ and not bad_ and "full Release" in why_b and not red_ and "GATES.md" in why_r,
               f"library_prerelease: a pre-release with green gates only: {why_} | {why_b} | {why_r}")
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
    # the done-list cases need tools/fork-port.py (the one done list); the Port CI kit ships layer-deps.py without
    # it (its jobs only fetch pinned jars), so its self-check from the unpacked kit skips them -- the repository's
    # own run always has it and runs them
    if _fork_port() is None:
        print("  skipped: the sibling done-list cases (tools/fork-port.py is not in this tree)")
    else:
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
        files[".multiversion/targets/1.21.4/GATES-RED.md"] = "Red full gate run at `abc1234` for Minecraft 1.21.4\n"
        r_ = rel_done("a release whose last full gate run was red (GATES-RED.md at the tag): not done", False)
        expect("GATES-RED.md" in r_[1], f"the red record is named: {r_}")
        del files[".multiversion/targets/1.21.4/GATES-RED.md"]
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
    _check_pins_and_bumps(expect)
    if bad:
        print("layer-deps self-check: FAIL\n  " + "\n  ".join(bad))
        return 1
    print("layer-deps self-check: OK")
    return 0


def _check_pins_and_bumps(expect):
    """A pinned dependency is kept by every re-resolve, on a version-branch fixture and a layer target (no lookup, the
    block and DEPS.md byte for byte as they were); --bump moves it (to a version, or to the newest); a single-version
    fixture: resolve without --bump changes nothing, --bump rewrites its build.gradle literal or gradle.properties key;
    `updates` names each newer build, ticked or with its reason, and a failed lookup as unchecked."""
    import contextlib, io
    build = ('dependencies {\n    implementation "curse.maven:somelib-111:222"\n'
             '    runtimeOnly "maven.modrinth:viewer:${viewer_version}"\n    compileOnly "maven.modrinth:devmod:abc123"\n}\n')
    vbuild = build.replace("${viewer_version}", "v10")
    looked = []
    lk = {"cf": lambda: None, "mr_id": lambda slug: None,
          "curse": lambda d, t, cf, c: (looked.append(d["module"]), {"to": f"maven.modrinth:somelib:new{len(looked)}",
                                                                      "source": "fake newest"})[1],
          "modrinth": lambda p, t, c: (looked.append(p), {"to": f"maven.modrinth:{p}:new{len(looked)}", "source": "fake"})[1]}
    def q(fn, *a, **k):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            return fn(*a, **k), buf
    with tempfile.TemporaryDirectory() as td:
        # a version branch: neoforge-1.21.4's checkout, its block in .port/1.21.4/branch.properties
        vb = pathlib.Path(td) / "vb"
        (vb / ".port/1.21.4").mkdir(parents=True)
        (vb / "gradle.properties").write_text("minecraft_version=1.21.4\nviewer_version=1.0\n", encoding="utf-8")
        (vb / "build.gradle").write_text(build.replace("${viewer_version}", "v10"), encoding="utf-8")
        bp = vb / ".port/1.21.4/branch.properties"
        bp.write_text("minecraft_version=1.21.4\nparent=neoforge-1.21.1\n", encoding="utf-8")
        rows, stop = resolve(vb, "1.21.4", {}, lk)
        write(vb, "1.21.4", rows, stop)
        first, md1 = bp.read_text(encoding="utf-8"), (vb / ".port/1.21.4/DEPS.md").read_text(encoding="utf-8")
        kept = current_registry_pins(vb, "1.21.4")
        expect(looked == ["somelib-111", "viewer", "devmod"] and kept == {"curse.maven:somelib-111": "maven.modrinth:somelib:new1",
               "maven.modrinth:viewer": "maven.modrinth:viewer:new2", "maven.modrinth:devmod": "maven.modrinth:devmod:new3"},
               f"a first resolve looks every dependency up and pins it: {looked} {kept}")
        rows, stop = resolve(vb, "1.21.4", {}, lk, kept=kept)
        write(vb, "1.21.4", rows, stop)
        expect(len(looked) == 3 and bp.read_text(encoding="utf-8") == first
               and (vb / ".port/1.21.4/DEPS.md").read_text(encoding="utf-8") == md1,
               f"a re-resolve keeps every pin, with no lookup: branch.properties and DEPS.md unchanged: {looked}")
        rc, buf = q(cmd_resolve, argparse.Namespace(repo=str(vb), target="1.21.4", port_checkout=[], port_release=[],
                                                    dry_run=False, allow_undone_library=False, bump=[], bump_all=False))
        expect(rc == 0 and bp.read_text(encoding="utf-8") == first,
               f"the command line re-resolve (fork-port.py setup's) keeps every pin, with no network: {rc} {buf.getvalue()}")
        rows, stop = resolve(vb, "1.21.4", {}, lk, kept=kept, bumps=parse_bumps(["viewer=maven.modrinth:viewer:v12"], False,
                                                                                    gradle_deps(vbuild)))
        by = {r["module"]: r for r in rows}
        expect(len(looked) == 3 and by["viewer"]["to"] == "maven.modrinth:viewer:v12" and "--bump" in by["viewer"]["source"]
               and by["somelib-111"]["to"] == "maven.modrinth:somelib:new1",
               f"--bump DEP=COORD pins that one as given, the others kept: {by}")
        rows, stop = resolve(vb, "1.21.4", {}, lk, kept=kept, bumps=parse_bumps(["somelib"], False, gradle_deps(vbuild)))
        by = {r["module"]: r for r in rows}
        expect(looked[3:] == ["somelib-111"] and by["somelib-111"]["to"] == "maven.modrinth:somelib:new4"
               and by["viewer"]["to"] == "maven.modrinth:viewer:new2", f"--bump DEP (a CurseForge slug) re-picks that one only: {looked}")
        rows, stop = resolve(vb, "1.21.4", {}, lk, kept=kept, bumps=parse_bumps([], True, gradle_deps(vbuild)))
        expect(len(looked) == 7, f"--bump-all re-picks every one: {looked}")
        try:
            parse_bumps(["nosuch"], False, gradle_deps(vbuild)); expect(False, "--bump of a dependency the build does not pin is refused")
        except ValueError:
            pass
        # a layer target: the same rule in target.properties
        ly = pathlib.Path(td) / "ly"
        (ly / ".multiversion/targets/1.21.4").mkdir(parents=True)
        (ly / ".multiversion/base.properties").write_text("minecraft_version=1.21.1\n", encoding="utf-8")
        (ly / ".multiversion/targets/1.21.4/target.properties").write_text("prop.minecraft_version=1.21.4\n", encoding="utf-8")
        (ly / "build.gradle").write_text(build.replace("${viewer_version}", "v10"), encoding="utf-8")
        rows, stop = resolve(ly, "1.21.4", {}, lk)
        write(ly, "1.21.4", rows, stop)
        tp1 = (ly / ".multiversion/targets/1.21.4/target.properties").read_text(encoding="utf-8")
        n = len(looked)
        rows, stop = resolve(ly, "1.21.4", {}, lk, kept=current_registry_pins(ly, "1.21.4"))
        write(ly, "1.21.4", rows, stop)
        expect(len(looked) == n and (ly / ".multiversion/targets/1.21.4/target.properties").read_text(encoding="utf-8") == tp1,
               "a layer target keeps its pins the same way")
        # a single-version fork: build.gradle's coordinates are the pins
        sv = pathlib.Path(td) / "sv"
        sv.mkdir()
        (sv / "build.gradle").write_text(build, encoding="utf-8")
        (sv / "gradle.properties").write_text("minecraft_version=1.21.1\nviewer_version=1.0\n", encoding="utf-8")
        snap = lambda: ((sv / "build.gradle").read_text(encoding="utf-8"), (sv / "gradle.properties").read_text(encoding="utf-8"))
        s0 = snap()
        ns = lambda **k: argparse.Namespace(**{"repo": str(sv), "target": "1.21.1", "port_checkout": [], "port_release": [],
                                               "dry_run": False, "allow_undone_library": False, "bump": [], "bump_all": False, **k})
        rc, buf = q(cmd_resolve, ns())
        expect(rc == 2 and snap() == s0 and "only by --bump" in buf.getvalue(),
               f"single-version: resolve without --bump has nothing to do and changes nothing: {buf.getvalue()}")
        expect([(d["coord"], d["src"]) for d in build_pins(*s0)] == [("curse.maven:somelib-111:222", "literal"),
               ("maven.modrinth:viewer:1.0", "prop:viewer_version"), ("maven.modrinth:devmod:abc123", "literal")],
               f"build_pins: literal and gradle.properties-keyed pins: {build_pins(*s0)}")
        rc, buf = q(cmd_resolve, ns(bump=["somelib=999", "maven.modrinth:viewer=1.1"]))
        s1 = snap()
        expect(rc == 0 and 'implementation "curse.maven:somelib-111:999"' in s1[0] and "viewer_version=1.1\n" in s1[1]
               and "${viewer_version}" in s1[0] and 'compileOnly "maven.modrinth:devmod:abc123"' in s1[0],
               f"single-version --bump: the literal rewritten in build.gradle, the keyed one in gradle.properties: {s1} {buf.getvalue()}")
        rc, buf = q(cmd_resolve, ns(bump=["viewer=maven.modrinth:other:2"]))
        expect(rc == 2 and snap() == s1, f"a keyed pin's coordinate change is refused: {buf.getvalue()}")
        # review S2: only the dependency's own quoted token on a line outside comments; a key two coordinates read
        # refuses; CRLF stays CRLF
        (sv / "build.gradle").write_bytes(('dependencies {\r\n    implementation "curse.maven:somelib-111:999"\r\n'
                                           '    // implementation "curse.maven:somelib-111:999"\r\n'
                                           '    /* implementation "curse.maven:somelib-111:999" */\r\n'
                                           '    runtimeOnly "maven.modrinth:viewer:${viewer_version}"\r\n'
                                           '    implementation "maven.modrinth:devmod:abc123"\r\n'
                                           '    compileOnly "maven.modrinth:devmod:abc1234"\r\n}\r\n').encode("utf-8"))
        (sv / "gradle.properties").write_bytes(b"minecraft_version=1.21.1\r\nviewer_version=1.1\r\n")
        rc, buf = q(cmd_resolve, ns(bump=["somelib=1000", "devmod=abc1239", "viewer=1.2"]))
        bb, pp = (sv / "build.gradle").read_bytes(), (sv / "gradle.properties").read_bytes()
        expect(rc == 0 and b'implementation "curse.maven:somelib-111:1000"\r\n' in bb
               and b'// implementation "curse.maven:somelib-111:999"' in bb and b'/* implementation "curse.maven:somelib-111:999" */' in bb
               and b'"maven.modrinth:devmod:abc1239"' in bb and b'"maven.modrinth:devmod:abc1234"' in bb
               and b"viewer_version=1.2\r\n" in pp and bb.count(b"\r\n") == 8 and b"\n" not in bb.replace(b"\r\n", b""),
               f"single-version --bump: the token on the live line only, comments and a longer coordinate untouched, CRLF "
               f"kept: {bb!r} {pp!r} {buf.getvalue()}")
        (sv / "build.gradle").write_bytes(bb.replace(b'    implementation "maven.modrinth:devmod:abc1239"',
                                                     b'    implementation "maven.modrinth:other:${viewer_version}"'))
        rc, buf = q(cmd_resolve, ns(bump=["viewer=1.3"]))
        expect(rc == 2 and (sv / "gradle.properties").read_bytes() == pp and "2 dependency lines read" in buf.getvalue(),
               f"a gradle.properties key another coordinate reads is refused, nothing written: {buf.getvalue()}")
        (sv / "build.gradle").write_text(build, encoding="utf-8")
        (sv / "gradle.properties").write_text("minecraft_version=1.21.1\nviewer_version=1.1\n", encoding="utf-8")
        files = {"viewer": [{"id": "a1", "name": "1.1", "mcs": ["1.21.1"], "loaders": ["neoforge"], "channel": "release", "date": "2026-01"},
                            {"id": "a2", "name": "1.2", "mcs": ["1.21.1"], "loaders": ["neoforge"], "channel": "release", "date": "2026-03"}]}
        fl = {"mr_files": lambda p: files[p]}
        rc, buf = q(cmd_bump_single, sv, "1.21.1", ["viewer"], False, False, fl)
        expect(rc == 0 and "viewer_version=1.2\n" in snap()[1], f"single-version --bump DEP: the newest compatible: {buf.getvalue()}")
    # updates: the plan's rows, their defaults and reasons
    mrf = {"rel": [{"id": "r1", "name": "2.0.1", "mcs": ["1.21.1", "1.21.4"], "loaders": ["neoforge"], "channel": "release", "date": "2026-01"},
                   {"id": "r2", "name": "2.1.0", "mcs": ["1.21.1", "1.21.4"], "loaders": ["neoforge"], "channel": "release", "date": "2026-02"}],
           "beta": [{"id": "b1", "name": "1.0", "mcs": ["1.21.1"], "loaders": ["neoforge"], "channel": "release", "date": "2026-01"},
                    {"id": "b2", "name": "1.1-beta", "mcs": ["1.21.1"], "loaders": ["neoforge"], "channel": "beta", "date": "2026-02"}],
           "major": [{"id": "m1", "name": "examplemod-1.21.1-neoforge-19.4.0", "mcs": ["1.21.1"], "loaders": ["neoforge"], "channel": "release", "date": "2026-01"},
                     {"id": "m2", "name": "examplemod-1.21.1-neoforge-20.0.1", "mcs": ["1.21.1"], "loaders": ["neoforge"], "channel": "release", "date": "2026-02"}],
           "drop": [{"id": "d1", "name": "3.0", "mcs": ["1.21.1", "1.21.4"], "loaders": ["neoforge"], "channel": "release", "date": "2026-01"},
                    {"id": "d2", "name": "3.1", "mcs": ["1.21.1"], "loaders": ["neoforge"], "channel": "release", "date": "2026-02"}],
           "fresh": [{"id": "f1", "name": "1.0", "mcs": ["1.21.1"], "loaders": ["neoforge"], "channel": "release", "date": "2026-01"}]}

    def mr(p):
        if p == "down":
            raise OSError("HTTP Error 503")
        return mrf[p]
    sw = {"project": "sw", "id": "s1", "name": "1.0", "mcs": ["1.21.1"], "loaders": ["neoforge"], "channel": "release", "date": "2026-01"}
    mrf["sw"] = [sw, dict(sw, id="s2", name="1.1", date="2026-02")]
    pins = {"1.21.1": {"maven.modrinth:rel": "maven.modrinth:rel:r1", "maven.modrinth:beta": "maven.modrinth:beta:1.0",
                       "maven.modrinth:major": "maven.modrinth:major:m1", "maven.modrinth:drop": "maven.modrinth:drop:d1",
                       "maven.modrinth:fresh": "maven.modrinth:fresh:f1", "maven.modrinth:down": "maven.modrinth:down:x",
                       "curse.maven:sw-77": "curse.maven:sw-77:5"},
            "1.21.4": {"maven.modrinth:rel": "maven.modrinth:rel:r1"}}
    res = dependency_updates(pins, {"mr_files": mr, "cf": lambda: None, "mr_sha1": lambda h: sw, "curse_jar": lambda m, v: b"jar"})
    got = {(r["mc"], r["ga"]): r for r in res["rows"]}
    expect(got[("1.21.1", "maven.modrinth:rel")]["to"] == "maven.modrinth:rel:r2" and got[("1.21.1", "maven.modrinth:rel")]["default"]
           and got[("1.21.4", "maven.modrinth:rel")]["default"]
           and got[("1.21.1", "maven.modrinth:beta")]["to"] == "maven.modrinth:beta:1.1-beta"
           and not got[("1.21.1", "maven.modrinth:beta")]["default"] and "a beta" in got[("1.21.1", "maven.modrinth:beta")]["why"]
           and "major-version jump" in got[("1.21.1", "maven.modrinth:major")]["why"]
           and "drops Minecraft 1.21.4" in got[("1.21.1", "maven.modrinth:drop")]["why"]
           and ("1.21.1", "maven.modrinth:fresh") not in got
           and got[("1.21.1", "curse.maven:sw-77")]["to"] == "maven.modrinth:sw:s2" and "switches the pin" in got[("1.21.1", "curse.maven:sw-77")]["why"]
           and [u["ga"] for u in res["unchecked"]] == ["maven.modrinth:down"] and "503" in res["unchecked"][0]["why"]
           and res["checked"] == 8,
           f"updates: a release ticked; a beta, a major jump, a dropped version, a provider switch unticked with the reason; "
           f"the current one not listed; a failed lookup unchecked: {res}")
    # review S2 (security): a registry version string is the publisher's -- `$(...)` or a backtick in it would run in
    # the round's --bump command; such a build is never offered, and the command is quoted
    evil = {"evil": [{"id": "e1", "name": "1.0", "mcs": ["1.21.1"], "loaders": ["neoforge"], "channel": "release", "date": "2026-01"},
                     {"id": "e2", "name": "1.1$(touch pwned)`id`", "mcs": ["1.21.1"], "loaders": ["neoforge"], "channel": "release",
                      "date": "2026-02"}]}
    res = dependency_updates({"1.21.1": {"maven.modrinth:evil": "maven.modrinth:evil:1.0"}}, {"mr_files": lambda p: evil[p]})
    import shlex
    cmd = bump_command("1.21.1", "maven.modrinth:x", "maven.modrinth:x:1.2", "<checkout of neoforge-{mc}>")
    expect(not res["rows"] and res["unchecked"] and "characters a command must not carry" in res["unchecked"][0]["why"]
           and shlex.split(cmd)[-1] == "maven.modrinth:x=maven.modrinth:x:1.2" and "'<checkout of neoforge-1.21.1>'" in cmd,
           f"a version string with shell syntax is never offered; the --bump command is quoted: {res} {cmd}")
    expect(version_pins(build.replace("${viewer_version}", "v1"), "", f"{BEGIN}\nsubstitute.maven.modrinth\\:viewer=maven.modrinth:viewer:v9\n"
                        f"exclude.maven.modrinth\\:devmod=x\nsubstitute.com.example\\:lib=com.example:lib:1-mc1.21.4\n"
                        f"port_jar.com.example\\:lib=u s\n{END}\n") == {"curse.maven:somelib-111": "curse.maven:somelib-111:222",
                                                                    "maven.modrinth:viewer": "maven.modrinth:viewer:v9"},
           "version_pins: the block's substitutes over build.gradle's, an exclude dropped, a sibling port not a registry pin")


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
        if ga in ports and coord == f"{ga}:{port_version(ports[ga]['version'], target)}":
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
                   help="owner/repo@TAG: a sibling's release -- a layer's holding <asset>-mc<t>.jar, or a version branch's "
                        "(<mod>-<ver>-mc<t>), whose asset of the pinned name and variant is read from the release; pinned "
                        "(url + sha256) for CI")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--bump", action="append", default=[], metavar="DEP[=VERSION]",
                   help="move this pinned CurseForge/Modrinth dependency: to VERSION (a file/version id, or a whole "
                        "group:module:version), else to the newest compatible build. Without it a pinned dependency is "
                        "kept as it is; on a single-version fork it edits build.gradle (or the gradle.properties key)")
    r.add_argument("--bump-all", action="store_true", help="move every pinned registry dependency to its newest compatible build")
    r.add_argument("--allow-undone-library", action="store_true",
                   help="development only: build against a --port-checkout sibling whose target is not done (recorded in DEPS.md)")
    ld = sub.add_parser("library-done", help="is a sibling library's target done? exit 0 yes, 18 no")
    ld.add_argument("--target", required=True)
    ld.add_argument("--port-checkout", help="owner/repo=DIR")
    ld.add_argument("--port-release", help="owner/repo@TAG")
    up = sub.add_parser("updates", help="each pinned CurseForge/Modrinth dependency with a newer compatible build "
                        "(what --bump would take), with the reason to look first; nothing is changed")
    up.add_argument("--repo", required=True); up.add_argument("--target", help="the checkout's Minecraft version (default: "
                    "gradle.properties')"); up.add_argument("--json", action="store_true")
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
    if a.cmd == "updates":
        return cmd_updates(a)
    if a.cmd == "library-done":
        return cmd_library_done(a)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
