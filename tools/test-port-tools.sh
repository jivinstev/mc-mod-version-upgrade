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

# PORT_TOOLS_PART=1|2|3 runs one third (CI runs them as parallel jobs); unset runs everything, as before.
case "${PORT_TOOLS_PART:-all}" in all|1|2|3) ;; *) echo "PORT_TOOLS_PART must be 1, 2, 3 or unset" >&2; exit 2 ;; esac
_part() { [ "${PORT_TOOLS_PART:-all}" = all ] || [ "${PORT_TOOLS_PART:-all}" = "$1" ]; }
if _part 1; then   # part 1: sections 0-13, the tool self-checks, the hop tests
echo "0. portable to macOS (bash 3.2, BSD tools, Python 3.9) and Windows -- tools/check-compat.py"
# Linux CI alone passes code that breaks the other two (GNU sed -i, a heredoc inside $( ), Python 3.10+ syntax,
# a bare "bash" argv on Windows...): the static check runs on every tracked .py and .sh.
out="$(python3 tools/check-compat.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "check-compat: each rule fires on its pattern and not on the safe form" || bad "check-compat self-check: $out"
out="$(python3 tools/check-compat.py 2>&1)"
grep -q 'check-compat: PASS' <<<"$out" && ok "check-compat: every tracked .py parses as Python 3.9 and no script or tool uses a pattern that breaks macOS or Windows" \
  || bad "check-compat: $out"
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
cat > "$T/test-port-tools-heredoc-1.py" <<'PY'   # written out, then run: macOS bash 3.2 misparses quotes in a heredoc inside $( )
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("bs", "tools/boot-smoke.py")
bs = importlib.util.module_from_spec(spec); spec.loader.exec_module(bs)
print(len(bs.game_already_running(pathlib.Path(sys.argv[1]))))
PY
seen="$(python3 "$T/test-port-tools-heredoc-1.py" "$I")"
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
cat > "$T/test-port-tools-heredoc-2.py" <<'PY'   # written out, then run: macOS bash 3.2 misparses quotes in a heredoc inside $( )
import importlib.util, sys
spec = importlib.util.spec_from_file_location("bs", sys.argv[1] + "/repo/tools/boot-smoke.py")
bs = importlib.util.module_from_spec(spec); spec.loader.exec_module(bs)
bs.DEFAULT_INSTANCE = sys.argv[1] + "/no-default"
found = bs.find_instance(None)
print(found.as_posix() if found else None)
PY
found="$(env -u MINECRAFT_DIR python3 "$T/test-port-tools-heredoc-2.py" "$B")"
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
grep -q 'self-check: OK' <<<"$out" && ok "file loop: R1 and client-import scans, supertype-aware probe filter, batching" \
  || bad "file-loop self-check: $out"

