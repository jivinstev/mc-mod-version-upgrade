#!/usr/bin/env python3
"""Find the classes a dependency MOVED between the author's version and the target's, and rewrite the mod.

    python3 tools/dep-moves.py --repo <gradle project> --old-jar A.jar --new-jar B.jar [--old-jar ... --new-jar ...]
                               [--pairs FILE.tsv] [--apply] [--json OUT]
    python3 tools/dep-moves.py --self-check

WHY: a library sometimes renames its root package between the version a mod was written against and the one
the port targets. javac then reports one `package X does not exist` per import -- dozens of errors that read
as removed API, so a model is sent to find a "replacement" for each. Measured: a library renamed its root
package, workers spent five model calls without seeing it, and one rename of the prefix cleared 38 errors.
The answer is in the two jars, and a script can read it before any model runs.

For every fully qualified class the mod's sources reference that lives in the OLD jar (imports, static
imports, on-demand `pkg.*` imports, inline FQNs -- over every source set, via tools/srcsets.py), decide:
  unchanged  same FQN exists in the new jar
  moved      absent there, and the new jar has exactly ONE class with the same name (`Outer$Inner` matches
             `Outer.Inner`; the nested path is tried first, the bare simple name second)
  ambiguous  several candidates -- reported, NEVER applied (a wrong guess compiles and misbehaves)
  gone       none -- a real API removal; that is the model's work, not this tool's
A whole-root rename is reported as a prefix rule (`a.b -> c.b`) when most moved classes share one.

--apply rewrites only moved classes, word-boundary anchored and only in code (strings and comments are
masked, the way tools/normalise-imports.py does), so a prefix rule can never touch a class that stayed put
or text that merely mentions the old name. Rules are per referenced class, longest first; that is the prefix
rule applied first and the per-class moves after it, minus the risk of the prefix catching an unchanged
sibling. A second run finds nothing left to do. Standard library only.
"""
import argparse, importlib.util, json, pathlib, re, subprocess, sys, tempfile, zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
_ss = importlib.util.spec_from_file_location("srcsets", ROOT / "tools/srcsets.py")
srcsets = importlib.util.module_from_spec(_ss); _ss.loader.exec_module(srcsets)

CAP = 40
IMPORT = re.compile(r"(?m)^[ \t]*import\s+(static\s+)?([\w.]+?)(\.\*)?\s*;")
INLINE = re.compile(r"(?<![\w.$])[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\.[A-Z]\w*(?:\.[A-Za-z_]\w*)*")


def code_spans(text):
    """Mask strings, chars and comments with spaces so matches only land in code (same idea as
    tools/normalise-imports.py; offsets are preserved so edits apply to the original text)."""
    out, i, n = list(text), 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            j = text.find("\n", i); j = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2); j = n if j < 0 else j + 2
        elif text.startswith('"""', i):
            j = text.find('"""', i + 3); j = n if j < 0 else j + 3
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            j += 1
        else:
            i += 1; continue
        for k in range(i, min(j, n)):
            if out[k] != "\n":
                out[k] = " "
        i = j
    return "".join(out)


# ---------------------------------------------------------------------------------------------- jars
def jar_classes(path):
    """Set of class names in jar form (`a.b.Outer$Inner`). Anonymous/local classes (`Foo$1`), module and
    package descriptors and multi-release copies are not API a mod can import."""
    names = set()
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if not n.endswith(".class") or n.startswith("META-INF/"):
                continue
            c = n[:-6].replace("/", ".")
            last = c.rsplit(".", 1)[-1]
            if last in ("module-info", "package-info") or any(s[:1].isdigit() for s in last.split("$")):
                continue
            names.add(c)
    return names


