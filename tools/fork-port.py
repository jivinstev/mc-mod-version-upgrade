#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Port one fork-layer target in one command, and hand back only the residue.

    python3 tools/fork-port.py --repo DIR --target 1.21.5 [--budget 15] [--gates none|ab|full]
                               [--json out.json] [--from-stage <name>] [--base REF] [--modid ID] [--self-check]

The fork-versions skill's procedure (steps 1-9) as stages, the way tools/run-port.py is the jar-port procedure.
The orchestrating session starts this and then reads ONE summary (the last lines of stdout, the --json file, and
.multiversion/targets/<t>/RESIDUE.md when it stopped) -- never the workers' transcripts or the build logs.

Stages, each logged as one line and skipped with a reason when it does not apply:
  setup    make-fork-layer.py add-target (when the target dir is missing) + sync-shared; installs layer.py's
           support for the generated moves table when the fork's copy predates it
  stage    one `.multiversion/build.sh <t> compileJava` to stage Minecraft; while createMinecraftArtifacts fails
           on the access transformer, fix-access-transformer.py --overrides-from <log> on the TARGET's own AT
           (bounded: 4 rounds)
  moves    build-class-move-map.py between the target this one inherits (or the base) and this one, from the
           staged merged jars -> .multiversion/targets/<t>/moves.generated.tsv, which layer.py reads as one more
           rename table. Generated per fork per target from Mojang's data, so not a map anyone commits upstream.
  measure  compile every source set (srcsets.compile_tasks); burndown-count.sh decides whether it is a count
  loop     file-loop.py --layer <t> --budget N (its kept rows land in the target's renames.tsv)
  audit    audit-mixin-targets.py against the target's sources jar and prepared tree, then audit-event-listeners.py (abstract
           events) over every prepared tree against the target's resolved classpath (only at 0 errors)
  gates    ci-gates.py --mc <t>: none = skip; ab = author build, Gate A, Gate B; full = + Gate C phases. After a green run, the
           mod's own log files (harness/run/logs, minus vanilla's) are compared with the inherited target's last gate run: a file
           it wrote there and not here is residue (exit 11) -- a subsystem stopped running (CATALOG X47)
  cost     port-cost.py --target <t> --since <start of this port> appended to targets/<t>/COST.md

THE STOP CONTRACT. When a stage cannot finish this does not hand the rest to anyone silently: it prints a
STOPPED block and writes the RESIDUE REPORT (into --json and .multiversion/targets/<t>/RESIDUE.md): the
remaining errors grouped by CATALOG entry / family (not the raw log), the loop's escalation record, audit
mismatches or failing gates with their one-line reason, and the command to resume (`--from-stage <stage>`).
Whoever picks it up fixes what RESIDUE.md lists, and puts each fix in the shared library
(templates/fork-layer/shared/<t>) or a converter -- not in one fork.

Exit codes: 0 done; 10 stopped before/in the compile (stage, moves, measure, loop); 11 Gate A/B (or the author's
build) red; 12 Gate C red; 13 mixin / event-listener audit; 2 bad input. Standard library only.
"""
import argparse, datetime, importlib.util, json, pathlib, re, shutil, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from gitbash import BASH   # noqa: E402

STAGES = ["setup", "stage", "moves", "measure", "loop", "audit", "gates", "cost"]
STAGE_ROUNDS = 4
INSERT = ('            m = MV / "targets" / t / "moves.generated.tsv"   # tools/fork-port.py: derived from the staged jars\n'
          '            if m.exists():\n'
          '                rows.append(f"# ---- moves {t} (generated)\\n" + m.read_text(encoding="utf-8"))\n')
ANCHOR = ('            if r.exists():\n'
          '                rows.append(f"# ---- {t}\\n" + r.read_text(encoding="utf-8"))\n')


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / file)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------- pure helpers (covered by --self-check)

def gate_evidence(text, limit=4):
    """The first few concrete reasons a gate log is red: javac `file:line: error: message` lines (path shortened to
    its last three parts), else failing JUnit/GameTest lines. [] when the log says neither."""
    out, seen = [], set()
    for m in re.finditer(r"(?m)^\s*(\S+\.java):(\d+): error: (.+)$", text):
        key = (m.group(1), m.group(2))
        if key in seen:
            continue
        seen.add(key)
        out.append("%s:%s: %s" % ("/".join(m.group(1).split("/")[-3:]), m.group(2), m.group(3).strip()))
        if len(out) >= limit:
            return out
    if out:
        return out
    for m in re.finditer(r"(?m)^.*(?:FAILED|failed after|Test .* failed|Unregistered holder|Missing test).*$", text):
        out.append(m.group(0).strip()[:200])
        if len(out) >= limit:
            break
    return out


def mod_log_files(d):
    """The log files the MOD wrote in a Gate B run's logs/ directory: every regular file except vanilla's own
    latest.log / debug.log, the dated archives log4j rolls them into (2026-10-12-1.log.gz), and crash dumps.
    set() when the directory is absent."""
    d = pathlib.Path(d)
    if not d.is_dir():
        return set()
    out = set()
    for f in d.iterdir():
        n = f.name
        if not f.is_file() or n in ("latest.log", "debug.log") or n.endswith(".gz"):
            continue
        if re.match(r"^\d{4}-\d{2}-\d{2}-\d+\.log$", n) or n.startswith("crash-"):
            continue
        out.add(n)
    return out


def lost_log_files(prev_dir, cur_dir, known_absent=()):
    """Log files the mod wrote on the previous target and did not write on this one (CATALOG X47), minus the ones
    the fork has recorded as deliberately gone. [] when there is no previous run to compare with."""
    if prev_dir is None or not pathlib.Path(prev_dir).is_dir():
        return []
    return sorted(mod_log_files(prev_dir) - mod_log_files(cur_dir) - set(known_absent))


def select_stages(from_stage):
    """(skipped, to_run) for --from-stage."""
    if from_stage is None:
        return [], list(STAGES)
    if from_stage not in STAGES:
        raise ValueError(f"unknown stage {from_stage!r}; stages: {', '.join(STAGES)}")
    i = STAGES.index(from_stage)
    return STAGES[:i], STAGES[i:]


def read_props(path):
    out = {}
    p = pathlib.Path(path)
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def chain(mv, target):
    out, t = [], target
    while t and t not in out:
        out.append(t)
        t = read_props(mv / "targets" / t / "target.properties").get("inherits")
    return out   # nearest first


def parse_modid(toml_text):
    m = re.search(r'(?s)\[\[mods\]\].*?modId\s*=\s*"([^"]+)"', toml_text)
    return m.group(1) if m else None


def plain_froms(text):
    """The `from` of every plain (type-name) row of a rename table."""
    out = set()
    for l in text.splitlines():
        if not l.strip() or l.lstrip().startswith("#") or "\t" not in l:
            continue
        f = l.split("\t", 1)[0].strip()
        if not f.startswith(("re:", "member:")):
            out.add(f)
    return out


def moves_table(raw_rows, existing, prev, target):
    """The generated rename table. Rows whose `from` another table of the chain already carries are dropped:
    a duplicate `from` is a hard error at table load (CATALOG X31). The block is #!exhaustive (most moves are
    for classes this mod does not use) and CLOSED with #!strict -- an unclosed block exempts every later rule."""
    kept = [(a, b) for a, b in raw_rows if a not in existing]
    text = [f"# GENERATED by tools/fork-port.py: Minecraft/NeoForge classes that kept their simple name and moved",
            f"# package between {prev} and {target} (build-class-move-map.py over the two staged merged jars).",
            "# Derived from Mojang's data: regenerate (fork-port.py --from-stage moves), do not edit. Committed in the fork (its build reads it); never copied into the migrator's shared library",
            "# as a map. A real API change is never in here (it is in the .removed / .ambiguous lists).",
            "#!exhaustive  generated: most rows are for classes this mod does not use"]
    text += [f"{a}\t{b}" for a, b in kept]
    text.append("#!strict")
    return "\n".join(text) + "\n", len(kept), len(raw_rows) - len(kept)


