#!/usr/bin/env python3
"""
Boot the REAL Minecraft instance — the one the player actually launches — and report whether
it still starts with the mods currently deployed into it.

WHY THIS EXISTS, and why the Gate-C harnesses do not cover it
-------------------------------------------------------------
Every `tools/*-client.sh` in Battle of Lord and Modding-from-a-Mod runs `./gradlew runClient`:
a DEV client, holding one mod and its declared dependencies. That is the right tool for "does
my feature work", and it is blind to the entire class of failure that only exists in the
player's instance, where ~60 third-party jars sit next to ours:

  * two jars declaring the SAME mod id (NeoForge refuses to boot — not a warning, a hard stop),
  * a jar built for the wrong Minecraft version,
  * a dependency another mod needed that is missing or the wrong version.

The first of those is not hypothetical. Battle of Lord's jar was renamed when it gained a second
Minecraft target (`examplemod-1.0.0.jar` -> `examplemod-mc1.21.1-1.0.0.jar`), so deploying
left BOTH in the mods folder. Every gate in both repos was green; the player's game would not
have started. Nothing caught it because nothing was looking at the folder the game reads.

TWO LAYERS, because only one of them needs a computer with a screen
-------------------------------------------------------------------
  preflight   Reads the jars. No JVM, no display, no Minecraft install needed beyond the folder.
              Catches duplicate ids and version-range mismatches in about a second.
              THIS RUNS ANYWHERE — a Linux VM, CI, a cloud session.
  boot        Actually launches the client and watches for the sound engine. Needs the instance
              and a display (or Xvfb on Linux). Local machines only.

`--preflight` alone is the default when no instance can be booted, so the tool degrades to the
half that still works rather than reporting nothing.

USAGE
    tools/boot-smoke.py                                  # preflight + boot, auto-detected instance
    tools/boot-smoke.py --preflight                      # jar checks only (VM/CI safe)
    tools/boot-smoke.py --expect examplemod --expect anothermod
    tools/boot-smoke.py --instance ~/.minecraft --version neoforge-21.1.233

EXIT CODES (the convention both mod repos' harnesses already use)
    0 = PASS    1 = FAIL    2 = setup error    3 = no verdict / timeout
"""
import argparse, json, os, pathlib, platform, re, shutil, subprocess, sys, time, zipfile

# ── platform ────────────────────────────────────────────────────────────────────────────────
SYS = platform.system()
IS_MAC, IS_LINUX = SYS == "Darwin", SYS == "Linux"
# Mojang's own names for the current box, used to evaluate the version JSON's library rules.
MOJANG_OS = {"Darwin": "osx", "Linux": "linux", "Windows": "windows"}.get(SYS, SYS.lower())
MOJANG_ARCH = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x86_64", "AMD64": "x86_64"} \
    .get(platform.machine(), platform.machine())
# Where the launcher unpacks its own JREs, per platform.
RUNTIME_DIR = {("Darwin", "arm64"): "mac-os-arm64", ("Darwin", "x86_64"): "mac-os",
               ("Linux", "x86_64"): "linux", ("Linux", "arm64"): "linux",
               ("Windows", "x86_64"): "windows-x64"}.get((SYS, MOJANG_ARCH))

DEFAULT_INSTANCE = {
    "Darwin": "~/Library/Application Support/minecraft",
    "Linux": "~/.minecraft",
    "Windows": "~/AppData/Roaming/.minecraft",
}.get(SYS, "~/.minecraft")


def die(msg, code=2):
    print(f"boot-smoke: {msg}", file=sys.stderr)
    sys.exit(code)


