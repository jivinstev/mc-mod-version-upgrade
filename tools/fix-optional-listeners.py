#!/usr/bin/env python3
"""Move methods that name an OPTIONAL mod's types out of @EventBusSubscriber classes (CATALOG R27).

NeoForge scans every method of a subscriber class, and resolving a method's descriptor loads every type in
it -- so a helper that takes or returns an optional mod's class makes the whole mod fail to load when that mod
is absent, however carefully its calls are guarded. The fix is mechanical: move the method into a plain
helper class beside the original (loaded only when called, and every call is already guarded) and point the
callers at it.

    fix-optional-listeners.py --src src/main/java --classes build/classes/java/main --jar <optional mod.jar> ...
    fix-optional-listeners.py --src src/main/java --classes ... --package baguchi/enchantwithmob  [--apply]
    fix-optional-listeners.py --self-check

What it moves, from tools/optional-dep-scan.py's BLOCKING findings:
  - a static method of the subscriber class whose own descriptor names the optional mod;
  - for a synthetic lambda (lambda$name$N), the static method `name` that contains it.
  The method goes to `<Class>Optional` in the same package. Unqualified calls in the original file and
  `<Class>.method(` calls anywhere under --src are rewritten; another package gets an import. Members of the
  original class the moved body uses are qualified (`<Class>.FIELD`), and a private one loses `private`.

What it refuses, by name (a person decides):
  - an @SubscribeEvent method (the event type itself is the optional mod's: register it from the guarded
    block instead);
  - an instance method, an overloaded name, or a method it cannot find in the source.
Report only unless --apply. Run optional-dep-scan again after compiling: it must report 0 blocking.
"""
import argparse, importlib.util, pathlib, re, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _scan_module():
    spec = importlib.util.spec_from_file_location("ods", ROOT / "tools/optional-dep-scan.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def find_method(text, name):
    """[(start, end)] of every method declaration called `name` (annotations and javadoc included)."""
    out = []
    for m in re.finditer(r"(?m)^([ \t]*)((?:public|protected|private|static|final|synchronized|\s)+)"
                         r"(?:<[^>]*>\s*)?[\w.<>\[\], ?]+\s+" + re.escape(name) + r"\s*\(", text):
        if "static" not in m.group(2) and not re.search(r"\b(public|private|protected)\b", m.group(2)):
            continue
        # back up over annotations, comments and blank lines that belong to it
        start = m.start()
        lines = text[:start].split("\n")
        k = len(lines) - 1
        while k > 0 and re.match(r"\s*(@|//|\*|/\*\*|\*/)", lines[k - 1]):
            k -= 1
        start = len("\n".join(lines[:k])) + (1 if k else 0)
        # the body: from the first { after the signature, matched braces, skipping strings and chars
        i = text.index("{", m.end())
        depth, j = 0, i
        while j < len(text):
            c = text[j]
            if c in "\"'":
                q = c; j += 1
                while j < len(text) and text[j] != q:
                    j += 2 if text[j] == "\\" else 1
            elif c == "/" and text[j + 1:j + 2] == "/":
                j = text.index("\n", j)
            elif c == "/" and text[j + 1:j + 2] == "*":
                j = text.index("*/", j) + 1
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        end = j + 1
        if end < len(text) and text[end] == "\n":
            end += 1
        out.append((start, end, m.group(2)))
    return out


def class_members(text, cls):
    """Static fields and methods declared directly in `cls` (name -> is_private)."""
    out = {}
    for m in re.finditer(r"(?m)^[ \t]+((?:public|protected|private|static|final|\s)+)[\w.<>\[\], ?]+\s+(\w+)\s*[=;(]", text):
        mods = m.group(1)
        if "static" in mods:
            out[m.group(2)] = "private" in mods
    return out


def plan(src, classes, packages, scan=None):
    """-> (moves: {java file: {"cls", "pkg", "methods": set}}, refused: [str])."""
    ods = scan or _scan_module()
    cl = ods.load_classes(classes)
    blocking, _att = ods.scan(cl, packages)
    moves, refused = {}, []
    for b in blocking:
        internal = b["class"]
        if "$" in internal.rsplit("/", 1)[-1]:
            refused.append(f"{internal}.{b['method']}: in a nested class; move it by hand"); continue
        java = pathlib.Path(src, internal + ".java")
        if not java.is_file():
            refused.append(f"{internal}: no source at {java}"); continue
        m = re.match(r"lambda\$(\w+)\$\d+$", b["method"])
        name = m.group(1) if m else b["method"]
        if name.startswith("lambda$") or name in ("<init>", "<clinit>"):
            refused.append(f"{internal}.{b['method']}: not a named method"); continue
        text = java.read_text(encoding="utf-8")
        found = find_method(text, name)
        if len(found) != 1:
            refused.append(f"{internal}.{name}: {len(found)} declarations found (overloaded or not in source)")
            continue
        s, e, mods = found[0]
        if "@SubscribeEvent" in text[s:e].split("{", 1)[0]:
            refused.append(f"{internal}.{name}: a listener whose own signature names the optional mod -- "
                           "register it from the guarded block instead of by annotation"); continue
        if "static" not in mods:
            refused.append(f"{internal}.{name}: an instance method"); continue
        ent = moves.setdefault(str(java), {"cls": internal.rsplit("/", 1)[-1],
                                           "pkg": internal.rsplit("/", 1)[0].replace("/", "."), "methods": set()})
        ent["methods"].add(name)
    return moves, refused


def apply_moves(src, moves):
    """Rewrite the sources. -> list of what was done."""
    done = []
    src = pathlib.Path(src)
    for java, ent in moves.items():
        java = pathlib.Path(java)
        text = java.read_text(encoding="utf-8")
        cls, pkg, helper = ent["cls"], ent["pkg"], ent["cls"] + "Optional"
        members = class_members(text, cls)
        bodies = []
        for name in sorted(ent["methods"]):
            (s, e, _mods), = find_method(text, name)
            body = text[s:e]
            text = text[:s] + text[e:]
            bodies.append((name, body))
        moved = {n for n, _ in bodies}
        # members of the original class the moved bodies use: qualify, and widen private ones
        widen = set()
        new_bodies = []
        for name, body in bodies:
            head, rest = body.split("{", 1)
            head = re.sub(r"\bprivate\s+", "", head)                     # helpers are called from the original
            for mem, priv in members.items():
                if mem in moved:
                    continue
                pat = re.compile(r"(?<![\w.])" + re.escape(mem) + r"\b(?!\s*[:=][^=])")
                if pat.search(rest):
                    rest = pat.sub(f"{cls}.{mem}", rest)
                    if priv:
                        widen.add(mem)
            new_bodies.append(head + "{" + rest)
        for mem in sorted(widen):
            text = re.sub(r"(?m)^([ \t]+)private\s+((?:static|final|\s)+[\w.<>\[\], ?]+\s+" + re.escape(mem) + r"\s*[=;(])",
                          r"\1\2", text, count=1)
        # calls left in the original file
        for name in moved:
            text = re.sub(r"(?<![\w.])" + re.escape(name) + r"\s*\(", f"{helper}.{name}(", text)
        java.write_text(text, encoding="utf-8")
        imports = "\n".join(re.findall(r"(?m)^import\s+[\w.*]+\s*;", java.read_text(encoding="utf-8")))
        out = (f"package {pkg};\n\n{imports}\n\n"
               f"/** Moved out of {cls} (an @EventBusSubscriber): these methods name an optional mod's types,\n"
               f" * and NeoForge resolves every method type of a subscriber class when it scans it. This class\n"
               f" * loads only when one of them is called, and every call is behind the mod-loaded check. */\n"
               f"public final class {helper} {{\n    private {helper}() {{}}\n\n"
               + "\n".join(new_bodies).rstrip() + "\n}\n")
        (java.parent / f"{helper}.java").write_text(out, encoding="utf-8")
        done.append(f"{pkg}.{cls}: moved {', '.join(sorted(moved))} to {helper}"
                    + (f"; widened {', '.join(sorted(widen))}" if widen else ""))
        # callers anywhere else: Cls.method( -> Helper.method(
        for f in src.rglob("*.java"):
            if f == java or f.name == f"{helper}.java":
                continue
            t = f.read_text(encoding="utf-8")
            n = t
            for name in moved:
                n = re.sub(r"\b" + re.escape(cls) + r"\s*\.\s*" + re.escape(name) + r"\s*\(", f"{helper}.{name}(", n)
            if n != t:
                fpkg = (re.search(r"(?m)^package\s+([\w.]+)\s*;", n) or [None, ""])[1]
                if fpkg != pkg and f"import {pkg}.{helper};" not in n:
                    n = re.sub(r"(?m)^(import\s+" + re.escape(f"{pkg}.{cls}") + r"\s*;)", rf"\1\nimport {pkg}.{helper};", n, count=1)
                    if f"import {pkg}.{helper};" not in n:
                        n = re.sub(r"(?m)^(package\s+[\w.]+\s*;\n)", rf"\1\nimport {pkg}.{helper};\n", n, count=1)
                f.write_text(n, encoding="utf-8")
                done.append(f"  callers updated in {f.relative_to(src)}")
    return done


def self_check():
    import shutil, subprocess
    ok = True
    failed = []
    def _chk(c, n):
        if not c:
            failed.append(n)
        return bool(c)
    if not shutil.which("javac"):
        print("self-check: SKIP (no javac)"); return 0
    with tempfile.TemporaryDirectory() as t:
        t = pathlib.Path(t)
        files = {
            "com/example/dep/Cap.java": "package com.example.dep; public class Cap { public void add(String s) {} }",
            "com/example/dep/Reg.java": "package com.example.dep; public class Reg { public static String id() { return \"x\"; } }",
            "net/neoforged/fml/common/EventBusSubscriber.java":
                "package net.neoforged.fml.common; import java.lang.annotation.*; @Retention(RetentionPolicy.RUNTIME) public @interface EventBusSubscriber {}",
            "net/neoforged/bus/api/SubscribeEvent.java":
                "package net.neoforged.bus.api; import java.lang.annotation.*; @Retention(RetentionPolicy.RUNTIME) public @interface SubscribeEvent {}",
            "com/example/app/Events.java": """package com.example.app;

import java.util.List;
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.fml.common.EventBusSubscriber;
import com.example.dep.Cap;

@EventBusSubscriber
public class Events {
    private static final String PREFIX = "p:";

    @SubscribeEvent
    public static void onJoin(String event) {
        if (event.isEmpty()) setup(event, new Cap());
    }

    /** helper */
    private static void setup(String name, Cap cap) {
        cap.add(PREFIX + name);
    }

    public static String label(List<String> xs) {
        StringBuilder b = new StringBuilder();
        Cap c = new Cap();
        xs.forEach(x -> c.add(x));                 // the lambda captures c: its synthetic method names Cap
        return b.toString();
    }
}
""",
            "com/example/app/other/User.java": "package com.example.app.other;\nimport com.example.app.Events;\npublic class User { String f() { return Events.label(java.util.List.of()); } }\n",
        }
        for rel, body in files.items():
            (t / "src" / rel).parent.mkdir(parents=True, exist_ok=True)
            (t / "src" / rel).write_text(body, encoding="utf-8")
        def compile_all():
            srcs = [str(p) for p in (t / "src").rglob("*.java")]
            shutil.rmtree(t / "out", ignore_errors=True)
            r = subprocess.run(["javac", "-d", str(t / "out")] + srcs, capture_output=True, text=True, encoding="utf-8")
            return r.returncode == 0, r.stderr
        good, err = compile_all()
        if not good:
            print("self-check: fixture does not compile:", err[:400]); return 1
        appsrc = t / "src"
        mv, rf = plan(appsrc, t / "out", {"com/example/dep"})
        ok &= _chk(set(next(iter(mv.values()))["methods"]) == {"setup", "label"} and not rf, 1)
        done = apply_moves(appsrc, mv)
        good, err = compile_all()
        ok &= _chk(good, 2)
        if not good:
            print(err[:800])
        ods = _scan_module()
        cl = {k: v for k, v in ods.load_classes(t / "out").items() if not k.startswith("com/example/dep/")}
        blocking, _ = ods.scan(cl, {"com/example/dep"})
        ok &= _chk(blocking == [], 3)                                              # A/B: blocking before, none after
        ev = (appsrc / "com/example/app/Events.java").read_text(encoding="utf-8")
        ok &= _chk("EventsOptional.setup(event, new Cap())" in ev and "static final String PREFIX" in ev, 4)
        ok &= _chk("private static final String PREFIX" not in ev, 5)               # widened for the helper
        ok &= _chk("EventsOptional.label(" in (appsrc / "com/example/app/other/User.java").read_text(encoding="utf-8"), 6)
        ok &= _chk("import com.example.app.EventsOptional;" in (appsrc / "com/example/app/other/User.java").read_text(encoding="utf-8"), 7)
        ok &= _chk("Events.PREFIX + name" in (appsrc / "com/example/app/EventsOptional.java").read_text(encoding="utf-8"), 8)
        # a listener whose event type is the optional mod's is refused, not moved
        (appsrc / "com/example/app/Bad.java").write_text("package com.example.app;\nimport net.neoforged.bus.api.SubscribeEvent;\n"
            "import net.neoforged.fml.common.EventBusSubscriber;\n@EventBusSubscriber\npublic class Bad {\n"
            "    @SubscribeEvent\n    public static void on(com.example.dep.Cap c) {}\n}\n", encoding="utf-8")
        compile_all()
        _mv2, rf2 = plan(appsrc, t / "out", {"com/example/dep"})
        ok &= _chk(any("com/example/app/Bad.on" in r and "guarded block" in r for r in rf2), 9)
    print("self-check:", "OK" if ok else f"FAIL (checks {failed})")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", help="the source root, e.g. src/main/java")
    ap.add_argument("--classes", help="the compiled classes (directory or jar)")
    ap.add_argument("--jar", action="append", default=[], help="an optional mod's jar")
    ap.add_argument("--package", action="append", default=[], help="an optional package, slash-separated")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not (a.src and a.classes and (a.jar or a.package)):
        ap.error("--src, --classes and at least one --jar or --package are needed")
    ods = _scan_module()
    packages = set(a.package)
    for j in a.jar:
        packages |= ods.jar_packages(j)
    moves, refused = plan(a.src, a.classes, packages, ods)
    for java, ent in moves.items():
        print(f"move {', '.join(sorted(ent['methods']))} out of {ent['pkg']}.{ent['cls']}")
    for r in refused:
        print(f"REFUSED (by hand): {r}")
    if a.apply and moves:
        for line in apply_moves(a.src, moves):
            print(line)
        print("now recompile, then run tools/optional-dep-scan.py again: it must report 0 blocking")
    elif moves:
        print("(report only -- pass --apply to move them)")
    if not moves and not refused:
        print("fix-optional-listeners: nothing to move")
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
