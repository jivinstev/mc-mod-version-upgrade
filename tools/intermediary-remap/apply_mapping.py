#!/usr/bin/env python3
"""Rewrite intermediary names (class_N / method_N / field_N) -> Mojang official
across a decompiled Fabric mod's source tree.

Usage: python3 apply_mapping.py <intermediary2official-<mc>.json> <src_dir>

Three passes, in order (order matters):
  1. fully-qualified `net.minecraft.class_A[.class_B...]` -> the official FQ dotted
     name (handles imports AND inner classes, which Vineflower writes dotted).
  2. bare `class_N` -> the official SIMPLE name (last `$` segment).
  3. `method_N` / `field_N` -> the official member name.

Intermediary ids are globally unique, so flat text replacement is safe.
Unmapped ids are left alone and surface as ordinary `cannot find symbol` errors.
"""
import json, re, sys, os


def main():
    if len(sys.argv) != 3:
        print(__doc__); sys.exit(2)
    mapping = json.load(open(sys.argv[1], encoding="utf-8"))
    root = sys.argv[2]

    classes = mapping["classes"]          # 'class_A$class_B' -> 'net/minecraft/x/Outer$Inner'
    members = mapping["members"]           # 'method_N'/'field_N' -> 'name'

    fq = {k: v.replace('/', '.').replace('$', '.') for k, v in classes.items()}
    simple = {}
    for k, v in classes.items():
        simple[k.split('$')[-1]] = v.split('/')[-1].split('$')[-1]

    fq_re = re.compile(r'\bnet\.minecraft\.(class_\d+(?:\.class_\d+)*)\b')
    bare_re = re.compile(r'\bclass_\d+\b')
    mem_re = re.compile(r'\b(?:method|field)_\d+\b')

    def sub_fq(m):
        key = m.group(1).replace('.', '$')
        return fq.get(key, m.group(0))

    n_files = n_fq = n_bare = n_mem = 0
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if not fn.endswith('.java'):
                continue
            p = os.path.join(dirpath, fn)
            src = open(p, encoding='utf-8').read()
            orig = src
            src, c1 = fq_re.subn(sub_fq, src)
            src, c2 = bare_re.subn(lambda m: simple.get(m.group(0), m.group(0)), src)
            src, c3 = mem_re.subn(lambda m: members.get(m.group(0), m.group(0)), src)
            if src != orig:
                open(p, 'w', encoding='utf-8').write(src)
                n_files += 1; n_fq += c1; n_bare += c2; n_mem += c3
    print(f"rewrote {n_files} files: fq={n_fq} bare={n_bare} members={n_mem}")


if __name__ == '__main__':
    main()
