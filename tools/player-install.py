#!/usr/bin/env python3
"""Install a mod from its GitHub releases into a player's own Minecraft: NeoForge, the mod, and the mods it depends on.

    python3 tools/player-install.py OWNER/REPO[:MOD[,MOD...]] [--mc VERSION] [--release TAG] [--pre]
                                    [--instance new|PROFILE|FOLDER] [--minecraft-dir DIR] [--yes] [--dry-run]
                                    [--with python] [--list] [--boot-test]

A player normally runs it through the one-line bootstrap a repository's README shows (player-install/install.sh,
install.ps1; published with each player-install-vN release of this repository), which needs no checkout:

    curl -fsSL https://github.com/jivinstev/mc-mod-version-upgrade/releases/download/player-install-vN/install.sh | bash -s -- OWNER/REPO
    & ([scriptblock]::Create((irm https://github.com/jivinstev/mc-mod-version-upgrade/releases/download/player-install-vN/install.ps1))) OWNER/REPO

What it reads (no extra file needed in the mod's repository):
  * the repository's releases: tags `<series>-<version>-mc<minecraft>` (a re-release adds `-r<N>`); a repository
    that ships several mods has one series per mod, chosen with `:MOD` (OWNER/REPO:isle,arena). The release's .jar
    asset, verified by sha256: GitHub's asset digest, else the release's SHA256SUMS. Nothing unverifiable is installed.
  * full releases by default; a version with only pre-releases is shown as such and needs --pre.
  * dependencies pinned at the release's tag: a `*.lock` file in the mod's folder (or the repository root) holding
    `<name>_repo`, `<name>_tag`, `<name>_asset`, `<name>_sha256` (exact repository, release, jar and sha256 -- the
    dependency is never chosen separately), and a fork port's `.github/port-install.json`. Followed recursively.
  * NeoForge: the version the release was built on (`neo_version` in its gradle.properties at the tag), else the
    newest NeoForge of that Minecraft line at or above every jar's own NeoForge floor.
  * optionally, `player-install.txt` in the mod's folder or `.github/player-install.txt` at the tag: up to 12 lines
    printed after the install (how to turn on an optional feature, say).

What it writes, only after showing the plan and (unless --yes) asking:
  * NeoForge with its own installer, headless, with the Minecraft Launcher's Java (fetched from Mojang when absent),
    plus the vanilla files it needs -- the same path tools/real-install.py uses;
  * the jars into the instance's mods/ folder. A jar of the same mod id is replaced (the old one moved to
    mods-replaced/); nothing else in the folder is touched, so a re-run updates in place;
  * --instance new (the default): a NEW launcher profile with its own game folder (<minecraft>/instances/<name>),
    so the player's existing profiles and mods are untouched. --instance "Profile name" uses an existing NeoForge
    profile of the same Minecraft version; --instance FOLDER uses a game folder (its mods/ subfolder).
A download whose sha256 does not match stops the run before anything is written.

  --yes          take every default, ask nothing (fails, saying why, where a default does not exist)
  --dry-run      show the plan, write nothing
  --list         list the mods and Minecraft versions the repository has released, then stop
  --pre          allow pre-releases (the newest release, full or not)
  --with python  also make sure Python 3 is on the PATH for the game (some mods' optional features run it)
  --boot-test    after installing, start the installed profile headless and check the mods load (CI, acceptance)

Exit 0 = installed (or, with --dry-run / --list, planned / listed); 1 = refused or failed, with the reason; 2 = usage.
Standard library only; Python 3.9+. GITHUB_TOKEN / GH_TOKEN, when set, is sent to api.github.com only.
"""
import argparse, hashlib, importlib.util, json, os, pathlib, re, shutil, subprocess, sys, tempfile, urllib.error, \
    urllib.request

HERE = pathlib.Path(__file__).resolve().parent


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


RI = _load("real_install", "real-install.py")     # releases, Mojang's files, Java, launcher profiles, the boot test
IP = RI.IP                                         # jar readers and NeoForge's headless installer (install-port.py)

TAG = re.compile(r"^(?P<series>.+?)-(?P<ver>\d[\w.+]*)-mc(?P<mc>\d+(?:\.\d+)+)(?:-r(?P<r>\d+))?$")
RELEASE_URL = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/releases/download/([^/]+)/([^/]+)")
NEO_META = "https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml"


class Refuse(Exception):
    """A reason to stop before writing anything, said to the player as it is."""


