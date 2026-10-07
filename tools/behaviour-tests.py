#!/usr/bin/env python3
"""Write and run a port's BEHAVIOUR tests: one model request, then the GameTest server decides.

    python3 tools/behaviour-tests.py --work <gradle project> [--model sonnet] [--out behaviour-tests.json]

Gates A-C prove a port compiles, loads and does not crash. None of them asks whether the mod still DOES
what it is for: a shield that no longer reduces damage, a food that no longer heals, a block whose
right-click does nothing all pass every gate. Those checks are mod-specific, so a template cannot hold
them; this writes them.

1. Script, no model: pick the mod's behaviour code (classes overriding use/useOn/hurtEnemy/
   finishUsingItem/applyEffectTick/mobInteract/useItemOn/... and the registration classes), its existing
   GameTest (for the structure name and holder annotations) and its display names.
2. One Sonnet request writes <package>.test.BehaviourGameTest: 4-10 GameTests, each asserting an OUTCOME
   the source clearly intends, all `required = false`, so a failure is reported, never a red Gate B.
3. Compile; if the new file does not compile, up to two single-shot repairs (tools/singleshot.py), and if
   it still does not, the file is removed and that is reported.
4. runGameTestServer. A failing behaviour test is a FINDING -- either the port lost the behaviour or the
   test misread the source -- for a person or a fix worker to judge. It is never auto-fixed here.

Exits 0 when every behaviour test passed, 1 when there are findings, 2 when no tests could be run.
Standard library only; needs the `claude` CLI.
"""
import argparse, importlib.util, json, os, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(name, file):
    s = importlib.util.spec_from_file_location(name, ROOT / file)
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
    return m


fl = _load("fl", "tools/file-loop.py")
gl = _load("gl", "tools/gate-loop.py")
sys.path.insert(0, str(ROOT / "tools"))
import singleshot  # noqa: E402

BEHAVIOUR = re.compile(r'\b(?:public|protected)\s+[\w<>\[\], ?]+\s+(use|useOn|useItemOn|useWithoutItem|finishUsingItem|'
                       r'hurtEnemy|postHurtEnemy|onHitEntity|onHitBlock|applyEffectTick|mobInteract|interact|'
                       r'entityInside|stepOn|fallOn|releaseUsing|onUseTick|inventoryTick|isDamageSourceBlocked|'
                       r'canPerformAction|getDestroySpeed|isCorrectToolForDrops|onBlocked|hurt|actuallyHurt|'
                       r'causeFallDamage|die|dropCustomDeathLoot|randomTick|tick|aiStep)\s*\(')
SYSTEM = ("You write Minecraft NeoForge GameTests for a mod that was just ported to a new version. You answer "
          "with one complete Java file and the requested notes, nothing else.")
PROMPT = """The mod `{modid}` was ported to {target}. It compiles, loads and its smoke tests pass. Write
GameTests that check it still DOES what its code says it does.

Write the class `{package}.test.BehaviourGameTest`:
- annotate it exactly like the existing test below: the same @GameTestHolder / @PrefixGameTestTemplate, and
  every test uses the same `template = "..."` value as the existing test;
- 4 to 10 `@GameTest(template = "...", required = false)` static methods taking GameTestHelper;
- each test sets up a situation, performs ONE behaviour from the mod's own code (use an item, hit with a
  weapon, eat a food, block with a shield, step on or right-click a block, apply an effect, let a mob tick)
  and asserts the OUTCOME the code intends (health went up, damage was reduced, an effect is present, a
  block or item changed), with a message that says what the mod should do;
- only behaviour the source below clearly implements; nothing that needs a client, rendering or sound;
  nothing that depends on chance unless you remove the chance (fixed values, enough attempts);
- use only the mod's classes shown here and vanilla/NeoForge {target} API: GameTestHelper#makeMockPlayer(GameType),
  spawn, spawnWithNoFreeWill, setBlock, getBlockState, useBlock, absolutePos, runAfterDelay, succeedWhen,
  assertTrue, assertEntityPresent, succeed, fail. Prefer simple direct calls (item.use(level, player, hand),
  entity.hurt(source, amount), stack.finishUsingItem(level, player)) over elaborate setups;
- end every test with helper.succeed() (or succeedWhen for something that takes ticks).

EXISTING TEST (copy its annotations, template name and style):
{existing}

WHAT THE MOD CONTAINS (display names):
{names}

THE MOD'S BEHAVIOUR CODE:
{sources}

Answer with the file in one ```java block, then one line per test:
INTENT: <method name> -- <the behaviour, and the file and method it comes from>"""


