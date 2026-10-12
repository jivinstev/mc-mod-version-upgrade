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
    (root / "src/main/java/net/m/A.java").write_text("a\n", encoding="utf-8")
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
    (mv / "targets/1.21.5/java/net/m/A.java").write_text("overlay\n", encoding="utf-8")
    (mv / "base.properties").write_text("minecraft_version=1.21.1\n", encoding="utf-8")
    (mv / "tools").mkdir()
    # the real isolation rule, from the template
    (mv / "tools/layer.py").write_text((HERE.parent / "templates/fork-layer/tools/layer.py").read_text(encoding="utf-8"), encoding="utf-8")
    git(root, "add", "-A"); git(root, "commit", "-qm", "layer")


def main():
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        stub = tmp / "stub-fork-port.py"; stub.write_text(STUB, encoding="utf-8")
        env = {"LAYER_SYNC_FORK_PORT": str(stub)}
        # single-version fork: every command changes nothing and says so
        s = tmp / "single"; s.mkdir(); make_fork(s, False)
        head = git(s, "rev-parse", "HEAD"); before = git(s, "status", "--porcelain=v1", "--ignored")
        for cmd in (["detect"], ["merge", "--port-ref", "port"], ["run"]):
            r = sh(sys.executable, str(LS), cmd[0], "--repo", str(s), *cmd[1:], env=env)
            expect(r.returncode == 0, f"single-version {cmd[0]} exits 0: {r.stdout}{r.stderr}")
        expect("layer=false" in sh(sys.executable, str(LS), "detect", "--repo", str(s)).stdout, "detect says layer=false")
        r = sh(sys.executable, str(LS), "detect", "--repo", str(s), "--ref", "port")
        expect(r.returncode == 0 and r.stdout.strip() == "layer=false", f"single-version detect --ref: layer=false, exit 0: {r.stdout}")
        expect("not a multi-version fork" in sh(sys.executable, str(LS), "run", "--repo", str(s), env=env).stdout, "run says nothing to do")
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
        (f / "src/main/java/net/m/A.java").write_text("a2\n", encoding="utf-8"); git(f, "commit", "-qam", "upstream change")
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
    print("test-layer-sync:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
