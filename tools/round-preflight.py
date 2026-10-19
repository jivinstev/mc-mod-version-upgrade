#!/usr/bin/env python3
"""What a keep-current round needs before it starts, checked first, each missing thing said exactly.

    python3 tools/round-preflight.py --fork owner/repo=DIR [--fork ...] [--versions 1.21.1,26.3] [--no-client]
    python3 tools/round-preflight.py --self-check

The weekly round (keep-current, "The weekly round") runs this as its first step, before it freezes the plan. It
prints one line per check -- `ok` or `MISSING` with what to do -- and exits 1 when anything is missing, 0 otherwise:

  clones      each fork of the set has a clone at DIR whose origin is owner/repo. A scheduled session cannot attach a
              repository itself (add_repo is refused there): a missing clone means the fork must be added to the
              scheduled task's repositories. The round stops on it, before freezing, and leaves the plan issue open.
  github      which way the tools reach GitHub: GITHUB_TOKEN (wins), else GH_TOKEN, both over REST; else the gh command
              line. Neither a token nor gh: missing. A broken gh login does not matter when a token is set.
  jdk         a JDK for each Minecraft version the forks build: 21 for 1.21.x, 25 for 26.x (a version branch's own
              java_version first) -- found where tools/shipped-gate.py looks (JAVA_HOME first, then JAVA_HOME_<n>,
              /usr/lib/jvm, /opt, ~/.gradle/jdks, ~/.jdks, SDKMAN).
  libEGL      (Linux, a 26.3 or later client) libEGL.so.1, which that client loads (Debian/Ubuntu: libegl1).
  XDG_RUNTIME_DIR  (Linux, any client gate) set to a directory this user can write: the client's audio and display
              libraries need it.

The versions are each fork's version branches (neoforge-<v> with .port/<v>/branch.properties) and its head's
gradle.properties minecraft_version; --versions names them instead. --no-client skips the client's needs (libEGL,
XDG_RUNTIME_DIR) for a round that runs no Gate C. A single-version fork is checked the same way, at its one version.
  origin/HEAD (a version-branch fork) its refs/remotes/origin/HEAD, a WARNING when unset, not counted missing (the
              tools fall back to git ls-remote; fix: git remote set-head origin -a).
  remotes     each remote a version's upstream freeze names (upstream=<remote>/<branch>): without it the gates stop
              with exit 19; the fix is the fetch-only `git remote add` tools/fork-port.py prints.

A shallow single-branch clone of a version-branch fork holds no ref of its other version branches: their names are
read from origin (git ls-remote --heads origin 'neoforge-*') and each counts at its version's JDK. A single-version
fork's origin names are not read: its versions and verdict are as before.
Read-only: it writes nothing and fetches nothing (git ls-remote is its one network read). Standard library only.
"""
import argparse, importlib.util, os, pathlib, re, shutil, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(name, file):
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def git(repo, *a):
    r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout


def mc_key(v):
    return tuple(int(x) for x in re.findall(r"\d+", v))


def origin_slug(repo):
    rc, url = git(repo, "remote", "get-url", "origin")
    m = re.search(r"github\.com[/:]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", url.strip()) if rc == 0 else None
    return m.group(1) if m else None


