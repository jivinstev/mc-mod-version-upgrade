#!/usr/bin/env python3
"""Derive each target's mixin config from the SHARED one minus that target's drops.

A dropped file is not compiled (versions/<t>.drops.txt, honoured by prepare-sources.py),
so a mixin still named in the config is a class that does not exist -- and Mixin does not
shrug at that. It is a HARD failure at mixin apply, i.e. the client dies at start on the
one target whose gates you are least likely to have run (X15).

The obvious fix is to hand-write src/<overlay>/resources/<ns>.mixins.json. That is a
second copy of a 60-entry list which must be edited every time a mixin is added, dropped
or renamed -- and nothing would ever say it had drifted. So it is DERIVED instead
(S2: when a lesson lands, wire it into something that runs), and --check is a `check`
task on every target.

Only drops inside the config's own `package` are mixins; the rest of a drops file
(a renderer, a helper class) is ignored here.

  python3 tools/gen-mixin-config.py --ns=<modid>            # write every target's config
  python3 tools/gen-mixin-config.py --ns=<modid> --check    # fail on drift
"""
import argparse, json, os, re, sys

SHARED_DIR = 'src/main/resources'


def shared_configs(ns):
    """Every shared mixin config for this namespace, not just `<ns>.mixins.json`.

    A mod with ONE config is the simple case and was all this handled. A multiloader mod ships
    two -- `<ns>.mixins.json` for the loader-specific mixins and `<ns>-common.mixins.json` for
    the portable ones -- which is the standard shape, not an exotic one. Reading only the first
    checked an EMPTY config and printed "all consistent" over the one that holds every mixin
    the mod has (X27's invariant again: assert the scope, not only the findings). a small shared-API library's
    loader config declares zero mixins and its -common config declares eight.
    """
    out = sorted(f for f in os.listdir(SHARED_DIR)
                 if f.startswith(ns) and f.endswith('.mixins.json'))
    return [os.path.join(SHARED_DIR, f) for f in out]


def overlay_of(target):
    """The overlay directory this target's build.gradle would use."""
    path = 'versions/%s.properties' % target
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if line.startswith('overlay') and '=' in line:
                return line.split('=', 1)[1].strip()
    sys.exit('%s: no `overlay` key -- cannot place this target\'s mixin config' % path)


def dropped_mixins(target, pkg):
    """Simple mixin names this target drops, derived from its drops list."""
    path = 'versions/%s.drops.txt' % target
    if not os.path.isfile(path):
        return []
    prefix = pkg + '.'
    out = []
    for line in open(path, encoding='utf-8'):
        line = line.split('#', 1)[0].strip()
        if not line or not line.endswith('.java'):
            continue
        fq = line[:-len('.java')].replace('/', '.')
        if fq.startswith(prefix):
            out.append(fq[len(prefix):])
    return out


def derive(shared_text, drops):
    cfg = json.loads(shared_text)
    removed = []
    for key in ('mixins', 'client', 'server'):
        if key not in cfg:
            continue
        kept, seen = [], set()
        for name in cfg[key]:
            if name in drops:
                removed.append(name)
                continue
            # A duplicate entry is harmless and invisible; carrying it into a GENERATED
            # file makes it look deliberate. Drop it here and fix the shared one too.
            if name in seen:
                continue
            seen.add(name)
            kept.append(name)
        cfg[key] = kept
    return cfg, removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ns', required=True, help='mod id / resource namespace')
    ap.add_argument('--check', action='store_true')
    a = ap.parse_args()

    configs = shared_configs(a.ns)
    if not configs:
        sys.exit('%s: no %s*.mixins.json -- run from the mod directory' % (SHARED_DIR, a.ns))

    targets = sorted(f[:-len('.properties')] for f in os.listdir('versions')
                     if f.endswith('.properties'))
    if not targets:
        sys.exit('versions/: no target properties files')

    bad, notes, checked = [], [], 0
    for shared_path in configs:
        name = os.path.basename(shared_path)
        shared_text = open(shared_path, encoding='utf-8').read()
        pkg = json.loads(shared_text).get('package')
        if not pkg:
            sys.exit('%s: no `package` -- cannot tell which drops are mixins' % shared_path)

        for target in targets:
            checked += 1
            drops = dropped_mixins(target, pkg)
            out_dir = os.path.join('src', overlay_of(target), 'resources')
            out_path = os.path.join(out_dir, name)

            if not drops:
                # No drops means the shared config is already right for this target, and an
                # overlay copy would then SHADOW it silently -- a stale second list that a
                # later edit to the shared one would never reach.
                if os.path.isfile(out_path):
                    bad.append('%s: this target drops no mixins from %s, so this overlay copy '
                               'only shadows the shared config -- delete it' % (out_path, name))
                notes.append('%-8s %-32s no mixin drops (uses the shared config)' % (target, name))
                continue

            cfg, removed = derive(shared_text, drops)
            text = json.dumps(cfg, indent=2) + '\n'
            notes.append('%-8s %-32s %d dropped: %s'
                         % (target, name, len(removed), ', '.join(sorted(removed))))

            if a.check:
                if not os.path.isfile(out_path):
                    bad.append('%s: missing -- this target drops %s, and the shared config '
                               'still names them (Mixin fails at APPLY, not at load)'
                               % (out_path, ', '.join(sorted(removed))))
                elif open(out_path, encoding='utf-8').read() != text:
                    bad.append('%s: out of date -- regenerate with '
                               'python3 tools/gen-mixin-config.py --ns=%s' % (out_path, a.ns))
            else:
                os.makedirs(out_dir, exist_ok=True)
                with open(out_path, 'w', encoding='utf-8') as fh:
                    fh.write(text)
                print('wrote %s' % out_path)

    for n in notes:
        print('  ' + n)
    if bad:
        print('\ngen-mixin-config: %d problem(s)' % len(bad), file=sys.stderr)
        for b in bad:
            print('  ' + b, file=sys.stderr)
        sys.exit(1)
    print('gen-mixin-config: %d config/target pair(s) checked over %d config(s), all consistent'
          % (checked, len(configs)))


if __name__ == '__main__':
    main()
