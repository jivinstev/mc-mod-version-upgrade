#!/usr/bin/env python3
"""Make sure one Minecraft version is fully present in a launcher directory -- and fetch only
what is missing.

    python3 tools/ensure-vanilla.py 1.21.1 --dir "~/.minecraft" --check   # report, change nothing
    python3 tools/ensure-vanilla.py 1.21.1 --dir "~/.minecraft"           # fetch what is missing

WHY THIS EXISTS
    A freshly installed mod loader has the LOADER's libraries but not Minecraft's own: those, and the
    version's asset index, are fetched by the launcher on the first launch of that version. Measured
    on a 26.2 instance created by hand: 54 libraries were missing on the first attempt, then 101
    libraries and 51 of 5,057 asset objects had to be downloaded. Without this step the instance
    preflights clean and will not launch, which is the worst combination.

    What decides the work is NOT whether this is your first instance or your fifth. It is whether
    this exact Minecraft version has ever been fully launched here. So there is one idempotent
    check over five artefacts, and each one is fetched only if it is absent or fails its sha1:

      version JSON   version_manifest_v2.json -> the per-version URL (+ sha1)
      client jar     downloads.client (+ sha1)
      libraries      libraries[].downloads.artifact (+ sha1), filtered by this OS's rules
      asset index    assetIndex (+ sha1)
      asset objects  resources.download.minecraft.net/<2>/<hash>, content-addressed, so they are
                     shared across versions and usually mostly present already

    Everything comes from Mojang's public manifests over plain HTTPS with pinned sha1s. No account
    is needed. What this can NOT do, and says so: install a launcher, or sign in to one.

Standard library only. Exit: 0 complete (or --check found nothing missing), 1 something missing or
failed verification, 2 could not run.
"""
import argparse, concurrent.futures, hashlib, json, os, pathlib, platform, sys, urllib.request

MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
OBJECTS = "https://resources.download.minecraft.net"
OS_NAME = {"Darwin": "osx", "Windows": "windows"}.get(platform.system(), "linux")


def sha1_file(p):
    h = hashlib.sha1()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def fetch(url, dest, sha1):
    """Download to a temp file beside dest, verify, then move into place. Never leaves a partial."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
    got = sha1_file(tmp)
    if sha1 and got != sha1:
        tmp.unlink()
        raise ValueError(f"sha1 mismatch for {url}: got {got}, want {sha1}")
    os.replace(tmp, dest)


def get_json(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def present(p, sha1):
    return p.is_file() and (not sha1 or sha1_file(p) == sha1)


def rules_allow(rules):
    if not rules:
        return True
    ok = False
    for r in rules:
        os_rule = r.get("os", {})
        if "features" in r:
            continue                          # launcher features (demo mode, custom resolution)
        if os_rule and os_rule.get("name") not in (None, OS_NAME):
            continue
        ok = r.get("action") == "allow"
    return ok


def plan(version, root):
    """-> list of (label, url, dest, sha1) for every artefact this version needs."""
    manifest = get_json(MANIFEST)
    entry = next((v for v in manifest["versions"] if v["id"] == version), None)
    if entry is None:
        raise SystemExit(f"ensure-vanilla: {version} is not in Mojang's version manifest")
    vdir = root / "versions" / version
    vjson = vdir / f"{version}.json"
    items = [("version json", entry["url"], vjson, entry.get("sha1"))]
    if not present(vjson, entry.get("sha1")):
        fetch(entry["url"], vjson, entry.get("sha1"))          # everything else is read from it
    v = json.loads(vjson.read_text(encoding="utf-8"))
    c = v["downloads"]["client"]
    items.append(("client jar", c["url"], vdir / f"{version}.jar", c["sha1"]))
    for lib in v.get("libraries", []):
        art = lib.get("downloads", {}).get("artifact")
        if art and rules_allow(lib.get("rules")):
            items.append(("library", art["url"], root / "libraries" / art["path"], art["sha1"]))
    ai = v["assetIndex"]
    idx = root / "assets" / "indexes" / f"{ai['id']}.json"
    items.append(("asset index", ai["url"], idx, ai["sha1"]))
    if not present(idx, ai["sha1"]):
        fetch(ai["url"], idx, ai["sha1"])
    for obj in json.loads(idx.read_text(encoding="utf-8"))["objects"].values():
        h = obj["hash"]
        items.append(("asset object", f"{OBJECTS}/{h[:2]}/{h}", root / "assets" / "objects" / h[:2] / h, h))
    return items


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("version")
    ap.add_argument("--dir", required=True, help="the launcher directory (holds versions/, libraries/, assets/)")
    ap.add_argument("--check", action="store_true", help="report what is missing; download nothing "
                    "(the version JSON and asset index are still read, since they say what is needed)")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--only", action="append", default=[], metavar="KIND",
                    choices=["version json", "client jar", "library", "asset index", "asset object"],
                    help="restrict to one artefact kind (repeatable)")
    a = ap.parse_args()
    root = pathlib.Path(a.dir).expanduser()
    if not root.is_dir():
        print(f"ensure-vanilla: {root} does not exist", file=sys.stderr)
        return 2
    try:
        items = plan(a.version, root)
    except (OSError, ValueError) as e:
        print(f"ensure-vanilla: could not read the manifests: {e}", file=sys.stderr)
        return 2
    if a.only:
        items = [it for it in items if it[0] in a.only]
    missing = [it for it in items if not present(it[2], it[3])]
    by_kind = {}
    for label, *_ in items:
        by_kind.setdefault(label, [0, 0])[0] += 1
    for label, *_ in missing:
        by_kind[label][1] += 1
    for label, (n, m) in by_kind.items():
        print(f"  {label:13} {n - m:>5} of {n:<5} present" + (f"   ({m} missing)" if m else ""))
    if not missing:
        print(f"ensure-vanilla: {a.version} is complete in {root}" + (f" (checked: {', '.join(a.only)})" if a.only else ""))
        return 0
    if a.check:
        print(f"ensure-vanilla: {len(missing)} artefact(s) missing -- run without --check to fetch them")
        return 1
    failed = []
    with concurrent.futures.ThreadPoolExecutor(a.jobs) as ex:
        futs = {ex.submit(fetch, url, dest, sha1): (label, url) for label, url, dest, sha1 in missing}
        for f in concurrent.futures.as_completed(futs):
            try:
                f.result()
            except (OSError, ValueError) as e:
                failed.append(f"{futs[f][0]}: {e}")
    print(f"ensure-vanilla: fetched {len(missing) - len(failed)} of {len(missing)} missing artefact(s)")
    for fl in failed[:20]:
        print(f"  FAILED {fl}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