def origin_heads(repo):
    """The branch names origin has (git ls-remote --heads origin 'neoforge-*'; never prompts), or [] when it cannot be
    read: a shallow single-branch clone has none of the other version branches as refs."""
    try:
        r = subprocess.run(["git", "-C", str(repo), "ls-remote", "--heads", "origin", "neoforge-*"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=60,
                           env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [ln.split("refs/heads/", 1)[1] for ln in r.stdout.splitlines() if "refs/heads/" in ln] if r.returncode == 0 else []


def fork_versions(repo, heads=origin_heads):
    """{version: java major} a clone builds: its version branches (local or origin's) and its checked-out
    gradle.properties minecraft_version, each version's own java_version first; then a version branch origin has that
    this clone holds no ref of (a shallow single-branch clone; weekly round 2026-10-19: its 26.x versions, and so JDK 25
    and libEGL, went unchecked), by its name (neoforge-<version>) and that version's JDK."""
    sg = _load("shipped_gate_pf", "shipped-gate.py")
    out = {}
    rc, refs = git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/neoforge-*", "refs/remotes/origin/neoforge-*")
    for ref in refs.splitlines() if rc == 0 else []:
        v = ref.split("neoforge-", 1)[1]
        rc2, props = git(repo, "show", f"{ref}:.port/{v}/branch.properties")
        if rc2 == 0 and v not in out:
            m = re.search(r"(?m)^java_version\s*=\s*(\d+)\s*$", props)
            out[v] = int(m.group(1)) if m else sg.java_major(v)
    mc = sg.gradle_prop(repo, "minecraft_version")
    if mc and mc not in out:
        out[mc] = sg.jdk_for(repo, mc)
    local = set(refs.split()) if rc == 0 else set()
    # origin's names count only for a version-branch fork (its checkout or a held version branch carries .port/): a
    # single-version fork's origin may hold the authors' or old work branches named neoforge-<v>, which it never builds
    for b in heads(repo) if is_branch_fork(repo, refs.splitlines() if rc == 0 else []) else []:
        v = b.split("neoforge-", 1)[1] if b.startswith("neoforge-") else ""
        if (re.fullmatch(r"\d+\.\d+(?:\.\d+)?", v) and v not in out and b not in local
                and f"origin/{b}" not in local):
            out[v] = sg.java_major(v)
    return out


def is_branch_fork(repo, refs=()):
    """Is the clone a fork of stacked version branches: its checkout carries .port/ (a version branch's
    .port/<mc>/branch.properties, or the port branch's records), or a held neoforge-<v> ref has
    .port/<v>/branch.properties?"""
    p = pathlib.Path(repo)
    if (p / ".port").is_dir():
        return True
    return any(git(repo, "cat-file", "-e", f"{ref}:.port/{ref.split('neoforge-', 1)[1]}/branch.properties")[0] == 0
               for ref in refs if "neoforge-" in ref)


def remote_checks(repo):
    """[(ok, what, detail)] for the remotes a round's gates read: origin/HEAD (the authors' base port-ci.py and
    port-offer.py name; a fresh cloud clone may lack it), and each remote a version's upstream freeze names
    (upstream=<remote>/<branch> in .port/<v>/branch.properties or .multiversion/base.properties): without it the gates
    stop with exit 19 (tools/fork-port.py DRIFT_EXIT: the freeze cannot be compared)."""
    out = []
    rc, refs = git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/neoforge-*", "refs/remotes/origin/neoforge-*")
    if is_branch_fork(repo, refs.splitlines() if rc == 0 else []):
        # a warning (None), never a MISSING: the tools fall back to git ls-remote --symref without it
        h, _ = git(repo, "symbolic-ref", "-q", "refs/remotes/origin/HEAD")
        out.append((True if h == 0 else None, f"origin/HEAD of {repo}", "set" if h == 0 else
                    f"not set (the authors' base is read from it, else from git ls-remote): git -C {repo} remote set-head origin -a"))
    named = {}
    for ref in (refs.splitlines() if rc == 0 else []) + ["HEAD"]:
        v = ref.split("neoforge-", 1)[1] if "neoforge-" in ref else None
        for f in ([f".port/{v}/branch.properties"] if v else []) + [".multiversion/base.properties"]:
            rc2, props = git(repo, "show", f"{ref}:{f}")
            m = re.search(r"(?m)^upstream\s*=\s*([\w.-]+)/\S+\s*$", props) if rc2 == 0 else None
            if m:
                named.setdefault(m.group(1), m.group(0).split("=", 1)[1].strip())
    remotes = set(git(repo, "remote")[1].split())
    for remote, ref in sorted(named.items()):
        if remote in remotes:
            out.append((True, f"remote {remote} of {repo}", f"the upstream freeze's {ref}"))
            continue
        hint = _load("fork_port_pf", "fork-port.py").add_remote_hint(repo, ref) or ""
        out.append((False, f"remote {remote} of {repo}", f"the upstream freeze names {ref}, and the gates stop with exit 19 "
                    "without it" + hint))
    return out


def has_lib(name, env=None, dirs=None):
    """Is the shared library `name` loadable: ldconfig's cache, else a file in the usual library directories."""
    env = os.environ if env is None else env
    if dirs is None:
        try:
            r = subprocess.run(["ldconfig", "-p"], capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode == 0 and re.search(rf"(?m)^\s*{re.escape(name)}\s", r.stdout):
                return True
        except OSError:
            pass
        dirs = ["/lib", "/usr/lib", "/lib64", "/usr/lib64", "/usr/lib/x86_64-linux-gnu", "/usr/lib/aarch64-linux-gnu",
                "/lib/x86_64-linux-gnu", "/lib/aarch64-linux-gnu"]
        dirs += [d for d in (env.get("LD_LIBRARY_PATH") or "").split(os.pathsep) if d]
    return any((pathlib.Path(d) / name).exists() for d in dirs)


def checks(forks, versions=None, client=True, env=None, find_java=None, lib=None, linux=None, gh=None, heads=None):
    """[(ok, what, detail)] for the round's needs. forks: [(owner/repo, dir)]. The keyword arguments stand in for the
    machine in the self-check."""
    env = os.environ if env is None else env
    linux = sys.platform.startswith("linux") if linux is None else linux
    if find_java is None:
        find_java = _load("shipped_gate_pf", "shipped-gate.py").find_java
    lib = lib or has_lib
    out, need = [], {}
    for slug, d in forks:
        p = pathlib.Path(d)
        got = origin_slug(p) if (p / ".git").exists() else None
        if not got:
            out.append((False, f"clone of {slug}", f"no clone at {p}: add {slug} to this scheduled task's repositories "
                        "(claude.ai/code/routines -> the task -> Edit -> Select a repository); a scheduled session cannot "
                        "attach one itself"))
            continue
        if got.lower() != slug.lower():
            out.append((False, f"clone of {slug}", f"{p} is a clone of {got}, not {slug}"))
            continue
        out.append((True, f"clone of {slug}", str(p)))
        out += remote_checks(p)
        for v, j in (fork_versions(p, heads or origin_heads) if versions is None else {}).items():
            need.setdefault(j, set()).add(v)
    for v in versions or ():
        need.setdefault(25 if re.match(r"26\.", v) else 21, set()).add(v)
    if env.get("GITHUB_TOKEN") or env.get("GH_TOKEN"):
        out.append((True, "github", ("GITHUB_TOKEN" if env.get("GITHUB_TOKEN") else "GH_TOKEN") + " over REST (wins over "
                    "gh: a broken gh login does not matter)"))
    elif (gh if gh is not None else shutil.which("gh")):
        out.append((True, "github", "the gh command line (no GITHUB_TOKEN or GH_TOKEN set)"))
    else:
        out.append((False, "github", "no GITHUB_TOKEN, GH_TOKEN or gh: the plan issue cannot be read"))
    for j in sorted(need):
        vs = ", ".join(sorted(need[j], key=mc_key))
        jv = find_java(j)
        out.append((bool(jv), f"JDK {j}", jv or f"none found for {vs} (install it -- Debian/Ubuntu: openjdk-{j}-jdk-headless -- "
                                                f"or set JAVA_HOME_{j})"))
    all_v = sorted({v for vs in need.values() for v in vs}, key=mc_key)
    if client and linux:
        egl = [v for v in all_v if mc_key(v) >= (26, 3)]
        if egl:
            ok = lib("libEGL.so.1")
            out.append((ok, "libEGL.so.1", "found" if ok else f"the {', '.join(egl)} client loads it: install libegl1 "
                                                                "(Debian/Ubuntu), or the Gate C client fails to start"))
        x = env.get("XDG_RUNTIME_DIR")
        good = bool(x) and os.path.isdir(x) and os.access(x, os.W_OK)
        out.append((good, "XDG_RUNTIME_DIR", x if good else ("unset" if not x else f"{x} is not a writable directory")
                    + ": export XDG_RUNTIME_DIR=$(mktemp -d) && chmod 700 \"$XDG_RUNTIME_DIR\" before the client gates"))
    return out


def self_check():
    import tempfile
    bad = []

    def expect(c, msg):
        if not c:
            bad.append(msg)
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        lib = d / "lib"
        run = lambda *a: subprocess.run(["git", "-C", str(lib), *a], capture_output=True, text=True, encoding="utf-8", check=True)
        lib.mkdir()
        run("init", "-q", "-b", "neoforge-1.21.1")
        (lib / "gradle.properties").write_text("minecraft_version=1.21.1\n", encoding="utf-8")
        run("add", "-A"); run("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "port")
        for v in ("26.1.2", "26.3"):
            run("checkout", "-q", "-b", f"neoforge-{v}")
            (lib / f".port/{v}").mkdir(parents=True)
            (lib / f".port/{v}/branch.properties").write_text(f"minecraft_version={v}\njava_version=25\n", encoding="utf-8")
            run("add", "-A"); run("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", v)
        run("checkout", "-q", "neoforge-1.21.1")
        run("remote", "add", "origin", "https://github.com/owner/examplelib.git")
        run("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/neoforge-1.21.1")
        none = lambda repo: []
        jdks = {21: "/jdk21/bin/java"}
        xdg = d / "xdg"; xdg.mkdir()
        res = checks([("owner/examplelib", lib), ("owner/examplemod", d / "absent")], env={"GITHUB_TOKEN": "x"},
                     find_java=jdks.get, lib=lambda n: False, linux=True, heads=none)
        by = {w: (ok, det) for ok, w, det in res}
        expect(not by["clone of owner/examplemod"][0] and "scheduled task's repositories" in by["clone of owner/examplemod"][1],
               f"a missing clone says to attach it to the task: {by}")
        expect(by["clone of owner/examplelib"][0] and by["github"][0] and "GITHUB_TOKEN" in by["github"][1], f"clone, github: {by}")
        expect(by["JDK 21"][0] and not by["JDK 25"][0] and "26.1.2, 26.3" in by["JDK 25"][1], f"JDK per version: {by}")
        expect(not by["libEGL.so.1"][0] and "26.3" in by["libEGL.so.1"][1] and not by["XDG_RUNTIME_DIR"][0], f"client needs: {by}")
        res = checks([("owner/examplelib", lib)], env={"XDG_RUNTIME_DIR": str(xdg)}, find_java=lambda j: f"/jdk{j}",
                     lib=lambda n: True, linux=True, gh="/usr/bin/gh", heads=none)
        expect(all(ok for ok, _w, _d in res) and any(w == "github" and "gh command line" in det for _o, w, det in res),
               f"everything there: all ok, gh named: {res}")
        res = checks([("owner/examplelib", lib)], versions=["1.21.1"], env={}, find_java=lambda j: None, linux=False, gh="", heads=none)
        expect([w for ok, w, _d in res if not ok] == ["github", "JDK 21"], f"--versions, no client off Linux: {res}")
        res = checks([("owner/examplelib", lib)], client=False, env={"GH_TOKEN": "y"}, find_java=lambda j: "/j", linux=True,
                     heads=none)
        expect(not any(w in ("libEGL.so.1", "XDG_RUNTIME_DIR") for _o, w, _d in res), "--no-client: no client checks")
        # weekly round 2026-10-19: (a) a shallow single-branch clone holds no ref of its 26.x branches: their names
        # from origin count, at their version's JDK (and the 26.3 client's libEGL); (b) the remote an upstream freeze
        # names; (c) origin/HEAD
        sh = d / "shallow"
        subprocess.run(["git", "clone", "-q", "--single-branch", "-b", "neoforge-26.3", str(lib), str(sh)], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(sh), "remote", "set-url", "origin", "https://github.com/owner/examplelib.git"], check=True)
        subprocess.run(["git", "-C", str(sh), "remote", "set-head", "origin", "-d"], check=True, capture_output=True)
        names = lambda repo: ["neoforge-1.21.1", "neoforge-26.1.2", "neoforge-26.3", "neoforge-1.21.x", "neoforge-26.3-wip"]
        expect(fork_versions(sh, none) == {"1.21.1": 21, "26.3": 25},
               f"control: the clone's refs alone miss 26.1.2: {fork_versions(sh, none)}")
        expect(fork_versions(sh, names) == {"1.21.1": 21, "26.1.2": 25, "26.3": 25},
               f"origin's version branches by name, at their JDK: {fork_versions(sh, names)}")
        res = checks([("owner/examplelib", sh)], env={"GH_TOKEN": "y", "XDG_RUNTIME_DIR": str(xdg)}, find_java=jdks.get,
                     lib=lambda n: False, linux=True, heads=names)
        by = {w: (ok, det) for ok, w, det in res}
        expect(not by["JDK 25"][0] and "26.1.2, 26.3" in by["JDK 25"][1] and not by["libEGL.so.1"][0],
               f"the shallow clone's 26.x needs are checked: {by}")
        expect(by[f"origin/HEAD of {sh}"][0] is None and f"git -C {sh} remote set-head origin -a" in by[f"origin/HEAD of {sh}"][1],
               f"a version-branch clone with no origin/HEAD: a warning, with the fix: {by}")
        expect(fork_versions(lib, names) == fork_versions(lib, none) == {"1.21.1": 21, "26.1.2": 25, "26.3": 25},
               "a clone holding the branches: the same versions whether origin is read or not")
        sv = d / "single"; sv.mkdir()
        subprocess.run(["git", "-C", str(sv), "init", "-q", "-b", "neoforge-1.21.1"], check=True)
        (sv / "gradle.properties").write_text("minecraft_version=1.21.1\n", encoding="utf-8")
        expect(fork_versions(sv, lambda repo: ["neoforge-1.21.1"]) == fork_versions(sv, none) == {"1.21.1": 21},
               "a single-version fork: its one version, as before")
        run("checkout", "-q", "neoforge-26.3")
        (lib / ".port/26.3/branch.properties").write_text("minecraft_version=26.3\njava_version=25\nupstream=authors/main\n"
                                                         "upstream_commit=" + "a" * 40 + "\n", encoding="utf-8")
        run("add", "-A"); run("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "freeze")
        run("checkout", "-q", "neoforge-1.21.1")
        rm = {w: (ok, det) for ok, w, det in remote_checks(lib)}
        expect(not rm[f"remote authors of {lib}"][0] and "exit 19" in rm[f"remote authors of {lib}"][1]
               and "git remote add authors" in rm[f"remote authors of {lib}"][1]
               and "set-url --push authors DISABLED" in rm[f"remote authors of {lib}"][1] and rm[f"origin/HEAD of {lib}"][0],
               f"the freeze's remote missing: exit 19 named, the fetch-only add command: {rm}")
        run("remote", "add", "authors", "https://github.com/owner/upstreamlib.git")
        rm = {w: (ok, det) for ok, w, det in remote_checks(lib)}
        expect(rm[f"remote authors of {lib}"][0], f"the remote there: ok: {rm}")
        expect(remote_checks(sv) == [], f"a single-version fork: no remote line at all, as before: {remote_checks(sv)}")
        # a single-version fork whose origin also has a neoforge-26.x branch that is no port (the authors', old work):
        # its versions and its verdict exactly as before origin was read
        expect(fork_versions(sv, lambda repo: ["neoforge-1.21.1", "neoforge-26.3", "neoforge-26.1.2"]) == {"1.21.1": 21},
               f"a single-version fork: origin's other neoforge-<v> names not counted: {fork_versions(sv, lambda repo: ['neoforge-26.3'])}")
        run_sv = lambda *a: subprocess.run(["git", "-C", str(sv), *a], capture_output=True, text=True, check=True)
        run_sv("remote", "add", "origin", "https://github.com/owner/examplemod.git")
        res_sv = checks([("owner/examplemod", sv)], env={"GH_TOKEN": "y", "XDG_RUNTIME_DIR": str(xdg)}, find_java=jdks.get,
                        lib=lambda n: False, linux=True, heads=lambda repo: ["neoforge-1.21.1", "neoforge-26.3"])
        expect(all(ok is True for ok, _w, _d in res_sv) and not any("origin/HEAD" in w or "JDK 25" in w for _o, w, _d in res_sv),
               f"a single-version fork with no origin/HEAD and an authors' 26.x branch: all ok, no new line: {res_sv}")
        expect(has_lib("libX.so.1", {}, [str(d)]) is False and (d / "libX.so.1").write_bytes(b"") == 0
               and has_lib("libX.so.1", {}, [str(d)]), "has_lib looks in the directories given")
    for m in bad:
        print("FAIL:", m)
    print("self-check:", "OK" if not bad else "FAILED")
    return 0 if not bad else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fork", action="append", default=[], metavar="OWNER/REPO=DIR", help="a fork of the set and its clone")
    ap.add_argument("--versions", help="comma-separated Minecraft versions (default: what each clone builds)")
    ap.add_argument("--no-client", action="store_true", help="no Gate C client this round: skip libEGL and XDG_RUNTIME_DIR")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args(argv)
    if a.self_check:
        return self_check()
    forks = []
    for f in a.fork:
        slug, sep, d = f.partition("=")
        if not sep or not re.fullmatch(r"[\w.-]+/[\w.-]+", slug):
            ap.error(f"--fork OWNER/REPO=DIR, got {f!r}")
        forks.append((slug, pathlib.Path(d).expanduser()))
    if not forks and not a.versions:
        ap.error("--fork OWNER/REPO=DIR (one per fork of the set) or --versions")
    res = checks(forks, a.versions.split(",") if a.versions else None, client=not a.no_client)
    for ok, what, det in res:
        print(f"preflight: {'ok     ' if ok else 'WARNING' if ok is None else 'MISSING'} {what}: {det}")
    missing = [w for ok, w, _d in res if ok is False]
    print(f"preflight: {len(missing)} missing" + (f" ({', '.join(missing)}): fix these first; a missing clone stops the "
                                                   "round before it freezes the plan" if missing else ""))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
