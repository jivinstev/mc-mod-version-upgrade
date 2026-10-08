#!/usr/bin/env python3
"""Rename members a version moved, exactly where javac says the owner type matches.

    python3 tools/fix-missing-members.py --src src/main/java --log build.log --table <members.tsv> [--dry-run]

A rename table rewrites text, so a member rename has to guess the receiver's type from its spelling
(CATALOG §X7: `member:MOVEMENT_SPEED` renamed an unrelated class's constant too). javac already knows the
type: every `cannot find symbol` names the symbol AND the type it was looked up on. This applies a row only
at those error sites, so `GuiGraphicsExtractor.drawString` becomes `text` and a mod's own `drawString`
is never touched. Run, recompile, repeat; errors javac reports one at a time (a chain) take one round each.

Table rows (tab-separated, `#` comments):
    <Owner simple name>  <old member>  <new form>
The new form is a member name (`text`), or a template using {recv} for the receiver expression
(`{recv}.gameRenderer.mainRenderTarget` turns `mc.getMainRenderTarget()` into
`mc.gameRenderer.mainRenderTarget()`), or `static:<expr>` to replace `Owner.member` / an unqualified
`member` with a fixed expression (`static:Minecraft.getInstance().hasControlDown`).
Standard library only.
"""
import argparse, collections, pathlib, re, sys

ERR = re.compile(r"^(?P<file>/\S+?\.java):(?P<line>\d+): error: cannot find symbol")


def table(path):
    rows = {}
    for l in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        if not l.strip() or l.lstrip().startswith("#"):
            continue
        owner, old, new = [x.strip() for x in l.split("\t")[:3]]
        rows[(owner, old)] = new
    return rows


def sites(log_text):
    lines = log_text.splitlines()
    out, seen = [], set()
    for i, l in enumerate(lines):
        m = ERR.match(l)
        if not m or (m.group("file"), m.group("line")) in seen:
            continue
        sym = next((x.strip() for x in lines[i + 1:i + 6] if x.strip().startswith("symbol:")), "")
        loc = next((x.strip() for x in lines[i + 1:i + 7] if x.strip().startswith("location:")), "")
        sm = re.match(r"symbol:\s+(?:method|variable)\s+(\w+)", sym)
        lm = re.match(r"location:\s+(?:variable \w+ of type |class |interface |@interface )?([\w.$]+)", loc)
        if not sm or not lm:
            continue
        seen.add((m.group("file"), m.group("line")))
        out.append((m.group("file"), int(m.group("line")), sm.group(1), lm.group(1).split("<")[0].rsplit(".", 1)[-1]))
    return out


RECV = r"((?:[\w$]+(?:\([^()]*\))?\.)*[\w$]+(?:\([^()]*\))?)"


def apply_row(line, member, new, owner):
    """Rewrite the first call/use of `member` on this line; return the new line or None."""
    if new.startswith("static:"):
        expr = new[7:]
        out, n = re.subn(r"(?<![\w$.])(?:%s\.)?%s(?=\s*\()" % (re.escape(owner), re.escape(member)), expr, line, count=1)
        return out if n else None
    if "{recv}" in new:
        m = re.search(RECV + r"\.%s(?=\s*\()" % re.escape(member), line)
        if not m:
            return None
        return line[:m.start()] + new.replace("{recv}", m.group(1)) + line[m.end():]
    out, n = re.subn(r"(?<=\.)%s(?=\s*[\(<])|(?<=\.)%s\b" % (re.escape(member), re.escape(member)), new, line, count=1)
    return out if n else None


def run(src, log_text, rows, dry=False):
    src = pathlib.Path(src).resolve()
    by_file = collections.defaultdict(list)
    for f, ln, member, owner in sites(log_text):
        if (owner, member) in rows:
            by_file[pathlib.Path(f).resolve()].append((ln, member, owner, rows[(owner, member)]))
    report, missed = collections.Counter(), []
    for f, hits in by_file.items():
        if src not in f.parents:
            continue
        lines = f.read_text(encoding="utf-8").split("\n")
        for ln, member, owner, new in hits:
            out = apply_row(lines[ln - 1], member, new, owner)
            if out is None:
                missed.append(f"{f.name}:{ln} {owner}.{member}")
                continue
            lines[ln - 1] = out
            report[f"{owner}.{member}"] += 1
        if not dry:
            f.write_text("\n".join(lines), encoding="utf-8")
    return report, missed


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src"); ap.add_argument("--log"); ap.add_argument("--table")
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    report, missed = run(a.src, pathlib.Path(a.log).read_text(encoding="utf-8", errors="replace"), table(a.table), a.dry_run)
    print(f"fix-missing-members: {sum(report.values())} site(s) rewritten" + (" (dry run)" if a.dry_run else ""))
    for k, v in report.most_common():
        print(f"  {v:4d}  {k}")
    if missed:
        print(f"  {len(missed)} matched row(s) whose call shape was not found on the line: " + ", ".join(missed[:6]))
    return 0


def self_check():
    import tempfile
    rows = {("GuiGraphicsExtractor", "drawString"): "text", ("Minecraft", "getMainRenderTarget"): "{recv}.gameRenderer.mainRenderTarget",
            ("Screen", "hasControlDown"): "static:Minecraft.getInstance().hasControlDown"}
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "A.java"
        f.write_text("class A {\n    void r() {\n        g.drawString(font, s, 1, 2, -1);\n        drawString(x);\n"
                     "        var t = Minecraft.getInstance().getMainRenderTarget();\n        if (Screen.hasControlDown()) {}\n    }\n}\n",
                     encoding="utf-8")
        F = str(f)
        log = "\n".join([f"{F}:3: error: cannot find symbol", "  symbol:   method drawString(Font,String,int,int,int)",
                         "  location: variable g of type GuiGraphicsExtractor",
                         f"{F}:4: error: cannot find symbol", "  symbol:   method drawString(int)", "  location: class A",
                         f"{F}:5: error: cannot find symbol", "  symbol:   method getMainRenderTarget()", "  location: class Minecraft",
                         f"{F}:6: error: cannot find symbol", "  symbol:   method hasControlDown()", "  location: class Screen"])
        report, missed = run(d, log, rows)
        t = f.read_text(encoding="utf-8")
        ok = ("g.text(font, s, 1, 2, -1);" in t and "        drawString(x);" in t
              and "Minecraft.getInstance().gameRenderer.mainRenderTarget()" in t
              and "if (Minecraft.getInstance().hasControlDown())" in t and not missed)
    print("self-check:", "OK" if ok else f"FAIL {report} {missed}\n{t}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
