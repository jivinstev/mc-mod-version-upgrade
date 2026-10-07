#!/usr/bin/env python3
"""Plan a port's route: the chain of hops from a source (loader, Minecraft) to a target.

    python3 tools/route.py --from forge:1.20.1 --to neoforge:26.2 [--json]

Reads tools/routes.tsv. Each hop is finished (compile + Gate B) before the next one starts, because a
hop's pack was measured on code that compiles at that hop's source version: the 1.21.1 -> 26.2 rename
table removed 59% of start errors on finished 1.21.1 ports, and nobody has measured it on a half-ported
tree. A hop with no pack is reported as such; whether to run it anyway is the caller's decision.
Exits 0 with a route, 2 when no chain of rows reaches the target. Standard library only.
"""
import argparse, fnmatch, json, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TABLE = ROOT / "tools/routes.tsv"


def load(path=TABLE):
    rows = []
    for l in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        if not l.strip() or l.lstrip().startswith("#"):
            continue
        p = l.split("\t")
        if len(p) != 7:
            raise ValueError(f"routes.tsv: expected 7 tab-separated columns, got {len(p)}: {l!r}")
        fl, fm, tl, tm, kind, pack, setup = p
        rows.append({"from_loader": fl, "from_mc": fm, "to_loader": tl, "to_mc": tm, "kind": kind,
                     "pack": None if pack == "-" else pack, "setup": setup})
    return rows


def parse(spec):
    loader, _, mc = spec.partition(":")
    if not mc:
        raise ValueError(f"expected loader:mc, got {spec!r}")
    return loader.lower(), mc


def plan(src, dst, rows=None):
    """-> list of hops (dicts with from/to added), or None. Never loops: each step must change something."""
    rows = rows if rows is not None else load()
    hops, cur, seen = [], src, {src}
    while cur != dst:
        for r in rows:
            if r["from_loader"] == cur[0] and fnmatch.fnmatch(cur[1], r["from_mc"]):
                nxt = (r["to_loader"], r["to_mc"])
                # a row is useful only if it moves toward the target: skip one that leads past a
                # stop we have already made, and prefer a row that lands on the target itself
                if nxt in seen:
                    continue
                hop = dict(r, **{"from": f"{cur[0]}:{cur[1]}", "to": f"{nxt[0]}:{nxt[1]}"})
                break
        else:
            return None
        hops.append(hop); cur = (hop["to_loader"], hop["to_mc"]); seen.add(cur)
        if len(hops) > 6:
            return None
    return hops


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--from", dest="src"); ap.add_argument("--to", dest="dst")
    ap.add_argument("--json", action="store_true"); ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not a.src or not a.dst:
        ap.error("--from and --to are required")
    hops = plan(parse(a.src), parse(a.dst))
    if hops is None:
        print(f"route: no chain of rows in tools/routes.tsv goes from {a.src} to {a.dst}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(hops, indent=1))
    else:
        for i, h in enumerate(hops, 1):
            print(f"hop {i}: {h['from']} -> {h['to']}  [{h['kind']}]  pack: {h['pack'] or 'NONE (stops unless allowed)'}")
    return 0


def self_check():
    rows = load()
    ok = True
    h = plan(("forge", "1.20.1"), ("neoforge", "26.2"), rows)
    ok &= h is not None and [x["to"] for x in h] == ["neoforge:1.21.1", "neoforge:26.2"]
    ok &= h[0]["pack"] and h[0]["setup"] == "srg" and h[1]["kind"] == "era"
    h = plan(("neoforge", "1.21.1"), ("neoforge", "26.2"), rows)
    ok &= h is not None and len(h) == 1 and h[0]["kind"] == "era"
    h = plan(("forge", "1.19.2"), ("neoforge", "1.21.1"), rows)
    ok &= h is not None and h[0]["pack"] is None
    ok &= plan(("fabric", "1.20.1"), ("neoforge", "26.2"), rows)[0]["setup"] == "intermediary"
    ok &= plan(("forge", "1.12.2"), ("neoforge", "26.2"), rows) is None      # no row: say so, do not guess
    ok &= plan(("neoforge", "1.21.4"), ("neoforge", "1.21.1"), rows)[0]["kind"] == "version"
    ok &= plan(("neoforge", "26.2"), ("neoforge", "26.2"), rows) == []
    for r in rows:   # every pack a row names must exist, or the route promises rewrites it cannot do
        ok &= r["pack"] is None or (ROOT / r["pack"]).is_file()
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
