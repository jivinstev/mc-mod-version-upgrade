#!/usr/bin/env bash
# Self-test for the port-side helpers: the @Override probe and the catalogue-sweep runner.
set -uo pipefail
cd "$(dirname "$0")/.."
. tools/python.sh || exit 1   # python3 on Windows too
ROOT="$PWD"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
pass=0; fail=0
# each result line says how long its test took (since the previous result), so a slow CI run shows where the time goes
_t_last=$SECONDS
_took() { _dt=$((SECONDS - _t_last)); _t_last=$SECONDS; }    # in this shell, not a $(...) subshell
ok()  { _took; printf '  PASS %4ss  %s\n' "$_dt" "$1"; pass=$((pass+1)); }
bad() { _took; printf '  FAIL %4ss  %s\n' "$_dt" "$1"; fail=$((fail+1)); }

echo "1. override-probe.py"
if command -v javac >/dev/null 2>&1; then
  P="$T/port"; S="$P/src/main/java/org/fake"; mkdir -p "$S"
  cat > "$S/Base.java" <<'J'
package org.fake;
public class Base {
    public int getLightBlock(Object state, Object level, Object pos) { return 0; }
    public boolean kept(int x) { return true; }
}
J
  cat > "$S/Block.java" <<'J'
package org.fake;
public class Block extends Base {
    public int getLightBlock(Object state) { return 15; }
    public boolean kept(int x) { return false; }
    @Deprecated
    public void helper() { }
    public static void notProbed() { }
    public void multi(int a,
                      int b) { }
}
J
  cat > "$S/Events.java" <<'J'
package org.fake;
public class Events {
    @interface SubscribeEvent {}
    @SubscribeEvent
    public void onTick(Object e) { }
}
J
  before="$(cat "$S"/*.java | sha1sum)"
  out="$(python3 tools/override-probe.py "$P" --compile "javac -d $T/out src/main/java/org/fake/Base.java src/main/java/org/fake/Block.java src/main/java/org/fake/Events.java" 2>&1)"; code=$?
  [ $code = 1 ] && ok "orphaned overrides are reported (exit 1)" || bad "exit $code: $out"
  grep -q 'Block.java:3  getLightBlock(Object state)' <<<"$out" && ok "the old-shaped getLightBlock(state) is named" || bad "getLightBlock not named: $out"
  grep -q 'helper()' <<<"$out" && ok "a helper that never overrode is listed for a human to judge" || bad "helper missing"
  ! grep -q 'kept(' <<<"$out" && ok "a method that DOES override is not reported" || bad "false positive on kept()"
  ! grep -qE 'onTick|notProbed' <<<"$out" && ok "event handlers and static methods are not probed" || bad "probed a handler/static"
  grep -q '1 multi-line header' <<<"$out" && ok "an unprobed multi-line header is counted, not silently skipped" || bad "multi-line not counted: $out"
  [ "$(cat "$S"/*.java | sha1sum)" = "$before" ] && ok "every source file is restored byte-for-byte" || bad "sources not restored"
  sed -i.bak 's/getLightBlock(Object state)/getLightBlock(Object state, Object level, Object pos)/; s/    public void helper/    public void helperX/' "$S/Block.java" && rm -f "$S/Block.java.bak"   # -i.bak: BSD (macOS) sed and GNU sed both take it
  sed -i.bak '/@Deprecated/d; /helperX/d' "$S/Block.java" && rm -f "$S/Block.java.bak"   # -i.bak: BSD (macOS) sed and GNU sed both take it
  out="$(python3 tools/override-probe.py "$P" --compile "javac -d $T/out2 src/main/java/org/fake/Base.java src/main/java/org/fake/Block.java src/main/java/org/fake/Events.java" 2>&1)"; code=$?
  [ $code = 0 ] && grep -qE 'probing [1-9][0-9]* method' <<<"$out" && ok "CONTROL: once fixed, the probe reports nothing (exit 0) -- and it did probe something" || bad "control exit $code: $out"
elif [ -n "${CI:-}" ]; then bad "no javac in CI -- the probe was NOT tested"
else echo "  SKIP  no javac (CI runs it; a skip is not a pass)"; fi

echo "2. run-catalog-scans.sh"
P2="$T/scanport"; mkdir -p "$P2/src/main/java" "$P2/src/main/resources/data/x/neoforge/biome_modifier"
printf '{"type":"neoforge:add_spawns","t":"#forge:ores"}\n' > "$P2/src/main/resources/data/x/neoforge/biome_modifier/a.json"
out="$(bash tools/run-catalog-scans.sh "$P2" 2>&1)"; code=$?
[ $code = 0 ] && ! grep -qiE 'syntax error|command not found' <<<"$out" && ok "the sweep runs as valid shell (exit 0, no shell errors)" || bad "sweep exit $code"
grep -q 'sweep complete' <<<"$out" && ok "the sweep runs to its end" || bad "sweep did not complete"
grep -qx '#forge:ores' <<<"$out" && ! grep -qx 'forge:add_spawns' <<<"$out" && ok "#forge: refs are found, neoforge: ids are not" || bad "forge regex wrong"
bash tools/run-catalog-scans.sh "$T/nosrc" >/dev/null 2>&1; [ $? = 2 ] && ok "a directory that is not a port is refused (exit 2)" || bad "non-port not refused"

echo "3. ResourceIntegrityTest.java.template (compiled against stub JUnit, run on fixtures)"
if command -v javac >/dev/null 2>&1; then
  J="$T/rit"; mkdir -p "$J/src/org/junit/jupiter/api" "$J/src/p"
  echo 'package org.junit.jupiter.api; import java.lang.annotation.*; @Retention(RetentionPolicy.RUNTIME) public @interface Test {}' > "$J/src/org/junit/jupiter/api/Test.java"
  echo 'package org.junit.jupiter.api; public class Assertions { public static void assertTrue(boolean c, String m) { if (!c) throw new AssertionError(m); } }' > "$J/src/org/junit/jupiter/api/Assertions.java"
  sed 's/PACKAGE_PLACEHOLDER/p/' templates/neoforge-mod/test-templates/ResourceIntegrityTest.java.template > "$J/src/p/ResourceIntegrityTest.java"
  cat > "$J/src/p/Run.java" <<'X'
package p;
import java.lang.reflect.*;
public class Run { public static void main(String[] a) throws Exception {
  int bad = 0; Object t = ResourceIntegrityTest.class.getDeclaredConstructor().newInstance();
  for (Method m : ResourceIntegrityTest.class.getDeclaredMethods()) {
    if (!m.isAnnotationPresent(org.junit.jupiter.api.Test.class)) continue; m.setAccessible(true);
    try { m.invoke(t); System.out.println("OK " + m.getName()); }
    catch (InvocationTargetException e) { bad++; System.out.println("FAILED " + m.getName() + ": " + e.getCause().getMessage().split("\n")[0]); } }
  System.exit(bad == 0 ? 0 : 1); } }
X
  if javac -d "$J/out" $(find "$J/src" -name '*.java') 2>"$J/javac.log"; then
    ok "the template compiles (JDK only; no Minecraft)"
    run() { java -Dmigrate.projectDir="$1" -Dmigrate.minecraftVersion="$2" -cp "$J/out" p.Run 2>/dev/null; }
    G="$T/good/src/main/resources/data/x/recipe"; mkdir -p "$G"
    printf '{"type":"minecraft:crafting_shapeless","ingredients":[{"item":"minecraft:stick"}],"result":{"id":"minecraft:stick","count":1}}\n' > "$G/a.json"
    out="$(run "$T/good" 1.21.1)"; [ $? = 0 ] && ok "CONTROL: a clean 1.21.1 tree passes all three checks" || bad "clean tree failed: $out"
    out="$(run "$T/good" 1.21.4)"; grep -q 'FAILED recipeIngredientsHaveTheTargetsForm' <<<"$out" && ok "the same {item:} recipe is caught when the target is 1.21.4" || bad "1.21.4 form not caught: $out"
    B="$T/bad/src/main/resources/data/x"; mkdir -p "$B/recipes" "$B/tags/items" "$B/recipe"
    printf '{"a": 1,}\n' > "$B/recipe/trailing.json"
    printf '{"type":"x","ingredient":"minecraft:stick"}\n' > "$B/recipe/bare.json"
    printf '// comment\n{"a": 1}\n' > "$B/tags/items/c.json"
    out="$(run "$T/bad" 1.21.1)"
    grep -q 'FAILED noPre121PluralDatapackDirectories' <<<"$out" && ok "plural datapack dirs are caught" || bad "plural not caught: $out"
    grep -q 'FAILED everyJsonFileIsStrictJson' <<<"$out" && ok "a trailing comma / a comment is caught as non-strict JSON" || bad "strict JSON not caught: $out"
    grep -q 'FAILED recipeIngredientsHaveTheTargetsForm' <<<"$out" && ok "a bare-string ingredient is caught on 1.21.1" || bad "bare string not caught: $out"
  else bad "the template does not compile: $(head -5 "$J/javac.log")"; fi
elif [ -n "${CI:-}" ]; then bad "no javac in CI -- the resource test was NOT tested"
else echo "  SKIP  no javac"; fi

echo "4. codemods and the scan runner"
C="$T/cm"; mkdir -p "$C/src"
cat > "$C/src/A.java" <<'X'
class A {
  Object a = new ResourceLocation("mod:x");
  Object b = new ResourceLocation("mod", name(1, 2));
  Object c = new ResourceLocation(ns, "p" + f(a, b));
}
X
python3 tools/srg-remap/mc121_codemod.py "$C/src" >/dev/null 2>&1
{ grep -q 'a = ResourceLocation.parse("mod:x")' "$C/src/A.java" \
  && grep -q 'b = ResourceLocation.fromNamespaceAndPath("mod", name(1, 2))' "$C/src/A.java" \
  && grep -q 'c = ResourceLocation.fromNamespaceAndPath(ns, "p" + f(a, b))' "$C/src/A.java"; } \
  && ok "mc121_codemod: 1 arg -> parse, 2 args -> fromNamespaceAndPath, commas inside nested calls ignored" \
  || bad "ResourceLocation rewrite: $(cat "$C/src/A.java")"
printf '@At(target = "Lnet/minecraftforge/common/ForgeHooks;onLivingDrops(Lnet/minecraftforge/event/ForgeEventFactory;)Z")\n' > "$C/B.txt"
perl -p tools/srg-remap/forge_import_codemod.pl "$C/B.txt" > "$C/B.out" 2>/dev/null
{ grep -q 'net/neoforged/neoforge/common/CommonHooks' "$C/B.out" && grep -q 'EventHooks' "$C/B.out" \
  && ! grep -q minecraftforge "$C/B.out"; } && ok "forge_import_codemod rewrites the slash form (descriptors, mixin targets)" \
  || bad "slash form: $(cat "$C/B.out")"
printf 'Set.of("net.minecraft.", "net.minecraftforge.", "cpw.mods.");\n' > "$C/W.txt"
perl -p tools/srg-remap/forge_import_codemod.pl "$C/W.txt" > "$C/W.out" 2>/dev/null
grep -q '"net.minecraft.", "net.neoforged.", "cpw.mods."' "$C/W.out" \
  && ok "forge_import_codemod: a loader package-prefix string (a transform whitelist) follows the loader" \
  || bad "prefix string: $(cat "$C/W.out")"
L="$T/link"; mkdir -p "$L"
case "$(uname -s)" in
  # Git Bash's ln -s COPIES a directory; a user's workspace has a junction (what setup makes without
  # Developer Mode), so test through one. // keeps Git Bash from rewriting /c and /J as paths.
  MINGW*|MSYS*) cmd //c mklink //J "$(cygpath -w "$L/tools")" "$(cygpath -w "$ROOT/tools")" >/dev/null ;;
  *) ln -s "$ROOT/tools" "$L/tools" ;;
esac
out="$(bash "$L/tools/run-catalog-scans.sh" "$C" 2>&1)"; code=$?
! grep -q "No such file" <<<"$out" && [ $code = 0 ] && ok "run-catalog-scans.sh works through a symlinked tools/ (the workspace layout)" \
  || bad "run-catalog-scans via symlink (exit $code): $(head -3 <<<"$out")"

echo "5. the Windows stand-ins (they run everywhere, so a drift shows up on Linux CI too)"
J="$ROOT/templates/neoforge-mod/gradle/wrapper/gradle-wrapper.jar"
n="$(python3 tools/zipls.py -l "$J" | grep -c '\.class$')"
[ "$n" -gt 10 ] && [ "$n" = "$(python3 tools/zipls.py -Z1 "$J" | grep -c '\.class$')" ] \
  && ok "zipls -l / -Z1 list the jar ($n classes), LF-terminated so grep's \$ matches" || bad "zipls listing: $n classes"
python3 tools/zipls.py -p "$J" META-INF/MANIFEST.MF | grep -q '^Manifest-Version' \
  && ok "zipls -p prints a member" || bad "zipls -p"
X="$T/zx"; python3 tools/zipls.py -o -q "$J" 'META-INF/*' -d "$X" && [ -f "$X/META-INF/MANIFEST.MF" ] \
  && ok "zipls extracts the matching members" || bad "zipls extract"
