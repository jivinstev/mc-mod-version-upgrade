#!/usr/bin/env bash
# Convert a mods/<modid> workspace from the single-target NeoGradle scaffold to the MULTI-VERSION
# ModDevGradle one (CATALOG.md section W).
#
#   ./tools/make-multiversion.sh <modid> [target ...]      # default target: 26.2
#
# WHY THIS EXISTS. Fourteen companion mods have to make this same move, and doing it by hand is
# both slow and a place to get one step wrong silently -- the first one done by hand needed four
# corrections that had nothing to do with the mod (the plugin, the repo ORDER that avoids Maven
# Central's 429 on artifacts that are not even there, the JDK-per-target, and a test that located
# itself via user.dir, which ModDevGradle moved). Each is now baked in.
#
# What it does NOT do: touch src/. The port itself is the port. This only builds the frame.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
TPL="$ROOT/templates/multi-version"

MODID="${1:?usage: make-multiversion.sh <modid> [target ...]}"; shift || true
TARGETS=("$@"); [ ${#TARGETS[@]} -eq 0 ] && TARGETS=(26.2)
D="$ROOT/mods/$MODID"
[ -d "$D" ] || { echo "no such port: mods/$MODID"; exit 2; }
[ -f "$D/versions/1.21.1.properties" ] && { echo "mods/$MODID is already multi-version"; exit 0; }

echo "=== $MODID -> multi-version (targets: 1.21.1 ${TARGETS[*]})"
mkdir -p "$D/versions" "$D/tools"
cp "$TPL/tools/prepare-sources.py" "$TPL/tools/gametest_adapter.py" "$D/tools/"

# --- read the mod's identity + its own dependency lines out of the existing build -------------
MOD_NAME=$(grep -oE '^mod_name=.*' "$D/gradle.properties" | cut -d= -f2- || echo "$MODID")

# The mod's OWN dependency lines: everything in dependencies{} except the neoforge artifact
# (ModDevGradle supplies Minecraft from `neoForge { version }`; declaring it too puts two copies
# of Minecraft on the compile classpath) and the JUnit rows the template already carries.
python3 - "$D" "$TPL" "$MOD_NAME" "$MODID" <<'PY'
import re, sys, os
d, tpl, mod_name, modid = sys.argv[1:5]
old = open(f"{d}/build.gradle").read()

# Pull the FIRST dependencies{} block, brace-matched.
i = old.find('dependencies {')
dep = ''
if i >= 0:
    depth, j = 0, i + len('dependencies ')
    while j < len(old):
        if old[j] == '{': depth += 1
        elif old[j] == '}':
            depth -= 1
            if depth == 0: break
        j += 1
    body = old[i + len('dependencies {'): j]
    keep = []
    for line in body.split('\n'):
        s = line.strip()
        if not s: continue
        if 'net.neoforged:neoforge' in s: continue          # MDG supplies it
        if 'junit' in s.lower(): continue                    # template carries these
        keep.append(line)
    dep = '\n'.join(keep).rstrip()

t = open(f"{tpl}/build.gradle.template").read()
t = t.replace('@MOD_NAME@', mod_name)
placeholder = [l for l in t.split('\n') if '@MOD_DEPENDENCIES@' in l][0]
block = ("    // ---- this mod's own dependencies, carried over from its single-target build ----\n"
         + dep + "\n") if dep.strip() else "    // (this mod declares no extra dependencies)\n"
# replace the placeholder comment paragraph with the real block
t = re.sub(r"    // @MOD_DEPENDENCIES@[^\n]*\n(?:    //[^\n]*\n)*", block, t)
open(f"{d}/build.gradle", 'w').write(t)
print(f"    build.gradle  <- template + {len([l for l in dep.split(chr(10)) if l.strip()])} carried dependency line(s)")
PY

# --- strip the per-target rows from gradle.properties -----------------------------------------
# They are versions/<id>.properties data now. Leaving them behind is a SECOND source of truth for
# the same fact: the build reads the versions file into ext (which shadows them), so a stale
# minecraft_version here is invisibly wrong rather than loudly wrong.
python3 - "$D" <<'PY2'
import re, sys
p = sys.argv[1] + "/gradle.properties"
s = open(p).read()
for k in ('minecraft_version', 'minecraft_version_range', 'neo_version', 'loader_version_range',
          'parchment_minecraft_version', 'parchment_version'):
    s = re.sub(rf'(?m)^{re.escape(k)}=.*\n', '', s)
s = s.replace("# Target platform (the thing we migrate TO)\n", "")
open(p, 'w').write(s.rstrip() + "\n")
print("    gradle.properties: per-target rows removed (they live in versions/ now)")
PY2

# --- settings.gradle: NeoForged BEFORE Central in BOTH blocks (V9) ----------------------------
cat > "$D/settings.gradle" <<EOF
pluginManagement {
    repositories {
        // NeoForged FIRST. ModDevGradle's own artifacts (neoform, dsl-userdev) do not live on
        // Maven Central at all, and Central rate-limits with HTTP 429 when asked for them (V9) --
        // which reads as a missing artifact rather than a throttle.
        maven { url = 'https://maven.neoforged.net/releases' }
        maven { url = 'https://libraries.minecraft.net' }
        gradlePluginPortal()
    }
}

plugins {
    id 'org.gradle.toolchains.foojay-resolver-convention' version '1.0.0'
}

dependencyResolutionManagement {
    repositories {
        maven { url = 'https://maven.neoforged.net/releases' }
        maven { url = 'https://libraries.minecraft.net' }
        maven { url = 'https://maven.parchmentmc.org' }
        maven { url = 'https://dl.cloudsmith.io/public/geckolib3/geckolib/maven/' }
        mavenCentral()
    }
}

rootProject.name = '$MODID'
EOF

# --- versions/<target>.properties -------------------------------------------------------------
cat > "$D/versions/1.21.1.properties" <<EOF
# $MOD_NAME — target: Minecraft 1.21.1. The CANONICAL target: src/main/java is written in this
# dialect, so this target's rename table is (near-)empty and its overlay a no-op. It still goes
# through the same prepare-sources pipeline as the others on purpose (W2) -- a mechanism only the
# untested version exercises is a mechanism that quietly breaks.
minecraft_version=1.21.1
minecraft_version_range=[1.21.1,1.21.2)
neo_version=21.1.228
loader_version_range=[1,)
java_version=21
overlay=mc21
EOF
: > "$D/versions/1.21.1.renames.tsv"
mkdir -p "$D/src/mc21/java" "$D/src/mc21/resources"

for T in "${TARGETS[@]}"; do
  case "$T" in
    26.2) NEO=26.2.0.75; JAVA=25; RANGE='[26.2,26.3)'; OVL=mc26 ;;
    *) echo "    !! unknown target $T — add its NeoForge line + Java level here"; continue ;;
  esac
  cat > "$D/versions/$T.properties" <<EOF
