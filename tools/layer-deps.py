#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Give a fork-layer target its own build of every mod the fork depends on (issue #77).

    python3 tools/layer-deps.py resolve --repo DIR --target 1.21.4 [--port-checkout owner/repo=DIR ...] [--dry-run]
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

Exit codes: 0 resolved (written unless --dry-run); 15 a required dependency has no build for the target; 2 bad input.
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
        got = r.get("to") or ("dropped: " + r["drop"] if r.get("drop") else "MISSING: " + r.get("missing", "?"))
        out.append(f"| `{r['group']}:{r['module']}` | {r['role']} | `{r['version']}` | {got} | {r.get('source', '')} |")
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
            return {"missing": f"{repo}'s release {co} has no {stem}-mc{target}.jar ({e})", "source": repo}
        install_m2(ga, ver, data)
        sha = hashlib.sha256(data).hexdigest()
        return {"to": f"{ga}:{ver}", "pin": f"{url} {sha}", "source": f"{repo} release {co}: {stem}-mc{target}.jar (sha256 {sha[:16]}…)"}
    if not co:
        return {"missing": f"no --port-checkout {repo}=DIR or --port-release {repo}@TAG to take its {target} jar from",
                "source": repo}
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


# configurations that put a mod on the runtime classpath: one the manifest does not name is taken as required (fail
# closed -- a mod the jar needs at run time that silently fell off the classpath ships a broken target)
# (jarJar bundles the mod inside the jar, so it is there at run time too)
RUNTIME_CONFS = {"implementation", "api", "modImplementation", "jarJar"}


def resolve(repo, target, checkouts, lookups=None, dry_run=False, pins=None):
    """(rows, stop). lookups overrides the network (self-check). dry_run: a sibling checkout is not built or installed
    (its row says what would be). pins: {group:module: (substitute coord, port_jar value)} the target's block already
    holds -- a released sibling's pin is kept when no --port-checkout/--port-release names it this run."""
    lk = {"curse": resolve_curse, "modrinth": resolve_modrinth, "port": resolve_port, "cf": cf_provider,
          "mr_id": mr_project_id, "pin_jar": install_pin, **(lookups or {})}
    pins = pins or {}
    build = (repo / "build.gradle").read_text(encoding="utf-8")
    mpath = repo / ".github/port-install.json"
    manifest = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else None
    roles = manifest_roles(manifest)
    wf = repo / ".github/workflows/port-ci.yml"
    ports = port_deps(wf.read_text(encoding="utf-8") if wf.exists() else "")
    cf = lk["cf"]()
    cache, ids, rows, missing_required = {}, {}, [], []
    for d in gradle_deps(build):
        ga = f"{d['group']}:{d['module']}"
        row = dict(d)
        unnamed = "required" if manifest is None or d["conf"] in RUNTIME_CONFS else "build-only"
        if ga in ports:
            spec = ports[ga]
            row["role"] = roles.get(f"port:{spec['repo']}", "required")
            pin = pins.get(ga)
            ver = f"{ga}:{spec['version']}-mc{target}"
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
                           else {"missing": f"{spec['repo']} has no {target} target", "source": spec["repo"]})
            else:
                row.update(lk["port"](ga, spec, target, checkouts))
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
    return rows, stop


PIN_LINE = re.compile(r"(?m)^(substitute|port_jar)\.(.+?)=(.*?)\s*$")


def current_pins(repo, target):
    """{group:module: (substitute coordinate, port_jar value)} for each port_jar pin in the target's layer-deps block."""
    tp = repo / ".multiversion/targets" / target / "target.properties"
    text = tp.read_text(encoding="utf-8") if tp.is_file() else ""
    if BEGIN not in text or END not in text:
        return {}
    got = {}
    for kind, k, v in PIN_LINE.findall(text[text.index(BEGIN):text.index(END)]):
        got.setdefault(k.replace("\\:", ":"), {})[kind] = v
    return {ga: (x.get("substitute"), x["port_jar"]) for ga, x in got.items() if x.get("port_jar")}


def write(repo, target, rows, stop):
    t = repo / ".multiversion/targets" / target
    if not t.is_dir():
        return False
    (t / "DEPS.md").write_text(render_md(target, rows, stop), encoding="utf-8")
    if stop:
        return True
    p = t / "target.properties"
    p.write_text(splice(p.read_text(encoding="utf-8"), render_block(rows)), encoding="utf-8")
    return True


def cmd_resolve(a):
    repo = pathlib.Path(a.repo).resolve()
    if not (repo / ".multiversion/base.properties").exists():
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
    rows, stop = resolve(repo, a.target, checkouts, dry_run=a.dry_run, pins=current_pins(repo, a.target))
    for r in rows:
        state = ("-> " + r["to"]) if r.get("to") else ("DROPPED " + r["drop"]) if r.get("drop") else ("MISSING " + r.get("missing", ""))
        print(f"layer-deps: {a.target} {r['group']}:{r['module']} [{r['role']}] {state}")
    wrote = False if a.dry_run else write(repo, a.target, rows, stop)
    n_sub, n_drop = sum(1 for r in rows if r.get("to")), sum(1 for r in rows if r.get("drop"))
    if stop:
        print(f"layer-deps: STOP {stop}")
        return EXIT_REQUIRED
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
              "mr_id": lambda slug: None}
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
        # control: without a manifest every dependency is required
        (repo / ".github/port-install.json").unlink()
        rows, stop = resolve(repo, "1.21.4", {}, lk)
        expect(stop and "devmod" in stop, "no manifest: every dependency is required")
        # single-version fork: nothing to do, nothing written
        sv = pathlib.Path(td) / "sv"; sv.mkdir()
        (sv / "build.gradle").write_text(build, encoding="utf-8")
        r = cmd_resolve(argparse.Namespace(repo=str(sv), target="1.21.4", port_checkout=[], dry_run=False))
        expect(r == 2 and sorted(p.name for p in sv.iterdir()) == ["build.gradle"], "single-version fork untouched")
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
    tp = repo / ".multiversion/targets" / target / "target.properties"
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
    tp = pathlib.Path(a.repo) / ".multiversion/targets" / a.target / "target.properties"
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
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