# ── instance discovery ──────────────────────────────────────────────────────────────────────
def find_instance(explicit):
    """An explicit path wins; then $MINECRAFT_DIR; then the parent of MINECRAFT_MODS_DIR in any
    sibling repo's .env.local (which is where both mod repos already record it); then the
    platform default."""
    cands = []
    if explicit:
        cands.append(pathlib.Path(explicit).expanduser())
    if os.environ.get("MINECRAFT_DIR"):
        cands.append(pathlib.Path(os.environ["MINECRAFT_DIR"]).expanduser())
    here = pathlib.Path(__file__).resolve().parent.parent
    for env in list(here.parent.glob("*/.env.local")) + [here / ".env.local"]:
        try:
            for line in env.read_text().splitlines():
                if line.startswith("MINECRAFT_MODS_DIR="):
                    p = pathlib.Path(line.split("=", 1)[1].strip().strip('"')).expanduser()
                    cands.append(p.parent)
        except OSError:
            pass
    cands.append(pathlib.Path(DEFAULT_INSTANCE).expanduser())
    for c in cands:
        if (c / "mods").is_dir() or (c / "versions").is_dir():
            return c
    return None


def pick_version(inst, explicit):
    vers = inst / "versions"
    if explicit:
        return explicit
    prof = inst / "launcher_profiles.json"
    if prof.exists():
        try:
            d = json.loads(prof.read_text())
            for p in d.get("profiles", {}).values():
                v = p.get("lastVersionId", "")
                if v and (vers / v).is_dir() and "latest" not in v:
                    return v
        except (OSError, ValueError):
            pass
    # Newest modded version folder wins over a vanilla one — a modded instance is the point.
    mods = sorted((p.name for p in vers.glob("*") if p.is_dir()
                   and re.match(r"(neoforge|forge|fabric)", p.name)), reverse=True)
    if mods:
        return mods[0]
    die(f"no version found under {vers} — pass --version")


# ── PREFLIGHT: the half that runs on a VM ───────────────────────────────────────────────────
def declared_mod_ids(jar):
    """The mod ids a jar REGISTERS -- from its [[mods]] blocks only.

    Not every `modId =` in a mods.toml is a mod the jar provides: [[dependencies.x]] blocks use
    the same key for things the jar merely NEEDS. Reading them all reported every one of 60 jars
    as "duplicating minecraft", which is both true and completely useless."""
    ids = []
    try:
        with zipfile.ZipFile(jar) as z:
            names = set(z.namelist())
            toml = next((n for n in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml")
                         if n in names), None)
            if toml:
                # Deliberately a hand-rolled section split, not a TOML parser: this has to run on
                # a bare VM with only the standard library, and Python 3.10 has no tomllib.
                section = None
                for line in z.read(toml).decode("utf-8", "replace").splitlines():
                    line = line.strip()
                    if line.startswith("[[") or line.startswith("["):
                        section = line.split("#")[0].strip()
                    elif section == "[[mods]]":
                        m = re.match(r'modId\s*=\s*"([^"]+)"', line)
                        if m:
                            ids.append(m.group(1))
            elif "fabric.mod.json" in names:
                d = json.loads(z.read("fabric.mod.json"))
                if d.get("id"):
                    ids.append(d["id"])
    except (OSError, zipfile.BadZipFile, ValueError):
        return []
    return ids


def preflight(mods_dir, expect):
    """Read the jars and answer the one question that reliably predicts a dead instance.

    DELIBERATELY NOT CHECKED: whether each jar's declared Minecraft versionRange contains the
    instance's version. It sounds like the obvious second check and it does not work -- JEI ships
    `versionRange="[1.21, 1.21.1)"` as a REQUIRED minecraft dependency and loads perfectly on
    1.21.1, as do a dozen others here. A check that flags 36 healthy mods teaches everyone to
    ignore the tool, and NeoForge reports a genuine version mismatch clearly at load anyway."""
    jars = sorted(mods_dir.glob("*.jar"))
    print(f"preflight: {len(jars)} jars in {mods_dir}")
    owners = {}
    for j in jars:
        for i in declared_mod_ids(j):
            owners.setdefault(i, []).append(j.name)

    problems = []
    # THE trap this tool exists for. NeoForge stops dead on a duplicate id, so a jar RENAME that
    # leaves the old file behind bricks the instance while every build in every repo stays green.
    for i, files in sorted(owners.items()):
        if len(files) > 1:
            problems.append(f"DUPLICATE mod id '{i}' in {len(files)} jars: {', '.join(files)}")
    for e in expect:
        if e not in owners:
            problems.append(f"EXPECTED mod '{e}' is not in the mods folder at all")
        else:
            print(f"  ok: {e} -> {owners[e][0]}")
    for p in problems:
        print(f"  !! {p}")
    if not problems:
        print(f"  ok: {len(owners)} mods, every id unique")
    return problems