class Index:
    def __init__(self, names):
        self.names = names
        self.by_local, self.by_simple, self.pkgs = {}, {}, set()
        for c in names:
            pkg, _, local = c.rpartition(".")
            self.pkgs.add(pkg)
            self.by_local.setdefault(local, []).append(c)
            self.by_simple.setdefault(local.rsplit("$", 1)[-1], []).append(c)

    def resolve(self, dotted):
        """Longest prefix of a dotted reference that names a class here -> jar-form name, else None.
        The class path starts at the first capitalised segment; deeper capitalised segments are nested
        classes, anything after the last matching one is a member."""
        segs = dotted.split(".")
        i0 = next((i for i, s in enumerate(segs) if s[:1].isupper()), None)
        if not i0:
            return None
        pkg = ".".join(segs[:i0])
        for k in range(len(segs), i0, -1):
            cand = pkg + "." + "$".join(segs[i0:k])
            if cand in self.names:
                return cand
        return None


def classify(name, old, new):
    """-> (state, [targets])."""
    if name in new.names:
        return "unchanged", []
    local = name.rpartition(".")[2]
    cands = new.by_local.get(local) or new.by_simple.get(local.rsplit("$", 1)[-1], [])
    if len(cands) == 1:
        return "moved", list(cands)
    return ("ambiguous", sorted(cands)) if cands else ("gone", [])


# ---------------------------------------------------------------------------------------------- source scan
def source_files(repo):
    repo = pathlib.Path(repo)
    dirs = srcsets.java_dirs(repo) or [d for d in [repo / "src/main/java"] if d.is_dir()]
    return sorted(f for d in dirs for f in d.rglob("*.java"))


def read(f):
    return f.read_bytes().decode("utf-8", errors="surrogateescape")


def references(text, old):
    """(classes referenced from old, on-demand packages that exist in old) for one file."""
    masked = code_spans(text)
    found, ondemand = set(), []
    imports = list(IMPORT.finditer(masked))
    for m in imports:
        path, star = m.group(2), m.group(3)
        c = old.resolve(path)
        if c:
            found.add(c)
        elif star and path in old.pkgs:
            ondemand.append(path)
    body = masked
    for m in imports:
        body = body[:m.start()] + " " * (m.end() - m.start()) + body[m.end():]
    for m in INLINE.finditer(body):
        c = old.resolve(m.group(0))
        if c:
            found.add(c)
    for pkg in ondemand:                       # `import p.*;` -- the classes used are the capitalised names in p
        for c in old.names:
            if c.rpartition(".")[0] == pkg and "$" not in c and re.search(r"(?<![\w.$])%s\b" % re.escape(c.rpartition(".")[2]), body):
                found.add(c)
    return found, ondemand


# ---------------------------------------------------------------------------------------------- prefix rules
def prefix_rules(moves):
    """Root renames shared by most moved classes: [(old_prefix, new_prefix, n_classes)].
    Per class, strip the package segments old and new have in common at the END (`a.x.sub` -> `b.x.sub`
    leaves `a` -> `b`); classes with the same stripped rule form a group; the rule is then extended over
    segments every class of the group has right after it, so a rename of a library's root reads as the
    library's root (`a.lib` -> `b.lib`), not as the one segment that happened to differ."""
    rules, pending = [], dict(moves)
    for _ in range(5):
        groups = {}
        for o, n in pending.items():
            op, np = o.rpartition(".")[0].split("."), n.rpartition(".")[0].split(".")
            s = 0
            while s < min(len(op), len(np)) - 1 and op[-1 - s] == np[-1 - s]:
                s += 1
            groups.setdefault((tuple(op[:len(op) - s]), tuple(np[:len(np) - s])), []).append((o, op[len(op) - s:]))
        if not groups:
            break
        (ro, rn), g = max(groups.items(), key=lambda kv: len(kv[1]))
        if len(g) < 2 or (not rules and len(g) * 2 <= len(moves)):   # the first rule must cover most of the moves
            break
        common = 0
        while all(len(t) > common for _, t in g) and len({t[common] for _, t in g}) == 1:
            common += 1
        ro, rn = list(ro) + g[0][1][:common], list(rn) + g[0][1][:common]
        rules.append((".".join(ro), ".".join(rn), len(g)))
        pending = {o: n for o, n in pending.items() if o not in {x for x, _ in g}}
    return rules


