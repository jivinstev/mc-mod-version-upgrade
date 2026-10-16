#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Give a fork port a multi-version layer: build other Minecraft versions from the same branch.

    make-fork-layer.py init        --repo <fork clone> [--mv-package net.example.mv] [--upstream REMOTE/BRANCH]
    make-fork-layer.py add-target  --repo <fork clone> --target 1.21.4 [--neo 21.4.158] [--inherits 1.21.3]
                                   [--java 21] [--prop geckolib_version=4.8.5 ...] [--substitute a:b=a:c ...]
                                   [--direct] [--allow-undone-parent]
    make-fork-layer.py freeze      --repo <fork clone> [--upstream REMOTE/BRANCH] [--library owner/repo@TAG ...]
    make-fork-layer.py sync-shared --repo <fork clone> [--check]
    make-fork-layer.py versions    [--refresh]
    make-fork-layer.py --self-check

Which versions to target: templates/fork-layer/versions.json. Only Minecraft versions with a STABLE NeoForge line
are targeted (players install stable NeoForge); 1.21.2, 1.21.6, 1.21.7, 1.21.9, 26.1 and 26.1.1 had beta-only NeoForge
lines and are skipped, with the reason recorded per line. `add-target` refuses a skipped version and fills --neo, --java
and the GeckoLib version (and, where GeckoLib moved Maven group, a dependency substitution) from that file.

Version numbering: 1.21.N is built by NeoForge 21.N.B; from the 26.x calendar line NeoForge carries the whole Minecraft
version (26.2 -> 26.2.0.B, 26.1.2 -> 26.1.2.B), and every Minecraft release is its own line. Versions compare as numeric
tuples, so 26.x sorts after 1.21.11.

The shared library (templates/fork-layer/shared/<t>/: a rename table every fork inherits plus helper classes such
as a 1.21.1-API ShaderInstance on 1.21.2's shader programs) is copied into the fork by `sync-shared`, with the
fork's helper package substituted; `--check` fails when the fork's copy drifted from the migrator's.

`add-target --direct` is for a fork that jumps (1.21.1 -> 1.21.11 without the middle targets): the target inherits
nothing, and `shared_chain=` in its target.properties (filled from versions.json: every targeted version above the
base up to this one) makes `layer.py prepare` still apply the shared rename tables and helpers of the versions in
between, newest first -- the same order a hop-by-hop `inherits` chain has. Without --direct nothing changes.

The layer lives on the fork's `neoforge-1.21.x` branch, which is `neoforge-1.21.1` plus a .multiversion/
directory and ONE commented hook line appended to the authors' build.gradle (design: issue #49):

  .multiversion/base.properties          the base Minecraft version (from gradle.properties)
  .multiversion/layer.gradle             inert without -Pmc; adjusts the authors' build with it
  .multiversion/build.sh <t> [tasks]     builds target t (passes its versions as -P)
  .multiversion/tools/                   layer.py (prepare, props, isolation, compare-jars) + prepare-sources.py
  .multiversion/targets/<t>/target.properties   prop.* (-P values), toml.* (mods.toml version ranges),
                                                java_version, inherits, substitute.*
  .multiversion/targets/<t>/{renames.tsv, java/, resources/, drops.txt}

`init` copies the template and appends the hook; `add-target` writes a target with its versions and seeds
its access transformer from the base one (re-derive it with fix-access-transformer.py --overrides-from).

`add-target` refuses a NEW target (exit 18) while the target it stands on (its inherits, or for --direct the nearest
lower target) is not done by tools/fork-port.py's one definition (target_done: green Gate C, no unreviewed stub, a
complete REVIEW.md for the reviewed commit, TEST-ITEMS.md); `--allow-undone-parent` adds it anyway and records
`allowed_undone_parent=<parent> <date>: <first reason>` in its target.properties (development only).

`freeze` (and `init --upstream`) records the buildout's starting point in base.properties: `upstream=<remote>/<branch>`,
`upstream_commit=` (the merge-base of HEAD and that branch; refused while HEAD lacks commits of it) and
`library.<owner>/<repo>=<tag>` per --library; run it again after keep-current brought the authors' new code in.
tools/fork-port.py stops (exit 19) when the branch moved since the freeze.
Standard library only.
"""
import argparse, datetime, subprocess, json, pathlib, re, shutil, sys, tempfile, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
TPL = ROOT / "templates" / "fork-layer"
HOOK = """
// Multi-version layer (community fork only; not the authors' code). Builds other Minecraft versions from
// .multiversion/ when run with -Pmc=<version>; without -Pmc it does nothing. Deleting this line removes
// multi-version support and leaves the base build exactly as it was.
apply from: '.multiversion/layer.gradle'
"""


def read_kv(path):
    """key=value lines of a .properties file ({} when it is absent)."""
    out = {}
    p = pathlib.Path(path)
    for line in p.read_text(encoding="utf-8").splitlines() if p.is_file() else []:
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def gradle_props(repo):
    out = {}
    for line in (repo / "gradle.properties").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


VERSIONS = TPL / "versions.json"
SHARED = TPL / "shared"


def versions():
    return json.loads(VERSIONS.read_text(encoding="utf-8"))


def line(mc):
    return next((l for l in versions()["lines"] if l["mc"] == mc), None)


def mv_package(repo):
    """The fork's helper package: base.properties' mv_package, else <first two segments of the mod's package>.mv."""
    bp = repo / ".multiversion/base.properties"
    if bp.exists():
        m = re.search(r"(?m)^mv_package=(\S+)", bp.read_text(encoding="utf-8"))
        if m:
            return m.group(1)
    src = repo / "src/main/java"
    first = next(src.rglob("*.java"), None) if src.is_dir() else None
    pkg = re.search(r"(?m)^package\s+([\w.]+)\s*;", first.read_text(encoding="utf-8")) if first else None
    parts = pkg.group(1).split(".") if pkg else ["mv"]
    return ".".join(parts[:2]) + ".mv"


