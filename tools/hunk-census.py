#!/usr/bin/env python3
"""Hunk census: diff a port's START tree against its finished END tree, and attribute every changed hunk
to the catalogue entry it most likely applies -- or mark it UNATTRIBUTED. Runs no model and no build.

    python3 tools/hunk-census.py --start <java-root> --end <java-root> [--json out.json] [--top 15]
    python3 tools/hunk-census.py --self-check

WHY (issue #27, Stage 2): the compile bench counts what is still broken; this counts what the port
actually CHANGED, which is the frequency table recipes should be written against. Citation counts in
migration notes cannot do this: they skew towards process lessons, and most compile fixes went in uncited.

HOW AN ENTRY IS DETECTED
    Each entry's **Pattern:** and **Error:** text names the OLD code shape, its **Fix:** text the NEW one.
    The code-shaped identifiers in their backtick fragments (CamelCase, dotted, underscored, digits) become
    the entry's old-side and new-side sets; an identifier on both sides says nothing and is dropped. Each
    identifier is weighted 1/(number of entries naming it), so `ResourceLocation` (named everywhere) counts
    for less than `getOrCreateTag`.
    A hunk's REMOVED identifiers are those on its `-` lines and not its `+` lines; ADDED the reverse. An
    entry scores the weight of its old-side ids among the removed plus its new-side ids among the added,
    and must hit BOTH sides when it has both (a rename is a removal AND an addition). The best score wins.

WHAT IS NOT ATTRIBUTED, ON PURPOSE
    comment-only hunks (counted, not attributed), whole new or deleted files (overlays, compat pairs, dropped classes), and hunks over --bulk lines (a
    regenerated model or a CFR-recovered method), reported as their own categories. Everything else that
    matches no entry is UNATTRIBUTED and grouped by its most specific removed -> added identifiers: a
    cluster that recurs across ports is a catalogue entry that does not exist yet.

The output names identifiers from the trees you point it at, so run it on the private side; only the
per-entry counts are fit to publish.
EXIT  0 measured   1 bad arguments   3 self-check failed
"""
import argparse, collections, importlib.util, json, pathlib, re, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
_s = importlib.util.spec_from_file_location("rb", ROOT / "tools/recipe-bench.py")
rb = importlib.util.module_from_spec(_s)
_s.loader.exec_module(rb)

FIELD = re.compile(r'\*\*(Pattern|Error|Fix)[^*]*:\*\*(.*?)(?=\*\*(?:Pattern|Error|Fix|Runtime|Symptom|Why|Scan|Gate)\b[^*]*:\*\*|$)', re.S)
IDENT = re.compile(r'[A-Za-z_$][\w$]*')
ARROW = re.compile(r'`([^`]+)`\s*\**\s*(?:→|->|⇒)\s*\**\s*`([^`]+)`')
COMMON = set("""String Object Integer Boolean Double Float Long List Map Set Optional Override Nullable
Supplier Function Consumer Predicate Stream Collectors Arrays Collections HashMap ArrayList Math System
Exception RuntimeException Class Void Iterable Iterator UUID""".split())


def code_shaped(t):
    if len(t) < 4 or t in COMMON or t in rb.JAVAC_WORDS:
        return False
    return bool(re.search(r'[A-Z]', t[1:]) or '_' in t or re.search(r'\d', t) or t[0].isupper())


STRING_OR_COMMENT = re.compile(r'"(?:\\.|[^"\\])*"|/\*.*?\*/|//[^\n]*', re.S)


def code(text):
    """Java with comments removed (string literals kept: they carry registry ids). Also drops hunk
    lines that are the inside of a block comment, which a -U0 hunk shows without its /* opener."""
    text = STRING_OR_COMMENT.sub(lambda m: m.group(0) if m.group(0).startswith('"') else " ", text)
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith(("*", "/*")))


def norm(text):
    return re.sub(r'\s+', '', text)


def ids(text, is_code=False):
    return {t for t in IDENT.findall(code(text) if is_code else text) if code_shaped(t)}


def detectors(text=None, exclude=()):
    """-> {entry: (old_ids, new_ids)}, {ident: weight}. `exclude`: entry-id prefixes out of scope for
    this port (the catalogue has no machine-readable scope yet; Stage 3 adds one)."""
    text = text if text is not None else rb.CATALOG.read_text(encoding="utf-8")
    det = {}
    for ident, body in rb.catalogue_entries(text):
        # §W (build architecture) and §X (instrument hygiene) cite API renames only as EXAMPLES; as
        # detectors they out-score the entry that actually describes the change (W1 claimed V4's hunks).
        if re.match(r'[WX]\d', ident) or any(re.match(rf'{x}\d', ident) for x in exclude):
            continue
        flat = re.sub(r'\s+', ' ', body)
        old, new = set(), set()
        for kind, field in FIELD.findall(flat):
            for frag in rb.TICKS.findall(field):
                (new if kind == "Fix" else old).update(ids(frag))
        # prose renames: "`A` → `B`" anywhere in the entry (the §V/§G clusters are written this way)
        for a_, b_ in ARROW.findall(flat):
            old.update(ids(a_)); new.update(ids(b_))
        both = old & new
        old, new = old - both, new - both
        if old or new:
            det[ident] = (old, new)
    df = collections.Counter(i for o, n in det.values() for i in o | n)
    return det, {i: 1.0 / c for i, c in df.items()}