# ── asking ────────────────────────────────────────────────────────────────────────────────────────────────────────
class Ask:
    """Questions on the terminal -- /dev/tty when stdin is the bootstrap's pipe (the one-line install). With --yes,
    every question takes its default and none is shown."""
    def __init__(self, yes, inp=None, out=print):
        self.yes, self.out, self._inp = yes, out, inp

    def _read(self, prompt):
        if self._inp is not None:
            return self._inp(prompt)
        if sys.stdin.isatty() or os.name == "nt":
            return input(prompt)
        try:
            with open("/dev/tty", encoding="utf-8") as tty:
                sys.stdout.write(prompt); sys.stdout.flush()
                return tty.readline().rstrip("\n")
        except OSError:
            raise Refuse("no terminal to ask on: run it in a terminal, or add --yes to take every default")

    def choose(self, question, options, default=0):
        """options: [(value, label)]. -> the chosen value."""
        if self.yes or len(options) == 1:
            return options[default][0]
        self.out(question)
        for i, (_v, label) in enumerate(options, 1):
            self.out(f"  {i}. {label}{'   (default)' if i - 1 == default else ''}")
        while True:
            a = self._read(f"Choose 1-{len(options)} [Enter = {default + 1}, q = quit]: ").strip().lower()
            if a in ("q", "quit"):
                raise Refuse("stopped: nothing was written")
            if not a:
                return options[default][0]
            if a.isdigit() and 1 <= int(a) <= len(options):
                return options[int(a) - 1][0]

    def confirm(self, question, default=True):
        if self.yes:
            return default
        a = self._read(f"{question} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
        return default if not a else a in ("y", "yes")


# ── releases ──────────────────────────────────────────────────────────────────────────────────────────────────────
def vkey(v):
    return [int(x) if x.isdigit() else x for x in re.split(r"[.+-]", v)]


def parse_spec(spec):
    m = re.fullmatch(r"(?:https://github\.com/)?([\w.-]+/[\w.-]+?)(?:\.git)?(?::([\w.,-]+))?/?", spec.strip())
    if not m:
        raise Refuse(f"{spec!r}: give the repository as OWNER/REPO, or OWNER/REPO:MOD for a repository of several mods")
    return m.group(1), ([x for x in m.group(2).split(",") if x] if m.group(2) else None)


def tagged(rels):
    """[(release, series, version, mc, rerelease)] for every published release whose tag is a mod release."""
    out = []
    for r in rels:
        m = TAG.match(r.get("tag_name", ""))
        if m and not r.get("draft"):
            out.append((r, m["series"], m["ver"], m["mc"], int(m["r"] or 0)))
    return out


def releases_of(src, repo):
    try:
        return src.releases(repo)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise Refuse(f"{repo}: no such public repository on GitHub")
        raise Refuse(f"{repo}: GitHub's release list could not be read (HTTP {e.code}"
                     f"{'; rate limited: wait an hour or set GITHUB_TOKEN' if e.code in (403, 429) else ''})")
    except OSError as e:
        raise Refuse(f"{repo}: GitHub could not be reached ({e})")


def versions(tl, series):
    """{mc: {"full": newest full release or None, "pre": newest release of any kind}} for one series."""
    out = {}
    for r, s, ver, mc, rr in sorted((t for t in tl if t[1] == series), key=lambda t: (vkey(t[2]), t[4])):
        e = out.setdefault(mc, {"full": None, "pre": None})
        e["pre"] = r
        if not r.get("prerelease"):
            e["full"] = r
    return out


def jar_asset(rel):
    jars = [a for a in rel.get("assets", []) if a["name"].endswith(".jar")
            and not re.search(r"-(sources|javadoc|dev|api)\.jar$", a["name"])]
    if not jars:
        raise Refuse(f"release {rel['tag_name']} has no mod jar")
    if len(jars) > 1:
        raise Refuse(f"release {rel['tag_name']} has several jars ({', '.join(a['name'] for a in jars)}): "
                     "this installer takes one jar per release")
    return jars[0]


def asset_sha256(src, rel, asset):
    d = asset.get("digest") or ""
    if d.startswith("sha256:"):
        return d.split(":", 1)[1]
    sums = next((a for a in rel.get("assets", []) if a["name"] in ("SHA256SUMS", "SHA256SUMS.txt")), None)
    if sums:
        for line in src.get(sums["browser_download_url"]).decode("utf-8", "replace").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].lstrip("*") == asset["name"]:
                return parts[0].lower()
    raise Refuse(f"{asset['name']} ({rel['tag_name']}): no sha256 to check it against (no GitHub digest, no "
                 "SHA256SUMS) -- not installed")


# ── what a release pins ───────────────────────────────────────────────────────────────────────────────────────────
def lock_deps(text):
    """[{repo, tag, asset, sha256}] from a lock file's `<name>_repo/_tag/_asset/_sha256` quads."""
    kv = RI.props(text)
    out = []
    for k in sorted(kv):
        if k.endswith("_repo"):
            n = k[:-len("_repo")]
            e = {"repo": kv[k], "tag": kv.get(n + "_tag"), "asset": kv.get(n + "_asset"), "sha256": kv.get(n + "_sha256")}
            if all(e.values()):
                out.append(e)
    return out


def tree(src, repo, tag):
    try:
        return [e["path"] for e in src._api(f"repos/{repo}/git/trees/{tag}?recursive=1").get("tree", [])
                if e.get("type") == "blob"]
    except Exception:
        return None


def pins(src, repo, tag, folder):
    """The dependencies a release pins at its tag, and the NeoForge it was built on (or None)."""
    paths = tree(src, repo, tag)
    if paths is not None:
        locks = [p for p in paths if p.endswith(".lock") and (p.count("/") == 0 or (folder and p == f"{folder}/{p.rsplit('/', 1)[-1]}"))]
    else:                      # no API: the lock names this installer knows how to look for
        locks = [p for p in ([f"{folder}/core.lock"] if folder else []) + ["core.lock", "deps.lock"]]
    deps = []
    for p in locks:
        t = src.file(repo, tag, p)
        if t:
            deps += lock_deps(t)
    man = src.file(repo, tag, ".github/port-install.json")
    if man:
        try:
            for f in json.loads(man).get("files", []):
                if f.get("role") == "required" and f.get("url") and f.get("sha256"):
                    m = RELEASE_URL.match(f["url"])
                    deps.append({"repo": m.group(1) if m else None, "tag": m.group(2) if m else None,
                                 "asset": f.get("name") or f["url"].rsplit("/", 1)[-1], "sha256": f["sha256"], "url": f["url"]})
        except ValueError:
            pass
    neo = None
    for p in ([f"{folder}/gradle.properties"] if folder else []) + ["gradle.properties"]:
        g = RI.props(src.file(repo, tag, p) or "")
        neo = g.get("neo_version") or g.get("neoforge_version")
        if neo:
            break
    return deps, neo


def neo_line(mc):
    """NeoForge's version prefix for a Minecraft version: 1.21.8 -> 21.8, 26.1.2 -> 26.1.2, 26.3 -> 26.3.0."""
    p = mc.split(".")
    if p[0] == "1":
        return ".".join(p[1:3])
    return ".".join((p + ["0"])[:3])


def newest_neo(src, mc, floor=None):
    xml = src.get(NEO_META).decode("utf-8", "replace")
    line = neo_line(mc) + "."
    vs = [v for v in re.findall(r"<version>([^<]+)</version>", xml) if v.startswith(line) and not re.search(r"[a-z]", v)]
    if floor:
        vs = [v for v in vs if vkey(v) >= vkey(floor)]
    return max(vs, key=vkey) if vs else None


