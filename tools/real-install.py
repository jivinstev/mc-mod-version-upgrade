#!/usr/bin/env python3
"""Test fork ports' released jars on real Launcher installs of every Minecraft version they support -- no Gradle.

    python3 tools/real-install.py plan    --fork owner/Lib --fork owner/Mod (--mc 1.21.1 ... | --all-versions)
                                          [--prerelease] [--variant mr|cf] [--with-carrier [--carrier-release TAG |
                                          --carrier-dir DIR | --carrier-jar [MC=]JAR]] [--margin-gb 5]
    python3 tools/real-install.py install [the same flags | --plan FILE] [--profiles] [--dry-run]
    python3 tools/real-install.py test    [the same flags | --plan FILE] [--phases launch,spawn,battle,gauntlet]
                                          [--boot-only] [--timeout 300] [--phase-timeout 900] [--report DIR]
    python3 tools/real-install.py --self-check

PLAN (reads GitHub, Mojang and the registries; writes only its own work folder). Per Minecraft version: the NeoForge
version (the version branch's gradle.properties `neo_version` / `neoforge_version`, the newest across the forks,
raised within its line when a jar's own neoforge range needs newer), the Java major (the version JSON's `javaVersion`;
21 for 1.21.x and 25 for 26.x when there is none), each fork's jar (the newest release whose tag ends `-mc<mc>` or
`-mc<mc>-rN`, of the branch's own mod_version series when it has releases; `--prerelease` admits pre-releases; of
several jars the `--variant` one, default `mr`), each sibling-port jar a fork's version-branch workflow pins (its
`--dep-jar` curl line and the sha256 on the line after; with `--variant cf` the workflow's `--dep-jar-variant` cf pin
when there is one) unless that sibling is itself a --fork (its own release wins, `--prefer-pins` takes the pin), and
every registry dependency the jars require (each jar's META-INF/neoforge.mods.toml; Modrinth/CurseForge through
tools/mod-registry, sha1-verified, as tools/install-port.py resolves what CI had no file for). `--all-versions` is every
`neoforge-<mc>` branch ALL the forks have: a library version whose dependent has no port there is skipped, and said
so. A fork branch's `.github/port-install.json` is not read (every version branch carries the base version's). The
plan prints what each version downloads and an estimate of what it adds on disk (client jar, libraries, assets the
Launcher folder lacks, the Java runtime if absent, NeoForge, the mods), and refuses when the disk would keep less
than --margin-gb free. It is saved to <work>/plan.json (`--work`, default ~/.mmvu/real-install/<label>).

INSTALL. Per version: the Minecraft files the Launcher never downloaded (version JSON, client jar, libraries for this
OS/arch, asset index and objects, from Mojang's manifests, sha1-checked), the Java runtime the version names (Mojang's
runtime manifest) when absent, NeoForge with its own installer run headless by the Launcher's Java (the code path of
tools/install-port.py; launcher_profiles.json is put back byte for byte afterwards, so the installer's "NeoForge"
profile edit never reaches the player's own profiles), then the instance `<minecraft>/instances/<label>-<mc>/` (or
--instances-dir) with mods/ holding exactly that version's set (plus the Gate C carrier with --with-carrier), each jar
sha-verified. The player's own mods folder and profiles are never touched. `--profiles` also adds one Launcher profile
per version (gameDir = the instance) to launcher_profiles.json after a backup, and refuses while the Launcher runs (it
rewrites that file); so does a NeoForge install. Idempotent; `--dry-run` changes nothing.

TEST, one launch at a time (the software client is CPU heavy). Per version: the BOOT TEST launches the real client
from the instance with the command the Launcher builds (the version JSON chain, libraries for this OS/arch, the
NeoForge version's own jar -- never vanilla's -- an offline player, the Java the version JSON names, a watchdog
timeout). PASS = every mod under test is in the loaded mod list, no new crash-reports/ file, no exception on stderr
(where a JVM that dies before logging is up leaves its only trace) and the title screen reached (`Sound engine
started` and a texture atlas created), then the game is stopped. With the carrier installed, each Gate C phase is its
own launch with -Dsmokeharness.boottest=true -Dsmokeharness.testmode=<phase> -Dsmokeharness.namespaces=<mod ids under
test>, judged by its `SMOKEHARNESS_BOOT_TEST: PASS|FAIL` line (stdout or logs/latest.log), each phase with its own
timeout and logs. A per-version, per-phase table is printed and report.json + report.md written (--report DIR, default
<work>/reports/<time>). Exit 0 = everything passed, 1 = something failed, 2 = could not run.

The Gate C carrier is MMVU's own release `gatec-carrier-v<N>` (assets gatec-carrier-<mc>.jar, SHA256SUMS,
MANIFEST.txt): the newest one by default, `--carrier-release TAG` for one, `--carrier-dir DIR` for a folder of jars
(a CI artifact; its SHA256SUMS is checked when present) or `--carrier-jar [MC=]JAR` for one jar. `--jar [MC=]JAR`
adds a local jar under test and `--neoforge [MC=]VERSION` pins NeoForge (no --fork needed then). The Minecraft folder
is --minecraft-dir, else MINECRAFT_DIR (environment or .env.local), else the platform's default. Standard library only.
"""
import argparse, datetime, hashlib, importlib.util, io, json, os, pathlib, platform, re, shutil, subprocess, sys
import tempfile, time, urllib.error, urllib.parse, urllib.request, zipfile

HERE = pathlib.Path(__file__).resolve().parent
UA = "mmvu-real-install"
CARRIER_REPO = "jivinstev/mc-mod-version-upgrade"
VERSION_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
RUNTIME_MANIFEST = ("https://launchermeta.mojang.com/v1/products/java-runtime/"
                    "2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json")
OBJECTS = "https://resources.download.minecraft.net"
NEOFORGE_EST = 150 * 2**20          # what a NeoForge install adds (its libraries, the patched client): measured ~120 MB
PHASES = ("launch", "spawn", "battle", "gauntlet")      # the default; the carrier also has "census"
ALL_PHASES = PHASES + ("census",)
MARKER = re.compile(r"SMOKEHARNESS_BOOT_TEST: (PASS|FAIL)\b\s*[-—:]*\s*(.*)")
TITLE_SOUND = re.compile(r"Sound engine started")
TITLE_ATLAS = re.compile(r"Created: \d+x\d+x\d+ minecraft:textures/atlas/")
FATAL_LOG = re.compile(r"Missing or unsupported mandatory dependencies|ModLoadingException|LoadingFailedException|"
                       r"Duplicate mod|---- Minecraft Crash Report ----|ResolutionException|"
                       r"Failed to find a primary monitor|UnsatisfiedLinkError")
STDERR_BAD = re.compile(r"\b[\w.$]*(?:Exception|Error)\b(?!\s*=)")
BRANCH = re.compile(r"neoforge-(\d+\.\d+(?:\.\d+)?)$")


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


IP = _load("install_port", "install-port.py")          # jar readers, registry lookup, the NeoForge installer path


# ── platform ──────────────────────────────────────────────────────────────────────────────────────────────────────
class Plat:
    """Mojang's names for an OS/arch (version JSON rules) and the Launcher's runtime folder for it."""
    def __init__(self, os_name, arch, sep=None):
        self.os_name, self.arch = os_name, arch
        self.sep = sep or (";" if os_name == "windows" else ":")
        self.runtime_dir = {("osx", "arm64"): "mac-os-arm64", ("osx", "x86_64"): "mac-os",
                            ("linux", "x86_64"): "linux", ("linux", "x86"): "linux-i386",
                            ("windows", "x86_64"): "windows-x64", ("windows", "arm64"): "windows-arm64",
                            ("windows", "x86"): "windows-x86"}.get((os_name, arch), "linux")

    @staticmethod
    def host():
        s = platform.system()
        m = platform.machine().lower()
        arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x86_64", "amd64": "x86_64", "i386": "x86",
                "i686": "x86", "x86": "x86"}.get(m, m)
        return Plat({"Darwin": "osx", "Windows": "windows"}.get(s, "linux"), arch, os.pathsep)


def allowed(rules, plat):
    """Mojang's rule evaluation: the last matching rule wins; a rule naming a launcher feature (demo, resolution,
    quick play) never matches -- accepting one emits a bare `--width` and the game dies parsing its own arguments."""
    if not rules:
        return True
    ok = False
    for r in rules:
        if r.get("features"):
            continue
        o = r.get("os") or {}
        if "name" in o and o["name"] != plat.os_name:
            continue
        if "arch" in o and o["arch"] != plat.arch:
            continue
        if "version" in o:
            continue
        ok = r.get("action") == "allow"
    return ok


# ── downloads ─────────────────────────────────────────────────────────────────────────────────────────────────────
def _token():
    for k in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(k):
            return os.environ[k]
    if shutil.which("gh"):
        try:
            r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=30)
            return r.stdout.strip() or None if r.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None
    return None


def http_get(url, headers=None, timeout=180):
    """Bytes at url. Falls back to the system curl when python.org's macOS Python has no CA certificates."""
    h = {"User-Agent": UA, **(headers or {})}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
            return r.read()
    except urllib.error.URLError as e:
        if "CERTIFICATE_VERIFY_FAILED" not in str(getattr(e, "reason", e)) or not shutil.which("curl"):
            raise
        cmd = ["curl", "-fsSL", "--max-time", str(timeout)]
        for k, v in h.items():
            cmd += ["-H", f"{k}: {v}"]
        r = subprocess.run(cmd + [url], capture_output=True)
        if r.returncode:
            raise urllib.error.URLError(f"curl exit {r.returncode}")
        return r.stdout


def sha(data, algo="sha256"):
    return hashlib.new(algo, data).hexdigest()


def sha_file(p, algo="sha1"):
    h = hashlib.new(algo)
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def download_to(url, dest, sha1=None, sha256=None, get=None):
    """Into place only once verified; a partial or mismatched file is never left at dest."""
    dest = pathlib.Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = (get or http_get)(url)
    if sha1 and sha(data, "sha1") != sha1:
        raise ValueError(f"sha1 mismatch for {url}")
    if sha256 and sha(data) != sha256:
        raise ValueError(f"sha256 mismatch for {url}")
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    return len(data)


