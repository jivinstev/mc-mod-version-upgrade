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
    cmd = ["bash", "gradlew", "--console=plain", "-I", str(ROOT / "tools/central-mirror.init.gradle")]
    for i in extra_init:
        cmd += ["-I", str(i)]
    # a fork's multi-version layer builds another Minecraft from the same tree (.multiversion/build.sh's -P set,
    # which tools/ci-gates.py --mc puts here); every Gradle call of a gate run must carry it, or the run tests 1.21.1
    cmd += gradle_target_args(env)
    return sh(cmd + list(args) + ["-Dorg.gradle.jvmargs=-Xmx6g"], cwd=repo, env=env, timeout=timeout, log=log)


def build_dir_name(env=None):
    """The build directory of this run: a fork layer builds target <t> into build/mc<t>/ (layer.gradle)."""
    for a in gradle_target_args(env):
        if a.startswith("-Pmc="):
            return "build/mc" + a[len("-Pmc="):]
    return "build"


def gradle_target_args(env=None):
    """PORT_GRADLE_ARGS: extra Gradle arguments for every call of a gate run, newline-separated."""
    raw = (env if env is not None else os.environ).get("PORT_GRADLE_ARGS", "")
    return [a for a in raw.splitlines() if a.strip()]


def tgt(c):
    """The Target of this run: from --branch normally; from gradle.properties for a caller that never parsed
    arguments (tools/ci-gates.py builds the same context to reuse the harness and Gate A)."""
    if "target" not in c:
        c["target"] = targets.target_of_repo(c["repo"])
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


def harness(c):
    """The external harness: a baseline GameTest, its structure, the mixin integrity test."""
    h, modid = c["dir"] / "harness", c["args"].modid
    # reuse a harness only when it was built for this target by this harness code: a stale one fails the same way
    # on every later run (measured: a pre-1.21.5-adapter harness, reused through a persistent --work-dir)
    stamp = f"{layer_target() or 'base'}\n{_harness_code_hash()}"
    if h.exists() and (h / ".built-for").is_file() and (h / ".built-for").read_text(encoding="utf-8") == stamp:
        return h
    shutil.rmtree(h, ignore_errors=True)
    pkg = (re.search(r"(?m)^package\s+([\w.]+)\s*;", next((c["repo"] / "src/main/java").rglob("*.java")).read_text(
        encoding="utf-8")) or [None, modid])[1].split(".")
    pkg = ".".join(pkg[:2]) if len(pkg) > 1 else pkg[0]
    gt = (ROOT / "templates/neoforge-mod/test-templates/BaselineGameTest.java.example").read_text(encoding="utf-8")
    gt = re.sub(r"(?m)^package\s+[\w.]+;", f"package {pkg}.gatetest;", gt).replace('"examplemod"', f'"{modid}"')
    (h / "java" / pkg.replace(".", "/") / "gatetest").mkdir(parents=True)
    (h / "java" / pkg.replace(".", "/") / "gatetest/BaselineGameTest.java").write_text(gt, encoding="utf-8")
    tg0 = tgt(c)
    if tg0.gametest != "registration":     # data liveness: annotation-discovered targets (1.21.x); 26.x registers tests
        dl = (ROOT / "templates/neoforge-mod/test-templates/DataLivenessGameTest.java.example").read_text(encoding="utf-8")
        dl = re.sub(r"(?m)^package\s+[\w.]+;", f"package {pkg}.gatetest;", dl).replace('"examplemod"', f'"{modid}"')
        (h / "java" / pkg.replace(".", "/") / "gatetest/DataLivenessGameTest.java").write_text(dl, encoding="utf-8")
    sd = h / "resources/data" / modid / "structure"; sd.mkdir(parents=True)
    import gzip, importlib.util
    spec = importlib.util.spec_from_file_location("ges", ROOT / "tools/gen-empty-structure.py")
    ges = importlib.util.module_from_spec(spec); spec.loader.exec_module(ges)
    tg = tgt(c)
    world_version = targets.derive_pack(tg, [c["repo"]])[0]["world_version"]     # the structure's DataVersion: the target's
    (sd / "empty_test.nbt").write_bytes(gzip.compress(ges.build(modid, "empty_test", 9, world_version)))
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
    """A layer target past 1.21.5: the (already renamed) harness tests are wired by the generated registrar and
    their dead annotations stripped, by the same templates/multi-version/tools/gametest_adapter.py the 26.x route uses.
    The 1.21.5 dialect keeps ResourceLocation and FML's @EventBusSubscriber bus attribute; 26.x is the adapter's default."""
    dialect = "26" if mc_tuple(mc) >= (26,) else "1.21.5"
    r = subprocess.run([sys.executable, str(ROOT / "templates/multi-version/tools/gametest_adapter.py"), "--root",
                        str(h / "java"), "--modid", c["args"].modid, "--package", f"{pkg}.gatetest", "--structure",
                        "empty_test", "--dialect", dialect],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode or "wired 0 tests" in r.stdout:
        raise Fail(f"wiring the harness GameTests for {mc} failed: {(r.stderr or r.stdout)[-400:]}")


def layer_target(env=None):
    """The Minecraft target a fork's multi-version layer builds in this run (-Pmc in PORT_GRADLE_ARGS), or None."""
    for a in gradle_target_args(env):
        if a.startswith("-Pmc="):
            return a[len("-Pmc="):]
    return None


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
    """A fork's multi-version layer builds the author's tree for another 1.21.x; the harness (written for 1.21.1)
    gets that target's own rename table, the same mechanism as the layer's."""
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
                        "--package", f"{pkg}.gatetest", "--structure", "empty_test"],
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
    res = list((c["repo"] / build_dir_name() / "test-results/test").glob("*.xml"))
    ran = sum(int(m) for f in res for m in re.findall(r'tests="(\d+)"', f.read_text(encoding="utf-8")))
    bad = sum(int(m) for f in res for m in re.findall(r'(?:failures|errors)="(\d+)"', f.read_text(encoding="utf-8")))
    if r.returncode or bad:
        errs = list(dict.fromkeys(f"{'/'.join(m[0].split('/')[-3:])}:{m[1]}: {m[2].strip()}" for m in re.findall(
            r"(?m)^\s*(\S+\.java):(\d+): error: (.+)$", pathlib.Path(log).read_text(encoding="utf-8", errors="replace"))))[:3]
        raise Fail(f"Gate A failed ({bad} failure(s)); see {log}" + (f" -- first compile error(s): {' | '.join(errs)}" if errs else ""))
    if not ran:
        raise Fail("Gate A ran no tests -- that is not a pass")
    return ran
