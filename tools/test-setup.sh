#!/usr/bin/env bash
# Prove ./setup keeps its promises, each as an assertion rather than a sentence in a README.
#
# Every case runs in a throwaway COPY of the repository with a throwaway $HOME, so nothing here can
# touch the real .env.local, a real Minecraft folder, or ~/.claude.
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
pass=0; fail=0
ok()  { echo "  PASS  $1"; pass=$((pass+1)); }
bad() { echo "  FAIL  $1"; fail=$((fail+1)); }

fresh() {  # a clean copy of the tracked tree + an empty HOME; sets $R and $H
  local T; T="$(mktemp -d)"; R="$T/repo"; H="$T/home"; mkdir -p "$R" "$H"
  ( git ls-files -z | tar --null -T - -cf - ) | ( cd "$R" && tar -xf - )
  ( cd "$R" && git init -q && git add -A && git -c user.name=t -c user.email=t@t commit -qm t )
}
st() { ( cd "$R" && HOME="$H" PATH="${SETUP_PATH_OVERRIDE:-$PATH}" ./setup --no-network "$@" ) 2>&1; }

# A PATH with python3 and git but NO java, to prove the install path never needs one.
NOJAVA="$(mktemp -d)"
ln -s "$(command -v python3)" "$NOJAVA/python3"; ln -s "$(command -v git)" "$NOJAVA/git"
for t in bash env tar sh cat dirname; do c="$(command -v $t)" && ln -sf "$c" "$NOJAVA/$t"; done

echo "1. idempotence: a second --yes run changes nothing"
fresh; st --yes >/dev/null; a="$(cat "$R/.env.local")"
out="$(st --yes)"; b="$(cat "$R/.env.local")"
{ echo "$out" | grep -q "no changes" && [ "$a" = "$b" ]; } && ok "second --yes reports zero changes and .env.local is identical" \
  || bad "second --yes changed something"

echo "2. the install path needs no Java, and never mentions it"
fresh; out="$(SETUP_PATH_OVERRIDE="$NOJAVA" st --yes)"; code=$?
[ $code = 0 ] && ok "install path exits 0 with no java on PATH" || bad "install path exit $code without java"
# The path MENU names the JDK (that is how you choose); what must never appear is a warning or problem.
echo "$out" | grep -iE '(problem|note|warn).*(jdk|java)' >/dev/null && bad "install path warns about Java" \
  || ok "install path raises no Java warning or problem"

echo "3. a hand edit always wins"
fresh; st --yes >/dev/null
sed -i.orig 's#^MINECRAFT_DIR=.*#MINECRAFT_DIR=/my/own/choice#' "$R/.env.local"
out="$(st --yes)"
grep -q '^MINECRAFT_DIR=/my/own/choice$' "$R/.env.local" && ok "hand-edited value survives a --yes re-run" \
  || bad "hand edit was overwritten"
python3 -c "import json,sys;s=json.load(open('$R/.setup-state.json'));sys.exit(0 if s['keys']['MINECRAFT_DIR']['origin']=='user' else 1)" \
  && ok "the hand-edited key is recorded as origin=user" || bad "hand edit not promoted to user"

echo "4. --check writes nothing"
fresh; out="$(st --check)"
[ ! -e "$R/.env.local" ] && [ ! -e "$R/.setup-state.json" ] && ok "--check on a fresh tree creates no files" \
  || bad "--check wrote something"

echo "5. switching to migrate is additive, and a missing JDK is a PROBLEM, not a crash"
fresh; st --yes >/dev/null
out="$(SETUP_PATH_OVERRIDE="$NOJAVA" st --yes --path migrate)"; code=$?
[ $code = 1 ] && echo "$out" | grep -q "PROBLEM: no JDK" && ok "migrate without java exits 1 naming the JDK" \
  || bad "migrate without java: exit $code"
grep -q '^SKILLS_INSTALL=project$' "$R/.env.local" && grep -q '^MIGRATE_WORKSPACE=' "$R/.env.local" \
  && ok "existing keys kept, migration keys added" || bad "path switch lost or failed to add keys"

echo "6. a workspace inside a git repository is refused"
fresh; st --yes >/dev/null
sed -i.orig "s#^SETUP_PATH=.*#SETUP_PATH=migrate#" "$R/.env.local"; echo "MIGRATE_WORKSPACE=$R/work" >> "$R/.env.local"
out="$(st --yes)"; echo "$out" | grep -q "is inside a git repository" && ok "workspace inside the checkout is flagged" \
  || bad "workspace inside a repo was accepted silently"

echo "7. mods directories are a SET"
fresh; st --yes >/dev/null; echo "MINECRAFT_MODS_DIR_1_21_1=/keep/me" >> "$R/.env.local"
st --yes --mods-dir "26.2=$H/mc-26.2/mods" --create-dirs >/dev/null
{ grep -q '^MINECRAFT_MODS_DIR_1_21_1=/keep/me$' "$R/.env.local" && grep -q "^MINECRAFT_MODS_DIR_26_2=$H/mc-26.2/mods$" "$R/.env.local" \
  && [ -d "$H/mc-26.2/mods" ]; } && ok "adding a target keeps the others and creates the folder (tier 1)" \
  || bad "--mods-dir did not add cleanly"

