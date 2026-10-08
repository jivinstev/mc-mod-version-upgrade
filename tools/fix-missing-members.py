#!/usr/bin/env python3
"""Rename members a version moved, exactly where javac says the owner type matches.

    python3 tools/fix-missing-members.py --src src/main/java --log build.log --table <members.tsv> [--dry-run]

A rename table rewrites text, so a member rename has to guess the receiver's type from its spelling
(CATALOG §X7: `member:MOVEMENT_SPEED` renamed an unrelated class's constant too). javac already knows the
type: every `cannot find symbol` names the symbol AND the type it was looked up on. This applies a row only
at those error sites, so `GuiGraphicsExtractor.drawString` becomes `text` and a mod's own `drawString`
is never touched. Run, recompile, repeat; errors javac reports one at a time (a chain) take one round each.

Also read: `<field> has private access in <Owner>` -- the shape a class takes when it becomes a record (its fields
go private behind accessors); a row `Owner  field  field()` turns the read into the accessor call.

Table rows (tab-separated, `#` comments):
    <Owner simple name>  <old member>  <new form>
The new form is a member name (`text`), or a template using {recv} for the receiver expression
(`{recv}.gameRenderer.mainRenderTarget` turns `mc.getMainRenderTarget()` into
`mc.gameRenderer.mainRenderTarget()`; {a0}, {a1}, ... are the call's own arguments, so `{recv}.store({a0}, X, {a1})`
replaces the whole call), or `static:<expr>` to replace `Owner.member` / an unqualified
`member` with a fixed expression (`static:Minecraft.getInstance().hasControlDown`).
Standard library only.
"""
import argparse, collections, pathlib, re, sys

ERR = re.compile(r"^(?P<file>(?:[A-Za-z]:)?[\\/]\S+?\.java):(?P<line>\d+): error: cannot find symbol")
# A class that became a record keeps its fields PRIVATE behind same-named accessors (26.2's EnchantmentInstance):
# javac then reports the field read, with the owner, on one line.
PRIV = re.compile(r"^(?P<file>(?:[A-Za-z]:)?[\\/]\S+?\.java):(?P<line>\d+): error: (?P<sym>\w+) has private access in "
                  r"(?P<owner>[\w.$]+)")


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
        p = PRIV.match(l)
        if p:
            caret = lines[i + 2] if i + 2 < len(lines) else ""
            col = caret.index("^") if caret.strip() == "^" else None     # javac copies the line's tabs, so it aligns
            key = (p.group("file"), p.group("line"), p.group("sym"), col)    # gradle echoes javac twice
            if key in seen:
                continue
            seen.add(key)
            out.append((p.group("file"), int(p.group("line")), p.group("sym"), p.group("owner").rsplit(".", 1)[-1], col))
            continue
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
        out.append((m.group("file"), int(m.group("line")), sm.group(1), lm.group(1).split("<")[0].rsplit(".", 1)[-1], None))
    return out


RECV = r"((?:[\w$]+(?:\([^()]*\))?\.)*[\w$]+(?:\([^()]*\))?)"


def call_args(line, open_paren):
    """([arg text, ...], index after the closing paren) of the call opening at open_paren, or None."""
    depth, cur, parts, i, quote = 0, "", [], open_paren, None
    while i < len(line):
        c = line[i]
        if quote:
            cur += c
            if c == "\\":
                cur += line[i + 1:i + 2]; i += 1
            elif c == quote:
                quote = None
        elif c in "\"'":
            quote = c; cur += c
        elif c in "([{":
            depth += 1
            if depth > 1 or c != "(":
                cur += c
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return ([p for p in parts + [cur] if p.strip()] if (parts or cur.strip()) else []), i + 1
            cur += c
        elif c == "," and depth == 1:
            parts.append(cur); cur = ""
        else:
            cur += c
        i += 1
    return None


