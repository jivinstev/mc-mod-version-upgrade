#!/usr/bin/env python3
"""Derive a target's RESOURCE overlays from the shared files, by a declared patch or a transform.

WHY THIS EXISTS
  §V45's rule is that one data file usually serves both Minecraft versions, because a Mojang codec
  ignores fields it does not know -- so a multi-version mod normally needs no per-version data at
  all. The exception is a key that is REQUIRED on both versions with INCOMPATIBLE VALUES: no value
  parses twice, and omitting it is not open either.

  Such a file has to go to a per-version resource overlay. Hand-copying it is the §W14 mistake in a
  new place: the overlay is then a SECOND copy of a file that is otherwise identical, every future
  edit to the shared one has to be made twice, and nothing anywhere would say it had drifted. So the
  overlay is DERIVED, and `--check` is a build task, which makes the shared file the single source
  of truth and a drift impossible rather than merely unlikely.

TWO WAYS TO DERIVE ONE, and the difference is how many files are involved
  A PATCH is a shallow declared edit to ONE named file -- `set`/`delete` on top-level keys. Right
  when the difference is a single hand-picked value (biome `carvers`), and deliberately not a
  deep-merge language: the whole point is that the difference is small enough to read.

  A TRANSFORM is a named, code-implemented rewrite applied over a GLOB. Right when a whole shape
  changed and a hundred files carry it -- 1.21.2 made a recipe ingredient a bare string where
  1.21.1 wants an object, and no declared patch can say that a hundred times without becoming the
  hand-written second copy this file exists to prevent.

  ⚠ A transform must REFUSE what it does not recognise, and every one below does: an unknown recipe
  type, an ingredient in an unexpected shape, an entity-predicate key with no known 26.2 name, all
  stop the build by name. §S1b's rule -- a codemod that quietly produces a WRONG result is worse
  than one that refuses -- has more force here than in a compile-driven rewrite, because a data
  file has no compiler behind it: a mis-shaped one is logged once at load and then ignored.

THE PATCH FILE -- versions/<target>.resource-patches.json

    {
      "patches": {
        "data/<ns>/worldgen/biome/bowels.json": {
          "why": "one line, kept in the generated file's own comment",
          "set": { "carvers": [] },
          "delete": ["some_key"]
        }
      },
      "transforms": [
        { "name": "<one of TRANSFORMS below>", "why": "...", "include": ["data/*/recipe/**.json"] }
      ]
    }

TWO INVARIANTS THIS TOOL ASSERTS ABOUT ITSELF, both of them §X10/§X11 in the data layer
  * A transform that matched no file, or that changed no file it matched, is a DEAD RULE and fails.
    A rewrite that silently does nothing is exactly as invisible here as a rename rule that matches
    nothing, and the coverage line is printed every run so "checked 0" cannot read as "all clear".
  * An overlay file the generator would NOT produce is an ORPHAN and fails. A hand-added copy under
    src/<overlay>/resources/data SHADOWS the shared file (processResources adds the overlay second),
    so it stops tracking it the moment someone edits the shared one -- silently, and only on the one
    target nobody is running today.

USAGE
  python3 tools/gen-resource-overlays.py                # write every target's overlays
  python3 tools/gen-resource-overlays.py --check        # fail on drift (wired into `check`)
"""
import fnmatch
import glob
import json
import os
import sys


class Refused(Exception):
    """A transform met something it does not understand. Never guess -- stop and name it."""


# --------------------------------------------------------------------------------------------
# Transform: recipe ingredients, object -> bare string  (1.21.1 -> 1.21.2+, catalog §M6 read
# forwards). 1.21.1 wants {"item": X} / {"tag": X} and REJECTS a bare string; 26.2 wants "X" /
# "#X" and rejects the object. Neither form parses on both, so this is a real overlay, not a
# union file.
#
# The ingredient POSITIONS are named per recipe type rather than found by shape, because the same
# object shape means different things elsewhere in these files: a result is {"id": X, "count": N}
# and a loot entry is {"type": "minecraft:item", "name": X}. A rewrite keyed on "looks like an
# item" would reach both. Every type the mod ships is listed; an unlisted one stops the build.
# --------------------------------------------------------------------------------------------
INGREDIENT_FIELDS = {
    # vanilla
    "minecraft:crafting_shaped":               ("key.*",),
    "minecraft:crafting_shapeless":            ("ingredients[]",),
    "minecraft:stonecutting":                  ("ingredient",),
    "minecraft:smelting":                      ("ingredient",),
    "minecraft:blasting":                      ("ingredient",),
    "minecraft:smoking":                       ("ingredient",),
    "minecraft:campfire_cooking":              ("ingredient",),
    "minecraft:smithing_transform":            ("template", "base", "addition"),
    "minecraft:smithing_trim":                 ("template", "base", "addition"),
}
# A MOD's own recipe types belong to that mod, not to this shared tool: kept here, every port
# carries every other port's table, and a mod whose serializer grows a field has to remember to
# edit a copy of this file that lives beside a different mod. So a transform entry may carry its
# own `ingredient_fields`, merged over the vanilla table above -- read off that serializer's own
# MapCodec, so the table and the codec cannot disagree quietly. An unknown type is still REFUSED.


