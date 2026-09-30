#!/usr/bin/env python3
"""Generate the per-colour -> ColorCollection rename rows for a Minecraft version jump.

    # from your two builds' compile classpaths (the normal case; same inputs as build-class-move-map.py)
    python3 tools/gen-color-renames.py --from-cp old/cp.txt --to-cp new/cp.txt --out colors.tsv
    # or straight from vanilla: an obfuscated version's Mojang mappings + an unobfuscated jar
    python3 tools/gen-color-renames.py --from-mappings 1.21.1-client.txt --to-jar 26.2-client.jar

WHY THIS EXISTS
    MC 26.x collapsed the 16 per-colour constants of each dyed family into one `ColorCollection<T>`
    record: `Blocks.WHITE_CONCRETE` became `Blocks.CONCRETE.white()` (catalogue section V11). For a
    mod that builds things, that is several hundred rename rows. They are entirely mechanical, and
    they are derived from the game's own field names, so like the class-move map they are GENERATED
    on the user's machine rather than published.

THE RULE (read from the two versions, nothing hard-coded but the holder classes)
    For each holder (Blocks, Items): every OLD constant `<COLOUR>_<REST>` that no longer exists in
    the NEW holder, where the new holder has a ColorCollection field named `<REST>` or `DYED_<REST>`,
    becomes  `<Holder>.<COLOUR>_<REST>  ->  <Holder>.<FIELD>.<component>()`. The colours and their
    accessor names come from ColorCollection's own record components (white, lightBlue, ...).
    A constant whose rest matches BOTH a plain and a DYED_ field is refused, never guessed.

Standard library only. Exit 0 on success, 2 when an input cannot be read.
"""
import argparse, pathlib, re, struct, sys, zipfile

HOLDERS = ["net/minecraft/world/level/block/Blocks", "net/minecraft/world/item/Items"]
COLLECTION = "net/minecraft/world/level/block/ColorCollection"


# ── class-file reading (fields only) ────────────────────────────────────────────────────────
def class_fields(data):
    """-> list of (access_flags, name, descriptor) for a class file's fields."""
    pos = 8
    (cp_count,) = struct.unpack_from(">H", data, pos); pos += 2
    utf8 = {}
    i = 1
    while i < cp_count:
        tag = data[pos]; pos += 1
        if tag == 1:
            (ln,) = struct.unpack_from(">H", data, pos); pos += 2
            utf8[i] = data[pos:pos + ln].decode("utf-8", "replace"); pos += ln
        elif tag in (7, 8, 16, 19, 20):
            pos += 2
        elif tag == 15:
            pos += 3
        elif tag in (3, 4, 9, 10, 11, 12, 17, 18):
            pos += 4
        elif tag in (5, 6):
            pos += 8; i += 1            # long/double take two slots
        else:
            raise ValueError(f"unknown constant-pool tag {tag}")
        i += 1
    pos += 6                            # access, this, super
    (ifaces,) = struct.unpack_from(">H", data, pos); pos += 2 + 2 * ifaces
    (nfields,) = struct.unpack_from(">H", data, pos); pos += 2
    out = []
    for _ in range(nfields):
        acc, ni, di, na = struct.unpack_from(">HHHH", data, pos); pos += 8
        for _ in range(na):
            (_, alen) = struct.unpack_from(">HI", data, pos); pos += 6 + alen
        out.append((acc, utf8[ni], utf8[di]))
    return out


def jars_from_cp(cp_file):
    return [pathlib.Path(l.strip()) for l in pathlib.Path(cp_file).read_text().splitlines()
            if l.strip().endswith(".jar")]


def read_class(jars, internal):
    for j in jars:
        try:
            with zipfile.ZipFile(j) as z:
                return z.read(internal + ".class")
        except KeyError:
            continue
    return None


