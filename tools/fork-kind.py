#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""What kind of fork a checkout is: the one route check (CLAUDE.md P1), and the refusal of a `.multiversion/` layer.

    python3 tools/fork-kind.py detect --repo DIR
    python3 tools/fork-kind.py --self-check

A fork is one of two kinds:
  single-version     one port branch (`neoforge-<mc>`), built, gated, synced and published as it is. The default:
                     nothing multi-version runs on it.
  version-branches   a `neoforge-<v>` branch (local, else origin's) carries `.port/<v>/branch.properties`
                     (tools/fork-branches.py): one real branch per Minecraft version.

`detect` prints `kind=single-version` or `kind=version-branches versions=<v>,<v>,...`, with `legacy_layer=<branch>,...`
added when branches still carry the removed `.multiversion/` layer (a `.multiversion/targets/<t>/target.properties`):
they are history, and ignored. Exit 0.

THE LAYER IS REMOVED. The `.multiversion/` overlay route (one layer branch building every version) was removed; the
last commit of this repository that supports it is LAST_LAYER_COMMIT. A checkout with the layer at HEAD
(`.multiversion/base.properties` or `.multiversion/targets/`) is refused by `detect` and by every tool that takes a
fork checkout (refuse_layer(), exit 2), with LAYER_REMOVED: check that commit out to migrate the fork to version
branches (`fork-branches.py migrate` there), then use the fork-versions skill. A single-version fork's shipped-jar
files (shipped-jar.txt, shipped-expect*.txt, which the layer era kept there) committed under `.multiversion/` at HEAD
(read with git ls-tree: an untracked or ignored leftover never counts) are refused too, exit 2, with MOVED: nothing reads that directory any more -- those files live in `.port/<mc>/` on every
kind of fork -- so a gate would otherwise run without them, silently. A migrated fork (version branches exist) checked out on its legacy layer branch is refused too, exit 2, but told to check
out its head branch instead (LEGACY_CHECKOUT): it needs no migration. Standard library only.
"""
import argparse, pathlib, re, subprocess, sys, tempfile

LAST_LAYER_COMMIT = "baa092b62eae1832d354065edadf9e289bf95ca4"
LAYER_REMOVED = (
    "{tool}: {repo} has the .multiversion/ layer at HEAD. The layer (overlay) route was removed from "
    "mc-mod-version-upgrade; the last commit that supports it is " + LAST_LAYER_COMMIT[:12] + ". To move this fork to "
    "version branches, check that commit out (git checkout " + LAST_LAYER_COMMIT[:12] + " in a clone of "
    "mc-mod-version-upgrade) and run `python3 tools/fork-branches.py migrate --repo <this fork> --layer-ref <its layer branch>` there (its fork-versions "
    "skill: \"Migrating a layer fork to version branches\"); then use the fork-versions skill of the current version.")


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def mc_key(v):
    return [int(x) if x.isdigit() else x for x in re.split(r"[.]", v)]


def is_layer(repo):
    """Has the checkout the removed .multiversion/ layer at HEAD (its base.properties, or a targets/ directory)?"""
    mv = pathlib.Path(repo) / ".multiversion"
    return (mv / "base.properties").is_file() or (mv / "targets").is_dir()


MOVED = ("{tool}: {repo} has a .multiversion/ directory at HEAD ({files}), which nothing reads since the layer route was "
         "removed: the shipped-jar files live in .port/<mc>/ on every kind of fork (ci-gates.py --shipped, shipped-gate.py). "
         "Move them -- mkdir -p .port/{mc} && git mv .multiversion/<file> .port/{mc}/<file> for each -- and commit, then "
         "run again.")


SHIPPED_FILE = re.compile(r"\.multiversion/(shipped-jar\.txt|shipped-expect(?:-[A-Za-z0-9]+)?\.txt)")


def stray_files(repo):
    """The shipped-jar files committed at HEAD under a `.multiversion/` that is no layer (what MOVED names): read from
    git, never the working tree, so an untracked or ignored leftover (a __pycache__) refuses nothing. [] when none."""
    ls = git(repo, "ls-tree", "-r", "--name-only", "HEAD", "--", ".multiversion")
    return sorted(m.group(1) for f in (ls.stdout.splitlines() if ls.returncode == 0 else [])
                  for m in [SHIPPED_FILE.fullmatch(f)] if m)


LEGACY_CHECKOUT = ("{tool}: {repo} is a version-branch fork checked out on a legacy layer branch ({branch}); check out "
                   "its head branch ({head})")


def head_of(repo, vb):
    """The head (port) branch of a version-branch fork: `port_branch=` in its oldest version's branch.properties
    (tools/fork-branches.py head_branch), else neoforge-1.21.1."""
    for v in sorted(vb, key=mc_key):
        props = git(repo, "show", f"{vb[v]}:.port/{v}/branch.properties").stdout
        m = re.search(r"(?m)^port_branch=(\S+)", props)
        if m:
            return m.group(1)
    return "neoforge-1.21.1"


def refuse_layer(repo, tool):
    """The one refusal: None for a fork this repository serves, else the message (print it and exit 2). A migrated
    fork (it has version branches) whose checkout sits on its legacy layer branch is told to check out its head branch;
    any other layer at HEAD is told where its migration runs (LAST_LAYER_COMMIT)."""
    if not repo:
        return None
    if not is_layer(repo):
        files = stray_files(repo)
        if not files:
            return None
        gp = pathlib.Path(repo) / "gradle.properties"
        m = re.search(r"(?m)^\s*minecraft_version\s*=\s*(\S+)", gp.read_text(encoding="utf-8")) if gp.is_file() else None
        return MOVED.format(tool=tool, repo=repo, files=", ".join(files[:4]) + (", ..." if len(files) > 4 else ""),
                            mc=m.group(1) if m else "<mc>")
    vb = version_refs(repo)
    if vb:
        branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        return LEGACY_CHECKOUT.format(tool=tool, repo=repo, branch=branch if branch not in ("", "HEAD") else "a detached HEAD",
                                      head=head_of(repo, vb))
    return LAYER_REMOVED.format(tool=tool, repo=repo)


def version_refs(repo):
    """{version: branch} of the fork's version branches (local first, then origin's): a `neoforge-<v>` branch that
    carries .port/<v>/branch.properties (tools/fork-branches.py version_branches)."""
    out = {}
    for pat in ("refs/heads/neoforge-*", "refs/remotes/origin/neoforge-*"):
        for line in git(repo, "for-each-ref", "--format=%(refname:short)", pat).stdout.split():
            v = line.split("neoforge-", 1)[1]
            if v not in out and git(repo, "cat-file", "-e", f"{line}:.port/{v}/branch.properties").returncode == 0:
                out[v] = line
    return out


def legacy_layer_refs(repo):
    """Every local or remote branch that still carries a layer target (history on a migrated fork; ignored)."""
    rows = git(repo, "for-each-ref", "--format=%(refname:short)\t%(symref)", "refs/heads", "refs/remotes").stdout.splitlines()
    out = []
    for r, _, sym in (l.partition("\t") for l in rows):
        if not r or sym:          # origin/HEAD, which --format=short prints as a bare "origin": an alias, not a branch
            continue
        ls = git(repo, "ls-tree", "-r", "--name-only", r, "--", ".multiversion/targets").stdout.split()
        if any(re.fullmatch(r"\.multiversion/targets/[^/]+/target\.properties", p) for p in ls):
            out.append(r)
    return out


def detect(repo):
    """(kind, versions, legacy layer branches): kind is "single-version" or "version-branches"."""
    vb = version_refs(repo)
    return ("version-branches" if vb else "single-version"), sorted(vb, key=mc_key), legacy_layer_refs(repo)


def cmd_detect(a):
    repo = pathlib.Path(a.repo).resolve()
    if git(repo, "rev-parse", "--git-dir").returncode:
        print(f"fork-kind: {repo} is not a git checkout"); return 2
    why = refuse_layer(repo, "fork-kind")
    if why:
        print(why); return 2
    kind, versions, legacy = detect(repo)
    print(f"kind={kind}" + (f" versions={','.join(versions)}" if versions else "")
          + (f" legacy_layer={','.join(legacy)}" if legacy else ""))
    if legacy:
        print(f"fork-kind: {', '.join(legacy)} still carr{'ies' if len(legacy) == 1 else 'y'} the removed .multiversion/ "
              "layer: history, ignored" + ("" if versions else
              f" (a fork not yet migrated: its layer is served only by commit {LAST_LAYER_COMMIT[:12]})"))
    return 0


def self_check():
    ok = True

    def expect(c, msg):
        nonlocal ok
        if not c:
            ok = False; print("FAIL:", msg)
    with tempfile.TemporaryDirectory() as d:
        r = pathlib.Path(d) / "fork"; r.mkdir()
        g = lambda *a: subprocess.run(["git", "-C", str(r), *a], capture_output=True, text=True, encoding="utf-8", check=True)
        def w(p, t):
            f = r / p; f.parent.mkdir(parents=True, exist_ok=True); f.write_text(t, encoding="utf-8")
        g("init", "-q", "-b", "neoforge-1.21.1"); g("config", "user.email", "a@b"); g("config", "user.name", "a")
        w("gradle.properties", "minecraft_version=1.21.1\n"); g("add", "-A"); g("commit", "-qm", "port")
        expect(detect(r) == ("single-version", [], []), f"one port branch: single-version: {detect(r)}")
        expect(refuse_layer(r, "x") is None, "a single-version fork is not refused")
        # a .multiversion/ with only a single-version fork's shipped-jar files is no layer, and nothing reads it any
        # more: refused, told to move them to .port/<mc>/ (#161 U1); read from HEAD, so untracked leftovers do not count
        w(".multiversion/tools/__pycache__/x.cpython-313.pyc", "junk")
        w(".multiversion/shipped-jar.txt", "*_mr.jar\n")
        expect(refuse_layer(r, "x") is None, "an untracked .multiversion/ (a __pycache__, an uncommitted file): not refused")
        g("add", ".multiversion/shipped-jar.txt"); g("commit", "-qm", "shipped list in the old place")
        msg = refuse_layer(r, "x")
        expect(msg and "mkdir -p .port/1.21.1 && git mv .multiversion/<file> .port/1.21.1/<file>" in msg
               and "pyc" not in msg and "__pycache__" not in msg, f"only the shipped files are named, the mkdir first: {msg}")
        expect(msg and msg.startswith("x: ") and "(shipped-jar.txt)" in msg and ".port/1.21.1/<file>" in msg
               and LAST_LAYER_COMMIT[:12] not in msg and detect(r)[0] == "single-version",
               f"a .multiversion/ holding only shipped-jar.txt: refused, the move to .port/<mc>/ named: {msg}")
        (r / ".port/1.21.1").mkdir(parents=True)
        g("mv", ".multiversion/shipped-jar.txt", ".port/1.21.1/shipped-jar.txt"); g("commit", "-qm", "moved")
        expect(refuse_layer(r, "x") is None, "the files moved (the leftover __pycache__ still there): no refusal")
        import shutil; shutil.rmtree(r / ".multiversion")
        # a legacy layer branch (history) beside the port branch: still single-version, the branch listed
        g("checkout", "-q", "-b", "neoforge-1.21.x")
        w(".multiversion/base.properties", "base=1.21.1\n"); w(".multiversion/targets/1.21.4/target.properties", "x=1\n")
        g("add", "-A"); g("commit", "-qm", "layer")
        msg = refuse_layer(r, "some-tool")
        expect(msg and msg.startswith("some-tool: ") and LAST_LAYER_COMMIT[:12] in msg and "fork-branches.py migrate" in msg
               and "fork-versions" in msg, f"the layer at HEAD is refused, naming the last commit and the migration: {msg}")
        g("checkout", "-q", "neoforge-1.21.1")
        expect(refuse_layer(r, "x") is None and detect(r) == ("single-version", [], ["neoforge-1.21.x"]),
               f"the layer branch not checked out: no refusal, listed as legacy: {detect(r)}")
        # version branches win; the layer branch is history
        g("checkout", "-q", "-b", "neoforge-1.21.4")
        w("gradle.properties", "minecraft_version=1.21.4\n"); w(".port/1.21.4/branch.properties", "parent=neoforge-1.21.1\nport_branch=neoforge-1.21.1\n")
        g("add", "-A"); g("commit", "-qm", "1.21.4")
        expect(detect(r) == ("version-branches", ["1.21.4"], ["neoforge-1.21.x"]), f"version branches: {detect(r)}")
        # a migrated fork checked out on its legacy layer branch: refused (exit 2), told to check out its head, no migration
        g("checkout", "-q", "neoforge-1.21.x")
        msg = refuse_layer(r, "some-tool")
        expect(msg == f"some-tool: {r} is a version-branch fork checked out on a legacy layer branch (neoforge-1.21.x); "
                      "check out its head branch (neoforge-1.21.1)" and "migrate" not in msg and LAST_LAYER_COMMIT[:12] not in msg,
               f"a migrated fork on its legacy layer branch is told to check out its head: {msg}")
        g("checkout", "-q", "neoforge-1.21.4")
        expect(refuse_layer(r, "x") is None, "back on a version branch: no refusal")
        # a single-branch clone of the head (the port branch) is a single-version fork
        c = pathlib.Path(d) / "clone"
        subprocess.run(["git", "clone", "-q", "--single-branch", "-b", "neoforge-1.21.1", str(r), str(c)], check=True,
                       capture_output=True)
        expect(detect(c) == ("single-version", [], []), f"a single-branch clone of the head: single-version: {detect(c)}")
        # an origin-only version branch counts (a fresh clone has no local branch for it)
        c2 = pathlib.Path(d) / "clone2"
        subprocess.run(["git", "clone", "-q", "-b", "neoforge-1.21.1", str(r), str(c2)], check=True, capture_output=True)
        expect(detect(c2)[:2] == ("version-branches", ["1.21.4"]) and detect(c2)[2] == ["origin/neoforge-1.21.x"],
               f"origin's version branches count: {detect(c2)}")
    expect(mc_key("1.21.11") > mc_key("1.21.8") and mc_key("26.1.2") > mc_key("1.21.11"), "versions sort as numbers")
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    if "--self-check" in sys.argv:
        return self_check()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("detect"); p.add_argument("--repo", default=".")
    a = ap.parse_args()
    return cmd_detect(a)


if __name__ == "__main__":
    sys.exit(main())