# NeoForge's own custom ingredients, 1.21.1 -> 26.2: the key that names the type moved from `type` to
# `neoforge:ingredient_type` (IngredientCodecs on 26.2), and the fields listed here are themselves
# ingredients, converted in turn. Read off both versions' CompoundIngredient, DifferenceIngredient and
# IntersectionIngredient codecs; a NeoForge type not listed here is refused.
NEOFORGE_CUSTOM = {
    "neoforge:compound":     {"children": "list", "ingredients": "list"},
    "neoforge:difference":   {"base": "one", "subtracted": "one"},
    "neoforge:intersection": {"children": "list"},
}


def _ingredient(value, where, own=()):
    """One 1.21.1 ingredient -> its 26.2 form. Anything unexpected is refused, not guessed.

    `own` names the namespaces whose custom ingredient types belong to the mod being ported: their
    fields are read by the mod's own codec, which the port carries across, so only the type key moves.
    A list stays a list only when every member is a plain item id: 26.2's list form is a holder set,
    which holds item ids and nothing else, so a list with a tag or a custom ingredient in it becomes
    NeoForge's compound ingredient (what 1.21.1 turned such a list into at load)."""
    if isinstance(value, str) and value:
        return value            # already the 26.2 form (a rerun over a converted file is a no-op)
    if isinstance(value, list):
        conv = [_ingredient(v, where + "[]", own) for v in value]
        if all(isinstance(c, str) and not c.startswith("#") for c in conv):
            return conv
        if len(conv) == 1:
            return conv[0]
        return {"neoforge:ingredient_type": "neoforge:compound", "children": conv}
    if isinstance(value, dict):
        if set(value) == {"item"} and isinstance(value["item"], str):
            return value["item"]
        if set(value) == {"tag"} and isinstance(value["tag"], str):
            return "#" + value["tag"]
        key = "type" if "type" in value else "neoforge:ingredient_type" if "neoforge:ingredient_type" in value else None
        if key:
            kind = value[key]
            rest = {k: v for k, v in value.items() if k != key}
            out = {"neoforge:ingredient_type": kind}
            if kind in NEOFORGE_CUSTOM:
                for k, v in rest.items():
                    shape = NEOFORGE_CUSTOM[kind].get(k)
                    if shape is None:
                        raise Refused("%s: %s has a field %r no rule knows" % (where, kind, k))
                    if shape == "list":
                        if not isinstance(v, list):
                            raise Refused("%s.%s of %s is not a list" % (where, k, kind))
                        out["children"] = [_ingredient(c, "%s.%s[]" % (where, k), own) for c in v]
                    else:
                        out[k] = _ingredient(v, "%s.%s" % (where, k), own)
                return out
            if isinstance(kind, str) and kind.split(":")[0] in own:
                out.update(rest)
                return out
            raise Refused("%s is a custom ingredient (%s); no rule covers its 26.2 shape"
                          % (where, kind))
    raise Refused("%s is not an ingredient shape this rule knows: %s"
                  % (where, json.dumps(value)[:120]))


