#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Give a version branch its own build of every mod the fork depends on (issue #77).

    python3 tools/layer-deps.py resolve --repo DIR --target 1.21.4 [--port-checkout owner/repo=DIR ...]
                                [--port-release owner/repo@TAG ...] [--allow-undone-library] [--dry-run]
                                [--bump DEP[=VERSION] ...] [--bump-all]
    python3 tools/layer-deps.py updates --repo DIR [--target MC] [--json]
    python3 tools/layer-deps.py library-done --target 1.21.4 (--port-checkout owner/repo=DIR | --port-release owner/repo@TAG)
    python3 tools/layer-deps.py --self-check

A version branch (tools/fork-branches.py: branch neoforge-<t>, checked out) compiles the authors' code against another
Minecraft version, but the authors' build.gradle pins its mods for the BASE version (`curse.maven:somelib-123:456`,
`maven.modrinth:somelib:abc`, a sibling port's `group:artifact:version` from mavenLocal). This finds each one's build
for the version and writes the swaps into its .port/<t>/branch.properties, between `# BEGIN layer-deps` and
`# END layer-deps` (.port/branch.gradle applies them):

  substitute.<group:module>=<group:module:version>   the dependency's build for this Minecraft
  exclude.<group:module>=<reason>                    no build for this Minecraft and not required: off the classpath

and a table of what it did into .port/<t>/DEPS.md. A checkout with the removed .multiversion/ layer at HEAD is refused
(tools/fork-kind.py).

WHERE A BUILD IS LOOKED UP
  curse.maven   the CurseForge project's files for (neoforge, target) when CURSEFORGE_API_KEY is set; else the
                pinned base jar is fetched from cursemaven, its sha1 is looked up on Modrinth (the same file is often
                uploaded to both) and that Modrinth project's builds are used. The DEPS.md row names which.
  maven.modrinth  that Modrinth project's builds.
  a sibling port  the coordinate the fork's port-ci.yml puts into mavenLocal from a sibling port's release
                (`--dep-jar`). A version-branch library's release (a tag ending -mc<t>) holds the jars as its
                build names them, often per variant (`<name>-<ver>_mr.jar`): the asset is read from the release's
                own asset list, the one of the pinned asset's name and variant (when the list cannot be read, the
                jar by name); --port-checkout owner/repo=DIR copies the jar that checkout of its neoforge-<t> branch
                already built (never built here), development only.
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
  the version cannot ship (the fork-versions skill: skip it, and let the next version jump over it: add-target --parent).
  Every other dependency (role "extra", or a compileOnly/runtimeOnly one the manifest does not name: a dev-time mod)
  with no build is excluded, and the drop is recorded. An implementation/api/jarJar dependency the manifest does not name
  is taken as required (fail closed). A Modrinth pin may name the project's slug while the manifest's cdn URL carries its
  id: the slug is looked up (/v2/project/<slug>) to match. A fork without port-install.json treats every dependency
  as required.

  A sibling's `port_jar.` pin (written by `--port-release`) is kept by a later resolve that names that sibling with
  neither --port-checkout nor --port-release, as long as it pins the version port-ci.yml installs, and its jar is
  installed into ~/.m2 by a sha256-checked download (skipped when ~/.m2 already holds it; a failed download or a
  mismatch keeps the pin and says NOT installed in its row). `--dry-run` writes nothing, and neither copies nor fetches
  or installs a sibling jar: its row says what it would do.

LIBRARY FIRST
  A dependent's version T builds only against a sibling library's T that is DONE by tools/fork-branches.py's one done
  list (target_done), the library rule's own reason (no dependent recorded yet) aside: for a --port-checkout, on the
  library's neoforge-T branch; for a --port-release, on the tag's tree with the library's history (a bare clone under
  $MMVU_CACHE_DIR/git/, default ~/.mmvu/cache/git/), so the release and a checkout answer alike. A sibling named by
  --port-checkout or --port-release that is not done stops the resolve with exit 18 (dry run too; nothing is fetched or
  written but DEPS.md's reason). --allow-undone-library lets a --port-checkout through for development only -- DEPS.md
  records it under "Development only" -- never a release. `library-done` asks the same question alone. A pin kept from
  an earlier --port-release is not re-checked.
  A version-branch library's PRE-RELEASE whose version is not done is still taken when its gates are green at the tag
  (GATES.md: a green Gate C at a commit the tag carries): the library rule waits for a dependent's Gate C against the
  library's jar, so a dependent's pre-release may pin it ("Don't exceed your dependency"). DEPS.md marks it
  ("Library pre-release (not done)") and the dependent's version is then not done until the library's is and the pin
  is re-resolved; a person who played the pair may still promote it, and port-publish.py publishes it only once that
  exact library release is promoted and published. A full Release, or a pre-release with red gates, is refused.

ISOLATION. A dependent never builds a sibling's checkout: --port-checkout copies the jar the checkout built and pins it
as `port_jar_dev.` -- development only, refused by the dependent's done check.

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
    """True when `repo` is checked out on version branch `target`."""
    repo = pathlib.Path(repo)
    if not (repo / ".port" / target / "branch.properties").is_file():
        return False
    gp = repo / "gradle.properties"        # read here, not through fork-branches.py: a fork's CI kit carries this file only
    m = re.search(r"(?m)^\s*minecraft_version\s*=\s*(\S+)", gp.read_text(encoding="utf-8")) if gp.is_file() else None
    return bool(m) and m.group(1) == target


def target_dir(repo, target):
    return pathlib.Path(repo) / ".port" / target


def props_file(repo, target):
    return target_dir(repo, target) / "branch.properties"


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
        if r.get("pin"):            # a sibling port's released per-version jar: the branch's CI installs it from this
            lines.append(f"port_jar.{key(ga)}={r['pin']}")
        if r.get("pin_dev"):        # a jar copied from a sibling's checkout (version branches): development only
            lines.append(f"port_jar_dev.{key(ga)}={r['pin_dev']}")
        if r.get("pin") and r.get("prerelease"):   # pinned to a library pre-release that is not done: kept with the pin
            lines.append(f"{PRERELEASE_KEY}{key(ga)}=" + " ".join(str(r["prerelease"]).split()))
    lines.append(END)
    return "\n".join(lines) + "\n"


def splice(props, block):
    """branch.properties with its layer-deps block replaced (or appended)."""
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
                   f"person plays) and a person who played the pair may promote it; it publishes only once that exact library "
                   f"release is promoted and published, and it is not done until the library's {target} is done: then "
                   f"re-resolve with `--port-release`.")
    return "\n".join(out) + "\n"


