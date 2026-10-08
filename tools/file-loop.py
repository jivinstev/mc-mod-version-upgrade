#!/usr/bin/env python3
"""The per-file residual loop (issue #27, plan steps 5b/6-7): fix what the recipes left, one small
context per file batch, with the cheapest model that works, and turn repeated fixes into rewrites.

    python3 tools/file-loop.py --work <gradle project> [--first-model haiku] [--budget 15] [--log run.jsonl]

Each ROUND: compile (javac's error cap lifted) -> group the error files into small batches -> one
headless Claude Code worker per batch, started in a CLEAN context (`claude -p`, no inherited
CLAUDE.md, Read/Edit/Grep/Glob only, no shell) that is given ONLY its files' errors and the
CATALOG.md entries those errors match -> recompile. A worker cannot compile; the loop does.

Model tiers (plan step 6): a batch is worked by --first-model; a file still failing after that goes
to the next tier (haiku -> sonnet -> opus). Every worker's exact dollar cost (Claude Code's own
`total_cost_usd`) and tokens are logged per batch, with the model, so the run answers "which tier
fixed what, for how much".

Propagation (plan step 7, H1): a worker may end with `RULE: <from><TAB><to>` (a rename-table row,
plain / member: / re:). The loop applies each proposed row to the whole tree through
tools/apply-recipes.py, recompiles, and KEEPS it only if the error count fell -- a row that does not
help is reverted, so a bad generalisation costs one compile, never correctness.

Stops at 0 errors, at --budget dollars, at --max-rounds, or when a round fixes nothing.
Standard library only; needs the `claude` CLI on PATH.
"""
import argparse, collections, json, os, pathlib, re, shutil, subprocess, sys, tempfile, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from gitbash import BASH   # noqa: E402
import importlib.util       # noqa: E402
import singleshot           # noqa: E402

_s = importlib.util.spec_from_file_location("rb", ROOT / "tools/recipe-bench.py")
rb = importlib.util.module_from_spec(_s); _s.loader.exec_module(rb)
_ss = importlib.util.spec_from_file_location("srcsets", ROOT / "tools/srcsets.py")
srcsets = importlib.util.module_from_spec(_ss); _ss.loader.exec_module(srcsets)

MODELS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}
TIERS = ["haiku", "sonnet", "opus"]

WORKER = """You are fixing compile errors in a Minecraft mod being ported to {target} (Java 21).
Edit ONLY these files (paths relative to the current directory):
{files}

javac errors in them (file:line: message):
{errors}

CATALOG.md entries these errors match -- each gives the old code shape and its fix:
{entries}

Minecraft/NeoForge sources for checking real signatures: {srcs}
(use Grep/Glob/Read there; never guess an API -- look it up).

Rules:
- Make the smallest change that removes these errors while keeping the code's behaviour. Never stub out
  logic, comment code out, or delete a feature to make an error go away.
- {scope_rule}
- You cannot compile. Reason from the errors and the sources.
Finish with exactly these three lines:
FIXED: <what you changed, one line>
NEEDS: <cross-file changes needed, or none>
RULE: <if one mechanical rewrite would fix this same error wherever it occurs, a rename-table row
`from<TAB>to` (plain name, `member:name`, or `re:<regex>`), else none>"""