def _find_leftover_ingredient_objects(node, where, out):
    if isinstance(node, dict):
        if set(node) in ({"item"}, {"tag"}) and isinstance(next(iter(node.values())), str):
            out.append(where)   # an {"item": {...stack...}} wrapper (a result) is not an ingredient
        for k, v in node.items():
            _find_leftover_ingredient_objects(v, "%s.%s" % (where, k), out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _find_leftover_ingredient_objects(v, "%s[%d]" % (where, i), out)


def ingredients_as_strings(doc, rel, spec):
    kind = doc.get("type")
    fields = dict(INGREDIENT_FIELDS)
    own = tuple(spec.get("own_namespaces") or ())
    for k, v in (spec.get("ingredient_fields") or {}).items():
        fields[k] = tuple(v)
    if kind not in fields:
        # A type no table names is only a problem if it carries something to convert: one whose fields
        # hold no 1.21.1 ingredient object anywhere (e.g. `"shield": "<item id>"`) is already valid on 26.2.
        leftover = []
        _find_leftover_ingredient_objects(doc, "", leftover)
        if not leftover:
            return doc
        raise Refused("recipe type %r is not in INGREDIENT_FIELDS, and this transform's own "
                      "ingredient_fields does not declare it either, so this rule does not know "
                      "which of its fields are ingredients" % (kind,))
    for field in fields[kind]:
        if field.endswith("[]"):
            name = field[:-2]
            if name in doc:
                doc[name] = [_ingredient(v, "%s[%d]" % (name, i), own)
                             for i, v in enumerate(doc[name])]
        elif field == "key.*":
            for k, v in doc.get("key", {}).items():
                doc["key"][k] = _ingredient(v, "key.%s" % k, own)
        elif field in doc:
            doc[field] = _ingredient(doc[field], field, own)

    # The table above says which fields are ingredients; this says nothing was left behind. A
    # known type that later grows a new ingredient field would otherwise pass through untouched
    # and fail at load -- which is the one thing the per-type table cannot see for itself.
    leftover = []
    _find_leftover_ingredient_objects(doc, "", leftover)
    if leftover:
        raise Refused("a 1.21.1 ingredient object survives the rewrite at %s -- recipe type %r "
                      "has an ingredient field INGREDIENT_FIELDS does not list"
                      % (", ".join(leftover), kind))
    return doc


# --------------------------------------------------------------------------------------------
# Transform: EntityPredicate, a record -> a dispatched map (1.21.1 -> 26.2).
#
# 26.2 made EntityPredicate `Codec.dispatchedMap(ENTITY_SUB_PREDICATE_TYPE)`: each key is now a
# registered sub-predicate id. Most 1.21.1 field names happen to be registered ids too and so
# survive untouched; `type` does NOT (there is no minecraft:type), and `type_specific` folded its
# inner dispatch into the key itself.
#
# Both maps below are the ids 26.2 really registers, read out of
# advancements/predicates/entity/EntitySubPredicates.bootstrap -- not names that look right.
# --------------------------------------------------------------------------------------------
ENTITY_PREDICATE_KEYS = {
    "type": "entity_type",
    "distance": "distance",
    "movement": "movement",
    "location": "location",
    "stepping_on": "stepping_on",
    "movement_affected_by": "movement_affected_by",
    "effects": "effects",
    "nbt": "nbt",
    "flags": "flags",
    "equipment": "equipment",
    "periodic_tick": "periodic_tick",
    "team": "team",
    "slots": "slots",
    "vehicle": "vehicle",
    "passenger": "passenger",
    "targeted_entity": "targeted_entity",
}
# Keys whose value is itself an EntityPredicate, so the rewrite has to go down into it.
NESTED_ENTITY_PREDICATE = ("vehicle", "passenger", "targeted_entity")
# `type_specific` inner ids that 26.2 still has, under a `type_specific/` prefix. The 1.21.1
# variant predicates (axolotl, cat, wolf, ...) and `slime` are deliberately absent: 26.2 either
# dropped them or reshaped their value, and this mod uses none of them.
TYPE_SPECIFIC = {"player", "lightning", "fishing_hook", "raider"}


def _entity_predicate(pred, where):
    if not isinstance(pred, dict):
        raise Refused("%s is not an entity predicate object" % where)
    out = {}
    for key, value in pred.items():
        if key == "type_specific":
            if not isinstance(value, dict) or "type" not in value:
                raise Refused("%s.type_specific has no `type` to fold into its key" % where)
            sub = str(value["type"]).split(":")[-1]
            if sub not in TYPE_SPECIFIC:
                raise Refused("%s.type_specific type %r is not one 26.2 still registers under "
                              "type_specific/ (see EntitySubPredicates)" % (where, value["type"]))
            rest = {k: v for k, v in value.items() if k != "type"}
            if sub == "player" and "looking_at" in rest:
                rest["looking_at"] = _entity_predicate(rest["looking_at"],
                                                       "%s.type_specific.looking_at" % where)
            out["type_specific/" + sub] = rest
        elif key in ENTITY_PREDICATE_KEYS:
            if key in NESTED_ENTITY_PREDICATE:
                value = _entity_predicate(value, "%s.%s" % (where, key))
            out[ENTITY_PREDICATE_KEYS[key]] = value
        elif key in set(ENTITY_PREDICATE_KEYS.values()) or key.startswith("type_specific/"):
            out[key] = value        # already the 26.2 id: a rerun over a converted file is a no-op
        else:
            raise Refused("%s.%s has no known 26.2 sub-predicate id" % (where, key))
    return out


# An EntityPredicate reaches a data file through TWO carriers, and the transform originally knew
# only the first: a loot condition (`{"condition": "minecraft:entity_properties", "predicate": …}`)
# and an ADVANCEMENT CRITERION (`criteria.<name>.conditions.<field>`). Measured on
# a ~320-file boss mod: the four boss-defeat advancements put theirs under
# `conditions.entity`, so the transform matched 20 files and changed 0 -- caught only because the
# generator refuses a rule that changes nothing (§X1 in the resource layer). The tool's scope was
# narrower than its name and its include glob both suggested, which is §X27's invariant again.
#
# These are the criterion fields vanilla types as an EntityPredicate/ContextAwarePredicate. The
# list is an ALLOW-list on purpose: `_entity_predicate` already refuses a key it has no 26.2 id
# for, so anything reached through this list fails loudly rather than silently, and anything not on
# it is left alone -- except for the missed-carrier guard below, which is what stops the list being
# quietly incomplete (§S1b: a codemod that produces a wrong result is worse than one that refuses).
CRITERION_ENTITY_FIELDS = (
    "entity", "player", "victim", "attacker", "killing_blow_entity", "child", "parent", "partner",
    "projectile", "shooter", "source", "zombie", "villager", "bystander", "lightning", "rider",
    "vehicle", "passenger", "owner",
)


def entity_predicates_as_dispatched_map(doc, rel, spec):
    def criterion(conds, where):
        """Handle the allow-listed EntityPredicate fields; return the ones left for the walk.

        ⚠ It must RETURN the rest rather than stopping: a criterion's other conditions can carry
        loot-condition-shaped predicates the outer walk still has to reach. Returning early here
        was a real regression -- it left four advancement overlays on a large boss mod underived, which
        the orphan check caught immediately (and which is what an orphan check is for)."""
        rest = {}
        for field, value in list(conds.items()):
            if field in CRITERION_ENTITY_FIELDS and isinstance(value, dict):
                conds[field] = _entity_predicate(value, "%s.%s" % (where, field))
            elif isinstance(value, dict) and isinstance(value.get("type"), str) \
                    and ":" in value["type"]:
                # The signature of a carrier this allow-list does not know: a criterion field
                # holding a `type` that names a registry id. On 26.2 `minecraft:type` is not a
                # registered entity_sub_predicate_type, so leaving it would drop the whole
                # advancement at load with nothing but one ERROR line to say so.
                raise Refused("%s.%s looks like an entity predicate (it has a namespaced `type`) "
                              "but is not in CRITERION_ENTITY_FIELDS -- add it there rather than "
                              "letting this file ship an advancement that cannot load" %
                              (where, field))
            else:
                rest[field] = value
        return rest

    def walk(node, where):
        if isinstance(node, dict):
            if node.get("condition") == "minecraft:entity_properties" and "predicate" in node:
                node["predicate"] = _entity_predicate(node["predicate"], where + ".predicate")
                return
            if "trigger" in node and isinstance(node.get("conditions"), dict):
                for k, v in criterion(node["conditions"], where + ".conditions").items():
                    walk(v, "%s.conditions.%s" % (where, k))
                return
            for k, v in node.items():
                walk(v, "%s.%s" % (where, k))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, "%s[%d]" % (where, i))
    walk(doc, "")
    return doc


