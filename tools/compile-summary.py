#!/usr/bin/env python3
"""A bounded summary of a compile log, for the build-error loop to read instead of the raw log.

    ./gradlew compileJava --console=plain > /tmp/build.log 2>&1
    python3 tools/compile-summary.py /tmp/build.log                 # ~35 lines, whatever the log size
    python3 tools/compile-summary.py /tmp/build.log --file a/b/C.java   # every error in one file

Why: in the measured ports (docs/EVALS.md, Stage 0) a model's raw tool output -- grep dumps of
thousands of error lines, re-read on every later request -- was 7-24% of the whole port's cost.
This prints the same decisions in a fixed size:

* the count, only after tools/burndown-count.sh accepts the log (a capped, parse-aborted, OOM'd or
  never-compiled log is NOT a count: it says why and exits with burndown's code);
* errors grouped by CATALOG.md entry (so you read only those entries) and the rest by family;
* the files with the most errors, in the order a per-file loop takes them;
* the change since the previous run on the same log path, with any GROWN group listed first --
  a rule that put errors back (§X9) shows up even when the total fell.

State is kept beside the log (<log>.prev.json), so successive passes on /tmp/build.log compare.
Standard library only.
"""
import argparse, collections, importlib.util, json, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from gitbash import BASH   # Git Bash on Windows, where plain "bash" is WSL's launcher

_s = importlib.util.spec_from_file_location("rb", ROOT / "tools/recipe-bench.py")
rb = importlib.util.module_from_spec(_s); _s.loader.exec_module(rb)


def rel(f):
    """Path below the java source root, / separated (javac on Windows writes \\)."""
    return f.replace("\\", "/").rsplit("/java/", 1)[-1]


