#!/usr/bin/env python3
"""Rank the compile errors a zero-model sweep left by how many MODS each signature hits, not how many errors:
a converter is worth building only for a signature that recurs across many mods (docs/POPULARITY_SWEEP.md).

    python3 tools/sweep/rank-patterns.py <run dir>... [--target 26.2] [--top 60] [--examples]

A signature is (target, javac message with type names folded, missing symbol, owner type). Standard library only.
"""
import argparse, collections, re, sys
from sweeplib import mod_dirs, summary, final_log

ERR = re.compile(r"^(/\S+\.java):(\d+): error: (.*)$")


def signatures(text):
    """[(signature, code line)] for every distinct file:line error in one javac log."""
    out, seen, lines = [], set(), text.split("\n")
    for i, l in enumerate(lines):
        m = ERR.match(l)
        if not m or (m[1], m[2]) in seen:
            continue
        seen.add((m[1], m[2]))
        sym = loc = ""
        for x in lines[i + 1:i + 7]:
            x = x.strip()
            if x.startswith("symbol:"):
                sym = re.sub(r"\(.*", "(..)", x[7:].strip())
            elif x.startswith("location:"):
                loc = re.sub(r"<.*", "", re.sub(r"^location:\s+(variable \w+ of type |class |interface )?", "", x)).split(".")[-1]
                break
        msg = re.sub(r"\b[A-Z]\w*(?:\.\w+)*\b(?= (?:cannot|is not|does not))", "T", m[3])
        msg = re.sub(r"'[^']*'|\"[^\"]*\"", "…", msg)[:110]
        out.append(((msg, sym, loc) if "cannot find symbol" in msg else (msg, "", ""),
                    lines[i + 1].strip() if i + 1 < len(lines) else ""))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("runs", nargs="+"); ap.add_argument("--target"); ap.add_argument("--top", type=int, default=60)
    ap.add_argument("--examples", action="store_true", help="print one code line per signature")
    a = ap.parse_args()
    mods = collections.defaultdict(set); errs = collections.Counter(); ex = {}; n = 0
    for slug, d in mod_dirs(*a.runs).items():
        tgt = summary(d).get("target")
        if a.target and tgt != a.target:
            continue
        text = final_log(d)
        if not text:
            continue
        n += 1
        for sig, code in signatures(text):
            s = (tgt,) + sig
            mods[s].add(slug); errs[s] += 1; ex.setdefault(s, code)
    print(f"mods with a remaining-error log: {n}")
    for s in sorted(mods, key=lambda s: (-len(mods[s]), -errs[s]))[:a.top]:
        print(f"{len(mods[s]):4d} mods {errs[s]:6d} errs  {s[0]:6s} {s[1][:70]} | {s[2][:45]} | {s[3]}")
        if a.examples:
            print("        e.g.", ex[s][:140])
    return 0


if __name__ == "__main__":
    sys.exit(main())