def compile_(work, log, heap):
    init = ROOT / "tools/maxerrs.init.gradle"
    with open(log, "w", encoding="utf-8") as fh:
        # every source set the author builds, not just main (tools/srcsets.py); --continue so all report
        subprocess.run([BASH, "gradlew", *srcsets.compile_tasks(work), "--continue", "--console=plain", "--init-script", str(init),
                        "--init-script", str(ROOT / "tools/central-mirror.init.gradle"),
                        f"-Dorg.gradle.jvmargs=-Xmx{heap}"], cwd=work, stdout=fh, stderr=subprocess.STDOUT)
    text = pathlib.Path(log).read_text(encoding="utf-8", errors="replace")
    if "BUILD SUCCESSFUL" in text:
        return 0, []
    r = subprocess.run([BASH, str(ROOT / "tools/burndown-count.sh"), str(log)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:   # a capped, parse-aborted, OOM'd or never-compiled log is not a count
        return None, r.stdout.strip().splitlines()[:3]
    return len(errs := [(rb_rel(f), l, msg) for f, l, msg in rb.parse_errors(text)]), errs


GEN = "/build/generated/sources/"


def rb_rel(f, work=None):
    """A multi-version build (§W) compiles a GENERATED copy; edit the source it came from instead."""
    f = f.replace("\\", "/")
    if GEN in f and "/java/" in f:
        base, tail = f.split(GEN, 1)
        overlay, rest = tail.split("/java/", 1)
        for cand in (f"{base}/src/{overlay}/java/{rest}", f"{base}/src/main/java/{rest}"):
            if pathlib.Path(cand).is_file():
                return cand
    return f


def entry_texts(msgs, sigs, entries, cap=2500, most=4):
    hit = collections.Counter()
    for m in msgs:
        best = None
        for ident, rx, spec in sigs:
            if rx.search(m) and (best is None or spec > best[1]):
                best = (ident, spec)
        if best:
            hit[best[0]] += 1
    out = []
    for ident, _n in hit.most_common(most):
        body = entries.get(ident, "")
        out.append(f"--- §{ident}\n{body[:cap]}" + (" [...]" if len(body) > cap else ""))
    return "\n".join(out) or "(none matched -- these are not in the catalogue yet)", list(hit)


def target_neo_version(work):
    """The NeoForge version the build compiles against NOW: versions/<mc>.properties when the port is
    versioned (gradle.properties `mc=` names the target), else gradle.properties."""
    def props(f):
        return dict(re.findall(r"(?m)^\s*([\w.]+)\s*=\s*(\S+)", f.read_text(encoding="utf-8", errors="replace"))) \
            if f.exists() else {}
    gp = props(work / "gradle.properties")
    vp = props(work / f"versions/{gp.get('mc', '')}.properties") if gp.get("mc") else {}
    return vp.get("neo_version") or gp.get("neo_version")


def find_sources(work):
    """Minecraft + NeoForge sources for workers to grep, FOR THE TARGET THE BUILD COMPILES AGAINST.
    NeoGradle keeps the transformed tree; ModDevGradle keeps sources jars, extracted once per NeoForge
    version into build/file-loop-sources-<version>. Measured: picking the jar by sort order handed a
    26.2 port's workers the 1.21.1 sources (`neoforge-21.1...` sorts after `minecraft-patched-26.2...`),
    and an extraction made before the era hop was never refreshed -- the workers said so and guessed.
    On 26.x the Minecraft sources jar holds no NeoForge API, so NeoForge's own sources jar is added
    from the Gradle cache. None if nothing matches yet (compile first)."""
    for d in sorted(work.glob("build/neoForm/*/steps/transformSource/transformed")):
        if any(d.rglob("Minecraft.java")):
            return d
    neo = target_neo_version(work)
    jars = sorted(work.glob("build/moddev/artifacts/*sources*.jar"))
    if neo:
        jars = [j for j in jars if neo in j.name]
        home = pathlib.Path(os.environ.get("GRADLE_USER_HOME") or pathlib.Path.home() / ".gradle")
        jars += sorted(home.glob(f"caches/modules-2/files-2.1/net.neoforged/neoforge/{neo}/*/neoforge-{neo}-sources.jar"))[:1]
    elif len(jars) > 1:
        return None    # several versions staged and no way to tell the target: never guess (X25b-ii)
    if not jars:
        return None
    out = work / f"build/file-loop-sources-{neo or 'default'}"
    stamp = out / ".from"
    want = "\n".join(sorted({str(j) for j in jars}))
    if not (stamp.exists() and stamp.read_text(encoding="utf-8") == want):
        import shutil, zipfile
        shutil.rmtree(out, ignore_errors=True)
        for j in jars:
            with zipfile.ZipFile(j) as z:
                z.extractall(out, [n for n in z.namelist() if n.endswith(".java")])
        stamp.write_text(want, encoding="utf-8")
    dependency_stubs(work, out)
    return out


def compile_classpath(work):
    """Gradle's own resolved compile classpath (tools/qtc-init.gradle), never one rebuilt from the cache."""
    r = subprocess.run(["bash", "gradlew", "-q", "qtcClasspath", "--init-script", str(ROOT / "tools/qtc-init.gradle"),
                        "--init-script", str(ROOT / "tools/central-mirror.init.gradle")], cwd=work, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=900)
    m = re.search(r"QTC_CLASSPATH_BEGIN\n(.*?)QTC_CLASSPATH_END", r.stdout, re.S)
    return [pathlib.Path(x) for x in m.group(1).split()] if m else []


def javap_stub(text):
    """javap -public output for one class -> a declaration the source index reads: simple type names, and a
    constructor written `public new Name(...)` so the member regex (which wants a return type) lists it."""
    out, outer = [], None
    for l in text.splitlines():
        l = re.sub(r"\b(?:[a-z_][\w]*\.)+([A-Z]\w*)", r"\1", l.rstrip())    # drop package qualifiers
        l = l.replace("$", ".")
        m = re.match(r"^((?:public|protected|abstract|final|static|sealed|non-sealed|\s)*)(class|interface|enum|record)\s+([\w.]+)(.*)\{$", l)
        if m:
            name = m.group(3).rsplit(".", 1)[-1]
            outer = name
            out.append(f"{m.group(1)}{m.group(2)} {name}{m.group(4)}{{"); continue
        if outer and re.match(rf"^\s+(public|protected)[^(]*\b{re.escape(outer)}\(", l) and \
                not re.search(rf"\s\w[\w<>\[\], ?.]*\s+{re.escape(outer)}\(", l.replace("public ", "", 1).replace("protected ", "", 1)):
            l = re.sub(rf"\b{re.escape(outer)}\(", f"new {outer}(", l, count=1)
        out.append(l)
    return "\n".join(out) + "\n"


def dependency_stubs(work, out):
    """Public-API stubs for every MOD jar the build compiles against (Minecraft and NeoForge come from real
    sources above). Measured: a library that renamed its root package and reshaped its registry left 38 errors
    workers could not see an answer for -- its jar ships no sources -- and five Opus calls ($2.18) were spent
    guessing. One javap per jar, cached by the classpath."""
    import zipfile
    try:
        cp = compile_classpath(work)
    except (subprocess.TimeoutExpired, OSError):
        return
    mods = []
    for j in cp:
        if j.suffix != ".jar" or not j.exists():
            continue
        try:
            with zipfile.ZipFile(j) as z:
                names = z.namelist()
        except zipfile.BadZipFile:
            continue
        if any(n in names for n in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml")) and \
                not any(n.startswith("net/minecraft/") for n in names[:2000]):
            mods.append((j, [n[:-6].replace("/", ".") for n in names
                             if n.endswith(".class") and "module-info" not in n and not re.search(r"\$\d", n)]))
    dd = out / "_deps"
    stamp = dd / ".from"
    want = "\n".join(sorted(str(j) for j, _ in mods))
    if stamp.exists() and stamp.read_text(encoding="utf-8") == want:
        return
    import shutil
    shutil.rmtree(dd, ignore_errors=True)
    for j, classes in mods:
        tops = [c for c in classes if "$" not in c]
        for i in range(0, len(tops), 200):
            chunk = tops[i:i + 200]
            nested = [c for c in classes if "$" in c and c.split("$")[0] in set(chunk)]
            r = subprocess.run(["javap", "-public", "-cp", str(j), *chunk, *nested], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=600)
            for block in re.split(r"(?m)^Compiled from [^\n]*\n", r.stdout):
                m = re.search(r"(?m)^[^\n]*\b(?:class|interface|enum|record)\s+([\w.$]+)", block)
                if not m:
                    continue
                fqn = m.group(1)
                top, simple = fqn.split("$")[0], fqn.rsplit(".", 1)[-1].split("$")[-1]
                f = dd / (top.replace(".", "/") + (".java" if "$" not in fqn else "$" + simple + ".java"))
                f.parent.mkdir(parents=True, exist_ok=True)
                pkg = top.rsplit(".", 1)[0]
                f.write_text(f"package {pkg};\n// public API of {j.name}, from javap (no sources ship)\n" + javap_stub(block),
                             encoding="utf-8")
    dd.mkdir(parents=True, exist_ok=True)
    stamp.write_text(want, encoding="utf-8")


def batches(errs, size, max_errs):
    by = collections.defaultdict(list)
    for f, l, m in errs:
        by[f].append((l, m))
    files = sorted(by, key=lambda f: -len(by[f]))
    out, cur, n = [], [], 0
    for f in files:   # big files alone; small files share a worker, up to `size` files / `max_errs` errors
        k = len(by[f])
        if cur and (len(cur) >= size or n + k > max_errs):
            out.append(cur); cur, n = [], 0
        cur.append(f); n += k
    if cur:
        out.append(cur)
    return out, by


def run_worker(work, files, by, sigs, entries, model, target, srcs, timeout, subsystem=False):
    rel = lambda f: os.path.relpath(f, work) if os.path.isabs(f) else f
    errors = "\n".join(f"{rel(f)}:{l}: {m}" for f in files for l, m in sorted(by[f])[:60])
    ent, ids = entry_texts([m for f in files for _l, m in by[f]], sigs, entries)
    scope_rule = ("These errors belong to one subsystem that needs coordinated changes across files: you MAY "
                  "edit or add any file under src/*/java to finish it (keep each change minimal)."
                  if subsystem else
                  "If a fix needs a change in a file not listed, do not edit it: describe it under NEEDS.")
    prompt = WORKER.format(scope_rule=scope_rule, target=target, files="\n".join("  " + rel(f) for f in files), errors=errors,
                           entries=ent, srcs=srcs or "(not available)")
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD"}
    t0 = time.time()
    extra = ["--add-dir", srcs] if srcs else []
    snap = singleshot.sibling_snapshot(work)
    r = subprocess.run(["claude", "-p", prompt, "--model", MODELS[model], "--output-format", "json",
                        "--permission-mode", "acceptEdits", "--allowedTools", "Read,Edit,Write,Grep,Glob", *extra],
                       cwd=work, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    outside = singleshot.restore_siblings(snap)
    try:
        d = json.loads(r.stdout)
    except ValueError:
        d = {"result": (r.stdout + r.stderr)[-500:], "total_cost_usd": 0, "is_error": True}
    res = d.get("result") or ""
    rule = re.search(r'^RULE:\s*(.+)$', res, re.M)
    rule = rule.group(1).strip() if rule and not rule.group(1).strip().lower().startswith("none") else None
    if rule:   # workers write prose: keep the first `from<TAB>to` row, with a literal <TAB> or an arrow allowed
        rule = rule.split("`, `")[0].strip("` ").replace("<TAB>", "\t")
        rule = re.split(r'\s+(?:\(|\||;|--|—)', rule)[0] if "\t" in rule else rule
    return {"model": model, "files": [rel(f) for f in files], "errors_in": sum(len(by[f]) for f in files),
            "entries": ids, "usd": d.get("total_cost_usd") or 0, "usage": d.get("usage"),
            "turns": d.get("num_turns"), "secs": round(time.time() - t0), "is_error": d.get("is_error"),
            "reverted_outside": outside or None,
            "rule": rule, "result": res[-600:]}


def try_rule(work, src, row, n_before, log, heap, n_rule):
    """Apply one proposed row tree-wide; keep it only if the error count falls."""
    if "\t" not in row:
        row = re.sub(r'\s{2,}|\s*->\s*|\s*→\s*', "\t", row, count=1)
    if "\t" not in row:
        return False, "no TAB in the row"
    snap = pathlib.Path(tempfile.mkdtemp()) / "src"
    shutil.copytree(src, snap)
    pack = pathlib.Path(tempfile.mkdtemp()) / "p.recipes.tsv"
    pack.write_text(f"#@ X{n_rule} auto  proposed by a worker\n{row}\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "tools/apply-recipes.py"), "--src", str(src), "--recipes", str(pack)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or " 0 file(s) rewritten" in r.stdout:
        shutil.rmtree(src); shutil.copytree(snap, src)
        return False, (r.stdout + r.stderr).strip().splitlines()[-1:] or ["no change"]
    n, _e = compile_(work, log, heap)
    if n is None or n >= n_before:
        shutil.rmtree(src); shutil.copytree(snap, src)
        return False, f"compile {n} >= {n_before}: reverted"
    return True, f"{n_before} -> {n}"


PROBE_MSG = ("this method no longer overrides anything on the target, so it compiles but is never called "
             "(the supertype's signature changed). Find the current method in the sources, change this "
             "signature to match and add @Override; leave it alone only if it never meant to override")


CLASS_DECL = re.compile(r'\b(?:class|interface|record|enum)\s+(\w+)[^{;]*?(?:\bextends\s+([\w.<>?, ]+?))?'
                        r'(?:\s+implements\s+([\w.<>?, ]+?))?\s*\{', re.S)
METHOD_DECL = re.compile(r'^\s*(?:@\w+\s+)*(?:(?:public|protected|private|static|final|abstract|default|synchronized|native)\s+)*'
                         r'(?:<[^>]+>\s+)?[\w.<>\[\], ?]+\s+(\w+)\s*\(', re.M)


class TypeIndex:
    """Simple-name index over the mod's sources and the game's, to ask: which method names can this
    class's supertypes declare? Ambiguous simple names union their candidates (errs toward keeping)."""

    def __init__(self, *roots):
        self.files = collections.defaultdict(list)
        for root in roots:
            if root and pathlib.Path(root).is_dir():
                for f in pathlib.Path(root).rglob("*.java"):
                    self.files[f.stem].append(f)
        self.memo = {}

    def decl(self, f):
        text = f.read_text(encoding="utf-8", errors="replace")
        m = CLASS_DECL.search(text)
        sup = []
        if m:
            for g in (m.group(2), m.group(3)):
                if g:
                    sup += [re.sub(r'<.*', '', s).strip().split(".")[-1] for s in re.split(r',(?![^<]*>)', g) if s.strip()]
        return sup, set(METHOD_DECL.findall(text))

    def inherited(self, name, depth=0):
        """Method names any supertype of `name` declares (not `name` itself)."""
        if name in self.memo or depth > 10:
            return self.memo.get(name, set())
        self.memo[name] = set()
        out = set()
        for f in self.files.get(name, []):
            sup, _own = self.decl(f)
            for s in sup:
                for g in self.files.get(s, []):
                    out |= self.decl(g)[1]
                out |= self.inherited(s, depth + 1)
        self.memo[name] = out
        return out


def probe(work, sources=None):
    cmd = (f"./gradlew compileJava --console=plain --init-script {ROOT / 'tools/central-mirror.init.gradle'}")
    r = subprocess.run([sys.executable, str(ROOT / "tools/override-probe.py"), str(work), "--compile", cmd],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    hits = re.findall(r'^\s+(\S+\.java):(\d+)\s+(\w+)\((.*)$', r.stdout, re.M)
    idx = TypeIndex(work / "src/main/java", sources)
    keep, dropped = [], 0
    for f, l, name, rest in hits:   # a dead override can only be one of a name some SUPERTYPE declares
        if name in idx.inherited(pathlib.Path(f).stem):
            sup = idx.decl((work / f))[0]   # name the supertypes, so a single-shot worker is shown their declarations
            keep.append((str(work / f), int(l), f"{name}({rest.strip()}: {PROBE_MSG} (this class extends/implements "
                                               f"{', '.join(sup) or '?'})"))
        else:
            dropped += 1
    return keep, r.returncode, dropped


SCAN_R1 = ("this class is registered on an event bus (@EventBusSubscriber or EVENT_BUS.register) but has no "
           "@SubscribeEvent methods, which NeoForge rejects at load (catalogue R1): remove the registration if it "
           "is not an event handler, or annotate its real handlers")
SCAN_CLIENT = ("this class outside a client package imports net.minecraft.client.*; if it can load on a dedicated "
               "server (packet handlers, common events) that crashes the server: move the client code into a "
               "client-only class reached behind a dist check. Leave it if it is only ever loaded on the client")


def static_scan(work):
    """The catalogue's cheap load-crash scans, run before any server boots."""
    src = work / "src/main/java"
    out = []
    for f in src.rglob("*.java"):
        t = f.read_text(encoding="utf-8", errors="replace")
        rel = f.relative_to(src).as_posix()
        if "@EventBusSubscriber" in t and not re.search(r'^\s*@SubscribeEvent', t, re.M):
            out.append((str(f), t[:t.index("@EventBusSubscriber")].count("\n") + 1, SCAN_R1))
        # an inline DistExecutor/Dist.CLIENT check does NOT stop the class loading its client imports
        if not re.search(r'(^|/)(client|mixin|mixins)/', rel) and re.search(r'^import net\.minecraft\.client\.', t, re.M) \
                and "@OnlyIn(Dist.CLIENT)" not in t:
            m = re.search(r'^import net\.minecraft\.client\.', t, re.M)
            out.append((str(f), t[:m.start()].count("\n") + 1, SCAN_CLIENT))
    for f in src.rglob("*.java"):   # EVENT_BUS.register(X.class) on a class with no handlers
        t = f.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'(?:EVENT_BUS|[bB]us)\.register\((\w+)\.class\)', t):
            for g in src.rglob(m.group(1) + ".java"):
                if not re.search(r'^\s*@SubscribeEvent', g.read_text(encoding="utf-8", errors="replace"), re.M):
                    out.append((str(g), 1, SCAN_R1))
    out += mixin_package_strays(work)
    return sorted(set(out))


SCAN_MIXIN_PKG = ("a class with no @Mixin sits in a package a mixin config declares: Mixin refuses to load any "
                  "non-mixin class from that package (\"is in a defined mixin package\") the first time it is "
                  "used. Move it to another package and update its references; keep its behaviour unchanged.")


def mixin_package_strays(work):
    """Non-mixin classes inside a declared mixin package -- compile clean, crash at first use. Measured: a
    helper split out of an accessor during a library's port; only a DEPENDENT mod's Gate B reached it."""
    import importlib.util
    sp = importlib.util.spec_from_file_location("srcsets", ROOT / "tools/srcsets.py")
    ss = importlib.util.module_from_spec(sp); sp.loader.exec_module(ss)
    out = []
    for cfg in ss.mixin_configs(work):
        try:
            pkg = json.loads(cfg.read_text(encoding="utf-8"))["package"]
        except (ValueError, KeyError):
            continue
        for jd in ss.java_dirs(work):
            pdir = jd / pkg.replace(".", "/")
            for f in sorted(pdir.rglob("*.java")) if pdir.is_dir() else []:
                if f.name != "package-info.java" and "@Mixin" not in f.read_text(encoding="utf-8", errors="replace"):
                    out.append((str(f), 1, SCAN_MIXIN_PKG))
    return out


def mixin_audit(work):
    """tools/audit-mixin-targets.py at 0 errors: every @Inject/@Shadow/@Accessor/@Invoker checked against the
    target's own sources, in seconds. A stale target compiles clean and fails at mixin APPLY; found at Gate B it
    costs one server boot per finding (measured: four boots, $1.48, on a library whose audit took 5 seconds)."""
    r = subprocess.run([sys.executable, str(ROOT / "tools/audit-mixin-targets.py"), str(work)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=600)
    if r.returncode != 1:          # 0 = all match; 2 = no mixins / no targets; anything else = no sources jar
        return []
    src, out, cur = work / "src/main/java", [], None
    for l in r.stdout.splitlines():
        m = re.match(r"^  (\S+\.java)\s+(.*\S)\s*$", l)
        if m:
            cur = [m.group(1), m.group(2), "", ""]; out.append(cur); continue
        m = re.match(r"^\s+(mixin|vanilla):\s+(.*)$", l)
        if m and cur:
            cur[2 if m.group(1) == "mixin" else 3] = m.group(2)
    hits = []
    for rel, name, mine, van in out:
        f = next((x for x in (work / rel, src / rel) if x.exists()), None) \
            or next(iter(sorted(src.rglob(pathlib.Path(rel).name))), src / rel)   # the audit may print a bare name
        t = f.read_text(encoding="utf-8", errors="replace") if f.exists() else ""
        key = re.split(r"[\s(]", name.split()[-1] if name.split() else name)[0]
        line = next((i + 1 for i, x in enumerate(t.splitlines()) if key and key in x), 1)
        hits.append((str(f), line, f"mixin target does not match vanilla -- it fails at mixin apply, not at compile: "
                                   f"{name}; mixin: {mine}" + (f"; vanilla: {van}" if van else "")))
    return hits


def subsystem_job(a, work, files, by, sigs, entries, needs_text, idx, note, agent_only=False):
    """A cross-file change: ONE Sonnet request over all the files (and the mod classes the errors and
    the NEEDS notes name) first; the tool-using subsystem agent only when that answer cannot be applied.
    Measured on the step-7 A/B: the agent alone was $3.69 of a $6.83 port."""
    if a.mode == "single" and idx is not None and not agent_only:
        ent, _ids = entry_texts([m for f in files for _l, m in by[f]], sigs, entries, cap=3000, most=5)
        r_ = singleshot.run_multi(work, {f: by[f] for f in files}, needs_text, ent, idx, MODELS["sonnet"],
                                  a.target, thinking=a.multi_thinking)
        r_["model"] = "sonnet"
        if r_["applied"]:
            return r_
        # logged without a "usd" key: its dollars ride on the agent's record below, so totals count them once
        note(event="multi-rejected", rejected=r_["rejected"], multi_usd=r_["usd"], files=r_["files"])
        agent = run_worker(work, files, by, sigs, entries, "sonnet", a.target, a.sources, a.timeout, True)
        agent["usd"] = (agent["usd"] or 0) + (r_["usd"] or 0)
        agent["multi_usd"] = r_["usd"]
        return agent
    return run_worker(work, files, by, sigs, entries, "sonnet", a.target, a.sources, a.timeout, True)


def finding_round(a, work, sigs, entries, note, kind, hits, idx=None):
    """A list of findings (not compile errors) becomes one worker round; returns dollars spent. Probe
    findings are per file and say exactly what is wrong, so they go single-shot when that mode is on;
    scan findings (registrations, client classes on the server) can need other files, so an agent."""
    note(event=kind, hits=len(hits))
    if not hits:
        return 0.0
    groups, by = batches(hits, a.batch_files, a.batch_errors)
    from concurrent.futures import ThreadPoolExecutor
    if kind == "probe" and a.mode == "single" and idx is not None:
        def one(f):
            r_ = singleshot.run_single(work, f, by[f], "(none: these are dead overrides, not compile errors)",
                                       idx, MODELS["sonnet"], a.target, thinking=a.single_thinking)
            r_["model"] = "sonnet"
            return r_
        with ThreadPoolExecutor(6) as ex:
            res = list(ex.map(one, [f for g in groups for f in g]))
    elif kind == "scan":
        with ThreadPoolExecutor(a.parallel) as ex:
            res = list(ex.map(lambda g: subsystem_job(a, work, g, by, sigs, entries,
                                                      "Load-crash scan findings: fix each so the mod loads.", idx, note),
                              groups))
    else:
        with ThreadPoolExecutor(a.parallel) as ex:
            res = list(ex.map(lambda f: run_worker(work, f, by, sigs, entries, a.first_model,
                                                   a.target, a.sources, a.timeout, False), groups))
    spent = 0.0
    for r_ in res:
        spent += r_["usd"]; note(event="worker", round=kind, **r_)
    return spent


def trim_round(plan, left):
    """plan: [(kind, item, estimated usd)] in priority order -> (the ones that fit in `left`, their cost).
    Always keeps the first, so a round never dispatches nothing while money remains."""
    kept, cost = [], 0.0
    for kind_, item, est in plan:
        if kept and cost + est > left:
            continue
        kept.append((kind_, item)); cost += est
    return kept, cost


# what one worker request costs before this run has measured its own (per model; a batch job is priced
# per file): measured means from the ports in docs/EVALS.md, rounded up
PRIOR_USD = {"haiku": 0.05, "sonnet": 0.08, "opus": 0.40}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work", help="the Gradle project (src/main/java is edited in place)")
    ap.add_argument("--self-check", action="store_true", help="test the model-free parts (scans, filters, batching)")
    ap.add_argument("--single-thinking", type=int, default=4000,
                    help="thinking budget for single-shot tiers after the first (the first, Haiku, runs without)")
    ap.add_argument("--mode", default="single", choices=["single", "agent"],
                    help="single (default): one request per file with script-gathered context, agent workers as "
                         "the fallback; agent: tool-using workers only (the step 6/7 spike's design)")
    ap.add_argument("--multi-thinking", type=int, default=8000,
                    help="thinking budget for the one-request cross-file (subsystem) worker")
    ap.add_argument("--first-model", default="haiku", choices=TIERS)
    ap.add_argument("--max-model", default="opus", choices=TIERS)
    ap.add_argument("--budget", type=float, default=15.0, help="stop when workers have spent this many dollars")
    ap.add_argument("--max-rounds", type=int, default=8)
    ap.add_argument("--batch-files", type=int, default=4)
    ap.add_argument("--batch-errors", type=int, default=40)
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--target", default=None, help="default: read from the project's gradle.properties")
    ap.add_argument("--sources", default="auto",
                    help="a directory of Minecraft/NeoForge sources workers may grep (default: found in the build)")
    ap.add_argument("--heap", default="6g")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--log", default="file-loop.jsonl")
    ap.add_argument("--subsystem", action="store_true",
                    help="workers may edit any file (for cross-file rewrites the per-file rounds cannot finish: "
                         "capabilities, networking); use with a large --batch-files")
    ap.add_argument("--no-probe", action="store_true",
                    help="skip the override probe at 0 errors (dead overrides compile clean; catalogue R-class)")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not a.work:
        ap.error("--work is required")
    work = pathlib.Path(a.work).resolve(); src = work / "src/main/java"
    all_java = lambda: (f for d in srcsets.java_dirs(work) for f in d.rglob("*.java"))
    if a.target is None:   # the build says what it targets; a default here once told 26.2 workers "1.21.1"
        a.target = srcsets.target(work) or "NeoForge 1.21.1"
    logf = open(a.log, "a", encoding="utf-8")
    note = lambda **k: (logf.write(json.dumps(k) + "\n"), logf.flush(), print(json.dumps({x: k[x] for x in k if x not in ("result", "usage")})[:300]))
    sigs = rb.signatures()
    entries = {i: b for i, b in rb.catalogue_entries((ROOT / "CATALOG.md").read_text(encoding="utf-8"))}
    # an upstream port must not write into the author's tree: PORT_LOG_DIR (tools/port-upstream.py) moves the log
    clog = pathlib.Path(os.environ.get("PORT_LOG_DIR") or work) / "file-loop-compile.log"
    n, errs = compile_(work, clog, a.heap)
    if a.sources == "auto":
        found = find_sources(work)
        a.sources = str(found) if found else None
    note(event="start", errors=n, sources=a.sources)
    if n is None:
        print("the start does not compile to a count:", errs); return 2
    state = {"spent": 0.0, "rules": 0, "tier_of": {}, "needs": {}, "xtries": {}, "cost_of": {}}
    # the escalation ladder: (model, single-shot?). Single-shot tiers go first in --mode single (the
    # default): one request per file with script-gathered context; agent tiers are the fallback.
    ladder = ([(m, True) for m in ("haiku", "sonnet")] if a.mode == "single" else []) + [(m, False) for m in TIERS]
    ladder = [r for r in ladder if TIERS.index(r[0]) <= TIERS.index(a.max_model)]
    lo = next(i for i, (m, _s) in enumerate(ladder) if m == a.first_model)
    hi = len(ladder) - 1
    idx = singleshot.SourceIndex(a.sources) if a.mode == "single" else None

    def fix_errors(n, errs):
        """Compile rounds until 0, the budget, the last tier, or a plateau."""
        tier_of = state["tier_of"]
        stalled = 0
        for rnd in range(1, a.max_rounds + 1):
            if n == 0 or state["spent"] >= a.budget:
                break
            groups, by = batches(errs, a.batch_files, a.batch_errors)
            jobs = []
            # files whose worker said the fix spans other files go together to ONE subsystem worker
            # (measured: escalating them per file to Opus cost $2.70 and moved 45 -> 33; one Sonnet
            # worker allowed to edit any file then cleared those 33 for $1.11)
            need = [f for f in by if f in state["needs"]]
            multi = None
            if need:
                multi = need[:40]
                agent_only = all(state["xtries"].get(f, 0) >= 2 for f in multi)
                needs_text = "\n".join(f"{os.path.relpath(f, work)}: {state['needs'][f]}" for f in multi)
            singles = []
            for files in groups:   # a batch runs at the highest tier any of its files has reached
                files = [f for f in files if f not in need]
                if not files:
                    continue
                t_ = max(tier_of.get(f, lo) for f in files)
                if t_ > hi:
                    continue
                m, single = ladder[t_]
                if single:   # one request per file
                    singles += [(f, m) for f in files]
                else:
                    jobs.append((files, m, a.subsystem))
            if not jobs and not singles and not multi:
                break
            # keep the round inside the budget: a round dispatches dozens of workers at once, so a check
            # only BETWEEN rounds overran $15 by $4.80 on a large port. Each job is priced at this run's
            # own average for its model (a fixed prior until there is one); the round takes jobs while
            # they fit, always at least one, and the rest wait for the next round.
            left = a.budget - state["spent"]
            avg = lambda m: (state["cost_of"][m][1] / state["cost_of"][m][0]) if state["cost_of"].get(m, [0])[0] \
                else PRIOR_USD.get(m, 0.1)
            plan_ = ([("m", None, 2 * avg("sonnet"))] if multi else []) + \
                [("j", j, avg(j[1])) for j in jobs] + [("s", sj, avg(sj[1])) for sj in singles]
            kept, cost = trim_round(plan_, left)
            if len(kept) < len(plan_):
                note(event="budget-trim", round=rnd, kept=len(kept), of=len(plan_), left=round(left, 3),
                     est=round(cost, 3))
            multi = multi if any(k == "m" for k, _ in kept) else None
            jobs = [i for k, i in kept if k == "j"]; singles = [i for k, i in kept if k == "s"]
            snap = {str(f): f.read_text(encoding="utf-8", errors="replace") for f in all_java()}
            from concurrent.futures import ThreadPoolExecutor

            def one(fm):
                f, m = fm
                ent, _ids = entry_texts([x for _l, x in by[f]], sigs, entries, cap=1500, most=3)
                r_ = singleshot.run_single(work, f, by[f], ent, idx, MODELS[m], a.target,
                                           thinking=0 if m == "haiku" else a.single_thinking)
                r_["model"] = m
                return r_
            with ThreadPoolExecutor(max(a.parallel, 6 if singles else 0)) as ex:
                futs = [ex.submit(run_worker, work, f, by, sigs, entries, m, a.target, a.sources, a.timeout, sub)
                        for f, m, sub in jobs]
                sfuts = [ex.submit(one, fm) for fm in singles]
                mfut = multi and ex.submit(subsystem_job, a, work, multi, by, sigs, entries, needs_text, idx, note,
                                          agent_only)
                results = [fu.result() for fu in futs]
            if multi:
                jobs.insert(0, (multi, "sonnet", True)); results.insert(0, mfut.result())
            sresults = [fu.result() for fu in sfuts]
            jobs += [([f], m, "single") for f, m in singles]
            results += sresults
            for res in results:
                state["spent"] += res["usd"]; note(event="worker", round=rnd, **res)
                c_ = state["cost_of"].setdefault(res.get("model") or "?", [0, 0.0]); c_[0] += 1; c_[1] += res["usd"] or 0
            before = n
            n, errs = compile_(work, clog, a.heap)
            if n is None and any("PARSE ABORT" in l for l in errs):
                # a worker's edit broke a file's syntax: revert just those files to this round's start,
                # move them up a tier, and count again (one bad edit must not stop the round)
                broken = {rb_rel(f) for f, _l, _m in rb.parse_errors(clog.read_text(encoding="utf-8", errors="replace"))}
                for f in broken:
                    if f in snap:
                        pathlib.Path(f).write_text(snap[f], encoding="utf-8")
                    elif pathlib.Path(f).exists():   # a file a cross-file worker created this round
                        pathlib.Path(f).unlink()
                        tier_of[f] = tier_of.get(f, lo) + 1
                note(event="reverted-parse-breaks", round=rnd, files=sorted(os.path.relpath(f, work) for f in broken))
                n, errs = compile_(work, clog, a.heap)
            note(event="compile", round=rnd, errors=n, spent=round(state["spent"], 4))
            if n is None:
                return None, errs
            still = {f for f, _l, _m in errs}
            state["needs"] = {}
            for (files, m, sub), res in zip(jobs, results):
                needs = re.search(r'^NEEDS:\s*(.+)$', res["result"], re.M)
                says_needs = needs and not needs.group(1).strip().lower().startswith("none")
                for f in files:
                    if f in still:
                        if sub is True and state["xtries"].get(f, 0) < 2:
                            # still failing after a cross-file pass: it stays with the cross-file worker
                            # (one more single request with the new errors, then the subsystem agent),
                            # never up the per-file ladder -- measured: that sent 33 files to per-file
                            # Opus agents for $5.52 after a $0.43 cross-file edit had done most of the work
                            state["xtries"][f] = state["xtries"].get(f, 0) + 1
                            state["needs"][f] = (needs.group(1).strip()[:400] if says_needs
                                                 else "still failing after the previous cross-file edit")
                        elif says_needs and sub is not True:   # True = already a subsystem worker
                            state["needs"][f] = needs.group(1).strip()[:400]
                        else:   # a file still failing moves up a tier
                            cur = ladder.index((m, sub == "single")) if (m, sub == "single") in ladder else lo
                            tier_of[f] = max(tier_of.get(f, lo), cur) + 1
            for res in results:
                if res["rule"] and n:
                    state["rules"] += 1
                    ok, why = try_rule(work, src, res["rule"], n, clog, a.heap, state["rules"])
                    if ok:
                        n, errs = compile_(work, clog, a.heap)
                    note(event="rule", round=rnd, row=res["rule"], kept=ok, why=str(why), errors=n)
            # a round that fixed nothing has already moved its files up a tier; stop only when the next one
            # also fails (measured: stopping at the first such round left 2 errors with Sonnet/Opus untried)
            stalled = stalled + 1 if n >= before and not state["needs"] else 0
            if stalled >= 2:
                note(event="plateau", round=rnd, errors=n); break
        return n, errs

    orig = {str(f): f.read_text(encoding="utf-8", errors="replace") for f in all_java()}
    n, errs = fix_errors(n, errs)
    if n is None:
        print("compile no longer counts:", errs); return 3
    # at 0 errors: the load-crash scans, then the dead-override probe; each finding round may break the
    # compile, which the error rounds then repair
    finders = [("scan", lambda: static_scan(work)), ("scan", lambda: mixin_audit(work))]
    if not a.no_probe:
        finders.append(("probe", lambda: probe(work, a.sources)))
    for kind, find in finders:
        if n != 0 or state["spent"] >= a.budget:
            break
        found = find()
        hits, extra = (found[0], {"rc": found[1], "dropped_by_supertype": found[2]}) if kind == "probe" else (found, {})
        if extra:
            note(event="probe-filter", **extra)
        state["spent"] += finding_round(a, work, sigs, entries, note, kind, hits, idx)
        if hits:
            n, errs = compile_(work, clog, a.heap)
            note(event=kind + "-after", errors=n, spent=round(state["spent"], 4))
            if n:
                n, errs = fix_errors(n, errs)
    spent = state["spent"]
    by_model = collections.defaultdict(lambda: [0, 0.0])
    for l in open(a.log, encoding="utf-8"):
        e = json.loads(l)
        if e.get("event") == "worker":
            by_model[e["model"]][0] += 1; by_model[e["model"]][1] += e["usd"]
    # the end-of-loop audit: every file a worker changed -- agents included, whose edits no guard saw --
    # compared with where it started. A removed feature makes errors go away just as well as a fix does.
    stubs = []
    for path, before in orig.items():
        f = pathlib.Path(path)
        after = f.read_text(encoding="utf-8", errors="replace") if f.exists() else ""
        if after == before:
            continue
        why = singleshot.stub_signals(before, after)
        # a signal must also hold against the AUTHOR's file (git HEAD: the port is uncommitted while the loop
        # runs), or an earlier deterministic stage's reshuffle reads as a worker emptying a method the author
        # had already left empty (measured: an upstream no-op event handler flagged as a stub)
        authored = subprocess.run(["git", "-C", work, "show", f"HEAD:{os.path.relpath(path, work)}"],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace")
        if why and authored.returncode == 0:
            kinds = {w.split()[0] for w in singleshot.stub_signals(authored.stdout, after)}
            why = [w for w in why if w.split()[0] in kinds]
        if not f.exists() and singleshot.REMOVED_API.search(before):
            why = []   # a file built on removed Forge API (§13) is meant to go
        elif not f.exists() and singleshot.WIRING.search(before):
            why = ["deleted a file that registered, listened or sent"]
        if why:
            stubs.append({"file": os.path.relpath(path, work), "why": "; ".join(why)})
    if stubs:
        note(event="stub-signals", files=stubs)
    note(event="end", errors=n, spent=round(spent, 4), stubs=len(stubs),
         by_model={k: [v[0], round(v[1], 4)] for k, v in by_model.items()})
    return 0 if n == 0 else 1


def self_check():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d); s = d / "src/main/java/m"; g = d / "game/net/x"
        for p_ in (s / "client", s / "net", g):
            p_.mkdir(parents=True)
        w = lambda p_, txt: p_.write_text(txt, encoding="utf-8")
        w(s / "Handler.java", "package m;\n@EventBusSubscriber\npublic class Handler { static void f() {} }\n")
        w(s / "Real.java", "package m;\n@EventBusSubscriber\npublic class Real {\n @SubscribeEvent\n static void on(E e) {} }\n")
        w(s / "Main.java", "package m;\nclass Main { Main(IEventBus bus) { NeoForge.EVENT_BUS.register(Plain.class); "
                           "event.register(Cap.class); } }\n")
        w(s / "Plain.java", "package m;\nclass Plain {}\n")
        w(s / "Cap.java", "package m;\nclass Cap {}\n")
        w(s / "net/Msg.java", "package m.net;\nimport net.minecraft.client.Minecraft;\nclass Msg { void h() { "
                              "DistExecutor.safeRunWhenOn(Dist.CLIENT, () -> () -> Minecraft.getInstance()); } }\n")
        w(s / "client/Screen.java", "package m.client;\nimport net.minecraft.client.Minecraft;\nclass Screen {}\n")
        w(g / "Block.java", "package net.x;\npublic class Block extends Base {\n public int getLightBlock(State s) { return 0; }\n}\n")
        w(g / "Base.java", "package net.x;\npublic abstract class Base {\n protected boolean propagatesSkylightDown(State s) { return true; }\n}\n")
        w(s / "MyBlock.java", "package m;\npublic class MyBlock extends Block {\n public int getLightBlock() { return 1; }\n"
                              " public int myHelper() { return 2; }\n}\n")
        found = {(pathlib.Path(f).name, msg[:20]) for f, _l, msg in static_scan(d)}
        idx = TypeIndex(d / "src/main/java", d / "game")
        inh = idx.inherited("MyBlock")
        gen = d / "build/generated/sources/mc21/java/m/Plain.java"
        ok = (("Handler.java", SCAN_R1[:20]) in found and ("Plain.java", SCAN_R1[:20]) in found
              and not any(n == "Real.java" or n == "Cap.java" or n == "Screen.java" for n, _m in found)
              and ("Msg.java", SCAN_CLIENT[:20]) in found
              and {"getLightBlock", "propagatesSkylightDown"} <= inh and "myHelper" not in inh
              and pathlib.Path(rb_rel(str(gen))) == s / "Plain.java")
        ok = ok and singleshot.self_check() and singleshot.self_check_multi()
        # the cross-file job hands the caller ONE record, both when its answer applies and when the agent
        # has to take over (a list here crashed the round after a $1+ request)
        import types
        real_multi, real_worker = singleshot.run_multi, run_worker
        fake_a = types.SimpleNamespace(mode="single", multi_thinking=0, target="t", sources="", timeout=1)
        for applied in (True, False):
            singleshot.run_multi = lambda *x, **k: {"applied": applied, "usd": 0.5, "rejected": "x", "files": [], "result": ""}
            globals()["run_worker"] = lambda *x, **k: {"usd": 1.0, "result": "", "rule": None}
            try:
                r_ = subsystem_job(fake_a, ".", ["f"], {"f": [(1, "m")]}, {}, {}, "", object(), lambda **k: None)
            finally:
                singleshot.run_multi = real_multi; globals()["run_worker"] = real_worker
            ok = ok and isinstance(r_, dict) and r_["usd"] == (0.5 if applied else 1.5)
        b, _by = batches([("a", 1, "x")] * 50 + [("b", 1, "x")] * 3 + [("c", 1, "x")] * 3, 4, 40)
        ok = ok and b == [["a"], ["b", "c"]]
    import tempfile, zipfile
    with tempfile.TemporaryDirectory() as d:
        w = pathlib.Path(d); art = w / "build/moddev/artifacts"; art.mkdir(parents=True)
        for name, cls in (("minecraft-patched-26.2.0.75-sources.jar", "New"), ("neoforge-21.1.228-sources.jar", "Old")):
            with zipfile.ZipFile(art / name, "w") as z:
                z.writestr(f"net/minecraft/{cls}.java", "class %s {}" % cls)
        (w / "gradle.properties").write_text("neo_version=21.1.228\nmc=26.2\n", encoding="utf-8")
        (w / "versions").mkdir(); (w / "versions/26.2.properties").write_text("neo_version=26.2.0.75\n", encoding="utf-8")
        os.environ["GRADLE_USER_HOME"] = str(w / "nogradle")
        o = find_sources(w)
        ok &= o is not None and (o / "net/minecraft/New.java").exists() and not (o / "net/minecraft/Old.java").exists()
        (w / "gradle.properties").write_text("neo_version=21.1.228\n", encoding="utf-8")   # flattened back: 1.21.1
        o = find_sources(w)
        ok &= (o / "net/minecraft/Old.java").exists() and not (o / "net/minecraft/New.java").exists()
        os.environ.pop("GRADLE_USER_HOME")
    k_, c_ = trim_round([("m", 1, 0.2), ("s", 2, 0.5), ("s", 3, 0.05), ("s", 4, 0.1)], 0.4)
    ok &= [i for _k, i in k_] == [1, 3, 4] and abs(c_ - 0.35) < 1e-9          # 0.5 does not fit; later ones do
    ok &= [i for _k, i in trim_round([("s", 9, 3.0)], 0.1)[0]] == [9]       # never an empty round
    stub = javap_stub("public class a.b.Foo extends a.b.Base {\n  public a.b.Foo(a.b.Foo$Props);\n"
                      "  public static a.b.Foo of(int);\n  public java.util.List<a.c.Bar> bars();\n}")
    ok = ok and "public new Foo(Foo.Props);" in stub and "public static Foo of(int);" in stub \
        and "List<Bar> bars();" in stub and "class Foo extends Base {" in stub
    print("self-check:", "OK" if ok else f"FAIL {sorted(found)} {sorted(inh)} {b}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
