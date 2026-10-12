#!/usr/bin/env python3
"""The fix ledger: every defect a fork port fixed or recorded, split into the failure modes a player meets, with
where each mode is fixed, by whom, and on which branch -- and the reconcile worksheet that keeps it true when the
authors (or a library the port depends on) change.

    python3 tools/fix-ledger.py worksheet --repo <fork clone> --port <branch> --upstream <remote/branch>
                                          [--dep-repo <dependency clone> --dep-range <old>..<new> ...]
    python3 tools/fix-ledger.py render  --ledger <ledger.json>          # the block that goes in the fork issue
    python3 tools/fix-ledger.py parse   --body <issue-body.md>          # the JSON back out of an issue body
    python3 tools/fix-ledger.py check   --ledger <ledger.json> [--release <branch>]
    python3 tools/fix-ledger.py --self-check

WHY IT EXISTS. "Did the authors fix bug X?" asked once per issue, from memory, by reading the files the author
touched, gets the wrong answer: one issue is several failure modes (a crash, a kick, a feature switched off, a
state that survives a reload), the authors' change can cover some of them and not others, and a change in a
LIBRARY the port depends on can cover part of what a fix in the dependent mod was for. So the unit here is the
failure mode, each with a severity and a way to reproduce it, and a decision is only as good as its evidence.

SEVERITY decides what may ship, whoever's bug it is (players blame the fork they installed):
  S1  crash, kick/disconnect, hang, world or save damage, other mods broken   -- never ships on a released branch
  S2  a feature of this mod broken or wrong
  S3  cosmetic (a texture, a message, a log line)

THE RULE for each failure mode a fix of ours addresses, at every reconcile:
  - upstream (the authors, or a dependency's new release) resolves it, PROVEN by an A/B -- our fix reverted, the
    reproduction run, the mode absent -- then drop ours (least fork);
  - upstream resolves part of it: keep the smallest change of ours that covers the rest;
  - otherwise keep ours.
  A decision to drop a fix without an A/B is refused by `check`. "The author touched the file" is not evidence.

THE LEDGER lives in the fork's issue for the defect, between <!-- fix-ledger:begin --> and <!-- fix-ledger:end -->:
a table for people and the JSON it was rendered from, so the next run reads it back (`parse`). JSON shape:

  {"issue": "owner/repo#N", "title": "...",
   "modes": [{"id": "kick", "what": "player disconnected ~7 s after ...", "severity": "S1",
              "repro": "Gate C spawn with bagus_lib; grep 'lost connection'",
              "status": {"neoforge-1.21.1": {"state": "fixed|open|not-affected",
                                             "by": "ours <sha> | upstream <owner/repo>@<sha> | dependency <owner/repo>@<tag>",
                                             "evidence": "<run, log line, test>", "decision": "keep|drop|adjust|accept"}}}]}

Taking or re-implementing an upstream fix (decision drop / accept / adjust) needs an A/B with TWO arms, recorded as
  "ab": {"control": "<our fix absent, nothing else changed: the mode SHOWN>", "treatment": "<their fix: mode absent>"}
-- a control that does not show the mode means the repro does not reproduce, not that the mode is gone -- or, when
the authors ship our fix byte-identical, "identical": "<the comparison>".

`check` fails on: an S1 mode open on the branch column named by --release (which must exist); a drop/accept/adjust
without both A/B arms (or "identical"); a mode with no repro; an unknown severity, state or decision. It warns
(without failing) on "fixed-unconfirmed" S1 modes on the released column. Standard library only.
"""
import argparse, json, pathlib, re, subprocess, sys, tempfile

BEGIN, END = "<!-- fix-ledger:begin -->", "<!-- fix-ledger:end -->"
FIX_SUBJECT = re.compile(r"^(Pre-existing upstream defect:|Port fix:)", re.I)
SEVERITIES = {"S1": "crash / kick / hang / save damage / other mods broken",
              "S2": "this mod's feature broken", "S3": "cosmetic"}
STATES = ("fixed", "fixed-unconfirmed", "open", "not-affected")   # fixed-unconfirmed: shipped, repro not re-run
DECISIONS = ("keep", "drop", "adjust", "accept")


