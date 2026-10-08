#!/usr/bin/env python3
"""Install fork ports (tools/port-offer.py) into a Minecraft instance with their dependencies -- the exact files CI tested.

    python3 tools/install-port.py <owner/repo> [<owner/repo> ...] [--mc 1.21.1] [--mods-dir DIR]
                                  [--with-optional] [--full] [--dry-run]

Each fork port carries `.github/port-install.json` on its `neoforge-<mc>` branch: the port's release jar, every
required dependency at the version CI ran (direct URL + sha256), optional integrations, and what the full CI
pass added. This reads it, follows other ports it depends on (their own manifests, recursively), skips any
mod whose id is already in the mods folder, downloads the rest, and checks every sha256 before moving a file
in -- a mismatch is never installed. A mod CI had no file for comes from Modrinth/CurseForge (tools/mod-registry):
a NeoForge build whose jar declares that mod id, labelled "not tested by CI"; an optional mod whose own
requirement cannot be found is left out (it would stop the game). It installs the NeoForge the ports were tested
on, raised within the same line when an added mod needs newer, with NeoForge's own installer, headless.

The output leads with the result: SUCCEEDED / FAILED per requested port (the port plus every mod it needs),
then optional extras, then warnings about optional mods, which never change the result. Exit 0 = every port in.

  --with-optional   also the optional mods CI tested with the ports (and what they need)
  --with-untested   also the optional integrations CI never loaded, from Modrinth/CurseForge, labelled as such
  --full            also what CI's full pass added (an exact replica of the tested setup)
  --dry-run         say what would happen, change nothing
  --no-neoforge     leave NeoForge alone
  -v, --verbose     also every file and every note

The mods folder defaults to $MINECRAFT_MODS_DIR, then the platform's default instance (macOS
~/Library/Application Support/minecraft/mods, Linux ~/.minecraft/mods, Windows %APPDATA%/.minecraft/mods).
Standard library only.
"""
import argparse, hashlib, json, os, pathlib, platform, re, shutil, sys, tempfile, urllib.error, urllib.request, zipfile

UA = {"User-Agent": "install-port"}


def default_mods_dir():
    if os.environ.get("MINECRAFT_MODS_DIR"):
        return pathlib.Path(os.environ["MINECRAFT_MODS_DIR"]).expanduser()
    home = pathlib.Path.home()
    if platform.system() == "Darwin":
        return home / "Library/Application Support/minecraft/mods"
    if platform.system() == "Windows":
        return pathlib.Path(os.environ.get("APPDATA", home)) / ".minecraft/mods"
    return home / ".minecraft/mods"


def fetch(url, timeout=120):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return r.read()
    except urllib.error.URLError as e:
        # python.org's macOS Python has no CA certificates until "Install Certificates.command" is run;
        # the system curl uses the OS's own. Fall back to it rather than fail on every download.
        if "CERTIFICATE_VERIFY_FAILED" not in str(getattr(e, "reason", e)) or not shutil.which("curl"):
            raise
        import subprocess
        r = subprocess.run(["curl", "-fsSL", "--max-time", str(timeout), "-A", UA["User-Agent"], url],
                           capture_output=True)
        if r.returncode:
            raise urllib.error.URLError(f"curl exit {r.returncode}: {r.stderr.decode('utf-8', 'replace').strip()}")
        return r.stdout


def manifest_url(repo, branch):
    if repo.startswith(("file:", "http://", "https://")) and repo.endswith(".json"):
        return repo
    repo = re.sub(r"^https://github\.com/", "", repo).strip("/").removesuffix(".git")
    return f"https://raw.githubusercontent.com/{repo}/{branch}/.github/port-install.json"


