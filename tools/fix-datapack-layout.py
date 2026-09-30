#!/usr/bin/env python3
"""
Move a port's data pack into the directories Minecraft 1.21 actually reads, and fix the JSON that then
starts being parsed.

    python3 tools/fix-datapack-layout.py mods/<modid>            # report only
    python3 tools/fix-datapack-layout.py mods/<modid> --apply    # do it
    python3 tools/fix-datapack-layout.py <dir> --verify          # exit 1 if anything is stranded (the gate)

WHY
---
1.21 singularised every datapack registry directory (`recipes`→`recipe`, `advancements`→`advancement`,
`loot_tables`→`loot_table`, `structures`→`structure`, and under `tags/`: `blocks`→`block`, `items`→`item`,
`fluids`→`fluid`, `entity_types`→`entity_type`, …). **The old plural directories are silently ignored — no
error, no crash, no log line.** A port that leaves them behind ships a mod whose entire data layer is dead
while looking perfectly healthy: it loads, mobs spawn, blocks place, and nothing at all reports a problem.

A space-exploration mod shipped that way: 1795 data files — 534 recipes, 562 advancements, 333 loot tables,
56 structures, 82 tags — none of them loaded. It presented to a seven-year-old as "I can't work out how to
get the parts to build a rocket", because in his game there was no recipe to find.

THE SECOND HALF
---------------
Renaming is only step one. Once the files actually load, 1.21's stricter codecs reject the 1.20 JSON, so
this also rewrites the shapes that changed:

  * recipe `result` `{"item": X}` → `{"id": X}`   (results only — NeoForge keeps an ingredient compat codec,
    and "fixing" ingredients breaks them: on 1.21.1 an ingredient is still an object.)
  * recipe `result` bare string `"mod:thing"` → `{"id": "mod:thing"}`
  * stonecutting's top-level `"count"` moves INSIDE the result.

It does NOT touch `data/forge/**` → `data/c/**`; that re-namespacing needs per-tag judgement (NeoForge's
common tags are plural nouns: `forge:leather` → `c:leathers`) and is reported for a human to do.
"""
import json
import os
import filecmp
import shutil
import sys

TOP_RENAMES = {
    "recipes": "recipe",
    "advancements": "advancement",
    "loot_tables": "loot_table",
    "structures": "structure",
    "predicates": "predicate",
    "item_modifiers": "item_modifier",
    "functions": "function",
}
TAG_RENAMES = {
    "blocks": "block",
    "items": "item",
    "fluids": "fluid",
    "entity_types": "entity_type",
    "game_events": "game_event",
    "functions": "function",
}


def plan_renames(data_root):
    """(from, to) for every stranded directory. Namespace dirs are walked explicitly so that a nested
    `advancement/recipes` — an advancement *group*, not a registry dir — is never touched."""
    moves = []
    if not os.path.isdir(data_root):
        return moves
    for ns in sorted(os.listdir(data_root)):
        ns_path = os.path.join(data_root, ns)
        if not os.path.isdir(ns_path):
            continue
        for old, new in TOP_RENAMES.items():
            src = os.path.join(ns_path, old)
            if os.path.isdir(src):
                moves.append((src, os.path.join(ns_path, new)))
        tags = os.path.join(ns_path, "tags")
        if os.path.isdir(tags):
            for old, new in TAG_RENAMES.items():
                src = os.path.join(tags, old)
                if os.path.isdir(src):
                    moves.append((src, os.path.join(tags, new)))
    return moves


def fix_recipe(path):
    """Bring one recipe's result up to the 1.21 codec. Returns a short note if it changed."""
    with open(path) as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict) or "result" not in data:
        return None

    result = data["result"]
    changed = None

    if isinstance(result, str):
        data["result"] = {"id": result}
        changed = "bare-string result"
    elif isinstance(result, dict) and "item" in result and "id" not in result:
        new = {"id": result.pop("item")}
        new.update(result)                      # keep count/components, id first for readability
        data["result"] = new
        changed = "result item->id"

    # Stonecutting used to carry its count beside the result; 1.21 wants it inside.
    if str(data.get("type", "")).endswith("stonecutting") and "count" in data:
        if isinstance(data["result"], dict):
            data["result"]["count"] = data.pop("count")
            changed = (changed + " + stonecutting count") if changed else "stonecutting count"

    if changed:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
    return changed


