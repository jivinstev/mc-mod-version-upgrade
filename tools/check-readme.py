#!/usr/bin/env python3
"""Does the README still describe the skills? (a pre-push and CI gate)

    python3 tools/check-readme.py [--base origin/main] [--root DIR]

WHY. The README's Skills table is how people find what this repository can do, and it went stale once: a whole
round of new port-fork tools shipped with none of them in it. A rule in CLAUDE.md is something to remember; this
is the control (CATALOG §S2: a step you must remember is not a control).

ALWAYS:
  1. every skill under .claude/skills/<name>/SKILL.md has a row in the README's Skills table (`| **<name>**`);
  2. every repository path the README names (`tools/...`, `docs/...`, `templates/...`, a relative link) exists.
WITH --base (the pre-push hook and CI pass the branch it will merge into):
  3. if the branch changes a SKILL.md, or ADDS a script that a SKILL.md names, then README.md must change on the
     branch too -- unless a commit message on the branch carries a line `README-unchanged: <reason>` (a typo fix
     in a skill, say). The reason is printed, so a reviewer sees it.
  4. if the branch changes a file the single-version route runs, a commit message on the branch carries
     `Single-version-change: <reason>` (what an existing single-version fork now does differently) or
     `Single-version-unchanged: <reason>` (CLAUDE.md, P2: nothing changes silently). Every such line is printed. The
     route, read at the merge-base and at HEAD: SV_ROOTS, every tools/ script and templates/ path they name, and those
     scripts' own, transitively (minus MV_TOOLS, the multi-version-only ones, and MV_PREFIX: the version branches' shared library and this repository's own);
     everything under templates/upstream-harness/; and each line of keep-current, port-fork or publish-port, old or new,
     outside a `## ` section about version branches or the layer.
Exit 0 pass, 1 fail, 2 could not run (no skills found, or the base is unknown): a gate that quietly checked
nothing reports like one that passed. Standard library only.
"""
import argparse, pathlib, re, subprocess, sys, tempfile

PATH = re.compile(r"(?<![\w./-])((?:tools|docs|templates|setup|cloud|\.claude|\.github)/[\w./-]*[\w/])")
SV_ROOTS = [f"tools/{t}.py" for t in ("port", "port-upstream", "port-sync", "port-ci", "port-publish", "ci-gates",
                                        "shipped-gate")]
SV_PREFIX = ("templates/upstream-harness/",)                           # every single-version fork's CI and harness
# round.py is not one of them: it drives single-version forks too (#161 U4)
MV_TOOLS = {f"tools/{t}" for t in ("fork-branches.py", "fork-port.py", "checkout-lock.py", "layer-inventory.py",
                                   "make-multiversion.sh")}
MV_PREFIX = ("templates/fork-layer/", "tools/check-", "tools/test-", "tools/guard-names.py", "tools/install-hooks.sh",
             "tools/propose-learnings.py")          # the version branches' shared library, or this repository's own tooling
SV_SKILLS = {f".claude/skills/{s}/SKILL.md" for s in ("keep-current", "port-fork", "publish-port")}
MV_SECTION = re.compile(r"version branch|\blayer\b|multi-version", re.I)  # a `## ` heading the single-version route skips
REF = re.compile(r"(?<![\w-])([\w-]+\.(?:py|sh))\b|(templates/[\w./-]*[\w/])")
LINK = re.compile(r"\]\(((?!https?:|#|mailto:)[^)\s]+)\)")


def git(root, *a):
    r = subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout


def show(root, rev, f):
    rc, t = git(root, "show", f"{rev}:{f}")
    return "" if rc else t


