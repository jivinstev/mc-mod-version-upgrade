#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Jason Hendrickson
# ─────────────────────────────────────────────────────────────────────────────
# client-validate.sh — THE one command for Gate C. Runs EVERY client-validation
# phase in sequence, each looping crash → (Claude fixes) → relaunch until it passes,
# then advancing to the next:
#
#     launch  →  spawn  →  battle  →  gauntlet
#
# Run this in YOUR Mac Terminal (it needs your GUI session for OpenGL/GLFW). You run
# ONE command; the Claude session watches the shared signal dir and fixes crashes as
# they surface across ALL phases — you don't touch anything between phases.
#
# Signal protocol (all under $SIG_DIR, shared with client-boot-loop.sh):
#   phase       <- current phase name (this script / the loop writes it)
#   crash.log   <- crash digest for the current phase (includes the mode)
#   crash.ready <- a crash is waiting; Claude reads crash.log, fixes, touches fix.done
#   fix.done    <- Claude signals "fixed + recompiled, relaunch this phase"
#   all-done    <- touched here when EVERY phase has passed (overall success)
#   stop        <- touch it (or Ctrl-C) to abort
#
# Env knobs: PHASES="launch spawn battle gauntlet" (subset/reorder), SIG_DIR, MOD,
#            BATTLE_SECONDS, BATTLE_COUNT (passed through to the battle phase).
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

# This script lives PER-MOD at <repo>/mods/<modid>/tools/ — derive the mod from its own location.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"          # <repo>/mods/<modid>/tools
MOD="${MOD:-$(basename "$(cd "$HERE/.." && pwd)")}"
# MC is forwarded to client-boot-loop.sh, which forwards it to Gradle. Exported rather than merely
# read, so a subset run (PHASES=...) gates the same target for every phase.
export MC="${MC:-}"
# Per-target as well as per-mod, for the reason the run directory is (W11) -- the second target's
# evidence must not replace the first's. Appends nothing on a single-target port.
export SIG_DIR="${SIG_DIR:-/tmp/${MOD}-clientloop${MC:+-$MC}}"  # export so client-boot-loop.sh shares it
export MOD
PHASES="${PHASES:-launch spawn battle gauntlet}"

mkdir -p "$SIG_DIR"
rm -f "$SIG_DIR/all-done" "$SIG_DIR/stop"

echo "════════════════════════════════════════════════════════════════"
echo "▶ client-validate — all phases in one run"
echo "  mod:     $MOD"
echo "  phases:  $PHASES"
echo "  sigdir:  $SIG_DIR   (Claude monitors this)"
echo "  stop:    Ctrl-C   (or: touch $SIG_DIR/stop)"
echo "════════════════════════════════════════════════════════════════"

for mode in $PHASES; do
  echo
  echo "▓▓▓▓▓▓▓▓ PHASE: $mode ▓▓▓▓▓▓▓▓"
  echo "$mode" > "$SIG_DIR/phase"
  if ! "$HERE/client-boot-loop.sh" "$mode"; then
    echo "✗ phase '$mode' aborted (stop/interrupt) — halting before the remaining phases."
    exit 1
  fi
  echo "✓ phase '$mode' PASSED"
done

rm -f "$SIG_DIR/phase"
touch "$SIG_DIR/all-done"
echo
echo "🏁🏁🏁 ALL CLIENT VALIDATION PHASES PASSED — $PHASES 🏁🏁🏁"
