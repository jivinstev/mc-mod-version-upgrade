#!/bin/sh
# One-line player install (tools/player-install.py), for macOS and Linux. Published per release as install.sh with
# @TAG@, @SHA@ and the portable-Python pins (player-install/portable-python.txt) filled in by
# `tools/player-install.py --bundle`; never run this template directly.
#
#   curl -fsSL https://github.com/jivinstev/mc-mod-version-upgrade/releases/download/@TAG@/install.sh | bash -s -- OWNER/REPO
#   ... | bash -s -- OWNER/REPO --yes          (take every default, ask nothing)
#   ... | bash -s -- OWNER/REPO --keep-python  (keep the portable Python, if one was downloaded, for the next run)
#
# It uses a Python 3.9+ that is already here. When there is none, it downloads a portable Python (pinned below by URL
# and sha256, checked before anything is unpacked) into a temporary folder, uses it for this install only and removes
# it afterwards. On a Mac it never runs Apple's /usr/bin/python3 unless the developer tools are installed: without
# them that python3 is a stub that asks to install them. Then it downloads this release's player-install.zip, checks
# its sha256 against the one written below, unpacks it under ~/.mmvu/player-install/@TAG@/ and runs the installer with
# your arguments (--keep-python is the bootstrap's own and is not passed on).
#
# Test hooks (CI and tools/player-install.py's tests only; a player sets none): PLAYER_INSTALL_ZIP and
# PLAYER_INSTALL_PYTHON_FILE are local files used in place of the two downloads (the sha256s below still decide);
# PLAYER_INSTALL_TEST_UNAME, PLAYER_INSTALL_TEST_STUB_DIR and PLAYER_INSTALL_TEST_XCODE_SELECT stand in for
# `uname -s`, /usr/bin and xcode-select.
set -eu
TAG="@TAG@"
SHA="@SHA@"
# platform, size in MB, sha256, URL
PINS='@PYTHON_PINS@'
URL="${PLAYER_INSTALL_ZIP:-https://github.com/jivinstev/mc-mod-version-upgrade/releases/download/$TAG/player-install.zip}"
HOMEDIR="${MMVU_HOME:-$HOME/.mmvu}"
DIR="$HOMEDIR/player-install/$TAG"
OS="${PLAYER_INSTALL_TEST_UNAME:-$(uname -s)}"
STUB_DIR="${PLAYER_INSTALL_TEST_STUB_DIR:-/usr/bin}"
XCODE_SELECT="${PLAYER_INSTALL_TEST_XCODE_SELECT:-xcode-select}"

KEEP=0
for a do
  shift
  if [ "$a" = "--keep-python" ]; then KEEP=1; else set -- "$@" "$a"; fi
done

usable() {    # a Python 3.9+ that really runs
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1
}

realdir() {   # a directory with every symlink resolved (macOS temp dirs are /var -> /private/var)
  (cd -P "$1" 2>/dev/null && pwd -P)
}

