#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for tools/port-publish.py that need a whole fork's worth of fakes (the network and git are faked):

  1. SINGLE-VERSION: a single-version publish (dry and real) prints exactly the bytes, and makes exactly the API calls,
     recorded in tools/golden/port-publish-single-version.txt -- captured from the code BEFORE the multi-version
     layer support existed. Multi-version publishing is opt-in (a "layer" config section + a .multiversion/ tag), so
     this is the guarantee that a fork without it publishes as it always did. A fork that has the layer in its tree
     but no "layer" section is checked too.
  2. LAYER: one file per green target, refusals, per-target dependency checks, idempotence, and preview targets
     (`preview=true` in a target's target.properties: listed as "preview, not published", never uploaded, never an error).

    python3 tools/test-port-publish.py            # exit 0 = all pass
    python3 tools/test-port-publish.py --regen    # rewrite the golden file (only after reading the diff)
"""
import contextlib, importlib.util, io, json, os, pathlib, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
GOLDEN = HERE / "golden" / "port-publish-single-version.txt"
ok = True


def expect(c, msg):
    global ok
    if not c:
        ok = False
        print("FAIL:", msg)


def load(name="port_publish_under_test"):
    spec = importlib.util.spec_from_file_location(name, HERE / "port-publish.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


class R:
    def __init__(self, rc, out=""):
        self.returncode, self.stdout = rc, out


def make_config(d, extra=None, deps=None):
    (d / ".github/workflows").mkdir(parents=True, exist_ok=True)
    cfg = {"schema": 1, "permission": "https://example.invalid/agreed", "auto": True, "modid": "m", "original_name": "M",
           "ledger_issues": [], "differences": ["A neutral difference."],
           "dependencies": deps if deps is not None else
           [{"port": "o/lib", "type": "required"}, {"modrinth": "geckolib", "curseforge": "geckolib", "type": "optional"}],
           "modrinth": {"slug": "m-neoforge", "asset": "_mr.jar"}, "curseforge": {"project_id": 7, "slug": "m-neoforge", "asset": "_cf.jar"}}
    cfg.update(extra or {})
    (d / ".github/port-publish.json").write_text(json.dumps(cfg), encoding="utf-8")
    (d / ".github/workflows/port-ci.yml").write_text("x https://github.com/o/lib/releases/download/lib-1-mc1.21.1/lib.jar",
                                                     encoding="utf-8")


def fakes(pp, release, git, lib_body="<!-- port-publish:modrinth:A1 --> <!-- port-publish:curseforge:9 -->",
          runs=(), mc_ids=None):
    calls, uploads = [], []
    mc_ids = mc_ids or {"1.21.1": 10, "1.21.4": 12, "1.21.11": 13}

    def gh(method, path, data=None, raw=False, accept=None):
        calls.append(("gh", method, path, json.dumps(data, sort_keys=True) if data else None))
        if "/check-runs" in path:
            return {"check_runs": list(runs)}
        if "/releases/tags/lib-1" in path:
            return {"body": lib_body}
        if "/releases/tags/" in path:
            return release
        if path.split("?")[0].endswith("/contents/.github/port-publish.json"):
            return json.dumps({"modrinth": {"slug": "lib-nf"}, "curseforge": {"slug": "lib-nf"}}).encode()
        return {}

    def modrinth(method, path, data=None, headers=None):
        calls.append(("modrinth", method, path))
        if path.endswith("/version") and method == "GET":
            return [{"id": "LV41", "project_id": "Plib-nf", "version_number": "1+1.21.4"}] if path.startswith("/project/lib-nf") else []
        if path.startswith("/project/"):
            return {"id": "P" + path.split("/")[2], "slug": path.split("/")[2]}
        uploads.append(("modrinth", json.loads(data)))
        return {"id": f"V{len(uploads)}", "version_number": json.loads(data)["fields"]["data"]["version_number"]}

    def curseforge(method, path, data=None, headers=None):
        calls.append(("curseforge", method, path))
        if path == "/game/version-types":
            return [{"id": 1, "slug": "minecraft-1-21"}, {"id": 2, "slug": "modloader"}, {"id": 3, "slug": "environment"}]
        if path == "/game/versions":
            return [{"id": i, "name": n, "gameVersionTypeID": 1} for n, i in mc_ids.items()] + \
                   [{"id": 99, "name": "NeoForge", "gameVersionTypeID": 2},
                    {"id": 97, "name": "Client", "gameVersionTypeID": 3}, {"id": 98, "name": "Server", "gameVersionTypeID": 3}]
        uploads.append(("curseforge", json.loads(data)))
        return {"id": 700 + len(uploads)}

    def multipart(fields, files):
        return json.dumps({"fields": {k: (json.loads(v) if k in ("data", "metadata") else v) for k, v in fields.items()},
                           "file": [v[0] for v in files.values()]}).encode(), {}

    pp.gh, pp.git, pp.modrinth, pp.curseforge, pp.multipart = gh, git, modrinth, curseforge, multipart
    pp.download = lambda asset: b"JAR"
    return calls, uploads


def single_version(path):
    pp = load("ppx")
    out = []
    for dry in (True, False):
        with tempfile.TemporaryDirectory() as t:
            d = pathlib.Path(t)
            make_config(d)
            os.environ.update({"GITHUB_REPOSITORY": "o/m", "MODRINTH_TOKEN": "t", "CURSEFORGE_TOKEN": "t"})
            rel = {"id": 5, "draft": False, "prerelease": False, "author": {"login": "github-actions[bot]"},
                   "body": "notes\n\nPlayed by @someone on 2026-10-12.",
                   "assets": [{"name": "m-2_mr.jar", "url": "u1"}, {"name": "m-2_cf.jar", "url": "u2"}]}
            calls = []

            def gh(method, path, data=None, raw=False, accept=None):
                calls.append(("gh", method, path, json.dumps(data, sort_keys=True) if data else None))
                if "/releases/tags/lib-1" in path:
                    return {"body": "<!-- port-publish:modrinth:A1 --> <!-- port-publish:curseforge:9 -->"}
                if "/releases/tags/" in path:
                    return rel
                if path.split("?")[0].endswith("/contents/.github/port-publish.json"):
                    return json.dumps({"modrinth": {"slug": "lib-nf"}, "curseforge": {"slug": "lib-nf"}}).encode()
                return {}

            def modrinth(method, p, data=None, headers=None):
                calls.append(("modrinth", method, p))
                if p.endswith("/version") and method == "GET":
                    return []
                if p.startswith("/project/"):
                    return {"id": "P" + p.split("/")[2], "slug": p.split("/")[2]}
                return {"id": "V1", "version_number": "2+1.21.1"}

            def curseforge(method, p, data=None, headers=None):
                calls.append(("curseforge", method, p))
                if p == "/game/version-types":
                    return [{"id": 1, "slug": "minecraft-1-21"}, {"id": 2, "slug": "modloader"}, {"id": 3, "slug": "environment"}]
                if p == "/game/versions":
                    return [{"id": 10, "name": "1.21.1", "gameVersionTypeID": 1}, {"id": 11, "name": "NeoForge", "gameVersionTypeID": 2},
                            {"id": 12, "name": "Client", "gameVersionTypeID": 3}, {"id": 13, "name": "Server", "gameVersionTypeID": 3}]
                return {"id": 77}
            pp.gh, pp.git, pp.modrinth, pp.curseforge = gh, (lambda repo, *a: R(0, "neoforge-1.21.1\n") if a[:2] == ("rev-parse", "--abbrev-ref") else R(0)), modrinth, curseforge
            pp.download = lambda asset: b"JAR"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = pp.publish(d, "m-2-mc1.21.1", dry=dry, manual=False)
            out.append(f"{'DRY' if dry else 'REAL'} {rc}\n{buf.getvalue()}\n{json.dumps(calls, indent=0)}\n")
    return "".join(out)


# ---------------------------------------------------------------- the layer fixture

GATES_41 = ("Gates for `m` at `abc1234` for Minecraft 1.21.4 (full environment) -- run by CI:\n\n| check | result | detail |\n"
            "|---|---|---|\n| build | PASS | x |\n| Gate B | PASS | 5 |\n")


def layer_git(changed=("README.md",), gates41=GATES_41, with_layer=True, targets=("1.21.11", "1.21.4"), pins="", preview=()):
    files = {"base.properties": "minecraft_version=1.21.1\nmv_package=net.m.mv\n"}

    def git(repo, *a):
        a = list(a)
        if a[0] == "show":
            ref = a[1]
            if ".multiversion/base.properties" in ref:
                return R(0, files["base.properties"]) if with_layer else R(128)
            if ref.endswith("/target.properties"):
                t = ref.split("/")[-2]
                return R(0, (f"inherits=1.21.4\n" if t == "1.21.11" else "x=1\n") + (pins if t == "1.21.4" else "")
                         + ("preview=true\n" if t in preview else ""))
            if ref.endswith("GATES.md"):
                return R(0, gates41) if "1.21.4" in ref and gates41 is not None else R(128)
        if a[0] == "ls-tree":
            return R(0, "\n".join(targets) + "\n")
        if a[:2] == ["rev-parse", "--abbrev-ref"]:      # CI checks out the port branch
            return R(0, "neoforge-1.21.1\n")
        if a[0] == "rev-parse":
            return R(0, "c0ffee0\n")
        if a[0] == "cat-file":
            return R(0)
        if a[0] == "diff":
            return R(0, "\n".join(changed) + "\n")
        if a[0] == "merge-base":
            return R(0)
        return R(1)
    return git


def ci(*names_ok):
    return [{"id": i, "name": n, "conclusion": "success" if good else "failure", "status": "completed"}
            for i, (n, good) in enumerate(names_ok, 1)]


GREEN = ci(("gates (full)", True), ("gates (minimal)", True), ("layer gates (newest target 1.21.11)", True),
           ("layer compile (1.21.4)", True))


def release(names):
    return {"id": 5, "draft": False, "prerelease": False, "author": {"login": "github-actions[bot]"},
            "body": "notes\n\nPlayed by @someone on 2026-10-12.", "assets": [{"name": n, "url": "u"} for n in names]}


ALL_JARS = ["m-2_mr.jar", "m-2_cf.jar", "m-2_mr-mc1.21.4.jar", "m-2_cf-mc1.21.4.jar",
            "m-2_mr-mc1.21.11.jar", "m-2_cf-mc1.21.11.jar", "m-2_base.jar"]


def layer_run(extra, rel_names=ALL_JARS, runs=GREEN, dry=False, deps=None, lib_body=None, git=None, tag="m-2-mc1.21.1",
              rel_body=None):
    pp = load("ppl")
    with tempfile.TemporaryDirectory() as t:
        d = pathlib.Path(t)
        make_config(d, extra, deps)
        os.environ.update({"GITHUB_REPOSITORY": "o/m", "MODRINTH_TOKEN": "t", "CURSEFORGE_TOKEN": "t"})
        rel = release(rel_names)
        if rel_body:
            rel["body"] += rel_body
        kw = {"lib_body": lib_body} if lib_body is not None else {}
        calls, uploads = fakes(pp, rel, git or layer_git(), runs=runs, **kw)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = pp.publish(d, tag, dry=dry, manual=False)
        return rc, buf.getvalue(), calls, uploads


LIB_ALL = ("<!-- port-publish:modrinth:A1@1.21.1 --> <!-- port-publish:curseforge:9@1.21.1 --> "
           "<!-- port-publish:modrinth:A2@1.21.4 --> <!-- port-publish:curseforge:8@1.21.4 --> "
           "<!-- port-publish:modrinth:A3@1.21.11 --> <!-- port-publish:curseforge:7@1.21.11 -->")


def main():
    global ok
    if "--regen" in sys.argv:
        GOLDEN.write_text(single_version(GOLDEN), encoding="utf-8")
        print("wrote", GOLDEN)
        return 0
    # 1. single-version: byte-for-byte what it printed before the layer support existed
    got = single_version(GOLDEN)
    want = GOLDEN.read_text(encoding="utf-8")
    expect(got == want, "single-version publish output/API calls differ from tools/golden/port-publish-single-version.txt")
    # ...and a fork that HAS the layer in its tree but no "layer" section publishes the same way
    pp = load("ppnolayer")
    with tempfile.TemporaryDirectory() as t:
        d = pathlib.Path(t)
        make_config(d)
        os.environ.update({"GITHUB_REPOSITORY": "o/m", "MODRINTH_TOKEN": "t", "CURSEFORGE_TOKEN": "t"})
        calls, _ = fakes(pp, release(["m-2_mr.jar", "m-2_cf.jar", "m-2_mr-mc1.21.4.jar"]), layer_git(), runs=GREEN)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = pp.publish(d, "m-2-mc1.21.1", dry=True, manual=False)
        expect(rc == 0 and "game_versions" in buf.getvalue() and '"1.21.1"' in buf.getvalue() and "1.21.4" not in buf.getvalue()
               and not any("check-runs" in c[2] for c in calls), "a layer tree without a \"layer\" config section publishes single-version")

    # 2. layer: 1.21.4 is compile-only in CI and not listed -> the whole publish is refused, nothing uploaded
    rc, out, calls, up = layer_run({"layer": {"branch": "neoforge-1.21.x"}}, deps=[])
    expect(rc == 1 and "target 1.21.4: gates are not green at the released commit" in out and not up
           and "1.21.11" not in out.replace("layer gates", ""), f"non-green middle target refused, nothing uploaded: {out}")
    # listed as released with a current GATES.md (only docs changed since): all three targets, one file each
    rc, out, calls, up = layer_run({"layer": {"branch": "neoforge-1.21.x", "released": ["1.21.4"]}}, deps=[])
    mr = [u[1] for u in up if u[0] == "modrinth"]
    cf = [u[1] for u in up if u[0] == "curseforge"]
    expect(rc == 0 and len(mr) == 3 and len(cf) == 3, f"a file per green target on each platform: rc={rc} {out}")
    expect(sorted(m["fields"]["data"]["game_versions"][0] for m in mr) == ["1.21.1", "1.21.11", "1.21.4"], "game_versions per target")
    expect(sorted(m["file"][0] for m in mr) == ["m-2_mr-mc1.21.11.jar", "m-2_mr-mc1.21.4.jar", "m-2_mr.jar"],
           f"the target's own jar per Modrinth version: {[m['file'] for m in mr]}")
    expect(sorted(m["fields"]["data"]["version_number"] for m in mr) == ["2+1.21.1", "2+1.21.11", "2+1.21.4"], "distinct version numbers")
    expect(sorted(c["file"][0] for c in cf) == ["m-2_cf-mc1.21.11.jar", "m-2_cf-mc1.21.4.jar", "m-2_cf.jar"], "CurseForge file per target")
    expect(sorted(c["fields"]["metadata"]["gameVersions"][0] for c in cf) == [10, 12, 13], "CurseForge game version id per target")
    patch = [c for c in calls if c[1] == "PATCH"]
    expect(len(patch) == 1 and patch[0][3].count("<!-- port-publish:") == 6 and "@1.21.4 -->" in patch[0][3],
           "the release notes carry one marker per platform per target")
    # idempotence: a body that already carries those markers publishes nothing more
    marks = patch[0][3]
    body = json.loads(marks)["body"]
    rc, out, calls, up = layer_run({"layer": {"branch": "neoforge-1.21.x", "released": ["1.21.4"]}}, deps=[],
                                   rel_body="\n\n" + body.split("Played by")[1].split("\n\n", 1)[1])
    expect(rc == 0 and not up and out.count("already published") == 6, f"re-run is idempotent: {out}")
    # stale GATES.md: source changed since its gates ran -> refused
    rc, out, _, up = layer_run({"layer": {"branch": "neoforge-1.21.x", "released": ["1.21.4"]}}, deps=[],
                               git=layer_git(changed=["src/main/java/net/m/X.java"]))
    expect(rc == 1 and "changed since its gates ran" in out and not up, f"stale gates refused: {out}")
    # ...but another target's own directory changing does not invalidate 1.21.4
    rc, out, _, up = layer_run({"layer": {"branch": "neoforge-1.21.x", "released": ["1.21.4"]}}, deps=[],
                               git=layer_git(changed=[".multiversion/targets/1.21.8/renames.tsv", ".multiversion/targets/1.21.4/GATES.md"]))
    expect(rc == 0 and len(up) == 6, f"unrelated target dir does not stale it: {out}")
    rc, out, _, up = layer_run({"layer": {"branch": "neoforge-1.21.x", "released": ["1.21.4"]}}, deps=[],
                               git=layer_git(changed=[".multiversion/targets/1.21.4/renames.tsv"]))
    expect(rc == 1 and not up, "the target's own rows changed since its gates: refused")
    # a failing newest-target gate refuses it; a red bookend refuses the base
    bad = ci(("gates (full)", True), ("gates (minimal)", True), ("layer gates (newest target 1.21.11)", False))
    rc, out, _, up = layer_run({"layer": {"branch": "neoforge-1.21.x", "released": ["1.21.4"]}}, deps=[], runs=bad)
    expect(rc == 1 and "target 1.21.11" in out and not up, f"red newest-target gates refuse it: {out}")
    bad = ci(("gates (full)", True), ("gates (minimal)", False), ("layer gates (newest target 1.21.11)", True))
    rc, out, _, up = layer_run({"layer": {"branch": "neoforge-1.21.x", "released": ["1.21.4"]}}, deps=[], runs=bad)
    expect(rc == 1 and "target 1.21.1:" in out and not up, f"red bookend gates refuse the base: {out}")
    # explicit layer.targets: only those, even when the release holds more; a name the layer lacks is refused
    rc, out, _, up = layer_run({"layer": {"branch": "neoforge-1.21.x", "targets": ["1.21.1", "1.21.11"]}}, deps=[])
    expect(rc == 0 and len(up) == 4 and {u[1]["fields"]["data"]["game_versions"][0] for u in up if u[0] == "modrinth"} == {"1.21.1", "1.21.11"},
           f"layer.targets limits the publish: {out}")
    rc, out, _, up = layer_run({"layer": {"branch": "neoforge-1.21.x", "targets": ["1.21.9"]}}, deps=[])
    expect(rc == 1 and "1.21.9" in out and not up, "an unknown target is refused")
    # a missing asset for a configured platform
    rc, out, _, up = layer_run({"layer": {"branch": "neoforge-1.21.x", "targets": ["1.21.11"]}}, deps=[],
                               rel_names=["m-2_mr.jar", "m-2_cf.jar", "m-2_mr-mc1.21.11.jar"])
    expect(rc == 1 and "no curseforge asset" in out and not up, f"a target with no file on a platform is refused: {out}")
    # the layer section must name its branch; the tag must be on it
    rc, out, _, _ = layer_run({"layer": {"targets": []}}, deps=[])
    expect(rc == 1 and "names no \"branch\"" in out, "layer without a branch")
    def not_ancestor(repo, *a):
        return R(1) if a[0] == "merge-base" else layer_git()(repo, *a)
    rc, out, _, up = layer_run({"layer": {"branch": "neoforge-1.21.x"}}, deps=[], git=not_ancestor)
    expect(rc == 1 and "not an ancestor of origin/neoforge-1.21.x" in out and not up, "tag must be on the layer branch")
    # dependency per target: the library is published for 1.21.1 only -> targets without it are refused;
    # published for all three -> fine, and Modrinth pins the dependency's version built for the same Minecraft
    deps = [{"port": "o/lib", "type": "required"}]
    only = "<!-- port-publish:modrinth:A1@1.21.1 --> <!-- port-publish:curseforge:9@1.21.1 -->"
    rc, out, _, up = layer_run({"layer": {"branch": "neoforge-1.21.x", "released": ["1.21.4"]}}, deps=deps, lib_body=only)
    expect(rc == 1 and "target 1.21.4: o/lib@lib-1-mc1.21.1 is not published on modrinth for Minecraft 1.21.4" in out and not up, f"dependency per target: {out}")
    rc, out, _, up = layer_run({"layer": {"branch": "neoforge-1.21.x", "released": ["1.21.4"]}}, deps=deps, lib_body=LIB_ALL,
                               git=layer_git(pins="publish.dep.lib=1+1.21.4\n"))
    mr = {u[1]["fields"]["data"]["game_versions"][0]: u[1]["fields"]["data"] for u in up if u[0] == "modrinth"}
    expect(rc == 0 and mr["1.21.4"]["dependencies"][0].get("version_id") == "LV41"
           and "version_id" not in mr["1.21.1"]["dependencies"][0], f"the dependency version for the same Minecraft is pinned: {out} {mr}")
    rc, out, _, up = layer_run({"layer": {"branch": "neoforge-1.21.x", "released": ["1.21.4"]}}, deps=deps, lib_body=LIB_ALL,
                               git=layer_git(pins="publish.dep.lib=9+1.21.4\n"))
    expect(rc == 1 and "has no Modrinth version 9+1.21.4" in out and not up, f"a pin the library lacks is refused: {out}")
    # an old-style single marker for the dependency counts for ITS minecraft only
    rc, out, _, up = layer_run({"layer": {"branch": "neoforge-1.21.x", "targets": ["1.21.1"]}}, deps=deps)
    expect(rc == 0 and len(up) == 2, f"a single-version dependency release covers the base target: {out}")
    # preview targets (issue #76): built and gated, never published, never an error -- listed instead
    rc, out, _, up = layer_run({"layer": {"branch": "neoforge-1.21.x", "released": ["1.21.4"]}}, deps=[],
                               git=layer_git(preview=("1.21.4",)))
    mr = sorted(u[1]["fields"]["data"]["game_versions"][0] for u in up if u[0] == "modrinth")
    expect(rc == 0 and mr == ["1.21.1", "1.21.11"] and len(up) == 4 and "target 1.21.4: preview, not published" in out
           and "m-2_mr-mc1.21.4.jar" not in json.dumps(up), f"a preview target is listed and not uploaded: rc={rc} {out}")
    # ...even when its gates would refuse it (a compile-only middle target, not released): skipped, not an error
    rc, out, _, up = layer_run({"layer": {"branch": "neoforge-1.21.x"}}, deps=[], git=layer_git(preview=("1.21.4",)))
    expect(rc == 0 and len(up) == 4 and "gates are not green" not in out and "target 1.21.4: preview, not published" in out,
           f"a preview target's gates are not asked: {out}")
    # ...and when the config names it explicitly, or every target left is preview: nothing uploaded, exit 0
    rc, out, _, up = layer_run({"layer": {"branch": "neoforge-1.21.x", "targets": ["1.21.11"]}}, deps=[],
                               git=layer_git(preview=("1.21.11",)))
    expect(rc == 0 and not up and "target 1.21.11: preview, not published" in out and "::error::" not in out,
           f"only preview targets asked: listed, nothing published, no error: {out}")
    # a target.properties without the key (or preview=false) publishes as before
    rc, out, _, up = layer_run({"layer": {"branch": "neoforge-1.21.x", "released": ["1.21.4"]}}, deps=[],
                               git=layer_git(pins="preview=false\n"))
    expect(rc == 0 and len(up) == 6 and "preview" not in out, f"preview=false is not preview: {out}")
    # dry run uploads and patches nothing
    rc, out, calls, up = layer_run({"layer": {"branch": "neoforge-1.21.x", "released": ["1.21.4"]}}, deps=[], dry=True)
    expect(rc == 0 and not up and not [c for c in calls if c[1] == "PATCH"] and out.count("modrinth version:") == 3, "dry run is dry")
    print("test-port-publish:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
