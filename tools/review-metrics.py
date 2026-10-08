#!/usr/bin/env python3
"""Measure a port's diff against the review principles that need no judgement (REVIEW_PRINCIPLES.md).

    python3 tools/review-metrics.py --repo <clone> --base <ref> [--json] [--sites 8]

Reads `git diff <base>` over src/ and reports, per principle:
  P1 faithful   deleted files; methods whose body became trivial (empty, `return null/0/false`) while the
                base had logic; empty catch blocks the port added.
  P2 minimal    lines whose only change is whitespace; import lines removed and re-added unchanged
                (reordering); imports in changed files that nothing uses; private members added and unused.
  P3 native     classes the port added, with how many files use each (a shim used once is a candidate to
                inline; one used everywhere is a design choice to justify).
  P4 voice      added comments whose language (CJK or not) differs from the file's existing comments.
Each finding names file:line. The reviewer (a model) gets this list, so it judges instead of searching.
Standard library only.
"""
import argparse, collections, importlib.util, json, pathlib, re, subprocess, sys

_s = importlib.util.spec_from_file_location("fs", pathlib.Path(__file__).resolve().parent / "forge-shapes.py")
fs = importlib.util.module_from_spec(_s); _s.loader.exec_module(fs)
CJK = re.compile(r"[぀-ヿ㐀-鿿]")
TRIVIAL = re.compile(r"\{\s*(?:return\s+(?:null|0|0\.0[fFdD]?|false|true|""|List\.of\(\)|Collections\.empty\w*\(\))\s*;)?\s*\}")


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, encoding="utf-8",
                          errors="replace").stdout


def comments(text):
    return [m.group(0) for m in re.finditer(r"//[^\n]*|/\*.*?\*/", text, re.S)]


def measure(repo, base):
    out = collections.defaultdict(list)
    status = git(repo, "diff", "--name-status", "-M", base, "--", "src")
    files = []
    for l in status.splitlines():
        parts = l.split("\t")
        kind, path = parts[0][0], parts[-1]
        if kind == "D":
            out["P1 deleted files"].append(parts[1])
        elif path.endswith(".java"):
            files.append((kind, parts[1] if kind == "R" else path, path))
    num = {l.split("\t")[2]: int(l.split("\t")[0]) + int(l.split("\t")[1])
           for l in git(repo, "diff", "--numstat", base, "--", "src").splitlines() if l.split("\t")[0].isdigit()}
    numw = {l.split("\t")[2]: int(l.split("\t")[0]) + int(l.split("\t")[1])
            for l in git(repo, "diff", "-w", "--numstat", base, "--", "src").splitlines() if l.split("\t")[0].isdigit()}
    ws = {f: num[f] - numw.get(f, 0) for f in num if num[f] - numw.get(f, 0) > 0}
    for f, n in sorted(ws.items(), key=lambda x: -x[1]):
        out["P2 whitespace-only changed lines"].append(f"{f}: {n}")
    added_classes = {}
    for kind, old_path, path in files:
        new = (repo / path).read_text(encoding="utf-8", errors="replace")
        old = git(repo, "show", f"{base}:{old_path}") if kind != "A" else ""
        if kind == "A":
            added_classes[pathlib.Path(path).stem] = path
        # P2 import churn and unused imports
        oi = set(re.findall(r"(?m)^import\s+[^;]+;", old)); ni = set(re.findall(r"(?m)^import\s+[^;]+;", new))
        old_order = [i for i in re.findall(r"(?m)^import\s+[^;]+;", old) if i in ni]
        new_order = [i for i in re.findall(r"(?m)^import\s+[^;]+;", new) if i in oi]
        if old_order != new_order:
            out["P2 imports reordered"].append(f"{path}")
        body = re.sub(r"(?m)^import\s[^\n]*\n", "", new)
        masked = fs.code_spans(body) if hasattr(fs, "code_spans") else body
        old_body = re.sub(r"(?m)^import\s[^\n]*\n", "", old)
        used = lambda name, text: re.search(r"(?<![\w$])%s(?![\w$])" % re.escape(name), text)
        for imp in re.findall(r"(?m)^import\s+([\w.]+)\s*;", new):
            simple = imp.rsplit(".", 1)[-1]
            if used(simple, body):
                continue
            # only the port's: an import it added, or one whose last use it removed (the author's own unused
            # imports on the base are not the port's to touch -- P2 says leave them)
            was_there = re.search(r"(?m)^import\s+%s\s*;" % re.escape(imp), old)
            if not was_there or used(simple, old_body):
                out["P2 unused imports in changed files"].append(f"{path}: {imp}")
        # P1 bodies made trivial
        if old:
            om = collections.defaultdict(list)   # nested classes reuse names, so keep every same-key body
            for m in fs.methods(old):
                om[(m.name, len(m.params))].append(old[m.body_open:m.body_close + 1])
            for m in fs.methods(new):
                nb = new[m.body_open:m.body_close + 1]
                obs = om.get((m.name, len(m.params)), [])
                if TRIVIAL.fullmatch(nb.strip()) and obs and not any(TRIVIAL.fullmatch(b.strip()) for b in obs) \
                        and max(len(b) for b in obs) > 40:
                    out["P1 method bodies made trivial"].append(f"{path}:{fs.line_of(new, m.start)} {m.name}")
        # P1 empty catch blocks added; P4 comment language
        old_c = comments(old) if old else comments(new)
        file_cjk = sum(1 for c in old_c if CJK.search(c)) > len(old_c) / 2 if old_c else False
        for h in re.finditer(r"(?m)^\+(?!\+\+)(.*)$", git(repo, "diff", "-U0", "-M", base, "--", path)):
            line = h.group(1)
            if re.search(r"catch\s*\([^)]*\)\s*\{\s*\}", line):
                out["P1 empty catch blocks added"].append(f"{path}: {line.strip()[:80]}")
            c = re.search(r"//\s*(.+)$|/\*+\s*(.+?)(\*/|$)|^\s*\*\s+(.+)$", line)
            if c:
                txt = next(g for g in c.groups() if g)
                if len(txt) > 12 and bool(CJK.search(txt)) != file_cjk and old:
                    out["P4 comment language differs from the file"].append(f"{path}: {txt[:70]}")
    # P3: added classes and how widely they are used
    alljava = {p: p.read_text(encoding="utf-8", errors="replace") for p in (repo / "src").rglob("*.java")}
    for name, path in sorted(added_classes.items()):
        users = sum(1 for p, t in alljava.items() if p.stem != name and re.search(r"\b%s\b" % re.escape(name), t))
        out["P3 classes added by the port (users)"].append(f"{path}: used by {users} file(s)")
    return out


