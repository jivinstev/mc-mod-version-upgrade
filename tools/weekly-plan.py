#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The weekly keep-current cycle of a fork set (issue #135, part 2a): a zero-model plan issue on Tuesday, the round on
Wednesday. Both are made only by the keep-current skill's setup, which runs `install` and creates the round task from
`round-task`; re-running setup regenerates both.

    python3 tools/weekly-plan.py install --repo DIR --fork SPEC [--fork SPEC ...] [--plan-owner LOGIN|@org/team]
                                         [--owner-type user|org] [--budget N] [--migrator-repo OWNER/REPO]
                                         [--migrator-sha SHA] [--default-branch-too] [--no-commit] [--print]
    python3 tools/weekly-plan.py round-task (--repo DIR | --config FILE) [--migrator-repo OWNER/REPO]
    python3 tools/weekly-plan.py fetch    --config FILE --repo DIR --work DIR      # the workflow's steps, in order
    python3 tools/weekly-plan.py cascade  --work DIR
    python3 tools/weekly-plan.py select   --work DIR
    python3 tools/weekly-plan.py previous --work DIR                               # GITHUB_TOKEN, GITHUB_REPOSITORY
    python3 tools/weekly-plan.py open     --work DIR                               # GITHUB_TOKEN, GITHUB_REPOSITORY
    python3 tools/weekly-plan.py --self-check | --regen-golden

SPEC is `owner/repo@HEAD_BRANCH=UPSTREAM_URL#UPSTREAM_BRANCH`, one per fork of the set, the LIBRARY FIRST: the plan
issue opens on the first fork's repository, with a section per fork (the others are public and read without a token).

INSTALL (on a checkout of the first fork's head branch) writes and commits there, in one `[skip ci]` commit:
  .github/workflows/weekly-plan.yml   templates/weekly-plan.yml with this repository and a PINNED commit of it (default:
                                      origin/main), so the planner runs the reviewed tools; golden tools/golden/weekly-plan.yml
  .github/weekly-plan.json            the fork set, the budget, the round's time and `plan_owner`
Who the plan is addressed to (assigned and @mentioned): `plan_owner` (setup passes whoever ran it), else the
repository's owner when that is a user. An organization-owned fork without --plan-owner is refused: a plan is never
addressed to nobody. GitHub runs a scheduled workflow only from the repository's DEFAULT branch: when that is not the
head branch, install refuses (exit 3) unless --default-branch-too, which also commits the same workflow file to the
default branch (a local commit; push both). Nothing is pushed. An unchanged file set makes no commit.

ROUND-TASK prints the Wednesday round's scheduled task as JSON for the skill to create (create_trigger): its name, the
cron `CRON_TZ=America/Los_Angeles <minute> 8 * * 3` (minute jittered below 08:30 from the name), a fresh session per
fire, push and email notifications on, and a prompt that runs the keep-current round for the fork set, starting with
`known-issues.py read-plan`. The same name is what setup deletes before recreating it (and any older keep-current
nightly routine).

Exit codes: 0 ok; 1 a step failed (the workflow run fails, no issue is opened); 2 bad input; 3 the head branch is not
the default branch (install). Standard library only.
"""
import argparse, datetime, json, os, pathlib, re, subprocess, sys, tempfile

TOOLS = pathlib.Path(__file__).resolve().parent
ROOT = TOOLS.parent
TEMPLATE = ROOT / "templates/weekly-plan.yml"
GOLDEN = TOOLS / "golden/weekly-plan.yml"
WORKFLOW = ".github/workflows/weekly-plan.yml"
CONFIG = ".github/weekly-plan.json"
TZ = "America/Los_Angeles"
ROUND_WEEKDAY = 2              # Wednesday (date.weekday())
ROUND_NOMINAL = (8, 30)        # the round's nominal time there; the cron minute is jittered just below it
DEFAULT_BUDGET = 3
NAME_PREFIX = "keep-current weekly round"
SPEC_RE = re.compile(r"^([\w.-]+/[\w.-]+)@([\w./-]+)=(\S+?)#([\w./-]+)$")


class Fail(Exception):
    pass


def git(repo, *a, check=False, env=None):
    r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env)
    if check and r.returncode:
        raise Fail(f"git {' '.join(a[:3])}: {(r.stderr or r.stdout).strip()[-300:]}")
    return r


def ki():
    import importlib.util
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("known_issues_wp", TOOLS / "known-issues.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------- time

def la_offset_hours(now):
    """UTC offset of America/Los_Angeles at `now` (aware): -7 on daylight saving time, -8 otherwise (the template's
    first step has the same function)."""
    try:
        import zoneinfo
        return int(now.astimezone(zoneinfo.ZoneInfo(TZ)).utcoffset().total_seconds() // 3600)
    except Exception:
        y = now.year
        start = datetime.datetime(y, 3, 8 + (6 - datetime.date(y, 3, 8).weekday()) % 7, 10, tzinfo=datetime.timezone.utc)
        end = datetime.datetime(y, 11, 1 + (6 - datetime.date(y, 11, 1).weekday()) % 7, 9, tzinfo=datetime.timezone.utc)
        return -7 if start <= now.astimezone(datetime.timezone.utc) < end else -8


def la(now):
    return now.astimezone(datetime.timezone(datetime.timedelta(hours=la_offset_hours(now))))


def round_name(forks):
    return f"{NAME_PREFIX}: " + ", ".join(f["repo"] for f in forks)


def round_minute(name):
    """The cron minute: 1-15 minutes below the nominal :30, from the name (the scheduled-task jitter rule)."""
    return ROUND_NOMINAL[1] - (sum(c.isalpha() for c in name) % 15 + 1)


def plan_times(now, minute):
    """(week, deadline ISO, deadline text): the plan's week (its date in Los Angeles) and the next round start after
    `now` -- the coming Wednesday at 08:<minute> there."""
    here = la(now)
    d = here.date() + datetime.timedelta(days=(ROUND_WEEKDAY - here.weekday()) % 7)
    for _ in range(3):
        naive = datetime.datetime(d.year, d.month, d.day, ROUND_NOMINAL[0], minute)
        guess = naive.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=-8)))
        off = la_offset_hours(guess)
        at = naive.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=off)))
        if at > now:
            break
        d += datetime.timedelta(days=7)
    zone = "PDT" if off == -7 else "PST"
    return (here.date().isoformat(), at.isoformat(timespec="minutes"),
            f"{at.strftime('%A')} {at.date().isoformat()} at {at.strftime('%H:%M')} {TZ} ({zone})")


# ---------------------------------------------------------------- the workflow

def render(text, values):
    out = re.sub(r"(?<!\$)\{\{([A-Z0-9_]+)\}\}", lambda m: values.get(m.group(1), m.group(0)), text)
    left = re.findall(r"(?<!\$)\{\{[A-Z0-9_]+\}\}", out)
    if left:
        raise Fail(f"unfilled template placeholders: {left}")
    return out


def workflow(migrator_repo, sha, head):
    if not re.fullmatch(r"[0-9a-f]{40}", sha or ""):
        raise Fail(f"the pinned commit must be a full 40-digit sha, not {sha!r}")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", migrator_repo or ""):
        raise Fail(f"--migrator-repo {migrator_repo!r} is not owner/repo")
    return render(TEMPLATE.read_text(encoding="utf-8"), {"MIGRATOR_REPO": migrator_repo, "MIGRATOR_SHA": sha,
                                                         "HEAD_BRANCH": head})


def fixture():
    return workflow("owner/migrator", "a" * 40, "neoforge-1.21.1")


def gate_script(wf):
    """The first step's Python, exactly as the workflow runs it."""
    body = wf.split("python3 - <<'PY'\n", 1)[1].split("\n          PY\n", 1)[0]
    return "\n".join(l[10:] if l.startswith(" " * 10) else l.strip() for l in body.splitlines()) + "\n"


def parse_fork(spec):
    m = SPEC_RE.match(spec.strip())
    if not m:
        raise Fail(f"--fork {spec!r}: owner/repo@HEAD_BRANCH=UPSTREAM_URL#UPSTREAM_BRANCH")
    return {"repo": m.group(1), "head": m.group(2), "upstream": m.group(3), "upstream_branch": m.group(4)}


def make_config(forks, plan_owner, budget, migrator_repo):
    name = round_name(forks)
    minute = round_minute(name)
    for i, f in enumerate(forks):
        f["role"] = "library" if i == 0 and len(forks) > 1 else ("dependent" if len(forks) > 1 else "")
    cfg = {"schema": 1, "forks": forks, "budget": budget, "migrator_repo": migrator_repo,
           "round": {"name": name, "tz": TZ, "weekday": "Wednesday", "time": f"{ROUND_NOMINAL[0]:02d}:{minute:02d}",
                     "cron": f"CRON_TZ={TZ} {minute} {ROUND_NOMINAL[0]} * * 3"}}
    if plan_owner:
        cfg["plan_owner"] = plan_owner
    return cfg


def default_branch(repo):
    r = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    return r.stdout.strip().split("/", 1)[-1] if r.returncode == 0 and r.stdout.strip() else None


def commit_to(repo, branch, files, msg):
    """Commit {path: text} onto `branch` with plumbing (no checkout touched): from the local branch, else origin's.
    -> the new commit, or None when nothing changes."""
    base = None
    for ref in (f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"):
        r = git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        if r.returncode == 0:
            base = r.stdout.strip()
            break
    if not base:
        raise Fail(f"no branch {branch} (local or origin) in {repo}")
    with tempfile.TemporaryDirectory() as d:
        env = dict(os.environ, GIT_INDEX_FILE=str(pathlib.Path(d) / "index"))
        git(repo, "read-tree", base, env=env, check=True)
        for p, text in files.items():
            r = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "--stdin"], input=text.encode("utf-8"),
                               capture_output=True)                  # bytes: no newline translation on Windows
            if r.returncode:
                raise Fail(f"git hash-object: {r.stderr.decode('utf-8', 'replace').strip()[-300:]}")
            blob = r.stdout.decode("ascii").strip()
            git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{p}", env=env, check=True)
        tree = git(repo, "write-tree", env=env, check=True).stdout.strip()
    if tree == git(repo, "rev-parse", f"{base}^{{tree}}").stdout.strip():
        return None
    c = git(repo, "commit-tree", tree, "-p", base, "-m", msg, check=True).stdout.strip()
    git(repo, "update-ref", f"refs/heads/{branch}", c, check=True)
    return c


