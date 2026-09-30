#!/usr/bin/env bash
# Prove the two halves of the port lifecycle keep their promises, on throwaway repos:
#   finish-port.py       the port lands in the DESTINATION, on a branch, without build output
#   propose-learnings.py only the LESSON reaches the migrator, gated, and never the mod's identity
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
pass=0; fail=0
ok()  { echo "  PASS  $1"; pass=$((pass+1)); }
bad() { echo "  FAIL  $1"; fail=$((fail+1)); }
gitq() { git -c user.name=t -c user.email=t@example.invalid "$@"; }

# a fake finished port in a fake workspace
WS="$T/ws"; P="$WS/mods/fakeport"
mkdir -p "$P/src/main/java/org/fake/fakeport" "$P/src/main/resources/META-INF" "$P/build/libs" \
         "$P/run/world" "$P/.gradle" "$P/decompiled-raw" "$P/run-mc26.2" "$P/src/main/resources/assets/x/out"
echo 'plugins {}' > "$P/build.gradle"
printf 'minecraft_version=26.3\nneo_version=26.3.0.39-beta\n' > "$P/gradle.properties"
echo 'class A {}' > "$P/src/main/java/org/fake/fakeport/A.java"
printf '[[mods]]\nmodId="fakeport"\ndisplayName="Fake Port Mod"\nversion="${file.jarVersion}"\n[[dependencies.fakeport]]\nmodId="neoforge"\n[[dependencies.fakeport]]\nmodId="minecraft"\n' > "$P/src/main/resources/META-INF/neoforge.mods.toml"
echo 'keep' > "$P/src/main/resources/assets/x/out/keep.json"
printf '# port\n**Status: DONE.** gates green\n' > "$P/MIGRATION.md"
mkdir -p "$P/.git"; echo x > "$P/.git/HEAD"
for f in build/libs/x.jar run/world/level.dat .gradle/c decompiled-raw/A.java run-mc26.2/x build.log; do echo x > "$P/$f"; done

echo "1. finish-port.py"
out="$(python3 tools/finish-port.py fakeport --workspace "$WS" --dest none --env /dev/null 2>&1)"; code=$?
[ $code = 0 ] && grep -q 'stays in the workspace' <<<"$out" && ok "no destination: keeps the port in the workspace, exit 0" || bad "dest none (exit $code)"

python3 tools/finish-port.py fakeport --workspace "$WS" --dest "$ROOT/templates" --env /dev/null >/dev/null 2>&1
[ $? = 1 ] && ok "a destination inside the migrator checkout is REFUSED" || bad "migrator-internal destination not refused"

D="$T/dest"; mkdir -p "$D"; gitq -C "$D" init -q -b main; echo r > "$D/README"; gitq -C "$D" add -A; gitq -C "$D" commit -qm init
GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.invalid GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.invalid \
  python3 tools/finish-port.py fakeport --workspace "$WS" --dest "$D" --env /dev/null >"$T/fp.log" 2>&1; code=$?
br="$(git -C "$D" rev-parse --abbrev-ref HEAD)"
[ $code = 0 ] && [ "$br" = port/fakeport ] && ok "git destination: committed on branch port/fakeport" || bad "git destination (exit $code, branch $br): $(cat "$T/fp.log")"
files="$(git -C "$D" ls-files mods/fakeport)"
if grep -q 'build.gradle' <<<"$files" && grep -q 'A.java' <<<"$files" && grep -q 'assets/x/out/keep.json' <<<"$files" \
   && ! grep -qE 'build/libs|run/world|\.gradle/|\.git/|decompiled-raw|run-mc26|\.log$' <<<"$files"; then
  ok "source copied; build/run/.gradle/decompiled-raw/per-target/logs left behind; a deep 'out' dir kept"
else bad "wrong file set: $files"; fi
git -C "$D" log -1 --format=%B | grep -q 'Status: DONE' && ok "the commit message carries MIGRATION.md's status" || bad "status not in commit"
[ "$(git -C "$D" rev-parse main)" = "$(git -C "$D" rev-parse main)" ] && [ -z "$(git -C "$D" ls-tree main mods 2>/dev/null)" ] \
  && ok "main is untouched" || bad "main was changed"

