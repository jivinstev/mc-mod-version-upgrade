#!/usr/bin/env python3
"""A small TOML reader for Pythons without tomllib (3.10 and older; macOS ships 3.9).

    from minitoml import loads          # only where `import tomllib` failed
    python3 tools/minitoml.py --self-check

It reads what mods.toml / neoforge.mods.toml files use: comments, [tables], [[arrays of tables]], dotted and
quoted keys, basic / literal / multi-line strings, integers, floats, booleans, arrays (multi-line, trailing
commas) and inline tables. Dates come back as their text. Anything it cannot read raises ValueError, so callers
treat it exactly like a tomllib.TOMLDecodeError. It is not a validator: it accepts some text real TOML rejects
(a redefined key, a multi-line inline table). The self-check compares it with tomllib wherever tomllib exists
(CI runs Python 3.12), so the two cannot drift apart unnoticed. Standard library only.
"""
import re
import sys

_BARE = re.compile(r"[A-Za-z0-9_-]+")
_NUM = re.compile(r"[+-]?(?:0x[0-9A-Fa-f_]+|0o[0-7_]+|0b[01_]+|(?:\d[\d_]*)(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?|inf|nan)")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?|\d{2}:\d{2}:\d{2}(?:\.\d+)?")
_ESC = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\"}


class _P:
    def __init__(self, s):
        self.s, self.i = s.replace("\r\n", "\n"), 0

    def err(self, msg):
        line = self.s.count("\n", 0, self.i) + 1
        raise ValueError(f"{msg} (line {line})")

    def peek(self, n=1):
        return self.s[self.i:self.i + n]

    def ws(self, newlines=False):
        """Skip spaces and tabs; with newlines, also newlines and comments (inside arrays)."""
        while self.i < len(self.s):
            c = self.s[self.i]
            if c in " \t" or (newlines and c == "\n"):
                self.i += 1
            elif newlines and c == "#":
                while self.i < len(self.s) and self.s[self.i] != "\n":
                    self.i += 1
            else:
                break

    def eol(self):
        self.ws()
        if self.peek() == "#":
            while self.i < len(self.s) and self.s[self.i] != "\n":
                self.i += 1
        if self.i < len(self.s):
            if self.s[self.i] != "\n":
                self.err("expected the end of the line")
            self.i += 1

    def key(self):
        parts = []
        while True:
            self.ws()
            c = self.peek()
            if c == '"':
                parts.append(self.basic())
            elif c == "'":
                parts.append(self.literal())
            else:
                m = _BARE.match(self.s, self.i)
                if not m:
                    self.err("expected a key")
                parts.append(m.group(0)); self.i = m.end()
            self.ws()
            if self.peek() != ".":
                return parts
            self.i += 1

    def basic(self):
        self.i += 1
        out = []
        while True:
            if self.i >= len(self.s) or self.s[self.i] == "\n":
                self.err("unterminated string")
            c = self.s[self.i]
            if c == '"':
                self.i += 1
                return "".join(out)
            if c == "\\":
                out.append(self.escape())
            else:
                out.append(c); self.i += 1

    def escape(self):
        e = self.s[self.i + 1:self.i + 2]
        if e in _ESC:
            self.i += 2
            return _ESC[e]
        if e in ("u", "U"):
            n = 4 if e == "u" else 8
            h = self.s[self.i + 2:self.i + 2 + n]
            if len(h) != n or not re.fullmatch(r"[0-9A-Fa-f]+", h):
                self.err("bad unicode escape")
            self.i += 2 + n
            return chr(int(h, 16))
        self.err(f"bad escape \\{e}")

    def literal(self):
        end = self.s.find("'", self.i + 1)
        nl = self.s.find("\n", self.i + 1)
        if end < 0 or (0 <= nl < end):
            self.err("unterminated string")
        v = self.s[self.i + 1:end]
        self.i = end + 1
        return v

    def multi(self, q):
        self.i += 3
        if self.peek() == "\n":
            self.i += 1
        out = []
        while True:
            if self.i >= len(self.s):
                self.err("unterminated multi-line string")
            if self.s.startswith(q * 3, self.i):
                n = 3
                while n < 5 and self.s.startswith(q, self.i + n):   # up to two quotes may end the content
                    n += 1
                out.append(q * (n - 3))
                self.i += n
                return "".join(out)
            c = self.s[self.i]
            if q == '"' and c == "\\":
                nxt = self.s[self.i + 1:self.i + 2]
                if nxt in (" ", "\t", "\n"):                          # line-ending backslash: trim to next text
                    j = self.i + 1
                    while j < len(self.s) and self.s[j] in " \t":
                        j += 1
                    if j < len(self.s) and self.s[j] == "\n":
                        while j < len(self.s) and self.s[j] in " \t\n":
                            j += 1
                        self.i = j
                        continue
                out.append(self.escape())
            else:
                out.append(c); self.i += 1

    def value(self):
        self.ws()
        s, i = self.s, self.i
        if s.startswith('"""', i):
            return self.multi('"')
        if s.startswith("'''", i):
            return self.multi("'")
        c = self.peek()
        if c == '"':
            return self.basic()
        if c == "'":
            return self.literal()
        if c == "[":
            return self.array()
        if c == "{":
            return self.inline()
        if s.startswith("true", i):
            self.i += 4
            return True
        if s.startswith("false", i):
            self.i += 5
            return False
        m = _DATE.match(s, i)
        if m and m.end() > i + 4 and not _NUM.fullmatch(m.group(0)):
            self.i = m.end()
            return m.group(0)
        m = _NUM.match(s, i)
        if m and m.group(0):
            self.i = m.end()
            t = m.group(0).replace("_", "")
            if t.lstrip("+-") in ("inf", "nan"):
                return float(t)
            if t[:2].lower() in ("0x", "0o", "0b") or t[1:3].lower() in ("0x", "0o", "0b"):
                return int(t, 0)
            return float(t) if any(ch in t for ch in ".eE") else int(t)
        self.err("expected a value")

    def array(self):
        self.i += 1
        out = []
        while True:
            self.ws(newlines=True)
            if self.peek() == "]":
                self.i += 1
                return out
            out.append(self.value())
            self.ws(newlines=True)
            if self.peek() == ",":
                self.i += 1
            elif self.peek() != "]":
                self.err("expected , or ] in an array")

    def inline(self):
        self.i += 1
        out = {}
        while True:
            self.ws(newlines=True)
            if self.peek() == "}":
                self.i += 1
                return out
            k = self.key()
            self.ws()
            if self.peek() != "=":
                self.err("expected = in an inline table")
            self.i += 1
            _set(out, k, self.value(), self)
            self.ws(newlines=True)
            if self.peek() == ",":
                self.i += 1
            elif self.peek() != "}":
                self.err("expected , or } in an inline table")


