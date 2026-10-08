#!/usr/bin/env bash
# Self-test for the port-side helpers: the @Override probe and the catalogue-sweep runner.
set -uo pipefail
cd "$(dirname "$0")/.."
. tools/python.sh || exit 1   # python3 on Windows too
ROOT="$PWD"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
pass=0; fail=0
ok()  { echo "  PASS  $1"; pass=$((pass+1)); }
bad() { echo "  FAIL  $1"; fail=$((fail+1)); }

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
  sed -i 's/getLightBlock(Object state)/getLightBlock(Object state, Object level, Object pos)/; s/    public void helper/    public void helperX/' "$S/Block.java"
  sed -i '/@Deprecated/d; /helperX/d' "$S/Block.java"
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
grep -q 'self-check: OK' <<<"$out" && ok "file loop: R1 and client-import scans, supertype-aware probe filter, batching" \
  || bad "file-loop self-check: $out"

out="$(python3 tools/gate-loop.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "gate loop: the deepest load-crash cause and the mod's own frames go to the worker" \
  || bad "gate-loop self-check: $out"

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
            "fix-missing-members.py|fix-missing-members: a moved member is renamed only where javac names its owner type" \
            "convert-rendertypes.py|convert-rendertypes: a CompositeState becomes a pipeline + RenderSetup; reverse-Z depth, unknown shards refused" \
            "convert-core-shaders.py|convert-core-shaders: GLSL 150 loose uniforms become 26.x blocks; vanilla-set names bind to vanilla" \
            "convert-simplechannel.py|convert-simplechannel: a SimpleChannel becomes payloads; directions inferred, handlers kept" \
            "normalise-imports.py|normalise-imports: inline names a port wrote become imports; the author's own and colliding names are kept" \
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

echo
echo "port-tools self-test: $pass passed, $fail failed"
[ "$fail" = 0 ] || exit 1
