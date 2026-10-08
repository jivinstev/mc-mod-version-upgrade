#!/usr/bin/env python3
"""Find code that needs an OPTIONAL dependency to load, without running the game.

A mod that lists a dependency as `optional` must still load when that mod is absent. Two shapes
break that, and both compile cleanly:

  BLOCKING  an @EventBusSubscriber class with a method whose parameter or return type comes from
            the optional mod. NeoForge scans those classes with getDeclaredMethods(), which
            resolves every method's types, so the mod fails to load. Always a crash.
  ATTENTION a mixin that refers to the optional mod's classes, directly or through one of the
            mod's own classes that does. Mixin code runs inside vanilla classes, so it runs whether
            or not the optional mod is present. It is safe only behind a "mod loaded" check, which
            this scan cannot see, so it is reported, not failed.

Reads .class files directly (no javap, no game). `ci-gates.py --env minimal` runs it on the
classes it is about to test, with the packages of the jars that environment leaves out.

  optional-dep-scan.py --classes build/classes/java/main --jar <optional-mod.jar> ...
  optional-dep-scan.py --classes <dir> --package baguchi/enchantwithmob
  optional-dep-scan.py --self-check
"""
import argparse, json, pathlib, re, struct, subprocess, sys, tempfile, zipfile

SUBSCRIBER = "Lnet/neoforged/fml/common/EventBusSubscriber;"
MIXIN = "Lorg/spongepowered/asm/mixin/Mixin;"


def parse_class(data):
    """(name, class refs, method descriptors, annotation types) from a class file."""
    if data[:4] != b"\xca\xfe\xba\xbe":
        return None
    i, (n,) = 10, struct.unpack(">H", data[8:10])
    pool, k = [None] * n, 1
    while k < n:
        tag = data[i]
        if tag == 1:
            ln, = struct.unpack(">H", data[i + 1:i + 3])
            pool[k] = ("utf8", data[i + 3:i + 3 + ln].decode("utf-8", "replace")); i += 3 + ln
        elif tag in (7, 8, 16, 19, 20):
            pool[k] = (tag, struct.unpack(">H", data[i + 1:i + 3])[0]); i += 3
        elif tag == 15:
            i += 4
        elif tag in (3, 4, 9, 10, 11, 12, 17, 18):
            i += 5
        elif tag in (5, 6):
            i += 9; k += 1
        else:
            return None
        k += 1
    utf = lambda j: pool[j][1] if pool[j] and pool[j][0] == "utf8" else ""
    this_cls, = struct.unpack(">H", data[i + 2:i + 4])
    name = utf(pool[this_cls][1])
    classes = {utf(e[1]) for e in pool if e and e[0] == 7}
    texts = [e[1] for e in pool if e and e[0] == "utf8"]
    i += 6
    ic, = struct.unpack(">H", data[i:i + 2]); i += 2 + 2 * ic

    def attrs(i):
        out = []
        c, = struct.unpack(">H", data[i:i + 2]); i += 2
        for _ in range(c):
            an, ln = struct.unpack(">HI", data[i:i + 6])
            out.append((utf(an), data[i + 6:i + 6 + ln])); i += 6 + ln
        return out, i

    def members(i, keep):
        c, = struct.unpack(">H", data[i:i + 2]); i += 2
        for _ in range(c):
            _f, nm, desc = struct.unpack(">HHH", data[i:i + 6])
            _a, i = attrs(i + 6)
            if keep is not None:
                keep.append((utf(nm), utf(desc)))
        return i

    i = members(i, None)
    methods = []
    i = members(i, methods)
    cattrs, _ = attrs(i)
    annos = set()
    for an, body in cattrs:       # class-level annotations only: walk each one's type index
        if an in ("RuntimeVisibleAnnotations", "RuntimeInvisibleAnnotations"):
            annos |= {utf(j) for j in class_annotation_types(body)}
    # descriptors and signatures name classes too (Lfoo/Bar;), and are what the JVM resolves
    for t in texts:
        classes.update(re.findall(r"L([\w/$]+);", t))
    return name, classes, methods, annos


def class_annotation_types(body):
    """The type index of every top-level annotation in a *Annotations attribute body."""
    out, (n,) = [], struct.unpack(">H", body[:2])
    i = 2

    def value(i):
        tag = chr(body[i]); i += 1
        if tag in "BCDFIJSZsc":
            return i + 2
        if tag == "e":
            return i + 4
        if tag == "@":
            return anno(i)[1]
        if tag == "[":
            c, = struct.unpack(">H", body[i:i + 2]); i += 2
            for _ in range(c):
                i = value(i)
            return i
        raise ValueError(tag)

    def anno(i):
        t, c = struct.unpack(">HH", body[i:i + 4]); i += 4
        for _ in range(c):
            i = value(i + 2)
        return t, i

    for _ in range(n):
        t, i = anno(i)
        out.append(t)
    return out


def load_classes(root):
    root = pathlib.Path(root)
    out = {}
    items = ([(p.name, p.read_bytes()) for p in root.rglob("*.class")] if root.is_dir() else
             [(n, zipfile.ZipFile(root).read(n)) for n in zipfile.ZipFile(root).namelist() if n.endswith(".class")])
    for _n, data in items:
        r = parse_class(data)
        if r:
            out[r[0]] = r
    return out


