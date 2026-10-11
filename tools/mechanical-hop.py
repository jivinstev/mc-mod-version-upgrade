#!/usr/bin/env python3
"""The deterministic tail of a hop, shared by BOTH porting routes, run before any model sees an error.

  --stage forge  (Forge 1.20.x -> NeoForge 1.21.1, after the remap, codemods and recipe pack): the access
                 transformer made valid (official names, override widenings, one compile per subclass level),
                 forge-shapes, convert-simplechannel, then fix-holders until a compile round fixes nothing.
  --stage era    (1.21.1 -> 26.x, after the rename table): the access transformer re-pointed through the class-move
                 map, the member pass, then every converter, then the member pass again.

    python3 tools/mechanical-hop.py --work <port> --stage forge|era [--target 26.2] [--modid ID] [--json report.json]
        [--render-state-type T --render-state-factory F] [--shader-block NAME] [--lib-tree DIR ...]
    python3 tools/mechanical-hop.py --self-check

WHO CALLS IT. tools/port-upstream.py (a fork) imports run_forge_stage() / at_loop() / run_stage(); tools/port.py
(a jar: after setup and the recipe pack for a Forge hop, after tools/era-hop.py flattened the tree for an era hop)
runs this command. One list per stage, so a converter added here reaches both routes; tools/test-port-tools.sh
fails if either route stops going through it or names a converter itself.

ORDER. members -> each converter in CONVERTERS order -> members again. The member pass needs javac's errors
(fix-missing-members.py renames a member only where javac named its owner type), so the stage compiles between
steps; a converter that changed nothing is not recounted. A converter that cannot rewrite a site exactly
REFUSES it and says so: the stage never guesses. Some need what only the port knows (a render-state type, a
shader uniform block): without it they are skipped with an advisory instead of run wrongly.

NOT FOR a two-target (§W, `era-hop --keep-old`) tree: converters write target-only code into the files they edit,
which would break the other target. Refused when the tree still has a versions/1.21.1.properties.
Standard library only (the converters it runs are too).
"""
import argparse, os, hashlib, importlib.util, json, pathlib, re, shutil, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

# (label, script, how to call it per source root; a condition key or None). The ONE list both routes run.
CONVERTERS = [
    ("gui-hooks", "convert-gui-hooks.py", None),
    ("valueio", "convert-valueio.py", None),
    ("attachment-io", "convert-attachment-io.py", None),
    ("override-signatures", "convert-override-signatures.py", None),
    ("reload-weighted", "convert-reload-weighted.py", "modid"),
    ("client-hooks", "convert-client-hooks.py", "modid"),
    ("saved-data", "convert-saved-data.py", "modid"),
    ("gear-tiers", "convert-gear-tiers.py", "assets"),
    ("rendertypes", "convert-rendertypes.py", "rendertypes"),
    ("core-shaders", "convert-core-shaders.py", "shaders"),
    ("entity-renderstate", "convert-entity-renderstate.py", "context"),
    ("geckolib", "convert-geckolib.py", "geckolib"),
    # last: the converters above pattern-match render hooks by their MultiBufferSource parameter
    ("buffer-seam", "convert-buffer-seam.py", "modid"),
]


TAKES_PACKAGE = {"convert-attachment-io.py", "convert-buffer-seam.py", "convert-client-hooks.py", "convert-gear-tiers.py",
                 "convert-reload-weighted.py", "convert-saved-data.py"}


def mod_package(dirs):
    """The @Mod class's package, searched across every source set. None when there is none."""
    for d in dirs:
        for f in sorted(pathlib.Path(d).rglob("*.java")):
            text = f.read_text(encoding="utf-8", errors="replace")
            if re.search(r"(?m)^\s*@(?:net\.neoforged\.fml\.common\.)?Mod\s*\(", text):
                m = re.search(r"(?m)^package\s+([\w.]+)\s*;", text)
                if m:
                    return m.group(1)
    return None


def _load(name, file):
    sp = importlib.util.spec_from_file_location(name, ROOT / "tools" / file)
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


