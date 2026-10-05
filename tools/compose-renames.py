#!/usr/bin/env python3
"""Compose a mod's rename table from the hand-written rows and the two generated maps.

    python3 tools/compose-renames.py \\
        --hand templates/multi-version/versions/26.2.renames.hand.tsv \\
        --generated "$MIGRATE_WORKSPACE/moves/moves-1.21.1-to-26.2.tsv" \\
        --generated "$MIGRATE_WORKSPACE/moves/colors-1.21.1-to-26.2.tsv" \\
        --out mods/<modid>/versions/26.2.renames.tsv

The generated rows go INSIDE the hand file's `#!exhaustive` block: like the rest of the inherited
sweep, most of them are dead for any one mod by design, and they must not trip the dead-rule
check that guards the mod's own rows (catalogue X10/X11/X23).

A generated row whose `from` a hand row already covers is DROPPED, and the hand row wins. The
pipeline treats a duplicate `from` as a hard error (X31), and a hand row is a checked judgement
where the generated one is only an inference from names. A missing generated file is reported
and skipped rather than failing: the table still works, just with less coverage, and the message
says how to build it.

Standard library only.
"""
import argparse, pathlib, sys


def rows(path):
    out = []
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "\t" in line:
            out.append(tuple(line.split("\t", 1)))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hand", required=True)
    ap.add_argument("--generated", action="append", default=[])
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    hand_text = pathlib.Path(a.hand).read_text(encoding="utf-8")
    hand_from = {f for f, _ in rows(a.hand)}
    lines = hand_text.split("\n")
    at = next((i for i, l in enumerate(lines) if l.startswith("#!exhaustive")), None)
    if at is None:
        print(f"compose-renames: {a.hand} has no #!exhaustive block to put generated rows in",
              file=sys.stderr)
        return 2
    while at + 1 < len(lines) and lines[at + 1].startswith("#  "):   # the block's own comment
        at += 1

    block, seen = [], set(hand_from)
    for g in a.generated:
        p = pathlib.Path(g).expanduser()
        if not p.is_file():
            print(f"compose-renames: {p} not found -- skipped (build it with "
                  f"tools/build-class-move-map.py / tools/gen-color-renames.py)", file=sys.stderr)
            continue
        kept = dropped = 0
        block.append(f"# -- GENERATED from {p.name} (not hand-checked; a hand row always wins)")
        for f, t in rows(p):
            if f in seen:
                dropped += 1
                continue
            seen.add(f)
            block.append(f"{f}\t{t}")
            kept += 1
        print(f"compose-renames: {p.name}: {kept} row(s) added, {dropped} already covered by hand")
    out = lines[:at + 1] + block + lines[at + 1:]
    pathlib.Path(a.out).write_text("\n".join(out), encoding="utf-8")
    print(f"compose-renames: wrote {a.out} ({len(hand_from)} hand + {len(seen) - len(hand_from)} generated)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
