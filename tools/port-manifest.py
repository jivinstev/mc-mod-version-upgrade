#!/usr/bin/env python3
"""Keep a fork port's install manifest on its newest full Release and on the sibling jars its CI pins.

    python3 tools/port-manifest.py refresh --repo <fork checkout> [--tag TAG] [--variant SUBSTRING]
                                           [--check] [--commit]

`.github/port-install.json` on a port branch (written by tools/port-offer.py) is what tools/install-port.py
installs: the port's own jar (`role: self`) and each dependency CI tested, with URL and sha256. port-offer writes
it once; this keeps it current after a release is promoted or a sibling port is re-pinned. It rewrites only:
  - the `self` entry: the asset of TAG, or of the newest full Release of this branch's tag series
    (<modid>-<version>-mc<mc><suffix>[-rN], the suffix read from the workflow's tag filter). A release with several
    jars keeps the variant the manifest already names (the same file name with the version numbers ignored, e.g.
    `*_mr.jar`), or the one --variant names;
  - each entry with `"port": "owner/repo"`: the release asset the checked-out `.github/workflows/port-ci.yml`
    pins for that repository (tools/port-ci.py --dep-jar), with the sha256 that workflow checks;
  - `release`: {"url": <the release page>, "prerelease": false}.
Every other entry (registry dependencies, optional mods) is left exactly as it is. sha256: the workflow's pin for
a sibling, GitHub's asset `digest` for the release when it gives one, else the file is downloaded and hashed.

Exit 0: written, or "unchanged" when nothing differs. --check writes nothing and exits 1 when the manifest is
behind. Exit 2, nothing written: no manifest, a tag that is not a full Release of this series (or not the newest
one), a self jar that cannot be chosen unambiguously, a sibling jar that cannot be fetched. --commit commits the
file alone, as a data-only `[skip ci]` commit; it never pushes (the Port CI promote job pushes its own).

The shared release logic (tag series, newest release) also serves tools/port-offer.py and tools/install-port.py.
Standard library only; GITHUB_TOKEN / GH_TOKEN, when set, is sent to api.github.com only.
"""
import argparse, hashlib, json, os, pathlib, re, subprocess, sys, urllib.request

MANIFEST = ".github/port-install.json"
WORKFLOW = ".github/workflows/port-ci.yml"
API = "https://api.github.com"
UA = {"User-Agent": "port-manifest"}
RELEASE_URL = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/releases/download/([^/]+)/([^/]+)")


class Refuse(Exception):
    pass


def api(url):
    """JSON from the GitHub API, or None (offline, rate-limited, not found)."""
    h = dict(UA)
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok and url.startswith(API + "/"):
        h["Authorization"] = f"Bearer {tok}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def download(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180) as r:
        return r.read()


def is_port_tag(tag, modid, mc, suffix=""):
    """<modid>-<version>-mc<mc><suffix>, or a re-release of it (-r2, -r3: the port changed, the author's version did
    not). The suffix is the branch's own tag series (port-ci --tag-suffix): a release-aligned branch's jars never
    pass for the development port's, nor the other way round."""
    return bool(re.fullmatch(re.escape(modid) + r"-.+-mc" + re.escape(mc) + re.escape(suffix) + r"(?:-r\d+)?", tag))


def workflow_tag_suffix(text, mc):
    """The tag series a branch's CI releases under, read from its workflow's tag filter."""
    m = re.search(r'tags:\s*\["\*-mc' + re.escape(mc) + r'([^"]*)"\]', text or "")
    return m.group(1) if m else ""


def series_suffix(tag, modid, mc):
    """The tag series suffix of a release tag of this mod and Minecraft version ("" or e.g. "-release"), or None."""
    m = re.fullmatch(re.escape(modid) + r"-.+?-mc" + re.escape(mc) + r"(|-[A-Za-z][\w.-]*?)(?:-r\d+)?", tag or "")
    return m.group(1) if m else None


def pinned_jars(text):
    """[(repo, tag, asset, url, sha256 or None)]: the sibling release jars a Port CI workflow puts into mavenLocal
    (tools/port-ci.py --dep-jar), each with the sha256 the workflow checks it against. --dep-jar-variant legs
    (written URL-first) are not the default pin and are left out."""
    out = []
    for m in re.finditer(r'curl -fsSL -o "([^"]*/[\w.+-]+\.jar)" "(https://github\.com/[^"]+/releases/download/[^"]+)"'
                         r'(?:\n[ \t]*echo "([0-9a-f]{64})  ([^"]+)" \| sha256sum -c -)?', text or ""):
        r = RELEASE_URL.fullmatch(m.group(2))
        if r:
            sha = m.group(3) if m.group(3) and m.group(4) == m.group(1) else None
            out.append((r.group(1), r.group(2), r.group(3), m.group(2), sha))
    return out


