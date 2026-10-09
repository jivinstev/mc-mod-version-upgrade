#!/usr/bin/env python3
"""Run the same port step over every fork you maintain, in dependency order, from one file.

After a change to the Port CI kit or the offer, every fork needs the same commands with its own
flags (its base ref, the sibling jar it builds against, which platform jar to install). Typing them
by hand is how one fork ends up on an old kit. This keeps the flags in a fleet file and runs them.

The fleet file lives OUTSIDE this repository (it names other people's mods). JSON:

  {"trailers": ["Co-Authored-By: ...", "..."],
   "ports": [
     {"repo": "/path/to/clone", "modid": "a", "base": "origin/main", "branch": "neoforge-1.21.1",
      "upstream": "https://github.com/author/a", "upstream_name": "A by author",
      "dep_jar": ["owner/fork@tag/x.jar=g:a:v"], "variant": ["_mr=why"], "gatec": "launch,spawn",
      "tag_suffix": "-release" (a release-aligned branch), "release": "<author's released commit>",
      "work_dir": "~/.mc-mod-upgrade/upstream/<dir>"}]}   # work_dir: only if the clone's folder name differs

Steps, run in the file's order (put a library before the mods that use it):
  ci       regenerate the CI workflow (tools/port-ci.py) as its own commit; --push to push it
  gates    run the gates locally as CI will (tools/ci-gates.py), --env full|minimal
  offer    refresh the offer branch and the install manifest (tools/port-offer.py); --push
  status   each fork: branch, HEAD, ahead/behind its remote, uncommitted files

Releases are a workflow_dispatch on GitHub (the fork's CI); `ci --push` prints which to dispatch.

  port-fleet.py --fleet ~/forks.json status
  port-fleet.py --fleet ~/forks.json ci --push --dry-run
  port-fleet.py --self-check
"""
import argparse, json, pathlib, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REQUIRED = ("repo", "modid")


def load(path):
    f = json.loads(pathlib.Path(path).expanduser().read_text(encoding="utf-8"))
    for i, p in enumerate(f.get("ports", [])):
        miss = [k for k in REQUIRED if not p.get(k)]
        if miss:
            raise SystemExit(f"fleet: port {i} has no {', '.join(miss)}")
    if not f.get("ports"):
        raise SystemExit("fleet: no ports")
    return f


def commands(fleet, step, push=False, env="full", work=None):
    """(port, cwd, argv) for every command a step runs, in order."""
    tr = [x for t in fleet.get("trailers", []) for x in ("--trailer", t)]
    out = []
    for p in fleet["ports"]:
        repo, branch = str(pathlib.Path(p["repo"]).expanduser()), p.get("branch", "neoforge-1.21.1")
        if step == "ci":
            argv = [sys.executable, str(ROOT / "tools/port-ci.py"), "--repo", repo, "--modid", p["modid"], *tr]
            if p.get("upstream"):
                argv += ["--upstream", p["upstream"]]
            argv += [x for d in p.get("dep_jar", []) for x in ("--dep-jar", d)]
            argv += [x for d in p.get("dep", []) for x in ("--dep", d)]
            if p.get("gatec"):
                argv += ["--gatec", p["gatec"]]
            if p.get("tag_suffix"):                     # a release-aligned branch's own tag series
                argv.append(f"--tag-suffix={p['tag_suffix']}")
            out.append((p, repo, argv))
            if push:
                out.append((p, repo, ["git", "push", "origin", branch]))
        elif step == "gates":
            w = pathlib.Path(work or tempfile.gettempdir()) / f"gates-{p['modid']}-{env}"
            argv = [sys.executable, str(ROOT / "tools/ci-gates.py"), "--repo", repo, "--modid", p["modid"],
                    "--env", env, "--work-dir", str(w), "--summary", str(w) + ".md"]
            if p.get("base"):
                argv += ["--base", p["base"]]
            if p.get("gatec"):
                argv += ["--gatec", p["gatec"]]
            out.append((p, repo, argv))
        elif step == "offer":
            argv = [sys.executable, str(ROOT / "tools/port-offer.py"), "--repo", repo, "--modid", p["modid"],
                    "--branch", branch, *tr]
            if p.get("base"):
                argv += ["--base", p["base"]]
            if p.get("upstream_repo"):
                argv += ["--upstream", p["upstream_repo"]]
            argv += [x for v in p.get("variant", []) for x in ("--variant", v)]
            if p.get("release"):                        # the author's released commit: the offer states the distance
                argv += ["--release", p["release"]]
            elif p.get("published"):
                argv += ["--published", p["published"]]
            if p.get("work_dir"):                       # the port's own records (state.json): licence, what was verified
                argv += ["--work-dir", str(pathlib.Path(p["work_dir"]).expanduser())]
            if push:
                argv.append("--push")
            out.append((p, repo, argv))
        elif step == "status":
            out.append((p, repo, ["git", "status", "-sb", "--untracked-files=no"]))
            out.append((p, repo, ["git", "log", "--oneline", "-1"]))
        else:
            raise SystemExit(f"unknown step {step}")
    return out


