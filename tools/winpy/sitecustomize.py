"""Windows only: tools/python.sh and setup's python3 shim put this folder on PYTHONPATH there, and
nowhere else.

Windows Python writes "\r\n" for every "\n" it prints. In Git Bash a `$(python3 tools/x.py)` then ends
in a stray \r, and `python3 tools/x.py | grep 'x$'` matches nothing -- the tools' output is read by
bash all the time. So stdout and stderr write LF, as on every other OS. (Files are a separate matter:
each open() that bash will read passes newline="\n" itself.)
"""
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(newline="\n")
    except (AttributeError, ValueError):          # replaced or detached stream: leave it
        pass