def fix_imports(repo, findings):
    by_file = {}
    for f in findings:
        path, imp = f.split(": ", 1)
        by_file.setdefault(path, set()).add(imp)
    n = 0
    for path, imps in by_file.items():
        p = repo / path
        t = p.read_text(encoding="utf-8")
        for imp in imps:
            t2 = __import__("re").sub(r"(?m)^import\s+%s\s*;[ \t]*\r?\n" % __import__("re").escape(imp), "", t, count=1)
            n += t2 != t
            t = t2
        p.write_text(t, encoding="utf-8")
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo"); ap.add_argument("--base"); ap.add_argument("--json", action="store_true")
    ap.add_argument("--sites", type=int, default=8); ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--fix", action="store_true", help="remove the unused imports the port introduced (P2); "
                                                         "nothing else is ever rewritten")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    res = measure(pathlib.Path(a.repo).resolve(), a.base)
    if a.fix:
        n = fix_imports(pathlib.Path(a.repo).resolve(), res.get("P2 unused imports in changed files", []))
        print(f"review-metrics: removed {n} unused import(s) the port introduced")
        res = measure(pathlib.Path(a.repo).resolve(), a.base)
    if a.json:
        print(json.dumps(res, indent=1)); return 0
    for k in sorted(res):
        print(f"{k}: {len(res[k])}")
        for s in res[k][:a.sites]:
            print("    " + s)
        if len(res[k]) > a.sites:
            print(f"    ... and {len(res[k]) - a.sites} more")
    if not res:
        print("review-metrics: nothing to report")
    return 0


def self_check():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        r = pathlib.Path(d)
        (r / "src/a").mkdir(parents=True)
        (r / "src/a/A.java").write_text("""package a;
import java.util.List;
import java.util.Map;
// 原始注释
class A {
    int f(int x) {
        int y = x * 2;
        return y + 1;
    }
    void g() { run(); }
}
""", encoding="utf-8")
        for c in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "b"]):
            subprocess.run(["git", "-C", d, *c], check=True)
        (r / "src/a/A.java").write_text("""package a;
import java.util.Map;
import java.util.List;
import java.util.Set;
// 原始注释
class A {
    int f(int x) {
        return 0;
    }
    void g()  { run(); }
    // the port changed this hook because the new platform has none
    void h() { try { run(); } catch (Exception e) {} }
}
""", encoding="utf-8")
        (r / "src/a/Shim.java").write_text("package a;\nclass Shim {}\n", encoding="utf-8")
        subprocess.run(["git", "-C", d, "add", "-A"], check=True)        # a port's new files are committed
        res = measure(r, "HEAD")
        want = {"P1 method bodies made trivial", "P2 imports reordered", "P2 unused imports in changed files",
                "P2 whitespace-only changed lines", "P1 empty catch blocks added",
                "P4 comment language differs from the file", "P3 classes added by the port (users)"}
        ok = want <= set(res) and any("Set" in x for x in res["P2 unused imports in changed files"])
        fix_imports(r, res["P2 unused imports in changed files"])
        t = (r / "src/a/A.java").read_text(encoding="utf-8")
        ok &= "import java.util.Set;" not in t and "import java.util.Map;" in t   # the port's removed, the author's kept
    print("self-check:", "OK" if ok else f"FAIL {sorted(set(want) - set(res))} {dict(res)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
