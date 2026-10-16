#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Helper for a fork's .multiversion/ layer (mc-mod-version-upgrade: templates/fork-layer).

  layer.py prepare   --target T --src src/main/java --out DIR
        Merge T's chain (T, its `inherits`, theirs ...) into one rename table, one overlay tree and one
        drop list -- the nearer target wins -- and run prepare-sources.py over the authors' tree. Overlays are
        copied as they are; with `inherited_overlay_rows=true` in .multiversion/base.properties (opt-in) an overlay
        T inherits from an older target A is first run through the rows of the targets after A up to T; such a
        tree is stamped (build/mc<T>/mv/java.inherited_overlay_rows, for the main set's default --out only: the tree
        tools/fork-port.py and the Gradle build use), and an off-mode prepare of it removes the stamp.
  layer.py mixins    --target T [--config FILE]
        Print T's mixin changes per config file (mixin_add over the chain, then T's own mixin_add_only and
        mixin_remove_only; layer.gradle applies the same set at processResources), or, with --config, the
        arrays that config ends up with on T. prepare leaves out of T's tree each mixin class that T's chain adds
        only for another target, and each class T itself removes, so the compile and the mixin audit see that set.
  layer.py props     --target T
        Print the -P arguments T builds with (one per line), from its target.properties `prop.*` keys.
  layer.py isolation --base REF
        Fail unless HEAD differs from REF only inside .multiversion/ and by the hook lines appended to
        build.gradle. That rule is what keeps every merge from the base branch conflict-free.
  layer.py compare-jars A.jar B.jar
        Fail unless the two jars hold the same entries with the same bytes (MANIFEST timestamps aside).
        CI builds the base jar with and without the hook line and compares them: the layer must be inert.
  layer.py --self-check
