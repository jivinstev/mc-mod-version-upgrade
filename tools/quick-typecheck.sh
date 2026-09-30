#!/usr/bin/env bash
# Fast SCREEN for a multi-version port: type-check one target's PREPARED tree in seconds.
#
# WHAT IT IS FOR
#   `./gradlew compileJava -Pmc=<t>` is the authority and takes minutes. During a burn-down you
#   want the same answer per file, many times an hour. This runs the §W source-preparation
#   pipeline for one target and hands the result to javac against the real artifact jars, so a
#   pass costs seconds and can be scoped to a subdirectory.
#
# ⚠ IT IS A SCREEN, NOT A GATE, AND THE DIFFERENCE MATTERS
#   The classpath here is assembled by hand from the Gradle module cache. Gradle's is assembled
#   from the resolved dependency graph. They are close, not identical -- so this can report an
#   error Gradle would not, and (worse) miss one Gradle would. Never QUOTE a number from this in
#   a commit message or a report: quote `./gradlew compileJava` via tools/burndown-count.sh.
#   Use this to decide what to edit next, and the gate to decide whether you are done.
#
# HOW TO KNOW THIS TOOL IS HONEST TODAY (§X25c, and it costs one command)
#   Run it against the CANONICAL target. The source is written for that version, so the right
#   answer is known in advance and is ZERO. That identity case is an oracle you always have and
#   never have to construct -- and it is the only reason the classpath bug below was caught
#   rather than shipped:
#
#     tools/quick-typecheck.sh <mod> 1.21.1     ->  must print  errors=0
#
#   Measured while writing this: a hand-built classpath reported 86 errors there, then 13 after
#   one fix, and 0 only once it stopped guessing and asked Gradle. Every one of those 86 was
#   phantom, on a tree that compiles clean. A screen that has never been shown to report zero on
#   the canonical target is a screen whose numbers you are taking on trust.
#
#   The mis-scoping failure it is built to avoid is §X25b: an incomplete classpath makes whole
#   subsystems read as REMOVED, which inflates the work list and looks like real findings. So it
#   asserts one CANARY class per artifact family and refuses to run if any is missing -- a count
#   of classes on the classpath cannot tell you WHICH jar you are short of.
#
# USAGE
#   tools/quick-typecheck.sh <mod> <mc-target> [path-under-prepared-tree ...]
#   tools/quick-typecheck.sh examplemod 26.2
#   tools/quick-typecheck.sh examplemod 1.21.1 com/example/examplemod/client/renderer
set -uo pipefail

MOD=${1:?usage: quick-typecheck.sh <mod> <mc-target> [subpath ...]}
MC=${2:?usage: quick-typecheck.sh <mod> <mc-target> [subpath ...]}
shift 2
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DIR="$REPO/mods/$MOD"
PROPS="$DIR/versions/$MC.properties"
[ -f "$PROPS" ] || { echo "no such target: $PROPS" >&2
                     echo "known: $(ls "$DIR/versions/"*.properties 2>/dev/null | xargs -n1 basename 2>/dev/null | sed 's/\.properties//' | tr '\n' ' ')" >&2
                     exit 2; }

get() { grep -E "^$1=" "$PROPS" | head -1 | cut -d= -f2-; }
OVERLAY=$(get overlay); NEO=$(get neo_version); JAVA_V=$(get java_version)
[ -n "$OVERLAY" ] || { echo "$PROPS: no 'overlay=' key" >&2; exit 2; }

PREP=$(mktemp -d "/tmp/qtc-$MOD-$MC.XXXXXX")
trap 'rm -rf "$PREP"' EXIT

cd "$DIR"
# Mirror the build's own invocation. Omitting --drop would compile files this target
# deliberately excludes, and omitting the GameTest adapter would leave 26.x's dead
# annotations in place -- either way the screen invents errors the gate does not have.
EXTRA=()
[ -f "versions/$MC.drops.txt" ] && EXTRA+=(--drop "versions/$MC.drops.txt")
GT=$(get gametest_adapter)
if [ -n "$GT" ]; then
    EXTRA+=(--gametest-adapter "$GT"
            --gametest-pkg "$(grep -E '^mod_group_id=' gradle.properties | cut -d= -f2-).compat"
            --gametest-structure empty_test)
