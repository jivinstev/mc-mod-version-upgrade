#!/usr/bin/env python3
"""The per-file residual loop (issue #27, plan steps 5b/6-7): fix what the recipes left, one small
context per file batch, with the cheapest model that works, and turn repeated fixes into rewrites.

    python3 tools/file-loop.py --work <gradle project> [--first-model sonnet] [--budget 15] [--log run.jsonl]

Each ROUND: compile (javac's error cap lifted) -> group the error files into small batches -> one
headless Claude Code worker per batch, started in a CLEAN context (`claude -p`, no inherited
CLAUDE.md, Read/Edit/Grep/Glob only, no shell) that is given ONLY its files' errors and the
CATALOG.md entries those errors match -> recompile. A worker cannot compile; the loop does.

Model tiers (plan step 6): a batch is worked by --first-model; a file still failing after that goes
to the next tier (haiku -> sonnet -> opus). Every worker's exact dollar cost (Claude Code's own
`total_cost_usd`) and tokens are logged per batch, with the model, so the run answers "which tier
fixed what, for how much".

Propagation (plan step 7, H1): a worker may end with `RULE: <from><TAB><to>` (a rename-table row,
plain / member: / re:). The loop applies each proposed row to the whole tree through
tools/apply-recipes.py, recompiles, and KEEPS it only if the error count fell -- a row that does not
help is reverted, so a bad generalisation costs one compile, never correctness.

Stops at 0 errors, at --budget dollars, at --max-rounds, or when a round fixes nothing.
Standard library only; needs the `claude` CLI on PATH.
"""
import argparse, collections, json, os, pathlib, re, shutil, subprocess, sys, tempfile, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from gitbash import BASH   # noqa: E402
import importlib.util       # noqa: E402

_s = importlib.util.spec_from_file_location("rb", ROOT / "tools/recipe-bench.py")
rb = importlib.util.module_from_spec(_s); _s.loader.exec_module(rb)
MODELS = {"haiku": "claude-haiku-4-5-20251001", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}
TIERS = ["haiku", "sonnet", "opus"]

WORKER = """You are fixing compile errors in a Minecraft mod being ported to {target} (Java 21).
Edit ONLY these files (paths relative to the current directory):
{files}

javac errors in them (file:line: message):
{errors}

CATALOG.md entries these errors match -- each gives the old code shape and its fix:
{entries}

Minecraft/NeoForge sources for checking real signatures: {srcs}
(use Grep/Glob/Read there; never guess an API -- look it up).

Rules:
- Make the smallest change that removes these errors while keeping the code's behaviour. Never stub out
  logic, comment code out, or delete a feature to make an error go away.
- {scope_rule}
- You cannot compile. Reason from the errors and the sources.
Finish with exactly these three lines:
FIXED: <what you changed, one line>
NEEDS: <cross-file changes needed, or none>
RULE: <if one mechanical rewrite would fix this same error wherever it occurs, a rename-table row
`from<TAB>to` (plain name, `member:name`, or `re:<regex>`), else none>"""


