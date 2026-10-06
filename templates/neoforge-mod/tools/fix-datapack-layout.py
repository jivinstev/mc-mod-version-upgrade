#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Jason Hendrickson
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
    with open(path, encoding="utf-8") as f:
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
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
    return changed


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
            if os.path.isdir(dst):                       # merge rather than clobber an existing dir
                for root, _, files in os.walk(src):
                    for fn in files:
                        rel = os.path.relpath(os.path.join(root, fn), src)
                        target = os.path.join(dst, rel)
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        shutil.move(os.path.join(root, fn), target)
                shutil.rmtree(src)
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
        print("   ✓ datapack layout is 1.21-clean")
    elif not apply:
        print("   (report only — pass --apply to make the changes)")


if __name__ == "__main__":
    main()
