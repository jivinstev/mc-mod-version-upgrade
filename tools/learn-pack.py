#!/usr/bin/env python3
"""Propose recipe-pack rows from a finished hop: what the workers changed the same way, again and again.

    python3 tools/learn-pack.py --work mods/<modid> --hop hop2 [--sources <target platform sources>]
                                [--min-count 3] [--out learned.tsv]

tools/port.py snapshots each hop's tree right after its deterministic rewrites (mods/<modid>/hop-start/
<hop>/). Everything between that snapshot and the finished src/main/java is what the workers had to do.
The part of it that recurs -- one identifier replaced by another in many places -- is a rename row the
next port of the same hop should get for free. That is the pack growing from use, by script (H1 measured
65% of fixes repeating an earlier shape; rows proposed by WORKERS mostly matched nothing, docs/EVALS.md,
so the rows here come from the diff itself).

Rules for a row:
  - the same old -> new identifier replacement in at least --min-count places, in at least 2 files;
  - the old identifier is gone from the finished tree (a partial rename is a judgement, not a rule);
  - BOTH names are platform names: the new one appears in the target's Minecraft/NeoForge sources, and
    neither is declared by the mod itself. This keeps mod names out of a pack, which is public.
A name after a `.` becomes a `member:` row, a capitalised one a plain type row (the rename-table kinds of
templates/multi-version/README.md). Rows are written as one `#@ learned-<hop> auto` group, ready to append
to the hop's pack, with the counts as comments. They are PROPOSALS: bench them (tools/recipe-bench.py, a
row is kept only if it removes errors and grows no error family, §X9) before they land, and open the PR
only when the person agrees. Standard library only.
"""
import argparse, collections, difflib, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
IDENT = re.compile(r"[A-Za-z_$][\w$]*")
TOKEN = re.compile(r"[A-Za-z_$][\w$]*|\S")
DECL = re.compile(r"\b(?:class|interface|enum|record)\s+([A-Za-z_]\w*)|\b[\w<>\[\],? ]+\s+([a-z_]\w*)\s*\(")
JAVA_WORDS = set("""abstract assert boolean break byte case catch char class const continue default do double else enum
extends final finally float for goto if implements import instanceof int interface long native new package private
protected public return short static strictfp super switch synchronized this throw throws transient try void
volatile while var record yield true false null String Object""".split())


def files(tree):
    return {f.relative_to(tree).as_posix(): f for f in pathlib.Path(tree).rglob("*.java")}


def replacements(old_text, new_text):
    """(old identifier, new identifier, preceded by '.') for each single-token identifier swap."""
    out = []
    a, b = old_text.splitlines(), new_text.splitlines()
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op != "replace" or (i2 - i1) != (j2 - j1):
            continue
        for la, lb in zip(a[i1:i2], b[j1:j2]):
            ta, tb = TOKEN.findall(la), TOKEN.findall(lb)
            for op2, k1, k2, m1, m2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
                if op2 == "replace" and k2 - k1 == 1 and m2 - m1 == 1:
                    o, n = ta[k1], tb[m1]
                    if IDENT.fullmatch(o) and IDENT.fullmatch(n) and o not in JAVA_WORDS and n not in JAVA_WORDS:
                        out.append((o, n, k1 > 0 and ta[k1 - 1] == "."))
    return out


def platform_names(sources, cap_files=60000):
    names = set()
    if not sources:
        return names
    for i, f in enumerate(pathlib.Path(sources).rglob("*.java")):
        if i >= cap_files:
            break
        names.update(IDENT.findall(f.read_text(encoding="utf-8", errors="replace")))
    return names


def mod_declared(tree):
    out = set()
    for f in pathlib.Path(tree).rglob("*.java"):
        for m in DECL.finditer(f.read_text(encoding="utf-8", errors="replace")):
            out.add(m.group(1) or m.group(2))
    out.discard(None)
    return out


