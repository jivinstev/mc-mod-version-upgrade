#!/usr/bin/env python3
"""Shipped-jar gate: boot the BUILT mod jar in a real NeoForge server the way a player installs it, and read the
mod's own logs.

    python3 tools/shipped-gate.py --repo DIR --jar BUILT.jar [--mc 1.21.8 --neo 21.8.54] [--java-home DIR]
                                  [--expect-log FILE:REGEX ...] [--expect-file FILE] [--variant NAME] [--deps JAR ...]
                                  [--work-dir DIR] [--cache-dir DIR] [--timeout SECONDS] [--settle SECONDS]

Every other gate (Gate A/B/C) runs the mod from the DEV classpath. FML discovers some things only on the launch
class loader there -- a coremod's ClassProcessorProvider, a service file -- and discovers them differently from a
jar in mods/. So a subsystem can run in every dev gate and be inert in the file a player downloads, and no dev gate
can see it. This one installs a real NeoForge server (installer from maven.neoforged.net, cached per version),
puts ONLY the built jar (+ --deps) in mods/, starts it, waits for "Done (", lets it run --settle seconds more
(default 10, about 200 ticks: a crash on the first world tick comes after "Done ("), sends `stop`, and then checks
each --expect-log FILE:REGEX against <server>/logs/FILE (the mod's own log, e.g. ExampleBootstrap.log). A missing
expectation is a FAIL: that is the point. So is any crash report (a file in crash-reports/, "Exception in server
tick loop", "Encountered an unexpected exception") and a server that exits after "Done (" before it is told to stop.

--expect-file reads one expectation per line (FILE:REGEX, '#' comments). Without it the fork's own list is used,
the first that exists of: .port/<mc>/shipped-expect-<variant>.txt (a version branch, per shipped jar variant:
--variant cf for the `*_cf.jar` one), then .port/<mc>/shipped-expect.txt, on every kind of fork; <mc> is --mc or else
the checkout's gradle.properties minecraft_version. None of them: no expectations, the jar is only booted.
--neo defaults to the checkout's gradle.properties neo_version.
The JDK is the one the Minecraft version needs (1.21.x: 21, 26.x: 25; a version branch's own java_version in
.port/<mc>/branch.properties first), the version being --mc or else the checkout's gradle.properties.
Exit 0 pass, 1 fail, 2 bad input. --self-check runs the model-free parts. Standard library only.
"""
import argparse, importlib.util, os, pathlib, re, shutil, socket, subprocess, sys, tempfile, time, urllib.request, zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAVEN = "https://maven.neoforged.net/releases/net/neoforged/neoforge"


def say(msg):
    print(f"[shipped-gate] {msg}", flush=True)


def java_major(mc):
    """The JDK a Minecraft version needs: 1.21.x -> 21, 26.x -> 25."""
    return 25 if re.match(r"26\.", mc or "") else 21


def gradle_prop(repo, key):
    gp = pathlib.Path(repo) / "gradle.properties"
    m = re.search(rf"^{key}\s*=\s*(\S+)", gp.read_text(encoding="utf-8"), re.M) if gp.is_file() else None
    return m and m.group(1)


def jdk_for(repo, mc):
    """The JDK to boot the server on: a version branch's own java_version (.port/<mc>/branch.properties, as the gates
    job's toolchain reads it), else the one its Minecraft version needs. With no --mc the Minecraft version is the
    checkout's gradle.properties minecraft_version, not a 1.21 default: a 26.x branch's server is Java 25 bytecode."""
    mc = mc or gradle_prop(repo, "minecraft_version") or "1.21"
    bp = pathlib.Path(repo) / ".port" / mc / "branch.properties"
    jv = re.search(r"(?m)^java_version\s*=\s*(\d+)\s*$", bp.read_text(encoding="utf-8")) if bp.is_file() else None
    return int(jv.group(1)) if jv else java_major(mc)


JDK_ROOTS = ("/usr/lib/jvm", "/opt", "/opt/java", "/usr/local", "~/.gradle/jdks", "~/.jdks", "~/.sdkman/candidates/java",
             "/Library/Java/JavaVirtualMachines")


