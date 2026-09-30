#!/usr/bin/env python3
"""Emit the CLIENT ITEM DEFINITIONS (`assets/<ns>/items/<id>.json`) MC 1.21.2+ requires.

WHY THIS EXISTS
---------------
Up to 1.21.1 an item found its model by convention: `models/item/<id>.json`, same id, no
declaration anywhere. From 1.21.2 that binding is DATA — `ModelManager.getItemModel` looks the
item up in what `ClientItemInfoLoader` read out of `assets/<ns>/items/`, and an item with no
entry gets `missingModels.item()`: the magenta cube, plus one `Missing item model for location
<id>` line in the log.

A ~460-file builder mod shipped none of these files, so on 26.2 **every one of its 231 items rendered as
the missing cube** while 1.21.1 was perfect. Nothing failed. Gate C's asset check passed,
because it asked whether the MODEL resource exists — which it does; it is the item→model
binding that was gone. Vanilla ships 1538 of these files for its own items, generated the same
way; a mod has to do the same.

TINTS ARE OPT-IN, AND THAT IS A CORRECTNESS RULE, NOT A CONVENIENCE
------------------------------------------------------------------
26.x also moved item TINTS into this file (an entry per tint index in `tints`), where 1.21.1
registered a Java handler per item.

Where a mod HAS a tint source, pass `--tint <ns>:<id>` and it is declared on **every** layer of
every item. That is deliberate: the roster of which items are tinted lives in Java, built by
loops over flavour enums, and copying it here would be a fifth place for it to drift. The mod's
own rule table answers opaque white for any (item, layer) it does not claim -- which is exactly
"no tint" -- so Java stays the single source of truth and adding a flavour needs no regeneration.

Where a mod has NO item tint source, pass nothing. Do **not** copy the flag across from a mod
that has one: an `items/*.json` naming a tint source that is registered nowhere is not an inert
extra field, it is an unknown registry key at data load. The blanket-tint trick is safe only
BECAUSE the mod it came from registers the source it names.

The layer COUNT is read from each model's own `textures` block, so an item gets exactly as many
tint entries as it has sprite layers, and a parent-based model (a block item) gets none.

SPAWN EGGS ARE THE ONE CASE THAT WANTS NO TINT SOURCE OF ITS OWN (catalog V42c)
------------------------------------------------------------------------------
26.x deleted the vanilla spawn-egg template and both its textures, and registers no spawn-egg
tint source -- so every modded egg is the missing cube until the mod supplies its own two
greyscale layers. But an egg's two colours are FIXED LITERALS per item, which is exactly what
vanilla's own `minecraft:constant` source is for: pass `--egg-tints <java>` and each egg gets
two constant tints read straight out of the registration call that already carries them. No
tint source to register, no Java, no client-side wiring -- pure data.

Reading them from the Java rather than from a table here is the same anti-drift rule as above:
the colours have exactly one home, and `--check` fails if someone edits a colour without
regenerating. Adding an egg needs no edit to this script.

USAGE
    python3 tools/gen-client-items.py --ns <namespace> [--tint <ns>:<id>]
    python3 tools/gen-client-items.py --ns <namespace> [--tint <ns>:<id>] --check
"""
import argparse
import json
import re
import os
import sys

