#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""A time budget per test of tools/test-port-tools.sh, per OS family: a test that grows past its budget, or a new test
with none, fails the part it runs in.

    python3 tools/check-budget.py --times FILE [--os linux|macos|windows] [--budgets FILE] [--enforce]
    python3 tools/check-budget.py --times FILE --add-missing [--os ...]     # budget the tests that have none
    python3 tools/check-budget.py --self-check

WHY. The suite's wall time only grew (the owner: "We can't keep expanding test duration"), one self-check at a time,
and nobody saw it until a Windows job took 20 minutes. test-port-tools.sh writes every result's seconds (since the
previous result, as its PASS/FAIL lines print them) to a file; this compares them with tools/test-budgets.tsv.

THE RULE. A test fails its budget when it took more than 1.5 x its budget and more than its budget + 10 s (two CI runs
of the same commit differed by up to 10 s on one test, so a 1 s test is not failed by a cold JVM); a test with no
budget for this OS family
fails too. Each line names the test, its time and its budget. Budgets were set from CI's measured times x 1.4 (runner
variance is about +-40%), at least 2 s. With --enforce (test-port-tools.sh passes it when CI is set, or with
PORT_TOOLS_BUDGET=enforce) a problem exits 1; without, it is printed as a warning and exits 0 (a slower laptop is not
a failure). PORT_TOOLS_BUDGET=off skips the check.

THE KEY. A test is named by its description: the first 100 characters of its PASS/FAIL line's text, with a measured
time `(in 12.3 s)` and any other `<n> s` duration removed, so the key does not change from run to run; a second test
with the same key is `<key> #2`. The budgets file is TSV: key, then seconds for linux, macos, windows (an empty cell:
no budget on that OS).

