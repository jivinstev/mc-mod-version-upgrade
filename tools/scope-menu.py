#!/usr/bin/env python3
"""Scope menu: split a decompiled mod into the chunks a port could leave out, and estimate what each costs.

    python3 tools/scope-menu.py --src mods/<modid>/src/main/java [--resources mods/<modid>/src/main/resources]
                                [--log <first compileJava log>] [--json out.json]

WHY: a user may want the whole mod, or only its core, or something in between -- a mob pack without its
recipe-viewer plugin, its custom shaders or its world generation. migrate-mod's Step 3b runs this after the
FIRST compile and offers the result as a choice (full / minimal / pick chunks). Runs no model.

WHAT A CHUNK IS. A file goes to the first rule it matches; everything left is CORE, which is never offered.
Each chunk names what the mod falls back to without it, so "leave it off" is a decision with a known result:
    integration:<mod>  imports an optional mod (recipe viewers, tooltips, guide books, accessory slots...)
    shaders            custom GLSL / post-processing (Java that loads them, plus assets/*/shaders)
    hud                HUD overlays and screen effects
    particles          custom particle classes
    commands           chat commands
    config-screen      in-game config screens (the config FILE keeps working)
    worldgen           structures, features, biome modifiers (content stops generating naturally)
    advancements       custom advancement triggers
    mixins             vanilla-behaviour tweaks done by mixins (each one is also a load-time risk)
    family:<name>      a group of 5+ entity classes in one sub-package (a mob family: entities, models,
                       renderers, goals). Leaving it off removes those mobs entirely.

WHAT THE ESTIMATE IS, AND IS NOT. A chunk's share is the MEAN of its share of the first compile's errors and
its share of the lines -- checked against what 10 finished ports actually changed, per chunk: lines alone
r=0.88, errors alone r=0.77, the mean r=0.91 (mean absolute error 1.9 points). The error half needs a FULL
error list: javac stops at 100 by default, so take the log with tools/maxerrs.init.gradle. A log
burndown-count.sh refuses (capped, parse abort, OOM) is ignored with a warning, and the share is lines only.
Mixin chunks ran ~1.5x their estimate in that check (much of their work never shows as a compile error). Dollars are a RANGE: start errors x USD_PER_ERROR, the spread of the
published baselines in docs/EVALS.md / docs/port-costs.tsv. It covers the compile phase only; runtime gates,
client rendering and mixin apply failures cost extra, so chunks with mixins or renderers are flagged.
"refs" counts references from the rest of the mod into the chunk: each is a call site to cut or stub when
the chunk is left off, which is work too.
"""
import argparse, collections, importlib.util, json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from gitbash import BASH   # Git Bash on Windows, where plain "bash" is WSL's launcher
USD_PER_ERROR = (0.05, 0.15)   # published baselines: ~$30 for ~640 start errors .. ~$7 for ~45

