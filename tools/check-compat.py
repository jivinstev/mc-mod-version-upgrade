#!/usr/bin/env python3
"""Portability gate: the tools run on Linux CI, on Windows (Git Bash, cp1252) and on macOS (bash 3.2, BSD sed and
grep, Python 3.9 from the Command Line Tools). Linux CI alone passes code that breaks the other two, so this catches,
statically and in about a second, the patterns that HAVE broken them here.

    python3 tools/check-compat.py                 # every tracked .py and .sh
    python3 tools/check-compat.py FILE ...        # just these (the pre-commit hook passes the staged ones)
    python3 tools/check-compat.py --self-check

Python (every check names the platform it protects):
  py39       the file must parse as Python 3.9 (macOS's /usr/bin/python3): no `match`, no parenthesised context
             managers, no `except*`.
  tomllib    a module-level `import tomllib` (3.11+) stops the whole tool on 3.9: import it where it is used, with
             tools/minitoml.py as the fallback.
  bash-argv  ["bash", ...] / ("bash", ...): on Windows a plain `bash` is WSL's (System32 comes before PATH), not
             Git Bash -- use tools/gitbash.py's BASH.
  pass-fds   subprocess's pass_fds is POSIX-only ("pass_fds not supported on Windows").
  isoformat  datetime.fromisoformat accepts a trailing "Z" only from 3.11: .replace("Z", "+00:00") first.
Shell (.sh):
  sed-i      `sed -i 's/..'` is GNU-only: BSD sed takes the expression as -i's backup suffix. Use
             `sed -i.bak ... && rm -f FILE.bak`.
  heredoc    a heredoc inside $( ... ): macOS bash 3.2 misparses quotes in it (a whole script fails to parse). Write
             it to a file with a plain heredoc, then run that file in $( ).
  timeout    GNU coreutils' `timeout` is absent on macOS: use a watchdog (perl -e 'alarm N; exec @ARGV', or Python).
  gnu-only   `grep -P`, `date -d`, `stat -c`, `readlink -e`: GNU-only options.
A line can opt out with a trailing `# compat-ok: <reason>` (say why it is safe). Exit 0 clean, 1 findings, 2 bad
input. Standard library only; runs on Python 3.9.
"""
import ast, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OK = re.compile(r"#\s*compat-ok:\s*\S")

PY_RULES = [
    ("bash-argv", re.compile(r"""[\[(]\s*["']bash["']\s*,"""),
     "a plain \"bash\" argv: on Windows that is WSL's bash, not Git Bash -- use tools/gitbash.py's BASH"),
    ("pass-fds", re.compile(r"\bpass_fds\s*="),
     "subprocess pass_fds is POSIX-only (Windows: \"pass_fds not supported\") -- guard it with os.name"),
    ("isoformat", re.compile(r"fromisoformat\((?!.*replace\(\s*[\"']Z)"),
     "datetime.fromisoformat takes a trailing \"Z\" only from Python 3.11 -- .replace(\"Z\", \"+00:00\") first"),
]
SH_RULES = [
    ("sed-i", re.compile(r"""(^|[;&|(\s])sed\s+-i\s+['"]"""),
     "GNU-only `sed -i '...'` (BSD sed reads the expression as the backup suffix) -- use sed -i.bak ... && rm -f FILE.bak"),
    ("heredoc", re.compile(r"\$\([^)]*<<-?\s*['\"]?\w"),
     "a heredoc inside $( ): macOS bash 3.2 misparses quotes in it -- write it to a file first, then run that in $( )"),
    ("timeout", re.compile(r"(^|[;&|(\s])timeout\s+-?[0-9]"),
     "GNU `timeout` does not exist on macOS -- use a watchdog (perl -e 'alarm N; exec @ARGV' or Python)"),
    ("gnu-only", re.compile(r"(^|[;&|(\s])(grep\s+(-\w*P\b|--perl-regexp)|date\s+-d\b|stat\s+-c\b|readlink\s+-e\b)"),
     "a GNU-only option (grep -P, date -d, stat -c, readlink -e) -- BSD tools on macOS lack it"),
]
SELF = "tools/check-compat.py"


