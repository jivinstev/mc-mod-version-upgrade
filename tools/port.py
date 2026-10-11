#!/usr/bin/env python3
"""Port a mod with one command: find it, check it needs porting, set it up, run every hop, run every gate.

    python3 tools/port.py "Some Mod" --to 26.2
    python3 tools/port.py path/to/mod.jar --to 1.21.1
    python3 tools/port.py "Some Mod" --to 26.2                # again: resumes where it stopped

This is the deterministic part of a port, in a fixed order, so two runs do the same steps:

  resolve   the jar: a path you give, or the registries (tools/mod-registry/modreg.py), matching the name
            EXACTLY (search results are ranked by downloads, not by match). STOPS if a build for the target
            already exists: there is nothing to port, only something to install.
  deps      the source file's required dependencies. STOPS when one has no build for the target: it has
            to be ported (or pinned) first, or this port cannot load.
  download  into ~/.mc-mod-upgrade/jars (never into mods/), sha1-checked.
  route     tools/route.py: the chain of hops, e.g. Forge 1.20.1 -> NeoForge 1.21.1 -> 26.2. STOPS when a
            hop has no recipe pack, unless --allow-no-pack: the workers would do that whole hop, which is
            the expensive case and the person's call.
  setup     scaffold mods/<modid> from templates/neoforge-mod, decompile (Vineflower), make the names
            official (SRG or intermediary remap), the existing codemods, metadata, the datapack layout,
            the hoisted-config-SPEC fix (CATALOG §A #3b), and the Gate B baseline GameTest.
  hops      each hop is FINISHED before the next starts (tools/run-port.py: recipes -> compile loop ->
            Gate B): a hop's pack was measured on code that compiles at that hop's start. The first hop
            also writes the behaviour tests and the client harness, so later hops port them with the mod.
            An era hop runs tools/era-hop.py first, then tools/mechanical-hop.py (member renames and every
            converter, the stage the fork route runs too). The last hop runs Gate C and the visual review.
  report    port-report.json: every hop's cost and result, and the total.

Every stage records itself in mods/<modid>/port-state.json, so a rerun skips what is done. When a stage
cannot finish, it prints a STOPPED block (what is left, what it cost, what continuing would cost, the
choices) and exits non-zero -- it never falls back to hand-porting on its own. The person, or the
session driving this, decides. Exit codes: 0 done; 10-12 a hop stopped (see run-port.py); 20 a target
build already exists; 21 a dependency needs porting first; 22 a hop has no pack; 23 not found or
ambiguous; 24 setup failed; 2 bad input. Standard library only; the workers need the `claude` CLI.
"""
import argparse, collections, json, os, pathlib, re, shutil, subprocess, sys, zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import importlib.util  # noqa: E402

_s = importlib.util.spec_from_file_location("route", ROOT / "tools/route.py")
route = importlib.util.module_from_spec(_s); _s.loader.exec_module(route)
MODREG = ROOT / "tools/mod-registry/modreg.py"
TARGET_NAME = {"1.21.1": "NeoForge 1.21.1", "26.2": "NeoForge 26.2"}


def ensure_ca_bundle():
    """The python.org Python on macOS starts with an empty certificate store, so every download a child tool
    makes (registry search, mapping files) fails CERTIFICATE_VERIFY_FAILED. Point SSL_CERT_FILE at a bundle
    for this process and every tool it starts, when one is needed and none is set."""
    sys.path.insert(0, str(ROOT / "tools/mod-registry"))
    try:
        import providers
        b = providers.ca_bundle()
    except Exception:  # noqa: BLE001 -- a missing helper must not stop a port that does not need it
        b = None
    if b:
        os.environ["SSL_CERT_FILE"] = b
    return b


def say(msg):
    print(f"port: {msg}", flush=True)


def stop(code, what, choices, state=None, work=None):
    print(f"STOPPED: {what}")
    if choices:
        print("Choices (ask the person; do not pick one silently):")
        for c in choices:
            print(f"  - {c}")
    if state is not None and work is not None:
        state["stopped"] = {"code": code, "what": what}
        save_state(work, state)
    return code