out="$(python3 tools/port-cost.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "port-cost: a session driving two version branches and a second fork (one subagent per version) is charged once -- each version only its own requests, the parts sum to the session; a single-version checkout without --target is counted whole, as before; records carry a price tier and no model name; round-table sums each version branch's COST.md records since the round's start and measures a single-version fork, with totals; round-table --session-cwd charges a scheduled session's own requests (its subagents included, another cwd not) per version at the round's marks; mark --phase splits it per fork x version x phase (a recurring phase summed, each version's rows summing to its unphased row, per-phase totals) while a marks file without phases gives today's table byte for byte" \
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

out="$(python3 tools/gatec-carrier.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "gatec-carrier: one carrier per stable versions.json line (Java 21/25, JEI pin or the stand-in probe), the smoke harness renamed by the gate harness tables down the versions (1.21.1 verbatim), a jar refused for another version's ranges / newer class files / a lost entry class or contract name / a bundled class, MANIFEST + SHA256SUMS over exactly one jar per version, a release cut only when a version's sources, NeoForge or Java change, and the client verdict required on stdout and in latest.log" \
  || bad "gatec-carrier self-check: $out"

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
            "install-port.py|install-port: a fork port and its sibling ports install from their manifests, sha256-checked, newest file on conflict; a manifest behind its fork's newest full Release is installed as tested and named NOT THE NEWEST RELEASE" \
            "real-install.py|real-install: a per-version plan from fixture branches (the versions every fork has, each fork's variant jar of its branch's series, a workflow's --dep-jar / cf pin, registry deps; port-install.json never read; sizes; disk refusal), the Launcher's launch command from an inheritsFrom chain with OS/arch/feature rules and the NeoForge jar never vanilla's, boot and carrier verdicts, profiles refused while the Launcher runs, mods/ exactly the set, carrier sources sha-checked, the report" \
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
            "audit-wiring.py|audit-wiring: a dropped @EventBusSubscriber or register call, an unregistered Forge capability, a lost registration and a null-side provider are reported; a clean port is not" \
            "audit-descriptors.py|audit-descriptors: a removed method, a changed signature and a moved class in a transformer's strings are reported; a supertype's method, a mod's own names, comments and mixin files are not" \
            "convert-override-signatures.py|convert-override-signatures: a hook whose signature changed after 1.21.1 keeps its body in a private 1.21.1-shaped copy; --crossing FROM..TO runs only the rows landing in that hop (1.21.2 / 1.21.5), idempotent, and the hops add up to the one-step era run" \
            "port-derive.py|port-derive: a dev-branch port replayed onto the release, unreleased files and their port-only handlers dropped" \
            "mechanical-hop.py|mechanical-hop: members, then every converter in order, then members (the era stage, no --crossing); a converter that changed nothing is not recounted; one needing the port's input is an advisory; the version-branch LADDER runs a converter only on a hop its change lands in, an output that needs a later version is an advisory, a re-run changes nothing" \
            "layer-inventory.py|layer-inventory: the base build's mixin targets/injections (fork-port's hook_targets), event listeners, render classes and uses, core shaders with uniforms/samplers and registries; per version branch same through the rename tables, changed in the branch, dropped by drops.txt or BEHAVIOUR.json, accounted by a reviewed stub or REVIEW.md, from 1.21.5 a sampler no RenderPipeline names is unaccounted (exit 16); a single-version fork has no requirement and nothing written; a layer checkout is refused" \
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
# real-install: --mc narrows a saved plan to those versions (the file keeps every version); an unknown one is refused
RI="$T/real-install-plan"; mkdir -p "$RI"
printf '{"label":"examplemod","versions":[{"mc":"1.21.1","problems":[]},{"mc":"1.21.4","problems":[]}],"totals":{"disk_ok":false}}' > "$RI/plan.json"
ri_bad="$(python3 tools/real-install.py install --plan "$RI/plan.json" --mc 9.9 2>&1)"; ri_bad_rc=$?
ri_one="$(python3 tools/real-install.py install --plan "$RI/plan.json" --mc 1.21.4 2>&1)"
[ "$ri_bad_rc" = 2 ] && grep -q '9.9 not in the plan' <<<"$ri_bad" && grep -q '(examplemod: 1.21.4)' <<<"$ri_one" \
  && grep -q '"1.21.1"' "$RI/plan.json" \
  && ok "real-install: --mc narrows a saved plan to those versions and keeps the file whole; a version not in it is refused" \
  || bad "real-install --mc with --plan: rc=$ri_bad_rc $(tail -2 <<<"$ri_bad") | $(tail -2 <<<"$ri_one")"

out="$(python3 tools/visual-review.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "visual review: black, flat and missing-texture frames are findings without a model; verdict lines parse" \
  || bad "visual-review self-check: $out"

out="$(python3 tools/behaviour-tests.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "behaviour tests: failures read from the GameTest log, a run with no summary is not a pass" \
  || bad "behaviour-tests self-check: $out"

echo "both porting routes run the same deterministic era stage"
cat > "$T/test-port-tools-heredoc-3.py" <<'PY'   # written out, then run: macOS bash 3.2 misparses quotes in a heredoc inside $( )
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
out="$(python3 "$T/test-port-tools-heredoc-3.py" 2>&1)"
grep -q '^parity OK$' <<<"$out" && ok "port.py and port-upstream.py both run tools/mechanical-hop.py's Forge and era stages; no route runs a stage tool itself; every era converter is listed" \
  || bad "route parity: $out"

echo "version-branch hops run the converters whose change lands in the hop; the era route and a single-version fork as before"
LD="$T/ladder"; mkdir -p "$LD/sv/src/main/java/com/example/mod" "$LD/vb/src/main/java/com/example/mod"
cat > "$LD/Blade.java" <<'JAVA'
package com.example.mod;

public class Blade extends Item {
    @Override
    public boolean hurtEnemy(ItemStack s, LivingEntity target, LivingEntity attacker) {
        target.igniteForSeconds(2);
        return super.hurtEnemy(s, target, attacker);
    }

    @Override
    public void releaseUsing(ItemStack s, Level level, LivingEntity user, int left) {
        user.stopUsingItem();
    }
}
JAVA
cp "$LD/Blade.java" "$LD/sv/src/main/java/com/example/mod/"; cp "$LD/Blade.java" "$LD/vb/src/main/java/com/example/mod/"
printf 'mod_id=examplemod\nminecraft_version=1.21.1\n' > "$LD/sv/gradle.properties"
cat > "$T/test-port-tools-heredoc-4.py" <<'PY'   # written out, then run: macOS bash 3.2 misparses quotes in a heredoc inside $( )
import importlib.util, pathlib, subprocess, sys
ld = pathlib.Path(sys.argv[1])
def load(n, f):
    s = importlib.util.spec_from_file_location(n, f"tools/{f}"); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
fp = load("fp", "fork-port.py")
bad = []
blade = lambda r: (r / "src/main/java/com/example/mod/Blade.java").read_text(encoding="utf-8")
orig = (ld / "Blade.java").read_text(encoding="utf-8")
# single-version fixture: no version branch -> fork-port stops before any stage (exit 2, fresh or resumed at moves)
# and no converter runs: the file is byte for byte as it was, no convert-*.out anywhere
sv = ld / "sv"
subprocess.run(["git", "init", "-q", str(sv)], check=True)
if load("fk", "fork-kind.py").detect(sv)[0] != "single-version":
    bad.append("a single-version fixture is taken as a version-branch fork")
for extra in ([], ["--from-stage", "moves"]):
    r = subprocess.run([sys.executable, "tools/fork-port.py", "--repo", str(sv), "--target", "1.21.5", *extra],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 2 or "single-version fork" not in r.stdout or blade(sv) != orig or list(sv.rglob("convert-*.out")) or "converters landing" in r.stdout:
        bad.append(f"fork-port on a single-version fork ran a converter or did not stop at setup ({extra}): {r.returncode} {r.stdout[-300:]}")
# the era route calls the converter with no --crossing and every row runs, exactly as before
subprocess.run([sys.executable, "tools/convert-override-signatures.py", "--src", str(sv / "src/main/java")], capture_output=True, check=False)
if "public void hurtEnemy(" not in blade(sv) or "public boolean releaseUsing(" not in blade(sv):
    bad.append("the era call (no --crossing) no longer converts every row")
# a version-branch hop: 1.21.5 -> 1.21.8 crosses no landing version and runs nothing
vb = ld / "vb"
line, steps, adv = fp.branch_converters(vb, "1.21.5", "1.21.8", ld / "w")
if steps or adv or blade(vb) != orig:
    bad.append(f"a hop crossing no landing version ran a converter: {line}")
# 1.21.1 -> 1.21.3 crosses 1.21.2: releaseUsing moves, hurtEnemy (1.21.5) stays
line, steps, adv = fp.branch_converters(vb, "1.21.1", "1.21.3", ld / "w")
if "public boolean releaseUsing(" not in blade(vb) or "public boolean hurtEnemy(" not in blade(vb):
    bad.append(f"1.21.1 -> 1.21.3 did not convert exactly the 1.21.2 rows: {line}")
# 1.21.4 -> 1.21.5 crosses 1.21.5: hurtEnemy moves; no gear class named, so no gear-tiers advisory
line, steps, adv = fp.branch_converters(vb, "1.21.4", "1.21.5", ld / "w")
after = blade(vb)
if "public void hurtEnemy(" not in after or adv:
    bad.append(f"1.21.4 -> 1.21.5 did not convert hurtEnemy, or advised gear-tiers with no gear class: {line} {adv}")
# a hurtEnemy that vetoes (return false spared the durability damage of postHurtEnemy on 1.21.1) is REFUSED, file untouched
vt = ld / "veto"; (vt / "src/main/java/com/example/mod").mkdir(parents=True)
veto = orig.replace("extends Item", "extends SwordItem").replace("        target.igniteForSeconds(2);\n",
                                                                 "        if (target.isBlocking()) return false;\n")
(vt / "src/main/java/com/example/mod/Blade.java").write_text(veto, encoding="utf-8")
line, steps, adv = fp.branch_converters(vt, "1.21.4", "1.21.5", ld / "wv")
if blade(vt) != veto or "1 REFUSED" not in line or not any("gear-tiers" in a and "26.1.2+" in a for a in adv):
    bad.append(f"a vetoing hurtEnemy was converted, or the refusal / the gear advisory (a SwordItem is named) is missing: {line} {adv}")
# the two ladder hops end where the one era-route step does, and a re-run changes nothing
if after != blade(sv):
    bad.append("the ladder hops and the one era-route step disagree")
fp.branch_converters(vb, "1.21.4", "1.21.5", ld / "w")
if blade(vb) != after:
    bad.append("a re-run of the hop converted again")
print("\n".join(bad) or "ladder OK")
PY
out="$(python3 "$T/test-port-tools-heredoc-4.py" "$LD" 2>&1)"
grep -q '^ladder OK$' <<<"$out" && ok "version-branch ladder: a hop crossing a converter's landing version runs it (1.21.1->1.21.3 the 1.21.2 rows, 1.21.4->1.21.5 the 1.21.5 rows), a hop crossing none runs nothing, a re-run converts nothing twice, the hops add up to the era route's one step; a vetoing hurtEnemy is REFUSED and counted; gear-tiers is an advisory below 26.1.2 only where a 1.21.x gear class is named; on a single-version fixture fork-port stops before any stage (exit 2) with no converter run and the file unchanged, and the era call is unchanged" \
  || bad "version-branch ladder converters: $out"

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

out="$(python3 tools/fork-kind.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "fork-kind: one port branch (and a single-branch clone of the head) is single-version; a neoforge-<v> branch with .port/<v>/branch.properties (local or origin's) makes version branches; a legacy layer branch is listed and ignored; a .multiversion/ holding only shipped-jar.txt is no layer but refused, told to move it to .port/<mc>/; the layer at HEAD is refused naming the last supporting commit" \
  || bad "fork-kind self-check: $out"
out="$(python3 tools/checkout-lock.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "checkout-lock: one run per checkout (a second refused naming the holder), the holder's child let through, a dead or unreadable lock taken over" \
  || bad "checkout-lock self-check: $out"
# the removed layer: a checkout with .multiversion/base.properties (or targets/) at HEAD is refused by every tool that
# takes a fork checkout, exit 2, naming the last commit that supports the layer and the migration there; nothing written
LAST="$(python3 -c 'import importlib.util as u; s=u.spec_from_file_location("fk","tools/fork-kind.py"); m=u.module_from_spec(s); s.loader.exec_module(m); print(m.LAST_LAYER_COMMIT[:12])')"
LR="$T/layer-refused"; mkdir -p "$LR/.multiversion/targets/1.21.4"; git -C "$LR" init -q -b neoforge-1.21.1
printf 'minecraft_version=1.21.1\nmod_id=examplemod\n' > "$LR/gradle.properties"; echo 'plugins {}' > "$LR/build.gradle"
printf 'minecraft_version=1.21.1\n' > "$LR/.multiversion/base.properties"; echo x=1 > "$LR/.multiversion/targets/1.21.4/target.properties"
before="$(cd "$LR" && find . -path ./.git -prune -o -type f -print | sort)"
git -C "$LR" remote add origin https://github.com/owner/examplemod
printf '{"forks": [{"repo": "owner/examplemod", "head": "neoforge-1.21.1"}]}\n' > "$T/lr-config.json"; echo '{}' > "$T/lr-frozen.json"
# every user-facing tool that takes a fork checkout (a --repo, or a --fork ...=DIR)
refused=""; n=0
while read -r cmd; do
  n=$((n+1)); out="$(python3 tools/$cmd 2>&1 </dev/null)"; code=$?
  { [ $code = 2 ] && grep -q "has the .multiversion/ layer at HEAD" <<<"$out" && grep -q "$LAST" <<<"$out" && grep -q 'fork-branches.py migrate' <<<"$out"; } \
    || refused="$refused [${cmd%% *}: exit $code: ${out:0:200}]"
done <<LAYERTOOLS
fork-kind.py detect --repo $LR
fork-port.py --repo $LR --target 1.21.4
fork-branches.py ci-check --repo $LR
ci-gates.py --repo $LR --modid examplemod
layer-deps.py resolve --repo $LR --target 1.21.4
layer-inventory.py build --repo $LR
port-ci.py --repo $LR --modid examplemod
port-publish.py publish --repo $LR --tag t
port-sync.py --repo $LR --port neoforge-1.21.1 --upstream upstream/main
port-upstream.py --repo $LR --modid examplemod
port-offer.py --repo $LR
port-derive.py --repo $LR --branch neoforge-1.21.1 --base origin/main
port-provenance.py --repo $LR --base HEAD
port-manifest.py refresh --repo $LR
review-metrics.py --repo $LR --base HEAD
manual-tests.py --repo $LR
port-ci-kit.py trace --repo $LR --modid examplemod
audit-wiring.py --repo $LR --against HEAD
shipped-gate.py --jar $T/none.jar --repo $LR
known-issues.py list --repo $LR
weekly-plan.py install --repo $LR --fork owner/examplemod@neoforge-1.21.1=https://example.invalid/a#main --plan-owner someone
round-preflight.py --fork owner/examplemod=$LR
round.py init --frozen $T/lr-frozen.json --fork owner/examplemod=$LR --config $T/lr-config.json --round-dir $T/lr-round
port-cost.py round-table --since 2026-01-01 --fork a=$LR
LAYERTOOLS
after="$(cd "$LR" && find . -path ./.git -prune -o -type f -print | sort)"
[ -z "$refused" ] && [ "$before" = "$after" ] \
  && [ "$n" = 24 ] \
  && ok "a .multiversion/ layer at HEAD is refused (exit 2) by all $n fork-taking tools (fork-kind, fork-port, fork-branches, ci-gates, layer-deps, layer-inventory, port-ci, port-publish, port-sync, port-upstream, port-offer, port-derive, port-provenance, port-manifest, review-metrics, manual-tests, port-ci-kit trace, audit-wiring, shipped-gate, known-issues, weekly-plan install, round-preflight, round init, port-cost round-table), naming commit $LAST and the migration; nothing written" \
  || bad "layer refusal:$refused $( [ "$before" = "$after" ] || echo 'files written')"
# a migrated fork (version branches exist) checked out on its legacy layer branch: refused (exit 2), told to check out
# its head branch -- no migration, no commit named
LV="$T/legacy-checkout"; mkdir -p "$LV"; git -C "$LV" init -q -b neoforge-1.21.1; git -C "$LV" config user.email a@b; git -C "$LV" config user.name a
printf 'minecraft_version=1.21.1\n' > "$LV/gradle.properties"; git -C "$LV" add -A; git -C "$LV" commit -qm base
git -C "$LV" checkout -qb neoforge-1.21.4; mkdir -p "$LV/.port/1.21.4"
printf 'parent=neoforge-1.21.1\nport_branch=neoforge-1.21.1\n' > "$LV/.port/1.21.4/branch.properties"; git -C "$LV" add -A; git -C "$LV" commit -qm 1.21.4
git -C "$LV" checkout -q neoforge-1.21.1; git -C "$LV" checkout -qb neoforge-1.21.x; mkdir -p "$LV/.multiversion/targets/1.21.4"
printf 'minecraft_version=1.21.1\n' > "$LV/.multiversion/base.properties"; echo x=1 > "$LV/.multiversion/targets/1.21.4/target.properties"
git -C "$LV" add -A; git -C "$LV" commit -qm layer
lvbad=""
for cmd in "fork-kind.py detect --repo $LV" "fork-port.py --repo $LV --target 1.21.4" "port-sync.py --repo $LV --port neoforge-1.21.1 --upstream upstream/main"; do
  out="$(python3 tools/$cmd 2>&1)"; code=$?
  { [ $code = 2 ] && grep -qF "is a version-branch fork checked out on a legacy layer branch (neoforge-1.21.x); check out its head branch (neoforge-1.21.1)" <<<"$out" \
      && ! grep -q 'migrate' <<<"$out" && ! grep -q "$LAST" <<<"$out"; } || lvbad="$lvbad [${cmd%% *}: exit $code: ${out:0:200}]"
done
git -C "$LV" checkout -q neoforge-1.21.1
out="$(python3 tools/fork-kind.py detect --repo "$LV" 2>&1)"; code=$?
[ -z "$lvbad" ] && [ $code = 0 ] && grep -q '^kind=version-branches versions=1.21.4 legacy_layer=neoforge-1.21.x$' <<<"$out" \
  && ok "a migrated fork checked out on its legacy layer branch is refused (exit 2) and told to check out its head branch (no migration named); on the head it is a version-branch fork" \
  || bad "legacy layer checkout:$lvbad detect on the head: exit $code: $out"
# single-version: no version branch -- fork-port stops before any stage, having written nothing; --chain, --check-done
# and --write-test-items stop the same way (exit 2)
SV="$T/r145-single"; mkdir -p "$SV"; git -C "$SV" init -q -b neoforge-1.21.1
printf 'minecraft_version=1.21.1\nneo_version=21.1.228\n' > "$SV/gradle.properties"; echo 'plugins {}' > "$SV/build.gradle"
out="$(python3 tools/fork-port.py --repo "$SV" --target 1.21.4 2>&1)"; code=$?
[ $code = 2 ] && grep -q 'single-version fork' <<<"$out" && ! grep -qi 'upstream\|review\|NOT done' <<<"$out" && [ ! -e "$SV/.multiversion" ] && [ ! -e "$SV/.port" ] \
  && ok "fork-port on a single-version fork: stops before any stage (exit 2); no freeze, no review, nothing written" || bad "single-version fork-port: exit $code: $out"
python3 tools/fork-port.py --repo "$SV" --chain 1.21.3..1.21.4 >/dev/null 2>&1; c1=$?
python3 tools/fork-port.py --repo "$SV" --target 1.21.4 --check-done >/dev/null 2>&1; c2=$?
python3 tools/fork-port.py --repo "$SV" --target 1.21.4 --write-test-items >/dev/null 2>&1; c3=$?
[ $c1 = 2 ] && [ $c2 = 2 ] && [ $c3 = 2 ] && [ ! -e "$SV/.multiversion" ] && [ ! -e "$SV/.port" ] && [ -z "$(find "$SV" -name TEST-ITEMS.md -o -name .fork-port.lock)" ] \
  && ok "single-version: --chain, --check-done and --write-test-items stop the same way (exit 2), nothing written" || bad "single-version --chain $c1 / --check-done $c2 / --write-test-items $c3"
out="$(python3 tools/fork-port.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "fork-port: stage selection, moves table, residue report, STOP block, every --from-stage resume re-measures, one run per checkout (tools/checkout-lock.py), a red full gate run is written to GATES-RED.md and keeps GATES.md byte for byte (not done, auto runs full, a green run deletes it), a killed or memory-refused Gate C is environmental (RESIDUE.md: re-run when memory is free, no fixer), --gates none and an open stub after --from-stage cost are not done, --port-release reaches layer-deps and the resume command, a library version not done stops setup with exit 18 and --allow-undone-library is passed through, recorded and resumed, the upstream freeze in .port/<t>/branch.properties stops a run when the authors moved (exit 19; --accept-upstream-drift records DRIFT.md), and on a single-version fixture fork-port stops at once and writes nothing" \
  || bad "fork-port self-check: $out"
out="$(python3 templates/multi-version/tools/gametest_adapter.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "gametest adapter: both dialects (26.x Identifier / 1.21.5 ResourceLocation + mod-bus attribute) strip the dead annotations and register every test" \
  || bad "gametest_adapter self-check: $out"
cat > "$T/test-port-tools-heredoc-5.py" <<'PY'   # written out, then run: macOS bash 3.2 misparses quotes in a heredoc inside $( )
import importlib.util, pathlib
s = importlib.util.spec_from_file_location("pg", "tools/port_gates.py"); m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
assert [m.no_gametest_annotation(v) for v in ("1.21.1", "1.21.4", "1.21.5", "1.21.10", "26.2")] == [False, False, True, True, True]
assert pathlib.Path("templates/upstream-harness/versions/1.21.5.renames.tsv").read_text().count("snapTo") >= 1
print("self-check: OK")
PY
out="$(python3 "$T/test-port-tools-heredoc-5.py" 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "port_gates: annotation-free GameTest harness from 1.21.5 on, 1.21.1-1.21.4 left alone" \
  || bad "port_gates check: $out"

fi   # part 1
if _part 2; then   # part 2: the known-issues ledger, the weekly cycle and round
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
v = s[s.index("## A fork with version branches"):s.index("## The weekly cycle")]
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
# block, the freeze rides in the version's PR, a version's release is dispatched right after its merge -- on every kind
# of fork (#161 D4: the release job needs that run's own gates on either route), and a single-version fork gates once on
# the tree that merges (D3)
python3 - <<'PY' && ok "keep-current, the round's cost: fresh worker subagents with ~40-line summaries, one blocking wait, round/NOTES.md past ~200k tokens; every fork set -- single-version, version-branch or mixed -- runs on tools/round.py instead (round-task's --version-branches changes nothing; waits inside the driver, state in round/STATE.json with resume, one full gate run on the machine, the library pre-merge dependent gate, a single-version library's too), a single-version fork on it is its head alone, in the single-version round's order; on a version branch the freeze in its PR, the release dispatched right after the merge (its own full gates on the released tree), one full local gate run after the review's fixes, the head's workflow named by ci-check; a single-version fork too dispatches the release right after the merge and runs one full gate pass after an A/B-only run the review starts from" || bad "keep-current no longer keeps the orchestrator small, or the CI order after a merge regressed on either route"
import pathlib, sys
s = pathlib.Path(".claude/skills/keep-current/SKILL.md").read_text(encoding="utf-8")
flat = lambda x: " ".join(x.split())
r = flat(s[s.index("## The weekly round"):s.index("## README Ports block")])
v = flat(s[s.index("## A fork with version branches"):s.index("## The weekly cycle")])
run6 = flat(s[s.index("6. **Push the work branch and open a PR"):s.index("7. **Dependents next.**")])
need_r = ("fresh subagents that return a short summary -- at most ~40 lines", "Wait in ONE blocking command",
          "`gh run watch <run id> --exit-status`", "round/NOTES.md", "past ~200k tokens",
          "Every fork set runs on its driver, `tools/round.py`; the session never does a step by hand",
          "`--version-branches`, which older setups pass, changes nothing", "**A single-version library has the same gate**",
          "**A single-version fork on the driver is its head alone**", "`ci-gates.py` full and minimal",
          "(dispatched right after the merge; the merge's push run and the release run awaited together)",
          "**fresh subagent** whose whole prompt is the job's `prompt` line", "at most ~40 lines",
          "**Waits are one blocking loop inside the driver**", "**State is the round's only memory:**", "`round.py resume`",
          "at most one full gate run on the machine", "**The library pre-merge dependent gate.**",
          "A red run is a blocking finding on the LIBRARY version",
          "A worker MAY carry the same change across several versions", "fresh worker per version",
          "**Dispatch it right after the merge** (every kind of fork; for now a version-branch fork's HEAD is the exception",
          "against a PRIVATE mavenLocal", "`~/.m2` itself is never written", "no pre-release is cut without a green full-gate run on exactly the released tree",
          "--from-stage measure --gates full", "ci --repo DIR --head --run",
          "**On a single-version fork, the cheapest correct sequence** -- one full gate pass", "--gatec none",
          "ONE pass of `ci-gates.py ... --env full` then `--env minimal`")
need_v = ("Move each version's freeze in its PR, not after it", "before its work branch is pushed")
sys.exit(0 if all(n in r for n in need_r) and all(n in v for n in need_v)
         and "Then wait for the CI run the merge itself triggers" in run6 and "Start the fork's release run (`release=true`) right after the merge" in run6
         and "A single-version fork keeps step 6's order" not in r else 1)
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
out="$(python3 tools/round.py --self-check 2>&1)"
grep -q 'round self-check: OK' <<<"$out" && ok "round.py (in $(grep -o '[0-9.]* s)$' <<<"$out" | tr -d ')')): init on a library + dependent fixture (three and two version branches) builds the queue in ladder order, each version after the one below merged, the library's pre-merge dependent gate (the dependent's Gate C against the candidate jar) between its gates and its merge and only where the dependent has that version, the dependent's re-pin after the library's release and its library record after its own Gate C; drive runs the mechanical steps (stand-ins), prints short self-contained agent jobs and writes a port-cost mark with --phase at every item; one running item per clone; a review with no not-ok row skips the fix and re-check; a failed pre-merge dependent gate blocks the library merge, becomes a fixer job, and its fix re-runs the library's gates and the dependent gate; resume re-lists an out job and re-queues a step whose process died; a blocked item stops only its dependents, drive then exits 3 naming it, and the close comes once nothing else can run; a step the allow list lacks is blocked and asked once on the plan issue; the PR, CI, merge (a merge commit) and release steps against a stand-in GitHub and a bare origin; a second full gate run waits for the machine's lock; single-version forks on the driver (#161 U4) -- a set of single-version forks only (a library and its dependent), a version-branch library with single-version dependents (at its head's version and at a version branch's), a single-version library with a version-branch dependent: each queue in the single-version round's order (ci-check, setup, upstream, authors, repin-head, backlog, review, fix, recheck, ONE gates pass, the library's dependent gate, pr, ci, merge, release), the library's pre-merge dependent gate before its merge, a red one its fixer's job with the gates and the gate run again, each set driven to the close and an interrupted step re-queued by resume from STATE.json; no lowest check, shared-library read or ladder step on a single-version fork; a version branch pinning a single-version library that releases this round refused, and otherwise the queue built and the hand re-pin named in the close; the head path's real steps on a single-version fixture against a stand-in GitHub and bare origins -- port-ci.py check's warning recorded and the workflow regenerated at setup, the authors' frozen commit merged by port-sync.py and a conflict its fixer's job, one ci-gates full+minimal pass under the machine's lock with the old jars removed, the dependent's head gated under the lock (its workflow's --base) against a private mavenLocal holding the candidate (its name and variant matched as a release asset) while ~/.m2 is never written, only the gated commit in the PR, the merge with no push-run wait and the release dispatched right after it, no README Ports block without a known-issues ledger; the done list on a single-version fork (#161 U5) -- the review job on .port/<mc>/REVIEW.md against the round's base, the records step committing check-done's records, the full run committed as GATES.md, a library marked in port.properties with no dependent gate refused, the done step reporting only while origin's head has no records and then blocking until the review is filled, an undone library pre-release re-pin marked in DEPS.md (promotion refuses it) and the mark dropped on a done one; the shared library: a version whose helper copy is older than the template gets a sync step (its own commit) before its gates and a full segment, an up-to-date fork gets no work, a hand-edited copy whose template moved is a reconcile job and stays untouched, an adopt candidate is a job only when the plan ticked it, a sync-only version gets the narrow review, an MV-AHEAD-OF-TEMPLATE file reaches the close job's Lessons" \
  || bad "round.py self-check: $( (grep '^FAIL' <<<"$out" || true; tail -3 <<<"$out") | head -40)"
out="$(python3 tools/weekly-plan.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "weekly-plan self-check (in $(grep -o '[0-9.]* s)$' <<<"$out" | tr -d ')')): the workflow equals tools/golden/weekly-plan.yml (Tuesdays 15:00 and 16:00 UTC plus workflow_dispatch, contents: read and issues: write, no secrets, the tools at a pinned commit, no model); its first step lets only the 08:00 Los Angeles slot through on both sides of daylight saving (and with no tz database); the deadline is the next Wednesday round; install refuses an org-owned fork without plan_owner, a checkout off the head branch and a head that is not the default branch (unless --default-branch-too), and is idempotent; the round task is CRON_TZ=America/Los_Angeles with a jittered minute, a fresh session, push and email on; the allow list is empty by default (the round asks) and rendered verbatim into the prompt, automerge follows merge-fork-prs; the round's own work branches and PRs fall under merge-fork-prs, the ledger and README Ports block under push-version-branches, plan-issue comments and ledger-issue edits are always allowed and the migrator repository is only read; the round clones current main and says the plan ran at migrator_sha, which install records; a re-install over an existing config (a single-version fork and a layer fork with layer_branch in one set) warns on stderr for each allow, ledger_issues, depends_on and layer_branch its flags drop, naming the flag, keeps the default result otherwise unchanged (no warning on a fresh install or with every value re-passed), and --keep-existing keeps them while a flag given still wins; replaces lists exact task names (its own, --replace-task, existing keep-current names of this set); the authors' upstream comes from the fork parent when omitted; --trailer lines end the install commit; the plan issue has the deadline first, is assigned to the owner, a section per fork; a second run the same week opens nothing; an unclosed plan is carried forward (ticks and the owner's replies, not a drive-by's) and closed as superseded; a single-version fork's Decisions section shows the fetch step's trial merge of the authors' new commits (a conflicting file listed, a clean merge said clean, a failed merge said with git's error, no branch moved); an incomplete plan opens no issue; the round prompt runs every fork set on tools/round.py (rules 7 and 8: init, drive in the background, each AGENT JOB a fresh subagent and its record line, exit 3 = blocked, resume from round/STATE.json, the driver marking the phases) before the lessons rule, byte for byte tools/golden/round-task-single-version.txt, and round-task --version-branches is accepted and prints the same task" \
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
fi   # part 2
if _part 3; then   # part 3: stacked version branches onward (fork-branches' full self-check is the long pole)
# ---- stacked version branches (tools/fork-branches.py, the multi-version fork): the branch-mode done check through
# fork-port.py's own CLI, the cascade, the library's release-side done check -- on the frozen version-branch fixture
# (tools/fixtures/version-branches.fi); a single-version fork is untouched (its own checks above and below run unchanged)
echo "fork-branches: stacked version branches (done, cascade, library release, CLI)"
export FB_FIXTURE_CACHE="$T/fb-fixtures"    # the fork-branches fixtures, built once for every block below
# On Windows (Git Bash: msys/cygwin) only the platform leg: the line-ending cases that failed natively there. The full default self-check -- every named case -- runs on Linux on every PR.
fb_mode=""; case "${OSTYPE:-}" in msys*|cygwin*|win*) fb_mode="--platform" ;; esac
out="$(python3 tools/fork-branches.py --self-check $fb_mode 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "fork-branches self-check${fb_mode:+ $fb_mode, the OS leg -- Linux runs all of}: on the frozen 3-version fixture each version branch is done from its migrated records (MIGRATED.json; without it the legacy layer records do not count); a layer at HEAD is refused; an edit stales gates and review; cascade carries the authors' change through each version's rows and a lower fix upward (never the head branch's known-issues ledger), and stops on a conflict; add-target refused over an undone parent; the library rule; a single-version fork refused, nothing written; cascade --plan on a throwaway clone (one decision per file and hunk across versions, mechanical ones merged, a default logged, an escalated one in the one issue), --apply holding only the decision files open while the versions above go on, --apply-decisions clearing them everywhere; port_taken re-offers a change no run took; ci-check names a head branch whose workflow pins an older kit or ignores no round records and ci --head regenerates it from itself (only the kit moved), while a single-version fork gets no head line and ci --head refuses it, writing nothing; sync-shared --check reads only (no ref moved) and names what a sync would change, a hand-edited table section to reconcile, adopt candidates (the fork's own helper copy, a duplicated row) and MV-AHEAD-OF-TEMPLATE files, while a single-version fork reports nothing; sync-shared leaves an edited section alone (and a differing one when the library's history is unknown), leaves a listed helper reverted to an older library text alone, and updates an unlisted stand-in of an old library text; a cascade keeps the upper version's installed helper copy" \
  || bad "fork-branches self-check: $(tail -5 <<<"$out")"
cat > "$T/test-port-tools-heredoc-6.py" <<'PY'   # written out, then run: macOS bash 3.2 misparses quotes in a heredoc inside $( )
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
out="$(python3 "$T/test-port-tools-heredoc-6.py" "$T/fb" 2>&1)"
[ "$out" = "OK" ] && ok "fork-port.py on version branches: --check-done done off the branch, a resume refused off it, --write-test-items on it, an edit makes it not done (gates stale, a review row), --chain is the cascade and stops at a conflict; layer-deps' release-side done check on a tag agrees with the checkout's (its clone under \$MMVU_CACHE_DIR, re-pointed when the URL changes)" \
  || bad "fork-port / layer-deps on version branches: $out"
# ---- #161 U1: the safety checks a version-branch head gets run on a single-version fork too (P5) -- on a single-branch
# clone of the frozen fixture's head (detected single-version), with the version-branch fixture alongside, unchanged
echo "#161 U1: stale Port CI kit, merge onto a stale branch, preflight remotes, shipped-jar path -- both kinds of fork"
cat > "$T/test-port-tools-heredoc-7.py" <<'PY'   # written out, then run: macOS bash 3.2 misparses quotes in a heredoc inside $( )
import importlib.util, json, pathlib, subprocess, sys
d = pathlib.Path(sys.argv[1]); d.mkdir()
L = lambda n, f: (lambda s: (s.loader.exec_module(m := importlib.util.module_from_spec(s)), m)[1])(importlib.util.spec_from_file_location(n, f"tools/{f}"))
fb, pc, ps, pf, fk, cg = (L("fb", "fork-branches.py"), L("pc", "port-ci.py"), L("ps", "port-sync.py"),
                          L("pf", "round-preflight.py"), L("fk", "fork-kind.py"), L("cg", "ci-gates.py"))
r, g, _ = fb._migrated_fixture(d)                     # the version-branch fork
bad = []
G = lambda w, *a: subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *a],
                                 capture_output=True, text=True, check=True).stdout.strip()