def sv_route(root, rev):
    """The files the single-version route runs at `rev`, and the templates/ paths (files or directories) it names."""
    _, ls = git(root, "ls-tree", "-r", "--name-only", rev, "tools", "templates")
    have = set(ls.split())
    files, dirs, todo = set(), set(), [f for f in SV_ROOTS if f in have]
    while todo:
        f = todo.pop()
        if f in files or f in MV_TOOLS or f.startswith(MV_PREFIX):
            continue
        files.add(f)
        for script, tpl in REF.findall(show(root, rev, f) if f.endswith((".py", ".sh")) else ""):
            if script and f"tools/{script}" in have:
                todo.append(f"tools/{script}")
            elif tpl and not tpl.startswith(MV_PREFIX):
                dirs.add(tpl.rstrip("/"))
    return files, dirs


def sv_lines(root, mb, f):
    """Does the branch change a line of skill `f`, on its old side or its new, outside a `## ` section about version
    branches or the layer? Read from git (never the working tree: an uncommitted line would shift the numbers)."""
    _, d = git(root, "diff", "-U0", "--no-renames", mb, "HEAD", "--", f)
    sides = [show(root, mb, f).splitlines(), show(root, "HEAD", f).splitlines()]
    for h in re.findall(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", d, re.M):
        for side, (start, n) in enumerate(((h[0], h[1]), (h[2], h[3]))):
            start, n = int(start), int(n or 1)
            for i in range(start, start + n):      # each line, judged by the `## ` heading over it (or itself)
                head = next((l for l in reversed(sides[side][:i]) if l.startswith("## ")), "")
                if not MV_SECTION.search(head):
                    return True
    return False


def on_route(f, route):
    files, dirs = route
    return f in files or f.startswith(SV_PREFIX) or any(f == x or f.startswith(x + "/") for x in dirs)


def check(root, base=None):
    root = pathlib.Path(root)
    problems, notes = [], []
    readme = (root / "README.md").read_text(encoding="utf-8") if (root / "README.md").is_file() else ""
    skills = sorted(p.parent.name for p in (root / ".claude/skills").glob("*/SKILL.md"))
    if not skills or not readme:
        return 2, ["no skills under .claude/skills or no README.md: nothing was checked"], notes
    for s in skills:                                                       # 1
        if not re.search(r"^\|\s*\*\*" + re.escape(s) + r"\*\*", readme, re.M):
            problems.append(f"skill `{s}` has no row in the README's Skills table (`| **{s}**: ... |`)")
    paths = {m.rstrip(".") for m in PATH.findall(readme)} | {m.split("#")[0] for m in LINK.findall(readme)}
    for p in sorted(x for x in paths if x):                                # 2
        if not (root / p).exists():
            problems.append(f"the README names `{p}`, which does not exist")
    checked = f"{len(skills)} skill(s), {len(paths)} path(s)"
    if base:                                                               # 3
        rc, mb = git(root, "merge-base", base, "HEAD")
        if rc:
            return 2, problems + [f"cannot compare with {base} (fetch it first)"], notes
        mb = mb.strip()
        rc, diff = git(root, "diff", "--name-status", "--no-renames", mb, "HEAD")   # a rename: both paths
        changed = {l.split("\t")[-1]: l.split("\t")[0] for l in diff.splitlines() if l}
        named = {t for s in skills for t in re.findall(r"tools/[\w./-]+\.(?:py|sh)",
                                                      (root / ".claude/skills" / s / "SKILL.md").read_text(encoding="utf-8"))}
        why = [f for f, st in changed.items() if f.startswith(".claude/skills/") and f.endswith("SKILL.md")]
        why += [f for f, st in changed.items() if st.startswith("A") and f in named]
        if why and "README.md" not in changed:
            _, log = git(root, "log", "--format=%B", f"{mb}..HEAD")
            ok = re.findall(r"^README-unchanged:\s*(\S.*)$", log, re.M)
            if ok:
                notes.append(f"README unchanged on purpose: {ok[-1]}")
            else:
                problems.append("this branch changes " + ", ".join(sorted(why)[:5]) + (" ..." if len(why) > 5 else "")
                                + " but not README.md: update the Skills table / workflow section, or say why not "
                                "with a commit line `README-unchanged: <reason>`")
        routes = [sv_route(root, mb), sv_route(root, "HEAD")]                                          # 4
        sv = sorted(f for f in changed if any(on_route(f, r) for r in routes) or (f in SV_SKILLS and sv_lines(root, mb, f)))
        if sv:
            _, log = git(root, "log", "--format=%B", f"{mb}..HEAD")
            said = re.findall(r"^(Single-version-(?:change|unchanged)):\s*(\S.*)$", log, re.M)
            if said:
                notes.append(f"single-version route touched ({', '.join(sv[:3])}" + (" ..." if len(sv) > 3 else "") + "):")
                notes += [f"  {k}: {v}" for k, v in said]
            else:
                problems.append("this branch changes " + ", ".join(sv[:5]) + (" ..." if len(sv) > 5 else "")
                                + ", which the single-version route runs: say what that means for an existing "
                                "single-version fork with a commit line `Single-version-change: <reason>` or "
                                "`Single-version-unchanged: <reason>`")
        checked += f", {len(changed)} changed file(s) since {base}"
    notes.insert(0, f"checked {checked}")
    return (1 if problems else 0), problems, notes


def self_check():
    ok = True
    with tempfile.TemporaryDirectory() as d:
        r = pathlib.Path(d)
        def g(*a):
            subprocess.run(["git", "-C", d, *a], check=True, capture_output=True)
        def w(p, t):
            (r / p).parent.mkdir(parents=True, exist_ok=True); (r / p).write_text(t, encoding="utf-8")
        g("init", "-q", "-b", "main"); g("config", "user.email", "a@a"); g("config", "user.name", "a")
        w(".claude/skills/alpha/SKILL.md", "run `python3 tools/a.py`\n"); w("tools/a.py", "")
        w("README.md", "| **alpha**: does a | `tools/a.py` |\n"); g("add", "-A"); g("commit", "-qm", "base")
        cases = [("clean tree passes", 0)]
        rc, _, _ = check(r); ok &= rc == 0
        w(".claude/skills/beta/SKILL.md", "x\n")
        rc, p, _ = check(r); ok &= rc == 1 and "beta" in p[0]; cases.append(("skill with no row", rc))
        (r / ".claude/skills/beta/SKILL.md").unlink(); (r / ".claude/skills/beta").rmdir()
        w("README.md", "| **alpha**: does a | `tools/a.py`, `tools/gone.py` |\n")
        rc, p, _ = check(r); ok &= rc == 1 and "tools/gone.py" in p[0]
        w("README.md", "| **alpha**: does a | `tools/a.py` |\n")
        g("checkout", "-qb", "feature")
        w(".claude/skills/alpha/SKILL.md", "run `python3 tools/a.py` and `python3 tools/b.py`\n"); w("tools/b.py", "")
        g("add", "-A"); g("commit", "-qm", "skill grows")
        rc, p, _ = check(r, "main"); ok &= rc == 1 and "not README.md" in p[0]
        g("commit", "-q", "--allow-empty", "-m", "x\n\nREADME-unchanged: internal step only")
        rc, p, n = check(r, "main"); ok &= rc == 0 and any("internal step only" in x for x in n)
        g("reset", "-q", "--hard", "HEAD~1")
        w("README.md", "| **alpha**: does a | `tools/a.py`, `tools/b.py` |\n"); g("add", "-A"); g("commit", "-qm", "readme")
        rc, _, _ = check(r, "main"); ok &= rc == 0
        # 4: the single-version route -- its tools, what they name (transitively), its CI template, its skill lines
        g("checkout", "-qb", "sv", "main")
        KC = ".claude/skills/keep-current/SKILL.md"
        kc = "# k\n## Run\nr1\n## A fork with the layer\nl1\nl2\n## The weekly cycle\nw1\n## README Ports block\np1\n" \
             "## The player's view\nv1\n## Version branches\nb1\n"
        for f, t in ((KC, kc), ("tools/ci-gates.py", "run(gate-loop.py)\n"), ("tools/gate-loop.py", "run(scaffold-gatec.py)\n"),
                     ("tools/scaffold-gatec.py", "x\n"), ("tools/fork-branches.py", "x\n"), ("tools/z.py", "x\n"),
                     ("templates/upstream-harness/port-ci.yml", "on: push\n"),
                     ("README.md", "| **alpha**: does a | `tools/a.py` |\n| **keep-current**: k | |\n")):
            w(f, t)
        g("add", "-A"); g("commit", "-qm", "fixture\n\nSingle-version-unchanged: fixture"); g("tag", "b")

        def case(name, edits, want, lines=("README-unchanged: a test",), dirty=None):
            nonlocal ok
            for f, t in edits.items():
                if t is None:
                    (r / f).unlink()
                else:
                    w(f, t)
            g("add", "-A"); g("commit", "-q", "--allow-empty", "-m", "c\n\n" + "\n".join(lines))
            if dirty:
                w(*dirty)                                   # an uncommitted edit: the check reads git, not the tree
            rc, p, n = check(r, "b")
            good = rc == want if isinstance(want, int) else want(rc, p, n)
            if not good:
                print(f"self-check: rule 4, {name}: rc {rc} {p} {n}")
            ok &= good
            g("reset", "-q", "--hard", "b"); g("clean", "-qfd")
        case("a layer section only", {KC: kc.replace("l1", "l1x")}, 0)
        case("a version-branch section only", {KC: kc.replace("b1", "b1x")}, 0)
        case("a single-version section", {KC: kc.replace("r1", "r1x")}, 1)
        case("'player' is not 'layer'", {KC: kc.replace("v1", "v1x")}, 1)
        case("one hunk from a layer section into the next", {KC: kc.replace("l2\n## The weekly cycle\nw1",
                                                                              "l2x\n## The weekly cycle!\nw1x")}, 1)
        case("a heading renamed into a version-branch one", {KC: kc.replace("## README Ports block",
                                                                              "## README Ports block for version branches")}, 1)
        case("a single-version section deleted", {KC: kc.replace("## The weekly cycle\nw1\n", "")}, 1)
        case("an uncommitted edit shifts no line", {KC: kc.replace("l1", "l1x")}, 0, dirty=(KC, "a\nb\nc\nd\ne\n" + kc))
        case("said", {KC: kc.replace("r1", "r1x")}, lambda rc, p, n: rc == 0 and any("wording only" in x for x in n),
             lines=("README-unchanged: a test", "Single-version-unchanged: wording only"))
        case("a script a route tool runs, transitively", {"tools/scaffold-gatec.py": "y\n"}, 1)
        case("the CI template", {"templates/upstream-harness/port-ci.yml": "on: [push]\n"}, 1)
        case("a multi-version tool, an unnamed one", {"tools/fork-branches.py": "y\n", "tools/z.py": "y\n"}, 0)
        case("a rename counts its old path", {"tools/gate-loop.py": None, "tools/gl.py": "run(scaffold-gatec.py)\n"}, 1)
        g("commit", "-q", "--allow-empty", "-m", "c\n\nSingle-version-change: merges name the commit")
        case("every line is printed", {"tools/scaffold-gatec.py": "y\n"},
             lambda rc, p, n: rc == 0 and any("merges name the commit" in x for x in n) and any("both kept" in x for x in n),
             lines=("Single-version-unchanged: both kept",))
        rc, _, _ = check(r, "nosuchbase"); ok &= rc == 2
        (r / "README.md").unlink()
        rc, _, _ = check(r); ok &= rc == 2
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=str(pathlib.Path(__file__).resolve().parent.parent))
    ap.add_argument("--base", help="the branch this will merge into, e.g. origin/main (enables rule 3)")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    rc, problems, notes = check(a.root, a.base)
    for n in notes:
        print(f"check-readme: {n}")
    for p in problems:
        print(f"check-readme: {p}")
    print("check-readme:", {0: "PASS", 1: "FAIL", 2: "COULD NOT RUN"}[rc])
    return rc


if __name__ == "__main__":
    sys.exit(main())
