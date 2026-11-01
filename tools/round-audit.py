#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Audit a finished keep-current round: did it keep its guarantees? Read-only (GitHub and the fork clones).

    python3 tools/round-audit.py --plan owner/repo#N --fork owner/repo=DIR [--fork ...] [--state round/STATE.json]
                                 [--since TIME] [--until TIME] [--json] [--keep]
    python3 tools/round-audit.py --self-check

WHY. tools/round.py drives the weekly round; its speed-ups (skipping redundant local gate re-runs, reusing reviews,
overlapping versions) must not lose what the round promises. This is an independent check of a round that already
ran, from what it left behind -- the plan issue, GitHub's record of the forks (branch pushes, PRs, Actions runs,
releases) and the commits -- so it works on rounds that ran in other containers, without their round/STATE.json
(given with --state it is used too: the round's own start, its CI run ids, its queue for I7).

THE WINDOW. --since, else STATE.json's round_start, else the plan issue's creation; to the first comment starting
`## Round closed` (else --until, else now, said as not closed). Every branch update, PR merge and release inside it
is the round's.

READ-ONLY. Nothing is fetched into, checked out in or pushed from a fork clone: each fork gets a throwaway SHADOW
repository under a temporary directory that borrows the clone's objects (objects/info/alternates) and fetches origin's
branches, tags and pull heads into itself (only what the clone lacks is downloaded); every git read, export and
historical ref state below is the shadow's. The clone is read only for its objects and its origin URL.

GitHub: REST with GITHUB_TOKEN / GH_TOKEN, else gh (known-issues.py gh_api, as every round tool). The branch history
is the repository activity API (`/activity`: every push with before/after sha, its kind -- push, force_push,
pr_merge -- and GitHub's own timestamp).

THE INVARIANTS, per fork and per version branch (and head) the round changed (PASS / FAIL / WARN / N-A, each with
its evidence; exit 1 on any FAIL):
  I1 gated tree   each update of the branch in the window: a PR merge needs a green Port CI pull_request run on the
                  PR's final head with every job success or skipped (a skipped gates job fails), the run's tested
                  tree IDENTICAL to the merge commit's (tree hashes: the gate logs' built-tree.txt when the artifact
                  can be read, else the PR head's tree, which equals the merge's only when the base did not move),
                  and Gate C in it (the workflow at the merge passes ci-gates.py --gatec; a workflow without Gate C
                  needs the merge's .port/<v>/GATES.md record, fork-branches.py gates_problems). A direct push (the
                  round's nochange path, the ledger, a library record) must be a fast-forward that changes nothing
                  shipped (round.py shipped_changes: fork-branches.py jar_relevant under .port/, Java by its code),
                  and when it changes the Port CI workflow its push run must be green (round.py's nochange step
                  waits on any .github/ change; a `[skip ci]` plan-config commit runs no Port CI, so only the
                  workflow itself counts here).
  I2 released     each release published in the window for the version (round.py TAG_RE) points at a commit whose
                  tree is a tree I1 found gated, and either names that gated run as its source (`jars and gates of
                  run N, the same tree` in the release body, port-ci.yml from_run, whose release job checked the
                  run's built-tree.txt against the commit) or was built by a green workflow_dispatch run on that
                  commit (it gated the released tree itself); a version merged by PR with no release is a FAIL; a
                  gate summary asset (gates-*.md) with a Gate C row not PASS is a FAIL.
  I3 reviewed     at the last merged commit, the review part of the done list -- fork-branches.py review_items and
                  review_problems, what `fork-port.py --check-done` runs (a single-version tree against the base its
                  REVIEW.md records) -- on an export of that commit, with origin's branches set to where they were at
                  the merge (the activity history): any problem (no REVIEW.md, a stale commit, a missing verdict, a
                  not-ok row) is a FAIL. Gate records are not part of it (CI may hold them). A version-branch
                  fork's head has no REVIEW.md (the round writes its review into the PR body, round.py pr_body):
                  there every shipped file the merge changed needs a row in the body's review or recheck section,
                  its last verdict ok, and the review's RESULT ok. N-A for a branch the round only pushed to.
  I4 lower first  every commit an update brought onto neoforge-<v> that is on the version below (its
                  branch.properties `parent`, read at the new tip; the lower branch's tip at the window's end) was
                  already on origin's lower branch when the update was pushed: the lower branch's tip at the update's
                  activity timestamp (GitHub's push times, not committer dates) must contain it. N-A on a head and a
                  single-version fork (nothing below).
  I5 dependents   on a library's version: the newest library release of the window at that version is pinned by
                  each dependent's matching version branch at the window's end (its branch.properties
                  `library.<slug>=TAG` or a port_jar / workflow --dep-jar naming the tag), and the library's branch
                  then has the dependent's Gate C record for it (fork-branches.py library_status on .port/<v>/
                  DEPENDENTS.json: a PASS record for a commit whose jar is current). A dependent that does not build
                  the version is N-A; one that did not re-pin is a WARN when the closing comment names that version
                  for it, else a FAIL.
  I6 ledger       every status the round set to fixed@<sha> (ledger commits on the head in the window, each against
                  its first parent) names a commit on that version's branch at the window's end; and for each fix the
                  plan selected (read-plan's `fixes`, at their version), every version at or above it the round
                  merged by PR is not still `open` in the head's ledger at the window's end.
  I7 not skipped  a version the fork set builds that the round neither merged nor pushed nor named in the closing
                  comment's section for that fork is a WARN (with --state: one the queue gave no item is not; a
                  fork the round merged nothing for, whose section the closing comment has, is not).

KNOWN LIMITS. The activity API returns up to 100 updates per branch (older history: the window is still read when
it fits, a branch with more is said); a release's Gate C rows are read from its gates-*.md assets when they can be
downloaded; the built-tree.txt artifact is often unreachable from a cloud session (its storage host is not
api.github.com), and then the head-tree method is used and said; the closing comment is the round agent's prose, so
"named in it" (I5, I7) is a version string under the fork's `###` heading; I3 judges the review against the parent's
branch as it was at the merge, which is what the round saw only to GitHub's push-time precision.

Exit: 0 every invariant PASS / WARN / N-A, 1 a FAIL, 2 bad input. Standard library only.
"""
import argparse, contextlib, datetime, importlib.util, io, json, os, pathlib, re, shutil, stat, subprocess, sys
import tempfile, time, zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
PORT_CI_WF = ".github/workflows/port-ci.yml"
ZERO = "0" * 40
INVS = ("I1", "I2", "I3", "I4", "I5", "I6", "I7")
TITLES = {"I1": "gated tree", "I2": "released tested", "I3": "reviewed", "I4": "lower first", "I5": "dependents",
          "I6": "ledger", "I7": "not skipped"}
PASS, FAIL, WARN, NA = "PASS", "FAIL", "WARN", "N-A"
_MODS = {}
API = None          # the self-check's stand-in GitHub: callable(path) -> parsed JSON
RAW = None          # and its downloads: callable(url, accept) -> bytes


def mod(key):
    """The round's own tools, loaded as modules (round.py's mod()): their definitions, never a copy."""
    files = {"rd": "round.py", "fb": "fork-branches.py", "ki": "known-issues.py"}
    if key not in _MODS:
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location(f"round_audit_{key}", TOOLS / files[key])
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _MODS[key] = m
    return _MODS[key]


def gh(path):
    return API(path) if API else mod("ki").gh_api(path)


def gh_list(path, stop=None, limit=10):
    """A paged list (per_page=100); stop(item) True ends it early."""
    out = []
    for page in range(1, limit + 1):
        got = gh(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")
        got = got.get("workflow_runs", got.get("jobs", [])) if isinstance(got, dict) else got
        out += got
        if len(got) < 100 or (stop and got and stop(got[-1])):
            break
    return out


def gh_raw(url, accept="application/octet-stream"):
    """A download from api.github.com (a release asset, an artifact zip): the token goes to api.github.com only, the
    redirect to storage is fetched without it. -> bytes (raises when it cannot be read)."""
    if RAW:
        return RAW(url, accept)
    import urllib.error, urllib.request
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "mmvu-round-audit",
                                               **({"Authorization": f"Bearer {tok}"} if tok else {})})
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308) or not e.headers.get("Location"):
            raise
        with urllib.request.urlopen(e.headers["Location"], timeout=120) as r:
            return r.read()


def git(d, *a, inp=None):
    return subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", input=inp)


def out_of(d, *a):
    r = git(d, *a)
    return r.stdout.strip() if r.returncode == 0 else ""


def tree(d, c):
    return out_of(d, "rev-parse", f"{c}^{{tree}}") if c else ""


def is_ancestor(d, a, b):
    return bool(a and b) and git(d, "merge-base", "--is-ancestor", a, b).returncode == 0


def show(d, c, path):
    r = git(d, "show", f"{c}:{path}")
    return r.stdout if r.returncode == 0 else None


def ts(s):
    """An ISO time (a trailing Z too, Python 3.9) -> aware datetime, or None."""
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def iso(t):
    return t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if t else "?"


def short(c):
    return (c or "")[:12]


def rmtree(p):
    def onerror(fn, path, _exc):          # Windows: .git/objects files are read-only
        os.chmod(path, stat.S_IWRITE)
        fn(path)
    shutil.rmtree(p, onerror=onerror)


# ---------------------------------------------------------------- what the round left behind

class Fork:
    """One fork of the set: its shadow repository, branch history, PRs and releases in the window."""

    def __init__(self, slug, clone, cfg):
        self.slug, self.clone, self.cfg = slug, pathlib.Path(clone).resolve(), cfg or {}
        self.name = slug.split("/", 1)[1]
        self.head = self.cfg.get("head") or "neoforge-1.21.1"
        self.base = self.head[len("neoforge-"):] if self.head.startswith("neoforge-") else self.head
        self.library = self.cfg.get("role") == "library"
        self.depends_on = list(self.cfg.get("depends_on") or [])
        self.shadow = None
        self.kind, self.versions = "single", []
        self.events, self.prs, self.releases, self.notes = {}, {}, [], []

    def branch(self, v):
        return self.head if v == self.base else f"neoforge-{v}"

    def all_versions(self):
        return [self.base] + self.versions


def make_shadow(F, into):
    """A throwaway repository borrowing the clone's objects, origin's refs fetched into it (the clone is never
    written)."""
    common = out_of(F.clone, "rev-parse", "--path-format=absolute", "--git-common-dir")
    url = out_of(F.clone, "ls-remote", "--get-url", "origin")
    if not common or not url or url == "origin":
        raise SystemExit(f"round-audit: {F.slug}: {F.clone} is not a git clone with an origin")
    d = pathlib.Path(into) / F.name
    d.mkdir(parents=True)
    git(d, "init", "-q")
    (d / ".git/objects/info").mkdir(parents=True, exist_ok=True)
    (d / ".git/objects/info/alternates").write_text(pathlib.Path(common, "objects").resolve().as_posix() + "\n",
                                                    encoding="utf-8")
    for k, v in (("remote.origin.url", url), ("core.autocrlf", "false"), ("gc.auto", "0"),
                 ("maintenance.auto", "false"), ("remote.origin.pushurl", "file:///round-audit-never-pushes")):
        git(d, "config", k, v)
    r = git(d, "fetch", "-q", "--no-tags", "origin", "+refs/heads/*:refs/remotes/origin/*", "+refs/tags/*:refs/tags/*",
            "+refs/pull/*/head:refs/remotes/origin/pull/*")
    if r.returncode:
        r = git(d, "fetch", "-q", "--no-tags", "origin", "+refs/heads/*:refs/remotes/origin/*", "+refs/tags/*:refs/tags/*")
    if r.returncode:
        raise SystemExit(f"round-audit: {F.slug}: fetching origin into the shadow failed: {r.stderr.strip()[-300:]}")
    F.shadow = d
    return d


def collect(F, start, end):
    """The fork's kind, versions, branch history, PRs merged and releases published in the window."""
    fb, rd = mod("fb"), mod("rd")
    d = F.shadow
    have = fb.version_branches(d)
    F.kind = "branches" if have else "single"
    F.versions = rd.ladder(d, F.base) if have else []
    for v in F.all_versions():
        b = F.branch(v)
        evs = gh(f"repos/{F.slug}/activity?ref=refs/heads/{b}&per_page=100") or []
        evs = sorted(evs, key=lambda e: (e.get("timestamp") or "", e.get("id") or 0))
        if len(evs) >= 100 and ts(evs[0].get("timestamp")) and ts(evs[0]["timestamp"]) > start:
            F.notes.append(f"{b}: GitHub listed 100 updates, the oldest after the window's start: older ones unread")
        F.events[v] = {"all": evs, "window": [e for e in evs if start <= (ts(e.get("timestamp")) or start) <= end]}
    F.prs = {}
    for p in gh_list(f"repos/{F.slug}/pulls?state=closed&sort=updated&direction=desc",
                     stop=lambda p: (ts(p.get("updated_at")) or end) < start):
        m = ts(p.get("merged_at"))
        if m and start <= m <= end and str((p.get("base") or {}).get("ref", "")).startswith("neoforge-"):
            F.prs[p.get("merge_commit_sha")] = p
    F.releases = []
    for r in gh_list(f"repos/{F.slug}/releases", stop=lambda r: (ts(r.get("created_at")) or end) < start - datetime.timedelta(days=2)):
        t = ts(r.get("published_at") or r.get("created_at"))
        if t and start <= t <= end and not r.get("draft"):
            F.releases.append(r)


def tip_at(F, v, when):
    """origin's neoforge-<v> (or head) at `when`, from the activity history: the last update at or before it, else
    what the first later update replaced, else the branch as it is now."""
    evs = F.events.get(v, {}).get("all", [])
    before = [e for e in evs if (ts(e.get("timestamp")) or when) <= when]
    if before:
        a = before[-1].get("after")
        return None if a == ZERO else a
    if evs:
        b = evs[0].get("before")
        return None if b == ZERO else b
    return out_of(F.shadow, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{F.branch(v)}") or None


@contextlib.contextmanager
def refs_as_of(F, when):
    """The shadow's origin branches set to where they were at `when` (restored afterwards)."""
    d = F.shadow
    saved = {}
    for line in out_of(d, "for-each-ref", "--format=%(refname) %(objectname)", "refs/remotes/origin/").splitlines():
        ref, c = line.rsplit(" ", 1)
        if "/pull/" not in ref:
            saved[ref] = c
    try:
        for v in F.all_versions():
            c = tip_at(F, v, when)
            if c:
                git(d, "update-ref", f"refs/remotes/origin/{F.branch(v)}", c)
        yield
    finally:
        for ref, c in saved.items():
            git(d, "update-ref", ref, c)


def updates(F, v):
    return F.events.get(v, {}).get("window", [])


def pr_of(F, after):
    p = F.prs.get(after)
    if p is None:
        try:
            got = gh(f"repos/{F.slug}/commits/{after}/pulls") or []
        except Exception:                        # noqa: BLE001 -- said as no PR
            got = []
        p = next((x for x in got if x.get("merge_commit_sha") == after and x.get("merged_at")), None)
    return p


def runs_of(F, sha, event=None):
    rs = gh(f"repos/{F.slug}/actions/runs?head_sha={sha}&per_page=100") or {}
    rs = rs.get("workflow_runs", []) if isinstance(rs, dict) else rs
    return [r for r in rs if (not event or r.get("event") == event) and (r.get("path") or "").endswith(PORT_CI_WF)]


GATEC = re.compile(r"--gatec[ =]+\"?([A-Za-z0-9_,${}.\- ]*)")


def gatec_in(wf):
    m = GATEC.search(wf or "")
    return bool(m and m.group(1).strip(" \"'"))


def built_tree(F, run_id):
    """The tree a Port CI run built, from its gate logs' built-tree.txt (port-ci.yml), or None when unreadable."""
    try:
        arts = gh(f"repos/{F.slug}/actions/runs/{run_id}/artifacts?per_page=100").get("artifacts", [])
        for a in sorted(arts, key=lambda a: a.get("name") or ""):
            if str(a.get("name", "")).startswith("gate-logs-") and not a.get("expired"):
                z = zipfile.ZipFile(io.BytesIO(gh_raw(a["archive_download_url"], "application/vnd.github+json")))
                if "built-tree.txt" in z.namelist():
                    return z.read("built-tree.txt").decode("utf-8").strip()
    except Exception:                            # noqa: BLE001 -- the head-tree method is used and said
        return None
    return None


def export(F, c, into):
    fb = mod("fb")
    return fb.export(F.shadow, c, pathlib.Path(into))


# ---------------------------------------------------------------- the invariants (each its own function)

def gated_merge(ctx, F, v, e):
    """I1 for one PR merge -> (ok, evidence, gated record or None)."""
    d, after = F.shadow, e["after"]
    p = pr_of(F, after)
    if not p:
        return False, [f"{short(after)} was pushed as a PR merge but no merged PR names it"], None
    head = (p.get("head") or {}).get("sha")
    want = ctx.state_runs.get((F.slug, v))
    runs = [r for r in runs_of(F, head, "pull_request") if r.get("status") == "completed"]
    green = sorted([r for r in runs if r.get("conclusion") == "success"],
                   key=lambda r: (r.get("id") == want, r.get("created_at") or ""), reverse=True)
    if not green:
        return False, [f"PR #{p['number']} (head {short(head)}): no green Port CI pull_request run "
                       f"({', '.join(str(r.get('id')) + ' ' + str(r.get('conclusion')) for r in runs) or 'none'})"], None
    run = green[0]
    jobs = gh_list(f"repos/{F.slug}/actions/runs/{run['id']}/jobs")
    bad = [f"{j.get('name')}: {j.get('conclusion')}" for j in jobs if j.get("conclusion") not in ("success", "skipped")]
    gates = [j for j in jobs if str(j.get("name", "")).startswith("gates")]
    bad += [f"{j.get('name')}: skipped" for j in gates if j.get("conclusion") == "skipped"]
    if not gates:
        bad.append("no gates job")
    ev = [f"PR #{p['number']} merged {short(after)} at {p.get('merged_at')}; run {run['id']} on head {short(head)}"]
    if bad:
        return False, ev + ["jobs not green: " + "; ".join(bad)], None
    bt = built_tree(F, run["id"])
    tested, how = (bt, "built-tree.txt") if bt else (tree(d, head), "the PR head's tree (artifact unread)")
    if tested != tree(d, after):
        return False, ev + [f"tested tree {short(tested)} ({how}) != merged tree {short(tree(d, after))}: the merge "
                            "is not the tree the run gated"], None
    wf = show(d, after, PORT_CI_WF) or ""
    if gatec_in(wf):
        gc = "Gate C in the run (ci-gates.py --gatec)"
    else:
        with tempfile.TemporaryDirectory() as t_:
            root = export(F, after, pathlib.Path(t_) / "tree")
            probs = mod("fb").gates_problems(d, root, v, after, False, mod("fb").root_files(d, root, False))
        if probs:
            return False, ev + [f"the workflow runs no Gate C and the merge has no current GATES.md record: {probs[0]}"], None
        gc = "Gate C by .port/{v}/GATES.md (the workflow runs none)"
    ev.append(f"jobs {len(jobs)} green ({len(gates)} gates); tested tree {short(tested)} = merged ({how}); {gc}")
    return True, ev, {"merge": after, "tree": tree(d, after), "run": run["id"], "pr": p["number"], "head": head,
                      "at": ts(p.get("merged_at")) or ts(e.get("timestamp"))}


def clean_push(ctx, F, v, e):
    """I1 for one direct push -> (ok, evidence)."""
    d, before, after = F.shadow, e["before"], e["after"]
    if before == ZERO:
        return False, [f"{F.branch(v)} created at {short(after)} in the window: not a round step"]
    if not is_ancestor(d, before, after):
        return False, [f"push {short(before)}..{short(after)} is not a fast-forward"]
    changed = mod("rd").shipped_changes(d, before, after, v)
    if changed:
        return False, [f"direct push {short(before)}..{short(after)} changes shipped files with no PR run: "
                       + ", ".join(changed[:5])]
    ev = f"push {short(before)}..{short(after)} at {e.get('timestamp')}: nothing shipped changed"
    gh_ = [f for f in out_of(d, "diff", "--name-only", before, after).splitlines() if f.startswith(".github/")]
    if PORT_CI_WF in gh_:
        rs = [r for r in runs_of(F, after, "push") if r.get("status") == "completed"]
        if not any(r.get("conclusion") == "success" for r in rs):
            return False, [ev + f"; but the Port CI workflow changed and no green push run on {short(after)}"]
        ev += "; the Port CI workflow changed, its push run green"
    elif gh_:
        ev += f"; .github/ changed outside the Port CI workflow ({', '.join(gh_[:2])}): no Port CI run needed"
    return True, [ev]


def i1_gated(ctx, F, v):
    us = updates(F, v)
    F.gated = getattr(F, "gated", {})
    F.gated[v] = []
    if not us:
        return NA, ["not changed in the window"]
    ok, ev = True, []
    for e in us:
        k = e.get("activity_type")
        if k == "pr_merge" or e.get("after") in F.prs:
            good, lines, rec = gated_merge(ctx, F, v, e)
            if rec:
                F.gated[v].append(rec)
        elif k == "push":
            good, lines = clean_push(ctx, F, v, e)
        else:
            good, lines = False, [f"{k} {short(e.get('before'))}..{short(e.get('after'))} at {e.get('timestamp')}"]
        ok &= good
        ev += lines
    return (PASS if ok else FAIL), ev


FROM_RUN = re.compile(r"jars and gates of run (\d+), the same tree")
TESTED_AS = re.compile(r"tested as (\w+) \((\w+) run (\d+)\), the same tree")


def release_gatec(F, rel):
    """(bad rows, read) of the release's gate summary assets."""
    bad, read = [], 0
    for a in sorted(rel.get("assets") or [], key=lambda a: a.get("name") or ""):
        if not re.fullmatch(r"gates-[\w-]+\.md", a.get("name") or ""):
            continue
        try:
            text = gh_raw(a["url"]).decode("utf-8", "replace")
        except Exception:                        # noqa: BLE001 -- said as unread
            continue
        read += 1
        for line in text.splitlines():
            c = [x.strip() for x in line.strip().strip("|").split("|")]
            if len(c) >= 2 and c[0].startswith("Gate C") and c[1] != "PASS":
                bad.append(f"{a['name']}: {c[0]} {c[1]}")
    return bad, read


def i2_released(ctx, F, v):
    rx = re.compile(mod("rd").TAG_RE.format(v=re.escape(v)))
    rels = [r for r in F.releases if rx.search(r.get("tag_name") or "")]
    gated = getattr(F, "gated", {}).get(v, [])
    merged = any(e.get("activity_type") == "pr_merge" for e in updates(F, v))
    if not rels:
        return (FAIL, ["merged by PR in the window but no release was published for it"]) if merged else \
               (NA, ["no release in the window"])
    ok, ev = True, []
    trees = {g["tree"]: g for g in gated}
    for r in sorted(rels, key=lambda r: r.get("published_at") or ""):
        tag = r["tag_name"]
        c = out_of(F.shadow, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}")
        if not c and re.fullmatch(r"[0-9a-f]{40}", r.get("target_commitish") or ""):
            c = r["target_commitish"]
        t_ = tree(F.shadow, c)
        g = trees.get(t_)
        if not g:
            ok = False
            ev.append(f"{tag} at {short(c)}: its tree {short(t_)} is no tree I1 found gated in the window")
            continue
        body = r.get("body") or ""
        m = FROM_RUN.search(body) or TESTED_AS.search(body)
        src = int(m.group(1) if m.re is FROM_RUN else m.group(3)) if m else None
        if src is not None:
            good = src == g["run"] or src in {x["run"] for x in gated if x["tree"] == t_}
            line = f"{tag} at {short(c)} (tree = PR #{g['pr']}'s merge): released from run {src}" + \
                   ("" if good else f", not the gated run {g['run']}")
        else:
            rr = [x for x in mod_runs(F, c) if x.get("event") == "workflow_dispatch" and x.get("conclusion") == "success"]
            good = bool(rr)
            line = f"{tag} at {short(c)}: " + (f"built and gated by its own release run {rr[0]['id']} on the same tree"
                                                if rr else "no from_run and no green release run on the commit")
        bad, read = release_gatec(F, r)
        if bad:
            good = False
            line += "; Gate C rows not PASS: " + "; ".join(bad[:3])
        elif read:
            line += f"; {read} gate summar{'y' if read == 1 else 'ies'}: Gate C PASS"
        ok &= good
        ev.append(line)
    return (PASS if ok else FAIL), ev


def mod_runs(F, c):
    return [r for r in runs_of(F, c) if r.get("status") == "completed"]


def review_verdict(F, v, c, when):
    """The done list's review part at commit c (fork-branches.py review_items + review_problems, what
    fork-port.py --check-done runs), on an export, origin's branches as they were at `when`. -> (problems, note)."""
    fb = mod("fb")
    d = F.shadow
    with refs_as_of(F, when), tempfile.TemporaryDirectory() as t_:
        root = export(F, c, pathlib.Path(t_) / "tree")
        files = fb.root_files(d, root, False)
        if (root / ".port" / v / "branch.properties").is_file():
            mech = fb.mechanical_files(d, root, v, c, files)
            items = fb.review_items(d, root, v, c, False, files, mech)
            note = f"{len(items)} review item(s) against the parent"
        else:
            base = fb.sv_base(root, v)
            if not base or git(d, "cat-file", "-e", f"{base}^{{commit}}").returncode:
                return [fb.SV_NO_BASE if not base else f"the review base {base} is not a commit"], "single-version"
            items = fb.review_items(d, root, v, c, False, files, set(), sv=base)
            note = f"{len(items)} review item(s) against the base {short(base)}"
        rv = fb.read_file(root / ".port" / v / "REVIEW.md")
        m = re.search(r"(?m)^\s*-\s*commit\s*:\s*`?([0-9a-f]{7,40})", rv)
        if m:
            note += f"; REVIEW.md commit {m.group(1)[:12]}"
        return fb.review_problems(d, root, v, c, False, files, items), note


ROW = re.compile(r"^\|\s*`?([^|`]+?)`?\s*\|\s*(ok|not-ok)\s*\|", re.I | re.M)


def pr_review_verdict(ctx, F, v, e):
    """I3 on a version-branch fork's head, which has no .port/<v>/REVIEW.md: the round's review lives in its PR body
    (round.py pr_body: the review, fix and recheck summaries). Every file the merge changes that ships (round.py
    shipped_changes) needs a row there, its last verdict -- the recheck's over the review's -- ok."""
    p = pr_of(F, e["after"])
    if not p:
        return FAIL, [f"{short(e['after'])}: no PR"]
    body = p.get("body")
    if body is None:
        body = (gh(f"repos/{F.slug}/pulls/{p['number']}") or {}).get("body") or ""
    secs = {}
    for part in ("\n" + body).split("\n### ")[1:]:
        name, _, text = part.partition("\n")
        secs.setdefault(name.strip(), text)
    if "review" not in secs:
        return FAIL, [f"PR #{p['number']}: no review section in the PR body (a head's review is the round's summary there)"]
    verdict = {}
    for sec in ("review", "recheck"):
        for m in ROW.finditer(secs.get(sec, "")):
            verdict[m.group(1).strip()] = m.group(2).lower()
    need = mod("rd").shipped_changes(F.shadow, f"{e['after']}^1", e["after"], v)
    miss = [f for f in need if f not in verdict]
    bad = [f for f in need if verdict.get(f) == "not-ok"]
    res = re.search(r"RESULT:\s*(\w+)", secs["review"])
    ev = [f"PR #{p['number']} body: {len(need)} shipped file(s) changed, {len(verdict)} row(s) in its review"
          + (" and recheck" if "recheck" in secs else "")]
    if not res or res.group(1) != "ok":
        ev.append(f"the review's RESULT is {res.group(1) if res else 'missing'}")
    ev += [f"no review row: {f}" for f in miss[:5]] + [f"not-ok at the end: {f}" for f in bad[:5]]
    return (PASS if len(ev) == 1 else FAIL), ev


def i3_reviewed(ctx, F, v):
    gated = getattr(F, "gated", {}).get(v, [])
    merges = [e for e in updates(F, v) if e.get("activity_type") == "pr_merge" or e.get("after") in F.prs]
    if not merges:
        return NA, ["no PR merged in the window" + (" (direct pushes only: nothing shipped changed)" if updates(F, v) else "")]
    e = merges[-1]
    if F.kind == "branches" and v == F.base:
        return pr_review_verdict(ctx, F, v, e)
    when = ts(e.get("timestamp")) or (gated[-1]["at"] if gated else None)
    probs, note = review_verdict(F, v, e["after"], when)
    if probs:
        return FAIL, [f"at {short(e['after'])}: {note}"] + [f"review: {p}" for p in probs[:6]] + \
               ([f"... {len(probs) - 6} more"] if len(probs) > 6 else [])
    return PASS, [f"at {short(e['after'])}: review current ({note})"]


def i4_lower_first(ctx, F, v):
    us = updates(F, v)
    if F.kind == "single" or v == F.base:
        return NA, ["no version below (head or single-version)"]
    if not us:
        return NA, ["not changed in the window"]
    d = F.shadow
    props = mod("fb").parse_props(show(d, us[-1]["after"], f".port/{v}/branch.properties") or "")
    lower = props.get("parent", "")
    lv = lower[len("neoforge-"):] if lower.startswith("neoforge-") else lower
    if lv not in F.all_versions():
        return NA, [f"its parent {lower or '(none)'} is not a branch of this fork"]
    lower_end = tip_at(F, lv, ctx.end)
    ok, ev, n = True, [], 0
    for e in us:
        if e.get("before") in (None, ZERO):
            continue
        new = out_of(d, "rev-list", e["after"], f"^{e['before']}").split()
        mine = [c for c in new if is_ancestor(d, c, lower_end)]
        when = ts(e.get("timestamp"))
        there = tip_at(F, lv, when)
        late = [c for c in mine if not is_ancestor(d, c, there)]
        n += len(mine)
        if late:
            ok = False
            ev.append(f"{e.get('activity_type')} {short(e['after'])} at {e.get('timestamp')}: {len(late)} commit(s) of "
                      f"{lower} not yet on origin's {lower} (then {short(there)}): " + ", ".join(short(c) for c in late[:5]))
    ev.insert(0, f"{n} commit(s) from {lower} in {len(us)} update(s), each checked against {lower}'s tip at the "
                 "update's push time (GitHub activity)")
    return (PASS if ok else FAIL), ev


def section_of(close, F):
    """The closing comment's `###` section for the fork (its prose), '' when none."""
    out, on = [], False
    for line in (close or "").splitlines():
        if line.startswith("### "):
            on = F.slug.lower() in line.lower() or re.search(rf"\b{re.escape(F.name)}\b", line, re.I) is not None
            continue
        if line.startswith("## ") and not line.startswith("## Round closed"):
            on = False
        if on:
            out.append(line)
    return "\n".join(out)


def names_version(text, v):
    return re.search(rf"(?<![\w.]){re.escape(v)}(?![\w]|\.\d)", text or "") is not None


def i5_dependents(ctx, F, v):
    if not F.library:
        return NA, ["not a library"]
    rx = re.compile(mod("rd").TAG_RE.format(v=re.escape(v)))
    rels = sorted([r for r in F.releases if rx.search(r.get("tag_name") or "")], key=lambda r: r.get("published_at") or "")
    deps = [D for D in ctx.forks.values() if F.slug in D.depends_on]
    if not rels:
        return NA, ["no library release at this version in the window"]
    if not deps:
        return NA, ["no dependent in the set"]
    tag = rels[-1]["tag_name"]
    ok, warn, ev = True, False, []
    fb = mod("fb")
    for D in deps:
        if v not in D.all_versions():
            ev.append(f"{D.slug} builds no {v}: N-A")
            continue
        dt = tip_at(D, v, ctx.end)
        txt = (show(D.shadow, dt, f".port/{v}/branch.properties") or "") + (show(D.shadow, dt, PORT_CI_WF) or "") + \
              (show(D.shadow, dt, "build.gradle") or "")
        pinned = f"library.{F.slug}={tag}" in txt or f"/releases/download/{tag}/" in txt
        if not pinned:
            if names_version(section_of(ctx.close, D), v):
                warn = True
                ev.append(f"{D.slug} {v} at {short(dt)} does not pin {tag}; the closing comment names {v} for it")
            else:
                ok = False
                ev.append(f"{D.slug} {v} at {short(dt)} does not pin {tag}, and the closing comment does not say why")
            continue
        lt = tip_at(F, v, ctx.end)
        with tempfile.TemporaryDirectory() as t_:
            root = export(F, lt, pathlib.Path(t_) / "tree")
            tested, stale = fb.library_status(F.shadow, root, v, lt, False, fb.root_files(F.shadow, root, False))
            try:
                recs = json.loads(fb.read_file(root / ".port" / v / "DEPENDENTS.json") or "[]")
            except ValueError:
                recs = []
        rec = [r for r in recs if isinstance(r, dict) and r.get("library_tag") == tag and r.get("dependent") == D.slug
               and r.get("gate_c") == "PASS"]
        if tested and rec:
            ev.append(f"{D.slug} {v} pins {tag}; {F.name} {v} at {short(lt)} has its Gate C record (library_status: current)")
        else:
            ok = False
            ev.append(f"{D.slug} {v} pins {tag}, but {F.name} {v} at {short(lt)} has " +
                      ("no PASS record for that tag" if not rec else f"a stale record ({stale})"))
    return (FAIL if not ok else WARN if warn else PASS if any("pins" in x for x in ev) else NA), ev


def ledger_at(F, c):
    try:
        return mod("ki").read_ledger(repo=F.shadow, ref=c)
    except Exception:                            # noqa: BLE001 -- an unreadable ledger is said by the caller
        return None


def ledger_writes(ctx, F):
    """[(commit, issue, version, status)] of every status a ledger commit on the head set in the window."""
    if hasattr(F, "_writes"):
        return F._writes
    out = []
    us = updates(F, F.base)
    if us and us[0].get("before") not in (None, ZERO):
        d = F.shadow
        rng = [f"{us[-1]['after']}", f"^{us[0]['before']}"]
        rel = mod("ki").ledger_rel()
        for c in out_of(d, "log", "--format=%H", "--reverse", *rng, "--", rel).split():
            new, old = ledger_at(F, c), ledger_at(F, f"{c}^1")
            if not new:
                continue
            was = {e["id"]: e.get("status") or {} for e in (old or {}).get("issues", [])}
            for e in new.get("issues", []):
                for w, s in sorted((e.get("status") or {}).items()):
                    if was.get(e["id"], {}).get(w) != s:
                        out.append((c, e["id"], w, s))
    F._writes = out
    return out


def i6_ledger(ctx, F, v):
    ev, ok = [], True
    writes = [x for x in ledger_writes(ctx, F) if x[2] == v and x[3].startswith("fixed@")]
    branch_end = tip_at(F, v, ctx.end)
    for c, i, _w, s in writes:
        sha = s.split("@", 1)[1]
        full = out_of(F.shadow, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
        good = bool(full) and is_ancestor(F.shadow, full, branch_end)
        ok &= good
        ev.append(f"{i} {s} (ledger commit {short(c)}): " + ("on" if good else "NOT on") + f" {F.branch(v)} {short(branch_end)}")
    merged = any(e.get("activity_type") == "pr_merge" for e in updates(F, v))
    fx = [x for x in (ctx.frozen_fork(F.slug).get("fixes") or [])]
    order = F.all_versions()
    if merged and fx:
        led = ledger_at(F, tip_at(F, F.base, ctx.end)) or {"issues": []}
        st = {e["id"]: e.get("status") or {} for e in led.get("issues", [])}
        for x in fx:
            at = x.get("at") if x.get("at") in order else F.base
            if order.index(v) < order.index(at):
                continue
            s = st.get(x["id"], {}).get(v)
            if s == "open":
                ok = False
                ev.append(f"{x['id']} (selected, at {at}) is still open at {v}, merged this round")
            else:
                ev.append(f"{x['id']} at {v}: {s or 'no status'}")
    if not ev:
        return NA, ["no ledger write and no selected fix at this version"]
    return (PASS if ok else FAIL), ev


def i7_not_skipped(ctx, F, v):
    if updates(F, v):
        return PASS, ["merged or pushed in the window"]
    if ctx.state_items is not None:
        mine = [it for it in ctx.state_items if it.get("fork") == F.slug and it.get("version") == v
                and not it.get("final")]
        if not mine:
            return PASS, ["no work queued for it (STATE.json)"]
        notes = sorted({str((it.get("result") or {}).get("note") or it.get("note") or "")[:80]
                        for it in mine if it.get("status") == "skipped"} - {""})
        open_ = [it["id"] for it in mine if it.get("status") not in ("ok", "skipped")]
        if not open_:
            return PASS, ["every queued item ok or skipped" + (f" ({'; '.join(notes[:2])})" if notes else "")]
    sec = section_of(ctx.close, F)
    if names_version(sec, v):
        return PASS, ["named in the closing comment's section for the fork"]
    if sec and not any(e.get("activity_type") == "pr_merge" for w in F.all_versions() for e in updates(F, w)):
        return PASS, ["the round merged nothing for this fork, and the closing comment has its section"]
    return WARN, ["neither merged nor pushed, and the closing comment names no reason for it"]


CHECKS = (("I1", i1_gated), ("I2", i2_released), ("I3", i3_reviewed), ("I4", i4_lower_first), ("I5", i5_dependents),
          ("I6", i6_ledger), ("I7", i7_not_skipped))


# ---------------------------------------------------------------- the round

class Ctx:
    def __init__(self, start, end, close, frozen, forks, state=None):
        self.start, self.end, self.close, self.frozen, self.forks = start, end, close, frozen or {}, forks
        self.state_items = None if state is None else state.get("items", [])
        self.state_runs = {}
        for it in (state or {}).get("items", []):
            if it.get("step") == "ci" and (it.get("result") or {}).get("run_id"):
                self.state_runs[(it["fork"], it["version"])] = it["result"]["run_id"]

    def frozen_fork(self, slug):
        return next((f for f in self.frozen.get("forks", []) if f.get("repo") == slug), {})


def plan_of(slug, n):
    """The plan issue, its comments, the frozen plan (known-issues.py read_plan, as `read-plan --issue N` gives it)."""
    ki = mod("ki")
    issue = gh(f"repos/{slug}/issues/{n}")
    comments = ki.gh_pages(f"/repos/{slug}/issues/{n}/comments", gh)
    data = ki.plan_data(issue.get("body") or "")
    owner = data.get("owner") or {}
    frozen = ki.read_plan(issue, comments, ki.owner_check(slug, owner, gh), iso(ts(issue.get("created_at"))))
    return issue, comments, frozen


def audit(plan, forks_arg, state=None, since=None, until=None, keep=False, say=print):
    """-> (report dict, exit code)."""
    t0 = time.monotonic()
    m = re.fullmatch(r"([\w.-]+/[\w.-]+)#(\d+)", plan or "")
    if not m:
        raise SystemExit("round-audit: --plan must be owner/repo#N")
    issue, comments, frozen = plan_of(m.group(1), m.group(2))
    st = json.loads(pathlib.Path(state).read_text(encoding="utf-8")) if state else None
    start = ts(since) or ts((st or {}).get("round_start")) or ts(issue.get("created_at"))
    closing = next((c for c in sorted(comments, key=lambda c: c.get("created_at") or "")
                    if (c.get("body") or "").lstrip().startswith("## Round closed")
                    and (ts(c.get("created_at")) or start) >= start), None)
    end = ts(until) or (ts(closing["created_at"]) if closing else datetime.datetime.now(datetime.timezone.utc))
    close = (closing or {}).get("body", "")
    cfg_forks = {f.get("repo"): f for f in frozen.get("forks", [])}
    forks = {}
    for s in forks_arg:
        slug, sep, d = s.partition("=")
        if not sep or "/" not in slug:
            raise SystemExit(f"round-audit: --fork must be owner/repo=DIR, got {s!r}")
        forks[slug] = Fork(slug, d, cfg_forks.get(slug, {}))
    work = pathlib.Path(tempfile.mkdtemp(prefix="round-audit-")).resolve()
    try:
        for F in forks.values():
            cf = cfg_forks.get(F.slug, {})
            make_shadow(F, work)
            if not cf:                     # the set's config names the fork's role and dependencies
                F.notes.append("not in the plan's fork set")
            collect(F, start, end)
        # the dependencies come from the set's weekly-plan.json (the plan issue names the roles only)
        for F in forks.values():
            for G in forks.values():
                txt = show(G.shadow, f"refs/remotes/origin/{G.head}", ".github/weekly-plan.json")
                if not txt:
                    continue
                try:
                    cfgj = json.loads(txt)
                except ValueError:
                    continue
                me = next((x for x in cfgj.get("forks", []) if x.get("repo") == F.slug), None)
                if me:
                    F.depends_on = list(me.get("depends_on") or F.depends_on)
                    F.library = F.library or me.get("role") == "library"
                    break
        ctx = Ctx(start, end, close, frozen, forks, st)
        rows = []
        for F in forks.values():
            for v in F.all_versions():
                row = {"fork": F.slug, "version": v, "head": v == F.base, "checks": {}}
                for k, fn in CHECKS:
                    try:
                        s_, ev = fn(ctx, F, v)
                    except Exception as e:      # noqa: BLE001 -- an audit that could not judge says so, as a FAIL
                        s_, ev = FAIL, [f"could not be checked: {type(e).__name__}: {e}"[:300]]
                    row["checks"][k] = {"status": s_, "evidence": ev}
                rows.append(row)
        rep = {"plan": plan, "window": {"start": iso(start), "end": iso(end), "closed": bool(closing),
                                        "start_from": "--since" if since else "STATE.json round_start" if st and
                                        st.get("round_start") else "the plan issue's creation"},
               "forks": {F.slug: {"kind": F.kind, "library": F.library, "depends_on": F.depends_on,
                                  "versions": F.all_versions(), "notes": F.notes} for F in forks.values()},
               "rows": rows, "seconds": round(time.monotonic() - t0, 1)}
        bad = any(c["status"] == FAIL for r in rows for c in r["checks"].values())
        return rep, (1 if bad else 0)
    finally:
        if keep:
            say(f"round-audit: shadows kept in {work.as_posix()}")
        else:
            rmtree(work)


def render(rep):
    w = rep["window"]
    out = [f"round-audit: plan {rep['plan']}, window {w['start']} .. {w['end']} (start: {w['start_from']}; "
           + ("end: the closing comment" if w["closed"] else "NOT CLOSED: end is --until or now") + ")"]
    for slug, info in rep["forks"].items():
        out.append("")
        out.append(f"## {slug} ({'version branches' if info['kind'] == 'branches' else 'single-version'}"
                   + (", library" if info["library"] else "") + (f", depends on {', '.join(info['depends_on'])}"
                                                                  if info["depends_on"] else "") + ")")
        for n in info["notes"]:
            out.append(f"note: {n}")
        out.append("| version | " + " | ".join(f"{k} {TITLES[k]}" for k in INVS) + " |")
        out.append("|---" * (len(INVS) + 1) + "|")
        rows = [r for r in rep["rows"] if r["fork"] == slug]
        for r in rows:
            out.append(f"| {r['version']}{' (head)' if r['head'] else ''} | "
                       + " | ".join(r["checks"][k]["status"] for k in INVS) + " |")
        out.append("evidence:")
        for r in rows:
            for k in INVS:
                c = r["checks"][k]
                if c["status"] == NA and k not in ("I1",):
                    continue
                for i, e in enumerate(c["evidence"]):
                    out.append(f"  {r['version']} {k} {c['status']}: {e}" if i == 0 else f"      {e}")
    fails = sum(c["status"] == FAIL for r in rep["rows"] for c in r["checks"].values())
    warns = sum(c["status"] == WARN for r in rep["rows"] for c in r["checks"].values())
    out.append("")
    out.append(f"round-audit: {fails} FAIL, {warns} WARN in {rep['seconds']} s")
    return "\n".join(out)


# ---------------------------------------------------------------- self-check (local bare repos, a stand-in GitHub)

def self_check():
    t0 = time.monotonic()
    global API, RAW
    ok = True
    keep_env = {k: os.environ.get(k) for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                                               "GIT_COMMITTER_EMAIL", "GIT_CONFIG_COUNT")}
    n = int(os.environ.get("GIT_CONFIG_COUNT", "0") or 0)
    for k, v in (("commit.gpgsign", "false"), ("gc.auto", "0"), ("core.autocrlf", "false"), ("init.defaultBranch", "main")):
        os.environ[f"GIT_CONFIG_KEY_{n}"], os.environ[f"GIT_CONFIG_VALUE_{n}"] = k, v
        n += 1
    os.environ["GIT_CONFIG_COUNT"] = str(n)
    os.environ.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                       "GIT_COMMITTER_EMAIL": "t@example.com"})

    def expect(c, msg):
        nonlocal ok
        if not c:
            ok = False
            print("FAIL:", msg)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="round-audit-selfcheck-")).resolve()
    try:
        fx = Fixture(tmp)
        API, RAW = fx.api, fx.raw
        base_rep, rc = fx.run()
        st = lambda rep, f, v, k: next(r for r in rep["rows"] if r["fork"] == f and r["version"] == v)["checks"][k]["status"]
        LIB, DEP = fx.LIB, fx.DEP
        want = {(LIB, "1.21.4"): {"I1": PASS, "I2": PASS, "I3": PASS, "I4": PASS, "I5": PASS, "I6": PASS},
                (LIB, "1.21.5"): {"I1": PASS, "I2": NA, "I3": NA, "I4": PASS, "I7": PASS},
                (DEP, "1.21.4"): {"I1": PASS, "I2": PASS, "I3": PASS, "I5": NA},
                (LIB, "1.21.1"): {"I1": PASS, "I4": NA, "I6": NA},
                (DEP, "1.21.1"): {"I1": PASS, "I2": PASS, "I3": PASS, "I7": PASS}}
        got = {(f, v): {k: st(base_rep, f, v, k) for k in INVS} for f, v in want}
        expect(rc == 0 and all(got[x][k] == s_ for x, w in want.items() for k, s_ in w.items()),
               f"a round that kept every guarantee passes: rc={rc} {got}\n{render(base_rep)}")
        # ---- mutations: each fixture violating one invariant fails exactly there
        cases = [
            ("I1", (LIB, "1.21.4"), "a PR whose CI run is red", lambda f: f.run_conclusion.update({f.r_lib: "failure"})),
            ("I1", (LIB, "1.21.4"), "a run whose gates job was skipped",
             lambda f: f.job_skip.add(f.r_lib)),
            ("I1", (LIB, "1.21.4"), "a run that tested another tree (the base moved: head tree != merge tree)",
             lambda f: f.moved_base()),
            ("I1", (LIB, "1.21.5"), "a direct push that changes shipped code", lambda f: f.shipped_push()),
            ("I1", (LIB, "1.21.5"), "a direct push changing the Port CI workflow with no green push run",
             lambda f: f.workflow_push()),
            ("I2", (LIB, "1.21.4"), "a release from a run that is not the gated one", lambda f: f.rel_body.update(
                {f.lib_tag: "Built from x by the author's own Gradle build (jars and gates of run 999, the same tree)."})),
            ("I2", (LIB, "1.21.4"), "a release whose commit has another tree", lambda f: f.release_elsewhere()),
            ("I2", (DEP, "1.21.4"), "a merged version with no release", lambda f: f.drop_release(f.dep_tag)),
            ("I2", (DEP, "1.21.4"), "a release whose gate summary has Gate C FAIL", lambda f: f.assets.update(
                {f.dep_tag: "| check | result |\n|---|---|\n| Gate C -- real client, `launch` | FAIL | crash |\n"})),
            ("I3", (LIB, "1.21.4"), "a stale review (REVIEW.md names a commit the code changed after)",
             lambda f: f.stale_review()),
            ("I3", (DEP, "1.21.1"), "a head PR whose review row stayed not-ok (no recheck)", head_body(
                "### review\n\nRESULT: ok\n\n| file | ok/not-ok | note |\n|---|---|---|\n"
                "| src/main/java/com/example/mod/Main.java | not-ok | wrong |\n")),
            ("I3", (DEP, "1.21.1"), "a head PR with no review in its body", head_body("no review here\n")),
            ("I4", (LIB, "1.21.5"), "a version pushed with its lower version's commits before they merged",
             lambda f: f.early_push()),
            ("I5", (LIB, "1.21.4"), "a dependent left on the old library release", lambda f: f.no_repin()),
            ("I5", (LIB, "1.21.4"), "a library with no Gate C record for its new release", lambda f: f.no_librecord()),
            ("I6", (LIB, "1.21.4"), "a fixed@ status naming a commit not on the branch", lambda f: f.bad_fixed()),
            ("I6", (LIB, "1.21.4"), "a selected fix left open at a merged version", lambda f: f.left_open()),
            ("I7", (DEP, "1.21.5"), "a version the round neither touched nor named", lambda f: f.silent_skip()),
        ]
        for k, (f_, v_), what, mut in cases:
            fm = Fixture(tmp / f"m-{k}-{len(what)}-{abs(hash(what)) % 997}", mutate=mut)
            API, RAW = fm.api, fm.raw
            rep, rc = fm.run()
            s_ = st(rep, f_, v_, k)
            ev_ = next(r for r in rep["rows"] if r["fork"] == f_ and r["version"] == v_)["checks"][k]["evidence"]
            if any(e.startswith("could not be checked") for e in ev_):
                s_ = f"an error, not a verdict: {ev_}"
            if os.environ.get("ROUND_AUDIT_VERBOSE"):
                print(f"mutation {k} ({what}): {s_}: {' / '.join(ev_)[:300]}")
            want_s = WARN if k == "I7" else FAIL
            others = sorted((r["fork"].split("/")[1], r["version"], kk) for r in rep["rows"]
                            for kk, c in r["checks"].items() if c["status"] == FAIL and (r["fork"], r["version"], kk) != (f_, v_, k))
            expect(s_ == want_s and rc == (0 if k == "I7" else 1),
                   f"mutation {k} ({what}): {f_} {v_} {k} = {s_} (want {want_s}), rc {rc}; other FAILs {others}\n"
                   + render(rep))
        # ---- a single-version fork: only its head; I4 N-A, I1/I3 judged on its one branch
        fs = SingleFixture(tmp / "sv")
        API, RAW = fs.api, fs.raw
        rep, rc = fs.run()
        rows = [r for r in rep["rows"] if r["fork"] == fs.SOLO]
        expect(rc == 0 and len(rows) == 1 and rows[0]["checks"]["I4"]["status"] == NA
               and rows[0]["checks"]["I1"]["status"] == PASS and rows[0]["checks"]["I3"]["status"] == PASS
               and rep["forks"][fs.SOLO]["kind"] == "single",
               f"a single-version fork: its head alone, no lower check: {render(rep)}")
        fs2 = SingleFixture(tmp / "sv2", stale=True)
        API, RAW = fs2.api, fs2.raw
        rep, rc = fs2.run()
        expect(rc == 1 and rep["rows"][0]["checks"]["I3"]["status"] == FAIL,
               f"a single-version fork's stale review fails I3: {render(rep)}")
        # ---- read-only: the clones are byte-for-byte untouched (refs, config, index)
        expect(fx.snapshot() == fx.before, "the fork clones were not written (refs, config, HEAD)")
    finally:
        API = RAW = None
        for k, v in keep_env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        rmtree(tmp)
    print(f"round-audit self-check: {'OK' if ok else 'FAILED'} ({time.monotonic() - t0:.1f} s)")
    return 0 if ok else 1


