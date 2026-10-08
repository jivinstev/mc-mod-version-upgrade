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

THE THIRD HALF: FORGE-ONLY DATA
--------------------------------
NeoForge reads none of Forge's data namespace, and says nothing either:
  * `data/<ns>/forge/{biome_modifier,structure_modifier}` → `data/<ns>/neoforge/…` (else no natural spawns)
  * `data/forge/loot_modifiers/` → `data/neoforge/loot_modifiers/` (else no global loot modifiers)
  * type / condition / model-loader ids `forge:X` → `neoforge:X`, for the X NeoForge 21.1 registers
  * `forge:conditional` recipes → the recipe itself with a `neoforge:conditions` key
  * biome tags `#forge:is_*` → the `c:` tag NeoForge ships; other `data/forge/tags/**` by a checked table
Anything not in those tables is REFUSED by name, never guessed (a common tag's new name is often plural,
`forge:leather` → `c:leathers`). `--verify` fails while any of it is left.
"""
import json
import os
import pathlib
import re
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
    with open(path, encoding="utf-8") as f:
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
        with open(path, "w", encoding="utf-8") as f:
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

# forge:X -> neoforge:X, only for the X NeoForge 21.1 registers (checked against its universal jar)
NEO_IDS = {"add_spawns", "remove_spawns", "add_features", "remove_features", "add_spawn_costs",
           "remove_spawn_costs", "and", "or", "not", "any", "loot_table_id", "mod_loaded", "item_exists",
           "tag_empty", "true", "false", "separate_transforms", "composite", "obj", "item_layers"}
NEO_KEYS = {"type", "condition", "loader"}
# Forge tags -> what NeoForge 21.1 ships (data/c/tags in its jar, or Tags.* fields); anything else is refused
FORGE_TAGS = {
    "worldgen/biome": {"is_mountain": "c:is_mountain", "is_mushroom": "c:is_mushroom", "is_snowy": "c:is_snowy",
                       "is_peak": "c:is_mountain/peak", "is_slope": "c:is_mountain/slope", "is_hot": "c:is_hot",
                       "is_cold": "c:is_cold", "is_dry": "c:is_dry", "is_wet": "c:is_wet", "is_sparse": "c:is_sparse",
                       "is_dense": "c:is_dense", "is_plains": "c:is_plains", "is_swamp": "c:is_swamp",
                       "is_desert": "c:is_desert", "is_spooky": "c:is_spooky", "is_dead": "c:is_dead",
                       "is_lush": "c:is_lush", "is_sandy": "c:is_sandy", "is_rare": "c:is_rare",
                       "is_wasteland": "c:is_wasteland", "is_void": "c:is_void", "is_underground": "c:is_underground",
                       "is_magical": "c:is_magical", "is_plateau": "c:is_plateau", "is_water": "c:is_aquatic"},
    "block": {"needs_netherite_tool": "neoforge:needs_netherite_tool", "needs_wood_tool": "neoforge:needs_wood_tool",
              "needs_gold_tool": "neoforge:needs_gold_tool"},
    # no NeoForge knife tag; c:tools/knife is the convention other mods read
    "item": {"tools/knives": "c:tools/knife"},
}


def _forge_tag(kind, name):
    return FORGE_TAGS.get(kind, {}).get(name)


def _rewrite(node, kind=None):
    """(new node, changes, refused) for forge: ids inside one JSON document."""
    changes, refused = [], []
    def walk(n):
        if isinstance(n, dict):
            out = {}
            for k, v in n.items():
                if k in NEO_KEYS and isinstance(v, str) and v.startswith("forge:"):
                    x = v[len("forge:"):]
                    if x in NEO_IDS:
                        out[k] = "neoforge:" + x; changes.append(f"{v} -> neoforge:{x}"); continue
                    refused.append(f'"{k}": "{v}"')
                out[k] = walk(v)
            return out
        if isinstance(n, list):
            return [walk(v) for v in n]
        if isinstance(n, str) and n.startswith("#forge:") and kind:
            new = _forge_tag(kind, n[len("#forge:"):])
            if new:
                changes.append(f"{n} -> #{new}"); return "#" + new
            refused.append(n)
        return n
    return walk(node), changes, refused


def _flatten_conditional(doc):
    """A forge:conditional recipe with one entry -> that recipe plus neoforge:conditions."""
    rs = doc.get("recipes") or []
    if len(rs) != 1 or not isinstance(rs[0], dict) or "recipe" not in rs[0]:
        return None
    out = dict(rs[0]["recipe"])
    out["neoforge:conditions"] = rs[0].get("conditions", [])
    return out


def forge_residue(res_root, apply):
    """Find (and with apply, convert) Forge-only data under a resources root. -> (findings, refused)."""
    data, findings, refused = os.path.join(res_root, "data"), [], []
    if os.path.isdir(data):
        for ns in sorted(os.listdir(data)):
            fdir = os.path.join(data, ns, "forge")
            if ns != "forge" and os.path.isdir(fdir):
                for sub in sorted(os.listdir(fdir)):
                    src = os.path.join(fdir, sub)
                    if sub in ("biome_modifier", "structure_modifier", "loot_modifiers"):
                        dst = os.path.join(data, ns, "neoforge", sub)
                        findings.append(f"data/{ns}/forge/{sub} -> data/{ns}/neoforge/{sub}")
                        if apply:
                            os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.move(src, dst)
                    else:
                        refused.append(f"data/{ns}/forge/{sub}: no NeoForge equivalent known")
                if apply and os.path.isdir(fdir) and not os.listdir(fdir):
                    os.rmdir(fdir)
        fns = os.path.join(data, "forge")
        if os.path.isdir(fns):
            lm = os.path.join(fns, "loot_modifiers")
            if os.path.isdir(lm):
                findings.append("data/forge/loot_modifiers -> data/neoforge/loot_modifiers")
                if apply:
                    dst = os.path.join(data, "neoforge", "loot_modifiers")
                    os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.move(lm, dst)
            tags = os.path.join(fns, "tags")
            for root, _, files in os.walk(tags) if os.path.isdir(tags) else []:
                for fn in files:
                    rel = os.path.relpath(os.path.join(root, fn), tags).replace(os.sep, "/")
                    kind, _, name = rel.rpartition("/")
                    name = name[:-5] if name.endswith(".json") else name
                    for k in sorted(FORGE_TAGS, key=len, reverse=True):     # worldgen/biome before block
                        if rel.startswith(k + "/"):
                            kind, name = k, rel[len(k) + 1:-5]
                            break
                    new = _forge_tag(kind, name)
                    if not new:
                        refused.append(f"data/forge/tags/{rel}: no known NeoForge tag for forge:{name}")
                        continue
                    nns, npath = new.split(":", 1)
                    findings.append(f"data/forge/tags/{rel} -> data/{nns}/tags/{kind}/{npath}.json")
                    if apply:
                        dst = os.path.join(data, nns, "tags", kind, npath + ".json")
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        if os.path.exists(dst):
                            refused.append(f"data/{nns}/tags/{kind}/{npath}.json already exists: merge by hand")
                        else:
                            shutil.move(os.path.join(root, fn), dst)
            if apply:
                for root, dirs, files in sorted(os.walk(fns), reverse=True):
                    if not os.listdir(root):
                        os.rmdir(root)
    for top in ("data", "assets"):
        base = os.path.join(res_root, top)
        for root, _, files in os.walk(base) if os.path.isdir(base) else []:
            for fn in files:
                if not fn.endswith(".json"):
                    continue
                path = os.path.join(root, fn)
                with open(path, encoding="utf-8") as f:
                    text = f.read()
                if '"forge:' not in text and '"#forge:' not in text:
                    continue
                try:
                    doc = json.loads(text)
                except ValueError:
                    refused.append(f"{os.path.relpath(path, res_root)}: not JSON, not touched"); continue
                rel = os.path.relpath(path, res_root).replace(os.sep, "/")
                m = re.search(r"/tags/(worldgen/biome|block|item)/", rel)
                kind = m.group(1) if m else ("worldgen/biome" if "biome_modifier" in rel or "structure" in rel else None)
                ch, structural = [], False
                if isinstance(doc, dict) and doc.get("type") == "forge:conditional":
                    flat = _flatten_conditional(doc)
                    if flat is None:
                        refused.append(f"{rel}: forge:conditional with several recipes"); continue
                    doc = flat; ch.append("forge:conditional -> neoforge:conditions"); structural = True
                new_doc, c2, rf = _rewrite(doc, kind)
                ch += c2
                refused += [f"{rel}: {r}" for r in rf]
                if ch:
                    findings.append(f"{rel}: " + "; ".join(sorted(set(ch))))
                    if apply:
                        if structural:      # a different shape: rewritten whole (one small file)
                            out = json.dumps(new_doc, indent=2, ensure_ascii=False) + "\n"
                        else:               # same shape: swap just the changed string literals, keep the formatting
                            out = text
                            for c in sorted(set(c2)):
                                a, b = c.split(" -> ")
                                out = out.replace(f'"{a}"', f'"{b}"')
                            if json.loads(out) != new_doc:
                                refused.append(f"{rel}: a literal swap did not give the expected document")
                                continue
                        with open(path, "w", encoding="utf-8") as f:
                            f.write(out)
                        if "/recipe/" in rel:
                            fix_recipe(path)
    return findings, refused



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

    ffind, frefused = forge_residue(os.path.dirname(data_root), apply)
    if ffind:
        print(f"   {'converted' if apply else 'Forge-only data to convert'}: {len(ffind)}")
        for x in ffind[:40]:
            print(f"      {x}")
    for x in frefused:
        print(f"   REFUSED (decide by hand): {x}")
    if verify and (ffind or frefused):
        raise SystemExit(
            f"\n   ✗ {len(ffind) + len(frefused)} Forge-only data item(s) remain. NeoForge reads none of them and says\n"
            "     nothing: no natural spawns, no loot modifiers, missing-model items.\n"
            "     Fix: python3 tools/fix-datapack-layout.py <mod> --apply (and the REFUSED ones by hand)")

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


def self_check():
    import tempfile, subprocess
    ok = True
    def w(root, rel, obj):
        f = pathlib.Path(root, "src/main/resources", rel); f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(obj), encoding="utf-8")
    def r(root, rel):
        return json.loads(pathlib.Path(root, "src/main/resources", rel).read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as t:
        w(t, "data/m/forge/biome_modifier/a.json", {"type": "forge:add_spawns", "biomes": {"type": "forge:and",
          "values": ["#forge:is_snowy", "#forge:is_peak"]}, "spawners": []})
        w(t, "data/forge/loot_modifiers/global_loot_modifiers.json", {"replace": False, "entries": ["m:x"]})
        w(t, "data/m/loot_modifiers/x.json", {"type": "m:t", "conditions": [{"condition": "forge:loot_table_id",
          "loot_table_id": "minecraft:chests/a"}]})
        w(t, "data/forge/tags/block/needs_netherite_tool.json", {"values": ["m:b"]})
        w(t, "data/forge/tags/item/tools/knives.json", {"values": ["m:k"]})
        w(t, "data/m/recipe/c.json", {"type": "forge:conditional", "recipes": [{"conditions": [{"type":
          "forge:mod_loaded", "modid": "z"}], "recipe": {"type": "minecraft:crafting_shapeless",
          "ingredients": [{"item": "minecraft:stick"}], "result": {"item": "m:r"}}}]})
        w(t, "assets/m/models/item/s.json", {"loader": "forge:separate_transforms"})
        w(t, "data/forge/tags/item/leather.json", {"values": ["m:l"]})          # unknown: refused, never guessed
        run = lambda *a: subprocess.run([sys.executable, __file__, t, *a], capture_output=True, text=True,
                                        encoding="utf-8")
        ok &= run("--verify").returncode != 0
        out = run("--apply").stdout
        ok &= "REFUSED (decide by hand): data/forge/tags/item/leather.json" in out
        bm = r(t, "data/m/neoforge/biome_modifier/a.json")
        ok &= bm["type"] == "neoforge:add_spawns" and bm["biomes"]["type"] == "neoforge:and"
        ok &= bm["biomes"]["values"] == ["#c:is_snowy", "#c:is_mountain/peak"]
        ok &= r(t, "data/neoforge/loot_modifiers/global_loot_modifiers.json")["entries"] == ["m:x"]
        ok &= r(t, "data/m/loot_modifiers/x.json")["conditions"][0]["condition"] == "neoforge:loot_table_id"
        ok &= r(t, "data/neoforge/tags/block/needs_netherite_tool.json")["values"] == ["m:b"]
        ok &= r(t, "data/c/tags/item/tools/knife.json")["values"] == ["m:k"]
        rc = r(t, "data/m/recipe/c.json")
        ok &= rc["type"] == "minecraft:crafting_shapeless" and rc["result"] == {"id": "m:r"}
        ok &= rc["neoforge:conditions"] == [{"type": "neoforge:mod_loaded", "modid": "z"}]
        ok &= r(t, "assets/m/models/item/s.json")["loader"] == "neoforge:separate_transforms"
        ok &= run("--verify").returncode != 0                  # the refused tag still fails the gate
        os.remove(os.path.join(t, "src/main/resources/data/forge/tags/item/leather.json"))
        v = run("--verify")
        ok &= v.returncode == 0 and "1.21-clean" in v.stdout
        ok &= "Forge-only" not in run("--apply").stdout          # idempotent
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        sys.exit(self_check())
    main()
