#!/usr/bin/env python3
"""Every `StreamCodec.unit(new X())` payload must be a type whose instances are all equal.

    python3 tools/audit-unit-codecs.py <src dir> [...]
    python3 tools/audit-unit-codecs.py --self-check

StreamCodec.unit(value) encodes nothing and, on encode, REFUSES any value that is not equals() to the one it
was built with (IllegalStateException: Can't encode '...X@a', expected '...X@b'). A field-less payload written
as an ordinary class has identity equality, so the codec built from one `new X()` rejects the `new X()` every
send site makes -- and the player is disconnected the first time the packet is sent. It compiles, loads, and
passes every server-side gate: the failure is on the CLIENT, at send time. Found live: a dual-wield attack
packet kicked the player out of a singleplayer world on every off-hand swing.

Accepted: X is a record or an enum, or X overrides equals(Object). Anything else is reported with the fix:
`public record X() implements CustomPacketPayload` (a record with no components: every instance is equal), or
one shared INSTANCE used both to build the codec and at every send site.

Exit 1 on a finding, 2 when nothing was checked (no Java files: a pass over an empty scope is not a pass).
Standard library only.
"""
import pathlib, re, sys, tempfile

UNIT = re.compile(r"StreamCodec\s*\.\s*unit\(\s*new\s+([A-Z]\w*)\s*\(\s*\)\s*\)", re.S)


def declares_equal_instances(text, cls):
    return bool(re.search(r"\b(record|enum)\s+" + cls + r"\b", text)
                or re.search(r"\bboolean\s+equals\s*\(\s*(?:final\s+)?Object\b", text))


def audit(roots):
    files, findings = 0, []
    by_name = {}
    for r in roots:
        for f in pathlib.Path(r).rglob("*.java"):
            files += 1
            by_name.setdefault(f.stem, []).append(f)
    for r in roots:
        for f in pathlib.Path(r).rglob("*.java"):
            text = f.read_text(encoding="utf-8", errors="replace")
            for m in UNIT.finditer(text):
                cls = m.group(1)
                decl = text if re.search(r"\b(class|record|enum)\s+" + cls + r"\b", text) else \
                    "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in by_name.get(cls, []))
                if not declares_equal_instances(decl, cls):
                    line = text.count("\n", 0, m.start()) + 1
                    findings.append(f"{f}:{line}: StreamCodec.unit(new {cls}()) but {cls} is a class with identity "
                                    f"equality -- every send of a fresh {cls} fails to encode and disconnects the "
                                    f"player. Fix: `public record {cls}() implements CustomPacketPayload`")
    return files, findings


def self_check():
    ok = True
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d)
        (p / "Bad.java").write_text("public class Bad implements CustomPacketPayload {\n  static final StreamCodec<B, Bad> C = "
                                    "StreamCodec\n      .unit(new Bad());\n}\n", encoding="utf-8")
        (p / "Rec.java").write_text("public record Rec() { static final Object C = StreamCodec.unit(new Rec()); }\n",
                                    encoding="utf-8")
        (p / "Eq.java").write_text("class Eq { static final Object C = StreamCodec.unit(new Eq());\n"
                                   "  public boolean equals(Object o) { return o instanceof Eq; } }\n", encoding="utf-8")
        n, f = audit([d])
        ok &= n == 3 and len(f) == 1 and "Bad.java:2" in f[0]
        (p / "Bad.java").write_text("public record Bad() { static final Object C = StreamCodec.unit(new Bad()); }\n",
                                    encoding="utf-8")
        ok &= audit([d])[1] == []
    with tempfile.TemporaryDirectory() as d:
        ok &= audit([d]) == (0, [])
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    if "--self-check" in sys.argv:
        return self_check()
    roots = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not roots:
        raise SystemExit(__doc__)
    files, findings = audit(roots)
    if not files:
        print("audit-unit-codecs: NO JAVA FILES CHECKED -- this is NOT a pass")
        return 2
    for x in findings:
        print(x)
    print(f"audit-unit-codecs: checked {files} file(s), {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
