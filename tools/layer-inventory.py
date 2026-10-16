#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The base build's behaviour inventory, and every layer target's account of it (the fork-versions skill, R2).

    python3 tools/layer-inventory.py build --repo DIR [--dry-run] [--out FILE]
    python3 tools/layer-inventory.py check --repo DIR --target T [--json] [--inventory FILE] [--no-write]
    python3 tools/layer-inventory.py --self-check

WHY.  The gates cannot see rendering, and a wrong stub review ("handlers moved, same effect") was once inherited by
eight targets and found a day later. So the question "does this target still do what the base does?" is asked at the
START of a buildout, from the base, and answered per target -- not discovered at the end in a real client.

BUILD writes .multiversion/INVENTORY.json from the authors' base sources (src/<set>/java and src/<set>/resources: the
layer branch never edits them, so they are the base build's code). Each item is (source set, file, kind, key):
  mixin       each @Mixin target class                         } fork-port.py's hook_targets(), imported, so a stub
  injection   each @Inject/@Redirect/@Modify*/@Wrap*: method=   } review and the inventory read a hook the same way
              and every @At                                    }
  overwrite   each @Overwrite by method name                   }
  event       each @SubscribeEvent handler by its event type, and each addListener(...) by the event class it names
              (or the handler method's parameter type)
  render-class  a class that extends/implements a render-path type (a *Renderer, *RenderLayer, *Model, *Screen, a
              particle, a GUI layer, an IClient*Extensions)
  render-use  a RenderType(s)./RenderPipeline(s)./CoreShaders. member, a ShaderInstance / setShader / get*Shader user
  registry    each registry the mod adds to (DeferredRegister.create*(...), Registry.register(BuiltInRegistries.X ...))
  shader      each core/program shader json, and each .vsh/.fsh/.glsl source, under assets/<ns>/shaders/
  shader-uniform / shader-sampler  each uniform and sampler a shader json declares
The file is deterministic (no time stamp); it records the git tree of src/ it was made from, and `check` warns when
the authors' sources have changed since (re-run `build` after a keep-current merge).

CHECK accounts for every item on target T, against T's PREPARED tree (build/mc<T>/mv/java, mv/<set>-java -- what the
compile and the mixin audit see; a non-main set the layer does not prepare compiles from the authors' tree) and T's
effective resources (targets/<chain>/resources over src/<set>/resources). Per item:
  same        the key is there in the same file -- or, in a file the target takes from the authors' tree through the
              rename tables only, its renamed counterpart of the same kind (recorded as "now")
  changed     in a file a target OVERLAYS, a counterpart of the same kind with a different key: accounted only when a
              review names the file (below)
  moved       not there, and a REVIEWED STUBS.json entry of T for that file (no open `recheck`), a REVIEW.md of T naming
              the file or the item id in a row with verdict `ok` and a note (a `?` or not-ok row, or a mention
              in a note, does not) -- for a hook (mixin, injection, overwrite, event, shader-uniform/-sampler) a row
              naming the file accounts only when its note names the item id; an `item` row naming the id always does --
              or a BEHAVIOUR.json entry {"item", "status": "moved", "where", "review"}
  dropped     the file is in the chain's drops.txt under a comment (the comment is the reason), or a BEHAVIOUR.json entry
              {"item", "status": "dropped", "reason"}
  unaccounted none of the above: the target is not done. The list is printed (and given in --json).
The account is written to .multiversion/targets/<t>/INVENTORY-CHECK.json (unless --no-write or --inventory), and
inventory_unaccounted(repo, t) -> [reasons] is the same check as a function (fork-port.py's done check).
BEHAVIOUR.json lives in .multiversion/targets/<t>/ and is read over T's inherits chain, nearest first (an item a parent
dropped stays dropped, marked inherited). tools/layer-test-plan.py lists every item that is not "same" as a manual-test
candidate.

A SINGLE-VERSION fork (no .multiversion/base.properties) has no inventory requirement: `build` writes nothing (exit 2,
like layer-deps.py), `check` says so and exits 0.

Exit codes: 0 built / every item accounted for; 16 `check`: unaccounted items (listed); 2 bad input (no layer, no
INVENTORY.json, the target's prepared tree is missing -- run `.multiversion/build.sh <t> compileJava` first).
Standard library only; reads files, runs nothing but `git rev-parse`.
"""
import argparse, difflib, fnmatch, importlib.util, json, pathlib, re, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
EXIT_UNACCOUNTED = 16
KINDS = ("mixin", "injection", "overwrite", "event", "render-class", "render-use", "registry", "shader",
         "shader-uniform", "shader-sampler")
INV = "INVENTORY.json"
HOOK_KINDS = ("mixin", "injection", "overwrite", "event")
# what vanilla's pipeline snippets (1.21.5 on) declare for every shader: a mod's pipeline need not name them
VANILLA_SHADER_INPUTS = {"ModelViewMat", "ProjMat", "ColorModulator", "TextureMat", "ModelOffset", "FogStart", "FogEnd",
                         "FogColor", "FogShape", "GameTime", "ScreenSize", "LineWidth", "Light0_Direction",
                         "Light1_Direction", "GlintAlpha", "Sampler0", "Sampler1", "Sampler2"}


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_FP = None


def hook_targets(text):
    """fork-port.py's hook_targets(): what STUBS.json's "hooks" field and the inherited-review check are made of."""
    global _FP
    if _FP is None:
        _FP = _load("fork_port_for_inventory", "fork-port.py")
    return _FP.hook_targets(text)


def review_ok_names(text):
    """{file or id: note} of REVIEW.md's rows (fork-port.py's parse_review()) with verdict `ok` and a note: only these
    account for an item. A `?` row (review_skeleton's), a not-ok row or a mention in a note accounts for nothing."""
    global _FP
    if _FP is None:
        _FP = _load("fork_port_for_inventory", "fork-port.py")
    _fields, rows = _FP.parse_review(text)
    return {f: note for (_k, f), (v, note) in rows.items() if v == "ok" and note.strip() not in ("", "?")}


# the items a file-level row cannot account for: a hook a reviewer must have looked at by name (measured: one ok row
# per file accounted for every injection in it, those the reviewer never looked at too)
REVIEW_HOOK_KINDS = HOOK_KINDS + ("shader-uniform", "shader-sampler")


def review_names(names, r):
    """Does an ok row account for item r? A row naming its id (an `item` row) always does. A row naming its file (or a
    path ending in /<its file>: an overlay row's .multiversion/targets/<t>/java/<file>) does for a non-hook item, and
    for a hook (REVIEW_HOOK_KINDS) only when its note names the item's id. names: review_ok_names()."""
    if r["id"] in names:
        return True
    rows = [note for n, note in names.items() if n == r["file"] or n.endswith("/" + r["file"])]
    if r["kind"] in REVIEW_HOOK_KINDS:
        return any(r["id"] in note for note in rows)
    return bool(rows)


def _plan():
    return _load("layer_test_plan_for_inventory", "layer-test-plan.py")


COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
STRINGS = re.compile(r'"(?:\\.|[^"\\])*"')
# render-path supertypes (both eras' names: a 26.x overlay's counterpart must be recognised too)
RENDER_SUPER = re.compile(r"\b(\w*Renderer|\w*RenderLayer|\w*RenderState|\w*Model|\w*Screen|\w*Particle|ParticleProvider|"
                          r"IClient\w*Extensions|LayeredDraw\.Layer|GuiLayer|IGuiOverlay|\w*Overlay)\b")
CLASS_HEAD = re.compile(r"\b(?:class|record|enum|interface)\s+(\w+)([^{;]*)\{")
RENDER_USE = re.compile(r"\b(?:(RenderTypes?|RenderPipelines?|CoreShaders|RenderStateShard)\.([A-Za-z_]\w*)|"
                        r"(ShaderInstance|ShaderProgram|CompiledShaderProgram|RenderPipeline)\b|"
                        r"RenderSystem\.(setShader\w*)|GameRenderer(?:::|\.)(get\w*Shader)\b)")
REGISTRY = re.compile(r"DeferredRegister\.(create\w*)\(\s*(?:(?:[\w.]*?(?:Registries|Keys)\.)?(\w+))?|"
                      r"\bRegistry\.register(?:ForHolder)?\(\s*(?:[\w.]*?(?:BuiltInRegistries|Registries)\.)(\w+)")
CREATE_NAMED = {"createItems": "ITEM", "createBlocks": "BLOCK", "createDataComponents": "DATA_COMPONENT_TYPE",
                "createEntities": "ENTITY_TYPE"}
ADD_LISTENER = re.compile(r"\.addListener\(\s*(?:EventPriority\.\w+\s*,\s*)?(?:(?:true|false)\s*,\s*)?"
                          r"(?:([\w.$]+)\.class|(?:[\w.]+|this)::(\w+))")


def _param_type(text, method):
    m = re.search(r"\b" + re.escape(method) + r"\s*\(\s*(?:final\s+)?([\w.$]+)(?:<[^>]*>)?\s+\w+\s*\)", text)
    return m.group(1) if m else None


def java_items(text):
    """[(kind, key)] for one Java file, in order of first appearance, de-duplicated."""
    out = []
    for h in hook_targets(text):
        kind = ("mixin" if h.startswith("@Mixin ") else "overwrite" if h.startswith("@Overwrite ")
                else "event" if h.startswith("@SubscribeEvent ") else "injection")
        out.append((kind, h))
    t = COMMENTS.sub("", text)
    code = STRINGS.sub('""', t)
    for m in ADD_LISTENER.finditer(code):
        ev = m.group(1) or _param_type(code, m.group(2)) or f"handler {m.group(2)}"
        out.append(("event", f"addListener {ev}"))
    for m in CLASS_HEAD.finditer(code):
        sup = sorted(set(RENDER_SUPER.findall(re.sub(r"<[^<>]*>", "", re.sub(r"<[^<>]*>", "", m.group(2))))))
        if sup and re.search(r"\b(?:extends|implements)\b", m.group(2)):
            out.append(("render-class", f"class {m.group(1)} : {', '.join(sup)}"))
    for m in RENDER_USE.finditer(code):
        if m.group(1):
            key = f"{m.group(1)}.{m.group(2)}"
        elif m.group(3):
            key = m.group(3)
        elif m.group(4):
            key = f"RenderSystem.{m.group(4)}"
        else:
            key = f"GameRenderer.{m.group(5)}"
        out.append(("render-use", key))
    for m in REGISTRY.finditer(code):
        if m.group(1):
            reg = m.group(2) if m.group(1) == "create" else CREATE_NAMED.get(m.group(1), m.group(1))
        else:
            reg = m.group(3)
        out.append(("registry", f"registry {reg}"))
    return list(dict.fromkeys(out))


def shader_items(rel, text):
    """[(kind, key)] for one file under assets/<ns>/shaders/ (rel is relative to the resources root)."""
    m = re.match(r"assets/([^/]+)/shaders/(.+)\.(json|vsh|fsh|glsl)$", rel)
    if not m:
        return []
    ns, name, ext = m.groups()
    sid = f"{ns}:{name}"
    out = [("shader", f"{sid}.{ext}")]
    if ext == "json":
        try:
            data = json.loads(text)
        except ValueError:
            return out
        for k, kind in (("uniforms", "shader-uniform"), ("samplers", "shader-sampler")):
            for u in data.get(k, []) if isinstance(data, dict) else []:
                if isinstance(u, dict) and u.get("name"):
                    out.append((kind, f"{sid} {u['name']}"))
    return out


def _sets(repo):
    src = repo / "src"
    return sorted(d.name for d in src.iterdir() if d.is_dir()) if src.is_dir() else []


def _rels(root, pattern):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob(pattern) if p.is_file()) if root.is_dir() else []


def _read(p):
    return pathlib.Path(p).read_text(encoding="utf-8", errors="replace")


def scan_tree(java_dirs, res_dirs):
    """{(set, file): [(kind, key)]} -- java_dirs {set: dir}, res_dirs {set: [dirs, nearest first]} (a resource in a
    nearer dir hides the same path in a farther one, as Gradle's srcDirs order does). A file is named by its path
    under its root: java files as `<rel>.java`, resources as `resources/<rel>`."""
    out = {}
    for sset, d in java_dirs.items():
        for rel in _rels(d, "*.java"):
            items = java_items(_read(d / rel))
            if items:
                out[(sset, rel)] = items
    for sset, dirs in res_dirs.items():
        seen = set()
        for d in dirs:
            for rel in _rels(d, "*"):
                if "/shaders/" not in "/" + rel or rel in seen:
                    continue
                seen.add(rel)
                items = shader_items(rel, _read(d / rel))
                if items:
                    out[(sset, "resources/" + rel)] = items
    return out


def item_id(sset, file, kind, key):
    return f"{sset}:{file}#{kind}:{key}"


def src_tree(repo):
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD:src"], capture_output=True, text=True, encoding="utf-8")
    return r.stdout.strip() if r.returncode == 0 else None


def build_inventory(repo):
    repo = pathlib.Path(repo)
    sets = _sets(repo)
    found = scan_tree({s: repo / "src" / s / "java" for s in sets if (repo / "src" / s / "java").is_dir()},
                      {s: [repo / "src" / s / "resources"] for s in sets if (repo / "src" / s / "resources").is_dir()})
    items = [{"id": item_id(s, f, k, key), "set": s, "file": f, "kind": k, "key": key}
             for (s, f), lst in sorted(found.items()) for k, key in lst]
    counts = {k: sum(1 for i in items if i["kind"] == k) for k in KINDS}
    base = _plan().read_props(repo / ".multiversion/base.properties").get("minecraft_version", "")
    inv = {"generated_by": "tools/layer-inventory.py build", "base": base, "src_tree": src_tree(repo),
           "counts": counts, "items": items}
    return inv


# ---------------------------------------------------------------- per-target account

def _drops(mv, names, dir_of=None):
    """{rel: reason or ""} over the chain's drops.txt (a comment block above a run of entries is their reason).
    dir_of(t): the directory holding t's drops.txt (default the layer's targets/<t>)."""
    out = {}
    for t in reversed(names):
        p = (dir_of(t) if dir_of else mv / "targets" / t) / "drops.txt"
        if not p.is_file():
            continue
        note, pending = "", []
        for raw in _read(p).splitlines():
            s = raw.strip()
            if not s:
                note = ""
                continue
            if s.startswith("#"):
                pending.append(s.lstrip("#").strip())
                continue
            if pending:
                note, pending = " ".join(pending), []
            rel, _, tail = s.partition("#")
            out[rel.strip()] = (tail.strip() or note) + (f" (drops.txt of {t})" if (tail.strip() or note) else "")
    return out


def _stub_files(tdir, branch=False):
    """{(set, rel): (reviewed?, review or open reason)} for T's STUBS.json entries (branch: a version branch's, whose
    entries name src/<set>/java/<rel>)."""
    out = {}
    try:
        entries = json.loads(_read(tdir / "STUBS.json")) if (tdir / "STUBS.json").is_file() else []
    except ValueError:
        entries = []
    for e in entries if isinstance(entries, list) else []:
        if not isinstance(e, dict) or not e.get("file"):
            continue
        m = re.search(r"/targets/[^/]+/(?:([\w-]+)/)?java/(.+)$", e["file"])
        if not m and branch:
            m = re.match(r"^src/([\w-]+)/java/(.+)$", e["file"])
        if not m:
            continue
        ok = bool(e.get("reviewed")) and not e.get("recheck")
        out[(m.group(1) or "main", m.group(2))] = (ok, e.get("reviewed") if ok else (e.get("recheck") or "stub not reviewed"))
    return out


def _behaviour(mv, names, dir_of=None):
    """{item id: (entry, from target)} over the chain's BEHAVIOUR.json, nearest first; [problems]. dir_of as _drops."""
    out, bad = {}, []
    for t in names:
        p = (dir_of(t) if dir_of else mv / "targets" / t) / "BEHAVIOUR.json"
        if not p.is_file():
            continue
        try:
            data = json.loads(_read(p))
        except ValueError as e:
            bad.append(f"{p.relative_to(mv.parent).as_posix()}: not JSON ({e})")
            continue
        for e in data if isinstance(data, list) else []:
            if isinstance(e, dict) and e.get("item") and e["item"] not in out:
                out[e["item"]] = (e, t)
    return out, bad


def _behaviour_for(beh, iid):
    """The BEHAVIOUR.json entry for an item: its exact id, else the first (nearest target first) whose `item` is a
    pattern with `*` matching it (one entry for a group, e.g. every core shader json of the mod)."""
    if iid in beh:
        return beh[iid]
    for k, v in beh.items():
        if "*" in k and fnmatch.fnmatchcase(iid, k):
            return v
    return None


def prepared_dirs(repo, target, sets):
    """{set: prepared java dir} for target; a non-main set the layer does not prepare compiles from src/<set>/java."""
    b = repo / "build" / f"mc{target}" / "mv"
    out = {}
    for s in sets:
        if not (repo / "src" / s / "java").is_dir():
            continue
        p = b / ("java" if s == "main" else f"{s}-java")
        out[s] = p if p.is_dir() else (None if s == "main" else repo / "src" / s / "java")
    return out


def _pair(base, now):
    """Greedy best pairs between two key lists (same kind, already without exact matches): [(b, n)], unpaired base."""
    cand = sorted(((difflib.SequenceMatcher(None, b, n).ratio(), i, j) for i, b in enumerate(base) for j, n in enumerate(now)),
                  reverse=True)
    used_b, used_n, pairs = set(), set(), []
    for r, i, j in cand:
        if r < 0.5 or i in used_b or j in used_n:
            continue
        used_b.add(i); used_n.add(j); pairs.append((base[i], now[j]))
    return pairs, [b for i, b in enumerate(base) if i not in used_b]


def account(repo, target, inv=None):
    """{"target", "items": [{id, kind, set, file, key, status, detail, now?}], "unaccounted": [ids], "notes": [...]}.
    Raises ValueError for a missing inventory or prepared tree."""
    repo = pathlib.Path(repo).resolve()
    mv = repo / ".multiversion"
    lp = _plan()
    if inv is None:
        p = mv / INV
        if not p.is_file():
            raise ValueError(f"no .multiversion/{INV}: run tools/layer-inventory.py build --repo {repo} first")
        inv = json.loads(_read(p))
    names = lp.chain(mv, target)
    if not names:
        raise ValueError(f"no such target: .multiversion/targets/{target}/target.properties")
    sets = _sets(repo)
    pdirs = prepared_dirs(repo, target, sets)
    if pdirs.get("main", 1) is None:
        raise ValueError(f"no prepared tree build/mc{target}/mv/java: run .multiversion/build.sh {target} compileJava first")
    res = {s: [mv / "targets" / c / "resources" for c in names if s == "main"] + [repo / "src" / s / "resources"]
           for s in sets}
    res = {s: [d for d in v if d.is_dir()] for s, v in res.items()}
    now = scan_tree({s: d for s, d in pdirs.items() if d}, {})
    overlaid = {(s, rel) for s in sets for c in names for rel in lp.java_rels(lp.overlay_dir(mv, c, s))}
    drops = _drops(mv, names)
    stubs = _stub_files(mv / "targets" / target)
    review_p = mv / "targets" / target / "REVIEW.md"
    review = review_ok_names(_read(review_p)) if review_p.is_file() else {}
    beh, notes = _behaviour(mv, names)
    src_moved = bool(inv.get("src_tree") and src_tree(repo) and inv["src_tree"] != src_tree(repo))
    return _account(target, inv, pdirs, res, sets, now, overlaid, drops, stubs, review, beh, notes, src_moved)


def _account(target, inv, pdirs, res, sets, now, overlaid, drops, stubs, review, beh, notes, src_moved):
    """The account itself, from what account() (a layer target) or account_branch() (a version branch) gathered:
    pdirs {set: java dir}, res {set: [resource dirs, nearest first]}, now scan_tree(pdirs), overlaid {(set, rel)} (files
    a person changed, not the rename tables alone), drops {rel: reason}, stubs _stub_files(), review review_ok_names(),
    beh _behaviour(), notes [..], src_moved: the base's sources changed since the inventory was built."""
    lp = _plan()
    present = now
    java_text = None          # the prepared trees' Java, read once when a 1.21.5+ shader item needs it

    json_read = lp.mc_key(target) < lp.mc_key("1.21.5")

    def prepared_java():
        nonlocal java_text
        if java_text is None:
            java_text = "\n".join(_read(p) for dd in pdirs.values() if dd for p in sorted(dd.rglob("*.java")))
        return java_text

    def effective(sset, ns, path):
        """The target's copy of assets/<ns>/shaders/<path> (nearest resources dir first), or None."""
        for dd in res.get(sset, []):
            q = dd / "assets" / ns / "shaders" / path
            if q.is_file():
                return _read(q)
        return None

    def shader_status(sset, it):
        """(status, detail) for a shader / uniform / sampler item: same, or missing (to be accounted for).
        Up to 1.21.4 a core shader is its json (uniforms and samplers declared there); from 1.21.5 the json is not read
        and a shader is a RenderPipeline in Java: it must be named there (its path), and so must each sampler and
        uniform the authors' json declared, unless it is one vanilla's pipeline snippets supply."""
        if it["kind"] == "shader":
            sid, ext = it["key"].rsplit(".", 1)
            ns, name = sid.split(":", 1)
            if effective(sset, ns, f"{name}.{ext}") is None:
                return "missing", "the target's resources do not have it"
            if ext != "json" or json_read:
                return "same", "the target's resources have it"
            jt = prepared_java()
            if f'"{name}"' in jt or f'"{ns}:{name}"' in jt or f'/{pathlib.PurePosixPath(name).name}"' in jt \
                    or f'"{pathlib.PurePosixPath(name).name}"' in jt:
                return "same", "named by the target's prepared Java (a RenderPipeline)"
            return "missing", "from 1.21.5 a core shader json is not read, and no prepared Java names the shader"
        sid, word = it["key"].rsplit(" ", 1)
        ns, name = sid.split(":", 1)
        if json_read:
            js = effective(sset, ns, f"{name}.json") or ""
            if re.search(r'"name"\s*:\s*"' + re.escape(word) + '"', js):
                return "same", "declared by the target's shader json"
            return "missing", "the target's shader json does not declare it"
        if f'"{word}"' in prepared_java():
            return "same", "named in the target's prepared Java (a pipeline declaration)"
        if word in VANILLA_SHADER_INPUTS:
            return "same", "a uniform/sampler vanilla's pipeline snippets supply"
        return "missing", "from 1.21.5 the json is not read, and no prepared Java (its RenderPipeline) names it"

    by_file = {}
    for it in inv.get("items", []):
        by_file.setdefault((it["set"], it["file"]), []).append(it)
    out = []
    for (s, f), its in sorted(by_file.items()):
        java = f.endswith(".java") and not f.startswith("resources/")
        is_overlay = (s, f) in overlaid
        have = present.get((s, f))
        exists = bool(java and pdirs.get(s) and (pdirs[s] / f).is_file())
        remaining = {}
        for it in its:
            r = dict(it)
            if have and (it["kind"], it["key"]) in have and not it["kind"].startswith("shader"):
                r.update(status="same", detail="unchanged")
                out.append(r)
                continue
            if it["kind"].startswith("shader"):
                st_, why = shader_status(s, it)
                r.update(status=st_, detail=why)
                out.append(r)
                continue
            remaining.setdefault(it["kind"], []).append(r)
        for kind, rs in remaining.items():
            left = [x["key"] for x in rs]
            cands = [k for (kk, k) in (have or []) if kk == kind and k not in {i["key"] for i in its if i["kind"] == kind}]
            pairs, unpaired = _pair(left, cands) if exists else ([], left)
            pmap = dict(pairs)
            for r in rs:
                if r["key"] in pmap and not is_overlay:
                    r.update(status="same", detail="through the rename tables", now=pmap[r["key"]])
                elif r["key"] in pmap:
                    r.update(status="changed", detail="the target's overlay has a different " + kind, now=pmap[r["key"]])
                elif exists and not is_overlay and kind not in HOOK_KINDS:
                    # a table-only file: a shared/target row rewrote the call (a render API that moved, a helper);
                    # the rows are the reviewed, catalogued part. A HOOK that vanished there is not let through.
                    r.update(status="same", detail="rewritten by the rename tables (no counterpart key)")
                else:
                    r.update(status="missing", detail=("not in the target's file" if exists else
                                                       "the file is not in the target's prepared tree"))
                out.append(r)
    # account for every item that is not the same
    unacc = []
    for r in out:
        if r["status"] == "same":
            continue
        rel = r["file"][len("resources/"):] if r["file"].startswith("resources/") else r["file"]
        b = _behaviour_for(beh, r["id"])
        st = stubs.get((r["set"], r["file"]))
        if b:
            e, t = b
            inh = f" (inherited from {t})" if t != target else ""
            if e.get("status") == "dropped" and str(e.get("reason", "")).strip():
                r.update(status="dropped", detail=e["reason"] + inh)
                continue
            if e.get("status") == "moved" and str(e.get("where", "")).strip() and str(e.get("review", "")).strip():
                r.update(status="moved", detail=f"to {e['where']}: {e['review']}{inh}")
                continue
            r["detail"] += f"; BEHAVIOUR.json of {t} has an incomplete entry (dropped needs a reason; moved a where and a review)"
        if r["status"] == "missing" and r["file"] in drops and r["set"] in ("main",) + tuple(sets) and drops[r["file"]]:
            r.update(status="dropped", detail=drops[r["file"]])
            continue
        if r["status"] == "missing" and rel in drops and drops[rel]:
            r.update(status="dropped", detail=drops[rel])
            continue
        if st and st[0]:
            r.update(status="moved" if r["status"] == "missing" else "changed",
                     detail=f"{r['detail']}; reviewed in STUBS.json: {str(st[1])[:300]}", accounted=True)
            continue
        if review_names(review, r):
            r.update(status="moved" if r["status"] == "missing" else "changed",
                     detail=f"{r['detail']}; an ok row of REVIEW.md names it", accounted=True)
            continue
        if st:
            r["detail"] += f"; STUBS.json entry open: {str(st[1])[:200]}"
        r["was"] = r["status"]
        r["status"] = "unaccounted"
        unacc.append(r["id"])
    if src_moved:
        notes.append(f"the authors' src/ changed since {INV} was built: re-run tools/layer-inventory.py build")
    counts = {}
    for r in out:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"target": target, "items": out, "unaccounted": unacc, "counts": counts, "notes": notes}