INTEGRATIONS = {
    "mezz.jei": "JEI recipe viewer", "dev.emi": "EMI recipe viewer", "me.shedaniel.rei": "REI recipe viewer",
    "snownee.jade": "Jade tooltips", "mcjty.theoneprobe": "The One Probe tooltips",
    "vazkii.patchouli": "Patchouli guide book", "top.theillusivec4.curios": "Curios accessory slots",
    "com.simibubi.create": "Create integration", "journeymap": "JourneyMap integration",
    "xaero": "Xaero map integration", "net.irisshaders": "Iris/Oculus shader compatibility",
    "dev.architectury": "Architectury integration", "me.shedaniel.clothconfig2": "Cloth Config screen",
}
FALLBACK = {
    "integration": "the integration is simply absent; the mod runs without the other mod's extras",
    "shaders": "affected effects render with vanilla shaders (or not at all)",
    "hud": "no custom HUD overlay or screen effect",
    "particles": "custom particles become a vanilla particle (or none)",
    "commands": "the chat commands are gone",
    "config-screen": "no in-game config screen; the config file still works",
    "worldgen": "the mod's structures/features stop generating naturally",
    "advancements": "custom advancement triggers never fire",
    "mixins": "the vanilla tweaks those mixins made are gone",
    "family": "these mobs are not in the game (no spawns, no eggs)",
}
RULES = [  # (kind, predicate over (path, text)) -- first match wins
    ("shaders", lambda p, t: re.search(r'\b(ShaderInstance|PostChain|PostPass|RenderPipeline|CompiledShaderProgram)\b', t)),
    ("hud", lambda p, t: re.search(r'\b(RegisterGuiLayersEvent|RegisterGuiOverlaysEvent|LayeredDraw|IGuiOverlay|RenderGuiLayerEvent)\b', t)),
    ("particles", lambda p, t: re.search(r'\bextends\s+(\w+Particle|TextureSheetParticle|SingleQuadParticle)\b|ParticleProvider<', t)),
    ("commands", lambda p, t: re.search(r'\bCommands\.literal\(|RegisterCommandsEvent\b', t)),
    ("config-screen", lambda p, t: re.search(r'\b(IConfigScreenFactory|ConfigScreenFactory|ConfigScreenHandler)\b', t)
                                    or ("config" in p.lower() and re.search(r'\bextends\s+\w*Screen\b', t))),
    ("worldgen", lambda p, t: re.search(r'/(worldgen|structures?|feature|features)/', p.lower())
                               or re.search(r'\bextends\s+(Structure|Feature|StructurePiece|TemplateStructurePiece)\b', t)),
    ("advancements", lambda p, t: re.search(r'\bextends\s+SimpleCriterionTrigger\b|\bCriterionTrigger<', t)),
    ("mixins", lambda p, t: re.search(r'^\s*@Mixin\b', t, re.M)),
]
CLASS = re.compile(r'\b(?:class|interface|enum|record)\s+([A-Z]\w*)')


def chunk_of(rel, text, families):
    imports = re.findall(r'^import\s+(?:static\s+)?([\w.]+)', text, re.M)
    for root, name in INTEGRATIONS.items():
        if any(i.startswith(root) for i in imports):
            return f"integration:{name}"
    for kind, pred in RULES:
        if pred(rel, text):
            return kind
    fam = families.get(str(pathlib.PurePath(rel).parent))
    return f"family:{fam}" if fam else "core"


def find_families(files):
    """A sub-package holding 5+ `class XEntity extends ...` (not block entities or projectiles) is a mob
    family. Its renderers and models usually live elsewhere (client/...), so a *Renderer/*Model/*Layer
    file that names one of the family's classes is pulled into it."""
    by_dir = collections.defaultdict(list)
    for rel, text in files.items():
        m = re.search(r'\bclass\s+(\w+Entity)\s+extends\s+(\w+)', text)
        if m and not re.search(r'BlockEntity|ItemEntity|Projectile|Arrow', m.group(1) + m.group(2)):
            by_dir[str(pathlib.PurePath(rel).parent)].append(rel)
    generic = {"entity", "entities", "mob", "mobs", "monster", "monsters", "goal", "goals", "ai", "block", "blocks",
               "blockentity", "capability", "layer", "layers", "model", "models", "render", "renderer", "client",
               "impl", "util", "common", "misc", "projectile", "projectiles", "living", "creature", "creatures"}
    return {d: pathlib.PurePath(d).name for d, fs in by_dir.items()
            if len(fs) >= 5 and pathlib.PurePath(d).name.lower() not in generic}