PRERELEASE_MARK = "**Library pre-release (not done):**"      # tools/fork-branches.py target_done() reads it


# ---------------------------------------------------------------- network lookups

def _get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "mc-mod-version-upgrade/layer-deps", **(headers or {})})
    try:
        import providers      # the migrator's CA handling; a fork's CI kit runs without it
        ctx = providers.ssl_context()
    except ImportError:
        ctx = None
    with urllib.request.urlopen(req, timeout=120, context=ctx) as r:
        return r.read()


def m2_root():
    """The local Maven repository jars are installed into: $MMVU_M2_REPO when set (round.py's version-branch dependent
    gate gives each run a private one, Gradle pointed at it with -Dmaven.repo.local, so no install elsewhere touches
    its candidate jar), else ~/.m2/repository."""
    d = os.environ.get("MMVU_M2_REPO")
    return pathlib.Path(d) if d else pathlib.Path.home() / ".m2/repository"


def write_atomic(path, data):
    """Write `data` (bytes) to `path` through a temporary file beside it and os.replace: a build reading the jar
    concurrently sees the old file or the new one, never half of one."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def install_pin(ga, ver, value):
    """Install a port_jar pin ("<url> <sha256>") into ~/.m2 as ga:ver, the download checked against the sha256; a jar
    already there with that sha256 is not fetched again. -> (ok, message)."""
    url, sha = value.split()
    g, a = ga.split(":")
    have = m2_root() / g.replace(".", "/") / a / ver / f"{a}-{ver}.jar"
    old = hashlib.sha256(have.read_bytes()).hexdigest() if have.is_file() else None
    if old == sha:
        return True, f"{ga}:{ver} already in mavenLocal (sha256 ok)"
    try:
        data = _get(url)
    except Exception as e:
        return False, f"{url}: {e}"
    got = hashlib.sha256(data).hexdigest()
    if got != sha:
        return False, f"{url} has sha256 {got}, the pin says {sha}"
    install_m2(ga, ver, data)
    # a different jar at the coordinate is said loudly: a stale download, or a jar something else put there on purpose
    return True, f"{ga}:{ver} <- {url} (sha256 ok)" + (f"; REPLACED {have.as_posix()} (sha256 {old[:12]} -> {sha[:12]})"
                                                       if old else "")


def install_m2(ga, ver, data):
    """Put a jar into the local Maven repository (m2_root) as group:artifact:ver (with a minimal POM), the way
    port-ci's --dep-jar step does; each file written atomically."""
    g, a = ga.split(":")
    dest = m2_root() / g.replace(".", "/") / a / ver
    dest.mkdir(parents=True, exist_ok=True)
    write_atomic(dest / f"{a}-{ver}.jar", data)
    write_atomic(dest / f"{a}-{ver}.pom", (f"<project><modelVersion>4.0.0</modelVersion><groupId>{g}</groupId>"
                                            f"<artifactId>{a}</artifactId><version>{ver}</version><packaging>jar"
                                            f"</packaging></project>\n").encode("utf-8"))


def install_pins(repo, target, pin_jar=None):
    """Install the jars version branch `target` pins into ~/.m2 before a local build: each port_jar.<g:a>=<url>
    <sha256> of .port/<target>/branch.properties, under the coordinate its substitute.<g:a> names, the download
    sha256-checked and a jar already there with that sha256 kept (install_pin). These are the pins the branch's Port
    CI installs too, but not exactly its set: CI installs what its workflow's --dep-jar steps name (fork-branches.py
    remap_ci_args swaps a step's release for the pin when it is a GitHub release URL with a substitute coordinate), so
    this can also install a pin no step names. A file with no pin, or no file, installs nothing. Without it a fresh
    machine's compileClasspath could not resolve the pinned library at every dependent version (measured, keep-current
    round 2026-10-31: each worker installed the jar by hand and re-ran). A head (single-version) branch pins in its
    workflow instead: install_workflow_pins. -> [(ok, message)], one per pin."""
    tp = props_file(repo, target)
    text = tp.read_text(encoding="utf-8") if tp.is_file() else ""
    got = {}
    for kind, k, v in PIN_LINE.findall(text):
        got.setdefault(k.replace("\\:", ":"), {})[kind] = v
    out = []
    for ga in sorted(got):
        pin, coord = got[ga].get("port_jar"), got[ga].get("substitute")
        if not pin:
            continue
        if not coord or coord.count(":") < 2 or len(pin.split()) != 2:
            out.append((False, f"{ga}: port_jar pin without a substitute.{key(ga)} coordinate or a sha256: not installed"))
            continue
        g, a, ver = coord.split(":", 2)
        out.append((pin_jar or install_pin)(f"{g}:{a}", ver, pin))
    return out


DEP_JAR_SHA = re.compile(DEP_JAR.pattern + r'\n          echo "([0-9a-f]{64})  ')


def workflow_pins(workflow):
    """{group:artifact: (version, "<url> <sha256>")} for each sibling release jar the workflow's --dep-jar steps
    (tools/port-ci.py dep_jars) put into mavenLocal, with the sha256 the step checks."""
    out = {}
    for m in DEP_JAR_SHA.finditer(workflow or ""):
        art, ver, path, repo, tag, asset, sha = m.groups()
        g = path[:-len(f"/{art}/{ver}")].replace("/", ".")
        out[f"{g}:{art}"] = (ver, f"https://github.com/{repo}/releases/download/{tag}/{asset} {sha}")
    return out


