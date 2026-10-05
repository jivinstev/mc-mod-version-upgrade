#!/usr/bin/env python3
"""Recipe bench: apply recipes to a port's START tree, compile it against the target, and say what is
left -- bucketed by catalogue entry -- and how far it still is from the finished port. Runs no model.

    python3 tools/recipe-bench.py --start <java-root> --scaffold <gradle-project> --work <dir> \
        [--end <java-root>] [--recipes <file>] [--mc <target>] [--init-script <file>]... \
        [--blank <rel-path>]... [--json out.json]

    python3 tools/recipe-bench.py --bucket-log <gradle.log> [--json out.json]   # bucket an existing log
    python3 tools/recipe-bench.py --self-check                                  # A/B the bucketer, offline

WHY (issue #27, Stage 1): a finished port gives two snapshots -- the deterministic decompile it started
from, and the gate-passing tree it ended as. That pair is an oracle. Applying recipes to the start and
compiling costs no tokens, so recipes can be measured across every port as often as we like.

STEPS
    1. copy --scaffold to --work (minus build/, run/, .gradle/ and src/main/java), then put --start in as
       src/main/java. --blank empties a scaffold path (e.g. a §W port's own rename table and overlay dir,
       which ARE recipes and must be off for a baseline).
    2. apply --recipes: one `<id><TAB><shell command>` per line, run in the work dir with $SRC set to the
       java root ($MIGRATOR = this repo). Each recipe's effect is measured; a recipe that changed nothing is reported DEAD (§X1),
       and one that leaves the tree failing to parse shows up as a PARSE ABORT, never as a count (§X5b).
    3. `./gradlew compileJava --console=plain` with javac's error cap lifted (-Xmaxerrs; §"Parallel agent
       orchestration": the capped count understated the work ~5x). Read with tools/burndown-count.sh,
       which refuses to print a number for a build that never compiled (§X5/X5b/X5c/X13).
    4. bucket every unique error by the catalogue entry whose **Error:** signature it matches; the rest go
       to `unmatched`, grouped by message family (the input Stage 2 turns into new entries).
    5. with --end: diff the recipe-applied tree against the finished port (files and lines still to change).

The catalogue's **Error:** fragments are matched literally, with `X`/`…` as wildcards and ` / ` as
alternatives. A fragment that names no code identifier (`incompatible types`, `does not override`) is too
generic to attribute anything and is ignored, so `unmatched` is honest rather than flattered.

EXIT CODES  0 measured   1 bad arguments   2 not a count (burndown-count refused)   3 self-check failed
No third-party source is read or written by this tool beyond the trees you point it at; it is meant to
run on the PRIVATE side, against snapshots that never enter this repository.
"""
import argparse, collections, hashlib, json, os, pathlib, re, shutil, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
CATALOG = ROOT / "CATALOG.md"
BURNDOWN = ROOT / "tools/burndown-count.sh"

# --- 1. catalogue signatures ------------------------------------------------------------------------
ENTRY_RES = [  # the same id shapes tools/check-catalog-fidelity.py recognises
    re.compile(r'^#{2,4}\s+(?:§\s*)?([A-Z]{1,2}\d*[a-z]?)[.)]\s', re.M),
    re.compile(r'^\*\*([A-Z]{1,2}\d+[a-z]?)\.', re.M),
    re.compile(r'^- \*\*([A-Z]{1,2}\d+[a-z]?)\.', re.M),
    re.compile(r'^([A-Z]{1,2}\d*[a-z]?)\.\s', re.M),
    re.compile(r'^(\d{1,3}[a-z]?)\.\s+\*\*', re.M),
]
# javac's own words; a fragment made only of these (plus wildcards) attributes nothing.
JAVAC_WORDS = set("""cannot find symbol class method variable package does not exist incompatible types
cannot be converted to is not abstract and override abstract in a an the of on from for with required found
reason be applied given has private access protected expected not statement illegal start expression
constructor no suitable interface enum record type types argument arguments wrong number cannot infer
type-variable s t r e invalid reference lambda parameter return compatible final variable assign value
missing location symbol other this that it may mismatch error x""".split())
ERR_FIELD = re.compile(r'\*\*Error[^*]*:\*\*(.*?)(?=\*\*(?:Fix|Runtime|Pattern|Symptom|Why)\b|$)', re.S)
TICKS = re.compile(r'`([^`]+)`')
SYMBOL_HEAD = re.compile(r'^(cannot find symbol:\s*(?:class|method|variable|package)?)\s*')


