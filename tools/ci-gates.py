#!/usr/bin/env python3
"""Run a finished port's gates in CI -- no model, no workers, a plain pass/fail with the evidence.

    python3 tools/ci-gates.py --repo . --modid <modid> --base <author's ref> [--gatec launch,spawn]
                              [--summary SUMMARY.md] [--work-dir DIR]

The fork's own CI calls this (templates/upstream-harness/port-ci.yml), so anyone can see on the branch that
the port PROVABLY loads and runs -- not only that it compiles. Measured: none of the four mods ported this
way shipped a test suite of its own, so "the author's tests pass" proves only that the JAR builds.

Same gates the port was held to, with the same code (tools/port_gates.py: the pipeline's own harness and Gate A,
tools/gate-loop.py --no-workers for Gate B and Gate C), so CI cannot judge a port more leniently than the
pipeline did:
  - the author's own `build` (and every Jar task they registered), with none of the harness;
  - Gate A: the mixin-config integrity test (every config entry compiled; every port-added mixin listed);
  - Gate B: a headless dedicated server loads the mod and runs the baseline GameTests;
  - Gate C: a real client under Xvfb + Mesa (software GL): reach the title screen, then spawn every entity.
The harness is generated OUTSIDE the author's tree (--work-dir, default a temp dir) and wired in with
--init-script, exactly as during the port. The summary (markdown) lists what ran and what passed, and is
what the release notes quote. Standard library only.
"""
import argparse, importlib.util, json, os, pathlib, re, subprocess, sys, tempfile, types, zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / file)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


PLATFORM = {"minecraft", "neoforge", "forge", "java", "mixinextras"}


def toml_mods(text):
    """{modId: [required dependency modIds]} from a (neoforge.)mods.toml."""
    mods = re.findall(r'(?s)\[\[mods\]\].*?modId\s*=\s*"([^"]+)"', text)
    out = {m: [] for m in mods}
    for owner, block in re.findall(r'(?s)\[\[dependencies\.([^\]]+)\]\](.*?)(?=\[\[|\Z)', text):
        mid = re.search(r'modId\s*=\s*"([^"]+)"', block)
        req = re.search(r'type\s*=\s*"required"', block) or re.search(r'mandatory\s*=\s*true', block)
        if mid and req and mid.group(1) not in PLATFORM:
            out.setdefault(owner, []).append(mid.group(1))
    return out


