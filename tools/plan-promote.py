#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Promote by comment: the plan owner's `/promote` on the weekly plan issue dispatches Port CI's promote, per version.

    python3 tools/plan-promote.py --config FILE --event FILE [--slug OWNER/REPO]   # GITHUB_TOKEN, GITHUB_REPOSITORY
    python3 tools/plan-promote.py --self-check

The weekly cycle ends with a person playing the round's pre-releases. Promoting them used to take one
`port-ci.py promote` (or one Port CI workflow_dispatch with promote=<tag> and played_by=<login>) per tag; this makes
it one comment on the round's plan issue. It is the step of `.github/workflows/weekly-plan-promote.yml`
(templates/weekly-plan-promote.yml, installed beside the plan workflow by tools/weekly-plan.py install), which runs
on `issue_comment: created` with no model and no secrets: --event is the event payload ($GITHUB_EVENT_PATH), read as
data, so nothing from the comment reaches a shell. --config is `.github/weekly-plan.json` from the head branch.

  /promote                   every pre-release the round cut: the Minecraft versions of the pre-releases published
                             since the plan issue opened, and of every pre-release tag a comment on the issue names
                             (the round's closing comment lists them); each version's newest tag is promoted
  /promote 1.21.8 26.3       those Minecraft versions only

Only the first line counts, and only a comment on an issue the plan workflow opened (written by github-actions[bot],
carrying the plan's marker and data block); any other comment is left alone, silently. Only the plan owner may
promote: `plan_owner` in the config (a login; for `@org/team`, an active member of that team, read from the API --
the default token may not see team membership, and then nobody is let through), else the repository's owner when that
is a user (known-issues.py resolve_owner, as the plan addresses it). Anyone else gets one reply saying who may
promote, and nothing is dispatched.

Per version: the newest non-draft release whose tag ends `-mc<version>` or `-mc<version>-rN` (newest by creation
time, then N). It is promoted only when it is a pre-release published by Port CI (github-actions[bot]): a newest
build that is already a full Release, or not Port CI's, is refused -- an older pre-release is never promoted over it.
Port CI (.github/workflows/port-ci.yml) is dispatched on `neoforge-<version>` (the head's own version on the head
branch the config names) with promote=<tag>, played_by=<the commenter>, played_notes="promoted from <issue url>";
a dispatch made with the workflow's token does start the run, and Port CI's promote job then checks the tag again and
its publish job uploads it in the same run, as with `port-ci.py promote`. The token reaches this repository only:
the set's other forks are named with the `port-ci.py promote` command to run for them. One reply lists what was
dispatched (tag -> run link) and anything refused or not found. Exit 0 handled (a refusal included); 1 a dispatch or
GitHub call failed (said in the reply when it could be posted); 2 bad input. Standard library only.
"""
import argparse, datetime, json, os, pathlib, re, sys, time

TOOLS = pathlib.Path(__file__).resolve().parent
BOT = "github-actions[bot]"
PORT_CI = "port-ci.yml"
MC = r"\d+\.\d+(?:\.\d+)?"
VERSION_RE = re.compile(rf"^{MC}$")
TAG_RE = re.compile(rf"-mc(?P<mc>{MC})(?:-r(?P<r>\d+))?$")
TAG_WORD = re.compile(rf"[A-Za-z0-9][\w.+-]*-mc{MC}(?:-r\d+)?(?![\w.-])")
LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:-?[A-Za-z0-9]){0,38}$")
HEAD_RE = re.compile(rf"^neoforge-(?P<mc>{MC})$")
USAGE = "`/promote` (every pre-release this round cut) or `/promote 1.21.8 26.3` (those Minecraft versions only)"


class Bad(Exception):
    pass


def ki():
    import importlib.util
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("known_issues_pp", TOOLS / "known-issues.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def mc_key(v):
    return tuple(int(x) for x in v.split("."))


# ---------------------------------------------------------------- the pure parts

def parse_command(body):
    """The comment's first line -> None (not a /promote), [] (every version the round cut) or the versions named.
    Bad for a /promote whose arguments are not Minecraft versions."""
    first = ((body or "").strip().splitlines() or [""])[0].strip()
    m = re.match(r"^/promote(?=\s|$)(.*)$", first)
    if not m:
        return None
    words = [w for w in re.split(r"[\s,]+", m.group(1).strip()) if w]
    bad = [w for w in words if not VERSION_RE.match(w)]
    if bad:
        raise Bad(f"`{bad[0][:40]}` is not a Minecraft version")
    return sorted(set(words), key=mc_key)


def is_plan_issue(issue, k):
    """Opened by the plan workflow: written by the bot, with the plan's marker and a readable data block."""
    if "pull_request" in issue or (issue.get("user") or {}).get("login") != BOT:
        return False
    try:
        k.plan_data(issue.get("body") or "")
        return True
    except k.Invalid:
        return False


def owner_of(cfg, repo, k):
    """The plan owner, as the plan addresses it: {"login"} or {"team": "org/team"}. Invalid when there is none."""
    o = repo.get("owner") or {}
    return k.resolve_owner(cfg.get("plan_owner"), o.get("login"), o.get("type"))


def may_promote(owner, login, api):
    """(allowed, note): the owner's login (any case), or an active member of the owner team."""
    if not login:
        return False, ""
    if owner.get("login"):
        return login.lower() == owner["login"].lower(), ""
    org, team = owner["team"].split("/", 1)
    try:
        return (api(f"/orgs/{org}/teams/{team}/memberships/{login}") or {}).get("state") == "active", ""
    except Exception as e:      # noqa: BLE001 -- not a member, or the token cannot read the team: either way, no
        return False, f" (the team's membership could not be confirmed with this workflow's token: {str(e)[:120]})"


def who_may(owner):
    return f"@{owner['login']}" if owner.get("login") else f"a member of @{owner['team']}"


def tag_mc(tag):
    m = TAG_RE.search(tag or "")
    return (m.group("mc"), int(m.group("r") or 0)) if m else (None, 0)


def select_tag(releases, version):
    """(tag, None) to promote, or (None, why). The newest non-draft release for `version`; only a Port CI
    pre-release is promoted."""
    rows = [r for r in releases if not r.get("draft") and tag_mc(r.get("tag_name"))[0] == version]
    if not rows:
        return None, f"no release tagged `…-mc{version}` or `…-mc{version}-rN`"
    r = max(rows, key=lambda r: (r.get("created_at") or r.get("published_at") or "", tag_mc(r["tag_name"])[1]))
    t = r["tag_name"]
    if not r.get("prerelease"):
        return None, f"the newest release, `{t}`, is already a full Release: nothing to promote"
    if (r.get("author") or {}).get("login") != BOT:
        return None, f"the newest pre-release, `{t}`, was not published by Port CI"
    return t, None


def round_versions(releases, comments, since):
    """The Minecraft versions of the pre-releases the round cut: published since the plan issue opened, or named by
    a comment on it (the closing comment lists them)."""
    pre = {r["tag_name"]: r for r in releases if r.get("prerelease") and not r.get("draft") and tag_mc(r.get("tag_name"))[0]}
    named = {w for c in comments for w in TAG_WORD.findall(c.get("body") or "")}
    vs = {tag_mc(t)[0] for t, r in pre.items() if (r.get("created_at") or "") >= (since or "") or t in named}
    return sorted(vs, key=mc_key)


def branch_for(version, head):
    m = HEAD_RE.match(head or "")
    return head if m and m.group("mc") == version else f"neoforge-{version}"


# ---------------------------------------------------------------- GitHub

def dispatch(api, slug, branch, inputs):
    """Dispatch Port CI; -> the run's details when GitHub returns them, else {}. A 4xx refusal of the
    return_run_details flag (older API) is retried once without it; nothing else is retried."""
    path = f"/repos/{slug}/actions/workflows/{PORT_CI}/dispatches"
    try:
        return api(path, "POST", {"ref": branch, "inputs": inputs, "return_run_details": True}) or {}
    except Exception as e:      # noqa: BLE001
        if not re.search(r"\b(400|422)\b", str(e)):
            raise
    return api(path, "POST", {"ref": branch, "inputs": inputs}) or {}


def run_link(api, slug, branch, since, taken, sleep=time.sleep, tries=6):
    """The workflow_dispatch run of Port CI on `branch` created at or after `since` (not one already linked)."""
    for i in range(tries):
        try:
            rows = api(f"/repos/{slug}/actions/workflows/{PORT_CI}/runs?event=workflow_dispatch&branch={branch}"
                       "&per_page=10").get("workflow_runs") or []
        except Exception:       # noqa: BLE001 -- the link is a convenience: the generic one stands in
            rows = []
        for r in sorted(rows, key=lambda r: r.get("created_at") or ""):
            if (r.get("created_at") or "") >= since and r.get("html_url") and r["html_url"] not in taken:
                return r["html_url"]
        if i + 1 < tries:
            sleep(5)
    return None


def handle(event, cfg, slug, api, k, sleep=time.sleep, now=None):
    """-> (reply text or None, exit code). None: not ours, said on stdout only."""
    issue, comment, repo = event.get("issue") or {}, event.get("comment") or {}, event.get("repository") or {}
    user = comment.get("user") or {}
    if user.get("type") == "Bot":
        print("plan-promote: a bot's comment: nothing to do")
        return None, 0
    try:
        versions = parse_command(comment.get("body"))
        bad = None
    except Bad as e:
        versions, bad = [], str(e)
    if versions is None:
        print("plan-promote: not a /promote comment: nothing to do")
        return None, 0
    if not is_plan_issue(issue, k):
        print(f"plan-promote: #{issue.get('number')} is not a weekly plan issue: nothing to do")
        return None, 0
    login = user.get("login") or ""
    try:
        owner = owner_of(cfg, repo, k)
    except k.Invalid as e:
        return f"Nothing was promoted: this plan has no owner to promote for ({e}).", 0
    ok, note = may_promote(owner, login, api)
    if not ok:
        return (f"Only {who_may(owner)}, the plan owner (`plan_owner` in `.github/weekly-plan.json`), may promote from "
                f"this issue{note}. Nothing was dispatched."), 0
    if bad or not LOGIN_RE.match(login):
        return f"Nothing was promoted: {bad or 'the commenter is not a GitHub login'}. Write {USAGE}.", 0
    releases = k.gh_pages(f"/repos/{slug}/releases", api)
    url = issue.get("html_url") or f"https://github.com/{slug}/issues/{issue.get('number')}"
    asked = versions
    if not asked:
        comments = k.gh_pages(f"/repos/{slug}/issues/{issue['number']}/comments", api)
        asked = round_versions(releases, comments, issue.get("created_at") or "")
        if not asked:
            return (f"Nothing was promoted: no pre-release was published since this plan opened and no comment here "
                    f"names one. Name the versions: `/promote 1.21.8 26.3`."), 0
    head = ((cfg.get("forks") or [{}])[0]).get("head") or ""
    lines, rc, taken = [], 0, set()
    for v in asked:
        tag, why = select_tag(releases, v)
        if not tag:
            lines.append(f"- {v}: not promoted: {why}")
            continue
        br = branch_for(v, head)
        since = (now or datetime.datetime.now(datetime.timezone.utc)).replace(microsecond=0) - datetime.timedelta(seconds=10)
        since = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        inputs = {"promote": tag, "played_by": login, "played_notes": f"promoted from {url}"}
        try:
            got = dispatch(api, slug, br, inputs)
        except Exception as e:  # noqa: BLE001
            lines.append(f"- {v}: `{tag}`: the dispatch of Port CI on `{br}` failed: {str(e)[:200]}")
            rc = 1
            continue
        link = got.get("html_url") or run_link(api, slug, br, since, taken, sleep)
        taken.add(link)
        lines.append(f"- {v}: `{tag}` -> Port CI promote on `{br}`: "
                     + (link or f"https://github.com/{slug}/actions/workflows/{PORT_CI} (its newest run)"))
    others = [f["repo"] for f in (cfg.get("forks") or [])[1:] if f.get("repo")]
    out = [f"Promote requested by @{login} (" + ("every pre-release this round cut" if not versions else
                                                  "Minecraft " + ", ".join(versions)) + "):", ""] + lines
    out += ["", "Port CI's promote job checks each tag again (a pre-release it published from that branch), makes it a "
                "full Release recording who played it, and, where the fork publishes, uploads it to Modrinth and "
                "CurseForge in the same run."]
    if others:
        out += ["", "This workflow's token reaches this repository only. The set's other forks ("
                + ", ".join(f"`{o}`" for o in others) + ") are promoted with `python3 tools/port-ci.py promote --fork "
                "<owner/repo> --tag <tag> --played-by " + login + "` (a dependent never ahead of its library)."]
    return "\n".join(out), rc


def main(argv=None):
    ap = argparse.ArgumentParser(prog="plan-promote.py", description=__doc__.split("\n")[0])
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--config"); ap.add_argument("--event"); ap.add_argument("--slug")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if a.self_check:
        return self_check()
    if not (a.config and a.event):
        ap.print_help()
        return 2
    try:
        cfg = json.loads(pathlib.Path(a.config).read_text(encoding="utf-8"))
        event = json.loads(pathlib.Path(a.event).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"plan-promote: {e}", file=sys.stderr)
        return 2
    slug = a.slug or os.environ.get("GITHUB_REPOSITORY") or (event.get("repository") or {}).get("full_name")
    if not slug:
        print("plan-promote: no repository (GITHUB_REPOSITORY or --slug)", file=sys.stderr)
        return 2
    k = ki()
    try:
        reply, rc = handle(event, cfg, slug, k.gh_api, k)
    except Exception as e:      # noqa: BLE001 -- a GitHub read failed: say so on the issue, fail the run
        reply, rc = f"Nothing was promoted: a GitHub call failed ({str(e)[:300]}).", 1
    if reply is None:
        return rc
    n = (event.get("issue") or {}).get("number")
    try:
        k.gh_api(f"/repos/{slug}/issues/{n}/comments", "POST", {"body": reply})
    except Exception as e:      # noqa: BLE001
        print(f"plan-promote: could not reply on #{n}: {e}", file=sys.stderr)
        rc = 1
    print(reply)
    return rc


# ---------------------------------------------------------------- self-check

def self_check():
    bad = []
    expect = lambda c, what: c or bad.append(what)
    k = ki()
    # the command: the first line only, versions validated, everything else not ours
    expect(parse_command("/promote") == [] and parse_command("  /promote  \nthanks") == [],
           "`/promote` alone: every version the round cut")
    expect(parse_command("/promote 26.3 1.21.8 1.21.8") == ["1.21.8", "26.3"]
           and parse_command("/promote 1.21.8, 26.3") == ["1.21.8", "26.3"], "`/promote 1.21.8 26.3`: those, once each")
    expect(all(parse_command(b) is None for b in ("/promoted", "please /promote", "", None, "/grade KI-1 S2",
                                                     "ok\n/promote")), "anything else is not a /promote")
    for g in ("/promote latest", "/promote 1.21.8 $(id)", "/promote 1", "/promote v1.21.8", "/promote 1.21.8.1.2"):
        try:
            parse_command(g); bad.append(f"garbage is refused: {g!r}")
        except Bad:
            pass
    # the plan issue: the bot's, with the marker and the data block
    body = k.PLAN_MARK + "\n" + k.data_block({"schema": 1, "forks": []})
    plan = {"number": 7, "user": {"login": BOT}, "body": body, "created_at": "2026-11-17T15:00:00Z",
            "html_url": "https://github.com/o/lib/issues/7"}
    expect(is_plan_issue(plan, k), "the plan workflow's issue is a plan issue")
    expect(not is_plan_issue(dict(plan, user={"login": "someone"}), k), "a person's issue with the marker is not")
    expect(not is_plan_issue(dict(plan, body="no marker"), k), "a bot issue without the marker is not")
    expect(not is_plan_issue(dict(plan, pull_request={}), k), "a pull request is not")
    # the owner: a login (any case), a team member, nobody else; a write collaborator is not the owner
    calls = []

    def team_api(path, method="GET", data=None):
        calls.append(path)
        if path == "/orgs/org/teams/maint/memberships/member":
            return {"state": "active"}
        if path == "/orgs/org/teams/maint/memberships/pending":
            return {"state": "pending"}
        raise RuntimeError("HTTP Error 404: Not Found")
    expect(may_promote({"login": "Owner"}, "owner", team_api)[0] and not may_promote({"login": "owner"}, "writer", team_api)[0]
           and not may_promote({"login": "owner"}, "", team_api)[0], "a login owner: that login only")
    expect(may_promote({"team": "org/maint"}, "member", team_api)[0]
           and not may_promote({"team": "org/maint"}, "pending", team_api)[0]
           and not may_promote({"team": "org/maint"}, "stranger", team_api)[0]
           and "could not be confirmed" in may_promote({"team": "org/maint"}, "stranger", team_api)[1],
           "a team owner: an active member only; an unreadable membership lets nobody through")
    repo = {"full_name": "o/lib", "owner": {"login": "o", "type": "User"}}
    expect(owner_of({"plan_owner": "someone"}, repo, k) == {"login": "someone"} and owner_of({}, repo, k) == {"login": "o"}
           and owner_of({"plan_owner": "@org/maint"}, repo, k) == {"team": "org/maint"},
           "plan_owner from the config, else a user-owned repository's owner")
    # tag selection: per version, the newest non-draft release; -rN; only a Port CI pre-release
    R = lambda t, c, pre=True, **kw: dict({"tag_name": t, "created_at": c, "prerelease": pre, "draft": False,
                                            "author": {"login": BOT}}, **kw)
    rel = [R("examplemod-1.0.0-mc1.21.8", "2026-11-01T00:00:00Z", pre=False),
           R("examplemod-1.0.1-mc1.21.8", "2026-11-18T10:00:00Z"),
           R("examplemod-1.0.1-mc1.21.8-r2", "2026-11-18T12:00:00Z"),
           R("examplemod-1.0.1-mc1.21.8-r3", "2026-11-19T00:00:00Z", draft=True),
           R("examplemod-1.0.1-mc1.21.80", "2026-11-20T00:00:00Z"),
           R("examplemod-1.0.1-mc1.21.8-release", "2026-11-20T00:00:00Z"),
           R("examplemod-1.0.1-mc26.3", "2026-11-18T11:00:00Z"),
           R("examplemod-1.0.2-mc26.3", "2026-11-18T13:00:00Z", pre=False),
           R("examplemod-1.0.1-mc26.2", "2026-11-18T11:00:00Z", author={"login": "someone"}),
           R("examplemod-1.0.1-mc1.21.1", "2026-11-10T00:00:00Z"),
           R("examplemod-1.0.1-mc1.21.1-r10", "2026-11-10T00:00:00Z"),
           R("examplemod-1.0.1-mc1.21.1-r9", "2026-11-10T00:00:00Z")]
    expect(select_tag(rel, "1.21.8") == ("examplemod-1.0.1-mc1.21.8-r2", None),
           f"1.21.8: the newest -rN pre-release, not a draft, not -mc1.21.80, not -release: {select_tag(rel, '1.21.8')}")
    expect(select_tag(rel, "1.21.1")[0] == "examplemod-1.0.1-mc1.21.1-r10", "the same time: the higher -rN")
    t, why = select_tag(rel, "26.3")
    expect(t is None and "already a full Release" in why, f"never a non-pre-release, nor an older one over it: {why}")
    t, why = select_tag(rel, "26.2")
    expect(t is None and "not published by Port CI" in why, f"a pre-release Port CI did not publish is refused: {why}")
    expect(select_tag(rel, "1.20.1")[0] is None and "no release" in select_tag(rel, "1.20.1")[1], "none found: said")
    since = "2026-11-17T15:00:00Z"
    expect(round_versions(rel, [], since) == ["1.21.8", "1.21.80", "26.2", "26.3"],
           f"/promote: the pre-releases since the plan opened: {round_versions(rel, [], since)}")
    expect(round_versions(rel, [{"body": "Pre-releases: examplemod-1.0.1-mc1.21.1-r9, done."}], since)[0] == "1.21.1",
           "/promote: a pre-release tag the closing comment names, from before the plan, counts too")
    expect(branch_for("1.21.8", "neoforge-1.21.8") == "neoforge-1.21.8" and branch_for("26.3", "neoforge-1.21.8") ==
           "neoforge-26.3" and branch_for("1.21.1", "main-port") == "neoforge-1.21.1", "the branch per version")

    # end to end against a fake GitHub
    def fake(releases, refuse_details=False, runs=None):
        log = []

        def api(path, method="GET", data=None):
            log.append((method, path, data))
            p = path.split("?")[0]
            if p == "/repos/o/lib/releases":
                return releases if "page=1" in path else []
            if p == "/repos/o/lib/issues/7/comments" and method == "GET":
                return [] if "page=1" in path else []
            if p.endswith("/dispatches"):
                if refuse_details and "return_run_details" in data:
                    raise RuntimeError("HTTP Error 422: Unprocessable Entity")
                return {} if refuse_details else {"workflow_run_id": 1, "html_url": f"https://github.com/o/lib/actions/runs/{data['ref']}"}
            if p.endswith("/runs"):
                return {"workflow_runs": runs or []}
            return team_api(path, method, data)
        return api, log
    cfg = {"plan_owner": "someone", "forks": [{"repo": "o/lib", "head": "neoforge-1.21.8"}, {"repo": "o/dep", "head": "neoforge-1.21.8"}]}
    ev = lambda body, login="someone", **kw: {"issue": dict(plan, **kw), "comment": {"body": body, "user": {"login": login, "type": "User"}},
                                               "repository": repo}
    api, log = fake(rel)
    reply, rc = handle(ev("/promote 1.21.8 26.3"), cfg, "o/lib", api, k, sleep=lambda s: None)
    d = [x for x in log if x[1].endswith("/dispatches")]
    expect(rc == 0 and len(d) == 1 and d[0][2]["ref"] == "neoforge-1.21.8"
           and d[0][2]["inputs"] == {"promote": "examplemod-1.0.1-mc1.21.8-r2", "played_by": "someone",
                                     "played_notes": "promoted from https://github.com/o/lib/issues/7"}
           and "`examplemod-1.0.1-mc1.21.8-r2` -> Port CI promote on `neoforge-1.21.8`: https://github.com/o/lib/actions/runs/neoforge-1.21.8" in reply
           and "26.3: not promoted" in reply and "o/dep" in reply, f"owner, two versions: one dispatched, one refused: {rc} {reply} {d}")
    api, log = fake(rel)
    reply, rc = handle(ev("/promote 26.3", login="writer"), cfg, "o/lib", api, k)
    expect(reply.startswith("Only @someone") and not [x for x in log if x[0] == "POST"],
           f"anyone else: one reply naming the owner, nothing dispatched: {reply}")
    api, log = fake(rel)
    reply, rc = handle(ev("/promote soon"), cfg, "o/lib", api, k)
    expect("not a Minecraft version" in reply and not [x for x in log if x[0] == "POST"], f"garbage: usage, nothing run: {reply}")
    api, log = fake(rel)
    expect(handle(ev("/promote 1.21.8", user={"login": "someone"}), cfg, "o/lib", api, k) == (None, 0)
           and handle(dict(ev("/promote"), comment={"body": "/promote", "user": {"login": "x", "type": "Bot"}}), cfg,
                      "o/lib", api, k) == (None, 0)
           and handle(ev("lgtm"), cfg, "o/lib", api, k) == (None, 0) and not log,
           "not a plan issue, a bot, or not /promote: silent, nothing read")
    now = datetime.datetime(2026, 11, 25, 12, 0, tzinfo=datetime.timezone.utc)
    api, log = fake(rel, refuse_details=True, runs=[{"created_at": "2026-11-25T11:00:00Z", "html_url": "old"},
                                                   {"created_at": "2026-11-25T12:00:01Z", "html_url": "new"}])
    reply, rc = handle(ev("/promote"), cfg, "o/lib", api, k, sleep=lambda s: None, now=now)
    d = [x for x in log if x[1].endswith("/dispatches")]
    expect(rc == 0 and len(d) == 4 and [x[2]["inputs"]["promote"] for x in d[1::2]] ==
           ["examplemod-1.0.1-mc1.21.8-r2", "examplemod-1.0.1-mc1.21.80"]
           and "1.21.8-r2` -> Port CI promote on `neoforge-1.21.8`: new" in reply and "26.3: not promoted" in reply
           and "26.2: not promoted" in reply and "every pre-release this round cut" in reply,
           f"/promote: each version the round cut; without run details the run is found by time: {reply} {d}")
    api, log = fake([R("examplemod-1.0.0-mc1.21.8", "2026-11-01T00:00:00Z")])
    reply, rc = handle(ev("/promote"), cfg, "o/lib", api, k)
    expect("no pre-release was published since this plan opened" in reply and not [x for x in log if x[0] == "POST"],
           f"/promote with nothing new: said, nothing dispatched: {reply}")
    for b in bad:
        print("FAIL:", b)
    print(f"plan-promote self-check: {'OK' if not bad else 'FAIL'}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
