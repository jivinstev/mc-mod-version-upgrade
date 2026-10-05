#!/usr/bin/env python3
"""Every shipped .json must parse under a STRICT parser.

WHY THIS IS A GATE AND NOT A HABIT (§S8, and §S2's rule about steps you must remember)
--------------------------------------------------------------------------------------
1.21.1 reads a mod's JSON leniently and 26.2 does not, so a file the original author
left with a missing comma, a colon inside the key's quotes, a raw tab inside a string
or a UTF-8 BOM loads on the version people play today and is SKIPPED WHOLE on the new
one.  The evidence is one WARN line -- `Skipped language file: <ns>:lang/fr_fr.json`
-- and then silence: no crash, no failing test, and a language, a model or a book page
that simply does not exist.  A port carries such a file across without ever opening it.

Like §S6's `file(1)` check this is a FACT, not a candidate: a strict parser either
accepts the bytes or it does not, so a hit is never a judgement call and the false
positive rate is zero.  Measured across all 21 ports here: 13101 files, 5 hits, all
five genuine and all five invisible to Gate A, Gate B and a `launch`-only Gate C.

⚠ Repairs must be made in BINARY mode.  Reading a CRLF file as text and writing it
back rewrites every line ending, which turns a four-character fix into a 532-line
diff nobody can review -- measured, and reverted, while fixing a large boss mod's pl_pl.json.

Usage:  audit-json-strict.py <dir> [<dir>...]        (exits 1 naming every offender)
"""
import json
import os
import subprocess
import sys


def ignored(paths):
    """The set of PATHS git does not track. A pristine `decompiled-raw/` decompile is the author's
    own bytes, not the port's, so scanning it reports the upstream mod's typos as findings about
    this migration -- permanently non-zero on every port that keeps one, which is exactly the noise
    §X10 says teaches a reader to stop reading the check. Git is the authority on what ships;
    outside a work tree (or with no git) nothing is pruned, which is the safe direction."""
    if not paths:
        return set()
    try:
        r = subprocess.run(["git", "check-ignore", "--stdin"], input="\n".join(paths),
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return set()
    if r.returncode not in (0, 1):          # 128 = not a work tree
        return set()
    return {line for line in r.stdout.splitlines() if line}


def main(argv):
    roots = argv[1:] or ["src"]
    scanned = 0
    bad = []
    for root in roots:
        for dirpath, dirs, files in os.walk(root):
            skip = ignored([os.path.join(dirpath, d) for d in dirs])
            dirs[:] = [d for d in dirs if os.path.join(dirpath, d) not in skip]
            for name in sorted(files):
                if not name.endswith(".json"):
                    continue
                path = os.path.join(dirpath, name)
                scanned += 1
                try:
                    with open(path, encoding="utf-8") as fh:
                        json.load(fh)
                except UnicodeDecodeError as exc:
                    bad.append((path, f"not valid UTF-8: {exc}"))
                except json.JSONDecodeError as exc:
                    bad.append((path, str(exc)))

    if scanned == 0:
        # §X27: "I looked and found nothing wrong" and "I looked at nothing" print the
        # same word, so the second one is an error rather than a pass.
        print(f"json-strict: NO FILES CHECKED under {roots} -- this is NOT a pass", file=sys.stderr)
        return 2

    if bad:
        print(f"json-strict: {len(bad)} of {scanned} shipped JSON file(s) do NOT parse strictly.")
        print("  1.21.1 accepts these and 26.2 skips the whole file, with one WARN line and no other symptom.")
        for path, err in bad:
            print(f"    {path}\n      {err}")
        print("  Repair in BINARY mode so line endings survive (see this script's docstring).")
        return 1

    print(f"json-strict: {scanned} file(s), all parse strictly")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