# X170: a member table that fails to load made the fixer exit 1, which the hop read as "0 rewritten" for weeks
for MT in tools/recipes/*members*.tsv; do
  python3 -c "import importlib.util,sys; s=importlib.util.spec_from_file_location('m','tools/fix-missing-members.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); m.table(sys.argv[1])" "$MT" \
    && ok "member table loads: $(basename "$MT")" || bad "member table does not load: $MT"
done
printf 'Item\tx\t{recv}.y(z)\n' > "$T/bad-members.tsv"
python3 tools/fix-missing-members.py --src "$T" --log /dev/null --table "$T/bad-members.tsv" >/dev/null 2>&1 \
  && bad "a broken member table must exit non-zero" || ok "a broken member table exits non-zero"
if command -v unzip >/dev/null 2>&1 && ! grep -q "mc-mod-version-upgrade's setup" "$(command -v unzip)" 2>/dev/null; then
  [ "$(unzip -Z1 "$J")" = "$(python3 tools/zipls.py -Z1 "$J")" ] && ok "zipls -Z1 matches unzip -Z1" || bad "zipls differs from unzip"
fi
# boot-smoke's "is the game already running on this instance?" must see a real process: on Windows
# `ps -axo` does not exist, and a probe that sees nothing reads exactly like "nothing running".
I="$T/inst"; mkdir -p "$I"
python3 -c 'import time; time.sleep(90)' net.minecraft.client.main.Main --gameDir "$I" &
FAKE=$!
sleep 3
seen="$(python3 - "$I" <<'PY'
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("bs", "tools/boot-smoke.py")
bs = importlib.util.module_from_spec(spec); spec.loader.exec_module(bs)
print(len(bs.game_already_running(pathlib.Path(sys.argv[1]))))
PY
)"
kill "$FAKE" 2>/dev/null; wait "$FAKE" 2>/dev/null
[ "${seen:-0}" -ge 1 ] && ok "boot-smoke sees a client already running on the instance" \
  || bad "boot-smoke's process probe missed a running client (saw ${seen:-nothing})"

# boot-smoke finds the instance from the mods folders setup records: per version (MINECRAFT_MODS_DIR_1_21_1),
# with '' for a version that has no install -- not only the single key older checkouts carry.
B="$T/bs"; mkdir -p "$B/repo/tools" "$B/game-1.21.1/mods"; cp tools/boot-smoke.py "$B/repo/tools/"
# The file holds the path as setup writes it: native (D:/a/... on Windows). Git Bash converts the
# arguments it hands Windows Python, never a file's contents, so /d/a/... in .env.local would not resolve.
W="$B"; command -v cygpath >/dev/null 2>&1 && W="$(cygpath -m "$B")"
printf 'MINECRAFT_MODS_DIR_26_2=\nMINECRAFT_MODS_DIR_1_21_1=%s\n' "$W/game-1.21.1/mods" > "$B/repo/.env.local"
found="$(env -u MINECRAFT_DIR python3 - "$B" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("bs", sys.argv[1] + "/repo/tools/boot-smoke.py")
bs = importlib.util.module_from_spec(spec); spec.loader.exec_module(bs)
bs.DEFAULT_INSTANCE = sys.argv[1] + "/no-default"
found = bs.find_instance(None)
print(found.as_posix() if found else None)
PY
)"
[ "$found" = "$W/game-1.21.1" ] && ok "boot-smoke finds the instance from setup's per-version mods key" \
  || bad "boot-smoke instance from MINECRAFT_MODS_DIR_1_21_1: got '${found}'"

echo "6. recipe-bench.py (bucketer A/B, and it refuses to count a build that never compiled)"
out="$(python3 tools/recipe-bench.py --self-check 2>&1)"
grep -q 'self-check: PASS' <<<"$out" && ok "bucketer: specific entries matched, generic + near-miss names left unmatched" \
  || bad "recipe-bench self-check: $out"
printf 'FAILURE: Build failed\n* What went wrong:\nCould not resolve all files\n' > "$T/never.log"
python3 tools/recipe-bench.py --bucket-log "$T/never.log" >/dev/null 2>&1; code=$?
[ $code = 2 ] && ok "a log with no compileJava task is NOT a count (exit 2)" || bad "never-compiled log exited $code, wanted 2"
printf '> Task :compileJava FAILED\n* What went wrong:\n> Could not resolve all files for configuration\n' > "$T/unres.log"
python3 tools/recipe-bench.py --bucket-log "$T/unres.log" >/dev/null 2>&1; code=$?
[ $code = 2 ] && ok "compileJava FAILED on an unresolvable dependency is NOT 0 errors (X5d)" || bad "unresolved-dep log exited $code, wanted 2"
printf '> Task :compileJava\n/w/A.java:3: error: cannot find symbol\nCaused by: java.lang.OutOfMemoryError: Java heap space\n' > "$T/oom.log"
python3 tools/recipe-bench.py --bucket-log "$T/oom.log" >/dev/null 2>&1; code=$?
[ $code = 2 ] && ok "errors printed before an OutOfMemoryError are NOT a count (X5e)" || bad "OOM log exited $code, wanted 2"
printf '> Task :compileJava\n/w/A.java:3: error: cannot find symbol\n100 errors\nonly showing the first 100 errors, of 2254 total; use -Xmaxerrs if you would like to see more\n' > "$T/cap.log"
python3 tools/recipe-bench.py --bucket-log "$T/cap.log" >/dev/null 2>&1; code=$?
[ $code = 2 ] && ok "a log cut at javac's error cap is NOT a count (X5f)" || bad "capped log exited $code, wanted 2"
printf '> Task :compileJava\n/w/A.java:3: error: package net.minecraftforge.common does not exist\n1 error\n' > "$T/one.log"
out="$(python3 tools/recipe-bench.py --bucket-log "$T/one.log" --json "$T/one.json" 2>&1)"
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d["errors"]==1 and sum(d["buckets"].values())==1 else 1)' "$T/one.json" \
  && ok "a real catalogue signature (Forge package) is attributed from the live CATALOG.md" || bad "bucket-log: $out"

echo "7. hunk-census.py (attribution A/B on a synthetic catalogue and trees)"
out="$(python3 tools/hunk-census.py --self-check 2>&1)"
grep -q 'self-check: PASS' <<<"$out" && ok "census: field, prose-arrow and SRG-shape detectors attribute; comment-only and renames-with-no-entry do not" \
  || bad "hunk-census self-check: $out"

echo "8. census-hypotheses.py (H1/H1x/H2/H5/H7 arithmetic on synthetic records)"
H="$T/hyp"; mkdir -p "$H/r1" "$H/r2@26.2" "$H/r3"
printf 'profile\tport\n' > "$H/census.tsv"; printf 'P1\tr1\nP2\tr2@26.2\nP2\tr3\n' >> "$H/census.tsv"
# r2@26.2 shares r1's entry edit and one of its cluster edits (2 mods); r3 repeats only the entry edit (3 mods)
{ echo '{"cat":"entry","key":"9","file":"b/A.java","shape":"S9","removed":["Old"],"added":["Shim"],"imports":[]}'
  echo '{"cat":"cluster","key":"x","file":"b/C.java","shape":"S8","removed":["Q"],"added":[],"imports":[]}'; } > "$H/r2@26.2/hunks.jsonl"
echo '{"cat":"entry","key":"9","file":"c/A.java","shape":"S9","removed":["Old"],"added":["Shim"],"imports":[]}' > "$H/r3/hunks.jsonl"
{ echo '{"cat":"new-file","file":"a/Shim.java","lines":9}'
  echo '{"cat":"entry","key":"9","file":"a/A.java","shape":"S1","removed":["Old"],"added":["Shim"],"imports":[]}'
  echo '{"cat":"entry","key":"9","file":"a/B.java","shape":"S1","removed":["Old"],"added":["Shim"],"imports":[]}'
  echo '{"cat":"cluster","key":"x","file":"a/C.java","shape":"S2","removed":["Q"],"added":["Shim"],"imports":["mezz.jei"]}'
  echo '{"cat":"cluster","key":"x","file":"a/C.java","shape":"S3","removed":["Q"],"added":[],"imports":["mezz.jei"]}'; } > "$H/r1/hunks.jsonl"
mkdir -p "$H/b/r1"; echo '{"errors_by_file":{"a/A.java":2,"a/Z.java":1}}' > "$H/b/r1/bench.json"
out="$(python3 tools/census-hypotheses.py --census "$H" --bench "$H/b" 2>&1)"
grep -q '| P1 | 1 | 4 | 25% / 25% | 75% | 0% | 75% / 50% | 50% | 25% (1 rows) | 50% |' <<<"$out" \
  && ok "hypotheses: repeats exact/loose, cross-port 2+/3+ mods, helper routing, file locality, optional code" || bad "census-hypotheses: $(grep '| P1' <<<"$out")"

# the path goes in argv, not the -c text: Git Bash converts /tmp/... only in arguments
out="$(python3 -c "import importlib.util as u, pathlib, sys; s = u.spec_from_file_location('h', 'tools/census-hypotheses.py'); m = u.module_from_spec(s); s.loader.exec_module(m); print(m.h3(m.load(pathlib.Path(sys.argv[1])), min_hunks=1))" "$H" 2>&1)"
[ "$out" = "[('9', 4, 3, 1, 1.0)]" ] && ok "H3: an entry's fixes counted across 3 mods, one shape covering all of them" || bad "h3: $out"

echo "9. scope-menu.py (chunks, error shares and references on a synthetic mod)"
out="$(python3 tools/scope-menu.py --self-check 2>&1)"
grep -q 'self-check: PASS' <<<"$out" && ok "scope menu: integration, commands and a mob family found; shares from the first-compile log" \
  || bad "scope-menu self-check: $out"

echo "10. compile-summary.py (a bounded log summary; refuses a log that is not a count)"
out="$(python3 tools/compile-summary.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "summary: catalogue groups, families, files, and GREW/NEW against the previous run" \
  || bad "compile-summary self-check: $out"
d="$(mktemp -d)"; printf '> Task :compileJava FAILED\n* What went wrong:\nCould not resolve x\n' > "$d/b.log"
python3 tools/compile-summary.py "$d/b.log" >"$d/o" 2>&1; rc=$?
[ "$rc" = 2 ] && grep -q 'NOT A COUNT' "$d/o" && grep -q 'Could not resolve x' "$d/o" \
  && ok "a never-compiled log prints Gradle's reason and exits 2, not '0 errors'" || bad "never-compiled log: rc=$rc $(head -3 "$d/o")"
rm -rf "$d"

echo "11. port-handoff.py (one current Hand-off section, Scope carried across)"
out="$(python3 tools/port-handoff.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "hand-off: replaces itself, keeps other sections, carries the Scope choice and blockers" \
  || bad "port-handoff self-check: $out"

echo "12. apply-recipes.py (auto rows applied per catalogue entry; choice/manual sites listed, never rewritten)"
out="$(python3 tools/apply-recipes.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "recipes: auto groups rewrite and count, a dead group is named, choice sites stay untouched" \
  || bad "apply-recipes self-check: $out"

for pk in tools/recipes/*.recipes.tsv; do
  out="$(python3 tools/apply-recipes.py --check-pack --recipes "$pk" 2>&1)" \
    && ok "shipped pack $(basename "$pk") parses and names only catalogue entries" || bad "pack $pk: $out"
done

echo "13. file-loop.py (model-free parts: load-crash scans, supertype filter, generated-path mapping, batching)"
out="$(python3 tools/file-loop.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "file loop: R1 and client-import scans, supertype-aware probe filter, batching, rows_first (layer mode: a worker row is tried before its overlay is kept; a dropped overlay's directory is recreated)" \
  || bad "file-loop self-check: $out"

out="$(python3 tools/port-cost.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "port-cost: a session driving two layer targets and a second fork (one subagent per target) is charged once -- each target only its own requests, the parts sum to the session; a single-version checkout and a layer checkout without --target are counted whole, as before; records carry a price tier and no model name; round-table sums each version branch's COST.md records since the round's start and measures a single-version fork, with totals; round-table --session-cwd charges a scheduled session's own requests (its subagents included, another cwd not) per version at the round's marks; mark --phase splits it per fork x version x phase (a recurring phase summed, each version's rows summing to its unphased row, per-phase totals) while a marks file without phases gives today's table byte for byte" \
  || bad "port-cost self-check: $out"

out="$(python3 tools/gate-loop.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "gate loop: the deepest load-crash cause and the mod's own frames go to the worker" \
  || bad "gate-loop self-check: $out"

out="$(python3 tools/port-publish.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "publish: refuses without permission, for a pre-release or foreign release, an open S1, an unknown tag, an unpublished dependency" \
  || bad "port-publish self-check: $out"

out="$(python3 tools/fix-ledger.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "fix ledger: A/B required to drop a fix, S1 open blocks a release, a joined column matches either tag" \
  || bad "fix-ledger self-check: $out"

out="$(python3 tools/scaffold-gatec.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "scaffold-gatec: the client harness template becomes a mod's harness with no example names left" \
  || bad "scaffold-gatec self-check: $out"

for spec in "route.py|route planner: every hop finished before the next, a missing pack or row is said, never guessed" \
            "port.py|port front door: jar metadata, the hoisted-config-SPEC fix" \
            "era-hop.py|era hop: the frame, maps and rename table it needs are present" \
            "scaffold-gametest.py|baseline GameTest: the template becomes a mod's test with no example names left" \
            "run-port.py|run-port: the stop contract's estimate and block, per-run spend on a resumed log" \
            "learn-pack.py|learn-pack: recurring renames become rows; one-offs and non-platform names never do" \
            "park-optional.py|park-optional: integration packages and datagen are parked, required-dep code is not" \
            "content-census.py|content census: a declared enchantment with no 1.21 data file is missing; all present passes" \
            "fix-json-strict.py|fix-json-strict: comment lines, trailing commas and a BOM are repaired byte-safe; a missing comma is refused" \
            "fix-access-transformer.py|fix-access-transformer: SRG names remapped, an overridden widening is widened on the subclass too" \
            "forge-shapes.py|forge-shapes: tick phases, DistExecutor, modifiers, item NBT, hooks, GeckoLib colour rewritten idempotently" \
            "fix-holders.py|fix-holders: the expression under javac's caret is unwrapped, retyped or resolved, never guessed" \
            "fix-missing-members.py|fix-missing-members: a moved member is renamed only where javac names its owner type (incl. record fields gone private)" \
            "fix-block-codecs.py|fix-block-codecs: a concrete block subclass gets codec() -- simpleCodec when it has a (Properties) constructor" \
            "convert-capabilities.py|convert-capabilities: Forge capability code keeps its shape through a compat layer wired into NeoForge" \
            "convert-paintings.py|convert-paintings: a code-registered painting becomes a painting_variant data file + the placeable tag" \
            "convert-rendertypes.py|convert-rendertypes: a CompositeState becomes a pipeline + RenderSetup; reverse-Z depth, unknown shards refused" \
            "convert-core-shaders.py|convert-core-shaders: GLSL 150 loose uniforms become 26.x blocks; vanilla-set names bind to vanilla" \
            "convert-gui-hooks.py|convert-gui-hooks: render hooks become extract*, input handlers take event records; bodies kept; a render hook's own renderBackground statement is dropped (1.21.6+: the screen draws it, a second blur throws), kept and noted under a braceless if/else/loop or when the class overrides its background" \
            "srcsets.py|srcsets: every source set the author builds, their compile and jar tasks, and the target from the build" \
            "port-profile.py|port-profile: count the cost-driving port patterns per mod and name the tool covering each" \
            "convert-entity-renderstate.py|convert-entity-renderstate: vanilla entity renderers/models/layers onto 26.2 render state; refuses what it cannot rewrite exactly" \
            "convert-geckolib.py|convert-geckolib: GeckoLib 4.8 renderer/layer/model/animation hooks and asset layout onto 5.5; refuses what it cannot rewrite exactly" \
            "ci-gates.py|ci-gates: a finished port's gates in CI, worker-free pass/fail with a summary" \
            "port-ci.py|port-ci: install the fork's CI + tag-driven pre-release workflow as its own commit" \
            "port-offer.py|port-offer: the authors see the port without our CI commit; port commits after a CI commit are replayed without it; only what state.json verified is claimed" \
            "port-ci-kit.py|port-ci-kit: the scoped CI kit -- deterministic archive holding exactly the manifest, nothing from mods/ or docs/" \
            "install-port.py|install-port: a fork port and its sibling ports install from their manifests, sha256-checked, newest file on conflict; a manifest behind its fork's newest full Release is installed as tested and named NOT THE NEWEST RELEASE; --all-versions (fixtures: a library's layer release, a dependent's local layer build) installs only the versions every fork has, one game directory and launcher profile each, every target jar and dependency checked, a single-version fork its one version" \
            "port-manifest.py|port-manifest: refresh points the install manifest at the newest full Release (same platform variant) and the workflow's sibling pins, leaves registry entries alone, says unchanged, refuses a pre-release, an older tag, an ambiguous jar or an unfetchable pin" \
            "optional-dep-scan.py|optional-dep-scan: a listener class naming an optional mod in a method type blocks; a mixin reaching one through the mod is reported" \
            "port-fleet.py|port-fleet: one fleet file runs ci/gates/offer over every fork in order, flags per fork, push after each" \
            "fix-datapack-layout.py|fix-datapack-layout: Forge-only data (biome/structure/loot modifiers, forge: ids, conditional recipes, forge tags) converted to NeoForge; unknown ones refused and fail --verify" \
            "fix-optional-listeners.py|fix-optional-listeners: a subscriber method naming an optional mod moves to a helper and its callers follow; it compiles and the scan then finds nothing; an optional-typed listener is refused" \
            "dep-api.py|dep-api: per dependency, what the mod imports, overrides and names by id, against the target jar; type variables match concrete types" \
            "port-deps.py|port-deps: dependency preflight -- blocked hosts, missing target builds, local-maven fill" \
            "dep-moves.py|dep-moves: classes a dependency moved between the author's jar and the target's are found by comparing the jars, and rewritten" \
            "convert-valueio.py|convert-valueio: scalar entity/block-entity save-load onto ValueInput/ValueOutput, hurt onto hurtServer; the rest refused" \
            "audit-unit-codecs.py|audit-unit-codecs: a field-less payload class behind StreamCodec.unit is caught; a record is not" \
            "convert-override-signatures.py|convert-override-signatures: a hook whose 26.2 signature changed keeps its body in a private 1.21.1-shaped copy" \
            "port-derive.py|port-derive: a dev-branch port replayed onto the release, unreleased files and their port-only handlers dropped" \
            "mechanical-hop.py|mechanical-hop: members, then every converter in order, then members; a converter that changed nothing is not recounted; one needing the port's input is an advisory" \
            "layer-test-plan.py|layer-test-plan: per layer target, the reviewed/reopened stubs, whole-file overlays, fired rows with a no-gate note, dropped optional integrations, preview targets and (with INVENTORY.json only) each base behaviour item not the same, a shared change once with its range; a single-version fork is its one version with no layer section" \
            "layer-inventory.py|layer-inventory: the base build's mixin targets/injections (fork-port's hook_targets), event listeners, render classes and uses, core shaders with uniforms/samplers and registries; per target same through the rename tables, changed in an overlay, dropped by drops.txt or BEHAVIOUR.json (inherited), accounted by a reviewed stub or REVIEW.md, from 1.21.5 a sampler no RenderPipeline names is unaccounted (exit 16); a single-version fork has no requirement and nothing written" \
            "manual-tests.py|manual-tests: the 10 manual tests no gate reaches, one per kind first (music, client packets, bosses, keys...), with steps naming real ids" \
            "offer-page.py|offer-page: the handoff page shows each fork's install, distance from release, checks and its manual tests" \
            "minitoml.py|minitoml: the TOML reader for Pythons without tomllib reads mods.toml forms (inline tables, every string kind) as tomllib does, and refuses broken TOML" \
            "port-provenance.py|port-provenance: the release commit is matched by publish time and the unreleased commits on top are counted and listed" \
            "port-sync.py|port-sync: the author's new commits merge into the dev port branch; a conflict stops with the files named" \
            "convert-attachment-io.py|convert-attachment-io: attachment serializers onto ValueInput/ValueOutput through the CompoundTag bridge, bodies unchanged" \
            "convert-gear-tiers.py|convert-gear-tiers: the 1.21.1 tool-tier and armour classes re-supplied on 26.2 components; instanceof widened only where safe" \
            "convert-reload-weighted.py|convert-reload-weighted: reload listeners named, JSON listeners on the raw map, Weight/WeightedEntry re-supplied; idempotent" \
            "convert-client-hooks.py|convert-client-hooks: TextureSheetParticle re-supplied, initializeClient through RegisterClientExtensionsEvent, ItemProperties a logged no-op" \
            "convert-saved-data.py|convert-saved-data: SavedData.Factory + name onto SavedDataType through the CompoundTag bridge; load/save unchanged, a 1.21.1 file read once" \
            "convert-buffer-seam.py|convert-buffer-seam: MultiBufferSource as an interface over the recording seam; lambdas and nested types keep their shape" \
            "local-maven.py|local-maven: a jar a blocked maven would serve is laid out locally, sha1 from the registry" \
            "convert-simplechannel.py|convert-simplechannel: a SimpleChannel becomes payloads; directions inferred, handlers kept" \
            "normalise-imports.py|normalise-imports: inline names a port wrote become imports; the author's own and colliding names are kept" \
            "targets.py|targets: one table of what each target needs -- unknown branch names listed, build bump and post-check agree, pack formats and prompts per target" \
            "port-upstream.py|port-upstream: an unlicensed or mismatched repo stops before spending; a dropped copyright line stops the push" \
            "review-metrics.py|review-metrics: stubbed bodies, import churn, the port's unused imports and voice measured; the author's own left alone"; do
  tool="${spec%%|*}"; what="${spec#*|}"
  out="$(python3 "tools/$tool" --self-check 2>&1)"
  grep -q 'self-check: OK' <<<"$out" && ok "$what" || bad "$tool self-check: $out"
done

out="$(python3 tools/visual-review.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "visual review: black, flat and missing-texture frames are findings without a model; verdict lines parse" \
  || bad "visual-review self-check: $out"

out="$(python3 tools/behaviour-tests.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "behaviour tests: failures read from the GameTest log, a run with no summary is not a pass" \
  || bad "behaviour-tests self-check: $out"

echo "both porting routes run the same deterministic era stage"
out="$(python3 - <<'PY' 2>&1
import pathlib, re, sys
t = pathlib.Path("tools")
mh = (t / "mechanical-hop.py").read_text(encoding="utf-8")
listed = set(re.findall(r'"(convert-[\w-]+\.py)"', mh.split("CONVERTERS = [", 1)[1].split("]", 1)[0]))
forge = set(re.findall(r'"([\w-]+\.py)"', mh.split("FORGE_STEPS = [", 1)[1].split("]\n", 1)[0]))   # the 1.21.1 converters
on_disk = {p.name for p in t.glob("convert-*.py")} - forge
problems = []
if on_disk - listed:
    problems.append(f"converters in neither CONVERTERS nor FORGE_STEPS (so neither route runs them): {sorted(on_disk - listed)}")
shared = on_disk | forge | {"fix-holders.py", "fix-access-transformer.py", "fix-missing-members.py", "fix-block-codecs.py"}
needs = {"port-upstream.py": ["mh.run_stage(", "mh.run_forge_stage(", "mh.at_loop(", "mh.era_access_transformer("],
         "port.py": ['"--stage", "forge"', '"--stage", "era"']}
for route, need in needs.items():
    src = (t / route).read_text(encoding="utf-8")
    for n in need:
        if n not in src:
            problems.append(f"{route} no longer goes through the shared stage ({n})")
    stray = sorted(set(re.findall(r"[\w-]+\.py", src)) & shared)
    if stray:
        problems.append(f"{route} runs a stage tool itself (keep it in mechanical-hop.py only): {stray}")
print("\n".join(problems) or "parity OK")
PY
)"
grep -q '^parity OK$' <<<"$out" && ok "port.py and port-upstream.py both run tools/mechanical-hop.py's Forge and era stages; no route runs a stage tool itself; every era converter is listed" \
  || bad "route parity: $out"

echo "burndown-count.sh: a log that hit the capped Gradle compile is refused unless recounted (X5g)"
printf '> Task :compileJava FAILED\n/w/A.java:3: error: cannot find symbol\n1,000 errors\n' > "$T/gcap.log"
bash tools/burndown-count.sh "$T/gcap.log" >/dev/null 2>&1; code=$?
[ $code = 6 ] && ok "a Gradle compile stopped at its 1000-error cap is NOT a count (X5g)" || bad "gradle-capped log exited $code, wanted 6"
printf '> Task :compileJava FAILED\n/w/A.java:3: error: cannot find symbol\n1,000 errors\nPORT_FULL_ERRORS compileJava: uncapped javac over 2 file(s)\n/w/A.java:3: error: cannot find symbol\n/w/B.java:9: error: cannot find symbol\n2 errors\nPORT_FULL_ERRORS compileJava: javac exit 1\n' > "$T/gfull.log"
out="$(bash tools/burndown-count.sh "$T/gfull.log" 2>&1)"
grep -q 'errors = 2 ' <<<"$out" && ok "the uncapped recount is read from the same log (union of unique file:line)" || bad "recounted log: $out"
printf '> Task :compileJava\n/w/A.java:61: error: : expected\n1 error\n' > "$T/colon.log"
bash tools/burndown-count.sh "$T/colon.log" >/dev/null 2>&1; code=$?
[ $code = 4 ] && ok "javac's ': expected' (no quotes) is a parse abort, not a count of 1" || bad "colon-expected log exited $code, wanted 4"
grep -q 'GRADLE_ERROR_CAP = 1000' tools/port_gates.py && grep -q "PORT_GRADLE_CAP = '1000'" tools/maxerrs.init.gradle \
  && grep -q '1,?000 errors' tools/burndown-count.sh && grep -q '1,?000 errors' tools/file-loop.py \
  && ok "the Gradle error cap agrees in the init script, port_gates, file-loop and burndown-count" || bad "the Gradle error cap disagrees between its four copies"

out="$(python3 tools/layer-deps.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "layer-deps: pinned mods resolved per target, a required miss stops (exit 15) and writes nothing, an optional miss is excluded and recorded, a slug-pinned Modrinth mod matches the manifest by project id, an unlisted implementation dependency is required (fail closed), an earlier --port-release pin survives a re-resolve, --dry-run builds no sibling, a single-version fork is untouched, library first: a sibling target with no green Gate C / an open REVIEW.md / a jar-changing file since its gated commit is refused (checkout or release), --allow-undone-library passes a checkout only and DEPS.md says so" \
  || bad "layer-deps self-check: $out"

out="$(python3 tools/make-fork-layer.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "fork layer: init is idempotent (one hook line), add-target writes versions and mods.toml ranges, add-target --direct writes shared_chain (default off), add-target refuses a new target over an undone parent (exit 18) unless --allow-undone-parent (recorded), init --upstream / freeze record the authors' commit" \
  || bad "make-fork-layer self-check: $out"
# the 26.x calendar line, through the real command line on a single-version fixture turned layer: 26.1.2 one hop on from
# 1.21.11, Java 25, ranges to the next patch release, GeckoLib's new group as an ESCAPED substitution (Properties keys end
# at ':'), and layer-ci's plan picks 26.3 as the newest with its own JDK
L26="$T/layer26"; mkdir -p "$L26/src/main/java/com/example/mod"
echo 'package com.example.mod; class A {}' > "$L26/src/main/java/com/example/mod/A.java"
printf 'minecraft_version=1.21.1\nneo_version=21.1.228\ngeckolib_version=4.8.4\n' > "$L26/gradle.properties"; echo 'plugins {}' > "$L26/build.gradle"
python3 tools/make-fork-layer.py init --repo "$L26" >/dev/null 2>&1
for t26 in 1.21.11 26.1.2 26.2 26.3; do python3 tools/make-fork-layer.py add-target --repo "$L26" --target "$t26" --allow-undone-parent >/dev/null 2>&1; done
P26="$L26/.multiversion/targets/26.1.2/target.properties"
grep -qx 'inherits=1.21.11' "$P26" && grep -qx 'java_version=25' "$P26" && grep -qx 'toml.minecraft=\[26.1.2,26.1.3)' "$P26" \
  && grep -qxF 'substitute.software.bernie.geckolib\:geckolib-neoforge-26.1.2=com.geckolib:geckolib-neoforge-26.1.2:5.5.2' "$P26" \
  && grep -qx 'inherits=26.2' "$L26/.multiversion/targets/26.3/target.properties" \
  && ! grep -q 'substitute\|java_version=25' "$L26/.multiversion/targets/1.21.11/target.properties" \
  && ok "fork layer: 26.x targets get Java 25, the 1.21.11 -> 26.1.2 -> 26.2 -> 26.3 chain, next-patch ranges and an escaped GeckoLib substitution; 1.21.11 unchanged" \
  || bad "26.x add-target: $(cat "$P26" 2>&1)"
out="$(python3 tools/layer-ci.py plan --repo "$L26" 2>&1)"
python3 -c 'import json,sys; p=json.load(sys.stdin); sys.exit(not (p["newest"] == "26.3" and p["newest_java"] == "25"))' <<<"$out" && ok "layer-ci plan: 26.3 is the newest target (after 1.21.11), on Java 25" || bad "layer-ci plan on 26.x: $out"
out="$(python3 templates/fork-layer/tools/layer.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "fork layer: isolation rule (only .multiversion/ + the commented hook), target chains, jar comparison ignores the manifest, a direct jump (shared_chain) prepares the hop-by-hop chain's tree" \
  || bad "layer.py self-check: $out"
# R1/R4/R5 through the real command line (no Gradle, no model): a target is done only with a complete REVIEW.md, a new
# target over an undone parent is refused, a moved authors' branch stops a run to the end (exit 19), and --chain sweeps a
# range oldest first and stops at the first red target with its RESIDUE.md. Each on a layer fixture AND a single-version one.
echo "fork-port: the done record (R1), the upstream freeze (R4), the fix-lowest sweep (R5)"
RL="$T/r145"; mkdir -p "$RL"
printf 'minecraft_version=1.21.1\nneo_version=21.1.228\n' > "$RL/gradle.properties"; echo 'plugins {}' > "$RL/build.gradle"
python3 tools/make-fork-layer.py init --repo "$RL" >/dev/null 2>&1
python3 tools/make-fork-layer.py add-target --repo "$RL" --target 1.21.3 >/dev/null 2>&1
out="$(python3 tools/make-fork-layer.py add-target --repo "$RL" --target 1.21.4 2>&1)"; code=$?
[ $code = 18 ] && grep -q 'REFUSED 1.21.4' <<<"$out" && [ ! -e "$RL/.multiversion/targets/1.21.4" ] \
  && ok "add-target: a new target over an undone parent is refused (exit 18) and nothing is written" || bad "add-target over an undone parent: exit $code: $out"
python3 tools/make-fork-layer.py add-target --repo "$RL" --target 1.21.4 --allow-undone-parent >/dev/null 2>&1
grep -q '^allowed_undone_parent=1.21.3 ' "$RL/.multiversion/targets/1.21.4/target.properties" \
  && ok "add-target --allow-undone-parent: added, and the escape is recorded in target.properties" || bad "--allow-undone-parent not recorded"
python3 tools/make-fork-layer.py add-target --repo "$RL" --target 1.21.5 --allow-undone-parent >/dev/null 2>&1
out="$(python3 tools/fork-port.py --repo "$RL" --target 1.21.3 --check-done 2>&1)"; code=$?
[ $code = 18 ] && grep -q 'no green Gate C' <<<"$out" && grep -q 'no REVIEW.md' <<<"$out" \
  && ok "fork-port --check-done: not done without Gate C and REVIEW.md (exit 18, each reason listed)" || bad "--check-done: exit $code: $out"
# TEST-ITEMS.md: --check-done names the command that writes it alone; --write-test-items writes it and nothing else
grep -q -- "--target 1.21.3 --write-test-items" <<<"$out" || bad "--check-done does not name --write-test-items: $out"
before="$(cd "$RL" && find . -path ./.git -prune -o -type f -print | sort)"
out="$(python3 tools/fork-port.py --repo "$RL" --target 1.21.3 --write-test-items 2>&1)"; code=$?
after="$(cd "$RL" && find . -path ./.git -prune -o -type f -print | sort)"
[ $code = 0 ] && [ "$(comm -13 <(echo "$before") <(echo "$after"))" = "./.multiversion/targets/1.21.3/TEST-ITEMS.md" ] \
  && [ ! -e "$RL/build/.fork-port.lock" ] && ! grep -q 'TEST-ITEMS' <<<"$(python3 tools/fork-port.py --repo "$RL" --target 1.21.3 --check-done 2>&1)" \
  && ok "fork-port --write-test-items: writes only TEST-ITEMS.md (no stage, no gate), releases the lock; --check-done no longer lists it" \
  || bad "--write-test-items: exit $code: $out"
# the sweep: --from-stage cost --gates none runs no Gradle; 1.21.3 is green-but-not-done (17), 1.21.4 has an open stub (red, 10)
printf '[{"file": "x/A.java", "why": "emptied f()", "reviewed": null}]\n' > "$RL/.multiversion/targets/1.21.4/STUBS.json"
out="$(python3 tools/fork-port.py --repo "$RL" --chain 1.21.3..1.21.5 --from-stage cost --gates none 2>&1)"; code=$?
[ $code = 10 ] && grep -q '^| 1.21.3 | 17 | green, NOT done' <<<"$out" && grep -q '^| 1.21.4 | 10 | RED at cost.*| .multiversion/targets/1.21.4/RESIDUE.md |$' <<<"$out" \
  && grep -q '^| 1.21.5 | - | not run' <<<"$out" && grep -q -- '--chain 1.21.4..1.21.5' <<<"$out" && [ ! -e "$RL/build/.fork-port.lock" ] \
  && ok "fork-port --chain: oldest first, stops at the first red target naming its RESIDUE.md, the table and the command to sweep on" \
  || bad "fork-port --chain: exit $code: $out"
# the freeze: the authors push after the buildout started -> a run to the end stops with exit 19 naming keep-current
UP="$T/r145-authors"; mkdir -p "$UP"; git -C "$UP" init -q -b main; git -C "$UP" config user.email a@b; git -C "$UP" config user.name a
echo a > "$UP/A.java"; git -C "$UP" add -A; git -C "$UP" commit -qm one
git clone -q -o upstream "$UP" "$T/r145-fork"; RF="$T/r145-fork"; git -C "$RF" config user.email a@b; git -C "$RF" config user.name a
cp "$RL/gradle.properties" "$RL/build.gradle" "$RF/"
python3 tools/make-fork-layer.py init --repo "$RF" --upstream upstream/main >/dev/null 2>&1
grep -q "^upstream_commit=$(git -C "$UP" rev-parse HEAD)$" "$RF/.multiversion/base.properties" \
  && ok "make-fork-layer init --upstream records the authors' commit the buildout starts from" || bad "init --upstream: $(cat "$RF/.multiversion/base.properties")"
mkdir -p "$RF/.multiversion/targets/1.21.3"; git -C "$RF" add -A; git -C "$RF" commit -qm layer
echo b > "$UP/A.java"; git -C "$UP" commit -qam two
out="$(python3 tools/fork-port.py --repo "$RF" --target 1.21.3 --from-stage cost --gates none 2>&1)"; code=$?
[ $code = 19 ] && grep -q 'upstream moved' <<<"$out" && grep -q 'port-sync.py' <<<"$out" \
  && ok "fork-port: the authors' branch moved since the freeze -> exit 19, naming the keep-current command" || bad "upstream drift: exit $code: $out"
out="$(python3 tools/fork-port.py --repo "$RF" --target 1.21.3 --from-stage cost --gates none --accept-upstream-drift 2>&1)"; code=$?
[ $code = 17 ] && grep -q 'accepted' "$RF/.multiversion/DRIFT.md" \
  && ok "fork-port --accept-upstream-drift continues (here to exit 17) and records the drift in DRIFT.md" || bad "--accept-upstream-drift: exit $code: $out"
# done includes the base behaviour inventory (layer-inventory.py): a layer without INVENTORY.json, then one whose target
# has no prepared tree yet, is not done -- each named in --check-done's reasons
out="$(python3 tools/fork-port.py --repo "$RF" --target 1.21.3 --check-done 2>&1)"; code=$?
echo x=1 > "$RF/.multiversion/targets/1.21.3/target.properties"
mkdir -p "$RF/src/main/java/com/example/mod"; echo 'class A {}' > "$RF/src/main/java/com/example/mod/A.java"
python3 tools/layer-inventory.py build --repo "$RF" >/dev/null 2>&1
out2="$(python3 tools/fork-port.py --repo "$RF" --target 1.21.3 --check-done 2>&1)"; code2=$?
[ $code = 18 ] && grep -q 'behaviour inventory: no .multiversion/INVENTORY.json: run tools/layer-inventory.py build' <<<"$out" \
  && [ $code2 = 18 ] && grep -q 'behaviour inventory: no prepared tree build/mc1.21.3/mv/java' <<<"$out2" \
  && ok "fork-port --check-done on a layer: the behaviour inventory is on the done list (no INVENTORY.json, then no prepared tree: exit 18, named)" \
  || bad "inventory in --check-done: $code: $out / $code2: $out2"
# single-version: no .multiversion/ -- fork-port stops at setup exactly as before; --chain and --check-done are bad input
SV="$T/r145-single"; mkdir -p "$SV"; cp "$RL/gradle.properties" "$RL/build.gradle" "$SV/"
out="$(python3 tools/fork-port.py --repo "$SV" --target 1.21.4 2>&1)"; code=$?
[ $code = 2 ] && grep -q 'no .multiversion/ in the fork' <<<"$out" && ! grep -qi 'upstream\|review\|NOT done' <<<"$out" && [ ! -e "$SV/.multiversion" ] \
  && ok "fork-port on a single-version fork: stops at setup (exit 2) as before; no freeze, no review, nothing written" || bad "single-version fork-port: exit $code: $out"
python3 tools/fork-port.py --repo "$SV" --chain 1.21.3..1.21.4 >/dev/null 2>&1; c1=$?
python3 tools/fork-port.py --repo "$SV" --target 1.21.4 --check-done >/dev/null 2>&1; c2=$?
python3 tools/fork-port.py --repo "$SV" --target 1.21.4 --write-test-items >/dev/null 2>&1; c3=$?
[ $c1 = 2 ] && [ $c2 = 2 ] && [ $c3 = 2 ] && [ ! -e "$SV/.multiversion" ] && [ -z "$(find "$SV" -name TEST-ITEMS.md -o -name .fork-port.lock)" ] \
  && ok "single-version: --chain, --check-done and --write-test-items are bad input (exit 2), nothing written" || bad "single-version --chain $c1 / --check-done $c2 / --write-test-items $c3"
out="$(python3 tools/fork-port.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "fork-port: stage selection, moves table, residue report, STOP block, layer.py patch, every --from-stage resume re-measures, one run per checkout, direct-jump shared_chain agrees with layer.py, a red full gate run is written to GATES-RED.md and keeps GATES.md byte for byte (not done, auto runs full, a green run deletes it), a killed or memory-refused Gate C is environmental (RESIDUE.md: re-run when memory is free, no fixer), --gates none and an open stub after --from-stage cost are not done, --port-release reaches layer-deps and the resume command, done needs REVIEW.md (for the commit reviewed), TEST-ITEMS.md and every base behaviour inventory item accounted for (layer-inventory.py; none on single-version), setup builds INVENTORY.json once when missing (layer only), a library target not done stops setup with exit 18 and --allow-undone-library is passed through, recorded and resumed, a new target over an undone parent is refused, the upstream freeze stops a run when the authors moved, --chain sweeps up and stops at the first red -- each also on a single-version fixture" \
  || bad "fork-port self-check: $out"
out="$(python3 templates/multi-version/tools/gametest_adapter.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "gametest adapter: both dialects (26.x Identifier / 1.21.5 ResourceLocation + mod-bus attribute) strip the dead annotations and register every test" \
  || bad "gametest_adapter self-check: $out"
out="$(python3 - <<'PY' 2>&1
import importlib.util, pathlib
s = importlib.util.spec_from_file_location("pg", "tools/port_gates.py"); m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
assert [m.no_gametest_annotation(v) for v in ("1.21.1", "1.21.4", "1.21.5", "1.21.10", "26.2")] == [False, False, True, True, True]
assert pathlib.Path("templates/upstream-harness/versions/1.21.5.renames.tsv").read_text().count("snapTo") >= 1
print("self-check: OK")
PY
)"
grep -q 'self-check: OK' <<<"$out" && ok "port_gates: annotation-free GameTest harness from 1.21.5 on, 1.21.1-1.21.4 left alone" \
  || bad "port_gates check: $out"

# ---- the known-issues ledger (tools/known-issues.py, issue #135 part 1): in-process, one tiny git repo -- seconds
echo "known-issues.py: the S1-S4 ledger at the head branch"
out="$(python3 tools/known-issues.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "known-issues self-check (in $(grep -o '[0-9.]* s)$' <<<"$out" | tr -d ')')): the rubric grades S1-S4 from its inputs and an owner override wins and is recorded; only an S1 reopens a done version; add/grade/status write atomically with sorted keys and a small diff; a bad entry exits 2; list filters by version and severity; select takes every S1, S2s lowest version first up to the budget, S3 only in touched files, then the owner's add/remove; render-issue sorts by severity; release-notes lists S1-S3 open at the tag (a fix after the tag still open); the ledger read from the head ref; a ledger edit stales no gate (ci-gates layer tree, single-version HEAD unchanged; port-publish, fork-port, fork-branches) and Port CI's push trigger ignores it; security and build grade S1; a cf-only S1 blocks-publish cf and not mr, and mr's release notes omit it; a root README edit is the same (a README under src/main/resources still counts); render-plan pre-ticks the defaults, qualifies ids two forks share and prefixes [decision needed]; read-plan counts the owner team's and writers' replies, ignores a drive-by's and bad ones, and writes each fork's --apply-decisions file; render-ports rows from the releases or tags, and only the marked block of a CRLF README changes" \
  || bad "known-issues self-check: $(tail -5 <<<"$out")"
# ---- the weekly cycle (tools/weekly-plan.py, issue #135 part 2a): the plan workflow's golden, its Pacific-time gate on
# both sides of daylight saving, install / round-task, and the steps against a fake GitHub on two tiny single-version
# forks -- no network, seconds
echo "weekly-plan.py: the Tuesday plan workflow and the Wednesday round task"
# the README names the weekly round's new flags and limits (review of #147)
python3 - <<'PY' && ok "README: round-table --session DIR, MMVU_GH_TIMEOUT and the REST fallback, round-preflight --versions/--no-client, the JDK search" || bad "the README no longer names the round's new flags (round-table --session, MMVU_GH_TIMEOUT, preflight --versions/--no-client, the JDK search)"
import pathlib, sys
r = " ".join(pathlib.Path("README.md").read_text(encoding="utf-8").split())
sys.exit(0 if all(x in r for x in ("`--session DIR`", "MMVU_GH_TIMEOUT", "falls back to REST", "[--versions 1.21.1,26.3] [--no-client]",
                                   "`JAVA_HOME` first, then `JAVA_HOME_<n>`")) else 1)
PY
# a version's work branch waits for the lower version's merge, and the 1.21.x -> 26.x conflicts are resolved by hand inside
# the cascade (dry run, 2026-10-18: the skill did not say either)
python3 - <<'PY' && ok "keep-current, version branches: keep-current/<date>-<v> is pushed only after the lower version's PR merged; the 1.21.x -> 26.x rename conflicts resolved by hand inside the cascade" || bad "keep-current no longer says a version's work branch waits for the lower merge, or how the 26.x boundary is crossed"
import pathlib, sys
s = pathlib.Path(".claude/skills/keep-current/SKILL.md").read_text(encoding="utf-8")
v = s[s.index("## A fork with version branches"):s.index("## A fork with the layer")]
need = ("is pushed only after the PR of the version below it is\n   merged", "never stacked on an unmerged lower PR",
        "resolve them\n   by hand inside the cascade", "26.1.2..<newest>")
need += ("With the **plain cascade**", "answered through step 2's decisions")
setup = s[s.index("## Setup: the weekly cycle"):]
flat = lambda x: " ".join(x.split())                 # the wording, not where its lines wrap
sys.exit(0 if all(flat(n) in flat(v) for n in need) and "merge them in chain order by step 6's" not in flat(v)
         and "outside every fork checkout" in flat(setup) else 1)
PY
# the round's cost (2026-10-19, ~$105: ~87% cache reads and writes of the orchestrator's ~565k-token context; four serial CI
# waits per version, five freeze-only push runs, nine ledger/README runs on the head): the orchestrator stays small, waits
# block, the freeze rides in the version's PR, a version's release is dispatched right after its merge -- version branches
# only: a single-version fork keeps its order
python3 - <<'PY' && ok "keep-current, the round's cost: fresh worker subagents with ~40-line summaries, one blocking wait, round/NOTES.md past ~200k tokens; on a version branch the freeze in its PR, the release dispatched right after the merge (its own full gates on the released tree), one full local gate run after the review's fixes, the head's workflow named by ci-check; a single-version fork keeps push run then release" || bad "keep-current no longer keeps the orchestrator small, or the version-branch CI order regressed, or it reached the single-version order"
import pathlib, sys
s = pathlib.Path(".claude/skills/keep-current/SKILL.md").read_text(encoding="utf-8")
flat = lambda x: " ".join(x.split())
r = flat(s[s.index("## The weekly round"):s.index("## README Ports block")])
v = flat(s[s.index("## A fork with version branches"):s.index("## A fork with the layer")])
run6 = flat(s[s.index("6. **Push the work branch and open a PR"):s.index("7. **Dependents next.**")])
need_r = ("fresh subagents that return a short summary -- at most ~40 lines", "Wait in ONE blocking command",
          "`gh run watch <run id> --exit-status`", "round/NOTES.md", "past ~200k tokens",
          "A worker MAY carry the same change across several versions", "fresh worker per version",
          "On a version branch, dispatch it right after the merge", "no pre-release is cut without a green full-gate run on exactly the released tree",
          "A single-version fork keeps step 6's order", "--from-stage measure --gates full", "ci --repo DIR --head --run",
          "on a single-version fork `ci-gates.py --env full` and `minimal`, as always")
need_v = ("Move each version's freeze in its PR, not after it", "before its work branch is pushed")
sys.exit(0 if all(n in r for n in need_r) and all(n in v for n in need_v)
         and "Then wait for the CI run the merge itself triggers" in run6 and "After merging, start the fork's release run" in run6 else 1)
PY
# the weekly round's per-version order (dry run, 2026-10-18: the round reviewed each version after it merged): the review
# comes before the merge and the pre-release, and a not-ok row graded S1/S2 at that version blocks both
python3 - <<'PY' && ok "keep-current, the weekly round: gates, then the review, then the merge, then the pre-release; an S1/S2 not-ok row at the version blocks its merge and pre-release" || bad "keep-current's weekly round no longer reviews each version before its merge and pre-release"
import pathlib, sys
s = pathlib.Path(".claude/skills/keep-current/SKILL.md").read_text(encoding="utf-8")
r = s[s.index("## The weekly round"):s.index("## README Ports block")]
step = r[r.index("4. **Per version, in ladder order**"):r.index("5. **New findings.**")]
pos = [step.find(x) for x in ("gates A/B/C in full", "one full review against the fixed rubric, before the merge",
                              "blocks that version's merge and its pre-release", "the merge of the version's PR",
                              "a pre-release through Port CI")]
run6 = s[s.index("6. **Push the work branch and open a PR"):s.index("7. **Dependents next.**")]
sv = r[r.index("   - **A single-version fork:**"):r.index("   - **A version-branch fork with `head_cascade: true`**")]
hc = r[r.index("   - **A version-branch fork with `head_cascade: true`**"):r.index("   - **A version-branch fork with `head_cascade: false`**")]
# review of #147: Run step 6 (auto-merge, release=true) must not run ahead of the round's review, for a single-version
# fork as for a version-branch one, and the review has a single-version form
ok6 = ("In the weekly round the merge and the release run wait for the round's step 4" in run6
       and "only after step 4" in sv and "only after step 4" in hc and "the cascade after that merge" in hc
       and "git diff --name-only" in step and "single-version fork" in step)
sys.exit(0 if all(p >= 0 for p in pos) and pos == sorted(pos) and "The order is fixed" in step and ok6 else 1)
PY
out="$(python3 tools/weekly-plan.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "weekly-plan self-check (in $(grep -o '[0-9.]* s)$' <<<"$out" | tr -d ')')): the workflow equals tools/golden/weekly-plan.yml (Tuesdays 15:00 and 16:00 UTC plus workflow_dispatch, contents: read and issues: write, no secrets, the tools at a pinned commit, no model); its first step lets only the 08:00 Los Angeles slot through on both sides of daylight saving (and with no tz database); the deadline is the next Wednesday round; install refuses an org-owned fork without plan_owner, a checkout off the head branch and a head that is not the default branch (unless --default-branch-too), and is idempotent; the round task is CRON_TZ=America/Los_Angeles with a jittered minute, a fresh session, push and email on; the allow list is empty by default (the round asks) and rendered verbatim into the prompt, automerge follows merge-fork-prs; the round's own work branches and PRs fall under merge-fork-prs, the ledger and README Ports block under push-version-branches, plan-issue comments and ledger-issue edits are always allowed and the migrator repository is only read; the round clones current main and says the plan ran at migrator_sha, which install records; a re-install over an existing config (a single-version fork and a layer fork with layer_branch in one set) warns on stderr for each allow, ledger_issues, depends_on and layer_branch its flags drop, naming the flag, keeps the default result otherwise unchanged (no warning on a fresh install or with every value re-passed), and --keep-existing keeps them while a flag given still wins; replaces lists exact task names (its own, --replace-task, existing keep-current names of this set); the authors' upstream comes from the fork parent when omitted; --trailer lines end the install commit; the plan issue has the deadline first, is assigned to the owner, a section per fork; a second run the same week opens nothing; an unclosed plan is carried forward (ticks and the owner's replies, not a drive-by's) and closed as superseded; an incomplete plan opens no issue; the round prompt keeps the orchestrator small (fresh worker subagents with ~40-line summaries, one blocking wait, round/NOTES.md past ~200k tokens) before the lessons rule" \
  || bad "weekly-plan self-check: $(tail -5 <<<"$out")"
# the round's lessons reach the migrator as a block in its closing comment (round of 2026-10-19: they were harvested by
# hand), for every fork kind, and a follow-up session's worklist reads it -- the round itself still writes nothing here
python3 - <<'PY' && ok "keep-current, the close: a 'Lessons for the migrator' block in the closing comment, single-version and version-branch forks alike, text only, nothing written to the migrator; harvested with propose-learnings.py --from-plan" || bad "keep-current's close no longer asks for the Lessons for the migrator block, or no longer says it is text only for every fork kind"
import pathlib, sys
s = pathlib.Path(".claude/skills/keep-current/SKILL.md").read_text(encoding="utf-8")
r = s[s.index("## The weekly round"):s.index("## README Ports block")]
close = " ".join(r[r.index("6. **Close.**"):r.index("7. **Afterwards:**")].split())
need = ("### Lessons for the migrator", "| version | symptom | fork commit | why it looks general |",
        "a single-version fork's and a version-branch fork's alike", "no tool reads or writes anything for it",
        "nothing is written to the migrator repository", "propose-learnings.py --from-plan", "| none | | | |")
sys.exit(0 if all(n in close for n in need) and close.index("Lessons for the migrator") < close.index("close the issue (state completed)") else 1)
PY
out="$(python3 tools/propose-learnings.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "propose-learnings --from-plan: the newest comment's Lessons block becomes a checklist (evidence links, catalogue hits by the symptom, a row naming a listed mod flagged); no block exits 1; --out refused inside the checkout; CATALOG.md untouched" \
  || bad "propose-learnings self-check: $(tail -5 <<<"$out")"
# ---- stacked version branches (tools/fork-branches.py, the default for a multi-version fork): migrating a layer fork, the
# branch-mode done check through fork-port.py's own CLI, the cascade, the library's release-side done check -- on a layer
# fixture; a single-version fork is untouched (its own checks above and below run unchanged)
echo "fork-branches: stacked version branches (migrate, done, cascade, library release, CLI)"
export FB_FIXTURE_CACHE="$T/fb-fixtures"    # the fork-branches fixtures, built once for every block below
# On Windows (Git Bash: msys/cygwin) only the platform leg: the line-ending cases and the port-ahead migration that
# failed natively there. The full default self-check -- every named case -- runs on Linux on every PR.
fb_mode=""; case "${OSTYPE:-}" in msys*|cygwin*|win*) fb_mode="--platform" ;; esac
out="$(python3 tools/fork-branches.py --self-check $fb_mode 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "fork-branches self-check${fb_mode:+ $fb_mode, the OS leg -- Linux runs all of}: a 3-target layer fixture migrates to byte-identical branches (every source set, resources, mixin arrays, toml ranges, versions), each done as its layer target was; the checkout untouched; without MIGRATED.json the layer records do not count; an edit stales gates and review; cascade carries the authors' change through each version's rows and a lower fix upward (never the head branch's known-issues ledger), and stops on a conflict; add-target refused over an undone parent; the library rule; a single-version fork refused, nothing written; cascade --plan on a throwaway clone (one decision per file and hunk across versions, mechanical ones merged, a default logged, an escalated one in the one issue), --apply holding only the decision files open while the versions above go on, --apply-decisions clearing them everywhere; port_taken re-offers a change no run took; ci-check names a head branch whose workflow pins an older kit or ignores no round records and ci --head regenerates it from itself (only the kit moved), while a single-version fork gets no head line and ci --head refuses it, writing nothing" \
  || bad "fork-branches self-check: $(tail -5 <<<"$out")"
out="$(python3 - "$T/fb" <<'PY' 2>&1
import importlib.util, json, os, pathlib, subprocess, sys
d = pathlib.Path(sys.argv[1]); d.mkdir()
s = importlib.util.spec_from_file_location("fb", "tools/fork-branches.py"); fb = importlib.util.module_from_spec(s); s.loader.exec_module(fb)
r, g, _ = fb._migrated_fixture(d)          # shared, each target done, migrated (built once: FB_FIXTURE_CACHE)
bad = []
def fp(*a):
    x = subprocess.run([sys.executable, "tools/fork-port.py", "--repo", str(r), *a], capture_output=True, text=True, encoding="utf-8")
    return x.returncode, x.stdout + x.stderr
# the library's release-side check is the same done list, run on the tag's tree with the library's history (gap 1)
g("checkout", "-q", "neoforge-1.21.1")
g("tag", "lib-1.0-mc1.21.5", "neoforge-1.21.5")
os.environ["MMVU_CACHE_DIR"] = str(d / "cache")      # not HOME: Windows' home is USERPROFILE
s2 = importlib.util.spec_from_file_location("ld", "tools/layer-deps.py"); ld = importlib.util.module_from_spec(s2); s2.loader.exec_module(ld)
os.environ["LAYER_DEPS_GIT_URL"] = str(d / "nowhere")    # a first, wrong URL: cached nothing
ld.library_clone("owner/lib")[0] is None or bad.append("a clone from a missing URL")
subprocess.run(["git", "clone", "-q", "--bare", str(r), str(d / "old")], check=True)
subprocess.run(["git", "-C", str(d / "old"), "tag", "-d", "lib-1.0-mc1.21.5"], check=True, capture_output=True)
os.environ["LAYER_DEPS_GIT_URL"] = str(d / "old"); ld.library_clone("owner/lib")
os.environ["LAYER_DEPS_GIT_URL"] = str(r)                # the URL changed: the cached clone follows it
done, why, _n = ld.library_done_branch_release("owner/lib", "lib-1.0-mc1.21.5", "1.21.5")
done or bad.append(f"a released, done library version reads done at its tag (fetched from the URL as it now is): {why} "
                  + " / ".join(fb.explain_changed(r, "1.21.5", "lib-1.0-mc1.21.5", w.split()[-1])
                               for w in why.split("; ") if "no verdict: changed " in w))
(d / "cache/git/owner__lib/HEAD").is_file() or bad.append("the clone is under $MMVU_CACHE_DIR")
g("checkout", "-q", "neoforge-1.21.1")
rc, out = fp("--target", "1.21.4", "--check-done")
rc and bad.append(f"--check-done on a migrated version (checkout elsewhere): {rc} {out}")
rc, out = fp("--target", "1.21.4", "--from-stage", "cost", "--gates", "none")
(rc != 2 or "check it out first" not in out) and bad.append(f"a resume off the branch: {rc} {out}")
g("checkout", "-q", "neoforge-1.21.4")
rc, out = fp("--target", "1.21.4", "--write-test-items")
(rc or not (r / ".port/1.21.4/TEST-ITEMS.md").is_file()) and bad.append(f"--write-test-items on the branch: {rc} {out}")
g("checkout", "-q", "--", ".")
(r / "src/main/java/com/example/mod/Plain.java").write_text("package com.example.mod;\npublic class Plain { int v = 7; }\n", encoding="utf-8")
g("commit", "-qam", "edit on 1.21.4")
g("tag", "lib-1.1-mc1.21.4", "HEAD")
done, why, _n = ld.library_done_branch_release("owner/lib", "lib-1.1-mc1.21.4", "1.21.4")
(done or "not done" not in why) and bad.append(f"control: a tag after an unreviewed edit is not done: {why}")
rc, out = fp("--target", "1.21.4", "--check-done")
(rc != 18 or "GATES.md" not in out or "no verdict: changed src/main/java/com/example/mod/Plain.java" not in out) and \
    bad.append(f"after an edit: not done, gates stale, the file a review row: {rc} {out}")
# on the branch's own checkout --check-done also writes the reviewer's `?` row (never a verdict)
"| changed | src/main/java/com/example/mod/Plain.java | ? | ? |" in (r / ".port/1.21.4/REVIEW.md").read_text(encoding="utf-8") \
    or bad.append(f"--check-done on the branch writes REVIEW.md's `?` row: {out}")
g("checkout", "-q", "--", ".")
g("checkout", "-q", "neoforge-1.21.3")
(r / "src/main/java/com/example/mod/Plain.java").write_text("package com.example.mod;\npublic class Plain {\n int v = 1;\n}\n", encoding="utf-8")
g("commit", "-qam", "fix at 1.21.3")
rc, out = fp("--chain", "1.21.3..1.21.5", "--gates", "none")
(rc != 1 or "cascade" not in out or "STOPPED at 1.21.4" not in out) and \
    bad.append(f"--chain on version branches is the cascade, and stops where 1.21.4's own edit conflicts: {rc} {out}")
class E(Exception): pass
import urllib.error
def get(u):
    if ".port/1.21.5/branch.properties" in u: return b"x"
    raise urllib.error.HTTPError(u, 404, "nf", {}, None)
(ld.branch_release("o/l", "t", "1.21.5", get) and not ld.branch_release("o/l", "t", "1.21.4", get)) or bad.append("branch_release")
print("\n".join(bad) or "OK")
PY
)"
[ "$out" = "OK" ] && ok "fork-port.py on version branches: --check-done done off the branch, a resume refused off it, --write-test-items on it, an edit makes it not done (gates stale, a review row), --chain is the cascade and stops at a conflict; layer-deps' release-side done check on a tag agrees with the checkout's (its clone under \$MMVU_CACHE_DIR, re-pointed when the URL changes)" \
  || bad "fork-port / layer-deps on version branches: $out"
echo "audit-event-listeners.py: abstract events (CATALOG X33), @SubscribeEvent and method-reference listeners, layer mode"
if command -v javac >/dev/null 2>&1 && command -v jar >/dev/null 2>&1; then
  E="$T/evt"; mkdir -p "$E/src/net/minecraft/world/item" "$E/src/net/neoforged/neoforge/common" "$E/src/net/neoforged/bus/api" \
    "$E/src/net/neoforged/neoforge/client/event" "$E/tree/a/b" "$E/classes"
  echo 'package net.minecraft.world.item; public class Item {}' > "$E/src/net/minecraft/world/item/Item.java"
  echo 'package net.neoforged.neoforge.common; public class NeoForge {}' > "$E/src/net/neoforged/neoforge/common/NeoForge.java"
  echo 'package net.neoforged.bus.api; public @interface SubscribeEvent {}' > "$E/src/net/neoforged/bus/api/SubscribeEvent.java"
  cat > "$E/src/net/neoforged/neoforge/client/event/Stage.java" <<'J'
package net.neoforged.neoforge.client.event;
public abstract class Stage { public static class After extends Stage {} }
J
  (cd "$E" && javac -d classes $(find src -name '*.java') && jar cf fake.jar -C classes .) >/dev/null 2>&1
  EW="$E"; command -v cygpath >/dev/null 2>&1 && EW="$(cygpath -m "$E")"   # python on Windows cannot open /tmp/... paths
  echo "$EW/fake.jar" > "$E/cp.txt"
  cat > "$E/tree/a/b/Hook.java" <<'J'
package a.b;
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.neoforge.client.event.Stage;
public class Hook {
    @SubscribeEvent
    public static void onStage(Stage event) { if (!(event instanceof Stage.After)) return; }
    static void init(Object bus) { bus.addListener(Hook::viaRef); }
    private static void viaRef(Stage event) { }
}
J
  out="$(python3 tools/audit-event-listeners.py "$EW" --cp-file="$EW/cp.txt" --tree="$EW/tree" 2>&1)"; rc=$?
  [ "$rc" = 1 ] && [ "$(grep -c 'is ABSTRACT' <<<"$out")" = 2 ] \
    && ok "audit-event-listeners: the abstract parameter is reported for the annotated listener AND the Cls::method one" \
    || bad "audit-event-listeners did not report both abstract listeners (rc=$rc): $out"
  sed -i.bak 's/Stage event) { if (!(event instanceof Stage.After)) return; }/Stage.After event) { }/; s/viaRef(Stage event)/viaRef(Stage.After event)/' "$E/tree/a/b/Hook.java" && rm -f "$E/tree/a/b/Hook.java.bak"   # -i.bak: BSD (macOS) sed and GNU sed both take it
  out="$(python3 tools/audit-event-listeners.py "$EW" --cp-file="$EW/cp.txt" --tree="$EW/tree" 2>&1)"; rc=$?
  [ "$rc" = 0 ] && grep -q 'checked 2 event listener' <<<"$out" \
    && ok "audit-event-listeners: naming the concrete subclass passes, with the listener count printed" \
    || bad "audit-event-listeners failed the fixed listeners (rc=$rc): $out"
  echo 'package a.b; public class Empty {}' > "$E/tree/a/b/Hook.java"
  python3 tools/audit-event-listeners.py "$EW" --cp-file="$EW/cp.txt" --tree="$EW/tree" >/dev/null 2>&1; rc=$?
  [ "$rc" = 2 ] && ok "audit-event-listeners: zero listeners checked exits 2, never a pass" \
    || bad "audit-event-listeners zero-listener exit was $rc, expected 2"
else
  echo "  SKIP  audit-event-listeners (no javac/jar)"
fi
out="$(python3 tools/gate-loop.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "gate-loop: offline authlib Caused-by noise never replaces the real cause; only the GameTest summary is a server verdict; _reap spares a sibling checkout sharing the prefix (/x/fork vs /x/fork-b); a client killed with exit 137 and no verdict is 'killed' (OOM said only when the cgroup or dmesg confirms it), a caught WARN trace is never the stop reason, and Gate C does not start below the free-memory floor (faked meminfo and cgroup); a second Gate C client on the machine waits for the first's lock (saying who holds it) and a killed holder's lock is free" || bad "gate-loop self-check: $out"
out="$(python3 tools/round-preflight.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "round-preflight: a missing fork clone says to attach it to the scheduled task; the GitHub way named (GITHUB_TOKEN wins over gh); a JDK per version (25 for 26.x); libEGL.so.1 for a 26.3 client and XDG_RUNTIME_DIR, skipped with --no-client or off Linux; a version-branch shallow clone's 26.x branches read from origin by name (a single-version fork's versions and verdict as before, an authors' 26.x branch on its origin not counted); origin/HEAD a warning and the remote an upstream freeze names (exit 19), each with its fix" \
  || bad "round-preflight self-check: $out"
out="$(python3 tools/shipped-gate.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "shipped-gate: expectation parsing, log matching, JDK pick (JAVA_HOME first, then JAVA_HOME_<n>, /usr/lib/jvm, /opt, the Gradle toolchain downloads; java.exe too) and crash-cause self-check; the expectations file (single-version with none: none, as before; version branch .port/<mc>/shipped-expect-cf.txt for _cf, the unsuffixed one for _mr; layer .multiversion/), a variant missing its expected line fails; boot() on a stand-in server, no Minecraft: clean passes, a crash after Done fails, an exit before stop fails" || bad "shipped-gate self-check: $out"
python3 tools/shipped-gate.py --jar "$T/nope.jar" >/dev/null 2>&1; [ $? = 2 ] && ok "shipped-gate: a jar that does not exist exits 2" || bad "shipped-gate missing-jar exit"
printf 'x' > "$T/notjar.jar"; python3 tools/shipped-gate.py --jar "$T/notjar.jar" --neo 21.1.228 >/dev/null 2>&1; [ $? = 2 ] && ok "shipped-gate: a file that is not a jar exits 2" || bad "shipped-gate non-jar exit"
python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1],'w').writestr('a','b')" "$T/ok.jar"
python3 tools/shipped-gate.py --jar "$T/ok.jar" --neo 21.1.228 --expect-log 'bad/path:x' >/dev/null 2>&1; [ $? = 2 ] && ok "shipped-gate: a malformed --expect-log exits 2 before anything is downloaded" || bad "shipped-gate bad expectation exit"
out="$(python3 tools/layer-ci.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "layer-ci: targets are VERSION-sorted (1.21.11 is newer than 1.21.8), java follows inherits, hook stripping" || bad "layer-ci self-check: $out"
mkdir -p "$T/lay/.multiversion/targets"/{1.21.8,1.21.10,1.21.11,1.21.3}; echo x=1 > "$T/lay/.multiversion/base.properties"
for t in 1.21.8 1.21.10 1.21.11 1.21.3; do echo "java_version=21" > "$T/lay/.multiversion/targets/$t/target.properties"; done
out="$(python3 tools/layer-ci.py plan --repo "$T/lay" --github-output "$T/gho" 2>&1)"
grep -q '^newest=1.21.11$' "$T/gho" && grep -q '"1.21.8"' <<<"$out" && ! grep -q '"t":"1.21.11"' "$T/gho" \
  && ok "layer-ci plan: newest target is 1.21.11 (not the lexically last 1.21.8) and it is not in the compile matrix" || bad "layer-ci plan: $out $(cat "$T/gho")"
out="$(python3 tools/layer-ci.py plan --repo "$T/nolayer" 2>&1)"
grep -q '"layer": "false"' <<<"$out" && ok "layer-ci plan: a fork without .multiversion/ plans no layer job" || bad "layer-ci plan (no layer): $out"
# CI policy: full_gates is opt-in (default none), a typo is an error, and a single-version fork never sees it
out="$(python3 tools/layer-ci.py plan --repo "$T/lay" --github-output "$T/gho0" 2>&1)"
grep -q '^has_full=false$' "$T/gho0" && ok "layer-ci plan: no ci.properties means no extra full-gate target (the default)" || bad "layer-ci plan default has_full: $out"
printf 'full_gates=1.21.8\n' > "$T/lay/.multiversion/ci.properties"
python3 tools/layer-ci.py plan --repo "$T/lay" --github-output "$T/gho1" >/dev/null 2>&1
grep -q '^has_full=true$' "$T/gho1" && grep -q '^full_matrix=.*"t":"1.21.8"' "$T/gho1" && ! grep -q '"t":"1.21.11"' <(grep '^full_matrix=' "$T/gho1") \
  && ok "layer-ci plan: full_gates=1.21.8 gets the released middle target a full-gate job" || bad "layer-ci plan full_gates: $(cat "$T/gho1")"
printf 'full_gates=1.21.9\n' > "$T/lay/.multiversion/ci.properties"
python3 tools/layer-ci.py plan --repo "$T/lay" >/dev/null 2>&1; [ $? != 0 ] && ok "layer-ci plan: a full_gates typo fails the plan instead of meaning 'no gates'" || bad "full_gates typo accepted"
rm "$T/lay/.multiversion/ci.properties"
mkdir -p "$T/single"; printf 'full_gates=1.21.4\n' > "$T/single/ci.properties"
python3 tools/layer-ci.py plan --repo "$T/single" --github-output "$T/gho2" >/dev/null 2>&1
grep -q '^layer=false$' "$T/gho2" && grep -q '^has_full=false$' "$T/gho2" && ok "layer-ci plan: a single-version fork (no .multiversion/) plans nothing, whatever files it has" || bad "single-version plan: $(cat "$T/gho2")"
# one run per checkout: a live holder refuses a second layer run (exit 14); a single-version run takes no lock at all
mkdir -p "$T/lk/build" "$T/lk/.multiversion/targets/1.21.4" "$T/lk/.multiversion/tools"; : > "$T/lk/.multiversion/tools/layer.py"
# the holder is a Python process writing its OWN pid: under Git Bash on Windows `$!` is an MSYS pid, not the native pid
# the lock code checks, so a shell-written pid reads as dead there
python3 -c 'import json,os,sys,time; open(sys.argv[1],"w",encoding="utf-8").write(json.dumps({"pid": os.getpid(), "tool": "fork-port.py", "target": "1.21.3", "started": "2026-01-01T00:00:00Z"})); time.sleep(30)' "$T/lk/build/.fork-port.lock" & HOLDJOB=$!
for _ in $(seq 50); do [ -s "$T/lk/build/.fork-port.lock" ] && break; sleep 0.1; done
HOLD="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1],encoding="utf-8"))["pid"])' "$T/lk/build/.fork-port.lock")"
out="$(python3 tools/ci-gates.py --repo "$T/lk" --modid m --mc 1.21.4 --work-dir "$T/lkw" 2>&1)"; code=$?
[ $code = 14 ] && grep -q "$HOLD" <<<"$out" && ok "ci-gates --mc: a second run on a busy checkout is refused (exit 14) naming the holder" || bad "ci-gates lock exit $code: $out"
out="$(python3 tools/fork-port.py --repo "$T/lk" --target 1.21.4 2>&1)"; code=$?
[ $code = 14 ] && ok "fork-port: a second run on a busy checkout is refused (exit 14)" || bad "fork-port lock exit $code: $out"
python3 tools/ci-gates.py --repo "$T/lk" --modid m --work-dir "$T/lkw2" >/dev/null 2>&1; code=$?
[ $code != 14 ] && grep -q "\"pid\": $HOLD" "$T/lk/build/.fork-port.lock" && ok "ci-gates without --mc (a single-version fork) ignores the lock entirely" || bad "single-version ci-gates touched the lock (exit $code)"
kill $HOLDJOB 2>/dev/null; wait $HOLDJOB 2>/dev/null
out="$(python3 tools/fork-port.py --repo "$T/lk" --target 1.21.4 2>&1)"; code=$?
[ $code = 10 ] || [ $code = 2 ] && [ ! -e "$T/lk/build/.fork-port.lock" ] && ok "a dead holder's lock is stale: taken over, and released when the run ends" || bad "stale lock exit $code: $out"

out="$(python3 tools/layer-sync.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "layer-sync: classification (untouched / touch-up / rework), overlay shadowing, table, stop block, overlay helpers (names-only, three-way merge, duplicate member, RESIDUE section)" || bad "layer-sync self-check: $out"
out="$(python3 tools/test-layer-sync.py 2>&1)"
grep -q 'test-layer-sync: OK' <<<"$out" && ok "keep-current on a layer fork: merge commit (no force), overlays re-merged three ways (clean rewrite, inherited, retired, conflict + duplicate member left with hunks in RESIDUE, hand resolution kept), overlay budget, per-target class + table, rework stops with RESIDUE (exit 10), single-version fork untouched, version branches win over a legacy layer branch in detect" || bad "test-layer-sync: $out"
out="$(python3 tools/test-port-publish.py 2>&1)"
grep -q 'test-port-publish: OK' <<<"$out" && ok "publish-port: single-version output and API calls byte-identical to the recorded golden; layer forks publish one file per green target, refuse non-green / stale / unpublished-dependency targets; --platform mr|cf, and a cf-only S1 in the ledger publishes to Modrinth and holds CurseForge back by name (a version branch reads the head branch's ledger; an unreadable ledger holds both)" || bad "test-port-publish: $out"
out="$(python3 tools/fix-ledger.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "fix-ledger: a ledger without a layer section renders as before; the layer table renders and validates" || bad "fix-ledger self-check: $out"
if command -v gradle >/dev/null 2>&1; then
  G="$T/gj"; mkdir -p "$G"
  printf "rootProject.name = 'scratch'\n" > "$G/settings.gradle"
  cat > "$G/build.gradle" <<'GR'
plugins { id 'java' }
version = '1.0'
tasks.register('embedded', Jar) { archiveFileName.set('helper.jar'); destinationDirectory = layout.buildDirectory.dir('embedded') }
tasks.register('mrJar', Jar) { archiveFileName.set("scratch-${project.version}_mr.jar") }
tasks.jar { archiveFileName.set("scratch-${project.version}_base.jar"); destinationDirectory = layout.buildDirectory.dir('intermediates') }
tasks.register('plain', Jar) { }
def mvTarget = project.findProperty('mc')?.toString()
if (mvTarget == null) return
layout.buildDirectory = file("build/mc${mvTarget}")
GR
  sed -n '/BEGIN-JAR-NAMES/,/END-JAR-NAMES/p' templates/fork-layer/layer.gradle >> "$G/build.gradle"
  (cd "$G" && gradle -q --offline mrJar jar plain embedded >/dev/null 2>&1; gradle -q --offline mrJar jar plain embedded -Pmc=1.21.4 >/dev/null 2>&1)
  base="$(cd "$G/build" && find . -name '*.jar' -not -path './mc*' | sort | tr '\n' ' ')"
  mc="$(cd "$G/build/mc1.21.4" && find . -name '*.jar' | sort | tr '\n' ' ')"
  [ "$base" = "./embedded/helper.jar ./intermediates/scratch-1.0_base.jar ./libs/scratch-1.0.jar ./libs/scratch-1.0_mr.jar " ] \
    && [ "$mc" = "./embedded/helper.jar ./intermediates/scratch-1.0_base.jar ./libs/scratch-1.0-mc1.21.4.jar ./libs/scratch-1.0_mr-mc1.21.4.jar " ] \
    && ok "layer.gradle: with -Pmc the jars in libs are named -mc<target>; without it, and for embedded/intermediate jars, names are unchanged" \
    || bad "layer.gradle jar names: base=[$base] mc=[$mc]"
  # an exclude. key with no ':' (unescaped, so java.util.Properties cut it there) is refused, never a whole-group exclude
  GE="$T/ge"; mkdir -p "$GE"; printf "rootProject.name = 'scratch'\n" > "$GE/settings.gradle"
  { printf "plugins { id 'java' }\ndef mvProps = new Properties()\nfile('t.properties').withInputStream { mvProps.load(it) }\n"
    sed -n '/BEGIN-EXCLUDES/,/END-EXCLUDES/p' templates/fork-layer/layer.gradle; } > "$GE/build.gradle"
  printf 'exclude.com.example\\:lib=no build\n' > "$GE/t.properties"
  (cd "$GE" && gradle -q --offline help >/dev/null 2>&1); good=$?
  printf 'exclude.com.example:lib=no build\n' > "$GE/t.properties"
  out="$(cd "$GE" && gradle -q --offline help 2>&1)"; badkey=$?
  [ $good = 0 ] && [ $badkey != 0 ] && grep -q 'escape the' <<<"$out" \
    && ok "layer.gradle: an escaped exclude.<group:module> applies; an unescaped one (no ':' left in the key) stops the build" \
    || bad "layer.gradle exclude keys: escaped exit $good, unescaped exit $badkey: $out"
  # issue #114: each layer target's client gets its own game directory; without -Pmc the authors' run/ is unchanged
  # (a stand-in for ModDevGradle's neoForge.runs: no plugin download, no Minecraft)
  GR="$T/gr"; mkdir -p "$GR"; printf "rootProject.name = 'scratch'\n" > "$GR/settings.gradle"
  { cat <<'GR'
class FakeRun { String name; def gameDirectory; FakeRun(String n) { name = n } }
def runs = project.container(FakeRun) { n -> new FakeRun(n) }
extensions.add('neoForge', [runs: runs])
neoForge.runs.create('client') { gameDirectory = file('run') }
neoForge.runs.create('gameTestServer') { gameDirectory = file('harness/run') }
tasks.register('printRunDirs') { doLast { neoForge.runs.each { println "RUNDIR ${it.name}=${projectDir.toPath().relativize(file(it.gameDirectory).toPath())}" } } }
def mvTarget = project.findProperty('mc')?.toString()
if (mvTarget == null) return
GR
    sed -n '/BEGIN-RUN-DIRS/,/END-RUN-DIRS/p' templates/fork-layer/layer.gradle; } > "$GR/build.gradle"
  rd() { (cd "$GR" && gradle -q --offline printRunDirs "$@" 2>&1) | grep '^RUNDIR' | tr '\\' '/' | sort | tr '\n' ' '; }   # Windows prints build\mc<t>\run
  r0="$(rd)"; r4="$(rd -Pmc=1.21.4)"; r26="$(rd -Pmc=26.2)"
  [ "$r0" = "RUNDIR client=run RUNDIR gameTestServer=harness/run " ] \
    && [ "$r4" = "RUNDIR client=build/mc1.21.4/run RUNDIR gameTestServer=harness/run " ] \
    && [ "$r26" = "RUNDIR client=build/mc26.2/run RUNDIR gameTestServer=harness/run " ] \
    && ok "layer.gradle: -Pmc=1.21.4 and -Pmc=26.2 run the client in their own build/mc<t>/run; without -Pmc the run directory is unchanged, and the gates' GameTest server keeps its own" \
    || bad "layer.gradle run dirs: base=[$r0] 1.21.4=[$r4] 26.2=[$r26]"
fi
# issue #119: per-target-only mixins on a layer fork. A three-target chain 1.21.3 < 1.21.4 < 1.21.5: 1.21.3 adds
# PlainMixin (mixin_add, inherited by every later target); 1.21.4 adds OnlyMixin with mixin_add_only and takes the
# authors' AuthorsMixin off with mixin_remove_only, both for 1.21.4 alone. layer.py (the prepared tree that compiles and
# that the mixin audit reads: layer-ci.py and fork-port.py audit build/mc<t>/mv/java) and layer.gradle (the config at
# processResources) must resolve the same set. A single-version fork has no .multiversion/ and never applies
# layer.gradle; the base build (no -Pmc) below keeps the authors' config byte-for-byte.
MXO="$T/mixin-only"; mkdir -p "$MXO/.multiversion/tools" "$MXO/src/main/java/com/example/mod/mixin" "$MXO/src/main/resources"
cp templates/fork-layer/tools/layer.py templates/fork-layer/tools/prepare-sources.py "$MXO/.multiversion/tools/"
for t in 1.21.3 1.21.4 1.21.5; do mkdir -p "$MXO/.multiversion/targets/$t"; done
printf 'mixin_add.examplemod.mixins.json=mixins:PlainMixin\n' > "$MXO/.multiversion/targets/1.21.3/target.properties"
printf 'inherits=1.21.3\nmixin_add_only.examplemod.mixins.json=client:OnlyMixin\nmixin_remove_only.examplemod.mixins.json=mixins:AuthorsMixin\n' \
  > "$MXO/.multiversion/targets/1.21.4/target.properties"
printf 'inherits=1.21.4\n' > "$MXO/.multiversion/targets/1.21.5/target.properties"
for t in 1.21.3:PlainMixin 1.21.4:OnlyMixin; do
  d="$MXO/.multiversion/targets/${t%%:*}/java/com/example/mod/mixin"; mkdir -p "$d"
  printf 'package com.example.mod.mixin;\npublic class %s {}\n' "${t#*:}" > "$d/${t#*:}.java"
done
printf 'package com.example.mod.mixin;\npublic class AuthorsMixin {}\n' > "$MXO/src/main/java/com/example/mod/mixin/AuthorsMixin.java"
printf 'package com.example.mod;\npublic class Mod {}\n' > "$MXO/src/main/java/com/example/mod/Mod.java"
printf '{"package":"com.example.mod.mixin","mixins":["AuthorsMixin"]}' > "$MXO/src/main/resources/examplemod.mixins.json"
trees=""
for t in 1.21.3 1.21.4 1.21.5; do
  (cd "$MXO" && python3 .multiversion/tools/layer.py prepare --target "$t" --src src/main/java --out "build/mc$t/mv/java" >/dev/null 2>&1)
  trees="$trees$t=$(cd "$MXO/build/mc$t/mv/java/com/example/mod/mixin" 2>/dev/null && ls | tr '\n' ',') "
done
[ "$trees" = "1.21.3=AuthorsMixin.java,PlainMixin.java, 1.21.4=OnlyMixin.java,PlainMixin.java, 1.21.5=AuthorsMixin.java,PlainMixin.java, " ] \
  && ok "layer.py: a mixin_add_only class is in its own target's prepared tree and not in the next one's (it inherits the overlay); a mixin_remove_only class is off its own target alone; a plain mixin_add still reaches every later target" \
  || bad "layer.py per-target-only mixins, prepared trees: $trees"
# inherited_overlay_rows (opt-in, .multiversion/base.properties): on the same three-target layer fixture, plus an
# overlay at 1.21.3 that names OldA/OldB/OldC and rows 1.21.3 OldA, 1.21.4 OldB, 1.21.5 OldC. Without the key (and
# with =false) every prepared tree is byte-for-byte what the fixture prepared above with no key and the inherited
# overlay is the 1.21.3 file verbatim; with it 1.21.4 renames it by its row, 1.21.5 by 1.21.4's and its own, never by
# 1.21.3's, and 1.21.3's own tree is unchanged. A single-version fork has no .multiversion/: no layer.py runs there.
MXI="$T/inherited-rows"; cp -r "$MXO" "$MXI"; rm -rf "$MXI/build"
printf 'package com.example.mod;\npublic class Uses { OldA a; OldB b; OldC c; }\n' > "$MXI/src/main/java/com/example/mod/Uses.java"
printf 'package com.example.mod;\r\npublic class Uses { OldA a; OldB b; OldC c; int real; }\r\n' > "$MXI/.multiversion/targets/1.21.3/java/com/example/mod/Uses.java"
i=0; for t in 1.21.3 1.21.4 1.21.5; do i=$((i+1)); n=$(printf 'ABC' | cut -c$i); printf 'Old%s\tNew%s\n' "$n" "$n" > "$MXI/.multiversion/targets/$t/renames.tsv"; done
printf 'minecraft_version=1.21.1\n' > "$MXI/.multiversion/base.properties"
iprep() { for t in 1.21.3 1.21.4 1.21.5; do (cd "$MXI" && python3 .multiversion/tools/layer.py prepare --target "$t" --src src/main/java --out "build/$1/mc$t" >/dev/null 2>&1) || echo "PREPARE-FAILED $1 $t"; done; }
ifail="$(iprep none)"
printf 'minecraft_version=1.21.1\ninherited_overlay_rows=false\n' > "$MXI/.multiversion/base.properties"; ifail="$ifail$(iprep false)"
printf 'minecraft_version=1.21.1\ninherited_overlay_rows=true\n' > "$MXI/.multiversion/base.properties"; ifail="$ifail$(iprep on)"
U=com/example/mod/Uses.java; OV="$MXI/.multiversion/targets/1.21.3/java/$U"
# the expected bytes from Python, not sed: Git for Windows' sed reads text mode and drops the overlay's CRs
want() { python3 -c 'import sys; b = open(sys.argv[1], "rb").read()
for r in sys.argv[2:]: b = b.replace(*(x.encode() for x in r.split(">")))
sys.stdout.buffer.write(b)' "$OV" "$@" | od -c; }
want4="$(want 'OldB>NewB')"; want5="$(want 'OldB>NewB' 'OldC>NewC')"
# the old fixture's trees (prepared above with the key absent) against the same targets here with the key absent or false
same_old=yes; for t in 1.21.3 1.21.4 1.21.5; do for m in none false; do
  diff -r "$MXO/build/mc$t/mv/java/com/example/mod/mixin" "$MXI/build/$m/mc$t/com/example/mod/mixin" >/dev/null || same_old=no
  diff -r "$MXI/build/none/mc$t" "$MXI/build/$m/mc$t" >/dev/null || same_old=no; done; done
iwhy=""      # each condition on its own, so a failure names the one that failed
[ -z "$ifail" ] || iwhy="$iwhy prepare-failed"; [ "$same_old" = yes ] || iwhy="$iwhy off-not-identical"
{ cmp -s "$OV" "$MXI/build/none/mc1.21.4/$U" && cmp -s "$OV" "$MXI/build/none/mc1.21.5/$U"; } || iwhy="$iwhy off-overlay-not-verbatim"
diff -r "$MXI/build/none/mc1.21.3" "$MXI/build/on/mc1.21.3" >/dev/null || iwhy="$iwhy root-tree-changed"
[ "$(od -c < "$MXI/build/on/mc1.21.4/$U")" = "$want4" ] || iwhy="$iwhy on-1.21.4-bytes"
[ "$(od -c < "$MXI/build/on/mc1.21.5/$U")" = "$want5" ] || iwhy="$iwhy on-1.21.5-bytes"
diff -r -x Uses.java "$MXI/build/none/mc1.21.5" "$MXI/build/on/mc1.21.5" >/dev/null || iwhy="$iwhy on-other-files-changed"
[ -z "$(ls "$MXI/build/none" "$MXI/build/false" "$MXI/build/on" | grep inherited_overlay_rows)" ] || iwhy="$iwhy stamp-beside-custom-out"
(cd "$MXI" && python3 .multiversion/tools/layer.py prepare --target 1.21.5 --src src/main/java --out build/mc1.21.5/mv/java >/dev/null 2>&1) \
  && [ -f "$MXI/build/mc1.21.5/mv/java.inherited_overlay_rows" ] || iwhy="$iwhy no-stamp-on-default-out"
[ -z "$iwhy" ] \
  && ok "layer.py inherited_overlay_rows: off (no key, or =false) every prepared tree is byte-identical and the inherited overlay verbatim; on, 1.21.3's overlay is renamed at 1.21.4 by 1.21.4's row and at 1.21.5 by 1.21.4's and 1.21.5's, never by 1.21.3's (CRLF kept), the root target's tree and every other file unchanged; only an on-mode prepare of the default build/mc<t>/mv/java leaves its stamp beside it (a custom --out none)" \
  || bad "layer.py inherited_overlay_rows:$iwhy; fail=[$ifail] same_old=$same_old 1.21.4 on: $(cat "$MXI/build/on/mc1.21.4/$U" 2>&1 | head -2 | tr '\r\n' '  ')"
if command -v gradle >/dev/null 2>&1; then
  printf "rootProject.name = 'scratch'\n" > "$MXO/settings.gradle"
  { cat <<'GR'
plugins { id 'java' }
def mvTarget = project.findProperty('mc')?.toString()
if (mvTarget == null) return
def mvChain = []
for (def t = mvTarget; t; ) {
    mvChain.add(0, t)
    def p = new Properties()
    file(".multiversion/targets/${t}/target.properties").withInputStream { p.load(it) }
    t = p.getProperty('inherits')
}
def mvRanges = [:]
layout.buildDirectory = file("build/mc${mvTarget}")
GR
    sed -n '/BEGIN-MIXINS/,/END-MIXINS/p' templates/fork-layer/layer.gradle; } > "$MXO/build.gradle"
  cfg() { local f="$1"; shift; (cd "$MXO" && gradle -q --offline processResources "$@" >/dev/null 2>&1) || { echo "BUILD-FAILED"; return; }
          python3 -c 'import json,sys; j=json.load(open(sys.argv[1])); print(";".join(k + ":" + ",".join(sorted(v)) for k, v in sorted(j.items()) if isinstance(v, list)))' "$f"; }
  c0="$(cfg "$MXO/build/resources/main/examplemod.mixins.json")"
  base_same=no; cmp -s "$MXO/build/resources/main/examplemod.mixins.json" "$MXO/src/main/resources/examplemod.mixins.json" && base_same=yes
  c3="$(cfg "$MXO/build/mc1.21.3/resources/main/examplemod.mixins.json" -Pmc=1.21.3)"
  c4="$(cfg "$MXO/build/mc1.21.4/resources/main/examplemod.mixins.json" -Pmc=1.21.4)"
  c5="$(cfg "$MXO/build/mc1.21.5/resources/main/examplemod.mixins.json" -Pmc=1.21.5)"
  agree="$(cd "$MXO" && for t in 1.21.3 1.21.4 1.21.5; do python3 .multiversion/tools/layer.py mixins --target "$t" --config src/main/resources/examplemod.mixins.json 2>&1; done | tr '\n' ' ')"
  # a mixin_remove_only naming a class the config does not list (a typo) stops the build, never silently removes nothing
  sed -i 's/mixins:AuthorsMixin/mixins:AuthorMixin/' "$MXO/.multiversion/targets/1.21.4/target.properties"
  typo="$(cd "$MXO" && gradle -q --offline processResources -Pmc=1.21.4 2>&1)"; typo_rc=$?
  [ "$base_same" = yes ] && [ "$c0" = "mixins:AuthorsMixin" ] && [ "$c3" = "mixins:AuthorsMixin,PlainMixin" ] \
    && [ "$c4" = "client:OnlyMixin;mixins:PlainMixin" ] && [ "$c5" = "mixins:AuthorsMixin,PlainMixin" ] \
    && [ "$agree" = "$c3 $c4 $c5 " ] && [ $typo_rc != 0 ] && grep -q 'does not list' <<<"$typo" \
    && ok "layer.gradle: mixin_add_only/mixin_remove_only change their own target's mixin config alone (1.21.4 has OnlyMixin, not AuthorsMixin; 1.21.5 has neither change), plain mixin_add inherits, the set equals layer.py mixins, a remove naming nothing stops the build; without -Pmc the authors' config is byte-for-byte unchanged" \
    || bad "layer.gradle per-target-only mixins: base=[$c0 same=$base_same] 1.21.3=[$c3] 1.21.4=[$c4] 1.21.5=[$c5] layer.py=[$agree] typo rc=$typo_rc"
fi
out="$(python3 tools/port-ci.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "port-ci: the layer jobs exist only with --layer-branch, are skipped on the port branch, and splice no input into a script; without --variant-jar/--dep-jar-variant the workflow is byte-identical to tools/golden (single-version and layer fork); --variant-jar adds a shipped-jars job the release needs (its script run on a fake fork: one build, each jar booted with its own .port/<mc>/shipped-expect[-<variant>].txt as --expect-file or none, a failing or unmatched variant named); --dep-jar-variant adds a sha256-pinned gates (full, <label>) leg and refuses a mismatched spec; args reads every option back; promote refreshes the install manifest on the port branch only (its script run on a fake fork, single-version and layer: pushed on top of the tag, retried when the branch moved, nothing pushed when unchanged, a bad tag refused); a version branch's push trigger also paths-ignores its round records (.port/*.md, .port/*/*.md -- not branch.properties, the rows, STUBS.json or src/), rendered through main() on a real version-branch checkout, while a single-version checkout renders the golden's line byte for byte" || bad "port-ci self-check: $out"
python3 - <<'PY' && ok "ci-gates: --gatec none means no Gate C (not a phase name)" || bad "ci-gates --gatec none"
import re,sys
src=open("tools/ci-gates.py").read()
assert 'if q and q != "none"' in src
PY
# the mixin audit reads `@Mixin(value = X.class, priority = N)` (it once skipped every such FILE in silence)
MX="$T/mixin-value"; mkdir -p "$MX/src/main/java/com/example/mod/mixin" "$MX/src/main/resources" "$MX/jar/net/x"
printf 'package net.x;\npublic class Dragon {\n    private void hurt(Level l, java.util.List<Object> e) {}\n}\n' > "$MX/jar/net/x/Dragon.java"
( cd "$MX/jar" && python3 -c 'import zipfile; z=zipfile.ZipFile("../src.jar","w"); z.write("net/x/Dragon.java"); z.close()' )
printf '{"package":"com.example.mod.mixin","mixins":["DragonMixin"]}' > "$MX/src/main/resources/examplemod.mixins.json"
printf 'package com.example.mod.mixin;\nimport net.x.Dragon;\n@Mixin(value = Dragon.class, priority = 1024)\npublic abstract class DragonMixin {\n    @ModifyConstant(method = "hurt(Ljava/util/List;)V", constant = @Constant(floatValue = 10.0F))\n    private float f(float v) { return v; }\n}\n' > "$MX/src/main/java/com/example/mod/mixin/DragonMixin.java"
out="$(python3 tools/audit-mixin-targets.py "$MX" "$MX/src.jar" 2>&1)"
grep -q '1 MISMATCH' <<<"$out" && cmp -s tools/audit-mixin-targets.py templates/multi-version/tools/audit-mixin-targets.py \
  && ok "mixin audit: a @Mixin(value = X.class, priority = N) file is checked (its stale pinned descriptor is a mismatch); both copies agree" \
  || bad "mixin audit skipped a value= @Mixin, or its two copies differ: $(tail -3 <<<"$out")"