def self_check():
    fleet = {"trailers": ["T: x"], "ports": [
        {"repo": "/r/lib", "modid": "lib", "base": "origin/main"},
        {"repo": "/r/mod", "modid": "mod", "base": "origin/master", "dep_jar": ["o/lib@t/lib.jar=g:lib:1"],
         "variant": ["_mr=why"], "upstream": "https://github.com/a/mod", "gatec": "launch", "work_dir": "/w/mod"}]}
    ok = True
    ci = commands(fleet, "ci", push=True)
    ok &= [c[2][0] for c in ci] == [sys.executable, "git", sys.executable, "git"]          # order kept, push after each
    ok &= "--dep-jar" in ci[2][2] and "--dep-jar" not in ci[0][2] and ci[2][2][-2:] == ["--gatec", "launch"]
    ok &= ci[0][2].count("--trailer") == 1
    of = commands(fleet, "offer", push=True)
    ok &= of[1][2][-1] == "--push" and "--variant" in of[1][2] and "--variant" not in of[0][2]
    ok &= pathlib.Path(of[1][2][of[1][2].index("--work-dir") + 1]) == pathlib.Path("/w/mod") and "--work-dir" not in of[0][2]   # a Path: Windows gives \\w\\mod
    g = commands(fleet, "gates", env="minimal", work="/w")
    ok &= g[0][2][g[0][2].index("--env") + 1] == "minimal" and g[1][2][g[1][2].index("--base") + 1] == "origin/master"
    with tempfile.TemporaryDirectory() as t:
        bad = pathlib.Path(t, "f.json"); bad.write_text('{"ports":[{"repo":"/x"}]}', encoding="utf-8")
        try:
            load(bad); ok = False
        except SystemExit as e:
            ok &= "modid" in str(e)
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fleet")
    ap.add_argument("step", nargs="?", choices=["ci", "gates", "offer", "status"])
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--env", default="full", choices=["full", "minimal"])
    ap.add_argument("--work-dir")
    ap.add_argument("--only", action="append", default=[], help="a modid; repeat to run a subset")
    ap.add_argument("--dry-run", action="store_true", help="print the commands, run nothing")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not (a.fleet and a.step):
        ap.error("--fleet and a step are needed")
    fleet = load(a.fleet)
    if a.only:
        fleet["ports"] = [p for p in fleet["ports"] if p["modid"] in a.only]
    failed = []
    for p, cwd, argv in commands(fleet, a.step, a.push, a.env, a.work_dir):
        print(f"== {p['modid']}: {' '.join(argv[1:] if argv[0] == sys.executable else argv)}", flush=True)
        if a.dry_run:
            continue
        r = subprocess.run(argv, cwd=cwd)
        if r.returncode:
            failed.append(p["modid"])
            if a.step in ("ci", "offer"):     # a later port may build against this one: stop
                break
    if a.step == "ci" and a.push and not a.dry_run and not failed:
        print("next: dispatch each fork's release workflow (release: true), then run `offer --push`")
    if failed:
        print(f"port-fleet: failed: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