def ours(rels, modid, mc, suffix=""):
    return [r for r in rels or [] if not r.get("draft") and is_port_tag(r.get("tag_name", ""), modid, mc, suffix)]


def newest_full(rels, modid, mc, suffix=""):
    """The newest full Release of the tag series (releases come newest first, so a -r2 wins), or None."""
    return next((r for r in ours(rels, modid, mc, suffix) if not r.get("prerelease")), None)


def own_release(fork, modid, mc, suffix="", rels=None):
    """(url, assets, prerelease) of the release to install: the newest FULL Release (a person played it and
    port-ci.py promote made it one -- the fork's official baseline), else the newest pre-release, flagged so the
    install text says it is not yet played. Releases come newest first, so a -r2 re-release wins within each kind."""
    if rels is None:
        rels = api(f"{API}/repos/{fork}/releases?per_page=30") or []
    o = ours(rels, modid, mc, suffix)
    r = next((r for r in o if not r.get("prerelease")), None) or (o[0] if o else None)
    if not r:
        return None, [], False
    return r["html_url"], [(a["name"], a["browser_download_url"]) for a in r.get("assets", [])], bool(r.get("prerelease"))


def skeleton(name):
    """A jar name with its version numbers blanked: the same variant of two releases has the same skeleton
    (x-1.1.8_mr.jar and x-1.1.9_mr.jar), two platform jars of one release do not."""
    return re.sub(r"\d+(?:\.\d+)*", "#", name or "")


def pick_asset(assets, old_name=None, variant=None):
    """The one jar of a release the manifest names: the only jar; else the --variant substring's; else the jar with
    the old self entry's skeleton. Refuses when that is not exactly one."""
    jars = [a for a in assets if a["name"].endswith(".jar")]
    if len(jars) > 1:
        jars = [a for a in jars if not a["name"].endswith(("-sources.jar", "-javadoc.jar"))] or jars
    if len(jars) == 1 and not variant:
        return jars[0]
    if variant:
        pick, how = [a for a in jars if variant in a["name"]], f"containing {variant!r}"
    elif old_name:
        pick, how = [a for a in jars if skeleton(a["name"]) == skeleton(old_name)], f"of the same variant as {old_name}"
    else:
        pick, how = [], "(the manifest names none; pass --variant)"
    if len(pick) != 1:
        raise Refuse(f"cannot choose the release's jar: {len(pick)} of {', '.join(a['name'] for a in jars) or 'no jars'} "
                     f"{how}")
    return pick[0]


def asset_sha(asset, fetch=download):
    d = asset.get("digest") or ""
    if re.fullmatch(r"sha256:[0-9a-f]{64}", d):
        return d.split(":", 1)[1]
    try:
        return hashlib.sha256(fetch(asset["browser_download_url"])).hexdigest()
    except Exception as e:
        raise Refuse(f"cannot fetch {asset['browser_download_url']}: {type(e).__name__}: {e}")


def choose_release(rels, modid, mc, suffix, tag=None, by_tag=None):
    """The release the self entry names: TAG (a full Release of this series and the newest one), else the newest
    full Release of the series."""
    newest = newest_full(rels, modid, mc, suffix)
    if not tag:
        if not newest:
            raise Refuse(f"no full Release of the tag series {modid}-<version>-mc{mc}{suffix} yet (promote one first)")
        return newest
    if not is_port_tag(tag, modid, mc, suffix):
        raise Refuse(f"{tag} is not a tag of this branch's series {modid}-<version>-mc{mc}{suffix}[-rN]")
    rel = next((r for r in rels or [] if r.get("tag_name") == tag), None) or (by_tag(tag) if by_tag else None)
    if not rel:
        raise Refuse(f"no release named {tag}")
    if rel.get("draft") or rel.get("prerelease"):
        raise Refuse(f"{tag} is a {'draft' if rel.get('draft') else 'pre-release'}: the manifest names a full Release")
    if newest and newest.get("tag_name") != tag:
        raise Refuse(f"{tag} is not the newest full Release ({newest['tag_name']} is); run without --tag")
    return rel


