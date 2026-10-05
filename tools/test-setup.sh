#!/usr/bin/env bash
# Prove ./setup keeps its promises, each as an assertion rather than a sentence in a README.
#
# Every case runs in a throwaway COPY of the repository with a throwaway $HOME, so nothing here can
# touch the real .env.local, a real Minecraft folder, or ~/.claude.
set -uo pipefail
unset CLAUDE_CODE_REMOTE   # a cloud session would otherwise make setup probe the network
unset CURSEFORGE_API_KEY   # a key in this shell would change what setup says about it
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
fresh; mkdir -p "$H/.minecraft/versions/1.21.1" "$H/.minecraft/versions/neoforge-21.1.228" "$H/.minecraft/mods"; touch "$H/.minecraft/mods/a.jar"
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

echo "12. migration is a yes/no ADD-ON, asked interactively"
fresh; st --yes --migrate >/dev/null
grep -q '^SETUP_PATH=migrate$' "$R/.env.local" && ok "--migrate is shorthand for --path migrate" || bad "--migrate did not set SETUP_PATH"
# drive the real prompt through a pseudo-terminal: setup refuses to be interactive without one
tty_run() { local ans="$1"; shift; python3 - "$R" "$H" "$ans" "$@" <<'PYX'
import os, pty, sys, time
root, home, answers, extra = sys.argv[1], sys.argv[2], sys.argv[3].split(","), sys.argv[4:]
pid, fd = pty.fork()
if pid == 0:
    os.chdir(root); os.environ["HOME"] = home
    os.environ["PATH"] = os.environ.get("TTY_PATH") or os.environ["PATH"]
    os.execvp("./setup", ["./setup", "--no-network"] + extra)
out, pending = b"", list(answers)
while True:
    try: chunk = os.read(fd, 4096)
    except OSError: break
    if not chunk: break
    out += chunk
    if out.rstrip().endswith(b":") or out.rstrip().endswith(b"]") or b"]: " in out[-60:]:
        os.write(fd, ((pending.pop(0) if pending else "") + "\n").encode()); out += b"\n"
os.waitpid(pid, 0); sys.stdout.write(out.decode(errors="replace"))
PYX
}
fresh; out="$(tty_run 'maybe,y')"
{ echo "$out" | grep -q "Also set up migration? (yes/no) \[no\]" && echo "$out" | grep -q "please answer" \
  && grep -q '^SETUP_PATH=migrate$' "$R/.env.local"; } \
  && ok "interactive: default shown as 'no', a bad answer is asked again, 'y' turns migration on" \
  || bad "interactive yes/no: $(echo "$out" | grep -iE 'migration|please' | head -3)"
fresh; tty_run '' >/dev/null
grep -q '^SETUP_PATH=install$' "$R/.env.local" && ok "interactive: Enter keeps installing only" || bad "Enter did not default to install: $(grep SETUP_PATH "$R/.env.local")"

echo "13. the ports repo is DETECTED by layout, not by name"
fresh; P="$(dirname "$R")"
mkdir -p "$P/any-name/mods/somemod" "$P/unrelated/mods"; git -C "$P/any-name" init -q; git -C "$P/unrelated" init -q
touch "$P/any-name/mods/somemod/build.gradle"
out="$(st --yes --migrate)"
{ echo "$out" | grep -q "looks like a ports repo.*any-name" && ! echo "$out" | grep -q "unrelated" \
  && grep -q '^MOD_OUTPUT_REPO=../any-name$' "$R/.env.local"; } \
  && ok "a sibling git repo with mods/<modid>/build.gradle is offered under any name; a bare mods/ is not" \
  || bad "detection: $(echo "$out" | grep -E 'ports repo|destination')"

echo "14. setup ends by saying how to start: Claude Code, and what to ask it"
fresh; out="$(SETUP_PATH_OVERRIDE="$NOJAVA" st --yes)"; code=$?
{ [ $code = 0 ] && echo "$out" | grep -q "Claude Code is not installed" && echo "$out" | grep -q "&& claude" \
  && echo "$out" | grep -q "./setup --migrate"; } \
  && ok "no claude on PATH: a note (not a PROBLEM) with the install command, then cd + claude + the add-on hint" \
  || bad "claude hint (exit $code): $(echo "$out" | grep -iE 'claude|next' | head -3)"
