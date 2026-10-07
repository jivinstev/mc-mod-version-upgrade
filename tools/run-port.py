#!/usr/bin/env python3
"""Run one hop of a port in one command: recipes -> compile loop -> Gate B -> behaviour tests -> Gate C -> visual review.

    python3 tools/run-port.py --work mods/<modid> [--pack <recipes.tsv> | --pack none] [--tag hop1]
                              [--budget 20] [--gate-budget 5]
                              [--behaviour write|rerun|off] [--gatec launch,spawn,battle,gauntlet | --gatec none]
                              [--gatec-budget 3] [--no-visual-review]

tools/port.py calls this once per hop; it is also the command to rerun a hop by hand. The orchestrating
session should not grow while a port runs: it starts this, then reads ONE summary (stdout's last lines and
run-port[-<tag>].json), never the workers' transcripts or the build logs.

Stages, each skipped with a reason when it does not apply:
  1. recipes   tools/apply-recipes.py with --pack
  2. compile   tools/file-loop.py (single-shot workers first; scans and the override probe at 0 errors)
  3. gate B    tools/gate-loop.py, when the compile reached 0 and the project has a gameTestServer run
  4. behaviour tools/behaviour-tests.py after a green Gate B. `write` (default): one Sonnet request writes
               mod-specific outcome GameTests and the server runs them. `rerun`: no model, run the ones an
               earlier hop wrote (they were ported with the mod). A failure is a finding, never a red gate.
  5. gate C    tools/scaffold-gatec.py (when the port has no client harness), then gate-loop.py --gatec;
               a real client per phase, under Xvfb on a headless Linux box
  6. visual    tools/visual-review.py over the frames Gate C saved: script checks, then one Haiku request
Steps 4 and 6 only report; --behaviour off / --no-visual-review skip them for the lowest cost (their
ground then falls to MANUAL_VALIDATION.md).

THE STOP CONTRACT. When a stage cannot finish (compile errors left at the budget or a plateau, Gate B or
Gate C still red), this does NOT hand the rest to anyone silently. It prints a STOPPED block: what is left
(error groups, or the failing test / crash), what was spent, a rough cost to continue with workers, and
the choices. Exit codes: 0 done; 10 stopped in the compile; 11 Gate B red; 12 Gate C red; 2 bad input.
Standard library only.
"""
import argparse, json, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def last_event(log, kind):
    out = None
    if pathlib.Path(log).exists():
        for l in open(log, encoding="utf-8"):
            e = json.loads(l)
            if e.get("event") == kind:
                out = e
    return out


def spend(log, since_start=True):
    """Worker dollars in a loop log; with since_start, only since its LAST start event (a resumed loop
    appends to the same log, and the earlier run's dollars belong to the earlier summary)."""
    total, by = 0.0, {}
    if pathlib.Path(log).exists():
        lines = [json.loads(l) for l in open(log, encoding="utf-8")]
        if since_start:
            starts = [i for i, e in enumerate(lines) if e.get("event") == "start"]
            lines = lines[starts[-1]:] if starts else lines
        for e in lines:
            if e.get("event") == "worker" and e.get("usd"):
                total += e["usd"]; m = e.get("model", "?"); by[m] = round(by.get(m, 0) + e["usd"], 4)
    return round(total, 4), by