n1="$(git -C "$D" rev-list --count HEAD)"
GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.invalid GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.invalid \
  python3 tools/finish-port.py fakeport --workspace "$WS" --dest "$D" --env /dev/null >/dev/null 2>&1
[ "$(git -C "$D" rev-list --count HEAD)" = "$n1" ] && ok "re-run with no change makes no commit" || bad "empty re-run committed"

echo edited >> "$P/src/main/java/org/fake/fakeport/A.java"
GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.invalid GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.invalid \
  python3 tools/finish-port.py fakeport --workspace "$WS" --dest "$D" --env /dev/null >/dev/null 2>&1
[ "$(git -C "$D" rev-list --count HEAD)" = "$((n1+1))" ] && ok "re-run after an edit adds one commit" || bad "edit not committed"

echo dirty >> "$D/mods/fakeport/build.gradle"
python3 tools/finish-port.py fakeport --workspace "$WS" --dest "$D" --env /dev/null >/dev/null 2>&1
[ $? = 1 ] && ok "uncommitted changes under mods/<modid> in the destination are REFUSED" || bad "dirty destination overwritten"
git -C "$D" checkout -q -- .

N="$T/plain"; mkdir -p "$N/mods/fakeport"; echo old > "$N/mods/fakeport/old"
python3 tools/finish-port.py fakeport --workspace "$WS" --dest "$N" --env /dev/null >/dev/null 2>&1
[ $? = 1 ] && [ -f "$N/mods/fakeport/old" ] && ok "non-git destination: an existing copy is not replaced without --force" || bad "non-git overwrite"
python3 tools/finish-port.py fakeport --workspace "$WS" --dest "$N" --env /dev/null --force >/dev/null 2>&1
[ $? = 0 ] && [ -f "$N/mods/fakeport/build.gradle" ] && [ ! -f "$N/mods/fakeport/old" ] && ok "--force replaces it" || bad "--force"

echo "2. propose-learnings.py (on a throwaway clone of this checkout)"
M="$T/mig"; git clone -q "$ROOT" "$M"
cp tools/propose-learnings.py tools/finish-port.py "$M/tools/"
gitq -C "$M" add -A; gitq -C "$M" commit -qm "test: current tools" >/dev/null 2>&1
before="$(git -C "$M" rev-parse HEAD)"; home="$(git -C "$M" rev-parse --abbrev-ref HEAD)"
# the clone may ALREADY have a learnings/* branch: when this runs on a learnings PR's own tree, git names the
# clone's branch after the checked-out one. Count only what the test creates.
pre_branches="$(git -C "$M" branch --list 'learnings/*' | wc -l)"
prop() { GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.invalid GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.invalid \
           python3 "$M/tools/propose-learnings.py" --modid fakeport --workspace "$WS" --env /dev/null --base "$before" "$@"; }

cat > "$WS/good.md" <<'EOF'
### new R
R99. **A test lesson** · **Pattern:** some old shape on Minecraft 1.21.1 and NeoForge · **Runtime:** `SomeException: a log line` · **Fix:** the new shape.

### new R
R98. **A qualified lesson** · **Pattern (1.21.2+):** old · **Error (downport):** `x` · **Fix (→1.21.1):** new.

### new R
R97. **A title with a star: `#minecraft:enchantable/*`** · **Pattern:** old · **Symptom:** nothing enchants · **Fix:** new.

### new D
D99. **A combined label** · **Pattern → Error → Fix:** old → `SomeError` → new.

### augment M6
· **AUGMENT — a test case:** the symptom is `Not a JSON object`; the fix is unchanged.
EOF
prop --additions "$WS/good.md" --dry-run >"$T/dry.log" 2>&1; code=$?
[ $code = 0 ] && grep -q '^+R99. \*\*A test lesson' "$T/dry.log" && [ "$(git -C "$M" rev-parse HEAD)" = "$before" ] \
  && [ -z "$(git -C "$M" status --porcelain)" ] && ok "--dry-run shows the diff and changes nothing" || bad "dry-run (exit $code)"