def install_workflow_pins(repo, pin_jar=None):
    """Install into ~/.m2 what a head branch's Port CI installs before it builds: each --dep-jar step of its
    .github/workflows/port-ci.yml (the sibling release jar at the step's coordinate, sha256-checked as the step checks
    it), through install_pin. A single-version fork pins its library there, not in .port/<mc>/branch.properties; a
    workflow with no such step, or none, installs nothing. Its --dep-jar-variant legs are CI's alone. -> [(ok,
    message)], one per step."""
    wf = pathlib.Path(repo) / ".github/workflows/port-ci.yml"
    pins = workflow_pins(wf.read_text(encoding="utf-8") if wf.is_file() else "")
    return [(pin_jar or install_pin)(ga, ver, val) for ga, (ver, val) in sorted(pins.items())]


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


# a version's qualifiers after its numbers (`-fix`, `-fix-fix`, `-beta.2`, `-rc1`, `+1.21.4`): hyphen or plus chunks
# that hold a digit, or are a known release word (fix, hotfix, beta, alpha, rc, pre, patch; any case, repeatable) --
# never an underscore (`_mr`/`_cf` is the variant), and never a loader or side word (`-NeoForge`, `-fabric`, `-client`)
_QUALIFIER_WORDS = r"(?:fix|hotfix|beta|alpha|rc|pre|patch)"
_VERSION_QUALIFIER = (r"(?:[-+](?:[A-Za-z0-9.]*\d[A-Za-z0-9.]*|(?i:" + _QUALIFIER_WORDS + r")[0-9.]*)(?![A-Za-z]))*")


def asset_shape(name):
    """A jar's name with its versions blanked: `lib-1.1.9_mr.jar`, `lib-1.1.8_mr-mc1.21.8.jar`, `lib-1.1.9-fix-fix_mr.jar`
    -> `lib-#_mr.jar`; what stays is the mod's name and the variant (`_mr`, `_cf`, `-fabric`, `-client`), compared
    without case (`-NeoForge` is `-neoforge`). The `.jar` extension is set aside first, so no qualifier can take it."""
    stem, ext = (name[:-4], ".jar") if name.lower().endswith(".jar") else (name, "")
    stem = re.sub(r"-mc\d+(?:\.\d+)*", "", stem)
    return (re.sub(r"\d+(?:\.\d+)+" + _VERSION_QUALIFIER, "#", stem) + ext).lower()


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
    if isinstance(co, str) and not is_version_tag(co, target):
        return {"missing": f"{repo}'s release {co} is not a version branch's release for {target} (a tag ending "
                           f"-mc{target})", "source": repo}
    if isinstance(co, str):         # a version branch's release whose asset list could not be read: the jar by name
        url = f"https://github.com/{repo}/releases/download/{co}/{stem}-mc{target}.jar"
        try:
            data = _get(url)
        except Exception:
            url = f"https://github.com/{repo}/releases/download/{co}/{spec['asset']}"
            try:
                data = _get(url)
            except Exception as e2:
                return {"missing": f"{repo}'s release {co} has neither {stem}-mc{target}.jar nor {spec['asset']} ({e2})"
                                   + (f"; and {list_err}" if list_err else ""), "source": repo}
        install_m2(ga, ver, data)
        sha = hashlib.sha256(data).hexdigest()
        return {"to": f"{ga}:{ver}", "pin": f"{url} {sha}", "source": f"{repo} release {co}: {url.rsplit('/', 1)[1]} (sha256 {sha[:16]}…)"}
    if not co:
        return {"missing": f"no --port-checkout {repo}=DIR or --port-release {repo}@TAG to take its {target} jar from",
                "source": repo}
    if (co / ".port" / target / "branch.properties").exists():
        return checkout_jar(ga, spec, target, co, ver)
    return {"missing": f"{repo}'s checkout {co} is not on its version branch for {target} (no .port/{target}/branch.properties)",
            "source": repo}


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

NOT_JAR = {"GATES.md", "GATES-RED.md", "COST.md", "RESIDUE.md", "STUBS.json", "REVIEW.md", "TEST-ITEMS.md", "BEHAVIOUR.json", "DEPS.md",
           "DRIFT.md", "INVENTORY.json", "INVENTORY-CHECK.json", "KNOWN-ISSUES.json"}


ROOT_RECORDS = ("README.md",)       # by path from the repository root: a README under src/ is jar content


def _not_jar(f):
    return f.rsplit("/", 1)[-1] in NOT_JAR or f in ROOT_RECORDS


def _git(co, *a):
    # GIT_OPTIONAL_LOCKS=0: `git status` must not refresh (write) the sibling's index -- another run may hold its checkout
    return subprocess.run(["git", "-C", str(co), *a], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})


def _reasons(why, more=6):
    return "; ".join(why[:more]) + (f" (+{len(why) - more} more)" if len(why) > more else "")


def library_done(repo, co, target):
    """resolve()'s check for a sibling named by --port-checkout (co a Path) or --port-release (co a tag): a version
    branch's release or checkout (a layer sibling is refused: the layer route was removed, tools/fork-kind.py)."""
    if isinstance(co, str):
        if branch_release(repo, co, target):
            return library_done_branch_release(repo, co, target)
        return False, f"{co} is not a version branch's release for {target} (no .port/{target}/branch.properties at the tag)", []
    why = _fork_kind().refuse_layer(co, "layer-deps")
    if why:
        return False, why, []
    if _fb().is_branch_fork(co, target):
        return library_done_branch(co, target)
    return False, f"{co} has no version branch for {target}", []