def refresh(man, wf_text, rels, tag=None, variant=None, fetch=download, by_tag=None):
    """-> (new manifest, [change lines]). Raises Refuse; never touches an entry it has no source for."""
    new = json.loads(json.dumps(man))
    modid, mc = new.get("modid"), new.get("minecraft")
    if not modid or not mc:
        raise Refuse("the manifest has no modid / minecraft")
    files = new.get("files") or []
    selfs = [f for f in files if f.get("role") == "self"]
    if len(selfs) > 1:
        raise Refuse("the manifest names more than one self entry")
    rel = choose_release(rels, modid, mc, workflow_tag_suffix(wf_text, mc), tag, by_tag)
    asset = pick_asset(rel.get("assets") or [], selfs[0].get("name") if selfs else None, variant)
    changes = []
    if selfs:
        f = selfs[0]
    else:
        f = {"role": "self", "modid": modid}
        files.insert(0, f)
    if f.get("url") != asset["browser_download_url"] or not f.get("sha256"):
        sha = asset_sha(asset, fetch)
        changes.append(f"self: {f.get('name') or '(none)'} -> {asset['name']} ({rel['tag_name']})")
        f.update(name=asset["name"], url=asset["browser_download_url"], sha256=sha)
        f.pop("error", None)
    want = {"url": rel["html_url"], "prerelease": False}
    if new.get("release") != want:
        changes.append(f"release: {(new.get('release') or {}).get('url') or '(none)'} -> {rel['html_url']}")
        new["release"] = want
    pins = pinned_jars(wf_text)
    notes = []
    for f in files:
        if f.get("role") == "self" or not f.get("port"):
            continue
        same = [p for p in pins if p[0] == f["port"]]
        if len(same) > 1:
            same = [p for p in same if skeleton(p[2]) == skeleton(f.get("name"))]
        if len(same) != 1:
            notes.append(f"{f.get('modid')} ({f['port']}): {'no' if not same else 'no single'} pin in {WORKFLOW}; left as is")
            continue
        repo, ptag, passet, url, psha = same[0]
        if f.get("url") == url and f.get("sha256") and (not psha or f["sha256"] == psha):
            continue
        if not psha:
            try:
                psha = hashlib.sha256(fetch(url)).hexdigest()
            except Exception as e:
                raise Refuse(f"cannot fetch {f.get('modid')}'s pinned jar {url}: {type(e).__name__}: {e}")
        changes.append(f"{f.get('modid')} ({repo}): {f.get('name') or '(none)'} -> {passet} ({ptag})")
        f.update(name=passet, url=url, sha256=psha)
        f.pop("error", None)
    return new, changes, notes


