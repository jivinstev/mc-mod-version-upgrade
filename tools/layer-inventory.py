#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The base build's behaviour inventory, and every version branch's account of it (the fork-versions skill, R2).

    python3 tools/layer-inventory.py build --repo DIR [--dry-run] [--out FILE]
    python3 tools/layer-inventory.py check --repo DIR --target T [--json] [--no-write]
    python3 tools/layer-inventory.py --self-check

WHY.  The gates cannot see rendering, and a wrong stub review ("handlers moved, same effect") was once inherited by
eight targets and found a day later. So the question "does this target still do what the base does?" is asked at the
START of a buildout, from the base, and answered per version -- not discovered at the end in a real client.

BUILD, on a version branch's checkout, writes .port/INVENTORY.json from the PORT branch's sources (src/<set>/java and
src/<set>/resources at the port commit the branch carries: the base build's code). Each item is (source set, file,
kind, key):
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

CHECK accounts for every item on version T, on its branch's checkout: against T's own src/<set>/java and
src/<set>/resources (what it builds). A file is one a person changed unless the rows alone made it from the port
branch's (tools/fork-branches.py mechanical_files). Per item:
  same        the key is there in the same file -- or, in a file the rows alone made, its renamed counterpart of the
              same kind (recorded as "now")
  changed     in a file a person changed, a counterpart of the same kind with a different key: accounted only when a
              review names the file (below)
  moved       not there, and a REVIEWED STUBS.json entry of T for that file (no open `recheck`), a REVIEW.md of T naming
              the file or the item id in a row with verdict `ok` and a note (a `?` or not-ok row, or a mention
              in a note, does not) -- for a hook (mixin, injection, overwrite, event, shader-uniform/-sampler) a row
              naming the file accounts only when its note names the item id; an `item` row naming the id always does --
              or a BEHAVIOUR.json entry {"item", "status": "moved", "where", "review"}
  dropped     the file is in the chain's drops.txt under a comment (the comment is the reason), or a BEHAVIOUR.json entry
              {"item", "status": "dropped", "reason"}
  unaccounted none of the above: the version is not done. The list is printed (and given in --json).
The account is written to .port/<t>/INVENTORY-CHECK.json (unless --no-write); tools/fork-branches.py's done check runs
the same check (check_branch). drops.txt and BEHAVIOUR.json are read over T's chain of .port/<v>/ copies, nearest first
(an item a parent dropped stays dropped, marked inherited).

