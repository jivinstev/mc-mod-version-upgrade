#!/usr/bin/env python3
"""Keep a port branch current with its author: bring in their new commits, then say what is left to port.

    python3 tools/port-sync.py --repo <fork clone> --port <port branch> --upstream <remote>/<branch> [--dry-run]
                               [--commit SHA] [--base <port branch>]       # --port a work branch, e.g. keep-current/<date>
    python3 tools/port-sync.py --self-check

A fork carries two port branches (see the port-fork skill):
  neoforge-<mc>          the DEVELOPMENT port, on the author's newest code. port-sync keeps it current.
  neoforge-<mc>-release  the port of what the author RELEASED (tools/port-derive.py). It moves only when the author
                         releases again: re-run port-derive with the new release commit, never port-sync.

WHAT IT DOES (a merge, never a rebase: the branch is shared, and its history is what the author reviews):
  0. compare the local port branch with origin's (tools/origin-sync.py, as the version-branch cascade does): strictly
     behind -> fast-forwarded (--dry-run: origin's commit is read, nothing moves); diverged -> refused, exit 2, with
     what to do. Never a reset or a force. With --base, --port is a work branch (keep-current/<date>): the BASE
     (the port branch) is the one checked, the work branch is cut from it when it does not exist yet, and an existing
     one that lacks the base's tip is refused (it was cut from a stale base);
  1. fetch the remote and list the author's commits not yet on the port branch (subjects, files, +/- lines), up to
     the tip of --upstream, or up to --commit SHA (the weekly round's frozen upstream_commit), which must be reachable
     from it;
  2. merge them (that commit, never a newer tip) into the port branch, as "Merge <upstream> at <sha> into <port>". A conflict stops here with the files listed -- both sides edited the same
     lines, which is a person's call (or a model's, with --dry-run first to see the size);
  3. compile and count (tools/burndown-count.sh): every error is new author code still written for the old target.
     Re-run the port pipeline's mechanical stage on it, then the gates (tools/ci-gates.py), then push.
Nothing is pushed by this tool. Standard library only.
"""
import argparse, os, pathlib, re, subprocess, sys, tempfile

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


