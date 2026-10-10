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
that file. A datagen CLASS is also found by its TYPE, wherever it lives (authors often keep the subscriber in
event/ and the providers in data/ or api/data/): it extends or implements a datagen base -- a provider, a
recipe/model builder, DataProvider -- imported from a datagen package (net.minecraft.data.* except
net.minecraft.data.worldgen, whose helpers mods call at runtime; NeoForge's/Forge's common.data and
client.model.generators), or another such class of the mod. A helper that merely imports a datagen package
joins them only when every class that names it is already datagen. Nothing that main code names is parked --
except through the datagen WIRING itself: when main code (often the mod's main class) holds a GatherDataEvent
handler or registers a datagen subscriber class, that handler method / registration line is cut out of it
(and recorded), so it no longer keeps every provider in the build. Parked files move to mods/<modid>/parked/ with their paths kept, and every one is listed in
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
DATAGEN_PKG = re.compile(r"^(?:net\.minecraft\.data\.(?!worldgen\b)|net\.minecraft\.data\.[A-Z]"
                         r"|net\.(?:neoforged\.neoforge|minecraftforge)\.(?:common\.data|client\.model\.generators)\.)")
ENTRY = re.compile(r"(?m)^\s*@(?:net\.neoforged\.fml\.common\.|net\.minecraftforge\.fml\.common\.)?Mod\s*\(")
DECL = re.compile(r"\b(?:class|interface|record)\s+(\w+)[^{;]*?\b(?:extends|implements)\s+([^{]+)\{", re.S)
GATHER_METHOD = re.compile(r"(?m)^[ \t]*(?:@[\w.]+(?:\([^)]*\))?\s*)*(?:(?:public|private|protected|static|final"
                           r"|synchronized)\s+)*void\s+(\w+)\s*\(\s*(?:final\s+)?(?:[\w.]+\.)?GatherDataEvent(?:\.\w+)?"
                           r"\s+\w+\s*\)\s*(?:throws[^{]*)?\{")


def _block_end(t, i):
    """Index just past the brace block opening at t[i] == '{', skipping strings, chars and comments."""
    depth, n = 0, len(t)
    while i < n:
        c = t[i]
        if t.startswith("//", i):
            i = t.find("\n", i); i = n if i < 0 else i
        elif t.startswith("/*", i):
            i = t.find("*/", i + 2); i = n if i < 0 else i + 2; continue
        elif c in "\"'":
            j = i + 1
            while j < n and t[j] != c:
                j += 2 if t[j] == "\\" else 1
            i = j
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def cut_registrations(t, stems):
    """Drop the lines that only REGISTER a datagen subscriber class (addListener(Gen::gather),
    EVENT_BUS.register(Gen.class)) and the imports left unused -> (new text, [stems cut])."""
    hit = []
    for st in sorted(stems):
        pat = re.compile(r"(?m)^[ \t]*[\w.()]*(?:addListener\(\s*(?:[\w.]+\.)?%s::\w+\s*\)"
                         r"|register\(\s*(?:[\w.]+\.)?%s\.class\s*\))\s*;[ \t]*\n" % (st, st))
        t2 = pat.sub("", t)
        if t2 != t:
            hit.append(st); t = t2
    if hit:
        body = IMPORT.sub("", t)
        for imp in IMPORT.findall(t):
            if imp.rsplit(".", 1)[1] in hit and not re.search(r"\b%s\b" % re.escape(imp.rsplit(".", 1)[1]), body):
                t = re.sub(r"(?m)^\s*import\s+%s\s*;[ \t]*\n" % re.escape(imp), "", t)
    return t, hit


def cut_gather(t):
    """A class that main code needs but that also wires datagen (a mod's main class registering its own
    GatherDataEvent handler is common): remove each GatherDataEvent handler METHOD, the line that registers
    it (addListener(this::m) / addListener(X::m)), and the imports left unused. -> (new text, [method names])."""
    names = []
    while True:
        m = GATHER_METHOD.search(t)
        if not m:
            break
        end = _block_end(t, m.end() - 1)
        if end < 0:
            break
        names.append(m.group(1))
        start = t.rfind("\n", 0, m.start()) + 1
        t = t[:start] + t[end:].lstrip(" \t").removeprefix("\n")
    for n in names:
        t = re.sub(r"(?m)^[ \t]*[\w.()]*addListener\(\s*[\w.]+::%s\s*\)\s*;[ \t]*\n" % re.escape(n), "", t)
    if names:
        body = IMPORT.sub("", t)
        for imp in IMPORT.findall(t):
            simple = imp.rsplit(".", 1)[1]
            if not re.search(r"\b%s\b" % re.escape(simple), body):
                t = re.sub(r"(?m)^\s*import\s+%s\s*;[ \t]*\n" % re.escape(imp), "", t)
    return t, names


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


def plan(work, group, also=()):
    java = work / "src/main/java"
    files = sorted(java.rglob("*.java"))
    own = {group, group.rsplit(".", 1)[0]} if group else set()
    req = required_roots(work) | {m.replace("-", "").replace("_", "").lower() for m in also if m}
    park, why, cuts = {}, {}, {}
    for f in files:
        t = f.read_text(encoding="utf-8", errors="replace")
        bad = sorted({i for i in IMPORT.findall(t) if foreign(i, own, req)})
        if bad and ENTRY.search(t):
            continue          # the @Mod entry class is never parked: its integration references are compile-loop work
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
            named = re.compile(r"\b" + re.escape(f.stem) + r"\b")
            new, names = cut_gather(t)
            if names and not re.search(r"\bGatherDataEvent\b", IMPORT.sub("", new)) and any(
                    g != f and named.search(g.read_text(encoding="utf-8", errors="replace")) for g in files):
                cuts[f] = (new, names)                   # main code wiring datagen: cut the handler, keep the class
            elif DATAGEN_DIR.match(d.name):
                for g in (java / d).rglob("*.java"):
                    park[g] = f"datagen: {d.as_posix()} (GatherDataEvent)"
            else:
                park[f] = "datagen: GatherDataEvent subscriber"
    text = {f: f.read_text(encoding="utf-8", errors="replace") for f in files}
    text.update({f: new for f, (new, _) in cuts.items()})
    # main code that only REGISTERS a separate datagen subscriber class must not keep it (and so everything
    # it names) in the build: cut the registration line, and let the subscriber park as datagen
    subs = {f.stem for f, why in park.items() if why.startswith("datagen")}
    for f in files:
        if f in park or not subs:
            continue
        new, hit = cut_registrations(text[f], subs)
        if hit:
            text[f] = new
            cuts[f] = (new, cuts.get(f, (None, []))[1] + [f"register {h}" for h in hit])
    park.update(datagen_by_type(java, files, park, text))
    # a datagen PACKAGE can hold runtime code too (a mod's tag constants under datagen/tags, named by its items
    # and blocks): never park a datagen-reason file that a file staying in the build still names
    changed = True
    while changed:
        changed = False
        for f in [f for f, why in park.items() if why.startswith("datagen")]:
            who = re.compile(r"\b" + re.escape(f.stem) + r"\b")
            if any(g not in park and who.search(u) for g, u in text.items() if g != f):
                del park[f]
                changed = True
    return park, cuts


def datagen_by_type(java, files, already, text=None):
    """-> {file: reason} for datagen classes found by type, plus helpers only datagen names (see the docstring)."""
    text = text or {f: f.read_text(encoding="utf-8", errors="replace") for f in files}
    dg_imports = {f: {i.rsplit(".", 1)[1] for i in IMPORT.findall(t) if DATAGEN_PKG.match(i)} for f, t in text.items()}
    simple = {f: f.stem for f in files}
    found, changed = {}, True
    while changed:                                   # transitive: a class extending one of the mod's providers
        changed = False
        dg_names = {simple[f] for f in found} | {simple[f] for f in already}
        for f, t in text.items():
            if f in found or f in already:
                continue
            for m in DECL.finditer(t):
                if m.group(1) != simple[f]:
                    continue
                bases = set(re.findall(r"\b([A-Z]\w*)\b", m.group(2)))
                hit = (bases & dg_imports[f]) or (bases & dg_names)
                if hit:
                    found[f] = f"datagen: {sorted(hit)[0]} subclass"
                    changed = True
                break
    changed = True
    while changed:                                   # helpers named by datagen classes only
        changed = False
        dg = set(found) | set(already)
        for f, t in text.items():
            if f in dg or not dg_imports[f]:
                continue
            who = re.compile(r"\b" + re.escape(simple[f]) + r"\b")
            users = [g for g, u in text.items() if g != f and who.search(u)]
            if users and all(g in dg for g in users):
                found[f] = "datagen: helper used only by datagen"
                changed = True
    # never park what main code names: drop any found class a non-datagen, non-parked class refers to
    changed = True
    while changed:
        changed = False
        dg = set(found) | set(already)
        for f in list(found):
            who = re.compile(r"\b" + re.escape(simple[f]) + r"\b")
            if any(g not in dg and who.search(u) for g, u in text.items() if g != f):
                del found[f]
                changed = True
    return found


def class_name(java, f):
    return f.relative_to(java).with_suffix("").as_posix().replace("/", ".")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work"); ap.add_argument("--group", help="the mod's root package (default: from gradle.properties)")
    ap.add_argument("--also-required", default="", help="mod ids to treat as required whatever the toml says (port.py "
                    "passes optional dependencies the code uses throughout, and geckolib when the build supplies it)")
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
    park, cuts = plan(work, group, [m for m in a.also_required.split(",") if m])
    parked_names = {class_name(java, f) for f in park}
    def now(f):
        return cuts[f][0] if f in cuts else f.read_text(encoding="utf-8", errors="replace")
    still = sorted({class_name(java, f) for f in java.rglob("*.java") if f not in park
                    and any(i in parked_names for i in IMPORT.findall(now(f)))})
    units = sorted(set(park.values()))
    print(f"park-optional: {len(park)} file(s) in {len(units)} unit(s)" + (" (dry run)" if a.dry_run else ""))
    for u in units:
        print(f"  {sum(1 for v in park.values() if v == u):4d}  {u}")
    for f, (_, names) in sorted(cuts.items()):
        print(f"  cut datagen wiring ({', '.join(names)}) out of {f.relative_to(java).as_posix()} (main code; kept)")
    if still:
        print(f"  {len(still)} remaining file(s) import a parked class (main code; left for the compile loop): "
              + ", ".join(s.rsplit('.', 1)[-1] for s in still[:8]))
    if a.dry_run or not (park or cuts):
        return 0
    for f, (new, _) in cuts.items():
        f.write_text(new, encoding="utf-8")
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
    lines += [f"- datagen wiring ({', '.join(n)}) cut out of {f.relative_to(java).as_posix()}, which main code needs "
              f"(the original is in decompiled-raw/)" for f, (_, n) in sorted(cuts.items())]
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
            "Entry.java": "package com.ex.mymod;\nimport top.theillusivec4.curios.api.CuriosApi;\n@Mod(\"mymod\")\nclass Entry {}",
            "integration/jei/Helper.java": "package com.ex.mymod.integration.jei;\nimport net.minecraft.world.item.Item;\nclass Helper {}",
            "integration/Shared.java": "package com.ex.mymod.integration;\nclass Shared {}",
            "data/Gen.java": "package com.ex.mymod.data;\nimport net.neoforged.neoforge.data.event.GatherDataEvent;\nclass Gen {}",
            "data/Recipes.java": "package com.ex.mymod.data;\nimport net.minecraft.data.recipes.RecipeProvider;\nclass Recipes {}",
            "lib/UsesLib.java": "package com.ex.mymod.lib;\nimport dev.reqlib.api.Thing;\nclass UsesLib {}",
            # the subscriber in event/, the providers elsewhere: found by type
            "event/DataGenEvents.java": "package com.ex.mymod.event;\nimport net.neoforged.neoforge.data.event.GatherDataEvent;\nimport com.ex.mymod.gen.ModItems;\nclass DataGenEvents { void g(GatherDataEvent e){ new ModItems(); new ModTags(); } }",
            "gen/ModItems.java": "package com.ex.mymod.gen;\nimport net.neoforged.neoforge.client.model.generators.ItemModelProvider;\npublic class ModItems extends ItemModelProvider {}",
            "gen/ModTags.java": "package com.ex.mymod.gen;\npublic class ModTags extends BaseTags {}",
            "gen/BaseTags.java": "package com.ex.mymod.gen;\nimport net.minecraft.data.tags.TagsProvider;\npublic abstract class BaseTags extends TagsProvider {}",
            "api/data/RecipeHelper.java": "package com.ex.mymod.api.data;\nimport net.minecraft.data.recipes.RecipeOutput;\npublic class RecipeHelper {}",
            "gen/ModRecipes.java": "package com.ex.mymod.gen;\nimport net.minecraft.data.recipes.RecipeProvider;\nclass ModRecipes extends RecipeProvider { void r(){ RecipeHelper.x(); } }",
            # runtime code: a builder named by a client model is NOT parked, nor is the model
            "item/CoatBuilder.java": "package com.ex.mymod.item;\nimport net.neoforged.neoforge.client.model.generators.ModelBuilder;\npublic class CoatBuilder extends ModelBuilder {}",
            "client/CoatModel.java": "package com.ex.mymod.client;\nimport com.ex.mymod.item.CoatBuilder;\nclass CoatModel { CoatBuilder b; }",
            "Reg.java": "package com.ex.mymod;\nimport com.ex.mymod.client.CoatModel;\nclass Reg { CoatModel m; }",
            # runtime constants inside the datagen package stay: main code names them
            "data/ModTagKeys.java": "package com.ex.mymod.data;\npublic class ModTagKeys { public static Object GEMS; }",
            "item/Gem.java": "package com.ex.mymod.item;\nimport com.ex.mymod.data.ModTagKeys;\nclass Gem { Object t = ModTagKeys.GEMS; }",
            # the mod's main class wires its own datagen: cut the handler, park the providers it named
            "Main.java": "package com.ex.mymod;\nimport com.ex.mymod.gen.Blocks;\nimport net.neoforged.neoforge.data.event.GatherDataEvent;\n"
                         "class Main {\n   Main(Bus b) {\n      b.addListener(this::gen);\n      Reg r = null;\n   }\n\n"
                         "   private void gen(GatherDataEvent event) {\n      String s = \"}\";\n      event.add(new Blocks());\n   }\n}\n",
            "Boot.java": "package com.ex.mymod;\nclass Boot { Main m; }",
            "gen/Blocks.java": "package com.ex.mymod.gen;\nimport net.neoforged.neoforge.client.model.generators.BlockStateProvider;\npublic class Blocks extends BlockStateProvider {}",
            # ...or registers a separate datagen subscriber: cut the registration, park the subscriber
            "Setup.java": "package com.ex.mymod;\nimport com.ex.mymod.datagen.DataGenerators;\nclass Setup {\n   void init(Bus bus) {\n"
                          "      bus.addListener(DataGenerators::gather);\n      bus.addListener(this::common);\n   }\n}\n",
            "Wire.java": "package com.ex.mymod;\nclass Wire { Setup s; }",
            "datagen/DataGenerators.java": "package com.ex.mymod.datagen;\nimport net.neoforged.neoforge.data.event.GatherDataEvent;\n"
                                           "public class DataGenerators { public static void gather(GatherDataEvent e) {} }",
            # worldgen helpers are runtime: not datagen
            "world/Feats.java": "package com.ex.mymod.world;\nimport net.minecraft.data.worldgen.placement.PlacementUtils;\nclass Feats extends PlacementUtils {}",
        }.items():
            (j / rel).parent.mkdir(parents=True, exist_ok=True)
            (j / rel).write_text(body, encoding="utf-8")
        (w / "src/main/resources/META-INF").mkdir(parents=True)
        (w / "src/main/resources/META-INF/neoforge.mods.toml").write_text(
            '[[dependencies.mymod]]\nmodId="reqlib"\ntype="required"\n[[dependencies.mymod]]\nmodId="jei"\ntype="optional"\n',
            encoding="utf-8")
        park, cuts = plan(w, "com.ex.mymod")
        got = {f.relative_to(w / "src/main/java").as_posix() for f in park}
        main = {f.name: c for f, c in cuts.items()}.get("Main.java")
        setup = {f.name: c for f, c in cuts.items()}.get("Setup.java")
        ok = setup is not None and "DataGenerators" not in setup[0] and "this::common" in setup[0] and main is not None and main[1] == ["gen"] and "GatherDataEvent" not in main[0] and "addListener" not in main[0] \
            and "gen.Blocks" not in main[0] and "Reg r = null;" in main[0] and main[0].rstrip().endswith("}") and got == {
                     "com/ex/mymod/gen/Blocks.java", "com/ex/mymod/datagen/DataGenerators.java", "com/ex/mymod/integration/jei/Plug.java", "com/ex/mymod/integration/jei/Helper.java",
                     "com/ex/mymod/data/Gen.java", "com/ex/mymod/data/Recipes.java",
                     "com/ex/mymod/event/DataGenEvents.java", "com/ex/mymod/gen/ModItems.java",
                     "com/ex/mymod/gen/ModTags.java", "com/ex/mymod/gen/BaseTags.java",
                     "com/ex/mymod/gen/ModRecipes.java", "com/ex/mymod/api/data/RecipeHelper.java"}
    print("self-check:", "OK" if ok else f"FAIL {sorted(got)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