def compile_(work, log, heap):
    init = ROOT / "tools/maxerrs.init.gradle"
    with open(log, "w", encoding="utf-8") as fh:
        subprocess.run(["./gradlew", "compileJava", "--console=plain", "--init-script", str(init),
                        "--init-script", str(ROOT / "tools/central-mirror.init.gradle"),
                        f"-Dorg.gradle.jvmargs=-Xmx{heap}"], cwd=work, stdout=fh, stderr=subprocess.STDOUT)
    text = pathlib.Path(log).read_text(encoding="utf-8", errors="replace")
    if "BUILD SUCCESSFUL" in text:
        return 0, []
    r = subprocess.run([BASH, str(ROOT / "tools/burndown-count.sh"), str(log)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:   # a capped, parse-aborted, OOM'd or never-compiled log is not a count
        return None, r.stdout.strip().splitlines()[:3]
    return len(errs := [(rb_rel(f), l, msg) for f, l, msg in rb.parse_errors(text)]), errs


GEN = "/build/generated/sources/"


def rb_rel(f, work=None):
    """A multi-version build (§W) compiles a GENERATED copy; edit the source it came from instead."""
    f = f.replace("\\", "/")
    if GEN in f and "/java/" in f:
        base, tail = f.split(GEN, 1)
        overlay, rest = tail.split("/java/", 1)
        for cand in (f"{base}/src/{overlay}/java/{rest}", f"{base}/src/main/java/{rest}"):
            if pathlib.Path(cand).is_file():
                return cand
    return f


def entry_texts(msgs, sigs, entries, cap=2500, most=4):
    hit = collections.Counter()
    for m in msgs:
        best = None
        for ident, rx, spec in sigs:
            if rx.search(m) and (best is None or spec > best[1]):
                best = (ident, spec)
        if best:
            hit[best[0]] += 1
    out = []
    for ident, _n in hit.most_common(most):
        body = entries.get(ident, "")
        out.append(f"--- §{ident}\n{body[:cap]}" + (" [...]" if len(body) > cap else ""))
    return "\n".join(out) or "(none matched -- these are not in the catalogue yet)", list(hit)


def batches(errs, size, max_errs):
    by = collections.defaultdict(list)
    for f, l, m in errs:
        by[f].append((l, m))
    files = sorted(by, key=lambda f: -len(by[f]))
    out, cur, n = [], [], 0
    for f in files:   # big files alone; small files share a worker, up to `size` files / `max_errs` errors
        k = len(by[f])
        if cur and (len(cur) >= size or n + k > max_errs):
            out.append(cur); cur, n = [], 0
        cur.append(f); n += k
    if cur:
        out.append(cur)
    return out, by


def run_worker(work, files, by, sigs, entries, model, target, srcs, timeout, subsystem=False):
    rel = lambda f: os.path.relpath(f, work) if os.path.isabs(f) else f
    errors = "\n".join(f"{rel(f)}:{l}: {m}" for f in files for l, m in sorted(by[f])[:60])
    ent, ids = entry_texts([m for f in files for _l, m in by[f]], sigs, entries)
    scope_rule = ("These errors belong to one subsystem that needs coordinated changes across files: you MAY "
                  "edit or add any file under src/main/java to finish it (keep each change minimal)."
                  if subsystem else
                  "If a fix needs a change in a file not listed, do not edit it: describe it under NEEDS.")
    prompt = WORKER.format(scope_rule=scope_rule, target=target, files="\n".join("  " + rel(f) for f in files), errors=errors,
                           entries=ent, srcs=srcs or "(not available)")
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD"}
    t0 = time.time()
    extra = ["--add-dir", srcs] if srcs else []
    r = subprocess.run(["claude", "-p", prompt, "--model", MODELS[model], "--output-format", "json",
                        "--permission-mode", "acceptEdits", "--allowedTools", "Read,Edit,Write,Grep,Glob", *extra],
                       cwd=work, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    try:
        d = json.loads(r.stdout)
    except ValueError:
        d = {"result": (r.stdout + r.stderr)[-500:], "total_cost_usd": 0, "is_error": True}
    res = d.get("result") or ""
    rule = re.search(r'^RULE:\s*(.+)$', res, re.M)
    rule = rule.group(1).strip() if rule and not rule.group(1).strip().lower().startswith("none") else None
    if rule:   # workers write prose: keep the first `from<TAB>to` row, with a literal <TAB> or an arrow allowed
        rule = rule.split("`, `")[0].strip("` ").replace("<TAB>", "\t")
        rule = re.split(r'\s+(?:\(|\||;|--|—)', rule)[0] if "\t" in rule else rule
    return {"model": model, "files": [rel(f) for f in files], "errors_in": sum(len(by[f]) for f in files),
            "entries": ids, "usd": d.get("total_cost_usd") or 0, "usage": d.get("usage"),
            "turns": d.get("num_turns"), "secs": round(time.time() - t0), "is_error": d.get("is_error"),
            "rule": rule, "result": res[-600:]}


def try_rule(work, src, row, n_before, log, heap, n_rule):
    """Apply one proposed row tree-wide; keep it only if the error count falls."""
    if "\t" not in row:
        row = re.sub(r'\s{2,}|\s*->\s*|\s*→\s*', "\t", row, count=1)
    if "\t" not in row:
        return False, "no TAB in the row"
    snap = pathlib.Path(tempfile.mkdtemp()) / "src"
    shutil.copytree(src, snap)
    pack = pathlib.Path(tempfile.mkdtemp()) / "p.recipes.tsv"
    pack.write_text(f"#@ X{n_rule} auto  proposed by a worker\n{row}\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "tools/apply-recipes.py"), "--src", str(src), "--recipes", str(pack)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or " 0 file(s) rewritten" in r.stdout:
        shutil.rmtree(src); shutil.copytree(snap, src)
        return False, (r.stdout + r.stderr).strip().splitlines()[-1:] or ["no change"]
    n, _e = compile_(work, log, heap)
    if n is None or n >= n_before:
        shutil.rmtree(src); shutil.copytree(snap, src)
        return False, f"compile {n} >= {n_before}: reverted"
    return True, f"{n_before} -> {n}"


PROBE_MSG = ("this method no longer overrides anything on the target, so it compiles but is never called "
             "(the supertype's signature changed). Find the current method in the sources, change this "
             "signature to match and add @Override; leave it alone only if it never meant to override")


def probe(work):
    cmd = (f"./gradlew compileJava --console=plain --init-script {ROOT / 'tools/central-mirror.init.gradle'}")
    r = subprocess.run([sys.executable, str(ROOT / "tools/override-probe.py"), str(work), "--compile", cmd],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    hits = re.findall(r'^\s+(\S+\.java):(\d+)\s+(.+)$', r.stdout, re.M)
    return [(str(work / f), int(l), f"{sig.strip()}: {PROBE_MSG}") for f, l, sig in hits], r.returncode


def probe_round(a, work, sigs, entries, note):
    """At 0 errors: dead overrides become the work list for one more round, then re-probe."""
    hits, rc = probe(work)
    note(event="probe", hits=len(hits), rc=rc)
    if not hits:
        return 0.0
    groups, by = batches(hits, a.batch_files, a.batch_errors)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(a.parallel) as ex:
        res = list(ex.map(lambda f: run_worker(work, f, by, sigs, entries, a.first_model, a.target, a.sources,
                                               a.timeout), groups))
    spent = 0.0
    for r_ in res:
        spent += r_["usd"]; note(event="worker", round="probe", **r_)
    n, errs = compile_(work, work / "file-loop-compile.log", a.heap)
    hits2, _rc = probe(work) if n == 0 else ([], None)
    note(event="probe-after", errors=n, hits=len(hits2), spent_probe=round(spent, 4))
    return spent


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work", required=True, help="the Gradle project (src/main/java is edited in place)")
    ap.add_argument("--first-model", default="sonnet", choices=TIERS)
    ap.add_argument("--max-model", default="opus", choices=TIERS)
    ap.add_argument("--budget", type=float, default=15.0, help="stop when workers have spent this many dollars")
    ap.add_argument("--max-rounds", type=int, default=8)
    ap.add_argument("--batch-files", type=int, default=4)
    ap.add_argument("--batch-errors", type=int, default=40)
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--target", default="NeoForge 1.21.1")
    ap.add_argument("--sources", help="a directory of Minecraft/NeoForge sources workers may grep")
    ap.add_argument("--heap", default="6g")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--log", default="file-loop.jsonl")
    ap.add_argument("--subsystem", action="store_true",
                    help="workers may edit any file (for cross-file rewrites the per-file rounds cannot finish: "
                         "capabilities, networking); use with a large --batch-files")
    ap.add_argument("--no-probe", action="store_true",
                    help="skip the override probe at 0 errors (dead overrides compile clean; catalogue R-class)")
    a = ap.parse_args()
    work = pathlib.Path(a.work).resolve(); src = work / "src/main/java"
    logf = open(a.log, "a", encoding="utf-8")
    note = lambda **k: (logf.write(json.dumps(k) + "\n"), logf.flush(), print(json.dumps({x: k[x] for x in k if x not in ("result", "usage")})[:300]))
    sigs = rb.signatures()
    entries = {i: b for i, b in rb.catalogue_entries((ROOT / "CATALOG.md").read_text(encoding="utf-8"))}
    clog = work / "file-loop-compile.log"
    n, errs = compile_(work, clog, a.heap)
    note(event="start", errors=n)
    if n is None:
        print("the start does not compile to a count:", errs); return 2
    spent, tier_of, rules = 0.0, {}, 0
    lo, hi = TIERS.index(a.first_model), TIERS.index(a.max_model)
    for rnd in range(1, a.max_rounds + 1):
        if n == 0 or spent >= a.budget:
            break
        groups, by = batches(errs, a.batch_files, a.batch_errors)
        jobs = []
        for files in groups:   # a batch runs at the highest tier any of its files has reached
            t = max(tier_of.get(f, lo) for f in files)
            if t > hi:
                continue
            jobs.append((files, TIERS[t]))
        if not jobs:
            break
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(a.parallel) as ex:
            futs = [ex.submit(run_worker, work, f, by, sigs, entries, m, a.target, a.sources, a.timeout, a.subsystem) for f, m in jobs]
            results = [fu.result() for fu in futs]
        for res in results:
            spent += res["usd"]; note(event="worker", round=rnd, **res)
        before = n
        n, errs = compile_(work, clog, a.heap)
        note(event="compile", round=rnd, errors=n, spent=round(spent, 4))
        if n is None:
            print("compile no longer counts:", errs); return 3
        still = {f for f, _l, _m in errs}
        for files, m in jobs:   # a file still failing moves up a tier
            for f in files:
                if f in still:
                    tier_of[f] = max(tier_of.get(f, lo), TIERS.index(m)) + 1
        for res in results:
            if res["rule"] and n:
                rules += 1
                ok, why = try_rule(work, src, res["rule"], n, clog, a.heap, rules)
                if ok:
                    n, errs = compile_(work, clog, a.heap)
                note(event="rule", round=rnd, row=res["rule"], kept=ok, why=str(why), errors=n)
        if n >= before:
            note(event="plateau", round=rnd, errors=n); break
    if n == 0 and not a.no_probe and spent < a.budget:
        spent += probe_round(a, work, sigs, entries, note)
    by_model = collections.defaultdict(lambda: [0, 0.0])
    for l in open(a.log, encoding="utf-8"):
        e = json.loads(l)
        if e.get("event") == "worker":
            by_model[e["model"]][0] += 1; by_model[e["model"]][1] += e["usd"]
    note(event="end", errors=n, spent=round(spent, 4), by_model={k: [v[0], round(v[1], 4)] for k, v in by_model.items()})
    return 0 if n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