# --------------------------------------------------------------------------------------------
# Transform: a declared id rename. 26.2 renamed minecraft:chain to minecraft:iron_chain when it
# added copper chains -- the data-layer twin of §V11/§V33's collections. A `set` patch cannot
# express it without copying the whole 65-entry tag into the patch file, which is the second
# hand-maintained copy this tool exists to prevent.
# --------------------------------------------------------------------------------------------
def rename_ids(doc, rel, spec):
    mapping = spec["map"]
    used = spec.setdefault("_used", set())

    def walk(node):
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        if isinstance(node, str):
            for old, new in mapping.items():
                if node == old or node == "#" + old:
                    used.add(old)
                    return node.replace(old, new)
        return node
    return walk(doc)


def advancement_display_icon_as_stack(doc, rel, spec):
    """`display.icon` is an ItemStack, and 1.21.2 renamed the stack's item field.

    1.21.1 writes `{"item": "ns:id"}`; 26.2's ItemStack codec wants `{"id": "ns:id"}` (or a bare
    string), and refuses the old spelling outright -- `No key id in MapLike[{"item":...}]` /
    `Not a string`, after which the whole advancement is dropped with no other trace. This is the
    same 1.21 `item` -> `id` rename catalog §122(b) records for RECIPE RESULTS, arriving one layer
    up on an advancement's icon, so it needs its own transform rather than riding on
    ingredients_as_strings: an ingredient becomes a STRING there and a stack must stay an OBJECT
    here, because the icon can carry `count` and `components` beside it.

    Deliberately narrow: only `display.icon`, only when it is an object, and it REFUSES an icon
    whose shape it does not recognise (§S1b) rather than passing it through unchanged.
    """
    display = doc.get("display")
    if not isinstance(display, dict) or "icon" not in display:
        return doc
    icon = display["icon"]
    if not isinstance(icon, dict):
        return doc
    if "id" in icon:
        return doc
    if "item" not in icon:
        raise SystemExit("%s: display.icon is an object with neither `item` nor `id`: %r "
                         "-- refusing rather than guessing" % (rel, icon))
    out = dict(doc)
    out["display"] = dict(display)
    out["display"]["icon"] = {("id" if k == "item" else k): v for k, v in icon.items()}
    return out


