#!/usr/bin/env bash
# Prove the two halves of the port lifecycle keep their promises, on throwaway repos:
#   finish-port.py       the port lands in the DESTINATION, on a branch, without build output
#   propose-learnings.py only the LESSON reaches the migrator, gated, and never the mod's identity
set -uo pipefail
cd "$(dirname "$0")/.."
. tools/python.sh || exit 1   # python3 on Windows too
ROOT="$PWD"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
export CLAUDE_CONFIG_DIR="$T/claude"      # never read the real ~/.claude/projects: tests use fixtures only
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

# fixture transcripts: session S1 ported fakeport (one request split over 2 content blocks, a subagent,
# a running cost), S2 is unrelated work, S3 (a build that writes no cost-state) touched it too
J="$CLAUDE_CONFIG_DIR/projects/-ws"; mkdir -p "$J/S1/subagents"
asst() { # asst <session> <reqId> <iso time> <tool path> <out> <cache_read> [effort]
  printf '{"type":"assistant","sessionId":"%s","requestId":"%s","timestamp":"%s","effort":"%s","version":"9.9.9","entrypoint":"cli","message":{"model":"claude-test-1","content":[{"type":"tool_use","input":{"file_path":"%s"}}],"usage":{"input_tokens":10,"output_tokens":%s,"cache_read_input_tokens":%s,"cache_creation_input_tokens":100,"cache_creation":{"ephemeral_5m_input_tokens":40,"ephemeral_1h_input_tokens":60},"output_tokens_details":{"thinking_tokens":5}}}}\n' \
    "$1" "$2" "$3" "${7:-high}" "$4" "$5" "$6"; }
{ asst S1 r1 2026-09-30T10:00:00Z /w/mods/fakeport/A.java 100 1000
  asst S1 r1 2026-09-30T10:00:01Z /w/mods/fakeport/A.java 100 1000      # same request, second block
  asst S1 r2 2026-09-30T10:05:00Z /w/mods/othermod/B.java 200 2000
  printf '{"type":"cost-state","sessionId":"S1","totalCostUSD":1.25}\n{"type":"cost-state","sessionId":"S1","totalCostUSD":1.5}\n'; } > "$J/S1.jsonl"
asst S1 a1 2026-09-30T10:03:00Z /w/x 50 500 medium > "$J/S1/subagents/agent-1.jsonl"
# a READ of a third mod (reference) must not mark the session mixed
printf '{"type":"assistant","sessionId":"S1","requestId":"r3","timestamp":"2026-09-30T10:06:00Z","effort":"high","version":"9.9.9","entrypoint":"cli","message":{"model":"claude-test-1","content":[{"type":"tool_use","name":"Read","input":{"file_path":"/w/mods/refmod/X.java"}}],"usage":{"input_tokens":0,"output_tokens":0,"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}}\n' >> "$J/S1.jsonl"
asst S2 z1 2026-09-30T11:00:00Z /elsewhere/y 999 9999 > "$J/S2.jsonl"

echo "0. port-cost.py (fixture transcripts)"
out="$(python3 tools/port-cost.py fakeport --workspace "$WS" --print 2>&1)"; code=$?
C="$(python3 tools/port-cost.py fakeport --workspace "$WS" --print >/dev/null 2>&1; python3 - "$WS" <<'PY2'
import importlib.util, sys, pathlib, os
spec = importlib.util.spec_from_file_location("pc", "tools/port-cost.py"); pc = importlib.util.module_from_spec(spec); spec.loader.exec_module(pc)
s = pc.collect("fakeport", pathlib.Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects")
c = pc.summarise("fakeport", s, pathlib.Path(sys.argv[1]) / "mods/fakeport")
t = c["totals"]
print(c["sessions"], t["requests"], t["subagent_requests"], t["output"], t["cache_read"], t["thinking"], t["cache_write_5m"], t["cache_write_1h"], c["usd"], sorted(c["effort"].items()), list(c["contamination"].values()))
PY2
)"
[ "$C" = "1 4 1 350 3500 15 120 180 1.5 [('high', 3), ('medium', 1)] [{'othermod': 1}]" ] \
  && ok "one request split over two blocks counts ONCE; subagent included; unrelated sessions not; a READ of another mod is not 'mixed'" \
  || bad "totals: $C"
grep -q 'these sessions also touched other mods' <<<"$out" && grep -q '\$1.50' <<<"$out" \
  && ok "dollars come from the session's recorded total; a mixed session is flagged" || bad "print: $out"

