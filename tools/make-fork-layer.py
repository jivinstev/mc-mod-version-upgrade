#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Give a fork port a multi-version layer: build other Minecraft versions from the same branch.

    make-fork-layer.py init       --repo <fork clone>
    make-fork-layer.py add-target --repo <fork clone> --target 1.21.4 --neo 21.4.158 [--inherits 1.21.3]
                                  [--java 21] [--prop geckolib_version=4.8.5 ...] [--substitute a:b=a:c ...]
    make-fork-layer.py --self-check

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
import argparse, pathlib, re, shutil, sys, tempfile

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


def init(repo):
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
    (mv / "base.properties").write_text(f"minecraft_version={base}\n", encoding="utf-8")
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
    print(f"add-target: {a.target} (minecraft {mc_range}, neoforge {neo_range}, java {a.java})")


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
        p = (repo / ".multiversion/targets/1.21.3/target.properties").read_text(encoding="utf-8")
        if "prop.g=1" not in p or "toml.minecraft=[1.21.3,1.21.4)" not in p:
            ok = False; print("FAIL target props")
    print("self-check:", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-check", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("init"); p.add_argument("--repo", required=True)
    p = sub.add_parser("add-target"); p.add_argument("--repo", required=True)
    p.add_argument("--target", required=True); p.add_argument("--neo", required=True)
    p.add_argument("--inherits"); p.add_argument("--java", type=int, default=21)
    p.add_argument("--prop", action="append", default=[], help="k=v passed as -Pk=v")
    p.add_argument("--substitute", action="append", default=[], help="group:name=group:name:version")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    repo = pathlib.Path(a.repo).resolve()
    if a.cmd == "init":
        init(repo)
    elif a.cmd == "add-target":
        add_target(repo, a)
    else:
        ap.print_help(); return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