TRANSFORMS = {
    "ingredients_as_strings": ingredients_as_strings,
    "advancement_display_icon_as_stack": advancement_display_icon_as_stack,
    "entity_predicates_as_dispatched_map": entity_predicates_as_dispatched_map,
    "rename_ids": rename_ids,
}


# --------------------------------------------------------------------------------------------


def targets(workspace):
    return sorted(os.path.basename(p)[: -len(".properties")]
                  for p in glob.glob(os.path.join(workspace, "versions/*.properties")))


def overlay_of(workspace, target):
    """The overlay directory name this target uses, read from its own properties file."""
    path = os.path.join(workspace, "versions/%s.properties" % target)
    for line in open(path, encoding="utf-8"):
        line = line.split("#", 1)[0].strip()
        if line.startswith("overlay"):
            return line.split("=", 1)[1].strip()
    return None


def apply_patch(shared, patch):
    out = json.loads(json.dumps(shared))          # a copy; the shared file is never mutated
    for key in patch.get("delete", []):
        out.pop(key, None)
    for key, value in patch.get("set", {}).items():
        out[key] = value
    return out


def shared_files(workspace):
    root = os.path.join(workspace, "src/main/resources")
    for path in glob.glob(os.path.join(root, "**/*.json"), recursive=True):
        yield os.path.relpath(path, root).replace(os.sep, "/")


def rendered(doc):
    return json.dumps(doc, indent=2) + "\n"