def _table(root, parts, p, array=False):
    t = root
    for n, k in enumerate(parts):
        last = n == len(parts) - 1
        if last and array:
            lst = t.setdefault(k, [])
            if not isinstance(lst, list):
                p.err(f"{'.'.join(parts)} is not an array of tables")
            lst.append({})
            return lst[-1]
        nxt = t.setdefault(k, {})
        if isinstance(nxt, list):
            nxt = nxt[-1] if nxt and isinstance(nxt[-1], dict) else p.err(f"{k} is not a table")
        elif not isinstance(nxt, dict):
            p.err(f"{k} is not a table")
        t = nxt
    return t


def _set(t, parts, v, p):
    for k in parts[:-1]:
        t = t.setdefault(k, {})
        if not isinstance(t, dict):
            p.err(f"{k} is not a table")
    t[parts[-1]] = v


def loads(text):
    """Parse TOML text into dicts, lists and scalars, as tomllib.loads does. ValueError when it cannot."""
    p = _P(text)
    root = cur = {}
    while True:
        p.ws(newlines=True)
        if p.i >= len(p.s):
            return root
        if p.s.startswith("[[", p.i):
            p.i += 2
            k = p.key()
            if not p.s.startswith("]]", p.i):
                p.err("expected ]]")
            p.i += 2
            cur = _table(root, k, p, array=True)
        elif p.peek() == "[":
            p.i += 1
            k = p.key()
            if p.peek() != "]":
                p.err("expected ]")
            p.i += 1
            cur = _table(root, k, p)
        else:
            k = p.key()
            p.ws()
            if p.peek() != "=":
                p.err("expected =")
            p.i += 1
            _set(cur, k, p.value(), p)
        p.eol()


