"""NeoForge version arithmetic shared by tools/fork-branches.py and tools/layer-deps.py: which Minecraft version and
build a NeoForge version string names, the inverse, and the newest stable build per Minecraft version from the NeoForge
maven's metadata. Its own module so a fork's CI kit (tools/port-ci-kit.txt), which carries layer-deps.py but not
fork-branches.py, has it. Standard library only.

    python3 tools/neoversion.py --self-check
"""
import re
import sys


def neo_line(v):
    """(Minecraft version, build) a NeoForge version builds, or None for a beta or a version of neither scheme.
    21.N.B -> 1.21.N (21.0.B -> 1.21); from 26.x the Minecraft version is the first three parts with a trailing .0
    dropped: 26.2.0.B -> 26.2, 26.1.2.B -> 26.1.2."""
    m = re.match(r"21\.(\d+)\.(\d+)$", v)
    if m:
        n = int(m.group(1))
        return ("1.21" if n == 0 else f"1.21.{n}"), int(m.group(2))
    m = re.match(r"(\d+)\.(\d+)\.(\d+)\.(\d+)$", v)
    if m and int(m.group(1)) >= 26:
        a, b, c = m.group(1), m.group(2), int(m.group(3))
        return (f"{a}.{b}" if c == 0 else f"{a}.{b}.{c}"), int(m.group(4))
    return None


def neo_of(mc, build):
    """The NeoForge version string for a Minecraft version and build: the inverse of neo_line."""
    p = mc.split(".")
    if p[0] == "1":
        return f"21.{p[2] if len(p) > 2 else 0}.{build}"
    return f"{p[0]}.{p[1]}.{p[2] if len(p) > 2 else 0}.{build}"


def newest_stable(xml):
    """{Minecraft version: newest stable NeoForge version string} from maven-metadata.xml."""
    newest = {}
    for v in re.findall(r"<version>([^<]+)</version>", xml):
        nl = neo_line(v)
        if nl:
            newest[nl[0]] = max(newest.get(nl[0], 0), nl[1])
    return {mc: neo_of(mc, b) for mc, b in newest.items()}


def self_check():
    ok = True

    def expect(c, msg):
        nonlocal ok
        if not c:
            ok = False
            print(f"FAIL: {msg}")
    expect(neo_line("21.1.77") == ("1.21.1", 77) and neo_line("21.0.3") == ("1.21", 3), "21.N.B")
    expect(neo_line("26.2.0.5") == ("26.2", 5) and neo_line("26.1.2.9") == ("26.1.2", 9), "26.x")
    expect(neo_line("21.1.0-beta") is None and neo_line("20.4.1") is None, "a beta or another scheme: None")
    for v in ("21.1.77", "21.0.3", "26.2.0.5", "26.1.2.9"):
        expect(neo_of(*neo_line(v)) == v, f"neo_of inverts neo_line: {v}")
    xml = "".join(f"<version>{v}</version>" for v in ("21.1.70", "21.1.77", "21.1.80-beta", "26.2.0.4", "26.2.0.5"))
    expect(newest_stable(xml) == {"1.21.1": "21.1.77", "26.2": "26.2.0.5"}, f"newest stable: {newest_stable(xml)}")
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        sys.exit(self_check())
    print(__doc__)
