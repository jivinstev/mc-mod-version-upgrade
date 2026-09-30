#!/usr/bin/env python3
"""
Generate an empty floored GameTest structure NBT for a NeoForge mod.

NeoForge 21.1 has no @EmptyTemplate, and the GameTest framework throws
"Missing test structure" without a real .nbt on disk. This writes a gzipped
StructureTemplate NBT (a solid floor layer + air above) that @GameTest methods
can reference.

Usage:
  python3 tools/gen-empty-structure.py <modid> [name] [size] [dataversion]

  <modid>        e.g. examplemod   -> data/<modid>/structure/<name>.nbt
  [name]         template name, default "empty_test"
  [size]         cube edge in blocks, default 9  (roomy for large mobs)
  [dataversion]  MC world DataVersion, default 3955 (1.21.1)

Output path:
  mods/<modid>/src/main/resources/data/<modid>/structure/<name>.nbt

Reference @GameTest with template="<name>", @GameTestHolder(<modid>),
@PrefixGameTestTemplate(false)  -> resolves to <modid>:<name>.
"""
import struct, gzip, os, sys

def name(s):
    b = s.encode('utf-8'); return struct.pack('>H', len(b)) + b
def i32(v): return struct.pack('>i', v)
def comp_entry(tid, nm, payload): return bytes([tid]) + name(nm) + payload
def list_payload(elem_id, elems): return bytes([elem_id]) + i32(len(elems)) + b''.join(elems)
def str_payload(s):
    b = s.encode('utf-8'); return struct.pack('>H', len(b)) + b

def build(modid, tname, size, dataversion):
    palette_elem = comp_entry(8, "Name", str_payload("minecraft:polished_andesite")) + bytes([0])
    palette = comp_entry(9, "palette", list_payload(10, [palette_elem]))
    block_elems = []
    for x in range(size):
        for z in range(size):
            pos_list = comp_entry(9, "pos", list_payload(3, [i32(x), i32(0), i32(z)]))
            state = comp_entry(3, "state", i32(0))
            block_elems.append(pos_list + state + bytes([0]))
    blocks = comp_entry(9, "blocks", list_payload(10, block_elems))
    size_t = comp_entry(9, "size", list_payload(3, [i32(size), i32(size), i32(size)]))
    entities = comp_entry(9, "entities", list_payload(0, []))       # empty END list
    dv = comp_entry(3, "DataVersion", i32(dataversion))
    root_payload = size_t + entities + blocks + palette + dv + bytes([0])
    return bytes([10]) + name("") + root_payload

def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    modid = sys.argv[1]
    tname = sys.argv[2] if len(sys.argv) > 2 else "empty_test"
    size  = int(sys.argv[3]) if len(sys.argv) > 3 else 9
    dv    = int(sys.argv[4]) if len(sys.argv) > 4 else 3955
    repo  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out   = os.path.join(repo, "mods", modid, "src", "main", "resources",
                         "data", modid, "structure", tname + ".nbt")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with gzip.open(out, 'wb') as f:
        f.write(build(modid, tname, size, dv))
    print("wrote", out, "(%d bytes gzipped)" % os.path.getsize(out))
    print("reference: @GameTest(template = \"%s\")  with  @GameTestHolder(\"%s\") + @PrefixGameTestTemplate(false)" % (tname, modid))

if __name__ == "__main__":
    main()
