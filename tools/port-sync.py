#!/usr/bin/env python3
"""Keep a port branch current with its author: bring in their new commits, then say what is left to port.

    python3 tools/port-sync.py --repo <fork clone> --port <port branch> --upstream <remote>/<branch> [--dry-run]
    python3 tools/port-sync.py --self-check

A fork carries two port branches (see the port-fork skill):
  neoforge-<mc>          the DEVELOPMENT port, on the author's newest code. port-sync keeps it current.
  neoforge-<mc>-release  the port of what the author RELEASED (tools/port-derive.py). It moves only when the author
                         releases again: re-run port-derive with the new release commit, never port-sync.

WHAT IT DOES (a merge, never a rebase: the branch is shared, and its history is what the author reviews):
  1. fetch the remote and list the author's commits not yet on the port branch (subjects, files, +/- lines);
  2. merge them into the port branch. A conflict stops here with the files listed -- both sides edited the same
     lines, which is a person's call (or a model's, with --dry-run first to see the size);
  3. compile and count (tools/burndown-count.sh): every error is new author code still written for the old target.
     Re-run the port pipeline's mechanical stage on it, then the gates (tools/ci-gates.py), then push.
Nothing is pushed by this tool. Standard library only.
"""
import argparse, pathlib, re, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent


def git(repo, *a, check=True):
    r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode:
        sys.exit(f"git {' '.join(a)}: {(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout if check else r


def incoming(repo, port, upstream):
    rows = [l.split("\t", 2) for l in git(repo, "log", "--no-merges", "--format=%h\t%cs\t%s",
                                          f"{port}..{upstream}").splitlines() if l]
    base = git(repo, "merge-base", port, upstream).strip()
    stat = git(repo, "diff", "--shortstat", base, upstream).strip()
    return rows, stat


def sync(repo, port, upstream, dry=False, out=print):
    remote = upstream.split("/", 1)[0]
    if remote in git(repo, "remote").split():
        git(repo, "fetch", "-q", remote)
    rows, stat = incoming(repo, port, upstream)
    if not rows:
        out(f"port-sync: {port} already has everything on {upstream}")
        return 0
    out(f"port-sync: {len(rows)} author commit(s) on {upstream} not on {port} ({stat or 'no file changes'}):")
    for sha, day, subj in rows[:15]:
        out(f"   {sha} {day} {subj}")
    if len(rows) > 15:
        out(f"   ... and {len(rows) - 15} more")
    if dry:
        return 0
    if git(repo, "status", "--porcelain").strip():
        sys.exit("port-sync: the working tree has uncommitted changes; commit or stash them first")
    git(repo, "checkout", "-q", port)
    r = git(repo, "merge", "--no-ff", "-m", f"Merge {upstream} into {port}", upstream, check=False)
    if r.returncode:
        files = git(repo, "diff", "--name-only", "--diff-filter=U").split()
        out(f"port-sync: CONFLICT in {len(files)} file(s) -- both the author and the port changed the same lines:")
        for f in files:
            out(f"   {f}")
        out("   resolve each keeping the author's change written in the port's API, then `git commit`;"
            " or `git merge --abort` to undo.")
        return 2
    out(f"port-sync: merged ({git(repo, 'rev-parse', '--short', 'HEAD').strip()}). Next:")
    out("   1. compile: tools/burndown-count.sh -- each error is new author code still on the old target;")
    out("   2. port it with the pipeline's mechanical stage (tools/port-upstream.py), then fix what is left;")
    out("   3. gates: tools/ci-gates.py --repo <clone> ...; then push (CI re-runs them and releases on a tag).")
    return 0


def self_check():
    ok = True
    with tempfile.TemporaryDirectory() as d:
        up, fork = pathlib.Path(d, "up"), pathlib.Path(d, "fork")
        def g(r, *a):
            subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True)
        up.mkdir(); g(up, "init", "-q", "-b", "main")
        for r in (up,):
            g(r, "config", "user.email", "a@a"); g(r, "config", "user.name", "a")
        (up / "A.java").write_text("a\n", encoding="utf-8"); g(up, "add", "-A"); g(up, "commit", "-qm", "base")
        subprocess.run(["git", "clone", "-q", str(up), str(fork)], check=True)
        g(fork, "config", "user.email", "a@a"); g(fork, "config", "user.name", "a")
        g(fork, "checkout", "-qb", "neoforge-1.21.1"); (fork / "A.java").write_text("a ported\n", encoding="utf-8")
        g(fork, "commit", "-qam", "port")
        (up / "B.java").write_text("new feature\n", encoding="utf-8"); g(up, "add", "-A"); g(up, "commit", "-qm", "Add B")
        log = []
        rc = sync(fork, "neoforge-1.21.1", "origin/main", dry=True, out=log.append)
        ok &= rc == 0 and any("Add B" in l for l in log) and "1 author commit" in log[0]
        log = []
        rc = sync(fork, "neoforge-1.21.1", "origin/main", out=log.append)
        ok &= rc == 0 and (fork / "B.java").exists() and (fork / "A.java").read_text(encoding="utf-8") == "a ported\n"
        log = []
        ok &= sync(fork, "neoforge-1.21.1", "origin/main", out=log.append) == 0 and "already has" in log[0]
        (up / "A.java").write_text("a changed upstream\n", encoding="utf-8"); g(up, "commit", "-qam", "Change A")
        log = []
        ok &= sync(fork, "neoforge-1.21.1", "origin/main", out=log.append) == 2 and any("A.java" in l for l in log)
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo"); ap.add_argument("--port"); ap.add_argument("--upstream")
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not (a.repo and a.port and a.upstream):
        ap.error("--repo, --port and --upstream are required")
    return sync(a.repo, a.port, a.upstream, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