def build(src, resources=None, log=None, errors_json=None):
    src = pathlib.Path(src)
    files = {f.relative_to(src).as_posix(): f.read_text(encoding="utf-8", errors="replace") for f in sorted(src.rglob("*.java"))}
    families = find_families(files)
    chunk = {rel: chunk_of(rel, t, families) for rel, t in files.items()}
    # pull a family's renderers/models (files naming 2+ of its classes) into it, unless already a chunk
    fam_classes = collections.defaultdict(set)
    for rel, c in chunk.items():
        if c.startswith("family:"):
            fam_classes[c].update(CLASS.findall(files[rel]))
    for rel, c in list(chunk.items()):
        if c == "core":
            for fc, names in fam_classes.items():
                if re.search(r'(Renderer|Model|Layer)\.java$', rel) and \
                        any(re.search(rf'\b{re.escape(n)}\b', files[rel]) for n in names):
                    chunk[rel] = fc; break
    errors = collections.Counter()
    refused = None
    if log:   # only a log burndown-count.sh accepts: a capped, parse-aborted or OOM'd list is a prefix
        import subprocess
        r = subprocess.run([BASH, str(ROOT / "tools/burndown-count.sh"), str(log)],
                           capture_output=True, text=True, encoding="utf-8")
        if r.returncode != 0:
            refused = r.stdout.strip().splitlines()[0] if r.stdout.strip() else f"exit {r.returncode}"
            log = None
    if log:
        _s = importlib.util.spec_from_file_location("rb", ROOT / "tools/recipe-bench.py")
        rb = importlib.util.module_from_spec(_s); _s.loader.exec_module(rb)
        for f, _l, _m in rb.parse_errors(pathlib.Path(log).read_text(encoding="utf-8", errors="replace")):
            rel = f.replace("\\", "/").rsplit("/java/", 1)[-1]   # javac on Windows writes \ paths
            if rel in chunk:
                errors[rel] += 1
    if errors_json:   # a recipe-bench.py bench.json: errors_by_file is already relative to the java root
        for rel, n in json.loads(pathlib.Path(errors_json).read_text(encoding="utf-8")).get("errors_by_file", {}).items():
            if rel in chunk:
                errors[rel] += n
    shader_assets = 0
    if resources:
        shader_assets = sum(1 for _ in pathlib.Path(resources).glob("assets/*/shaders/**/*") if _.is_file())
    # references from outside a chunk into it (each is a call site to cut when the chunk is left off)
    owned = collections.defaultdict(set)
    for rel, c in chunk.items():
        owned[c].update(CLASS.findall(files[rel]))
    out = {}
    tot_err, tot_lines = sum(errors.values()), sum(t.count("\n") for t in files.values())
    for c in sorted(set(chunk.values())):
        mine = [r for r in chunk if chunk[r] == c]
        names = owned[c]
        refs = 0
        if c != "core" and names:
            rx = re.compile(r'\b(' + "|".join(map(re.escape, sorted(names))) + r')\b')
            refs = sum(len(rx.findall(files[r])) for r in files if chunk[r] != c)
        lines = sum(files[r].count("\n") for r in mine)
        kind = c.split(":")[0]
        out[c] = {"kind": kind, "files": len(mine), "lines": lines, "errors": errors_in(mine, errors),
                  "refs_from_rest": refs, "fallback": FALLBACK.get(kind, ""),
                  "mixins": sum(1 for r in mine if re.search(r'^\s*@Mixin\b', files[r], re.M)),
                  "renderers": sum(1 for r in mine if re.search(r'\bextends\s+\w*(Renderer|RenderLayer)\b', files[r])),
                  "share_errors": round(errors_in(mine, errors) / tot_err, 3) if tot_err else None,
                  "share_lines": round(lines / max(tot_lines, 1), 3),
                  # Measured against 39 chunks of 10 finished ports (what each port actually changed):
                  # lines alone r=0.88, start errors alone r=0.77, their MEAN r=0.91 (mean abs error 1.9
                  # points). So the estimate is the mean when a full error list exists, else lines.
                  "share": round(((errors_in(mine, errors) / tot_err) + lines / max(tot_lines, 1)) / 2, 3)
                           if tot_err else round(lines / max(tot_lines, 1), 3),
                  "share_basis": "mean of start-error share and line share" if tot_err else "lines"}
    if shader_assets and "shaders" not in out:
        out["shaders"] = {"kind": "shaders", "files": 0, "lines": 0, "errors": 0, "refs_from_rest": 0, "mixins": 0,
                          "renderers": 0, "fallback": FALLBACK["shaders"], "share": 0.0, "share_basis": "assets only",
                          "assets": shader_assets}
    return {"total_errors": tot_err, "total_lines": tot_lines, "log_refused": refused, "file_chunk": chunk,
            "usd_range_full": [round(tot_err * USD_PER_ERROR[0], 2), round(tot_err * USD_PER_ERROR[1], 2)] if tot_err else None,
            "chunks": out}


def errors_in(files, errors):
    return sum(errors[f] for f in files)