def modreg(*args, timeout=180):
    r = subprocess.run([sys.executable, str(MODREG), *map(str, args)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    try:
        return r.returncode, json.loads(r.stdout)
    except ValueError:
        return r.returncode, {"error": (r.stdout + r.stderr)[-500:]}


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def load_state(work):
    p = work / "port-state.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"done": []}


def save_state(work, state):
    work.mkdir(parents=True, exist_ok=True)
    (work / "port-state.json").write_text(json.dumps(state, indent=1), encoding="utf-8")


# ── resolve / deps / download ────────────────────────────────────────────────
def resolve(query, target, providers=("modrinth", "curseforge")):
    """-> (provider, id, versions-json) or (None, None, reason)."""
    rc, d = modreg("search", "--query", query, "--limit", "20")
    if "results" not in d:
        return None, None, f"search failed: {d.get('error', rc)}"
    exact = [r for r in d["results"] if norm(r.get("name")) == norm(query)]
    if not exact:
        if not d["results"] and d.get("warnings"):
            # nothing came back because the search itself failed: say why, never "no match"
            return None, None, ("the registry search FAILED, so this is not a 'no match': " + "; ".join(d["warnings"])
                                + (" -- a certificate problem: set SSL_CERT_FILE to your CA bundle (e.g. /etc/ssl/cert.pem)"
                                   if any("CERTIFICATE" in w.upper() for w in d["warnings"]) else ""))
        names = ", ".join(r.get("name", "?") for r in d["results"][:8])
        return None, None, f"no result named exactly {query!r}; closest: {names or '(none)'}"
    best = None
    for prov in providers:
        if prov == "curseforge" and not os.environ.get("CURSEFORGE_API_KEY"):
            continue
        for r in exact:
            for p in r.get("providers", []):
                if p.get("provider") == prov:
                    best = (prov, str(p.get("slug") if prov == "modrinth" else p.get("id")))
                    break
            if best:
                break
        if best:
            break
    if not best:
        return None, None, "found only on a provider this machine cannot use (CurseForge needs CURSEFORGE_API_KEY)"
    rc, v = modreg("versions", "--provider", best[0], "--id", best[1], "--loader", "neoforge", "--mc", target)
    if "classification" not in v:
        return None, None, f"versions failed: {v.get('error', rc)}"
    return best[0], best[1], v


def parse_mods_toml(text):
    """(mods, deps) from a mods.toml / neoforge.mods.toml with a real TOML parser: [[mods]] blocks and the
    `mods = [ { modId = ... } ]` inline form are both legal, and single quotes too. None when it does not parse
    (callers keep their regex reading). mods: [dict]; deps: [(owner, dict)]."""
    try:
        import tomllib
        d = tomllib.loads(text)
    except Exception:  # noqa: BLE001 -- unparseable (a template placeholder, a typo): the regex path reads it
        return None
    mods = [m for m in d.get("mods", []) if isinstance(m, dict)]
    deps = [(owner, x) for owner, lst in (d.get("dependencies") or {}).items() if isinstance(lst, list)
            for x in lst if isinstance(x, dict)]
    return mods, deps, d


def read_jar_meta(jar):
    """mods.toml / neoforge.mods.toml / fabric.mod.json fields we carry into the scaffold."""
    meta = {"deps": [], "mixins": []}
    with zipfile.ZipFile(jar) as z:
        names = z.namelist()
        toml = next((n for n in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml") if n in names), None)
        if toml:
            t = z.read(toml).decode("utf-8", "replace")
            parsed = parse_mods_toml(t)
            if parsed and parsed[0]:            # a real TOML parse: [[mods]] or inline tables, either quote
                m0 = parsed[0][0]
                for k in ("modId", "version", "displayName", "authors", "description"):
                    v = m0.get(k)
                    meta[k] = v.strip() if isinstance(v, str) else None
                lic = parsed[2].get("license")
                meta["license"] = lic if isinstance(lic, str) else None
                for _owner, x in parsed[1]:
                    dep = x.get("modId")
                    if dep in (None, "forge", "neoforge", "minecraft"):
                        continue
                    req = x.get("mandatory") is True or str(x.get("type", "")).lower() == "required"
                    meta["deps"].append({"modId": dep, "required": req})
                meta["mixins"] = [m.get("config") for m in parsed[2].get("mixins", [])
                                  if isinstance(m, dict) and isinstance(m.get("config"), str)]
            else:
                mods = t.split("[[mods]]", 1)[1].split("[[", 1)[0] if "[[mods]]" in t else t

                def field(k, src):
                    m = re.search(rf'^\s*{k}\s*=\s*(?:"""|\'\'\')(.*?)(?:"""|\'\'\')', src, re.S | re.M) or \
                        re.search(rf'^\s*{k}\s*=\s*"([^"\n]*)"', src, re.M) or re.search(rf"^\s*{k}\s*=\s*'([^'\n]*)'", src, re.M)
                    return m.group(1).strip() if m else None
                for k in ("modId", "version", "displayName", "authors", "description"):
                    meta[k] = field(k, mods)
                meta["license"] = field("license", t)
                for blk in re.split(r"\[\[dependencies\.[^\]]+\]\]", t)[1:]:
                    dep = field("modId", blk)
                    if dep in (None, "forge", "neoforge", "minecraft"):
                        continue
                    mandatory = re.search(r'^\s*mandatory\s*=\s*true', blk, re.M) or re.search(r'^\s*type\s*=\s*"required"', blk, re.M)
                    meta["deps"].append({"modId": dep, "required": bool(mandatory)})
                meta["mixins"] = re.findall(r'config\s*=\s*"([^"]+\.json)"', t)
        meta["mixins"] = sorted(set(meta["mixins"]) | {n for n in names if n.endswith(".mixins.json") and "/" not in n})
        if "fabric.mod.json" in names:
            f = json.loads(z.read("fabric.mod.json").decode("utf-8", "replace"))
            fab = {"modId": f.get("id"), "version": f.get("version"), "displayName": f.get("name"),
                   "description": f.get("description"), "authors": ", ".join(
                       a if isinstance(a, str) else a.get("name", "") for a in f.get("authors", [])),
                   "license": f.get("license") if isinstance(f.get("license"), str) else None}
            # a multi-loader jar carries both: the (neo)forge toml is the one this port runs on, so the Fabric
            # file only fills what the toml left out (its id can differ, and a hyphen is not a legal NeoForge id)
            meta.update({k: v for k, v in fab.items() if not (toml and meta.get(k))})
            meta["mixins"] = sorted(set(meta["mixins"]) | {m if isinstance(m, str) else m.get("config") for m in f.get("mixins", [])})
    if meta.get("version") and "${" in meta["version"]:
        meta["version"] = None
    return meta


# ── setup ───────────────────────────────────────────────────────────────────
def _clip(t, head=2000, tail=4000):
    """Keep where an output STARTS as well as where it ends: a stack trace's first lines name the exception."""
    return t if len(t) <= head + tail else t[:head] + f"\n[... {len(t) - head - tail} chars ...]\n" + t[-tail:]


def run(cmd, cwd=None, log=None, timeout=3600):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if log:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(f"$ {' '.join(map(str, cmd))}\n{_clip(r.stdout)}{_clip(r.stderr)}\n")
    return r


def missing_classes(jar, raw):
    """Top-level classes in the jar with no .java in the decompile: Vineflower can fail to WRITE a class (a huge
    registration class, under memory pressure) and say so only in a stack trace, and the class is then simply
    absent -- one mod's 2401 errors were one such class (X79)."""
    with zipfile.ZipFile(jar) as z:
        names = [n[:-6] for n in z.namelist() if n.endswith(".class") and "$" not in n
                 and not n.startswith("META-INF/") and not n.endswith(("module-info.class", "package-info.class"))]
    return sorted(n for n in names if not (raw / f"{n}.java").exists())


def recover_missing_classes(jar, raw, vf, log):
    """Retry the classes the decompile dropped: Vineflower again with room (stack, heap), then CFR. -> still missing"""
    miss = missing_classes(jar, raw)
    if not miss:
        return []
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(f"decompile: {len(miss)} class(es) missing after Vineflower: {', '.join(miss[:8])}; retrying\n")
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        run(["java", "-Xss64m", "-Xmx8g", "-jar", str(vf), "-dgs=1", "-rsy=1", "-rbr=1", str(jar), t], log=log, timeout=7200)
        for n in miss:
            f = pathlib.Path(t, f"{n}.java")
            if f.exists():
                (raw / f"{n}.java").parent.mkdir(parents=True, exist_ok=True); shutil.copy2(f, raw / f"{n}.java")
    miss = missing_classes(jar, raw)
    cfr = ROOT / "tools/cfr.jar"
    if miss and cfr.exists():
        with tempfile.TemporaryDirectory() as t:
            for n in miss:
                run(["java", "-Xss64m", "-jar", str(cfr), str(jar), "--outputdir", t,
                     "--jarfilter", "^" + re.escape(n.replace("/", ".")) + "$"], log=log, timeout=1800)
                f = pathlib.Path(t, f"{n}.java")
                if f.exists():
                    (raw / f"{n}.java").parent.mkdir(parents=True, exist_ok=True); shutil.copy2(f, raw / f"{n}.java")
        miss = missing_classes(jar, raw)
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(f"decompile: {len(miss)} class(es) still missing after the retries" + (f": {', '.join(miss)}" if miss else "") + "\n")
    return miss


def fix_hoisted_spec(src):
    """CATALOG §A #3b: Vineflower hoists `SPEC = BUILDER.build()` above the BUILDER.define(...) fields, so the
    spec builds empty and every config value NPEs at first read. Move the line to just before the class's
    closing brace (never 'after the last BUILDER line', which splits a multi-line defineList)."""
    fixed = []
    for f in src.rglob("*.java"):
        t = f.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'^[ \t]*(?:public|private|protected)?[ \t]*static[ \t]+final[ \t]+\w*ConfigSpec[ \t]+\w+[ \t]*=[ \t]*'
                      r'\w*BUILDER\.build\(\);[ \t]*\n', t, re.M)
        if not m:
            continue
        rest = t[m.end():]
        if not re.search(r'\bBUILDER\b(?!\.build\(\))', rest):
            continue
        body = t[:m.start()] + rest
        k = body.rstrip().rfind("}")
        if k < 0:
            continue
        line = m.group(0).strip()
        body = body[:k].rstrip() + "\n\n   " + line + "\n" + body[k:]
        f.write_text(body, encoding="utf-8")
        fixed.append(f.relative_to(src).as_posix())
    return fixed


FORGE_CONDITIONS = {"forge:mod_loaded": "neoforge:mod_loaded", "forge:not": "neoforge:not", "forge:and": "neoforge:and",
                    "forge:or": "neoforge:or", "forge:true": "neoforge:true", "forge:false": "neoforge:false",
                    "forge:tag_empty": "neoforge:tag_empty", "forge:item_exists": "neoforge:item_exists"}


def neoforge_conditions(res):
    """Forge's data-load conditions -> NeoForge's (catalog §142): the key `conditions` becomes
    `neoforge:conditions` and each `forge:` condition type its `neoforge:` twin. Left as they were, NeoForge
    ignores them, so a recipe meant to load only with another mod present loads always. Mechanical, so it is
    done here; `#forge:` TAG names are not (NeoForge's common tags are plural nouns) and are only counted."""
    changed, unknown, forge_tags = 0, set(), 0

    def walk(node, root=False):
        nonlocal forge_tags
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if k == "type" and isinstance(v, str) and v.startswith("forge:"):
                    if v in FORGE_CONDITIONS:
                        v = FORGE_CONDITIONS[v]
                    else:
                        unknown.add(v)
                # only the ROOT key: a loot table's pools and entries have vanilla `conditions` of their own
                out["neoforge:conditions" if root and k == "conditions" and isinstance(v, list) else k] = walk(v)
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        if isinstance(node, str) and (node.startswith("forge:") or node.startswith("#forge:")):
            forge_tags += 1
        return node
    for f in (res / "data").rglob("*.json") if (res / "data").is_dir() else []:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not isinstance(doc, dict) or "conditions" not in json.dumps(doc):
            walk(doc)   # still count forge tag references
            continue
        new = walk(doc, root=True)
        if new != doc:
            f.write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8"); changed += 1
    return changed, sorted(unknown), forge_tags


def setup(work, jar, src_loader, src_mc, setup_kind, meta, log):
    tpl = ROOT / "templates/neoforge-mod"
    if work.exists() and any(work.iterdir()) and not (work / "port-state.json").exists():
        raise RuntimeError(f"{work} exists and was not made by tools/port.py; move it away first")
    for item in tpl.iterdir():
        if item.name in ("test-templates",):
            continue
        dst = work / item.name
        if item.is_dir():
            shutil.copytree(item, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dst)
    (work / "gradlew").chmod(0o755)
    st = work / "settings.gradle"
    st.write_text(st.read_text(encoding="utf-8").replace("MOD_ID_PLACEHOLDER", meta["modId"]), encoding="utf-8")
    # decompile
    vf = ROOT / "tools/vineflower.jar"
    if not vf.exists():
        run(["bash", str(ROOT / "tools/download-tools.sh")], log=log)
    raw = work / "decompiled-raw"
    shutil.rmtree(raw, ignore_errors=True); raw.mkdir(parents=True)
    # an explicit heap and stack: the JVM default heap ran out on a 3-MB mod jar, and Vineflower then silently skips
    # whichever class it was writing (X79)
    r = run(["java", "-Xss16m", "-Xmx6g", "-jar", str(vf), "-dgs=1", "-rsy=1", "-rbr=1", str(jar), str(raw)], log=log, timeout=7200)
    if any(raw.rglob("*.java")):
        recover_missing_classes(jar, raw, vf, log)
    if not any(raw.rglob("*.java")):
        with zipfile.ZipFile(jar) as z:
            if any(n.endswith(".class") for n in z.namelist()):
                raise RuntimeError("Vineflower produced no Java (see setup.log)")
            # a RESOURCE-ONLY mod (a structure or datapack bundle): nothing to decompile, the port is its data
            for n in z.namelist():
                if n.startswith(("assets/", "data/", "META-INF/")) or n == "pack.mcmeta":
                    if not n.endswith("/"):
                        d = raw / n; d.parent.mkdir(parents=True, exist_ok=True); d.write_bytes(z.read(n))
        meta["resource_only"] = True
        # its loader was lowcodefml (no code); the port runs on javafml like every other, so it gets a one-line
        # @Mod entry class -- which also gives the GameTest gates a package to live in
        mid = meta["modId"]
        cls = "".join(w.capitalize() for w in re.split(r"[_\W]+", mid) if w) + "Mod"
        d = raw / "com" / mid
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{cls}.java").write_text(f"package com.{mid};\n\nimport net.neoforged.fml.common.Mod;\n\n"
                                       f"/** Entry point for a resource-only mod: its content is all data and assets. */\n"
                                       f"@Mod(\"{mid}\")\npublic class {cls} {{\n}}\n", encoding="utf-8")
        with open(log, "a", encoding="utf-8") as fh:
            fh.write("setup: resource-only mod (no classes): the port is its data and metadata\n")
    srcj, res = work / "src/main/java", work / "src/main/resources"
    srcj.mkdir(parents=True, exist_ok=True)
    for f in raw.rglob("*.java"):
        d = srcj / f.relative_to(raw); d.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(f, d)
    artifacts = fix_decompile_artifacts(srcj)
    leftover = decompile_markers(srcj)
    if leftover:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write("decompile markers left after the artifact fixes (by kind: count, first file):\n"
                     + "".join(f"  {k}: {n}, {f}\n" for k, (n, f) in sorted(leftover.items())))
    for x in list(raw.iterdir()):
        if x.name in ("assets", "data") and x.is_dir():
            shutil.copytree(x, res / x.name, dirs_exist_ok=True)
        elif x.is_file() and (x.name == "pack.mcmeta" or x.suffix == ".json"):
            shutil.copy2(x, res / x.name)
    meta_inf = raw / "META-INF"
    if meta_inf.is_dir():
        for f in meta_inf.rglob("*"):
            rel = f.relative_to(raw)
            if f.is_file() and not f.name.endswith("mods.toml") and "jarjar" not in rel.parts and f.name != "MANIFEST.MF":
                d = res / rel; d.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(f, d)
    for f in res.glob("*.refmap.json"):
        f.unlink()
    # official names
    if setup_kind == "srg":
        mp = name_map("srg", src_mc, log)
        run([sys.executable, str(ROOT / "tools/srg-remap/apply_mapping.py"), str(mp), str(srcj)], log=log)
        files = [str(f) for f in srcj.rglob("*.java")]
        for i in range(0, len(files), 200):
            run(["perl", "-pi", str(ROOT / "tools/srg-remap/forge_import_codemod.pl"), *files[i:i + 200]], log=log)
        run([sys.executable, str(ROOT / "tools/srg-remap/mc121_codemod.py"), str(srcj)], log=log)
    elif setup_kind == "intermediary":
        mp = name_map("intermediary", src_mc, log)
        run([sys.executable, str(ROOT / "tools/intermediary-remap/apply_mapping.py"), str(mp), str(srcj)], log=log)
    # A (Neo)Forge jar can carry intermediary-named code too (a multi-loader build that bundles its Fabric-side
    # classes): 1951 such names in one NeoForge jar. Intermediary ids are globally unique, so the same remap a Fabric
    # source gets is safe on any route (X76).
    if setup_kind != "intermediary" and any(re.search(r"\bnet\.minecraft\.class_\d+\b|\b(?:method|field)_\d+\b",
                                                      f.read_text(encoding="utf-8", errors="replace"))
                                            for f in srcj.rglob("*.java")):
        mp = name_map("intermediary", src_mc, log)
        if mp.exists():
            run([sys.executable, str(ROOT / "tools/intermediary-remap/apply_mapping.py"), str(mp), str(srcj)], log=log)
    left = sum(len(re.findall(r'\b[mf]_\d+_\b|\b(?:class|method|field)_\d+\b', f.read_text(encoding="utf-8", errors="replace")))
               for f in srcj.rglob("*.java"))
    # gradle.properties
    group = None
    for f in srcj.rglob("*.java"):
        t = f.read_text(encoding="utf-8", errors="replace")
        if re.search(r'^@Mod\(', t, re.M):
            m = re.search(r'^package\s+([\w.]+);', t, re.M)
            group = m.group(1) if m else None
            break
    uses_gecko = any("software.bernie.geckolib" in f.read_text(encoding="utf-8", errors="replace") for f in srcj.rglob("*.java"))
    gp = work / "gradle.properties"
    g = gp.read_text(encoding="utf-8")

    def put(k, v):
        nonlocal g
        v = (v or "").replace("\n", " ").replace("\\", "/").strip()
        g = re.sub(rf"(?m)^{k}=.*$", lambda _m: f"{k}={v}", g)
    put("mod_id", meta["modId"]); put("mod_name", meta.get("displayName") or meta["modId"])
    put("mod_version", meta.get("version") or "1.0.0"); put("mod_group_id", group or f"com.{meta['modId']}")
    put("mod_license", meta.get("license") or "All Rights Reserved"); put("mod_authors", meta.get("authors") or "unknown")
    put("mod_description", (meta.get("description") or "A ported mod.")[:300])
    put("uses_mixins", "true" if meta["mixins"] else "false"); put("uses_geckolib", "true" if uses_gecko else "false")
    gp.write_text(g, encoding="utf-8")
    # metadata: mixins and the mod's other dependencies into the template's toml
    toml = res / "META-INF/neoforge.mods.toml"
    t = toml.read_text(encoding="utf-8")
    for cfg in meta["mixins"]:
        p = res / cfg
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            d.pop("refmap", None)
            if str(d.get("compatibilityLevel", "JAVA_21")) < "JAVA_21":
                d["compatibilityLevel"] = "JAVA_21"
            p.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")
            t += f'\n[[mixins]]\nconfig="{cfg}"\n'
    for dep in meta["deps"]:
        t += (f'\n[[dependencies.${{mod_id}}]]\n    modId="{dep["modId"]}"\n    type="{"required" if dep["required"] else "optional"}"\n'
              f'    versionRange="*"\n    ordering="NONE"\n    side="BOTH"\n')
    toml.write_text(t, encoding="utf-8")
    pm = res / "pack.mcmeta"
    if pm.exists():
        try:
            d = json.loads(pm.read_text(encoding="utf-8"))
            d.setdefault("pack", {})["pack_format"] = 34
            pm.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")
        except ValueError:
            pass
    run([sys.executable, str(ROOT / "tools/fix-datapack-layout.py"), str(work), "--apply"], log=log)
    cond_changed, cond_unknown, forge_tags = neoforge_conditions(res) if src_loader == "forge" else (0, [], 0)
    hoisted = fix_hoisted_spec(srcj)
    # optional integrations and datagen are code the port cannot compile against (tools/park-optional.py)
    keep = sorted({d["modId"] for d in meta.get("deps", []) if d["required"]} | ({"geckolib"} if uses_gecko else set()))
    pk = run([sys.executable, str(ROOT / "tools/park-optional.py"), "--work", str(work), "--group",
              group or f"com.{meta['modId']}", "--also-required", ",".join(keep)], log=log)
    parked = int((re.findall(r"park-optional: (\d+) file", pk.stdout) or ["0"])[0])
    run([sys.executable, str(ROOT / "tools/scaffold-gametest.py"), "--work", str(work)], log=log)
    return {"group": group, "parked_files": parked, "decompile_artifacts_fixed": artifacts, "decompile_markers_left": {k: v[0] for k, v in leftover.items()}, "unmapped_names_left": left, "hoisted_spec_fixed": hoisted, "geckolib": uses_gecko,
            "mixin_configs": meta["mixins"], "deps": meta["deps"],
            "conditions_rewritten": cond_changed, "unknown_forge_conditions": cond_unknown,
            "forge_tag_references_left": forge_tags}


def run_hops(a, T, hops, work, state, meta):
    # ── hops
    for i, h in enumerate(hops, 1):
        key, last, first = f"hop{i}", i == len(hops), i == 1
        if key in state["done"]:
            continue
        pack = h["pack"]
        if h["kind"] == "era":
            if f"{key}-era" not in state["done"]:
                say(f"hop {i}: era step to {h['to_mc']} (frame, maps, rename table, flatten)")
                r = subprocess.run([sys.executable, str(ROOT / "tools/era-hop.py"), "--work", str(work), "--target", h["to_mc"]])
                if r.returncode != 0:
                    return stop(24, f"the era step failed (exit {r.returncode}); see its output above", ["fix and rerun"], state, work)
                state["done"].append(f"{key}-era"); save_state(work, state)
            if f"{key}-mechanical" not in state["done"]:
                # members -> every converter -> members (tools/mechanical-hop.py): the same deterministic stage the
                # fork route runs, so a converter reaches both routes and no worker is paid for what a script does
                say(f"hop {i}: mechanical stage for {h['to_mc']} (member renames, converters)")
                r = subprocess.run([sys.executable, str(ROOT / "tools/mechanical-hop.py"), "--work", str(work),
                                    "--stage", "era", "--target", h["to_mc"], "--json", str(work / f"mechanical-{key}.json")])
                if r.returncode != 0:
                    return stop(25, f"the mechanical stage failed (exit {r.returncode}); see its output above",
                                ["fix and rerun"], state, work)
                state["done"].append(f"{key}-mechanical"); save_state(work, state)
            if a.stop_after == f"{key}-mechanical":   # measuring: every deterministic rewrite of the hop, no worker
                say(f"stopped after {key}'s mechanical stage (--stop-after)"); return 0
            pack = None
        if pack and f"{key}-recipes" not in state["done"]:
            # applied here rather than by run-port, so the tree is snapshotted AFTER every deterministic rewrite:
            # tools/learn-pack.py diffs that snapshot against the finished hop to propose the next pack rows
            subprocess.run([sys.executable, str(ROOT / "tools/apply-recipes.py"), "--src", str(work / "src/main/java"),
                            "--recipes", str(ROOT / pack), "--json", str(work / f"recipes-report-{key}.json")],
                           stdout=open(work / f"recipes-report-{key}.txt", "w", encoding="utf-8"), stderr=subprocess.STDOUT)
            state["done"].append(f"{key}-recipes"); save_state(work, state)
        if h.get("from_loader") == "forge" and h["to_loader"] == "neoforge" and f"{key}-mechanical" not in state["done"]:
            # the access transformer, forge-shapes, convert-simplechannel, fix-holders (tools/mechanical-hop.py):
            # the same deterministic tail the fork route runs on a Forge hop
            say(f"hop {i}: mechanical stage for {h['to_mc']} (access transformer, Forge shapes, networking, holders)")
            ws = pathlib.Path(os.environ.get("MIGRATE_WORKSPACE") or pathlib.Path.home() / ".mc-mod-upgrade/work")
            srg = next(iter(sorted(ws.glob(f"srg2official-{h['from_mc']}.json"))), None)   # setup built it for this hop
            r = subprocess.run([sys.executable, str(ROOT / "tools/mechanical-hop.py"), "--work", str(work), "--stage", "forge",
                                *(["--srg-map", str(srg)] if srg else []),
                                "--json", str(work / f"mechanical-{key}.json")])
            if r.returncode != 0:
                return stop(25, f"the mechanical stage failed (exit {r.returncode}); see its output above",
                            ["fix and rerun"], state, work)
            state["done"].append(f"{key}-mechanical"); save_state(work, state)
            if a.stop_after == f"{key}-mechanical":
                say(f"stopped after {key}'s mechanical stage (--stop-after)"); return 0
        pack = None
        snap = work / "hop-start" / key
        if not snap.exists():
            shutil.copytree(work / "src/main/java", snap)
        if a.stop_after == f"{key}-recipes":   # testing: this hop's deterministic rewrites only, no worker
            say(f"stopped after {key}'s recipes (--stop-after)"); return 0
        behaviour = "off" if a.no_behaviour else ("write" if first else ("rerun" if last else "off"))
        gatec = a.gatec if last else "none"
        cmd = [sys.executable, str(ROOT / "tools/run-port.py"), "--work", str(work), "--tag", key,
               "--pack", str(ROOT / pack) if pack else "none", "--target", TARGET_NAME.get(h["to_mc"], f"NeoForge {h['to_mc']}"),
               "--budget", str(a.budget), "--gate-budget", str(a.gate_budget), "--gatec-budget", str(a.gatec_budget),
               "--behaviour", behaviour, "--gatec", gatec]
        if a.no_visual_review:
            cmd.append("--no-visual-review")
        say(f"hop {i}: {h['from']} -> {h['to']}: recipes, compile loop, Gate B"
            + (", behaviour tests" if behaviour != "off" else "") + (", Gate C, visual review" if gatec != "none" else ""))
        r = subprocess.run(cmd)
        if r.returncode != 0:
            state["stopped"] = {"code": r.returncode, "what": f"hop {i} stopped (see run-port-{key}.json)"}
            save_state(work, state)
            again = f"--from-port {a.from_port}" if getattr(a, "from_port", None) else repr(a.mod)
            print(f"port: resume with the same command once the choice above is made: python3 tools/port.py {again} --to {T}")
            return r.returncode
        if first and not last and a.gatec != "none":
            # the client harness is ported with the mod by the later hops rather than written per version
            subprocess.run([sys.executable, str(ROOT / "tools/scaffold-gatec.py"), "--work", str(work)])
        state["done"].append(key); save_state(work, state)
        if a.stop_after == key:
            say(f"stopped after {key} (--stop-after)"); return 0
    # ── report
    total, rows = 0.0, []
    for i in range(1, len(hops) + 1):
        p = work / f"run-port-hop{i}.json"
        d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        total += d.get("usd") or 0
        rows.append({"hop": i, "route": state["hops"][i - 1], "usd": d.get("usd"), "stages": {
            k: ({"green": v.get("green")} if "green" in v else {kk: v.get(kk) for kk in ("start_errors", "end_errors", "stubs", "findings", "skipped") if kk in v})
            for k, v in d.get("stages", {}).items()}})
    report = {"modid": meta["modId"], "target": T, "workers_usd": round(total, 4), "hops": rows,
              "note": "workers only; the session that drove this is counted by tools/port-cost.py"}
    (work / "port-report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    state["done"].append("report"); save_state(work, state)
    say(f"DONE: {meta['modId']} ported to NeoForge {T}; workers ${total:.2f}; details in {work / 'port-report.json'}")
    return 0



def port_meta(src):
    """(modid, minecraft version, why-not) for an existing port workspace, read off its own build files."""
    gp = src / "gradle.properties"
    if not gp.exists():
        return None, None, f"{src} has no gradle.properties; is it a port workspace?"
    props = dict(re.findall(r"(?m)^\s*([\w.]+)\s*=\s*(.*?)\s*$", gp.read_text(encoding="utf-8", errors="replace")))
    if list(src.glob("versions/*.properties")):
        return None, None, (f"{src} is already a multi-version workspace ({', '.join(sorted(p.stem for p in src.glob('versions/*.properties')))}); "
                            "build its other target with -Pmc=<version> instead")
    modid, mc = props.get("mod_id"), props.get("minecraft_version")
    if not modid or not mc:
        return None, None, f"{gp} does not name mod_id and minecraft_version"
    return modid, mc, None


def from_port(a):
    """Continue a finished port to a newer target: copy the workspace (never build outputs or a run dir), treat
    setup as done, and run only the hops from its version. The port's own fixes are the starting point,
    which is what the 26.x era hop was measured on (59% of start errors removed by the table)."""
    ensure_ca_bundle()
    T = a.to
    if T not in TARGET_NAME:
        print(f"port: unknown target {T}; known: {', '.join(TARGET_NAME)}"); return 2
    src = pathlib.Path(a.from_port).expanduser().resolve()
    modid, mc, why = port_meta(src)
    if why:
        return stop(24, why, ["pass a single-target port workspace"])
    if mc == T:
        return stop(20, f"{src.name} is already a NeoForge {T} port", ["nothing to do"])
    hops = route.plan(("neoforge", mc), ("neoforge", T))
    if hops is None:
        return stop(22, f"no route from neoforge {mc} to NeoForge {T} in tools/routes.tsv", ["add a row for the missing hop"])
    work = pathlib.Path(a.mods_dir).resolve() / modid
    say(f"{modid}: existing port at {src} (NeoForge {mc}) -> NeoForge {T}, {len(hops)} hop(s): "
        + " | ".join(f"{h['from']} -> {h['to']} [{h['kind']}]" for h in hops))
    if a.plan_only:
        print(json.dumps({"from_port": str(src), "modid": modid, "work": str(work), "hops": hops}, indent=1)); return 0
    if work != src:
        if work.exists() and not (work / "port-state.json").exists():
            return stop(24, f"{work} exists and is not a port.py workspace; refusing to overwrite it",
                        ["pass --mods-dir to put the new workspace elsewhere"])
        if not work.exists():
            shutil.copytree(src, work, ignore=shutil.ignore_patterns("build", "run", ".gradle", "hop-start", "*.log"))
            say(f"copied the port to {work} (build outputs and run dirs left behind)")
    state = load_state(work)
    state.update({"info": {"from_port": str(src), "target": T}, "hops": [h["from"] + "->" + h["to"] for h in hops],
                  "modid": modid})
    state.pop("stopped", None)
    if "setup" not in state["done"]:
        if not list((work / "src/main/java").rglob("BaselineGameTest.java")):   # an older port may predate it
            run([sys.executable, str(ROOT / "tools/scaffold-gametest.py"), "--work", str(work)], log=work / "setup.log")
        state["setup"] = {"from_port": str(src)}; state["done"].append("setup")
    save_state(work, state)
    a.mod = None
    return run_hops(a, T, hops, work, state, {"modId": modid})


def parse_ver(v):
    """'1.21.1' -> (1, 21, 1); '26.2' and '26.2.0' -> (26, 2) (trailing zeros dropped, so a range's '[26.2.0,)'
    admits 26.2); a snapshot or junk sorts first."""
    if not re.fullmatch(r"\d+(\.\d+)*", v or ""):
        return (0,)
    t = [int(x) for x in v.split(".")]
    while len(t) > 1 and t[-1] == 0:
        t.pop()
    return tuple(t)


# ── main ────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mod", nargs="?", help="a mod's exact name on Modrinth/CurseForge, or a path to its jar")
    ap.add_argument("--to", default="1.21.1", help="target Minecraft version (NeoForge): 1.21.1 or 26.2")
    ap.add_argument("--mods-dir", default=str(ROOT / "mods")); ap.add_argument("--jars-dir")
    ap.add_argument("--budget", type=float, default=15.0, help="compile-loop dollars per hop")
    ap.add_argument("--gate-budget", type=float, default=5.0); ap.add_argument("--gatec-budget", type=float, default=5.0)
    ap.add_argument("--allow-no-pack", action="store_true"); ap.add_argument("--ignore-deps", action="store_true")
    ap.add_argument("--no-behaviour", action="store_true"); ap.add_argument("--no-visual-review", action="store_true")
    ap.add_argument("--gatec", default="launch,spawn,battle,gauntlet")
    ap.add_argument("--plan-only", action="store_true", help="resolve, check and print the route; change nothing")
    ap.add_argument("--stop-after", help="stop after this stage: setup, hop1, hop1-recipes, hop2-mechanical, ... (for testing a stage)")
    ap.add_argument("--from-port", metavar="WORKSPACE",
                    help="continue an EXISTING port (a finished single-target NeoForge workspace, e.g. a 1.21.1 port) "
                         "to --to, instead of starting from a jar")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if a.from_port:
        return from_port(a)
    if not a.mod:
        ap.error("name a mod or a jar (or --from-port <workspace>)")
    ensure_ca_bundle()
    T = a.to
    if T not in TARGET_NAME:
        print(f"port: unknown target {T}; known: {', '.join(TARGET_NAME)}"); return 2
    jars = pathlib.Path(a.jars_dir or pathlib.Path.home() / ".mc-mod-upgrade/jars")
    # ── resolve
    local = pathlib.Path(a.mod).expanduser()
    info = {"query": a.mod, "target": T}
    if local.is_file():
        jar = local.resolve()
        info["source"] = {"jar": str(jar)}
        # a toml range ([1.21,)) names no exact version and no registry file; the jar's hash names both
        rc, idf = modreg("identify", "--jar", str(jar))
        if idf.get("found"):
            mcs = idf.get("mcs") or []
            info["source"].update({"provider": idf["provider"], "id": idf["id"], "fileId": idf["fileId"]})
            if mcs:
                info["source"]["chosen"] = {"mc": max(mcs, key=parse_ver), "fileId": idf["fileId"]}   # [1.21, 1.21.1] -> 1.21.1
    else:
        prov, pid, v = resolve(a.mod, T)
        if prov is None:
            return stop(23, f"could not resolve {a.mod!r}: {v}", ["give the exact name as it appears on Modrinth/CurseForge",
                                                                  "or pass the path to the jar"])
        info["source"] = {"provider": prov, "id": pid, "classification": v["classification"], "chosen": v.get("chosen"),
                          "fileId": (v.get("chosen") or {}).get("fileId")}
        # the newest older build is not always the right source: a mod on 1.21.8 that ALSO ships 1.21.1 should port
        # 1.21.1 -> 26.2 (a packed era hop), not downport 1.21.8 -> 1.21.1 first (no pack: a worker-only hop)
        alt = packed_source(prov, pid, T, v)
        if alt:
            say(f"source: the {v['chosen'].get('loader', '?')} {v['chosen']['mc']} build would need an unpacked hop; "
                f"using the {alt.get('loader', '?')} {alt['mc']} build instead")
            v = {**v, "chosen": alt}
            info["source"].update({"chosen": alt, "fileId": alt["fileId"]})
        if v.get("has_native"):
            c = v["chosen"] or {}
            return stop(20, f"{a.mod} already has a NeoForge {T} build ({c.get('fileName')}); there is nothing to port",
                        [f"install it: python3 tools/mod-registry/modreg.py download --provider {prov} --id {pid} --file {c.get('fileId')} --out <mods dir>",
                         "or use the install-mod skill"])
        c = v.get("chosen")
        if not c:
            return stop(23, f"{a.mod} has no build older than {T} to port from (newer only: {v.get('newer_mcs')})",
                        ["a downport needs a person's decision: see the install-mod skill"])
        # ── deps
        if not a.ignore_deps:
            rc, dd = modreg("deps", "--provider", prov, "--id", pid, "--file", c["fileId"])
            if "dependencies" not in dd:
                return stop(21, f"could not read the dependencies of {c['fileName']}: {dd.get('error', rc)}",
                            ["retry", "pass --ignore-deps if you know it has none"])
            need = []
            for dep in dd["dependencies"]:
                if dep.get("type") != "required":
                    continue
                if not any(modreg("versions", "--provider", prov, "--id", sid, "--loader", "neoforge", "--mc", T)[1]
                           .get("has_native") for sid in (loader_siblings(dep["id"]) if prov == "modrinth" else [dep["id"]])):
                    need.append(dep.get("name") or dep["id"])
            info["required_deps_missing_at_target"] = need
            if need:   # the registry's word; the jar's own toml decides once downloaded (wire_deps): registry
                       # dependency data is sometimes the other loader's (a Forge build listing a Fabric project)
                say(f"registry: {', '.join(need)} listed as required, " + ("with no" if len(need) == 1 else "none with a")
                    + f" NeoForge {T} build -- checking the jar's own toml")
        jars.mkdir(parents=True, exist_ok=True)
        jar = jars / c["fileName"]
        if not jar.exists():
            rc, dl = modreg("download", "--provider", prov, "--id", pid, "--file", c["fileId"], "--out", str(jars) + "/", timeout=600)
            if rc != 0 or not jar.exists():
                return stop(23, f"download failed: {dl}", ["retry", "download the jar yourself and pass its path"])
        say(f"source: {c['fileName']} ({prov} {pid}, Minecraft {c['mc']})")
    # ── triage
    rc, rm = modreg("resolve-modid", "--jar", str(jar))
    meta = read_jar_meta(jar)
    loader = (rm.get("loader") or "").lower()
    if not loader:              # no (neo)forge toml: say what the jar IS rather than assume Forge
        with zipfile.ZipFile(jar) as z:
            names = set(z.namelist())
        nested = sorted(n for n in names if n.endswith(".jar"))
        if any(n.startswith("META-INF/services/") and n.endswith("IModLocator") for n in names) and nested:
            return stop(23, f"{jar.name} is a self-loading multi-loader jar: a Forge mod LOCATOR (not a mod) that picks "
                             f"one of {len(nested)} nested jars at runtime ({', '.join(nested[:3])}...), so there is no "
                             f"single mod source to port",
                        ["port the nested jar for the nearest Minecraft version by hand (unzip it and pass its path)",
                         "or skip this mod"])
        if "fabric.mod.json" in names or "quilt.mod.json" in names:
            loader = "fabric"
        else:
            return stop(23, f"{jar.name} carries no Forge, NeoForge or Fabric metadata, so it is not a mod this tool can port",
                        ["check it is the mod's jar and not a library or a launcher plugin"])
    fams = loader_families(jar)
    if len(fams) >= 3:
        return stop(23, f"{jar.name} is a universal jar: its classes call {', '.join(sorted(fams))} APIs at once and "
                        f"pick a path per loader and Minecraft version at runtime, so its decompile mixes every target's "
                        f"names and there is no single source tree to port (X75)",
                    ["port from the mod's own source repository, which builds each target separately",
                     "or skip this mod"])
    kt, total = kotlin_share(jar)
    if total and kt * 2 > total:
        return stop(23, f"{jar.name} is a Kotlin mod ({kt} of {total} classes carry kotlin.Metadata): this pipeline "
                        f"decompiles and ports Java, and turning a Kotlin mod into Java is a rewrite, not a port",
                    ["port it by hand in Kotlin (kotlinforforge has NeoForge builds)", "or skip this mod"])
    if loader == "neoforge" and forge_api_jar(jar):
        say(f"{jar.name}: NeoForge on the Forge API (mods.toml, net.minecraftforge classes) -- ported as Forge")
        loader = "forge"
    src_mc = (info["source"].get("chosen") or {}).get("mc") or re.sub(r"[\[\](),]", "", (rm.get("mcRange") or "").split(",")[0]) or "1.20.1"
    meta["modId"] = meta.get("modId") or (rm.get("modIds") or [None])[0]
    if not meta["modId"]:
        return stop(23, "could not read the jar's mod id", ["pass a different jar"])
    hops = route.plan((loader, src_mc), ("neoforge", T))
    if hops is None:
        return stop(22, f"no route from {loader} {src_mc} to NeoForge {T} in tools/routes.tsv",
                    ["add a row (and, ideally, a pack) for the missing hop"])
    work = pathlib.Path(a.mods_dir) / meta["modId"]
    WORKS.append(work)
    say(f"{meta['modId']}: {loader} {src_mc} -> NeoForge {T}, {len(hops)} hop(s): "
        + " | ".join(f"{h['from']} -> {h['to']} [{h['kind']}, pack: {'yes' if h['pack'] else 'NONE'}]" for h in hops))
    nopack = [h for h in hops if not h["pack"]]
    listed = ((info.get("source") or {}).get("chosen") or {}).get("loader")
    if listed and listed != loader and not (listed == "neoforge" and loader == "forge"):
        say(f"note: the registry lists this file as a {listed} build, but the jar itself is {loader} (a mislabelled upload)")
    if nopack and not a.allow_no_pack:
        return stop(22, f"no recipe pack for {', '.join(h['from'] + ' -> ' + h['to'] for h in nopack)}: the workers would do that whole hop"
                    + (f" (the registry calls this file {listed}; the jar is {loader})" if listed and listed != loader else ""),
                    ["continue anyway: rerun with --allow-no-pack (likely several times the cost of a packed hop)",
                     "build a pack for the hop first (tools/learn-pack.py from a finished port of that kind)"])
    if a.plan_only:
        print(json.dumps({**info, "modid": meta["modId"], "work": str(work), "hops": hops}, indent=1)); return 0
    state = load_state(work)
    state.update({"info": info, "hops": [h["from"] + "->" + h["to"] for h in hops], "modid": meta["modId"]})
    state.pop("stopped", None)
    log = work / "setup.log"
    hard = hard_optional_deps(jar, meta.get("deps", []))
    if hard:
        say(f"dependencies declared optional but used throughout the code, treated as required: {', '.join(hard)}")
        for d in meta.get("deps", []):
            if d["modId"] in hard:
                d["required"] = True
    state["hard_optional"] = hard
    if "deps-wired" not in state["done"] and not a.ignore_deps:
        src = info["source"]
        req = [d["modId"] for d in meta.get("deps", []) if d["required"]]
        work.mkdir(parents=True, exist_ok=True)
        wired = wire_deps(work, src.get("provider"), src.get("id"), src.get("fileId"),
                          sorted({h["to_mc"] for h in hops}, key=parse_ver), req,
                          toml_authority=bool(rm.get("hasNeoforgeToml") or rm.get("hasForgeToml")))
        state["deps_wired"] = wired
        if wired:
            say("dependencies: " + "; ".join(f"{mc}: {', '.join(n for n in names)}" for mc, names in wired.items()))
        missing = [n for names in wired.values() for n in names if "NO usable" in n or "REQUIRED by" in n]
        if missing:     # the same decision the registry pre-check asks for, for a dependency only the toml names
            return stop(21, f"required dependencies with no usable build for the target: {'; '.join(missing)}",
                        ["port each one first (python3 tools/port.py \"<name>\" --to <target>)",
                         "or rerun with --ignore-deps to port this one anyway (its Gate B will not load without them)"],
                        state, work)
        state["done"].append("deps-wired"); save_state(work, state)
    # ── setup
    if "setup" not in state["done"]:
        say(f"setup: scaffold {work}, decompile, official names, metadata, datapack layout, baseline GameTest")
        try:
            work.mkdir(parents=True, exist_ok=True)
            save_state(work, state)
            info_setup = setup(work, jar, loader, src_mc, hops[0]["setup"], meta, log)
        except Exception as e:  # noqa: BLE001 -- a setup failure is a STOP with the reason, never a traceback-and-guess
            import traceback
            with open(log, "a", encoding="utf-8") as fh:
                fh.write("\nsetup failed:\n" + traceback.format_exc())
            return stop(24, f"setup failed: {e}", ["read mods/<modid>/setup.log", "fix the cause and rerun"], state, work)
        state["setup"] = info_setup; state["done"].append("setup"); save_state(work, state)
        if info_setup.get("decompile_markers_left"):
            say("decompile markers left (see setup.log): " + ", ".join(f"{k} x{n}" for k, n in info_setup["decompile_markers_left"].items()))
        say(f"setup done: group {info_setup['group']}, {info_setup['unmapped_names_left']} unmapped names left, "
            f"{len(info_setup['hoisted_spec_fixed'])} hoisted config SPEC(s) fixed, "
            f"{info_setup.get('parked_files', 0)} file(s) of optional integrations/datagen parked (MIGRATION.md)")
    if a.stop_after == "setup":
        say("stopped after setup (--stop-after)"); return 0
    return run_hops(a, T, hops, work, state, meta)


def packed_source(prov, pid, T, v, tries=6):
    """When the chosen build's route has a hop with no pack, the newest OLDER build whose route is fully packed,
    or None. Only builds the registry says exist are considered, newest first."""
    c = v.get("chosen") or {}
    def packed(loader, mc):
        if loader == "neoforge" and mc == "1.20.1":   # NeoForge's 1.20.1 line IS the Forge fork (forge_api_jar
            loader = "forge"                          # confirms it from the jar once downloaded)
        hops = route.plan((loader, mc), ("neoforge", T))
        return hops is not None and all(h["pack"] for h in hops)
    if not c or packed(c.get("loader") or "neoforge", c["mc"]):
        return None
    # the SAME version on another loader first (Forge still ships 1.21.x; a NeoForge build of it is the right
    # source), then older versions, newest first
    mcs = [c["mc"]] + sorted(v.get("older_mcs") or [], key=parse_ver, reverse=True)[:tries * 2]
    for mc in mcs:
        if not re.fullmatch(r"\d+(\.\d+)+", mc):
            continue
        for loader in ("neoforge", "forge"):
            if (mc, loader) == (c["mc"], c.get("loader")) or not packed(loader, mc):
                continue
            rc, dv = modreg("versions", "--provider", prov, "--id", pid, "--loader", loader, "--mc", mc)
            alt = dv.get("chosen") if dv.get("has_native") else None
            if alt:
                return alt
            tries -= 1
            if tries <= 0:
                return None
    return None


PLATFORM_IDS = {"minecraft", "forge", "neoforge", "fabric", "fabricloader", "java", "fabric-api"}


def jar_toml(jar):
    """(own mod ids, minecraft versionRange or None) read from a jar's (neo)forge toml."""
    try:
        with zipfile.ZipFile(jar) as z:
            name = next((n for n in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml") if n in z.namelist()), None)
            text = z.read(name).decode("utf-8", "replace") if name else ""
    except (OSError, zipfile.BadZipFile):
        return set(), None
    parsed = parse_mods_toml(text)
    if parsed:
        own = {m["modId"] for m in parsed[0] if isinstance(m.get("modId"), str)}
        rng = next((x.get("versionRange") for _o, x in parsed[1] if x.get("modId") == "minecraft"), None)
        return own, rng
    text = re.sub(r"#[^\n]*", "", text)
    own, rng = set(), None
    for block in re.split(r"(?m)^\s*\[\[", text)[1:]:
        mid = re.search(r"""modId\s*=\s*["']([^"'\n]+)["']""", block)   # TOML: either quote
        if block.startswith("mods]]") and mid:
            own.add(mid.group(1))
        elif block.startswith("dependencies.") and mid and mid.group(1) == "minecraft":
            r = re.search(r"""versionRange\s*=\s*["']([^"'\n]+)["']""", block)
            rng = r.group(1) if r else None
    return own, rng


def loader_siblings(slug):
    """Some authors publish one registry project per loader, and a dependency can point at the Fabric one
    (friends-and-foes) while the NeoForge build lives in a sibling (friends-and-foes-forge)."""
    base = re.sub(r"-(fabric|quilt)$", "", str(slug))
    return list(dict.fromkeys([str(slug), base, base + "-forge", base + "-neoforge"]))


def slug_guesses(mid):
    """Registry slugs a mod id commonly corresponds to: itself, underscores as hyphens, and one hyphen inserted
    at each word boundary of a run-together id (farmersdelight -> farmers-delight)."""
    out = [mid, mid.replace("_", "-")]
    if "_" not in mid and len(mid) >= 8:
        out += [mid[:i] + "-" + mid[i:] for i in range(3, len(mid) - 2)]
    return list(dict.fromkeys(out))


def hard_optional_deps(jar, deps):
    """Optional dependencies the mod actually USES as hard ones. Many mods (MCreator's default above all) declare
    every dependency mandatory=false while hundreds of classes import its API, and parking all of those as an
    'optional integration' would gut the port. A declared dependency whose id appears as a package segment in at
    least 20 classes, or a tenth of them, is treated as required."""
    opt = [d["modId"] for d in deps if not d["required"]]
    if not opt:
        return []
    with zipfile.ZipFile(jar) as z:
        cls = [z.read(n) for n in z.namelist() if n.endswith(".class") and not n.startswith("META-INF/")]
    out = []
    for mid in opt:
        flat = mid.replace("_", "").replace("-", "").lower().encode()
        if len(flat) < 4:
            continue
        pat = re.compile(rb"l[\w/$]*/" + re.escape(flat) + rb"[\w$]*/")   # the bytes are lowercased below: L -> l
        n = sum(1 for b in cls if pat.search(b.replace(b"_", b"").lower()))
        if n >= max(20, len(cls) // 10):
            out.append(mid)
    return out


LOADER_FAMILIES = {"forge": (b"net/minecraftforge/", b"cpw/mods/fml/"), "neoforge": (b"net/neoforged/",),
                   "fabric": (b"net/fabricmc/", b"net/minecraft/class_")}


def loader_families(jar, at_least=2):
    """The loader APIs a jar's own classes reference ({family}, each in >= at_least classes). An ordinary mod jar,
    an Architectury per-loader jar included, references one; a universal jar (one jar for Forge, NeoForge and Fabric
    across Minecraft versions, dispatching at runtime) references all three."""
    hits = collections.Counter()
    with zipfile.ZipFile(jar) as z:
        for n in z.namelist():
            if n.endswith(".class") and not n.startswith("META-INF/"):
                b = z.read(n)
                for fam, pats in LOADER_FAMILIES.items():
                    if any(p in b for p in pats):
                        hits[fam] += 1
    return {f for f, k in hits.items() if k >= at_least}


def kotlin_share(jar):
    """(classes carrying kotlin.Metadata, all classes) -- a Kotlin mod decompiles to Kotlin, not Java."""
    with zipfile.ZipFile(jar) as z:
        cls = [n for n in z.namelist() if n.endswith(".class") and not n.startswith("META-INF/")]
        return sum(1 for n in cls if b"Lkotlin/Metadata;" in z.read(n)), len(cls)


def forge_api_jar(jar):
    """NeoForge's first line (MC 1.20.1, 47.1.x) is a fork of Forge: mods.toml, net.minecraftforge packages, SRG
    names at runtime. Such a jar is ported exactly like a Forge one, whatever its toml's loader dependency says."""
    with zipfile.ZipFile(jar) as z:
        names = z.namelist()
        if "META-INF/mods.toml" not in names:   # some carry a neoforge.mods.toml too: the CLASSES decide
            return False
        neo = forge = 0
        for n in names:
            if n.endswith(".class"):
                b = z.read(n)
                forge += b"net/minecraftforge/" in b
                neo += b"net/neoforged/neoforge/" in b
        return forge > 0 and neo == 0


def in_range(ver, rng):
    """Maven-style range check ('[1.21,1.21.2)', '[1.21.1,)', '1.21.1'); True when the range is absent."""
    if not rng or rng.strip() in ("*", ""):
        return True
    v = parse_ver(ver)
    for part in re.findall(r"[\[(][^\])]*[\])]", rng) or [f"[{rng},{rng}]"]:
        lo, _, hi = part[1:-1].partition(",") if "," in part else (part[1:-1], "", part[1:-1])
        ok_lo = not lo.strip() or (v >= parse_ver(lo.strip()) if part[0] == "[" else v > parse_ver(lo.strip()))
        ok_hi = not hi.strip() or (v <= parse_ver(hi.strip()) if part[-1] == "]" else v < parse_ver(hi.strip()))
        if ok_lo and ok_hi:
            return True
    return False


VF_MARK = "$" + "VF" + ":"   # Vineflower's failure marker, spelled so this file does not carry it (tools/check-no-ip.py)
_BARE_NEW = re.compile(r"(?m)^([ \t]*)(?:([\w.$<>\[\], ?]+?)\s+)?(\w+)\s*=\s*new\s+([\w.$]+(?:<[^;()]*>)?)\s*;[ \t]*\n")


def _resugar_constructors(t):
    count = 0
    while True:
        for m in _BARE_NEW.finditer(t):
            v = m.group(3)
            call = re.compile(r"\b%s\.\s*/\*\s*" % re.escape(v) + re.escape(VF_MARK) + r"\s*Unable to resugar constructor\s*\*/\s*<init>\(")
            c = call.search(t, m.end())
            if not c or re.search(r"\b%s\b" % re.escape(v), t[m.end():c.start()]):
                continue
            depth, i = 1, c.end()                    # the matching ')' of the <init>( call
            while i < len(t) and depth:
                ch = t[i]
                if ch in "\"'":
                    j = i + 1
                    while j < len(t) and t[j] != ch:
                        j += 2 if t[j] == "\\" else 1
                    i = j
                elif ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                i += 1
            if depth:
                continue
            args = t[c.end():i - 1]
            decl = (m.group(2) + " ") if m.group(2) else ""
            t = t[:m.start()] + t[m.end():c.start()] + f"{decl}{v} = new {m.group(4)}({args})" + t[i:]
            count += 1
            break
        else:
            return t, count


_INDY_CONCAT = re.compile(r'(?:java\.lang\.invoke\.)?StringConcatFactory\.makeConcatWithConstants'
                          r'<"makeConcatWithConstants"\s*,\s*"((?:[^"\\]|\\.)*)"\s*>\(')


def _resugar_string_concat(t):
    """`StringConcatFactory.makeConcatWithConstants<"makeConcatWithConstants","<recipe>">(a, b)` -- an invokedynamic
    string concatenation Vineflower did not fold. In the recipe each \\u0001 is the next argument and the rest is
    literal text, so it becomes ("" + (a) + "lit" + (b)). A recipe with \\u0002 (a bootstrap constant) is left."""
    count, out, pos = 0, [], 0
    for m in _INDY_CONCAT.finditer(t):
        if m.start() < pos:
            continue
        recipe = m.group(1)
        if "\\u0002" in recipe or "\x02" in recipe:
            continue
        depth, i = 1, m.end()
        while i < len(t) and depth:
            ch = t[i]
            if ch in "\"'":
                j = i + 1
                while j < len(t) and t[j] != ch:
                    j += 2 if t[j] == "\\" else 1
                i = j
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            i += 1
        if depth:
            continue
        inner = t[m.end():i - 1]
        args, d, cur = [], 0, []
        k = 0
        while k < len(inner):                       # split on top-level commas, skipping string literals
            ch = inner[k]
            if ch in "\"'":
                j = k + 1
                while j < len(inner) and inner[j] != ch:
                    j += 2 if inner[j] == "\\" else 1
                cur.append(inner[k:j + 1]); k = j + 1; continue
            if ch in "([{":
                d += 1
            elif ch in ")]}":
                d -= 1
            if ch == "," and d == 0:
                args.append("".join(cur).strip()); cur = []
            else:
                cur.append(ch)
            k += 1
        if "".join(cur).strip():
            args.append("".join(cur).strip())
        segs = re.split(r"\\u0001|\x01", recipe)
        if len(segs) - 1 != len(args):
            continue
        parts = ['""']
        for k2, seg in enumerate(segs):
            if seg:
                parts.append(f'"{seg}"')
            if k2 < len(args):
                parts.append(f"({args[k2]})")
        out.append(t[pos:m.start()] + "(" + " + ".join(parts) + ")")
        pos = i
        count += 1
    out.append(t[pos:])
    return "".join(out), count


def decompile_markers(srcj):
    """Vineflower output that is not Java, still in the tree after fix_decompile_artifacts: {kind: (count, first file)}.
    Each kind is a known way the decompiler gives up; reporting them by name at setup means an unhandled one is seen
    before it breaks a compile (where a parse abort hides every other error)."""
    kinds = {"failure marker": re.compile(re.escape(VF_MARK) + r"\s*([A-Za-z][A-Za-z' ]{3,40})"),
             "<unrepresentable>": re.compile(r"<unrepresentable>"),
             "raw <init> call": re.compile(r"\.\s*(?:/\*[^*]*\*/\s*)?<init>\("),
             "<clinit>/<lambda>": re.compile(r"<(?:clinit|lambda)[^>]*>"),
             "digit-named local class": re.compile(r"\bclass\s+\d"),
             "diamond cast": re.compile(r"\([A-Za-z_][\w.$]*<>\)")}
    out = {}
    for f in pathlib.Path(srcj).rglob("*.java"):
        t = f.read_text(encoding="utf-8", errors="replace")
        for kind, rx in kinds.items():
            for m in rx.finditer(t):
                k = f"{kind}: {m.group(1).strip()}" if kind == "failure marker" else kind
                n, first = out.get(k, (0, f.name))
                out[k] = (n + 1, first)
    return out


def name_map(kind, mc, log):
    """The cached <kind>2official-<mc>.json (kind: srg | intermediary), built on first use and REBUILT when it
    predates the current builder (no "__schema__" >= 2: those builds dropped every member of an unobfuscated class,
    MinecraftServer's included -- X77)."""
    mp = pathlib.Path(os.environ.get("MIGRATE_WORKSPACE") or pathlib.Path.home() / ".mc-mod-upgrade/work") / f"{kind}2official-{mc}.json"
    mp.parent.mkdir(parents=True, exist_ok=True)
    try:
        fresh = json.loads(mp.read_text(encoding="utf-8")).get("__schema__", 0) >= 2
    except (OSError, ValueError, AttributeError):
        fresh = False
    if not fresh:
        run([sys.executable, str(ROOT / f"tools/{kind}-remap/build_mapping.py"), mc, str(mp)], log=log)
    return mp


def fix_decompile_artifacts(srcj):
    """Vineflower output that does not PARSE, fixed before anything else reads it (a parse error stops javac before
    it reports anything else, and the stage then misreads the failure):
      * a local class named with a leading digit (`class 1NoiseCondition`, `new 1NoiseCondition(..)`); Vineflower
        already renames its constructor `_NoiseCondition`, so the class and its uses get that name too;
      * CATALOG §A2: `<unrepresentable>.$assertionsDisabled` -> `true` (asserts are off at runtime);
      * a cast with an empty diamond, `(Supplier<>) () -> x`, written when the type arguments cannot be expressed:
        it becomes the raw cast `(Supplier)`, which is legal and keeps the lambda's target type;
      * an unresugared constructor: `X v = new X;` ... `v./* <marker> Unable to resugar constructor */<init>(args);`
        (the statements between compute the arguments) -> the bare `new X;` goes and the call becomes
        `X v = new X(args);`, when nothing between them touches v.
      * Google's `@AutoService(X.class)` -- build-time only, the jar already carries the META-INF/services file it
        generated -- is stripped likewise (counted with expect_platform);
      * Architectury's `@ExpectPlatform` (+ `.Transformed`): the build already rewrote each such method to call
        `<pkg>.<loader>.<Name>Impl`, so the annotation is inert -- but its foreign package made park-optional park
        the mod's own platform facade (X72). Strip it and its import.
    -> {"digit_classes": n, "assert_guards": n, "constructors": n, ...}"""
    n = {"digit_classes": 0, "assert_guards": 0, "constructors": 0, "string_concats": 0, "diamond_casts": 0,
         "expect_platform": 0}
    for f in pathlib.Path(srcj).rglob("*.java"):
        t = f.read_text(encoding="utf-8", errors="replace")
        new = t
        for name in sorted(set(re.findall(r"\bclass\s+(\d+[A-Za-z_]\w*)", t)), key=len, reverse=True):
            fixed = "_" + re.sub(r"^\d+", "", name)
            new = re.sub(r"(?<![\w$])" + re.escape(name) + r"(?![\w$])", fixed, new)
            n["digit_classes"] += 1
        new, c = _resugar_constructors(new)
        n["constructors"] += c
        new, c = _resugar_string_concat(new)
        n["string_concats"] += c
        new, c = re.subn(r"\(([A-Za-z_][\w.$]*)<>\)", r"(\1)", new)   # `(Supplier<>) () -> x`: a raw cast is legal
        n["diamond_casts"] += c
        if "com.google.auto.service.AutoService" in new:    # build-time only: the jar already has META-INF/services
            new = re.sub(r"(?m)^import com\.google\.auto\.service\.AutoService;[ \t]*\n", "", new)
            new, c = re.subn(r"@(?:com\.google\.auto\.service\.)?AutoService\((?:[^()]|\([^()]*\))*\)[ \t]*\n?[ \t]*", "", new)
            n["expect_platform"] += c
        if "dev.architectury.injectables.annotations" in new:
            new = re.sub(r"(?m)^import dev\.architectury\.injectables\.annotations\.[\w.]+;[ \t]*\n", "", new)
            new, c = re.subn(r"@(?:dev\.architectury\.injectables\.annotations\.)?ExpectPlatform(?:\.Transformed)?\b[ \t]*\n?[ \t]*",
                             "", new)
            n["expect_platform"] += c
        new, c = re.subn(r"(?m)^[ \t]*static\s*\{\s*if\s*\(\s*<unrepresentable>\.\$assertionsDisabled\s*\)\s*\{\s*\}\s*\}[ \t]*\n",
                         "", new)          # javac's assertion-status initializer, empty once decompiled (illegal in an interface)
        n["assert_guards"] += c
        k = new.count("<unrepresentable>.$assertionsDisabled")
        if k:
            new = new.replace("<unrepresentable>.$assertionsDisabled", "true")
            n["assert_guards"] += k
        if new != t:
            f.write_text(new, encoding="utf-8")
    return n


def wire_deps(work, prov, pid, file_id, mcs, toml_required=(), toml_authority=False):
    """Put each REQUIRED dependency's build for every hop's target into libs/<mc>/, which the build compiles and
    runs against (templates/neoforge-mod/build.gradle), transitive ones too. Two sources, because each misses
    things the other has: the registry's declared dependencies, and the jar's own toml (measured: a mod's toml
    required "emi" and "foundation" that its registry page never declared). A toml id the registry did not cover
    is looked up by name and accepted only when the downloaded jar's own toml declares that mod id. Every jar is
    checked against the target by its OWN minecraft range: a registry tag is not proof (a 1.21.1 build was
    listed for 26.2). GeckoLib is left to the build's own coordinate (uses_geckolib). Anything unresolved is
    reported, never fatal. When the jar has a (neo)forge toml it is the authority: a dependency only the registry
    lists that has no build is a note, not a stop (registry data can be the other loader's), while a toml-required id
    with no build stays fatal for the caller. -> {mc: [file names and notes]}"""
    top = []
    if prov and pid and file_id:
        rc, dd = modreg("deps", "--provider", prov, "--id", pid, "--file", file_id)
        top = [(prov, d["id"]) for d in dd.get("dependencies", []) if d.get("type") == "required"]
    out = {}
    for mc in mcs:
        lib = work / "libs" / mc
        seen, queue, names = set(), list(top), []

        def fetch(p, dep, expect=None):
            rc2, dv = modreg("versions", "--provider", p, "--id", dep, "--loader", "neoforge", "--mc", mc)
            c = dv.get("chosen") if dv.get("has_native") else None
            if not c:
                return None, "no build"
            lib.mkdir(parents=True, exist_ok=True)
            f = lib / c["fileName"]
            if not f.exists():
                modreg("download", "--provider", p, "--id", dep, "--file", c["fileId"], "--out", str(lib) + "/", timeout=600)
            if not f.exists():
                return None, "download failed"
            own, rng = jar_toml(f)
            if expect and expect not in own or not in_range(mc, rng):
                f.unlink()
                return None, (f"does not declare mod id {expect}" if expect and expect not in own
                              else f"{c['fileName']} is for Minecraft {rng}, not {mc}")
            return (c, own), None

        while queue:
            p, dep = queue.pop(0)
            if (p, dep) in seen or "geckolib" in dep.lower():
                continue
            seen.add((p, dep))
            got, why = None, "no build"
            for sid in loader_siblings(dep) if p == "modrinth" else [dep]:
                got, why = fetch(p, sid)
                if got:
                    break
            if not got:
                if toml_authority and (p, dep) in top:
                    names.append(f"{dep} (listed by the registry, no {mc} build: {why}; not what the jar's toml requires)")
                else:
                    names.append(f"{dep} (NO usable {mc} build: {why})")
                continue
            c, _own = got
            names.append(c["fileName"])
            rc3, sub = modreg("deps", "--provider", p, "--id", dep, "--file", c["fileId"])
            queue += [(p, d["id"]) for d in sub.get("dependencies", []) if d.get("type") == "required"]
        declared = set().union(*(jar_toml(f)[0] for f in lib.glob("*.jar"))) if lib.exists() else set()
        for mid in sorted(set(toml_required) - declared - PLATFORM_IDS):
            if "geckolib" in mid.lower():
                continue
            hit = None
            # most mod ids are their Modrinth slug with the hyphens taken out (farmersdelight -> farmers-delight),
            # and a full-text search for the bare id can miss the mod entirely: try those slugs first. fetch()
            # still accepts a build only if its own toml declares `mid`, so a wrong guess costs a lookup, nothing more.
            for slug in slug_guesses(mid):
                got, _why = fetch("modrinth", slug, expect=mid)
                if got:
                    hit = got[0]["fileName"]
                    break
            res = []
            for q in ([] if hit else list(dict.fromkeys([mid, mid.replace("_", " ")]))):   # structure_gel -> "structure gel"
                res += modreg("search", "--query", q, "--loader", "neoforge", "--mc", mc, "--limit", "5")[1].get("results", [])[:5]
            for r in res:
                for pv in r.get("providers", []):
                    got, _why = fetch(pv["provider"], pv["id"], expect=mid)
                    if got:
                        hit = got[0]["fileName"]
                        break
                if hit:
                    break
            names.append(f"{hit} (required by the jar's toml as {mid})" if hit
                         else f"{mid} (REQUIRED by the jar's toml; no {mc} build found that declares it)")
        if names:
            out[mc] = names
    return out


def self_check():
    import tempfile
    ok = norm("Some Mod Name!") == "somemodname"
    with tempfile.TemporaryDirectory() as d:          # decompile artifacts that do not parse
        f = pathlib.Path(d) / "A.java"
        f.write_text("class A { Object m(Ctx c) {\n class 1Cond implements X {\n _Cond/* $VF was: 1Cond*/(Ctx c) {}\n }\n"
                     " if (!<unrepresentable>.$assertionsDisabled && c == null) throw new AssertionError();\n"
                     " return new 1Cond(c); float f = 1F; } }", encoding="utf-8")
        got = fix_decompile_artifacts(d)
        s = f.read_text(encoding="utf-8")
        ok &= got == {"digit_classes": 1, "assert_guards": 1, "constructors": 0, "string_concats": 0, "diamond_casts": 0, "expect_platform": 0} and "class _Cond" in s and "new _Cond(c)" in s
        ok &= "1Cond" not in s.split("$VF was")[0] and "!true &&" in s and "1F" in s
        g = pathlib.Path(d) / "P.java"
        g.write_text("package p;\n\nimport dev.architectury.injectables.annotations.ExpectPlatform;\n\npublic class P {\n"
                     "   @ExpectPlatform\n   @ExpectPlatform.Transformed\n   public static Path dir() {\n"
                     "      return PImpl.dir();\n   }\n}\n", encoding="utf-8")
        got = fix_decompile_artifacts(d)
        s = g.read_text(encoding="utf-8")
        ok &= got["expect_platform"] == 2 and "ExpectPlatform" not in s and "public static Path dir()" in s
        g.write_text("package p;\nimport com.google.auto.service.AutoService;\n@AutoService(Svc.class)\npublic class Q implements Svc {}\n",
                     encoding="utf-8")
        got = fix_decompile_artifacts(d)
        s = g.read_text(encoding="utf-8")
        ok &= got["expect_platform"] == 1 and "AutoService" not in s and "public class Q implements Svc" in s
    ok &= in_range("26.2", "[1.21,)") and not in_range("26.2", "[1.21,1.21.2)") and in_range("1.21.1", "[1.21.1]")
    ok &= not in_range("1.21.1", "[1.20.1,1.21)") and in_range("1.21.1", None)
    import tempfile as _tf
    with _tf.TemporaryDirectory() as d:
        for nm, toml, cls in (("a.jar", "META-INF/mods.toml", b"net/minecraftforge/common/MinecraftForge"),
                              ("b.jar", "META-INF/mods.toml", b"net/neoforged/neoforge/common/NeoForge"),
                              ("c.jar", "META-INF/neoforge.mods.toml", b"net/minecraftforge/x"),
                              ("d.jar", "META-INF/mods.toml", b"net/minecraftforge/x")):
            with zipfile.ZipFile(f"{d}/{nm}", "w") as z:
                z.writestr(toml, "modLoader=\"javafml\"\n"); z.writestr("x/A.class", b"\xca\xfe" + cls)
                if nm == "d.jar":
                    z.writestr("META-INF/neoforge.mods.toml", "modLoader=\"javafml\"\n")
        with zipfile.ZipFile(f"{d}/k.jar", "w") as z:
            z.writestr("a/A.class", b"\xca\xfe Lkotlin/Metadata; x"); z.writestr("a/B.class", b"\xca\xfe x")
            z.writestr("a/C.class", b"\xca\xfe Lkotlin/Metadata; y")
        ok &= kotlin_share(pathlib.Path(d, "k.jar")) == (2, 3) and kotlin_share(pathlib.Path(d, "a.jar")) == (0, 1)
        with zipfile.ZipFile(f"{d}/u.jar", "w") as z:
            for i, b in enumerate((b"net/minecraftforge/x", b"net/minecraftforge/y", b"net/neoforged/a", b"net/neoforged/b",
                                   b"net/fabricmc/api", b"net/minecraft/class_123")):
                z.writestr(f"u/C{i}.class", b"\xca\xfe" + b)
        ok &= loader_families(pathlib.Path(d, "u.jar")) == {"forge", "neoforge", "fabric"}
        rawd = pathlib.Path(d, "raw"); (rawd / "u").mkdir(parents=True); (rawd / "u/C0.java").write_text("", encoding="utf-8")
        ok &= missing_classes(pathlib.Path(d, "u.jar"), rawd) == ["u/C1", "u/C2", "u/C3", "u/C4", "u/C5"]
        ok &= loader_families(pathlib.Path(d, "a.jar")) == set()      # one class only: below the threshold
        ok &= forge_api_jar(pathlib.Path(d, "a.jar")) and not forge_api_jar(pathlib.Path(d, "b.jar")) \
            and not forge_api_jar(pathlib.Path(d, "c.jar")) and forge_api_jar(pathlib.Path(d, "d.jar"))
    with _tf.TemporaryDirectory() as d:            # multi-loader jar: the toml (single-quoted is legal TOML) wins
        with zipfile.ZipFile(f"{d}/m.jar", "w") as z:
            z.writestr("META-INF/mods.toml", "modLoader = 'lowcodefml'\n[[mods]]\n  modId = 'mymod'\n")
            z.writestr("fabric.mod.json", '{"id": "my-mod", "name": "My Mod"}')
        m = read_jar_meta(pathlib.Path(d, "m.jar"))
        ok &= m["modId"] == "mymod" and m["displayName"] == "My Mod" and jar_toml(pathlib.Path(d, "m.jar"))[0] == {"mymod"}
    ok &= loader_siblings("x-fabric") == ["x-fabric", "x", "x-forge", "x-neoforge"] and loader_siblings(123)[0] == "123"
    ok &= "farmers-delight" in slug_guesses("farmersdelight") and slug_guesses("my_mod")[:2] == ["my_mod", "my-mod"]
    t, c = _resugar_constructors("   T f() {\n      Pair var1 = new Pair;\n      String a = g(x);\n"
                                 "      var1./* " + VF_MARK + " Unable to resugar constructor */<init>(a, h(\")\", switch (y) { case 1 -> 2; }));\n"
                                 "      return var1;\n   }\n")
    ok &= c == 1 and "new Pair;" not in t and 'Pair var1 = new Pair(a, h(")", switch (y) { case 1 -> 2; }));' in t \
        and t.index("String a") < t.index("Pair var1")
    sc, k = _resugar_string_concat('x = StringConcatFactory.makeConcatWithConstants<"makeConcatWithConstants","Hi \\u0001, you (\\u0001)">'
                                   '(a.b(1, "x,)"), n);')
    ok &= k == 1 and sc == 'x = ("" + "Hi " + (a.b(1, "x,)")) + ", you (" + (n) + ")");'
    with _tf.TemporaryDirectory() as d:
        with zipfile.ZipFile(f"{d}/h.jar", "w") as z:
            for i in range(30):
                z.writestr(f"m/C{i}.class", b"\xca\xfe Ltop/theillusivec4/curios/api/SlotContext; Lm/X;"
                           + (b" Lmezz/jei/api/IModPlugin;" if i == 0 else b""))
        ok &= hard_optional_deps(pathlib.Path(d, "h.jar"), [{"modId": "curios", "required": False},
                                                            {"modId": "jei", "required": False},
                                                            {"modId": "caelus", "required": False}]) == ["curios"]
    with _tf.TemporaryDirectory() as d:
        with zipfile.ZipFile(f"{d}/i.jar", "w") as z:
            z.writestr("META-INF/mods.toml", "modLoader = 'lowcodefml'\nmods = [\n\t{ modId = 'inl', version = '1.1' },\n]\n"
                       "[[dependencies.inl]]\nmodId='minecraft'\nmandatory=true\nversionRange='[1.20.1]'\n"
                       "[[dependencies.inl]]\nmodId='lib'\nmandatory=true\n")
        m = read_jar_meta(pathlib.Path(d, "i.jar"))
        ok &= m["modId"] == "inl" and m["version"] == "1.1" and m["deps"] == [{"modId": "lib", "required": True}]
        ok &= jar_toml(pathlib.Path(d, "i.jar")) == ({"inl"}, "[1.20.1]")
    with _tf.TemporaryDirectory() as d:
        pathlib.Path(d, "A.java").write_text("class A { void m() { /* " + VF_MARK + " Couldn't be decompiled */ x.<init>(1); "
                                             "Object o = (Supplier<>) () -> 1; } }", encoding="utf-8")
        mk = decompile_markers(d)
        ok &= set(mk) == {"failure marker: Couldn't be decompiled", "raw <init> call", "diamond cast"}
    ok &= in_range("26.2", "[26.2.0,)") and in_range("1.21", "[1.21.0]") and not in_range("26.1", "[26.2.0,)")
    ok &= max(["1.21", "1.21.1"], key=parse_ver) == "1.21.1"
    with tempfile.TemporaryDirectory() as d:          # a toml's OWN mod ids are the [[mods]] blocks, not its deps
        j = pathlib.Path(d) / "dep.jar"
        with zipfile.ZipFile(j, "w") as z:
            z.writestr("META-INF/neoforge.mods.toml", '[[mods]]\nmodId="emi"\n[[dependencies.emi]]\nmodId="minecraft"\n'
                       'versionRange="[1.21.1,1.21.2)"\n[[dependencies.emi]]\nmodId="other"\n')
        own, rng = jar_toml(j)
        ok &= own == {"emi"} and rng == "[1.21.1,1.21.2)"
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        jar = d / "m.jar"
        with zipfile.ZipFile(jar, "w") as z:
            z.writestr("META-INF/mods.toml", 'modLoader="javafml"\nlicense="MIT"\n[[mods]]\nmodId="mymod"\nversion="${file.jarVersion}"\n'
                       'displayName="My Mod"\ndescription=\'\'\'\nLine one\n\'\'\'\n[[dependencies.mymod]]\nmodId="forge"\nmandatory=true\n'
                       '[[dependencies.mymod]]\nmodId="cloth"\nmandatory=false\n[[mixins]]\nconfig="mymod.mixins.json"\n')
            z.writestr("mymod.mixins.json", "{}")
        m = read_jar_meta(jar)
        ok &= m["modId"] == "mymod" and m["version"] is None and m["displayName"] == "My Mod" and m["license"] == "MIT"
        ok &= m["deps"] == [{"modId": "cloth", "required": False}] and m["mixins"] == ["mymod.mixins.json"]
        ok &= m["description"] == "Line one"
        src = d / "src"; (src / "a").mkdir(parents=True)
        (src / "a/Cfg.java").write_text("class Cfg {\n   public static final ModConfigSpec.Builder BUILDER = new ModConfigSpec.Builder();\n"
                                       "   public static final ModConfigSpec SPEC = BUILDER.build();\n"
                                       "   public static final ModConfigSpec.IntValue X = BUILDER.defineInRange(\"x\", 1, 0, 9);\n}\n",
                                       encoding="utf-8")
        ok &= fix_hoisted_spec(src) == ["a/Cfg.java"]
        t = (src / "a/Cfg.java").read_text(encoding="utf-8")
        ok &= t.index("SPEC = BUILDER.build()") > t.index("defineInRange") and fix_hoisted_spec(src) == []
        res = d / "res"; (res / "data/m/recipe").mkdir(parents=True)
        (res / "data/m/recipe/r.json").write_text(json.dumps({"type": "minecraft:crafting_shaped", "conditions": [
            {"type": "forge:not", "value": {"type": "forge:mod_loaded", "modid": "x"}}], "key": {"#": {"tag": "forge:ingots/tin"}}}),
            encoding="utf-8")
        c, unk, tags = neoforge_conditions(res)
        out = json.loads((res / "data/m/recipe/r.json").read_text(encoding="utf-8"))
        ok &= c == 1 and not unk and tags == 1 and "conditions" not in out
        ok &= out["neoforge:conditions"][0] == {"type": "neoforge:not", "value": {"type": "neoforge:mod_loaded", "modid": "x"}}
        (res / "data/m/loot_table").mkdir(parents=True)
        loot = {"type": "minecraft:entity", "pools": [{"rolls": 1, "conditions": [{"condition": "minecraft:killed_by_player"}]}]}
        (res / "data/m/loot_table/l.json").write_text(json.dumps(loot), encoding="utf-8")
        neoforge_conditions(res)
        ok &= json.loads((res / "data/m/loot_table/l.json").read_text(encoding="utf-8")) == loot   # vanilla key untouched
    with tempfile.TemporaryDirectory() as d:    # --from-port reads the port's own build; refuses what it cannot continue
        w = pathlib.Path(d)
        (w / "gradle.properties").write_text("mod_id=mymod\nminecraft_version=1.21.1\n", encoding="utf-8")
        ok &= port_meta(w) == ("mymod", "1.21.1", None)
        (w / "versions").mkdir(); (w / "versions/26.2.properties").write_text("x=1\n", encoding="utf-8")
        ok &= port_meta(w)[2] is not None and "multi-version" in port_meta(w)[2]
        ok &= port_meta(w / "nope")[2] is not None
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


WORKS = []     # workspaces this run touched: their Gradle daemons are stopped on exit


def stop_daemons():
    """An idle Gradle daemon keeps its heap (gigabytes) for hours, and a NeoGradle and a ModDevGradle port each
    leave one. Porting several mods in a row then runs the next compile out of memory and the kernel kills it
    ("Gradle build daemon disappeared unexpectedly"). Stop this run's daemons when it ends, however it ends."""
    for w in WORKS:
        if (w / "gradlew").exists():
            subprocess.run(["bash", "gradlew", "--stop"], cwd=w, capture_output=True, timeout=120)


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        stop_daemons()
    sys.exit(rc)
