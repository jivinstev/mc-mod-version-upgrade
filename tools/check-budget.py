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

THE RULE. A test fails its budget when it took more than max(2 x its budget, its budget + 30 s); a test with no
budget for this OS family fails too. Each line names the test, its time and its budget. A budget is the largest time
CI measured for the test x 1.14, at least 2 s. Why: the budget is there to catch a test that doubles or more (the
round-audit self-check grew by ~10 min on Windows); run-to-run noise on shared runners reaches ~60% (macOS: a round-audit
third took 61 s, then 114 s; Windows: a 3-6 s test took 24 s with a cold JVM), and a tighter band only turns CI red on
noise. With --enforce (test-port-tools.sh passes it when CI is set, or with
PORT_TOOLS_BUDGET=enforce) a problem exits 1; without, it is printed as a warning and exits 0 (a slower laptop is not
a failure). PORT_TOOLS_BUDGET=off skips the check.

THE KEY. A test is named by its description (a PASS line's; a FAIL line carries the failure text, not the test's name,
so a failed test is not timed -- its FAIL already fails the part): the first 100 characters of its PASS/FAIL line's text, with a measured
time `(in 12.3 s)` and any other `<n> s` duration removed, so the key does not change from run to run; a second test
with the same key is `<key> #2`. The budgets file is TSV: key, then seconds for linux, macos, windows (an empty cell:
no budget on that OS).

