#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""keep-current for a MULTI-VERSION fork: carry the authors' new code from the port branch into the layer branch and
say, per target, what it did.

    python3 tools/layer-sync.py detect --repo DIR
    python3 tools/layer-sync.py merge  --repo DIR --port-ref REF [--layer-branch neoforge-1.21.x]
    python3 tools/layer-sync.py run    --repo DIR [--targets 1.21.5,1.21.8] [--budget 15] [--changed-from REF]
                                       [--json out.json] [--md out.md]
    python3 tools/layer-sync.py ledger --ledger ledger.json --json out.json --column keep-current/2026-10-12
    python3 tools/layer-sync.py --self-check

Single-version forks are the default and are NOT touched: every command except `detect` prints "not a multi-version
fork" and exits 0 having changed nothing when the checkout has no .multiversion/ (base.properties + at least one
target). The keep-current skill only calls this for a fork that has the layer.

  detect  prints layer=true|false (and the targets).
  merge   merge REF (the 1.21.1 port branch, or the keep-current work branch cut from it) into the checked-out layer
          branch with a MERGE COMMIT (--no-ff; never a rebase, never a force-push), then run the layer's isolation rule
          against REF. Refuses a dirty tree. A conflict is aborted and listed: it is a person's.
  run     for every target (oldest first), with tools/fork-port.py's own stages:
            measure   `--from-stage stage --to-stage measure`  staging, the generated class-move table, the compile count
            audit     `--from-stage audit --to-stage audit`    mixin + event-listener audits, when the count is 0
            loop      `--from-stage loop --to-stage audit`     the compile loop (budget $N), then the audits, when it is not
          and classify it:
            untouched  0 errors and clean audits straight after the merge: upstream's change did not reach this target
            touch-up   errors after the merge, 0 errors and clean audits after the loop: the loop cleared it
            rework     anything left (errors, an audit mismatch, a stage that would not run): fork-port.py wrote
                       .multiversion/targets/<t>/RESIDUE.md; THE RUN STOPS FOR A PERSON (exit 10). A rework target is
                       never skipped, silently or otherwise, and never reported as done.
          It also lists, per target, the upstream-changed files that target OVERLAYS (an overlay replaces the whole
          file, so a clean compile does not say the overlay still does what upstream's new version does).
          Writes a JSON record and the markdown table (target | errors before | errors after | class | residue) that
          goes in the PR body.
  ledger  writes the table into a fix ledger's JSON ("layer"), so `fix-ledger.py render --into` puts it in the issue.

Gates are not run here: the layer's CI runs the full gates on the bookends and compile + audits on every middle
target, and keep-current's gate step runs `ci-gates.py --mc` for the targets it releases.
Environment (tests): LAYER_SYNC_FORK_PORT replaces tools/fork-port.py. Standard library only.
"""
import argparse, importlib.util, json, os, pathlib, re, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
CLASSES = ("untouched", "touch-up", "rework")


def _layer_ci():
    spec = importlib.util.spec_from_file_location("layer_ci_for_sync", HERE / "layer-ci.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def git(repo, *a, check=False):
    r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode:
        raise SystemExit(f"git {' '.join(a)}: {(r.stderr or r.stdout).strip()[:300]}")
    return r


# ---------------------------------------------------------------- pure helpers (covered by --self-check)

def layer_targets(repo):
    """The fork's layer targets, oldest first; [] when it has no layer (a single-version fork)."""
    p = _layer_ci().plan(repo)
    return list(p["targets"]) if p["layer"] == "true" else []


def classify(before, audit_ok_before, after, audit_ok_after, stopped):
    """-> class. before/after: compile error counts (None = no count); audit_ok_*: True/False/None (not run).
    stopped: a stage refused to finish (it wrote a RESIDUE.md)."""
    if stopped:
        return "rework"
    if before == 0 and audit_ok_before:
        return "untouched"
    if before is not None and before > 0 and after == 0 and audit_ok_after:
        return "touch-up"
    return "rework"


def shadowed(mv, t, changed):
    """Upstream-changed files that target t (or a target it inherits) overlays: changed is a list of repo paths; an
    overlay lives at .multiversion/targets/<x>/java/<path under src/main/java>."""
    ch, seen, x = [], set(), t
    while x and x not in seen:
        seen.add(x); ch.append(x)
        props = pathlib.Path(mv) / "targets" / x / "target.properties"
        x = None
        if props.is_file():
            m = re.search(r"(?m)^\s*inherits\s*=\s*(\S+)", props.read_text(encoding="utf-8"))
            x = m.group(1) if m else None
    out = []
    for f in changed:
        m = re.match(r"src/(?:main|[\w-]+)/java/(.+)$", f)
        if not m:
            continue
        for c in ch:
            sub = pathlib.Path(mv) / "targets" / c / ("java" if f.startswith("src/main/") else f"{f.split('/')[1]}/java")
            if (sub / m.group(1)).is_file():
                out.append(f)
                break
    return sorted(out)


def table(rows):
    cell = lambda v: "-" if v is None else str(v)
    md = ["| target | errors before | errors after | class | residue |", "|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['target']} | {cell(r['before'])} | {cell(r['after'])} | {r['class']} | "
                  f"{('`' + r['residue'] + '`') if r.get('residue') else '-'} |")
    notes = [f"- `{r['target']}` overlays {len(r['shadowed'])} file(s) upstream changed: {', '.join(r['shadowed'][:6])}"
             f"{' ...' if len(r['shadowed']) > 6 else ''} -- read each overlay against upstream's new version"
             for r in rows if r.get("shadowed")]
    notes += [f"- `{r['target']}`: {r['note']}" for r in rows if r.get("note")]
    return "\n".join(md + ([""] + notes if notes else []))


def stopped_block(rows):
    bad = [r for r in rows if r["class"] == "rework"]
    if not bad:
        return ""
    lines = [f"STOPPED for a person: {len(bad)} target(s) need rework -- never skipped, never released as is:"]
    for r in bad:
        lines.append(f"  {r['target']}: {r['note'] or 'residue left'}" + (f" -> {r['residue']}" if r.get("residue") else ""))
    lines += ["Read each RESIDUE.md, fix what it lists in the shared library (templates/fork-layer/shared/<t>) or a",
              "converter -- not in one fork -- and resume with: python3 tools/layer-sync.py run --repo DIR --targets "
              + ",".join(r["target"] for r in bad)]
    return "\n".join(lines)


# ---------------------------------------------------------------- commands

def not_a_layer(repo):
    print(f"layer-sync: {repo} is not a multi-version fork (no .multiversion/ with a target): nothing to do, nothing changed")
    return 0


def ref_targets(repo, ref):
    """Layer targets on REF without checking it out (a target is a .multiversion/targets/<t>/target.properties)."""
    r = git(repo, "ls-tree", "-r", "--name-only", ref, "--", ".multiversion/targets")
    if r.returncode:
        return None
    ts = {m.group(1) for m in (re.fullmatch(r"\.multiversion/targets/([^/]+)/target\.properties", l)
                               for l in r.stdout.split()) if m}
    return sorted(ts, key=lambda t: [int(x) if x.isdigit() else x for x in re.split(r"[.]", t)])


def layer_refs(repo):
    """Every local or remote branch that carries a layer target (so a check of the wrong branch cannot pass silently)."""
    rows = git(repo, "for-each-ref", "--format=%(refname:short)\t%(symref)", "refs/heads", "refs/remotes").stdout.splitlines()
    # skip symbolic refs (origin/HEAD, which --format=short prints as a bare "origin"): an alias, not a branch
    return [r for r, _, sym in (l.partition("\t") for l in rows) if r and not sym and ref_targets(repo, r)]


def cmd_detect(a):
    repo = pathlib.Path(a.repo).resolve()
    if a.ref:
        ts = ref_targets(repo, a.ref)
        if ts is None:
            print(f"layer-sync: unknown ref {a.ref}")
            return 2
    else:
        ts = layer_targets(repo)
    print(f"layer={'true' if ts else 'false'}" + (f" targets={','.join(ts)}" if ts else ""))
    if ts or a.ref:
        return 0
    # The checked-out branch has no layer. On a single-version fork no branch has one either, so nothing changes; but
    # a layer fork checked out on its port branch would otherwise read as single-version and skip the layer step.
    others = layer_refs(repo)
    if others:
        print(f"layer-sync: the checked-out branch has no layer, but {', '.join(others)} carr"
              f"{'ies' if len(others) == 1 else 'y'} .multiversion/ -- this fork HAS a layer; rerun with --ref <layer branch>"
              " (the fleet entry's layer_branch)")
        return 3
    return 0


def cmd_merge(a):
    repo = pathlib.Path(a.repo).resolve()
    if not layer_targets(repo):
        return not_a_layer(repo)
    if git(repo, "status", "--porcelain").stdout.strip():
        print("layer-sync: the working tree is not clean -- commit or stash first")
        return 2
    cur = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if a.layer_branch and cur != a.layer_branch:
        print(f"layer-sync: checked out {cur}, not the layer branch {a.layer_branch}")
        return 2
    if git(repo, "rev-parse", "--verify", a.port_ref + "^{commit}").returncode:
        print(f"layer-sync: unknown ref {a.port_ref}")
        return 2
    sha = git(repo, "rev-parse", "--short", a.port_ref).stdout.strip()
    if git(repo, "merge-base", "--is-ancestor", a.port_ref, "HEAD").returncode == 0:
        print(f"layer-sync: {cur} already contains {a.port_ref} ({sha}): nothing to merge")
        return 0
    r = git(repo, "merge", "--no-ff", "-m", f"Merge {a.port_ref} ({sha}) into {cur}: the authors' new code, carried to the layer", a.port_ref)
    if r.returncode:
        files = git(repo, "diff", "--name-only", "--diff-filter=U").stdout.split()
        git(repo, "merge", "--abort")
        print(f"layer-sync: merging {a.port_ref} conflicts in {len(files)} file(s) -- aborted, nothing changed: "
              f"{', '.join(files[:8])}\n  the layer branch differs from the port branch only in .multiversion/ and the "
              "hook line at the end of build.gradle, so a conflict elsewhere means that rule was already broken")
        return 1
    iso = subprocess.run([sys.executable, str(repo / ".multiversion/tools/layer.py"), "isolation", "--base", a.port_ref],
                         cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(iso.stdout.strip())
    print(f"layer-sync: merged {a.port_ref} ({sha}) into {cur} at {git(repo, 'rev-parse', '--short', 'HEAD').stdout.strip()}")
    return 0 if iso.returncode == 0 else 1


class Runner:
    def __init__(self, repo, budget):
        self.repo, self.budget = pathlib.Path(repo), budget
        self.tool = os.environ.get("LAYER_SYNC_FORK_PORT") or str(HERE / "fork-port.py")

    def call(self, t, frm, to):
        r = subprocess.run([sys.executable, self.tool, "--repo", str(self.repo), "--target", t, "--budget", str(self.budget),
                            "--gates", "none", "--from-stage", frm, "--to-stage", to],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return r.returncode, r.stdout

    def summary(self, t):
        p = self.repo / "build" / f"mc{t}" / "fork-port" / "fork-port.json"
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"stages": {}}


def residue_of(repo, t):
    f = pathlib.Path(repo) / ".multiversion/targets" / t / "RESIDUE.md"
    return f".multiversion/targets/{t}/RESIDUE.md" if f.is_file() else ""


def first_stop(summ):
    st = summ.get("stop") or {}
    return f"{st.get('stage')}: {st.get('left')}" if st else ""


def run_target(repo, t, runner):
    rec = {"target": t, "before": None, "after": None, "class": "rework", "residue": "", "note": ""}
    rc, out = runner.call(t, "stage", "measure")
    s = runner.summary(t)
    meas = s.get("stages", {}).get("measure", {})
    if rc != 0 or not isinstance(meas.get("errors"), int):
        rec["note"] = first_stop(s) or f"fork-port exit {rc}"
        rec["residue"] = residue_of(repo, t)
        return rec
    rec["before"] = meas["errors"]
    if rec["before"] == 0:
        rc, out = runner.call(t, "audit", "audit")
        s = runner.summary(t)
        audit_ok = rc == 0
        rec["after"] = 0
        rec["class"] = classify(0, audit_ok, 0, audit_ok, not audit_ok and bool(first_stop(s) or rc))
        if rec["class"] == "rework":
            rec["note"] = first_stop(s) or f"audit exit {rc}"
            rec["residue"] = residue_of(repo, t)
        return rec
    rc, out = runner.call(t, "loop", "audit")
    s = runner.summary(t)
    loop = s.get("stages", {}).get("loop", {})
    rec["after"] = loop.get("errors") if isinstance(loop.get("errors"), int) else (0 if rc == 0 else None)
    rec["class"] = classify(rec["before"], rc == 0, rec["after"], rc == 0, rc != 0 and rec["after"] == 0)
    if rec["class"] == "rework":
        rec["note"] = first_stop(s) or f"fork-port exit {rc}"
        rec["residue"] = residue_of(repo, t)
    return rec


def default_changed_from(repo):
    """The first parent of a merge HEAD (what the layer branch was before `merge`), else None."""
    r = git(repo, "rev-parse", "--verify", "HEAD^2")
    return "HEAD^1" if r.returncode == 0 else None


def cmd_run(a):
    repo = pathlib.Path(a.repo).resolve()
    ts = layer_targets(repo)
    if not ts:
        return not_a_layer(repo)
    want = [x for x in a.targets.split(",") if x] if a.targets else ts
    unknown = [x for x in want if x not in ts]
    if unknown:
        print(f"layer-sync: no such target(s): {', '.join(unknown)} (the layer has {', '.join(ts)})")
        return 2
    base = a.changed_from or default_changed_from(repo)
    changed = git(repo, "diff", "--name-only", base, "HEAD").stdout.split() if base else []
    runner = Runner(repo, a.budget)
    rows = []
    for t in [x for x in ts if x in want]:
        print(f"layer-sync: {t} ...", flush=True)
        rec = run_target(repo, t, runner)
        rec["shadowed"] = shadowed(repo / ".multiversion", t, changed)
        print(f"layer-sync: {t}: {rec['before']} -> {rec['after']} errors, {rec['class']}", flush=True)
        rows.append(rec)
    head = git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
    md = table(rows)
    data = {"head": head, "changed_from": base, "targets": rows}
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(data, indent=1), encoding="utf-8")
    if a.md:
        pathlib.Path(a.md).write_text(md + "\n", encoding="utf-8")
    print("\n" + md)
    stop = stopped_block(rows)
    if stop:
        print("\n" + stop)
        return 10
    return 0


def cmd_ledger(a):
    led_path = pathlib.Path(a.ledger)
    led = json.loads(led_path.read_text(encoding="utf-8"))
    data = json.loads(pathlib.Path(a.json).read_text(encoding="utf-8"))
    led["layer"] = {"column": a.column, "targets": [
        {k: r.get(k) for k in ("target", "before", "after", "class", "residue")} for r in data["targets"]]}
    led_path.write_text(json.dumps(led, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"layer-sync: {len(data['targets'])} target(s) written into {led_path} under column {a.column}")
    return 0


# ---------------------------------------------------------------- self-check

def self_check():
    ok = True

    def expect(c, msg):
        nonlocal ok
        if not c:
            ok = False; print("FAIL:", msg)
    expect(classify(0, True, 0, True, False) == "untouched", "0 errors, audits clean: untouched")
    expect(classify(7, None, 0, True, False) == "touch-up", "errors cleared by the loop: touch-up")
    expect(classify(7, None, 3, False, True) == "rework", "errors left: rework")
    expect(classify(0, False, 0, False, True) == "rework", "a failing audit at 0 errors: rework")
    expect(classify(None, None, None, None, True) == "rework", "a stage that would not run: rework")
    expect(classify(7, None, 0, False, False) == "rework", "loop cleared the errors but the audit is red: rework")
    with tempfile.TemporaryDirectory() as d:
        mv = pathlib.Path(d)
        for t, inh in (("1.21.4", None), ("1.21.5", "1.21.4")):
            (mv / "targets" / t).mkdir(parents=True)
            (mv / "targets" / t / "target.properties").write_text(f"inherits={inh}\n" if inh else "x=1\n", encoding="utf-8")
        (mv / "targets/1.21.4/java/net/m").mkdir(parents=True)
        (mv / "targets/1.21.4/java/net/m/A.java").write_text("x", encoding="utf-8")
        (mv / "targets/1.21.5/java/net/m").mkdir(parents=True)
        (mv / "targets/1.21.5/java/net/m/B.java").write_text("x", encoding="utf-8")
        ch = ["src/main/java/net/m/A.java", "src/main/java/net/m/B.java", "src/main/java/net/m/C.java", "README.md"]
        expect(shadowed(mv, "1.21.4", ch) == ["src/main/java/net/m/A.java"], "own overlay only")
        expect(shadowed(mv, "1.21.5", ch) == ["src/main/java/net/m/A.java", "src/main/java/net/m/B.java"], "inherited overlays count")
    rows = [{"target": "1.21.4", "before": 0, "after": 0, "class": "untouched", "residue": "", "note": "", "shadowed": []},
            {"target": "1.21.8", "before": 12, "after": 3, "class": "rework", "residue": ".multiversion/targets/1.21.8/RESIDUE.md",
             "note": "loop: 3 compile errors", "shadowed": ["src/main/java/net/m/A.java"]}]
    t = table(rows)
    expect("| 1.21.4 | 0 | 0 | untouched | - |" in t and "| 1.21.8 | 12 | 3 | rework | `.multiversion/targets/1.21.8/RESIDUE.md` |" in t
           and "overlays 1 file(s)" in t, "table")
    expect("STOPPED for a person" in stopped_block(rows) and stopped_block(rows[:1]) == "", "stop block only for rework")
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    if "--self-check" in sys.argv:
        return self_check()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1] if __doc__ else "")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("detect"); p.add_argument("--repo", default=".")
    p.add_argument("--ref", help="read the layer from this branch/ref (the fleet's layer_branch) without checking it out")
    p = sub.add_parser("merge"); p.add_argument("--repo", default="."); p.add_argument("--port-ref", required=True)
    p.add_argument("--layer-branch")
    p = sub.add_parser("run"); p.add_argument("--repo", default="."); p.add_argument("--targets")
    p.add_argument("--budget", type=float, default=15.0); p.add_argument("--changed-from")
    p.add_argument("--json"); p.add_argument("--md")
    p = sub.add_parser("ledger"); p.add_argument("--ledger", required=True); p.add_argument("--json", required=True)
    p.add_argument("--column", required=True)
    a = ap.parse_args()
    return {"detect": cmd_detect, "merge": cmd_merge, "run": cmd_run, "ledger": cmd_ledger}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