C="$(mktemp -d)"; for f in "$NOJAVA"/*; do ln -s "$(readlink "$f")" "$C/$(basename "$f")"; done
printf '#!/bin/sh\n' > "$C/claude"; chmod +x "$C/claude"
fresh; out="$(SETUP_PATH_OVERRIDE="$C" st --yes --migrate)"
{ echo "$out" | grep -q "claude: ok" && ! echo "$out" | grep -q "not installed" && echo "$out" | grep -q "Migrate <mod name>"; } \
  && ok "claude present: reported ok, and the migrate path shows how to ask for a migration" || bad "claude present: $(echo "$out" | grep -iE 'claude' | head -3)"
rm -rf "$C"

echo "15. deploy targets: tested versions + NeoForge installs; vanilla-only skipped; shared folders flagged"
fresh; B="$H/.minecraft"; mkdir -p "$B/versions/26.3" "$B/versions/1.21.1" "$B/versions/neoforge-21.1.228" "$B/mods"
out="$(st --yes)"
{ echo "$out" | grep -q "skipped (vanilla only, no NeoForge installed): 26.3" && ! grep -q '^MINECRAFT_MODS_DIR_26_3=' "$R/.env.local" \
  && grep -q "^MINECRAFT_MODS_DIR_1_21_1=$B/mods$" "$R/.env.local" && grep -q '^MINECRAFT_MODS_DIR_26_2=$' "$R/.env.local"; } \
  && ok "vanilla 26.3 is listed as skipped; 1.21.1 -> its NeoForge folder; tested 26.2 with no install -> none" \
  || bad "targets: $(echo "$out" | sed -n '/Mod versions/,/For each/p' | tr '\n' '|')"
fresh; B="$H/.minecraft"; mkdir -p "$B/versions/neoforge-21.1.228" "$B/versions/neoforge-26.2.0.75" "$B/mods"
out="$(st --yes)"
echo "$out" | grep -q "is also the mods folder of your Minecraft" && ok "one mods/ folder recommended for two versions is FLAGGED" \
  || bad "shared folder not flagged: $(echo "$out" | grep note)"
fresh; B="$H/.minecraft"; mkdir -p "$B/versions/neoforge-21.1.228" "$B/versions/neoforge-26.2.0.75" "$B/mods" "$B-26.2/mods"
st --yes >/dev/null
{ grep -q "^MINECRAFT_MODS_DIR_26_2=$B-26.2/mods$" "$R/.env.local" && grep -q "^MINECRAFT_MODS_DIR_1_21_1=$B/mods$" "$R/.env.local"; } \
  && ok "a separate minecraft-26.2 folder wins for 26.2 over the shared main folder" \
  || bad "per-version folder not preferred: $(grep MODS_DIR "$R/.env.local" | tr '\n' ' ')"
rm -rf "$B-26.2"
fresh; B="$H/.minecraft"; mkdir -p "$B/versions/neoforge-21.1.228" "$B/mods"; tty_run 'n,,,new' >/dev/null
grep -q "^MINECRAFT_MODS_DIR_26_2=$B-26.2/mods$" "$R/.env.local" && [ -d "$B-26.2/mods" ] \
  && ok "interactive 'new' creates a separate folder for that version and uses it" || bad "'new': $(grep MODS_DIR_26 "$R/.env.local")"

echo "16. a RE-RUN asks only NEW questions; --review asks them all"
fresh; st --yes >/dev/null                                  # an earlier install-only setup
out="$(TTY_PATH="$NOJAVA" tty_run ',,no' --migrate)"
{ echo "$out" | grep -q "kept: your main Minecraft folder" && ! echo "$out" | grep -q "your main Minecraft folder (or 'none') \[" \
  && echo "$out" | grep -q "Share your lessons as pull requests? (yes/no) \[yes\]" && echo "$out" | grep -q "Kept [0-9]* earlier answer" \
  && grep -q '^CONTRIBUTE_LEARNINGS=no$' "$R/.env.local"; } \
  && ok "earlier answers are kept and listed; only the new questions are asked (here: 'share lessons' -> no)" \
  || bad "re-run asked old questions: $(echo "$out" | grep -E 'kept|\]: ' | head -5 | tr '\n' '|')"
out="$(TTY_PATH="$NOJAVA" tty_run '' --review)"
echo "$out" | grep -q "your main Minecraft folder (or 'none') \[" && ok "--review asks every question again" || bad "--review did not re-ask"

echo "17. sharing lessons: yes without gh -> the install + sign-in steps are named, nothing is run"
fresh; out="$(SETUP_PATH_OVERRIDE="$NOJAVA" st --yes --migrate)"
{ echo "$out" | grep -q "sharing lessons needs the GitHub CLI" && grep -q '^CONTRIBUTE_LEARNINGS=yes$' "$R/.env.local"; } \
  && ok "the default is to share; with no gh, --yes names 'gh auth login' and installs nothing" || bad "gh note: $(echo "$out" | grep -i 'gh\|share' | head -3)"
fresh; out="$(st --yes)"; ! echo "$out" | grep -q "Share what your ports teach" && ok "install-only setup does not ask about sharing lessons" || bad "asked without the add-on"

echo "18. Claude Code permissions: added (only added), backed up, remembered"
cs() { python3 -c "import json,sys;d=json.load(open('$H/.claude/settings.json'));print(eval(sys.argv[1]))" "$1"; }
fresh; st --yes >/dev/null
{ [ "$(cs "'api.modrinth.com' in d['sandbox']['network']['allowedDomains']")" = True ] \
  && [ "$(cs "'maven.neoforged.net' in d['sandbox']['network']['allowedDomains']")" = False ] \
  && [ "$(cs "d['cleanupPeriodDays']")" = 365 ]; } \
  && ok "install-only: the registries are allowed, the build servers are not, transcripts are kept" || bad "install settings: $(cat "$H/.claude/settings.json")"
fresh; mkdir -p "$H/.claude"
printf '{"model":"x","cleanupPeriodDays":7,"permissions":{"allow":["Bash(ls:*)"]},"sandbox":{"network":{"allowedDomains":["example.org"]}}}' > "$H/.claude/settings.json"
SETUP_PATH_OVERRIDE="$NOJAVA" st --yes --migrate >/dev/null
{ [ "$(cs "d['model']")" = x ] && [ "$(cs "d['cleanupPeriodDays']")" = 7 ] \
  && [ "$(cs "d['permissions']['allow']")" = "['Bash(ls:*)', 'Bash(./gradlew:*)']" ] \
  && [ "$(cs "d['sandbox']['network']['allowedDomains'][0]")" = example.org ] \
  && [ "$(cs "'maven.neoforged.net' in d['sandbox']['network']['allowedDomains']")" = True ] \
  && [ "$(cs "d['sandbox']['excludedCommands']")" = "['./gradlew']" ] \
  && [ "$(cs "'~/.gradle' in d['sandbox']['filesystem']['allowWrite']")" = True ] \
  && grep -q '"model":"x"' "$H/.claude/settings.json.bak"; } \
  && ok "migrate: merged into existing settings -- nothing removed or changed, lists extended, backup kept" \
  || bad "merge: $(cat "$H/.claude/settings.json")"
before="$(cat "$H/.claude/settings.json")"; out="$(SETUP_PATH_OVERRIDE="$NOJAVA" st --yes --migrate)"
[ "$(cat "$H/.claude/settings.json")" = "$before" ] && echo "$out" | grep -q "already in place" \
  && ok "a re-run adds nothing and says so" || bad "re-run changed settings"
fresh; mkdir -p "$H/.claude"; printf '{not json' > "$H/.claude/settings.json"
out="$(st --yes)"; [ "$(cat "$H/.claude/settings.json")" = "{not json" ] && echo "$out" | grep -q "is not valid JSON" \
  && ok "an unparseable settings file is left untouched and reported" || bad "bad JSON touched"
fresh; out="$(st --check)"; [ ! -e "$H/.claude/settings.json" ] && ok "--check does not write Claude Code settings" || bad "--check wrote settings"
fresh; out="$(tty_run 'n,,,,,,no')"
[ ! -e "$H/.claude/settings.json" ] && grep -q '^CLAUDE_SETTINGS=no$' "$R/.env.local" \
  && ok "answering no writes nothing, and the answer is remembered" || bad "no: $(ls "$H/.claude" 2>&1; grep CLAUDE_SETTINGS "$R/.env.local")"

fresh; out="$(CURSEFORGE_API_KEY=not-a-real-key st --yes)"
echo "$out" | grep -q "found in the environment" && ! echo "$out" | grep -q "not-a-real-key" \
  && ! grep -q '^CURSEFORGE_API_KEY' "$R/.env.local" 2>/dev/null \
  && ok "a CurseForge key in the environment is reported as found, never printed or written" \
  || bad "env key: $(echo "$out" | grep -A3 'Mod registries'; grep CURSEFORGE "$R/.env.local")"
fresh; out="$(st --yes)"; echo "$out" | grep -q "CurseForge is OPTIONAL" \
  && ok "without a key, setup still explains how to get one" || bad "no key: $(echo "$out" | grep -A3 'Mod registries')"

rm -rf "$NOJAVA"
echo
echo "setup self-test: $pass passed, $fail failed"
[ "$fail" = 0 ] || exit 1