# ── the plan ──────────────────────────────────────────────────────────────────────────────────────────────────────
def resolve(src, repo, mods, a, ask, log=print):
    """-> plan dict: mc, jars [{repo, tag, name, url, sha256, size, role, modids}], neoforge, notes."""
    tl = tagged(releases_of(src, repo))
    if not tl:
        raise Refuse(f"{repo}: no releases tagged <mod>-<version>-mc<minecraft> -- nothing to install")
    series = sorted({t[1] for t in tl})
    want_mc = a.mc
    if a.release:
        # --release names one release: its tag fixes the mod and the Minecraft version, so the NeoForge and the
        # vanilla files are that release's (with --yes the default would otherwise be the newest version)
        hit = next((t for t in tl if t[0]["tag_name"] == a.release), None)
        if not hit:
            raise Refuse(f"{repo} has no release {a.release}")
        if mods and mods != [hit[1]]:
            raise Refuse(f"{a.release} is a release of {hit[1]}, not of {', '.join(mods)}")
        if a.mc and a.mc != hit[3]:
            raise Refuse(f"{a.release} is for Minecraft {hit[3]}, not {a.mc}")
        mods, want_mc = [hit[1]], hit[3]
    if mods:
        unknown = [m for m in mods if m not in series]
        if unknown:
            raise Refuse(f"{repo}: no releases of {', '.join(unknown)} (it releases: {', '.join(series)})")
    elif len(series) == 1:
        mods = series
    else:
        pick = ask.choose(f"{repo} releases several mods. Which one?",
                          [(s, s) for s in series] + [(",".join(series), "all of them")], 0) if not a.yes else None
        if pick is None:
            raise Refuse(f"{repo} releases several mods ({', '.join(series)}): name one with {repo}:<mod> "
                         f"(e.g. {repo}:{series[0]})")
        mods = pick.split(",")
    vs = {m: versions(tl, m) for m in mods}
    common = sorted(set.intersection(*(set(v) for v in vs.values())), key=vkey)
    if not common:
        raise Refuse(f"{', '.join(mods)} have no Minecraft version in common")
    def label(mc):
        full = all(vs[m][mc]["full"] for m in mods)
        tags = ", ".join(vs[m][mc]["full" if full else "pre"]["tag_name"] for m in mods)
        return f"Minecraft {mc}  -- {'' if full else 'pre-release only: '}{tags}"
    if want_mc:
        if want_mc not in common:
            raise Refuse(f"no release for Minecraft {want_mc} (released for: {', '.join(common)})")
        mc = want_mc
    else:
        full_mcs = [mc for mc in common if all(vs[m][mc]["full"] for m in mods)]
        default = (full_mcs or common)[-1]
        mc = ask.choose("Which Minecraft version?", [(m, label(m)) for m in reversed(common)],
                        list(reversed(common)).index(default))
    jars, notes = [], []
    for m in mods:
        e = vs[m][mc]
        rel = next((t[0] for t in tl if t[0]["tag_name"] == a.release), None) if a.release else None
        if a.release and not rel:
            raise Refuse(f"{repo} has no release {a.release}")
        if not rel:
            rel = e["full"]
            if rel is None or (a.pre and e["pre"] is not e["full"]):
                if a.pre or ask.confirm(f"No full release of {m} for Minecraft {mc} yet. Install the pre-release "
                                        f"{e['pre']['tag_name']}?", default=False):
                    rel = e["pre"]
                else:
                    raise Refuse(f"no full release of {m} for Minecraft {mc} yet; add --pre to install the pre-release "
                                 f"{e['pre']['tag_name']}")
        jars.append(jar_entry(src, repo, rel, "mod", m))
    seen = {(j["repo"], j["tag"]) for j in jars}
    todo = [(j["repo"], j["tag"], j["series"]) for j in jars]
    neos = []
    while todo:
        r, tag, s = todo.pop(0)
        deps, neo = pins(src, r, tag, s)
        if neo:
            neos.append(neo)
        for d in deps:
            key = (d["repo"], d["tag"])
            if key in seen:
                continue
            seen.add(key)
            m = TAG.match(d["tag"] or "")
            if m and m["mc"] != mc:
                raise Refuse(f"{tag} pins {d['repo']} {d['tag']}, built for Minecraft {m['mc']}, not {mc}")
            url = d.get("url") or f"https://github.com/{d['repo']}/releases/download/{d['tag']}/{d['asset']}"
            jars.append({"repo": d["repo"], "tag": d["tag"], "series": m["series"] if m else None, "name": d["asset"],
                         "url": url, "sha256": d["sha256"].lower(), "size": None, "role": f"needed by {tag}"})
            if d["repo"] and d["tag"]:
                todo.append((d["repo"], d["tag"], m["series"] if m else None))
    line = neo_line(mc)
    tested = sorted({n for n in neos if n.startswith(line + ".")}, key=vkey)
    return {"repo": repo, "mods": mods, "mc": mc, "jars": jars, "neoforge": tested[-1] if tested else None,
            "notes": notes}


def jar_entry(src, repo, rel, role, series):
    asset = jar_asset(rel)
    return {"repo": repo, "tag": rel["tag_name"], "series": series, "name": asset["name"],
            "url": asset["browser_download_url"], "sha256": asset_sha256(src, rel, asset).lower(),
            "size": asset.get("size"), "role": role, "prerelease": bool(rel.get("prerelease"))}


def fetch_all(src, plan):
    """Every jar downloaded and checked before anything is written. -> {name: bytes}."""
    out = {}
    for j in plan["jars"]:
        data = src.get(j["url"])
        got = hashlib.sha256(data).hexdigest()
        if got != j["sha256"]:
            raise Refuse(f"{j['name']}: sha256 {got} does not match the release's {j['sha256']} -- nothing was written")
        j["modids"] = sorted(IP.jar_modids(data))
        j["neo_floor"] = IP.jar_neoforge_floor(data)
        j["title"] = display_name(data)
        out[j["name"]] = data
    if not plan["neoforge"]:
        floors = [j["neo_floor"] for j in plan["jars"] if j.get("neo_floor")]
        floor = max(floors, key=vkey) if floors else None
        plan["neoforge"] = newest_neo(src, plan["mc"], floor)
        if not plan["neoforge"]:
            raise Refuse(f"no NeoForge release for Minecraft {plan['mc']}{' at or above ' + floor if floor else ''}")
    return out