# ---------------------------------------------------------------- CLI

def is_layer(repo):
    return (pathlib.Path(repo) / ".multiversion/base.properties").is_file()


def cmd_build(a):
    repo = pathlib.Path(a.repo).resolve()
    if not is_layer(repo) and _fb().checkout_version(repo):
        try:
            dest, inv = build_branch_inventory(repo)
        except ValueError as e:
            print(f"layer-inventory: {e}"); return 2
        dest = pathlib.Path(a.out) if getattr(a, "out", None) else dest
        if not a.dry_run:
            dest.write_text(json.dumps(inv, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"layer-inventory: {len(inv['items'])} item(s) from the port branch" + ("" if a.dry_run else f" -> {dest}"))
        return 0
    if not is_layer(repo):
        print("layer-inventory: no .multiversion/ in the fork (a single-version fork has no inventory requirement)")
        return 2
    inv = build_inventory(repo)
    text = json.dumps(inv, indent=1, ensure_ascii=False) + "\n"
    c = ", ".join(f"{k} {v}" for k, v in inv["counts"].items() if v)
    dest = pathlib.Path(a.out) if getattr(a, "out", None) else repo / ".multiversion" / INV
    if not a.dry_run:
        dest.write_text(text, encoding="utf-8")
    print(f"layer-inventory: {len(inv['items'])} item(s) from the base ({c or 'none'})"
          + ("" if a.dry_run else f" -> {dest}"))
    return 0


def check(repo, target, inventory=None):
    """(exit code, report dict or None, message lines) -- what fork-port.py calls. inventory: a path to read instead
    of .multiversion/INVENTORY.json."""
    repo = pathlib.Path(repo).resolve()
    if not is_layer(repo):
        return 0, None, ["layer-inventory: single-version fork: no inventory requirement"]
    try:
        inv = json.loads(_read(inventory)) if inventory else None
        rep = account(repo, target, inv)
    except ValueError as e:
        return 2, None, [f"layer-inventory: {e}"]
    lines = [f"layer-inventory: {target}: " + ", ".join(f"{k} {v}" for k, v in sorted(rep["counts"].items()))]
    lines += [f"layer-inventory: note: {n}" for n in rep["notes"]]
    for r in rep["items"]:
        if r["status"] == "unaccounted":
            lines.append(f"layer-inventory: UNACCOUNTED {r['id']} ({r['detail']})")
    if rep["unaccounted"]:
        lines.append(f"layer-inventory: {target} is NOT done: {len(rep['unaccounted'])} base behaviour item(s) unaccounted for; "
                     f"record each in .multiversion/targets/{target}/BEHAVIOUR.json "
                     '({"item": ID, "status": "dropped", "reason": ...} or {"item": ID, "status": "moved", "where": ..., '
                     '"review": ...}), or review the file\'s STUBS.json entry')
        return EXIT_UNACCOUNTED, rep, lines
    return 0, rep, lines


CHECK_FILE = "INVENTORY-CHECK.json"


def check_text(rep):
    """The account as INVENTORY-CHECK.json holds it (deterministic)."""
    return json.dumps(rep, indent=1, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------- a version branch (tools/fork-branches.py)

def _fb():
    return _load("fork_branches_for_inventory", "fork-branches.py")


def account_branch(repo, root, target, mechanical, inv=None, ref="HEAD"):
    """account() for version branch `target` checked out (or exported) at `root`: its own src/<set>/java and
    src/<set>/resources are what it builds; a file is `overlaid` (a person changed it) unless it is one of `mechanical`
    (the rows alone made it: tools/fork-branches.py mechanical_files); drops.txt and BEHAVIOUR.json are read over the
    chain's .port/<v>/ copies, STUBS.json and REVIEW.md from .port/<target>/; the inventory is .port/INVENTORY.json."""
    root = pathlib.Path(root)
    rec = root / ".port"
    if inv is None:
        p = rec / INV
        if not p.is_file():
            raise ValueError(f"no .port/{INV}: run tools/layer-inventory.py build --repo {root} on the version branch")
        inv = json.loads(_read(p))
    fb = _fb()
    names = fb.chain(root, target)
    sets = _sets(root)
    pdirs = {s: root / "src" / s / "java" for s in sets if (root / "src" / s / "java").is_dir()}
    res = {s: [root / "src" / s / "resources"] for s in sets if (root / "src" / s / "resources").is_dir()}
    now = scan_tree(pdirs, {})
    overlaid = {(s, rel) for s, d in pdirs.items() for rel in _rels(d, "*.java")
                if f"src/{s}/java/{rel}" not in mechanical}
    dir_of = lambda t: rec / t
    drops = _drops(None, names, dir_of)
    stubs = _stub_files(rec / target, branch=True)
    review_p = rec / target / "REVIEW.md"
    review = review_ok_names(_read(review_p)) if review_p.is_file() else {}
    beh, notes = _behaviour(root / ".port-unused", names, dir_of)
    pr = fb.port_ref_of(repo, root, target, ref)
    now_src = subprocess.run(["git", "-C", str(repo), "rev-parse", f"{pr}:src"], capture_output=True, text=True,
                             encoding="utf-8").stdout.strip() if pr else ""
    src_moved = bool(inv.get("src_tree") and now_src and inv["src_tree"] != now_src)
    return _account(target, inv, pdirs, res, sets, now, overlaid, drops, stubs, review, beh, notes, src_moved)


def check_branch(repo, root, target, mechanical=None, files=None, ref="HEAD"):
    """check() for a version branch: (exit code, report or None, message lines)."""
    root = pathlib.Path(root)
    if mechanical is None:
        fb = _fb()
        files = files if files is not None else fb.root_files(repo, root, (root / ".git").exists())
        mechanical = fb.mechanical_files(repo, root, target, ref, files)
    try:
        rep = account_branch(repo, root, target, mechanical, ref=ref)
    except ValueError as e:
        return 2, None, [f"layer-inventory: {e}"]
    lines = [f"layer-inventory: {target}: " + ", ".join(f"{k} {v}" for k, v in sorted(rep["counts"].items()))]
    lines += [f"layer-inventory: note: {n}" for n in rep["notes"]]
    lines += [f"layer-inventory: UNACCOUNTED {r['id']} ({r['detail']})" for r in rep["items"] if r["status"] == "unaccounted"]
    if rep["unaccounted"]:
        lines.append(f"layer-inventory: {target} is NOT done: {len(rep['unaccounted'])} base behaviour item(s) unaccounted for; "
                     f"record each in .port/{target}/BEHAVIOUR.json, or review the file's STUBS.json entry")
        return EXIT_UNACCOUNTED, rep, lines
    return 0, rep, lines


def build_branch_inventory(repo):
    """.port/INVENTORY.json for a version branch checkout: built from the PORT branch's sources (the base build),
    which a version branch does not hold. -> (path, inventory)."""
    repo = pathlib.Path(repo)
    fb = _fb()
    t = fb.checkout_version(repo)
    pr = fb.port_ref_of(repo, repo, t, "HEAD")
    if not pr:
        raise ValueError(f"the port branch {fb.info(repo, t).get('port_branch')} is not in this clone")
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / "base"
        for path, data in fb.ref_files(repo, pr, "src").items():
            f = base / path
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
        inv = build_inventory(base)
    inv["base"] = fb.info(repo, t).get("base_minecraft_version", "")
    inv["src_tree"] = subprocess.run(["git", "-C", str(repo), "rev-parse", f"{pr}:src"], capture_output=True, text=True,
                                     encoding="utf-8").stdout.strip() or None
    return repo / ".port" / INV, inv


def write_check(repo, target, rep):
    """The target's account into .multiversion/targets/<t>/INVENTORY-CHECK.json (deterministic: rewritten only when it
    changed). A run output like GATES.md: what fork-port.py's done check and a reviewer read, not a reviewed input."""
    p = pathlib.Path(repo) / ".multiversion/targets" / target / CHECK_FILE
    text = json.dumps(rep, indent=1, ensure_ascii=False) + "\n"
    if not p.is_file() or p.read_text(encoding="utf-8") != text:
        p.write_text(text, encoding="utf-8")


def inventory_unaccounted(repo, target, write=True):
    """[] when every base behaviour item is accounted for on target (or the fork is single-version); else one line per
    reason it is not: each unaccounted item, a missing INVENTORY.json or prepared tree. Writes INVENTORY-CHECK.json
    when the check ran (write=False: nothing). What fork-port.py's target_done() calls."""
    code, rep, lines = check(repo, target)
    if rep is None:
        return [] if code == 0 else [ln.replace("layer-inventory: ", "behaviour inventory: ") for ln in lines]
    if write:
        write_check(repo, target, rep)
    return [f"behaviour inventory: unaccounted {r['id']} ({r['detail']})" for r in rep["items"] if r["status"] == "unaccounted"]


def cmd_check(a):
    if not is_layer(a.repo) and _fb().checkout_version(pathlib.Path(a.repo)) == a.target:
        code, rep, lines = check_branch(pathlib.Path(a.repo).resolve(), pathlib.Path(a.repo).resolve(), a.target)
        if rep is not None and not a.no_write:
            (pathlib.Path(a.repo) / ".port" / a.target / CHECK_FILE).write_text(check_text(rep), encoding="utf-8")
        print(json.dumps(rep, indent=1, ensure_ascii=False) if a.json and rep is not None else "\n".join(lines))
        return code
    code, rep, lines = check(a.repo, a.target, a.inventory)
    if rep is not None and not a.inventory and not a.no_write:
        write_check(a.repo, a.target, rep)
    if a.json and rep is not None:
        print(json.dumps(rep, indent=1, ensure_ascii=False))
    else:
        print("\n".join(lines))
    return code


# ---------------------------------------------------------------- self-check

def self_check():
    ok = True

    def expect(c, msg):
        nonlocal ok
        if not c:
            ok = False
            print("FAIL:", msg)

    def w(p, text):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    mixin = ('@Mixin(LevelRenderer.class)\npublic class LevelRendererMixin {\n'
             '  @Inject(method = "renderSky", at = @At("TAIL"))\n  private void sky(CallbackInfo ci) {}\n'
             '  @Inject(method = "renderClouds", at = @At("HEAD"), cancellable = true)\n  private void clouds(CallbackInfo ci) {}\n}\n')
    events = ('public class Events {\n  @SubscribeEvent\n  public static void hud(RenderGuiEvent.Post e) {}\n'
              '  static void init(IEventBus bus) { bus.addListener(Events::reg); NeoForge.EVENT_BUS.addListener(TickEvent.class, x -> {}); }\n'
              '  static void reg(RegisterEvent e) {}\n'
              '  static final DeferredRegister<Item> ITEMS = DeferredRegister.createItems("examplemod");\n'
              '  static final DeferredRegister<SoundEvent> S = DeferredRegister.create(Registries.SOUND_EVENT, "examplemod");\n}\n')
    render = ('public class GlowRenderer extends EntityRenderer<Glow> implements Foo {\n'
              '  void r() { RenderType t = RenderType.entityTranslucent(TEX); RenderSystem.setShader(GameRenderer::getPositionTexShader); }\n}\n')
    shader = json.dumps({"vertex": "examplemod:glow", "fragment": "examplemod:glow",
                         "samplers": [{"name": "Sampler0"}, {"name": "MaskSampler"}],
                         "uniforms": [{"name": "ModelViewMat"}, {"name": "GlowTime"}]})
    got = java_items(mixin)
    expect(("mixin", "@Mixin LevelRenderer") in got and sum(1 for k, _ in got if k == "injection") == 2,
           f"mixin items from fork-port's hook_targets: {got}")
    ev = java_items(events)
    expect({("event", "@SubscribeEvent RenderGuiEvent.Post"), ("event", "addListener RegisterEvent"),
            ("event", "addListener TickEvent"), ("registry", "registry ITEM"), ("registry", "registry SOUND_EVENT")} <= set(ev),
           f"event listeners by type (annotation, method reference, class literal) and registries: {ev}")
    rd = java_items(render)
    expect(("render-class", "class GlowRenderer : EntityRenderer") in rd and ("render-use", "RenderType.entityTranslucent") in rd
           and ("render-use", "RenderSystem.setShader") in rd and ("render-use", "GameRenderer.getPositionTexShader") in rd,
           f"render-path class and render API uses: {rd}")
    sh = shader_items("assets/examplemod/shaders/core/glow.json", shader)
    expect(("shader-sampler", "examplemod:core/glow MaskSampler") in sh and ("shader-uniform", "examplemod:core/glow GlowTime") in sh
           and ("shader", "examplemod:core/glow.json") in sh, f"core shader with its uniforms and samplers: {sh}")

    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        # single-version fork: no requirement, nothing written
        sv = d / "single"
        w(sv / "src/main/java/com/example/mod/client/GlowRenderer.java", render)
        before = sorted(p.relative_to(sv).as_posix() for p in sv.rglob("*"))
        expect(cmd_build(argparse.Namespace(repo=str(sv), dry_run=False)) == 2, "single-version: build refuses (exit 2)")
        code, rep, lines = check(sv, "1.21.4")
        expect(code == 0 and rep is None and "no inventory requirement" in lines[0], f"single-version: check passes, says so: {lines}")
        expect(sorted(p.relative_to(sv).as_posix() for p in sv.rglob("*")) == before, "single-version: nothing written")
        # layer fork
        lf = d / "layer"
        mvd = lf / ".multiversion"
        w(mvd / "base.properties", "minecraft_version=1.21.1\n")
        w(lf / "src/main/java/com/example/mod/mixin/LevelRendererMixin.java", mixin)
        w(lf / "src/main/java/com/example/mod/Events.java", events)
        w(lf / "src/main/java/com/example/mod/client/GlowRenderer.java", render)
        w(lf / "src/main/java/com/example/mod/compat/OptMixin.java", '@Mixin(OptThing.class)\nclass OptMixin {\n'
          '  @Inject(method = "x", at = @At("HEAD"))\n  void x(CallbackInfo ci) {}\n}\n')
        w(lf / "src/main/java/com/example/mod/Plain.java", "class Plain {}\n")
        w(lf / "src/main/resources/assets/examplemod/shaders/core/glow.json", shader)
        w(lf / "src/main/resources/assets/examplemod/shaders/core/glow.fsh", "uniform sampler2D Sampler0;\nuniform sampler2D MaskSampler;\n"
          "uniform float GlowTime;\nuniform mat4 ModelViewMat;\n")
        w(mvd / "targets/1.21.4/target.properties", "x=1\n")
        w(mvd / "targets/1.21.5/target.properties", "inherits=1.21.4\n")
        expect(cmd_build(argparse.Namespace(repo=str(lf), dry_run=False)) == 0, "layer: build writes the inventory")
        inv = json.loads((mvd / INV).read_text(encoding="utf-8"))
        text1 = (mvd / INV).read_text(encoding="utf-8")
        cmd_build(argparse.Namespace(repo=str(lf), dry_run=False))
        expect((mvd / INV).read_text(encoding="utf-8") == text1, "build is deterministic (byte-identical on a re-run)")
        expect(inv["counts"]["mixin"] == 2 and inv["counts"]["injection"] == 3 and inv["counts"]["shader-sampler"] == 2
               and inv["counts"]["registry"] == 2 and not any(i["file"].endswith("Plain.java") for i in inv["items"]),
               f"inventory counts: {inv['counts']}")
        expect(check(lf, "1.21.4")[0] == 2, "no prepared tree: exit 2 (build the target first)")
        # 1.21.4's prepared tree: LevelRendererMixin renamed by the tables only (LevelRenderer -> LevelRenderer2, not an
        # overlay); GlowRenderer overlaid, and the overlay lost the setShader use; OptMixin in drops.txt under a comment
        pt = lf / "build/mc1.21.4/mv/java/com/example/mod"
        w(pt / "mixin/LevelRendererMixin.java", mixin.replace("LevelRenderer.class", "LevelRenderer2.class")
          .replace('"renderSky"', '"renderSky2"'))
        w(pt / "Events.java", events)
        ovl = render.replace(" RenderSystem.setShader(GameRenderer::getPositionTexShader);", "")
        w(pt / "client/GlowRenderer.java", ovl)
        w(mvd / "targets/1.21.4/java/com/example/mod/client/GlowRenderer.java", ovl)
        w(mvd / "targets/1.21.4/drops.txt", "# the optional mod has no 1.21.4 build (DEPS.md)\ncom/example/mod/compat/OptMixin.java\n")
        code, rep, lines = check(lf, "1.21.4")
        st = {(r["file"].rsplit("/", 1)[-1], r["kind"], r["key"]): r["status"] for r in rep["items"]}
        expect(st.get(("LevelRendererMixin.java", "mixin", "@Mixin LevelRenderer")) == "same"
               and any(r.get("now") == "@Mixin LevelRenderer2" for r in rep["items"]),
               f"a table-renamed mixin target is the same, with its new key: {st}")
        expect(st.get(("OptMixin.java", "injection", '@Inject method=x @At(value="HEAD")')) == "dropped",
               f"a file in drops.txt under a comment is dropped with that reason: {st}")
        expect(st.get(("GlowRenderer.java", "render-use", "RenderSystem.setShader")) == "unaccounted"
               and st.get(("GlowRenderer.java", "render-class", "class GlowRenderer : EntityRenderer")) == "same",
               f"an overlay that lost a render use is unaccounted; its class is the same: {st}")
        expect(st.get(("glow.json", "shader-sampler", "examplemod:core/glow MaskSampler")) == "same",
               f"up to 1.21.4 the shader json is read: its samplers are the same: {st}")
        expect(code == EXIT_UNACCOUNTED and len(rep["unaccounted"]) == 2 and any("UNACCOUNTED" in l for l in lines),
               f"unaccounted items fail the check (exit 16) with a list: {code} {rep['unaccounted']}")
        # account: a reviewed STUBS.json entry for the overlay; GameRenderer.getPositionTexShader too (the same file)
        w(mvd / "targets/1.21.4/STUBS.json", json.dumps([{"file": ".multiversion/targets/1.21.4/java/com/example/mod/client/GlowRenderer.java",
                                                          "why": "empties 1 method body", "reviewed": "the shader is set by the RenderType now"}]))
        code, rep, _ = check(lf, "1.21.4")
        expect(code == 0 and not rep["unaccounted"], f"a reviewed stub of the file accounts for it: exit 0 ({rep['unaccounted']})")
        w(mvd / "targets/1.21.4/STUBS.json", json.dumps([{"file": ".multiversion/targets/1.21.4/java/com/example/mod/client/GlowRenderer.java",
                                                          "why": "x", "reviewed": None, "recheck": "re-justify: hook targets changed"}]))
        expect(check(lf, "1.21.4")[0] == EXIT_UNACCOUNTED, "an unreviewed (recheck) stub does not account")
        w(mvd / "targets/1.21.4/STUBS.json", json.dumps([{"file": ".multiversion/targets/1.21.4/java/com/example/mod/client/GlowRenderer.java",
                                                          "why": "x", "reviewed": "ok"}]))
        # 1.21.5 inherits 1.21.4; from 1.21.5 the json is not read: the shader, and each sampler/uniform that is not a
        # vanilla one, must be named by the prepared Java (its RenderPipeline). Only Sampler0, GlowTime and the path are.
        import shutil
        shutil.copytree(lf / "build/mc1.21.4", lf / "build/mc1.21.5")
        w(lf / "build/mc1.21.5/mv/java/com/example/mod/mv/Pipelines.java",
          'class Pipelines { Object p = b.withVertexShader("core/glow").withSampler("Sampler0").withUniform("GlowTime"); }\n')
        code, rep, _ = check(lf, "1.21.5")
        st = {(r["kind"], r["key"]): r["status"] for r in rep["items"] if r["file"].endswith("glow.json")}
        expect(st.get(("shader", "examplemod:core/glow.json")) == "same" and st.get(("shader-uniform", "examplemod:core/glow GlowTime")) == "same"
               and st.get(("shader-uniform", "examplemod:core/glow ModelViewMat")) == "same"
               and st.get(("shader-sampler", "examplemod:core/glow MaskSampler")) == "unaccounted",
               f"1.21.5+: a sampler no pipeline names is unaccounted; vanilla's and named ones are the same: {st}")
        expect(code == EXIT_UNACCOUNTED and len(rep["unaccounted"]) == 3 and any("setShader" in i for i in rep["unaccounted"]),
               f"a stub review is per target (1.21.5 has no STUBS.json of its own): {rep['unaccounted']}")
        mask = [r["id"] for r in rep["items"] if r["key"].endswith("MaskSampler")]
        w(mvd / "targets/1.21.4/BEHAVIOUR.json", json.dumps([{"item": i, "status": "dropped"} for i in mask]))
        # REVIEW.md accounts only through a parsed row with verdict ok (and a note) naming the file or the item id:
        # a mention in a note, review_skeleton's `?` row (it contains the path) and a not-ok row do not
        ovp = ".multiversion/targets/1.21.5/java/com/example/mod/client/GlowRenderer.java"
        head = "- reviewer: r\n- date: 2026-01-01\n- commit: abc1234\n\n| kind | file | verdict | note |\n|---|---|---|---|\n"
        for body, why in (("- com/example/mod/client/GlowRenderer.java: reviewed against the authors' file\n", "a mention in a note"),
                          (f"| overlay | {ovp} | ? | ? |\n", "a `?` row (review_skeleton's)"),
                          (f"| overlay | {ovp} | not-ok | loses the shader |\n", "a not-ok row"),
                          (f"| overlay | {ovp} | ok | ? |\n", "an ok row without a note"),
                          (f"| overlay | {ovp}.bak | ok | fine |\n", "an ok row for another file")):
            w(mvd / "targets/1.21.5/REVIEW.md", head + body)
            code, rep, _ = check(lf, "1.21.5")
            expect(code == EXIT_UNACCOUNTED and len(rep["unaccounted"]) == 3,
                   f"{why} in REVIEW.md does not account for the file's items: {rep['unaccounted']}")
        w(mvd / "targets/1.21.5/REVIEW.md", head + f"| overlay | {ovp} | ok | the RenderType sets the shader now |\n")
        code, rep, _ = check(lf, "1.21.5")
        expect(code == EXIT_UNACCOUNTED and rep["unaccounted"] == mask,
               f"an ok REVIEW.md row naming the file accounts for it; a drop with no reason does not: {rep['unaccounted']}")
        mid = [i for i in rep["items"] if i["key"] == "RenderSystem.setShader"]
        w(mvd / "targets/1.21.5/REVIEW.md", head + f"| stub | {mid[0]['id']} | ok | moved to the RenderType |\n" if mid else head)
        code, rep, _ = check(lf, "1.21.5")
        expect(mid and mid[0]["id"] not in rep["unaccounted"] and len(rep["unaccounted"]) == 2,
               f"an ok row naming an item id accounts for that item only: {rep['unaccounted']}")
        w(mvd / "targets/1.21.5/REVIEW.md", head + f"| overlay | {ovp} | ok | the RenderType sets the shader now |\n")
        w(mvd / "targets/1.21.4/BEHAVIOUR.json", json.dumps([{"item": i, "status": "dropped", "reason": "the mask pass is gone"}
                                                              for i in mask]))
        code, rep, _ = check(lf, "1.21.5")
        dropped = [r for r in rep["items"] if r["status"] == "dropped" and r["key"].endswith("MaskSampler")]
        expect(code == 0 and dropped and "inherited from 1.21.4" in dropped[0]["detail"],
               f"a parent's BEHAVIOUR.json drop is inherited, marked so: exit {code} {dropped}")
        expect(inventory_unaccounted(lf, "1.21.5") == [] and json.loads((mvd / "targets/1.21.5" / CHECK_FILE).read_text(
               encoding="utf-8"))["unaccounted"] == [], "inventory_unaccounted: [] when done, the account written")
        (mvd / "targets/1.21.5/REVIEW.md").unlink()
        un = inventory_unaccounted(lf, "1.21.5")
        expect(len(un) == 2 and all(u.startswith("behaviour inventory: unaccounted ") for u in un),
               f"inventory_unaccounted: one line per unaccounted item: {un}")
        (mvd / INV).rename(d / INV)
        expect(inventory_unaccounted(lf, "1.21.5") and "INVENTORY.json" in inventory_unaccounted(lf, "1.21.5")[0],
               "a layer fork without INVENTORY.json is not done (fail closed), naming the build command")
        expect(inventory_unaccounted(sv, "1.21.4") == [] and not (sv / ".multiversion").exists(),
               "single-version: inventory_unaccounted is [] and writes nothing")
        expect(src_tree(lf) is None, "not a git repo: no src tree recorded")
    # ---- a file-level ok row accounts for a file's non-hook items only: a hook (mixin target, injection, event
    # listener, shader uniform/sampler) needs its id named, in the row's note or as an `item` row's file
    with tempfile.TemporaryDirectory() as d:
        lf = pathlib.Path(d) / "layer"
        mvd = lf / ".multiversion"
        w(mvd / "base.properties", "minecraft_version=1.21.1\n")
        w(mvd / "targets/1.21.4/target.properties", "x=1\n")
        base = mixin.replace("private void sky(CallbackInfo ci) {}",
                             "private void sky(CallbackInfo ci) { RenderSystem.setShader(GameRenderer::getPositionTexShader); }")
        w(lf / "src/main/java/com/example/mod/mixin/LevelRendererMixin.java", base)
        cmd_build(argparse.Namespace(repo=str(lf), dry_run=False))
        ov = (base.replace(' RenderSystem.setShader(GameRenderer::getPositionTexShader);', "")
              .replace('  @Inject(method = "renderClouds", at = @At("HEAD"), cancellable = true)\n  private void clouds(CallbackInfo ci) {}\n', ""))
        w(mvd / "targets/1.21.4/java/com/example/mod/mixin/LevelRendererMixin.java", ov)
        w(lf / "build/mc1.21.4/mv/java/com/example/mod/mixin/LevelRendererMixin.java", ov)
        code, rep, _ = check(lf, "1.21.4")
        lost = {r["kind"]: r["id"] for r in rep["items"] if r["status"] == "unaccounted"}
        expect(code == EXIT_UNACCOUNTED and set(lost) >= {"injection", "render-use"},
               f"the overlay lost an injection and a render use: {rep['unaccounted']}")
        ovp = ".multiversion/targets/1.21.4/java/com/example/mod/mixin/LevelRendererMixin.java"
        head = "- reviewer: r\n- date: 2026-01-01\n- commit: abc1234\n\n| kind | file | verdict | note |\n|---|---|---|---|\n"
        w(mvd / "targets/1.21.4/REVIEW.md", head + f"| overlay | {ovp} | ok | the sky hook is the same |\n")
        code, rep, _ = check(lf, "1.21.4")
        expect(lost["injection"] in rep["unaccounted"] and not any(":render-use:" in i for i in rep["unaccounted"]),
               f"a file-level ok row accounts for the render use, not for the injection it never names: {rep['unaccounted']}")
        w(mvd / "targets/1.21.4/REVIEW.md", head + f"| overlay | {ovp} | ok | sky same; {lost['injection']}: the clouds "
                                                   "hook is vanilla's own option now |\n")
        code, rep, _ = check(lf, "1.21.4")
        expect(code == 0 and not rep["unaccounted"], f"an ok row whose note names the hook's id accounts for it: {rep['unaccounted']}")
        w(mvd / "targets/1.21.4/REVIEW.md", head + f"| overlay | {ovp} | ok | sky same |\n"
                                                   f"| item | {lost['injection']} | ok | the clouds hook is vanilla's own option now |\n")
        code, rep, _ = check(lf, "1.21.4")
        expect(code == 0, f"an ok `item` row for the hook's id accounts for it: {rep['unaccounted']}")
        w(mvd / "targets/1.21.4/REVIEW.md", head + f"| overlay | {ovp} | ok | sky same |\n"
                                                   f"| item | {lost['injection']} | ? | ? |\n")
        expect(check(lf, "1.21.4")[0] == EXIT_UNACCOUNTED, "a `?` item row (review_skeleton's) accounts for nothing")
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    if "--self-check" in sys.argv:
        return self_check()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    b = sub.add_parser("build", help="write .multiversion/INVENTORY.json from the base sources")
    b.add_argument("--repo", required=True)
    b.add_argument("--dry-run", action="store_true", help="count, write nothing")
    b.add_argument("--out", help="write the inventory here instead of .multiversion/INVENTORY.json")
    c = sub.add_parser("check", help="account for every inventory item on a target")
    c.add_argument("--repo", required=True)
    c.add_argument("--target", required=True)
    c.add_argument("--json", action="store_true")
    c.add_argument("--inventory", help="read the inventory from here instead of .multiversion/INVENTORY.json")
    c.add_argument("--no-write", action="store_true", help=f"do not write the target's {CHECK_FILE}")
    a = ap.parse_args()
    if a.cmd == "build":
        return cmd_build(a)
    if a.cmd == "check":
        return cmd_check(a)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
