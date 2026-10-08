#!/usr/bin/env python3
"""Turn the fully qualified names a port wrote inline back into imports, as the author would have.

    python3 tools/normalise-imports.py --src src/main/java --base <git ref> [--dry-run] [--self-check]

Rewrites are written fully qualified because a rename table cannot add an import (CATALOG §V19), and the
structural tools do the same where an import might collide. In a port that stays in its own workspace that
is harmless; in an UPSTREAM branch it is diff noise a reviewer has to read past, and code the author would
not have written. For each `pkg.Name` written inline in code (not in a string, a comment or an import), this
adds `import pkg.Name;` and shortens the use to `Name` -- unless:
  * the original file at --base already wrote that FQN inline (the author's own choice is kept);
  * `Name` is already imported from a different package, declared in the file, or the file's package has a
    class of that name (shortening would change which class it means);
  * it is a nested-type or static-member path whose outer class is not certain.
Standard library only.
"""
import argparse, pathlib, re, subprocess, sys

FQN = re.compile(r"(?<![\w.$\"])((?:net\.minecraft|net\.neoforged|com\.mojang|software\.bernie)(?:\.[a-z_][\w]*)+)\.([A-Z][\w]*)\b")


def code_spans(text):
    """Mask strings, chars and comments with spaces so matches only land in code."""
    out, i, n = list(text), 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            j = text.find("\n", i); j = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2); j = n if j < 0 else j + 2
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            j += 1
        else:
            i += 1; continue
        for k in range(i, min(j, n)):
            if out[k] != "\n":
                out[k] = " "
        i = j
    return "".join(out)


