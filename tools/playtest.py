#!/usr/bin/env python3
"""Run a playtest -- a script of Minecraft commands and `@` directives -- in the REAL client of each installed
Minecraft version, grade every step, and write a report with key frames (and, optionally, a screen recording).

    python3 tools/playtest.py run SCRIPT.playtest [--plan PLAN] [--mc 1.21.1 ...] [--video] [--report DIR]
                                                  [--carrier-jar [MC=]JAR | --carrier-dir DIR] [--timeout 900]
    python3 tools/playtest.py lint SCRIPT.playtest [--jar JAR ...]   # syntax and directives; with --jar, every
                                                              # <ns>:<id> the script names must exist in the jars
    python3 tools/playtest.py inventory --jar JAR [--jar JAR ...] [--json]   # what the mod registers: entities,
                                                              # items, sounds, subtitles and other lang keys, data files
    python3 tools/playtest.py explain RUN_DIR [--mc MC]       # one failed run, compact, for playtest-debug
    python3 tools/playtest.py --self-check

The engine is the Gate C carrier (templates/smoke-harness, mod id smokeharness, built per version by
.github/workflows/gatec-carrier.yml) in its `playtest` mode: `-Dsmokeharness.testmode=playtest
-Dsmokeharness.playtest=<script> -Dsmokeharness.playtest.out=<dir>`. The instances are tools/real-install.py's
(`plan` + `install --with-carrier`): one game directory per Minecraft version, never the player's own. Each version
runs alone, one after another, through real-install's launch (the Launcher's command, the version's Java).

Script format: tools/playtest-lint.py's docstring is the grammar (the carrier runs it; `lint` here checks it with
playtest-lint's checker, then adds what needs the mod: --jar ids, --forbid ids, a missing @intent). In short:
Minecraft commands, one per line, plus @intent @step @assert @wait @aim @key @click @expect @shot @allow @world
@continue-on-fail @end.
Output per version: <report>/<mc>/ with stdout/stderr/latest.log, playtest-result.json and the frames; then
<report>/report.md and report.html (frames embedded by relative path) for the run. Standard library only; macOS
recordings use the system's `screencapture`.
"""
import argparse, datetime, html, importlib.util, json, os, pathlib, re, shutil, signal, subprocess, sys, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
RI_PATH = ROOT / "tools" / "real-install.py"
STEP_LINE = re.compile(r"PLAYTEST: STEP (\d+) (.*?): (PASS|FAIL|SKIPPED)\b(?:\s*--\s*(.*))?")
VERDICT_LINE = re.compile(r"PLAYTEST: (PASS|FAIL)\b\s*--\s*(.*)")


