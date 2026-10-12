#!/usr/bin/env bash
# Boot a folder of mod jars together on a headless NeoForge server and say whether they load.
#
#   tools/boot-mods.sh <mods-dir> [--no-jei] [--neoforge 21.1.x]
#
# This is the check for an install: `install-port.py ... --mods-dir X` then `boot-mods.sh X` proves the
# set the installer picked loads together (FML's "Mod List:" is printed, then the harness GameTest
# passes) before anyone copies it into a real instance. It runs templates/smoke-harness in a temporary
# copy, so nothing in the repository is touched.
#
# --neoforge: boot on that NeoForge (install-port names the one it installs) instead of the harness's own.
# --no-jei: the harness compiles against JEI's API to open recipe screens in client tests. Where that
# maven is unreachable, this swaps the probe for a stub; a server boot never uses it.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
dir="${1:?usage: boot-mods.sh <mods-dir> [--no-jei]}"; shift || true
nojei=0; neo=""
while [ $# -gt 0 ]; do
  case "$1" in --no-jei) nojei=1 ;; --neoforge) neo="$2"; shift ;; *) echo "boot-mods: unknown option $1"; exit 2 ;; esac
  shift
done
jars=$(ls "$dir"/*.jar 2>/dev/null | paste -sd, - || true)
[ -n "$jars" ] || { echo "boot-mods: no jars in $dir"; exit 2; }
work=$(mktemp -d "${TMPDIR:-/tmp}/boot-mods.XXXXXX")
trap 'rm -rf "$work"' EXIT
cp -r "$ROOT/templates/smoke-harness/." "$work/"
[ -n "$neo" ] && sed -i.bak "s/^neo_version=.*/neo_version=$neo/" "$work/gradle.properties"
if [ $nojei = 1 ]; then
  sed -i.bak '/mezz.jei:jei-.*-neoforge-api/d' "$work/build.gradle"
  p=$(find "$work/src" -name JeiRecipeProbe.java)
  pkg=$(sed -n 's/^package \(.*\);/\1/p' "$p")
  cat > "$p" <<EOF
package $pkg;
// boot-mods.sh --no-jei: a server boot never opens a recipe screen
final class JeiRecipeProbe {
    static boolean runtimeReady() { return false; }
    static java.util.Map<String, Integer> countRecipesProducing(java.util.List<String> i) { return java.util.Map.of(); }
    static java.util.List<String> openRecipes(java.util.List<String> u) { return u; }
}
EOF
fi
init=(); [ -f "$ROOT/tools/central-mirror.init.gradle" ] && init=(--init-script "$ROOT/tools/central-mirror.init.gradle")
log="$work/boot.log"
set +e
(cd "$work" && ./gradlew -q runGameTestServer "${init[@]}" -Psmokejars="$jars") > "$log" 2>&1
rc=$?
set -e
awk '/Mod List:/{on=1;next} on && /^[[:space:]]+[^[]*\([a-z0-9_.-]+\)[[:space:]]*$/{sub(/^[[:space:]]*/,"  loaded: ");print;n++;next} on && n {exit}' "$log" || true
if [ $rc = 0 ] && grep -aq 'All [0-9]* required tests passed' "$log"; then
  echo "boot-mods: PASS -- the $(echo "$jars" | tr ',' '\n' | wc -l | tr -d ' ') jar(s) in $dir load together"
  exit 0
fi
cp "$log" "$dir/../boot-mods.log" 2>/dev/null && echo "boot-mods: log kept at $dir/../boot-mods.log"
grep -aE 'What went wrong|Missing or unsupported|Caused by|required tests failed' -A2 "$log" | head -15
echo "boot-mods: FAIL"
exit 1
