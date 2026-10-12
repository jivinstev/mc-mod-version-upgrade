#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Helper for a fork's .multiversion/ layer (mc-mod-version-upgrade: templates/fork-layer).

  layer.py prepare   --target T --src src/main/java --out DIR
        Merge T's chain (T, its `inherits`, theirs ...) into one rename table, one overlay tree and one
        drop list -- the nearer target wins -- and run prepare-sources.py over the authors' tree.
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
import re, argparse, hashlib, os, pathlib, shutil, subprocess, sys, tempfile, zipfile

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
        drops = set()
        for t in names:
            d = MV / "targets" / t / "drops.txt"
            if d.exists():
                drops |= {l.split("#", 1)[0].strip() for l in d.read_text(encoding="utf-8").splitlines()} - {""}
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
        if a.set != "main":
            args += ["--hits-out", str(hits_dir / f"hits-{a.set}.txt")]
        else:
            merged = sorted(hits_dir.glob("hits-*.txt"))
            if merged:
                allhits = tmp / "hits.txt"
                allhits.write_text("".join(f.read_text(encoding="utf-8") for f in merged), encoding="utf-8")
                args += ["--hits-in", str(allhits)]
        print(f"layer: {a.target} [{a.set}] chain {' > '.join(names)}")
        rc = subprocess.call(args)
        if rc == 0 and a.set == "main":
            add_shared_helpers(snames, out)
        if rc == 0 and load_props(MV / "targets" / a.target / "target.properties").get("strip_onlyin") == "true":
            strip_onlyin(out)
        return rc


ONLYIN = re.compile(r"\n[ \t]*@OnlyIn\([^)]*\)[ \t]*")


def strip_onlyin(out):
    """CATALOG V79: from 1.21.6 (as on 26.x) @OnlyIn strips nothing, and in a DEV run any @OnlyIn in the mod is a
    mod-loading error. A rename row cannot reach an overlay, so a target with `strip_onlyin=true` has the annotation
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


def self_check():
    ok = True
    def expect(c, msg):
        nonlocal ok
        if not c:
            ok = False; print("FAIL:", msg)
    check_direct_jump(expect)
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
    p = sub.add_parser("props"); p.add_argument("--target", required=True)
    p = sub.add_parser("isolation"); p.add_argument("--base", required=True)
    p = sub.add_parser("compare-jars"); p.add_argument("a"); p.add_argument("b")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    return {"prepare": cmd_prepare, "props": cmd_props, "isolation": cmd_isolation,
            "compare-jars": cmd_compare}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
