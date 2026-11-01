#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Audit a finished keep-current round: did it keep its guarantees? Read-only (GitHub and the fork clones).

    python3 tools/round-audit.py --plan owner/repo#N --fork owner/repo=DIR [--fork ...] [--state round/STATE.json]
                                 [--since TIME] [--until TIME] [--pin REV] [--json] [--keep]
    python3 tools/round-audit.py --self-check

WHY. tools/round.py drives the weekly round; its speed-ups (skipping redundant local gate re-runs, reusing reviews,
overlapping versions) must not lose what the round promises. This is an independent check of a round that already
ran, from what it left behind -- the plan issue, GitHub's record of the forks (branch pushes, PRs, Actions runs and
their jobs and steps, releases) and the commits -- so it works on rounds that ran in other containers, without their
round/STATE.json (given with --state it is used too: the round's own start, its CI run ids, its queue for I5/I7).

THE RULE: missing or unreadable evidence is never a pass. A check that cannot read what it needs says UNKNOWN, which
exits 1 like a FAIL; the closing comment is the round agent's prose and is never the verdict (it is quoted as
supporting text only).

INDEPENDENCE. What the audit polices is defined here, not imported from what it polices: the direct-push ALLOWLIST
(below, frozen from what round.py's record steps write), the release tag pattern (TAG_RE), the Gate C phases (read
from the workflow's ci-gates.py command). The review and library-record checks ARE fork-branches.py's own
(review_items/review_problems, library_status, gates_problems: the definition of a complete review), and are run
twice -- with this checkout's fork-branches.py and with the PINNED migrator's (PINNED_MIGRATOR, origin/main when this
was written; --pin REV) -- and a disagreement is a FAIL, so a later change that narrows them cannot pass silently.

THE WINDOW. --since, else STATE.json's round_start, else the plan issue's creation; to the first comment starting
`## Round closed` (else --until, else now, said as not closed). Every branch update, PR merge and release inside it
is the round's.

READ-ONLY. Nothing is fetched into, checked out in or pushed from a fork clone: each fork gets a throwaway SHADOW
repository under a temporary directory that borrows the clone's objects (objects/info/alternates) and fetches origin's
branches, tags and pull heads into itself (only what the clone lacks is downloaded); every git read, export and
historical ref state below is the shadow's. The clone is read only for its objects and its origin URL.

GitHub: REST with GITHUB_TOKEN / GH_TOKEN, else gh (known-issues.py gh_api, as every round tool). The branch history
is the repository activity API (`/activity`, every page through its Link cursor: each push with before/after sha,
its kind -- push, force_push, pr_merge, branch_creation -- and GitHub's own timestamp). A branch's history is trusted
only when it reaches back before the window (or to the branch's creation), chains without a gap and ends at origin's
tip now; otherwise I1 FAILs for it and I3/I4/I7 say UNKNOWN. A download (a release asset, an artifact) goes to
api.github.com only, and follows one redirect only to GitHub's storage hosts (REDIRECT_HOSTS).

THE INVARIANTS, per fork and per version branch (and head) (PASS / FAIL / UNKNOWN / N-A, each with its evidence;
exit 1 on any FAIL or UNKNOWN):
  I1 gated tree   the branch's history is complete (above), and every PR merged in the window into the branch has its
                  merge in that history (a merge GitHub's history does not show is a FAIL); each update: a PR merge
                  needs a green Port CI pull_request run for that PR (its pull_requests name it, or -- GitHub empties
                  the list once the head branch is deleted -- it ran on the PR's head branch) on the PR's final head,
                  every job success or skipped, at least one gates job, none skipped, each one's ci-gates step
                  ("Build and gates") success in the jobs API; the run's tested tree the merge commit's (tree hashes:
                  the gate logs' built-tree.txt when the artifact can be read, else the PR head's tree, else -- a
                  head behind its base -- the run's test merge when the merge commit's parents are exactly the base
                  as it was when the run started (the branch history) and the PR head; otherwise "the base moved"
                  (the merge's first parent against that base) and only a difference inside the allowlist passes);
                  and Gate C in it: the workflow at the merge has a
                  ci-gates.py command with --gatec naming at least one phase (comments stripped; `none`, empty and
                  `${{ ... }}` values are no Gate C), else the merge's .port/<v>/GATES.md record (gates_problems,
                  this checkout's and the pinned). A direct push (the round's nochange path, the ledger, a library
                  record) must be a fast-forward changing only the ALLOWLIST: the ledger (.port/KNOWN-ISSUES.json,
                  .port/<mod>/KNOWN-ISSUES.json), root README.md (the Ports block), .port/<v>/*.md records,
                  .port/<v>/DEPENDENTS.json and INVENTORY-CHECK.json, .port/CHANGE-REVIEWS.md and AUTHORS-REVIEWS.md,
                  another version's .port/<w>/ copy (v's build reads .port/<v>/ only), .port/<v>/branch.properties
                  keys port_taken / upstream / upstream_commit, and the .github/ files no Port CI run reads
                  (weekly-plan.json and .yml, port-publish*, port-install.json). Any other .github/ change needs a
                  green push run; anything else (.port/branch.gradle, a build key, a pin, code, resources) a PR.
  I2 released     each release published in the window for the version (TAG_RE) points at a commit whose tree I1
                  found gated and has a green workflow_dispatch (release) run on that commit; it either names a
                  gated run as its source (`jars and gates of run N, the same tree` in the body: port-ci.yml
                  from_run, whose release job checked that run's built-tree.txt) with its release run's jobs green,
                  or has no such marker and its release run gated it itself (I1's jobs check: gates jobs success,
                  ci-gates steps success). The last PR merge of the window that ships (more than the allowlist)
                  must have a release with its tree. Its gate summaries (gates-*.md assets) must exist and be readable
                  (UNKNOWN otherwise), each with Gate C rows all PASS (a library's `skipped` row too). A version
                  merged by PR with no release is a FAIL.
  I3 reviewed     at EVERY merged commit of the window, the review part of the done list -- fork-branches.py
                  review_items and review_problems, what `fork-port.py --check-done` runs (a single-version tree
                  against the base its REVIEW.md records) -- on an export, origin's branches set to where they were at
                  the merge (a branch that did not exist then: removed), with this checkout's and the pinned
                  fork-branches.py: a problem, or the two disagreeing, is a FAIL. Gate records are not part of it. A
                  version-branch fork's head has no REVIEW.md (the round writes its review into the PR body, round.py
                  pr_body): every file the merge changed outside the allowlist needs a row in the body's review or
                  recheck section, its last verdict ok, and the review's RESULT ok; the rows name files, not
                  commits, so a file a PR commit after the last commit the summaries name (`commit:` lines) changed
                  must be in the recheck (not determinable when they name none: said). N-A for a branch the round
                  only pushed to.
  I4 lower first  every commit an update brought onto neoforge-<v> that belongs to the version below (its
                  branch.properties `parent`, read at the new tip) -- on that branch at the window's end, or on the
                  head of any PR into it (a lower PR closed unmerged or squashed: its commits never reach the
                  branch) -- was on origin's lower branch at the update's push time (GitHub's activity timestamp,
                  not committer dates). N-A on a head and a single-version fork (nothing below).
  I5 dependents   on a library's version: the newest library release of the window at that version is pinned by
                  each dependent's matching branch at the window's end (a non-comment line of its branch.properties,
                  port-ci.yml or build.gradle: `library.<owner/repo>=TAG`, or a github.com/<owner/repo>/releases/
                  download/TAG/ URL), and the library's branch then has the dependent's PASS Gate C record for it
                  (.port/<v>/DEPENDENTS.json, library_status current, this checkout's and the pinned). Not pinned is a
                  FAIL when a re-pin was due: always for a release at a version the dependent builds (a
                  single-version library's at a version branch excepted), or, with --state, when the queue has a
                  repin item there. A dependent that does not build the version is N-A.
  I6 ledger       the head's history is readable; every status the round's ledger commits on the head set to
                  fixed@<sha> (each against its first parent; an unreadable one is a FAIL) names a commit on that
                  version's branch at the window's end; and for each fix the plan selected (read-plan's `fixes`, at
                  their version), every version at or above it the round merged by PR has a status other than
                  `open` in the head's ledger at the window's end (no status, or an unreadable ledger, is a FAIL).
  I7 not skipped  a version neither merged nor pushed is a PASS only on evidence that it had no work: STATE.json's
                  queue (no item, or every item ok/skipped), or the frozen plan (no authors' code taken, no selected
                  fix at or below it, no dependency bump at it, no release of a library it depends on at it in the
                  window). Otherwise a FAIL.

KNOWN LIMITS. The built-tree.txt artifact is often unreachable from a cloud session (its storage host is denied by
the proxy), and then the head-tree method is used and said; I3 judges the review against the parent's branch as it was
at the merge only to GitHub's push-time precision; I7's frozen-plan evidence does not know the shared-library sync
init reads from the clones (a version the sync alone would have changed and the round skipped reads as "no work");
the pinned cross-check needs the pinned commit in this checkout (UNKNOWN otherwise).

Exit: 0 every invariant PASS / WARN / N-A, 1 a FAIL or UNKNOWN, 2 bad input. Standard library only.
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
PASS, FAIL, WARN, NA, UNKNOWN = "PASS", "FAIL", "WARN", "N-A", "UNKNOWN"
BAD = (FAIL, UNKNOWN)            # both exit 1: evidence that is missing or unreadable is never a pass
# The migrator commit whose fork-branches.py the review and library-record checks are ALSO run with (--pin): a later
# change that narrows those definitions disagrees with it, and the disagreement is a FAIL. origin/main when this
# audit was written.
PINNED_MIGRATOR = "e7ed78c762fd93ec060843bae03f0407dd0a85f5"
# A release tag of version v (frozen here, not read from round.py: the audit's own definition)
TAG_RE = r"-mc{v}(?:-r\d+)?$"
# THE ALLOWLIST of what a direct push (no PR, no Port CI pull_request run) may change -- frozen here, independent of
# round.py's and fork-branches.py's definitions of "shipped", from what round.py's record steps write: the ledger
# (.port/KNOWN-ISSUES.json and a multi-mod's .port/<mod>/KNOWN-ISSUES.json), the README Ports block (root README.md),
# the version's records (.port/<v>/*.md: DRIFT, COST, TEST-ITEMS, GATES, DEPS, REVIEW; DEPENDENTS.json from a library
# record, INVENTORY-CHECK.json from the done list), the change reviews (.port/CHANGE-REVIEWS.md,
# .port/AUTHORS-REVIEWS.md), every OTHER version's .port/<w>/ copy (a version branch carries its ancestors' records;
# its build reads only .port/<v>/), the bookkeeping keys of .port/<v>/branch.properties (ALLOWED_KEYS: what a merge-up
# moves) and the weekly plan's own files. Everything else -- .port/branch.gradle, a build key, a pin, any source,
# resource or build file -- needs a PR and its gated run.
ALLOWED_ROOT = re.compile(r"^(?:README\.md|\.port/KNOWN-ISSUES\.json|\.port/[^/]+/KNOWN-ISSUES\.json|"
                          r"\.port/CHANGE-REVIEWS\.md|\.port/AUTHORS-REVIEWS\.md)$")
ALLOWED_OWN = re.compile(r"^(?:[^/]+\.md|DEPENDENTS\.json|INVENTORY-CHECK\.json|KNOWN-ISSUES\.json)$")
ALLOWED_KEYS = ("port_taken", "upstream", "upstream_commit")
# .github/ files no Port CI run reads (the weekly plan, the publish and install configs): any other .github/ change
# on a direct push needs a green push run
GITHUB_NON_CI = re.compile(r"^\.github/(?:weekly-plan\.json|workflows/weekly-plan\.yml|port-publish\.json|"
                           r"port-publish/.*|port-install\.json)$")
GATEC_PHASES = re.compile(r"^[a-z][a-z0-9_-]*$")
# hosts a download may be redirected to (release assets, artifact zips), and only those
REDIRECT_HOSTS = ("githubusercontent.com", "blob.core.windows.net", "amazonaws.com")
_MODS = {}
API = None          # the self-check's stand-in GitHub: callable(path) -> parsed JSON
RAW = None          # and its downloads: callable(url, accept) -> bytes


def mod(key):
    """The round's own tools, loaded as modules (round.py's mod()): structure (versions, ladder, exports, ledger
    reading). What the audit polices is its own (ALLOWLIST, TAG_RE) or cross-checked against PINNED (pinned())."""
    files = {"rd": "round.py", "fb": "fork-branches.py", "ki": "known-issues.py"}
    if key not in _MODS:
        _MODS[key] = load_from(TOOLS / files[key], f"round_audit_{key}")
    return _MODS[key]


def load_from(path, name):
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


PIN = {"rev": PINNED_MIGRATOR, "mod": None, "dir": None, "why": None}


def pinned():
    """fork-branches.py at the pinned migrator commit (git archive of tools/ and templates/ from this checkout), or
    None with PIN["why"] set when that commit cannot be read."""
    if PIN["mod"] is None and PIN["why"] is None:
        d = pathlib.Path(tempfile.mkdtemp(prefix="round-audit-pin-")).resolve()
        PIN["dir"] = d
        import atexit
        atexit.register(lambda: d.exists() and rmtree(d))
        r = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", PIN["rev"], "tools", "templates"],
                           capture_output=True)
        if r.returncode:
            PIN["why"] = f"the pinned migrator commit {PIN['rev'][:12]} is not in {ROOT.as_posix()} (fetch it, or --pin REV)"
            return None
        import tarfile
        with tarfile.open(fileobj=io.BytesIO(r.stdout)) as t:
            for m in t.getmembers():
                if m.name.startswith("/") or ".." in pathlib.PurePosixPath(m.name).parts or not (m.isfile() or m.isdir()):
                    continue
                if hasattr(tarfile, "data_filter"):
                    t.extract(m, str(d), filter="data")
                else:
                    t.extract(m, str(d))
        PIN["mod"] = load_from(d / "tools/fork-branches.py", "round_audit_pinned_fb")
    return PIN["mod"]


def gh(path):
    return API(path) if API else mod("ki").gh_api(path)


def gh_list(path, stop=None, limit=30):
    """A page-numbered list (per_page=100); stop(item) True ends it early."""
    out = []
    for page in range(1, limit + 1):
        got = gh(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")
        got = got.get("workflow_runs", got.get("jobs", [])) if isinstance(got, dict) else got
        out += got
        if len(got) < 100 or (stop and got and stop(got[-1])):
            break
    return out


def gh_cursor(path, limit=50):
    """A cursor-paged list (the activity API: its Link header's rel="next"), every page. -> (items, complete)."""
    out, nxt, n = [], f"{path}{'&' if '?' in path else '?'}per_page=100", 0
    while nxt and n < limit:
        n += 1
        if API:
            got = API(nxt)
            items, nxt = (got.get("__items__", []), got.get("__next__")) if isinstance(got, dict) else (got, None)
        else:
            items, nxt = rest_link(nxt)
        out += items
    return out, not nxt


def rest_link(path):
    """One REST GET with its Link header (GITHUB_TOKEN / GH_TOKEN, else `gh api -i`). -> (items, next path or None)."""
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        import urllib.request
        req = urllib.request.Request("https://api.github.com/" + path.lstrip("/"), headers={
            "Accept": "application/vnd.github+json", "User-Agent": "mmvu-round-audit", "Authorization": f"Bearer {tok}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            body, link = r.read().decode("utf-8"), r.headers.get("Link") or ""
    else:
        r = subprocess.run(["gh", "api", "-i", path], capture_output=True, text=True, encoding="utf-8", timeout=120)
        if r.returncode:
            raise RuntimeError(f"gh api {path}: {(r.stderr or r.stdout).strip()[-300:]}")
        head, _, body = r.stdout.partition("\r\n\r\n") if "\r\n\r\n" in r.stdout else r.stdout.partition("\n\n")
        link = next((ln.split(":", 1)[1] for ln in head.splitlines() if ln.lower().startswith("link:")), "")
    return json.loads(body), next_link(path, link)


def next_link(path, link):
    """The rel="next" path of a Link header, named as `path` names its repository: GitHub's link names it by its
    numeric id (repositories/<id>/), which a proxy may not serve."""
    m = re.search(r'<https://api\.github\.com/([^>]+)>;\s*rel="next"', link or "")
    nxt = m.group(1) if m else None
    own = re.match(r"/?(repos/[^/]+/[^/]+)/", path)
    if nxt and own:
        nxt = re.sub(r"^repositories/\d+/", own.group(1) + "/", nxt)
    return nxt


def host_ok(url, first):
    from urllib.parse import urlparse
    h = (urlparse(url).hostname or "").lower()
    if first:
        return h == "api.github.com"
    return any(h == x or h.endswith("." + x) for x in REDIRECT_HOSTS)


def gh_raw(url, accept="application/octet-stream"):
    """A download from api.github.com (a release asset, an artifact zip): the token goes to api.github.com only, the
    one redirect it follows must be to a storage host GitHub serves these from (REDIRECT_HOSTS), fetched without the
    token. -> bytes (raises when it cannot be read)."""
    if not host_ok(url, True):
        raise ValueError(f"not an api.github.com download: {url}")
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
        loc = e.headers.get("Location") if e.code in (301, 302, 303, 307, 308) else None
        if not loc:
            raise
        if not host_ok(loc, False):
            raise ValueError(f"a redirect to an unexpected host: {loc[:80]}")
        with urllib.request.build_opener(_NoRedirect).open(urllib.request.Request(loc), timeout=120) as r:
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


def props_of(text):
    """branch.properties -> {key: value}, comments and blank lines dropped (the audit's own reader)."""
    out = {}
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s or s.startswith(("#", "!")) or "=" not in s:
            continue
        k, _, v = s.partition("=")
        out[k.strip().replace("\\:", ":")] = v.strip()
    return out


def not_allowed(d, a, b, v):
    """The paths between commits a and b a direct push may NOT change (the ALLOWLIST above), with why."""
    out = []
    for f in out_of(d, "diff", "--name-only", "--no-renames", a, b).splitlines():
        m = re.match(r"^\.port/([^/]+)/(.+)$", f)
        if ALLOWED_ROOT.match(f) or GITHUB_NON_CI.match(f):
            continue
        if m and m.group(1) != v and m.group(1) not in ("",):
            if re.match(r"^\d", m.group(1)):        # another version's record copy (its build reads .port/<v>/ only)
                continue
        if m and m.group(1) == v:
            if ALLOWED_OWN.match(m.group(2)):
                continue
            if m.group(2) == "branch.properties":
                pa, pb = props_of(show(d, a, f)), props_of(show(d, b, f))
                keys = sorted(k for k in set(pa) | set(pb) if pa.get(k) != pb.get(k) and k not in ALLOWED_KEYS)
                if not keys:
                    continue
                out.append(f"{f} ({', '.join(keys[:4])})")
                continue
        out.append(f)
    return out


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
        self.events, self.prs, self.all_prs, self.releases, self.notes = {}, {}, [], [], []
        self.history = {}           # version -> None (complete) or why the branch history cannot be trusted
        self.gated = {}

    def branch(self, v):
        return self.head if v == self.base else f"neoforge-{v}"

    def all_versions(self):
        return [self.base] + self.versions

    def version_of(self, branch):
        if branch == self.head:
            return self.base
        return branch[len("neoforge-"):] if branch.startswith("neoforge-") else None


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
        F.notes.append("origin's pull heads could not be fetched: I4 reads only the PR heads the API names")
    if r.returncode:
        raise SystemExit(f"round-audit: {F.slug}: fetching origin into the shadow failed: {r.stderr.strip()[-300:]}")
    F.shadow = d
    return d


def history_problem(F, b, evs, start):
    """Why the activity history of branch b cannot be trusted for the window, or None: it must reach back before the
    window (or to the branch's creation), chain without a gap (each update's before = the previous one's after) and
    end at the branch's tip now."""
    if not evs:
        return None       # never updated in GitHub's history: the window cannot have changed it (the PR cross-check
        # below still fails a merge into it)
    first = evs[0]
    if first.get("activity_type") != "branch_creation" and first.get("before") != ZERO and \
            (ts(first.get("timestamp")) or start) >= start:
        return (f"{b}: GitHub's activity history starts at {first.get('timestamp')}, inside the window: the updates "
                "before it are not known")
    for x, y in zip(evs, evs[1:]):
        if y.get("before") != x.get("after") and y.get("activity_type") != "force_push":
            return f"{b}: a gap in GitHub's activity history between {short(x.get('after'))} and {short(y.get('before'))}"
    tip = out_of(F.shadow, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{b}")
    if tip and evs[-1].get("after") != tip:
        return f"{b}: GitHub's activity history ends at {short(evs[-1].get('after'))}, but origin's {b} is at {short(tip)}"
    return None


def collect(F, start, end):
    """The fork's kind, versions, branch history, PRs and releases."""
    fb, rd = mod("fb"), mod("rd")
    d = F.shadow
    have = fb.version_branches(d)
    F.kind = "branches" if have else "single"
    F.versions = rd.ladder(d, F.base) if have else []
    for v in F.all_versions():
        b = F.branch(v)
        evs, complete = gh_cursor(f"repos/{F.slug}/activity?ref=refs/heads/{b}")
        evs = sorted(evs, key=lambda e: (e.get("timestamp") or "", e.get("id") or 0))
        F.events[v] = {"all": evs, "window": [e for e in evs if start <= (ts(e.get("timestamp")) or start) <= end]}
        F.history[v] = None if complete else f"{b}: the activity history was cut off after 50 pages"
        F.history[v] = F.history[v] or history_problem(F, b, evs, start)
    F.prs, F.all_prs = {}, []
    for p in gh_list(f"repos/{F.slug}/pulls?state=all&sort=updated&direction=desc",
                     stop=lambda p: (ts(p.get("updated_at")) or end) < start):
        F.all_prs.append(p)
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
    what the first later update replaced (None: the branch did not exist), else the branch as it is now. Callers
    check F.history[v] first: a history that cannot be trusted gives no tip."""
    evs = F.events.get(v, {}).get("all", [])
    before = [e for e in evs if (ts(e.get("timestamp")) or when) <= when]
    if before:
        a = before[-1].get("after")
        return None if a == ZERO else a
    if evs:
        b = evs[0].get("before")
        return None if b in (ZERO, None) else b
    return out_of(F.shadow, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{F.branch(v)}") or None


@contextlib.contextmanager
def refs_as_of(F, when):
    """The shadow's origin branches set to where they were at `when` (a branch that did not exist then: deleted);
    restored afterwards."""
    d = F.shadow
    saved = {}
    for line in out_of(d, "for-each-ref", "--format=%(refname) %(objectname)", "refs/remotes/origin/").splitlines():
        ref, c = line.rsplit(" ", 1)
        if "/pull/" not in ref:
            saved[ref] = c
    try:
        for v in F.all_versions():
            c = tip_at(F, v, when)
            ref = f"refs/remotes/origin/{F.branch(v)}"
            if c:
                git(d, "update-ref", ref, c)
            elif F.events.get(v, {}).get("all"):
                git(d, "update-ref", "-d", ref)
        yield
    finally:
        for ref, c in saved.items():
            git(d, "update-ref", ref, c)


def updates(F, v):
    return F.events.get(v, {}).get("window", [])


def is_merge(F, e):
    return e.get("activity_type") == "pr_merge" or e.get("after") in F.prs


def shipping_merge(F, v, e):
    """Is a PR merge more than records? (what it brought -- its first parent to the merge -- outside the ALLOWLIST).
    A records-only PR (a publish config, a ledger edit) needs its gated run (I1) but no release or review."""
    return bool(not_allowed(F.shadow, f"{e['after']}^1", e["after"], v))


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


def gatec_phases(wf):
    """The Gate C phases the workflow's ci-gates.py command runs ([] when none): comments stripped, continuation lines
    joined, `none`, empty and expression values (`${{ ... }}`: not known from the file) dropped."""
    lines = []
    for ln in (wf or "").splitlines():
        s = ln.split(" #", 1)[0] if not ln.lstrip().startswith("#") else ""
        lines.append(s)
    text = re.sub(r"\\\n\s*", " ", "\n".join(lines))
    out = []
    for cmd in [ln for ln in text.splitlines() if "ci-gates.py" in ln]:
        for m in re.finditer(r"--gatec[ =]+(\"[^\"]*\"|'[^']*'|\S+)", cmd):
            val = m.group(1).strip("\"'")
            if "${{" in val or "$" in val:
                continue
            out += [p for p in val.split(",") if p and p != "none" and GATEC_PHASES.match(p)]
    return sorted(set(out))


def jobs_green(F, run_id, gates_required=True):
    """(ok, evidence) of a Port CI run's jobs: every job success or skipped; with gates_required, at least one gates
    job, none skipped, and each one's ci-gates step ("Build and gates") concluded success."""
    jobs = gh_list(f"repos/{F.slug}/actions/runs/{run_id}/jobs")
    bad = [f"{j.get('name')}: {j.get('conclusion')}" for j in jobs if j.get("conclusion") not in ("success", "skipped")]
    gates = [j for j in jobs if str(j.get("name", "")).startswith("gates")]
    if gates_required:
        bad += [f"{j.get('name')}: skipped" for j in gates if j.get("conclusion") == "skipped"]
        if not gates:
            bad.append("no gates job")
        for j in gates:
            st = [s for s in j.get("steps") or [] if "gates" in str(s.get("name", "")).lower()
                  and "summary" not in str(s.get("name", "")).lower()]
            if not st:
                bad.append(f"{j.get('name')}: no ci-gates step in the jobs API")
            elif any(s.get("conclusion") != "success" for s in st):
                bad.append(f"{j.get('name')}: step " + "; ".join(f"{s.get('name')} {s.get('conclusion')}" for s in st))
    ev = f"{len(jobs)} job(s), {len(gates)} gates job(s)" + (", each ci-gates step success" if gates and not bad else "")
    return not bad, ev, bad


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
    return mod("fb").export(F.shadow, c, pathlib.Path(into))


def tree_diff_not_allowed(d, a, b, v):
    """not_allowed() between two trees' commits (the merge and the tested head)."""
    return not_allowed(d, a, b, v)


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

    def for_pr(r):
        """The run is this PR's: its pull_requests name it, or (GitHub empties the list once the head branch is gone)
        it ran on the PR's head branch."""
        prs = [x.get("number") for x in r.get("pull_requests") or []]
        return p["number"] in prs if prs else r.get("head_branch") == (p.get("head") or {}).get("ref")
    green = sorted([r for r in runs if r.get("conclusion") == "success" and for_pr(r)],
                   key=lambda r: (r.get("id") == want, r.get("created_at") or ""), reverse=True)
    if not green:
        return False, [f"PR #{p['number']} (head {short(head)}): no green Port CI pull_request run for this PR "
                       f"({', '.join(str(r.get('id')) + ' ' + str(r.get('conclusion')) for r in runs) or 'none'})"], None
    run = green[0]
    ev = [f"PR #{p['number']} merged {short(after)} at {p.get('merged_at')}; run {run['id']} on head {short(head)}"]
    ok, jev, bad = jobs_green(F, run["id"])
    if not ok:
        return False, ev + ["jobs not green: " + "; ".join(bad)], None
    bt = built_tree(F, run["id"])
    tested, how = (bt, "built-tree.txt") if bt else (tree(d, head), "the PR head's tree (artifact unread)")
    if tested != tree(d, after):
        if bt:
            return False, ev + [f"tested tree {short(tested)} (built-tree.txt) != merged tree {short(tree(d, after))}"], None
        # a pull_request run tests GitHub's merge of the PR head into its base as the base was when the run started
        # (the branch history says where): a merge commit with exactly those parents has that merge's tree
        p1, p2 = out_of(d, "rev-parse", after + "^1"), out_of(d, "rev-parse", after + "^2")
        base_then = None if F.history.get(v) else tip_at(F, v, ts(run.get("created_at")))
        if base_then and p1 == base_then and p2 == head:
            how = (f"the run's test merge: the merge's parents are the base when the run started ({short(p1)}, from the "
                   f"branch history) and the PR head")
            tested = tree(d, after)
        else:
            rest = not_allowed(d, head, after, v)
            moved = (f"the base moved (the merge's first parent {short(p1)}, the base when the run started "
                     f"{short(base_then) if base_then else 'unknown: the branch history cannot be trusted'})")
            if rest:
                return False, ev + [f"{moved}: the merged tree {short(tree(d, after))} is not the tree the run gated "
                                    f"(the PR head's {short(tested)}); it differs in {', '.join(rest[:4])}"], None
            how += f"; {moved}, by records only"
    phases = gatec_phases(show(d, after, PORT_CI_WF))
    if phases:
        gc = f"Gate C in the run: ci-gates.py --gatec {','.join(phases)}"
    else:
        with tempfile.TemporaryDirectory() as t_:
            root = export(F, after, pathlib.Path(t_) / "tree")
            files = mod("fb").root_files(d, root, False)
            probs = mod("fb").gates_problems(d, root, v, after, False, files)
            pf = pinned()
            pprobs = pf.gates_problems(d, root, v, after, False, files) if pf else None
        if probs or pprobs is None or bool(pprobs) != bool(probs):
            return False, ev + ["the workflow runs no Gate C (no --gatec phase in its ci-gates.py command) and the "
                                "merge's .port/<v>/GATES.md record is " + (probs[0] if probs else
                                PIN["why"] if pprobs is None else f"judged differently by the pinned migrator: {pprobs[:1]}")], None
        gc = f"Gate C by .port/{v}/GATES.md (the workflow runs none)"
    ev.append(f"jobs: {jev}; tested tree {short(tested)} = merged ({how}); {gc}")
    return True, ev, {"merge": after, "tree": tree(d, after), "run": run["id"], "pr": p["number"], "head": head,
                      "at": ts(p.get("merged_at")) or ts(e.get("timestamp"))}


def clean_push(ctx, F, v, e):
    """I1 for one direct push -> (ok, evidence): a fast-forward that changes only what the ALLOWLIST allows; a
    .github/ change outside GITHUB_NON_CI needs a green push run."""
    d, before, after = F.shadow, e["before"], e["after"]
    if before == ZERO:
        return False, [f"{F.branch(v)} created at {short(after)} in the window: not a round step"]
    if not is_ancestor(d, before, after):
        return False, [f"push {short(before)}..{short(after)} is not a fast-forward"]
    bad = not_allowed(d, before, after, v)
    ci = [f for f in bad if f.startswith(".github/")]
    other = [f for f in bad if not f.startswith(".github/")]
    if other:
        return False, [f"direct push {short(before)}..{short(after)} changes what only a PR may change (no gated run): "
                       + ", ".join(other[:5]) + (" ..." if len(other) > 5 else "")]
    ev = f"push {short(before)}..{short(after)} at {e.get('timestamp')}: records only (the allowlist)"
    if ci:
        rs = [r for r in runs_of(F, after, "push") if r.get("status") == "completed"]
        if not any(r.get("conclusion") == "success" for r in rs):
            return False, [ev + f"; but {', '.join(ci[:2])} changed and no green push run on {short(after)}"]
        ev += f"; {', '.join(ci[:2])} changed, its push run green"
    return True, [ev]


def i1_gated(ctx, F, v):
    us = updates(F, v)
    F.gated[v] = []
    ok, ev = True, []
    if F.history.get(v):
        ok = False
        ev.append(f"history: {F.history[v]}")
    seen = {e.get("after") for e in us}
    for p in sorted(F.prs.values(), key=lambda p: p.get("merged_at") or ""):
        if F.version_of((p.get("base") or {}).get("ref", "")) == v and p.get("merge_commit_sha") not in seen:
            ok = False
            ev.append(f"PR #{p['number']} merged into {F.branch(v)} at {p.get('merged_at')} ({short(p.get('merge_commit_sha'))}), "
                      "but no branch update in GitHub's activity history names that merge")
    if not us and ok:
        return NA, ["not changed in the window (activity history complete, no PR merged into it)"]
    for e in us:
        k = e.get("activity_type")
        if is_merge(F, e):
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


def pr_set_rows(ctx, F):
    """PRs merged in the window into a branch that is not one of the fork's versions: a FAIL row of their own."""
    known = {F.branch(v) for v in F.all_versions()}
    return [p for p in F.prs.values() if (p.get("base") or {}).get("ref") not in known]


FROM_RUN = re.compile(r"jars and gates of run (\d+), the same tree")
TESTED_AS = re.compile(r"tested as (\w+) \((\w+) run (\d+)\), the same tree")


def release_gatec(F, rel):
    """(bad rows, read, problem or None) of the release's gate summary assets: none, or one not downloadable, is a
    problem."""
    bad, read, names = [], 0, []
    for a in sorted(rel.get("assets") or [], key=lambda a: a.get("name") or ""):
        if not re.fullmatch(r"gates-[\w-]+\.md", a.get("name") or ""):
            continue
        names.append(a["name"])
        try:
            text = gh_raw(a["url"]).decode("utf-8", "replace")
        except Exception as e:                   # noqa: BLE001 -- unreadable evidence: said as such
            return bad, read, f"{a['name']} could not be downloaded ({type(e).__name__})"
        read += 1
        rows = 0
        for line in text.splitlines():
            c = [x.strip() for x in line.strip().strip("|").split("|")]
            if len(c) >= 2 and c[0].startswith("Gate C"):
                rows += 1
                if c[1] not in ("PASS", "skipped"):
                    bad.append(f"{a['name']}: {c[0]} {c[1]}")
        if not rows:
            bad.append(f"{a['name']}: no Gate C row")
    if not names:
        return bad, read, "the release has no gate summary (gates-*.md)"
    return bad, read, None


def i2_released(ctx, F, v):
    rx = re.compile(TAG_RE.format(v=re.escape(v)))
    rels = [r for r in F.releases if rx.search(r.get("tag_name") or "")]
    gated = F.gated.get(v, [])
    merged = any(is_merge(F, e) and shipping_merge(F, v, e) for e in updates(F, v))
    if not rels:
        return (FAIL, ["merged by PR in the window (more than records) but no release was published for it"]) if merged \
            else (NA, ["no release in the window, and no PR merged" + (" but records-only ones (the allowlist)"
                                                                       if any(is_merge(F, e) for e in updates(F, v)) else "")])
    ok, unknown, ev = True, False, []
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
        disp = sorted([x for x in mod_runs(F, c) if x.get("event") == "workflow_dispatch" and x.get("conclusion") == "success"],
                      key=lambda x: x.get("created_at") or "")
        if not disp:
            ok = False
            ev.append(f"{tag} at {short(c)}: no green release (workflow_dispatch) run on the commit")
            continue
        if src is not None:
            good = src in {x["run"] for x in gated if x["tree"] == t_}
            rok, _rev, rbad = jobs_green(F, disp[-1]["id"], gates_required=False)
            good &= rok
            line = (f"{tag} at {short(c)} (tree = PR #{g['pr']}'s merge): released by run {disp[-1]['id']} from run {src}"
                    + ("" if src in {x['run'] for x in gated} else f", not a gated run ({g['run']})")
                    + ("" if rok else f"; its release run is not green: {'; '.join(rbad[:2])}"))
        else:
            rok, rev_, rbad = jobs_green(F, disp[-1]["id"], gates_required=True)
            good = rok
            line = f"{tag} at {short(c)}: no from_run marker; " + (
                f"built and gated by its own release run {disp[-1]['id']} ({rev_})" if rok
                else f"its release run {disp[-1]['id']} did not gate it: {'; '.join(rbad[:3])}")
        bad, read, prob = release_gatec(F, r)
        if prob:
            unknown = True
            line += f"; {prob}"
        if bad:
            good = False
            line += "; Gate C rows not PASS: " + "; ".join(bad[:3])
        elif read:
            line += f"; {read} gate summar{'y' if read == 1 else 'ies'}: Gate C PASS"
        ok &= good
        ev.append(line)
    ship = [e["after"] for e in updates(F, v) if is_merge(F, e) and shipping_merge(F, v, e)]
    last = next((g for g in reversed(gated) if ship and g["merge"] == ship[-1]), None)
    rel_trees = {tree(F.shadow, out_of(F.shadow, "rev-parse", "--verify", "--quiet", f"refs/tags/{r['tag_name']}^{{commit}}")
                      or r.get("target_commitish")) for r in rels}
    if ship and (not last or last["tree"] not in rel_trees):
        ok = False
        ev.append(f"the last merge that ships ({short(ship[-1])}" + (f", PR #{last['pr']}" if last else "") +
                  ") was not released: no release of the window has its tree")
    return (FAIL if not ok else UNKNOWN if unknown else PASS), ev


def mod_runs(F, c):
    return [r for r in runs_of(F, c) if r.get("status") == "completed"]


def review_problems_with(fb, F, v, c, root, files):
    d = F.shadow
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
    return fb.review_problems(d, root, v, c, False, files, items), note


def review_verdict(F, v, c, when):
    """The done list's review part at commit c (fork-branches.py review_items + review_problems, what
    fork-port.py --check-done runs), on an export, origin's branches as they were at `when` -- with this checkout's
    fork-branches.py AND the pinned migrator's. -> (problems, note)."""
    fb, pf = mod("fb"), pinned()
    with refs_as_of(F, when), tempfile.TemporaryDirectory() as t_:
        root = export(F, c, pathlib.Path(t_) / "tree")
        files = fb.root_files(F.shadow, root, False)
        probs, note = review_problems_with(fb, F, v, c, root, files)
        if pf is None:
            return probs + [f"UNKNOWN: {PIN['why']}"], note
        pprobs, _n = review_problems_with(pf, F, v, c, root, files)
        rv = fb.read_file(root / ".port" / v / "REVIEW.md")
    m = re.search(r"(?m)^\s*-\s*commit\s*:\s*`?([0-9a-f]{7,40})", rv)
    if m:
        note += f"; REVIEW.md commit {m.group(1)[:12]}"
    if bool(probs) != bool(pprobs):
        probs = list(probs) + [f"this checkout's and the pinned migrator's ({PIN['rev'][:12]}) review checks disagree: "
                               f"{len(probs)} vs {len(pprobs)} problem(s)" + (f", pinned: {pprobs[0]}" if pprobs else "")]
    return probs, note


ROW = re.compile(r"^\|\s*`?([^|`]+?)`?\s*\|\s*(ok|not-ok)\s*\|", re.I | re.M)


def pr_review_verdict(ctx, F, v, e):
    """I3 on a version-branch fork's head, which has no .port/<v>/REVIEW.md: the round's review lives in its PR body
    (round.py pr_body: the review, fix and recheck summaries). Every file the merge changes outside the ALLOWLIST
    needs a row there, its last verdict -- the recheck's over the review's -- ok, and the review's RESULT ok. The rows
    name files, not commits: a file changed by a commit after the last one the summaries name (backlog, authors and
    fix `commit:` lines) must be in the recheck too, when the PR's commit list shows that order."""
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
    verdict, rechecked = {}, set()
    for sec in ("review", "recheck"):
        for m in ROW.finditer(secs.get(sec, "")):
            verdict[m.group(1).strip()] = m.group(2).lower()
            if sec == "recheck":
                rechecked.add(m.group(1).strip())
    need = not_allowed(F.shadow, f"{e['after']}^1", e["after"], v)
    need = [f.split(" (", 1)[0] for f in need]
    miss = [f for f in need if f not in verdict]
    bad = [f for f in need if verdict.get(f) == "not-ok"]
    res = re.search(r"RESULT:\s*(\w+)", secs["review"])
    ev = [f"PR #{p['number']} body: {len(need)} file(s) outside the allowlist changed, {len(verdict)} row(s) in its "
          "review" + (" and recheck" if "recheck" in secs else "") + " (rows name files, not commits)"]
    fails = []
    if not res or res.group(1) != "ok":
        fails.append(f"the review's RESULT is {res.group(1) if res else 'missing'}")
    fails += [f"no review row: {f}" for f in miss[:5]] + [f"not-ok at the end: {f}" for f in bad[:5]]
    named = [c for s in secs.values() for c in re.findall(r"(?m)^commit:\s*([0-9a-f]{7,40})", s)]
    commits = [c.get("sha") for c in gh_list(f"repos/{F.slug}/pulls/{p['number']}/commits")] if named else []
    idx = [i for i, c in enumerate(commits) for n in named if c and c.startswith(n)]
    if named and idx:
        later = commits[max(idx) + 1:]
        for c in later:
            for f in not_allowed(F.shadow, f"{c}^1", c, v):
                f = f.split(" (", 1)[0]
                if f not in rechecked:
                    fails.append(f"{f} changed by {short(c)}, after the last commit the summaries name, and not in the recheck")
    elif named:
        ev.append("the commits the summaries name are not in the PR's commit list: the order is not determinable")
    else:
        ev.append("the summaries name no commit: the order is not determinable")
    return (FAIL if fails else PASS), ev + fails


def i3_reviewed(ctx, F, v):
    allm = [e for e in updates(F, v) if is_merge(F, e)]
    merges = [e for e in allm if shipping_merge(F, v, e)]
    if not merges:
        return NA, ["no PR merged in the window" + (" (direct pushes only: records)" if updates(F, v) else "")
                    if not allm else f"{len(allm)} PR merge(s), records only (the allowlist): no review needed"]
    if F.history.get(v):
        return UNKNOWN, [f"history: {F.history[v]}"]
    out, ok = [], True
    for e in merges:                          # every merge of the window, not only the last
        if F.kind == "branches" and v == F.base:
            s_, ev = pr_review_verdict(ctx, F, v, e)
            ok &= s_ == PASS
            out += ev
            continue
        probs, note = review_verdict(F, v, e["after"], ts(e.get("timestamp")))
        if probs:
            ok = False
            out += [f"at {short(e['after'])}: {note}"] + [f"review: {p}" for p in probs[:6]] + \
                   ([f"... {len(probs) - 6} more"] if len(probs) > 6 else [])
        else:
            out.append(f"at {short(e['after'])}: review current ({note}; this checkout's and the pinned check agree)")
    return (PASS if ok else FAIL), out


def revs(d, *a):
    return set(out_of(d, "rev-list", *a).split())


def i4_lower_first(ctx, F, v):
    us = updates(F, v)
    if F.kind == "single" or v == F.base:
        return NA, ["no version below (head or single-version)"]
    if not us:
        return NA, ["not changed in the window"]
    d = F.shadow
    lower = props_of(show(d, us[-1]["after"], f".port/{v}/branch.properties")).get("parent", "")
    lv = F.version_of(lower)
    if lv not in F.all_versions():
        return FAIL, [f"its parent {lower or '(none)'} is not a branch of this fork: nothing to check against"]
    if F.history.get(lv):
        return UNKNOWN, [f"the lower branch's history cannot be trusted: {F.history[lv]}"]
    lower_end = tip_at(F, lv, ctx.end)
    heads = sorted({(p.get("head") or {}).get("sha") for p in F.all_prs
                    if (p.get("base") or {}).get("ref") == lower and (p.get("head") or {}).get("sha")} - {None})
    heads = [h for h in heads if git(d, "cat-file", "-e", f"{h}^{{commit}}").returncode == 0]
    ok, ev, n = True, [], 0
    for e in us:
        if e.get("before") in (None, ZERO):
            continue
        there = tip_at(F, lv, ts(e.get("timestamp")))
        excl = [f"^{there}"] if there else []
        new = revs(d, e["after"], f"^{e['before']}", *excl)
        lowerish = revs(d, lower_end, *excl) if lower_end else set()
        for h in heads:
            lowerish |= revs(d, h, *excl)
        late = sorted(new & lowerish)
        n += 1
        if late:
            ok = False
            ev.append(f"{e.get('activity_type')} {short(e['after'])} at {e.get('timestamp')}: {len(late)} commit(s) of "
                      f"{lower} (its branch or a PR into it) not on origin's {lower} then ({short(there)}): "
                      + ", ".join(short(c) for c in late[:5]))
    ev.insert(0, f"{n} update(s): every commit of {lower} (its branch at the window's end and {len(heads)} PR head(s) "
                 "into it) each brought was on origin's lower branch at the update's push time (GitHub activity)"
              if ok else f"{n} update(s) checked against {lower} (its branch and {len(heads)} PR head(s) into it)")
    return (PASS if ok else FAIL), ev


def section_of(close, F):
    """The closing comment's `###` section for the fork: the heading names its owner/repo (prose; never a verdict)."""
    out, on = [], False
    for line in (close or "").splitlines():
        if line.startswith("### "):
            on = re.search(rf"(?<![\w./-]){re.escape(F.slug)}(?![\w./-])", line, re.I) is not None
            continue
        if line.startswith("## ") and not line.startswith("## Round closed"):
            on = False
        if on:
            out.append(line)
    return "\n".join(out)


def names_version(text, v):
    return re.search(rf"(?<![\w.]){re.escape(v)}(?![\w]|\.\d)", text or "") is not None


def pins(text, slug, tag):
    """Does a pin file (branch.properties, port-ci.yml, build.gradle) pin the library's tag? Comment lines never
    count; the library's own owner/repo is part of the match."""
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s or s.startswith(("#", "//", "!")):
            continue
        if s.replace("\\:", ":").startswith(f"library.{slug}={tag}") or \
                f"github.com/{slug}/releases/download/{tag}/".lower() in s.lower():
            return True
    return False


def i5_dependents(ctx, F, v):
    if not F.library:
        return NA, ["not a library"]
    rx = re.compile(TAG_RE.format(v=re.escape(v)))
    rels = sorted([r for r in F.releases if rx.search(r.get("tag_name") or "")], key=lambda r: r.get("published_at") or "")
    deps = [D for D in ctx.forks.values() if F.slug in D.depends_on]
    if not rels:
        return NA, ["no library release at this version in the window"]
    if not deps:
        return NA, ["no dependent in the set"]
    tag = rels[-1]["tag_name"]
    ok, ev, any_pin = True, [], False
    fb, pf = mod("fb"), pinned()
    for D in deps:
        if v not in D.all_versions():
            ev.append(f"{D.slug} builds no {v}: N-A")
            continue
        if D.history.get(v):
            ok = False
            ev.append(f"{D.slug} {v}: {D.history[v]}")
            continue
        dt = tip_at(D, v, ctx.end)
        txt = "\n".join((show(D.shadow, dt, p) or "") for p in (f".port/{v}/branch.properties", PORT_CI_WF, "build.gradle"))
        if not pins(txt, F.slug, tag):
            due, why = repin_due(ctx, F, D, v)
            say = section_of(ctx.close, D)
            prose = " (the closing comment names the version for it: prose, not evidence)" if names_version(say, v) else ""
            if due:
                ok = False
                ev.append(f"{D.slug} {v} at {short(dt)} does not pin {tag}: a re-pin was due ({why}){prose}")
            else:
                ev.append(f"{D.slug} {v} at {short(dt)} does not pin {tag}: no re-pin was due ({why})")
            continue
        any_pin = True
        lt = tip_at(F, v, ctx.end)
        with tempfile.TemporaryDirectory() as t_:
            root = export(F, lt, pathlib.Path(t_) / "tree")
            files = fb.root_files(F.shadow, root, False)
            tested, stale = fb.library_status(F.shadow, root, v, lt, False, files)
            ptested = pf.library_status(F.shadow, root, v, lt, False, files)[0] if pf else None
            try:
                recs = json.loads(fb.read_file(root / ".port" / v / "DEPENDENTS.json") or "[]")
            except ValueError:
                recs = None
        rec = [r for r in recs or [] if isinstance(r, dict) and r.get("library_tag") == tag and r.get("dependent") == D.slug
               and r.get("gate_c") == "PASS"]
        if recs is None:
            ok = False
            ev.append(f"{F.name} {v} at {short(lt)}: DEPENDENTS.json is not JSON")
        elif tested and rec and ptested:
            ev.append(f"{D.slug} {v} pins {tag}; {F.name} {v} at {short(lt)} has its Gate C record (library_status: "
                      "current, this checkout's and the pinned check)")
        else:
            ok = False
            ev.append(f"{D.slug} {v} pins {tag}, but {F.name} {v} at {short(lt)} has " +
                      ("no PASS record for that tag" if not rec else f"a stale record ({stale})" if not tested else
                       PIN["why"] if ptested is None else "a record the pinned migrator's library_status does not accept"))
    return (FAIL if not ok else PASS if any_pin else NA), ev


def repin_due(ctx, F, D, v):
    """Was the dependent D's version v due to re-pin to the library F's release this round? With STATE.json: its queue
    has a re-pin item there. Without it: yes -- a library release of the window at a version the dependent builds is
    re-pinned by the round (round.py build_queue), except a single-version library's release at a version BRANCH."""
    if ctx.state_items is not None:
        q = [it for it in ctx.state_items if it.get("fork") == D.slug and it.get("version") == v
             and it.get("step") in ("repin", "repin-head")]
        return bool(q), ("STATE.json queues " + ", ".join(it["id"] for it in q)) if q else "STATE.json queues no re-pin there"
    if F.kind == "single" and v != D.base:
        return False, "a single-version library's release is never re-pinned on a version branch"
    return True, "a library release of the window at a version the dependent builds"


def ledger_at(F, c):
    try:
        return mod("ki").read_ledger(repo=F.shadow, ref=c)
    except Exception as e:                       # noqa: BLE001 -- the callers fail on it
        F.notes.append(f"the ledger at {short(c)} could not be read: {type(e).__name__}: {e}"[:200])
        return None


def ledger_writes(ctx, F):
    """([(commit, issue, version, status)] of every status a ledger commit on the head set in the window, [problems])."""
    if hasattr(F, "_writes"):
        return F._writes
    out, probs = [], []
    us = updates(F, F.base)
    if F.history.get(F.base):
        probs.append(f"history: {F.history[F.base]}")
    elif us and us[0].get("before") not in (None, ZERO):
        d = F.shadow
        rel = mod("ki").ledger_rel()
        for c in out_of(d, "log", "--format=%H", "--reverse", us[-1]["after"], f"^{us[0]['before']}", "--", rel).split():
            new, old = ledger_at(F, c), ledger_at(F, f"{c}^1")
            if new is None or old is None:
                probs.append(f"the ledger at {short(c)} or its parent cannot be read")
                continue
            was = {e["id"]: e.get("status") or {} for e in old.get("issues", [])}
            for e in new.get("issues", []):
                for w, s in sorted((e.get("status") or {}).items()):
                    if was.get(e["id"], {}).get(w) != s:
                        out.append((c, e["id"], w, s))
    F._writes = (out, probs)
    return F._writes


def i6_ledger(ctx, F, v):
    ev, ok = [], True
    writes, probs = ledger_writes(ctx, F)
    if probs:
        ok = False
        ev += probs
    branch_end = tip_at(F, v, ctx.end)
    for c, i, _w, s in [x for x in writes if x[2] == v and x[3].startswith("fixed@")]:
        sha = s.split("@", 1)[1]
        full = out_of(F.shadow, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
        good = bool(full) and is_ancestor(F.shadow, full, branch_end)
        ok &= good
        ev.append(f"{i} {s} (ledger commit {short(c)}): " + ("on" if good else "NOT on") + f" {F.branch(v)} {short(branch_end)}")
    merged = any(is_merge(F, e) for e in updates(F, v))
    fx = list(ctx.frozen_fork(F.slug).get("fixes") or [])
    order = F.all_versions()
    if merged and fx:
        head_end = tip_at(F, F.base, ctx.end)
        led = ledger_at(F, head_end) if head_end else None
        if led is None:
            ok = False
            ev.append(f"the head's ledger at the window's end ({short(head_end)}) cannot be read")
            led = {"issues": []}
        st = {e["id"]: e.get("status") or {} for e in led.get("issues", [])}
        for x in fx:
            at = x.get("at") if x.get("at") in order else F.base
            if order.index(v) < order.index(at):
                continue
            s = st.get(x["id"], {}).get(v)
            if s == "open" or not s:
                ok = False
                ev.append(f"{x['id']} (selected, at {at}) is " + ("still open" if s else "not in the ledger" if x["id"] not in st
                                                                  else "without a status") + f" at {v}, merged this round")
            else:
                ev.append(f"{x['id']} at {v}: {s}")
    if not ev:
        return NA, ["no ledger write and no selected fix at this version"]
    return (PASS if ok else FAIL), ev


def work_due(ctx, F, v):
    """The work the frozen plan (and the window's library releases) gave version v, independently of round.py: the
    authors' code taken (the head cascade reaches every version), a selected fix at or below v (carried up), a
    ticked dependency bump at v, a library release of the window at v to re-pin to. -> [reasons]."""
    f = ctx.frozen_fork(F.slug)
    order = F.all_versions()
    out = []
    if f.get("take_authors"):
        out.append("the plan takes the authors' code")
    for x in f.get("fixes") or [{"id": i, "at": F.base} for i in f.get("selected", [])]:
        at = x.get("at") if x.get("at") in order else F.base
        if order.index(v) >= order.index(at):
            out.append(f"{x['id']} (fixed at {at})")
    out += [f"dependency bump {b.get('ga')}" for b in f.get("dep_bumps") or [] if b.get("mc") == v]
    for lb in F.depends_on:
        L = ctx.forks.get(lb)
        rx = re.compile(TAG_RE.format(v=re.escape(v)))
        if L and any(rx.search(r.get("tag_name") or "") for r in L.releases):
            out.append(f"{lb} released at {v}")
    return out


def i7_not_skipped(ctx, F, v):
    if updates(F, v):
        return PASS, ["merged or pushed in the window"]
    if F.history.get(v):
        return UNKNOWN, [f"history: {F.history[v]}"]
    if ctx.state_items is not None:
        mine = [it for it in ctx.state_items if it.get("fork") == F.slug and it.get("version") == v
                and not it.get("final")]
        if not mine:
            return PASS, ["no work queued for it (STATE.json)"]
        open_ = [it["id"] for it in mine if it.get("status") not in ("ok", "skipped")]
        if not open_:
            return PASS, ["every queued item ok or skipped (STATE.json)"]
        return FAIL, [f"queued and not finished: {', '.join(open_[:4])}"]
    due = work_due(ctx, F, v)
    if not due:
        return PASS, ["the frozen plan gives it no work (no authors' code, no fix at or below it, no bump, no library "
                      "release to re-pin to)"]
    prose = " (the closing comment names it: prose, not evidence)" if names_version(section_of(ctx.close, F), v) else ""
    return FAIL, [f"the plan gave it work ({'; '.join(due[:3])}) but the round neither merged nor pushed it{prose}"]


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


def audit(plan, forks_arg, state=None, since=None, until=None, keep=False, say=print, pin=None):
    """-> (report dict, exit code)."""
    t0 = time.monotonic()
    if pin and pin != PIN["rev"]:
        PIN.update(rev=pin, mod=None, why=None)
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
                    except Exception as e:      # noqa: BLE001 -- an audit that could not judge: UNKNOWN, exit 1
                        s_, ev = UNKNOWN, [f"could not be checked: {type(e).__name__}: {e}"[:300]]
                    row["checks"][k] = {"status": s_, "evidence": ev}
                rows.append(row)
            stray = pr_set_rows(ctx, F)
            if stray:          # a PR the round merged into a branch that is no version of the fork: its own FAIL row
                rows.append({"fork": F.slug, "version": "(other)", "head": False, "checks": dict(
                    {k: {"status": NA, "evidence": []} for k in INVS}, I1={"status": FAIL, "evidence": [
                        f"PR #{p['number']} merged into {(p.get('base') or {}).get('ref')}, not a version branch of the "
                        "fork the audit knows" for p in stray]})})
        rep = {"plan": plan, "window": {"start": iso(start), "end": iso(end), "closed": bool(closing) and not until,
                                        "end_from": "--until" if until else None,
                                        "start_from": "--since" if since else "STATE.json round_start" if st and
                                        st.get("round_start") else "the plan issue's creation"},
               "forks": {F.slug: {"kind": F.kind, "library": F.library, "depends_on": F.depends_on,
                                  "versions": F.all_versions(), "notes": F.notes} for F in forks.values()},
               "rows": rows, "seconds": round(time.monotonic() - t0, 1), "pin": PIN["rev"]}
        bad = any(c["status"] in BAD for r in rows for c in r["checks"].values())
        return rep, (1 if bad else 0)
    finally:
        if keep:
            say(f"round-audit: shadows kept in {work.as_posix()}")
        else:
            rmtree(work)



def render(rep):
    w = rep["window"]
    out = [f"round-audit: plan {rep['plan']}, window {w['start']} .. {w['end']} (start: {w['start_from']}; "
           + ("end: the closing comment" if w["closed"] else "end: --until" if w.get("end_from") else
              "NOT CLOSED: end is now") + ")"]
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
    unk = sum(c["status"] == UNKNOWN for r in rep["rows"] for c in r["checks"].values())
    warns = sum(c["status"] == WARN for r in rep["rows"] for c in r["checks"].values())
    out.append("")
    out.append(f"round-audit: {fails} FAIL, {unk} UNKNOWN, {warns} WARN in {rep['seconds']} s (pinned migrator "
               f"{rep.get('pin', PIN['rev'])[:12]})")
    return "\n".join(out)


# ---------------------------------------------------------------- self-check (local bare repos, a stand-in GitHub)

def self_check():
    t0 = time.monotonic()
    global API, RAW
    ok = True
    keep_env = {k: os.environ.get(k) for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                                               "GIT_COMMITTER_EMAIL", "GIT_CONFIG_COUNT")}
    keep_pin = dict(PIN)
    n = int(os.environ.get("GIT_CONFIG_COUNT", "0") or 0)
    for k, v in (("commit.gpgsign", "false"), ("gc.auto", "0"), ("core.autocrlf", "false"), ("init.defaultBranch", "main")):
        os.environ[f"GIT_CONFIG_KEY_{n}"], os.environ[f"GIT_CONFIG_VALUE_{n}"] = k, v
        n += 1
    os.environ["GIT_CONFIG_COUNT"] = str(n)
    os.environ.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                       "GIT_COMMITTER_EMAIL": "t@example.com"})
    # the cross-check's pinned migrator: this checkout's own HEAD (always in a clone; the real default is a constant)
    PIN.update(rev="HEAD", mod=None, why=None, dir=None)

    def expect(c, msg):
        nonlocal ok
        if not c:
            ok = False
            print("FAIL:", msg)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="round-audit-selfcheck-")).resolve()
    try:
        expect(not host_ok("https://example.com/x", True) and host_ok("https://api.github.com/x", True)
               and host_ok("https://objects.githubusercontent.com/x", False) and not host_ok("https://evil.example/x", False),
               "gh_raw: only api.github.com, and only GitHub's storage hosts as a redirect")
        try:
            gh_raw("https://example.com/asset")
            expect(False, "gh_raw refuses a host that is not api.github.com")
        except ValueError:
            pass
        expect(next_link("repos/o/r/activity?ref=x", '<https://api.github.com/repositories/12/activity?after=c>; rel="next", '
                         '<https://api.github.com/repositories/12/activity?before=b>; rel="prev"') == "repos/o/r/activity?after=c"
               and next_link("repos/o/r/activity", '<https://api.github.com/x?before=b>; rel="prev"') is None,
               "next_link: the next page, named by owner/repo; no next on the last page")
        expect(gatec_phases('run: python3 ci-gates.py --gatec "launch,spawn"\n') == ["launch", "spawn"]
               and gatec_phases("run: python3 ci-gates.py --gatec none\n") == []
               and gatec_phases('run: python3 ci-gates.py --gatec "${{ inputs.gatec }}"\n') == []
               and gatec_phases('run: python3 ci-gates.py --gatec "launch,${{ inputs.more }}"\n') == []
               and gatec_phases("# python3 ci-gates.py --gatec launch\nrun: python3 ci-gates.py --base x\n") == []
               and gatec_phases("run: python3 ci-gates.py \\\n  --gatec launch,battle\n") == ["battle", "launch"],
               "gatec_phases: phases from the ci-gates command; none, an expression, a comment are no Gate C")
        fx = Fixture(tmp / "base")
        API, RAW = fx.api, fx.raw
        base_rep, rc = fx.run()
        row = lambda rep, f, v: next(r for r in rep["rows"] if r["fork"] == f and r["version"] == v)
        st = lambda rep, f, v, k: row(rep, f, v)["checks"][k]["status"]
        LIB, DEP = fx.LIB, fx.DEP
        want = {(LIB, "1.21.4"): {"I1": PASS, "I2": PASS, "I3": PASS, "I4": PASS, "I5": PASS, "I6": PASS, "I7": PASS},
                (LIB, "1.21.5"): {"I1": PASS, "I2": NA, "I3": NA, "I4": PASS, "I6": PASS, "I7": PASS},
                (DEP, "1.21.4"): {"I1": PASS, "I2": PASS, "I3": PASS, "I4": PASS, "I5": NA, "I7": PASS},
                (LIB, "1.21.1"): {"I1": PASS, "I4": NA, "I6": NA, "I7": PASS},
                (DEP, "1.21.1"): {"I1": PASS, "I2": PASS, "I3": PASS, "I7": PASS},
                (DEP, "1.21.5"): {"I1": NA, "I7": PASS}}
        got = {(f, v): {k: st(base_rep, f, v, k) for k in INVS} for f, v in want}
        expect(rc == 0 and all(got[x][k] == s_ for x, w in want.items() for k, s_ in w.items()),
               f"a round that kept every guarantee passes: rc={rc} {got}\n{render(base_rep)}")
        # ---- mutations: each fixture violating one check fails exactly there, for the reason named
        X = "examplelib-1.0-mc1.21.4"
        cases = [
            ("I1", (LIB, "1.21.4"), "a red PR run", lambda f: f.run_conclusion.update({f.r_lib: "failure"}),
             FAIL, "no green Port CI pull_request run"),
            ("I1", (LIB, "1.21.4"), "a skipped gates job", lambda f: f.job_skip.add(f.r_lib), FAIL, "gates (full): skipped"),
            ("I1", (LIB, "1.21.4"), "a gates job green whose ci-gates step failed", lambda f: f.step_fail.add(f.r_lib),
             FAIL, "step Build and gates failure"),
            ("I1", (LIB, "1.21.4"), "the green run is another PR's", lambda f: f.run_pr.update({f.r_lib: 99}),
             FAIL, "for this PR"),
            ("I1", (LIB, "1.21.4"), "the workflow's Gate C is `none`", pre(wf='run: python3 ci-gates.py --gatec none\n'),
             FAIL, "runs no Gate C"),
            ("I1", (LIB, "1.21.4"), "the workflow's Gate C is an expression",
             pre(wf='run: python3 ci-gates.py --gatec "${{ inputs.gatec }}"\n'), FAIL, "runs no Gate C"),
            ("I1", (LIB, "1.21.4"), "Gate C only in a comment",
             pre(wf='# python3 ci-gates.py --gatec launch\nrun: python3 ci-gates.py --base x\n'), FAIL, "runs no Gate C"),
            ("I1", (LIB, "1.21.4"), "the base moved by code after the run", lambda f: f.moved_base(records=False),
             FAIL, "the base moved"),
            ("I1", (LIB, "1.21.4"), "a PR head behind a base that did not move after the run: its test merge's parents",
             lambda f: f.head_behind(), PASS, "the run's test merge"),
            ("I1", (LIB, "1.21.4"), "the base moved by records only (a ledger push): not a failure",
             lambda f: f.moved_base(records=True), PASS, "by records only"),
            ("I1", (LIB, "1.21.5"), "a push that is not a fast-forward", lambda f: f.non_ff(), FAIL, "not a fast-forward"),
            ("I1", (LIB, "1.21.5"), "a direct push of shipped code", lambda f: f.push_file(
                "src/main/java/com/example/mod/Main.java", "class Main { int a = 3; }\n"), FAIL, "only a PR may change"),
            ("I1", (LIB, "1.21.5"), "a direct push of .port/branch.gradle (a jar rename, the 2026-10-31 case)",
             lambda f: f.push_file(".port/branch.gradle", "// jar names -mc<version>\n"), FAIL, ".port/branch.gradle"),
            ("I1", (LIB, "1.21.5"), "a direct push of a build key", lambda f: f.push_props("java_version=25\n"),
             FAIL, "branch.properties (java_version)"),
            ("I1", (LIB, "1.21.5"), "a direct push of the Port CI workflow with no green push run",
             lambda f: f.push_file(PORT_CI_WF, 'run: python3 ci-gates.py --gatec "launch"\n'), FAIL, "no green push run"),
            ("I1", (LIB, "1.21.4"), "GitHub's history starts inside the window", lambda f: f.cut_history(LIB, "neoforge-1.21.4"),
             FAIL, "inside the window"),
            ("I1", (LIB, "1.21.4"), "GitHub's history does not end at the branch's tip",
             lambda f: f.drop_last_event(LIB, "neoforge-1.21.4"), FAIL, "but origin's"),
            ("I1", (LIB, "1.21.5"), "a gap in GitHub's history (an update missing)", lambda f: f.history_gap(),
             FAIL, "a gap in GitHub's activity history"),
            ("I1", (LIB, "1.21.4"), "a PR merged with no branch update in the history", lambda f: f.hide_merge(),
             FAIL, "no branch update in GitHub's activity history names that merge"),
            ("I2", (LIB, "1.21.5"), "a records-only PR (a publish config): gated, no release needed",
             lambda f: f.records_pr(), NA, "records-only"),
            ("I3", (LIB, "1.21.5"), "a records-only PR needs no review", lambda f: f.records_pr(), NA, "records only"),
            ("I2", (LIB, "1.21.4"), "a release from a run that is not the gated one", lambda f: f.rel_body.update(
                {X: "Built from x (jars and gates of run 999, the same tree)."}), FAIL, "not a gated run"),
            ("I2", (LIB, "1.21.4"), "a release on another tree", lambda f: f.release_elsewhere(), FAIL,
             "is no tree I1 found gated"),
            ("I2", (DEP, "1.21.4"), "a merged version with no release", lambda f: f.drop_release(f.dep_tag), FAIL,
             "no release was published"),
            ("I2", (DEP, "1.21.4"), "a Gate C FAIL in a release's gate summary", lambda f: f.assets.update(
                {f.dep_tag: "| check | result |\n|---|---|\n| Gate C -- real client, `launch` | FAIL | crash |\n"}),
             FAIL, "Gate C rows not PASS"),
            ("I2", (DEP, "1.21.4"), "a later PR merged after the release and never released",
             lambda f: f.late_pr(), FAIL, "was not released"),
            ("I2", (DEP, "1.21.4"), "a release with no gate summary", lambda f: f.no_assets.add(f.dep_tag), UNKNOWN,
             "no gate summary"),
            ("I2", (DEP, "1.21.4"), "no from_run marker and a release run whose gates were skipped",
             lambda f: (f.rel_body.update({f.dep_tag: "Built from it."}), f.job_skip.add(f.r_dep + 5000)),
             FAIL, "did not gate it"),
            ("I2", (DEP, "1.21.4"), "no green release run on the commit",
             lambda f: f.run_conclusion.update({f.r_dep + 5000: "failure"}), FAIL, "no green release"),
            ("I3", (LIB, "1.21.4"), "a stale review", lambda f: f.stale_review(), FAIL, "re-review"),
            ("I3", (LIB, "1.21.4"), "the first of two merges stale (every merge is checked)",
             lambda f: f.stale_review(second=True), FAIL, "re-review"),
            ("I3", (DEP, "1.21.1"), "a head PR whose review row stayed not-ok", pre(head_body=(
                "### review\n\nRESULT: ok\n\n| file | ok/not-ok | note |\n|---|---|---|\n"
                "| src/main/java/com/example/mod/Main.java | not-ok | wrong |\n")), FAIL, "not-ok at the end"),
            ("I3", (DEP, "1.21.1"), "a head PR with no review in its body", pre(head_body="no review here\n"), FAIL,
             "no review section"),
            ("I3", (DEP, "1.21.1"), "a head file changed after the last commit the summaries name, not rechecked",
             pre(late_head=True), FAIL, "after the last commit the summaries name"),
            ("I3", (LIB, "1.21.4"), "the pinned migrator's review check disagrees", pre(pin_lies=True), FAIL, "disagree"),
            ("I4", (LIB, "1.21.5"), "pushed before the lower version's commits merged", lambda f: f.early_push(), FAIL,
             "not on origin's"),
            ("I4", (LIB, "1.21.5"), "carrying a lower PR's commits that never reached the lower branch",
             lambda f: f.abandoned_lower(), FAIL, "not on origin's"),
            ("I5", (LIB, "1.21.4"), "a dependent left on the old release (the close names the version)",
             lambda f: f.no_repin(), FAIL, "a re-pin was due"),
            ("I5", (LIB, "1.21.4"), "a pin only in a comment line", lambda f: f.no_repin(commented=True), FAIL,
             "a re-pin was due"),
            ("I5", (LIB, "1.21.4"), "a pin of the same tag from another repository",
             lambda f: f.no_repin(other_repo=True), FAIL, "a re-pin was due"),
            ("I5", (LIB, "1.21.4"), "no Gate C record for the release", lambda f: f.no_librecord(), FAIL, "no PASS record"),
            ("I5", (LIB, "1.21.4"), "with STATE.json and no re-pin queued: not due", pre(state_no_repin=True),
             NA, "no re-pin was due"),
            ("I6", (LIB, "1.21.4"), "fixed@ a commit not on the branch", lambda f: f.ledger({"1.21.4": "PRE", "1.21.5": "FIX"}),
             FAIL, "NOT on"),
            ("I6", (LIB, "1.21.4"), "a selected fix left open", lambda f: f.ledger({"1.21.4": "open", "1.21.5": "FIX"}),
             FAIL, "still open"),
            ("I6", (LIB, "1.21.4"), "a selected fix with no status at a merged version",
             lambda f: f.ledger({"1.21.5": "FIX"}), FAIL, "without a status"),
            ("I6", (LIB, "1.21.4"), "an unreadable ledger at the window's end", lambda f: f.ledger(None), FAIL,
             "cannot be read"),
            ("I6", (LIB, "1.21.4"), "an unreadable ledger commit in the window (the end readable)",
             lambda f: (f.ledger(None), f.ledger({"1.21.4": "FIX", "1.21.5": "FIX"})), FAIL, "or its parent cannot be read"),
            ("I7", (DEP, "1.21.5"), "a version the plan gave work (a bump) that the round never touched",
             pre(dep_bump="1.21.5"), FAIL, "the plan gave it work"),
        ]
        seen = set()
        for k, (f_, v_), what, mut, want_s, why in cases:
            name = re.sub(r"\W+", "-", what)[:40]
            assert name not in seen
            seen.add(name)
            fm = Fixture(tmp / f"m-{k}-{name}", mutate=mut)
            API, RAW = fm.api, fm.raw
            rep, rc = fm.run(state=getattr(fm, "state_path", None))
            c = row(rep, f_, v_)["checks"][k]
            s_, ev_ = c["status"], " / ".join(c["evidence"])
            if os.environ.get("ROUND_AUDIT_VERBOSE"):
                print(f"mutation {k} ({what}): {s_}: {ev_[:300]}")
            expect(s_ == want_s and why in ev_ and rc == (1 if want_s in BAD else rc) and "could not be checked" not in ev_,
                   f"mutation {k} ({what}): {f_} {v_} {k} = {s_} (want {want_s} saying {why!r}), rc {rc}: {ev_[:600]}")
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
        # ---- refs_as_of: a branch that did not exist at the time is removed for the check, and restored after
        F_ = Fork(LIB, fx.lib, {"head": "neoforge-1.21.1"})
        make_shadow(F_, tmp / "refs-as-of")
        F_.versions = ["1.21.4", "1.21.5"]
        ref5 = "refs/remotes/origin/neoforge-1.21.5"
        tip5 = out_of(F_.shadow, "rev-parse", ref5)
        F_.events = {"1.21.5": {"all": [{"before": ZERO, "after": tip5, "timestamp": at(10),
                                         "activity_type": "branch_creation"}], "window": []}}
        with refs_as_of(F_, T0):
            gone = git(F_.shadow, "rev-parse", "--verify", "--quiet", ref5).returncode != 0
        expect(gone and out_of(F_.shadow, "rev-parse", ref5) == tip5,
               "refs_as_of: a branch created after the time is absent during the check and restored after it")
        # ---- read-only: the clones are byte-for-byte untouched (refs, config, index)
        expect(fx.snapshot() == fx.before, "the fork clones were not written (refs, config, HEAD)")
    finally:
        API = RAW = None
        if PIN.get("dir"):
            rmtree(PIN["dir"])
        PIN.clear()
        PIN.update(keep_pin)
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


def pre(**opts):
    """A mutation set before the fixture is built (its workflow, a PR body, the plan, the pinned check)."""
    def m(_f):
        return None
    m.opts = opts
    return m


T0 = datetime.datetime(2026, 10, 21, 15, 0, tzinfo=datetime.timezone.utc)


def at(minutes):
    return iso(T0 + datetime.timedelta(minutes=minutes))


class LyingPin:
    """A pinned fork-branches.py whose review check finds nothing (the self-check of the cross-check)."""

    def __init__(self, real):
        self.real = real

    def __getattr__(self, k):
        return getattr(self.real, k)

    def review_problems(self, *a, **k):
        return ["REVIEW.md: a problem only the pinned check sees"]


class Fixture:
    """A library (head 1.21.1, versions 1.21.4 and 1.21.5) and its dependent (the same versions), bare origins, and the
    stand-in GitHub of a round that kept every guarantee: the dependent's head a PR (its review in the PR body)
    merged and released; lib 1.21.4 a PR (a fix) merged, gated and released; lib 1.21.5 its merge-up pushed directly
    (records only: 1.21.5 had the same code); the dependent's 1.21.4 re-pinned to the library's release by PR, gated
    and released; the library's Gate C record pushed; the ledger written (with a plan-config change); every branch's
    activity history from its creation, served two updates a page."""
    LIB, DEP = "owner/examplelib", "owner/examplemod"
    WF = 'jobs:\n  gates:\n    steps:\n      - run: python3 ci-gates.py --repo . --gatec "launch,spawn"\n'

    def __init__(self, tmp, mutate=None):
        self.tmp = pathlib.Path(tmp)
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.run_conclusion, self.job_skip, self.step_fail, self.run_pr = {}, set(), set(), {}
        self.rel_body, self.assets, self.no_assets = {}, {}, set()
        self.activity, self.pulls, self.runs, self.releases = {}, {self.LIB: [], self.DEP: []}, {}, {self.LIB: [], self.DEP: []}
        self.pr_commits, self.comments = {}, []
        o = getattr(mutate, "opts", {}) if mutate else {}
        self.opts = o
        self.wf = o.get("wf", self.WF)
        self.head_body = o.get("head_body", (
            "keep-current round\n\n### backlog\n\nRESULT: ok\ncommit: {FIX}\n\n### review\n\nRESULT: ok\nnot_ok: 1\n\n"
            "| file | ok/not-ok | note |\n|---|---|---|\n| src/main/java/com/example/mod/Main.java | not-ok | the comment |\n\n"
            "### fix\n\nRESULT: ok\n\n### recheck\n\nRESULT: ok\nnot_ok: 0\n\n"
            "| file | ok/not-ok | note |\n|---|---|---|\n| src/main/java/com/example/mod/Main.java | ok | fixed |\n"))
        self.build()
        if o.get("pin_lies"):
            pf = pinned()
            PIN["mod"] = LyingPin(pf.real if isinstance(pf, LyingPin) else pf)
        elif isinstance(PIN.get("mod"), LyingPin):
            PIN["mod"] = PIN["mod"].real
        if o.get("state_no_repin"):
            self.no_repin()
            self.state_path = self.tmp / "STATE.json"
            self.state_path.write_text(json.dumps({"items": [
                {"id": "examplemod@1.21.4:pr", "fork": self.DEP, "version": "1.21.4", "step": "pr", "status": "ok"}]}),
                encoding="utf-8")
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
        self.w(d, PORT_CI_WF, self.wf)
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
        self.g(d, "push", "-q", "-f", "--prune", str(bare), "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*",
               "+refs/pull/*:refs/pull/*")
        return bare

    def tip(self, d, b):
        return self.g(d, "rev-parse", b)

    def ev(self, slug, branch, before, after, kind, when):
        evs = self.activity.setdefault((slug, branch), [])
        evs.append({"id": 1000 + len(evs), "before": before, "after": after, "ref": f"refs/heads/{branch}",
                    "timestamp": when, "activity_type": kind})

    def pr_merge(self, slug, d, v, work, number, when):
        """A work branch from neoforge-<v> merged by PR: its run green, the event recorded."""
        b = f"neoforge-{v}"
        before = self.tip(d, b)
        self.g(d, "checkout", "-q", b)
        head = self.tip(d, work)
        self.pr_commits[(slug, number)] = out_of(d, "rev-list", "--reverse", head, f"^{before}").split()
        m = self.merge(d, work, f"Merge pull request #{number}", when)
        self.g(d, "update-ref", f"refs/pull/{number}/head", head)
        rid = 1000 + number
        self.runs.setdefault(slug, []).append({"id": rid, "head_sha": head, "event": "pull_request", "status": "completed",
                                               "conclusion": "success", "path": PORT_CI_WF,
                                               "created_at": iso(ts(when) - datetime.timedelta(minutes=20)),
                                               "head_branch": work, "pull_requests": [{"number": number,
                                                                                       "base": {"sha": before}}]})
        self.pulls[slug].append({"number": number, "merged_at": when, "updated_at": when, "merge_commit_sha": m,
                                 "base": {"ref": b, "sha": before}, "head": {"sha": head, "ref": work}})
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
        self.g(d, "tag", "-f", tag, c)
        self.runs.setdefault(slug, []).append({"id": run_id + 5000, "head_sha": c, "event": "workflow_dispatch",
                                               "status": "completed", "conclusion": "success", "path": PORT_CI_WF,
                                               "created_at": when, "head_branch": "x", "pull_requests": []})
        self.releases[slug].append({"tag_name": tag, "target_commitish": c, "published_at": when, "created_at": when,
                                    "draft": False, "body": f"Built from `{c}` (jars and gates of run {run_id}, the same tree)."
                                    if from_run else f"Built from `{c}`.",
                                    "assets": [{"name": "gates-full.md", "url": f"https://api.github.com/repos/{slug}/"
                                                f"releases/assets/{tag}/gates-full.md"}]})

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
        # lib 1.21.5 already has the code 1.21.4's fix brings (its merge-up then changes records only)
        self.g(lib, "checkout", "-q", "neoforge-1.21.5")
        self.w(lib, "src/main/java/com/example/mod/Main.java", "class Main { int a = 2; } // LIB-1\n")
        self.commit(lib, "1.21.5 had the fix", at(-380))
        # the dependent's head: a change by PR at +5 (a version-branch fork's head: its review is in the PR body)
        self.g(dep, "checkout", "-q", "-b", "keep-current/x", "neoforge-1.21.1")
        self.w(dep, "src/main/java/com/example/mod/Main.java", "class Main { int a = 7; }\n")
        hfix = self.commit(dep, "a head fix", at(2))
        if self.opts.get("late_head"):
            self.w(dep, "src/main/java/com/example/mod/Other.java", "class Other { }\n")
            self.commit(dep, "a change after the review", at(3))
        m_h, r_h = self.pr_merge(DEP, dep, "1.21.1", "keep-current/x", 4, at(5))
        self.pulls[DEP][-1]["body"] = self.head_body.replace("{FIX}", hfix[:7])
        self.release(DEP, dep, "examplemod-1.0-mc1.21.1", m_h, r_h, at(6))
        # lib 1.21.4: the fix (LIB-1), its review, a PR merged at +60, released from its run at +65
        self.g(lib, "checkout", "-q", "-b", "keep-current/x-1.21.4", "neoforge-1.21.4")
        self.w(lib, "src/main/java/com/example/mod/Main.java", "class Main { int a = 2; } // LIB-1\n")
        fix = self.lib_fix = self.commit(lib, "LIB-1: the fix", at(20))
        self.write_review(lib, "1.21.4", fix)
        self.commit(lib, "review", at(30))
        self.m_lib, self.r_lib = self.pr_merge(LIB, lib, "1.21.4", "keep-current/x-1.21.4", 1, at(60))
        self.lib_tag = "examplelib-1.0-mc1.21.4"
        self.release(LIB, lib, self.lib_tag, self.m_lib, self.r_lib, at(65))
        # lib 1.21.5: the merge-up of 1.21.4, records only, pushed directly at +70
        self.g(lib, "checkout", "-q", "neoforge-1.21.5")
        before = self.tip(lib, "neoforge-1.21.5")
        self.merge(lib, "neoforge-1.21.4", "merge-up 1.21.4", at(69))
        self.ev(LIB, "neoforge-1.21.5", before, self.tip(lib, "neoforge-1.21.5"), "push", at(70))
        # dep 1.21.4: re-pinned to the library's release by PR at +90, released from its run at +95
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
        # the ledger: LIB-1 fixed at 1.21.4 and 1.21.5, pushed on the head at +110 with a plan-config change
        self.g(lib, "checkout", "-q", "neoforge-1.21.1")
        self.w(lib, ".github/weekly-plan.json", (lib / ".github/weekly-plan.json").read_text(encoding="utf-8") + "\n")
        self.write_ledger(lib, {"1.21.4": f"fixed@{fix[:7]}", "1.21.5": f"fixed@{fix[:7]}"})
        before = self.tip(lib, "neoforge-1.21.1")
        self.commit(lib, "ledger", at(109))
        self.ev(LIB, "neoforge-1.21.1", before, self.tip(lib, "neoforge-1.21.1"), "push", at(110))
        self.comments = [{"id": 1, "created_at": at(0), "body": "Round progress: started", "user": {"login": "owner"}},
                         {"id": 2, "created_at": at(120), "user": {"login": "owner"}, "body":
                          f"## Round closed\n\n### {LIB} (the library)\n- 1.21.4 merged, 1.21.5 pushed\n\n"
                          f"### {DEP}\n- 1.21.1 (head) merged; 1.21.4 merged; 1.21.5: nothing to do\n"}]
        self.finish()

    def write_deprec(self, lib, tag, commit_):
        self.w(lib, ".port/1.21.4/DEPENDENTS.json", json.dumps([{"dependent": self.DEP, "dependent_commit": self.m_dep,
                                                                  "gate_c": "PASS", "library": "com.example:lib",
                                                                  "library_tag": tag, "library_commit": commit_,
                                                                  "recorded": "2026-10-21"}], indent=1) + "\n")

    def write_ledger(self, lib, status):
        if status is None:
            self.w(lib, ".port/KNOWN-ISSUES.json", "{ not json\n")
            return
        self.w(lib, ".port/KNOWN-ISSUES.json", json.dumps({"schema": 1, "issues": [
            {"id": "LIB-1", "title": "a defect", "severity": "S3", "source": "review",
             "lowest": "1.21.4" if "1.21.4" in status else "1.21.5",
             "rubric": {"failure": "functional", "visibility": "common", "reach": "normal", "workaround": True},
             "status": status, "files": ["src/main/java/com/example/mod/Main.java"]}]}, indent=1) + "\n")

    def finish(self):
        """Push to the bare origins, and give every branch its activity history from its creation (-300)."""
        for s, d in ((self.LIB, self.lib), (self.DEP, self.dep)):
            self.g(d, "checkout", "-q", "neoforge-1.21.1")
            bare = self.push_bare(d, s)
            if not git(d, "remote").stdout.strip():
                self.g(d, "remote", "add", "origin", bare.as_posix())
            self.g(d, "fetch", "-q", "--prune", "origin")
            for b in ("neoforge-1.21.1", "neoforge-1.21.4", "neoforge-1.21.5"):
                evs = [e for e in self.activity.get((s, b), []) if not e.get("synthetic")]
                first = evs[0]["before"] if evs else self.tip(d, b)
                self.activity[(s, b)] = [{"id": 1, "before": ZERO, "after": first, "ref": f"refs/heads/{b}",
                                          "timestamp": at(-300), "activity_type": "branch_creation", "synthetic": True}] + evs
        self.before = self.snapshot()

    def snapshot(self):
        return [(out_of(d, "for-each-ref", "--format=%(refname) %(objectname)"), out_of(d, "config", "--list", "--local"),
                 out_of(d, "rev-parse", "HEAD"), out_of(d, "status", "--porcelain")) for d in (self.lib, self.dep)]

    # -- the mutations
    def moved_base(self, records, when=50):
        """A base commit lands on 1.21.4 at +when: after the PR's run (+40) by code, or only a record (a ledger-like
        file); or before the run (the PR head behind its base, the run's test merge the merge's own parents)."""
        lib = self.lib
        self.g(lib, "checkout", "-q", "neoforge-1.21.4")
        self.g(lib, "reset", "-q", "--hard", f"{self.m_lib}^1")
        before = self.tip(lib, "HEAD")
        if records:
            self.w(lib, ".port/1.21.4/COST.md", "a record\n")
        else:
            self.w(lib, "src/main/java/com/example/mod/Other.java", "class Other {}\n")
        mid = self.commit(lib, "a base commit", at(when))
        m = self.merge(lib, self.g(lib, "rev-parse", f"{self.m_lib}^2"), "Merge pull request #1", at(60))
        self.write_deprec(lib, self.lib_tag, m)
        rec = self.commit(lib, "library record", at(99))
        evs = [e for e in self.activity[(self.LIB, "neoforge-1.21.4")] if not e.get("synthetic")]
        evs[0].update(before=mid)
        evs[0]["after"], evs[1]["before"], evs[1]["after"] = m, m, rec
        self.activity[(self.LIB, "neoforge-1.21.4")] = [{"id": 2, "before": before, "after": mid, "timestamp": at(when),
                                                         "activity_type": "push", "ref": "refs/heads/neoforge-1.21.4"}] + evs
        for p in self.pulls[self.LIB]:
            if p["number"] == 1:
                p["merge_commit_sha"] = m
        self.g(lib, "tag", "-f", self.lib_tag, m)
        self.releases[self.LIB][0]["target_commitish"] = m
        self.relink_lib_15(m)
        self.finish()

    def head_behind(self):
        """Another PR (#8, gated) lands on 1.21.4 at +10, before PR #1's run (+40): PR #1's head is behind its base,
        and its merge has the parents of the run's test merge."""
        lib = self.lib
        self.g(lib, "checkout", "-q", "neoforge-1.21.4")
        self.g(lib, "reset", "-q", "--hard", f"{self.m_lib}^1")
        head1 = self.g(lib, "rev-parse", f"{self.m_lib}^2")
        self.pulls[self.LIB] = [p for p in self.pulls[self.LIB] if p["number"] != 1]
        self.runs[self.LIB] = [r for r in self.runs[self.LIB] if r["id"] not in (self.r_lib, self.r_lib + 5000)]
        self.activity[(self.LIB, "neoforge-1.21.4")] = [e for e in self.activity[(self.LIB, "neoforge-1.21.4")]
                                                       if e.get("synthetic")]
        self.releases[self.LIB] = []
        self.g(lib, "checkout", "-q", "-b", "base-change", f"{self.m_lib}^1")
        self.w(lib, "src/main/java/com/example/mod/Other.java", "class Other {}\n")
        self.commit(lib, "another change", at(8))
        m8, _r8 = self.pr_merge(self.LIB, lib, "1.21.4", "base-change", 8, at(10))
        self.g(lib, "update-ref", "refs/heads/keep-current/x-1.21.4", head1)
        m, rid = self.pr_merge(self.LIB, lib, "1.21.4", "keep-current/x-1.21.4", 1, at(60))
        self.release(self.LIB, lib, self.lib_tag, m, rid, at(65))
        self.g(lib, "checkout", "-q", "neoforge-1.21.4")
        self.write_deprec(lib, self.lib_tag, m)
        b2 = self.tip(lib, "neoforge-1.21.4")
        self.commit(lib, "library record", at(99))
        self.ev(self.LIB, "neoforge-1.21.4", b2, self.tip(lib, "neoforge-1.21.4"), "push", at(100))
        self.m_lib = m
        self.relink_lib_15(m)
        self.finish()

    def relink_lib_15(self, m):
        """lib 1.21.5's merge-up again, from the rewritten 1.21.4."""
        lib = self.lib
        self.g(lib, "checkout", "-q", "neoforge-1.21.5")
        e = [x for x in self.activity[(self.LIB, "neoforge-1.21.5")] if not x.get("synthetic")][0]
        self.g(lib, "reset", "-q", "--hard", e["before"])
        self.g(lib, "merge", "-q", "--no-ff", "-X", "theirs", "-m", "merge-up 1.21.4", m)
        e["after"] = self.tip(lib, "neoforge-1.21.5")

    def non_ff(self):
        """1.21.5's second push replaces its first (recorded as a push, not a fast-forward)."""
        lib = self.lib
        e = [x for x in self.activity[(self.LIB, "neoforge-1.21.5")] if not x.get("synthetic")][0]
        self.g(lib, "checkout", "-q", "neoforge-1.21.5")
        self.g(lib, "reset", "-q", "--hard", e["before"])
        self.w(lib, ".port/1.21.5/COST.md", "rewritten\n")
        self.commit(lib, "a rewrite", at(71))
        self.ev(self.LIB, "neoforge-1.21.5", e["after"], self.tip(lib, "neoforge-1.21.5"), "push", at(72))
        self.finish()

    def push_file(self, path, text):
        self.g(self.lib, "checkout", "-q", "neoforge-1.21.5")
        before = self.tip(self.lib, "neoforge-1.21.5")
        self.w(self.lib, path, text)
        self.commit(self.lib, f"{path} pushed directly", at(71))
        self.ev(self.LIB, "neoforge-1.21.5", before, self.tip(self.lib, "neoforge-1.21.5"), "push", at(72))
        self.finish()

    def push_props(self, line):
        p = self.lib / ".port/1.21.5/branch.properties"
        self.g(self.lib, "checkout", "-q", "neoforge-1.21.5")
        self.push_file(".port/1.21.5/branch.properties", p.read_text(encoding="utf-8") + line)

    def cut_history(self, slug, b):
        self.activity[(slug, b)] = [e for e in self.activity[(slug, b)] if not e.get("synthetic")]
        self.finish = lambda: None        # keep it cut

    def drop_last_event(self, slug, b):
        self.activity[(slug, b)] = self.activity[(slug, b)][:-1]
        self.finish = lambda: None

    def records_pr(self):
        """A PR into 1.21.5 (#7) changing only a record and the publish config, merged at +75 with its green run."""
        lib = self.lib
        self.g(lib, "checkout", "-q", "-b", "publish-config", "neoforge-1.21.5")
        self.w(lib, ".github/port-publish.json", "{}\n")
        self.w(lib, ".port/1.21.5/COST.md", "a record\n")
        self.commit(lib, "publish config", at(74))
        self.pr_merge(self.LIB, lib, "1.21.5", "publish-config", 7, at(75))
        self.finish()

    def late_pr(self):
        """A second PR (#9, gated) into dep 1.21.4 at +97, after its release (+95), with no release of its own."""
        dep = self.dep
        self.g(dep, "checkout", "-q", "-b", "late", "neoforge-1.21.4")
        self.w(dep, "src/main/java/com/example/mod/Main.java", "class Main { int a = 9; }\n")
        c = self.commit(dep, "a later change", at(96))
        self.write_review(dep, "1.21.4", c)
        self.commit(dep, "its review", at(96))
        self.pr_merge(self.DEP, dep, "1.21.4", "late", 9, at(97))
        self.finish()

    def history_gap(self):
        """Two records pushes on 1.21.5; GitHub's history lists only the second."""
        self.push_file(".port/1.21.5/COST.md", "one\n")
        self.push_file(".port/1.21.5/DRIFT.md", "two\n")
        evs = self.activity[(self.LIB, "neoforge-1.21.5")]
        evs.remove([e for e in evs if not e.get("synthetic")][-2])
        self.finish = lambda: None

    def hide_merge(self):
        """GitHub's history shows the merge and the record push as one push (no pr_merge update names PR #1's merge)."""
        evs = self.activity[(self.LIB, "neoforge-1.21.4")]
        m = [e for e in evs if e["activity_type"] == "pr_merge"][0]
        r = evs[evs.index(m) + 1]
        r["before"] = m["before"]
        evs.remove(m)
        self.finish = lambda: None

    def release_elsewhere(self):
        rel = next(r for r in self.releases[self.LIB] if r["tag_name"] == self.lib_tag)
        self.g(self.lib, "tag", "-f", self.lib_tag, self.lib_fix)
        rel["target_commitish"] = self.lib_fix
        self.finish()

    def drop_release(self, tag):
        for s in self.releases:
            self.releases[s] = [r for r in self.releases[s] if r["tag_name"] != tag]

    def stale_review(self, second=False):
        """The review names the fix commit; a later commit in the PR changed the code again. second: a second PR (#6)
        then names the new commit, so only the first merge's review is stale."""
        lib = self.lib
        self.g(lib, "checkout", "-q", "neoforge-1.21.4")
        self.g(lib, "reset", "-q", "--hard", f"{self.m_lib}^1")
        before = self.tip(lib, "HEAD")
        self.g(lib, "checkout", "-q", "keep-current/x-1.21.4")
        self.w(lib, "src/main/java/com/example/mod/Main.java", "class Main { int a = 4; } // LIB-1\n")
        late = self.commit(lib, "a change after the review", at(40))
        self.g(lib, "checkout", "-q", "neoforge-1.21.4")
        self.pulls[self.LIB] = [p for p in self.pulls[self.LIB] if p["number"] != 1]
        self.runs[self.LIB] = [r for r in self.runs[self.LIB] if r["id"] not in (self.r_lib, self.r_lib + 5000)]
        self.activity[(self.LIB, "neoforge-1.21.4")] = [e for e in self.activity[(self.LIB, "neoforge-1.21.4")]
                                                       if e.get("synthetic")]
        self.releases[self.LIB] = []
        m, rid = self.pr_merge(self.LIB, lib, "1.21.4", "keep-current/x-1.21.4", 1, at(60))
        last = m
        if second:
            self.g(lib, "checkout", "-q", "-b", "keep-current/y-1.21.4", m)
            self.write_review(lib, "1.21.4", late)
            self.commit(lib, "the review again", at(61))
            last, rid = self.pr_merge(self.LIB, lib, "1.21.4", "keep-current/y-1.21.4", 6, at(62))
        self.release(self.LIB, lib, self.lib_tag, last, rid, at(65))
        self.g(lib, "checkout", "-q", "neoforge-1.21.4")
        self.write_deprec(lib, self.lib_tag, last)
        b2 = self.tip(lib, "neoforge-1.21.4")
        self.commit(lib, "library record", at(99))
        self.ev(self.LIB, "neoforge-1.21.4", b2, self.tip(lib, "neoforge-1.21.4"), "push", at(100))
        self.m_lib = last
        self.relink_lib_15(last)
        self.finish()

    def early_push(self):
        """1.21.5's push at +50, before 1.21.4's PR merged (+60): it carried 1.21.4's unmerged commits."""
        e = [x for x in self.activity[(self.LIB, "neoforge-1.21.5")] if not x.get("synthetic")][0]
        e["timestamp"] = at(50)

    def abandoned_lower(self):
        """A PR into 1.21.4 (#5) that was closed unmerged; 1.21.5 merged its commit (a record) and pushed it."""
        lib = self.lib
        self.g(lib, "checkout", "-q", "-b", "keep-current/z-1.21.4", self.m_lib)   # 1.21.4 as it was at +70
        self.w(lib, ".port/1.21.4/COST.md", "a lower record never merged\n")
        z = self.commit(lib, "a lower PR's commit", at(72))
        self.g(lib, "update-ref", "refs/pull/5/head", z)
        self.pulls[self.LIB].append({"number": 5, "merged_at": None, "updated_at": at(80), "merge_commit_sha": None,
                                     "base": {"ref": "neoforge-1.21.4"}, "head": {"sha": z, "ref": "keep-current/z-1.21.4"}})
        self.g(lib, "checkout", "-q", "neoforge-1.21.5")
        before = self.tip(lib, "neoforge-1.21.5")
        self.merge(lib, z, "merge the lower PR's work", at(73))
        self.ev(self.LIB, "neoforge-1.21.5", before, self.tip(lib, "neoforge-1.21.5"), "push", at(74))
        self.finish()

    def no_repin(self, commented=False, other_repo=False):
        self.g(self.dep, "checkout", "-q", "neoforge-1.21.4")
        p = self.dep / ".port/1.21.4/branch.properties"
        text = p.read_text(encoding="utf-8")
        if other_repo:
            text = "\n".join(ln.replace(f"github.com/{self.LIB}/", "github.com/someone/else/") for ln in text.splitlines()
                             if not ln.startswith("library.")) + "\n"
        elif commented:
            text = "\n".join(("# " + ln if self.lib_tag in ln else ln) for ln in text.splitlines()) + "\n"
        else:
            text = text.replace(self.lib_tag, "examplelib-0.9-mc1.21.4")
        p.write_text(text, encoding="utf-8")
        before = self.tip(self.dep, "neoforge-1.21.4")
        self.commit(self.dep, "back to the old pin", at(111))
        self.ev(self.DEP, "neoforge-1.21.4", before, self.tip(self.dep, "neoforge-1.21.4"), "push", at(112))
        self.finish()

    def no_librecord(self):
        self.g(self.lib, "checkout", "-q", "neoforge-1.21.4")
        before = self.tip(self.lib, "neoforge-1.21.4")
        self.write_deprec(self.lib, "examplelib-0.9-mc1.21.4", self.m_lib)
        self.commit(self.lib, "a record for another tag", at(101))
        self.ev(self.LIB, "neoforge-1.21.4", before, self.tip(self.lib, "neoforge-1.21.4"), "push", at(102))
        self.finish()

    def ledger(self, status):
        """A second ledger push on the head at +112: PRE / FIX stand for a pre-window commit and the fix."""
        if status is not None:
            status = {k: ("fixed@" + self.lib_fix[:7] if s == "FIX" else
                          "fixed@" + out_of(self.lib, "log", "-1", "--format=%H", "--grep", "1.21.5 had the fix",
                                                  "neoforge-1.21.5")[:7] if s == "PRE" else s)
                      for k, s in status.items()}
        self.g(self.lib, "checkout", "-q", "neoforge-1.21.1")
        before = self.tip(self.lib, "neoforge-1.21.1")
        self.write_ledger(self.lib, status)
        self.commit(self.lib, "ledger again", at(111))
        self.ev(self.LIB, "neoforge-1.21.1", before, self.tip(self.lib, "neoforge-1.21.1"), "push", at(112))
        self.finish()

    # -- the stand-in GitHub
    def plan_issue(self):
        dep = {"repo": self.DEP, "head": "neoforge-1.21.1", "role": "dependent", "defaults": [], "candidates": [],
               "cascade": {"range": "neoforge-1.21.1..1.21.5", "decisions": []}}
        if self.opts.get("dep_bump"):
            dep["dep_updates"] = [{"id": "D1", "mc": self.opts["dep_bump"], "ga": "com.example:dep", "from": "1",
                                   "to": "2", "default": True}]
        return plan_body([{"repo": self.LIB, "head": "neoforge-1.21.1", "role": "library", "defaults": ["LIB-1"],
                           "candidates": [], "fix_at": {"LIB-1": "1.21.4"},
                           "cascade": {"range": "neoforge-1.21.1..1.21.5", "decisions": []}}, dep])

    def api(self, path):
        path = path.lstrip("/")
        p, _, q = path.partition("?")
        qs = dict(x.split("=", 1) for x in q.split("&") if "=" in x)
        m = re.fullmatch(r"repos/([^/]+/[^/]+)/(.*)", p)
        slug, rest = m.group(1), m.group(2)
        if rest == "activity":                  # newest first, two a page, a cursor in the next link
            evs = list(reversed(self.activity.get((slug, qs["ref"].split("refs/heads/", 1)[1]), [])))
            i = int(qs.get("cursor", "0"))
            nxt = f"repos/{slug}/activity?ref={qs['ref']}&cursor={i + 2}" if i + 2 < len(evs) else None
            return {"__items__": [{k: v for k, v in e.items() if k != "synthetic"} for e in evs[i:i + 2]], "__next__": nxt}
        if int(qs.get("page", "1")) > 1:
            return []
        if rest == "issues/7":
            return self.plan_issue()
        if rest == "issues/7/comments":
            return list(self.comments)
        if rest == "pulls":
            return list(self.pulls.get(slug, []))
        m2 = re.fullmatch(r"pulls/(\d+)/commits", rest)
        if m2:
            return [{"sha": c} for c in self.pr_commits.get((slug, int(m2.group(1))), [])]
        if rest == "releases":
            return [dict(r, body=self.rel_body.get(r["tag_name"], r["body"]),
                         assets=[] if r["tag_name"] in self.no_assets else r["assets"]) for r in self.releases.get(slug, [])]
        if rest == "actions/runs":
            return {"workflow_runs": [dict(r, conclusion=self.run_conclusion.get(r["id"], r["conclusion"]),
                                           pull_requests=[{"number": self.run_pr[r["id"]]}] if r["id"] in self.run_pr
                                           else r["pull_requests"])
                                      for r in self.runs.get(slug, []) if r["head_sha"] == qs.get("head_sha")]}
        m2 = re.fullmatch(r"actions/runs/(\d+)/jobs", rest)
        if m2:
            rid = int(m2.group(1))
            gate = "skipped" if rid in self.job_skip else "success"
            step = {"name": "Build and gates", "conclusion": "failure" if rid in self.step_fail else "success"}
            return {"jobs": [{"name": "gates (full)", "conclusion": gate, "steps": [step, {"name": "Gate summary", "conclusion": "success"}]},
                             {"name": "gates (minimal)", "conclusion": "skipped" if gate == "skipped" else "success",
                              "steps": [dict(step, conclusion="success")]},
                             {"name": "release", "conclusion": "success" if rid >= 5000 else "skipped"}]}
        if re.fullmatch(r"actions/runs/\d+/artifacts", rest):
            return {"artifacts": []}
        if re.fullmatch(r"commits/\w+/pulls", rest):
            return []
        if re.fullmatch(r"collaborators/[^/]+/permission", rest):
            return {"permission": "admin"}
        raise RuntimeError(f"the stand-in GitHub has no {path}")

    def raw(self, url, accept):
        m = re.fullmatch(r"https://api\.github\.com/repos/[^/]+/[^/]+/releases/assets/([^/]+)/(.+)", url)
        if not m:
            raise OSError("no such download")
        return self.assets.get(m.group(1), "| check | result |\n|---|---|\n| Gate C -- real client, `launch` | PASS | ok |\n").encode("utf-8")

    def run(self, state=None):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            return audit(f"{self.LIB}#7", [f"{self.LIB}={self.lib}", f"{self.DEP}={self.dep}"],
                         state=str(state) if state else None, pin=PIN["rev"])


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
        self.w(d, PORT_CI_WF, self.WF)
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
        evs = self.activity[(S, "neoforge-1.21.1")]
        self.activity[(S, "neoforge-1.21.1")] = [{"id": 1, "before": ZERO, "after": evs[0]["before"], "timestamp": at(-300),
                                                  "activity_type": "branch_creation"}] + evs
        self.before = [None]

    def plan_issue(self):
        return plan_body([{"repo": self.SOLO, "head": "neoforge-1.21.1", "defaults": [], "candidates": []}])

    def run(self, state=None):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            return audit(f"{self.SOLO}#7", [f"{self.SOLO}={self.lib}"], pin=PIN["rev"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--plan", help="owner/repo#N: the round's plan issue")
    ap.add_argument("--fork", action="append", default=[], help="owner/repo=DIR: each fork's clone (only read)")
    ap.add_argument("--state", help="the round's round/STATE.json, when it is at hand")
    ap.add_argument("--since", help="the window's start (ISO time; default STATE.json's, else the plan issue's creation)")
    ap.add_argument("--until", help="the window's end when the plan has no closing comment")
    ap.add_argument("--json", action="store_true", help="the report as JSON")
    ap.add_argument("--keep", action="store_true", help="keep the shadow repositories (their path is printed)")
    ap.add_argument("--pin", help=f"the migrator commit whose fork-branches.py the review and library checks are "
                                  f"cross-checked with (default {PINNED_MIGRATOR[:12]})")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args(argv)
    if a.self_check:
        return self_check()
    if not a.plan or not a.fork:
        ap.error("--plan and at least one --fork are required")
    try:
        rep, rc = audit(a.plan, a.fork, a.state, a.since, a.until, a.keep, pin=a.pin)
    except SystemExit as e:
        print(e, file=sys.stderr)
        return 2
    print(json.dumps(rep, indent=1) if a.json else render(rep))
    return rc


if __name__ == "__main__":
    sys.exit(main())