def git(repo, *a, check=True):
    r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode:
        sys.exit(f"git {' '.join(a)}: {(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout


def fix_commits(repo, port, upstream):
    """Our fix commits: on the port branch, not on the authors' branch, subject marks them as a fix."""
    out = []
    for line in git(repo, "log", "--no-merges", "--format=%H%x09%s", f"{upstream}..{port}").splitlines():
        sha, subj = line.split("\t", 1)
        if FIX_SUBJECT.match(subj):
            body = git(repo, "log", "-1", "--format=%b", sha)
            files = [f for f in git(repo, "show", "--name-only", "--format=", sha).splitlines() if f]
            out.append({"sha": sha[:7], "subject": subj, "files": files,
                        "issues": sorted(set(re.findall(r"(?:[\w.-]+/[\w.-]+)?#\d+", body)))})
    return out


def changed_files(repo, rng):
    return {f for f in git(repo, "diff", "--name-only", rng.replace("..", "...", 1) if "..." not in rng else rng).splitlines() if f}


def symbols(repo, files, rng):
    """Identifiers the incoming change adds or removes in those files -- what to read first, not a verdict."""
    diff = git(repo, "diff", rng.replace("..", "...", 1) if "..." not in rng else rng, "--", *files) if files else ""
    return sorted({m for l in diff.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---"))
                   for m in re.findall(r"\b([a-z][A-Za-z0-9]{3,}\s*\()", l)})[:20]


def ledger_issues(paths):
    """commit sha (7) -> issue, from ledgers' "by" fields: maps a fix whose commit message names no issue."""
    out = {}
    for p in paths:
        led = json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
        for m in led.get("modes", []):
            for s in m.get("status", {}).values():
                for sha in re.findall(r"\b[0-9a-f]{7,40}\b", s.get("by", "")):
                    out.setdefault(sha[:7], led.get("issue"))
    return out


def worksheet(repo, port, upstream, deps=(), ledgers=()):
    """The reconcile worksheet: every fix of ours, what the incoming changes (the authors' and each dependency's)
    touch near it, and the questions to answer per failure mode. It lists; it never decides."""
    fixes = fix_commits(repo, port, upstream)
    known = ledger_issues(ledgers)
    for f in fixes:
        if not f["issues"] and f["sha"] in known:
            f["issues"] = [f"{known[f['sha']]} (from its ledger)"]
    incoming = git(repo, "log", "--no-merges", "--format=%h %s", f"{port}..{upstream}").splitlines()
    up_files = changed_files(repo, f"{port}..{upstream}")
    head = git(repo, "rev-parse", "--short", upstream).strip()
    L = [f"# Reconcile worksheet: {port} <- {upstream} at {head}", "",
         f"Upstream is at `{head}` now; fetch again right before merging, and name the commit actually merged.", "",
         f"{len(incoming)} incoming commit(s) from the authors; {len(fixes)} fix commit(s) of ours on {port}.", ""]
    for d in deps:
        L.append(f"Dependency `{d['repo']}` moves {d['range']}: "
                 f"{len(changed_files(d['repo'], d['range']))} file(s) changed -- its fixes may cover part of ours.")
    L += ["", "## Step 1 -- read EVERY issue first, on this fork AND on each dependency/dependent fork, open and closed",
          "List each issue's failure modes from the issue text itself (not from memory), each with a severity "
          "(S1 crash/kick/hang/save damage/other mods broken, S2 feature, S3 cosmetic) and a reproduction.", "",
          "## Step 2 -- our fixes against the incoming changes", ""]
    port_only = [f for f in fixes if f["subject"].lower().startswith("port fix:")]
    if port_only:
        L += ["Port-only fixes (a fix to OUR port, not to the authors' code: no upstream change supersedes it, keep; "
              "check only that the incoming code did not move what it fixed):",
              *[f"- {f['sha']} {f['subject']}" for f in port_only], ""]
    for f in fixes:
        if f in port_only:
            continue
        near = sorted(set(f["files"]) & up_files)
        L += [f"### {f['sha']} {f['subject']}", f"- issues it names: {', '.join(f['issues']) or '(none -- find it)'}",
              f"- files: {', '.join(f['files'])}",
              f"- the authors changed {len(near)} of them" + (f": {', '.join(near)}" if near else
                                                                " (they may still have fixed it elsewhere, or a "
                                                                "dependency may have)"),
              (f"- identifiers the change adds/removes there: {', '.join(symbols(repo, near, f'{port}..{upstream}'))}"
               if near else "- identifiers: -"),
              "- per failure mode: does the authors' change resolve it? does a dependency's? A/B: revert this "
              "commit, run the repro, record the result in the ledger.", ""]
    L += ["## Step 3 -- decide by the rule, record in each issue's ledger, and check",
          "Drop ours only with an A/B; keep the smallest residual otherwise; no S1 mode open on a released branch.",
          "`fix-ledger.py check --ledger <file> --release <branch>` before any release or promotion."]
    return "\n".join(L)


def render(led):
    rows = ["| failure mode | severity | " + " | ".join(sorted({b for m in led["modes"] for b in m["status"]})) + " |"]
    branches = sorted({b for m in led["modes"] for b in m["status"]})
    rows.append("|---|---|" + "---|" * len(branches))
    for m in led["modes"]:
        cells = []
        for b in branches:
            s = m["status"].get(b)
            cells.append("–" if not s else f"**{s['state']}**" + (f" by {s['by']}" if s.get("by") else "")
                         + (f" ({s['decision']})" if s.get("decision") else ""))
        rows.append(f"| {m['what']} | {m['severity']} | " + " | ".join(cells) + " |")
    ev = [f"- `{m['id']}` on `{b}`: {s['evidence']}" for m in led["modes"] for b, s in m["status"].items()
          if s.get("evidence")]
    return "\n".join([BEGIN, f"**Fix ledger** for {led['issue']}: where each failure mode is fixed, by whom, on which "
                      "branch. Updated by every reconcile; edit the JSON, not the table.", "", *rows, "",
                      "Evidence:", *(ev or ["- (none yet)"]), "", "<details><summary>ledger JSON</summary>", "",
                      "```json", json.dumps(led, ensure_ascii=False, separators=(",", ":")), "```", "", "</details>", END])


def parse(body):
    m = re.search(re.escape(BEGIN) + r".*?```json\n(.*?)\n```.*?" + re.escape(END), body, re.S)
    if not m:
        raise SystemExit("no fix ledger in this issue body")
    return json.loads(m.group(1))


def replace_block(body, led):
    block = render(led)
    if BEGIN in body and END in body:
        return re.sub(re.escape(BEGIN) + r".*?" + re.escape(END), lambda _: block, body, flags=re.S)
    return body.rstrip() + "\n\n" + block + "\n"


def column_matches(col, release):
    """A column names a release when it IS that name or lists it among " + "-joined names: one ledger can span a
    library and the mod that uses it, so a column is often "<lib tag> + <mod tag>"."""
    return col == release or release in [t.strip() for t in col.split(" + ")]


def check(led, release=None):
    errs = []
    for m in led.get("modes", []):
        if m.get("severity") not in SEVERITIES:
            errs.append(f"{m.get('id')}: severity must be one of {', '.join(SEVERITIES)}")
        if not m.get("repro"):
            errs.append(f"{m.get('id')}: no reproduction -- a mode nobody can reproduce cannot be shown fixed")
        for b, s in m.get("status", {}).items():
            if s.get("state") not in STATES:
                errs.append(f"{m.get('id')} on {b}: state must be one of {', '.join(STATES)}")
            if s.get("decision") and s["decision"] not in DECISIONS:
                errs.append(f"{m.get('id')} on {b}: decision must be one of {', '.join(DECISIONS)}")
            ab = s.get("ab") or {}
            if s.get("decision") in ("drop", "accept", "adjust") and not s.get("identical") and not (
                    ab.get("control") and ab.get("treatment")):
                errs.append(f"{m.get('id')} on {b}: '{s['decision']}' needs an A/B with both arms -- \"ab\": "
                            "{\"control\": <our fix absent: the mode shown>, \"treatment\": <with theirs: absent>} "
                            "-- or \"identical\": <proof they ship our fix byte-identical>")
            if s.get("state") == "fixed" and not s.get("by"):
                errs.append(f"{m.get('id')} on {b}: fixed by whom? (ours <sha> / upstream <repo>@<sha> / dependency)")
            if release and column_matches(b, release) and m.get("severity") == "S1" and s.get("state") == "open":
                errs.append(f"{m.get('id')}: S1 ({SEVERITIES['S1']}) is open on {b} -- it must not be released")
    if release and not any(column_matches(b, release) for m in led.get("modes", []) for b in m.get("status", {})):
        cols = sorted({b for m in led.get("modes", []) for b in m.get("status", {})})
        errs.append(f"--release {release!r} names no column of this ledger (columns: {', '.join(cols)}) -- a check "
                    "against a column that does not exist checks nothing")
    return errs


def warnings(led, release=None):
    return [f"{m.get('id')} on {b}: S1 fixed but not confirmed by re-running its repro ({s.get('evidence', '')[:80]})"
            for m in led.get("modes", []) for b, s in m.get("status", {}).items()
            if m.get("severity") == "S1" and s.get("state") == "fixed-unconfirmed" and (not release or column_matches(b, release))]


def self_check():
    ok = True
    led = {"issue": "o/r#2", "title": "t", "modes": [
        {"id": "kick", "what": "player disconnected", "severity": "S1", "repro": "gate C spawn",
         "status": {"neoforge-1.21.1": {"state": "fixed", "by": "dependency o/lib@v2", "decision": "accept",
                                        "evidence": "A/B in ab", "ab": {"control": "ours reverted: disconnect",
                                                                        "treatment": "theirs: 0 disconnects"}}}},
        {"id": "rearm", "what": "re-armed on every load", "severity": "S1", "repro": "reload world",
         "status": {"neoforge-1.21.1": {"state": "fixed", "by": "ours abc1234", "decision": "keep",
                                        "evidence": "gate run"}}}]}
    ok &= check(led, "neoforge-1.21.1") == []
    body = "Some issue text.\n"
    b2 = replace_block(body, led)
    ok &= parse(b2) == led and b2.startswith("Some issue text.") and "| player disconnected | S1 |" in b2
    led2 = json.loads(json.dumps(led)); led2["modes"][0]["severity"] = "S0"
    ok &= replace_block(b2, led2).count(BEGIN) == 1 and parse(replace_block(b2, led2))["modes"][0]["severity"] == "S0"
    bad = json.loads(json.dumps(led))
    bad["modes"][0]["status"]["neoforge-1.21.1"]["ab"] = {"treatment": "absent"}   # no control arm
    bad["modes"][1]["status"]["neoforge-1.21.1"] = {"state": "open"}          # S1 open on the released branch
    errs = check(bad, "neoforge-1.21.1")
    ok &= any("both arms" in e for e in errs) and any("must not be released" in e for e in errs)
    ok &= any("names no column" in e for e in check(led, "neoforge-1.21"))   # a --release that matches nothing
    ok &= column_matches("lib-1-mc1.21.1 + mod-2-mc1.21.1", "mod-2-mc1.21.1")            # one tag of a joined column
    ok &= not column_matches("lib-1-mc1.21.1-r2 + mod-2", "lib-1-mc1.21.1")             # a prefix is not that tag
    idl = json.loads(json.dumps(led)); idl["modes"][0]["status"]["neoforge-1.21.1"].pop("ab")
    idl["modes"][0]["status"]["neoforge-1.21.1"]["identical"] = "cmp: identical"
    ok &= check(idl, "neoforge-1.21.1") == []
    unc = json.loads(json.dumps(led)); unc["modes"][1]["status"]["neoforge-1.21.1"]["state"] = "fixed-unconfirmed"
    ok &= check(unc, "neoforge-1.21.1") == [] and len(warnings(unc, "neoforge-1.21.1")) == 1
    ok &= not any("must not be released" in e for e in check(bad))           # only with --release
    with tempfile.TemporaryDirectory() as d:
        r = pathlib.Path(d)
        def g(*a): subprocess.run(["git", "-C", d, *a], check=True, capture_output=True)
        g("init", "-q", "-b", "up"); g("config", "user.email", "a@a"); g("config", "user.name", "a")
        (r / "A.java").write_text("void spawn() { arm(); }\n", encoding="utf-8"); g("add", "-A"); g("commit", "-qm", "base")
        g("checkout", "-qb", "port")
        (r / "A.java").write_text("void spawn() { }\n", encoding="utf-8")
        g("commit", "-qam", "Pre-existing upstream defect: spawn arms the switch\n\nFixes #2")
        (r / "C.java").write_text("c\n", encoding="utf-8"); g("add", "-A"); g("commit", "-qm", "Port fix: our own port")
        (r / "B.java").write_text("x\n", encoding="utf-8"); g("add", "-A"); g("commit", "-qm", "Port: unrelated")
        g("checkout", "-q", "up"); (r / "A.java").write_text("void spawn() { arm(); clearOnLogout(); }\n", encoding="utf-8")
        g("commit", "-qam", "upstream change")
        fx = fix_commits(r, "port", "up")
        ok &= [f["subject"][:12] for f in fx] == ["Port fix: ou", "Pre-existing"]
        fx = [f for f in fx if f["subject"].startswith("Pre-existing")]
        ok &= fx[0]["issues"] == ["#2"] and fx[0]["files"] == ["A.java"]
        w = worksheet(r, "port", "up")
        ok &= "the authors changed 1 of them: A.java" in w and "clearOnLogout(" in w and "read EVERY issue" in w
        ok &= "Port-only fixes" in w and "Port fix: our own port" not in "".join(w.split("### ")[1:])
        lp = r / "l.json"; lp.write_text(json.dumps({"issue": "o/r#9", "modes": [{"status": {"x": {
            "by": "ours " + fx[0]["sha"]}}}]}), encoding="utf-8")
        g("checkout", "-q", "port"); g("commit", "-q", "--allow-empty", "-m", "Pre-existing upstream defect: no issue named")
        sha = subprocess.run(["git", "-C", d, "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True, encoding="utf-8").stdout.strip()
        lp.write_text(json.dumps({"issue": "o/r#9", "modes": [{"status": {"x": {"by": "ours " + sha}}}]}), encoding="utf-8")
        ok &= "o/r#9 (from its ledger)" in worksheet(r, "port", "up", ledgers=[lp])
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    if "--self-check" in sys.argv:
        return self_check()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("worksheet"); w.add_argument("--repo", required=True); w.add_argument("--port", required=True)
    w.add_argument("--upstream", required=True)
    w.add_argument("--dep-repo", action="append", default=[]); w.add_argument("--dep-range", action="append", default=[])
    w.add_argument("--ledger", action="append", default=[], help="existing ledger JSON, to map fix commits to issues")
    r = sub.add_parser("render"); r.add_argument("--ledger", required=True); r.add_argument("--into", help="issue body file to update in place")
    p = sub.add_parser("parse"); p.add_argument("--body", required=True)
    c = sub.add_parser("check"); c.add_argument("--ledger", required=True); c.add_argument("--release")
    a = ap.parse_args()
    if a.cmd == "worksheet":
        if len(a.dep_repo) != len(a.dep_range):
            sys.exit("give one --dep-range per --dep-repo")
        print(worksheet(a.repo, a.port, a.upstream, [{"repo": x, "range": y} for x, y in zip(a.dep_repo, a.dep_range)], a.ledger))
    elif a.cmd == "render":
        led = json.loads(pathlib.Path(a.ledger).read_text(encoding="utf-8"))
        if a.into:
            f = pathlib.Path(a.into); f.write_text(replace_block(f.read_text(encoding="utf-8"), led), encoding="utf-8")
        else:
            print(render(led))
    elif a.cmd == "parse":
        print(json.dumps(parse(pathlib.Path(a.body).read_text(encoding="utf-8")), indent=1, ensure_ascii=False))
    elif a.cmd == "check":
        led = json.loads(pathlib.Path(a.ledger).read_text(encoding="utf-8"))
        errs = check(led, a.release)
        for w in warnings(led, a.release):
            print("fix-ledger: warning:", w)
        for e in errs:
            print("fix-ledger:", e)
        print("fix-ledger:", "OK" if not errs else f"{len(errs)} problem(s)")
        return 1 if errs else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
