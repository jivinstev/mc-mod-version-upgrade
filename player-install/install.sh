#!/bin/sh
# One-line player install (tools/player-install.py), for macOS and Linux. Published per release as install.sh with
# @TAG@ and @SHA@ filled in by `tools/player-install.py --bundle`; never run this template directly.
#
#   curl -fsSL https://github.com/jivinstev/mc-mod-version-upgrade/releases/download/@TAG@/install.sh | bash -s -- OWNER/REPO
#   ... | bash -s -- OWNER/REPO --yes          (take every default, ask nothing)
#
# It checks for Python 3.9+, downloads this release's player-install.zip, checks its sha256 against the one written
# below, unpacks it under ~/.mmvu/player-install/@TAG@/ and runs the installer with your arguments.
set -eu
TAG="@TAG@"
SHA="@SHA@"
URL="${PLAYER_INSTALL_ZIP:-https://github.com/jivinstev/mc-mod-version-upgrade/releases/download/$TAG/player-install.zip}"
DIR="${MMVU_HOME:-$HOME/.mmvu}/player-install/$TAG"
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    PY="$c"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "player-install needs Python 3.9 or newer, and none was found." >&2
  case "$(uname -s)" in
    Darwin) echo "On a Mac, run  xcode-select --install  (Apple's command line tools include Python 3), then run this again." >&2 ;;
    *) echo "Install python3 with your system's package manager, then run this again." >&2 ;;
  esac
  exit 1
fi
if [ ! -f "$DIR/tools/player-install.py" ]; then
  mkdir -p "$DIR"
  tmp="$DIR/player-install.zip.part"
  case "$URL" in
    http*) curl -fsSL "$URL" -o "$tmp" ;;
    *) cp "$URL" "$tmp" ;;      # a local file: PLAYER_INSTALL_ZIP for testing; the sha256 below still decides
  esac
  got=$("$PY" -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$tmp")
  if [ "$got" != "$SHA" ]; then
    rm -f "$tmp"
    echo "player-install: the download's sha256 ($got) is not this release's ($SHA): nothing was installed" >&2
    exit 1
  fi
  "$PY" -m zipfile -e "$tmp" "$DIR"
  rm -f "$tmp"
fi
exec "$PY" "$DIR/tools/player-install.py" "$@"
