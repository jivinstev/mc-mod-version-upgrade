#!/usr/bin/env python3
"""First-run setup, and every run after it.  Usually invoked as `./setup`.

    ./setup                 interactive: every question shows a recommendation; Enter accepts it
    ./setup --yes           first run: take every recommendation.  RE-RUN: keep every decision.
    ./setup --check         change nothing; print what setup WOULD change, and verify prerequisites
    ./setup --migrate       add the migration add-on (same as --path migrate; adds keys, never removes any)
    ./setup --mods-dir 26.2=/path/to/minecraft-26.2   add one more deploy target (a set, not a value)
    ./setup --remove KEY    the ONLY way setup ever deletes a setting

INSTALLING IS ALWAYS SET UP; MIGRATION IS AN ADD-ON (stored as SETUP_PATH=install|migrate)
    installing  find, resolve, verify and deploy mods.  Needs Python 3 and a network.  NO Java.
    migration   port a mod that has no build for your version.  Adds a JDK, a workspace, several GB.
    Without the add-on setup must complete on a machine with no Java at all, and never warn about one.

WHAT IT WRITES
    .env.local          the VALUES, flat KEY=value.  Every tool reads this; edit it by hand freely.
    .setup-state.json   PROVENANCE for each key: `user` (you chose or typed it), `detected`, or
                        `default` -- so a re-run can tell your decisions from its own guesses.

RE-RUN RULES (each one a way a re-run could otherwise quietly break something)
    1. A hand-edited .env.local always wins; the key is promoted to `user`.
    2. `user` values are offered back as the default and never changed without a keystroke.
    3. Mods directories are a SET (MINECRAFT_MODS_DIR_<version>); adding one never touches another.
    4. Path changes are additive; nothing is deleted when you switch back.
    5. Nothing is ever destroyed by a re-run.  Removal needs --remove KEY.
    6. Every change is shown as `KEY: old -> new` and nothing is written until you confirm.
    7. .env.local is backed up to .env.local.bak before every write.
    8. A newer setup adds new keys with defaults and leaves everything else alone.
    ⚠ `--yes` means "accept recommendations" on a FIRST run and "keep what is there" on a RE-RUN.
      Applying recommendations on a re-run would silently discard your overrides -- the classic
      failure that looks exactly like the tool working.  tools/test-setup.sh asserts a second
      `--yes` run reports zero changes.

Standard library only.
"""
import argparse, datetime, json, os, pathlib, platform, re, shutil, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV = ROOT / ".env.local"
STATE = ROOT / ".setup-state.json"
SCHEMA = 1
SYS = platform.system()
DEFAULT_WORKSPACE = "~/.mc-mod-upgrade/work"
DEFAULT_TARGET = "1.21.1"
SECRET_KEYS = {"CURSEFORGE_API_KEY"}

INSTALL_HINTS = {  # tool -> {platform: command}
    "git": {"Darwin": "xcode-select --install   (or: brew install git)",
            "Windows": "winget install --id Git.Git",
            "Linux": "sudo apt install git   (Fedora: sudo dnf install git)"},
    "python3": {"Darwin": "brew install python",
                "Windows": "winget install --id Python.Python.3.12",
                "Linux": "sudo apt install python3   (Fedora: sudo dnf install python3)"},
    "java": {"Darwin": "brew install --cask temurin@21   (MC 26.x also needs 25: temurin@25)",
             "Windows": "winget install --id EclipseAdoptium.Temurin.21.JDK",
             "Linux": "sudo apt install openjdk-21-jdk   (Fedora: sudo dnf install java-21-openjdk-devel)"},
    "claude": {"Darwin": "brew install --cask claude-code   (or, with Node 18+: npm install -g @anthropic-ai/claude-code)",
               "Windows": "npm install -g @anthropic-ai/claude-code   (needs Node 18+)",
               "Linux": "npm install -g @anthropic-ai/claude-code   (needs Node 18+)"},
}
CLAUDE_DOCS = "https://docs.claude.com/en/docs/claude-code/setup"


