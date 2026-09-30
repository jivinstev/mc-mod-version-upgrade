#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# client-boot-loop.sh — iterate on client-only crashes with Claude in the loop.
#
# Run this in YOUR Mac Terminal (it needs your GUI session for OpenGL/GLFW — an
# agent/background shell has no display).
#
# Usage:  client-boot-loop.sh [launch|spawn|battle|gauntlet|census]   (default: spawn)
#   launch   — boot to the title screen only (client-LOAD check: mixins, renderers, model bake)
#   spawn    — create a fresh world + spawn one of every <modid> creature, tick ~10s
#   battle   — battle royale STRESS test: N of every creature in two teams fighting around the
#              player (Creative/invincible), tick ~30s — stresses entity count, combat, AoE
#              particles and death sequences all in the camera frustum
#   gauntlet — item/block/use/effect STRESS test (the client/UI axis the mob modes miss): builds
#              every item tooltip, renders all items in the inventory, wears every armor piece,
#              places every block, use()s every item, and applies every mob effect — each once
#
# Battle env vars (optional):
#   BATTLE_SECONDS=<n>  keep the royale running n seconds so you can fly around and watch it
#                       (default 30). Scales the watchdog too. e.g. BATTLE_SECONDS=180 ... battle
#   BATTLE_COUNT=<n>    mobs of EACH type per side-cycle (default 8; ~64 mobs total at 8)
#
# Each round it:
#   1. launches the DEV client in the chosen mode (`runClient -Pboottest -Ptestmode=MODE`),
#      driving it with no manual input,
#   2. if it survives the mode's run -> prints SUCCESS and exits 0,
#   3. if it crashes/hangs -> writes a crash digest and BLOCKS, waiting for Claude
#      to fix the source and signal, then relaunches (picks up the fix on recompile).
#
# SAFE for your son's game: `runClient` launches a *separate* dev instance from this
# repo's `run/` dir. It never reads or writes ~/Library/Application Support/minecraft.
# Nothing is deployed.
#
# Handshake with Claude (files under $SIG_DIR):
#   crash.log    <- this script writes the crash digest here
#   crash.ready  <- this script touches it to say "a crash is waiting"
#   fix.done     <- Claude touches it to say "fixed + recompiled, relaunch"
#   stop         <- touch this (or Ctrl-C) to end the loop
#
# Stop anytime with Ctrl-C.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

# This script lives PER-MOD at <repo>/mods/<modid>/tools/ — derive the mod from its own location
# (no hardcoded id), so each mod's stress test is self-contained and independently runnable.
MOD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # <repo>/mods/<modid>
MOD="${MOD:-$(basename "$MOD_DIR")}"
SIG_DIR="${SIG_DIR:-/tmp/${MOD}-clientloop}"                 # per-mod signal dir (no cross-mod collision)
TIMEOUT_SECS="${TIMEOUT_SECS:-600}"   # kill a boot that never reaches the title (default 10 min; first run downloads assets)

# Test mode (positional arg 1): launch | spawn | battle  (default: spawn)
#   launch — boot to the title screen only (client-load check)
#   spawn  — create a world + spawn one of every <modid> creature, tick ~10s
#   battle — N of every creature in two teams fighting around the player, tick ~30s (stress test)
MODE="${1:-spawn}"
case "$MODE" in
  launch|spawn|battle|gauntlet|census) ;;
  *) echo "usage: $(basename "$0") [launch|spawn|battle|gauntlet|census]   (got: '$MODE')"; exit 2 ;;
esac
GRADLE_MODE_ARGS=(-Pboottest "-Ptestmode=$MODE")
# battle/gauntlet take a while — give the watchdog more room.
{ [ "$MODE" = "battle" ] || [ "$MODE" = "gauntlet" ]; } && TIMEOUT_SECS="${TIMEOUT_SECS_BATTLE:-900}"
# Watch mode: BATTLE_SECONDS=<n> keeps the royale running that long (e.g. to float around and watch).
# Scales the battle window and the watchdog (+300s buffer for world gen/load).
if [ "$MODE" = "battle" ] && [ -n "${BATTLE_SECONDS:-}" ]; then
  GRADLE_MODE_ARGS+=("-Pbattleticks=$((BATTLE_SECONDS * 20))")
  TIMEOUT_SECS=$((BATTLE_SECONDS + 300))
fi
# Optional: BATTLE_COUNT=<n> mobs per type (default 8).
[ -n "${BATTLE_COUNT:-}" ] && GRADLE_MODE_ARGS+=("-Pbattlecount=$BATTLE_COUNT")
# SMOKE-HARNESS: forward the target prebuilt jars + their namespaces to the dev client so the
# generic ClientBootSmokeTest loads them (localRuntime) and drives them (-Psmokens). install-mod
# sets SMOKEJARS (comma-separated abs paths) + SMOKENS (comma-separated target modIds).
[ -n "${SMOKEJARS:-}" ] && GRADLE_MODE_ARGS+=("-Psmokejars=$SMOKEJARS")
[ -n "${SMOKENS:-}" ]   && GRADLE_MODE_ARGS+=("-Psmokens=$SMOKENS")
# Optional: open specific JEI recipe categories after world load to exercise their setRecipe()/draw()
# render path. SMOKEJEIRECIPES="modid:recipe_uid,…" (JEI must be in SMOKEJARS). See JeiRecipeProbe.
[ -n "${SMOKEJEIRECIPES:-}" ] && GRADLE_MODE_ARGS+=("-Psmokejeirecipes=$SMOKEJEIRECIPES")
# Optional: assert JEI shows ≥1 recipe producing each listed output item (works for mods with NO
# custom category, whose recipes ride the vanilla ones). SMOKEJEIVERIFY="modid:item_id,…".
[ -n "${SMOKEJEIVERIFY:-}" ] && GRADLE_MODE_ARGS+=("-Psmokejeiverify=$SMOKEJEIVERIFY")

