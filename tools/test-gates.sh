#!/usr/bin/env bash
# Prove the gates work: clean on this repository, and FAILING on each kind of violation.
#
# WHY: check-no-ip.py is code I wrote checking content I wrote, which is self-referential. "It says
# PASS" is therefore weak evidence on its own. What makes it evidence is that it also says FAIL when
# something bad is genuinely present -- so every rule is exercised against a planted violation here,
# in a scratch directory that is deleted afterwards. A gate never verified-to-fail is decoration.
set -uo pipefail
cd "$(dirname "$0")/.."
. tools/python.sh || exit 1   # python3 on Windows too
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
( env -u FORBIDDEN_NAMES_FILE python3 "$ROOT/tools/check-no-ip.py" --root "$T" --strict >/dev/null 2>&1 )
[ $? = 2 ] && { echo "  PASS  --strict without --names exits 2"; pass=$((pass+1)); } \
           || { echo "  FAIL  --strict without --names did not exit 2"; fail=$((fail+1)); }

echo "3b. without --names, FORBIDDEN_NAMES_FILE (environment, else <root>/.env.local) is the list; unreadable is a failure"
plant; printf 'we ported SomeMod last week\n' > "$T/F.md"; printf 'somemod\n' > "$T/../envnames.$$"
( FORBIDDEN_NAMES_FILE="$T/../envnames.$$" python3 "$ROOT/tools/check-no-ip.py" --root "$T" --strict >/dev/null 2>&1 )
[ $? = 1 ] && { echo "  PASS  the environment's list is used without --names"; pass=$((pass+1)); } \
           || { echo "  FAIL  FORBIDDEN_NAMES_FILE was not used"; fail=$((fail+1)); }
# a path written INTO .env.local is read by Python as is: on Windows (Git Bash) it must be a native path, which the
# shell converts only for arguments and environment variables, never for file contents
envpath="$T/../envnames.$$"; command -v cygpath >/dev/null 2>&1 && envpath="$(cygpath -m "$envpath")"
printf 'FORBIDDEN_NAMES_FILE=%s\n' "$envpath" > "$T/.env.local"
( env -u FORBIDDEN_NAMES_FILE python3 "$ROOT/tools/check-no-ip.py" --root "$T" --strict >/dev/null 2>&1 )
[ $? = 1 ] && { echo "  PASS  <root>/.env.local's list is used without --names"; pass=$((pass+1)); } \
           || { echo "  FAIL  .env.local's FORBIDDEN_NAMES_FILE was not used"; fail=$((fail+1)); }
rm -f "$T/.env.local" "$T/../envnames.$$"
( FORBIDDEN_NAMES_FILE="$T/../no-such-names.$$" python3 "$ROOT/tools/check-no-ip.py" --root "$T" >/dev/null 2>&1 )
[ $? = 2 ] && { echo "  PASS  a configured list that is missing exits 2 (not a silent skip)"; pass=$((pass+1)); } \
           || { echo "  FAIL  a missing FORBIDDEN_NAMES_FILE was skipped"; fail=$((fail+1)); }

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
names_case 1 "a re: entry with a / matches a file PATH (/ on Windows too)" mods/arenaxyz/L.md 'nothing' 're:mods/arenaxyz/'

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
vcase 1 "a vendored file names the WRONG copyright holder" \
  'import pathlib;p=pathlib.Path("templates/neoforge-mod/tools/watch-gatec.sh");import re;p.write_text(re.sub(r"(SPDX-FileCopyrightText: \d{4}) .*", r"\1 Someone Else", p.read_text(), count=1))'
vcase 1 "a new scaffold file lands without regenerating the manifest" \
  'import pathlib;pathlib.Path("templates/neoforge-mod/tools/new-tool.sh").write_text("# SPDX-License-Identifier: MIT\nx\n")'

echo "6. the catalogue fidelity gate"
fcase() {  # fcase <wanted-exit> <label> <python-mutation> -- over a tiny planted catalogue
   local want="$1" label="$2" mutation="$3"
   local C; C="$(mktemp -d)"; mkdir -p "$C/tools" "$C/docs"; : > "$C/tools/real-tool.py"
   printf '## A. Section\n**A1. first** body uses `tools/real-tool.py`.\n**A2. second** body.\n12. **numbered** body.\n' > "$C/CATALOG.md"
   ( cd "$C" && python3 "$ROOT/tools/check-catalog-fidelity.py" --update ) >/dev/null 2>&1
   ( cd "$C" && python3 -c "$mutation" ) >/dev/null 2>&1
   ( cd "$C" && python3 "$ROOT/tools/check-catalog-fidelity.py" ) >/dev/null 2>&1
   local got=$?; rm -rf "$C"
   if [ "$got" = "$want" ]; then echo "  PASS  $label (exit $got)"; pass=$((pass+1))
   else echo "  FAIL  $label — wanted exit $want, got $got"; fail=$((fail+1)); fi
}
fcase 0 "control: an unchanged catalogue passes" 'pass'
fcase 0 "an EDITED entry is reported, not blocked" \
  'import pathlib;p=pathlib.Path("CATALOG.md");p.write_text(p.read_text().replace("second","second, revised"))'
