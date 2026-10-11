#!/usr/bin/env python3
"""Give every concrete block class the codec() 1.21 made abstract, from javac's own errors.

    python3 tools/fix-block-codecs.py --src src/main/java --log build.log [--dry-run] [--self-check]

Since 1.20.3 a block class that extends an abstract vanilla block (BushBlock, DirectionalBlock, BaseEntityBlock,
HorizontalDirectionalBlock, ...) must implement `protected MapCodec<? extends B> codec()` (CATALOG §72). Swept across
the popularity list this was the commonest single Forge-hop error left after the converters (X147: 49 mods). The fix
is the same at every site and javac names each one:

    X.java:49: error: X is not abstract and does not override abstract method codec() in DirectionalBlock

  * X has a one-argument (Properties) constructor -> `return simpleCodec(X::new);`, vanilla's own shape;
  * otherwise -> `return MapCodec.unit(this);` -- the block instance itself, which is what a codec for a registered
    block decodes to anyway (vanilla only reads block codecs to validate the BLOCK_TYPE registry).

A generic class or one javac names that is not in the source is LISTED. Standard library only.
"""
import argparse, importlib.util, pathlib, re, sys

_spec = importlib.util.spec_from_file_location("forge_shapes", pathlib.Path(__file__).resolve().parent / "forge-shapes.py")
fs = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(fs)

ERR = re.compile(r"^(?P<file>(?:[A-Za-z]:)?[\\/]\S+?\.java):(?P<line>\d+): error: (?P<cls>[\w$]+) is not abstract and does not "
                 r"override abstract method codec\(\) in (?P<base>[\w$.]+)\s*$", re.M)


def fix(text, cls):
    """-> (new text, how) or (text, None) when the class is not found / not fixable."""
    m = re.search(r"\b(?:class)\s+%s\b(?P<gen><[^{]*?>)?[^{;]*\{" % re.escape(cls), text)
    if not m or m.group("gen"):
        return text, None
    b0 = m.end() - 1
    b1 = fs.match(text, b0)
    if b1 < 0:
        return text, None
    body = text[b0:b1]
    if re.search(r"\bMapCodec<[^;{]*>\s+codec\s*\(\s*\)", body):
        return text, None
    simple = re.search(r"\b(?:public|protected|private)?\s*%s\s*\(\s*(?:final\s+)?(?:[\w.]*\.)?Properties\s+\w+\s*\)" % re.escape(cls), body)
    ret = f"simpleCodec({cls}::new)" if simple else "com.mojang.serialization.MapCodec.unit(this)"
    line_start = text.rfind("\n", 0, m.start()) + 1
    ind = re.match(r"[ \t]*", text[line_start:]).group(0)
    inner = ind + (ind or "   ")
    method = (f"\n{inner}@Override\n{inner}protected com.mojang.serialization.MapCodec<? extends {cls}> codec() {{\n"
              f"{inner}{ind or '   '}return {ret};\n{inner}}}\n")
    return text[:b0 + 1] + method + text[b0 + 1:], ("simpleCodec" if simple else "unit")


def run(src, log_text, dry=False):
    src = pathlib.Path(src).resolve()
    report, flags, changed = {"simpleCodec": 0, "unit": 0}, [], 0
    by_file = {}
    for m in ERR.finditer(log_text):
        f = pathlib.Path(m.group("file")).resolve()
        if src in f.parents and f.exists():
            by_file.setdefault(f, set()).add(m.group("cls"))
        else:
            flags.append(f"{m.group('cls')}: not in the source")
    for f, classes in by_file.items():
        t = f.read_text(encoding="utf-8")
        orig = t
        for c in sorted(classes):
            t, how = fix(t, c)
            if how:
                report[how] += 1
            else:
                flags.append(f"{f.name}: {c} not fixed (generic, nested oddly, or already has codec())")
        if t != orig:
            changed += 1
            if not dry:
                f.write_text(t, encoding="utf-8")
    return report, flags, changed


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src"); ap.add_argument("--log"); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sites", type=int, default=8); ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not (a.src and a.log):
        ap.error("--src and --log are required")
    report, flags, changed = run(a.src, pathlib.Path(a.log).read_text(encoding="utf-8", errors="replace"), a.dry_run)
    print(f"fix-block-codecs: {changed} file(s) changed, {sum(report.values())} site(s) rewritten"
          + "".join(f", {v} {k}" for k, v in report.items() if v))
    for s in flags[:a.sites]:
        print("  left: " + s)
    return 0


def self_check():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "a"; p.mkdir()
        (p / "Fern.java").write_text("package a;\npublic class Fern extends DirectionalBlock implements BonemealableBlock {\n"
                                     "   public Fern(BlockBehaviour.Properties props) {\n      super(props);\n   }\n}\n", encoding="utf-8")
        (p / "Cactus.java").write_text("package a;\npublic class Cactus extends BushBlock {\n"
                                       "   public Cactus(Supplier<Block> big, Properties p) { super(p); }\n"
                                       "   static class Inner extends BaseEntityBlock {\n      Inner(Properties p) { super(p); }\n   }\n}\n",
                                       encoding="utf-8")
        log = "\n".join([f"{p / 'Fern.java'}:2: error: Fern is not abstract and does not override abstract method codec() in DirectionalBlock",
                         f"{p / 'Cactus.java'}:2: error: Cactus is not abstract and does not override abstract method codec() in BushBlock",
                         f"{p / 'Cactus.java'}:4: error: Inner is not abstract and does not override abstract method codec() in BaseEntityBlock",
                         f"{p / 'Cactus.java'}:4: error: Inner is not abstract and does not override abstract method codec() in BaseEntityBlock"])
        report, flags, changed = run(d, log)
        fern, cac = (p / "Fern.java").read_text(encoding="utf-8"), (p / "Cactus.java").read_text(encoding="utf-8")
        run(d, log)
        ok = ("protected com.mojang.serialization.MapCodec<? extends Fern> codec() {\n      return simpleCodec(Fern::new);" in fern
              and "MapCodec<? extends Cactus> codec() {\n      return com.mojang.serialization.MapCodec.unit(this);" in cac
              and "MapCodec<? extends Inner> codec() {\n         return simpleCodec(Inner::new);" in cac
              and report == {"simpleCodec": 2, "unit": 1} and not flags
              and (p / "Fern.java").read_text(encoding="utf-8") == fern and (p / "Cactus.java").read_text(encoding="utf-8") == cac)
        if not ok:
            print(fern + cac, report, flags)
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