# ── BOOT: reconstruct the launcher's own command line ───────────────────────────────────────
def rules_ok(entry):
    """Mojang's rule evaluation. Two things here are each a bug that already happened:
    a rule gated on a launcher FEATURE (demo, custom resolution, quickPlay) must be REJECTED --
    accepting it emits a bare `--width` with no value and the game dies parsing its own args;
    and the os name/arch must both be checked."""
    rules = entry.get("rules")
    if not rules:
        return True
    allowed = False
    for r in rules:
        if r.get("features"):
            continue                       # we enable no launcher features
        o = r.get("os", {})
        if "name" in o and o["name"] != MOJANG_OS:
            continue
        if "arch" in o and o["arch"] != MOJANG_ARCH:
            continue
        if "version" in o:
            continue                       # os-version regexes: ignore rather than guess
        allowed = r.get("action") == "allow"
    return allowed


def find_java(inst, manifest):
    """The launcher's OWN JRE first. Using an arbitrary JDK is not neutral: a Temurin JDK on
    macOS failed `glfwGetPrimaryMonitor` on a machine whose real launcher boots fine, which
    reads exactly like a broken display and is not one."""
    comp = (manifest.get("javaVersion") or {}).get("component", "java-runtime-delta")
    if RUNTIME_DIR:
        base = inst / "runtime" / comp / RUNTIME_DIR / comp
        for cand in (base / "jre.bundle" / "Contents" / "Home" / "bin" / "java",
                     base / "bin" / "java"):
            if cand.exists():
                return str(cand), f"launcher JRE ({comp})"
    if os.environ.get("JAVA_HOME"):
        j = pathlib.Path(os.environ["JAVA_HOME"]) / "bin" / "java"
        if j.exists():
            return str(j), "JAVA_HOME"
    j = shutil.which("java")
    return (j, "java on PATH") if j else (None, None)