def java_candidates(major, explicit=None, env=None, roots=JDK_ROOTS):
    """The JDK homes to try for `major`, in order: --java-home; JAVA_HOME (the one the person set, used when its java
    -version is that major -- find_java checks every candidate); JAVA_HOME_<major> (and the _X64/_ARM64 forms that
    setup-java and the hosted runners set); each directory of the usual install roots whose name carries the version
    as a whole number (/usr/lib/jvm, /opt, the Gradle toolchain downloads in ~/.gradle/jdks, ~/.jdks, SDKMAN, macOS)."""
    env = os.environ if env is None else env
    cands = [explicit] if explicit else []
    if env.get("JAVA_HOME"):
        cands.append(env["JAVA_HOME"])
    cands += [env[k] for k in (f"JAVA_HOME_{major}", f"JAVA_HOME_{major}_X64", f"JAVA_HOME_{major}_ARM64") if env.get(k)]
    for root in roots:
        r = pathlib.Path(os.path.expanduser(root))
        for d in sorted(r.glob("*")) if r.is_dir() else []:
            if d.is_dir() and re.search(rf"(?:^|[^0-9.]){major}(?:[.+_-]|$)", d.name):
                cands.append(str(d / "Contents/Home") if (d / "Contents/Home/bin").is_dir() else str(d))
    return list(dict.fromkeys(cands))


def no_jdk_message(major):
    """What find_java looked at, in the order java_candidates tries them."""
    return (f"no JDK {major} found (looked at JAVA_HOME, JAVA_HOME_{major}, {', '.join(JDK_ROOTS)}); install it or "
            "pass --java-home")


def find_java(major, explicit=None):
    cands = java_candidates(major, explicit)
    for c in cands:
        j = next((x for x in (pathlib.Path(c) / "bin" / "java", pathlib.Path(c) / "bin" / "java.exe") if x.is_file()), None)
        if j is not None:
            out = subprocess.run([str(j), "-version"], capture_output=True, text=True, encoding="utf-8").stderr
            m = re.search(r'version "(\d+)', out)
            if m and int(m.group(1)) == major:
                return str(j)
    return None


def parse_expect(lines):
    """['FILE:REGEX', ...] -> [(file, compiled regex)]; ValueError on a bad one."""
    out = []
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        f, sep, rx = s.partition(":")
        if not sep or not f or not rx or "/" in f or "\\" in f:
            raise ValueError(f"expectation must be FILE:REGEX with FILE a bare name under logs/, got {s!r}")
        out.append((f, re.compile(rx)))
    return out


def variant_of(glob):
    """The variant a shipped-jar glob names: '*_cf.jar' -> 'cf', '*-fabric.jar' -> 'fabric', '*.jar' -> None."""
    m = re.search(r"[_-]([A-Za-z0-9]+)[*?]*\.jar$", glob or "")
    return m.group(1).lower() if m else None


def expect_file(repo, mc=None, variant=None):
    """The fork's own expectations file for this jar, or None: .port/<mc>/shipped-expect-<variant>.txt, then
    .port/<mc>/shipped-expect.txt (<mc>: the given one, else gradle.properties'), on every kind of fork."""
    repo = pathlib.Path(repo)
    pmc = mc or gradle_prop(repo, "minecraft_version")
    names = []
    if pmc:
        names += ([f".port/{pmc}/shipped-expect-{variant}.txt"] if variant else []) + [f".port/{pmc}/shipped-expect.txt"]
    return next((repo / n for n in names if (repo / n).is_file()), None)


CRASH = re.compile(r"Exception in server tick loop|Encountered an unexpected exception|---- Minecraft Crash Report ----"
                   r"|This crash report has been saved to")


def crash_seen(run, text):
    """A crash the server reported, as one line, or None: a file in <run>/crash-reports/, else a crash line in the
    console/latest.log text. "Done (" is no proof: a server can crash on its first tick after it."""
    cr = pathlib.Path(run) / "crash-reports"
    reports = sorted(p.name for p in cr.iterdir() if p.is_file()) if cr.is_dir() else []
    if reports:
        return f"crash report crash-reports/{reports[0]}"
    hit = next((l.strip() for l in text.splitlines() if CRASH.search(l)), None)
    return hit[:300] if hit else None