resolve() {   # a file with every symlink resolved
  p=$1; n=0
  while [ -L "$p" ] && [ "$n" -lt 40 ]; do
    t=$(readlink "$p")
    case $t in /*) p=$t ;; *) p=$(dirname "$p")/$t ;; esac
    n=$((n + 1))
  done
  d=$(realdir "$(dirname "$p")") || d=$(dirname "$p")
  printf '%s/%s\n' "$d" "$(basename "$p")"
}

apple_stub() {    # a Mac's /usr/bin/python* (or a link to it), and no developer tools: running it would pop a dialog
  [ "$OS" = Darwin ] || return 1
  r=$(resolve "$1")
  s=$(realdir "$STUB_DIR") || s=$STUB_DIR
  case $r in "$s"/python*) ;; *) return 1 ;; esac
  d=$("$XCODE_SELECT" -p 2>/dev/null) || return 0
  [ -n "$d" ] && [ -d "$d" ] && return 1
  return 0
}

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | cut -d' ' -f1
  else openssl dgst -sha256 -r "$1" | cut -d' ' -f1
  fi
}

PY=""
for c in python3 python; do
  p=$(command -v "$c" 2>/dev/null) || continue
  case $p in /*) ;; *) continue ;; esac
  if apple_stub "$p"; then continue; fi
  if usable "$p"; then PY=$p; break; fi
done

PORTABLE=0
WORK=""
if [ -z "$PY" ]; then
  case $OS in Darwin) o=macos ;; Linux) o=linux ;; *) o=$OS ;; esac
  case $(uname -m) in
    x86_64|amd64) m=x86_64 ;;
    arm64|aarch64) if [ "$o" = macos ]; then m=arm64; else m=aarch64; fi ;;
    *) m=$(uname -m) ;;
  esac
  pin=$(printf '%s\n' "$PINS" | awk -v k="$o-$m" '$1 == k { print $2, $3, $4; exit }')
  if [ -z "$pin" ]; then
    echo "player-install needs Python 3.9 or newer, none was found, and there is no portable Python for $o-$m." >&2
    echo "Install python3 with your system's package manager, then run this again." >&2
    exit 1
  fi
  MB=${pin%% *}; rest=${pin#* }; PSHA=${rest%% *}; PURL=${rest#* }
  KEPT="$HOMEDIR/python/$(printf '%s' "$PSHA" | cut -c1-16)"
  if usable "$KEPT/python/bin/python3"; then
    PY="$KEPT/python/bin/python3"           # kept by an earlier --keep-python
  else
    if [ "$KEEP" = 1 ]; then
      echo "No Python 3.9+ here: downloading a portable Python (about $MB MB) for this install; --keep-python keeps it in $KEPT."
      rm -rf "$KEPT"; mkdir -p "$KEPT"; WORK=$KEPT
    else
      echo "No Python 3.9+ here: downloading a portable Python (about $MB MB) just for this install; it is removed afterwards."
      WORK=$(mktemp -d "${TMPDIR:-/tmp}/mmvu-python.XXXXXX")
      trap 'rm -rf "$WORK"' EXIT
      trap 'rm -rf "$WORK"; exit 130' INT TERM HUP
    fi
    f="$WORK/python.tar.gz"
    if [ -n "${PLAYER_INSTALL_PYTHON_FILE:-}" ]; then cp "$PLAYER_INSTALL_PYTHON_FILE" "$f"; else curl -fsSL "$PURL" -o "$f"; fi
    got=$(sha256 "$f")
    if [ "$got" != "$PSHA" ]; then
      rm -rf "$WORK"
      echo "player-install: the portable Python's sha256 ($got) is not the pinned one ($PSHA): nothing was run, nothing was installed" >&2
      exit 1
    fi
    tar -xzf "$f" -C "$WORK"
    rm -f "$f"
    PY="$WORK/python/bin/python3"
    if ! usable "$PY"; then
      [ "$KEEP" = 1 ] && rm -rf "$WORK"
      echo "player-install: the portable Python does not run on this machine ($o-$m): install python3 3.9+ with your system's package manager, then run this again" >&2
      exit 1
    fi
  fi
  PORTABLE=1
  if [ -z "${SSL_CERT_FILE:-}" ]; then
    # A standalone build's OpenSSL may not know where this system keeps its trusted certificates: name the system's
    # own bundle (macOS: /etc/ssl/cert.pem). It adds to the default certificate paths and replaces nothing.
    for c in /etc/ssl/cert.pem /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt \
             /etc/ssl/ca-bundle.pem /etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem; do
      if [ -f "$c" ]; then SSL_CERT_FILE=$c; export SSL_CERT_FILE; break; fi
    done
  fi
fi

if [ ! -f "$DIR/tools/player-install.py" ]; then
  mkdir -p "$DIR"
  tmp="$DIR/player-install.zip.part"
  case "$URL" in
    http*) curl -fsSL "$URL" -o "$tmp" ;;
    *) cp "$URL" "$tmp" ;;      # a local file: PLAYER_INSTALL_ZIP for testing; the sha256 below still decides
  esac
  got=$(sha256 "$tmp")
  if [ "$got" != "$SHA" ]; then
    rm -f "$tmp"
    echo "player-install: the download's sha256 ($got) is not this release's ($SHA): nothing was installed" >&2
    exit 1
  fi
  "$PY" -m zipfile -e "$tmp" "$DIR"
  rm -f "$tmp"
fi
if [ "$PORTABLE" = 0 ]; then
  exec "$PY" "$DIR/tools/player-install.py" "$@"
fi
# not exec: the portable Python is removed after it (the EXIT trap). Its stdin is not this script's: under `curl | bash`
# the rest of the script is still in that pipe, and the installer asks its questions on /dev/tty anyway.
rc=0
"$PY" "$DIR/tools/player-install.py" "$@" </dev/null || rc=$?
exit "$rc"
