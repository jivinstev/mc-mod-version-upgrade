#!/usr/bin/env python3
"""Move code-registered painting variants to the datapack registry 1.21 made them (CATALOG X159).

    python3 tools/convert-paintings.py --src src/main/java --modid <modid> [--dry-run] [--self-check]

In 1.20 a painting was registered in code: `PAINTINGS.register("name", () -> new PaintingVariant(32, 16))`, sizes in
PIXELS. 1.21 made PAINTING_VARIANT a datapack registry: there is nothing to register in code (a DeferredRegister on it
never fires), the record takes an asset id, and the sizes are in BLOCKS. So each such field becomes

    data/<modid>/painting_variant/<name>.json   {"asset_id": "<modid>:<name>", "width": w/16, "height": h/16}

plus an entry in `#minecraft:placeable` (data/minecraft/tags/painting_variant/placeable.json), which is what lets a
player place it -- 1.20 placed every registered variant. The texture stays where 1.20 had it
(assets/<modid>/textures/painting/<name>.png). The Java field is deleted; a field still read elsewhere is LISTED and
kept, since only the author knows what it was for. Standard library only.
"""
import argparse, json, pathlib, re, sys

FIELD = re.compile(r"(?m)^[ \t]*(?:(?:public|private|protected|static|final)\s+)*(?:Supplier|RegistryObject|DeferredHolder)"
                   r"<[^;=]*?PaintingVariant\s*>\s+(\w+)\s*=\s*\w+\.register\(\s*\"([a-z0-9_/.-]+)\"\s*,\s*\(\)\s*->\s*"
                   r"new\s+PaintingVariant\(\s*(\d+)\s*,\s*(\d+)\s*\)\s*\)\s*;[ \t]*\n")


def run(src, modid, dry=False):
    src = pathlib.Path(src)
    res = src.parent / "resources"
    report, flags, changed = {"paintings": 0}, [], 0
    files = {f: f.read_text(encoding="utf-8", errors="replace") for f in sorted(src.rglob("*.java"))}
    found = []
    for f, t in files.items():
        for m in FIELD.finditer(t):
            found.append((f, m))
    if not found:
        return report, flags, 0
    names = []
    for f, m in found:
        field, name, w, h = m.group(1), m.group(2), int(m.group(3)), int(m.group(4))
        used = [g for g, t in files.items() if re.search(r"(?<![\w$])%s(?![\w$])" % re.escape(field), t if g != f else t.replace(m.group(0), ""))]
        data = {"asset_id": f"{modid}:{name}", "width": max(1, round(w / 16)), "height": max(1, round(h / 16))}
        if not dry:
            p = res / "data" / modid / "painting_variant" / f"{name}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        names.append(f"{modid}:{name}")
        report["paintings"] += 1
        if used:
            flags.append(f"{f.name}: {field} is still read in {', '.join(sorted(g.name for g in used))}; field kept")
        else:
            files[f] = files[f].replace(m.group(0), "", 1)
    tag = res / "data/minecraft/tags/painting_variant/placeable.json"
    if not dry:
        cur = json.loads(tag.read_text(encoding="utf-8")) if tag.exists() else {"replace": False, "values": []}
        cur["values"] += [n for n in names if n not in cur["values"]]
        tag.parent.mkdir(parents=True, exist_ok=True)
        tag.write_text(json.dumps(cur, indent=2) + "\n", encoding="utf-8")
        for f, t in files.items():
            if t != f.read_text(encoding="utf-8", errors="replace"):
                f.write_text(t, encoding="utf-8")
                changed += 1
    return report, flags, changed


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src"); ap.add_argument("--modid"); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not (a.src and a.modid):
        ap.error("--src and --modid are required")
    report, flags, changed = run(a.src, a.modid, a.dry_run)
    print(f"convert-paintings: {changed} file(s) changed, {report['paintings']} painting(s) moved to data")
    for fl in flags:
        print("  left: " + fl)
    return 0


def self_check():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        j = pathlib.Path(d) / "java/m"; j.mkdir(parents=True)
        (j / "Paintings.java").write_text(
            "package m;\npublic class Paintings {\n"
            "   public static final DeferredRegister<PaintingVariant> REGISTRY = DeferredRegister.create(Registries.PAINTING_VARIANT, \"m\");\n"
            "   public static final Supplier<PaintingVariant> ALBUM = REGISTRY.register(\"album\", () -> new PaintingVariant(32, 16));\n"
            "   public static final RegistryObject<PaintingVariant> SHIP = REGISTRY.register(\"flying_ship\", () -> new PaintingVariant(32, 48));\n"
            "}\n", encoding="utf-8")
        (j / "Use.java").write_text("package m;\nclass Use { Object o = Paintings.SHIP; }\n", encoding="utf-8")
        report, flags, changed = run(pathlib.Path(d) / "java", "m")
        p = (j / "Paintings.java").read_text(encoding="utf-8")
        res = pathlib.Path(d) / "resources/data"
        album = json.loads((res / "m/painting_variant/album.json").read_text(encoding="utf-8"))
        ship = json.loads((res / "m/painting_variant/flying_ship.json").read_text(encoding="utf-8"))
        tag = json.loads((res / "minecraft/tags/painting_variant/placeable.json").read_text(encoding="utf-8"))
        ok = (album == {"asset_id": "m:album", "width": 2, "height": 1} and ship["height"] == 3 and "ALBUM" not in p
              and "SHIP" in p and len(flags) == 1 and tag == {"replace": False, "values": ["m:album", "m:flying_ship"]})
        run(pathlib.Path(d) / "java", "m")
        ok &= json.loads((res / "minecraft/tags/painting_variant/placeable.json").read_text(encoding="utf-8")) == tag
        if not ok:
            print(p, album, ship, tag, flags)
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