Exit: 0 within budget (or not enforcing), 1 over budget or unbudgeted (enforcing), 2 bad input. Standard library only.
"""
import argparse, math, pathlib, platform, re, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUDGETS = ROOT / "tools" / "test-budgets.tsv"
OSES = ("linux", "macos", "windows")
FACTOR, FLOOR, HEADROOM, MIN_BUDGET = 1.5, 10, 1.4, 2
KEY_LEN = 100


def os_family():
    s = platform.system().lower()
    return "macos" if s == "darwin" else "windows" if s.startswith(("windows", "cygwin", "msys", "mingw")) else "linux"


def key_of(desc):
    d = re.sub(r"\s*\(in [0-9.]+ s\)", "", desc.split("\n", 1)[0])
    d = re.sub(r"\b[0-9]+(?:\.[0-9]+)? ?s\b", "N s", d)
    return " ".join(d.split())[:KEY_LEN]


def read_times(path):
    """[(key, seconds)] of a times file (SECONDS<TAB>DESCRIPTION per line), keys made unique in order."""
    out, seen = [], {}
    for ln in pathlib.Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        t, sep, desc = ln.partition("\t")
        if not sep or not t.strip().isdigit():
            continue
        k = key_of(desc)
        seen[k] = seen.get(k, 0) + 1
        out.append((k if seen[k] == 1 else f"{k} #{seen[k]}", int(t)))
    return out


def read_budgets(path):
    out = {}
    p = pathlib.Path(path)
    for ln in (p.read_text(encoding="utf-8").splitlines() if p.is_file() else []):
        if not ln.strip() or (ln.startswith("#") and "\t" not in ln):     # a comment has no tab; a key may start with #
            continue
        cells = ln.split("\t")
        out[cells[0]] = {o: int(c) for o, c in zip(OSES, cells[1:4]) if c.strip().isdigit()}
    return out


def write_budgets(path, budgets):
    lines = ["# tools/check-budget.py: per-test seconds of tools/test-port-tools.sh (key, linux, macos, windows; empty = no"
             " budget there).", "# A test fails when it takes > 1.5 x its budget and > budget + 10 s, or has no budget on its"
             " OS. Add one: python3 tools/check-budget.py --times FILE --add-missing"]
    for k in sorted(budgets):
        lines.append("\t".join([k] + [str(budgets[k][o]) if o in budgets[k] else "" for o in OSES]))
    pathlib.Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def budget_for(seconds):
    return max(MIN_BUDGET, int(math.ceil(seconds * HEADROOM)))


def problems(times, budgets, osf):
    out = []
    for k, t in times:
        b = budgets.get(k, {}).get(osf)
        if b is None:
            out.append(f"budget: NO BUDGET on {osf}: {t} s, no entry: {k} -- add it: python3 tools/check-budget.py "
                       "--times FILE --add-missing (tools/test-budgets.tsv)")
        elif t > b * FACTOR and t > b + FLOOR:
            out.append(f"budget: OVER on {osf}: {t} s against a budget of {b} s (allowed {max(b * FACTOR, b + FLOOR):.0f} s): {k}")
    return out


def self_check():
    ok = True

    def expect(c, msg):
        nonlocal ok
        if not c:
            ok = False
            print("FAIL:", msg)
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        (d / "times.tsv").write_text("3\texamplemod: a quick test\n40\texamplemod: a slow test (in 39.5 s): long text\n"
                                     "1\texamplemod: a quick test\n0\ta new test nobody budgeted\n", encoding="utf-8")
        times = read_times(d / "times.tsv")
        expect([k for k, _ in times] == ["examplemod: a quick test", "examplemod: a slow test: long text",
                                         "examplemod: a quick test #2", "a new test nobody budgeted"],
               f"keys: the measured time dropped, a repeated key numbered: {times}")
        b = {"examplemod: a quick test": {"linux": 2}, "examplemod: a slow test: long text": {"linux": 20, "windows": 60},
             "examplemod: a quick test #2": {"linux": 2}}
        p = problems(times, b, "linux")
        expect(len(p) == 2 and any("OVER on linux: 40 s against a budget of 20 s" in x and "a slow test" in x for x in p)
               and any("NO BUDGET on linux: 0 s" in x and "a new test nobody budgeted" in x for x in p),
               f"an over-budget test and an unbudgeted test fail, each named with its time and budget: {p}")
        p = problems(times[:3], b, "windows")
        expect(len(p) == 2 and all("NO BUDGET on windows" in x for x in p), f"budgets are per OS family: {p}")
        expect(problems([("x", 12)], {"x": {"linux": 2}}, "linux") == [] and problems([("x", 13)], {"x": {"linux": 2}}, "linux"),
               "a small test: over only past budget + 10 s too (runner jitter)")
        expect(problems([("x", 30)], {"x": {"linux": 20}}, "linux") == [] and problems([("x", 31)], {"x": {"linux": 20}}, "linux"),
               "the 50% line: 30 s on a 20 s budget passes, 31 s fails")
        write_budgets(d / "b.tsv", b)
        expect(read_budgets(d / "b.tsv") == b, "the budgets file reads back as written")
        rc_enforce = main(["--times", str(d / "times.tsv"), "--os", "linux", "--budgets", str(d / "b.tsv"), "--enforce"])
        rc_warn = main(["--times", str(d / "times.tsv"), "--os", "linux", "--budgets", str(d / "b.tsv")])
        expect(rc_enforce == 1 and rc_warn == 0, f"--enforce exits 1, a warning exits 0: {rc_enforce} {rc_warn}")
        main(["--times", str(d / "times.tsv"), "--os", "linux", "--budgets", str(d / "b.tsv"), "--add-missing"])
        nb = read_budgets(d / "b.tsv")
        expect(nb.get("a new test nobody budgeted") == {"linux": 2} and nb["examplemod: a slow test: long text"]["linux"] == 20,
               f"--add-missing budgets only the tests that had none: {nb}")
        real = read_budgets(BUDGETS)
        expect(len(real) > 50 and all(set(v) <= set(OSES) for v in real.values()),
               f"tools/test-budgets.tsv reads ({len(real)} test(s))")
    print(f"check-budget self-check: {'OK' if ok else 'FAILED'}")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--times", help="SECONDS<TAB>DESCRIPTION per test (test-port-tools.sh writes it)")
    ap.add_argument("--os", choices=OSES, help="the OS family (default: this machine's)")
    ap.add_argument("--budgets", default=str(BUDGETS))
    ap.add_argument("--enforce", action="store_true", help="exit 1 on a problem (CI)")
    ap.add_argument("--add-missing", action="store_true", help="give each test with no budget on this OS one (x1.4)")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args(argv)
    if a.self_check:
        return self_check()
    if not a.times or not pathlib.Path(a.times).is_file():
        print("check-budget: --times FILE is required (and must exist)", file=sys.stderr)
        return 2
    osf, times, budgets = a.os or os_family(), read_times(a.times), read_budgets(a.budgets)
    if a.add_missing:
        n = 0
        for k, t in times:
            if osf not in budgets.setdefault(k, {}):
                budgets[k][osf] = budget_for(t)
                n += 1
        write_budgets(a.budgets, budgets)
        print(f"check-budget: {n} budget(s) added for {osf} -> {a.budgets}")
        return 0
    p = problems(times, budgets, osf)
    for x in p:
        print(x)
    total = sum(t for _k, t in times)
    print(f"check-budget: {len(times)} test(s), {total} s on {osf}: " + (f"{len(p)} over budget or unbudgeted"
                                                                         + ("" if a.enforce else " (warning only)") if p else "all within budget"))
    return 1 if p and a.enforce else 0


if __name__ == "__main__":
    sys.exit(main())
