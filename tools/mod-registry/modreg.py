#!/usr/bin/env python3
"""
mod-registry CLI — a backend-agnostic front-end over Modrinth + CurseForge for the
install-mod pipeline. Emits JSON to stdout ONLY (never writes catalog data to disk,
per the CurseForge ToS). The CurseForge API key is read from .env.local and sent
only as a header — it is never printed.

Subcommands:
  search        --query "<text>" [--loader neoforge --mc 1.21.1] [--limit N]
  versions      --provider <p> --id <projectId> [--loader neoforge --mc 1.21.1]
  deps          --provider <p> --id <projectId> --file <fileId>
  download      --provider <p> --id <projectId> --file <fileId> --out <path>
  resolve-modid --jar <path>

Design notes:
  * loader/mc are ALWAYS arguments (default neoforge / 1.21.1) — nothing is hardcoded
    to a single target, so re-targeting later is free.
  * `versions` classifies a project against the target as:
      native        — a build for exactly (target loader, target mc) exists → download only
      needs-migrate — best source is an OLDER mc (e.g. forge 1.20.1) → run migrate-mod
      needs-downport— only NEWER 1.21.x builds exist → ask the user, then downport
      none          — nothing usable found
"""
import argparse
import json
import os
import re
import sys
import zipfile

import providers

DEFAULT_LOADER = "neoforge"
DEFAULT_MC = "1.21.1"


# ── MC version helpers ───────────────────────────────────────────────────────
def parse_mc(s):
    """"1.21.1" -> (1,21,1); "1.21" -> (1,21,0). Non-release strings -> None."""
    if not s:
        return None
    m = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?$", s.strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def _emit(obj):
    json.dump(obj, sys.stdout, indent=2)
    sys.stdout.write("\n")


# ── classification ───────────────────────────────────────────────────────────
def classify(files, target_loader, target_mc):
    Mt = parse_mc(target_mc)
    pairs = []  # (loader, mc_str, mc_tuple, file)
    for f in files:
        loaders = f.get("loaders") or [None]
        for l in loaders:
            for mc in (f.get("mcs") or []):
                t = parse_mc(mc)
                if t:
                    pairs.append((l, mc, t, f))

    def as_choice(p):
        l, mc, _, f = p
        return {"fileId": f["fileId"], "fileName": f.get("fileName"),
                "loader": l, "mc": mc, "sha1": f.get("sha1"),
                "downloadUrl": f.get("downloadUrl"),
                "datePublished": f.get("datePublished")}

    native = [p for p in pairs if p[0] == target_loader and p[2] == Mt]
    older = [p for p in pairs if p[2] < Mt]
    newer = [p for p in pairs if p[2] > Mt]

    newer_mcs = sorted({p[1] for p in newer}, key=lambda s: parse_mc(s))
    older_mcs = sorted({p[1] for p in older}, key=lambda s: parse_mc(s))

    if native:
        best = sorted(native, key=lambda p: (p[2], p[3].get("datePublished") or ""))[-1]
        cls = "native"
        chosen = as_choice(best)
    elif older:
        # Prefer target loader, then forge (the filled migration corpus), then newest older mc.
        best = sorted(older, key=lambda p: (
            0 if p[0] == target_loader else (1 if p[0] == "forge" else 2),
            tuple(-x for x in p[2]),
        ))[0]
        cls = "needs-migrate"
        chosen = as_choice(best)
    elif newer:
        # Only newer builds exist: prefer target loader, then the CLOSEST newer mc.
        best = sorted(newer, key=lambda p: (0 if p[0] == target_loader else 1, p[2]))[0]
        cls = "needs-downport"
        chosen = as_choice(best)
    else:
        cls = "none"
        chosen = None

    return {
        "classification": cls,
        "chosen": chosen,
        "has_native": bool(native),
        "has_older": bool(older),
        "has_newer": bool(newer),
        "older_mcs": older_mcs,
        "newer_mcs": newer_mcs,
    }


