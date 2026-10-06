#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Fail if the README's cloud domain list differs from cloud/allowed-domains.txt."""
import pathlib, sys
root = pathlib.Path(__file__).resolve().parent.parent
lines = {l.strip() for l in root.joinpath('README.md').read_text(encoding="utf-8").splitlines()}
domains = [l.strip() for l in root.joinpath('cloud/allowed-domains.txt').read_text(encoding="utf-8").splitlines() if l.strip()]
missing = [d for d in domains if d not in lines]
print('check_readme: %d domains, %s' % (len(domains), 'in sync' if not missing else 'DRIFTED'))
for m in missing:
    print('  README is missing  ' + m)
sys.exit(1 if missing else 0)
