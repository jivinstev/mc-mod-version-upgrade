#!/usr/bin/env python3
"""The one place a target version's support status is computed.

    python3 tools/supported-versions.py                       list every target and its status
    python3 tools/supported-versions.py status 26.3           print one status (tested/reported/untested)
    python3 tools/supported-versions.py record 26.3 neoforge "PR #42: <mod description>"
                                  count one more mod whose Gate C passed on 26.3 (the reviewer runs this)

A target is "tested" once THRESHOLD different mods have been ported to it with Gate C (a real client
boot) passing, "reported" below that, and "untested" when it is not listed. The owner can pin a status
with the override column. See SUPPORTED_VERSIONS.md.
"""
import pathlib, sys

THRESHOLD = 10
ROOT = pathlib.Path(__file__).resolve().parent.parent
TSV = ROOT / "SUPPORTED_VERSIONS.tsv"
COLS = ["minecraft", "loader", "mods_gate_c", "override", "evidence"]


def load(path=TSV):
    head, rows = [], []
    for line in path.read_text().splitlines() if path.exists() else []:
        if line.startswith("#") or line.split("\t")[0] == "minecraft" or not line.strip():
            head.append(line)
            continue
        c = (line.split("\t") + [""] * len(COLS))[:len(COLS)]
        rows.append(dict(zip(COLS, c)))
    return head, rows


def count(row):
    try:
        return int(row["mods_gate_c"] or 0)
    except ValueError:
        return 0


def status(row):
    if row is None:
        return "untested"
    if row["override"] in ("tested", "reported"):
        return row["override"]
    return "tested" if count(row) >= THRESHOLD else "reported"


def describe(row):
    """'tested', 'reported (3 of 10 mods with Gate C)', 'untested'."""
    s = status(row)
    return s if s != "reported" else f"reported ({count(row)} of {THRESHOLD} mods with Gate C)"


def main(argv):
    head, rows = load()
    by = {r["minecraft"]: r for r in rows}
    if not argv:
        for r in rows:
            print(f"{r['minecraft']:<8} {r['loader']:<9} {describe(r)}")
        return 0
    if argv[0] == "status" and len(argv) == 2:
        print(describe(by.get(argv[1])))
        return 0
    if argv[0] == "record" and len(argv) == 4:
        mc, loader, ev = argv[1:]
        r = by.get(mc)
        if r is None:
            r = dict(zip(COLS, [mc, loader, "0", "", ""]))
            rows.append(r)
        r["mods_gate_c"] = str(count(r) + 1)
        r["evidence"] = "; ".join(x for x in (r["evidence"], ev) if x)
        TSV.write_text("\n".join(head + ["\t".join(r[c] for c in COLS) for r in rows]) + "\n")
        print(f"{mc}: {describe(r)}")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
