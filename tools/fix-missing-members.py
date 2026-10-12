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
replaces the whole call; a template ending in its own call ends with {()} to consume an EMPTY original call, or
{(..)} to consume the original call whatever its arguments -- `{recv}.get(FOOD){(..)}` drops `getFoodProperties(entity)`'s
argument), or `static:<expr>` to replace `Owner.member` / an unqualified
`member` with a fixed expression (`static:Minecraft.getInstance().hasControlDown`).
Standard library only.
"""
import argparse, collections, pathlib, re, sys

ERR = re.compile(r"^(?P<file>(?:[A-Za-z]:)?[\\/]\S+?\.java):(?P<line>\d+): error: cannot find symbol")
# A class that became a record keeps its fields PRIVATE behind same-named accessors (26.2's EnchantmentInstance):
# javac then reports the field read, with the owner, on one line.
PRIV = re.compile(r"^(?P<file>(?:[A-Za-z]:)?[\\/]\S+?\.java):(?P<line>\d+): error: (?P<sym>\w+) has (?:private|protected) access in "
                  r"(?P<owner>[\w.$]+)")


def table(path):
    rows = {}
    for l in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        if not l.strip() or l.lstrip().startswith("#"):
            continue
        owner, old, new = [x.strip() for x in l.split("\t")[:3]]
        if "{recv}" in new and new.endswith(")") and not re.search(r"\{a\d\}|\{\(\)\}|\{\(\.\.\)\}", new):
            # the original call's (...) is kept after a {recv} template, so one ending in its own call yields `f(x)()`
            raise ValueError(f"{path}: row {owner}.{old} -> {new} supplies its own call: end it with {{()}} (X77)")
        rows[(owner, old)] = new
    return rows


def enclosing_class(path, line):
    """Simple name of the innermost class/interface/enum/record declared above `line` in `path` (a close enough
    reading of 'the class this statement is in' for the supertype walk), or None."""
    try:
        lines = pathlib.Path(to_source(path)).read_text(encoding="utf-8", errors="replace").split("\n")[:line]
    except OSError:
        return None
    for l in reversed(lines):
        m = re.search(r"\b(?:class|interface|enum|record)\s+(\w+)", l)
        if m and not l.lstrip().startswith(("//", "*")):
            return m.group(1)
    return None


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
        if not m:
            continue
        caret = lines[i + 2] if i + 2 < len(lines) else ""
        key = (m.group("file"), m.group("line"), caret.index("^") if caret.strip() == "^" else None)
        # Key on the caret too, not just file:line -- gradle's echo of the same error repeats it exactly, while a
        # SECOND missing member on the same line (`c.hasParam(p) && c.getParam(p)`) has its own caret and must be
        # fixed in this pass, not one compile later.
        if key in seen:
            continue
        sym = next((x.strip() for x in lines[i + 1:i + 6] if x.strip().startswith("symbol:")), "")
        loc = next((x.strip() for x in lines[i + 1:i + 7] if x.strip().startswith("location:")), "")
        sm = re.match(r"symbol:\s+(?:method|variable)\s+(\w+)", sym)
        lm = re.match(r"location:\s+(?:variable \w+ of type |class |interface |@interface )?([\w.$]+)", loc)
        owner = lm.group(1).split("<")[0].rsplit(".", 1)[-1] if lm else None
        if sm and not owner:
            # X130: an unqualified or `this.` call gets NO location line from javac; its owner is the enclosing class
            owner = enclosing_class(m.group("file"), int(m.group("line")))
        if not sm or not owner:
            continue
        seen.add(key)
        out.append((m.group("file"), int(m.group("line")), sm.group(1), owner, None))
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
        if "{(..)}" in new:      # the template supplies its own call and DROPS the original's arguments, whatever they are
            args = call_args(line, line.index("(", m.end()))
            if args is None:
                return None
            end = args[1]
            new = new.replace("{(..)}", "")
        elif "{()}" in new:      # the template supplies its own arguments: consume an EMPTY call, keep nothing of it
            args = call_args(line, line.index("(", m.end()))
            if args is None or args[0]:
                return None
            end = args[1]
            new = new.replace("{()}", "")
        elif "{a" in new:          # the call's arguments, split at top-level commas: {a0}, {a1}, ...
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
    if col is not None and new.endswith("()"):   # a field read whose accessor has another name (26.2 Level.random -> getRandom())
        m = re.compile(r"\.\s*(%s)\b(?!\s*\()(?!\s*=[^=])" % re.escape(member)).match(line, col)
        return line[:m.start(1)] + new + line[m.end(1):] if m else None
    out, n = re.subn(r"(?<=\.)%s(?=\s*[\(<])|(?<=\.)%s\b" % (re.escape(member), re.escape(member)), new, line, count=1)
    return out if n else None



# A row names the type that DECLARED the member (`Entity  setSecondsOnFire`), but javac names the receiver's
# static type (`location: variable player of type ServerPlayer`, `class NightmareEntity`). Exact matching
# skipped every call on a subclass (X111), so the owner's supertypes are walked: the mod's own classes from the
# source tree, Minecraft's from the compile jar's class files. Simple names, like the rows.
def _class_supers(data):
    """(super simple name, [interface simple names]) from a .class file's bytes."""
    import struct
    n = struct.unpack(">H", data[8:10])[0]
    pool, i, k = [None] * n, 10, 1
    while k < n:
        tag = data[i]
        if tag == 1:
            ln = struct.unpack(">H", data[i + 1:i + 3])[0]
            pool[k] = data[i + 3:i + 3 + ln].decode("utf-8", "replace"); i += 3 + ln
        elif tag == 7:
            pool[k] = ("C", struct.unpack(">H", data[i + 1:i + 3])[0]); i += 3
        elif tag in (8, 16, 19, 20):
            i += 3
        elif tag == 15:
            i += 4
        elif tag in (3, 4, 9, 10, 11, 12, 17, 18):
            i += 5
        elif tag in (5, 6):
            i += 9; k += 1
        else:
            return None
        k += 1
    def name(ix):
        c = pool[ix]
        return pool[c[1]].rsplit("/", 1)[-1].rsplit("$", 1)[-1] if c else None
    sup = struct.unpack(">H", data[i + 4:i + 6])[0]
    cnt = struct.unpack(">H", data[i + 6:i + 8])[0]
    ifs = [name(struct.unpack(">H", data[i + 8 + 2 * j:i + 10 + 2 * j])[0]) for j in range(cnt)]
    return (name(sup) if sup else None), ifs