def stale(man, rels):
    """For tools/install-port.py: (manifest tag, newest full Release tag) when the manifest's self jar is not on the
    newest full Release of its fork's tag series, else None. A manifest naming a release newer than every full one
    (a pre-release, before any promotion) is not stale."""
    s = next((f for f in man.get("files") or [] if f.get("role") == "self" and f.get("url")), None)
    m = RELEASE_URL.fullmatch((s or {}).get("url") or "")
    modid, mc = man.get("modid"), man.get("minecraft")
    if not m or not modid or not mc:
        return None
    tag = m.group(2)
    suffix = series_suffix(tag, modid, mc)
    if suffix is None:
        return None
    o = ours(rels, modid, mc, suffix)
    n = next((r for r in o if not r.get("prerelease")), None)
    if not n or n["tag_name"] == tag:
        return None
    tags = [r["tag_name"] for r in o]
    if tag in tags and tags.index(tag) < tags.index(n["tag_name"]):
        return None
    return tag, n["tag_name"], n.get("html_url")


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def run_refresh(a, rels_of=None, fetch=download):
    repo = pathlib.Path(a.repo)
    mf, wf = repo / MANIFEST, repo / WORKFLOW
    if not mf.is_file():
        raise Refuse(f"no {MANIFEST} in {repo} (tools/port-offer.py --push writes the first one)")
    text = mf.read_text(encoding="utf-8")
    try:
        man = json.loads(text)
    except ValueError as e:
        raise Refuse(f"{MANIFEST} is not JSON: {e}")
    fork = man.get("port")
    if not fork or not re.fullmatch(r"[\w.-]+/[\w.-]+", fork):
        raise Refuse(f"{MANIFEST} names no fork (\"port\": \"owner/repo\")")
    rels_of = rels_of or (lambda f: api(f"{API}/repos/{f}/releases?per_page=100"))
    rels = rels_of(fork)
    if rels is None:
        raise Refuse(f"cannot read {fork}'s releases from GitHub")
    new, changes, notes = refresh(man, wf.read_text(encoding="utf-8") if wf.is_file() else "", rels, a.tag, a.variant,
                                  fetch, by_tag=lambda t: api(f"{API}/repos/{fork}/releases/tags/{t}"))
    for n in notes:
        print(f"  note: {n}")
    out = json.dumps(new, indent=2) + "\n"
    if out == text or not changes and json.loads(text) == new:
        print(f"port-manifest: {MANIFEST} unchanged ({new['release']['url']})")
        return 0
    for c in changes:
        print(f"  {c}")
    if a.check:
        print(f"port-manifest: {MANIFEST} is behind ({len(changes)} change(s)); nothing written")
        return 1
    mf.write_text(out, encoding="utf-8")
    print(f"port-manifest: wrote {MANIFEST}")
    if a.commit:
        tag = next((f["url"].rsplit("/", 2)[1] for f in new["files"] if f.get("role") == "self"), "")
        r = git(repo, "add", MANIFEST)
        r = r if r.returncode else git(repo, "commit", "-q", "-m", f"Install manifest: {tag} [skip ci]\n\n"
                                       "The newest full Release and the sibling jars this branch's CI pins, with sha256, "
                                       "for tools/install-port.py\nin the migrator (tools/port-manifest.py refresh). "
                                       "Data only; not part of the port.\n", "--", MANIFEST)
        if r.returncode:
            raise Refuse(f"commit failed: {(r.stderr or r.stdout).strip()[-300:]}")
        print(f"port-manifest: committed {MANIFEST}")
    return 0