def origin_check(repo, names, dry, out):
    """The branches about to be read against origin's (tools/origin-sync.py, the version-branch cascade's check):
    strictly behind -> fast-forwarded (a dry run reads origin's commit instead and moves nothing); diverged, or behind
    with uncommitted changes -> refused. -> ({name: the ref to read it at}, None) or (None, why)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("origin_sync_ps", HERE / "origin-sync.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    over, why = m.origin_sync(repo, names, change=not dry, say=out, tool="port-sync", again="run port-sync again")
    return (None, why) if why else ({n: (over or {}).get(n, n) for n in names}, None)


def has_branch(repo, name):
    return git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}^{{commit}}", check=False).returncode == 0


def sync(repo, port, upstream, dry=False, out=print, commit=None, base=None):
    """Merge `upstream`'s tip -- or exactly `commit`, which must be reachable from it (the weekly round's frozen
    upstream_commit) -- into `port`. `base`: the port branch a work branch `port` is cut from; it is checked against
    origin (and `port` cut from it when it does not exist yet), and an existing `port` must contain it."""
    base = base if base and base != port else None
    at, why = origin_check(repo, [base] if base else [port], dry, out)
    if why:
        out(f"port-sync: REFUSED: {why}")
        return 2
    if base:
        if not has_branch(repo, base):
            out(f"port-sync: REFUSED: --base {base} is not a local branch"); return 2
        if has_branch(repo, port):
            if git(repo, "merge-base", "--is-ancestor", at[base], port, check=False).returncode:
                out(f"port-sync: REFUSED: the work branch {port} was cut from an older {base} (it lacks "
                    f"{git(repo, 'rev-parse', '--short', at[base]).strip()}): merge {base} into it, or delete it and "
                    "run port-sync again to cut it afresh")
                return 2
            at[port] = port
        elif dry:
            at[port] = at[base]
            out(f"port-sync: {port} does not exist yet: it will be cut from {base} ({git(repo, 'rev-parse', '--short', at[base]).strip()})")
        else:
            git(repo, "branch", port, base)
            out(f"port-sync: cut {port} from {base} ({git(repo, 'rev-parse', '--short', base).strip()})")
            at[port] = port
    remote = upstream.split("/", 1)[0]
    if remote in git(repo, "remote").split():
        git(repo, "fetch", "-q", remote)
    target = upstream
    if commit:
        r = git(repo, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}", check=False)
        sha = r.stdout.strip()
        if r.returncode or git(repo, "merge-base", "--is-ancestor", sha, upstream, check=False).returncode:
            out(f"port-sync: REFUSED: --commit {commit} is not a commit reachable from {upstream} (after fetching "
                f"{remote}): name one of the authors' commits on that branch")
            return 2
        target = sha
    rows, stat = incoming(repo, at[port], target)
    if not rows:
        out(f"port-sync: {port} already has everything on {upstream}" + (f" up to {target[:12]}" if commit else ""))
        return 0
    out(f"port-sync: {len(rows)} author commit(s) on {upstream}" + (f" up to {target[:12]}" if commit else "")
        + f" not on {port} ({stat or 'no file changes'}):")
    for sha_, day, subj in rows[:15]:
        out(f"   {sha_} {day} {subj}")
    if len(rows) > 15:
        out(f"   ... and {len(rows) - 15} more")
    if dry:
        return 0
    if git(repo, "status", "--porcelain").strip():
        sys.exit("port-sync: the working tree has uncommitted changes; commit or stash them first")
    git(repo, "checkout", "-q", port)
    head = git(repo, "rev-parse", "--short", target).strip()            # name the commit merged: the authors can
    r = git(repo, "merge", "--no-ff", "-m", f"Merge {upstream} at {head} into {port}", target, check=False)  # push mid-run
    if r.returncode:
        files = git(repo, "diff", "--name-only", "--diff-filter=U").split()
        if not files:                    # not a conflict: git refused the merge itself (no identity, a hook, ...)
            out(f"port-sync: the merge failed, no file in conflict: {(r.stderr or r.stdout).strip()[-400:]}")
            if (pathlib.Path(repo) / ".git" / "MERGE_HEAD").exists():
                git(repo, "merge", "--abort", check=False)
            return 2
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


def base_commit_check():
    """--base and --commit, the weekly round's path (#161 U1 review): a work branch keep-current/<date> cut from a port
    branch behind origin is cut from origin's tip (the base fast-forwarded first); a diverged base refuses; a work
    branch cut earlier from a stale base refuses; --commit merges that commit, never the newer tip, and refuses a commit
    the upstream branch does not reach."""
    ok = True
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        G = lambda r, *a: subprocess.run(["git", "-C", str(r), "-c", "user.name=a", "-c", "user.email=a@a", *a],
                                         check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()
        up, fork, other = d / "up", d / "fork", d / "other"
        up.mkdir(); G(up, "init", "-q", "-b", "main")
        (up / "A.java").write_text("a\n", encoding="utf-8"); G(up, "add", "-A"); G(up, "commit", "-qm", "base")
        G(up, "branch", "neoforge-1.21.1")
        subprocess.run(["git", "clone", "-q", "-b", "neoforge-1.21.1", str(up), str(fork)], check=True, capture_output=True)
        subprocess.run(["git", "clone", "-q", "-b", "neoforge-1.21.1", str(up), str(other)], check=True, capture_output=True)
        for r_ in (fork, other):         # port-sync's own merge commits in the clone: its identity, not the machine's
            G(r_, "config", "user.name", "a"); G(r_, "config", "user.email", "a@a")
        (other / "P.java").write_text("a PR merged on origin\n", encoding="utf-8"); G(other, "add", "-A"); G(other, "commit", "-qm", "PR")
        G(other, "push", "-q", "origin", "neoforge-1.21.1")
        pr = G(other, "rev-parse", "HEAD")
        (up / "B.java").write_text("frozen\n", encoding="utf-8"); G(up, "add", "-A"); G(up, "commit", "-qm", "frozen")
        frozen = G(up, "rev-parse", "HEAD")
        (up / "C.java").write_text("pushed after the freeze\n", encoding="utf-8"); G(up, "add", "-A"); G(up, "commit", "-qm", "later")
        wb = "keep-current/2026-10-28"
        log = []
        rc = sync(fork, wb, "origin/main", dry=True, out=log.append, commit=frozen, base="neoforge-1.21.1")
        ok &= rc == 0 and not has_branch(fork, wb) and any("reading origin's" in l for l in log) \
            and any("1 author commit(s)" in l and frozen[:12] in l for l in log)
        log = []
        rc = sync(fork, wb, "origin/main", out=log.append, commit=frozen, base="neoforge-1.21.1")
        ok &= rc == 0 and any("fast-forwarded the local neoforge-1.21.1" in l for l in log) \
            and G(fork, "rev-parse", "neoforge-1.21.1") == pr \
            and subprocess.run(["git", "-C", str(fork), "merge-base", "--is-ancestor", pr, wb]).returncode == 0 \
            and (fork / "B.java").is_file() and not (fork / "C.java").exists() \
            and G(fork, "log", "-1", "--format=%s", wb) == f"Merge origin/main at {G(fork, 'rev-parse', '--short', frozen)} into {wb}"
        G(fork, "checkout", "-q", "neoforge-1.21.1")
        log = []
        ok &= sync(fork, wb, "origin/main", out=log.append, commit="0" * 40, base="neoforge-1.21.1") == 2 \
            and any("is not a commit reachable" in l for l in log)
        # a work branch cut before origin moved again: refused (it lacks the base's new tip)
        (other / "Q.java").write_text("PR 2\n", encoding="utf-8"); G(other, "add", "-A"); G(other, "commit", "-qm", "PR 2")
        G(other, "push", "-q", "origin", "neoforge-1.21.1")
        log = []
        ok &= sync(fork, wb, "origin/main", out=log.append, base="neoforge-1.21.1") == 2 \
            and any("cut from an older neoforge-1.21.1" in l for l in log)
        # a diverged base: refused, nothing cut or merged
        (fork / "L.java").write_text("only here\n", encoding="utf-8"); G(fork, "add", "-A"); G(fork, "commit", "-qm", "local")
        (other / "R.java").write_text("PR 3\n", encoding="utf-8"); G(other, "add", "-A"); G(other, "commit", "-qm", "PR 3")
        G(other, "push", "-q", "origin", "neoforge-1.21.1")
        h = G(fork, "rev-parse", "HEAD")
        log = []
        ok &= sync(fork, "keep-current/other", "origin/main", out=log.append, base="neoforge-1.21.1") == 2 \
            and any("has diverged" in l for l in log) and not has_branch(fork, "keep-current/other") \
            and G(fork, "rev-parse", "HEAD") == h
        # a merge git refuses with no file in conflict (here: no committer identity) is said as git said it, not
        # as a CONFLICT in 0 files
        G(fork, "checkout", "-q", "--", "."); G(fork, "reset", "-q", "--hard", "origin/neoforge-1.21.1")
        G(fork, "config", "--unset", "user.name"); G(fork, "config", "--unset", "user.email")
        G(fork, "config", "user.useConfigOnly", "true")    # no identity guessed from the host name: git refuses
        env = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_", "EMAIL"))}
        home, os.environ["HOME"] = os.environ.get("HOME"), str(d)                 # no ~/.gitconfig identity either
        try:
            log = []
            rc = sync(fork, "neoforge-1.21.1", "origin/main", out=log.append, commit=frozen)
        finally:
            os.environ.update(env)
            if home is not None:
                os.environ["HOME"] = home
        ok &= rc == 2 and any("the merge failed, no file in conflict:" in l for l in log) \
            and not any("CONFLICT" in l for l in log) and not (fork / ".git/MERGE_HEAD").exists()
    if not ok:
        print("FAIL: port-sync --base / --commit", log)
    return ok


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
        # a single-version fork whose local port branch is behind origin's (a PR merged there, the clone not pulled):
        # fast-forwarded before the merge, so the merge starts from origin's tip; diverged: refused, nothing merged
        g(fork, "merge", "--abort")
        g(fork, "push", "-q", "origin", "neoforge-1.21.1")
        other = pathlib.Path(d, "other")
        subprocess.run(["git", "clone", "-q", "-b", "neoforge-1.21.1", str(up), str(other)], check=True, capture_output=True)
        g(other, "config", "user.email", "a@a"); g(other, "config", "user.name", "a")
        (other / "C.java").write_text("merged on origin\n", encoding="utf-8"); g(other, "add", "-A"); g(other, "commit", "-qm", "PR")
        g(other, "push", "-q", "origin", "neoforge-1.21.1")
        tip = git(other, "rev-parse", "HEAD").strip()
        (up / "D.java").write_text("d\n", encoding="utf-8"); g(up, "add", "-A"); g(up, "commit", "-qm", "Add D")
        g(up, "checkout", "-q", "main")
        before = git(fork, "rev-parse", "neoforge-1.21.1").strip()
        log = []
        ok &= sync(fork, "neoforge-1.21.1", "origin/main", dry=True, out=log.append) == 0 \
            and git(fork, "rev-parse", "neoforge-1.21.1").strip() == before and any("reading origin's" in l for l in log)
        log = []
        ok &= sync(fork, "neoforge-1.21.1", "origin/main", out=log.append, dry=False) in (0, 2) \
            and any("fast-forwarded" in l for l in log) \
            and git(fork, "merge-base", "--is-ancestor", tip, "neoforge-1.21.1", check=False).returncode == 0
        g(fork, "merge", "--abort") if (fork / ".git/MERGE_HEAD").exists() else None
        g(fork, "reset", "-q", "--hard", tip)          # the fixture's own state, not the tool's: a local commit next
        (fork / "E.java").write_text("only here\n", encoding="utf-8"); g(fork, "add", "-A"); g(fork, "commit", "-qm", "local")
        (other / "F.java").write_text("only there\n", encoding="utf-8"); g(other, "add", "-A"); g(other, "commit", "-qm", "PR 2")
        g(other, "push", "-q", "origin", "neoforge-1.21.1")
        head = git(fork, "rev-parse", "HEAD").strip()
        log = []
        ok &= sync(fork, "neoforge-1.21.1", "origin/main", out=log.append) == 2 \
            and any("REFUSED" in l and "has diverged" in l and "run port-sync again" in l for l in log) \
            and git(fork, "rev-parse", "HEAD").strip() == head and not (fork / ".git/MERGE_HEAD").exists()
    ok &= base_commit_check()
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def _refuse_layer(repo, tool):
    """tools/fork-kind.py's refusal of a checkout with the removed .multiversion/ layer at HEAD: None, or the message
    (print it and exit 2)."""
    import importlib.util as _ilu
    s = _ilu.spec_from_file_location("fork_kind", pathlib.Path(__file__).resolve().parent / "fork-kind.py")
    m = _ilu.module_from_spec(s); s.loader.exec_module(m)
    return m.refuse_layer(repo, tool)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo"); ap.add_argument("--port"); ap.add_argument("--upstream")
    ap.add_argument("--commit", help="merge exactly this commit (reachable from --upstream), not the tip")
    ap.add_argument("--base", help="the port branch a work branch --port is cut from: checked against origin first")
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    why = _refuse_layer(a.repo, "port-sync")
    if why:
        print(why)
        return 2
    if not (a.repo and a.port and a.upstream):
        ap.error("--repo, --port and --upstream are required")
    return sync(a.repo, a.port, a.upstream, a.dry_run, commit=a.commit, base=a.base)


if __name__ == "__main__":
    sys.exit(main())