g("checkout", "-q", "neoforge-1.21.1")
wf = pc.fixture(False)                                # a single-version workflow at kit 7
(r / ".github/workflows").mkdir(parents=True, exist_ok=True); (r / pc.WORKFLOW).write_text(wf, encoding="utf-8")
g("add", "-A"); g("commit", "-qm", "CI at kit 7")
sv = d / "sv"
subprocess.run(["git", "clone", "-q", "--single-branch", "-b", "neoforge-1.21.1", str(r), str(sv)], check=True, capture_output=True)
G(sv, "config", "user.name", "t"); G(sv, "config", "user.email", "t@example.invalid")   # port-sync's merges commit here
fk.detect(sv)[0] == "single-version" or bad.append(f"the head's single-branch clone is single-version: {fk.detect(sv)}")
fk.detect(r)[0] == "version-branches" or bad.append("control: the fixture is a version-branch fork")
# E1: the kit check -- one function (port-ci.py stale_workflow) for the single-version port branch and the head
said = []; pc.check(sv, kit_version=9, say=said.append)
(len(said) == 1 and said[0].startswith("port-ci: check: WARNING neoforge-1.21.1: its workflow pins Port CI kit 7, older than kit 9 -- regenerate: python3 tools/port-ci.py --repo")
 and "--kit-version" not in said[0]) or bad.append(f"E1 single-version, a stale kit: warned with the regenerate command: {said}")