# ---------------------------------------------------------------------------------------------- rewrite
def build_rules(moved):
    """dotted old -> dotted new for classes that moved; a nested class whose outer moved with it is covered
    by the outer's rule (the regex keeps the `.Inner` tail)."""
    rules = {}
    for o, n in moved.items():
        od, nd = o.replace("$", "."), n.replace("$", ".")
        if "$" in o:
            outer = o.split("$")[0]
            if outer in moved and moved[outer].replace("$", ".") + od[len(outer):] == nd:
                continue
        rules[od] = nd
    return rules


def rewrite(text, rules, pkg_rules):
    """-> (new_text, n_replacements). Only masked-code positions are touched."""
    masked = code_spans(text)
    edits = []
    if rules:
        alt = "|".join(re.escape(k) for k in sorted(rules, key=len, reverse=True))
        edits += [(m.start(), m.end(), rules[m.group(0)]) for m in re.finditer(r"(?<![\w.$])(?:%s)(?![\w$])" % alt, masked)]
    if pkg_rules:
        alt = "|".join(re.escape(k) for k in sorted(pkg_rules, key=len, reverse=True))
        edits += [(m.start(), m.end(), pkg_rules[m.group(0)])
                  for m in re.finditer(r"(?<![\w.$])(?:%s)(?=\.\*\s*;)" % alt, masked)]
    for s, e, r in sorted(edits, reverse=True):
        text = text[:s] + r + text[e:]
    return text, len(edits)


# ---------------------------------------------------------------------------------------------- one pair
def run_pair(files, texts, old_jar, new_jar, apply):
    old, new = Index(jar_classes(old_jar)), Index(jar_classes(new_jar))
    refs, ondemand = set(), {}
    for f in files:
        r, od = references(texts[f], old)
        refs |= r
        for p in od:
            ondemand.setdefault(p, set()).update(c for c in r if c.rpartition(".")[0] == p)
    states = {c: classify(c, old, new) for c in refs}
    pick = lambda s: sorted(c for c, v in states.items() if v[0] == s)
    moved = {c: states[c][1][0] for c in pick("moved")}
    rep = {"old_jar": str(old_jar), "new_jar": str(new_jar), "referenced": len(refs), "unchanged": len(pick("unchanged")),
           "moved": [{"old": o, "new": n} for o, n in sorted(moved.items())],
           "ambiguous": [{"old": c, "candidates": states[c][1]} for c in pick("ambiguous")],
           "gone": pick("gone"),
           "prefix_rules": [{"old": a, "new": b, "classes": n} for a, b, n in prefix_rules(moved)],
           "on_demand": [], "files_changed": 0, "rewrites": 0}
    # `import p.*;` moves with its package only if every class used from it moved to one new package.
    pkg_rules = {}
    for p, used in sorted(ondemand.items()):
        dests = {states[c][1][0].rpartition(".")[0] for c in used if states[c][0] == "moved"}
        if used and all(states[c][0] == "moved" for c in used) and len(dests) == 1:
            pkg_rules[p] = dests.pop()
        elif used and any(states[c][0] != "unchanged" for c in used):
            rep["on_demand"].append(f"{p}.* : classes used from it do not all move to one package -- split the import by hand")
    if apply and (moved or pkg_rules):
        rules = build_rules(moved)
        for f in files:
            t2, n = rewrite(texts[f], rules, pkg_rules)
            if n:
                f.write_bytes(t2.encode("utf-8", errors="surrogateescape"))
                texts[f] = t2
                rep["files_changed"] += 1; rep["rewrites"] += n
    return rep