# the mixin audit: an argsOnly @ModifyVariable binds to the FIRST declared overload of a name-only target, and a
# CallbackInfoReturnable<T> must still name the target's return type (26.1's FogRenderer: both loaded "all match")
MO="$T/mixin-overload"; mkdir -p "$MO/src/main/java/com/example/mod/mixin" "$MO/src/main/resources" "$MO/jar/net/x"
mo_jar() {
  printf 'package net.x;\npublic class Fog {\n    public Data setup(int a) { return null; }\n%s\n}\n' "$1" > "$MO/jar/net/x/Fog.java"
  ( cd "$MO/jar" && python3 -c 'import zipfile; z=zipfile.ZipFile("../src.jar","w"); z.write("net/x/Fog.java"); z.close()' )
}
printf '{"package":"com.example.mod.mixin","client":["FogMixin"]}' > "$MO/src/main/resources/examplemod.mixins.json"
printf 'package com.example.mod.mixin;\nimport net.x.Fog;\n@Mixin(Fog.class)\npublic class FogMixin {\n    @Inject(method = "setup", at = @At("RETURN"))\n    private void a(int a, CallbackInfoReturnable<Vec> cir) {}\n\n    @ModifyVariable(method = "upload", at = @At("HEAD"), argsOnly = true, ordinal = 0)\n    private static Vec b(Vec v) { return v; }\n}\n' > "$MO/src/main/java/com/example/mod/mixin/FogMixin.java"
mo_jar '    public void upload(Data d) {}
    private void upload(java.nio.ByteBuffer b, int o, Vec c, float s) {}'