said = []; rc = pc.check(sv, kit_version=7, say=said.append)
(rc == 0 and "is current" in said[0]) or bad.append(f"E1 single-version, current: {said}")
# ci-check prints the resolved checkout path (Windows: the long form of an 8.3 TEMP path), so the line is compared
# with that path, every other character exactly
head = [l for l in fb.ci_check(r, kit_version=9) if "(the head branch)" in l]
head == [f"fork-branches: ci-check: WARNING neoforge-1.21.1 (the head branch): its workflow pins Port CI kit 7, older than kit 9 -- "
         f"regenerate: python3 tools/fork-branches.py ci --repo {r.resolve()} --head --run, then push neoforge-1.21.1"] \
    or bad.append(f"E1 version-branch head: the same line as before: {head}")
[l for l in fb.ci_check(r, kit_version=7) if "(the head branch)" in l] == [] or bad.append("E1 version-branch head, current: no line")
# E2: port-sync compares the port branch with origin first (the cascade's origin_sync)
up = d / "authors"
subprocess.run(["git", "clone", "-q", "--single-branch", "-b", "neoforge-1.21.1", str(r), str(up)], check=True, capture_output=True)
(up / "NEW.txt").write_text("authors\n"); G(up, "add", "-A"); G(up, "commit", "-qm", "authors' change")
G(sv, "remote", "add", "authors", str(up)); G(sv, "remote", "set-url", "--push", "authors", "DISABLED")
g("checkout", "-q", "neoforge-1.21.1"); (r / "PR.txt").write_text("merged on origin\n"); g("add", "-A"); g("commit", "-qm", "a PR merged")
tip = G(r, "rev-parse", "HEAD"); g("checkout", "-q", "neoforge-1.21.4")
log = []; rc = ps.sync(sv, "neoforge-1.21.1", "authors/neoforge-1.21.1", out=log.append)
(rc == 0 and any("fast-forwarded the local neoforge-1.21.1" in l for l in log)
 and subprocess.run(["git", "-C", str(sv), "merge-base", "--is-ancestor", tip, "HEAD"]).returncode == 0
 and (sv / "NEW.txt").is_file()) or bad.append(f"E2 single-version, behind origin: fast-forwarded, then merged on origin's tip: {rc} {log}")
(sv / "LOCAL.txt").write_text("x\n"); G(sv, "add", "-A"); G(sv, "commit", "-qm", "local only")
g("checkout", "-q", "neoforge-1.21.1"); (r / "PR2.txt").write_text("y\n"); g("add", "-A"); g("commit", "-qm", "PR 2"); g("checkout", "-q", "neoforge-1.21.4")
(up / "NEW2.txt").write_text("z\n"); G(up, "add", "-A"); G(up, "commit", "-qm", "authors' change 2")
h = G(sv, "rev-parse", "HEAD"); log = []
rc = ps.sync(sv, "neoforge-1.21.1", "authors/neoforge-1.21.1", out=log.append)
(rc == 2 and any("has diverged" in l for l in log) and G(sv, "rev-parse", "HEAD") == h and not (sv / "NEW2.txt").exists()) \
    or bad.append(f"E2 single-version, diverged: refused, nothing merged or reset: {rc} {log}")
# E6: origin/HEAD and the authors' remote on the single-version clone; the version-branch fork's lines as before
plan = {"forks": [{"repo": "owner/examplemod", "upstream": "https://github.com/up/examplemod", "upstream_branch": "main"}]}
(sv / ".github/weekly-plan.json").write_text(json.dumps(plan)); G(sv, "add", "-A"); G(sv, "commit", "-qm", "plan")
G(sv, "remote", "set-url", "origin", "https://github.com/owner/examplemod.git")
G(sv, "remote", "set-head", "origin", "-d")
rs = {w: ok for ok, w, _d in pf.remote_checks(sv, pf.plan_upstreams([sv]))}
(rs.get(f"origin/HEAD of {sv}", 1) is None and rs.get(f"authors' remote of {sv}") is False) \
    or bad.append(f"E6 single-version: no origin/HEAD warned, no remote fetching the plan's upstream missing: {rs}")
G(sv, "remote", "add", "up", "https://github.com/up/examplemod.git")
rs = {w: ok for ok, w, _d in pf.remote_checks(sv, pf.plan_upstreams([sv]))}
rs.get(f"authors' remote of {sv}") is True or bad.append(f"E6 single-version, the remote there: ok: {rs}")
vb = pf.remote_checks(r)
(pf.remote_checks(r, {"owner/examplemod": ("https://github.com/up/examplemod", "main")}) == vb
 and not any("authors' remote" in w for _o, w, _d in vb)) or bad.append(f"E6 version-branch: its lines as before: {vb}")
# B2/F3: the shipped-jar list in .port/<mc>/ on the single-version clone; a .multiversion/ left there is refused
cg.shipped_jar_file(sv) == sv / ".port/1.21.1/shipped-jar.txt" or bad.append(f"B2: {cg.shipped_jar_file(sv)}")
cg.shipped_jar_file(r, "1.21.4") == r / ".port/1.21.4/shipped-jar.txt" or bad.append("B2 version branch: as before")
(sv / ".multiversion").mkdir(); (sv / ".multiversion/shipped-jar.txt").write_text("*_mr.jar\n")
fk.refuse_layer(sv, "ci-gates") is None or bad.append("B2: an uncommitted .multiversion/ file refuses nothing")
G(sv, "add", ".multiversion"); G(sv, "commit", "-qm", "the shipped list in the old place")
m = fk.refuse_layer(sv, "ci-gates")
(m and ".port/1.21.1/<file>" in m and "shipped-jar.txt" in m) or bad.append(f"B2: a stray .multiversion/ refused, the move named: {m}")
print("\n".join(bad) or "OK")
PY
out="$(python3 "$T/test-port-tools-heredoc-7.py" "$T/u1" 2>&1)"
[ "$out" = "OK" ] && ok "#161 U1 on a single-branch clone of the head (single-version) and the version-branch fixture: port-ci.py check warns about an older kit with the regenerate command (one stale_workflow, the head's ci-check line unchanged); port-sync fast-forwards a port branch behind origin before the merge and refuses a diverged one, nothing merged or reset; preflight warns about a missing origin/HEAD and names the authors' remote the weekly plan's upstream needs (a version-branch fork's lines unchanged); --shipped reads .port/<mc>/shipped-jar.txt and a stray .multiversion/ is refused with the move" \
  || bad "#161 U1: $out"
# ---- #161 U2 (C1): ci-gates runs the mixin-target and event-listener audits on every fork, report-only -- on a
# single-branch clone of the frozen fixture's head (single-version) and on its version branch 1.21.4, the gates' Gradle
# and Gate B faked, both audits real
echo "#161 U2: mixin-target and event-listener audits as ci-gates REPORT rows -- both kinds of fork"
if command -v javac >/dev/null 2>&1 && command -v jar >/dev/null 2>&1; then
cat > "$T/test-port-tools-heredoc-8.py" <<'PY'   # written out, then run: macOS bash 3.2 misparses quotes in a heredoc inside $( )
import contextlib, importlib.util, io, pathlib, shutil, subprocess, sys, zipfile
d = pathlib.Path(sys.argv[1]); d.mkdir()
L = lambda n, f: (lambda s: (s.loader.exec_module(m := importlib.util.module_from_spec(s)), m)[1])(importlib.util.spec_from_file_location(n, f"tools/{f}"))
fb, cg, fk = L("fb", "fork-branches.py"), L("cg", "ci-gates.py"), L("fk", "fork-kind.py")
bad = []
G = lambda w, *a: subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *a],
                                 capture_output=True, text=True, check=True).stdout.strip()
r, g, _ = fb._migrated_fixture(d)                     # the version-branch fork
sv = d / "sv"
subprocess.run(["git", "clone", "-q", "--single-branch", "-b", "neoforge-1.21.1", str(r), str(sv)], check=True, capture_output=True)
G(sv, "config", "user.name", "t"); G(sv, "config", "user.email", "t@example.invalid")
g("checkout", "-q", "neoforge-1.21.4")
fk.detect(sv)[0] == "single-version" or bad.append(f"the head's clone is single-version: {fk.detect(sv)}")
cg.branch_version(r) == "1.21.4" or bad.append("control: the checkout is version branch 1.21.4")
# the build's own NeoForge and Minecraft, faked: an abstract event with a concrete subclass, and the canaries the
# listener audit asks for; javac/jar from the JDK
ev = d / "evt"
for f, src in {"net/minecraft/world/item/Item.java": "package net.minecraft.world.item; public class Item {}",
               "net/neoforged/neoforge/common/NeoForge.java": "package net.neoforged.neoforge.common; public class NeoForge {}",
               "net/neoforged/bus/api/SubscribeEvent.java": "package net.neoforged.bus.api; public @interface SubscribeEvent {}",
               "net/neoforged/neoforge/client/event/Stage.java":
                   "package net.neoforged.neoforge.client.event;\npublic abstract class Stage { public static class After extends Stage {} }"}.items():
    (ev / "src" / f).parent.mkdir(parents=True, exist_ok=True); (ev / "src" / f).write_text(src + "\n", encoding="utf-8")
subprocess.run(["javac", "-d", str(ev / "classes"), *map(str, (ev / "src").rglob("*.java"))], check=True, capture_output=True)
subprocess.run(["jar", "cf", str(ev / "fake.jar"), "-C", str(ev / "classes"), "."], check=True, capture_output=True)
calls = []


class FakeGates:                  # tools/port_gates.py with no Gradle: Gate A green, the classpath task printed
    Fail = type("Fail", (Exception,), {})
    qtc = True

    @staticmethod
    def gate_a(c, log):
        return 3

    @staticmethod
    def gate_env(c):
        return {"PORT_LOG_DIR": str(c["dir"])}

    @staticmethod
    def gradle(repo, args, log, extra_init=(), env=None, timeout=None):
        calls.append(list(args))
        if "qtcClasspath" in args and FakeGates.qtc in ("timeout", "raise"):
            if FakeGates.qtc == "raise":
                raise RuntimeError("the classpath plumbing broke")
            raise subprocess.TimeoutExpired(args, timeout)
        out = f"QTC_CLASSPATH_BEGIN\n{ev / 'fake.jar'}\nQTC_CLASSPATH_END\n" if FakeGates.qtc is True and "qtcClasspath" in args \
            else ("FAILURE: Build failed with an exception.\n\n* What went wrong:\nExecution failed for task ':qtcClasspath'.\n"
                  "> Could not resolve all files for configuration ':compileClasspath'.\n   > Could not resolve com.example:lib:1.0.\n"
                  if FakeGates.qtc == "wrong" else "")
        pathlib.Path(log).write_text(out, encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, out, "")