def read_pairs(path):
    out = []
    for l in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        if "\t" in l and not l.startswith("#"):
            a, b = l.split("\t")[:2]
            out.append((a.strip(), b.strip()))
    return out


def layer_patch(fork_text, template_text):
    """('current'|'replace'|'diverged', text). The fork's layer.py is the migrator's file copied in by `init`; when
    it equals the template minus the moves lines it is safe to replace, otherwise a human merges."""
    if "moves.generated.tsv" in fork_text:
        return "current", fork_text
    if INSERT in template_text and fork_text == template_text.replace(INSERT, ""):
        return "replace", template_text
    return "diverged", fork_text


def below_java(p):
    return p.replace("\\", "/").rsplit("/java/", 1)[-1]


def loop_events(log):
    """The events of the LAST run in a (resumable, appended-to) file-loop log."""
    p = pathlib.Path(log)
    if not p.is_file():
        return []
    ev = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    starts = [i for i, e in enumerate(ev) if e.get("event") == "start"]
    return ev[starts[-1]:] if starts else ev


def escalation(events, failing_files, top="opus"):
    """What the loop tried: files that reached the top model tier and still fail, rows it kept and reverted."""
    tiers = {}
    order = ["haiku", "sonnet", "opus"]
    for e in events:
        if e.get("event") == "worker":
            for f in e.get("files") or []:
                m = e.get("model")
                if m in order and order.index(m) >= order.index(tiers.get(below_java(f), "haiku")):
                    tiers[below_java(f)] = m
    still = sorted(f for f, m in tiers.items() if m == top and f in failing_files)
    reverted = [(e.get("row"), str(e.get("why"))[:100]) for e in events if e.get("event") == "rule" and not e.get("kept")]
    kept = [e.get("row") for e in events if e.get("event") == "rule" and e.get("kept")]
    spent = max([e.get("spent", 0) for e in events if "spent" in e] or [0])
    return {"top_tier_still_failing": still, "rows_kept": kept, "rows_reverted": reverted,
            "stubs": [s["file"] for e in events if e.get("event") == "stub-signals" for s in e.get("files", [])],
            "spent": spent}


def stop_block(stop):
    lines = [f"STOPPED at {stop['stage']}: {stop['left']} (spent ${stop.get('spent', 0)} here)."]
    lines += [f"  {d}" for d in stop.get("detail", [])]
    lines.append(f"Residue: {stop.get('residue', '(none)')}")
    lines.append("Choices (ask the person; do not pick one silently):")
    lines += [f"  - {c}" for c in stop["choices"]]
    return "\n".join(lines)


def residue_report(stage, left, errors_view, esc, audit, gates, resume):
    """RESIDUE.md and its JSON twin. Bounded by construction: groups and the top files, never the raw log."""
    md = [f"# Residue: {stage}", "", f"Stopped at **{stage}**: {left}.", ""]
    if errors_view:
        md += ["## Remaining compile errors (compile-summary.py, grouped by CATALOG entry / family)", "", "```"]
        md += errors_view + ["```", ""]
    if esc:
        md += ["## The loop's record", ""]
        md += [f"- spent ${esc['spent']}"]
        md += [f"- still failing at the top tier: {', '.join(esc['top_tier_still_failing']) or 'none'}"]
        md += [f"- rows kept: {len(esc['rows_kept'])}"]
        md += [f"- row tried and reverted: `{r}` ({w})" for r, w in esc["rows_reverted"][:10]]
        if esc["stubs"]:
            md += [f"- STUB WARNING (a feature may have been deleted to clear an error): {', '.join(esc['stubs'][:10])}"]
        md.append("")
    if audit:
        md += ["## Mixin audit mismatches", "", "```"] + audit[:14] + ["```", ""]
    if gates:
        md += ["## Failing gates", ""] + [f"- {g}" for g in gates[:10]] + [""]
    md += ["## To resume", "", f"`{resume}`", "",
           "Fix what is listed here, and put every fix in the shared library "
           "(templates/fork-layer/shared/<t>) or a converter, not in one fork.", ""]
    data = {"stage": stage, "left": left, "errors": errors_view, "loop": esc, "audit": audit, "gates": gates,
            "resume": resume}
    return "\n".join(md), data


# ---------------------------------------------------------------- running things

