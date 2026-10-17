#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The known-issues ledger of a fork port: every known defect, graded S1-S4 by a fixed rubric, with its status at
each Minecraft version (issue #135, part 1).

    python3 tools/known-issues.py add     [LEDGER] --title T --failure F --visibility V --reach R --workaround yes|no
                                          --lowest MC [--status MC=STATE ...] [--id ID] [--source S] [--notes N]
                                          [--file PATH ...]
    python3 tools/known-issues.py grade   [LEDGER] ID [--failure F] [--visibility V] [--reach R] [--workaround yes|no]
                                          [--override S --reason WHY [--by WHO] | --clear-override]
    python3 tools/known-issues.py status  [LEDGER] ID --set MC=STATE [--set ...] [--lowest MC]
    python3 tools/known-issues.py list    [LEDGER] [--version MC] [--severity S1,S2] [--min S] [--open] [--json]
    python3 tools/known-issues.py select  [LEDGER] [--budget N] [--touched PATH ...] [--version MC ...]
                                          [--add ID ...] [--remove ID ...] [--json]
    python3 tools/known-issues.py render-issue  [LEDGER]
    python3 tools/known-issues.py release-notes [LEDGER] --version MC --tag TAG [--min S3] [--repo DIR]
    python3 tools/known-issues.py reopens [LEDGER] --version MC (--severity S | --id ID)
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
               "rubric": {"failure": "crash|kick|data|hang|functional|cosmetic",
                          "visibility": "always|common|rare|invisible",
                          "reach": "normal|feature|unusual", "workaround": false},
               "severity": "S1".."S4",          # the rubric's grade, or the override's when there is one
               "override": null | {"severity": "S2", "reason": "...", "by": "owner"},
               "lowest": "1.21.1",              # the lowest affected Minecraft version (a key of status)
               "status": {"1.21.1": "open", "1.21.3": "fixed@<commit>", "1.21.4": "wontfix:<reason>", "1.21.5": "n/a"},
               "source": "authors|review|gate|player",
               "files": ["src/main/java/..."],  # optional: the files the defect is in (select's S3 rule)
               "notes": "..."}]}
  A version missing from status is not affected. Which releases contain an issue is never stored: release-notes works
  it out from the tag (with --repo, a fixed@<commit> that is not in the tag counts as open there).

THE RUBRIC (grade derives the severity from the recorded inputs; an owner override wins and is recorded):
  S1  failure crash, kick (or network disconnect), data (world or player data lost or corrupted) or hang -- whatever
      else. It blocks publishing (publish-port refuses an open S1).
  S4  otherwise: visibility invisible, or reach unusual (an unusual setup, other mods, debug options).
  S2  otherwise: a functional failure with reach normal, visibility always or common and no workaround.
  S3  everything else: cosmetic, rare, a specific feature only, or a workaround exists. Cosmetic is S3 at most.
  The failure types beside the four S1 ones:
    functional  a feature does not work, or information or controls the player needs are missing, unreadable or
                unusable -- invisible GUI text and a missing UI element are functional;
    cosmetic    the look is wrong, but nothing the player needs is lost (a wrong colour tint, a texture seam).

ONLY S1 GOES BACK. A finding at a version that is already done reopens it only when it is an S1 (`reopens` answers
yes, exit 0); any other finding goes into the ledger at its lowest affected version (exit 1).

SELECT (the default policy before a run): every issue still open at a version of the run graded S1; then S2s, lowest
affected version first, up to --budget (default 3); then any S3 in a file the run already touches (--touched). The
owner's --add and --remove apply last, and the output says who chose each item.

Exit codes: 0 ok (reopens: it reopens); 1 reopens: it does not reopen; 2 bad input or an invalid ledger.
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
FAILURES = ("crash", "kick", "data", "hang", "functional", "cosmetic")
BLOCKING = ("crash", "kick", "data", "hang")
VISIBILITY = ("always", "common", "rare", "invisible")
REACH = ("normal", "feature", "unusual")
SOURCES = ("authors", "review", "gate", "player")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MOD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MC_RE = re.compile(r"^\d+(?:\.\d+){1,3}$")
STATE_RE = re.compile(r"^(?:open|n/a|fixed@[0-9a-f]{7,40}|wontfix:\S.*)$")
DEFAULT_BUDGET = 3
TOP_KEYS = {"schema", "issues"}
ISSUE_KEYS = {"id", "title", "rubric", "severity", "override", "lowest", "status", "source", "files", "notes"}


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


def reopens(severity):
    return severity == "S1"


def fixed_in(repo, commit, tag):
    """True / False: the fix commit is / is not in the tag; None when git cannot tell."""
    r = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", commit, tag], capture_output=True)
    return {0: True, 1: False}.get(r.returncode)