fcase 1 "a bold-lettered entry (**A2.) that DISAPPEARS" \
  'import pathlib;p=pathlib.Path("CATALOG.md");p.write_text(p.read_text().replace("**A2. second** body.\n",""))'
fcase 1 "a numbered entry (12.) that DISAPPEARS" \
  'import pathlib;p=pathlib.Path("CATALOG.md");p.write_text(p.read_text().replace("12. **numbered** body.\n",""))'
fcase 1 "a tool the catalogue names is DELETED" \
  'import os;os.remove("tools/real-tool.py")'

echo "7. the encoding gate (Windows reads and writes cp1252 unless told otherwise)"
( python3 "$ROOT/tools/check-encoding.py" >/dev/null 2>&1 )
[ $? = 0 ] && { echo "  PASS  the real tree names every encoding"; pass=$((pass+1)); } \
           || { echo "  FAIL  the real tree fails its own encoding gate"; fail=$((fail+1)); }
expcase() {  # expcase <wanted-exit> <label> <python-line>
   plant; printf 'import pathlib, subprocess\np = pathlib.Path("x")\n%s\n' "$3" > "$T/e.py"
   # a crash also exits 1, so a FAIL counts only with the gate's own verdict line
   local log; log="$(python3 "$ROOT/tools/check-encoding.py" "$T/e.py" 2>&1)"
   local got=$?
   [ "$got" = 1 ] && ! grep -q '^check-encoding: FAIL' <<<"$log" && got="crash"
   if [ "$got" = "$1" ]; then echo "  PASS  $2 (exit $got)"; pass=$((pass+1))
   else echo "  FAIL  $2 — wanted exit $1, got $got"; fail=$((fail+1)); fi
}
expcase 1 "open() in text mode"                 'open("x").read()'
expcase 1 "open(..., \"w\")"                    'open("x", "w").write("→")'
expcase 1 "Path.read_text()"                    'p.read_text()'
expcase 1 "Path.write_text()"                   'p.write_text("⚠")'
expcase 1 "Path.open() in text mode"            'p.open("a")'
expcase 1 "subprocess.run(text=True)"           'subprocess.run(["git"], text=True)'
expcase 0 "binary open is fine"                 'open("x", "rb").read()'
expcase 0 "encoding= named"                     'p.read_text(encoding="utf-8")'
expcase 0 "a marked exception"                  'p.read_text()  # encoding-ok: test'

echo "8. the README gate (a skill change is a README change)"
( python3 "$ROOT/tools/check-readme.py" >/dev/null 2>&1 )
[ $? = 0 ] && { echo "  PASS  the real README describes every skill"; pass=$((pass+1)); } \
           || { echo "  FAIL  the real README fails its own gate"; fail=$((fail+1)); }
( python3 "$ROOT/tools/check-readme.py" --self-check | grep -q "self-check: OK" )
[ $? = 0 ] && { echo "  PASS  a skill with no row, a missing path, and a skill change without a README change all FAIL; 'nothing checked' is not a pass"; pass=$((pass+1)); } \
           || { echo "  FAIL  check-readme.py --self-check"; fail=$((fail+1)); }

echo "9. the pre-push hook name-checks what the push publishes (FORBIDDEN_NAMES_FILE, no names in the repo)"
H="$(mktemp -d)"; ( cd "$H" && git init -q . && git config user.email t@example.com && git config user.name t \
  && mkdir tools && cp -r "$ROOT"/tools/. tools/ \
  && git -C "$ROOT" ls-files -z | tr '\0' '\n' | grep -v / | while IFS= read -r f; do cp "$ROOT/$f" .; done \
  && cp -r "$ROOT/docs" "$ROOT/.claude" . && ./tools/install-hooks.sh >/dev/null \
  && git add -A && git commit -qm "base" ) >/dev/null 2>&1