def original(path, base, root):
    if not base:
        return ""
    rel = path.resolve().relative_to(root)
    r = subprocess.run(["git", "-C", str(root), "show", f"{base}:{rel.as_posix()}"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout if r.returncode == 0 else ""


def normalise(text, orig, same_package_classes):
    masked = code_spans(text)
    body_start = 0
    for m in re.finditer(r"(?m)^(?:package|import)\s[^\n]*\n", text):
        body_start = m.end()
    imports = {m.group(1).rsplit(".", 1)[1]: m.group(1)
               for m in re.finditer(r"(?m)^import\s+([\w.]+)\s*;", text)}
    declared = set(re.findall(r"\b(?:class|interface|enum|record)\s+([A-Z]\w*)", text))
    edits, adds = [], set()
    for m in FQN.finditer(masked, body_start):
        pkg, name = m.group(1), m.group(2)
        fqn = f"{pkg}.{name}"
        if fqn in orig:
            continue
        if name in declared or (name in same_package_classes) or (name in imports and imports[name] != fqn):
            continue
        # a lowercase segment directly after the match would mean we cut a package path short
        edits.append((m.start(), m.end(), name))
        if imports.get(name) != fqn:
            adds.add(fqn); imports[name] = fqn
    for s, e, r in sorted(edits, key=lambda x: -x[0]):
        text = text[:s] + r + text[e:]
    for fqn in sorted(adds):
        text = add_import(text, fqn)
    return text, len(edits)


def blank_lines(text, orig):
    """Undo blank-line debris a port leaves: a removed import whose newline stayed behind, or three blank
    lines where an edit deleted a block. Measured against the author's own file, so their spacing is kept:
    blank lines between imports survive only if the original import block had any, and no run of blank lines
    grows longer than the original file's longest."""
    if not orig:
        return text
    lines = text.split("\n")
    imp = [i for i, l in enumerate(lines) if re.match(r"import\s", l)]
    ol = orig.split("\n")
    opos = {l.strip(): i for i, l in enumerate(ol) if re.match(r"import\s", l)}
    oblank = [i for i, l in enumerate(ol) if not l.strip()]
    def author_gap(a, b):
        """The author put a blank line between these two imports (both theirs, in this order)."""
        pa, pb = opos.get(a.strip()), opos.get(b.strip())
        return pa is not None and pb is not None and pa < pb and any(pa < k < pb for k in oblank)
    drop = set()
    if imp:
        for i in range(imp[0], imp[-1]):
            if lines[i].strip():
                continue
            prev = next((lines[k] for k in range(i - 1, imp[0] - 1, -1) if re.match(r"import\s", lines[k])), None)
            nxt = next((lines[k] for k in range(i + 1, imp[-1] + 1) if re.match(r"import\s", lines[k])), None)
            # a blank line the port left (a removed import's newline, or one set off around an added import)
            # goes; one the author put between two of their own import groups stays -- once
            if not (prev and nxt and author_gap(prev, nxt)) or (i > imp[0] and not lines[i - 1].strip()):
                drop.add(i)
    lines = [l for i, l in enumerate(lines) if i not in drop]
    longest = max((len(m.group(0)) - 1 for m in re.finditer(r"\n(?:[ \t]*\n)+", orig)), default=1)
    text = "\n".join(lines)
    return re.sub(r"\n(?:[ \t]*\n){%d,}" % (longest + 1), "\n" * (longest + 1), text)


def add_import(text, fqn):
    imps = list(re.finditer(r"(?m)^import\s+(static\s+)?([\w.*]+)\s*;[ \t]*\n", text))
    if not imps:
        pm = re.search(r"(?m)^package\s+[\w.]+\s*;[ \t]*\n", text)
        at = pm.end() if pm else 0
        return text[:at] + f"\nimport {fqn};\n" + text[at:]
    best, blen = imps[-1], -1
    for m in imps:
        if m.group(1):
            continue
        a, b = m.group(2).split("."), fqn.split(".")
        k = next((i for i in range(min(len(a), len(b))) if a[i] != b[i]), min(len(a), len(b)))
        if k > blen or (k == blen and m.group(2) < fqn):
            best, blen = m, k
    return text[:best.end()] + f"import {fqn};\n" + text[best.end():]


def run(src, base, dry=False):
    src = pathlib.Path(src).resolve()
    root = pathlib.Path(subprocess.run(["git", "-C", str(src), "rev-parse", "--show-toplevel"], capture_output=True,
                                       text=True, encoding="utf-8").stdout.strip() or src)
    by_pkg = {}
    for f in src.rglob("*.java"):
        by_pkg.setdefault(f.parent, set()).add(f.stem)
    files = n = blanks = 0
    for f in sorted(src.rglob("*.java")):
        t = f.read_text(encoding="utf-8")
        orig = original(f, base, root)
        nt, k = normalise(t, orig, by_pkg.get(f.parent, set()) - {f.stem})
        nt2 = blank_lines(nt, orig)
        b = nt2 != nt; nt = nt2
        blanks += b; k += b
        if k:
            files += 1; n += k
            if not dry:
                f.write_text(nt, encoding="utf-8")
    return files, n, blanks


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src"); ap.add_argument("--base"); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not a.src:
        ap.error("--src is required")
    files, n, blanks = run(a.src, a.base, a.dry_run)
    print(f"normalise-imports: {n - blanks} inline name(s) shortened to imports, blank-line debris removed in "
          f"{blanks} file(s); {files} file(s) changed" + (" (dry run)" if a.dry_run else ""))
    return 0


def self_check():
    t = """package my;

import net.minecraft.world.entity.Entity;
import java.awt.Item;

class A {
    // net.minecraft.world.level.Level in a comment stays
    void f(Object o) {
        if (!(o instanceof net.minecraft.world.entity.LivingEntity living)) return;
        net.minecraft.world.item.Item i = null;
        String s = "net.minecraft.world.entity.Mob";
        net.neoforged.fml.ModLoadingContext.get().getActiveContainer();
        net.minecraft.world.entity.Entity e = null;
        net.minecraft.core.Kept k = null;
    }
}
"""
    out, n = normalise(t, "net.minecraft.core.Kept", set())
    want = ["import net.minecraft.world.entity.LivingEntity;", "o instanceof LivingEntity living",
            "net.minecraft.world.item.Item i = null;", '"net.minecraft.world.entity.Mob"',
            "ModLoadingContext.get().getActiveContainer();", "import net.neoforged.fml.ModLoadingContext;",
            "        Entity e = null;", "net.minecraft.core.Kept k", "// net.minecraft.world.level.Level"]
    miss = [w for w in want if w not in out]
    again, n2 = normalise(out, "net.minecraft.core.Kept", set())
    if n2:
        miss.append("not idempotent")
    o = "package p;\n\nimport a.B;\nimport a.C;\n\nclass X {\n\n    int i;\n}\n"
    port = "package p;\n\nimport a.B;\n\nimport a.D;\n\nclass X {\n\n\n\n    int i;\n}\n"
    if blank_lines(port, o) != "package p;\n\nimport a.B;\nimport a.D;\n\nclass X {\n\n    int i;\n}\n":
        miss.append("blank-line debris kept")
    grouped = "import a.B;\n\nimport b.C;\n"
    if blank_lines(grouped, grouped) != grouped:
        miss.append("author's import grouping removed")
    # the author groups imports; the port removed one (its newline stayed) and set a new one off by a blank
    og = "package p;\n\nimport a.B;\nimport a.C;\nimport a.Gone;\n\nimport m.Y;\n\nclass X {}\n"
    pg = "package p;\n\nimport a.New;\n\nimport a.B;\nimport a.C;\n\n\nimport m.Y;\n\nclass X {}\n"
    if blank_lines(pg, og) != "package p;\n\nimport a.New;\nimport a.B;\nimport a.C;\n\nimport m.Y;\n\nclass X {}\n":
        miss.append("port debris kept inside an author-grouped import block: " + repr(blank_lines(pg, og)))
    print("self-check:", "OK" if not miss else f"FAIL {miss}\n{out}")
    return 0 if not miss else 1


if __name__ == "__main__":
    sys.exit(main())
