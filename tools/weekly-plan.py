#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The weekly keep-current cycle of a fork set (issue #135, part 2a): a zero-model plan issue on Tuesday, the round on
Wednesday. Both are made only by the keep-current skill's setup, which runs `install` and creates the round task from
`round-task`; re-running setup regenerates both.

    python3 tools/weekly-plan.py install --repo DIR --fork SPEC [--fork SPEC ...] [--plan-owner LOGIN|@org/team]
                                         [--owner-type user|org] [--budget N] [--allow WHAT ...]
                                         [--ledger-issues REPO=N[,N]] [--depends-on REPO=REPO[,REPO]]
                                         [--layer-branch REPO=BRANCH] [--replace-task NAME ...] [--trailer "K: V" ...]
                                         [--migrator-repo OWNER/REPO] [--migrator-sha SHA] [--default-branch-too]
                                         [--keep-existing] [--no-commit] [--print]
    python3 tools/weekly-plan.py round-task (--repo DIR | --config FILE) [--existing NAME ...] [--migrator-repo O/R]
    python3 tools/weekly-plan.py fetch    --config FILE --repo DIR --work DIR      # the workflow's steps, in order
    python3 tools/weekly-plan.py cascade  --work DIR
    python3 tools/weekly-plan.py select   --work DIR
    python3 tools/weekly-plan.py deps     --work DIR                               # newer builds of the pinned deps
    python3 tools/weekly-plan.py previous --work DIR                               # GITHUB_TOKEN, GITHUB_REPOSITORY
    python3 tools/weekly-plan.py open     --work DIR                               # GITHUB_TOKEN, GITHUB_REPOSITORY
    python3 tools/weekly-plan.py --self-check | --regen-golden

SPEC is `owner/repo@HEAD_BRANCH[=UPSTREAM_URL#UPSTREAM_BRANCH]`, one per fork of the set, the LIBRARY FIRST: the plan
issue opens on the first fork's repository, with a section per fork (the others are public and read without a token).
Without `=UPSTREAM_URL#UPSTREAM_BRANCH` the authors' upstream is the GitHub fork parent and its default branch (read from
the GitHub API; refused for a repository that is not a fork). Only the first fork's repository gets the workflow and the
config; the dependents get nothing.

`.github/weekly-plan.json` on the first fork's head branch is the fork set's ONLY source for the round: per fork its
head, role (library / dependent), the authors' upstream URL and branch, `depends_on` (a dependent: the library unless
--depends-on says otherwise; the exact pin is the dependent's `--dep-jar` in its port-ci.yml), `ledger_issues`,
`automerge` (true exactly when `allow` has merge-fork-prs) and `layer_branch` (only when given); and for the set the
budget, the round's time (jittered per set from its name), `plan_owner`, `allow`, `replace_tasks` and `migrator_sha`
(the commit of this repository the workflow pins). An older fleet file is only an input to the first setup that writes
it.

ALLOW (--allow, repeatable; the default is none, which means the round asks the owner, on the plan issue, before each
of these): what the unattended round may do without asking. Each owner chooses for their fork set:
  push-version-branches  commit to the fork's head branch (its known-issues ledger and README Ports block included)
                         and its `neoforge-<v>` branches; never force-push
  pre-release            dispatch Port CI with `release=true`; never promote, never publish to Modrinth or CurseForge
  merge-fork-prs         push the round's own `keep-current/<date>` work branches on the fork repositories, open PRs
                         from them, and merge those PRs once CI is green; never the migrator repository
Always allowed, whatever the list says, because they are how the round reports: comments on the plan issue (and closing
it at the close), and edits to the ledger issues the config lists. Nothing on the migrator repository but reading it
is ever allowed. The round prompt carries all of this verbatim; anything else is a stop that asks the owner on the plan
issue.

