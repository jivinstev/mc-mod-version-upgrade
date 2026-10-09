#!/usr/bin/env python3
"""Take a finished NeoForge 1.21.1 port to a 26.x target, deterministically, before any worker runs.

    python3 tools/era-hop.py --work <port> [--target 26.2] [--keep-old]

Run it on a port whose 1.21.1 compile is clean and whose Gate B is green (tools/port.py does): the
26.2 rename table removed 59% of start errors on finished 1.21.1 ports (docs/EVALS.md), and that is the
only state it has been measured on.

1. Frame: tools/make-multiversion.sh turns the single-target build into the ModDevGradle multi-version
   one (CATALOG §W), with a versions/<target>.properties and the composed rename table.
2. Maps: the class-move and colour maps for 1.21.1 -> <target> are generated from the two REAL compile
   classpaths (tools/build-class-move-map.py, tools/gen-color-renames.py) into
   $MIGRATE_WORKSPACE/moves/ when they are not there yet, and the table is composed again with them.
   They are Mojang's names, so they stay on this machine and are never published.
3. Flatten (the default): the table is applied to src/main/java and src/test/java IN PLACE, the target
   becomes the build's default (`mc=<target>` in gradle.properties) and the 1.21.1 target is removed,
   so the compile loop edits the files it compiles. --keep-old leaves a two-target workspace instead
   (shared tree + table + overlays, §W), which needs per-site compat decisions a worker cannot make
   reliably yet.

Prints one line per step and the rename report's dead-rule count. Exit 0 when the workspace is ready
for the compile loop; non-zero, with the failing step, otherwise. Standard library only.
"""
import argparse, os, pathlib, re, shutil, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TPL = ROOT / "templates/multi-version"


def ws_moves():
    return pathlib.Path(os.environ.get("MIGRATE_WORKSPACE") or pathlib.Path.home() / ".mc-mod-upgrade/work") / "moves"


