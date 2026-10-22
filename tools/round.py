#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The keep-current weekly round as a state machine: the orchestration is a tool, the model does only the judgement.

    python3 tools/round.py init   --frozen round/frozen.json --fork owner/repo=DIR [--fork ...] [--config FILE]
    python3 tools/round.py drive  [--state round/STATE.json] [--max-hours H]   # run every mechanical step; print agent jobs
    python3 tools/round.py next   [--state ...]                  # the runnable items: agent jobs (claimed) and tool steps
    python3 tools/round.py run    ID [--state ...]               # run one mechanical step now
    python3 tools/round.py record ID [--status ok|blocked|failed] --summary FILE [--state ...]
    python3 tools/round.py status [--state ...] [--json | --markdown]
    python3 tools/round.py resume [--state ...]                  # after a restart: reap dead steps, re-list jobs
    python3 tools/round.py retry  ID [--why TEXT] [--allow] [--state ...]   # a blocked item again, after the owner answered
    python3 tools/round.py gate   [--state ...] -- COMMAND ...   # run a full gate run under the machine's one-gate lock
    python3 tools/round.py --self-check

WHY (the 2026-10-21 round, two version-branch forks, 19 versions): one model session orchestrated everything; its
context peaked at ~549k tokens although the prompt said to hand off at ~200k, and cache writes -- the whole context
re-written after every idle gap over 5 minutes (gates, CI, parallel workers) and three container restarts -- were
~51% of ~$215. A prompt rule did not keep the orchestrator small; this tool does: the round's memory is
round/STATE.json, every mechanical step (merge-ups, records, marks, PRs, CI waits, merges, release dispatch) runs
here with no model, and the model session only starts fresh workers for the jobs this prints and records their
summaries. It can be restarted at any time (`resume`). The same round also found a library S1 (a crash) only in the
dependent's Gate C AFTER the library version had merged and pre-released (reopen, re-release `-r2`, re-pin, two
weeks running): here a library version's merge waits for the dependent's Gate C against the library's candidate jar.