real_load, real_run = cg._load, subprocess.run
cg._load = lambda n, f: FakeGates if n == "port_gates" else real_load(n, f)
subprocess.run = lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 0, "", "") \
    if any(str(x).endswith("gate-loop.py") for x in cmd) else real_run(cmd, *a, **k)
JAR = "build/moddev/artifacts/neoforge-{neo}-sources.jar"
VANILLA = "package net.x;\npublic class Dragon {\n    public void hurt(int amount) {}\n}\n"
MIXIN = ("package com.example.mod.mixin;\nimport net.x.Dragon;\n@Mixin(Dragon.class)\npublic class DragonMixin {\n"
         "    @Inject(method = \"%s\", at = @At(\"HEAD\"))\n    private void a(int amount, CallbackInfo ci) {}\n}\n")
HOOK = ("package com.example.mod;\nimport net.neoforged.bus.api.SubscribeEvent;\nimport net.neoforged.neoforge.client.event.Stage;\n"
        "public class Hook {\n    @SubscribeEvent\n    public static void on(%s event) {}\n}\n")


def tree(w, method="hurt", event="Stage.After", jar=True, mixin=True, hook=True):
    neo = cg.read_kv(w / "gradle.properties").get("neo_version")
    shutil.rmtree(w / "build", ignore_errors=True)
    if jar:
        (w / JAR.format(neo=neo)).parent.mkdir(parents=True)
        with zipfile.ZipFile(w / JAR.format(neo=neo), "w") as z:
            z.writestr("net/x/Dragon.java", VANILLA)
    m, h = w / "src/main/java/com/example/mod/mixin/DragonMixin.java", w / "src/main/java/com/example/mod/Hook.java"
    m.unlink(missing_ok=True); h.unlink(missing_ok=True)
    if mixin:
        m.write_text(MIXIN % method, encoding="utf-8")
    if hook:
        h.write_text(HOOK % event, encoding="utf-8")
    G(w, "add", "-A"); G(w, "commit", "-qm", "fixture", "--allow-empty")


def gates(w, name, reverted=False):
    calls.clear()
    real = cg.target_audit_rows
    if reverted:                  # the change undone: what ci-gates wrote before it
        cg.target_audit_rows = lambda *a, **k: []
    summ, work = d / f"{name}.md", d / f"{name}-work"
    bv = cg.branch_version(w)
    sys.argv = ["ci-gates.py", "--repo", str(w), "--modid", "examplemod", "--gatec", "", "--skip-author-build",
                "--summary", str(summ), "--work-dir", str(work)] + (["--mc", bv] if bv else [])
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            rc = cg.main()
    finally:
        cg.target_audit_rows = real
    return rc, summ.read_text(encoding="utf-8"), work


for kind, w in (("single-version", sv), ("version-branch", r)):
    tree(w)                                                            # clean: both audits ran, found nothing
    rc0, before, _ = gates(w, f"{kind}-before", reverted=True)
    rc, after, work = gates(w, f"{kind}-clean")
    ran = "all targets match" in (work / "audit-mixin-targets.log").read_text(encoding="utf-8") and \
        "no listener names" in (work / "audit-event-listeners.log").read_text(encoding="utf-8")
    (rc0 == rc == 0 and after == before and ran and "REPORT" not in after) or \
        bad.append(f"{kind}: a clean tree's GATES.md is byte-identical to the one without the audits (both ran): "
                   f"{rc0} {rc} {ran}\n{before}\n---\n{after}")
    tree(w, method="hurtGone", event="Stage")                          # a stale target and an abstract event
    rc, md, _ = gates(w, f"{kind}-stale")
    rows = [l for l in md.splitlines() if "| REPORT |" in l]
    # every finding's file repo-relative and POSIX on every OS (Windows printed an absolute, backslashed temp path)
    (rc == 0 and "FAIL" not in md and len(rows) == 2
     and rows[0] == f"| {cg.MIXIN_ROW} | REPORT | 1 to read: src/main/java/com/example/mod/mixin/DragonMixin.java @Inject "
                    "hurtGone: mixin: no such method in vanilla |"
     and rows[1] == f"| {cg.LISTENER_ROW} | REPORT | 1 to read: src/main/java/com/example/mod/Hook.java Stage: "
                    "net.neoforged.neoforge.client.event.Stage is abstract or an interface |") or \
        bad.append(f"{kind}: a stale mixin target and an abstract-event listener are one REPORT row each, never red: {rc}\n{md}")
    FakeGates.qtc = False                                              # inputs missing: no sources jar, no classpath
    tree(w, method="hurtGone", event="Stage", jar=False)
    rc, md, _ = gates(w, f"{kind}-missing")
    FakeGates.qtc = True
    rows = [l for l in md.splitlines() if "| REPORT |" in l]
    (rc == 0 and len(rows) == 2 and all("| REPORT | skipped: " in l for l in rows) and "sources jar" in rows[0]
     and "classpath" in rows[1]) or bad.append(f"{kind}: a missing input is a `skipped: <why>` row, never clean or red: {rc}\n{md}")
    tree(w, method="hurtGone", event="Stage")                          # both audits crash: no row
    neo = cg.read_kv(w / "gradle.properties").get("neo_version")
    (w / JAR.format(neo=neo)).write_bytes(b"not a zip")
    (w / "src/main/java/com/example/mod/Bad.java").write_bytes(b"// @SubscribeEvent \xff\xfe\n")
    rc, md, work = gates(w, f"{kind}-crash")
    (w / "src/main/java/com/example/mod/Bad.java").unlink()
    crashed = all("Traceback" in (work / f"{t}.log").read_text(encoding="utf-8") for t in ("audit-mixin-targets", "audit-event-listeners"))
    (rc == 0 and crashed and "| REPORT |" not in md) or bad.append(f"{kind}: a crashing audit writes no row: {rc} {crashed}\n{md}")
    # S2: a hung classpath resolution and a hung audit are `skipped: timed out after Ns`, never a crash or silence
    FakeGates.qtc = "timeout"
    tree(w, method="hurtGone", event="Stage")
    rc, md, _ = gates(w, f"{kind}-cp-timeout")
    (rc == 0 and f"| {cg.LISTENER_ROW} | REPORT | skipped: timed out after 300s resolving Gradle's compile classpath |" in md
     and f"| {cg.MIXIN_ROW} | REPORT | 1 to read: " in md) or bad.append(f"{kind}: a classpath timeout is a skipped row: {rc}\n{md}")
    FakeGates.qtc, cg.AUDIT_TIMEOUT = True, 0.001
    rc, md, _ = gates(w, f"{kind}-audit-timeout")
    cg.AUDIT_TIMEOUT = 600
    (rc == 0 and f"| {cg.MIXIN_ROW} | REPORT | skipped: timed out after 0.001s |" in md
     and f"| {cg.LISTENER_ROW} | REPORT | skipped: timed out after 0.001s |" in md) or \
        bad.append(f"{kind}: an audit run that times out is a skipped row: {rc}\n{md}")
    # S4a: the plumbing raising writes no row and leaves every other row as it was
    FakeGates.qtc = "raise"
    rc, md, _ = gates(w, f"{kind}-cp-raise")
    FakeGates.qtc = True
    real_load2 = cg._load
    cg._load = lambda n, f: (_ for _ in ()).throw(RuntimeError("amt broke")) if n == "amt" else real_load2(n, f)
    rc2, md2, _ = gates(w, f"{kind}-amt-raise")
    cg._load = real_load2
    _rc, base, _ = gates(w, f"{kind}-raise-base", reverted=True)
    strip = lambda t, lab: "\n".join(l for l in t.splitlines() if not l.startswith(f"| {lab} |"))
    (rc == rc2 == 0 and f"| {cg.LISTENER_ROW} |" not in md and f"| {cg.MIXIN_ROW} | REPORT | 1 to read: " in md
     and strip(md, cg.MIXIN_ROW) == strip(base, cg.MIXIN_ROW) and f"| {cg.MIXIN_ROW} |" not in md2 and f"| {cg.LISTENER_ROW} | REPORT | 1 to read: " in md2
     and strip(md2, cg.LISTENER_ROW) == strip(base, cg.MIXIN_ROW)) or bad.append(f"{kind}: an audit's plumbing raising writes no row, gates unaffected: {rc} {rc2}\n{md}\n{md2}")
    # S4b: the skipped row quotes Gradle's own reason
    FakeGates.qtc = "wrong"
    rc, md, _ = gates(w, f"{kind}-cp-wrong")
    FakeGates.qtc = True
    (f"| {cg.LISTENER_ROW} | REPORT | skipped: Gradle printed no compile classpath: Could not resolve all files for "
     "configuration ':compileClasspath'. (qtc-classpath.log) |" in md) or bad.append(f"{kind}: the skipped row names Gradle's reason:\n{md}")
    # S3: mixins in another source set, in a config-named package outside mixin/, and in Kotlin
    tree(w)
    cm = w / "src/client/java/com/example/mod/mixin/ClientDragonMixin.java"
    cm.parent.mkdir(parents=True, exist_ok=True)
    cm.write_text((MIXIN % "hurtGone").replace("class DragonMixin", "class ClientDragonMixin"), encoding="utf-8")
    hk = w / "src/main/java/com/example/mod/hooks/HookedMixin.java"
    hk.parent.mkdir(parents=True, exist_ok=True)
    hk.write_text((MIXIN % "hurt").replace("package com.example.mod.mixin", "package com.example.mod.hooks")
                  .replace("class DragonMixin", "class HookedMixin"), encoding="utf-8")
    kt = w / "src/main/kotlin/com/example/mod/hooks/KtMixin.kt"
    kt.parent.mkdir(parents=True, exist_ok=True); kt.write_text("package com.example.mod.hooks\n", encoding="utf-8")
    (w / "src/main/resources/examplemod.hooks.mixins.json").write_text(
        '{"package":"com.example.mod.hooks","mixins":["HookedMixin","KtMixin"]}', encoding="utf-8")
    (w / "src/main/resources/examplemod.nested.mixins.json").write_text(      # Outer$Inner is in Outer's file
        '{"package":"com.example.mod.mixin","mixins":["DragonMixin$Inner"]}', encoding="utf-8")
    G(w, "add", "-A"); G(w, "commit", "-qm", "more mixins")
    rc, md, _ = gates(w, f"{kind}-scope")
    rows = [l for l in md.splitlines() if l.startswith(f"| {cg.MIXIN_ROW} |")]
    (rc == 0 and len(rows) == 2
     and rows[0] == f"| {cg.MIXIN_ROW} | REPORT | 1 to read: src/client/java/com/example/mod/mixin/ClientDragonMixin.java "
                    "@Inject hurtGone: mixin: no such method in vanilla |"
     and "skipped: 2 configured mixin(s) the audit cannot read: com.example.mod.hooks.HookedMixin (not in a mixin/ or mixins/ "
         "package), com.example.mod.hooks.KtMixin (Kotlin)" in rows[1]) or bad.append(f"{kind}: every source root's mixins are audited and "
                                                                                       f"the configured ones it cannot read are named: {rc}\n{md}")
    for f in (cm, hk, kt, w / "src/main/resources/examplemod.hooks.mixins.json", w / "src/main/resources/examplemod.nested.mixins.json"):
        f.unlink()
    tree(w, mixin=False, hook=False)                                   # nothing to check: neither runs, no Gradle call
    shutil.rmtree(w / "src/main/java/com/example/mod/mixin", ignore_errors=True)
    for f in (w / "src/main/resources").glob("*.mixins.json"):        # and no config lists a mixin
        f.write_text('{"package":"com.example.mod.mixin","mixins":[]}', encoding="utf-8")
    G(w, "add", "-A"); G(w, "commit", "-qm", "no mixins", "--allow-empty")
    rc, md, work = gates(w, f"{kind}-none")
    (rc == 0 and "| REPORT |" not in md and not any("qtcClasspath" in c for c in calls)
     and not (work / "audit-mixin-targets.log").exists()) or bad.append(f"{kind}: a tree with no mixin and no listener runs neither audit: {calls}\n{md}")