# ── .env.local + state ──────────────────────────────────────────────────────────────────────
def read_env():
    vals = {}
    if ENV.is_file():
        for line in ENV.read_text().splitlines():
            m = re.match(r'\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$', line)
            if m and not line.lstrip().startswith("#"):
                vals[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return vals


def read_state():
    try:
        s = json.loads(STATE.read_text())
        if isinstance(s, dict) and isinstance(s.get("keys"), dict):
            return s
    except (OSError, ValueError):
        pass
    return {"schema": SCHEMA, "keys": {}}


def reconcile(env, state):
    """Rule 1: anything in .env.local that setup did not write, or that differs from what it
    wrote, is the user's own statement of intent."""
    promoted = []
    for k, v in env.items():
        rec = state["keys"].get(k)
        if rec is None or rec.get("value") != v:
            if rec is None or rec.get("origin") != "user":
                promoted.append(k)
            state["keys"][k] = {"value": v, "origin": "user",
                                "set_at": (rec or {}).get("set_at") or now()}
    # A key in the state but gone from .env.local was removed by hand: forget it (rule 1 again).
    for k in [k for k in state["keys"] if k not in env]:
        del state["keys"][k]
    return promoted


def now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def write_env(changes, removals):
    """Update lines in place so comments and ordering the user wrote survive; append new keys."""
    lines = ENV.read_text().splitlines() if ENV.is_file() else [
        "# Written by ./setup. Edit freely -- a hand edit always wins over setup's own guesses.",
        "# Provenance for each key lives in .setup-state.json."]
    seen = set()
    out = []
    for line in lines:
        m = re.match(r'\s*([A-Za-z_][A-Za-z0-9_]*)\s*=', line)
        if m and not line.lstrip().startswith("#"):
            k = m.group(1)
            if k in removals:
                continue
            if k in changes:
                out.append(f"{k}={changes[k]}")
                seen.add(k)
                continue
        out.append(line)
    for k, v in changes.items():
        if k not in seen:
            out.append(f"{k}={v}")
    if ENV.is_file():
        shutil.copy2(ENV, ENV.with_name(".env.local.bak"))           # rule 7
    ENV.write_text("\n".join(out) + "\n")


# ── probes ──────────────────────────────────────────────────────────────────────────────────
def run(cmd, timeout=15):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return None, str(e)


def java_major():
    if not shutil.which("java"):
        return None
    code, out = run(["java", "-version"])
    m = re.search(r'version "(\d+)(?:\.(\d+))?', out or "")
    if not m:
        return None
    major = int(m.group(1))
    return int(m.group(2)) if major == 1 and m.group(2) else major


def hint(tool):
    return INSTALL_HINTS[tool].get(SYS, INSTALL_HINTS[tool]["Linux"])


def discover_installs():
    code, out = run([sys.executable, str(ROOT / "tools/find-minecraft.py"), "--json",
                     "--env", str(ENV)], timeout=60)
    try:
        return json.loads(out).get("installs", []) if code == 0 else []
    except ValueError:
        return []


def inside_git_repo(path):
    p = pathlib.Path(path).expanduser()
    while not p.exists() and p != p.parent:
        p = p.parent
    code, _ = run(["git", "-C", str(p), "rev-parse", "--is-inside-work-tree"])
    return code == 0


def likely_output_repos():
    """Sibling checkouts that look like a place finished ports live. No name is assumed: a folder next
    to this one qualifies when it is a git repo whose mods/ holds at least one port, i.e. a
    mods/<modid>/build.gradle -- the layout tools/finish-port.py writes. A bare `mods/` is not enough
    (plenty of unrelated projects have one), so a brand-new empty destination is typed in, not guessed."""
    out = []
    for d in sorted(ROOT.parent.iterdir()):
        if d == ROOT or not d.is_dir() or not (d / ".git").exists() or not (d / "mods").is_dir():
            continue
        try:
            if any((m / "build.gradle").is_file() for m in (d / "mods").iterdir() if m.is_dir()):
                out.append(os.path.relpath(d, ROOT))
        except OSError:
            continue
    return out


WORKSPACE_LINKS = ("tools", "templates", ".env.local")


def ensure_workspace(ws, notes, problems, dry):
    """Make $MIGRATE_WORKSPACE look like the root every tool expects: `mods/<modid>/` for the
    ports, with `tools/`, `templates/` and `.env.local` beside it. The last three are SYMLINKS
    back into this checkout, so there is one copy of the tooling and an update reaches every
    workspace. Idempotent; never replaces anything that is not already our own link."""
    root = pathlib.Path(ws).expanduser()
    if dry:
        return
    (root / "mods").mkdir(parents=True, exist_ok=True)
    for name in WORKSPACE_LINKS:
        link, target = root / name, ROOT / name
        if link.is_symlink():
            if os.path.realpath(link) != str(target.resolve()):
                problems.append(f"{link} links somewhere else ({os.readlink(link)}) -- left alone")
            continue
        if link.exists():
            problems.append(f"{link} exists and is not a link to this checkout -- left alone")
            continue
        try:
            link.symlink_to(target)
            notes.append(f"workspace: linked {link} -> {target}")
        except OSError as e:          # Windows without developer mode, for one
            problems.append(f"could not link {link}: {e} (run from WSL, or copy it by hand)")


def supported_versions():
    """minecraft -> 'tested' / 'reported (3 of 10 mods with Gate C)'; absent = untested.
    Computed by tools/supported-versions.py, the only place the rule lives."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("sv", ROOT / "tools/supported-versions.py")
    sv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sv)
    return {r["minecraft"]: sv.describe(r) for r in sv.load()[1]}


def neoforge_minecraft(loader):
    """The Minecraft version a NeoForge build is for: 21.1.228 -> 1.21.1, 21.0.x -> 1.21,
    26.2.0.75 -> 26.2 (NeoForge's own numbering follows Minecraft's). None if not NeoForge."""
    m = re.match(r'neoforged?[-_ ]?(\d+)\.(\d+)\.', loader.strip(), re.I)
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    return f"{a}.{b}" if a >= 26 else (f"1.{a}" + (f".{b}" if b else ""))


def key_for_version(v):
    return "MINECRAFT_MODS_DIR_" + v.replace(".", "_")


# ── asking ──────────────────────────────────────────────────────────────────────────────────
class Asker:
    def __init__(self, args, state, first_run):
        self.a, self.state, self.first = args, state, first_run
        self.proposed = {}          # key -> (value, origin)
        self.interactive = not (args.yes or args.check)

    def existing(self, key):
        return self.state["keys"].get(key)

    def decide(self, key, recommended, question, origin="default", secret=False, choices=None):
        """Return the value to use, honouring the re-run rules. Records a proposal if it changes.
        `choices` maps what the user may TYPE to the value stored, e.g. yes/no -> migrate/install;
        the default is then shown as the typed form, and anything else is asked again."""
        rec = self.existing(key)
        cur = rec["value"] if rec else None
        if rec and (not self.interactive):
            return cur                            # re-run + --yes/--check: keep every decision
        default = cur if rec else recommended
        if self.interactive and choices:
            shown = next((k for k, v in choices.items() if v == default), default)
            while True:
                ans = input(f"  {question} [{shown}]: ").strip().lower()
                if not ans or ans in choices:
                    break
                print(f"    please answer {' or '.join(dict.fromkeys(choices))}")
            if ans:
                value, origin = choices[ans], "user"
            else:
                value = default
                origin = rec["origin"] if rec else origin
        elif self.interactive:
            shown = "(set)" if secret and default else (default if default != "" else "none")
            ans = input(f"  {question} [{shown}]: ").strip()
            if ans.lower() == "none":
                value, origin = "", "user"
            elif ans:
                value, origin = ans, "user"
            else:
                value = default
                origin = rec["origin"] if rec else origin
        else:
            value = recommended                   # first run + --yes: take the recommendation
        if value != cur or rec is None:
            self.proposed[key] = (value, origin)
        return value


# ── the steps ───────────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--path", choices=["install", "migrate"],
                    help="install = installing only; migrate = installing plus the migration add-on")
    ap.add_argument("--migrate", dest="path", action="store_const", const="migrate",
                    help="shorthand for --path migrate: also set up migration")
    ap.add_argument("--mods-dir", action="append", default=[], metavar="VERSION=PATH")
    ap.add_argument("--create-dirs", action="store_true",
                    help="create a missing mods folder named by --mods-dir (tier 1: folder only)")
    ap.add_argument("--remove", action="append", default=[], metavar="KEY")
    ap.add_argument("--output-repo", metavar="PATH",
                    help="where finished ports go (a git repo, any folder, or 'none'); sets MOD_OUTPUT_REPO")
    ap.add_argument("--no-network", action="store_true", help="skip the git reachability probe")
    a = ap.parse_args()

    if not (a.yes or a.check) and not sys.stdin.isatty():
        print("setup: not a terminal -- pass --yes (take recommendations / keep decisions) or --check.",
              file=sys.stderr)
        return 2

    env, state = read_env(), read_state()
    first_run = not ENV.exists() and not STATE.exists()
    promoted = reconcile(env, state)
    ask = Asker(a, state, first_run)
    problems, notes = [], []

    print(f"setup: {'first run' if first_run else 're-run'} on {SYS}  ({ROOT})")
    if promoted:
        print(f"  kept your hand edits to .env.local: {', '.join(sorted(promoted))}")

    # 0. installing is always set up; migration is an ADD-ON ----------------------------------
    print("\n0. Setup always prepares INSTALLING mods: find them on Modrinth/CurseForge, resolve\n"
          "   dependencies, deploy. That needs only Python.\n"
          "   MIGRATION is an add-on: when a mod has NO build for your Minecraft version, port it\n"
          "   yourself. Adds a JDK, a workspace and several GB of disk. You can add it later with\n"
          "   `./setup --migrate`; installing always uses an existing build first either way.")
    if a.path:
        ask.proposed["SETUP_PATH"] = (a.path, "user")
        path = a.path
    else:
        path = ask.decide("SETUP_PATH", "install", "Also set up migration? (yes/no)",
                          choices={"no": "install", "n": "install", "yes": "migrate", "y": "migrate"})
    if path not in ("install", "migrate"):
        problems.append(f"SETUP_PATH must be install or migrate, not {path!r}")
        path = "install"

    # 1. prerequisites ------------------------------------------------------------------------
    print("\n1. Prerequisites")
    for tool in ("git",):
        if shutil.which(tool):
            print(f"   {tool}: ok")
        else:
            problems.append(f"{tool} is missing -- install it: {hint(tool)}")
    print(f"   python3: ok ({platform.python_version()})")
    # Claude Code drives the skills (installing, migrating). Detected, never installed: a global
    # install is the user's call, like git/python/JDK above. Not a PROBLEM -- the tools run without it.
    has_claude = shutil.which("claude") is not None
    if has_claude:
        print("   claude: ok (Claude Code)")
    else:
        notes.append(f"Claude Code is not installed -- it is what you talk to. Install: {hint('claude')}  "
                     f"(details: {CLAUDE_DOCS})")
    if sys.version_info < (3, 8):
        problems.append(f"python 3.8+ needed -- {hint('python3')}")
    if path == "migrate":
        jm = java_major()
        if jm is None:
            problems.append(f"no JDK found (migration needs Java 21; MC 26.x needs 25) -- {hint('java')}")
        elif jm < 21:
            problems.append(f"Java {jm} found; migration needs 21+ -- {hint('java')}")
        else:
            print(f"   java: ok (Java {jm}{'' if jm >= 25 else '; MC 26.x targets also need Java 25'})")
    if SYS == "Windows":
        notes.append("the tooling is bash + Python: on Windows run it from WSL or Git Bash")

    # 2. git access ---------------------------------------------------------------------------
    print("\n2. Git access")
    print("   clone to USE it; FORK to contribute (main takes pull requests only):\n"
          "     gh repo fork jivinstev/mc-mod-version-upgrade --clone\n"
          "   A mirror is not needed.")
    if not a.no_network:
        code, _ = run(["git", "-C", str(ROOT), "ls-remote", "--exit-code", "origin", "HEAD"], timeout=20)
        print("   origin: " + ("reachable" if code == 0 else "NOT reachable (offline, or no remote) -- "
                                 "setup continues; updates and contributions need it"))

    # 3. Minecraft installs + per-version deploy targets ---------------------------------------
    print("\n3. Minecraft installs")
    installs = discover_installs()
    if not installs:
        print("   none found -- fine for building; deploying needs one (add later with --mods-dir)")
    for i, f in enumerate(installs, 1):
        mods = "no mods/" if f.get("mods") is None else f"{f['mods']} mods"
        vers = ", ".join(f.get("versions") or []) or "?"
        print(f"   {i}. {f['path']}  [{f['source']}; Minecraft {vers}; {mods}]")
    main_dir = next((f["path"] for f in installs if f.get("source") == "official launcher default"),
                    installs[0]["path"] if installs else "")
    ask.decide("MINECRAFT_DIR", main_dir, "your main Minecraft folder (or 'none')", origin="detected")

    # Which versions to ask about: the TESTED ones, plus any version you have NeoForge installed for.
    # A vanilla version with no NeoForge (the launcher downloads each new release by itself) is only
    # listed. The recommended folder is an install that has NeoForge FOR THAT VERSION.
    support = supported_versions()
    neo = {}                                        # minecraft version -> [install, ...] with NeoForge for it
    dedicated = {}                                  # install path -> the ONE version its folder is named for
    for f in installs:
        for l in f.get("loaders") or []:
            v = neoforge_minecraft(l)
            if v and f not in neo.setdefault(v, []):
                neo[v].append(f)
        for v in f.get("versions") or []:           # minecraft-26.2: its profile lives in the main folder
            if v.endswith("(from the folder name)"):
                v = v.split(" ")[0]
                dedicated[f["path"]] = v
                if f not in neo.setdefault(v, []):
                    neo[v].append(f)
    vanilla = sorted({v.split(" ")[0] for f in installs for v in (f.get("versions") or [])
                      if re.fullmatch(r'\d+(\.\d+)+', v.split(" ")[0])})
    targets = sorted(set(v for v, s in support.items() if s == "tested") | set(neo),
                     key=lambda v: [int(x) for x in v.split(".")])
    print("\n   Mod versions (tested = our migrations are verified there; see SUPPORTED_VERSIONS.md):")
    for v in targets:
        where = ", ".join(f["path"] for f in neo.get(v, [])) or "no NeoForge install found"
        print(f"     {v:<8} {support.get(v, 'untested'):<9} {where}")
    if any(not support.get(v, "").startswith("tested") for v in targets):
        print("     (reported/untested: it may work, but expect gaps; you can still choose it)")
    skipped = [v for v in vanilla if v not in targets]
    if skipped:
        print(f"     skipped (vanilla only, no NeoForge installed): {', '.join(skipped)}"
              "  -- add one later with --mods-dir VERSION=PATH")
    print("   For each version: the mods folder to deploy into. 'new' makes a separate folder for that\n"
          "   version (point a launcher profile's Game Directory at it); 'none' skips it.")
    base = pathlib.Path(main_dir or "~/minecraft").expanduser()
    for v in targets:
        cands = neo.get(v, [])
        # prefer a folder that serves ONLY this version: jars for one version break another's game
        cands = sorted(cands, key=lambda f: 0 if dedicated.get(f["path"]) == v else
                       1 + len({neoforge_minecraft(l) for l in f.get("loaders") or []} - {None}))
        rec = os.path.join(cands[0]["path"], "mods") if cands else ""
        label = "" if support.get(v) == "tested" else f" [{support.get(v, 'UNTESTED')}]"
        val = ask.decide(key_for_version(v), rec, f"deploy target for Minecraft {v}{label} (path, 'new' or 'none')",
                         origin="detected" if rec else "default")
        if val.strip().lower() == "new":
            d = str(base.parent / f"{base.name}-{v}" / "mods")
            if not a.check:
                pathlib.Path(d).mkdir(parents=True, exist_ok=True)
                notes.append(f"created {d} (folder only: point a launcher profile's Game Directory at its parent)")
            ask.proposed[key_for_version(v)] = (d, "user")
            val = d
        if val:
            sharers = [w for w in targets if w != v and
                       any(os.path.join(f["path"], "mods") == val for f in neo.get(w, []))]
            if sharers:
                notes.append(f"{val} is also the mods folder of your Minecraft {', '.join(sharers)} install: "
                             f"a {v} jar there breaks that game. Prefer 'new' (a separate folder per version).")
    for spec in a.mods_dir:                       # rule 3: add to the set, never touch the others
        if "=" not in spec:
            problems.append(f"--mods-dir wants VERSION=PATH, got {spec!r}")
            continue
        v, d = spec.split("=", 1)
        d = str(pathlib.Path(d).expanduser())
        if not pathlib.Path(d).is_dir():
            if a.create_dirs and not a.check:
                pathlib.Path(d).mkdir(parents=True, exist_ok=True)
                notes.append(f"created {d} (folder only: point a launcher profile's Game Directory at "
                             f"its parent to play it)")
            elif not a.check:
                problems.append(f"{d} does not exist -- pass --create-dirs to make it (folder only)")
                continue
        ask.proposed[key_for_version(v)] = (d, "user")

    # 4-5. migration-only settings -------------------------------------------------------------
    if path == "migrate":
        print("\n4. Migration workspace (decompiled mods live here -- keep it OUTSIDE any git repo)")
        ws = ask.decide("MIGRATE_WORKSPACE", DEFAULT_WORKSPACE, "workspace directory")
        if ws and inside_git_repo(ws):
            problems.append(f"MIGRATE_WORKSPACE {ws} is inside a git repository: one `git add -A` would "
                            "publish somebody's decompiled mod. Choose a path outside any checkout.")
        if ws and not inside_git_repo(ws):
            ensure_workspace(ws, notes, problems, dry=a.check)
        if ws:
            try:
                free = shutil.disk_usage(pathlib.Path(ws).expanduser().anchor or "/").free / 2**30
                if free < 10:
                    notes.append(f"only {free:.0f} GB free; a migration wants several GB (10+ is comfortable)")
            except OSError:
                pass
        print("\n5. Where finished ports go (a git repo you choose, or none)")
        cands = likely_output_repos()
        if cands:
            print("   looks like a ports repo (a git repo next to this one with mods/<modid>/build.gradle): "
                  + ", ".join(cands))
        if a.output_repo is not None:                 # an explicit flag is the user's decision
            v = "" if a.output_repo.strip().lower() == "none" else str(pathlib.Path(a.output_repo).expanduser())
            if v and not pathlib.Path(v).is_dir():
                problems.append(f"--output-repo {v} does not exist (create it, or `git init` it, first)")
            else:
                ask.proposed["MOD_OUTPUT_REPO"] = (v, "user")
        else:
            ask.decide("MOD_OUTPUT_REPO", cands[0] if cands else "", "output repo path (or 'none')",
                       origin="detected" if cands else "default")
        # always SAY where finished ports will go: a hand edit, a flag and a detection all end up here
        prop = ask.proposed.get("MOD_OUTPUT_REPO")
        out = prop[0] if prop else ((ask.existing("MOD_OUTPUT_REPO") or {}).get("value") or "")
        if not out:
            print("   destination: none -- finished ports stay in the workspace")
        elif pathlib.Path(out).expanduser().joinpath(".git").exists():
            print(f"   destination: {out} (a git repo: tools/finish-port.py commits on a port/<modid> branch)")
        else:
            print(f"   destination: {out} (not a git repo: tools/finish-port.py copies only)")

    # 6. registries ---------------------------------------------------------------------------
    print("\n6. Mod registries\n"
          "   Modrinth works with no account and is searched first.\n"
          "   CurseForge is OPTIONAL. Without a key, a mod published ONLY on CurseForge cannot be found\n"
          "   or downloaded (you will be told when that happens); everything on Modrinth still works.\n"
          "   To get a free key: sign in at https://console.curseforge.com/ and open \"API keys\".\n"
          "   You can add it later by re-running ./setup, or by editing CURSEFORGE_API_KEY in .env.local.")
    ask.decide("CURSEFORGE_API_KEY", "", "CurseForge API key (or Enter to skip)", secret=True)

    # 7. Claude hookup ------------------------------------------------------------------------
    print("\n7. Claude Code")
    print("   claude: " + ("found" if shutil.which("claude") else
                           "not found -- install from https://claude.com/claude-code, then re-run"))
    skills = sorted(p.name for p in (ROOT / ".claude/skills").iterdir() if (p / "SKILL.md").is_file())
    print(f"   skills in this checkout: {', '.join(skills)} (they load when Claude runs here)")
    mode = ask.decide("SKILLS_INSTALL", "project", "project (only in this checkout) or user (everywhere)?")
    if mode == "user" and not a.check:
        dest = pathlib.Path.home() / ".claude/skills"
        dest.mkdir(parents=True, exist_ok=True)
        for s in skills:
            link = dest / s
            if link.exists() or link.is_symlink():
                if link.is_symlink() and os.path.realpath(link) == str((ROOT / ".claude/skills" / s).resolve()):
                    continue
                notes.append(f"~/.claude/skills/{s} already exists and is not ours -- left alone")
                continue
            link.symlink_to(ROOT / ".claude/skills" / s)
            notes.append(f"linked ~/.claude/skills/{s}")

    # removals ---------------------------------------------------------------------------------
    removals = set()
    for k in a.remove:
        if k in state["keys"]:
            removals.add(k)
        else:
            notes.append(f"--remove {k}: not set, nothing to do")

    # 8. diff, confirm, write ------------------------------------------------------------------
    changes = {k: v for k, (v, _) in ask.proposed.items()
               if state["keys"].get(k, {}).get("value") != v or k not in state["keys"]}
    print("\n8. Summary")
    if not changes and not removals:
        print("   no changes")
    for k in sorted(changes):
        old = state["keys"].get(k, {}).get("value")
        show = (lambda s: "(set)" if s else "(empty)") if k in SECRET_KEYS else (lambda s: s if s else "(empty)")
        print(f"   {k}: {show(old) if old is not None else '(unset)'} -> {show(changes[k])}")
    for k in sorted(removals):
        print(f"   {k}: REMOVE")
    for n in notes:
        print(f"   note: {n}")
    for p in problems:
        print(f"   PROBLEM: {p}")

    if a.check:
        print("\nsetup --check: nothing written.")
        return 1 if problems else 0
    if changes or removals:
        if ask.interactive and input("\n   write these changes? [Y/n]: ").strip().lower() not in ("", "y", "yes"):
            print("   nothing written.")
            return 0
        write_env(changes, removals)
        for k, v in changes.items():
            state["keys"][k] = {"value": v, "origin": ask.proposed[k][1], "set_at": now()}
        for k in removals:
            state["keys"].pop(k, None)
    state["schema"] = SCHEMA
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")

    print("\nReady." if not problems else "\nWritten, but fix the PROBLEM lines above before relying on it.")
    print("\nNext:" + ("" if has_claude else "  (install Claude Code first -- see the note above)"))
    if path == "migrate":
        print("  bash tools/download-tools.sh     # once: fetches + verifies the two decompilers")
    print(f"  cd {ROOT} && claude")
    print("  then say what you want, in plain words, e.g.")
    print('    "Install Sodium"                                   (finds the right build, deploys it)')
    if path == "migrate":
        print('    "Migrate <mod name> to Minecraft 1.21.1"           (ports it when no build exists;')
        print("                                                        hours, not minutes)")
    else:
        print("  Porting a mod that has no build for your version needs the add-on: ./setup --migrate")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