# ── search (merge + dedupe across backends) ──────────────────────────────────
def _norm(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def cmd_search(args):
    query = args.query
    loader = args.loader
    mc = args.mc
    warnings = []
    grouped = {}  # norm_name -> {name, description, categories, providers[]}

    for pname in ("modrinth", "curseforge"):
        try:
            prov = providers.get_provider(pname)
            if pname == "curseforge" and not prov.available():
                warnings.append("curseforge: CURSEFORGE_API_KEY not set — skipped")
                continue
            # Search unfiltered by mc/loader so we can classify ALL candidates
            # (a mod that only has an older build should still surface).
            hits = prov.search(query, loader=None, mc=None, limit=args.limit)
        except Exception as e:
            warnings.append(f"{pname}: {type(e).__name__}")
            continue
        for h in hits:
            key = _norm(h["name"])
            g = grouped.setdefault(key, {
                "name": h["name"], "description": h.get("description", ""),
                "categories": h.get("categories", []), "providers": [],
            })
            g["providers"].append({
                "provider": h["provider"], "id": h["id"], "slug": h.get("slug"),
                "downloads": h.get("downloads", 0), "url": h.get("url"),
            })

    results = list(grouped.values())
    for g in results:
        g["downloads_max"] = max((p["downloads"] for p in g["providers"]), default=0)
    results.sort(key=lambda g: g["downloads_max"], reverse=True)

    _emit({"query": query, "target": {"loader": loader, "mc": mc},
           "results": results[:args.limit], "warnings": warnings})


def cmd_versions(args):
    prov = providers.get_provider(args.provider)
    files = prov.list_files(args.id)
    proj = prov.project(args.id)
    result = classify(files, args.loader, args.mc)
    _emit({"provider": args.provider, "id": args.id, "name": proj.get("name"),
           "slug": proj.get("slug"), "target": {"loader": args.loader, "mc": args.mc},
           **result})


def cmd_deps(args):
    prov = providers.get_provider(args.provider)
    deps = prov.file_deps(args.id, args.file)
    _emit({"provider": args.provider, "id": args.id, "file": args.file,
           "dependencies": deps})


def cmd_download(args):
    prov = providers.get_provider(args.provider)
    res = prov.download(args.id, args.file, args.out)
    _emit(res)
    if res.get("status") == "opt-out":
        sys.exit(3)  # author opted out — orchestrator surfaces the manual link
    if res.get("status") == "ok" and res.get("sha1_ok") is False:
        sys.exit(4)  # hash mismatch


# ── resolve-modid (jar introspection, no network) ────────────────────────────
def cmd_resolve_modid(args):
    info = {"jar": args.jar, "loader": None, "modIds": [], "mcRange": None,
            "mixins": [], "jarjar": [], "hasNeoforgeToml": False, "hasForgeToml": False}
    with zipfile.ZipFile(args.jar) as z:
        names = z.namelist()
        toml_txt = None
        if "META-INF/neoforge.mods.toml" in names:
            info["hasNeoforgeToml"] = True
            info["loader"] = "neoforge"
            toml_txt = z.read("META-INF/neoforge.mods.toml").decode("utf-8", "replace")
        elif "META-INF/mods.toml" in names:
            info["hasForgeToml"] = True
            info["loader"] = "forge"
            toml_txt = z.read("META-INF/mods.toml").decode("utf-8", "replace")
        if toml_txt:
            info["modIds"] = re.findall(r'^\s*modId\s*=\s*"([^"]+)"', toml_txt, re.M)
            # first minecraft dependency versionRange, if any
            mrange = re.search(r'modId\s*=\s*"minecraft".*?versionRange\s*=\s*"([^"]+)"',
                               toml_txt, re.S)
            if mrange:
                info["mcRange"] = mrange.group(1)
        info["mixins"] = [n for n in names if re.match(r".*mixins.*\.json$", n)]
        info["jarjar"] = [n for n in names if n.startswith("META-INF/jarjar/") and n.endswith(".jar")]
    _emit(info)


def _read_modids_from_jar(path):
    ids = []
    loader = None
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            txt = None
            if "META-INF/neoforge.mods.toml" in names:
                loader = "neoforge"
                txt = z.read("META-INF/neoforge.mods.toml").decode("utf-8", "replace")
            elif "META-INF/mods.toml" in names:
                loader = "forge"
                txt = z.read("META-INF/mods.toml").decode("utf-8", "replace")
            elif "fabric.mod.json" in names:
                loader = "fabric"
                fj = json.loads(z.read("fabric.mod.json").decode("utf-8", "replace"))
                if fj.get("id"):
                    ids = [fj["id"]]
            if txt:
                ids = re.findall(r'^\s*modId\s*=\s*"([^"]+)"', txt, re.M)
    except Exception:
        pass
    return loader, ids


def cmd_scan_instance(args):
    """List modIds already present in the target instance, so the resolver can skip
    dependencies that are already installed. Reads MINECRAFT_MODS_DIR from .env.local
    unless --dir is given."""
    import glob
    directory = args.dir or providers.read_env_key("MINECRAFT_MODS_DIR")
    result = {"dir": directory, "mods": [], "modIds": []}
    if not directory:
        _emit({**result, "error": "MINECRAFT_MODS_DIR not set and --dir not given"})
        sys.exit(2)
    seen = set()
    for jar in sorted(glob.glob(os.path.join(directory, "*.jar"))):
        loader, ids = _read_modids_from_jar(jar)
        result["mods"].append({"file": os.path.basename(jar), "loader": loader, "modIds": ids})
        for i in ids:
            seen.add(i)
    result["modIds"] = sorted(seen)
    _emit(result)


def main():
    ap = argparse.ArgumentParser(prog="modreg")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search")
    s.add_argument("--query", required=True)
    s.add_argument("--loader", default=DEFAULT_LOADER)
    s.add_argument("--mc", default=DEFAULT_MC)
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(func=cmd_search)

    v = sub.add_parser("versions")
    v.add_argument("--provider", required=True)
    v.add_argument("--id", required=True)
    v.add_argument("--loader", default=DEFAULT_LOADER)
    v.add_argument("--mc", default=DEFAULT_MC)
    v.set_defaults(func=cmd_versions)

    d = sub.add_parser("deps")
    d.add_argument("--provider", required=True)
    d.add_argument("--id", required=True)
    d.add_argument("--file", required=True)
    d.set_defaults(func=cmd_deps)

    dl = sub.add_parser("download")
    dl.add_argument("--provider", required=True)
    dl.add_argument("--id", required=True)
    dl.add_argument("--file", required=True)
    dl.add_argument("--out", required=True)
    dl.set_defaults(func=cmd_download)

    rm = sub.add_parser("resolve-modid")
    rm.add_argument("--jar", required=True)
    rm.set_defaults(func=cmd_resolve_modid)

    si = sub.add_parser("scan-instance")
    si.add_argument("--dir", default=None, help="mods dir (default: MINECRAFT_MODS_DIR from .env.local)")
    si.set_defaults(func=cmd_scan_instance)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
