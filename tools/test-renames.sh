#!/usr/bin/env bash
# Prove the rename-table generators and composer do what the table needs, on synthetic inputs, so
# the test needs no Minecraft jar and publishes none.
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
pass=0; fail=0
ok()  { echo "  PASS  $1"; pass=$((pass+1)); }
bad() { echo "  FAIL  $1"; fail=$((fail+1)); }

echo "1. compose-renames.py"
printf '# header\n#!exhaustive  # inherited\n#             # block comment\nre:foo\tbar\na.b.Hand\ta.c.Hand\n\n#!strict  # own rows\n' > "$T/hand.tsv"
printf 'a.b.Hand\tWRONG.Hand\nx.y.Moved\tx.z.Moved\n' > "$T/gen.tsv"
python3 tools/compose-renames.py --hand "$T/hand.tsv" --generated "$T/gen.tsv" --generated "$T/absent.tsv" --out "$T/out.tsv" >"$T/log" 2>&1
code=$?
[ $code = 0 ] && ok "a missing generated map is reported and skipped (exit 0)" || bad "compose exit $code"
grep -q 'absent.tsv not found' "$T/log" && ok "the missing map is named" || bad "missing map not reported"
grep -q $'^a.b.Hand\ta.c.Hand$' "$T/out.tsv" && ! grep -q 'WRONG' "$T/out.tsv" && ok "a hand row beats a generated row with the same key" \
  || bad "generated row overrode or duplicated a hand row"
python3 - "$T/out.tsv" <<'PY' && ok "generated rows land INSIDE the #!exhaustive block, before #!strict" || bad "generated rows outside the exhaustive block"
import sys
L=open(sys.argv[1]).read().split('\n')
ex=next(i for i,l in enumerate(L) if l.startswith('#!exhaustive')); st=next(i for i,l in enumerate(L) if l.startswith('#!strict'))
mv=L.index('x.y.Moved\tx.z.Moved'); sys.exit(0 if ex < mv < st and L[ex+1].startswith('#  ') else 1)
PY
printf 're:a\tb\n' > "$T/nomarker.tsv"
python3 tools/compose-renames.py --hand "$T/nomarker.tsv" --out "$T/x.tsv" >/dev/null 2>&1
[ $? = 2 ] && ok "a hand table without #!exhaustive is refused (exit 2)" || bad "missing marker not refused"

echo "2. gen-color-renames.py: the rule"
python3 - "$ROOT" <<'PY'
import importlib.util, sys
s = importlib.util.spec_from_file_location("g", sys.argv[1] + "/tools/gen-color-renames.py")
g = importlib.util.module_from_spec(s); s.loader.exec_module(g)
C = g.COLLECTION
old = {"Blocks": {"WHITE_WOOL": "B", "LIGHT_BLUE_WOOL": "B", "BLUE_WOOL": "B", "WHITE_TERRACOTTA": "B",
                  "WHITE_STILL_HERE": "B", "WHITE_BOTH": "B", "STONE": "B"}}
new = {"Blocks": {"WOOL": C, "DYED_TERRACOTTA": C, "WHITE_STILL_HERE": "B", "BOTH": C, "DYED_BOTH": C, "STONE": "B"}}
rows, refused = g.generate(old, new, ["white", "lightBlue", "blue"])
r = dict(rows)
checks = {
  "plain family": r.get("Blocks.WHITE_WOOL") == "Blocks.WOOL.white()",
  "LIGHT_BLUE is not read as BLUE": r.get("Blocks.LIGHT_BLUE_WOOL") == "Blocks.WOOL.lightBlue()",
  "DYED_ prefix family": r.get("Blocks.WHITE_TERRACOTTA") == "Blocks.DYED_TERRACOTTA.white()",
  "a constant that still exists is left alone": "Blocks.WHITE_STILL_HERE" not in r,
  "an ambiguous match is refused, not guessed": "Blocks.WHITE_BOTH" not in r and len(refused) == 1,
}
bad = [k for k, v in checks.items() if not v]
print("\n".join(f"  {'FAIL' if k in bad else 'PASS'}  {k}" for k in checks))
sys.exit(len(bad))
PY
n=$?; pass=$((pass + 5 - n)); fail=$((fail + n))

echo "3. gen-color-renames.py: reading real class files"
if command -v javac >/dev/null 2>&1; then
  mk() {  # mk <dir> <blocks-body> <with-collection>
    local d="$1"; mkdir -p "$d/src/net/minecraft/world/level/block" "$d/src/net/minecraft/world/item"
    echo 'package net.minecraft.world.level.block; public class Block {}' > "$d/src/net/minecraft/world/level/block/Block.java"
    echo 'package net.minecraft.world.item; public class Item {}' > "$d/src/net/minecraft/world/item/Item.java"
    echo "package net.minecraft.world.level.block; public class Blocks { $2 }" > "$d/src/net/minecraft/world/level/block/Blocks.java"
    echo 'package net.minecraft.world.item; public class Items { public static final Item STICK = null; }' > "$d/src/net/minecraft/world/item/Items.java"
    [ "$3" = yes ] && echo 'package net.minecraft.world.level.block; public record ColorCollection<T>(T white, T lightBlue) {}' \
        > "$d/src/net/minecraft/world/level/block/ColorCollection.java"
    ( cd "$d/src" && javac -d ../cls $(find . -name '*.java') && cd ../cls && jar cf ../x.jar . ) >/dev/null 2>&1
  }
  mk "$T/old" 'public static final Block WHITE_WOOL = null; public static final Block LIGHT_BLUE_WOOL = null;' no
  mk "$T/new" 'public static final ColorCollection<Block> WOOL = null;' yes
  out="$(python3 tools/gen-color-renames.py --from-jar "$T/old/x.jar" --to-jar "$T/new/x.jar" 2>/dev/null)"
  exp=$'Blocks.LIGHT_BLUE_WOOL\tBlocks.WOOL.lightBlue()\nBlocks.WHITE_WOOL\tBlocks.WOOL.white()'
  [ "$out" = "$exp" ] && ok "fields and record components read from compiled classes" || bad "class-file reader: got [$out]"
elif [ -n "${CI:-}" ]; then
  bad "no javac in CI -- the class-file reader was NOT tested"
else
  echo "  SKIP  no javac here (CI runs it; a skip is not a pass)"
fi

echo
echo "renames self-test: $pass passed, $fail failed"
[ "$fail" = 0 ] || exit 1
