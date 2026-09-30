#!/usr/bin/env bash
# Prove the gates work: clean on this repository, and FAILING on each kind of violation.
#
# WHY: check-no-ip.py is code I wrote checking content I wrote, which is self-referential. "It says
# PASS" is therefore weak evidence on its own. What makes it evidence is that it also says FAIL when
# something bad is genuinely present -- so every rule is exercised against a planted violation here,
# in a scratch directory that is deleted afterwards. A gate never verified-to-fail is decoration.
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
pass=0; fail=0

expect() {  # expect <wanted-exit> <label> -- reads the planted tree from $T
   local want="$1" label="$2"
   ( cd "$T" && python3 "$ROOT/tools/check-no-ip.py" --root "$T" >/dev/null 2>&1 )
   local got=$?
   if [ "$got" = "$want" ]; then echo "  PASS  $label (exit $got)"; pass=$((pass+1))
   else echo "  FAIL  $label — wanted exit $want, got $got"; fail=$((fail+1)); fi
}

plant() { rm -rf "$T"; mkdir -p "$T"; }   # no git repo -> the filesystem-walk fallback is exercised too

echo "1. this repository itself must be CLEAN"
( python3 "$ROOT/tools/check-no-ip.py" >/dev/null 2>&1 )
if [ $? = 0 ]; then echo "  PASS  the real tree is clean (exit 0)"; pass=$((pass+1))
else echo "  FAIL  the real tree does not pass its own gate"; fail=$((fail+1)); fi

echo "2. each violation kind must FAIL"
plant; printf 'PK\x03\x04junk' > "$T/somemod.jar";                       expect 1 "a .jar"
plant; printf '\xca\xfe\xba\xbe' > "$T/Foo.class";                       expect 1 "a .class file"
plant; printf 'import com.someauthor.theirmod.Thing;\n' > "$T/A.java";   expect 1 "import of a non-allowlisted package root"
plant; printf 'class A {} // %s: could not be decompiled\n' '$VF' > "$T/B.java"; expect 1 "a decompiler fingerprint"
plant; mkdir -p "$T/decompiled-raw"; printf 'x\n' > "$T/decompiled-raw/C.java"; expect 1 "a migration working directory"
plant; printf 'import net.minecraft.world.item.Item;\n' > "$T/D.java";   expect 0 "an ALLOWED import (must not false-positive)"
plant; printf 'import com.someauthor.theirmod.Thing;\n' > "$T/T.java.example"; expect 1 "an import hidden behind a template suffix (.java.example)"
plant; cp "$ROOT/templates/neoforge-mod/gradle/wrapper/gradle-wrapper.jar" "$T/";   expect 0 "Gradle's wrapper jar, allowlisted BY SHA1"
plant; printf 'PK\x03\x04not-gradle' > "$T/gradle-wrapper.jar";                  expect 1 "a different jar merely NAMED gradle-wrapper.jar"

echo "3. a skipped name check must FAIL under --strict, never pass"
plant; printf 'hello\n' > "$T/E.md"
( python3 "$ROOT/tools/check-no-ip.py" --root "$T" --strict >/dev/null 2>&1 )
[ $? = 2 ] && { echo "  PASS  --strict without --names exits 2"; pass=$((pass+1)); } \
           || { echo "  FAIL  --strict without --names did not exit 2"; fail=$((fail+1)); }

echo "4. the name list, when supplied, must actually match"
plant; printf 'we ported SomeMod last week\n' > "$T/F.md"; printf 'somemod\n' > "$T/names.txt"
( python3 "$ROOT/tools/check-no-ip.py" --root "$T" --names "$T/names.txt" --strict >/dev/null 2>&1 )
[ $? = 1 ] && { echo "  PASS  a forbidden name is caught"; pass=$((pass+1)); } \
           || { echo "  FAIL  a forbidden name was not caught"; fail=$((fail+1)); }

