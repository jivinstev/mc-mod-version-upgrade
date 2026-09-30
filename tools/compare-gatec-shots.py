#!/usr/bin/env python3
"""Put two targets' Gate-C photographs side by side and say what differs.

Gate C on a multi-version mod (catalogue W) shoots the SAME named frames from the SAME pose in
the same hand-built room on both targets, which is the only reason a comparison is possible at
all: two shots of randomly-generated terrain would differ for reasons that have nothing to do
with the mod.

What this can and cannot say
---------------------------
It is a SCREEN, not a ranker. Two Minecraft versions do not draw the same scene identically --
lighting, shading and antialiasing all moved -- so a non-zero pixel difference is EXPECTED and is
reported as a number for a person to judge, never asserted to be zero. What it fails on is the
handful of differences that are unambiguously defects rather than rendering:

  missing    a frame one target shot and the other did not
  size       the two frames are different sizes, so no comparison below means anything
  dark       a frame is essentially black -- the render failed and every check on the file passed
  flat       a frame is one colour -- nothing was drawn
  magenta    materially more missing-texture cubes on one side than the other (catalogue V42b/c)

Reads PNG with the standard library only. A comparison tool that needs a package installed is a
comparison tool that does not run on a fresh clone.
"""
import argparse
import os
import struct
import sys
import zlib

# A missing texture is magenta-and-black checks; a real texture almost never is. Both numbers are
# deliberately loose -- this is looking for hundreds of cubes, not for one pink pixel.
MAGENTA_MIN_R = 170
MAGENTA_MIN_B = 170
MAGENTA_MAX_G = 90
# A side must exceed the other by this fraction of the frame to be flagged. MEASURED rather than
# guessed, which matters: the first value here was 0.02, picked out of the air, and it let a real
# defect through -- ten block models rendering as the magenta cube came to 1.3% of one frame and
# reported "ok". Across a clean six-frame pair the worst gap is 0.054% (a handful of genuinely pink
# item sprites), and the defect was 1.3%, so 0.3% sits about six times above the clean maximum and
# four times below the thing it has to catch. Re-measure before moving it; a threshold nobody
# measured is a threshold that fails in the reassuring direction.
MAGENTA_GAP = 0.003
DARK_MEAN = 8.0         # below this the frame is black for practical purposes
FLAT_STDDEV = 1.5       # below this nothing was drawn
TILES_X, TILES_Y = 32, 18


def read_png(path):
    """Decode a PNG to (width, height, channels, bytes). Truecolour 8-bit, which is what MC writes."""
    with open(path, 'rb') as fh:
        data = fh.read()
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError(f'{path}: not a PNG')
    pos, idat, width, height, colour = 8, [], None, None, None
    while pos < len(data):
        (length,) = struct.unpack('>I', data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b'IHDR':
            width, height, depth, colour, _, _, interlace = struct.unpack('>IIBBBBB', body[:13])
            if depth != 8 or interlace:
                raise ValueError(f'{path}: only 8-bit non-interlaced PNG is supported')
        elif kind == b'IDAT':
            idat.append(body)
        elif kind == b'IEND':
            break
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[colour]
    raw = zlib.decompress(b''.join(idat))
    stride = width * channels
    out = bytearray(stride * height)
    prev = bytearray(stride)
    src = 0
    for y in range(height):
        filt = raw[src]
        src += 1
        line = bytearray(raw[src:src + stride])
        src += stride
        if filt == 1:
            for x in range(channels, stride):
                line[x] = (line[x] + line[x - channels]) & 0xFF
        elif filt == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif filt == 3:
            for x in range(stride):
                a = line[x - channels] if x >= channels else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 0xFF
        elif filt == 4:
            for x in range(stride):
                a = line[x - channels] if x >= channels else 0
                b = prev[x]
                c = prev[x - channels] if x >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pred) & 0xFF
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return width, height, channels, bytes(out)


