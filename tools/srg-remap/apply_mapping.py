#!/usr/bin/env python3
"""Apply an SRG->official dict (from build_mapping.py) across a decompiled source
tree, rewriting `m_NNNN_` / `f_NNNN_` member names to their official names.
Unmapped ids (a handful of Forge-injected ones) are left untouched.

Usage: python3 apply_mapping.py <srg2official.json> <src_dir>
"""
import json, re, os, sys

def main():
    if len(sys.argv) != 3:
        print(__doc__); sys.exit(2)
    srg = json.load(open(sys.argv[1]))
    root = sys.argv[2]
    pat = re.compile(r'\b([mf]_\d+_)\b')
    files = hits = 0
    miss = set()
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            if not f.endswith('.java'): continue
            p = os.path.join(dp, f)
            t = open(p).read()
            def rep(m):
                nonlocal hits
                v = srg.get(m.group(1))
                if v is None:
                    miss.add(m.group(1)); return m.group(1)
                hits += 1; return v
            nt = pat.sub(rep, t)
            if nt != t:
                open(p, 'w').write(nt); files += 1
    print(f"rewrote {files} files, {hits} member renames applied")
    print(f"unmapped SRG ids left as-is: {len(miss)} distinct: {sorted(miss)[:20]}")

if __name__ == '__main__':
    main()