prop --additions "$WS/good.md" >"$T/good.log" 2>&1; code=$?
br="$(git -C "$M" rev-parse --abbrev-ref HEAD)"
if [ $code = 0 ] && [[ "$br" == learnings/* ]] && grep -q '^R99. \*\*A test lesson' "$M/CATALOG.md" \
   && git -C "$M" show HEAD --stat | grep -q 'catalog-census.tsv'; then
  ok "a valid lesson is committed on learnings/*, with the census updated"
else bad "valid lesson (exit $code): $(tail -5 "$T/good.log")"; fi
git -C "$M" log -1 --format=%B | grep -q "Port target(s): Minecraft 26.3, NeoForge 26.3.0.39-beta (UNTESTED)" \
  && ok "the learnings commit records the port's target and its SUPPORTED_VERSIONS status" || bad "target not recorded: $(git -C "$M" log -1 --format=%B | grep -i target)"
python3 - "$M/CATALOG.md" <<'PY' && ok "R99 lands in section R, D99 in '## D.' (not '## Deeper…'), the augment right after M6" || bad "placement wrong"
import re, sys
L = open(sys.argv[1]).read().split("\n")
r = next(i for i, l in enumerate(L) if l.startswith("R99."))
sec = max(i for i, l in enumerate(L[:r]) if l.startswith("## "))
m6 = next(i for i, l in enumerate(L) if l.startswith("M6."))
aug = next(i for i, l in enumerate(L) if "AUGMENT — a test case" in l)
nxt = next(i for i in range(m6 + 1, len(L)) if re.match(r"^(M\d+\.|#{2,4} )", L[i]))
d = next(i for i, l in enumerate(L) if l.startswith("D99."))
dsec = max(i for i, l in enumerate(L[:d]) if l.startswith("## "))
sys.exit(0 if L[sec].startswith("## R.") and m6 < aug < nxt and L[dsec].startswith("## D.") else 1)
PY
grep -q "^CATALOG.md::R99	" "$M/docs/catalog-census.tsv" && ok "the census now inventories R99 (a later PR cannot silently drop it)" \
  || bad "R99 missing from the census"
gitq -C "$M" checkout -q "$home"

printf '### new R\nR98. **Seen in Fake Port Mod** · **Pattern:** x · **Error:** y · **Fix:** z\n' > "$WS/leak.md"
prop --additions "$WS/leak.md" >"$T/leak.log" 2>&1; code=$?
[ $code = 1 ] && grep -q 'name this port' "$T/leak.log" && [ -z "$(git -C "$M" status --porcelain)" ] \
  && ok "additions naming the ported mod are REFUSED, checkout left clean" || bad "identity leak not refused (exit $code)"

printf '### new R\nR98. **No pattern** · **Error:** y · **Fix:** z\n' > "$WS/nopat.md"
prop --additions "$WS/nopat.md" >/dev/null 2>&1
[ $? = 1 ] && ok "a new entry without **Pattern:** is REFUSED" || bad "missing Pattern accepted"

printf '### new R\nR98. **No symptom** · **Pattern:** x · **Fix:** z\n' > "$WS/nosym.md"
prop --additions "$WS/nosym.md" >/dev/null 2>&1
[ $? = 1 ] && ok "a new entry with no Error/Runtime/Symptom is REFUSED" || bad "missing symptom accepted"

printf '### augment ZZ404\n· **AUGMENT — x:** y\n' > "$WS/noent.md"
prop --additions "$WS/noent.md" >/dev/null 2>&1
[ $? = 1 ] && ok "augmenting an entry that does not exist is REFUSED" || bad "unknown entry accepted"

branches="$(( $(git -C "$M" branch --list 'learnings/*' | wc -l) - pre_branches ))"
[ "$branches" = 1 ] && ok "refused proposals leave no branch behind (only the one good proposal exists)" || bad "$branches learnings branches"

echo
echo "cycle self-test: $pass passed, $fail failed"
[ "$fail" = 0 ] || exit 1
