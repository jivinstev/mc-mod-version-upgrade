#!/usr/bin/env python3
"""Run the deterministic middle of a port in one command: recipes -> file loop -> Gate B loop -> Gate C loop.

    python3 tools/run-port.py --work mods/<modid> [--pack <recipes.tsv>] [--budget 20] [--gate-budget 5]
                              [--gatec launch,spawn,battle,gauntlet | --gatec none] [--gatec-budget 3]

Why (issue #27, plan step 3): the orchestrating session should not grow while a port runs. It starts
this, then reads ONE summary (stdout's last line, and run-port.json), never the workers' transcripts or
the build logs. Each stage's own log stays on disk for when something needs a human.

Stages, each skipped with a reason when it does not apply:
  1. recipes   tools/apply-recipes.py with --pack (or none)
  2. compile   tools/file-loop.py (single-shot workers first; scans and the override probe at 0 errors)
  3. gate B    tools/gate-loop.py, only when the compile reached 0 and the project has a gameTestServer run
  4. gate C    tools/scaffold-gatec.py (when the port has no client harness), then gate-loop.py --gatec,
               only after Gate B is green; a real client per phase, under Xvfb on a headless Linux box
The dollar caps are per stage; the summary adds them up with the per-model split.
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


def spend(log):
    total, by = 0.0, {}
    if pathlib.Path(log).exists():
        for l in open(log, encoding="utf-8"):
            e = json.loads(l)
            if e.get("event") == "worker" and e.get("usd"):
                total += e["usd"]; m = e.get("model", "?"); by[m] = round(by.get(m, 0) + e["usd"], 4)
    return round(total, 4), by


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work", required=True); ap.add_argument("--pack")
    ap.add_argument("--budget", type=float, default=20.0); ap.add_argument("--gate-budget", type=float, default=5.0)
    ap.add_argument("--first-model", default="haiku"); ap.add_argument("--mode", default="single")
    ap.add_argument("--target", default="NeoForge 1.21.1"); ap.add_argument("--heap", default="6g")
    ap.add_argument("--gatec", default="launch,spawn,battle,gauntlet", help="Gate C phases, or none")
    ap.add_argument("--gatec-budget", type=float, default=3.0)
    a = ap.parse_args()
    work = pathlib.Path(a.work).resolve()
    s = {"work": str(work), "stages": {}}
    py = sys.executable
    if a.pack:
        r = subprocess.run([py, str(ROOT / "tools/apply-recipes.py"), "--src", str(work / "src/main/java"),
                            "--recipes", a.pack, "--json", str(work / "recipes-report.json")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        (work / "recipes-report.txt").write_text(r.stdout + r.stderr, encoding="utf-8")
        s["stages"]["recipes"] = {"exit": r.returncode, "summary": (r.stdout.splitlines() or [""])[:2]}
    else:
        s["stages"]["recipes"] = {"skipped": "no --pack for this axis"}
    flog = work / "file-loop.jsonl"
    r = subprocess.run([py, str(ROOT / "tools/file-loop.py"), "--work", str(work), "--budget", str(a.budget),
                        "--first-model", a.first_model, "--mode", a.mode, "--target", a.target, "--heap", a.heap,
                        "--log", str(flog)], stdout=open(work / "file-loop.out", "w", encoding="utf-8"),
                       stderr=subprocess.STDOUT)
    start, end = last_event(flog, "start"), last_event(flog, "end")
    usd, by = spend(flog)
    s["stages"]["compile"] = {"exit": r.returncode, "start_errors": start and start.get("errors"),
                              "end_errors": end and end.get("errors"), "usd": usd, "by_model": by}
    gate_ok = None
    has_gate = "gameTestServer" in (work / "build.gradle").read_text(encoding="utf-8", errors="replace") \
        if (work / "build.gradle").exists() else False
    if end and end.get("errors") == 0 and has_gate:
        glog = work / "gate-loop.jsonl"
        r = subprocess.run([py, str(ROOT / "tools/gate-loop.py"), "--work", str(work), "--budget", str(a.gate_budget),
                            "--target", a.target, "--heap", a.heap, "--log", str(glog)],
                           stdout=open(work / "gate-loop.out", "w", encoding="utf-8"), stderr=subprocess.STDOUT)
        gusd, gby = spend(glog)
        gate_ok = r.returncode == 0
        s["stages"]["gate_b"] = {"green": gate_ok, "usd": gusd, "by_model": gby,
                                 "last": (last_event(glog, "green") or last_event(glog, "stuck")
                                          or last_event(glog, "budget") or last_event(glog, "max-runs"))}
    else:
        s["stages"]["gate_b"] = {"skipped": "compile not at 0" if not (end and end.get("errors") == 0)
                                 else "no gameTestServer run in build.gradle"}
    gatec_ok = None
    if gate_ok and a.gatec != "none":
        sc = subprocess.run([py, str(ROOT / "tools/scaffold-gatec.py"), "--work", str(work)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
        clog = work / "gatec-loop.jsonl"
        r = subprocess.run([py, str(ROOT / "tools/gate-loop.py"), "--work", str(work), "--gatec", a.gatec,
                            "--budget", str(a.gatec_budget), "--max-runs", "10", "--target", a.target,
                            "--heap", a.heap, "--log", str(clog)],
                           stdout=open(work / "gatec-loop.out", "w", encoding="utf-8"), stderr=subprocess.STDOUT)
        cusd, cby = spend(clog)
        gatec_ok = r.returncode == 0
        s["stages"]["gate_c"] = {"green": gatec_ok, "usd": cusd, "by_model": cby, "scaffold": sc.stdout.strip()[-200:],
                                 "last": (last_event(clog, "stuck") or last_event(clog, "budget")
                                          or last_event(clog, "max-runs") or last_event(clog, "green"))}
    else:
        s["stages"]["gate_c"] = {"skipped": "--gatec none" if a.gatec == "none" else "Gate B not green"}
    total = round(sum(st.get("usd", 0) for st in s["stages"].values()), 4)
    s["usd"] = total
    s["done"] = bool(end and end.get("errors") == 0 and gate_ok and (gatec_ok or a.gatec == "none"))
    (work / "run-port.json").write_text(json.dumps(s, indent=1), encoding="utf-8")
    c, g, gc = s["stages"]["compile"], s["stages"]["gate_b"], s["stages"]["gate_c"]
    print(f"run-port: compile {c['start_errors']} -> {c['end_errors']} errors, Gate B "
          f"{'green' if gate_ok else ('red' if gate_ok is False else 'not run: ' + g.get('skipped', ''))}, Gate C "
          f"{'green' if gatec_ok else ('red' if gatec_ok is False else 'not run: ' + gc.get('skipped', ''))}, "
          f"${total} of workers {json.dumps({**c['by_model'], **{('gate:' + k): v for k, v in g.get('by_model', {}).items()}})}"
          f" -- details in {work / 'run-port.json'}")
    return 0 if s["done"] else 1


if __name__ == "__main__":
    sys.exit(main())