out="$(python3 tools/audit-mixin-targets.py "$MO" "$MO/src.jar" 2>&1)"
sed -i 's/CallbackInfoReturnable<Vec>/CallbackInfoReturnable<Data>/' "$MO/src/main/java/com/example/mod/mixin/FogMixin.java"
mo_jar '    private void upload(java.nio.ByteBuffer b, int o, Vec c, float s) {}
    public void upload(Data d) {}'
ctl="$(python3 tools/audit-mixin-targets.py "$MO" "$MO/src.jar" 2>&1)"
grep -q '2 MISMATCH' <<<"$out" && grep -q 'callback type' <<<"$out" && grep -q 'FIRST declared upload' <<<"$out" \
  && grep -q 'all targets match' <<<"$ctl" \
  && ok "mixin audit: an argsOnly @ModifyVariable on a name-only overload is judged against the FIRST declared one, and a stale CallbackInfoReturnable<T> is a mismatch; the matching shapes pass" \
  || bad "mixin audit overload/callback-type: $(tail -4 <<<"$out") | control: $(tail -2 <<<"$ctl")"
# the mixin audit: a pinned descriptor naming a class that only MOVED package (same simple name) is a mismatch --
# 26.1's CameraRenderState move passed "all targets match" and Gate C died at launch on the injection
MP="$T/mixin-moved"; mkdir -p "$MP/src/main/java/com/example/mod/mixin" "$MP/src/main/resources" "$MP/jar/net/x/state/level"
printf 'package net.x;\npublic class Rend {\n    public void submit(net.x.state.level.Cam c, com.example.mod.Own o) {}\n}\n' > "$MP/jar/net/x/Rend.java"
printf 'package net.x.state.level;\npublic class Cam {}\n' > "$MP/jar/net/x/state/level/Cam.java"
( cd "$MP/jar" && python3 -c 'import zipfile; z=zipfile.ZipFile("../src.jar","w"); z.write("net/x/Rend.java"); z.write("net/x/state/level/Cam.java"); z.close()' )
printf '{"package":"com.example.mod.mixin","client":["RendMixin"]}' > "$MP/src/main/resources/examplemod.mixins.json"
printf 'package com.example.mod.mixin;\nimport net.x.Rend;\n@Mixin(Rend.class)\npublic class RendMixin {\n    @Inject(method = "submit(Lnet/x/state/Cam;Lcom/example/mod/Own;)V", at = @At("HEAD"))\n    private void a(Cam c, Own o, CallbackInfo ci) {}\n}\n' > "$MP/src/main/java/com/example/mod/mixin/RendMixin.java"
out="$(python3 tools/audit-mixin-targets.py "$MP" "$MP/src.jar" 2>&1)"
sed -i 's#Lnet/x/state/Cam;#Lnet/x/state/level/Cam;#' "$MP/src/main/java/com/example/mod/mixin/RendMixin.java"
ctl="$(python3 tools/audit-mixin-targets.py "$MP" "$MP/src.jar" 2>&1)"
grep -q '1 MISMATCH' <<<"$out" && grep -q 'does not have: net.x.state.Cam' <<<"$out" && grep -q 'all targets match' <<<"$ctl" \
  && ok "mixin audit: a pinned descriptor naming a class that moved package (same simple name) is a mismatch; the moved name, and a mod's own type, pass" \
  || bad "mixin audit moved-class descriptor: $(tail -4 <<<"$out") | control: $(tail -2 <<<"$ctl")"

