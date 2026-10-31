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
  origin/HEAD (every kind of fork) its refs/remotes/origin/HEAD, a WARNING when unset, not counted missing (the
              tools fall back to git ls-remote; fix: git remote set-head origin -a).
  remotes     (a version-branch fork) each remote a version's upstream freeze names (upstream=<remote>/<branch>):
              without it the gates stop with exit 19; the fix is the fetch-only `git remote add` tools/fork-port.py
              prints. (a single-version fork, tools/fork-kind.py detect) a remote fetching the authors' repository its
              entry in the set's .github/weekly-plan.json names (`upstream`; read from the clones given, the library's
              head first), which port-sync.py merges from: missing, with the fetch-only add, when none does; no line
              when no clone given carries that config. Missing even when the plan takes no authors' code: the
              preflight runs before the plan is frozen, so it cannot know.

A shallow or single-branch clone (a cloud session's: the fork's GitHub default branch, which may be the authors' own
branch, not the port's head) holds none of origin's neoforge-<v> branches, so it would read as an unported tree: no
26.x version (no JDK 25), no upstream freeze (no remote line) and no weekly-plan.json (it is on the head branch only).
So first, as tools/round.py init does moments later, each clone that lacks one of origin's neoforge-* branches (or is
shallow) gets them fetched as remote-tracking refs (refs/remotes/origin/neoforge-*; no branch or working tree moves,
nothing when the clone already has them all; a single-branch clone's fetch refspec gains neoforge-*: the one
implementation, make_whole, which init runs too); a fetch that fails is MISSING with git's message, as init then refuses
(origin listed within 60 s, a stalled transfer ended, no prompt). When origin cannot
be listed, a shallow clone's other version branches are still counted by name (git ls-remote) at their version's JDK;
a single-version fork's origin names are not counted: its versions are as before. weekly-plan.json is read from HEAD,
origin/HEAD, then the clone's neoforge-* head branches (no .port/<v>/branch.properties: the set's config lives on a
head), then its version branches. Besides those refs and that refspec line it writes nothing. Standard library only.
"""
import argparse, importlib.util, json, os, pathlib, re, shutil, subprocess, sys, time

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


LIST_TIMEOUT = 60           # git ls-remote: the preflight's one read of origin before (origin_heads)
FETCH_TIMEOUT = 1800        # a whole fetch (an unshallow of a large fork); a stalled transfer ends sooner (lowSpeed*)


def net_git(repo, args, timeout):
    """(rc, stdout, stderr) of a git command that talks to origin: no prompt (GIT_TERMINAL_PROMPT=0), a stalled
    transfer ended by git itself (http.lowSpeedLimit/Time), the whole bounded by `timeout`; its output through files,
    never pipes (Windows: a killed git leaves git-remote-https holding a pipe, and the timeout would wait on it)."""
    import tempfile
    with tempfile.TemporaryFile() as o, tempfile.TemporaryFile() as e:
        try:
            rc = subprocess.run(["git", "-C", str(repo), "-c", "http.lowSpeedLimit=1", "-c", "http.lowSpeedTime=30", *args],
                                stdin=subprocess.DEVNULL, stdout=o, stderr=e, timeout=timeout,
                                env=dict(os.environ, GIT_TERMINAL_PROMPT="0")).returncode
        except subprocess.TimeoutExpired:
            rc = None
        except OSError as x:
            return 1, "", str(x)
        o.seek(0); e.seek(0)
        out, err = (f.read().decode("utf-8", "replace") for f in (o, e))
    if rc is None:
        return 1, out, (err.strip() + f" (no answer in {timeout} s)").strip()
    return rc, out, err


def make_whole(repo, say=None):
    """None, or why the clone cannot be made whole -- the one implementation tools/round.py init runs too
    (fetch_version_branches), run here first so the checks read what the round will (weekly round 2026-10-31: a
    dependent's clone of its default branch held no 26.x branch, so no JDK 25 line, and its 26.1.2 measure stopped on
    the Gradle toolchain; the library's held no freeze, so no line for its missing `authors` remote). A shallow or
    single-branch clone has none of origin's neoforge-<v> branches, so a version-branch fork would read as
    single-version: origin's neoforge-* branches are fetched whole as remote-tracking refs (the clone made whole when it
    is shallow; a single-branch clone's fetch refspec gains neoforge-*) when one is missing, and nothing is fetched when
    none is. say(what) is told what was fetched."""
    rc, out, err = net_git(repo, ["ls-remote", "--heads", "origin", "refs/heads/neoforge-*"], LIST_TIMEOUT)
    if rc:
        return f"cannot list origin's branches ({(err or out).strip()[-200:]}): the fork's kind is read from them"
    remote = sorted(ln.split("refs/heads/", 1)[1] for ln in out.splitlines() if "refs/heads/" in ln)
    shallow = git(repo, "rev-parse", "--is-shallow-repository")[1].strip() == "true"
    missing = [b for b in remote if git(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{b}")[0]]
    if not missing and not shallow:
        return None
    rc, out, err = net_git(repo, ["fetch", "-q", *(["--unshallow"] if shallow else []), "origin",
                                  *[f"+refs/heads/{b}:refs/remotes/origin/{b}" for b in remote]], FETCH_TIMEOUT)
    if rc:
        return (f"a shallow or single-branch clone (missing {', '.join(missing) or 'its history'}), and fetching them "
                f"failed: {(err or out).strip()[-200:]} -- clone the fork whole (no --depth/--single-branch)")
    if missing and not any("*" in x for x in git(repo, "config", "--get-all", "remote.origin.fetch")[1].split()):
        # a single-branch clone's fetch refspec names one branch: later `git fetch origin <b>` must update origin/<b>
        git(repo, "config", "--add", "remote.origin.fetch", "+refs/heads/neoforge-*:refs/remotes/origin/neoforge-*")
    if say:
        say(", ".join(missing) if missing else "history (the clone was shallow)")
    return None


def is_branch_fork(repo, refs=()):
    """Is the clone a fork of stacked version branches: its checkout carries .port/ (a version branch's
    .port/<mc>/branch.properties, or the port branch's records), or a held neoforge-<v> ref has
    .port/<v>/branch.properties?"""
    p = pathlib.Path(repo)
    if (p / ".port").is_dir():
        return True
    return any(git(repo, "cat-file", "-e", f"{ref}:.port/{ref.split('neoforge-', 1)[1]}/branch.properties")[0] == 0
               for ref in refs if "neoforge-" in ref)


def norm_url(u):
    """A repository URL compared loosely: scheme, `git@host:` form, `.git` and case do not matter."""
    u = (u or "").strip().lower()
    u = re.sub(r"^git@([^:]+):", r"https://\1/", u)
    u = re.sub(r"^(?:https?|ssh|git)://(?:[^@/]+@)?", "", u)
    return re.sub(r"(?:\.git)?/*$", "", u)


def plan_upstreams(dirs):
    """{fork slug (lower case): (the authors' repository URL, their branch)} from .github/weekly-plan.json at HEAD (else
    origin/HEAD, else the clone's neoforge-* head branches -- no .port/<v>/branch.properties -- then its version
    branches, lowest version first) of each clone given: the library's head branch holds the set's config
    (keep-current, "The fork set"), and a clone of the GitHub default branch may not be on it."""
    out = {}
    for d in dirs:
        rc, refs = git(d, "for-each-ref", "--format=%(refname:short)", "refs/heads/neoforge-*",
                       "refs/remotes/origin/neoforge-*")
        vb = lambda r: git(d, "cat-file", "-e", f"{r}:.port/{r.split('neoforge-', 1)[1]}/branch.properties")[0] == 0
        held = sorted(refs.split() if rc == 0 else [], key=lambda r: (vb(r), mc_key(r.split("neoforge-", 1)[1]), r))
        for ref in ("HEAD", "origin/HEAD", *held):
            rc, cfg = git(d, "show", f"{ref}:.github/weekly-plan.json")
            if rc != 0:
                continue
            try:
                forks = json.loads(cfg).get("forks", [])
            except (ValueError, AttributeError):
                forks = []
            for f in forks if isinstance(forks, list) else []:
                if isinstance(f, dict) and f.get("repo") and f.get("upstream"):
                    out.setdefault(str(f["repo"]).lower(), (str(f["upstream"]), str(f.get("upstream_branch") or "")))
            break
    return out


def authors_remote_check(repo, upstreams):
    """A single-version fork's authors' remote (the check a version branch's upstream freeze gets, from the one record
    a single-version fork has: its weekly-plan.json `upstream`): a remote of the clone that fetches that URL, which
    port-sync.py --upstream <remote>/<branch> merges from. [] when the config names no upstream for this fork."""
    slug = origin_slug(repo)
    up = upstreams.get((slug or "").lower())
    if not up:
        return []
    url, branch = up
    for name in git(repo, "remote")[1].split():
        if norm_url(git(repo, "remote", "get-url", name)[1]) == norm_url(url):
            return [(True, f"authors' remote of {repo}", f"{name} fetches {url}: port-sync.py --upstream {name}/{branch or '<branch>'}")]
    return [(False, f"authors' remote of {repo}", f"no remote fetches {url} (the weekly plan's upstream for {slug}), which "
             f"the round's port-sync.py merges from -- add it once, fetch-only: git -C {repo} remote add authors {url} && "
             f"git -C {repo} remote set-url --push authors DISABLED && git -C {repo} fetch authors")]


def is_single_version(repo):
    """tools/fork-kind.py detect (the one route check): no neoforge-<v> branch, local or origin's, carries
    .port/<v>/branch.properties."""
    return _load("fork_kind_pf", "fork-kind.py").detect(repo)[0] == "single-version"


def remote_checks(repo, upstreams=None):
    """[(ok, what, detail)] for the remotes a round's gates read: origin/HEAD on every kind of fork (the authors' base
    port-ci.py and port-offer.py name; a fresh cloud clone may lack it), and each remote a version's upstream freeze
    names (upstream=<remote>/<branch> in .port/<v>/branch.properties or .multiversion/base.properties): without it the
    gates stop with exit 19 (tools/fork-port.py DRIFT_EXIT: the freeze cannot be compared). A single-version fork has
    no freeze: its authors' remote is the one fetching its weekly-plan.json `upstream` (upstreams, plan_upstreams())."""
    out = []
    rc, refs = git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/neoforge-*", "refs/remotes/origin/neoforge-*")
    # a warning (None), never a MISSING: the tools fall back to git ls-remote --symref without it
    h, _ = git(repo, "symbolic-ref", "-q", "refs/remotes/origin/HEAD")
    out.append((True if h == 0 else None, f"origin/HEAD of {repo}", "set" if h == 0 else
                f"not set (the authors' base is read from it, else from git ls-remote): git -C {repo} remote set-head origin -a"))
    if upstreams and is_single_version(repo):
        out += authors_remote_check(repo, upstreams)
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


def checks(forks, versions=None, client=True, env=None, find_java=None, lib=None, linux=None, gh=None, heads=None,
           whole=None):
    """[(ok, what, detail)] for the round's needs. forks: [(owner/repo, dir)]. The keyword arguments stand in for the
    machine in the self-check (whole: make_whole)."""
    env = os.environ if env is None else env
    linux = sys.platform.startswith("linux") if linux is None else linux
    if find_java is None:
        find_java = _load("shipped_gate_pf", "shipped-gate.py").find_java
    lib = lib or has_lib
    whole = whole or make_whole
    out, need, made = [], {}, {}
    for slug, d in forks:           # each clone made whole first, as round.py init does: the checks read what it will
        p = pathlib.Path(d)
        if (p / ".git").exists() and (origin_slug(p) or "").lower() == slug.lower():
            made[slug] = whole(p)
    ups = plan_upstreams([pathlib.Path(d) for _s, d in forks if (pathlib.Path(d) / ".git").exists()])
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
        if made.get(slug):
            out.append((False, f"version branches of {slug}", made[slug]))
        out += remote_checks(p, ups)
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
        nf = lambda repo: None              # the clones here are whole already, and their origin is no real host
        jdks = {21: "/jdk21/bin/java"}
        xdg = d / "xdg"; xdg.mkdir()
        res = checks(whole=nf, forks=[("owner/examplelib", lib), ("owner/examplemod", d / "absent")], env={"GITHUB_TOKEN": "x"},
                     find_java=jdks.get, lib=lambda n: False, linux=True, heads=none)
        by = {w: (ok, det) for ok, w, det in res}
        expect(not by["clone of owner/examplemod"][0] and "scheduled task's repositories" in by["clone of owner/examplemod"][1],
               f"a missing clone says to attach it to the task: {by}")
        expect(by["clone of owner/examplelib"][0] and by["github"][0] and "GITHUB_TOKEN" in by["github"][1], f"clone, github: {by}")
        expect(by["JDK 21"][0] and not by["JDK 25"][0] and "26.1.2, 26.3" in by["JDK 25"][1], f"JDK per version: {by}")
        expect(not by["libEGL.so.1"][0] and "26.3" in by["libEGL.so.1"][1] and not by["XDG_RUNTIME_DIR"][0], f"client needs: {by}")
        res = checks(whole=nf, forks=[("owner/examplelib", lib)], env={"XDG_RUNTIME_DIR": str(xdg)}, find_java=lambda j: f"/jdk{j}",
                     lib=lambda n: True, linux=True, gh="/usr/bin/gh", heads=none)
        expect(all(ok for ok, _w, _d in res) and any(w == "github" and "gh command line" in det for _o, w, det in res),
               f"everything there: all ok, gh named: {res}")
        res = checks(whole=nf, forks=[("owner/examplelib", lib)], versions=["1.21.1"], env={}, find_java=lambda j: None, linux=False, gh="", heads=none)
        expect([w for ok, w, _d in res if not ok] == ["github", "JDK 21"], f"--versions, no client off Linux: {res}")
        res = checks(whole=nf, forks=[("owner/examplelib", lib)], client=False, env={"GH_TOKEN": "y"}, find_java=lambda j: "/j", linux=True,
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
        res = checks(whole=nf, forks=[("owner/examplelib", sh)], env={"GH_TOKEN": "y", "XDG_RUNTIME_DIR": str(xdg)}, find_java=jdks.get,
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
        rs = remote_checks(sv)
        expect([(ok, w) for ok, w, _d in rs] == [(None, f"origin/HEAD of {sv}")] and "remote set-head origin -a" in rs[0][2],
               f"a single-version fork with no origin/HEAD: the same warning as a version-branch head (#161 U1, E6): {rs}")
        # a single-version fork whose origin also has a neoforge-26.x branch that is no port (the authors', old work):
        # its versions and its verdict exactly as before origin was read
        expect(fork_versions(sv, lambda repo: ["neoforge-1.21.1", "neoforge-26.3", "neoforge-26.1.2"]) == {"1.21.1": 21},
               f"a single-version fork: origin's other neoforge-<v> names not counted: {fork_versions(sv, lambda repo: ['neoforge-26.3'])}")
        run_sv = lambda *a: subprocess.run(["git", "-C", str(sv), *a], capture_output=True, text=True, encoding="utf-8", check=True)
        run_sv("remote", "add", "origin", "https://github.com/owner/examplemod.git")
        res_sv = checks(whole=nf, forks=[("owner/examplemod", sv)], env={"GH_TOKEN": "y", "XDG_RUNTIME_DIR": str(xdg)}, find_java=jdks.get,
                        lib=lambda n: False, linux=True, heads=lambda repo: ["neoforge-1.21.1", "neoforge-26.3"])
        expect(all(ok is not False for ok, _w, _d in res_sv) and not any("JDK 25" in w for _o, w, _d in res_sv)
               and [w for ok, w, _d in res_sv if ok is None] == [f"origin/HEAD of {sv}"],
               f"a single-version fork with no origin/HEAD and an authors' 26.x branch: nothing missing, the origin/HEAD "
               f"warning, no 26.x JDK: {res_sv}")
        # E6: a single-version fork's authors' remote, from the set's weekly-plan.json (on the library's head)
        cfg = {"forks": [{"repo": "owner/examplelib", "upstream": "https://github.com/up/examplelib", "upstream_branch": "main"},
                         {"repo": "owner/examplemod", "upstream": "https://github.com/up/examplemod", "upstream_branch": "dev"}]}
        (sv / ".github").mkdir()
        (sv / ".github/weekly-plan.json").write_text(json.dumps(cfg), encoding="utf-8")
        run_sv("add", "-A"); run_sv("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "plan")
        ups = plan_upstreams([sv])
        expect(ups.get("owner/examplemod") == ("https://github.com/up/examplemod", "dev"), f"plan_upstreams: {ups}")
        rs = {w: (ok, det) for ok, w, det in remote_checks(sv, ups)}
        expect(rs[f"authors' remote of {sv}"][0] is False and "git -C" in rs[f"authors' remote of {sv}"][1]
               and "remote add authors https://github.com/up/examplemod" in rs[f"authors' remote of {sv}"][1]
               and "set-url --push authors DISABLED" in rs[f"authors' remote of {sv}"][1],
               f"a single-version fork without its authors' remote: missing, with the fetch-only add: {rs}")
        run_sv("remote", "add", "upstream", "git@github.com:Up/examplemod.git")
        rs = {w: (ok, det) for ok, w, det in remote_checks(sv, ups)}
        expect(rs[f"authors' remote of {sv}"][0] is True and "--upstream upstream/dev" in rs[f"authors' remote of {sv}"][1],
               f"the authors' remote there (another URL form): ok: {rs}")
        res = checks(whole=nf, forks=[("owner/examplemod", sv)], env={"GH_TOKEN": "y", "XDG_RUNTIME_DIR": str(xdg)}, find_java=jdks.get,
                     lib=lambda n: False, linux=True, heads=none)
        expect(any(w == f"authors' remote of {sv}" and ok for ok, w, _d in res), f"checks() reads the set's config: {res}")
        # a version-branch fork with the same config: its remote lines are its freezes', exactly as before
        before = remote_checks(lib)
        expect(remote_checks(lib, {"owner/examplelib": ("https://github.com/up/examplelib", "main")}) == before
               and not any("authors' remote" in w for _o, w, _d in before),
               f"a version-branch fork: no weekly-plan remote line, its freeze lines unchanged: {before}")
        # weekly round 2026-10-31: a cloud clone of the fork's GitHub default branch (the authors' own branch, the port
        # head not checked out) holds none of the port's branches -- no 26.x JDK, no freeze remote, no weekly-plan.json
        # read. make_whole fetches origin's neoforge-* branches first, as round.py init does, and the checks see them.
        au = d / "authors-default"; au.mkdir()
        ga = lambda *a: subprocess.run(["git", "-C", str(au), *a], capture_output=True, text=True, encoding="utf-8", check=True)
        cm = lambda m: (ga("add", "-A"), ga("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", m))
        ga("init", "-q", "-b", "main"); ga("config", "core.autocrlf", "false")
        (au / "gradle.properties").write_text("minecraft_version=1.20.1\n", encoding="utf-8"); cm("authors")
        ga("checkout", "-q", "-b", "neoforge-1.21.1")
        (au / "gradle.properties").write_text("minecraft_version=1.21.1\n", encoding="utf-8")
        (au / ".port").mkdir(); (au / ".port/KNOWN-ISSUES.json").write_text("{}\n", encoding="utf-8")
        (au / ".github").mkdir(); (au / ".github/weekly-plan.json").write_text(json.dumps(cfg), encoding="utf-8"); cm("port")
        ga("checkout", "-q", "-b", "neoforge-26.1.2"); (au / ".port/26.1.2").mkdir()
        (au / ".port/26.1.2/branch.properties").write_text("minecraft_version=26.1.2\njava_version=25\nupstream=authors/main\n",
                                                          encoding="utf-8"); cm("26.1.2")
        ga("checkout", "-q", "main")
        gh_url = "https://github.com/owner/examplelib.git"

        def cloud_clone(name, branch):
            c = d / name
            subprocess.run(["git", "clone", "-q", "--single-branch", "--depth", "1", "-b", branch, au.as_uri(), str(c)],
                           check=True, capture_output=True)
            return c
        cc = cloud_clone("cloud", "main")
        subprocess.run(["git", "-C", str(cc), "remote", "set-url", "origin", gh_url], check=True)
        res0 = checks(whole=nf, forks=[("owner/examplelib", cc)], env={"GH_TOKEN": "y"}, find_java=jdks.get, linux=False,
                      heads=none)
        expect([w for ok, w, _d in res0 if ok is False] == [],
               f"control: the clone as given hides its 26.x JDK and its freeze's remote (nothing missing): {res0}")
        subprocess.run(["git", "-C", str(cc), "remote", "set-url", "origin", au.as_uri()], check=True)
        expect(make_whole(cc) is None and git(cc, "rev-parse", "--verify", "-q", "refs/remotes/origin/neoforge-26.1.2")[0] == 0
               and git(cc, "rev-parse", "--abbrev-ref", "HEAD")[1].strip() == "main",
               "make_whole fetches origin's neoforge-* branches as remote-tracking refs; the checkout stays where it was")
        expect(make_whole(cc) is None, "a whole clone: nothing to fetch, no error")
        # a version branch LOWER than the head that carries an older config: the head's config wins
        ga("checkout", "-q", "-b", "neoforge-1.20.6", "neoforge-26.1.2"); (au / ".port/1.20.6").mkdir()
        (au / ".port/1.20.6/branch.properties").write_text("minecraft_version=1.20.6\n", encoding="utf-8")
        (au / ".github/weekly-plan.json").write_text(json.dumps({"forks": [{"repo": "owner/examplemod", "upstream":
                                                     "https://github.com/stale/examplemod"}]}), encoding="utf-8")
        cm("an older config on a version branch"); ga("checkout", "-q", "main")
        told = []
        expect(make_whole(cc, say=told.append) is None and told == ["neoforge-1.20.6"],
               f"a branch origin gained is fetched, and said: {told}")
        bad_ = d / "no-origin"
        subprocess.run(["git", "clone", "-q", "--single-branch", "-b", "main", au.as_uri(), str(bad_)], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(bad_), "remote", "set-url", "origin", (d / "gone.git").as_uri()], check=True)
        why_ = make_whole(bad_) or ""
        expect(why_.startswith("cannot list origin's branches (") and ("gone.git" in why_ or "fatal" in why_),
               f"origin that cannot be listed: git's own message in the MISSING line: {why_}")
        ff_ = d / "fetch-fails"         # origin lists, the fetch fails: git's message in the MISSING line
        subprocess.run(["git", "clone", "-q", "--single-branch", "-b", "main", au.as_uri(), str(ff_)], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(ff_), "config", "fetch.negotiationAlgorithm", "bogus"], check=True)
        why_ = make_whole(ff_) or ""
        expect("and fetching them failed: " in why_ and "bogus" in why_ and "clone the fork whole" in why_,
               f"a fetch that fails: git's message in the MISSING line: {why_}")
        if os.name != "nt":      # the ext transport runs a shell command (POSIX here)
            ext = ["-c", "protocol.ext.allow=always", "ls-remote"]
            (d / "env.sh").write_text("echo prompt=$GIT_TERMINAL_PROMPT >&2\nexit 1\n", encoding="utf-8")
            (d / "hang.sh").write_text("sleep 20\n", encoding="utf-8")
            env0_ = os.environ.get("GIT_TERMINAL_PROMPT")
            os.environ["GIT_TERMINAL_PROMPT"] = "1"        # the caller's own setting does not matter
            try:
                rc_, _o, e_ = net_git(au, ext + [f"ext::sh {(d / 'env.sh').as_posix()}"], 30)
            finally:
                os.environ.pop("GIT_TERMINAL_PROMPT") if env0_ is None else os.environ.update(GIT_TERMINAL_PROMPT=env0_)
            expect(rc_ != 0 and "prompt=0" in e_, f"no terminal prompt on a network read: {rc_} {e_}")
            t_ = time.time()
            rc_, _o, e_ = net_git(au, ext + [f"ext::sh {(d / 'hang.sh').as_posix()}"], 2)
            expect(rc_ == 1 and "no answer in 2 s" in e_ and time.time() - t_ < 15,
                   f"a network read that never answers is cut off: {rc_} {e_} {time.time() - t_:.1f}s")
        subprocess.run(["git", "-C", str(cc), "remote", "set-url", "origin", gh_url], check=True)
        seen = []
        res = checks(whole=lambda r: seen.append(pathlib.Path(r)) or None, forks=[("owner/examplelib", cc)],
                     env={"GH_TOKEN": "y"}, find_java=jdks.get, linux=False, heads=none)
        by = {w: (ok, det) for ok, w, det in res}
        expect(seen == [cc], f"checks() makes each clone whole before reading it: {seen}")
        j25, ra = by.get("JDK 25", (True, "")), by.get(f"remote authors of {cc}", (True, ""))
        expect(not j25[0] and "26.1.2" in j25[1] and ra[0] is False and "exit 19" in ra[1] and f"authors' remote of {cc}" not in by,
               f"the whole clone: JDK 25 for 26.1.2 and the freeze's `authors` remote are missing: {by}")
        expect(plan_upstreams([cc]).get("owner/examplemod") == ("https://github.com/up/examplemod", "dev"),
               f"weekly-plan.json read from the head branch (not a lower version branch) when HEAD and origin/HEAD lack "
               f"it: {plan_upstreams([cc])}")
        res = checks(whole=lambda r: "fetching them failed", forks=[("owner/examplelib", cc)], env={"GH_TOKEN": "y"},
                     find_java=jdks.get, linux=False, heads=none)
        expect(any(ok is False and w == "version branches of owner/examplelib" and "failed" in det for ok, w, det in res),
               f"a clone that cannot be made whole is missing: {res}")
        # a single-version fork's clone of its head (origin has only that branch): make_whole fetches nothing and the
        # checks are exactly as before
        sh1 = d / "sv-origin"; sh1.mkdir()
        g1 = lambda *a: subprocess.run(["git", "-C", str(sh1), *a], capture_output=True, text=True, encoding="utf-8", check=True)
        g1("init", "-q", "-b", "neoforge-1.21.1")
        (sh1 / "gradle.properties").write_text("minecraft_version=1.21.1\n", encoding="utf-8")
        g1("add", "-A"); g1("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "port")
        svc = d / "sv-cloud"
        subprocess.run(["git", "clone", "-q", "--single-branch", "-b", "neoforge-1.21.1", sh1.as_uri(), str(svc)],
                       check=True, capture_output=True)
        refs_before = git(svc, "for-each-ref")[1]
        expect(make_whole(svc) is None and git(svc, "for-each-ref")[1] == refs_before
               and git(svc, "config", "--get-all", "remote.origin.fetch")[1].split() == ["+refs/heads/neoforge-1.21.1:refs/remotes/origin/neoforge-1.21.1"],
               "a single-version fork's clone of its one branch: nothing fetched, no ref or config written")
        subprocess.run(["git", "-C", str(svc), "remote", "set-url", "origin", "https://github.com/owner/examplemod.git"], check=True)
        a_ = checks(whole=nf, forks=[("owner/examplemod", svc)], env={"GH_TOKEN": "y"}, find_java=jdks.get, linux=False, heads=none)
        expect([w for ok, w, _d in a_ if ok is False] == [] and not any("version branches" in w for _o, w, _d in a_),
               f"the single-version verdict unchanged: {a_}")
        expect(has_lib("libX.so.1", {}, [str(d)]) is False and (d / "libX.so.1").write_bytes(b"") == 0
               and has_lib("libX.so.1", {}, [str(d)]), "has_lib looks in the directories given")
    for m in bad:
        print("FAIL:", m)
    print("self-check:", "OK" if not bad else "FAILED")
    return 0 if not bad else 1


def _refuse_layer(repo, tool):
    """tools/fork-kind.py's refusal of a checkout with the removed .multiversion/ layer at HEAD: None, or the message
    (print it and exit 2)."""
    import importlib.util as _ilu
    s = _ilu.spec_from_file_location("fork_kind", pathlib.Path(__file__).resolve().parent / "fork-kind.py")
    m = _ilu.module_from_spec(s); s.loader.exec_module(m)
    return m.refuse_layer(repo, tool)


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
        why = _refuse_layer(pathlib.Path(d).expanduser(), "round-preflight")
        if why:
            print(f"preflight: {slug}: {why}")
            return 2
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
