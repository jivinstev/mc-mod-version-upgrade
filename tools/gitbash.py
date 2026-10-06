"""The bash to run a tools/*.sh script with, from Python.

Plain `bash` everywhere but Windows. There CreateProcess searches System32 BEFORE PATH, so "bash" is
WSL's launcher, not Git Bash: with no distribution installed it prints an error in UTF-16 and the
script never runs. Git for Windows' own bash sits two levels above `git --exec-path`
(<Git>/mingw64/libexec/git-core). Standard library only.
"""
import os, pathlib, subprocess


def find_bash():
    if os.name == "nt":
        r = subprocess.run(["git", "--exec-path"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode == 0 and r.stdout.strip():
            git_root = pathlib.Path(r.stdout.strip()).parents[2]
            for c in (git_root / "bin/bash.exe", git_root / "usr/bin/bash.exe"):
                if c.is_file():
                    return str(c)
    return "bash"


BASH = find_bash()
