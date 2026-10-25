#!/usr/bin/env python3
"""Run a finished port's gates in CI -- no model, no workers, a plain pass/fail with the evidence.

    python3 tools/ci-gates.py --repo . --modid <modid> --base <author's ref> [--gatec launch,spawn,battle,gauntlet]
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
import argparse, atexit, shutil, importlib.util, json, os, pathlib, re, subprocess, sys, tempfile, types, zipfile

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


def log_tails(paths, n=40, cap=12000):
    """The last n lines of each log, for printing into a CI job log when a check fails. CI artifacts cannot be
    downloaded from a cloud session (the egress proxy blocks their storage host), so a failure must carry its own
    evidence in the job output. Printed only on failure: a passing run's output is unchanged."""
    out = []
    for f in sorted(pathlib.Path(x) for x in paths):
        if not f.is_file():
            continue
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        out.append(f"----- tail of {f.name} ({min(n, len(lines))} of {len(lines)} lines) -----")
        out += lines[-n:]
    text = "\n".join(out)
    return text[-cap:] if len(text) > cap else text


GATE_OUTPUTS = ("GATES.md", "GATES-RED.md", "COST.md", "RESIDUE.md", "STUBS.json", "REVIEW.md", "TEST-ITEMS.md",   # what a gate run (and
                "INVENTORY-CHECK.json",      # fork-port.py's done record, layer-inventory.py's account) writes into a target
                "DEPS.md",                   # (and layer-deps.py resolve's table, rewritten by fork-port.py's every setup)
                "DRIFT.md",                  # (and the --accept-upstream-drift line fork-port.py appends before the gates)
                "KNOWN-ISSUES.json")         # (and the known-issues ledger, tools/known-issues.py: a record, never port code)
ROOT_RECORDS = ("README.md",)   # records by their path from the repository root (a README in src/ is jar content)


def tested_tree(repo, mc):
    """The commit a version branch's GATES.md (--mc) certifies. A run on an edited checkout tested more than HEAD, so the record
    says so ("<sha> + uncommitted: a, b") instead of naming a commit that does not hold the code that went green; only
    changes to tracked files count, and the run's own outputs do not. Without --mc (a single-version fork, CI's clean
    checkout) it is HEAD, as before."""
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()
    if not mc:
        return head
    st = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"], capture_output=True,
                        text=True, encoding="utf-8").stdout
    dirty = sorted(f for f in (l[3:].strip() for l in st.splitlines() if len(l) > 3)
                   if f.rsplit("/", 1)[-1] not in GATE_OUTPUTS and f not in ROOT_RECORDS)
    if not dirty:
        return head
    return f"{head} + uncommitted: {', '.join(dirty[:6])}{f' (+{len(dirty) - 6} more)' if len(dirty) > 6 else ''}"


def branch_version(repo):
    """The Minecraft version of a fork's VERSION BRANCH checked out at `repo` (tools/fork-branches.py: gradle.properties'
    minecraft_version, with .port/<it>/branch.properties beside it), else None -- the port branch and every
    single-version fork."""
    gp = pathlib.Path(repo) / "gradle.properties"
    m = re.search(r"(?m)^\s*minecraft_version\s*=\s*(\S+)", gp.read_text(encoding="utf-8")) if gp.is_file() else None
    return m.group(1) if m and (pathlib.Path(repo) / ".port" / m.group(1) / "branch.properties").is_file() else None


def read_kv(path):
    p = pathlib.Path(path)
    return dict(re.findall(r"(?m)^\s*([\w.:\\-]+)\s*=\s*(.*?)\s*$", p.read_text(encoding="utf-8"))) if p.is_file() else {}


LIBRARY_SKIP = ("spawn", "battle")


def is_library(repo, bv):
    """A version branch marked library=true (its .port/<bv>/branch.properties). Only a version branch: there the
    library rule stands in for the skipped phases (a dependent's Gate C against the jar, tools/fork-branches.py)."""
    return bool(bv) and read_kv(pathlib.Path(repo) / ".port" / bv / "branch.properties").get("library") == "true"


