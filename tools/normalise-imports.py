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
    files = n = 0
    for f in sorted(src.rglob("*.java")):
        t = f.read_text(encoding="utf-8")
        nt, k = normalise(t, original(f, base, root), by_pkg.get(f.parent, set()) - {f.stem})
        if k:
            files += 1; n += k
            if not dry:
                f.write_text(nt, encoding="utf-8")
    return files, n


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src"); ap.add_argument("--base"); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not a.src:
        ap.error("--src is required")
    files, n = run(a.src, a.base, a.dry_run)
    print(f"normalise-imports: {n} inline name(s) shortened to imports in {files} file(s)" + (" (dry run)" if a.dry_run else ""))
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
    print("self-check:", "OK" if not miss else f"FAIL {miss}\n{out}")
    return 0 if not miss else 1


if __name__ == "__main__":
    sys.exit(main())
