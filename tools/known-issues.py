#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The known-issues ledger of a fork port: every known defect, graded S1-S4 by a fixed rubric, with its status at
each Minecraft version (issue #135, part 1).

    python3 tools/known-issues.py add     [LEDGER] --title T --failure F --visibility V --reach R --workaround yes|no
                                          --lowest MC [--status MC=STATE ...] [--id ID] [--source S] [--notes N]
                                          [--file PATH ...] [--pattern REGEX] [--platform mr|cf ...]
    python3 tools/known-issues.py grade   [LEDGER] ID [--failure F] [--visibility V] [--reach R] [--workaround yes|no]
                                          [--override S --reason WHY [--by WHO] | --clear-override]
    python3 tools/known-issues.py status  [LEDGER] ID [--set MC[,MC...|FROM..TO]=STATE ...] [--lowest MC] [--pattern REGEX]
    python3 tools/known-issues.py lowest  [LEDGER] [ID ...] [--at MC] [--pattern REGEX] [--json]
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
                          "reach": "normal|feature|unusual|latent", "workaround": false},
               "severity": "S1".."S4",          # the rubric's grade, or the override's when there is one
               "override": null | {"severity": "S2", "reason": "...", "by": "owner"},
               "lowest": "1.21.1",              # the lowest affected Minecraft version (a key of status)
               "status": {"1.21.1": "open", "1.21.3": "fixed@<commit>", "1.21.4": "wontfix:<reason>", "1.21.5": "n/a",
                          "1.21.8": "duplicate:KI-2"},     # tracked by that issue at this version: not open here
               "source": "authors|review|gate|player",
               "files": ["src/main/java/..."],  # optional: the files the defect is in (select's S3 rule)
               "pattern": "textures/block/x",   # optional: an extended regex (git grep -E) whose match in `files` (or
                                                # anywhere, with no files) is EVIDENCE the defect may be there, never
                                                # proof (the same text can be right at another version): `lowest`
                                                # reports a hit as a candidate a person or agent confirms
               "platforms": ["cf"],             # optional: only that platform's jar (mr Modrinth, cf CurseForge);
                                                # absent: every platform
               "notes": "..."}]}
  A version missing from status is not affected. Which releases contain an issue is never stored: release-notes works
  it out from the tag (with --repo, a fixed@<commit> that is not in the tag counts as open there).