def run_tool(name, *args):
    r = subprocess.run([sys.executable, str(ROOT / "tools" / name), *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout + r.stderr).strip()


def fingerprint(repo):
    """The content of every source and resource file: did a converter change anything? (no git needed)"""
    h = hashlib.sha1()
    for f in sorted(pathlib.Path(repo, "src").rglob("*")):
        if f.is_file():
            h.update(str(f.relative_to(repo)).encode("utf-8"))
            h.update(f.read_bytes())
    return h.hexdigest()


def headline(out, stem, label):
    lines = [l for l in out.splitlines() if l.strip()]
    own = [l.strip() for l in lines if l.startswith(stem) and not l.rstrip().endswith(":")]
    if own:
        return own[-1]
    m = next((re.match(r"\s+(\d+)\s+files? changed", l) for l in lines if re.match(r"\s+(\d+)\s+files? changed", l)), None)
    if m:
        return f"{label}: {m.group(1)} file(s) changed"
    return lines[-1].strip() if lines else f"{label}: no output"


def uses_geckolib(repo, dirs):
    targets = _load("targets", "targets.py")
    bg = pathlib.Path(repo, "build.gradle")
    if targets.uses_geckolib(bg.read_text(encoding="utf-8", errors="replace") if bg.exists() else ""):
        return True
    return any(targets.uses_geckolib(f.read_text(encoding="utf-8", errors="replace")) for d in dirs for f in d.rglob("*.java"))


def plan(repo, dirs, modid, render_state_type=None, render_state_factory=None, shader_block=None, lib_trees=()):
    """-> ([(label, script, args_for(dir, index))], advisories): CONVERTERS with each one's arguments, and the
    ones that cannot run without something only the port knows, as advisories."""
    repo = pathlib.Path(repo)
    text = "\n".join(f.read_text(encoding="utf-8", errors="replace") for d in dirs for f in d.rglob("*.java"))
    assets = repo / "src/main/resources/assets"
    ctx = [x for lib in lib_trees for x in ("--context", lib)]
    idx = [x for lib in lib_trees for x in ("--index", lib)]
    out, adv = [], []
    for label, script, cond in CONVERTERS:
        if cond is None:
            fn = lambda d, i: ["--src", d]
        elif cond == "assets":
            fn = lambda d, i: ["--src", d, *(["--assets", assets] if i == 0 and assets.is_dir() else [])]
        elif cond == "rendertypes":
            if "CompositeState" not in text:
                continue
            custom = re.search(r"new\s+(?:RenderStateShard\.)?ShaderStateShard\s*\(", text)
            if custom and not (render_state_type and render_state_factory):
                adv.append("custom ShaderStateShard in the render types: convert-rendertypes needs the port's own "
                           "state type and factory (--render-state-type / --render-state-factory); not run")
                continue
            st, sf = (render_state_type, render_state_factory) if custom else ("unused.State", "unused.state")
            fn = lambda d, i, st=st, sf=sf: ["--src", d, "--state-type", st, "--state-factory", sf]
        elif cond == "shaders":
            sh_dir = repo / f"src/main/resources/assets/{modid}/shaders"
            if not (sh_dir.is_dir() and any(sh_dir.glob("core/*.json"))):
                continue
            if not shader_block:
                adv.append("core shader programs present: pass --shader-block <UniformBlockName> to run convert-core-shaders "
                           "(26.x reads no program JSON and binds uniform blocks; the Java that fills the block is the port's to write)")
                continue
            fn = lambda d, i, sh_dir=sh_dir: ["--shaders", sh_dir, "--ns", modid, "--block", shader_block] if i == 0 else None
        elif cond == "modid":
            fn = lambda d, i: ["--src", d, "--modid", modid]
        elif cond == "context":
            fn = lambda d, i: ["--src", d, *ctx]
        elif cond == "geckolib":
            if not uses_geckolib(repo, dirs):
                continue
            fn = lambda d, i: ["--src", d, *(["--assets", assets] if i == 0 and assets.is_dir() else []), *idx]
        out.append((label, script, fn))
    return out, adv


def run_stage(repo, dirs, members_table, workdir, modid, compile_count, start_errors, say=print,
              run=run_tool, fp=fingerprint, render_state_type=None, render_state_factory=None, shader_block=None,
              lib_trees=()):
    """members -> CONVERTERS -> members. compile_count(log) -> (errors or None, line) is the caller's (each route
    compiles its own way). -> (steps, advisories); each step {step, summary, errors}."""
    repo, workdir = pathlib.Path(repo), pathlib.Path(workdir)
    steps, last = [], {"n": start_errors}

    def member_loop(label):
        fixed, n = 0, last["n"]
        for r in range(6):
            log = workdir / f"era-{label}-{r}.log"
            n, _ = compile_count(log)
            if n is None:
                raise RuntimeError(f"compile did not run during {label}: see {log.name}")
            if n == 0:
                break
            got = 0
            for d in dirs:
                _rc, o = run("fix-missing-members.py", "--src", d, "--log", log, "--table", members_table)
                got += sum(int(x) for x in re.findall(r"(\d+) site\(s\) rewritten", o))
            fixed += got
            if not got:
                break
        last["n"] = n
        steps.append({"step": label, "errors": n, "summary": f"{fixed} member site(s) renamed where javac named the owner"})
        say(f"{label}: {steps[-1]['summary']} -> {n} errors")

    member_loop("members")
    todo, advisories = plan(repo, dirs, modid, render_state_type, render_state_factory, shader_block, lib_trees)
    print_fp = [fp(repo)]
    # the generated helpers' package, found ONCE across every source set: a converter run on a source set that
    # holds no @Mod class (a client or datagen set) otherwise stops with "no @Mod class found"
    pkg = mod_package(dirs)
    if not pkg:                     # no @Mod class in the compiled tree: the scaffold's group is the mod's package
        gp = pathlib.Path(repo) / "gradle.properties"
        pkg = (re.findall(r"(?m)^mod_group_id\s*=\s*(\S+)", gp.read_text(encoding="utf-8")) or [None])[0] if gp.exists() else None
    for label, script, args_for in todo:
        outs = []
        for i, d in enumerate(dirs):
            args = args_for(d, i)
            if args is None:
                continue
            if pkg and script in TAKES_PACKAGE:
                args = [*args, "--package", pkg + ".compat"]
            rc, o = run(script, *args)
            if rc not in (0, 1):
                raise RuntimeError(f"{script} failed: {o[-600:]}")
            outs.append(o)
        joined = "\n".join(outs)
        heads = list(dict.fromkeys(headline(o, "convert-", script[:-3]) for o in outs))
        (workdir / f"era-{label}.out").write_text(joined + "\n", encoding="utf-8")
        now = fp(repo)
        changed, print_fp[0] = now != print_fp[0], now
        nref = sum(1 for l in joined.splitlines() if "REFUSED" in l and not re.search(r"REFUSED \d+ site", l))
        summary = " | ".join(heads) + (f"; {nref} REFUSED (see era-{label}.out)" if nref else "")
        n = before = last["n"]
        if changed:
            n, _ = compile_count(workdir / f"era-{len(steps)}-{label}.log")
            if n is None:
                raise RuntimeError(f"compile did not run after {label}")
        last["n"] = n
        steps.append({"step": label, "summary": summary, "errors": n, **({"raised_from": before} if n > before else {})})
        say(f"{label}: {summary}" + (f" -> {n} errors" if changed else " (no change, no recount)")
            + (f"  ** RAISED from {before}: a converter regression, or errors it unmasked -- compare "
               f"era-{len(steps) - 1}-{label}.log with the previous log **" if n > before else ""))
    member_loop("members-after-converters")
    return steps, advisories


def at_loop(repo, compile_count, workdir, run=run_tool):
    """The access transformer has to be valid before Minecraft recompiles at all. Override widenings appear one
    subclass level per compile: compile, widen what the log names, repeat. -> errors, or None if it never compiled.
    A dependency that does not resolve is reported as that, not blamed on the AT."""
    has_at = any(pathlib.Path(repo).glob("src/*/resources/META-INF/accesstransformer.cfg"))
    for k in range(4):
        log = pathlib.Path(workdir) / f"at-{k}.log"
        n, _ = compile_count(log)
        if n is not None:
            return n
        text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        unresolved = sorted(set(re.findall(r"Could not resolve ([\w.\-]+:[\w.\-]+:[^\s.]+[^\s]*)\.", text)))
        if unresolved:
            raise RuntimeError("dependencies do not resolve: " + ", ".join(unresolved[:6]) + " -- wrong coordinates for "
                               "the target, or a maven this machine cannot reach (tools/local-maven.py); see " + log.name)
        if "NoSuchFileException" in text and "/.gradle/repositories/" in text:
            healed = heal_ng_cache(repo)
            if healed:                                  # the daemon holds the old entry in memory: stop it, then retry
                subprocess.run(["bash", "gradlew", "--stop"], cwd=repo, capture_output=True)
                continue
        if not has_at:      # the stale-cache heal above is the only repair that applies without an AT (X71)
            return None
        if "daemon has disappeared" in text or "OutOfMemoryError" in text:   # not the AT's fault: say so
            raise RuntimeError(f"the Gradle daemon died while recompiling Minecraft (memory?), not an AT problem; "
                               f"rerun the stage (see {log.name})")
        src_err = re.search(r"(\S+\.java:\d+: error: [^\n]+)", text)
        if src_err and "neoForm" not in src_err.group(1):     # Minecraft compiled; the MOD's own sources did not
            raise RuntimeError(f"the mod's own sources do not compile (Minecraft's recompile is not the problem): "
                               f"{src_err.group(1)[-220:]} -- see {log.name}")
        _rc, out = run("fix-access-transformer.py", "--work", repo, "--overrides-from", log)
        if " 0 override" in out:
            raise RuntimeError(f"Minecraft's recompile fails and no access-transformer override explains it (see {log.name})")
    return None


def heal_ng_cache(repo, home=None):
    """NeoGradle caches a recompile step machine-wide (<gradle home>/caches/ng_execute/<hash>/libraries.txt), and
    that list names files inside the .gradle/repositories/ of whichever workspace first ran it. Delete that
    workspace and every later port whose access transformer forces the step dies with NoSuchFileException on a
    path in a project that no longer exists. The file is the same in every workspace of that Minecraft version
    (a dummy repository of Minecraft's own jars), so copy this workspace's file to a STABLE place in the Gradle
    home and point the stale entries there -- pointing them at this workspace would only move the bug to the day
    this workspace is deleted. The caller must stop the Gradle daemon before retrying: a running daemon keeps
    the entry in memory and never rereads the file (measured: the rewrite alone left the retry failing).
    -> number of paths rewritten (0: nothing to heal, the failure is something else)."""
    home = pathlib.Path(home or os.environ.get("GRADLE_USER_HOME") or pathlib.Path.home() / ".gradle")
    stable = home / "caches/ng-heal"
    n = 0
    # the cache entry, and every copy of it a workspace's own build already made (a retry reads that copy)
    libs = list((home / "caches/ng_execute").glob("*/libraries.txt")) + list(pathlib.Path(repo).glob("build/neoForm/**/libraries.txt"))
    for lib in libs:
        text = lib.read_text(encoding="utf-8", errors="replace")
        def fix(m):
            nonlocal n
            path = m.group(0)
            if pathlib.Path(path).exists():
                return path
            rel = path.split("/.gradle/repositories/", 1)[1]
            mine, keep = pathlib.Path(repo) / ".gradle/repositories" / rel, stable / rel
            if not keep.exists():
                if not mine.exists():
                    return path
                keep.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(mine, keep)
            n += 1
            return str(keep)
        new = re.sub(r"[^\s=]+/\.gradle/repositories/[^\s]+", fix, text)
        if new != text:
            lib.write_text(new, encoding="utf-8")
    return n


def era_access_transformer(repo, moves, run=run_tool):
    """Re-point the access transformer's class names through the 1.21.1 -> 26.x class-move map. -> its output"""
    if not any(pathlib.Path(repo).glob("src/*/resources/META-INF/accesstransformer.cfg")):
        return "fix-access-transformer: no access transformer"
    return run("fix-access-transformer.py", "--work", repo, "--class-map", moves)[1]


FORGE_STEPS = [("shapes", "forge-shapes.py"), ("net", "convert-simplechannel.py")]


FORGE_MEMBERS = "tools/recipes/forge-1.21.1-members.tsv"


def run_forge_stage(repo, dirs, modid, workdir, compile_count, run=run_tool, srg_map=None, say=print):
    """Forge -> NeoForge 1.21.1, after the remap, codemods and recipe pack. -> {at, shapes, net, errors}"""
    repo, rep = pathlib.Path(repo), {}
    if any(repo.glob("src/*/resources/META-INF/accesstransformer.cfg")):
        rep["at"] = run("fix-access-transformer.py", "--work", repo, *(["--srg-map", srg_map] if srg_map else []))[1]
        say(f"access transformer: {headline(rep['at'], 'fix-access-transformer', 'at')}")
    if at_loop(repo, compile_count, workdir, run) is None:
        raise RuntimeError("Minecraft's recompile never succeeded after the access-transformer passes (see at-*.log)")
    for key, script in FORGE_STEPS:
        rep[key] = []
        for d in dirs:
            rc, o = run(script, "--src", d, "--modid", modid)
            if rc not in (0, 1):
                raise RuntimeError(f"{script} failed: {o[-600:]}")
            rep[key].append(o)
        say(f"{key}: " + " | ".join(dict.fromkeys(headline(o, script[:-3], key) for o in rep[key])))
    counts = []
    for r in range(4):
        log = pathlib.Path(workdir) / f"mech-{r}.log"
        n, line = compile_count(log)
        if n is None:
            raise RuntimeError(f"compile did not run: {line}")
        counts.append(n)
        outs = [run("fix-holders.py", "--src", d, "--log", log, "--sites", 0)[1] for d in dirs]
        # the owner-resolved member renames (X73): javac names the owner, so a same-named member elsewhere is safe
        mouts = [run("fix-missing-members.py", "--src", d, "--log", log, "--table", ROOT / FORGE_MEMBERS)[1]
                 for d in dirs] if (ROOT / FORGE_MEMBERS).exists() else []
        rep.setdefault("members", []).extend(mouts)
        if all(" 0 site" in (o.splitlines() or [""])[0] for o in outs) and \
                all(re.search(r"\b0 site\(s\) rewritten", o or "") for o in mouts):
            break
    rep["errors"] = counts
    say(f"holders+members: {' -> '.join(map(str, counts))} errors")
    return rep


# ---------------------------------------------------------------------------------------------- CLI (jar route)

PARSE_ERR = re.compile(r"^(\S+\.java):\d+: error: (?:[^ ]+(?: or [^ ]+)* expected|illegal start of|class, interface, enum, "
                       r"or record expected|reached end of file while parsing|not a statement|initializers not allowed "
                       r"in interfaces|enum constant expected here)", re.M)


def quarantine_unparseable(work, log, cap=10):
    """javac stops at PARSE errors before it attributes anything, so one file the decompiler left unparseable hides
    every other error and stops the stage (each new decompile artifact did, until a fix landed: X84). Move such
    files (at most `cap`, and at most a tenth of the tree) to parked/unparseable/, record them for the compile loop,
    and let the count go on. -> files moved (0: nothing to do, or too many to be a decompile artifact)"""
    work = pathlib.Path(work)
    text = pathlib.Path(log).read_text(encoding="utf-8", errors="replace") if pathlib.Path(log).exists() else ""
    files = sorted({pathlib.Path(m.group(1)) for m in PARSE_ERR.finditer(text)})
    srcs = [work / d for d in ("src/main/java", "src/test/java")]
    files = [f for f in files if f.exists() and any(r in f.parents for r in srcs)]
    total = sum(1 for r in srcs if r.exists() for _ in r.rglob("*.java"))
    if not files or len(files) > cap or len(files) * 10 > max(total, 10):
        return 0
    dest = work / "parked/unparseable"
    for f in files:
        root = next(r for r in srcs if r in f.parents)
        t = dest / root.relative_to(work) / f.relative_to(root)
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), str(t))
    print(f"mechanical-hop: QUARANTINED {len(files)} unparseable file(s) to parked/unparseable/: "
          + ", ".join(f.name for f in files), flush=True)
    with open(work / "MIGRATION.md", "a", encoding="utf-8") as fh:
        fh.write("\n## Unparseable after decompile (parked/unparseable/)\n\nThese did not parse, which stops javac before it "
                 "reports anything else; fix them by hand (or re-decompile with CFR) and move them back:\n"
                 + "".join(f"- {f.relative_to(work).as_posix()}\n" for f in files))
    return len(files)


