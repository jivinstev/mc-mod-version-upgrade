#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Jason Hendrickson
"""Materialise the source tree for ONE Minecraft target from the shared tree.

THE PROBLEM THIS SOLVES
-----------------------
A ~460-file builder mod builds for several Minecraft versions from one source tree. Most of the
code is genuinely portable (measured 1.21.1 -> 26.2: 86% of imports unchanged), but two
kinds of difference cannot live in a single shared .java file:

  1. MECHANICAL renames -- a type or package that changed name with an identical API.
     `net.minecraft.resources.ResourceLocation` became `...resources.Identifier`;
     `...entity.monster.Zombie` became `...entity.monster.zombie.Zombie`. Java has no
     type alias, so a shared file cannot name both.
  2. REAL differences -- a changed method signature, a rewritten client-render call.

(1) is data: a rename table per target. (2) is code: an overlay file per target.
Everything else stays shared and is written once.

So the pipeline is: shared tree -> apply this target's rename table -> let this target's
overlay REPLACE whole files -> compile. Both targets go through it identically (the
canonical dialect just has an empty table), so the mechanism is exercised by every build
rather than only by the version nobody is testing today.

WHY A RENAME TABLE RATHER THAN A PREPROCESSOR
Version-gated comment blocks put the branching back inside every file, which is the
thing being avoided. A table is reviewable in one place, is generated from the two real
compile classpaths (tools/build-class-move-map.py in the migrator repo), and cannot
silently drift into hand-written per-version logic.

USAGE
  prepare-sources.py --src src/main/java --overlay src/mc26/java \
                     --renames versions/26.2.renames.tsv --out build/generated/sources/mc26/java
"""
import argparse, glob, os, re, shutil, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def load_renames(path):
    """TSV of `from<TAB>to`. Blank lines and #-comments ignored.

    Three kinds of row:
      plain      a TYPE rename, matched on identifier boundaries and deliberately NOT
                 after a dot, so `a.b.Foo` is never half-rewritten by a rule for `Foo`.
      member:    a MEMBER rename (method or field), matched ONLY after a dot. This kind
                 exists because the type rule's no-dot guard blocks exactly the position
                 a call site occupies -- `x.oldName()` -- so member renames written as
                 plain rows silently do nothing.
      re:        a raw regex, for anything that is not a name swap at all.

    Regex rules exist because not every version difference is a token swap -- a field
    that became a method, or a boolean argument that chose between two new methods,
    cannot be expressed as `A -> B`. They are applied BEFORE the token pass (see the
    ORDER MATTERS comment in main(): a shape rule naming a type can never fire once the
    broad sweep has already renamed that type) and among themselves in file order, so a
    table stays readable top-to-bottom.
    """
    out = []
    rows = []
    exhaustive, exhaustive_line = False, 0
    if not path or not os.path.isfile(path):
        return out
    for lineno, raw in enumerate(open(path, encoding='utf-8'), 1):
        line = raw.rstrip('\n')
        # `#!exhaustive` marks a GENERATED block (e.g. every colour x family combination)
        # where a rule matching nothing is expected, not a bug. `#!strict` ends it. Without
        # this the dead-rule warning drowns in hundreds of unused generated rows and stops
        # being read -- which would defeat the point of having it.
        #
        # The scope is a BLOCK and it must be CLOSED, because the alternative was measured:
        # one marker whose `#!strict` was never written exempted 699 of 719 rules, leaving
        # the dead-rule detector looking at twenty. That is worse than not having it -- a
        # detector reporting "all clear" over 3% of the table reads exactly like one that
        # checked everything. An unclosed block is a hard error now, so the leak cannot be
        # silent. A trailing `# ...` comment on the marker is allowed (and encouraged: a
        # marker with no stated reason is how the next reader learns nothing) -- it used to
        # make the line parse as an ordinary comment, so the marker did nothing at all.
        marker = line.split('#', 2)[1].strip() if line.lstrip().startswith('#!') else ''
        if marker.split()[0:1] == ['!exhaustive']:
            exhaustive, exhaustive_line = True, lineno
            continue
        if marker.split()[0:1] == ['!strict']:
            exhaustive = False
            continue
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if '\t' not in line:
            sys.exit(f"{path}: expected 'from<TAB>to', got: {line!r}")
        frm, to = line.split('\t', 1)
        out.append((frm.strip(), to.strip(), exhaustive))
        rows.append((lineno, out[-1]))
    if exhaustive:
        sys.exit(f"{path}:{exhaustive_line}: '#!exhaustive' block was never closed with "
                 f"'#!strict' -- every rule after it would be silently exempt from the "
                 f"dead-rule check.")
    # A DUPLICATE `from` key is invisible to the dead-rule detector, because hits are keyed
    # by the pattern TEXT: the first row's match marks the second one live, so a redundant
    # row reads as a working rule forever. And for a token rule the second half is worse --
    # `build_pattern` collapses the table with `{f: t for ...}`, so two rows disagreeing on
    # the replacement silently DISCARD the earlier one and the later one wins with no
    # warning anywhere. Both halves fail in the reassuring direction, so this is a hard
    # error rather than a warning (X10: a warning that is normally non-zero is decoration).
    firsts = {}
    for lineno, (frm, to, _) in rows:
        if frm in firsts:
            prev_line, prev_to = firsts[frm]
            if prev_to == to:
                sys.exit(f"{path}:{lineno}: duplicate rule {frm!r} (first at line "
                         f"{prev_line}, same replacement) -- delete one. A duplicate is "
                         f"invisible to the dead-rule check, because its twin's hit marks "
                         f"it live.")
            sys.exit(f"{path}:{lineno}: duplicate rule {frm!r} (first at line {prev_line}) "
                     f"with a DIFFERENT replacement: {prev_to!r} then {to!r}. Only the last "
                     f"one takes effect and nothing reports the loss -- delete or anchor one.")
        firsts[frm] = (lineno, to)

    # An IDENTITY rule -- one whose replacement is exactly what its pattern matches -- is the
    # same blind spot from the other side. It fires on every run, so the dead-rule check counts
    # it LIVE and reports all-clear over a rule that does nothing. That is not merely noise:
    # the one this check was written for sat directly below the row producing its input and hid
    # that that row was wrong (measured: it had propagated to all ten ports and the template).
    # Hard error rather than a warning, for X10's reason, and there are zero violations today.
    for lineno, (frm, to, _) in rows:
        literal = frm
        if literal.startswith('re:'):
            pat = literal[3:]
            # only judge patterns with no unescaped metacharacters; anything else can't be
            # compared as a literal and is left alone rather than guessed at
            stripped = re.sub(r'\\.', '', pat)
            if re.search(r'[(\[|+*?^$.]', stripped):
                continue
            literal = re.sub(r'\\(.)', r'\1', pat)
        if literal == to:
            sys.exit(f"{path}:{lineno}: identity rule {frm!r} -- its replacement is exactly "
                     f"what it matches, so it rewrites nothing while the dead-rule check "
                     f"counts it live. Delete it.")
    return out


