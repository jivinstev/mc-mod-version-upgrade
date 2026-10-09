#!/usr/bin/env python3
"""Park the code a port cannot compile against: optional integrations, and the build-time datagen.

    python3 tools/park-optional.py --work mods/<modid> [--dry-run] [--self-check]

A decompiled mod carries two kinds of code the port does not need in order to load:

  * OPTIONAL INTEGRATIONS -- a JEI/EMI/REI plugin, CraftTweaker/KubeJS bindings, a Jade provider -- written
    against another mod's API. That API is not on the port's classpath, so every line of it is a compile
    error a worker would be paid to "fix" by guessing at an API it cannot see (measured on a 1.21.1 food
    mod: ~425 of 1707 starting errors were three integration packages). CATALOG §N: the first pass parks
    them and records it; re-porting one is its own step, against the viewer's real API.
  * DATAGEN -- the providers behind a GatherDataEvent. They ran when the author built the jar and their
    output ships in the jar's data/ and assets/; the game never runs them (CATALOG §M14).

A file is an integration when it imports a package from neither the platform (Minecraft, NeoForge, the
JDK, the libraries every NeoForge game ships) nor the mod itself, nor a REQUIRED dependency's package.
When such a file sits under an `integration`/`compat`/`addon(s)`/`plugin(s)` package, that whole
sub-package goes (an integration's helpers import nothing foreign themselves). Datagen is the package
that holds the GatherDataEvent subscriber, when it is a dedicated data/datagen package; otherwise only
that file. Parked files move to mods/<modid>/parked/ with their paths kept, and every one is listed in
MIGRATION.md, so nothing is dropped silently. A remaining file that imports a parked class is listed too:
the compile loop will see it, and it is main code, so it is never parked automatically. Standard library.
"""
import argparse, pathlib, re, shutil, sys

# net.minecraftforge.* is the SOURCE loader's own API, not an optional integration: the recipe pack and
# forge-shapes port it. Parking it hid a main-class dependency (measured: a config-screen handler).

PLATFORM = ("java.", "javax.", "jdk.", "sun.", "net.minecraft.", "net.neoforged.", "net.minecraftforge.", "com.mojang.", "org.spongepowered.",
            "org.jetbrains.", "org.intellij.", "com.google.", "it.unimi.", "org.apache.", "com.llamalad7.", "org.joml.",
            "org.slf4j.", "io.netty.", "org.lwjgl.", "com.electronwill.", "org.objectweb.", "cpw.mods.", "org.checkerframework.",
            "org.jspecify.", "net.jodah.", "com.ibm.icu.", "oshi.", "com.sun.", "org.w3c.", "org.xml.", "org.antlr.")
INTEGRATION_DIR = re.compile(r"^(integration|integrations|compat|compatibility|addon|addons|plugin|plugins)$", re.I)
DATAGEN_DIR = re.compile(r"^(data|datagen|datagenerator|datagenerators|gen|generators?)$", re.I)
IMPORT = re.compile(r"(?m)^\s*import\s+(?:static\s+)?([\w.]+)\s*;")


def required_roots(work):
    """Package hints for REQUIRED non-platform dependencies: their modId, which a mod's own package
    almost always contains. Their API is meant to be on the classpath, so their imports are kept."""
    toml = work / "src/main/resources/META-INF/neoforge.mods.toml"
    t = toml.read_text(encoding="utf-8", errors="replace") if toml.exists() else ""
    out = set()
    for block in re.split(r"\[\[dependencies\.", t)[1:]:
        mid = re.search(r'modId\s*=\s*"([^"]+)"', block)
        kind = re.search(r'type\s*=\s*"(\w+)"', block) or re.search(r"mandatory\s*=\s*(\w+)", block)
        if mid and kind and kind.group(1) in ("required", "true") and mid.group(1) not in ("minecraft", "neoforge"):
            out.add(mid.group(1).replace("-", "").replace("_", "").lower())
    return out


def foreign(imp, own, req):
    if imp.startswith(PLATFORM) or any(imp.startswith(o + ".") for o in own):
        return False
    flat = imp.replace("_", "").lower()
    return not any(r in flat.split(".") or r in flat for r in req)


def plan(work, group):
    java = work / "src/main/java"
    files = sorted(java.rglob("*.java"))
    own = {group, group.rsplit(".", 1)[0]} if group else set()
    req = required_roots(work)
    park, why = {}, {}
    for f in files:
        t = f.read_text(encoding="utf-8", errors="replace")
        bad = sorted({i for i in IMPORT.findall(t) if foreign(i, own, req)})
        if bad:
            rel = f.relative_to(java)
            parts = rel.parts
            hit = next((k for k, p in enumerate(parts[:-1]) if INTEGRATION_DIR.match(p)), None)
            if hit is not None and hit + 1 < len(parts) - 1:   # integration/<name>/... -> the whole <name>
                unit = pathlib.Path(*parts[:hit + 2])
                why[unit.as_posix()] = f"integration: imports {bad[0].rsplit('.', 1)[0]}"
                for g in (java / unit).rglob("*.java"):
                    park[g] = why[unit.as_posix()]
            else:
                park[f] = f"integration: imports {bad[0]}"
        if re.search(r"\bGatherDataEvent\b", t):
            rel = f.relative_to(java)
            d = rel.parent
            if DATAGEN_DIR.match(d.name):
                for g in (java / d).rglob("*.java"):
                    park[g] = f"datagen: {d.as_posix()} (GatherDataEvent)"
            else:
                park[f] = "datagen: GatherDataEvent subscriber"
    return park