# ─────────────────────────── §143: the 1.21 loot-table cluster ───────────────────────────
# Every 1.20 mob-drop table fails to parse on 1.21, and it fails SILENTLY: the error is logged at
# data load and the table is then ignored, so the mob simply drops nothing. Live example, found by
# finally running a Gate C with the mod loaded: TEN of a large boss mod's infected-mob tables, in a port
# that had been shipping for weeks.
#
#   Couldn't parse element ...loot_table]:examplemod:entities/example_zombie
#     Unknown registry key in ...loot_function_type]: minecraft:looting_enchant
#
# You only SEE these once the directory rename above is done -- under `loot_tables/` the files were
# never read at all, so fixing the layout UNMASKS this whole class (catalog §144).
LOOT_FUNCTIONS = {
    # looting is data-driven now, and the replacement must name the enchantment explicitly
    "minecraft:looting_enchant": "minecraft:enchanted_count_increase",
}
# set_nbt -> set_components is in §143 too, and it is deliberately NOT automated: the payload changes
# shape as well as name (a stringified-SNBT `tag` becomes a `components` object), so a rename alone
# yields a function with no argument -- valid JSON, still dead, and now it LOOKS fixed. Reported for a
# human instead. A codemod that quietly produces a wrong result is worse than one that refuses.
LOOT_MANUAL = {"minecraft:set_nbt": "minecraft:set_components (payload must be rewritten by hand)"}
# loot-context entity targets
LOOT_ENTITIES = {"killer": "attacker", "killer_player": "attacking_player"}


def _walk_json(node, fn):
    """Apply fn to every dict in a decoded JSON tree, depth-first. Returns True if anything changed."""
    changed = False
    if isinstance(node, dict):
        changed |= bool(fn(node))
        for v in list(node.values()):
            changed |= _walk_json(v, fn)
    elif isinstance(node, list):
        for v in node:
            changed |= _walk_json(v, fn)
    return changed


def fix_loot_table(path, apply=True):
    """Bring one loot table up to the 1.21 codecs. Returns a list of notes, empty if already clean."""
    with open(path) as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            return []
    notes = []

    def visit(d):
        hit = False
        fn = d.get("function")
        if fn in LOOT_MANUAL:
            notes.append(f"MANUAL: {fn} -> {LOOT_MANUAL[fn]}")
            # deliberately not `hit` — nothing is rewritten, so the file is left untouched
        if fn in LOOT_FUNCTIONS:
            d["function"] = LOOT_FUNCTIONS[fn]
            # enchanted_count_increase must say WHICH enchantment; looting_enchant implied it.
            if d["function"] == "minecraft:enchanted_count_increase":
                d.setdefault("enchantment", "minecraft:looting")
            notes.append(f"{fn} -> {d['function']}")
            hit = True
        cond = d.get("condition")
        if cond == "minecraft:random_chance_with_looting":
            # {chance, looting_multiplier} -> {unenchanted_chance, enchanted_chance:{linear}, enchantment}
            base = d.pop("chance", 0.0)
            per = d.pop("looting_multiplier", 0.0)
            d["condition"] = "minecraft:random_chance_with_enchanted_bonus"
            d["unenchanted_chance"] = base
            d["enchanted_chance"] = {"type": "minecraft:linear",
                                     "base": base, "per_level_above_first": per}
            d["enchantment"] = "minecraft:looting"
            notes.append("random_chance_with_looting -> random_chance_with_enchanted_bonus")
            hit = True
        ent = d.get("entity")
        if isinstance(ent, str) and ent in LOOT_ENTITIES:
            d["entity"] = LOOT_ENTITIES[ent]
            notes.append(f"entity {ent} -> {d['entity']}")
            hit = True
        return hit

    rewrote = _walk_json(data, visit)
    if rewrote and apply:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
    return notes