def supertypes(src, jars=()):
    """{simple name: [direct supertype simple names]} for the mod's sources and the given jars."""
    import zipfile
    out = collections.defaultdict(list)
    for f in pathlib.Path(src).rglob("*.java"):
        t = f.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"\b(?:class|interface)\s+(\w+)(?:\s*<[^{]*?>)?\s+((?:extends|implements)[^{]*)\{", t):
            for s in re.findall(r"(?:extends|implements|,)\s*([\w.]+)", re.sub(r"<[^<>]*>", "", re.sub(r"<[^<>]*>", "", m.group(2)))):
                out[m.group(1)].append(s.rsplit(".", 1)[-1])
    top, nested = collections.defaultdict(list), collections.defaultdict(list)
    for j in jars:
        try:
            z = zipfile.ZipFile(j)
        except Exception:
            continue
        for e in z.namelist():
            if e.startswith("net/minecraft/") and e.endswith(".class"):
                r = _class_supers(z.read(e))
                if r:
                    base = e.rsplit("/", 1)[-1][:-6]
                    # a nested class shares its simple name with unrelated top-level ones (OutgoingChatMessage$Player):
                    # it only speaks for a name no top-level class has
                    (nested if "$" in base else top)[base.rsplit("$", 1)[-1]] += [s for s in [r[0], *r[1]] if s and s != "Object"]
    for k, v in list(top.items()) + [(k, v) for k, v in nested.items() if k not in top]:
        if k not in out:
            out[k] += v
    return out