def modinfo(work):
    p = fl_props = {}
    for l in (work / "gradle.properties").read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r'\s*([\w.]+)\s*=\s*(.*?)\s*$', l)
        if m:
            fl_props[m.group(1)] = m.group(2)
    return fl_props.get("mod_id"), fl_props.get("mod_group_id")


def existing_test(work):
    """The port's own GameTest (not the client harness, not ours)."""
    for f in sorted((work / "src/main/java").rglob("*.java")):
        t = f.read_text(encoding="utf-8", errors="replace")
        if "@GameTest" in t and f.name != "BehaviourGameTest.java":
            return f, t
    return None, None


def pick_sources(work, cap=110000):
    src = work / "src/main/java"
    scored = []
    for f in src.rglob("*.java"):
        if "/test/" in f.as_posix():
            continue
        t = f.read_text(encoding="utf-8", errors="replace")
        n = len(BEHAVIOUR.findall(t))
        reg = "DeferredRegister" in t or "DeferredHolder" in t
        if n or reg:
            scored.append((0 if reg else 1, -n, len(t), f, t))
    scored.sort(key=lambda x: x[:3])
    out, total, used = [], 0, []
    for reg, _n, _l, f, t in scored:
        body = t if len(t) < 12000 else t[:12000] + "\n// ... (truncated)\n"
        block = f"// FILE {f.relative_to(work).as_posix()}\n{body}\n"
        if total + len(block) > cap:
            continue
        out.append(block); used.append(f.relative_to(work).as_posix()); total += len(block)
    return "\n".join(out), used


def java_block(answer):
    m = re.search(r'```java\s*\n(.*?)\n```', answer, re.S)
    return (m.group(1) + "\n") if m else None


