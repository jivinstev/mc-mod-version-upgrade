#!/usr/bin/env python3
"""The Gate C carrier: templates/smoke-harness's client driver, built as one jar per Minecraft version that loads in a
player's real NeoForge install, and published by this repository's CI (.github/workflows/gatec-carrier.yml).

    python3 tools/gatec-carrier.py versions [--json]          the carrier's versions (templates/fork-layer/versions.json)
    python3 tools/gatec-carrier.py prepare --mc 26.3 --out DIR  write the carrier's Gradle project for one version
    python3 tools/gatec-carrier.py digest [--mc MC ...]        each version's sources digest (what a release is cut on)
    python3 tools/gatec-carrier.py check-jar --mc 26.3 --jar FILE
                                  the built jar would load in a real install of that version: metadata, entry class,
                                  class-file version, the contract's property names and marker
    python3 tools/gatec-carrier.py client-smoke --project DIR [--mode gauntlet] [--timeout 1200]
                                  run the carrier in that project's dev client (Xvfb + Mesa on headless Linux) and
                                  require SMOKEHARNESS_BOOT_TEST: PASS on stdout AND in run/client/logs/latest.log
    python3 tools/gatec-carrier.py client-smoke --project DIR --mode playtest --playtest FILE [--expect pass|fail]
                                  [--expect-steps PASS,FAIL,SKIPPED]
                                  run a playtest script (tools/playtest-lint.py's grammar) in the dev client; pass: the
                                  verdict PASS on stdout, in latest.log and in <project>/playtest-out/<script>/
                                  playtest-result.json; fail: FAIL in all three, from the script's steps (each step's
                                  verdict as --expect-steps lists), not from the harness
    python3 tools/gatec-carrier.py plan [--published MANIFEST.txt] --newest N
                                  publish=true|false and version=N+1 (GITHUB_OUTPUT lines): true when any version's
                                  sources digest, NeoForge or Java differs from the newest published release's
    python3 tools/gatec-carrier.py release --dist DIR --tag gatec-carrier-vN --commit SHA
                                  write DIR/MANIFEST.txt and DIR/SHA256SUMS over DIR/gatec-carrier-<mc>.jar
    python3 tools/gatec-carrier.py --self-check

The versions are the targeted (stable) lines of templates/fork-layer/versions.json, with their NeoForge, Java and NeoForm
runtime, so a new line there is a new carrier jar; JEI's API version is templates/gatec-carrier/jei.tsv (a version with
no row gets a stand-in probe: JEI has no build for it). Each version's sources are the smoke harness's SmokeHarness,
ClientBootSmokeTest and JeiRecipeProbe renamed by the same gate harness tables a fork's version branch applies to its
client harness (tools/port_gates.py harness_table: templates/upstream-harness/versions/<mc>.renames.tsv, inherited down
the versions), then by the carrier's own rows (templates/gatec-carrier/versions/<mc>.renames.tsv, every one up to the
version), through templates/multi-version/tools/prepare-sources.py. The build is ModDevGradle on the Gradle wrapper
version tools/targets.py names; its neoforge.mods.toml ranges are the ones tools/fork-branches.py writes for a version
branch. SmokeGameTest (the smoke harness's server GameTest for install-mod) is not part of the carrier.

The contract the runner relies on, identical on every version: -Dsmokeharness.boottest=true, -Dsmokeharness.testmode=
launch|spawn|battle|gauntlet|census|playtest, -Dsmokeharness.namespaces=<mod ids>, optional -Dsmokeharness.battlecount /
battleticks / jeirecipes / jeiverify, and for playtest -Dsmokeharness.playtest=<script> / playtest.out=<dir> /
playtest.world=<save name> (PlaytestRunner: PLAYTEST: STEP/PASS/FAIL lines and <out>/playtest-result.json); "SMOKEHARNESS_BOOT_TEST: PASS ..." or "... FAIL ..." printed to System.out (which
Minecraft's bootstrap routes into the log, so it is in latest.log too), then the client stops. Mod id smokeharness.

Release: gatec-carrier-vN (immutable, never "latest"), assets gatec-carrier-<mc>.jar per version, SHA256SUMS and
MANIFEST.txt (`jar <mc> <neoforge> <java> <file> <sha256> <sources digest>` lines). Standard library only.
"""
import argparse, hashlib, importlib.util, json, os, pathlib, re, shutil, subprocess, sys, tempfile, time, zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
TPL = ROOT / "templates/gatec-carrier"
HARNESS = ROOT / "templates/smoke-harness"
PKG = "com/forgeupgrade/smokeharness"
SOURCES = ("SmokeHarness.java", "ClientBootSmokeTest.java", "JeiRecipeProbe.java", "PlaytestRunner.java")
PREPARE = ROOT / "templates/multi-version/tools/prepare-sources.py"
MODID = "smokeharness"
MARKER = "SMOKEHARNESS_BOOT_TEST"
PROPS = ("boottest", "testmode", "namespaces", "battlecount", "battleticks", "jeirecipes", "jeiverify",
         "playtest", "playtest.out", "playtest.world")
MODES = ("launch", "spawn", "battle", "gauntlet", "census", "playtest")
MOD_VERSION = "1.0.0"
TAG = re.compile(r"^gatec-carrier-v([0-9]+)$")


def _load(name, file):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / file)
    mod = importlib.util.module_from_spec(spec); sys.modules[name] = mod; spec.loader.exec_module(mod)
    return mod


def mc_key(mc):
    return tuple(int(x) for x in re.findall(r"\d+", mc))


def jei_table(path=None):
    out = {}
    for line in (path or TPL / "jei.tsv").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            mc, ver = line.split("\t")
            out[mc.strip()] = ver.strip()
    return out


def build_tools():
    """(ModDevGradle version, Gradle wrapper version) from tools/targets.py: the newest each target names."""
    t = _load("targets", "targets.py").TARGETS.values()
    mdg = max((x.mdg for x in t if x.mdg), key=mc_key)
    gradle = max((x.gradle for x in t if x.gradle), key=mc_key)
    return mdg, gradle