def continue_estimate(start, end, usd):
    """Rough dollars to finish the compile with workers: this run's dollars per error removed, times
    what is left, times 1.5 because the residue is the hard part. None when nothing was removed."""
    if not start or end is None or start <= end or usd <= 0:
        return None
    return round(usd / (start - end) * end * 1.5, 2)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work"); ap.add_argument("--pack"); ap.add_argument("--tag", default="")
    ap.add_argument("--budget", type=float, default=20.0); ap.add_argument("--gate-budget", type=float, default=5.0)
    ap.add_argument("--first-model", default="haiku"); ap.add_argument("--mode", default="single")
    ap.add_argument("--target", default="NeoForge 1.21.1"); ap.add_argument("--heap", default="6g")
    ap.add_argument("--gatec", default="launch,spawn,battle,gauntlet", help="Gate C phases, or none")
    ap.add_argument("--gatec-budget", type=float, default=3.0)
    ap.add_argument("--behaviour", default="write", choices=["write", "rerun", "off"])
    ap.add_argument("--no-behaviour", action="store_true", help="same as --behaviour off")
    ap.add_argument("--no-visual-review", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not a.work:
        ap.error("--work is required")
    if a.no_behaviour:
        a.behaviour = "off"
    work = pathlib.Path(a.work).resolve()
    sfx = f"-{a.tag}" if a.tag else ""
    s = {"work": str(work), "tag": a.tag, "target": a.target, "stages": {}}
    py = sys.executable
    if a.pack and a.pack != "none":
        r = subprocess.run([py, str(ROOT / "tools/apply-recipes.py"), "--src", str(work / "src/main/java"),
                            "--recipes", a.pack, "--json", str(work / f"recipes-report{sfx}.json")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        (work / f"recipes-report{sfx}.txt").write_text(r.stdout + r.stderr, encoding="utf-8")
        s["stages"]["recipes"] = {"exit": r.returncode, "summary": (r.stdout.splitlines() or [""])[:2]}
    else:
        s["stages"]["recipes"] = {"skipped": "no pack for this hop (or it was applied before this run)"}
    flog = work / f"file-loop{sfx}.jsonl"
    r = subprocess.run([py, str(ROOT / "tools/file-loop.py"), "--work", str(work), "--budget", str(a.budget),
                        "--first-model", a.first_model, "--mode", a.mode, "--target", a.target, "--heap", a.heap,
                        "--log", str(flog)], stdout=open(work / f"file-loop{sfx}.out", "w", encoding="utf-8"),
                       stderr=subprocess.STDOUT)
    start, end = last_event(flog, "start"), last_event(flog, "end")
    usd, by = spend(flog)
    end_errors = end.get("errors") if end else None
    stubs = (last_event(flog, "stub-signals") or {}).get("files", []) if end and end.get("stubs") else []
    s["stages"]["compile"] = {"exit": r.returncode, "start_errors": start and start.get("errors"),
                              "end_errors": end_errors, "usd": usd, "by_model": by, "stubs": stubs}
    stop = None
    if end_errors != 0:
        summ = subprocess.run([py, str(ROOT / "tools/compile-summary.py"), str(work / "file-loop-compile.log")],
                              capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
        est = continue_estimate(start and start.get("errors"), end_errors, usd)
        stop = {"stage": "compile", "code": 10,
                "left": f"{end_errors} compile errors" if end_errors is not None else "the compile does not count (see file-loop.out)",
                "spent": usd, "estimate_to_continue": est, "detail": summ.strip().splitlines()[:14],
                "choices": [f"continue with workers: rerun this command with a higher --budget (about ${est} more)" if est
                            else "continue with workers: rerun with a higher --budget",
                            "fix the listed groups by hand in this session",
                            "leave chunks out: python3 tools/scope-menu.py (catalogue Step 3b)"]}
    gate_ok = None
    has_gate = "gameTestServer" in (work / "build.gradle").read_text(encoding="utf-8", errors="replace") \
        if (work / "build.gradle").exists() else False
    if end_errors == 0 and has_gate:
        glog = work / f"gate-loop{sfx}.jsonl"
        r = subprocess.run([py, str(ROOT / "tools/gate-loop.py"), "--work", str(work), "--budget", str(a.gate_budget),
                            "--target", a.target, "--heap", a.heap, "--log", str(glog)],
                           stdout=open(work / f"gate-loop{sfx}.out", "w", encoding="utf-8"), stderr=subprocess.STDOUT)
        gusd, gby = spend(glog)
        gate_ok = r.returncode == 0
        last = (last_event(glog, "green") if gate_ok else (last_event(glog, "stuck") or last_event(glog, "budget")
                or last_event(glog, "max-runs") or last_event(glog, "red")))
        s["stages"]["gate_b"] = {"green": gate_ok, "usd": gusd, "by_model": gby, "last": last}
        if not gate_ok:
            stop = {"stage": "gate B", "code": 11, "left": (last or {}).get("sig", "see gate-loop.log"), "spent": gusd,
                    "choices": ["rerun with a higher --gate-budget", "fix the failure by hand in this session",
                                "read the catalogue's §R for the crash signature"]}
    else:
        s["stages"]["gate_b"] = {"skipped": "compile not at 0" if end_errors != 0 else "no gameTestServer run in build.gradle"}

    def finding_stage(tool, enabled, why_not, extra=()):
        if not enabled:
            return {"skipped": why_not}
        r = subprocess.run([py, str(ROOT / "tools" / tool), "--work", str(work), "--target", a.target, *extra],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        rep_ = work / (tool.replace(".py", ".json"))
        d = json.loads(rep_.read_text(encoding="utf-8")) if rep_.exists() else {}
        return {"exit": r.returncode, "usd": round(d.get("usd") or 0, 4) if not extra else 0.0,
                "findings": d.get("findings", []), "skipped_reason": d.get("skipped"),
                "summary": (r.stdout.strip().splitlines() or [""])[0]}
    s["stages"]["behaviour"] = finding_stage("behaviour-tests.py", gate_ok and a.behaviour != "off",
                                             "--behaviour off" if a.behaviour == "off" else "Gate B not green",
                                             ("--rerun",) if a.behaviour == "rerun" else ())
    gatec_ok = None
    if gate_ok and a.gatec != "none":
        sc = subprocess.run([py, str(ROOT / "tools/scaffold-gatec.py"), "--work", str(work)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
        clog = work / f"gatec-loop{sfx}.jsonl"
        r = subprocess.run([py, str(ROOT / "tools/gate-loop.py"), "--work", str(work), "--gatec", a.gatec,
                            "--budget", str(a.gatec_budget), "--max-runs", "10", "--target", a.target,
                            "--heap", a.heap, "--log", str(clog)],
                           stdout=open(work / f"gatec-loop{sfx}.out", "w", encoding="utf-8"), stderr=subprocess.STDOUT)
        cusd, cby = spend(clog)
        gatec_ok = r.returncode == 0
        last = (last_event(clog, "stuck") or last_event(clog, "budget") or last_event(clog, "max-runs")
                or last_event(clog, "green"))
        s["stages"]["gate_c"] = {"green": gatec_ok, "usd": cusd, "by_model": cby, "scaffold": sc.stdout.strip()[-200:],
                                 "last": last}
        if not gatec_ok:
            stop = {"stage": "gate C", "code": 12, "left": (last or {}).get("sig") or (last or {}).get("phase", "?"),
                    "spent": cusd, "choices": ["rerun with a higher --gatec-budget", "fix the failing phase by hand",
                                               "run the client yourself: tools/client-boot-loop.sh in the port"]}
    else:
        s["stages"]["gate_c"] = {"skipped": "--gatec none" if a.gatec == "none" else "Gate B not green"}
    s["stages"]["visual"] = finding_stage("visual-review.py", bool(gatec_ok) and not a.no_visual_review,
                                          "--no-visual-review" if a.no_visual_review else "Gate C not run or not green")
    total = round(sum(st.get("usd", 0) for st in s["stages"].values()), 4)
    s["usd"] = total
    s["done"] = stop is None and end_errors == 0 and bool(gate_ok or not has_gate)
    s["stop"] = stop
    (work / f"run-port{sfx}.json").write_text(json.dumps(s, indent=1), encoding="utf-8")
    c, g, gc = s["stages"]["compile"], s["stages"]["gate_b"], s["stages"]["gate_c"]
    print(f"run-port{(' ' + a.tag) if a.tag else ''}: compile {c['start_errors']} -> {c['end_errors']} errors, Gate B "
          f"{'green' if gate_ok else ('red' if gate_ok is False else 'not run: ' + g.get('skipped', ''))}, Gate C "
          f"{'green' if gatec_ok else ('red' if gatec_ok is False else 'not run: ' + gc.get('skipped', ''))}, "
          f"findings: behaviour {len(s['stages']['behaviour'].get('findings', []))}, "
          f"visual {len(s['stages']['visual'].get('findings', []))}, "
          f"${total} of workers -- details in {work / f'run-port{sfx}.json'}")
    if stubs:
        # not a stop -- the gates may be right that nothing crashes -- but never a silent one either
        print(f"STUB WARNINGS: the compile loop removed wiring or emptied methods in {len(stubs)} file(s); "
              "check each is a real port, not a feature deleted to make an error go away:")
        for x in stubs[:12]:
            print(f"  - {x['file']}: {x['why']}")
    if stop:
        print(stop_block(stop))
        return stop["code"]
    return 0


def stop_block(stop):
    lines = [f"STOPPED at {stop['stage']}: {stop['left']} (spent ${stop.get('spent', 0)} here)."]
    lines += [f"  {d}" for d in stop.get("detail", [])]
    lines.append("Choices (ask the person; do not pick one silently):")
    lines += [f"  - {c}" for c in stop["choices"]]
    return "\n".join(lines)


def self_check():
    ok = continue_estimate(468, 2, 4.66) is not None and abs(continue_estimate(468, 2, 4.66) - 0.03) < 0.02
    ok &= continue_estimate(10, 10, 1.0) is None and continue_estimate(None, 3, 1.0) is None
    b = stop_block({"stage": "compile", "left": "12 compile errors", "spent": 3.1, "detail": ["x"],
                    "choices": ["a", "b"]})
    ok &= b.startswith("STOPPED at compile: 12 compile errors") and "do not pick one silently" in b
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        log = pathlib.Path(d) / "l.jsonl"
        log.write_text("\n".join(json.dumps(e) for e in [
            {"event": "start"}, {"event": "worker", "usd": 1.0, "model": "haiku"},
            {"event": "start"}, {"event": "worker", "usd": 0.25, "model": "sonnet"}]) + "\n", encoding="utf-8")
        ok &= spend(log) == (0.25, {"sonnet": 0.25}) and spend(log, since_start=False)[0] == 1.25
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
