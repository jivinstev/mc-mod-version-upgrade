#!/usr/bin/env python3
"""Install a port's CI (and tag-driven release) into a fork's port branch, as a commit of its own.

    python3 tools/port-ci.py --repo <fork checkout> --modid <modid> [--upstream <author repo URL>]
                             [--dep owner/repo@branch ...] [--gatec launch,spawn] [--no-commit]

The last step of every port: the fork's branch then shows, on every push, that the port builds with the
author's own Gradle build and passes the gates it was held to (tools/ci-gates.py, no model involved); and a
tag `<mod>-<version>-mc<minecraft>` publishes the author-built JARs as a GitHub pre-release once the gates
pass. Everything is read from the repository rather than guessed: the target from gradle.properties, the
Java toolchain from build.gradle, the author's branch from origin/HEAD. --dep names a sibling port this one
compiles against (published to mavenLocal by its own build first, as during the port).

The workflow pins the migrator repository to its CURRENT commit, which must already be pushed -- a pin to a
commit GitHub does not have fails every run. Standard library only.
"""
import argparse, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates/upstream-harness/port-ci.yml"
WORKFLOW = ".github/workflows/port-ci.yml"


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def props(repo):
    p = pathlib.Path(repo) / "gradle.properties"
    return dict(re.findall(r"(?m)^\s*([\w.]+)\s*=\s*(.*?)\s*$", p.read_text(encoding="utf-8"))) if p.exists() else {}


def java_version(repo, mc):
    g = (pathlib.Path(repo) / "build.gradle").read_text(encoding="utf-8", errors="replace")
    m = re.search(r"JavaLanguageVersion\.of\(\s*(\d+)\s*\)", g)
    return m.group(1) if m else ("21" if mc.startswith("1.21") else "25")


def migrator_ref():
    sha = git(ROOT, "rev-parse", "HEAD").stdout.strip()
    url = git(ROOT, "remote", "get-url", "origin").stdout.strip()
    m = re.search(r"github\.com[/:]([\w.-]+/[\w.-]+?)(?:\.git)?$", url)
    if not m:
        sys.exit(f"cannot read the migrator's GitHub repository from {url!r}")
    on_remote = git(ROOT, "branch", "-r", "--contains", sha).stdout.strip()
    if not on_remote:
        sys.exit(f"migrator commit {sha[:10]} is not on any remote branch -- push it first; CI pins to it")
    return m.group(1), sha


def render(text, values):
    out = re.sub(r"(?<!\$)\{\{([A-Z_]+)\}\}", lambda m: values.get(m.group(1), m.group(0)), text)
    left = re.findall(r"(?<!\$)\{\{[A-Z_]+\}\}", out)
    if left:
        raise SystemExit(f"unfilled template placeholders: {left}")
    return out


def pre_build(deps):
    steps = []
    for d in deps:
        m = re.fullmatch(r"([\w.-]+/([\w.-]+))@([\w./-]+)", d)
        if not m:
            raise SystemExit(f"--dep must be owner/repo@branch, got {d!r}")
        full, name, branch = m.groups()
        steps += [f"      - uses: actions/checkout@v4",
                  f"        with:",
                  f"          repository: {full}",
                  f"          ref: {branch}",
                  f"          path: .port-deps/{name}",
                  f"      - name: Build {name} ({branch}) into mavenLocal",
                  f"        run: cd .port-deps/{name} && bash gradlew --console=plain publishToMavenLocal"]
    return "\n".join(steps)


def self_check():
    t = TEMPLATE.read_text(encoding="utf-8")
    v = {k: f"<{k}>" for k in set(re.findall(r"(?<!\$)\{\{([A-Z_]+)\}\}", t))}
    v["PRE_BUILD"] = pre_build(["owner/lib@neoforge-1.21.1"])
    out = render(t, v)
    ok = "${{ github.sha }}" in out and "{{" not in out.replace("${{", "")    # GitHub's own ${{ }} survive
    ok &= "repository: owner/lib" in out and "path: .port-deps/lib" in out and "publishToMavenLocal" in out
    import tempfile
    with tempfile.TemporaryDirectory() as d:        # the toolchain the workflow installs is the build's, per target
        for build, mc, want in (("java.toolchain.languageVersion = JavaLanguageVersion.of(25)\n", "26.2", "25"),
                                ("java.toolchain.languageVersion = JavaLanguageVersion.of(21)\n", "1.21.1", "21"),
                                ("// no toolchain line\n", "26.2", "25"), ("// no toolchain line\n", "1.21.1", "21")):
            (pathlib.Path(d) / "build.gradle").write_text(build, encoding="utf-8")
            ok &= java_version(d, mc) == want
    try:
        render("x {{NOPE}}", {}); ok = False
    except (SystemExit, KeyError):
        pass
    try:
        pre_build(["not-a-dep"]); ok = False
    except SystemExit:
        pass
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    if "--self-check" in sys.argv:
        return self_check()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", required=True); ap.add_argument("--modid", required=True)
    ap.add_argument("--upstream", default="", help="the author's repository URL, for the release notes")
    ap.add_argument("--dep", action="append", default=[], help="owner/repo@branch built into mavenLocal first")
    ap.add_argument("--gatec", default="launch,spawn"); ap.add_argument("--no-commit", action="store_true")
    ap.add_argument("--trailer", action="append", default=[])
    ap.add_argument("--print", action="store_true", help="print the rendered workflow and change nothing")
    a = ap.parse_args()
    repo = pathlib.Path(a.repo).resolve()
    p = props(repo)
    mc, neo = p.get("minecraft_version"), p.get("neo_version")
    if not mc or not neo:
        sys.exit("gradle.properties has no minecraft_version / neo_version")
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    base = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").stdout.strip().split("/", 1)[-1] or "main"
    mrepo, msha = migrator_ref()
    values = {"TARGET": f"NeoForge {neo} (Minecraft {mc})", "BRANCH": branch, "MC": mc, "MODID": a.modid,
              "BASE": base, "JAVA": java_version(repo, mc), "MIGRATOR_REPO": mrepo, "MIGRATOR_SHA": msha,
              "GATEC": a.gatec, "PRE_BUILD": pre_build(a.dep), "UPSTREAM": a.upstream or "the original"}
    out = render(TEMPLATE.read_text(encoding="utf-8"), values)
    if a.print:
        print(out); return 0
    f = repo / WORKFLOW
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(out, encoding="utf-8")
    if a.no_commit:
        print(f"wrote {f}"); return 0
    git(repo, "add", WORKFLOW)
    msg = (f"CI: build, gate and release this port ({values['TARGET']})\n\n"
           "Runs the author's own Gradle build, then the gates the port was held to -- mixin-config\n"
           "integrity, a headless server running GameTests, and a real client under Xvfb -- with no\n"
           "model and no automatic fixes. A tag <mod>-<version>-mc" + mc + " publishes the author-built\n"
           "JARs as a pre-release once every gate passes. The gate harness is not added to this\n"
           "repository: it is generated from " + mrepo + " at " + msha[:10] + ", outside the tree.\n"
           "This commit stands alone so it can be dropped from anything offered upstream.\n")
    if a.trailer:
        msg += "\n" + "\n".join(a.trailer) + "\n"
    r = git(repo, "commit", "-q", "-m", msg)
    print(r.stdout + r.stderr or f"committed {WORKFLOW} on {branch}")
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