# 1.21.9: isClientSide is a method; a mod that already calls the method keeps ONE pair of parentheses
python3 - <<'PY' && ok "shared rows: isClientSide becomes a call, an existing call is left alone" || bad "the isClientSide row doubles an existing call"
import re, pathlib, sys
row = [l for l in pathlib.Path("templates/fork-layer/shared/1.21.10/renames.tsv").read_text(encoding="utf-8").splitlines()
       if l.startswith("re:") and "isClientSide" in l][0]
pat, rep = row[3:].split("\t")
f = lambda t: re.sub(pat, rep, t)
sys.exit(0 if f("if (level.isClientSide) {") == "if (level.isClientSide()) {" and f("level.isClientSide()") == "level.isClientSide()" else 1)
PY
# a shared helper marked `// REQUIRES: <token>` is installed only in a fork whose build names the token (GeckoLib helpers)
python3 - <<'PY' && ok "sync-shared: a REQUIRES-gated helper is skipped in a fork without the library, installed in one with it" || bad "a REQUIRES-gated shared helper reached a fork without its library (or missed one with it)"
import importlib.util, pathlib, sys, tempfile
sys.argv = ["x"]
spec = importlib.util.spec_from_file_location("m", "tools/make-fork-layer.py"); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
d = pathlib.Path(tempfile.mkdtemp()); (d / ".multiversion").mkdir()
(d / "gradle.properties").write_text("mod_id=examplemod\n", encoding="utf-8")
(d / "src/main/resources/META-INF").mkdir(parents=True)
(d / "src/main/resources/META-INF/neoforge.mods.toml").write_text('[[mods]]\nmodId="examplemod"\n', encoding="utf-8")
(d / "build.gradle").write_text("dependencies {}\n", encoding="utf-8")
sh = pathlib.Path(tempfile.mkdtemp()); (sh / "9.9/java").mkdir(parents=True); m.SHARED = sh
(sh / "9.9/java/Needs.java").write_text("// OVERLAY-ONLY\n// REQUIRES: geckolib\npackage @MVPKG@;\nclass Needs {}\n", encoding="utf-8")
(sh / "9.9/java/Plain.java").write_text("// OVERLAY-ONLY\npackage @MVPKG@;\nclass Plain {}\n", encoding="utf-8")
names = lambda: sorted(k.rsplit("/", 1)[1] for k in m.shared_files(d, "9.9"))
without = names()
(d / "build.gradle").write_text('dependencies { implementation "com.geckolib:geckolib-neoforge-1.21.11:5.4.5" }\n', encoding="utf-8")
with_ = names()
sys.exit(0 if without == ["Plain.java"] and with_ == ["Needs.java", "Plain.java"] else 1)
PY
# a layer made before a layer.gradle change gets the current machinery from sync-shared (--check reports it first);
# a copy someone edited (not any version the migrator shipped) is reported, fails --check, and is never written over
python3 - <<'PY' && ok "sync-shared: a verbatim older layer.gradle is refreshed, a missing build.sh installed, an edited layer.py reported (--check fails) and left alone" || bad "sync-shared overwrote an edited machinery file (or left a verbatim older one stale)"
import importlib.util, io, contextlib, pathlib, sys, tempfile
sys.argv = ["x"]
spec = importlib.util.spec_from_file_location("m", "tools/make-fork-layer.py"); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
d = pathlib.Path(tempfile.mkdtemp()); (d / ".multiversion/tools").mkdir(parents=True); (d / ".multiversion/targets").mkdir()
(d / "gradle.properties").write_text("mod_id=examplemod\n", encoding="utf-8")
for f in m.MACHINERY:
    (d / ".multiversion" / f).write_bytes((m.TPL / f).read_bytes())
