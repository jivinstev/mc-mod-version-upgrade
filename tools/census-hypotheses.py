#!/usr/bin/env python3
"""Zero-token checks of the #27 hypotheses about shrinking the judgement work, over hunk-census records.

    python3 tools/census-hypotheses.py --census <dir> [--bench <dir>] [--detail]

<dir> is a recipe-bench-drive.py --census output: census.tsv plus <row>/hunks.jsonl (from hunk-census.py
--hunks-out). --bench points at a recipe-bench-drive.py output whose <row>/bench.json carries
errors_by_file (H5). Prints PROFILE-LEVEL numbers only, fit to publish; --detail adds per-row and
per-name lines, which name identifiers from the ports and stay private.

H1  propagation    Judgement fixes repeat inside a port, so "the model fixes one site, a script applies
                   the same edit to the rest" shrinks N fixes to 1. Measured: share of code hunks that
                   repeat an earlier hunk's normalised SHAPE (same entry/cluster, incidental names
                   blanked) in the same row -- exact, and loosely (same removed/added identifiers).
H2  compat shims   Ports route judgement changes through local helpers (an NBT shim, a MobType shim, the
                   §W5 pairs) that a shared library could supply once. Measured: share of code hunks that
                   call a class the port ADDED (a new file) from 3+ hunks in 2+ files; and, in --detail,
                   helper names recurring across rows.
H5  file locality  Most fixes land in the file that fails to compile, so a cheap model with one file, its
                   errors and the compiler in a loop can do them. Measured: share of code hunks (in files
                   present at the start) that are in a file with at least one start compile error.
H7  optional code  Optional integrations (recipe viewers, accessory/probe/guide mods) can be stubbed and
                   deferred by default. Measured: share of code hunks and of new files in files importing
                   one of OPTIONAL_ROOTS.
H8  (decompiler choice) is a bench comparison, not a census one: see docs/EVALS.md.
"""
import argparse, collections, csv, json, pathlib, sys

OPTIONAL_ROOTS = ("mezz.jei", "dev.emi", "me.shedaniel", "snownee.jade", "mcjty.theoneprobe",
                  "vazkii.patchouli", "top.theillusivec4", "journeymap.client", "com.simibubi",
                  "dev.architectury", "xaero.common", "net.irisshaders", "net.coderbot")
CODE = ("entry", "cluster")


def load(census):
    rows = []
    for t in csv.DictReader(open(census / "census.tsv", encoding="utf-8"), delimiter="\t"):
        f = census / t["port"] / "hunks.jsonl"
        if f.exists():
            recs = [json.loads(l) for l in open(f, encoding="utf-8")]
            rows.append({"profile": t["profile"], "row": t["port"], "recs": recs})
    return rows


def h1(recs, only=None):
    """only: restrict to 'entry' (attributed) or 'cluster' (unattributed) hunks."""
    code = [r for r in recs if r["cat"] in CODE and (only is None or r["cat"] == only)]
    seen_exact, seen_loose, rep_exact, rep_loose = set(), set(), 0, 0
    for r in code:
        e = (r["key"], r["shape"]); l = (r["key"], tuple(r["removed"]), tuple(r["added"]))
        rep_exact += e in seen_exact; rep_loose += l in seen_loose
        seen_exact.add(e); seen_loose.add(l)
    return len(code), rep_exact, rep_loose


def h2(recs):
    new = {pathlib.PurePath(r["file"]).stem for r in recs if r["cat"] == "new-file"}
    code = [r for r in recs if r["cat"] in CODE]
    use = collections.defaultdict(lambda: [0, set()])
    for r in code:
        for a in set(r["added"]) & new:
            use[a][0] += 1; use[a][1].add(r["file"])
    helpers = {h for h, (n, fs) in use.items() if n >= 3 and len(fs) >= 2}
    via = sum(1 for r in code if set(r["added"]) & helpers)
    return len(code), via, helpers


def h5(recs, errors_by_file):
    code = [r for r in recs if r["cat"] in CODE]
    err_files = {f for f, n in errors_by_file.items() if n}
    inside = sum(1 for r in code if r["file"] in err_files)
    changed = {r["file"] for r in code}
    return len(code), inside, len(err_files), len(err_files & changed)