def mod_id(repo):
    toml = repo / "src/main/resources/META-INF/neoforge.mods.toml"
    m = re.search(r'(?m)^\s*modId\s*=\s*"([^"]+)"', toml.read_text(encoding="utf-8")) if toml.exists() else None
    return m.group(1) if m else gradle_props(repo).get("mod_id", "")


def shared_files(repo, target):
    """{fork path: text} the shared library puts in the fork for one target."""
    src = SHARED / target
    if not src.is_dir():
        return {}
    pkg, modid = mv_package(repo), mod_id(repo)
    sub = lambda t: t.replace("@MVPKG@", pkg).replace("@MODID@", modid)
    out = {}
    if (src / "renames.tsv").exists():
        out[f".multiversion/shared/{target}/renames.tsv"] = sub((src / "renames.tsv").read_text(encoding="utf-8"))
    if (src / "accesstransformer.cfg").exists():   # lines the helpers need; fork-port.py's stage adds them to a target's AT
        out[f".multiversion/shared/{target}/accesstransformer.cfg"] = sub((src / "accesstransformer.cfg").read_text(encoding="utf-8"))
    build = "".join((repo / n).read_text(encoding="utf-8", errors="replace") for n in ("build.gradle", "gradle.properties")
                    if (repo / n).is_file())
    for f in sorted((src / "java").glob("*.java")) if (src / "java").is_dir() else []:
        text = f.read_text(encoding="utf-8")
        # `// REQUIRES: <token>` -- a helper built on a mod library (GeckoLib) compiles only in a fork whose build names
        # it; every helper is otherwise installed into every fork, and one that cannot compile breaks the target
        need = re.findall(r"(?m)^// REQUIRES: (\S+)", text)
        if any(n not in build for n in need):
            continue
        out[f".multiversion/shared/{target}/java/{pkg.replace('.', '/')}/{f.name}"] = sub(text)
    return out


def shared_chain_names(repo):
    """Versions named by any target's `shared_chain=` key (the direct-jump targets): their shared library is installed too."""
    out = set()
    tdir = repo / ".multiversion/targets"
    for p in sorted(tdir.glob("*/target.properties")) if tdir.is_dir() else []:
        m = re.search(r"(?m)^shared_chain\s*=\s*(.*?)\s*$", p.read_text(encoding="utf-8"))
        if m:
            out |= {x.strip() for x in m.group(1).split(",") if x.strip()}
    return out


def direct_chain(target):
    """The versions.json lines a direct jump to `target` takes the shared library of: targeted (stable) lines above the
    base, up to and including the target, oldest first."""
    base = versions()["base"]
    return [l["mc"] for l in versions()["lines"]
            if l["stable"] and mc_key(base) < mc_key(l["mc"]) <= mc_key(target)]


# The layer's machinery: init copies it once; sync-shared keeps it current, so a fork whose layer predates a
# layer.gradle change (a target's nfrt_version, exclude. lines) builds the way this migrator's targets now assume.
# A fork's copy is replaced only when it is missing or a verbatim copy of a version this migrator once shipped (its
# git history); a copy someone edited is reported and left alone -- a human merges it (fork-port's layer_patch guard
# for tools/layer.py relies on that).
MACHINERY = ("layer.gradle", "build.sh", "tools/layer.py", "tools/prepare-sources.py")
_HISTORY = {}


def template_history(f):
    """Every committed version of templates/fork-layer/<f> in this migrator's git history (newline-normalised bytes);
    an empty set without git."""
    if f not in _HISTORY:
        rel = (TPL / f).relative_to(ROOT).as_posix()
        out = set()
        try:
            r = subprocess.run(["git", "-C", str(ROOT), "log", "--format=%H", "--", rel], capture_output=True)
            for h in r.stdout.decode().split() if r.returncode == 0 else []:
                s = subprocess.run(["git", "-C", str(ROOT), "show", f"{h}:{rel}"], capture_output=True)
                if s.returncode == 0:
                    out.add(s.stdout.replace(b"\r\n", b"\n"))
        except OSError:
            pass
        _HISTORY[f] = out
    return _HISTORY[f]