Exit: 0 within budget (or not enforcing), 1 over budget or unbudgeted (enforcing), 2 bad input. Standard library only.
"""
import argparse, math, pathlib, platform, re, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUDGETS = ROOT / "tools" / "test-budgets.tsv"
OSES = ("linux", "macos", "windows")
FACTOR, FLOOR, HEADROOM, MIN_BUDGET = 2, 30, 1.14, 2
KEY_LEN = 100


def os_family():
    s = platform.system().lower()
    return "macos" if s == "darwin" else "windows" if s.startswith(("windows", "cygwin", "msys", "mingw")) else "linux"


def key_of(desc):
    d = re.sub(r"\s*\(in [0-9.]+ s\)", "", desc.split("\n", 1)[0])
    d = re.sub(r"\b[0-9]+(?:\.[0-9]+)? ?s\b", "N s", d)
    return " ".join(d.split())[:KEY_LEN]


def read_times(path, failed=None):
    """[(key, seconds)] of a times file (SECONDS<TAB>PASS|FAIL<TAB>DESCRIPTION per line; SECONDS<TAB>DESCRIPTION is a
    PASS), keys made unique in order. A FAIL line's text is its failure, not the test's name, so it has no stable key:
    it is left out (counted in `failed`, a list, when given) -- its FAIL already fails the part."""
    out, seen = [], {}
    for ln in pathlib.Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        t, sep, desc = ln.partition("\t")
        if not sep or not t.strip().isdigit():
            continue
        st, sep2, rest = desc.partition("\t")
        if sep2 and st in ("PASS", "FAIL"):
            if st == "FAIL":
                if failed is not None:
                    failed.append(int(t))
                continue
            desc = rest
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
             " budget there).", "# A test fails when it takes > max(2 x its budget, budget + 30 s), or has no budget on its"
             " OS. Add one: python3 tools/check-budget.py --times FILE --add-missing"]
    for k in sorted(budgets):
        lines.append("\t".join([k] + [str(budgets[k][o]) if o in budgets[k] else "" for o in OSES]))
    with open(path, "w", encoding="utf-8", newline="\n") as f:      # LF on Windows too (newline= on write_text: 3.10+)
        f.write("\n".join(lines) + "\n")


def budget_for(seconds):
    return max(MIN_BUDGET, int(math.ceil(seconds * HEADROOM)))


def problems(times, budgets, osf):
    out = []
    for k, t in times:
        b = budgets.get(k, {}).get(osf)
        if b is None:
            out.append(f"budget: NO BUDGET on {osf}: {t} s, no entry: {k} -- add it: python3 tools/check-budget.py "
                       "--times FILE --add-missing (tools/test-budgets.tsv)")
        elif t > max(b * FACTOR, b + FLOOR):
            out.append(f"budget: OVER on {osf}: {t} s against a budget of {b} s (allowed {max(b * FACTOR, b + FLOOR)} s): {k}")
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
        (d / "times.tsv").write_text("3\tPASS\texamplemod: a quick test\n60\tPASS\texamplemod: a slow test (in 59.5 s): long text\n"
                                     "1\texamplemod: a quick test\n0\tPASS\ta new test nobody budgeted\n"
                                     "132\tFAIL\texamplemod self-check: FAIL: the lock is still held\n", encoding="utf-8")
        failed = []
        times = read_times(d / "times.tsv", failed)
        expect(failed == [132], f"a FAIL line is not keyed by its failure text (not timed, counted): {failed}")
        expect([k for k, _ in times] == ["examplemod: a quick test", "examplemod: a slow test: long text",
                                         "examplemod: a quick test #2", "a new test nobody budgeted"],
               f"keys: the measured time dropped, a repeated key numbered: {times}")
        b = {"examplemod: a quick test": {"linux": 2}, "examplemod: a slow test: long text": {"linux": 20, "windows": 60},
             "examplemod: a quick test #2": {"linux": 2}}
        p = problems(times, b, "linux")
        expect(len(p) == 2 and any("OVER on linux: 60 s against a budget of 20 s (allowed 50 s)" in x and "a slow test" in x for x in p)
               and any("NO BUDGET on linux: 0 s" in x and "a new test nobody budgeted" in x for x in p),
               f"an over-budget test and an unbudgeted test fail, each named with its time and budget: {p}")
        p = problems(times[:3], b, "windows")
        expect(len(p) == 2 and all("NO BUDGET on windows" in x for x in p), f"budgets are per OS family: {p}")
        expect(problems([("x", 24)], {"x": {"linux": 9}}, "linux") == [],
               "a small test's cold-start spike (24 s on a 9 s budget, Windows CI 2026-11-02) passes")
        expect(problems([("x", 114)], {"x": {"macos": 70}}, "macos") == [],
               "runner noise of ~60% on a heavy test (114 s on a 70 s budget, macOS CI 2026-11-02) passes")
        expect(problems([("x", 32)], {"x": {"linux": 2}}, "linux") == [] and problems([("x", 33)], {"x": {"linux": 2}}, "linux"),
               "a small test: budget + 30 s passes, one second more fails")
        expect(problems([("x", 200)], {"x": {"windows": 100}}, "windows") == []
               and problems([("x", 201)], {"x": {"windows": 100}}, "windows"),
               "a heavy test: 2 x its budget passes, one second more fails")
        heavy = budget_for(198)                    # round.py's self-check on Windows: its largest measured time
        expect(problems([("x", 2 * 198 * 1.14 + 1)], {"x": {"windows": heavy}}, "windows")
               and not problems([("x", 198)], {"x": {"windows": heavy}}, "windows"),
               f"a heavy test that doubles (past 2 x its budget of {heavy} s) fails")
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
    ap.add_argument("--add-missing", action="store_true", help="give each test with no budget on this OS one (x1.14)")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args(argv)
    if a.self_check:
        return self_check()
    if not a.times or not pathlib.Path(a.times).is_file():
        print("check-budget: --times FILE is required (and must exist)", file=sys.stderr)
        return 2
    failed = []
    osf, times, budgets = a.os or os_family(), read_times(a.times, failed), read_budgets(a.budgets)
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
    print(f"check-budget: {len(times)} test(s), {total} s on {osf}"
          + (f" ({len(failed)} failed test(s) not timed: their own FAIL lines report them)" if failed else "") + ": "
          + (f"{len(p)} over budget or unbudgeted"
                                                                         + ("" if a.enforce else " (warning only)") if p else "all within budget"))
    return 1 if p and a.enforce else 0


if __name__ == "__main__":
    sys.exit(main())