def install(a, say=print, owner_type_of=None):
    repo = pathlib.Path(a.repo).resolve()
    forks = [parse_fork(s) for s in a.fork]
    if not forks:
        raise Fail("--fork: at least one (the library first)")
    if len({f["repo"].lower() for f in forks}) != len(forks):
        raise Fail("--fork: a fork is named twice")
    head = forks[0]["head"]
    cur = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if cur != head:
        raise Fail(f"{repo} is on {cur!r}, not the head branch {head}: check it out first")
    k = ki()
    owner_login = forks[0]["repo"].split("/")[0]
    otype = a.owner_type
    if not a.plan_owner and not otype:
        try:
            otype = (owner_type_of or (lambda s: k.gh_api(f"/repos/{s}")["owner"]["type"]))(forks[0]["repo"])
        except Exception as e:
            raise Fail(f"cannot tell whether {forks[0]['repo']} is owned by a user or an organization ({e}): pass "
                       "--plan-owner LOGIN (the person running setup), or --owner-type user")
    try:
        k.resolve_owner(a.plan_owner, owner_login, otype or "")
    except k.Invalid as e:
        raise Fail(f"REFUSED: {e}")
    mrepo = a.migrator_repo or migrator_repo()
    sha = a.migrator_sha or git(ROOT, "rev-parse", "origin/main").stdout.strip()
    wf = workflow(mrepo, sha, head)
    cfg = make_config(forks, a.plan_owner, a.budget, mrepo)
    cfg_text = json.dumps(cfg, indent=2, sort_keys=True) + "\n"
    if a.print:
        say(wf, end="")
        say(cfg_text, end="")
        return 0
    dflt = default_branch(repo)
    also = None
    if dflt and dflt != head:
        if not a.default_branch_too:
            say(f"weekly-plan: REFUSED: GitHub runs a scheduled workflow only from the default branch, which is {dflt}, "
                f"not the head branch {head}. Either make {head} the default branch (Settings -> Branches) and rerun, or "
                f"rerun with --default-branch-too to commit the same {WORKFLOW} to {dflt} as well. Nothing was written.")
            return 3
        also = dflt
    elif not dflt:
        say("weekly-plan: note: origin/HEAD is unknown (git remote set-head origin -a): make sure the head branch is the "
            "default branch, or the schedule will not fire")
    files = {WORKFLOW: wf, CONFIG: cfg_text}
    changed = []
    for p, text in files.items():
        f = repo / p
        if not f.is_file() or f.read_text(encoding="utf-8") != text:
            f.parent.mkdir(parents=True, exist_ok=True)
            with open(f, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            changed.append(p)
    msg = ("Weekly plan: the keep-current plan workflow and its config [skip ci]\n\n"
           f"Every Tuesday at 08:00 {TZ} a GitHub Actions workflow, with no model and no secrets, opens one plan\n"
           "issue for the fork owner: the authors' new commits, the cascade plan and the known issues the Wednesday\n"
           f"round takes. Generated by {mrepo}'s tools/weekly-plan.py install (keep-current setup) with the tools\n"
           f"pinned at {sha[:12]}; re-run setup to change it, do not edit by hand.\n")
    if not changed:
        say(f"weekly-plan: {WORKFLOW} and {CONFIG} unchanged on {head}")
    elif a.no_commit:
        say(f"weekly-plan: wrote {', '.join(changed)} (not committed)")
    else:
        git(repo, "add", *changed, check=True)
        git(repo, "commit", "-q", "-m", msg, check=True)
        say(f"weekly-plan: committed {', '.join(changed)} on {head} ({git(repo, 'rev-parse', '--short', 'HEAD').stdout.strip()})")
    if also:
        c = commit_to(repo, also, {WORKFLOW: wf}, msg)
        say(f"weekly-plan: {WORKFLOW} on {also}: " + (f"committed {c[:12]}" if c else "unchanged"))
    say(f"weekly-plan: plan issues go to {k.mention(k.resolve_owner(a.plan_owner, owner_login, otype or ''))} on "
        f"{forks[0]['repo']}; the round: {cfg['round']['cron']}. Push: git push origin {head}" + (f" {also}" if also else ""))
    return 0


def migrator_repo():
    url = git(ROOT, "remote", "get-url", "origin").stdout.strip()
    m = re.search(r"github\.com[/:]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", url)
    if not m:
        raise Fail(f"cannot read this repository's GitHub name from {url!r}: pass --migrator-repo")
    return m.group(1)


def round_task(cfg, mrepo):
    forks = cfg["forks"]
    first = forks[0]
    lines = [f"Run the keep-current skill's weekly round for this fork set, unattended (fresh session, no human present).",
             "", "Fork set (dependency order, the library first; the plan issue is on the first):"]
    lines += [f"- {f['repo']} head {f['head']}" + (f" ({f['role']})" if f.get("role") else "")
              + f"; authors' upstream {f['upstream']} {f['upstream_branch']}" for f in forks]
    lines += ["", "Steps:",
              f"1. git clone --depth 1 https://github.com/{mrepo} mmvu (public, read only; never ask for it to be "
              "attached) and read mmvu/.claude/skills/keep-current/SKILL.md, section \"The weekly round\". Follow it "
              "exactly, in order.",
              f"2. Its first step freezes the input: python3 mmvu/tools/known-issues.py read-plan --latest --slug "
              f"{first['repo']} --out round/frozen.json --decisions-dir round/decisions. The selected known issues and "
              "the authors' changes it says to take are the run's only inputs; nothing else enters except a new S1. "
              "With no open plan issue, stop and report.",
              "3. Look for each fork's clone in this session. If one is missing, still do step 2, then stop: name the "
              "repository and say it must be added to this scheduled task's repositories.",
              "4. Never promote a release, publish to Modrinth or CurseForge, push to or comment on the authors' "
              "repositories, force-push, or merge into the migrator repository."]
    name = cfg["round"]["name"]
    return {"name": name, "cron_expression": cfg["round"]["cron"], "create_new_session_on_fire": True,
            "notifications": {"push": True, "email": True}, "initiation": "human_request", "prompt": "\n".join(lines),
            "replaces": [name, "keep-current (nightly routine of an earlier setup, if any)"],
            "repositories": [f["repo"] for f in forks]}


# ---------------------------------------------------------------- the workflow's steps

def load_input(work):
    p = pathlib.Path(work) / "plan-input.json"
    return json.loads(p.read_text(encoding="utf-8"))


def save_input(work, inp):
    (pathlib.Path(work) / "plan-input.json").write_text(json.dumps(inp, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def head_ref(clone, head):
    for ref in (f"refs/remotes/origin/{head}", f"refs/heads/{head}"):
        r = git(clone, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        if r.returncode == 0:
            return ref, r.stdout.strip()
    raise Fail(f"{clone}: no branch {head}")


def step_fetch(config, repo, work, now=None, clone=None):
    """Clone the other forks (public), fetch each fork's authors' upstream, list their new commits, and merge them into
    the head in this throwaway checkout so the cascade plan sees them."""
    cfg = json.loads(pathlib.Path(config).read_text(encoding="utf-8"))
    work = pathlib.Path(work)
    work.mkdir(parents=True, exist_ok=True)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    minute = int(cfg["round"]["time"].split(":")[1])
    week, deadline, text = plan_times(now, minute)
    forks = []
    for i, f in enumerate(cfg["forks"]):
        d = pathlib.Path(repo) if i == 0 else work / "forks" / str(i)
        if i and not (d / ".git").exists():
            url = clone(f["repo"]) if clone else f"https://github.com/{f['repo']}.git"
            r = subprocess.run(["git", "clone", "-q", url, str(d)], capture_output=True, text=True, encoding="utf-8",
                               errors="replace")
            if r.returncode:
                raise Fail(f"cannot clone {f['repo']} (it must be public): {r.stderr.strip()[-300:]}")
        ref, hc = head_ref(d, f["head"])
        up = f"refs/remotes/upstream/{f['upstream_branch']}"
        git(d, "fetch", "-q", "--no-tags", f["upstream"], f"+refs/heads/{f['upstream_branch']}:{up}", check=True)
        uc = git(d, "rev-parse", up, check=True).stdout.strip()
        log = git(d, "log", "--format=%H%x09%s", f"{hc}..{uc}", check=True).stdout.splitlines()
        commits = [{"sha": l.split("\t", 1)[0], "subject": l.split("\t", 1)[1] if "\t" in l else ""} for l in log]
        merged, conflicts = False, []
        if commits:
            git(d, "checkout", "-q", "-B", f["head"], hc, check=True)
            r = git(d, "-c", "user.name=weekly-plan", "-c", "user.email=weekly-plan@localhost", "merge", "-q", "--no-edit",
                    "--no-ff", uc)
            if r.returncode:
                conflicts = git(d, "diff", "--name-only", "--diff-filter=U").stdout.split()
                git(d, "merge", "--abort")
                git(d, "checkout", "-q", "-B", f["head"], hc, check=True)
            else:
                merged = True
        forks.append(dict(f, clone=str(d.resolve()), head_ref=ref, head_commit=hc, upstream_commit=uc,
                          new_commits=commits, new_count=len(commits), authors_merged=merged, merge_conflicts=conflicts))
        print(f"weekly-plan: {f['repo']}: {len(commits)} new commit(s) from {f['upstream']} {f['upstream_branch']} "
              f"at {uc[:12]}" + (f"; merging them conflicts in {', '.join(conflicts[:6])}" if conflicts else ""))
    inp = {"schema": 1, "repo": cfg["forks"][0]["repo"], "now": now.isoformat(timespec="seconds"), "week": week,
           "deadline": deadline, "deadline_text": text, "budget": cfg.get("budget", DEFAULT_BUDGET),
           "plan_owner": cfg.get("plan_owner"), "round": cfg["round"], "forks": forks,
           "migrator": {"repo": cfg.get("migrator_repo"), "sha": git(TOOLS, "rev-parse", "HEAD").stdout.strip() or None}}
    save_input(work, inp)
    return 0


def fb_mod():
    import importlib.util
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("fork_branches_wp", TOOLS / "fork-branches.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def step_cascade(work):
    """Each version-branch fork: fork-branches.py cascade <head>..<newest> --plan (a single-version fork: none)."""
    inp = load_input(work)
    fb = fb_mod()
    for i, f in enumerate(inp["forks"]):
        vs = sorted(fb.version_branches(f["clone"]), key=fb.mc_key)
        if not vs:
            f["cascade"] = None
            print(f"weekly-plan: {f['repo']}: a single-version fork, no cascade to plan")
            continue
        spec = f"{f['head']}..{vs[-1]}"
        out = pathlib.Path(work) / f"cascade-{i}"
        r = subprocess.run([sys.executable, str(TOOLS / "fork-branches.py"), "cascade", "--repo", f["clone"], spec,
                            "--plan", "--out", str(out)], capture_output=True, text=True, encoding="utf-8")
        print(r.stdout[-3000:], end="")
        if r.returncode or not (out / "CASCADE-PLAN.json").is_file():
            raise Fail(f"{f['repo']}: cascade {spec} --plan failed (exit {r.returncode}): {r.stderr.strip()[-500:]}")
        plan = json.loads((out / "CASCADE-PLAN.json").read_text(encoding="utf-8"))
        f["cascade"] = {"range": spec, "plan": {
            "mechanical": plan.get("mechanical", []), "versions": plan.get("versions", []),
            "decisions": [{k: d.get(k) for k in ("id", "file", "versions", "escalate", "choice", "reasoning", "class")}
                          for d in plan.get("decisions", [])]}}
    save_input(work, inp)
    return 0


def step_select(work):
    """Each fork: known-issues.py select (the default policy) and the other open issues, from the head's ledger."""
    inp = load_input(work)
    ki_py = str(TOOLS / "known-issues.py")
    for f in inp["forks"]:
        L = ["--repo", f["clone"], "--ref", f["head_commit"]]
        runs = []
        for args in (["select", *L, "--budget", str(inp.get("budget", DEFAULT_BUDGET)), "--json"],
                     ["list", *L, "--open", "--json"], ["list", *L, "--json"]):
            r = subprocess.run([sys.executable, ki_py, *args], capture_output=True, text=True, encoding="utf-8")
            if r.returncode:
                raise Fail(f"{f['repo']}: known-issues.py {args[0]}: {(r.stderr or r.stdout).strip()[-400:]}")
            runs.append(json.loads(r.stdout))
        sel, opened, every = runs
        title = {e["id"]: e["title"] for e in every}
        f["selection"] = [dict(s, title=title.get(s["id"], "")) for s in sel]
        taken = {s["id"] for s in sel}
        f["candidates"] = [{"id": e["id"], "severity": e["severity"], "lowest": e["lowest"], "title": e["title"]}
                           for e in opened if e["id"] not in taken]
        f["ledger_ids"] = [e["id"] for e in every]
        print(f"weekly-plan: {f['repo']}: {len(sel)} known issue(s) by default, {len(f['candidates'])} other open")
    save_input(work, inp)
    return 0


def step_previous(work, api=None, slug=None):
    """Resolve the owner (plan_owner, else the repository's user owner) and read the open plan issues: one of this week
    already open makes `open` a no-op; an older one no round closed is carried forward."""
    k = ki()
    api = api or k.gh_api
    inp = load_input(work)
    slug = slug or os.environ.get("GITHUB_REPOSITORY") or inp["repo"]
    info = api(f"/repos/{slug}")
    try:
        owner = k.resolve_owner(inp.get("plan_owner"), info["owner"]["login"], info["owner"]["type"])
    except k.Invalid as e:
        raise Fail(str(e))
    inp["owner"], inp["repo"] = owner, slug
    issues = k.open_plan_issues(slug, api)
    same = [i for i in issues if _week_of(k, i) == inp["week"]]
    older = [i for i in issues if _week_of(k, i) != inp["week"]]
    inp["already_open"] = same[0]["number"] if same else None
    inp["previous"] = None
    if older and not same:
        p = older[0]
        comments = k.gh_pages(f"/repos/{slug}/issues/{p['number']}/comments", api)
        frozen = k.read_plan(p, comments, k.owner_check(slug, owner, api), inp["now"])
        inp["previous"] = {"issue": p["number"], "frozen": frozen}
    inp["supersede"] = [i["number"] for i in older] if not same else []
    print(f"weekly-plan: owner {k.mention(owner)}; " + (f"this week's plan is already #{same[0]['number']}" if same else
          (f"carrying #{older[0]['number']} forward (no round closed it)" if older else "no earlier plan issue open")))
    save_input(work, inp)
    return 0


def _week_of(k, issue):
    try:
        return k.plan_data(issue.get("body") or "").get("week")
    except k.Invalid:
        return None


def step_open(work, api=None, slug=None):
    """Open the rendered plan issue, assigned to the owner (a user); then close each older one as superseded."""
    k = ki()
    api = api or k.gh_api
    inp = load_input(work)
    slug = slug or os.environ.get("GITHUB_REPOSITORY") or inp["repo"]
    if inp.get("already_open"):
        print(f"weekly-plan: this week's plan issue is already open (#{inp['already_open']}): nothing opened")
        return 0
    res = json.loads((pathlib.Path(work) / "issue.json").read_text(encoding="utf-8"))
    if not res.get("title") or k.PLAN_MARK not in res.get("body", "") or k.DATA_BEGIN not in res.get("body", ""):
        raise Fail("the rendered plan is incomplete: no issue opened")
    payload = {"title": res["title"], "body": res["body"]}
    if inp["owner"].get("login"):
        payload["assignees"] = [inp["owner"]["login"]]
    try:
        made = api(f"/repos/{slug}/issues", "POST", payload)
    except Exception as e:
        if "assignees" not in payload:
            raise
        print(f"weekly-plan: could not assign {inp['owner']['login']} ({e}); opening it with the @mention only")
        payload.pop("assignees")
        made = api(f"/repos/{slug}/issues", "POST", payload)
    n = made["number"]
    for old in inp.get("supersede", []):
        api(f"/repos/{slug}/issues/{old}/comments", "POST",
            {"body": f"Superseded by #{n}: last week's round did not run, so this plan's ticks and replies were carried "
                     f"forward into #{n}."})
        api(f"/repos/{slug}/issues/{old}", "PATCH", {"state": "closed", "state_reason": "not_planned"})
    url = made.get("html_url") or f"https://github.com/{slug}/issues/{n}"
    print(f"weekly-plan: opened #{n}: {res['title']} -- {url}")
    s = os.environ.get("GITHUB_STEP_SUMMARY")
    if s:
        with open(s, "a", encoding="utf-8") as fh:
            fh.write(f"Plan issue: [{res['title']}]({url})\n")
    return 0


# ---------------------------------------------------------------- self-check

def self_check():
    import contextlib, io, time
    t0 = time.monotonic()
    bad = []
    expect = lambda c, what: c or bad.append(what)
    UTC = datetime.timezone.utc
    wf = fixture()
    golden = GOLDEN.read_text(encoding="utf-8") if GOLDEN.is_file() else ""
    expect(wf == golden, "the rendered workflow differs from tools/golden/weekly-plan.yml (regenerate with --regen-golden "
                         "after reading the diff)")
    expect('cron: "0 15 * * 2"' in wf and 'cron: "0 16 * * 2"' in wf and "workflow_dispatch:" in wf,
           "Tuesdays at 15:00 and 16:00 UTC, and workflow_dispatch")
    perms = wf.split("\npermissions:\n", 1)[1].split("\n\n", 1)[0]
    expect(perms == "  contents: read\n  issues: write", f"permissions contents: read and issues: write only: {perms!r}")
    expect("secrets." not in wf and "${{ github.token }}" in wf, "no secrets: the default token only")
    expect("ref: " + "a" * 40 in wf and "repository: owner/migrator" in wf, "the planner tools at the pinned commit")
    code = "\n".join(l for l in wf.splitlines() if not l.lstrip().startswith("#"))
    expect(not re.search(r"\bclaude\b|anthropic", code, re.I) and "port-ci-kit" not in wf,
           "no model and not the Port CI kit")
    steps = re.findall(r"\n      - name: (.*)", wf)
    expect(steps[0].startswith("Continue only at 08:00") and [s[:2] for s in steps if s[:1].isdigit()] ==
           ["1.", "2.", "3.", "4.", "5."], f"the gate first, then the five steps in order: {steps}")
    expect(all(b.count("if: steps.when.outputs.go == 'true'") == 1 for b in wf.split("\n      - name: ")[2:]),
           "every step after the gate runs only when it lets the run through")
    try:
        import yaml
        y = yaml.safe_load(wf)
        expect(y[True]["schedule"] == [{"cron": "0 15 * * 2"}, {"cron": "0 16 * * 2"}] and "workflow_dispatch" in y[True]
               and y["permissions"] == {"contents": "read", "issues": "write"} and len(y["jobs"]["plan"]["steps"]) == 9,
               "the workflow parses as YAML, with its triggers, permissions and steps")
    except ImportError:
        pass
    for bad_sha in ("abc", "g" * 40):
        try:
            workflow("o/m", bad_sha, "neoforge-1.21.1"); bad.append(f"a pinned commit {bad_sha!r} is refused")
        except Fail:
            pass
    # the gate, exactly the workflow's Python: the 08:00 slot on both sides of daylight saving, a late start, manual runs
    ns = {"__name__": "gate"}
    exec(compile(gate_script(wf), "gate", "exec"), ns)
    go = ns["go"]
    summer, winter = datetime.datetime(2026, 7, 14, 15, 0, tzinfo=UTC), datetime.datetime(2026, 12, 15, 16, 0, tzinfo=UTC)
    expect(go("schedule", "0 15 * * 2", summer) and not go("schedule", "0 16 * * 2", summer + datetime.timedelta(hours=1)),
           "daylight saving (UTC-7): the 15:00 slot plans, the 16:00 one does not")
    expect(go("schedule", "0 16 * * 2", winter) and not go("schedule", "0 15 * * 2", winter - datetime.timedelta(hours=1)),
           "standard time (UTC-8): the 16:00 slot plans, the 15:00 one does not")
    expect(go("schedule", "0 15 * * 2", summer + datetime.timedelta(minutes=70)),
           "a run GitHub starts late still plans from its slot (judged by the cron, not the clock)")
    expect(go("workflow_dispatch", "", winter) and not go("schedule", "", winter), "a manual run always plans")
    for when in (datetime.datetime(2026, 3, 8, 9, 59, tzinfo=UTC), datetime.datetime(2026, 3, 8, 10, 0, tzinfo=UTC),
                 datetime.datetime(2026, 11, 1, 8, 59, tzinfo=UTC), datetime.datetime(2026, 11, 1, 9, 0, tzinfo=UTC),
                 summer, winter):
        expect(ns["la_offset_hours"](when) == la_offset_hours(when), f"the workflow's and the tool's offset agree at {when}")
    import builtins
    real_import = builtins.__import__

    def no_tz(name, *a, **kw):
        if name == "zoneinfo":
            raise ImportError("no tz database")
        return real_import(name, *a, **kw)
    builtins.__import__ = no_tz
    try:
        rule = [ns["la_offset_hours"](w) for w in (datetime.datetime(2026, 3, 8, 9, 59, tzinfo=UTC),
                                                   datetime.datetime(2026, 3, 8, 10, 0, tzinfo=UTC),
                                                   datetime.datetime(2026, 11, 1, 8, 59, tzinfo=UTC),
                                                   datetime.datetime(2026, 11, 1, 9, 0, tzinfo=UTC))]
    finally:
        builtins.__import__ = real_import
    expect(rule == [-8, -7, -7, -8], f"without a tz database the US rule switches at 02:00 local: {rule}")
    # the plan's week and deadline: the coming Wednesday at the round's time, in Los Angeles
    wk, dl, txt = plan_times(summer, 21)
    expect(wk == "2026-07-14" and dl == "2026-07-15T08:21-07:00" and txt.startswith("Wednesday 2026-07-15 at 08:21"),
           f"Tuesday's plan: Wednesday's round, PDT: {wk} {dl} {txt}")
    wk, dl, _ = plan_times(winter, 21)
    expect(dl == "2026-12-16T08:21-08:00", f"PST deadline: {dl}")
    _, dl, _ = plan_times(datetime.datetime(2026, 7, 15, 16, 0, tzinfo=UTC), 21)
    expect(dl == "2026-07-22T08:21-07:00", f"a manual run after this week's round plans next week's: {dl}")
    # config and the round task
    forks = [parse_fork("o/lib@neoforge-1.21.1=https://github.com/a/lib#main"),
             parse_fork("o/dep@neoforge-1.21.1=https://github.com/a/dep.git#dev")]
    cfg = make_config(forks, "someone", 3, "owner/migrator")
    task = round_task(cfg, "owner/migrator")
    m = re.fullmatch(r"CRON_TZ=America/Los_Angeles (\d+) 8 \* \* 3", task["cron_expression"])
    expect(m and 15 <= int(m.group(1)) <= 29 and cfg["round"]["time"] == f"08:{int(m.group(1)):02d}",
           f"the round: Wednesday, a jittered minute just below 08:30 Los Angeles: {task['cron_expression']}")
    expect(task["create_new_session_on_fire"] and task["notifications"] == {"push": True, "email": True},
           "a fresh session each fire, push and email on")
    expect("read-plan --latest --slug o/lib" in task["prompt"] and "The weekly round" in task["prompt"]
           and task["name"].startswith(NAME_PREFIX) and task["name"] in task["replaces"], f"the round prompt: {task}")
    expect(forks[0]["role"] == "library" and forks[1]["role"] == "dependent", "the first fork is the library")
    for s in ("o/lib", "o/lib@b=https://x", "o/lib@b#main"):
        try:
            parse_fork(s); bad.append(f"a bad --fork {s!r} is refused")
        except Fail:
            pass
    k = ki()
    expect(k.resolve_owner("someone", "o", "Organization") == {"login": "someone"}, "plan_owner wins")
    expect(k.resolve_owner(None, "o", "User") == {"login": "o"}, "a user-owned repository: its owner")
    expect(k.resolve_owner("@org/team", "org", "Organization") == {"team": "org/team"}, "an org with an owner team")
    try:
        k.resolve_owner(None, "org", "Organization"); bad.append("an org-owned fork without plan_owner is refused")
    except k.Invalid:
        pass

    # install on a tiny fixture: refused off the head branch, refused for an org without plan_owner, refused when the
    # head is not the default branch; idempotent; --default-branch-too commits the workflow to the default branch
    def gq(d, *a):
        return subprocess.run(["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *a],
                              capture_output=True, text=True, encoding="utf-8").stdout.strip()
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        origin, fork = d / "origin.git", d / "fork"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "neoforge-1.21.1", str(origin)], check=True)
        subprocess.run(["git", "clone", "-q", str(origin), str(fork)], check=True, capture_output=True)
        gq(fork, "checkout", "-q", "-b", "neoforge-1.21.1")
        (fork / "README.md").write_text("x\n", encoding="utf-8")
        gq(fork, "add", "-A"); gq(fork, "commit", "-qm", "c"); gq(fork, "push", "-q", "origin", "neoforge-1.21.1")
        gq(fork, "remote", "set-head", "origin", "neoforge-1.21.1")
        for k_, v_ in (("user.name", "t"), ("user.email", "t@example.invalid")):
            gq(fork, "config", k_, v_)
        ap = build_parser()
        base = ["install", "--repo", str(fork), "--fork", "o/lib@neoforge-1.21.1=https://github.com/a/lib#main",
                "--migrator-repo", "owner/migrator", "--migrator-sha", "a" * 40]

        def inst(*extra, otype=None):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                try:
                    rc = install(ap.parse_args(base + list(extra)), owner_type_of=(lambda s: otype) if otype else None)
                except Fail as e:
                    rc = 2; print(e)
            return rc, buf.getvalue()
        rc, o = inst(otype="Organization")
        expect(rc == 2 and "REFUSED" in o and "plan_owner" in o and not (fork / WORKFLOW).exists(),
               f"an org-owned fork without --plan-owner is refused, nothing written: {rc} {o}")
        rc, o = inst("--plan-owner", "someone")
        wf_written = (fork / WORKFLOW).read_text(encoding="utf-8") if (fork / WORKFLOW).is_file() else ""
        expect(rc == 0 and "committed" in o and wf_written == fixture() and "[skip ci]" in gq(fork, "log", "-1", "--format=%s")
               and json.loads((fork / CONFIG).read_text(encoding="utf-8"))["plan_owner"] == "someone",
               f"install commits the golden workflow and plan_owner on the head branch, [skip ci]: {rc} {o}")
        n = gq(fork, "rev-list", "--count", "HEAD")
        rc, o = inst("--plan-owner", "someone")
        expect(rc == 0 and "unchanged" in o and gq(fork, "rev-list", "--count", "HEAD") == n,
               f"re-running setup with the same values commits nothing: {o}")
        rc, o = inst(otype="User")
        expect(rc == 0 and "plan_owner" not in json.loads((fork / CONFIG).read_text(encoding="utf-8")) and "@o" in o,
               f"a user-owned fork without --plan-owner: addressed to its owner at run time: {o}")
        gq(fork, "checkout", "-q", "-b", "other")
        rc, o = inst("--plan-owner", "someone")
        expect(rc == 2 and "not the head branch" in o, f"refused off the head branch: {o}")
        gq(fork, "checkout", "-q", "neoforge-1.21.1")
        gq(fork, "branch", "main", "neoforge-1.21.1~1"); gq(fork, "push", "-q", "origin", "main")
        gq(fork, "remote", "set-head", "origin", "main")
        before = gq(fork, "rev-parse", "HEAD")
        rc, o = inst("--plan-owner", "other")
        expect(rc == 3 and "default branch" in o and gq(fork, "rev-parse", "HEAD") == before,
               f"head is not the default branch: refused, nothing written: {rc} {o}")
        rc, o = inst("--plan-owner", "other", "--default-branch-too")
        expect(rc == 0 and gq(fork, "show", f"main:{WORKFLOW}") == fixture().rstrip("\n")
               and gq(fork, "show", "main:README.md") == "x" and gq(fork, "status", "--porcelain") == "",
               f"--default-branch-too: the workflow also committed to the default branch, no checkout touched: {o}")

        # the steps, offline: a library fork with a version branch is too big a fixture here (fork-branches'
        # own self-check covers cascade --plan); a single-version fork set of two, upstream with new commits
        up = d / "up"
        subprocess.run(["git", "init", "-q", "-b", "main", str(up)], check=True)
        (up / "README.md").write_text("x\n", encoding="utf-8")
        gq(up, "add", "-A"); gq(up, "commit", "-qm", "root")
        lib = d / "libfork"
        subprocess.run(["git", "clone", "-q", str(up), str(lib)], check=True, capture_output=True)
        gq(lib, "checkout", "-q", "-b", "neoforge-1.21.1")
        (lib / "gradle.properties").write_text("minecraft_version=1.21.1\n", encoding="utf-8")
        gq(lib, "add", "-A"); gq(lib, "commit", "-qm", "port")
        led = lib / ".port/KNOWN-ISSUES.json"
        led.parent.mkdir()
        for args in (["--title", "crash on load", "--failure", "crash", "--visibility", "common", "--reach", "normal",
                      "--workaround", "no", "--lowest", "1.21.1"],
                     ["--title", "a seam", "--failure", "cosmetic", "--visibility", "rare", "--reach", "normal",
                      "--workaround", "no", "--lowest", "1.21.1"]):
            with contextlib.redirect_stdout(io.StringIO()):
                k.main(["add", "--repo", str(lib), *args])
        gq(lib, "add", "-A"); gq(lib, "commit", "-qm", "ledger")
        gq(lib, "update-ref", "refs/remotes/origin/neoforge-1.21.1", "HEAD")
        (up / "A.java").write_text("class A {}\n", encoding="utf-8")
        gq(up, "add", "-A"); gq(up, "commit", "-qm", "authors: a new feature")
        dep = d / "depfork"
        subprocess.run(["git", "clone", "-q", str(up), str(dep)], check=True, capture_output=True)
        gq(dep, "checkout", "-q", "-b", "neoforge-1.21.1", "HEAD~1")
        (dep / "gradle.properties").write_text("minecraft_version=1.21.1\n", encoding="utf-8")
        gq(dep, "add", "-A"); gq(dep, "commit", "-qm", "port")
        conf = d / "weekly-plan.json"
        c2 = make_config([parse_fork(f"o/lib@neoforge-1.21.1={up}#main"), parse_fork(f"o/dep@neoforge-1.21.1={up}#main")],
                         "someone", 3, "owner/migrator")
        conf.write_text(json.dumps(c2), encoding="utf-8")
        w = d / "plan"
        with contextlib.redirect_stdout(io.StringIO()):
            step_fetch(conf, lib, w, now=summer, clone=lambda r: str(dep))
            step_cascade(w)
            step_select(w)
        inp = load_input(w)
        f0 = inp["forks"][0]
        expect(f0["new_count"] == 1 and f0["new_commits"][0]["subject"] == "authors: a new feature" and f0["authors_merged"]
               and inp["deadline"] == "2026-07-15T" + c2["round"]["time"] + "-07:00",
               f"fetch: the authors' new commits, merged in the throwaway checkout; the deadline: {f0} {inp['deadline']}")
        expect(f0["cascade"] is None and [s["id"] for s in f0["selection"]] == ["KI-1"]
               and [c["id"] for c in f0["candidates"]] == ["KI-2"] and f0["selection"][0]["title"] == "crash on load",
               f"a single-version fork: no cascade; select's S1 by default, the S3 a candidate: {f0}")
        # previous / open against a fake GitHub: carry-forward from an unclosed plan, then supersede it
        issues, calls = {}, []

        def api(path, method="GET", data=None):
            calls.append((method, path))
            p = path.split("?")[0]
            if p == "/repos/o/lib":
                return {"owner": {"login": "o", "type": "Organization"}}
            if p == "/repos/o/lib/issues" and method == "GET":
                return [dict(v, number=n) for n, v in issues.items() if v.get("state", "open") == "open"] if "page=1" in path else []
            if p == "/repos/o/lib/issues" and method == "POST":
                n = max(issues, default=10) + 1
                issues[n] = dict(data, number=n, state="open", comments=[])
                return issues[n]
            m = re.fullmatch(r"/repos/o/lib/issues/(\d+)/comments", p)
            if m:
                if method == "POST":
                    issues[int(m.group(1))]["comments"].append(data["body"]); return {}
                return issues[int(m.group(1))].get("thread", []) if "page=1" in path else []
            m = re.fullmatch(r"/repos/o/lib/issues/(\d+)", p)
            if m and method == "PATCH":
                issues[int(m.group(1))].update(data); return {}
            m = re.fullmatch(r"/repos/o/lib/collaborators/([^/]+)/permission", p)
            if m:
                return {"permission": "write" if m.group(1) == "writer" else "read"}
            raise RuntimeError(f"unexpected {method} {path}")
        with contextlib.redirect_stdout(io.StringIO()):
            step_previous(w, api, "o/lib")
        rp = subprocess.run([sys.executable, str(TOOLS / "known-issues.py"), "render-plan", "--input",
                             str(w / "plan-input.json"), "--out", str(w / "issue.json")], capture_output=True, text=True,
                            encoding="utf-8")
        expect(rp.returncode == 0, f"render-plan: {rp.stderr}")
        with contextlib.redirect_stdout(io.StringIO()):
            step_open(w, api, "o/lib")
        first = issues.get(11, {})
        expect(first.get("assignees") == ["someone"] and first["body"].startswith(k.PLAN_MARK + "\n**Deadline: the round "
               "starts Wednesday 2026-07-15") and "@someone" in first["body"] and "[x] Take the authors' new commits" in first["body"]
               and "- [x] **S1** `KI-1`" in first["body"] and "- [ ] **S3** `KI-2`" in first["body"]
               and "## o/dep (dependent)" in first["body"] and not first["title"].startswith("[decision needed]"),
               f"the plan issue: deadline first, assigned and @mentioned, a section per fork: {first}")
        # this week's issue already open: a second run (e.g. a manual dispatch) opens nothing
        with contextlib.redirect_stdout(io.StringIO()):
            step_previous(w, api, "o/lib")
            step_open(w, api, "o/lib")
        expect(len(issues) == 1, "a second run the same week opens no second issue")
        # next week, no round closed #11: the owner unticked KI-1 and graded KI-2; a drive-by comment is ignored
        issues[11]["body"] = issues[11]["body"].replace("- [x] **S1** `KI-1`", "- [ ] **S1** `KI-1`", 1)
        issues[11]["thread"] = [{"id": 1, "created_at": "2026-07-14T16:00:00Z", "user": {"login": "someone"},
                                 "body": "/grade o/lib:KI-2 S2 seen by everyone"},
                                {"id": 2, "created_at": "2026-07-14T17:00:00Z", "user": {"login": "passerby"},
                                 "body": "/grade o/lib:KI-2 S4"}]
        nxt = summer + datetime.timedelta(days=7)
        with contextlib.redirect_stdout(io.StringIO()):
            step_fetch(conf, lib, w, now=nxt, clone=lambda r: str(dep))
            step_cascade(w); step_select(w); step_previous(w, api, "o/lib")
        subprocess.run([sys.executable, str(TOOLS / "known-issues.py"), "render-plan", "--input", str(w / "plan-input.json"),
                        "--out", str(w / "issue.json")], capture_output=True)
        with contextlib.redirect_stdout(io.StringIO()):
            step_open(w, api, "o/lib")
        new = issues.get(12, {})
        expect("Last week's round did not run" in new.get("body", "") and "- [ ] **S1** `KI-1`" in new.get("body", "")
               and "`/grade o/lib:KI-2 S2` by @someone" in new.get("body", "") and "passerby" not in new.get("body", ""),
               f"carry-forward: last week's tick and the owner's reply, not the drive-by one: {new.get('body', '')[-1500:]}")
        expect(issues[11]["state"] == "closed" and issues[11]["state_reason"] == "not_planned"
               and "Superseded by #12" in issues[11]["comments"][-1], "the old plan is closed as superseded")
        frozen = k.read_plan(new, [], lambda u: u == "someone", "t")
        f0 = frozen["forks"][0]
        expect("KI-1" not in f0["selected"] and f0["removed"] == ["KI-1"] and frozen["grades"][0]["severity"] == "S2"
               and frozen["grades"][0]["by"] == "someone" and f0["take_authors"], f"the carried plan reads back: {frozen}")
        # a failed step opens nothing: an incomplete rendering is refused
        (w / "issue.json").write_text(json.dumps({"title": "x", "body": "half"}), encoding="utf-8")
        inp = load_input(w); inp["already_open"] = None; save_input(w, inp)
        n_before = len(issues)
        try:
            step_open(w, api, "o/lib"); bad.append("an incomplete plan is refused")
        except Fail:
            pass
        expect(len(issues) == n_before, "no half-finished issue is opened")
    took = time.monotonic() - t0
    for b in bad:
        print("FAIL:", b)
    print(f"self-check: {'OK' if not bad else 'FAIL'} ({took:.1f} s)")
    return 0 if not bad else 1


# ---------------------------------------------------------------- the command line

def build_parser():
    ap = argparse.ArgumentParser(prog="weekly-plan.py", description=__doc__.split("\n")[0])
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--regen-golden", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("install", help="the plan workflow and its config on the head branch (keep-current setup)")
    p.add_argument("--repo", required=True, help="a checkout of the first fork, on its head branch")
    p.add_argument("--fork", action="append", default=[], required=True, metavar="SPEC",
                   help="owner/repo@HEAD=UPSTREAM_URL#BRANCH, the library first")
    p.add_argument("--plan-owner", help="who the plan is addressed to: the login of whoever runs setup, or @org/team")
    p.add_argument("--owner-type", choices=("user", "org"), help="the fork's owner kind (default: read from GitHub)")
    p.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help=f"S2s a round takes (default {DEFAULT_BUDGET})")
    p.add_argument("--migrator-repo", help="this repository on GitHub (default: its origin)")
    p.add_argument("--migrator-sha", help="the commit the workflow pins (default: origin/main)")
    p.add_argument("--default-branch-too", action="store_true",
                   help="also commit the workflow to the default branch when that is not the head branch")
    p.add_argument("--no-commit", action="store_true"); p.add_argument("--print", action="store_true")
    p = sub.add_parser("round-task", help="the Wednesday round's scheduled task, as JSON")
    p.add_argument("--repo"); p.add_argument("--config"); p.add_argument("--migrator-repo")
    p = sub.add_parser("fetch"); p.add_argument("--config", required=True); p.add_argument("--repo", required=True)
    p.add_argument("--work", required=True)
    for n in ("cascade", "select", "previous", "open"):
        sub.add_parser(n).add_argument("--work", required=True)
    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if a.self_check:
        return self_check()
    if a.regen_golden:
        GOLDEN.write_text(fixture(), encoding="utf-8")
        print(f"wrote {GOLDEN.relative_to(ROOT)}")
        return 0
    try:
        if a.cmd == "install":
            return install(a)
        if a.cmd == "round-task":
            if not (a.config or a.repo):
                raise Fail("--repo DIR (its .github/weekly-plan.json) or --config FILE")
            cfg = json.loads(pathlib.Path(a.config or pathlib.Path(a.repo) / CONFIG).read_text(encoding="utf-8"))
            print(json.dumps(round_task(cfg, a.migrator_repo or cfg.get("migrator_repo") or migrator_repo()), indent=1))
            return 0
        if a.cmd == "fetch":
            return step_fetch(a.config, a.repo, a.work)
        if a.cmd in ("cascade", "select", "previous", "open"):
            return {"cascade": step_cascade, "select": step_select, "previous": step_previous, "open": step_open}[a.cmd](a.work)
    except (Fail, OSError, ValueError, KeyError) as e:
        print(f"weekly-plan: {e}", file=sys.stderr)
        return 1 if a.cmd in ("fetch", "cascade", "select", "previous", "open") else 2
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
