#!/usr/bin/env python3
"""The jobs of a fork's multi-version layer branch, as commands a CI workflow (and tools/fork-port.py) can run.

    python3 tools/layer-ci.py plan      --repo . [--github-output FILE]   # which targets: newest + the middle ones
    python3 tools/layer-ci.py inertness --repo . --work DIR               # the layer hook changes no base jar
    python3 tools/layer-ci.py compile   --repo . --mc T --work DIR [--summary FILE]
                                                                          # compile every source set + both audits

The layer branch (templates/fork-layer) may differ from the fork's 1.21.1 port branch only inside .multiversion/ and
by one appended commented hook line in build.gradle, so its CI cannot be a workflow file of its own: the SAME
port-ci.yml (templates/upstream-harness/port-ci.yml) carries the layer jobs on both branches and they switch
themselves off where there is no .multiversion/. This tool is what those jobs call:

  plan        reads .multiversion/targets and prints the newest target (VERSION-sorted: 1.21.11 is newer than 1.21.8,
              which a lexical sort gets backwards), the middle targets as a Actions matrix, and each one's Java
              version (target.properties `java_version`, following `inherits`, else 21). No .multiversion/base.properties
              or no target: layer=false and every layer job is skipped.
  inertness   builds the fork's jars (`assemble` + every Jar task the authors registered) with the hook line and again
              without it, and runs the fork's own `layer.py compare-jars` on each pair. Zero jars compared is a failure,
              not a pass (CATALOG X27), as is a build.gradle with no hook line to remove.
  compile     one middle target: `.multiversion/build.sh <t>` over EVERY source set's compile task (srcsets.py, the list
              the authors' `build` and fork-port.py's measure stage use), then the mixin audit against that target's
              staged sources jar and the event-listener audit against Gradle's own resolved classpath. These are the
              checks that go red when a change on the 1.21.1 branch breaks only one middle target's overlay.

CI policy (fork-versions skill): the bookends (the base branch's 1.21.1 and the NEWEST target) get every gate on every
push; every middle target gets compile + mixin audit + event-listener audit only. A middle target that is RELEASED
gets the full gates too, and the fork says so in `.multiversion/ci.properties`:

    full_gates=1.21.4,1.21.8        # comma list of middle targets; absent or empty = none (the default)

`plan` then also prints `has_full` / `full_matrix` for the `layer-gates-extra` job. A name that is not a target is an
error (a typo must not quietly mean "no gates"); the newest target is always fully gated, so naming it is redundant.
A fork without ci.properties behaves exactly as before.

One run per checkout (CATALOG X44): `acquire_lock()` is the lock tools/fork-port.py and `ci-gates.py --mc` take on
build/.fork-port.lock (pid, stale-pid aware). Without a layer target the lock is never taken.

target_classpath() and sources_jars() are shared with fork-port.py, so the CI audit and the port's audit stage
cannot drift apart. Standard library only.
"""
import argparse, datetime, importlib.util, json, os, pathlib, re, shutil, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
HOOK_MARK = "apply from: '.multiversion/layer.gradle'"
DEFAULT_JAVA = "21"


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------- pure helpers (covered by --self-check)

def version_key(v):
    """Sort key for a Minecraft version: numeric parts compare as numbers ('1.21.11' > '1.21.8'), words after."""
    return tuple((1, int(p)) if p.isdigit() else (0, p) for p in re.findall(r"\d+|[A-Za-z]+", v))


def read_props(path):
    out = {}
    p = pathlib.Path(path)
    if p.is_file():
        for l in p.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([\w.]+)\s*=\s*(.*?)\s*$", l)
            if m and not l.lstrip().startswith("#"):
                out[m.group(1)] = m.group(2)
    return out


def java_of(mv, target):
    """java_version of a layer target, following `inherits`; the base's 21 when nothing says."""
    seen, t = set(), target
    while t and t not in seen:
        seen.add(t)
        p = read_props(pathlib.Path(mv) / "targets" / t / "target.properties")
        if p.get("java_version"):
            return p["java_version"]
        t = p.get("inherits")
    return DEFAULT_JAVA


def full_gate_targets(mv, targets):
    """(extra full-gate targets, problems) from .multiversion/ci.properties `full_gates=a,b`. Only MIDDLE targets are
    returned (the newest is always fully gated, so naming it is redundant); a name that is not a target is a problem."""
    raw = read_props(pathlib.Path(mv) / "ci.properties").get("full_gates", "")
    names = [x.strip() for x in raw.split(",") if x.strip()]
    bad = [n for n in names if n not in targets]
    newest = targets[-1] if targets else None
    mid = sorted({n for n in names if n in targets and n != newest}, key=version_key)
    return mid, [f"full_gates names {n}, which is not a target in .multiversion/targets" for n in bad]


