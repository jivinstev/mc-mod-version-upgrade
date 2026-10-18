#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The known-issues ledger of a fork port: every known defect, graded S1-S4 by a fixed rubric, with its status at
each Minecraft version (issue #135, part 1).

    python3 tools/known-issues.py add     [LEDGER] --title T --failure F --visibility V --reach R --workaround yes|no
                                          --lowest MC [--status MC=STATE ...] [--id ID] [--source S] [--notes N]
                                          [--file PATH ...] [--platform mr|cf ...]
    python3 tools/known-issues.py grade   [LEDGER] ID [--failure F] [--visibility V] [--reach R] [--workaround yes|no]
                                          [--override S --reason WHY [--by WHO] | --clear-override]
    python3 tools/known-issues.py status  [LEDGER] ID --set MC=STATE [--set ...] [--lowest MC]
    python3 tools/known-issues.py list    [LEDGER] [--version MC] [--severity S1,S2] [--min S] [--open] [--platform P]
                                          [--json]
    python3 tools/known-issues.py select  [LEDGER] [--budget N] [--touched PATH ...] [--version MC ...]
                                          [--add ID ...] [--remove ID ...] [--json]
    python3 tools/known-issues.py render-issue  [LEDGER]
    python3 tools/known-issues.py release-notes [LEDGER] --version MC --tag TAG [--min S3] [--platform P] [--repo DIR]
    python3 tools/known-issues.py blocks-publish [LEDGER] --version MC --platform mr|cf
    python3 tools/known-issues.py reopens [LEDGER] --version MC (--severity S | --id ID)
    python3 tools/known-issues.py render-plan --input PLAN-INPUT.json --out ISSUE.json
    python3 tools/known-issues.py read-plan (--issue N | --latest) [--slug OWNER/REPO] [--owner LOGIN|@org/team]
                                          [--from-json FILE] [--out FROZEN.json] [--decisions-dir DIR]
    python3 tools/known-issues.py render-ports [LEDGER] [--head BRANCH] [--slug OWNER/REPO] [--releases FILE |
                                          --fetch-releases] [--pinned-issue N] [--readme FILE --write] [--no-done]
    python3 tools/known-issues.py --self-check

LEDGER (where it is read from; the same options on every subcommand):
    --ledger FILE              a file (the default: <--repo or .>/.port/KNOWN-ISSUES.json)
    --repo DIR --ref REF       the ledger committed at REF (`git show REF:.port/KNOWN-ISSUES.json`): the head branch,
                               e.g. --ref neoforge-1.21.1. Read-only: add, grade and status write a file.
    --mod M                    a multi-mod repository's ledger for mod M: .port/M/KNOWN-ISSUES.json
A ledger that does not exist yet reads as empty; `add` creates it.

WHERE IT LIVES. One file per fork, `.port/KNOWN-ISSUES.json`, on the fork's HEAD branch (the root port branch, e.g.
neoforge-1.21.1; a single-version fork's only branch); one per mod, `.port/<mod>/KNOWN-ISSUES.json`, in a multi-mod
repository. It is a record, not port code: Port CI's push trigger ignores it, it never makes gates or a review stale
(tools/ci-gates.py GATE_OUTPUTS, tools/fork-port.py REVIEW_OUTPUTS, tools/port-publish.py GATES_DOC) and
`fork-branches.py cascade` never carries it to a version branch. Tools write it; nobody edits it by hand. Each write
is atomic, with sorted keys, a 2-space indent and a trailing newline, so a change is a small diff.

THE SCHEMA (schema 1):
  {"schema": 1,
   "issues": [{"id": "KI-1", "title": "...",
               "rubric": {"failure": "crash|kick|data|hang|security|build|functional|cosmetic",
                          "visibility": "always|common|rare|invisible",
                          "reach": "normal|feature|unusual", "workaround": false},
               "severity": "S1".."S4",          # the rubric's grade, or the override's when there is one
               "override": null | {"severity": "S2", "reason": "...", "by": "owner"},
               "lowest": "1.21.1",              # the lowest affected Minecraft version (a key of status)
               "status": {"1.21.1": "open", "1.21.3": "fixed@<commit>", "1.21.4": "wontfix:<reason>", "1.21.5": "n/a"},
               "source": "authors|review|gate|player",
               "files": ["src/main/java/..."],  # optional: the files the defect is in (select's S3 rule)
               "platforms": ["cf"],             # optional: only that platform's jar (mr Modrinth, cf CurseForge);
                                                # absent: every platform
               "notes": "..."}]}
  A version missing from status is not affected. Which releases contain an issue is never stored: release-notes works
  it out from the tag (with --repo, a fixed@<commit> that is not in the tag counts as open there).

THE RUBRIC (grade derives the severity from the recorded inputs; an owner override wins and is recorded):
  S1  failure crash, kick (or network disconnect), data (world or player data lost or corrupted), hang, security
      (an exposed credential or secret, a token leak, an exploit or privilege path) or build (the version does not
      compile or produce a jar) -- whatever else. It blocks publishing (publish-port refuses an open S1; for an issue
      scoped to one platform, that platform's upload only: `blocks-publish`).
  S4  otherwise: visibility invisible, or reach unusual (an unusual setup, other mods, debug options).
  S2  otherwise: a functional failure with reach normal, visibility always or common and no workaround.
  S3  everything else: cosmetic, rare, a specific feature only, or a workaround exists. Cosmetic is S3 at most.
  The failure types beside the six S1 ones:
    functional  a feature does not work, or information or controls the player needs are missing, unreadable or
                unusable -- invisible GUI text and a missing UI element are functional;
    cosmetic    the look is wrong, but nothing the player needs is lost (a wrong colour tint, a texture seam).

ONLY S1 GOES BACK. A finding at a version that is already done reopens it only when it is an S1 (`reopens` answers
yes, exit 0); any other finding goes into the ledger at its lowest affected version (exit 1).

SELECT (the default policy before a run): every issue still open at a version of the run graded S1; then S2s, lowest
affected version first, up to --budget (default 3); then any S3 in a file the run already touches (--touched). The
owner's --add and --remove apply last, and the output says who chose each item.

THE WEEKLY PLAN ISSUE (issue #135, part 2a; tools/weekly-plan.py runs it as a GitHub Actions workflow, no model):
  render-plan  turns the plan's input (each fork's authors' new commits, its cascade plan, its default selection and
               the other open issues; tools/weekly-plan.py writes it) into ONE issue: the deadline line first (the
               round's start; with no answer the defaults apply), then per fork the authors' commits (a checkbox to
               take them), the known issues as checkboxes pre-ticked for the defaults, the escalated decisions with
               their options (`/decide <id> <choice>`), and grade overrides (`/grade <ID> <S1-S4> [reason]`). The
               title starts `[decision needed]` when the plan escalates something. A previous plan issue no round
               closed is carried in: its ticks and replies, with "last week's round did not run".
  read-plan    the round's first step: the issue's checkboxes and the `/decide` and `/grade` replies, frozen as JSON.
               Only replies from the resolved owner (a user, or a member of the owner team) or from users with write
               access count; every other comment is listed under `ignored`. A checkbox is part of the issue body, which
               only the bot and users with write access can edit. Each fork's `cascade` block is what
               `fork-branches.py cascade --apply-decisions` reads (--decisions-dir writes one file per fork).
  render-ports the fork README's Ports block, between `<!-- ports:begin -->` and `<!-- ports:end -->`: one row per
               Minecraft version (branch, newest pre-release or release, done state) and the known-issues links. With
               --write it replaces only the marked block (appended at the end when the README has none); every byte
               outside the markers stays as it was.
  The GitHub API is read with GITHUB_TOKEN / GH_TOKEN when set, else with the `gh` command line.

Exit codes: 0 ok (reopens: it reopens; blocks-publish: nothing blocks); 1 reopens: it does not reopen;
blocks-publish: an open S1 applies to that platform; 2 bad input or an invalid ledger.
"""
import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

SCHEMA = 1
LEDGER_NAME = "KNOWN-ISSUES.json"
SEVERITIES = ("S1", "S2", "S3", "S4")
FAILURES = ("crash", "kick", "data", "hang", "security", "build", "functional", "cosmetic")
BLOCKING = ("crash", "kick", "data", "hang", "security", "build")
PLATFORMS = ("mr", "cf")                     # the Modrinth and CurseForge jars (port-ci.py --variant-jar)
VISIBILITY = ("always", "common", "rare", "invisible")
REACH = ("normal", "feature", "unusual")
SOURCES = ("authors", "review", "gate", "player")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MOD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MC_RE = re.compile(r"^\d+(?:\.\d+){1,3}$")
STATE_RE = re.compile(r"^(?:open|n/a|fixed@[0-9a-f]{7,40}|wontfix:\S.*)$")
DEFAULT_BUDGET = 3
TOP_KEYS = {"schema", "issues"}
ISSUE_KEYS = {"id", "title", "rubric", "severity", "override", "lowest", "status", "source", "files", "platforms", "notes"}


class Invalid(Exception):
    pass


def mc_key(v):
    return tuple(int(x) for x in v.split("."))


def sev_rank(s):
    return SEVERITIES.index(s)


def derive(rubric):
    """The rubric's severity for its recorded inputs."""
    if rubric["failure"] in BLOCKING:
        return "S1"
    if rubric["visibility"] == "invisible" or rubric["reach"] == "unusual":
        return "S4"
    if (rubric["failure"] == "functional" and rubric["reach"] == "normal"
            and rubric["visibility"] in ("always", "common") and not rubric["workaround"]):
        return "S2"
    return "S3"


def effective(issue):
    o = issue.get("override")
    return o["severity"] if o else derive(issue["rubric"])


# ---------------------------------------------------------------- validation

def validate(data):
    """Raise Invalid naming the entry and field of the first problem."""
    if not isinstance(data, dict):
        raise Invalid("the ledger is not a JSON object")
    if data.get("schema") != SCHEMA:
        raise Invalid(f"schema {data.get('schema')!r}: this tool reads schema {SCHEMA}")
    extra = set(data) - TOP_KEYS
    if extra:
        raise Invalid(f"unknown top-level key(s): {', '.join(sorted(extra))}")
    issues = data.get("issues")
    if not isinstance(issues, list):
        raise Invalid("`issues` is not a list")
    seen = set()
    for n, e in enumerate(issues):
        where = f"issue #{n + 1}"
        if not isinstance(e, dict):
            raise Invalid(f"{where} is not an object")
        i = e.get("id")
        if not isinstance(i, str) or not ID_RE.match(i):
            raise Invalid(f"{where}: id {i!r} is not a short name ([A-Za-z0-9._-])")
        where = f"issue {i}"
        if i in seen:
            raise Invalid(f"{where}: duplicate id")
        seen.add(i)
        extra = set(e) - ISSUE_KEYS
        if extra:
            raise Invalid(f"{where}: unknown key(s) {', '.join(sorted(extra))}")
        for k in ("title", "rubric", "severity", "lowest", "status", "source"):
            if k not in e:
                raise Invalid(f"{where}: no {k}")
        if not isinstance(e["title"], str) or not e["title"].strip():
            raise Invalid(f"{where}: empty title")
        r = e["rubric"]
        if not isinstance(r, dict) or set(r) != {"failure", "visibility", "reach", "workaround"}:
            raise Invalid(f"{where}: rubric must hold exactly failure, visibility, reach and workaround")
        for k, allowed in (("failure", FAILURES), ("visibility", VISIBILITY), ("reach", REACH)):
            if r[k] not in allowed:
                raise Invalid(f"{where}: rubric {k} {r[k]!r} is not one of {', '.join(allowed)}"
                              + (" (`visual` is now `cosmetic`, or `functional` when the player loses something "
                                 "they need)" if r[k] == "visual" else ""))
        if not isinstance(r["workaround"], bool):
            raise Invalid(f"{where}: rubric workaround must be true or false")
        o = e.get("override")
        if o is not None:
            if not isinstance(o, dict) or o.get("severity") not in SEVERITIES or not str(o.get("reason", "")).strip() \
                    or set(o) - {"severity", "reason", "by"}:
                raise Invalid(f"{where}: override must be null or {{severity S1-S4, reason, by}}")
        if e["severity"] != effective(e):
            raise Invalid(f"{where}: severity {e['severity']!r} is not what its rubric and override give "
                          f"({effective(e)}): re-grade it with `known-issues.py grade {i}`")
        st = e["status"]
        if not isinstance(st, dict) or not st:
            raise Invalid(f"{where}: status must map at least one Minecraft version to a state")
        for v, s in st.items():
            if not MC_RE.match(str(v)):
                raise Invalid(f"{where}: status key {v!r} is not a Minecraft version")
            if not isinstance(s, str) or not STATE_RE.match(s):
                raise Invalid(f"{where}: status {v}={s!r} is not open, fixed@<commit>, wontfix:<reason> or n/a")
        lo = e["lowest"]
        if not isinstance(lo, str) or not MC_RE.match(lo) or st.get(lo) in (None, "n/a"):
            raise Invalid(f"{where}: lowest {lo!r} must be a version in status that is affected (not n/a)")
        if any(mc_key(v) < mc_key(lo) and s != "n/a" for v, s in st.items()):
            raise Invalid(f"{where}: a version below lowest {lo} is affected: lowest must be the lowest")
        if e["source"] not in SOURCES:
            raise Invalid(f"{where}: source {e['source']!r} is not one of {', '.join(SOURCES)}")
        if "files" in e and (not isinstance(e["files"], list) or not all(isinstance(f, str) and f for f in e["files"])):
            raise Invalid(f"{where}: files must be a list of paths")
        pl = e.get("platforms", PLATFORMS)
        if "platforms" in e and (not isinstance(pl, list) or not pl or len(set(pl)) != len(pl)
                                 or not set(pl) <= set(PLATFORMS)):
            raise Invalid(f"{where}: platforms must be a non-empty list of {', '.join(PLATFORMS)} (absent: all)")
        if "notes" in e and not isinstance(e["notes"], str):
            raise Invalid(f"{where}: notes must be text")
    return data


def empty():
    return {"schema": SCHEMA, "issues": []}


# ---------------------------------------------------------------- reading and writing

def ledger_rel(mod=None):
    if mod is not None and not MOD_RE.match(mod):
        raise Invalid(f"--mod {mod!r} is not a mod id")
    return f".port/{mod}/{LEDGER_NAME}" if mod else f".port/{LEDGER_NAME}"


def read_ledger(path=None, repo=None, ref=None, mod=None):
    """-> validated ledger dict. From `ref` (git show REF:<ledger>) when given, else from the file."""
    if ref:
        r = subprocess.run(["git", "-C", str(repo or "."), "show", f"{ref}:{ledger_rel(mod)}"], capture_output=True,
                           text=True, encoding="utf-8")
        if r.returncode:
            ok = subprocess.run(["git", "-C", str(repo or "."), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                                capture_output=True).returncode == 0
            if not ok:
                raise Invalid(f"no commit {ref!r} in {repo or '.'}")
            return empty()                                       # the ref has no ledger yet
        text = r.stdout
    else:
        p = pathlib.Path(path)
        if not p.is_file():
            return empty()
        text = p.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except ValueError as e:
        raise Invalid(f"not JSON: {e}")
    return validate(data)


def natural(i):
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", i)]


def dump(data):
    data = {**data, "issues": sorted(data["issues"], key=lambda e: natural(e["id"]))}
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def write_ledger(path, data):
    """Atomic: a temporary file beside it, then a rename."""
    validate(data)
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".known-issues.", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(dump(data))
        os.replace(tmp, p)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------- queries

def find(data, i):
    for e in data["issues"]:
        if e["id"] == i:
            return e
    raise Invalid(f"no issue {i!r} in the ledger")


def open_versions(e):
    return sorted((v for v, s in e["status"].items() if s == "open"), key=mc_key)


def versions(data):
    return sorted({v for e in data["issues"] for v in e["status"]}, key=mc_key)


def order(e):
    return (sev_rank(e["severity"]), mc_key(e["lowest"]), natural(e["id"]))


def select(data, budget=DEFAULT_BUDGET, touched=(), run_versions=(), add=(), remove=()):
    """The default policy (issue #135 section 8) -> [(issue, why)] in the order they are taken."""
    for i in (*add, *remove):
        find(data, i)
    rv = set(run_versions)

    def live(e):
        ov = open_versions(e)
        return [v for v in ov if v in rv] if rv else ov
    cand = [e for e in data["issues"] if live(e)]
    out = [(e, "S1: always taken") for e in sorted(cand, key=order) if e["severity"] == "S1"]
    s2 = sorted((e for e in cand if e["severity"] == "S2"), key=order)
    out += [(e, f"S2 within the budget of {budget}, lowest affected version first") for e in s2[:max(budget, 0)]]
    t = set(touched)
    out += [(e, "S3 in a file the run already touches") for e in sorted(cand, key=order)
            if e["severity"] == "S3" and t & set(e.get("files", []))]
    taken = {e["id"] for e, _ in out}
    out += [(find(data, i), "added by the owner") for i in add if i not in taken]
    gone = set(remove)
    return [(e, why) for e, why in out if e["id"] not in gone]


def applies(e, platform):
    """Does the issue affect that platform's jar? (no platforms key: every platform)"""
    return platform is None or platform in e.get("platforms", PLATFORMS)


def scope(e):
    return f" ({'/'.join(e['platforms'])} only)" if e.get("platforms") and set(e["platforms"]) != set(PLATFORMS) else ""


def blocks_publish(data, version, platform):
    """The open S1s at `version` that apply to `platform`'s jar."""
    return [e for e in sorted(data["issues"], key=order)
            if e["severity"] == "S1" and e["status"].get(version) == "open" and applies(e, platform)]


def reopens(severity):
    return severity == "S1"


def fixed_in(repo, commit, tag):
    """True / False: the fix commit is / is not in the tag; None when git cannot tell."""
    r = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", commit, tag], capture_output=True)
    return {0: True, 1: False}.get(r.returncode)


def release_notes(data, version, tag, min_sev="S3", repo=None, platform=None):
    rows = []
    for e in sorted(data["issues"], key=order):
        if sev_rank(e["severity"]) > sev_rank(min_sev) or not applies(e, platform):
            continue
        s = e["status"].get(version, "n/a")
        if s == "open":
            rows.append((e, ""))
        elif s.startswith("fixed@") and repo is not None:
            inside = fixed_in(repo, s[6:], tag)
            if inside is not True:
                rows.append((e, " (fixed after this release)" if inside is False else
                             " (its fix commit is not in this clone: listed as open)"))
    lines = [f"### Known issues in {tag} (Minecraft {version}{', ' + platform + ' jar' if platform else ''})", ""]
    if not rows:
        lines.append(f"No known issues at {min_sev} or above.")
    for e, extra in rows:
        lines.append(f"- **{e['severity']}** {e['title']} ({e['id']}){'' if platform else scope(e)}{extra}")
    lines += ["", f"_From the port's known-issues ledger; {'S1' if min_sev == 'S1' else 'S1-' + min_sev} listed._"]
    return "\n".join(lines) + "\n"


def cell(s):
    if s is None or s == "n/a":
        return "n/a"
    if s.startswith("fixed@"):
        return f"fixed `{s[6:13]}`"
    if s.startswith("wontfix:"):
        return "wontfix"
    return "**open**"


def md(text):
    return text.replace("|", "\\|").replace("\n", " ")


def render_issue(data):
    vs = versions(data)
    out = ["<!-- known-issues: generated by tools/known-issues.py render-issue from .port/KNOWN-ISSUES.json; "
           "do not edit by hand -->", "## Known issues by Minecraft version", ""]
    issues = sorted(data["issues"], key=order)
    if not issues:
        return "\n".join(out + ["No known issues.", ""])
    counts = {s: sum(1 for e in issues if e["severity"] == s and open_versions(e)) for s in SEVERITIES}
    out += ["Open: " + ", ".join(f"{s} {counts[s]}" for s in SEVERITIES) + ".", ""]
    out.append("| Sev | ID | Issue | " + " | ".join(vs) + " |")
    out.append("|---|---|---| " + " | ".join("---" for _ in vs) + " |")
    for e in issues:
        sev = e["severity"] + ("\\*" if e.get("override") else "")
        out.append(f"| {sev} | {e['id']} | {md(e['title'] + scope(e))} | " + " | ".join(cell(e["status"].get(v)) for v in vs) + " |")
    notes = [f"- {e['id']}: graded {e['override']['severity']} by {e['override'].get('by', 'owner')} "
             f"(the rubric gives {derive(e['rubric'])}): {md(e['override']['reason'])}" for e in issues if e.get("override")]
    notes += [f"- {e['id']} at {v}: won't fix: {md(s[8:])}" for e in issues
              for v, s in sorted(e["status"].items(), key=lambda x: mc_key(x[0])) if s.startswith("wontfix:")]
    if notes:
        out += ["", "\\* an owner override.", ""] + notes
    out += ["", "S1 crash, kick, data loss or hang (blocks publishing); S2 a core feature broken in normal play, no "
            "workaround; S3 cosmetic, uncommon or with a workaround; S4 invisible or an unusual setup."]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- the weekly plan issue (issue #135, part 2a)

PLAN_MARK = "<!-- weekly-plan:v1 -->"
DATA_BEGIN, DATA_END = "<!-- weekly-plan:data", "-->"
CHOICES = ("take", "keep", "patch", "content")
WRITE_PERMS = ("admin", "maintain", "write")
BOT = "github-actions[bot]"
SHOWN_COMMITS = 20
CHECKBOX_RE = re.compile(r"(?m)^\s*[-*] \[( |x|X)\] .*?<!-- (ki|authors):(\S+?) (default|candidate) -->\s*$")
DECIDE_RE = re.compile(r"^/decide\s+(\S+)\s+(\S+)\s*(.*)$")
GRADE_RE = re.compile(r"^/grade\s+(\S+)\s+(\S+)\s*(.*)$")


def data_block(d):
    """The issue's machine-readable part: JSON in an HTML comment (no `--` can end it early)."""
    return DATA_BEGIN + "\n" + json.dumps(d, sort_keys=True, ensure_ascii=True).replace("--", "-\\u002d") + "\n" + DATA_END


def plan_data(body):
    i = (body or "").find(DATA_BEGIN)
    if i < 0 or PLAN_MARK not in body:
        raise Invalid("not a weekly plan issue (no weekly-plan:data block)")
    j = body.find(DATA_END, i + len(DATA_BEGIN))
    try:
        return json.loads(body[i + len(DATA_BEGIN):j])
    except ValueError as e:
        raise Invalid(f"the plan issue's data block is not JSON: {e}")


def mention(owner):
    return "@" + (owner.get("login") or owner.get("team") or "?")


def resolve_owner(plan_owner, repo_owner, owner_type):
    """The fork owner a plan is addressed to: plan_owner (a user, or `@org/team`) from the fork's port config, else
    the repository's owner when that is a user. An organization with no plan_owner has no single owner: Invalid."""
    p = (plan_owner or "").strip().lstrip("@")
    if p:
        if re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+", p):
            return {"team": p}
        if re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", p):
            return {"login": p}
        raise Invalid(f"plan_owner {plan_owner!r} is neither a GitHub login nor @org/team")
    if (owner_type or "").lower() == "user" and repo_owner:
        return {"login": repo_owner}
    raise Invalid(f"{repo_owner or 'the repository'} is owned by an organization and the fork's port config names no "
                  "plan_owner: re-run keep-current setup (tools/weekly-plan.py install --plan-owner LOGIN|@org/team)")


def _q(fork_repo, i, multi, clash):
    return f"{fork_repo}:{i}" if multi and i in clash else i


def render_plan(inp):
    """PLAN-INPUT (tools/weekly-plan.py) -> {"title", "body", "decision_needed"}."""
    forks = inp["forks"]
    multi = len(forks) > 1
    prev = inp.get("previous") or {}
    pf = prev.get("frozen") or {}
    pticks = {f["repo"]: f.get("ticks", {}) for f in pf.get("forks", [])}
    pauth = {f["repo"]: f.get("take_authors") for f in pf.get("forks", [])}
    seen, clash = set(), set()
    for f in forks:
        ids = {r["id"] for r in f.get("selection", [])} | {r["id"] for r in f.get("candidates", [])}
        clash |= seen & ids
        seen |= ids
    owner = inp["owner"]
    dl = inp["deadline_text"]
    lines = [PLAN_MARK,
             f"**Deadline: the round starts {dl}. With no answer by then, the defaults below apply.**", "",
             f"{mention(owner)}: the plan for this week's keep-current round of {', '.join(f['repo'] for f in forks)}. "
             "Tick or untick a box to change what the round takes; answer an escalated decision with "
             "`/decide <id> <choice>` and override a grade with `/grade <ID> <S1-S4> [reason]`, each as a comment here. "
             "Only your replies and those of users with write access count. The round reads this issue once, at its "
             "start; after that nothing enters the run except a new S1.", ""]
    if prev.get("issue"):
        lines += [f"> **Last week's round did not run:** #{prev['issue']} was never closed by a round. Its ticks and "
                  f"replies are carried forward below, and #{prev['issue']} is closed as superseded.", ""]
    escalated, data_forks = 0, []
    for f in forks:
        repo = f["repo"]
        lines += [f"## {repo}" + (f" ({f['role']})" if f.get("role") else "") + f" -- head `{f['head']}`", "",
                  "### The authors' new commits", ""]
        n = f.get("new_count", 0)
        take = pauth.get(repo) if pauth.get(repo) is not None else True
        if n:
            lines.append(f"- [{'x' if take else ' '}] Take the authors' new commits this round <!-- authors:{repo} default -->")
            lines += ["", f"`{f['upstream']}` `{f['upstream_branch']}` at `{f['upstream_commit'][:12]}`: {n} new commit(s) "
                      f"the head does not have:", ""]
            lines += [f"- `{c['sha'][:12]}` {md(c['subject'])}" for c in f.get("new_commits", [])[:SHOWN_COMMITS]]
            if n > SHOWN_COMMITS:
                lines.append(f"- ... and {n - SHOWN_COMMITS} more")
        else:
            lines.append(f"No new commits from the authors: the head has `{f['upstream']}` `{f['upstream_branch']}` "
                         f"at `{(f.get('upstream_commit') or '?')[:12]}`.")
        lines += ["", "### Known issues", ""]
        ticks = pticks.get(repo, {})
        sel, cand = f.get("selection", []), f.get("candidates", [])
        for r in sel:
            on = ticks.get(r["id"], True)
            lines.append(f"- [{'x' if on else ' '}] **{r['severity']}** `{_q(repo, r['id'], multi, clash)}` "
                         f"{md(r.get('title', ''))} -- lowest {r['lowest']}; {r['why']} <!-- ki:{repo}:{r['id']} default -->")
        for r in cand:
            on = ticks.get(r["id"], False)
            lines.append(f"- [{'x' if on else ' '}] **{r['severity']}** `{_q(repo, r['id'], multi, clash)}` "
                         f"{md(r.get('title', ''))} -- lowest {r['lowest']}; not in the default selection "
                         f"<!-- ki:{repo}:{r['id']} candidate -->")
        if not sel and not cand:
            lines.append(f"No open known issues in the ledger (`.port/{LEDGER_NAME}` on `{f['head']}`).")
        lines += ["", "### Decisions", ""]
        c = f.get("cascade")
        decs = []
        if c is None:
            lines.append("A single-version fork (no version branches): there is no cascade to plan.")
        else:
            plan = c.get("plan") or {}
            ds = plan.get("decisions", [])
            esc = [d for d in ds if d.get("escalate")]
            escalated += len(esc)
            lines.append(f"The cascade plan (`fork-branches.py cascade {c['range']} --plan`, git and the rename rows "
                         f"only): {len(plan.get('mechanical', []))} file(s) merged mechanically, {len(ds)} decision(s), "
                         f"{len(esc)} escalated to you.")
            for d in esc:
                lines += ["", f"- **`{d['id']}`** `{d['file']}` at {', '.join(d['versions'])}: {md(d.get('reasoning', ''))} "
                          f"Options: {', '.join('`' + x + '`' for x in CHOICES)}. Recommended: `{d['choice']}` (the "
                          f"default with no answer). Answer: `/decide {d['id']} <choice>`."]
            rest = [d for d in ds if not d.get("escalate")]
            if rest:
                lines += ["", "Decided by the defaults (no answer needed; `/decide <id> <choice>` overrides one):", ""]
                lines += [f"- `{d['id']}` `{d['file']}` at {', '.join(d['versions'])} -> `{d['choice']}`" for d in rest]
            decs = [{"id": d["id"], "file": d["file"], "versions": d["versions"], "recommended": d["choice"],
                     "escalate": bool(d.get("escalate"))} for d in ds]
        lines.append("")
        data_forks.append({"repo": repo, "head": f["head"], "role": f.get("role", ""), "new_count": n,
                           "upstream": f["upstream"], "upstream_branch": f["upstream_branch"],
                           "upstream_commit": f.get("upstream_commit"), "head_commit": f.get("head_commit"),
                           "defaults": [r["id"] for r in sel], "candidates": [r["id"] for r in cand],
                           "ids": sorted(set(f.get("ledger_ids", [])) | {r["id"] for r in sel + cand}, key=natural),
                           "cascade": None if c is None else {"range": c["range"], "decisions": decs}})
    lines += ["## Grade overrides", "",
              "Each issue above shows its grade (S1 crash, kick, data loss, hang, security or build; S2 a core feature "
              "broken in normal play with no workaround; S3 cosmetic, uncommon or with a workaround; S4 invisible or an "
              "unusual setup). Reply `/grade <ID> <S1-S4> [reason]` to override one; the round records it as your "
              "override (`known-issues.py grade --override`)"
              + (", and an ID two forks share is written `<owner/repo>:<ID>`." if clash else "."), ""]
    carried = {"decide": [d for d in pf.get("decisions", []) if d.get("by") not in (None, "default")],
               "grade": list(pf.get("grades", []))}
    if prev.get("issue") and (carried["decide"] or carried["grade"]):
        lines += [f"Carried from #{prev['issue']} (still applied unless you reply again):", ""]
        lines += [f"- `/decide {d['id']} {d['choice']}` by @{d['by']}" for d in carried["decide"]]
        lines += [f"- `/grade {g['fork']}:{g['id']} {g['severity']}` by @{g['by']}" for g in carried["grade"]]
        lines.append("")
    data = {"schema": 1, "repo": inp["repo"], "week": inp["week"], "deadline": inp["deadline"], "owner": owner,
            "forks": data_forks, "carried": carried if prev.get("issue") else {"decide": [], "grade": []},
            "carried_from": prev.get("issue"), "migrator": inp.get("migrator", {})}
    lines += [data_block(data), ""]
    need = escalated > 0
    title = (("[decision needed] " if need else "") + f"Weekly plan {inp['week']}: keep-current round "
             f"{inp['deadline'][:10]} ({', '.join(f['repo'] for f in forks)})")
    return {"title": title, "body": "\n".join(lines), "decision_needed": need, "week": inp["week"]}


def read_plan(issue, comments, allowed, now=None):
    """The plan issue's answers, frozen: checkboxes (the issue body, editable only by the bot and write-access users),
    then every `/decide` and `/grade` reply from an allowed user, oldest first (a later one wins). -> dict."""
    body = issue.get("body") or ""
    data = plan_data(body)
    forks = {f["repo"]: f for f in data["forks"]}
    ticks = {r: {} for r in forks}
    authors = {}
    for m in CHECKBOX_RE.finditer(body):
        on, kind, key = m.group(1) != " ", m.group(2), m.group(3)
        if kind == "authors":
            if key in forks:
                authors[key] = on
            continue
        repo, _, i = key.rpartition(":")
        if repo in ticks:
            ticks[repo][i] = on
    decisions = {}
    for r, f in forks.items():
        for d in (f.get("cascade") or {}).get("decisions", []):
            decisions[(r, d["id"])] = {"choice": d["recommended"], "by": "default", "reason": ""}
    grades, ignored = {}, []

    def target(ref, pool):
        repo, sep, i = ref.rpartition(":")
        if sep:
            return [(repo, i)] if (repo, i) in pool else []
        return [k for k in pool if k[1] == ref]
    dec_pool = set(decisions)
    id_pool = {(r, i) for r, f in forks.items() for i in f.get("ids", []) + f["defaults"] + f["candidates"]}
    for d in data.get("carried", {}).get("decide", []):
        if (d.get("fork"), d.get("id")) in dec_pool:
            decisions[(d["fork"], d["id"])] = {"choice": d["choice"], "by": d["by"], "reason": d.get("reason", ""),
                                               "carried_from": data.get("carried_from")}
    for g in data.get("carried", {}).get("grade", []):
        if (g.get("fork"), g.get("id")) in id_pool:
            grades[(g["fork"], g["id"])] = dict(g, carried_from=data.get("carried_from"))
    for c in sorted(comments, key=lambda c: (c.get("created_at") or "", c.get("id") or 0)):
        who = ((c.get("user") or {}).get("login") or "")
        if who == BOT:
            continue
        for ln in (c.get("body") or "").splitlines():
            ln = ln.strip()
            m, kind = DECIDE_RE.match(ln), "decide"
            if not m:
                m, kind = GRADE_RE.match(ln), "grade"
            if not m:
                continue
            if not allowed(who):
                ignored.append({"by": who, "line": ln, "why": "not the owner and no write access"})
                continue
            ref, val, rest = m.group(1), m.group(2), m.group(3).strip()
            hit = target(ref, dec_pool if kind == "decide" else id_pool)
            if len(hit) != 1:
                ignored.append({"by": who, "line": ln, "why": ("ambiguous: two forks have it, write <owner/repo>:<id>"
                                                               if hit else f"no such {'decision' if kind == 'decide' else 'issue'} in this plan")})
                continue
            if kind == "decide":
                if val not in CHOICES:
                    ignored.append({"by": who, "line": ln, "why": f"choice {val!r} is not one of {', '.join(CHOICES)}"})
                    continue
                decisions[hit[0]] = {"choice": val, "by": who, "reason": rest}
            else:
                if val.upper() not in SEVERITIES:
                    ignored.append({"by": who, "line": ln, "why": f"{val!r} is not S1-S4"})
                    continue
                grades[hit[0]] = {"fork": hit[0][0], "id": hit[0][1], "severity": val.upper(), "by": who,
                                  "reason": rest or f"owner override in plan issue #{issue.get('number')}"}
    out_forks = []
    for r, f in forks.items():
        t = ticks[r]
        sel = [i for i in f["defaults"] + f["candidates"] if t.get(i, i in f["defaults"])]
        casc = f.get("cascade")
        out_forks.append({
            "repo": r, "head": f["head"], "role": f.get("role", ""), "upstream": f.get("upstream"),
            "upstream_branch": f.get("upstream_branch"), "upstream_commit": f.get("upstream_commit"),
            "take_authors": bool(f.get("new_count")) and authors.get(r, True),
            "selected": sel, "ticks": {i: i in sel for i in f["defaults"] + f["candidates"]},
            "added": [i for i in sel if i not in f["defaults"]],
            "removed": [i for i in f["defaults"] if i not in sel],
            "cascade": None if casc is None else {
                "range": casc["range"],
                "decisions": [{"id": d["id"], "file": d["file"], "versions": d["versions"], "recommended": d["recommended"],
                               "choice": decisions[(r, d["id"])]["choice"], "by": decisions[(r, d["id"])]["by"],
                               "reason": decisions[(r, d["id"])]["reason"]} for d in casc["decisions"]]}})
    return {"schema": 1, "issue": issue.get("number"), "repo": data["repo"], "week": data["week"],
            "deadline": data["deadline"], "owner": data["owner"], "frozen_at": now, "forks": out_forks,
            "decisions": [dict({"fork": r, "id": i}, **v) for (r, i), v in sorted(decisions.items())],
            "grades": [grades[k] for k in sorted(grades)], "ignored": ignored, "carried_from": data.get("carried_from")}


def gh_api(path, method="GET", data=None):
    """GitHub's REST API: GITHUB_TOKEN / GH_TOKEN when set, else the `gh` command line. -> parsed JSON."""
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        import urllib.request
        req = urllib.request.Request("https://api.github.com" + path, method=method,
                                     data=None if data is None else json.dumps(data).encode("utf-8"),
                                     headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
                                              "User-Agent": "mmvu-weekly-plan", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode("utf-8")
        return json.loads(text) if text.strip() else {}
    cmd = ["gh", "api", "-X", method, path] + (["--input", "-"] if data is not None else [])
    r = subprocess.run(cmd, input=None if data is None else json.dumps(data), capture_output=True, text=True,
                       encoding="utf-8")
    if r.returncode:
        raise Invalid(f"gh api {path}: {(r.stderr or r.stdout).strip()[-300:]} (set GITHUB_TOKEN or log in with gh)")
    return json.loads(r.stdout) if r.stdout.strip() else {}


def gh_pages(path, api=gh_api):
    out, page = [], 1
    while True:
        sep = "&" if "?" in path else "?"
        got = api(f"{path}{sep}per_page=100&page={page}")
        out += got
        if len(got) < 100:
            return out
        page += 1


def open_plan_issues(slug, api=gh_api):
    """Open plan issues of the repository, newest first."""
    rows = gh_pages(f"/repos/{slug}/issues?state=open&creator=github-actions%5Bbot%5D", api)
    return sorted((i for i in rows if "pull_request" not in i and PLAN_MARK in (i.get("body") or "")),
                  key=lambda i: -i["number"])


def owner_check(slug, owner, api=gh_api):
    """allowed(login): the resolved owner (or a member of the owner team), or a user with write access."""
    cache = {}

    def allowed(login):
        if not login:
            return False
        if login in cache:
            return cache[login]
        ok = login.lower() == (owner.get("login") or "").lower()
        if not ok and owner.get("team"):
            org, team = owner["team"].split("/", 1)
            try:
                ok = api(f"/orgs/{org}/teams/{team}/memberships/{login}").get("state") == "active"
            except Exception:
                ok = False
        if not ok:
            try:
                p = api(f"/repos/{slug}/collaborators/{login}/permission")
                ok = p.get("permission") in WRITE_PERMS or p.get("role_name") in WRITE_PERMS
            except Exception:
                ok = False
        cache[login] = ok
        return ok
    return allowed


def slug_of(repo):
    r = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"], capture_output=True, text=True,
                       encoding="utf-8")
    m = re.search(r"github\.com[/:]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", r.stdout.strip())
    return m.group(1) if m else None


# ---------------------------------------------------------------- the README Ports block

PORTS_BEGIN, PORTS_END = "<!-- ports:begin -->", "<!-- ports:end -->"


def newest_build(repo, v, releases=None):
    """(tag, kind) of version v's newest Port CI tag (`<mod>-<ver>-mc<v>[-rN]`): kind release / pre-release from the
    releases list when given (drafts skipped), else `tag`. (None, None) when there is none."""
    rx = re.compile(rf"-mc{re.escape(v)}(?:-r\d+)?$")
    if releases is not None:
        rs = sorted((r for r in releases if not r.get("draft") and rx.search(r.get("tag_name", ""))),
                    key=lambda r: r.get("published_at") or r.get("created_at") or "", reverse=True)
        return (rs[0]["tag_name"], "pre-release" if rs[0].get("prerelease") else "release") if rs else (None, None)
    r = subprocess.run(["git", "-C", str(repo), "tag", "--list", f"*-mc{v}*", "--sort=-creatordate"],
                       capture_output=True, text=True, encoding="utf-8")
    tags = [t for t in r.stdout.split() if rx.search(t)]
    return (tags[0], "tag") if tags else (None, None)


def ports_rows(repo, head, releases=None, done_fn=None, versions_fn=None):
    """[(mc, branch, tag, kind, done)] -- the head branch first, then each version branch in version order."""
    gp = subprocess.run(["git", "-C", str(repo), "show", f"{head}:gradle.properties"], capture_output=True, text=True,
                        encoding="utf-8", errors="replace").stdout
    m = re.search(r"(?m)^\s*minecraft_version\s*=\s*(\S+)", gp)
    head_mc = m.group(1) if m else head.rsplit("-", 1)[-1]
    rows = [(head_mc, head, *newest_build(repo, head_mc, releases), "head")]
    for v in sorted(versions_fn(repo) if versions_fn else [], key=mc_key):
        if v == head_mc:
            continue
        done = done_fn(v) if done_fn else None
        rows.append((v, f"neoforge-{v}", *newest_build(repo, v, releases),
                     "-" if done is None else ("done" if not done else "not done")))
    return rows


def ports_block(rows, slug, head, pinned=None):
    base = f"https://github.com/{slug}" if slug else ""
    link = lambda text, path: f"[{text}]({base}/{path})" if base else text
    out = [PORTS_BEGIN, "<!-- generated by tools/known-issues.py render-ports; do not edit inside these markers -->",
           "## Ports", "", "| Minecraft | Branch | Newest build | Done |", "|---|---|---|---|"]
    for mc, br, tag, kind, done in rows:
        build = link(f"`{tag}`", f"releases/tag/{tag}") + f" ({kind})" if tag else "none yet"
        out.append(f"| {mc} | {link(f'`{br}`', f'tree/{br}')} | {build} | {done} |")
    ki = [link("by Minecraft version", f"issues/{pinned}")] if pinned else []
    ki.append(link(f"`.port/{LEDGER_NAME}` on `{head}`", f"blob/{head}/.port/{LEDGER_NAME}"))
    out += ["", "Known issues: " + " · ".join(ki), PORTS_END]
    return "\n".join(out) + "\n"


def put_block(text, block):
    """README text with the Ports block replaced (or appended): every byte outside the markers kept."""
    i, j = text.find(PORTS_BEGIN), text.find(PORTS_END)
    if i >= 0 and j > i:
        end = j + len(PORTS_END)
        if text[end:end + 1] == "\n":
            end += 1
        return text[:i] + block + text[end:]
    if i >= 0 or j >= 0:
        raise Invalid("the README has only one of the ports markers: fix it by hand, then rerun")
    return text + ("" if not text or text.endswith("\n") else "\n") + ("\n" if text else "") + block


# ---------------------------------------------------------------- the command line

def yesno(s):
    if s in ("yes", "true"):
        return True
    if s in ("no", "false"):
        return False
    raise Invalid(f"--workaround {s!r}: yes or no")


def parse_states(items):
    out = {}
    for it in items or ():
        v, sep, s = it.partition("=")
        if not sep:
            raise Invalid(f"--set/--status {it!r}: MC=STATE")
        out[v.strip()] = s.strip()
    return out


def next_id(data):
    n = [int(m.group(1)) for e in data["issues"] for m in [re.match(r"^KI-(\d+)$", e["id"])] if m]
    return f"KI-{max(n, default=0) + 1}"


def regrade(e, a):
    for k in ("failure", "visibility", "reach"):
        if getattr(a, k, None):
            e["rubric"][k] = getattr(a, k)
    if getattr(a, "workaround", None):
        e["rubric"]["workaround"] = yesno(a.workaround)
    if getattr(a, "clear_override", False):
        e["override"] = None
    if getattr(a, "override", None):
        if not (a.reason or "").strip():
            raise Invalid("--override needs --reason (the override is recorded with why)")
        e["override"] = {"severity": a.override, "reason": a.reason.strip(), "by": a.by or "owner"}
    for k, allowed in (("failure", FAILURES), ("visibility", VISIBILITY), ("reach", REACH)):
        if e["rubric"][k] not in allowed:
            raise Invalid(f"--{k} {e['rubric'][k]!r} is not one of {', '.join(allowed)}")
    e["severity"] = effective(e)


def build_parser():
    ap = argparse.ArgumentParser(prog="known-issues.py", description="The fork port's known-issues ledger (S1-S4).")
    ap.add_argument("--self-check", action="store_true")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--ledger", help=f"the ledger file (default <repo>/.port/{LEDGER_NAME})")
        p.add_argument("--repo", help="the fork checkout (default .)")
        p.add_argument("--ref", help="read the ledger committed at this ref (the head branch); read-only")
        p.add_argument("--mod", help="a multi-mod repository's ledger for this mod (.port/<mod>/)")
        return p

    def rubric_args(p, required):
        p.add_argument("--failure", choices=FAILURES, required=required)
        p.add_argument("--visibility", choices=VISIBILITY, required=required)
        p.add_argument("--reach", choices=REACH, required=required)
        p.add_argument("--workaround", choices=("yes", "no"), required=required)
    p = common(sub.add_parser("add", help="record a new issue (graded from the rubric)"))
    p.add_argument("--id"); p.add_argument("--title", required=True)
    rubric_args(p, True)
    p.add_argument("--lowest", required=True, help="the lowest affected Minecraft version")
    p.add_argument("--status", action="append", metavar="MC=STATE", help="default: <lowest>=open")
    p.add_argument("--source", choices=SOURCES, default="review")
    p.add_argument("--notes", default="")
    p.add_argument("--file", action="append", dest="files", metavar="PATH")
    p.add_argument("--platform", action="append", dest="platforms", choices=PLATFORMS,
                   help="only this platform's jar is affected (repeatable; default: every platform)")
    p = common(sub.add_parser("grade", help="re-grade an issue; an owner override wins and is recorded"))
    p.add_argument("id")
    rubric_args(p, False)
    p.add_argument("--override", choices=SEVERITIES); p.add_argument("--reason"); p.add_argument("--by")
    p.add_argument("--clear-override", action="store_true")
    p = common(sub.add_parser("status", help="set an issue's state at Minecraft versions"))
    p.add_argument("id"); p.add_argument("--set", action="append", required=True, metavar="MC=STATE")
    p.add_argument("--lowest")
    p = common(sub.add_parser("list", help="the issues, filtered by version and severity"))
    p.add_argument("--version"); p.add_argument("--severity", help="comma-separated, e.g. S1,S2")
    p.add_argument("--min", choices=SEVERITIES, help="this severity or worse")
    p.add_argument("--open", action="store_true", help="only issues open (at --version, or anywhere)")
    p.add_argument("--platform", choices=PLATFORMS, help="only issues that affect this platform's jar")
    p.add_argument("--json", action="store_true")
    p = common(sub.add_parser("select", help="the issues a run takes (the default policy, then the owner's changes)"))
    p.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help=f"S2s taken (default {DEFAULT_BUDGET})")
    p.add_argument("--touched", action="append", default=[], metavar="PATH", help="a file the run already changes")
    p.add_argument("--version", action="append", default=[], metavar="MC", help="the run's versions (default: all)")
    p.add_argument("--add", action="append", default=[], metavar="ID")
    p.add_argument("--remove", action="append", default=[], metavar="ID")
    p.add_argument("--json", action="store_true")
    common(sub.add_parser("render-issue", help="the pinned issue's markdown"))
    p = common(sub.add_parser("release-notes", help="the open issues of one version at a tag"))
    p.add_argument("--version", required=True); p.add_argument("--tag", required=True)
    p.add_argument("--min", choices=SEVERITIES, default="S3", help="the threshold (default S3: S4 omitted)")
    p.add_argument("--platform", choices=PLATFORMS, help="the notes of this platform's upload (its issues only)")
    p = common(sub.add_parser("blocks-publish", help="exit 1 when an open S1 applies to this platform at this version"))
    p.add_argument("--version", required=True); p.add_argument("--platform", choices=PLATFORMS, required=True)
    p = common(sub.add_parser("reopens", help="may a finding at a done version reopen it? (S1 only)"))
    p.add_argument("--version", required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--severity", choices=SEVERITIES); g.add_argument("--id")
    p = sub.add_parser("render-plan", help="the weekly plan issue (title and body) from tools/weekly-plan.py's input")
    p.add_argument("--input", required=True); p.add_argument("--out", help="write {title, body} JSON here (default: print)")
    p = sub.add_parser("read-plan", help="the plan issue's ticks and owner replies, frozen as JSON (the round's input)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--issue", type=int); g.add_argument("--latest", action="store_true", help="the newest open plan issue")
    p.add_argument("--slug", help="owner/repo the issue is on (default: the origin of --repo)")
    p.add_argument("--repo", help="a clone of that repository (default .)")
    p.add_argument("--owner", help="the fork owner (default: the one the plan was addressed to)")
    p.add_argument("--from-json", help="read the issue, comments and permissions from this file (offline)")
    p.add_argument("--out"); p.add_argument("--decisions-dir", help="write each fork's cascade --apply-decisions file here")
    p = common(sub.add_parser("render-ports", help="the README's Ports block (one row per Minecraft version)"))
    p.add_argument("--head", help="the head branch (default: the checked-out branch)")
    p.add_argument("--slug", help="owner/repo for the links (default: the origin remote)")
    p.add_argument("--releases", help="the releases list as JSON (gh api repos/O/R/releases)")
    p.add_argument("--fetch-releases", action="store_true", help="read the releases from GitHub")
    p.add_argument("--pinned-issue", type=int, help="the pinned known-issues issue")
    p.add_argument("--readme", help="the README to update (with --write; default <repo>/README.md)")
    p.add_argument("--write", action="store_true", help="replace the marked block in the README (else print it)")
    p.add_argument("--no-done", action="store_true", help="skip the done checks (the Done column reads -)")
    return ap


def run(argv):
    out = print
    a = build_parser().parse_args(argv)
    if a.self_check:
        return self_check()
    if not a.cmd:
        build_parser().print_help()
        return 2
    if a.cmd == "render-plan":
        try:
            inp = json.loads(pathlib.Path(a.input).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise Invalid(f"--input {a.input}: {e}")
        res = render_plan(inp)
        text = json.dumps(res, indent=1, ensure_ascii=False) + "\n"
        if a.out:
            pathlib.Path(a.out).write_text(text, encoding="utf-8")
            out(f"render-plan: {res['title']} -> {a.out}")
        else:
            out(text, end="")
        return 0
    if a.cmd == "read-plan":
        return cmd_read_plan(a, out)
    repo = pathlib.Path(a.repo or ".")
    path = pathlib.Path(a.ledger) if a.ledger else repo / ledger_rel(a.mod)
    if a.cmd == "render-ports":
        return cmd_render_ports(a, repo, out)
    writes = a.cmd in ("add", "grade", "status")
    if writes and a.ref:
        raise Invalid(f"--ref is read-only: {a.cmd} writes the file on the head branch's checkout")
    data = read_ledger(path, repo, a.ref, a.mod)
    vs = getattr(a, "version", None) or []
    for v in [*([vs] if isinstance(vs, str) else vs), *([a.lowest] if getattr(a, "lowest", None) else [])]:
        if not MC_RE.match(v):
            raise Invalid(f"{v!r} is not a Minecraft version (--version, --lowest)")
    if a.cmd == "add":
        i = a.id or next_id(data)
        if any(e["id"] == i for e in data["issues"]):
            raise Invalid(f"issue {i} exists already")
        e = {"id": i, "title": a.title.strip(), "override": None, "lowest": a.lowest, "source": a.source,
             "rubric": {"failure": a.failure, "visibility": a.visibility, "reach": a.reach,
                        "workaround": yesno(a.workaround)},
             "status": parse_states(a.status) or {a.lowest: "open"}}
        if a.notes:
            e["notes"] = a.notes
        if a.files:
            e["files"] = sorted(set(a.files))
        if a.platforms:
            e["platforms"] = sorted(set(a.platforms), key=PLATFORMS.index)
        e["severity"] = effective(e)
        data["issues"].append(e)
        write_ledger(path, data)
        out(f"{i}: {e['severity']} ({a.failure}, {a.visibility}, {a.reach}, workaround {a.workaround}) -> {path}")
        return 0
    if a.cmd == "grade":
        e = find(data, a.id)
        before = e["severity"]
        regrade(e, a)
        write_ledger(path, data)
        r = e["rubric"]
        o = e.get("override")
        out(f"{e['id']}: {before} -> {e['severity']} (rubric {derive(r)}: {r['failure']}, {r['visibility']}, "
            f"{r['reach']}, workaround {'yes' if r['workaround'] else 'no'}"
            + (f"; override {o['severity']} by {o['by']}: {o['reason']}" if o else "") + ")")
        return 0
    if a.cmd == "status":
        e = find(data, a.id)
        e["status"].update(parse_states(a.set))
        if a.lowest:
            e["lowest"] = a.lowest
        write_ledger(path, data)
        out(f"{e['id']}: " + ", ".join(f"{v}={s}" for v, s in sorted(e["status"].items(), key=lambda x: mc_key(x[0]))))
        return 0
    if a.cmd == "list":
        sevs = set(x.strip() for x in a.severity.split(",")) if a.severity else set(SEVERITIES)
        if sevs - set(SEVERITIES):
            raise Invalid(f"--severity {a.severity!r}: S1-S4, comma-separated")
        rows = []
        for e in sorted(data["issues"], key=order):
            if e["severity"] not in sevs or (a.min and sev_rank(e["severity"]) > sev_rank(a.min)):
                continue
            if a.version and e["status"].get(a.version, "n/a") == "n/a":
                continue
            if not applies(e, a.platform):
                continue
            if a.open and (e["status"].get(a.version) != "open" if a.version else not open_versions(e)):
                continue
            rows.append(e)
        if a.json:
            out(json.dumps(rows, sort_keys=True, indent=2, ensure_ascii=False))
        for e in ([] if a.json else rows):
            st = e["status"].get(a.version) if a.version else ", ".join(
                f"{v}={s}" for v, s in sorted(e["status"].items(), key=lambda x: mc_key(x[0])))
            out(f"{e['id']}\t{e['severity']}{'*' if e.get('override') else ''}\tlowest {e['lowest']}\t{st}\t{e['title']}{scope(e)}")
        return 0
    if a.cmd == "select":
        picked = select(data, a.budget, a.touched, a.version, a.add, a.remove)
        if a.json:
            out(json.dumps([{"id": e["id"], "severity": e["severity"], "lowest": e["lowest"], "why": why}
                            for e, why in picked], indent=2))
        else:
            for e, why in picked:
                out(f"{e['id']}\t{e['severity']}\tlowest {e['lowest']}\t{why}\t{e['title']}")
            for i in a.remove:
                out(f"{i}\t{find(data, i)['severity']}\t-\tremoved by the owner")
        return 0
    if a.cmd == "render-issue":
        out(render_issue(data), end="")
        return 0
    if a.cmd == "release-notes":
        text = release_notes(data, a.version, a.tag, a.min, repo if a.repo else None, a.platform)
        out(text, end="")
        return 0
    if a.cmd == "blocks-publish":
        hit = blocks_publish(data, a.version, a.platform)
        for e in hit:
            out(f"{e['id']}\tS1\t{a.version}\t{a.platform}{scope(e)}\t{e['title']}")
        out(f"blocks-publish: {len(hit)} open S1 at {a.version} against the {a.platform} jar: do not upload it"
            if hit else f"blocks-publish: no open S1 at {a.version} against the {a.platform} jar")
        return 1 if hit else 0
    if a.cmd == "reopens":
        sev = a.severity or find(data, a.id)["severity"]
        if reopens(sev):
            out(f"yes: an {sev} at {a.version} reopens it; fix it at its lowest affected version and cascade only that fix")
            return 0
        out(f"no: an {sev} never reopens a done version: record it in the ledger at its lowest affected version "
            f"(known-issues.py add ... --lowest <version>)")
        return 1
    return 2


def fixture_api(fx):
    """A stand-in for gh_api serving a --from-json file: {"issue", "comments", "open", "permissions", "teams"}."""
    def api(path, method="GET", data=None):
        p = path.split("?")[0]
        if re.search(r"/issues$", p):
            return fx.get("open", [fx["issue"]]) if "page=1" in path or "page=" not in path else []
        if re.search(r"/issues/\d+/comments$", p):
            return fx.get("comments", []) if "page=1" in path or "page=" not in path else []
        if re.search(r"/issues/\d+$", p):
            return fx["issue"]
        m = re.search(r"/collaborators/([^/]+)/permission$", p)
        if m:
            return {"permission": fx.get("permissions", {}).get(m.group(1), "none")}
        m = re.search(r"/orgs/([^/]+)/teams/([^/]+)/memberships/([^/]+)$", p)
        if m and m.group(3) in fx.get("teams", {}).get(f"{m.group(1)}/{m.group(2)}", []):
            return {"state": "active"}
        raise Invalid(f"404 {path}")
    return api


def cmd_read_plan(a, out):
    import datetime
    api = gh_api
    if a.from_json:
        try:
            api = fixture_api(json.loads(pathlib.Path(a.from_json).read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError) as e:
            raise Invalid(f"--from-json {a.from_json}: {e}")
    slug = a.slug or slug_of(a.repo or ".")
    if not slug:
        raise Invalid("no --slug and no GitHub origin remote to read it from")
    if a.latest:
        found = open_plan_issues(slug, api)
        if not found:
            raise Invalid(f"{slug} has no open weekly plan issue (the Tuesday workflow opens one; a round with none "
                          "has no frozen input: stop and report)")
        issue = found[0]
    else:
        issue = api(f"/repos/{slug}/issues/{a.issue}")
    n = issue["number"]
    comments = gh_pages(f"/repos/{slug}/issues/{n}/comments", api)
    data = plan_data(issue.get("body") or "")
    owner = resolve_owner(a.owner, None, None) if a.owner else data["owner"]
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    frozen = read_plan(issue, comments, owner_check(slug, owner, api), now)
    text = json.dumps(frozen, indent=1, ensure_ascii=False) + "\n"
    if a.decisions_dir:
        d = pathlib.Path(a.decisions_dir)
        d.mkdir(parents=True, exist_ok=True)
        for f in frozen["forks"]:
            if f["cascade"]:
                p = d / (f["repo"].replace("/", "__") + ".decisions.json")
                p.write_text(json.dumps(f["cascade"], indent=1) + "\n", encoding="utf-8")
                out(f"read-plan: {f['repo']}: {len(f['cascade']['decisions'])} decision(s) -> {p}")
    if a.out:
        pathlib.Path(a.out).write_text(text, encoding="utf-8")
        sel = sum(len(f["selected"]) for f in frozen["forks"])
        out(f"read-plan: #{n} frozen at {now}: {sel} known issue(s) selected, {len(frozen['grades'])} grade "
            f"override(s), {len(frozen['ignored'])} reply line(s) ignored -> {a.out}")
    else:
        out(text, end="")
    return 0


def cmd_render_ports(a, repo, out):
    head = a.head or subprocess.run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True,
                                    text=True, encoding="utf-8").stdout.strip()
    if not head or head == "HEAD":
        raise Invalid("--head: which branch is the head of the chain (e.g. neoforge-1.21.1)?")
    slug = a.slug or slug_of(repo)
    releases = None
    if a.releases:
        releases = json.loads(pathlib.Path(a.releases).read_text(encoding="utf-8"))
    elif a.fetch_releases:
        if not slug:
            raise Invalid("--fetch-releases needs --slug or a GitHub origin remote")
        releases = gh_pages(f"/repos/{slug}/releases")
    fb = None
    try:
        import importlib.util
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("fork_branches_ports",
                                                      pathlib.Path(__file__).resolve().parent / "fork-branches.py")
        fb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fb)
    except (OSError, ImportError):
        fb = None
    rows = ports_rows(repo, head, releases, None if (a.no_done or fb is None) else (lambda v: fb.target_done(repo, v)),
                      fb.version_branches if fb else None)
    block = ports_block(rows, slug, head, a.pinned_issue)
    if not a.write:
        out(block, end="")
        return 0
    p = pathlib.Path(a.readme) if a.readme else repo / "README.md"
    old = ""
    if p.is_file():
        with open(p, encoding="utf-8", newline="") as f:      # newline="": a CRLF README keeps its line ends
            old = f.read()
    new = put_block(old, block)
    if new == old:
        out(f"render-ports: {p} unchanged")
        return 0
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(new)
    out(f"render-ports: wrote the Ports block of {p} ({len(rows)} version(s)); commit it alone (a root README edit "
        "runs no Port CI and stales no gate)")
    return 0


def main(argv=None):
    try:
        return run(sys.argv[1:] if argv is None else argv)
    except Invalid as e:
        print(f"known-issues: {e}", file=sys.stderr)
        return 2


# ---------------------------------------------------------------- self-check (seconds: one tiny git repo)

def gh_glob(pattern):
    """A GitHub Actions path filter as a regex: `**` crosses directories, `*` does not."""
    out = ""
    for tok in re.split(r"(\*\*/?|\*)", pattern):
        out += {"**/": "(?:.*/)?", "**": ".*", "*": "[^/]*"}.get(tok, re.escape(tok))
    return re.compile(f"^{out}$")


def self_check():
    import importlib.util
    import io
    import contextlib
    import time
    t0 = time.monotonic()
    root = pathlib.Path(__file__).resolve().parent.parent
    bad = []

    def expect(c, what):
        if not c:
            bad.append(what)

    def cli(*argv):
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = main(list(argv))
        return rc, buf.getvalue() + err.getvalue()

    def load(name, file):
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location(name, root / "tools" / file)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    # the rubric
    R = lambda f, v="common", r="normal", w=False: {"failure": f, "visibility": v, "reach": r, "workaround": w}
    for f in BLOCKING:
        expect(derive(R(f, "rare", "unusual", True)) == "S1", f"{f} is S1 whatever else")
    expect(derive(R("functional")) == "S2", "a functional failure in normal play, common, no workaround is S2")
    expect(derive(R("functional", w=True)) == "S3", "a workaround makes it S3")
    expect(derive(R("functional", "rare")) == "S3", "rare is S3")
    expect(derive(R("functional", r="feature")) == "S3", "a specific feature only is S3")
    expect(derive(R("cosmetic", "always")) == "S3", "cosmetic is S3 at most")
    expect(derive(R("functional", "invisible")) == "S4" and derive(R("cosmetic", r="unusual")) == "S4", "S4")
    # the spec's own examples: invisible GUI text is a functional failure (S2), a wrong colour tint cosmetic (S3)
    expect(derive(R("functional", "common", "normal", False)) == "S2", "invisible text (functional, common, normal, "
           "no workaround) grades S2")
    expect(derive(R("cosmetic", "common", "normal", False)) == "S3", "a wrong colour tint (cosmetic, common) grades S3")
    expect(derive(R("security", "invisible", "unusual", True)) == "S1", "security (a leaked token) grades S1")
    expect(derive(R("build", "rare", "feature", True)) == "S1", "build (the version does not compile) grades S1")
    expect(reopens("S1") and not any(reopens(s) for s in ("S2", "S3", "S4")), "only S1 reopens a done version")

    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        led = d / ".port" / LEDGER_NAME
        L = ("--repo", str(d))
        rc, o = cli("add", *L, "--title", "client crash opening the config screen", "--failure", "crash",
                    "--visibility", "common", "--reach", "normal", "--workaround", "no", "--lowest", "1.21.4",
                    "--status", "1.21.1=n/a", "--status", "1.21.4=open", "--status", "1.21.5=open", "--source", "gate")
        expect(rc == 0 and "KI-1: S1" in o, f"add an S1: {rc} {o}")
        rc, o = cli("add", *L, "--title", "GUI text invisible", "--failure", "functional", "--visibility", "always",
                    "--reach", "normal", "--workaround", "no", "--lowest", "1.21.5", "--status", "1.21.5=open",
                    "--file", "src/main/java/com/example/mod/Gui.java")
        expect(rc == 0 and "KI-2: S2" in o, f"add an S2: {rc} {o}")
        cli("add", *L, "--title", "S2 lower down", "--failure", "functional", "--visibility", "common", "--reach",
            "normal", "--workaround", "no", "--lowest", "1.21.1", "--status", "1.21.1=open", "--status", "1.21.4=open")
        cli("add", *L, "--title", "a texture seam", "--failure", "cosmetic", "--visibility", "common", "--reach", "normal",
            "--workaround", "no", "--lowest", "1.21.4", "--status", "1.21.4=open", "--file", "src/a/Seam.java")
        cli("add", *L, "--title", "a debug log line", "--failure", "functional", "--visibility", "invisible",
            "--reach", "unusual", "--workaround", "yes", "--lowest", "1.21.4", "--status", "1.21.4=open")
        cli("add", *L, "--title", "another texture", "--failure", "cosmetic", "--visibility", "rare", "--reach", "normal",
            "--workaround", "no", "--lowest", "1.21.5", "--status", "1.21.5=open", "--file", "src/a/Other.java")
        text = led.read_text(encoding="utf-8")
        data = json.loads(text)
        expect(text == dump(data) and text.endswith("}\n") and '\n  "issues": [' in text and text.index('"issues"')
               < text.index('"schema"'), "stable formatting: sorted keys, 2-space indent, a trailing newline")
        expect([e["severity"] for e in data["issues"]] == ["S1", "S2", "S2", "S3", "S4", "S3"], f"grades {data}")
        expect(not list(led.parent.glob(".known-issues.*")), "an atomic write leaves no temporary file")
        # a re-grade, the owner's override recorded, then cleared
        rc, o = cli("grade", *L, "KI-4", "--override", "S2", "--reason", "seen by every player", "--by", "owner")
        e4 = find(read_ledger(led), "KI-4")
        expect(rc == 0 and e4["severity"] == "S2" and e4["override"] == {"severity": "S2", "reason": "seen by every player",
               "by": "owner"} and "rubric S3" in o, f"an override wins and is recorded: {o}")
        rc, o = cli("grade", *L, "KI-4", "--override", "S1")
        expect(rc == 2 and "--reason" in o, f"an override without a reason is refused: {rc} {o}")
        rc, o = cli("grade", *L, "KI-4", "--clear-override")
        expect(rc == 0 and find(read_ledger(led), "KI-4")["severity"] == "S3", f"clear the override: {o}")
        rc, o = cli("grade", *L, "KI-6", "--visibility", "always", "--workaround", "yes")
        expect(rc == 0 and "S3 -> S3" in o, f"a re-grade from new inputs: {o}")
        # status
        before = led.read_text(encoding="utf-8")
        rc, o = cli("status", *L, "KI-1", "--set", "1.21.4=fixed@abcdef1", "--set", "1.21.5=wontfix:the authors' design")
        expect(rc == 0 and "1.21.4=fixed@abcdef1" in o, f"status: {o}")
        after = led.read_text(encoding="utf-8")
        import difflib
        changed = [x for x in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0)
                   if x[:1] in "+-" and not x.startswith(("+++", "---"))]
        expect(len(changed) == 4, f"a status change is a small diff (2 lines out, 2 in): {changed}")
        rc, o = cli("status", *L, "KI-1", "--set", "1.21.4=fixed")
        expect(rc == 2 and "fixed@<commit>" in o and led.read_text(encoding="utf-8") == after,
               f"a bad state exits 2 and writes nothing: {rc} {o}")
        cli("status", *L, "KI-1", "--set", "1.21.4=open", "--set", "1.21.5=open")
        # list
        rc, o = cli("list", *L, "--version", "1.21.5", "--min", "S2")
        expect(rc == 0 and [x.split("\t")[0] for x in o.splitlines()] == ["KI-1", "KI-2"], f"list --version --min: {o}")
        rc, o = cli("list", *L, "--severity", "S3,S4", "--json")
        expect(rc == 0 and [x["id"] for x in json.loads(o)] == ["KI-4", "KI-6", "KI-5"], f"list --severity: {o}")
        # select: every S1, S2 lowest version first up to the budget, S3 only in touched files, the owner's changes
        rc, o = cli("select", *L, "--budget", "1", "--touched", "src/a/Seam.java")
        ids = [x.split("\t")[0] for x in o.splitlines()]
        expect(rc == 0 and ids == ["KI-1", "KI-3", "KI-4"], f"select: S1, the lowest S2 within budget 1, a touched S3: {o}")
        rc, o = cli("select", *L, "--budget", "1", "--add", "KI-5", "--remove", "KI-3", "--json")
        sel = json.loads(o)
        expect(rc == 0 and [x["id"] for x in sel] == ["KI-1", "KI-5"] and sel[1]["why"] == "added by the owner",
               f"select with the owner's add and remove: {o}")
        rc, o = cli("select", *L, "--version", "1.21.5", "--budget", "5")
        expect(rc == 0 and [x.split("\t")[0] for x in o.splitlines()] == ["KI-1", "KI-2"],
               f"select limited to the run's versions: {o}")
        rc, o = cli("select", *L, "--add", "KI-99")
        expect(rc == 2, f"an unknown id exits 2: {o}")
        # only S1 reopens a done version; an S2 found lower goes to the ledger
        rc, o = cli("reopens", *L, "--version", "1.21.4", "--id", "KI-1")
        expect(rc == 0 and o.startswith("yes"), f"an S1 reopens: {o}")
        rc, o = cli("reopens", *L, "--version", "1.21.1", "--severity", "S2")
        expect(rc == 1 and o.startswith("no") and "ledger" in o, f"an S2 at a done version does not reopen it: {o}")
        # the pinned issue
        rc, o = cli("render-issue", *L)
        rows = [x for x in o.splitlines() if re.match(r"^\| S\d", x)]
        expect(rc == 0 and "| Sev | ID | Issue | 1.21.1 | 1.21.4 | 1.21.5 |" in o and [r.split(" | ")[1] for r in rows]
               == ["KI-1", "KI-3", "KI-2", "KI-4", "KI-6", "KI-5"] and "do not edit by hand" in o
               and "| S1 | KI-1 | client crash opening the config screen | n/a | **open** | **open** |" in o,
               f"render-issue: issue by version, sorted by severity then lowest version: {o}")
        # a ledger read from the head ref, and a release tag that does or does not hold a fix
        g = lambda *a: subprocess.run(["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                                       *a], capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()
        g("init", "-q", "-b", "neoforge-1.21.1")
        (d / "f.txt").write_text("1\n", encoding="utf-8")
        g("add", "f.txt"); g("commit", "-qm", "port")
        g("tag", "examplemod-1.0-mc1.21.5")
        (d / "f.txt").write_text("2\n", encoding="utf-8")
        g("commit", "-qam", "fix")
        fix = g("rev-parse", "--short", "HEAD")
        rc, o = cli("status", *L, "KI-2", "--set", f"1.21.5=fixed@{fix}")
        g("add", "-A"); g("commit", "-qm", "ledger")
        rc, o = cli("list", "--repo", str(d), "--ref", "neoforge-1.21.1", "--ledger", str(d / "nowhere.json"), "--json")
        expect(rc == 0 and len(json.loads(o)) == 6, f"read from the head ref (git show), not the file: {o}")
        rc, o = cli("add", "--repo", str(d), "--ref", "neoforge-1.21.1", "--title", "x", "--failure", "hang",
                    "--visibility", "rare", "--reach", "normal", "--workaround", "no", "--lowest", "1.21.1")
        expect(rc == 2 and "read-only" in o, f"a write through --ref is refused: {o}")
        rc, o = cli("list", "--repo", str(d), "--ref", "examplemod-1.0-mc1.21.5", "--json")
        expect(rc == 0 and json.loads(o) == [], f"a ref without a ledger reads as empty: {o}")
        rc, o = cli("list", "--repo", str(d), "--ref", "no-such-ref")
        expect(rc == 2, f"an unknown ref exits 2: {o}")
        rc, o = cli("release-notes", *L, "--version", "1.21.5", "--tag", "examplemod-1.0-mc1.21.5")
        expect(rc == 0 and "**S1** client crash" in o and "**S2** GUI text invisible (KI-2) (fixed after this release)" in o
               and "another texture" in o and "debug log" not in o, f"release notes: S1-S3 open at the tag, S4 omitted: {o}")
        rc, o = cli("release-notes", *L, "--version", "1.21.5", "--tag", "HEAD", "--min", "S1")
        expect(rc == 0 and "GUI text" not in o and "client crash" in o and "another texture" not in o,
               f"release notes at a tag holding the fix, threshold S1: {o}")
        rc, o = cli("release-notes", *L, "--version", "1.21.1", "--tag", "t", "--min", "S1")
        expect(rc == 0 and "No known issues at S1 or above." in o, f"no issues: {o}")
        # per-platform scope: a cf-only S1 blocks the cf upload, not mr; mr's notes omit it
        rc, o = cli("add", *L, "--title", "crash on the CurseForge jar only", "--failure", "crash", "--visibility",
                    "common", "--reach", "normal", "--workaround", "no", "--lowest", "1.21.8", "--platform", "cf")
        expect(rc == 0 and find(read_ledger(led), "KI-7")["platforms"] == ["cf"], f"add --platform cf: {o}")
        rc, o = cli("blocks-publish", *L, "--version", "1.21.8", "--platform", "cf")
        expect(rc == 1 and "KI-7" in o and "do not upload" in o, f"a cf-only S1 blocks cf: {rc} {o}")
        rc, o = cli("blocks-publish", *L, "--version", "1.21.8", "--platform", "mr")
        expect(rc == 0 and "no open S1" in o, f"... and not mr: {rc} {o}")
        rc, o = cli("blocks-publish", *L, "--version", "1.21.5", "--platform", "mr")
        expect(rc == 1 and "KI-1" in o, f"an S1 with no platforms blocks every platform: {rc} {o}")
        rc, o = cli("release-notes", *L, "--version", "1.21.8", "--tag", "t", "--platform", "mr")
        expect(rc == 0 and "CurseForge jar only" not in o and "No known issues" in o, f"mr's notes omit a cf-only issue: {o}")
        rc, o = cli("release-notes", *L, "--version", "1.21.8", "--tag", "t", "--platform", "cf")
        expect(rc == 0 and "**S1** crash on the CurseForge jar only (KI-7)" in o, f"cf's notes list it: {o}")
        rc, o = cli("release-notes", *L, "--version", "1.21.8", "--tag", "t")
        expect(rc == 0 and "(KI-7) (cf only)" in o, f"notes for every platform name the scope: {o}")
        rc, o = cli("list", *L, "--platform", "mr", "--json")
        expect(rc == 0 and "KI-7" not in [x["id"] for x in json.loads(o)] and len(json.loads(o)) == 6, f"list --platform: {o}")
        rc, o = cli("reopens", *L, "--version", "1.21.8", "--id", "KI-7")
        expect(rc == 0, f"reopens is unchanged by platforms: {o}")
        cli("status", *L, "KI-7", "--set", "1.21.8=fixed@abcdef1")
        # a multi-mod repository: one ledger per mod
        rc, o = cli("add", *L, "--mod", "examplemod", "--title", "t", "--failure", "kick", "--visibility", "rare",
                    "--reach", "normal", "--workaround", "no", "--lowest", "1.21.1")
        expect(rc == 0 and (d / ".port/examplemod" / LEDGER_NAME).is_file(), f"--mod writes .port/<mod>/: {o}")
        expect(cli("list", *L, "--mod", "../x")[0] == 2, "a bad --mod exits 2")
        # validation: a bad entry exits 2
        good = json.loads(led.read_text(encoding="utf-8"))
        cases = [
            (lambda x: x.update(schema=2), "schema"),
            (lambda x: x["issues"][0].update(severity="S3"), "re-grade"),
            (lambda x: x["issues"][0]["rubric"].update(failure="boom"), "failure"),
            (lambda x: x["issues"][0]["status"].update({"1.21.9": "fixed@zz"}), "fixed@<commit>"),
            (lambda x: x["issues"][0]["status"].update({"1.21.9": "wontfix:"}), "wontfix:<reason>"),
            (lambda x: x["issues"][0].update(lowest="1.21.5"), "lowest must be the lowest"),
            (lambda x: x["issues"][0].update(lowest="1.21.1"), "affected (not n/a)"),
            (lambda x: x["issues"][1].update(id=x["issues"][0]["id"]), "duplicate"),
            (lambda x: x["issues"][0].update(extra=1), "unknown key"),
            (lambda x: x["issues"][0].update(override={"severity": "S2"}), "override"),
            (lambda x: x["issues"][0].update(source="forum"), "source"),
            (lambda x: x["issues"][0].update(platforms=["gh"]), "platforms"),
            (lambda x: x["issues"][0].update(platforms=[]), "platforms"),
        ]
        for mutate, why in cases:
            x = json.loads(json.dumps(good))
            mutate(x)
            p = d / "bad.json"
            p.write_text(json.dumps(x), encoding="utf-8")
            rc, o = cli("list", "--ledger", str(p))
            expect(rc == 2 and why in o, f"a bad entry exits 2 ({why}): {rc} {o}")
        (d / "bad.json").write_text("{", encoding="utf-8")
        expect(cli("render-issue", "--ledger", str(d / "bad.json"))[0] == 2, "not JSON exits 2")

        # the ledger never makes gates or a review stale, and the cascade never carries it -- the other tools' own
        # lists, so only where they are (the Port CI kit ships this file without them: its verify runs the rest)
        others = ("ci-gates.py", "fork-port.py", "layer-deps.py", "port-publish.py", "fork-branches.py")
        if not all((root / "tools" / f).is_file() for f in others):
            print("self-check: the cross-tool checks need the whole repository: skipped (a Port CI kit)")
        else:
            # layer target (--mc) and a single-version fork (no --mc: HEAD, as before -- byte-for-byte)
            cg = load("ci_gates_ki", "ci-gates.py")
            (d / ".port" / LEDGER_NAME).write_text(dump(empty()), encoding="utf-8")         # an edit to the ledger alone
            head = g("rev-parse", "--short", "HEAD")
            expect(cg.tested_tree(d, "1.21.5") == head, "ci-gates: a ledger edit does not make a layer target's tree edited")
            expect(cg.tested_tree(d, None) == head, "ci-gates: a single-version fork's tested tree is HEAD, as before")
            (d / "README.md").write_text("# a generated Ports block\n", encoding="utf-8")       # a root README edit
            g("add", "README.md")
            expect(cg.tested_tree(d, "1.21.5") == head, "ci-gates: a root README edit does not make the tree edited")
            res = d / "src/main/resources"
            res.mkdir(parents=True)
            (res / "README.md").write_text("in the jar\n", encoding="utf-8")
            g("add", "src")
            expect("uncommitted: src/main/resources/README.md" in cg.tested_tree(d, "1.21.5"),
                   "control: a README under src/main/resources is jar content and still counts")
            (d / "f.txt").write_text("3\n", encoding="utf-8")
            expect("f.txt" in cg.tested_tree(d, "1.21.5"), "control: a code edit still counts")
            fpm = load("fork_port_ki", "fork-port.py")
            expect(LEDGER_NAME in fpm.REVIEW_OUTPUTS and LEDGER_NAME in fpm.NOT_JAR_FILES, "fork-port: a run output")
            expect("README.md" in fpm.ROOT_RECORDS and "README.md" not in fpm.NOT_JAR_FILES,
                   "fork-port: the root README is a record by path, not by name (a README in src/ stays jar content)")
            ldm = load("layer_deps_ki", "layer-deps.py")
            expect(ldm._not_jar("README.md") and ldm._not_jar(".port/KNOWN-ISSUES.json")
                   and not ldm._not_jar("src/main/resources/README.md"), "layer-deps: the root README is not jar content")
            pp = load("port_publish_ki", "port-publish.py")
            expect(all(pp.GATES_DOC.match(f) for f in (".port/KNOWN-ISSUES.json", ".port/examplemod/KNOWN-ISSUES.json"))
                   and pp.GATES_DOC.match("README.md")
                   and not any(pp.GATES_DOC.match(f) for f in ("src/main/resources/KNOWN-ISSUES.json", ".port/a/b/KNOWN-ISSUES.json",
                                                              "src/main/resources/README.md",
                                                              ".port/1.21.4/renames.tsv", "build.gradle")),
                   "port-publish: the ledger and the root README are not jar content (a README in src/ is)")
            green, why = pp.listed_green("1.21.4", "# Gates at `abc1234` for Minecraft 1.21.4\n| A | PASS |\n",
                                         [".port/KNOWN-ISSUES.json", "README.md"], ["1.21.4"])
            expect(green, f"a released layer target stays green after a ledger and README commit: {why}")
            green, why = pp.listed_green("1.21.4", "# Gates at `abc1234` for Minecraft 1.21.4\n| A | PASS |\n",
                                         ["src/main/resources/README.md"], ["1.21.4"])
            expect(not green, "control: a README under src/main/resources makes the gates stale")
            fb = load("fork_branches_ki", "fork-branches.py")
            expect(all(fb.LEDGER_PATH.match(f) and not fb.jar_relevant(f, "1.21.4")
                       for f in (".port/KNOWN-ISSUES.json", ".port/examplemod/KNOWN-ISSUES.json"))
                   and not fb.LEDGER_PATH.match(".port/1.21.4/branch.properties")
                   and not fb.jar_relevant("README.md", "1.21.4") and fb.jar_relevant("src/main/resources/README.md", "1.21.4"),
                   "fork-branches: the ledger is no version branch's jar input, and the cascade skips it (LEDGER_PATH)")
            tpl = (root / "templates/upstream-harness/port-ci.yml").read_text(encoding="utf-8")
            push = tpl[tpl.index("  push:"):tpl.index("  pull_request:")]
            m = re.search(r"paths-ignore: (\[.*\])", push)
            ignore = json.loads(m.group(1)) if m else []
            expect(ignore == [".port/KNOWN-ISSUES.json", ".port/*/KNOWN-ISSUES.json", "README.md"],
                   f"Port CI's push trigger paths-ignores the ledgers and the root README: {ignore}")
            ignored = lambda f: any(gh_glob(p).match(f) for p in ignore)
            expect(all(ignored(f) for f in (".port/KNOWN-ISSUES.json", ".port/examplemod/KNOWN-ISSUES.json", "README.md"))
                   and not any(ignored(f) for f in ("src/main/resources/README.md", "docs/README.md", "build.gradle",
                                                   ".port/a/b/KNOWN-ISSUES.json")),
                   "paths-ignore matches the root README and the ledgers only (GitHub's glob: * stops at /)")
    plan_check(expect, cli)
    took = time.monotonic() - t0
    for b in bad:
        print("FAIL:", b)
    print(f"self-check: {'OK' if not bad else 'FAIL'} ({took:.1f} s)")
    return 0 if not bad else 1


def plan_check(expect, cli):
    """render-plan / read-plan / render-ports on invented data: no network, no git beyond one tiny repo."""
    dec = {"id": "d1e2f3a4b5c6", "file": "src/main/java/com/example/mod/A.java", "versions": ["1.21.4", "1.21.5"],
           "escalate": True, "choice": "keep", "reasoning": "over the budget."}
    dflt = {"id": "a1b2c3d4e5f6", "file": "src/main/java/com/example/mod/B.java", "versions": ["1.21.4"],
            "escalate": False, "choice": "take", "reasoning": "the authors' fix."}
    sel = [{"id": "KI-1", "severity": "S1", "lowest": "1.21.4", "why": "S1: always taken", "title": "a crash | on load"}]
    inp = {"repo": "o/lib", "week": "2026-10-20", "deadline": "2026-10-21T08:21-07:00",
           "deadline_text": "Wednesday 2026-10-21 at 08:21 America/Los_Angeles (PDT)", "owner": {"team": "o/maintainers"},
           "forks": [{"repo": "o/lib", "head": "neoforge-1.21.1", "role": "library", "upstream": "https://example.invalid/a",
                      "upstream_branch": "main", "upstream_commit": "c" * 40, "head_commit": "d" * 40, "new_count": 0,
                      "cascade": {"range": "neoforge-1.21.1..1.21.5", "plan": {"mechanical": [{}], "decisions": [dec, dflt]}},
                      "selection": sel, "candidates": [{"id": "KI-2", "severity": "S2", "lowest": "1.21.5", "title": "t"}],
                      "ledger_ids": ["KI-1", "KI-2", "KI-3"]},
                     {"repo": "o/dep", "head": "neoforge-1.21.1", "role": "dependent", "upstream": "https://example.invalid/b",
                      "upstream_branch": "dev", "upstream_commit": "e" * 40, "new_count": 25,
                      "new_commits": [{"sha": f"{n:040x}", "subject": f"change {n}"} for n in range(25)], "cascade": None,
                      "selection": [], "candidates": [{"id": "KI-1", "severity": "S3", "lowest": "1.21.1", "title": "u"}]}]}
    res = render_plan(inp)
    b = res["body"]
    expect(res["title"].startswith("[decision needed] Weekly plan 2026-10-20") and res["decision_needed"],
           f"an escalated decision: the title starts [decision needed]: {res['title']}")
    expect(b.split("\n")[1].startswith("**Deadline: the round starts Wednesday 2026-10-21") and "defaults below apply" in
           b.split("\n")[1] and "@o/maintainers" in b, "the deadline line first, then the owner (a team) @mentioned")
    expect("- [x] **S1** `o/lib:KI-1` a crash \\| on load" in b and "- [ ] **S3** `o/dep:KI-1`" in b
           and "`/decide d1e2f3a4b5c6 <choice>`" in b and "`a1b2c3d4e5f6`" in b and "- ... and 5 more" in b
           and "<!-- authors:o/dep default -->" in b and "authors:o/lib" not in b and "single-version fork" in b,
           f"checkboxes pre-ticked for the defaults, ids two forks share qualified, decisions, commits: {b}")
    ok_users = {"owner-member", "writer"}
    cm = lambda n, who, text: {"id": n, "created_at": f"2026-10-20T1{n}:00:00Z", "user": {"login": who}, "body": text}
    ticked = b.replace("- [ ] **S2** `KI-2`", "- [x] **S2** `KI-2`").replace("- [x] Take the authors' new commits this "
                                                                            "round <!-- authors:o/dep", "- [ ] Take the "
                                                                            "authors' new commits this round <!-- authors:o/dep")
    fx = {"issue": {"number": 7, "body": ticked}, "permissions": {"writer": "write", "passerby": "read"},
          "teams": {"o/maintainers": ["owner-member"]},
          "comments": [cm(1, "passerby", "/decide d1e2f3a4b5c6 take"),
                       cm(2, "owner-member", "please\n/decide d1e2f3a4b5c6 take they fixed it\n/grade KI-1 S2"),
                       cm(3, "writer", "/grade o/lib:KI-1 s2 seen once\n/decide d1e2f3a4b5c6 maybe\n/grade KI-9 S1"),
                       cm(4, BOT, "/decide d1e2f3a4b5c6 keep")]}
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        (d / "fx.json").write_text(json.dumps(fx), encoding="utf-8")
        rc, o = cli("read-plan", "--issue", "7", "--slug", "o/lib", "--from-json", str(d / "fx.json"), "--out",
                    str(d / "frozen.json"), "--decisions-dir", str(d / "dec"))
        fr = json.loads((d / "frozen.json").read_text(encoding="utf-8")) if rc == 0 else {}
        lib = (fr.get("forks") or [{}])[0]
        dep = (fr.get("forks") or [{}, {}])[1]
        expect(rc == 0 and lib.get("selected") == ["KI-1", "KI-2"] and lib.get("added") == ["KI-2"] and dep.get("selected") == []
               and dep.get("take_authors") is False and lib.get("take_authors") is False, f"read-plan: the ticks: {o} {fr}")
        d1 = [x for x in fr.get("decisions", []) if x["id"] == "d1e2f3a4b5c6"]
        d2 = [x for x in fr.get("decisions", []) if x["id"] == "a1b2c3d4e5f6"]
        expect(d1 and d1[0]["choice"] == "take" and d1[0]["by"] == "owner-member" and d1[0]["reason"] == "they fixed it"
               and d2 and d2[0]["choice"] == "take" and d2[0]["by"] == "default",
               f"/decide from a member of the owner team counts; no answer keeps the default: {fr.get('decisions')}")
        expect([(g["id"], g["severity"], g["by"]) for g in fr.get("grades", [])] == [("KI-1", "S2", "writer")],
               f"/grade: an id two forks share needs <owner/repo>:, a writer's counts: {fr.get('grades')}")
        why = sorted(x["why"].split(":")[0].split(" ")[0] for x in fr.get("ignored", []))
        expect(len(fr.get("ignored", [])) == 4 and any(x["by"] == "passerby" and "write access" in x["why"]
                                                        for x in fr["ignored"]), f"drive-by and bad replies ignored: {fr.get('ignored')} {why}")
        df = d / "dec" / "o__lib.decisions.json"
        got = json.loads(df.read_text(encoding="utf-8")) if df.is_file() else {}
        expect(got.get("range") == "neoforge-1.21.1..1.21.5" and [x["choice"] for x in got.get("decisions", [])] ==
               ["take", "take"] and not (d / "dec" / "o__dep.decisions.json").exists(),
               f"--decisions-dir: one cascade --apply-decisions file per version-branch fork: {got}")
        fx2 = dict(fx, issue={"number": 7, "body": "no plan here"})
        (d / "fx2.json").write_text(json.dumps(fx2), encoding="utf-8")
        expect(cli("read-plan", "--issue", "7", "--slug", "o/lib", "--from-json", str(d / "fx2.json"))[0] == 2,
               "an issue that is not a plan exits 2")
        (d / "fx3.json").write_text(json.dumps(dict(fx, open=[])), encoding="utf-8")
        rc, o = cli("read-plan", "--latest", "--slug", "o/lib", "--from-json", str(d / "fx3.json"))
        expect(rc == 2 and "no open weekly plan issue" in o, f"--latest with none open stops: {o}")
        # the README Ports block: rows from the tags and the releases, only the marked block ever changes
        g = lambda *a: subprocess.run(["git", "-C", str(d / "r"), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                                       *a], capture_output=True, text=True, encoding="utf-8", check=True)
        (d / "r").mkdir()
        g("init", "-q", "-b", "neoforge-1.21.1")
        (d / "r/gradle.properties").write_text("minecraft_version=1.21.1\n", encoding="utf-8")
        g("add", "-A"); g("commit", "-qm", "port")
        for t in ("examplemod-1.0-mc1.21.1", "examplemod-1.1-mc1.21.1", "examplemod-1.1-mc1.21.10", "examplemod-1.1-mc1.21.4-r2"):
            g("tag", t)
        rel = [{"tag_name": "examplemod-1.0-mc1.21.1", "prerelease": False, "published_at": "2026-01-01"},
               {"tag_name": "examplemod-1.1-mc1.21.1", "prerelease": True, "published_at": "2026-02-01"},
               {"tag_name": "examplemod-1.2-mc1.21.1", "draft": True, "published_at": "2026-03-01"}]
        rows = ports_rows(d / "r", "neoforge-1.21.1", rel, lambda v: [] if v == "1.21.4" else ["no Gate C"],
                          lambda repo: {"1.21.5": "x", "1.21.4": "y"})
        expect(rows == [("1.21.1", "neoforge-1.21.1", "examplemod-1.1-mc1.21.1", "pre-release", "head"),
                        ("1.21.4", "neoforge-1.21.4", None, None, "done"),
                        ("1.21.5", "neoforge-1.21.5", None, None, "not done")], f"rows from the releases: {rows}")
        rows = ports_rows(d / "r", "neoforge-1.21.1", None, None, lambda repo: {"1.21.4": "y"})
        expect(rows[1][2:4] == ("examplemod-1.1-mc1.21.4-r2", "tag") and rows[1][4] == "-", f"rows from the tags: {rows}")
        block = ports_block(rows, "o/lib", "neoforge-1.21.1", 3)
        expect(block.startswith(PORTS_BEGIN) and block.endswith(PORTS_END + "\n")
               and "](https://github.com/o/lib/blob/neoforge-1.21.1/.port/KNOWN-ISSUES.json)" in block
               and "(https://github.com/o/lib/issues/3)" in block, f"the block: {block}")
        authors = "# Example mod\r\n\r\nThe authors' text.\r\n"
        once = put_block(authors, block)
        twice = put_block(once.replace("1.21.4", "1.21.9"), block)
        mid = "Intro\n" + block + "Outro\n"
        expect(once.startswith(authors) and twice == once and put_block(mid, block.replace("Ports", "P")) ==
               "Intro\n" + block.replace("Ports", "P") + "Outro\n", "only the text between the markers ever changes")
        try:
            put_block("x\n" + PORTS_BEGIN + "\n", block); expect(False, "a README with one marker is refused")
        except Invalid:
            pass
        (d / "r/README.md").write_bytes(authors.encode("utf-8"))
        rc, o = cli("render-ports", "--repo", str(d / "r"), "--head", "neoforge-1.21.1", "--slug", "o/lib", "--write", "--no-done")
        txt = (d / "r/README.md").read_bytes().decode("utf-8")
        expect(rc == 0 and txt.startswith(authors) and PORTS_END in txt, f"render-ports --write keeps the CRLF text: {o}")
        rc, o = cli("render-ports", "--repo", str(d / "r"), "--head", "neoforge-1.21.1", "--slug", "o/lib", "--write", "--no-done")
        expect(rc == 0 and "unchanged" in o, f"a second write changes nothing: {o}")


if __name__ == "__main__":
    sys.exit(main())