def check_expect(logs_dir, expects):
    """-> [(file, regex, matched line or None, why)] -- one row per expectation."""
    rows = []
    for f, rx in expects:
        p = pathlib.Path(logs_dir) / f
        if not p.is_file():
            rows.append((f, rx.pattern, None, "log file absent"))
            continue
        hit = next((l.strip() for l in p.read_text(encoding="utf-8", errors="replace").splitlines() if rx.search(l)), None)
        rows.append((f, rx.pattern, hit, "" if hit else "no line matches"))
    return rows


def crash_cause(text):
    """The most telling line of a failed server log: FML's failure message, else the last 'Caused by', else the last
    exception line (the same priorities as tools/gate-loop.py failure_of)."""
    lines = text.splitlines()
    caused = [l.strip() for l in lines if l.startswith("Caused by:")]
    if caused:
        return caused[-1][:300]
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip().startswith("Failure message:"):
            return " ".join(x.strip() for x in lines[i:i + 2])[:300]
    for l in reversed(lines):
        if re.search(r"\b(?:[a-z_]\w*\.)+[A-Z]\w*(?:Exception|Error)\b", l) and not l.lstrip().startswith("at "):
            return l.strip()[:300]
    for l in lines:
        if re.search(r"ModLoadingException|Error loading mods|Mod loading has failed|requires .* or above", l):
            return l.strip()[:300]
    return lines[-1].strip()[:300] if lines else "no output"


def load_problems(text):
    """Mod-loading problems that do not stop a dedicated server dead: FML lists them and exits, or runs without
    the mod. Returned as lines; empty when clean."""
    return [l.strip()[:300] for l in text.splitlines()
            if re.search(r"Mod loading has failed|Error loading mods|ModLoadingException|"
                         r"has failed to load correctly|Missing or unsupported mandatory dependencies", l)
            and "/DEBUG]" not in l][:5]


