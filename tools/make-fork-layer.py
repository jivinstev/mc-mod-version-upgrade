#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Give a fork port a multi-version layer: build other Minecraft versions from the same branch.

    make-fork-layer.py init        --repo <fork clone> [--mv-package net.example.mv]
    make-fork-layer.py add-target  --repo <fork clone> --target 1.21.4 [--neo 21.4.158] [--inherits 1.21.3]
                                   [--java 21] [--prop geckolib_version=4.8.5 ...] [--substitute a:b=a:c ...]
    make-fork-layer.py sync-shared --repo <fork clone> [--check]
    make-fork-layer.py versions    [--refresh]
    make-fork-layer.py --self-check

Which versions to target: templates/fork-layer/versions.json. Only Minecraft versions with a STABLE NeoForge line
are targeted (players install stable NeoForge); 1.21.2, 1.21.6, 1.21.7 and 1.21.9 had beta-only NeoForge lines and
are skipped, with the reason recorded per line. `add-target` refuses a skipped version and fills --neo, --java and
the GeckoLib version from that file.

The shared library (templates/fork-layer/shared/<t>/: a rename table every fork inherits plus helper classes such
as a 1.21.1-API ShaderInstance on 1.21.2's shader programs) is copied into the fork by `sync-shared`, with the
fork's helper package substituted; `--check` fails when the fork's copy drifted from the migrator's.

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
Standard library only.
"""
import argparse, subprocess, json, pathlib, re, shutil, sys, tempfile, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
TPL = ROOT / "templates" / "fork-layer"
HOOK = """
// Multi-version layer (community fork only; not the authors' code). Builds other Minecraft versions from
// .multiversion/ when run with -Pmc=<version>; without -Pmc it does nothing. Deleting this line removes
// multi-version support and leaves the base build exactly as it was.
apply from: '.multiversion/layer.gradle'
"""


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
    for f in sorted((src / "java").glob("*.java")) if (src / "java").is_dir() else []:
        out[f".multiversion/shared/{target}/java/{pkg.replace('.', '/')}/{f.name}"] = sub(f.read_text(encoding="utf-8"))
    return out


def sync_shared(repo, check=False):
    targets = sorted(p.name for p in (repo / ".multiversion/targets").iterdir() if p.is_dir()) \
        if (repo / ".multiversion/targets").is_dir() else []
    want = {}
    for t in targets:
        want.update(shared_files(repo, t))
    have = {p.relative_to(repo).as_posix(): p for p in (repo / ".multiversion/shared").rglob("*") if p.is_file()} \
        if (repo / ".multiversion/shared").is_dir() else {}
    stale = sorted(set(have) - set(want))
    drift = sorted(k for k, v in want.items() if k not in have or have[k].read_text(encoding="utf-8") != v)
    if check:
        for k in drift + stale:
            print(f"sync-shared: {k} differs from the migrator's shared library")
        print(f"sync-shared: {len(want)} file(s) for {', '.join(targets) or 'no targets'}: "
              f"{'DRIFT' if drift or stale else 'current'}")
        return 1 if drift or stale else 0
    for k in stale:
        (repo / k).unlink()
    for k in drift:
        (repo / k).parent.mkdir(parents=True, exist_ok=True)
        (repo / k).write_text(want[k], encoding="utf-8")
    print(f"sync-shared: wrote {len(drift)}, removed {len(stale)}, {len(want)} file(s) in .multiversion/shared")
    return 0


def refresh_versions():
    """Newest stable (non-beta) NeoForge per 21.N line, from the NeoForge maven."""
    url = "https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml"
    xml = urllib.request.urlopen(url, timeout=30).read().decode("utf-8")
    newest = {}
    for v in re.findall(r"<version>([^<]+)</version>", xml):
        m = re.match(r"21\.(\d+)\.(\d+)$", v)
        if m:
            newest[int(m.group(1))] = max(newest.get(int(m.group(1)), 0), int(m.group(2)))
    for l in versions()["lines"]:
        n = int(l["mc"].split(".")[2]) if l["mc"].count(".") == 2 else 0
        print(f"{l['mc']:8} recorded {l['neo'] or '-':10} newest stable on maven: "
              f"{'21.%d.%d' % (n, newest[n]) if n in newest else 'none (beta only)'}"
              + (f"   skipped: {l['skip']}" if l.get("skip") else ""))


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
    parts = [int(x) for x in target.split(".")]
    nxt = ".".join(map(str, parts[:-1] + [parts[-1] + 1])) if len(parts) == 3 else f"{target}.1"
    major, minor = neo.split(".")[:2]
    return f"[{target},{nxt})", f"[{major}.{minor},{major}.{int(minor) + 1})"


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
    if a.inherits is None:      # the nearest lower target the fork already has
        have = {p.name for p in (mv / "targets").iterdir() if p.is_dir()}
        order = [l["mc"] for l in versions()["lines"]]
        lower = [m for m in order[:order.index(a.target)] if m in have]
        a.inherits = lower[-1] if lower else None
    t = mv / "targets" / a.target
    t.mkdir(parents=True, exist_ok=True)
    mc_range, neo_range = ranges(a.target, a.neo)
    lines = [f"# Minecraft {a.target} on NeoForge {a.neo}", f"prop.minecraft_version={a.target}",
             f"prop.neo_version={a.neo}"]
    lines += [f"prop.{p}" for p in a.prop]
    lines += [f"toml.minecraft={mc_range}", f"toml.neoforge={neo_range}", f"java_version={a.java}"]
    if a.inherits:
        lines.append(f"inherits={a.inherits}")
    lines += [f"substitute.{s}" for s in a.substitute]
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
          f"{', inherits ' + a.inherits if a.inherits else ''})")
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
                           capture_output=True, text=True)
        tp = repo / ".multiversion/targets/1.21.4/target.properties"
        if r.returncode != 0 or not tp.exists() or "prop.geckolib_version=" not in tp.read_text(encoding="utf-8"):
            ok = False; print("FAIL add-target from the command line:", (r.stdout + r.stderr)[-300:])
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
        p4 = (repo / ".multiversion/targets/1.21.4/target.properties").read_text(encoding="utf-8")
        if "inherits=1.21.3" not in p4 or "prop.neo_version=21.4.158" not in p4:
            ok = False; print("FAIL 1.21.4 defaults from versions.json")
        if sync_shared(repo, check=True) != 0:
            ok = False; print("FAIL shared library drift right after sync")
    print("self-check:", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-check", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("init"); p.add_argument("--repo", required=True); p.add_argument("--mv-package")
    p = sub.add_parser("add-target"); p.add_argument("--repo", required=True)
    p.add_argument("--target", required=True); p.add_argument("--neo")
    p.add_argument("--inherits"); p.add_argument("--java", type=int)
    p.add_argument("--prop", action="append", default=[], help="k=v passed as -Pk=v")
    p.add_argument("--substitute", action="append", default=[], help="group:name=group:name:version")
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
    if a.cmd == "init":
        init(repo, a.mv_package)
    elif a.cmd == "add-target":
        add_target(repo, a)
    else:
        ap.print_help(); return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
