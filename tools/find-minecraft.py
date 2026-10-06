#!/usr/bin/env python3
"""Find the Minecraft installs on this machine and say what is in each one.

    python3 tools/find-minecraft.py            # human-readable report
    python3 tools/find-minecraft.py --json     # the same, for tools/setup.py

WHY THIS EXISTS
    The first question setup has to answer is "where does your game live?", and nobody should have
    to go looking for a hidden folder to answer it. So this PROBES, in order:

      1. $MINECRAFT_DIR, if set;
      2. MINECRAFT_DIR / MINECRAFT_MODS_DIR* already recorded in this checkout's .env.local;
      3. the official launcher's platform default -- and its `<dir>-<mc version>` siblings, which is
         the layout a second, separate instance per Minecraft version uses;
      4. the common third-party launchers' instance folders (Prism, MultiMC, CurseForge, Modrinth
         App, ATLauncher).

    For each candidate it reports what it can see WITHOUT launching anything: the Minecraft
    version(s) and loader(s), and how many mod jars are in its mods/ folder. A report that only
    printed a path would not tell you which of two instances is the one you play.

    Finding nothing is a normal answer, not an error. A cloud VM or a build-only machine has no
    game install, and a migration that only compiles does not need one. Exit status is 0 either way.

Standard library only.
"""
import argparse, json, os, pathlib, platform, re, sys

SYS = platform.system()
HOME = pathlib.Path.home()
ROOT = pathlib.Path(__file__).resolve().parent.parent


def _appdata():
    return pathlib.Path(os.environ.get("APPDATA", HOME / "AppData/Roaming"))


def official_default():
    if SYS == "Darwin":
        return HOME / "Library/Application Support/minecraft"
    if SYS == "Windows":
        return _appdata() / ".minecraft"
    return HOME / ".minecraft"


def launcher_instance_roots():
    """(launcher name, directory whose children are instances). Only existing ones are probed."""
    if SYS == "Darwin":
        app = HOME / "Library/Application Support"
        return [("Prism", app / "PrismLauncher/instances"),
                ("MultiMC", app / "MultiMC/instances"),
                ("Modrinth App", app / "com.modrinth.theseus/profiles"),
                ("CurseForge", HOME / "Documents/curseforge/minecraft/Instances"),
                ("ATLauncher", app / "ATLauncher/instances")]
    if SYS == "Windows":
        a = _appdata()
        return [("Prism", a / "PrismLauncher/instances"),
                ("Modrinth App", a / "com.modrinth.theseus/profiles"),
                ("CurseForge", HOME / "curseforge/minecraft/Instances"),
                ("ATLauncher", a / "ATLauncher/instances")]
    share = pathlib.Path(os.environ.get("XDG_DATA_HOME", HOME / ".local/share"))
    return [("Prism", share / "PrismLauncher/instances"),
            ("MultiMC", HOME / ".local/share/multimc/instances"),
            ("Modrinth App", share / "com.modrinth.theseus/profiles"),
            ("CurseForge", HOME / "curseforge/minecraft/Instances"),
            ("ATLauncher", share / "ATLauncher/instances")]


def read_env_local(path):
    out = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r'\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$', line)
            if m and not line.lstrip().startswith("#"):
                out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    except OSError:
        pass
    return out


LOADER_RE = re.compile(r'^(neoforge|forge|fabric-loader|quilt-loader)[-_]?(.*)$', re.I)


def describe_official(game_dir):
    """The official launcher: versions/ lists what is installed, launcher_profiles.json which one a
    profile points at. A second instance (`minecraft-26.2`) usually has only mods/ and the profile
    that points at it lives in the MAIN launcher dir -- that is expected, not broken."""
    info = {"kind": "official launcher", "path": str(game_dir), "versions": [], "loaders": []}
    vers = game_dir / "versions"
    if vers.is_dir():
        for v in sorted(p.name for p in vers.iterdir() if p.is_dir()):
            m = LOADER_RE.match(v)
            (info["loaders"] if m else info["versions"]).append(v)
    return info


def describe_prism(inst):
    info = {"kind": "Prism/MultiMC", "path": str(inst / ".minecraft"), "versions": [], "loaders": []}
    try:
        pack = json.loads((inst / "mmc-pack.json").read_text(encoding="utf-8"))
        for c in pack.get("components", []):
            uid, ver = c.get("uid", ""), c.get("version", "")
            if uid == "net.minecraft":
                info["versions"].append(ver)
            elif uid in ("net.neoforged", "net.minecraftforge", "net.fabricmc.fabric-loader"):
                info["loaders"].append(f"{uid.split('.')[-1]} {ver}")
    except (OSError, ValueError):
        pass
    if not (inst / ".minecraft").is_dir() and (inst / "minecraft").is_dir():
        info["path"] = str(inst / "minecraft")
    return info