def _fork_kind():
    import importlib.util
    spec = importlib.util.spec_from_file_location("fork_kind_for_layer_deps", ROOT / "tools/fork-kind.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


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
    except Exception:          # noqa: BLE001 -- unreachable: not a version branch's release
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
                    # version is not done until the library's is (it publishes only with that library release published)
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
                    # the pin alone serves CI (the version's workflow pins it); put the jar into this machine's
                    # mavenLocal too, so a fresh checkout's compile resolves the substitute -- a sha256-checked download
                    ok, msg = lk["pin_jar"](ga, ver.split(":", 2)[2], pin[1])
                    row["source"] += f"; {msg}" if ok else f"; NOT installed into mavenLocal: {msg}"
            elif dry_run and isinstance(checkouts.get(spec["repo"]), str):
                row.update({"to": ver, "source": f"{spec['repo']}: would fetch release {checkouts[spec['repo']]}'s "
                                                 f"asset for {spec['asset']}, pin and install it (dry run)"})
            elif dry_run and isinstance(checkouts.get(spec["repo"]), pathlib.Path):
                co = checkouts[spec["repo"]]
                row.update({"to": ver, "source": f"{spec['repo']}: would copy {co}'s built {spec['asset']} (version "
                                                 f"branch {target}; development only) into mavenLocal (dry run)"}
                           if (co / ".port" / target / "branch.properties").exists()
                           else {"missing": f"{spec['repo']} has no {target} version branch checked out", "source": spec["repo"]})
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
    unsafe = [f"{r['group']}:{r['module']} -> {r['to']!r}" for r in rows
              if r.get("to") and r.get("kind") in ("curse", "modrinth") and not SAFE_COORD.match(r["to"])]
    if unsafe:
        # a registry's version string is the publisher's: never written into branch.properties / target.properties
        stop = "; ".join(x for x in ["coordinates holding characters a build file must not get (only A-Z a-z 0-9 . _ + : - "
                                     "are written): " + "; ".join(unsafe), stop] if x)
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
        if out[ga] and not SAFE_COORD.match(out[ga]):
            raise ValueError(f"--bump {spec}: {out[ga]!r} holds characters a build file must not get (only A-Z a-z 0-9 . _ "
                             "+ : - are written)")
    return out


def cmd_resolve(a):
    repo = pathlib.Path(a.repo).resolve()
    why = _fork_kind().refuse_layer(repo, "layer-deps")
    if why:
        print(why); return 2
    if not branch_target(repo, a.target):
        if getattr(a, "bump", None) or getattr(a, "bump_all", False):
            return cmd_bump_single(repo, a.target, a.bump, a.bump_all, a.dry_run)
        print(f"layer-deps: the checkout is not version branch {a.target} (a single-version fork has nothing to resolve; "
              "its pins are build.gradle's, moved only by --bump)")
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
            print(f"layer-deps: {a.target} {r['group']}:{r['module']}: LIBRARY PRE-RELEASE, not done: pinned for the "
                  f"pair a person plays (recorded in DEPS.md; not done until the library's {a.target} is, published only "
                  f"with that library release published): "
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


def _candidate_files(coord, mc, lk=None, cache=None):
    """(the project's builds, the pinned build or None, coordinate maker, provider switch?) of a pinned registry
    coordinate, for Minecraft `mc`. Raises LookupError when it cannot tell."""
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
    return files, cur, mk, switch


def update_candidate(coord, mc, lk=None, cache=None):
    """(current build, newer build, its coordinate, provider switch?) for a pinned registry coordinate at Minecraft `mc`:
    the newest compatible build published after the pinned one (release first, then beta, then alpha, as pick() picks);
    newer None when there is none. Raises LookupError when it cannot tell (the plan says "could not check")."""
    files, cur, mk, switch = _candidate_files(coord, mc, lk, cache)
    if cur is None:
        raise LookupError(f"the pinned build {coord.split(':')[-1]} is not among the project's builds")
    newer = [f for f in files if (f.get("date") or "") > (cur.get("date") or "") and f.get("id") != cur.get("id")]
    new = pick(newer, mc)
    return cur, new, (mk(new) if new else None), switch


def restore_candidate(coord, mc, lk=None, cache=None):
    """(build, its coordinate, provider switch?) for a dependency a version DROPPED (no build for `mc` when it was
    resolved; `coord` is the authors' base pin): the build pick() takes for `mc` now, or (None, None, switch). Raises
    LookupError when it cannot tell."""
    files, _cur, mk, switch = _candidate_files(coord, mc, lk, cache)
    new = pick(files, mc)
    return new, (mk(new) if new else None), switch


def dropped_pins(build_text, props_text, branch_props_text=""):
    """{group:module: the authors' base coordinate} of each registry dependency a version's layer-deps block excludes
    (DEPS.md "dropped: no <v> build"): what the weekly plan re-checks for a build that has appeared since."""
    base = {f"{d['group']}:{d['module']}": d["coord"] for d in build_pins(build_text, props_text)}
    text = branch_props_text or ""
    if BEGIN not in text or END not in text:
        return {}
    blk = text[text.index(BEGIN):text.index(END)]
    out = {}
    for k in re.findall(r"(?m)^exclude\.(.+?)=", blk):
        ga = k.replace("\\:", ":")
        if ga in base:
            out[ga] = base[ga]
    return out


SAFE_COORD = re.compile(r"^[A-Za-z0-9._+:-]+$")     # what a coordinate may hold to be written into a command (and a box)


def dependency_updates(pins_by_mc, lk=None, dropped_by_mc=None):
    """{"rows": [{"id", "mc", "ga", "from", "to", "from_name", "to_name", "channel", "default", "why"}],
    "unchecked": [{"mc", "ga", "coord", "why"}], "checked": n} for {mc: {group:module: coordinate}}. A row is ticked
    (default) unless the newer build is an alpha or beta, a major-version jump, drops a Minecraft version the fork builds
    that the pinned build serves, or switches provider. A lookup that fails is listed unchecked, never raised.
    dropped_by_mc ({mc: {group:module: base coordinate}}, dropped_pins): a dependency a version dropped that has a build
    for it now is a "restore" row (restore: True), never ticked by default -- bringing an integration back is a port of
    its code, the owner's call."""
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
    for mc in sorted(dropped_by_mc or {}, key=lambda v: tuple(int(x) for x in re.findall(r"\d+", v))):
        for ga, coord in sorted(dropped_by_mc[mc].items()):
            n += 1
            try:
                if not SAFE_COORD.match(coord) or not SAFE_COORD.match(ga):
                    raise LookupError("the pinned coordinate holds characters a command must not carry")
                new, to, switch = restore_candidate(coord, mc, lk, cache)
                if to is not None and not SAFE_COORD.match(to):
                    raise LookupError("the build's version holds characters a command must not carry "
                                      "(only A-Z a-z 0-9 . _ + : - are taken): restore it by hand if it is right")
            except Exception as e:      # noqa: BLE001 -- fail soft
                unchecked.append({"mc": mc, "ga": ga, "coord": coord if SAFE_COORD.match(coord) else "",
                                  "why": f"dropped at {mc}: " + (str(e)[:200] or type(e).__name__)})
                continue
            if not new:
                continue
            why = [f"restore: dropped at {mc} (no build when it was resolved; DEPS.md), a build exists now -- if ticked, "
                   "the dependency is pinned again and its integration code has to compile, be ported and reviewed"]
            if new.get("channel") in ("alpha", "beta"):
                why.append(f"a {new['channel']}")
            if switch:
                why.append("found on Modrinth by the CurseForge jar's sha1 (no CURSEFORGE_API_KEY): a Modrinth coordinate")
            rows.append({"id": f"D{len(rows) + 1}", "mc": mc, "ga": ga, "from": coord, "to": to, "restore": True,
                         "from_name": "dropped", "to_name": new.get("name"), "channel": new.get("channel"),
                         "default": False, "why": "; ".join(why)})
    return {"rows": rows, "unchecked": unchecked, "checked": n}


def bump_command(mc, ga, to, checkout="<checkout of neoforge-{mc}>"):
    """The one command that applies a bump, the same for every kind of fork."""
    import shlex
    return (f"python3 tools/layer-deps.py resolve --repo {shlex.quote(checkout.format(mc=mc))} --target {shlex.quote(mc)} "
            f"--bump {shlex.quote(f'{ga}={to}')}")


def _mask_comments(text):
    """`text` with every comment character (// to the end of the line, /* ... */) turned into a space, string literals
    and line endings kept: the same length, so an offset into it is an offset into `text`."""
    out, i, n, q = list(text), 0, len(text), None
    while i < n:
        c = text[i]
        if q:
            if c == "\\":
                i += 2; continue
            if c == q or c == "\n":
                q = None
            i += 1; continue
        if c in "\"'":
            q = c; i += 1; continue
        if text.startswith("//", i):
            while i < n and text[i] not in "\r\n":
                out[i] = " "; i += 1
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if text[k] not in "\r\n":
                    out[k] = " "
            i = j; continue
        i += 1
    return "".join(out)


def _dep_code_lines(text):
    """[(start offset, masked line)] of each line of `text` that is a dependency declaration outside any comment (the
    line's comments blanked, so offsets into it are offsets into `text`)."""
    masked, out, pos = _mask_comments(text), [], 0
    for ln in masked.splitlines(True):
        if DEP_LINE.match(ln.rstrip("\r\n")):
            out.append((pos, ln))
        pos += len(ln)
    return out


def replace_coordinate(text, old, new):
    """(`text` with the quoted coordinate `old` replaced by `new` on the dependency lines outside comments only, as a
    whole quoted token -- `lib:1.2` never rewrites `lib:1.2.5` -- every other byte, line endings included, kept; the
    number of replacements)."""
    tok = re.compile(r"""(["'])""" + re.escape(old) + r"""(["'])""")
    spans = [(pos + m.start() + 1, pos + m.end() - 1) for pos, ln in _dep_code_lines(text) for m in tok.finditer(ln)]
    for a, b in reversed(spans):
        text = text[:a] + new + text[b:]
    return text, len(spans)


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
        if not SAFE_COORD.match(to or ""):
            say(f"layer-deps: {target} {ga}: {to!r} holds characters a build file must not get (only A-Z a-z 0-9 . _ + : - "
                "are written): refused, nothing written"); return 2
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
            before = new_props
            if len(users) > 1:
                say(f"layer-deps: {target} {ga}: its version is gradle.properties' {key}, which {len(users)} dependency "
                    f"lines read: bumping it would move the others too -- edit them by hand")
                return 2
            new_props = re.sub(r"(?m)^([ \t]*" + re.escape(key) + r"[ \t]*=[ \t]*)" + re.escape(d["version"])
                               + r"([ \t]*)(?=\r?$)", lambda x: x.group(1) + to.rsplit(":", 1)[1] + x.group(2), new_props)
            if new_props == before:
                say(f"layer-deps: {target} {ga}: gradle.properties' {key}={d['version']} was not found to rewrite: "
                    "nothing written"); return 2
        else:
            new_build, n = replace_coordinate(new_build, d["coord"], to)
            if not n:
                say(f"layer-deps: {target} {ga}: {d['coord']} was not found on a dependency line outside a comment: "
                    "nothing written"); return 2
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
    block applied)."""
    repo = pathlib.Path(repo)
    rd = lambda p: (repo / p).read_text(encoding="utf-8") if (repo / p).is_file() else ""
    build, props = rd("build.gradle"), rd("gradle.properties")
    mc = target or read_props_text(props).get("minecraft_version") or "?"
    return {mc: version_pins(build, props, rd(f".port/{mc}/branch.properties"))}


def checkout_dropped(repo, target=None):
    """{mc: {group:module: base coordinate}} of the dependencies a version branch's checkout dropped (its block's
    excludes); {} for a single-version checkout (no .port/<mc>/branch.properties)."""
    repo = pathlib.Path(repo)
    rd = lambda p: (repo / p).read_text(encoding="utf-8") if (repo / p).is_file() else ""
    props = rd("gradle.properties")
    mc = target or read_props_text(props).get("minecraft_version") or "?"
    d = dropped_pins(rd("build.gradle"), props, rd(f".port/{mc}/branch.properties"))
    return {mc: d} if d else {}


def cmd_updates(a):
    why = _fork_kind().refuse_layer(pathlib.Path(a.repo), "layer-deps")
    if why:
        print(why); return 2
    res = dependency_updates(checkout_pins(pathlib.Path(a.repo), a.target), None,
                             checkout_dropped(pathlib.Path(a.repo), a.target))
    if a.json:
        print(json.dumps(res, indent=1))
        return 0
    for r in res["rows"]:
        print(f"layer-deps: {'restore' if r.get('restore') else 'update'} {r['mc']} {r['ga']}: {r['from']} -> {r['to']} "
              f"({r['from_name']} -> {r['to_name']}, "
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
        (repo / ".port/1.21.4").mkdir(parents=True)
        (repo / "gradle.properties").write_text("minecraft_version=1.21.4\n", encoding="utf-8")     # version branch 1.21.4
        (repo / ".port/1.21.4/branch.properties").write_text(props, encoding="utf-8")
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
        tp = (repo / ".port/1.21.4/branch.properties").read_text(encoding="utf-8")
        expect("substitute.curse.maven\\:somelib-111=" in tp and "exclude.maven.modrinth\\:devmod=" in tp, f"written: {tp}")
        expect((repo / ".port/1.21.4/DEPS.md").exists(), "DEPS.md written")
        have["somelib-111"] = False
        rows, stop = resolve(repo, "1.21.4", {}, lk)
        expect(stop and "curse.maven:somelib-111" in stop, f"a required miss stops: {stop}")
        write(repo, "1.21.4", rows, stop)
        tp2 = (repo / ".port/1.21.4/branch.properties").read_text(encoding="utf-8")
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
        md_ = (repo / ".port/1.21.4/DEPS.md").read_text(encoding="utf-8")
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
        # --dry-run never copies or installs a sibling checkout's jar: it says what it would do
        rows, stop = resolve(repo, "1.21.4", {"owner/lib": repo}, lk_p, dry_run=True)
        expect(len(ported) == 2 and stop is None and any("would copy" in r.get("source", "") and "development only" in r.get("source", "")
                                                         for r in rows),
               f"dry run: no sibling build or copy, what it would do said: {rows}")
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
               "port_version: -mc<t> once, a plain version suffixed")
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
        # a version with a letters-only qualifier (`1.1.9-fix`, `-fix-fix`; weekly round 2026-10-21): the same shape, and
        # still the variant and the mod's name honoured
        fixrel = ["examplelib-1.2-fix-fix_mr.jar", "examplelib-1.2-fix-fix_cf.jar", "otherlib-1.2-fix_mr.jar"]
        expect(asset_shape("examplelib-1.2-fix-fix_mr.jar") == asset_shape("examplelib-1.1_mr.jar") == "examplelib-#_mr.jar"
               and asset_shape("examplelib-1.2-beta1_mr.jar") == "examplelib-#_mr.jar"
               and asset_shape("examplelib-1.2-fabric.jar") == "examplelib-#-fabric.jar"
               and asset_shape("examplelib-1.2-fix-neoforge.jar") == "examplelib-#-neoforge.jar"
               and pick_release_asset(fixrel, "examplelib-1.1_mr.jar", "1.21.4")[0] == "examplelib-1.2-fix-fix_mr.jar"
               and pick_release_asset(fixrel, "examplelib-1.1-fix_cf.jar", "1.21.4")[0] == "examplelib-1.2-fix-fix_cf.jar"
               and pick_release_asset(["examplelib-1.2-fix_cf.jar"], "examplelib-1.1_mr.jar", "1.21.4")[0] is None
               and pick_release_asset(["otherlib-1.2-fix_mr.jar"], "examplelib-1.1_mr.jar", "1.21.4")[0] is None
               and pick_release_asset(["examplelib-1.2-fix-fabric.jar"], "examplelib-1.1.jar", "1.21.4")[0] is None,
               "asset_shape: a -fix qualifier blanked with the version; _cf never for _mr, another mod's jar never")
        # review S2-1: `.jar` is no qualifier, loader and side words in any case are the variant, and only a qualifier
        # with a digit or a release word is blanked (the four repro cases)
        expect(pick_release_asset(["examplelib-1.2-Fabric.jar", "examplelib-1.2-Forge.jar"], "examplelib-1.1-NeoForge.jar",
                                  "1.21.4")[0] is None
               and pick_release_asset(["examplelib-1.2-client.jar"], "examplelib-1.1-fix.jar", "1.21.4")[0] is None
               and pick_release_asset(["examplelib-1.2-fix.jar"], "examplelib-1.1.jar", "1.21.4")[0] == "examplelib-1.2-fix.jar"
               and pick_release_asset(["examplelib-1.2-NeoForge.jar", "examplelib-1.2-Fabric.jar"], "examplelib-1.1-neoforge.jar",
                                      "1.21.4")[0] == "examplelib-1.2-NeoForge.jar"
               and asset_shape("examplelib-1.2-RC2-Hotfix-common.jar") == "examplelib-#-common.jar"
               and asset_shape("examplelib-1.2+1.21.4-beta.2.jar") == "examplelib-#.jar",
               "asset_shape: `.jar` kept out, loader/side words honoured in any case, only digit or release-word qualifiers blanked")
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
    # a released sibling jar is pinned in the block
    blk2 = render_block([{"group": "com.example", "module": "lib", "to": "com.example:lib:1.0-mc1.21.4",
                          "pin": "https://github.com/owner/lib/releases/download/lib-1.0-mc1.21.1/lib-1.0_a-mc1.21.4.jar " + "a" * 64}])
    expect("port_jar.com.example\\:lib=https://github.com/owner/lib/releases/download/lib-1.0-mc1.21.1/lib-1.0_a-mc1.21.4.jar "
           + "a" * 64 in blk2, f"pin line: {blk2}")
    _check_pins_and_bumps(expect)
    _check_install_pins(expect)
    if bad:
        print("layer-deps self-check: FAIL\n  " + "\n  ".join(bad))
        return 1
    print("layer-deps self-check: OK")
    return 0


def _check_pins_and_bumps(expect):
    """A pinned dependency is kept by every re-resolve, on a version-branch fixture (no lookup, the
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
        # re-review S2: a coordinate behind a /* */ comment on the same line is still the one rewritten; a bump that
        # rewrote nothing fails, and so does a registry id with build-file syntax (single-version and version branch)
        (sv / "build.gradle").write_text('dependencies {\n    /* old */ implementation "maven.modrinth:viewer:1.1"\n}\n',
                                         encoding="utf-8")
        rc, buf = q(cmd_resolve, ns(bump=["viewer=1.2"]))
        expect(rc == 0 and '/* old */ implementation "maven.modrinth:viewer:1.2"' in (sv / "build.gradle").read_text(encoding="utf-8"),
               f"a coordinate after a block comment on its line is rewritten: {buf.getvalue()}")
        expect(replace_coordinate('x\n// implementation "maven.modrinth:viewer:1.2"\n', "maven.modrinth:viewer:1.2", "z")[1] == 0,
               "replace_coordinate: nothing outside a dependency line, nothing in a comment")
        payload = 'a2" ; println(\'pwned\') ; def z = "'
        pf = {"viewer": [{"id": "1.2", "name": "1.2", "mcs": ["1.21.1"], "loaders": ["neoforge"], "channel": "release", "date": "2026-01"},
                         {"id": payload, "name": "1.3", "mcs": ["1.21.1"], "loaders": ["neoforge"], "channel": "release", "date": "2026-02"}]}
        bsave = (sv / "build.gradle").read_bytes()
        rc, buf = q(cmd_bump_single, sv, "1.21.1", ["viewer"], False, False, {"mr_files": lambda p_: pf[p_]})
        expect(rc == 2 and (sv / "build.gradle").read_bytes() == bsave and "pwned" not in bsave.decode("utf-8"),
               f"single-version: a registry id with build-file syntax is refused, nothing written: {buf.getvalue()}")
        rc, buf = q(cmd_resolve, ns(bump=["viewer=" + payload]))
        expect(rc == 2 and (sv / "build.gradle").read_bytes() == bsave, f"a user-given unsafe version is refused: {buf.getvalue()}")
        lk_evil = dict(lk, modrinth=lambda p_, t_, c_: {"to": f"maven.modrinth:{p_}:{payload}", "source": "evil registry"})
        b0 = bp.read_text(encoding="utf-8")
        rows, stop = resolve(vb, "1.21.4", {}, lk_evil, kept=current_registry_pins(vb, "1.21.4"),
                             bumps=parse_bumps(["viewer"], False, gradle_deps(vbuild)))
        write(vb, "1.21.4", rows, stop)
        expect(stop and "must not get" in stop and bp.read_text(encoding="utf-8") == b0 and "pwned" not in bp.read_text(encoding="utf-8"),
               f"version branch: a registry coordinate with build-file syntax stops the resolve, branch.properties unchanged: {stop}")
        (sv / "build.gradle").write_text(build, encoding="utf-8")
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
    # a dependency a version dropped (weekly round 2026-10-21: never re-checked): its base pin, offered as an unticked
    # restore row once a build for that version exists; none for a version that excludes nothing (a single-version fork)
    blk_ = f"{BEGIN}\nexclude.maven.modrinth\\:devmod=no 1.21.4 build (extra)\nexclude.com.example\\:lib=x\n{END}\n"
    dp = dropped_pins(build.replace("${viewer_version}", "v1"), "", blk_)
    expect(list(dp) == ["maven.modrinth:devmod"] and dropped_pins(build, "", "") == {}
           and dropped_pins(build, "", f"{BEGIN}\nsubstitute.maven.modrinth\\:viewer=maven.modrinth:viewer:v9\n{END}\n") == {},
           f"dropped_pins: an excluded registry pin's base coordinate, nothing else: {dp}")
    rf = {"back": [{"id": "k1", "name": "1.0", "mcs": ["1.21.1"], "loaders": ["neoforge"], "channel": "release", "date": "2026-01"},
                   {"id": "k4", "name": "1.0+1.21.4", "mcs": ["1.21.4"], "loaders": ["neoforge"], "channel": "release", "date": "2026-05"}],
          "gone": [{"id": "g1", "name": "1.0", "mcs": ["1.21.1"], "loaders": ["neoforge"], "channel": "release", "date": "2026-01"}]}
    res = dependency_updates({"1.21.4": {}}, {"mr_files": lambda p_: rf[p_]},
                             {"1.21.4": {"maven.modrinth:back": "maven.modrinth:back:k1", "maven.modrinth:gone": "maven.modrinth:gone:g1"}})
    expect(len(res["rows"]) == 1 and res["rows"][0]["restore"] and res["rows"][0]["to"] == "maven.modrinth:back:k4"
           and not res["rows"][0]["default"] and res["rows"][0]["why"].startswith("restore: dropped at 1.21.4")
           and res["checked"] == 2 and not res["unchecked"]
           and dependency_updates({"1.21.4": {}}, {"mr_files": lambda p_: rf[p_]}) == {"rows": [], "unchecked": [], "checked": 0},
           f"restore: a dropped dependency with a build now is an unticked restore row; one still without none: {res}")


def _check_install_pins(expect):
    """install_pins: the version's pins into ~/.m2 (a stand-in download, a temporary home), sha256-checked, a jar there
    with that sha256 never fetched again; a single-version fork with pins gets them, one without is untouched."""
    global _get
    jar = b"PK\x03\x04 a library jar"
    sha = hashlib.sha256(jar).hexdigest()
    url = "https://github.com/owner/examplelib/releases/download/examplelib-1.0-mc1.21.1/examplelib-1.0_a.jar"
    fetched, keep_get, keep_env = [], _get, {k: os.environ.get(k) for k in ("HOME", "USERPROFILE")}
    _get = lambda u, headers=None: (fetched.append(u), jar)[1]
    with tempfile.TemporaryDirectory() as td:
        home = pathlib.Path(td) / "home"
        home.mkdir()
        os.environ["HOME"] = os.environ["USERPROFILE"] = str(home)
        try:
            m2 = home / ".m2/repository/com/example/examplelib/1.0-mc1.21.1/examplelib-1.0-mc1.21.1.jar"
            for kind, mc in (("single-version", "1.21.1"), ("version-branch", "1.21.4")):
                repo = pathlib.Path(td) / kind
                (repo / ".port" / mc).mkdir(parents=True)
                (repo / "gradle.properties").write_text(f"minecraft_version={mc}\n", encoding="utf-8")
                (repo / ".port" / mc / "branch.properties").write_text(
                    f"{BEGIN}\nsubstitute.com.example\\:examplelib=com.example:examplelib:1.0-mc{mc}\n"
                    f"port_jar.com.example\\:examplelib={url} {sha}\n{END}\n", encoding="utf-8")
                want = m2.parent.parent / f"1.0-mc{mc}" / f"examplelib-1.0-mc{mc}.jar"
                del fetched[:]
                r1 = install_pins(repo, mc)
                r2 = install_pins(repo, mc)
                expect(len(r1) == 1 and r1[0][0] and want.read_bytes() == jar and want.with_suffix(".pom").is_file()
                       and len(r2) == 1 and r2[0][0] and "already in mavenLocal" in r2[0][1] and fetched == [url],
                       f"install_pins on a {kind} branch: installed under the substitute's coordinate, sha256-checked, "
                       f"not fetched again: {r1} {r2} {fetched}")
                want.parent.mkdir(parents=True, exist_ok=True)
                want.write_bytes(b"a stale jar")
                r3 = install_pins(repo, mc)
                expect(r3 and r3[0][0] and want.read_bytes() == jar and f"REPLACED {want.as_posix()} (sha256 "
                       f"{hashlib.sha256(b'a stale jar').hexdigest()[:12]} -> {sha[:12]})" in r3[0][1],
                       f"a jar there with another sha256 is replaced, and said so: {r3}")
            bad_ = pathlib.Path(td) / "bad"
            (bad_ / ".port/1.21.1").mkdir(parents=True)
            (bad_ / ".port/1.21.1/branch.properties").write_text(
                f"substitute.com.example\\:other=com.example:other:2.0\nport_jar.com.example\\:other={url} {'0' * 64}\n",
                encoding="utf-8")
            r4 = install_pins(bad_, "1.21.1")
            expect(len(r4) == 1 and not r4[0][0] and "sha256" in r4[0][1]
                   and not (home / ".m2/repository/com/example/other").exists(), f"a sha256 mismatch installs nothing: {r4}")
            # a single-version head pins its library in its Port CI workflow (port-ci.py --dep-jar), not in .port/
            import importlib.util as _iu
            _sp = _iu.spec_from_file_location("port_ci_for_layer_deps", ROOT / "tools/port-ci.py")
            pc = _iu.module_from_spec(_sp)
            _sp.loader.exec_module(pc)
            head_ = pathlib.Path(td) / "head"
            (head_ / ".github/workflows").mkdir(parents=True)
            (head_ / "gradle.properties").write_text("minecraft_version=1.21.1\n", encoding="utf-8")
            (head_ / ".github/workflows/port-ci.yml").write_text(
                "jobs:\n  gates:\n    steps:\n" + pc.dep_jars(
                    ["owner/examplelib@examplelib-2.0-mc1.21.1/examplelib-2.0.jar=com.example:examplelib:2.0"],
                    sha_of=lambda u: sha) + "\n", encoding="utf-8")
            del fetched[:]
            w1 = install_workflow_pins(head_)
            hw = home / ".m2/repository/com/example/examplelib/2.0/examplelib-2.0.jar"
            expect(len(w1) == 1 and w1[0][0] and hw.read_bytes() == jar and fetched == [
                       "https://github.com/owner/examplelib/releases/download/examplelib-2.0-mc1.21.1/examplelib-2.0.jar"]
                   and install_pins(head_, "1.21.1") == [],
                   f"a single-version head: its workflow's --dep-jar installed at the step's coordinate: {w1} {fetched}")
            (head_ / ".github/workflows/port-ci.yml").write_text("jobs: {}\n", encoding="utf-8")
            expect(install_workflow_pins(head_) == [] and install_workflow_pins(pathlib.Path(td) / "nofile") == [],
                   "a workflow with no --dep-jar step, or none, installs nothing")
            # a private repository (MMVU_M2_REPO, round.py's version-branch dependent gate): installs go there, ~/.m2
            # is not touched; each file is written through a temporary file, none left behind
            priv, keep_m2 = pathlib.Path(td) / "private-m2", os.environ.get("MMVU_M2_REPO")
            os.environ["MMVU_M2_REPO"] = str(priv)
            try:
                before_ = sorted(str(p) for p in home.rglob("*"))
                install_m2("com.example:cand", "1.0-mc1.21.4", b"the candidate")
                pj = priv / "com/example/cand/1.0-mc1.21.4/cand-1.0-mc1.21.4.jar"
                expect(pj.is_file() and pj.read_bytes() == b"the candidate" and sorted(str(p) for p in home.rglob("*")) == before_
                       and not [p for p in priv.rglob("*.tmp")] and pj.with_suffix(".pom").is_file()
                       and m2_root() == priv,
                       "MMVU_M2_REPO: an install goes into the private repository, atomically, ~/.m2 untouched")
            finally:
                os.environ.pop("MMVU_M2_REPO", None)
                if keep_m2 is not None:
                    os.environ["MMVU_M2_REPO"] = keep_m2
            none_ = pathlib.Path(td) / "nopins"
            (none_ / ".port/1.21.1").mkdir(parents=True)
            (none_ / ".port/1.21.1/branch.properties").write_text("upstream_commit=abc\n", encoding="utf-8")
            before = sorted(str(p) for p in home.rglob("*"))
            del fetched[:]
            expect(install_pins(none_, "1.21.1") == [] and install_pins(pathlib.Path(td) / "nofile", "1.21.1") == []
                   and not fetched and sorted(str(p) for p in home.rglob("*")) == before,
                   "a single-version fork without pins (or without the file) is untouched")
        finally:
            _get = keep_get
            for k, v in keep_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


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
    """The sibling ports' jars for this version in mavenLocal (what a resolve installed) for its sibling substitutes:
    what a version branch's shipped-jar gate boots beside the built jar, as port-ci's `shipped jars`
    job passes the --dep-jar jars on the port branch. A jar not in mavenLocal is left out (shipped-gate then names the
    missing mod). `home`: a home whose ~/.m2 to read, else m2_root()."""
    m2 = pathlib.Path(home) / ".m2/repository" if home else m2_root()
    out = []
    for ga, coord in sibling_substitutes(repo, target).items():
        g, a, ver = coord.split(":", 2)
        jar = m2 / g.replace(".", "/") / a / ver / f"{a}-{ver}.jar"
        if jar.is_file():
            out.append(str(jar))
    return out


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
    r.add_argument("--port-checkout", action="append", default=[],
                   help="owner/repo=DIR: a sibling fork's checkout on its version branch (its jar copied, development only)")
    r.add_argument("--port-release", action="append", default=[],
                   help="owner/repo@TAG: a sibling version branch's release (<mod>-<ver>-mc<t>), whose asset of the pinned "
                        "name and variant is read from the release; pinned (url + sha256) for CI")
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
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if a.cmd == "resolve":
        return cmd_resolve(a)
    if a.cmd == "updates":
        return cmd_updates(a)
    if a.cmd == "library-done":
        return cmd_library_done(a)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
