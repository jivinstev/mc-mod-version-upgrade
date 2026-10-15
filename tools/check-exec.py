#!/usr/bin/env python3
"""Fail on a command list whose program name only works on Linux and macOS.

    python3 tools/check-exec.py          # every tracked .py file
    python3 tools/check-exec.py FILE...  # just these

Windows starts a program with CreateProcess, which searches System32 BEFORE PATH and runs only real
executables. So a command list (the argv given to subprocess, or built to be given to it) that begins with:
    "bash" / "sh"          runs WSL's launcher (System32\\bash.exe), not Git Bash: with no WSL distribution the
                           script never runs (#109). Use gitbash.BASH (plain "bash" off Windows).
    "./x" (a script path)  is not an executable to CreateProcess: run it through BASH, e.g. [BASH, "gradlew", ...].
    "python" / "python3"   is the Microsoft Store stub on a fresh Windows, or missing. Use sys.executable.
This flags every list or tuple literal whose first element is one of those strings. A deliberate exception
(a command printed for a person, a fixture) carries `# exec-ok: <why>` on the same line.
Standard library only.
"""
import ast, pathlib, subprocess, sys, warnings

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIX = {"bash": "gitbash.BASH", "sh": "gitbash.BASH", "python": "sys.executable", "python3": "sys.executable"}


def problems(path):
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()
    out = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)   # another file's escape-sequence warning is not ours to report
        tree = ast.parse(src, str(path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple)) or not node.elts:
            continue
        first = node.elts[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        prog = first.value
        if prog in FIX:
            why = f'"{prog}" as a program: use {FIX[prog]}'
        elif prog.startswith("./") and len(prog) > 2:
            why = f'"{prog}" as a program: run it through gitbash.BASH ([BASH, "{prog[2:]}", ...])'
        else:
            continue
        if "exec-ok:" in lines[node.lineno - 1]:
            continue
        out.append(f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}:{node.lineno}: {why}")
    return out


def tracked():
    r = subprocess.run(["git", "-C", str(ROOT), "ls-files", "*.py"], capture_output=True, text=True, encoding="utf-8")
    return [ROOT / l for l in r.stdout.splitlines() if l.strip() and (ROOT / l).is_file()]


def main():
    files = [pathlib.Path(f).resolve() for f in sys.argv[1:]] or tracked()
    bad = [p for f in files for p in problems(f)]
    for b in bad:
        print(b)
    print(f"check-exec: {'FAIL' if bad else 'PASS'} ({len(files)} file(s), {len(bad)} finding(s))")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