def propose(start, end, platform, min_count=3):
    s_files, e_files = files(start), files(end)
    count, where = collections.Counter(), collections.defaultdict(set)
    for rel, f in s_files.items():
        if rel not in e_files:
            continue
        for o, n, member in replacements(f.read_text(encoding="utf-8", errors="replace"),
                                         e_files[rel].read_text(encoding="utf-8", errors="replace")):
            count[(o, n, member)] += 1; where[(o, n, member)].add(rel)
    end_idents = collections.Counter()
    for f in e_files.values():
        end_idents.update(IDENT.findall(f.read_text(encoding="utf-8", errors="replace")))
    own = mod_declared(end) | mod_declared(start)
    rows, why_not = [], collections.Counter()
    for (o, n, member), c in count.most_common():
        if c < min_count or len(where[(o, n, member)]) < 2:
            why_not["too few"] += 1; continue
        if end_idents[o]:
            why_not["old name still used"] += 1; continue
        if o in own or n in own:
            why_not["a name the mod declares"] += 1; continue
        if platform and n not in platform:
            why_not["new name not in the platform sources"] += 1; continue
        kind = "member" if member else ("type" if n[:1].isupper() else "member")
        rows.append({"from": o, "to": n, "kind": kind, "count": c, "files": len(where[(o, n, member)])})
    return rows, why_not


def as_tsv(rows, hop):
    out = [f"#@ learned-{hop} auto", f"# proposed by tools/learn-pack.py from a finished {hop}; bench before merging (§X9)"]
    for r in rows:
        out.append(f"# {r['count']} sites in {r['files']} files")
        out.append(f"{'member:' if r['kind'] == 'member' else ''}{r['from']}\t{r['to']}")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work"); ap.add_argument("--hop", default="hop1")
    ap.add_argument("--start"); ap.add_argument("--end"); ap.add_argument("--sources")
    ap.add_argument("--min-count", type=int, default=3); ap.add_argument("--out")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if a.work:
        work = pathlib.Path(a.work)
        a.start = a.start or str(work / "hop-start" / a.hop)
        a.end = a.end or str(work / "src/main/java")
        if not a.sources:
            s = importlib_file_loop().find_sources(work)
            a.sources = str(s) if s else None
    if not a.start or not a.end or not pathlib.Path(a.start).is_dir():
        print("learn-pack: need the hop's start snapshot (mods/<modid>/hop-start/<hop>, written by tools/port.py)")
        return 2
    if not a.sources:
        print("learn-pack: no platform sources found; refusing to propose rows that could carry mod names")
        return 2
    rows, why_not = propose(a.start, a.end, platform_names(a.sources), a.min_count)
    out = pathlib.Path(a.out) if a.out else pathlib.Path(a.end).parent.parent.parent / f"learned-{a.hop}.tsv"
    out.write_text(as_tsv(rows, a.hop), encoding="utf-8")
    print(f"learn-pack: {len(rows)} proposed row(s) -> {out}; rejected: {dict(why_not)}")
    for r in rows[:15]:
        print(f"  {r['count']:4d}x {r['files']:3d} files  {('member:' if r['kind'] == 'member' else '') + r['from']} -> {r['to']}")
    return 0


def importlib_file_loop():
    import importlib.util
    s = importlib.util.spec_from_file_location("fl", ROOT / "tools/file-loop.py")
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
    return m


def self_check():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        for side in ("s", "e", "plat"):
            (d / side).mkdir()
        old = "class A{{ void f(){{ x.getFoo(); ResourceLocation r; y.getFoo(); }} }}\n"
        for i in range(3):
            (d / f"s/F{i}.java").write_text(old.replace("{{", "{").replace("}}", "}"), encoding="utf-8")
            (d / f"e/F{i}.java").write_text(old.replace("{{", "{").replace("}}", "}").replace("getFoo", "foo")
                                          .replace("ResourceLocation", "Identifier"), encoding="utf-8")
        (d / "s/Mine.java").write_text("class Mine { void own(){ Helper.go(); } }\n", encoding="utf-8")
        (d / "e/Mine.java").write_text("class Mine { void own(){ Helper2.go(); } }\n", encoding="utf-8")
        (d / "plat/P.java").write_text("class P { int foo(){return 0;} Identifier id; Helper2 h; }\n", encoding="utf-8")
        rows, why = propose(d / "s", d / "e", platform_names(d / "plat"), min_count=3)
        got = {(r["from"], r["to"], r["kind"]) for r in rows}
        ok = ("getFoo", "foo", "member") in got and ("ResourceLocation", "Identifier", "type") in got
        ok &= not any(r["from"] == "Helper" for r in rows)            # one site: never a rule
        t = as_tsv(rows, "hop2")
        ok &= t.startswith("#@ learned-hop2 auto") and "member:getFoo\tfoo" in t
        rows2, _ = propose(d / "s", d / "e", {"foo"}, min_count=3)     # Identifier not in the platform: dropped
        ok &= {r["to"] for r in rows2} == {"foo"}
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