def library_phases(phases, library):
    """A library fork's Gate C phases: without spawn and battle, which spawn the mod's entities -- a library has none,
    so they check nothing there (issue #133); its versions are proven by a dependent's Gate C instead
    (tools/fork-branches.py library-record). -> (phases kept, phases skipped)."""
    if not library:
        return phases, []
    return [p for p in phases if p not in LIBRARY_SKIP], [p for p in phases if p in LIBRARY_SKIP]


def cf_jars(libs, found):
    """The CurseForge variant jar(s) a build writes beside the shipped one (`*_cf*.jar`): each is booted by the
    shipped-jar gate too, since players install it."""
    return [j for j in sorted(pathlib.Path(libs).glob("*_cf*.jar")) if j != found and "-sources" not in j.name] \
        if pathlib.Path(libs).is_dir() else []


def wiring_ref(repo, bv, base):
    """The sources tools/audit-wiring.py compares the tree with: a version branch's parent branch (origin's, else the
    local one), else the authors' ref (--base); None when neither resolves."""
    cands = []
    if bv:
        parent = read_kv(pathlib.Path(repo) / ".port" / bv / "branch.properties").get("parent")
        if parent:
            cands += [f"origin/{parent}", parent]
    if base:
        cands.append(base)
    for ref in cands:
        if ref.startswith("-"):
            continue                    # never an option to git
        if subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                          capture_output=True).returncode == 0:
            return ref
    return None


