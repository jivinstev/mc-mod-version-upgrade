#!/usr/bin/env python3
"""Drive Gate B to green with cheap workers: run the GameTest server, hand ONE failure to a headless
worker in a clean context, rerun; repeat. The runtime counterpart of tools/file-loop.py (issue #27).

    python3 tools/gate-loop.py --work <gradle project> [--model sonnet] [--budget 5] [--max-runs 6]
    python3 tools/gate-loop.py --work <gradle project> --gatec launch,spawn,battle,gauntlet

--gatec drives Gate C instead: a real client per phase (`runClient -Pboottest -Ptestmode=<phase>`, under
Xvfb + Mesa when there is no display), green on the harness's `<MODID>_BOOT_TEST: PASS` line. Each phase
must pass before the next starts; the budget and run cap are shared across phases. The harness itself
comes from tools/scaffold-gatec.py.

A failure is either a load crash (the first `Caused by:` plus the mod's own stack frames) or failed
GameTests (their names and messages). The worker gets that, the catalogue's runtime section (§R) to
look in, the Minecraft/NeoForge sources to check APIs, and Read/Edit/Grep/Glob only -- it cannot boot
the game; this loop does. Every worker's exact dollars are logged. Stops at "All N required tests
passed", at --budget, at --max-runs, or when a run fails the same way twice (a fix that did nothing).
Standard library only; needs the `claude` CLI.
"""
import collections, argparse, importlib.util, json, os, pathlib, re, shutil, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
_FL = None


def _fl():
    """The burn-down engine (tools/file-loop.py), loaded only when a worker is about to run: a worker-free CI
    run (--no-workers) never needs it, so the Port CI kit does not carry it."""
    global _FL
    if _FL is None:
        s = importlib.util.spec_from_file_location("fl", ROOT / "tools/file-loop.py")
        _FL = importlib.util.module_from_spec(s); s.loader.exec_module(_FL)
    return _FL


_ss = importlib.util.spec_from_file_location("srcsets", ROOT / "tools/srcsets.py")
srcsets = importlib.util.module_from_spec(_ss); _ss.loader.exec_module(srcsets)
sys.path.insert(0, str(ROOT / "tools"))
from gitbash import BASH   # noqa: E402  (Git Bash on Windows; plain bash elsewhere)

PROMPT = """A Minecraft mod ported to {target} compiles, but its headless GameTest server (Gate B) fails:

{failure}

Find the cause in src/main/java (or, for data files, src/main/resources) and fix it with the smallest change that keeps the mod's behaviour.
Never delete a feature, empty a method or disable a test to make this go away. CATALOG.md section R
(runtime patterns) in {catalog} lists known causes. Minecraft/NeoForge sources to check APIs: {srcs}.
You cannot run the game. Finish with one line: FIXED: <what you changed>"""
PASS = re.compile(r'All (\d+) required tests passed')
BOOT = re.compile(r'[A-Z0-9_]+_BOOT_TEST: (PASS|FAIL).*')
TOP_EXC = re.compile(r'\b(?:[a-z_]\w*\.)+[A-Z]\w*(?:Exception|Error)\b(?::[^\n]{0,240})?')
MOD_FRAME = re.compile(r'^\s+at (?!java\.|jdk\.|sun\.|net\.minecraft\.|net\.neoforged\.|com\.mojang\.|cpw\.|org\.)\S+')


# A client whose mods fail to load does not crash: it shows NeoForge's "Error loading mods" screen and
# waits for a click. On 26.x no client tick fires before loading finishes (CATALOG §V69), so the harness
# never reports either, and the run would sit until its timeout. These lines mean mod loading has
# failed; the gate stops the client as soon as one appears, and the exception goes to the worker.
LOAD_FAILED = re.compile(r"Cannot register listeners for|has failed to load correctly|ModLoadingException|"
                         r"Failed to create mod instance|Error loading mods|LoadingFailedException|"
                         r"Mod loading has failed|Encountered an error during the \w+ event phase")


def load_failed(text):
    """A real mod-loading failure, not a mention of the words: a DEBUG line naming the TYPE (measured: a mixin
    renaming an accessor that returns ModLoadingException) stopped a healthy client and sent two workers after
    nothing."""
    return any(LOAD_FAILED.search(l) for l in text.splitlines()
               if "/DEBUG]" not in l and "/TRACE]" not in l and not re.search(r"L[\w/]+/ModLoadingException;", l))


STALL_SECONDS = 420   # a client whose log has not grown for this long, with no verdict, is stuck, not slow


def _stop(proc):
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, 15)
        else:
            proc.kill()
        proc.wait(timeout=30)
    except Exception:  # noqa: BLE001 -- best effort; the next line makes sure
        try:
            if hasattr(os, "killpg"):
                os.killpg(proc.pid, 9)
            else:
                proc.kill()
        except Exception:  # noqa: BLE001
            pass


