#!/usr/bin/env python3
"""The gate harness and Gate A, shared by tools/port-upstream.py (the porting pipeline) and tools/ci-gates.py
(a ported fork's CI). Kept apart from the pipeline so the CI kit (tools/port-ci-kit.py) carries the gates and
not the model-driving stages. Standard library only.
"""
import importlib.util, json, os, pathlib, re, shutil, subprocess, sys, time

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_tool(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / file)
    mod = importlib.util.module_from_spec(spec); sys.modules.setdefault(name, mod); spec.loader.exec_module(mod)
    return mod


srcsets = _load_tool("srcsets", "srcsets.py")
targets = _load_tool("targets", "targets.py")
BASH = _load_tool("gitbash", "gitbash.py").BASH   # Git Bash on Windows, where plain "bash" is WSL's launcher


class Fail(Exception):
    def __init__(self, msg, usd=0.0):
        super().__init__(msg); self.usd = usd


def sh(cmd, cwd=None, env=None, timeout=None, log=None):
    r = subprocess.run(cmd, cwd=cwd, env=env, timeout=timeout, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", shell=isinstance(cmd, str))
    if log:
        pathlib.Path(log).write_text(r.stdout + r.stderr, encoding="utf-8")
    return r


def gradle(repo, args, log, extra_init=(), env=None, timeout=2400):
    cmd = [BASH, "gradlew","--console=plain", "-I", str(ROOT / "tools/central-mirror.init.gradle")]
    for i in extra_init:
        cmd += ["-I", str(i)]
    cmd += gradle_target_args(env)      # PORT_GRADLE_ARGS: extra arguments every Gradle call of a gate run carries
    return sh(cmd + list(args) + ["-Dorg.gradle.jvmargs=-Xmx6g"], cwd=repo, env=env, timeout=timeout, log=log)


GRADLE_ERROR_CAP = 1000   # PORT_GRADLE_CAP in tools/maxerrs.init.gradle


def compile_log(repo, tasks, log, extra_init=(), env=None, timeout=2400):
    """Compile `tasks` and leave the WHOLE javac error list in `log`, for tools/burndown-count.sh.

    Gradle's own javac integration is quadratic in the error count (tools/maxerrs.init.gradle says why), so the
    compile runs capped, and only a run that HIT the cap is recounted by `portFullErrors` -- the same javac, run
    directly, uncapped -- with its output appended to the same log."""
    init = ROOT / "tools/maxerrs.init.gradle"
    r = gradle(repo, ["-I", str(init), *tasks, "--continue"], log, extra_init=extra_init, env=env, timeout=timeout)
    text = pathlib.Path(log).read_text(encoding="utf-8", errors="replace")
    if not re.search(r"(?m)^\s*%s errors\s*$" % format(GRADLE_ERROR_CAP, ",").replace(",", ",?"), text):
        return r
    full = pathlib.Path(str(log) + ".full")
    r2 = gradle(repo, ["-I", str(init), "portFullErrors", "-PportFullErrorsTasks=" + ",".join(tasks)], full,
                extra_init=extra_init, env=env, timeout=timeout)
    with open(log, "a", encoding="utf-8") as fh:
        fh.write("\n# --- the capped compile hit %d errors; uncapped recount (portFullErrors) ---\n" % GRADLE_ERROR_CAP)
        fh.write(full.read_text(encoding="utf-8", errors="replace"))
    return r2 if r2.returncode else r


def gradle_target_args(env=None):
    """PORT_GRADLE_ARGS: extra Gradle arguments for every call of a gate run, newline-separated."""
    raw = (env if env is not None else os.environ).get("PORT_GRADLE_ARGS", "")
    return [a for a in raw.splitlines() if a.strip()]


def tgt(c):
    """The Target of this run: from --branch normally; from gradle.properties for a caller that never parsed
    arguments (tools/ci-gates.py builds the same context to reuse the harness and Gate A). On a fork's version branch
    (PORT_BRANCH_MC, set by ci-gates.py) the base version's: the harness is then renamed for the branch's version
    (layer_target)."""
    if "target" not in c:
        base = os.environ.get("PORT_BRANCH_BASE") if os.environ.get("PORT_BRANCH_MC") else None
        c["target"] = targets.TARGETS[base] if base in targets.TARGETS else targets.target_of_repo(c["repo"])
    return c["target"]


def author_unlisted_mixins(repo, base):
    """@Mixin classes in the author's tree at `base` that no config of theirs listed: parked by the author.
    The port must keep them unregistered, and the integrity test must not demand otherwise."""
    if not base:
        return []
    out = []
    files = sh(["git", "ls-tree", "-r", "--name-only", base], cwd=repo).stdout.split()
    listed = set()
    for f in files:
        if f.endswith(".json") and "/resources/" in f:
            t = sh(["git", "show", f"{base}:{f}"], cwd=repo).stdout
            try:
                d = json.loads(t)
            except ValueError:
                continue
            if isinstance(d, dict) and isinstance(d.get("package"), str):
                listed |= {(d["package"], n) for k in ("mixins", "client", "server") for n in d.get(k) or []}
    pkgs = {p for p, _ in listed}
    for f in files:
        if not f.endswith(".java") or "/java/" not in f:
            continue
        cls = f.split("/java/", 1)[1][:-5].replace("/", ".")
        pkg = next((p for p in pkgs if cls.startswith(p + ".")), None)
        if not pkg:
            continue
        name = cls[len(pkg) + 1:]
        if (pkg, name) not in listed and "@Mixin" in sh(["git", "show", f"{base}:{f}"], cwd=repo).stdout:
            out.append(name)
    return sorted(out)


# The harness's own GameTest structure. A kit-owned name, never the `empty_test` a mod ships for its own GameTests: the
# harness's resources are added to the mod's resource root for the gate run, so a shared name is a duplicate entry
# that fails processResources (and with a duplicate strategy, one of the two files silently loses).
HARNESS_STRUCTURE = "port_ci_harness_empty"
# Entity types the mod deliberately replaces when they join a level, with the fork's reason for each: the baseline's
# every-entity test excuses only the level refusing one of these, and the gate report lists them.
BASELINE_EXEMPT = "baseline-exempt.txt"


def baseline_exempt_file(repo, mc=None):
    """The fork's committed .port/<mc>/baseline-exempt.txt, else the nearest lower version's, or None (<mc>: the given
    one, else the version branch's (PORT_BRANCH_MC), else gradle.properties' minecraft_version), on every kind of fork."""
    repo = pathlib.Path(repo)
    if not mc:
        mc = layer_target()
    if not mc:
        gp = repo / "gradle.properties"
        m = re.search(r"(?m)^\s*minecraft_version\s*=\s*(\S+)", gp.read_text(encoding="utf-8")) if gp.is_file() else None
        mc = m.group(1) if m else None
    f = repo / ".port" / str(mc) / BASELINE_EXEMPT if mc else None
    if f is None or f.is_file():
        return f
    # no file of this version's own: the nearest LOWER version's (.port/<lower>/ is carried up by every merge, and a
    # single-version fork that moved its minecraft_version keeps its old directory). What a mod replaces when it joins
    # a level is the mod's behaviour, not the version's, so a new version branch inherits it rather than failing Gate B
    # on the same stand-in the lower version excused (measured: a new rung's baseline failed "the level refused it").
    # The report row names the file used; an empty file of the version's own grants nothing.
    lower = sorted((mc_tuple(d.name), d / BASELINE_EXEMPT) for d in (repo / ".port").glob("*")
                   if re.fullmatch(r"\d+(\.\d+)+", d.name) and mc_tuple(d.name) < mc_tuple(str(mc))
                   and (d / BASELINE_EXEMPT).is_file()) if (repo / ".port").is_dir() else []
    return lower[-1][1] if lower else None


def baseline_exemptions(repo, modid, mc=None):
    """[(entity id, reason)] from the fork's baseline-exempt.txt, in file order: one `<modid>:<path> <reason>` a line,
    `#` comments and blank lines ignored. A line with no reason, an id outside the mod's namespace or a repeated id is
    refused (Fail): an exemption nobody can explain, or one for another mod's entity, is not one to grant silently."""
    f = baseline_exempt_file(repo, mc)
    if f is None:
        return []
    out, seen = [], set()
    for n, raw in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
        line = "" if raw.lstrip().startswith("#") else raw.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        eid, reason = parts[0], (parts[1].strip() if len(parts) > 1 else "")
        where = f"{f.as_posix()}:{n}"
        if not re.fullmatch(r"[a-z0-9_.-]+:[a-z0-9_./-]+", eid):
            raise Fail(f"{where}: {eid!r} is not an entity id (namespace:path)")
        if eid.split(":", 1)[0] != modid:
            raise Fail(f"{where}: {eid} is not in this mod's namespace ({modid}); the baseline only tests the mod's own")
        if not reason:
            raise Fail(f"{where}: {eid} has no reason; say why the mod replaces it (`{eid} <reason>`)")
        if eid in seen:
            raise Fail(f"{where}: {eid} is listed twice")
        seen.add(eid)
        out.append((eid, reason))
    return out


def _java_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def harness_baseline(gt, pkg, modid, exempt):
    """The baseline GameTest source for the harness: the mod's package and namespace, the kit's own structure, and the
    fork's exemptions written into REPLACED."""
    gt = re.sub(r"(?m)^package\s+[\w.]+;", f"package {pkg}.gatetest;", gt).replace('"examplemod"', f'"{modid}"')
    gt = gt.replace('template = "empty_test"', f'template = "{HARNESS_STRUCTURE}"')
    puts = "\n        ".join(f"REPLACED.put({_java_str(e)}, {_java_str(r)});" for e, r in exempt)
    return gt.replace("/*BASELINE_EXEMPT*/", puts)


def harness(c):
    """The external harness: a baseline GameTest, its structure, the mixin integrity test."""
    h, modid = c["dir"] / "harness", c["args"].modid
    # reuse a harness only when it was built for this target by this harness code: a stale one fails the same way
    # on every later run (measured: a pre-1.21.5-adapter harness, reused through a persistent --work-dir)
    exempt = baseline_exemptions(c["repo"], modid)
    stamp = f"{layer_target() or 'base'}\n{_harness_code_hash()}\n{exempt!r}"
    if h.exists() and (h / ".built-for").is_file() and (h / ".built-for").read_text(encoding="utf-8") == stamp:
        return h
    shutil.rmtree(h, ignore_errors=True)
    pkg = (re.search(r"(?m)^package\s+([\w.]+)\s*;", next((c["repo"] / "src/main/java").rglob("*.java")).read_text(
        encoding="utf-8")) or [None, modid])[1].split(".")
    pkg = ".".join(pkg[:2]) if len(pkg) > 1 else pkg[0]
    gt = (ROOT / "templates/neoforge-mod/test-templates/BaselineGameTest.java.example").read_text(encoding="utf-8")
    gt = harness_baseline(gt, pkg, modid, exempt)
    (h / "java" / pkg.replace(".", "/") / "gatetest").mkdir(parents=True)
    (h / "java" / pkg.replace(".", "/") / "gatetest/BaselineGameTest.java").write_text(gt, encoding="utf-8")
    tg0 = tgt(c)
    if tg0.gametest != "registration":     # data liveness: annotation-discovered targets (1.21.x); 26.x registers tests
        dl = (ROOT / "templates/neoforge-mod/test-templates/DataLivenessGameTest.java.example").read_text(encoding="utf-8")
        dl = re.sub(r"(?m)^package\s+[\w.]+;", f"package {pkg}.gatetest;", dl).replace('"examplemod"', f'"{modid}"')
        dl = dl.replace('template = "empty_test"', f'template = "{HARNESS_STRUCTURE}"')
        (h / "java" / pkg.replace(".", "/") / "gatetest/DataLivenessGameTest.java").write_text(dl, encoding="utf-8")
    sd = h / "resources/data" / modid / "structure"; sd.mkdir(parents=True)
    own = [p for p in sorted((c["repo"] / "src").glob(f"*/resources/data/{modid}/structure/{HARNESS_STRUCTURE}.nbt"))]
    if own:
        raise Fail(f"the mod ships {own[0].as_posix()}, the gate harness's own structure name: rename the mod's file")
    import gzip, importlib.util
    spec = importlib.util.spec_from_file_location("ges", ROOT / "tools/gen-empty-structure.py")
    ges = importlib.util.module_from_spec(spec); spec.loader.exec_module(ges)
    tg = tgt(c)
    world_version = targets.derive_pack(tg, [c["repo"]])[0]["world_version"]     # the structure's DataVersion: the target's
    (sd / f"{HARNESS_STRUCTURE}.nbt").write_bytes(gzip.compress(ges.build(modid, HARNESS_STRUCTURE, 9, world_version)))
    sg = importlib.util.spec_from_file_location("sgc", ROOT / "tools/scaffold-gatec.py")
    sgc = importlib.util.module_from_spec(sg); sg.loader.exec_module(sgc)
    (h / "java" / pkg.replace(".", "/") / "test").mkdir(parents=True, exist_ok=True)
    (h / "java" / pkg.replace(".", "/") / "test/ClientBootSmokeTest.java").write_text(
        sgc.render(sgc.TEMPLATE.read_text(encoding="utf-8"), modid, pkg), encoding="utf-8")
    for cfg in [f.name for f in srcsets.mixin_configs(c["repo"]) if f.parent.parent.name == "main"][:1]:
        t = (ROOT / "templates/neoforge-mod/test-templates/MixinConfigIntegrityTest.java.template").read_text(encoding="utf-8")
        t = t.replace("PACKAGE_PLACEHOLDER", pkg).replace("MODID.mixins.json", cfg)
        parked = author_unlisted_mixins(c["repo"], c["args"].base)
        t = t.replace("/*AUTHOR_UNLISTED*/", ", ".join(f'"{n}"' for n in parked))
        if layer_target():
            # a version branch's mixin config is the one processResources wrote (its mixin changes are made at that
            # step): read it from build/resources/main, not the source tree
            t = t.replace('DIR.resolve("src/main/resources/', 'DIR.resolve("build/resources/main/')
        (h / "test" / pkg.replace(".", "/")).mkdir(parents=True)
        (h / "test" / pkg.replace(".", "/") / "MixinConfigIntegrityTest.java").write_text(t, encoding="utf-8")
    if tg.gametest == "registration":
        harness_registration(c, h, pkg)
    layer = layer_target()
    if layer:
        harness_layer_target(h, layer)
        if no_gametest_annotation(layer):
            harness_layer_registration(c, h, pkg, layer)
    (h / ".built-for").write_text(stamp, encoding="utf-8")
    return h


def _harness_code_hash():
    """What the harness is built from: this module, the harness templates and the GameTest adapter."""
    import hashlib
    d = hashlib.sha256()
    for p in [pathlib.Path(__file__), ROOT / "templates/multi-version/tools/gametest_adapter.py",
              *sorted((ROOT / "templates/upstream-harness").rglob("*"))]:
        if p.is_file():
            d.update(p.read_bytes())
    return d.hexdigest()[:16]


def mc_tuple(mc):
    return tuple(int(x) for x in re.findall(r"\d+", mc))


def no_gametest_annotation(mc):
    """1.21.5 removed the annotation-driven GameTest API (net.minecraft.gametest.framework.GameTest,
    neoforge's GameTestHolder / PrefixGameTestTemplate): tests are data-driven GameTestInstances from there on
    (CATALOG V5, V20, V47). 1.21.1-1.21.4 keep the annotations, so their harness is left byte-identical."""
    return mc_tuple(mc) >= (1, 21, 5)


def harness_layer_registration(c, h, pkg, mc):
    """A version branch past 1.21.5: the (already renamed) harness tests are wired by the generated registrar and
    their dead annotations stripped, by the same templates/multi-version/tools/gametest_adapter.py the 26.x route uses.
    The 1.21.5 dialect keeps ResourceLocation and FML's @EventBusSubscriber bus attribute; 1.21.6+ (FML 9) drops the attribute; 1.21.11 writes Identifier (the 26.x rename) over a non-generic TestEnvironmentDefinition; 26.x is the adapter's default."""
    dialect = ("26" if mc_tuple(mc) >= (26,) else "1.21.11" if mc_tuple(mc) >= (1, 21, 11)
               else "1.21.8" if mc_tuple(mc) >= (1, 21, 6) else "1.21.5")
    r = subprocess.run([sys.executable, str(ROOT / "templates/multi-version/tools/gametest_adapter.py"), "--root",
                        str(h / "java"), "--modid", c["args"].modid, "--package", f"{pkg}.gatetest", "--structure",
                        HARNESS_STRUCTURE, "--dialect", dialect],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode or "wired 0 tests" in r.stdout:
        raise Fail(f"wiring the harness GameTests for {mc} failed: {(r.stderr or r.stdout)[-400:]}")


def layer_target(env=None):
    """The Minecraft version of the version branch this run gates (PORT_BRANCH_MC, set by tools/ci-gates.py; its
    harness is renamed for that version), or None (a single-version fork)."""
    return (env if env is not None else os.environ).get("PORT_BRANCH_MC") or None


def harness_table(mc, seen=()):
    """templates/upstream-harness/versions/<mc>.renames.tsv, with any `#!inherit <mc>` line expanded in place."""
    p = ROOT / "templates/upstream-harness/versions" / f"{mc}.renames.tsv"
    if not p.is_file() and not seen:
        # a new 1.21.x target with no table yet inherits the nearest lower 1.21.x table (a hop inside one family
        # mostly needs no harness rows; any it does need still show up as Gate A compile errors). Never across
        # families: a 26.x target without its own table is still refused.
        fam = mc_tuple(mc)[:2]
        lower = sorted((mc_tuple(q.name[:-len(".renames.tsv")]), q) for q in p.parent.glob("*.renames.tsv")
                       if mc_tuple(q.name[:-len(".renames.tsv")])[:2] == fam
                       and mc_tuple(q.name[:-len(".renames.tsv")]) < mc_tuple(mc))
        if lower:
            print(f"[port_gates] no harness table for {mc}: inheriting {lower[-1][1].name}", file=sys.stderr)
            return harness_table(lower[-1][1].name[:-len(".renames.tsv")], (mc,))
    if not p.is_file():
        raise Fail(f"the gate harness has no rename table for Minecraft {mc} "
                   f"(templates/upstream-harness/versions/{mc}.renames.tsv): it is written for 1.21.1")
    if mc in seen:
        raise Fail(f"#!inherit loop at {mc}")
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.startswith("#!inherit "):
            out.append(harness_table(line.split()[1], (*seen, mc)))
        else:
            out.append(line)
    return "\n".join(out)


def harness_layer_target(h, mc):
    """A version branch builds the author's code for another Minecraft version; the harness (written for 1.21.1) gets
    that version's own rename table."""
    table = h.parent / f"harness-{mc}.renames.tsv"
    table.write_text(harness_table(mc) + "\n", encoding="utf-8")
    out = h / "java.renamed"
    r = subprocess.run([sys.executable, str(ROOT / "templates/multi-version/tools/prepare-sources.py"), "--src",
                        str(h / "java"), "--renames", str(table), "--out", str(out)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise Fail(f"renaming the harness for {mc} failed: {(r.stderr or r.stdout)[-400:]}")
    shutil.rmtree(h / "java"); out.rename(h / "java")


def read_patch(text):
    """templates/upstream-harness/clientboot-*.patch.txt -> [(old, new)]. `@@ old` / `@@ new` / `@@` blocks;
    text between them is taken as is, minus its last newline; `#` lines outside a block are comments."""
    blocks, state, old, new = [], None, [], []
    for line in text.split("\n"):
        if line.startswith("@@"):
            word = line[2:].strip()
            if word == "old":
                state, old, new = "old", [], []
            elif word == "new" and state == "old":
                state = "new"
            elif word == "" and state == "new":
                blocks.append(("\n".join(old), "\n".join(new))); state = None
            else:
                raise ValueError(f"unexpected {line!r}")
        elif state == "old":
            old.append(line)
        elif state == "new":
            new.append(line)
        elif line.strip() and not line.startswith("#"):
            raise ValueError(f"text outside a block: {line!r}")
    if state:
        raise ValueError("unterminated block")
    return blocks


def apply_patch(text, blocks, name="the file"):
    """Each block replaces exactly one occurrence; anything else is an error, naming the block."""
    for i, (old, new) in enumerate(blocks, 1):
        n = text.count(old)
        if n != 1:
            raise Fail(f"patch block {i} ({old.strip().splitlines()[0][:70]!r}) matches {n} time(s) in {name}, not 1: "
                       "the harness template moved under the 26.2 patch")
        text = text.replace(old, new)
    return text


def harness_registration(c, h, pkg):
    """The harness for a target without @GameTest discovery (26.x): its sources get the target's renames, then
    the generated registrar wires each test by registration (templates/multi-version/tools/gametest_adapter.py;
    CATALOG V5, V20, V47). Only the harness -- the author's tree is never touched. The hand table alone is used,
    not the machine's generated maps: the hand table is all the harness needs (it compiles against the target), and
    the client patch anchors on the text the hand table leaves, which the generated moves would change (GameRules
    moves package) -- so the harness is the same wherever it is generated, CI with no maps included."""
    t, modid = tgt(c), c["args"].modid
    tools = ROOT / "templates/multi-version/tools"
    out = h / "java.renamed"
    r = subprocess.run([sys.executable, str(tools / "prepare-sources.py"), "--src", str(h / "java"), "--renames",
                        str(ROOT / t.rename_table), "--out", str(out)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise Fail(f"renaming the harness for {t.mc} failed: {(r.stderr or r.stdout)[-400:]}")
    shutil.rmtree(h / "java"); out.rename(h / "java")
    patch = ROOT / t.client_patch
    for f in (h / "java").rglob("ClientBootSmokeTest.java"):      # the call shapes a rename cannot express
        f.write_text(apply_patch(f.read_text(encoding="utf-8"), read_patch(patch.read_text(encoding="utf-8")), f.name),
                     encoding="utf-8")
    r = subprocess.run([sys.executable, str(tools / "gametest_adapter.py"), "--root", str(h / "java"), "--modid", modid,
                        "--package", f"{pkg}.gatetest", "--structure", HARNESS_STRUCTURE],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode or "wired 0 tests" in r.stdout:
        raise Fail(f"wiring the harness GameTests failed: {(r.stderr or r.stdout)[-400:]}")


def gate_env(c):
    env = dict(os.environ, PORT_HARNESS_DIR=str(harness(c)), PORT_MODID=c["args"].modid, PORT_LOG_DIR=str(c["dir"]),
               PORT_GRADLE_INIT=str(ROOT / "templates/upstream-harness/gates.init.gradle"))
    return env


def gate_a(c, log):
    lost = srcsets.undeclared_mixin_configs(c["repo"])
    if lost:
        raise Fail(f"mixin config(s) declared in no neoforge.mods.toml [[mixins]] -- they never load: {lost}")
    env = gate_env(c)
    r = gradle(c["repo"], ["test"], log, extra_init=[env["PORT_GRADLE_INIT"]], env=env)
    res = list((c["repo"] / "build" / "test-results/test").glob("*.xml"))
    ran = sum(int(m) for f in res for m in re.findall(r'tests="(\d+)"', f.read_text(encoding="utf-8")))
    bad = sum(int(m) for f in res for m in re.findall(r'(?:failures|errors)="(\d+)"', f.read_text(encoding="utf-8")))
    if r.returncode or bad:
        errs = list(dict.fromkeys(f"{'/'.join(m[0].split('/')[-3:])}:{m[1]}: {m[2].strip()}" for m in re.findall(
            r"(?m)^\s*(\S+\.java):(\d+): error: (.+)$", pathlib.Path(log).read_text(encoding="utf-8", errors="replace"))))[:3]
        raise Fail(f"Gate A failed ({bad} failure(s)); see {log}" + (f" -- first compile error(s): {' | '.join(errs)}" if errs else ""))
    if not ran:
        raise Fail("Gate A ran no tests -- that is not a pass")
    return ran