def compile_jars(src):
    """The Minecraft jar the build compiles against: NeoGradle's recompile output, or ModDevGradle's merged jar."""
    p = pathlib.Path(src).resolve()
    for d in [p, *p.parents]:
        if (d / "build.gradle").is_file():
            js = sorted(d.glob("build/neoForm/*/steps/recompile/outputs.jar"))
            js += [x for x in sorted(d.glob("build/moddev/artifacts/*merged.jar")) if "sources" not in x.name]
            return js
    return []


def row_for(rows, owner, member, sup):
    """The row for owner.member, or for the nearest supertype that has one (breadth first)."""
    seen, queue = set(), [owner]
    while queue:
        o = queue.pop(0)
        if o in seen:
            continue
        seen.add(o)
        if (o, member) in rows:
            return o, rows[(o, member)]
        queue += sup.get(o, [])
    return None

GEN = "/build/generated/sources/"


def to_source(f):
    """A multi-version build (§W) makes javac report errors against a GENERATED copy
    (build/generated/sources/<overlay>/java). Edit the file it came from: the overlay's own copy if the target
    overrides it, else the shared tree. Without this every site was skipped -- 0 renames in every sweep (X75)."""
    f = f.replace("\\", "/")
    if GEN in f and "/java/" in f.split(GEN, 1)[1]:
        base, tail = f.split(GEN, 1)
        overlay, rest = tail.split("/java/", 1)
        for cand in (f"{base}/src/{overlay}/java/{rest}", f"{base}/src/main/java/{rest}"):
            if pathlib.Path(cand).is_file():
                return cand
    return f


def record_accessors(jars, owners):
    """X134: {owner simple name: {component names}} for the owners that are RECORDS in the compile jar -- a class that
    became a record keeps each field private behind a same-named accessor, so `x.f` is `x.f()` with no row needed."""
    import subprocess, zipfile
    out = {}
    for j in jars:
        try:
            names = zipfile.ZipFile(j).namelist()
        except Exception:
            continue
        for o in owners - set(out):
            hits = [n[:-6].replace("/", ".") for n in names if n.endswith(f"/{o}.class") and n.startswith("net/minecraft/")]
            for fq in hits[:3]:
                try:
                    jp = subprocess.run(["javap", "-cp", str(j), fq], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60).stdout
                except Exception:
                    continue
                if "extends java.lang.Record" in jp:
                    out[o] = set(re.findall(r"public [\w.<>\[\], ?]+ (\w+)\(\);", jp))
                    break
    return out


