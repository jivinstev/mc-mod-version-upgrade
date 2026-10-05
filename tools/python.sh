# Sourced, not run:  . "$(dirname "$0")/python.sh"
#
# Makes `python3` work in the sourcing script on every OS. The python.org installer for Windows
# provides `python` and `py`, not `python3`, and Windows also ships a `python3` STUB that only opens
# the Microsoft Store -- so a candidate counts only if it actually runs Python 3. On Linux and macOS
# that is python3 and nothing else changes. Otherwise $PYTHON becomes the interpreter's full path
# ($PYTHON itself is tried first: setup gives Claude Code one on Windows), and an exported python3()
# function stands in for the missing command, so the script's `python3 ...` lines run unchanged.
#
# On Windows it also sets PYTHONUTF8=1: Python's stdout there is cp1252, and the tools print UTF-8
# (→, ⚠). File I/O does not depend on it: every open() names its encoding (tools/check-encoding.py).
_py_ok() { "$@" -c 'import sys; sys.exit(sys.version_info[0] != 3)' >/dev/null 2>&1; }
if [ -n "${PYTHON:-}" ] && [ "$PYTHON" != python3 ] && _py_ok "$PYTHON"; then
  :
elif _py_ok python3; then
  PYTHON=python3
elif _py_ok python; then
  PYTHON="$(python -c 'import sys; print(sys.executable)')"
elif _py_ok py -3; then
  PYTHON="$(py -3 -c 'import sys; print(sys.executable)')"
else
  echo "no Python 3 found (tried python3, python, py -3). Install it -- Windows: winget install --id Python.Python.3.12" >&2
  unset -f _py_ok
  return 1 2>/dev/null || exit 1
fi
unset -f _py_ok
export PYTHON
if [ "$PYTHON" != python3 ]; then
  python3() { "$PYTHON" "$@"; }
  export -f python3
fi
# On Windows also: tools/winpy/sitecustomize.py makes print() write LF, not CRLF, so `$(...)` and
# `grep 'x$'` work on the tools' output.
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    export PYTHONUTF8=1
    _winpy="$(cygpath -m "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/winpy")"
    case ";${PYTHONPATH:-};" in *";$_winpy;"*) ;; *) export PYTHONPATH="$_winpy${PYTHONPATH:+;$PYTHONPATH}" ;; esac
    unset _winpy ;;
esac