GIT_HEADER = re.compile(r'^diff --git (?:"a/(?:[^"\\]|\\.)*"|a/.*?) (?:"b/((?:[^"\\]|\\.)*)"|b/(.*))$')


def header_path(line):
    """The b/ path of a `diff --git` header. git QUOTES a path holding a backslash, a quote or (by default)
    any non-ASCII byte -- `"b/C:\\Users\\...\\A.java"` -- and C-escapes it; a naive split then yields a
    name ending in `"`, not `.java`, and every hunk of that file is silently dropped."""
    m = GIT_HEADER.match(line)
    if not m:
        return line.split(" b/")[-1]
    if m.group(1) is None:
        return m.group(2)
    raw = m.group(1).encode("latin-1", "backslashreplace").decode("unicode_escape")   # \\ \" \t \303\251
    return raw.encode("latin-1", "replace").decode("utf-8", "replace")


def hunks(start, end):
    """-> [(file, kind, minus_text, plus_text, nlines)] with kind in hunk/new-file/deleted-file."""
    # / paths: git quotes any path with a backslash in it ("b/C:\\Users\\...\\A.java"), and a quoted
    # header line ends in `"`, not `.java` -- so on Windows every hunk was dropped
    r = subprocess.run(["git", "diff", "--no-index", "-U0", "--no-color", "--",
                        pathlib.Path(start).as_posix(), pathlib.Path(end).as_posix()],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out, cur, kind, minus, plus, n = [], None, "hunk", [], [], 0

    def flush():
        if cur is not None and (minus or plus):
            out.append((cur, kind, "\n".join(minus), "\n".join(plus), n))

    for l in r.stdout.splitlines():
        if l.startswith("diff --git"):
            flush(); cur, kind, minus, plus, n = header_path(l), "hunk", [], [], 0
        elif l.startswith("new file mode"):
            kind = "new-file"
        elif l.startswith("deleted file mode"):
            kind = "deleted-file"
        elif l.startswith("@@"):
            if kind == "hunk":
                flush(); minus, plus, n = [], [], 0
        elif l.startswith(("---", "+++")):
            continue
        elif l.startswith("-"):
            minus.append(l[1:]); n += 1
        elif l.startswith("+"):
            plus.append(l[1:]); n += 1
    flush()
    return out


def attribute(minus, plus, det, w, gone=None):
    """gone: identifiers absent from the whole END tree. Removing an identifier the finished port still
    uses everywhere (`Attribute`, `getCapability`) is not evidence of a removed API, so on its own it
    attributes nothing; it still counts alongside a new-side hit (a signature change keeps the name)."""
    m, p = ids(minus, True), ids(plus, True)
    removed, added = m - p, p - m
    gone = removed if gone is None else gone
    best = None
    for ent, rx in SHAPES:   # whole families no identifier list can enumerate
        if any(rx.fullmatch(t) for t in removed):
            return ent, removed, added
    for ent, (old, new) in det.items():
        hit_old = old & removed
        so_gone = sum(w[i] for i in hit_old & gone)
        sn = sum(w[i] for i in new & added)
        # The OLD side identifies a migration when what it names is GONE from the finished tree; the new
        # side often names something too common to weigh (`Supplier`), so then it only breaks ties.
        # Otherwise the entry needs a new-side hit, plus an old-side one if it has an old side.
        # A new side alone is enough when it names something specific (an id at most two entries
        # name): `EntityType.ZOMBIE` -> `EntityTypes.ZOMBIE` removes nothing that is gone.
        if not (so_gone or (sn and (hit_old or not old or sn >= 0.5))):
            continue
        s = so_gone + sn + 0.5 * sum(w[i] for i in hit_old - gone)
        if best is None or s > best[1]:
            best = (ent, s)
    return best[0] if best else None, removed, added


# Identifier SHAPES that are a catalogue entry by themselves: unmapped SRG names (§A #1 / §I #54) and
# intermediary names (§P #146). Checked before the per-identifier detectors.
SHAPES = [("1", re.compile(r'[mf]_\d+_')), ("146", re.compile(r'(class|method|field)_\d+'))]


TOKEN = re.compile(r'[A-Za-z_$][\w$]*|\d[\w.]*|"(?:\\.|[^"\\])*"|\S')


def reshape_key(minus, plus):
    """Same identifiers on both sides: the change is in the SHAPE (a cast dropped, a field read become
    a call, arguments reordered). Key on the first differing token run, with literals blanked."""
    import difflib
    ta = [("\"…\"" if t.startswith('"') else t) for t in TOKEN.findall(code(minus))]
    tb = [("\"…\"" if t.startswith('"') else t) for t in TOKEN.findall(code(plus))]
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
        if op != "equal":
            ctx = ta[max(0, i1 - 2):i1]
            return f"{' '.join(ctx)} [{' '.join(ta[i1:i2])[:40]}] => [{' '.join(tb[j1:j2])[:40]}]"
    return "(identical after normalising)"


def cluster_key(removed, added):
    pick = lambda s: ",".join(sorted(sorted(s, key=lambda t: (-len(t), t))[:2]))
    return f"{pick(removed) or '-'} -> {pick(added) or '-'}"


def shape(minus, plus, keep):
    """A hunk with its incidental names blanked: identifiers outside `keep` (the removed/added API) become
    ID, literals LIT. Two hunks with the same shape are the same edit at different sites, so one written
    fix (or one generated rewrite) covers both."""
    def one(text):
        out = []
        for t in TOKEN.findall(code(text)):
            if t.startswith('"') or t[0].isdigit():
                out.append("LIT")
            elif IDENT.fullmatch(t) and t not in keep and code_shaped(t):
                out.append("ID")
            else:
                out.append(t)
        return " ".join(out)
    return one(minus) + " => " + one(plus)


def census(start, end, bulk=300, det=None, w=None, exclude=(), records=None):
    """records: a list to append one dict per hunk to (file, kind, lines, entry/cluster, removed, added,
    shape) -- the input for the hypothesis checks. It names identifiers from the trees: private side only."""
    if det is None:
        det, w = detectors(exclude=exclude)
    res = {"hunks": 0, "attributed": 0, "new_files": 0, "deleted_files": 0, "bulk_hunks": 0,
           "unattributed": 0, "comment_only": 0, "by_entry": collections.Counter(), "clusters": collections.Counter()}
    end_ids = set()
    for f in pathlib.Path(end).rglob("*.java"):
        end_ids |= ids(f.read_text(encoding="utf-8", errors="replace"), is_code=True)
    root = pathlib.Path(end).as_posix().lstrip("/") + "/"   # matches git's b/ path on every OS
    imp_cache = {}

    def imports(f):   # the import roots (first two segments) of the file, end side else start side
        if f not in imp_cache:
            src = pathlib.Path(end, f) if pathlib.Path(end, f).exists() else pathlib.Path(start, f)
            txt = src.read_text(encoding="utf-8", errors="replace") if src.exists() else ""
            imp_cache[f] = sorted({".".join(m.split(".")[:2]) for m in re.findall(r'^import\s+(?:static\s+)?([\w.]+)', txt, re.M)})
        return imp_cache[f]

    rec = lambda **k: records.append(dict(k, imports=imports(k["file"]))) if records is not None else None
    for _f, kind, minus, plus, n in hunks(start, end):
        if not _f.endswith(".java"):
            continue
        f = _f[len(root):] if _f.startswith(root) else _f
        if kind == "new-file":
            res["new_files"] += 1; rec(file=f, cat="new-file", lines=n); continue
        if kind == "deleted-file":
            res["deleted_files"] += 1; rec(file=f, cat="deleted-file", lines=n); continue
        res["hunks"] += 1
        if n > bulk:
            res["bulk_hunks"] += 1; rec(file=f, cat="bulk", lines=n); continue
        if norm(code(minus)) == norm(code(plus)):
            res["comment_only"] += 1; rec(file=f, cat="comment", lines=n); continue
        ent, removed, added = attribute(minus, plus, det, w, gone=ids(minus, True) - end_ids)
        if ent:
            res["attributed"] += 1; res["by_entry"][ent] += 1; key = ent; cat = "entry"
        elif removed or added:
            res["unattributed"] += 1; key = cluster_key(removed, added); res["clusters"][key] += 1; cat = "cluster"
        else:
            res["unattributed"] += 1; key = "reshape: " + reshape_key(minus, plus); res["clusters"][key] += 1; cat = "cluster"
        if records is not None:
            rec(file=f, cat=cat, key=key, lines=n, removed=sorted(removed), added=sorted(added),
                shape=shape(minus, plus, removed | added))
    return res


def self_check():
    cat = ("## B.\n"
           "9. **Holder** · **Pattern:** `RegistryObject<Item> FOO` · **Error:** `cannot find symbol: class RegistryObject` "
           "· **Fix:** `DeferredHolder<Item, Item> FOO`\n"
           "10. **Keys** · **Pattern:** `ForgeRegistries.ITEMS` · **Error:** x · **Fix:** `Registries.ITEM`\n"
           "33. **Vanish** · **Pattern:** `implements Vanishable` · **Error:** `cannot find symbol: class Vanishable` "
           "· **Fix:** remove it\n"
           "V17. **Small renames** `Minecraft.setScreen` → `setScreenAndShow`, and more.\n")
    det, w = detectors(cat)
    with tempfile.TemporaryDirectory() as t:
        a, b = pathlib.Path(t, "a"), pathlib.Path(t, "b")
        for d in (a, b):
            d.mkdir()
        (a / "A.java").write_text("class A {\n RegistryObject<Item> FOO = R.reg();\n int x;\n}\n", encoding="utf-8")
        (b / "A.java").write_text("class A {\n DeferredHolder<Item, Item> FOO = R.reg();\n int x;\n}\n", encoding="utf-8")
        (a / "B.java").write_text("class B extends Item implements Vanishable {\n}\n", encoding="utf-8")
        (b / "B.java").write_text("class B extends Item {\n}\n", encoding="utf-8")
        (a / "C.java").write_text("class C {\n void tickOld() {}\n}\n", encoding="utf-8")
        (b / "C.java").write_text("class C {\n void tickRenamedHook() {}\n}\n", encoding="utf-8")
        (b / "D.java").write_text("class D {}\n", encoding="utf-8")
        (a / "E.java").write_text("class E {\n void f() { mc.setScreen(s); }\n int keep;\n // old note\n}\n", encoding="utf-8")
        (b / "E.java").write_text("class E {\n void f() { mc.setScreenAndShow(s); }\n int keep;\n // a new, longer note\n}\n", encoding="utf-8")
        r = census(a, b, det=det, w=w)
    ok = (dict(r["by_entry"]) == {"9": 1, "33": 1, "V17": 1} and r["unattributed"] == 1 and r["new_files"] == 1
          and r["comment_only"] == 1
          and "tickOld -> tickRenamedHook" in r["clusters"])
    ok = ok and reshape_key("if (level.isClientSide) x();", "if (level.isClientSide()) x();") == ". isClientSide [] => [( )]"
    ok = ok and attribute("x.m_91087_();", "x.getInstance();", det, w)[0] == "1"
    ok = ok and header_path('diff --git "a/C:\\\\t\\\\a/A.java" "b/C:\\\\t\\\\b/A.java"') == "C:\\t\\b/A.java"
    ok = ok and header_path('diff --git a/x/a/A.java b/x/b/A.java') == "x/b/A.java"
    print("self-check:", "PASS" if ok else f"FAIL {json.dumps(r, default=dict)}")
    real, _ = detectors()
    print(f"catalogue: {len(real)} entries with a detector")
    return 0 if ok and real else 3


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--start"); ap.add_argument("--end"); ap.add_argument("--json")
    ap.add_argument("--bulk", type=int, default=300); ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--hunks-out", help="write one JSON line per hunk (file, category, removed/added ids, "
                    "normalised shape): the hypothesis checks' input. Names identifiers -- keep it private")
    ap.add_argument("--exclude", default="", help="comma-separated entry-id prefixes out of scope, e.g. V "
                    "for a 1.20 -> 1.21 port (the §V entries are the 1.21 -> 26.x era jump)")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not (a.start and a.end):
        ap.error("--start and --end are required")
    recs = [] if a.hunks_out else None
    r = census(pathlib.Path(a.start).resolve(), pathlib.Path(a.end).resolve(), a.bulk,
               exclude=tuple(x for x in a.exclude.split(",") if x), records=recs)
    if a.hunks_out:
        with open(a.hunks_out, "w", encoding="utf-8") as fh:
            for x in recs:
                fh.write(json.dumps(x, sort_keys=True) + "\n")
    h = r["hunks"]
    print(f"hunks {h}: attributed {r['attributed']} ({100 * r['attributed'] // max(h, 1)}%), "
          f"unattributed {r['unattributed']}, comment-only {r['comment_only']}, bulk {r['bulk_hunks']}; files new {r['new_files']}, deleted {r['deleted_files']}")
    print(f"entries hit: {len(r['by_entry'])}")
    for k, v in r["by_entry"].most_common(a.top):
        print(f"  {v:6d}  {k}")
    print("top unattributed clusters:")
    for k, v in r["clusters"].most_common(a.top):
        print(f"  {v:6d}  {k[:110]}")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(r, indent=1, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