def run(src, log_text, rows, dry=False, jars=None):
    src = pathlib.Path(src).resolve()
    by_file = collections.defaultdict(list)
    sup = None
    all_sites = sites(log_text)
    # private-field reads with no row: ask the jar whether the owner is a record (one javap per owner, only when needed)
    priv = {(o, m) for f, ln, m, o, col in all_sites if col is not None and (o, m) not in rows}
    if priv:
        rec = record_accessors(compile_jars(src) if jars is None else jars, {o for o, _ in priv})
        rows = dict(rows)
        for o, m in priv:
            if m in rec.get(o, ()):
                rows[(o, m)] = m + "()"
    for f, ln, member, owner, col in all_sites:
        if (owner, member) in rows:
            by_file[pathlib.Path(to_source(f)).resolve()].append((ln, member, owner, rows[(owner, member)], col))
            continue
        if not any(m == member for _o, m in rows):
            continue
        if sup is None:      # only built when some row's member is missing on a type the row does not name
            sup = supertypes(src, compile_jars(src) if jars is None else jars)
        hit = row_for(rows, owner, member, sup)
        if hit:
            by_file[pathlib.Path(to_source(f)).resolve()].append((ln, member, owner, hit[1], col))
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
            ("CompoundTag", "putUUID"): "{recv}.store({a0}, UUIDUtil.CODEC, {a1})",
            ("Minecraft", "getPartialTick"): "{recv}.getTimer().getGameTimeDeltaPartialTick(true){()}"}
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "A.java"
        f.write_text("class A {\n    void r() {\n        g.drawString(font, s, 1, 2, -1);\n        drawString(x);\n"
                     "        var t = Minecraft.getInstance().getMainRenderTarget();\n        if (Screen.hasControlDown()) {}\n"
                     "        int l = this.level + e.level;\n        tag.putUUID(\"o\", f(a, b)); x();\n"
                     "        var p = e.getPosition(mc.getPartialTick());\n    }\n}\n",
                     encoding="utf-8")
        F = str(f)
        log = "\n".join([f"{F}:3: error: cannot find symbol", "  symbol:   method drawString(Font,String,int,int,int)",
                         "  location: variable g of type GuiGraphicsExtractor",
                         f"{F}:4: error: cannot find symbol", "  symbol:   method drawString(int)", "  location: class A",
                         f"{F}:5: error: cannot find symbol", "  symbol:   method getMainRenderTarget()", "  location: class Minecraft",
                         f"{F}:6: error: cannot find symbol", "  symbol:   method hasControlDown()", "  location: class Screen",
                         f"{F}:7: error: level has private access in EnchantmentInstance",
                         "        int l = this.level + e.level;", "                              ^",
                         f"{F}:8: error: cannot find symbol", "  symbol:   method putUUID(String,UUID)", "  location: variable tag of type CompoundTag",
                         f"{F}:9: error: cannot find symbol", "  symbol:   method getPartialTick()", "  location: variable mc of type Minecraft"])
        report, missed = run(d, log, rows)
        t = f.read_text(encoding="utf-8")
        ok = ("g.text(font, s, 1, 2, -1);" in t and "        drawString(x);" in t
              and "Minecraft.getInstance().gameRenderer.mainRenderTarget()" in t
              and "if (Minecraft.getInstance().hasControlDown())" in t and "int l = this.level + e.level();" in t
              and 'tag.store("o", UUIDUtil.CODEC, f(a, b)); x();' in t
              and "e.getPosition(mc.getTimer().getGameTimeDeltaPartialTick(true));" in t
              and not missed)
    with tempfile.TemporaryDirectory() as d:     # an error reported against the GENERATED copy edits the source
        w = pathlib.Path(d); src = w / "src/main/java/p"; gen = w / "build/generated/sources/mc26/java/p"
        src.mkdir(parents=True); gen.mkdir(parents=True)
        for g in (src, gen):
            (g / "B.java").write_text("class B {\n  void r() {\n    var v = facing.getNormal();\n  }\n}\n", encoding="utf-8")
        log = "\n".join([f"{gen / 'B.java'}:3: error: cannot find symbol", "  symbol:   method getNormal()",
                         "  location: variable facing of type Direction"])
        report2, missed2 = run(w / "src/main/java", log, {("Direction", "getNormal"): "getUnitVec3i"})
        ok = ok and "facing.getUnitVec3i()" in (src / "B.java").read_text(encoding="utf-8") and not missed2
    with tempfile.TemporaryDirectory() as d:     # two missing members on ONE line are both fixed in one pass
        f = pathlib.Path(d) / "C.java"
        f.write_text("class C {\n  boolean t() { return c.hasParam(p) && c.getParam(p) != null; }\n}\n", encoding="utf-8")
        line = "  boolean t() { return c.hasParam(p) && c.getParam(p) != null; }"
        log = "\n".join([f"{f}:2: error: cannot find symbol", line, " " * line.index(".hasParam") + "^",
                         "  symbol:   method hasParam(ContextKey<Entity>)", "  location: variable c of type LootContext",
                         f"{f}:2: error: cannot find symbol", line, " " * line.index(".getParam") + "^",
                         "  symbol:   method getParam(ContextKey<Entity>)", "  location: variable c of type LootContext"])
        run(d, log, {("LootContext", "hasParam"): "hasParameter", ("LootContext", "getParam"): "getParameter"})
        ok = ok and "c.hasParameter(p) && c.getParameter(p)" in f.read_text(encoding="utf-8")
    ok = ok and bool(ERR.match(r"D:\a\w\A.java:3: error: cannot find symbol"))   # javac on Windows names a drive path
    with tempfile.TemporaryDirectory() as d:          # a row on the declaring type reaches a subclass receiver (X111)
        f = pathlib.Path(d) / "N.java"
        f.write_text("class N extends Monster {\n  void t() { this.setSecondsOnFire(3); }\n}\n", encoding="utf-8")
        log = (f"{f}:2: error: cannot find symbol\n  void t() {{ this.setSecondsOnFire(3); }}\n"
               "                 ^\n  symbol: method setSecondsOnFire(int)\n  location: class N\n")
        got, _ = run(d, log, {("Entity", "setSecondsOnFire"): "igniteForSeconds"}, jars=[])
        ok &= got.get("N.setSecondsOnFire") is None and "igniteForSeconds" not in f.read_text(encoding="utf-8")   # Monster unknown: no hit
        (pathlib.Path(d) / "Monster.java").write_text("abstract class Monster extends Entity {}\n", encoding="utf-8")
        got, _ = run(d, log, {("Entity", "setSecondsOnFire"): "igniteForSeconds"}, jars=[])
        ok &= sum(got.values()) == 1 and "this.igniteForSeconds(3)" in f.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as d:          # a PROTECTED field whose accessor has another name (X112)
        f = pathlib.Path(d) / "R.java"
        line = "  int t(ServerLevel w) { return w.random.nextInt(w.random.nextInt(3) + 1); }"
        f.write_text("class R {\n" + line + "\n}\n", encoding="utf-8")
        col = line.index(".random", line.index("nextInt("))
        log = "\n".join([f"{f}:2: error: random has protected access in Level", line, " " * col + "^"])
        run(d, log, {("Level", "random"): "getRandom()"}, jars=[])
        ok &= "w.random.nextInt(w.getRandom().nextInt(3) + 1)" in f.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as d:          # X130: a `this.` call has no location line; its owner is the class
        f = pathlib.Path(d) / "Lich.java"
        f.write_text("class Lich extends Mob {\n  void t() { this.teleportToWithTicket(1.0, 2.0, 3.0); }\n}\n", encoding="utf-8")
        (pathlib.Path(d) / "Mob.java").write_text("abstract class Mob extends Entity {}\n", encoding="utf-8")
        log = (f"{f}:2: error: cannot find symbol\n  void t() {{ this.teleportToWithTicket(1.0, 2.0, 3.0); }}\n"
               "                 ^\n  symbol: method teleportToWithTicket(double,double,double)\n")
        got, _ = run(d, log, {("Entity", "teleportToWithTicket"): "teleportTo"}, jars=[])
        ok &= sum(got.values()) == 1 and "this.teleportTo(1.0, 2.0, 3.0)" in f.read_text(encoding="utf-8")
    import shutil, subprocess, zipfile
    if shutil.which("javac") and shutil.which("javap"):
        with tempfile.TemporaryDirectory() as d:      # X134: a record owner needs no row
            src = pathlib.Path(d, "net/minecraft/Cfg.java"); src.parent.mkdir(parents=True)
            src.write_text("package net.minecraft;\npublic record Cfg(int stemProvider) {}\n", encoding="utf-8")
            subprocess.run(["javac", "-d", d, str(src)], capture_output=True)
            jar = pathlib.Path(d, "mc.jar")
            with zipfile.ZipFile(jar, "w") as z:
                z.write(pathlib.Path(d, "net/minecraft/Cfg.class"), "net/minecraft/Cfg.class")
            w = pathlib.Path(d, "w"); w.mkdir()
            f = w / "F.java"
            line = "  int t(Cfg config) { return config.stemProvider + 1; }"
            f.write_text("class F {\n" + line + "\n}\n", encoding="utf-8")
            log = "\n".join([f"{f}:2: error: stemProvider has private access in Cfg", line, " " * line.index(".stemProvider") + "^"])
            got, _ = run(w, log, {}, jars=[jar])
            ok &= "config.stemProvider() + 1" in f.read_text(encoding="utf-8")
    print("self-check:", "OK" if ok else f"FAIL ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
