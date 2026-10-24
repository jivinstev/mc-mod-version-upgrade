#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Are a fork clone's local branches what origin has? Fetch origin, fast-forward a branch strictly behind it, refuse
one that has diverged -- never a reset, never a force.

    python3 tools/origin-sync.py --self-check

A library, no command of its own: tools/port-sync.py runs it on the port branch before it merges the authors' code
(every kind of fork), and tools/fork-branches.py's cascade runs it on the branches it reads (version-branch forks). A
stale local branch (a PR merged on origin, the clone not pulled) gives a merge a wrong merge base. Standard library only.
"""
import pathlib, subprocess, sys, tempfile


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, encoding="utf-8",
                          errors="surrogateescape")


def branch_worktrees(repo):
    """{branch name: worktree path} of every checkout (the main one included) that has a branch checked out."""
    out, cur = {}, None
    for ln in git(repo, "worktree", "list", "--porcelain").stdout.splitlines():
        if ln.startswith("worktree "):
            cur = ln[len("worktree "):]
        elif ln.startswith("branch refs/heads/") and cur:
            out[ln[len("branch refs/heads/"):]] = cur
    return out


def origin_sync(repo, names, change=True, say=print, tool="fork-branches", again="run the cascade again"):
    """Before a merge reads its branches: are the local ones what origin has? Fetches origin, then per branch: the
    same, or ahead of origin (a merge not pushed yet), or local only -> used as it is; strictly behind -> fast-forwarded
    (change; a checked-out branch only when its checkout is clean), else origin's commit used in its place (a plan's
    throwaway clone, a dry run); diverged, or behind with a dirty checkout -> refused. No `origin` remote: nothing to
    check. `tool` prefixes what is said, `again` ends a refusal (what to run once it is fixed).
    -> ({name: origin's commit} for the branches to read at origin's commit, None) or (None, why refused)."""
    if git(repo, "remote", "get-url", "origin").returncode:
        return {}, None
    f = git(repo, "fetch", "-q", "origin")
    if f.returncode:
        say(f"{tool}: cannot fetch origin ({f.stderr.strip()[-200:]}): comparing with origin's refs as last fetched")
    over, wts = {}, None
    for name in dict.fromkeys(names):
        loc = git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}^{{commit}}").stdout.strip()
        org = git(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{name}^{{commit}}").stdout.strip()
        if not loc or not org or loc == org or git(repo, "merge-base", "--is-ancestor", org, loc).returncode == 0:
            continue
        behind = git(repo, "rev-list", "--count", f"{loc}..{org}").stdout.strip()
        if git(repo, "merge-base", "--is-ancestor", loc, org).returncode != 0:
            ahead = git(repo, "rev-list", "--count", f"{org}..{loc}").stdout.strip()
            return None, (f"the local {name} ({loc[:12]}) has diverged from origin/{name} ({org[:12]}): {ahead} commit(s) "
                          f"only here, {behind} only on origin. Its merges would start from a wrong merge base: merge "
                          f"origin/{name} into it (or reset it to origin/{name} if the local commits are not wanted), "
                          f"then {again}")
        if not change:
            over[name] = org
            say(f"{tool}: the local {name} is {behind} commit(s) behind origin/{name}: reading origin's {org[:12]}")
            continue
        if wts is None:
            wts = branch_worktrees(repo)
        wt = wts.get(name)
        if wt is None:
            r = git(repo, "update-ref", f"refs/heads/{name}", org, loc)
        elif git(wt, "status", "--porcelain", "--untracked-files=no").stdout.strip():
            return None, (f"the local {name} is {behind} commit(s) behind origin/{name} and its checkout {wt} has "
                          f"uncommitted changes: commit or stash them, git -C {wt} merge --ff-only origin/{name}, then "
                          f"{again}")
        else:
            r = git(wt, "merge", "--ff-only", "-q", org)
        if r.returncode:
            return None, (f"cannot fast-forward the local {name} to origin/{name} ({org[:12]}): "
                          f"{(r.stderr or r.stdout).strip()[-200:]}")
        say(f"{tool}: fast-forwarded the local {name} {behind} commit(s) to origin/{name} ({org[:12]})")
    return over, None


def self_check():
    bad = []
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        up, c = d / "up", d / "clone"
        g = lambda r, *a: subprocess.run(["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                                          *a], capture_output=True, text=True, check=True).stdout.strip()
        up.mkdir(); g(up, "init", "-q", "-b", "neoforge-1.21.1")
        (up / "A").write_text("a\n", encoding="utf-8"); g(up, "add", "-A"); g(up, "commit", "-qm", "a")
        subprocess.run(["git", "clone", "-q", str(up), str(c)], check=True, capture_output=True)
        said = []
        if origin_sync(c, ["neoforge-1.21.1"], say=said.append) != ({}, None) or said:
            bad.append(f"the same as origin: nothing done, nothing said: {said}")
        (up / "A").write_text("b\n", encoding="utf-8"); g(up, "commit", "-qam", "b")
        tip = g(up, "rev-parse", "HEAD")
        said = []
        over, why = origin_sync(c, ["neoforge-1.21.1"], change=False, say=said.append, tool="port-sync")
        if why or over != {"neoforge-1.21.1": tip} or g(c, "rev-parse", "HEAD") == tip or "port-sync: the local" not in said[0]:
            bad.append(f"behind, change=False: origin's commit named, nothing moved: {over} {why} {said}")
        (c / "A").write_text("dirty\n", encoding="utf-8")
        over, why = origin_sync(c, ["neoforge-1.21.1"], say=said.append, again="run port-sync again")
        if over is not None or "uncommitted" not in why or not why.endswith("run port-sync again"):
            bad.append(f"behind with a dirty checkout: refused: {why}")
        g(c, "checkout", "-q", "--", "A")
        said = []
        over, why = origin_sync(c, ["neoforge-1.21.1"], say=said.append)
        if why or g(c, "rev-parse", "HEAD") != tip or "fast-forwarded" not in said[-1]:
            bad.append(f"behind and clean: fast-forwarded: {why} {said}")
        (c / "B").write_text("x\n", encoding="utf-8"); g(c, "add", "-A"); g(c, "commit", "-qm", "local")
        if origin_sync(c, ["neoforge-1.21.1"], say=said.append) != ({}, None):
            bad.append("ahead of origin: used as it is")
        (up / "A").write_text("c\n", encoding="utf-8"); g(up, "commit", "-qam", "c")
        head = g(c, "rev-parse", "HEAD")
        over, why = origin_sync(c, ["neoforge-1.21.1"], say=said.append)
        if over is not None or "has diverged" not in why or g(c, "rev-parse", "HEAD") != head:
            bad.append(f"diverged: refused, nothing moved (no reset): {why}")
        g(c, "remote", "remove", "origin")
        if origin_sync(c, ["neoforge-1.21.1"]) != ({}, None):
            bad.append("no origin: nothing to check")
    for m in bad:
        print("FAIL:", m)
    print("self-check:", "OK" if not bad else "FAILED")
    return 0 if not bad else 1


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        sys.exit(self_check())
    print(__doc__.strip().split("\n")[0]); sys.exit(2)