class Sources:
    """Everything plan() reads from the network or the disk, so the self-check can swap in fixtures."""
    def __init__(self, cache_dir):
        self.cache = pathlib.Path(cache_dir)
        self._tok, self._mem = None, {}

    def _api(self, path):
        if self._tok is None:
            self._tok = _token() or ""
        h = {"Accept": "application/vnd.github+json"}
        if self._tok:
            h["Authorization"] = f"Bearer {self._tok}"
        return json.loads(http_get("https://api.github.com/" + path.lstrip("/"), h).decode("utf-8"))

    def _pages(self, path):
        out, page = [], 1
        while True:
            got = self._api(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")
            out += got
            if len(got) < 100:
                return out
            page += 1

    def branches(self, repo):
        return [b["name"] for b in self._pages(f"repos/{repo}/branches")]

    def releases(self, repo):
        if ("rel", repo) not in self._mem:
            self._mem[("rel", repo)] = self._pages(f"repos/{repo}/releases")
        return self._mem[("rel", repo)]

    def file(self, repo, branch, path):
        """A file on a branch, or None when it is not there."""
        try:
            return http_get(f"https://raw.githubusercontent.com/{repo}/{urllib.parse.quote(branch)}/{path}").decode(
                "utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    def workflows(self, repo, branch):
        """The text of every workflow file on a branch."""
        try:
            ls = self._api(f"repos/{repo}/contents/.github/workflows?ref={urllib.parse.quote(branch)}")
        except urllib.error.HTTPError:
            return []
        return [t for t in (self.file(repo, branch, f".github/workflows/{e['name']}") for e in ls
                            if e.get("name", "").endswith((".yml", ".yaml"))) if t]

    def get(self, url):
        """Bytes at url, cached on disk under the work folder (a plan's jars are its install's jars)."""
        self.cache.mkdir(parents=True, exist_ok=True)
        p = self.cache / (sha(url.encode("utf-8"), "sha1")[:16] + "-" + re.sub(r"[^\w.+-]", "_", url.rsplit("/", 1)[-1])[-60:])
        if p.is_file():
            return p.read_bytes()
        data = http_get(url)
        p.write_bytes(data)
        return data

    def json(self, url):
        if url not in self._mem:
            self._mem[url] = json.loads(http_get(url).decode("utf-8"))
        return self._mem[url]

    def registry(self, modid, mc):
        return IP.registry_lookup(modid, mc, self.get)

    def free_bytes(self, path):
        p = pathlib.Path(path)
        while not p.exists():
            p = p.parent
        return shutil.disk_usage(str(p)).free


# ── fork releases and pins ────────────────────────────────────────────────────────────────────────────────────────
def props(text):
    out = {}
    for line in (text or "").splitlines():
        m = re.match(r"\s*([\w.]+)\s*=\s*(.*?)\s*$", line)
        if m and not line.lstrip().startswith("#"):
            out[m.group(1)] = m.group(2)
    return out


def pick_release(releases, mc, mod_version=None, prerelease=False):
    """The newest release of a version branch's tag series (`*-mc<mc>` or `*-mc<mc>-rN`) that has a jar; of the
    branch's own mod_version series when it has one there. -> (release, why-not)."""
    tail = re.compile(r"-mc" + re.escape(mc) + r"(?:-r\d+)?$")
    cands = [r for r in releases if not r.get("draft") and tail.search(r.get("tag_name", ""))
             and any(a["name"].endswith(".jar") for a in r.get("assets", []))]
    full = [r for r in cands if not r.get("prerelease")]
    if not prerelease:
        if not full:
            return None, (f"no full release for {mc} ({len(cands)} pre-release(s): pass --prerelease)" if cands
                          else f"no release for {mc}")
        cands = full
    if not cands:
        return None, f"no release for {mc}"
    if mod_version:
        own = [r for r in cands if f"-{mod_version}-mc{mc}" in r["tag_name"]]
        cands = own or cands
    return max(cands, key=lambda r: (r.get("published_at") or r.get("created_at") or "", r["tag_name"])), None


def pick_asset(assets, mc, variant="mr"):
    """The release jar for this Minecraft version and platform variant. A release may carry one jar, a jar per
    variant (`_mr`, `_cf`, `_dev`, ...), or also other versions' jars (`..._mr-mc1.21.4.jar`)."""
    jars = [a for a in assets if a["name"].endswith(".jar") and not re.search(r"[-_](sources|javadoc)\.jar$", a["name"])]
    def mc_of(n):
        m = re.search(r"-mc(\d+\.\d+(?:\.\d+)?)\.jar$", n)
        return m.group(1) if m else None
    here = [a for a in jars if mc_of(a["name"]) == mc] or [a for a in jars if mc_of(a["name"]) is None]
    tok = lambda n, v: re.search(r"[-_+]" + v + r"(?=[-_.+])", n)
    tagged = [a for a in here if any(tok(a["name"], v) for v in ("mr", "cf", "dev", "pro"))]
    if not tagged:
        return here[0] if len(here) == 1 else (min(here, key=lambda a: len(a["name"])) if here else None)
    want = [a for a in tagged if tok(a["name"], variant)]
    return want[0] if want else None


PIN = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/releases/download/([^/\"'\s]+)/([\w.+-]+\.jar)")
PIN_SHA = re.compile(r"\b([0-9a-f]{64})\b")
PIN_LEG = re.compile(r"matrix\.variant\s*==\s*'([\w.+-]+)'")


def parse_pins(workflow):
    """The sibling-port jars a fork's workflow pins (port-ci.py --dep-jar / --dep-jar-variant): [{repo, tag, asset,
    url, sha256, leg}] where leg is None for the default pin and the matrix leg (`cf`) for a variant."""
    lines = (workflow or "").splitlines()
    out = []
    for i, line in enumerate(lines):
        if "curl" not in line:
            continue
        m = PIN.search(line)
        if not m:
            continue
        s = next((PIN_SHA.search(x) for x in lines[i + 1:i + 3] if PIN_SHA.search(x)), None)
        leg = next((PIN_LEG.search(x).group(1) for x in reversed(lines[max(0, i - 4):i]) if PIN_LEG.search(x)), None)
        out.append({"repo": m.group(1), "tag": m.group(2), "asset": m.group(3), "url": m.group(0),
                    "sha256": s.group(1) if s else None, "leg": leg})
    return out


def choose_pins(pins, variant):
    """Per pinned repo: the --dep-jar-variant leg named `variant` when the workflow has one, else the default pin."""
    by = {}
    for p in pins:
        if p["leg"] is None:
            by.setdefault(p["repo"], {})["default"] = p
        elif p["leg"] == variant:
            by.setdefault(p["repo"], {})["variant"] = p
    return {r: (d.get("variant") if variant != "mr" and d.get("variant") else d.get("default") or d.get("variant"))
            for r, d in by.items()}


# ── Minecraft files ───────────────────────────────────────────────────────────────────────────────────────────────
def lib_path(lib):
    art = (lib.get("downloads") or {}).get("artifact") or {}
    if art.get("path"):
        return art["path"]
    g, a, v, *c = lib["name"].split(":")
    return "/".join(g.split(".") + [a, v, f"{a}-{v}" + (f"-{c[0]}" if c else "") + ".jar"])


def vanilla_needs(mcdir, mc, src, plat):
    """What the Launcher folder lacks for vanilla `mc`: [(kind, url, rel path, sha1, size)], plus its version JSON.
    Reads only; asset objects are checked by presence and size (they are content-addressed)."""
    mcdir = pathlib.Path(mcdir)
    man = src.json(VERSION_MANIFEST)
    entry = next((v for v in man["versions"] if v["id"] == mc), None)
    if entry is None:
        raise ValueError(f"{mc} is not in Mojang's version manifest")
    vj = mcdir / "versions" / mc / f"{mc}.json"
    meta = src.json(entry["url"])
    need = []
    if not (vj.is_file() and sha_file(vj) == entry.get("sha1")):
        need.append(("version json", entry["url"], f"versions/{mc}/{mc}.json", entry.get("sha1"), 0))
    c = meta["downloads"]["client"]
    cj = mcdir / "versions" / mc / f"{mc}.jar"
    if not (cj.is_file() and cj.stat().st_size == c["size"]):
        need.append(("client jar", c["url"], f"versions/{mc}/{mc}.jar", c["sha1"], c["size"]))
    for lib in meta.get("libraries", []):
        art = (lib.get("downloads") or {}).get("artifact")
        if art and allowed(lib.get("rules"), plat):
            p = mcdir / "libraries" / art["path"]
            if not (p.is_file() and p.stat().st_size == art["size"]):
                need.append(("library", art["url"], "libraries/" + art["path"], art["sha1"], art["size"]))
    ai = meta["assetIndex"]
    ip = mcdir / "assets" / "indexes" / f"{ai['id']}.json"
    if not (ip.is_file() and sha_file(ip) == ai["sha1"]):
        need.append(("asset index", ai["url"], f"assets/indexes/{ai['id']}.json", ai["sha1"], ai["size"]))
    idx = src.json(ai["url"])
    seen = set()
    for o in idx.get("objects", {}).values():
        h = o["hash"]
        if h in seen:
            continue
        seen.add(h)
        p = mcdir / "assets" / "objects" / h[:2] / h
        if not (p.is_file() and p.stat().st_size == o["size"]):
            need.append(("asset object", f"{OBJECTS}/{h[:2]}/{h}", f"assets/objects/{h[:2]}/{h}", h, o["size"]))
    lg = ((meta.get("logging") or {}).get("client") or {}).get("file")
    if lg and not (mcdir / "assets" / "log_configs" / lg["id"]).is_file():
        need.append(("log config", lg["url"], f"assets/log_configs/{lg['id']}", lg["sha1"], lg["size"]))
    return need, meta


def java_exe(mcdir, component, plat):
    base = pathlib.Path(mcdir) / "runtime" / component / plat.runtime_dir / component
    exe = "java.exe" if plat.os_name == "windows" else "java"
    for c in (base / "jre.bundle" / "Contents" / "Home" / "bin" / exe, base / "bin" / exe):
        if c.is_file():
            return c
    return None


def runtime_needs(component, src, plat):
    """The Java runtime's files from Mojang's runtime manifest: [(path, entry)], or [] when it has none for here."""
    allm = src.json(RUNTIME_MANIFEST)
    rel = (allm.get(plat.runtime_dir) or {}).get(component) or []
    if not rel:
        return []
    files = src.json(rel[0]["manifest"]["url"]).get("files", {})
    return sorted(files.items())


def install_runtime(mcdir, component, src, plat, log):
    base = pathlib.Path(mcdir) / "runtime" / component / plat.runtime_dir / component
    files = runtime_needs(component, src, plat)
    if not files:
        raise ValueError(f"Mojang has no {component} runtime for {plat.runtime_dir}")
    for path, e in files:
        if e.get("type") == "directory":
            (base / path).mkdir(parents=True, exist_ok=True)
    n = 0
    for path, e in files:
        if e.get("type") == "file":
            raw = e["downloads"]["raw"]
            d = base / path
            if not (d.is_file() and d.stat().st_size == raw["size"]):
                download_to(raw["url"], d, sha1=raw["sha1"]); n += 1
            if e.get("executable") and plat.os_name != "windows":
                d.chmod(0o755)
    for path, e in files:
        if e.get("type") == "link" and plat.os_name != "windows":
            d = base / path
            if not d.exists() and not d.is_symlink():
                d.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(e["target"], d)
    log(f"  Java runtime {component}: {n} file(s) fetched into {base}")


# ── the plan ──────────────────────────────────────────────────────────────────────────────────────────────────────
def mb(n):
    return f"{n / 2**20:,.1f} MB" if n < 2**30 else f"{n / 2**30:,.2f} GB"


def java_major_default(mc):
    return 25 if mc.split(".")[0] not in ("1",) else 21


def _kv(items):
    """[MC=]VALUE flags -> {mc or '*': [values]}"""
    out = {}
    for x in items or []:
        m = re.match(r"(\d+\.\d+(?:\.\d+)?)=(.+)$", x)
        k, v = (m.group(1), m.group(2)) if m else ("*", x)
        out.setdefault(k, []).append(v)
    return out


def jar_info(data):
    return {"modids": IP.jar_modids(data), "bundled": sorted(IP.jar_bundled(data)),
            "requires": sorted(IP.jar_required(data)), "neoforge_floor": IP.jar_neoforge_floor(data)}


def carrier_for(mc, a, src):
    """The Gate C carrier jar for mc: {name, url|path, sha256, size, source} or (None, why)."""
    jars = _kv(a.carrier_jar)
    if jars:
        p = (jars.get(mc) or jars.get("*") or [None])[0]
        if not p:
            return None, f"no --carrier-jar for {mc}"
        data = pathlib.Path(p).expanduser().read_bytes()
        return {"name": pathlib.Path(p).name, "path": str(pathlib.Path(p).expanduser().resolve()), "sha256": sha(data),
                "size": len(data), "source": "local jar"}, None
    if a.carrier_dir:
        d = pathlib.Path(a.carrier_dir).expanduser()
        j = d / f"gatec-carrier-{mc}.jar"
        if not j.is_file():
            return None, f"{j} is not there"
        data = j.read_bytes()
        sums = d / "SHA256SUMS"
        if sums.is_file():
            want = parse_sums(sums.read_text(encoding="utf-8")).get(j.name)
            if want != sha(data):
                return None, f"{j.name}: sha256 does not match {sums}"
        return {"name": j.name, "path": str(j.resolve()), "sha256": sha(data), "size": len(data),
                "source": f"folder {d}"}, None
    repo = a.carrier_repo
    tag = a.carrier_release
    if not tag:
        tags = [r["tag_name"] for r in src.releases(repo) if re.fullmatch(r"gatec-carrier-v\d+", r.get("tag_name", ""))
                and not r.get("draft")]
        if not tags:
            return None, f"{repo} has no gatec-carrier-v* release yet (pass --carrier-dir or --carrier-jar)"
        tag = max(tags, key=lambda t: int(t.rsplit("v", 1)[1]))
    rel = next((r for r in src.releases(repo) if r.get("tag_name") == tag), None)
    if rel is None:
        return None, f"{repo} has no release {tag}"
    name = f"gatec-carrier-{mc}.jar"
    asset = next((x for x in rel.get("assets", []) if x["name"] == name), None)
    if asset is None:
        return None, f"{tag} has no {name}"
    base = f"https://github.com/{repo}/releases/download/{tag}/"
    want = parse_sums(src.get(base + "SHA256SUMS").decode("utf-8", "replace")).get(name)
    if not want:
        return None, f"{tag}'s SHA256SUMS does not list {name}"
    note = ""
    try:
        for l in src.get(base + "MANIFEST.txt").decode("utf-8", "replace").splitlines():
            t = l.split()          # jar <mc> <neoforge> <java> <file> <sha256> <sources digest>
            if len(t) >= 4 and t[0] == "jar" and t[1] == mc:
                note = f"built for NeoForge {t[2]}, Java {t[3]}"
                if len(t) >= 6 and t[5] != want:
                    return None, f"{tag}: MANIFEST.txt and SHA256SUMS disagree on {name}"
    except Exception:
        pass
    return {"name": name, "url": base + name, "sha256": want, "size": asset.get("size", 0),
            "source": f"{repo} release {tag}", "manifest": note}, None


def parse_sums(text):
    out = {}
    for line in text.splitlines():
        m = re.match(r"([0-9a-f]{64})\s+\*?(\S+)", line.strip())
        if m:
            out[pathlib.PurePosixPath(m.group(2)).name] = m.group(1)
    return out


def build_plan(a, src, plat, log=print):
    forks = [re.sub(r"^https://github\.com/", "", f).strip("/").removesuffix(".git") for f in a.fork]
    mcdir = pathlib.Path(a.minecraft_dir)
    label = a.label
    inst_root = pathlib.Path(a.instances_dir) if a.instances_dir else mcdir / "instances"
    plan = {"label": label, "minecraft_dir": str(mcdir), "instances_dir": str(inst_root), "variant": a.variant,
            "forks": forks, "created": datetime.datetime.now().isoformat(timespec="seconds"),
            "with_carrier": bool(a.with_carrier), "versions": [], "skipped": []}
    vbranches = {}
    for f in forks:
        vbranches[f] = {BRANCH.match(b).group(1) for b in src.branches(f) if BRANCH.match(b)}
    if a.all_versions:
        common = set.intersection(*vbranches.values()) if vbranches else set()
        for f in forks:
            for v in sorted(vbranches[f] - common, key=IP.vkey):
                plan["skipped"].append({"mc": v, "why": f"only {f} has neoforge-{v} ("
                                        + ", ".join(g for g in forks if v not in vbranches[g]) + " has no port there)"})
        mcs = sorted(common, key=IP.vkey)
    else:
        mcs = list(dict.fromkeys(a.mc))
    extra, neo_pin = _kv(a.jar), _kv(a.neoforge)
    for mc in mcs:
        v = {"mc": mc, "mods": [], "problems": [], "notes": [], "modids_under_test": []}
        no = [f for f in forks if mc not in vbranches[f]]
        if no:
            plan["skipped"].append({"mc": mc, "why": ", ".join(no) + f" has no neoforge-{mc} branch"})
            continue
        neos, have = [], {}
        def add(entry, data, under_test=False):
            entry.update(jar_info(data))
            for m in entry["modids"]:
                if m in have:
                    v["problems"].append(f"{entry['name']} and {have[m]['name']} both declare mod id {m}")
            for m in entry["modids"]:
                have.setdefault(m, entry)
            v["mods"].append(entry)
            if under_test:
                v["modids_under_test"] += [m for m in entry["modids"] if m not in v["modids_under_test"]]
        pins_all = {}
        for f in forks:
            br = f"neoforge-{mc}"
            pr = props(src.file(f, br, "gradle.properties"))
            n = pr.get("neo_version") or pr.get("neoforge_version")
            if n:
                neos.append((n, f"{f}@{br}"))
            rel, why = pick_release(src.releases(f), mc, pr.get("mod_version"), a.prerelease)
            if rel is None:
                v["problems"].append(f"{f}: {why}")
                continue
            asset = pick_asset(rel.get("assets", []), mc, a.variant)
            if asset is None:
                v["problems"].append(f"{f}: release {rel['tag_name']} has no {a.variant} jar for {mc}")
                continue
            data = src.get(asset["browser_download_url"])
            dig = (asset.get("digest") or "").removeprefix("sha256:")
            if dig and sha(data) != dig:
                v["problems"].append(f"{f}: {asset['name']} does not match the sha256 GitHub records -- not used")
                continue
            add({"name": asset["name"], "url": asset["browser_download_url"], "sha256": sha(data), "size": len(data),
                 "source": f"{f} release {rel['tag_name']}" + (" (pre-release)" if rel.get("prerelease") else ""),
                 "repo": f}, data, under_test=True)
            for t in src.workflows(f, br):
                for r, p in choose_pins(parse_pins(t), a.variant).items():
                    pins_all.setdefault(r, (p, f))
        for r, (p, by) in pins_all.items():
            if r in forks:
                own = next((m for m in v["mods"] if m.get("repo") == r), None)
                if a.prefer_pins and own:
                    v["mods"].remove(own)
                    for m in own["modids"]:
                        have.pop(m, None)
                elif own:
                    if own["url"] != p["url"]:
                        v["notes"].append(f"{by}'s CI pinned {r} at {p['tag']}/{p['asset']}; installing {r}'s own "
                                          f"{own['source'].split(' release ')[-1]} (--prefer-pins takes the pin)")
                    continue
                else:
                    continue
            data = src.get(p["url"])
            if p["sha256"] and sha(data) != p["sha256"]:
                v["problems"].append(f"{r}: {p['asset']} from {p['tag']} does not match the sha256 {by}'s workflow pins")
                continue
            add({"name": p["asset"], "url": p["url"], "sha256": sha(data), "size": len(data),
                 "source": f"{r} release {p['tag']}, pinned by {by}'s CI", "repo": r}, data, under_test=r in forks)
        for p in (extra.get(mc) or []) + (extra.get("*") or []):
            data = pathlib.Path(p).expanduser().read_bytes()
            add({"name": pathlib.Path(p).name, "path": str(pathlib.Path(p).expanduser().resolve()), "sha256": sha(data),
                 "size": len(data), "source": "local jar"}, data, under_test=True)
        # registry dependencies: what the set requires and nothing in it provides (or bundles), resolved for this mc
        tried = set()
        while True:
            provided = {m for e in v["mods"] for m in e["modids"] + e["bundled"]} | {"minecraft", "neoforge", "java"}
            need = sorted({r for e in v["mods"] for r in e["requires"]} - provided - tried)
            if not need:
                break
            for m in need:
                tried.add(m)
                hit, why = src.registry(m, mc)
                if not hit:
                    v["problems"].append(f"required mod {m}: {why}")
                    continue
                data = src.get(hit["url"])
                add({"name": hit["name"], "url": hit["url"], "sha256": hit["sha256"], "size": len(data),
                     "source": f"{hit['source']} ({hit.get('page', '')}) -- not tested by CI"}, data)
        if neo_pin.get(mc) or neo_pin.get("*"):
            neo = (neo_pin.get(mc) or neo_pin["*"])[0]
        elif neos:
            neo = max((n for n, _ in neos), key=IP.vkey)
            if len({n for n, _ in neos}) > 1:
                v["notes"].append("forks built on different NeoForge (" + "; ".join(f"{w}: {n}" for n, w in neos)
                                  + f") -- using the newest, {neo}")
        else:
            v["problems"].append("no NeoForge version: no neo_version in the branches' gradle.properties "
                                 "(pass --neoforge MC=VERSION)")
            neo = None
        if neo:
            line = neo.rsplit(".", 1)[0]
            for e in v["mods"]:
                fl = e.get("neoforge_floor")
                if fl and fl.startswith(line + ".") and IP.vkey(fl) > IP.vkey(neo):
                    v["notes"].append(f"{e['name']} needs NeoForge {fl} or newer -- raised from {neo}")
                    neo = fl
        v["neoforge"] = neo
        try:
            need, meta = vanilla_needs(mcdir, mc, src, plat)
        except Exception as e:
            v["problems"].append(f"Minecraft {mc}: could not read Mojang's manifests ({type(e).__name__}: {e})")
            need, meta = [], {}
        jv = meta.get("javaVersion") or {}
        v["java_major"] = jv.get("majorVersion") or java_major_default(mc)
        v["java_component"] = jv.get("component") or ("java-runtime-epsilon" if v["java_major"] >= 25
                                                      else "java-runtime-delta")
        v["vanilla_missing"] = {}
        for kind, _u, _p, _s, size in need:
            c = v["vanilla_missing"].setdefault(kind, [0, 0]); c[0] += 1; c[1] += size
        v["vanilla_bytes"] = sum(x[4] for x in need)
        v["java_present"] = bool(java_exe(mcdir, v["java_component"], plat))
        v["java_bytes"] = 0
        if not v["java_present"]:
            try:
                v["java_bytes"] = sum(e["downloads"]["raw"]["size"] for _p, e in runtime_needs(v["java_component"], src, plat)
                                      if e.get("type") == "file")
            except Exception as e:
                v["notes"].append(f"Java runtime size unknown ({type(e).__name__})")
        inst = IP.neoforge_installed(mcdir, neo) if neo else None
        v["neoforge_installed"] = inst
        v["neoforge_bytes"] = 0 if inst else NEOFORGE_EST
        v["instance"] = str(inst_root / f"{label}-{mc}")
        v["carrier"] = None
        if a.with_carrier:
            c, why = carrier_for(mc, a, src)
            if c:
                v["carrier"] = c
            else:
                v["problems"].append(f"Gate C carrier: {why}")
        v["mods_bytes"] = sum(e["size"] for e in v["mods"]) + (v["carrier"]["size"] if v["carrier"] else 0)
        v["total_bytes"] = v["vanilla_bytes"] + v["java_bytes"] + v["neoforge_bytes"] + v["mods_bytes"]
        plan["versions"].append(v)
    total = sum(v["total_bytes"] for v in plan["versions"])
    free = src.free_bytes(mcdir)
    margin = int(a.margin_gb * 2**30)
    plan["totals"] = {"install_bytes": total, "free_bytes": free, "margin_bytes": margin,
                      "disk_ok": free - total >= margin}
    return plan


def render_plan(plan):
    out = [f"real-install plan: {plan['label']} ({', '.join(plan['forks']) or 'local jars'}), variant {plan['variant']}"
           f" -> {plan['instances_dir']}"]
    for s in plan["skipped"]:
        out.append(f"  skipped {s['mc']}: {s['why']}")
    for v in plan["versions"]:
        neo = v["neoforge"] or "?"
        out.append("")
        out.append(f"Minecraft {v['mc']}: NeoForge {neo} ({'installed' if v['neoforge_installed'] else '~' + mb(v['neoforge_bytes']) + ' to install'}), "
                   f"Java {v['java_major']} ({v['java_component']}: {'present' if v['java_present'] else mb(v['java_bytes']) + ' to fetch'})")
        out.append(f"  instance {v['instance']}  (mods under test: {', '.join(v['modids_under_test']) or '-'})")
        for e in v["mods"]:
            out.append(f"  {e['name']:<52} {mb(e['size']):>10}  {', '.join(e['modids'])}  [{e['source']}]")
        if v.get("carrier"):
            c = v["carrier"]
            out.append(f"  {c['name']:<52} {mb(c['size']):>10}  Gate C carrier  [{c['source']}]"
                       + (f"  {c['manifest']}" if c.get("manifest") else ""))
        miss = ", ".join(f"{n} {k if n == 1 else ('libraries' if k == 'library' else k + 's')} ({mb(b)})"
                         for k, (n, b) in v["vanilla_missing"].items())
        out.append(f"  Minecraft files the Launcher folder lacks: {miss or 'none'}")
        out.append(f"  on disk: ~{mb(v['total_bytes'])} (mods {mb(v['mods_bytes'])}, Minecraft {mb(v['vanilla_bytes'])}, "
                   f"Java {mb(v['java_bytes'])}, NeoForge ~{mb(v['neoforge_bytes'])})")
        for n in v["notes"]:
            out.append(f"  note: {n}")
        for p in v["problems"]:
            out.append(f"  PROBLEM: {p}")
    t = plan["totals"]
    out.append("")
    out.append(f"total: ~{mb(t['install_bytes'])} on disk; {mb(t['free_bytes'])} free; keeps {mb(t['margin_bytes'])} "
               f"free: {'yes' if t['disk_ok'] else 'NO -- refused (free space, or --margin-gb)'}")
    return "\n".join(out)


# ── install ───────────────────────────────────────────────────────────────────────────────────────────────────────
def process_lines():
    if platform.system() == "Windows":
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
               "Get-CimInstance Win32_Process | ForEach-Object { \"$($_.ProcessId) $($_.CommandLine)\" }"]
    else:
        cmd = ["ps", "-axo", "pid=,command="]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=60).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return []


LAUNCHER_PROC = re.compile(r"Minecraft Launcher|minecraft-launcher|MinecraftLauncher\.exe", re.I)


def launcher_running(lines=None):
    me = str(os.getpid())
    for line in (process_lines() if lines is None else lines):
        pid, _, cmd = line.strip().partition(" ")
        if pid != me and LAUNCHER_PROC.search(cmd) and "real-install" not in cmd:
            return pid
    return None


def profile_entries(plan):
    out = {}
    for v in plan["versions"]:
        vid = v.get("version_id") or (f"neoforge-{v['neoforge']}" if v.get("neoforge") else None)
        if not vid:
            continue
        out[f"mmvu-test-{plan['label']}-{v['mc']}"] = {
            "name": f"{plan['label']} {v['mc']} (MMVU test)", "type": "custom", "lastVersionId": vid,
            "gameDir": v["instance"], "icon": "Furnace"}
    return out


def add_profiles(mcdir, entries, dry=False, lines=None, log=print):
    """Our profiles into launcher_profiles.json (after a backup); nothing else in it changes. -> problems."""
    f = pathlib.Path(mcdir) / "launcher_profiles.json"
    if not f.is_file():
        return [f"{f} is not there -- open the Minecraft Launcher once, then run this again"]
    pid = launcher_running(lines)
    if pid:
        return [f"the Minecraft Launcher is running (pid {pid}); it rewrites launcher_profiles.json -- close it and "
                "run this again"]
    raw = f.read_text(encoding="utf-8")
    d = json.loads(raw)
    profs = d.setdefault("profiles", {})
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    changed = []
    for k, p in entries.items():
        cur = profs.get(k) or {}
        if any(cur.get(x) != y for x, y in p.items()):
            profs[k] = {**cur, **p, "created": cur.get("created", now), "lastUsed": cur.get("lastUsed", now)}
            changed.append(k)
    if not changed:
        log("  launcher profiles: already there")
        return []
    if dry:
        log("  would add/update launcher profiles: " + ", ".join(changed))
        return []
    bak = f.with_name(f"launcher_profiles.json.mmvu-{datetime.datetime.now():%Y%m%d-%H%M%S}.bak")
    bak.write_text(raw, encoding="utf-8")
    tmp = f.with_name(f.name + ".part")
    tmp.write_text(json.dumps(d, indent=2), encoding="utf-8")
    os.replace(tmp, f)
    log(f"  launcher profiles added/updated: {', '.join(changed)} (backup {bak.name})")
    return []


MARK = ".mmvu-real-install.json"
OPTIONS = "onboardAccessibility:false\npauseOnLostFocus:false\nnarrator:0\ntutorialStep:none\nskipMultiplayerWarning:true\n"


def place_mods(v, src, dry=False, log=print):
    """The instance's mods/ holds exactly this version's set: missing jars copied in (sha256 checked), any other jar
    moved to mods-removed/. A folder this tool did not make is refused. -> problems."""
    inst = pathlib.Path(v["instance"])
    mods = inst / "mods"
    if inst.exists() and not (inst / MARK).is_file() and any(inst.iterdir()):
        return [f"{inst} exists and was not made by real-install -- refusing to touch it"]
    want = list(v["mods"]) + ([v["carrier"]] if v.get("carrier") else [])
    names = {e["name"] for e in want}
    if dry:
        have = {p.name for p in mods.glob("*.jar")} if mods.is_dir() else set()
        log(f"  would place {len(names - have)} jar(s) in {mods}, remove {len(have - names)}")
        return []
    mods.mkdir(parents=True, exist_ok=True)
    (inst / MARK).write_text(json.dumps({"label": v["instance"], "mc": v["mc"], "mods": sorted(names)}, indent=2),
                             encoding="utf-8")
    if not (inst / "options.txt").exists():
        (inst / "options.txt").write_text(OPTIONS, encoding="utf-8")
    for p in sorted(mods.glob("*.jar")):
        if p.name not in names:
            (inst / "mods-removed").mkdir(exist_ok=True)
            os.replace(p, inst / "mods-removed" / p.name)
            log(f"  moved {p.name} out of mods/ (not in this version's set)")
    for e in want:
        d = mods / e["name"]
        if d.is_file() and sha_file(d, "sha256") == e["sha256"]:
            continue
        data = pathlib.Path(e["path"]).read_bytes() if e.get("path") else src.get(e["url"])
        if sha(data) != e["sha256"]:
            return [f"{e['name']}: sha256 does not match the plan -- not installed"]
        tmp = d.with_name(d.name + ".part"); tmp.write_bytes(data); os.replace(tmp, d)
        log(f"  + {e['name']}")
    return []


def ensure_version_jar(mcdir, vid, mc, dry=False):
    """A 21.x NeoForge version (mainClass BootstrapLauncher) launches with versions/<id>/<id>.jar, the copy of the
    client jar the Launcher makes on first launch: its name is on NeoForge's -DignoreList, the vanilla jar's is not
    (with vanilla's name the module system refuses two `minecraft` modules). 26.x has none (a patched client lib)."""
    mcdir = pathlib.Path(mcdir)
    vj = mcdir / "versions" / vid / f"{vid}.json"
    if not vj.is_file():
        return
    d = json.loads(vj.read_text(encoding="utf-8"))
    if d.get("mainClass", "").startswith("net.neoforged.fml.startup"):
        return
    jar = mcdir / "versions" / vid / f"{vid}.jar"
    base = mcdir / "versions" / mc / f"{mc}.jar"
    if not jar.is_file() and base.is_file() and not dry:
        shutil.copyfile(base, jar)


def do_install(plan, a, src, plat, log=print):
    mcdir = pathlib.Path(plan["minecraft_dir"])
    problems, needs, left = [], {}, 0
    keep_mc = getattr(a, "use_installed", False)      # nothing is written to the Launcher folder
    for v in plan["versions"]:                       # what is still to write (a re-run counts only what is missing)
        try:
            needs[v["mc"]] = vanilla_needs(mcdir, v["mc"], src, plat)[0]
        except Exception as e:
            problems.append(f"{v['mc']}: Mojang's manifests: {e}"); continue
        have = {p.name for p in (pathlib.Path(v["instance"]) / "mods").glob("*.jar")}
        left += (0 if keep_mc else sum(x[4] for x in needs[v["mc"]])
                 + (0 if java_exe(mcdir, v["java_component"], plat) else v["java_bytes"])
                 + (0 if v.get("neoforge") and IP.neoforge_installed(mcdir, v["neoforge"]) else NEOFORGE_EST))
        left += (sum(e["size"] for e in v["mods"] + ([v["carrier"]] if v.get("carrier") else []) if e["name"] not in have))
    free, margin = src.free_bytes(mcdir), plan["totals"]["margin_bytes"]
    if free - left < margin:
        return [f"refused: ~{mb(left)} to write, {mb(free)} free, and {mb(margin)} must stay free"]
    for v in plan["versions"]:
        log(f"Minecraft {v['mc']} -> {v['instance']}")
        if v["problems"]:
            problems += [f"{v['mc']}: {p}" for p in v["problems"]]
            log("  skipped: the plan has problems for this version (see `plan`)")
            continue
        if v["mc"] not in needs:
            continue
        need = needs[v["mc"]]
        if need and keep_mc:
            hard = [x for x in need if x[0] in ("client jar", "library")]
            log(f"  --use-installed: {len(need)} Minecraft file(s) missing or older than Mojang's, not fetched")
            if hard:
                problems.append(f"{v['mc']}: --use-installed, but the Launcher folder lacks {len(hard)} client/library "
                                f"file(s), e.g. {hard[0][2]}"); continue
        elif need:
            log(f"  {'would fetch' if a.dry_run else 'fetching'} {len(need)} Minecraft file(s), {mb(sum(x[4] for x in need))}")
            if not a.dry_run:
                bad = []
                for kind, url, rel, s1, _size in need:
                    try:
                        download_to(url, mcdir / rel, sha1=s1)
                    except Exception as e:
                        bad.append(f"{kind} {rel}: {e}")
                if bad:
                    problems += [f"{v['mc']}: {b}" for b in bad[:10]]; continue
        if not java_exe(mcdir, v["java_component"], plat):
            if keep_mc:
                problems.append(f"{v['mc']}: --use-installed, but there is no Java runtime {v['java_component']}"); continue
            if a.dry_run:
                log(f"  would fetch the Java runtime {v['java_component']}")
            else:
                try:
                    install_runtime(mcdir, v["java_component"], src, plat, log)
                except Exception as e:
                    problems.append(f"{v['mc']}: Java runtime {v['java_component']}: {e}"); continue
        have = IP.neoforge_installed(mcdir, v["neoforge"])
        if not have:
            if keep_mc:
                problems.append(f"{v['mc']}: --use-installed, but NeoForge {v['neoforge']} is not installed"); continue
            if a.dry_run:
                log(f"  would install NeoForge {v['neoforge']} with its installer (headless)")
            else:
                pid = launcher_running()
                if pid:
                    problems.append(f"{v['mc']}: NeoForge {v['neoforge']} needs installing and the Minecraft Launcher "
                                    f"is running (pid {pid}) -- close it (the installer edits launcher_profiles.json)")
                    continue
                java = java_exe(mcdir, v["java_component"], plat)
                lp = mcdir / "launcher_profiles.json"
                keep = lp.read_bytes() if lp.is_file() else None
                run = lambda argv, **kw: subprocess.run([str(java)] + list(argv[1:]), **kw)
                try:
                    notes, prob = IP.ensure_neoforge([(v["mc"], v["neoforge"], "plan")], pathlib.Path(v["instance"]) / "mods",
                                                     False, src.get, run=run, mc_dir=mcdir)
                finally:
                    if keep is not None:
                        lp.write_bytes(keep)     # the installer's own "NeoForge" profile edit is undone
                for n in notes:
                    log(f"  {n}")
                if prob:
                    problems += [f"{v['mc']}: {p}" for p in prob]; continue
                have = IP.neoforge_installed(mcdir, v["neoforge"])
        else:
            log(f"  NeoForge {have} is installed")
        v["version_id"] = f"neoforge-{have or v['neoforge']}"
        ensure_version_jar(mcdir, v["version_id"], v["mc"], a.dry_run or keep_mc)
        problems += [f"{v['mc']}: {p}" for p in place_mods(v, src, a.dry_run, log)]
    if a.profiles and not problems:
        problems += add_profiles(mcdir, profile_entries(plan), a.dry_run, log=log)
    elif a.profiles:
        log("launcher profiles not added: fix the problems above first")
    return problems


# ── launch ────────────────────────────────────────────────────────────────────────────────────────────────────────
def load_chain(mcdir, vid):
    chain, name = [], vid
    while name:
        f = pathlib.Path(mcdir) / "versions" / name / f"{name}.json"
        if not f.is_file():
            raise FileNotFoundError(f"missing version JSON {f} (run install)")
        d = json.loads(f.read_text(encoding="utf-8"))
        chain.append(d)
        name = d.get("inheritsFrom")
    return chain


def build_launch(mcdir, vid, instance, plat, java=None, props=None, xmx="4G", username="Tester"):
    """The command the Launcher builds for version `vid` with game directory `instance`. -> (argv, missing libraries)."""
    mcdir, instance = pathlib.Path(mcdir), pathlib.Path(instance)
    chain = load_chain(mcdir, vid)
    base = chain[-1]
    libdir = mcdir / "libraries"
    cp, seen, missing = [], set(), []
    for d in chain:                                     # the child's libraries first
        for lib in d.get("libraries", []):
            if not allowed(lib.get("rules"), plat):
                continue
            parts = lib["name"].split(":")
            key = ":".join(parts[:2] + parts[3:4])      # the classifier is part of a library's identity (natives)
            if key in seen:
                continue
            seen.add(key)
            p = libdir / lib_path(lib)
            (cp if p.is_file() else missing).append(str(p))
    main = chain[0].get("mainClass") or base.get("mainClass")
    vjar = mcdir / "versions" / vid / f"{vid}.jar"
    if vjar.is_file():
        cp.append(str(vjar))
    elif len(chain) == 1:
        missing.append(str(vjar))
    elif not main.startswith("net.neoforged.fml.startup"):
        missing.append(str(vjar) + " (install copies the client jar there, as the Launcher does)")
    natives = instance / "natives"
    rep = {"library_directory": str(libdir), "classpath_separator": plat.sep, "version_name": vid,
           "natives_directory": str(natives), "launcher_name": "mmvu-real-install", "launcher_version": "1",
           "classpath": plat.sep.join(cp), "auth_player_name": username, "version_type": "release",
           "game_directory": str(instance), "assets_root": str(mcdir / "assets"),
           "assets_index_name": (base.get("assetIndex") or {}).get("id") or base.get("assets", ""),
           "auth_uuid": "0" * 32, "auth_access_token": "0", "clientid": "0", "auth_xuid": "0", "user_type": "legacy"}
    sub = lambda s: re.sub(r"\$\{(\w+)\}", lambda m: rep.get(m.group(1), m.group(0)), s)
    jvm, game = [], []
    for d in reversed(chain):                           # the parent's arguments, then the child's
        for sect, out in (("jvm", jvm), ("game", game)):
            for x in (d.get("arguments") or {}).get(sect, []):
                if isinstance(x, str):
                    out.append(sub(x))
                elif allowed(x.get("rules"), plat):
                    val = x.get("value")
                    out += [sub(y) for y in (val if isinstance(val, list) else [val])]
        if d.get("minecraftArguments") and not (d.get("arguments") or {}).get("game"):
            game += [sub(y) for y in d["minecraftArguments"].split()]
    head = [f"-Xmx{xmx}"]
    if plat.os_name == "osx" and "-XstartOnFirstThread" not in jvm:
        head.append("-XstartOnFirstThread")
    if java is None:
        comp = next(((d.get("javaVersion") or {}).get("component") for d in chain if d.get("javaVersion")), None) \
            or "java-runtime-delta"
        java = java_exe(mcdir, comp, plat) or comp
    extra = [f"-D{k}={v}" for k, v in (props or {}).items()]
    return [str(java)] + head + jvm + extra + [main] + game, missing


def loaded_mods(text):
    """Mod ids the log says are loaded: FML's "Mod List" block and the resource manager's mod/<id> packs."""
    out = set()
    blk = re.search(r"Mod List:\s*\n(.*?)(?:\n\[|\Z)", text, re.S)
    if blk:
        out |= set(re.findall(r"\(([a-z][a-z0-9_.-]*)\)\s*$", blk.group(1), re.M))
    for m in re.finditer(r"Reloading ResourceManager: (.*)", text):
        out |= set(re.findall(r"\bmod/([a-z][a-z0-9_.-]*)", m.group(1)))
    return out


def marker(text):
    """The carrier's verdict: ('PASS'|'FAIL', detail) from its last SMOKEHARNESS_BOOT_TEST: PASS/FAIL line, or None."""
    hits = [m for m in MARKER.finditer(text)]
    return (hits[-1].group(1), hits[-1].group(2).strip()) if hits else None


def judge_boot(log_text, stderr_text, crashed, modids):
    """-> (verdict, detail) for a boot test from what the run left behind."""
    if crashed:
        return "FAIL", "crash report: " + crashed
    bad = [l.strip() for l in stderr_text.splitlines() if STDERR_BAD.search(l)]
    if bad:
        return "FAIL", "stderr: " + bad[0][:300]
    f = FATAL_LOG.search(log_text)
    if f:
        line = next((l for l in log_text.splitlines() if f.group(0) in l), f.group(0))
        return "FAIL", line.strip()[:300]
    if not (TITLE_SOUND.search(log_text) and TITLE_ATLAS.search(log_text)):
        return "TIMEOUT", "the title screen was not reached"
    lost = [m for m in modids if m not in loaded_mods(log_text)]
    if lost:
        return "FAIL", "not in the loaded mod list: " + ", ".join(lost)
    return "PASS", "title screen reached; loaded " + ", ".join(modids)


def native_crash(text):
    """The JVM's own fatal-error banner (it goes to stdout, and the Launcher shows nothing): its problematic frame."""
    m = re.search(r"# Problematic frame:\s*\n#\s*(.+)", text)
    if not m:
        return None
    gl = re.search(r"glfw|CF_IS_OBJC", text, re.I)
    return f"the JVM crashed in native code: {m.group(1).strip()}" + (
        " -- in GLFW: no awake, unlocked display? (macOS needs a display it can open a window on)" if gl else "")


def judge_phase(log_text, stderr_text, crashed):
    if crashed:
        return "FAIL", "crash report: " + crashed
    m = marker(log_text)
    if m:
        return m[0], m[1][:300]
    bad = [l.strip() for l in stderr_text.splitlines() if STDERR_BAD.search(l)]
    if bad:
        return "FAIL", "stderr: " + bad[0][:300]
    f = FATAL_LOG.search(log_text)
    if f:
        return "FAIL", f.group(0)
    return "TIMEOUT", "no SMOKEHARNESS_BOOT_TEST verdict"


def _read(p):
    try:
        return pathlib.Path(p).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def run_once(argv, instance, outdir, timeout, done, plat, env=None):
    """Launch, watch stdout + logs/latest.log every second until done(text) or the timeout, then stop the game.
    -> (log text, stderr text, crash report name or None, seconds, exit code)."""
    instance, outdir = pathlib.Path(instance), pathlib.Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (instance / "natives").mkdir(parents=True, exist_ok=True)
    crash_dir = instance / "crash-reports"
    before = {p.name for p in crash_dir.glob("*")} if crash_dir.is_dir() else set()
    latest = instance / "logs" / "latest.log"
    if latest.is_file():
        latest.unlink()
    env = dict(env or os.environ)
    if plat.os_name == "linux" and not env.get("DISPLAY"):
        if not shutil.which("xvfb-run"):
            raise RuntimeError("headless Linux needs Xvfb (apt-get install -y xvfb mesa-utils libgl1-mesa-dri)")
        env["LIBGL_ALWAYS_SOFTWARE"] = "1"
        env.setdefault("SDL_VIDEO_FORCE_EGL", "1")     # 26.3+ opens its window through SDL: Xvfb's GLX has no sRGB visual
        argv = ["xvfb-run", "-a", "-s", "-screen 0 1280x720x24"] + argv
    (outdir / "command.txt").write_text(subprocess.list2cmdline(argv) if plat.os_name == "windows"
                                        else " ".join(_q(x) for x in argv), encoding="utf-8")
    t0 = time.time()
    with open(outdir / "stdout.log", "wb") as so, open(outdir / "stderr.log", "wb") as se:
        kw = {"start_new_session": True} if os.name != "nt" else {}
        awake = None
        if plat.os_name == "osx" and shutil.which("caffeinate"):
            # WAKE the display first: GLFW on a sleeping display dies in glfwGetVideoModes (a SIGSEGV in
            # CoreFoundation, no Java exception, no crash report) -- it reads like a broken mod and is not one.
            subprocess.run(["caffeinate", "-u", "-t", "2"], check=False)
        proc = subprocess.Popen(argv, cwd=str(instance), stdout=so, stderr=se, env=env, **kw)
        if plat.os_name == "osx" and shutil.which("caffeinate"):
            awake = subprocess.Popen(["caffeinate", "-d", "-i", "-w", str(proc.pid)])  # keeps it awake for this run
        grace = None
        while proc.poll() is None:
            time.sleep(1)
            text = _read(outdir / "stdout.log") + "\n" + _read(latest)
            if grace is None and (done(text) or (crash_dir.is_dir() and {p.name for p in crash_dir.glob("*")} - before)):
                grace = time.time() + 5          # a crash right after the verdict still counts
            if (grace and time.time() > grace) or time.time() - t0 > timeout:
                break
        stop(proc)
        if awake is not None and awake.poll() is None:
            awake.terminate()
            awake.wait(10)
    secs = round(time.time() - t0)
    if latest.is_file():
        shutil.copyfile(latest, outdir / "latest.log")
    after = {p.name for p in crash_dir.glob("*")} if crash_dir.is_dir() else set()
    new = sorted(after - before)
    for n in new:
        shutil.copyfile(crash_dir / n, outdir / n)
    text = _read(outdir / "stdout.log") + "\n" + _read(outdir / "latest.log")
    return text, _read(outdir / "stderr.log"), (new[0] if new else None), secs, proc.returncode


def _q(s):
    return s if re.fullmatch(r"[\w@%+=:,./-]+", s) else "'" + s.replace("'", "'\"'\"'") + "'"


def stop(proc):
    if proc.poll() is not None:
        return
    try:
        if os.name != "nt":
            import signal
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
        proc.wait(20)
    except (subprocess.TimeoutExpired, OSError):
        try:
            if os.name != "nt":
                import signal
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except OSError:
            pass
        proc.wait(10)


def client_running(instance, lines=None):
    me = str(os.getpid())
    for line in (process_lines() if lines is None else lines):
        pid, _, cmd = line.strip().partition(" ")
        if pid != me and ("--gameDir" in cmd and str(instance) in cmd):
            return pid
    return None


def do_test(plan, a, plat, log=print):
    mcdir = pathlib.Path(plan["minecraft_dir"])
    phases = [] if a.boot_only else [p.strip() for p in a.phases.split(",") if p.strip()]
    bad = [p for p in phases if p not in ALL_PHASES]
    if bad:
        raise SystemExit(f"unknown phase(s) {bad}: choose from {', '.join(ALL_PHASES)}")
    rep = pathlib.Path(a.report) if a.report else pathlib.Path(a.work) / "reports" / f"{datetime.datetime.now():%Y%m%d-%H%M%S}"
    rows = []
    for v in plan["versions"]:
        inst = pathlib.Path(v["instance"])
        if not (inst / MARK).is_file():
            rows.append(row(v, "boot", "FAIL", "the instance is not installed (run install)", 0, None)); continue
        vid = v.get("version_id") or IP.neoforge_installed(mcdir, v["neoforge"] or "0.0.0")
        vid = vid if (vid or "").startswith("neoforge-") else f"neoforge-{vid}" if vid else None
        if not vid:
            rows.append(row(v, "boot", "FAIL", f"NeoForge {v['neoforge']} is not installed (run install)", 0, None)); continue
        pid = client_running(inst)
        if pid:
            rows.append(row(v, "boot", "FAIL", f"a client is already running on this instance (pid {pid})", 0, None)); continue
        mods = v["modids_under_test"]
        java = java_exe(mcdir, v["java_component"], plat)
        if not java:
            rows.append(row(v, "boot", "FAIL", f"no Java {v['java_component']} in {mcdir / 'runtime'} (run install)", 0, None))
            continue
        runs = [("boot", {}, lambda t: bool(TITLE_SOUND.search(t) and TITLE_ATLAS.search(t)) or bool(FATAL_LOG.search(t)),
                 a.timeout)]
        carrier = v.get("carrier") and (inst / "mods" / v["carrier"]["name"]).is_file()
        if phases and not carrier:
            log(f"Minecraft {v['mc']}: no Gate C carrier in the instance -- boot test only (install --with-carrier)")
        for ph in (phases if carrier else []):
            runs.append((ph, {"smokeharness.boottest": "true", "smokeharness.testmode": ph,
                              "smokeharness.namespaces": ",".join(mods)},
                         lambda t: bool(marker(t)) or bool(FATAL_LOG.search(t)), a.phase_timeout))
        for name, prps, done, tmo in runs:
            argv, missing = build_launch(mcdir, vid, inst, plat, java, prps, a.xmx)
            out = rep / v["mc"] / name
            if missing:
                rows.append(row(v, name, "FAIL", f"{len(missing)} missing file(s), e.g. {missing[0]} (run install)", 0, out))
                break
            log(f"Minecraft {v['mc']} [{vid}] {name}: launching (timeout {tmo}s)", flush=True)
            text, err, crash, secs, rc = run_once(argv, inst, out, tmo, done, plat)
            verdict, detail = judge_boot(text, err, crash, mods) if name == "boot" else judge_phase(text, err, crash)
            if verdict == "TIMEOUT" and rc not in (None, 0) and secs < tmo:
                verdict, detail = "FAIL", f"the game exited ({rc}) before a verdict; see {out / 'stderr.log'}"
                detail = native_crash(err + "\n" + text) or detail
            rows.append(row(v, name, verdict, detail, secs, out))
            log(f"  {verdict}  {detail}")
            if name == "boot" and verdict != "PASS":
                break                                   # the phases cannot pass on a client that does not boot
    write_report(rep, plan, rows)
    log("")
    log(table(rows))
    log(f"\nreport: {rep / 'report.md'}")
    return rows


def row(v, phase, verdict, detail, secs, logs):
    return {"mc": v["mc"], "neoforge": v.get("neoforge"), "phase": phase, "verdict": verdict, "detail": detail,
            "seconds": secs, "logs": str(logs) if logs else None}


def table(rows):
    w = max([len(r["phase"]) for r in rows] + [5])
    out = [f"{'Minecraft':<10} {'NeoForge':<14} {'phase':<{w}} {'result':<8} detail"]
    for r in rows:
        out.append(f"{r['mc']:<10} {str(r['neoforge']):<14} {r['phase']:<{w}} {r['verdict']:<8} {r['detail']}")
    return "\n".join(out)


def write_report(rep, plan, rows):
    rep = pathlib.Path(rep)
    rep.mkdir(parents=True, exist_ok=True)
    ok = bool(rows) and all(r["verdict"] == "PASS" for r in rows)
    (rep / "report.json").write_text(json.dumps({"label": plan["label"], "forks": plan["forks"],
                                                 "variant": plan["variant"], "passed": ok, "results": rows}, indent=2),
                                     encoding="utf-8")
    md = [f"# Real-install test: {plan['label']}", "",
          f"Forks: {', '.join(plan['forks']) or 'local jars'}; variant {plan['variant']}; "
          f"result: **{'PASS' if ok else 'FAIL'}**", "",
          "| Minecraft | NeoForge | Phase | Result | Seconds | Detail |", "|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['mc']} | {r['neoforge']} | {r['phase']} | {r['verdict']} | {r['seconds']} | "
                  + r["detail"].replace("|", "\\|") + " |")
    for v in plan["versions"]:
        md += ["", f"## {v['mc']}", ""] + [f"- {e['name']} ({', '.join(e['modids'])}) -- {e['source']}" for e in v["mods"]]
        if v.get("carrier"):
            md.append(f"- {v['carrier']['name']} (Gate C carrier) -- {v['carrier']['source']}")
    (rep / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return ok


# ── self-check ────────────────────────────────────────────────────────────────────────────────────────────────────
def _jar(modid, requires=(), floor="21.1.0", bundled=None):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        deps = "".join(f'[[dependencies.{modid}]]\nmodId="{r}"\ntype="required"\nversionRange="[1,)"\n' for r in requires)
        deps += f'[[dependencies.{modid}]]\nmodId="neoforge"\ntype="required"\nversionRange="[{floor},)"\n'
        z.writestr("META-INF/neoforge.mods.toml", f'modLoader="javafml"\n[[mods]]\nmodId="{modid}"\nversion="1"\n{deps}')
        if bundled:
            z.writestr("META-INF/jarjar/inner.jar", bundled)
    return b.getvalue()


class FakeSources(Sources):
    def __init__(self, tmp, branches, releases, files, urls, metas, registry, free):
        super().__init__(pathlib.Path(tmp) / "cache")
        self.b, self.r, self.f, self.u, self.m, self.reg, self.free = branches, releases, files, urls, metas, registry, free
        self.fetched = []

    def branches(self, repo): return self.b[repo]
    def releases(self, repo): return self.r.get(repo, [])
    def file(self, repo, branch, path): return self.f.get((repo, branch, path))
    def workflows(self, repo, branch):
        t = self.f.get((repo, branch, ".github/workflows/port-ci.yml"))
        return [t] if t else []
    def get(self, url):
        self.fetched.append(url)
        if url not in self.u:
            raise urllib.error.URLError(f"no fixture for {url}")
        return self.u[url]
    def json(self, url): return self.m[url]
    def registry(self, modid, mc): return self.reg(modid, mc)
    def free_bytes(self, path): return self.free


def self_check():
    failed = []
    def chk(cond, what):
        if not cond:
            failed.append(what)
    osx = Plat("osx", "arm64", ":")
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="real-install-"))
    try:
        # ── launch command: inheritsFrom, rules, the NeoForge jar not vanilla's, substitutions ──
        mc = tmp / "mc"
        def wj(vid, d):
            (mc / "versions" / vid).mkdir(parents=True, exist_ok=True)
            (mc / "versions" / vid / f"{vid}.json").write_text(json.dumps(d), encoding="utf-8")
        def lib(path):
            p = mc / "libraries" / path; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b"x")
        wj("1.21.1", {"id": "1.21.1", "mainClass": "net.minecraft.client.main.Main", "assets": "17",
                      "assetIndex": {"id": "17"}, "javaVersion": {"component": "java-runtime-delta", "majorVersion": 21},
                      "libraries": [
                          {"name": "org.ex:objc:1.1", "downloads": {"artifact": {"path": "org/ex/objc/1.1/objc-1.1.jar"}},
                           "rules": [{"action": "allow", "os": {"name": "osx"}}]},
                          {"name": "org.ex:epoll:1:linux", "downloads": {"artifact": {"path": "org/ex/epoll/1/epoll-1-linux.jar"}},
                           "rules": [{"action": "allow", "os": {"name": "linux"}}]},
                          {"name": "org.ex:asm:9.0"},
                          {"name": "org.ex:lwjgl:3:natives-macos-arm64", "rules": [
                              {"action": "allow"}, {"action": "disallow", "os": {"name": "osx", "arch": "x86_64"}}]},
                          {"name": "org.ex:lwjgl:3"}],
                      "arguments": {"jvm": [{"rules": [{"action": "allow", "os": {"name": "osx"}}],
                                             "value": ["-XstartOnFirstThread"]},
                                            {"rules": [{"action": "allow", "os": {"arch": "x86"}}], "value": "-Xss1M"},
                                            "-Djava.library.path=${natives_directory}", "-cp", "${classpath}"],
                                    "game": ["--username", "${auth_player_name}", "--version", "${version_name}",
                                             "--gameDir", "${game_directory}", "--assetsDir", "${assets_root}",
                                             "--assetIndex", "${assets_index_name}", "--uuid", "${auth_uuid}",
                                             "--userType", "${user_type}",
                                             {"rules": [{"action": "allow", "features": {"has_custom_resolution": True}}],
                                              "value": ["--width", "${resolution_width}"]}]}})
        wj("neoforge-21.1.9", {"id": "neoforge-21.1.9", "inheritsFrom": "1.21.1",
                               "mainClass": "cpw.mods.bootstraplauncher.BootstrapLauncher",
                               "libraries": [{"name": "org.ex:asm:9.8"}, {"name": "net.neoforged:fml:4"}],
                               "arguments": {"jvm": ["-DignoreList=client-extra,${version_name}.jar",
                                                     "-p", "${library_directory}/net/neoforged/fml/4/fml-4.jar"],
                                             "game": ["--launchTarget", "forgeclient"]}})
        for p in ("org/ex/objc/1.1/objc-1.1.jar", "org/ex/epoll/1/epoll-1-linux.jar", "org/ex/asm/9.8/asm-9.8.jar",
                  "org/ex/asm/9.0/asm-9.0.jar", "org/ex/lwjgl/3/lwjgl-3-natives-macos-arm64.jar",
                  "org/ex/lwjgl/3/lwjgl-3.jar", "net/neoforged/fml/4/fml-4.jar"):
            lib(p)
        (mc / "versions/1.21.1/1.21.1.jar").write_bytes(b"v")
        inst = tmp / "inst"
        argv, missing = build_launch(mc, "neoforge-21.1.9", inst, osx, java="JAVA", props={"smokeharness.testmode": "spawn"})
        cp = argv[argv.index("-cp") + 1].split(":")
        chk(argv[0] == "JAVA" and argv[1] == "-Xmx4G", "java + heap first")
        chk(argv.count("-XstartOnFirstThread") == 1, "-XstartOnFirstThread once on macOS (from the rule, not doubled)")
        chk("-Xss1M" not in argv and "--width" not in argv, "an x86 arch rule and a feature rule do not apply")
        chk(any(c.endswith("asm-9.8.jar") for c in cp) and not any(c.endswith("asm-9.0.jar") for c in cp),
            "the child's library wins over the parent's of the same group:artifact")
        chk(any(c.endswith("natives-macos-arm64.jar") for c in cp) and any(c.endswith("lwjgl-3.jar") for c in cp),
            "the classifier keeps a natives jar beside its main jar")
        chk(not any("epoll" in c for c in cp) and any("objc" in c for c in cp), "os rules: linux lib out, osx lib in")
        chk(missing and "neoforge-21.1.9.jar" in missing[0] and not any(c.endswith("1.21.1.jar") for c in cp),
            "a 21.x NeoForge version without its own jar is reported missing, and vanilla's jar is never used")
        ensure_version_jar(mc, "neoforge-21.1.9", "1.21.1")
        argv, missing = build_launch(mc, "neoforge-21.1.9", inst, osx, java="JAVA", props={"smokeharness.testmode": "spawn"})
        cp = argv[argv.index("-cp") + 1].split(":")
        chk(not missing and cp[-1].endswith("neoforge-21.1.9.jar"), "install copies the client jar to the NeoForge id")
        mi = argv.index("cpw.mods.bootstraplauncher.BootstrapLauncher")
        chk("-Dsmokeharness.testmode=spawn" in argv[:mi], "the carrier's properties go before the main class")
        chk(argv[mi + 1:] == ["--username", "Tester", "--version", "neoforge-21.1.9", "--gameDir", str(inst),
                              "--assetsDir", str(mc / "assets"), "--assetIndex", "17", "--uuid", "0" * 32,
                              "--userType", "legacy", "--launchTarget", "forgeclient"],
            "game arguments: parent's then child's, substituted, offline player")
        chk("-DignoreList=client-extra,neoforge-21.1.9.jar" in argv and f"-Djava.library.path={inst / 'natives'}" in argv
            and f"-p" in argv and argv[argv.index("-p") + 1] == f"{mc / 'libraries'}/net/neoforged/fml/4/fml-4.jar",
            "jvm substitutions: version_name, natives_directory, library_directory")
        win = Plat("windows", "x86_64")
        argv_w, _ = build_launch(mc, "neoforge-21.1.9", inst, win, java="J")
        chk(";" in argv_w[argv_w.index("-cp") + 1] and "-XstartOnFirstThread" not in argv_w, "Windows: ';' and no macOS flag")
        wj("neoforge-26.1.0", {"id": "neoforge-26.1.0", "inheritsFrom": "1.21.1", "mainClass": "net.neoforged.fml.startup.Client",
                               "libraries": []})
        _a, miss26 = build_launch(mc, "neoforge-26.1.0", inst, osx, java="J")
        chk(not miss26, "26.x (fml.startup) needs no version jar")
        jdir = mc / "runtime/java-runtime-epsilon/mac-os-arm64/java-runtime-epsilon/jre.bundle/Contents/Home/bin"
        jdir.mkdir(parents=True); (jdir / "java").write_bytes(b"")
        chk(java_exe(mc, "java-runtime-epsilon", osx) == jdir / "java" and java_exe(mc, "java-runtime-delta", osx) is None,
            "the Launcher's runtime folder per component (jre.bundle on macOS)")

        # ── markers and verdicts ──
        log = ("[main/INFO]: \n     Mod List:\n\t\tName Version (Mod Id)\n\n\t\tExample Mod 1.0 (examplemod)\n"
               "\t\tMinecraft 1.21.1 (minecraft)\n[13:16:15] [main/INFO]: Launching\n"
               "[Render thread/INFO]: Reloading ResourceManager: vanilla, mod_resources, mod/examplelib, mod/neoforge\n"
               "[Render thread/INFO]: Sound engine started\n"
               "[Render thread/INFO]: Created: 512x256x0 minecraft:textures/atlas/particles.png-atlas\n")
        chk(loaded_mods(log) >= {"examplemod", "minecraft", "examplelib"}, "loaded mods from the Mod List and resource packs")
        chk(judge_boot(log, "", None, ["examplemod", "examplelib"])[0] == "PASS", "boot PASS")
        chk(judge_boot(log, "", None, ["examplemod", "othermod"]) == ("FAIL", "not in the loaded mod list: othermod"),
            "boot FAIL when a mod under test is not loaded")
        chk(judge_boot(log, "Exception in thread \"main\" java.util.ServiceConfigurationError: x", None, ["examplemod"])[0]
            == "FAIL", "boot FAIL on a ServiceConfigurationError on stderr")
        chk(judge_boot(log, "", "crash-2026.txt", ["examplemod"])[0] == "FAIL", "boot FAIL on a new crash report")
        chk(judge_boot(log.split("[Render thread/INFO]: Sound")[0], "", None, ["examplemod"])[0] == "TIMEOUT",
            "no title screen = no PASS")
        chk(judge_boot(log + "Missing or unsupported mandatory dependencies:\n", "", None, ["examplemod"])[0] == "FAIL",
            "a missing dependency fails the boot")
        chk(judge_boot(log, "WARNING: A restricted method in java.lang.System has been called\n", None, ["examplemod"])[0]
            == "PASS", "a JVM warning on stderr is not an exception")
        hs = "#\n# A fatal error\n# Problematic frame:\n# C  [CoreFoundation+0x15082c]  __CF_IS_OBJC+0x0\n"
        chk(native_crash(hs).startswith("the JVM crashed in native code: C  [CoreFoundation") and "display" in native_crash(hs)
            and native_crash("fine") is None, "a native JVM crash is named by its frame")
        chk(marker("x\n[STDOUT]: SMOKEHARNESS_BOOT_TEST: [SPAWN] reached title\n"
                   "SMOKEHARNESS_BOOT_TEST: PASS — created world, spawned 3\n") == ("PASS", "created world, spawned 3"),
            "carrier PASS marker (em dash) parsed")
        chk(judge_phase("SMOKEHARNESS_BOOT_TEST: FAIL — smoke test threw in phase SURVIVE: boom", "", None)
            == ("FAIL", "smoke test threw in phase SURVIVE: boom"), "carrier FAIL marker")
        chk(judge_phase("nothing", "", None)[0] == "TIMEOUT" and judge_phase("SMOKEHARNESS_BOOT_TEST: PASS", "", "c.txt")[0]
            == "FAIL", "no marker = TIMEOUT; a crash report beats a PASS")

        # ── pins, releases, assets ──
        wf = ("      - name: examplelib 1.0-mc26.3 from the lib-1.0-mc26.3 release into mavenLocal (pinned by sha256)\n"
              "        run: |\n          mkdir -p x\n"
              '          curl -fsSL -o "$HOME/.m2/x.jar" "https://github.com/o/lib/releases/download/lib-1.0-mc26.3/lib-1.0_mr.jar"\n'
              f'          echo "{"a" * 64}  $HOME/.m2/x.jar" | sha256sum -c -\n'
              "      - name: examplelib -- the lib-1.0_cf.jar variant instead (pinned by sha256)\n"
              "        if: matrix.variant == 'cf'\n        run: |\n"
              '          curl -fsSL "https://github.com/o/lib/releases/download/lib-1.0-mc26.3/lib-1.0_cf.jar" -o "$HOME/x.jar"\n'
              f'          echo "{"b" * 64}  $HOME/x.jar" | sha256sum -c -\n'
              '          curl -fsSL -o k.tar.gz "https://github.com/o/kit/releases/download/kit-v1/kit-v1.tar.gz"\n')
        pins = parse_pins(wf)
        chk(len(pins) == 2 and pins[0]["leg"] is None and pins[0]["sha256"] == "a" * 64 and pins[1]["leg"] == "cf"
            and pins[1]["sha256"] == "b" * 64, "workflow pins: the default and the cf leg, each with its sha256; no kit")
        chk(choose_pins(pins, "mr")["o/lib"]["asset"] == "lib-1.0_mr.jar"
            and choose_pins(pins, "cf")["o/lib"]["asset"] == "lib-1.0_cf.jar", "--variant picks the pin")
        chk(choose_pins(pins[:1], "cf")["o/lib"]["asset"] == "lib-1.0_mr.jar", "no cf pin: the default pin")
        rel = lambda tag, pre, when, names: {"tag_name": tag, "prerelease": pre, "draft": False, "published_at": when,
                                             "assets": [{"name": n, "size": 10, "browser_download_url": f"https://g/{tag}/{n}"}
                                                        for n in names]}
        rels = [rel("lib-1.0-mc1.21.1-r2", True, "2026-10-02", ["lib-1.0_mr.jar", "lib-1.0_cf.jar", "lib-1.0_mr-mc1.21.4.jar"]),
                rel("lib-1.0-mc1.21.1", False, "2026-10-01", ["lib-1.0_mr.jar"]),
                rel("lib-1.0-fix-mc1.21.1", True, "2026-10-03", ["lib-1.0-fix_mr.jar"]),
                rel("lib-1.0-mc1.21.10", True, "2026-10-05", ["lib-1.0_mr.jar"])]
        chk(pick_release(rels, "1.21.1", "1.0")[0]["tag_name"] == "lib-1.0-mc1.21.1", "without --prerelease: the full release")
        chk(pick_release(rels, "1.21.1", "1.0", True)[0]["tag_name"] == "lib-1.0-mc1.21.1-r2",
            "--prerelease: the newest of the branch's mod_version series (-mc1.21.1 never matches -mc1.21.10)")
        chk(pick_release(rels, "1.21.1", "1.0-fix", True)[0]["tag_name"] == "lib-1.0-fix-mc1.21.1", "the -fix series")
        chk("--prerelease" in pick_release(rels, "1.21.10", None)[1], "only pre-releases: say --prerelease")
        a0 = rels[0]["assets"]
        chk(pick_asset(a0, "1.21.1", "mr")["name"] == "lib-1.0_mr.jar" and pick_asset(a0, "1.21.4", "mr")["name"]
            == "lib-1.0_mr-mc1.21.4.jar" and pick_asset(a0, "1.21.1", "cf")["name"] == "lib-1.0_cf.jar",
            "the variant jar for this version")
        chk(pick_asset([{"name": "mod-1.21.1-neoforge-1.0.jar"}], "1.21.1")["name"] == "mod-1.21.1-neoforge-1.0.jar",
            "a release with one jar")

        # ── a plan from fixture branch data ──
        lib_jar = _jar("examplelib", floor="21.1.5")
        mod_jar = _jar("examplemod", requires=["examplelib", "geckoish"])
        lib_mr = _jar("examplelib")
        dep = _jar("geckoish")
        files = {("o/lib", "neoforge-1.21.1", "gradle.properties"): "neo_version=21.1.9\nmod_version=1.0\n",
                 ("o/mod", "neoforge-1.21.1", "gradle.properties"): "neo_version=21.1.7\nmod_version=2.0\n",
                 ("o/mod", "neoforge-1.21.1", ".github/workflows/port-ci.yml"):
                     wf.replace("mc26.3", "mc1.21.1").replace("a" * 64, sha(lib_mr)),
                 ("o/lib", "neoforge-1.21.1", ".github/port-install.json"): "{\"never\": \"read\"}"}
        rels_lib = [rel("lib-1.0-mc1.21.1", True, "2026-10-02", ["lib-1.0_mr.jar", "lib-1.0_cf.jar"])]
        rels_mod = [rel("mod-2.0-mc1.21.1", True, "2026-10-04", ["mod-1.21.1-2.0.jar"])]
        urls = {"https://g/lib-1.0-mc1.21.1/lib-1.0_mr.jar": lib_jar, "https://g/lib-1.0-mc1.21.1/lib-1.0_cf.jar": lib_jar,
                "https://g/mod-2.0-mc1.21.1/mod-1.21.1-2.0.jar": mod_jar,
                "https://github.com/o/lib/releases/download/lib-1.0-mc1.21.1/lib-1.0_mr.jar": lib_mr,
                "https://reg/geckoish.jar": dep}
        metas = {VERSION_MANIFEST: {"versions": [{"id": "1.21.1", "url": "https://m/1.21.1.json", "sha1": "f" * 40}]},
                 "https://m/1.21.1.json": {"javaVersion": {"component": "java-runtime-delta", "majorVersion": 21},
                                           "downloads": {"client": {"url": "https://m/c.jar", "sha1": "1" * 40, "size": 2**20}},
                                           "libraries": [{"name": "a:b:1", "downloads": {"artifact": {
                                               "path": "a/b/1/b-1.jar", "url": "https://m/b.jar", "sha1": "2" * 40, "size": 100}}}],
                                           "assetIndex": {"id": "17", "url": "https://m/17.json", "sha1": "3" * 40, "size": 50}},
                 "https://m/17.json": {"objects": {"a": {"hash": "ab" + "0" * 38, "size": 7}}},
                 RUNTIME_MANIFEST: {"mac-os-arm64": {"java-runtime-delta": [{"manifest": {"url": "https://m/rt.json"}}]}},
                 "https://m/rt.json": {"files": {"bin": {"type": "directory"},
                                                 "bin/java": {"type": "file", "downloads": {"raw": {"size": 3 * 2**20}}}}}}
        reg_calls = []
        def reg(m, v):
            reg_calls.append((m, v))
            return ({"name": "geckoish.jar", "url": "https://reg/geckoish.jar", "sha256": sha(dep), "source": "modrinth",
                     "page": "https://modrinth.com/mod/geckoish"}, None) if m == "geckoish" else (None, "none")
        br = {"o/lib": ["main", "neoforge-1.21.1", "neoforge-1.21.4", "neoforge-1.21.x"],
              "o/mod": ["neoforge-1.21.1", "forge-1.20.1"]}
        mcd = tmp / "plan-mc"; (mcd / "versions").mkdir(parents=True)
        src = FakeSources(tmp, br, {"o/lib": rels_lib, "o/mod": rels_mod}, files, urls, metas, reg, 100 * 2**30)
        ap = make_parser()
        args = lambda *x: finish_args(ap.parse_args(list(x)))
        a = args("plan", "--fork", "o/lib", "--fork", "o/mod", "--all-versions", "--prerelease",
                 "--minecraft-dir", str(mcd), "--work", str(tmp / "w"))
        p = build_plan(a, src, osx, log=lambda *x: None)
        v = p["versions"][0] if p["versions"] else {}
        chk([x["mc"] for x in p["versions"]] == ["1.21.1"] and p["skipped"] and p["skipped"][0]["mc"] == "1.21.4",
            "--all-versions: the versions every fork has; a library-only version is skipped and said so")
        chk(a.label == "mod" and v.get("instance") == str(mcd / "instances" / "mod-1.21.1"),
            "the label defaults to the last fork; the instance is <minecraft>/instances/<label>-<mc>")
        chk(v.get("neoforge") == "21.1.9" and v.get("java_major") == 21, "NeoForge: the newest branch's; Java from javaVersion")
        names = [e["name"] for e in v.get("mods", [])]
        chk(names == ["lib-1.0_mr.jar", "mod-1.21.1-2.0.jar", "geckoish.jar"],
            f"the set: each fork's mr jar, then the registry dependency (got {names})")
        chk(any("pinned" in n and "o/lib" in n for n in v.get("notes", [])), "a pin of a --fork is noted, its own release wins")
        chk(reg_calls == [("geckoish", "1.21.1")], "only what nothing in the set provides goes to the registries")
        chk(v.get("modids_under_test") == ["examplelib", "examplemod"], "mods under test: the forks' jars")
        chk(not any(u.endswith("port-install.json") for u in src.fetched), "port-install.json is never read")
        chk(v.get("java_bytes") == 3 * 2**20 and v.get("neoforge_bytes") == NEOFORGE_EST
            and v.get("vanilla_bytes") == 2**20 + 100 + 50 + 7 and v.get("vanilla_missing", {}).get("asset object") == [1, 7],
            "sizes: client jar, libraries, asset index and objects the folder lacks, the Java runtime, NeoForge")
        chk(p["totals"]["disk_ok"] and not v.get("problems"), f"plan problems: {v.get('problems')}")
        txt = render_plan(p)
        chk("Minecraft 1.21.1: NeoForge 21.1.9" in txt and "skipped 1.21.4" in txt and "keeps 5.00 GB free: yes" in txt,
            "the plan prints versions, skips and the disk verdict")
        a = args("plan", "--fork", "o/lib", "--fork", "o/mod", "--mc", "1.21.1", "--prerelease", "--variant", "cf",
                 "--prefer-pins", "--minecraft-dir", str(mcd), "--work", str(tmp / "w"))
        urls["https://github.com/o/lib/releases/download/lib-1.0-mc1.21.1/lib-1.0_cf.jar"] = lib_jar
        files[("o/mod", "neoforge-1.21.1", ".github/workflows/port-ci.yml")] = \
            files[("o/mod", "neoforge-1.21.1", ".github/workflows/port-ci.yml")].replace("b" * 64, sha(lib_jar))
        src.free = 5 * 2**30 + 10 * 2**20
        p2 = build_plan(a, src, osx, log=lambda *x: None)
        v2 = p2["versions"][0]
        chk([e["name"] for e in v2["mods"]][:2] == ["mod-1.21.1-2.0.jar", "lib-1.0_cf.jar"] and "pinned" in v2["mods"][1]["source"],
            "--variant cf --prefer-pins: the workflow's cf pin replaces the library's own release")
        chk(not p2["totals"]["disk_ok"] and "NO -- refused" in render_plan(p2), "disk refusal under the margin")
        ia = args("install", "--mc", "1.21.1", "--dry-run", "--minecraft-dir", str(mcd), "--work", str(tmp / "w"))
        chk((do_install(p2, ia, src, osx, log=lambda *x: None) or [""])[0].startswith("refused"), "install refuses too")
        a = args("plan", "--mc", "1.21.1", "--jar", str(tmp / "x.jar"), "--neoforge", "1.21.1=21.1.3",
                 "--minecraft-dir", str(mcd), "--work", str(tmp / "w"), "--label", "live")
        (tmp / "x.jar").write_bytes(mod_jar); src.free = 100 * 2**30
        p3 = build_plan(a, src, osx, log=lambda *x: None)
        chk(p3["versions"][0]["neoforge"] == "21.1.3" and p3["versions"][0]["mods"][0]["source"] == "local jar"
            and p3["versions"][0]["modids_under_test"] == ["examplemod"], "local jars + a pinned NeoForge, no fork")

        # ── profiles: refused while the Launcher runs; a backup; only our keys change ──
        lp = mcd / "launcher_profiles.json"
        orig = {"profiles": {"NeoForge": {"name": "NeoForge", "type": "custom", "lastVersionId": "neoforge-21.1.1"}},
                "settings": {"x": 1}}
        lp.write_text(json.dumps(orig), encoding="utf-8")
        ents = profile_entries(p)
        pr = add_profiles(mcd, ents, lines=["123 /Applications/Minecraft Launcher.app/Contents/MacOS/launcher"], log=lambda *x: None)
        chk(pr and "running" in pr[0] and json.loads(lp.read_text(encoding="utf-8")) == orig, "Launcher running: refused, untouched")
        chk(launcher_running(["1 /usr/bin/java -jar x"]) is None, "no Launcher process: not running")
        pr = add_profiles(mcd, ents, lines=[], log=lambda *x: None)
        d = json.loads(lp.read_text(encoding="utf-8"))
        k = "mmvu-test-mod-1.21.1"
        chk(not pr and d["profiles"]["NeoForge"] == orig["profiles"]["NeoForge"] and d["settings"] == orig["settings"]
            and d["profiles"][k]["gameDir"] == v["instance"] and d["profiles"][k]["lastVersionId"] == "neoforge-21.1.9"
            and len(list(mcd.glob("launcher_profiles.json.mmvu-*.bak"))) == 1, "profiles added beside the player's, backup")
        add_profiles(mcd, ents, lines=[], log=lambda *x: None)
        chk(len(list(mcd.glob("launcher_profiles.json.mmvu-*.bak"))) == 1, "idempotent: nothing to change, no new backup")

        # ── the instance holds exactly the set; a folder we did not make is refused ──
        vv = {"instance": str(tmp / "instances/mod-1.21.1"), "mc": "1.21.1",
              "mods": [{"name": "m.jar", "url": "https://g/mod-2.0-mc1.21.1/mod-1.21.1-2.0.jar", "sha256": sha(mod_jar)}],
              "carrier": {"name": "gatec-carrier-1.21.1.jar", "path": str(tmp / "x.jar"), "sha256": sha(mod_jar)}}
        chk(not place_mods(vv, src, log=lambda *x: None), "place the set")
        im = pathlib.Path(vv["instance"]) / "mods"
        (im / "stray.jar").write_bytes(b"s")
        place_mods(vv, src, log=lambda *x: None)
        chk(sorted(x.name for x in im.glob("*.jar")) == ["gatec-carrier-1.21.1.jar", "m.jar"]
            and (im.parent / "mods-removed/stray.jar").is_file() and (im.parent / "options.txt").is_file(),
            "mods/ is exactly the set (+ carrier); a stray jar moved aside, not deleted")
        other = tmp / "instances/players"; (other / "mods").mkdir(parents=True)
        chk(place_mods({**vv, "instance": str(other)}, src, log=lambda *x: None), "a folder not made by us is refused")
        bad = {**vv, "mods": [{**vv["mods"][0], "name": "n.jar", "sha256": "0" * 64}]}
        chk(place_mods(bad, src, log=lambda *x: None) and not (im / "n.jar").exists(), "a sha256 mismatch is never installed")

        # ── carrier sources ──
        cd = tmp / "carrier"; cd.mkdir()
        (cd / "gatec-carrier-1.21.1.jar").write_bytes(b"c1")
        (cd / "SHA256SUMS").write_text(f"{sha(b'c1')}  gatec-carrier-1.21.1.jar\n", encoding="utf-8")
        a = args("plan", "--mc", "1.21.1", "--with-carrier", "--carrier-dir", str(cd), "--label", "x", "--work", str(tmp))
        c, why = carrier_for("1.21.1", a, src)
        chk(c and c["sha256"] == sha(b"c1") and carrier_for("1.21.4", a, src)[0] is None, "--carrier-dir, SHA256SUMS checked")
        (cd / "SHA256SUMS").write_text(f"{'0' * 64}  gatec-carrier-1.21.1.jar\n", encoding="utf-8")
        chk(carrier_for("1.21.1", a, src)[0] is None, "a carrier that does not match SHA256SUMS is refused")
        crel = {"tag_name": "gatec-carrier-v3", "assets": [{"name": "gatec-carrier-1.21.1.jar", "size": 2}]}
        src.r[CARRIER_REPO] = [{"tag_name": "gatec-carrier-v2", "assets": []}, crel, {"tag_name": "port-ci-kit-v9"}]
        base = f"https://github.com/{CARRIER_REPO}/releases/download/gatec-carrier-v3/"
        urls[base + "SHA256SUMS"] = f"{sha(b'c1')}  gatec-carrier-1.21.1.jar\n".encode("utf-8")
        urls[base + "MANIFEST.txt"] = f"source_commit abc\njar 1.21.1 21.1.200 21 gatec-carrier-1.21.1.jar {sha(b'c1')} d1\n".encode("utf-8")
        a = args("plan", "--mc", "1.21.1", "--with-carrier", "--label", "x", "--work", str(tmp))
        c, why = carrier_for("1.21.1", a, src)
        chk(c and c["url"] == base + "gatec-carrier-1.21.1.jar" and c["sha256"] == sha(b"c1") and "21.1.200" in c["manifest"],
            f"the newest gatec-carrier-v* release, its SHA256SUMS and MANIFEST line ({why})")
        urls[base + "MANIFEST.txt"] = f"jar 1.21.1 21.1.200 21 gatec-carrier-1.21.1.jar {'9' * 64} d1\n".encode("utf-8")
        chk(carrier_for("1.21.1", a, src)[0] is None, "MANIFEST.txt and SHA256SUMS disagreeing is refused")
        src.r[CARRIER_REPO] = []
        chk("no gatec-carrier-v* release" in carrier_for("1.21.1", a, src)[1], "no carrier release yet: said so")

        # ── the report ──
        rows = [row({"mc": "1.21.1", "neoforge": "21.1.9"}, "boot", "PASS", "ok", 30, tmp / "l"),
                row({"mc": "1.21.1", "neoforge": "21.1.9"}, "spawn", "FAIL", "a|b", 60, tmp / "l")]
        okr = write_report(tmp / "rep", p, rows)
        j = json.loads((tmp / "rep/report.json").read_text(encoding="utf-8"))
        md = (tmp / "rep/report.md").read_text(encoding="utf-8")
        chk(not okr and j["passed"] is False and len(j["results"]) == 2 and "| 1.21.1 | 21.1.9 | spawn | FAIL | 60 | a\\|b |" in md,
            "report.json + report.md, a FAIL is a FAIL")
        chk("spawn" in table(rows) and "FAIL" in table(rows), "the printed table")
        chk(client_running(inst, [f"9 java --gameDir {inst} x"]) == "9" and client_running(inst, ["9 java"]) is None,
            "a client already on the instance is found")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("self-check:", "OK" if not failed else "FAIL: " + "; ".join(failed))
    return 0 if not failed else 1