def jar_packages(jar):
    pk = set()
    with zipfile.ZipFile(jar) as z:
        for n in z.namelist():
            if n.endswith(".class") and "/" in n and not n.startswith(("META-INF/", "net/minecraft/", "com/mojang/")):
                pk.add(n.rsplit("/", 1)[0])
    return pk


def scan(classes, packages):
    def optional(c):
        return any(c == p or c.startswith(p + "/") for p in packages) or \
            any(c.rsplit("/", 1)[0] == p for p in packages)
    tainted = {n for n, (_nm, refs, _m, _a) in classes.items() if any(optional(r) for r in refs)}
    blocking, attention = [], []
    for n, (_nm, refs, methods, annos) in sorted(classes.items()):
        if SUBSCRIBER in annos:
            for m, desc in methods:
                bad = sorted({c for c in re.findall(r"L([\w/$]+);", desc) if optional(c)})
                if bad:
                    blocking.append({"class": n, "method": m, "types": bad})
        if MIXIN in annos:
            via = sorted(r for r in refs if optional(r) or (r in tainted and r != n))
            if via:
                attention.append({"class": n, "refers_to": via})
    return blocking, attention


def report(blocking, attention, out=sys.stdout):
    for b in blocking:
        print(f"BLOCKING  {b['class'].replace('/', '.')}.{b['method']} names {', '.join(t.replace('/', '.') for t in b['types'])}"
              " -- an @EventBusSubscriber class cannot name an optional mod's classes in a method's types; "
              "move the method to another class or register the listener only when the mod is loaded", file=out)
    for a in attention:
        print(f"ATTENTION mixin {a['class'].replace('/', '.')} refers to {', '.join(t.replace('/', '.') for t in a['refers_to'])}"
              " -- it runs whether or not the optional mod is loaded; check every use is behind a loaded check", file=out)


def self_check():
    src = {
        "opt/dep/Thing.java": "package opt.dep; public class Thing { public static int x() { return 1; } }",
        "net/neoforged/fml/common/EventBusSubscriber.java":
            "package net.neoforged.fml.common; import java.lang.annotation.*; @Retention(RetentionPolicy.RUNTIME) public @interface EventBusSubscriber {}",
        "org/spongepowered/asm/mixin/Mixin.java":
            "package org.spongepowered.asm.mixin; import java.lang.annotation.*; @Retention(RetentionPolicy.CLASS) public @interface Mixin {}",
        "my/Bad.java": "package my; @net.neoforged.fml.common.EventBusSubscriber public class Bad { static void h(opt.dep.Thing t) {} }",
        "my/Good.java": "package my; @net.neoforged.fml.common.EventBusSubscriber public class Good { static void h(String s) { if (s == null) opt.dep.Thing.x(); } }",
        "my/Holder.java": "package my; public class Holder { public static final int V = opt.dep.Thing.x(); }",
        "my/HopMixin.java": "package my; @org.spongepowered.asm.mixin.Mixin public class HopMixin { int f() { return Holder.V + 0; } }",
        "my/CleanMixin.java": "package my; @org.spongepowered.asm.mixin.Mixin public class CleanMixin { int f() { return 2; } }",
    }
    with tempfile.TemporaryDirectory() as t:
        t = pathlib.Path(t)
        for rel, body in src.items():
            (t / "src" / rel).parent.mkdir(parents=True, exist_ok=True)
            (t / "src" / rel).write_text(body, encoding="utf-8")
        r = subprocess.run(["javac", "-d", str(t / "out")] + [str(t / "src" / k) for k in src],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode:
            print("self-check: javac failed:", r.stderr[:400]); return 1
        # V is a compile-time constant only if javac can prove it; it calls a method, so Holder is real
        cl = {k: v for k, v in load_classes(t / "out").items() if not k.startswith("opt/")}
        blocking, attention = scan(cl, {"opt/dep"})
        ok = [b["class"] for b in blocking] == ["my/Bad"]
        ok &= [a["class"] for a in attention] == ["my/HopMixin"] and "my/Holder" in attention[0]["refers_to"]
        nothing, _ = scan(cl, set())
        ok &= nothing == []
    print("self-check:", "OK" if ok else f"FAIL blocking={blocking} attention={attention}")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--classes", help="the mod's compiled classes: a directory or a jar")
    ap.add_argument("--jar", action="append", default=[], help="an optional mod's jar (its packages are the optional ones)")
    ap.add_argument("--package", action="append", default=[], help="an optional package, slash-separated")
    ap.add_argument("--json", help="also write the findings here")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not a.classes or not (a.jar or a.package):
        ap.error("--classes and at least one --jar or --package are needed")
    packages = set(a.package)
    for j in a.jar:
        packages |= jar_packages(j)
    classes = load_classes(a.classes)
    if not classes:
        print(f"optional-dep-scan: no classes read from {a.classes} -- this is NOT a pass"); return 2
    blocking, attention = scan(classes, packages)
    report(blocking, attention)
    print(f"optional-dep-scan: {len(classes)} class(es), {len(packages)} optional package(s): "
          f"{len(blocking)} blocking, {len(attention)} for attention")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps({"blocking": blocking, "attention": attention}, indent=1), encoding="utf-8")
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