def plan(repo):
    mv = pathlib.Path(repo) / ".multiversion"
    tdir = mv / "targets"
    targets = sorted((d.name for d in tdir.iterdir() if d.is_dir() and (d / "target.properties").is_file()),
                     key=version_key) if tdir.is_dir() else []
    if not (mv / "base.properties").is_file() or not targets:
        return {"layer": "false", "targets": [], "newest": "", "newest_java": DEFAULT_JAVA, "middle": [],
                "has_middle": "false", "matrix": json.dumps({"include": []}, separators=(",", ":")), "shipped": "false",
                "full_extra": [], "has_full": "false", "full_matrix": json.dumps({"include": []}, separators=(",", ":"))}
    newest, middle = targets[-1], targets[:-1]
    inc = [{"t": t, "java": java_of(mv, t)} for t in middle]
    full, problems = full_gate_targets(mv, targets)
    if problems:
        raise SystemExit("plan: " + "; ".join(problems))
    finc = [{"t": t, "java": java_of(mv, t)} for t in full]
    return {"layer": "true", "targets": targets, "newest": newest, "newest_java": java_of(mv, newest),
            "middle": middle, "has_middle": "true" if middle else "false",
            "matrix": json.dumps({"include": inc}, separators=(",", ":")),
            "shipped": "true" if list(mv.glob("shipped-expect*.txt")) else "false",
            "full_extra": full, "has_full": "true" if full else "false",
            "full_matrix": json.dumps({"include": finc}, separators=(",", ":"))}


def strip_hook(text):
    """(text without the hook line(s), how many were removed). Comment lines are left: they do nothing."""
    keep, n = [], 0
    for l in text.splitlines(keepends=True):
        if l.strip() == HOOK_MARK:
            n += 1
        else:
            keep.append(l)
    return "".join(keep), n


# ---------------------------------------------------------------- one run per checkout (CATALOG X44)

LOCK_REL = "build/.fork-port.lock"
LOCK_ENV = "MV_CHECKOUT_LOCK_HOLDER"      # a child of the lock holder (fork-port.py -> ci-gates.py) is let through