def build_command(inst, version, game_dir=None):
    chain, name = [], version
    while name:
        f = inst / "versions" / name / f"{name}.json"
        if not f.exists():
            die(f"missing version manifest: {f}")
        d = json.loads(f.read_text())
        chain.append(d)
        name = d.get("inheritsFrom")

    merged = {}
    for d in reversed(chain):
        merged.update({k: v for k, v in d.items() if k not in ("arguments", "libraries")})

    libs_dir, seen, cp = inst / "libraries", set(), []
    for d in chain:                                  # child overrides parent
        for l in d.get("libraries", []):
            if not rules_ok(l):
                continue
            parts = l["name"].split(":")
            # The CLASSIFIER is part of a library's identity. Keying on group:artifact alone
            # silently drops every `natives-*` jar, and LWJGL then cannot find liblwjgl.
            key = ":".join(parts[:2] + parts[3:4])
            if key in seen:
                continue
            seen.add(key)
            art = (l.get("downloads") or {}).get("artifact") or {}
            p = libs_dir / art["path"] if art.get("path") else \
                libs_dir.joinpath(*parts[0].split("."), parts[1], parts[2],
                                  "-".join(parts[1:3]) + ".jar")
            cp.append(str(p))
    # The version jar, IF the layout has one. Two NeoForge eras differ here and demanding it
    # unconditionally made the newer one unbootable:
    #   * 21.1.x   mainClass cpw...Launcher -- the launcher COPIES the vanilla client jar into
    #              versions/<neoforge-id>/, and nothing in the manifest's libraries replaces it,
    #              so it genuinely has to be on the classpath.
    #   * 26.x     mainClass net.neoforged.fml.startup.Client -- the installer pre-patches the
    #              client to libraries/net/neoforged/minecraft-client-patched/, FML finds it via
    #              -DlibraryDirectory, and versions/<neoforge-id>/ holds only a .json. Requiring a
    #              jar there reported one MISSING LIBRARY and the honest-but-useless advice to
    #              "open the launcher once to repair" -- which can never create that file.
    # Absent is only a fault when the manifest gives us no other route to the client.
    vjar = inst / "versions" / merged["id"] / f"{merged['id']}.jar"
    if vjar.exists():
        cp.append(str(vjar))
    missing = [p for p in cp if not pathlib.Path(p).exists()]
    if not vjar.exists() and not merged.get("mainClass", "").startswith("net.neoforged.fml.startup"):
        missing.append(str(vjar))

    jvm_a, game_a = [], []
    for d in reversed(chain):
        for sect, out in (("jvm", jvm_a), ("game", game_a)):
            for a in d.get("arguments", {}).get(sect, []):
                if isinstance(a, str):
                    out.append(a)
                elif rules_ok(a):
                    v = a["value"]
                    out.extend(v if isinstance(v, list) else [v])

    natives = inst / "versions" / merged["id"] / "natives"
    # The VANILLA launcher separates these two: versions/, libraries/ and assets/ live in its own
    # installation directory, while each profile's "Game Directory" holds mods/, saves/ and config/.
    # That split is exactly how a second Minecraft version is kept apart from the one being played
    # (battle-of-lord/docs/SECOND_INSTANCE.md), so a tool whose job is booting THE PLAYER'S instance
    # has to model it. Before this, --instance had to be both at once and the 26.2 game directory --
    # the very folder that doc tells you to check -- could not be booted at all: it has no versions/.
    # Prism/MultiMC-style layouts pass one path and keep the old behaviour.
    gdir = game_dir or inst
    repl = {"auth_player_name": "BootSmoke", "version_name": merged["id"],
            "game_directory": str(gdir), "assets_root": str(inst / "assets"),
            "assets_index_name": (merged.get("assetIndex") or {}).get("id", merged.get("assets", "")),
            "auth_uuid": "0" * 32, "auth_access_token": "0", "clientid": "0", "auth_xuid": "0",
            "user_type": "legacy", "version_type": "release", "natives_directory": str(natives),
            "launcher_name": "boot-smoke", "launcher_version": "1",
            "classpath": os.pathsep.join(cp), "classpath_separator": os.pathsep,
            "library_directory": str(libs_dir)}
    sub = lambda a: re.sub(r"\$\{(\w+)\}", lambda m: repl.get(m.group(1), m.group(0)), a)

    java, how = find_java(inst, merged)
    if not java:
        die("no Java found (set JAVA_HOME or install the launcher's JRE)")
    jvm = ["-Xmx4G"]
    if IS_MAC and not any("-XstartOnFirstThread" in a for a in jvm_a):
        jvm.append("-XstartOnFirstThread")           # macOS GLFW must own the first thread
    cmd = [java, *jvm, *(sub(a) for a in jvm_a), merged["mainClass"],
           *(sub(a) for a in game_a)]
    return cmd, merged, missing, how


OK = re.compile(r"Sound engine started|OpenAL initialized|Backend library: LWJGL")
BAD = re.compile(r"Exception in thread \"main\"|LoadingFailedException|ModLoadingException|"
                 r"Duplicate mod|Missing or unsupported mandatory dependencies|"
                 r"A potential solution has been determined|UnsatisfiedLinkError|"
                 r"Failed to find a primary monitor")