def versions(table=None, jei=None):
    """[{mc, neo, java, nfrt, jei, mc_range, neo_range}] for every targeted line of templates/fork-layer/versions.json."""
    fb = _load("fork_branches_gc", "fork-branches.py")
    tbl = table if table is not None else fb.versions_table()
    jei = jei_table() if jei is None else jei
    out = []
    for ln in tbl["lines"]:
        if not ln.get("stable") or not ln.get("neo"):
            continue
        mr, nr = fb.toml_ranges(ln["mc"], ln["neo"])
        out.append({"mc": ln["mc"], "neo": ln["neo"], "java": int(ln.get("java", 21)), "nfrt": ln.get("nfrt", ""),
                    "jei": jei.get(ln["mc"], ""), "mc_range": mr, "neo_range": nr})
    return sorted(out, key=lambda v: mc_key(v["mc"]))


def version(mc):
    v = next((x for x in versions() if x["mc"] == mc), None)
    if v is None:
        raise SystemExit(f"gatec-carrier: {mc} is not a targeted line of templates/fork-layer/versions.json "
                         f"(carrier versions: {', '.join(x['mc'] for x in versions())})")
    return v


def rename_table(mc, base, carrier_dir=None):
    """The rename rows for version mc: the gate harness table (inherited down the versions), then every carrier table
    up to mc, oldest first. The base version (the smoke harness is written for it) has none."""
    if mc == base:
        return ""
    pg = _load("port_gates", "port_gates.py")
    rows = [pg.harness_table(mc)]
    cdir = carrier_dir or TPL / "versions"
    for p in sorted(cdir.glob("*.renames.tsv"), key=lambda q: mc_key(q.name[:-len(".renames.tsv")])):
        v = p.name[:-len(".renames.tsv")]
        if mc_key(base) < mc_key(v) <= mc_key(mc):
            rows.append(p.read_text(encoding="utf-8").rstrip("\n"))
    return "\n".join(rows) + "\n"