def catalogue_entries(text):
    marks = sorted({(m.start(), m.group(1)) for rx in ENTRY_RES for m in rx.finditer(text)})
    out, seen = [], {}
    for i, (pos, ident) in enumerate(marks):
        if i and marks[i - 1][0] == pos:
            continue
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        seen[ident] = seen.get(ident, 0) + 1
        out.append((ident if seen[ident] == 1 else f"{ident}#{seen[ident]}", text[pos:end]))
    return out


def informative(frag):
    """Does this fragment name something code-shaped (so a match means something)?"""
    for tok in re.findall(r"[A-Za-z_$][\w$.]*", frag):
        t = tok.strip('.')
        if t.lower() in JAVAC_WORDS or len(t) < 3:
            continue
        if re.search(r'[A-Z]', t[1:]) or '.' in t or '_' in t or re.match(r'[A-Z]', t) or re.search(r'\d', t):
            return True
    return False


def frag_regex(frag):
    """`cannot find symbol: method getTag/X` -> a regex over our normalised error text."""
    parts, out = re.split(r'(\bX\b|…|\.\.\.|\*)', frag.strip()), ""
    for p in parts:
        if p in ("X", "…", "...", "*"):
            out += r"[\w$.<>?, ]*?"
        else:
            out += re.sub(r'\\ ', r'\\s+', re.escape(p))
    if re.match(r'[\w$]', frag):
        out = r'(?<![\w$])' + out
    if re.search(r'[\w$]$', frag):
        out += r'(?![\w$])'
    return out


def signatures(text=None):
    """-> [(entry_id, compiled_regex, specificity)]"""
    text = text if text is not None else CATALOG.read_text(encoding="utf-8")
    sigs = []
    for ident, body in catalogue_entries(text):
        flat = re.sub(r'\s+', ' ', body)
        for field in ERR_FIELD.findall(flat):
            for frag in TICKS.findall(field):
                frag = frag.strip()
                alts = [a.strip() for a in re.split(r'\s+/\s+', frag) if a.strip()]
                head = SYMBOL_HEAD.match(alts[0]) if alts else None
                for j, alt in enumerate(alts):
                    if j and head and re.fullmatch(r'[\w$.()<>]+', alt):   # "…: class A / B / C"
                        alt = "cannot find symbol: " + alt
                    # javac's symbol KIND is noise to the catalogue (it writes "class X", "X", "method X")
                    alt = SYMBOL_HEAD.sub("cannot find symbol: X ", alt) if alt.startswith("cannot find symbol") else alt
                    if not informative(alt):
                        continue
                    try:
                        sigs.append((ident, re.compile(frag_regex(alt)), len(alt)))
                    except re.error:
                        pass
    return sigs


# --- 2. reading a gradle/javac log ------------------------------------------------------------------
ERR_LINE = re.compile(r'^(\S+\.java):(\d+): error: (.*)$')


def parse_errors(log_text):
    """-> unique [(file, line, normalised message)] -- `symbol:` folded in, gradle's echo removed."""
    lines, out, seen = log_text.splitlines(), [], set()
    for i, l in enumerate(lines):
        m = ERR_LINE.match(l)
        if not m:
            continue
        msg = m.group(3).strip()
        for k in range(i + 1, min(i + 6, len(lines))):
            if ERR_LINE.match(lines[k]):
                break
            s = re.match(r'^\s*symbol:\s+(.*)$', lines[k])
            if s:
                msg += ": " + s.group(1).strip()
                break
            r = re.match(r'^\s*(required|found|reason):\s+(.*)$', lines[k])
            if r:
                msg += f" [{r.group(1)}: {r.group(2).strip()}]"
        key = (m.group(1), m.group(2))
        if key in seen:
            continue
        seen.add(key)
        out.append((m.group(1), int(m.group(2)), re.sub(r'\s+', ' ', msg)))
    return out