older = b"// an older layer.gradle the migrator shipped\n"
m._HISTORY["layer.gradle"] = {older}   # the template's git history, without git (CI clones shallow)
(d / ".multiversion/layer.gradle").write_bytes(older)
(d / ".multiversion/build.sh").unlink()
edited = (m.TPL / "tools/layer.py").read_text(encoding="utf-8") + "# a hand edit\n"
(d / ".multiversion/tools/layer.py").write_text(edited, encoding="utf-8")
out = io.StringIO()
with contextlib.redirect_stdout(out): rc_check = m.sync_shared(d, check=True)
untouched = (d / ".multiversion/layer.gradle").read_bytes() == older
with contextlib.redirect_stdout(out): m.sync_shared(d)
with contextlib.redirect_stdout(out): rc_after = m.sync_shared(d, check=True)
cur = lambda f: (d / ".multiversion" / f).read_bytes() == (m.TPL / f).read_bytes()
log = out.getvalue()
sys.exit(0 if rc_check == 1 and untouched and cur("layer.gradle") and cur("build.sh")
         and (d / ".multiversion/tools/layer.py").read_text(encoding="utf-8") == edited
         and rc_after == 1 and "DIVERGED .multiversion/tools/layer.py" in log else 1)
PY
# audit-mixin-targets roots_of: its cache never answers with another jar's roots (an id() reused, or the set grown)
python3 - <<'PY' && ok "audit-mixin-targets: roots_of is recomputed for a new object reusing an id() and for a grown one (both copies identical)" || bad "audit-mixin-targets roots_of answered from a stale cache entry"
import importlib.util, sys
spec = importlib.util.spec_from_file_location("a", "tools/audit-mixin-targets.py"); m = importlib.util.module_from_spec(spec)
sys.argv = ["x"]; spec.loader.exec_module(m)
a = {"net/minecraft/A.java"}
first = m.roots_of(a)
a.add("com/mojang/B.java")
grown = m.roots_of(a)
m._ROOTS[id(b := {"org/example/C.java"})] = (object(), 1, {"net/minecraft/"})   # an entry left by a dead object with b's id
sys.exit(0 if first == {"net/minecraft/"} and grown == {"net/minecraft/", "com/mojang/"} and m.roots_of(b) == {"org/example/"} else 1)
PY
# audit-mixin-targets find_sources_jar: the build's own NeoForge version's jar, never the first one sorted (a version
# branch keeps the previous version's staged jars beside its own); a lone jar (single-version) as before
python3 - <<'PY' && ok "audit-mixin-targets: the sources jar of the build's neo_version is picked over an older version's left beside it; a lone jar and no gradle.properties as before" || bad "audit-mixin-targets find_sources_jar picked another version's sources jar"
import importlib.util, os, pathlib, sys, tempfile, time
spec = importlib.util.spec_from_file_location("a", "tools/audit-mixin-targets.py"); m = importlib.util.module_from_spec(spec)
sys.argv = ["x"]; spec.loader.exec_module(m)
ok = True
same = lambda got, want: got is not None and pathlib.Path(got).resolve() == pathlib.Path(want).resolve()   # Windows: glob's
# path joins the workspace and the pattern with mixed separators (and may keep an 8.3 short name), so compare files
with tempfile.TemporaryDirectory() as d:
    art = pathlib.Path(d) / "build/moddev/artifacts"; art.mkdir(parents=True)
    new = art / "neoforge-21.11.5-sources.jar"; new.write_bytes(b"x")
    ok &= same(m.find_sources_jar(d), new)                                   # single-version: its one jar
    old = art / "neoforge-21.10.40-sources.jar"; old.write_bytes(b"x")
    t = time.time(); os.utime(new, (t - 60, t - 60)); os.utime(old, (t, t))   # the stale one even newer on disk
    (pathlib.Path(d) / "gradle.properties").write_text("minecraft_version=1.21.11\nneo_version=21.11.5\n", encoding="utf-8")
    ok &= same(m.find_sources_jar(d), new)
    (pathlib.Path(d) / "gradle.properties").write_text("neo_version=21.10.40\n", encoding="utf-8")
    ok &= same(m.find_sources_jar(d), old)
    (pathlib.Path(d) / "gradle.properties").write_text("neo_version=21.10.4\n", encoding="utf-8")   # a prefix is no match
    ok &= same(m.find_sources_jar(d), old)                                   # none matches: the newest, said on stderr
sys.exit(0 if ok else 1)
PY
# R25: a registration supplier names what it builds and every Block/Item Properties takes that name (prefix-only rows)
python3 - <<'PY' && ok "shared rows: R25 registry ids -- suppliers enter their name, Item/Block Properties are stamped, a constructor reference becomes an entering supplier" || bad "the R25 RegIds rows rewrite the wrong shapes"
import re, pathlib, sys
rows = [l for l in pathlib.Path("templates/fork-layer/shared/1.21.3/renames.tsv").read_text(encoding="utf-8").splitlines()
        if l.startswith("re:") and "RegIds" in l]
src = ('ITEMS.register("blade", () -> new BladeItem());\n'
       'BLOCKS.register("t", () -> new T(BlockBehaviour.Properties.of().strength(2f)));\n'
       'super(new Properties().stacksTo(1));\nBlock.Properties.ofFullCopy(Blocks.STONE)\nx.register("y", Foo::new);\n')
for r in rows:
    pat, rep = r[3:].split("\t")
    src = re.sub(pat, rep.replace("@MVPKG@", "p"), src)
want = ('ITEMS.register("blade", () -> p.RegIds.enter("blade") ? null : new BladeItem());\n'
        'BLOCKS.register("t", () -> p.RegIds.enter("t") ? null : new T(p.RegIds.stamp(BlockBehaviour.Properties.of()).strength(2f)));\n'
        'super(p.RegIds.stamp(new Properties()).stacksTo(1));\np.RegIds.stamp(Block.Properties.ofFullCopy(Blocks.STONE))\n'
        'x.register("y", () -> p.RegIds.enter("y") ? null : new Foo());\n')
sys.exit(0 if len(rows) >= 4 and src == want else 1)
PY
# a row that routes a CALL through a static helper must leave `super.`/`this.` alone: a helper cannot make a super call
# (CATALOG W5b), and `Mv.cullingBox(super)` does not even parse -- it once stopped a dependent fork's measure as a parse abort
python3 - <<'PY' && ok "shared rows: the culling-box helper row skips super./this. receivers (and still rewrites e.x())" || bad "the culling-box row rewrites super./this. (or no longer rewrites e.x())"
import re, pathlib, sys
row = [l for l in pathlib.Path("templates/fork-layer/shared/1.21.3/renames.tsv").read_text(encoding="utf-8").splitlines()
       if "getBoundingBoxForCulling" in l][0]
pat, rep = row[3:].split("\t")
f = lambda s: re.sub(pat, rep.replace("@MVPKG@", "p"), s)
sys.exit(0 if f("super.getBoundingBoxForCulling()") == "super.getBoundingBoxForCulling()"
         and f("this.getBoundingBoxForCulling()") == "this.getBoundingBoxForCulling()"
         and f("e.getBoundingBoxForCulling()") == "p.Mv.cullingBox(e)" else 1)
PY
# M66/M68/M71 (1.21.2 API, shared/1.21.3): a boolean Entity.hurt use is hurtServer's result through Hurt.server (never a
# health comparison), hurt overrides become hurtServer, InteractionResultHolder/cooldowns/Ingredient/GeckoLib 4.7/
# doHurtTarget/profiler/BlockEntityType.Builder shapes, and a vanilla core-shader shard is named before the helper row
# takes it. Layer forks only: a single-version fork has no .multiversion/ and never reads these tables.
python3 - <<'PY' && ok "shared rows (1.21.3): boolean hurt -> Hurt.server, hurt overrides, InteractionResult, cooldowns, GeckoLib 4.7, doHurtTarget, profiler, BE builder, core-shader shards (and their controls)" || bad "a 1.21.3 shared row for the 1.21.2 entity/item/client API rewrites the wrong shape"
import re, pathlib, sys
rows = [l for l in pathlib.Path("templates/fork-layer/shared/1.21.3/renames.tsv").read_text(encoding="utf-8").splitlines()
        if l.startswith("re:")]
def f(s):
    for r in rows:
        pat, rep = r[3:].split("\t")
        s = re.sub(pat, rep.replace("@MVPKG@", "p"), s)
    return s