echo "0b. context-profile.py (fixture transcript)"
mv "$J/S1.jsonl" "$T/S1.keep"; mv "$J/S1" "$T/S1dir.keep"; mv "$J/S2.jsonl" "$T/S2.keep"
python3 - "$J/S5.jsonl" <<'PY4'
import json, sys
L = []
def req(rid, ctx, tool=None):
    content = [{"type": "tool_use", "id": tool[0], "name": tool[1], "input": tool[2]}] if tool else []
    L.append({"type": "assistant", "requestId": rid, "timestamp": "2026-09-30T13:00:00Z", "message": {
        "model": "claude-opus-5-5", "content": content,
        "usage": {"input_tokens": 0, "output_tokens": 10, "cache_read_input_tokens": ctx, "cache_creation_input_tokens": 0}}})
def res(tid, chars):
    L.append({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": "x" * chars}]}})
req("q1", 1000, ("t1", "Read", {"file_path": "/w/mods/fakeport/../CATALOG.md"}))
res("t1", 4000)                                                      # 1000 tokens of catalogue
req("q2", 2000, ("t2", "Bash", {"command": "cd /w/mods/fakeport && ./gradlew compileJava"}))
res("t2", 800)                                                       # 200 tokens of build output
req("q3", 2200); req("q4", 2200)
req("q5", 300)                                                       # compaction: nothing carried after this
req("q6", 400)
open(sys.argv[1], "w").write("\n".join(json.dumps(x) for x in L) + "\n")
PY4
P5="$(python3 - <<'PY5'
import importlib.util, pathlib, os
spec = importlib.util.spec_from_file_location("cp", "tools/context-profile.py"); cp = importlib.util.module_from_spec(spec); spec.loader.exec_module(cp)
p = cp.profile("fakeport", pathlib.Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects")
print(len(p["reqs"]), p["carried"]["catalogue"], p["carried"]["build"], p["sizes"]["catalogue"])
PY5
)"
# catalogue (1000 tok) is carried by q2,q3,q4 = 3000; build (200 tok) by q3,q4 = 400; the compaction at q5 stops both
[ "$P5" = "6 3000 400 1000" ] && ok "context-profile charges each result for the requests that carried it, and a compaction stops it" \
  || bad "context-profile: $P5"
out="$(python3 tools/context-profile.py fakeport 2>&1)"
grep -q 'FLOOR' <<<"$out" && grep -q 'fewer requests' <<<"$out" && grep -q 'without catalogue results' <<<"$out" \
  && ok "context-profile prints the floor, the categories and the what-ifs" || bad "context-profile output: $out"
rm "$J/S5.jsonl"; mv "$T/S1.keep" "$J/S1.jsonl"; mv "$T/S1dir.keep" "$J/S1"; mv "$T/S2.keep" "$J/S2.jsonl"

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

{ [ -f "$D/mods/fakeport/COST.json" ] && grep -q '^## Cost (recorded by tools/port-cost.py' "$D/mods/fakeport/MIGRATION.md"; } \
  && ok "finish-port records the cost (COST.json + MIGRATION.md) and it travels with the port" || bad "cost not delivered"
# as if the first run was in an earlier minute (CI once straddled one): recorded_at alone must not
# make a re-run commit
for f in "$P/COST.json" "$D/mods/fakeport/COST.json"; do
  sed -i.bak 's/"recorded_at": "[^"]*"/"recorded_at": "2000-01-01T00:00Z"/' "$f" && rm -f "$f.bak"
done
gitq -C "$D" commit -qam "earlier minute"
n1="$(git -C "$D" rev-list --count HEAD)"
GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.invalid GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.invalid \
  python3 tools/finish-port.py fakeport --workspace "$WS" --dest "$D" --env /dev/null >/dev/null 2>&1
[ "$(git -C "$D" rev-list --count HEAD)" = "$n1" ] && ok "re-run with no change makes no commit" || bad "empty re-run committed"

[ "$(grep -c '^## Cost (recorded' "$P/MIGRATION.md")" = 1 ] && ok "re-running replaces the Cost section, never duplicates it" || bad "Cost section duplicated"
asst S3 q1 2026-09-30T12:00:00Z /w/mods/fakeport/C.java 70 700 > "$J/S3.jsonl"
out="$(python3 tools/port-cost.py fakeport --workspace "$WS" --print 2>&1)"
grep -q 'not recorded by this Claude Code build' <<<"$out" \
  && ok "a session from a build that records no cost makes dollars 'not recorded', never a guess" || bad "no-cost build: $out"
rm "$J/S3.jsonl"
# a no-cost-state build on a PRICED model gets an estimate from the exact tokens, labelled as one
mv "$J/S1.jsonl" "$T/S1.keep"; mv "$J/S1" "$T/S1dir.keep"
asst S4 p1 2026-09-30T12:00:00Z /w/mods/fakeport/C.java 1000000 1000000 | sed 's/claude-test-1/claude-opus-5-5/' > "$J/S4.jsonl"
E="$(python3 - "$WS" <<'PY3'
import importlib.util, sys, pathlib, os
spec = importlib.util.spec_from_file_location("pc", "tools/port-cost.py"); pc = importlib.util.module_from_spec(spec); spec.loader.exec_module(pc)
c = pc.summarise("fakeport", pc.collect("fakeport", pathlib.Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects"), pathlib.Path(sys.argv[1]) / "mods/fakeport")
print(c["usd"], c["usd_source"], pc.corpus_row(c)["usd_source"])
PY3
)"
# 10 input @4 + 1M output @20 + 1M cache read @0.20 + 40 5m-writes @5 + 60 1h-writes @8, per million
[ "$E" = "20.2 estimated estimated" ] \
  && grep -q '≈ \$20.20.*estimated from tokens at list prices' <<<"$(python3 tools/port-cost.py fakeport --workspace "$WS" --print 2>&1)" \
  && ok "no recorded dollars + a priced model: an estimate from the tokens, marked usd_source=estimated" || bad "estimate: $E"
rm "$J/S4.jsonl"; mv "$T/S1.keep" "$J/S1.jsonl"; mv "$T/S1dir.keep" "$J/S1"
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
cp tools/propose-learnings.py tools/finish-port.py tools/supported-versions.py tools/port-cost.py tools/model-prices.tsv SUPPORTED_VERSIONS.tsv docs/port-costs.tsv "$M/tools/" && mv "$M/tools/SUPPORTED_VERSIONS.tsv" "$M/" && mv "$M/tools/port-costs.tsv" "$M/docs/"
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
git -C "$M" log -1 --format=%B | grep -q "Port target(s): Minecraft 26.3, NeoForge 26.3.0.39-beta (untested). Gate C (real client): not-run" \
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
row="$(git -C "$M" show HEAD:docs/port-costs.tsv | tail -1)"
{ git -C "$M" log -1 --format=%B | grep -q '^Cost: \$1.50' && grep -q 'claude-test-1' <<<"$row" \
  && ! grep -qi 'fake' <<<"$row" && [ "$(git -C "$M" show HEAD:docs/port-costs.tsv | grep -vc '^#')" -ge 2 ]; } \
  && ok "the learnings PR adds the port's cost row to docs/port-costs.tsv, with no mod identity, and says it" \
  || bad "cost row: $row / $(git -C "$M" log -1 --format=%B | grep Cost)"
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

echo "3. supported-versions.py: 10 mods with Gate C makes a target tested"
S="$T/sv"; mkdir -p "$S/tools"; cp tools/supported-versions.py "$S/tools/"; cp SUPPORTED_VERSIONS.tsv "$S/"
sv() { python3 "$S/tools/supported-versions.py" "$@"; }
[ "$(sv status 26.3)" = untested ] && ok "an unlisted version is untested" || bad "unlisted: $(sv status 26.3)"
for i in 1 2 3 4 5 6 7 8 9; do sv record 26.3 neoforge "PR #$i" >/dev/null; done
[ "$(sv status 26.3)" = "reported (9 of 10 mods with Gate C)" ] && ok "9 mods: reported, and says how far from tested" || bad "9: $(sv status 26.3)"
sv record 26.3 neoforge "PR #10" >/dev/null
[ "$(sv status 26.3)" = tested ] && ok "the 10th mod makes it tested, with no manual step" || bad "10: $(sv status 26.3)"
[ "$(sv status 1.21.1)" = tested ] && grep -q $'^1.21.1\tneoforge\t\ttested\t' "$S/SUPPORTED_VERSIONS.tsv" \
  && ok "an owner override is honoured and survives a record on another row" || bad "override lost"

echo
echo "cycle self-test: $pass passed, $fail failed"
[ "$fail" = 0 ] || exit 1