CRASH_LOG="$SIG_DIR/crash.log"
CRASH_READY="$SIG_DIR/crash.ready"
FIX_DONE="$SIG_DIR/fix.done"
STOP="$SIG_DIR/stop"
LAUNCH_LOG="$SIG_DIR/launch.log"

[ -d "$MOD_DIR" ] || { echo "no mod dir: $MOD_DIR"; exit 1; }
mkdir -p "$SIG_DIR"
rm -f "$CRASH_READY" "$FIX_DONE" "$STOP"
echo "$MODE" > "$SIG_DIR/phase"   # so a monitor (and the uber runner) always knows which phase is live
export JAVA_HOME="${JAVA_HOME:-/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home}"
# Keep the display awake during the run. A slept display CAN stall GLFW; a LOCKED screen does NOT
# (verified: the client boots to title with the screen locked, given a live WindowServer session).
# This is belt-and-suspenders robustness, not a hard requirement.
CAFFEINATE=""; command -v caffeinate >/dev/null 2>&1 && CAFFEINATE="caffeinate -dimsu"

echo "loop dir:   $SIG_DIR"
echo "mod:        $MOD  ($MOD_DIR)"
echo "mode:       $MODE"
echo "watchdog:   ${TIMEOUT_SECS}s per launch"
echo "stop with:  Ctrl-C   (or: touch $STOP)"
echo

cd "$MOD_DIR"
iter=0
while true; do
  [ -f "$STOP" ] && { echo "stop file present — exiting"; exit 3; }
  iter=$((iter+1))
  echo "════════════════════════════════════════════════════════════════"
  echo "[$(date '+%H:%M:%S')] iteration $iter — launching dev client (boot smoke test)…"
  echo "════════════════════════════════════════════════════════════════"
  rm -f "$CRASH_READY" "$FIX_DONE" "$LAUNCH_LOG"

  # Launch in the background so we can enforce a watchdog timeout, then wait on it.
  $CAFFEINATE ./gradlew runClient "${GRADLE_MODE_ARGS[@]}" --no-daemon --console=plain > "$LAUNCH_LOG" 2>&1 &
  GRADLE_PID=$!
  ( sleep "$TIMEOUT_SECS"
    if kill -0 "$GRADLE_PID" 2>/dev/null; then
      echo "[watchdog] ${TIMEOUT_SECS}s elapsed without reaching the title — killing launch" >> "$LAUNCH_LOG"
      pkill -P "$GRADLE_PID" 2>/dev/null; kill "$GRADLE_PID" 2>/dev/null
    fi ) &
  WATCH_PID=$!
  wait "$GRADLE_PID"; EXIT=$?
  kill "$WATCH_PID" 2>/dev/null; wait "$WATCH_PID" 2>/dev/null

  if grep -q '_BOOT_TEST: PASS' "$LAUNCH_LOG"; then
    echo
    echo "✅ CLIENT BOOTED CLEAN — reached the title screen with no crash. Done after $iter iteration(s)."
    exit 0
  fi

  echo "❌ client did not reach the title screen (gradle exit $EXIT). Writing crash digest…"
  CRASH_REPORT="$(ls -t "$MOD_DIR"/run/*/crash-reports/*.txt 2>/dev/null | head -1)"
  # Only trust the crash report if it was written DURING this launch — soft crashes (e.g. a client
  # disconnect from a bad packet) don't produce a new report, and a stale one from a prior run is
  # misleading. If it predates this run's log, ignore it and rely on the launch-log tail below.
  if [ -n "$CRASH_REPORT" ] && [ ! "$CRASH_REPORT" -nt "$LAUNCH_LOG" ]; then
    CRASH_REPORT=""
  fi
  {
    echo "### client-boot-loop iteration $iter — $(date)"
    echo "### mod: $MOD    mode: $MODE    gradle exit: $EXIT"
    echo
    if [ -n "$CRASH_REPORT" ]; then
      echo "### newest crash report: $CRASH_REPORT"
      echo "----------------------------------------------------------------"
      sed -n '1,140p' "$CRASH_REPORT"
      echo "----------------------------------------------------------------"
      echo
    else
      echo "### (no crash-report file — likely a build/compile failure or a hang; see log tail)"
      echo
    fi
    echo "### launch log — errors/exceptions (filtered):"
    grep -nE "error:|ERROR\]|Exception|Caused by|Mod loading|Failed to|FATAL|has no @SubscribeEvent|$MOD" "$LAUNCH_LOG" \
      | grep -viE '/DEBUG\]|deprecat|Watching TOML' | tail -100
  } > "$CRASH_LOG"

  echo "   crash digest -> $CRASH_LOG"
  touch "$CRASH_READY"
  echo "   waiting for Claude to fix (will relaunch when $FIX_DONE appears; Ctrl-C to stop)…"
  while [ ! -f "$FIX_DONE" ]; do
    [ -f "$STOP" ] && { echo "stop file present — exiting"; exit 3; }
    sleep 5
  done
  echo "   fix signalled — relaunching."
  echo
done