def describe_modrinth(prof):
    info = {"kind": "Modrinth App", "path": str(prof), "versions": [], "loaders": []}
    try:
        d = json.loads((prof / "profile.json").read_text(encoding="utf-8"))
        meta = d.get("metadata", d)
        if meta.get("game_version"):
            info["versions"].append(meta["game_version"])
        if meta.get("loader"):
            info["loaders"].append(f"{meta['loader']} {meta.get('loader_version', '') or ''}".strip())
    except (OSError, ValueError):
        pass
    return info


def describe_generic(kind, inst):
    info = {"kind": kind, "path": str(inst), "versions": [], "loaders": []}
    try:
        d = json.loads((inst / "minecraftinstance.json").read_text(encoding="utf-8"))   # CurseForge
        if d.get("gameVersion"):
            info["versions"].append(d["gameVersion"])
        bml = d.get("baseModLoader") or {}
        if bml.get("name"):
            info["loaders"].append(bml["name"])
    except (OSError, ValueError):
        pass
    return info


def mods_count(game_dir):
    mods = pathlib.Path(game_dir) / "mods"
    if not mods.is_dir():
        return None
    return sum(1 for p in mods.iterdir() if p.suffix == ".jar")


def discover(env_path):
    found, seen = [], set()

    def add(info, source):
        key = os.path.realpath(info["path"])
        if key in seen:
            return
        seen.add(key)
        info["source"] = source
        info["mods"] = mods_count(info["path"])
        found.append(info)

    env = read_env_local(env_path)
    explicit = []
    if os.environ.get("MINECRAFT_DIR"):
        explicit.append(("$MINECRAFT_DIR", os.environ["MINECRAFT_DIR"]))
    if env.get("MINECRAFT_DIR"):
        explicit.append((".env.local MINECRAFT_DIR", env["MINECRAFT_DIR"]))
    for k, v in sorted(env.items()):
        if k.startswith("MINECRAFT_MODS_DIR") and v:
            explicit.append((f".env.local {k}", str(pathlib.Path(v).expanduser().parent)))
    for source, p in explicit:
        p = pathlib.Path(p).expanduser()
        if p.is_dir():
            add(describe_official(p), source)

    base = official_default()
    if base.is_dir():
        add(describe_official(base), "official launcher default")
    for sib in sorted(base.parent.glob(base.name + "-*")):     # e.g. minecraft-26.2
        if sib.is_dir():
            info = describe_official(sib)
            m = re.search(r'-(\d+(?:\.\d+)+)$', sib.name)
            if m and m.group(1) not in info["versions"]:
                info["versions"].append(m.group(1) + " (from the folder name)")
            add(info, "separate per-version game directory")

    for launcher, root in launcher_instance_roots():
        if not root.is_dir():
            continue
        for inst in sorted(p for p in root.iterdir() if p.is_dir()):
            if launcher in ("Prism", "MultiMC"):
                add(describe_prism(inst), f"{launcher} instance")
            elif launcher == "Modrinth App":
                add(describe_modrinth(inst), "Modrinth App profile")
            else:
                add(describe_generic(launcher, inst), f"{launcher} instance")
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--env", default=str(ROOT / ".env.local"))
    a = ap.parse_args()
    found = discover(pathlib.Path(a.env))
    if a.json:
        print(json.dumps({"platform": SYS, "installs": found}, indent=2))
        return 0
    if not found:
        print("find-minecraft: no Minecraft install found on this machine.")
        print("  That is fine for building and testing a migration -- only deploying needs one.")
        print(f"  (looked at {official_default()} and the usual launcher instance folders)")
        return 0
    print(f"find-minecraft: {len(found)} install(s) found")
    for i, f in enumerate(found, 1):
        mods = "no mods/ folder" if f["mods"] is None else f"{f['mods']} mod jar(s)"
        print(f"  {i}. {f['path']}")
        print(f"     {f['source']}; {mods}")
        if f["versions"]:
            print(f"     Minecraft: {', '.join(f['versions'])}")
        if f["loaders"]:
            print(f"     loaders:   {', '.join(f['loaders'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