"""
import re, argparse, hashlib, json, os, pathlib, shutil, subprocess, sys, tempfile, zipfile

HERE = pathlib.Path(__file__).resolve().parent
MV = HERE.parent
HOOK_MARK = "apply from: '.multiversion/layer.gradle'"


def load_props(path):
    out = {}
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def chain(target, root=None):
    root = root or MV
    seen, out, t = set(), [], target
    while t:
        if t in seen:
            sys.exit(f"inherits loop at {t}")
        seen.add(t)
        p = root / "targets" / t / "target.properties"
        if not p.exists():
            sys.exit(f"no such target: {p}")
        out.append(t)
        t = load_props(p).get("inherits")
    return out  # nearest first


def _mixin_spec(value):
    """`client:A,B;mixins:C` -> [(array, class), ...] (layer.gradle reads the same form)."""
    out = []
    for spec in value.split(";"):
        array, _, classes = spec.partition(":")
        out += [(array.strip(), c.strip()) for c in classes.split(",") if c.strip()]
    return out


def mixin_set(target, root=None):
    """(adds, removes) for `target`, each {config file: {array: [class, ...]}}: every mixin_add of its chain
    (farthest first), then its OWN mixin_add_only; removes are its own mixin_remove_only. The _only keys of the
    targets it inherits are not read: they apply to that target alone (issue #119)."""
    root = root or MV
    adds, removes = {}, {}
    def put(into, cfg, value):
        for array, c in _mixin_spec(value):
            lst = into.setdefault(cfg, {}).setdefault(array, [])
            if c not in lst:
                lst.append(c)
    for t in reversed(chain(target, root)):
        props = load_props(root / "targets" / t / "target.properties")
        for k, v in sorted(props.items()):
            if k.startswith("mixin_add."):
                put(adds, k[len("mixin_add."):], v)
        if t == target:
            for k, v in sorted(props.items()):
                if k.startswith("mixin_add_only."):
                    put(adds, k[len("mixin_add_only."):], v)
                elif k.startswith("mixin_remove_only."):
                    put(removes, k[len("mixin_remove_only."):], v)
    return adds, removes


def mixins_off(target, root=None):
    """{config file: {class, ...}}: the mixin classes `target`'s prepared tree leaves out -- each one an inherited
    target added with mixin_add_only and `target` does not add itself, and each one `target` takes off with
    mixin_remove_only. Their sources would otherwise reach the target through the overlay chain (or the authors'
    tree) and be compiled and audited as if they applied."""
    root = root or MV
    adds, removes = mixin_set(target, root)
    on = {(cfg, c) for cfg, arrs in adds.items() for cs in arrs.values() for c in cs}
    off = set()
    for t in chain(target, root)[1:]:
        for k, v in load_props(root / "targets" / t / "target.properties").items():
            if k.startswith("mixin_add_only."):
                off |= {(k[len("mixin_add_only."):], c) for _, c in _mixin_spec(v)}
    off = (off - on) | {(cfg, c) for cfg, arrs in removes.items() for cs in arrs.values() for c in cs}
    out = {}
    for cfg, c in off:
        out.setdefault(cfg, set()).add(c)
    return out


def mixin_config(cfg, names, root=None):
    """The JSON of mixin config `cfg` as the target's resources see it: a target overlay's copy (nearest first),
    else the authors' src/<set>/resources/<cfg>; None when there is none."""
    root = root or MV
    for p in [root / "targets" / t / "resources" / cfg for t in names] + sorted((root.parent / "src").glob(f"*/resources/{cfg}")):
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    return None


def apply_mixins(config, cfg, adds, removes):
    """`config` (the parsed JSON) with layer.gradle's processResources changes applied: the arrays, sorted."""
    out = {}
    for array, classes in (adds.get(cfg) or {}).items():
        out.setdefault(array, list(config.get(array) or []))
        out[array] += [c for c in classes if c not in out[array]]
    for array, classes in (removes.get(cfg) or {}).items():
        lst = out.setdefault(array, list(config.get(array) or []))
        for c in classes:
            if c not in lst:
                sys.exit(f"mixin_remove_only.{cfg} names {array}:{c}, which {cfg} does not list")
            lst[:] = [x for x in lst if x != c]
    for array, v in config.items():
        if isinstance(v, list):
            out.setdefault(array, list(v))
    return {k: sorted(v) for k, v in sorted(out.items())}


def cmd_mixins(a):
    adds, removes = mixin_set(a.target)
    if a.config:
        cfgpath = pathlib.Path(a.config)
        config = json.loads(cfgpath.read_text(encoding="utf-8"))
        arrays = apply_mixins(config, cfgpath.name, adds, removes)
        print(";".join(f"{k}:{','.join(v)}" for k, v in arrays.items()))
        return 0
    for cfg in sorted(set(adds) | set(removes)):
        for array, cs in sorted((adds.get(cfg) or {}).items()):
            print(f"{cfg} {array} + {','.join(cs)}")
        for array, cs in sorted((removes.get(cfg) or {}).items()):
            print(f"{cfg} {array} - {','.join(cs)}")
    return 0


def drop_mixins_off(target, names, overlay, src):
    """Leave `mixins_off(target)` out of the prepared tree: delete it from the merged overlay, and return the
    authors' files (relative to `src`) to add to the drop list. A class with no source in this set is skipped (a
    mixin of another source set)."""
    drops = set()
    for cfg, classes in sorted(mixins_off(target).items()):
        config = mixin_config(cfg, names)
        if config is None or not config.get("package"):
            sys.exit(f"layer: {target}: a mixin_*_only key names {cfg}, which has no `package` in targets/*/resources "
                     f"or src/*/resources -- cannot find its classes")
        for c in sorted(classes):
            rel = config["package"].replace(".", "/") + "/" + c.replace(".", "/") + ".java"
            hit = False
            if (overlay / rel).is_file():
                (overlay / rel).unlink(); hit = True
            if (pathlib.Path(src) / rel).is_file():
                drops.add(rel); hit = True
            if hit:
                print(f"layer: {target} leaves mixin {c} out (mixin_add_only of an inherited target, or this "
                      f"target's mixin_remove_only)")
    return drops


def mc_key(v):
    return tuple(int(x) if x.isdigit() else 0 for x in re.findall(r"\d+|[A-Za-z]+", v))


def shared_chain(target, root=None):
    """The targets whose SHARED library (shared/<t>/) applies to `target`, nearest (newest) first.

    Normally that is the `inherits` chain. A target that jumps over the middle versions (1.21.11 with no 1.21.3..1.21.10
    targets in the fork) adds `shared_chain=1.21.3,1.21.4,...,1.21.11` to its target.properties (add-target --direct
    writes it from versions.json). The union over the target's whole chain is used, ordered by version, newest first:
    the order the same targets have in a hop-by-hop `inherits` chain, so rows (nearest first) and helpers (nearest
    wins) resolve identically. Without the key this is `chain(target)`, unchanged."""
    root = root or MV
    names = chain(target, root)
    extra = []
    for t in names:
        extra += [x.strip() for x in load_props(root / "targets" / t / "target.properties").get("shared_chain", "").split(",")
                  if x.strip()]
    if not extra:
        return names
    return sorted(set(names) | set(extra), key=mc_key, reverse=True)


INHERITED_ROWS = "inherited_overlay_rows"


def inherited_overlay_rows(root=None):
    """True when .multiversion/base.properties says `inherited_overlay_rows=true` (opt-in, default off).

    Off (the default, and every layer made before the key existed): an overlay reaches every later target VERBATIM --
    rename rows apply to the authors' files only, so a later target that needs the overlay renamed carries its own
    renamed copy. On: when preparing T, an overlay whose nearest owner is an older target A in T's chain (T has no copy
    of its own) is run through the rows of the chain targets after A up to T -- their renames.tsv and
    generated moves, then the shared tables of the versions T's libraries have and A's do not (rows_after) --,
    never through A's or older rows, which it was written against. T's own overlays are copied as they are, either way."""
    p = (root or MV) / "base.properties"
    return p.is_file() and load_props(p).get(INHERITED_ROWS, "").lower() == "true"


def rows_after(target, owner, root=None):
    """(fork targets, shared libraries) whose rows an overlay `owner` wrote takes on its way to `target`: the targets of
    target's chain strictly nearer than owner (owner's own and older rows are what the overlay was written against),
    and the shared libraries of target that owner's own shared chain does not have. Both nearest first, the order
    cmd_prepare's table has."""
    root = root or MV
    names, snames = chain(target, root), shared_chain(target, root)
    older = set(shared_chain(owner, root))
    return names[:names.index(owner)], [s for s in snames if s not in older]


def _prepare_sources_module():
    import importlib.util
    sys.dont_write_bytecode = True        # never leave a __pycache__ in the fork's .multiversion/tools
    spec = importlib.util.spec_from_file_location("layer_prepare_sources", HERE / "prepare-sources.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def apply_rows(text, rules, hits):
    """`text` rewritten by `rules` (prepare-sources.py's split_rules + build_pattern) exactly as prepare-sources.py
    rewrites an authors' file: regex/member rules first, in table order, then the type-token sweep; CRLF kept."""
    regex_rules, pattern, table = rules
    crlf = "\r\n" in text
    if crlf:
        text = text.replace("\r\n", "\n")
    for rx, repl, _ex in regex_rules:
        text, n = rx.subn(repl, text)
        if n:
            hits.add(rx.pattern)
    if pattern is not None:
        def tok(m):
            hits.add(m.group(1))
            return table[m.group(1)]
        text = pattern.sub(tok, text)
    return text.replace("\n", "\r\n") if crlf else text


def rename_inherited_overlays(target, names, sub, overlay, tmp, rows):
    """inherited_overlay_rows=true: rewrite, in the merged `overlay` tree, every .java overlay whose nearest owner is
    an older target of the chain by the sections of cmd_prepare's own table (`rows`, each starting `# ---- <what>`)
    that rows_after(target, owner) names -- the same text, in the same order. -> the set of rules that fired (so the
    dead-rule check counts a row that fires only in an inherited overlay as live)."""
    owner = {}
    for t in names:                        # nearest first: the nearest copy is the one in the merged tree
        d = MV / "targets" / t / sub
        if d.is_dir():
            for f in d.rglob("*.java"):
                owner.setdefault(f.relative_to(d).as_posix(), t)
    by_owner = {}
    for rel, t in sorted(owner.items()):
        if t != target and (overlay / rel).is_file():
            by_owner.setdefault(t, []).append(rel)
    hits, n = set(), 0
    if not by_owner:
        return hits
    ps = _prepare_sources_module()
    for t in names:
        if t not in by_owner:
            continue
        fork, shared = rows_after(target, t)
        heads = {f"# ---- {x}" for x in fork} | {f"# ---- moves {x} (generated)" for x in fork} | {f"# ---- shared {x}" for x in shared}
        table = tmp / f"rows-after-{t}.tsv"
        table.write_text("\n".join(r for r in rows if r.split("\n", 1)[0] in heads) + "\n", encoding="utf-8")
        token_rules, regex_rules = ps.split_rules(ps.load_renames(str(table)))
        pattern, tab = ps.build_pattern(token_rules)
        for rel in by_owner[t]:
            f = overlay / rel
            try:
                with open(f, encoding="utf-8", newline="") as fh:
                    text = fh.read()
            except UnicodeDecodeError as e:
                # prepare-sources.py reads every authors' file as UTF-8 and stops on one that is not; rows cannot be
                # run over this overlay either, so stop the same way -- naming it -- rather than build it un-renamed
                sys.exit(f"layer: {target}: the overlay {(MV / 'targets' / t / sub / rel).relative_to(MV.parent).as_posix()} "
                         f"(inherited from {t}) is not UTF-8 ({e.reason} at byte {e.start}), so {INHERITED_ROWS}=true cannot "
                         f"run the rows after {t} over it: save it as UTF-8 (every source the layer renames is read as UTF-8)")
            new = apply_rows(text, (regex_rules, pattern, tab), hits)
            if new != text:
                with open(f, "w", encoding="utf-8", newline="") as fh:
                    fh.write(new)
                n += 1
    print(f"layer: {target} inherited overlays through the rows after their owner ({INHERITED_ROWS}=true): "
          f"{sum(len(v) for v in by_owner.values())} inherited, {n} rewritten")
    return hits


def cmd_prepare(a):
    names = chain(a.target)
    snames = shared_chain(a.target)
    if snames != names:
        print(f"layer: {a.target} shared libraries {' > '.join(snames)} (shared_chain)")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        overlay = tmp / "overlay"
        overlay.mkdir()
        # farthest first, so a nearer target's file overwrites
        sub = "java" if a.set == "main" else f"{a.set}/java"      # a non-main source set's overlays: targets/<t>/<set>/java
        for t in reversed(names):
            d = MV / "targets" / t / sub
            if d.is_dir():
                shutil.copytree(d, overlay, dirs_exist_ok=True)
        mixin_drops = drop_mixins_off(a.target, names, overlay, a.src)
        # rename rows: nearer target's rows FIRST so a correction to an inherited row is applied before it; the
        # shared tables (mc-mod-version-upgrade, synced into .multiversion/shared/) after every fork row
        rows = []
        for t in names:
            r = MV / "targets" / t / "renames.tsv"
            if r.exists():
                rows.append(f"# ---- {t}\n" + r.read_text(encoding="utf-8"))
            m = MV / "targets" / t / "moves.generated.tsv"   # tools/fork-port.py: derived from the staged jars
            if m.exists():
                rows.append(f"# ---- moves {t} (generated)\n" + m.read_text(encoding="utf-8"))
        for t in snames:
            r = MV / "shared" / t / "renames.tsv"
            if r.exists():
                rows.append(f"# ---- shared {t}\n" + r.read_text(encoding="utf-8"))
        renames = tmp / "renames.tsv"
        renames.write_text("\n".join(rows) + "\n", encoding="utf-8")
        inherited_hits = None
        if inherited_overlay_rows():
            inherited_hits = rename_inherited_overlays(a.target, names, sub, overlay, tmp, rows)
        drops = set()
        for t in names:
            d = MV / "targets" / t / "drops.txt"
            if d.exists():
                drops |= {l.split("#", 1)[0].strip() for l in d.read_text(encoding="utf-8").splitlines()} - {""}
        drops |= mixin_drops
        args = [sys.executable, str(HERE / "prepare-sources.py"), "--src", a.src, "--out", a.out,
                "--overlay", str(overlay), "--renames", str(renames)]
        if drops:
            dl = tmp / "drops.txt"
            dl.write_text("\n".join(sorted(drops)) + "\n", encoding="utf-8")
            args += ["--drop", str(dl)]
        # one table, several source sets: "matched nothing" is a property of the union (CATALOG X22). A non-main set
        # records its hits next to its output; main (prepared last) merges every set's and reports once.
        out = pathlib.Path(a.out)
        hits_dir = out.parent
        # the mode the tree is prepared in, beside it (tools/fork-port.py's staleness check reads it): only for the main
        # set's default tree, build/mc<T>/mv/java (the Gradle build's and fork-port's), and written only when on, so a
        # default layer's build directory is as before; a stamp left by an earlier on-mode run goes
        default = (MV.parent / "build" / f"mc{a.target}" / "mv" / "java").resolve()
        stamp = out.parent / f"{out.name}.{INHERITED_ROWS}" if a.set == "main" and out.resolve() == default else None
        if stamp is not None and stamp.is_file():
            stamp.unlink()
        if a.set != "main":
            args += ["--hits-out", str(hits_dir / f"hits-{a.set}.txt")]
        else:
            merged = sorted(hits_dir.glob("hits-*.txt"))
            if merged or inherited_hits:
                allhits = tmp / "hits.txt"
                allhits.write_text("".join(f.read_text(encoding="utf-8") for f in merged)
                                   + "".join(h + "\n" for h in sorted(inherited_hits or ())), encoding="utf-8")
                args += ["--hits-in", str(allhits)]
        print(f"layer: {a.target} [{a.set}] chain {' > '.join(names)}")
        rc = subprocess.call(args)
        if rc == 0 and a.set != "main" and inherited_hits:     # a row that fired only in an inherited overlay is live
            hf = hits_dir / f"hits-{a.set}.txt"
            have = set(hf.read_text(encoding="utf-8").split("\n")) if hf.is_file() else set()
            hf.write_text("".join(h + "\n" for h in sorted((have | set(inherited_hits)) - {""})), encoding="utf-8")
        if rc == 0 and a.set == "main":
            add_shared_helpers(snames, out)
        if rc == 0 and load_props(MV / "targets" / a.target / "target.properties").get("strip_onlyin") == "true":
            strip_onlyin(out)
        if rc == 0 and inherited_hits is not None and stamp is not None:
            stamp.write_text(f"{INHERITED_ROWS}=true\n", encoding="utf-8")
        return rc


ONLYIN = re.compile(r"\n[ \t]*@OnlyIn\([^)]*\)[ \t]*")


def strip_onlyin(out):
    """CATALOG V79: from 1.21.6 (as on 26.x) @OnlyIn strips nothing, and in a DEV run any @OnlyIn in the mod is a
    mod-loading error. A rename row cannot reach an own overlay, so a target with `strip_onlyin=true` has the annotation
    removed from its whole PREPARED tree -- the authors' files and every overlay the chain merged in. Earlier targets,
    where @OnlyIn still strips client classes on a server, keep it."""
    n = 0
    for f in pathlib.Path(out).rglob("*.java"):
        t = f.read_text(encoding="utf-8")
        new, k = ONLYIN.subn("", t)
        if k:
            f.write_text(new, encoding="utf-8"); n += k
    if n:
        print(f"layer: stripped {n} @OnlyIn (strip_onlyin=true)")


def add_shared_helpers(names, out):
    """Copy in the shared helper classes (.multiversion/shared/<t>/java) that the prepared tree actually uses,
    transitively. A helper nothing references stays out of the jar; a fork's own overlay of the same path wins."""
    pool = {}
    for t in reversed(names):          # farthest first, nearer target's copy wins
        d = MV / "shared" / t / "java"
        if d.is_dir():
            for f in d.rglob("*.java"):
                pool[f.relative_to(d).as_posix()] = f
    if not pool:
        return
    def fqn(rel):
        return rel[:-5].replace("/", ".")
    text = "\n".join(p.read_text(encoding="utf-8") for p in out.rglob("*.java"))
    added, changed = set(), True
    while changed:
        changed = False
        for rel, f in pool.items():
            if rel in added or (out / rel).exists():
                continue
            name = rel.rsplit("/", 1)[-1][:-5]
            # a helper nothing names but that must ride along (an event subscriber) is pulled in by a
            # `// MV-REQUIRES: Name` line in a helper that is used
            if fqn(rel) in text or (fqn(rel).rsplit(".", 1)[0] + ".*") in text or f"MV-REQUIRES: {name}" in text:
                dest = out / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                body = f.read_text(encoding="utf-8")
                dest.write_text(body, encoding="utf-8")
                text += "\n" + body
                added.add(rel); changed = True
    if added:
        print(f"layer: shared helpers used: {', '.join(sorted(r.rsplit('/', 1)[-1][:-5] for r in added))}")


def cmd_props(a):
    p = load_props(MV / "targets" / a.target / "target.properties")
    for k, v in sorted(p.items()):
        if k.startswith("prop."):
            print(f"-P{k[5:]}={v}")
    print(f"-Pmc={a.target}")
    return 0


def git(*args, cwd="."):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=True).stdout


def isolation_problems(changed, build_diff):
    probs = [f"{f}: outside .multiversion/" for f in changed
             if not f.startswith(".multiversion/") and f != "build.gradle"]
    if "build.gradle" in changed:
        added = [l[1:] for l in build_diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
        removed = [l for l in build_diff.splitlines() if l.startswith("-") and not l.startswith("---")]
        if removed:
            probs.append("build.gradle: lines removed or changed (only the hook may be appended)")
        if sum(1 for l in added if l.strip() == HOOK_MARK) != 1:
            probs.append("build.gradle: the hook line is missing or repeated")
        if any(l.strip() and not l.lstrip().startswith("//") and l.strip() != HOOK_MARK for l in added):
            probs.append("build.gradle: added lines other than the commented hook")
    return probs


def cmd_isolation(a):
    changed = [f for f in git("diff", "--name-only", f"{a.base}...HEAD").splitlines() if f]
    diff = git("diff", "-U0", f"{a.base}...HEAD", "--", "build.gradle") if "build.gradle" in changed else ""
    probs = isolation_problems(changed, diff)
    for p in probs:
        print("isolation:", p)
    print(f"isolation: {len(changed)} changed file(s) vs {a.base}: {'FAIL' if probs else 'ok'}")
    return 1 if probs else 0


def jar_digest(path):
    out = {}
    with zipfile.ZipFile(path) as z:
        for i in z.infolist():
            if i.is_dir() or i.filename == "META-INF/MANIFEST.MF":
                continue
            data = z.read(i)
            if i.filename.endswith(".jar"):  # nested jar: compare its contents, not its zip timestamps
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jar")
                tmp.write(data); tmp.close()
                try:
                    data = repr(sorted(jar_digest(tmp.name).items())).encode()
                finally:
                    os.unlink(tmp.name)
            out[i.filename] = hashlib.sha256(data).hexdigest()
    return out


def cmd_compare(a):
    x, y = jar_digest(a.a), jar_digest(a.b)
    only_a, only_b = sorted(set(x) - set(y)), sorted(set(y) - set(x))
    diff = sorted(k for k in set(x) & set(y) if x[k] != y[k])
    for k in only_a[:20]: print("only in", a.a, k)
    for k in only_b[:20]: print("only in", a.b, k)
    for k in diff[:20]: print("differs:", k)
    bad = only_a or only_b or diff
    print(f"compare-jars: {len(x)} entries; {'DIFFER' if bad else 'identical'}")
    return 1 if bad else 0


def check_direct_jump(expect):
    """A direct 1.21.11 target with shared_chain prepares the same tree as the hop-by-hop 1.21.3 > ... > 1.21.11
    chain for shared-only content (rows and helper classes); without the key only 1.21.11's own library applies."""
    global MV
    saved = MV
    ns = lambda root: argparse.Namespace(target="1.21.11", src=str(root / "src"), out=str(root / "out"), set="main")
    hops = ["1.21.3", "1.21.4", "1.21.5", "1.21.11"]
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        trees = {}
        try:
            for label in ("chained", "direct", "direct-without-key"):
                root = d / label
                (root / "src/m").mkdir(parents=True)
                (root / "src/m/A.java").write_text("package m;\nimport old.One;\nimport old.Two;\nimport old.Three;\n"
                                                   "import m.Helper;\nclass A { One a; Two b; Three c; Helper h; }\n", encoding="utf-8")
                mv = root / "mv"
                for t, row in zip(hops, ("old.One\tnew.One", "old.Two\tnew.Two", "old.Three\tnew.Three", "# none")):
                    (mv / "shared" / t).mkdir(parents=True)
                    (mv / "shared" / t / "renames.tsv").write_text(row + "\n", encoding="utf-8")
                for t in ("1.21.4", "1.21.11"):       # the same helper twice: the nearest (newest) copy must win
                    (mv / "shared" / t / "java/m").mkdir(parents=True)
                    (mv / "shared" / t / "java/m/Helper.java").write_text("package m;\nclass Helper { int v = %s; }\n"
                                                                          % t.replace(".", "_"), encoding="utf-8")
                tp = lambda t, body: ((mv / "targets" / t).mkdir(parents=True),
                                      (mv / "targets" / t / "target.properties").write_text(body, encoding="utf-8"))
                if label == "chained":
                    for i, t in enumerate(hops):
                        tp(t, f"inherits={hops[i - 1]}\n" if i else "x=1\n")
                else:
                    tp("1.21.11", "x=1\n" + ("shared_chain=1.21.3, 1.21.4,1.21.5,1.21.11\n" if label == "direct" else ""))
                MV = mv
                if label == "chained":
                    expect(shared_chain("1.21.11", mv) == chain("1.21.11", mv) == hops[::-1],
                           "no shared_chain key: the shared libraries are the inherits chain, as before")
                if label == "direct":
                    expect(shared_chain("1.21.11", mv) == hops[::-1], f"direct shared order is newest first: {shared_chain('1.21.11', mv)}")
                    expect(chain("1.21.11", mv) == ["1.21.11"], "the fork-target chain of a direct target is just itself")
                rc = cmd_prepare(ns(root))
                expect(rc == 0, f"{label}: prepare ran")
                trees[label] = {f.relative_to(root / "out").as_posix(): f.read_text(encoding="utf-8")
                                for f in (root / "out").rglob("*.java")}
        finally:
            MV = saved
    expect(trees["direct"] == trees["chained"], "a direct jump prepares the same tree as the chain for shared-only content")
    a_ = trees["chained"]["m/A.java"]
    expect("new.One" in a_ and "new.Two" in a_ and "new.Three" in a_, "all three intermediate shared rows applied")
    expect("1_21_11" in trees["chained"]["m/Helper.java"], "nearest helper wins")
    expect(trees["direct-without-key"] != trees["chained"] and "old.One" in trees["direct-without-key"]["m/A.java"],
           "control: without shared_chain the intermediate libraries are NOT pulled in (the test can tell)")


def check_inherited_rows(expect):
    """inherited_overlay_rows: A < B < C (1.21.3 < 1.21.4 < 1.21.5), an overlay at A, a row at A, B, C and the shared
    1.21.4 table. Off (no key, or =false): the overlay reaches B and C verbatim, as before. On: at B it takes B's rows
    and the shared 1.21.4 row, at C also C's; never A's; A's own tree and C's own overlay are as they were."""
    global MV
    saved, real_call = MV, subprocess.call
    out_log = []

    def call(args, *k, **kw):                  # prepare-sources' report, for the dead-rule assertion
        r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
        out_log.append(r.stdout + r.stderr)
        return r.returncode
    trees = {}
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        src = d / "src"; (src / "m").mkdir(parents=True)
        (src / "m/O.java").write_text("package m;\nclass O { old.A a; old.B b; }\n", encoding="utf-8")
        (src / "m/Own.java").write_text("package m;\nclass Own { old.B b; }\n", encoding="utf-8")
        (src / "m/P.java").write_text("package m;\nclass P { old.A a; old.B b; old.C c; old.S s; }\n", encoding="utf-8")
        mv = d / "mv"
        for t, inh in (("1.21.3", None), ("1.21.4", "1.21.3"), ("1.21.5", "1.21.4")):
            (mv / "targets" / t).mkdir(parents=True)
            (mv / "targets" / t / "target.properties").write_text(f"inherits={inh}\n" if inh else "x=1\n", encoding="utf-8")
        (mv / "targets/1.21.3/renames.tsv").write_text("old.A\tnew.A\n", encoding="utf-8")
        (mv / "targets/1.21.4/renames.tsv").write_text("old.B\tnew.B\n", encoding="utf-8")
        # the generated moves table (its file name spelled once in this file: tools/fork-port.py's layer_patch reads it)
        (mv / "targets/1.21.5" / ("moves.generated" + ".tsv")).write_text("old.C\tnew.C\nre:oldOnlyInOverlay\tnewOnlyInOverlay\n",
                                                               encoding="utf-8")
        (mv / "shared/1.21.4").mkdir(parents=True)
        (mv / "shared/1.21.4/renames.tsv").write_text("old.S\tnew.S\n", encoding="utf-8")
        overlay_a = "package m;\r\nclass O { old.A a; old.B b; old.C c; old.S s; int oldOnlyInOverlay; }\r\n"
        own_c = "package m;\nclass Own { old.B b; old.C c; /* C's own */ }\n"
        (mv / "targets/1.21.3/java/m").mkdir(parents=True)
        with open(mv / "targets/1.21.3/java/m/O.java", "w", encoding="utf-8", newline="") as fh:
            fh.write(overlay_a)
        (mv / "targets/1.21.5/java/m").mkdir(parents=True)
        with open(mv / "targets/1.21.5/java/m/Own.java", "w", encoding="utf-8", newline="") as fh:
            fh.write(own_c)               # these bytes on every OS (write_text would write CRLF on Windows)
        try:
            MV = mv
            subprocess.call = call
            for mode, props in (("none", None), ("false", "minecraft_version=1.21.1\ninherited_overlay_rows=false\n"),
                                ("on", "minecraft_version=1.21.1\ninherited_overlay_rows=true\n")):
                if props is None:
                    (mv / "base.properties").write_text("minecraft_version=1.21.1\n", encoding="utf-8")
                else:
                    (mv / "base.properties").write_text(props, encoding="utf-8")
                for t in ("1.21.3", "1.21.4", "1.21.5"):
                    o = d / f"out-{mode}-{t}"
                    out_log.clear()
                    rc = cmd_prepare(argparse.Namespace(target=t, src=str(src), out=str(o), set="main"))
                    expect(rc == 0, f"{mode} {t}: prepare ran")
                    tree = {}
                    for f in sorted(o.rglob("*.java")):
                        with open(f, encoding="utf-8", newline="") as fh:
                            tree[f.relative_to(o).as_posix()] = fh.read()
                    trees[(mode, t)] = (tree, "".join(out_log))
            expect(rows_after("1.21.5", "1.21.3", mv) == (["1.21.5", "1.21.4"], ["1.21.5", "1.21.4"]),
                   f"rows after A, nearest first: {rows_after('1.21.5', '1.21.3', mv)}")
            # the stamp: beside the default main tree only (build/mc<t>/mv/java), written on, removed off
            expect(not list(d.glob(f"*.{INHERITED_ROWS}")), "a custom --out gets no stamp beside it")
            dflt = d / "build/mc1.21.5/mv/java"
            st = d / f"build/mc1.21.5/mv/java.{INHERITED_ROWS}"
            cmd_prepare(argparse.Namespace(target="1.21.5", src=str(src), out=str(dflt), set="main"))
            expect(st.is_file() and st.read_text(encoding="utf-8") == f"{INHERITED_ROWS}=true\n",
                   "an on-mode prepare of build/mc<t>/mv/java stamps it")
            cmd_prepare(argparse.Namespace(target="1.21.5", src=str(src), out=str(d / "build/mc1.21.5/mv/cf-java"), set="cf"))
            expect(not (d / f"build/mc1.21.5/mv/cf-java.{INHERITED_ROWS}").exists(), "another source set gets no stamp")
            (mv / "base.properties").write_text("minecraft_version=1.21.1\n", encoding="utf-8")
            cmd_prepare(argparse.Namespace(target="1.21.5", src=str(src), out=str(dflt), set="main"))
            expect(not st.exists() and not list((d / "build/mc1.21.5/mv").glob(f"*.{INHERITED_ROWS}")),
                   "re-prepared off: the stamp goes, and nothing is written in its place")
            # a non-UTF-8 inherited overlay (prepare-sources stops on a non-UTF-8 authors' file; this stops the same way)
            (src / "m/L.java").write_text("class L {}\n", encoding="utf-8")       # it replaces an authors' file
            (mv / "targets/1.21.3/java/m/L.java").write_bytes(b"class L { String s = \"\xe9\"; }\n")
            for mode, body in (("off", "minecraft_version=1.21.1\n"), ("on", "minecraft_version=1.21.1\ninherited_overlay_rows=true\n")):
                (mv / "base.properties").write_text(body, encoding="utf-8")
                o = d / f"out-l-{mode}"
                try:
                    rc, msg = cmd_prepare(argparse.Namespace(target="1.21.4", src=str(src), out=str(o), set="main")), b""
                    msg = (o / "m/L.java").read_bytes() if rc == 0 else b""
                except SystemExit as e:
                    rc, msg = 1, str(e)
                trees[("non-utf8", mode)] = (rc, msg)
            (mv / "targets/1.21.3/java/m/L.java").unlink(); (src / "m/L.java").unlink()
        finally:
            MV, subprocess.call = saved, real_call
    off = {t: trees[("none", t)][0] for t in ("1.21.3", "1.21.4", "1.21.5")}
    on = {t: trees[("on", t)][0] for t in ("1.21.3", "1.21.4", "1.21.5")}
    for t in off:
        expect(trees[("false", t)][0] == off[t], f"inherited_overlay_rows=false prepares what no key does ({t})")
        expect(off[t]["m/O.java"] == overlay_a, f"off: the inherited overlay reaches {t} verbatim, as before")
    expect(on["1.21.3"] == off["1.21.3"], "on: the root target's tree is unchanged (its overlay is its own)")
    expect(on["1.21.4"]["m/O.java"] == overlay_a.replace("old.B", "new.B").replace("old.S", "new.S"),
           f"on: at B the overlay takes B's row and the shared 1.21.4 row, never A's: {on['1.21.4']['m/O.java']!r}")
    expect(on["1.21.5"]["m/O.java"] == overlay_a.replace("old.B", "new.B").replace("old.S", "new.S")
           .replace("old.C", "new.C").replace("oldOnlyInOverlay", "newOnlyInOverlay"),
           f"on: at C it takes B's and C's rows (and the shared row), never A's; CRLF kept: {on['1.21.5']['m/O.java']!r}")
    expect(on["1.21.5"]["m/Own.java"] == off["1.21.5"]["m/Own.java"] == own_c, "C's own overlay is copied as it is")
    for t in off:
        expect(on[t]["m/P.java"] == off[t]["m/P.java"], f"the authors' files are prepared the same either way ({t})")
    expect(trees[("non-utf8", "off")][0] == 0 and trees[("non-utf8", "off")][1] == b"class L { String s = \"\xe9\"; }\n"
           and trees[("non-utf8", "on")][0] != 0 and "targets/1.21.3/java/m/L.java" in trees[("non-utf8", "on")][1]
           and "is not UTF-8" in trees[("non-utf8", "on")][1],
           f"a non-UTF-8 inherited overlay: copied byte-for-byte off; on, the prepare stops naming it: {trees[('non-utf8', 'off')]} {trees[('non-utf8', 'on')]}")
    expect("DEAD RULE (matched nothing in any source root): oldOnlyInOverlay" in trees[("none", "1.21.5")][1]
           and "DEAD RULE" not in trees[("on", "1.21.5")][1],
           "a row that fires only in an inherited overlay is dead off and live on: " + trees[("on", "1.21.5")][1][-300:])


def self_check():
    ok = True
    def expect(c, msg):
        nonlocal ok
        if not c:
            ok = False; print("FAIL:", msg)
    check_direct_jump(expect)
    check_inherited_rows(expect)
    hook = f"+// comment\n+{HOOK_MARK}\n"
    expect(not isolation_problems([".multiversion/a", "build.gradle"], hook), "clean layer passes")
    expect(isolation_problems(["src/main/java/X.java"], ""), "source edit fails")
    expect(isolation_problems(["build.gradle"], "+// c\n"), "missing hook fails")
    expect(isolation_problems(["build.gradle"], hook + "+foo()\n"), "extra code line fails")
    expect(isolation_problems(["build.gradle"], hook + "-old\n"), "removed line fails")
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        for t, inh in (("1.21.3", None), ("1.21.4", "1.21.3")):
            (root / "targets" / t).mkdir(parents=True)
            (root / "targets" / t / "target.properties").write_text(f"inherits={inh}\n" if inh else "x=1\n", encoding="utf-8")
        expect(chain("1.21.4", root) == ["1.21.4", "1.21.3"], "chain")
        a, b = root / "a.jar", root / "b.jar"
        for p, body in ((a, b"x"), (b, b"x")):
            with zipfile.ZipFile(p, "w") as z:
                z.writestr("a/B.class", body); z.writestr("META-INF/MANIFEST.MF", str(p))
        expect(jar_digest(a) == jar_digest(b), "manifest ignored")
        with zipfile.ZipFile(b, "a") as z:
            z.writestr("c.txt", "y")
        expect(jar_digest(a) != jar_digest(b), "extra entry seen")
    with tempfile.TemporaryDirectory() as d:   # strip_onlyin: one-line and multi-line forms, nothing else touched
        f = pathlib.Path(d) / "A.java"
        f.write_text("package a;\n@OnlyIn(Dist.CLIENT)\nclass A {\n    @OnlyIn(\n        value = Dist.CLIENT,\n"
                     "        _interface = X.class\n    )\n    void m() {}\n    String s = \"@OnlyIn\";\n}\n", encoding="utf-8")
        strip_onlyin(d)
        t = f.read_text(encoding="utf-8")
        expect("@OnlyIn(" not in t and 'String s = "@OnlyIn";' in t and "void m() {}" in t, "strip_onlyin")
    print("self-check:", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-check", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("prepare"); p.add_argument("--target", required=True)
    p.add_argument("--src", required=True); p.add_argument("--out", required=True)
    p.add_argument("--set", default="main", help="the source set (overlays for a non-main set: targets/<t>/<set>/java)")
    p = sub.add_parser("mixins"); p.add_argument("--target", required=True)
    p.add_argument("--config", help="a mixin config JSON: print the arrays it ends up with on the target")
    p = sub.add_parser("props"); p.add_argument("--target", required=True)
    p = sub.add_parser("isolation"); p.add_argument("--base", required=True)
    p = sub.add_parser("compare-jars"); p.add_argument("a"); p.add_argument("b")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    return {"prepare": cmd_prepare, "mixins": cmd_mixins, "props": cmd_props, "isolation": cmd_isolation,
            "compare-jars": cmd_compare}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