SAMPLES = [
    # a [[mods]] file with every string kind, comments and the dependency tables a NeoForge mod carries
    '''modLoader="javafml" #mandatory
loaderVersion="[4,)"
license='MIT'
issueTrackerURL = "https://example.invalid/issues"
[[mods]]
modId="examplemod"
version="1.0.0"
displayName="Example Mod"
logoFile="examplemod.png"
credits="Art: someone,Tests:someone_else"
authors="Someone"
description=\'\'\'
Line one with a 'quote' and a "double".
  Indented line two.
\'\'\'
[[mixins]]
config="examplemod.mixins.json"
[[dependencies.examplemod]]
    modId="neoforge"
    type="required"
    versionRange="[21.1,)"
    ordering="NONE"
    side="BOTH"
[[dependencies.examplemod]]
    modId = "minecraft"
    type = "required"
    versionRange = "[1.21.1,1.21.2)"
''',
    # the inline-table form, single quotes, a multi-line array with a trailing comma and a comment
    "modLoader = 'lowcodefml'\nmods = [\n\t{ modId = 'inl', version = '1.1' }, # first\n]\n"
    "[[dependencies.inl]]\nmodId='minecraft'\nmandatory=true\nversionRange='[1.20.1]'\n"
    "[[dependencies.inl]]\nmodId='lib'\nmandatory=true\n",
    # escapes, a multi-line basic string with a line-ending backslash, numbers, nested and dotted keys
    'a = "tab\\tquote\\" \\u00e9"\nb = """\\\n   joined \\\n   text"""\nc = 1_000\nd = -2.5e3\ne = 0x1F\nf = [1, [2, 3], []]\n'
    'g.h = true\n"quoted key" = false\n[t.u]\nv = { w = 1, x = { y = "z" } }\n',
    # credits in a literal multi-line string, as one author writes them
    "credits = '''\nContributor: Someone\nMascot: Something\n'''\nwhen = 2026-10-13T12:00:00Z\n",
]

BAD = ['a = "unterminated\n', "a = \n", "[t\nb = 1\n", 'a = "x" b = 2\n', "a = [1 2]\n"]


def self_check():
    ok = True
    try:
        import tomllib
    except ImportError:
        tomllib = None
    for n, s in enumerate(SAMPLES):
        got = loads(s)
        if tomllib is not None:
            want = tomllib.loads(s)
            if n == 3:                    # a date: tomllib returns a datetime, this reader its text
                want["when"] = "2026-10-13T12:00:00Z"
            if got != want:
                ok = False; print(f"FAIL: sample {n} differs from tomllib:\n  got  {got}\n  want {want}")
    m = loads(SAMPLES[0])
    if not (m["mods"][0]["modId"] == "examplemod" and m["license"] == "MIT"
            and m["mods"][0]["description"] == "Line one with a 'quote' and a \"double\".\n  Indented line two.\n"
            and [d["modId"] for d in m["dependencies"]["examplemod"]] == ["neoforge", "minecraft"]
            and m["mixins"] == [{"config": "examplemod.mixins.json"}]):
        ok = False; print(f"FAIL: the [[mods]] sample: {m}")
    i = loads(SAMPLES[1])
    if not (i["mods"] == [{"modId": "inl", "version": "1.1"}] and i["dependencies"]["inl"][1] == {"modId": "lib", "mandatory": True}):
        ok = False; print(f"FAIL: the inline sample: {i}")
    e = loads(SAMPLES[2])
    if not (e["a"] == 'tab\tquote" \u00e9' and e["b"] == "joined text" and e["c"] == 1000 and e["d"] == -2500.0
            and e["e"] == 31 and e["f"] == [1, [2, 3], []] and e["g"] == {"h": True} and e["quoted key"] is False
            and e["t"]["u"]["v"] == {"w": 1, "x": {"y": "z"}}):
        ok = False; print(f"FAIL: escapes, numbers and keys: {e}")
    if loads(SAMPLES[3])["credits"] != "Contributor: Someone\nMascot: Something\n":
        ok = False; print("FAIL: a literal multi-line string")
    for b in BAD:
        try:
            loads(b)
            ok = False; print(f"FAIL: accepted bad TOML {b!r}")
        except ValueError:
            pass
    print("self-check:", "OK" if ok else "FAIL", "" if tomllib else "(no tomllib here: compared with fixed values only)")
    return 0 if ok else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-check"]:
        sys.exit(self_check())
    sys.exit(__doc__)