def tracked():
    r = subprocess.run(["git", "-C", str(ROOT), "ls-files", "*.py", "*.sh"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return [ROOT / f for f in r.stdout.splitlines() if f]


def strip_strings_comments(line):
    """The code part of a Python line, roughly: comments dropped (string contents are kept -- argv literals live there)."""
    out, q = [], None
    i = 0
    while i < len(line):
        c = line[i]
        if q:
            out.append(c)
            if c == "\\":
                out.append(line[i + 1:i + 2]); i += 2; continue
            if c == q:
                q = None
        elif c in "\"'":
            q = c; out.append(c)
        elif c == "#":
            break
        else:
            out.append(c)
        i += 1
    return "".join(out)


def check_python(rel, text):
    found = []
    try:
        tree = ast.parse(text, filename=rel, feature_version=(3, 9))
    except SyntaxError as e:
        return [(rel, e.lineno or 0, "py39", f"does not parse as Python 3.9 (macOS's /usr/bin/python3): {e.msg}")]
    for node in tree.body:                              # a module-level tomllib import stops the tool on 3.9/3.10
        names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
            [node.module] if isinstance(node, ast.ImportFrom) else []
        if "tomllib" in names and not OK.search(text.splitlines()[node.lineno - 1]):
            found.append((rel, node.lineno, "tomllib", "module-level `import tomllib` (Python 3.11+): import it where "
                          "it is used, falling back to tools/minitoml.py, so the tool still starts on 3.9"))
    if rel == SELF:
        return found
    for n, line in enumerate(text.splitlines(), 1):
        if OK.search(line):
            continue
        code = strip_strings_comments(line)
        for key, rx, msg in PY_RULES:
            if key == "bash-argv" and rel == "tools/gitbash.py":
                continue
            if rx.search(code):
                found.append((rel, n, key, msg))
    return found


def check_shell(rel, text):
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        if OK.search(line) or line.lstrip().startswith("#"):
            continue
        for key, rx, msg in SH_RULES:
            if rx.search(line):
                found.append((rel, n, key, msg))
    return found


def check(paths):
    found = []
    for p in paths:
        p = pathlib.Path(p).resolve()
        if not p.is_file() or p.suffix not in (".py", ".sh"):
            continue
        try:
            rel = p.relative_to(ROOT).as_posix()
        except ValueError:
            rel = p.as_posix()
        text = p.read_text(encoding="utf-8", errors="replace")
        found += check_python(rel, text) if p.suffix == ".py" else check_shell(rel, text)
    return found


def self_check():
    bad = []
    def expect(src, kind, keys):
        got = {k for _f, _n, k, _m in (check_python("x.py", src) if kind == "py" else check_shell("x.sh", src))}
        if got != set(keys):
            bad.append(f"{kind} {src!r}: got {sorted(got)}, want {sorted(keys)}")
    expect("match x:\n    case 1: pass\n", "py", ["py39"])
    expect("import tomllib\n", "py", ["tomllib"])
    expect("def f():\n    import tomllib\n", "py", [])                       # imported where it is used: fine
    expect("try:\n    import tomllib\nexcept ImportError:\n    tomllib = None\n", "py", [])
    expect('subprocess.run(["bash", "gradlew"])\n', "py", ["bash-argv"])
    expect('subprocess.run([BASH, "gradlew"])  # the Git Bash\n', "py", [])
    expect('x = "bash"  # a word in a string is not an argv\n', "py", [])
    expect('subprocess.Popen(a, pass_fds=(3,))\n', "py", ["pass-fds"])
    expect('subprocess.Popen(a, pass_fds=(3,))  # compat-ok: inside an os.name != "nt" branch\n', "py", [])
    expect('datetime.fromisoformat(s)\n', "py", ["isoformat"])
    expect('datetime.fromisoformat(s.replace("Z", "+00:00"))\n', "py", [])
    expect('datetime.fromisoformat(str(d["x"]).replace("Z", "+00:00"))\n', "py", [])
    expect("sed -i 's/a/b/' f\n", "sh", ["sed-i"])
    expect("sed -i.bak 's/a/b/' f && rm -f f.bak\n", "sh", [])
    expect("out=\"$(python3 - <<'PY'\n", "sh", ["heredoc"])
    expect('grep -q x <<<"$out"\n', "sh", [])                                   # a here-string is fine
    expect('x="$(tail -3 <<<"$out")"\n', "sh", [])
    expect("timeout 30 java -jar x\n", "sh", ["timeout"])
    expect("grep -P '\\d' f\n", "sh", ["gnu-only"])
    expect("grep -E 'x' f; date -u +%s\n", "sh", [])
    expect("# sed -i 's/a/b/' f is what NOT to write\n", "sh", [])            # a comment is not code
    print("\n".join(bad) or "self-check: OK")
    return 1 if bad else 0


def main(argv):
    if argv[:1] == ["--self-check"]:
        return self_check()
    if any(a.startswith("-") for a in argv):
        print(__doc__, file=sys.stderr)
        return 2
    paths = [pathlib.Path(a) for a in argv] if argv else tracked()
    found = check(paths)
    for rel, n, key, msg in found:
        print(f"{rel}:{n}: [{key}] {msg}")
    print(f"check-compat: {'FAIL -- ' + str(len(found)) + ' finding(s)' if found else 'PASS'} "
          f"({len(paths)} file(s); macOS bash 3.2 / BSD tools / Python 3.9, and Windows)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
