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
import argparse, importlib.util, json, os, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
_s = importlib.util.spec_from_file_location("fl", ROOT / "tools/file-loop.py")
fl = importlib.util.module_from_spec(_s); _s.loader.exec_module(fl)

PROMPT = """A Minecraft mod ported to {target} compiles, but its headless GameTest server (Gate B) fails:

{failure}

Find the cause in src/main/java and fix it with the smallest change that keeps the mod's behaviour.
Never delete a feature, empty a method or disable a test to make this go away. CATALOG.md section R
(runtime patterns) in {catalog} lists known causes. Minecraft/NeoForge sources to check APIs: {srcs}.
You cannot run the game. Finish with one line: FIXED: <what you changed>"""
PASS = re.compile(r'All (\d+) required tests passed')
BOOT = re.compile(r'[A-Z0-9_]+_BOOT_TEST: (PASS|FAIL).*')
TOP_EXC = re.compile(r'\b(?:[a-z_]\w*\.)+[A-Z]\w*(?:Exception|Error)\b(?::[^\n]{0,240})?')
MOD_FRAME = re.compile(r'^\s+at (?!java\.|jdk\.|sun\.|net\.minecraft\.|net\.neoforged\.|com\.mojang\.|cpw\.|org\.)\S+')


def run_gate(work, task, heap, log, phase=None, timeout=1500):
    cmd = ["./gradlew", task, "--console=plain", "--init-script",
           str(ROOT / "tools/central-mirror.init.gradle"), f"-Dorg.gradle.jvmargs=-Xmx{heap}"]
    env = dict(os.environ)
    if phase:   # Gate C: a real client; headless Linux gets Xvfb + Mesa's software GL (OpenGL 4.5 core)
        cmd += ["-Pboottest", f"-Ptestmode={phase}", "--no-daemon"]
        if sys.platform.startswith("linux") and not env.get("DISPLAY"):
            cmd = ["xvfb-run", "-a", "-s", "-screen 0 1280x720x24"] + cmd
            env["LIBGL_ALWAYS_SOFTWARE"] = "1"
    with open(log, "w", encoding="utf-8") as fh:
        try:
            subprocess.run(cmd, cwd=work, stdout=fh, stderr=subprocess.STDOUT, env=env, timeout=timeout)
        except subprocess.TimeoutExpired:
            fh.write(f"\n[gate-loop] TIMEOUT after {timeout}s with no verdict\n")
    return pathlib.Path(log).read_text(encoding="utf-8", errors="replace")