def gradle_classpath(work, target, out):
    """One target's resolved compile classpath, via Gradle (never a hand-built one, X25b-ii). ModDevGradle
    lists its Minecraft jars on the classpath before it has staged them, so they are created first, and
    a classpath naming a file that does not exist is refused: a map built from it is silently partial."""
    subprocess.run(["./gradlew", "-I", str(ROOT / "tools/central-mirror.init.gradle"), "-q",
                    "createMinecraftArtifacts", f"-Pmc={target}", "--console=plain"], cwd=work,
                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
    r = subprocess.run(["./gradlew", "-I", str(ROOT / "tools/qtc-init.gradle"), "-I",
                        str(ROOT / "tools/central-mirror.init.gradle"), "-q", "qtcClasspath", f"-Pmc={target}",
                        "--console=plain"], cwd=work, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=3600)
    m = re.search(r"QTC_CLASSPATH_BEGIN\n(.*?)QTC_CLASSPATH_END", r.stdout, re.S)
    if not m or not m.group(1).strip():
        raise RuntimeError(f"could not resolve the {target} classpath: {(r.stdout + r.stderr)[-600:]}")
    missing = [l for l in m.group(1).splitlines() if l.strip() and not pathlib.Path(l.strip()).exists()]
    if missing:
        raise RuntimeError(f"the {target} classpath names {len(missing)} file(s) that do not exist, e.g. {missing[0]}")
    out.write_text(m.group(1), encoding="utf-8")
    return out


# 26.x data-format transforms, applied in place on a flattened port (CATALOG §V45, §V45b, §V68): recipe
# ingredients become strings, an advancement icon's `item` becomes `id`, entity predicates become the
# dispatched map. They live in the multi-version template's gen-resource-overlays.py and REFUSE what they
# do not recognise; a refused file is listed, never guessed at, because a data file that does not parse
# is logged once at load and then silently missing.
RESOURCE_TRANSFORMS = {
    "26.2": [("ingredients_as_strings", "data/*/recipe/*.json"),
             ("advancement_display_icon_as_stack", "data/*/advancement/*.json"),
             ("entity_predicates_as_dispatched_map", "data/*/advancement/*.json"),
             ("entity_predicates_as_dispatched_map", "data/*/loot_table/*.json")],
}


SER_REG = re.compile(r'\.register\(\s*"([a-z0-9_/]+)"\s*,\s*(?:\(\)\s*->\s*new\s+)?([\w.]+?)(?:::new|\(\))?\s*\)', re.S)
SIMPLE_SER = re.compile(r'\.register\(\s*"([a-z0-9_/]+)"\s*,\s*\(\)\s*->\s*new\s+(?:\w+\.)*Simple\w*RecipeSerializer\s*[<(]')
ING_FIELD = re.compile(r'\bIngredient\.(\w+)((?:\s*\.\s*listOf\([^)]*\))?)\s*\.\s*(?:optionalFieldOf|fieldOf)\(\s*"(\w+)"')


def mod_namespaces(work):
    """The mod's own id(s): `[[mods]]` entries of its toml (a template's `${mod_id}` resolved from
    gradle.properties), never its dependencies."""
    toml = work / "src/main/resources/META-INF/neoforge.mods.toml"
    t = toml.read_text(encoding="utf-8", errors="replace") if toml.exists() else ""
    gp = work / "gradle.properties"
    g = gp.read_text(encoding="utf-8", errors="replace") if gp.exists() else ""
    mid = (re.findall(r"(?m)^mod_id\s*=\s*(\S+)", g) or [""])[0]
    ids = [m.replace("${mod_id}", mid) for m in re.findall(r'\[\[mods\]\][^\[]*?modId\s*=\s*"([^"]+)"', t)]
    return sorted({i for i in ids if re.fullmatch(r"[a-z0-9_.-]+", i)})


def mod_ingredient_fields(work, namespaces):
    """The mod's OWN recipe types -> which JSON fields are ingredients, read off its serializers' codecs
    (`Ingredient.CODEC.fieldOf("tool")`, `Ingredient.LIST_CODEC_NONEMPTY.fieldOf("ingredients")`), so
    the table and the codec cannot disagree. A serializer whose class cannot be found or read is left
    out, and its recipes stay refused; a `Simple*RecipeSerializer` (a special recipe) has none."""
    java = work / "src/main/java"
    if not java.is_dir() or not namespaces:
        return {}
    texts = {f: f.read_text(encoding="utf-8", errors="replace") for f in java.rglob("*.java")}
    by_class = {}
    for f, t in texts.items():
        for m in re.finditer(r"\b(?:class|record)\s+(\w+)", t):
            by_class.setdefault(m.group(1), f)
    out = {}
    for f, t in texts.items():
        if "RecipeSerializer" not in t:
            continue
        for m in SIMPLE_SER.finditer(t):
            for ns in namespaces:
                out[f"{ns}:{m.group(1)}"] = ()
        for m in SER_REG.finditer(t):
            name, ref = m.group(1), m.group(2)
            if f"{namespaces[0]}:{name}" in out:
                continue
            owner = ref.split(".")[0] if "." in ref else ref
            src = by_class.get(owner)
            if not src:
                continue
            fields = []
            for g in ING_FIELD.finditer(texts[src]):
                many = g.group(1).startswith("LIST") or bool(g.group(2).strip())
                fields.append(g.group(3) + ("[]" if many else ""))
            if fields:
                for ns in namespaces:
                    out[f"{ns}:{name}"] = tuple(dict.fromkeys(fields))
    return out


def needs_other_mod(doc, namespaces):
    """The mod a recipe's `neoforge:conditions` require that is not this one, if any: such a file
    loads only alongside that mod, so its format is that mod's business on the target."""
    for c in doc.get("neoforge:conditions") or []:
        if isinstance(c, dict) and c.get("type") == "neoforge:mod_loaded" and c.get("modid") not in (
                *namespaces, "minecraft", "neoforge"):
            return c.get("modid")
    return None


def transform_resources(work, target):
    import fnmatch, importlib.util, json
    s = importlib.util.spec_from_file_location("gro", TPL / "tools/gen-resource-overlays.py")
    gro = importlib.util.module_from_spec(s); s.loader.exec_module(gro)
    res = work / "src/main/resources"
    changed, refused, other = set(), [], []
    ns = mod_namespaces(work)
    spec = {"own_namespaces": ns, "ingredient_fields": mod_ingredient_fields(work, ns)}
    for name, pattern in RESOURCE_TRANSFORMS.get(target, []):
        fn = gro.TRANSFORMS[name]
        for f in sorted(res.rglob("*.json")):
            rel = f.relative_to(res).as_posix()
            if not fnmatch.fnmatch(rel, pattern):
                continue
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
                new = fn(json.loads(json.dumps(doc)), rel, spec)   # the transforms edit in place: hand them a copy
            except (gro.Refused, SystemExit, ValueError) as e:
                req = needs_other_mod(doc, ns) if isinstance(locals().get("doc"), dict) else None
                (other if req else refused).append(f"{rel} ({name}): " + (f"loads only with {req}" if req else str(e)[:160]))
                continue
            if new != doc:
                f.write_text(gro.rendered(new), encoding="utf-8")
                changed.add(rel)
    return changed, refused, other


def client_items(work, namespaces):
    """1.21.2+ binds an item to its model through assets/<ns>/items/<id>.json; an item without one is the
    magenta cube (§V42b). Measured: 184 of a 1.21.1 mod's items, behind a green Gate C. Writes ONLY the
    missing definitions, with the multi-version template's generator (which skips parent templates and
    counts layers), so a definition the port already has -- a special renderer -- is never touched.
    -> number written."""
    import runpy
    written = 0
    for ns in namespaces:
        if not (work / f"src/main/resources/assets/{ns}/models/item").is_dir():
            continue
        argv, sys.argv = sys.argv, ["gen-client-items.py", "--ns", ns, "--root", str(work)]
        try:
            g = runpy.run_path(str(TPL / "tools/gen-client-items.py"), run_name="gen_client_items")
        finally:
            sys.argv = argv
        models = g["load_models"]()
        items = work / f"src/main/resources/assets/{ns}/items"
        items.mkdir(parents=True, exist_ok=True)
        parented = {m.get("parent", "").split("/", 1)[1] for m in models.values()
                    if (m.get("parent") or "").startswith(ns + ":item/")}
        for item_id in sorted(models):
            if (items / f"{item_id}.json").exists() or (item_id in parented and not models[item_id].get("textures")):
                continue
            (items / f"{item_id}.json").write_text(
                g["render"](g["definition"](item_id, g["layer_count"](item_id, models), {})), encoding="utf-8")
            written += 1
    return written


def gecko_bump(work, T):
    """A GeckoLib mod's build names GeckoLib's 1.21.1 coordinate; the target's GeckoLib is another artifact on
    another repository, with interface injections it cannot compile without (CATALOG §V10, §V18b). The fork route
    bumps it in tools/targets.py; this is the same bump, so the jar route's compile can start at all (§X48)."""
    sys.path.insert(0, str(ROOT / "tools"))
    import targets
    t = targets.TARGETS.get(T)
    bg = work / "build.gradle"
    if not t or not bg.exists():
        return
    build = bg.read_text(encoding="utf-8")
    srcs = (f.read_text(encoding="utf-8", errors="replace") for f in (work / "src/main/java").rglob("*.java"))
    gp, vp = work / "gradle.properties", work / f"versions/{T}.properties"
    props = "".join(x.read_text(encoding="utf-8") for x in (gp, vp) if x.exists())
    flag = re.search(r"(?m)^uses_geckolib\s*=\s*(\w+)", props)
    if flag and flag.group(1) != "true" or not (flag or targets.uses_geckolib(build) or any(map(targets.uses_geckolib, srcs))):
        return
    new, _props, notes = targets.bump_build(build, props, t, True)
    bg.write_text(new, encoding="utf-8")
    g = t.geckolib
    for f in (gp, vp):          # the version property the build reads, wherever it lives, becomes the target's id
        if f.exists() and re.search(r"(?m)^geckolib_version\s*=", f.read_text(encoding="utf-8")):
            f.write_text(re.sub(r"(?m)^geckolib_version\s*=.*$", f"geckolib_version={g['version']}",
                                f.read_text(encoding="utf-8")), encoding="utf-8")
            notes.append(f"geckolib_version={g['version']} in {f.name} (GeckoLib {g['release']})")
            break
    else:
        if "${geckolib_version}" in new or "project.geckolib_version" in new:
            vp.write_text(vp.read_text(encoding="utf-8").rstrip() + f"\ngeckolib_version={g['version']}\n", encoding="utf-8")
            notes.append(f"geckolib_version={g['version']} added to {vp.name}")
    step("GeckoLib: " + ("; ".join(notes) if notes else "already on the target's coordinate"))


def step(msg):
    print(f"era-hop: {msg}", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work"); ap.add_argument("--target", default="26.2")
    ap.add_argument("--keep-old", action="store_true", help="keep 1.21.1 as a second target (no flatten)")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not a.work:
        ap.error("--work is required")
    work = pathlib.Path(a.work).resolve()
    T = a.target
    if (work / "versions/1.21.1.properties").exists() or (work / f"versions/{T}.properties").exists():
        step("already multi-version; nothing to frame");
    else:
        r = subprocess.run(["bash", str(ROOT / "tools/make-multiversion.sh"), str(work), T],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print(r.stdout[-1500:] + r.stderr[-800:]); step("FAILED building the multi-version frame"); return 3
        step(f"frame built (ModDevGradle, targets 1.21.1 + {T})")
    moves, colors = ws_moves() / f"moves-1.21.1-to-{T}.tsv", ws_moves() / f"colors-1.21.1-to-{T}.tsv"
    if not moves.exists() or not colors.exists():
        moves.parent.mkdir(parents=True, exist_ok=True)
        step(f"generating the class-move and colour maps from both classpaths (once per machine) into {moves.parent}")
        try:
            old = gradle_classpath(work, "1.21.1", work / "build/era-cp-1.21.1.txt")
            new = gradle_classpath(work, T, work / f"build/era-cp-{T}.txt")
        except RuntimeError as e:
            print(e); step("FAILED resolving a classpath"); return 4
        for tool, out in (("build-class-move-map.py", moves), ("gen-color-renames.py", colors)):
            r = subprocess.run([sys.executable, str(ROOT / "tools" / tool), "--from-cp", str(old), "--to-cp", str(new),
                                "--out", str(out)], capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0 or not out.exists():
                print(r.stdout[-800:] + r.stderr[-800:])
                for f in (moves, colors):   # never leave a half-made map behind: the next run would trust it
                    f.unlink(missing_ok=True)
                step(f"FAILED {tool}"); return 4
    table = work / f"versions/{T}.renames.tsv"
    hand = TPL / f"versions/{T}.renames.hand.tsv"
    r = subprocess.run([sys.executable, str(ROOT / "tools/compose-renames.py"), "--hand", str(hand),
                        "--generated", str(moves), "--generated", str(colors), "--out", str(table)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stdout[-800:] + r.stderr[-800:]); step("FAILED composing the rename table"); return 5
    rows = sum(1 for l in table.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#"))
    step(f"rename table: {rows} rows ({table.relative_to(work)})")
    if a.keep_old:
        step(f"--keep-old: two-target workspace left as is; compile with -Pmc={T}")
        return 0
    prep = ROOT / "templates/multi-version/tools/prepare-sources.py"
    log = work / "era-renames.log"
    with open(log, "w", encoding="utf-8") as fh:
        for tree in ("src/main/java", "src/test/java"):
            if not (work / tree).is_dir():
                continue
            out = work / (tree + ".era")
            shutil.rmtree(out, ignore_errors=True)
            r = subprocess.run([sys.executable, str(prep), "--src", str(work / tree), "--renames", str(table),
                                "--out", str(out)], stdout=fh, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
            if r.returncode != 0:
                step(f"FAILED applying the table to {tree} (see {log.name})"); return 6
            shutil.rmtree(work / tree); out.rename(work / tree)
    text = log.read_text(encoding="utf-8", errors="replace")
    rewrites = sum(int(x) for x in re.findall(r"renameRewrites[=:]\s*(\d+)", text)) or text.count("rewrote")
    step(f"applied in place to src/main/java and src/test/java; report in {log.name}"
         + (f" ({rewrites} rewrites)" if rewrites else ""))
    changed, refused, other = transform_resources(work, T)
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(f"\nresource transforms: {len(changed)} file(s) changed\n" + "".join(f"  REFUSED {r}\n" for r in refused)
                 + "".join(f"  OTHER-MOD {r}\n" for r in other))
    if other:
        step(f"data files: {len(other)} only load alongside another mod and were left in its 1.21.1 format "
             f"(listed as OTHER-MOD in {log.name})")
    step(f"data files: {len(changed)} rewritten to the {T} format"
         + (f"; {len(refused)} REFUSED and left as they were (listed in {log.name}; they will not load on {T} "
            f"until fixed): " + "; ".join(r.split(' (')[0] for r in refused[:6]) if refused else ""))
    n_items = client_items(work, mod_namespaces(work))
    step(f"client item definitions: {n_items} written (an item without one renders as the missing cube on {T})")
    # 26.x parses JSON strictly and skips a lenient-only file (§S8): repair the shapes with one meaning
    r = subprocess.run([sys.executable, str(ROOT / "tools/fix-json-strict.py"), "--work", str(work)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    step(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "fix-json-strict: no output")
    # the target is now the canonical one: an empty table, the default target, and no 1.21.1 build
    # keep the table that was applied: code written LATER in the old dialect (a generated behaviour test)
    # is passed through it too, instead of failing on every renamed name (measured: EntityType.COW, §V21)
    shutil.copy(table, work / f"versions/{T}.renames.applied.tsv")
    table.write_text(f"# {T} is this port's only target: its renames were applied to src/ by tools/era-hop.py.\n",
                     encoding="utf-8")
    for f in ("versions/1.21.1.properties", "versions/1.21.1.renames.tsv"):
        (work / f).unlink(missing_ok=True)
    shutil.rmtree(work / "src/mc21", ignore_errors=True)
    gp = work / "gradle.properties"
    g = re.sub(r"(?m)^mc=.*\n?", "", gp.read_text(encoding="utf-8"))
    gp.write_text(g.rstrip() + f"\n# the build's target (tools/era-hop.py): ./gradlew picks versions/{T}.properties\nmc={T}\n",
                  encoding="utf-8")
    gecko_bump(work, T)
    step(f"flattened: {T} is now the only target (mc={T}); ready for the compile loop")
    return 0


def self_check():
    import tempfile
    ok = True
    with tempfile.TemporaryDirectory() as d:
        os.environ["MIGRATE_WORKSPACE"] = d
        ok &= ws_moves() == pathlib.Path(d) / "moves"
    ok &= (TPL / "versions/26.2.renames.hand.tsv").is_file() and (ROOT / "tools/make-multiversion.sh").is_file()
    ok &= (ROOT / "templates/multi-version/tools/prepare-sources.py").is_file()
    import importlib.util, json
    with tempfile.TemporaryDirectory() as d:
        w = pathlib.Path(d)
        (w / "src/main/resources/META-INF").mkdir(parents=True)
        (w / "src/main/resources/META-INF/neoforge.mods.toml").write_text(
            '[[mods]]\nmodId="${mod_id}"\n[[dependencies.${mod_id}]]\nmodId="other"\n', encoding="utf-8")
        (w / "gradle.properties").write_text("mod_id=mymod\n", encoding="utf-8")
        (w / "src/main/java/a").mkdir(parents=True)
        (w / "src/main/java/a/Reg.java").write_text(
            'class Reg { RecipeSerializer<?> X = S.register("press", PressRecipe.Serializer::new);\n'
            ' RecipeSerializer<?> Y = S.register("special", () -> new SimpleCraftingRecipeSerializer<>(Z::new)); }',
            encoding="utf-8")
        (w / "src/main/java/a/PressRecipe.java").write_text(
            'class PressRecipe { static class Serializer { M c = Ingredient.LIST_CODEC_NONEMPTY.fieldOf("ingredients");'
            ' M t = Ingredient.CODEC.fieldOf("tool"); } }', encoding="utf-8")
        ns = mod_namespaces(w)
        ok &= ns == ["mymod"]
        fl = mod_ingredient_fields(w, ns)
        ok &= fl == {"mymod:press": ("ingredients[]", "tool"), "mymod:special": ()}
        s = importlib.util.spec_from_file_location("gro", TPL / "tools/gen-resource-overlays.py")
        gro = importlib.util.module_from_spec(s); s.loader.exec_module(gro)
        spec = {"own_namespaces": ns, "ingredient_fields": fl}
        doc = {"type": "mymod:press", "ingredients": [{"tag": "c:a"}],
               "tool": [{"type": "mymod:knife"}, {"tag": "c:knives"}], "result": [{"item": {"id": "x:y"}}]}
        out = gro.TRANSFORMS["ingredients_as_strings"](json.loads(json.dumps(doc)), "r", spec)
        ok &= out["ingredients"] == ["#c:a"] and out["tool"] == {"neoforge:ingredient_type": "neoforge:compound",
              "children": [{"neoforge:ingredient_type": "mymod:knife"}, "#c:knives"]}
        ok &= gro.TRANSFORMS["ingredients_as_strings"](json.loads(json.dumps(out)), "r", spec) == out  # idempotent
        try:
            gro.TRANSFORMS["ingredients_as_strings"]({"type": "minecraft:smelting", "ingredient": {"type": "x:z"}}, "r", spec)
            ok = False                                           # a foreign custom ingredient is refused
        except gro.Refused:
            pass
        nothing = {"type": "mymod:apply_banner", "shield": "mymod:iron_shield"}   # unknown type, no ingredients
        ok &= gro.TRANSFORMS["ingredients_as_strings"](json.loads(json.dumps(nothing)), "r", spec) == nothing
        try:
            gro.TRANSFORMS["ingredients_as_strings"]({"type": "mymod:unknown", "input": {"item": "a:b"}}, "r", spec)
            ok = False                                           # unknown type WITH an ingredient: still refused
        except gro.Refused:
            pass
        (w / "src/main/resources/assets/mymod/models/item").mkdir(parents=True)
        (w / "src/main/resources/assets/mymod/models/item/pie.json").write_text(
            '{"parent":"minecraft:item/generated","textures":{"layer0":"mymod:item/pie"}}', encoding="utf-8")
        (w / "src/main/resources/assets/mymod/models/item/pan.json").write_text('{"parent":"minecraft:item/handheld"}',
                                                                               encoding="utf-8")
        (w / "src/main/resources/assets/mymod/items").mkdir(parents=True)
        (w / "src/main/resources/assets/mymod/items/pan.json").write_text('{"model":{"type":"minecraft:special"}}',
                                                                          encoding="utf-8")
        ok &= client_items(w, ["mymod"]) == 1                     # only the missing one
        ok &= '"mymod:item/pie"' in (w / "src/main/resources/assets/mymod/items/pie.json").read_text(encoding="utf-8")
        ok &= "special" in (w / "src/main/resources/assets/mymod/items/pan.json").read_text(encoding="utf-8")
        ok &= needs_other_mod({"neoforge:conditions": [{"type": "neoforge:mod_loaded", "modid": "create"}]}, ns) == "create"
    with tempfile.TemporaryDirectory() as d:   # §X48: the jar route's GeckoLib coordinate moves to the target's
        w = pathlib.Path(d); (w / "versions").mkdir(); (w / "src/main/java").mkdir(parents=True)
        tpl = (ROOT / "templates/neoforge-mod/build.gradle").read_text(encoding="utf-8")
        (w / "build.gradle").write_text(tpl, encoding="utf-8")
        (w / "gradle.properties").write_text("mod_id=m\nuses_geckolib=true\ngeckolib_version=4.8.4\n", encoding="utf-8")
        (w / "versions/26.2.properties").write_text("minecraft_version=26.2\n", encoding="utf-8")
        gecko_bump(w, "26.2")
        b = (w / "build.gradle").read_text(encoding="utf-8")
        ok &= "geckolib-neoforge-${minecraft_version}" not in b and "maven.modrinth:geckolib" in b
        ok &= "geckolib_version=4.8.4" not in (w / "gradle.properties").read_text(encoding="utf-8")
        (w / "gradle.properties").write_text("mod_id=m\nuses_geckolib=false\n", encoding="utf-8")
        (w / "build.gradle").write_text(tpl, encoding="utf-8")
        gecko_bump(w, "26.2")
        ok &= (w / "build.gradle").read_text(encoding="utf-8") == tpl        # a mod without GeckoLib is untouched
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
