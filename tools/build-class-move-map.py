#!/usr/bin/env python3
"""Build a CLASS MOVE MAP between two Minecraft versions by diffing real compile classpaths.

WHY THIS EXISTS
---------------
The 1.21.x -> 26.x jump is dominated by a *sub-package reorganisation*: Mojang split
flat packages into per-family sub-packages (monster.Zombie -> monster.zombie.Zombie,
animal.Cow -> animal.cow.Cow, ...). Measured 1.21.1 -> 26.2: 339 net.minecraft classes
moved with an IDENTICAL simple name. Those are mechanical and must never be hand-fixed
one compile error at a time -- that is how a port burns a week and still misses some.

A class whose simple name is unchanged and which vanished from exactly one package and
appeared in exactly one other is a MOVE. Anything else is a real API change and is
reported separately so it cannot hide inside the mechanical bucket.

USAGE
  # In each version's project (a ModDevGradle scaffold is enough), dump the classpath:
  #   tasks.register('dumpCp') { doLast {
  #       new File(projectDir,"cp.txt").text =
  #           sourceSets.main.compileClasspath.files.collect{it.absolutePath}.join("\n") } }
  python3 tools/build-class-move-map.py --from-cp old/cp.txt --to-cp new/cp.txt \
      --out "$MIGRATE_WORKSPACE/moves/moves-1.21.1-to-26.2.tsv"

  The generated maps are deliberately NOT committed to this repository: they are derived from
  Mojang's mapping data, so each user builds their own from classpaths they already have. Anyone
  who needs a map is already migrating between those two versions, so both classpaths exist.

  # Then apply to a mod's source:
  python3 tools/build-class-move-map.py --apply <map.tsv> --src mods/<modid>/src/main/java
"""
import argparse, os, re, sys, zipfile


# Only the PLATFORM's own packages. Measured 1.21.1 -> 1.21.4: with every jar on the compile
# classpath in scope, a library UPGRADE posed as a game-API move -- `org.antlr...Token` ->
# `com.nimbusds...Token`, `org.antlr...Triple` -> `kotlin.Triple`, and a deleted Mojang
# `blaze3d.shaders.Effect` -> `kotlin.contracts.Effect` -- because the newer target happens to
# carry kotlin-stdlib and a newer antlr. 169 of 390 "removed" rows were antlr alone. A mod port
# never renames a library's classes through this table, so they are noise in both buckets.
# --all-packages restores the old behaviour for the rare case that wants it.
PLATFORM_PREFIXES = ('net.minecraft.', 'com.mojang.', 'net.neoforged.', 'cpw.mods.')


def classes_of(cp_file, prefixes=PLATFORM_PREFIXES):
    """Every non-inner PLATFORM class on a compile classpath, as dotted FQNs."""
    out = set()
    for line in open(cp_file):
        jar = line.strip()
        if not jar or not os.path.isfile(jar):
            continue
        try:
            with zipfile.ZipFile(jar) as z:
                for n in z.namelist():
                    if n.endswith('.class') and '$' not in n:
                        fq = n[:-6].replace('/', '.')
                        # package-info is a per-package marker, not a type: its simple
                        # name collides across every package and would swamp the
                        # ambiguous bucket with hundreds of meaningless candidates.
                        if fq.rsplit('.', 1)[-1] == 'package-info':
                            continue
                        if prefixes and not fq.startswith(prefixes):
                            continue
                        out.add(fq)
        except zipfile.BadZipFile:
            continue
    return out


def build(old_cp, new_cp, out_path, prefixes=PLATFORM_PREFIXES):
    a, b = classes_of(old_cp, prefixes), classes_of(new_cp, prefixes)
    if not a or not b:
        # A scope that selected nothing is not an empty diff (§X27's checked == 0).
        sys.exit(f"no platform classes on one side (old={len(a)} new={len(b)}) -- "
                 "is the Minecraft jar on the classpath file? (NeoGradle: build/neoForm/*/steps/"
                 "recompile/outputs.jar; the ng_dummy_ng neoforge jar is a 197-byte placeholder)")
    gone, new = a - b, b - a
    by_leaf = {}
    for n in new:
        by_leaf.setdefault(n.rsplit('.', 1)[-1], []).append(n)

    moves, ambiguous, removed = [], [], []
    for g in sorted(gone):
        leaf = g.rsplit('.', 1)[-1]
        cands = by_leaf.get(leaf, [])
        if len(cands) == 1:
            moves.append((g, cands[0]))
        elif len(cands) > 1:
            ambiguous.append((g, cands))   # never auto-apply these
        else:
            removed.append(g)

    with open(out_path, 'w') as f:
        for x, y in moves:
            f.write(f"{x}\t{y}\n")

    base = os.path.splitext(out_path)[0]
    with open(base + '.removed.txt', 'w') as f:
        f.write("# Gone with no same-named replacement: a REAL API change, port by hand.\n")
        f.write("\n".join(removed) + "\n")
    with open(base + '.ambiguous.txt', 'w') as f:
        f.write("# Same simple name landed in >1 new package -- choose by hand, never auto-apply.\n")
        for g, c in ambiguous:
            f.write(f"{g}\t{','.join(c)}\n")

    print(f"old={len(a)} new={len(b)}")
    print(f"unambiguous MOVES : {len(moves)}  -> {out_path}")
    print(f"ambiguous         : {len(ambiguous)}  -> {base}.ambiguous.txt (hand-pick)")
    print(f"removed/renamed   : {len(removed)}  -> {base}.removed.txt (real work)")


def apply(map_path, src_root):
    moves = {}
    for line in open(map_path):
        if '\t' in line:
            o, n = line.rstrip('\n').split('\t')
            moves[o] = n
    # Longest-first so a.b.C never gets rewritten by a prefix rule for a.b
    keys = sorted(moves, key=len, reverse=True)
    pat = re.compile(r'\b(' + '|'.join(re.escape(k) for k in keys) + r')\b')

    changed = hits = 0
    for dirpath, _, files in os.walk(src_root):
        for fn in files:
            if not fn.endswith('.java'):
                continue
            p = os.path.join(dirpath, fn)
            s = open(p, encoding='utf-8').read()
            new, n = pat.subn(lambda m: moves[m.group(1)], s)
            if n:
                open(p, 'w', encoding='utf-8').write(new)
                changed += 1
                hits += n
    print(f"rewrote {hits} references across {changed} files")
    print("NOTE: re-grep for leftovers; a simple-name-only usage (no FQN, no import) is")
    print("      untouched by design -- the import line is what carries the package.")


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--from-cp'); ap.add_argument('--to-cp'); ap.add_argument('--out')
    ap.add_argument('--apply'); ap.add_argument('--src')
    ap.add_argument('--all-packages', action='store_true',
                    help='diff every class on the classpath, libraries included (noisy)')
    a = ap.parse_args()
    if a.apply:
        if not a.src:
            sys.exit("--apply needs --src")
        apply(a.apply, a.src)
    elif a.from_cp and a.to_cp and a.out:
        build(a.from_cp, a.to_cp, a.out, prefixes=None if a.all_packages else PLATFORM_PREFIXES)
    else:
        ap.print_help(); sys.exit(2)