print("\n".join(bad) or "OK")
PY
out="$(python3 "$T/test-port-tools-heredoc-8.py" "$T/u2" 2>&1)"
[ "$out" = "OK" ] && ok "#161 U2 on a single-branch clone of the head (single-version) and version branch 1.21.4: ci-gates' mixin-target and event-listener audits write one REPORT row each for a stale target and an abstract-event listener (never red), \`skipped: <why>\` with no sources jar or no classpath, no row when the audit crashes, and neither runs (no Gradle call) with nothing to check; a hung classpath resolution or audit is \`skipped: timed out after Ns\`; the classpath or sources-jar plumbing raising writes no row and leaves every other row as it was; a skipped row quotes Gradle's own reason; a mixin in src/client/java is audited and configured mixins it cannot read (outside mixin/, Kotlin) are named, a nested Outer\$Inner read in Outer's file; a clean tree's GATES.md is byte-identical to the one written without them" \
  || bad "#161 U2: $out"
else
  echo "  SKIP  #161 U2 ci-gates audit rows (no javac/jar)"
fi
# ---- #161 U5 (C2, C3, B1): the done list on a single-version fork -- on a single-branch clone of the frozen fixture's
# head (single-version): its records in .port/<mc>/, judged against the review base; the version-branch fixture alongside
echo "#161 U5: the done check, the library pre-release mark and the library marker on a single-version fork"
cat > "$T/test-port-tools-heredoc-u5.py" <<'PY'   # written out, then run: macOS bash 3.2 misparses quotes in a heredoc inside $( )
import contextlib, importlib.util, io, pathlib, subprocess, sys
d = pathlib.Path(sys.argv[1]); d.mkdir()
L = lambda n, f: (lambda s: (s.loader.exec_module(m := importlib.util.module_from_spec(s)), m)[1])(importlib.util.spec_from_file_location(n, f"tools/{f}"))
fb, cg, fk, pc, pp = (L("fb", "fork-branches.py"), L("cg", "ci-gates.py"), L("fk", "fork-kind.py"), L("pc", "port-ci.py"),
                      L("pp", "port-publish.py"))
bad = []
G = lambda w, *a: subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *a],
                                 capture_output=True, text=True, check=True).stdout.strip()
r, g, _ = fb._migrated_fixture(d)                     # the version-branch fork
vb_before = {v: fb.target_done(r, v) for v in ("1.21.4", "1.21.5")}
sv = d / "sv"
subprocess.run(["git", "clone", "-q", "--single-branch", "-b", "neoforge-1.21.1", str(r), str(sv)], check=True, capture_output=True)
G(sv, "config", "user.name", "t"); G(sv, "config", "user.email", "t@example.invalid")
(fk.detect(sv)[0] == "single-version" and fb.sv_version(sv, sv) == "1.21.1") or bad.append(f"the head's clone is single-version: {fk.detect(sv)}")
mc = "1.21.1"; rec = sv / ".port" / mc
def done(**k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fb.check_done(sv, mc, "t", **k)
    return rc, buf.getvalue()
# no base yet: not done, and it says how to start
rc, o = done()
(rc == 18 and fb.SV_NO_BASE in o and not rec.exists()) or bad.append(f"C2: no review base: not done, nothing written: {rc} {o}")
java = sv / "src/main/java/com/example/mod/Listen.java"
java.write_text("package com.example.mod;\npublic class Listen {\n  @SubscribeEvent\n  public static void onTick(Object e) {}\n}\n",
                encoding="utf-8")
G(sv, "add", "-A"); G(sv, "commit", "-qm", "a listener")
base = G(sv, "rev-parse", "HEAD")
jrel = java.relative_to(sv).as_posix()
text0 = java.read_text(encoding="utf-8")
# the round's change: an edit that drops a listener, and an added class
java.write_text(text0.replace("@SubscribeEvent", "// listener removed", 1), encoding="utf-8")
(java.parent / "Added.java").write_text("package x; class Added {}\n", encoding="utf-8")
G(sv, "add", "-A"); G(sv, "commit", "-qm", "the change")
rc, o = done(base=base[:10])
rv = (rec / "REVIEW.md").read_text(encoding="utf-8") if (rec / "REVIEW.md").is_file() else ""
added = (java.parent / "Added.java").relative_to(sv).as_posix()
(rc == 18 and f"- base: {base}" in rv and f"| changed | {jrel} | ? | ? |" in rv and f"| changed | {added} | ? | ? |" in rv
 and "| item | " in rv and (rec / "TEST-ITEMS.md").is_file() and (rec / "INVENTORY-CHECK.json").is_file()
 and "no green Gate C recorded in GATES.md" in o) \
    or bad.append(f"C2: --base records the base, a `?` row per changed or added file and an `item` row for the dropped "
                  f"listener, TEST-ITEMS.md and INVENTORY-CHECK.json; GATES.md missing: {rc} {o} {rv}")
# the gates (ci-gates' own header) and the review filled: done; an export of the commit agrees (a release reads that)
h = G(sv, "rev-parse", "--short", "HEAD")
(rec / "GATES.md").write_text(cg.summary_head(sv, "examplemod", None, None, "full") + "\n\n| check | result | detail |\n"
                              "|---|---|---|\n| Gate C -- real client, `launch` | PASS | ok |\n", encoding="utf-8")
rv = (rec / "REVIEW.md").read_text(encoding="utf-8")
(rec / "REVIEW.md").write_text(rv.replace("- reviewer: ?", "- reviewer: fresh").replace("- date: ?", "- date: 2026-10-28")
                               .replace("- commit: ?", f"- commit: {h}").replace("| ? | ? |", "| ok | intended: the listener moved away |"),
                               encoding="utf-8")
done()                                                # the round's records step: the inventory account the review settled
G(sv, "add", "-A"); G(sv, "commit", "-qm", "records")
rc, o = done()
c = G(sv, "rev-parse", "HEAD")
import tempfile
with tempfile.TemporaryDirectory() as t:
    exp = fb.target_done(sv, mc, root=fb.export(sv, c, pathlib.Path(t) / "x"), ref=c)
(rc == 0 and "done" in o and exp == [] and not G(sv, "status", "--porcelain")) \
    or bad.append(f"C2: gates and the review recorded: done, the export agrees, nothing rewritten: {rc} {o} {exp}")
# a code change after them stales the gates and the review
java.write_text(java.read_text(encoding="utf-8") + "\n// later\n", encoding="utf-8")
G(sv, "add", "-A"); G(sv, "commit", "-qm", "later")
why = fb.target_done(sv, mc)
(any(w.startswith("GATES.md 1 file(s) changed since") for w in why) and any(w.startswith("REVIEW.md 1 file(s) changed since") for w in why)) \
    or bad.append(f"C2: an edit after the records stales the gates and the review: {why}")
# a new round's base starts REVIEW.md afresh: the old verdicts were about another change
nb = G(sv, "rev-parse", "HEAD")
rc, o = done(base=nb)
rv = (rec / "REVIEW.md").read_text(encoding="utf-8")
(f"- base: {nb}" in rv and "- reviewer: ?" in rv and "| ok |" not in rv) or bad.append(f"C2: a new base starts afresh: {rv}")
G(sv, "checkout", "-q", "--", "."); G(sv, "clean", "-qfd", ".port")
# C3: the library pre-release mark in .port/<mc>/DEPS.md at the tag refuses promotion and publishing, as on a version branch
rec.mkdir(parents=True, exist_ok=True)
(rec / "DEPS.md").write_text(f"{fb.LIBRARY_PRERELEASE_MARK} `owner/examplelib` is pinned to examplelib-1.0-mc1.21.1\n", encoding="utf-8")
G(sv, "add", "-A"); G(sv, "commit", "-qm", "pin"); G(sv, "tag", "examplemod-1.0-mc1.21.1")
why = fb.target_done(sv, mc)
read = lambda p: subprocess.run(["git", "-C", str(sv), "show", f"examplemod-1.0-mc1.21.1:{p}"], capture_output=True, text=True).stdout or None
pp.gh = lambda *a, **k: {}
pub = pp.gate(str(sv), {"permission": "https://example.invalid/agreed", "auto": True}, "examplemod-1.0-mc1.21.1",
              {"prerelease": False, "draft": False, "author": {"login": "github-actions[bot]"}})
(fb.SV_PRERELEASE in why and len(pc.prerelease_pin_blockers("examplemod-1.0-mc1.21.1", read)) == 1
 and any("library pre-release" in w for w in pub)) \
    or bad.append(f"C3: the mark: not done, promotion and publishing refuse the tag: {why} {pub}")
# B1: the library marker in .port/<mc>/port.properties: spawn and battle skipped
(not cg.is_library(sv, None)) or bad.append("B1: unmarked")
(rec / "port.properties").write_text("library=true\n", encoding="utf-8")
(cg.is_library(sv, None) and cg.library_phases(["launch", "spawn", "battle"], True)[0] == ["launch"]) or bad.append("B1: marked")
# --base on a version branch's checkout is refused; the version-branch fixture's done lists as before
g("checkout", "-q", "neoforge-1.21.4")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = fb.check_done(r, "1.21.4", "t", base="HEAD")
(rc == 2 and "--base is for a single-version" in buf.getvalue()) or bad.append(f"--base on a version branch: {rc} {buf.getvalue()}")
g("checkout", "-q", "neoforge-1.21.1")
{v: fb.target_done(r, v) for v in vb_before} == vb_before or bad.append("control: the version branches' done lists changed")
print("\n".join(bad) or "OK")
PY
out="$(python3 "$T/test-port-tools-heredoc-u5.py" "$T/u5" 2>&1)"
[ "$out" = "OK" ] && ok "#161 U5 on a single-branch clone of the head (single-version) and the version-branch fixture: fork-branches.py check-done --base records the review base in .port/<mc>/REVIEW.md and writes a \`?\` row per changed or added file and an \`item\` row for a dropped listener, TEST-ITEMS.md and INVENTORY-CHECK.json; with GATES.md (ci-gates' header names the version) and the review filled the version is done, on the checkout and on an export of the commit; a later edit stales the gates and the review; a new base starts REVIEW.md afresh; DEPS.md's library pre-release mark makes it not done and promotion and publishing refuse the tag; .port/<mc>/port.properties library=true drops spawn and battle; --base is refused on a version branch, whose done lists are unchanged" \
  || bad "#161 U5: $out"
# ---- #161 U6 (C5): the authors' data files merged in an older version's form are put in the version's forms after the
# merge -- port-sync on a single-version fork (a single-branch clone of the fixture's head, made a 26.2 port), the
# cascade's merge-up on the version-branch fixture; each converted once, its own commit, and a second run commits nothing
echo "#161 U6: data forms after the authors' merge -- a single-version fork and the version-branch cascade"
out="$(python3 - "$T/u6" <<'PY' 2>&1
import importlib.util, json, pathlib, subprocess, sys
d = pathlib.Path(sys.argv[1]); d.mkdir()
L = lambda n, f: (lambda s: (s.loader.exec_module(m := importlib.util.module_from_spec(s)), m)[1])(importlib.util.spec_from_file_location(n, f"tools/{f}"))
fb, ps, fk, fp = L("fb", "fork-branches.py"), L("ps", "port-sync.py"), L("fk", "fork-kind.py"), L("fp", "fork-port.py")
bad = []
G = lambda w, *a: subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *a],
                                 capture_output=True, text=True, check=True).stdout.strip()
RECIPE = "src/main/resources/data/examplemod/recipe/probe.json"
ADV = "src/main/resources/data/examplemod/advancement/probe.json"
old_recipe = {"type": "minecraft:crafting_shaped", "pattern": ["ab"],
              "key": {"a": {"item": "minecraft:stick"}, "b": {"tag": "c:ingots/iron"}}, "result": {"id": "minecraft:diamond", "count": 1}}
old_adv = {"criteria": {"k": {"trigger": "minecraft:player_killed_entity", "conditions": {"entity": {"type": "minecraft:zombie"}}}},
           "display": {"icon": {"item": "minecraft:diamond"}, "title": "t", "description": "d"}}
def put(w, rel, doc):
    (w / rel).parent.mkdir(parents=True, exist_ok=True); (w / rel).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
rj = lambda w, rel: json.loads((w / rel).read_text(encoding="utf-8"))
r, g, _ = fb._migrated_fixture(d)                     # the version-branch fork
# --- single-version: the head's clone made a 26.2 port; the authors (1.21.1-era data) merged by port-sync
sv = d / "sv"
subprocess.run(["git", "clone", "-q", "--single-branch", "-b", "neoforge-1.21.1", str(r), str(sv)], check=True, capture_output=True)
G(sv, "config", "user.name", "t"); G(sv, "config", "user.email", "t@example.invalid")
gp = sv / "gradle.properties"
gp.write_text(gp.read_text(encoding="utf-8").replace("minecraft_version=1.21.1", "minecraft_version=26.2"), encoding="utf-8")
G(sv, "commit", "-qam", "the port at 26.2")
put(sv, "src/main/resources/data/examplemod/recipe/ours.json",           # the port's own file a converter refuses:
    {"type": "minecraft:crafting_shaped", "pattern": ["a"], "key": {"a": {"item": "minecraft:stick", "count": 2}},
     "result": {"id": "minecraft:stick", "count": 1}})                  # no merge brought it, so never reported