def gradle_count(work, log):
    """Compile the port's own source sets and count unique errors (burndown-count.sh refuses a run that never
    reached javac, which a bare grep would read as 0)."""
    srcsets, pg = _load("srcsets", "srcsets.py"), _load("port_gates", "port_gates.py")
    pg.compile_log(work, srcsets.compile_tasks(work), log)
    for _ in range(3):
        if not quarantine_unparseable(work, log):
            break
        pg.compile_log(work, srcsets.compile_tasks(work), log)
    r = subprocess.run(["bash", str(ROOT / "tools/burndown-count.sh"), str(log)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    m = re.search(r"errors = (\d+)", r.stdout)
    if not m or "compileJava: yes" not in r.stdout:
        return None, (r.stdout.strip().splitlines() or ["no count"])[-1]
    return int(m.group(1)), r.stdout.strip().splitlines()[-1]


def main_cli(a):
    work = pathlib.Path(a.work).resolve()
    if a.stage == "forge":
        return forge_cli(a, work)
    if (work / "versions/1.21.1.properties").exists() and (work / "versions" / f"{a.target}.properties").exists():
        print("mechanical-hop: REFUSED -- a two-target tree (era-hop --keep-old): converters write target-only code "
              "into shared files and would break 1.21.1. Flatten it (era-hop without --keep-old) first.")
        return 2
    targets, srcsets = _load("targets", "targets.py"), _load("srcsets", "srcsets.py")
    t = targets.TARGETS.get(a.target)
    if not t or not t.members_table:
        print(f"mechanical-hop: no member table for target {a.target}")
        return 2
    modid = a.modid
    if not modid:
        m = re.search(r"^mod_id\s*=\s*(\S+)", (work / "gradle.properties").read_text(encoding="utf-8")
                      if (work / "gradle.properties").exists() else "", re.M)
        modid = m.group(1) if m else ""
    dirs = srcsets.java_dirs(work)
    logs = work / "mechanical-hop"
    logs.mkdir(exist_ok=True)
    n0, _ = gradle_count(work, logs / "start.log")
    moves = _load("era_hop", "era-hop.py").ws_moves() / f"moves-1.21.1-to-{a.target}.tsv"
    has_at = any(work.glob("src/*/resources/META-INF/accesstransformer.cfg")) and moves.exists()
    # An AT that widens a vanilla method breaks Minecraft's own recompile, so the START compile never reaches javac:
    # that is exactly the case the AT loop below repairs, so it must not be a stop (X71).
    if n0 is None and not has_at:
        print("mechanical-hop: the start compile never reached javac; see mechanical-hop/start.log")
        return 2
    if has_at:
        out = era_access_transformer(work, moves)
        print(f"mechanical-hop:   access transformer: {headline(out, 'fix-access-transformer', 'at')}", flush=True)
        try:
            n0 = at_loop(work, lambda log: gradle_count(work, log), logs)
        except RuntimeError as e:
            print(f"mechanical-hop: {e}")
            return 2
        if n0 is None:
            print("mechanical-hop: Minecraft's recompile never succeeded after the access-transformer passes (see at-*.log)")
            return 2
    print(f"mechanical-hop: start -> {n0} errors", flush=True)
    steps, adv = run_stage(work, dirs, ROOT / t.members_table, logs, modid, lambda log: gradle_count(work, log), n0,
                           say=lambda s: print(f"mechanical-hop:   {s}", flush=True),
                           render_state_type=a.render_state_type, render_state_factory=a.render_state_factory,
                           shader_block=a.shader_block, lib_trees=a.lib_tree or [])
    end = steps[-1]["errors"] if steps else n0
    for x in adv:
        print(f"mechanical-hop: ADVISORY {x}")
    print(f"mechanical-hop: {n0} -> {end} errors before any worker")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps({"start": n0, "end": end, "steps": steps, "advisories": adv}, indent=1)
                                        + "\n", encoding="utf-8")
    return 0


def forge_cli(a, work):
    srcsets = _load("srcsets", "srcsets.py")
    modid = a.modid or (re.search(r"^mod_id\s*=\s*(\S+)", (work / "gradle.properties").read_text(encoding="utf-8"), re.M)
                        or [None, ""])[1]
    logs = work / "mechanical-hop"
    logs.mkdir(exist_ok=True)
    srg = pathlib.Path(a.srg_map) if a.srg_map else None
    try:
        rep = run_forge_stage(work, srcsets.java_dirs(work), modid, logs, lambda log: gradle_count(work, log),
                              srg_map=srg if srg and srg.exists() else None,
                              say=lambda s: print(f"mechanical-hop:   {s}", flush=True))
    except RuntimeError as e:
        print(f"mechanical-hop: {e}")
        return 2
    print(f"mechanical-hop: {rep['errors'][0]} -> {rep['errors'][-1]} errors before any worker")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps({"stage": "forge", "errors": rep["errors"]}, indent=1) + "\n",
                                        encoding="utf-8")
    return 0


