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
  4. if the branch changes a file the single-version route runs (SV_TOOLS, or a line of keep-current, port-fork or
     publish-port outside a `## ` section about version branches or the layer), a commit message on the branch carries
     `Single-version-change: <reason>` (what an existing single-version fork now does differently) or
     `Single-version-unchanged: <reason>` (CLAUDE.md, P2: nothing changes silently). The line is printed.
Exit 0 pass, 1 fail, 2 could not run (no skills found, or the base is unknown): a gate that quietly checked
nothing reports like one that passed. Standard library only.
"""
import argparse, pathlib, re, subprocess, sys, tempfile

PATH = re.compile(r"(?<![\w./-])((?:tools|docs|templates|setup|cloud|\.claude|\.github)/[\w./-]*[\w/])")
SV_TOOLS = {f"tools/{t}.py" for t in ("port", "port-upstream", "port-sync", "port-ci", "port-publish", "ci-gates",
                                         "shipped-gate")}
SV_SKILLS = {f".claude/skills/{s}/SKILL.md" for s in ("keep-current", "port-fork", "publish-port")}
MV_SECTION = re.compile(r"version branch|layer|multi-version", re.I)    # a `## ` heading the single-version route skips
LINK = re.compile(r"\]\(((?!https?:|#|mailto:)[^)\s]+)\)")


def git(root, *a):
    r = subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout


def sv_lines(root, mb, f):
    """Does the branch change a line of skill `f` outside a `## ` section about version branches or the layer?"""
    _, d = git(root, "diff", "-U0", mb, "HEAD", "--", f)
    starts = [int(m) for m in re.findall(r"^@@ -\S+ \+(\d+)", d, re.M)]
    p = root / f
    lines = p.read_text(encoding="utf-8").splitlines() if p.is_file() else []
    for n in starts:                      # a deletion's hunk names the line before it: the same section
        head = next((l for l in reversed(lines[:max(n, 1)]) if l.startswith("## ")), "")
        if not MV_SECTION.search(head):
            return True
    return False


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
        rc, diff = git(root, "diff", "--name-status", mb, "HEAD")
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
        sv = sorted(f for f in changed if f in SV_TOOLS or (f in SV_SKILLS and sv_lines(root, mb, f)))   # 4
        if sv:
            _, log = git(root, "log", "--format=%B", f"{mb}..HEAD")
            said = re.findall(r"^(Single-version-(?:change|unchanged)):\s*(\S.*)$", log, re.M)
            if said:
                notes.append(f"single-version route touched ({', '.join(sv[:3])}): {said[-1][0]}: {said[-1][1]}")
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
        # 4: the single-version route (a tool it runs; a skill line outside a version-branch or layer section)
        g("checkout", "-qb", "sv", "main")
        w(".claude/skills/keep-current/SKILL.md", "# k\n## Run\nstep\n## A fork with version branches\nvb\n")
        w("README.md", "| **alpha**: does a | `tools/a.py` |\n| **keep-current**: k | |\n")
        g("add", "-A"); g("commit", "-qm", "kc")
        rc, p, _ = check(r, "main"); ok &= rc == 1 and "Single-version-change" in p[0]  # a new skill's Run section
        g("checkout", "-qb", "sv2")
        w(".claude/skills/keep-current/SKILL.md", "# k\n## Run\nstep\n## A fork with version branches\nvb, more\n")
        g("commit", "-qam", "vb only\n\nREADME-unchanged: a test")
        rc, p, _ = check(r, "sv"); ok &= rc == 0 and not p                            # a version-branch section only
        w(".claude/skills/keep-current/SKILL.md", "# k\n## Run\nstep, changed\n## A fork with version branches\nvb, more\n")
        g("commit", "-qam", "run step")
        rc, p, _ = check(r, "sv"); ok &= rc == 1 and any("Single-version-change" in x for x in p)
        g("commit", "-q", "--allow-empty", "-m", "y\n\nSingle-version-unchanged: wording only")
        rc, p, n = check(r, "sv"); ok &= rc == 0 and any("wording only" in x for x in n)
        g("tag", "t"); w("tools/port-sync.py", ""); g("add", "-A"); g("commit", "-qm", "tool")
        rc, p, _ = check(r, "t"); ok &= rc == 1 and "tools/port-sync.py" in p[0]    # a tool the route runs
        g("commit", "-q", "--allow-empty", "-m", "z\n\nSingle-version-change: merges now name the commit")
        rc, p, n = check(r, "t"); ok &= rc == 0 and any("Single-version-change: merges" in x for x in n)
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
