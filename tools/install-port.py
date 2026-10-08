#!/usr/bin/env python3
"""Install fork ports (tools/port-offer.py) into a Minecraft instance with their dependencies -- the exact files CI tested.

    python3 tools/install-port.py <owner/repo> [<owner/repo> ...] [--mc 1.21.1] [--mods-dir DIR]
                                  [--with-optional] [--full] [--dry-run]

Each fork port carries `.github/port-install.json` on its `neoforge-<mc>` branch: the port's release jar, every
required dependency at the version CI ran (direct URL + sha256), optional integrations, and what the full CI
pass added. This reads it, follows other ports it depends on (their own manifests, recursively), skips any
mod whose id is already in the mods folder, downloads the rest, and checks every sha256 before moving a file
in -- a mismatch is never installed. It says which NeoForge the ports were tested on and whether that version
is installed; it does not install NeoForge itself (that is the official installer's job).

  --with-optional   also the optional integrations (only the ones with a tested file)
  --full            also what CI's full pass added (an exact replica of the tested setup)
  --dry-run         say what would happen, change nothing

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
            z = zipfile.ZipFile(j)
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


def jar_required(data):
    """The modIds a mod jar's mods.toml marks required."""
    import io
    z = zipfile.ZipFile(io.BytesIO(data))
    t = "".join(z.read(n).decode("utf-8", "replace") for n in z.namelist()
                if n in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"))
    out = set()
    for blk in re.split(r"(?m)^\s*\[\[", t):
        if blk.startswith("dependencies."):
            m = re.search(r'modId\s*=\s*"([^"]+)"', blk)
            req = re.search(r'type\s*=\s*"required"', blk) or re.search(r"mandatory\s*=\s*true", blk)
            if m and req and m.group(1) not in ("minecraft", "neoforge", "forge", "java"):
                out.add(m.group(1))
    return out


def vkey(name):
    """Sortable version from a jar name or version string: the numbers in order (geckolib-...-4.8.4.jar > 4.7.1)."""
    nums = re.findall(r"\d+", re.sub(r"(?:neoforge|forge)-?1\.\d+(?:\.\d+)?|mc1\.\d+(?:\.\d+)?|\+1\.\d+(?:\.\d+)?", "",
                                    name or ""))
    return tuple(int(n) for n in nums)


def plan(repos, branch, with_optional, full, get=fetch):
    """-> (files to install in order, notes, problems). Ports first-come; a mod named twice is installed once."""
    with_optional = with_optional or full          # an exact replica includes the optional integrations
    roles = {"self", "required"} | ({"optional"} if with_optional else set()) | ({"extra"} if full else set())
    files, seen, notes, problems, mans, queue, conflicts = [], {}, [], [], [], list(repos), {}
    extras = {}                                     # modid -> an "extra" file, in case an optional mod needs it
    visited = set()
    while queue:
        repo = queue.pop(0)
        url = manifest_url(repo, branch)
        if url in visited:
            continue
        visited.add(url)
        try:
            man = json.loads(get(url).decode("utf-8"))
        except Exception as e:
            problems.append(f"{repo}: no port manifest at {url} ({type(e).__name__}: {getattr(e, 'reason', e)})")
            continue
        mans.append(man)
        if not any(f.get("role") == "self" for f in man.get("files", [])):
            problems.append(f"{man.get('port', repo)}: its release has several jars and the manifest names none")
        for f in man.get("files", []):
            if f.get("role") == "extra" and f.get("url"):
                extras.setdefault(f.get("modid") or f.get("name"), dict(f, from_port=man.get("port", repo)))
            if f.get("role") not in roles:
                continue
            mid = f.get("modid") or f.get("name")
            if mid in seen:
                old = seen[mid]
                if f.get("url") and old.get("url") and f["url"] != old["url"]:
                    if vkey(f.get("name")) > vkey(old.get("name")):     # ports disagree: the newest tested file
                        newf = dict(f, from_port=man.get("port", repo))
                        files[next(i for i, x in enumerate(files) if x is old)] = newf
                        seen[mid] = newf
                    if f.get("name") != old.get("name"):     # the same file from two URLs is not a conflict
                        conflicts.setdefault(mid, set()).update({old.get("name"), f.get("name")})
                continue
            if not f.get("url"):
                (problems if f["role"] in ("self", "required") else notes).append(
                    f"{mid} ({f['role']}): no tested file -- get it by hand: {f.get('page', '?')}")
                continue
            entry = dict(f, from_port=man.get("port", repo))
            seen[mid] = entry
            files.append(entry)
            if f.get("port") and f["role"] != "self":
                queue.append(f["port"])                 # a port this one depends on: its own requirements too
    # an optional mod brings its own requirements, which CI loaded as "extra": read them from its jar
    checked = set()
    while with_optional and not full:
        todo = [f for f in files if f["role"] in ("optional", "extra") and f["modid"] not in checked]
        if not todo:
            break
        for f in todo:
            checked.add(f["modid"])
            try:
                need = jar_required(get(f["url"]))
            except Exception:
                continue
            missing = need - set(seen) - set(extras)
            for k, e in list(extras.items()):         # an extra named by its build coordinate: read its modIds
                if missing and not e.get("_read"):
                    e["_read"] = True
                    try:
                        for m in jar_modids(get(e["url"])):
                            extras.setdefault(m, dict(e, modid=m))
                    except Exception:
                        pass
            for d in sorted(need - set(seen)):
                if d in extras:
                    seen[d] = extras[d]; files.append(extras[d])
                    notes.append(f"{d}: added because {f['modid']} requires it")
    for mid, names in sorted(conflicts.items()):
        notes.append(f"{mid}: the ports were tested with {', '.join(sorted(n for n in names if n))}; "
                     f"installing the newest, {seen[mid]['name']}")
    neo = sorted({(m.get("minecraft"), m.get("neoforge"), m.get("port")) for m in mans if m.get("neoforge")},
                 key=lambda t: vkey(t[1]))
    return files, notes, problems, neo


def install(files, mods_dir, dry=False, get=fetch):
    have = installed_modids(mods_dir)
    done, skipped, bad = [], [], []
    for f in files:
        mid = f.get("modid") or f["name"]
        if mid in have:
            skipped.append(f"{mid}: already in the mods folder ({have[mid]})")
            continue
        if dry:
            done.append(f"{mid}: would install {f['name']}")
            continue
        try:
            data = get(f["url"])
        except Exception as e:
            bad.append(f"{mid}: download failed ({type(e).__name__}: {e})"); continue
        sha = hashlib.sha256(data).hexdigest()
        if f.get("sha256") and sha != f["sha256"]:
            bad.append(f"{mid}: sha256 mismatch -- not the file CI tested, not installed"); continue
        mods_dir.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^\w.+-]", "_", f.get("name") or f"{mid}.jar")
        tmp = tempfile.NamedTemporaryFile(dir=mods_dir, suffix=".part", delete=False)
        tmp.write(data); tmp.close()
        os.replace(tmp.name, mods_dir / name)
        done.append(f"{mid}: installed {name}" + ("" if f.get("sha256") else " (no sha256 in the manifest)"))
    return done, skipped, bad


