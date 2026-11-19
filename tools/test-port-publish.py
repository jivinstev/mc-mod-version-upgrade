#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for tools/port-publish.py that need a whole fork's worth of fakes (the network and git are faked):

  1. SINGLE-VERSION: a single-version publish (dry and real) prints exactly the bytes, and makes exactly the API calls,
     recorded in tools/golden/port-publish-single-version.txt: the guarantee that a fork publishes as it always did.
  2. PER PLATFORM: --platform and the known-issues ledger's blocks-publish, a version branch's ledger read from its head.
  3. OWNED: a repository whose publishing config says `"own_mod": true` (the author's own mod) publishes with no permission link and no
     "unofficial" wording, and `setup` writes its page without either; without the marker nothing changes.

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


def release(names):
    return {"id": 5, "draft": False, "prerelease": False, "author": {"login": "github-actions[bot]"},
            "body": "notes\n\nPlayed by @someone on 2026-10-12.", "assets": [{"name": n, "url": "u"} for n in names]}


def ki_ledger(platforms=None, mc="1.21.1", sev_failure="crash"):
    e = {"id": "KI-7", "title": "crash on the jar", "override": None, "lowest": mc, "status": {mc: "open"}, "source": "gate",
         "rubric": {"failure": sev_failure, "visibility": "common", "reach": "normal", "workaround": False},
         "severity": "S1" if sev_failure == "crash" else "S3"}
    if platforms:
        e["platforms"] = platforms
    return json.dumps({"schema": 1, "issues": [e]})


def per_platform():
    """--platform mr|cf and the known-issues ledger's blocks-publish, per platform (issue #135): a cf-only S1 publishes
    to Modrinth and holds CurseForge back, naming why; a fork without a ledger publishes as before."""
    def run(ledger=None, platform=None, tag="m-2-mc1.21.1", branch_props=None, head_ledger=None):
        pp = load("ppplat")
        with tempfile.TemporaryDirectory() as t:
            d = pathlib.Path(t)
            make_config(d, deps=[])
            if ledger is not None:
                (d / ".port").mkdir(exist_ok=True)
                (d / ".port/KNOWN-ISSUES.json").write_text(ledger, encoding="utf-8")
            if branch_props:
                mc = tag.rsplit("-mc", 1)[1]
                (d / ".port" / mc).mkdir(parents=True)
                (d / ".port" / mc / "branch.properties").write_text(branch_props, encoding="utf-8")
            os.environ.update({"GITHUB_REPOSITORY": "o/m", "MODRINTH_TOKEN": "t", "CURSEFORGE_TOKEN": "t"})
            mc = tag.rsplit("-mc", 1)[1]
            rel = release([f"m-2_mr.jar", f"m-2_cf.jar"])
            shown = []

            def git(repo, *a):
                if a[:2] == ("rev-parse", "--abbrev-ref"):
                    return R(0, f"neoforge-{mc}\n")
                if a[0] == "show" and a[1].endswith(":.port/KNOWN-ISSUES.json"):
                    shown.append(a[1])
                    return R(0, head_ledger) if head_ledger is not None else R(128)
                return R(0)
            calls, up = fakes(pp, rel, git)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = pp.publish(d, tag, dry=False, manual=False, platform=platform)
            return rc, buf.getvalue(), sorted({u[0] for u in up}), shown
    rc, out, up, _ = run(ki_ledger(["cf"]))
    expect(rc == 0 and up == ["modrinth"] and "::warning::curseforge: HELD BACK, not uploaded: 1 open S1 at Minecraft 1.21.1 "
           "against the cf jar" in out and "KI-7 crash on the jar" in out,
           f"a cf-only S1: published to Modrinth, CurseForge held back and named: rc={rc} {up} {out}")
    rc, out, up, _ = run(ki_ledger(["cf"]), platform="cf")
    expect(rc == 1 and up == [] and "HELD BACK" in out and "modrinth: not asked (--platform cf)" in out,
           f"--platform cf with a cf-only S1: nothing uploaded, exit 1: rc={rc} {out}")
    rc, out, up, _ = run(platform="mr")
    expect(rc == 0 and up == ["modrinth"] and "curseforge: not asked (--platform mr) -- skipped" in out,
           f"--platform mr, no ledger: Modrinth only: rc={rc} {out}")
    rc, out, up, _ = run(ki_ledger())
    expect(rc == 1 and up == [] and out.count("HELD BACK") == 2, f"an S1 on every platform holds both back: {out}")
    rc, out, up, _ = run(ki_ledger(sev_failure="cosmetic"))
    expect(rc == 0 and up == ["curseforge", "modrinth"] and "HELD BACK" not in out, f"an open S3 blocks nothing: {out}")
    rc, out, up, _ = run("{not json")
    expect(rc == 1 and up == [] and "not valid" in out, f"an unreadable ledger holds every platform back: {out}")
    # a version branch: the ledger is read from the head branch (port_branch), not the checkout
    rc, out, up, shown = run(ki_ledger(), tag="m-2-mc1.21.4", branch_props="port_branch=neoforge-1.21.1\n",
                             head_ledger=ki_ledger(["mr"], mc="1.21.4"))
    expect(rc == 0 and up == ["curseforge"] and shown == ["refs/remotes/origin/neoforge-1.21.1:.port/KNOWN-ISSUES.json"]
           and "modrinth: HELD BACK" in out, f"a version branch reads the head branch's ledger: rc={rc} {up} {shown} {out}")


def git_repo(d, files, owned_marker):
    import subprocess
    g = lambda *a: subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True, encoding="utf-8", check=True)
    g("init", "-q", "-b", "neoforge-1.21.8"); g("config", "user.email", "a@b"); g("config", "user.name", "a")
    g("config", "core.autocrlf", "false")
    for path, text in files.items():
        f = d / path; f.parent.mkdir(parents=True, exist_ok=True); f.write_text(text, encoding="utf-8")
    if owned_marker:
        cfg_path = d / ".github/port-publish.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {"schema": 1}
        cfg["own_mod"] = True
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    g("add", "-A"); g("commit", "-qm", "x")


