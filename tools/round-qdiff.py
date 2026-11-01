#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""What a change to the round's driver does to its step graph: the queue `round.py init` builds, old against new.

    python3 tools/round-qdiff.py --frozen FROZEN.json --fork owner/repo=DIR [--fork ...] [--old PATH/round.py]
                                 [--new PATH/round.py] [--config FILE] [--through owner/repo=V] [--date YYYY-MM-DD]
                                 [--json]
    python3 tools/round-qdiff.py --self-check

WHY. tools/round.py's queue (build_queue: items with id, step, kind, deps, extra) is the round's contract: which steps
run, in which order, waiting on what. A speed-up to the driver should change that graph only where it means to; this
prints exactly what it changes, on a real plan and real clones, before the change runs a real round.

HOW. Each round.py (default --old: origin/main's, read with `git archive` into a temporary directory with its sibling
tools and templates, so it runs against the tools it shipped with; default --new: this checkout's) runs its own
`init` -- its main(["init", ...]) in a child process, nothing reimplemented -- on the same frozen plan, against
THROWAWAY CLONES and a throwaway state directory:
  - each fork's throwaway clone is a fresh repository under a temporary directory that borrows the clone's objects
    (objects/info/alternates) and copies its local branches, origin's remote-tracking branches and tags (a local
    fetch: no object is written to the clone, no ref of it moves, its checkout is untouched); HEAD names the clone's
    current branch; remote.origin.url is the clone's own (init checks it), every github.com URL (https, ssh, git) is
    rewritten to a path that does not exist and every transport but file:// is refused (protocol.allow never), so a
    fetch or push in the throwaway clone -- a token URL too -- fails at once instead of reaching the network;
  - a shallow or single-branch clone is warned about (the real init fetches origin's neoforge-* first; here it is
    read as it is), and the throwaway clone has an empty working tree, so init's layer refusal sees only what git
    holds (fork-kind.py stray_files reads HEAD's tree; is_layer's working-tree .multiversion/ check finds nothing):
    a fork with the removed layer checked out is refused by the real init, not by this diff;
  - init's one network step, fetch_version_branches (round-preflight.py make_whole: ls-remote and a fetch of origin's
    neoforge-* when the clone lacks one), is replaced by "the clone is whole" (the throwaway clone has every branch
    the clone has), and migrator_file (the B1 reader check's fetch of a pinned migrator commit) reads only what this
    checkout already holds;
  - the date (--date, default the frozen plan's frozen_at day) and --since are fixed, so the two runs differ only by
    their round.py.
Then: items added and removed, and per item in both its added and removed deps, and changed kind, step, dirs, lock
and extra keys (added, removed, changed). --json gives the same as JSON. Exit 0 the graphs are equal, 1 they differ,
2 bad input or an init that failed.

A FROZEN PLAN FOR A PAST ROUND: `python3 tools/known-issues.py read-plan --issue N --slug owner/repo --out FILE` works
on a closed plan issue too (it reads the issue and its comments; nothing checks the state). Two differences from the
round's own frozen.json: it reads the issue as it is now (boxes and `/grade` or `/decide` replies posted after the
round started count), and a read before the plan's deadline adds `early_start` (the queue does not read it).
Standard library only.
"""
import argparse, contextlib, io, json, os, pathlib, re, shutil, stat, subprocess, sys, tarfile, tempfile, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
ITEM_KEYS = ("kind", "step", "arg", "dirs", "lock", "phase", "fork", "version", "final", "close")


def git(d, *a, check=False):
    r = subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(a[:3])} in {d}: {(r.stderr or r.stdout).strip()[-300:]}")
    return r


def rmtree(p):
    def onerror(fn, path, _exc):          # Windows: .git/objects files are read-only
        os.chmod(path, stat.S_IWRITE)
        fn(path)
    shutil.rmtree(p, onerror=onerror)


def throwaway_clone(src, dest, say=None):
    """A clone of `src` that shares its objects and copies its refs; nothing in `src` is written."""
    src = pathlib.Path(src).resolve()
    if say and git(src, "rev-parse", "--is-shallow-repository").stdout.strip() == "true":
        say(f"round-qdiff: WARNING {src.as_posix()} is shallow: init reads the clone as it is (the real init would "
            "fetch origin's neoforge-* branches whole first), so its queue may differ from a real round's")
    if say and not any("*" in x for x in git(src, "config", "--get-all", "remote.origin.fetch").stdout.split()):
        say(f"round-qdiff: WARNING {src.as_posix()} is a single-branch clone: origin's other neoforge-* branches it "
            "lacks are not fetched here (round-preflight.py make_whole would), so a version-branch fork may read as "
            "single-version")
    common = git(src, "rev-parse", "--path-format=absolute", "--git-common-dir", check=True).stdout.strip()
    dest.mkdir(parents=True)
    git(dest, "init", "-q", check=True)
    (dest / ".git/objects/info").mkdir(parents=True, exist_ok=True)
    (dest / ".git/objects/info/alternates").write_text(pathlib.Path(common, "objects").resolve().as_posix() + "\n",
                                                       encoding="utf-8")
    # refs only: every object is already reachable through the alternates (a local fetch from the clone's git dir)
    git(dest, "fetch", "-q", "--no-tags", "--update-head-ok", common, "+refs/heads/*:refs/heads/*",
        "+refs/remotes/origin/*:refs/remotes/origin/*", "+refs/tags/*:refs/tags/*", check=True)
    url = git(src, "config", "--get", "remote.origin.url").stdout.strip()
    for k, v in (("remote.origin.url", url), ("remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"),
                 ("core.autocrlf", "false"), ("gc.auto", "0"), ("maintenance.auto", "false"),
                 ("protocol.allow", "never"), ("protocol.file.allow", "always"),
                 ("url.file:///round-qdiff-no-network/.insteadOf", "https://github.com/")):
        git(dest, "config", k, v, check=True)
    for prefix in ("git@github.com:", "ssh://git@github.com/", "git://github.com/"):
        git(dest, "config", "--add", "url.file:///round-qdiff-no-network/.insteadOf", prefix)
    branch = git(src, "symbolic-ref", "-q", "HEAD").stdout.strip()
    if branch:
        git(dest, "symbolic-ref", "HEAD", branch, check=True)
    else:
        c = git(src, "rev-parse", "HEAD").stdout.strip()
        if c:
            git(dest, "update-ref", "--no-deref", "HEAD", c, check=True)
    return dest


def round_from_rev(rev, dest):
    """origin/main's (or any revision's) tools/ and templates/ written under dest: its round.py runs with them."""
    r = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", rev, "tools", "templates"], capture_output=True)
    if r.returncode:
        raise RuntimeError(f"git archive {rev}: {r.stderr.decode('utf-8', 'replace').strip()[-300:]}")
    dest.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(r.stdout)) as t:
        for m in t.getmembers():
            if m.name.startswith("/") or ".." in pathlib.PurePosixPath(m.name).parts or not (m.isfile() or m.isdir()):
                continue
            if hasattr(tarfile, "data_filter"):          # Python 3.12+ (and patched 3.9-3.11): the safe filter
                t.extract(m, str(dest), filter="data")
            else:
                t.extract(m, str(dest))
    return dest / "tools" / "round.py"


BUILD = r'''
import contextlib, importlib.util, io, json, pathlib, subprocess, sys
spec = importlib.util.spec_from_file_location("round_under_test", sys.argv[1])
m = importlib.util.module_from_spec(spec)
sys.dont_write_bytecode = True
spec.loader.exec_module(m)
here = pathlib.Path(sys.argv[2])                      # the migrator checkout whose objects migrator_file may read
if hasattr(m, "fetch_version_branches"):
    m.fetch_version_branches = lambda d: None         # the throwaway clone holds every branch the clone holds
if hasattr(m, "migrator_file"):
    def migrator_file(rev, path="tools/known-issues.py"):
        r = subprocess.run(["git", "-C", str(here), "show", f"{rev}:{path}"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        return r.stdout if r.returncode == 0 else None
    m.migrator_file = migrator_file
if hasattr(m, "MIGRATOR"):
    m.MIGRATOR = here
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = m.main(json.loads(sys.argv[3]))
sys.stderr.write(buf.getvalue())
sys.exit(rc or 0)
'''


def build(round_py, frozen, forks, work, config=None, through=(), date=None, since=None):
    """The queue round_py's init builds on throwaway clones of `forks` ({slug: dir}). -> (items, init output)."""
    work.mkdir(parents=True)
    args = ["init", "--frozen", str(pathlib.Path(frozen).resolve()), "--round-dir", str(work / "rd"), "--force"]
    for slug, d in forks.items():
        c = throwaway_clone(d, work / "clones" / slug.replace("/", "__"), say=lambda m: print(m, file=sys.stderr))
        args += ["--fork", f"{slug}={c}"]
    if config:
        args += ["--config", str(pathlib.Path(config).resolve())]
    for t in through:
        args += ["--through", t]
    if date:
        args += ["--date", date]
    if since:
        args += ["--since", since]
    script = work / "build.py"
    script.write_text(BUILD, encoding="utf-8")
    r = subprocess.run([sys.executable, str(script), str(pathlib.Path(round_py).resolve()), str(ROOT), json.dumps(args)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(work))
    state = work / "rd" / "round" / "STATE.json"
    if r.returncode or not state.is_file():
        raise RuntimeError(f"{round_py}: init exited {r.returncode}: {(r.stderr + r.stdout).strip()[-1500:]}")
    return json.loads(state.read_text(encoding="utf-8"))["items"], r.stderr


def diff(old, new):
    """-> {"added": [ids], "removed": [ids], "changed": {id: {...}}} of two item lists."""
    a, b = {it["id"]: it for it in old}, {it["id"]: it for it in new}
    out = {"added": [i for i in b if i not in a], "removed": [i for i in a if i not in b], "changed": {}}
    for i in [x for x in b if x in a]:
        x, y = a[i], b[i]
        ch = {}
        da, db = list(x.get("deps") or []), list(y.get("deps") or [])
        if [d for d in db if d not in da]:
            ch["deps_added"] = [d for d in db if d not in da]
        if [d for d in da if d not in db]:
            ch["deps_removed"] = [d for d in da if d not in db]
        for k in ITEM_KEYS:
            if x.get(k) != y.get(k):
                ch[k] = {"old": x.get(k), "new": y.get(k)}
        ea, eb = x.get("extra") or {}, y.get("extra") or {}
        if sorted(set(eb) - set(ea)):
            ch["extra_added"] = {k: eb[k] for k in sorted(set(eb) - set(ea))}
        if sorted(set(ea) - set(eb)):
            ch["extra_removed"] = {k: ea[k] for k in sorted(set(ea) - set(eb))}
        cc = {k: {"old": ea[k], "new": eb[k]} for k in sorted(set(ea) & set(eb)) if ea[k] != eb[k]}
        if cc:
            ch["extra_changed"] = cc
        if ch:
            out["changed"][i] = ch
    return out


def render(d, n_old, n_new):
    if not (d["added"] or d["removed"] or d["changed"]):
        return f"round-qdiff: the same queue ({n_old} item(s))"
    out = [f"round-qdiff: {n_old} item(s) -> {n_new}: {len(d['added'])} added, {len(d['removed'])} removed, "
           f"{len(d['changed'])} changed"]
    out += [f"+ {i}" for i in d["added"]] + [f"- {i}" for i in d["removed"]]
    for i, ch in d["changed"].items():
        out.append(f"~ {i}")
        for k, v in ch.items():
            if k in ("deps_added", "deps_removed"):
                out += [f"    {'+' if k == 'deps_added' else '-'}dep {x}" for x in v]
            elif k.startswith("extra_"):
                for kk, vv in v.items():
                    out.append(f"    {k.replace('_', ' ')} {kk}: " + (json.dumps(vv, sort_keys=True)[:300]))
            else:
                out.append(f"    {k}: {json.dumps(v['old'])} -> {json.dumps(v['new'])}")
    return "\n".join(out)


def run(a):
    forks = {}
    for s in a.fork:
        slug, sep, d = s.partition("=")
        if not sep or "/" not in slug or not pathlib.Path(d).is_dir():
            print(f"round-qdiff: --fork must be owner/repo=DIR (an existing clone), got {s!r}", file=sys.stderr)
            return 2
        forks[slug] = d
    frozen = json.loads(pathlib.Path(a.frozen).read_text(encoding="utf-8"))
    date = a.date or str(frozen.get("frozen_at") or "")[:10] or None
    since = f"{date}T00:00:00Z" if date else None
    work = pathlib.Path(tempfile.mkdtemp(prefix="round-qdiff-")).resolve()
    try:
        paths = {}
        for which, given, rev in (("old", a.old, "origin/main"), ("new", a.new, None)):
            if given:
                paths[which] = pathlib.Path(given).resolve()
            elif rev:
                paths[which] = round_from_rev(rev, work / f"{which}-tools")
            else:
                paths[which] = TOOLS / "round.py"
        res = {}
        for which in ("old", "new"):
            res[which], _o = build(paths[which], a.frozen, forks, work / which, a.config, a.through or (), date, since)
        d = diff(res["old"], res["new"])
        if a.json:
            print(json.dumps(dict(d, old=str(paths["old"].as_posix()) if a.old else "origin/main",
                                  new=str(paths["new"].as_posix()), items_old=len(res["old"]),
                                  items_new=len(res["new"])), indent=1))
        else:
            print(render(d, len(res["old"]), len(res["new"])))
        return 1 if (d["added"] or d["removed"] or d["changed"]) else 0
    except RuntimeError as e:
        print(f"round-qdiff: {e}", file=sys.stderr)
        return 2
    finally:
        rmtree(work)


# ---------------------------------------------------------------- self-check

EDGE = 'addv("reviewref", deps=[review_of[prev_seg]] if prev_seg in review_of else None)'


def self_check():
    t0 = time.monotonic()
    ok = True

    def expect(c, msg):
        nonlocal ok
        if not c:
            ok = False
            print("FAIL:", msg)
    env_keep = {k: os.environ.get(k) for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                                               "GIT_COMMITTER_EMAIL", "GIT_CONFIG_COUNT", "MMVU_SHARED_DIR")}
    n = int(os.environ.get("GIT_CONFIG_COUNT", "0") or 0)
    for k, v in (("commit.gpgsign", "false"), ("gc.auto", "0"), ("core.autocrlf", "false")):
        os.environ[f"GIT_CONFIG_KEY_{n}"], os.environ[f"GIT_CONFIG_VALUE_{n}"] = k, v
        n += 1
    os.environ["GIT_CONFIG_COUNT"] = str(n)
    os.environ.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                       "GIT_COMMITTER_EMAIL": "t@example.com"})
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="round-qdiff-selfcheck-")).resolve()
    try:
        (tmp / "no-shared").mkdir()
        os.environ["MMVU_SHARED_DIR"] = str(tmp / "no-shared")     # no shared library to sync the fixture to
        # a version-branch fork: head 1.21.1, versions 1.21.4 and 1.21.5; origin github.com/owner/examplemod (a bare
        # repository beside it), the authors' code taken -- every version gets its segment
        d = tmp / "examplemod"
        d.mkdir()
        g = lambda *x: git(d, *x, check=True)
        g("init", "-q", "-b", "neoforge-1.21.1")
        (d / "gradle.properties").write_text("minecraft_version=1.21.1\nmod_id=examplemod\n", encoding="utf-8")
        g("add", "-A"); g("commit", "-q", "-m", "port")
        prev, pv = "neoforge-1.21.1", "1.21.1"
        for v in ("1.21.4", "1.21.5"):
            g("checkout", "-q", "-b", f"neoforge-{v}")
            (d / "gradle.properties").write_text(f"minecraft_version={v}\nmod_id=examplemod\n", encoding="utf-8")
            (d / ".port" / v).mkdir(parents=True)
            (d / ".port" / v / "branch.properties").write_text(
                f"minecraft_version={v}\nbranch=neoforge-{v}\nparent={prev}\nparent_minecraft_version={pv}\n"
                "port_branch=neoforge-1.21.1\n", encoding="utf-8")
            g("add", "-A"); g("commit", "-q", "-m", f"version {v}")
            prev, pv = f"neoforge-{v}", v
        g("checkout", "-q", "neoforge-1.21.1")
        bare = tmp / "examplemod.git"
        git(tmp, "clone", "-q", "--bare", str(d), str(bare), check=True)
        g("remote", "add", "origin", "https://github.com/owner/examplemod.git")
        g("config", "url." + bare.as_posix() + ".insteadOf", "https://github.com/owner/examplemod.git")
        g("fetch", "-q", "origin")
        before = (git(d, "for-each-ref").stdout, git(d, "config", "--list", "--local").stdout, git(d, "status", "--porcelain").stdout)
        cfg = tmp / "cfg.json"
        cfg.write_text(json.dumps({"forks": [{"repo": "owner/examplemod", "head": "neoforge-1.21.1", "role": ""}],
                                   "allow": []}), encoding="utf-8")
        frozen = tmp / "frozen.json"
        frozen.write_text(json.dumps({"schema": 1, "issue": 7, "repo": "owner/examplemod", "owner": {"login": "owner"},
                                      "frozen_at": "2026-10-21T15:00:00+00:00", "grades": [], "decisions": [], "forks": [
            {"repo": "owner/examplemod", "head": "neoforge-1.21.1", "take_authors": True, "head_cascade": True,
             "upstream_commit": "a" * 40, "upstream": "authors", "upstream_branch": "main", "selected": [],
             "cascade": {"range": "neoforge-1.21.1..1.21.5", "decisions": []}, "fixes": [], "dep_bumps": []}]}),
            encoding="utf-8")
        # two copies of this checkout's tools: A as it is, B with one dependency edge cut (a version's reviewref no
        # longer waits for the review below)
        for name in ("A", "B"):
            dst = tmp / name / "tools"
            dst.mkdir(parents=True)
            for f in sorted(TOOLS.glob("*.py")):
                shutil.copy2(f, dst / f.name)
        rb = tmp / "B/tools/round.py"
        text = rb.read_text(encoding="utf-8")
        expect(text.count(EDGE) == 1, f"round.py has the edge the self-check cuts: {EDGE}")
        rb.write_text(text.replace(EDGE, 'addv("reviewref")'), encoding="utf-8")
        ns = lambda o, n_, js=False: argparse.Namespace(frozen=str(frozen), fork=[f"owner/examplemod={d}"], old=str(o),
                                                          new=str(n_), config=str(cfg), through=None, date=None, json=js)
        for o, n_, want, what in ((tmp / "A/tools/round.py", tmp / "A/tools/round.py", 0, "identical round.py: no diff"),
                                  (tmp / "A/tools/round.py", tmp / "B/tools/round.py", 1, "one dep edge cut: exactly it")):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = run(ns(o, n_, js=True))
            try:
                got = json.loads(buf.getvalue())
            except ValueError:
                got = {}
            if want == 0:
                expect(rc == 0 and got.get("changed") == {} and not got.get("added") and not got.get("removed")
                       and got.get("items_old", 0) > 10, f"{what}: rc={rc} {buf.getvalue()[-800:]}")
            else:
                expect(rc == 1 and not got.get("added") and not got.get("removed")
                       and got.get("changed") == {"examplemod@1.21.5:reviewref": {"deps_removed": ["examplemod@1.21.4:recheck"]}},
                       f"{what}: rc={rc} {buf.getvalue()[-1500:]}")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            run(ns(tmp / "A/tools/round.py", tmp / "B/tools/round.py"))
        expect("~ examplemod@1.21.5:reviewref\n    -dep examplemod@1.21.4:recheck" in buf.getvalue(),
               f"the text report names the item and its removed dep: {buf.getvalue()}")
        tc = throwaway_clone(d, tmp / "tc")
        r1 = git(tc, "ls-remote", "https://x-access-token:t@github.com/owner/examplemod.git")
        r2 = git(tc, "ls-remote", "origin")
        r3 = git(tc, "ls-remote", "https://example.com/other.git")
        expect(r1.returncode and r2.returncode and r3.returncode and "not allowed" in (r3.stderr + r1.stderr),
               f"the throwaway clone reaches no network (a token URL, origin, another host): {r1.stderr} {r2.stderr} {r3.stderr}")
        warned = []
        shallow = tmp / "shallow"
        git(tmp, "clone", "-q", "--depth", "1", "--single-branch", bare.as_uri(), str(shallow), check=True)
        throwaway_clone(shallow, tmp / "tc2", say=warned.append)
        expect(any("shallow" in w_ for w_ in warned) and any("single-branch" in w_ for w_ in warned),
               f"a shallow single-branch clone is warned about: {warned}")
        after = (git(d, "for-each-ref").stdout, git(d, "config", "--list", "--local").stdout, git(d, "status", "--porcelain").stdout)
        expect(after == before, "the fork clone is untouched (refs, config, checkout)")
        # the diff itself: every kind of change named
        x = {"id": "a", "deps": ["p"], "kind": "tool", "extra": {"k": 1, "gone": 2}}
        y = {"id": "a", "deps": ["q"], "kind": "agent", "extra": {"k": 3, "new": 4}}
        dd = diff([x, {"id": "old"}], [y, {"id": "new"}])
        expect(dd == {"added": ["new"], "removed": ["old"], "changed": {"a": {
            "deps_added": ["q"], "deps_removed": ["p"], "kind": {"old": "tool", "new": "agent"},
            "extra_added": {"new": 4}, "extra_removed": {"gone": 2}, "extra_changed": {"k": {"old": 1, "new": 3}}}}},
            f"diff: items added/removed, deps, kind and extra keys: {dd}")
    finally:
        for k, v in env_keep.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        rmtree(tmp)
    print(f"round-qdiff self-check: {'OK' if ok else 'FAILED'} ({time.monotonic() - t0:.1f} s)")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--frozen", help="the frozen plan (known-issues.py read-plan --out)")
    ap.add_argument("--fork", action="append", default=[], help="owner/repo=DIR: each fork's clone (never written)")
    ap.add_argument("--old", help="the old round.py (default: origin/main's, with its tools)")
    ap.add_argument("--new", help="the new round.py (default: this checkout's)")
    ap.add_argument("--config", help="the set's weekly-plan.json (default: what init reads from the first fork's head)")
    ap.add_argument("--through", action="append", help="owner/repo=VERSION|none, passed to both inits")
    ap.add_argument("--date", help="the round's date (default: the frozen plan's frozen_at day)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args(argv)
    if a.self_check:
        return self_check()
    if not a.frozen or not a.fork:
        ap.error("--frozen and at least one --fork are required")
    return run(a)


if __name__ == "__main__":
    sys.exit(main())