def neoforge_note(neo, mods_dir):
    """One instance has one NeoForge: the newest version any of the ports was tested on (21.1.x is compatible
    within its line), and which port was tested on which when they differ."""
    if not neo:
        return []
    mc, v, _p = neo[-1]
    out = []
    if len({n[1] for n in neo}) > 1:
        out.append("tested on different NeoForge versions (" + "; ".join(f"{p}: {n}" for _m, n, p in neo)
                   + f") -- use the newest, {v}")
    versions = mods_dir.parent / "versions"
    if (versions / f"neoforge-{v}").is_dir():
        out.append(f"NeoForge {v} (Minecraft {mc}) is installed.")
    else:
        have = sorted(p.name for p in versions.glob("neoforge-*")) if versions.is_dir() else []
        out.append(f"Needs NeoForge {v} (Minecraft {mc}); " + (f"this instance has {', '.join(have)}. "
                   if have else "none found in this instance. ")
                   + f"Installer: https://maven.neoforged.net/releases/net/neoforged/neoforge/{v}/neoforge-{v}-installer.jar")
    return out


def self_check():
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
            {"role": "required", "modid": "ghost", "page": "https://example.invalid"}]}
        mans = {manifest_url("o/me", "b"): meman, manifest_url("o/lib", "b"): libman}
        get = lambda u: json.dumps(mans[u]).encode() if u in mans else fetch(u)
        files, notes, problems, neo = plan(["o/me"], "b", False, False, get)
        ok &= [f["modid"] for f in files] == ["me", "lib", "base"]           # the sibling's own requirement too
        ok &= any("ghost" in p for p in problems) and {n[1] for n in neo} == {"21.1.1"}
        mods = d / "inst/mods"
        done, skipped, bad = install(files, mods, get=get)
        ok &= len(done) == 3 and not bad and sorted(installed_modids(mods)) == ["base", "lib", "me"]
        done2, skipped2, _ = install(files, mods, get=get)
        ok &= not done2 and len(skipped2) == 3                               # idempotent: nothing twice
        lib2 = jar("lib-2.jar", "lib")
        mans[manifest_url("o/other", "b")] = {"port": "o/other", "minecraft": "1.21.1", "neoforge": "21.1.9", "files": [
            {"role": "self", "modid": "other", "name": "other.jar", "url": me.as_uri()},
            {"role": "required", "modid": "lib", "name": "lib-2.jar", "url": lib2.as_uri()}]}
        f3, n3, _p3, neo3 = plan(["o/me", "o/other"], "b", False, False, get)
        ok &= next(f for f in f3 if f["modid"] == "lib")["name"] == "lib-2.jar" and any("newest" in n for n in n3)
        ok &= neo3[-1][1] == "21.1.9"
        withdep = d / "src" / "optdep.jar"
        with zipfile.ZipFile(withdep, "w") as z:
            z.writestr("META-INF/neoforge.mods.toml", '[[mods]]\nmodId="optdep"\n[[dependencies.optdep]]\n'
                       'modId="needed"\ntype="required"\n[[dependencies.optdep]]\nmodId="neoforge"\ntype="required"\n')
        needed = jar("needed.jar", "needed")
        mans[manifest_url("o/x", "b")] = {"port": "o/x", "files": [
            {"role": "self", "modid": "x", "name": "x.jar", "url": me.as_uri()},
            {"role": "optional", "modid": "optdep", "name": "optdep.jar", "url": withdep.as_uri()},
            {"role": "extra", "modid": "g:needed-123", "name": "needed.jar", "url": needed.as_uri()},
            {"role": "extra", "modid": "unrelated", "name": "u.jar", "url": needed.as_uri()}]}
        f4, n4, _p4, _ = plan(["o/x"], "b", True, False, get)
        ok &= [f["modid"] for f in f4] == ["x", "optdep", "needed"] and any("requires it" in n for n in n4)
        ok &= [f["modid"] for f in plan(["o/x"], "b", False, False, get)[0]] == ["x"]
        ok &= {f["modid"] for f in plan(["o/x"], "b", False, True, get)[0]} == {"x", "optdep", "g:needed-123", "unrelated"}
        f2, _n, _p, _ = plan(["o/me"], "b", True, False, get)
        _d, _s, bad2 = install([f for f in f2 if f["modid"] == "opt"], mods, get=get)
        ok &= bad2 and "sha256 mismatch" in bad2[0] and "opt" not in installed_modids(mods)
    with tempfile.TemporaryDirectory() as d:     # macOS python.org Python with no CA bundle: curl takes over
        f = pathlib.Path(d, "m.json"); f.write_text('{"x": 1}', encoding="utf-8")
        real = urllib.request.urlopen
        def broken(*a, **k):
            raise urllib.error.URLError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
        urllib.request.urlopen = broken
        try:
            ok &= json.loads(fetch(f.as_uri())) == {"x": 1}
        finally:
            urllib.request.urlopen = real
    ok &= vkey("geckolib-neoforge-1.21.1-4.8.4.jar") > vkey("geckolib-neoforge-1.21.1-4.7.1.jar")
    ok &= vkey("curios-neoforge-9.5.1+1.21.1.jar") == (9, 5, 1)
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    if "--self-check" in sys.argv:
        return self_check()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("repos", nargs="+", help="fork ports, owner/repo or https://github.com/owner/repo")
    ap.add_argument("--mc", default="1.21.1"); ap.add_argument("--branch")
    ap.add_argument("--mods-dir"); ap.add_argument("--with-optional", action="store_true")
    ap.add_argument("--full", action="store_true"); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    mods = pathlib.Path(a.mods_dir).expanduser() if a.mods_dir else default_mods_dir()
    files, notes, problems, neo = plan(a.repos, a.branch or f"neoforge-{a.mc}", a.with_optional, a.full)
    print(f"install-port: {len(files)} file(s) for {', '.join(a.repos)} -> {mods}" + (" (dry run)" if a.dry_run else ""))
    done, skipped, bad = install(files, mods, a.dry_run)
    for line in done + skipped:
        print("  " + line)
    for line in neoforge_note(neo, mods) + notes:
        print("  note: " + line)
    for line in problems + bad:
        print("  PROBLEM: " + line)
    return 1 if (problems or bad) else 0


if __name__ == "__main__":
    sys.exit(main())
