#!/usr/bin/env python3
"""Refuse a 26.x prepared tree that still carries @OnlyIn.  (catalogue §V79 + §W8 + §X16)

WHAT IT IS FOR.  On 26.x `@OnlyIn` strips nothing, and its mere PRESENCE is enough:
`OnlyInWarningsHandler` scans each mod file's whole annotation set and raises
`loadwarning.neoforge.onlyin` if it finds even one.  Read out of that class's bytecode rather
than assumed -- it opens `if (FMLEnvironment.isProduction()) return;`, so a SHIPPED jar is
unaffected and a player never sees it, but every gate this repo has runs in DEV:

  * Gate B (dedicated server) can die on the warning before a single test runs (§V79), and
  * Gate C gets NeoForge's LoadingErrorScreen in front of the title screen, which §X16 records
    stalling a whole run -- and which a harness that dismisses it (as ours now do) makes
    INVISIBLE.  That is how this survived a green four-target board: the guard that makes the
    gate robust is the same guard that hides the defect.

WHY IT NEEDS A TOOL RATHER THAN A RENAME ROW.  A row strips the SHARED tree and cannot reach an
OVERLAY, because §W8 deliberately does not run `src/mc26` through the rename table -- overlays
are written in the target's dialect already.  So every overlay is a fresh chance to reintroduce
it, one file at a time, and nothing says so: measured across the ports here, a large boss mod's row
took 182 occurrences to 3 and the survivors were exactly its three mc26 overlays.  §S2: a step
you must remember is not a control.

SCOPE, NOT ONLY FINDINGS (§X27).  It reads the PREPARED tree -- renames applied, overlays
merged, i.e. what actually compiles -- refuses when that tree is absent rather than falling
back to `src/main/java` (which is the CANONICAL dialect and a different question), prints how
many files it checked, and exits 2 on `checked == 0` so "looked at nothing" cannot read as
"found nothing".

It is deliberately silent on a pre-26 target: there `@OnlyIn` really does strip and is doing a
job, so removing it would be a behaviour change rather than a no-op.
"""
import glob
import os
import re
import sys

# The annotation in CODE POSITION only.  A line whose stripped form starts with `@OnlyIn` -- so a
# javadoc paragraph explaining why an overlay dropped it (one mob mod's shield model and
# a transfer library's client class both carry one) is not a hit.  A grep that counts the word in
# a comment is measuring its own noise, and the first cut of this sweep did exactly that.
ONLYIN = re.compile(r"^\s*@OnlyIn\b")

# Below this the annotation still strips, so it is legitimate and must not be reported.
FIRST_CALENDAR_ERA = 26


def era_of(target):
    """The Minecraft major, as an int -- 26 for `26.2`, 1 for `1.21.1`."""
    try:
        return int(target.split(".", 1)[0])
    except ValueError:
        return 0


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a.split("=", 1)[0]: a.split("=", 1)[1] if "=" in a else True
             for a in sys.argv[1:] if a.startswith("--")}
    ws = args[0] if args else "."
    if not os.path.isdir(ws):
        sys.exit("no such port directory: %s -- this is NOT a pass.\n"
                 "A port's DIRECTORY is not always its modId: a port in mods/spacemod may declare space_mod,\n"
                 "so a task wired with ${mod_id} rather than ${projectDir.name} lands here." % ws)
    target = flags.get("--mc")
    targets = sorted(os.path.basename(p)[:-len(".properties")]
                     for p in glob.glob(os.path.join(ws, "versions/*.properties")))
    if targets and not isinstance(target, str):
        sys.exit("%s is a multi-version port -- audit one target at a time:\n  %s"
                 % (ws, "\n  ".join("python3 tools/audit-onlyin.py %s --mc=%s" % (ws, t)
                                    for t in targets)))

    props = {}
    if isinstance(target, str):
        path = os.path.join(ws, "versions", target + ".properties")
        if not os.path.exists(path):
            sys.exit("no such target: %s\nknown: %s" % (path, " ".join(targets)))
        for line in open(path):
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                props[k.strip()] = v.strip()
        root = os.path.join(ws, "build/generated/sources", props.get("overlay", ""), "java")
    else:
        target = ""
        root = os.path.join(ws, "src/main/java")

    if target and era_of(target) < FIRST_CALENDAR_ERA:
        print("@OnlyIn audit: %s is pre-26.x, where the annotation still strips -- nothing to check"
              % target)
        return 0

    if not os.path.isdir(root):
        sys.exit("no prepared tree at %s -- run a build for this target first.\n"
                 "  This tool will NOT fall back to src/main/java: that is the CANONICAL dialect,\n"
                 "  and an overlay is exactly where the annotation survives (catalogue §W8)." % root)

    checked = 0
    hits = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(".java"):
                continue
            checked += 1
            p = os.path.join(dirpath, name)
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                for n, line in enumerate(fh, 1):
                    if ONLYIN.match(line):
                        hits.append((os.path.relpath(p, root), n, line.strip()))

    if checked == 0:
        print("NO FILES CHECKED under %s -- this is NOT a pass.\n"
              "  Either the prepared tree is empty or the scope is wrong." % root, file=sys.stderr)
        return 2

    if hits:
        print("@OnlyIn survives in the %s prepared tree (%d file(s)):" % (target, len(hits)),
              file=sys.stderr)
        for rel, n, text in hits:
            print("  %s:%d  %s" % (rel, n, text), file=sys.stderr)
        print("\nOn 26.x this strips nothing and raises loadwarning.neoforge.onlyin, which stalls\n"
              "Gate C behind NeoForge's LoadingErrorScreen and can kill Gate B outright (§V79).\n"
              "  * in SHARED source  -> add the §V79 strip row to versions/%s.renames.tsv\n"
              "  * in an OVERLAY     -> delete it by hand; §W8 means no rename row can reach it"
              % target, file=sys.stderr)
        return 1

    print("@OnlyIn audit: checked %d file(s) in the %s prepared tree -- none carries it" % (checked, target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
