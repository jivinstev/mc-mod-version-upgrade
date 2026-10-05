#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Fail if the README's cloud domain list or setup script differ from cloud/."""
import pathlib, sys
root = pathlib.Path(__file__).resolve().parent.parent
readme = root.joinpath('README.md').read_text()
lines = [l.strip() for l in readme.splitlines()]
missing = []
for name in ('allowed-domains.txt', 'setup.sh'):
    text = root.joinpath('cloud', name).read_text()
    if name == 'setup.sh':
        text = text.split('set -euo pipefail\n', 1)[1]
    for want in (l.strip() for l in text.splitlines() if l.strip()):
        if want not in lines:
            missing.append('%s: %s' % (name, want))
domains = {l.strip() for l in root.joinpath('cloud/allowed-domains.txt').read_text().splitlines() if l.strip()}
print('check_readme: %d domains, setup script %s' % (len(domains), 'in sync' if not missing else 'DRIFTED'))
for m in missing:
    print('  README is missing  ' + m)
sys.exit(1 if missing else 0)
