@echo off
rem First-run setup from cmd.exe or PowerShell: the same as `./setup` in Git Bash.
rem See tools\setup.py for what it asks, what it writes, and why re-running is safe.
rem Finds Python 3 the way tools/python.sh does: python3, then python, then py -3 -- each only if it
rem really runs Python 3 (Windows ships a python3 stub that only opens the Microsoft Store).
setlocal
set "PYTHONUTF8=1"
set "PY="
python3 -c "import sys; sys.exit(sys.version_info[0] != 3)" >nul 2>&1 && set "PY=python3"
if not defined PY (python -c "import sys; sys.exit(sys.version_info[0] != 3)" >nul 2>&1 && set "PY=python")
if not defined PY (py -3 -c "import sys" >nul 2>&1 && set "PY=py -3")
if not defined PY (
  echo setup: no Python 3 found. Install it with:  winget install --id Python.Python.3.12
  exit /b 1
)
%PY% "%~dp0tools\setup.py" %*
exit /b %ERRORLEVEL%
