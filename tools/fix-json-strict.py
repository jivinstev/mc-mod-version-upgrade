#!/usr/bin/env python3
"""Make a mod's shipped JSON strict, so 26.x loads it (CATALOG §S8).

    python3 tools/fix-json-strict.py --work mods/<modid> [--check]

Up to 1.21.1 Minecraft parsed lang, model and data JSON leniently; 26.x parses it strictly and SKIPS a
file that is not strict JSON with one WARN line -- for a lang file, that is every name in the mod shown
as its raw key. Authors leave lenient-only shapes behind on purpose (a `// "commented.out": "line"`),
and nothing on 1.21.1 ever complains. Measured: a shield mod's en_us.json carried one `//` line.

Repairs only what has one meaning, byte for byte (the file's own line endings are kept):
  - a UTF-8 byte-order mark;
  - whole-line `//` comments (a line whose first non-blank characters are `//`; never a `//` inside a
    string, which is a URL);
  - a trailing comma before `}` or `]`.
A file is rewritten only when the result parses; anything else is REFUSED and listed by name, never
guessed at (§S1b). --check repairs nothing and exits 1 if any file needs repair or is refused.
Exit 0 when every JSON file under src/main/resources parses strictly. Standard library only.
"""
import argparse, json, pathlib, re, sys


def strict_ok(b):
    try:
        json.loads(b.decode("utf-8"))
        return True
    except (ValueError, UnicodeDecodeError):
        return False


def _drop_trailing_commas(text):
    """Remove a comma followed (across whitespace) by } or ], outside strings."""
    out, i, in_str, esc = [], 0, False, False
    while i < len(text):
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True; out.append(c); i += 1; continue
        if c == ",":
            j = i + 1
            while j < len(text) and text[j] in " \t\r\n":
                j += 1
            if j < len(text) and text[j] in "}]":
                i += 1
                continue
        out.append(c); i += 1
    return "".join(out)


def repair(b):
    """-> repaired bytes, or None when no safe repair makes it parse."""
    if b.startswith(b"\xef\xbb\xbf"):
        b = b[3:]
    lines = b.split(b"\n")
    lines = [l for l in lines if not l.lstrip().startswith(b"//")]
    b2 = b"\n".join(lines)
    if strict_ok(b2):
        return b2
    try:
        b3 = _drop_trailing_commas(b2.decode("utf-8")).encode("utf-8")
    except UnicodeDecodeError:
        return None
    return b3 if strict_ok(b3) else None


def scan(work, apply):
    fixed, refused = [], []
    res = pathlib.Path(work) / "src/main/resources"
    for f in sorted(res.rglob("*.json")) + sorted(res.rglob("*.mcmeta")):
        b = f.read_bytes()
        if strict_ok(b):
            continue
        r = repair(b)
        rel = f.relative_to(res).as_posix()
        if r is None:
            refused.append(rel)
        else:
            fixed.append(rel)
            if apply:
                f.write_bytes(r)
    return fixed, refused


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work"); ap.add_argument("--check", action="store_true"); ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    fixed, refused = scan(a.work, apply=not a.check)
    verb = "need repair" if a.check else "repaired"
    if fixed:
        print(f"fix-json-strict: {len(fixed)} file(s) {verb} (lenient-only JSON that 26.x skips): " + ", ".join(fixed[:8]))
    if refused:
        print(f"fix-json-strict: {len(refused)} file(s) are not JSON and have no safe repair; 26.x will skip "
              "them (fix by hand, §S8): " + ", ".join(refused[:8]))
    if not fixed and not refused:
        print("fix-json-strict: every JSON file parses strictly")
    return 1 if refused or (a.check and fixed) else 0


def self_check():
    ok = repair(b'{\r\n  "a": "1",\r\n//  "b": "2",\r\n  "c": "http://x"\r\n}') == b'{\r\n  "a": "1",\r\n  "c": "http://x"\r\n}'
    ok &= json.loads(repair(b'\xef\xbb\xbf{"a": [1, 2,],}')) == {"a": [1, 2]}
    ok &= json.loads(repair(b'{"u": "a,}b",}')) == {"u": "a,}b"}          # a comma inside a string is kept
    ok &= repair(b'{"a": 1 "b": 2}') is None                              # missing comma: refused, never guessed
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
