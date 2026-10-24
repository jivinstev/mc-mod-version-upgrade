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
  && cp -r "$ROOT/docs" "$ROOT/.claude" "$ROOT/templates" . && ./tools/install-hooks.sh >/dev/null \
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

echo "11. gate-loop: a killed client is 'killed', not a crash; a caught WARN trace is never the stop reason (issue #113)"
# gate-loop is shared by single-version and version-branch forks: these cases hold for both
GL="$(mktemp -d)"
gl_kind() {  # gl_kind <wanted-kind> <label> <log-file> -- the kind tools/gate-loop.py's classifier gives a client log
   local got; got="$(python3 -c "import importlib.util as u,sys; s=u.spec_from_file_location('gl', sys.argv[1]); m=u.module_from_spec(s); s.loader.exec_module(m); f=m.failure_of(open(sys.argv[2], encoding='utf-8').read(), client=True); print(f[0] if f else 'green')" "$ROOT/tools/gate-loop.py" "$3" 2>&1)"
   if [ "$got" = "$1" ]; then echo "  PASS  $2 ($got)"; pass=$((pass+1))
   else echo "  FAIL  $2 -- wanted $1, got $got"; fail=$((fail+1)); fi
}
cat > "$GL/warn.txt" <<'LOG'
[12:00:01] [Render thread/WARN] [o.e.Info/]: Error during loading of villager trades
java.lang.IllegalStateException: Missing registry: ResourceKey[minecraft:root / minecraft:trade_set]
	at TRANSFORMER/othermod@1/b.o.Trades.load(Trades.java:3)
LOG
{ cat "$GL/warn.txt"; printf 'EXAMPLEMOD_BOOT_TEST: spawned 11 creature type(s)\n'
  printf "Process 'command '/usr/bin/java'' finished with non-zero exit value 137\n"; } > "$GL/killed.log"
gl_kind killed "a caught WARN trace, then exit value 137, no verdict" "$GL/killed.log"
{ cat "$GL/warn.txt"; printf '[12:00:05] [Render thread/ERROR] [minecraft/Minecraft]: Unreported exception thrown!\n'
  printf 'java.lang.NullPointerException: boom\n\tat TRANSFORMER/examplemod@1/a.m.Spawner.tick(Spawner.java:42)\n'
  printf "Process 'command '/usr/bin/java'' finished with non-zero exit value 1\n"; } > "$GL/crash.log"
gl_kind crash "control: a real uncaught crash before the verdict" "$GL/crash.log"
python3 -c "import importlib.util as u,sys; s=u.spec_from_file_location('gl', sys.argv[1]); m=u.module_from_spec(s); s.loader.exec_module(m); f=m.failure_of(open(sys.argv[2], encoding='utf-8').read(), client=True); sys.exit(0 if 'NullPointerException' in f[1] and 'Missing registry' not in f[1] else 1)" "$ROOT/tools/gate-loop.py" "$GL/crash.log" \
  && { echo "  PASS  the crash reported is the uncaught one, not the caught WARN trace"; pass=$((pass+1)); } \
  || { echo "  FAIL  the caught WARN trace was reported as the crash"; fail=$((fail+1)); }
printf 'MemTotal: 16000000 kB\nMemAvailable: 1024000 kB\n' > "$GL/meminfo"; printf '0::/\n' > "$GL/cgroup"
out="$(env -u MMVU_GATEC_MIN_FREE_MB python3 -c "import importlib.util as u,sys; s=u.spec_from_file_location('gl', sys.argv[1]); m=u.module_from_spec(s); s.loader.exec_module(m); print(m.memory_guard(sys.argv[2], sys.argv[3], sys.argv[4], linux=True))" "$ROOT/tools/gate-loop.py" "$GL/meminfo" "$GL/nocg" "$GL/cgroup" 2>&1)"
grep -q '^not enough free memory (1000 MB free, need ' <<<"$out" \
  && { echo "  PASS  the pre-Gate-C guard stops on a faked 1000 MB MemAvailable"; pass=$((pass+1)); } \
  || { echo "  FAIL  the free-memory guard did not stop: $out"; fail=$((fail+1)); }
rm -rf "$GL"