def self_check():
    import tempfile
    ok, failed = True, []
    def chk(c, n):
        if not c:
            failed.append(n)
        return bool(c)
    def rel(tag, pre=False, draft=False, names=None, digest=True):
        return {"tag_name": tag, "prerelease": pre, "draft": draft, "html_url": f"https://github.com/o/me/releases/tag/{tag}",
                "assets": [dict({"name": n, "browser_download_url": f"https://github.com/o/me/releases/download/{tag}/{n}"},
                                **({"digest": "sha256:" + hashlib.sha256(n.encode()).hexdigest()} if digest else {}))
                           for n in (names or [f"{tag}.jar"])]}
    ok &= chk(is_port_tag("m-2.2.1-mc1.21.1-r2", "m", "1.21.1") and not is_port_tag("m-2-mc1.21.1-release", "m", "1.21.1"), 1)
    ok &= chk(series_suffix("m-2-mc1.21.1-release-r2", "m", "1.21.1") == "-release"
              and series_suffix("m-2-mc1.21.1", "m", "1.21.1") == "" and series_suffix("x-2-mc1.21.1", "m", "1.21.1") is None
              and series_suffix("m-2-mc1.21.10", "m", "1.21.1") is None, 2)
    ok &= chk(skeleton("x-1.1.8_mr.jar") == skeleton("x-1.2_mr.jar") != skeleton("x-1.1.8_cf.jar"), 3)
    wf = ('    tags: ["*-mc1.21.1"]\n'
          '          curl -fsSL -o "$HOME/.m2/repository/com/example/lib/lib/1.1.9/lib-1.1.9.jar" '
          '"https://github.com/o/lib/releases/download/lib-1.1.9-mc1.21.1/lib-1.1.9_mr.jar"\n'
          '          echo "' + "a" * 64 + '  $HOME/.m2/repository/com/example/lib/lib/1.1.9/lib-1.1.9.jar" | sha256sum -c -\n'
          '          curl -fsSL "https://github.com/o/lib/releases/download/lib-1.1.9-mc1.21.1/lib-1.1.9_cf.jar" -o "x"\n')
    ok &= chk(pinned_jars(wf) == [("o/lib", "lib-1.1.9-mc1.21.1", "lib-1.1.9_mr.jar",
                                   "https://github.com/o/lib/releases/download/lib-1.1.9-mc1.21.1/lib-1.1.9_mr.jar", "a" * 64)], 4)
    old = "https://github.com/o/me/releases/download/me-1.1.1-mc1.21.1/me-1.1.1_mr.jar"
    man = {"schema": 1, "port": "o/me", "modid": "me", "minecraft": "1.21.1", "neoforge": "21.1.1", "files": [
        {"role": "self", "modid": "me", "name": "me-1.1.1_mr.jar", "url": old, "sha256": "0" * 64},
        {"role": "required", "modid": "lib", "name": "lib-1.1.8_mr.jar", "sha256": "1" * 64, "port": "o/lib",
         "url": "https://github.com/o/lib/releases/download/lib-1.1.8-mc1.21.1/lib-1.1.8_mr.jar"},
        {"role": "required", "modid": "base", "name": "base.jar", "url": "https://example.invalid/base.jar", "sha256": "2" * 64}],
        "release": {"url": "https://github.com/o/me/releases/tag/me-1.1.1-mc1.21.1", "prerelease": False}}
    rels = [rel("me-1.1.3-mc1.21.1", pre=True), rel("me-1.1.2-mc1.21.1", names=["me-1.1.2_cf.jar", "me-1.1.2_mr.jar"]),
            rel("me-1.1.2-mc1.21.1-release"), rel("me-1.1.1-mc1.21.1", names=["me-1.1.1_cf.jar", "me-1.1.1_mr.jar"])]
    new, ch, notes = refresh(man, wf, rels)
    s, lib = new["files"][0], new["files"][1]
    ok &= chk(s["name"] == "me-1.1.2_mr.jar" and s["sha256"] == hashlib.sha256(b"me-1.1.2_mr.jar").hexdigest(), 10)
    ok &= chk(lib["name"] == "lib-1.1.9_mr.jar" and lib["sha256"] == "a" * 64 and "lib-1.1.9-mc1.21.1" in lib["url"], 11)
    ok &= chk(new["files"][2] == man["files"][2] and new["release"] == {
        "url": "https://github.com/o/me/releases/tag/me-1.1.2-mc1.21.1", "prerelease": False} and len(ch) == 3 and not notes, 12)
    ok &= chk(man["files"][0]["url"] == old, 13)                              # the input is never modified
    ok &= chk(refresh(new, wf, rels)[1] == [], 14)                            # idempotent: unchanged
    ok &= chk(refresh(man, wf, rels, tag="me-1.1.2-mc1.21.1")[0] == new, 15)
    for bad, why in ((dict(tag="me-1.1.3-mc1.21.1"), "pre-release"), (dict(tag="me-1.1.1-mc1.21.1"), "not the newest"),
                     (dict(tag="me-1.1.2-mc1.21.1-release"), "not a tag of this branch"), (dict(tag="nope"), "not a tag")):
        try:
            refresh(man, wf, rels, **bad); ok &= chk(False, 16)
        except Refuse as e:
            ok &= chk(why in str(e), f"17 {why}")
    two = dict(man, files=[dict(man["files"][0], name="me.jar")] + man["files"][1:])     # no variant to match
    for args in ((two, wf, rels), (dict(man, files=man["files"][1:]), wf, rels)):
        try:
            refresh(*args); ok &= chk(False, 18)
        except Refuse as e:
            ok &= chk("cannot choose" in str(e), 19)
    ok &= chk(refresh(two, wf, rels, variant="_cf")[0]["files"][0]["name"] == "me-1.1.2_cf.jar", 20)
    try:                                                    # only pre-releases: nothing to name
        refresh(man, wf, rels[:1]); ok &= chk(False, 21)
    except Refuse as e:
        ok &= chk("no full Release" in str(e), 22)
    # a release-aligned branch: its own series; a sibling pin without a sha256 line is downloaded; a failed one refuses
    rman = dict(man, files=[dict(man["files"][0], name="me-1.1.1-mc1.21.1-release.jar")] + man["files"][1:])
    rwf = wf.replace('"*-mc1.21.1"', '"*-mc1.21.1-release"').replace("| sha256sum", "| nothing")
    rn, rc, _ = refresh(rman, rwf, rels, fetch=lambda u: b"lib")
    ok &= chk(rn["files"][0]["name"] == "me-1.1.2-mc1.21.1-release.jar" and rn["files"][1]["sha256"] == hashlib.sha256(b"lib").hexdigest(), 23)
    def boom(u):
        raise OSError("404")
    try:
        refresh(rman, rwf, rels, fetch=boom); ok &= chk(False, 24)
    except Refuse as e:
        ok &= chk("cannot fetch lib's pinned jar" in str(e), 25)
    nodig = [rel("me-1.1.2-mc1.21.1", names=["me-1.1.2_mr.jar"], digest=False)]
    ok &= chk(refresh(man, wf, nodig, fetch=lambda u: b"x")[0]["files"][0]["sha256"] == hashlib.sha256(b"x").hexdigest(), 26)
    ok &= chk(any("no pin" in n for n in refresh(man, "", rels)[2]), 27)       # an unpinned sibling: left as is, said
    # install-port's question: is the manifest's self on the newest full Release?
    ok &= chk(stale(man, rels) == ("me-1.1.1-mc1.21.1", "me-1.1.2-mc1.21.1", "https://github.com/o/me/releases/tag/me-1.1.2-mc1.21.1"), 30)
    ok &= chk(stale(new, rels) is None and stale(man, rels[3:]) is None, 31)
    pre = dict(man, files=[dict(man["files"][0], url=old.replace("1.1.1-mc", "1.1.3-mc"))])
    ok &= chk(stale(pre, rels) is None, 32)                                    # a pre-release newer than every full one
    rel_old = dict(rman, files=[dict(rman["files"][0], url=old.replace("1.1.1-mc1.21.1", "1.1.1-mc1.21.1-release"))])
    ok &= chk(stale(rel_old, rels)[1] == "me-1.1.2-mc1.21.1-release", 33)    # each series against its own newest
    # the command: refuses without a manifest; --check writes nothing; --commit commits the manifest alone
    import contextlib, io
    with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
        r = pathlib.Path(d)
        for g in (["init", "-q"], ["config", "user.email", "t@e"], ["config", "user.name", "t"]):
            git(r, *g)
        ns = argparse.Namespace(repo=str(r), tag=None, variant=None, check=False, commit=True)
        try:
            run_refresh(ns, lambda f: rels); ok &= chk(False, 40)
        except Refuse as e:
            ok &= chk("no .github/port-install.json" in str(e), 41)
        (r / ".github/workflows").mkdir(parents=True)
        (r / WORKFLOW).write_text(wf, encoding="utf-8")
        (r / MANIFEST).write_text(json.dumps(man, indent=2) + "\n", encoding="utf-8")
        (r / "other.txt").write_text("x\n", encoding="utf-8")
        git(r, "add", "-A"); git(r, "commit", "-qm", "base")
        (r / "other.txt").write_text("y\n", encoding="utf-8")
        ok &= chk(run_refresh(argparse.Namespace(**dict(vars(ns), check=True)), lambda f: rels) == 1, 42)
        ok &= chk(json.loads((r / MANIFEST).read_text(encoding="utf-8")) == man, 43)
        ok &= chk(run_refresh(ns, lambda f: rels) == 0, 44)
        ok &= chk(json.loads((r / MANIFEST).read_text(encoding="utf-8")) == new, 45)
        log = git(r, "log", "-1", "--format=%s", "--name-only").stdout.split()
        ok &= chk(log[:4] == ["Install", "manifest:", "me-1.1.2-mc1.21.1", "[skip"] and log[-1] == MANIFEST
                  and "other.txt" in git(r, "status", "--porcelain").stdout, 46)
        head = git(r, "rev-parse", "HEAD").stdout
        ok &= chk(run_refresh(ns, lambda f: rels) == 0 and git(r, "rev-parse", "HEAD").stdout == head, 47)   # unchanged
    print("self-check:", "OK" if ok else f"FAIL (checks {failed})")
    return 0 if ok else 1


def main():
    if "--self-check" in sys.argv:
        return self_check()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("refresh", help=f"point {MANIFEST} at the newest full Release and the workflow's sibling pins")
    r.add_argument("--repo", default=".", help="the fork's checkout, on its port branch")
    r.add_argument("--tag", help="the release to name (refused unless it is the newest full Release of the series)")
    r.add_argument("--variant", help="when the release has several jars: a substring of the one to name")
    r.add_argument("--check", action="store_true", help="write nothing; exit 1 when the manifest is behind")
    r.add_argument("--commit", action="store_true", help="commit the manifest alone ([skip ci]); never pushes")
    a = ap.parse_args()
    try:
        return run_refresh(a)
    except Refuse as e:
        print(f"port-manifest: refused, nothing written: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