def owned_repo():
    """An owned repository: publish needs no permission link and its notes carry no "unofficial" or permission line;
    setup takes no --permission and writes no community-port wording; the same repository without the marker refuses."""
    def run(marker, permission, github=None):
        pp = load("ppowned")
        with tempfile.TemporaryDirectory() as t:
            d = pathlib.Path(t)
            make_config(d, extra={"permission": permission} if permission is not None else None, deps=[])
            if permission is None:
                cfg = json.loads((d / ".github/port-publish.json").read_text(encoding="utf-8"))
                cfg.pop("permission")
                (d / ".github/port-publish.json").write_text(json.dumps(cfg), encoding="utf-8")
            git_repo(d, {"README.md": "x\n"}, marker)
            os.environ.update({"GITHUB_REPOSITORY": "o/m", "MODRINTH_TOKEN": "t", "CURSEFORGE_TOKEN": "t"})
            rel = release(["m-2.jar"])
            pp_git = lambda repo, *a: R(0, "neoforge-1.21.8\n") if a[:2] == ("rev-parse", "--abbrev-ref") else R(0)
            calls, up = fakes(pp, rel, pp_git, mc_ids={"1.21.8": 14})
            if github is not None:
                inner = pp.gh
                pp.gh = lambda method, path, *a, **k: github if path == "/repos/o/m" else inner(method, path, *a, **k)
            cfg_ = json.loads((d / ".github/port-publish.json").read_text(encoding="utf-8"))
            cfg_["modrinth"]["asset"] = cfg_["curseforge"]["asset"] = ""
            (d / ".github/port-publish.json").write_text(json.dumps(cfg_), encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = pp.publish(d, "m-2-mc1.21.8", dry=False, manual=False)
            return rc, buf.getvalue(), up
    rc, out, up = run(True, None)
    texts = [u[1]["fields"]["data" if u[0] == "modrinth" else "metadata"]["changelog"] for u in up]
    expect(rc == 0 and sorted(u[0] for u in up) == ["curseforge", "modrinth"] and len(texts) == 2
           and all(x.startswith("M 2 for NeoForge 1.21.8.") and "nofficial" not in x and "permission" not in x
                   and "original" not in x and "https://github.com/o/m/issues" in x for x in texts),
           f"owned, no permission link: published to both, notes with no unofficial/permission wording: rc={rc} {texts} {out}")
    rc, out, up = run(False, None)
    expect(rc == 1 and up == [] and "no permission link in the config" in out,
           f"the same repository without own_mod and with no permission link: refused as before: rc={rc} {out}")
    rc, out, up = run(False, "https://example.invalid/agreed")
    texts = [u[1]["fields"]["data" if u[0] == "modrinth" else "metadata"]["changelog"] for u in up]
    expect(rc == 0 and texts and all("**Unofficial community port**" in x and "https://example.invalid/agreed" in x for x in texts),
           f"a fork with its permission link: the community-port notes, unchanged: rc={rc} {texts}")
    rc, out, up = run(True, "https://example.invalid/agreed")
    expect(rc == 1 and up == [] and "both own_mod and a permission link" in out,
           f"own_mod and a permission link together: refused, nothing uploaded: rc={rc} {out}")
    rc, out, up = run(True, None, github={"fork": True, "parent": {"full_name": "authors/mod"}})
    expect(rc == 1 and up == [] and "GitHub says this repository is a fork" in out,
           f"own_mod on a GitHub fork: refused, nothing uploaded: rc={rc} {out}")

    # setup: GitHub's own answer decides, with no manual flag -- not a fork: the author's own mod (own_mod: true, no
    # permission, no community-port wording); a fork: --permission required; unknown: --own-mod or --permission
    toml = (
        'modLoader="javafml"\nloaderVersion="[4,)"\nlicense="MPL-2.0"\n[[mods]]\nmodId="examplemod"\n'
        'displayName="Example Mod"\nauthors="Example Co"\ndescription="Adds a few example blocks."\n'
        '[[dependencies.examplemod]]\nmodId="neoforge"\ntype="required"\n')
    for github, argv, want_rc, want_owned in (
            ({"fork": False}, [], 0, True),                                        # automatic: not a fork
            ({"fork": False}, ["--own-mod"], 0, True),                             # explicit, same result
            ({"fork": False}, ["--own-mod", "--permission", "https://example.invalid/agreed"], 1, False),
            ({"fork": True, "parent": {"full_name": "authors/mod"}}, [], 1, False),          # a fork: needs permission
            ({"fork": True, "parent": {"full_name": "authors/mod"}}, ["--own-mod"], 1, False),  # never own on a fork
            (None, [], 1, False),                                                  # GitHub cannot say: refused
            (None, ["--own-mod"], 0, True)):                                       # ... unless said explicitly
        pp = load("ppownsetup")
        with tempfile.TemporaryDirectory() as t:
            d = pathlib.Path(t)
            git_repo(d, {"src/main/resources/META-INF/neoforge.mods.toml": toml, "gradle.properties": "minecraft_version=1.21.8\n"},
                     False)
            os.environ["GITHUB_REPOSITORY"] = "example/mod"
            def http(method, url, *a, _g=github, **k):
                if url.endswith("/repos/example/mod") and _g is not None:
                    return _g
                raise SystemExit(f"{method} {url} -> HTTP 404: offline in the test")
            pp.http = http
            sys.argv = ["port-publish.py", "setup", "--repo", str(d), "--branch", "neoforge-1.21.8", *argv]
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    rc = pp.main()
            except SystemExit as e:
                rc = 1 if e.code else 0
            expect(rc == want_rc, f"setup {github} {argv}: exit {rc}, want {want_rc}: {buf.getvalue()}")
            made = d / ".github/port-publish.json"
            expect(made.exists() == want_owned, f"setup {github} {argv}: config written={made.exists()}, want {want_owned}")
            if want_owned:
                body = (d / ".github/port-publish/description.md").read_text(encoding="utf-8")
                cfg = json.loads(made.read_text(encoding="utf-8"))
                expect(body.startswith("# Example Mod\n") and "Adds a few example blocks." in body
                       and not any(w in body for w in ("nofficial", "Community Port", "permission", "original"))
                       and "Minecraft 1.21.8" in body and pp.public_text_problems(body) == [],
                       f"owned setup: the page names the mod, with no community-port wording: {body}")
                expect(cfg.get("own_mod") is True and "permission" not in cfg and cfg["title"] == "Example Mod"
                       and cfg["modrinth"]["slug"] == "examplemod" and cfg["license"] == "MPL-2.0"
                       and [x["type"] for x in cfg["disclosures"]] == ["ai_content"]
                       and cfg["summary"] == "Adds a few example blocks.",
                       f"owned setup: own_mod true, no permission, no derivative-work disclosure: {cfg}")


def templated_toml():
    """A mods.toml the build fills in from gradle.properties (processResources `expand`): setup reads the values, and a
    ${key} no property names (a build-only block) is left out, not a TOML error."""
    toml = ('modLoader="javafml"\nloaderVersion="${loader_version_range}"\nlicense="${mod_license}"\n[[mods]]\n'
            'modId="${mod_id}"\nversion="${mod_version}"\ndisplayName="${mod_name}"\nauthors="${mod_authors}"\n'
            "description='''\n${mod_description}\n'''\n[[dependencies.${mod_id}]]\nmodId=\"neoforge\"\ntype=\"required\"\n"
            '[[mixins]]\nconfig="${mod_id}.mixins.json"\n${build_only_block}\n')
    props = ("# a comment=ignored\nloader_version_range=[1,)\nmod_license=MPL-2.0\nmod_id=examplemod\nmod_version=1.0.0\n"
             "mod_name=Example Mod\nmod_authors=Example Co\nmod_description=Adds a few example blocks.\n"
             "minecraft_version=1.21.8\n")
    pp = load("pptemplated")
    with tempfile.TemporaryDirectory() as t:
        d = pathlib.Path(t)
        git_repo(d, {"src/main/resources/META-INF/neoforge.mods.toml": toml, "gradle.properties": props}, False)
        got = pp.mods_toml(d)
        mod = got["mods"][0]
        expect(mod["modId"] == "examplemod" and mod["displayName"] == "Example Mod" and got["license"] == "MPL-2.0"
               and mod["description"].strip() == "Adds a few example blocks." and "examplemod" in got["dependencies"],
               f"a templated mods.toml is read with gradle.properties' values: {got}")


def main():
    global ok
    if "--regen" in sys.argv:
        GOLDEN.write_text(single_version(GOLDEN), encoding="utf-8")
        print("wrote", GOLDEN)
        return 0
    # 1. single-version: byte-for-byte what it printed before
    got = single_version(GOLDEN)
    want = GOLDEN.read_text(encoding="utf-8")
    expect(got == want, "single-version publish output/API calls differ from tools/golden/port-publish-single-version.txt")
    per_platform()
    owned_repo()
    templated_toml()
    print("test-port-publish:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
