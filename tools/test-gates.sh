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

echo
echo "gates self-test: $pass passed, $fail failed"
[ "$fail" = 0 ] || exit 1
