#!/usr/bin/env python3
"""Replace a vanilla type 26.x removed with a library type of the same shape -- only in files that IMPORT it.

    python3 tools/convert-removed-types.py --src src/main/java [--dry-run] [--self-check]

A rename table rewrites tokens everywhere, so it cannot replace a SIMPLE name such as `Tuple` without also hitting a
mod's own class of that name. This step is scoped by the import: a file that says `import net.minecraft.util.Tuple;`
means vanilla's, and only there are the type and its accessors rewritten (CATALOG X125).

    net.minecraft.util.Tuple (gone in 26.x) -> org.apache.commons.lang3.tuple.MutablePair (on every Minecraft classpath)
        getA/getB/setA/setB -> getLeft/getRight/setLeft/setRight      -- mutable, like Tuple, so setB keeps working

MCreator writes one into every NeoForge 1.21.1 main class (its `workQueue` of (Runnable, delay) pairs).
Standard library only.
"""
import argparse, pathlib, re, sys

TYPES = [  # (removed fqn, replacement fqn, {old member: new member})
    ("net.minecraft.util.Tuple", "org.apache.commons.lang3.tuple.MutablePair",
     {"getA": "getLeft", "getB": "getRight", "setA": "setLeft", "setB": "setRight"}),
]


def convert(text):
    n = 0
    for old, new, members in TYPES:
        osimple, nsimple = old.rsplit(".", 1)[1], new.rsplit(".", 1)[1]
        imp = re.compile(r"(?m)^import\s+%s\s*;" % re.escape(old))
        if not imp.search(text):
            continue
        if re.search(r"(?m)^import\s+[\w.]+\.%s\s*;" % re.escape(nsimple), text) and not \
                re.search(r"(?m)^import\s+%s\s*;" % re.escape(new), text):
            continue                       # the replacement's simple name is taken in this file: leave it for a worker
        text = imp.sub(f"import {new};", text, count=1)
        text, k = re.subn(r"(?<![\w$.])%s(?![\w$])" % re.escape(osimple), nsimple, text)
        n += k
        for a, b in members.items():
            text, k = re.subn(r"\.%s\(" % re.escape(a), f".{b}(", text)
            n += k
    return text, n


def run(src, dry=False):
    files = sites = 0
    for f in sorted(pathlib.Path(src).rglob("*.java")):
        t = f.read_text(encoding="utf-8", errors="replace")
        new, n = convert(t)
        if new != t:
            files += 1; sites += n
            if not dry:
                f.write_text(new, encoding="utf-8")
    return files, sites


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src"); ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not a.src:
        ap.error("--src is required")
    files, sites = run(a.src, a.dry_run)
    print(f"convert-removed-types: {files} file(s) changed, {sites} rewrite(s)")
    return 0


def self_check():
    mc = ("package m;\nimport net.minecraft.util.Tuple;\nclass M {\n"
          "  static final Collection<Tuple<Runnable, Integer>> q = new ConcurrentLinkedQueue<>();\n"
          "  static void a(Runnable r, int t) { q.add(new Tuple(r, t)); }\n"
          "  static void tick() { q.forEach(w -> { w.setB(w.getB() - 1); if (w.getB() == 0) w.getA().run(); }); }\n}\n")
    out, n = convert(mc)
    ok = ("import org.apache.commons.lang3.tuple.MutablePair;" in out and "Collection<MutablePair<Runnable, Integer>>" in out
          and "new MutablePair(r, t)" in out and "w.setRight(w.getRight() - 1)" in out and "w.getLeft().run()" in out
          and "Tuple" not in out)
    own = "package m;\nclass M { Tuple t; void f() { t.getA(); } }\n"          # the mod's own Tuple: untouched
    ok &= convert(own)[0] == own
    print("self-check:", "OK" if ok else "FAIL\n" + out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