def validate(log):
    r = subprocess.run([BASH, str(ROOT / "tools/burndown-count.sh"), str(log)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout.strip().splitlines()


def what_went_wrong(text, n=15):
    lines = text.splitlines()
    i = next((k for k, l in enumerate(lines) if "What went wrong" in l), None)
    return lines[i:i + n] if i is not None else lines[-n:]


def summarise(log_text, sigs):
    errs = [(rel(f), l, m) for f, l, m in rb.parse_errors(log_text)]
    groups, example = collections.Counter(), {}
    for f, l, msg in errs:
        best = None
        for ident, rx, spec in sigs:
            if rx.search(msg) and (best is None or spec > best[1]):
                best = (ident, spec)
        g = f"§{best[0]}" if best else rb.family(msg)
        groups[g] += 1
        example.setdefault(g, f"{f}:{l}: {msg}")
    files = collections.Counter(f for f, _l, _m in errs)
    return {"total": len(errs), "groups": dict(groups), "files": dict(files), "example": example}


def delta(cur, prev, key):
    return {k: cur[key].get(k, 0) - prev.get(key, {}).get(k, 0)
            for k in set(cur[key]) | set(prev.get(key, {}))}


def render(s, prev, top_groups, top_files, width=150):
    out = []
    d_total = f"  (previous run: {prev['total']}, {s['total'] - prev['total']:+d})" if prev else ""
    out.append(f"{s['total']} errors in {len(s['files'])} files{d_total}")
    if prev:
        dg = delta(s, prev, "groups")
        grown = sorted((k for k, v in dg.items() if v > 0 and prev["groups"].get(k, 0) > 0), key=lambda k: -dg[k])
        new = sorted((k for k, v in dg.items() if v > 0 and prev["groups"].get(k, 0) == 0), key=lambda k: -dg[k])
        gone = [k for k, v in dg.items() if v < 0 and s["groups"].get(k, 0) == 0]
        if grown:
            out.append("GREW since the previous run (did the last change put these back?):")
            out += [f"  {dg[k]:+5d}  {k}"[:width] for k in grown[:5]]
        if new:
            out.append("NEW groups (often the next layer a fix unmasked):")
            out += [f"  {dg[k]:+5d}  {k}"[:width] for k in new[:5]]
        if gone:
            out.append(f"cleared: {len(gone)} group(s)")
    ents = sorted(((k, v) for k, v in s["groups"].items() if k.startswith("§")), key=lambda kv: -kv[1])
    fams = sorted(((k, v) for k, v in s["groups"].items() if not k.startswith("§")), key=lambda kv: -kv[1])
    if ents:
        ids = " ".join(k[1:] for k, _ in ents[:top_groups])
        out.append(f"By CATALOG.md entry ({sum(v for _, v in ents)} errors) -- read only these entries: {ids}")
        out += [f"  {v:5d}  {k:6} e.g. {s['example'][k]}"[:width] for k, v in ents[:top_groups]]
        if len(ents) > top_groups:
            out.append(f"  ... {len(ents) - top_groups} more entries, {sum(v for _, v in ents[top_groups:])} errors")
    if fams:
        out.append(f"Not in the catalogue ({sum(v for _, v in fams)} errors), by family:")
        out += [f"  {v:5d}  {k}"[:width] for k, v in fams[:top_groups]]
        if len(fams) > top_groups:
            out.append(f"  ... {len(fams) - top_groups} more families, {sum(v for _, v in fams[top_groups:])} errors")
    fl = sorted(s["files"].items(), key=lambda kv: (-kv[1], kv[0]))
    out.append("Files with the most errors (`--file <path>` lists one file's errors):")
    out += [f"  {v:5d}  {k}"[:width] for k, v in fl[:top_files]]
    if len(fl) > top_files:
        out.append(f"  ... {len(fl) - top_files} more files")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("log", nargs="?")
    ap.add_argument("--file", help="print every error in this file (a path below the java root, or a suffix of one)")
    ap.add_argument("--top", type=int, default=8, help="groups shown per section")
    ap.add_argument("--files", type=int, default=8, help="files shown")
    ap.add_argument("--state", help="where the previous run is kept (default <log>.prev.json)")
    ap.add_argument("--no-state", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not a.log:
        ap.error("a log path is required")
    log = pathlib.Path(a.log)
    if not log.is_file():
        print(f"no such log: {log}"); return 3
    text = log.read_text(encoding="utf-8", errors="replace")
    code, why = validate(log)
    if code != 0:
        print(f"NOT A COUNT (burndown-count exit {code}):")
        print("\n".join("  " + l for l in why[:6]))
        if code == 2:
            print("Gradle's own explanation:")
            print("\n".join("  " + l for l in what_went_wrong(text)))
        return code
    if a.file:
        want = a.file.replace("\\", "/")
        hits = [(f, l, m) for f, l, m in ((rel(f), l, m) for f, l, m in rb.parse_errors(text))
                if f == want or f.endswith("/" + want)]
        for f, l, m in sorted(hits, key=lambda t: (t[0], t[1])):
            print(f"{f}:{l}: {m}")
        print(f"{len(hits)} error(s) in {len({f for f, _l, _m in hits})} file(s) matching {want}")
        return 0
    s = summarise(text, rb.signatures())
    state = pathlib.Path(a.state) if a.state else log.with_name(log.name + ".prev.json")
    prev = None
    if not a.no_state and state.is_file():
        try:
            prev = json.loads(state.read_text(encoding="utf-8"))
        except ValueError:
            prev = None
    print("\n".join(render(s, prev, a.top, a.files)))
    if not a.no_state:
        state.write_text(json.dumps(s), encoding="utf-8")
    return 0


def self_check():
    import tempfile
    cat = ("## Z. test\n7. **Thing** · **Pattern:** x · **Error:** `cannot find symbol: class FooLoadingContext` · "
           "**Fix:** y\n")
    sigs = rb.signatures(cat)
    one = ("> Task :compileJava\n"
           "/w/src/main/java/m/A.java:3: error: cannot find symbol\n  x\n  ^\n  symbol:   class FooLoadingContext\n"
           "/w/src/main/java/m/A.java:9: error: cannot find symbol\n  symbol:   class FooLoadingContext\n"
           "C:\\w\\src\\main\\java\\m\\B.java:4: error: incompatible types: Bar cannot be converted to Baz\n")
    s1 = summarise(one, sigs)
    two = one.replace("/w/src/main/java/m/A.java:9", "/w/src/main/java/m/A.java:10") + \
        "/w/src/main/java/m/B.java:5: error: incompatible types: Qux cannot be converted to Baz\n" \
        "/w/src/main/java/m/C.java:1: error: cannot find symbol\n  symbol:   variable NEWTHING\n"
    s2 = summarise(two, sigs)
    lines = render(s2, s1, 8, 8)
    text = "\n".join(lines)
    ok = (s1["total"] == 3 and s1["groups"].get("§7") == 2 and s1["files"] == {"m/A.java": 2, "m/B.java": 1}
          and s2["total"] == 5 and "previous run: 3, +2" in lines[0]
          and "GREW" in text and "+1  incompatible types: T cannot be converted to T" in text
          and "NEW groups" in text and "cannot find symbol: variable NEWTHING" in text
          and "read only these entries: 7" in text and len(lines) < 40)
    # a log that never compiled is refused, not summarised as 0
    with tempfile.TemporaryDirectory() as t:
        bad = pathlib.Path(t, "b.log")
        bad.write_text("> Task :compileJava FAILED\n* What went wrong:\nCould not resolve x\n", encoding="utf-8")
        code, _ = validate(bad)
        ok = ok and code != 0
    print("self-check:", "OK" if ok else "FAIL\n" + text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
