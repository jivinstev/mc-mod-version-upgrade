#!/usr/bin/env python3
"""Apply a recipe pack to a Java source tree: the deterministic half of a port, with no model.

    python3 tools/apply-recipes.py --src src/main/java --recipes tools/recipes/<pack>.recipes.tsv [--dry-run]

A RECIPE PACK is a rename table in exactly the format templates/multi-version/tools/prepare-sources.py
reads (plain / member: / re: rows, `#!exhaustive` blocks, every guard from catalogue §X), with one
addition: `#@` lines group the rows under the CATALOGUE ENTRY they implement, and say what kind of
recipe the group is. Because every addition is a comment, a pack is still a valid rename table.

    #@ V4 auto  ResourceLocation became Identifier
    net.minecraft.resources.ResourceLocation	net.minecraft.resources.Identifier
    ResourceLocation	Identifier

    #@ V39 choice  Entity.moveTo split into two families
    #? detect	(?<![\\w$])moveTo\\(
    #? option	snapTo	same five overloads; what moveTo did (the usual answer)
    #? option	absSnapTo	clamps to the world border; double overloads only

    #@ 16 manual  SimpleChannel -> payloads: rewrite by hand from the catalogue entry
    #? detect	SimpleChannel

Kinds (Stage 3 of issue #27; measured in docs/EVALS.md, H3):
  auto     the rows are applied, in place, by the prepare-sources engine. For an entry whose real
           fixes are one edit (V4 is 88% one shape, 25 is 95%).
  choice   nothing is rewritten. Each site the `detect` regex finds is listed with the options, so
           the model PICKS one instead of working the fix out (for 38 of 84 frequent entries three
           options cover 80%+ of real fixes).
  manual   nothing is rewritten. The sites are listed with the entry id, so the model reads that
           one entry rather than searching the catalogue.

Output is bounded: per group, a count and the first --sites sites. Every auto group that rewrote
nothing is reported as DEAD (the §X1 detector, per entry). Standard library only.
"""
import argparse, collections, importlib.util, json, os, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
_s = importlib.util.spec_from_file_location("ps", ROOT / "templates/multi-version/tools/prepare-sources.py")
ps = importlib.util.module_from_spec(_s); _s.loader.exec_module(ps)

GROUP = re.compile(r'^#@\s+(\S+)\s+(auto|choice|manual)\b\s*(.*)$')
META = re.compile(r'^#\?\s+(detect|option)\t(.*)$')


def load_pack(path):
    """-> [group dict]: id, kind, title, rows (from<TAB>to, as prepare-sources reads them), detect, options."""
    groups, cur = [], None
    for lineno, raw in enumerate(pathlib.Path(path).read_text(encoding="utf-8").splitlines(), 1):
        m = GROUP.match(raw)
        if m:
            cur = {"id": m.group(1), "kind": m.group(2), "title": m.group(3).strip(), "line": lineno,
                   "rows": [], "detect": None, "options": []}
            groups.append(cur)
            continue
        m = META.match(raw)
        if m:
            if cur is None:
                sys.exit(f"{path}:{lineno}: '#?' line before any '#@' group")
            if m.group(1) == "detect":
                cur["detect"] = re.compile(m.group(2))
            else:
                cur["options"].append(m.group(2).split("\t", 1))
            continue
        if raw.strip() and not raw.lstrip().startswith("#") and "\t" in raw:
            if cur is None:
                sys.exit(f"{path}:{lineno}: rule before any '#@' group -- say which catalogue entry it implements")
            if cur["kind"] != "auto":
                sys.exit(f"{path}:{lineno}: a {cur['kind']} group cannot carry rewrite rows -- "
                         "only `#? detect`/`#? option` lines")
            cur["rows"].append(raw)
    for g in groups:
        if g["kind"] != "auto" and g["detect"] is None:
            sys.exit(f"{path}:{g['line']}: {g['kind']} group {g['id']} has no `#? detect` line")
        if g["kind"] == "choice" and len(g["options"]) < 2:
            sys.exit(f"{path}:{g['line']}: choice group {g['id']} needs 2+ `#? option` lines")
    return groups