def scan_loot(data_root, apply):
    """Returns (files_changed, note_counts) over every loot_table/ tree under data_root."""
    files, counts = 0, {}
    for root, _, fnames in os.walk(data_root):
        if os.sep + "loot_table" not in root + os.sep:
            continue
        for fn in fnames:
            if not fn.endswith(".json"):
                continue
            notes = fix_loot_table(os.path.join(root, fn), apply=apply)
            if notes:
                files += 1
                for n in notes:
                    counts[n] = counts.get(n, 0) + 1
    return files, counts


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    mod = sys.argv[1].rstrip("/")
    apply = "--apply" in sys.argv
    verify = "--verify" in sys.argv
    data_root = os.path.join(mod, "src/main/resources/data")

    moves = plan_renames(data_root)
    print(f"{mod}: {len(moves)} stranded director{'y' if len(moves)==1 else 'ies'}")
    for src, dst in moves:
        n = sum(len(fs) for _, _, fs in os.walk(src))
        print(f"   {os.path.relpath(src, data_root)}  ->  {os.path.relpath(dst, data_root)}   ({n} files)")
        if apply:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.isdir(dst):
                # BOTH spellings exist. The singular one is the directory the game actually reads,
                # so anything ALSO present under the plural name is a stale leftover the author
                # never deleted -- not content to rescue. `shutil.move` over an existing file
                # overwrites it silently, which would revert every edit made to the live copy
                # since the rename. So: identical -> drop the stale twin; different -> REFUSE and
                # name it, because which copy is wanted is a decision a person has to make
                # (S1b -- a codemod that quietly produces a wrong result is worse than one that
                # refuses). Measured on a ~660-file GeckoLib mob mod: 16 of 16 stranded files were byte-identical
                # duplicates of live ones, so a blind merge was a no-op there and would not have
                # been on the next mod.
                clashes = []
                for root, _, files in os.walk(src):
                    for fn in files:
                        rel = os.path.relpath(os.path.join(root, fn), src)
                        target = os.path.join(dst, rel)
                        if os.path.exists(target) and not filecmp.cmp(
                                os.path.join(root, fn), target, shallow=False):
                            clashes.append(rel)
                if clashes:
                    print(f"   REFUSING: {len(clashes)} file(s) exist under BOTH names with "
                          f"different content -- the live copy is the singular one, so decide "
                          f"by hand which to keep:")
                    for rel in sorted(clashes)[:20]:
                        print(f"     {rel}")
                    sys.exit(1)
                dropped = kept = 0
                for root, _, files in os.walk(src):
                    for fn in files:
                        rel = os.path.relpath(os.path.join(root, fn), src)
                        target = os.path.join(dst, rel)
                        if os.path.exists(target):
                            os.remove(os.path.join(root, fn)); dropped += 1
                        else:
                            os.makedirs(os.path.dirname(target), exist_ok=True)
                            shutil.move(os.path.join(root, fn), target); kept += 1
                shutil.rmtree(src)
                print(f"     merged: {kept} moved, {dropped} stale duplicate(s) dropped")
            else:
                shutil.move(src, dst)

    if apply:
        fixed = 0
        for root, _, files in os.walk(data_root):
            if os.sep + "recipe" not in root + os.sep:
                continue
            for fn in files:
                if fn.endswith(".json") and fix_recipe(os.path.join(root, fn)):
                    fixed += 1
        print(f"   rewrote {fixed} recipe results for the 1.21 codec")
        lfiles, lcounts = scan_loot(data_root, apply=True)
        print(f"   rewrote {lfiles} loot table(s) for the 1.21 codecs")
        for note, n in sorted(lcounts.items()):
            print(f"      {n:4d}x  {note}")

    forge = os.path.join(data_root, "forge")
    if os.path.isdir(forge):
        n = sum(len(fs) for _, _, fs in os.walk(forge))
        print(f"   ⚠ data/forge/** still present ({n} files) — needs per-tag re-namespacing to data/c/**")
        print("     (NeoForge common tags are PLURAL nouns: forge:leather -> c:leathers)")

    if verify and moves:
        # Non-zero on purpose: this is wired into `check` so a stranded data pack fails the build.
        # Documentation asking someone to remember this step did not survive contact with a
        # 1384-error compile loop; a gate does.
        raise SystemExit(
            f"\n   ✗ {len(moves)} datapack director{'y' if len(moves)==1 else 'ies'} still use pre-1.21 "
            "names.\n     Minecraft 1.21 ignores them SILENTLY — no error, no crash, and every recipe, "
            "advancement,\n     loot table and tag inside them simply does not exist.\n"
            "     Fix: python3 tools/fix-datapack-layout.py <mod> --apply")
    if verify:
        # §143 is checked even when the layout is already clean -- fixing the layout is what UNMASKS
        # it, so a port that passed the directory check months ago can still be shipping dead tables.
        lfiles, lcounts = scan_loot(data_root, apply=False)
        if lfiles:
            detail = "\n".join(f"       {n:4d}x  {note}" for note, n in sorted(lcounts.items()))
            raise SystemExit(
                f"\n   ✗ {lfiles} loot table(s) use pre-1.21 functions/conditions.\n{detail}\n"
                "     1.21 logs a parse error and then IGNORES the table, so the mob drops NOTHING —\n"
                "     no crash, and no gate fails. Fix: python3 tools/fix-datapack-layout.py <mod> --apply")
        print("   ✓ datapack layout is 1.21-clean")
    elif not apply:
        print("   (report only — pass --apply to make the changes)")


if __name__ == "__main__":
    main()