# $MOD_NAME — target: Minecraft $T (NeoForge $NEO.x).
# MC 26.x requires Java $JAVA (version manifest: java-runtime-epsilon), which is why the Java level
# is per-version DATA rather than a constant in build.gradle (V1).
minecraft_version=$T
minecraft_version_range=$RANGE
neo_version=$NEO
loader_version_range=[1,)
java_version=$JAVA
overlay=$OVL
# 26.x has no @GameTest annotation (V5/V20/V47) -- the adapter generates the registrar, and the
# DirectGameTest class it needs, from the annotations the shared tree already carries.
gametest_adapter=$MODID
EOF
  # The table is COMPOSED: hand-written rows shipped here + two maps generated on this machine
  # from Mojang's own names (never published). See templates/multi-version/versions/*.hand.tsv.
  WS="${MIGRATE_WORKSPACE:-$HOME/.mc-mod-upgrade/work}/moves"
  HAND="$TPL/versions/$T.renames.hand.tsv"
  if [ -f "$HAND" ]; then
    python3 "$ROOT/tools/compose-renames.py" --hand "$HAND" \
      --generated "$WS/moves-1.21.1-to-$T.tsv" --generated "$WS/colors-1.21.1-to-$T.tsv" \
      --out "$D/versions/$T.renames.tsv" | sed 's/^/    /'
  else
    { echo "# $MODID: Minecraft 1.21.1 -> $T mechanical renames (see templates/multi-version/README.md)."
      echo "# No hand-written starting table ships for $T; generate the type rows with"
      echo "# tools/build-class-move-map.py and add call-shape rules as the burn-down finds them."
      if [ -f "$WS/moves-1.21.1-to-$T.tsv" ]; then grep -v '^#' "$WS/moves-1.21.1-to-$T.tsv" || true; fi
    } > "$D/versions/$T.renames.tsv"
    echo "    !! no hand table for $T -- versions/$T.renames.tsv holds only the generated moves (if any)"
  fi
  printf '\n# --- %s'"'"'s own rows (CHECKED: a dead rule here is a real bug) ---\n' "$MODID" >> "$D/versions/$T.renames.tsv"
  mkdir -p "$D/src/$OVL/java" "$D/src/$OVL/resources"
  echo "    versions/$T.properties + rename table + src/$OVL/"
done

echo
echo "Frame built. If the composer said a generated map was missing, build both once per version pair:"
echo "  python3 tools/build-class-move-map.py --from-cp <1.21.1 cp.txt> --to-cp <T cp.txt> \\"
echo "      --out \"\$MIGRATE_WORKSPACE/moves/moves-1.21.1-to-<T>.tsv\""
echo "  python3 tools/gen-color-renames.py  --from-cp <1.21.1 cp.txt> --to-cp <T cp.txt> \\"
echo "      --out \"\$MIGRATE_WORKSPACE/moves/colors-1.21.1-to-<T>.tsv\""
echo "Next:"
echo "  1. ./gradlew build                 # the CANONICAL target must stay green (W2/X8)"
echo "  2. ./gradlew build -Pmc=${TARGETS[0]}        # start the burn-down"
echo "  3. Check build.gradle's carried dependencies: a libs/ jar or a version-specific"
echo "     coordinate may need to differ per target (a per-target geckolib_source property is the pattern)."