def apply(src, pack, dry_run=False, max_sites=5):
    src = pathlib.Path(src)
    # prepare-sources' own loader does the validation (duplicates, identity rules, exhaustive blocks)
    renames = ps.load_renames(str(pack))
    tokens, regexes = ps.split_rules(renames)
    pattern, table = ps.build_pattern(tokens)
    owner = {}   # rule text -> group id
    for g in load_pack(pack):
        for row in g["rows"]:
            frm = row.split("\t", 1)[0].strip()
            owner[frm[3:] if frm.startswith("re:") else
                  (r'(?<=\.)' + re.escape(frm[len("member:"):]) + r'\b' if frm.startswith("member:") else frm)] = g["id"]
    groups = load_pack(pack)
    hits, sites = collections.Counter(), collections.defaultdict(list)
    files_changed = 0
    for full, rel in ps.java_files(str(src)):
        rel = pathlib.PurePath(rel).as_posix()
        text = before = open(full, encoding="utf-8", errors="replace").read()
        for g in groups:   # choice/manual: find, do not touch -- on the text BEFORE any rewrite
            if g["kind"] != "auto":
                for m in g["detect"].finditer(before):
                    sites[g["id"]].append(f"{rel}:{before.count(chr(10), 0, m.start()) + 1}")
        for rx, repl, _ex in regexes:
            text, n = rx.subn(repl, text)
            hits[owner.get(rx.pattern)] += n
        if pattern is not None:
            def tok(m):
                hits[owner.get(m.group(1))] += 1
                return table[m.group(1)]
            text = pattern.sub(tok, text)
        if text != before:
            files_changed += 1
            if not dry_run:
                with open(full, "w", encoding="utf-8") as fh:
                    fh.write(text)
    report = {"files_changed": files_changed, "groups": []}
    for g in groups:
        r = {"id": g["id"], "kind": g["kind"], "title": g["title"]}
        if g["kind"] == "auto":
            r["rewrites"] = hits.get(g["id"], 0)
        else:
            r["sites"] = len(sites[g["id"]]); r["first"] = sites[g["id"]][:max_sites]
            if g["kind"] == "choice":
                r["options"] = [o[0] for o in g["options"]]
        report["groups"].append(r)
    return report, groups


def render(report, groups):
    out = [f"apply-recipes: {report['files_changed']} file(s) rewritten"]
    auto = [r for r in report["groups"] if r["kind"] == "auto"]
    live = [r for r in auto if r["rewrites"]]
    dead = [r for r in auto if not r["rewrites"]]
    out.append(f"auto: {sum(r['rewrites'] for r in live)} rewrites from {len(live)} of {len(auto)} group(s)")
    out += [f"  {r['rewrites']:6d}  §{r['id']}  {r['title']}"[:140] for r in sorted(live, key=lambda r: -r["rewrites"])[:12]]
    if dead:
        out.append(f"  dead (matched nothing here): {' '.join('§' + r['id'] for r in dead)}")
    opts = {g["id"]: g["options"] for g in groups}
    for kind, head in (("choice", "CHOICES -- pick one option per site (the catalogue entry says how):"),
                       ("manual", "MANUAL -- fix by hand; read only these catalogue entries:")):
        rs = [r for r in report["groups"] if r["kind"] == kind and r["sites"]]
        if not rs:
            continue
        out.append(head)
        for r in sorted(rs, key=lambda r: -r["sites"]):
            out.append(f"  §{r['id']}  {r['sites']} site(s)  {r['title']}"[:140])
            if kind == "choice":
                out += [f"      option {o[0]}: {o[1] if len(o) > 1 else ''}"[:140] for o in opts[r["id"]]]
            out.append("      at " + ", ".join(r["first"]) + (" ..." if r["sites"] > len(r["first"]) else ""))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src"); ap.add_argument("--recipes")
    ap.add_argument("--dry-run", action="store_true", help="report, do not write")
    ap.add_argument("--sites", type=int, default=5, help="sites listed per choice/manual group")
    ap.add_argument("--json", help="also write the full report here")
    ap.add_argument("--check-pack", action="store_true",
                    help="validate --recipes only: it parses, and every '#@' id is a CATALOG.md entry")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if a.check_pack:
        return check_pack(a.recipes)
    if not (a.src and a.recipes):
        ap.error("--src and --recipes are required")
    report, groups = apply(a.src, a.recipes, a.dry_run, a.sites)
    print("\n".join(render(report, groups)))
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 0