def display_name(data):
    """The first mod's displayName in a jar's neoforge.mods.toml, or None."""
    import io, zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            toml = z.read("META-INF/neoforge.mods.toml").decode("utf-8", "replace")
    except (KeyError, zipfile.BadZipFile):
        return None
    m = re.search(r'(?m)^\s*displayName\s*=\s*"([^"$]+)"', toml)
    return m.group(1).strip() if m else None


def notes(src, plan):
    """What each installed mod tells a player after an install: `player-install.txt` in the mod's folder, else
    `.github/player-install.txt`, at the release's tag -- plain text, at most 12 lines (e.g. how to turn on an
    optional feature in game). Optional; a repository without one prints nothing."""
    out = []
    for j in plan["jars"]:
        for path in ([f"{j['series']}/player-install.txt"] if j.get("series") else []) + [".github/player-install.txt"]:
            try:
                t = src.file(j["repo"], j["tag"], path) if j.get("repo") and j.get("tag") else None
            except Exception:
                t = None
            if t:
                out += [l.rstrip() for l in t.strip().splitlines()[:12]]
                break
    return out


# ── where it goes ─────────────────────────────────────────────────────────────────────────────────────────────────
def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "mod"


def profiles(mcdir):
    f = pathlib.Path(mcdir) / "launcher_profiles.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")).get("profiles", {}) if f.is_file() else {}
    except ValueError:
        return {}


def neoforge_profiles(mcdir, mc):
    """[(profile id, name, game dir)] of the launcher's NeoForge profiles for this Minecraft version."""
    line = neo_line(mc) + "."
    out = []
    for k, p in sorted(profiles(mcdir).items()):
        if str(p.get("lastVersionId", "")).startswith("neoforge-" + line):
            out.append((k, p.get("name") or k, pathlib.Path(p.get("gameDir") or mcdir)))
    return out


def choose_target(plan, mcdir, a, ask):
    title = "-".join(plan["mods"])
    shown = " + ".join(j.get("title") or j["series"] for j in plan["jars"] if j["role"] == "mod")
    new = {"kind": "new", "profile": f"mmvu-{slug(plan['repo'].split('/')[1])}-{slug(title)}-{plan['mc']}",
           "name": f"{shown} {plan['mc']}", "dir": pathlib.Path(mcdir) / "instances" / f"{slug(title)}-{plan['mc']}"}
    existing = neoforge_profiles(mcdir, plan["mc"])
    if a.instance and a.instance != "new":
        hit = next((e for e in existing if a.instance in (e[0], e[1])), None)
        if hit:
            return {"kind": "profile", "profile": hit[0], "name": hit[1], "dir": hit[2]}
        p = pathlib.Path(a.instance).expanduser()
        if p.is_dir() or not existing or os.sep in a.instance or "/" in a.instance:
            return {"kind": "folder", "profile": None, "name": str(p), "dir": p}
        raise Refuse(f"--instance {a.instance!r}: no NeoForge {neo_line(plan['mc'])} profile of that name "
                     f"(there are: {', '.join(e[1] for e in existing) or 'none'}) and no such folder")
    if a.instance == "new" or not existing:
        return new
    opts = [(new, f"a NEW profile \"{new['name']}\" with its own folder ({new['dir']}) -- your other mods stay untouched")]
    opts += [({"kind": "profile", "profile": k, "name": n, "dir": d}, f"your profile \"{n}\" ({d / 'mods'})")
             for k, n, d in existing]
    return ask.choose("Where should it go?", opts, 0)


# ── writing ───────────────────────────────────────────────────────────────────────────────────────────────────────
def place(mods_dir, jars, data, log=print):
    """Each jar into mods/; a jar of the same mod id moved to mods-replaced/; nothing else touched."""
    mods_dir.mkdir(parents=True, exist_ok=True)
    want = {m for j in jars for m in j["modids"]}
    names = {j["name"] for j in jars}
    for p in sorted(mods_dir.glob("*.jar")):
        if p.name in names:
            continue
        try:
            ids = set(IP.jar_modids(p.read_bytes()))
        except Exception:
            continue
        if ids & want:
            (mods_dir.parent / "mods-replaced").mkdir(exist_ok=True)
            os.replace(p, mods_dir.parent / "mods-replaced" / p.name)
            log(f"  replaced {p.name} (moved to mods-replaced/)")
    for j in jars:
        d = mods_dir / j["name"]
        if d.is_file() and RI.sha_file(d, "sha256") == j["sha256"]:
            log(f"  {j['name']}: already there")
            continue
        tmp = d.with_name(d.name + ".part")
        tmp.write_bytes(data[j["name"]])
        os.replace(tmp, d)
        log(f"  + {j['name']}")


ESSENTIAL = ("version json", "client jar")    # what NeoForge's installer needs; the Launcher fetches the rest on first play