echo "8. discovery finds a real layout and records it as detected"
fresh; mkdir -p "$H/.minecraft/versions/1.21.1" "$H/.minecraft/mods"; touch "$H/.minecraft/mods/a.jar"
st --yes >/dev/null
{ grep -q "^MINECRAFT_DIR=$H/.minecraft$" "$R/.env.local" && grep -q "^MINECRAFT_MODS_DIR_1_21_1=$H/.minecraft/mods$" "$R/.env.local"; } \
  && ok "platform default found, per-version deploy target written" || bad "discovery did not write the install"

echo "9. removal is explicit, and every write is backed up"
fresh; st --yes >/dev/null; st --yes --remove SKILLS_INSTALL >/dev/null
{ ! grep -q '^SKILLS_INSTALL=' "$R/.env.local" && grep -q '^SKILLS_INSTALL=' "$R/.env.local.bak"; } \
  && ok "--remove deletes the key; .env.local.bak holds the previous version" || bad "--remove or backup misbehaved"

echo "10. the migration workspace is laid out the way the tools expect"
fresh; st --yes >/dev/null
WS="$H/ws"; sed -i.orig "s#^SETUP_PATH=.*#SETUP_PATH=migrate#" "$R/.env.local"; echo "MIGRATE_WORKSPACE=$WS" >> "$R/.env.local"
st --yes >/dev/null
{ [ -d "$WS/mods" ] && [ "$(readlink "$WS/tools")" = "$R/tools" ] && [ "$(readlink "$WS/templates")" = "$R/templates" ] \
  && [ -L "$WS/.env.local" ]; } && ok "workspace has mods/ plus links to tools/, templates/, .env.local" \
  || bad "workspace layout not created"
mkdir -p "$WS/mods/demo"; printf 'mod_name=Demo\n' > "$WS/mods/demo/gradle.properties"; printf 'dependencies {\n}\n' > "$WS/mods/demo/build.gradle"
( cd "$WS" && MIGRATE_WORKSPACE="$WS" bash tools/make-multiversion.sh demo 26.2 ) >/dev/null 2>&1
{ [ -f "$WS/mods/demo/versions/26.2.renames.tsv" ] && [ -f "$WS/mods/demo/tools/prepare-sources.py" ]; } \
  && ok "make-multiversion.sh runs from INSIDE the workspace and scaffolds mods/<modid>" || bad "tools do not work from the workspace"
out="$(st --yes)"; echo "$out" | grep -q "no changes" && ok "re-running with a workspace is still a no-op" || bad "workspace re-run changed something"
mkdir -p "$H/ws2/tools"; sed -i.orig "s#^MIGRATE_WORKSPACE=.*#MIGRATE_WORKSPACE=$H/ws2#" "$R/.env.local"
out="$(st --yes)"; echo "$out" | grep -q "ws2/tools exists and is not a link" && [ -d "$H/ws2/tools" ] && [ ! -L "$H/ws2/tools" ] \
  && ok "an existing real directory is never replaced by a link" || bad "setup clobbered an existing directory"

echo "11. --output-repo sets the destination, and setup always says where ports go"
fresh; out="$(st --yes --path migrate)"; echo "$out" | grep -q "destination: none" && ok "no destination: setup says ports stay in the workspace" \
  || bad "no destination line: $(echo "$out" | grep destination)"
D="$H/ports"; mkdir -p "$D"; git -C "$D" init -q
out="$(st --yes --path migrate --output-repo "$D")"
{ grep -q "^MOD_OUTPUT_REPO=$D$" "$R/.env.local" && echo "$out" | grep -q "destination: $D (a git repo"; } \
  && ok "--output-repo on a git repo is written and echoed as a git destination" || bad "--output-repo git: $(echo "$out" | grep destination)"
mkdir -p "$H/plain"; out="$(st --yes --path migrate --output-repo "$H/plain")"
echo "$out" | grep -q "destination: $H/plain (not a git repo" && ok "a plain folder is echoed as copy-only" || bad "plain folder not echoed"
out="$(st --yes --path migrate --output-repo "$H/nope")"; code=$?
{ [ $code = 1 ] && echo "$out" | grep -q "does not exist"; } && ok "a nonexistent --output-repo is a PROBLEM (exit 1)" || bad "nonexistent accepted (exit $code)"
st --yes --path migrate --output-repo none >/dev/null
grep -q '^MOD_OUTPUT_REPO=$' "$R/.env.local" && ok "--output-repo none clears the destination" || bad "none did not clear: $(grep MOD_OUTPUT "$R/.env.local")"

rm -rf "$NOJAVA"
echo
echo "setup self-test: $pass passed, $fail failed"
[ "$fail" = 0 ] || exit 1