def sync_machinery(repo, check=False):
    """(refreshed, diverged): machinery files that differ from the templates. A missing file or a verbatim older
    template copy is written over (mode kept) unless check; an edited copy is only reported."""
    drift, diverged = [], []
    for f in MACHINERY:
        tpl, mine = TPL / f, repo / ".multiversion" / f
        if not tpl.is_file():
            continue
        if mine.is_file():
            have = mine.read_bytes().replace(b"\r\n", b"\n")
            if have == tpl.read_bytes().replace(b"\r\n", b"\n"):
                continue
            if have not in template_history(f):
                diverged.append(f".multiversion/{f}")
                continue
        drift.append(f)
    for f in [] if check else drift:
        (repo / ".multiversion" / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(TPL / f, repo / ".multiversion" / f)
    return [f".multiversion/{f}" for f in drift], diverged


def sync_shared(repo, check=False):
    machinery, diverged = sync_machinery(repo, check) if (repo / ".multiversion").is_dir() else ([], [])
    for k in diverged:
        print(f"sync-shared: DIVERGED {k} is not a copy of any template version this migrator shipped; left alone "
              f"(merge templates/fork-layer/{k.split('/', 1)[1]} into it by hand)")
    targets = sorted({p.name for p in (repo / ".multiversion/targets").iterdir() if p.is_dir()} | shared_chain_names(repo)) \
        if (repo / ".multiversion/targets").is_dir() else []
    want = {}
    for t in targets:
        want.update(shared_files(repo, t))
    have = {p.relative_to(repo).as_posix(): p for p in (repo / ".multiversion/shared").rglob("*") if p.is_file()} \
        if (repo / ".multiversion/shared").is_dir() else {}
    stale = sorted(set(have) - set(want))
    drift = sorted(k for k, v in want.items() if k not in have or have[k].read_text(encoding="utf-8") != v)
    if check:
        for k in machinery:
            print(f"sync-shared: {k} differs from the migrator's layer template")
        for k in drift + stale:
            print(f"sync-shared: {k} differs from the migrator's shared library")
        print(f"sync-shared: {len(want)} file(s) for {', '.join(targets) or 'no targets'}: "
              f"{'DRIFT' if drift or stale or machinery or diverged else 'current'}")
        return 1 if drift or stale or machinery or diverged else 0
    for k in stale:
        (repo / k).unlink()
    for k in drift:
        (repo / k).parent.mkdir(parents=True, exist_ok=True)
        (repo / k).write_text(want[k], encoding="utf-8")
    print(f"sync-shared: wrote {len(drift)}, removed {len(stale)}, {len(want)} file(s) in .multiversion/shared")
    if machinery:
        print(f"sync-shared: refreshed the layer machinery from the template: {', '.join(machinery)}")
    return 0


def neo_line(v):
    """(Minecraft version, build) a NeoForge version builds, or None for a beta or a version of neither scheme.
    21.N.B -> 1.21.N (21.0.B -> 1.21); from 26.x the Minecraft version is the first three parts with a trailing .0
    dropped: 26.2.0.B -> 26.2, 26.1.2.B -> 26.1.2."""
    m = re.match(r"21\.(\d+)\.(\d+)$", v)
    if m:
        n = int(m.group(1))
        return ("1.21" if n == 0 else f"1.21.{n}"), int(m.group(2))
    m = re.match(r"(\d+)\.(\d+)\.(\d+)\.(\d+)$", v)
    if m and int(m.group(1)) >= 26:
        a, b, c = m.group(1), m.group(2), int(m.group(3))
        return (f"{a}.{b}" if c == 0 else f"{a}.{b}.{c}"), int(m.group(4))
    return None


def neo_of(mc, build):
    """The NeoForge version string for a Minecraft version and build: the inverse of neo_line."""
    p = mc.split(".")
    if p[0] == "1":
        return f"21.{p[2] if len(p) > 2 else 0}.{build}"
    return f"{p[0]}.{p[1]}.{p[2] if len(p) > 2 else 0}.{build}"


def newest_stable(xml):
    """{Minecraft version: newest stable NeoForge version string} from maven-metadata.xml."""
    newest = {}
    for v in re.findall(r"<version>([^<]+)</version>", xml):
        nl = neo_line(v)
        if nl:
            newest[nl[0]] = max(newest.get(nl[0], 0), nl[1])
    return {mc: neo_of(mc, b) for mc, b in newest.items()}


def refresh_versions():
    """Newest stable (non-beta) NeoForge per line (1.21.x and 26.x), from the NeoForge maven."""
    url = "https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml"
    newest = newest_stable(urllib.request.urlopen(url, timeout=30).read().decode("utf-8"))
    known = {l["mc"] for l in versions()["lines"]}
    for l in versions()["lines"]:
        print(f"{l['mc']:8} recorded {l['neo'] or '-':10} newest stable on maven: "
              f"{newest.get(l['mc'], 'none (beta only)')}"
              + (f"   skipped: {l['skip']}" if l.get("skip") else ""))
    for mc in sorted(set(newest) - known, key=mc_key):
        if mc_key(mc) > mc_key(versions()["base"]):
            print(f"{mc:8} NOT IN versions.json: newest stable on maven {newest[mc]} -- add a line for it")


def init(repo, mv_pkg=None):
    mv = repo / ".multiversion"
    mv.mkdir(exist_ok=True)
    (mv / "tools").mkdir(exist_ok=True)
    for f in ("layer.gradle", "build.sh"):
        shutil.copy2(TPL / f, mv / f)
    for f in ("layer.py", "prepare-sources.py"):
        shutil.copy2(TPL / "tools" / f, mv / "tools" / f)
    (mv / "targets").mkdir(exist_ok=True)
    base = gradle_props(repo).get("minecraft_version")
    if not base:
        sys.exit("gradle.properties has no minecraft_version: this build hard-codes its versions, so the layer "
                 "needs a second hook line here (issue #49); not automated")
    (mv / "base.properties").write_text(f"minecraft_version={base}\nmv_package={mv_pkg or mv_package(repo)}\n",
                                        encoding="utf-8")
    bg = repo / "build.gradle"
    text = bg.read_text(encoding="utf-8")
    if HOOK.strip().splitlines()[-1] not in text:
        bg.write_text(text.rstrip("\n") + "\n" + HOOK, encoding="utf-8")
    print(f"init: .multiversion/ written (base {base}); hook appended to build.gradle")


def ranges(target, neo):
    """(minecraft, neoforge) mods.toml version ranges for a target: one NeoForge line each.
    1.21.N: [1.21.N,1.21.N+1) and [21.N,21.N+1). 26.x: every Minecraft release is its own NeoForge line (26.1, 26.1.1
    and 26.1.2 are three; NeoForge 26.2.0.B builds 26.2), so both ranges stop at the next patch release:
    [26.2,26.2.1) and [26.1.2,26.1.3), the same interval for NeoForge since its version starts with the Minecraft one
    (26.2.0.90 is in [26.2,26.2.1)). The NeoForge MDK pins the Minecraft version exactly ([26.2]); a half-open range
    to the next patch admits the same releases."""
    parts = [int(x) for x in target.split(".")]
    if parts[0] >= 26:
        nxt = ".".join(map(str, parts[:2] + [parts[2] + 1 if len(parts) > 2 else 1]))
        return f"[{target},{nxt})", f"[{target},{nxt})"
    nxt = ".".join(map(str, parts[:-1] + [parts[-1] + 1])) if len(parts) == 3 else f"{target}.1"
    major, minor = neo.split(".")[:2]
    return f"[{target},{nxt})", f"[{major}.{minor},{major}.{int(minor) + 1})"


def substitute_line(s):
    """`group:name=group:name:version` as a target.properties line. The key's ':' is escaped: java.util.Properties
    (layer.gradle reads the file with it) ends a key at the first unescaped ':' as well as '=', so an unescaped
    `substitute.a:b=a:c:1` would load as key `substitute.a`."""
    k, _, v = s.partition("=")
    return f"{k.replace(':', chr(92) + ':')}={v}"


def mc_key(v):
    return tuple(int(x) for x in v.split(".") if x.isdigit())


def fork_port():
    """tools/fork-port.py as a module: the one definition of a done target (target_done) and the upstream freeze."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("fork_port", ROOT / "tools/fork-port.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def freeze(repo, upstream=None, libraries=()):
    rec, why = fork_port().freeze_upstream(repo, upstream, libraries)
    if not rec:
        print(f"freeze: {why}"); return 2
    print("freeze: " + ", ".join(f"{k}={v}" for k, v in rec.items()) + " (.multiversion/base.properties)")
    return 0


def add_target(repo, a):
    mv = repo / ".multiversion"
    if not (mv / "base.properties").exists():
        sys.exit("run init first")
    ln = line(a.target)
    if ln is None:
        sys.exit(f"{a.target} is not in templates/fork-layer/versions.json: measure it first (make-fork-layer.py versions --refresh)")
    if not ln["stable"]:
        sys.exit(f"{a.target} is not targeted: {ln['skip']}")
    a.neo = a.neo or ln["neo"]
    a.java = a.java or ln.get("java", 21)
    if ln.get("geckolib") and "geckolib_version" in gradle_props(repo) and not any(p.startswith("geckolib_version=") for p in a.prop):
        a.prop = [*a.prop, f"geckolib_version={ln['geckolib']}"]
        old = f"software.bernie.geckolib:geckolib-neoforge-{a.target}"
        if ln.get("geckolib_group") and not any(s.startswith(old + "=") for s in a.substitute):
            # GeckoLib 5.5+ publishes under a new group; the authors' build names the old one
            a.substitute = [*a.substitute, f"{old}={ln['geckolib_group']}:geckolib-neoforge-{a.target}:{ln['geckolib']}"]
    if a.inherits is None and not getattr(a, "direct", False):      # the nearest lower target the fork already has
        have = {p.name for p in (mv / "targets").iterdir() if p.is_dir()}
        order = [l["mc"] for l in versions()["lines"]]
        lower = [m for m in order[:order.index(a.target)] if m in have]
        a.inherits = lower[-1] if lower else None
    t = mv / "targets" / a.target
    allowed = read_kv(t / "target.properties").get("allowed_undone_parent")
    if not t.is_dir():          # never start target N+1 before N is done (fork-versions skill, "Done")
        fp = fork_port()
        und = fp.undone_parent_stop(repo, a.target, parent=a.inherits)   # the parent it WILL inherit (--inherits)
        if und:
            p, why = und
            if not getattr(a, "allow_undone_parent", False):
                print(f"add-target: REFUSED {a.target}: the target it stands on, {p}, is not done:")
                print("\n".join(f"  - {w}" for w in why[:12]))
                print(f"add-target: finish {p} (python3 tools/fork-port.py --repo {repo} --target {p} --check-done says what is "
                      f"open), or for development only pass --allow-undone-parent (recorded; {a.target} is then not done until {p} is)")
                raise SystemExit(fp.UNDONE_EXIT)
            allowed = f"{p} {datetime.date.today().isoformat()}: {why[0][:160]}"
            print(f"add-target: WARNING {p} is not done ({len(why)} open); adding {a.target} anyway (--allow-undone-parent, recorded)")
    t.mkdir(parents=True, exist_ok=True)
    mc_range, neo_range = ranges(a.target, a.neo)
    lines = [f"# Minecraft {a.target} on NeoForge {a.neo}", f"prop.minecraft_version={a.target}",
             f"prop.neo_version={a.neo}"]
    lines += [f"prop.{p}" for p in a.prop]
    lines += [f"toml.minecraft={mc_range}", f"toml.neoforge={neo_range}", f"java_version={a.java}"]
    if ln.get("nfrt"):                # a NeoForm runtime newer than the authors' ModDevGradle brings (layer.gradle)
        lines.append(f"nfrt_version={ln['nfrt']}")
    if a.inherits:
        lines.append(f"inherits={a.inherits}")
    if getattr(a, "direct", False):     # a jump: still pull the shared library of every targeted version in between
        lines.append("shared_chain=" + ",".join(direct_chain(a.target)))
    lines += [f"substitute.{substitute_line(s)}" for s in a.substitute]
    if mc_key(a.target) >= mc_key("1.21.6"):   # CATALOG V79: @OnlyIn strips nothing from 1.21.6 and fails a dev run
        lines.append("strip_onlyin=true")
    if allowed:                                 # the recorded escape (kept when the target is re-added)
        lines.append(f"allowed_undone_parent={allowed}")
    (t / "target.properties").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not (t / "renames.tsv").exists():
        (t / "renames.tsv").write_text(f"# {a.target}: from<TAB>to (plain type, member:, re:) -- see prepare-sources.py\n",
                                       encoding="utf-8")
    at = repo / "src/main/resources/META-INF/accesstransformer.cfg"
    dst = t / "resources/META-INF/accesstransformer.cfg"
    if at.exists() and not dst.exists() and not a.inherits:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(at, dst)
    print(f"add-target: {a.target} (minecraft {mc_range}, neoforge {neo_range}, java {a.java}"
          f"{', inherits ' + a.inherits if a.inherits else ''}"
          f"{', direct: shared libraries ' + ' '.join(direct_chain(a.target)) if getattr(a, 'direct', False) else ''})")
    lp = mv / "tools/layer.py"
    if getattr(a, "direct", False) and lp.is_file() and "shared_chain" not in lp.read_text(encoding="utf-8"):
        print("add-target: WARNING .multiversion/tools/layer.py predates shared_chain and would IGNORE it; "
              "run tools/fork-port.py (its setup stage replaces a verbatim older copy) or copy templates/fork-layer/tools/layer.py")
    sync_shared(repo)


def self_check():
    ok = True
    if ranges("1.21.4", "21.4.158") != ("[1.21.4,1.21.5)", "[21.4,21.5)"):
        ok = False; print("FAIL ranges")
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d)
        (repo / "gradle.properties").write_text("minecraft_version=1.21.1\nneo_version=21.1.1\n", encoding="utf-8")
        (repo / "build.gradle").write_text("plugins {}\n", encoding="utf-8")
        init(repo); init(repo)
        if (repo / "build.gradle").read_text(encoding="utf-8").count("apply from: '.multiversion/layer.gradle'") != 1:
            ok = False; print("FAIL hook not idempotent")
        ns = argparse.Namespace(target="1.21.3", neo="21.3.97", inherits=None, java=21, prop=["g=1"], substitute=[])
        add_target(repo, ns)
        # through the real command line too: --prop/--substitute once hung off the wrong subcommand, and a
        # Namespace built here could not see it (a GeckoLib mod's add-target crashed on a.prop)
        (repo / "gradle.properties").write_text("minecraft_version=1.21.1\nneo_version=21.1.1\ngeckolib_version=4.7\n",
                                                encoding="utf-8")
        r = subprocess.run([sys.executable, __file__, "add-target", "--repo", str(repo), "--target", "1.21.4"],
                           capture_output=True, text=True, encoding="utf-8")
        if r.returncode != 18 or "REFUSED 1.21.4" not in r.stdout or "1.21.3" not in r.stdout or \
                (repo / ".multiversion/targets/1.21.4").exists():
            ok = False; print("FAIL add-target over an undone parent is refused (exit 18) and writes nothing:", r.returncode, r.stdout[-300:])
        r = subprocess.run([sys.executable, __file__, "add-target", "--repo", str(repo), "--target", "1.21.4", "--allow-undone-parent"],
                           capture_output=True, text=True, encoding="utf-8")
        tp = repo / ".multiversion/targets/1.21.4/target.properties"
        if r.returncode != 0 or not tp.exists() or "prop.geckolib_version=" not in tp.read_text(encoding="utf-8"):
            ok = False; print("FAIL add-target from the command line:", (r.stdout + r.stderr)[-300:])
        if not re.search(r"(?m)^allowed_undone_parent=1\.21\.3 \d{4}-\d\d-\d\d: no green Gate C", tp.read_text(encoding="utf-8")):
            ok = False; print("FAIL --allow-undone-parent is recorded in target.properties:", tp.read_text(encoding="utf-8"))
        p = (repo / ".multiversion/targets/1.21.3/target.properties").read_text(encoding="utf-8")
        if "prop.g=1" not in p or "toml.minecraft=[1.21.3,1.21.4)" not in p:
            ok = False; print("FAIL target props")
        try:
            add_target(repo, argparse.Namespace(target="1.21.2", neo=None, inherits=None, java=None, prop=[], substitute=[]))
            ok = False; print("FAIL a beta-only line was accepted")
        except SystemExit as e:
            if "beta" not in str(e):
                ok = False; print("FAIL wrong refusal:", e)
        add_target(repo, argparse.Namespace(target="1.21.4", neo=None, inherits=None, java=None, prop=[], substitute=[]))
        if "allowed_undone_parent=1.21.3" not in (repo / ".multiversion/targets/1.21.4/target.properties").read_text(encoding="utf-8"):
            ok = False; print("FAIL re-adding an existing target keeps its recorded escape")
        p4 = (repo / ".multiversion/targets/1.21.4/target.properties").read_text(encoding="utf-8")
        if "inherits=1.21.3" not in p4 or "prop.neo_version=21.4.158" not in p4:
            ok = False; print("FAIL 1.21.4 defaults from versions.json")
        if sync_shared(repo, check=True) != 0:
            ok = False; print("FAIL shared library drift right after sync")
        # --direct: no inherits, shared_chain from versions.json, and the middle versions' shared libraries installed;
        # without it the target.properties has no shared_chain (default off)
        if "shared_chain" in p4 or "shared_chain" in p:
            ok = False; print("FAIL a hop target got shared_chain")
        add_target(repo, argparse.Namespace(target="1.21.11", neo=None, inherits=None, java=None, prop=[], substitute=[], direct=True,
                                            allow_undone_parent=True))
        pd = (repo / ".multiversion/targets/1.21.11/target.properties").read_text(encoding="utf-8")
        want = "shared_chain=1.21.3,1.21.4,1.21.5,1.21.8,1.21.10,1.21.11"
        if want not in pd or "inherits=" in pd:
            ok = False; print("FAIL --direct properties:", pd)
        if not (repo / ".multiversion/shared/1.21.8/renames.tsv").is_file() or sync_shared(repo, check=True) != 0:
            ok = False; print("FAIL --direct did not install the intermediate shared libraries")
        if direct_chain("1.21.5") != ["1.21.3", "1.21.4", "1.21.5"]:
            ok = False; print("FAIL direct_chain skips beta-only lines and stops at the target")
    ok = self_check_26x() and ok
    ok = self_check_done_parent() and ok
    print("self-check:", "OK" if ok else "FAILED")
    return 0 if ok else 1


def self_check_done_parent():
    """A done parent lets the next target in without the escape; `freeze` / `init --upstream` record the authors' commit."""
    ok = True

    def expect(c, msg):
        nonlocal ok
        if not c:
            ok = False; print("FAIL", msg)
    with tempfile.TemporaryDirectory() as d:
        up, repo = pathlib.Path(d) / "authors", pathlib.Path(d) / "fork"
        up.mkdir()
        g = lambda r, *x: subprocess.run(["git", "-C", str(r), *x], capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()
        g(up, "init", "-q", "-b", "main"); g(up, "config", "user.email", "a@b"); g(up, "config", "user.name", "a")
        (up / "gradle.properties").write_text("minecraft_version=1.21.1\nneo_version=21.1.1\n", encoding="utf-8")
        (up / "build.gradle").write_text("plugins {}\n", encoding="utf-8")
        g(up, "add", "-A"); g(up, "commit", "-qm", "authors")
        subprocess.run(["git", "clone", "-q", "-o", "upstream", str(up), str(repo)], check=True, capture_output=True)
        g(repo, "config", "user.email", "a@b"); g(repo, "config", "user.name", "a")
        r = subprocess.run([sys.executable, __file__, "init", "--repo", str(repo), "--upstream", "upstream/main"],
                           capture_output=True, text=True, encoding="utf-8")
        bp = read_kv(repo / ".multiversion/base.properties")
        expect(r.returncode == 0 and bp.get("upstream") == "upstream/main" and bp.get("upstream_commit") == g(up, "rev-parse", "HEAD")
               and bp.get("minecraft_version") == "1.21.1", f"init --upstream freezes the authors' commit: {bp} {r.stdout}")
        r = subprocess.run([sys.executable, __file__, "freeze", "--repo", str(repo), "--library", "owner/lib@v3"],
                           capture_output=True, text=True, encoding="utf-8")
        expect(r.returncode == 0 and read_kv(repo / ".multiversion/base.properties").get("library.owner/lib") == "v3",
               f"freeze --library records a sibling library's release: {r.stdout}")
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            add_target(repo, argparse.Namespace(target="1.21.3", neo=None, inherits=None, java=None, prop=[], substitute=[]))
        t3 = repo / ".multiversion/targets/1.21.3"
        (t3 / "TEST-ITEMS.md").write_text("# Manual-test items: Minecraft 1.21.3\n\n- none\n", encoding="utf-8")
        r = subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve().parent / "layer-inventory.py"), "build",
                            "--repo", str(repo)], capture_output=True, text=True, encoding="utf-8")   # done item (6)
        expect(r.returncode == 0, f"the base behaviour inventory is built: {r.stdout}")
        g(repo, "add", "-A"); g(repo, "commit", "-qm", "1.21.3")
        (t3 / "GATES.md").write_text(f"Gates for `examplemod` at `{g(repo, 'rev-parse', '--short', 'HEAD')}` for Minecraft 1.21.3 "
                                     "(local environment):\n\n| Gate C -- real client, `launch` | PASS | ok |\n", encoding="utf-8")
        (t3 / "REVIEW.md").write_text(f"- reviewer: fresh agent\n- date: 2026-10-16\n- commit: {g(repo, 'rev-parse', 'HEAD')}\n",
                                      encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            add_target(repo, argparse.Namespace(target="1.21.4", neo=None, inherits=None, java=None, prop=[], substitute=[]))
        p4 = (repo / ".multiversion/targets/1.21.4/target.properties").read_text(encoding="utf-8")
        expect("inherits=1.21.3" in p4 and "allowed_undone_parent" not in p4 and "WARNING" not in buf.getvalue(),
               f"a done parent: the next target is added as before, nothing recorded: {p4}")
        # --inherits names the parent the done check judges: 1.21.5 on the done 1.21.3 is let in although the nearest
        # lower target (1.21.4, just added) is not done; 1.21.8 on the undone 1.21.4 is refused, and the escape
        # records 1.21.4
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                add_target(repo, argparse.Namespace(target="1.21.5", neo=None, inherits="1.21.3", java=None, prop=[], substitute=[]))
            p5 = (repo / ".multiversion/targets/1.21.5/target.properties").read_text(encoding="utf-8")
        except SystemExit as e:
            p5 = f"refused: exit {e.code}: {buf.getvalue()}"
        expect("inherits=1.21.3" in p5 and "allowed_undone_parent" not in p5,
               f"--inherits 1.21.3 (done) is judged, not the nearest lower 1.21.4: {p5}")
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                add_target(repo, argparse.Namespace(target="1.21.8", neo=None, inherits="1.21.4", java=None, prop=[], substitute=[]))
            refused = None
        except SystemExit as e:
            refused = e.code
        expect(refused == 18 and "1.21.4" in buf.getvalue(), f"--inherits 1.21.4 (not done) is refused: {refused} {buf.getvalue()}")
        with contextlib.redirect_stdout(io.StringIO()):
            add_target(repo, argparse.Namespace(target="1.21.8", neo=None, inherits="1.21.4", java=None, prop=[], substitute=[],
                                                allow_undone_parent=True))
        p8 = (repo / ".multiversion/targets/1.21.8/target.properties").read_text(encoding="utf-8")
        expect("allowed_undone_parent=1.21.4 " in p8 and "inherits=1.21.4" in p8, f"the escape records the --inherits parent: {p8}")
    return ok


def props_key(line):
    """The key java.util.Properties reads from one line: up to the first unescaped '=' or ':' (escapes removed)."""
    out, i = [], 0
    while i < len(line) and line[i] not in "=:":
        if line[i] == "\\" and i + 1 < len(line):
            i += 1
        out.append(line[i]); i += 1
    return "".join(out)


def self_check_26x():
    """The 26.x calendar line: numbering, ranges, the maven parse, and add-target's defaults (Java 25, the inherits
    chain 1.21.11 -> 26.1.2 -> 26.2 -> 26.3, GeckoLib's new Maven group as an escaped substitution)."""
    ok = True
    def expect(c, msg):
        nonlocal ok
        if not c:
            ok = False; print("FAIL", msg)
    expect(ranges("26.2", "26.2.0.90") == ("[26.2,26.2.1)", "[26.2,26.2.1)"), f"26.2 ranges {ranges('26.2', '26.2.0.90')}")
    expect(ranges("26.1.2", "26.1.2.116") == ("[26.1.2,26.1.3)", "[26.1.2,26.1.3)"), "26.1.2 ranges")
    expect(ranges("1.21.11", "21.11.45") == ("[1.21.11,1.21.12)", "[21.11,21.12)"), "1.21.11 ranges unchanged")
    expect(sorted(["26.1.2", "1.21.11", "26.3", "26.2", "1.21.8"], key=mc_key) == ["1.21.8", "1.21.11", "26.1.2", "26.2", "26.3"],
           "26.x sorts after 1.21.11, 26.1.2 before 26.2")
    expect([neo_line(v) for v in ("21.4.158", "26.2.0.90", "26.1.2.116", "26.1.0.19-beta", "21.6.0-beta", "20.4.1")]
           == [("1.21.4", 158), ("26.2", 90), ("26.1.2", 116), None, None, None], "neo_line")
    xml = "".join(f"<version>{v}</version>" for v in ("21.11.44", "21.11.45", "26.1.0.19-beta", "26.1.1.15-beta",
                                                       "26.1.2.9", "26.1.2.116", "26.2.0.90", "26.3.0.69-beta", "26.3.0.76"))
    expect(newest_stable(xml) == {"1.21.11": "21.11.45", "26.1.2": "26.1.2.116", "26.2": "26.2.0.90", "26.3": "26.3.0.76"},
           f"newest_stable: {newest_stable(xml)}")
    expect(neo_of("1.21.4", 158) == "21.4.158" and neo_of("26.2", 90) == "26.2.0.90", "neo_of")
    expect(props_key(substitute_line("a.b:c-26.2=x.y:c-26.2:5")) == "a.b:c-26.2", "an escaped substitute key loads whole")
    expect(props_key("substitute.a.b:c=x") == "substitute.a.b", "control: an unescaped one is cut at the ':'")
    expect(direct_chain("26.3")[-3:] == ["26.1.2", "26.2", "26.3"] and "26.1" not in direct_chain("26.3"), "direct_chain to 26.3")
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d)
        (repo / "gradle.properties").write_text("minecraft_version=1.21.1\nneo_version=21.1.1\ngeckolib_version=4.8.4\n",
                                                encoding="utf-8")
        (repo / "build.gradle").write_text("plugins {}\n", encoding="utf-8")
        init(repo)
        ns = lambda t: argparse.Namespace(target=t, neo=None, inherits=None, java=None, prop=[], substitute=[], allow_undone_parent=True)
        for t in ("1.21.11", "26.1.2", "26.2", "26.3"):
            add_target(repo, ns(t))
        props = {t: (repo / f".multiversion/targets/{t}/target.properties").read_text(encoding="utf-8")
                 for t in ("1.21.11", "26.1.2", "26.2", "26.3")}
        expect("java_version=21" in props["1.21.11"] and "substitute." not in props["1.21.11"] and "nfrt" not in props["1.21.11"]
               and "prop.geckolib_version=5.4.5" in props["1.21.11"], "1.21.11 keeps Java 21, no substitution")
        for t, inh, neo, gl in (("26.1.2", "1.21.11", "26.1.2.116", "5.5.2"), ("26.2", "26.1.2", "26.2.0.90", "5.5.6"),
                                ("26.3", "26.2", "26.3.0.76", "5.5.7")):
            p = props[t]
            expect(f"inherits={inh}\n" in p and "java_version=25" in p and f"prop.neo_version={neo}" in p
                   and f"prop.geckolib_version={gl}" in p and "strip_onlyin=true" in p, f"{t} defaults: {p}")
            sub = f"substitute.software.bernie.geckolib\\:geckolib-neoforge-{t}=com.geckolib:geckolib-neoforge-{t}:{gl}\n"
            expect(sub in p, f"{t} GeckoLib group substitution: {p}")
            expect(("nfrt_version=2.0.31\n" in p) == (t != "26.1.2"), f"{t} nfrt_version from versions.json: {p}")
        expect("toml.minecraft=[26.2,26.2.1)" in props["26.2"] and "toml.neoforge=[26.2,26.2.1)" in props["26.2"], "26.2 toml")
        try:
            add_target(repo, ns("26.1.1"))
            expect(False, "a beta-only 26.x line was accepted")
        except SystemExit as e:
            expect("beta" in str(e), f"wrong refusal: {e}")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-check", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("init"); p.add_argument("--repo", required=True); p.add_argument("--mv-package")
    p.add_argument("--upstream", help="<remote>/<branch>: the authors' branch; records the commit the buildout starts from")
    p = sub.add_parser("add-target"); p.add_argument("--repo", required=True)
    p.add_argument("--target", required=True); p.add_argument("--neo")
    p.add_argument("--inherits"); p.add_argument("--java", type=int)
    p.add_argument("--direct", action="store_true",
                   help="a jump: no inherits, and shared_chain= lists every targeted version up to this one so layer.py "
                        "still applies their shared libraries (default off: one hop from the nearest lower target)")
    p.add_argument("--prop", action="append", default=[], help="k=v passed as -Pk=v")
    p.add_argument("--substitute", action="append", default=[], help="group:name=group:name:version")
    p.add_argument("--allow-undone-parent", action="store_true",
                   help="development only: add a new target although the target it stands on is not done (recorded)")
    p = sub.add_parser("freeze"); p.add_argument("--repo", required=True)
    p.add_argument("--upstream", help="<remote>/<branch> (default: the recorded one, else the one `upstream` remote branch HEAD carries)")
    p.add_argument("--library", action="append", default=[], help="owner/repo@TAG: a sibling library's layer release")
    p = sub.add_parser("sync-shared"); p.add_argument("--repo", required=True); p.add_argument("--check", action="store_true")
    p = sub.add_parser("versions"); p.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if a.cmd == "versions":
        if a.refresh:
            refresh_versions()
        else:
            for l in versions()["lines"]:
                print(f"{l['mc']:8} {l['neo'] or '-':10} {'target' if l['stable'] else 'skipped: ' + l['skip']}")
        return 0
    repo = pathlib.Path(a.repo).resolve()
    if a.cmd == "sync-shared":
        return sync_shared(repo, a.check)
    if a.cmd == "freeze":
        return freeze(repo, a.upstream, a.library)
    if a.cmd == "init":
        init(repo, a.mv_package)
        if a.upstream:
            return freeze(repo, a.upstream)
    elif a.cmd == "add-target":
        add_target(repo, a)
    else:
        ap.print_help(); return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
