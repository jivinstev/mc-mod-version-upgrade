#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Installs what a Claude Code cloud session lacks, only if it is missing:
#   xvfb   a virtual screen + software OpenGL, for the real-client test (Gate C)
#   java25 Java 25, which Minecraft 26.x needs (Java 21 is already there)
#
#   cloud/ensure.sh            # both
#   cloud/ensure.sh xvfb       # just one
#
# Why not the environment's setup script: that blocks the session from opening, and its
# cache is not reliable, so it was slow every time. Instead the session-start hook runs this
# in the background, and anything that needs a tool runs it again, which waits for the
# background run (a lock) and then has nothing left to do. Safe to run any time.
# On your own computer it does nothing: install tools yourself there.
set -uo pipefail
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0
want="${1:-all}"
LOCK="${TMPDIR:-/tmp}/cloud-ensure.lock"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "cloud/ensure.sh: another install is running (started at session start); waiting for it..."
  flock 9
fi

has_xvfb() { command -v xvfb-run >/dev/null; }
has_java25() { ls -d /usr/lib/jvm/*25*/bin/javac >/dev/null 2>&1; }

todo=()
case "$want" in all|xvfb) has_xvfb || todo+=(xvfb) ;; esac
case "$want" in all|java25) has_java25 || todo+=(java25) ;; esac
[ "${#todo[@]}" -eq 0 ] && exit 0

if [ "$(id -u)" != 0 ]; then
  echo "cloud/ensure.sh: need root to install ${todo[*]}; this cloud session is not root." >&2
  exit 1
fi
echo "cloud/ensure.sh: installing ${todo[*]} (first time in this session, about a minute or two)..."
# apt's chatter (and harmless warnings such as debconf's "apt-utils is not installed") goes to a
# log, shown only when something fails.
APTLOG="${TMPDIR:-/tmp}/cloud-ensure-apt.log"
: > "$APTLOG"
fail() { tail -n 20 "$APTLOG" >&2; echo "cloud/ensure.sh: FAILED: $1" >&2; echo "  Is the host allowed? Run: python3 cloud/check.py" >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get update -q >>"$APTLOG" 2>&1 || fail "apt-get update"
for t in "${todo[@]}"; do
  case "$t" in
    xvfb)
      apt-get install -y -q xvfb mesa-utils libgl1-mesa-dri >>"$APTLOG" 2>&1 || fail "installing xvfb"
      echo "cloud/ensure.sh: xvfb installed" ;;
    java25)
      install -d /etc/apt/keyrings
      curl -fsSL https://packages.adoptium.net/artifactory/api/gpg/key/public \
        | gpg --dearmor --yes -o /etc/apt/keyrings/adoptium.gpg || fail "fetching the Adoptium key (packages.adoptium.net)"
      echo "deb [signed-by=/etc/apt/keyrings/adoptium.gpg] https://packages.adoptium.net/artifactory/deb $(. /etc/os-release; echo "$VERSION_CODENAME") main" \
        > /etc/apt/sources.list.d/adoptium.list
      apt-get update -q >>"$APTLOG" 2>&1 || fail "apt-get update (Adoptium)"
      apt-get install -y -q temurin-25-jdk >>"$APTLOG" 2>&1 || fail "installing temurin-25-jdk"
      echo "cloud/ensure.sh: Java 25 installed" ;;
  esac
done