def facts_from_jars(jars):
    """-> {holder_simple: {field: type_internal_name}}, [colour components]"""
    holders = {}
    for h in HOLDERS:
        data = read_class(jars, h)
        if data is None:
            raise SystemExit(f"gen-color-renames: {h}.class is on none of the {len(jars)} jar(s)")
        holders[h.rsplit("/", 1)[1]] = {n: d[1:-1] if d.startswith("L") else d
                                        for acc, n, d in class_fields(data) if acc & 0x0008}
    cc = read_class(jars, COLLECTION)
    comps = [n for acc, n, d in class_fields(cc) if not acc & 0x0008] if cc else []
    return holders, comps


def facts_from_mappings(path):
    """Mojang's ProGuard-format mappings list each field with its type, which is all we need."""
    holders, current = {}, None
    want = {h.replace("/", "."): h.rsplit("/", 1)[1] for h in HOLDERS}
    for line in pathlib.Path(path).read_text().splitlines():
        if line.lstrip().startswith("#"):           # `# {"fileName": ...}` metadata, not a class
            continue
        if not line.startswith(" "):
            current = want.get(line.split(" -> ")[0])
            if current:
                holders[current] = {}
            continue
        if current and "(" not in line:
            m = re.match(r'\s+(\S+) (\S+) -> ', line)
            if m:
                holders[current][m.group(2)] = m.group(1).replace(".", "/")
    return holders, []


# ── the rule ────────────────────────────────────────────────────────────────────────────────
def snake(camel):
    return re.sub(r'([a-z])([A-Z])', r'\1_\2', camel).upper()


def generate(old, new, comps):
    rows, refused = [], []
    colours = sorted(((snake(c), c) for c in comps), key=lambda x: -len(x[0]))
    for holder, new_fields in new.items():
        coll = {f for f, t in new_fields.items() if t == COLLECTION}
        for const in sorted(old.get(holder, {})):
            if const in new_fields:
                continue
            for upper, accessor in colours:          # longest colour first: LIGHT_BLUE before BLUE
                if const.startswith(upper + "_"):
                    rest = const[len(upper) + 1:]
                    hits = [f for f in (rest, "DYED_" + rest) if f in coll]
                    if len(hits) == 1:
                        rows.append((f"{holder}.{const}", f"{holder}.{hits[0]}.{accessor}()"))
                    elif len(hits) > 1:
                        refused.append(f"{holder}.{const} matches both {hits}")
                    break
    return rows, refused


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g1 = ap.add_mutually_exclusive_group(required=True)
    g1.add_argument("--from-cp"); g1.add_argument("--from-jar"); g1.add_argument("--from-mappings")
    g2 = ap.add_mutually_exclusive_group(required=True)
    g2.add_argument("--to-cp"); g2.add_argument("--to-jar")
    ap.add_argument("--out")
    a = ap.parse_args()
    try:
        if a.from_mappings:
            old, _ = facts_from_mappings(a.from_mappings)
        else:
            old, _ = facts_from_jars(jars_from_cp(a.from_cp) if a.from_cp else [pathlib.Path(a.from_jar)])
        new, comps = facts_from_jars(jars_from_cp(a.to_cp) if a.to_cp else [pathlib.Path(a.to_jar)])
    except (OSError, ValueError, zipfile.BadZipFile) as e:
        print(f"gen-color-renames: cannot read inputs: {e}", file=sys.stderr)
        return 2
    if not comps:
        print("gen-color-renames: the NEW side has no ColorCollection -- nothing to generate "
              "(this jump did not collapse the colour constants).")
        rows, refused = [], []
    else:
        rows, refused = generate(old, new, comps)
    text = "".join(f"{f}\t{t}\n" for f, t in rows)
    if a.out:
        pathlib.Path(a.out).write_text(text)
    else:
        sys.stdout.write(text)
    print(f"gen-color-renames: {len(rows)} row(s), {len(comps)} colours", file=sys.stderr)
    for r in refused:
        print(f"  REFUSED (ambiguous): {r}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