def split_rules(renames):
    """Split into token (type), member and regex rules, preserving order within each."""
    tokens, regexes = [], []
    for frm, to, exhaustive in renames:
        if frm.startswith('re:'):
            regexes.append((re.compile(frm[3:]), to, exhaustive))
        elif frm.startswith('member:'):
            name = frm[len('member:'):]
            # Match only after a dot, and not where it is already a longer identifier.
            regexes.append((re.compile(r'(?<=\.)' + re.escape(name) + r'\b'), to, exhaustive))
        else:
            tokens.append((frm, to, exhaustive))
    return tokens, regexes


def build_pattern(renames):
    if not renames:
        return None, {}
    # Longest first so `a.b.C` is never clipped by a rule for `a.b`.
    table = {f: t for f, t, _ in renames}
    keys = sorted(table, key=len, reverse=True)
    return re.compile(r'(?<![\w.])(' + '|'.join(re.escape(k) for k in keys) + r')(?![\w])'), table


def java_files(root):
    for dirpath, _, files in os.walk(root):
        for fn in files:
            if fn.endswith('.java'):
                full = os.path.join(dirpath, fn)
                yield full, os.path.relpath(full, root)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--overlay')
    ap.add_argument('--renames')
    ap.add_argument('--out', required=True)
    # ONE rename table is applied to SEVERAL source roots (src/main/java and src/test/java),
    # so "this rule matched nothing" is only meaningful across ALL of them. Checked per root it
    # is guaranteed noise: the 72-file test tree does not use most of the API, so it reported
    # 184 dead rules on every build -- sitting directly beside the main tree's hard-won ZERO and
    # training the reader to skip both. So the first root COLLECTS its hits and the last root
    # MERGES them and reports.
    ap.add_argument('--hits-out', metavar='FILE',
                    help='write this root\'s matched rules here and skip the dead-rule report '
                         '(for a root that shares its table with a later one)')
    ap.add_argument('--hits-in', metavar='FILE',
                    help='merge rules matched by an earlier root before reporting dead ones')
    # A file the NEW target must not compile AT ALL. The motivating case is a mixin whose
    # vanilla target class was deleted (catalogue V43: DimensionSpecialEffects is gone on 26.2),
    # where there is nothing to rewrite and nothing to overlay -- the class has no reason to
    # exist on that version. An overlay holding an inert stand-in would leave dead code with a
    # javadoc explaining why it does nothing, which is worse than its absence.
    #
    # A drop is a FEATURE LOSS, so it is recorded twice on purpose: here, and in the target's
    # own mixin config / MIGRATION.md. A stale entry is a hard error rather than a shrug --
    # a drop list that silently stops matching is X11's suppression marker wearing a hat.
    ap.add_argument('--drop', metavar='FILE',
                    help='file listing source-relative paths this target must not compile')
    ap.add_argument('--gametest-adapter', metavar='MODID',
                    help="Rewire annotation-driven @GameTests for Minecraft 26.x, which has "
                         "no @GameTest annotation. Runs over the prepared tree.")
    ap.add_argument('--gametest-pkg', metavar='PKG',
                    help="Package for the generated GameTest registrar "
                         "(default: com.<modid>.compat).")
    ap.add_argument('--gametest-structure', metavar='NAME', default='empty_test',
                    help="Default structure for tests that name none (default: empty_test).")
    a = ap.parse_args()

    overlay_rel = set()
    if a.overlay and os.path.isdir(a.overlay):
        overlay_rel = {rel for _, rel in java_files(a.overlay)}

    dropped_rel = set()
    if a.drop and os.path.isfile(a.drop):
        for line in open(a.drop, encoding='utf-8'):
            line = line.split('#', 1)[0].strip()
            if line:
                dropped_rel.add(line)
        missing = sorted(r for r in dropped_rel if not os.path.isfile(os.path.join(a.src, r)))
        if missing:
            sys.exit('%s names %d path(s) that are not in %s: %s'
                     % (a.drop, len(missing), a.src, ', '.join(missing)))

    token_rules, regex_rules = split_rules(load_renames(a.renames))
    pattern, table = build_pattern(token_rules)

    if os.path.isdir(a.out):
        shutil.rmtree(a.out)
    os.makedirs(a.out, exist_ok=True)

    shared = replaced = rewrites = dropped = 0
    # Count hits per rule. A rule that matches NOTHING is the failure mode this tool has
    # actually suffered: a member rename blocked by the type rule's no-dot guard, and a
    # regex whose escapes were mangled on the way into the file. Both were silently inert
    # while the error count still fell for other reasons, so silence is not evidence.
    hits = {}
    for full, rel in java_files(a.src):
        # An overlay file REPLACES its shared counterpart wholesale. That is the escape
        # hatch for a real difference; renames handle everything mechanical.
        if rel in overlay_rel:
            replaced += 1
            continue
        if rel in dropped_rel:
            dropped += 1
            continue
        text = open(full, encoding='utf-8').read()
        # ORDER MATTERS, and getting it wrong is silent. Regex rules encode whole call
        # SHAPES (`InteractionResultHolder.success(stack)`), token rules are the broad
        # type sweep (`InteractionResultHolder` -> `InteractionResult`). With the sweep
        # first, the shape rules can never match -- the type in them has already been
        # renamed -- so they quietly do nothing and the call site stays broken in a new
        # way. Specific before general.
        for rx, repl, _ex in regex_rules:
            text, n = rx.subn(repl, text)
            if n:
                hits[rx.pattern] = hits.get(rx.pattern, 0) + n
            rewrites += n
        if pattern is not None:
            def _tok(m):
                hits[m.group(1)] = hits.get(m.group(1), 0) + 1
                return table[m.group(1)]
            text, n = pattern.subn(_tok, text)
            rewrites += n
        dest = os.path.join(a.out, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, 'w', encoding='utf-8') as fh:
            fh.write(text)
        shared += 1

    for full, rel in ([] if not a.overlay else java_files(a.overlay)):
        dest = os.path.join(a.out, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(full, dest)

    # Report an overlay file that shadows nothing: it is almost always a stale path left
    # behind by a rename, and it would sit there doing nothing with no other signal.
    #
    # ...with ONE legitimate exception, which has to be excluded or the note fires on every
    # build and stops being read. A COMPAT PAIR (§W5 -- Msg, Nbt, SpawnEggs, ClientTestWorlds)
    # is a class that exists ONLY as one implementation per target, so by construction it
    # shadows nothing. The test that tells the two apart is whether a sibling overlay carries
    # the same path: present in every overlay = a deliberate pair; present in exactly one =
    # a stale path. A warning that cries wolf four times a build is worse than no warning.
    src_rel = {rel for _, rel in java_files(a.src)}
    paired = set()
    if a.overlay:
        overlays_root = os.path.dirname(os.path.dirname(os.path.abspath(a.overlay)))
        for sib in sorted(glob.glob(os.path.join(overlays_root, 'mc*', 'java'))):
            if os.path.abspath(sib) == os.path.abspath(a.overlay) or not os.path.isdir(sib):
                continue
            paired |= {rel for _, rel in java_files(sib)}
    for rel in sorted(overlay_rel - src_rel - paired):
        # ...and one more exemption, for the same reason the pair test exists: a class that only makes
        # sense on ONE version (26.x's GameTest registrar has no 1.21.1 counterpart, because 1.21.1 has
        # the @GameTest annotation) is deliberately overlay-only and would otherwise print this note on
        # every single build until nobody read it. Saying so IN THE FILE is the opt-out, so the reason
        # travels with the code rather than living in this script's exception list.
        head = open(os.path.join(a.overlay, rel), encoding='utf-8').read(4000)
        if 'OVERLAY-ONLY:' in head:
            continue
        print(f"  note: overlay-only file (shadows nothing in --src, and no sibling overlay "
              f"has it either -- stale path?): {rel}")

    if a.hits_out:
        with open(a.hits_out, 'w', encoding='utf-8') as fh:
            for k in sorted(hits):
                fh.write(k + '\n')
        print(f"  dead-rule check: deferred, {len(hits)} rule(s) matched here -> {a.hits_out}")
    else:
        seen = set(hits)
        if a.hits_in:
            # A missing hits file must be LOUD. Silently reporting the dead set of one root as
            # if it covered every root is the exact failure this flag exists to remove, and it
            # fails in the reassuring direction -- more "dead" rules, all of them wrong.
            if not os.path.exists(a.hits_in):
                sys.exit(f"--hits-in {a.hits_in} does not exist: the root that was supposed to "
                         f"collect hits never ran, so a dead-rule report from here would be "
                         f"measured against one source root instead of all of them.")
            seen |= set(open(a.hits_in, encoding='utf-8').read().split('\n')) - {''}

        checked = sum(1 for _, _, ex in token_rules if not ex) + \
                  sum(1 for _, _, ex in regex_rules if not ex)
        exempt = (len(token_rules) + len(regex_rules)) - checked
        dead = [f for f, _, ex in token_rules if not ex and f not in seen] + \
               [rx.pattern for rx, _, ex in regex_rules if not ex and rx.pattern not in seen]
        # Print the COVERAGE every run, not only the violations: "1 dead rule" reads identically
        # whether the check looked at 719 rules or at 20, and a collapsed scope is invisible
        # otherwise. And print the WHOLE dead list -- truncating it is what hid half of them
        # last time.
        print(f"  dead-rule check: checked={checked} exempt={exempt} dead={len(dead)}")
        for d in dead:
            print(f"    DEAD RULE (matched nothing in any source root): {d}")

    print(f"prepare-sources: shared={shared} overlayReplaced={replaced} dropped={dropped} "
          f"overlayOnly={len(overlay_rel - src_rel)} renameRewrites={rewrites} -> {a.out}")

    if a.gametest_adapter:
        import gametest_adapter
        pkg = a.gametest_pkg or f"com.{a.gametest_adapter}.compat"
        ts = gametest_adapter.adapt(a.out, a.gametest_adapter,
                                    pkg, a.gametest_structure)
        print(f"  gametest-adapter: wired {len(ts)} tests "
              f"({sum(1 for t in ts if not t['static'])} non-static)")


if __name__ == '__main__':
    main()