fi
[ -d "src/$OVERLAY/java" ] && EXTRA+=(--overlay "src/$OVERLAY/java")
python3 tools/prepare-sources.py \
    --src src/main/java --out "$PREP/java" \
    --renames "versions/$MC.renames.tsv" "${EXTRA[@]}" \
    >"$PREP/prepare.log" 2>&1 || { echo "prepare-sources FAILED:" >&2; cat "$PREP/prepare.log" >&2; exit 3; }
grep -E 'checked=|dead=' "$PREP/prepare.log" | tail -2

# --- classpath: GRADLE'S OWN, cached. Not assembled by hand, and that is the whole point. ---
# The module cache holds every version both targets ever resolved, so a find(1) sweep puts two
# Guavas and two fmlloaders on the path and javac takes whichever sorts first. Measured: that
# made FMLEnvironment.dist (a FIELD on 1.21.1, a method on 26.2) and Guava's buildKeepingLast
# read as missing on the CANONICAL target -- 13 confident, entirely phantom findings on a tree
# that compiles clean. X25b one level down: not a missing artifact, a DUPLICATED one.
CPFILE="$DIR/build/qtc-classpath-$MC.txt"
NEWER=""
for f in "$DIR/build.gradle" "$PROPS" "$DIR/gradle.properties"; do
    [ -f "$f" ] && [ ! "$CPFILE" -nt "$f" ] && NEWER=1
done
if [ ! -s "$CPFILE" ] || [ -n "$NEWER" ]; then
    echo "  resolving $MC compile classpath via Gradle (cached after this)..." >&2
    mkdir -p "$(dirname "$CPFILE")"
    ./gradlew -I "$REPO/tools/qtc-init.gradle" -q qtcClasspath -Pmc="$MC" --console=plain 2>/dev/null \
      | sed -n '/QTC_CLASSPATH_BEGIN/,/QTC_CLASSPATH_END/p' | grep -v QTC_CLASSPATH_ > "$CPFILE"
fi
if [ ! -s "$CPFILE" ]; then
    echo "could not resolve the $MC compile classpath via Gradle." >&2
    echo "  Refusing to fall back to a hand-built one: it is wrong in the ALARMING direction" >&2
    echo "  (phantom findings that look exactly like real work) and this tool exists to be" >&2
    echo "  trusted between gate runs. Fix the Gradle side first." >&2
    exit 4
fi
CP=$(tr '\n' ':' < "$CPFILE")