THE QUEUE (`init`, from the frozen plan and the clones; ordered, each item a small typed step with explicit
dependencies). Per fork, library first (`depends_on` in .github/weekly-plan.json):
  single-version fork (no `.port/<v>/branch.properties` branches) -- the existing single-version order, unchanged:
      setup (work branch keep-current/<date> cut from the head; the plan's ticked dependency bumps) -> authors (only
      with take_authors: keep-current Run steps 2-5 at the frozen upstream_commit) -> repin (a dependent, when its
      library released this round) -> backlog (the selected known issues) -> gates (ci-gates.py full and minimal) ->
      review (fresh) -> fix -> recheck (fresh; both skipped when the review left no not-ok row) -> pr -> ci -> merge
      (and its push run green) -> release (then the release run) -> ledger
  version-branch fork: the head as above when it has work (its authors' code, a fix at its version), then per version
      in ladder order, each waiting for the one below it to merge:
      mergeup (fork-branches.py cascade <lower>..<v> --gates ab; the head cascade, with the plan's decisions, at the
      first version -- an agent when a decision needs a patch written) -> repin (a dependent: the library's release of
      this round; the plan's dependency bumps) -> backlog (the issues the plan fixes AT this version) -> review ->
      fix -> recheck -> gates (ONE full run: fork-port.py --from-stage measure --gates full) -> depgate:<dependent>
      (a LIBRARY version: the dependent's Gate C against this candidate jar, before the merge) -> freeze -> pr -> ci ->
      merge -> release (dispatched right after the merge; the push run and the release run awaited together) ->
      librecord:<library> (a dependent: fork-branches.py library-record once its Gate C passed on the released jar)
      and per fork a final ledger (statuses for each fix the summaries name, then the README Ports block).
  and one close (the closing comment), once nothing else can run.
A layer fork (.multiversion/, no version branches) is refused: the skill drives it by hand.

DISPATCH. A `tool` item runs here (`drive`, `run`). An `agent` item is printed as a job: a fresh worker (the session's
Agent/Task subagent, never the orchestrator itself) gets the one-line prompt "Read <job file> and do exactly what it
says", writes a summary of at most 40 lines starting `RESULT: ok|blocked|failed` to the named file, and the
orchestrator runs the `record` line the job prints -- it never reads the worker's transcript, the logs or the
summary. A tool step that fails becomes a fixer job (`<id>+fix`, at most 2); when the fixer succeeds the step runs
again. A step the round's `allow` list does not permit is blocked and asked once on the plan issue (always allowed).

RULES ENFORCED HERE. One running item per fork clone (the checkout is shared: an item touching two forks holds both);
at most ONE full gate run on the machine at a time (an OS lock, ~/.mmvu/locks/full-gates.lock or
MMVU_FULL_GATE_LOCK; workers run any full gate run through `round.py gate -- ...`); every wait is one blocking loop
inside this tool, never a model polling; a port-cost.py mark with --phase at every item boundary (round/marks.tsv);
a blocked item stops only the items that depend on it; a failed library pre-merge dependent gate blocks the library
version's merge (it is a finding on the library version, not a reopen later).

Exit codes: 0 ok (drive: progress made or the round done); 3 nothing can run (blocked items listed); 2 bad input.
Standard library only.
"""
import argparse, concurrent.futures, contextlib, datetime, importlib.util, io, json, os, pathlib, re, shlex
import shutil, subprocess, sys, tempfile, threading, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
SKILL = ROOT / ".claude/skills/keep-current/SKILL.md"
STATE_DEFAULT = "round/STATE.json"
CONFIG = ".github/weekly-plan.json"
LOCK_ENV = "MMVU_FULL_GATE_LOCK"
MAX_FIX = 2
SUMMARY_LINES = 40
TERMINAL = ("ok", "skipped", "blocked")
PHASE = {"authors": "upstream", "mergeup": "upstream", "decisions": "upstream", "backlog": "backlog",
         "review": "review", "fix": "review", "recheck": "review"}
# the permission (the config's `allow`) a step needs; comments on the plan issue are always allowed
NEEDS = {"pr": "merge-fork-prs", "merge": "merge-fork-prs", "release": "pre-release", "ledger": "push-version-branches",
         "librecord": "push-version-branches", "grades": "push-version-branches"}
AGENT_STEPS = {"authors", "backlog", "review", "fix", "recheck", "decisions", "close", "repin-head"}
LOCKED_STEPS = {"gates", "depgate"}
SLEEP = time.sleep
POLL = 60


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, TOOLS / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_MODS = {}


def mod(key):
    files = {"fb": "fork-branches.py", "ki": "known-issues.py", "pc": "port-ci.py", "lc": "layer-ci.py"}
    if key not in _MODS:
        sys.dont_write_bytecode = True
        _MODS[key] = load(f"round_{key}", files[key])
    return _MODS[key]


def gh(path, method="GET", data=None):
    """GitHub's REST API, the way every round tool reads it (known-issues.py gh_api: GITHUB_TOKEN, else GH_TOKEN,
    else gh; bounded)."""
    return mod("ki").gh_api(path, method, data)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def git(d, *a, check=False):
    r = subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode:
        raise StepError(f"git {' '.join(a[:3])}: {(r.stderr or r.stdout).strip()[-300:]}")
    return r


class StepError(Exception):
    pass


class Blocked(Exception):
    pass


# ---------------------------------------------------------------- state (the round's only memory)

class State:
    """round/STATE.json under an OS lock: `with State.tx(path) as st:` reads it, and writes it back atomically."""

    _local = threading.Lock()

    def __init__(self, path, data):
        self.path, self.data = pathlib.Path(path), data

    @classmethod
    @contextlib.contextmanager
    def tx(cls, path, write=True):
        path = pathlib.Path(path)
        with cls._local:
            with open(str(path) + ".lock", "a+", encoding="utf-8") as fh:
                _flock(fh)
                try:
                    st = cls(path, json.loads(path.read_text(encoding="utf-8")))
                    yield st
                    if write:
                        tmp = path.with_suffix(".tmp")
                        tmp.write_text(json.dumps(st.data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
                        os.replace(tmp, path)
                finally:
                    _funlock(fh)

    @property
    def items(self):
        return self.data["items"]

    def item(self, i):
        for it in self.items:
            if it["id"] == i:
                return it
        raise KeyError(i)

    @property
    def rdir(self):
        return pathlib.Path(self.data["round_dir"])

    def fork(self, slug):
        return self.data["forks"][slug]


def _flock(fh, block=True):
    if os.name == "nt":
        import msvcrt
        while True:
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                if not block:
                    return False
                time.sleep(1)
    import fcntl
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | (0 if block else fcntl.LOCK_NB))
        return True
    except OSError:
        return False


def _funlock(fh):
    if os.name == "nt":
        import msvcrt
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def gate_lock_path():
    return pathlib.Path(os.environ.get(LOCK_ENV) or (pathlib.Path.home() / ".mmvu/locks/full-gates.lock"))


@contextlib.contextmanager
def full_gate_lock(who, say=print):
    """At most one full gate run (Gate C client, its builds) on the machine: the 2026-10-21 round's third container
    restart came with two Gate C clients running at once on 15 GB / 4 CPU. Waits for the holder, saying who it is."""
    p = gate_lock_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a+", encoding="utf-8") as fh:
        if not _flock(fh, block=False):
            holder = (p.with_suffix(".holder").read_text(encoding="utf-8").strip()
                      if p.with_suffix(".holder").is_file() else "?")
            say(f"round: waiting for the machine's full-gate lock ({p}, held by {holder})")
            _flock(fh)
        p.with_suffix(".holder").write_text(f"{who} pid {os.getpid()} since {now()}\n", encoding="utf-8")
        try:
            yield
        finally:
            _funlock(fh)


# ---------------------------------------------------------------- init: the queue

def fork_kind(d):
    """'branches' (version branches: .port/<v>/branch.properties), 'layer' (a .multiversion/ layer with no version
    branches) or 'single'."""
    fb = mod("fb")
    if fb.version_branches(d):
        return "branches"
    r = git(d, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes/origin")
    for ref in r.stdout.split():
        if git(d, "cat-file", "-e", f"{ref}:.multiversion/base.properties").returncode == 0:
            return "layer"
    return "single"


def ladder(d, head_version):
    """The fork's versions above the head, oldest first, along the newest branch's chain."""
    fb = mod("fb")
    vs = sorted(fb.version_branches(d), key=fb.mc_key)
    if not vs:
        return []
    try:
        _port, chain = fb.versions_to(d, vs[-1])
    except ValueError:
        chain = vs
    return [v for v in chain if v != head_version]


class Q:
    """Builds the item list: add() appends an item depending on the fork's previous item unless deps are given."""

    def __init__(self):
        self.items, self.last = [], {}

    def add(self, fork, version, step, deps=None, kind=None, dirs=None, extra=None, after_last=True):
        name = fork.split("/", 1)[-1]
        iid = f"{name}@{version}:{step}"
        d = list(deps or [])
        if after_last and self.last.get(fork) and self.last[fork] not in d:
            d.insert(0, self.last[fork])
        base = step.split(":", 1)[0]
        it = {"id": iid, "fork": fork, "version": version, "step": base, "arg": step.split(":", 1)[1] if ":" in step else "",
              "kind": kind or ("agent" if base in AGENT_STEPS else "tool"), "phase": PHASE.get(base, "overhead"),
              "deps": d, "dirs": dirs or [fork], "status": "pending", "fixes": 0, "lock": base in LOCKED_STEPS,
              "extra": extra or {}, "result": {}}
        self.items.append(it)
        if after_last:
            self.last[fork] = iid
        return iid


def build_queue(frozen, cfg, forks, date):
    """forks: {slug: {"dir", "kind", "head", "base", "versions"}} in config order (library first)."""
    q = Q()
    fz = {f["repo"]: f for f in frozen["forks"]}
    deps_of = {f["repo"]: [d for d in f.get("depends_on", []) if d in forks] for f in cfg["forks"] if f["repo"] in forks}
    dependents = {s: [d for d in forks if s in deps_of.get(d, [])] for s in forks}
    release_of = {}            # (fork, version) -> the release item id
    gates_of = {}
    for slug, F in forks.items():
        f = fz.get(slug, {"selected": [], "take_authors": False})
        base, kind = F["base"], F["kind"]
        fixes = f.get("fixes") or [{"id": i, "at": base} for i in f.get("selected", [])]
        at = {}
        for x in fixes:
            at.setdefault(x.get("at") or base, []).append(x)
        grades = [g for g in frozen.get("grades", []) if g.get("fork") == slug]
        if kind == "branches":
            q.add(slug, base, "ci-check")
        if grades:
            q.add(slug, base, "grades", extra={"grades": grades})
        bumps = {}
        for b in f.get("dep_bumps", []):
            bumps.setdefault(b["mc"], []).append(b)
        libs = deps_of.get(slug, [])
        lib_head = [release_of[(lb, forks[lb]["base"])] for lb in libs if (lb, forks[lb]["base"]) in release_of]
        head_work = bool(f.get("take_authors")) or bool(at.get(base)) or (kind == "single" and (bumps.get(base) or lib_head))
        if kind == "branches" and not head_work and (bumps.get(base) or lib_head):
            head_work = True
        if head_work:
            q.add(slug, base, "setup", extra={"bumps": bumps.get(base, []), "branch": f"keep-current/{date}"})
            if f.get("take_authors"):
                q.add(slug, base, "authors", extra={"upstream_commit": f.get("upstream_commit"),
                                                     "upstream": f.get("upstream"), "upstream_branch": f.get("upstream_branch")})
            if lib_head:
                q.add(slug, base, "repin-head", deps=lib_head, extra={"libraries": libs})
            if at.get(base):
                q.add(slug, base, "backlog", extra={"fixes": at[base]})
            gates_of[(slug, base)] = q.add(slug, base, "gates")
            seg_review(q, slug, base)
            for s in ("pr", "ci", "merge"):
                q.add(slug, base, s)
            release_of[(slug, base)] = q.add(slug, base, "release")
        if kind == "branches":
            casc = f.get("cascade") or {}
            head_cascade = f.get("head_cascade", True)
            prev = F["head"]
            for k, v in enumerate(F["versions"]):
                if k == 0 and not head_cascade:
                    q.add(slug, v, "mergeup", kind="tool", extra={"from": None, "why": "the plan does not take the "
                          "authors' code: no cascade from the head (only the selected fixes move)"})
                elif k == 0 and any(d.get("choice") == "patch" for d in casc.get("decisions", [])):
                    q.add(slug, v, "decisions", extra={"from": prev, "to": F["versions"][-1], "decisions": casc["decisions"]})
                else:
                    q.add(slug, v, "mergeup", extra={"from": prev, "head_cascade": k == 0,
                                                     "decisions_file": k == 0 and bool(casc.get("decisions"))})
                lib_rel = [release_of[(lb, v)] for lb in libs if (lb, v) in release_of]
                if lib_rel or bumps.get(v):
                    q.add(slug, v, "repin", deps=lib_rel, extra={"bumps": bumps.get(v, []),
                                                                "libraries": [lb for lb in libs if (lb, v) in release_of]})
                if at.get(v):
                    q.add(slug, v, "backlog", extra={"fixes": at[v]})
                seg_review(q, slug, v)
                g = gates_of[(slug, v)] = q.add(slug, v, "gates")
                if (forks[slug].get("library")):
                    for dep in dependents.get(slug, []):
                        if v in forks[dep]["versions"]:
                            q.add(slug, v, f"depgate:{dep}", dirs=[slug, dep], extra={"dependent": dep, "reset": [g]})
                q.add(slug, v, "freeze", extra={"libraries": [lb for lb in libs if (lb, v) in release_of]})
                for s in ("pr", "ci", "merge"):
                    q.add(slug, v, s)
                release_of[(slug, v)] = q.add(slug, v, "release")
                for lb in libs:
                    if (lb, v) in release_of:
                        q.add(slug, v, f"librecord:{forks[lb]['name']}", deps=[g, release_of[(slug, v)]], dirs=[slug, lb],
                              extra={"library": lb}, after_last=False)
                prev = v
        if any(it["fork"] == slug for it in q.items):
            q.add(slug, base, "ledger", deps=[it["id"] for it in q.items if it["fork"] == slug])
    q.items.append({"id": "round:close", "fork": "", "version": "", "step": "close", "arg": "", "kind": "agent",
                    "phase": "overhead", "deps": [], "dirs": [], "status": "pending", "fixes": 0, "lock": False,
                    "extra": {}, "result": {}, "close": True})
    return q.items


def seg_review(q, slug, v):
    q.add(slug, v, "review")
    q.add(slug, v, "fix")
    q.add(slug, v, "recheck")


def cmd_init(a):
    rdir = pathlib.Path(a.round_dir).resolve()
    state = pathlib.Path(a.state) if a.state else rdir / STATE_DEFAULT
    if state.exists() and not a.force:
        print(f"round: {state} exists (the round's memory): `round.py resume` continues it; --force starts over")
        return 2
    frozen = json.loads(pathlib.Path(a.frozen).read_text(encoding="utf-8"))
    dirs = {}
    for s in a.fork:
        slug, sep, d = s.partition("=")
        if not sep or "/" not in slug:
            print(f"round: --fork must be owner/repo=DIR, got {s!r}")
            return 2
        dirs[slug] = pathlib.Path(d).resolve()
    if a.config:
        cfg = json.loads(pathlib.Path(a.config).read_text(encoding="utf-8"))
    else:
        first = frozen["forks"][0]
        r = git(dirs.get(first["repo"], "."), "show", f"origin/{first['head']}:{CONFIG}")
        if r.returncode:
            print(f"round: cannot read {CONFIG} from {first['repo']}'s origin/{first['head']}: pass --config FILE")
            return 2
        cfg = json.loads(r.stdout)
    forks = {}
    for cf in cfg["forks"]:
        slug = cf["repo"]
        if slug not in dirs:
            print(f"round: no --fork {slug}=DIR (every fork of the set needs its clone; round-preflight.py says so first)")
            return 2
        d = dirs[slug]
        kind = fork_kind(d)
        if kind == "layer":
            print(f"round: {slug} is a .multiversion/ layer fork: the driver does not run a layer fork (the skill's "
                  "\"A fork with the layer\" does, by hand)")
            return 2
        head = cf["head"]
        base = head[len("neoforge-"):] if head.startswith("neoforge-") else head
        forks[slug] = {"dir": str(d), "kind": kind, "head": head, "base": base, "name": slug.split("/", 1)[1],
                       "versions": ladder(d, base) if kind == "branches" else [],
                       "library": cf.get("role") == "library", "automerge": bool(cf.get("automerge")),
                       "depends_on": cf.get("depends_on", [])}
    date = a.date or datetime.date.today().isoformat()
    items = build_queue(frozen, cfg, forks, date)
    data = {"schema": 1, "created": now(), "round_start": a.since or now(), "round_dir": str(rdir),
            "frozen": str(pathlib.Path(a.frozen).resolve()), "date": date, "allow": cfg.get("allow", []),
            "plan": {"repo": frozen.get("repo"), "issue": frozen.get("issue"), "owner": frozen.get("owner")},
            "forks": forks, "items": items}
    state.parent.mkdir(parents=True, exist_ok=True)
    tmp = state.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, state)
    print(f"round: {len(items)} item(s) for {len(forks)} fork(s) ("
          + ", ".join(f"{s}: {F['kind']}" + (f" {F['base']}..{F['versions'][-1]}" if F["versions"] else "")
                      for s, F in forks.items()) + f") -> {state}")
    print(f"round: next: python3 {TOOLS / 'round.py'} drive --state {state}")
    return 0


# ---------------------------------------------------------------- scheduling

def blocked_by(st, it, seen=None):
    """The blocked (or failed past its fixers) item this one waits on, transitively; None when it can still run."""
    seen = seen or set()
    for d in it["deps"]:
        if d in seen:
            continue
        seen.add(d)
        x = st.item(d)
        if x["status"] == "blocked":
            return x["id"]
        if x["status"] not in ("ok", "skipped"):
            b = blocked_by(st, x, seen)
            if b:
                return b
    return None


def runnable(st):
    busy = {f for it in st.items if it["status"] == "running" for f in it["dirs"]}
    out = []
    for it in st.items:
        if it.get("close"):
            continue
        if it["status"] == "failed" and it["kind"] == "tool" and not set(it["dirs"]) & busy:
            out.append(it)                                      # its fixer job
            busy |= set(it["dirs"])
            continue
        if it["status"] != "pending" or set(it["dirs"]) & busy:
            continue
        if all(st.item(d)["status"] in ("ok", "skipped") for d in it["deps"]):
            out.append(it)
            busy |= set(it["dirs"])
    if not out and not any(it["status"] == "running" for it in st.items):
        close = [it for it in st.items if it.get("close") and it["status"] == "pending"]
        out += close
    return out


def phase_mark(st, it):
    fork = it["fork"] or "(round)"
    version = it["version"] or "close"
    r = subprocess.run([sys.executable, str(TOOLS / "port-cost.py"), "mark", "--fork", fork, "--version", version,
                        "--phase", it["phase"], "--file", str(st.rdir / "round/marks.tsv")],
                       capture_output=True, text=True, encoding="utf-8")
    return r.returncode == 0


def claim(st, it):
    it["status"] = "running"
    it["started"] = now()
    it["pid"] = os.getpid()
    it["host"] = os.uname().nodename if hasattr(os, "uname") else ""
    phase_mark(st, it)


def finish(st, it, status, note="", result=None):
    it["status"] = status
    it["finished"] = now()
    it["note"] = note[-1500:]
    it.pop("pid", None)
    if result:
        it["result"].update(result)


def pid_alive(pid):
    """tools/layer-ci.py's check (Windows too, where os.kill(pid, 0) would end the process)."""
    try:
        return mod("lc").pid_alive(int(pid))
    except (ValueError, TypeError):
        return False


def reap(st):
    """Tool steps left `running` by a process that is gone (a container restart) go back to pending: every step is
    safe to run again (each checks what is already done first)."""
    n = 0
    for it in st.items:
        if it["status"] == "running" and it["kind"] == "tool" and not it.get("fixing") and not pid_alive(it.get("pid")):
            it["status"] = "pending"
            it["note"] = f"re-queued: its run (pid {it.get('pid')}) ended without a result"
            it.pop("pid", None)
            n += 1
    return n


# ---------------------------------------------------------------- agent jobs (the prompts)

SUMMARY_FORMAT = (f"Write your summary, at most {SUMMARY_LINES} lines, to {{summary}}. Its first line is exactly one of "
                  "`RESULT: ok`, `RESULT: blocked` (a finding or a question that needs the owner; say which) or "
                  "`RESULT: failed` (you could not finish; say where you stopped). Then, as they apply, one per line: "
                  "`not_ok: <n>` (a review or re-check: the not-ok rows left), `blocking: <ID> <S1|S2> <what>` (a "
                  "graded finding that blocks this version), `fixed: <ID> <sha>` (a selected known issue fixed, its fix "
                  "commit), `commit: <sha> <subject>`; then what changed, the gate verdicts and the open rows. Never "
                  "paste logs or diffs into it. Your final reply is only that file's path.")


def job_rules(st, it):
    F = st.fork(it["fork"]) if it["fork"] else None
    rules = ["Rules: you are a fresh worker of the keep-current weekly round; everything you need is named here. Read "
             f"{SKILL} only where this job points.",
             "Commit on the branch named here; never push, never open, merge or comment on a PR, never dispatch CI or "
             "a release (the round driver does all of that), never force anything, never touch another fork's clone "
             "unless named, and never change the authors' repositories.",
             f"Never run a full gate run or a Gate C client yourself except as `python3 {TOOLS / 'round.py'} gate "
             f"--state {st.path} -- <the command>` (one full run on this machine at a time); A/B gates need no lock.",
             "Run long commands with their output to a file and read only its tail, RESIDUE.md or the summary they "
             "print -- never a whole build log."]
    if F:
        rules.insert(1, f"Fork {it['fork']} ({'version branches' if F['kind'] == 'branches' else 'single-version'}), "
                        f"clone {F['dir']}, head {F['head']}. Frozen input: {st.data['frozen']}. Round directory "
                        f"{st.rdir} (run the tools from there: fork-port.py reads round/frozen.json there).")
    return rules


def where(st, it):
    F = st.fork(it["fork"])
    if F["kind"] == "branches" and it["version"] != F["base"]:
        try:
            co = vco(st, it["fork"], it["version"])
        except StepError as e:
            co = f"(no clean checkout yet: {e})"
        return f"the version branch neoforge-{it['version']}, checked out at {co}", f"neoforge-{it['version']}"
    wb = f"keep-current/{st.data['date']}"
    return f"the work branch {wb} in {F['dir']} (cut from origin/{F['head']} by the driver)", wb


def job_text(st, it, fixing=False):
    T, sp = TOOLS, st.path
    summary = st.rdir / "round/summaries" / (job_id(it, fixing) + ".md")
    F = st.fork(it["fork"]) if it["fork"] else None
    vb = F and F["kind"] == "branches" and it["version"] != F["base"]
    lines = [f"# Round job {job_id(it, fixing)}", ""]
    if fixing:
        log = it.get("result", {}).get("log", "")
        lines += [f"The driver's mechanical step `{it['step']}` for {it['fork']} {it['version']} failed:",
                  "", f"    {it.get('note', '')[-900:]}", ""]
        if log:
            lines.append(f"Its log is {log} (read only its tail and any RESIDUE.md / GATES-RED.md it names).")
        lines += [f"Find and fix the cause on {where(st, it)[0]}, commit, leave the checkout clean, and stop: the driver "
                  "runs the step again after you.", FIX_HINT.get(it["step"], ""), "",
                  "Acceptance: the cause is fixed and committed (or, when it needs the owner, RESULT: blocked naming "
                  "what to ask)."]
    else:
        lines += job_task(st, it)
    lines += ["", *job_rules(st, it), "", SUMMARY_FORMAT.format(summary=summary)]
    return "\n".join(x for x in lines if x is not None) + "\n"


FIX_HINT = {
    "mergeup": "A conflict or a red branch in the cascade: keep-current \"A fork with version branches\" step 2 (a red "
               "branch: RESIDUE.md, fork-port.py --from-stage measure with a budget) and step 3 (the 1.21.x -> 26.x "
               "boundary: resolve by hand inside the cascade's worktree, remake, cutout-check --fix, commit).",
    "gates": "A red full gate run: read .port/<v>/GATES-RED.md and RESIDUE.md (or the ci-gates summary on a "
             "single-version branch), fix the cause at this version, commit. Do not re-run the full gates yourself.",
    "depgate": "The DEPENDENT's Gate C failed against this LIBRARY version's candidate jar (built from this version's "
               "branch, before its merge). That is a finding on the library version: grade it with known-issues.py add "
               "(a crash is S1) on the library's head, find the cause in the library at this version (the dependent's "
               "GATES-RED.md / RESIDUE.md copied into the log directory name it), fix it on the library's version "
               "branch, commit. The driver re-runs the library's full gates and this dependent gate before the merge.",
    "ci": "A red CI run on the PR: read the failing job's summary (gh run view --log-failed, tail only); a job that "
          "failed before any gate body ran (setup, a dependency download) is re-run once (gh run rerun --failed); a "
          "failure inside a gate is fixed on the branch and committed (the driver pushes it).",
    "merge": "The merge or its push run failed: keep-current Run step 6's rule (one re-run of a job that failed before "
             "any gate body; anything else is reported as blocking: RESULT: blocked).",
    "release": "The release run failed: Run step 6's rule (one re-run of a job that failed before any gate body; else "
               "RESULT: blocked with the failing job's summary line). Never promote.",
    "repin": "The re-pin failed: keep-current \"A fork with version branches\" step 5 (layer-deps.py resolve "
             "--port-release, fork-branches.py ci --target <v> --modid ID --run).",
    "freeze": "fork-branches.py freeze failed: \"A fork with version branches\" step 4.",
    "ledger": "keep-current step 7 (known-issues.py status ... --set <versions>=fixed@<commit>, then render-ports "
              "--write), each committed alone on the head branch.",
    "ci-check": "keep-current \"The weekly round\" step 4 (a): regenerate the workflows it names (fork-branches.py ci "
                "--repo DIR --all --modid ID --run, and ci --repo DIR --head --run for the head) and commit them.",
}


def job_id(it, fixing=False):
    return re.sub(r"[^\w.@+-]", "_", it["id"] + ("+fix" if fixing else ""))


def job_task(st, it):
    T, s, v = TOOLS, it["step"], it["version"]
    if s == "close":
        P = st.data["plan"]
        return [f"Close the round: keep-current \"The weekly round\" step 6, on plan issue {P['repo']}#{P['issue']} "
                f"(@{P.get('owner') or 'the owner'}).",
                f"The outcome per fork is `python3 {T / 'round.py'} status --markdown --state {st.path}` (merged PRs, "
                f"pre-release tags, blocked items with their reasons) and the summaries under {st.rdir / 'round/summaries'} "
                "(read those you need: the new findings and their grades, the review fixes).",
                f"The cost table: `python3 {T / 'port-cost.py'} round-table --since {st.data['round_start']} "
                f"--session-cwd {st.rdir}` (the phase marks are in round/marks.tsv), with the authors' change size per "
                "version and the mechanical-merge share (step 6).",
                "Add the `### Lessons for the migrator` table (step 6; a `| none | | | |` row when there is none).",
                "Close the issue (state completed) only when every item in status is ok, skipped or a blocked item "
                "that waits on the owner and is named in the comment; a round that stopped leaves it open (step 5).",
                "Acceptance: one closing comment posted; the issue closed or left open by that rule."]
    F = st.fork(it["fork"])
    w, br = where(st, it)
    vb = F["kind"] == "branches" and v != F["base"]
    co = F["dir"]
    if vb:
        with contextlib.suppress(StepError):
            co = vco(st, it["fork"], v)          # the version's own checkout: check-done writes its rows only there
    if s == "authors":
        x = it["extra"]
        return [f"Take the authors' code (the plan's take_authors) into {it['fork']} at {v}: keep-current \"Run\" steps "
                f"2-5 on {w}, with the frozen upstream commit {x.get('upstream_commit')} (merge that commit, never the "
                f"branch tip {x.get('upstream')} {x.get('upstream_branch')}; a merge commit, never a rebase), reconcile "
                "every fix the port carries (step 3: take theirs only when proven, keep or shrink ours), port the new "
                "code (step 4) to a green compile and Gate A/B (ci-gates.py with --gatec none). Do not run the full "
                "gates: the driver runs them next. Record each reconcile decision in the fix ledgers (step 3.5) and "
                f"run fix-ledger.py check --release {br}.",
                f"Acceptance: {br} holds the merge and the port, A/B green, committed."]
    if s == "backlog":
        fx = it["extra"]["fixes"]
        return [f"Fix the selected known issues of the plan at {it['fork']} {v}, on {w}: keep-current \"The weekly "
                "round\" step 3 (each selected known issue, made once at this version and carried up by the merges, "
                "never fixed version by version):",
                *[f"- {x['id']}: {x.get('how') or 'fix it here'}" for x in fx],
                f"Read each in the ledger (`python3 {T / 'known-issues.py'} list --repo {F['dir']} --ref "
                f"origin/{F['head']}`), fix it with its reproduction where it has one, keep A/B green ("
                + (f"`python3 {T / 'fork-port.py'} --repo {co} --target {v} --from-stage measure --gates ab`"
                   if vb else "ci-gates.py with --gatec none") + "), commit each fix on its own. A new finding is "
                "graded with known-issues.py add (step 5). Write a `fixed: <ID> <sha>` line per fixed issue: the "
                "driver sets the ledger statuses from them.",
                f"Acceptance: each listed issue fixed and committed on {br}, or RESULT: blocked with why."]
    if s == "review":
        if vb:
            what = (f"Run `python3 {T / 'fork-port.py'} --repo {co} --target {v} --check-done` (the checkout of "
                    f"{br}): it writes REVIEW.md's `?` rows. Fill every row (fork-versions \"Done\" item 4, plus the "
                    "behaviour-inventory account, item 7) with ok / not-ok and a note, then commit .port/" + v + "/REVIEW.md "
                    f"on {br}.")
        else:
            what = (f"The work branch's changes: `git -C {F['dir']} diff --name-only origin/{F['head']}...{br}`; one row per "
                    "file -- `| file | ok/not-ok | note |` -- written to the summary (the driver puts it in the PR body).")
        return [f"You are the FRESH REVIEWER of {it['fork']} {v} (never the fixer): keep-current \"The weekly round\" "
                "step 4, \"one full review against the fixed rubric, before the merge\". The rubric: "
                f"{ROOT / 'templates/upstream-harness/reviewer-prompt.md'} and REVIEW_PRINCIPLES.md beside it.", what,
                "Do not fix anything. Write `not_ok: <n>` (the not-ok rows) in the summary.",
                "Acceptance: every row has a verdict and a note; committed (a version branch) or in the summary."]
    if s == "fix":
        rev = st.rdir / "round/summaries" / (job_id(st.item(it["id"].rsplit(":", 1)[0] + ":review")) + ".md")
        return [f"You are the FIXER of {it['fork']} {v}: fix every not-ok row of the review "
                + (f"(.port/{v}/REVIEW.md on {br})" if vb else f"({rev})") + f" on {w}, one commit per fix, A/B kept "
                "green. Never edit a verdict. Write `fixed:` lines only for selected known issues.",
                f"Acceptance: each not-ok row fixed and committed on {br}, or RESULT: blocked with why."]
    if s == "recheck":
        return [f"You are a FRESH RE-CHECKER of {it['fork']} {v} (neither the reviewer nor the fixer): re-check only the "
                "rows the fixes changed -- " + (f"the rows `python3 {T / 'fork-port.py'} --repo {co} --target {v} "
                "--check-done` reports stale since the review" if vb else "the files the fixer's commits changed") + ".",
                "That is the stopping rule (keep-current step 4): no second full review. Each not-ok row left is a "
                f"finding: grade it with `python3 {T / 'known-issues.py'} add` at its lowest affected version (step 5; "
                "a WARNING that it may repeat an entry: read that entry first). An S1 or S2 at this version blocks its "
                "merge: write its `blocking:` line and RESULT: blocked. S3/S4 are ledgered and do not block.",
                f"Acceptance: the changed rows have verdicts (committed on {br}); `not_ok: <n>` in the summary."]
    if s == "decisions":
        x = it["extra"]
        dec = st.rdir / "round/decisions" / (it["fork"].replace("/", "__") + ".decisions.json")
        return [f"The head cascade of {it['fork']} with the plan's decisions: keep-current \"The weekly round\" step 3 "
                f"(\"A version-branch fork with head_cascade: true\") and \"A fork with version branches\" step 2. Write "
                f"the diff of each decision chosen `patch` into {dec} (in the port branch's names, from the entry or "
                "CASCADE-PLAN.md), then:",
                f"    python3 {T / 'fork-branches.py'} cascade --repo {F['dir']} --apply-decisions {dec} --gates ab "
                f"--untaken-ok {st.data['frozen']}",
                f"It runs the head cascade {x['from']}..{x['to']} (its --apply first). Never resolve a decision by hand.",
                f"Acceptance: the cascade green at {v}; every decision applied or an open entry named in the summary."]
    if s == "repin-head":
        libs = it["extra"]["libraries"]
        tags = [st.item(f"{st.fork(lb)['name']}@{st.fork(lb)['base']}:release").get("result", {}).get("tag") for lb in libs]
        return [f"Re-pin {it['fork']}'s head ({F['head']}, single-version) to the library pre-release(s) this round cut: "
                + ", ".join(f"{lb}@{t}" for lb, t in zip(libs, tags)) + ". keep-current Run step 7: `python3 "
                f"{T / 'port-ci.py'} args --repo {F['dir']}` gives the current command; change the tag and asset of "
                "--dep-jar AND of any --dep-jar-variant together and rerun it, on " + w + "; commit.",
                f"Acceptance: the workflow pins the new release(s), committed on {br}."]
    return [f"(no task text for step {s})"]


# ---------------------------------------------------------------- tool steps

def say_to(log):
    def say(*parts):
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(" ".join(str(p) for p in parts) + "\n")
    return say


def sh(st, it, cmd, log, cwd=None, ok=(0,)):
    """Run a command with its output appended to the step's log; -> CompletedProcess (stdout = the output)."""
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(f"$ {' '.join(shlex.quote(str(c)) for c in cmd)}\n")
        fh.flush()
        r = subprocess.run([str(c) for c in cmd], cwd=str(cwd or st.rdir), stdout=fh, stderr=subprocess.STDOUT,
                           text=True, encoding="utf-8", errors="replace")
    if r.returncode not in ok:
        raise StepError(f"{pathlib.Path(str(cmd[1])).name if len(cmd) > 1 else cmd[0]} exit {r.returncode}: "
                        + tail(log, 6))
    return r


def tail(log, n):
    try:
        return " | ".join(pathlib.Path(log).read_text(encoding="utf-8", errors="replace").strip().splitlines()[-n:])[-900:]
    except OSError:
        return ""


def clean(d):
    r = git(d, "status", "--porcelain", "--untracked-files=no")
    if r.stdout.strip():
        raise StepError(f"the clone {d} has uncommitted changes: {r.stdout.strip().splitlines()[0]} ...")


def checkout(d, branch, create_from=None):
    clean(d)
    if git(d, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0:
        git(d, "checkout", "-q", branch, check=True)
    elif create_from:
        git(d, "checkout", "-q", "-b", branch, create_from, check=True)
    else:
        git(d, "checkout", "-q", branch, check=True)      # origin's, tracked


def vco(st, fork, v):
    """The checkout of version v's branch the tools work in: the clone itself when it is on that branch and clean,
    else the cascade's worktree under build/fork-branches/<v> (fork-branches.py worktree_for)."""
    p, why = mod("fb").worktree_for(st.fork(fork)["dir"], v)
    if not p:
        raise StepError(why)
    return str(p)


def ff_local(d, branch):
    """The local branch fast-forwarded to origin's (after a merge), wherever it is checked out."""
    git(d, "fetch", "-q", "origin", branch, check=True)
    wt = None
    for block in git(d, "worktree", "list", "--porcelain").stdout.split("\n\n"):
        if f"branch refs/heads/{branch}" in block:
            wt = block.split("\n", 1)[0].split(" ", 1)[1]
    if wt:
        git(wt, "merge", "-q", "--ff-only", f"origin/{branch}", check=True)
    elif git(d, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0:
        git(d, "branch", "-q", "-f", branch, f"origin/{branch}", check=True)


def commit_paths(d, paths, message):
    git(d, "add", "-A", "--", *paths, check=True)
    if git(d, "diff", "--cached", "--quiet").returncode == 0:
        return None
    git(d, "commit", "-q", "-m", message, check=True)
    return git(d, "rev-parse", "HEAD").stdout.strip()


def allowed(st, it):
    if it.get("allow_once"):
        return None
    need = NEEDS.get(it["step"])
    if need and need not in st.data["allow"]:
        return need
    if it["step"] == "merge" and not st.fork(it["fork"]).get("automerge"):
        return "merge-fork-prs (the fork's automerge)"
    return None


def seg_item(st, it, step):
    try:
        return st.item(f"{st.fork(it['fork'])['name']}@{it['version']}:{step}")
    except KeyError:
        return None


def is_vb(st, it):
    F = st.fork(it["fork"])
    return F["kind"] == "branches" and it["version"] != F["base"]


def branch_of(st, it):
    return f"neoforge-{it['version']}" if is_vb(st, it) else f"keep-current/{st.data['date']}"


def base_of(st, it):
    return f"neoforge-{it['version']}" if is_vb(st, it) else st.fork(it["fork"])["head"]


def t_ci_check(st, it, log):
    F = st.fork(it["fork"])
    r = sh(st, it, [sys.executable, TOOLS / "fork-branches.py", "ci-check", "--repo", F["dir"]], log)
    warn = [ln for ln in pathlib.Path(log).read_text(encoding="utf-8").splitlines() if "WARNING" in ln]
    if warn:
        raise StepError(f"{len(warn)} workflow warning(s): {warn[0][:300]}")
    return "workflows current", {}


def t_grades(st, it, log):
    F, P = st.fork(it["fork"]), st.data["plan"]
    d = F["dir"]
    checkout(d, F["head"])
    ff_local(d, F["head"])
    for g in it["extra"]["grades"]:
        sh(st, it, [sys.executable, TOOLS / "known-issues.py", "grade", "--repo", d, g["id"], "--override", g["severity"],
                    "--reason", f"{g.get('reason', '')} (plan #{P['issue']})", "--by", g.get("by", "owner")], log)
    c = commit_paths(d, [".port"], f"Known issues: the plan's grade overrides (plan #{P['issue']})")
    if c:
        git(d, "push", "-q", "origin", f"HEAD:refs/heads/{F['head']}", check=True)
    return f"{len(it['extra']['grades'])} override(s)" + (f", {c[:12]}" if c else ", nothing to commit"), {}


def t_setup(st, it, log):
    F = st.fork(it["fork"])
    d = F["dir"]
    git(d, "fetch", "-q", "origin", F["head"], check=True)
    checkout(d, it["extra"]["branch"], create_from=f"origin/{F['head']}")
    n = 0
    for b in it["extra"]["bumps"]:
        cmd = shlex.split(b["command"].replace("mmvu/tools/", str(TOOLS) + "/"))
        cmd = [d if c == f"<checkout of neoforge-{b['mc']}>" else c for c in cmd]
        sh(st, it, [sys.executable if c == "python3" else c for c in cmd], log)
        n += 1
    c = commit_paths(d, ["."], "Dependency updates the plan ticked (keep-current round)") if n else None
    return f"on {it['extra']['branch']}" + (f"; {n} bump(s), {c[:12]}" if c else ""), {}


def t_mergeup(st, it, log):
    F, x = st.fork(it["fork"]), it["extra"]
    if not x.get("from"):
        return x.get("why", "nothing to merge"), {}
    d, v = F["dir"], it["version"]
    git(d, "fetch", "-q", "origin", check=True)
    frm = x["from"]
    cmd = [sys.executable, TOOLS / "fork-branches.py", "cascade", "--repo", d]
    dec = st.rdir / "round/decisions" / (it["fork"].replace("/", "__") + ".decisions.json")
    if x.get("decisions_file") and dec.is_file():
        cmd += ["--apply-decisions", dec]
    else:
        cmd += [f"{frm}..{v}"]
    sh(st, it, cmd + ["--gates", "ab", "--untaken-ok", st.data["frozen"]], log)
    return f"{frm} merged into neoforge-{v}, A/B green", {}


def t_repin(st, it, log):
    v = it["version"]
    d = vco(st, it["fork"], v)
    pins = []
    for lb in it["extra"]["libraries"]:
        tag = st.item(f"{st.fork(lb)['name']}@{v}:release")["result"].get("tag")
        if not tag:
            raise StepError(f"{lb} {v}: its release item recorded no tag")
        sh(st, it, [sys.executable, TOOLS / "layer-deps.py", "resolve", "--repo", d, "--target", v,
                    "--port-release", f"{lb}@{tag}"], log)
        pins.append(f"{lb}@{tag}")
    for b in it["extra"]["bumps"]:
        cmd = shlex.split(b["command"].replace("mmvu/tools/", str(TOOLS) + "/"))
        cmd = [d if c.startswith("<checkout of neoforge-") else c for c in cmd]
        sh(st, it, [sys.executable if c == "python3" else c for c in cmd], log)
    if pins:
        modid = mod("fb").parse_props(mod("fb").read_file(pathlib.Path(d) / "gradle.properties")).get("mod_id")
        if not modid:
            raise StepError("no mod_id in gradle.properties: run fork-branches.py ci --target <v> --modid ID --run")
        sh(st, it, [sys.executable, TOOLS / "fork-branches.py", "ci", "--repo", d, "--target", v, "--modid", modid, "--run"], log)
    c = commit_paths(d, ["."], f"Re-pin {', '.join(pins) or 'dependencies'} at {v} (keep-current round)")
    return ("re-pinned " + ", ".join(pins) if pins else f"{len(it['extra']['bumps'])} bump(s)") + (f", {c[:12]}" if c else ""), {"pins": pins}


def sv_gate_cmds(d, upstream_commit=None):
    wf = pathlib.Path(d) / ".github/workflows/port-ci.yml"
    if not wf.is_file():
        raise StepError("no .github/workflows/port-ci.yml on the work branch: the gates' --modid cannot be read")
    text = wf.read_text(encoding="utf-8")
    args = mod("pc").recover_args(text)
    modid = args[args.index("--modid") + 1] if "--modid" in args else None
    if not modid:
        raise StepError("the Port CI workflow names no --modid")
    gatec = (re.search(r'--gatec "?([\w,]+)"?', text) or [None, "launch,spawn,battle,gauntlet"])[1]
    base = ["--base", upstream_commit] if upstream_commit and git(d, "cat-file", "-e", f"{upstream_commit}^{{commit}}").returncode == 0 else []
    out = []
    for env in ("full", "minimal"):
        out.append([sys.executable, TOOLS / "ci-gates.py", "--repo", d, "--modid", modid, *base, "--gatec", gatec,
                    "--env", env, "--summary", f"build/round-gates-{env}.md"])
    return out


def t_gates(st, it, log):
    F, v = st.fork(it["fork"]), it["version"]
    if is_vb(st, it):
        d = vco(st, it["fork"], v)
        with full_gate_lock(it["id"], say_to(log)):
            sh(st, it, [sys.executable, TOOLS / "fork-port.py", "--repo", d, "--target", v, "--from-stage", "measure",
                        "--gates", "full", "--untaken-ok", st.data["frozen"]], log, ok=(0, 18))
        if (pathlib.Path(d) / ".port" / v / "GATES-RED.md").is_file():
            raise StepError(f".port/{v}/GATES-RED.md: the full run is not green")
        c = commit_paths(d, [f".port/{v}"], f"Gate records for {v} (keep-current round)")
        return "full gates green" + (f", records {c[:12]}" if c else ""), {}
    d = F["dir"]
    checkout(d, branch_of(st, it))
    au = seg_item(st, it, "authors")
    up = au["extra"].get("upstream_commit") if au else None
    with full_gate_lock(it["id"], say_to(log)):
        for cmd in sv_gate_cmds(d, up):
            sh(st, it, cmd, log)
    return "ci-gates full and minimal green", {}


def t_depgate(st, it, log):
    """The library pre-merge dependent gate: the dependent's version branch (origin's, on a throwaway worktree) gated
    in full against the library's candidate jar -- the jar the library's own full run just built from this version's
    branch, taken with --port-checkout (development only, never committed or released)."""
    lib, dep = st.fork(it["fork"]), st.fork(it["extra"]["dependent"])
    v = it["version"]
    lco = vco(st, it["fork"], v)
    git(dep["dir"], "fetch", "-q", "origin", f"neoforge-{v}", check=True)
    work = st.rdir / "round/work" / f"depgate-{dep['name']}-{v}"
    evid = st.rdir / "round/logs" / job_id(it)
    evid.mkdir(parents=True, exist_ok=True)
    if work.exists():
        git(dep["dir"], "worktree", "remove", "--force", str(work))
        shutil.rmtree(work, ignore_errors=True)
    git(dep["dir"], "worktree", "add", "-q", "--detach", str(work), f"origin/neoforge-{v}", check=True)
    try:
        with full_gate_lock(it["id"], say_to(log)):
            r = sh(st, it, [sys.executable, TOOLS / "fork-port.py", "--repo", work, "--target", v, "--from-stage",
                            "setup", "--gates", "full", "--port-checkout", f"{it['fork']}={lco}",
                            "--allow-undone-library", "--untaken-ok", st.data["frozen"]], log, ok=range(0, 256))
        for f in ("GATES.md", "GATES-RED.md", "RESIDUE.md", "DEPS.md"):
            p = work / ".port" / v / f
            if p.is_file():
                shutil.copy2(p, evid / f)
        red = (work / ".port" / v / "GATES-RED.md").is_file()
        if r.returncode not in (0, 18) or red:
            raise StepError(f"{it['extra']['dependent']} {v}: Gate C against {it['fork']}'s candidate jar is NOT green "
                            f"(fork-port exit {r.returncode}{', GATES-RED.md' if red else ''}; evidence in {evid}): a "
                            f"blocking finding on the library version, which does not merge")
    finally:
        git(dep["dir"], "worktree", "remove", "--force", str(work))
        shutil.rmtree(work, ignore_errors=True)
        git(dep["dir"], "worktree", "prune")
    return f"{it['extra']['dependent']} {v} Gate C green against the candidate jar", {"evidence": str(evid)}


def t_freeze(st, it, log):
    v = it["version"]
    d = vco(st, it["fork"], v)
    cmd = [sys.executable, TOOLS / "fork-branches.py", "freeze", "--repo", d, "--target", v]
    for lb in it["extra"]["libraries"]:
        tag = st.item(f"{st.fork(lb)['name']}@{v}:release")["result"].get("tag")
        cmd += ["--library", f"{lb}@{tag}"]
    sh(st, it, cmd, log)
    c = commit_paths(d, [f".port/{v}/branch.properties"], f"Move the freeze of {v} (keep-current round)")
    return "freeze moved" + (f", {c[:12]}" if c else " (unchanged)"), {}


def pr_body(st, it):
    seg = [x for x in st.items if x["fork"] == it["fork"] and x["version"] == it["version"] and x["id"] != it["id"]]
    out = [f"keep-current round {st.data['date']} (plan {st.data['plan']['repo']}#{st.data['plan']['issue']}): "
           f"{it['fork']} {it['version']}, driven by tools/round.py.", "", "| step | status | note |", "|---|---|---|"]
    out += [f"| {x['step']}{':' + x['arg'] if x['arg'] else ''} | {x['status']} | {(x.get('note') or '').replace('|', '/')[:200]} |"
            for x in seg if x["status"] != "pending"]
    for x in seg:
        p = st.rdir / "round/summaries" / (job_id(x) + ".md")
        if x["kind"] == "agent" and p.is_file():
            out += ["", f"### {x['step']}", "", p.read_text(encoding="utf-8")[:6000]]
    return "\n".join(out)[:60000]


def t_pr(st, it, log):
    F, v = st.fork(it["fork"]), it["version"]
    d, slug = F["dir"], it["fork"]
    local, base = branch_of(st, it), base_of(st, it)
    work = f"keep-current/{st.data['date']}-{v}" if is_vb(st, it) else f"keep-current/{st.data['date']}"
    owner = slug.split("/")[0]
    have = gh(f"repos/{slug}/pulls?state=all&head={owner}:{work}&base={base}")
    open_ = [p for p in have if p.get("state") == "open" or p.get("merged_at")]
    r = git(d, "push", "-q", "origin", f"{local}:refs/heads/{work}")
    if r.returncode:
        raise StepError(f"push {local} -> {work}: {(r.stderr or r.stdout).strip()[-300:]}")
    sha = git(d, "rev-parse", local).stdout.strip()
    if open_:
        n = open_[0]["number"]
    else:
        p = gh(f"repos/{slug}/pulls", "POST", {"title": f"keep-current {st.data['date']}: {v}", "head": work,
                                              "base": base, "body": pr_body(st, it)})
        n = p["number"]
    return f"#{n} ({work} -> {base})", {"pr": n, "sha": sha, "work": work}


def wait_runs(slug, sha, log, event=None, branch=None, since=None, appear=900, limit=6 * 3600, want=1):
    """ONE blocking wait for the Actions runs of a commit: until at least `want` (matching event/branch/created after
    `since`) have appeared and all are completed. -> (ok, text)."""
    say = say_to(log)
    t0 = time.monotonic()
    while True:
        runs = gh(f"repos/{slug}/actions/runs?head_sha={sha}&per_page=50").get("workflow_runs", [])
        runs = [r for r in runs if (not event or r.get("event") == event) and (not branch or r.get("head_branch") == branch)
                and (not since or (r.get("created_at") or "") >= since)]
        el = time.monotonic() - t0
        if len(runs) >= want and all(r.get("status") == "completed" for r in runs):
            bad = [r for r in runs if r.get("conclusion") not in ("success", "skipped", "neutral")]
            text = "; ".join(f"{r.get('name')} {r.get('event')} {r.get('conclusion')} {r.get('html_url')}" for r in runs)
            say(f"runs: {text}")
            return not bad, text
        if len(runs) < want and el > appear:
            return False, f"no Actions run appeared for {sha[:12]} in {appear // 60} min ({event or 'any event'})"
        if el > limit:
            return False, f"still running after {limit // 3600} h: " + ", ".join(str(r.get("html_url")) for r in runs)
        SLEEP(POLL)


def t_ci(st, it, log):
    pr = seg_item(st, it, "pr")["result"]
    ok, text = wait_runs(it["fork"], pr["sha"], log, event="pull_request")
    if not ok:
        raise StepError(f"PR #{pr['pr']} CI: {text}")
    return f"PR #{pr['pr']} CI green", {}


def t_merge(st, it, log):
    slug = it["fork"]
    pr = seg_item(st, it, "pr")["result"]
    for x in ("review", "recheck"):
        r = seg_item(st, it, x)
        if r and r.get("result", {}).get("blocking"):
            raise Blocked(f"{x} left blocking finding(s): {'; '.join(r['result']['blocking'])}")
    p = gh(f"repos/{slug}/pulls/{pr['pr']}")
    if p.get("head", {}).get("sha") and p["head"]["sha"] != pr["sha"]:
        raise StepError(f"PR #{pr['pr']}'s head moved to {p['head']['sha'][:12]} after its CI: run the pr and ci steps again")
    if not p.get("merged"):
        m = gh(f"repos/{slug}/pulls/{pr['pr']}/merge", "PUT", {"merge_method": "merge", "sha": pr["sha"]})
        sha = m.get("sha")
    else:
        sha = p.get("merge_commit_sha")
    base = base_of(st, it)
    ff_local(st.fork(slug)["dir"], base)
    res = {"merge_sha": sha, "merged_at": now()}
    if not is_vb(st, it):          # the head / a single-version fork: its push run green first (Run step 6)
        ok, text = wait_runs(slug, sha, log, event="push", branch=base)
        if not ok:
            raise StepError(f"the merge's push run on {base}: {text}")
        return f"#{pr['pr']} merged ({sha[:12]}), push run green", res
    return f"#{pr['pr']} merged ({sha[:12]})", res


TAG_RE = r"-mc{v}(?:-r\d+)?$"


def t_release(st, it, log):
    slug, v = it["fork"], it["version"]
    mg = seg_item(st, it, "merge")["result"]
    base = base_of(st, it)
    since = it["result"].get("dispatched_at")
    if not since:
        since = now()
        gh(f"repos/{slug}/actions/workflows/port-ci.yml/dispatches", "POST", {"ref": base, "inputs": {"release": "true"}})
        with State.tx(st.path) as s2:       # a restart waits for this dispatch instead of dispatching again
            s2.item(it["id"])["result"]["dispatched_at"] = since
    ok, text = wait_runs(slug, mg["merge_sha"], log, event="workflow_dispatch", branch=base, since=since)
    if is_vb(st, it):              # the merge's push run, awaited together with the release run
        ok2, text2 = wait_runs(slug, mg["merge_sha"], log, event="push", branch=base)
        if not ok2:
            raise StepError(f"the merge's push run on {base} is red ({text2}): it blocks the pre-release's promotion")
    if not ok:
        raise StepError(f"the release run on {base}: {text}")
    rx = re.compile(TAG_RE.format(v=re.escape(v)))
    rel = [r for r in gh(f"repos/{slug}/releases?per_page=30") if rx.search(r.get("tag_name", ""))
           and (r.get("created_at") or "") >= since[:19]]
    if not rel:
        raise StepError(f"the release run is green but no release tagged *-mc{v} was created after {since}")
    tag = rel[0]["tag_name"]
    P = st.data["plan"]
    pr = seg_item(st, it, "pr")["result"]
    with contextlib.suppress(Exception):
        gh(f"repos/{P['repo']}/issues/{P['issue']}/comments", "POST",
           {"body": f"Round progress: {slug} {v} -- gates and review done, PR #{pr['pr']} merged, pre-release `{tag}`."})
    return f"pre-release {tag}", {"tag": tag}


def t_librecord(st, it, log):
    dep, lib = st.fork(it["fork"]), st.fork(it["extra"]["library"])
    v = it["version"]
    ff_local(lib["dir"], f"neoforge-{v}")
    dco, lco = vco(st, it["fork"], v), vco(st, it["extra"]["library"], v)
    sh(st, it, [sys.executable, TOOLS / "fork-branches.py", "library-record", "--repo", lco, "--target", v,
                "--dependent", dco, "--slug", it["extra"]["library"]], log)
    c = commit_paths(lco, [f".port/{v}/DEPENDENTS.json"], f"Library record: {it['fork']} {v} Gate C green "
                     "against the release (keep-current round)")
    if c:
        r = git(lco, "push", "-q", "origin", f"neoforge-{v}")
        if r.returncode:
            raise StepError(f"push neoforge-{v}: {(r.stderr or r.stdout).strip()[-300:]}")
    return "library record" + (f" {c[:12]}" if c else " (unchanged)"), {}


def t_ledger(st, it, log):
    F = st.fork(it["fork"])
    d = F["dir"]
    led = pathlib.Path(d) / ".port/KNOWN-ISSUES.json"
    checkout(d, F["head"])
    ff_local(d, F["head"])
    if not led.is_file():
        return "no .port/KNOWN-ISSUES.json: nothing for the driver to set (the fix ledgers are the workers')", {}
    order = [F["base"]] + F["versions"]
    sets = 0
    for x in st.items:
        if x["fork"] != it["fork"] or x["step"] not in ("backlog", "fix") or x["status"] != "ok":
            continue
        for ident, sha in x["result"].get("fixed", []):
            k = order.index(x["version"]) if x["version"] in order else 0
            reached = [v for v in order[k:] if (seg_item(st, {"fork": it["fork"], "version": v}, "merge") or {}).get("status") == "ok"]
            if F["kind"] == "branches":         # carried up only as far as the merges reached, unbroken
                reached = [v for i, v in enumerate(order[k:]) if all(w in reached for w in order[k:k + i + 1])]
            if reached:
                sh(st, it, [sys.executable, TOOLS / "known-issues.py", "status", ident, "--repo", d, "--set",
                            f"{','.join(reached)}=fixed@{sha}"], log)
                sets += 1
    c1 = commit_paths(d, [".port/KNOWN-ISSUES.json"], "Known issues: the round's fixes (keep-current round)")
    sh(st, it, [sys.executable, TOOLS / "known-issues.py", "render-ports", "--repo", d, "--head", F["head"],
                "--fetch-releases", "--write"], log)
    c2 = commit_paths(d, ["README.md"], "README Ports block (keep-current round)")
    if c1 or c2:
        r = git(d, "push", "-q", "origin", f"{F['head']}")
        if r.returncode:
            raise StepError(f"push {F['head']}: {(r.stderr or r.stdout).strip()[-300:]}")
    return f"{sets} status update(s); Ports block {'refreshed' if c2 else 'unchanged'}", {}


TOOL_IMPL = {"ci-check": t_ci_check, "grades": t_grades, "setup": t_setup, "mergeup": t_mergeup, "repin": t_repin,
             "gates": t_gates, "depgate": t_depgate, "freeze": t_freeze, "pr": t_pr, "ci": t_ci, "merge": t_merge,
             "release": t_release, "librecord": t_librecord, "ledger": t_ledger}


def ask_owner(st, it, need):
    P = st.data["plan"]
    if it.get("asked"):
        return
    with contextlib.suppress(Exception):
        gh(f"repos/{P['repo']}/issues/{P['issue']}/comments", "POST",
           {"body": f"@{P.get('owner') or 'owner'} the round needs your go-ahead for {it['fork']} {it['version']}: "
                    f"`{it['step']}` needs `{need}`, which the round's `allow` list does not hold. Everything that does "
                    "not depend on it goes on; it is named in the closing comment."})
        it["asked"] = True


def run_tool(state_path, iid):
    """Run one claimed tool item; its outcome is written to the state. -> (status, note)."""
    with State.tx(state_path, write=False) as st:
        it = st.item(iid)
        st_snapshot = st
    logd = st_snapshot.rdir / "round/logs"
    logd.mkdir(parents=True, exist_ok=True)
    log = logd / (job_id(it) + ".log")
    try:
        note, res = TOOL_IMPL[it["step"]](st_snapshot, it, log)
        status = "ok"
    except Blocked as e:
        note, res, status = str(e), {}, "blocked"
    except StepError as e:
        note, res, status = str(e), {}, "failed"
    except Exception as e:                       # noqa: BLE001 -- any crash of a step is a failed step, never the driver's
        note, res, status = f"{type(e).__name__}: {e}", {}, "failed"
    res["log"] = str(log)
    with State.tx(state_path) as st:
        x = st.item(iid)
        finish(st, x, status, note, res)
        if status == "failed" and x["fixes"] >= MAX_FIX:
            x["status"] = "blocked"
            x["note"] = f"failed again after {MAX_FIX} fixer(s): {note}"[-1500:]
    return status, note


# ---------------------------------------------------------------- the commands

def dispatch_agent(st, it):
    """Claim an agent item (or a failed tool item's fixer) and write its job file. -> the job JSON."""
    fixing = it["kind"] == "tool"
    jid = job_id(it, fixing)
    jobs = st.rdir / "round/jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    (st.rdir / "round/summaries").mkdir(parents=True, exist_ok=True)
    jf = jobs / f"{jid}.md"
    jf.write_text(job_text(st, it, fixing), encoding="utf-8")
    summary = st.rdir / "round/summaries" / f"{jid}.md"
    if fixing:
        it["fixing"] = True
        it["fixes"] += 1
        it["status"] = "running"
        it["started"] = now()
        phase_mark(st, it)
    else:
        claim(st, it)
        it.pop("pid", None)
    return {"id": jid, "fork": it["fork"], "version": it["version"], "step": it["step"] + ("+fix" if fixing else ""),
            "prompt": f"Read {jf} and do exactly what it says.", "summary": str(summary),
            "record": f"python3 {TOOLS / 'round.py'} record {jid} --summary {summary} --state {st.path}"}


def cmd_next(a, out=print):
    with State.tx(a.state) as st:
        reap(st)
        jobs, tools = [], []
        for it in runnable(st):
            need = allowed(st, it) if it["kind"] == "tool" and it["status"] == "pending" else None
            if need:
                finish(st, it, "blocked", f"needs the owner: {need}")
                ask_owner(st, it, need)
                continue
            if it["kind"] == "agent" or it["status"] == "failed":
                jobs.append(dispatch_agent(st, it))
            else:
                tools.append(it["id"])
    for j in jobs:
        out(json.dumps(j))
    for t in tools:
        out(json.dumps({"id": t, "run": f"python3 {TOOLS / 'round.py'} run {t} --state {a.state}"}))
    if not jobs and not tools:
        out(summary_line(a.state))
    return 0


def cmd_run(a, out=print):
    with State.tx(a.state) as st:
        it = st.item(a.id)
        if it["kind"] != "tool" or it["status"] not in ("pending", "failed"):
            out(f"round: {a.id} is a {it['kind']} item, {it['status']}: nothing to run")
            return 2
        claim(st, it)
    status, note = run_tool(a.state, a.id)
    out(f"round: {a.id} {status}: {note}")
    return 0 if status == "ok" else 1


def cmd_drive(a, out=print):
    """Run every runnable tool step (forks in parallel, one per clone, one full gate run at a time) until none is
    running or runnable; the agent jobs that came up are printed for the orchestrator. -> 0, or 3 when stuck."""
    lockp = pathlib.Path(a.state).with_suffix(".drive.lock")
    with open(lockp, "a+", encoding="utf-8") as lk:
        if not _flock(lk, block=False):
            out(f"round: another drive is running on {a.state}: wait for it (its jobs are printed when it ends)")
            return 2
        jobs, ran = [], []
        deadline = time.monotonic() + a.max_hours * 3600
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            running = {}
            while True:
                with State.tx(a.state) as st:
                    reap(st) if not running else None
                    for it in runnable(st):
                        if it.get("close") and running:
                            continue
                        need = allowed(st, it) if it["kind"] == "tool" and it["status"] == "pending" else None
                        if need:
                            finish(st, it, "blocked", f"needs the owner: {need}")
                            ask_owner(st, it, need)
                        elif it["kind"] == "agent" or it["status"] == "failed":
                            jobs.append(dispatch_agent(st, it))
                        elif time.monotonic() < deadline:
                            claim(st, it)
                            running[pool.submit(run_tool, a.state, it["id"])] = it["id"]
                if not running:
                    break
                done, _ = concurrent.futures.wait(running, return_when=concurrent.futures.FIRST_COMPLETED)
                for fu in done:
                    iid = running.pop(fu)
                    status, note = fu.result()
                    ran.append(f"{iid} {status}: {note[:160]}")
    for r in ran:
        out(f"round: ran {r}")
    if jobs:
        out(f"round: {len(jobs)} AGENT JOB(S): start each as a fresh subagent with its `prompt` (nothing else), and when "
            "it ends run its `record` line; never read its transcript or summary here.")
        for j in jobs:
            out(json.dumps(j))
    out(summary_line(a.state))
    with State.tx(a.state, write=False) as st:
        live = any(it["status"] == "running" for it in st.items)
        left = [it for it in st.items if it["status"] in ("pending", "failed")]
        stuck = not jobs and not ran and not live and left
        if stuck:
            for it in st.items:
                if it["status"] == "blocked":
                    out(f"round: BLOCKED {it['id']}: {it.get('note', '')[:300]}")
            out(f"round: once the owner answered one: python3 {TOOLS / 'round.py'} retry <id> --why ... [--allow]")
        if all(it["status"] in TERMINAL for it in st.items):
            out("round: ROUND DONE")
    return 3 if stuck else 0


def parse_summary(text):
    lines = text.splitlines()
    m = re.match(r"^\s*RESULT:\s*(ok|blocked|failed)\b", lines[0] if lines else "", re.I)
    res = {"status": m.group(1).lower() if m else None, "blocking": [], "fixed": [], "commits": []}
    for ln in lines[1:]:
        ln = ln.strip()
        if (k := re.match(r"^not_ok:\s*(\d+)", ln)):
            res["not_ok"] = int(k.group(1))
        elif (k := re.match(r"^blocking:\s*(\S+)\s+(S[12])\b\s*(.*)", ln)):
            res["blocking"].append(f"{k.group(1)} {k.group(2)} {k.group(3)}".strip())
        elif (k := re.match(r"^fixed:\s*(\S+)\s+([0-9a-f]{7,40})\b", ln)):
            res["fixed"].append([k.group(1), k.group(2)])
        elif (k := re.match(r"^commit:\s*([0-9a-f]{7,40})\b", ln)):
            res["commits"].append(k.group(1))
    return res


def cmd_record(a, out=print):
    text = pathlib.Path(a.summary).read_text(encoding="utf-8") if a.summary and pathlib.Path(a.summary).is_file() else ""
    parsed = parse_summary(text)
    status = a.status or parsed["status"]
    if status not in ("ok", "blocked", "failed"):
        out(f"round: {a.id}: no --status and the summary's first line is not `RESULT: ok|blocked|failed`")
        return 2
    long_ = len(text.splitlines()) > SUMMARY_LINES
    fixing = a.id.endswith("+fix")
    iid = a.id[:-4] if fixing else a.id
    with State.tx(a.state) as st:
        try:
            it = next(x for x in st.items if job_id(x) == iid or x["id"] == iid)
        except StopIteration:
            out(f"round: no item {a.id}")
            return 2
        res = {k: parsed[k] for k in ("blocking", "fixed", "commits") if parsed[k]}
        if "not_ok" in parsed:
            res["not_ok"] = parsed["not_ok"]
        res["summary"] = str(a.summary or "")
        if fixing:
            it.pop("fixing", None)
            if status == "ok":
                it["status"] = "pending"
                it["note"] = f"fixed by {a.id}; runs again"
                for r in it["extra"].get("reset", []):
                    x = st.item(r)
                    if x["status"] in ("ok", "failed"):
                        x["status"] = "pending"
                        x["note"] = f"runs again: {a.id} changed the code it gated"
            else:
                finish(st, it, "blocked" if status == "blocked" or it["fixes"] >= MAX_FIX else "failed",
                       f"{a.id}: {status}")
        else:
            if status == "ok" and parsed["blocking"]:
                status = "blocked"
            finish(st, it, status, f"{status}" + (f"; blocking: {'; '.join(parsed['blocking'])}" if parsed["blocking"] else ""), res)
            if status == "failed":
                it["attempts"] = it.get("attempts", 0) + 1
                if it["attempts"] < MAX_FIX:
                    it["status"] = "pending"           # offered once more as a fresh worker
                else:
                    it["status"] = "blocked"
            if it["step"] == "review" and status == "ok" and parsed.get("not_ok") == 0:
                for s in ("fix", "recheck"):
                    x = seg_item(st, it, s)
                    if x and x["status"] == "pending":
                        finish(st, x, "skipped", "the review left no not-ok row")
    out(f"round: {a.id} recorded {status}" + (f" (WARNING: the summary has {len(text.splitlines())} lines, over "
                                              f"{SUMMARY_LINES})" if long_ else ""))
    out(summary_line(a.state))
    return 0


def summary_line(state):
    with State.tx(state, write=False) as st:
        c = {}
        for it in st.items:
            c[it["status"]] = c.get(it["status"], 0) + 1
    return "round: " + ", ".join(f"{n} {k}" for k, n in sorted(c.items()))


SYM = {"ok": "+", "skipped": "-", "pending": ".", "running": ">", "failed": "x", "blocked": "B"}


def cmd_status(a, out=print):
    with State.tx(a.state, write=False) as st:
        if a.json:
            out(json.dumps([{k: it.get(k) for k in ("id", "status", "note", "result")} for it in st.items], indent=1))
            return 0
        rows = {}
        for it in st.items:
            rows.setdefault((it["fork"], it["version"]), []).append(it)
        if a.markdown:
            out("| fork | version | steps | result |")
            out("|---|---|---|---|")
        for (f, v), its in rows.items():
            steps = " ".join(f"{SYM.get(x['status'], '?')}{x['step']}{':' + x['arg'].split('/')[-1] if x['arg'] else ''}"
                             for x in its)
            res = []
            for x in its:
                r = x.get("result", {})
                if r.get("pr"):
                    res.append(f"#{r['pr']}")
                if r.get("tag"):
                    res.append(r["tag"])
                if x["status"] == "blocked":
                    res.append(f"BLOCKED {x['step']}: {(x.get('note') or '')[:120]}")
            line = f"| {f or '(round)'} | {v} | {steps} | {'; '.join(res)} |" if a.markdown else \
                f"{(f or '(round)'):<28} {v:<8} {steps}" + (f"  [{'; '.join(res)}]" if res else "")
            out(line)
    if not a.markdown:
        out("legend: + ok, - skipped, . pending, > running, x failed (a fixer job is due), B blocked")
        out(summary_line(a.state))
    return 0


def cmd_resume(a, out=print):
    with State.tx(a.state) as st:
        n = reap(st)
        again = []
        for it in st.items:
            if it["status"] == "running" and (it["kind"] == "agent" or it.get("fixing")):
                jid = job_id(it, bool(it.get("fixing")))
                s = st.rdir / "round/summaries" / f"{jid}.md"
                again.append((jid, s))
    out(f"round: resumed {a.state}: {n} interrupted tool step(s) re-queued")
    for jid, s in again:
        if s.is_file() and parse_summary(s.read_text(encoding="utf-8"))["status"]:
            cmd_record(argparse.Namespace(id=jid, status=None, summary=str(s), state=a.state), out)
        else:
            with State.tx(a.state, write=False) as st:
                jf = st.rdir / "round/jobs" / f"{jid}.md"
            out(json.dumps({"id": jid, "redispatch": True, "prompt": f"Read {jf} and do exactly what it says.",
                            "summary": str(s), "record": f"python3 {TOOLS / 'round.py'} record {jid} --summary {s} "
                                                         f"--state {a.state}"}))
    out(f"round: then `python3 {TOOLS / 'round.py'} drive --state {a.state}`")
    return cmd_status(argparse.Namespace(state=a.state, json=False, markdown=False), out)


def cmd_retry(a, out=print):
    """A blocked or failed item back to pending, its fixer and retry counts reset: after the owner answered (a re-grade
    below S2 recorded, a permission granted for this round in a plan-issue reply)."""
    with State.tx(a.state) as st:
        try:
            it = next(x for x in st.items if job_id(x) == a.id or x["id"] == a.id)
        except StopIteration:
            out(f"round: no item {a.id}")
            return 2
        if it["status"] not in ("blocked", "failed"):
            out(f"round: {it['id']} is {it['status']}: nothing to retry")
            return 2
        it.update(status="pending", fixes=0, attempts=0, note=f"retried at {now()}: {a.why or 'by the session'}")
        it.pop("fixing", None)
        if a.allow:
            it["allow_once"] = True
    out(f"round: {a.id} pending again")
    return 0


def cmd_gate(a, out=print):
    cmd = a.command[1:] if a.command[:1] == ["--"] else a.command
    if not cmd:
        out("round: gate needs a command after --")
        return 2
    with full_gate_lock(" ".join(cmd)[:120], out):
        return subprocess.run(cmd).returncode


# ---------------------------------------------------------------- self-check

def self_check():
    t0 = time.monotonic()
    ok = True
    n = int(os.environ.get("GIT_CONFIG_COUNT", "0") or 0)
    for k, v in (("gc.auto", "0"), ("maintenance.auto", "false")):
        os.environ[f"GIT_CONFIG_KEY_{n}"], os.environ[f"GIT_CONFIG_VALUE_{n}"] = k, v
        n += 1
    os.environ["GIT_CONFIG_COUNT"] = str(n)
    env_keep = {k: os.environ.get(k) for k in (LOCK_ENV, "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                                               "GIT_COMMITTER_EMAIL")}
    os.environ.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                       "GIT_COMMITTER_EMAIL": "t@example.com"})

    def expect(c, msg):
        nonlocal ok
        if not c:
            ok = False
            print("FAIL:", msg)

    def q(fn, *a_):
        buf = []
        rc = fn(*a_, out=buf.append)
        return rc, "\n".join(buf)

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="round-selfcheck-"))
    os.environ[LOCK_ENV] = str(tmp / "full-gates.lock")
    try:
        def repo(name, versions):
            d = tmp / name
            d.mkdir()
            g = lambda *x: git(d, *x, check=True)
            g("init", "-q", "-b", "neoforge-1.21.1")
            (d / "gradle.properties").write_text("minecraft_version=1.21.1\nmod_id=examplemod\n", encoding="utf-8")
            g("add", "-A"); g("commit", "-q", "-m", "port")
            prev = "neoforge-1.21.1"
            for v, parent_v in versions:
                g("checkout", "-q", "-b", f"neoforge-{v}")
                (d / "gradle.properties").write_text(f"minecraft_version={v}\nmod_id=examplemod\n", encoding="utf-8")
                p = d / ".port" / v
                p.mkdir(parents=True)
                (p / "branch.properties").write_text(
                    f"minecraft_version={v}\nbranch=neoforge-{v}\nparent={prev}\nparent_minecraft_version={parent_v}\n"
                    "port_branch=neoforge-1.21.1\n", encoding="utf-8")
                g("add", "-A"); g("commit", "-q", "-m", f"version {v}")
                prev = f"neoforge-{v}"
            g("checkout", "-q", "neoforge-1.21.1")
            return d
        lib = repo("examplelib", [("1.21.4", "1.21.1"), ("1.21.5", "1.21.4"), ("1.21.8", "1.21.5")])
        dep = repo("examplemod", [("1.21.4", "1.21.1"), ("1.21.5", "1.21.4")])
        single = repo("examplesolo", [])
        frozen = {"schema": 1, "issue": 7, "repo": "owner/examplelib", "owner": "owner", "forks": [
            {"repo": "owner/examplelib", "head": "neoforge-1.21.1", "take_authors": False, "head_cascade": False,
             "selected": ["LIB-1"], "cascade": {"range": "neoforge-1.21.1..1.21.8", "decisions": [],
                                                 "not_applied": []},
             "fixes": [{"id": "LIB-1", "at": "1.21.4", "how": "on neoforge-1.21.4", "cascade": "..."}], "dep_bumps": []},
            {"repo": "owner/examplemod", "head": "neoforge-1.21.1", "take_authors": True, "head_cascade": True,
             "upstream_commit": "a" * 40, "upstream": "authors", "upstream_branch": "main", "selected": [],
             "cascade": {"range": "neoforge-1.21.1..1.21.5", "decisions": [{"id": "d1", "choice": "take"}]},
             "fixes": [], "dep_bumps": [{"id": "b1", "mc": "1.21.5", "ga": "g:a", "from": "1", "to": "2",
                                         "command": "python3 mmvu/tools/layer-deps.py resolve --repo '<checkout of neoforge-1.21.5>' --target 1.21.5 --bump g:a=2"}]}],
            "grades": [], "decisions": []}
        cfg = {"forks": [{"repo": "owner/examplelib", "head": "neoforge-1.21.1", "role": "library", "automerge": True},
                         {"repo": "owner/examplemod", "head": "neoforge-1.21.1", "role": "dependent", "automerge": True,
                          "depends_on": ["owner/examplelib"]}],
               "allow": ["push-version-branches", "pre-release", "merge-fork-prs"]}
        rd = tmp / "work"
        (rd / "round").mkdir(parents=True)
        (rd / "round/frozen.json").write_text(json.dumps(frozen), encoding="utf-8")
        (rd / "cfg.json").write_text(json.dumps(cfg), encoding="utf-8")
        state = str(rd / STATE_DEFAULT)

        def init(fz, cf, forks, st_path, extra=()):
            ns = argparse.Namespace(frozen=str(fz), fork=forks, config=str(cf), round_dir=str(pathlib.Path(st_path).parent.parent),
                                    state=st_path, force=True, date="2026-10-21", since="2026-10-21T15:00:00Z")
            for k, v in extra:
                setattr(ns, k, v)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cmd_init(ns)
            return rc, buf.getvalue()
        rc, o = init(rd / "round/frozen.json", rd / "cfg.json", [f"owner/examplelib={lib}", f"owner/examplemod={dep}"], state)
        expect(rc == 0 and "owner/examplelib: branches 1.21.1..1.21.8" in o, f"init: {rc} {o}")
        S = json.loads(pathlib.Path(state).read_text(encoding="utf-8"))
        ids = [it["id"] for it in S["items"]]
        by = {it["id"]: it for it in S["items"]}
        pos = {i: k for k, i in enumerate(ids)}
        # ladder order, and the library pre-merge dependent gate between the library's own gates and its merge
        expect([it["version"] for it in S["items"] if it["fork"] == "owner/examplelib" and it["step"] == "mergeup"]
               == ["1.21.4", "1.21.5", "1.21.8"], f"the library's versions in ladder order: {ids}")
        dg = "examplelib@1.21.4:depgate:owner/examplemod"
        expect(dg in by and pos["examplelib@1.21.4:review"] < pos["examplelib@1.21.4:gates"] < pos[dg]
               < pos["examplelib@1.21.4:merge"] < pos["examplelib@1.21.4:release"]
               and dg in by["examplelib@1.21.4:freeze"]["deps"] and set(by[dg]["dirs"]) == {"owner/examplelib", "owner/examplemod"}
               and by[dg]["lock"] and by["examplelib@1.21.4:gates"]["lock"],
               f"the library version's merge waits for the dependent's Gate C against its candidate jar: {ids}")
        expect("examplelib@1.21.8:depgate:owner/examplemod" not in by and "examplelib@1.21.5:depgate:owner/examplemod" in by,
               "no dependent gate where the dependent has no branch at that version")
        expect(by["examplelib@1.21.5:mergeup"]["deps"] == ["examplelib@1.21.4:release"]
               and by["examplelib@1.21.4:mergeup"]["extra"]["from"] is None,
               "each version waits for the one below it; head_cascade false: no cascade from the head")
        expect("examplelib@1.21.1:authors" not in by and "examplelib@1.21.1:backlog" not in by
               and by["examplelib@1.21.4:backlog"]["extra"]["fixes"][0]["id"] == "LIB-1",
               "the selected fix is made at its lowest open version, not on the head")
        expect("examplelib@1.21.4:release" in by["examplemod@1.21.4:repin"]["deps"]
               and "examplelib@1.21.5:release" in by["examplemod@1.21.5:repin"]["deps"]
               and by["examplemod@1.21.5:repin"]["extra"]["bumps"][0]["id"] == "b1"
               and by["examplemod@1.21.1:authors"]["kind"] == "agent"
               and by["examplemod@1.21.4:mergeup"]["extra"]["from"] == "neoforge-1.21.1"
               and by["examplemod@1.21.4:mergeup"]["extra"]["head_cascade"],
               "the dependent re-pins each version to the library's release of this round; the ticked bump at its version")
        lr = "examplemod@1.21.4:librecord:examplelib"
        expect(lr in by and "examplemod@1.21.4:gates" in by[lr]["deps"] and "examplemod@1.21.4:release" in by[lr]["deps"]
               and set(by[lr]["dirs"]) == {"owner/examplemod", "owner/examplelib"},
               "the library record follows the dependent's own Gate C on the released jar")
        expect(by["examplemod@1.21.1:review"]["kind"] == "agent" and by["examplemod@1.21.1:gates"]["kind"] == "tool"
               and by["examplemod@1.21.1:pr"]["phase"] == "overhead" and by["examplemod@1.21.1:authors"]["phase"] == "upstream"
               and by["examplelib@1.21.4:backlog"]["phase"] == "backlog" and by["examplelib@1.21.4:fix"]["phase"] == "review",
               "kinds and phases")
        # ---- scheduling with stand-in tool steps (no git work, no GitHub)
        calls = []
        fail_once = {"examplelib@1.21.4:depgate:owner/examplemod"}

        def fake(st, it, log):
            calls.append(it["id"])
            if it["id"] in fail_once:
                fail_once.discard(it["id"])
                raise StepError("Gate C against the candidate jar is NOT green (stand-in)")
            return "stand-in ok", ({"tag": f"x-mc{it['version']}"} if it["step"] == "release" else {})
        saved = dict(TOOL_IMPL)
        for k in TOOL_IMPL:
            TOOL_IMPL[k] = fake
        a = argparse.Namespace(state=state, max_hours=1.0)
        rc, o = q(cmd_drive, a)
        jobs = [json.loads(ln) for ln in o.splitlines() if ln.startswith("{")]
        jid = {j["id"] for j in jobs}
        expect(rc == 0 and {"examplelib@1.21.1:ci-check", "examplemod@1.21.1:ci-check"} <= set(calls)
               and "examplelib@1.21.4:mergeup" in calls
               and "examplelib@1.21.4_backlog" in jid and "examplemod@1.21.1_authors" in jid,
               f"drive runs the mechanical steps and prints agent jobs for both forks: {calls} {jid} {o}")
        j0 = next(j for j in jobs if j["id"] == "examplelib@1.21.4_backlog")
        jtext = pathlib.Path(j0["prompt"].split("Read ", 1)[1].split(" and do", 1)[0]).read_text(encoding="utf-8")
        expect("LIB-1" in jtext and "RESULT: ok" in jtext and "round.py gate" in jtext and "never push" in jtext
               and len(jtext.splitlines()) < 40 and "fixed: <ID> <sha>" in jtext,
               f"a job is short and self-contained: the task, the rules, the summary format: {jtext}")
        marks = (rd / "round/marks.tsv").read_text(encoding="utf-8").splitlines()
        expect(any(m.endswith("\towner/examplelib\t1.21.4\tupstream") for m in marks)
               and any(m.endswith("\towner/examplelib\t1.21.4\tbacklog") for m in marks)
               and all(len(m.split("\t")) == 4 for m in marks),
               f"a port-cost mark with --phase at every item boundary: {marks}")
        # one running item per clone: nothing else of the library runs while its backlog job is out, not even an item of
        # the other fork that touches the library's clone too (a library record)
        with State.tx(state, write=False) as st:
            expect(not [it for it in runnable(st) if "owner/examplelib" in it["dirs"]],
                   "one running item per fork clone")
            mk = lambda i, dirs, status="pending": {"id": i, "fork": "", "version": "", "step": "x", "kind": "tool",
                                                    "deps": [], "dirs": dirs, "status": status}
            fake_st = State(st.path, {"items": [mk("a", ["o/lib"], "running"), mk("b", ["o/dep", "o/lib"]), mk("c", ["o/dep"]),
                                                mk("d", ["o/dep"])]})
            expect([x["id"] for x in runnable(fake_st)] == ["c"],
                   f"an item holding two clones waits for both; one item per clone: {[x['id'] for x in runnable(fake_st)]}")

        def rec(i, text):
            p = rd / "round/summaries" / f"{i}.md"
            p.write_text(text, encoding="utf-8")
            return q(cmd_record, argparse.Namespace(id=i, status=None, summary=str(p), state=state))

        def answers(j):
            s = j["step"]
            if s == "review":
                return "RESULT: ok\nnot_ok: 0\n"
            if s == "backlog":
                return "RESULT: ok\nfixed: LIB-1 abcdef1\n"
            return "RESULT: ok\n"

        def drive_until(hold, limit=80):
            """Drive, answering every job but those `hold` picks, until a drive prints a held job or none at all."""
            outs = []
            for _ in range(limit):
                rc_, o_ = q(cmd_drive, a)
                outs.append(o_)
                js = [json.loads(ln) for ln in o_.splitlines() if ln.startswith("{")]
                held = [j for j in js if hold(j)]
                for j in js:
                    if j not in held:
                        rec(j["id"], answers(j))
                if held or not js:
                    return held, "\n".join(outs), rc_
            return [], "\n".join(outs), 1
        for j in jobs:
            rec(j["id"], answers(j))
        js, o, rc = drive_until(lambda j: j["step"] == "depgate+fix")
        st0 = json.loads(pathlib.Path(state).read_text(encoding="utf-8"))
        by = {it["id"]: it for it in st0["items"]}
        expect(by["examplelib@1.21.4:fix"]["status"] == "skipped" and by["examplelib@1.21.4:recheck"]["status"] == "skipped",
               "a review with no not-ok row skips the fix and the re-check")
        expect(len(js) == 1 and by[dg]["status"] == "running" and by["examplelib@1.21.4:merge"]["status"] == "pending"
               and "examplelib@1.21.4:merge" not in calls and "examplelib@1.21.4:freeze" not in calls,
               f"a failed library pre-merge dependent gate blocks the library merge and becomes a fixer job: {js} {o[-800:]}")
        fx = js[0] if js else {"id": "-", "prompt": "Read /nonexistent and do"}
        ftext = pathlib.Path(fx["prompt"].split("Read ", 1)[1].split(" and do", 1)[0])
        ftext = ftext.read_text(encoding="utf-8") if ftext.is_file() else ""
        expect("finding on the library version" in ftext and "known-issues.py add" in ftext, f"the fixer job: {ftext}")
        # restart: the state is the only memory -- a fresh process sees the job that is out and lists it again
        rc, o = q(cmd_resume, argparse.Namespace(state=state))
        expect("redispatch" in o and fx["id"] in o, f"resume re-lists an agent job that has no summary yet: {o}")
        n_gates = calls.count("examplelib@1.21.4:gates")
        rec(fx["id"], "RESULT: ok\ncommit: abcdef1 fix the shared render path\n")
        with State.tx(state, write=False) as st:
            expect(st.item("examplelib@1.21.4:gates")["status"] == "pending" and st.item(dg)["status"] == "pending",
                   "the fixer's success re-runs the library's full gates and its dependent gate")
        # a blocked item stops only its dependents: the dependent's 1.21.4 re-check finds an S1
        js, o, rc = drive_until(lambda j: j["id"] == "examplemod@1.21.4_review")
        expect(calls.count("examplelib@1.21.4:gates") == n_gates + 1 and "examplelib@1.21.4:merge" in calls
               and calls.index("examplelib@1.21.4:merge") > len(calls) - 1 - calls[::-1].index(dg),
               f"after the fix the gates and the dependent gate ran once more, then the library merged: {calls}")
        for x in js:
            rec(x["id"], "RESULT: ok\nnot_ok: 0\nblocking: MOD-9 S1 crash on join\n")
        js, o, rc = drive_until(lambda j: j["id"] == "round_close")
        with State.tx(state, write=False) as st:
            expect(st.item("examplemod@1.21.4:review")["status"] == "blocked"
                   and st.item("examplemod@1.21.4:merge")["status"] == "pending"
                   and st.item("examplemod@1.21.5:mergeup")["status"] == "pending"
                   and st.item("examplelib@1.21.8:release")["status"] == "ok"
                   and st.item("examplelib@1.21.1:ledger")["status"] == "ok",
                   "a blocked item stops its dependents only: the library's ladder finished")
            expect(blocked_by(st, st.item("examplemod@1.21.5:merge")) == "examplemod@1.21.4:review", "blocked_by")
        expect([x["id"] for x in js] == ["round_close"] and rc == 0,
               f"the close comes once nothing else can run: {js} {o[-600:]}")
        cjob = pathlib.Path(js[0]["prompt"].split("Read ", 1)[1].split(" and do", 1)[0]).read_text(encoding="utf-8") if js else ""
        expect("round-table" in cjob and "Lessons for the migrator" in cjob, f"the close job: {cjob}")
        rec("round_close", "RESULT: ok\n")
        rc, o = q(cmd_drive, a)
        expect(rc == 3 and "BLOCKED examplemod@1.21.4:review" in o, f"stuck: exit 3 naming the blocked item: {o}")
        rc, o = q(cmd_status, argparse.Namespace(state=state, json=False, markdown=True))
        expect("BLOCKED review" in o and "x-mc1.21.8" in o, f"status: {o}")
        rc, o = q(cmd_retry, argparse.Namespace(id="examplemod@1.21.4:review", why="the owner re-graded MOD-9 S3",
                                                allow=False, state=state))
        with State.tx(state, write=False) as st:
            expect(rc == 0 and st.item("examplemod@1.21.4:review")["status"] == "pending", f"retry after the owner's answer: {o}")
        # permissions: a step the allow list does not hold is blocked and asked once on the plan issue
        posted = []
        global gh
        real_gh = gh
        gh = lambda path, method="GET", data=None: posted.append((path, method)) or {}
        try:
            cfg2 = dict(cfg, allow=["push-version-branches"])
            (rd / "cfg2.json").write_text(json.dumps(cfg2), encoding="utf-8")
            st2 = str(rd / "round2/round/STATE.json")
            pathlib.Path(st2).parent.mkdir(parents=True)
            frozen_s = {"schema": 1, "issue": 8, "repo": "owner/examplesolo", "owner": "owner", "grades": [],
                        "forks": [{"repo": "owner/examplesolo", "head": "neoforge-1.21.1", "take_authors": True,
                                   "upstream_commit": "b" * 40, "selected": ["SOLO-1"]}]}
            (rd / "fz-solo.json").write_text(json.dumps(frozen_s), encoding="utf-8")
            cfg_s = {"forks": [{"repo": "owner/examplesolo", "head": "neoforge-1.21.1", "role": ""}], "allow": []}
            (rd / "cfg-solo.json").write_text(json.dumps(cfg_s), encoding="utf-8")
            rc, o = init(rd / "fz-solo.json", rd / "cfg-solo.json", [f"owner/examplesolo={single}"], st2)
            S2 = json.loads(pathlib.Path(st2).read_text(encoding="utf-8"))
            steps = [it["step"] for it in S2["items"]]
            expect(rc == 0 and steps == ["setup", "authors", "backlog", "gates", "review", "fix", "recheck", "pr", "ci",
                                         "merge", "release", "ledger", "close"]
                   and all(it["version"] == "1.21.1" for it in S2["items"][:-1])
                   and not any(it["step"] in ("mergeup", "depgate", "freeze", "repin", "ci-check") for it in S2["items"]),
                   f"a single-version fork: the existing single-version order, nothing of the version branches: {steps}")
            calls.clear()
            a2 = argparse.Namespace(state=st2, max_hours=1.0)
            for _ in range(10):
                rc, o = q(cmd_drive, a2)
                js = [json.loads(ln) for ln in o.splitlines() if ln.startswith("{")]
                if not js:
                    break
                for j in js:
                    p = pathlib.Path(j["summary"])
                    p.write_text("RESULT: ok\nnot_ok: 1\n" if j["step"] == "review" else "RESULT: ok\n", encoding="utf-8")
                    q(cmd_record, argparse.Namespace(id=j["id"], status=None, summary=str(p), state=st2))
            with State.tx(st2, write=False) as st:
                expect(st.item("examplesolo@1.21.1:pr")["status"] == "blocked" and "merge-fork-prs" in st.item("examplesolo@1.21.1:pr")["note"]
                       and st.item("examplesolo@1.21.1:fix")["status"] == "ok" and "examplesolo@1.21.1:pr" not in calls
                       and sum(1 for p_, m_ in posted if p_.endswith("/issues/8/comments") and m_ == "POST") == 1,
                       f"no merge-fork-prs: the PR step is blocked and asked once on the plan issue: {posted}")
            q(cmd_retry, argparse.Namespace(id="examplesolo@1.21.1:pr", why="allowed on the plan issue", allow=True, state=st2))
            with State.tx(st2, write=False) as st:
                expect(allowed(st, st.item("examplesolo@1.21.1:pr")) is None and allowed(st, st.item("examplesolo@1.21.1:merge")),
                       "retry --allow lets that one step through, and no other")
        finally:
            gh = real_gh
        TOOL_IMPL.update(saved)
        # ---- the GitHub steps of one library version against a stand-in API and a bare origin: the work branch pushed
        # and its PR opened, CI awaited, merged with a merge commit, the release dispatched and its tag read back
        bare = tmp / "examplelib.git"
        git(tmp, "clone", "-q", "--bare", str(lib), str(bare), check=True)
        git(lib, "remote", "add", "origin", str(bare), check=True)
        git(lib, "fetch", "-q", "origin", check=True)
        sha = git(lib, "rev-parse", "neoforge-1.21.4").stdout.strip()
        api = []

        def fake_gh(path, method="GET", data=None):
            api.append((method, path.split("?")[0], data))
            if path.startswith("repos/owner/examplelib/pulls?"):
                return []
            if path == "repos/owner/examplelib/pulls" and method == "POST":
                return {"number": 5}
            if "/actions/runs?" in path:
                ev = ["pull_request", "push", "workflow_dispatch"]
                return {"workflow_runs": [{"name": "Port CI", "event": e, "head_branch": "neoforge-1.21.4", "status": "completed",
                                           "conclusion": "success", "created_at": "2999-01-01T00:00:00Z",
                                           "html_url": f"run-{e}"} for e in ev]}
            if path == "repos/owner/examplelib/pulls/5":
                return {"merged": False, "head": {"sha": sha}}
            if path.endswith("/pulls/5/merge"):
                return {"sha": sha}
            if path.startswith("repos/owner/examplelib/releases"):
                return [{"tag_name": "examplelib-1.0-mc1.21.5", "created_at": "2999-01-01T00:00:00Z"},
                        {"tag_name": "examplelib-1.0-mc1.21.4", "created_at": "2999-01-01T00:00:00Z"}]
            return {}
        gh = fake_gh
        try:
            res = {}
            for step in ("pr", "ci", "merge", "release"):
                with State.tx(state) as st:
                    st.item(f"examplelib@1.21.4:{step}")["result"] = {}
                with State.tx(state, write=False) as st:
                    pass                                      # a snapshot, as run_tool takes one: the step writes itself
                note, r = TOOL_IMPL[step](st, st.item(f"examplelib@1.21.4:{step}"), rd / f"{step}.log")
                with State.tx(state) as st:
                    st.item(f"examplelib@1.21.4:{step}")["result"].update(r)
                res[step] = (note, r)
            expect(res["pr"][1].get("pr") == 5 and git(bare, "rev-parse", "keep-current/2026-10-21-1.21.4").stdout.strip() == sha
                   and ("POST", "repos/owner/examplelib/pulls", None) not in api
                   and any(m == "POST" and p_ == "repos/owner/examplelib/pulls" and d_["base"] == "neoforge-1.21.4"
                           and d_["head"] == "keep-current/2026-10-21-1.21.4" for m, p_, d_ in api),
                   f"pr: the version's branch pushed as keep-current/<date>-<v>, its PR into neoforge-<v>: {res['pr']} {api}")
            expect(any(m == "PUT" and p_.endswith("/pulls/5/merge") and d_["merge_method"] == "merge" for m, p_, d_ in api)
                   and res["merge"][1].get("merge_sha") == sha,
                   f"merge: a merge commit, never squash or rebase: {res['merge']}")
            expect(res["release"][1].get("tag") == "examplelib-1.0-mc1.21.4"
                   and any(m == "POST" and p_.endswith("/workflows/port-ci.yml/dispatches") and d_["ref"] == "neoforge-1.21.4"
                           and d_["inputs"] == {"release": "true"} for m, p_, d_ in api)
                   and any(p_.endswith("/issues/7/comments") for m, p_, d_ in api),
                   f"release: dispatched on the version branch, its tag read back, progress posted on the plan: {res['release']}")
        finally:
            gh = real_gh
        # ---- the one-gate lock: a second holder waits
        with full_gate_lock("first", lambda *x: None):
            with open(gate_lock_path(), "a+", encoding="utf-8") as fh:
                expect(not _flock(fh, block=False), "a second full gate run waits for the machine's lock")
        with open(gate_lock_path(), "a+", encoding="utf-8") as fh:
            got = _flock(fh, block=False)
            expect(got, "the lock is free again after the run")
            if got:
                _funlock(fh)
        # ---- a restart mid-step: a tool item left running by a dead process is re-queued, not lost
        with State.tx(state) as st:
            it = st.item("examplemod@1.21.4:merge")
            it["status"], it["pid"] = "running", 2 ** 22 + 12345
        with State.tx(state) as st:
            expect(reap(st) == 1 and st.item("examplemod@1.21.4:merge")["status"] == "pending", "reap after a restart")
        # ---- the real setup step on a single-version clone: the work branch cut from the head
        with State.tx(st2, write=False) as st:
            it = st.item("examplesolo@1.21.1:setup")
            git(single, "update-ref", "refs/remotes/origin/neoforge-1.21.1", "neoforge-1.21.1")
            git(single, "remote", "add", "origin", str(single))
            note, _r = t_setup(st, it, rd / "setup.log")
            expect(git(single, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "keep-current/2026-10-21",
                   f"setup cuts keep-current/<date> from the head: {note}")
        expect(parse_summary("RESULT: blocked\nnot_ok: 2\nblocking: X-1 S2 kick\nfixed: X-2 abc1234\n")
               == {"status": "blocked", "blocking": ["X-1 S2 kick"], "fixed": [["X-2", "abc1234"]], "commits": [], "not_ok": 2},
               "the summary format")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for k, v in env_keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print(f"round self-check: {'OK' if ok else 'FAILED'} ({time.monotonic() - t0:.1f} s)")
    return 0 if ok else 1


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--self-check"]:
        return self_check()
    ap = argparse.ArgumentParser(prog="round.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="build round/STATE.json from the frozen plan and the clones")
    p.add_argument("--frozen", required=True); p.add_argument("--fork", action="append", required=True, metavar="OWNER/REPO=DIR")
    p.add_argument("--config", help=f"the fork set's {CONFIG} (default: read from the first fork's origin head)")
    p.add_argument("--round-dir", default=".", help="the round's working directory (default .)")
    p.add_argument("--state"); p.add_argument("--force", action="store_true", help="start over (the old state is lost)")
    p.add_argument("--date", help="the work branches' date (default today)"); p.add_argument("--since", help="the round's start (ISO)")
    for name, h in (("drive", "run every mechanical step; print the agent jobs"), ("next", "the runnable items"),
                    ("status", "a compact table"), ("resume", "after a restart"), ("run", "run one mechanical step"),
                    ("record", "record an agent job's outcome"), ("gate", "a full gate run under the one-gate lock"),
                    ("retry", "a blocked or failed item back to pending")):
        s = sub.add_parser(name, help=h)
        s.add_argument("--state", default=STATE_DEFAULT)
        if name in ("run", "record", "retry"):
            s.add_argument("id")
        if name == "retry":
            s.add_argument("--why", help="what changed (recorded)")
            s.add_argument("--allow", action="store_true", help="the owner allowed this one step on the plan issue")
        if name == "record":
            s.add_argument("--status", choices=["ok", "blocked", "failed"]); s.add_argument("--summary")
        if name == "status":
            s.add_argument("--json", action="store_true"); s.add_argument("--markdown", action="store_true")
        if name == "drive":
            s.add_argument("--max-hours", type=float, default=6.0, help="start no new step after this long")
        if name == "gate":
            s.add_argument("command", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    if a.cmd != "init" and not pathlib.Path(a.state).is_file():
        print(f"round: no {a.state}: run `round.py init` first")
        return 2
    return {"init": cmd_init, "drive": cmd_drive, "next": cmd_next, "status": cmd_status, "resume": cmd_resume,
            "run": cmd_run, "record": cmd_record, "gate": cmd_gate, "retry": cmd_retry}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