A SINGLE-VERSION fork (no version branch checked out) has no inventory requirement HERE: `build` writes nothing (exit
2), `check` says so and exits 0 -- its review base's inventory is accounted for by tools/fork-branches.py check-done
(#161 U5), through check_branch(inv=inventory_of_commit(base)). A checkout with the removed .multiversion/ layer at HEAD is refused (tools/fork-kind.py).

Exit codes: 0 built / every item accounted for; 16 `check`: unaccounted items (listed); 2 bad input (not a version
branch's checkout, no .port/INVENTORY.json, a layer checkout).
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
    path ending in /<its file>: a changed row's src/<set>/java/<file>) does for a non-hook item, and
    for a hook (REVIEW_HOOK_KINDS) only when its note names the item's id. names: review_ok_names()."""
    if r["id"] in names:
        return True
    rows = [note for n, note in names.items() if n == r["file"] or n.endswith("/" + r["file"])]
    if r["kind"] in REVIEW_HOOK_KINDS:
        return any(r["id"] in note for note in rows)
    return bool(rows)


def mc_key(v):
    return [int(x) if x.isdigit() else x for x in re.split(r"[.]", v)]


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
    inv = {"generated_by": "tools/layer-inventory.py build", "base": "", "src_tree": src_tree(repo),
           "counts": counts, "items": items}
    return inv


# ---------------------------------------------------------------- per-target account

def _drops(names, dir_of):
    """{rel: reason or ""} over the chain's drops.txt (a comment block above a run of entries is their reason).
    dir_of(t): the directory holding t's drops.txt (.port/<t>)."""
    out = {}
    for t in reversed(names):
        p = dir_of(t) / "drops.txt"
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


def _stub_files(tdir):
    """{(set, rel): (reviewed?, review or open reason)} for T's STUBS.json entries (a version branch's, which name
    src/<set>/java/<rel>)."""
    out = {}
    try:
        entries = json.loads(_read(tdir / "STUBS.json")) if (tdir / "STUBS.json").is_file() else []
    except ValueError:
        entries = []
    for e in entries if isinstance(entries, list) else []:
        if not isinstance(e, dict) or not e.get("file"):
            continue
        m = re.match(r"^src/([\w-]+)/java/(.+)$", e["file"])
        if not m:
            continue
        ok = bool(e.get("reviewed")) and not e.get("recheck")
        out[(m.group(1) or "main", m.group(2))] = (ok, e.get("reviewed") if ok else (e.get("recheck") or "stub not reviewed"))
    return out


def _behaviour(root, names, dir_of):
    """{item id: (entry, from version)} over the chain's BEHAVIOUR.json, nearest first; [problems]. dir_of as _drops."""
    out, bad = {}, []
    for t in names:
        p = dir_of(t) / "BEHAVIOUR.json"
        if not p.is_file():
            continue
        try:
            data = json.loads(_read(p))
        except ValueError as e:
            bad.append(f"{p.relative_to(root).as_posix()}: not JSON ({e})")
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


def _account(target, inv, pdirs, res, sets, now, overlaid, drops, stubs, review, beh, notes, src_moved):
    """The account itself, from what account_branch() (a version branch) gathered:
    pdirs {set: java dir}, res {set: [resource dirs, nearest first]}, now scan_tree(pdirs), overlaid {(set, rel)} (files
    a person changed, not the rename tables alone), drops {rel: reason}, stubs _stub_files(), review review_ok_names(),
    beh _behaviour(), notes [..], src_moved: the base's sources changed since the inventory was built."""
    present = now
    java_text = None          # the prepared trees' Java, read once when a 1.21.5+ shader item needs it

    json_read = mc_key(target) < mc_key("1.21.5")

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

def cmd_build(a):
    repo = pathlib.Path(a.repo).resolve()
    why = _load("fork_kind_for_inventory", "fork-kind.py").refuse_layer(repo, "layer-inventory")
    if why:
        print(why); return 2
    if not _fb().checkout_version(repo):
        print("layer-inventory: not a version branch's checkout (a single-version fork has no inventory requirement)")
        return 2
    try:
        dest, inv = build_branch_inventory(repo)
    except ValueError as e:
        print(f"layer-inventory: {e}"); return 2
    dest = pathlib.Path(a.out) if getattr(a, "out", None) else dest
    if not a.dry_run:
        dest.write_text(json.dumps(inv, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"layer-inventory: {len(inv['items'])} item(s) from the port branch" + ("" if a.dry_run else f" -> {dest}"))
    return 0


CHECK_FILE = "INVENTORY-CHECK.json"


def check_text(rep):
    """The account as INVENTORY-CHECK.json holds it (deterministic)."""
    return json.dumps(rep, indent=1, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------- a version branch (tools/fork-branches.py)

_FB = None


def _fb():
    global _FB
    if _FB is None:                 # loaded once: a done check asks for it per version, and it is a large module
        _FB = _load("fork_branches_for_inventory", "fork-branches.py")
    return _FB


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
    drops = _drops(names, dir_of)
    stubs = _stub_files(rec / target)
    review_p = rec / target / "REVIEW.md"
    review = review_ok_names(_read(review_p)) if review_p.is_file() else {}
    beh, notes = _behaviour(root, names, dir_of)
    pr = fb.port_ref_of(repo, root, target, ref)
    now_src = subprocess.run(["git", "-C", str(repo), "rev-parse", f"{pr}:src"], capture_output=True, text=True,
                             encoding="utf-8").stdout.strip() if pr else ""
    src_moved = bool(inv.get("src_tree") and now_src and inv["src_tree"] != now_src)
    return _account(target, inv, pdirs, res, sets, now, overlaid, drops, stubs, review, beh, notes, src_moved)


def check_branch(repo, root, target, mechanical=None, files=None, ref="HEAD", inv=None):
    """check() for a version branch (or a single-version fork's tree with inv, its review base's inventory:
    inventory_of_commit): (exit code, report or None, message lines)."""
    root = pathlib.Path(root)
    if mechanical is None:
        fb = _fb()
        files = files if files is not None else fb.root_files(repo, root, (root / ".git").exists())
        mechanical = fb.mechanical_files(repo, root, target, ref, files)
    try:
        rep = account_branch(repo, root, target, mechanical, inv=inv, ref=ref)
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
    return repo / ".port" / INV, inventory_of_commit(repo, pr, fb.info(repo, t).get("base_minecraft_version", ""))


def inventory_of_commit(repo, c, base_label=""):
    """The inventory of commit c's sources (its src/ exported; no checkout touched): the port branch's for a version
    branch, a single-version fork's review base for its done check."""
    fb = _fb()
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / "base"
        for path, data in fb.ref_files(repo, c, "src").items():
            f = base / path
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
        inv = build_inventory(base)
    inv["base"] = base_label
    inv["src_tree"] = subprocess.run(["git", "-C", str(repo), "rev-parse", f"{c}:src"], capture_output=True, text=True,
                                     encoding="utf-8").stdout.strip() or None
    return inv


def cmd_check(a):
    repo = pathlib.Path(a.repo).resolve()
    why = _load("fork_kind_for_inventory", "fork-kind.py").refuse_layer(repo, "layer-inventory")
    if why:
        print(why); return 2
    if _fb().checkout_version(repo) != a.target:
        print("layer-inventory: single-version fork (no version branch checked out for this version): no inventory "
              "requirement here -- its review base's inventory is accounted for by tools/fork-branches.py check-done")
        return 0
    code, rep, lines = check_branch(repo, repo, a.target)
    if rep is not None and not a.no_write:
        (repo / ".port" / a.target / CHECK_FILE).write_text(check_text(rep), encoding="utf-8")
    print(json.dumps(rep, indent=1, ensure_ascii=False) if a.json and rep is not None else "\n".join(lines))
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

    def version(root, t, parent=None, inv=None):
        """A version branch's checkout at root: gradle.properties' version, .port/<t>/branch.properties (and the
        parent's carried copy), .port/INVENTORY.json."""
        w(root / "gradle.properties", f"minecraft_version={t}\n")
        for v, par in ((t, parent), (parent, None)):
            if v:
                w(root / ".port" / v / "branch.properties", f"minecraft_version={v}\nport_branch=neoforge-1.21.1\n"
                  + (f"parent=neoforge-{par}\nparent_minecraft_version={par}\n" if par else "parent=neoforge-1.21.1\n"))
        if inv is not None:
            w(root / ".port" / INV, json.dumps(inv, indent=1) + "\n")
        return root

    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        # single-version fork: no requirement, nothing written
        sv = d / "single"
        w(sv / "src/main/java/com/example/mod/client/GlowRenderer.java", render)
        before = sorted(p.relative_to(sv).as_posix() for p in sv.rglob("*"))
        expect(cmd_build(argparse.Namespace(repo=str(sv), dry_run=False)) == 2, "single-version: build refuses (exit 2)")
        expect(cmd_check(argparse.Namespace(repo=str(sv), target="1.21.4", json=False, no_write=False)) == 0,
               "single-version: check passes, says so")
        expect(sorted(p.relative_to(sv).as_posix() for p in sv.rglob("*")) == before, "single-version: nothing written")
        # the base build (the port branch's sources) -> the inventory
        base = d / "base"
        w(base / "src/main/java/com/example/mod/mixin/LevelRendererMixin.java", mixin)
        w(base / "src/main/java/com/example/mod/Events.java", events)
        w(base / "src/main/java/com/example/mod/client/GlowRenderer.java", render)
        w(base / "src/main/java/com/example/mod/compat/OptMixin.java", '@Mixin(OptThing.class)\nclass OptMixin {\n'
          '  @Inject(method = "x", at = @At("HEAD"))\n  void x(CallbackInfo ci) {}\n}\n')
        w(base / "src/main/java/com/example/mod/Plain.java", "class Plain {}\n")
        fsh = "uniform sampler2D Sampler0;\nuniform sampler2D MaskSampler;\nuniform float GlowTime;\nuniform mat4 ModelViewMat;\n"
        w(base / "src/main/resources/assets/examplemod/shaders/core/glow.json", shader)
        w(base / "src/main/resources/assets/examplemod/shaders/core/glow.fsh", fsh)
        inv = build_inventory(base)
        expect(json.dumps(build_inventory(base)) == json.dumps(inv), "build is deterministic (the same inventory on a re-run)")
        expect(inv["counts"]["mixin"] == 2 and inv["counts"]["injection"] == 3 and inv["counts"]["shader-sampler"] == 2
               and inv["counts"]["registry"] == 2 and not any(i["file"].endswith("Plain.java") for i in inv["items"]),
               f"inventory counts: {inv['counts']}")
        # version 1.21.4: LevelRendererMixin renamed by the rows only (LevelRenderer -> LevelRenderer2: a mechanical file);
        # GlowRenderer changed by hand and lost the setShader use; OptMixin dropped (drops.txt, under a comment)
        v4 = version(d / "v4", "1.21.4", inv=inv)
        pt = v4 / "src/main/java/com/example/mod"
        w(pt / "mixin/LevelRendererMixin.java", mixin.replace("LevelRenderer.class", "LevelRenderer2.class")
          .replace('"renderSky"', '"renderSky2"'))
        w(pt / "Events.java", events)
        ovl = render.replace(" RenderSystem.setShader(GameRenderer::getPositionTexShader);", "")
        w(pt / "client/GlowRenderer.java", ovl)
        w(v4 / "src/main/resources/assets/examplemod/shaders/core/glow.json", shader)
        w(v4 / "src/main/resources/assets/examplemod/shaders/core/glow.fsh", fsh)
        w(v4 / ".port/1.21.4/drops.txt", "# the optional mod has no 1.21.4 build (DEPS.md)\ncom/example/mod/compat/OptMixin.java\n")
        mech = {"src/main/java/com/example/mod/mixin/LevelRendererMixin.java", "src/main/java/com/example/mod/Events.java"}
        chk = lambda root, t: check_branch(root, root, t, mech)
        code, rep, lines = chk(v4, "1.21.4")
        st = {(r["file"].rsplit("/", 1)[-1], r["kind"], r["key"]): r["status"] for r in rep["items"]}
        expect(st.get(("LevelRendererMixin.java", "mixin", "@Mixin LevelRenderer")) == "same"
               and any(r.get("now") == "@Mixin LevelRenderer2" for r in rep["items"]),
               f"a mixin target the rows renamed is the same, with its new key: {st}")
        expect(st.get(("OptMixin.java", "injection", '@Inject method=x @At(value="HEAD")')) == "dropped",
               f"a file in drops.txt under a comment is dropped with that reason: {st}")
        expect(st.get(("GlowRenderer.java", "render-use", "RenderSystem.setShader")) == "unaccounted"
               and st.get(("GlowRenderer.java", "render-class", "class GlowRenderer : EntityRenderer")) == "same",
               f"a hand-changed file that lost a render use is unaccounted; its class is the same: {st}")
        expect(st.get(("glow.json", "shader-sampler", "examplemod:core/glow MaskSampler")) == "same",
               f"up to 1.21.4 the shader json is read: its samplers are the same: {st}")
        expect(code == EXIT_UNACCOUNTED and len(rep["unaccounted"]) == 2 and any("UNACCOUNTED" in l for l in lines),
               f"unaccounted items fail the check (exit 16) with a list: {code} {rep['unaccounted']}")
        # account: a reviewed STUBS.json entry for the file; GameRenderer.getPositionTexShader too (the same file)
        gp = "src/main/java/com/example/mod/client/GlowRenderer.java"
        w(v4 / ".port/1.21.4/STUBS.json", json.dumps([{"file": gp, "why": "empties 1 method body",
                                                       "reviewed": "the shader is set by the RenderType now"}]))
        code, rep, _ = chk(v4, "1.21.4")
        expect(code == 0 and not rep["unaccounted"], f"a reviewed stub of the file accounts for it: exit 0 ({rep['unaccounted']})")
        w(v4 / ".port/1.21.4/STUBS.json", json.dumps([{"file": gp, "why": "x", "reviewed": None,
                                                       "recheck": "re-justify: hook targets changed"}]))
        expect(chk(v4, "1.21.4")[0] == EXIT_UNACCOUNTED, "an unreviewed (recheck) stub does not account")
        w(v4 / ".port/1.21.4/STUBS.json", json.dumps([{"file": gp, "why": "x", "reviewed": "ok"}]))
        # 1.21.5 stands on 1.21.4; from 1.21.5 the json is not read: the shader, and each sampler/uniform that is not a
        # vanilla one, must be named by the Java (its RenderPipeline). Only Sampler0, GlowTime and the path are.
        import shutil
        shutil.copytree(v4, d / "v5")
        v5 = version(d / "v5", "1.21.5", parent="1.21.4")
        w(v5 / "src/main/java/com/example/mod/mv/Pipelines.java",
          'class Pipelines { Object p = b.withVertexShader("core/glow").withSampler("Sampler0").withUniform("GlowTime"); }\n')
        code, rep, _ = chk(v5, "1.21.5")
        st = {(r["kind"], r["key"]): r["status"] for r in rep["items"] if r["file"].endswith("glow.json")}
        expect(st.get(("shader", "examplemod:core/glow.json")) == "same" and st.get(("shader-uniform", "examplemod:core/glow GlowTime")) == "same"
               and st.get(("shader-uniform", "examplemod:core/glow ModelViewMat")) == "same"
               and st.get(("shader-sampler", "examplemod:core/glow MaskSampler")) == "unaccounted",
               f"1.21.5+: a sampler no pipeline names is unaccounted; vanilla's and named ones are the same: {st}")
        expect(code == EXIT_UNACCOUNTED and len(rep["unaccounted"]) == 3 and any("setShader" in i for i in rep["unaccounted"]),
               f"a stub review is per version (1.21.5 has no STUBS.json of its own): {rep['unaccounted']}")
        mask = [r["id"] for r in rep["items"] if r["key"].endswith("MaskSampler")]
        w(v5 / ".port/1.21.4/BEHAVIOUR.json", json.dumps([{"item": i, "status": "dropped"} for i in mask]))
        # REVIEW.md accounts only through a parsed row with verdict ok (and a note) naming the file or the item id:
        # a mention in a note, review_skeleton's `?` row (it contains the path) and a not-ok row do not
        head = "- reviewer: r\n- date: 2026-01-01\n- commit: abc1234\n\n| kind | file | verdict | note |\n|---|---|---|---|\n"
        for body, why in (("- com/example/mod/client/GlowRenderer.java: reviewed against the authors' file\n", "a mention in a note"),
                          (f"| changed | {gp} | ? | ? |\n", "a `?` row (review_skeleton's)"),
                          (f"| changed | {gp} | not-ok | loses the shader |\n", "a not-ok row"),
                          (f"| changed | {gp} | ok | ? |\n", "an ok row without a note"),
                          (f"| changed | {gp}.bak | ok | fine |\n", "an ok row for another file")):
            w(v5 / ".port/1.21.5/REVIEW.md", head + body)
            code, rep, _ = chk(v5, "1.21.5")
            expect(code == EXIT_UNACCOUNTED and len(rep["unaccounted"]) == 3,
                   f"{why} in REVIEW.md does not account for the file's items: {rep['unaccounted']}")
        w(v5 / ".port/1.21.5/REVIEW.md", head + f"| changed | {gp} | ok | the RenderType sets the shader now |\n")
        code, rep, _ = chk(v5, "1.21.5")
        expect(code == EXIT_UNACCOUNTED and rep["unaccounted"] == mask,
               f"an ok REVIEW.md row naming the file accounts for it; a drop with no reason does not: {rep['unaccounted']}")
        mid = [i for i in rep["items"] if i["key"] == "RenderSystem.setShader"]
        w(v5 / ".port/1.21.5/REVIEW.md", head + f"| stub | {mid[0]['id']} | ok | moved to the RenderType |\n" if mid else head)
        code, rep, _ = chk(v5, "1.21.5")
        expect(mid and mid[0]["id"] not in rep["unaccounted"] and len(rep["unaccounted"]) == 2,
               f"an ok row naming an item id accounts for that item only: {rep['unaccounted']}")
        w(v5 / ".port/1.21.5/REVIEW.md", head + f"| changed | {gp} | ok | the RenderType sets the shader now |\n")
        w(v5 / ".port/1.21.4/BEHAVIOUR.json", json.dumps([{"item": i, "status": "dropped", "reason": "the mask pass is gone"}
                                                          for i in mask]))
        code, rep, _ = chk(v5, "1.21.5")
        dropped = [r for r in rep["items"] if r["status"] == "dropped" and r["key"].endswith("MaskSampler")]
        expect(code == 0 and dropped and "inherited from 1.21.4" in dropped[0]["detail"],
               f"a parent's BEHAVIOUR.json drop is inherited, marked so: exit {code} {dropped}")
        (v5 / ".port" / INV).unlink()
        code, rep, lines = chk(v5, "1.21.5")
        expect(code == 2 and rep is None and "INVENTORY.json" in lines[0],
               f"a version branch without .port/INVENTORY.json is not judged (fail closed), naming the build command: {lines}")
        expect(src_tree(base) is None, "not a git repo: no src tree recorded")
    # ---- a file-level ok row accounts for a file's non-hook items only: a hook (mixin target, injection, event
    # listener, shader uniform/sampler) needs its id named, in the row's note or as an `item` row's file
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        base_ = d / "base"
        src = mixin.replace("private void sky(CallbackInfo ci) {}",
                            "private void sky(CallbackInfo ci) { RenderSystem.setShader(GameRenderer::getPositionTexShader); }")
        w(base_ / "src/main/java/com/example/mod/mixin/LevelRendererMixin.java", src)
        v4 = version(d / "v4", "1.21.4", inv=build_inventory(base_))
        ov = (src.replace(' RenderSystem.setShader(GameRenderer::getPositionTexShader);', "")
              .replace('  @Inject(method = "renderClouds", at = @At("HEAD"), cancellable = true)\n  private void clouds(CallbackInfo ci) {}\n', ""))
        w(v4 / "src/main/java/com/example/mod/mixin/LevelRendererMixin.java", ov)
        chk = lambda: check_branch(v4, v4, "1.21.4", set())
        code, rep, _ = chk()
        lost = {r["kind"]: r["id"] for r in rep["items"] if r["status"] == "unaccounted"}
        expect(code == EXIT_UNACCOUNTED and set(lost) >= {"injection", "render-use"},
               f"the hand-changed file lost an injection and a render use: {rep['unaccounted']}")
        ovp = "src/main/java/com/example/mod/mixin/LevelRendererMixin.java"
        head = "- reviewer: r\n- date: 2026-01-01\n- commit: abc1234\n\n| kind | file | verdict | note |\n|---|---|---|---|\n"
        rv = v4 / ".port/1.21.4/REVIEW.md"
        w(rv, head + f"| changed | {ovp} | ok | the sky hook is the same |\n")
        code, rep, _ = chk()
        expect(lost["injection"] in rep["unaccounted"] and not any(":render-use:" in i for i in rep["unaccounted"]),
               f"a file-level ok row accounts for the render use, not for the injection it never names: {rep['unaccounted']}")
        w(rv, head + f"| changed | {ovp} | ok | sky same; {lost['injection']}: the clouds hook is vanilla's own option now |\n")
        code, rep, _ = chk()
        expect(code == 0 and not rep["unaccounted"], f"an ok row whose note names the hook's id accounts for it: {rep['unaccounted']}")
        w(rv, head + f"| changed | {ovp} | ok | sky same |\n| item | {lost['injection']} | ok | the clouds hook is vanilla's own option now |\n")
        code, rep, _ = chk()
        expect(code == 0, f"an ok `item` row for the hook's id accounts for it: {rep['unaccounted']}")
        w(rv, head + f"| changed | {ovp} | ok | sky same |\n| item | {lost['injection']} | ? | ? |\n")
        expect(chk()[0] == EXIT_UNACCOUNTED, "a `?` item row (review_skeleton's) accounts for nothing")
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    if "--self-check" in sys.argv:
        return self_check()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    b = sub.add_parser("build", help="write .port/INVENTORY.json from the port branch's sources (on a version branch)")
    b.add_argument("--repo", required=True)
    b.add_argument("--dry-run", action="store_true", help="count, write nothing")
    b.add_argument("--out", help="write the inventory here instead of .port/INVENTORY.json")
    c = sub.add_parser("check", help="account for every inventory item on the checked-out version branch")
    c.add_argument("--repo", required=True)
    c.add_argument("--target", required=True)
    c.add_argument("--json", action="store_true")
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