def apply_row(line, member, new, owner, col=None):
    """Rewrite the first call/use of `member` on this line; return the new line or None."""
    if new.startswith("static:"):
        expr = new[7:]
        out, n = re.subn(r"(?<![\w$.])(?:%s\.)?%s(?=\s*\()" % (re.escape(owner), re.escape(member)), expr, line, count=1)
        return out if n else None
    if "{recv}" in new:
        m = re.search(RECV + r"\.%s(?=\s*\()" % re.escape(member), line)
        if not m:
            return None
        end = m.end()
        if "{a" in new:          # the call's arguments, split at top-level commas: {a0}, {a1}, ...
            args = call_args(line, line.index("(", m.end()))
            if args is None:
                return None
            parts, end = args
            if any("{a%d}" % i in new for i in range(len(parts), 9)):
                return None
            for i, a in enumerate(parts):
                new = new.replace("{a%d}" % i, a.strip())
        return line[:m.start()] + new.replace("{recv}", m.group(1)) + line[end:]
    if new == member + "()":     # field -> accessor: only a read (not a call already, not an assignment target)
        if col is not None:      # javac's caret is on the `.` before the field: rewrite exactly that site
            m = re.compile(r"\.\s*%s\b(?!\s*\()(?!\s*=[^=])" % re.escape(member)).match(line, col)
            return line[:m.end()] + "()" + line[m.end():] if m else None
        out, n = re.subn(r"(?<=\.)%s\b(?!\s*\()(?!\s*=[^=])" % re.escape(member), new, line, count=1)
        return out if n else None
    out, n = re.subn(r"(?<=\.)%s(?=\s*[\(<])|(?<=\.)%s\b" % (re.escape(member), re.escape(member)), new, line, count=1)
    return out if n else None


def run(src, log_text, rows, dry=False):
    src = pathlib.Path(src).resolve()
    by_file = collections.defaultdict(list)
    for f, ln, member, owner, col in sites(log_text):
        if (owner, member) in rows:
            by_file[pathlib.Path(f).resolve()].append((ln, member, owner, rows[(owner, member)], col))
    report, missed = collections.Counter(), []
    for f, hits in by_file.items():
        if src not in f.parents:
            continue
        lines = f.read_text(encoding="utf-8").split("\n")
        for ln, member, owner, new, col in sorted(hits, key=lambda h: -(h[4] or 0)):   # right to left: columns stay valid
            out = apply_row(lines[ln - 1], member, new, owner, col)
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
            ("Screen", "hasControlDown"): "static:Minecraft.getInstance().hasControlDown",
            ("EnchantmentInstance", "level"): "level()",
            ("CompoundTag", "putUUID"): "{recv}.store({a0}, UUIDUtil.CODEC, {a1})"}
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "A.java"
        f.write_text("class A {\n    void r() {\n        g.drawString(font, s, 1, 2, -1);\n        drawString(x);\n"
                     "        var t = Minecraft.getInstance().getMainRenderTarget();\n        if (Screen.hasControlDown()) {}\n"
                     "        int l = this.level + e.level;\n        tag.putUUID(\"o\", f(a, b)); x();\n    }\n}\n",
                     encoding="utf-8")
        F = str(f)
        log = "\n".join([f"{F}:3: error: cannot find symbol", "  symbol:   method drawString(Font,String,int,int,int)",
                         "  location: variable g of type GuiGraphicsExtractor",
                         f"{F}:4: error: cannot find symbol", "  symbol:   method drawString(int)", "  location: class A",
                         f"{F}:5: error: cannot find symbol", "  symbol:   method getMainRenderTarget()", "  location: class Minecraft",
                         f"{F}:6: error: cannot find symbol", "  symbol:   method hasControlDown()", "  location: class Screen",
                         f"{F}:7: error: level has private access in EnchantmentInstance",
                         "        int l = this.level + e.level;", "                              ^",
                         f"{F}:8: error: cannot find symbol", "  symbol:   method putUUID(String,UUID)", "  location: variable tag of type CompoundTag"])
        report, missed = run(d, log, rows)
        t = f.read_text(encoding="utf-8")
        ok = ("g.text(font, s, 1, 2, -1);" in t and "        drawString(x);" in t
              and "Minecraft.getInstance().gameRenderer.mainRenderTarget()" in t
              and "if (Minecraft.getInstance().hasControlDown())" in t and "int l = this.level + e.level();" in t
              and 'tag.store("o", UUIDUtil.CODEC, f(a, b)); x();' in t
              and not missed)
    ok = ok and bool(ERR.match(r"D:\a\w\A.java:3: error: cannot find symbol"))   # javac on Windows names a drive path
    print("self-check:", "OK" if ok else f"FAIL {report} {missed}\n{t}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