names_case() {  # names_case <wanted-exit> <label> <file-name> <content> <names-file-content>
   local want="$1" label="$2"
   plant; mkdir -p "$T/$(dirname "$3")"; printf '%s\n' "$4" > "$T/$3"; printf '%s\n' "$5" > "$T/../names.$$"
   ( python3 "$ROOT/tools/check-no-ip.py" --root "$T" --names "$T/../names.$$" --strict >/dev/null 2>&1 )
   local got=$?; rm -f "$T/../names.$$"
   if [ "$got" = "$want" ]; then echo "  PASS  $label (exit $got)"; pass=$((pass+1))
   else echo "  FAIL  $label — wanted exit $want, got $got"; fail=$((fail+1)); fi
}
names_case 1 "a long name GLUED to other text (-Dsomemod.flag)"  G.java 'String k = "-Dsomemodxyz.flag";' 'somemodxyz'
names_case 1 "a long name inside an identifier (somemodxyzItems())" H.java 'x = somemodxyzItems();' 'somemodxyz'
names_case 0 "a SHORT name inside an ordinary word is not a hit"   I.md 'the arcade was busy' 'arc'
names_case 1 "a name in a file PATH, not its content"             docs/somemodxyz/notes.md 'nothing to see' 'somemodxyz'
names_case 1 "a re: entry matches its context"                     J.md 'see mods/arena/x' 're:mods/arena\b'
names_case 0 "a re: entry does not fire on the ordinary word"      K.md 'the arena path runs' 're:mods/arena\b'

echo "5. the VENDORED.tsv / SPDX gate"
( python3 "$ROOT/tools/gen-vendored.py" --check >/dev/null 2>&1 )
[ $? = 0 ] && { echo "  PASS  the real tree's manifest and headers are consistent"; pass=$((pass+1)); } \
           || { echo "  FAIL  the real tree fails its own vendored gate"; fail=$((fail+1)); }
vcase() {  # vcase <wanted-exit> <label> <python-mutation> -- run --check in a throwaway git copy
   local want="$1" label="$2" mutation="$3"
   local V; V="$(mktemp -d)"
   # tar rather than `cp --parents` (GNU-only) so this also runs on macOS.
   ( cd "$ROOT" && git ls-files -z | tar --null -T - -cf - ) | ( cd "$V" && tar -xf - )
   ( cd "$V" && git init -q && python3 -c "$mutation" && git add -A ) >/dev/null 2>&1
   ( cd "$V" && python3 tools/gen-vendored.py --check >/dev/null 2>&1 )
   local got=$?; rm -rf "$V"
   if [ "$got" = "$want" ]; then echo "  PASS  $label (exit $got)"; pass=$((pass+1))
   else echo "  FAIL  $label — wanted exit $want, got $got"; fail=$((fail+1)); fi
}
# The CONTROL: an unmodified copy must pass, or every "exit 1" below could be the copy failing for
# some unrelated reason rather than the gate catching the mutation.
vcase 0 "control: an unmodified copy passes" 'pass'
vcase 1 "a vendored file LOSES its SPDX header" \
  'import pathlib;p=pathlib.Path("templates/neoforge-mod/settings.gradle");p.write_text("".join(l for l in p.read_text().splitlines(True) if "SPDX-License-Identifier" not in l))'
vcase 1 "a NON-vendored file claims MIT" \
  'import pathlib;p=pathlib.Path("tools/find-member.py");l=p.read_text().splitlines(True);l.insert(1,"# SPDX-License-Identifier: MIT\n");p.write_text("".join(l))'
vcase 1 "a new scaffold file lands without regenerating the manifest" \
  'import pathlib;pathlib.Path("templates/neoforge-mod/tools/new-tool.sh").write_text("# SPDX-License-Identifier: MIT\nx\n")'

echo
echo "gates self-test: $pass passed, $fail failed"
[ "$fail" = 0 ] || exit 1