class Frame:
    def __init__(self, path):
        self.path = path
        self.w, self.h, self.ch, self.px = read_png(path)
        n = self.w * self.h
        total = 0
        total_sq = 0
        magenta = 0
        # Tile means, so a structural comparison survives one-pixel antialiasing differences.
        self.tiles = [0.0] * (TILES_X * TILES_Y)
        counts = [0] * (TILES_X * TILES_Y)
        for y in range(self.h):
            ty = y * TILES_Y // self.h
            row = y * self.w * self.ch
            for x in range(self.w):
                i = row + x * self.ch
                r, g, b = self.px[i], self.px[i + 1], self.px[i + 2]
                lum = (r + g + b) / 3.0
                total += lum
                total_sq += lum * lum
                if r >= MAGENTA_MIN_R and b >= MAGENTA_MIN_B and g <= MAGENTA_MAX_G:
                    magenta += 1
                t = ty * TILES_X + (x * TILES_X // self.w)
                self.tiles[t] += lum
                counts[t] += 1
        self.mean = total / n
        self.stddev = max(0.0, (total_sq / n) - self.mean * self.mean) ** 0.5
        self.magenta = magenta / n
        self.tiles = [self.tiles[i] / counts[i] if counts[i] else 0.0 for i in range(len(self.tiles))]


def compare(dir_a, dir_b, name_a, name_b):
    names = sorted(set(os.listdir(dir_a)) | set(os.listdir(dir_b)))
    names = [n for n in names if n.endswith('.png')]
    if not names:
        print(f'no .png frames in {dir_a} or {dir_b}', file=sys.stderr)
        return 1
    problems = []
    print(f'{"frame":<16} {"size":>10}  {name_a:>22}  {name_b:>22}  {"tile Δ":>7}  verdict')
    print('-' * 100)
    for name in names:
        pa, pb = os.path.join(dir_a, name), os.path.join(dir_b, name)
        if not os.path.exists(pa) or not os.path.exists(pb):
            missing = name_a if not os.path.exists(pa) else name_b
            problems.append(f'{name}: missing on {missing}')
            print(f'{name:<16} {"-":>10}  {"":>22}  {"":>22}  {"":>7}  MISSING on {missing}')
            continue
        fa, fb = Frame(pa), Frame(pb)
        if (fa.w, fa.h) != (fb.w, fb.h):
            problems.append(f'{name}: {fa.w}x{fa.h} vs {fb.w}x{fb.h} — nothing below compares')
            print(f'{name:<16} {"MISMATCH":>10}  {fa.w}x{fa.h}{"":>13}  {fb.w}x{fb.h}{"":>13}  {"":>7}  SIZE')
            continue
        delta = sum(abs(x - y) for x, y in zip(fa.tiles, fb.tiles)) / len(fa.tiles)
        verdict = []
        for frame, who in ((fa, name_a), (fb, name_b)):
            if frame.mean < DARK_MEAN:
                verdict.append(f'DARK on {who}')
                problems.append(f'{name}: black frame on {who} (mean {frame.mean:.1f})')
            if frame.stddev < FLAT_STDDEV:
                verdict.append(f'FLAT on {who}')
                problems.append(f'{name}: nothing drawn on {who} (stddev {frame.stddev:.2f})')
        if abs(fa.magenta - fb.magenta) > MAGENTA_GAP:
            worse = name_a if fa.magenta > fb.magenta else name_b
            verdict.append(f'MAGENTA on {worse}')
            problems.append(f'{name}: {max(fa.magenta, fb.magenta) * 100:.1f}% missing-texture '
                            f'magenta on {worse} vs {min(fa.magenta, fb.magenta) * 100:.1f}% on the other')
        desc_a = f'lum {fa.mean:5.1f} mag {fa.magenta * 100:4.1f}%'
        desc_b = f'lum {fb.mean:5.1f} mag {fb.magenta * 100:4.1f}%'
        print(f'{name:<16} {f"{fa.w}x{fa.h}":>10}  {desc_a:>22}  {desc_b:>22}  {delta:7.1f}  '
              + (', '.join(verdict) if verdict else 'ok'))
    print()
    print('tile Δ is the mean per-tile brightness difference over a '
          f'{TILES_X}x{TILES_Y} grid. It is NOT expected to be zero: two Minecraft versions')
    print('do not shade a scene identically. Read it as "how far apart do these look", and open '
          'the two files for anything surprising.')
    if problems:
        print()
        print(f'{len(problems)} problem(s):')
        for p in problems:
            print(f'  - {p}')
        return 1
    print()
    print('no missing, mis-sized, black, blank or missing-texture frames on either target.')
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('dir_a')
    ap.add_argument('dir_b')
    ap.add_argument('--name-a', default=None)
    ap.add_argument('--name-b', default=None)
    args = ap.parse_args()
    name_a = args.name_a or os.path.basename(os.path.dirname(args.dir_a.rstrip('/')))
    name_b = args.name_b or os.path.basename(os.path.dirname(args.dir_b.rstrip('/')))
    return compare(args.dir_a, args.dir_b, name_a, name_b)


if __name__ == '__main__':
    sys.exit(main())