FAKE="Example""craft"   # built at run time so this file does not itself name it
printf '%s\n' "$FAKE" > "$H/../names.$$"
hook_case() {  # hook_case <wanted-exit> <label> <file-content> <commit-message>
   ( cd "$H" && printf '%s\n' "$3" > note.txt && git add -A && git commit -qm "$4" )
   local sha; sha="$(git -C "$H" rev-parse HEAD)"
   local base; base="$(git -C "$H" rev-parse HEAD~1)"
   [ "${5:-}" = new ] && base=0000000000000000000000000000000000000000   # a branch the remote does not have yet
   ( cd "$H" && echo "refs/heads/x $sha refs/heads/x $base" | FORBIDDEN_NAMES_FILE="$H/../names.$$" .git/hooks/pre-push origin url >/dev/null 2>&1 )
   local got=$?
   if [ "$got" = "$1" ]; then echo "  PASS  $2 (exit $got)"; pass=$((pass+1))
   else echo "  FAIL  $2 -- wanted exit $1, got $got"; fail=$((fail+1)); fi
}
hook_case 0 "no forbidden name in the push passes"           "plain text"                      "plain commit"
# (before any named commit: with no remote, a new branch's range is its WHOLE history)
hook_case 0 "a NEW branch (no remote ref) with no name passes" "new branch text"              "new branch commit"  new
hook_case 1 "a forbidden name in an ADDED line blocks"      "we ported $FAKE"          "innocent message"
hook_case 1 "a forbidden name in a commit MESSAGE blocks"   "plain again"                     "port of ${FAKE,,}"
hook_case 1 "a NEW branch carrying a name blocks"            "new $FAKE"                       "new branch named"   new

echo "10. the similarity gate (invented code only: the real corpus is third-party and never in CI)"
( python3 "$ROOT/tools/check-similarity.py" --self-check | grep -q "self-check: OK" )
[ $? = 0 ] && { echo "  PASS  check-similarity.py --self-check: a renamed copy FLAGS, an API-only caller passes, no corpus exits 2"; pass=$((pass+1)); } \
           || { echo "  FAIL  check-similarity.py --self-check"; fail=$((fail+1)); }
SR="$(mktemp -d)"; mkdir -p "$SR/corpus" "$SR/gradle" "$SR/cache"
fixture() { python3 -c "import importlib.util as u,sys; s=u.spec_from_file_location('cs', sys.argv[1]); m=u.module_from_spec(s); s.loader.exec_module(m); sys.stdout.write(getattr(m, sys.argv[2]))" "$ROOT/tools/check-similarity.py" "$1"; }
fixture LIBRARY > "$SR/corpus/Library.java"
corpus="$SR/corpus"; command -v cygpath >/dev/null 2>&1 && corpus="$(cygpath -m "$corpus")"
sim_hook() {  # sim_hook <wanted-exit> <label> <hook> <corpus or empty> [strict] -- runs a hook in $H on what is staged/pushed
   local want="$1" label="$2" hook="$3" ref="$4" strict="${5:-}"
   local sha; sha="$(git -C "$H" rev-parse HEAD)"; local base; base="$(git -C "$H" rev-parse HEAD~1)"
   ( cd "$H" && echo "refs/heads/x $sha refs/heads/x $base" | GRADLE_USER_HOME="$SR/gradle" MMVU_SIMILARITY_CACHE="$SR/cache" \
       MMVU_REFERENCE_SOURCES="$ref" MMVU_SIMILARITY_STRICT="$strict" ".git/hooks/$hook" origin url >/dev/null 2>&1 )
   local got=$?
   if [ "$got" = "$want" ]; then echo "  PASS  $label (exit $got)"; pass=$((pass+1))
   else echo "  FAIL  $label -- wanted exit $want, got $got"; fail=$((fail+1)); fi
}
( cd "$H" && printf 'plain\n' > note2.txt && git add note2.txt )
sim_hook 0 "pre-commit: nothing under templates/ staged passes (and does not need a corpus)" pre-commit "" 1
( cd "$H" && git reset -q && mkdir -p templates/shared && fixture COPY > templates/shared/Slots.java && git add templates )
sim_hook 1 "pre-commit: a staged renamed copy of a reference file is REFUSED" pre-commit "$corpus"
sim_hook 0 "pre-commit: no corpus on this machine warns and lets the commit through" pre-commit ""
sim_hook 1 "pre-commit: no corpus with MMVU_SIMILARITY_STRICT=1 is REFUSED" pre-commit "" 1
( cd "$H" && git reset -q && fixture CALLER > templates/shared/Slots.java && git add templates )
sim_hook 0 "pre-commit: a file that only calls the same API passes" pre-commit "$corpus"
( cd "$H" && git commit -q --no-verify -m "caller" )
sim_hook 0 "control: pre-push of the API-only caller passes (so the next refusal is the similarity gate's)" pre-push "$corpus"
( cd "$H" && fixture COPY > templates/shared/Slots.java && git add templates && git commit -q --no-verify -m "copy" )
sim_hook 1 "pre-push: a pushed copy is REFUSED" pre-push "$corpus"
rm -rf "$SR"
rm -rf "$H" "$H/../names.$$"

echo
echo "gates self-test: $pass passed, $fail failed"
[ "$fail" = 0 ] || exit 1