def render(rep, out=print):
    out(f"dep-moves: {pathlib.Path(rep['old_jar']).name} -> {pathlib.Path(rep['new_jar']).name}: {rep['referenced']} referenced, "
        f"{rep['unchanged']} unchanged, {len(rep['moved'])} moved, {len(rep['ambiguous'])} ambiguous, {len(rep['gone'])} gone")
    for r in rep["prefix_rules"]:
        out(f"  prefix rule: {r['old']} -> {r['new']}  ({r['classes']} classes)")
    def capped(label, rows):
        for r in rows[:CAP]:
            out(f"  {label}: {r}")
        if len(rows) > CAP:
            out(f"  ... {len(rows) - CAP} more {label}")
    capped("moved", [f"{m['old']} -> {m['new']}" for m in rep["moved"]])
    capped("ambiguous", [f"{a['old']} ? {', '.join(a['candidates'][:4])}" for a in rep["ambiguous"]])
    capped("gone", rep["gone"])
    capped("on-demand", rep["on_demand"])
    if rep.get("rewrites"):
        out(f"  applied: {rep['rewrites']} rewrites in {rep['files_changed']} files")


def analyse(repo, pairs, apply=False, out=print):
    files = source_files(repo)
    texts = {f: read(f) for f in files}
    reps = []
    for o, n in pairs:
        rep = run_pair(files, texts, o, n, apply)
        render(rep, out)
        reps.append(rep)
    return reps