ap = argparse.ArgumentParser(description=__doc__,
                             formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--ns", required=True, help="the mod's namespace")
ap.add_argument("--root", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."),
                help="the mod project directory (default: this script's parent)")
ap.add_argument("--overlay", default="mc26",
                help="the source root whose resource overlay the NEW target reads (default: mc26)")
# OPT-IN, and the reason is a correctness trap rather than tidiness -- see the module docstring.
ap.add_argument("--tint", metavar="ID", default=None,
                help="tint-source id to declare on every sprite layer. Pass this ONLY if the mod "
                     "registers that source; naming one that does not exist fails data load.")
ap.add_argument("--egg-tints", metavar="JAVA", default=None,
                help="Java file registering spawn eggs; each egg gets two "
                     "minecraft:constant tints read from its own registration call "
                     "(see the module docstring).")
ap.add_argument("--special", action="append", metavar="ID=TYPE", default=[],
                help="an item drawn by a SPECIAL model renderer rather than by its own model, as "
                     "<item_id>=<special model type id> (repeatable). The definition becomes the "
                     "minecraft:special wrapper: `base` keeps the item's own model for the "
                     "inventory icon and `model` names the renderer. There is no fallback path -- "
                     "a special renderer is chosen by THIS data and nothing else -- so an item "
                     "whose Java hands back a renderer and whose definition omits this draws as a "
                     "flat sprite, silently.")
ap.add_argument("--check", action="store_true", help="fail if the shipped files have drifted")
a = ap.parse_args()

NS = a.ns
ROOT = a.root
MODELS = os.path.join(ROOT, "src/main/resources/assets", NS, "models/item")
# 26.x is the only target that READS these definitions, so layer counts are resolved through the
# 26.x view of the models: its resource overlay wins where it replaces a shared file. The spawn-egg
# template is exactly that case -- one overlaid file gives all 38 eggs their two tintable layers.
OVERLAY_MODELS = os.path.join(ROOT, "src", a.overlay, "resources/assets", NS, "models/item")
ITEMS = os.path.join(ROOT, "src/main/resources/assets", NS, "items")
TINT = a.tint
SPECIAL = {}
for _spec in a.special:
    if "=" not in _spec:
        sys.exit(f"--special wants <item_id>=<model type>, got {_spec!r}")
    _id, _type = _spec.split("=", 1)
    SPECIAL[_id.strip()] = _type.strip()


# ITEMS.register("infected_cow_spawn_egg", () -> ...ItemCompat.spawnEgg(TYPE, 3613496, 10066329, ...
# The DeferredRegister field is not always called ITEMS (MCreator names it REGISTRY), and the
# two colours are ARGB ints written SIGNED -- -6711040 is a perfectly ordinary spawn egg.
# Both were pinned here and both made the scan match nothing, which the guard below turns into
# a refusal rather than a silent zero (X1/X27): every egg untinted still reads as success.
# A FOURTH pin, found on a ~660-file GeckoLib mob mod while fixing R25: the method is not always `register`, and
# the lambda does not always take nothing. The id-stamping form every 26.x port has to move to is
# `REG.registerItem("x", properties -> ...spawnEgg(TYPE, bg, hl, properties))` -- the properties
# arrive from the register rather than being built in the lambda, which is the whole point of
# R25 -- so both `register`/`registerItem` and `()`/`p`/`(p)` are accepted here.
EGG_CALL = re.compile(
    r'\w+\.register(?:Item)?\(\s*"([a-z0-9_]+)"\s*,\s*(?:\(\s*\)|\(\s*\w+\s*\)|\w+)\s*->\s*'
    # ...and the CONSTRUCTOR spelling as well as the compat-pair one: this script reads SHARED
    # source (which is the point -- it is the single place the two colours are stated), and a
    # §W tree's shared source still says `new DeferredSpawnEggItem(...)`; the rename to a pair
    # happens downstream in the prepared tree.
    # A THIRD pin, found on a large boss mod: the constructor may be vanilla's own `new SpawnEggItem(`
    # rather than NeoForge's DeferredSpawnEggItem, and the entity argument is routinely a CAST
    # over a call -- `(EntityType)ModEntities.ENDER_GOLEM.get()` -- which `[^,()]+` forbids by
    # construction. One nesting level of parens is allowed (§X10), which covers the cast and the
    # `.get()` without letting the match run across a comma.
    r'(?:[\w.]*\bspawnEgg|new\s+(?:Deferred)?SpawnEggItem)'
    r'\(\s*(?:[^,()]|\([^()]*\))+,\s*(-?\d+)\s*,\s*(-?\d+)\s*,',
    re.S)


def load_egg_tints(path):
    """item id -> [background, highlight], read from the registration call itself."""
    if not path:
        return {}
    if not os.path.isfile(path):
        sys.exit("--egg-tints: no such file " + path)
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    eggs = {m.group(1): [int(m.group(2)), int(m.group(3))] for m in EGG_CALL.finditer(src)}
    if not eggs:
        # A silent zero here would ship every egg untinted and still read as success -- the
        # exact shape of a rule that matches nothing (catalog X1).
        sys.exit("--egg-tints: matched no spawn-egg registrations in " + path
                 + "; the call shape this script looks for has changed.")
    return eggs


def load_models():
    """id -> parsed model, shared tree with the 26.x overlay layered on top."""
    models = {}
    for d in (MODELS, OVERLAY_MODELS):
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if name.endswith(".json"):
                with open(os.path.join(d, name), encoding="utf-8") as fh:
                    models[name[:-5]] = json.load(fh)
    return models


def layer_count(item_id, models, _seen=None):
    """Sprite layers this model resolves to, following `parent` through THIS mod's models.

    A `minecraft:` parent contributes nothing: `item/generated` and `item/handheld` declare no
    layers of their own (the child does), and for the few items that parent a concrete vanilla
    model the mod has no tint for them anyway — so under-counting there costs nothing, while
    NOT following our own parents would cost every spawn egg its colours.
    """
    seen = _seen or set()
    if item_id in seen:
        return 0                      # a cycle in our own models; refuse rather than recurse
    seen.add(item_id)

    model = models.get(item_id)
    if model is None:
        return 0
    textures = model.get("textures") or {}
    n = 0
    while f"layer{n}" in textures:
        n += 1
    if n:
        return n

    parent = model.get("parent") or ""
    if parent.startswith(NS + ":item/"):
        return layer_count(parent.split("/", 1)[1], models, seen)
    return 0


def definition(item_id, layers, egg_tints):
    special = SPECIAL.get(item_id)
    if special:
        # `base` is the ordinary model, kept because the special renderer draws the held/dropped
        # item and the inventory icon still comes from here; `model` names the renderer. Field
        # names read off SpecialModelWrapper$Unbaked's codec (base required, model required,
        # transformation optional) rather than remembered. A tint would be meaningless: the
        # renderer, not the sprite, decides what is drawn.
        return {"model": {"type": "minecraft:special",
                          "base": f"{NS}:item/{item_id}",
                          "model": {"type": special}}}
    model = {"type": "minecraft:model", "model": f"{NS}:item/{item_id}"}
    colours = egg_tints.get(item_id)
    if colours:
        # Positional: the array is indexed BY layer, so layer0 takes the background colour and
        # layer1 the highlight -- the same two ints the item constructor takes on both targets.
        # Inert on 1.21.1, which ignores the items/ directory entirely.
        model["tints"] = [{"type": "minecraft:constant", "value": c}
                          for c in colours[:layers or len(colours)]]
    elif layers and TINT:
        model["tints"] = [{"type": TINT, "layer": i} for i in range(layers)]
    return {"model": model}


def render(obj):
    return json.dumps(obj, indent=2) + "\n"


def main():
    check = a.check
    if not os.path.isdir(MODELS):
        sys.exit(f"no item models at {MODELS}")

    models = load_models()
    egg_tints = load_egg_tints(a.egg_tints)

    # A TEMPLATE IS NOT AN ITEM, and emitting a definition for one is not a harmless extra.
    #
    # `models/item/` is not always 1:1 with the registry: a mod that shares display transforms
    # between several items keeps the shared half as a parent with no `textures` of its own
    # (one boss mod's `spear` and `throwing`, parented by `earthdive_spear` and
    # `earthdive_spear_throwing`). Emitting a client item definition for one binds a NON-EXISTENT
    # item to a model that resolves `item/generated` with no layer0 -- so 26.2 bakes it and logs
    # `Missing texture references in model <ns>:item/spear`, which is a new warning where there was
    # none, over an item nobody can hold.
    #
    # Both halves of the test are read off the files rather than guessed: it is a template only if
    # something in this namespace parents it AND it declares no textures of its own. A real item
    # model that happens to also be someone's parent keeps its definition.
    parented = set()
    for name, model in models.items():
        parent = model.get("parent") or ""
        if parent.startswith(NS + ":item/"):
            parented.add(parent.split("/", 1)[1])
    templates = sorted(n for n in parented
                       if n in models and not models[n].get("textures"))

    wanted = {}
    for name in sorted(os.listdir(MODELS)):
        if not name.endswith(".json"):
            continue
        item_id = name[:-5]
        if item_id in templates:
            continue
        wanted[item_id] = render(definition(item_id, layer_count(item_id, models), egg_tints))

    os.makedirs(ITEMS, exist_ok=True)
    have = {n[:-5] for n in os.listdir(ITEMS) if n.endswith(".json")}

    missing = sorted(set(wanted) - have)
    stale = sorted(have - set(wanted))
    differing = []
    for item_id, text in wanted.items():
        path = os.path.join(ITEMS, item_id + ".json")
        if os.path.exists(path) and open(path, encoding="utf-8").read() != text:
            differing.append(item_id)

    if check:
        problems = []
        if missing:
            problems.append(f"{len(missing)} item(s) have a model but NO client item definition, "
                            f"so they render as the missing cube on 1.21.2+: {missing[:8]}"
                            + (" ..." if len(missing) > 8 else ""))
        if stale:
            problems.append(f"{len(stale)} definition(s) name an item with no model: {stale[:8]}")
        if differing:
            problems.append(f"{len(differing)} definition(s) differ from what this script would "
                            f"write (layer count or egg colours changed?): {sorted(differing)[:8]}")
        if problems:
            print("gen-client-items --check FAILED:")
            for p in problems:
                print("  " + p)
            # Echo back EVERY flag this invocation was given. A hint that drops one is worse
            # than no hint: following it here would rewrite the nine special-model definitions as
            # ordinary ones and silently un-fix the thing the check just complained about.
            print(f"  run: python3 tools/gen-client-items.py --ns {NS}"
                  + (f" --tint {TINT}" if TINT else "")
                  + (f" --egg-tints {a.egg_tints}" if a.egg_tints else "")
                  + "".join(f" --special {k}={v}" for k, v in sorted(SPECIAL.items())))
            return 1
        print(f"gen-client-items --check: {len(wanted)} definitions, all current"
              + (f" ({len(templates)} parent template(s) skipped: {', '.join(templates)})"
                 if templates else ""))
        return 0

    for item_id, text in wanted.items():
        with open(os.path.join(ITEMS, item_id + ".json"), "w", encoding="utf-8") as fh:
            fh.write(text)
    for item_id in stale:
        os.remove(os.path.join(ITEMS, item_id + ".json"))

    tinted = sum(1 for t in wanted.values() if '"tints"' in t)
    if templates:
        print(f"gen-client-items: skipped {len(templates)} parent template(s) "
              f"(parented by another model, no textures of their own): {', '.join(templates)}")
    print(f"gen-client-items: wrote {len(wanted)} definitions "
          f"({tinted} with tint layers) "
          f"removed {len(stale)} stale")
    return 0


if __name__ == "__main__":
    sys.exit(main())
