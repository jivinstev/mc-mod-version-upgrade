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
import argparse, json, os, pathlib, re, shutil, subprocess, sys, zipfile

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
    r = subprocess.run([sys.executable, str(MODREG), *args], capture_output=True, text=True, encoding="utf-8",
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


def read_jar_meta(jar):
    """mods.toml / neoforge.mods.toml / fabric.mod.json fields we carry into the scaffold."""
    meta = {"deps": [], "mixins": []}
    with zipfile.ZipFile(jar) as z:
        names = z.namelist()
        toml = next((n for n in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml") if n in names), None)
        if toml:
            t = z.read(toml).decode("utf-8", "replace")
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
            meta.update({"modId": f.get("id"), "version": f.get("version"), "displayName": f.get("name"),
                         "description": f.get("description"), "authors": ", ".join(
                             a if isinstance(a, str) else a.get("name", "") for a in f.get("authors", [])),
                         "license": f.get("license") if isinstance(f.get("license"), str) else None})
            meta["mixins"] = sorted(set(meta["mixins"]) | {m if isinstance(m, str) else m.get("config") for m in f.get("mixins", [])})
    if meta.get("version") and "${" in meta["version"]:
        meta["version"] = None
    return meta


# ── setup ───────────────────────────────────────────────────────────────────
def run(cmd, cwd=None, log=None, timeout=3600):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if log:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(f"$ {' '.join(map(str, cmd))}\n{r.stdout[-4000:]}{r.stderr[-4000:]}\n")
    return r


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
    r = run(["java", "-jar", str(vf), "-dgs=1", "-rsy=1", "-rbr=1", str(jar), str(raw)], log=log, timeout=7200)
    if not any(raw.rglob("*.java")):
        raise RuntimeError("Vineflower produced no Java (see setup.log)")
    srcj, res = work / "src/main/java", work / "src/main/resources"
    srcj.mkdir(parents=True, exist_ok=True)
    for f in raw.rglob("*.java"):
        d = srcj / f.relative_to(raw); d.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(f, d)
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
        mp = pathlib.Path(os.environ.get("MIGRATE_WORKSPACE") or pathlib.Path.home() / ".mc-mod-upgrade/work") / f"srg2official-{src_mc}.json"
        mp.parent.mkdir(parents=True, exist_ok=True)
        if not mp.exists():
            run([sys.executable, str(ROOT / "tools/srg-remap/build_mapping.py"), src_mc, str(mp)], log=log)
        run([sys.executable, str(ROOT / "tools/srg-remap/apply_mapping.py"), str(mp), str(srcj)], log=log)
        files = [str(f) for f in srcj.rglob("*.java")]
        for i in range(0, len(files), 200):
            run(["perl", "-pi", str(ROOT / "tools/srg-remap/forge_import_codemod.pl"), *files[i:i + 200]], log=log)
        run([sys.executable, str(ROOT / "tools/srg-remap/mc121_codemod.py"), str(srcj)], log=log)
    elif setup_kind == "intermediary":
        mp = pathlib.Path(os.environ.get("MIGRATE_WORKSPACE") or pathlib.Path.home() / ".mc-mod-upgrade/work") / f"intermediary2official-{src_mc}.json"
        mp.parent.mkdir(parents=True, exist_ok=True)
        if not mp.exists():
            run([sys.executable, str(ROOT / "tools/intermediary-remap/build_mapping.py"), src_mc, str(mp)], log=log)
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
    pk = run([sys.executable, str(ROOT / "tools/park-optional.py"), "--work", str(work), "--group", group], log=log)
    parked = int((re.findall(r"park-optional: (\d+) file", pk.stdout) or ["0"])[0])
    run([sys.executable, str(ROOT / "tools/scaffold-gametest.py"), "--work", str(work)], log=log)
    return {"group": group, "parked_files": parked, "unmapped_names_left": left, "hoisted_spec_fixed": hoisted, "geckolib": uses_gecko,
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
    """'1.21.1' -> (1, 21, 1); '26.2' -> (26, 2); a snapshot or junk sorts first."""
    return tuple(int(x) for x in re.findall(r"\d+", v)) if re.fullmatch(r"\d+(\.\d+)*", v or "") else (0,)


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
            say(f"source: {v['chosen']['mc']} would need an unpacked hop; using the {alt['mc']} build instead")
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
                rc2, dv = modreg("versions", "--provider", prov, "--id", dep["id"], "--loader", "neoforge", "--mc", T)
                if not dv.get("has_native"):
                    need.append(dep.get("name") or dep["id"])
            info["required_deps_missing_at_target"] = need
            if need:
                return stop(21, f"{a.mod} requires {', '.join(need)}, which has no NeoForge {T} build",
                            [f'port it first: python3 tools/port.py "{n}" --to {T}' for n in need]
                            + ["pass --ignore-deps to port this one anyway (its Gate B will not load without them)"])
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
    loader = (rm.get("loader") or "forge").lower()
    src_mc = (info["source"].get("chosen") or {}).get("mc") or re.sub(r"[\[\](),]", "", (rm.get("mcRange") or "").split(",")[0]) or "1.20.1"
    meta["modId"] = meta.get("modId") or (rm.get("modIds") or [None])[0]
    if not meta["modId"]:
        return stop(23, "could not read the jar's mod id", ["pass a different jar"])
    hops = route.plan((loader, src_mc), ("neoforge", T))
    if hops is None:
        return stop(22, f"no route from {loader} {src_mc} to NeoForge {T} in tools/routes.tsv",
                    ["add a row (and, ideally, a pack) for the missing hop"])
    work = pathlib.Path(a.mods_dir) / meta["modId"]
    say(f"{meta['modId']}: {loader} {src_mc} -> NeoForge {T}, {len(hops)} hop(s): "
        + " | ".join(f"{h['from']} -> {h['to']} [{h['kind']}, pack: {'yes' if h['pack'] else 'NONE'}]" for h in hops))
    nopack = [h for h in hops if not h["pack"]]
    if nopack and not a.allow_no_pack:
        return stop(22, f"no recipe pack for {', '.join(h['from'] + ' -> ' + h['to'] for h in nopack)}: the workers would do that whole hop",
                    ["continue anyway: rerun with --allow-no-pack (likely several times the cost of a packed hop)",
                     "build a pack for the hop first (tools/learn-pack.py from a finished port of that kind)"])
    if a.plan_only:
        print(json.dumps({**info, "modid": meta["modId"], "work": str(work), "hops": hops}, indent=1)); return 0
    state = load_state(work)
    state.update({"info": info, "hops": [h["from"] + "->" + h["to"] for h in hops], "modid": meta["modId"]})
    state.pop("stopped", None)
    log = work / "setup.log"
    # ── setup
    if "setup" not in state["done"]:
        say(f"setup: scaffold {work}, decompile, official names, metadata, datapack layout, baseline GameTest")
        try:
            work.mkdir(parents=True, exist_ok=True)
            save_state(work, state)
            info_setup = setup(work, jar, loader, src_mc, hops[0]["setup"], meta, log)
        except Exception as e:  # noqa: BLE001 -- a setup failure is a STOP with the reason, never a traceback-and-guess
            return stop(24, f"setup failed: {e}", ["read mods/<modid>/setup.log", "fix the cause and rerun"], state, work)
        state["setup"] = info_setup; state["done"].append("setup"); save_state(work, state)
        say(f"setup done: group {info_setup['group']}, {info_setup['unmapped_names_left']} unmapped names left, "
            f"{len(info_setup['hoisted_spec_fixed'])} hoisted config SPEC(s) fixed, "
            f"{info_setup.get('parked_files', 0)} file(s) of optional integrations/datagen parked (MIGRATION.md)")
    if "deps-wired" not in state["done"] and not a.ignore_deps:
        src = info["source"]
        if src.get("provider") and src.get("fileId"):
            wired = wire_deps(work, src["provider"], src["id"], src["fileId"], sorted({h["to_mc"] for h in hops}, key=parse_ver))
            state["deps_wired"] = wired
            if wired:
                say("dependencies: " + "; ".join(f"{mc}: {', '.join(n for n in names)}" for mc, names in wired.items()))
        else:
            say("dependencies: the jar is not on a registry; required mods its toml names must be put in "
                "libs/<minecraft version>/ by hand: " + (", ".join(d["modId"] for d in meta.get("deps", []) if d["required"]) or "none"))
        state["done"].append("deps-wired"); save_state(work, state)
    if a.stop_after == "setup":
        say("stopped after setup (--stop-after)"); return 0
    return run_hops(a, T, hops, work, state, meta)


def packed_source(prov, pid, T, v, tries=6):
    """When the chosen build's route has a hop with no pack, the newest OLDER build whose route is fully packed,
    or None. Only builds the registry says exist are considered, newest first."""
    c = v.get("chosen") or {}
    def packed(loader, mc):
        hops = route.plan((loader, mc), ("neoforge", T))
        return hops is not None and all(h["pack"] for h in hops)
    if not c or packed(c.get("loader") or "neoforge", c["mc"]):
        return None
    for mc in sorted(v.get("older_mcs") or [], key=parse_ver, reverse=True)[:tries * 2]:
        if mc == c["mc"] or not re.fullmatch(r"\d+(\.\d+)+", mc):
            continue
        for loader in ("neoforge", "forge"):
            if not packed(loader, mc):
                continue
            rc, dv = modreg("versions", "--provider", prov, "--id", pid, "--loader", loader, "--mc", mc)
            alt = dv.get("chosen") if dv.get("has_native") else None
            if alt:
                return alt
            tries -= 1
            if tries <= 0:
                return None
    return None


def wire_deps(work, prov, pid, file_id, mcs):
    """Put each REQUIRED dependency's build for every hop's target into libs/<mc>/, which the build compiles and
    runs against (templates/neoforge-mod/build.gradle). Transitive required dependencies too. GeckoLib is left to
    the build's own coordinate (uses_geckolib). A dependency with no build for a target is reported, not fatal:
    the name route already stops on it, and a jar path gets to decide. -> {mc: [file names]}"""
    rc, dd = modreg("deps", "--provider", prov, "--id", pid, "--file", file_id)
    top = [d["id"] for d in dd.get("dependencies", []) if d.get("type") == "required"]
    out = {}
    for mc in mcs:
        lib = work / "libs" / mc
        seen, queue, names = set(), list(top), []
        while queue:
            dep = queue.pop(0)
            if dep in seen or "geckolib" in dep.lower():
                continue
            seen.add(dep)
            rc2, dv = modreg("versions", "--provider", prov, "--id", dep, "--loader", "neoforge", "--mc", mc)
            c = dv.get("chosen") if dv.get("has_native") else None
            if not c:
                names.append(f"{dep} (NO {mc} BUILD)")
                continue
            lib.mkdir(parents=True, exist_ok=True)
            if not (lib / c["fileName"]).exists():
                modreg("download", "--provider", prov, "--id", dep, "--file", c["fileId"], "--out", str(lib) + "/", timeout=600)
            names.append(c["fileName"])
            rc3, sub = modreg("deps", "--provider", prov, "--id", dep, "--file", c["fileId"])
            queue += [d["id"] for d in sub.get("dependencies", []) if d.get("type") == "required"]
        if names:
            out[mc] = names
    return out


def self_check():
    import tempfile
    ok = norm("Some Mod Name!") == "somemodname"
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


if __name__ == "__main__":
    sys.exit(main())
