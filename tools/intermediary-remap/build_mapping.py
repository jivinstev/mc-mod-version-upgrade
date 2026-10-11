#!/usr/bin/env python3
"""Build a global INTERMEDIARY -> Mojang-official dictionary for a Minecraft version.

WHY THIS EXISTS: a compiled **Fabric** mod jar is remapped to the *intermediary*
namespace (`MANIFEST.MF: Fabric-Mapping-Namespace: intermediary`). Every Minecraft
reference in it looks like `net.minecraft.class_1799`, `method_7909`, `field_8125`.
Vineflower decompiles that faithfully, so the raw `.java` will NOT compile against
NeoForge (which uses Mojang official names). This is the Fabric-side twin of
`tools/srg-remap` — same idea, different source namespace.

Intermediary ids are GLOBALLY UNIQUE (class_N / method_N / field_N), so a flat
text replacement over the decompiled sources is safe and complete.

We join two official mapping sources on the OBFUSCATED names:
  * Mojang official mappings (official <-> obf)  — from the version manifest
  * Fabric `intermediary` tiny v2 (obf <-> intermediary) — from maven.fabricmc.net

Output JSON:
  {"classes": {"class_1799": "net/minecraft/world/item/ItemStack", ...},
   "members": {"method_7909": "getItem", "field_8125": "count", ...}}

Usage:  python3 build_mapping.py <mc_version> <out.json>
"""
import json, re, sys, os, urllib.request, zipfile, tempfile


def fetch(url):
    with urllib.request.urlopen(url) as r:
        return r.read()


def v1_to_v2(v1):
    """tiny v1 (`CLASS obf inter`, `METHOD owner desc obf inter`, `FIELD owner desc obf inter`) -> the tiny v2 shape
    this builder walks (`c obf inter`, then `\tm desc obf inter` / `\tf desc obf inter` under their class)."""
    cls, mem = [], {}
    for ln in v1.splitlines():
        p = ln.split('\t')
        if p[0] == 'CLASS' and len(p) >= 3:
            cls.append((p[1], p[2]))
        elif p[0] in ('METHOD', 'FIELD') and len(p) >= 5:
            mem.setdefault(p[1], []).append(('m' if p[0] == 'METHOD' else 'f', p[2], p[3], p[4]))
    out = ['tiny\t2\t0\tofficial\tintermediary']
    for obf, inter in cls:
        out.append(f"c\t{obf}\t{inter}")
        out += [f"\t{k}\t{d}\t{o}\t{i}" for k, d, o, i in mem.get(obf, [])]
    return '\n'.join(out) + '\n'


def main():
    if len(sys.argv) != 3:
        print(__doc__); sys.exit(2)
    mc, out = sys.argv[1], sys.argv[2]
    tmp = tempfile.mkdtemp(prefix=f"intmap-{mc}-")

    # 1) Mojang official client mappings (official <-> obf)
    manifest = json.loads(fetch("https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"))
    vurl = next(v['url'] for v in manifest['versions'] if v['id'] == mc)
    vjson = json.loads(fetch(vurl))
    moj = fetch(vjson['downloads']['client_mappings']['url']).decode()

    # 2) Fabric intermediary tiny v2 (obf <-> intermediary)
    try:
        z = os.path.join(tmp, "intermediary.jar")
        open(z, 'wb').write(fetch(
            f"https://maven.fabricmc.net/net/fabricmc/intermediary/{mc}/intermediary-{mc}-v2.jar"))
        with zipfile.ZipFile(z) as zf:
            tiny = zf.read("mappings/mappings.tiny").decode()
    except Exception as e:          # maven.fabricmc.net is not on every egress allowlist; FabricMC's own repository
        print(f"[{mc}] maven.fabricmc.net unreachable ({e}); using github.com/FabricMC/intermediary (tiny v1)")
        tiny = v1_to_v2(fetch(f"https://raw.githubusercontent.com/FabricMC/intermediary/master/mappings/{mc}.tiny").decode())

    PRIM = {'void': 'V', 'boolean': 'Z', 'byte': 'B', 'char': 'C', 'short': 'S',
            'int': 'I', 'long': 'J', 'float': 'F', 'double': 'D'}

    # official (dotted, $ for inner) -> obf (slashed)
    cls_off2obf = {}
    for ln in moj.splitlines():
        if ln and not ln.startswith(' ') and '->' in ln and not ln.startswith('#'):
            off, obf = ln.split(' -> ')
            cls_off2obf[off.strip()] = obf.strip().rstrip(':').replace('.', '/')   # an UNobfuscated class keeps its dotted name (X77)
    cls_obf2off = {v: k for k, v in cls_off2obf.items()}

    def to_obf_desc(t):
        t = t.strip(); arr = 0
        while t.endswith('[]'):
            arr += 1; t = t[:-2]
        if t in PRIM:
            d = PRIM[t]
        else:
            obf = cls_off2obf.get(t)
            d = 'L' + (obf if obf is not None else t.replace('.', '/')) + ';'
        return '[' * arr + d

    # (obfClass, obfMember, obfDesc) -> official member name
    methods_by_obf, fields_by_obf = {}, {}
    cur = None
    # a method Mojang's compiler inlined carries its origin lines AFTER the parameters too: `foo():12:34 -> a`.
    # Without the optional tail every such method was dropped -- all of MinecraftServer's, among others (X77).
    mre = re.compile(r'^\s+(?:\d+:\d+:)?([\w.$\[\]]+)\s+([\w$<>]+)\(([^)]*)\)(?::\d+:\d+)?\s+->\s+(\S+)$')
    fre = re.compile(r'^\s+([\w.$\[\]]+)\s+([\w$]+)\s+->\s+(\S+)$')
    for ln in moj.splitlines():
        if not ln or ln.startswith('#'):
            continue
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

    # Walk the tiny v2 file. Header: `tiny 2 0 official intermediary`
    classes, members = {}, {}
    cur_obf = None
    unresolved_m = unresolved_f = 0
    for ln in tiny.splitlines():
        if ln.startswith('tiny\t') or not ln:
            continue
        if ln.startswith('c\t'):
            _, obf, inter = ln.split('\t')[:3]
            cur_obf = obf
            off = cls_obf2off.get(obf)
            if off:
                # intermediary class ids are the LAST path segment (net/minecraft/class_N),
                # and inner classes appear as class_A$class_B — key on the whole path tail.
                key = inter.split('/')[-1]
                classes[key] = off.replace('.', '/')
            continue
        if not ln.startswith('\t'):
            continue
        p = ln.split('\t')
        # ['', 'm', desc, obfName, interName]  /  ['', 'f', desc, obfName, interName]
        if len(p) >= 5 and p[1] == 'm':
            off = methods_by_obf.get((cur_obf, p[3], p[2]))
            if off:
                members[p[4]] = off
            else:
                unresolved_m += 1
        elif len(p) >= 5 and p[1] == 'f':
            off = fields_by_obf.get((cur_obf, p[3]))
            if off:
                members[p[4]] = off
            else:
                unresolved_f += 1

    json.dump({"__schema__": 2, "classes": classes, "members": members}, open(out, 'w', encoding="utf-8"))
    print(f"[{mc}] intermediary->official: classes {len(classes)}, members {len(members)} "
          f"(unresolved m={unresolved_m} f={unresolved_f})")
    for s in ['class_1799', 'class_1887']:
        print(f"  {s} -> {classes.get(s)}")
    for s in ['method_7909', 'method_8863']:
        print(f"  {s} -> {members.get(s)}")


if __name__ == '__main__':
    main()