echo "12. guard-names: the forbidden-names guard set up in one command (issue #107)"
gn_out="$(python3 "$ROOT/tools/guard-names.py" --self-check 2>&1)"
grep -q "self-check: OK" <<<"$gn_out" \
  && { echo "  PASS  guard-names --self-check: derivation, a re: line for a short id, merge keeps every line, idempotent, a list inside a repo refused, hooks installed"; pass=$((pass+1)); } \
  || { echo "  FAIL  guard-names.py --self-check"; tail -15 <<<"$gn_out" | sed 's/^/        /'; fail=$((fail+1)); }
# no false hits on THIS repository's own text: a fixture mod with an invented id (built at run time, so this file does
# not name it) and ordinary-word display name, authors and archives name, checked with a scratch list (--dry-run)
G="$(mktemp -d)"; mkdir -p "$G/mod/src/main/resources/META-INF"
python3 - "$G/mod" <<'PY'
import pathlib, sys
d = pathlib.Path(sys.argv[1]); mid = "qzv" + "exmod"; pkg = "com.qzv" + "ex.ex"
(d / "src/main/java" / pkg.replace(".", "/")).mkdir(parents=True)
(d / "src/main/resources/META-INF/neoforge.mods.toml").write_text(
    '[[mods]]\nmodId="%s"\ndisplayName="Storage"\nauthors="Team, Magic and Arena"\n' % mid, encoding="utf-8")
(d / "gradle.properties").write_text("mod_id=%s\nmod_group_id=%s\narchives_name=storage\n" % (mid, pkg), encoding="utf-8")
(d / "src/main/java" / pkg.replace(".", "/") / "M.java").write_text('package %s;\n@Mod("x")\nclass M {}\n' % pkg, encoding="utf-8")
PY
: > "$G/names.txt"
out="$(FORBIDDEN_NAMES_FILE="$G/names.txt" python3 "$ROOT/tools/guard-names.py" ensure --mod-repo "$G/mod" --dry-run 2>&1)"; rc=$?
[ "$rc" = 0 ] && grep -q 'would add' <<<"$out" && [ ! -s "$G/names.txt" ] \
  && { echo "  PASS  ordinary-word names make no false hit on this repository (dry run, scratch list untouched)"; pass=$((pass+1)); } \
  || { echo "  FAIL  guard-names on this repository: exit $rc"; fail=$((fail+1)); }
rm -rf "$G"

echo "13. check-exec: no command list starts with a program name only Linux and macOS resolve (issue #109)"
( python3 "$ROOT/tools/check-exec.py" >/dev/null 2>&1 )
[ $? = 0 ] && { echo "  PASS  the real tree starts every command with a portable program"; pass=$((pass+1)); } \
           || { echo "  FAIL  the real tree fails its own exec gate"; fail=$((fail+1)); }
xcase() {  # xcase <wanted-exit> <label> <python-line>
   plant; printf 'import subprocess, sys\nfrom gitbash import BASH\n%s\n' "$3" > "$T/x.py"
   local log; log="$(python3 "$ROOT/tools/check-exec.py" "$T/x.py" 2>&1)"
   local got=$?
   [ "$got" = 1 ] && ! grep -q '^check-exec: FAIL' <<<"$log" && got="crash"
   if [ "$got" = "$1" ]; then echo "  PASS  $2 (exit $got)"; pass=$((pass+1))
   else echo "  FAIL  $2 — wanted exit $1, got $got"; fail=$((fail+1)); fi
}
xcase 1 'a bare "bash"'                       'subprocess.run(["bash", "tools/x.sh"])'
xcase 1 'a bare "sh" in a tuple'              'subprocess.run(("sh", "-c", "true"))'
xcase 1 'a script path run directly'          'cmd = ["./gradlew", "build"]'
xcase 1 'a bare "python3"'                    'subprocess.run(["python3", "tools/x.py"])'
xcase 0 'BASH from tools/gitbash.py'          'subprocess.run([BASH, "gradlew", "build"])'
xcase 0 'sys.executable'                      'subprocess.run([sys.executable, "tools/x.py"])'
xcase 0 'a program that resolves everywhere'  'subprocess.run(["git", "status"])'
xcase 0 'a marked exception'                  'print(["python3", "x.py"])   # exec-ok: printed for a person'

echo
echo "gates self-test: $pass passed, $fail failed"
[ "$fail" = 0 ] || exit 1
