#!/usr/bin/env python3
"""Land a finished port in the mods DESTINATION -- never in this repository.

    python3 tools/finish-port.py <modid>            copy, commit on a branch, do not push
    python3 tools/finish-port.py <modid> --push     ... and push that branch
    python3 tools/finish-port.py <modid> --dry-run  say what it would do, change nothing

WHERE THINGS LIVE
    $MIGRATE_WORKSPACE/mods/<modid>/   the working port (decompiled source, build output, runs).
                                       Outside every repository, by design.
    $MOD_OUTPUT_REPO/mods/<modid>/     where a FINISHED port's source is kept.  Optional: the value
                                       `none` (or no value) means "keep it in the workspace only".
    Both come from .env.local, which ./setup writes; flags override them.

WHAT IS COPIED
    The port's source tree: build files, src/, the Gradle wrapper, MIGRATION.md and friends.  Build
    output, run directories, Gradle caches and the pristine decompile are LEFT BEHIND -- they are
    regenerated, often gigabytes, and `decompiled-raw/` is somebody's unmodified code.  The port's
    local `.git` (the history the skill's retrospective reads) stays behind too.

WHY A BRANCH AND NOT main
    The destination may be shared, and a port is worth a review before it lands.  The branch is
    `port/<modid>` unless --branch says otherwise; re-running updates it with a new commit.

SAFETY
    - Refuses a destination inside this checkout: that is how a third-party mod would get published.
    - Refuses to overwrite `mods/<modid>/` in a destination that is not a git repository unless
      --force, because nothing could bring the old copy back.
    - Refuses when the destination has uncommitted changes under `mods/<modid>/`.

Standard library only.  Exit codes: 0 done (or nothing to do)  1 refused  2 could not run.
"""
import argparse, os, pathlib, shutil, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LEAVE_BEHIND = {"build", "run", ".gradle", "decompiled-raw", "out", "bin", ".idea", ".vscode",
                "logs", "crash-reports"}
LEAVE_BEHIND_PREFIX = ("run-", "build-mc", "build-")      # per-target run/build dirs (§W11, §W17)


def read_env(path):
    env = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def expand(p):
    return pathlib.Path(os.path.expandvars(os.path.expanduser(p))).resolve() if p else None


def git(dest, *args, check=True):
    return subprocess.run(["git", "-C", str(dest), *args], capture_output=True, text=True, check=check, encoding="utf-8", errors="replace")


