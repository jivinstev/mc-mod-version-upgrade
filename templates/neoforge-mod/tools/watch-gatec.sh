#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 jivinstev
# ─────────────────────────────────────────────────────────────────────────────
# watch-gatec.sh — poll the Gate C / smoke-harness signal dir until a terminal
# state, print the outcome (+ a crash digest if any), and exit with an ACCURATE
# code so the harness's pass/fail flag means something:
#
#   ALL-DONE (every phase passed) -> exit 0
#   STOPPED  (someone touched stop) -> exit 0
#   CRASH    (a phase crashed)     -> exit 1   (genuinely failed)
#   TIMEOUT  (no signal in time)   -> exit 3   (genuinely stuck)
#
# WHY THIS EXISTS: hand-rolled monitor loops that END with `[ -f "$SIG/crash.ready" ] && …`
# return exit 1 on the SUCCESS path (crash.ready is ABSENT when the run passed), so a green
# run gets mislabeled "background shell failed / exit code 1". A script with explicit exits
# per outcome removes that false signal. Use this instead of inlining the loop.
#
# Usage:  SIG_DIR=/tmp/<modid>-clientloop ./tools/watch-gatec.sh [maxSeconds] [intervalSeconds]
#         (SIG_DIR defaults to /tmp/smokeharness-clientloop; max 900s, interval 12s)
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
SIG="${SIG_DIR:-/tmp/smokeharness-clientloop}"
MAX="${1:-900}"
INTERVAL="${2:-12}"
elapsed=0

while :; do
  if [ -f "$SIG/crash.ready" ]; then
    echo "RESULT=CRASH phase=$(cat "$SIG/phase" 2>/dev/null)"
    echo "--- crash digest ($SIG/crash.log) ---"
    head -40 "$SIG/crash.log" 2>/dev/null
    exit 1
  fi
  if [ -f "$SIG/all-done" ]; then
    echo "RESULT=ALL-DONE (all phases passed)"
    exit 0
  fi
  if [ -f "$SIG/stop" ]; then
    echo "RESULT=STOPPED"
    exit 0
  fi
  if [ "$elapsed" -ge "$MAX" ]; then
    echo "RESULT=TIMEOUT after ${MAX}s (phase=$(cat "$SIG/phase" 2>/dev/null))"
    exit 3
  fi
  sleep "$INTERVAL"
  elapsed=$((elapsed + INTERVAL))
done
