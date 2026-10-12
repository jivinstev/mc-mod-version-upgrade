#!/usr/bin/env python3
"""How many MODS does each deterministic rule actually fire in? The overfitting check for the migrator's rule
tables, over a zero-model sweep that kept its per-rule evidence (docs/POPULARITY_SWEEP.md).

    python3 tools/sweep/overfit-report.py <run dir> [-v]

Reads era-rule-hits-*.json (26.x rename rows), recipes-report-*.json (Forge recipe groups), `member-row:` lines in
port.log (owner-resolved member renames) and the converter lines of port.log. A rule's count is the number of
distinct mods it rewrote anything in. -v lists the single-mod rows. Standard library only.
"""
import collections, json, re, sys
from sweeplib import ROOT, mod_dirs


def hand_rows():
    out = {}
    for l in (ROOT / "templates/multi-version/versions/26.2.renames.hand.tsv").read_text(encoding="utf-8").splitlines():
        if l.strip() and not l.startswith("#") and "\t" in l:
            f = l.split("\t", 1)[0].strip()
            key = f[3:] if f.startswith("re:") else (r"(?<=\.)" + re.escape(f[7:]) + r"\b" if f.startswith("member:") else f)
            out[key] = f
    return out


def dist(d, label, total):
    c = collections.Counter(min(len(v), 4) for v in d.values())
    print(f"\n{label}: {len(d)} rules fired at least once over {total} mods -> "
          f"1 mod: {c[1]}, 2 mods: {c[2]}, 3 mods: {c[3]}, 4+ mods: {c[4]}")


def main(run, verbose):
    hand = hand_rows()
    era, rec, mem, conv = (collections.defaultdict(set) for _ in range(4)); n_era = n_forge = 0
    for slug, d in mod_dirs(run).items():
        hj = list(d.glob("era-rule-hits-*.json"))
        n_era += bool(hj)
        for h in hj:
            for k in json.loads(h.read_text(encoding="utf-8")):
                era[k].add(slug)
        for rj in d.glob("recipes-report-*.json"):
            n_forge += 1
            for g in json.loads(rj.read_text(encoding="utf-8")).get("groups", []):
                if g.get("rewrites"):
                    rec[(g["id"], g["title"][:70])].add(slug)
        pl = (d / "port.log").read_text(encoding="utf-8", errors="replace") if (d / "port.log").exists() else ""
        for row, _ in re.findall(r"member-row: (\S+) (\d+)", pl):
            mem[row].add(slug)
        for m in re.finditer(r"mechanical-hop:\s+[\w+-]+: ([\w-]+): (\d+) file\(s\) changed", pl):
            if int(m[2]):
                conv[m[1]].add(slug)
    print(f"mods with 26.x rule-hit data: {n_era}; with Forge recipe data: {n_forge}")
    fired = {k: v for k, v in era.items() if k in hand}
    dist(fired, "26.2 HAND-written rename rows", n_era)
    dist({k: v for k, v in era.items() if k not in hand}, "26.2 GENERATED rename rows (class-move map, colours)", n_era)
    print(f"  hand rows that fired in no mod: {len(hand) - len(fired)} of {len(hand)}")
    dist(rec, "Forge recipe GROUPS", n_forge)
    dist(mem, "owner-resolved MEMBER rows", n_era + n_forge)
    print("\nconverters (mods changed):", ", ".join(f"{k} {len(v)}" for k, v in sorted(conv.items(), key=lambda x: -len(x[1]))))
    if verbose:
        print("\nsingle-mod hand rows:")
        for k, v in fired.items():
            if len(v) == 1:
                print("  ", hand[k][:100])
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "-v"]
    if len(args) != 1:
        sys.exit(__doc__)
    sys.exit(main(args[0], "-v" in sys.argv))