def check_pack(pack):
    _r = importlib.util.spec_from_file_location("rb", ROOT / "tools/recipe-bench.py")
    rb = importlib.util.module_from_spec(_r); _r.loader.exec_module(rb)
    ids = {i.split("#")[0] for i, _b in rb.catalogue_entries((ROOT / "CATALOG.md").read_text(encoding="utf-8"))}
    ps.load_renames(str(pack))   # exits on a malformed, duplicate or identity row
    groups = load_pack(pack)
    unknown = [g["id"] for g in groups if g["id"] not in ids]
    if unknown:
        print(f"{pack}: '#@' ids that are not CATALOG.md entries: {' '.join(unknown)}"); return 1
    print(f"{pack}: {len(groups)} groups ({sum(g['kind'] == 'auto' for g in groups)} auto), every id a catalogue entry")
    return 0


def self_check():
    import tempfile
    pack = ("# a test pack\n"
            "#@ V4 auto  RL became Identifier\n"
            "net.minecraft.resources.ResourceLocation\tnet.minecraft.resources.Identifier\n"
            "ResourceLocation\tIdentifier\n"
            "#@ V17 auto  a member rename\n"
            "member:serverLevel\tlevel\n"
            "#@ 99 auto  matches nothing\n"
            "NoSuchThing\tOtherThing\n"
            "#@ V39 choice  moveTo split\n"
            "#? detect\t(?<![\\w$])moveTo\\(\n"
            "#? option\tsnapTo\twhat moveTo did\n"
            "#? option\tabsSnapTo\tclamps to the border\n"
            "#@ 16 manual  networking by hand\n"
            "#? detect\tSimpleChannel\n")
    java = ("package a;\nimport net.minecraft.resources.ResourceLocation;\n"
            "class A {\n  ResourceLocation r = ResourceLocation.parse(\"x:y\");\n"
            "  void f(P p) { p.serverLevel(); e.moveTo(1, 2, 3); }\n  SimpleChannel c;\n}\n")
    with tempfile.TemporaryDirectory() as t:
        src = pathlib.Path(t, "java/a"); src.mkdir(parents=True)
        (src / "A.java").write_text(java, encoding="utf-8")
        pk = pathlib.Path(t, "p.recipes.tsv"); pk.write_text(pack, encoding="utf-8")
        report, groups = apply(pathlib.Path(t, "java"), pk)
        out = (src / "A.java").read_text(encoding="utf-8")
        text = "\n".join(render(report, groups))
        g = {r["id"]: r for r in report["groups"]}
        ok = ("import net.minecraft.resources.Identifier;" in out and "Identifier.parse" in out
              and "p.level()" in out and "e.moveTo(1, 2, 3)" in out   # choice sites are NOT rewritten
              and g["V4"]["rewrites"] == 3 and g["V17"]["rewrites"] == 1 and g["99"]["rewrites"] == 0
              and g["V39"]["sites"] == 1 and g["V39"]["first"] == ["a/A.java:5"] and g["16"]["first"] == ["a/A.java:6"]
              and "dead (matched nothing here): §99" in text and "option snapTo" in text)
        # a rewrite row inside a choice group is refused, not silently applied
        bad = pathlib.Path(t, "bad.tsv"); bad.write_text("#@ 1 choice x\n#? detect\tx\nA\tB\n", encoding="utf-8")
        try:
            load_pack(bad); ok = False
        except SystemExit:
            pass
    print("self-check:", "OK" if ok else "FAIL\n" + text + "\n" + out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
