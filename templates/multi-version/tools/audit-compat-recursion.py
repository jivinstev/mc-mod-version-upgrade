#!/usr/bin/env python3
"""A §W5 compat pair over an OVERRIDE, written as one over a CALL, recurses forever.

The shape, measured once and fatal (a large boss mod, catalogue §W5b):

    // shared source
    public AABB getBoundingBoxForCulling() { return Entities.cullingBox(this).inflate(3.0); }
    // src/mc21 half of the pair
    public static AABB cullingBox(Entity e) { return e.getBoundingBoxForCulling(); }

The override calls the pair, the pair calls the override: StackOverflowError. It compiles on both
targets and it fails on ONE of them -- whichever half happens to answer with the same-named vanilla
method -- so the burn-down cannot see it (§X14) and only the §X15 control run finds it.

Why it is a scan and not a lesson: the honest fix is a pair over the override (a per-target base
class, §V41), and the tempting one is a static helper, which cannot make a `super` call. That is a
decision every §W port makes dozens of times.

It is target-INDEPENDENT: it reads every `src/mc*/java/**/compat` half, because either half can be
the one that re-enters.

Usage:  audit-compat-recursion.py mods/<modid>
        audit-compat-recursion.py --src <dir> --compat <dir> [--compat <dir>]
        audit-compat-recursion.py --self-check   # plant the offender; assert caught, then clean
Exit:   0 clean · 1 finding · 2 nothing checked (which is NOT a pass -- §X27)
"""
import argparse, pathlib, re, sys, tempfile

METHOD = re.compile(r'public static [\w.<>\[\]]+ (\w+)\(([^)]*)\)\s*\{(.*?)\n   \}', re.S)
# an override body that hands `this` to a compat method
CALL = re.compile(r'(?:\.)?(\w+)\.(\w+)\(\s*this\s*[,)]')
OVERRIDE = re.compile(
    r'\n   (?:@\w+\s+)*(?:public|protected)[\w\s<>,.\[\]]*?\b(\w+)\(([^)]*)\)\s*\{(.*?)\n   \}', re.S)


def pair_index(compat_dirs):
    """(ClassSimpleName, staticMethod) -> {vanilla methods it calls on its first parameter}."""
    idx, files = {}, 0
    for d in compat_dirs:
        for f in sorted(pathlib.Path(d).glob('*.java')):
            files += 1
            src = f.read_text()
            for m in METHOD.finditer(src):
                name, params, body = m.group(1), m.group(2), m.group(3)
                if not params.strip():
                    continue
                recv = params.split(',')[0].strip().split()[-1]
                called = {c.group(1) for c in re.finditer(re.escape(recv) + r'\.(\w+)\(', body)}
                if called:
                    idx[(f.stem, name)] = called
    return idx, files


def scan(src_root, idx):
    hits = []
    for f in sorted(pathlib.Path(src_root).rglob('*.java')):
        text = f.read_text()
        for m in OVERRIDE.finditer(text):
            mname, body = m.group(1), m.group(3)
            for c in CALL.finditer(body):
                vanilla = idx.get((c.group(1), c.group(2)))
                if vanilla and mname in vanilla:
                    hits.append((f, mname, f'{c.group(1)}.{c.group(2)}'))
    return hits


def self_check():
    """A guard you cannot A/B is a guard equally consistent with matching nothing (§X14)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        compat = root / 'compat'; shared = root / 'shared' / 'x'
        compat.mkdir(parents=True); shared.mkdir(parents=True)
        (compat / 'Probe.java').write_text(
            'class Probe {\n'
            '   public static int widthOf(Thing t) {\n'
            '      return t.probeWidth();\n'
            '   }\n'
            '}\n')
        bad = ('class Sub {\n'
               '   public int probeWidth() {\n'
               '      return Probe.widthOf(this) + 1;\n'
               '   }\n'
               '}\n')
        good = ('class Sub {\n'
                '   public int probeWidth() {\n'
                '      return this.baseWidth() + 1;\n'
                '   }\n'
                '}\n')
        idx, _ = pair_index([compat])
        if not idx:
            print('self-check FAILED: the probe pair was not indexed'); return 1
        (shared / 'Sub.java').write_text(bad)
        if not scan(shared, idx):
            print('self-check FAILED: the planted recursion was NOT caught'); return 1
        (shared / 'Sub.java').write_text(good)
        if scan(shared, idx):
            print('self-check FAILED: the fixed form was reported'); return 1
    print('self-check ok: planted recursion caught, fixed form clean')
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mod', nargs='?', help='mods/<modid> (defaults to the working directory)')
    ap.add_argument('--src')
    ap.add_argument('--compat', action='append', default=[])
    ap.add_argument('--self-check', action='store_true')
    a = ap.parse_args()
    if a.self_check:
        sys.exit(self_check())
    root = pathlib.Path(a.mod) if a.mod else pathlib.Path('.')
    a.src = a.src or str(root / 'src/main/java')
    # X27's invariant is "assert the SCOPE, not only the findings" -- but the scope here has two
    # levels, and collapsing them fails a port that is perfectly fine. `src/mc*/java` missing means
    # this is not a §W tree at all (or we are pointed at the wrong directory): that is "I looked at
    # nothing" and must exit 2. Overlay roots present with no `compat` package underneath is a
    # different statement -- it says this port needs no compat pairs yet, which is a real reading of
    # a real tree. Print both numbers either way so a collapse from N roots to 0 stays visible.
    overlay_roots = [p for p in root.glob('src/mc*/java') if p.is_dir()] if not a.compat else []
    compat = a.compat or [str(p) for p in root.glob('src/mc*/java/**/compat')]
    compat = [d for d in compat if pathlib.Path(d).is_dir()]
    if not compat and not a.compat and not overlay_roots:
        print('compat-recursion: NO OVERLAY ROOTS (src/mc*/java) -- not a multi-version tree, '
              'or pointed at the wrong directory. This is NOT a pass.')
        sys.exit(2)
    if not compat:
        print(f'compat-recursion: {len(overlay_roots)} overlay root(s), 0 compat dir(s) -- this port '
              f'declares no compat pairs, so there is nothing that can recurse.')
        sys.exit(0)
    idx, files = pair_index(compat)
    if not idx:
        print(f'compat-recursion: {files} file(s) but NO PAIR METHODS INDEXED -- this is NOT a pass')
        sys.exit(2)
    hits = scan(a.src, idx)
    for f, mname, pair in hits:
        print(f'  RECURSION: {f}:{mname}() calls {pair}(this), which calls {mname}() back')
    print(f'compat-recursion: checked {len(idx)} pair method(s) over {files} compat file(s); '
          f'{len(hits)} finding(s)')
    sys.exit(1 if hits else 0)


if __name__ == '__main__':
    main()