def rename_sources(src_dir, table_text, out_dir):
    """Run prepare-sources.py over src_dir with the table; -> its report (dead rules and counts)."""
    with tempfile.TemporaryDirectory() as d:
        table = pathlib.Path(d) / "carrier.renames.tsv"
        table.write_text(table_text, encoding="utf-8")
        r = subprocess.run([sys.executable, str(PREPARE), "--src", str(src_dir), "--renames", str(table), "--out", str(out_dir)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise SystemExit(f"gatec-carrier: renaming the carrier sources failed: {(r.stderr or r.stdout)[-600:]}")
    return r.stdout


def prepare(mc, out, quiet=False):
    """Write the carrier's Gradle project for version mc into out (replaced). -> the version dict."""
    v = version(mc)
    base = _load("fork_branches_gc", "fork-branches.py").versions_table()["base"]
    mdg, gradle = build_tools()
    out = pathlib.Path(out)
    if out.exists():
        shutil.rmtree(out)
    java_out = out / "src/main/java"
    with tempfile.TemporaryDirectory() as d:
        src = pathlib.Path(d) / "java"
        (src / PKG).mkdir(parents=True)
        for name in SOURCES:
            if name == "JeiRecipeProbe.java" and not v["jei"]:
                shutil.copyfile(TPL / "JeiRecipeProbe.nojei.java", src / PKG / name)
            else:
                shutil.copyfile(HARNESS / "src/main/java" / PKG / name, src / PKG / name)
        table = rename_table(mc, base)
        if table:
            report = rename_sources(src, table, java_out)
            if not quiet:
                print(report.rstrip())
        else:
            shutil.copytree(src, java_out)
    res = out / "src/main/resources/META-INF"
    res.mkdir(parents=True)
    shutil.copyfile(TPL / "neoforge.mods.toml", res / "neoforge.mods.toml")
    build = (TPL / "build.gradle").read_text(encoding="utf-8")
    build = build.replace("@MDG@", mdg).replace("@MINECRAFT@", mc).replace("@NEOFORGE@", v["neo"])
    (out / "build.gradle").write_text(build, encoding="utf-8")
    (out / "settings.gradle").write_text(
        "// generated by tools/gatec-carrier.py\npluginManagement {\n    repositories {\n        gradlePluginPortal()\n"
        "        maven { url = 'https://maven.neoforged.net/releases' }\n    }\n}\nrootProject.name = 'gatec-carrier'\n",
        encoding="utf-8")
    props = [("org.gradle.jvmargs", "-Xmx3G"), ("org.gradle.daemon", "false"), ("minecraft_version", mc),
             ("neo_version", v["neo"]), ("java_version", str(v["java"])), ("minecraft_range", v["mc_range"]),
             ("neoforge_range", v["neo_range"]), ("mod_version", MOD_VERSION)]
    props += [("nfrt_version", v["nfrt"])] if v["nfrt"] else []
    props += [("jei_version", v["jei"])] if v["jei"] else []
    (out / "gradle.properties").write_text("# generated by tools/gatec-carrier.py\n" + "".join(f"{k}={x}\n" for k, x in props),
                                           encoding="utf-8")
    (out / "gradle/wrapper").mkdir(parents=True)
    shutil.copyfile(HARNESS / "gradle/wrapper/gradle-wrapper.jar", out / "gradle/wrapper/gradle-wrapper.jar")
    wp = (HARNESS / "gradle/wrapper/gradle-wrapper.properties").read_text(encoding="utf-8")
    wp = re.sub(r"gradle-[0-9.]+-bin\.zip", f"gradle-{gradle}-bin.zip", wp)
    (out / "gradle/wrapper/gradle-wrapper.properties").write_text(wp, encoding="utf-8")
    for w in ("gradlew", "gradlew.bat"):
        shutil.copyfile(HARNESS / w, out / w)
    os.chmod(out / "gradlew", 0o755)
    return v


def tree_digest(root):
    """sha256 over every file of a generated project (path and bytes, sorted), build outputs and run dirs left out."""
    root = pathlib.Path(root)
    h = hashlib.sha256()
    for p in sorted(x for x in root.rglob("*") if x.is_file()):
        rel = p.relative_to(root).as_posix()
        if rel.split("/")[0] in ("build", "run", ".gradle"):
            continue
        h.update(rel.encode("utf-8") + b"\0" + hashlib.sha256(p.read_bytes()).hexdigest().encode("ascii") + b"\n")
    return h.hexdigest()


def digests(mcs=None):
    """{mc: sources digest} for the given versions (all by default)."""
    out = {}
    for v in versions():
        if mcs and v["mc"] not in mcs:
            continue
        with tempfile.TemporaryDirectory() as d:
            prepare(v["mc"], pathlib.Path(d) / "p", quiet=True)
            out[v["mc"]] = tree_digest(pathlib.Path(d) / "p")
    return out


# ------------------------------------------------------------------------------------------------ the jar check
def _vkey(s):
    return [int(x) for x in re.findall(r"\d+", s)]


def _cmp(a, b):
    ka, kb = _vkey(a), _vkey(b)
    n = max(len(ka), len(kb))
    ka, kb = ka + [0] * (n - len(ka)), kb + [0] * (n - len(kb))
    return (ka > kb) - (ka < kb)


def in_range(ver, rng):
    """Does a version satisfy a Maven range such as [1.21.1,1.21.2), [21.1,) or [26.2,26.2.1)? (one interval)"""
    m = re.fullmatch(r"\s*([\[(])\s*([^,\s]*)\s*,\s*([^,\s\])]*)\s*([\])])\s*", rng)
    if not m:
        m1 = re.fullmatch(r"\s*\[([^,\]]+)\]\s*", rng)
        return bool(m1) and _cmp(ver, m1.group(1)) == 0
    lo_inc, lo, hi, hi_inc = m.group(1) == "[", m.group(2), m.group(3), m.group(4) == "]"
    if lo and (_cmp(ver, lo) < 0 or (_cmp(ver, lo) == 0 and not lo_inc)):
        return False
    if hi and (_cmp(ver, hi) > 0 or (_cmp(ver, hi) == 0 and not hi_inc)):
        return False
    return True


def _toml(text):
    try:
        import tomllib
        return tomllib.loads(text)
    except ImportError:
        return _load("minitoml", "minitoml.py").loads(text)


def check_jar(jar, mc, neo, java):
    """-> [problem, ...] (empty: the jar would load as a mod in a real install of mc on NeoForge neo)."""
    probs = []
    try:
        z = zipfile.ZipFile(jar)
    except (OSError, zipfile.BadZipFile) as e:
        return [f"not a jar: {e}"]
    with z:
        names = set(z.namelist())
        if "META-INF/neoforge.mods.toml" not in names:
            return ["no META-INF/neoforge.mods.toml: NeoForge would not see a mod in it"]
        try:
            meta = _toml(z.read("META-INF/neoforge.mods.toml").decode("utf-8"))
        except ValueError as e:
            return [f"neoforge.mods.toml does not parse: {e}"]
        if "${" in z.read("META-INF/neoforge.mods.toml").decode("utf-8"):
            probs.append("neoforge.mods.toml still has an unexpanded ${...} placeholder")
        mods = meta.get("mods") or []
        if [m.get("modId") for m in mods] != [MODID]:
            probs.append(f"mod ids {[m.get('modId') for m in mods]}, expected exactly [{MODID!r}]")
        if meta.get("modLoader") != "javafml":
            probs.append(f"modLoader {meta.get('modLoader')!r}, expected 'javafml'")
        deps = {d.get("modId"): d for d in (meta.get("dependencies") or {}).get(MODID, [])}
        for dep, ver in (("minecraft", mc), ("neoforge", neo)):
            d = deps.get(dep)
            if d is None:
                probs.append(f"no {dep} dependency")
            elif not in_range(ver, d.get("versionRange", "")):
                probs.append(f"{dep} versionRange {d.get('versionRange')!r} does not accept {ver}")
            elif d.get("type", "required") != "required":
                probs.append(f"{dep} dependency type {d.get('type')!r}, expected 'required'")
        for cls in ("SmokeHarness", "ClientBootSmokeTest", "JeiRecipeProbe", "PlaytestRunner"):
            if f"{PKG}/{cls}.class" not in names:
                probs.append(f"{PKG}/{cls}.class missing")
        top = 44 + int(java)
        for n in sorted(x for x in names if x.endswith(".class")):
            b = z.read(n)
            if b[:4] != b"\xca\xfe\xba\xbe":
                probs.append(f"{n}: not a class file"); continue
            major = int.from_bytes(b[6:8], "big")
            if major > top:
                probs.append(f"{n}: class file version {major} needs Java {major - 44}; Minecraft {mc} runs on Java {java}")
            if not n.startswith(PKG + "/"):
                probs.append(f"{n}: a class outside {PKG}/ is bundled (the carrier bundles nothing)")
        main = z.read(f"{PKG}/ClientBootSmokeTest.class") if f"{PKG}/ClientBootSmokeTest.class" in names else b""
        entry = z.read(f"{PKG}/SmokeHarness.class") if f"{PKG}/SmokeHarness.class" in names else b""
        if main:
            for s in [MARKER] + [f"{MODID}.{p}" for p in PROPS]:
                if s.encode("utf-8") not in main:
                    probs.append(f"ClientBootSmokeTest does not carry {s!r}: the contract changed")
        if entry and b"Lnet/neoforged/fml/common/Mod;" not in entry:
            probs.append("SmokeHarness has no @Mod annotation: FML would construct no mod")
    return probs


# --------------------------------------------------------------------------------------------- the client smoke
def playtest_out(project, script):
    """Where a client-smoke playtest run writes its results: <project>/playtest-out/<script name without suffix>."""
    return pathlib.Path(project).resolve() / "playtest-out" / pathlib.Path(script).stem


def client_smoke(project, mode="gauntlet", timeout=1200, extra=(), runner=None, playtest=None, expect="pass",
                 expect_steps=None):
    """Run the carrier in the project's dev client; -> (ok, why). PASS must be on stdout and in latest.log (a playtest:
    also in playtest-result.json; with expect="fail", FAIL from the script's own steps in all three)."""
    project = pathlib.Path(project).resolve()
    bash = _load("gitbash", "gitbash.py").BASH
    cmd = [bash, "gradlew", "--no-daemon", "--console=plain", "runClient", f"-P{MODID}.boottest=true",
           f"-P{MODID}.testmode={mode}"] + list(extra)
    res = None
    if mode == "playtest":
        if not playtest:
            return False, "--mode playtest needs --playtest FILE"
        res = playtest_out(project, playtest)
        if res.exists():
            shutil.rmtree(res)
        cmd += [f"-P{MODID}.playtest={pathlib.Path(playtest).resolve().as_posix()}", f"-P{MODID}.playtest.out={res.as_posix()}"]
    env = dict(os.environ)
    if sys.platform.startswith("linux") and not env.get("DISPLAY"):
        cmd = ["xvfb-run", "-a", "-s", "-screen 0 1280x720x24"] + cmd
        env["LIBGL_ALWAYS_SOFTWARE"] = "1"
        env.setdefault("SDL_VIDEO_FORCE_EGL", "1")      # 26.3+ opens its window with SDL (see tools/gate-loop.py)
        if mode == "playtest":
            # A runner has no sound card: OpenAL's null device lets the sound engine start, so sounds and subtitles play.
            env.setdefault("ALSOFT_DRIVERS", "null")
    log = project / "client-smoke.log"
    latest = project / "run/client/logs/latest.log"
    if latest.is_file():
        latest.unlink()
    (runner or _run_logged)(cmd, project, env, log, timeout)
    out = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
    game = latest.read_text(encoding="utf-8", errors="replace") if latest.is_file() else ""
    if mode != "playtest":
        return judge(out, game)
    for src, name in ((log, "client-smoke"), (latest, "latest")):
        if src.is_file():
            res.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, res / f"{name}.log")
    rj = res / "playtest-result.json"
    return judge_playtest(out, game, rj.read_text(encoding="utf-8") if rj.is_file() else "", expect, expect_steps)


def judge_playtest(out, game, result_text, expect="pass", expect_steps=None):
    """-> (ok, why) for a playtest run: the marker on stdout and in latest.log, the PLAYTEST verdict line, and the JSON."""
    rx = re.compile(MARKER + r": (PASS|FAIL)[^\n]*")
    verdicts = [m.group(0) for m in rx.finditer(out)]
    in_log = [m.group(0) for m in rx.finditer(game)]
    pt = re.findall(r"PLAYTEST: (PASS|FAIL) -- ", out)
    try:
        res = json.loads(result_text) if result_text.strip() else None
    except ValueError as e:
        return False, f"playtest-result.json does not parse: {e}"
    if not verdicts:
        return False, "no " + MARKER + " verdict on stdout (the client never reached it: see client-smoke.log)"
    if not in_log:
        return False, f"{verdicts[-1]!r} on stdout but not in run/client/logs/latest.log"
    if not pt:
        return False, "no 'PLAYTEST: PASS|FAIL -- k/n steps' line on stdout"
    if res is None:
        return False, "no playtest-result.json was written"
    steps = [s.get("verdict") for s in res.get("steps", [])]
    want = expect.upper()
    mark = lambda v: re.match(MARKER + r": (PASS|FAIL)", v).group(1)
    got = (mark(verdicts[-1]), mark(in_log[-1]), pt[-1], res.get("verdict"))
    if any(g != want for g in got):
        fails = [f"step {s.get('n')} {s.get('name')}: {s.get('detail')}" for s in res.get("steps", []) if s.get("verdict") == "FAIL"]
        return False, (f"expected {want}: marker {got[0]} (stdout) / {got[1]} (latest.log), PLAYTEST {got[2]}, JSON {got[3]}"
                       + (f"; {fails[0]}" if fails else f"; {res.get('detail', verdicts[-1])}"))
    if not res.get("finished"):
        return False, "playtest-result.json says the run did not finish"
    if want == "FAIL" and "FAIL" not in steps:
        return False, f"the run FAILed but not in a step of the script ({res.get('detail', verdicts[-1])}): the harness failed"
    if expect_steps and steps != list(expect_steps):
        return False, f"step verdicts {steps}, expected {list(expect_steps)}"
    return True, f"{verdicts[-1]} (steps {steps})"


def judge(out, game):
    """-> (ok, why) from the client's console output and its latest.log."""
    rx = re.compile(MARKER + r": (PASS|FAIL)[^\n]*")
    verdicts = [m.group(0) for m in rx.finditer(out)]
    in_log = [m.group(0) for m in rx.finditer(game)]
    if not verdicts:
        return False, "no " + MARKER + " verdict on stdout (the client never reached it: see client-smoke.log)"
    if any(": FAIL" in v for v in verdicts + in_log):
        return False, next(v for v in verdicts + in_log if ": FAIL" in v)
    if not in_log:
        return False, f"{verdicts[-1]!r} on stdout but not in run/client/logs/latest.log"
    return True, verdicts[-1]


def _run_logged(cmd, cwd, env, log, timeout):
    with open(log, "w", encoding="utf-8") as fh:
        p = subprocess.Popen(cmd, cwd=str(cwd), env=env, stdout=fh, stderr=subprocess.STDOUT,
                             start_new_session=hasattr(os, "killpg"))
        t0 = time.time()
        while p.poll() is None and time.time() - t0 < timeout:
            time.sleep(2)
        if p.poll() is None:
            try:
                os.killpg(p.pid, 9) if hasattr(os, "killpg") else p.kill()
            except OSError:
                p.kill()
            p.wait()
            fh.write(f"\n[gatec-carrier] killed after {timeout} s\n")
    return p.returncode


# ------------------------------------------------------------------------------------------- release and plan
def manifest_rows(text):
    """{mc: (neo, java, file, sha256, sources)} from a MANIFEST.txt."""
    out = {}
    for line in (text or "").splitlines():
        p = line.split()
        if len(p) == 7 and p[0] == "jar":
            out[p[1]] = tuple(p[2:])
    return out


def plan(published_text, newest, current):
    """-> (publish, version). current: {mc: (neo, java, sources)}. Publish when there is no release yet, or when the set
    of versions or any version's NeoForge, Java or sources digest differs from the newest release's MANIFEST."""
    pub = {mc: (r[0], r[1], r[4]) for mc, r in manifest_rows(published_text).items()}
    if newest > 0 and pub == current:
        return False, newest
    return True, newest + 1


def write_release(dist, tag, commit, vs=None, digest=None):
    """MANIFEST.txt and SHA256SUMS in dist for the jars gatec-carrier-<mc>.jar there (one per version, no other)."""
    dist = pathlib.Path(dist)
    vs = versions() if vs is None else vs
    if not TAG.match(tag):
        raise SystemExit(f"gatec-carrier: {tag!r} is not gatec-carrier-v<N>")
    want = {f"gatec-carrier-{v['mc']}.jar" for v in vs}
    have = {p.name for p in dist.glob("*.jar")}
    if want != have:
        raise SystemExit(f"gatec-carrier: {dist} must hold exactly one jar per version: missing {sorted(want - have)}, "
                         f"unexpected {sorted(have - want)}")
    dg = digest if digest is not None else digests()
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    lines = [f"# Gate C carrier {tag}: templates/smoke-harness's client driver for every Minecraft version",
             "# (tools/gatec-carrier.py; .github/workflows/gatec-carrier.yml). Space-separated; '#' lines are comments.",
             f"release {tag}", f"source_commit {commit}", f"mod_id {MODID}", f"marker {MARKER}",
             "properties " + " ".join(f"{MODID}.{p}" for p in PROPS),
             "# jar <minecraft> <neoforge> <java major> <file> <sha256> <sources digest>"]
    for v in vs:
        f = dist / f"gatec-carrier-{v['mc']}.jar"
        lines.append(f"jar {v['mc']} {v['neo']} {v['java']} {f.name} {sha(f)} {dg[v['mc']]}")
    (dist / "MANIFEST.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    files = sorted(want) + ["MANIFEST.txt"]
    (dist / "SHA256SUMS").write_text("".join(f"{sha(dist / n)}  {n}\n" for n in files), encoding="utf-8")
    return dist / "MANIFEST.txt"


# ---------------------------------------------------------------------------------------------------- self-check
def self_check():
    ok = True

    def expect(cond, what):
        nonlocal ok
        if not cond:
            print(f"self-check: FAIL {what}")
            ok = False

    vs = versions()
    mcs = [v["mc"] for v in vs]
    expect(mcs == ["1.21.1", "1.21.3", "1.21.4", "1.21.5", "1.21.8", "1.21.10", "1.21.11", "26.1.2", "26.2", "26.3"],
           f"the carrier versions are versions.json's stable lines: {mcs}")
    by = {v["mc"]: v for v in vs}
    expect(by["1.21.1"]["java"] == 21 and by["26.3"]["java"] == 25 and by["26.3"]["nfrt"] == "2.0.31"
           and by["1.21.1"]["nfrt"] == "", "Java 21 for 1.21.x, 25 for 26.x; the NeoForm runtime only where versions.json names one")
    expect(by["1.21.3"]["jei"] == "" and by["1.21.1"]["jei"] == "19.39.0.370", "JEI pins from jei.tsv; 1.21.3 has none")
    expect(by["1.21.1"]["mc_range"] == "[1.21.1,1.21.2)" and by["1.21.1"]["neo_range"] == "[21.1,21.2)"
           and by["26.1.2"]["mc_range"] == "[26.1.2,26.1.3)", "the mods.toml ranges of fork-branches toml_ranges")
    # one more stable line in versions.json is one more carrier; a beta line is none
    tbl = {"base": "1.21.1", "lines": [{"mc": "1.21.1", "neo": "21.1.1", "stable": True, "java": 21},
                                         {"mc": "1.21.2", "neo": None, "stable": False, "skip": "beta"},
                                         {"mc": "27.1", "neo": "27.1.0.5", "stable": True, "java": 27}]}
    got = versions(tbl, {})
    expect([(v["mc"], v["java"], v["jei"]) for v in got] == [("1.21.1", 21, ""), ("27.1", 27, "")],
           f"a new versions.json row is a new carrier version: {got}")
    mdg, gradle = build_tools()
    expect(mdg and re.fullmatch(r"\d+\.\d+\.\d+", gradle or ""), f"ModDevGradle and Gradle from targets.py: {mdg} {gradle}")

    # ranges
    for ver, rng, want in (("1.21.1", "[1.21.1,1.21.2)", True), ("1.21.2", "[1.21.1,1.21.2)", False),
                           ("21.1.228", "[21.1,21.2)", True), ("21.2.0", "[21.1,21.2)", False),
                           ("26.2.0.90", "[26.2,26.2.1)", True), ("26.2", "[26.2,26.2.1)", True),
                           ("26.2.1", "[26.2,26.2.1)", False), ("26.1.2", "[26.2,26.2.1)", False),
                           ("5.0", "[1,)", True), ("1.0", "(1,)", False), ("1.21.1", "[1.21.1]", True)):
        expect(in_range(ver, rng) == want, f"in_range({ver}, {rng}) == {want}")

    # the rename tables: base none; later versions the harness table then the carrier rows, with the inherited rows
    expect(rename_table("1.21.1", "1.21.1") == "", "the base version is not renamed")
    t3, t4, t263 = rename_table("1.21.3", "1.21.1"), rename_table("1.21.4", "1.21.1"), rename_table("26.3", "1.21.1")
    expect("MobSpawnType\tEntitySpawnReason" in t3 and "ITEM\\.getValue" not in t3 and "ITEM.getValue" in t3
           and "IRecipeType" not in t3, "1.21.3: the harness rows and the carrier's item lookup, no JEI 20 rows")
    expect("IRecipeType" in t4 and "ITEM.getValue" in t4, "1.21.4: the carrier rows of 1.21.3 and 1.21.4")
    expect(t263.index("MobSpawnType") < t263.index("setPermanentlyInvulnerable") < t263.index("ITEM.getValue"),
           "26.3: every harness table down the versions, then the carrier rows")

    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        # prepare: the base version is the smoke harness verbatim; 26.3 is renamed; 1.21.3 has the stand-in probe
        prepare("1.21.1", d / "a", quiet=True)
        same = all((d / "a/src/main/java" / PKG / n).read_bytes() == (HARNESS / "src/main/java" / PKG / n).read_bytes()
                   for n in SOURCES)
        expect(same, "1.21.1: the smoke harness sources unchanged")
        expect(not (d / "a/src/main/java" / PKG / "SmokeGameTest.java").exists(), "SmokeGameTest is not in the carrier")
        gp = (d / "a/gradle.properties").read_text(encoding="utf-8")
        expect("jei_version=19.39.0.370" in gp and "nfrt_version" not in gp and "java_version=21" in gp,
               "1.21.1 gradle.properties: JEI pin, no NeoForm runtime, Java 21")
        bg = (d / "a/build.gradle").read_text(encoding="utf-8")
        expect("@" + "MDG@" not in bg and f"version '{mdg}'" in bg, "build.gradle carries the ModDevGradle version")
        expect(f"gradle-{gradle}-bin.zip" in (d / "a/gradle/wrapper/gradle-wrapper.properties").read_text(encoding="utf-8"),
               "the wrapper is the Gradle version targets.py names")
        prepare("26.3", d / "b", quiet=True)
        cb = (d / "b/src/main/java" / PKG / "ClientBootSmokeTest.java").read_text(encoding="utf-8")
        expect("Identifier" in cb and "ResourceLocation" not in cb and "EntitySpawnReason.COMMAND" in cb
               and "mc.gui.screen()" in cb and "extractRenderState(GuiGraphicsExtractor g" in cb
               and "LevelSettings.DifficultySettings(Difficulty.NORMAL, false, false)" in cb
               and "sp.setPermanentlyInvulnerable(true)" in cb and "MobEffects.RESISTANCE" in cb
               and "g.pose().pushMatrix()" in cb and "armor.slot()" in cb,
               "26.3: the harness tables' rows took on the smoke harness text")
        expect(all(f'"{p}"' in cb or f'+ "{p}"' in cb for p in ("boottest", "testmode", "namespaces")) and
               'TAG = "' + MARKER + '"' in cb, "26.3: the property names and the marker are unchanged")
        pr = (d / "b/src/main/java" / PKG / "PlaytestRunner.java").read_text(encoding="utf-8")
        expect("mc.gui.screen()" in pr and "mc.screen " not in pr and "mc.gameRenderer.mainRenderTarget(), 1, msg" in pr,
               "26.3: the playtest runner took the harness rows (screen on Gui, the screenshot's downscale and render target)")
        expect('+ "playtest"' in cb, "26.3: the playtest property is read by ClientBootSmokeTest")
        jp = (d / "b/src/main/java" / PKG / "JeiRecipeProbe.java").read_text(encoding="utf-8")
        expect("mezz.jei.api.recipe.types.IRecipeType" in jp and "ITEM.getValue(Identifier.parse(id))" in jp,
               "26.3: the JEI probe's rows")
        gp = (d / "b/gradle.properties").read_text(encoding="utf-8")
        expect("nfrt_version=2.0.31" in gp and "java_version=25" in gp and "minecraft_range=[26.3,26.3.1)" in gp,
               "26.3 gradle.properties")
        prepare("1.21.3", d / "c", quiet=True)
        expect("JEI has no build" in (d / "c/src/main/java" / PKG / "JeiRecipeProbe.java").read_text(encoding="utf-8")
               and "jei_version" not in (d / "c/gradle.properties").read_text(encoding="utf-8"),
               "1.21.3: the stand-in probe, no JEI dependency")
        # the digest is stable, ignores build output, and moves with any source byte
        d1 = tree_digest(d / "b")
        (d / "b/build/libs").mkdir(parents=True); (d / "b/build/libs/x.jar").write_bytes(b"x")
        expect(tree_digest(d / "b") == d1, "the digest ignores build output")
        prepare("26.3", d / "b2", quiet=True)
        expect(tree_digest(d / "b2") == d1, "the digest is reproducible")
        f = d / "b2/src/main/java" / PKG / "SmokeHarness.java"
        f.write_text(f.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        expect(tree_digest(d / "b2") != d1, "the digest moves with a source change")

        # check-jar on hand-made jars: a good one, then one per defect
        def jar(path, toml, classes):
            with zipfile.ZipFile(path, "w") as z:
                if toml is not None:
                    z.writestr("META-INF/neoforge.mods.toml", toml)
                for n, b in classes.items():
                    z.writestr(n, b)
            return path
        toml = ((TPL / "neoforge.mods.toml").read_text(encoding="utf-8").replace("${mod_version}", "1.0.0")
                .replace("${neoforge_range}", "[26.3,26.3.1)").replace("${minecraft_range}", "[26.3,26.3.1)"))
        cls = lambda major, extra=b"": b"\xca\xfe\xba\xbe\x00\x00" + major.to_bytes(2, "big") + extra
        body = (MARKER + "".join(f" {MODID}.{p}" for p in PROPS)).encode("utf-8")
        good = {f"{PKG}/SmokeHarness.class": cls(69, b"Lnet/neoforged/fml/common/Mod;"),
                f"{PKG}/ClientBootSmokeTest.class": cls(69, body), f"{PKG}/JeiRecipeProbe.class": cls(69),
                f"{PKG}/PlaytestRunner.class": cls(69)}
        expect(check_jar(jar(d / "g.jar", toml, good), "26.3", "26.3.0.76", 25) == [], "a good jar passes")
        expect(any("does not accept 26.2" in p for p in check_jar(d / "g.jar", "26.2", "26.2.0.90", 25)),
               "a jar for another version is refused")
        expect(any("needs Java 25" in p for p in check_jar(d / "g.jar", "26.3", "26.3.0.76", 21)),
               "Java 25 classes for a Java 21 version are refused")
        bad = dict(good); bad.pop(f"{PKG}/SmokeHarness.class")
        expect(any("SmokeHarness.class missing" in p for p in check_jar(jar(d / "e.jar", toml, bad), "26.3", "26.3.0.76", 25)),
               "a jar without the entry class is refused")
        bad = dict(good); bad[f"{PKG}/ClientBootSmokeTest.class"] = cls(69, MARKER.encode("utf-8"))
        expect(any("contract changed" in p for p in check_jar(jar(d / "p.jar", toml, bad), "26.3", "26.3.0.76", 25)),
               "a jar whose driver lost a property name is refused")
        bad = dict(good); bad["mezz/jei/api/IModPlugin.class"] = cls(65)
        expect(any("bundles nothing" in p for p in check_jar(jar(d / "j.jar", toml, bad), "26.3", "26.3.0.76", 25)),
               "a jar that bundles JEI is refused")
        expect(check_jar(jar(d / "n.jar", None, good), "26.3", "26.3.0.76", 25)[0].startswith("no META-INF"),
               "a jar without metadata is refused")
        expect(any("unexpanded" in p for p in check_jar(jar(d / "u.jar", toml.replace("1.0.0", "${mod_version}"), good),
                                                       "26.3", "26.3.0.76", 25)), "an unexpanded placeholder is refused")
        expect(any("mod ids" in p for p in check_jar(jar(d / "m.jar", toml.replace('modId="smokeharness"', 'modId="other"'), good),
                                                    "26.3", "26.3.0.76", 25)), "another mod id is refused")

        # release: MANIFEST + SHA256SUMS over exactly one jar per version; plan reads the MANIFEST back
        two = [by["1.21.1"], by["26.3"]]
        dist = d / "dist"; dist.mkdir()
        for v in two:
            (dist / f"gatec-carrier-{v['mc']}.jar").write_bytes(v["mc"].encode("utf-8"))
        dg = {"1.21.1": "a" * 64, "26.3": "b" * 64}
        m = write_release(dist, "gatec-carrier-v3", "c0ffee", two, dg).read_text(encoding="utf-8")
        rows = manifest_rows(m)
        expect(sorted(rows) == ["1.21.1", "26.3"] and rows["26.3"][:3] == ("26.3.0.76", "25", "gatec-carrier-26.3.jar")
               and rows["26.3"][3] == hashlib.sha256(b"26.3").hexdigest() and rows["26.3"][4] == "b" * 64
               and "release gatec-carrier-v3" in m and "source_commit c0ffee" in m, "MANIFEST.txt rows")
        sums = (dist / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        expect([s.split("  ")[1] for s in sums] == ["gatec-carrier-1.21.1.jar", "gatec-carrier-26.3.jar", "MANIFEST.txt"],
               "SHA256SUMS covers each jar and the MANIFEST")
        cur = {"1.21.1": ("21.1.228", "21", "a" * 64), "26.3": ("26.3.0.76", "25", "b" * 64)}
        expect(plan(m, 3, cur) == (False, 3), "unchanged sources: nothing to publish")
        expect(plan(m, 3, dict(cur, **{"26.3": ("26.3.0.76", "25", "c" * 64)})) == (True, 4), "a changed source: v4")
        expect(plan(m, 3, dict(cur, **{"27.1": ("27.1.0.5", "27", "d" * 64)})) == (True, 4), "a new version: v4")
        expect(plan("", 0, cur) == (True, 1), "no release yet: v1")
        (dist / "gatec-carrier-1.21.3.jar").write_bytes(b"x")
        try:
            write_release(dist, "gatec-carrier-v3", "c0ffee", two, dg)
            expect(False, "an unexpected jar in the release directory is refused")
        except SystemExit:
            pass
        try:
            write_release(dist, "latest", "c0ffee", two, dg)
            expect(False, "a tag other than gatec-carrier-v<N> is refused")
        except SystemExit:
            pass

        # the client verdict: PASS on stdout and in latest.log; FAIL or a missing log line is a failure
        p = "SMOKEHARNESS_BOOT_TEST: PASS — gauntlet ran 9 steps"
        expect(judge(f"x\n{p}\n", f"[STDOUT]: {p}\n")[0], "PASS on both passes")
        expect(not judge(f"{p}\n", "")[0], "PASS only on stdout fails: the runner reads latest.log too")
        expect(not judge("SMOKEHARNESS_BOOT_TEST: FAIL — threw\n", "")[0], "FAIL fails")
        expect(not judge("nothing\n", "")[0], "no verdict fails")
        # client_smoke drives gradle and reads both places (a stand-in runner, no Gradle)
        proj = d / "proj"; (proj / "run/client/logs").mkdir(parents=True)
        seen = []

        def fake(cmd, cwd, env, log, timeout):
            seen.append(cmd)
            log.write_text(p + "\n", encoding="utf-8")
            (cwd / "run/client/logs/latest.log").write_text("[STDOUT]: " + p + "\n", encoding="utf-8")
            return 0
        res = client_smoke(proj, "launch", 5, runner=fake)
        expect(res[0] and "runClient" in seen[0] and f"-P{MODID}.testmode=launch" in seen[0], f"client_smoke: {res} {seen}")

        # playtest: the verdict on stdout, in latest.log, the PLAYTEST line and playtest-result.json must agree
        def pj(verdict, steps, finished=True):
            return json.dumps({"script": "x.playtest", "intent": "", "verdict": verdict, "finished": finished,
                               "steps": [{"n": i + 1, "name": f"s{i + 1}", "verdict": v, "detail": "d"} for i, v in enumerate(steps)],
                               "events": []})
        mp, mf = MARKER + ": PASS — playtest x: 2/2", MARKER + ": FAIL — playtest x: 1/3"
        po, fo = f"PLAYTEST: PASS -- 2/2 steps\n{mp}\n", f"PLAYTEST: FAIL -- 1/3 steps\n{mf}\n"
        expect(judge_playtest(po, mp, pj("PASS", ["PASS", "PASS"]))[0], "playtest PASS everywhere passes")
        expect(not judge_playtest(po, mp, pj("FAIL", ["PASS", "FAIL"]))[0], "playtest: a JSON FAIL fails a PASS run")
        expect(not judge_playtest(po, mp, "")[0], "playtest: no result JSON fails")
        expect(not judge_playtest(mp + "\n", mp, pj("PASS", ["PASS"]))[0], "playtest: no PLAYTEST line fails")
        expect(not judge_playtest(po, mp, pj("PASS", ["PASS"], finished=False))[0], "playtest: an unfinished run fails")
        expect(judge_playtest(fo, mf, pj("FAIL", ["PASS", "FAIL", "SKIPPED"]), "fail", ["PASS", "FAIL", "SKIPPED"])[0],
               "playtest --expect fail: FAIL from a step, the later step skipped, passes")
        expect(not judge_playtest(fo, mf, pj("FAIL", []), "fail")[0], "playtest --expect fail: a FAIL outside the steps (harness) fails")
        expect(not judge_playtest(fo, mf, pj("FAIL", ["FAIL", "SKIPPED", "SKIPPED"]), "fail", ["PASS", "FAIL", "SKIPPED"])[0],
               "playtest --expect-steps: another step pattern fails")
        expect(not judge_playtest(po, mp, pj("PASS", ["PASS", "PASS"]), "fail")[0], "playtest --expect fail: a PASS fails")
        script = d / "x.playtest"
        script.write_text("say hi\n", encoding="utf-8")

        def fake_pt(cmd, cwd, env, log, timeout):
            seen.append(cmd)
            outp = playtest_out(cwd, script)
            outp.mkdir(parents=True, exist_ok=True)
            (outp / "playtest-result.json").write_text(pj("PASS", ["PASS", "PASS"]), encoding="utf-8")
            log.write_text(po, encoding="utf-8")
            (cwd / "run/client/logs/latest.log").write_text("[Render thread/INFO]: " + mp + "\n", encoding="utf-8")
            return 0
        res = client_smoke(proj, "playtest", 5, runner=fake_pt, playtest=script)
        expect(res[0] and f"-P{MODID}.testmode=playtest" in seen[-1]
               and any(a.startswith(f"-P{MODID}.playtest=") and a.endswith("x.playtest") for a in seen[-1])
               and any(a.startswith(f"-P{MODID}.playtest.out=") for a in seen[-1])
               and (playtest_out(proj, script) / "latest.log").is_file(),
               f"client_smoke playtest: the script and out dir are passed, the logs kept beside the result: {res} {seen[-1]}")
        expect(not client_smoke(proj, "playtest", 5, runner=fake_pt)[0], "--mode playtest without --playtest fails")
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    if "--self-check" in sys.argv:
        return self_check()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("versions"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("prepare"); s.add_argument("--mc", required=True); s.add_argument("--out", required=True)
    s = sub.add_parser("digest"); s.add_argument("--mc", nargs="*")
    s = sub.add_parser("check-jar"); s.add_argument("--mc", required=True); s.add_argument("--jar", required=True)
    s = sub.add_parser("client-smoke"); s.add_argument("--project", required=True)
    s.add_argument("--mode", default="gauntlet", choices=list(MODES))
    s.add_argument("--timeout", type=int, default=1200)
    s.add_argument("--playtest", help="--mode playtest: the script")
    s.add_argument("--expect", default="pass", choices=["pass", "fail"], help="--mode playtest: the verdict required")
    s.add_argument("--expect-steps", help="--mode playtest: each step's verdict, comma-separated (PASS,FAIL,SKIPPED)")
    s = sub.add_parser("plan"); s.add_argument("--published"); s.add_argument("--newest", type=int, required=True)
    s = sub.add_parser("release"); s.add_argument("--dist", required=True); s.add_argument("--tag", required=True)
    s.add_argument("--commit", required=True)
    a = ap.parse_args()
    if a.cmd == "versions":
        vs = versions()
        if a.json:
            print(json.dumps([{k: v[k] for k in ("mc", "neo", "java")} for v in vs]))
        else:
            for v in vs:
                print(f"{v['mc']:8} NeoForge {v['neo']:12} Java {v['java']}  JEI {v['jei'] or '-':14} "
                      f"minecraft {v['mc_range']} neoforge {v['neo_range']}")
        return 0
    if a.cmd == "prepare":
        v = prepare(a.mc, a.out)
        print(f"gatec-carrier: wrote the {a.mc} carrier project (NeoForge {v['neo']}, Java {v['java']}) to {a.out}")
        return 0
    if a.cmd == "digest":
        for mc, h in digests(a.mc).items():
            print(f"{mc} {h}")
        return 0
    if a.cmd == "check-jar":
        v = version(a.mc)
        probs = check_jar(a.jar, a.mc, v["neo"], v["java"])
        for p in probs:
            print(f"gatec-carrier: FAIL {a.jar}: {p}")
        if not probs:
            print(f"gatec-carrier: OK {a.jar}: smokeharness for Minecraft {a.mc} (accepted by {v['mc_range']}) on NeoForge "
                  f"{v['neo']} (accepted by {v['neo_range']}), Java {v['java']} classes, entry class and contract present")
        return 1 if probs else 0
    if a.cmd == "client-smoke":
        steps = [x.strip() for x in a.expect_steps.split(",")] if a.expect_steps else None
        ok, why = client_smoke(a.project, a.mode, a.timeout, playtest=a.playtest, expect=a.expect, expect_steps=steps)
        print(f"gatec-carrier: client smoke ({a.mode}) {'PASS' if ok else 'FAIL'}: {why}")
        return 0 if ok else 1
    if a.cmd == "plan":
        text = pathlib.Path(a.published).read_text(encoding="utf-8") if a.published else ""
        dg = digests()
        cur = {v["mc"]: (v["neo"], str(v["java"]), dg[v["mc"]]) for v in versions()}
        pub, ver = plan(text, a.newest, cur)
        print(f"publish={'true' if pub else 'false'}")
        print(f"version={ver}")
        return 0
    if a.cmd == "release":
        m = write_release(a.dist, a.tag, a.commit)
        print(f"gatec-carrier: wrote {m} and {pathlib.Path(a.dist) / 'SHA256SUMS'}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
