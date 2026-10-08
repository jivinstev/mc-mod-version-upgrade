#!/usr/bin/env python3
"""How far a port's base is from what the author actually RELEASED: the commits in between, how big, and what.

    python3 tools/port-provenance.py --repo <fork clone> --base <commit the port branch starts from>
        (--release <commit> | --published <ISO time> | --provider modrinth|curseforge --id <project> --mc <ver>)
        [--branch <author's branch, default: the base's own history>] [--format md|json]
    python3 tools/port-provenance.py --self-check

WHY. A fork is usually cut from the author's development tip, not from the release players run. Every commit in
between is code nobody has played: new features, half-finished ones, and the bugs that come with them. A port
offered or installed without saying so hides that. This names the release, counts what sits on top of it, and
summarises it so a reader can judge -- and it is the number that decides whether to derive a release-aligned
branch (tools/port-derive.py).

FINDING THE RELEASE. A registry stores when a file was PUBLISHED, not which commit built it. The release commit is
taken as the last commit on the author's branch at or before that time (the version bump usually lands minutes
before the upload). Say "matched by publish time" when you quote it; pass --release when you know the commit.
Standard library only; --provider runs tools/mod-registry/modreg.py.
"""
import argparse, json, pathlib, re, subprocess, sys, tempfile
from datetime import datetime, timezone


def git(repo, *a):
    r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        sys.exit(f"git {' '.join(a)}: {r.stderr.strip()[-300:]}")
    return r.stdout


def published_from_registry(provider, pid, mc):
    """The newest datePublished among the project's files for this Minecraft version (any loader)."""
    tool = pathlib.Path(__file__).parent / "mod-registry/modreg.py"
    r = subprocess.run([sys.executable, str(tool), "versions", "--provider", provider, "--id", pid, "--mc", mc],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    dates = re.findall(r'"datePublished":\s*"([^"]+)"', r.stdout)
    if not dates:
        sys.exit(f"modreg found no published file for {pid} on {mc}: {(r.stderr or r.stdout)[-300:]}")
    return max(dates)


def release_at(repo, ref, published):
    """The last commit on `ref`'s first-parent history at or before `published`."""
    t = datetime.fromisoformat(published.replace("Z", "+00:00")).astimezone(timezone.utc)
    for line in git(repo, "log", "--first-parent", "--format=%H %cI", ref).splitlines():
        sha, when = line.split(" ", 1)
        if datetime.fromisoformat(when).astimezone(timezone.utc) <= t:
            return sha
    sys.exit(f"no commit on {ref} at or before {published}")


def report(repo, base, release, matched_by="given"):
    base = git(repo, "rev-parse", base).strip()
    release = git(repo, "rev-parse", release).strip()
    if subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", release, base]).returncode:
        sys.exit(f"the release {release[:10]} is not an ancestor of the base {base[:10]}: wrong branch or wrong time")
    commits = [l.split("\t", 2) for l in git(repo, "log", "--no-merges", "--format=%h\t%cs\t%s",
                                              f"{release}..{base}").splitlines() if l]
    stat = git(repo, "diff", "--shortstat", release, base).strip()
    num = lambda word: int((re.search(r"(\d+) " + word, stat) or [0, 0])[1])
    files, ins, dels = num("file"), num("insertion"), num("deletion")
    areas = {}
    for f in git(repo, "diff", "--name-only", release, base).splitlines():
        parts = f.split("/")
        key = "/".join(parts[-3:-1]) if f.startswith("src/") and len(parts) > 3 else (parts[0] if len(parts) > 1 else "(root)")
        areas[key] = areas.get(key, 0) + 1
    return {"release": release, "release_date": git(repo, "show", "-s", "--format=%cs", release).strip(),
            "matched_by": matched_by, "base": base, "commits": len(commits),
            "first": commits[-1][1] if commits else None, "last": commits[0][1] if commits else None,
            "files": files, "insertions": ins, "deletions": dels,
            "areas": sorted(areas.items(), key=lambda kv: -kv[1])[:8],
            "subjects": [{"sha": c[0], "date": c[1], "subject": c[2]} for c in commits]}


def markdown(r, limit=12):
    head = (f"Built on the author's release commit `{r['release'][:10]}` ({r['release_date']}, matched by "
            f"{r['matched_by']}).")
    if not r["commits"]:
        return "## Distance from the author's release\n\n" + head + " Nothing unreleased is included.\n"
    L = ["## Distance from the author's release", "",
         head + f" The port starts **{r['commits']} commit(s) after it** ({r['first']} to {r['last']}): "
         f"{r['files']} files, +{r['insertions']} / -{r['deletions']} lines of code the author has not released "
         "and players have not run.", "",
         "Where: " + ", ".join(f"{a} ({n})" for a, n in r["areas"]) + ".", ""]
    L += [f"- `{s['sha']}` {s['date']} {s['subject']}" for s in r["subjects"][:limit]]
    if len(r["subjects"]) > limit:
        L.append(f"- ... and {len(r['subjects']) - limit} more")
    return "\n".join(L) + "\n"


def self_check():
    ok = True
    with tempfile.TemporaryDirectory() as d:
        def g(*a, env=None):
            subprocess.run(["git", "-C", d, *a], check=True, capture_output=True,
                           env={"GIT_AUTHOR_DATE": env, "GIT_COMMITTER_DATE": env, "HOME": d, "PATH": "/usr/bin:/bin",
                                "GIT_AUTHOR_NAME": "a", "GIT_AUTHOR_EMAIL": "a@a", "GIT_COMMITTER_NAME": "a",
                                "GIT_COMMITTER_EMAIL": "a@a"} if env else None)
        g("init", "-q")
        for i, (when, f) in enumerate([("2026-07-01T10:00:00+00:00", "a"), ("2026-07-01T11:00:00+00:00", "src/main/java/x/y/Z.java"),
                                       ("2026-07-03T09:00:00+00:00", "src/main/java/x/y/W.java")]):
            p = pathlib.Path(d, f); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(f"{i}\n" * (i + 1), encoding="utf-8")
            g("add", "-A"); g("commit", "-q", "-m", f"c{i}", env=when)
        rel = release_at(d, "HEAD", "2026-07-01T11:04:00Z")
        r = report(d, "HEAD", rel, "publish time")
        md = markdown(r)
        for label, cond in [("release found by time", r["release"] == git(d, "rev-parse", "HEAD~1").strip()),
                            ("one commit after", r["commits"] == 1 and "c2" in md),
                            ("size", r["files"] == 1 and r["insertions"] == 3),
                            ("area", "x/y (1)" in md),
                            ("aligned branch says nothing included", "Nothing unreleased" in markdown(report(d, "HEAD~1", rel)))]:
            if not cond:
                print("FAIL:", label); ok = False
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo"); ap.add_argument("--base"); ap.add_argument("--branch")
    ap.add_argument("--release"); ap.add_argument("--published")
    ap.add_argument("--provider"); ap.add_argument("--id"); ap.add_argument("--mc")
    ap.add_argument("--format", choices=["md", "json"], default="md"); ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not (a.repo and a.base):
        ap.error("--repo and --base are required")
    if a.release:
        rel, how = a.release, "the given commit"
    else:
        pub = a.published or (published_from_registry(a.provider, a.id, a.mc) if a.provider and a.id and a.mc else None)
        if not pub:
            ap.error("give --release, --published, or --provider/--id/--mc")
        rel, how = release_at(a.repo, a.branch or a.base, pub), f"publish time {pub}"
    r = report(a.repo, a.base, rel, how)
    print(json.dumps(r, indent=1) if a.format == "json" else markdown(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