def self_check():
    ok = True
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d)
        src = repo / "src/main/java/x"
        src.mkdir(parents=True)
        (src / "A.java").write_text("class A {}\n", encoding="utf-8")
        calls, counts = [], iter([5, 3, 3, 1, 0])

        def fake_run(name, *args):
            calls.append(name)
            if name == "convert-valueio.py":
                (src / "A.java").write_text("class A { int x; }\n", encoding="utf-8")
            return 0, f"{name[:-3]}: done"
        steps, adv = run_stage(repo, [repo / "src/main/java"], "members.tsv", repo, "x",
                               lambda log: (next(counts), "ok"), 9, say=lambda s: None, run=fake_run)
        names = [s["step"] for s in steps]
        ok &= names[0] == "members" and names[-1] == "members-after-converters"
        ok &= [c for c in calls if c.startswith("convert-")] == ["convert-gui-hooks.py", "convert-valueio.py",
                                                                    "convert-attachment-io.py", "convert-override-signatures.py",
                                                                    "convert-reload-weighted.py", "convert-client-hooks.py",
                                                                    "convert-saved-data.py",
                                                                    "convert-gear-tiers.py", "convert-entity-renderstate.py",
                                                                    "convert-buffer-seam.py"]
        recount = [s for s in steps if s["step"] == "valueio"][0]
        ok &= recount["errors"] == 3 and [s for s in steps if s["step"] == "gui-hooks"][0]["errors"] == 5
        (repo / "src/main/resources/assets/x/shaders/core").mkdir(parents=True)
        (repo / "src/main/resources/assets/x/shaders/core/p.json").write_text("{}", encoding="utf-8")
        todo, adv = plan(repo, [repo / "src/main/java"], "x")
        ok &= any("shader-block" in x for x in adv) and "core-shaders" not in [t[0] for t in todo]
        todo, adv = plan(repo, [repo / "src/main/java"], "x", shader_block="B")
        ok &= "core-shaders" in [t[0] for t in todo]
        # forge stage: AT fixed first, the loop widens until it compiles, then shapes, net, holders until quiet
        (repo / "src/main/resources/META-INF").mkdir(parents=True, exist_ok=True)
        (repo / "src/main/resources/META-INF/accesstransformer.cfg").write_text("public a.B c\n", encoding="utf-8")
        seq, calls = iter([None, 40, 30, 30]), []
        def frun(name, *args):
            calls.append(name)
            return 0, {"fix-access-transformer.py": "fix-access-transformer: 1 override widened",
                       "fix-holders.py": "fix-holders: 0 site(s)",
                       "fix-missing-members.py": "fix-missing-members: 0 site(s) rewritten"}.get(name, f"{name[:-3]}: ok")
        rep = run_forge_stage(repo, [repo / "src/main/java"], "x", repo, lambda log: (next(seq), "line"), run=frun,
                              say=lambda s: None)
        ok &= calls[0] == "fix-access-transformer.py" and calls.count("fix-access-transformer.py") == 2
        ok &= calls.index("forge-shapes.py") < calls.index("convert-simplechannel.py") < calls.index("fix-holders.py")
        ok &= rep["errors"] == [30] and "fix-missing-members.py" in calls
    with tempfile.TemporaryDirectory() as d:        # an unparseable file is set aside, a mass of them is not
        w = pathlib.Path(d); j = w / "src/main/java/p"; j.mkdir(parents=True)
        for i in range(12):
            (j / f"C{i}.java").write_text("class C {}", encoding="utf-8")
        log = w / "b.log"
        log.write_text(f"{j / 'C3.java'}:13: error: enum constant expected here\n{j / 'C4.java'}:2: error: ';' expected\n",
                       encoding="utf-8")
        ok &= quarantine_unparseable(w, log) == 0      # 2 of 12 > a tenth: refused
        ok &= (j / "C3.java").exists()
        for i in range(12, 40):
            (j / f"C{i}.java").write_text("class C {}", encoding="utf-8")
        ok &= quarantine_unparseable(w, log) == 2 and not (j / "C3.java").exists() \
            and (w / "parked/unparseable/src/main/java/p/C3.java").exists() and "C4.java" in (w / "MIGRATION.md").read_text()
    with tempfile.TemporaryDirectory() as d:        # a NeoGradle cache entry naming a deleted workspace is healed
        d = pathlib.Path(d)
        rel = ".gradle/repositories/ng_dummy_ng/net/minecraft/client/1.21.1/client-1.21.1-client-extra.jar"
        (d / "repo" / rel).parent.mkdir(parents=True)
        (d / "repo" / rel).write_bytes(b"jar")
        lib = d / "home/caches/ng_execute/abc/libraries.txt"
        lib.parent.mkdir(parents=True)
        lib.write_text(f"-e={d}/gone/{rel}\n-e=/does/not/matter.jar\n", encoding="utf-8")
        ok &= heal_ng_cache(d / "repo", home=d / "home") == 1
        ok &= str(d / "home/caches/ng-heal" / rel.split(".gradle/repositories/", 1)[1]) in lib.read_text(encoding="utf-8")
        ok &= (d / "home/caches/ng-heal" / rel.split(".gradle/repositories/", 1)[1]).exists()   # survives the workspace
        ok &= "/does/not/matter.jar" in lib.read_text(encoding="utf-8")
        ok &= heal_ng_cache(d / "repo", home=d / "home") == 0      # idempotent
        copy = d / "repo/build/neoForm/x/steps/listTransformLibraries/libraries.txt"   # the workspace's own copy
        copy.parent.mkdir(parents=True)
        copy.write_text(f"-e={d}/gone/{rel}\n", encoding="utf-8")
        ok &= heal_ng_cache(d / "repo", home=d / "home") == 1 and "/gone/" not in copy.read_text(encoding="utf-8")
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work"); ap.add_argument("--stage", choices=["forge", "era"], default="era")
    ap.add_argument("--target", default="26.2"); ap.add_argument("--modid"); ap.add_argument("--srg-map")
    ap.add_argument("--json"); ap.add_argument("--render-state-type"); ap.add_argument("--render-state-factory")
    ap.add_argument("--shader-block"); ap.add_argument("--lib-tree", action="append")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not a.work:
        ap.error("--work is required")
    return main_cli(a)


if __name__ == "__main__":
    sys.exit(main())