# --- the classpath is Gradle's, but one entry on it is a TASK OUTPUT the screen never triggers ---
# ModDevGradle stages Minecraft into build/moddev/artifacts with this mod's ACCESS TRANSFORMERS
# already applied, and the AT files are an input to that task. A real ./gradlew compileJava
# re-stages the jar when an AT changes; this screen only READS the path, so after an AT edit it
# type-checks against a jar where the widened members are still private.
#
# That is X25b-ii's shape one layer down -- the classpath is right and one file on it is stale --
# and it fails in the ALARMING direction: every AT'd member reads as "has private access", which is
# exactly the shape of real porting work. Measured on a large boss mod: 6 phantom errors out of 637, all
# of them AbstractArrow's life and baseDamage, gone the moment the artifact was re-staged.
for atf in "$DIR/src/main/resources/META-INF/accesstransformer.cfg" \
           "$DIR/src/$OVERLAY/resources/META-INF/accesstransformer.cfg"; do
    [ -f "$atf" ] || continue
    for art in "$DIR"/build/moddev/artifacts/*-merged.jar; do
        [ -f "$art" ] || continue
        if [ "$atf" -nt "$art" ]; then
            echo "  WARNING: $(basename "$art") is OLDER than $(basename "$atf")." >&2
            echo "    The staged Minecraft has your previous access transformers, so anything you" >&2
            echo "    just widened still reads as 'has private access' -- phantom errors that look" >&2
            echo "    exactly like real work. Re-stage before trusting the count:" >&2
            echo "      (cd $DIR && ./gradlew -Pmc=$MC createMinecraftArtifacts)" >&2
            break 2
        fi
    done
done

# --- §X25b: assert one canary per artifact family, and NAME what is missing ---
missing=""
for c in net.minecraft.world.item.Item \
         net.neoforged.neoforge.common.NeoForge \
         net.neoforged.fml.loading.FMLEnvironment \
         net.neoforged.bus.api.SubscribeEvent \
         net.neoforged.api.distmarker.Dist; do
    javap -cp "$CP" "$c" >/dev/null 2>&1 || missing="$missing $c"
done
# ...and the canary set has to cover the mod's OWN third-party deps, not just the loader's.
# A canary list is only as good as your enumeration of artifact FAMILIES.
#
# TWO THINGS THIS READS THAT ARE EASY TO GET WRONG, AND BOTH REPORT A PHANTOM GAP:
#   1. Derive the canaries from the PREPARED tree, never from src/main/java. On a §W tree the
#      shared source is written in the CANONICAL dialect, so a class this target RENAMES is
#      absent under its old name by construction -- the tool then reports the port's own
#      pending work as a broken classpath. (X27 fault #3, in a different instrument.)
#   2. Exclude the mod's OWN packages by the full mod_group_id, not by a two-segment prefix.
#      A three-segment group (com.example.examplemobs) collapses to a prefix that is
#      also a SIBLING library's (com.example.examplelib), so a representative picked
#      for that prefix can be one of the mod's own classes -- which is never on a compile
#      classpath, and reads as a missing artifact.
OWNGROUP=$(grep -E '^mod_group_id=' gradle.properties | cut -d= -f2-)
IMPORTS=$(grep -rhoE '^import ([a-z][a-z0-9]*\.){2,}[A-Za-z0-9_.]+;' "$PREP/java" 2>/dev/null \
          | sed 's/^import //; s/;$//' \
          | grep -vE '^(java|javax|net\.minecraft|net\.neoforged|com\.mojang|it\.unimi|org\.(joml|slf4j|jetbrains|apache|lwjgl|jspecify))\.' \
          | grep -v "^${OWNGROUP}\." | sort -u)
#   3. Ask whether the FAMILY is present, not whether one chosen member is. A single
#      representative conflates "this artifact is missing" with "this one class moved" -- and
#      during a library major bump the second is the normal state of a half-ported tree, so a
#      one-member canary refuses to run for the whole span you most want the screen. Try
#      several members and only report the family absent when NONE of them resolve.
unresolved=""
for pkg in $(printf '%s\n' "$IMPORTS" | awk -F. 'NF>1{print $1"."$2}' | sort -u); do
    reps=$(printf '%s\n' "$IMPORTS" | grep -E "^${pkg//./\\.}\." | head -12)
    [ -n "$reps" ] || continue
    found=""
    for rep in $reps; do
        javap -cp "$CP" "$rep" >/dev/null 2>&1 && { found=1; break; }
    done
    [ -n "$found" ] || unresolved="$unresolved $pkg.*"
done

# A LOADER canary that will not resolve means the classpath itself is short, and every name in
# the missing jar reads as REMOVED -- that is X25b, and it is a REFUSAL.
if [ -n "$missing" ]; then
    echo "CLASSPATH INCOMPLETE for $MC -- refusing to run." >&2
    echo "  absent canaries:$missing" >&2
    echo "  A count of classes cannot tell you WHICH artifact you are short of, which is why" >&2
    echo "  these are named. Fix the classpath first -- do not read a burn-down through it." >&2
    exit 4
fi
# A DERIVED third-party family that will not resolve is a different finding and must NOT block:
# during a library major bump (a whole root moving, e.g. software.bernie -> com.geckolib) the
# only members left under the old root are the ones with no successor at all, which is real
# porting work rather than a broken classpath. Refusing there withholds the screen for exactly
# the span you most need it. Say it out loud and carry on -- javac and Gradle both report these.
if [ -n "$unresolved" ]; then
    echo "⚠ no class resolves under:$unresolved" >&2
    echo "  Either that artifact is off the classpath, or the tree still names classes this" >&2
    echo "  target removed/renamed. The errors below include them; they are not phantom." >&2
fi

SCOPE=("$PREP/java")
if [ $# -gt 0 ]; then
    SCOPE=(); for p in "$@"; do SCOPE+=("$PREP/java/$p"); done
fi
FILES=$(find "${SCOPE[@]}" -name '*.java' 2>/dev/null)
[ -n "$FILES" ] || { echo "no sources under: $*" >&2; exit 2; }

javac -proc:none -nowarn -d "$PREP/out" \
      --release "${JAVA_V:-21}" \
      -sourcepath "$PREP/java" -cp "$CP" \
      -Xmaxerrs 100000 $FILES 2>"$PREP/errors.txt"

# Count UNIQUE file:line, and classify by KIND -- a pass whose errors are mostly PARSE errors
# has not made progress, it has broken a file, and the raw count reads as triumph (§X5b).
# A scoped run still COMPILES whatever -sourcepath drags in, so unfiltered it reports errors
# from files you did not ask about -- measured: scoping to test/ printed 176, all of them the
# renderer bucket. A number that answers a different question than the one asked is the failure
# this whole tool exists to avoid, so scoped runs count only the scope and say what they hid.
if [ $# -gt 0 ]; then
    PAT=$(printf '%s\n' "$@" | sed 's|[.[\*^$]|\\&|g' | paste -sd'|' -)
    ALL=$(grep -oE '^[^ ]+\.java:[0-9]+: error:' "$PREP/errors.txt" | sort -u)
    HITS=$(printf '%s\n' "$ALL" | grep -E "java/($PAT)" || true)
    OUTSIDE=$(( $(printf '%s\n' "$ALL" | grep -c . ) - $(printf '%s\n' "$HITS" | grep -c . ) ))
    printf '%s\n' "$HITS" | grep . > "$PREP/scoped.txt" || : > "$PREP/scoped.txt"
    UNIQ=$(grep -c . "$PREP/scoped.txt" || echo 0)
    [ "$OUTSIDE" -gt 0 ] && echo "  ($OUTSIDE error(s) outside the scope, pulled in by -sourcepath, not counted)"
else
    UNIQ=$(grep -oE '^[^ ]+\.java:[0-9]+: error:' "$PREP/errors.txt" | sort -u | wc -l | tr -d ' ')
fi
PARSE=$(grep -cE "error: (';' expected|<identifier> expected|class, interface, enum, or record expected|illegal start of|reached end of file)" "$PREP/errors.txt")
echo "screen: $MC  scope=${*:-<whole tree>}  unique file:line errors=$UNIQ  (parse-kind=$PARSE)"
if [ "$PARSE" -gt 0 ] && [ "$UNIQ" -gt 0 ] && [ "$PARSE" -ge $((UNIQ / 2)) ]; then
    echo "⚠ most errors are PARSE errors -- javac stopped early, so this count is NOT a burn-down." >&2
fi
echo "  top files:"
{ [ -s "$PREP/scoped.txt" ] && cat "$PREP/scoped.txt" \
  || grep -oE '^[^ ]+\.java:[0-9]+: error:' "$PREP/errors.txt" | sort -u; } \
  | sed "s|$PREP/java/||; s|:[0-9]*: error:||" | sort | uniq -c | sort -rn | head -12 | sed 's/^/    /'
cp "$PREP/errors.txt" "/tmp/qtc-$MOD-$MC.errors.txt"
echo "  full javac output: /tmp/qtc-$MOD-$MC.errors.txt"
