#!/usr/bin/env python3
"""Draw the two greyscale spawn-egg layers this mod needs on MC 26.2+.

WHY THIS EXISTS (catalog V42c): 26.x deleted `models/item/template_spawn_egg.json`
AND both textures it used (`item/spawn_egg`, `item/spawn_egg_overlay`), and registers
no spawn-egg tint source. Vanilla stopped needing them -- each of its ~80 eggs is now a
hand-drawn sprite. A mod with 20 eggs cannot follow that, so it draws the two greyscale
layers once and tints them per egg.

The layers are GREYSCALE on purpose: each egg's client item definition supplies its own
two `minecraft:constant` tints (the same two ints the item constructor already takes), so
one pair of sprites serves all 20 eggs exactly as vanilla's pair used to.

DRAWN, NOT COPIED. Lifting Mojang's two PNGs out of the 1.21.1 jar would ship their art in
this mod's jar. An ellipse plus speckles is cheaper than that argument.

  python3 tools/gen-spawn-egg-art.py --ns <namespace>
  python3 tools/gen-spawn-egg-art.py --ns <namespace> --check
"""
import argparse, hashlib, pathlib, struct, sys, zlib

SIZE = 16


def png(pixels):
    """pixels: SIZE*SIZE list of (r,g,b,a). Returns PNG bytes."""
    raw = b"".join(
        b"\x00" + b"".join(struct.pack("BBBB", *pixels[y * SIZE + x]) for x in range(SIZE))
        for y in range(SIZE)
    )

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def inside(x, y):
    """The egg outline: an ellipse, narrower at the top like a real egg."""
    cx, cy = 7.5, 8.4
    dx = (x + 0.5 - cx) / 5.0
    dy = (y + 0.5 - cy) / 6.6
    # taper the top half so it reads as an egg rather than a pill
    if y + 0.5 < cy:
        dx *= 1.0 + 0.30 * ((cy - (y + 0.5)) / 6.6)
    return dx * dx + dy * dy <= 1.0


def base():
    """Layer 0 -- the solid egg, shaded so it is not a flat blob once tinted."""
    px = []
    for y in range(SIZE):
        for x in range(SIZE):
            if not inside(x, y):
                px.append((0, 0, 0, 0))
                continue
            # light from the upper left; keep the range narrow so the tint stays readable
            shade = 255 - int(min(1.0, max(0.0, (x - 3.0) / 11.0 + (y - 3.0) / 13.0)) * 70)
            px.append((shade, shade, shade, 255))
    return px


SPOTS = [
    (6, 4), (9, 5),
    (5, 7), (8, 8), (11, 7),
    (6, 10), (9, 11),
    (7, 13),
]


def overlay():
    """Layer 1 -- the speckles, tinted with the egg's highlight colour."""
    px = [(0, 0, 0, 0)] * (SIZE * SIZE)
    for sx, sy in SPOTS:
        for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):
            x, y = sx + dx, sy + dy
            if 0 <= x < SIZE and 0 <= y < SIZE and inside(x, y):
                px[y * SIZE + x] = (255, 255, 255, 255)
    return px


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ns", required=True, help="the mod's namespace")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    out = pathlib.Path("src/main/resources/assets") / args.ns / "textures/item"

    want = {"spawn_egg.png": png(base()), "spawn_egg_overlay.png": png(overlay())}
    bad = []
    for name, data in want.items():
        path = out / name
        if args.check:
            if not path.exists():
                bad.append(f"{path}: missing")
            elif path.read_bytes() != data:
                bad.append(
                    f"{path}: drifted (shipped {hashlib.sha1(path.read_bytes()).hexdigest()[:8]}, "
                    f"generated {hashlib.sha1(data).hexdigest()[:8]})"
                )
        else:
            out.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    # The shared egg models point at OUR template, which only an overlay supplies -- mc21
    # parents it straight back to vanilla's, mc26 gives it these two layers. So a target that
    # ever forgets one turns every egg into the missing cube, silently. Assert the scope rather
    # than the symptom (catalog X11): every overlay that exists must carry the template.
    if args.check:
        rel = f"resources/assets/{args.ns}/models/item/template_spawn_egg.json"
        overlays = sorted(d for d in pathlib.Path("src").iterdir()
                          if d.is_dir() and d.name != "main" and (d / "resources").is_dir())
        if not overlays:
            bad.append("src/: no resource overlay found at all; the egg template has nowhere "
                       "to live and every egg will render as the missing cube")
        for d in overlays:
            if not (d / rel).is_file():
                bad.append(f"{d / rel}: missing -- this target's spawn eggs have no template")

    if args.check:
        if bad:
            print("gen-spawn-egg-art --check FAILED:", file=sys.stderr)
            for b in bad:
                print("  " + b, file=sys.stderr)
            return 1
        print(f"gen-spawn-egg-art --check: {len(want)} textures, all current")
    else:
        print(f"gen-spawn-egg-art: wrote {len(want)} textures to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