def ri():
    spec = importlib.util.spec_from_file_location("real_install", RI_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ── the script ───────────────────────────────────────────────────────────────────────────────────────────────────
def grammar():
    """tools/playtest-lint.py: the carrier's grammar, checked without Minecraft (one grammar, one checker)."""
    spec = importlib.util.spec_from_file_location("playtest_lint", ROOT / "tools" / "playtest-lint.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def parse(text):
    """-> {"intent", "steps": [{"n", "name", "lines": [(lineno, text)]}], "problems": [(lineno, msg)]}, the steps
    numbered as the carrier numbers them (1..n; lines before any @step form step "main"). The syntax problems are
    playtest-lint's; a script with no @intent is a problem here too (playtest-debug judges a failure by it)."""
    out = {"intent": "", "steps": [], "problems": list(grammar().lint(text)[0])}
    cur = None
    for no, raw in enumerate(text.lstrip("\ufeff").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        word, _, rest = line[1:].partition(" ") if line.startswith("@") else ("", "", "")
        if word == "intent" and cur is None:
            out["intent"] = rest.strip()
        elif word == "step":
            cur = {"n": len(out["steps"]) + 1, "name": rest.strip(), "lines": []}
            out["steps"].append(cur)
        elif word == "end":
            break
        elif word in ("world", "continue-on-fail") or (word == "allow" and cur is None):
            continue
        else:
            if cur is None:
                cur = {"n": len(out["steps"]) + 1, "name": "main", "lines": []}
                out["steps"].append(cur)
            cur["lines"].append((no, line[1:] if line.startswith("/") else line))
    if not out["intent"]:
        out["problems"].append((0, "no @intent: say what the script proves (playtest-debug judges a failure by it)"))
    return out


# ── what a mod registers (the vocabulary a script can name) ──────────────────────────────────────────────────────────
def inventory(jars):
    """From the jars alone (no decompiling): per namespace, the entity and item/block ids (from their lang keys), the
    sound events (sounds.json), every other lang key grouped by its first segment (subtitles, boss lines, chat
    messages, ...) and the data files by folder (loot tables, advancements, recipes, ...)."""
    import zipfile
    inv = {}
    for jar in jars:
        with zipfile.ZipFile(jar) as z:
            for n in z.namelist():
                parts = n.split("/")
                if len(parts) < 3 or parts[0] not in ("assets", "data") or n.endswith("/"):
                    continue
                ns = parts[1]
                if ns in ("minecraft", "c", "neoforge", "forge"):
                    continue
                d = inv.setdefault(ns, {"entities": set(), "items": set(), "blocks": set(), "sounds": set(),
                                        "other_ids": set(), "lang": {}, "data": {}})
                if parts[0] == "assets" and parts[2] == "sounds.json":
                    try:
                        d["sounds"] |= set(json.loads(z.read(n).decode("utf-8")))
                    except ValueError:
                        pass
                elif parts[0] == "assets" and parts[2] == "lang" and parts[-1] == "en_us.json":
                    try:
                        keys = json.loads(z.read(n).decode("utf-8"))
                    except ValueError:
                        continue
                    for k in keys:
                        seg = k.split(".")
                        if len(seg) >= 3 and seg[1] == ns and seg[0] in ("entity", "item", "block"):
                            d[{"entity": "entities", "item": "items", "block": "blocks"}[seg[0]]].add(seg[2])
                        else:
                            d["lang"].setdefault(seg[0], []).append(k)
                            if len(seg) == 3 and seg[1] == ns and seg[0] in ("attribute", "effect", "enchantment"):
                                d["other_ids"].add(seg[2])      # /attribute, /effect and /enchant take these ids
                elif parts[0] == "data" and len(parts) >= 4:
                    d["data"].setdefault(parts[2], []).append("/".join(parts[3:]).rsplit(".", 1)[0])
    return {ns: {k: sorted(v) if isinstance(v, set) else {g: sorted(x) for g, x in sorted(v.items())}
                 for k, v in d.items()} for ns, d in sorted(inv.items())}


ID_REF = re.compile(r"\b([a-z0-9_.-]+):([a-z0-9_./-]+)")


def unknown_ids(parsed, inv):
    """Every <ns>:<id> in the script whose namespace is the mod's, but which the mod does not register."""
    known = {}
    for ns, d in inv.items():
        known[ns] = set(d["entities"]) | set(d["items"]) | set(d["blocks"]) | set(d["sounds"]) | set(d["other_ids"])
        for g in d["data"].values():
            known[ns] |= set(g)
    out = []
    for s in parsed["steps"]:
        for no, line in s["lines"]:
            for ns, ident in ID_REF.findall(line):
                if ns in known and ident not in known[ns]:
                    out.append((no, f"{ns}:{ident} is not registered by the mod (playtest.py inventory lists what is)"))
    return out


def forbidden(parsed, ids):
    """Lines naming an id the plan rules out (a debug entity, a creative-only test item): never summoned or given."""
    out = []
    for s in parsed["steps"]:
        for no, line in s["lines"]:
            for f in ids:
                if re.search(r"(?<![\w:])" + re.escape(f) + r"(?![\w/])", line):
                    out.append((no, f"{f} is forbidden by the plan (--forbid)"))
    return out


# ── one version ──────────────────────────────────────────────────────────────────────────────────────────────────
class Recorder:
    """The screen while the game runs, with the system's own tool (macOS `screencapture -v`, stopped with SIGINT so
    the file is finalised). Anywhere else, or when macOS refuses (Screen Recording not granted), it says so and the
    run goes on with the key frames alone."""
    def __init__(self, path, plat):
        self.path, self.proc, self.note = pathlib.Path(path), None, None
        if plat.os_name != "osx" or not shutil.which("screencapture"):
            self.note = "no recording: only macOS's screencapture is supported (key frames are still taken)"

    def __enter__(self):
        if self.note is None:
            self.proc = subprocess.Popen(["screencapture", "-v", "-C", str(self.path)],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return self

    def __exit__(self, *exc):
        if self.proc is None:
            return False
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        err = (self.proc.stderr.read() or b"").decode("utf-8", "replace") if self.proc.stderr else ""
        if not self.path.is_file() or self.path.stat().st_size == 0:
            self.note = ("no recording: macOS did not record the screen -- allow Screen Recording for the app running "
                         "this (System Settings > Privacy & Security > Screen Recording)" + (f": {err.strip()[:160]}" if err.strip() else ""))
        return False


def run_version(R, plan, v, script, out, a, plat, log):
    mcdir = pathlib.Path(plan["minecraft_dir"])
    inst = pathlib.Path(v["instance"])
    res = {"mc": v["mc"], "neoforge": v.get("neoforge"), "verdict": "FAIL", "detail": "", "steps": [], "out": str(out),
           "video": None, "notes": []}
    if not (inst / R.MARK).is_file():
        res["detail"] = "the instance is not installed (real-install.py install --with-carrier)"
        return res
    vid = v.get("version_id") or R.IP.neoforge_installed(mcdir, v["neoforge"] or "0.0.0")
    vid = vid if (vid or "").startswith("neoforge-") else f"neoforge-{vid}" if vid else None
    java = R.java_exe(mcdir, v["java_component"], plat)
    carrier = carrier_jar(v, inst, a)
    if not vid or not java or not carrier:
        res["detail"] = ("NeoForge is not installed" if not vid else "no Java for it" if not java else
                         "no Gate C carrier in the instance (install --with-carrier, or --carrier-jar/--carrier-dir)")
        return res
    frames = out / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    props = {"smokeharness.boottest": "true", "smokeharness.testmode": "playtest",
             "smokeharness.playtest": str(pathlib.Path(script).resolve()), "smokeharness.playtest.out": str(frames),
             "smokeharness.namespaces": ",".join(v.get("modids_under_test") or [])}
    argv, missing = R.build_launch(mcdir, vid, inst, plat, java, props, a.xmx)
    if missing:
        res["detail"] = f"{len(missing)} missing file(s), e.g. {missing[0]} (real-install.py install)"
        return res
    done = lambda t: bool(VERDICT_LINE.search(t)) or bool(R.FATAL_LOG.search(t))
    log(f"Minecraft {v['mc']} [{vid}]: playtest {pathlib.Path(script).name} (timeout {a.timeout}s)", flush=True)
    rec = Recorder(out / f"playtest-{v['mc']}.mov", plat) if a.video else None
    if rec:
        with rec:
            text, err, crash, secs, rc = R.run_once(argv, inst, out, a.timeout, done, plat)
        if rec.note:
            res["notes"].append(rec.note)
        elif rec.path.is_file():
            res["video"] = rec.path.name
    else:
        text, err, crash, secs, rc = R.run_once(argv, inst, out, a.timeout, done, plat)
    res["seconds"] = secs
    data = {}
    rj = frames / "playtest-result.json"
    if rj.is_file():
        try:
            data = json.loads(rj.read_text(encoding="utf-8"))
        except ValueError:
            res["notes"].append("playtest-result.json did not parse")
    res["steps"] = data.get("steps") or [{"n": int(m.group(1)), "name": m.group(2), "verdict": m.group(3),
                                           "detail": m.group(4) or ""} for m in STEP_LINE.finditer(text)]
    res["events"] = data.get("events", [])
    m = VERDICT_LINE.search(text)
    if m:
        res["verdict"], res["detail"] = m.group(1), m.group(2).strip()
    elif crash:
        res["detail"] = f"the game crashed ({crash}) before the playtest finished"
    elif "glfwGetPrimaryMonitor failed" in text + err:
        # measured: the display slept between two runs -- the game never opened a window; not the mod, not the script
        res["detail"] = ("the display was asleep or the screen locked (glfwGetPrimaryMonitor failed): keep the screen "
                         "awake and unlocked, and run it again")
    else:
        res["detail"] = (R.native_crash(err + "\n" + text) or
                         (f"no verdict within {a.timeout}s" if secs >= a.timeout else f"the game exited ({rc}) before a verdict"))
    return res


def carrier_jar(v, inst, a):
    """The carrier in the instance's mods/, replaced first by --carrier-jar / --carrier-dir when given (a CI-built jar
    before its release exists)."""
    mods = inst / "mods"
    src = None
    for spec in a.carrier_jar or []:
        mc, _, path = spec.rpartition("=") if "=" in spec else ("", "", spec)
        if not mc or mc == v["mc"]:
            src = pathlib.Path(path)
    if a.carrier_dir:
        hits = sorted(pathlib.Path(a.carrier_dir).rglob(f"gatec-carrier-{v['mc']}.jar"))
        src = hits[0] if hits else src
    if src:
        for old in mods.glob("gatec-carrier-*.jar"):
            old.unlink()
        shutil.copyfile(src, mods / src.name)
    hits = sorted(mods.glob("gatec-carrier-*.jar"))
    return hits[0] if hits else None


# ── the report ───────────────────────────────────────────────────────────────────────────────────────────────────
def write_report(rep, script, parsed, results):
    rep.mkdir(parents=True, exist_ok=True)
    (rep / "playtest-report.json").write_text(json.dumps({"script": str(script), "intent": parsed["intent"],
                                                          "versions": results}, indent=2), encoding="utf-8")
    ok = all(r["verdict"] == "PASS" for r in results) and results
    md = [f"# Playtest: {pathlib.Path(script).name}", "", f"**Intent:** {parsed['intent']}", "",
          f"**Result: {'PASS' if ok else 'FAIL'}** -- {sum(r['verdict'] == 'PASS' for r in results)} of "
          f"{len(results)} version(s) passed", "", "| Minecraft | NeoForge | Result | Detail |", "|---|---|---|---|"]
    md += [f"| {r['mc']} | {r.get('neoforge')} | {r['verdict']} | {r['detail']} |" for r in results]
    for r in results:
        md += ["", f"## {r['mc']}: {r['verdict']}", ""]
        md += [f"- step {s.get('n')} **{s.get('name')}**: {s.get('verdict')} -- {s.get('detail', '')}" for s in r["steps"]]
        md += [f"- note: {n}" for n in r.get("notes", [])]
        if r.get("video"):
            md.append(f"- recording: `{r['mc']}/{r['video']}`")
    (rep / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (rep / "report.html").write_text(render_html(script, parsed, results, ok), encoding="utf-8")
    return ok


def render_html(script, parsed, results, ok):
    e = html.escape
    rows = "".join(f"<tr><td>{e(r['mc'])}</td><td>{e(str(r.get('neoforge')))}</td><td class='{r['verdict'].lower()}'>"
                   f"{e(r['verdict'])}</td><td>{e(r['detail'])}</td></tr>" for r in results)
    secs = []
    for r in results:
        steps = ""
        for s in r["steps"]:
            shots = "".join(f"<figure><img loading='lazy' src='{e(r['mc'])}/frames/{e(f)}' alt='{e(f)}'>"
                            f"<figcaption>{e(f)}</figcaption></figure>" for f in s.get("shots", []))
            steps += (f"<details{' open' if s.get('verdict') == 'FAIL' else ''}><summary><span class='{str(s.get('verdict')).lower()}'>"
                      f"{e(str(s.get('verdict')))}</span> step {e(str(s.get('n')))}: {e(str(s.get('name')))}</summary>"
                      f"<p>{e(str(s.get('detail', '')))}</p><div class='shots'>{shots}</div></details>")
        notes = "".join(f"<p class='note'>{e(n)}</p>" for n in r.get("notes", []))
        vid = f"<p>Recording: <code>{e(r['mc'])}/{e(r['video'])}</code></p>" if r.get("video") else ""
        secs.append(f"<section><h2>{e(r['mc'])} <span class='{r['verdict'].lower()}'>{e(r['verdict'])}</span></h2>"
                    f"{notes}{vid}{steps}</section>")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Playtest report</title><style>
:root{{--bg:#fff;--fg:#1b1b1f;--muted:#5d5d66;--line:#dedee3;--pass:#13795b;--fail:#b3261e}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#141417;--fg:#ececf1;--muted:#a3a3ad;--line:#2c2c33;--pass:#4cc38a;--fail:#ff8a80}}}}
:root[data-theme="dark"]{{--bg:#141417;--fg:#ececf1;--muted:#a3a3ad;--line:#2c2c33;--pass:#4cc38a;--fail:#ff8a80}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0 auto;max-width:1100px;padding:24px 16px}}
table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left}}
.pass{{color:var(--pass);font-weight:600}}.fail{{color:var(--fail);font-weight:600}}.note{{color:var(--muted)}}
details{{border:1px solid var(--line);border-radius:8px;margin:8px 0;padding:8px 12px}}summary{{cursor:pointer}}
.shots{{display:grid;gap:8px;grid-template-columns:repeat(auto-fill,minmax(240px,1fr))}}
figure{{margin:0}}img{{border-radius:6px;max-width:100%}}figcaption{{color:var(--muted);font-size:12px}}
</style></head><body><h1>Playtest: {e(pathlib.Path(script).name)}</h1><p><strong>Intent:</strong> {e(parsed['intent'])}</p>
<p class="{'pass' if ok else 'fail'}">Result: {'PASS' if ok else 'FAIL'}: {sum(r['verdict'] == 'PASS' for r in results)} of {len(results)} version(s) passed</p>
<table><tr><th>Minecraft</th><th>NeoForge</th><th>Result</th><th>Detail</th></tr>{rows}</table>{''.join(secs)}</body></html>
"""


def explain(run_dir, mc=None):
    """The one failed version of a run, compact: intent, the failing step's lines and observations, and the logs' tails
    -- what playtest-debug reads."""
    rep = json.loads((pathlib.Path(run_dir) / "playtest-report.json").read_text(encoding="utf-8"))
    cands = [v for v in rep["versions"] if (mc is None or v["mc"] == mc) and v["verdict"] != "PASS"]
    if not cands:
        return {"intent": rep["intent"], "failed": None}
    v = cands[0]
    out = pathlib.Path(v["out"])
    fail = next((s for s in v["steps"] if s.get("verdict") == "FAIL"), None)
    tail = lambda p, n: "\n".join(p.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]) if p.is_file() else ""
    return {"intent": rep["intent"], "script": rep["script"], "mc": v["mc"], "neoforge": v.get("neoforge"),
            "verdict": v["verdict"], "detail": v["detail"], "failed_step": fail,
            "steps": [{k: s.get(k) for k in ("n", "name", "verdict", "detail")} for s in v["steps"]],
            "events": v.get("events", []), "notes": v.get("notes", []),
            "latest_log_tail": tail(out / "latest.log", 60), "stderr_tail": tail(out / "stderr.log", 30),
            "frames": sorted(p.name for p in (out / "frames").glob("*.png")) if (out / "frames").is_dir() else []}


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def load_plan(a):
    if a.plan:
        p = pathlib.Path(a.plan)
    else:
        cands = sorted((pathlib.Path.home() / ".mmvu" / "real-install").glob("*/plan.json"))
        if len(cands) != 1:
            raise SystemExit("playtest: pass --plan (real-install.py's plan.json): "
                             + (f"{len(cands)} plans found" if cands else "none found -- run real-install.py plan/install first"))
        p = cands[0]
    plan = json.loads(p.read_text(encoding="utf-8"))
    if a.mc:
        unknown = [m for m in a.mc if m not in {v["mc"] for v in plan["versions"]}]
        if unknown:
            raise SystemExit(f"playtest: {', '.join(unknown)} not in the plan {p}")
        plan = dict(plan, versions=[v for v in plan["versions"] if v["mc"] in a.mc])
    return p, plan


def cmd_run(a):
    script = pathlib.Path(a.script)
    parsed = parse(script.read_text(encoding="utf-8"))
    bad = [(n, m) for n, m in parsed["problems"] if not m.startswith("no @intent")]   # that one only warns
    for n, m in parsed["problems"]:
        print(f"{script}:{n}: {m}", file=sys.stderr)
    if bad:
        return 2
    R = ri()
    plat = R.Plat.host()
    pf, plan = load_plan(a)
    rep = pathlib.Path(a.report) if a.report else pf.parent / "playtests" / f"{script.stem}-{datetime.datetime.now():%Y%m%d-%H%M%S}"
    rep = rep.resolve()          # the game runs in the instance folder: a relative out dir would land there
    results = []
    for v in plan["versions"]:
        res = run_version(R, plan, v, script, rep / v["mc"], a, plat, print)
        results.append(res)
        print(f"  {res['verdict']}  {v['mc']}: {res['detail']}", flush=True)
    ok = write_report(rep, script, parsed, results)
    print(f"\nplaytest: {'PASS' if ok else 'FAIL'} -- {sum(r['verdict'] == 'PASS' for r in results)}/{len(results)} "
          f"version(s); report {rep / 'report.html'}")
    return 0 if ok else 1


def cmd_inventory(a):
    inv = inventory(a.jar)
    if a.json:
        print(json.dumps(inv, indent=2))
        return 0
    for ns, d in inv.items():
        print(f"== {ns}")
        for k in ("entities", "items", "blocks", "sounds", "other_ids"):
            if d[k]:
                print(f"  {k} ({len(d[k])}): {', '.join(d[k])}")
        for g, keys in d["lang"].items():
            print(f"  lang {g} ({len(keys)}): {', '.join(keys[:12])}{' ...' if len(keys) > 12 else ''}")
        for g, files in d["data"].items():
            print(f"  data {g} ({len(files)}): {', '.join(files[:8])}{' ...' if len(files) > 8 else ''}")
    return 0


def cmd_lint(a):
    parsed = parse(pathlib.Path(a.script).read_text(encoding="utf-8"))
    if a.jar:
        parsed["problems"] += unknown_ids(parsed, inventory(a.jar))
    parsed["problems"] += forbidden(parsed, a.forbid or [])
    for n, m in parsed["problems"]:
        print(f"{a.script}:{n}: {m}")
    steps = parsed["steps"]
    print(f"playtest lint: {'FAIL' if parsed['problems'] else 'PASS'} -- {len(steps)} step(s), "
          f"intent: {parsed['intent'] or '(none)'}")
    return 1 if parsed["problems"] else 0


def self_check():
    bad = []
    def expect(c, m):
        if not c:
            bad.append(m)
    p = parse("@intent the zombie dies\ngamemode creative\n@step summon\n/summon minecraft:zombie ~ ~ ~3\n"
              "@expect sound minecraft:entity.zombie.ambient within 10s\n@step kill\n@aim entity minecraft:zombie\n"
              "@key key.attack hold 5t\n@assert execute unless entity @e[type=minecraft:zombie]\n@shot dead\n")
    expect(p["intent"] == "the zombie dies" and not p["problems"], f"a valid script parses clean: {p['problems']}")
    expect([s["name"] for s in p["steps"]] == ["main", "summon", "kill"] and [s["n"] for s in p["steps"]] == [1, 2, 3],
           f"steps numbered as the carrier numbers them: {[(s['n'], s['name']) for s in p['steps']]}")
    expect(p["steps"][1]["lines"][0][1] == "summon minecraft:zombie ~ ~ ~3", "a leading / is dropped")
    for src, want in [("@intent x\n@wiat 5s\n", "unknown directive"), ("@intent x\n@wait 5\n", "a duration is"),
                      ("@intent x\n@expect noise a\n", "unknown @expect kind"), ("@intent x\n@key key.use hold 5\n", "hold"),
                      ("@intent x\n@aim at zombie\n", "@aim entity"), ("@intent x\n@expect sound\n", "needs a match"),
                      ("@step\n", "needs a name"), ("say hi\n", "no @intent")]:
        expect(any(want in m for _n, m in parse(src)["problems"]), f"{src!r} is refused naming {want!r}")
    t = ("x PLAYTEST: STEP 1 summon: PASS -- spawned\n[12:00] [Render thread/INFO] [smokeharness/]: PLAYTEST: STEP 2 kill: "
         "FAIL -- @assert line 9 failed\n[12:00] [x/INFO] [smokeharness/]: PLAYTEST: FAIL -- 1/2 steps\n")
    steps = [(m.group(1), m.group(3)) for m in STEP_LINE.finditer(t)]
    expect(steps == [("1", "PASS"), ("2", "FAIL")] and VERDICT_LINE.search(t).group(1) == "FAIL",
           f"step and verdict lines are read with a log4j prefix: {steps}")
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        res = [{"mc": "1.21.1", "neoforge": "21.1.1", "verdict": "FAIL", "detail": "1/2 steps", "out": str(d / "1.21.1"),
                "steps": [{"n": 1, "name": "summon", "verdict": "PASS", "detail": "", "shots": ["01-step-1-end.png"]},
                          {"n": 2, "name": "kill", "verdict": "FAIL", "detail": "@assert failed", "shots": []}],
                "notes": ["no recording: x"], "video": None}]
        (d / "1.21.1").mkdir()
        (d / "1.21.1" / "latest.log").write_text("a\nb\n", encoding="utf-8")
        ok = write_report(d, d / "t.playtest", p, res)
        h = (d / "report.html").read_text(encoding="utf-8")
        expect(not ok and "1.21.1/frames/01-step-1-end.png" in h and "<details open>" in h and "prefers-color-scheme" in h,
               "the HTML report links each step's frames by relative path and opens the failed step")
        x = explain(d, "1.21.1")
        expect(x["failed_step"]["name"] == "kill" and x["intent"] == "the zombie dies" and "b" in x["latest_log_tail"],
               f"explain: the failed step, the intent and the log tail: {x.get('failed_step')}")
        import zipfile
        jar = d / "m.jar"
        with zipfile.ZipFile(jar, "w") as z:
            z.writestr("assets/examplemod/sounds.json", json.dumps({"boss_music": {}}))
            z.writestr("assets/examplemod/lang/en_us.json", json.dumps({
                "entity.examplemod.boss": "Boss", "item.examplemod.blade": "Blade",
                "subtitles.examplemod.boss_music": "Music plays", "examplemod.boss.line_1": "Hello",
                "effect.examplemod.doom": "Doom"}))
            z.writestr("data/examplemod/loot_table/entities/boss.json", "{}")
            z.writestr("assets/minecraft/lang/en_us.json", "{}")
        inv = inventory([jar])
        e = inv.get("examplemod", {})
        expect(list(inv) == ["examplemod"] and e["entities"] == ["boss"] and e["items"] == ["blade"]
               and e["sounds"] == ["boss_music"] and "subtitles" in e["lang"] and "loot_table" in e["data"]
               and e["other_ids"] == ["doom"],
               f"inventory reads entities, items, sounds, lang groups and data, and skips minecraft: {inv}")
        q = parse("@intent x\nsummon examplemod:boss\ngive @s examplemod:blade\nsummon examplemod:bos\n"
                  "@expect sound examplemod:boss_music\nsummon minecraft:zombie\neffect give @s examplemod:doom\n")
        u = unknown_ids(q, inv)
        expect([n for n, _m in u] == [4], f"lint --jar flags only the misspelt id: {u}")
        f = forbidden(parse("@intent x\nsummon examplemod:test_entity\nsummon examplemod:test_entity_two\n"),
                      ["examplemod:test_entity"])
        expect([n for n, _m in f] == [2], f"--forbid matches the whole id only: {f}")
    print("\n".join(bad) or "self-check: OK")
    return 1 if bad else 0


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if argv[:1] == ["--self-check"]:
        return self_check()
    ap = argparse.ArgumentParser(prog="playtest", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run a playtest on each installed version")
    r.add_argument("script")
    r.add_argument("--plan", help="real-install.py's plan.json (default: the only one under ~/.mmvu/real-install)")
    r.add_argument("--mc", action="append", help="only these Minecraft versions (repeat)")
    r.add_argument("--video", action="store_true", help="record the screen while each version runs (macOS)")
    r.add_argument("--report", help="the report folder (default: next to the plan, under playtests/)")
    r.add_argument("--carrier-jar", action="append", metavar="[MC=]JAR", help="use this carrier jar (a CI build)")
    r.add_argument("--carrier-dir", help="a folder of gatec-carrier-<mc>.jar (e.g. a downloaded CI artifact)")
    r.add_argument("--timeout", type=int, default=900, help="seconds per version (default 900)")
    r.add_argument("--xmx", default="4G")
    l = sub.add_parser("lint", help="check a script's syntax and directives, without Minecraft")
    l.add_argument("script")
    l.add_argument("--jar", action="append", help="check every <ns>:<id> against what these jars register")
    l.add_argument("--forbid", action="append", metavar="NS:ID", help="an id the script must never name (repeat)")
    i = sub.add_parser("inventory", help="what the mod's jars register: entities, items, sounds, lang keys, data")
    i.add_argument("--jar", action="append", required=True)
    i.add_argument("--json", action="store_true")
    x = sub.add_parser("explain", help="the failed run of one version, compact, for playtest-debug")
    x.add_argument("run_dir")
    x.add_argument("--mc")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        return cmd_run(a)
    if a.cmd == "lint":
        return cmd_lint(a)
    if a.cmd == "inventory":
        return cmd_inventory(a)
    print(json.dumps(explain(a.run_dir, a.mc), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