def ensure_game(src, plat, mcdir, mc, neo, dry, log=print, full=False):
    """Vanilla files, the Launcher's Java and NeoForge `neo`, as tools/real-install.py installs them. Only the version
    file and the client jar unless `full` (--boot-test, which starts the game without the Launcher): the Minecraft
    Launcher downloads vanilla's libraries and thousands of asset files itself the first time the profile is played.
    -> (version id, Java runtime component)."""
    mcdir = pathlib.Path(mcdir)
    need, meta = RI.vanilla_needs(mcdir, mc, src, plat)
    if not full:
        need = [n for n in need if n[0] in ESSENTIAL]
    jv = meta.get("javaVersion") or {}
    comp = jv.get("component") or ("java-runtime-epsilon" if (jv.get("majorVersion") or RI.java_major_default(mc)) >= 25
                                   else "java-runtime-delta")
    have = IP.neoforge_installed(mcdir, neo)
    if dry:
        log(f"  would fetch {len(need)} Minecraft file(s)" if need else "  Minecraft files: present")
        log("  Java: present" if RI.java_exe(mcdir, comp, plat) else f"  would fetch the Launcher's Java ({comp})")
        log(f"  NeoForge {have} is installed" if have else f"  would install NeoForge {neo} (its installer, headless)")
        return f"neoforge-{have or neo}", comp
    if need:
        # thousands of small asset files: 16 at a time (one at a time took over 15 minutes for 1.21.8's assets)
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(lambda n: RI.download_to(n[1], mcdir / n[2], sha1=n[3]), need))
        log(f"  fetched {len(need)} Minecraft file(s)")
    if not RI.java_exe(mcdir, comp, plat):
        RI.install_runtime(mcdir, comp, src, plat, log)
    if not have:
        pid = RI.launcher_running()
        if pid:
            raise Refuse(f"the Minecraft Launcher is running (pid {pid}): close it and run this again (installing "
                         "NeoForge edits its profiles file)")
        lp = mcdir / "launcher_profiles.json"
        made = RI.ensure_launcher_profiles(mcdir)
        keep = lp.read_bytes()
        java = RI.java_exe(mcdir, comp, plat)
        run = lambda argv, **kw: subprocess.run([str(java)] + list(argv[1:]), **kw)
        try:
            notes, prob = IP.ensure_neoforge([(mc, neo, "release")], mcdir / "mods", False, src.get, run=run, mc_dir=mcdir)
        finally:
            lp.write_bytes(keep)       # the installer's own "NeoForge" profile edit is undone: ours is added below
        if made:
            log(f"  wrote a minimal {lp} (no Minecraft Launcher has run here yet)")
        if prob:
            raise Refuse("; ".join(prob))
        have = IP.neoforge_installed(mcdir, neo)
        log(f"  NeoForge {have} installed")
    vid = f"neoforge-{have}"
    RI.ensure_version_jar(mcdir, vid, mc)
    return vid, comp


def ensure_python(ask, dry, log=print):
    """--with python: Python 3 on the PATH for the game. Installs Python 3.12 for this user only (Windows: winget)."""
    for c in (["python3"], ["python"], ["py", "-3"]):   # exec-ok: is a Python on the PATH for the GAME, not this script
        try:
            r = subprocess.run(c + ["-c", "import sys; print(sys.version.split()[0])"], capture_output=True, text=True,
                               encoding="utf-8", timeout=30)
            if r.returncode == 0 and r.stdout.startswith("3."):
                log(f"  Python {r.stdout.strip()} is on the PATH ({' '.join(c)})")
                return
        except (OSError, subprocess.SubprocessError):
            pass
    if os.name == "nt" and shutil.which("winget"):
        if dry:
            log("  would install Python 3.12 for this user (winget)"); return
        if ask.confirm("Python 3 is not on the PATH. Install Python 3.12 for this user (winget)?"):
            subprocess.run(["winget", "install", "-e", "--id", "Python.Python.3.12", "--scope", "user",
                            "--accept-source-agreements", "--accept-package-agreements"])
            return
    log("  Python 3 is not on the PATH: install it from https://www.python.org/downloads/ (this installer does not "
        "install it system-wide)")


def add_profile(mcdir, target, vid, mc, dry, log=print):
    if target["kind"] == "new":
        entry = {"name": target["name"], "type": "custom", "lastVersionId": vid, "gameDir": str(target["dir"]),
                 "icon": "Furnace"}
        prob = RI.add_profiles(mcdir, {target["profile"]: entry}, dry, log=log)
    elif target["kind"] == "profile":
        cur = profiles(mcdir).get(target["profile"], {})
        if cur.get("lastVersionId") == vid:
            return
        prob = RI.add_profiles(mcdir, {target["profile"]: {**cur, "lastVersionId": vid}}, dry, log=log)
    else:
        log(f"  no launcher profile changed: in the Launcher, use NeoForge {vid[len('neoforge-'):]} with game "
            f"directory {target['dir']}")
        return
    if prob:
        raise Refuse("; ".join(prob))


def boot_test(src, plat, mcdir, vid, comp, target, modids, log=print):
    java = RI.java_exe(mcdir, comp, plat)
    argv, missing = RI.build_launch(mcdir, vid, target["dir"], plat, java)
    if missing:
        return "FAIL", f"{len(missing)} missing file(s), e.g. {missing[0]}"
    done = lambda t: bool(RI.TITLE_SOUND.search(t) and RI.TITLE_ATLAS.search(t)) or bool(RI.FATAL_LOG.search(t))
    out = pathlib.Path(tempfile.mkdtemp(prefix="player-install-boot-"))
    env = dict(os.environ)
    if plat.os_name == "linux":
        # a headless box has no sound card: OpenAL's null device lets the sound engine start (the boot judge waits
        # for "Sound engine started"); 26.3+ opens its window through SDL, which needs EGL under Xvfb (CATALOG V103)
        env.setdefault("ALSOFT_DRIVERS", "null")
        env.setdefault("SDL_VIDEO_FORCE_EGL", "1")
    text, err, crash, secs, rc = RI.run_once(argv, target["dir"], out, 600, done, plat, env=env)
    return RI.judge_boot(text, err, crash, modids)


# ── the release bundle ────────────────────────────────────────────────────────────────────────────────────────────
BUNDLE = ("tools/player-install.py", "tools/real-install.py", "tools/install-port.py", "tools/find-minecraft.py",
          "tools/port-manifest.py", "tools/fork-kind.py")
BOOTSTRAPS = ("player-install/install.sh", "player-install/install.ps1", "player-install/RELEASE-NOTES.md")


