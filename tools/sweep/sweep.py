#!/usr/bin/env python3
"""Zero-model sweep: run tools/port.py up to the end of hop 1's mechanical stage for each mod (no model, no worker),
keep the evidence, delete the workspace, and write one summary row per mod (docs/POPULARITY_SWEEP.md).

    python3 tools/sweep/sweep.py <out-dir> "Mod Name|1.21.1" "Other Mod|26.2" ...

A mod whose <out-dir>/results/<slug>/summary.json exists is skipped, so an interrupted run resumes by re-running
the same command. Mods run SERIALLY: each one stops leftover JVMs before it starts. SWEEP_KEEP=<slug>,... keeps
those workspaces. Prints one line per mod: `<name> -> <target>: exit=N start=E left=L`. Standard library only.
"""
import os, json, pathlib, re, shutil, subprocess, sys, time

MIG = pathlib.Path(__file__).resolve().parents[2]
if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
    sys.exit(__doc__)
out = pathlib.Path(sys.argv[1]).resolve(); out.mkdir(parents=True, exist_ok=True)
ports = out / "ports"; jars = out / "jars"


def buckets(log):
    if not log or not log.exists():
        return {}
    t = log.read_text(encoding="utf-8", errors="replace")
    lines = sorted(set(re.findall(r"(/[^\s:]+\.java:\d+): error: (.*)", t)))
    msg = {}
    for _, m in lines:
        k = re.sub(r"(symbol|class|variable|method) .*", r"\1", m)[:90]
        msg[k] = msg.get(k, 0) + 1
    sym = {}
    for s in re.findall(r"symbol: +((?:class|variable|method|package) \w+)", t):
        sym[s] = sym.get(s, 0) + 1
    pk = {}
    for s in re.findall(r"package ([\w.]+) does not exist", t):
        pk[s] = pk.get(s, 0) + 1
    top = lambda d: dict(sorted(d.items(), key=lambda x: -x[1])[:12])
    return {"messages": top(msg), "symbols": top(sym), "missing_packages": top(pk)}


for spec in sys.argv[2:]:
    name, target = spec.rsplit("|", 1)
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    res = out / "results" / slug
    if (res / "summary.json").exists():
        print(f"skip {name} (done)"); continue
    res.mkdir(parents=True, exist_ok=True)
    subprocess.run(["pkill", "-x", "java"], capture_output=True)   # serial sweep: any JVM now is a leftover
    if shutil.disk_usage("/").free < 6 * 2**30:        # between mods only: nothing is building now
        shutil.rmtree(pathlib.Path.home() / ".gradle/caches/build-cache-1", ignore_errors=True)
        now = time.time()
        for d in list((pathlib.Path.home() / ".gradle/caches/ng_execute").glob("*")) + \
                 list((pathlib.Path.home() / ".gradle/caches/neoformruntime/intermediate_results").glob("*")):
            if now - d.stat().st_mtime > 600 and sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) < 500 * 2**20:
                shutil.rmtree(d, ignore_errors=True) if d.is_dir() else d.unlink(missing_ok=True)
        for l in (pathlib.Path.home() / ".gradle/daemon").rglob("*.log"):
            l.unlink(missing_ok=True)
    t0 = time.time()
    r = subprocess.run([sys.executable, str(MIG / "tools/port.py"), name, "--to", target, "--mods-dir", str(ports),
                        "--jars-dir", str(jars), "--budget", "0", "--gate-budget", "0", "--gatec-budget", "0",
                        "--stop-after", "hop1-mechanical"], cwd=MIG, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=5400)
    text = r.stdout + r.stderr
    (res / "port.log").write_text(text, encoding="utf-8")
    m = re.search(r"port: (\w+): \w+ [\d.]+ -> NeoForge", text)
    modid = m.group(1) if m else None
    work = ports / modid if modid else None
    row = {"name": name, "target": target, "exit": r.returncode, "seconds": round(time.time() - t0),
           "modid": modid}
    stop = re.search(r"STOPPED: (.*)", text)
    row["stopped"] = stop.group(1) if stop else None
    me = re.findall(r"mechanical-hop: (\d+) -> (\d+) errors before any worker", text)
    row["errors_start"], row["errors_left"] = (int(me[-1][0]), int(me[-1][1])) if me else (None, None)
    for k, pat in (("deps", r"port: dependencies: (.*)"), ("setup", r"port: setup done: (.*)"),
                   ("source", r"port: source: (.*)")):
        mm = re.search(pat, text)
        row[k] = mm.group(1) if mm else None
    if work and work.exists():
        logs = sorted((work / "mechanical-hop").glob("*.log"), key=lambda p: p.stat().st_mtime)
        logs = [l for l in logs if "error:" in l.read_text(encoding="utf-8", errors="replace")] or logs
        row["buckets"] = buckets(logs[-1] if logs else None)
        for keep in ("MIGRATION.md", "setup.log", "port-state.json", "mechanical-hop1.json"):
            if (work / keep).exists():
                shutil.copy(work / keep, res / keep)
        for pat in ("recipes-report-*.json", "mechanical-*.json", "era-renames.log", "era-rule-hits-*.json"):
            for kf in work.glob(pat):
                shutil.copy(kf, res / kf.name)
        if (work / "mechanical-hop").exists():
            shutil.copytree(work / "mechanical-hop", res / "mechanical-hop", dirs_exist_ok=True)
        if slug not in os.environ.get("SWEEP_KEEP", "").split(","):
            shutil.rmtree(work)
    (res / "summary.json").write_text(json.dumps(row, indent=1) + "\n", encoding="utf-8")
    print(f"{name} -> {target}: exit={row['exit']} start={row['errors_start']} left={row['errors_left']} "
          f"{'STOP: ' + row['stopped'][:120] if row['stopped'] else ''} ({row['seconds']}s)", flush=True)
print("ALL-DONE")