# ── command line ──────────────────────────────────────────────────────────────────────────────────────────────────
def make_parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "install", "test"):
        s = sub.add_parser(name)
        s.add_argument("--fork", action="append", default=[], help="owner/repo of a fork port (repeat; dependents last)")
        s.add_argument("--mc", action="append", default=[], help="a Minecraft version (repeat)")
        s.add_argument("--all-versions", action="store_true", help="every neoforge-<mc> branch all the forks have")
        s.add_argument("--prerelease", action="store_true", help="pre-releases may be used")
        s.add_argument("--variant", choices=("mr", "cf"), default="mr", help="the platform variant of a multi-jar release")
        s.add_argument("--prefer-pins", action="store_true", help="a sibling a dependent's CI pins: the pinned jar, "
                       "not that fork's own newest release")
        s.add_argument("--jar", action="append", default=[], metavar="[MC=]JAR", help="also a local jar under test")
        s.add_argument("--neoforge", action="append", default=[], metavar="[MC=]VERSION", help="this NeoForge version")
        s.add_argument("--label", help="instance name prefix (default: the last fork's repository name)")
        s.add_argument("--minecraft-dir", help="the Launcher's folder (default MINECRAFT_DIR, else the platform's)")
        s.add_argument("--instances-dir", help="where instances go (default <minecraft>/instances)")
        s.add_argument("--work", help="plan, download cache and reports (default ~/.mmvu/real-install/<label>)")
        s.add_argument("--plan", help="a plan file from `plan` (default: rebuilt from the flags, or <work>/plan.json)")
        s.add_argument("--margin-gb", type=float, default=5.0, help="free disk that must remain (default 5)")
        s.add_argument("--with-carrier", action="store_true", help="also the Gate C carrier")
        s.add_argument("--carrier-release", metavar="TAG", help="this gatec-carrier-vN release (default the newest)")
        s.add_argument("--carrier-dir", metavar="DIR", help="a folder of gatec-carrier-<mc>.jar (+ SHA256SUMS)")
        s.add_argument("--carrier-jar", action="append", default=[], metavar="[MC=]JAR", help="one carrier jar")
        s.add_argument("--carrier-repo", default=CARRIER_REPO, help=argparse.SUPPRESS)
        if name == "install":
            s.add_argument("--profiles", action="store_true", help="also a Launcher profile per version")
            s.add_argument("--dry-run", action="store_true")
            s.add_argument("--use-installed", action="store_true", help="write only the instances: Minecraft, Java "
                           "and NeoForge must already be in the Launcher folder (nothing is written there)")
        if name == "test":
            s.add_argument("--phases", default=",".join(PHASES), help="Gate C phases (default all four)")
            s.add_argument("--boot-only", action="store_true", help="the boot test only")
            s.add_argument("--timeout", type=int, default=300, help="boot test seconds (default 300)")
            s.add_argument("--phase-timeout", type=int, default=900, help="seconds per Gate C phase (default 900)")
            s.add_argument("--xmx", default="4G")
            s.add_argument("--report", metavar="DIR")
        s.add_argument("--json", action="store_true", help="plan: print the plan as JSON")
    return ap