INSTALL (on a checkout of the first fork's head branch) writes and commits there, in one `[skip ci]` commit:
  .github/workflows/weekly-plan.yml   templates/weekly-plan.yml with this repository and a PINNED commit of it (default:
                                      origin/main), so the planner runs the reviewed tools; golden tools/golden/weekly-plan.yml
  .github/weekly-plan.json            the fork set, the budget, the round's time and `plan_owner`
Who the plan is addressed to (assigned and @mentioned): `plan_owner` (setup passes whoever ran it), else the
repository's owner when that is a user. An organization-owned fork without --plan-owner is refused: a plan is never
addressed to nobody. GitHub runs a scheduled workflow only from the repository's DEFAULT branch: when that is not the
head branch, install refuses (exit 3) unless --default-branch-too, which also commits the same workflow file to the
default branch (a local commit; push both). Nothing is pushed. An unchanged file set makes no commit. --trailer adds
a `Key: value` line to the end of the commit message (an agent passes its session's attribution trailers).
Install builds the config from its flags only, so a re-run drops what it is not given again: when a config exists,
each per-fork `ledger_issues`, `depends_on` and `layer_branch`, and the set's `allow`, that it holds and the new flags
leave out is warned about on stderr, naming the flag to re-pass; --keep-existing keeps those values instead. Without
an existing config, or with every value re-passed, the output is as before.

ROUND-TASK prints the Wednesday round's scheduled task as JSON for the skill to create (create_trigger): its name, the
cron `CRON_TZ=America/Los_Angeles <minute> 8 * * 3` (minute jittered below 08:30 from the name), a fresh session per
fire, push and email notifications on, and a prompt that runs the keep-current round for the fork set, starting with
`known-issues.py read-plan`, with the fork set and the `allow` list in it, and `model`: the model to set on the task
(update_trigger), the most capable one priced in tools/model-prices.tsv (its top tier), read from that file. The prompt
checks every fork's clone first (tools/round-preflight.py: a scheduled session cannot attach a repository) and stops,
before freezing, when one is missing; a stopped round leaves the plan issue open. The round clones this repository's current
main (it gets the tools' fixes); only the plan workflow is pinned at `migrator_sha` (a reproducible plan); they meet at
the plan issue. `replaces` lists exact task names, never a
description: the round's own name, every `replace_tasks` name, and each --existing name (the names `list_triggers`
returned) that starts with "keep-current" and names a fork of this set (`owner/repo`, or its repository name as a word).
Setup deletes a listed task only when one with exactly that name exists.

DEPS (the workflow's step 4; every kind of fork alike, a deliberate change to single-version forks decided by the
maintainer): per fork and per Minecraft version it builds (the head's build.gradle; each version branch's block over its
build.gradle), each pinned CurseForge/Modrinth dependency with a newer compatible build (tools/layer-deps.py
dependency_updates). The plan lists them under "Dependency updates", ticked by default, unticked with the reason when
the newer build is an alpha/beta, a major-version jump, drops a Minecraft version the fork builds, or switches provider;
a lookup that fails is listed as "could not check" and never fails the plan. read-plan returns the ticked ones as each
fork's `dep_bumps`, and the round applies each once at setup with `layer-deps.py resolve --bump`; nothing else moves a
pin.

SHARED (inside the cascade step; a version-branch fork only, so a single-version fork's plan is unchanged): each
version against MMVU's shared library as of the pinned commit (fork-branches.py sync-shared --check, read-only). The
plan's "Adopt MMVU's shared code" section says which versions the round's sync step will bring up and which hand-edited
copies an agent reconciles, gives a box per hand-written copy of what the library ships -- NOT ticked by default -- and
lists the code marked MV-AHEAD-OF-TEMPLATE; read-plan returns the ticked ids as the fork's `adopt`. The round checks
again at its start against current MMVU (round.py init).

Exit codes: 0 ok; 1 a step failed (the workflow run fails, no issue is opened); 2 bad input; 3 the head branch is not
the default branch (install). Standard library only.
"""
import argparse, copy, datetime, json, os, pathlib, re, subprocess, sys, tempfile

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
SPEC_RE = re.compile(r"^([\w.-]+/[\w.-]+)@([\w./-]+)(?:=(\S+?)#([\w./-]+))?$")
REPLACE_PREFIX = "keep-current"
# what the unattended round may do without asking (install --allow); the round prompt carries these lines verbatim
ALLOW = {
    "push-version-branches": "commit to the fork's head branch (its known-issues ledger and README Ports block "
                             "included) and its `neoforge-<v>` branches; never force-push",
    "pre-release": "dispatch Port CI with `release=true`; never promote, never publish to Modrinth or CurseForge",
    "merge-fork-prs": "push the round's own `keep-current/<date>` work branches on the fork repositories, open PRs from "
                      "them, and merge those PRs once CI is green; never the migrator repository",
}
# allowed whatever `allow` says: how the round reports (round-task's prompt carries these verbatim)
ALWAYS = ("Always allowed, whatever the list says, because they are how the round reports: comments on the plan issue "
          "(and closing it at the close), and edits to the ledger issues listed above.")
MIGRATOR_RULE = "Nothing on the migrator repository, apart from reading it (the clone in step 1), is ever allowed."
# the closing comment carries the round's candidate lessons for the migrator, which the round itself never writes
LESSONS_RULE = ("Before closing, the closing comment carries a `### Lessons for the migrator` table (every fork kind, single-version included; text only): | version | symptom | fork commit | why it looks general |, one row per candidate lesson whose cause looks like the version, NeoForge, a library or a shared helper rather than the mod -- the error text or behaviour, the fix as owner/repo@<sha> -- or one `| none | | | |` row. Nothing is written to the migrator repository: a later session harvests it with tools/propose-learnings.py --from-plan.")
# what the round's cost is made of (2026-10-19 round, one session, ~$105: ~48% cache reads of a ~565k-token context
# re-read every turn, ~39% cache writes -- the whole context re-written after each idle gap longer than the 5-minute
# cache -- ~14% output): the orchestrator stays small, workers are fresh subagents, and waits are few and long
CONTEXT_RULE = ("Keep this session small: it orchestrates and never does a version's porting, gating or review in its own context. Each version's fixer and reviewer are fresh subagents that return a short summary (at most ~40 lines: what changed, gate verdicts, open rows, commit shas); keep only those, never their transcripts or the build logs. One worker may carry the same change across several versions (a re-pin, one fix cascaded up) while its context stays modest; distinct porting work at each version gets a fresh worker per version. Wait for gates and CI in ONE blocking command (`gh run watch <run id> --exit-status`, or a single until-loop run in the background or under Monitor), never by polling turn by turn: every idle gap longer than 5 minutes re-writes this session's whole cached context, so a small context and few, long waits are what keep the round cheap. If this session's context passes ~200k tokens, write the round's state (frozen input, per-version status, open rows, shas, what comes next) to round/NOTES.md and continue from a fresh subagent that reads it.")
# the round's cost by KIND of work, so the cost of taking the authors' code can be compared week over week
PHASE_RULE = ("Mark each change of the kind of work with python3 mmvu/tools/port-cost.py mark --fork <owner/repo> --version <v> --phase upstream|backlog|review|overhead (upstream: the authors' cascade and its conflicts; backlog: the selected known-issue fixes; review: fresh reviews and the fixes they cause; overhead: pins, gates, CI waits, records). The closing comment's cost table (round-table --session-cwd) gives the per-phase split and per-phase totals, and for upstream the size of what the authors changed per version (commits, files, changed lines) and how many files the cascade merged mechanically versus by an agent, so cost per authors' changed line and the mechanical-merge share can be compared week over week.")
# a set of version-branch forks only (round-task --version-branches) runs on the driver instead: the 2026-10-21 round
# (~$215) reached ~549k tokens despite the hand-off rule above, cache writes ~51% -- idle gaps and three container
# restarts re-wrote it; a single-version set keeps CONTEXT_RULE and PHASE_RULE, unchanged
DRIVER_RULE = ("Run the round with its driver and never do a step by hand. Right after step 3: python3 mmvu/tools/round.py init --frozen round/frozen.json --fork <owner/repo>=<its clone> for every fork, the library first. Then loop: run python3 mmvu/tools/round.py drive in the background (it runs every mechanical step itself -- merge-ups, re-pins, the one full gate run per version under a machine-wide lock, the library's pre-merge dependent gate, PRs, CI waits, merges, release dispatch, records, phase marks -- and blocks in its own waits); for each AGENT JOB it prints, start a fresh subagent whose whole prompt is the job's `prompt` line, and when it ends run the job's `record` line; never read a worker's transcript, its summary or a log here; then drive again. drive exit 3 means blocked: its BLOCKED lines say what; handle only that, then drive again. round/STATE.json is the round's only memory: after a restart, or once this session's context passes ~100k tokens, continue from a fresh session with python3 mmvu/tools/round.py resume. Keep this session small -- the job lines and the drive output only: every idle gap longer than 5 minutes re-writes its whole cached context.")
DRIVER_PHASE_RULE = ("The driver marks every item with python3 mmvu/tools/port-cost.py mark --fork <owner/repo> --version <v> --phase upstream|backlog|review|overhead as it starts (upstream: the authors' cascade and its conflicts; backlog: the selected known-issue fixes; review: fresh reviews and the fixes they cause; overhead: pins, gates, CI waits, records); never mark by hand. The closing comment's cost table (round-table --session-cwd) gives the per-phase split and per-phase totals, and for upstream the size of what the authors changed per version (commits, files, changed lines) and how many files the cascade merged mechanically versus by an agent, so cost per authors' changed line and the mechanical-merge share can be compared week over week.")
GOLDEN_SV_PROMPT = TOOLS / "golden/round-task-single-version.txt"
# the per-fork fields `install` rebuilds from flags only: an existing config's values are warned about or kept
KEPT = (("ledger_issues", "--ledger-issues"), ("depends_on", "--depends-on"), ("layer_branch", "--layer-branch"))
TRAILER_RE = re.compile(r"^[A-Za-z][\w-]*: \S.*$")


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
        raise Fail(f"--fork {spec!r}: owner/repo@HEAD_BRANCH[=UPSTREAM_URL#UPSTREAM_BRANCH]")
    return {"repo": m.group(1), "head": m.group(2), "upstream": m.group(3), "upstream_branch": m.group(4)}


def resolve_upstreams(forks, repo_info):
    """A fork named without its authors' upstream: the GitHub fork parent, at the parent's default branch."""
    for f in forks:
        if f["upstream"]:
            continue
        try:
            parent = (repo_info(f["repo"]) or {}).get("parent")
        except Exception as e:
            raise Fail(f"--fork {f['repo']}: cannot read its GitHub fork parent ({e}): name the authors' upstream "
                       "(owner/repo@HEAD=UPSTREAM_URL#UPSTREAM_BRANCH)")
        if not parent or not parent.get("default_branch") or not (parent.get("clone_url") or parent.get("full_name")):
            raise Fail(f"--fork {f['repo']}: not a GitHub fork, so the authors' upstream cannot be derived: name it "
                       "(owner/repo@HEAD=UPSTREAM_URL#UPSTREAM_BRANCH)")
        f["upstream"] = parent.get("clone_url") or f"https://github.com/{parent['full_name']}.git"
        f["upstream_branch"] = parent["default_branch"]
        f["upstream_source"] = "github fork parent"
    return forks


def per_fork(values, forks, flag):
    """['owner/repo=VALUE', ...] -> {repo: VALUE}; a repository outside the fork set is refused."""
    names = {f["repo"].lower(): f["repo"] for f in forks}
    out = {}
    for v in values or []:
        repo, sep, val = v.partition("=")
        if not sep or not val.strip() or repo.strip().lower() not in names:
            raise Fail(f"{flag} {v!r}: REPO=VALUE with REPO one of the forks ({', '.join(names.values())})")
        out[names[repo.strip().lower()]] = val.strip()
    return out


def make_config(forks, plan_owner, budget, migrator_repo, allow=(), ledger_issues=None, depends_on=None,
                layer_branch=None, replace_tasks=(), migrator_sha=None):
    name = round_name(forks)
    minute = round_minute(name)
    bad = [x for x in allow if x not in ALLOW]
    if bad:
        raise Fail(f"--allow {', '.join(bad)}: one of {', '.join(ALLOW)}")
    allow = [x for x in ALLOW if x in allow]
    ledger_issues, depends_on, layer_branch = ledger_issues or {}, depends_on or {}, layer_branch or {}
    repos = {f["repo"] for f in forks}
    for i, f in enumerate(forks):
        f["role"] = "library" if i == 0 and len(forks) > 1 else ("dependent" if len(forks) > 1 else "")
        nums = [n.strip().lstrip("#") for n in ledger_issues.get(f["repo"], "").split(",") if n.strip()]
        if any(not n.isdigit() for n in nums):
            raise Fail(f"--ledger-issues {f['repo']}={ledger_issues[f['repo']]}: issue numbers, comma separated")
        f["ledger_issues"] = sorted({int(n) for n in nums})
        f["automerge"] = "merge-fork-prs" in allow
        if f["repo"] in depends_on:
            deps = [d.strip() for d in depends_on[f["repo"]].split(",") if d.strip()]
            if any(d not in repos or d == f["repo"] for d in deps):
                raise Fail(f"--depends-on {f['repo']}={depends_on[f['repo']]}: other forks of the set")
            f["depends_on"] = deps
        elif f["role"] == "dependent":
            f["depends_on"] = [forks[0]["repo"]]
        if f["repo"] in layer_branch:
            f["layer_branch"] = layer_branch[f["repo"]]
    cfg = {"schema": 1, "forks": forks, "budget": budget, "migrator_repo": migrator_repo, "allow": allow,
           "round": {"name": name, "tz": TZ, "weekday": "Wednesday", "time": f"{ROUND_NOMINAL[0]:02d}:{minute:02d}",
                     "cron": f"CRON_TZ={TZ} {minute} {ROUND_NOMINAL[0]} * * 3"}}
    if plan_owner:
        cfg["plan_owner"] = plan_owner
    if migrator_sha:
        cfg["migrator_sha"] = migrator_sha
    extra = [t.strip() for t in replace_tasks if t.strip()]
    if extra:
        cfg["replace_tasks"] = list(dict.fromkeys(extra))
    return cfg


def carried_over(old, forks, flags, allow, build, keep=False):
    """What an existing config holds that this run's flags would drop: `install` rebuilds the config from its flags
    only. For each per-fork field in KEPT (and the set's `allow`) that the existing config has, this run gives no flag
    for, and the new config would hold differently: a warning naming the flag to re-pass; with `keep`, the existing
    value is put into `flags` (or returned as the allow list). `build(flags, allow)` makes the new config.
    -> (warnings, allow)"""
    warns = []
    if not isinstance(old, dict):
        return warns, allow
    new = {f["repo"].lower(): f for f in build(flags, allow)["forks"]}
    names = {f["repo"].lower(): f["repo"] for f in forks}
    for of in old.get("forks") or []:
        repo = names.get(str(of.get("repo", "")).lower())
        if not repo:
            continue
        for field, flag in KEPT:
            ov = of.get(field)
            if not ov or repo in flags[field] or ov == new[repo.lower()].get(field):
                continue
            val = ",".join(str(x) for x in ov) if isinstance(ov, list) else str(ov)
            warns.append(f"{repo}: the existing {CONFIG} has {field} {val}, which this run "
                         + ("keeps (--keep-existing)" if keep else f"drops: re-pass {flag} {repo}={val}, or rerun "
                                                                   "with --keep-existing"))
            if keep:
                flags[field][repo] = val
    oa = [x for x in old.get("allow") or [] if x in ALLOW]
    if oa and not allow:
        warns.append(f"the existing {CONFIG} has allow {', '.join(oa)}, which this run "
                     + ("keeps (--keep-existing)" if keep else "drops: re-pass " + " ".join(f"--allow {x}" for x in oa)
                        + ", or rerun with --keep-existing"))
        if keep:
            allow = oa
    return warns, allow


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


def install(a, say=print, repo_info=None):
    repo = pathlib.Path(a.repo).resolve()
    forks = [parse_fork(s) for s in a.fork]
    trailers = [t.strip() for t in a.trailer or []]
    bad = [t for t in trailers if not TRAILER_RE.match(t)]
    if bad:
        raise Fail(f"--trailer {bad[0]!r}: a `Key: value` line")
    if not forks:
        raise Fail("--fork: at least one (the library first)")
    if len({f["repo"].lower() for f in forks}) != len(forks):
        raise Fail("--fork: a fork is named twice")
    head = forks[0]["head"]
    cur = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if cur != head:
        raise Fail(f"{repo} is on {cur!r}, not the head branch {head}: check it out first")
    k = ki()
    info = repo_info or (lambda s: k.gh_api(f"/repos/{s}"))
    resolve_upstreams(forks, info)
    owner_login = forks[0]["repo"].split("/")[0]
    otype = a.owner_type
    if not a.plan_owner and not otype:
        try:
            otype = info(forks[0]["repo"])["owner"]["type"]
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
    flags = {"ledger_issues": per_fork(a.ledger_issues, forks, "--ledger-issues"),
             "depends_on": per_fork(a.depends_on, forks, "--depends-on"),
             "layer_branch": per_fork(a.layer_branch, forks, "--layer-branch")}

    def build(fl, allow):
        return make_config(copy.deepcopy(forks), a.plan_owner, a.budget, mrepo, allow=allow, replace_tasks=a.replace_task
                           or [], migrator_sha=sha, **fl)
    allow = a.allow or []
    if (repo / CONFIG).is_file():
        try:
            old = json.loads((repo / CONFIG).read_text(encoding="utf-8"))
        except ValueError as e:
            old = None
            print(f"weekly-plan: warning: the existing {CONFIG} is not JSON ({e}): nothing in it is carried over",
                  file=sys.stderr)
        warns, allow = carried_over(old, forks, flags, allow, build, keep=a.keep_existing)
        for w in warns:
            print(f"weekly-plan: warning: {w}", file=sys.stderr)
    cfg = build(flags, allow)
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
    if trailers:
        msg += "\n" + "\n".join(trailers) + "\n"
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
    for f in forks:
        if f.get("upstream_source"):
            say(f"weekly-plan: {f['repo']}: the authors' upstream is its GitHub fork parent: {f['upstream']} "
                f"{f['upstream_branch']}")
    say("weekly-plan: allow: " + (", ".join(cfg["allow"]) if cfg["allow"] else
        "none (the round asks the owner on the plan issue before each push, pre-release and merge)"))
    return 0


def migrator_repo():
    url = git(ROOT, "remote", "get-url", "origin").stdout.strip()
    m = re.search(r"github\.com[/:]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", url)
    if not m:
        raise Fail(f"cannot read this repository's GitHub name from {url!r}: pass --migrator-repo")
    return m.group(1)


def allow_lines(allow):
    """The round prompt's permissions block: the `allow` list verbatim, and the rule for everything else."""
    lines = ["Unattended permissions (the `allow` list in the config, chosen by the owner at setup; exactly these, "
             "nothing more):"]
    lines += [f"- {x}: {ALLOW[x]}." for x in ALLOW if x in allow] or [
        "- none: every push (the round's work branches included), PR, pre-release and merge needs the owner's answer "
        "first."]
    lines += [ALWAYS, MIGRATOR_RULE,
              "Any other action is a stop: do not take it; ask the owner in a comment on the plan issue "
              "(@mention them, the action and why), go on with what the list allows, and report it at the close."]
    return lines


def replaces_for(cfg, existing=()):
    """Exact task names the setup deletes before creating the round: its own name, the configured replace_tasks, and
    each existing name that starts with keep-current and names a fork of this set."""
    forks = [f["repo"] for f in cfg["forks"]]

    def ours(n):
        low = n.strip().lower()
        if not low.startswith(REPLACE_PREFIX):
            return False
        return any(r.lower() in low or re.search(r"(?<![\w./-])" + re.escape(r.split("/", 1)[1].lower()) + r"(?![\w/-])",
                                                 low) for r in forks)
    names = [cfg["round"]["name"], *cfg.get("replace_tasks", []), *(n.strip() for n in existing if ours(n))]
    return list(dict.fromkeys(n for n in names if n))


def round_task(cfg, mrepo, existing=(), driver=False):
    """The round's scheduled task. driver: every fork of the set is a version-branch fork, and the round runs on
    tools/round.py (rules 7 and 8, and who applies step 3's bumps); without it the prompt is the single-version one,
    byte for byte (tools/golden/round-task-single-version.txt)."""
    forks = cfg["forks"]
    first = forks[0]
    allow = cfg.get("allow", [])

    def fork_line(f):
        bits = [f"- {f['repo']} head {f['head']}" + (f" ({f['role']})" if f.get("role") else ""),
                f"authors' upstream {f['upstream']} {f['upstream_branch']}"]
        if f.get("depends_on"):
            bits.append("depends on " + ", ".join(f["depends_on"]))
        bits.append("ledger issues " + (", ".join(f"#{n}" for n in f["ledger_issues"]) if f.get("ledger_issues") else "none"))
        bits.append(f"automerge {'yes' if f.get('automerge') else 'no'}")
        if f.get("layer_branch"):
            bits.append(f"layer branch {f['layer_branch']}")
        return "; ".join(bits)
    lines = [f"Run the keep-current skill's weekly round for this fork set, unattended (fresh session, no human present).",
             "", f"The fork set's only source is {CONFIG} on {first['repo']} branch {first['head']} (no fleet file). "
             "As setup wrote it (dependency order, the library first; the plan issue is on the first):"]
    lines += [fork_line(f) for f in forks]
    lines += ["", *allow_lines(allow)]
    lines += ["", "Steps:",
              f"1. git clone --depth 1 https://github.com/{mrepo} mmvu (public, read only; never ask for it to be "
              "attached) and read mmvu/.claude/skills/keep-current/SKILL.md, section \"The weekly round\". Follow it "
              "exactly, in order. This is the migrator's current main, on purpose: the round runs the newest tools and "
              f"skill, fixes included. Tuesday's plan workflow ran the tools pinned at `migrator_sha` in {CONFIG}, so "
              "that a plan is reproducible from its commit; the two meet only at the plan issue, which step 3 reads. If "
              "read-plan cannot read it, stop and report (setup must be re-run to move the pin).",
              "2. First of all, before freezing anything: python3 mmvu/tools/round-preflight.py "
              + " ".join(f"--fork {f['repo']}=<its clone>" for f in forks) + ". A fork with no clone in this session: "
              "stop now -- this scheduled session cannot attach a repository (add_repo is refused here), so post one "
              f"comment on the newest open plan issue on {first['repo']} naming the repository and saying it must be "
              "added to this scheduled task's repositories, and stop. Install or set what else it names that the "
              f"session can (a JDK, libegl1, XDG_RUNTIME_DIR) and run it again. Read {CONFIG} from {first['repo']}'s "
              f"{first['head']}: if it differs from the fork set above, stop and report that setup must be re-run. "
              "GitHub is read through GITHUB_TOKEN when it is set (over REST; a broken gh login then does not matter), "
              "else GH_TOKEN, else gh.",
              f"3. Freeze the input: python3 mmvu/tools/known-issues.py read-plan --latest --slug "
              f"{first['repo']} --out round/frozen.json --decisions-dir round/decisions. The selected known issues and "
              "the authors' changes it says to take are the run's only inputs; nothing else enters except a new S1. "
              "With no open plan issue, stop and report. Each fork's `dep_bumps` there are the dependency updates the owner left "
              + ("ticked: the driver (rule 7) applies each exactly once, at that version's setup and before its gates, by its "
               "`command` (layer-deps.py resolve --bump, in the checkout of that version's branch), and commits it; "
               if driver else
               "ticked: apply each exactly once, at that version's setup and before its gates, by its `command` (layer-deps.py "
               "resolve --bump, in the checkout of that version's branch), and commit it; ")
              + "no other step moves a dependency (every resolve keeps the pins), and an unticked one stays as it is.",
              "4. A fork with version branches (.port/<v>/branch.properties) is a version-branch fork even when a legacy "
              "layer branch with .multiversion/ exists: that layer branch is history; ignore it.",
              "5. Never promote a release, publish to Modrinth or CurseForge, push to or comment on the authors' "
              "repositories, force-push, or merge into the migrator repository.",
              "6. A round that stops before its close leaves the plan issue open (next Tuesday's plan carries it "
              "forward); only the closing comment of a round that ran its steps closes it.",
              "7. " + (DRIVER_RULE if driver else CONTEXT_RULE),
              "8. " + (DRIVER_PHASE_RULE if driver else PHASE_RULE),
              "9. " + LESSONS_RULE]
    name = cfg["round"]["name"]
    return {"name": name, "cron_expression": cfg["round"]["cron"], "create_new_session_on_fire": True,
            "notifications": {"push": True, "email": True}, "initiation": "human_request", "prompt": "\n".join(lines),
            "replaces": replaces_for(cfg, existing),
            "replaces_rule": f"delete only a task whose name is exactly one of `replaces`; pass every name list_triggers "
                             f"returns with --existing so the ones that start with {REPLACE_PREFIX!r} and name a fork of "
                             "this set are listed",
            "allow": allow, "repositories": [f["repo"] for f in forks],
            "model": recommended_model(),
            "model_rule": "set it on the task right after create_trigger (update_trigger model): a task without one runs on "
                          "the account's default model; this is the most capable model priced in tools/model-prices.tsv "
                          "(its top price tier), which does the round's judgement -- the owner may choose another"}


def recommended_model(prices=None):
    """The model the round should run on: the highest-priced (top tier) row of tools/model-prices.tsv, the newest
    price date breaking a tie -- read from that file, never written here. None when it has no row."""
    path = pathlib.Path(prices) if prices else ROOT / "tools/model-prices.tsv"
    rows, head = [], None
    for line in path.read_text(encoding="utf-8").splitlines() if path.is_file() else []:
        if not line.strip() or line.startswith("#"):
            continue
        cells = line.split("\t")
        if head is None:
            head = cells
            continue
        r = dict(zip(head, cells))
        try:
            rows.append((float(r.get("input") or 0), r.get("as_of") or "", r["model"]))
        except (KeyError, ValueError):
            continue
    return max(rows)[2] if rows else None


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
    """Each version-branch fork: fork-branches.py cascade <head>..<newest> --plan (a single-version fork: none), and the
    authors' code the head already carries that a version branch has not taken (fork-branches.py untaken: in some
    version's port_taken..head and reachable from the authors' branch, or a merge of it) -- a head that merged the
    authors' release earlier with no cascade since has nothing new upstream, yet the round must not carry it unasked."""
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
        up = f"refs/remotes/upstream/{f['upstream_branch']}"
        f["untaken"] = [{k: c[k] for k in ("sha", "subject", "merge", "versions")}
                        for c in fb.untaken_authors(f["clone"], f["head_commit"], up, vs)]
        f["base_version"] = fb.info_ref(f["clone"], vs[0]).get("base_minecraft_version") or \
            (f["head"][len("neoforge-"):] if f["head"].startswith("neoforge-") else None)
        if f["untaken"]:
            print(f"weekly-plan: {f['repo']}: the head carries {len(f['untaken'])} commit(s) of the authors' that a version "
                  f"branch has not taken")
        f["shared"] = shared_summary(fb, f["clone"], vs)
        sh = f["shared"]
        print(f"weekly-plan: {f['repo']}: " + (f"the shared-library check failed: {sh['error']}" if sh.get("error") else
              f"{sum(1 for x in sh['versions'].values() if x['changes'])} version(s) behind MMVU's shared library, "
              f"{len(sh['adopt'])} adopt candidate(s), {len(sh['ahead'])} file(s) marked ahead of it"))
    save_input(work, inp)
    return 0


def shared_summary(fb, clone, versions):
    """A version-branch fork against MMVU's shared library (fork-branches.py sync-shared --check, read-only): per
    version the files the round's sync step would change and the hand-edited copies to reconcile; the fork's own copies
    of what the library ships (adopt candidates, each with its stable id); the code marked MV-AHEAD-OF-TEMPLATE. Fails
    soft: {"error": why}."""
    try:
        st = fb.shared_status(clone, versions)
    except Exception as e:          # noqa: BLE001 -- the plan goes on, its section says so
        return {"error": f"{type(e).__name__}: {e}"[:300]}
    bad = [f"{v}: {x['error']}" for v, x in st.items() if x.get("error")]
    if bad:
        return {"error": "; ".join(bad)[:300]}
    return {"versions": {v: {"changes": len(x["changes"]), "reconcile": x["reconcile"]} for v, x in st.items()},
            "adopt": [dict(a, version=v) for v, x in st.items() for a in x["adopt"]],
            "ahead": [dict(a, version=v) for v, x in st.items() for a in x["ahead"]]}


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
        # the lowest version still open (the status map), where the round makes the fix -- `lowest` is the first
        # affected version, which may be fixed already
        lo = {e["id"]: min((v for v, st in e["status"].items() if st == "open"), key=k_mc, default=None) for e in every}
        f["selection"] = [dict(s, title=title.get(s["id"], ""), lowest_open=lo.get(s["id"])) for s in sel]
        taken = {s["id"] for s in sel}
        f["candidates"] = [{"id": e["id"], "severity": e["severity"], "lowest": e["lowest"], "title": e["title"],
                            "lowest_open": lo.get(e["id"])} for e in opened if e["id"] not in taken]
        f["ledger_ids"] = [e["id"] for e in every]
        print(f"weekly-plan: {f['repo']}: {len(sel)} known issue(s) by default, {len(f['candidates'])} other open")
    save_input(work, inp)
    return 0


def k_mc(v):
    return tuple(int(x) for x in re.findall(r"\d+", v))


def ld_mod():
    import importlib.util
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("layer_deps_wp", TOOLS / "layer-deps.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def fork_pins(f, fb, ld):
    """{mc: {group:module: coordinate}}: the registry pins of every Minecraft version the fork builds -- the head's (its
    build.gradle, at the head commit) and each version branch's (its block over its build.gradle, at the branch tip)."""
    clone = f["clone"]
    show = lambda ref, p: git(clone, "show", f"{ref}:{p}").stdout if git(clone, "cat-file", "-e", f"{ref}:{p}").returncode == 0 else ""
    out = {}
    hc = f["head_commit"]
    hp = show(hc, "gradle.properties")
    mc = ld.read_props_text(hp).get("minecraft_version") or (f["head"][len("neoforge-"):] if f["head"].startswith("neoforge-") else "?")
    out[mc] = ld.version_pins(show(hc, "build.gradle"), hp, show(hc, f".port/{mc}/branch.properties"))
    for v in sorted(fb.version_branches(clone), key=fb.mc_key):
        ref = fb.resolve_branch(clone, fb.branch_name(v))
        if ref and v not in out:
            out[v] = ld.version_pins(show(ref, "build.gradle"), show(ref, "gradle.properties"),
                                     show(ref, f".port/{v}/branch.properties"))
    return out


def step_deps(work, lk=None):
    """Each fork: every pinned CurseForge/Modrinth dependency with a newer compatible build, per Minecraft version it
    builds (tools/layer-deps.py dependency_updates: no model; Modrinth's public API, CurseForge only with a key). A
    lookup that fails is listed as "could not check", and a fork whose pins cannot be read gets one such line: this
    step never fails the plan."""
    inp = load_input(work)
    fb, ld = fb_mod(), ld_mod()
    for f in inp["forks"]:
        try:
            f["dep_updates"] = ld.dependency_updates(fork_pins(f, fb, ld), lk)
        except Exception as e:          # noqa: BLE001 -- fail soft: the plan goes on, the section says so
            f["dep_updates"] = {"rows": [], "checked": 0,
                                "unchecked": [{"mc": "?", "ga": "*", "coord": "", "why": f"the pins could not be read ({e})"}]}
        du = f["dep_updates"]
        print(f"weekly-plan: {f['repo']}: {du['checked']} pinned dependenc{'y' if du['checked'] == 1 else 'ies'} checked, "
              f"{len(du['rows'])} with a newer build ({sum(1 for r in du['rows'] if r['default'])} ticked), "
              f"{len(du['unchecked'])} could not be checked")
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
           ["1.", "2.", "3.", "4.", "5.", "6."], f"the gate first, then the six steps in order: {steps}")
    expect(all(b.count("if: steps.when.outputs.go == 'true'") == 1 for b in wf.split("\n      - name: ")[2:]),
           "every step after the gate runs only when it lets the run through")
    try:
        import yaml
        y = yaml.safe_load(wf)
        expect(y[True]["schedule"] == [{"cron": "0 15 * * 2"}, {"cron": "0 16 * * 2"}] and "workflow_dispatch" in y[True]
               and y["permissions"] == {"contents": "read", "issues": "write"} and len(y["jobs"]["plan"]["steps"]) == 10,
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
    # the dry run (2026-10-18): the round checks every fork's clone first and stops, before freezing, when one is missing
    # (a scheduled session cannot attach one); a stopped round leaves the plan issue open; the task says which model to
    # set (read from tools/model-prices.tsv: a task created without one ran on the account's default)
    pr_ = task["prompt"]
    expect("round-preflight.py --fork o/lib=<its clone>" in pr_ and pr_.index("round-preflight.py") < pr_.index("read-plan --latest")
           and "add_repo is refused" in pr_ and "leaves the plan issue open" in pr_ and "GITHUB_TOKEN when it is set" in pr_
           and "which step 3 reads" in pr_ and pr_.index("3. Freeze the input") > pr_.index("which step 3 reads") - 2000,
           f"the round prompt checks the clones first, says how GitHub is read and that a stop leaves the plan open: {pr_}")
    with tempfile.TemporaryDirectory() as d_:
        tsv = pathlib.Path(d_) / "p.tsv"
        tsv.write_text("# x\nmodel\tinput\toutput\tas_of\nsmall-a\t0.10\t1\t2026-10-08\nbig-a\t4.00\t20\t2026-09-25\n"
                       "mid-a\t2.00\t10\t2026-09-25\n", encoding="utf-8")
        expect(recommended_model(tsv) == "big-a" and recommended_model(pathlib.Path(d_) / "none.tsv") is None,
               "recommended_model: the top-priced row, None without a file")
    expect(task.get("model") == recommended_model() and task.get("model") and "update_trigger" in task.get("model_rule", ""),
           f"round-task names the model to set, read from tools/model-prices.tsv: {task.get('model')}")
    expect("read-plan --latest --slug o/lib" in task["prompt"] and "The weekly round" in task["prompt"]
           and task["name"].startswith(NAME_PREFIX) and task["name"] in task["replaces"], f"the round prompt: {task}")
    expect(forks[0]["role"] == "library" and forks[1]["role"] == "dependent", "the first fork is the library")
    # the allow list: empty by default (ask), rendered verbatim and explicitly; automerge follows merge-fork-prs
    expect(cfg["allow"] == [] and not any(f["automerge"] for f in cfg["forks"])
           and "- none: every push (the round's work branches included), PR, pre-release and merge needs the owner's "
               "answer first." in task["prompt"]
           and "Any other action is a stop" in task["prompt"] and "plan issue" in task["prompt"],
           f"no --allow: nothing allowed, the round asks: {task['prompt']}")
    # the round's own bookkeeping: always allowed whatever the list says; the migrator repository only read
    expect(ALWAYS in task["prompt"] and MIGRATOR_RULE in task["prompt"]
           and task["prompt"].index(ALWAYS) < task["prompt"].index("Any other action is a stop")
           and "keep-current/<date>` work branches" in ALLOW["merge-fork-prs"] and "open PRs" in ALLOW["merge-fork-prs"]
           and "known-issues ledger and README Ports block" in ALLOW["push-version-branches"],
           f"plan-issue comments and ledger-issue edits always allowed, the migrator never written, work branches "
           f"under merge-fork-prs, ledger and Ports block under push-version-branches: {task['prompt']}")
    # the closing comment's lessons block (2026-10-19 round: its lessons reached the migrator only by hand): asked for in
    # the prompt, after the close rule, while the migrator stays read-only
    expect(LESSONS_RULE in task["prompt"] and task["prompt"].index(LESSONS_RULE) > task["prompt"].index("only the closing "
           "comment") and "### Lessons for the migrator" in LESSONS_RULE and "propose-learnings.py --from-plan" in LESSONS_RULE
           and "Nothing is written to the migrator" in LESSONS_RULE and MIGRATOR_RULE in task["prompt"]
           and "single-version included" in LESSONS_RULE,
           f"the round prompt asks for the Lessons for the migrator block and keeps the migrator read-only: {task['prompt']}")
    # the round's cost (2026-10-19: ~87% cache reads and writes of the orchestrator's ~565k-token context): the prompt keeps
    # the orchestrator small, its workers fresh with short summaries, and its waits blocking -- for every fork kind
    expect(CONTEXT_RULE in task["prompt"] and task["prompt"].index(CONTEXT_RULE) < task["prompt"].index(LESSONS_RULE)
           and "fresh subagents" in CONTEXT_RULE and "~40 lines" in CONTEXT_RULE and "ONE blocking command" in CONTEXT_RULE
           and "5 minutes" in CONTEXT_RULE and "round/NOTES.md" in CONTEXT_RULE and "~200k tokens" in CONTEXT_RULE
           and "the same change across several versions" in CONTEXT_RULE and "fresh worker per version" in CONTEXT_RULE
           and "\n7. " + CONTEXT_RULE in task["prompt"] and "\n9. " + LESSONS_RULE in task["prompt"],
           f"the round prompt keeps the orchestrator small, its workers fresh and its waits blocking: {task['prompt']}")
    # a single-version set's prompt is byte for byte the one before the driver (the golden); only a set of version-branch
    # forks (--version-branches) runs on tools/round.py
    expect(GOLDEN_SV_PROMPT.is_file() and task["prompt"] == GOLDEN_SV_PROMPT.read_text(encoding="utf-8"),
           "the single-version round prompt equals tools/golden/round-task-single-version.txt byte for byte")
    dtask = round_task(cfg, "owner/migrator", driver=True)
    dp = dtask["prompt"]
    expect("\n7. " + DRIVER_RULE in dp and "\n8. " + DRIVER_PHASE_RULE in dp and CONTEXT_RULE not in dp
           and PHASE_RULE not in dp and "never do a step by hand" in DRIVER_RULE and "round.py drive" in DRIVER_RULE
           and "round.py init --frozen round/frozen.json" in DRIVER_RULE and "round.py resume" in DRIVER_RULE
           and "`record` line" in DRIVER_RULE and "exit 3" in DRIVER_RULE and "pre-merge dependent gate" in DRIVER_RULE
           and "the driver (rule 7) applies each exactly once" in dp and "the driver (rule 7)" not in task["prompt"]
           and (ROOT / "tools/round.py").is_file() and "\n9. " + LESSONS_RULE in dp
           and {k: v for k, v in dtask.items() if k != "prompt"} == {k: v for k, v in task.items() if k != "prompt"},
           f"--version-branches: rules 7 and 8 run the driver, nothing else of the task changes: {dp}")
    # the cost by kind of work (upstream intake measured week over week): phase marks, the per-phase split, the authors'
    # change size and the mechanical-merge share in the closing table
    expect("\n8. " + PHASE_RULE in task["prompt"] and "--phase upstream|backlog|review|overhead" in PHASE_RULE
           and "per-phase" in PHASE_RULE and "changed lines" in PHASE_RULE and "mechanically versus by an agent" in PHASE_RULE
           and task["prompt"].index(PHASE_RULE) < task["prompt"].index(LESSONS_RULE),
           f"the round prompt marks phases and asks for the per-phase split and the authors' change size: {task['prompt']}")
    # the round runs the migrator's current main, the plan the pinned sha, and the prompt says so
    expect("current main, on purpose" in task["prompt"] and "`migrator_sha`" in task["prompt"]
           and "git clone --depth 1 https://github.com/owner/migrator mmvu" in task["prompt"]
           and "migrator_sha" not in cfg
           and make_config(copy.deepcopy(forks), "someone", 3, "owner/migrator", migrator_sha="b" * 40)["migrator_sha"]
           == "b" * 40, f"the round clones main and says the plan used migrator_sha: {task['prompt']}")
    full = make_config([parse_fork("o/lib@neoforge-1.21.1=https://github.com/a/lib#main"),
                        parse_fork("o/dep@neoforge-1.21.1=https://github.com/a/dep#dev")], "someone", 3, "owner/migrator",
                       allow=["merge-fork-prs", "push-version-branches", "pre-release"], ledger_issues={"o/dep": "#7,3"},
                       replace_tasks=["keep-current nightly"])
    ft = round_task(full, "owner/migrator", existing=["keep-current nightly: o/lib", "Keep-Current dep weekly",
                                                      "keep-current: other/thing", "nightly o/lib", "keep-current deps",
                                                      full["round"]["name"]])
    expect(full["allow"] == list(ALLOW) and all(f"- {k_}: {v_}." in ft["prompt"] for k_, v_ in ALLOW.items())
           and ft["allow"] == list(ALLOW) and all(f["automerge"] for f in full["forks"]),
           f"--allow: the list in canonical order, each line verbatim in the prompt, automerge on: {ft['prompt']}")
    expect(full["forks"][1]["ledger_issues"] == [3, 7] and full["forks"][1]["depends_on"] == ["o/lib"]
           and "depends_on" not in full["forks"][0] and "ledger issues #3, #7" in ft["prompt"]
           and "authors' upstream https://github.com/a/dep dev" in ft["prompt"] and CONFIG in ft["prompt"]
           and "legacy layer branch" in ft["prompt"], f"the fork set's fields in the config and the prompt: {full}")
    expect(ft["replaces"] == [full["round"]["name"], "keep-current nightly", "keep-current nightly: o/lib",
                              "Keep-Current dep weekly"] and not any("(" in n for n in ft["replaces"]),
           f"replaces: exact names only -- its own, --replace-task, and existing keep-current names of this set: "
           f"{ft['replaces']}")
    expect(round_task(cfg, "owner/migrator")["replaces"] == [cfg["round"]["name"]],
           "no existing task named: replaces is the round's own name alone")
    for bad_allow in (["push-everything"],):
        try:
            make_config(forks, None, 3, "o/m", allow=bad_allow); bad.append("an unknown --allow is refused")
        except Fail:
            pass
    # the authors' upstream from the GitHub fork parent when the spec omits it
    nf = resolve_upstreams([parse_fork("o/lib@neoforge-1.21.1")], lambda s: {"parent": {
        "clone_url": "https://github.com/a/lib.git", "default_branch": "dev", "full_name": "a/lib"}})
    expect(nf[0]["upstream"] == "https://github.com/a/lib.git" and nf[0]["upstream_branch"] == "dev"
           and nf[0]["upstream_source"] == "github fork parent", f"the upstream from the fork parent: {nf}")
    try:
        resolve_upstreams([parse_fork("o/lib@neoforge-1.21.1")], lambda s: {"fork": False})
        bad.append("a repository that is not a fork, named without its upstream, is refused")
    except Fail:
        pass
    try:
        per_fork(["x/y=1"], forks, "--ledger-issues"); bad.append("a per-fork flag naming a repository outside the set")
    except Fail:
        pass
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

        def inst(*extra, otype=None, info=None, argv=None):
            buf = io.StringIO()
            ri = info or ((lambda s: {"owner": {"type": otype}}) if otype else None)
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                try:
                    rc = install(ap.parse_args((argv or base) + list(extra)), repo_info=ri)
                except Fail as e:
                    rc = 2; print(e)
            return rc, buf.getvalue()
        rc, o = inst(otype="Organization")
        expect(rc == 2 and "REFUSED" in o and "plan_owner" in o and not (fork / WORKFLOW).exists(),
               f"an org-owned fork without --plan-owner is refused, nothing written: {rc} {o}")
        rc, o = inst("--plan-owner", "someone")
        wf_written = (fork / WORKFLOW).read_text(encoding="utf-8") if (fork / WORKFLOW).is_file() else ""
        expect(rc == 0 and "committed" in o and "warning" not in o and wf_written == fixture() and "[skip ci]" in gq(fork, "log", "-1", "--format=%s")
               and json.loads((fork / CONFIG).read_text(encoding="utf-8"))["plan_owner"] == "someone",
               f"install commits the golden workflow and plan_owner on the head branch, [skip ci], no warning with no "
               f"existing config: {rc} {o}")
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
        # trailers, the allow list and the derived upstream, through install
        gq(fork, "remote", "set-head", "origin", "neoforge-1.21.1")
        tr = ["Co-Authored-By: Someone <someone@example.invalid>", "Session: https://example.invalid/s/1"]
        argv2 = ["install", "--repo", str(fork), "--fork", "o/lib@neoforge-1.21.1", "--migrator-repo", "owner/migrator",
                 "--migrator-sha", "a" * 40]
        rc, o = inst("--plan-owner", "someone", "--allow", "pre-release", "--trailer", tr[0], "--trailer", tr[1],
                     argv=argv2, info=lambda s: {"parent": {"clone_url": "https://github.com/a/lib.git",
                                                            "default_branch": "main"}})
        c3 = json.loads((fork / CONFIG).read_text(encoding="utf-8"))
        parsed = gq(fork, "log", "-1", "--format=%(trailers:only,unfold)")
        expect(rc == 0 and c3["allow"] == ["pre-release"] and c3["forks"][0]["upstream"] == "https://github.com/a/lib.git"
               and c3["forks"][0]["automerge"] is False and parsed.splitlines() == tr
               and "fork parent" in o and "allow: pre-release" in o,
               f"install: --allow recorded, the upstream from the fork parent, --trailer lines end the commit: {rc} {o} "
               f"{parsed!r}")
        rc, o = inst("--plan-owner", "someone", "--trailer", "not a trailer")
        expect(rc == 2 and "--trailer" in o, f"a malformed --trailer is refused: {o}")
        expect(c3["migrator_sha"] == "a" * 40, f"install records the pinned migrator_sha in the config: {c3}")
        # a re-run drops what it is not given (the default, unchanged) but warns for each; --keep-existing keeps it.
        # A three-fork set: a layer fork (layer_branch) and a dependent with non-default depends_on and ledger issues
        argv3 = ["install", "--repo", str(fork), "--fork", "o/lib@neoforge-1.21.1=https://github.com/a/lib#main",
                 "--fork", "o/mid@neoforge-1.21.1=https://github.com/a/mid#main",
                 "--fork", "o/dep@neoforge-1.21.1=https://github.com/a/dep#main", "--migrator-repo", "owner/migrator",
                 "--migrator-sha", "a" * 40, "--plan-owner", "someone"]
        full_flags = ["--allow", "push-version-branches", "--allow", "merge-fork-prs", "--ledger-issues", "o/dep=7,3",
                      "--depends-on", "o/dep=o/lib,o/mid", "--layer-branch", "o/mid=layer"]
        rc, o = inst(*full_flags, argv=argv3)
        c4 = json.loads((fork / CONFIG).read_text(encoding="utf-8"))
        expect(rc == 0 and "warning" not in o and c4["allow"] == ["push-version-branches", "merge-fork-prs"],
               f"--allow given: the existing allow (pre-release) is replaced, not warned about as a drop: {o}")
        rc, o = inst(*full_flags, argv=argv3)
        expect(rc == 0 and "warning" not in o and "unchanged" in o,
               f"every value re-passed: no warning, nothing changes: {o}")
        rc, o = inst(argv=argv3)
        c5 = json.loads((fork / CONFIG).read_text(encoding="utf-8"))
        w = [l for l in o.splitlines() if "warning" in l]
        expect(rc == 0 and len(w) == 4 and "re-pass --ledger-issues o/dep=3,7" in o
               and "re-pass --depends-on o/dep=o/lib,o/mid" in o and "re-pass --layer-branch o/mid=layer" in o
               and "re-pass --allow push-version-branches --allow merge-fork-prs" in o and "--keep-existing" in o
               and c5["allow"] == [] and c5["forks"][2]["ledger_issues"] == [] and c5["forks"][2]["depends_on"] == ["o/lib"]
               and "layer_branch" not in c5["forks"][1],
               f"a bare re-run: the old values dropped as before, one warning each naming the flag: {o}")
        rc, o = inst(*full_flags, argv=argv3)
        rc, o = inst("--keep-existing", "--ledger-issues", "o/dep=9", argv=argv3)
        c6 = json.loads((fork / CONFIG).read_text(encoding="utf-8"))
        expect(rc == 0 and c6["allow"] == c4["allow"] and c6["forks"][2]["ledger_issues"] == [9]
               and c6["forks"][2]["depends_on"] == ["o/lib", "o/mid"] and c6["forks"][1]["layer_branch"] == "layer"
               and "drops" not in o and o.count("keeps (--keep-existing)") == 3,
               f"--keep-existing: the old values the flags leave out are kept, a flag given still wins: {o}")

        # the steps, offline: a library fork with a version branch is too big a fixture here (fork-branches'
        # own self-check covers cascade --plan); a single-version fork set of two, upstream with new commits
        up = d / "up"
        subprocess.run(["git", "init", "-q", "-b", "main", str(up)], check=True)
        (up / "README.md").write_text("x\n", encoding="utf-8")
        gq(up, "add", "-A"); gq(up, "commit", "-qm", "root")
        lib = d / "libfork"
        subprocess.run(["git", "clone", "-q", str(up), str(lib)], check=True, capture_output=True)
        gq(lib, "checkout", "-q", "-b", "neoforge-1.21.1")
        (lib / "gradle.properties").write_text("minecraft_version=1.21.1\nviewer_version=a1\n", encoding="utf-8")
        (lib / "build.gradle").write_text('dependencies {\n    runtimeOnly "maven.modrinth:viewer:${viewer_version}"\n'
                                          '    compileOnly "maven.modrinth:down:z1"\n}\n', encoding="utf-8")
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
        vfiles = [{"id": "a1", "name": "1.1", "mcs": ["1.21.1"], "loaders": ["neoforge"], "channel": "release", "date": "2026-01"},
                  {"id": "a2", "name": "1.2", "mcs": ["1.21.1"], "loaders": ["neoforge"], "channel": "release", "date": "2026-02"},
                  {"id": "a3", "name": "1.2", "mcs": ["1.21.3"], "loaders": ["neoforge"], "channel": "release", "date": "2026-01"},
                  {"id": "a4", "name": "1.3-beta", "mcs": ["1.21.3"], "loaders": ["neoforge"], "channel": "beta", "date": "2026-03"}]

        def mr_files(p):
            if p == "down":
                raise OSError("HTTP Error 503: the lookup failed")
            return vfiles
        dlk = {"mr_files": mr_files, "cf": lambda: None}
        with contextlib.redirect_stdout(io.StringIO()):
            step_fetch(conf, lib, w, now=summer, clone=lambda r: str(dep))
            step_cascade(w)
            step_select(w)
            step_deps(w, dlk)
        inp = load_input(w)
        du0, du1 = inp["forks"][0]["dep_updates"], inp["forks"][1]["dep_updates"]
        expect([(r["mc"], r["ga"], r["from"], r["to"], r["default"]) for r in du0["rows"]]
               == [("1.21.1", "maven.modrinth:viewer", "maven.modrinth:viewer:a1", "maven.modrinth:viewer:a2", True)]
               and [u["ga"] for u in du0["unchecked"]] == ["maven.modrinth:down"] and du1 == {"rows": [], "unchecked": [], "checked": 0},
               f"deps, a single-version fork: the gradle.properties-keyed pin's newer build ticked, a failed lookup listed, "
               f"the plan going on: {du0} {du1}")
        f0 = inp["forks"][0]
        expect(f0["new_count"] == 1 and f0["new_commits"][0]["subject"] == "authors: a new feature" and f0["authors_merged"]
               and inp["deadline"] == "2026-07-15T" + c2["round"]["time"] + "-07:00",
               f"fetch: the authors' new commits, merged in the throwaway checkout; the deadline: {f0} {inp['deadline']}")
        expect(f0["cascade"] is None and [s["id"] for s in f0["selection"]] == ["KI-1"]
               and [c["id"] for c in f0["candidates"]] == ["KI-2"] and f0["selection"][0]["title"] == "crash on load",
               f"a single-version fork: no cascade; select's S1 by default, the S3 a candidate: {f0}")
        expect("untaken" not in f0 and "base_version" not in f0 and "shared" not in f0 and f0["selection"][0].get("lowest_open") == "1.21.1",
               f"a single-version fork: no untaken list (its plan renders as before): {f0}")
        # the smallest version-branch fork (one version, hand-made): the head merged the authors' release earlier and
        # no version took it -- nothing new upstream, yet the plan lists it (the first weekly-round dry run's bug)
        vb = d / "vbfork"
        subprocess.run(["git", "clone", "-q", str(up), str(vb)], check=True, capture_output=True)
        gq(vb, "checkout", "-q", "-b", "neoforge-1.21.1", "HEAD~1")
        (vb / "gradle.properties").write_text("minecraft_version=1.21.1\n", encoding="utf-8")
        (vb / "build.gradle").write_text('dependencies {\n    runtimeOnly "maven.modrinth:viewer:a1"\n}\n', encoding="utf-8")
        gq(vb, "add", "-A"); gq(vb, "commit", "-qm", "port")
        x = gq(vb, "rev-parse", "HEAD")
        gq(vb, "checkout", "-q", "-b", "neoforge-1.21.3")
        (vb / "gradle.properties").write_text("minecraft_version=1.21.3\n", encoding="utf-8")
        (vb / ".port/1.21.3").mkdir(parents=True)
        (vb / ".port/1.21.3/branch.properties").write_text(
            "minecraft_version=1.21.3\nbranch=neoforge-1.21.3\nparent=neoforge-1.21.1\nparent_minecraft_version=1.21.1\n"
            f"port_branch=neoforge-1.21.1\nbase_minecraft_version=1.21.1\nport_taken={x}\n"
            "# BEGIN layer-deps (tools/layer-deps.py)\nsubstitute.maven.modrinth\\:viewer=maven.modrinth:viewer:a3\n"
            "# END layer-deps\n", encoding="utf-8")
        gq(vb, "add", "-A"); gq(vb, "commit", "-qm", "1.21.3")
        gq(vb, "checkout", "-q", "neoforge-1.21.1"); gq(vb, "merge", "-q", "--no-ff", "--no-edit", "origin/main")
        for b_ in ("neoforge-1.21.1", "neoforge-1.21.3"):
            gq(vb, "update-ref", f"refs/remotes/origin/{b_}", b_)
        c3 = make_config([parse_fork(f"o/vb@neoforge-1.21.1={up}#main")], "someone", 3, "owner/migrator")
        conf3 = d / "weekly-plan-vb.json"
        conf3.write_text(json.dumps(c3), encoding="utf-8")
        w3 = d / "plan-vb"
        with contextlib.redirect_stdout(io.StringIO()):
            step_fetch(conf3, vb, w3, now=summer)
            step_cascade(w3)
            step_select(w3)
            step_deps(w3, dlk)
        fv = load_input(w3)["forks"][0]
        expect([(r["mc"], r["to"], r["default"], "a beta" in r["why"]) for r in fv["dep_updates"]["rows"]]
               == [("1.21.1", "maven.modrinth:viewer:a2", True, False), ("1.21.3", "maven.modrinth:viewer:a4", False, True)],
               f"deps, a version-branch fork: per version, the branch's own pin (its block) checked, a beta unticked: "
               f"{fv['dep_updates']}")
        with contextlib.redirect_stdout(io.StringIO()):
            step_deps(w3, {"mr_files": lambda p: (_ for _ in ()).throw(OSError("offline")), "cf": lambda: None})
        fo = load_input(w3)["forks"][0]["dep_updates"]
        expect(not fo["rows"] and len(fo["unchecked"]) == 2 and all("offline" in u["why"] for u in fo["unchecked"]),
               f"deps with every lookup failing: each pin could not be checked, no failure: {fo}")
        expect(fv["new_count"] == 0 and fv["cascade"]["range"] == "neoforge-1.21.1..1.21.3" and fv["base_version"] == "1.21.1"
               and [(c["subject"].split(" ")[0], c["merge"], c["versions"]) for c in fv.get("untaken", [])]
               == [("Merge", True, ["1.21.3"]), ("authors:", False, ["1.21.3"])],
               f"a version-branch fork: nothing new upstream, the head's untaken authors' commit and its merge listed: {fv}")
        expect(set(fv.get("shared", {}).get("versions", {})) == {"1.21.3"} and "adopt" in fv["shared"] and "ahead" in fv["shared"],
               f"a version-branch fork: its shared-library status (sync-shared --check) is in the plan input: {fv.get('shared')}")
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
               and "## o/dep (dependent)" in first["body"] and not first["title"].startswith("[decision needed]")
               and "- [x] `maven.modrinth:viewer` at 1.21.1" in first["body"] and "Could not check" in first["body"],
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
    p.add_argument("--allow", action="append", choices=tuple(ALLOW), metavar="WHAT",
                   help="what the unattended round may do without asking (repeatable): " + ", ".join(ALLOW)
                        + "; default none: it asks the owner")
    p.add_argument("--ledger-issues", action="append", metavar="REPO=N[,N]", help="the fork issues with fix ledgers")
    p.add_argument("--depends-on", action="append", metavar="REPO=REPO[,REPO]",
                   help="a dependent's dependencies in the set (default: the library)")
    p.add_argument("--layer-branch", action="append", metavar="REPO=BRANCH", help="a layer fork's .multiversion/ branch")
    p.add_argument("--replace-task", action="append", metavar="NAME", help="an old scheduled task's exact name to replace")
    p.add_argument("--trailer", action="append", metavar='"KEY: VALUE"', help="a line added to the end of the commit message")
    p.add_argument("--migrator-repo", help="this repository on GitHub (default: its origin)")
    p.add_argument("--migrator-sha", help="the commit the workflow pins (default: origin/main)")
    p.add_argument("--default-branch-too", action="store_true",
                   help="also commit the workflow to the default branch when that is not the head branch")
    p.add_argument("--keep-existing", action="store_true",
                   help="keep the existing config's allow, ledger_issues, depends_on and layer_branch this run's flags "
                        "do not set (without it, each one dropped is warned about on stderr)")
    p.add_argument("--no-commit", action="store_true"); p.add_argument("--print", action="store_true")
    p = sub.add_parser("round-task", help="the Wednesday round's scheduled task, as JSON")
    p.add_argument("--repo"); p.add_argument("--config"); p.add_argument("--migrator-repo")
    p.add_argument("--existing", action="append", default=[], metavar="NAME",
                   help="an existing scheduled task's name (list_triggers), repeatable: matched into replaces")
    p.add_argument("--version-branches", action="store_true",
                   help="every fork of the set is a version-branch fork (layer-sync.py detect: kind=version-branches): the "
                        "round runs on tools/round.py; without it the single-version round prompt, unchanged")
    p = sub.add_parser("fetch"); p.add_argument("--config", required=True); p.add_argument("--repo", required=True)
    p.add_argument("--work", required=True)
    for n in ("cascade", "select", "deps", "previous", "open"):
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
            print(json.dumps(round_task(cfg, a.migrator_repo or cfg.get("migrator_repo") or migrator_repo(), a.existing,
                                        driver=a.version_branches), indent=1))
            return 0
        if a.cmd == "fetch":
            return step_fetch(a.config, a.repo, a.work)
        if a.cmd in ("cascade", "select", "deps", "previous", "open"):
            return {"cascade": step_cascade, "select": step_select, "deps": step_deps, "previous": step_previous,
                    "open": step_open}[a.cmd](a.work)
    except (Fail, OSError, ValueError, KeyError) as e:
        print(f"weekly-plan: {e}", file=sys.stderr)
        return 1 if a.cmd in ("fetch", "cascade", "select", "deps", "previous", "open") else 2
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