def results(log):
    """-> ({test name: failure message}, did the run finish). Vanilla 1.21.1 logs only failures
    (LogTestReporter: "<name> failed at <pos>! <error>", optional ones "(optional) <name> failed at <pos>. ..."),
    so a declared test with no failure line passed -- but only if the server reached its summary line."""
    fails = {}
    for m in re.finditer(r'(\S*behaviourgametest\.\w+) failed at [^!\n]*?[!.]\s(.*)', log, re.I):
        fails[m.group(1).split(".")[-1].lower()] = m.group(2).strip()[:300]
    return fails, bool(re.search(r'required tests (passed|failed)', log))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work"); ap.add_argument("--model", default="sonnet", choices=list(fl.MODELS))
    ap.add_argument("--target", default="NeoForge 1.21.1"); ap.add_argument("--heap", default="6g")
    ap.add_argument("--thinking", type=int, default=8000); ap.add_argument("--out")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not a.work:
        ap.error("--work is required")
    work = pathlib.Path(a.work).resolve()
    modid, group = modinfo(work)
    out_path = pathlib.Path(a.out) if a.out else work / "behaviour-tests.json"
    report = {"modid": modid, "usd": 0.0, "tests": {}, "findings": []}
    ex_file, ex_text = existing_test(work)
    if not ex_file:
        report["skipped"] = "the port has no GameTest to copy the template and holder from"
        out_path.write_text(json.dumps(report, indent=1), encoding="utf-8"); print("behaviour-tests:", report["skipped"]); return 2
    package = re.search(r'^package\s+([\w.]+)\s*;', ex_text, re.M).group(1).rsplit(".test", 1)[0]
    sources, used = pick_sources(work)
    vr = _load("vr", "tools/visual-review.py")
    prompt = PROMPT.format(modid=modid, target=a.target, package=package, existing=ex_text[:9000],
                           names=vr.names(work, modid), sources=sources)
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD"}
    env["MAX_THINKING_TOKENS"] = str(a.thinking)
    r = subprocess.run(["claude", "-p", "--model", fl.MODELS[a.model], "--system-prompt", SYSTEM, "--tools", "",
                        "--output-format", "json"], input=prompt, cwd=work, env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=900)
    try:
        d = json.loads(r.stdout)
    except ValueError:
        d = {"result": (r.stdout + r.stderr)[-400:], "total_cost_usd": 0}
    report["usd"] += d.get("total_cost_usd") or 0
    report["sources"] = used
    answer = d.get("result") or ""
    report["intent"] = re.findall(r'^INTENT:\s*(.+)$', answer, re.M)
    code = java_block(answer)
    if not code:
        report["skipped"] = "the answer had no java block"
        out_path.write_text(json.dumps(report, indent=1), encoding="utf-8"); print("behaviour-tests:", report["skipped"]); return 2
    target = work / "src/main/java" / pathlib.Path(*package.split(".")) / "test/BehaviourGameTest.java"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(code, encoding="utf-8")
    clog = work / "behaviour-compile.log"
    idx = singleshot.SourceIndex(fl.find_sources(work)) if fl.find_sources(work) else None
    for attempt in range(3):
        n, errs = fl.compile_(work, clog, a.heap)
        if n is None:   # a parse abort (most likely this file): nothing countable to repair from
            mine = [(1, e) for e in errs]
        else:
            mine = [(l, m) for f, l, m in (errs or []) if pathlib.Path(f).name == "BehaviourGameTest.java"]
        if n == 0 or (n and not mine):
            break
        if attempt == 2 or idx is None or n is None:
            target.unlink()
            report["skipped"] = f"the generated tests did not compile after {attempt} repair(s); removed"
            report["compile_errors"] = [m for _l, m in mine][:10]
            out_path.write_text(json.dumps(report, indent=1), encoding="utf-8"); print("behaviour-tests:", report["skipped"]); return 2
        fix = singleshot.run_single(work, target, mine, "(a generated GameTest; fix it to compile, keep every test)",
                                    idx, fl.MODELS["sonnet"], a.target, thinking=4000)
        report["usd"] += fix["usd"]
        report.setdefault("repairs", []).append({"applied": fix["applied"], "usd": fix["usd"]})
    if n:
        target.unlink()
        report["skipped"] = f"the port itself does not compile ({n} errors elsewhere); tests removed"
        out_path.write_text(json.dumps(report, indent=1), encoding="utf-8"); print("behaviour-tests:", report["skipped"]); return 2
    log = gl.run_gate(work, "runGameTestServer", a.heap, work / "behaviour-gametest.log")
    fails, finished = results(log)
    declared = re.findall(r'@GameTest\b[^\n]*\n(?:\s*@\w+[^\n]*\n)*\s*public\s+static\s+void\s+(\w+)',
                          target.read_text(encoding="utf-8"))
    if not finished:
        crash = gl.failure_of(log)
        report["skipped"] = "the GameTest server did not finish: " + (crash[1][:400] if crash else "no summary line")
        target.unlink()
        out_path.write_text(json.dumps(report, indent=1), encoding="utf-8"); print("behaviour-tests:", report["skipped"]); return 2
    for name in declared:
        msg = fails.get(name.lower())
        report["tests"][name] = {"passed": msg is None, "message": msg or ""}
        if msg is not None:
            report["findings"].append({"test": name, "why": msg})
    report["gate_b_still_green"] = bool(gl.PASS.search(log))
    out_path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"behaviour-tests: {len(declared)} test(s), {len(report['findings'])} finding(s), ${report['usd']:.4f}"
          + "".join(f"\n  {f['test']}: {f['why']}" for f in report["findings"]))
    return 1 if report["findings"] else (0 if declared else 2)


def self_check():
    log = ("[x] [Server thread/WARN] [minecraft/LogTestReporter]: (optional) behaviourgametest.shieldblocks failed at "
           "-1,-60,0. Expected damage below 4.0, got 4.0\n[x] All 7 required tests passed :)\n")
    fails, done = results(log)
    ok = done and "Expected damage" in fails.get("shieldblocks", "") and "foodheals" not in fails
    ok &= results("Caused by: x\n")[1] is False
    ok &= java_block("x\n```java\nclass A {}\n```\nINTENT: a -- b") == "class A {}\n"
    ok &= BEHAVIOUR.search("   public InteractionResultHolder<ItemStack> use(Level l, Player p, InteractionHand h) {") is not None
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