def game_already_running(inst):
    """PIDs of a Minecraft client already using this instance.

    TWO REASONS THIS IS A HARD STOP, and the second one is the expensive one.

    A second client on one gameDir shares its config, its logs and its worlds, so the run pollutes
    whatever the player is doing. Worse, the obvious cleanup afterwards is a `pkill` on something in
    the java command line -- and the player's OWN game is launched by the same bundled JRE, from the
    same path, so `pkill -f java-runtime-delta` kills it too. That happened: a child's session died
    mid-wish with no exception and no crash report, the log simply stopped, and it was diagnosed as
    a mod bug before the timestamps were compared. SIGKILL looks exactly like a mod crash and leaves
    strictly less evidence."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True,
                             text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    me, hits = str(os.getpid()), []
    for line in out.splitlines():
        pid, _, cmdline = line.strip().partition(" ")
        if pid == me or "boot-smoke" in cmdline:
            continue
        if "bootstraplauncher" in cmdline or "net.minecraft.client.main.Main" in cmdline:
            if str(inst) in cmdline or "--gameDir" not in cmdline:
                hits.append(pid)
    return hits


def boot(inst, version, log_path, timeout, expect, game_dir=None):
    running = game_already_running(game_dir or inst)
    if running and not os.environ.get("BOOT_SMOKE_ALLOW_CONCURRENT"):
        die(f"a Minecraft client is ALREADY running on this instance (pid {', '.join(running)}).\n"
            "Refusing to launch a second one on the same gameDir -- and do NOT pkill java to tidy\n"
            "up afterwards: the player's own game runs from the same bundled JRE and dies too.\n"
            "Close the game first, or set BOOT_SMOKE_ALLOW_CONCURRENT=1 if you are certain.", 2)
    cmd, manifest, missing, how = build_command(inst, version, game_dir)
    print(f"boot: {manifest['id']} via {how}; classpath {len(cmd)} args, missing libs {len(missing)}")
    for m in missing[:5]:
        print(f"  MISSING LIBRARY: {m}")
    if missing:
        return "FAIL", "libraries missing from the instance — open the launcher once to repair"

    env = dict(os.environ)
    if IS_MAC and shutil.which("caffeinate"):
        # WAKE the display; `caffeinate -d` only keeps an awake one awake, and a sleeping
        # display fails GLFW in a way that looks like a broken machine.
        subprocess.run(["caffeinate", "-u", "-t", "2"], check=False)
    elif IS_LINUX and not env.get("DISPLAY"):
        if not shutil.which("xvfb-run"):
            # Never silently skip. The same rule as tools/lib/gate-c-env.sh in battle-of-lord:
            # a gate that quietly did not run reports identically to one that passed.
            die("headless Linux needs Xvfb:\n"
                "  apt-get update && apt-get install -y xvfb mesa-utils libgl1-mesa-dri", 2)
        env["LIBGL_ALWAYS_SOFTWARE"] = "1"
        cmd = ["xvfb-run", "-a", "-s", "-screen 0 1280x720x24"] + cmd

    proc = subprocess.Popen(cmd, cwd=str(inst), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1,
                            errors="replace", env=env)
    verdict, detail, start, seen_mods = None, "", time.time(), set()
    with open(log_path, "w") as fh:
        for line in proc.stdout:
            fh.write(line)
            for mid in re.findall(r"\(([a-z0-9_\-]{2,})\)\s*$", line.strip()):
                seen_mods.add(mid)
            if verdict is None:
                if BAD.search(line):
                    verdict, detail = "FAIL", line.strip()[:300]
                    break
                if OK.search(line):
                    verdict = "PASS"
                    break
            if time.time() - start > timeout:
                verdict, detail = "TIMEOUT", f"no verdict in {timeout}s"
                break
        if verdict == "PASS":
            # Keep reading briefly so the mod list (printed during load) is in the log.
            #
            # This MUST be time-bounded from the outside. Checking a deadline inside
            # `for line in proc.stdout` only tests it once a line ARRIVES -- and a client that
            # has reached the title screen stops logging, so the iterator blocks on read()
            # forever and the deadline never fires. Measured: a client that had already printed
            # "Sound engine started" sat running for over ten minutes with no verdict written,
            # which reads exactly like a boot that never succeeded. Same shape as the
            # phase-machine wait recorded in battle-of-lord/CLAUDE.md: a timer that only ticks
            # on activity cannot time out an idle thing.
            import threading
            def drain():
                for line in proc.stdout:
                    fh.write(line)
                    for mid in re.findall(r"\(([a-z0-9_\-]{2,})\)\s*$", line.strip()):
                        seen_mods.add(mid)
            t = threading.Thread(target=drain, daemon=True)
            t.start()
            t.join(20)
    proc.terminate()
    try:
        proc.wait(15)
    except subprocess.TimeoutExpired:
        proc.kill()

    if verdict == "PASS":
        text = pathlib.Path(log_path).read_text(errors="replace")
        for e in expect:
            if e not in seen_mods and f"({e})" not in text:
                verdict, detail = "FAIL", f"booted, but '{e}' never appeared in the mod list"
            else:
                print(f"  loaded: {e}")
        lines = text.splitlines()
        errs = [l for l in lines if "/ERROR]" in l or "/FATAL]" in l]
        ours = [l for l in errs if any(e in l for e in expect)]
        # State the WINDOW, not just the count. Reading stops shortly after the sound engine, so
        # a bare "0 errors" would read as "the game is clean" when it means "nothing had gone
        # wrong yet by the time we stopped looking".
        print(f"  errors in the {len(lines)} lines captured (boot only, not a full session): "
              f"{len(errs)} total, {len(ours)} from the expected mods")
        for l in ours[:3]:
            print(f"    {l.strip()[:160]}")
    return verdict or "TIMEOUT", detail


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instance"), ap.add_argument("--version")
    ap.add_argument("--game-dir", dest="game_dir", default=None,
                    help="the profile's Game Directory (mods/, saves/, config/) when it is NOT the "
                         "same folder as the launcher installation -- i.e. the vanilla launcher "
                         "running a second Minecraft version. Defaults to --instance.")
    ap.add_argument("--expect", action="append", default=[],
                    help="mod id that MUST be present (repeatable)")
    ap.add_argument("--preflight", action="store_true", help="jar checks only; no launch")
    ap.add_argument("--timeout", type=int, default=int(os.environ.get("BOOT_TIMEOUT", "600")))
    ap.add_argument("--log", default=None)
    a = ap.parse_args()

    inst = find_instance(a.instance)
    if not inst:
        die(f"no Minecraft instance found (looked at {DEFAULT_INSTANCE}); pass --instance.\n"
            "On a VM with no instance there is nothing to boot — that is expected.", 2)
    game_dir = pathlib.Path(a.game_dir).expanduser() if a.game_dir else inst
    if a.game_dir and not game_dir.is_dir():
        die(f"no such game directory: {game_dir}", 2)
    mods_dir = game_dir / "mods"
    if not mods_dir.is_dir():
        die(f"no mods folder at {mods_dir}", 2)
    version = pick_version(inst, a.version)
    print(f"instance: {inst}\nversion:  {version}")
    if game_dir != inst:
        print(f"gameDir:  {game_dir}")

    problems = preflight(mods_dir, a.expect)
    if problems:
        print("\nVERDICT: FAIL (preflight)")
        return 1
    if a.preflight:
        print("\nVERDICT: PASS (preflight only — not launched)")
        return 0

    log = a.log or str(pathlib.Path(os.environ.get("TMPDIR", "/tmp")) / "boot-smoke.log")
    verdict, detail = boot(inst, version, log, a.timeout, a.expect, game_dir)
    if detail:
        print(f"  {detail}")
    print(f"\nVERDICT: {verdict}\nlog: {log}")
    return {"PASS": 0, "FAIL": 1}.get(verdict, 3)


if __name__ == "__main__":
    sys.exit(main())