G(sv, "add", "-A"); G(sv, "commit", "-qm", "the port's own recipe")
fk.detect(sv)[0] == "single-version" or bad.append(f"the clone is single-version: {fk.detect(sv)}")
up = d / "authors"
subprocess.run(["git", "clone", "-q", "--single-branch", "-b", "neoforge-1.21.1", str(r), str(up)], check=True, capture_output=True)
put(up, RECIPE, old_recipe); put(up, ADV, old_adv); G(up, "add", "-A"); G(up, "commit", "-qm", "authors: a recipe and an advancement")
G(sv, "remote", "add", "authors", str(up))
log = []; rc = ps.sync(sv, "neoforge-1.21.1", "authors/neoforge-1.21.1", out=log.append)
subj = G(sv, "log", "-1", "--format=%s")
parents = G(sv, "log", "-1", "--format=%P", "HEAD^").split()
(rc == 0 and subj == "data forms for 26.2 after the authors' merge" and len(parents) == 2) \
    or bad.append(f"single-version: the merge, then its data forms as their own commit: {rc} {subj!r} {log}")
rec_, adv = rj(sv, RECIPE), rj(sv, ADV)
rec_["key"] == {"a": "minecraft:stick", "b": "#c:ingots/iron"} or bad.append(f"single-version: the recipe's ingredients in 1.21.2+'s form: {rec_['key']}")
adv["criteria"]["k"]["conditions"]["entity"] == {"entity_type": "minecraft:zombie"} \
    or bad.append(f"single-version: the advancement's entity predicate in 26.2's form: {adv['criteria']['k']['conditions']}")
adv["display"]["icon"] == {"id": "minecraft:diamond"} or bad.append(f"single-version: the icon in 1.21's form (the layout step): {adv['display']['icon']}")
h = G(sv, "rev-parse", "HEAD"); log = []
ps.sync(sv, "neoforge-1.21.1", "authors/neoforge-1.21.1", out=log.append)          # nothing new: data forms again
ps.data_forms(sv, log.append)
(G(sv, "rev-parse", "HEAD") == h and not G(sv, "status", "--porcelain")) \
    or bad.append(f"single-version: a second run changes nothing: {log}")
not (sv / ".port/26.2/DATA-FORMS.md").exists() or bad.append("single-version: no report when nothing is refused")
# a refused file the merge brought in is listed in .port/<mc>/DATA-FORMS.md; a re-run keeps it as it was
weird = "src/main/resources/data/examplemod/recipe/weird.json"
put(up, weird, {"type": "minecraft:crafting_shaped", "pattern": ["a"], "key": {"a": {"item": "minecraft:stick", "count": 2}},
                "result": {"id": "minecraft:stick", "count": 1}})
G(up, "add", "-A"); G(up, "commit", "-qm", "authors: an ingredient beside other keys")
log = []; ps.sync(sv, "neoforge-1.21.1", "authors/neoforge-1.21.1", out=log.append)
rep = sv / ".port/26.2/DATA-FORMS.md"
(rep.is_file() and "recipe/weird.json" in rep.read_text(encoding="utf-8") and "ours.json" not in rep.read_text(encoding="utf-8")) \
    or bad.append(f"single-version: the merged file's refusal listed, the port's own not: {log}")
h = G(sv, "rev-parse", "HEAD"); ps.data_forms(sv, log.append)
G(sv, "rev-parse", "HEAD") == h or bad.append("single-version: a re-run with a refusal commits nothing")
# --- version branches: the cascade's merge-up converts what the head (1.21.1) brought up, at 1.21.3/.4/.5
fb._authors(r, g, {RECIPE: json.dumps(old_recipe, indent=2) + "\n"}, "authors: a recipe")
rc, o = fb._quiet(fb.cascade, r, "port..1.21.5", "none")
rc == 0 or bad.append(f"version branches: the cascade ran: {rc} {o[-600:]}")
show = lambda ref: json.loads(subprocess.run(["git", "-C", str(r), "show", ref], capture_output=True, text=True).stdout or "null")
head_rec = show(f"neoforge-1.21.1:{RECIPE}") or {"key": {}}
head_rec["key"]["a"] == {"item": "minecraft:stick"} or bad.append("version branches: the head (1.21.1) keeps the authors' 1.21.1 form")
for v in ("1.21.3", "1.21.4", "1.21.5"):
    b = f"neoforge-{v}"
    (show(f"{b}:{RECIPE}") or {}).get("key") == {"a": "minecraft:stick", "b": "#c:ingots/iron"} \
        or bad.append(f"version branches: {v}'s recipe in 1.21.2+'s form")
subj = lambda b, n=1: G(r, "log", f"-{n}", "--format=%s", b).splitlines()
subj("neoforge-1.21.3") == ["data forms for 1.21.3 after the authors' merge"] \
    or bad.append(f"version branches: 1.21.3's data forms its own commit after the merge-up: {subj('neoforge-1.21.3')}")
all(s_.startswith("Merge neoforge-") for v in ("1.21.4", "1.21.5") for s_ in subj(f"neoforge-{v}")) \
    or bad.append("version branches: 1.21.4/.5 take the converted file in the merge-up, no data forms commit of their own")
all(fb.remerge_entries(fb.ref_files(r, fb.resolve_branch(r, f"neoforge-{v}")).get(f".port/{v}/REMERGE.json", b"")) == []
    for v in ("1.21.4", "1.21.5")) or bad.append("version branches: the converted recipe counts as the authors' change taken")
w4, _w = fb.worktree_for(r, "1.21.4")
h = G(w4, "rev-parse", "HEAD")
fp.data_forms_after_merge(w4, "1.21.4", say=lambda x: None)[2] is None and G(w4, "rev-parse", "HEAD") == h \
    or bad.append("version branches: a second run on 1.21.4 commits nothing")
rc, o = fb._quiet(fb.cascade, r, "port..1.21.5", "none")
(rc == 0 and "data forms" not in o) or bad.append(f"version branches: a second cascade, nothing new: no data forms: {o[-400:]}")
print("\n".join(bad) or "OK")
PY
)"
[ "$out" = "OK" ] && ok "#161 U6 on a single-branch clone of the head made a 26.2 port (single-version) and on the version-branch fixture: the authors' 1.21.1-form recipe and advancement merged by port-sync come out in 26.2's forms (string ingredients, the entity_type sub-predicate, the 1.21 icon) as their own commit after the merge, a refused file listed in .port/26.2/DATA-FORMS.md, and a second run commits nothing; the cascade puts the authors' recipe in 1.21.2+'s form at 1.21.3 (its own commit after the merge-up) and 1.21.4/.5 take it by the merge-up, recorded as the authors' change taken (no REMERGE entry), the head keeping the authors' form, and a second run or cascade commits nothing" \
  || bad "#161 U6: $out"
echo "audit-event-listeners.py: abstract events (CATALOG X33), @SubscribeEvent and method-reference listeners, --cp-file/--tree (fork) mode"
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
grep -q 'self-check: OK' <<<"$out" && ok "shipped-gate: expectation parsing, log matching, JDK pick (JAVA_HOME first, then JAVA_HOME_<n>, /usr/lib/jvm, /opt, the Gradle toolchain downloads; java.exe too) and crash-cause self-check; the expectations file (single-version with none: none, as before; version branch .port/<mc>/shipped-expect-cf.txt for _cf, the unsuffixed one for _mr), a variant missing its expected line fails; boot() on a stand-in server, no Minecraft: clean passes, a crash after Done fails, an exit before stop fails" || bad "shipped-gate self-check: $out"
python3 tools/shipped-gate.py --jar "$T/nope.jar" >/dev/null 2>&1; [ $? = 2 ] && ok "shipped-gate: a jar that does not exist exits 2" || bad "shipped-gate missing-jar exit"
printf 'x' > "$T/notjar.jar"; python3 tools/shipped-gate.py --jar "$T/notjar.jar" --neo 21.1.228 >/dev/null 2>&1; [ $? = 2 ] && ok "shipped-gate: a file that is not a jar exits 2" || bad "shipped-gate non-jar exit"
python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1],'w').writestr('a','b')" "$T/ok.jar"
python3 tools/shipped-gate.py --jar "$T/ok.jar" --neo 21.1.228 --expect-log 'bad/path:x' >/dev/null 2>&1; [ $? = 2 ] && ok "shipped-gate: a malformed --expect-log exits 2 before anything is downloaded" || bad "shipped-gate bad expectation exit"
# one run per checkout (tools/checkout-lock.py): a live holder refuses a second run on a version branch's checkout
# (exit 14); a single-version run takes no lock at all
LK="$T/lk"; mkdir -p "$LK"; git -C "$LK" init -q -b neoforge-1.21.1; git -C "$LK" config user.email a@b; git -C "$LK" config user.name a
printf 'minecraft_version=1.21.1\n' > "$LK/gradle.properties"; git -C "$LK" add -A; git -C "$LK" commit -qm base
git -C "$LK" checkout -qb neoforge-1.21.4; mkdir -p "$LK/.port/1.21.4" "$LK/build"
printf 'parent=neoforge-1.21.1\n' > "$LK/.port/1.21.4/branch.properties"; printf 'minecraft_version=1.21.4\n' > "$LK/gradle.properties"
git -C "$LK" add -A; git -C "$LK" commit -qm 1.21.4
# the holder is a Python process writing its OWN pid: under Git Bash on Windows `$!` is an MSYS pid, not the native pid
# the lock code checks, so a shell-written pid reads as dead there
python3 -c 'import json,os,sys,time; open(sys.argv[1],"w",encoding="utf-8").write(json.dumps({"pid": os.getpid(), "tool": "fork-port.py", "target": "1.21.3", "started": "2026-01-01T00:00:00Z"})); time.sleep(30)' "$LK/build/.fork-port.lock" & HOLDJOB=$!
for _ in $(seq 50); do [ -s "$LK/build/.fork-port.lock" ] && break; sleep 0.1; done
HOLD="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1],encoding="utf-8"))["pid"])' "$LK/build/.fork-port.lock")"
out="$(python3 tools/ci-gates.py --repo "$LK" --modid m --mc 1.21.4 --work-dir "$T/lkw" 2>&1)"; code=$?
[ $code = 14 ] && grep -q "$HOLD" <<<"$out" && ok "ci-gates --mc on a version branch: a second run on a busy checkout is refused (exit 14) naming the holder" || bad "ci-gates lock exit $code: $out"
out="$(python3 tools/fork-port.py --repo "$LK" --target 1.21.4 2>&1)"; code=$?
[ $code = 14 ] && ok "fork-port: a second run on a busy checkout is refused (exit 14)" || bad "fork-port lock exit $code: $out"
SVL="$T/lk-single"; mkdir -p "$SVL/build"; cp "$LK/build/.fork-port.lock" "$SVL/build/"
python3 tools/ci-gates.py --repo "$SVL" --modid m --work-dir "$T/lkw2" >/dev/null 2>&1; code=$?
[ $code != 14 ] && grep -q "\"pid\": $HOLD" "$SVL/build/.fork-port.lock" && ok "ci-gates without --mc (a single-version fork) ignores the lock entirely" || bad "single-version ci-gates touched the lock (exit $code)"
# The real Gate A (port_gates.gate_a) against a faked Gradle: every other test fakes gate_a itself, so a broken
# reference inside it (a helper removed with the layer route) crashed every fork's gates with nothing failing here.
GA="$(mktemp -d)"; mkdir -p "$GA/src/main/resources/META-INF"
cat > "$T/test-port-tools-heredoc-9.py" <<'PY'   # written out, then run: macOS bash 3.2 misparses quotes in a heredoc inside $( )
import pathlib, sys, types
sys.path.insert(0, "tools"); import port_gates as pg
repo = pathlib.Path(sys.argv[1])
def gradle(r, args, log, **kw):
    d = pathlib.Path(r) / "build/test-results/test"; d.mkdir(parents=True, exist_ok=True)
    (d / "TEST-x.xml").write_text('<testsuite tests="3" failures="0" errors="0"/>', encoding="utf-8")
    pathlib.Path(log).write_text("", encoding="utf-8"); return types.SimpleNamespace(returncode=0)