def LOG_DIR(work):
    """Where run logs go: the workspace, unless PORT_LOG_DIR says otherwise (an upstream port must not write
    into the author's tree)."""
    d = pathlib.Path(os.environ.get("PORT_LOG_DIR") or work)
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_gate(work, task, heap, log, phase=None, timeout=1500):
    cmd = [BASH, "gradlew", task, "--console=plain", "--init-script",
           str(ROOT / "tools/central-mirror.init.gradle"), f"-Dorg.gradle.jvmargs=-Xmx{heap}"]
    # an upstream port keeps its test harness OUTSIDE the author's tree and wires it in per run
    # (templates/upstream-harness/gates.init.gradle; tools/port-upstream.py sets this)
    for extra in filter(None, os.environ.get("PORT_GRADLE_INIT", "").split(os.pathsep)):
        cmd += ["--init-script", extra]
    env = dict(os.environ)
    if phase:   # Gate C: a real client; headless Linux gets Xvfb + Mesa's software GL (OpenGL 4.5 core)
        cmd += ["-Pboottest", f"-Ptestmode={phase}", "--no-daemon"]
        if sys.platform.startswith("linux") and not env.get("DISPLAY"):
            cmd = ["xvfb-run", "-a", "-s", "-screen 0 1280x720x24"] + cmd
            env["LIBGL_ALWAYS_SOFTWARE"] = "1"
    import time
    with open(log, "w", encoding="utf-8") as fh:
        proc = subprocess.Popen(cmd, cwd=work, stdout=fh, stderr=subprocess.STDOUT, env=env,
                                start_new_session=hasattr(os, "killpg"))
        start = last_growth = time.time(); size = 0; failed_at = None; why = None
        while proc.poll() is None:
            time.sleep(2)
            now = time.time()
            fh.flush()
            cur = os.path.getsize(log)
            if cur != size:
                size, last_growth = cur, now
            if phase and failed_at is None and cur:
                with open(log, encoding="utf-8", errors="replace") as rd:
                    if load_failed(rd.read()):
                        failed_at = now      # give it a few seconds to finish printing the stack trace
            if failed_at and now - failed_at > 8:
                why = "mod loading failed; the client shows the error screen and would wait for a click"
            elif phase and now - last_growth > STALL_SECONDS:
                why = f"the client's log has not grown for {STALL_SECONDS}s and it has given no verdict"
            elif now - start > timeout:
                why = f"TIMEOUT after {timeout}s with no verdict"
            if why:
                _stop(proc)
                break
        if why:
            fh.write(f"\n[gate-loop] stopped the run: {why}\n")
    return pathlib.Path(log).read_text(encoding="utf-8", errors="replace")


