#!/usr/bin/env python3
"""Build a global SRG -> Mojang-official name dictionary for a Minecraft version.

WHY THIS EXISTS: a compiled Forge 1.20.x mod jar uses *official class names* but
*SRG member names* (methods/fields look like `m_20615_`, `f_19853_`). Vineflower
decompiles it faithfully, so the raw source is full of SRG names and will NOT
compile against NeoForge (which uses official names like `create`, `level`). SRG
member ids are GLOBALLY UNIQUE, so a flat {srg: official} dict applied as a text
replacement over the decompiled sources is safe and complete.

We derive the dict by joining two official mapping sources on the obfuscated names:
  * Mojang official mappings  (official  <-> obf)  — from the version manifest
  * MCPConfig `joined.tsrg`    (obf       <-> srg)  — from the NeoForged maven

Usage:  python3 build_mapping.py <mc_version> <out.json>
Example: python3 build_mapping.py 1.20.1 srg2official-1.20.1.json
"""
import json, re, sys, os, urllib.request, zipfile, tempfile

def fetch(url):
    with urllib.request.urlopen(url) as r:
        return r.read()

def main():
    if len(sys.argv) != 3:
        print(__doc__); sys.exit(2)
    mc, out = sys.argv[1], sys.argv[2]
    tmp = tempfile.mkdtemp(prefix=f"srgmap-{mc}-")

    # 1) Mojang official client mappings for this version
    manifest = json.loads(fetch("https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"))
    vurl = next(v['url'] for v in manifest['versions'] if v['id'] == mc)
    vjson = json.loads(fetch(vurl))
    moj = fetch(vjson['downloads']['client_mappings']['url']).decode()

    # 2) MCPConfig joined.tsrg (obf<->srg)
    z = os.path.join(tmp, "mcp.zip")
    open(z, 'wb').write(fetch(f"https://maven.neoforged.net/releases/de/oceanlabs/mcp/mcp_config/{mc}/mcp_config-{mc}.zip"))
    with zipfile.ZipFile(z) as zf:
        tsrg = zf.read("config/joined.tsrg").decode()

    PRIM = {'void':'V','boolean':'Z','byte':'B','char':'C','short':'S',
            'int':'I','long':'J','float':'F','double':'D'}
    cls_off2obf = {}
    for ln in moj.splitlines():
        if ln and not ln.startswith(' ') and '->' in ln:
            off, obf = ln.split(' -> ')
            cls_off2obf[off.strip()] = obf.strip().rstrip(':').replace('.', '/')   # an UNobfuscated class keeps its dotted name (X77)

    def to_obf_desc(t):
        t = t.strip(); arr = 0
        while t.endswith('[]'):
            arr += 1; t = t[:-2]
        if t in PRIM: d = PRIM[t]
        else:
            obf = cls_off2obf.get(t)
            d = 'L' + (obf if obf is not None else t.replace('.', '/')) + ';'
        return '[' * arr + d

    methods_by_obf, fields_by_obf = {}, {}
    cur = None
    # a method Mojang's compiler inlined carries its origin lines AFTER the parameters too: `foo():12:34 -> a`.
    # Without the optional tail every such method was dropped -- all of MinecraftServer's, among others (X77).
    mre = re.compile(r'^\s+(?:\d+:\d+:)?([\w.$\[\]]+)\s+([\w$<>]+)\(([^)]*)\)(?::\d+:\d+)?\s+->\s+(\S+)$')
    fre = re.compile(r'^\s+([\w.$\[\]]+)\s+([\w$]+)\s+->\s+(\S+)$')
    for ln in moj.splitlines():
        if not ln or ln.startswith('#'): continue
        if not ln.startswith(' '):
            cur = ln.split(' -> ')[1].strip().rstrip(':').replace('.', '/'); continue
        m = mre.match(ln)
        if m:
            ret, name, params, obfn = m.groups()
            pts = [p for p in params.split(',') if p] if params.strip() else []
            desc = '(' + ''.join(to_obf_desc(p) for p in pts) + ')' + to_obf_desc(ret)
            methods_by_obf[(cur, obfn, desc)] = name
            continue
        f = fre.match(ln)
        if f:
            _t, name, obfn = f.groups()
            fields_by_obf[(cur, obfn)] = name

    srg2off = {}
    cur = None
    for ln in tsrg.splitlines():
        if ln.startswith('tsrg2'): continue
        if not ln.startswith('\t'):
            cur = ln.split()[0]; continue
        if ln.startswith('\t\t'): continue
        p = ln.strip().split()
        if len(p) == 3 and '(' not in p[1]:            # field: obf srg id
            off = fields_by_obf.get((cur, p[0]))
            if off: srg2off[p[1]] = off
        elif len(p) == 4:                               # method: obf desc srg id
            off = methods_by_obf.get((cur, p[0], p[1]))
            if off: srg2off[p[2]] = off

    srg2off["__schema__"] = 2      # tools/port.py rebuilds a cached map without it (X77: earlier builds dropped ~900 names)
    json.dump(srg2off, open(out, 'w', encoding="utf-8"))
    print(f"[{mc}] SRG->official entries: {len(srg2off)}  (classes {len(cls_off2obf)})")
    for s in ['m_91087_', 'f_19853_']:
        print(f"  {s} -> {srg2off.get(s)}")

if __name__ == '__main__':
    main()
