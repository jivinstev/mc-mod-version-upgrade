#!/usr/bin/env python3
"""Fail on any text-mode file I/O that leaves the encoding to the platform.

    python3 tools/check-encoding.py          # every tracked .py file
    python3 tools/check-encoding.py FILE...  # just these

Windows Python reads and writes files as cp1252 by default, and the catalogue, the docs and most of
the tools' output are UTF-8 (→, ⚠, ≈). One `open(p)` without `encoding=` is a UnicodeDecodeError on
the first Windows machine that runs it. This flags, in every Python file:
    open(...)              text mode (no "b" in the mode) and no encoding=
    X.open(...)            same, for pathlib
    X.read_text()          no encoding=
    X.write_text(...)      no encoding=
    subprocess.*(text=True / universal_newlines=True) with no encoding=
A deliberate exception carries `# encoding-ok: <why>` on the same line.
Standard library only.
"""
import ast, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SUBPROCESS = {"run", "check_output", "Popen", "call", "check_call"}


def mode_of(call, pos):
    if len(call.args) > pos and isinstance(call.args[pos], ast.Constant):
        return call.args[pos].value
    for k in call.keywords:
        if k.arg == "mode" and isinstance(k.value, ast.Constant):
            return k.value.value
    return "r" if len(call.args) <= pos and not any(k.arg == "mode" for k in call.keywords) else None


def problems(path):
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()
    out = []
    for node in ast.walk(ast.parse(src, str(path))):
        if not isinstance(node, ast.Call):
            continue
        kws = {k.arg for k in node.keywords}
        if "encoding" in kws or None in kws:          # None: **kwargs, can't tell -- trust it
            continue
        f, what = node.func, None
        if isinstance(f, ast.Name) and f.id == "open":
            m = mode_of(node, 1)
            if m is None or "b" not in m:
                what = "open() in text mode without encoding="
        elif isinstance(f, ast.Attribute) and f.attr == "open" and not (
                isinstance(f.value, ast.Name) and f.value.id in ("os", "zipfile", "tarfile", "io", "gzip", "zf", "z")):
            m = mode_of(node, 0)
            if m is not None and "b" not in m and not node.args[1:]:
                what = ".open() in text mode without encoding="
        elif isinstance(f, ast.Attribute) and f.attr in ("read_text", "write_text"):
            what = f".{f.attr}() without encoding="
        elif isinstance(f, ast.Attribute) and f.attr in SUBPROCESS and isinstance(f.value, ast.Name) \
                and f.value.id == "subprocess":
            for k in node.keywords:
                if k.arg in ("text", "universal_newlines") and isinstance(k.value, ast.Constant) and k.value.value:
                    what = f"subprocess.{f.attr}({k.arg}=True) without encoding="
        if what and "encoding-ok" not in lines[node.lineno - 1]:
            shown = path.relative_to(ROOT) if ROOT in path.parents else path
            out.append(f"{shown}:{node.lineno}: {what}")
    return out


def main(argv):
    if argv:
        files = [pathlib.Path(a).resolve() for a in argv]
    else:
        ls = subprocess.run(["git", "-C", str(ROOT), "ls-files", "*.py"], capture_output=True,
                            text=True, encoding="utf-8", check=True).stdout.split()
        files = [ROOT / f for f in ls]
    found = [p for f in files for p in problems(f)]
    for p in found:
        print(p)
    if found:
        print(f"check-encoding: FAIL -- {len(found)} call(s) leave the encoding to the platform "
              "(cp1252 on Windows). Pass encoding=\"utf-8\".", file=sys.stderr)
        return 1
    print(f"check-encoding: PASS -- {len(files)} file(s), every text I/O names its encoding")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