def installer(neo, cache, java):
    """Installed server directory for a NeoForge version, cached (copy it, never run in the cache)."""
    srv = cache / f"neoforge-{neo}-server"
    if (srv / ".installed").is_file():
        return srv
    inst = cache / f"neoforge-{neo}-installer.jar"
    if not inst.is_file():
        url = f"{MAVEN}/{neo}/neoforge-{neo}-installer.jar"
        say(f"downloading {url}")
        try:
            with urllib.request.urlopen(url, timeout=120) as r, open(str(inst) + ".part", "wb") as f:
                shutil.copyfileobj(r, f)
        except OSError as e:
            raise SystemExit(f"[shipped-gate] cannot download the installer: {e}")
        os.replace(str(inst) + ".part", inst)
    if srv.exists():
        shutil.rmtree(srv)
    srv.mkdir(parents=True)
    say(f"installing NeoForge {neo} server into {srv} (headless)")
    log = cache / f"install-{neo}.log"
    r = subprocess.run([java, "-jar", str(inst), "--install-server", str(srv)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(srv))
    log.write_text(r.stdout + r.stderr, encoding="utf-8")
    if r.returncode or not list(srv.glob("libraries/net/neoforged/neoforge/*/unix_args.txt")):
        raise SystemExit(f"[shipped-gate] the NeoForge installer failed (see {log}): {crash_cause(r.stdout + r.stderr)}")
    (srv / ".installed").write_text(neo, encoding="utf-8")
    return srv


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def prepare(work, cache_srv, jar, deps, port):
    run = work / "server"
    if run.exists():
        shutil.rmtree(run)
    shutil.copytree(cache_srv, run)
    (run / ".installed").unlink(missing_ok=True)
    mods = run / "mods"
    mods.mkdir(exist_ok=True)
    for j in [jar, *deps]:
        shutil.copy2(j, mods / pathlib.Path(j).name)
    (run / "eula.txt").write_text("eula=true\n", encoding="utf-8")
    (run / "server.properties").write_text(
        f"server-port={port}\nonline-mode=false\nlevel-type=minecraft\\:flat\nview-distance=2\n"
        "simulation-distance=2\nspawn-protection=0\nmax-players=1\nenable-command-block=false\n"
        "network-compression-threshold=-1\nsync-chunk-writes=false\nspawn-monsters=false\n", encoding="utf-8")
    (run / "user_jvm_args.txt").write_text("-Xmx1G\n", encoding="utf-8")
    return run


def boot(run, java, timeout, settle=10.0, poll=1.0):
    """-> (ok, reason, console text). Start, wait for 'Done (', let it tick `settle` seconds, send stop, wait for
    exit. A crash report, or an exit before `stop` was sent, fails it even after 'Done ('."""
    args = sorted(run.glob("libraries/net/neoforged/neoforge/*/unix_args.txt"))[0]
    cmd = [java, "@user_jvm_args.txt", f"@{args.relative_to(run)}", "nogui"]
    console = run / "console.log"
    with open(console, "wb") as out:
        p = subprocess.Popen(cmd, cwd=str(run), stdin=subprocess.PIPE, stdout=out, stderr=subprocess.STDOUT,
                             start_new_session=True)
        t0, done, sent = time.time(), None, None
        while time.time() - t0 < timeout:
            text = console.read_text(encoding="utf-8", errors="replace")
            if done is None and re.search(r"Done \(\d", text):
                done = time.time()
            if done is not None and sent is None and time.time() - done >= settle:
                try:
                    p.stdin.write(b"stop\n"); p.stdin.flush()
                except OSError:
                    pass
                sent = time.time()
            if p.poll() is not None:
                break
            if sent and time.time() - sent > 90:      # asked to stop, never did
                break
            time.sleep(poll)
        timed_out = p.poll() is None
        if timed_out:
            try:
                os.killpg(p.pid, 9)
            except OSError:
                p.kill()
            p.wait()
    text = console.read_text(encoding="utf-8", errors="replace")
    latest = run / "logs" / "latest.log"
    full = text + ("\n" + latest.read_text(encoding="utf-8", errors="replace") if latest.is_file() else "")
    problems = load_problems(full)
    if done is None:
        why = ("timed out before 'Done (' appeared" if timed_out else
               f"the server exited ({p.returncode}) before 'Done ('") + ": " + crash_cause(full)
        return False, why, full
    crash = crash_seen(run, full)
    if crash:
        return False, "Done, then the server crashed: " + crash, full
    if problems:
        return False, "server started but reported mod-loading problems: " + problems[0], full
    if sent is None:
        return False, ("Done, but the server was still settling when the timeout hit" if timed_out else
                       f"Done, then the server exited ({p.returncode}) before it was told to stop: {crash_cause(full)}"), full
    if timed_out:
        return False, "Done, but the server did not exit within 90 s of `stop`", full
    return True, f"started and stopped cleanly in {time.time() - t0:.0f} s", full


def _refuse_layer(repo, tool):
    """tools/fork-kind.py's refusal of a checkout with the removed .multiversion/ layer at HEAD: None, or the message
    (print it and exit 2)."""
    import importlib.util as _ilu
    s = _ilu.spec_from_file_location("fork_kind", pathlib.Path(__file__).resolve().parent / "fork-kind.py")
    m = _ilu.module_from_spec(s); s.loader.exec_module(m)
    return m.refuse_layer(repo, tool)


def main():
    if "--self-check" in sys.argv:
        return self_check()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default="."); ap.add_argument("--jar", required=True)
    ap.add_argument("--mc"); ap.add_argument("--neo")
    ap.add_argument("--deps", nargs="*", default=[]); ap.add_argument("--expect-log", nargs="*", default=[])
    ap.add_argument("--expect-file"); ap.add_argument("--work-dir"); ap.add_argument("--cache-dir")
    ap.add_argument("--java-home"); ap.add_argument("--timeout", type=int, default=420)
    ap.add_argument("--variant", help="the shipped jar's variant (cf for *_cf.jar): picks .port/<mc>/shipped-expect-<variant>.txt")
    ap.add_argument("--settle", type=float, default=10.0, help="seconds the server runs after 'Done (' before `stop`")
    a = ap.parse_args()
    repo = pathlib.Path(a.repo).resolve()
    why = _refuse_layer(a.repo, "shipped-gate")
    if why:
        print(why)
        return 2
    jar = pathlib.Path(a.jar).resolve()
    if not jar.is_file() or not zipfile.is_zipfile(jar):
        say(f"--jar {a.jar}: not a jar"); return 2
    for d in a.deps:
        if not pathlib.Path(d).is_file():
            say(f"--deps {d}: no such file"); return 2
    mc = a.mc
    neo = a.neo or gradle_prop(repo, "neo_version")
    if not neo:
        say("no NeoForge version: pass --neo"); return 2
    exp_lines = list(a.expect_log)
    ef = pathlib.Path(a.expect_file) if a.expect_file else None
    if ef is None:     # the fork's own list: the version branch's (per variant first)
        ef = expect_file(repo, a.mc, a.variant)
    if ef:
        if not ef.is_file():
            say(f"--expect-file {ef}: no such file"); return 2
        exp_lines += ef.read_text(encoding="utf-8").splitlines()
        say(f"expectations from {ef}")
    try:
        expects = parse_expect(exp_lines)
    except ValueError as e:
        say(str(e)); return 2
    if not expects:
        say("no expectations: the gate would only prove the jar boots (still checked, but weak)")
    major = jdk_for(repo, mc)
    java = find_java(major, a.java_home)
    if not java:
        say(no_jdk_message(major)); return 2
    cache = pathlib.Path(a.cache_dir or os.environ.get("SHIPPED_GATE_CACHE") or
                         pathlib.Path.home() / ".cache/shipped-gate").resolve()
    cache.mkdir(parents=True, exist_ok=True)
    work = pathlib.Path(a.work_dir or tempfile.mkdtemp(prefix="shipped-gate-")).resolve()
    work.mkdir(parents=True, exist_ok=True)
    say(f"jar {jar.name}; NeoForge {neo}; JDK {major}; deps {[pathlib.Path(d).name for d in a.deps] or 'none'}")
    srv = installer(neo, cache, java)
    run = prepare(work, srv, jar, [pathlib.Path(d).resolve() for d in a.deps], free_port())
    ok, why, full = boot(run, java, a.timeout, a.settle)
    say(f"{'PASS' if ok else 'FAIL'} boot -- {why}")
    allok = ok
    for f, rx, hit, reason in check_expect(run / "logs", expects):
        good = hit is not None
        allok &= good
        say(f"{'PASS' if good else 'FAIL'} logs/{f} ~ /{rx}/ -- {hit[:200] if good else reason}")
    if not allok:
        say(f"server directory kept for inspection: {run}")
        if not ok:
            (work / "server-failure.log").write_text(full[-20000:], encoding="utf-8")
    say("RESULT: " + ("PASS" if allok else "FAIL"))
    return 0 if allok else 1