def build_target(workspace, target, problems):
    """-> {relative path: wanted content} for every overlay file this target should have."""
    patches_path = os.path.join(workspace, "versions/%s.resource-patches.json" % target)
    if not os.path.isfile(patches_path):
        # No patch file is the NORMAL case (§V45: one data file usually serves both). Say so
        # rather than staying silent, so "this target has none" and "the tool did not look" do
        # not print the same thing.
        print("  %-8s no resource patches or transforms" % target)
        return {}

    spec = json.load(open(patches_path, encoding="utf-8"))
    if "patches" not in spec and "transforms" not in spec:
        problems.append("%s: the patch file has neither `patches` nor `transforms`" % target)
        return {}

    want = {}
    for rel, patch in sorted(spec.get("patches", {}).items()):
        shared_path = os.path.join(workspace, "src/main/resources", rel)
        if not os.path.isfile(shared_path):
            problems.append("%s: patches %s, which does not exist in src/main/resources"
                            % (target, rel))
            continue
        want[rel] = rendered(apply_patch(json.load(open(shared_path, encoding="utf-8")), patch))
    if spec.get("patches"):
        print("  %-8s %d patched file(s)" % (target, len(spec["patches"])))

    every = sorted(shared_files(workspace))
    for entry in spec.get("transforms", []):
        name = entry.get("name")
        fn = TRANSFORMS.get(name)
        if fn is None:
            problems.append("%s: no transform named %r (have: %s)"
                            % (target, name, ", ".join(sorted(TRANSFORMS))))
            continue
        matched = changed = 0
        for rel in every:
            if not any(fnmatch.fnmatch(rel, pat) for pat in entry["include"]):
                continue
            matched += 1
            before = json.load(open(os.path.join(workspace, "src/main/resources", rel),
                                    encoding="utf-8"))
            try:
                after = fn(json.loads(json.dumps(before)), rel, entry)
            except Refused as why:
                problems.append("%s: transform %r REFUSED %s:\n      %s"
                                % (target, name, rel, why))
                continue
            if after != before:
                changed += 1
                # A file two transforms both touch is rewritten from the previous result, not
                # from the shared file, so they compose instead of the last one winning.
                if rel in want:
                    want[rel] = rendered(fn(json.loads(want[rel]), rel, entry))
                else:
                    want[rel] = rendered(after)
        # §X10/§X11 in the data layer: a rewrite that quietly does nothing is as invisible as a
        # rename rule that matches nothing, so both readings are printed and both are failures.
        print("  %-8s transform %-38s matched %3d  changed %3d"
              % (target, name, matched, changed))
        if matched == 0:
            problems.append("%s: transform %r matched NO file -- its `include` is wrong, or the "
                            "shape it rewrites is gone" % (target, name))
        elif changed == 0:
            problems.append("%s: transform %r changed no file it matched -- it is a dead rule"
                            % (target, name))
        if name == "rename_ids":
            dead = sorted(set(entry["map"]) - entry.get("_used", set()))
            if dead:
                problems.append("%s: transform %r has rename(s) that matched nothing: %s"
                                % (target, name, ", ".join(dead)))
    return want


def main():
    check = "--check" in sys.argv
    workspace = next((a for a in sys.argv[1:] if not a.startswith("--")), ".")
    problems, written, checked = [], 0, 0

    for target in targets(workspace):
        overlay = overlay_of(workspace, target)
        want = build_target(workspace, target, problems)
        if want and not overlay:
            problems.append("%s declares no `overlay` in versions/%s.properties" % (target, target))
            continue
        if not overlay:
            continue

        out_root = os.path.join(workspace, "src", overlay, "resources")
        for rel, content in sorted(want.items()):
            out_path = os.path.join(out_root, rel)
            checked += 1
            if check:
                have = open(out_path, encoding="utf-8").read() if os.path.isfile(out_path) else None
                if have != want[rel]:
                    problems.append(
                        "src/%s/resources/%s is stale (or missing) -- it is DERIVED from\n"
                        "    src/main/resources/%s plus versions/%s.resource-patches.json.\n"
                        "    Regenerate: python3 tools/gen-resource-overlays.py"
                        % (overlay, rel, rel, target))
            else:
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                open(out_path, "w", encoding="utf-8").write(content)
                written += 1

        # An overlay data file nobody derives SHADOWS its shared twin (processResources adds the
        # overlay second), so it silently stops tracking it the first time the shared one changes.
        data_root = os.path.join(out_root, "data")
        for path in glob.glob(os.path.join(data_root, "**/*"), recursive=True):
            if not os.path.isfile(path):
                continue
            rel = os.path.relpath(path, out_root).replace(os.sep, "/")
            if rel in want:
                continue
            if check:
                problems.append("src/%s/resources/%s is an ORPHAN: no patch or transform derives "
                                "it, so it shadows the shared file without tracking it. Delete "
                                "it, or declare how it is derived." % (overlay, rel))
            else:
                os.remove(path)
                print("  removed orphan src/%s/resources/%s" % (overlay, rel))

    if not check and written:
        print("wrote %d overlay resource(s)" % written)
    if problems:
        print("\ngen-resource-overlays: %d problem(s)" % len(problems), file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        return 1
    print("gen-resource-overlays: %d resource(s) checked, all consistent" % checked)
    return 0


if __name__ == "__main__":
    sys.exit(main())
