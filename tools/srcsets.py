#!/usr/bin/env python3
"""The author's Java source sets and Gradle tasks, so every tool covers ALL of them, not just src/main.

    python3 tools/srcsets.py --repo <gradle project>        # prints dirs, compile tasks, author jar tasks, target

A mod can ship more than one artifact from one tree -- a CurseForge variant, a "pro" build, a JVMTI backend --
each from its own source set, and the author's `build` assembles them all. A port that only ever compiles
src/main passes every gate and still fails the author's own `./gradlew build` (measured: a finished 1.21.1
upstream port left 38 errors in two variant source sets that no gate had compiled). Import this module rather
than hard-coding src/main/java. Standard library only.
"""
import argparse, pathlib, re, sys


def java_dirs(repo):
    """src/<set>/java for every source set the build declares (main always; test excluded -- the port's
    own harness tests live outside the author's tree)."""
    repo = pathlib.Path(repo)
    gradle = (repo / "build.gradle").read_text(encoding="utf-8", errors="replace") if (repo / "build.gradle").exists() else ""
    out = []
    for d in sorted(repo.glob("src/*/java")):
        name = d.parent.name
        if name == "test":
            continue
        if name == "main" or re.search(r"(?<![\w.])%s\s*\{" % re.escape(name), gradle):
            out.append(d)
    return out


def compile_tasks(repo):
    return ["compileJava" if d.parent.name == "main" else f"compile{d.parent.name[:1].upper()}{d.parent.name[1:]}Java"
            for d in java_dirs(repo)]


def author_jar_tasks(repo):
    """Jar tasks the author registered or configured, which `build` alone may not reach."""
    gradle = (pathlib.Path(repo) / "build.gradle").read_text(encoding="utf-8", errors="replace")
    return sorted(set(re.findall(r"""tasks\.register\(\s*['"](\w+)['"]\s*,\s*Jar\b""", gradle)))


def target(repo):
    """'NeoForge <neo_version> (Minecraft <minecraft_version>)' from gradle.properties, or None."""
    p = pathlib.Path(repo) / "gradle.properties"
    if not p.exists():
        return None
    props = dict(re.findall(r"(?m)^\s*([\w.]+)\s*=\s*(.*?)\s*$", p.read_text(encoding="utf-8", errors="replace")))
    mc, neo = props.get("minecraft_version"), props.get("neo_version")
    return f"NeoForge {neo} (Minecraft {mc})" if mc and neo else None


def mixin_configs(repo):
    """Every mixin config the mod ships: resource JSONs that carry a "package" and a "mixins"/"client"/"server"
    list, whatever they are called. A port must not assume <modid>.mixins.json -- Forge mods often named the
    config mixins.<modid>.json and registered it from build.gradle (MixinGradle `config '...'`), a line the
    NeoForge build drops; the config then loads only if neoforge.mods.toml declares it in [[mixins]]."""
    import json
    out = []
    for f in sorted(pathlib.Path(repo).glob("src/*/resources/*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(d, dict) and isinstance(d.get("package"), str) and any(
                isinstance(d.get(k), list) for k in ("mixins", "client", "server")):
            out.append(f)
    return out


def undeclared_mixin_configs(repo):
    """Mixin configs no neoforge.mods.toml [[mixins]] entry names -- each one silently never loads."""
    import re as _re
    declared = set()
    for t in pathlib.Path(repo).glob("src/*/resources/META-INF/neoforge.mods.toml"):
        declared |= set(_re.findall(r'(?m)^\s*config\s*=\s*"([^"]+)"', t.read_text(encoding="utf-8")))
    return [f.name for f in mixin_configs(repo) if f.name not in declared]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo"); ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    print("java dirs:", " ".join(str(d.relative_to(a.repo)) for d in java_dirs(a.repo)))
    print("compile tasks:", " ".join(compile_tasks(a.repo)))
    print("author jar tasks:", " ".join(author_jar_tasks(a.repo)))
    print("target:", target(a.repo))
    return 0


def self_check():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        r = pathlib.Path(d)
        for s in ("main", "cf", "test", "generated"):
            (r / "src" / s / "java").mkdir(parents=True)
        (r / "build.gradle").write_text("sourceSets {\n    cf {\n        java.srcDir 'src/cf/java'\n    }\n}\n"
                                        "tasks.register('cfJar', Jar) {}\ntasks.register('x', Copy) {}\n", encoding="utf-8")
        (r / "gradle.properties").write_text("minecraft_version=26.2\nneo_version=26.2.0.75\n", encoding="utf-8")
        (r / "src/main/resources/META-INF").mkdir(parents=True)
        (r / "src/main/resources/mixins.foo.json").write_text('{"package": "a.b", "mixins": ["X"]}', encoding="utf-8")
        (r / "src/main/resources/pack.json").write_text('{"pack": {}}', encoding="utf-8")
        (r / "src/main/resources/META-INF/neoforge.mods.toml").write_text('modId="foo"\n', encoding="utf-8")
        ok = undeclared_mixin_configs(r) == ["mixins.foo.json"]
        (r / "src/main/resources/META-INF/neoforge.mods.toml").write_text('[[mixins]]\nconfig = "mixins.foo.json"\n', encoding="utf-8")
        ok &= undeclared_mixin_configs(r) == []
        ok &= (compile_tasks(r) == ["compileCfJava", "compileJava"] and author_jar_tasks(r) == ["cfJar"]
              and target(r) == "NeoForge 26.2.0.75 (Minecraft 26.2)")
    print("self-check:", "OK" if ok else f"FAIL {compile_tasks(r)} {author_jar_tasks(r)} {target(r)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