P = "net.minecraft.server.level.ServerLevel"
cases = [
    ("if (target.hurt(src, 2.0F)) {", "if (p.Hurt.server(target, src, 2.0F)) {"),
    ("boolean b = this.target.hurt(s, a);", "boolean b = p.Hurt.server(this.target, s, a);"),
    ("ok = ok && e.getTarget().hurt(s, a);", "ok = ok && p.Hurt.server(e.getTarget(), s, a);"),
    ("target.hurt(src, 1.0F);", "target.hurt(src, 1.0F);"),                                   # a statement still compiles
    ("if (DamageApi.hurt(t, s, a)) {", "if (DamageApi.hurt(t, s, a)) {"),                         # a static helper, not an entity
    ("public boolean hurt(@NotNull DamageSource source, float amount) {\n    return super.hurt(source, amount);",
     f"public boolean hurtServer({P} mv$hurtLevel, @NotNull DamageSource source, float amount) {{\n    return super.hurtServer(mv$hurtLevel, source, amount);"),
    ("public InteractionResultHolder<ItemStack> use(Level l, Player p, InteractionHand h) {", "public InteractionResult use(Level l, Player p, InteractionHand h) {"),
    ("return InteractionResultHolder.sidedSuccess(stack, level.isClientSide());", "return InteractionResult.SUCCESS.heldItemTransformedTo(stack);"),
    ("return InteractionResultHolder.pass(player.getItemInHand(hand));", "return InteractionResult.PASS;"),
    ("return InteractionResult.sidedSuccess(level.isClientSide);", "return InteractionResult.SUCCESS;"),
    ("player.getCooldowns().isOnCooldown(target.getUseItem().getItem())", "player.getCooldowns().isOnCooldown(target.getUseItem())"),
    ("player.getCooldowns().addCooldown(ModItems.WAND.get(), 20);", "player.getCooldowns().addCooldown(new net.minecraft.world.item.ItemStack(ModItems.WAND.get()), 20);"),
    ("return Ingredient.of(new ItemStack(Items.DIAMOND));", "return Ingredient.of(Items.DIAMOND);"),
    ("public ResourceLocation getModelResource(MobEntity animatable) {", "public ResourceLocation getModelResource(MobEntity animatable, software.bernie.geckolib.renderer.GeoRenderer<MobEntity> renderer) {"),
    ("public ResourceLocation getTextureResource(GeoRenderState renderState) {", "public ResourceLocation getTextureResource(GeoRenderState renderState) {"),
    ("protected float getDeathMaxRotation(MobEntity e) {", "protected float getDeathMaxRotation(MobEntity e, float partialTick) {"),
    ("        mob.doHurtTarget(target);", f"        {{ if (mob.level() instanceof {P} mv$sl) mob.doHurtTarget(mv$sl, target); }}"),
    ("        boolean b = mob.doHurtTarget(t);", "        boolean b = mob.doHurtTarget(t);"),
    ("this.minecraft.getProfiler().push(\"x\");", "net.minecraft.util.profiling.Profiler.get().push(\"x\");"),
    ("() -> BlockEntityType.Builder.of(\n    TableEntity::new,\n    ModBlocks.TABLE.get()).build(null)", "() -> new BlockEntityType<>(TableEntity::new, java.util.Set.of(ModBlocks.TABLE.get()))"),
    (".setShaderState(new RenderStateShard.ShaderStateShard(GameRenderer::getPositionColorShader))",
     ".setShaderState(new net.minecraft.client.renderer.RenderStateShard.ShaderStateShard(net.minecraft.client.renderer.CoreShaders.POSITION_COLOR))"),
    (".setShaderState(new RenderStateShard.ShaderStateShard(ModShaders::glow))", ".setShaderState(new p.ShaderShard(ModShaders::glow))"),
    ("for (var e : super.getDefaultAttributeModifiers().modifiers())", "for (var e : p.AttributeOverrides.base(this).modifiers())"),
    ("public ItemAttributeModifiers getDefaultAttributeModifiers(ItemStack stack) {", "public ItemAttributeModifiers getDefaultAttributeModifiers(ItemStack stack) { // MV-REQUIRES: AttributeOverrides"),
]
bad = [(s, f(s), w) for s, w in cases if f(s) != w]
for b in bad:
    print("  in:  %r\n  got: %r\n  want:%r" % b, file=sys.stderr)
helpers = pathlib.Path("templates/fork-layer/shared/1.21.3/java")
sys.exit(0 if not bad and all("package @MVPKG@;" in (helpers / n).read_text(encoding="utf-8") for n in ("Hurt.java", "AttributeOverrides.java")) else 1)
PY
# a row that routes a call through a static helper takes the WHOLE receiver chain, never its tail:
# `Minecraft.getInstance().font.drawInBatch(` once became `Minecraft.getInstance().p.Fonts.drawInBatch(font, `
python3 - <<'PY' && ok "shared rows: receiver-chain rows (26.2 drawInBatch/setFilter/bufferSource, 1.21.3 culling box, 1.21.5 frameBufferId) take the whole chain; setFilter leaves AbstractTexture.setFilter(blur, mip) alone" || bad "a receiver row rewrites mid-chain, or setFilter catches a non-EditBox call"
import re, pathlib, sys
def f(ver, key, s):
    for l in pathlib.Path(f"templates/fork-layer/shared/{ver}/renames.tsv").read_text(encoding="utf-8").splitlines():
        if l.startswith("re:") and key in l.split("\t")[0]:
            pat, rep = l[3:].split("\t"); s = re.sub(pat, rep.strip().replace("@MVPKG@", "p"), s)
    return s
cases = [
    ("26.2", "drawInBatch", "Minecraft.getInstance().font.drawInBatch(text, x, y);", "p.Fonts.drawInBatch(Minecraft.getInstance().font, text, x, y);"),
    ("26.2", "drawInBatch", "this.font.drawInBatch(t, 0, 0)", "p.Fonts.drawInBatch(this.font, t, 0, 0)"),
    ("26.2", "drawInBatch", "super.drawInBatch(t, 0, 0)", "super.drawInBatch(t, 0, 0)"),
    ("26.2", "setFilter", "this.screen.box.setFilter(s -> s.length() < 5);", "p.EditBoxes.setFilter(this.screen.box, s -> s.length() < 5);"),
    ("26.2", "setFilter", "this.input.setFilter(Util::isNumeric);", "p.EditBoxes.setFilter(this.input, Util::isNumeric);"),
    ("26.2", "setFilter", "nameField.setFilter(FILTER);", "p.EditBoxes.setFilter(nameField, FILTER);"),
    ("26.2", "setFilter", "texture.setFilter(true, false);", "texture.setFilter(true, false);"),
    ("26.2", "setFilter", "handler.setFilter(LOG_FILTER);", "handler.setFilter(LOG_FILTER);"),
    ("26.2", "bufferSource", "this.minecraft.gameRenderer.renderBuffers().bufferSource().getBuffer(t)", "p.MultiBufferSource.main().getBuffer(t)"),
    ("26.2", "bufferSource", "event.getMinecraft().renderBuffers().bufferSource()", "p.MultiBufferSource.main()"),
    ("1.21.3", "getBoundingBoxForCulling", "this.entity.getBoundingBoxForCulling()", "p.Mv.cullingBox(this.entity)"),
    ("1.21.3", "getBoundingBoxForCulling", "a.get().getBoundingBoxForCulling().inflate(1)", "p.Mv.cullingBox(a.get()).inflate(1)"),
    ("1.21.5", "frameBufferId", "Minecraft.getInstance().getMainRenderTarget().frameBufferId", "p.LegacyTargets.fbo(Minecraft.getInstance().getMainRenderTarget())"),
    ("1.21.5", "frameBufferId", "this.target.frameBufferId", "p.LegacyTargets.fbo(this.target)"),
]
bad = [(c, f(*c[:3])) for c in cases if f(*c[:3]) != c[3]]
print(*bad, sep="\n")
sys.exit(1 if bad else 0)
PY
# the 2026-10-19 harvest rows (1.21.10 hasControlDown/hasAltDown, 1.21.11 BLOCK_SHEET_MIPPED): a simple-name call is
# rewritten, a fully qualified one (a package dot before the class) is left alone
python3 - <<'PY' && ok "shared rows: Screen.hasControlDown/hasAltDown (1.21.10) and BLOCK_SHEET_MIPPED (1.21.11) rewrite the simple name and never a fully qualified one" || bad "a 2026-10-19 harvest row misses its simple name or breaks a fully qualified one"
import re, pathlib, sys
def rows(ver, word):
    out = []
    for l in pathlib.Path(f"templates/fork-layer/shared/{ver}/renames.tsv").read_text(encoding="utf-8").splitlines():
        if l.startswith("re:") and word in l.split("\t")[0]:
            pat, rep = l[3:].split("\t")
            out.append((pat, rep))
    return out
def f(rs, s):
    for pat, rep in rs:
        s = re.sub(pat, rep, s)
    return s
ctl = rows("1.21.10", "hasControlDown") + rows("1.21.10", "hasAltDown")
bsm = rows("1.21.11", "BLOCK_SHEET_MIPPED")
ok = (len(ctl) == 2 and len(bsm) == 1
      and f(ctl, "if (Screen.hasControlDown() && Screen.hasAltDown())") ==
          "if (net.minecraft.client.Minecraft.getInstance().hasControlDown() && net.minecraft.client.Minecraft.getInstance().hasAltDown())"
      and f(ctl, "net.minecraft.client.gui.screens.Screen.hasControlDown()") == "net.minecraft.client.gui.screens.Screen.hasControlDown()"
      and f(bsm, ".setTextureState(RenderStateShard.BLOCK_SHEET_MIPPED)") == ".setTextureState(@MVPKG@.LegacyRenderType.BLOCK_SHEET_MIPPED)"
      and f(bsm, "net.minecraft.client.renderer.RenderType.BLOCK_SHEET_MIPPED") == "net.minecraft.client.renderer.RenderType.BLOCK_SHEET_MIPPED")
sys.exit(0 if ok else 1)
PY
# 1.21.8 drops a leading renderBackground(...) from render() only in a Screen (a file with an `extends ...Screen`)
python3 - <<'PY' && ok "shared rows: the 1.21.6 renderBackground row applies in a Screen subclass only, not to another Renderable's render()" || bad "the renderBackground row misses a Screen or deletes a non-Screen's call"
import re, pathlib, sys
row = [l for l in pathlib.Path("templates/fork-layer/shared/1.21.8/renames.tsv").read_text(encoding="utf-8").splitlines()
       if l.startswith("re:") and "renderBackground" in l][0]
pat, rep = row[3:].split("\t")
body = (" {\n    @Override\n    public void render(GuiGraphics g, int mx, int my, float pt) {\n"
        "        renderBackground(g, mx, my, pt);\n        super.render(g, mx, my, pt);\n    }\n}\n")
f = lambda s: re.sub(pat, rep, s)
scr, ctr, wid = "class S extends Screen" + body, "class C extends AbstractContainerScreen<M>" + body, "class W extends AbstractWidget" + body
gone = lambda s: "renderBackground" not in f(s) and "super.render(g, mx, my, pt);" in f(s)
sys.exit(0 if gone(scr) and gone(ctr) and f(wid) == wid else 1)
PY
# 1.21.6+ text draws skip a colour with alpha 0 (CATALOG M75): the shared 1.21.8 row makes a 6-digit literal colour opaque
python3 - <<'PY' && ok "shared rows: the 1.21.6 text-colour row makes 0xRRGGBB opaque in drawString/drawCenteredString/drawWordWrap only, as a whole argument" || bad "the text-colour row is wrong"
import importlib.util, pathlib, sys
s = importlib.util.spec_from_file_location("ps", "templates/fork-layer/tools/prepare-sources.py"); ps = importlib.util.module_from_spec(s); s.loader.exec_module(ps)
rows = [r for r in ps.load_renames("templates/fork-layer/shared/1.21.8/renames.tsv") if "drawCenteredString" in r[0]]
_tok, rx = ps.split_rules(rows)
f = lambda t: rx[0][0].sub(rx[0][1], t)
cases = {"g.drawString(this.font, name, 4, 4, 0xFFFFFF);": "g.drawString(this.font, name, 4, 4, 0xFFFFFFFF);",
         "g.drawString(font, s, x, y, 0x40ff40, false);": "g.drawString(font, s, x, y, 0xFF40ff40, false);",
         "graphics.drawCenteredString(font, title, w / 2, 8, 0x404040);": "graphics.drawCenteredString(font, title, w / 2, 8, 0xFF404040);",
         "g.drawWordWrap(font, txt, 2, 2, 100, 0xAAAAAA);": "g.drawWordWrap(font, txt, 2, 2, 100, 0xFFAAAAAA);",
         "g.drawString(font, s, x, y, 0x80FFFFFF);": None, "g.drawString(font, s, x, y, color);": None,
         "g.fill(0, 0, 4, 4, 0x202020);": None, "int c = 0x123456;": None}
bad = [(k, f(k)) for k, v in cases.items() if f(k) != (v or k)]
print(bad) if bad else None
sys.exit(1 if len(rows) != 1 or bad else 0)
PY
# R25: the name RegIds.enter records lasts only while ITS supplier runs -- a registration that never entered a name used to
# take the previous registration's id silently; it now gets none (vanilla's loud "Item id not set")
if command -v javac >/dev/null 2>&1; then
  for v in 1.21.3 1.21.11; do
    W="$T/regids-$v"; mkdir -p "$W/p" "$W/net/minecraft/core/registries" "$W/net/minecraft/resources" "$W/net/minecraft/world/item" "$W/net/minecraft/world/level/block/state"
    sed -e 's/@MVPKG@/p/g' -e 's/@MODID@/examplemod/g' "templates/fork-layer/shared/$v/java/RegIds.java" > "$W/p/RegIds.java"
    printf 'package net.minecraft.core.registries;\npublic class BuiltInRegistries { public static final R BLOCK = new R(); public static class R { public boolean containsKey(Object o) { return false; } } }\n' > "$W/net/minecraft/core/registries/BuiltInRegistries.java"
    printf 'package net.minecraft.core.registries;\npublic class Registries { public static final Object ITEM = "item", BLOCK = "block"; }\n' > "$W/net/minecraft/core/registries/Registries.java"
    printf 'package net.minecraft.resources;\npublic class ResourceKey { public final String s; ResourceKey(String s) { this.s = s; } public static ResourceKey create(Object r, Object id) { return new ResourceKey(r + "/" + id); } }\n' > "$W/net/minecraft/resources/ResourceKey.java"
    for n in ResourceLocation Identifier; do printf 'package net.minecraft.resources;\npublic class %s { final String s; %s(String s) { this.s = s; } public static %s fromNamespaceAndPath(String a, String b) { return new %s(a + ":" + b); } public String toString() { return s; } }\n' $n $n $n $n > "$W/net/minecraft/resources/$n.java"; done
    printf 'package net.minecraft.world.item;\npublic class Item { public static class Properties { public String id; public Properties setId(net.minecraft.resources.ResourceKey k) { id = k.s; return this; } public Properties useBlockDescriptionPrefix() { return this; } } }\n' > "$W/net/minecraft/world/item/Item.java"
    printf 'package net.minecraft.world.level.block.state;\npublic class BlockBehaviour { public static class Properties { public String id; public static Properties of() { return new Properties(); } public Properties setId(net.minecraft.resources.ResourceKey k) { id = k.s; return this; } } }\n' > "$W/net/minecraft/world/level/block/state/BlockBehaviour.java"
    cat > "$W/p/Main.java" <<'J'
package p;
import java.util.function.Supplier;
import net.minecraft.world.item.Item;
import net.minecraft.world.level.block.state.BlockBehaviour;
public class Main {
    static Object reg(String n, Supplier<Object> s) { return s.get(); }
    static class Blade { Item.Properties p = RegIds.stamp(new Item.Properties()); }
    static class Pair { Item.Properties a = RegIds.stamp(new Item.Properties()); BlockBehaviour.Properties b = RegIds.stamp(BlockBehaviour.Properties.of()); }
    static Object helper(String name) { return reg(name, () -> new Blade()); }
    public static void main(String[] x) {
        Blade b = (Blade) reg("blade", () -> RegIds.enter("blade") ? null : new Blade());
        Pair p = (Pair) reg("pair", () -> RegIds.enter("pair") ? null : new Pair());
        Blade h = (Blade) helper("unentered");
        java.util.Properties u = RegIds.stamp(new java.util.Properties());
        System.out.println(b.p.id + " " + p.a.id + " " + p.b.id + " " + h.p.id + " " + (u != null));
    }
}
J
    out="$(javac -d "$W/out" $(find "$W" -name '*.java') 2>&1 | grep -v JAVA_TOOL_OPTIONS; java -cp "$W/out" p.Main 2>&1 | grep -v JAVA_TOOL_OPTIONS)"
    [ "$out" = "item/examplemod:blade item/examplemod:pair block/examplemod:pair null true" ] \
      && ok "RegIds ($v): the entered name holds for every Properties its supplier builds, an un-entered registration gets none (not the previous id), java.util.Properties passes through" \
      || bad "RegIds ($v) name scoping: $out"
  done
elif [ -n "${CI:-}" ]; then bad "no javac in CI -- RegIds was NOT tested"
else echo "  SKIP  no javac (CI runs it; a skip is not a pass)"; fi
# EditBoxes (26.2): a rejected edit puts the last accepted value back; that setValue re-enters the responder, and the
# chained responder must not hear the old value again
if command -v javac >/dev/null 2>&1; then
  W="$T/editboxes"; mkdir -p "$W/p" "$W/net/minecraft/client/gui/components" "$W/com/mojang/logging" "$W/org/slf4j"
  sed -e 's/@MVPKG@/p/g' -e 's/@MODID@/examplemod/g' templates/fork-layer/shared/26.2/java/EditBoxes.java > "$W/p/EditBoxes.java"
  printf 'package org.slf4j;\npublic interface Logger { void warn(String f, Object a); }\n' > "$W/org/slf4j/Logger.java"
  printf 'package com.mojang.logging;\npublic class LogUtils { public static org.slf4j.Logger getLogger() { return (f, a) -> System.out.println("WARN " + f); } }\n' > "$W/com/mojang/logging/LogUtils.java"
  printf 'package net.minecraft.client.gui.components;\npublic class EditBox { private String value = ""; private java.util.function.Consumer<String> responder;\n public void setResponder(java.util.function.Consumer<String> r) { responder = r; }\n public String getValue() { return value; }\n public void setValue(String v) { if (!v.equals(value)) { value = v; if (responder != null) responder.accept(v); } } }\n' > "$W/net/minecraft/client/gui/components/EditBox.java"
  cat > "$W/p/Main.java" <<'J'
package p;
import net.minecraft.client.gui.components.EditBox;
public class Main {
    public static void main(String[] x) {
        EditBox box = new EditBox();
        StringBuilder heard = new StringBuilder();
        box.setResponder(v -> heard.append('[').append(v).append(']'));
        EditBoxes.setFilter(box, s -> s.length() < 3);
        box.setValue("ab");
        box.setValue("abcd");
        box.setValue("a");
        System.out.println(box.getValue() + " " + heard);
    }
}
J
  out="$(javac -d "$W/out" $(find "$W" -name '*.java') 2>&1 | grep -v JAVA_TOOL_OPTIONS; java -cp "$W/out" p.Main 2>&1 | grep -v JAVA_TOOL_OPTIONS)"
  [ "$out" = "a [ab][a]" ] \
    && ok "EditBoxes (26.2): a rejected edit restores the last accepted value without calling the earlier responder again" \
    || bad "EditBoxes restore re-invoked the chained responder: $out"
elif [ -n "${CI:-}" ]; then bad "no javac in CI -- EditBoxes was NOT tested"
else echo "  SKIP  no javac (CI runs it; a skip is not a pass)"; fi
# add_shared_helpers copies a helper only when its FQN or an `MV-REQUIRES: Name` line is in the tree; a helper naming
# another by its simple name (same package) needs the tag, or a mod using one helper gets a set that does not compile
python3 - <<'PY' && ok "shared helpers: for every target and every helper used alone, the copied set is closed under simple-name references (MV-REQUIRES tags complete)" || bad "a shared helper names another by simple name without an MV-REQUIRES tag"
import importlib.util, io, contextlib, pathlib, re, sys, tempfile
spec = importlib.util.spec_from_file_location("layer", "templates/fork-layer/tools/layer.py")
L = importlib.util.module_from_spec(spec); spec.loader.exec_module(L)
key = lambda v: tuple(int(x) for x in v.split("."))
src = pathlib.Path("templates/fork-layer/shared")
allv = sorted([d.name for d in src.iterdir() if (d / "java").is_dir()], key=key)
PKG = "com.example.mod.mv"
def strip(t):
    t = re.sub(r"//[^\n]*|/\*.*?\*/", "", t, flags=re.S); t = re.sub(r'"(?:\\.|[^"\\])*"', '""', t)
    return re.sub(r"^import [^\n]*\n", "", t, flags=re.M)