def listener_audit(work):
    """Before a client is launched: a listener on an event the bus refuses (26.x made several ABSTRACT,
    one subclass per phase) compiles, passes Gate B (a server never registers client listeners) and kills
    the client at load (CATALOG §X33). The audit asks the target's own jars, so it is cheap and exact.
    -> (findings text or None, a note). Runs only where the audit can: a port with versions/<target>."""
    gp = work / "gradle.properties"
    mc = (re.findall(r"(?m)^mc\s*=\s*(\S+)", gp.read_text(encoding="utf-8")) or [None])[0] if gp.exists() else None
    tool = ROOT / "templates/multi-version/tools/audit-event-listeners.py"
    if not mc or not (work / f"versions/{mc}.properties").exists() or not tool.exists():
        return None, "listener audit: skipped (not a versioned port)"
    cp = work / "build" / f"qtc-classpath-{mc}.txt"
    if not cp.exists():
        try:
            _e = importlib.util.spec_from_file_location("eh", ROOT / "tools/era-hop.py")
            eh = importlib.util.module_from_spec(_e); _e.loader.exec_module(eh)
            eh.gradle_classpath(work, mc, cp)
        except Exception as e:  # noqa: BLE001 -- the audit is a pre-check; the client run still decides
            return None, f"listener audit: skipped (no classpath: {str(e)[:120]})"
    # the audit reads the PREPARED tree; refresh it, or a worker's fix is judged against the stale copy
    subprocess.run([BASH, "gradlew", "-q", "prepareSources", f"-Pmc={mc}", "--console=plain", "--init-script",
                    str(ROOT / "tools/central-mirror.init.gradle")], cwd=work, capture_output=True, timeout=900)
    r = subprocess.run([sys.executable, str(tool), str(work), f"--mc={mc}"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout + r.stderr).strip()
    if r.returncode == 1:
        return ("Before launching the client, this check found listeners the event bus will refuse at load "
                "(\"Cannot register listeners for abstract class ...\"; name a concrete subclass instead, "
                "CATALOG §X33):\n" + out[-2500:]), "listener audit: findings"
    return None, f"listener audit: exit {r.returncode}: {out.splitlines()[-1][:160] if out else ''}"


def json_strict(work):
    """Repair lenient-only JSON (a `//` line, a trailing comma, a BOM) before booting -- 26.x skips such a
    file, and for a lang file that is every name in the mod (§S8). Free; only files with no safe repair
    go to the worker. -> findings text or None."""
    r = subprocess.run([sys.executable, str(ROOT / "tools/fix-json-strict.py"), "--work", str(work)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip()[-2000:] if r.returncode == 1 else None


DROPS = re.compile(r"\bContainers\.drop\w*\(|\bdropContents\(|\.spawnAtLocation\(|\bpopResource\w*\(|\.dropItemStack\(")


def lost_drops(work):
    """Calls that put items into the world (a container spilling its contents when broken) that the hop's
    starting snapshot had and the port no longer has anywhere in that file. They compile away silently:
    26.x removes the block entity BEFORE `affectNeighborsAfterRemoval` runs (§V48), so a port that moves
    `onRemove`'s body there has nothing to drop and deletes the call. Measured: five storage blocks on a
    26.2 port lost their contents-on-break. -> findings text or None."""
    snaps = sorted((work / "hop-start").glob("*")) if (work / "hop-start").is_dir() else []
    if not snaps:
        return None
    start, src = snaps[-1], work / "src/main/java"
    now = "\n".join(f.read_text(encoding="utf-8", errors="replace") for f in src.rglob("*.java"))
    lost = []
    for f in sorted(start.rglob("*.java")):
        before = len(DROPS.findall(f.read_text(encoding="utf-8", errors="replace")))
        cur = src / f.relative_to(start)
        after = len(DROPS.findall(cur.read_text(encoding="utf-8", errors="replace"))) if cur.exists() else 0
        if before > after:
            lost.append(f"{f.relative_to(start).as_posix()} ({before} -> {after})")
    if not lost or len(DROPS.findall(now)) >= sum(1 for _ in DROPS.finditer(
            "\n".join(f.read_text(encoding="utf-8", errors="replace") for f in start.rglob("*.java")))):
        return None    # every drop still exists somewhere (moved to the block entity): not lost
    return ("These files dropped items into the world when broken (a container spilling its contents) and the "
            "port no longer does anywhere: " + "; ".join(lost[:12]) + ". On 26.x the block entity is already gone "
            "when Block#affectNeighborsAfterRemoval runs: move the drop to the BLOCK ENTITY's "
            "preRemoveSideEffects(BlockPos, BlockState) (CATALOG §V48). Restore every one; never delete a drop.")


def content_census(work):
    """Before a server is booted: content the mod's own lang file declares, that 1.21 made data-driven
    (enchantments above all), must still exist (tools/content-census.py). A port that deleted the Java
    registration and wrote no data file is otherwise green on every gate. -> findings text or None."""
    r = subprocess.run([sys.executable, str(ROOT / "tools/content-census.py"), "--work", str(work)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (r.stdout.strip()[-2500:] or None) if r.returncode == 1 else None


def mixin_audit(work):
    """Before a server is booted: every mixin target checked against the target's own sources in ONE pass
    (tools/audit-mixin-targets.py, §X27). Measured: Gate B found four broken 26.2 mixin targets one boot
    and one worker at a time; this finds them together. -> findings text or None."""
    gp = work / "gradle.properties"
    mc = (re.findall(r"(?m)^mc\s*=\s*(\S+)", gp.read_text(encoding="utf-8")) or [None])[0] if gp.exists() else None
    if not list((work / "src/main/resources").glob("*mixins*.json")):
        return None
    args = [f"--mc={mc}"] if mc and (work / f"versions/{mc}.properties").exists() else []
    if args and (work / "gradlew").exists():   # the audit reads the PREPARED tree: refresh it so a worker's fix is not judged on a stale copy
        subprocess.run([BASH, "gradlew", "-q", "prepareSources", f"-Pmc={mc}", "--console=plain", "--init-script",
                        str(ROOT / "tools/central-mirror.init.gradle")], cwd=work, capture_output=True, timeout=900)
    audit = lambda: subprocess.run([sys.executable, str(ROOT / "tools/audit-mixin-targets.py"), str(work), *args],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace")
    r = audit()
    if "no sources" in r.stderr and (work / "gradlew").exists():
        # a fresh checkout (CI) has not staged Minecraft's sources yet; the audit's sys.exit(msg) is ALSO exit 1,
        # which read as findings with an empty list and failed a clean port's Gate B. Stage them once and retry.
        subprocess.run([BASH, "gradlew", "-q", "createMinecraftArtifacts", "--console=plain", "--init-script",
                        str(ROOT / "tools/central-mirror.init.gradle")], cwd=work, capture_output=True, timeout=1800)
        r = audit()
    if r.returncode != 1 or "MISMATCH" not in r.stdout:   # only real mismatches are findings
        return None
    return ("These mixins name a member the target's vanilla code no longer has (each fails at mixin APPLY and "
            "stops the game loading; CATALOG §R14/§R17). Retarget each to the current member, or delete a mixin "
            "whose feature vanilla now covers and record it:\n" + r.stdout.strip()[-2500:])


# A crash signature that is one instance of a whole FAMILY: the worker is told so, or it fixes one site
# per boot (measured: the 26.x unset-Properties-id crash, one block per Gate B run).
FAMILY_HINTS = [
    (re.compile(r"unbound value: ResourceKey\[minecraft:(block|item) /|(Block|Item) id not set"),
     "This is CATALOG §R25 and it is a FAMILY: from 1.21.2 every Block and Item Properties needs "
     ".setId(ResourceKey.create(Registries.BLOCK/ITEM, id)). Fix it where Properties are built for ALL "
     "blocks and items in one change (a registration helper, or every register call), not just the one named."),
]


def data_errors(text, ns):
    """The mod's own data files that failed to PARSE at load. A green GameTest run says nothing about
    them: the game logs one ERROR per file and goes on without it, so a recipe, advancement or loot
    table is silently missing (§144, §V67 -- on 26.2 a recipe whose result is read with ItemStack.CODEC
    fails exactly this way). Missing tag REFERENCES are not counted: those are usually optional-mod
    entries, and the mod's own upstream ships them."""
    if not ns:
        return []
    pat = re.compile(r"(Couldn't parse data file '%s:[^']*'|Failed to parse \S+ from pack mod/%s\b|"
                     r"Couldn't parse element [^\n]*?%s:)[^\n]*" % ((re.escape(ns),) * 3))
    seen, out = set(), []
    for m in pat.finditer(text):
        line = m.group(0).strip()[:400]
        key = re.sub(r"'%s:[^']*'" % re.escape(ns), "'<file>'", line)
        if line not in seen:
            seen.add(line); out.append((key, line))
    return out


# What a PASSING client can still be telling you, in one WARN/ERROR line each (CATALOG §X49): the mod's own
# assets missing (each renders or plays as nothing) and errors its code threw that something caught and only
# logged. A crash gate cannot see any of them. Each pattern must name the mod's namespace, so another mod's
# noise is never this port's.
ASSET_LOST = [
    (r"Missing item model for location ({ns}:[\w/.-]+)", "item with no model (renders as the missing-texture cube; "
     "from 1.21.2 it needs assets/<ns>/items/<id>.json, CATALOG §V42b)"),
    (r"Failed to load texture: ({ns}:[\w/.-]+)", "texture that does not load"),
    (r"Missing textures in model ({ns}:[\w/.#-]+)", "model whose textures are missing"),
    (r"File ({ns}:sounds/[\w/.-]+) does not exist", "sound file a sounds.json names but the jar lacks"),
    (r"Missing sound for event: ({ns}:[\w/.-]+)", "sound event with no sound"),
    (r"Unable to find (?:model|animation)[:]? ({ns}:[\w/.-]+)", "GeckoLib model or animation that is not found"),
]
VANILLA_FRAME = re.compile(r"^(?:java\.|jdk\.|sun\.|com\.sun\.|net\.minecraft\.|net\.neoforged\.|com\.mojang\.|"
                           r"com\.google\.|it\.unimi\.|io\.netty\.|org\.|cpw\.|com\.llamalad7\.)")
FRAME_CLASS = re.compile(r"^\s*at (?:\S+/)?([\w$.]+)\(")    # drops a 'LAYER/module@ver/' prefix
HARNESS = re.compile(r"(BootSmokeTest|RecipeProbe|GameTestRegistrar)")
EXC_HEAD = re.compile(r"^(?:Caused by: )?((?:[a-z_]\w*\.)+[A-Z]\w*(?:Exception|Error|Throwable))\b(:.*)?$")


def mod_packages(work):
    """The Java packages of the mod's @Mod classes: whose frames make a logged exception this mod's own."""
    out = set()
    for f in pathlib.Path(work, "src/main/java").rglob("*.java") if work else []:
        t = f.read_text(encoding="utf-8", errors="replace")
        if re.search(r"^@Mod\b|^\s*@Mod\(", t, re.M):
            m = re.search(r"^package\s+([\w.]+)\s*;", t, re.M)
            if m:
                out.add(m.group(1))
    return out


def known_findings(work):
    """<repo>/.github/gatec-known.txt: one substring per line (+ '# why'); a finding containing it is a recorded,
    pre-existing defect -- still listed, no longer failing. Lets a port be green without hiding what is wrong."""
    f = pathlib.Path(work, ".github/gatec-known.txt") if work else None
    if not f or not f.exists():
        return []
    return [l.split("#", 1)[0].strip() for l in f.read_text(encoding="utf-8").splitlines() if l.split("#", 1)[0].strip()]


def logged_exceptions(text):
    """{(exception, the first non-platform frame's method): count} for every stack trace in a log, most first.
    The owner is the code that threw, read past the platform (Minecraft, NeoForge, the JDK, libraries)."""
    lines, seen = text.splitlines(), {}
    for i, l in enumerate(lines):
        m = EXC_HEAD.match(l.strip())
        if not m or i + 1 >= len(lines) or not lines[i + 1].lstrip().startswith("at "):
            continue
        frames = [x for x in lines[i + 1:i + 60] if x.lstrip().startswith("at ")]
        names = [FRAME_CLASS.match(x).group(1) for x in frames if FRAME_CLASS.match(x)]
        owner = next((n for n in names if not VANILLA_FRAME.match(n) and "$$Lambda" not in n), None)
        if owner and not HARNESS.search(owner):
            seen[(m.group(1), owner)] = seen.get((m.group(1), owner), 0) + 1
    return dict(sorted(seen.items(), key=lambda kv: -kv[1]))


def foreign_findings(text, work=None):
    """Logged exceptions thrown in ANOTHER mod's code during this mod's run -- never this port's failure, but
    often triggered by it (a mod calling a library's API that is broken). Reported, so the trail is not lost."""
    pkgs = mod_packages(work)
    return [f"{exc} thrown in {where}, caught and only logged ({n}x)" for (exc, where), n in logged_exceptions(text).items()
            if not any(where.startswith(pk + ".") for pk in pkgs)] if pkgs else []


def log_findings(text, ns, work=None):
    """-> (findings, known): the mod's lost assets and the logged exceptions its own code threw, one line each."""
    out = []
    if ns:
        for rx, what in ASSET_LOST:
            hits = sorted(set(re.findall(rx.format(ns=re.escape(ns)), text)))
            if hits:
                out.append(f"{len(hits)} {what}: {', '.join(hits[:12])}")
        anims = sorted(set(re.findall(r"Unable to parse animation: (\S+)", text)))
        mine = [a for a in anims if work and any(a in f.read_text(encoding="utf-8", errors="replace")
                for f in pathlib.Path(work, "src/main/resources/assets", ns).rglob("*.json"))]
        if mine:
            out.append(f"{len(mine)} GeckoLib animation(s) that fail to parse (each plays as nothing): {', '.join(mine[:12])}")
        unplaced = sorted(set(re.findall(r"^\s+- (%s:[\w/.-]+)$" % re.escape(ns), text, re.M)))
        if unplaced and "RegisterSpawnPlacementsEvent" in text:
            out.append(f"{len(unplaced)} entity type(s) with a spawn entry but no spawn placement (they spawn anywhere, "
                       f"even in mid-air): {', '.join(unplaced)}")
    pkgs = mod_packages(work)
    if pkgs:
        for (exc, where), n in logged_exceptions(text).items():
            if any(where.startswith(pk + ".") for pk in pkgs):
                out.append(f"{exc} thrown in {where}, caught and only logged ({n}x)")
    known = known_findings(work)
    return [f for f in out if not any(k in f for k in known)], [f for f in out if any(k in f for k in known)]


def failure_of(text, client=False, ns=None, work=None):
    """-> (kind, text for the worker, a short signature to detect a repeat) or None when green."""
    lines = text.splitlines()
    if client:   # Gate C: the harness's own verdict decides; a FAIL carries its reason
        verdicts = [m.group(0) for m in BOOT.finditer(text)]
        if verdicts and ": PASS" in verdicts[-1]:
            # a client that did not crash is not a client that DRAWS the mod: an item with no model is the
            # magenta cube with one WARN line (§V42b; 184 items on a 26.2 port behind a PASS)
            bad, _known = log_findings(text, ns, work)
            if not bad:
                return None
            kind = "assets" if all("thrown in" not in b for b in bad) else "logged"
            return (kind, "The client passed, but the log shows what it could not do (CATALOG §X49); fix each, or "
                    "record a pre-existing one in .github/gatec-known.txt with why:\n" + "\n".join(f"  - {b}" for b in bad),
                    f"{kind}:" + "|".join(b[:60] for b in bad)[:180])
        if verdicts:
            boot = [l.strip() for l in lines if "_BOOT_TEST:" in l][-15:]
            return "gatec", "\n".join(boot), verdicts[-1][:200]
    elif PASS.search(text):
        bad = data_errors(text, ns)
        if not bad:
            return None
        kinds = collections.Counter(k for k, _ in bad)
        body = [f"{len(bad)} of this mod's own data files fail to parse at load (each is then silently missing):"]
        body += [f"  {n}x like: {next(l for k2, l in bad if k2 == k)}" for k, n in kinds.most_common(8)]
        return "data", "\n".join(body), f"data:{len(bad)}:{kinds.most_common(1)[0][0][:120]}"
    for i, l in enumerate(lines):
        if l.startswith("Caused by:"):   # the deepest cause is the last top-level one
            last = i
    if "last" in locals():
        frames = [x.strip() for x in lines[last + 1:last + 60] if MOD_FRAME.match(x)][:8]
        cause = lines[last]
        return "crash", "\n".join([cause] + frames), cause[:200]
    # the NAMED lines first: "<test> failed at <pos>! <why>", the per-test summary "   - <id>: <why>" and a
    # harness's own "_BASELINE ... FAILED" lines. Measured: matching only "N required tests failed" handed a
    # worker that one line and nothing about which test or why.
    fails = [l.strip() for l in lines if re.search(r'( failed at |_BASELINE: .*FAILED|^\S*\s*\[.*GameTestServer\]:\s+- )', l)]
    fails += [l.strip() for l in lines if re.search(r'(failed!|::.*fail|GameTestAssert|required tests? failed)', l)
              and l.strip() not in fails]
    if fails:
        return "tests", "\n".join(fails[:30]), "|".join(fails[:3])[:200]
    m = [l for l in lines if re.search(r"\.(?:java|kt|groovy|gradle):\d+: error:|^\s*error: |^e: ", l)][:10]
    if m:
        return "compile", "\n".join(m), m[0][:200]
    # a crash thrown with no `Caused by:` -- e.g. "IllegalStateException: Registry is already frozen" at mod
    # setup (§R13). Measured: filed as "unknown", its worker changed item code instead of the registration.
    # the LAST one: a game log carries many harmless logged exceptions before the one that stopped it
    for i in range(len(lines) - 1, -1, -1):
        l = lines[i]
        if TOP_EXC.search(l) and not l.lstrip().startswith("at "):
            frames = [x.strip() for x in lines[i + 1:i + 80] if MOD_FRAME.match(x)][:8]
            return "crash", "\n".join([l.strip()[:400]] + frames), TOP_EXC.search(l).group(0)[:200]
    return "unknown", "\n".join(lines[-40:]), lines[-1][:200] if lines else ""


def call_worker(a, work, srcs, text, phase, note, run):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD"}
    prompt = PROMPT.format(target=a.target, failure=text, catalog=ROOT, srcs=srcs or "(none)")
    if phase:
        prompt = prompt.replace("its headless GameTest server (Gate B) fails", f"its real client (Gate C, phase {phase}) fails")
    snap = _fl().singleshot.sibling_snapshot(work)
    r = subprocess.run(["claude", "-p", prompt,
                        "--model", _fl().MODELS[a.model], "--output-format", "json", "--permission-mode", "acceptEdits",
                        "--allowedTools", "Read,Edit,Write,Grep,Glob", "--add-dir", str(ROOT),
                        *(["--add-dir", srcs] if srcs else [])],
                       cwd=work, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=a.timeout, stdin=subprocess.DEVNULL)
    # a fix belongs in THIS mod: an edit to a sibling repo (the library it depends on) is undone and recorded,
    # so the failure surfaces as the library's own bug instead of a silent half-fix in someone else's tree
    outside = _fl().singleshot.restore_siblings(snap)
    try:
        d = json.loads(r.stdout)
    except ValueError:
        d = {"total_cost_usd": 0, "result": (r.stdout + r.stderr)[-300:]}
    note(event="worker", run=run, phase=phase, model=a.model, usd=d.get("total_cost_usd"), turns=d.get("num_turns"),
         result=(d.get("result") or "")[-300:], reverted_outside=outside or None)
    return d.get("total_cost_usd") or 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work"); ap.add_argument("--model", default="sonnet", choices=("haiku", "sonnet", "opus"))
    ap.add_argument("--task", default="runGameTestServer")
    ap.add_argument("--no-workers", action="store_true",
                    help="CI: report a red gate and exit 1; never call a model")
    ap.add_argument("--budget", type=float, default=5.0); ap.add_argument("--max-runs", type=int, default=6)
    ap.add_argument("--target", default=None); ap.add_argument("--sources", default="auto")
    ap.add_argument("--heap", default="6g"); ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--log", default="gate-loop.jsonl")
    ap.add_argument("--gatec", help="comma list of Gate C phases (launch,spawn,battle,gauntlet) instead of Gate B")
    ap.add_argument("--namespace", help="the mod's id, for the data-load check (default: gradle.properties mod_id)")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.target is None and a.work:   # the build says what it targets (tools/srcsets.py)
        a.target = srcsets.target(pathlib.Path(a.work)) or "NeoForge 1.21.1"
    if a.self_check:
        return self_check()
    if not a.work:
        ap.error("--work is required")
    work = pathlib.Path(a.work).resolve()
    # sources (and dependency stubs) are only for workers; a worker-free CI run skips extracting them
    srcs = "" if a.no_workers else (str(_fl().find_sources(work) or "") if a.sources == "auto" else a.sources)
    logf = open(a.log, "a", encoding="utf-8")
    note = lambda **k: (logf.write(json.dumps(k) + "\n"), logf.flush(), print(json.dumps(k)[:300]))
    spent, last_sig = 0.0, None
    gp = work / "gradle.properties"
    ns = a.namespace
    if not ns and gp.exists():
        ns = (re.findall(r"(?m)^mod_id\s*=\s*(\S+)", gp.read_text(encoding="utf-8")) or [None])[0]
    phases = a.gatec.split(",") if a.gatec else [None]
    task = "runClient" if a.gatec else a.task
    run = 0
    while phases:
        run += 1
        if run > a.max_runs:
            break
        phase = phases[0]
        if phase:
            found, why = listener_audit(work)
            note(event="listener-audit", run=run, note=why)
        else:
            found = "\n\n".join(x for x in (json_strict(work), content_census(work), lost_drops(work),
                                            mixin_audit(work)) if x) or None
            note(event="pre-checks", run=run, note="findings" if found else "clean or not applicable")
        if found:
            f = ("listeners" if phase else "content", found, ("listeners:" if phase else "content:") + found[-160:])
            kind, text, sig = f
            note(event="red", run=run, phase=phase, kind=kind, sig=sig[:200])
            if sig == last_sig:
                note(event="stuck", run=run, spent=round(spent, 4)); return 1
            last_sig = sig
            if a.no_workers:
                return verdict_red(note, run, phase, text)
            spent += call_worker(a, work, srcs, text, phase, note, run)
            if spent >= a.budget:
                note(event="budget", spent=round(spent, 4)); return 1
            continue
        if not phase:
            gone = fresh_world(work)
            if gone:
                note(event="fresh-world", run=run, note=str(gone))
        f = failure_of(run_gate(work, task, a.heap, LOG_DIR(work) / f"gate-loop{'-' + phase if phase else ''}.log", phase),
                       client=bool(phase), ns=ns, work=work)
        if f is None:
            note(event="green", run=run, phase=phase, spent=round(spent, 4))
            phases.pop(0); last_sig = None
            if not phases:
                return 0
            continue
        kind, text, sig = f
        note(event="red", run=run, phase=phase, kind=kind, sig=sig)
        if sig == last_sig:
            note(event="stuck", run=run, spent=round(spent, 4)); return 1
        if spent >= a.budget:
            note(event="budget", spent=round(spent, 4)); return 1
        last_sig = sig
        if a.no_workers:
            return verdict_red(note, run, phase, text)
        text += "".join("\n\n" + hint for rx, hint in FAMILY_HINTS if rx.search(text))
        spent += call_worker(a, work, srcs, text, phase, note, run)
    note(event="max-runs", spent=round(spent, 4)); return 1


def fresh_world(work):
    """Gate B's world is deleted before every run: the GameTest server reuses `run/world`, so anything a run
    saved there -- a mod's own persistent state -- is still there for the next one, and the gate measures the
    previous run instead of the code. Measured: a mod whose weapon spawn-bans what it kills (saved data in the
    world) banned more of its own entity types each run, and two worker rounds ($5) chased "the level refused
    it" for 22 entities that no code change could fix. Only `world` goes; configs and the rest of run/ stay.
    -> the directory removed, or None."""
    base = pathlib.Path(os.environ.get("PORT_HARNESS_DIR") or work)
    w = base / "run" / "world"
    if w.is_dir():
        shutil.rmtree(w, ignore_errors=True)
        return w
    return None


def verdict_red(note, run, phase, text):
    """--no-workers (CI): a red gate is the verdict. Print what failed and stop; never call a model."""
    note(event="red-final", run=run, phase=phase)
    print(f"GATE {'C/' + phase if phase else 'B'} FAILED:\n{text[-4000:]}", flush=True)
    return 1


def self_check():
    crash = ("[x] FATAL\nCaused by: java.lang.RuntimeException: wrapper\n\tat net.neoforged.fml.X.y(X.java:1)\n"
             "Caused by: java.lang.IllegalArgumentException: class a.b.H has no @SubscribeEvent methods\n"
             "\tat net.neoforged.bus.EventBus.register(EventBus.java:1)\n\tat a.b.Mod.<init>(Mod.java:9)\n")
    k, text, _sig = failure_of(crash)
    ok = (k == "crash" and text.startswith("Caused by: java.lang.IllegalArgumentException") and "a.b.Mod.<init>" in text
          and "net.neoforged" not in text and failure_of("All 6 required tests passed :)") is None)
    ok = ok and failure_of("x\nMY_MOD_BOOT_TEST: PASS mode=spawn\n", client=True) is None
    import tempfile
    with tempfile.TemporaryDirectory() as d:                       # Gate B's saved world never outlives a run
        (pathlib.Path(d) / "run/world/data").mkdir(parents=True)
        (pathlib.Path(d) / "run/world/data/bans.dat").write_bytes(b"x")
        (pathlib.Path(d) / "run/config").mkdir()
        old = os.environ.pop("PORT_HARNESS_DIR", None)
        gone = fresh_world(pathlib.Path(d))
        ok = ok and gone is not None and not (pathlib.Path(d) / "run/world").exists() \
            and (pathlib.Path(d) / "run/config").is_dir() and fresh_world(pathlib.Path(d)) is None
        if old is not None:
            os.environ["PORT_HARNESS_DIR"] = old
    k2, t2, _s = failure_of("MY_MOD_BOOT_TEST: spawning 4\nMY_MOD_BOOT_TEST: FAIL — 1 entity crashed\n", client=True)
    ok = ok and k2 == "gatec" and "FAIL" in t2
    k3, _t, _s = failure_of("Caused by: java.lang.NoClassDefFoundError: x\n\tat a.b.C.d(C.java:1)\n", client=True)
    ok = ok and k3 == "crash"
    k4, t4, s4 = failure_of("[main/ERROR]: oops\njava.lang.IllegalStateException: Registry is already frozen (trying to add key x)\n"
                            "\tat net.minecraft.core.MappedRegistry.validateWrite(MappedRegistry.java:1)\n\tat a.b.Stats.init(Stats.java:9)\n")
    ok = ok and k4 == "crash" and "already frozen" in t4 and "a.b.Stats.init" in t4
    green = "All 6 required tests passed :)\n"
    bad = green + ("[ERROR] Couldn't parse data file 'mymod:cooking/a' from 'x': DataResult.Error['Item mymod:b does not have components yet']\n"
                   "[ERROR] Couldn't parse data file 'mymod:cooking/c' from 'x': DataResult.Error['Item mymod:d does not have components yet']\n"
                   "[ERROR] Couldn't parse data file 'other:z' from 'x': nope\n")
    ok &= failure_of(green, ns="mymod") is None and failure_of(bad, ns="other") is not None
    d = failure_of(bad, ns="mymod")
    ok &= d is not None and d[0] == "data" and "2 of this mod's own data files" in d[1] and "other:z" not in d[1]
    ok &= any(rx.search("Trying to access unbound value: ResourceKey[minecraft:item / m:x]") for rx, _h in FAMILY_HINTS)
    ok &= not any(rx.search("unbound value: ResourceKey[minecraft:sound_event / m:x]") for rx, _h in FAMILY_HINTS)
    named = ("[x] [Server thread/ERROR] [minecraft/LogTestReporter]: m:baseline/every_block_places failed at 1, 2, 3! 1 block(s) failed: m:rice\n"
             "M_BASELINE: block FAILED m:rice: did not stay placed\n"
             "[x] [Server thread/ERROR] [minecraft/GameTestServer]: 1 required tests failed :(\n"
             "[x] [Server thread/ERROR] [minecraft/GameTestServer]:    - m:baseline/every_block_places: 1 block(s) failed: m:rice\n")
    k2, t2, _s2 = failure_of(named)
    ok &= k2 == "tests" and "m:rice" in t2.splitlines()[0] and "every_block_places" in t2
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        w = pathlib.Path(d); (w / "hop-start/hop1/a").mkdir(parents=True); (w / "src/main/java/a").mkdir(parents=True)
        (w / "hop-start/hop1/a/Pot.java").write_text("void onRemove(){ Containers.dropContents(l, p, c); }", encoding="utf-8")
        (w / "src/main/java/a/Pot.java").write_text("void affectNeighborsAfterRemoval(){ }", encoding="utf-8")
        ok &= "a/Pot.java (1 -> 0)" in (lost_drops(w) or "")
        (w / "src/main/java/a/PotBE.java").write_text("void preRemoveSideEffects(){ Containers.dropContents(l, p, c); }",
                                                       encoding="utf-8")
        ok &= lost_drops(w) is None        # moved to the block entity: not lost
    cl = "M_BOOT_TEST: PASS - done\n[x] [Render thread/WARN] [minecraft/ModelManager]: Missing item model for location m:pie\n"
    ok &= failure_of(cl, client=True, ns="m")[0] == "assets" and failure_of(cl, client=True, ns="z") is None
    ok &= failure_of(bad) is None          # no namespace known: the check is off, never guessing
    with tempfile.TemporaryDirectory() as d:  # §X49: a PASS with a logged exception from the mod's own code is red
        w = pathlib.Path(d); (w / "src/main/java/a/m").mkdir(parents=True); (w / ".github").mkdir()
        (w / "src/main/java/a/m/M.java").write_text("package a.m;\n@Mod(\"m\")\nclass M {}\n", encoding="utf-8")
        lg = ("M_BOOT_TEST: PASS - done\n[S/ERROR] [x]: removal failed\njava.lang.UnsupportedOperationException: null\n"
              "\tat MC-BOOTSTRAP/com.google.common@32/com.google.common.collect.ImmutableCollection.remove(I.java:1)\n"
              "\tat TRANSFORMER/m@1/a.m.Util.clear(Util.java:9)\n"
              "[W/WARN] [minecraft/TextureManager]: Failed to load texture: m:textures/e/x.png\n"
              "\tat TRANSFORMER/o@1/b.o.Other.x(O.java:1)\n")
        f = failure_of(lg, client=True, ns="m", work=w)
        ok &= f is not None and f[0] == "logged" and "a.m.Util.clear" in f[1] and "m:textures/e/x.png" in f[1]
        ok &= failure_of(lg.replace("a.m.Util", "b.o.Util"), client=True, ns="z", work=w) is None   # not this mod's
        ok &= foreign_findings(lg.replace("a.m.Util", "b.o.Util"), w) == [
            "java.lang.UnsupportedOperationException thrown in b.o.Util.clear, caught and only logged (1x)"]
        (w / ".github/gatec-known.txt").write_text("a.m.Util.clear  # pre-existing upstream\nx.png\n", encoding="utf-8")
        ok &= failure_of(lg, client=True, ns="m", work=w) is None       # recorded as known: listed, not failing

    # not a load failure: a DEBUG mixin line naming the type; a real one still is
    ok = ok and not load_failed("[04:57:25] [Render thread/DEBUG] [mixin/]: Renaming @Accessor method "
                                "getError()Lnet/neoforged/fml/ModLoadingException; to getError$x in a.json\n")
    ok = ok and load_failed("[Render thread/ERROR] [ne.ne.fm.ModLoader/]: Mod loading has failed\n")
    # an ALSA "error" from the sound device is not a compile error
    k5 = failure_of("ALSA lib conf.c:5208:(_snd_config_evaluate) function snd_func_card_inum returned error: No such file\n"
                    "Caused by: java.lang.NoClassDefFoundError: x\n\tat a.b.C.d(C.java:1)\n", client=True)
    ok = ok and k5 and k5[0] == "crash"
    k6 = failure_of("src/main/java/a/B.java:12: error: cannot find symbol\n")
    ok = ok and k6 and k6[0] == "compile"
    print("self-check:", "OK" if ok else f"FAIL {k} {text!r}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
