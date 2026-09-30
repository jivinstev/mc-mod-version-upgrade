#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Jason Hendrickson
# ─────────────────────────────────────────────────────────────────────────────
# run-gatec.command — macOS LaunchServices launcher for Gate C.
#
# WHY THIS EXISTS: an agent/headless shell has no WindowServer (Aqua GUI) access, and a
# child process it spawns inherits that — so `./gradlew runClient` fails GLFW ("Failed to
# find a primary monitor"). `open`-ing THIS file hands the launch to LaunchServices, which
# starts it inside the logged-in GUI session (full display access), independent of the
# caller's session. Unlike `osascript`/AppleEvents it needs NO Automation-consent prompt.
#
# The agent CAN kick off Gate C itself with:   open mods/<modid>/tools/run-gatec.command
# (then it arms a monitor on the signal dir and fixes crashes). Also double-clickable in Finder.
# Requires a logged-in desktop session; on headless Linux CI use `xvfb-run ./tools/client-validate.sh`.
# ─────────────────────────────────────────────────────────────────────────────
cd "$(dirname "$0")/.."
exec ./tools/client-validate.sh