problems = []
for target in allv:
    vers = [v for v in reversed(allv) if key(v) <= key(target)]
    mv = pathlib.Path(tempfile.mkdtemp()) / ".multiversion"
    for v in vers:
        d = mv / "shared" / v / "java" / PKG.replace(".", "/"); d.mkdir(parents=True)
        for f in (src / v / "java").glob("*.java"):
            (d / f.name).write_text(f.read_text(encoding="utf-8").replace("@MVPKG@", PKG).replace("@MODID@", "examplemod"), encoding="utf-8")
    L.MV = mv
    pool = sorted({f.stem for v in vers for f in (src / v / "java").glob("*.java")})
    for seed in pool:
        out = mv.parent / f"out-{seed}"; (out / "com/example/mod").mkdir(parents=True)
        (out / "com/example/mod/Use.java").write_text(f"package com.example.mod;\nclass Use {{ {PKG}.{seed} x; }}\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()): L.add_shared_helpers(vers, out)
        got = {f.stem: f.read_text(encoding="utf-8") for f in (out / PKG.replace(".", "/")).glob("*.java")}
        for n, t in got.items():
            for o in set(pool) - set(got):
                if not re.search(r"^import [\w.]*\." + o + ";", t, re.M) and re.search(r"(?<![\w.])" + o + r"\b", strip(t)):
                    problems.append(f"{target}: {seed} -> {n} names {o}")
print(*sorted(set(problems)), sep="\n")
sys.exit(1 if problems else 0)
PY
python3 - <<'PY' && ok "shared tables: every '#!exhaustive' is closed by '#!strict' (a chain ending at any version loads)" || bad "a shared renames.tsv leaves '#!exhaustive' open"
import glob,sys
for f in sorted(glob.glob("templates/fork-layer/shared/*/renames.tsv")):
    marks=[l.strip().split()[0] for l in open(f) if l.startswith(("#!exhaustive","#!strict"))]
    if marks and marks[-1]!="#!strict": sys.exit(f"{f}: unclosed")
PY

python3 - <<'PY' && ok "shared MultiBufferSource (26.2, 26.3): a non-consolidating type's started builder is drawn before a fresh one is handed out; a sortOnUpload type's quads are sorted before the draw" || bad "shared MultiBufferSource lost the non-consolidating split or the sort-on-upload step"
import pathlib, re, sys
bad = []
for v in ("26.2", "26.3"):
    t = pathlib.Path(f"templates/fork-layer/shared/{v}/java/MultiBufferSource.java").read_text(encoding="utf-8")
    t = re.sub(r"//[^\n]*|/\*.*?\*/", "", t, flags=re.S)
    gb = re.search(r"public VertexConsumer getBuffer\(RenderType type\) \{(.*?)\n        \}", t, re.S)
    # 26.3's endBatch(type) delegates to endBatch(type, into) (into: an open pass, or null)
    eb = re.search(r"public void endBatch\(RenderType type(?:, RenderPass into)?\) \{((?:(?!\n        \}).)*?sortOnUpload.*?)\n        \}", t, re.S)
    if not gb or not re.search(r"if \(open != null\) \{\s*if \(type\.canConsolidateConsecutiveGeometry\(\)\) \{\s*return open;\s*\}\s*endBatch\(type\);", gb.group(1)):
        bad.append(f"{v}: getBuffer does not end a non-consolidating type's started builder")
    if not eb or not re.search(r"if \(type\.sortOnUpload\(\)\) \{.*?mesh\.sortQuads\(\w+, RenderSystem\.getProjectionType\(\)\.vertexSorting\(\)\);.*?\}\s*draw\(type, mesh(?:, into)?\);", eb.group(1), re.S):
        bad.append(f"{v}: endBatch(type) does not sort a sortOnUpload type's quads before drawing")
    if "import com.mojang.blaze3d.systems.RenderSystem;" not in t:
        bad.append(f"{v}: RenderSystem not imported")
print(*bad, sep="\n")
sys.exit(1 if bad else 0)
PY

# 26.3 render passes (#120): one command encoder refuses a second pass (and buffer writes) while one is open, and NeoForge
# posts every RenderLevelStageEvent stage but AfterSky / AfterLevel inside the main pass. The rules live in a client-free
# helper (RenderPasses), run here against stubs of the two GPU API types it names; the helpers' use of them is checked
# in their sources.
if command -v javac >/dev/null 2>&1; then
  W="$T/renderpasses"; mkdir -p "$W/p" "$W/com/mojang/renderpearl/api/textures"
  sed -e 's/@MVPKG@/p/g' -e 's/@MODID@/examplemod/g' templates/fork-layer/shared/26.3/java/RenderPasses.java > "$W/p/RenderPasses.java"
  printf 'package com.mojang.renderpearl.api;\npublic enum GpuFormat { R8_UNORM, RG8_UNORM, RGB8_UNORM, RGBA8_UNORM, R16_FLOAT, R32_FLOAT, RGBA16_FLOAT, RGBA32_FLOAT, RGB10A2_UNORM, RG11B10_FLOAT, D32_FLOAT, D32_FLOAT_S8_UINT, D24_UNORM_S8_UINT, D16_UNORM }\n' > "$W/com/mojang/renderpearl/api/GpuFormat.java"
  # the 26.3 constants (javap -constants com.mojang.renderpearl.api.textures.GpuTexture)
  printf 'package com.mojang.renderpearl.api.textures;\npublic interface GpuTexture { int USAGE_COPY_DST = 1; int USAGE_COPY_SRC = 2; int USAGE_TEXTURE_BINDING = 4; int USAGE_RENDER_ATTACHMENT = 8; int USAGE_CUBEMAP_COMPATIBLE = 16; }\n' > "$W/com/mojang/renderpearl/api/textures/GpuTexture.java"
  cat > "$W/p/Main.java" <<'J'
package p;
import com.mojang.renderpearl.api.GpuFormat;
public class Main {
    public static void main(String[] x) {
        StringBuilder o = new StringBuilder();
        RenderPasses.requireNoOpenPass(false, "LegacyGl.draw");
        try { RenderPasses.requireNoOpenPass(true, "LegacyGl.draw"); o.append("no-throw"); }
        catch (IllegalStateException e) {
            String m = e.getMessage();
            o.append(m.contains("LegacyGl.draw") && m.contains("AfterSky") && m.contains("AfterLevel")
                    && m.contains("AfterTranslucentFeatures") && m.contains("getRenderPass()") ? "msg" : "bad-msg:" + m);
        }
        RenderPasses.Shape d = RenderPasses.glShape(854, 480, 0x8CAC);          // GL_DEPTH_COMPONENT32F
        o.append(' ').append(d.width()).append('x').append(d.height()).append(' ').append(d.format())
         .append(' ').append(d.attachable()).append(' ').append(d.bindable());
        RenderPasses.Shape u = RenderPasses.glShape(16, 16, 0x81A6);            // GL_DEPTH_COMPONENT24: no GpuFormat
        o.append(' ').append(u.format()).append(' ').append(u.attachable()).append(' ').append(u.bindable());
        RenderPasses.Shape z = RenderPasses.glShape(0, 0, 0x8058);
        o.append(' ').append(z.width()).append('x').append(z.height());
        RenderPasses.Shape s = RenderPasses.shape(64, 32, GpuFormat.RGBA8_UNORM);
        o.append(' ').append(s.attachable() && s.bindable()).append(' ').append(s.usage());
        o.append(' ').append(RenderPasses.firstElement(24, 4)).append(' ').append(RenderPasses.firstElement(96, 32));
        try { RenderPasses.firstElement(6, 4); o.append(" misaligned-ok"); } catch (IllegalStateException e) { o.append(" misaligned"); }
        java.util.Set<String> all = new java.util.TreeSet<>(RenderPasses.PASS_FREE_STAGES);
        all.addAll(RenderPasses.IN_PASS_STAGES);
        o.append(' ').append(all.size());
        System.out.println(o);
    }
}
J
  out="$(javac -d "$W/out" $(find "$W" -name '*.java') 2>&1 | grep -v JAVA_TOOL_OPTIONS; java -cp "$W/out" p.Main 2>&1 | grep -v JAVA_TOOL_OPTIONS)"
  # 9 = NeoForge 26.3.0.76's RenderLevelStageEvent subclasses (AfterSky / AfterLevel have no getRenderPass())
  [ "$out" = "msg 854x480 D32_FLOAT true true RGBA8_UNORM false true 1x1 true 15 6 3 misaligned 9" ] \
    && ok "RenderPasses (26.3): an open pass is refused with a message naming the pass-free stages and getRenderPass(); a raw GL texture gets its real size, format and sample+attach usage (an unknown format: sample only); transient offsets become element indices" \
    || bad "RenderPasses (26.3): $out"
elif [ -n "${CI:-}" ]; then bad "no javac in CI -- RenderPasses was NOT tested"
else echo "  SKIP  no javac (CI runs it; a skip is not a pass)"; fi
python3 - <<'PY' && ok "shared 26.3 render helpers (#120): LegacyGl draws into an open pass or given views and asserts before opening its own; in-pass geometry goes to mapped transient memory; RawGlTexture has its real shape; MultiBufferSource ends the in-pass flush into the event's pass (and a drawingInto scope's ends into its pass) and defers the rest to a pass-free flush; an in-pass draw that replaced Projection / Globals rebinds the defaults" || bad "shared 26.3 render helpers lost an in-pass / given-texture / real-shape path"
import pathlib, re, sys
d = pathlib.Path("templates/fork-layer/shared/26.3/java")
strip = lambda s: re.sub(r"//[^\n]*|/\*.*?\*/", "", s, flags=re.S)
gl = strip((d / "LegacyGl.java").read_text(encoding="utf-8"))
mbs = strip((d / "MultiBufferSource.java").read_text(encoding="utf-8"))
raw = strip((d / "RawGlTexture.java").read_text(encoding="utf-8"))
lrt = strip((d / "LegacyRenderType.java").read_text(encoding="utf-8"))
bad = []
def need(cond, msg):
    if not cond: bad.append(msg)
need(re.search(r"public static void draw\(MeshData mesh, RenderPass into\)", gl), "LegacyGl.draw(mesh, RenderPass into) missing")
need(re.search(r"public static void draw\(MeshData mesh, GpuTextureView colour, GpuTextureView depth\)", gl), "LegacyGl.draw(mesh, colour, depth) missing")
core = re.search(r"static void draw\(MeshData mesh, Object shaderObj, Key key, GpuTextureView colorView, GpuTextureView depthView,\s*RenderPass into, GpuFormat colorFormat\) \{(.*?)\n    \}\n", gl, re.S)
need(core, "LegacyGl's draw core (views or an open pass) missing")
if core:
    c = core.group(1)
    a = c.find("RenderPasses.requireNoOpenPass(inRenderPass()"); u = c.find("Uploaded.of(m)"); o = c.find("createRenderPass(")
    need(0 <= a < u and a < o and re.search(r"if \(into == null\) RenderPasses\.requireNoOpenPass", c), "the own-pass path does not assert (before uploading) that no pass is open")
    need("into == null ? Uploaded.of(m) : Uploaded.inPass(m)" in c, "a draw into an open pass does not upload through Uploaded.inPass")
    need(re.search(r"RenderPass pass = into != null \? into :", c) and re.search(r"if \(into == null\) \{?\s*pass\.close\(\);", c), "a pass handed in is replaced or closed")
    need("drawIndexed(up.indexCount(), 1, up.firstIndex(), 0, 0)" in c, "the draw ignores the transient upload's first index")
    # 26.3's transient buffer refuses slice() ("Cannot slice transient buffer"): the vertices bind as the allocation's own
    # slice, never re-sliced from its buffer (weekly round 2026-10-19: a shader-masked entity crashed the client)
    need("pass.setVertexBuffer(0, up.vertices());" in c and ".slice()" not in c,
         "an in-pass draw re-slices the transient vertex buffer (26.3: Cannot slice transient buffer)")
inpass = re.search(r"static Uploaded inPass\(MeshData m\) \{(.*?)\n        \}\n", gl, re.S)
tr = re.search(r"private static GpuBufferSlice transient_\(.*?\n        \}\n", gl, re.S)
need(inpass and tr and "transientMemory()" in tr.group(0) and "allocateGpuMapped(" in tr.group(0) and "writeToBuffer" not in inpass.group(1) + tr.group(0),
     "Uploaded.inPass does not write through a mapped transient allocation (an open pass refuses writeToBuffer)")
need(re.search(r"f\.isInRenderPass\(\)", gl), "LegacyGl.inRenderPass does not ask the frontend encoder")
need(re.search(r"super\(shape\.usage\(\), \"mv_raw_\" \+ id, shape\.format\(\), shape\.width\(\), shape\.height\(\), 1, 1, id,", raw), "RawGlTexture is not created with its real usage / format / size")
need(not re.search(r"super\(0,", raw), "RawGlTexture still has usage 0")
need(re.search(r"public static RawGlTexture of\(int glId, int width, int height, GpuFormat format\)", raw) and re.search(r"public static GpuTexture allocate\(", raw)
     and re.search(r"public static int glId\(GpuTexture texture\)", raw), "RawGlTexture lacks of(id, w, h, format) / allocate / glId")
need(re.search(r"_getTexLevelParameter\(0x0DE1, 0, 0x1000\)", raw) and re.search(r"_bindTexture\(previous\)", raw), "RawGlTexture.of(id) does not read the shape from GL and restore the binding")
need(re.search(r"public void draw\(MeshData mesh, com\.mojang\.renderpearl\.api\.commands\.RenderPass into\)", lrt), "LegacyRenderType.draw(mesh, pass) missing")
fl = re.search(r"final class Flush \{(.*?)\n    \}", mbs, re.S)
need(fl and re.search(r"afterTranslucentFeatures\(RenderLevelStageEvent\.AfterTranslucentFeatures event\) \{\s*main\(\)\.endBatch\(event\.getRenderPass\(\)\);", fl.group(1)),
     "Flush.afterTranslucentFeatures does not end the batch into the event's pass")
need(fl and re.search(r"afterLevel\(RenderLevelStageEvent\.AfterLevel event\) \{\s*main\(\)\.endBatch\(\);", fl.group(1)), "Flush.afterLevel no longer ends the batch outside a pass")
eb = re.search(r"public void endBatch\(RenderPass into\) \{(.*?)\n        \}", mbs, re.S)
need(eb and re.search(r"if \(into == null\) \{\s*Deferred\.drawIfNoPassOpen\(\);", eb.group(1)), "a pass-free endBatch does not draw what an in-pass end deferred")
dr = re.search(r"static void draw\(RenderType type, MeshData mesh, RenderPass into\) \{(.*?)\n        \}\n", mbs, re.S)
need(dr and re.search(r"into == null \? LegacyGl\.inRenderPass\(\) :.*?Deferred\.add\(type, mesh\);", dr.group(1), re.S), "a batch ended while a pass is open is not deferred")
need(dr and "Uploaded.inPassOwnVertices(m)" in dr.group(1) and "drawFromBuffer(" in dr.group(1)
     and re.search(r"try \(GpuBuffer vertices = up\.vertices\(\)\.buffer\(\)\)", dr.group(1)) and "Uploaded.inPass(m)" not in dr.group(1),
     "a vanilla type drawn into the open pass hands drawFromBuffer (which slices it) a transient vertex buffer, or never closes its own")
own = re.search(r"static Uploaded inPassOwnVertices\(MeshData m\) \{(.*?)\n        \}\n", gl, re.S)
need(own and "createBuffer(" in own.group(1) and "own.close()" in own.group(1),
     "Uploaded.inPassOwnVertices does not put the vertices in a buffer of their own (closed on failure)")
# a handler at an in-pass stage that mixes types: its type switches and ends go into the scope's pass, not to AfterLevel
sc = re.search(r"public Scope drawingInto\(RenderPass pass\) \{(.*?)\n        \}\n", mbs, re.S)
need(sc and re.search(r"scopePass = pass;", sc.group(1)) and re.search(r"endBatch\(pass\);\s*scopePass = outer;", sc.group(1)),
     "BufferSource.drawingInto does not set the scope's pass, end its batch into it on close and restore the outer scope")
ebt = re.search(r"public void endBatch\(RenderType type, RenderPass into\) \{(.*?)\n        \}", mbs, re.S)
need(eb and re.search(r"^\s*if \(into == null\) \{\s*into = scopePass;", eb.group(1)) and ebt and re.search(r"^\s*if \(into == null\) \{\s*into = scopePass;", ebt.group(1)),
     "an endBatch with no pass does not fall back to the drawingInto scope's pass first")
need(core and re.search(r"else if \(replacedDefaults\) \{\s*RenderSystem\.bindDefaultUniforms\(into\);", core.group(1))
     and core.group(1).find("bindDefaultUniforms(into)") < core.group(1).find("tb.close()"),
     "an in-pass draw that replaced Projection / Globals leaves the owner's pass bound to blocks it then closes")
print(*bad, sep="\n")
sys.exit(1 if bad else 0)
PY
python3 - <<'PY' && ok "shared 1.21.5 / 1.21.8 / 1.21.10 render helpers: a LegacyRenderType's setupRenderState loads its shader, blend / depth / write-mask / cull / bias state and output target into LegacyGl (so setupRenderState + drawWithShader draws as the type does) and clearRenderState restores it; draw() runs the shader shard once; 1.21.5's draw calls the shader's apply() before binding its uniforms and samplers, keeps no COLOR_MODULATOR written inside apply() (as on 1.21.1) and calls clear() after" || bad "shared 1.21.5-1.21.10 render helpers: a set-up LegacyRenderType no longer reaches drawWithShader, or 1.21.5's draw lost apply()"
import pathlib, re, sys
strip = lambda s: re.sub(r"//[^\n]*|/\*.*?\*/", "", s, flags=re.S)
bad = []
def need(c, msg):
    if not c: bad.append(msg)
def body(text, head):
    m = re.search(head + r"\s*\{", text)
    if not m: return None
    i, depth = m.end(), 1
    while depth and i < len(text):
        depth += {"{": 1, "}": -1}.get(text[i], 0); i += 1
    return text[m.end():i - 1]
for v in ("1.21.5", "1.21.8", "1.21.10"):
    d = pathlib.Path("templates/fork-layer/shared") / v / "java"
    gl = strip((d / "LegacyGl.java").read_text(encoding="utf-8"))
    lrt = strip((d / "LegacyRenderType.java").read_text(encoding="utf-8"))
    setup = body(lrt, r"public void setupRenderState\(\)")
    clear = body(lrt, r"public void clearRenderState\(\)")
    draw = body(lrt, r"public void draw\(MeshData mesh\)")
    need(setup and re.search(r"super\.setupRenderState\(\);.*LegacyGl\.load\(state\.key, state\.shader\.shader\(\), getRenderTarget\(\)\).*state\.shader\.setupRenderState\(\);", setup, re.S),
         f"{v}: LegacyRenderType.setupRenderState does not run its shards, load its shader/state into LegacyGl and run the shader shard")
    need(clear and re.search(r"state\.shader\.clearRenderState\(\);.*LegacyGl\.restore\(.*super\.clearRenderState\(\);", clear, re.S),
         f"{v}: LegacyRenderType.clearRenderState does not restore LegacyGl's state")
    need(draw and "setupRenderState();" in draw and "state.shader.setupRenderState" not in draw and "clearRenderState();" in draw,
         f"{v}: LegacyRenderType.draw runs the shader shard twice or not inside setup/clear")
    dws = body(gl, r"public static void drawWithShader\(MeshData mesh\)")
    need(dws and re.search(r"draw\(mesh, shader, current\(\), target != null \? target :", dws),
         f"{v}: LegacyGl.drawWithShader ignores a set-up type's output target")
    cur = body(gl, r"static Key current\(\)")
    need(cur and "biasScale, biasConstant" in cur, f"{v}: LegacyGl.current() drops a set-up type's depth bias")
    load = body(gl, r"static Saved load\(Key key, Object shaderObj, RenderTarget into\)")
    need(load and all(f in load for f in ("blend =", "depthTest =", "depthWrite =", "writeColor =", "writeAlpha =", "cull =",
                                          "biasScale =", "biasConstant =", "shader = shaderObj", "target = into")),
         f"{v}: LegacyGl.load does not set every piece of a type's state")
    rest = body(gl, r"static void restore\(Saved s\)")
    need(rest and "shader" not in rest and "target = s.target()" in rest and "blend = s.blend()" in rest,
         f"{v}: LegacyGl.restore does not put the state back (or resets the shader, which 1.21.4's shard left set)")
d = pathlib.Path("templates/fork-layer/shared/1.21.5/java")
gl = strip((d / "LegacyGl.java").read_text(encoding="utf-8"))
core = body(gl, r"static void draw\(MeshData mesh, Object shaderObj, Key key, RenderTarget target\)")
need(core and 0 <= core.find("own.apply();") < core.find("COLOR_MODULATOR.touched()", core.find("own.apply();")) < core.find("own.bind("),
     "1.21.5: LegacyGl.draw does not call the shader's apply() before the COLOR_MODULATOR check and bind()")
need(core and re.search(r"own\.apply\(\);\s*if \(cm != null\) \{\s*if \(before != null\) cm\.set\(before\);\s*else if \(!wrote\) cm\.consume\(\);", core),
     "1.21.5: a COLOR_MODULATOR written inside apply() reaches the draw (1.21.1 never uploaded it)")
need(core and re.search(r"finally \{\s*if \(own != null\) own\.clear\(\);", core), "1.21.5: LegacyGl.draw does not call clear() after the draw")
print(*bad, sep="\n")
sys.exit(1 if bad else 0)
PY

echo
echo "port-tools self-test: $pass passed, $fail failed"
[ "$fail" = 0 ] || exit 1