def report(m):
    if m.get("log_refused"):
        print(f"WARNING: the compile log is not a full error list, so shares fall back to LINES:\n  {m['log_refused']}")
    print(f"start compile errors: {m['total_errors'] or 'n/a (no --log; shares are by lines)'}"
          + (f"   rough compile-phase cost of the FULL port: ${m['usd_range_full'][0]}-${m['usd_range_full'][1]}" if m["usd_range_full"] else ""))
    print(f"{'chunk':40} {'files':>5} {'errors':>6} {'share':>6} {'refs':>5}  flags / without it")
    for c, d in sorted(m["chunks"].items(), key=lambda kv: (kv[0] == "core", -kv[1]["share"])):
        flags = ", ".join(x for x in (f"{d['mixins']} mixins (often ~1.5x the estimate)" if d["mixins"] else "",
                                      f"{d['renderers']} renderers" if d["renderers"] else "") if x)
        usd = ""
        if m["usd_range_full"] and c != "core":
            usd = f" ~${d['errors'] * USD_PER_ERROR[0]:.0f}-{d['errors'] * USD_PER_ERROR[1]:.0f}"
        print(f"{c[:40]:40} {d['files']:5} {d['errors']:6} {100 * d['share']:5.0f}% {d['refs_from_rest']:5}  "
              f"{(flags + '; ') if flags else ''}{'(always ported)' if c == 'core' else d['fallback']}{usd}")
    opt = [d for c, d in m["chunks"].items() if c != "core"]
    if opt:
        s = sum(d["share"] for d in opt)
        print(f"\nminimal port (core only) skips ~{100 * s:.0f}% of the compile-phase work, plus the runtime work of "
              f"{sum(d['mixins'] for d in opt)} mixins and {sum(d['renderers'] for d in opt)} renderers.")
    else:
        print("\nno optional chunks found: offer the full port only.")


def self_check():
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        s = pathlib.Path(t, "java/m"); (s / "compat").mkdir(parents=True); (s / "mobs/bugs").mkdir(parents=True)
        (s / "compat/JeiPlugin.java").write_text("package m.compat;\nimport mezz.jei.api.IModPlugin;\nclass JeiPlugin {}\n", encoding="utf-8")
        (s / "Cmd.java").write_text("package m;\nclass Cmd { void r(){ Commands.literal(\"x\"); } }\n", encoding="utf-8")
        (s / "Main.java").write_text("package m;\nclass Main { JeiPlugin p; AntEntity a; }\n", encoding="utf-8")
        for n in ("Ant", "Bee", "Fly", "Gnat", "Moth"):
            (s / f"mobs/bugs/{n}Entity.java").write_text(f"package m.mobs.bugs;\nclass {n}Entity extends Monster {{}}\n", encoding="utf-8")
        log = pathlib.Path(t, "b.log")
        log.write_text(f"> Task :compileJava\n{s}/compat/JeiPlugin.java:2: error: package mezz.jei.api does not exist\n"
                       f"{s}/Main.java:2: error: cannot find symbol\n", encoding="utf-8")
        m = build(pathlib.Path(t, "java"), log=log)
        log.write_text(log.read_text(encoding="utf-8") + "only showing the first 100 errors, of 900 total\n", encoding="utf-8")
        capped = build(pathlib.Path(t, "java"), log=log)
    c = m["chunks"]
    ok = (set(c) == {"core", "commands", "integration:JEI recipe viewer", "family:bugs"}
          and c["integration:JEI recipe viewer"]["errors"] == 1 and c["integration:JEI recipe viewer"]["share_errors"] == 0.5
          and c["family:bugs"]["files"] == 5 and c["family:bugs"]["refs_from_rest"] == 1 and m["total_errors"] == 2
          and capped["total_errors"] == 0 and capped["log_refused"])
    print("self-check:", "PASS" if ok else f"FAIL {json.dumps(m)[:400]}")
    return 0 if ok else 3


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src"); ap.add_argument("--resources"); ap.add_argument("--log"); ap.add_argument("--json")
    ap.add_argument("--errors-json", help="a recipe-bench.py bench.json (errors_by_file) instead of --log")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not a.src:
        ap.error("--src is required")
    m = build(a.src, a.resources, a.log, a.errors_json)
    report(m)
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(m, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