pg.gradle = gradle; pg.gate_env = lambda c: {"PORT_GRADLE_INIT": "x"}
print("ran", pg.gate_a({"repo": repo}, str(repo / "a.log")))
PY
out="$(python3 "$T/test-port-tools-heredoc-9.py" "$GA" 2>&1)"
[ "$out" = "ran 3" ] && ok "port_gates.gate_a itself (Gradle faked): reads build/test-results/test and counts the tests run" \
  || bad "port_gates.gate_a: $out"
rm -rf "$GA"
kill $HOLDJOB 2>/dev/null; wait $HOLDJOB 2>/dev/null
# the holder is gone: its lock is stale -- a real fork-port run on the version branch takes it over and releases it when
# it ends (it stops at setup here: no Gradle project, exit 10 or 2)
out="$(python3 tools/fork-port.py --repo "$LK" --target 1.21.4 --gates none 2>&1)"; code=$?
{ [ $code = 10 ] || [ $code = 2 ]; } && ! grep -q 'another fork-port.py is using this checkout' <<<"$out" && [ ! -e "$LK/build/.fork-port.lock" ] \
  && ok "a dead holder's lock is stale: taken over, and released when the run ends" || bad "stale lock exit $code: $out"

out="$(python3 tools/test-port-publish.py 2>&1)"
grep -q 'test-port-publish: OK' <<<"$out" && ok "publish-port: single-version output and API calls byte-identical to the recorded golden; --platform mr|cf, and a cf-only S1 in the ledger publishes to Modrinth and holds CurseForge back by name (a version branch reads the head branch's ledger; an unreadable ledger holds both)" || bad "test-port-publish: $out"
out="$(python3 tools/fix-ledger.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "fix-ledger: a ledger renders and validates; a legacy layer section is kept in the JSON and not rendered" || bad "fix-ledger self-check: $out"
out="$(python3 tools/port-ci.py --self-check 2>&1)"
grep -q 'self-check: OK' <<<"$out" && ok "port-ci: without --variant-jar/--dep-jar-variant the workflow is byte-identical to tools/golden (with and without dependencies); infer drops a removed layer branch's trigger; --variant-jar adds a shipped-jars job the release needs (its script run on a fake fork: one build, each jar booted with its own .port/<mc>/shipped-expect[-<variant>].txt as --expect-file or none, a failing or unmatched variant named); --dep-jar-variant adds a sha256-pinned gates (full, <label>) leg and refuses a mismatched spec; args reads every option back; promote refreshes the install manifest on the port branch only (its script run on a fake fork: pushed on top of the tag, retried when the branch moved, nothing pushed when unchanged, a bad tag refused); a version branch's push trigger also paths-ignores its round records (.port/*.md, .port/*/*.md -- not branch.properties, the rows, STUBS.json or src/), rendered through main() on a real version-branch checkout, while a single-version checkout renders the golden's line byte for byte" || bad "port-ci self-check: $out"
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
sed -i.bak 's/CallbackInfoReturnable<Vec>/CallbackInfoReturnable<Data>/' "$MO/src/main/java/com/example/mod/mixin/FogMixin.java" && rm -f "$MO/src/main/java/com/example/mod/mixin/FogMixin.java.bak"   # -i.bak: BSD (macOS) sed and GNU sed both take it
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
sed -i.bak 's#Lnet/x/state/Cam;#Lnet/x/state/level/Cam;#' "$MP/src/main/java/com/example/mod/mixin/RendMixin.java" && rm -f "$MP/src/main/java/com/example/mod/mixin/RendMixin.java.bak"   # -i.bak: BSD (macOS) sed and GNU sed both take it
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
python3 - <<'PY' && ok "shared helpers: a REQUIRES-gated helper is skipped in a fork without the library, installed in one with it" || bad "a REQUIRES-gated shared helper reached a fork without its library (or missed one with it)"
import importlib.util, pathlib, sys, tempfile
sys.argv = ["x"]
spec = importlib.util.spec_from_file_location("fb", "tools/fork-branches.py"); fb = importlib.util.module_from_spec(spec); spec.loader.exec_module(fb)
sh = pathlib.Path(tempfile.mkdtemp()); (sh / "9.9/java").mkdir(parents=True); fb.shared_dir = lambda: sh
(sh / "9.9/java/Needs.java").write_text("// OVERLAY-ONLY\n// REQUIRES: geckolib\npackage @MVPKG@;\nclass Needs {}\n", encoding="utf-8")
(sh / "9.9/java/Plain.java").write_text("// OVERLAY-ONLY\npackage @MVPKG@;\nclass Plain {}\n", encoding="utf-8")
names = lambda build: sorted(k.rsplit("/", 1)[1] for k in fb.shared_helpers_for(["9.9"], "com.example.mod.mv", "examplemod", build))
without = names("dependencies {}\n")
with_ = names('dependencies { implementation "com.geckolib:geckolib-neoforge-1.21.11:5.4.5" }\n')
sys.exit(0 if without == ["Plain.java"] and with_ == ["Needs.java", "Plain.java"] else 1)
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
# takes it. Version-branch forks only: a single-version fork never reads these tables.
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
# a helper is copied only when its FQN or an `MV-REQUIRES: Name` line is in the tree (fork-branches used_helpers); a
# helper naming another by its simple name (same package) needs the tag, or a mod using one helper gets a set that does
# not compile
python3 - <<'PY' && ok "shared helpers: for every version and every helper used alone, the copied set is closed under simple-name references (MV-REQUIRES tags complete)" || bad "a shared helper names another by simple name without an MV-REQUIRES tag"
import importlib.util, pathlib, re, sys
sys.argv = ["x"]
spec = importlib.util.spec_from_file_location("fb", "tools/fork-branches.py")
fb = importlib.util.module_from_spec(spec); spec.loader.exec_module(fb)
key = lambda v: tuple(int(x) for x in v.split("."))
src = pathlib.Path("templates/fork-layer/shared")
allv = sorted([d.name for d in src.iterdir() if (d / "java").is_dir()], key=key)
PKG = "com.example.mod.mv"
every = " ".join(sorted({t for f in src.glob("*/java/*.java")
                         for t in re.findall(r"(?m)^// REQUIRES: (\S+)", f.read_text(encoding="utf-8"))}))
def strip(t):
    t = re.sub(r"//[^\n]*|/\*.*?\*/", "", t, flags=re.S); t = re.sub(r'"(?:\\.|[^"\\])*"', '""', t)
    return re.sub(r"^import [^\n]*\n", "", t, flags=re.M)
problems = []
for target in allv:
    vers = [v for v in reversed(allv) if key(v) <= key(target)]
    pool = fb.shared_helpers_for(vers, PKG, "examplemod", every)
    names = sorted({rel.rsplit("/", 1)[1][:-5] for rel in pool})
    for seed in names:
        got = {rel.rsplit("/", 1)[1][:-5]: t for rel, t in fb.used_helpers(pool, [f"package com.example.mod;\nclass Use {{ {PKG}.{seed} x; }}\n"]).items()}
        for n, t in got.items():
            for o in set(names) - set(got):
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
python3 - <<'PY' && ok "shared 1.21.5-26.3 render helpers (every version with LegacyRenderType): a LegacyRenderType's setupRenderState loads its shader (only a ShaderInstance / Vanilla), blend / depth / write-mask / cull / bias state and output target into LegacyGl (so setupRenderState + drawWithShader draws as the type does) on ONE stack for all types, and clearRenderState pops to its own entry (non-nested clears end with nothing loaded, bias and target default once empty) restoring the shader too; a throwing shader shard leaves nothing loaded; draw() runs the shader shard once; 1.21.5's draw calls the shader's apply() before binding its uniforms and samplers, keeps no COLOR_MODULATOR written inside apply() (as on 1.21.1) and calls clear() after" || bad "shared 1.21.5-26.3 render helpers: a set-up LegacyRenderType no longer reaches drawWithShader, or 1.21.5's draw lost apply()"
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
# every shared version that carries LegacyRenderType (1.21.11+ have no RenderStateShard: the shards run as Shard)
lrts = sorted(p.parent.parent.name for p in pathlib.Path("templates/fork-layer/shared").glob("*/java/LegacyRenderType.java"))
need(set(lrts) >= {"1.21.5", "1.21.8", "1.21.10", "1.21.11", "26.1.2", "26.2", "26.3"}, f"shared LegacyRenderType versions: {lrts}")
for v in lrts:
    d = pathlib.Path("templates/fork-layer/shared") / v / "java"
    gl = strip((d / "LegacyGl.java").read_text(encoding="utf-8"))
    lrt = strip((d / "LegacyRenderType.java").read_text(encoding="utf-8"))
    # the type's own (1.21.11+'s Shard / Modifier / texture shards declare the same methods first)
    own = lambda head: next((b for b in (body(lrt[m.start():], head) for m in re.finditer(head, lrt)) if b and "state." in b), None)
    setup = own(r"public void setupRenderState\(\)")
    clear = own(r"public void clearRenderState\(\)")
    draw = body(lrt, r"public void draw\(MeshData mesh\)")
    shards_up = r"(?:super\.setupRenderState\(\);|state\.real\.forEach\(Shard::setupRenderState\);)"
    shards_down = r"(?:super\.clearRenderState\(\);|state\.real\.forEach\(Shard::clearRenderState\);)"
    need(setup and re.search(shards_up + r".*LegacyGl\.load\(this, state\.key, state\.shader\.shader\(\), getRenderTarget\(\)\);\s*try \{\s*state\.shader\.setupRenderState\(\);\s*\} catch \(RuntimeException \| Error e\) \{\s*LegacyGl\.restore\(this\);\s*throw e;", setup, re.S),
         f"{v}: LegacyRenderType.setupRenderState does not run its shards, load its shader/state into LegacyGl and run the shader shard -- restoring what it loaded when that shard throws")
    need(clear and re.search(r"state\.shader\.clearRenderState\(\);.*LegacyGl\.restore\(this\);.*" + shards_down, clear, re.S),
         f"{v}: LegacyRenderType.clearRenderState does not restore LegacyGl's state")
    need("Deque<LegacyGl.Saved>" not in lrt, f"{v}: LegacyRenderType keeps a save stack of its own (one per type: non-nested clears leak)")
    need(draw and "setupRenderState();" in draw and "state.shader.setupRenderState" not in draw and "clearRenderState();" in draw,
         f"{v}: LegacyRenderType.draw runs the shader shard twice or not inside setup/clear")
    dws = body(gl, r"public static void drawWithShader\(MeshData mesh\)")
    need(dws and re.search(r"draw\(mesh, shader, current\(\), target != null \? target :", dws),
         f"{v}: LegacyGl.drawWithShader ignores a set-up type's output target")
    cur = body(gl, r"static Key current\(\)")
    need(cur and "biasScale, biasConstant" in cur, f"{v}: LegacyGl.current() drops a set-up type's depth bias")
    load = body(gl, r"static Saved load\(Object owner, Key key, Object shaderObj, RenderTarget into\)")
    need(load and all(f in load for f in ("blend =", "depthTest =", "depthWrite =", "writeColor =", "writeAlpha =", "cull =",
                                          "biasScale =", "biasConstant =", "target = into", "LOADED.add(s)")),
         f"{v}: LegacyGl.load does not set every piece of a type's state, or does not push it on the one stack")
    need(load and re.search(r"if \(shaderObj instanceof ShaderInstance \|\| shaderObj instanceof Vanilla\) shader = shaderObj;", load)
         and not re.search(r"(?m)^\s*shader = shaderObj;", load),
         f"{v}: LegacyGl.load sets a shader object it cannot draw with (a type with no shader state must keep setShader's)")
    need(re.search(r"private static final java\.util\.ArrayList<Saved> LOADED", gl), f"{v}: LegacyGl has no single save stack")
    rest = body(gl, r"static void restore\(Object owner\)")
    need(rest and "target = s.target()" in rest and "blend = s.blend()" in rest and "shader = s.shader()" in rest
         and re.search(r"LOADED\.get\(i\)\.owner\(\) != owner", rest) and "LOADED.subList(i, LOADED.size()).clear()" in rest
         and re.search(r"if \(LOADED\.isEmpty\(\)\) \{\s*biasScale = 0;\s*biasConstant = 0;\s*target = null;", rest),
         f"{v}: LegacyGl.restore does not pop to the owner's entry, put back the state and shader, or default the bias / target once empty")
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
fi   # part 3
echo "port-tools self-test${PORT_TOOLS_PART:+ (part $PORT_TOOLS_PART of 3)}: $pass passed, $fail failed"
[ "$fail" = 0 ] || exit 1
