#!/usr/bin/env bash
# Self-test for the port-side helpers: the @Override probe and the catalogue-sweep runner.
set -uo pipefail
cd "$(dirname "$0")/.."
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

echo
echo "port-tools self-test: $pass passed, $fail failed"
[ "$fail" = 0 ] || exit 1