def failure_of(text, client=False):
    """-> (kind, text for the worker, a short signature to detect a repeat) or None when green."""
    lines = text.splitlines()
    if client:   # Gate C: the harness's own verdict decides; a FAIL carries its reason
        verdicts = [m.group(0) for m in BOOT.finditer(text)]
        if verdicts and ": PASS" in verdicts[-1]:
            return None
        if verdicts:
            boot = [l.strip() for l in lines if "_BOOT_TEST:" in l][-15:]
            return "gatec", "\n".join(boot), verdicts[-1][:200]
    elif PASS.search(text):
        return None
    for i, l in enumerate(lines):
        if l.startswith("Caused by:"):   # the deepest cause is the last top-level one
            last = i
    if "last" in locals():
        frames = [x.strip() for x in lines[last + 1:last + 60] if MOD_FRAME.match(x)][:8]
        cause = lines[last]
        return "crash", "\n".join([cause] + frames), cause[:200]
    fails = [l.strip() for l in lines if re.search(r'(failed!|::.*fail|GameTestAssert|required tests? failed)', l)]
    if fails:
        return "tests", "\n".join(fails[:30]), "|".join(fails[:3])[:200]
    m = [l for l in lines if "error:" in l][:10]
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


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work"); ap.add_argument("--model", default="sonnet", choices=fl.TIERS)
    ap.add_argument("--task", default="runGameTestServer")
    ap.add_argument("--budget", type=float, default=5.0); ap.add_argument("--max-runs", type=int, default=6)
    ap.add_argument("--target", default="NeoForge 1.21.1"); ap.add_argument("--sources", default="auto")
    ap.add_argument("--heap", default="6g"); ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--log", default="gate-loop.jsonl")
    ap.add_argument("--gatec", help="comma list of Gate C phases (launch,spawn,battle,gauntlet) instead of Gate B")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not a.work:
        ap.error("--work is required")
    work = pathlib.Path(a.work).resolve()
    srcs = str(fl.find_sources(work) or "") if a.sources == "auto" else a.sources
    logf = open(a.log, "a", encoding="utf-8")
    note = lambda **k: (logf.write(json.dumps(k) + "\n"), logf.flush(), print(json.dumps(k)[:300]))
    spent, last_sig = 0.0, None
    phases = a.gatec.split(",") if a.gatec else [None]
    task = "runClient" if a.gatec else a.task
    run = 0
    while phases:
        run += 1
        if run > a.max_runs:
            break
        phase = phases[0]
        f = failure_of(run_gate(work, task, a.heap, work / f"gate-loop{'-' + phase if phase else ''}.log", phase),
                       client=bool(phase))
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
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD"}
        prompt = PROMPT.format(target=a.target, failure=text, catalog=ROOT, srcs=srcs or "(none)")
        if phase:
            prompt = prompt.replace("its headless GameTest server (Gate B) fails", f"its real client (Gate C, phase {phase}) fails")
        r = subprocess.run(["claude", "-p", prompt,
                            "--model", fl.MODELS[a.model], "--output-format", "json", "--permission-mode", "acceptEdits",
                            "--allowedTools", "Read,Edit,Write,Grep,Glob", "--add-dir", str(ROOT),
                            *(["--add-dir", srcs] if srcs else [])],
                           cwd=work, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=a.timeout, stdin=subprocess.DEVNULL)
        try:
            d = json.loads(r.stdout)
        except ValueError:
            d = {"total_cost_usd": 0, "result": (r.stdout + r.stderr)[-300:]}
        spent += d.get("total_cost_usd") or 0
        note(event="worker", run=run, phase=phase, model=a.model, usd=d.get("total_cost_usd"), turns=d.get("num_turns"),
             result=(d.get("result") or "")[-300:])
    note(event="max-runs", spent=round(spent, 4)); return 1


def self_check():
    crash = ("[x] FATAL\nCaused by: java.lang.RuntimeException: wrapper\n\tat net.neoforged.fml.X.y(X.java:1)\n"
             "Caused by: java.lang.IllegalArgumentException: class a.b.H has no @SubscribeEvent methods\n"
             "\tat net.neoforged.bus.EventBus.register(EventBus.java:1)\n\tat a.b.Mod.<init>(Mod.java:9)\n")
    k, text, _sig = failure_of(crash)
    ok = (k == "crash" and text.startswith("Caused by: java.lang.IllegalArgumentException") and "a.b.Mod.<init>" in text
          and "net.neoforged" not in text and failure_of("All 6 required tests passed :)") is None)
    ok = ok and failure_of("x\nMY_MOD_BOOT_TEST: PASS mode=spawn\n", client=True) is None
    k2, t2, _s = failure_of("MY_MOD_BOOT_TEST: spawning 4\nMY_MOD_BOOT_TEST: FAIL — 1 entity crashed\n", client=True)
    ok = ok and k2 == "gatec" and "FAIL" in t2
    k3, _t, _s = failure_of("Caused by: java.lang.NoClassDefFoundError: x\n\tat a.b.C.d(C.java:1)\n", client=True)
    ok = ok and k3 == "crash"
    k4, t4, s4 = failure_of("[main/ERROR]: oops\njava.lang.IllegalStateException: Registry is already frozen (trying to add key x)\n"
                            "\tat net.minecraft.core.MappedRegistry.validateWrite(MappedRegistry.java:1)\n\tat a.b.Stats.init(Stats.java:9)\n")
    ok = ok and k4 == "crash" and "already frozen" in t4 and "a.b.Stats.init" in t4
    print("self-check:", "OK" if ok else f"FAIL {k} {text!r}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