class Run:
    def __init__(self, a):
        self.a = a
        self.repo = pathlib.Path(a.repo).resolve()
        self.mv = self.repo / ".multiversion"
        self.t = a.target
        self.tdir = self.mv / "targets" / self.t
        self.work = self.repo / "build" / f"mc{self.t}" / "fork-port"
        self.summary = {"repo": str(self.repo), "target": self.t, "stages": {},
                        "started": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")}
        self.jpath = pathlib.Path(a.json) if a.json else self.work / "fork-port.json"
        self.py = sys.executable

    def say(self, stage, msg):
        print(f"fork-port: {stage}: {msg}", flush=True)

    def sh(self, cmd, log=None, cwd=None):
        out = open(log, "w", encoding="utf-8") if log else subprocess.PIPE
        r = subprocess.run(cmd, cwd=cwd or self.repo, stdout=out, stderr=subprocess.STDOUT, text=True,
                           encoding="utf-8", errors="replace")
        if log:
            out.close()
        return r

    def gradle(self, tasks, log):
        init = [str(x) for x in ("--init-script", ROOT / "tools/maxerrs.init.gradle",
                                 "--init-script", ROOT / "tools/central-mirror.init.gradle")]
        return self.sh([BASH, str(self.mv / "build.sh"), self.t, *tasks, "--continue", "--console=plain", *init,
                        "-Dorg.gradle.jvmargs=-Xmx6g"], log)

    def count(self, log):
        """(errors or None, reason). BUILD SUCCESSFUL is 0; otherwise only burndown-count.sh may call it a count."""
        text = pathlib.Path(log).read_text(encoding="utf-8", errors="replace")
        if "BUILD SUCCESSFUL" in text:
            return 0, ""
        r = subprocess.run([BASH, str(ROOT / "tools/burndown-count.sh"), str(log)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            return None, " ".join(r.stdout.strip().splitlines()[:2])
        m = re.search(r"errors = (\d+)", r.stdout)
        return (int(m.group(1)) if m else None), ""

    def artifacts(self):
        d = self.repo / "build" / f"mc{self.t}" / "moddev" / "artifacts"
        return sorted(d.glob("*merged.jar")), sorted(j for j in d.glob("*-sources.jar") if "resources" not in j.name)

    def resume_cmd(self, stage):
        a = self.a
        return (f"python3 tools/fork-port.py --repo {a.repo} --target {self.t} --from-stage {stage} "
                f"--budget {a.budget} --gates {a.gates}")

    def save(self):
        self.jpath.parent.mkdir(parents=True, exist_ok=True)
        self.jpath.write_text(json.dumps(self.summary, indent=1), encoding="utf-8")

    # ---- stages: each returns a result dict; a "stop" key ends the run

    def s_setup(self):
        if not (self.mv / "base.properties").exists():
            return {"stop": "no .multiversion/ in the fork", "code": 2,
                    "choices": [f"python3 tools/make-fork-layer.py init --repo {self.repo}"]}
        did = []
        if not self.tdir.is_dir():
            r = self.sh([self.py, str(ROOT / "tools/make-fork-layer.py"), "add-target", "--repo", str(self.repo),
                         "--target", self.t])
            if r.returncode != 0:
                return {"stop": "add-target refused: " + (r.stdout or "").strip().splitlines()[-1][:200], "code": 2,
                        "choices": ["make-fork-layer.py versions lists the targeted lines and why others are skipped"]}
            did.append("add-target")
        r = self.sh([self.py, str(ROOT / "tools/make-fork-layer.py"), "sync-shared", "--repo", str(self.repo)])
        did.append("sync-shared")
        lp, tp = self.mv / "tools/layer.py", ROOT / "templates/fork-layer/tools/layer.py"
        verdict, text = layer_patch(lp.read_text(encoding="utf-8"), tp.read_text(encoding="utf-8"))
        if verdict == "replace":
            lp.write_text(text, encoding="utf-8"); did.append("layer.py: generated-moves support installed")
        elif verdict == "diverged":
            return {"stop": ".multiversion/tools/layer.py does not read moves.generated.tsv and differs from the "
                            "template, so it cannot be replaced safely", "code": 2,
                    "choices": ["merge templates/fork-layer/tools/layer.py's moves.generated.tsv block into the fork's copy"]}
        return {"done": ", ".join(did)}

    def s_stage(self):
        ch = chain(self.mv, self.t)
        at = self.tdir / "resources/META-INF/accesstransformer.cfg"
        if not at.exists():     # the nearest AT in the chain, else the authors': the target's file replaces them
            src = next((self.mv / "targets" / x / "resources/META-INF/accesstransformer.cfg" for x in ch[1:]
                        if (self.mv / "targets" / x / "resources/META-INF/accesstransformer.cfg").exists()),
                       self.repo / "src/main/resources/META-INF/accesstransformer.cfg")
            if src.exists():
                at.parent.mkdir(parents=True, exist_ok=True)
                at.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        self.work.mkdir(parents=True, exist_ok=True)
        for rnd in range(1, STAGE_ROUNDS + 1):
            log = self.work / f"stage-{rnd}.log"
            self.gradle(["compileJava"], log)
            text = log.read_text(encoding="utf-8", errors="replace")
            merged, sources = self.artifacts()
            if "createMinecraftArtifacts FAILED" not in text and merged and sources:
                return {"done": f"Minecraft staged ({len(merged)} merged jar(s)), {rnd} round(s)", "rounds": rnd}
            if not at.exists():
                break
            # fix-access-transformer edits src/*/resources/META-INF/accesstransformer.cfg under --work: give it the
            # TARGET's file in a scratch tree, then take the result back. The authors' AT is never touched.
            with tempfile.TemporaryDirectory() as tmp:
                scratch = pathlib.Path(tmp) / "src/main/resources/META-INF/accesstransformer.cfg"
                scratch.parent.mkdir(parents=True)
                scratch.write_text(at.read_text(encoding="utf-8"), encoding="utf-8")
                r = self.sh([self.py, str(ROOT / "tools/fix-access-transformer.py"), "--work", tmp,
                             "--overrides-from", str(log)])
                added = re.search(r"(\d+) override widening", r.stdout or "")
                if not added or int(added.group(1)) == 0:
                    break
                at.write_text(scratch.read_text(encoding="utf-8"), encoding="utf-8")
        tail = [l for l in text.splitlines() if "What went wrong" in l or "error" in l.lower()][:4]
        return {"stop": "Minecraft would not stage (createMinecraftArtifacts)", "code": 10, "detail": tail,
                "choices": ["read build/mc%s/fork-port/stage-*.log for the first failure, fix the access "
                            "transformer by hand in targets/%s/resources/META-INF/" % (self.t, self.t)]}

    def s_moves(self):
        ch = chain(self.mv, self.t)
        prev = ch[1] if len(ch) > 1 else read_props(self.mv / "base.properties").get("minecraft_version", "base")
        if len(ch) > 1:
            old_dir = self.repo / "build" / f"mc{prev}" / "moddev" / "artifacts"
        else:
            old_dir = self.repo / "build" / "moddev" / "artifacts"
        old = sorted(old_dir.glob("*merged.jar"))
        if not old and len(ch) > 1:     # the previous layer target was never staged on this checkout
            self.sh([BASH, str(self.mv / "build.sh"), prev, "createMinecraftArtifacts", "--console=plain"],
                    self.work / "stage-prev.log")
            old = sorted(old_dir.glob("*merged.jar"))
        new, _ = self.artifacts()
        if not old or not new:
            return {"stop": f"no staged merged jar for {prev if not old else self.t}", "code": 10,
                    "choices": [f"stage it: .multiversion/build.sh {prev if not old else self.t} createMinecraftArtifacts"]}
        self.work.mkdir(parents=True, exist_ok=True)
        cps = []
        for name, jars in (("old", old), ("new", new)):
            p = self.work / f"cp-{name}.txt"
            p.write_text("\n".join(str(j) for j in jars) + "\n", encoding="utf-8")
            cps.append(p)
        raw = self.work / f"moves-{prev}-to-{self.t}.tsv"
        r = self.sh([self.py, str(ROOT / "tools/build-class-move-map.py"), "--from-cp", str(cps[0]), "--to-cp",
                     str(cps[1]), "--out", str(raw)])
        if r.returncode != 0 or not raw.exists():
            return {"stop": "build-class-move-map.py failed: " + (r.stdout or "").strip()[-200:], "code": 10,
                    "choices": ["see build-class-move-map.py's usage; check both jars hold net.minecraft classes"]}
        existing = set()
        for x in ch:
            for f in (self.mv / "targets" / x / "renames.tsv", self.mv / "shared" / x / "renames.tsv"):
                if f.is_file():
                    existing |= plain_froms(f.read_text(encoding="utf-8"))
        text, kept, dup = moves_table(read_pairs(raw), existing, prev, self.t)
        gen = self.tdir / "moves.generated.tsv"
        gen.write_text(text, encoding="utf-8")
        at = self.tdir / "resources/META-INF/accesstransformer.cfg"
        fixed = ""
        if at.is_file():    # an AT entry for a class vanilla moved matches nothing; rewrite those to the new names
            with tempfile.TemporaryDirectory() as tmp:
                scratch = pathlib.Path(tmp) / "src/main/resources/META-INF/accesstransformer.cfg"
                scratch.parent.mkdir(parents=True)
                scratch.write_text(at.read_text(encoding="utf-8"), encoding="utf-8")
                self.sh([self.py, str(ROOT / "tools/fix-access-transformer.py"), "--work", tmp, "--class-map", str(raw)])
                new_at = scratch.read_text(encoding="utf-8")
                if new_at != at.read_text(encoding="utf-8"):
                    at.write_text(new_at, encoding="utf-8"); fixed = ", access transformer re-pointed at moved classes"
        return {"done": f"{kept} unambiguous move(s) {prev}->{self.t} -> {gen.relative_to(self.repo)}"
                        f"{f' ({dup} already in a table)' if dup else ''}{fixed}", "moves": kept}

    def s_measure(self):
        ss = load("srcsets", "srcsets.py")
        tasks = ss.compile_tasks(self.repo)
        log = self.work / "measure.log"
        self.work.mkdir(parents=True, exist_ok=True)
        self.gradle(tasks, log)
        n, why = self.count(log)
        if n is None:
            return {"stop": "the compile does not count: " + why, "code": 10, "errors": None, "detail": [why],
                    "choices": ["read build/mc%s/fork-port/measure.log; fix what keeps javac from attributing" % self.t]}
        return {"done": f"{n} error(s) across {len(tasks)} compile task(s)", "errors": n, "log": str(log), "this_run": True}

    def error_view(self, log):
        cs = load("compile_summary", "compile-summary.py")
        text = pathlib.Path(log).read_text(encoding="utf-8", errors="replace")
        code, why = cs.validate(log)
        if code != 0:
            return [f"NOT A COUNT: {w}" for w in why[:3]], set()
        s = cs.summarise(text, cs.rb.signatures())
        return cs.render(s, None, 8, 8), set(s["files"])

    def s_loop(self):
        prior = self.summary["stages"].get("measure", {})
        start = prior.get("errors")
        if start == 0:
            return {"skipped": "already at 0 errors", "errors": 0}
        flog = self.work / "file-loop.jsonl"
        out = self.work / "file-loop.out"
        r = self.sh([self.py, str(ROOT / "tools/file-loop.py"), "--work", str(self.repo), "--layer", self.t,
                     "--budget", str(self.a.budget), "--log", str(flog)], out)
        ev = loop_events(flog)
        end = next((e for e in reversed(ev) if e.get("event") == "end"), None)
        n = end.get("errors") if end else None
        clog = self.repo / "build" / f"mc{self.t}" / "file-loop-compile.log"
        res = {"errors": n, "start_errors": start, "exit": r.returncode, "log": str(flog), "this_run": True}
        if n == 0:
            # 0 errors is not done when a worker got there by emptying code or by forking the shared library: the
            # loop's own stub audit, and any overlay of a shared helper, are residue (measured on the first
            # unattended target: both happened at "0 errors" and the run went on to the audit)
            stubs = [s for e in ev if e.get("event") == "stub-signals" for s in e.get("files", [])]
            shared = self.shared_helper_overlays()
            if stubs or shared:
                detail = [f"stub: {s['file']}: {s['why'][:200]}" for s in stubs] + \
                         [f"shared helper overlaid in this fork: {f}" for f in shared]
                res.update({"stop": f"0 errors, but {len(stubs)} stub signal(s) and {len(shared)} overlay(s) of the "
                                    f"shared library", "code": 10, "detail": detail, "view": detail,
                            "choices": ["port each flagged method to the new signature (never drop @Override)",
                                        "fix a shared helper in templates/fork-layer/shared/<t> and delete the "
                                        "fork's overlay of it; then resume with --from-stage loop"]})
                return res
            res["done"] = f"{start} -> 0 errors, ${end.get('spent', 0)} of workers"
            return res
        view, files = self.error_view(clog) if clog.is_file() else ([], set())
        esc = escalation(ev, files)
        res.update({"stop": f"{n} compile errors" if n is not None else "the loop ended without a count (file-loop.out)",
                    "code": 10, "spent": esc["spent"], "view": view, "esc": esc, "detail": view[:6],
                    "choices": [f"continue: {self.resume_cmd('loop')} with a higher --budget",
                                "fix the listed groups by hand, as shared rows / helpers (not in this fork)"]})
        return res

    def shared_helper_overlays(self):
        """Overlays (this target's java/) of a class the shared library supplies: a fix there belongs in the library."""
        mv_pkg = read_props(self.repo / ".multiversion/base.properties").get("mv_package", "")
        names = {p.name for p in (self.repo / ".multiversion/shared").glob("*/java/**/*.java")}
        root = self.tdir / "java" / mv_pkg.replace(".", "/")
        return sorted(str(p.relative_to(self.repo)) for p in root.glob("*.java") if p.name in names) if mv_pkg else []

    def compile_errors(self):
        """The compile count the audit and gates rely on, FROM THIS RUN. A count carried over from an earlier run's
        summary is not trusted: measured, a resume from 'audit' read a stale count, skipped the audit and every
        gate, and reported the target done. So unless this run measured (or looped), measure again now."""
        st = self.summary["stages"]
        for k in ("loop", "measure"):
            v = st.get(k, {})
            if v.get("this_run") and isinstance(v.get("errors"), int):
                return v["errors"]
        res = self.s_measure()
        res["this_run"] = True
        st["measure"] = {k: v for k, v in res.items() if k not in ("view", "esc", "stop", "choices", "code", "detail")}
        self.say("measure", res.get("done") or res.get("stop") or "re-measured")
        return res.get("errors")

    def not_compiled(self, n, stage):
        """Never skip the audit or the gates quietly: a target that does not compile is not done."""
        return {"stop": f"{stage} needs a clean compile; this run measured {n if n is not None else 'no count'}",
                "code": 10, "detail": [f"compile errors: {n}"],
                "choices": [f"resume from measure: {self.resume_cmd('measure')}"]}

    def s_audit(self):
        n = self.compile_errors()
        if n != 0:
            return self.not_compiled(n, "audit")
        neo = read_props(self.tdir / "target.properties").get("prop.neo_version", "")
        jars = [j for j in self.artifacts()[1] if neo in j.name] or self.artifacts()[1]
        if not jars:
            return {"stop": "no staged sources jar to audit against", "code": 13, "choices": ["stage Minecraft first"]}
        prepared = self.repo / "build" / f"mc{self.t}" / "mv" / "java"
        r = self.sh([self.py, str(ROOT / "tools/audit-mixin-targets.py"), str(self.repo), str(jars[0]),
                     f"--sources={prepared}"])
        lines = (r.stdout or "").strip().splitlines()
        if r.returncode == 0:
            ev = self.audit_event_listeners()
            if ev.get("stop"):
                return ev
            return {"done": ((lines[-1] if lines else "all targets match")[:120] + "; " + ev["done"])[:200]}
        if r.returncode == 2 or any("NO TARGETS CHECKED" in l for l in lines):
            # the audit looked at nothing: a scope fault in this run, never a verdict on the mixins (§X27)
            return {"stop": "the mixin audit checked no targets (wrong jar or tree?) -- not a pass",
                    "code": 13, "audit": lines[:8], "detail": lines[:4],
                    "choices": ["fix the audit's inputs in tools/fork-port.py and resume with --from-stage audit"]}
        # keep each mismatch WITH its header line (the mixin file and the annotated target): a "mixin:/vanilla:"
        # pair alone does not say which file to fix -- measured, the first residue report could not be acted on
        bad = [l for l in lines if l.strip() and not l.strip().startswith(("ok ", "checked ")) and "all targets" not in l] or lines
        return {"stop": f"{len([l for l in lines if 'mixin:' in l]) or len(bad)} mixin target(s) do not match vanilla",
                "code": 13, "audit": bad, "detail": bad[:6],
                "choices": ["retarget each mixin in a per-target overlay or with mixin_add (fork-versions step 5)",
                            "a recurring shape belongs in the shared library"]}

    def target_classpath(self):
        """Gradle's own resolved compile classpath for this layer target (tools/qtc-init.gradle, passing the layer's
        -P args through .multiversion/build.sh), cached under the run's work dir. Never rebuilt by hand (X25b-ii)."""
        cp = self.work / f"qtc-classpath-{self.t}.txt"
        self.work.mkdir(parents=True, exist_ok=True)
        r = self.sh([BASH, str(self.mv / "build.sh"), self.t, "-I", str(ROOT / "tools/qtc-init.gradle"),
                     "-I", str(ROOT / "tools/central-mirror.init.gradle"), "-q", "qtcClasspath", "--console=plain"])
        m = re.search(r"QTC_CLASSPATH_BEGIN\n(.*?)QTC_CLASSPATH_END", r.stdout or "", re.S)
        jars = [l.strip() for l in m.group(1).splitlines() if l.strip()] if m else []
        if not jars:
            return None, "could not resolve the %s classpath: %s" % (self.t, (r.stdout or "")[-300:])
        gone = [j for j in jars if not pathlib.Path(j).exists()]
        if gone:
            return None, "the %s classpath names %d file(s) that do not exist, e.g. %s" % (self.t, len(gone), gone[0])
        cp.write_text("\n".join(jars) + "\n", encoding="utf-8")
        return cp, ""

    def audit_event_listeners(self):
        """audit-event-listeners.py (CATALOG X33/X42) over every prepared tree of the target, against its own classpath:
        a listener naming an ABSTRACT event compiles everywhere and kills mod loading (measured on 1.21.8)."""
        trees = sorted(str(p) for p in (self.repo / "build" / f"mc{self.t}" / "mv").glob("*java") if p.is_dir())
        if not trees:
            return {"stop": "no prepared tree to audit event listeners in", "code": 13,
                    "choices": ["build the target once so build/mc<t>/mv exists"]}
        cp, why = self.target_classpath()
        if cp is None:
            return {"stop": "event-listener audit: " + why, "code": 13, "detail": [why],
                    "choices": ["fix the classpath resolution; this audit refuses to run on a guessed one"]}
        r = self.sh([self.py, str(ROOT / "tools/audit-event-listeners.py"), str(self.repo), f"--cp-file={cp}",
                     f"--tree={','.join(trees)}", f"--mc={self.t}"])
        lines = (r.stdout or "").strip().splitlines()
        if r.returncode == 0:
            return {"done": (lines[0] if lines else "event listeners ok")[:100]}
        if r.returncode == 2:
            return {"stop": "the event-listener audit checked no listeners (wrong tree or classpath?) -- not a pass",
                    "code": 13, "audit": lines[:8], "detail": lines[:4],
                    "choices": ["fix the audit's inputs in tools/fork-port.py and resume with --from-stage audit"]}
        bad = [l for l in lines if l.strip() and not l.startswith("checked ")]
        return {"stop": f"{sum('ABSTRACT' in l for l in bad)} event listener(s) name an abstract event "
                        f"(the bus refuses them at load)",
                "code": 13, "audit": bad, "detail": bad[:6],
                "choices": ["port each listener to a concrete subclass of the event in a per-target overlay "
                            "(CATALOG X33: check the subclass still carries what the listener uses)"]}

    def modid(self):
        if self.a.modid:
            return self.a.modid
        f = self.repo / "src/main/resources/META-INF/neoforge.mods.toml"
        return parse_modid(f.read_text(encoding="utf-8")) if f.is_file() else None

    def s_gates(self):
        if self.a.gates == "none":
            return {"skipped": "--gates none"}
        n = self.compile_errors()
        if n != 0:
            return self.not_compiled(n, "gates")
        modid = self.modid()
        if not modid:
            return {"stop": "cannot read the mod id from neoforge.mods.toml (pass --modid)", "code": 2, "choices": ["--modid ID"]}
        summ = self.work / "ci-gates-summary.md"
        # a fresh gate work dir every run: ci-gates reuses an existing harness, and a harness generated by an
        # older version of the tooling (measured: one from before the 1.21.5 GameTest adapter) fails the same way
        # forever while the tool that would fix it is never asked
        shutil.rmtree(self.work / "gates", ignore_errors=True)
        cmd = [self.py, str(ROOT / "tools/ci-gates.py"), "--repo", str(self.repo), "--modid", modid, "--base",
               self.a.base, "--mc", self.t, "--summary", str(summ), "--work-dir", str(self.work / "gates")]
        if self.a.gates == "ab":
            cmd += ["--gatec", ""]
        if any((self.repo / ".multiversion").glob("shipped-expect*.txt")):    # the fork lists what its shipped jar must log
            cmd += ["--shipped"]
        r = self.sh(cmd, self.work / "ci-gates.log")
        lines = (self.work / "ci-gates.log").read_text(encoding="utf-8", errors="replace").splitlines()
        rows = [re.sub(r"^\[ci-gates\] ", "", l) for l in lines if l.startswith("[ci-gates]") and ": " in l]
        failed = [l for l in rows if re.search(r": FAIL\b", l)]
        passed = [l for l in rows if re.search(r": PASS\b", l)]
        if r.returncode == 0:
            lost, prev = self.lost_logs()
            if lost:
                msg = "; ".join(f"the mod wrote {f} on {prev} and not on {self.t}: a subsystem stopped running" for f in lost)
                return {"stop": f"gates green but {msg}", "code": 11, "gates": [f"log file {f} (written on {prev}, absent on {self.t})" for f in lost],
                        "detail": [msg[:240]],
                        "choices": ["find what writes that file (grep the source for its name), and why it no longer runs on this target "
                                    "(CATALOG X47: a registration the new loader ignores is the usual cause)",
                                    "if the file is gone on purpose, list it in .multiversion/targets/%s/logs-known-absent.txt with the reason" % self.t]}
            note = f"; log files match {prev}" if prev else ""
            return {"done": f"{len(passed)} gate(s) green ({self.a.gates}){note}"}
        code = 12 if any("Gate C" in l for l in failed) else 11
        evidence = []       # why each red gate is red: its own log's first compile errors / failing tests
        for l in failed[:2]:
            lp = re.search(r"see (\S+?)(?:[;)\s]|$)", l)
            lp = pathlib.Path(lp.group(1)) if lp else None
            if lp and lp.is_file():
                evidence += [f"{l.split(':')[0][:40]}: {e}" for e in gate_evidence(lp.read_text(encoding="utf-8", errors="replace"))]
        return {"stop": f"{len(failed) or 'a'} gate(s) red: " + "; ".join(l[:90] for l in failed[:2]), "code": code,
                "gates": [l[:200] for l in failed], "detail": [l[:200] for l in failed[:4]] + [e[:240] for e in evidence],
                "choices": ["run the same gates without --mc as the control: a red that is red on the base is not this target's",
                            "read build/mc%s/fork-port/ci-gates.log and the gate's own log" % self.t]}

    def lost_logs(self):
        """(files, previous target) -- the mod's own log files from the inherited target's last gate run that this run lacks."""
        inh = read_props(self.tdir / "target.properties").get("inherits")
        if not inh:
            return [], None
        prev = self.repo / "build" / f"mc{inh}" / "fork-port" / "gates" / "harness" / "run" / "logs"
        if not prev.is_dir():
            return [], None
        known = pathlib.Path(self.tdir / "logs-known-absent.txt")
        absent = [l.split("#", 1)[0].strip() for l in known.read_text(encoding="utf-8").splitlines()] if known.is_file() else []
        return lost_log_files(prev, self.work / "gates" / "harness" / "run" / "logs", [a for a in absent if a]), inh

    def s_cost(self):
        modid = self.modid()
        if not modid:
            return {"skipped": "no mod id"}
        since = self.summary["started"]
        r = self.sh([self.py, str(ROOT / "tools/port-cost.py"), modid, "--path", str(self.repo), "--target", self.t,
                     "--since", since, "--print"])
        if r.returncode != 0 or not (r.stdout or "").strip():
            return {"skipped": "no transcripts for this checkout (port-cost exit %d)" % r.returncode}
        f = self.tdir / "COST.md"
        old = f.read_text(encoding="utf-8") if f.is_file() else ""
        f.write_text(old.rstrip("\n") + ("\n\n" if old else "") + f"## fork-port.py run since {since}\n\n" + r.stdout.strip() + "\n",
                     encoding="utf-8")
        return {"done": f"appended to {f.relative_to(self.repo)}"}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo"); ap.add_argument("--target")
    ap.add_argument("--budget", type=float, default=15.0, help="dollars the compile loop's workers may spend")
    ap.add_argument("--gates", default="ab", choices=["none", "ab", "full"])
    ap.add_argument("--json"); ap.add_argument("--from-stage"); ap.add_argument("--modid")
    ap.add_argument("--base", default="origin/neoforge-1.21.1", help="the authors' ref, for ci-gates.py --base")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not a.repo or not a.target:
        ap.error("--repo and --target are required")
    if not pathlib.Path(a.repo).is_dir():
        print(f"fork-port: no such directory: {a.repo}"); return 2
    try:
        skipped, todo = select_stages(a.from_stage)
    except ValueError as e:
        print(f"fork-port: {e}"); return 2
    run = Run(a)
    if run.jpath.is_file() and skipped:    # a resume keeps what the earlier stages found
        try:
            old = json.loads(run.jpath.read_text(encoding="utf-8"))
            # carried-over results are an earlier run's: drop this_run so compile_errors() re-measures
            run.summary["stages"] = {k: {x: y for x, y in v.items() if x != "this_run"}
                                     for k, v in old.get("stages", {}).items() if k in skipped}
            run.summary["started"] = old.get("started", run.summary["started"])
        except ValueError:
            pass
    for s in skipped:
        run.summary["stages"].setdefault(s, {"skipped": f"before --from-stage {a.from_stage}"})
    stop = None
    for s in todo:
        res = getattr(run, "s_" + s)()
        run.summary["stages"][s] = {k: v for k, v in res.items() if k not in ("view", "esc", "stop", "choices", "code", "detail")}
        if "stop" in res:
            run.summary["stages"][s]["stopped"] = res["stop"]
            stop = dict(res, stage=s, left=res["stop"])
            break
        run.say(s, res.get("done") or ("skipped: " + res.get("skipped", "")))
    run.summary["done"] = stop is None
    if stop:
        stop.setdefault("spent", 0)
        resume = run.resume_cmd(stop["stage"])
        view = stop.get("view") or stop.get("detail") or []
        md, data = residue_report(stop["stage"], stop["left"], view if stop["stage"] in ("loop", "measure", "stage", "moves") else [],
                                  stop.get("esc"), stop.get("audit") or [], stop.get("gates") or [], resume)
        rpath = run.tdir / "RESIDUE.md"
        if run.tdir.is_dir():
            rpath.write_text(md, encoding="utf-8")
        stop["residue"] = str(rpath.relative_to(run.repo)) if run.tdir.is_dir() else "(no target dir)"
        run.summary["residue"] = data
        run.summary["stop"] = {"stage": stop["stage"], "code": stop["code"], "left": stop["left"], "resume": resume}
        run.save()
        print(stop_block(stop))
        print(f"fork-port: summary in {run.jpath}")
        return stop["code"]
    r = run.tdir / "RESIDUE.md"
    if r.is_file():
        r.unlink()
    run.save()
    print(f"fork-port {a.target}: done -- summary in {run.jpath}")
    return 0


def self_check():
    ok = True

    def expect(c, msg):
        nonlocal ok
        if not c:
            ok = False; print("FAIL:", msg)
    # stage selection
    expect(select_stages(None) == ([], STAGES), "no --from-stage runs everything")
    sk, td = select_stages("loop")
    expect(sk == ["setup", "stage", "moves", "measure"] and td[0] == "loop" and td[-1] == "cost", "from-stage loop")
    try:
        select_stages("nope"); expect(False, "unknown stage must raise")
    except ValueError:
        pass
    # the moves table: existing rows dropped (X31), exhaustive block closed (X11), tab-separated
    raw = [("net.minecraft.a.Zombie", "net.minecraft.a.z.Zombie"), ("net.minecraft.a.Cow", "net.minecraft.a.c.Cow")]
    text, kept, dup = moves_table(raw, {"net.minecraft.a.Cow"}, "1.21.4", "1.21.5")
    lines = text.splitlines()
    expect(kept == 1 and dup == 1 and "net.minecraft.a.Zombie\tnet.minecraft.a.z.Zombie" in lines
           and "net.minecraft.a.Cow\tnet.minecraft.a.c.Cow" not in lines, "moves table rows")
    expect(lines.index("#!strict") > max(i for i, l in enumerate(lines) if l.startswith("#!exhaustive")), "block closed")
    expect(plain_froms("a.B\tc.D\nre:x\ty\nmember:z\tq\n# c\n") == {"a.B"}, "plain_froms skips re:/member:")
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d, "m.tsv"); p.write_text(text, encoding="utf-8")
        expect(read_pairs(p) == [("net.minecraft.a.Zombie", "net.minecraft.a.z.Zombie")], "table reads back as pairs")
    # layer.py patch decision, against the real template
    tpl = (ROOT / "templates/fork-layer/tools/layer.py").read_text(encoding="utf-8")
    expect(INSERT in tpl, "template layer.py carries the moves.generated.tsv block")
    old = tpl.replace(INSERT, "")
    expect(layer_patch(old, tpl)[0] == "replace" and layer_patch(tpl, tpl)[0] == "current"
           and layer_patch(old + "# hand edit\n", tpl)[0] == "diverged", "layer.py patch verdicts")
    expect(ANCHOR + INSERT in tpl, "moves block sits right after the target's own rows")
    # chain / modid
    with tempfile.TemporaryDirectory() as d:
        mv = pathlib.Path(d)
        for t, inh in (("1.21.4", None), ("1.21.5", "1.21.4")):
            (mv / "targets" / t).mkdir(parents=True)
            (mv / "targets" / t / "target.properties").write_text(f"inherits={inh}\n" if inh else "x=1\n", encoding="utf-8")
        expect(chain(mv, "1.21.5") == ["1.21.5", "1.21.4"], "chain")
    expect(parse_modid('[[mods]]\nmodId = "eca"\n[[dependencies.eca]]\nmodId = "neoforge"\n') == "eca", "modid")
    # the residue builder over a canned compile log, plus a canned loop log
    cs = load("compile_summary", "compile-summary.py")
    log = ("> Task :compileJava\n/w/build/mc1.21.5/mv/java/m/A.java:3: error: cannot find symbol\n  symbol:   class Foo\n"
           "/w/build/mc1.21.5/mv/java/m/A.java:9: error: cannot find symbol\n  symbol:   class Foo\n"
           "/w/build/mc1.21.5/mv/java/m/B.java:4: error: incompatible types: Bar cannot be converted to Baz\n"
           "3 errors\nBUILD FAILED\n")
    s = cs.summarise(log, cs.rb.signatures())
    view = cs.render(s, None, 8, 8)
    expect(s["total"] == 3 and view and "3 errors in 2 files" in view[0], "canned compile summary")
    evs = [{"event": "start", "errors": 3},
           {"event": "worker", "model": "haiku", "files": ["x/java/m/A.java"], "usd": 0.1},
           {"event": "worker", "model": "opus", "files": [".multiversion/targets/1.21.5/java/m/A.java"], "usd": 1.0},
           {"event": "rule", "row": "a\tb", "kept": False, "why": "errors grew", "errors": 5},
           {"event": "rule", "row": "c\td", "kept": True, "why": "", "errors": 2},
           {"event": "stub-signals", "files": [{"file": "m/B.java", "why": "emptied"}]},
           {"event": "end", "errors": 3, "spent": 1.1}]
    esc = escalation(evs, set(s["files"]))
    expect(esc["top_tier_still_failing"] == ["m/A.java"] and esc["rows_kept"] == ["c\td"]
           and esc["rows_reverted"][0][0] == "a\tb" and esc["stubs"] == ["m/B.java"], "escalation record")
    md, data = residue_report("loop", "3 compile errors", view, esc, [], [], "python3 tools/fork-port.py --from-stage loop")
    expect("## Remaining compile errors" in md and "--from-stage loop" in md and "m/A.java" in md
           and "## The loop's record" in md and "BUILD FAILED" not in md, "residue md")
    expect(len(md.splitlines()) < 60 and data["resume"].endswith("--from-stage loop"), "residue is bounded")
    b = stop_block({"stage": "loop", "left": "3 compile errors", "spent": 1.1, "detail": ["x"],
                    "residue": ".multiversion/targets/1.21.5/RESIDUE.md", "choices": ["a", "b"]})
    expect(b.startswith("STOPPED at loop: 3 compile errors") and "Residue: " in b and "do not pick one silently" in b, "stop block")
    with tempfile.TemporaryDirectory() as d:
        lg = pathlib.Path(d, "l.jsonl")
        lg.write_text("\n".join(json.dumps(e) for e in [{"event": "start"}, {"event": "worker", "usd": 9}] + evs) + "\n", encoding="utf-8")
        expect(loop_events(lg)[0]["event"] == "start" and len(loop_events(lg)) == len(evs), "only the last run's events")
    ev = gate_evidence("noise\n/w/x/y/java/net/eca/gatetest/BaselineGameTest.java:15: error: cannot find symbol\n"
                       "/w/x/y/java/net/eca/gatetest/BaselineGameTest.java:15: error: cannot find symbol\n"
                       "  /w/x/y/java/net/eca/Other.java:9: error: incompatible types: A cannot be converted to B\n")
    expect(ev == ["eca/gatetest/BaselineGameTest.java:15: cannot find symbol",
                  "net/eca/Other.java:9: incompatible types: A cannot be converted to B"], "gate_evidence compile errors: %s" % ev)
    expect(gate_evidence("> Task :test FAILED\n")[0].endswith("FAILED") and gate_evidence("all fine") == [], "gate_evidence tests/none")
    # CATALOG X47: the mod's own log files, compared between the inherited target and this one
    with tempfile.TemporaryDirectory() as d:
        prev, cur = pathlib.Path(d, "prev"), pathlib.Path(d, "cur")
        for n in ("latest.log", "debug.log", "2026-10-12-1.log.gz", "2026-10-12-2.log", "EcaBootstrap.log", "EcaAgent.log"):
            (prev.mkdir(exist_ok=True) or prev / n).write_text("x")
        for n in ("latest.log", "debug.log"):
            (cur.mkdir(exist_ok=True) or cur / n).write_text("x")
        expect(mod_log_files(prev) == {"EcaBootstrap.log", "EcaAgent.log"}, "mod_log_files skips vanilla's and the dated archives: %s" % mod_log_files(prev))
        expect(lost_log_files(prev, cur) == ["EcaAgent.log", "EcaBootstrap.log"], "A: the 1.21.10 state (mod logs missing) is flagged")
        expect(lost_log_files(prev, cur, ["EcaAgent.log"]) == ["EcaBootstrap.log"], "a recorded known-absent file is not flagged")
        for n in ("EcaBootstrap.log", "EcaAgent.log", "NewOnlyHere.log"):
            (cur / n).write_text("x")
        expect(lost_log_files(prev, cur) == [], "B: after the fix (same set, plus a new file) it passes")
        expect(lost_log_files(None, cur) == [] and lost_log_files(pathlib.Path(d, "nope"), cur) == [], "no previous run: nothing to compare")
    # a resume must not trust a carried-over compile count (measured: it skipped audit + gates and said "done")
    r_ = object.__new__(Run)
    r_.summary = {"stages": {"measure": {"errors": 0}}}            # an earlier run's 0, no this_run
    r_.s_measure = lambda: {"done": "5 error(s)", "errors": 5, "this_run": True}
    r_.say = lambda *a_: None
    r_.resume_cmd = lambda st: "resume " + st
    expect(r_.compile_errors() == 5, "a stale count is re-measured")
    res_ = Run.not_compiled(r_, 5, "audit")
    expect(res_.get("code") == 10 and "stop" in res_, "not compiled is a stop, never a skip")
    r_.summary = {"stages": {"measure": {"errors": 0, "this_run": True}}}
    r_.s_measure = lambda: (_ for _ in ()).throw(AssertionError("must not re-measure"))
    expect(r_.compile_errors() == 0, "this run's own count is used as is")
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