def family(msg):
    """Group unmatched errors: keep javac's words and the symbol KIND, drop the names."""
    m = re.match(r'(cannot find symbol): (\w+) ([\w$]+)', msg)
    if m:
        return f"{m.group(1)}: {m.group(2)} {m.group(3)}"   # the name IS the family for a missing symbol
    m = re.match(r'package ([\w.]+) does not exist', msg)
    if m:
        return "package " + ".".join(m.group(1).split(".")[:3]) + ".* does not exist"
    return re.sub(r'[A-Z][\w$.]*(<[^ ]*>)?', 'T', msg.split(" [")[0])[:90]


def bucket(errors, sigs):
    by_entry, unmatched = collections.Counter(), collections.Counter()
    examples = {}
    for _f, _l, msg in errors:
        best = None
        for ident, rx, spec in sigs:
            if rx.search(msg) and (best is None or spec > best[1]):
                best = (ident, spec)
        if best:
            by_entry[best[0]] += 1
            examples.setdefault(best[0], msg[:120])
        else:
            unmatched[family(msg)] += 1
    return by_entry, unmatched, examples


# --- 3. the bench -----------------------------------------------------------------------------------
SKIP = {"build", "run", ".gradle", ".git"}
MAXERRS_INIT = """allprojects { tasks.withType(JavaCompile).configureEach {
    options.compilerArgs += ['-Xmaxerrs', '100000', '-Xmaxwarns', '0'] } }
"""


def tree_hash(root):
    h = hashlib.sha1()
    for p in sorted(pathlib.Path(root).rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).encode()); h.update(p.read_bytes())
    return h.hexdigest()


def copy_scaffold(scaffold, work, blanks):
    if work.exists():
        shutil.rmtree(work)
    def ignore(d, names):
        rel = pathlib.Path(d).relative_to(scaffold)
        return [n for n in names if (rel == pathlib.Path(".") and n in SKIP)
                or (rel == pathlib.Path("src/main") and n == "java")]
    shutil.copytree(scaffold, work, ignore=ignore, symlinks=True)
    for b in blanks:
        t = work / b
        if t.is_dir():
            shutil.rmtree(t); t.mkdir(parents=True)
        elif t.exists():
            t.write_text("")