def pid_alive(pid):
    """Is a process with this pid running? (A reused pid reads as alive: the lock then needs deleting by hand, which
    the refusal message says.)"""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)      # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return bool(ok) and code.value == 259                            # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class CheckoutLock:
    """An exclusive lock on one checkout: build/.fork-port.lock holding {"pid", "tool", "target", "started"}.
    acquire() -> None when taken (or inherited from a live parent that holds it), else the refusal message.
    A lock whose pid is dead is stale and is taken over. release() removes only a lock this object created."""

    def __init__(self, repo, tool, target="", pid=None, alive=pid_alive, env=None):
        self.path = pathlib.Path(repo) / LOCK_REL
        self.tool, self.target = tool, target
        self.pid = pid if pid is not None else os.getpid()
        self.alive = alive
        self.env = env if env is not None else os.environ
        self.mine = False

    def _read(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(3):
            body = json.dumps({"pid": self.pid, "tool": self.tool, "target": self.target,
                               "started": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                held = self._read()
                hp = held.get("pid") if isinstance(held, dict) else None
                if isinstance(hp, int) and hp != self.pid and self.alive(hp):
                    if str(hp) == str(self.env.get(LOCK_ENV, "")):
                        return None           # our parent holds it and started us: its run, not a second one
                    return (f"another {held.get('tool', 'run')} is using this checkout (pid {hp}, target "
                            f"{held.get('target') or '-'}, since {held.get('started', '?')}): one gate run per checkout "
                            f"(CATALOG X44) -- two share build/, the Gradle daemon and the CPU, and their results are "
                            f"not attributable. Wait for it, or if pid {hp} is not really that run, delete {self.path}")
                try:
                    self.path.unlink()           # stale (dead pid) or unreadable: take it over
                except FileNotFoundError:
                    pass
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(body)
            self.mine = True
            self.env[LOCK_ENV] = str(self.pid)
            return None
        return f"could not take {self.path} (it keeps being recreated by another process)"

    def release(self):
        if self.mine:
            held = self._read()
            if isinstance(held, dict) and held.get("pid") == self.pid:
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
            if self.env.get(LOCK_ENV) == str(self.pid):
                del self.env[LOCK_ENV]
            self.mine = False


# ---------------------------------------------------------------- shared with fork-port.py

def sources_jars(repo, t):
    d = pathlib.Path(repo) / "build" / f"mc{t}" / "moddev" / "artifacts"
    return sorted(j for j in d.glob("*-sources.jar") if "resources" not in j.name)


def no_ci_env():
    """The environment for a Gradle call whose result the audits read. ModDevGradle/NFRT decompile Minecraft (the
    sources jar the mixin audit compares against, and the merged jar) only when the CI variable is unset: with CI
    set, `compileJava` still succeeds and stages neither (measured on a GitHub runner: 'no staged sources jar')."""
    return {k: v for k, v in os.environ.items() if k != "CI"}


def target_classpath(repo, t, work, extra_init=(), bash="bash", env=None):
    """Gradle's own resolved compile classpath for layer target t (tools/qtc-init.gradle through the fork's
    .multiversion/build.sh), written under `work`. Never rebuilt by hand (CATALOG X25b-ii). -> (path|None, why)."""
    repo, work = pathlib.Path(repo), pathlib.Path(work)
    work.mkdir(parents=True, exist_ok=True)
    cmd = [bash, str(repo / ".multiversion/build.sh"), t, "-I", str(HERE / "qtc-init.gradle")]
    for x in extra_init:
        cmd += ["-I", str(x)]
    cmd += ["-q", "qtcClasspath", "--console=plain"]
    r = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    m = re.search(r"QTC_CLASSPATH_BEGIN\n(.*?)QTC_CLASSPATH_END", r.stdout or "", re.S)
    jars = [l.strip() for l in m.group(1).splitlines() if l.strip()] if m else []
    if not jars:
        return None, f"could not resolve the {t} classpath: {(r.stdout or '')[-300:]}"
    gone = [j for j in jars if not pathlib.Path(j).exists()]
    if gone:
        return None, f"the {t} classpath names {len(gone)} file(s) that do not exist, e.g. {gone[0]}"
    cp = work / f"qtc-classpath-{t}.txt"
    cp.write_text("\n".join(jars) + "\n", encoding="utf-8")
    return cp, ""


# ---------------------------------------------------------------- commands

def _run(cmd, cwd, log, env=None):
    with open(log, "w", encoding="utf-8") as f:
        return subprocess.run(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                              errors="replace", env=env).returncode


def _tail_cause(log):
    text = pathlib.Path(log).read_text(encoding="utf-8", errors="replace")
    m = re.search(r"\* What went wrong:\n(.*?)(?:\n\* Try:|\Z)", text, re.S)
    errs = re.findall(r"(?m)^.*: error: .*$", text)
    return (("\n".join(dict.fromkeys(errs[:12])) + "\n") if errs else "") + (m.group(1).strip() if m else text[-1500:])


def cmd_plan(a):
    p = plan(a.repo)
    print(json.dumps(p, indent=1))
    if a.github_output:
        with open(a.github_output, "a", encoding="utf-8") as f:
            for k in ("layer", "newest", "newest_java", "has_middle", "matrix", "shipped", "has_full", "full_matrix"):
                f.write(f"{k}={p[k]}\n")
    return 0


def cmd_inertness(a):
    repo, work = pathlib.Path(a.repo).resolve(), pathlib.Path(a.work).resolve()
    ss = _load("srcsets", "srcsets.py")
    layer = repo / ".multiversion/tools/layer.py"
    bg = repo / "build.gradle"
    original = bg.read_text(encoding="utf-8")
    stripped, n = strip_hook(original)
    if n != 1:
        print(f"inertness: build.gradle holds {n} hook line(s) ({HOOK_MARK}); expected exactly 1 -- nothing to compare")
        return 1
    tasks = ["assemble", *ss.author_jar_tasks(repo)]
    sets = {}
    try:
        for label, text in (("with-hook", original), ("without-hook", stripped)):
            bg.write_text(text, encoding="utf-8", newline="")
            cmd = ["bash", "gradlew", "--console=plain", "clean", *tasks]   # clean both: classes an earlier run left in build/ are not the hook's doing
            log = work / f"inertness-{label}.log"
            work.mkdir(parents=True, exist_ok=True)
            if _run(cmd, repo, log):
                print(f"inertness: the {label} build failed:\n{_tail_cause(log)}")
                return 1
            dst = work / label
            shutil.rmtree(dst, ignore_errors=True); dst.mkdir(parents=True)
            for j in (repo / "build/libs").glob("*.jar"):
                shutil.copy2(j, dst / j.name)
            sets[label] = sorted(p.name for p in dst.glob("*.jar"))
    finally:
        bg.write_text(original, encoding="utf-8", newline="")
    a_, b_ = sets["with-hook"], sets["without-hook"]
    if not a_ or a_ != b_:
        print(f"inertness: the two builds produced different jars (with: {a_}, without: {b_}) or none")
        return 1
    bad = 0
    for name in a_:
        r = subprocess.run([sys.executable, str(layer), "compare-jars", str(work / "with-hook" / name),
                            str(work / "without-hook" / name)], capture_output=True, text=True, encoding="utf-8")
        print(f"{name}: {r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr.strip()[-200:]}")
        if r.returncode:
            print(r.stdout[-1500:]); bad += 1
    print(f"inertness: {len(a_)} jar(s) compared, {'DIFFER' if bad else 'all identical with and without the hook'}")
    return 1 if bad else 0


def cmd_compile(a):
    repo, work, t = pathlib.Path(a.repo).resolve(), pathlib.Path(a.work).resolve(), a.mc
    work.mkdir(parents=True, exist_ok=True)
    if not (repo / ".multiversion/targets" / t).is_dir():
        print(f"compile: no .multiversion/targets/{t} in {repo}")
        return 2
    ss = _load("srcsets", "srcsets.py")
    tasks = ss.compile_tasks(repo)
    rows, ok = [], True

    def record(name, passed, detail):
        nonlocal ok
        ok &= passed
        rows.append(f"| {name} | {'PASS' if passed else 'FAIL'} | {detail} |")
        print(f"[layer-ci] {t} {name}: {'PASS' if passed else 'FAIL'} -- {detail}", flush=True)

    log = work / f"compile-{t}.log"
    rc = _run(["bash", str(repo / ".multiversion/build.sh"), t, *tasks, "--console=plain"], repo, log, no_ci_env())
    compiled = rc == 0
    record(f"compile ({' '.join(tasks)})", rc == 0, "0 errors" if rc == 0 else "see the log")
    if rc:
        print(_tail_cause(log))
    if ok:
        neo = read_props(repo / ".multiversion/targets" / t / "target.properties").get("prop.neo_version", "")
        jars = sources_jars(repo, t)
        jars = [j for j in jars if neo and neo in j.name] or jars
        prepared = repo / "build" / f"mc{t}" / "mv" / "java"
        if not jars:
            record("mixin audit", False, "no staged sources jar to audit against")
        else:
            r = subprocess.run([sys.executable, str(HERE / "audit-mixin-targets.py"), str(repo), str(jars[0]),
                                f"--sources={prepared}"], capture_output=True, text=True, encoding="utf-8",
                               errors="replace")
            lines = [l for l in r.stdout.strip().splitlines() if l.strip()]
            (work / f"mixin-audit-{t}.log").write_text(r.stdout + r.stderr, encoding="utf-8")
            record("mixin audit", r.returncode == 0,
                   (lines[-1] if lines else "no output")[:200] if r.returncode == 0 else
                   ("checked no targets (not a pass)" if r.returncode == 2 else
                    f"{len([l for l in lines if 'mixin:' in l]) or len(lines)} mixin target(s) do not match vanilla"))
            if r.returncode:
                print(r.stdout[-3000:])
    if compiled:
        trees = sorted(str(p) for p in (repo / "build" / f"mc{t}" / "mv").glob("*java") if p.is_dir())
        cp, why = target_classpath(repo, t, work, env=no_ci_env())
        if not trees or cp is None:
            record("event-listener audit", False, why or "no prepared tree to audit")
        else:
            r = subprocess.run([sys.executable, str(HERE / "audit-event-listeners.py"), str(repo),
                                f"--cp-file={cp}", f"--tree={','.join(trees)}", f"--mc={t}"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            lines = [l for l in r.stdout.strip().splitlines() if l.strip()]
            (work / f"listener-audit-{t}.log").write_text(r.stdout + r.stderr, encoding="utf-8")
            record("event-listener audit", r.returncode == 0,
                   (lines[0] if lines else "event listeners ok")[:200] if r.returncode == 0 else
                   ("checked no listeners (not a pass)" if r.returncode == 2 else
                    f"{sum('ABSTRACT' in l for l in lines)} listener(s) name an abstract event"))
            if r.returncode:
                print(r.stdout[-3000:])
    if not compiled:
        for n in ("mixin audit", "event-listener audit"):
            rows.append(f"| {n} | not run | the compile failed |")
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()
    md = [f"Layer target {t} at `{head}` (compile-only; the full gates run on the newest target):", "",
          "| check | result | detail |", "|---|---|---|", *rows, ""]
    if a.summary:
        pathlib.Path(a.summary).write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    return 0 if ok else 1


def self_check():
    ok = True

    def expect(c, msg):
        nonlocal ok
        if not c:
            ok = False; print("FAIL:", msg)
    names = ["1.21.10", "1.21.11", "1.21.3", "1.21.4", "1.21.5", "1.21.8"]
    expect(sorted(names, key=version_key)[-1] == "1.21.11", "1.21.11 is newer than 1.21.8 (a lexical sort says 1.21.8)")
    expect(sorted(names)[-1] == "1.21.8", "control: the lexical sort really is wrong here")
    expect(sorted(names, key=version_key) == ["1.21.3", "1.21.4", "1.21.5", "1.21.8", "1.21.10", "1.21.11"], "full order")
    expect(sorted(["26.1", "1.21.11", "26.2"], key=version_key) == ["1.21.11", "26.1", "26.2"], "26.x after 1.21.x")
    with tempfile.TemporaryDirectory() as d:
        r = pathlib.Path(d)
        expect(plan(r)["layer"] == "false" and plan(r)["matrix"] == '{"include":[]}', "no .multiversion: layer=false")
        mv = r / ".multiversion"
        (mv / "targets").mkdir(parents=True)
        (mv / "base.properties").write_text("x=1\n", encoding="utf-8")
        expect(plan(r)["layer"] == "false", "no targets: layer=false")
        for t, body in (("1.21.8", "inherits=1.21.5\n"), ("1.21.11", "inherits=1.21.10\n"), ("1.21.10", "inherits=1.21.8\n"),
                        ("1.21.5", "java_version=21\n"), ("26.1", "java_version=25\n")):
            (mv / "targets" / t).mkdir()
            (mv / "targets" / t / "target.properties").write_text(body, encoding="utf-8")
        (mv / "targets" / "notatarget").mkdir()                           # no target.properties: not a target
        p = plan(r)
        expect(p["newest"] == "26.1" and p["newest_java"] == "25", "newest by version, with its own java")
        expect(p["middle"] == ["1.21.5", "1.21.8", "1.21.10", "1.21.11"], f"middle targets: {p['middle']}")
        inc = json.loads(p["matrix"])["include"]
        expect(all(i["java"] == "21" for i in inc) and len(inc) == 4, "java follows inherits, default 21")
        (mv / "targets" / "26.1").rename(mv / "targets" / "x")
        expect(plan(r)["newest"] == "1.21.11" and plan(r)["has_middle"] == "true", "newest after removing 26.1")
        for t in ("1.21.5", "1.21.8", "1.21.10", "x"):
            shutil.rmtree(mv / "targets" / t)
        expect(plan(r)["has_middle"] == "false" and plan(r)["newest"] == "1.21.11", "one target: nothing in the middle")
        expect(plan(r)["shipped"] == "false", "no shipped expectations")
        (mv / "shipped-expect-1.21.11.txt").write_text("x\n", encoding="utf-8")
        expect(plan(r)["shipped"] == "true", "shipped-expect*.txt is noticed")
        (mv / "targets" / "1.21.11" / "target.properties").write_text("prop.neo_version=1\n", encoding="utf-8")
        expect(java_of(mv, "1.21.11") == "21", "no java_version anywhere: 21")
    # CI policy: full_gates (ci.properties) names extra fully gated MIDDLE targets; default none; typos are errors
    with tempfile.TemporaryDirectory() as d:
        r = pathlib.Path(d); mv = r / ".multiversion"
        (mv / "targets").mkdir(parents=True)
        (mv / "base.properties").write_text("x=1\n", encoding="utf-8")
        for t_ in ("1.21.3", "1.21.4", "1.21.8", "1.21.10", "1.21.11"):
            (mv / "targets" / t_).mkdir()
            (mv / "targets" / t_ / "target.properties").write_text("java_version=21\n", encoding="utf-8")
        p0 = plan(r)
        expect(p0["has_full"] == "false" and p0["full_extra"] == [] and json.loads(p0["full_matrix"]) == {"include": []},
               "no ci.properties: no extra full-gate target (the default)")
        (mv / "ci.properties").write_text("# policy\nfull_gates = 1.21.10, 1.21.4 ,1.21.11\n", encoding="utf-8")
        p1 = plan(r)
        expect(p1["full_extra"] == ["1.21.4", "1.21.10"], f"extras are middle targets in version order, newest dropped: {p1['full_extra']}")
        expect([i["t"] for i in json.loads(p1["full_matrix"])["include"]] == ["1.21.4", "1.21.10"] and p1["has_full"] == "true",
               "full_matrix")
        expect(p1["middle"] == p0["middle"] and p1["matrix"] == p0["matrix"] and p1["newest"] == p0["newest"],
               "the compile-only matrix does not change: an extra target is compiled AND gated")
        (mv / "ci.properties").write_text("full_gates=1.21.9\n", encoding="utf-8")
        try:
            plan(r); expect(False, "a typo in full_gates must fail the plan")
        except SystemExit as e:
            expect("1.21.9" in str(e), "the failure names the unknown target")
        (mv / "ci.properties").write_text("full_gates=\n", encoding="utf-8")
        expect(plan(r)["has_full"] == "false", "empty list = none")
        # a single-version fork (no .multiversion) is untouched, ci.properties or not
        r2 = pathlib.Path(d) / "single"; r2.mkdir()
        (r2 / "ci.properties").write_text("full_gates=1.21.4\n", encoding="utf-8")
        q = plan(r2)
        expect(q["layer"] == "false" and q["has_full"] == "false", "no .multiversion: layer=false, nothing extra")
    # one run per checkout
    with tempfile.TemporaryDirectory() as d:
        alive = {1001, 1002}
        mk = lambda pid, env=None, tool="fork-port.py": CheckoutLock(d, tool, "1.21.4", pid=pid, alive=lambda x: x in alive,
                                                                       env={} if env is None else env)
        a_, b_ = mk(1001), mk(1002)
        expect(a_.acquire() is None and (pathlib.Path(d) / LOCK_REL).is_file(), "first run takes the lock")
        msg = b_.acquire()
        expect(msg and "1001" in msg and "one gate run per checkout" in msg and "delete" in msg, f"second run refused: {msg}")
        expect(not b_.mine, "a refused run owns nothing")
        b_.release()
        expect((pathlib.Path(d) / LOCK_REL).is_file(), "a refused run's release leaves the holder's lock")
        kid = mk(1002, env={LOCK_ENV: "1001"}, tool="ci-gates.py")
        expect(kid.acquire() is None and not kid.mine, "a child of the holder is let through and owns nothing")
        kid.release()
        expect((pathlib.Path(d) / LOCK_REL).is_file(), "the child's release leaves the parent's lock")
        a_.release()
        expect(not (pathlib.Path(d) / LOCK_REL).exists(), "release removes the lock")
        (pathlib.Path(d) / "build").mkdir(exist_ok=True)
        (pathlib.Path(d) / LOCK_REL).write_text(json.dumps({"pid": 4242, "tool": "fork-port.py"}), encoding="utf-8")
        c_ = mk(1002)
        expect(c_.acquire() is None and c_.mine and json.loads((pathlib.Path(d) / LOCK_REL).read_text(encoding="utf-8"))["pid"] == 1002,
               "a dead holder's lock is stale and taken over")
        c_.release()
        (pathlib.Path(d) / LOCK_REL).write_text("garbage", encoding="utf-8")
        e_ = mk(1001)
        expect(e_.acquire() is None, "an unreadable lock file is stale")
        e_.release()
    expect(pid_alive(os.getpid()) and not pid_alive(0), "pid_alive: this process is alive, pid 0 is not")
    t, n = strip_hook(f"a\n// c\n{HOOK_MARK}\n")
    expect(n == 1 and HOOK_MARK not in t and "// c" in t, "strip_hook removes the hook line, keeps comments")
    expect(strip_hook("a\n")[1] == 0, "no hook: counted 0")
    os.environ["CI"] = "true"
    expect("CI" not in no_ci_env() and "PATH" in no_ci_env(), "no_ci_env drops CI only")
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    if "--self-check" in sys.argv:
        return self_check()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan"); p.add_argument("--repo", default="."); p.add_argument("--github-output")
    p = sub.add_parser("inertness"); p.add_argument("--repo", default="."); p.add_argument("--work", required=True)
    p = sub.add_parser("compile"); p.add_argument("--repo", default="."); p.add_argument("--mc", required=True)
    p.add_argument("--work", required=True); p.add_argument("--summary")
    a = ap.parse_args()
    return {"plan": cmd_plan, "inertness": cmd_inertness, "compile": cmd_compile}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