def release_notes(data, version, tag, min_sev="S3", repo=None):
    rows = []
    for e in sorted(data["issues"], key=order):
        if sev_rank(e["severity"]) > sev_rank(min_sev):
            continue
        s = e["status"].get(version, "n/a")
        if s == "open":
            rows.append((e, ""))
        elif s.startswith("fixed@") and repo is not None:
            inside = fixed_in(repo, s[6:], tag)
            if inside is not True:
                rows.append((e, " (fixed after this release)" if inside is False else
                             " (its fix commit is not in this clone: listed as open)"))
    lines = [f"### Known issues in {tag} (Minecraft {version})", ""]
    if not rows:
        lines.append(f"No known issues at {min_sev} or above.")
    for e, extra in rows:
        lines.append(f"- **{e['severity']}** {e['title']} ({e['id']}){extra}")
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
        out.append(f"| {sev} | {e['id']} | {md(e['title'])} | " + " | ".join(cell(e["status"].get(v)) for v in vs) + " |")
    notes = [f"- {e['id']}: graded {e['override']['severity']} by {e['override'].get('by', 'owner')} "
             f"(the rubric gives {derive(e['rubric'])}): {md(e['override']['reason'])}" for e in issues if e.get("override")]
    notes += [f"- {e['id']} at {v}: won't fix: {md(s[8:])}" for e in issues
              for v, s in sorted(e["status"].items(), key=lambda x: mc_key(x[0])) if s.startswith("wontfix:")]
    if notes:
        out += ["", "\\* an owner override.", ""] + notes
    out += ["", "S1 crash, kick, data loss or hang (blocks publishing); S2 a core feature broken in normal play, no "
            "workaround; S3 cosmetic, uncommon or with a workaround; S4 invisible or an unusual setup."]
    return "\n".join(out) + "\n"


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
    p = common(sub.add_parser("reopens", help="may a finding at a done version reopen it? (S1 only)"))
    p.add_argument("--version", required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--severity", choices=SEVERITIES); g.add_argument("--id")
    return ap


def run(argv):
    out = print
    a = build_parser().parse_args(argv)
    if a.self_check:
        return self_check()
    if not a.cmd:
        build_parser().print_help()
        return 2
    repo = pathlib.Path(a.repo or ".")
    path = pathlib.Path(a.ledger) if a.ledger else repo / ledger_rel(a.mod)
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
            if a.open and (e["status"].get(a.version) != "open" if a.version else not open_versions(e)):
                continue
            rows.append(e)
        if a.json:
            out(json.dumps(rows, sort_keys=True, indent=2, ensure_ascii=False))
        for e in ([] if a.json else rows):
            st = e["status"].get(a.version) if a.version else ", ".join(
                f"{v}={s}" for v, s in sorted(e["status"].items(), key=lambda x: mc_key(x[0])))
            out(f"{e['id']}\t{e['severity']}{'*' if e.get('override') else ''}\tlowest {e['lowest']}\t{st}\t{e['title']}")
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
        text = release_notes(data, a.version, a.tag, a.min, repo if a.repo else None)
        out(text, end="")
        return 0
    if a.cmd == "reopens":
        sev = a.severity or find(data, a.id)["severity"]
        if reopens(sev):
            out(f"yes: an {sev} at {a.version} reopens it; fix it at its lowest affected version and cascade only that fix")
            return 0
        out(f"no: an {sev} never reopens a done version: record it in the ledger at its lowest affected version "
            f"(known-issues.py add ... --lowest <version>)")
        return 1
    return 2


def main(argv=None):
    try:
        return run(sys.argv[1:] if argv is None else argv)
    except Invalid as e:
        print(f"known-issues: {e}", file=sys.stderr)
        return 2


# ---------------------------------------------------------------- self-check (seconds: one tiny git repo)

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

        # the ledger never makes gates or a review stale, and the cascade never carries it: the tools' own lists, on a
        # layer target (--mc) and a single-version fork (no --mc: HEAD, as before -- byte-for-byte)
        cg = load("ci_gates_ki", "ci-gates.py")
        (d / ".port" / LEDGER_NAME).write_text(dump(empty()), encoding="utf-8")         # an edit to the ledger alone
        head = g("rev-parse", "--short", "HEAD")
        expect(cg.tested_tree(d, "1.21.5") == head, "ci-gates: a ledger edit does not make a layer target's tree edited")
        expect(cg.tested_tree(d, None) == head, "ci-gates: a single-version fork's tested tree is HEAD, as before")
        (d / "f.txt").write_text("3\n", encoding="utf-8")
        expect("uncommitted: f.txt" in cg.tested_tree(d, "1.21.5"), "control: a code edit still counts")
        fpm = load("fork_port_ki", "fork-port.py")
        expect(LEDGER_NAME in fpm.REVIEW_OUTPUTS and LEDGER_NAME in fpm.NOT_JAR_FILES, "fork-port: a run output")
        pp = load("port_publish_ki", "port-publish.py")
        expect(all(pp.GATES_DOC.match(f) for f in (".port/KNOWN-ISSUES.json", ".port/examplemod/KNOWN-ISSUES.json"))
               and not any(pp.GATES_DOC.match(f) for f in ("src/main/resources/KNOWN-ISSUES.json", ".port/a/b/KNOWN-ISSUES.json",
                                                          ".port/1.21.4/renames.tsv", "build.gradle")),
               "port-publish: the ledger is not jar content (and nothing else changed)")
        green, why = pp.listed_green("1.21.4", "# Gates at `abc1234` for Minecraft 1.21.4\n| A | PASS |\n",
                                     [".port/KNOWN-ISSUES.json"], ["1.21.4"])
        expect(green, f"a released layer target stays green after a ledger-only commit: {why}")
        fb = load("fork_branches_ki", "fork-branches.py")
        expect(all(fb.LEDGER_PATH.match(f) and not fb.jar_relevant(f, "1.21.4")
                   for f in (".port/KNOWN-ISSUES.json", ".port/examplemod/KNOWN-ISSUES.json"))
               and not fb.LEDGER_PATH.match(".port/1.21.4/branch.properties"),
               "fork-branches: the ledger is no version branch's jar input, and the cascade skips it (LEDGER_PATH)")
        tpl = (root / "templates/upstream-harness/port-ci.yml").read_text(encoding="utf-8")
        push = tpl[tpl.index("  push:"):tpl.index("  pull_request:")]
        expect('paths-ignore: [".port/KNOWN-ISSUES.json", ".port/*/KNOWN-ISSUES.json"]' in push,
               "Port CI's push trigger paths-ignores the ledger")
    took = time.monotonic() - t0
    for b in bad:
        print("FAIL:", b)
    print(f"self-check: {'OK' if not bad else 'FAIL'} ({took:.1f} s)")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
