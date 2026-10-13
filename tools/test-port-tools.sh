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
            "convert-gui-hooks.py|convert-gui-hooks: render hooks become extract*, input handlers take event records; bodies kept" \
            "srcsets.py|srcsets: every source set the author builds, their compile and jar tasks, and the target from the build" \
            "port-profile.py|port-profile: count the cost-driving port patterns per mod and name the tool covering each" \
            "convert-entity-renderstate.py|convert-entity-renderstate: vanilla entity renderers/models/layers onto 26.2 render state; refuses what it cannot rewrite exactly" \
            "convert-geckolib.py|convert-geckolib: GeckoLib 4.8 renderer/layer/model/animation hooks and asset layout onto 5.5; refuses what it cannot rewrite exactly" \
            "ci-gates.py|ci-gates: a finished port's gates in CI, worker-free pass/fail with a summary" \
            "port-ci.py|port-ci: install the fork's CI + tag-driven pre-release workflow as its own commit" \
            "port-offer.py|port-offer: the authors see the port without our CI commit; port commits after a CI commit are replayed without it; only what state.json verified is claimed" \
            "port-ci-kit.py|port-ci-kit: the scoped CI kit -- deterministic archive holding exactly the manifest, nothing from mods/ or docs/" \
            "install-port.py|install-port: a fork port and its sibling ports install from their manifests, sha256-checked, newest file on conflict" \
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

out="$(python3 tools/make-fork-layer.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "fork layer: init is idempotent (one hook line), add-target writes versions and mods.toml ranges, add-target --direct writes shared_chain (default off)" \
  || bad "make-fork-layer self-check: $out"
# the 26.x calendar line, through the real command line on a single-version fixture turned layer: 26.1.2 one hop on from
# 1.21.11, Java 25, ranges to the next patch release, GeckoLib's new group as an ESCAPED substitution (Properties keys end
# at ':'), and layer-ci's plan picks 26.3 as the newest with its own JDK
L26="$T/layer26"; mkdir -p "$L26/src/main/java/com/example/mod"
echo 'package com.example.mod; class A {}' > "$L26/src/main/java/com/example/mod/A.java"
printf 'minecraft_version=1.21.1\nneo_version=21.1.228\ngeckolib_version=4.8.4\n' > "$L26/gradle.properties"; echo 'plugins {}' > "$L26/build.gradle"
python3 tools/make-fork-layer.py init --repo "$L26" >/dev/null 2>&1
for t26 in 1.21.11 26.1.2 26.2 26.3; do python3 tools/make-fork-layer.py add-target --repo "$L26" --target "$t26" >/dev/null 2>&1; done
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
out="$(python3 tools/fork-port.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "fork-port: stage selection, moves table, residue report, STOP block, layer.py patch, every --from-stage resume re-measures, one run per checkout, direct-jump shared_chain agrees with layer.py" \
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
grep -q 'self-check: OK' <<<"$out" && ok "gate-loop: offline authlib Caused-by noise never replaces the real cause" || bad "gate-loop self-check: $out"
out="$(python3 tools/shipped-gate.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "shipped-gate: expectation parsing, log matching, JDK pick and crash-cause self-check" || bad "shipped-gate self-check: $out"
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
grep -q 'self-check: OK' <<<"$out" && ok "layer-sync: classification (untouched / touch-up / rework), overlay shadowing, table, stop block" || bad "layer-sync self-check: $out"
out="$(python3 tools/test-layer-sync.py 2>&1)"
grep -q 'test-layer-sync: OK' <<<"$out" && ok "keep-current on a layer fork: merge commit (no force), per-target class + table, rework stops with RESIDUE (exit 10), single-version fork untouched" || bad "test-layer-sync: $out"
out="$(python3 tools/test-port-publish.py 2>&1)"
grep -q 'test-port-publish: OK' <<<"$out" && ok "publish-port: single-version output and API calls byte-identical to the recorded golden; layer forks publish one file per green target, refuse non-green / stale / unpublished-dependency targets" || bad "test-port-publish: $out"
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
fi
out="$(python3 tools/port-ci.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "port-ci: the layer jobs exist only with --layer-branch, are skipped on the port branch, and splice no input into a script" || bad "port-ci self-check: $out"
python3 - <<'PY' && ok "ci-gates: --gatec none means no Gate C (not a phase name)" || bad "ci-gates --gatec none"
import re,sys
src=open("tools/ci-gates.py").read()
assert 'if q and q != "none"' in src
PY
python3 - <<'PY' && ok "shared tables: every '#!exhaustive' is closed by '#!strict' (a chain ending at any version loads)" || bad "a shared renames.tsv leaves '#!exhaustive' open"
import glob,sys
for f in sorted(glob.glob("templates/fork-layer/shared/*/renames.tsv")):
    marks=[l.strip().split()[0] for l in open(f) if l.startswith(("#!exhaustive","#!strict"))]
    if marks and marks[-1]!="#!strict": sys.exit(f"{f}: unclosed")
PY

echo
echo "port-tools self-test: $pass passed, $fail failed"
[ "$fail" = 0 ] || exit 1
