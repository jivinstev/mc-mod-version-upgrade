#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""What a person must check by hand on each Minecraft version after a fork-versions buildout (issue #117, part 1).

    python3 tools/layer-test-plan.py --repo DIR [--repo DIR ...] [--json]
    python3 tools/layer-test-plan.py --self-check

WHY.  Gate C boots a real client and runs launch, spawn, battle and gauntlet, but it never looks at what a port changed
on screen: a re-implemented render path, a re-targeted mixin, a removed optional integration. Nothing told the
maintainer WHAT to test on WHICH version. This collects, with no model and no network, the per-target places where a
layer target differs from the authors' code in a way the gates do not judge. The fork-versions skill turns the items
into a test-plan artifact: the tool supplies the sources, the agent writes the prose (what changed, how to see it in
game, why the gates did not cover it).

WHAT IT COLLECTS, per layer target (.multiversion/targets/<t>/):
  stub        every STUBS.json entry (a removed or moved mixin handler, an emptied method; fork-port.py's stub
              signals): its file, its signal ("why"), its review, and the "hooks" / "recheck" / "inherited_review"
              fields when recorded. A reviewed one says the change is right; a person still has to see it work.
  overlay     an overlay that REPLACES a whole authors' file (targets/<t>/java/<path> or targets/<t>/<set>/java/<path>
              with the same <path> under src/<set>/java). Effective on every target that inherits it until a nearer
              target overlays the same path. An overlay-only file (a helper the authors do not have) is not listed.
              Flagged "mixin" (it holds an @Mixin) and "client" (under a client/ package, or it imports
              net.minecraft.client / com.mojang.blaze3d): the ones that change what is drawn or how input is read.
  catalogue   a rename row (the target's own renames.tsv or a shared library it applies, shared/<v>/renames.tsv) whose
              comment says it has a known limit or is not covered by the gates -- in the comment itself
              (`known limit`, `not covered by gates`, `no gate sees ...`) or in a CATALOG.md entry the comment cites
              (`CATALOG V81`, `§X14`) -- and that FIRED on the target. Fired rows are not committed per target (the
              build keeps only the non-main source sets' hits, in build/mc<t>/mv/hits-*.txt), so a row counts as fired
              when its pattern matches an authors' file the target compiles (src/<set>/java, minus the files the
              target overlays) or when a recorded hits file names it. With `inherited_overlay_rows=true` in
              .multiversion/base.properties also when it matches an overlay the target inherits from an older target
              whose rows come before it (layer.py runs that overlay through the later rows).
  dropped-dep a DEPS.md row (tools/layer-deps.py) whose dependency was dropped for the target: an optional
              integration that is off on that version.
  preview     a target marked `preview=true` in its own target.properties (built and gated, never published: #76).
  behaviour   when the fork has .multiversion/INVENTORY.json (tools/layer-inventory.py build: what the base build hooks
              and renders), every inventory item that is not "same" on the target -- changed in an overlay, moved,
              dropped, or unaccounted -- with its detail (a target whose prepared tree is missing says so under
              "inventory" in its targets entry). Without the file nothing is added: the output is as before.

GROUPING. A change shared by a range of targets is ONE item, with the targets it applies to, their range(s) in version
order and the version where it first appears: an inherited overlay, an inherited stub review (the "(inherited from X)"
prefix is ignored when comparing), a shared row that fires on several targets, the same dependency dropped on several.

A SINGLE-VERSION fork (no .multiversion/base.properties) is reported as its one version with no layer section and no
items. Open issues on the fork labelled for the layer are not read here (that needs the network): the skill lists them.

Standard library only; reads files, runs nothing.
"""
import argparse, importlib.util, json, pathlib, re, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
CATALOG = ROOT / "CATALOG.md"
PREPARE = ROOT / "templates/fork-layer/tools/prepare-sources.py"

# a note that says the gates do not judge this change
KNOWN = re.compile(r"(?i)\bknown[- ]limit|\bnot covered by (?:any |the )?(?:automated )?gates?\b|"
                   r"\bno (?:automated |other )?gate\b|\bnot caught by (?:any |the )?gates?\b|"
                   r"\bgates? (?:[A-C] )?(?:can ?not|can't|does not|do not|did not|never) (?:see|catch|cover|reach|open|render)")
CITE = re.compile(r"(?:CATALOG\s+(?:§\s*)?|§)([A-Z]{1,2}\d+[a-z]?)\b")
ENTRY = re.compile(r"^(?:- )?\*\*([A-Z]{1,2}\d+[a-z]?)\.\s|^([A-Z]{1,2}\d+[a-z]?)\.\s+\*\*")
CLIENT = re.compile(r"^import (?:net\.minecraft\.client|com\.mojang\.blaze3d)\.", re.M)   # draws or reads input
KINDS = ("preview", "overlay", "stub", "catalogue", "dropped-dep", "behaviour")


def mc_key(v):
    return tuple(int(x) if x.isdigit() else 0 for x in re.findall(r"\d+|[A-Za-z]+", v))


def read_props(path):
    out = {}
    p = pathlib.Path(path)
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def chain(mv, target):
    """target, its `inherits`, theirs ... (nearest first), as layer.py's chain()."""
    seen, out, t = set(), [], target
    while t and t not in seen and (mv / "targets" / t / "target.properties").is_file():
        seen.add(t)
        out.append(t)
        t = read_props(mv / "targets" / t / "target.properties").get("inherits")
    return out


def shared_chain(mv, target):
    """The versions whose shared library applies to target, newest first (layer.py's shared_chain())."""
    names = chain(mv, target)
    extra = []
    for x in names:
        extra += [s.strip() for s in read_props(mv / "targets" / x / "target.properties").get("shared_chain", "").split(",")
                  if s.strip()]
    return sorted(set(names) | set(extra), key=mc_key, reverse=True) if extra else names


def inherited_overlay_rows(mv):
    """.multiversion/base.properties `inherited_overlay_rows=true` (opt-in; layer.py's inherited_overlay_rows())."""
    p = pathlib.Path(mv) / "base.properties"
    return p.is_file() and read_props(p).get("inherited_overlay_rows", "").lower() == "true"


def runs(targets, order):
    """['1.21.3', '1.21.4', '1.21.8'] over the fork's ordered versions -> '1.21.3-1.21.4, 1.21.8' (adjacent ones joined)."""
    idx = {v: i for i, v in enumerate(order)}
    ts = sorted(set(targets), key=lambda v: idx.get(v, 1 << 30))
    out, start, prev = [], None, None
    for t in ts:
        if start is not None and idx.get(t) == idx.get(prev, -9) + 1:
            prev = t
            continue
        if start is not None:
            out.append(start if start == prev else f"{start}-{prev}")
        start = prev = t
    if start is not None:
        out.append(start if start == prev else f"{start}-{prev}")
    return ", ".join(out)


# ---------------------------------------------------------------- CATALOG.md

def catalogue_notes(path=CATALOG):
    """{entry id: the sentence(s) of that entry that say the gates do not cover it} for every CATALOG.md entry with
    such a note."""
    out, cur, buf = {}, None, []

    def flush():
        if cur and buf:
            text = " ".join(buf)
            hits = []
            for m in KNOWN.finditer(text):
                a = max(text.rfind(". ", 0, m.start()), text.rfind(" · ", 0, m.start()))
                b = text.find(". ", m.end())
                hits.append(text[a + 2 if a >= 0 else 0: b + 1 if b >= 0 else len(text)].strip()[:400])
            if hits:
                out[cur] = " ... ".join(dict.fromkeys(hits))
    p = pathlib.Path(path)
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        m = ENTRY.match(line)
        if m or line.startswith("#"):
            flush()
            cur, buf = (m.group(1) or m.group(2)) if m else None, []
        if cur:
            buf.append(line.strip())
    flush()
    return out


# ---------------------------------------------------------------- rename rows

def table_rows(path):
    """[(line, from, to, note)] of a renames.tsv: note is the comment block right above the row (or above the run of
    rows it heads; a blank line ends a run)."""
    rows, pending, note = [], [], ""
    for n, raw in enumerate(pathlib.Path(path).read_text(encoding="utf-8").splitlines(), 1):
        s = raw.strip()
        if not s:
            note = ""
            continue
        if s.startswith("#"):
            if not s.startswith("#!"):
                pending.append(s.lstrip("#").strip())
            continue
        if pending:
            note, pending = " ".join(pending), []
        if "\t" in raw:
            frm, to = raw.split("\t", 1)
            rows.append((n, frm.strip(), to.strip(), note))
    return rows


def _inventory():
    spec = importlib.util.spec_from_file_location("ltp_layer_inventory", HERE / "layer-inventory.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _prepare():
    spec = importlib.util.spec_from_file_location("ltp_prepare_sources", PREPARE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def row_fires(frm, to, texts, ps):
    """(fired, the first file it matched in) for one row over {rel: text}: the same matching prepare-sources.py does
    (a type token not after a dot, member: only after a dot, re: a regex)."""
    toks, rxs = ps.split_rules([(frm, to, False)])
    if rxs:
        rx = rxs[0][0]
    else:
        rx = re.compile(r"(?<![\w.])(" + re.escape(toks[0][0]) + r")(?![\w])")
    for rel, text in texts.items():
        if rx.search(text):
            return True, rel
    return False, None


def recorded_hits(repo, target):
    out = set()
    for f in (pathlib.Path(repo) / "build" / f"mc{target}" / "mv").glob("hits-*.txt"):
        try:
            out |= {ln for ln in f.read_text(encoding="utf-8").splitlines() if ln}
        except OSError:
            pass
    return out


# ---------------------------------------------------------------- per fork

def source_sets(repo):
    """{set name: src/<set>/java} for every source set the authors have."""
    src = pathlib.Path(repo) / "src"
    return {d.name: d / "java" for d in sorted(src.iterdir()) if (d / "java").is_dir()} if src.is_dir() else {}


def overlay_dir(mv, t, sset):
    return mv / "targets" / t / ("java" if sset == "main" else f"{sset}/java")


def java_rels(root):
    return sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*.java")) if root.is_dir() else []


def deps_dropped(mdfile):
    """[(dependency, role, reason)] of a DEPS.md's dropped rows."""
    out = []
    if not mdfile.is_file():
        return out
    for line in mdfile.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 4 and cells[3].startswith("dropped:"):
            out.append((cells[0].strip("`"), cells[1], cells[3][len("dropped:"):].strip()))
    return out


def plan_fork(repo, catalog=CATALOG):
    repo = pathlib.Path(repo).resolve()
    mv = repo / ".multiversion"
    if not (mv / "base.properties").is_file():
        mc = read_props(repo / "gradle.properties").get("minecraft_version", "")
        return {"repo": str(repo), "name": repo.name, "kind": "single-version", "versions": [mc] if mc else []}
    base = read_props(mv / "base.properties").get("minecraft_version", "")
    targets = sorted((d.name for d in (mv / "targets").iterdir() if (d / "target.properties").is_file()),
                     key=mc_key) if (mv / "targets").is_dir() else []
    order = ([base] if base else []) + targets
    sets = source_sets(repo)
    notes = catalogue_notes(catalog)
    ps = _prepare()
    groups = {}            # key -> item

    def add(kind, key, t, item, source):
        g = groups.setdefault((kind,) + key, dict(kind=kind, targets=[], sources=[], **item))
        if t not in g["targets"]:
            g["targets"].append(t)
        if source not in g["sources"]:
            g["sources"].append(source)
        return g

    info = []
    inv_path = mv / "INVENTORY.json"
    inventory = _inventory() if inv_path.is_file() else None
    inv = json.loads(inv_path.read_text(encoding="utf-8")) if inventory else None
    for t in targets:
        props = read_props(mv / "targets" / t / "target.properties")
        preview = props.get("preview", "").lower() == "true"
        info.append({"target": t, "inherits": props.get("inherits"), "preview": preview})
        if inventory:
            # the base build's behaviour inventory (tools/layer-inventory.py): every item that is not the same on this
            # target -- changed in an overlay, moved, dropped, or not accounted for yet -- is a manual-test candidate
            try:
                acc = inventory.account(repo, t, inv)
            except ValueError as e:
                info[-1]["inventory"] = f"not checked: {e}"
                acc = None
            if acc:
                info[-1]["inventory"] = acc["counts"]
                for r in acc["items"]:
                    if r["status"] == "same":
                        continue
                    detail = re.sub(r" \(inherited from [^)]+\)", "", r["detail"])
                    g = add("behaviour", (r["id"], r["status"], detail), t,
                            {"title": f"{r['kind']} {r['key'][:160]} ({r['file'].rsplit('/', 1)[-1]}): {r['status']}",
                             "item": r["id"], "file": r["file"], "set": r["set"], "inventory_kind": r["kind"],
                             "key": r["key"], "status": r["status"], "detail": detail, "now": {}},
                            f".multiversion/INVENTORY.json: {r['id']}")
                    if r.get("now"):
                        g["now"][t] = r["now"]
        if preview:
            add("preview", (t,), t, {"title": f"Minecraft {t} is a preview target: built and gated, not published"},
                f".multiversion/targets/{t}/target.properties")
        names = chain(mv, t)
        # overlays that replace an authors' file: the nearest target in the chain that has the path is the origin
        overlaid, origins = {}, {}
        for sset, sdir in sets.items():
            seen = {}
            for c in reversed(names):            # farthest first, nearer overwrites
                for rel in java_rels(overlay_dir(mv, c, sset)):
                    seen[rel] = c
            overlaid[sset] = set(seen)
            origins[sset] = seen
            for rel, origin in sorted(seen.items()):
                if (sdir / rel).is_file():
                    ov = overlay_dir(mv, origin, sset).relative_to(repo).as_posix() + "/" + rel
                    if ("overlay", sset, rel, origin) not in groups:
                        body = (repo / ov).read_text(encoding="utf-8", errors="replace")
                        flags = {"mixin": bool(re.search(r"@Mixin\s*\(", body)),
                                 "client": "/client/" in "/" + rel or bool(CLIENT.search(body))}
                    add("overlay", (sset, rel, origin), t,
                        {"title": f"{rel} is replaced as a whole file", "authors_file": f"src/{sset}/java/{rel}",
                         "overlay": ov, "since": origin, **flags}, ov)
        # STUBS.json
        sj = mv / "targets" / t / "STUBS.json"
        try:
            entries = json.loads(sj.read_text(encoding="utf-8")) if sj.is_file() else []
        except ValueError:
            entries = []
        for e in entries if isinstance(entries, list) else []:
            if not isinstance(e, dict) or not e.get("file"):
                continue
            norm = re.sub(r"/targets/[^/]+/", "/targets/*/", e["file"])
            rev = e.get("reviewed") or ""
            m = re.match(r"\(inherited from ([^)]+)\)\s*", rev)
            bare = rev[m.end():] if m else rev
            status = "reviewed" if rev else ("recheck" if e.get("recheck") else "open")
            g = add("stub", (norm, e.get("why", ""), bare, status, json.dumps(e.get("hooks"))), t,
                    {"title": f"{norm.rsplit('/', 1)[-1]}: {str(e.get('why', ''))[:120]}", "file": norm,
                     "why": e.get("why", ""), "review": bare, "status": status, "files": {}}, sj.relative_to(repo).as_posix())
            g["files"][t] = e["file"]
            for k in ("hooks", "recheck", "inherited_review"):
                if k in e and k not in g:
                    g[k] = e[k]
        # catalogue rows that fired
        texts = {}
        for sset, sdir in sets.items():
            for rel in java_rels(sdir):
                if rel not in overlaid.get(sset, ()):
                    texts[f"src/{sset}/java/{rel}"] = (sdir / rel).read_text(encoding="utf-8", errors="replace")
        hits = recorded_hits(repo, t)
        snames = shared_chain(mv, t)
        tables = [(mv / "targets" / c / "renames.tsv", "target", c) for c in names] + [
            (mv / "shared" / s / "renames.tsv", "shared", s) for s in snames]
        # base.properties inherited_overlay_rows=true: layer.py runs an overlay t inherits from an older target through
        # the rows of the targets after it (and the shared libraries t has and it does not), so those rows fire there too
        inherited = {}
        if inherited_overlay_rows(mv):
            for sset, seen in origins.items():
                for rel, origin in seen.items():
                    if origin != t:
                        inherited.setdefault(origin, {})[f"{overlay_dir(mv, origin, sset).relative_to(repo).as_posix()}/{rel}"] = \
                            (overlay_dir(mv, origin, sset) / rel).read_text(encoding="utf-8", errors="replace")
        for tab, tkind, ver in tables:
            if not tab.is_file():
                continue
            seen_by = dict(texts)
            for origin, ovs in inherited.items():
                if (names.index(ver) < names.index(origin) if tkind == "target"
                        else ver not in shared_chain(mv, origin)):
                    seen_by.update(ovs)
            for line, frm, to, note in table_rows(tab):
                cited = {c: notes[c] for c in dict.fromkeys(CITE.findall(note)) if c in notes}
                own = KNOWN.search(note)
                if not own and not cited:
                    continue
                fired, where = row_fires(frm, to, seen_by, ps)
                if not fired:
                    pat = frm[3:] if frm.startswith("re:") else frm
                    fired, where = (pat in hits or frm in hits), "build/mc%s/mv/hits-*.txt" % t
                if not fired:
                    continue
                rel_tab = tab.relative_to(repo).as_posix()
                g = add("catalogue", (rel_tab, frm), t,
                        {"title": f"rename row {rel_tab}:{line} fired, and its note says the gates do not cover it",
                         "table": rel_tab, "line": line, "row": frm + ("\t" + to if to else ""), "note": note[:800],
                         "catalogue": cited, "matched": {}}, f"{rel_tab}:{line}")
                g["matched"][t] = where
        # DEPS.md drops
        for dep, role, reason in deps_dropped(mv / "targets" / t / "DEPS.md"):
            g = add("dropped-dep", (dep,), t, {"title": f"{dep} ({role}) is not on the classpath: its integration is off",
                                               "dependency": dep, "role": role, "reasons": {}},
                    f".multiversion/targets/{t}/DEPS.md")
            g["reasons"][t] = reason
    items = sorted(groups.values(), key=lambda g: (mc_key(sorted(g["targets"], key=mc_key)[0]), KINDS.index(g["kind"]),
                                                    g["title"]))
    for i, g in enumerate(items, 1):
        g["targets"] = sorted(g["targets"], key=mc_key)
        g["first"] = g["targets"][0]
        g["range"] = runs(g["targets"], order)
        g["id"] = f"{g['kind']}-{i}"
    by_version = {t: [g["id"] for g in items if t in g["targets"]] for t in targets}
    first_in = {t: [g["id"] for g in items if g["first"] == t] for t in targets}
    return {"repo": str(repo), "name": repo.name, "kind": "layer", "base": base, "versions": order,
            "layer": {"targets": info, "items": items, "by_version": by_version, "first_in": first_in}}


def render(plans):
    out = []
    for p in plans:
        if p["kind"] == "single-version":
            out.append(f"# {p['name']}: single-version fork (Minecraft {', '.join(p['versions']) or '?'})")
            out.append("")
            continue
        L = p["layer"]
        out.append(f"# {p['name']}: layer fork, base {p['base']}, targets {', '.join(t['target'] for t in L['targets'])}")
        pv = [t["target"] for t in L["targets"] if t["preview"]]
        if pv:
            out.append(f"preview (built and gated, not published): {', '.join(pv)}")
        out.append("")
        for t in [x["target"] for x in L["targets"]]:
            new = [g for g in L["items"] if g["id"] in L["first_in"][t]]
            carried = [g["id"] for g in L["items"] if g["id"] in L["by_version"][t] and g["first"] != t]
            out.append(f"## Minecraft {t}: {len(new)} new item(s)" + (f"; also applies: {', '.join(carried)}" if carried else ""))
            for g in new:
                out.append(f"- [{g['id']}] {g['title']}  (targets {g['range']}; first {g['first']})")
                for s in g["sources"][:3]:
                    out.append(f"    source: {s}")
                if g["kind"] == "overlay" and (g["mixin"] or g["client"]):
                    out.append("    " + ", ".join(k for k in ("mixin", "client") if g[k]))
                if g["kind"] == "stub":
                    out.append(f"    status: {g['status']}" + (f"; review: {g['review'][:300]}" if g["review"] else ""))
                    if g.get("recheck"):
                        out.append(f"    recheck: {g['recheck'][:300]}")
                if g["kind"] == "catalogue":
                    for c, txt in g["catalogue"].items():
                        out.append(f"    CATALOG {c}: {txt[:300]}")
                    if not g["catalogue"]:
                        out.append(f"    note: {g['note'][:300]}")
                if g["kind"] == "dropped-dep":
                    out.append("    reason: " + "; ".join(f"{k}: {v}" for k, v in g["reasons"].items())[:300])
                if g["kind"] == "behaviour":
                    out.append(f"    {g['status']}: {g['detail'][:300]}")
            out.append("")
    return "\n".join(out).rstrip() + "\n"


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

    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        cat = d / "CATALOG.md"
        w(cat, "# Catalog\n\n**Z1. A sky pass moved** · **Fix:** a row. · Why no gate sees it: Gate C never looks at the sky.\n"
               "continued text\n\n**Z2. A rename** · **Fix:** a row, compile-checked.\n")
        # single-version fork: one version, no layer section
        sv = d / "single"
        w(sv / "gradle.properties", "minecraft_version=1.21.1\nmod_id=examplemod\n")
        w(sv / "src/main/java/com/example/mod/A.java", "class A {}\n")
        p = plan_fork(sv, cat)
        expect(p["kind"] == "single-version" and p["versions"] == ["1.21.1"] and "layer" not in p and "items" not in p,
               f"single-version: one version, no layer section: {p}")
        expect(render([p]) == "# single: single-version fork (Minecraft 1.21.1)\n", f"single-version text: {render([p])!r}")
        # layer fork
        lf = d / "layer"
        mvd = lf / ".multiversion"
        w(lf / "gradle.properties", "minecraft_version=1.21.1\n")
        w(mvd / "base.properties", "minecraft_version=1.21.1\n")
        w(lf / "src/main/java/com/example/mod/client/SkyRenderer.java", "class SkyRenderer { void draw() { fooSky(); } }\n")
        w(lf / "src/main/java/com/example/mod/mixin/LevelRendererMixin.java", "class LevelRendererMixin { Old x; }\n")
        w(lf / "src/main/java/com/example/mod/Util.java", "class Util { void f() { quietCall(); renamedOnly(); } }\n")
        w(lf / "src/cf/java/com/example/mod/cf/Extra.java", "class Extra {}\n")
        w(mvd / "targets/1.21.3/target.properties", "prop.minecraft_version=1.21.3\n")
        w(mvd / "targets/1.21.4/target.properties", "inherits=1.21.3\n")
        w(mvd / "targets/1.21.5/target.properties", "inherits=1.21.4\npreview=true\n")
        w(mvd / "targets/1.21.8/target.properties", "inherits=1.21.5\npreview=false\n")
        # 1.21.3 overlays SkyRenderer (inherited to 1.21.4/1.21.5); 1.21.8 overlays it again: a new item from 1.21.8
        w(mvd / "targets/1.21.3/java/com/example/mod/client/SkyRenderer.java", "class SkyRenderer { void draw() {} }\n")
        w(mvd / "targets/1.21.8/java/com/example/mod/client/SkyRenderer.java", "class SkyRenderer { void draw2() {} }\n")
        w(mvd / "targets/1.21.4/java/com/example/mod/mv/Helper.java", "class Helper {}\n")      # overlay-only: not listed
        w(mvd / "targets/1.21.4/cf/java/com/example/mod/cf/Extra.java", "class Extra { int x; }\n")
        # STUBS: one review inherited across 1.21.4-1.21.5, one reopened by a hook change on 1.21.8
        stub = ".multiversion/targets/{t}/java/com/example/mod/mixin/LevelRendererMixin.java"
        why = "removes 1 mixin injection handler(s): check each still applies somewhere, with the same effect"
        w(mvd / "targets/1.21.4/STUBS.json", json.dumps([{"file": stub.format(t="1.21.4"), "why": why,
                                                           "reviewed": "moved to the AFTER_SKY event, same effect",
                                                           "hooks": ["@Mixin LevelRenderer"]}]))
        w(mvd / "targets/1.21.5/STUBS.json", json.dumps([{"file": stub.format(t="1.21.5"), "why": why,
                                                           "reviewed": "(inherited from 1.21.4) moved to the AFTER_SKY event, same effect",
                                                           "hooks": ["@Mixin LevelRenderer"]}]))
        w(mvd / "targets/1.21.8/STUBS.json", json.dumps([{"file": stub.format(t="1.21.8"), "why": why, "reviewed": None,
                                                           "hooks": ["@SubscribeEvent RenderLevelStageEvent"],
                                                           "inherited_review": "(inherited from 1.21.4) moved",
                                                           "recheck": "re-justify: hook targets changed from @Mixin LevelRenderer to @SubscribeEvent RenderLevelStageEvent"}]))
        # rename rows: shared 1.21.4 (applies to 1.21.4 and later)
        w(mvd / "shared/1.21.4/renames.tsv",
          "#!exhaustive  # shared library\n"
          "# ---- CATALOG Z1: the sky call moved into a frame pass\n"
          "re:\\bfooSky\\(\tbarSky(\n"          # fires only where SkyRenderer is NOT overlaid -- nowhere here
          "re:\\bquietCall\\(\tloudCall(\n"     # same note (run of rows), fires in Util
          "\n"
          "# known limit: a block lambda is not handled, and no gate opens the screen\n"
          "member:neverThere\tgone\n"            # noted, never fires
          "\n"
          "# ---- CATALOG Z2: plain rename\n"
          "renamedOnly\trenamed\n"               # fires, but its entry has no gates note: not listed
          "#!strict\n")
        w(mvd / "targets/1.21.8/renames.tsv", "# not covered by gates: the old Old type is swapped by hand\nOld\tNew\n")
        # DEPS.md: an optional integration dropped on 1.21.4 and 1.21.5
        deps = ("# Dependencies for Minecraft {t}\n\n| dependency | role | base pin | for this target | from |\n|---|---|---|---|---|\n"
                "| `curse.maven:examplelib-1` | extra | `2` | dropped: no build for {t} | curseforge |\n"
                "| `curse.maven:examplecore-3` | required | `4` | curse.maven:examplecore-3:5 | curseforge |\n")
        for t in ("1.21.4", "1.21.5"):
            w(mvd / f"targets/{t}/DEPS.md", deps.format(t=t))
        p = plan_fork(lf, cat)
        L = p.get("layer", {})
        items = L.get("items", [])
        kinds = {}
        for g in items:
            kinds.setdefault(g["kind"], []).append(g)
        expect(p["kind"] == "layer" and p["versions"] == ["1.21.1", "1.21.3", "1.21.4", "1.21.5", "1.21.8"], f"layer versions: {p}")
        ov = sorted((g["overlay"], g["range"], g["first"]) for g in kinds.get("overlay", []))
        expect(ov == [(".multiversion/targets/1.21.3/java/com/example/mod/client/SkyRenderer.java", "1.21.3-1.21.5", "1.21.3"),
                      (".multiversion/targets/1.21.4/cf/java/com/example/mod/cf/Extra.java", "1.21.4-1.21.8", "1.21.4"),
                      (".multiversion/targets/1.21.8/java/com/example/mod/client/SkyRenderer.java", "1.21.8", "1.21.8")],
               f"overlays replacing an authors' file, grouped over the targets that inherit them; a helper is not listed: {ov}")
        fl = {g["overlay"].split("/")[2]: (g["mixin"], g["client"]) for g in kinds.get("overlay", []) if "SkyRenderer" in g["overlay"]}
        expect(fl == {"1.21.3": (False, True), "1.21.8": (False, True)}, f"an overlay under client/ is flagged client: {fl}")
        st = sorted((g["status"], g["range"]) for g in kinds.get("stub", []))
        expect(st == [("recheck", "1.21.8"), ("reviewed", "1.21.4-1.21.5")],
               f"an inherited review is one item over its range; a reopened one is its own: {st}")
        rc = [g for g in kinds.get("stub", []) if g["status"] == "recheck"]
        expect(rc and rc[0].get("recheck", "").startswith("re-justify") and rc[0].get("hooks") == ["@SubscribeEvent RenderLevelStageEvent"],
               "a stub item carries the hooks and recheck fields")
        cg = sorted((g["row"].split("\t")[0], g["range"], tuple(g["catalogue"])) for g in kinds.get("catalogue", []))
        expect(cg == [("Old", "1.21.8", ()), ("re:\\bquietCall\\(", "1.21.4-1.21.8", ("Z1",))],
               f"fired rows with a gates note (own or a cited CATALOG entry's), grouped; unfired and note-less rows left out: {cg}")
        z1 = [g for g in kinds.get("catalogue", []) if g["catalogue"]]
        expect(z1 and "Gate C never looks at the sky" in z1[0]["catalogue"]["Z1"], "the CATALOG entry's gates sentence is carried")
        dd = [(g["dependency"], g["range"], g["reasons"]) for g in kinds.get("dropped-dep", [])]
        expect(dd == [("curse.maven:examplelib-1", "1.21.4-1.21.5", {"1.21.4": "no build for 1.21.4", "1.21.5": "no build for 1.21.5"})],
               f"a dropped optional integration, once over its range; a resolved one is not listed: {dd}")
        pv = [(g["range"]) for g in kinds.get("preview", [])]
        expect(pv == ["1.21.5"] and [t["preview"] for t in L["targets"]] == [False, False, True, False],
               f"preview=true marks the target (preview=false does not): {pv}")
        expect(L["first_in"]["1.21.3"] and all(i.startswith("overlay") for i in L["first_in"]["1.21.3"])
               and set(L["by_version"]["1.21.5"]) >= set(L["first_in"]["1.21.3"]), "per-version: new items and carried ones")
        # recorded build hits count as fired
        w(lf / "build/mc1.21.3/mv/hits-cf.txt", "\\bfooSky\\(\n")
        w(mvd / "shared/1.21.3/renames.tsv", "# ---- CATALOG Z1\nre:\\bfooSky\\(\tbarSky(\n")
        p2 = plan_fork(lf, cat)
        hit = [g for g in p2["layer"]["items"] if g["kind"] == "catalogue" and g["row"].startswith("re:\\bfooSky")]
        expect(hit and hit[0]["targets"] == ["1.21.3"] and "hits-" in hit[0]["matched"]["1.21.3"],
               f"a row the build recorded as hit counts as fired where the source no longer shows it: {hit}")
        expect(not any(g["kind"] == "behaviour" for g in p2["layer"]["items"]) and "inventory" not in p2["layer"]["targets"][0],
               "no INVENTORY.json: no behaviour items, the targets entries as before")
        # with the base build's behaviour inventory: a render use the 1.21.3 overlay lost is a candidate from 1.21.3 on
        # (inherited overlay), and a target with no prepared tree says it was not checked
        inv = {"items": [{"id": "main:com/example/mod/client/SkyRenderer.java#render-use:RenderType.sky", "set": "main",
                          "file": "com/example/mod/client/SkyRenderer.java", "kind": "render-use", "key": "RenderType.sky"},
                         {"id": "main:com/example/mod/Util.java#render-use:RenderType.cutout", "set": "main",
                          "file": "com/example/mod/Util.java", "kind": "render-use", "key": "RenderType.cutout"}]}
        w(mvd / "INVENTORY.json", json.dumps(inv))
        for t in ("1.21.3", "1.21.4", "1.21.5"):
            w(lf / f"build/mc{t}/mv/java/com/example/mod/client/SkyRenderer.java", "class SkyRenderer { void draw() {} }\n")
            w(lf / f"build/mc{t}/mv/java/com/example/mod/Util.java", "class Util { Object o = RenderType.cutout(); }\n")
        p3 = plan_fork(lf, cat)
        beh = [g for g in p3["layer"]["items"] if g["kind"] == "behaviour"]
        expect([(g["item"].rsplit(":", 1)[-1], g["status"], g["range"]) for g in beh] == [("RenderType.sky", "unaccounted", "1.21.3-1.21.5")],
               f"an inventory item that is not the same is one candidate over its range; a same one is not listed: {beh}")
        t8 = [x for x in p3["layer"]["targets"] if x["target"] == "1.21.8"][0]
        expect(str(t8.get("inventory", "")).startswith("not checked") and p3["layer"]["targets"][0]["inventory"].get("same") == 1,
               f"per target: the inventory counts, or why it was not checked: {p3['layer']['targets']}")
        expect("unaccounted: " in render([p3]), "the text names the behaviour item's status and detail")
        (mvd / "INVENTORY.json").unlink()
        # inherited_overlay_rows=true: a gates-note row of 1.21.4 that matches only 1.21.3's overlay fires on 1.21.4
        # (layer.py runs the inherited overlay through it); a 1.21.3 row does not reach its own target's overlay; off,
        # neither fires -- as before
        w(mvd / "targets/1.21.3/java/com/example/mod/client/SkyRenderer.java",
          "class SkyRenderer { void draw() { inOverlayOnly(); ownOnly(); } }\n")
        w(mvd / "targets/1.21.4/renames.tsv", "# known limit: no gate sees it\nre:\\binOverlayOnly\\(\tnewCall(\n")
        w(mvd / "targets/1.21.3/renames.tsv", "# known limit: no gate sees it\nre:\\bownOnly\\(\townCall(\n")
        rows_of = lambda pl: sorted((g["row"].split("\t")[0], g["range"]) for g in pl["layer"]["items"]
                                    if g["kind"] == "catalogue" and ("OverlayOnly" in g["row"] or "ownOnly" in g["row"]))
        off_rows = rows_of(plan_fork(lf, cat))
        w(mvd / "base.properties", "minecraft_version=1.21.1\ninherited_overlay_rows=true\n")
        on_rows = rows_of(plan_fork(lf, cat))
        w(mvd / "base.properties", "minecraft_version=1.21.1\n")
        expect(off_rows == [] and on_rows == [("re:\\binOverlayOnly\\(", "1.21.4-1.21.5")],
               f"inherited_overlay_rows: a later target's row fires in the overlay it inherits (on only; never the owner's "
               f"own row; 1.21.8 overlays the path itself): off {off_rows} on {on_rows}")
        txt = render([p, plan_fork(sv, cat)])
        expect("## Minecraft 1.21.5: " in txt and "preview (built and gated, not published): 1.21.5" in txt
               and txt.rstrip().endswith("single-version fork (Minecraft 1.21.1)") and "## Minecraft 1.21.1" not in txt,
               f"text: one section per target, the single-version fork as one line: {txt}")
        json.dumps({"forks": [p, p2]})
    # the real CATALOG parses and has entries with a gates note
    expect(len(catalogue_notes()) > 5, "CATALOG.md has entries with a gates note")
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    if "--self-check" in sys.argv:
        return self_check()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", action="append", required=True, help="a fork checkout (repeat for several forks)")
    ap.add_argument("--json", action="store_true", help="print the items as JSON")
    a = ap.parse_args()
    plans = [plan_fork(r) for r in a.repo]
    print(json.dumps({"forks": plans}, indent=1) if a.json else render(plans), end="" if not a.json else "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
