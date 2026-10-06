#!/usr/bin/env python3
"""Refuse to lose a hard-won migration rule.

WHY THIS EXISTS
    The migration catalogue is the most valuable thing in this repository: every entry is a bug that
    cost somebody a day, written down so it costs nobody a day again. Its risk is not corruption, it
    is QUIET EROSION -- an entry dropped during a reshuffle, a merge resolved the lazy way, a
    "tidy-up" that removes a section nobody recognised the value of. Nothing complains, and the loss
    surfaces months later as a bug that was already solved once.

    So the census below is the inventory: every entry id plus a hash of its body. A disappearance is
    a hard failure. A change is reported, never blocked -- editing a rule is normal, losing one is not.

USAGE
    python3 tools/check-catalog-fidelity.py --update    # regenerate the census (review the diff!)
    python3 tools/check-catalog-fidelity.py             # verify; non-zero if an entry vanished

EXIT CODES     0 clean    1 an entry disappeared, or the count fell    2 could not run
"""
import argparse, hashlib, pathlib, re, sys

# Every shape an entry id takes in the catalogue. The first version of this gate knew only two of
# them and recognised 56 of ~450 entries -- a fidelity gate blind to nine rules in ten reports
# "nothing lost" exactly as loudly as one that looked.
ENTRY_RES = [
    re.compile(r'^#{2,4}\s+(?:§\s*)?([A-Z]{1,2}\d*[a-z]?)[.)]\s', re.M),   # ## V. / ### V42c.
    re.compile(r'^\*\*([A-Z]{1,2}\d+[a-z]?)\.', re.M),                    # **V42c. ...
    re.compile(r'^- \*\*([A-Z]{1,2}\d+[a-z]?)\.', re.M),                  # - **M7. ...
    re.compile(r'^([A-Z]{1,2}\d*[a-z]?)\.\s', re.M),                       # R9b. / M7. / NN.
    re.compile(r'^(\d{1,3}[a-z]?)\.\s+\*\*', re.M),                       # 123. **...
]


def entries(text):
    """-> {id: body-hash}. Body runs to the next entry, so an edit changes only its own hash.
    An id that occurs more than once (the catalogue reuses a few numbers across sections) is keyed
    `id#2`, `id#3`, ... in order, so a repeat can neither shadow nor hide its twin."""
    marks = set()
    for rx in ENTRY_RES:
        for m in rx.finditer(text):
            marks.add((m.start(), m.group(1)))
    marks = sorted(marks)
    out, seen = {}, {}
    for i, (pos, ident) in enumerate(marks):
        if i and marks[i - 1][0] == pos:
            continue
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        body = re.sub(r'\s+', ' ', text[pos:end]).strip()
        seen[ident] = seen.get(ident, 0) + 1
        key = ident if seen[ident] == 1 else f"{ident}#{seen[ident]}"
        out[key] = hashlib.sha1(body.encode()).hexdigest()[:16]
    return out


def collect(root, globs):
    found = {}
    for g in globs:
        for f in sorted(root.glob(g)):
            if f.is_file():
                for ident, h in entries(f.read_text(errors="replace", encoding="utf-8")).items():
                    found[f"{f.relative_to(root).as_posix()}::{ident}"] = h
    return found


PATH_RE = re.compile(r'(?:^|[^/A-Za-z0-9_.-])((?:tools|templates)/[A-Za-z0-9_./-]+'
                     r'\.(?:py|sh|gradle|command|template|example|md|tsv|properties))')


def dangling_paths(root, globs):
    """Every tools/... or templates/... path the catalogue names must exist, unless it is listed
    with a reason in docs/catalog-external-paths.tsv (a per-mod copy, another repo's tool)."""
    allowed = set()
    ext = root / "docs/catalog-external-paths.tsv"
    if ext.is_file():
        for line in ext.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and "\t" in line and not line.startswith("path\t"):
                allowed.add(line.split("\t", 1)[0])
    out = {}
    for g in globs:
        for f in sorted(root.glob(g)):
            if not f.is_file():
                continue
            for m in PATH_RE.finditer(f.read_text(errors="replace", encoding="utf-8")):
                p = m.group(1).rstrip(".")
                if p not in allowed and not (root / p).exists():
                    out.setdefault(p, f.relative_to(root).as_posix())
    return sorted(out.items())


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--census", default="docs/catalog-census.tsv")
    ap.add_argument("--globs", nargs="*",
                    default=["CATALOG.md", "docs/catalog/*.md", ".claude/skills/*/SKILL.md",
                             ".claude/skills/*/references/*.md"])
    ap.add_argument("--update", action="store_true")
    a = ap.parse_args()

    root = pathlib.Path(a.root).resolve()
    census = root / a.census
    found = collect(root, a.globs)

    if not found and not census.exists():
        # Chunk 1 state: the gate exists before the catalogue does. Say so out loud -- a check that
        # silently found nothing to do is indistinguishable from one that passed on real content.
        print("check-catalog-fidelity: no catalogue files yet and no census — NOTHING CHECKED.")
        print("                        This is expected only before the catalogue lands.")
        return 0

    if a.update:
        census.parent.mkdir(parents=True, exist_ok=True)
        census.write_text("# entry\tbody-sha1  (generated by tools/check-catalog-fidelity.py --update)\n"
                          + "".join(f"{k}\t{v}\n" for k, v in sorted(found.items())), encoding="utf-8")
        print(f"check-catalog-fidelity: wrote {len(found)} entries -> {census.relative_to(root)}")
        print("                        REVIEW THE DIFF: a removal here is a rule being deleted.")
        return 0

    if not census.exists():
        print(f"check-catalog-fidelity: FAIL — {a.census} is missing but {len(found)} entries exist.",
              file=sys.stderr)
        print("                        Run --update and commit it.", file=sys.stderr)
        return 2

    prev = {}
    for line in census.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or "\t" not in line:
            continue
        k, v = line.split("\t", 1)
        prev[k] = v.strip()

    gone = sorted(set(prev) - set(found))
    changed = sorted(k for k in set(prev) & set(found) if prev[k] != found[k])
    added = sorted(set(found) - set(prev))

    print(f"check-catalog-fidelity: {len(found)} entries now, {len(prev)} in the census")
    for k in added:
        print(f"  + new     {k}")
    for k in changed:
        print(f"  ~ changed {k}   (edits are fine; this is here so the diff is visible)")

    if gone:
        print(f"\ncheck-catalog-fidelity: FAIL — {len(gone)} entr{'y' if len(gone)==1 else 'ies'} DISAPPEARED:",
              file=sys.stderr)
        for k in gone:
            print(f"  - {k}", file=sys.stderr)
        print("\n  Each of these is a rule somebody paid for. If a removal is genuinely intended,",
              file=sys.stderr)
        print("  rerun with --update and say why in the commit message.", file=sys.stderr)
        return 1

    if len(found) < len(prev):
        print(f"\ncheck-catalog-fidelity: FAIL — entry count fell {len(prev)} -> {len(found)}.",
              file=sys.stderr)
        return 1

    missing = dangling_paths(root, a.globs)
    if missing:
        print(f"\ncheck-catalog-fidelity: FAIL — the catalogue names {len(missing)} path(s) that do not exist:",
              file=sys.stderr)
        for p, where in missing:
            print(f"  {p}   (named in {where})", file=sys.stderr)
        print("  Restore the file, fix the reference, or list it with a reason in "
              "docs/catalog-external-paths.tsv.", file=sys.stderr)
        return 1

    print("check-catalog-fidelity: PASS — nothing lost, and every tools/ and templates/ path it names exists.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
