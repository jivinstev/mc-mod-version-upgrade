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
Exit 0 pass, 1 fail, 2 could not run (no skills found, or the base is unknown): a gate that quietly checked
nothing reports like one that passed. Standard library only.
"""
import argparse, pathlib, re, subprocess, sys, tempfile

PATH = re.compile(r"(?<![\w./-])((?:tools|docs|templates|setup|cloud|\.claude|\.github)/[\w./-]*[\w/])")
LINK = re.compile(r"\]\(((?!https?:|#|mailto:)[^)\s]+)\)")


def git(root, *a):
    r = subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout


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
