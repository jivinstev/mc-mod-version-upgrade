#!/usr/bin/env python3
"""Shipped-jar gate: boot the BUILT mod jar in a real NeoForge server the way a player installs it, and read the
mod's own logs.

    python3 tools/shipped-gate.py --repo DIR --jar BUILT.jar [--mc 1.21.8 --neo 21.8.54] [--java-home DIR]
                                  [--expect-log FILE:REGEX ...] [--expect-file FILE] [--deps JAR ...]
                                  [--work-dir DIR] [--cache-dir DIR] [--timeout SECONDS]

Every other gate (Gate A/B/C) runs the mod from the DEV classpath. FML discovers some things only on the launch
class loader there -- a coremod's ClassProcessorProvider, a service file -- and discovers them differently from a
jar in mods/. So a subsystem can run in every dev gate and be inert in the file a player downloads, and no dev gate
can see it. This one installs a real NeoForge server (installer from maven.neoforged.net, cached per version),
puts ONLY the built jar (+ --deps) in mods/, starts it, waits for "Done (", sends `stop`, and then checks each
--expect-log FILE:REGEX against <server>/logs/FILE (the mod's own log, e.g. EcaBootstrap.log). A missing
expectation is a FAIL: that is the point.

--expect-file reads one expectation per line (FILE:REGEX, '#' comments); a fork lists its own in
.multiversion/shipped-expect.txt (or shipped-expect-<target>.txt). --mc/--neo default from the fork layer's
target when --repo has .multiversion and --mc names a target (tools: layer.py props).
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


def find_java(major, explicit=None):
    cands = [explicit] if explicit else []
    for d in sorted(pathlib.Path("/usr/lib/jvm").glob("*")) if pathlib.Path("/usr/lib/jvm").is_dir() else []:
        if re.search(rf"(?:^|[-.]){major}(?:[.-]|$)", d.name):
            cands.append(str(d))
    if os.environ.get("JAVA_HOME"):
        cands.append(os.environ["JAVA_HOME"])
    for c in cands:
        j = pathlib.Path(c) / "bin" / "java"
        if j.is_file():
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


def boot(run, java, timeout):
    """-> (ok, reason, console text). Start, wait for 'Done (', send stop, wait for exit."""
    args = sorted(run.glob("libraries/net/neoforged/neoforge/*/unix_args.txt"))[0]
    cmd = [java, "@user_jvm_args.txt", f"@{args.relative_to(run)}", "nogui"]
    console = run / "console.log"
    with open(console, "wb") as out:
        p = subprocess.Popen(cmd, cwd=str(run), stdin=subprocess.PIPE, stdout=out, stderr=subprocess.STDOUT,
                             start_new_session=True)
        t0, done, sent = time.time(), False, None
        while time.time() - t0 < timeout:
            text = console.read_text(encoding="utf-8", errors="replace")
            if not done and re.search(r"Done \(\d", text):
                done = True
                try:
                    p.stdin.write(b"stop\n"); p.stdin.flush()
                except OSError:
                    pass
                sent = time.time()
            if p.poll() is not None:
                break
            if sent and time.time() - sent > 90:      # asked to stop, never did
                break
            time.sleep(1)
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
    if not done:
        why = ("timed out before 'Done (' appeared" if timed_out else
               f"the server exited ({p.returncode}) before 'Done ('") + ": " + crash_cause(full)
        return False, why, full
    if problems:
        return False, "server started but reported mod-loading problems: " + problems[0], full
    if timed_out and sent:
        return False, "Done, but the server did not exit within 90 s of `stop`", full
    return True, f"started and stopped cleanly in {time.time() - t0:.0f} s", full


def layer_props(repo, mc):
    layer = repo / ".multiversion/tools/layer.py"
    if not layer.is_file() or not (repo / ".multiversion/targets" / mc).is_dir():
        return {}
    out = subprocess.run([sys.executable, str(layer), "props", "--target", mc], capture_output=True, text=True, encoding="utf-8").stdout
    return dict(re.findall(r"-P(\w+)=(\S+)", out))


def main():
    if "--self-check" in sys.argv:
        return self_check()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default="."); ap.add_argument("--jar", required=True)
    ap.add_argument("--mc"); ap.add_argument("--neo")
    ap.add_argument("--deps", nargs="*", default=[]); ap.add_argument("--expect-log", nargs="*", default=[])
    ap.add_argument("--expect-file"); ap.add_argument("--work-dir"); ap.add_argument("--cache-dir")
    ap.add_argument("--java-home"); ap.add_argument("--timeout", type=int, default=420)
    a = ap.parse_args()
    repo = pathlib.Path(a.repo).resolve()
    jar = pathlib.Path(a.jar).resolve()
    if not jar.is_file() or not zipfile.is_zipfile(jar):
        say(f"--jar {a.jar}: not a jar"); return 2
    for d in a.deps:
        if not pathlib.Path(d).is_file():
            say(f"--deps {d}: no such file"); return 2
    props = layer_props(repo, a.mc) if a.mc else {}
    mc = a.mc and props.get("minecraft_version", a.mc)
    neo = a.neo or props.get("neo_version")
    if not neo and (repo / "gradle.properties").is_file():
        m = re.search(r"^neo_version\s*=\s*(\S+)", (repo / "gradle.properties").read_text(encoding="utf-8"), re.M)
        neo = m and m.group(1)
    if not neo:
        say("no NeoForge version: pass --neo (or --mc naming a .multiversion target)"); return 2
    exp_lines = list(a.expect_log)
    ef = pathlib.Path(a.expect_file) if a.expect_file else None
    if ef is None:     # the fork's own list: per target first, then the shared one
        for n in ((f"shipped-expect-{a.mc}.txt",) if a.mc else ()) + ("shipped-expect.txt",):
            if (repo / ".multiversion" / n).is_file():
                ef = repo / ".multiversion" / n; break
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
    major = java_major(mc or "1.21")
    java = find_java(major, a.java_home)
    if not java:
        say(f"no JDK {major} found (looked in /usr/lib/jvm, JAVA_HOME); pass --java-home"); return 2
    cache = pathlib.Path(a.cache_dir or os.environ.get("SHIPPED_GATE_CACHE") or
                         pathlib.Path.home() / ".cache/shipped-gate").resolve()
    cache.mkdir(parents=True, exist_ok=True)
    work = pathlib.Path(a.work_dir or tempfile.mkdtemp(prefix="shipped-gate-")).resolve()
    work.mkdir(parents=True, exist_ok=True)
    say(f"jar {jar.name}; NeoForge {neo}; JDK {major}; deps {[pathlib.Path(d).name for d in a.deps] or 'none'}")
    srv = installer(neo, cache, java)
    run = prepare(work, srv, jar, [pathlib.Path(d).resolve() for d in a.deps], free_port())
    ok, why, full = boot(run, java, a.timeout)
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
    free = free_port()
    ok &= 1024 < free < 65536
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