def is_git_repo(p):
    return subprocess.run(["git", "-C", str(p), "rev-parse", "--is-inside-work-tree"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace").returncode == 0


def ignored(name):
    return name in LEAVE_BEHIND or name.startswith(LEAVE_BEHIND_PREFIX) or name.endswith(".log")


def copy_tree(src, dst):
    n = 0
    for dirpath, dirnames, filenames in os.walk(src):
        rel = pathlib.Path(dirpath).relative_to(src)
        # build/run dirs are pruned at the port's TOP level only: a resource folder deep in src/ may
        # legitimately be called `out` or `bin`.  Gradle's cache dir is noise wherever it appears.
        dirnames[:] = [d for d in dirnames if d not in (".gradle", ".git") and not (rel == pathlib.Path(".") and ignored(d))]
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for f in filenames:
            if f.endswith(".log"):
                continue
            shutil.copy2(pathlib.Path(dirpath) / f, dst / rel / f)
            n += 1
    return n


def status_line(port):
    mig = port / "MIGRATION.md"
    if not mig.is_file():
        return ""
    for line in mig.read_text(errors="replace", encoding="utf-8").splitlines():
        s = line.strip().strip("*").strip()
        if s.lower().startswith("status"):
            return s[:120]
    return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("modid")
    ap.add_argument("--workspace", help="default: MIGRATE_WORKSPACE from .env.local")
    ap.add_argument("--dest", help="default: MOD_OUTPUT_REPO from .env.local ('none' = keep in workspace)")
    ap.add_argument("--branch", help="default: port/<modid>")
    ap.add_argument("--push", action="store_true", help="push the branch to the destination's origin")
    ap.add_argument("--force", action="store_true", help="allow replacing a copy in a NON-git destination")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--env", default=str(ROOT / ".env.local"))
    a = ap.parse_args()

    env = read_env(pathlib.Path(a.env))
    ws = expand(a.workspace or env.get("MIGRATE_WORKSPACE", ""))
    dest_raw = a.dest if a.dest is not None else env.get("MOD_OUTPUT_REPO", "")
    if ws is None:
        print("finish-port: no workspace -- run ./setup --migrate, or pass --workspace", file=sys.stderr)
        return 2
    port = ws / "mods" / a.modid
    if not (port / "build.gradle").is_file() and not (port / "build.gradle.kts").is_file():
        print(f"finish-port: {port} is not a port workspace (no build.gradle)", file=sys.stderr)
        return 2

    if dest_raw.strip().lower() in ("", "none"):
        print(f"finish-port: no destination configured (MOD_OUTPUT_REPO={dest_raw or 'unset'}).\n"
              f"  The port stays in the workspace: {port}\n"
              f"  To keep it somewhere durable, re-run ./setup and choose an output repo, or pass --dest.")
        return 0
    dest = expand(dest_raw)
    if dest == ROOT or ROOT in dest.parents:
        print(f"finish-port: REFUSED -- the destination {dest} is inside the migrator checkout.\n"
              f"  Ported mods must never be committed here: this repository is public.", file=sys.stderr)
        return 1
    if not dest.is_dir():
        print(f"finish-port: destination {dest} does not exist", file=sys.stderr)
        return 2

    target = dest / "mods" / a.modid
    use_git = is_git_repo(dest)
    branch = a.branch or f"port/{a.modid}"

    if use_git:
        dirty = git(dest, "status", "--porcelain", "--", f"mods/{a.modid}").stdout.strip()
        if dirty:
            print(f"finish-port: REFUSED -- uncommitted changes under {target}:\n{dirty}", file=sys.stderr)
            return 1
    elif target.exists() and not a.force:
        print(f"finish-port: REFUSED -- {target} exists and {dest} is not a git repository, so the old "
              f"copy could not be recovered. Pass --force to replace it.", file=sys.stderr)
        return 1

    plan = [f"copy {port} -> {target} (leaving build output, runs and decompiler output behind)"]
    if use_git:
        plan += [f"commit on branch {branch} in {dest}"] + ([f"push {branch} to origin"] if a.push else [])
    else:
        plan += [f"{dest} is not a git repository: copy only, nothing committed"]
    print("finish-port:\n  " + "\n  ".join(plan))
    if a.dry_run:
        print("finish-port: --dry-run, nothing changed")
        return 0

    if use_git:
        cur = git(dest, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if cur != branch:
            exists = git(dest, "rev-parse", "--verify", "--quiet", branch, check=False).returncode == 0
            r = git(dest, "checkout", *([branch] if exists else ["-b", branch]), check=False)
            if r.returncode:
                print(f"finish-port: could not switch {dest} to {branch}:\n{r.stderr}", file=sys.stderr)
                return 1

    # record what the port cost (COST.json + MIGRATION.md) BEFORE copying, so it travels with the port.
    # Never fatal: a missing transcript must not block delivering a finished port.
    r = subprocess.run([sys.executable, str(ROOT / "tools/port-cost.py"), a.modid, "--workspace", str(ws)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print((r.stdout + r.stderr).rstrip() if r.returncode == 0 else
          f"finish-port: cost not recorded ({(r.stderr or r.stdout).strip()})")

    if target.exists():
        shutil.rmtree(target)
    n = copy_tree(port, target)
    print(f"finish-port: copied {n} file(s)")

    if not use_git:
        print(f"finish-port: done. {target} holds the port (not version-controlled).")
        return 0

    git(dest, "add", "-A", "--", f"mods/{a.modid}")
    if not git(dest, "status", "--porcelain", "--", f"mods/{a.modid}").stdout.strip():
        print("finish-port: nothing changed since the last finish; no commit made")
    else:
        status = status_line(target)
        msg = f"Port {a.modid}" + (f"\n\n{status}" if status else "")
        r = git(dest, "commit", "-m", msg, check=False)
        if r.returncode:
            print(f"finish-port: commit failed:\n{r.stdout}{r.stderr}", file=sys.stderr)
            return 1
        print(f"finish-port: committed {git(dest, 'rev-parse', '--short', 'HEAD').stdout.strip()} on {branch}")
    if a.push:
        r = git(dest, "push", "-u", "origin", branch, check=False)
        if r.returncode:
            print(f"finish-port: push failed (the commit is safe locally):\n{r.stderr}", file=sys.stderr)
            return 1
        print(f"finish-port: pushed {branch}")
    else:
        print(f"finish-port: not pushed. Push when ready:  git -C {dest} push -u origin {branch}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