def jar_mods(path):
    try:
        z = zipfile.ZipFile(path)
        return toml_mods("".join(z.read(n).decode("utf-8", "replace") for n in z.namelist()
                                 if n in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml")))
    except (OSError, zipfile.BadZipFile):
        return {}


def minimal_exclusions(own_toml, artifacts):
    """The runtime mods a MINIMAL environment leaves out: every mod jar none of whose mods is required -- by this
    mod's neoforge.mods.toml, or (transitively) by a mod that is. Libraries (no mods.toml) and the platform
    stay. -> ([group:module to exclude], {group:module: [modIds]})."""
    own = toml_mods(own_toml)
    need = {d for deps in own.values() for d in deps}
    blocks = len(re.findall(r"\[\[dependencies\.", own_toml))
    parsed = len(re.findall(r'(?s)\[\[dependencies\.([^\]]+)\]\]', own_toml))
    if blocks != parsed:     # a dependency block the parser cannot read would be "not required" -> removed
        raise SystemExit(f"minimal environment: read {parsed} of {blocks} dependency blocks in neoforge.mods.toml")
    jars = {coord: jar_mods(path) for coord, path in artifacts}
    changed = True
    while changed:                                  # close over what the required mods themselves require
        changed = False
        for mods in jars.values():
            if set(mods) & need:
                for deps in mods.values():
                    for d in deps:
                        if d not in need:
                            need.add(d); changed = True
    out = {c: sorted(m) for c, m in jars.items() if m and not (set(m) & (need | PLATFORM))}
    return sorted(out), out


def loaded_mods(log):
    """Mod ids FML listed in its "Mod List:" block -- what the game actually loaded."""
    m = re.search(r"Mod List:\n(.*?)(?:\n\[|\Z)", log, re.S)
    return set(re.findall(r"\(([a-z0-9_.-]+)\)\s*$", m.group(1), re.M)) if m else set()


# What the game prints when it REJECTS a data file and carries on without it. Each is an ERROR or WARN
# line and then silence: the recipe, advancement, tag or loot table simply does not exist, and every
# GameTest still passes. Caught live: 38 advancements and 62 smithing recipes of one port, after green CI.
DATA_REJECTED = re.compile(r"Couldn't parse|Parsing error loading|Couldn't load tag|missing following references|"
                           r"No key \w+ in MapLike|Not a JSON object|Failed to parse|Unknown registry key|"
                           r"MalformedJsonException|Failed to load built-in")


def data_rejections(log, modid):
    """Log lines where the game rejected one of THIS mod's data files: the line, or the one after it (where a
    mod's own loader logs the cause), must name the mod's namespace. Other mods' noise is not this port's."""
    lines, out = log.splitlines(), []
    for i, l in enumerate(lines):
        near = [l] + [x for x in lines[i + 1:i + 2] if not x.startswith("[")]   # a continuation, not a new log line
        if DATA_REJECTED.search(l) and any(f"{modid}:" in x or f"/{modid}/" in x for x in near):
            out.append(l.strip()[:240])
    return list(dict.fromkeys(out))


def green_detail(text):
    """What a green gate log proves, in one line: the GameTest count, or the client's PASS line and spawns."""
    m = re.search(r"All (\d+) required tests passed", text) or re.search(r"BOOT_TEST: (PASS[^\n]*)", text)
    spawned = re.search(r"BOOT_TEST: spawned (\d+) creature type", text)
    head = (f"{m.group(1)} required test(s) passed" if m and m.group(1).isdigit() else
            (m.group(1)[:160] if m else "green"))
    return head + (f"; {spawned.group(1)} entity types spawned" if spawned else "")


def self_check():
    ok = green_detail("[x] All 4 required tests passed :)") == "4 required test(s) passed"
    ok &= green_detail("M_BOOT_TEST: spawned 40 creature type(s): [a]\nM_BOOT_TEST: PASS — ticked 200").startswith(
        "PASS — ticked 200; 40 entity types spawned")
    ok &= green_detail("") == "green"
    own = ('[[mods]]\nmodId="${mod_id}"\n[[dependencies.${mod_id}]] #optional\n    modId="lib" #mandatory\n'
           '    type = "required" #mandatory\n[[dependencies.${mod_id}]]\nmodId="opt"\ntype="optional"\n')
    import tempfile as _t
    with _t.TemporaryDirectory() as d:
        def jar(name, toml):
            path = pathlib.Path(d) / name
            with zipfile.ZipFile(path, "w") as z:
                if toml is not None:
                    z.writestr("META-INF/neoforge.mods.toml", toml)
            return str(path)
        arts = [("g:lib", jar("lib.jar", '[[mods]]\nmodId="lib"\n[[dependencies.lib]]\nmodId="base"\ntype="required"\n')),
                ("g:base", jar("base.jar", '[[mods]]\nmodId="base"\n')),
                ("g:opt", jar("opt.jar", '[[mods]]\nmodId="opt"\n')),
                ("g:spark", jar("spark.jar", '[[mods]]\nmodId="spark"\n')),
                ("g:plainlib", jar("plain.jar", None))]
        ex, _info = minimal_exclusions(own, arts)
        ok &= ex == ["g:opt", "g:spark"]               # optional + dev-only out; required + its own requirement in
    ok &= loaded_mods("x\n     Mod List:\n\t\tName Version (Mod Id)\n\n\t\tCurios 9 (curios)\n\t\tMe 1 (me)\n[10:00] next") \
        == {"curios", "me"}
    rej = ("[x] ERROR Couldn't parse data file 'm:adv/a' from 'mod/m': No key id in MapLike[{\"item\":\"m:i\"}]\n"
           "[x] ERROR [m] Failed to load built-in recipe: smithing/b.json\n"
           "java.lang.IllegalArgumentException: Invalid recipe m:b: No key id in MapLike\n"
           "[x] ERROR Couldn't parse data file 'other:x': No key id\n[x] INFO loaded m:fine")
    ok &= len(data_rejections(rej, "m")) == 3 and data_rejections(rej, "zz") == []
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    if "--self-check" in sys.argv:
        return self_check()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default="."); ap.add_argument("--modid", required=True)
    ap.add_argument("--base", help="the author's ref (mixins they left unlisted stay exempt from Gate A)")
    ap.add_argument("--gatec", default="launch,spawn", help="Gate C phases; '' skips Gate C")
    ap.add_argument("--summary", default="ci-gates-summary.md"); ap.add_argument("--work-dir")
    ap.add_argument("--skip-author-build", action="store_true")
    ap.add_argument("--env", choices=("full", "minimal"), default="full",
                    help="full: every mod the build puts on the runtime; minimal: only what neoforge.mods.toml "
                         "requires (optional integrations and dev-only mods removed from the runtime, not the compile)")
    a = ap.parse_args()
    repo = pathlib.Path(a.repo).resolve()
    work = pathlib.Path(a.work_dir or tempfile.mkdtemp(prefix="port-ci-")).resolve()
    work.mkdir(parents=True, exist_ok=True)
    pu, ss = _load("port_gates", "port_gates.py"), _load("srcsets", "srcsets.py")
    c = {"repo": repo, "dir": work, "args": types.SimpleNamespace(modid=a.modid, base=a.base)}
    rows, ok = [], True

    def record(name, passed, detail):
        nonlocal ok
        ok &= passed
        rows.append(f"| {name} | {'PASS' if passed else 'FAIL'} | {detail} |")
        print(f"[ci-gates] {name}: {'PASS' if passed else 'FAIL'} -- {detail}", flush=True)

    excluded, ex, arts = {}, [], []
    if a.env == "minimal":
        env0 = dict(os.environ, **pu.gate_env(c))
        lst = work / "runtime-artifacts.log"
        r = pu.gradle(repo, ["portRuntimeArtifacts", "-q"], lst, extra_init=[env0["PORT_GRADLE_INIT"]], env=env0)
        arts = [tuple(l[len("PORT_RT "):].split("\t", 1)) for l in lst.read_text(encoding="utf-8", errors="replace").splitlines()
                if l.startswith("PORT_RT ")]
        if r.returncode or not arts:
            record("minimal environment", False, "could not list the runtime's mods (see runtime-artifacts.log)")
            arts = []
        toml = repo / "src/main/resources/META-INF/neoforge.mods.toml"
        ex, excluded = minimal_exclusions(toml.read_text(encoding="utf-8") if toml.is_file() else "", arts)
        os.environ["PORT_RUNTIME_EXCLUDE"] = ",".join(ex)
        if arts:
            rows.append("| environment | minimal | " + (("without " + ", ".join(m for ms in excluded.values() for m in ms))
                                                       if ex else "nothing to leave out: every runtime mod is required")
                        + " |")
            print(f"[ci-gates] minimal environment: leaving out {ex or 'nothing'}", flush=True)
    if not a.skip_author_build and a.env == "full":
        jars = ss.author_jar_tasks(repo)
        r = pu.gradle(repo, ["build", *jars], work / "author-build.log")
        built = sorted(p.name for p in (repo / "build/libs").glob("*.jar")) if (repo / "build/libs").is_dir() else []
        passed = r.returncode == 0 and bool(built)
        if not passed:   # the reason, on the run page -- the log itself is only in the downloadable artifact
            log = (work / "author-build.log").read_text(encoding="utf-8", errors="replace")
            m = re.search(r"\* What went wrong:\n(.*?)(?:\n\* Try:|\Z)", log, re.S)
            print("author's build failed:\n" + (m.group(1).strip() if m else log[-3000:]), flush=True)
        record("author's own build (`build" + "".join(f" {j}" for j in jars) + "`)", passed,
               ", ".join(built) or "no jar produced")

    if ok:
        try:
            n = pu.gate_a(c, work / "gateA.log")
            record("Gate A -- mixin-config integrity", True, f"{n} test(s)")
        except pu.Fail as e:
            record("Gate A -- mixin-config integrity", False, str(e)[:300])

    if ok:     # static: data and assets NeoForge would silently ignore (the GameTest sees server data only)
        r = subprocess.run([sys.executable, str(ROOT / "tools/fix-datapack-layout.py"), str(repo), "--verify"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        (work / "datapack-verify.log").write_text(r.stdout + r.stderr, encoding="utf-8")
        lines = [l.strip() for l in (r.stdout + r.stderr).splitlines() if l.strip()]
        record("Data -- nothing NeoForge would silently ignore", r.returncode == 0,
               "1.21-clean" if r.returncode == 0 else
               "; ".join(l for l in lines if l.startswith(("✗", "REFUSED")))[:300] or lines[-1][:300])

    if ok:     # static: a payload codec that rejects every send (client disconnect, no server-side gate sees it)
        r = subprocess.run([sys.executable, str(ROOT / "tools/audit-unit-codecs.py"), str(repo / "src/main/java")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        lines = [l.strip() for l in r.stdout.splitlines() if l.strip()]
        record("Payloads -- every StreamCodec.unit payload has equal instances", r.returncode == 0,
               (lines[0] if r.returncode else lines[-1] if lines else "no output")[:300])

    if ok and a.env == "minimal" and ex:
        # Before booting anything: does code the minimal run will load need a mod it leaves out?
        jars = [path for coord, path in arts if coord in ex]
        cls = repo / "build/classes/java/main"
        r = subprocess.run([sys.executable, str(ROOT / "tools/optional-dep-scan.py"), "--classes", str(cls),
                            *[x for j in jars for x in ("--jar", j)]],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        (work / "optional-dep-scan.log").write_text(r.stdout + r.stderr, encoding="utf-8")
        print(r.stdout.rstrip(), flush=True)
        last = (r.stdout.strip().splitlines() or ["no output"])[-1]
        record("Optional mods -- nothing the mod loads needs one", r.returncode == 0,
               last.replace("optional-dep-scan: ", "")[:300])

    env = dict(os.environ, **pu.gate_env(c))
    for label, extra in [("Gate B -- headless server loads the mod, GameTests", [])] + \
            [(f"Gate C -- real client, `{p}`", ["--gatec", p]) for p in filter(None, a.gatec.split(","))]:
        if not ok:
            rows.append(f"| {label} | not run | an earlier gate failed |")
            continue
        log = work / ("gate-" + (extra[1] if extra else "b") + ".jsonl")
        r = subprocess.run([sys.executable, str(ROOT / "tools/gate-loop.py"), "--work", str(repo), "--namespace",
                            a.modid, "--no-workers", "--log", str(log), *extra], env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=3 * 3600)
        detail = "green"
        if r.returncode:
            m = re.search(r"GATE \S+ FAILED:\n(.*)", r.stdout, re.S)
            detail = (m.group(1) if m else (r.stdout + r.stderr)[-600:]).strip().splitlines()[0][:300]
            print(r.stdout[-6000:], flush=True)
        else:
            gl = pathlib.Path(env["PORT_LOG_DIR"]) / f"gate-loop{'-' + extra[1] if extra else ''}.log"
            text = gl.read_text(encoding="utf-8", errors="replace") if gl.exists() else ""
            detail = green_detail(text)
            leaked = sorted(loaded_mods(text) & {m for ms in excluded.values() for m in ms})
            if leaked:      # a minimal pass that loaded the mods it meant to leave out tested nothing
                record(label, False, f"minimal environment not applied: the game loaded {', '.join(leaked)}")
                continue
            if a.env == "minimal" and excluded and not loaded_mods(text):
                record(label, False, "minimal environment unverified: no Mod List in the log")
                continue
            if not extra:
                bad = data_rejections(text, a.modid)
                record("Data loads -- the server rejected none of the mod's data files", not bad,
                       f"{len(bad)} rejected, e.g. {bad[0]}" if bad else "no rejection logged")
        record(label, r.returncode == 0, detail)

    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()
    md = [f"Gates for `{a.modid}` at `{head}` ({a.env} environment) -- run by CI with no model and no fixes applied:", "",
          "| check | result | detail |", "|---|---|---|", *rows, "",
          "Not covered by any automated gate: gameplay by a person."]
    pathlib.Path(a.summary).write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