def plan_body(forks):
    """A plan issue as weekly-plan.py opens it: the data block known-issues.py plan_data reads, and the boxes."""
    ki = mod("ki")
    data = {"repo": forks[0]["repo"], "week": "2026-10-21", "deadline": "2026-10-21T15:00:00Z",
            "owner": {"login": "owner"}, "forks": forks}
    boxes = "".join(f"- [x] {i} <!-- ki:{f['repo']}:{i} default -->\n" for f in forks for i in f["defaults"])
    return {"number": 7, "created_at": at(-5), "user": {"login": "owner"},
            "body": f"{ki.PLAN_MARK}\n{ki.DATA_BEGIN}\n{json.dumps(data)}\n{ki.DATA_END}\n{boxes}"}


def head_body(text):
    """A mutation that only replaces the dependent head's PR body (set before the fixture is built)."""
    def m(_f):
        return None
    m.head_body = text
    return m


T0 = datetime.datetime(2026, 10, 21, 15, 0, tzinfo=datetime.timezone.utc)


def at(minutes):
    return iso(T0 + datetime.timedelta(minutes=minutes))


class Fixture:
    """A library (head 1.21.1, versions 1.21.4 and 1.21.5) and its dependent (head 1.21.1, 1.21.4), bare origins, and
    the stand-in GitHub of a round that kept every guarantee: lib 1.21.4 a PR (a fix) merged, gated and released; lib
    1.21.5 its merge-up pushed directly (a comment-only change: nothing shipped); the dependent's 1.21.4 re-pinned
    to the library's release by PR, gated and released; the library's Gate C record pushed; the ledger written."""
    LIB, DEP = "owner/examplelib", "owner/examplemod"

    def __init__(self, tmp, mutate=None):
        self.tmp = pathlib.Path(tmp)
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.run_conclusion, self.job_skip, self.rel_body, self.assets = {}, set(), {}, {}
        self.activity, self.pulls, self.runs, self.releases = {}, {self.LIB: [], self.DEP: []}, {}, {self.LIB: [], self.DEP: []}
        self.comments, self.mutate = [], mutate
        self.fix_ledger_sha = None
        self.head_body = ("keep-current round\n\n### review\n\nRESULT: ok\nnot_ok: 1\n\n| file | ok/not-ok | note |\n"
                          "|---|---|---|\n| src/main/java/com/example/mod/Main.java | not-ok | the comment |\n\n"
                          "### fix\n\nRESULT: ok\n\n### recheck\n\nRESULT: ok\nnot_ok: 0\n\n"
                          "| file | ok/not-ok | note |\n|---|---|---|\n| src/main/java/com/example/mod/Main.java | ok | fixed |\n")
        if mutate is not None and getattr(mutate, "head_body", None):
            self.head_body = mutate.head_body
        self.build()
        if mutate:
            mutate(self)

    # -- git
    def g(self, d, *a):
        r = git(d, *a)
        if r.returncode:
            raise RuntimeError(f"git {' '.join(a)}: {r.stderr}")
        return r.stdout.strip()

    def w(self, d, path, text):
        p = pathlib.Path(d) / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def commit(self, d, msg, when):
        self.g(d, "add", "-A")
        env_t = when.replace("Z", "+0000")
        r = subprocess.run(["git", "-C", str(d), "commit", "-q", "-m", msg], capture_output=True, text=True,
                           encoding="utf-8", env=dict(os.environ, GIT_AUTHOR_DATE=env_t, GIT_COMMITTER_DATE=env_t))
        if r.returncode:
            raise RuntimeError(r.stderr)
        return self.g(d, "rev-parse", "HEAD")

    def merge(self, d, other, msg, when):
        env_t = when.replace("Z", "+0000")
        r = subprocess.run(["git", "-C", str(d), "merge", "-q", "--no-ff", "-m", msg, other], capture_output=True,
                           text=True, encoding="utf-8", env=dict(os.environ, GIT_AUTHOR_DATE=env_t, GIT_COMMITTER_DATE=env_t))
        if r.returncode:
            raise RuntimeError(r.stderr + r.stdout)
        return self.g(d, "rev-parse", "HEAD")

    def repo(self, name, versions):
        d = self.tmp / name
        d.mkdir()
        self.g(d, "init", "-q", "-b", "neoforge-1.21.1")
        self.w(d, "gradle.properties", "minecraft_version=1.21.1\nmod_id=examplemod\n")
        self.w(d, "src/main/java/com/example/mod/Main.java", "class Main { int a = 1; }\n")
        self.w(d, ".port/KNOWN-ISSUES.json", json.dumps({"schema": 1, "issues": []}) + "\n")
        self.w(d, PORT_CI_WF, "run: python3 ci-gates.py --gatec \"launch,spawn\"\n")
        c0 = self.commit(d, "port", at(-600))
        prev, prev_v = "neoforge-1.21.1", "1.21.1"
        for v in versions:
            self.g(d, "checkout", "-q", "-b", f"neoforge-{v}")
            self.w(d, "gradle.properties", f"minecraft_version={v}\nmod_id=examplemod\n")
            self.w(d, f".port/{v}/branch.properties", f"minecraft_version={v}\nbranch=neoforge-{v}\nparent={prev}\n"
                   f"parent_minecraft_version={prev_v}\nport_branch=neoforge-1.21.1\nport_taken={c0}\n")
            self.commit(d, f"version {v}", at(-500))
            prev, prev_v = f"neoforge-{v}", v
        self.g(d, "checkout", "-q", "neoforge-1.21.1")
        return d

    def push_bare(self, d, slug):
        bare = self.tmp / (slug.split("/")[1] + ".git")
        if not bare.exists():
            self.g(self.tmp, "init", "-q", "--bare", str(bare))
        self.g(d, "push", "-q", "-f", str(bare), "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*")
        return bare

    def tip(self, d, b):
        return self.g(d, "rev-parse", b)

    def ev(self, slug, branch, before, after, kind, when):
        self.activity.setdefault((slug, branch), []).append(
            {"id": len(self.activity) * 100 + len(self.activity.get((slug, branch), [])), "before": before,
             "after": after, "ref": f"refs/heads/{branch}", "timestamp": when, "activity_type": kind})

    def pr_merge(self, slug, d, v, work, number, when, review=True):
        """A work branch from neoforge-<v> merged by PR: its run green, the event recorded."""
        b = f"neoforge-{v}"
        before = self.tip(d, b)
        self.g(d, "checkout", "-q", b)
        head = self.tip(d, work)
        m = self.merge(d, work, f"Merge pull request #{number}", when)
        self.g(d, "update-ref", f"refs/pull/{number}/head", head)
        rid = 1000 + number
        self.runs.setdefault(slug, []).append({"id": rid, "head_sha": head, "event": "pull_request", "status": "completed",
                                               "conclusion": "success", "path": PORT_CI_WF, "created_at": when})
        self.pulls[slug].append({"number": number, "merged_at": when, "updated_at": when, "merge_commit_sha": m,
                                 "base": {"ref": b}, "head": {"sha": head}})
        self.ev(slug, b, before, m, "pr_merge", when)
        return m, rid

    def write_review(self, d, v, reviewed):
        """REVIEW.md with an ok row for every item the done list asks (fork-branches.py review_items)."""
        fb = mod("fb")
        with tempfile.TemporaryDirectory() as t_:
            root = fb.export(d, "HEAD", pathlib.Path(t_) / "tree")
            items = fb.review_items(d, root, v, self.tip(d, "HEAD"), False)
        rows = "".join(f"| {k} | `{f}` | ok | read against the parent |\n" for k, f in sorted(items))
        self.w(d, f".port/{v}/REVIEW.md", f"# Review\n\n- reviewer: a fresh agent\n- date: 2026-10-21\n- commit: {reviewed}\n\n"
               "| kind | file | verdict | note |\n|---|---|---|---|\n" + rows)

    def release(self, slug, d, tag, c, run_id, when, from_run=True):
        self.g(d, "tag", tag, c)
        self.runs.setdefault(slug, []).append({"id": run_id + 5000, "head_sha": c, "event": "workflow_dispatch",
                                               "status": "completed", "conclusion": "success", "path": PORT_CI_WF,
                                               "created_at": when})
        self.releases[slug].append({"tag_name": tag, "target_commitish": c, "published_at": when, "created_at": when,
                                    "draft": False, "body": f"Built from `{c}` (jars and gates of run {run_id}, the same tree)."
                                    if from_run else f"Built from `{c}`.",
                                    "assets": [{"name": "gates-full.md", "url": f"asset://{tag}/gates-full.md"}]})

    def build(self):
        LIB, DEP = self.LIB, self.DEP
        lib = self.lib = self.repo("examplelib", ["1.21.4", "1.21.5"])
        dep = self.dep = self.repo("examplemod", ["1.21.4", "1.21.5"])
        self.w(lib, ".github/weekly-plan.json", json.dumps({"forks": [
            {"repo": LIB, "head": "neoforge-1.21.1", "role": "library"},
            {"repo": DEP, "head": "neoforge-1.21.1", "role": "dependent", "depends_on": [LIB]}]}))
        self.g(lib, "checkout", "-q", "neoforge-1.21.1")
        self.commit(lib, "the set's config", at(-400))
        for d in (lib, dep):
            for v in ("1.21.4", "1.21.5"):
                self.g(d, "checkout", "-q", f"neoforge-{v}")
                self.merge(d, "neoforge-1.21.1", "cascade", at(-390))
            self.g(d, "checkout", "-q", "neoforge-1.21.1")
        self.pre = {s: {b: self.tip(d, b) for b in ("neoforge-1.21.1", "neoforge-1.21.4", "neoforge-1.21.5")}
                    for s, d in ((LIB, lib), (DEP, dep))}
        # the dependent's head: a change by PR at +5 (a version-branch fork's head: its review is in the PR body)
        self.g(dep, "checkout", "-q", "-b", "keep-current/x", "neoforge-1.21.1")
        self.w(dep, "src/main/java/com/example/mod/Main.java", "class Main { int a = 7; }\n")
        self.commit(dep, "a head fix", at(2))
        m_h, r_h = self.pr_merge(DEP, dep, "1.21.1", "keep-current/x", 4, at(5))
        self.pulls[DEP][-1]["body"] = self.head_body
        self.release(DEP, dep, "examplemod-1.0-mc1.21.1", m_h, r_h, at(6))
        # lib 1.21.4: the fix (LIB-1), its review, a PR merged at +60
        self.g(lib, "checkout", "-q", "-b", "keep-current/x-1.21.4", "neoforge-1.21.4")
        self.w(lib, "src/main/java/com/example/mod/Main.java", "class Main { int a = 2; } // LIB-1\n")
        fix = self.lib_fix = self.commit(lib, "LIB-1: the fix", at(20))
        self.write_review(lib, "1.21.4", fix)
        self.commit(lib, "review", at(30))
        self.m_lib, self.r_lib = self.pr_merge(LIB, lib, "1.21.4", "keep-current/x-1.21.4", 1, at(60))
        self.lib_tag = "examplelib-1.0-mc1.21.4"
        self.release(LIB, lib, self.lib_tag, self.m_lib, self.r_lib, at(65))
        # lib 1.21.5: the merge-up of 1.21.4 changes Main.java's code -- so 1.21.5 has the same fix already (a
        # comment-only difference left): nothing shipped changes, pushed directly at +70
        self.g(lib, "checkout", "-q", "neoforge-1.21.5")
        self.w(lib, "src/main/java/com/example/mod/Main.java", "class Main { int a = 2; }\n")
        self.commit(lib, "1.21.5 had the fix", at(-380))
        self.pre[LIB]["neoforge-1.21.5"] = self.tip(lib, "neoforge-1.21.5")
        self.g(lib, "checkout", "-q", "neoforge-1.21.5")
        r = subprocess.run(["git", "-C", str(lib), "merge", "-q", "--no-ff", "-X", "theirs", "-m", "merge-up 1.21.4",
                            "neoforge-1.21.4"], capture_output=True, text=True, encoding="utf-8",
                           env=dict(os.environ, GIT_AUTHOR_DATE=at(69).replace("Z", "+0000"),
                                    GIT_COMMITTER_DATE=at(69).replace("Z", "+0000")))
        if r.returncode:
            raise RuntimeError(r.stderr + r.stdout)
        self.ev(LIB, "neoforge-1.21.5", self.pre[LIB]["neoforge-1.21.5"], self.tip(lib, "neoforge-1.21.5"), "push", at(70))
        # dep 1.21.4: re-pinned to the library's release by PR at +90, released from its run
        self.g(dep, "checkout", "-q", "-b", "keep-current/x-1.21.4", "neoforge-1.21.4")
        p = dep / ".port/1.21.4/branch.properties"
        p.write_text(p.read_text(encoding="utf-8") + f"library.{LIB}={self.lib_tag}\nport_jar.com.example\\:lib="
                     f"https://github.com/{LIB}/releases/download/{self.lib_tag}/lib.jar {'a' * 64}\n", encoding="utf-8")
        c = self.commit(dep, "re-pin", at(80))
        self.write_review(dep, "1.21.4", c)
        self.commit(dep, "review", at(81))
        self.m_dep, self.r_dep = self.pr_merge(DEP, dep, "1.21.4", "keep-current/x-1.21.4", 2, at(90))
        self.dep_tag = "examplemod-1.0-mc1.21.4"
        self.release(DEP, dep, self.dep_tag, self.m_dep, self.r_dep, at(95))
        # lib 1.21.4: the dependent's Gate C record, pushed at +100
        self.g(lib, "checkout", "-q", "neoforge-1.21.4")
        self.write_deprec(lib, self.lib_tag, self.m_lib)
        before = self.tip(lib, "neoforge-1.21.4")
        self.commit(lib, "library record", at(99))
        self.ev(LIB, "neoforge-1.21.4", before, self.tip(lib, "neoforge-1.21.4"), "push", at(100))
        # the ledger: LIB-1 fixed at 1.21.4 and 1.21.5, pushed on the head at +110 (with a plan-config change: .github/
        # outside the Port CI workflow, [skip ci], no run)
        self.g(lib, "checkout", "-q", "neoforge-1.21.1")
        self.w(lib, ".github/weekly-plan.json", (lib / ".github/weekly-plan.json").read_text(encoding="utf-8") + "\n")
        self.write_ledger(lib, {"1.21.4": f"fixed@{fix[:7]}", "1.21.5": f"fixed@{fix[:7]}"})
        before = self.tip(lib, "neoforge-1.21.1")
        self.commit(lib, "ledger", at(109))
        self.ev(LIB, "neoforge-1.21.1", before, self.tip(lib, "neoforge-1.21.1"), "push", at(110))
        self.comments = [{"id": 1, "created_at": at(0), "body": "Round progress: started", "user": {"login": "owner"}},
                         {"id": 2, "created_at": at(120), "user": {"login": "owner"}, "body":
                          f"## Round closed\n\n### {LIB} (the library)\n- 1.21.4 merged, 1.21.5 pushed\n\n"
                          f"### {DEP}\n- 1.21.1 (head): nothing new; 1.21.4 merged; 1.21.5: nothing to do (no library release at it)\n"}]
        self.finish()

    def write_deprec(self, lib, tag, commit_):
        self.w(lib, ".port/1.21.4/DEPENDENTS.json", json.dumps([{"dependent": self.DEP, "dependent_commit": self.m_dep,
                                                                  "gate_c": "PASS", "library": "com.example:lib",
                                                                  "library_tag": tag, "library_commit": commit_,
                                                                  "recorded": "2026-10-21"}], indent=1) + "\n")

    def write_ledger(self, lib, status):
        self.w(lib, ".port/KNOWN-ISSUES.json", json.dumps({"schema": 1, "issues": [
            {"id": "LIB-1", "title": "a defect", "severity": "S3", "source": "review", "lowest": "1.21.4",
             "rubric": {"failure": "functional", "visibility": "common", "reach": "normal", "workaround": True},
             "status": status, "files": ["src/main/java/com/example/mod/Main.java"]}]}, indent=1) + "\n")

    def finish(self):
        for s, d in ((self.LIB, self.lib), (self.DEP, self.dep)):
            self.g(d, "checkout", "-q", "neoforge-1.21.1")
            bare = self.push_bare(d, s)
            # the clone a person has: origin's branches as remote-tracking refs, never the round's own later state
            if not git(d, "remote").stdout.strip():
                self.g(d, "remote", "add", "origin", bare.as_posix())
            self.g(d, "fetch", "-q", "origin")
        self.before = self.snapshot()

    def snapshot(self):
        return [(out_of(d, "for-each-ref", "--format=%(refname) %(objectname)"), out_of(d, "config", "--list", "--local"),
                 out_of(d, "rev-parse", "HEAD"), out_of(d, "status", "--porcelain")) for d in (self.lib, self.dep)]

    # -- the mutations
    def moved_base(self):
        """The merge took a base commit made after the run: the merged tree is not the PR head's."""
        self.g(self.lib, "checkout", "-q", "neoforge-1.21.4")
        self.g(self.lib, "reset", "-q", "--hard", f"{self.m_lib}^1")
        self.w(self.lib, "src/main/java/com/example/mod/Other.java", "class Other {}\n")
        self.commit(self.lib, "a base commit after the run", at(50))
        m = self.merge(self.lib, self.g(self.lib, "rev-parse", f"{self.m_lib}^2"), "Merge pull request #1", at(60))
        self.retarget(self.m_lib, m)
        self.finish()

    def retarget(self, old, new):
        for p in self.pulls[self.LIB]:
            if p["merge_commit_sha"] == old:
                p["merge_commit_sha"] = new
        for evs in self.activity.values():
            for e in evs:
                e["after"] = new if e["after"] == old else e["after"]
                e["before"] = new if e["before"] == old else e["before"]

    def shipped_push(self):
        self.g(self.lib, "checkout", "-q", "neoforge-1.21.5")
        before = self.tip(self.lib, "neoforge-1.21.5")
        self.w(self.lib, "src/main/java/com/example/mod/Main.java", "class Main { int a = 3; }\n")
        self.commit(self.lib, "a code change pushed directly", at(71))
        self.ev(self.LIB, "neoforge-1.21.5", before, self.tip(self.lib, "neoforge-1.21.5"), "push", at(72))
        self.finish()

    def workflow_push(self):
        self.g(self.lib, "checkout", "-q", "neoforge-1.21.5")
        before = self.tip(self.lib, "neoforge-1.21.5")
        self.w(self.lib, PORT_CI_WF, "run: python3 ci-gates.py --gatec \"launch\"\n")
        self.commit(self.lib, "the workflow regenerated", at(71))
        self.ev(self.LIB, "neoforge-1.21.5", before, self.tip(self.lib, "neoforge-1.21.5"), "push", at(72))
        self.finish()

    def release_elsewhere(self):
        rel = next(r for r in self.releases[self.LIB] if r["tag_name"] == self.lib_tag)
        self.g(self.lib, "tag", "-f", self.lib_tag, self.lib_fix)
        rel["target_commitish"] = self.lib_fix
        self.finish()

    def drop_release(self, tag):
        for s in self.releases:
            self.releases[s] = [r for r in self.releases[s] if r["tag_name"] != tag]

    def stale_review(self):
        """The review names the fix commit; a later commit in the PR changed the code again."""
        self.g(self.lib, "checkout", "-q", "neoforge-1.21.4")
        self.g(self.lib, "reset", "-q", "--hard", f"{self.m_lib}^1")
        self.g(self.lib, "checkout", "-q", "keep-current/x-1.21.4")
        self.w(self.lib, "src/main/java/com/example/mod/Main.java", "class Main { int a = 4; } // LIB-1\n")
        self.commit(self.lib, "a change after the review", at(40))
        head = self.tip(self.lib, "HEAD")
        self.g(self.lib, "checkout", "-q", "neoforge-1.21.4")
        m = self.merge(self.lib, "keep-current/x-1.21.4", "Merge pull request #1", at(60))
        self.g(self.lib, "update-ref", "refs/pull/1/head", head)
        for r in self.runs[self.LIB]:
            if r["id"] == self.r_lib:
                r["head_sha"] = head
        self.pulls[self.LIB][0]["head"]["sha"] = head
        self.retarget(self.m_lib, m)
        self.g(self.lib, "tag", "-f", self.lib_tag, m)
        self.releases[self.LIB][0]["target_commitish"] = m
        self.write_deprec(self.lib, self.lib_tag, m)
        self.commit(self.lib, "library record", at(99))
        self.activity[(self.LIB, "neoforge-1.21.4")][-1:] = []
        self.ev(self.LIB, "neoforge-1.21.4", m, self.tip(self.lib, "neoforge-1.21.4"), "push", at(100))
        self.finish()

    def early_push(self):
        """1.21.5's push at +50, before 1.21.4's PR merged (+60): it carried 1.21.4's unmerged commits."""
        e = self.activity[(self.LIB, "neoforge-1.21.5")][-1]
        e["timestamp"] = at(50)

    def no_repin(self):
        self.g(self.dep, "checkout", "-q", "neoforge-1.21.4")
        p = self.dep / ".port/1.21.4/branch.properties"
        p.write_text(p.read_text(encoding="utf-8").replace(self.lib_tag, "examplelib-0.9-mc1.21.4"), encoding="utf-8")
        before = self.tip(self.dep, "neoforge-1.21.4")
        self.commit(self.dep, "back to the old pin", at(111))
        self.ev(self.DEP, "neoforge-1.21.4", before, self.tip(self.dep, "neoforge-1.21.4"), "push", at(112))
        self.comments[-1]["body"] = self.comments[-1]["body"].replace("1.21.4 merged;", "merged;")
        self.finish()

    def no_librecord(self):
        self.g(self.lib, "checkout", "-q", "neoforge-1.21.4")
        before = self.tip(self.lib, "neoforge-1.21.4")
        self.write_deprec(self.lib, "examplelib-0.9-mc1.21.4", self.m_lib)
        self.commit(self.lib, "a record for another tag", at(101))
        self.ev(self.LIB, "neoforge-1.21.4", before, self.tip(self.lib, "neoforge-1.21.4"), "push", at(102))
        self.finish()

    def bad_fixed(self):
        self.g(self.lib, "checkout", "-q", "neoforge-1.21.1")
        before = self.tip(self.lib, "neoforge-1.21.1")
        self.write_ledger(self.lib, {"1.21.4": f"fixed@{self.pre[self.LIB]['neoforge-1.21.5'][:7]}",
                                     "1.21.5": f"fixed@{self.lib_fix[:7]}"})
        self.commit(self.lib, "ledger: a wrong commit", at(111))
        self.ev(self.LIB, "neoforge-1.21.1", before, self.tip(self.lib, "neoforge-1.21.1"), "push", at(112))
        self.finish()

    def left_open(self):
        self.g(self.lib, "checkout", "-q", "neoforge-1.21.1")
        before = self.tip(self.lib, "neoforge-1.21.1")
        self.write_ledger(self.lib, {"1.21.4": "open", "1.21.5": f"fixed@{self.lib_fix[:7]}"})
        self.commit(self.lib, "ledger: back to open", at(111))
        self.ev(self.LIB, "neoforge-1.21.1", before, self.tip(self.lib, "neoforge-1.21.1"), "push", at(112))
        self.finish()

    def silent_skip(self):
        self.comments[-1]["body"] = self.comments[-1]["body"].replace("; 1.21.5: nothing to do (no library release at it)", "")

    # -- the stand-in GitHub
    def plan_issue(self):
        return plan_body([{"repo": self.LIB, "head": "neoforge-1.21.1", "role": "library", "defaults": ["LIB-1"],
                           "candidates": [], "fix_at": {"LIB-1": "1.21.4"},
                           "cascade": {"range": "neoforge-1.21.1..1.21.5", "decisions": []}},
                          {"repo": self.DEP, "head": "neoforge-1.21.1", "role": "dependent", "defaults": [],
                           "candidates": [], "cascade": {"range": "neoforge-1.21.1..1.21.5", "decisions": []}}])

    def api(self, path):
        path = path.lstrip("/")
        p, _, q = path.partition("?")
        qs = dict(x.split("=", 1) for x in q.split("&") if "=" in x)
        if int(qs.get("page", "1")) > 1:
            return []
        m = re.fullmatch(r"repos/([^/]+/[^/]+)/(.*)", p)
        slug, rest = m.group(1), m.group(2)
        if rest == "issues/7":
            return self.plan_issue()
        if rest == "issues/7/comments":
            return list(self.comments)
        if rest == "activity":
            return list(reversed(self.activity.get((slug, qs["ref"].split("refs/heads/", 1)[1]), [])))
        if rest == "pulls":
            return list(self.pulls.get(slug, []))
        if rest == "releases":
            out = []
            for r in self.releases.get(slug, []):
                r = dict(r, body=self.rel_body.get(r["tag_name"], r["body"]))
                out.append(r)
            return out
        if rest == "actions/runs":
            rs = [dict(r, conclusion=self.run_conclusion.get(r["id"], r["conclusion"])) for r in self.runs.get(slug, [])
                  if r["head_sha"] == qs.get("head_sha")]
            return {"workflow_runs": rs}
        m2 = re.fullmatch(r"actions/runs/(\d+)/jobs", rest)
        if m2:
            rid = int(m2.group(1))
            return {"jobs": [{"name": "gates (full)", "conclusion": "skipped" if rid in self.job_skip else "success"},
                             {"name": "gates (minimal)", "conclusion": "success"}, {"name": "release", "conclusion": "skipped"}]}
        if re.fullmatch(r"actions/runs/\d+/artifacts", rest):
            return {"artifacts": []}
        if re.fullmatch(r"commits/\w+/pulls", rest):
            return []
        if re.fullmatch(r"collaborators/[^/]+/permission", rest):
            return {"permission": "admin"}
        raise RuntimeError(f"the stand-in GitHub has no {path}")

    def raw(self, url, accept):
        m = re.fullmatch(r"asset://([^/]+)/(.+)", url)
        if not m:
            raise OSError("no such download")
        return self.assets.get(m.group(1), "| check | result |\n|---|---|\n| Gate C -- real client, `launch` | PASS | ok |\n").encode("utf-8")

    def run(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            return audit(f"{self.LIB}#7", [f"{self.LIB}={self.lib}", f"{self.DEP}={self.dep}"])


class SingleFixture(Fixture):
    """One single-version fork (its head neoforge-1.21.1 only): a PR merged, gated, reviewed against its base."""
    SOLO = "owner/examplesolo"

    def __init__(self, tmp, stale=False):
        self.stale = stale
        Fixture.__init__(self, tmp)

    def build(self):
        S = self.SOLO
        d = self.lib = self.dep = self.tmp / "examplesolo"
        d.mkdir()
        self.pulls, self.releases = {S: []}, {S: []}
        self.g(d, "init", "-q", "-b", "neoforge-1.21.1")
        self.w(d, "gradle.properties", "minecraft_version=1.21.1\nmod_id=examplemod\n")
        self.w(d, "src/main/java/com/example/mod/Main.java", "class Main { int a = 1; }\n")
        self.w(d, PORT_CI_WF, "run: python3 ci-gates.py --gatec \"launch\"\n")
        self.w(d, ".github/weekly-plan.json", json.dumps({"forks": [{"repo": S, "head": "neoforge-1.21.1"}]}))
        base = self.commit(d, "port", at(-600))
        self.g(d, "checkout", "-q", "-b", "keep-current/x")
        self.w(d, "src/main/java/com/example/mod/Main.java", "class Main { int a = 2; }\n")
        c = self.commit(d, "a fix", at(20))
        fb = mod("fb")
        with tempfile.TemporaryDirectory() as t_:
            root = fb.export(d, "HEAD", pathlib.Path(t_) / "tree")
            items = fb.review_items(d, root, "1.21.1", c, False, sv=base)
        rows = "".join(f"| {k} | `{f}` | ok | read |\n" for k, f in sorted(items))
        self.w(d, ".port/1.21.1/REVIEW.md", f"# Review\n\n- base: {base}\n- reviewer: a fresh agent\n- date: 2026-10-21\n"
               f"- commit: {c}\n\n| kind | file | verdict | note |\n|---|---|---|---|\n" + rows)
        self.commit(d, "review", at(30))
        if self.stale:
            self.w(d, "src/main/java/com/example/mod/Main.java", "class Main { int a = 5; }\n")
            self.commit(d, "after the review", at(35))
        m, rid = self.pr_merge(S, d, "1.21.1", "keep-current/x", 3, at(60))
        self.release(S, d, "examplesolo-1.0-mc1.21.1", m, rid, at(65))
        self.comments = [{"id": 2, "created_at": at(120), "user": {"login": "owner"},
                          "body": f"## Round closed\n\n### {S}\n- merged\n"}]
        self.g(d, "checkout", "-q", "neoforge-1.21.1")
        bare = self.push_bare(d, S)
        self.g(d, "remote", "add", "origin", bare.as_posix())
        self.g(d, "fetch", "-q", "origin")
        self.before = [None]

    def plan_issue(self):
        return plan_body([{"repo": self.SOLO, "head": "neoforge-1.21.1", "defaults": [], "candidates": []}])

    def run(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            return audit(f"{self.SOLO}#7", [f"{self.SOLO}={self.lib}"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--plan", help="owner/repo#N: the round's plan issue")
    ap.add_argument("--fork", action="append", default=[], help="owner/repo=DIR: each fork's clone (only read)")
    ap.add_argument("--state", help="the round's round/STATE.json, when it is at hand")
    ap.add_argument("--since", help="the window's start (ISO time; default STATE.json's, else the plan issue's creation)")
    ap.add_argument("--until", help="the window's end when the plan has no closing comment")
    ap.add_argument("--json", action="store_true", help="the report as JSON")
    ap.add_argument("--keep", action="store_true", help="keep the shadow repositories (their path is printed)")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args(argv)
    if a.self_check:
        return self_check()
    if not a.plan or not a.fork:
        ap.error("--plan and at least one --fork are required")
    try:
        rep, rc = audit(a.plan, a.fork, a.state, a.since, a.until, a.keep)
    except SystemExit as e:
        print(e, file=sys.stderr)
        return 2
    print(json.dumps(rep, indent=1) if a.json else render(rep))
    return rc


if __name__ == "__main__":
    sys.exit(main())
