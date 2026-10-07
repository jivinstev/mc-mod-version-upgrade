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
H1x cross-port    The same edit recurs in OTHER ports -- the test of whether a recipe written once pays
                   off again (H1 alone only says a port repeats itself). Measured: share of code hunks
                   whose loose shape (entry/cluster + removed/added identifiers) appears in 2+ and 3+
                   different MODS (a mod's 1.21.1 and 26.2 rows count once), counted within one AXIS:
                   era-jump rows (profile *-era, or one of --era-profiles) only match era rows, since
                   a recipe pack is written per axis.
H3  choice points  A catalogue entry's real fixes fall into a FEW repeatable edits, so the model can pick
                   one of k options instead of writing the fix. Measured, per entry hit in 3+ mods with
                   20+ hunks: the share of its hunks covered by its top-3 loose shapes (removed/added
                   identifiers); reported as entries (and their hunks) at 50%+ and 80%+ coverage.
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


def load(census, era_profiles=("P9", "P10")):
    rows = []
    for t in csv.DictReader(open(census / "census.tsv", encoding="utf-8"), delimiter="\t"):
        f = census / t["port"] / "hunks.jsonl"
        if f.exists():
            recs = [json.loads(l) for l in open(f, encoding="utf-8")]
            rows.append({"profile": t["profile"], "row": t["port"], "recs": recs,
                         "era": t["profile"].endswith("-era") or t["profile"] in era_profiles})
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


def loose(r):
    return (r["key"], tuple(r["removed"]), tuple(r["added"]))


def cross_port(rows):
    """-> {(axis, loose shape): set of mods} over every row's code hunks."""
    mods = collections.defaultdict(set)
    for r in rows:
        for h in r["recs"]:
            if h["cat"] in CODE and (h["removed"] or h["added"]):
                mods[(r["era"], loose(h))].add(r["row"].split("@")[0])
    return mods


def h3(rows, k=3, min_mods=3, min_hunks=20):
    """-> [(entry, hunks, mods, distinct shapes, top-k coverage)] over attributed hunks."""
    shapes, mods = collections.defaultdict(collections.Counter), collections.defaultdict(set)
    for r in rows:
        for h in r["recs"]:
            if h["cat"] == "entry":
                shapes[h["key"]][(tuple(h["removed"]), tuple(h["added"]))] += 1
                mods[h["key"]].add(r["row"].split("@")[0])
    out = []
    for e, c in shapes.items():
        n = sum(c.values())
        if len(mods[e]) >= min_mods and n >= min_hunks:
            out.append((e, n, len(mods[e]), len(c), sum(v for _s, v in c.most_common(k)) / n))
    return sorted(out, key=lambda r: -r[1])


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
    ap.add_argument("--era-profiles", default="P9,P10", help="profiles whose rows are era jumps (besides *-era)")
    a = ap.parse_args()
    rows = load(pathlib.Path(a.census), tuple(a.era_profiles.split(",")))
    if not rows:
        print("no hunks.jsonl under", a.census, "-- run recipe-bench-drive.py --census first"); return 2
    key = lambda p: (int(p.split("-")[0][1:]), p)
    prof = collections.defaultdict(lambda: collections.Counter())
    helper_rows = collections.defaultdict(set)
    xp = cross_port(rows)
    for r in rows:
        p = prof[r["profile"]]; p["rows"] += 1
        n, ex, lo = h1(r["recs"]); p["code"] += n; p["rep_exact"] += ex; p["rep_loose"] += lo
        un, uex, _ulo = h1(r["recs"], "cluster"); p["un"] += un; p["un_rep"] += uex
        for h in r["recs"]:
            if h["cat"] in CODE:
                k = len(xp.get((r["era"], loose(h)), ()))
                p["x2"] += k >= 2; p["x3"] += k >= 3
                if h["cat"] == "cluster":
                    p["ux2"] += k >= 2
        _n, via, helpers = h2(r["recs"]); p["via"] += via; p["helpers"] += len(helpers)
        for h in helpers:
            helper_rows[h].add(r["row"])
        _n, oc, nn, on = h7(r["recs"]); p["opt_code"] += oc; p["new"] += nn; p["opt_new"] += on
        if a.bench:
            bj = pathlib.Path(a.bench) / r["row"] / "bench.json"
            ebf = json.loads(bj.read_text(encoding="utf-8")).get("errors_by_file") if bj.exists() else None
            if ebf:   # a start with no errors (one that is not a raw start) has nothing to locate
                n5, inside, ef, efc = h5(r["recs"], ebf)
                p["h5_rows"] += 1; p["h5_code"] += n5; p["h5_in"] += inside; p["h5_ef"] += ef; p["h5_efc"] += efc
        if a.detail:
            print(f"  {r['profile']:8} {r['row']:30} code={n} repeat={pct(ex, n)}/{pct(lo, n)} via-helper={pct(via, n)} "
                  f"helpers={','.join(sorted(helpers))[:80]}")
    print("| Profile | Rows | Code hunks | H1 repeats (exact / loose) | H2 via a port-added helper | "
          "H1 among unattributed | H1x in 2+ / 3+ mods | H1x unattributed in 2+ mods | H5 in a file with a start error | H7 in optional-integration code |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    T = collections.Counter()
    for pr in sorted(prof, key=key):
        p = prof[pr]; T.update(p)
        h5s = f"{pct(p['h5_in'], p['h5_code'])} ({p['h5_rows']} rows)" if p["h5_rows"] else "—"
        print(f"| {pr} | {p['rows']} | {p['code']:,} | {pct(p['rep_exact'], p['code'])} / {pct(p['rep_loose'], p['code'])} | "
              f"{pct(p['via'], p['code'])} | {pct(p['un_rep'], p['un'])} | {pct(p['x2'], p['code'])} / {pct(p['x3'], p['code'])} | {pct(p['ux2'], p['un'])} | {h5s} | {pct(p['opt_code'], p['code'])} |")
    h5s = f"{pct(T['h5_in'], T['h5_code'])} ({T['h5_rows']} rows)" if T["h5_rows"] else "—"
    print(f"| **all** | {T['rows']} | {T['code']:,} | {pct(T['rep_exact'], T['code'])} / {pct(T['rep_loose'], T['code'])} | "
          f"{pct(T['via'], T['code'])} | {pct(T['un_rep'], T['un'])} | {pct(T['x2'], T['code'])} / {pct(T['x3'], T['code'])} | {pct(T['ux2'], T['un'])} | {h5s} | {pct(T['opt_code'], T['code'])} |")
    print(f"\nH7: {T['opt_new']} of {T['new']} new files import an optional integration.")
    if T["h5_rows"]:
        print(f"H5: {T['h5_efc']} of {T['h5_ef']} files with a start error were changed by the port.")
    for era in (False, True):
        mods, n = collections.defaultdict(set), collections.Counter()
        tot = 0
        for r in (r for r in rows if r["era"] == era):
            for h in r["recs"]:
                if h["cat"] in CODE:
                    tot += 1
                    if h["removed"] or h["added"]:
                        mods[loose(h)].add(r["row"].split("@")[0]); n[loose(h)] += 1
        cand = sorted((k for k in mods if len(mods[k]) >= 2), key=lambda k: -n[k])
        if tot and cand:
            cum = [sum(n[k] for k in cand[:i]) for i in (10, 25, 50, 100)]
            print(f"H1x {'era-jump' if era else 'to-1.21.x'} rows: {len(cand)} cross-port shapes; top 10/25/50/100 cover "
                  + " / ".join(pct(c, tot) for c in cum) + f" of {tot:,} code hunks ({pct(sum(n[k] for k in cand), tot)} all)")
    e3 = h3(rows)
    if e3:
        nh = sum(r[1] for r in e3)
        for thr in (0.5, 0.8):
            sel = [r for r in e3 if r[4] >= thr]
            print(f"H3: {len(sel)} of {len(e3)} entries (hit in 3+ mods, 20+ hunks) have top-3 shapes covering "
                  f"{thr:.0%}+ of their fixes: {pct(sum(r[1] for r in sel), nh)} of those entries' hunks.")
        if a.detail:
            for e, n, m, s, cov in e3[:30]:
                print(f"  {e:6} hunks={n} mods={m} shapes={s} top3={cov:.0%}")
    shared = {h: rs for h, rs in helper_rows.items() if len(rs) >= 2}
    print(f"H2: {len(helper_rows)} port-added helper classes; {len(shared)} names recur in 2+ rows.")
    if a.detail:
        for h, rs in sorted(shared.items(), key=lambda kv: -len(kv[1])):
            print(f"  {h}: {','.join(sorted(rs))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