def apply_recipes(work, src, recipes_file):
    report = []
    if not recipes_file:
        return report
    for n, line in enumerate(pathlib.Path(recipes_file).read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        rid, _, cmd = line.partition("\t")
        before = tree_hash(src)
        r = subprocess.run(cmd, shell=True, cwd=work, env={**os.environ, "SRC": str(src), "MIGRATOR": str(ROOT)},
                           capture_output=True, text=True)
        changed = tree_hash(src) != before
        report.append({"id": rid.strip(), "exit": r.returncode, "changed": changed})
    return report


def diff_stat(a, b):
    r = subprocess.run(["git", "diff", "--no-index", "--numstat", "--", str(a), str(b)],
                       capture_output=True, text=True)
    files = add = rem = 0
    for l in r.stdout.splitlines():
        p = l.split("\t")
        if len(p) >= 3:
            files += 1
            add += int(p[0]) if p[0].isdigit() else 0
            rem += int(p[1]) if p[1].isdigit() else 0
    return {"files": files, "added": add, "removed": rem}


def count(log):
    r = subprocess.run(["bash", str(BURNDOWN), str(log)], capture_output=True, text=True)
    m = re.search(r'errors = (\d+)', r.stdout)
    return r.returncode, (int(m.group(1)) if m else None), r.stdout.strip().splitlines()[:4]


def report(res, top):
    print(f"errors = {res['errors']}  (burndown exit {res['burndown_exit']})")
    if res.get("recipes"):
        dead = [r["id"] for r in res["recipes"] if not r["changed"]]
        print(f"recipes: {len(res['recipes'])} run, {len(dead)} dead" + (f": {', '.join(dead[:10])}" if dead else ""))
    if res.get("buckets") is not None:
        att = sum(res["buckets"].values())
        print(f"attributed {att} / {res['errors']} to {len(res['buckets'])} catalogue entries")
        for k, v in sorted(res["buckets"].items(), key=lambda x: -x[1])[:top]:
            print(f"  {v:6d}  {k}")
        print(f"unmatched families: {len(res['unmatched'])}")
        for k, v in sorted(res["unmatched"].items(), key=lambda x: -x[1])[:top]:
            print(f"  {v:6d}  {k}")
    if res.get("diff_vs_end"):
        d = res["diff_vs_end"]
        print(f"vs end: {d['files']} files differ, +{d['added']} -{d['removed']} lines")


def self_check():
    """Offline A/B: a synthetic catalogue and log, with the answer known in advance."""
    cat = ("## B. Loader\n"
           "5. **Mod bus** · **Pattern:** p · **Error:** `cannot find symbol: class FooLoadingContext` · **Fix:** f\n"
           "6. **Pkg** · **Pattern:** p · **Error:** `package net.oldloader.X does not exist` · **Fix:** f\n"
           "7. **Alts** · **Pattern:** p · **Error:** `cannot find symbol: class AlphaChannel / BetaDirection` · **Fix:** f\n"
           "8. **Generic** · **Pattern:** p · **Error:** `incompatible types` · **Fix:** f\n")
    log = ("/w/A.java:3: error: cannot find symbol\n  x\n  ^\n  symbol:   class FooLoadingContext\n"
           "/w/A.java:3: error: cannot find symbol\n  symbol:   class FooLoadingContext\n"     # gradle echo
           "/w/B.java:1: error: package net.oldloader.common does not exist\n"
           "/w/C.java:9: error: cannot find symbol\n  symbol:   class BetaDirection\n"
           "/w/D.java:2: error: incompatible types: int cannot be converted to String\n"
           "/w/E.java:4: error: cannot find symbol\n  symbol:   class FooLoadingContextual\n")
    sigs = signatures(cat)
    b, u, _ = bucket(parse_errors(log), sigs)
    want = {"5": 1, "6": 1, "7": 1}
    ok = dict(b) == want and sum(u.values()) == 2 and not any(s[0] == "8" for s in sigs)
    print("self-check:", "PASS" if ok else f"FAIL buckets={dict(b)} unmatched={dict(u)}")
    real = signatures()
    print(f"catalogue: {len(real)} signatures from {len({s[0] for s in real})} entries")
    return 0 if ok and real else 3


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--start"); ap.add_argument("--end"); ap.add_argument("--scaffold"); ap.add_argument("--work")
    ap.add_argument("--recipes"); ap.add_argument("--mc")
    ap.add_argument("--init-script", action="append", default=[])
    ap.add_argument("--blank", action="append", default=[])
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--bucket-log"); ap.add_argument("--json"); ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()

    res = {}
    if a.bucket_log:
        log = pathlib.Path(a.bucket_log)
    else:
        if not (a.start and a.scaffold and a.work):
            ap.error("--start, --scaffold and --work are required (or --bucket-log / --self-check)")
        start, scaffold, work = (pathlib.Path(p).resolve() for p in (a.start, a.scaffold, a.work))
        copy_scaffold(scaffold, work, a.blank)
        src = work / "src/main/java"
        shutil.copytree(start, src)
        res["recipes"] = apply_recipes(work, src, a.recipes)
        init = work / "recipe-bench-maxerrs.init.gradle"
        init.write_text(MAXERRS_INIT)
        cmd = ["./gradlew", "compileJava", "--console=plain", "--init-script", str(init)]
        for s in a.init_script:
            cmd += ["--init-script", str(pathlib.Path(s).resolve())]
        if a.mc:
            cmd.append(f"-Pmc={a.mc}")
        log = work / "recipe-bench-compile.log"
        with open(log, "w") as fh:
            try:
                subprocess.run(cmd, cwd=work, stdout=fh, stderr=subprocess.STDOUT, timeout=a.timeout)
            except subprocess.TimeoutExpired:
                fh.write("\nrecipe-bench: TIMEOUT\n")
        if a.end:
            res["diff_vs_end"] = diff_stat(src, pathlib.Path(a.end).resolve())

    code, n, msg = count(log)
    res.update(burndown_exit=code, errors=n)
    if code != 0:
        res["not_a_count"] = msg
        print("\n".join(msg))
    else:
        b, u, ex = bucket(parse_errors(log.read_text(errors="replace")), signatures())
        res.update(buckets=dict(b), unmatched=dict(u), examples=ex)
    report(res, a.top)
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(res, indent=1, sort_keys=True))
    return 0 if code == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