def class_name(java, f):
    return f.relative_to(java).with_suffix("").as_posix().replace("/", ".")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work"); ap.add_argument("--group", help="the mod's root package (default: from gradle.properties)")
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not a.work:
        ap.error("--work is required")
    work = pathlib.Path(a.work).resolve()
    group = a.group
    gp = work / "gradle.properties"
    if not group and gp.exists():
        group = (re.findall(r"(?m)^mod_group_id\s*=\s*(\S+)", gp.read_text(encoding="utf-8")) or [""])[0]
    if not group:
        print("park-optional: no root package (pass --group); refusing to guess what is the mod's own code")
        return 2
    java = work / "src/main/java"
    park = plan(work, group)
    parked_names = {class_name(java, f) for f in park}
    still = sorted({class_name(java, f) for f in java.rglob("*.java") if f not in park
                    and any(i in parked_names for i in IMPORT.findall(f.read_text(encoding="utf-8", errors="replace")))})
    units = sorted(set(park.values()))
    print(f"park-optional: {len(park)} file(s) in {len(units)} unit(s)" + (" (dry run)" if a.dry_run else ""))
    for u in units:
        print(f"  {sum(1 for v in park.values() if v == u):4d}  {u}")
    if still:
        print(f"  {len(still)} remaining file(s) import a parked class (main code; left for the compile loop): "
              + ", ".join(s.rsplit('.', 1)[-1] for s in still[:8]))
    if a.dry_run or not park:
        return 0
    dest = work / "parked/src/main/java"
    for f in park:
        t = dest / f.relative_to(java)
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), str(t))
    for d in sorted(java.rglob("*"), reverse=True):     # drop the directories parking emptied
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    mig = work / "MIGRATION.md"
    lines = ["", "## Parked (tools/park-optional.py)", "",
             "Not compiled into this port; kept under `parked/` with their paths. Re-port an integration against",
             "the other mod's real API when it is wanted (CATALOG §N); datagen output already ships in resources/.", ""]
    lines += [f"- {u}: {sum(1 for v in park.values() if v == u)} file(s)" for u in units]
    if still:
        lines += ["", "Main-code files that imported a parked class: " + ", ".join(still)]
    with open(mig, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return 0


def self_check():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        w = pathlib.Path(d)
        j = w / "src/main/java/com/ex/mymod"
        for rel, body in {
            "MyMod.java": "package com.ex.mymod;\nimport net.minecraft.world.item.Item;\nimport com.ex.mymod.integration.jei.Plug;\nclass MyMod {}",
            "integration/jei/Plug.java": "package com.ex.mymod.integration.jei;\nimport mezz.jei.api.IModPlugin;\nclass Plug {}",
            "integration/jei/Helper.java": "package com.ex.mymod.integration.jei;\nimport net.minecraft.world.item.Item;\nclass Helper {}",
            "integration/Shared.java": "package com.ex.mymod.integration;\nclass Shared {}",
            "data/Gen.java": "package com.ex.mymod.data;\nimport net.neoforged.neoforge.data.event.GatherDataEvent;\nclass Gen {}",
            "data/Recipes.java": "package com.ex.mymod.data;\nimport net.minecraft.data.recipes.RecipeProvider;\nclass Recipes {}",
            "lib/UsesLib.java": "package com.ex.mymod.lib;\nimport dev.reqlib.api.Thing;\nclass UsesLib {}",
        }.items():
            (j / rel).parent.mkdir(parents=True, exist_ok=True)
            (j / rel).write_text(body, encoding="utf-8")
        (w / "src/main/resources/META-INF").mkdir(parents=True)
        (w / "src/main/resources/META-INF/neoforge.mods.toml").write_text(
            '[[dependencies.mymod]]\nmodId="reqlib"\ntype="required"\n[[dependencies.mymod]]\nmodId="jei"\ntype="optional"\n',
            encoding="utf-8")
        got = {f.relative_to(w / "src/main/java").as_posix() for f in plan(w, "com.ex.mymod")}
        ok = got == {"com/ex/mymod/integration/jei/Plug.java", "com/ex/mymod/integration/jei/Helper.java",
                     "com/ex/mymod/data/Gen.java", "com/ex/mymod/data/Recipes.java"}
    print("self-check:", "OK" if ok else f"FAIL {sorted(got)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