def self_check():
    ok = True
    ok &= java_major("1.21.10") == 21 and java_major("26.2") == 25
    ok &= [(f, r.pattern) for f, r in parse_expect(["# c", "", "A.log:foo.*bar"])] == [("A.log", "foo.*bar")]
    for bad in ("nocolon", "x/y.log:z", ":z", "f.log:"):
        try:
            parse_expect([bad]); ok = False
        except ValueError:
            pass
    with tempfile.TemporaryDirectory() as d:
        (pathlib.Path(d) / "M.log").write_text("a\nInstalled protected containers=2 target=X\n", encoding="utf-8")
        rows = check_expect(d, parse_expect(["M.log:Installed protected containers=.* target=X",
                                              "M.log:never", "Gone.log:x"]))
        ok &= [bool(r[2]) for r in rows] == [True, False, False] and rows[2][3] == "log file absent"
    ok &= crash_cause("x\nCaused by: java.lang.Foo: a\nCaused by: java.lang.Bar: b\n") == "Caused by: java.lang.Bar: b"
    ok &= crash_cause("Failure message: Missing mandatory\nMod x").startswith("Failure message: Missing")
    ok &= load_problems("[x/INFO] ok\n[y/ERROR] Mod loading has failed\n[z/DEBUG] ModLoadingException") != []
    ok &= len(load_problems("[z/DEBUG] ModLoadingException\n[x/INFO] fine")) == 0
    ok &= find_java(99) is None
    # the not-found message names the places in the order they are tried: JAVA_HOME, then JAVA_HOME_<major>, then roots
    msg = no_jdk_message(25)
    ok &= re.findall(r"JAVA_HOME(?:_\d+)?", msg) == ["JAVA_HOME", "JAVA_HOME_25"] and msg.find("JAVA_HOME_25") < msg.find(JDK_ROOTS[0])
    with tempfile.TemporaryDirectory() as d:      # where a JDK 25 is looked for, not only /usr/lib/jvm (dry run, 2026-10-18)
        r = pathlib.Path(d)
        for n in ("opt/jdk-25.0.1+9", "opt/jdk-21.0.4", "opt/jdk-125", "gradle/jdks/eclipse_adoptium-25-amd64-linux.2",
                  "jvm/java-25-openjdk-amd64"):
            (r / n).mkdir(parents=True)
        got = java_candidates(25, None, {"JAVA_HOME_25_X64": "/hosted/25", "JAVA_HOME": "/default"},
                              (str(r / "jvm"), str(r / "opt"), str(r / "gradle/jdks")))
        ok &= got == ["/default", "/hosted/25", str(r / "jvm/java-25-openjdk-amd64"), str(r / "opt/jdk-25.0.1+9"),
                      str(r / "gradle/jdks/eclipse_adoptium-25-amd64-linux.2")]
        ok &= java_candidates(21, "/explicit", {}, (str(r / "opt"),)) == ["/explicit", str(r / "opt/jdk-21.0.4")]
        # a JDK home with only bin/java.exe (Windows) is found; the major is still checked by running it
        exe = r / "winjdk/bin"; exe.mkdir(parents=True); (exe / "java.exe").write_bytes(b"")
        seen_ = []
        real_run = subprocess.run
        try:
            subprocess.run = lambda cmd, **k: (seen_.append(cmd[0]), subprocess.CompletedProcess(cmd, 0, "", 'version "25.0.1"'))[1]
            got_exe = find_java(25, str(r / "winjdk"))
        finally:
            subprocess.run = real_run
        ok &= got_exe == str(exe / "java.exe") and seen_ == [str(exe / "java.exe")]
    with tempfile.TemporaryDirectory() as d:      # the JDK pick, with no --mc: the checkout's own Minecraft version
        r = pathlib.Path(d)
        (r / "gradle.properties").write_text("minecraft_version=1.21.1\nneo_version=21.1.1\n", encoding="utf-8")
        ok &= jdk_for(r, None) == 21 and gradle_prop(r, "neo_version") == "21.1.1"
        (r / "gradle.properties").write_text("minecraft_version=26.3\nneo_version=26.3.0.1\n", encoding="utf-8")
        ok &= jdk_for(r, None) == 25 and jdk_for(r, "1.21.4") == 21
        (r / ".port/26.3").mkdir(parents=True)
        (r / ".port/26.3/branch.properties").write_text("minecraft_version=26.3\njava_version=26\n", encoding="utf-8")
        ok &= jdk_for(r, None) == 26
        ok &= jdk_for(r / "none", None) == 21
    free = free_port()
    ok &= 1024 < free < 65536
    ok &= expect_check() and boot_check()
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def expect_check():
    """Which expectations file a jar gets: .port/<mc>/ per variant on every kind of fork (none: no file)."""
    ok = True
    ok &= [variant_of(g) for g in ("*_cf.jar", "*_mr.jar", "*-fabric.jar", "*.jar", None)] == ["cf", "mr", "fabric", None, None]
    with tempfile.TemporaryDirectory() as d:
        r = pathlib.Path(d)
        (r / "gradle.properties").write_text("minecraft_version=1.21.4\nneo_version=21.4.1\n", encoding="utf-8")
        ok &= expect_file(r) is None and expect_file(r, None, "cf") is None       # single-version, no file: as before
        (r / ".port/1.21.4").mkdir(parents=True)
        (r / ".port/1.21.4/shipped-expect-cf.txt").write_text("A.log:cf ran\n", encoding="utf-8")
        ok &= expect_file(r, None, "mr") is None and expect_file(r) is None       # only the cf variant has one
        ok &= expect_file(r, None, "cf") == r / ".port/1.21.4/shipped-expect-cf.txt"
        (r / ".port/1.21.4/shipped-expect.txt").write_text("A.log:ran\n", encoding="utf-8")
        ok &= expect_file(r, None, "cf") == r / ".port/1.21.4/shipped-expect-cf.txt"   # _cf: its own
        ok &= expect_file(r, None, "mr") == r / ".port/1.21.4/shipped-expect.txt"      # _mr: the unsuffixed one
        ok &= expect_file(r, "1.21.4") == r / ".port/1.21.4/shipped-expect.txt"
        ok &= expect_file(r, "1.21.5") is None                                       # another version's: not this one
        (r / ".multiversion").mkdir()                       # the removed layer's location is not read (#161 U1)
        (r / ".multiversion/shipped-expect.txt").write_text("A.log:x\n", encoding="utf-8")
        ok &= expect_file(r, "1.21.6") is None
        ok &= expect_file(r / "none") is None
        # a variant whose expected line is missing from its log fails
        lg = r / "logs"; lg.mkdir()
        (lg / "A.log").write_text("ran\n", encoding="utf-8")
        rows = check_expect(lg, parse_expect(expect_file(r, None, "cf").read_text(encoding="utf-8").splitlines()))
        ok &= [x[2] for x in rows] == [None]
        rows = check_expect(lg, parse_expect(expect_file(r, None, "mr").read_text(encoding="utf-8").splitlines()))
        ok &= [x[2] for x in rows] == ["ran"]
    if not ok:
        print("self-check: expectations-file lookup FAIL")
    return ok


