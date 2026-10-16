#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Integration test for tools/layer-sync.py with a stub fork-port.py (no Gradle): merge, per-target classification,
the stop for rework, and that a single-version fork is left untouched."""
import json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
LS = HERE / "layer-sync.py"
ok = True


def expect(c, msg):
    global ok
    if not c:
        ok = False
        print("FAIL:", msg)


STUB = r'''
import json, pathlib, sys
a = sys.argv[1:]
g = lambda k: a[a.index(k) + 1]
repo, t, frm, to = pathlib.Path(g("--repo")), g("--target"), g("--from-stage"), g("--to-stage")
sc = json.loads((repo / "scenario.json").read_text())[t]
out = repo / "build" / f"mc{t}" / "fork-port"; out.mkdir(parents=True, exist_ok=True)
jp = out / "fork-port.json"
summ = json.loads(jp.read_text()) if jp.exists() else {"stages": {}}
def stop(stage, left, code=10):
    (repo / ".multiversion/targets" / t / "RESIDUE.md").write_text("# Residue\n")
    summ["stop"] = {"stage": stage, "left": left}; jp.write_text(json.dumps(summ)); sys.exit(code)
summ.pop("stop", None)
if frm == "stage":
    if sc.get("nostage"): stop("stage", "Minecraft would not stage")
    summ["stages"]["measure"] = {"errors": sc["before"]}; jp.write_text(json.dumps(summ)); sys.exit(0)
if frm == "audit":
    if not sc.get("audit_ok", True): stop("audit", "2 mixin target(s) do not match vanilla", 13)
    sys.exit(0)
if frm == "loop":
    summ["stages"]["loop"] = {"errors": sc["after"]}; jp.write_text(json.dumps(summ))
    if sc["after"] != 0: stop("loop", f"{sc['after']} compile errors")
    sys.exit(0)
'''


def sh(*a, cwd=None, env=None):
    return subprocess.run(a, cwd=cwd, capture_output=True, text=True, encoding="utf-8", env={**os.environ, **(env or {})})


def git(d, *a):
    r = subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, (a, r.stderr)
    return r.stdout


def make_fork(root, with_layer):
    git(root, "init", "-q", "-b", "port"); git(root, "config", "user.email", "a@a"); git(root, "config", "user.name", "a")
    (root / "src/main/java/net/m").mkdir(parents=True)
    (root / "src/main/java/net/m/A.java").write_text("a\nb\nc\nd\n", encoding="utf-8")
    (root / "build.gradle").write_text("plugins {}\n", encoding="utf-8")
    (root / ".gitignore").write_text("build/\n", encoding="utf-8")
    git(root, "add", "-A"); git(root, "commit", "-qm", "port")
    if not with_layer:
        return
    git(root, "checkout", "-qb", "layer")
    mv = root / ".multiversion"
    for t in ("1.21.4", "1.21.5", "1.21.8"):
        (mv / "targets" / t).mkdir(parents=True)
        (mv / "targets" / t / "target.properties").write_text("x=1\n", encoding="utf-8")
    (mv / "targets/1.21.5/java/net/m").mkdir(parents=True)
    (mv / "targets/1.21.5/java/net/m/A.java").write_text("a\nb\nc\noverlay\n", encoding="utf-8")
    (mv / "base.properties").write_text("minecraft_version=1.21.1\n", encoding="utf-8")
    (mv / "tools").mkdir()
    # the real isolation rule, from the template
    (mv / "tools/layer.py").write_text((HERE.parent / "templates/fork-layer/tools/layer.py").read_text(encoding="utf-8"), encoding="utf-8")
    git(root, "add", "-A"); git(root, "commit", "-qm", "layer")


AUTHORS = {  # the authors' files before (index 0) and after (1) their change; OldName is renamed for every target
    "A": ("package net.m;\nclass A {\n    OldName x;\n    int one() { return 1; }\n    int k1;\n    int k2;\n    int k3;\n"
          "    int two() { return 2; }\n}\n", None),
    "B": ("class B {\n    int v() { return 2; }\n}\n", None),
    "C": ("class C {\n    int v() { return 2; }\n}\n", None),
    "D": ("class D {\n    int a;\n    int b;\n    int c;\n    int d;\n}\n", None),
    "E": ("class E {\n    OldName n;\n    Thing t;\n}\n", None),
}
NEW = {"A": AUTHORS["A"][0].replace("int two() { return 2; }", "Thing two() { return 22; }"), "B": AUTHORS["B"][0].replace("2", "4"),
       "C": AUTHORS["C"][0].replace("2", "3"),
       "D": AUTHORS["D"][0].replace("    int d;\n", "    int d;\n    public int extra() { return 0; }\n")}


def remerge_checks():
    """R6: each overlay of a file the authors changed is re-merged three ways; a clean merge rewrites it (an inherited
    overlay follows its parent's), one that becomes what it replaces is retired, a conflict (or a duplicate member)
    leaves the overlay as it was and its hunks in RESIDUE.md; and the overlay budget."""
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        stub = tmp / "stub-fork-port.py"; stub.write_text(STUB, encoding="utf-8")
        env = {"LAYER_SYNC_FORK_PORT": str(stub)}
        f = tmp / "fork"; f.mkdir()
        git(f, "init", "-q", "-b", "port"); git(f, "config", "user.email", "a@a"); git(f, "config", "user.name", "a")
        src = f / "src/main/java/net/m"; src.mkdir(parents=True)
        for k, (old, _) in AUTHORS.items():
            (src / f"{k}.java").write_text(old, encoding="utf-8")
        (f / ".gitignore").write_text("build/\n", encoding="utf-8")
        (f / "scenario.json").write_text(json.dumps({"1.21.4": {"before": 0}, "1.21.5": {"before": 0}}), encoding="utf-8")
        git(f, "add", "-A"); git(f, "commit", "-qm", "port")
        git(f, "checkout", "-qb", "layer")
        mv = f / ".multiversion"
        for tg, inh in (("1.21.4", None), ("1.21.5", "1.21.4")):
            (mv / "targets" / tg / "java/net/m").mkdir(parents=True)
            (mv / "targets" / tg / "target.properties").write_text(f"inherits={inh}\n" if inh else "x=1\n", encoding="utf-8")
        (mv / "targets/1.21.4/renames.tsv").write_text("OldName\tNewName\n", encoding="utf-8")
        (mv / "targets/1.21.5/renames.tsv").write_text("Thing\tThing2\n", encoding="utf-8")   # 1.21.5's own row
        (mv / "base.properties").write_text("minecraft_version=1.21.1\n", encoding="utf-8")
        (mv / "tools").mkdir()
        (mv / "tools/layer.py").write_text((HERE.parent / "templates/fork-layer/tools/layer.py").read_text(encoding="utf-8"), encoding="utf-8")
        ov4, ov5 = mv / "targets/1.21.4/java/net/m", mv / "targets/1.21.5/java/net/m"
        prepared = {k: v[0].replace("OldName", "NewName") for k, v in AUTHORS.items()}
        a4 = prepared["A"].replace("return 1;", "return 100;")          # 1.21.4's real change, far from the authors'
        (ov4 / "A.java").write_text(a4, encoding="utf-8")
        (ov5 / "A.java").write_text(a4.replace("int k2;", "long k2;"), encoding="utf-8")   # 1.21.5 builds on it
        (ov4 / "B.java").write_text(prepared["B"].replace("2", "3"), encoding="utf-8")     # the authors' line, changed otherwise
        (ov4 / "C.java").write_text(NEW["C"], encoding="utf-8")                           # already what the authors now do
        (ov4 / "D.java").write_text(prepared["D"].replace("class D {\n", "class D {\n    public int extra() { return 0; }\n"),
                                    encoding="utf-8")                                    # the loop's stand-in the authors now add
        (ov4 / "E.java").write_text(prepared["E"].replace("Thing", "Other"), encoding="utf-8")  # names only: should be rows
        (ov5 / "B.java").write_text(prepared["B"].replace("2", "3"), encoding="utf-8")    # redundant: what it inherits
        (ov4 / "Compat.java").write_text("// OVERLAY-ONLY: x\nclass Compat {}\n", encoding="utf-8")
        # 1.21.5's own copy of the inherited compat class: still replaces no authors' file, so overlay-only too
        (ov5 / "Compat.java").write_text("// OVERLAY-ONLY: x\nclass Compat { int v; }\n", encoding="utf-8")
        git(f, "add", "-A"); git(f, "commit", "-qm", "layer")
        # the overlay budget, before anything moves
        r = sh(sys.executable, str(LS), "overlays", "--repo", str(f), "--json", str(tmp / "ov.json"))
        o = {x["target"]: x for x in json.loads((tmp / "ov.json").read_text(encoding="utf-8"))}
        expect(r.returncode == 0 and (o["1.21.4"]["overlays"], o["1.21.4"]["overlay_only"], o["1.21.4"]["mechanical"],
                                      o["1.21.4"]["redundant"], o["1.21.4"]["real"]) == (5, 1, 1, 0, 4),
               f"budget: 1.21.4 counts its 5 whole-file overlays (1 mechanical), not its overlay-only class: {o['1.21.4']} {r.stdout}")
        expect((o["1.21.5"]["overlays"], o["1.21.5"]["redundant"], o["1.21.5"]["real"]) == (2, 1, 1)
               and "E.java" in r.stdout and "rename rows" in r.stdout and "OVER" not in r.stdout,
               f"budget: an overlay identical to the one it inherits is redundant; the mechanical one is named: {r.stdout}")
        expect(o["1.21.5"]["overlay_only"] == 1, f"an inherited compat class replaces nothing: overlay-only: {o['1.21.5']}")
        # one definition of a whole-file overlay: the budget's counted files are exactly fork-port.py's REVIEW.md rows
        import importlib.util
        spec = importlib.util.spec_from_file_location("fork_port_t", HERE / "fork-port.py")
        fp = importlib.util.module_from_spec(spec); spec.loader.exec_module(fp)
        for tg in ("1.21.4", "1.21.5"):
            counted = sorted(x["file"] for x in o[tg]["files"] if x["kind"] != "overlay-only")
            rows = sorted(x for k, x in fp.review_items(f, mv / "targets" / tg) if k == "overlay")
            expect(counted == rows, f"{tg}: overlay report and REVIEW.md rows agree: {counted} vs {rows}")
        (mv / "ci.properties").write_text("overlay_budget=4\n", encoding="utf-8")
        git(f, "add", "-A"); git(f, "commit", "-qm", "budget")
        r = sh(sys.executable, str(LS), "overlays", "--repo", str(f), "--target", "1.21.4")
        expect(r.returncode == 0 and "| 1.21.4 | 5 |" in r.stdout and "4 OVER" in r.stdout and "WARNING 1.21.4" in r.stdout
               and "1.21.5" not in r.stdout, f"budget: ci.properties overlay_budget, a warning above it: {r.stdout}")
        # the authors' change, merged into the layer as keep-current does
        git(f, "checkout", "-q", "port")
        for k, new in NEW.items():
            (src / f"{k}.java").write_text(new, encoding="utf-8")
        git(f, "commit", "-qam", "upstream change"); git(f, "checkout", "-q", "layer")
        r = sh(sys.executable, str(LS), "merge", "--repo", str(f), "--port-ref", "port")
        expect(r.returncode == 0 and "FAIL" not in r.stdout, f"merge: {r.stdout}{r.stderr}")
        snap = {p: p.read_bytes() for p in mv.rglob("*") if p.is_file()}
        r = sh(sys.executable, str(LS), "remerge", "--repo", str(f), "--old", "HEAD^1", "--dry-run", "--json", str(tmp / "dry.json"))
        dry = json.loads((tmp / "dry.json").read_text(encoding="utf-8"))
        expect(r.returncode == 10 and "dry run, nothing written" in r.stdout
               and {p: p.read_bytes() for p in mv.rglob("*") if p.is_file()} == snap,
               f"remerge --dry-run reports and writes nothing: {r.stdout}")
        expect((len(dry["1.21.4"]["rewritten"]), len(dry["1.21.4"]["retired"]), len(dry["1.21.4"]["conflicts"])) == (1, 1, 2)
               and len(dry["1.21.5"]["rewritten"]) == 1 and len(dry["1.21.5"]["conflicts"]) == 2, f"dry-run counts: {dry}")
        out_json, out_md = tmp / "o.json", tmp / "o.md"
        r = sh(sys.executable, str(LS), "run", "--repo", str(f), "--json", str(out_json), "--md", str(out_md), env=env)
        by = {x["target"]: x for x in json.loads(out_json.read_text(encoding="utf-8"))["targets"]}
        a4n = (ov4 / "A.java").read_text(encoding="utf-8")
        expect("return 100;" in a4n and "return 22;" in a4n and "NewName x;" in a4n and "OldName" not in a4n,
               f"clean merge: the overlay keeps its change and gains the authors' (renamed, as prepared): {a4n}")
        a5n = (ov5 / "A.java").read_text(encoding="utf-8")
        expect("long k2;" in a5n and "return 22;" in a5n and "return 100;" in a5n and "Thing two()" in a4n
               and "Thing2 two()" in a5n, f"an inherited overlay follows its parent's re-merge, in its own names: {a5n}")
        expect(not (ov4 / "C.java").exists(), "an overlay that merged into exactly what it replaces is retired (deleted)")
        expect((ov4 / "B.java").read_text(encoding="utf-8") == prepared["B"].replace("2", "3")
               and "extra()" in (ov4 / "D.java").read_text(encoding="utf-8")
               and (ov4 / "D.java").read_text(encoding="utf-8").count("extra()") == 1,
               "a conflict, and a clean merge into a duplicate member, leave the overlay as it was")
        markers = [p for p in f.rglob("*.java") if "<<<<<<<" in p.read_text(encoding="utf-8")]
        expect(not markers, f"no conflict marker reaches the tree: {markers}")
        res = (mv / "targets/1.21.4/RESIDUE.md").read_text(encoding="utf-8")
        expect("did not merge into (layer-sync)" in res and "<<<<<<< overlay" in res and "B.java" in res
               and "D.java" in res and "method extra()" in res, f"the conflicting hunks are in RESIDUE.md: {res}")
        expect(by["1.21.4"]["class"] == "rework" and by["1.21.4"]["residue"].endswith("1.21.4/RESIDUE.md")
               and "did not merge" in by["1.21.4"]["note"] and r.returncode == 10, f"a conflicted target is rework: {by['1.21.4']}")
        # 1.21.5's copy of B conflicts like 1.21.4's (it is the same overlay), and the D it inherits as is did not take
        # the change: 1.21.5 is rework too, not skipped (the #131 review, finding 2); its A merged (above)
        c5 = sorted(c["file"].rsplit("/", 1)[1] + ":" + c["file"].split("/")[2] for c in by["1.21.5"]["remerge"]["conflicts"])
        expect(by["1.21.5"]["class"] == "rework" and by["1.21.5"]["before"] == 0 and c5 == ["B.java:1.21.5", "D.java:1.21.4"]
               and "inherits an overlay" in (mv / "targets/1.21.5/RESIDUE.md").read_text(encoding="utf-8"),
               f"a target whose inherited overlays did not take the change is rework: {by['1.21.5']}")
        md = out_md.read_text(encoding="utf-8")
        expect("1 rewritten, 1 retired" in md and "2 did not merge" in md and "4 whole-file overlay(s)" in md,
               f"the table notes the re-merge and the budget: {md}")
        # a re-run changes nothing more and still reports what is left
        before = {p: p.read_bytes() for p in mv.rglob("*.java")}
        r = sh(sys.executable, str(LS), "remerge", "--repo", str(f), "--old", "HEAD^1", "--json", str(tmp / "again.json"))
        again = json.loads((tmp / "again.json").read_text(encoding="utf-8"))
        expect({p: p.read_bytes() for p in mv.rglob("*.java")} == before and not again["1.21.4"]["rewritten"]
               and len(again["1.21.4"]["conflicts"]) == 2 and r.returncode == 10, f"re-merge is idempotent: {again}")
        # the person resolves B and D by hand: the section leaves RESIDUE.md
        (ov4 / "B.java").write_text(NEW["B"].replace("4", "5"), encoding="utf-8")
        (ov4 / "D.java").write_text(NEW["D"], encoding="utf-8")
        r = sh(sys.executable, str(LS), "remerge", "--repo", str(f), "--old", "HEAD^1", "--targets", "1.21.4")
        expect(r.returncode == 0 and "every overlay merged" in r.stdout and "| 1.21.4 | 3 | 0 | 0 | 2 | 0 |" in r.stdout
               and (ov4 / "B.java").read_text(encoding="utf-8") == NEW["B"].replace("4", "5")
               and "layer-sync" not in ((mv / "targets/1.21.4/RESIDUE.md").read_text(encoding="utf-8")
                                        if (mv / "targets/1.21.4/RESIDUE.md").exists() else ""),
               f"resolved: exit 0, the section is gone: {r.stdout}")


def remerge_ledger_checks():
    """Finding 1 and 2 of the #131 review: a re-merge that leaves an overlay unmerged records it in
    .multiversion/targets/<t>/REMERGE.json ({"conflicts": [{overlay, digest (sha256 of the overlay's bytes), reason,
    since}]}) as well as RESIDUE.md; an inherited overlay is never silently skipped (its ancestor conflicted, or was not
    re-merged in this run, or was merged by hand later); an entry leaves the file when its overlay is edited or later
    merges cleanly, and the file goes when it is empty."""
    import hashlib
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        stub = tmp / "stub-fork-port.py"; stub.write_text(STUB, encoding="utf-8")
        env = {"LAYER_SYNC_FORK_PORT": str(stub)}
        f = tmp / "fork"; f.mkdir()
        git(f, "init", "-q", "-b", "port"); git(f, "config", "user.email", "a@a"); git(f, "config", "user.name", "a")
        src = f / "src/main/java/net/m"; src.mkdir(parents=True)
        body = lambda k, a=1, e=1, d="int d;": f"class {k} {{\n  int a = {a};\n  int b;\n  int c;\n  {d}\n  int e = {e};\n}}\n"
        for k in "PQRS":
            (src / f"{k}.java").write_text(body(k), encoding="utf-8")
        (f / ".gitignore").write_text("build/\n", encoding="utf-8")
        (f / "scenario.json").write_text(json.dumps({x: {"before": 0} for x in ("1.21.4", "1.21.5", "1.21.6")}), encoding="utf-8")
        git(f, "add", "-A"); git(f, "commit", "-qm", "port")
        git(f, "checkout", "-qb", "layer")
        mv = f / ".multiversion"
        for tg, inh in (("1.21.4", None), ("1.21.5", "1.21.4"), ("1.21.6", "1.21.5")):
            (mv / "targets" / tg / "java/net/m").mkdir(parents=True)
            (mv / "targets" / tg / "target.properties").write_text(f"inherits={inh}\n" if inh else "x=1\n", encoding="utf-8")
        (mv / "targets/1.21.4/renames.tsv").write_text("Unused\tUnused2\n", encoding="utf-8")
        (mv / "base.properties").write_text("minecraft_version=1.21.1\n", encoding="utf-8")
        (mv / "tools").mkdir()
        (mv / "tools/layer.py").write_text((HERE.parent / "templates/fork-layer/tools/layer.py").read_text(encoding="utf-8"), encoding="utf-8")
        ov4, ov5 = mv / "targets/1.21.4/java/net/m", mv / "targets/1.21.5/java/net/m"
        for k in "PQS":
            (ov4 / f"{k}.java").write_text(body(k, a=9), encoding="utf-8")      # 1.21.4 changes the line the authors change
        (ov4 / "R.java").write_text(body("R", e=7), encoding="utf-8")            # 1.21.4 changes another line: merges
        (ov5 / "P.java").write_text(body("P", e=5), encoding="utf-8")            # 1.21.5's own copy: merges by itself
        (ov5 / "R.java").write_text(body("R", e=7, d="long d;"), encoding="utf-8")
        git(f, "add", "-A"); git(f, "commit", "-qm", "layer")
        old = git(f, "rev-parse", "--short", "HEAD").strip()
        git(f, "checkout", "-q", "port")
        for k in "PQRS":
            (src / f"{k}.java").write_text(body(k, a=2), encoding="utf-8")
        git(f, "commit", "-qam", "upstream change"); git(f, "checkout", "-q", "layer")
        r = sh(sys.executable, str(LS), "merge", "--repo", str(f), "--port-ref", "port")
        expect(r.returncode == 0, f"merge: {r.stdout}{r.stderr}")
        rj = lambda tg: mv / "targets" / tg / "REMERGE.json"
        load = lambda tg: json.loads(rj(tg).read_text(encoding="utf-8")) if rj(tg).exists() else {"conflicts": []}
        sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
        q4 = ".multiversion/targets/1.21.4/java/net/m/Q.java"
        p4 = ".multiversion/targets/1.21.4/java/net/m/P.java"
        s4 = ".multiversion/targets/1.21.4/java/net/m/S.java"
        # 1. the child alone (--targets 1.21.5): its ancestor is not re-merged in this run
        r = sh(sys.executable, str(LS), "run", "--repo", str(f), "--targets", "1.21.5", "--json", str(tmp / "o.json"), env=env)
        by = {x["target"]: x for x in json.loads((tmp / "o.json").read_text(encoding="utf-8"))["targets"]}
        p5, r5 = (ov5 / "P.java").read_text(encoding="utf-8"), (ov5 / "R.java").read_text(encoding="utf-8")
        expect("int a = 2;" in p5 and "int e = 5;" in p5 and "int a = 2;" in r5 and "long d;" in r5 and "int e = 7;" in r5,
               f"--targets child: its own copies of inherited overlays still take the authors' change: {p5} {r5}")
        expect((ov4 / "P.java").read_text(encoding="utf-8") == body("P", a=9) and (ov4 / "R.java").read_text(encoding="utf-8")
               == body("R", e=7) and not rj("1.21.4").exists() and not rj("1.21.6").exists(), "--targets 1.21.5 writes nothing else")
        e5 = load("1.21.5")
        expect(rj("1.21.5").exists() and [c["overlay"] for c in e5["conflicts"]] == [q4, s4]
               and all("inherits an overlay that did not take the authors' change" in c["reason"] for c in e5["conflicts"])
               and e5["conflicts"][:1] and e5["conflicts"][0]["digest"] == sha(f / q4) and e5["conflicts"][0]["since"].startswith(old + ".."),
               f"--targets child: an overlay it inherits as is, unmerged in its ancestor, is recorded, not skipped: {e5}")
        expect(r.returncode == 10 and by["1.21.5"]["class"] == "rework", f"so the child is rework: {by.get('1.21.5')} {r.stdout}")
        res5 = (mv / "targets/1.21.5/RESIDUE.md").read_text(encoding="utf-8") if (mv / "targets/1.21.5/RESIDUE.md").exists() else ""
        expect("Q.java" in res5 and "inherits an overlay" in res5, f"and listed in its RESIDUE.md section: {res5}")
        git(f, "add", "-A"); git(f, "commit", "-qm", "1.21.5 re-merged", "--allow-empty")
        # 2. every target: the ancestor conflicts on P, Q, S; the child is flagged for Q, S (it inherits them as they are)
        r = sh(sys.executable, str(LS), "remerge", "--repo", str(f), "--old", "HEAD~1^1", "--json", str(tmp / "r.json"))
        e4, e5, e6 = load("1.21.4"), load("1.21.5"), load("1.21.6")
        expect(r.returncode == 10 and [c["overlay"] for c in e4["conflicts"]] == [p4, q4, s4]
               and all(c["digest"] == sha(f / c["overlay"]) and "conflicting hunk" in c["reason"] for c in e4["conflicts"]),
               f"a conflict is a REMERGE.json entry with the overlay's digest: {e4} {r.stdout}")
        expect("int a = 2;" in (ov4 / "R.java").read_text(encoding="utf-8"), "the ancestor's clean overlay merged")
        expect([c["overlay"] for c in e5["conflicts"]] == [q4, s4] and [c["overlay"] for c in e6["conflicts"]] == [q4, s4],
               f"an ancestor's conflict flags every target that inherits that overlay as is, not the one with its own merged "
               f"copy: {e5} {e6}")
        # 3. a person merges 1.21.4's Q by hand: the re-run clears it there AND in every target that inherits it
        (ov4 / "Q.java").write_text(body("Q", a=2, e=9), encoding="utf-8")
        r = sh(sys.executable, str(LS), "remerge", "--repo", str(f), "--old", "HEAD~1^1")
        e4, e5, e6 = load("1.21.4"), load("1.21.5"), load("1.21.6")
        expect(r.returncode == 10 and [c["overlay"] for c in e4["conflicts"]] == [p4, s4] and "| 1.21.4 | 4 | 0 | 0 | 1 | 2 |" in r.stdout,
               f"an overlay edited since it was recorded is a person's resolution: its entry goes: {e4} {r.stdout}")
        expect([c["overlay"] for c in e5["conflicts"]] == [s4] and [c["overlay"] for c in e6["conflicts"]] == [s4],
               f"the hand-merged ancestor: the targets that inherit it are handled on the re-run: {e5} {e6}")
        # 4. the person resolves P too; S stays
        (ov4 / "P.java").write_text(body("P", a=2, e=9), encoding="utf-8")
        r = sh(sys.executable, str(LS), "remerge", "--repo", str(f), "--old", "HEAD~1^1")
        expect(r.returncode == 10 and [c["overlay"] for c in load("1.21.4")["conflicts"]] == [s4], f"P resolved, S left: {load('1.21.4')}")
        # 5. the authors move again and take back the line S conflicted on: S now merges against the authors' file the
        # entry was recorded against (its `since`), so the entry goes, and an empty REMERGE.json is deleted
        git(f, "add", "-A"); git(f, "commit", "-qm", "resolved by hand", "--allow-empty")
        git(f, "checkout", "-q", "port")
        (src / "S.java").write_text(body("S", a=1, d="int d = 4;"), encoding="utf-8")
        git(f, "commit", "-qam", "upstream change 2"); git(f, "checkout", "-q", "layer")
        r = sh(sys.executable, str(LS), "merge", "--repo", str(f), "--port-ref", "port")
        expect(r.returncode == 0, f"second merge: {r.stdout}{r.stderr}")
        r = sh(sys.executable, str(LS), "remerge", "--repo", str(f), "--old", "HEAD^1")
        s4n = (ov4 / "S.java").read_text(encoding="utf-8")
        expect(r.returncode == 0 and "int a = 9;" in s4n and "int d = 4;" in s4n and not rj("1.21.4").exists()
               and not rj("1.21.5").exists() and not rj("1.21.6").exists(),
               f"a later clean merge removes the entry; an empty REMERGE.json is deleted: {r.stdout} {s4n}")
        expect(not [p for p in f.rglob("*.java") if "<<<<<<<" in p.read_text(encoding="utf-8")], "no conflict marker in the tree")


def main():
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        stub = tmp / "stub-fork-port.py"; stub.write_text(STUB, encoding="utf-8")
        env = {"LAYER_SYNC_FORK_PORT": str(stub)}
        # single-version fork: every command changes nothing and says so
        s = tmp / "single"; s.mkdir(); make_fork(s, False)
        head = git(s, "rev-parse", "HEAD"); before = git(s, "status", "--porcelain=v1", "--ignored")
        for cmd in (["detect"], ["merge", "--port-ref", "port"], ["run"], ["overlays"], ["remerge", "--old", "HEAD"]):
            r = sh(sys.executable, str(LS), cmd[0], "--repo", str(s), *cmd[1:], env=env)
            expect(r.returncode == 0, f"single-version {cmd[0]} exits 0: {r.stdout}{r.stderr}")
        expect("layer=false" in sh(sys.executable, str(LS), "detect", "--repo", str(s)).stdout, "detect says layer=false")
        r = sh(sys.executable, str(LS), "detect", "--repo", str(s), "--ref", "port")
        expect(r.returncode == 0 and r.stdout.strip() == "layer=false", f"single-version detect --ref: layer=false, exit 0: {r.stdout}")
        for cmd in (["run"], ["overlays"], ["remerge", "--old", "HEAD"]):
            r = sh(sys.executable, str(LS), cmd[0], "--repo", str(s), *cmd[1:], env=env)
            # the tool prints the checkout resolved: a Windows temp dir is a short 8.3 path (RUNNER~1) until resolve()
            one = {f"layer-sync: {x} is not a multi-version fork (no .multiversion/ with a target): nothing to do, "
                   "nothing changed\n" for x in (s, s.resolve())}
            expect(r.stdout in one and r.returncode == 0, f"single-version {cmd[0]}: the one line, exit 0: {r.stdout!r}")
        expect(git(s, "rev-parse", "HEAD") == head and git(s, "status", "--porcelain=v1", "--ignored") == before
               and not (s / "build").exists(), "single-version fork: no commit, no file, no build dir")
        # layer fork: new upstream commit on the port branch, merged into the layer
        f = tmp / "layer"; f.mkdir(); make_fork(f, True)
        # detect from the PORT branch of a layer fork must not read as single-version (the nightly-run miss)
        git(f, "checkout", "-q", "port")
        r = sh(sys.executable, str(LS), "detect", "--repo", str(f))
        expect(r.returncode == 3 and "layer=false" in r.stdout and "but layer carries .multiversion" in r.stdout,
               f"detect on the port branch names the layer branch and exits 3: {r.stdout}{r.stderr}")
        c = tmp / "clone"; sh("git", "clone", "-q", "--no-checkout", str(f), str(c)); git(c, "checkout", "-q", "port"); git(c, "remote", "set-head", "origin", "layer")
        r = sh(sys.executable, str(LS), "detect", "--repo", str(c))
        expect(r.returncode == 3 and "origin/layer carries" in r.stdout and "origin," not in r.stdout
               and "origin carries" not in r.stdout, f"a clone's origin/HEAD alias is not listed as a branch: {r.stdout}")
        r = sh(sys.executable, str(LS), "detect", "--repo", str(f), "--ref", "layer")
        expect(r.returncode == 0 and "layer=true targets=1.21.4,1.21.5,1.21.8" in r.stdout
               and git(f, "rev-parse", "--abbrev-ref", "HEAD").strip() == "port", f"detect --ref reads the layer without checkout: {r.stdout}")
        expect(sh(sys.executable, str(LS), "detect", "--repo", str(f), "--ref", "nope").returncode == 2, "detect --ref unknown exits 2")
        r = sh(sys.executable, str(LS), "detect", "--repo", str(f), "--ref", "layer", "--port-ref", "port")
        expect(r.returncode == 0 and "layer_behind=0" in r.stdout, f"detect: a layer cut from the port branch owes nothing: {r.stdout}")
        git(f, "checkout", "-q", "layer")
        git(f, "checkout", "-q", "port")
        (f / "src/main/java/net/m/A.java").write_text("a2\nb\nc\nd\n", encoding="utf-8"); git(f, "commit", "-qam", "upstream change")
        r = sh(sys.executable, str(LS), "detect", "--repo", str(f), "--ref", "layer", "--port-ref", "port")
        expect(r.returncode == 0 and "layer_behind=1" in r.stdout, f"detect counts the port commit the layer has not merged: {r.stdout}")
        git(f, "checkout", "-q", "layer")
        r = sh(sys.executable, str(LS), "merge", "--repo", str(f), "--port-ref", "port", "--layer-branch", "layer")
        expect(r.returncode == 0 and len(git(f, "rev-list", "--merges", "-1", "HEAD").split()) == 1, f"merge made a merge commit: {r.stdout}{r.stderr}")
        expect("isolation" in r.stdout and "FAIL" not in r.stdout, f"isolation ran and passed: {r.stdout}")
        r = sh(sys.executable, str(LS), "merge", "--repo", str(f), "--port-ref", "port")
        expect("nothing to merge" in r.stdout, "second merge is a no-op")
        # scenarios
        def scen(d):
            (f / "scenario.json").write_text(json.dumps(d), encoding="utf-8")
            git(f, "add", "-A"); git(f, "commit", "-qm", "scenario", "--allow-empty")
        scen({"1.21.4": {"before": 0}, "1.21.5": {"before": 9, "after": 0}, "1.21.8": {"before": 14, "after": 4}})
        out_json, out_md = tmp / "o.json", tmp / "o.md"
        # the scenario commit sits on top of the merge: compare against the merge's first parent explicitly
        r = sh(sys.executable, str(LS), "run", "--repo", str(f), "--changed-from", "HEAD~1^1", "--json", str(out_json), "--md", str(out_md), env=env)
        data = json.loads(out_json.read_text(encoding="utf-8"))
        by = {x["target"]: x for x in data["targets"]}
        expect(by["1.21.4"]["class"] == "untouched" and by["1.21.4"]["before"] == 0, f"untouched: {by['1.21.4']}")
        expect(by["1.21.5"]["class"] == "touch-up" and (by["1.21.5"]["before"], by["1.21.5"]["after"]) == (9, 0), f"touch-up: {by['1.21.5']}")
        expect(by["1.21.5"]["shadowed"] == ["src/main/java/net/m/A.java"], f"overlay shadowing upstream's change: {by['1.21.5']}")
        expect((f / ".multiversion/targets/1.21.5/java/net/m/A.java").read_text(encoding="utf-8") == "a2\nb\nc\noverlay\n",
               "the overlay carries the authors' change after run (three-way re-merge)")
        expect(by["1.21.8"]["class"] == "rework" and by["1.21.8"]["residue"].endswith("1.21.8/RESIDUE.md")
               and (by["1.21.8"]["before"], by["1.21.8"]["after"]) == (14, 4), f"rework with residue: {by['1.21.8']}")
        expect(r.returncode == 10 and "STOPPED for a person" in r.stdout and "RESIDUE.md" in r.stdout, f"rework stops (exit 10): {r.returncode} {r.stdout}")
        expect("| 1.21.8 | 14 | 4 | rework |" in out_md.read_text(encoding="utf-8"), "table in the md")
        # an audit failure at 0 errors, and a stage that will not run, are rework too
        scen({"1.21.4": {"before": 0, "audit_ok": False}, "1.21.5": {"before": 0}, "1.21.8": {"nostage": True}})
        r = sh(sys.executable, str(LS), "run", "--repo", str(f), "--json", str(out_json), env=env)
        by = {x["target"]: x for x in json.loads(out_json.read_text(encoding="utf-8"))["targets"]}
        expect(by["1.21.4"]["class"] == "rework" and "mixin target" in by["1.21.4"]["note"], f"audit failure: {by['1.21.4']}")
        expect(by["1.21.5"]["class"] == "untouched", "clean one still untouched")
        expect(by["1.21.8"]["class"] == "rework" and by["1.21.8"]["before"] is None and r.returncode == 10, f"unrunnable stage: {by['1.21.8']}")
        # all clear -> exit 0
        scen({"1.21.4": {"before": 0}, "1.21.5": {"before": 3, "after": 0}, "1.21.8": {"before": 0}})
        for x in (f / ".multiversion/targets").glob("*/RESIDUE.md"):
            x.unlink()
        r = sh(sys.executable, str(LS), "run", "--repo", str(f), "--targets", "1.21.5,1.21.8", "--json", str(out_json), env=env)
        expect(r.returncode == 0 and "STOPPED" not in r.stdout and len(json.loads(out_json.read_text(encoding="utf-8"))["targets"]) == 2, f"--targets subset, all clear: {r.stdout}")
        r = sh(sys.executable, str(LS), "run", "--repo", str(f), "--targets", "9.9", env=env)
        expect(r.returncode == 2, "unknown target refused")
        # ledger
        led = tmp / "led.json"; led.write_text(json.dumps({"issue": "o/r#1", "title": "t", "modes": []}), encoding="utf-8")
        r = sh(sys.executable, str(LS), "ledger", "--ledger", str(led), "--json", str(out_json), "--column", "keep-current/x")
        l = json.loads(led.read_text(encoding="utf-8"))
        expect(r.returncode == 0 and l["layer"]["column"] == "keep-current/x" and len(l["layer"]["targets"]) == 2, "ledger written")
        # a dirty tree refuses the merge
        git(f, "checkout", "-q", "port"); (f / "src/main/java/net/m/A.java").write_text("a3\n", encoding="utf-8"); git(f, "commit", "-qam", "more")
        git(f, "checkout", "-q", "layer"); (f / "dirty.txt").write_text("x", encoding="utf-8")
        r = sh(sys.executable, str(LS), "merge", "--repo", str(f), "--port-ref", "port")
        expect(r.returncode == 2, "dirty tree refused")
        (f / "dirty.txt").unlink()
        # a conflict is aborted, nothing changes
        (f / "src/main/java/net/m/A.java").write_text("conflict\n", encoding="utf-8"); git(f, "commit", "-qam", "layer edits src")
        h = git(f, "rev-parse", "HEAD")
        r = sh(sys.executable, str(LS), "merge", "--repo", str(f), "--port-ref", "port")
        expect(r.returncode == 1 and "conflicts" in r.stdout and git(f, "rev-parse", "HEAD") == h
               and not git(f, "status", "--porcelain").strip(), f"conflict aborted cleanly: {r.stdout}")
    remerge_checks()
    remerge_ledger_checks()
    print("test-layer-sync:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