def report_row(name, rc, out, prefix):
    """A REPORT row (a finding the run states, never a red gate) for an audit's exit and output, or None when it found
    nothing or could not run: a clean tree's GATES.md is exactly what it was."""
    if rc != 1:
        return None
    lines = [l[len(prefix):].strip() for l in out.splitlines() if l.startswith(prefix)]
    # a crash (a traceback, also exit 1) is no finding: the audit's own count line must close its output
    if not lines or not re.match(r"\d+ (?:finding|stale)", lines[-1]) or len(lines) < 2:
        return None
    hits = lines[:-1]
    return f"| {name} | REPORT | {len(hits)} to read: " + "; ".join(h.replace('|', '/') for h in hits)[:600] + " |"


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
    with _t.TemporaryDirectory() as d:
        (pathlib.Path(d) / "a.log").write_text("\n".join(f"l{i}" for i in range(100)), encoding="utf-8")
        t = log_tails([pathlib.Path(d) / "a.log", pathlib.Path(d) / "missing.log"], n=3)
        ok &= t.splitlines() == ["----- tail of a.log (3 of 100 lines) -----", "l97", "l98", "l99"]
        ok &= log_tails([pathlib.Path(d) / "missing.log"]) == ""
    # the report-only audits: a row only for a finding, so a clean tree's GATES.md is what it was (single-version too)
    ok &= report_row("W", 0, "audit-wiring: 0 finding(s)", "audit-wiring:") is None
    ok &= report_row("W", 2, "audit-wiring: no Java sources", "audit-wiring:") is None
    rr = report_row("W", 1, "audit-wiring: event wiring lost: A|B\naudit-wiring: 1 finding(s) against x", "audit-wiring:")
    ok &= rr == "| W | REPORT | 1 to read: event wiring lost: A/B |" and "FAIL" not in rr
    ok &= report_row("D", 1, "Traceback (most recent call last):\n  File ...\nKeyError: 'x'", "audit-descriptors:") is None
    ok &= report_row("D", 1, "audit-descriptors: something odd\nTraceback ...", "audit-descriptors:") is None
    ok &= report_row("D", 1, "audit-descriptors: a.java:3: \"x\": gone\naudit-descriptors: 1 stale of 4 vanilla", "audit-descriptors:") \
        == "| D | REPORT | 1 to read: a.java:3: \"x\": gone |"
    with _t.TemporaryDirectory() as d:   # wiring_ref: a version branch's parent, else --base, else nothing
        g = lambda *x: subprocess.run(["git", "-C", d, *x], capture_output=True, check=True)
        g("init", "-q", "-b", "neoforge-1.21.1"); g("config", "user.email", "a@b"); g("config", "user.name", "a")
        (pathlib.Path(d) / "f").write_text("x", encoding="utf-8"); g("add", "-A"); g("commit", "-qm", "x")
        (pathlib.Path(d) / ".port/1.21.4").mkdir(parents=True)
        (pathlib.Path(d) / ".port/1.21.4/branch.properties").write_text("parent=neoforge-1.21.1\n", encoding="utf-8")
        ok &= wiring_ref(d, "1.21.4", "authors/main") == "neoforge-1.21.1" and wiring_ref(d, None, "neoforge-1.21.1") == \
            "neoforge-1.21.1" and wiring_ref(d, None, "nosuch/ref") is None and wiring_ref(d, None, None) is None
        (pathlib.Path(d) / ".port/1.21.4/branch.properties").write_text("parent=--output=x\n", encoding="utf-8")
        ok &= wiring_ref(d, "1.21.4", None) is None and not (pathlib.Path(d) / "x").exists()
    with _t.TemporaryDirectory() as d:   # tested_tree: a version branch's run names the edits it tested; HEAD alone when clean
        g = lambda *x: subprocess.run(["git", "-C", d, *x], capture_output=True, check=True)
        g("init", "-q"); g("config", "user.email", "a@b"); g("config", "user.name", "a")
        t = pathlib.Path(d) / ".port/1.21.4"; t.mkdir(parents=True)
        (t / "A.java").write_text("a", encoding="utf-8"); (t / "GATES.md").write_text("g", encoding="utf-8")
        (t / "INVENTORY-CHECK.json").write_text("{}", encoding="utf-8")
        (t / "DEPS.md").write_text("# Dependencies\n", encoding="utf-8")
        (t / "DRIFT.md").write_text("- accepted\n", encoding="utf-8")
        g("add", "-A"); g("commit", "-qm", "c")
        h = subprocess.run(["git", "-C", d, "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                           encoding="utf-8").stdout.strip()
        (t / "GATES.md").write_text("g2", encoding="utf-8")
        (t / "INVENTORY-CHECK.json").write_text("{\"x\": 1}", encoding="utf-8")   # layer-inventory.py's account: an output
        (t / "DEPS.md").write_text("# Dependencies\n\nresolved here\n", encoding="utf-8")   # setup's layer-deps resolve
        (t / "DRIFT.md").write_text("- accepted\n- accepted again\n", encoding="utf-8")   # this run's drift line
        ok &= tested_tree(d, "1.21.4") == h                      # the run's own output is not an edit
        (t / "A.java").write_text("b", encoding="utf-8")
        ok &= tested_tree(d, "1.21.4") == f"{h} + uncommitted: .port/1.21.4/A.java"
        ok &= tested_tree(d, None) == h                          # single-version: HEAD, exactly as before
        # --shipped on a version branch boots the sibling ports' jars beside it (layer-deps.sibling_jars, its own
        # self-check covers a dependent fixture); a fork with no sibling substitute gets no --deps
        ok &= _load("layer_deps", "layer-deps.py").sibling_jars(d, "1.21.4") == []
    # a version branch (tools/fork-branches.py) is known by .port/<its gradle.properties version>/; a single-version
    # fork is not one; a library's Gate C drops spawn and battle; the _cf jar is booted too
    with tempfile.TemporaryDirectory() as d:
        r = pathlib.Path(d)
        (r / "gradle.properties").write_text("minecraft_version=1.21.4\nneo_version=21.4.158\n", encoding="utf-8")
        ok &= branch_version(r) is None                                   # single-version fork: unchanged path
        (r / ".port/1.21.3").mkdir(parents=True); (r / ".port/1.21.3/branch.properties").write_text("x=1\n", encoding="utf-8")
        ok &= branch_version(r) is None                                   # only a parent's carried records: not this one
        (r / ".port/1.21.4").mkdir(); (r / ".port/1.21.4/branch.properties").write_text("library=true\n", encoding="utf-8")
        ok &= branch_version(r) == "1.21.4" and read_kv(r / ".port/1.21.4/branch.properties").get("library") == "true"
        ok &= library_phases(["launch", "spawn", "battle", "gauntlet"], True) == (["launch", "gauntlet"], ["spawn", "battle"])
        ok &= library_phases(["launch", "spawn"], False) == (["launch", "spawn"], [])
        ok &= is_library(r, "1.21.4")
        ok &= not is_library(r, None)              # not a version branch: every phase, as before
        libs = r / "libs"; libs.mkdir()
        for n in ("m-1_mr.jar", "m-1_cf.jar", "m-1_cf-sources.jar"):
            (libs / n).write_bytes(b"x")
        ok &= [j.name for j in cf_jars(libs, libs / "m-1_mr.jar")] == ["m-1_cf.jar"]
    with tempfile.TemporaryDirectory() as d:      # --shipped on a version branch: the variant's expectations file
        r, sg = pathlib.Path(d), _load("shipped_gate", "shipped-gate.py")
        (r / ".port/1.21.4").mkdir(parents=True)
        (r / ".port/1.21.4/shipped-expect-cf.txt").write_text("A.log:x\n", encoding="utf-8")
        ok &= sg.expect_file(r, "1.21.4", "cf") == r / ".port/1.21.4/shipped-expect-cf.txt"
        ok &= sg.expect_file(r, "1.21.4", sg.variant_of("*_mr.jar")) is None
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    if "--self-check" in sys.argv:
        return self_check()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default="."); ap.add_argument("--modid", required=True)
    ap.add_argument("--base", help="the author's ref (mixins they left unlisted stay exempt from Gate A)")
    ap.add_argument("--gatec", default="launch,spawn,battle,gauntlet", help="Gate C phases; '' or 'none' skips Gate C")
    ap.add_argument("--summary", default="ci-gates-summary.md"); ap.add_argument("--work-dir")
    ap.add_argument("--skip-author-build", action="store_true")
    ap.add_argument("--env", choices=("full", "minimal"), default="full",
                    help="full: every mod the build puts on the runtime; minimal: only what neoforge.mods.toml "
                         "requires (optional integrations and dev-only mods removed from the runtime, not the compile)")
    ap.add_argument("--shipped", action="store_true",
                    help="also boot the BUILT jar in a real NeoForge server and read the mod's own logs (tools/shipped-gate.py); "
                         "expectations come from the fork's .port/<mc>/shipped-expect[-<variant>].txt on a version branch, "
                         "else .multiversion/shipped-expect.txt")
    ap.add_argument("--shipped-jar", help="glob (under the build's libs dir) naming the jar a player installs; default: "
                                          ".multiversion/shipped-jar.txt, else the only jar there")
    ap.add_argument("--mc", help="the Minecraft version of the version branch checked out (tools/fork-branches.py; "
                                 "it must be that branch's): the run takes build/.fork-port.lock, so a second run on the "
                                 "same checkout is refused (exit 14)")
    a = ap.parse_args()
    repo = pathlib.Path(a.repo).resolve()
    why = _load("fork_kind", "fork-kind.py").refuse_layer(repo, "ci-gates")
    if why:
        print(f"[ci-gates] {why}", flush=True)
        return 2
    bv = branch_version(repo)        # a version branch (tools/fork-branches.py): a plain build at its own version
    if bv:
        bprops = read_kv(repo / ".port" / bv / "branch.properties")
        # the harness is written for the base version and renamed for this one (port_gates.py)
        os.environ["PORT_BRANCH_MC"] = bv
        os.environ["PORT_BRANCH_BASE"] = bprops.get("base_minecraft_version", "1.21.1")
    if a.mc and bv and a.mc != bv:
        print(f"[ci-gates] --mc {a.mc}: the checkout is version branch {bv}", flush=True)
        return 2
    if a.mc and not bv:
        print(f"[ci-gates] --mc {a.mc}: the checkout is not a version branch (no .port/<its version>/branch.properties); "
              "a single-version fork's gates take no --mc", flush=True)
        return 2
    if a.mc:
        # one gate run per checkout (CATALOG X44): a single-version fork never takes the lock. tools/fork-port.py holds
        # it for its whole run and its gates stage reaches here as a child (let through)
        lock = _load("checkout_lock", "checkout-lock.py").CheckoutLock(repo, "ci-gates.py", a.mc)
        busy = lock.acquire()
        if busy:
            print(f"[ci-gates] {busy}", flush=True)
            return 14
        atexit.register(lock.release)
        print(f"[ci-gates] version branch {bv} (.port/{bv}/branch.properties)", flush=True)
    work = pathlib.Path(a.work_dir or tempfile.mkdtemp(prefix="port-ci-")).resolve()
    work.mkdir(parents=True, exist_ok=True)
    pu, ss = _load("port_gates", "port_gates.py"), _load("srcsets", "srcsets.py")
    bdir = repo / "build"
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
            print(log_tails([lst], n=60), flush=True)
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
        # the harness' Gate A test classes of an earlier run on this checkout are still in the build dir, and with no test
        # source of the author's Gradle reports compileTestJava NO-SOURCE but `test` runs what is left there: the author's
        # build "failed" on our own harness (measured: a second ci-gates run on the same checkout). CI is always fresh.
        shutil.rmtree(bdir / "classes/java/test", ignore_errors=True)
        shutil.rmtree(bdir / "test-results", ignore_errors=True)
        r = pu.gradle(repo, ["build", *jars], work / "author-build.log")
        built = sorted(p.name for p in (bdir / "libs").glob("*.jar")) if (bdir / "libs").is_dir() else []
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
        rep = [l[len("⚠ report:"):].strip() for l in lines if l.startswith("⚠ report:")]
        record("Data -- nothing NeoForge would silently ignore", r.returncode == 0,
               ("1.21-clean" + ("; report: " + rep[0][:240].replace("|", "/") if rep else "")) if r.returncode == 0 else
               "; ".join(l for l in lines if l.startswith(("✗", "REFUSED")))[:300] or lines[-1][:300])

    if ok:     # report only, never red: wiring the port dropped against its parent / the authors' source (CATALOG 12e),
        # and vanilla descriptors kept as strings outside the mixins checked against the build's sources jar (V118)
        against = wiring_ref(repo, bv, a.base)
        if against:
            r = subprocess.run([sys.executable, str(ROOT / "tools/audit-wiring.py"), "--repo", str(repo), "--against", against],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            (work / "audit-wiring.log").write_text(r.stdout + r.stderr, encoding="utf-8")
            print((r.stdout.strip().splitlines() or ["audit-wiring: no output"])[-1], flush=True)
            row = report_row(f"Wiring -- subscribers and capabilities against `{against}` (report)", r.returncode,
                             r.stdout, "audit-wiring:")
            if row:
                rows.append(row)
        r = subprocess.run([sys.executable, str(ROOT / "tools/audit-descriptors.py"), str(repo)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        (work / "audit-descriptors.log").write_text(r.stdout + r.stderr, encoding="utf-8")
        print((r.stdout.strip().splitlines() or ["audit-descriptors: no output"])[-1], flush=True)
        row = report_row("Descriptors -- vanilla names in strings outside the mixins (report)", r.returncode, r.stdout,
                         "audit-descriptors:")
        if row:
            rows.append(row)

    if ok:     # static: a payload codec that rejects every send (client disconnect, no server-side gate sees it)
        r = subprocess.run([sys.executable, str(ROOT / "tools/audit-unit-codecs.py"), str(repo / "src/main/java")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        lines = [l.strip() for l in r.stdout.splitlines() if l.strip()]
        record("Payloads -- every StreamCodec.unit payload has equal instances", r.returncode == 0,
               (lines[0] if r.returncode else lines[-1] if lines else "no output")[:300])

    if ok and a.env == "minimal" and ex:
        # Before booting anything: does code the minimal run will load need a mod it leaves out?
        jars = [path for coord, path in arts if coord in ex]
        cls = bdir / "classes/java/main"
        r = subprocess.run([sys.executable, str(ROOT / "tools/optional-dep-scan.py"), "--classes", str(cls),
                            *[x for j in jars for x in ("--jar", j)]],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        (work / "optional-dep-scan.log").write_text(r.stdout + r.stderr, encoding="utf-8")
        print(r.stdout.rstrip(), flush=True)
        last = (r.stdout.strip().splitlines() or ["no output"])[-1]
        record("Optional mods -- nothing the mod loads needs one", r.returncode == 0,
               last.replace("optional-dep-scan: ", "")[:300])

    env = dict(os.environ, **pu.gate_env(c))
    library = is_library(repo, bv)
    phases, skipped = library_phases([q for q in a.gatec.split(",") if q and q != "none"], library)
    for q in skipped:
        rows.append(f"| Gate C -- real client, `{q}` | skipped | a library: no entity of its own to spawn; proven by a "
                    "dependent's Gate C against this jar (tools/fork-branches.py library-record) |")
        print(f"[ci-gates] Gate C {q}: skipped (library)", flush=True)
    for label, extra in [("Gate B -- headless server loads the mod, GameTests", [])] + \
            [(f"Gate C -- real client, `{p}`", ["--gatec", p]) for p in phases]:
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
            lines = (m.group(1) if m else (r.stdout + r.stderr)[-600:]).strip().splitlines()
            items = [x.strip()[2:] for x in lines if x.lstrip().startswith("- ")]   # a list of findings: name them
            detail = ("; ".join(items[:3]) + (f"; +{len(items) - 3} more" if len(items) > 3 else "") if items
                      else lines[0] if lines else "no output")[:300]
            print(r.stdout[-6000:], flush=True)
        else:
            gl = pathlib.Path(env["PORT_LOG_DIR"]) / f"gate-loop{'-' + extra[1] if extra else ''}.log"
            text = gl.read_text(encoding="utf-8", errors="replace") if gl.exists() else ""
            detail = green_detail(text)
            if extra:     # a green client with recorded pre-existing defects says so, never silently
                gl = _load("gate_loop", "gate-loop.py")
                _b, known = gl.log_findings(text, a.modid, repo)
                if known:
                    detail += f"; {len(known)} known pre-existing defect(s) listed in .github/gatec-known.txt"
                foreign = gl.foreign_findings(text, repo)
                if foreign:   # another mod's code threw during this run: not this port's failure, but its trail
                    print("[ci-gates] WARNING: logged exceptions thrown in OTHER mods' code during this run "
                          "(report each to that mod):\n" + "\n".join(f"  - {f}" for f in foreign), flush=True)
                    detail += (f"; warning: {len(foreign)} logged exception(s) in other mods' code, e.g. "
                               f"{foreign[0].split(', caught')[0]}")
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

    if a.shipped:
        if not ok:
            rows.append("| Shipped jar -- the built jar boots in a real server | not run | an earlier gate failed |")
        else:
            libs = bdir / "libs"
            sj = repo / (f".port/{bv}" if bv else ".multiversion") / "shipped-jar.txt"
            pat = a.shipped_jar or (sj.read_text(encoding="utf-8").split() or [""])[0] \
                if (a.shipped_jar or sj.is_file()) else None
            found = sorted(libs.glob(pat)) if pat and libs.is_dir() else (sorted(libs.glob("*.jar")) if libs.is_dir() else [])
            label = "Shipped jar -- the built jar boots in a real server, the mod's own logs show its subsystems ran"
            if len(found) != 1:
                record(label, False, f"cannot tell which jar a player installs: {len(found)} match(es) in {libs}"
                                     f"{' for ' + pat if pat else ''}; pass --shipped-jar GLOB or write .multiversion/shipped-jar.txt")
            for jar in ([found[0]] + (cf_jars(libs, found[0]) if a.mc else [])) if len(found) == 1 else []:
                first = jar == found[0]
                log_name = "shipped-gate.log" if first else f"shipped-gate-{jar.stem}.log"
                cmd = [sys.executable, str(ROOT / "tools/shipped-gate.py"), "--repo", str(repo), "--jar", str(jar),
                       "--work-dir", str(work / ("shipped" if jar == found[0] else f"shipped-{jar.stem}"))]
                if bv:
                    gp = repo / "gradle.properties"
                    m = re.search(r"^neo_version\s*=\s*(\S+)", gp.read_text(encoding="utf-8"), re.M) if gp.is_file() else None
                    cmd += ["--mc", bv] + (["--neo", m.group(1)] if m else [])
                    # .port/<bv>/shipped-expect-<variant>.txt (cf for a *_cf*.jar beside it), else the unsuffixed one
                    sg = _load("shipped_gate", "shipped-gate.py")
                    ef = sg.expect_file(repo, bv, sg.variant_of(pat) if first else "cf")
                    if ef:
                        cmd += ["--expect-file", str(ef)]
                    sib = _load("layer_deps", "layer-deps.py").sibling_jars(repo, bv)
                    if sib:
                        cmd += ["--deps", *sib]
                else:
                    gp = repo / "gradle.properties"
                    m = re.search(r"^neo_version\s*=\s*(\S+)", gp.read_text(encoding="utf-8"), re.M) if gp.is_file() else None
                    if m:
                        cmd += ["--neo", m.group(1)]
                    for n in ("shipped-expect.txt",):
                        if (repo / ".multiversion" / n).is_file():
                            cmd += ["--expect-file", str(repo / ".multiversion" / n)]
                r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
                (work / log_name).write_text(r.stdout + r.stderr, encoding="utf-8")
                print(r.stdout[-4000:] if r.returncode == 0 else r.stdout + r.stderr, flush=True)
                if r.returncode != 0:   # the mod's own logs and latest.log: why an agent/coremod did not run (#88)
                    print(log_tails(sorted((work / ("shipped" if first else f"shipped-{jar.stem}") / "server" / "logs").glob("*.log"))), flush=True)
                rl = [l.replace("[shipped-gate] ", "") for l in r.stdout.splitlines() if "FAIL" in l or "PASS" in l]
                bad = [l for l in rl if l.startswith("FAIL")]
                record(label if first else f"{label} ({jar.name})", r.returncode == 0,
                       f"{jar.name}: " + ("; ".join(l[:160] for l in bad[:2]) + f" (see {work / log_name})" if bad
                                                  else f"{len([l for l in rl if l.startswith('PASS')])} check(s) green"
                                                       if r.returncode == 0 else (r.stdout + r.stderr).strip().splitlines()[-1][:200]))

    head = tested_tree(repo, a.mc)
    md = [f"Gates for `{a.modid}` at `{head}`{f' for Minecraft {a.mc}' if a.mc else ''} ({a.env} environment) -- run by CI with no model and no fixes applied:", "",
          "| check | result | detail |", "|---|---|---|", *rows, "",
          "Not covered by any automated gate: gameplay by a person."]
    pathlib.Path(a.summary).write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