FAKE_SERVER = """import pathlib, sys, time
mode = pathlib.Path("mode.txt").read_text(encoding="utf-8")
print("[Server thread/INFO]: Done (0.1s)! For help, type \\"help\\"", flush=True)
time.sleep(0.2)
if mode == "crash":
    pathlib.Path("crash-reports").mkdir()
    pathlib.Path("crash-reports/crash-server.txt").write_text("---- Minecraft Crash Report ----", encoding="utf-8")
    print("[Server thread/ERROR]: Encountered an unexpected exception", flush=True)
    sys.exit(255)
if mode == "exit":
    sys.exit(0)
sys.exit(0 if sys.stdin.readline().strip() == "stop" else 3)
"""


def boot_check():
    """boot() on a stand-in server (Python running '@user_jvm_args.txt' as its script, no Minecraft, about a second
    each): a clean one passes; one that crashes on its first tick after 'Done (', or exits unasked, fails."""
    ok = True
    with tempfile.TemporaryDirectory() as d:
        for mode, want in (("clean", True), ("crash", False), ("exit", False)):
            run = pathlib.Path(d) / mode
            (run / "libraries/net/neoforged/neoforge/0").mkdir(parents=True)
            (run / "libraries/net/neoforged/neoforge/0/unix_args.txt").write_text("", encoding="utf-8")
            (run / "@user_jvm_args.txt").write_text(FAKE_SERVER, encoding="utf-8")
            (run / "mode.txt").write_text(mode, encoding="utf-8")
            got, why, _ = boot(run, sys.executable, 30, settle=0.5, poll=0.1)
            good = got == want and (mode != "crash" or "crashed: crash report crash-reports/crash-server.txt" in why) \
                and (mode != "exit" or "before it was told to stop" in why)
            if not good:
                print(f"self-check: boot {mode}: {got} {why}")
            ok &= good
    return ok


if __name__ == "__main__":
    sys.exit(main())