def bundle(out, tag, root=HERE.parent):
    """The files a player-install-vN release publishes: player-install.zip (the installer and the tools it composes,
    byte-identical for the same sources: sorted, fixed timestamps), install.sh and install.ps1 with the tag and the
    zip's sha256 written in, and SHA256SUMS. -> {name: sha256}."""
    import io, zipfile
    if not re.fullmatch(r"player-install-v\d+", tag):
        raise Refuse(f"--tag {tag!r}: a player-install release tag is player-install-v<N>")
    out = pathlib.Path(out); out.mkdir(parents=True, exist_ok=True)
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in sorted(BUNDLE):
            zi = zipfile.ZipInfo(rel, date_time=(2020, 1, 1, 0, 0, 0))
            zi.external_attr = 0o644 << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, (root / rel).read_bytes())
    data = b.getvalue()
    sha = hashlib.sha256(data).hexdigest()
    (out / "player-install.zip").write_bytes(data)
    sums = {"player-install.zip": sha}
    for rel in BOOTSTRAPS:
        text = (root / rel).read_text(encoding="utf-8").replace("@TAG@", tag).replace("@SHA@", sha)
        name = rel.rsplit("/", 1)[1]
        with open(out / name, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        if name != "RELEASE-NOTES.md":
            sums[name] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    with open(out / "SHA256SUMS", "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(f"{v}  {k}\n" for k, v in sorted(sums.items())))
    return sums


# ── main ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def show_plan(plan, target, mcdir, log=print):
    log("")
    log(f"Plan for {plan['repo']}: Minecraft {plan['mc']}, NeoForge {plan['neoforge']}")
    for j in plan["jars"]:
        log(f"  {j['name']}  ({j['tag']}{', pre-release' if j.get('prerelease') else ''}; {j['role']})")
    where = {"new": f"a new launcher profile \"{target['name']}\", game folder {target['dir']}",
             "profile": f"your launcher profile \"{target['name']}\" ({target['dir'] / 'mods'})",
             "folder": f"the game folder {target['dir']}"}[target["kind"]]
    log(f"  into {where}")
    log(f"  Minecraft folder: {mcdir}")
    log("")


def run(a, src=None, plat=None, ask=None, log=print):
    repo, mods = parse_spec(a.repo)
    src = src or RI.Sources(pathlib.Path.home() / ".mmvu" / "player-install" / "cache")
    plat = plat or RI.Plat.host()
    ask = ask or Ask(a.yes, out=log)
    if a.list:
        tl = tagged(releases_of(src, repo))
        for s in sorted({t[1] for t in tl}):
            for mc, e in sorted(versions(tl, s).items(), key=lambda kv: vkey(kv[0])):
                log(f"{repo}:{s}  Minecraft {mc}: " + (f"{e['full']['tag_name']}" if e["full"] else "no full release")
                    + (f"; newest pre-release {e['pre']['tag_name']}" if e["pre"] is not e["full"] else ""))
        return 0
    plan = resolve(src, repo, mods, a, ask, log)
    log("Downloading and checking every jar before anything is written...")
    data = fetch_all(src, plan)
    mcdir = pathlib.Path(a.minecraft_dir or RI.default_minecraft_dir()).expanduser()
    target = choose_target(plan, mcdir, a, ask)
    show_plan(plan, target, mcdir, log)
    if a.dry_run:
        ensure_game(src, plat, mcdir, plan["mc"], plan["neoforge"], True, log)
        log("--dry-run: nothing was written.")
        return 0
    if not ask.confirm("Install it?"):
        raise Refuse("stopped: nothing was written")
    vid, comp = ensure_game(src, plat, mcdir, plan["mc"], plan["neoforge"], False, log, full=a.boot_test)
    place(target["dir"] / "mods", plan["jars"], data, log)
    add_profile(mcdir, target, vid, plan["mc"], False, log)
    if a.with_ and "python" in a.with_:
        ensure_python(ask, False, log)
    log("")
    log(f"Installed. Open the Minecraft Launcher and play \"{target['name']}\"." if target["kind"] != "folder"
        else f"Installed into {target['dir']}.")
    for line in notes(src, plan):
        log(line)
    if a.boot_test:
        verdict, detail = boot_test(src, plat, mcdir, vid, comp, target, sorted({m for j in plan["jars"] for m in j["modids"]}), log)
        log(f"Boot test: {verdict} -- {detail}")
        return 0 if verdict == "PASS" else 1
    return 0


def make_parser():
    ap = argparse.ArgumentParser(description="Install a mod from its GitHub releases into your own Minecraft.")
    ap.add_argument("repo", help="OWNER/REPO, or OWNER/REPO:MOD[,MOD] for a repository of several mods")
    ap.add_argument("--mc", help="Minecraft version (default: the newest with a full release; asked when interactive)")
    ap.add_argument("--release", help="a release tag instead of the newest")
    ap.add_argument("--pre", action="store_true", help="allow pre-releases")
    ap.add_argument("--instance", help="new (default), an existing NeoForge profile's name, or a game folder")
    ap.add_argument("--minecraft-dir", help="the Minecraft folder (default: the official launcher's)")
    ap.add_argument("--yes", "-y", action="store_true", help="take every default, ask nothing")
    ap.add_argument("--dry-run", action="store_true", help="show the plan, write nothing")
    ap.add_argument("--list", action="store_true", help="list released mods and Minecraft versions")
    ap.add_argument("--with", dest="with_", action="append", choices=["python"], help="optional extras")   # exec-ok: an option name
    ap.add_argument("--boot-test", action="store_true", help="after installing, boot it headless and check the mods load")
    ap.add_argument("--self-check", action="store_true", help=argparse.SUPPRESS)
    return ap


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--self-check" in argv:
        return self_check()
    if "--bundle" in argv:
        bp = argparse.ArgumentParser(); bp.add_argument("--bundle", required=True); bp.add_argument("--tag", required=True)
        b = bp.parse_args(argv)
        try:
            for k, v in sorted(bundle(b.bundle, b.tag).items()):
                print(f"{v}  {k}")
        except Refuse as e:
            print(f"player-install: {e}", file=sys.stderr)
            return 1
        return 0
    a = make_parser().parse_args(argv)
    try:
        return run(a)
    except Refuse as e:
        print(f"player-install: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nplayer-install: stopped", file=sys.stderr)
        return 1


# ── self-check ────────────────────────────────────────────────────────────────────────────────────────────────────
def self_check():
    """Offline: fake releases, jars and a fake Minecraft folder -- resolution, refusals and placement."""
    import io, zipfile
    ok = True
    def chk(c, what):
        nonlocal ok
        if not c:
            ok = False
            print("FAIL:", what)
    def jar(modid, floor="21.8.0"):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            z.writestr("META-INF/neoforge.mods.toml", f'modLoader="javafml"\nloaderVersion="[1,)"\n[[mods]]\nmodId="{modid}"\n'
                       f'[[dependencies.{modid}]]\nmodId="neoforge"\ntype="required"\nversionRange="[{floor},)"\n')
        return b.getvalue()
    core = {mc: jar("examplecore") for mc in ("1.21.8", "26.3")}
    isle = jar("exampleisle")
    def rel(tag, pre, name, data, digest=True):
        a = {"name": name, "browser_download_url": f"https://example.invalid/{tag}/{name}", "size": len(data)}
        if digest:
            a["digest"] = "sha256:" + hashlib.sha256(data).hexdigest()
        return {"tag_name": tag, "prerelease": pre, "draft": False, "assets": [a]}
    rels = {"o/core": [rel("examplecore-1.0.9-mc1.21.8", False, "examplecore-1.0.9.jar", core["1.21.8"]),
                       rel("examplecore-1.0.10-mc1.21.8", True, "examplecore-1.0.10.jar", core["1.21.8"]),
                       rel("examplecore-1.0.10-mc26.3", True, "examplecore-1.0.10-26.3.jar", core["26.3"])],
            "o/worlds": [rel("isle-0.1.6-mc1.21.8", False, "exampleisle-0.1.6.jar", isle, digest=False)
                         | {"assets": [{"name": "exampleisle-0.1.6.jar", "browser_download_url": "https://example.invalid/i.jar", "size": len(isle)},
                                       {"name": "SHA256SUMS", "browser_download_url": "https://example.invalid/sums"}]},
                         rel("arena-0.2.0-mc1.21.8", False, "examplearena.jar", jar("examplearena"))]}
    lock = (f"core_repo=o/core\ncore_tag=examplecore-1.0.9-mc1.21.8\ncore_asset=examplecore-1.0.9.jar\n"
            f"core_sha256={hashlib.sha256(core['1.21.8']).hexdigest()}\n")
    files = {("o/worlds", "isle-0.1.6-mc1.21.8", "isle/core.lock"): lock,
             ("o/core", "examplecore-1.0.9-mc1.21.8", "gradle.properties"): "neo_version=21.8.57\n"}
    urls = {"https://example.invalid/examplecore-1.0.9-mc1.21.8/examplecore-1.0.9.jar": core["1.21.8"],
            "https://example.invalid/examplecore-1.0.10-mc26.3/examplecore-1.0.10-26.3.jar": core["26.3"],
            "https://github.com/o/core/releases/download/examplecore-1.0.9-mc1.21.8/examplecore-1.0.9.jar": core["1.21.8"],
            "https://example.invalid/i.jar": isle,
            "https://example.invalid/sums": f"{hashlib.sha256(isle).hexdigest()}  exampleisle-0.1.6.jar\n".encode(),
            NEO_META: b"<versions><version>26.3.0.101</version><version>26.3.0.118</version><version>26.3.0.120-beta</version></versions>"}

    class Fake:
        def releases(self, repo):
            if repo not in rels:
                raise urllib.error.HTTPError("u", 404, "nf", None, None)
            return rels[repo]
        def file(self, repo, ref, path): return files.get((repo, ref, path))
        def get(self, url): return urls[url]
        def _api(self, path): raise OSError("no api in the self-check")
    src = Fake()
    def A(spec, **kw):
        d = dict(repo=spec, mc=None, release=None, pre=False, instance=None, minecraft_dir=None, yes=True,
                 dry_run=False, list=False, with_=None, boot_test=False)
        d.update(kw)
        return argparse.Namespace(**d)
    ask = Ask(True, out=lambda *x: None)
    q = lambda *x: None
    # core: newest FULL release by default; 26.3 has only a pre-release
    p = resolve(src, "o/core", None, A("o/core"), ask, q)
    chk(p["mc"] == "1.21.8" and p["jars"][0]["tag"] == "examplecore-1.0.9-mc1.21.8" and p["neoforge"] == "21.8.57",
        f"core: the newest full release and its built-on NeoForge: {p}")
    try:
        resolve(src, "o/core", None, A("o/core", mc="26.3"), ask, q); chk(False, "26.3 without --pre must refuse")
    except Refuse as e:
        chk("no full release" in str(e) and "--pre" in str(e), f"26.3 without --pre says to add --pre: {e}")
    p = resolve(src, "o/core", None, A("o/core", mc="26.3", pre=True), ask, q)
    fetch_all(src, p)
    chk(p["jars"][0]["tag"].endswith("mc26.3") and p["neoforge"] == "26.3.0.118",
        f"26.3 --pre: the pre-release and the newest stable NeoForge of 26.3 at or above the jar's floor: {p['neoforge']}")
    p = resolve(src, "o/core", None, A("o/core", pre=True, mc="1.21.8"), ask, q)
    chk(p["jars"][0]["tag"] == "examplecore-1.0.10-mc1.21.8", "--pre takes the newest release, full or not")
    # --release: the tag fixes the Minecraft version (not the newest), and the mod; a pre-release needs no --pre
    p = resolve(src, "o/core", None, A("o/core", release="examplecore-1.0.10-mc26.3"), ask, q)
    chk(p["mc"] == "26.3" and p["jars"][0]["tag"] == "examplecore-1.0.10-mc26.3",
        f"--release of a 26.3 tag installs for 26.3, not the newest full version: {p['mc']} {p['jars'][0]['tag']}")
    for kw, want in ((dict(release="examplecore-1.0.10-mc26.3", mc="1.21.8"), "not 1.21.8"),
                     (dict(release="examplecore-9.9-mc1.21.8"), "has no release")):
        try:
            resolve(src, "o/core", None, A("o/core", **kw), ask, q); chk(False, f"--release {kw} must refuse")
        except Refuse as e:
            chk(want in str(e), f"--release {kw} refused: {e}")
    try:
        resolve(src, "o/core", ["other"], A("o/core", release="examplecore-1.0.9-mc1.21.8"), ask, q)
        chk(False, "--release of another mod must refuse")
    except Refuse as e:
        chk("not of other" in str(e), f"--release of another mod refused: {e}")
    # worlds: several mods -- --yes refuses without :mod; :isle pulls core from its core.lock, sha from SHA256SUMS
    try:
        resolve(src, "o/worlds", None, A("o/worlds"), ask, q); chk(False, "several mods with --yes must refuse")
    except Refuse as e:
        chk("o/worlds:isle" in str(e) or "o/worlds:arena" in str(e), f"names the :mod form: {e}")
    p = resolve(src, "o/worlds", ["isle"], A("o/worlds:isle"), ask, q)
    names = [j["name"] for j in p["jars"]]
    chk(names == ["exampleisle-0.1.6.jar", "examplecore-1.0.9.jar"] and p["neoforge"] == "21.8.57",
        f"isle + the core its core.lock pins, NeoForge from core's tag: {names} {p['neoforge']}")
    data = fetch_all(src, p)
    # a tampered asset stops before writing
    bad = dict(urls); bad["https://example.invalid/i.jar"] = isle + b"x"
    class Tampered(Fake):
        def get(self, url): return bad[url]
    try:
        fetch_all(Tampered(), resolve(Tampered(), "o/worlds", ["isle"], A("o/worlds:isle"), ask, q)); chk(False, "tamper")
    except Refuse as e:
        chk("does not match" in str(e) and "nothing was written" in str(e), f"a sha mismatch refuses: {e}")
    # placement: replaces the same mod id, leaves everything else, idempotent
    with tempfile.TemporaryDirectory() as d:
        mods = pathlib.Path(d) / "inst" / "mods"; mods.mkdir(parents=True)
        (mods / "examplecore-0.9.jar").write_bytes(jar("examplecore"))
        (mods / "other.jar").write_bytes(jar("someoneelse"))
        (mods / "notes.txt").write_text("keep", encoding="utf-8")
        place(mods, p["jars"], data, q)
        have = sorted(x.name for x in mods.iterdir())
        chk(have == ["examplecore-1.0.9.jar", "exampleisle-0.1.6.jar", "notes.txt", "other.jar"]
            and (mods.parent / "mods-replaced" / "examplecore-0.9.jar").is_file(),
            f"placement replaces the same mod id only: {have}")
        before = {x.name: x.stat().st_mtime_ns for x in mods.iterdir()}
        place(mods, p["jars"], data, q)
        chk(before == {x.name: x.stat().st_mtime_ns for x in mods.iterdir()}, "a re-run changes nothing")
        # targets: a new profile by default; an existing NeoForge profile by name
        mc = pathlib.Path(d) / "mc"; mc.mkdir()
        (mc / "launcher_profiles.json").write_text(json.dumps({"profiles": {"p1": {"name": "My NeoForge", "lastVersionId": "neoforge-21.8.50", "gameDir": str(pathlib.Path(d) / "mine")},
                                                                            "p2": {"name": "Vanilla", "lastVersionId": "1.21.8"}}}), encoding="utf-8")
        t = choose_target(p, mc, A("o/worlds:isle"), ask)
        chk(t["kind"] == "new" and t["dir"] == mc / "instances" / "isle-1.21.8", f"--yes: a new profile: {t}")
        t = choose_target(p, mc, A("o/worlds:isle", instance="My NeoForge"), ask)
        chk(t["kind"] == "profile" and t["profile"] == "p1", f"--instance by profile name: {t}")
        picks = iter(["2"])
        t = choose_target(p, mc, A("o/worlds:isle", yes=False), Ask(False, inp=lambda _p: next(picks), out=q))
        chk(t["kind"] == "profile" and t["name"] == "My NeoForge", f"interactive: the existing NeoForge profile: {t}")
        try:
            Ask(False, inp=lambda _p: "q", out=q).choose("x", [(1, "a"), (2, "b")]); chk(False, "q must stop")
        except Refuse as e:
            chk("nothing was written" in str(e), "q at any question stops with nothing written")
    # the release bundle: deterministic, the bootstraps carry the tag and the zip's sha256 (in an MMVU checkout only:
    # an installed bundle holds the installer, not the bootstrap templates it was published with)
    if (HERE.parent / BOOTSTRAPS[0]).is_file():
        with tempfile.TemporaryDirectory() as d:
            s1 = bundle(pathlib.Path(d) / "a", "player-install-v7")
            s2 = bundle(pathlib.Path(d) / "b", "player-install-v7")
            sh = (pathlib.Path(d) / "a" / "install.sh").read_text(encoding="utf-8")
            ps = (pathlib.Path(d) / "a" / "install.ps1").read_text(encoding="utf-8")
            chk(s1 == s2 and s1["player-install.zip"] in sh and "player-install-v7" in sh and "@SHA@" not in sh + ps
                and s1["player-install.zip"] in ps, "bundle: byte-identical twice, both bootstraps stamped")
            with zipfile.ZipFile(pathlib.Path(d) / "a" / "player-install.zip") as z:
                chk(sorted(z.namelist()) == sorted(BUNDLE), f"bundle: exactly the installer's files: {z.namelist()}")
            try:
                bundle(pathlib.Path(d) / "c", "v7"); chk(False, "a bad tag must refuse")
            except Refuse:
                pass
    files[("o/core", "examplecore-1.0.9-mc1.21.8", ".github/player-install.txt")] = "To turn on the extra later: open the menu.\n"
    chk(notes(src, resolve(src, "o/worlds", ["isle"], A("o/worlds:isle"), ask, q)) == ["To turn on the extra later: open the menu."],
        "a dependency's player-install.txt at its tag is printed after the install")
    chk(neo_line("1.21.8") == "21.8" and neo_line("26.1.2") == "26.1.2" and neo_line("26.3") == "26.3.0", "neo_line")
    chk(parse_spec("https://github.com/o/worlds:isle,arena") == ("o/worlds", ["isle", "arena"]), "parse_spec")
    print("self-check: OK" if ok else "self-check: FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