def default_minecraft_dir():
    if os.environ.get("MINECRAFT_DIR"):
        return os.environ["MINECRAFT_DIR"]
    fm = IP._find_minecraft()
    env = fm.read_env_local(fm.ROOT / ".env.local")
    return env.get("MINECRAFT_DIR") or str(fm.official_default())


def finish_args(a):
    if a.with_carrier is False and (a.carrier_dir or a.carrier_jar or a.carrier_release):
        a.with_carrier = True
    if not a.label:
        a.label = (re.sub(r"[^a-z0-9]+", "", a.fork[-1].rstrip("/").split("/")[-1].lower()) if a.fork else
                   (pathlib.Path(a.plan).parent.name if a.plan else None))
    a.minecraft_dir = str(pathlib.Path(a.minecraft_dir or default_minecraft_dir()).expanduser())
    if not a.work:
        a.work = str(pathlib.Path.home() / ".mmvu" / "real-install" / (a.label or "default"))
    if a.instances_dir:
        a.instances_dir = str(pathlib.Path(a.instances_dir).expanduser().resolve())
    return a


def main():
    if "--self-check" in sys.argv:
        return self_check()
    a = finish_args(make_parser().parse_args())
    plat = Plat.host()
    src = Sources(pathlib.Path(a.work) / "cache")
    spec = bool(a.fork or a.mc or a.all_versions)
    full = None                                    # a saved plan's every version, when --mc narrows it
    if a.cmd == "plan" or (spec and not a.plan):
        if not (a.mc or a.all_versions):
            print("real-install: name the versions (--mc X ... or --all-versions)", file=sys.stderr)
            return 2
        if not a.fork and not a.jar:
            print("real-install: name the forks (--fork owner/repo ...) or local jars (--jar)", file=sys.stderr)
            return 2
        try:
            plan = build_plan(a, src, plat)
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"real-install: could not build the plan: {type(e).__name__}: {e}", file=sys.stderr)
            return 2
        pathlib.Path(a.work).mkdir(parents=True, exist_ok=True)
        pf = pathlib.Path(a.work) / "plan.json"
        pf.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        print(json.dumps(plan, indent=2) if a.json else render_plan(plan))
        print(f"\nplan saved: {pf}")
        if a.cmd == "plan":
            bad = not plan["totals"]["disk_ok"] or any(v["problems"] for v in plan["versions"]) or not plan["versions"]
            return 1 if bad else 0
    else:
        pf = pathlib.Path(a.plan) if a.plan else pathlib.Path(a.work) / "plan.json"
        if not pf.is_file():
            cands = sorted((pathlib.Path.home() / ".mmvu" / "real-install").glob("*/plan.json"))
            if a.plan or len(cands) != 1:
                print(f"real-install: no plan at {pf} -- run `plan` first, or pass --plan FILE", file=sys.stderr)
                return 2
            pf = cands[0]
        plan = full = json.loads(pf.read_text(encoding="utf-8"))
        a.work = str(pf.parent)
        if a.mc:                                   # --mc narrows a saved plan to those versions; the file keeps them all
            unknown = [m for m in a.mc if m not in {v["mc"] for v in full["versions"]}]
            if unknown:
                print(f"real-install: {', '.join(unknown)} not in the plan {pf} "
                      f"({', '.join(v['mc'] for v in full['versions'])})", file=sys.stderr)
                return 2
            plan = dict(full, versions=[v for v in full["versions"] if v["mc"] in a.mc])
        print(f"real-install: plan {pf} ({plan['label']}: {', '.join(v['mc'] for v in plan['versions'])})")
        if a.cmd == "install" and a.with_carrier:
            for v in plan["versions"]:
                if not v.get("carrier"):
                    c, why = carrier_for(v["mc"], a, src)
                    if c:
                        v["carrier"] = c
                    else:
                        v["problems"].append(f"Gate C carrier: {why}")
    if a.cmd == "install":
        if not plan["totals"]["disk_ok"]:
            print(render_plan(plan).splitlines()[-1], file=sys.stderr)
            return 1
        probs = do_install(plan, a, src, plat)
        if not a.dry_run:                           # the whole plan: a --mc run updated only its versions (same objects)
            (pathlib.Path(a.work) / "plan.json").write_text(json.dumps(full or plan, indent=2), encoding="utf-8")
        for p in probs:
            print(f"PROBLEM: {p}")
        print("\ninstall: " + ("FAILED" if probs else "done" + (" (dry run)" if a.dry_run else "")))
        return 1 if probs else 0
    if a.cmd == "test":
        rows = do_test(plan, a, plat, log=lambda *x, **k: print(*x, flush=True))
        return 0 if rows and all(r["verdict"] == "PASS" for r in rows) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