def installed_modids(mods_dir):
    out = {}
    for j in sorted(pathlib.Path(mods_dir).glob("*.jar")) if pathlib.Path(mods_dir).is_dir() else []:
        try:
            with zipfile.ZipFile(j) as z:        # closed again: Windows cannot replace an open file
                t = "".join(z.read(n).decode("utf-8", "replace") for n in z.namelist()
                            if n in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"))
        except (OSError, zipfile.BadZipFile):
            continue
        for m in re.findall(r'(?s)\[\[mods\]\].*?modId\s*=\s*"([^"]+)"', t):
            out.setdefault(m, j.name)
    return out


def jar_modids(data):
    import io
    z = zipfile.ZipFile(io.BytesIO(data))
    t = "".join(z.read(n).decode("utf-8", "replace") for n in z.namelist()
                if n in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"))
    return re.findall(r'(?s)\[\[mods\]\].*?modId\s*=\s*"([^"]+)"', t)


def jar_neoforge_floor(data):
    """The lowest NeoForge a mod jar accepts (its neoforge dependency's versionRange lower bound), or None."""
    import io
    z = zipfile.ZipFile(io.BytesIO(data))
    t = "".join(z.read(n).decode("utf-8", "replace") for n in z.namelist() if n == "META-INF/neoforge.mods.toml")
    for blk in re.split(r"(?m)^\s*\[\[", t):
        if blk.startswith("dependencies.") and re.search(r'modId\s*=\s*"neoforge"', blk):
            m = re.search(r'versionRange\s*=\s*"[\[(]\s*([0-9][0-9.]*)', blk)
            if m:
                return m.group(1)
    return None


def jar_bundled(data):
    """The modIds of the jars a mod bundles in META-INF/jarjar (it needs no separate install of those)."""
    import io
    z = zipfile.ZipFile(io.BytesIO(data))
    out = set()
    for n in z.namelist():
        if n.startswith("META-INF/jarjar/") and n.endswith(".jar"):
            try:
                inner = z.read(n)
                out |= set(jar_modids(inner)) | jar_bundled(inner)
            except Exception:
                pass
    return out


def jar_required(data):
    """The modIds a mod jar's mods.toml marks required, less the ones it bundles itself."""
    import io
    z = zipfile.ZipFile(io.BytesIO(data))
    t = "".join(z.read(n).decode("utf-8", "replace") for n in z.namelist()
                if n in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"))
    out = set()
    for blk in re.split(r"(?m)^\s*\[\[", t):
        if blk.startswith("dependencies."):
            m = re.search(r'modId\s*=\s*"([^"]+)"', blk)
            t = re.search(r'type\s*=\s*"(\w+)"', blk)
            req = (t.group(1) == "required") if t else not re.search(r"mandatory\s*=\s*false", blk)  # NeoForge: no type = required
            if m and req and m.group(1) not in ("minecraft", "neoforge", "forge", "java"):
                out.add(m.group(1))
    return out - jar_bundled(data)


def vkey(name):
    """Sortable version from a jar name or version string: the numbers in order (geckolib-...-4.8.4.jar > 4.7.1)."""
    nums = re.findall(r"\d+", re.sub(r"(?:neoforge|forge)-?1\.\d+(?:\.\d+)?|mc1\.\d+(?:\.\d+)?|\+1\.\d+(?:\.\d+)?", "",
                                    name or ""))
    return tuple(int(n) for n in nums)


MODREG = pathlib.Path(__file__).resolve().parent / "mod-registry/modreg.py"


def registry_lookup(modid, mc, get=fetch):
    """A file for `modid` from Modrinth/CurseForge (tools/mod-registry, the install-mod skill's backend) when CI
    tested none: a NeoForge <mc> build whose jar really declares that mod id, its sha1 checked.
    -> (file dict, None) or (None, why)."""
    import subprocess
    if not MODREG.is_file():
        return None, "the registry tool is missing"
    def call(*args):
        try:
            r = subprocess.run([sys.executable, str(MODREG), *args], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=180)
            return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None
        except Exception:
            return None
    cands = [("modrinth", modid), ("modrinth", modid.replace("_", "-"))]      # a Modrinth slug is often the id
    for q in dict.fromkeys([modid, modid.replace("_", " ")]):
        for res in ((call("search", "--query", q) or {}).get("results") or [])[:4]:
            cands += [(pv["provider"], pv["id"]) for pv in res.get("providers", [])]
    for prov, pid in list(dict.fromkeys(cands))[:8]:
        v = call("versions", "--provider", prov, "--id", pid, "--loader", "neoforge", "--mc", mc) or {}
        ch = v.get("chosen") or {}
        if not ch.get("downloadUrl") or ch.get("loader") != "neoforge" or ch.get("mc") != mc:
            continue
        try:
            data = get(ch["downloadUrl"])
            if ch.get("sha1") and hashlib.sha1(data).hexdigest() != ch["sha1"]:
                continue
            if modid not in jar_modids(data):
                continue
        except Exception:
            continue
        page = (f"https://modrinth.com/mod/{v.get('slug') or pid}" if prov == "modrinth"
                else f"https://www.curseforge.com/minecraft/mc-mods/{v.get('slug') or pid}")
        return {"name": ch.get("fileName") or f"{modid}.jar", "url": ch["downloadUrl"],
                "sha256": hashlib.sha256(data).hexdigest(), "source": prov, "page": page}, None
    return None, f"no NeoForge {mc} build on Modrinth or CurseForge"


UNTESTED = "not tested by CI"


def plan(repos, branch, with_optional, full, get=fetch, mc="1.21.1", lookup=None, untested=False):
    """-> (files, notes, problems, neo, ports, missing). A mod named twice is installed once (the newest tested
    file); a mod CI had no file for comes from the registries; an added mod's own requirements come too.
    ports: per requested repo, the mod ids it needs (its siblings' included). missing: modid -> why not found."""
    lookup = lookup or (lambda m: registry_lookup(m, mc, get))
    with_optional = with_optional or full or untested   # each flag includes the CI-tested optional mods
    roles = {"self", "required"} | ({"optional"} if with_optional else set()) | ({"extra"} if full else set())
    files, seen, notes, problems, mans, queue, conflicts = [], {}, [], [], {}, [(r, r) for r in repos], {}
    extras, missing, wanted_by, raise_to = {}, {}, {}, []        # extras: modid -> an "extra" file, should an added mod need it
    visited = {}
    while queue:
        repo, asked = queue.pop(0)
        url = manifest_url(repo, branch)
        if url in visited:
            continue
        visited[url] = repo
        try:
            man = json.loads(get(url).decode("utf-8"))
        except Exception as e:
            problems.append(f"{repo}: no port manifest at {url} ({type(e).__name__}: {getattr(e, 'reason', e)})")
            mans[repo] = None
            continue
        mans[repo] = man
        port = man.get("port", repo)
        if not any(f.get("role") == "self" for f in man.get("files", [])):
            problems.append(f"{port}: its release has several jars and the manifest names none")
        for f in man.get("files", []):
            mid = f.get("modid") or f.get("name")
            if f.get("role") == "extra" and f.get("url"):
                extras.setdefault(mid, dict(f, from_port=port))
            if f.get("role") not in roles:
                continue
            wanted_by.setdefault(mid, set()).add(port)
            if mid in seen or mid in missing:
                old = seen.get(mid)
                if old and f.get("url") and old.get("url") and f["url"] != old["url"]:
                    if vkey(f.get("name")) > vkey(old.get("name")):     # ports disagree: the newest tested file
                        newf = dict(f, from_port=port)
                        files[next(i for i, x in enumerate(files) if x is old)] = newf
                        seen[mid] = newf
                    if f.get("name") != old.get("name"):     # the same file from two URLs is not a conflict
                        conflicts.setdefault(mid, set()).update({old.get("name"), f.get("name")})
                continue
            entry = dict(f, from_port=port)
            if not f.get("url"):                         # CI had no file: the registries
                if f["role"] not in ("self", "required") and not untested:
                    missing[mid] = UNTESTED              # an optional integration CI never loaded: only on request
                    continue
                found, why = lookup(mid)
                if not found:
                    missing[mid] = why
                    continue
                entry.update(found)
            seen[mid] = entry
            files.append(entry)
            if f.get("port") and f["role"] != "self":
                queue.append((f["port"], None))         # a port this one depends on: its own requirements too
    # a mod CI did not load in the minimal pass brings its own requirements: read them from its jar
    checked = set()
    while not full:
        todo = [f for f in files if (f["role"] in ("optional", "extra") or f.get("source")) and f["modid"] not in checked]
        if not todo:
            break
        for f in todo:
            checked.add(f["modid"])
            try:
                need = jar_required(get(f["url"]))
            except Exception:
                continue
            if need - set(seen) - set(extras):
                for k, e in list(extras.items()):         # an extra named by its build coordinate: read its modIds
                    if not e.get("_read"):
                        e["_read"] = True
                        try:
                            for m in jar_modids(get(e["url"])):
                                extras.setdefault(m, dict(e, modid=m))
                        except Exception:
                            pass
            f["needs_all"] = sorted(need)
            for d in sorted(need - set(seen)):
                wanted_by.setdefault(d, set()).update(wanted_by.get(f["modid"], set()))
                if d in extras:
                    e = dict(extras[d], role=f["role"] if f["role"] != "self" else "required", needed_by=f["modid"])
                elif f["role"] in ("optional", "extra") and not untested:
                    missing[d] = f"needed by {f['modid']}, and CI has no file for it ({UNTESTED})"
                    continue
                else:
                    found, why = lookup(d)
                    if not found:
                        missing[d] = f"needed by {f['modid']}: {why}"
                        continue
                    e = dict(found, modid=d, role=f["role"], needed_by=f["modid"], from_port=f.get("from_port"))
                seen[d] = e; files.append(e)
                f.setdefault("needs", []).append(d)
    # one NeoForge serves every mod: the newest tested, raised to what any added mod demands (same line only)
    tested = max((m.get("neoforge") for m in mans.values() if m and m.get("neoforge")), key=vkey, default=None)
    if tested:
        line = tested.rsplit(".", 1)[0]
        for f in list(files):
            try:
                floor = jar_neoforge_floor(get(f["url"])) if (f["role"] != "self" and (f.get("source") or
                                                                 f["role"] in ("optional", "extra"))) else None
            except Exception:
                floor = None
            if floor and vkey(floor) > vkey(tested):
                if floor.rsplit(".", 1)[0] != line:
                    missing[f["modid"]] = f"left out: it needs NeoForge {floor}, outside the ports' {line} line"
                    files.remove(f); seen.pop(f["modid"], None)
                else:
                    raise_to.append((floor, f["modid"]))
    # an optional mod with a requirement nobody has would stop the game from starting: leave it out
    while True:
        gone = {f["modid"] for f in files if f["role"] in ("optional", "extra")
                and (any(n in missing for n in f.get("needs_all", [])) or f.get("needed_by") in missing)}
        if not gone:
            break
        for f in [f for f in files if f["modid"] in gone]:
            lack = [n for n in f.get("needs_all", []) if n in missing]
            missing[f["modid"]] = (f"left out: it needs {', '.join(lack)}, which was not found" if lack
                                   else f"left out: only {f['needed_by']} needed it")
            files.remove(f); seen.pop(f["modid"], None)
    for mid, names in sorted(conflicts.items()):
        notes.append(f"{mid}: the ports were tested with {', '.join(sorted(n for n in names if n))}; "
                     f"installing the newest, {seen[mid]['name']}")
    neo = sorted({(m.get("minecraft"), m.get("neoforge"), m.get("port")) for m in mans.values()
                  if m and m.get("neoforge")}, key=lambda t: vkey(t[1]))
    if raise_to and neo:
        v, who = max(raise_to, key=lambda t: vkey(t[0]))
        neo.append((neo[-1][0], v, f"{who} (needs at least {v})"))
    # what each requested repo needs: its self + required files, its sibling ports', and what those need
    by_port = {}
    for repo, man in mans.items():
        if man:
            fs = man.get("files", [])
            by_port[man.get("port", repo)] = {
                "self": next((x.get("modid") for x in fs if x.get("role") == "self"), None),
                "required": [x.get("modid") or x.get("name") for x in fs if x.get("role") == "required"],
                "siblings": [x["port"] for x in fs if x.get("port") and x.get("role") != "self"]}
    def closure(port, seen_p=None):
        seen_p = seen_p or set()
        if port in seen_p or port not in by_port:
            return []
        seen_p.add(port)
        out = list(by_port[port]["required"])
        for sib in by_port[port]["siblings"]:
            out += closure(sib, seen_p)
        return out
    ports = []
    for r in repos:
        man = mans.get(r)
        if not man:
            ports.append({"repo": r, "self": None, "deps": [], "error": next((x for x in problems if x.startswith(r)), "?")})
            continue
        port = man.get("port", r)
        need = list(dict.fromkeys(closure(port)))
        need += [x for f in files if f["modid"] in need for x in f.get("needs", []) if x not in need]
        ports.append({"repo": r, "port": port, "self": by_port[port]["self"],
                      "deps": [d for d in need if d != by_port[port]["self"]]})
    return files, notes, problems, neo, ports, missing


def install(files, mods_dir, dry=False, get=fetch):
    """-> (done lines, skipped lines, failure lines, status: modid -> installed|updated|present|would install|
    would update|failed)."""
    have = installed_modids(mods_dir)
    done, skipped, bad, status = [], [], [], {}
    for f in files:
        mid = f.get("modid") or f["name"]
        name = re.sub(r"[^\w.+-]", "_", f.get("name") or f"{mid}.jar")
        update = False
        if mid in have:
            same = have[mid] == name and f.get("sha256")
            if not (same and hashlib.sha256((mods_dir / name).read_bytes()).hexdigest() != f["sha256"]):
                skipped.append(f"{mid}: already in the mods folder ({have[mid]})")
                status[mid] = "present"
                continue
            update = True                    # same file name, different contents: a re-release of the port
        if dry:
            done.append(f"{mid}: would {'update' if update else 'install'} {f['name']}")
            status[mid] = "would update" if update else "would install"
            continue
        try:
            data = get(f["url"])
        except Exception as e:
            bad.append(f"{mid}: download failed ({type(e).__name__}: {e})"); status[mid] = "failed"; continue
        if f.get("sha256") and hashlib.sha256(data).hexdigest() != f["sha256"]:
            bad.append(f"{mid}: sha256 mismatch -- not the file CI tested, not installed"); status[mid] = "failed"
            continue
        mods_dir.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.NamedTemporaryFile(dir=mods_dir, suffix=".part", delete=False)
        tmp.write(data); tmp.close()
        os.replace(tmp.name, mods_dir / name)
        done.append(f"{mid}: {'updated' if update else 'installed'} {name}"
                    + ("" if f.get("sha256") else " (no sha256 in the manifest)"))
        status[mid] = "updated" if update else "installed"
    return done, skipped, bad, status


OK_WORDS = {"installed", "updated", "present", "would install", "would update"}


def summarise(ports, files, status, missing, bad, neo_line, neo_ok, dry):
    """The result first: per requested port, succeeded or failed; then optional extras; then warnings."""
    by_id = {f["modid"]: f for f in files}
    def label(m):
        f = by_id.get(m, {})
        bits = ([f"from {f['source'].capitalize()}, not tested by CI"] if f.get("source") else []) + \
               ([f"needed by {f['needed_by']}"] if f.get("needed_by") else [])
        return m + (f" ({'; '.join(bits)})" if bits else "")
    lines, failed = [], []
    for pt in ports:
        name = pt["repo"]
        if pt["self"] is None:
            failed.append(f"{name}: {pt.get('error', 'no manifest')}"); continue
        bad_ids = [m for m in [pt["self"]] + pt["deps"] if status.get(m) not in OK_WORDS]
        if bad_ids:
            failed.append(f"{name}: " + "; ".join(f"{m}: {missing.get(m) or status.get(m) or 'not installed'}"
                                                  for m in bad_ids))
            continue
        deps = pt["deps"]
        lines.append(f"  {name}: {pt['self']}" + (f" + {len(deps)} dependenc{'y' if len(deps) == 1 else 'ies'} "
                     f"({', '.join(label(d) for d in deps)})" if deps else " (no dependencies)"))
    required = {m for pt in ports for m in [pt["self"]] + pt["deps"] if m}
    extra_ok = [m for m in by_id if m not in required and status.get(m) in OK_WORDS]
    skipped = sorted(m for m, why in missing.items() if why == UNTESTED and m not in required)
    warn = [f"{m}: {why}" for m, why in sorted(missing.items()) if m not in required and why != UNTESTED]
    warn += [x for x in bad if x.split(":")[0] not in required]
    out = []
    if lines:
        out.append("WOULD SUCCEED:" if dry else "SUCCEEDED:")
        out += lines
        if neo_line and neo_ok:
            out.append(f"  {neo_line}")
    if extra_ok:
        out.append("Optional, " + ("would also install" if dry else "also installed") + ": "
                   + ", ".join(label(m) for m in extra_ok))
    if failed or not neo_ok:
        out += ["", "FAILED:"] + [f"  {x}" for x in failed] + ([] if neo_ok else [f"  {neo_line}"])
    if skipped:
        out += ["", f"Not installed: {len(skipped)} optional integration(s) CI never tested ({', '.join(skipped)}). "
                "--with-untested fetches them from Modrinth/CurseForge."]
    if warn:
        out += ["", "Warnings (optional mods only; they do not change the result):"] + [f"  - {x}" for x in warn]
    good = not failed and neo_ok and bool(ports)
    n, verb = len(ports), ("would install" if dry else "installed")
    out.append("")
    out.append(f"RESULT: OK -- {n} of {n} port(s) {verb}." + ("" if dry else " Start Minecraft with the NeoForge profile.")
               if good else f"RESULT: FAILED -- {n - len(failed)} of {n} port(s) {verb}. See FAILED above.")
    return out, good


NEO_MAVEN = "https://maven.neoforged.net/releases/net/neoforged/neoforge"


def neoforge_installed(mc_dir, v):
    """The installed NeoForge of v's line (21.1.x) that is v or newer, else None."""
    line = v.rsplit(".", 1)[0]
    have = sorted((p.name[len("neoforge-"):] for p in (mc_dir / "versions").glob(f"neoforge-{line}.*")),
                  key=vkey) if (mc_dir / "versions").is_dir() else []
    ok = [h for h in have if vkey(h) >= vkey(v)]
    return ok[-1] if ok else None


def java_works(java):
    try:
        import subprocess
        return subprocess.run([str(java), "-version"], capture_output=True, timeout=60).returncode == 0
    except (OSError, Exception):
        return False


def find_java(mc_dir):
    """A Java to run the NeoForge installer: the one the Minecraft Launcher downloaded (always there once it
    has run), then JAVA_HOME, then PATH. macOS's /usr/bin/java is a stub without a JDK, so each is tried."""
    rt = mc_dir / "runtime"
    exe = "java.exe" if os.name == "nt" else "java"
    cands = sorted(rt.glob(f"**/bin/{exe}"), key=lambda p: ("delta" not in str(p), str(p))) if rt.is_dir() else []
    if os.environ.get("JAVA_HOME"):
        cands.append(pathlib.Path(os.environ["JAVA_HOME"]) / "bin" / exe)
    if shutil.which("java"):
        cands.append(pathlib.Path(shutil.which("java")))
    return next((c for c in cands if c.is_file() and java_works(c)), None)


def ensure_neoforge(neo, mods_dir, dry=False, get=fetch, run=None):
    """One instance has one NeoForge: the newest version any port was tested on (newer in the same line is
    fine). Install it with its own installer, headless, when it is missing. -> (notes, problems)."""
    if not neo:
        return [], []
    mc, v, _p = neo[-1]
    notes, problems = [], []
    if len({n[1] for n in neo}) > 1:
        notes.append("tested on different NeoForge versions (" + "; ".join(f"{p}: {n}" for _m, n, p in neo)
                     + f") -- using the newest, {v}")
    mc_dir = mods_dir.parent
    have = neoforge_installed(mc_dir, v)
    if have:
        notes.append(f"NeoForge {have} (Minecraft {mc}) is installed -- pick it in the Launcher")
        return notes, problems
    url = f"{NEO_MAVEN}/{v}/neoforge-{v}-installer.jar"
    if dry:
        notes.append(f"would install NeoForge {v} (Minecraft {mc}) with its installer, {url}")
        return notes, problems
    if not (mc_dir / "launcher_profiles.json").is_file():
        problems.append(f"NeoForge {v} not installed: {mc_dir} has no launcher_profiles.json -- open the "
                        "Minecraft Launcher once (it creates it), then run this again")
        return notes, problems
    java = find_java(mc_dir)
    if not java:
        problems.append(f"NeoForge {v} not installed: no working Java found (the Minecraft Launcher's own, "
                        f"JAVA_HOME, or PATH) -- install it by hand: {url}")
        return notes, problems
    try:
        data = get(url)
        want = get(url + ".sha256").decode("utf-8").split()[0]
    except Exception as e:
        problems.append(f"NeoForge {v}: download failed ({type(e).__name__}: {getattr(e, 'reason', e)})")
        return notes, problems
    if hashlib.sha256(data).hexdigest() != want:
        problems.append(f"NeoForge {v}: the installer's sha256 does not match the maven's -- not run")
        return notes, problems
    import subprocess
    with tempfile.TemporaryDirectory() as t:
        jar = pathlib.Path(t, f"neoforge-{v}-installer.jar"); jar.write_bytes(data)
        r = (run or subprocess.run)([str(java), "-jar", str(jar), "--install-client", str(mc_dir)],
                                    cwd=t, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode or not neoforge_installed(mc_dir, v):
        problems.append(f"NeoForge {v}: its installer failed: " + (r.stdout + r.stderr).strip()[-400:])
    else:
        notes.append(f"installed NeoForge {v} (Minecraft {mc}) -- in the Launcher pick the \"NeoForge\" profile")
    return notes, problems


def self_check():
    failed = []
    def _chk(cond, where):
        if not cond:
            failed.append(where)
        return bool(cond)
    ok = True
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        def jar(name, modid):
            p = d / "src" / name
            p.parent.mkdir(exist_ok=True)
            with zipfile.ZipFile(p, "w") as z:
                z.writestr("META-INF/neoforge.mods.toml", f'[[mods]]\nmodId="{modid}"\n')
            return p
        lib, me, opt, dep2 = jar("lib.jar", "lib"), jar("me.jar", "me"), jar("opt.jar", "opt"), jar("base.jar", "base")
        sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
        libman = {"port": "o/lib", "minecraft": "1.21.1", "neoforge": "21.1.1", "files": [
            {"role": "self", "modid": "lib", "name": "lib.jar", "url": lib.as_uri(), "sha256": sha(lib)},
            {"role": "required", "modid": "base", "name": "base.jar", "url": dep2.as_uri(), "sha256": sha(dep2)}]}
        meman = {"port": "o/me", "minecraft": "1.21.1", "neoforge": "21.1.1", "files": [
            {"role": "self", "modid": "me", "name": "me.jar", "url": me.as_uri(), "sha256": sha(me)},
            {"role": "required", "modid": "lib", "name": "lib.jar", "url": lib.as_uri(), "sha256": sha(lib), "port": "o/lib"},
            {"role": "optional", "modid": "opt", "name": "opt.jar", "url": opt.as_uri(), "sha256": "0" * 64},
            {"role": "required", "modid": "ghost", "page": "https://example.invalid"}]}  # no CI file: registry
        mans = {manifest_url("o/me", "b"): meman, manifest_url("o/lib", "b"): libman}
        get = lambda u: json.dumps(mans[u]).encode() if u in mans else fetch(u)
        ghostjar = jar("ghost-1.jar", "ghost")
        reg = {"ghost": {"name": "ghost-1.jar", "url": ghostjar.as_uri(), "sha256": sha(ghostjar), "source": "modrinth"}}
        lookup = lambda m: (reg[m], None) if m in reg else (None, "no NeoForge 1.21.1 build on Modrinth or CurseForge")
        files, notes, problems, neo, ports, missing = plan(["o/me"], "b", False, False, get, lookup=lookup)
        ok &= _chk([f["modid"] for f in files] == ["me", "lib", "ghost", "base"], 26)  # registry fills the gap; sibling's too
        ok &= _chk(next(f for f in files if f["modid"] == "ghost")["source"] == "modrinth" and not missing, 27)
        ok &= _chk(ports == [{"repo": "o/me", "port": "o/me", "self": "me", "deps": ["lib", "ghost", "base"]}], 28)
        ok &= _chk({n[1] for n in neo} == {"21.1.1"}, 29)
        mods = d / "inst/mods"
        done, skipped, bad, status = install(files, mods, get=get)
        ok &= _chk(len(done) == 4 and not bad and sorted(installed_modids(mods)) == ["base", "ghost", "lib", "me"], 32)
        out, good = summarise(ports, files, status, missing, bad, "NeoForge 21.1.1 is installed", True, False)
        ok &= _chk(good and out[0] == "SUCCEEDED:" and "me + 3 dependencies" in out[1] and "Modrinth, not tested" in out[1], 34)
        ok &= _chk(out[-1].startswith("RESULT: OK -- 1 of 1"), 35)
        done2, skipped2, _, st2 = install(files, mods, get=get)
        ok &= _chk(not done2 and len(skipped2) == 4 and set(st2.values()) == {"present"}, 37)   # idempotent
        with zipfile.ZipFile(me, "a") as z:                                  # a re-release: same name, new bytes
            z.writestr("changed.txt", "r2")
        meman["files"][0]["sha256"] = sha(me)
        done3, _s3, bad3, _st3 = install(plan(["o/me"], "b", False, False, get, lookup=lookup)[0], mods, get=get)
        ok &= _chk(done3 == ["me: updated me.jar"] and not bad3, 42)
        # a required mod nobody has: that port FAILS, and says why
        f5, _n5, _p5, _neo5, ports5, missing5 = plan(["o/me"], "b", False, False, get, lookup=lambda m: (None, "nowhere"))
        _d5, _s5, bad5, st5 = install(f5, d / "inst5/mods", dry=True, get=get)
        out5, good5 = summarise(ports5, f5, st5, missing5, bad5, "", True, True)
        ok &= _chk(not good5 and "FAILED:" in out5 and any("ghost: nowhere" in x for x in out5), 47)
        ok &= _chk(out5[-1].startswith("RESULT: FAILED -- 0 of 1"), 48)
        lib2 = jar("lib-2.jar", "lib")
        mans[manifest_url("o/other", "b")] = {"port": "o/other", "minecraft": "1.21.1", "neoforge": "21.1.9", "files": [
            {"role": "self", "modid": "other", "name": "other.jar", "url": me.as_uri()},
            {"role": "required", "modid": "lib", "name": "lib-2.jar", "url": lib2.as_uri()}]}
        f3, n3, _p3, neo3, _pt3, _m3 = plan(["o/me", "o/other"], "b", False, False, get, lookup=lookup)
        ok &= _chk(next(f for f in f3 if f["modid"] == "lib")["name"] == "lib-2.jar" and any("newest" in n for n in n3), 54)
        ok &= _chk(neo3[-1][1] == "21.1.9", 55)
        withdep = d / "src" / "optdep.jar"
        with zipfile.ZipFile(withdep, "w") as z:
            z.writestr("META-INF/neoforge.mods.toml", '[[mods]]\nmodId="optdep"\n[[dependencies.optdep]]\n'
                       'modId="needed"\ntype="required"\n[[dependencies.optdep]]\nmodId="neoforge"\ntype="required"\n')
        needed = jar("needed.jar", "needed")
        mans[manifest_url("o/x", "b")] = {"port": "o/x", "files": [
            {"role": "self", "modid": "x", "name": "x.jar", "url": me.as_uri()},
            {"role": "optional", "modid": "optdep", "name": "optdep.jar", "url": withdep.as_uri()},
            {"role": "optional", "modid": "gone"},
            {"role": "extra", "modid": "g:needed-123", "name": "needed.jar", "url": needed.as_uri()},
            {"role": "extra", "modid": "unrelated", "name": "u.jar", "url": needed.as_uri()}]}
        f4, _n4, _p4, _neo4, pt4, m4 = plan(["o/x"], "b", True, False, get, lookup=lookup)
        ok &= _chk([f["modid"] for f in f4] == ["x", "optdep", "needed"] and "gone" in m4, 68)
        _d4, _s4, bad4, st4 = install(f4, d / "inst4/mods", dry=True, get=get)
        out4, good4 = summarise(pt4, f4, st4, m4, bad4, "", True, True)
        ok &= _chk(good4 and any(x.startswith("Optional, would also install: optdep, needed") for x in out4), 71)
        ok &= _chk(any(x.startswith("Not installed: 1 optional") and "gone" in x and "--with-untested" in x for x in out4), 72)
        f4u, _n4u, _p4u, _neo4u, pt4u, m4u = plan(["o/x"], "b", False, False, get, lookup=lookup, untested=True)
        ok &= _chk("gone" in m4u and m4u["gone"] != UNTESTED and [f["modid"] for f in f4u] == ["x", "optdep", "needed"], 900)
        ok &= _chk([f["modid"] for f in plan(["o/x"], "b", False, False, get, lookup=lookup)[0]] == ["x"], 73)
        ok &= _chk({f["modid"] for f in plan(["o/x"], "b", False, True, get, lookup=lookup)[0]} == {"x", "optdep", "g:needed-123", "unrelated"}, 74)
        f2 = plan(["o/me"], "b", True, False, get, lookup=lookup)[0]
        _d, _s, bad2, _st = install([f for f in f2 if f["modid"] == "opt"], mods, get=get)
        ok &= _chk(bad2 and "sha256 mismatch" in bad2[0] and "opt" not in installed_modids(mods), 77)
    with tempfile.TemporaryDirectory() as d:     # bundled (jarjar) mods are not missing; an unmet need drops the mod
        d = pathlib.Path(d)
        def mk(name, toml, nested=()):
            p = d / name
            with zipfile.ZipFile(p, "w") as z:
                z.writestr("META-INF/neoforge.mods.toml", toml)
                for nn, nt in nested:
                    import io
                    b = io.BytesIO()
                    with zipfile.ZipFile(b, "w") as zz:
                        zz.writestr("META-INF/neoforge.mods.toml", nt)
                    z.writestr(f"META-INF/jarjar/{nn}", b.getvalue())
            return p
        dep = lambda m: f'[[dependencies.a]]\nmodId="{m}"\ntype="required"\n'
        big = mk("big.jar", '[[mods]]\nmodId="big"\n' + dep("inner"), [("inner.jar", '[[mods]]\nmodId="inner"\n')])
        ok &= _chk(jar_required(big.read_bytes()) == set(), 93)
        bad = mk("bad.jar", '[[mods]]\nmodId="bad"\n' + dep("helper") + dep("absent"))
        helper = mk("helper.jar", '[[mods]]\nmodId="helper"\n')
        selfj = mk("s.jar", '[[mods]]\nmodId="s"\n')
        man = {"port": "o/s", "files": [{"role": "self", "modid": "s", "name": "s.jar", "url": selfj.as_uri()},
                                        {"role": "optional", "modid": "big", "name": "big.jar", "url": big.as_uri()},
                                        {"role": "optional", "modid": "bad", "name": "bad.jar", "url": bad.as_uri()}]}
        g2 = lambda u: json.dumps(man).encode() if u == manifest_url("o/s", "b") else fetch(u)
        reg2 = {"helper": {"name": "helper.jar", "url": helper.as_uri(), "source": "modrinth"}}
        lk2 = lambda m: (reg2[m], None) if m in reg2 else (None, "nowhere")
        fs, _n, _p, _ne, pts, ms = plan(["o/s"], "b", True, False, g2, lookup=lk2, untested=True)
        fsn = plan(["o/s"], "b", True, False, g2, lookup=lk2)[0]
        ok &= _chk([f["modid"] for f in fsn] == ["s", "big"], 901)        # without --with-untested, no registry fetch
        ok &= _chk([f["modid"] for f in fs] == ["s", "big"], 104)                     # bad and its helper both left out
        ok &= _chk("needs absent" in ms.get("bad", "") and "only bad needed it" in ms.get("helper", ""), 105)
        nf = lambda v: f'[[dependencies.a]]\nmodId="neoforge"\ntype="required"\nversionRange="[{v},)"\n'
        newer = mk("newer.jar", '[[mods]]\nmodId="newer"\n' + nf("21.1.300"))
        other = mk("other.jar", '[[mods]]\nmodId="otherline"\n' + nf("22.0.1"))
        man3 = {"port": "o/n", "minecraft": "1.21.1", "neoforge": "21.1.9", "files": [
            {"role": "self", "modid": "s", "name": "s.jar", "url": selfj.as_uri()},
            {"role": "optional", "modid": "newer", "name": "newer.jar", "url": newer.as_uri()},
            {"role": "optional", "modid": "otherline", "name": "other.jar", "url": other.as_uri()}]}
        g3 = lambda u: json.dumps(man3).encode() if u == manifest_url("o/n", "b") else fetch(u)
        fs3, _n3, _p3, ne3, _pt3, ms3 = plan(["o/n"], "b", True, False, g3, lookup=lk2, untested=True)
        ok &= _chk([f["modid"] for f in fs3] == ["s", "newer"] and "outside" in ms3.get("otherline", ""), 115)
        ok &= _chk(ne3[-1][1] == "21.1.300" and "newer" in ne3[-1][2], 116)       # NeoForge raised for the added mod
    with tempfile.TemporaryDirectory() as d:     # macOS python.org Python with no CA bundle: curl takes over
        f = pathlib.Path(d, "m.json"); f.write_text('{"x": 1}', encoding="utf-8")
        real = urllib.request.urlopen
        def broken(*a, **k):
            raise urllib.error.URLError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
        urllib.request.urlopen = broken
        try:
            ok &= _chk(json.loads(fetch(f.as_uri())) == {"x": 1}, 124)
        finally:
            urllib.request.urlopen = real
    with tempfile.TemporaryDirectory() as d:     # NeoForge: detected, installed headless, or refused
        mc = pathlib.Path(d, "mc"); mods = mc / "mods"; mods.mkdir(parents=True)
        neo = [("1.21.1", "21.1.9", "o/a"), ("1.21.1", "21.1.20", "o/b")]
        inst = pathlib.Path(d, "inst.jar"); inst.write_bytes(b"jar")
        calls = []
        def fake_get(u):
            return hashlib.sha256(b"jar").hexdigest().encode() if u.endswith(".sha256") else b"jar"
        def fake_run(argv, **k):
            calls.append(argv); (mc / "versions/neoforge-21.1.20").mkdir(parents=True)
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        n, pr = ensure_neoforge(neo, mods, get=fake_get, run=fake_run)
        ok &= _chk(any("launcher_profiles" in x for x in pr) and not calls, 138)          # launcher never run: refuse
        (mc / "launcher_profiles.json").write_text("{}", encoding="utf-8")
        javadir = mc / "runtime/java-runtime-delta/x/bin"; javadir.mkdir(parents=True)
        fakejava = javadir / ("java.exe" if os.name == "nt" else "java")
        fakejava.write_text("", encoding="utf-8")
        global java_works
        real_works, java_works = java_works, (lambda j: True)    # a stand-in that "runs" on any OS
        try:
            n, pr = ensure_neoforge(neo, mods, get=fake_get, run=fake_run)
        finally:
            java_works = real_works
        ok &= _chk(not pr and calls and calls[0][0] == str(fakejava) and "--install-client" in calls[0], 149)
        ok &= _chk(any("21.1.20" in x and "installed" in x for x in n), 150)
        n, pr = ensure_neoforge(neo, mods, get=fake_get, run=fake_run)
        ok &= _chk(len(calls) == 1 and any("is installed" in x for x in n), 152)            # second run: nothing to do
        (mc / "versions/neoforge-21.1.20").rename(mc / "versions/neoforge-21.1.30")
        ok &= _chk(neoforge_installed(mc, "21.1.20") == "21.1.30" and neoforge_installed(mc, "21.1.31") is None, 154)
        bad = lambda u: b"0" * 64 if u.endswith(".sha256") else b"jar"
        shutil.rmtree(mc / "versions")
        _n, pr = ensure_neoforge(neo, mods, get=bad, run=fake_run)
        ok &= _chk(any("sha256" in x for x in pr) and len(calls) == 1, 158)
    ok &= _chk(vkey("geckolib-neoforge-1.21.1-4.8.4.jar") > vkey("geckolib-neoforge-1.21.1-4.7.1.jar"), 159)
    ok &= _chk(vkey("curios-neoforge-9.5.1+1.21.1.jar") == (9, 5, 1), 160)
    print("self-check:", "OK" if ok else f"FAIL (checks at self_check lines {failed})")
    return 0 if ok else 1


def main():
    if "--self-check" in sys.argv:
        return self_check()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("repos", nargs="+", help="fork ports, owner/repo or https://github.com/owner/repo")
    ap.add_argument("--mc", default="1.21.1"); ap.add_argument("--branch")
    ap.add_argument("--mods-dir")
    ap.add_argument("--with-optional", action="store_true", help="also the optional mods CI tested with the ports")
    ap.add_argument("--with-untested", action="store_true",
                    help="also optional integrations CI never loaded, from Modrinth/CurseForge, with what they need")
    ap.add_argument("--full", action="store_true"); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-neoforge", action="store_true", help="do not install NeoForge when it is missing")
    ap.add_argument("-v", "--verbose", action="store_true", help="also every file and every note")
    a = ap.parse_args()
    mods = pathlib.Path(a.mods_dir).expanduser() if a.mods_dir else default_mods_dir()
    cache = {}
    def get(u):                                      # each file is downloaded once, however often it is read
        if u not in cache:
            cache[u] = fetch(u)
        return cache[u]
    print(f"install-port: {', '.join(a.repos)} -> {mods}" + (" (dry run)" if a.dry_run else ""), flush=True)
    files, notes, problems, neo, ports, missing = plan(a.repos, a.branch or f"neoforge-{a.mc}", a.with_optional,
                                                       a.full, get, a.mc, untested=a.with_untested)
    done, skipped, bad, status = install(files, mods, a.dry_run, get)
    nnotes, nprob = ([], []) if (a.no_neoforge or not neo) else ensure_neoforge(neo, mods, a.dry_run, get)
    neo_line = nprob[0] if nprob else next((x for x in nnotes if not x.startswith("tested on")), "")
    if neo_line and not nprob and neo and "needs at least" in neo[-1][2]:
        tested = max((n[1] for n in neo[:-1]), key=vkey, default="?")
        neo_line += f" (newer than the {tested} the ports were tested on: {neo[-1][2]})"
    if a.verbose:
        print("\n".join(["  " + x for x in done + skipped] + ["  note: " + x for x in nnotes + notes + problems]) + "\n")
    out, good = summarise(ports, files, status, missing, bad, neo_line, not nprob, a.dry_run)
    print("\n".join(out))
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
