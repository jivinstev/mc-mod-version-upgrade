# Sourced, not run:  . "$(dirname "$0")/python.sh"
#
# Makes `python3` work in the sourcing script on every OS. The python.org installer for Windows
# provides `python` and `py`, not `python3`, and Windows also ships a `python3` STUB that only opens
# the Microsoft Store -- so a candidate counts only if it actually runs Python 3. Sets $PYTHON to the
# command found (python3 on Linux and macOS, where nothing else changes). When that is not `python3`,
# an exported python3() function stands in for it, so the script's `python3 ...` lines run unchanged.
#
# On Windows it also sets PYTHONUTF8=1: Python's stdout there is cp1252, and the tools print UTF-8
# (→, ⚠). File I/O does not depend on it: every open() names its encoding (tools/check-encoding.py).
for _py in "${PYTHON:-}" python3 python "py -3"; do
  [ -n "$_py" ] || continue
  if $_py -c 'import sys; sys.exit(sys.version_info[0] != 3)' >/dev/null 2>&1; then PYTHON="$_py"; break; fi
  _py=""
done
if [ -z "$_py" ]; then
  echo "no Python 3 found (tried python3, python, py -3). Install it -- Windows: winget install --id Python.Python.3.12" >&2
  return 1 2>/dev/null || exit 1
fi
unset _py
export PYTHON
if [ "$PYTHON" != python3 ]; then
  python3() { $PYTHON "$@"; }
  export -f python3
fi
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) export PYTHONUTF8=1 ;; esac