# ---------------------------------------------------------------------------------------------- self-check
def self_check():
    import shutil
    if not shutil.which("javac"):
        print("self-check: SKIP (no javac)"); return 0
    fails = []
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)

        def jar(name, srcs):
            d = td / name; (d / "src").mkdir(parents=True)
            paths = []
            for rel, body in srcs.items():
                p = d / "src" / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(body)
                paths.append(str(p))
            r = subprocess.run(["javac", "-nowarn", "-d", str(d / "out"), *paths], capture_output=True, text=True)
            if r.returncode:
                raise SystemExit(f"self-check: javac failed: {r.stderr[-300:]}")
            j = td / f"{name}.jar"
            with zipfile.ZipFile(j, "w") as z:
                for c in (d / "out").rglob("*.class"):
                    z.write(c, c.relative_to(d / "out").as_posix())
            return j

        def cls(pkg, name, extra=""):
            return f"{pkg.replace('.', '/')}/{name}.java", f"package {pkg}; public class {name} {{ {extra} }}\n"

        A = {}
        for pkg, n, x in [("com.example.alphalib.core", "Alpha", "public static void helper() {}"),
                          ("com.example.alphalib.core.sub", "Beta", "public static class Inner {}"),
                          ("com.example.alphalib.core", "Delta", ""), ("com.example.alphalib.util", "Gamma", ""),
                          ("com.example.alphalib.util", "Dup", ""), ("com.example.alphalib.util", "Lost", ""),
                          ("com.example.alphalib.util", "Same", "")]:
            k, v = cls(pkg, n, x); A[k] = v
        B = {}
        for pkg, n, x in [("com.example.betalib.core", "Alpha", "public static void helper() {}"),
                          ("com.example.betalib.core.sub", "Beta", "public static class Inner {}"),
                          ("com.example.betalib.core", "Delta", ""), ("com.example.betalib.misc", "Gamma", ""),
                          ("com.example.betalib.x", "Dup", ""), ("com.example.betalib.y", "Dup", ""),
                          ("com.example.alphalib.util", "Same", "")]:
            k, v = cls(pkg, n, x); B[k] = v
        ja, jb = jar("old", A), jar("new", B)

        repo = td / "mod"; src = repo / "src/main/java/com/example/mod"; src.mkdir(parents=True)
        (repo / "build.gradle").write_text("plugins {}\n")
        original = '''package com.example.mod;

import com.example.alphalib.core.Alpha;
import static com.example.alphalib.core.Alpha.helper;
import com.example.alphalib.core.sub.Beta.Inner;
import com.example.alphalib.core.sub.*;
import com.example.alphalib.util.Same;
import com.example.alphalib.util.Dup;
import com.example.alphalib.util.Lost;

public class Use {
    // com.example.alphalib.core.Alpha is only mentioned here
    String s = "com.example.alphalib.core.Alpha";
    com.example.alphalib.util.Gamma g;
    com.example.alphalib.core.Delta d = null;
    Beta b; Alpha a; Inner i; Same same; Dup dup; Lost lost;
}
'''
        (src / "Use.java").write_text(original)
        pairs = [(ja, jb)]
        lines = []
        rep = analyse(repo, pairs, False, lines.append)[0]
        if (src / "Use.java").read_text() != original:
            fails.append("report-only edited the source")
        got = {m["old"]: m["new"] for m in rep["moved"]}
        want = {"com.example.alphalib.core.Alpha": "com.example.betalib.core.Alpha",
                "com.example.alphalib.core.Delta": "com.example.betalib.core.Delta",
                "com.example.alphalib.core.sub.Beta": "com.example.betalib.core.sub.Beta",
                "com.example.alphalib.core.sub.Beta$Inner": "com.example.betalib.core.sub.Beta$Inner",
                "com.example.alphalib.util.Gamma": "com.example.betalib.misc.Gamma"}
        if got != want:
            fails.append(f"moved {got}")
        if (rep["referenced"], rep["unchanged"], len(rep["ambiguous"]), rep["gone"]) != (8, 1, 1, ["com.example.alphalib.util.Lost"]):
            fails.append(f"counts {rep['referenced']} {rep['unchanged']} {rep['ambiguous']} {rep['gone']}")
        if [(r["old"], r["new"]) for r in rep["prefix_rules"]] != [("com.example.alphalib.core", "com.example.betalib.core")]:
            fails.append(f"prefix {rep['prefix_rules']}")
        analyse(repo, pairs, True, lambda s: None)
        after = (src / "Use.java").read_text()
        for must in ("import com.example.betalib.core.Alpha;", "import static com.example.betalib.core.Alpha.helper;",
                     "import com.example.betalib.core.sub.Beta.Inner;", "import com.example.betalib.core.sub.*;",
                     "com.example.betalib.misc.Gamma g;", "com.example.betalib.core.Delta d",
                     'String s = "com.example.alphalib.core.Alpha";', "// com.example.alphalib.core.Alpha is only",
                     "import com.example.alphalib.util.Same;", "import com.example.alphalib.util.Dup;",
                     "import com.example.alphalib.util.Lost;"):
            if must not in after:
                fails.append(f"after apply missing: {must}")
        analyse(repo, pairs, True, lambda s: None)
        if (src / "Use.java").read_text() != after:
            fails.append("not idempotent")
        rep2 = analyse(repo, pairs, False, lambda s: None)[0]
        if rep2["moved"] or rep2["unchanged"] != 1:
            fails.append(f"second report {rep2['moved']}")
    print("self-check:", "OK" if not fails else "FAIL " + "; ".join(fails))
    return 0 if not fails else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo"); ap.add_argument("--old-jar", action="append", default=[]); ap.add_argument("--new-jar", action="append", default=[])
    ap.add_argument("--pairs", help="TSV: old jar path, tab, new jar path"); ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json"); ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    pairs = list(zip(a.old_jar, a.new_jar))
    if len(a.old_jar) != len(a.new_jar):
        ap.error("--old-jar and --new-jar must be given the same number of times")
    if a.pairs:
        for ln in pathlib.Path(a.pairs).read_text().splitlines():
            if ln.strip() and not ln.startswith("#"):
                o, _, n = ln.partition("\t"); pairs.append((o.strip(), n.strip()))
    if not a.repo or not pairs:
        ap.error("--repo and at least one old/new jar pair are required")
    reps = analyse(a.repo, pairs, a.apply)
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(reps, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