def h7(recs):
    opt = lambda r: any(i.startswith(OPTIONAL_ROOTS) for i in r.get("imports", []))
    code = [r for r in recs if r["cat"] in CODE]
    new = [r for r in recs if r["cat"] == "new-file"]
    return len(code), sum(map(opt, code)), len(new), sum(map(opt, new))


def pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "—"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--census", required=True); ap.add_argument("--bench"); ap.add_argument("--detail", action="store_true")
    a = ap.parse_args()
    rows = load(pathlib.Path(a.census))
    if not rows:
        print("no hunks.jsonl under", a.census, "-- run recipe-bench-drive.py --census first"); return 2
    key = lambda p: (int(p.split("-")[0][1:]), p)
    prof = collections.defaultdict(lambda: collections.Counter())
    helper_rows = collections.defaultdict(set)
    for r in rows:
        p = prof[r["profile"]]; p["rows"] += 1
        n, ex, lo = h1(r["recs"]); p["code"] += n; p["rep_exact"] += ex; p["rep_loose"] += lo
        un, uex, _ulo = h1(r["recs"], "cluster"); p["un"] += un; p["un_rep"] += uex
        _n, via, helpers = h2(r["recs"]); p["via"] += via; p["helpers"] += len(helpers)
        for h in helpers:
            helper_rows[h].add(r["row"])
        _n, oc, nn, on = h7(r["recs"]); p["opt_code"] += oc; p["new"] += nn; p["opt_new"] += on
        if a.bench:
            bj = pathlib.Path(a.bench) / r["row"] / "bench.json"
            ebf = json.loads(bj.read_text(encoding="utf-8")).get("errors_by_file") if bj.exists() else None
            if ebf is not None:
                n5, inside, ef, efc = h5(r["recs"], ebf)
                p["h5_rows"] += 1; p["h5_code"] += n5; p["h5_in"] += inside; p["h5_ef"] += ef; p["h5_efc"] += efc
        if a.detail:
            print(f"  {r['profile']:8} {r['row']:30} code={n} repeat={pct(ex, n)}/{pct(lo, n)} via-helper={pct(via, n)} "
                  f"helpers={','.join(sorted(helpers))[:80]}")
    print("| Profile | Rows | Code hunks | H1 repeats (exact / loose) | H2 via a port-added helper | "
          "H1 among unattributed | H5 in a file with a start error | H7 in optional-integration code |")
    print("|---|---|---|---|---|---|---|---|")
    T = collections.Counter()
    for pr in sorted(prof, key=key):
        p = prof[pr]; T.update(p)
        h5s = f"{pct(p['h5_in'], p['h5_code'])} ({p['h5_rows']} rows)" if p["h5_rows"] else "—"
        print(f"| {pr} | {p['rows']} | {p['code']:,} | {pct(p['rep_exact'], p['code'])} / {pct(p['rep_loose'], p['code'])} | "
              f"{pct(p['via'], p['code'])} | {pct(p['un_rep'], p['un'])} | {h5s} | {pct(p['opt_code'], p['code'])} |")
    h5s = f"{pct(T['h5_in'], T['h5_code'])} ({T['h5_rows']} rows)" if T["h5_rows"] else "—"
    print(f"| **all** | {T['rows']} | {T['code']:,} | {pct(T['rep_exact'], T['code'])} / {pct(T['rep_loose'], T['code'])} | "
          f"{pct(T['via'], T['code'])} | {pct(T['un_rep'], T['un'])} | {h5s} | {pct(T['opt_code'], T['code'])} |")
    print(f"\nH7: {T['opt_new']} of {T['new']} new files import an optional integration.")
    if T["h5_rows"]:
        print(f"H5: {T['h5_efc']} of {T['h5_ef']} files with a start error were changed by the port.")
    shared = {h: rs for h, rs in helper_rows.items() if len(rs) >= 2}
    print(f"H2: {len(helper_rows)} port-added helper classes; {len(shared)} names recur in 2+ rows.")
    if a.detail:
        for h, rs in sorted(shared.items(), key=lambda kv: -len(kv[1])):
            print(f"  {h}: {','.join(sorted(rs))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