THE RUBRIC (grade derives the severity from the recorded inputs; an owner override wins and is recorded):
  S1  failure crash, kick (or network disconnect), data (world or player data lost or corrupted), hang, security
      (an exposed credential or secret, a token leak, an exploit or privilege path) or build (the version does not
      compile or produce a jar) -- whatever else. It blocks publishing (publish-port refuses an open S1; for an issue
      scoped to one platform, that platform's upload only: `blocks-publish`). Reach latent (nothing calls the code
      today, so no player can hit it yet) makes one of these S2 instead: taken by the next rounds within their budget,
      never parked, and not blocking publishing. An override never grades one below S2 without the owner (`--owner`:
      the owner's `/grade` on the plan issue, which the round applies).
  S4  otherwise: visibility invisible, or reach unusual (an unusual setup, other mods, debug options) or latent.
  S2  otherwise: a functional failure with reach normal, visibility always or common and no workaround.
  S3  everything else: cosmetic, rare, a specific feature only, or a workaround exists. Cosmetic is S3 at most.
  The failure types beside the six S1 ones:
    functional  a feature does not work, or information or controls the player needs are missing, unreadable or
                unusable -- invisible GUI text and a missing UI element are functional;
    cosmetic    the look is wrong, but nothing the player needs is lost (a wrong colour tint, a texture seam).

LOWEST (read-only; the plan and the round run it before a fix): is the defect also at a version BELOW the one it is to
be fixed at (its lowest open version, or --at)? It finds candidates, never proof: Only the fork's `neoforge-<mc>` branches below it whose status the
ledger does not set are looked at (a version set n/a, fixed, wontfix or duplicate is trusted), by git alone on the refs
(origin's, else the local branch; no checkout). With a `pattern` (or --pattern): `git grep -E` in `files` on each such
branch -- a match is a candidate (`found`: "may be at <v> (unconfirmed)"); nothing moves a fix on a hit alone (the
plan's fix_at stays; the round's `lowest` job confirms or rejects it). With no pattern: each branch
holding a listed file byte-identical to the fix version's is `unchanged` ("lowest may be lower (unchecked)"), one
holding it changed is `present`; a branch with none of the files cannot have the defect in them. With no files and no
pattern every such branch is `unchecked`. A single-version fork has no branch below its one version: nothing to check.

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
               their options (`/decide <id> <choice>`), the dependency updates (tools/weekly-plan.py deps: per
               Minecraft version, each pinned dependency with a newer compatible build, ticked by default, unticked
               with the reason for an alpha/beta, a major-version jump, a dropped Minecraft version or a provider
               switch; a failed lookup listed as "could not check"), and grade overrides (`/grade <ID> <S1-S4> [reason]`). The
               title starts `[decision needed]` when the plan escalates something. A previous plan issue no round
               closed is carried in: its ticks and replies, with "last week's round did not run".
  read-plan    the round's first step: the issue's checkboxes and the `/decide` and `/grade` replies, frozen as JSON.
               Only replies from the resolved owner (a user, or a member of the owner team) or from users with write
               access count; every other comment is listed under `ignored`. A checkbox is part of the issue body, which
               only the bot and users with write access can edit. Each fork's `cascade` block is what
               `fork-branches.py cascade --apply-decisions` reads (--decisions-dir writes one file per fork).
               A version-branch fork's entry also says whether the head is cascaded (`head_cascade`: false when the
               plan does not take the authors' code the head has -- new or untaken -- and then no decision applies and
               no decisions file is written) and, per selected issue, `fixes`: the lowest version still open (the
               status map, not `lowest`) and the one cascade that carries the fix. Every fork whose plan has the
               dependency section gets `dep_bumps` (the ticked ones, each with its `layer-deps.py resolve --bump`
               command; the round applies them once at setup) and `dep_kept`. Run before the plan's deadline it
               warns (the owner's window is cut short); --now records an early start the owner asked for.
  render-ports the fork README's Ports block, between `<!-- ports:begin -->` and `<!-- ports:end -->`: one row per
               Minecraft version (branch, newest pre-release or release, done state) and the known-issues links. With
               --write it replaces only the marked block (appended at the end when the README has none); every byte
               outside the markers stays as it was.
  The GitHub API is read with GITHUB_TOKEN / GH_TOKEN when set, else with the `gh` command line.

Exit codes: 0 ok (reopens: it reopens; blocks-publish: nothing blocks); 1 reopens: it does not reopen;
blocks-publish: an open S1 applies to that platform; 2 bad input or an invalid ledger.
"""
import argparse
import collections
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
REACH = ("normal", "feature", "unusual", "latent")
SOURCES = ("authors", "review", "gate", "player")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MOD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MC_RE = re.compile(r"^\d+(?:\.\d+){1,3}$")
STATE_RE = re.compile(r"^(?:open|n/a|fixed@[0-9a-f]{7,40}|wontfix:\S.*|duplicate:[A-Za-z0-9][A-Za-z0-9._-]{0,63})$")
DEFAULT_BUDGET = 3
TOP_KEYS = {"schema", "issues"}
ISSUE_KEYS = {"id", "title", "rubric", "severity", "override", "lowest", "status", "source", "files", "pattern",
              "platforms", "notes"}


class Invalid(Exception):
    pass


def mc_key(v):
    return tuple(int(x) for x in v.split("."))


def sev_rank(s):
    return SEVERITIES.index(s)


def derive(rubric):
    """The rubric's severity for its recorded inputs."""
    if rubric["failure"] in BLOCKING:
        return "S2" if rubric["reach"] == "latent" else "S1"
    if rubric["visibility"] == "invisible" or rubric["reach"] in ("unusual", "latent"):
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
                raise Invalid(f"{where}: status {v}={s!r} is not open, fixed@<commit>, wontfix:<reason>, "
                              "duplicate:<ID> or n/a")
        lo = e["lowest"]
        if not isinstance(lo, str) or not MC_RE.match(lo) or st.get(lo) in (None, "n/a"):
            raise Invalid(f"{where}: lowest {lo!r} must be a version in status that is affected (not n/a)")
        if any(mc_key(v) < mc_key(lo) and s != "n/a" for v, s in st.items()):
            raise Invalid(f"{where}: a version below lowest {lo} is affected: lowest must be the lowest")
        if e["source"] not in SOURCES:
            raise Invalid(f"{where}: source {e['source']!r} is not one of {', '.join(SOURCES)}")
        if "files" in e and (not isinstance(e["files"], list) or not all(isinstance(f, str) and f for f in e["files"])):
            raise Invalid(f"{where}: files must be a list of paths")
        if "pattern" in e and (not isinstance(e["pattern"], str) or not e["pattern"].strip() or "\n" in e["pattern"]):
            raise Invalid(f"{where}: pattern must be one line of text (an extended regex, git grep -E)")
        pl = e.get("platforms", PLATFORMS)
        if "platforms" in e and (not isinstance(pl, list) or not pl or len(set(pl)) != len(pl)
                                 or not set(pl) <= set(PLATFORMS)):
            raise Invalid(f"{where}: platforms must be a non-empty list of {', '.join(PLATFORMS)} (absent: all)")
        if "notes" in e and not isinstance(e["notes"], str):
            raise Invalid(f"{where}: notes must be text")
    for e in issues:              # duplicate:<ID> names another issue of this ledger
        for v, s in e["status"].items():
            if s.startswith("duplicate:") and (s[10:] == e["id"] or s[10:] not in seen):
                raise Invalid(f"issue {e['id']}: status {v}={s!r}: duplicate:<ID> must name another issue of this ledger")
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


def version_refs(repo):
    """{mc: ref} of the fork's `neoforge-<mc>` branches: origin's (what is pushed), else the local branch."""
    r = subprocess.run(["git", "-C", str(repo), "for-each-ref", "--format=%(refname)", "refs/heads/neoforge-*",
                        "refs/remotes/origin/neoforge-*"], capture_output=True, text=True, encoding="utf-8")
    out = {}
    for ref in sorted(r.stdout.split(), key=lambda x: x.startswith("refs/remotes/")):     # origin's last: it wins
        v = ref.rsplit("/neoforge-", 1)[-1]
        if MC_RE.match(v):
            out[v] = ref
    return out


def _tree(repo, ref, cache):
    """{path: blob} of a ref's whole tree, read once per ref."""
    if ref not in cache:
        r = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "-z", ref], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        cache[ref] = {ln.split("\t", 1)[1]: ln.split()[2] for ln in r.stdout.split("\0") if "\t" in ln} if not r.returncode else {}
    return cache[ref]


def lowest_check(repo, e, at=None, refs=None, pattern=None, cache=None):
    """Is the defect also below the version it is to be fixed at (LOWEST in the module text)? Read-only, git alone.
    -> {"id", "at", "below": [versions looked at], "how": "pattern"|"files"|None, "found": [...], "unchanged": [...],
    "present": [...], "unchecked": [...], "lowest": the lowest found (else at), "error": None or why}."""
    refs = version_refs(repo) if refs is None else refs
    cache = {} if cache is None else cache
    ov = open_versions(e)
    at = at or (ov[0] if ov else e["lowest"])
    below = sorted((v for v in refs if mc_key(v) < mc_key(at) and e["status"].get(v, "open") == "open"), key=mc_key)
    pat = pattern or e.get("pattern")
    files = list(e.get("files") or [])
    out = {"id": e["id"], "at": at, "below": below, "how": "pattern" if pat else "files" if files else None,
           "found": [], "unchanged": [], "present": [], "unchecked": [], "lowest": at, "error": None}
    for v in below:
        if pat:
            r = subprocess.run(["git", "-C", str(repo), "grep", "-q", "-E", "-e", pat, refs[v], "--", *files],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode == 0:
                out["found"].append(v)
            elif r.returncode != 1:
                out["unchecked"].append(v)
                out["error"] = (r.stderr.strip() or f"git grep exit {r.returncode}")[-200:]
        elif files:
            here, mine = _tree(repo, refs[v], cache), _tree(repo, refs[at], cache) if at in refs else {}
            hit = [f for f in files if f in here]
            if any(here[f] == mine.get(f) for f in hit):
                out["unchanged"].append(v)
            elif hit:
                out["present"].append(v)
        else:
            out["unchecked"].append(v)
    if out["found"]:
        out["lowest"] = out["found"][0]
    return out


def lowest_note(r):
    """A plan line's words for a lowest_check result ("" when it says nothing)."""
    if r.get("found"):
        return f"may be at {', '.join(r['found'])} (unconfirmed: its pattern is there)"
    if r.get("unchanged"):
        return f"lowest may be lower (unchecked): {', '.join(r['unchanged'])} {'has' if len(r['unchanged']) == 1 else 'have'} its file unchanged"
    return ""


def lowest_line(r):
    """`lowest`'s words for one result."""
    if r["found"]:
        return f"may be at {', '.join(r['found'])} (unconfirmed: the pattern is there)"
    if r["unchanged"]:
        return f"lowest may be lower (unchecked): {', '.join(r['unchanged'])} hold the file unchanged"
    if r["how"] is None and r["below"]:
        return f"unchecked: no files and no pattern ({', '.join(r['below'])} below)"
    return (f"nothing below ({len(r['below'])} version(s) looked at" + (f" by {r['how']}" if r["how"] else "")
            + (f"; {', '.join(r['present'])} hold the file changed" if r["present"] else "")
            + (f"; unchecked {', '.join(r['unchecked'])}" if r["unchecked"] else "") + ")")


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
    if s.startswith("duplicate:"):
        return f"dup of {s[10:]}"
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
    notes += [f"- {e['id']} at {v}: a duplicate of {s[10:]} (tracked there)" for e in issues
              for v, s in sorted(e["status"].items(), key=lambda x: mc_key(x[0])) if s.startswith("duplicate:")]
    if notes:
        out += ["", "\\* an owner override.", ""] + notes
    out += ["", "S1 crash, kick, data loss or hang (blocks publishing); S2 a core feature broken in normal play, no "
            "workaround, or a crash on a path nothing calls yet; S3 cosmetic, uncommon or with a workaround; S4 "
            "invisible or an unusual setup."]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- the weekly plan issue (issue #135, part 2a)

PLAN_MARK = "<!-- weekly-plan:v1 -->"
DATA_BEGIN, DATA_END = "<!-- weekly-plan:data", "-->"
CHOICES = ("take", "keep", "patch", "content")
DEFERRED_CHOICE = re.compile(r"^deferred:[A-Za-z0-9._:-]+$")    # keep ours, deferred on a dependency the version drops


def valid_choice(val):
    """A cascade decision's answer: take, keep, patch, content, or deferred:<dependency> (fork-branches.py
    --apply-decisions: ours kept, the authors' change a deferred entry while the version drops that dependency)."""
    return val in CHOICES or bool(DEFERRED_CHOICE.match(val or ""))
WRITE_PERMS = ("admin", "maintain", "write")
BOT = "github-actions[bot]"
SHOWN_COMMITS = 20
CHECKBOX_RE = re.compile(r"(?m)^\s*[-*] \[( |x|X)\] .*?<!-- (ki|authors|dep|adopt):(\S+?) (default|candidate) -->\s*$")
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


def _where(r):
    """An issue's versions on its plan line: the lowest version still open (the status map: where the round makes the
    fix) when it is not the first affected one, else `lowest` as before."""
    lo = r.get("lowest_open")
    w = f"lowest open {lo} (first affected {r['lowest']})" if lo and lo != r["lowest"] else f"lowest {r['lowest']}"
    return w + (f" ({r['lowest_note']})" if r.get("lowest_note") else "")


def _commit_line(c):
    return (f"- `{c['sha'][:12]}` {md(c['subject'])}" + (" (a merge of the authors' branch)" if c.get("merge") else "")
            + (f" -- not in {', '.join(c['versions'])}" if c.get("versions") else ""))


def fix_plan(f, selected, head_cascade):
    """Per selected issue of a version-branch fork: the version its fix is made at (the lowest one still open at plan
    time, `fix_at`) and the one cascade that carries it up -- never the head's untaken authors' code unless the plan
    takes it (head_cascade). -> [{"id", "at", "how", "cascade"}]."""
    rng = (f.get("cascade") or {}).get("range", "")
    head, newest = (rng.split("..", 1) + [""])[:2] if ".." in rng else (f["head"], "")
    base = f.get("base_version") or (head[len("neoforge-"):] if head.startswith("neoforge-") else None)
    out = []
    for i in selected:
        at = (f.get("fix_at") or {}).get(i)
        tool = "python3 tools/fork-branches.py cascade --repo <checkout>"
        if not at:
            how, cmd = "no open version in the plan's ledger: read the ledger's status map", None
        elif at == base and head_cascade:
            how, cmd = f"on the head {head}; the head cascade carries it", f"{tool} {head}..{newest}"
        elif at == base:
            how = (f"on a branch fix/{i} cut from the lowest version's port_taken, not on the head (the head has authors' "
                   f"code this plan does not take); merged into {head} too")
            cmd = f"{tool} {head}..{newest} --port-ref fix/{i}"
        elif at == newest:
            how, cmd = f"on neoforge-{at}, the newest version: nothing to cascade", None
        else:
            how, cmd = f"on neoforge-{at}, then cascaded from that version branch", f"{tool} {at}..{newest}"
        out.append({"id": i, "at": at, "how": how, "cascade": cmd})
    return out


SAFE_COORD = re.compile(r"^[A-Za-z0-9._+:-]+$")      # tools/layer-deps.py's: what a --bump command may carry


def esc(text):
    """Registry text (a build's name, a reason) for the issue: no code span, mention or table cell it could open."""
    return md(str(text)).replace("`", "\\`").replace("@", "&#64;").replace("<", "&lt;")


def dep_key(r):
    return f"{r['mc']} {r['ga']} {r['to']}"


def dep_lines(repo, du, carried):
    """The plan's "Dependency updates" lines of one fork: a box per pinned dependency with a newer compatible build,
    ticked by default unless it has a reason to look first (then unticked, the reason shown); a carried tick wins; a
    lookup that failed listed as "could not check"."""
    ok = lambda r: all(SAFE_COORD.match(str(r.get(k) or "")) for k in ("mc", "ga", "from", "to"))
    rows = [r for r in du.get("rows", []) if ok(r)]
    un = list(du.get("unchecked", [])) + [{"mc": r.get("mc"), "ga": r.get("ga"), "coord": r.get("from"),
                                           "why": "a coordinate holds characters a command must not carry"}
                                          for r in du.get("rows", []) if not ok(r)]
    code = lambda x: f"`{x}`" if SAFE_COORD.match(str(x or "")) else esc(x)
    if not rows and not un:
        return [f"Every pinned CurseForge/Modrinth dependency is on its newest compatible build ({du.get('checked', 0)} "
                "checked)." if du.get("checked") else "No CurseForge/Modrinth dependency pinned."]
    out = []
    if rows:
        out += ["Ticked, the round bumps it once at its setup (`tools/layer-deps.py resolve --bump`, in that version's "
                "checkout); unticked, the pin stays. Nothing else moves a pinned dependency.", ""]
    for r in rows:
        on = carried.get(dep_key(r), r["default"])
        names = f" ({esc(r.get('from_name'))} -> {esc(r.get('to_name'))}, {esc(r.get('channel') or '?')})"
        out.append(f"- [{'x' if on else ' '}] " + ("**restore** " if r.get("restore") else "")
                   + f"`{r['ga']}` at {r['mc']}: `{r['from']}` -> `{r['to']}`{names}"
                   + ("" if r["default"] else f" -- not ticked by default: {esc(r['why'])}")
                   + f" <!-- dep:{repo}:{r['id']} {'default' if r['default'] else 'candidate'} -->")
    if un:
        out += ["", "Could not check (the round leaves these pins as they are):", ""]
        out += [f"- {code(u['ga'])} at {esc(u['mc'])}" + (f" (`{u['coord']}`)" if SAFE_COORD.match(str(u.get("coord") or "")) else "")
                + f": {esc(u['why'])}" for u in un]
    return out


def shared_lines(repo, sh, carried):
    """The plan's "Adopt MMVU's shared code" lines of one version-branch fork (tools/weekly-plan.py, from
    fork-branches.py sync-shared --check): what the round's sync step takes by itself, a box per hand-written copy of
    what MMVU's shared library ships -- NOT ticked by default (it changes the fork's code); a carried tick wins -- and
    the code the fork marks ahead of MMVU (the closing comment's Lessons block lists it to harvest)."""
    if sh.get("error"):
        return [f"The shared-library check could not run ({esc(sh['error'])}): the round checks again at its start."]
    vs = sh.get("versions") or {}
    sync = [v for v, x in vs.items() if x.get("changes")]
    rec = [(v, p) for v, x in vs.items() for p in x.get("reconcile", [])]
    out = [(f"The round runs `fork-branches.py sync-shared` first, each as its own commit before the version's gates: "
            f"{len(sync)} version(s) take MMVU's newer shared tables or helpers ({', '.join(sync)})." if sync else
            "Every version is on MMVU's current shared tables and helpers: no sync this round.")]
    if rec:
        out += ["", "Edited by hand while MMVU's copy moved (the round never overwrites them; an agent reconciles each):", ""]
        out += [f"- {esc(p)} at {v}" for v, p in rec]
    ad = sh.get("adopt") or []
    if ad:
        out += ["", "The fork carries its own copy of what MMVU's shared library now ships. Not ticked by default (it "
                "changes the fork's code); ticked, the round replaces the copy with MMVU's at that version and runs "
                "its gates:", ""]
        for a in ad:
            what = (f"the fork's own copy of MMVU's shared helper `{esc(a.get('helper'))}` (library {esc(a.get('library'))}"
                    + (", byte-identical" if a.get("same") else "") + ")" if a.get("kind") == "helper" else
                    f"the row `{esc(a.get('row'))}` duplicates MMVU's shared table")
            out.append(f"- [{'x' if carried.get(a['id'], False) else ' '}] {esc(a['path'])} at {a['version']}: {what}"
                       + (" -- marked ahead of MMVU: harvest it first" if a.get("ahead") else "")
                       + f" <!-- adopt:{repo}:{a['id']} candidate -->")
    ah = sh.get("ahead") or []
    if ah:
        out += ["", "Marked ahead of MMVU (`MV-AHEAD-OF-TEMPLATE`): the closing comment's Lessons block lists these to "
                "harvest:", ""]
        out += [f"- {esc(a['path'])} at {a['version']}: {esc(a.get('text', ''))[:200]}" for a in ah]
    return out


def head_merge_lines(hm):
    """The Decisions section's words for the previewed head merge (weekly-plan.py cascade, a fork with new upstream
    commits only): the preview's caveat, the head merge's own conflicts, or that it could not run."""
    if hm.get("error"):
        return [f"Could not preview the authors' new commits in the cascade ({esc(hm['error'])[:300]}): the plan is the "
                "head's as it is, and the round's head merge may raise decisions it does not show."]
    out = []
    if hm.get("merged"):
        out.append("Previewed: the round makes its own head merge and ports it, so a decision's id can change; an answer "
                   "whose id the round does not find is asked again there.")
    cf = hm.get("conflicts") or []
    if cf:
        out += ["", f"- **Head merge needs an agent: {len(cf)} file(s)** conflict when the authors' commits merge into "
                "the head (the round's authors step ports them; the preview carried the authors' side of each conflicting "
                "hunk): " + ", ".join(f"`{esc(x)}`" for x in cf[:SHOWN_COMMITS])
                + (f" and {len(cf) - SHOWN_COMMITS} more" if len(cf) > SHOWN_COMMITS else "") + "."]
    return out


def sv_merge_lines(f):
    """A single-version fork's Decisions words for the fetch step's trial merge of the authors' new commits into the head
    (weekly-plan.py fetch: merge_conflicts, authors_merged, merge_error): the conflicting files, no conflict, or that it
    could not run."""
    cf = f.get("merge_conflicts") or []
    if cf:
        return ["", f"- **Head merge needs an agent: {len(cf)} file(s)** conflict when the authors' commits merge into the "
                "head (a trial merge; the round's authors step ports them): " + ", ".join(f"`{esc(x)}`" for x in cf[:SHOWN_COMMITS])
                + (f" and {len(cf) - SHOWN_COMMITS} more" if len(cf) > SHOWN_COMMITS else "") + "."]
    if f.get("authors_merged"):
        return ["", "The authors' new commits merge into the head with no conflict (a trial merge)."]
    return ["", f"Could not preview merging the authors' new commits into the head ("
            f"{esc(f.get('merge_error') or 'the trial merge did not run')[:300]}): the round's merge may conflict in files "
            "this plan does not show."]


def render_plan(inp):
    """PLAN-INPUT (tools/weekly-plan.py) -> {"title", "body", "decision_needed"}."""
    forks = inp["forks"]
    multi = len(forks) > 1
    prev = inp.get("previous") or {}
    pf = prev.get("frozen") or {}
    pticks = {f["repo"]: f.get("ticks", {}) for f in pf.get("forks", [])}
    pauth = {f["repo"]: f.get("take_authors") for f in pf.get("forks", [])}
    pdeps = {f["repo"]: f.get("dep_ticks") or {} for f in pf.get("forks", [])}
    padopt = {f["repo"]: f.get("adopt_ticks") or {} for f in pf.get("forks", [])}
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
        un = f.get("untaken") or []          # a version-branch fork's head: the authors' code no version has taken
        take = pauth.get(repo) if pauth.get(repo) is not None else True
        if n or un:
            what = ("the authors' new commits" if not un else "the authors' code the head already has" if not n else
                    "the authors' new commits and the code the head already has")
            lines.append(f"- [{'x' if take else ' '}] Take {what} this round <!-- authors:{repo} default -->")
        if n:
            lines += ["", f"`{f['upstream']}` `{f['upstream_branch']}` at `{f['upstream_commit'][:12]}`: {n} new commit(s) "
                      f"the head does not have:", ""]
            lines += [f"- `{c['sha'][:12]}` {md(c['subject'])}" for c in f.get("new_commits", [])[:SHOWN_COMMITS]]
            if n > SHOWN_COMMITS:
                lines.append(f"- ... and {n - SHOWN_COMMITS} more")
        if un:
            if not n:
                lines += ["", f"No new commits from the authors upstream (`{f['upstream']}` `{f['upstream_branch']}` at "
                          f"`{(f.get('upstream_commit') or '?')[:12]}`), but the head `{f['head']}` is ahead of the version "
                          "branches with the authors' code below: merged into the head earlier, never cascaded."]
            lines += ["", f"{len(un)} commit(s) of the authors' that the head has and a version branch has not taken (in "
                      f"that version's `port_taken..{f['head']}`):", ""]
            lines += [_commit_line(c) for c in un[:SHOWN_COMMITS]]
            if len(un) > SHOWN_COMMITS:
                lines.append(f"- ... and {len(un) - SHOWN_COMMITS} more")
        if (n or un) and f.get("cascade") is not None:
            lines += ["", "Ticked, the round cascades the head into every version branch, taking the authors' code (the "
                      "decisions below apply). Unticked, no version takes it this week: the head is not cascaded, and each "
                      "selected known issue is fixed at its lowest open version and cascaded up from there."]
        if not n and not un:
            lines.append(f"No new commits from the authors: the head has `{f['upstream']}` `{f['upstream_branch']}` "
                         f"at `{(f.get('upstream_commit') or '?')[:12]}`.")
        lines += ["", "### Known issues", ""]
        ticks = pticks.get(repo, {})
        sel, cand = f.get("selection", []), f.get("candidates", [])
        for r in sel:
            on = ticks.get(r["id"], True)
            lines.append(f"- [{'x' if on else ' '}] **{r['severity']}** `{_q(repo, r['id'], multi, clash)}` "
                         f"{md(r.get('title', ''))} -- {_where(r)}; {r['why']} <!-- ki:{repo}:{r['id']} default -->")
        for r in cand:
            on = ticks.get(r["id"], False)
            lines.append(f"- [{'x' if on else ' '}] **{r['severity']}** `{_q(repo, r['id'], multi, clash)}` "
                         f"{md(r.get('title', ''))} -- {_where(r)}; not in the default selection "
                         f"<!-- ki:{repo}:{r['id']} candidate -->")
        if not sel and not cand:
            lines.append(f"No open known issues in the ledger (`.port/{LEDGER_NAME}` on `{f['head']}`).")
        du = f.get("dep_updates")
        if du is not None:
            lines += ["", "### Dependency updates", ""] + dep_lines(repo, du, pdeps.get(repo, {}))
        sh = f.get("shared")
        if sh is not None:           # a version-branch fork only (weekly-plan.py cascade): a single-version plan stays as it was
            lines += ["", "### Adopt MMVU's shared code", ""] + shared_lines(repo, sh, padopt.get(repo, {}))
        lines += ["", "### Decisions", ""]
        c = f.get("cascade")
        decs = []
        if c is None:
            lines.append("A single-version fork (no version branches): there is no cascade to plan.")
            lines += sv_merge_lines(f) if n else []
        else:
            plan = c.get("plan") or {}
            ds = plan.get("decisions", [])
            esc = [d for d in ds if d.get("escalate")]
            escalated += len(esc)
            hm = c.get("head_merge")
            lines.append(f"The cascade plan (`fork-branches.py cascade {c['range']} --plan"
                         + (f" --head-merge {str(hm.get('commit') or hm.get('ref') or '')[:12]}`, the authors' new "
                            "commits merged into the head on a throwaway ref first, as the round will; git"
                            if hm and hm.get("merged") else "`, git")
                         + f" and the rename rows only): {len(plan.get('mechanical', []))} file(s) merged mechanically, "
                         f"{len(ds)} decision(s), {len(esc)} escalated to you.")
            if (n or un) and ds:
                lines.append("They are the head cascade's, which carries the authors' code: they apply only with the box "
                             "above ticked; unticked, the round runs no head cascade and none of them applies.")
            lines += head_merge_lines(hm) if hm else []
            for d in esc:
                lines += ["", f"- **`{d['id']}`** `{d['file']}` at {', '.join(d['versions'])}: {md(d.get('reasoning', ''))} "
                          f"Options: {', '.join('`' + x + '`' for x in CHOICES)}, `deferred:<dependency>` (ours kept "
                          f"while that version drops it). Recommended: `{d['choice']}` (the "
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
                           "cascade": None if c is None else {"range": c["range"], "decisions": decs,
                                                              **({"head_merge_conflicts": len(c["head_merge"].get("conflicts") or [])}
                                                                 if c.get("head_merge") else {})}})
        if c is None and n:          # a single-version fork: its trial merge's conflicts, like a head's cascade.head_merge_conflicts
            data_forks[-1]["head_merge_conflicts"] = len(f.get("merge_conflicts") or [])
        if du is not None:           # every kind of fork: the dependency updates the step found
            data_forks[-1]["dep_updates"] = [{k: r.get(k) for k in ("id", "mc", "ga", "from", "to", "default", "why")}
                                             for r in du.get("rows", [])]
        if sh is not None:
            data_forks[-1]["adopt"] = [{k: a.get(k) for k in ("id", "version", "kind", "path")} for a in sh.get("adopt") or []]
        if c is not None:            # a version-branch fork only: a single-version fork's data block stays as it was
            data_forks[-1].update({"untaken_count": len(un), "base_version": f.get("base_version"),
                                   "fix_at": {r["id"]: r.get("lowest_open") or r["lowest"] for r in sel + cand}})
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
    dticks = {r: {} for r in forks}
    aticks = {r: {} for r in forks}
    authors = {}
    for m in CHECKBOX_RE.finditer(body):
        on, kind, key = m.group(1) != " ", m.group(2), m.group(3)
        if kind == "authors":
            if key in forks:
                authors[key] = on
            continue
        if kind in ("dep", "adopt"):
            repo, _, i = key.rpartition(":")
            if repo in dticks:
                (dticks if kind == "dep" else aticks)[repo][i] = on
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
                if not valid_choice(val):
                    ignored.append({"by": who, "line": ln, "why": f"choice {val!r} is not one of {', '.join(CHOICES)} "
                                                                  "or deferred:<dependency>"})
                    continue
                decisions[hit[0]] = {"choice": val, "by": who, "reason": rest}
            else:
                if val.upper() not in SEVERITIES:
                    ignored.append({"by": who, "line": ln, "why": f"{val!r} is not S1-S4"})
                    continue
                grades[hit[0]] = {"fork": hit[0][0], "id": hit[0][1], "severity": val.upper(), "by": who,
                                  "reason": rest or f"owner override in plan issue #{issue.get('number')}"}
    out_forks, said_by = [], {}
    for r, f in forks.items():
        t = ticks[r]
        sel = [i for i in f["defaults"] + f["candidates"] if t.get(i, i in f["defaults"])]
        casc = f.get("cascade")
        has = bool(f.get("new_count")) or bool(f.get("untaken_count"))
        take = has and authors.get(r, True)
        # who decided: the plan pre-ticks the box, so an unticked box is the owner's (or a writer's) decision
        take_by = ("nothing to take (no new or untaken authors' commits)" if not has else
                   "default (the box was found ticked, as the plan pre-ticks it)" if authors.get(r) is True else
                   "owner (the box was unticked)" if r in authors else "default (no box found in the issue)")
        said_by[r] = take_by
        out_forks.append({
            "repo": r, "head": f["head"], "role": f.get("role", ""), "upstream": f.get("upstream"),
            "upstream_branch": f.get("upstream_branch"), "upstream_commit": f.get("upstream_commit"),
            "take_authors": take,
            "selected": sel, "ticks": {i: i in sel for i in f["defaults"] + f["candidates"]},
            "added": [i for i in sel if i not in f["defaults"]],
            "removed": [i for i in f["defaults"] if i not in sel],
            "cascade": None if casc is None else {
                "range": casc["range"],
                "decisions": [{"id": d["id"], "file": d["file"], "versions": d["versions"], "recommended": d["recommended"],
                               "choice": decisions[(r, d["id"])]["choice"], "by": decisions[(r, d["id"])]["by"],
                               "reason": decisions[(r, d["id"])]["reason"]} for d in casc["decisions"]]}})
        if casc is not None:
            # the head cascade carries the head's untaken authors' code: it runs only when the plan takes it (or there
            # is none of it); without it the plan's decisions -- all about that cascade -- do not apply
            head_cascade = take or not has
            o = out_forks[-1]
            o["take_authors_by"] = take_by       # a version-branch fork's only: a single-version fork's input stays as it was
            o["head_cascade"] = head_cascade
            if not head_cascade:
                o["cascade"] = {"range": casc["range"], "decisions": [],
                                "not_applied": [d["id"] for d in casc["decisions"]],
                                "why": "the authors' code is not taken this round: no head cascade, so no decision of it applies"}
            o["fixes"] = fix_plan(f, sel, head_cascade)
        if f.get("dep_updates") is not None:
            # the dependency updates the owner left ticked: the round bumps each once, at setup; nothing else moves a pin
            o = out_forks[-1]
            on = {d["id"]: dticks[r].get(d["id"], bool(d.get("default"))) for d in f["dep_updates"]}
            import shlex
            safe = lambda d: all(SAFE_COORD.match(str(d.get(k) or "")) for k in ("mc", "ga", "to"))
            o["dep_bumps"] = [{"id": d["id"], "mc": d["mc"], "ga": d["ga"], "from": d["from"], "to": d["to"],
                               "command": f"python3 mmvu/tools/layer-deps.py resolve --repo "
                                          f"{shlex.quote('<checkout of neoforge-' + d['mc'] + '>')} --target "
                                          f"{shlex.quote(d['mc'])} --bump {shlex.quote(d['ga'] + '=' + d['to'])}"}
                              for d in f["dep_updates"] if on[d["id"]] and safe(d)]
            o["dep_kept"] = [d["id"] for d in f["dep_updates"] if not on[d["id"]]]
            o["dep_ticks"] = {f"{d['mc']} {d['ga']} {d['to']}": on[d["id"]] for d in f["dep_updates"]}
        if f.get("adopt") is not None:
            # the hand-written copies of MMVU's shared code the owner ticked to adopt (none by default: a behaviour change)
            o = out_forks[-1]
            on = {a["id"]: aticks[r].get(a["id"], False) for a in f["adopt"]}
            o["adopt"] = [a["id"] for a in f["adopt"] if on[a["id"]]]
            o["adopt_ticks"] = on
    read_plan.take_by = said_by           # read-plan says it for every fork (stderr), the JSON keeps its form
    return {"schema": 1, "issue": issue.get("number"), "repo": data["repo"], "week": data["week"],
            "deadline": data["deadline"], "owner": data["owner"], "frozen_at": now, "forks": out_forks,
            "decisions": [dict({"fork": r, "id": i}, **v) for (r, i), v in sorted(decisions.items())],
            "grades": [grades[k] for k in sorted(grades)], "ignored": ignored, "carried_from": data.get("carried_from")}


GH_TIMEOUT_ENV = "MMVU_GH_TIMEOUT"       # seconds one GitHub call may take (gh or REST); default 60


def gh_timeout():
    try:
        return max(1.0, float(os.environ.get(GH_TIMEOUT_ENV) or 60))
    except ValueError:
        return 60.0


def gh_rest(path, method="GET", data=None, tok=None):
    """One REST call to api.github.com (urllib, a timeout on every socket operation); tok None: anonymous (a public
    repository's issues and releases read without one). -> parsed JSON."""
    import urllib.request
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "mmvu-weekly-plan", "Content-Type": "application/json"}
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request("https://api.github.com" + path, method=method,
                                 data=None if data is None else json.dumps(data).encode("utf-8"), headers=headers)
    with urllib.request.urlopen(req, timeout=min(30.0, gh_timeout())) as r:
        text = r.read().decode("utf-8")
    return json.loads(text) if text.strip() else {}


def gh_source():
    """Which way gh_api reaches GitHub, said once by the commands that read it."""
    if os.environ.get("GITHUB_TOKEN"):
        return "GITHUB_TOKEN over REST (gh is not used, so a broken gh login does not matter)"
    if os.environ.get("GH_TOKEN"):
        return "GH_TOKEN over REST (gh is not used)"
    return "the gh command line (no GITHUB_TOKEN or GH_TOKEN set), anonymous REST if gh fails or times out"


def gh_api(path, method="GET", data=None):
    """GitHub's REST API: GITHUB_TOKEN (else GH_TOKEN) over REST when set; else the `gh` command line. Every call is
    bounded (MMVU_GH_TIMEOUT, default 60 s; `gh` hung where its GraphQL endpoint was blocked), and a read whose way
    failed falls back -- a token's REST call to `gh`, `gh` to anonymous REST -- before it fails. -> parsed JSON."""
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    errs = []
    if tok:
        try:
            return gh_rest(path, method, data, tok)
        except Exception as e:          # noqa: BLE001 -- a bad token or a blocked call: try the next way for a read
            if method != "GET":
                raise
            errs.append(f"REST with the token: {e}")
    cmd = ["gh", "api", "-X", method, path] + (["--input", "-"] if data is not None else [])
    try:
        r = subprocess.run(cmd, input=None if data is None else json.dumps(data), capture_output=True, text=True,
                           encoding="utf-8", timeout=gh_timeout())
        if r.returncode == 0:
            return json.loads(r.stdout) if r.stdout.strip() else {}
        errs.append(f"gh: {(r.stderr or r.stdout).strip()[-300:]}")
    except subprocess.TimeoutExpired:
        errs.append(f"gh: no answer in {gh_timeout():.0f} s ({GH_TIMEOUT_ENV})"
                    + ("" if method == "GET" else f" -- this {method} may have been posted: check before re-running it"))
    except OSError as e:
        errs.append(f"gh: {e}")
    if method == "GET":
        try:
            return gh_rest(path, method, None, None)
        except Exception as e:          # noqa: BLE001
            errs.append(f"anonymous REST: {e}")
    raise Invalid(f"gh api {path}: " + "; ".join(errs) + " (set GITHUB_TOKEN or log in with gh)")


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
    """The next id in the ledger's own numbering: its most used `<PREFIX>-<n>` prefix, zero-padded as its widest number
    is (`EXM-024` -> `EXM-025`); `KI-1` for an empty ledger, as before."""
    got = [(m.group(1), m.group(2)) for e in data["issues"] for m in [re.match(r"^([A-Za-z][A-Za-z0-9]*)-(\d+)$", e["id"])] if m]
    if not got:
        return "KI-1"
    count = collections.Counter(p for p, _n in got)
    prefix = max(count, key=lambda p: (count[p], p == "KI"))
    nums = [n for p, n in got if p == prefix]
    width = max(len(n) for n in nums) if any(n.startswith("0") for n in nums) else 0
    return f"{prefix}-{max(int(n) for n in nums) + 1:0{width}d}"


def similar_titles(a, b):
    """0..1: how alike two issue titles read (difflib's ratio over the lower-cased words)."""
    import difflib
    w = lambda t: " ".join(re.findall(r"[a-z0-9]+", t.lower()))
    return difflib.SequenceMatcher(None, w(a), w(b)).ratio()


def likely_duplicates(data, title, files):
    """Existing entries a new one probably repeats: one that names a file the new one names and has a similar title
    (similar_titles >= 0.5), or any whose title is nearly the same (>= 0.85). -> [(id, title, why)]."""
    out = []
    for e in data["issues"]:
        sim = similar_titles(title, e.get("title", ""))
        same = sorted(set(files or ()) & set(e.get("files") or ()))
        if same and sim >= 0.5:
            out.append((e["id"], e.get("title", ""), f"same file {same[0]}, similar title"))
        elif sim >= 0.85:
            out.append((e["id"], e.get("title", ""), "nearly the same title"))
    return out


def expand_states(items, have=()):
    """`--set` values, each `MC=STATE`, `MC,MC,...=STATE` or `FROM..TO=STATE` (FROM, TO and every version the entry
    already lists between them) -> {MC: STATE}."""
    out = {}
    for it in items or ():
        vs, sep, st = it.partition("=")
        if not sep:
            raise Invalid(f"--set/--status {it!r}: MC=STATE")
        for v in (x.strip() for x in vs.split(",")):
            lo, dots, hi = v.partition("..")
            if dots:
                for x in (lo, hi):
                    if not MC_RE.match(x):
                        raise Invalid(f"--set {it!r}: {x!r} is not a Minecraft version")
                if mc_key(lo) > mc_key(hi):
                    raise Invalid(f"--set {it!r}: the range runs backwards ({lo} is after {hi}): write {hi}..{lo}")
                span = {lo, hi} | {x for x in have if mc_key(lo) <= mc_key(x) <= mc_key(hi)}
                for x in span:
                    out[x] = st.strip()
            elif not MC_RE.match(v):
                raise Invalid(f"--set {it!r}: {v!r} is not a Minecraft version")
            else:
                out[v] = st.strip()
    return out


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
        if e["rubric"]["failure"] in BLOCKING and sev_rank(a.override) > sev_rank("S2") \
                and not getattr(a, "owner", False):
            raise Invalid(f"--override {a.override}: a {e['rubric']['failure']} is S2 at the lowest without the owner "
                          f"-- one nothing reaches today is --reach latent (S2); a lower grade is the owner's call "
                          f"(`/grade {e['id']} {a.override}` on the plan issue, applied with --owner)")
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
    p.add_argument("--pattern", help="an extended regex (git grep -E) whose match in the files means the defect is there")
    p.add_argument("--platform", action="append", dest="platforms", choices=PLATFORMS,
                   help="only this platform's jar is affected (repeatable; default: every platform)")
    p = common(sub.add_parser("grade", help="re-grade an issue; an owner override wins and is recorded"))
    p.add_argument("id")
    rubric_args(p, False)
    p.add_argument("--override", choices=SEVERITIES); p.add_argument("--reason"); p.add_argument("--by")
    p.add_argument("--owner", action="store_true",
                   help="the override is the owner's (a /grade on the plan issue): it may grade a crash, kick, data loss, "
                        "hang, security or build failure below S2")
    p.add_argument("--clear-override", action="store_true")
    p = common(sub.add_parser("status", help="set an issue's state at Minecraft versions"))
    p.add_argument("id"); p.add_argument("--set", action="append", default=[], metavar="MC[,MC...|FROM..TO]=STATE",
                                         help="one version, a comma list, or FROM..TO (both ends and every version the "
                                              "entry lists between them); repeatable")
    p.add_argument("--lowest")
    p.add_argument("--pattern", help="set the entry's pattern (an extended regex; `lowest` greps the files for it)")
    p = common(sub.add_parser("lowest", help="is the defect also below the version it is fixed at? (read-only, git only)"))
    p.add_argument("ids", nargs="*", metavar="ID", help="default: every issue still open")
    p.add_argument("--at", help="the version the fix is made at (default: the issue's lowest open version)")
    p.add_argument("--pattern", help="grep for this extended regex instead of the entry's pattern")
    p.add_argument("--json", action="store_true")
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
    p.add_argument("--now", action="store_true", help="the owner asked the round to start now: freeze before the plan's "
                   "deadline on purpose (recorded in frozen.json as early_start); without it an early freeze warns")
    p.add_argument("--clock", help=argparse.SUPPRESS)        # the self-check's fixed "now" (ISO 8601)
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
    why = _refuse_layer((getattr(a, "repo", None) or ".") if hasattr(a, "repo") else None, "known-issues")
    if why:
        print(why)
        return 2
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
        for d_id, d_title, why in likely_duplicates(data, a.title.strip(), a.files):
            print(f"known-issues: WARNING {i} may repeat {d_id} ({why}: {d_title!r}): if it is the same defect, drop "
                  f"{i} and add to {d_id} instead (known-issues.py status/grade)", file=sys.stderr)
        e = {"id": i, "title": a.title.strip(), "override": None, "lowest": a.lowest, "source": a.source,
             "rubric": {"failure": a.failure, "visibility": a.visibility, "reach": a.reach,
                        "workaround": yesno(a.workaround)},
             "status": parse_states(a.status) or {a.lowest: "open"}}
        if a.notes:
            e["notes"] = a.notes
        if a.files:
            e["files"] = sorted(set(a.files))
        if a.pattern:
            e["pattern"] = a.pattern
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
        if not (a.set or a.lowest or a.pattern):
            raise Invalid("status: nothing to change (--set, --lowest or --pattern)")
        e["status"].update(expand_states(a.set, e["status"]))
        if a.lowest:
            e["lowest"] = a.lowest
        if a.pattern:
            e["pattern"] = a.pattern
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
    if a.cmd == "lowest":
        if a.at and not MC_RE.match(a.at):
            raise Invalid(f"--at {a.at!r} is not a Minecraft version")
        refs, cache = version_refs(repo), {}
        rows = [lowest_check(repo, find(data, i), a.at, refs, a.pattern, cache) for i in a.ids] if a.ids else \
            [lowest_check(repo, e, a.at, refs, a.pattern, cache) for e in sorted(data["issues"], key=order) if open_versions(e)]
        if a.json:
            out(json.dumps(rows, indent=1))
        for r in ([] if a.json else rows):
            out(f"{r['id']}\tat {r['at']}\t" + lowest_line(r) + (f"\tERROR {r['error']}" if r["error"] else ""))
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
    if not a.from_json:
        print(f"read-plan: GitHub through {gh_source()}", file=sys.stderr)
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
    clock = (datetime.datetime.fromisoformat(a.clock.replace("Z", "+00:00")) if a.clock else datetime.datetime.now(datetime.timezone.utc))
    now = clock.isoformat(timespec="seconds")
    frozen = read_plan(issue, comments, owner_check(slug, owner, api), now)
    try:
        ahead = (datetime.datetime.fromisoformat(str(data["deadline"]).replace("Z", "+00:00")) - clock).total_seconds()
    except (KeyError, TypeError, ValueError):
        ahead = 0
    if ahead > 0:
        left = f"{int(ahead // 3600)}h{int(ahead % 3600 // 60):02d}m"
        frozen["early_start"] = {"requested": bool(a.now), "deadline": data["deadline"], "frozen_at": now, "before_deadline": left}
        if a.now:
            print(f"read-plan: an early start the owner asked for (--now): frozen {left} before the deadline {data['deadline']}; "
                "recorded as early_start in the frozen input", file=sys.stderr)
        else:
            print(f"read-plan: WARNING frozen {left} before the plan's deadline {data['deadline']}: the owner's window to "
                  "tick boxes and reply is cut short (the plan workflow and the round are normally 24 hours apart). If the "
                  "owner asked to start now, run read-plan again with --now (recorded in frozen.json); otherwise wait for "
                  "the deadline.", file=sys.stderr)
    elif a.now:
        print(f"read-plan: --now: the deadline {data['deadline']} has passed already; nothing early to record", file=sys.stderr)
    text = json.dumps(frozen, indent=1, ensure_ascii=False) + "\n"
    if a.decisions_dir:
        d = pathlib.Path(a.decisions_dir)
        d.mkdir(parents=True, exist_ok=True)
        for f in frozen["forks"]:
            if f["cascade"] and f.get("head_cascade") is False:
                out(f"read-plan: {f['repo']}: no head cascade this round (the authors' code is not taken): no decisions "
                    f"file; {len(f['cascade']['not_applied'])} plan decision(s) do not apply")
            elif f["cascade"]:
                p = d / (f["repo"].replace("/", "__") + ".decisions.json")
                p.write_text(json.dumps(f["cascade"], indent=1) + "\n", encoding="utf-8")
                out(f"read-plan: {f['repo']}: {len(f['cascade']['decisions'])} decision(s) -> {p}")
    for f in frozen["forks"]:
        print(f"read-plan: {f['repo']}: take_authors {str(f['take_authors']).lower()} -- "
              f"{getattr(read_plan, 'take_by', {}).get(f['repo'], '?')}", file=sys.stderr)
    if a.out:
        pathlib.Path(a.out).write_text(text, encoding="utf-8")
        sel = sum(len(f["selected"]) for f in frozen["forks"])
        bumps = sum(len(f.get("dep_bumps", [])) for f in frozen["forks"])
        out(f"read-plan: #{n} frozen at {now}: {sel} known issue(s) selected, {bumps} dependency bump(s), {len(frozen['grades'])} grade "
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
        print(f"render-ports: GitHub through {gh_source()}", file=sys.stderr)
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


def _refuse_layer(repo, tool):
    """tools/fork-kind.py's refusal of a checkout with the removed .multiversion/ layer at HEAD: None, or the message
    (print it and exit 2)."""
    import importlib.util as _ilu
    s = _ilu.spec_from_file_location("fork_kind", pathlib.Path(__file__).resolve().parent / "fork-kind.py")
    m = _ilu.module_from_spec(s); s.loader.exec_module(m)
    return m.refuse_layer(repo, tool)


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
    for f in BLOCKING:
        expect(derive(R(f, "invisible", "latent", True)) == "S2", f"a {f} on a path nothing calls yet (latent) is S2")
    expect(derive(R("functional", r="latent")) == "S4" and derive(R("cosmetic", r="latent")) == "S4",
           "latent is S4 for any other failure, as unusual is")

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
        # a crash: never graded below S2 but by the owner; one nothing calls yet is S2 by its rubric (--reach latent)
        k1 = find(read_ledger(led), "KI-1"); reach1 = k1["rubric"]["reach"]
        rc, o = cli("grade", *L, "KI-1", "--override", "S4", "--reason", "nothing calls it", "--by", "keep-current-round")
        expect(rc == 2 and "--reach latent" in o and find(read_ledger(led), "KI-1") == k1,
               f"a round's override of a crash below S2 is refused, nothing written: {rc} {o}")
        rc, o = cli("grade", *L, "KI-1", "--override", "S2", "--reason", "nothing calls it yet")
        expect(rc == 0 and find(read_ledger(led), "KI-1")["severity"] == "S2", f"down to S2 is allowed: {rc} {o}")
        rc, o = cli("grade", *L, "KI-1", "--override", "S3", "--reason", "accepted", "--by", "owner", "--owner")
        expect(rc == 0 and find(read_ledger(led), "KI-1")["severity"] == "S3", f"the owner's --owner may go lower: {rc} {o}")
        rc, o = cli("grade", *L, "KI-1", "--clear-override", "--reach", "latent")
        expect(rc == 0 and find(read_ledger(led), "KI-1")["severity"] == "S2" and "rubric S2" in o,
               f"a crash with --reach latent grades S2: {rc} {o}")
        rc, o = cli("grade", *L, "KI-1", "--reach", reach1)
        expect(rc == 0 and find(read_ledger(led), "KI-1") == k1, f"back as it was: {rc} {o}")
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
        # weekly-round dry run (2026-10-18): new ids follow the ledger's own numbering, one --set takes several versions,
        # and an entry that repeats one (the same file, a similar title) is warned about
        led2 = d / "other"
        L2 = ("--ledger", str(led2 / LEDGER_NAME))
        base = ("--failure", "functional", "--visibility", "common", "--reach", "feature", "--workaround", "no")
        cli("add", *L2, "--id", "EXM-023", "--title", "the panel closes on its own", *base, "--lowest", "1.21.10",
            "--file", "src/main/java/com/example/mod/Panel.java")
        cli("add", *L2, "--id", "EXM-024", "--title", "fog drawn with the wrong shape", *base, "--lowest", "1.21.10",
            "--status", "1.21.10=open", "--status", "1.21.11=open", "--status", "26.1.2=open", "--status", "26.3=open")
        rc, o = cli("add", *L2, "--title", "a cursor drawn transparent", *base, "--lowest", "26.3")
        expect(rc == 0 and o.startswith("EXM-025: ") and "WARNING" not in o, f"add follows the ledger's prefix: {o}")
        rc, o = cli("add", *L2, "--title", "The panel closes on its own again", *base, "--lowest", "26.3",
                    "--file", "src/main/java/com/example/mod/Panel.java")
        expect(rc == 0 and "EXM-026" in o and "WARNING EXM-026 may repeat EXM-023" in o, f"a likely duplicate is warned about: {o}")
        rc, o = cli("status", *L2, "EXM-024", "--set", "1.21.10,1.21.11=fixed@abc1234", "--set", "26.1.2..26.3=fixed@def5678")
        st = find(read_ledger(led2 / LEDGER_NAME), "EXM-024")["status"]
        expect(rc == 0 and st == {"1.21.10": "fixed@abc1234", "1.21.11": "fixed@abc1234", "26.1.2": "fixed@def5678",
                                  "26.3": "fixed@def5678"}, f"status --set takes a list and a range in one call: {st} {o}")
        rc, o = cli("status", *L2, "EXM-024", "--set", "1.21.10..x=open")
        expect(rc == 2, f"a bad range is refused: {o}")
        rc, o = cli("status", *L2, "EXM-024", "--set", "26.3..1.21.10=open")
        expect(rc == 2 and "backwards" in o and find(read_ledger(led2 / LEDGER_NAME), "EXM-024")["status"]["26.3"] == "fixed@def5678",
               f"a reversed range is refused and nothing is written: {o}")
        expect(next_id({"issues": []}) == "KI-1" and next_id({"issues": [{"id": "KI-9"}]}) == "KI-10",
               "an empty ledger and a KI- ledger number as before")
        # GitHub calls are bounded and fall back (weekly-round dry run: render-ports --fetch-releases hung where gh's
        # GraphQL was blocked): gh timing out, then anonymous REST for a read; a write is never retried anonymously
        saved_env = {k: os.environ.pop(k, None) for k in ("GITHUB_TOKEN", "GH_TOKEN")}
        real_run, real_rest, seen = subprocess.run, globals()["gh_rest"], []
        try:
            def slow(cmd, **k):
                seen.append(("gh", k.get("timeout")))
                raise subprocess.TimeoutExpired(cmd, k.get("timeout"))
            subprocess.run = slow
            globals()["gh_rest"] = lambda path, method="GET", data=None, tok=None: (seen.append(("rest", tok)), [{"tag_name": "t"}])[1]
            got = gh_api("/repos/owner/examplemod/releases?per_page=100&page=1")
            try:
                gh_api("/repos/owner/examplemod/issues", "POST", {"title": "x"})
                wrote = True
            except Invalid as e:
                wrote = "no answer in" not in str(e) or "may have been posted" not in str(e)
            src = gh_source()
        finally:
            subprocess.run, globals()["gh_rest"] = real_run, real_rest
            for k, v in saved_env.items():
                if v is not None:
                    os.environ[k] = v
        expect(got == [{"tag_name": "t"}] and seen[:2] == [("gh", gh_timeout()), ("rest", None)] and not wrote
               and "gh command line" in src, f"gh bounded, a read falls back to REST, a write does not: {seen} {wrote}")
        # a multi-mod repository: one ledger per mod
        rc, o = cli("add", *L, "--mod", "examplemod", "--title", "t", "--failure", "kick", "--visibility", "rare",
                    "--reach", "normal", "--workaround", "no", "--lowest", "1.21.1")
        expect(rc == 0 and (d / ".port/examplemod" / LEDGER_NAME).is_file(), f"--mod writes .port/<mod>/: {o}")
        expect(cli("list", *L, "--mod", "../x")[0] == 2, "a bad --mod exits 2")
        # validation: a bad entry exits 2
        good = json.loads(led.read_text(encoding="utf-8"))
        # duplicate:<ID> (gap 5 after #147): a version of one issue tracked by another -- accepted by the validator and
        # the status command, rendered as such, never open (so neither selected for a round nor in release notes)
        dup = json.loads(json.dumps(good))
        a_, b_ = dup["issues"][0], dup["issues"][1]
        v_ = max(a_["status"], key=mc_key)
        a_["status"][v_] = f"duplicate:{b_['id']}"
        validate(dup)
        rendered = render_issue(dup)
        expect(f"dup of {b_['id']}" in rendered and f"{a_['id']} at {v_}: a duplicate of {b_['id']}" in rendered
               and v_ not in open_versions(a_)
               and a_["id"] not in [e["id"] for e, _w in select(dup, run_versions=[v_])]
               and a_["title"] not in release_notes(dup, v_, "examplemod-1.0-mc" + v_),
               f"duplicate:<ID> renders, is not open, is not selected nor listed in release notes: {rendered}")
        dup_file = d / "dup.json"
        dup_file.write_text(json.dumps(dup), encoding="utf-8")
        rc, o = cli("status", "--ledger", str(dup_file), a_["id"], "--set", f"{v_}=duplicate:{b_['id']}")
        expect(rc == 0, f"status --set MC=duplicate:<ID> is accepted: {rc} {o}")
        dup_file.unlink()
        cases = [
            (lambda x: x.update(schema=2), "schema"),
            (lambda x: x["issues"][0].update(severity="S3"), "re-grade"),
            (lambda x: x["issues"][0]["rubric"].update(failure="boom"), "failure"),
            (lambda x: x["issues"][0]["status"].update({"1.21.9": "fixed@zz"}), "fixed@<commit>"),
            (lambda x: x["issues"][0]["status"].update({"1.21.9": "wontfix:"}), "wontfix:<reason>"),
            (lambda x: x["issues"][0]["status"].update({"1.21.9": "duplicate:"}), "duplicate:<ID>"),
            (lambda x: x["issues"][0]["status"].update({"1.21.9": "duplicate:KI-404"}), "another issue"),
            (lambda x: x["issues"][0]["status"].update({"1.21.9": "duplicate:" + x["issues"][0]["id"]}), "another issue"),
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
            # a version branch (--mc) and a single-version fork (no --mc: HEAD, as before -- byte-for-byte)
            cg = load("ci_gates_ki", "ci-gates.py")
            (d / ".port" / LEDGER_NAME).write_text(dump(empty()), encoding="utf-8")         # an edit to the ledger alone
            head = g("rev-parse", "--short", "HEAD")
            expect(cg.tested_tree(d, "1.21.5") == head, "ci-gates: a ledger edit does not make a version branch's tree edited")
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
            fb = load("fork_branches_ki", "fork-branches.py")
            expect(all(fb.LEDGER_PATH.match(f) and not fb.jar_relevant(f, "1.21.4")
                       for f in (".port/KNOWN-ISSUES.json", ".port/examplemod/KNOWN-ISSUES.json"))
                   and not fb.LEDGER_PATH.match(".port/1.21.4/branch.properties")
                   and not fb.jar_relevant("README.md", "1.21.4") and fb.jar_relevant("src/main/resources/README.md", "1.21.4"),
                   "fork-branches: the ledger is no version branch's jar input, and the cascade skips it (LEDGER_PATH)")
            tpl = (root / "templates/upstream-harness/port-ci.yml").read_text(encoding="utf-8")
            # the base list, as a single-version fork gets it (a version branch's records join it: port-ci.py RECORDS_IGNORE)
            push = tpl[tpl.index("  push:"):tpl.index("  pull_request:")].replace("{{RECORDS_IGNORE}}", "")
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
    plan_untaken_check(expect, cli)
    lowest_selfcheck(expect, cli)
    took = time.monotonic() - t0
    for b in bad:
        print("FAIL:", b)
    print(f"self-check: {'OK' if not bad else 'FAIL'} ({took:.1f} s)")
    return 0 if not bad else 1


def lowest_selfcheck(expect, cli):
    """LOWEST on a three-version fixture (the head 1.21.1, 1.21.4, 26.1.2): the pattern found below the ledger's
    lowest, a file unchanged below flagged, a set status trusted, a single-version fork with nothing below."""
    import shutil
    d = pathlib.Path(tempfile.mkdtemp(prefix="known-issues-lowest-"))
    try:
        g = lambda *a: subprocess.run(["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                                       "-c", "gc.auto=0", *a], capture_output=True, text=True, encoding="utf-8")
        g("init", "-q", "-b", "neoforge-1.21.1")
        src = d / "src" / "a"
        src.mkdir(parents=True)
        (src / "Defect.java").write_text("class Defect { void x() { goodCall(); } }\n", encoding="utf-8")
        (src / "Same.java").write_text("class Same {}\n", encoding="utf-8")
        g("add", "-A"); g("commit", "-qm", "head")
        for v in ("1.21.4", "26.1.2"):
            g("checkout", "-q", "-b", f"neoforge-{v}")
            (src / "Defect.java").write_text(f"class Defect {{ void x() {{ badCall(); }} }} // {v}\n", encoding="utf-8")
            g("add", "-A"); g("commit", "-qm", v)
        led = d / ".port" / LEDGER_NAME
        base = ["--repo", str(d), "--failure", "functional", "--visibility", "common", "--reach", "normal",
                "--workaround", "no", "--lowest", "26.1.2"]
        rc1, _ = cli("add", *base, "--id", "EXM-1", "--title", "the bad call", "--file", "src/a/Defect.java",
                     "--pattern", r"badCall\(\)")
        rc2, _ = cli("add", *base, "--id", "EXM-2", "--title", "the same file", "--file", "src/a/Same.java")
        rc3, _ = cli("add", *base, "--id", "EXM-3", "--title", "nothing recorded about where")
        rc4, _ = cli("add", *base, "--id", "EXM-4", "--title", "trusted below", "--file", "src/a/Defect.java",
                     "--pattern", "badCall", "--status", "26.1.2=open", "--status", "1.21.4=n/a")
        data = read_ledger(led)
        expect(rc1 == rc2 == rc3 == rc4 == 0 and find(data, "EXM-1")["pattern"] == r"badCall\(\)",
               f"add --pattern records the pattern: {rc1} {rc2} {rc3} {rc4}")
        refs = version_refs(d)
        expect(sorted(refs) == ["1.21.1", "1.21.4", "26.1.2"], f"the fork's version branches: {refs}")
        r1 = lowest_check(d, find(data, "EXM-1"), refs=refs)
        expect(r1["at"] == "26.1.2" and r1["found"] == ["1.21.4"] and r1["lowest"] == "1.21.4" and r1["how"] == "pattern"
               and lowest_note(r1) == "may be at 1.21.4 (unconfirmed: its pattern is there)",
               f"the pattern on 1.21.4 (not on the head) while the ledger says 26.1.2: a candidate, unconfirmed: {r1}")
        r2 = lowest_check(d, find(data, "EXM-2"), refs=refs)
        expect(not r2["found"] and r2["unchanged"] == ["1.21.1", "1.21.4"] and r2["lowest"] == "26.1.2"
               and lowest_note(r2).startswith("lowest may be lower (unchecked): 1.21.1, 1.21.4"),
               f"no pattern, the file byte-identical below: flagged, the fix stays: {r2}")
        r3 = lowest_check(d, find(data, "EXM-3"), refs=refs)
        expect(r3["how"] is None and r3["unchecked"] == ["1.21.1", "1.21.4"] and not lowest_note(r3),
               f"no files, no pattern: every version below unchecked, nothing claimed: {r3}")
        r4 = lowest_check(d, find(data, "EXM-4"), refs=refs)
        expect(r4["below"] == ["1.21.1"] and not r4["found"], f"a status the ledger sets (n/a) is trusted: {r4}")
        rc, o = cli("lowest", "--repo", str(d), "EXM-1", "EXM-2")
        expect(rc == 0 and "EXM-1\tat 26.1.2\tmay be at 1.21.4 (unconfirmed" in o and "lowest may be lower (unchecked)" in o,
               f"lowest prints one line per issue: {o}")
        rc, o = cli("lowest", "--repo", str(d), "EXM-3", "--pattern", "badCall", "--json")
        expect(rc == 0 and json.loads(o)[0]["found"] == ["1.21.4"], f"--pattern overrides the entry's: {o}")
        rc, o = cli("status", "--repo", str(d), "EXM-2", "--pattern", "class Same")
        expect(rc == 0 and find(read_ledger(led), "EXM-2")["pattern"] == "class Same", f"status --pattern: {rc} {o}")
        rc, o = cli("status", "--repo", str(d), "EXM-2")
        expect(rc == 2, f"status with nothing to change is refused: {rc} {o}")
        bad = json.loads(led.read_text(encoding="utf-8"))
        bad["issues"][0]["pattern"] = "two\nlines"
        try:
            validate(bad)
            expect(False, "a two-line pattern is refused")
        except Invalid:
            pass
        # a single-version fork: one branch, nothing below it
        g("checkout", "-q", "neoforge-1.21.1")
        for v in ("1.21.4", "26.1.2"):
            g("branch", "-q", "-D", f"neoforge-{v}")
        r5 = lowest_check(d, find(data, "EXM-1"), at="1.21.1")
        expect(r5["below"] == [] and not r5["found"] and r5["lowest"] == "1.21.1" and not lowest_note(r5),
               f"a single-version fork: nothing below its one version, nothing changes: {r5}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def plan_untaken_check(expect, cli):
    """The first weekly-round dry run's findings: the head's untaken authors' code in the plan, the round's cascades
    when it is not taken, the lowest open version, the early start -- and a single-version fork's plan byte for byte."""
    import hashlib
    import shutil
    h = lambda x: hashlib.sha256(json.dumps(x, sort_keys=True).encode()).hexdigest()[:16]
    sv = {"repo": "o/solo", "week": "2026-10-20", "deadline": "2026-10-21T08:21-07:00",
          "deadline_text": "Wednesday 2026-10-21 at 08:21 America/Los_Angeles (PDT)", "owner": {"login": "someone"},
          "forks": [{"repo": "o/solo", "head": "neoforge-1.21.1", "role": "", "upstream": "https://example.invalid/s",
                     "upstream_branch": "main", "upstream_commit": "a" * 40, "head_commit": "b" * 40, "new_count": 2,
                     "new_commits": [{"sha": "1" * 40, "subject": "one"}, {"sha": "2" * 40, "subject": "two"}], "cascade": None,
                     "selection": [{"id": "KI-1", "severity": "S2", "lowest": "1.21.1", "lowest_open": "1.21.1",
                                    "why": "S2 within the budget of 3, lowest affected version first", "title": "a"}],
                     "candidates": [{"id": "KI-2", "severity": "S3", "lowest": "1.21.1", "lowest_open": "1.21.1", "title": "b"}],
                     "ledger_ids": ["KI-1", "KI-2"]}]}
    res = render_plan(json.loads(json.dumps(sv)))
    fr = read_plan({"number": 3, "body": res["body"]}, [], lambda w: True, "2026-10-21T16:00:00+00:00")
    sv0 = json.loads(json.dumps(sv))
    sv0["forks"][0].update(new_count=0, new_commits=[])
    # pinned: a single-version fork's plan issue and frozen input, byte for byte (#161 U3 changed the first on purpose:
    # with new commits its Decisions section shows the fetch step's trial merge, here "Could not preview", none recorded)
    expect("Could not preview merging" in res["body"] and "Could not preview" not in render_plan(sv0)["body"]
           and (h(res), h(fr), h(render_plan(sv0))) == ("ce1aa9dc5af34a9c", "af5159d1b42a19c3", "9b9ceb2f13e3497b"),
           f"a single-version fork's plan and frozen input are unchanged: {(h(res), h(fr), h(render_plan(sv0)))}")
    dec = {"id": "d1e2f3a4b5c6", "file": "src/main/java/com/example/mod/A.java", "versions": ["1.21.4", "1.21.5"],
           "escalate": True, "choice": "keep", "reasoning": "over the budget."}
    dflt = {"id": "a1b2c3d4e5f6", "file": "src/main/java/com/example/mod/B.java", "versions": ["1.21.4"],
            "escalate": False, "choice": "patch", "reasoning": "the authors' fix."}
    un = [{"sha": "f" * 40, "subject": "Merge the authors' 2.0", "merge": True, "versions": ["1.21.3", "1.21.4", "1.21.5"]},
          {"sha": "e" * 40, "subject": "authors: 2.0", "merge": False, "versions": ["1.21.3", "1.21.4", "1.21.5"]}]
    vb = {"repo": "o/lib", "week": "2026-10-20", "deadline": "2026-10-21T08:21-07:00",
          "deadline_text": "Wednesday 2026-10-21 at 08:21 America/Los_Angeles (PDT)", "owner": {"login": "someone"},
          "forks": [{"repo": "o/lib", "head": "neoforge-1.21.1", "role": "library", "upstream": "https://example.invalid/a",
                     "upstream_branch": "main", "upstream_commit": "c" * 40, "head_commit": "d" * 40, "new_count": 0,
                     "untaken": un, "base_version": "1.21.1",
                     "cascade": {"range": "neoforge-1.21.1..1.21.5", "plan": {"mechanical": [], "decisions": [dec, dflt]}},
                     "selection": [{"id": "KI-3", "severity": "S1", "lowest": "1.21.1", "lowest_open": "1.21.4",
                                    "why": "S1: always taken", "title": "a crash"},
                                   {"id": "KI-4", "severity": "S1", "lowest": "1.21.1", "lowest_open": "1.21.1",
                                    "why": "S1: always taken", "title": "a kick"}],
                     "candidates": [{"id": "KI-5", "severity": "S3", "lowest": "1.21.1", "lowest_open": "26.3", "title": "c"}],
                     "ledger_ids": ["KI-3", "KI-4", "KI-5"]}]}
    b = render_plan(vb)["body"]
    expect("- [x] Take the authors' code the head already has this round <!-- authors:o/lib default -->" in b
           and "No new commits from the authors upstream" in b and "is ahead of the version branches" in b
           and "- `ffffffffffff` Merge the authors' 2.0 (a merge of the authors' branch) -- not in 1.21.3, 1.21.4, 1.21.5" in b
           and "- `eeeeeeeeeeee` authors: 2.0 -- not in" in b and "Unticked, no version takes it this week" in b
           and "No new commits from the authors: the head has" not in b,
           f"the head's untaken authors' code: listed with its own box (default ticked, as for new commits): {b}")
    expect("`KI-3` a crash -- lowest open 1.21.4 (first affected 1.21.1);" in b and "`KI-4` a kick -- lowest 1.21.1;" in b
           and "`KI-5` c -- lowest open 26.3 (first affected 1.21.1);" in b,
           "each issue shows its lowest open version when the first affected one is fixed already")
    expect("they apply only with the box above ticked" in b, "the cascade decisions are the head cascade's: conditional on the box")
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)

        def freeze(body, *extra):
            (d / "fx.json").write_text(json.dumps({"issue": {"number": 9, "body": body}, "comments": []}), encoding="utf-8")
            for x in ("frozen.json", "dec"):
                shutil.rmtree(d / x, ignore_errors=True) if x == "dec" else (d / x).unlink(missing_ok=True)
            rc, o = cli("read-plan", "--issue", "9", "--slug", "o/lib", "--owner", "someone", "--from-json", str(d / "fx.json"),
                        "--out", str(d / "frozen.json"), "--decisions-dir", str(d / "dec"), *extra)
            fr = json.loads((d / "frozen.json").read_text(encoding="utf-8")) if rc == 0 else {}
            return rc, o, fr, (fr.get("forks") or [{}])[0]
        late = ("--clock", "2026-10-21T16:00:00+00:00")
        rc, o, fr, f = freeze(b, *late)
        fx = {x["id"]: x for x in f.get("fixes", [])}
        expect(rc == 0 and f.get("take_authors") is True and f.get("head_cascade") is True
               and len(f.get("cascade", {}).get("decisions", [])) == 2 and (d / "dec" / "o__lib.decisions.json").is_file()
               and fx.get("KI-3", {}).get("cascade", "").endswith(" 1.21.4..1.21.5")
               and fx.get("KI-4", {}).get("cascade", "").endswith(" neoforge-1.21.1..1.21.5") and "early_start" not in fr,
               f"ticked: the authors' code is taken, the head cascade runs with its decisions; each fix at its lowest open "
               f"version: {f} {o}")
        expect(f.get("take_authors_by", "").startswith("default") and "take_authors true -- default" in o,
               f"read-plan says a ticked box is the plan's default: {f.get('take_authors_by')} {o}")
        rc, o, fr, f = freeze(b.replace("- [x] Take the authors' code", "- [ ] Take the authors' code"), *late)
        fx = {x["id"]: x for x in f.get("fixes", [])}
        expect(f.get("take_authors_by", "").startswith("owner") and "take_authors false -- owner" in o,
               f"read-plan says the owner unticked the authors' box: {f.get('take_authors_by')} {o}")
        expect(rc == 0 and f.get("take_authors") is False and f.get("head_cascade") is False
               and f.get("cascade", {}).get("decisions") == [] and f["cascade"].get("not_applied") == ["d1e2f3a4b5c6", "a1b2c3d4e5f6"]
               and not (d / "dec" / "o__lib.decisions.json").exists() and "no head cascade" in o
               and fx.get("KI-3", {}).get("at") == "1.21.4" and fx["KI-3"]["cascade"].endswith(" 1.21.4..1.21.5")
               and fx.get("KI-4", {}).get("cascade", "").endswith(" neoforge-1.21.1..1.21.5 --port-ref fix/KI-4"),
               f"unticked: no head cascade, no decision applies, no decisions file; the fixes cascade from their lowest open "
               f"version (the head's version through a fix branch, --port-ref): {f} {o}")
        early = ("--clock", "2026-10-20T18:00:00+00:00")
        rc, o, fr, f = freeze(b, *early)
        es = fr.get("early_start") or {}
        expect(rc == 0 and "WARNING frozen 21h21m before the plan's deadline" in o and "24 hours apart" in o and "--now" in o
               and es.get("requested") is False and es.get("before_deadline") == "21h21m",
               f"read-plan before the deadline warns and records the early freeze: {es} {o}")
        rc, o, fr, f = freeze(b, *early, "--now")
        es = fr.get("early_start") or {}
        expect(rc == 0 and "WARNING" not in o and "early start the owner asked for" in o and es.get("requested") is True
               and es.get("deadline") == "2026-10-21T08:21-07:00",
               f"read-plan --now: the owner's early start, recorded in frozen.json: {es} {o}")


def plan_check(expect, cli):
    """render-plan / read-plan / render-ports on invented data: no network, no git beyond one tiny repo."""
    import shlex
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
        expect(valid_choice("keep") and valid_choice("deferred:examplespells") and valid_choice("deferred:curse.maven:x-1")
               and not valid_choice("deferred:") and not valid_choice("deferred:$(x)") and not valid_choice("maybe"),
               "a decision answer: take/keep/patch/content or deferred:<dependency>, nothing with shell syntax")
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
        # the shared library (a version-branch fork only): the sync preview, the reconcile list, an adopt box NOT ticked
        # by default, the ahead marks; a ticked box comes back as the fork's `adopt`; the single-version fork gets none
        inp3 = json.loads(json.dumps(inp))
        inp3["forks"][0]["shared"] = {
            "versions": {"1.21.4": {"changes": 2, "reconcile": []},
                         "1.21.5": {"changes": 0, "reconcile": ["src/main/java/com/example/mod/mv/Gl.java"]}},
            "adopt": [{"id": "A0123456789", "version": "1.21.5", "kind": "helper", "path": "src/main/java/com/example/mod/mv/Gl.java",
                       "helper": "Gl", "library": "1.21.5", "same": False, "ahead": True},
                      {"id": "Abcdef01234", "version": "1.21.4", "kind": "row", "path": ".port/1.21.4/renames.tsv",
                       "row": "a.B\tc.B", "library": "1.21.4", "same": True}],
            "ahead": [{"version": "1.21.5", "path": "src/main/java/com/example/mod/mv/Gl.java", "line": 1, "text": "a faster draw"}]}
        b3 = render_plan(inp3)["body"]
        sec = b3.split("### Adopt MMVU's shared code", 1)[-1].split("###", 1)[0]
        expect(b3.count("### Adopt MMVU's shared code") == 1 and "1 version(s) take MMVU's newer shared tables or helpers (1.21.4)" in sec
               and "Gl.java at 1.21.5" in sec and "- [ ] " in sec and "- [x]" not in sec
               and "<!-- adopt:o/lib:A0123456789 candidate -->" in sec and "harvest it first" in sec
               and "a faster draw" in sec and b3.replace(sec, "").count("Adopt MMVU") == 1
               and "### Adopt" not in b and render_plan(inp)["body"] == b,
               f"render-plan: the shared-library section of the version-branch fork only, its boxes unticked: {sec}")
        fr3 = read_plan({"number": 7, "body": b3.replace("- [ ] src/main/java/com/example/mod/mv/Gl.java", "- [x] src/main/java/com/example/mod/mv/Gl.java")
                         .replace("- [ ] src/main/java/com/example/mod/mv/Gl\\.java", "- [x] src/main/java/com/example/mod/mv/Gl\\.java")},
                        [], lambda u: True, "t")
        l3, d3 = fr3["forks"][0], fr3["forks"][1]
        expect(l3.get("adopt") == ["A0123456789"] and l3.get("adopt_ticks") == {"A0123456789": True, "Abcdef01234": False}
               and "adopt" not in d3 and "adopt" not in read_plan({"number": 7, "body": b}, [], lambda u: True, "t")["forks"][0],
               f"read-plan: the ticked adopt box only; no adopt key without the section: {l3.get('adopt_ticks')} {sec}")
        # dependency updates (every kind of fork): ticked by default, unticked with the reason; ticks read back
        du_lib = {"checked": 3, "rows": [
            {"id": "D1", "mc": "1.21.1", "ga": "maven.modrinth:viewer", "from": "maven.modrinth:viewer:a1",
             "to": "maven.modrinth:viewer:a2", "from_name": "1.1", "to_name": "1.2", "channel": "release", "default": True, "why": ""},
            {"id": "D2", "mc": "1.21.4", "ga": "curse.maven:extra-333", "from": "curse.maven:extra-333:5",
             "to": "curse.maven:extra-333:9", "from_name": "x-2.0", "to_name": "x-3.0-beta", "channel": "beta", "default": False,
             "why": "a beta; a major-version jump (x-2.0 -> x-3.0-beta)"}],
            "unchecked": [{"mc": "1.21.5", "ga": "maven.modrinth:down", "coord": "maven.modrinth:down:z", "why": "HTTP Error 503"}]}
        inp2 = json.loads(json.dumps(inp))
        inp2["forks"][0]["dep_updates"] = du_lib
        inp2["forks"][1]["dep_updates"] = {"checked": 2, "rows": [], "unchecked": []}
        b2 = render_plan(inp2)["body"]
        expect("### Dependency updates" in b2 and "- [x] `maven.modrinth:viewer` at 1.21.1: `maven.modrinth:viewer:a1` -> "
               "`maven.modrinth:viewer:a2` (1.1 -> 1.2, release) <!-- dep:o/lib:D1 default -->" in b2
               and "- [ ] `curse.maven:extra-333` at 1.21.4" in b2 and "not ticked by default: a beta; a major-version jump" in b2
               and "<!-- dep:o/lib:D2 candidate -->" in b2 and "`maven.modrinth:down` at 1.21.5 (`maven.modrinth:down:z`): HTTP Error 503" in b2
               and "on its newest compatible build (2 checked)" in b2 and "### Dependency updates" not in b,
               f"render-plan: the dependency section per fork, defaults and reasons, the unchecked listed: {b2}")
        b3 = b2.replace("- [x] `maven.modrinth:viewer`", "- [ ] `maven.modrinth:viewer`").replace("- [ ] `curse.maven:extra-333`",
                                                                                                  "- [x] `curse.maven:extra-333`")
        for body, want in ((b2, ["D1"]), (b3, ["D2"])):
            fr2 = read_plan({"number": 8, "body": body}, [], lambda u: True, "t")
            l2, d2_ = fr2["forks"][0], fr2["forks"][1]
            expect([x["id"] for x in l2.get("dep_bumps", [])] == want and d2_.get("dep_bumps") == []
                   and all(f"--bump {x['ga']}={x['to']}" in x["command"] and f"--target {x['mc']}" in x["command"]
                           for x in l2["dep_bumps"]) and len(l2.get("dep_ticks", {})) == 2,
                   f"read-plan: the ticked dependency bumps, each with its command: {l2.get('dep_bumps')} {l2.get('dep_kept')}")
        # review S2 (security): registry text cannot open a code span or a mention, and a coordinate with shell syntax
        # is never a bump; every command is quoted
        bad_inp = json.loads(json.dumps(inp2))
        bad_inp["forks"][0]["dep_updates"]["rows"] = [dict(du_lib["rows"][0], from_name="`@someone` $(x)"),
                                                     dict(du_lib["rows"][0], id="D2", to="maven.modrinth:viewer:$(touch x)")]
        b4 = render_plan(bad_inp)["body"]
        fr4 = read_plan({"number": 9, "body": b4}, [], lambda u: True, "t")["forks"][0]
        expect("\\`&#64;someone\\` $(x)" in b4 and "`@someone`" not in b4 and "$(touch x)" not in b4.split("<!-- weekly-plan:data")[0]
               and [x["id"] for x in fr4.get("dep_bumps", [])] == ["D1"]
               and shlex.split(fr4["dep_bumps"][0]["command"])[-1] == "maven.modrinth:viewer=maven.modrinth:viewer:a2",
               f"registry text escaped, an unsafe coordinate never a bump, the command quoted: {b4} {fr4}")
        expect("dep_bumps" not in json.loads(json.dumps(read_plan({"number": 7, "body": b}, [], lambda u: True, "t")))["forks"][0],
               "a plan without the dependency section reads back as before")
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
