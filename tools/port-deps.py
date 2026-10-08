#!/usr/bin/env python3
"""Dependency PREFLIGHT for a port: find missing / blocked dependencies with a script, before any model is spent.

    python3 tools/port-deps.py --repo <mod git repo> --mc <target MC> [--loader neoforge] [--fill-local] [--apply-moves] [--json out.json]
    python3 tools/port-deps.py --self-check

Reads the author's build.gradle (+ settings.gradle, gradle.properties, META-INF/*mods.toml), then:
  1. lists every repository URL and probes whether THIS machine can reach it (curl through the configured proxy);
  2. lists every dependency (implementation/api/compileOnly/runtimeOnly/localRuntime/modImplementation/
     annotationProcessor/jarJar, fg.deobf wrappers, string and map forms, ${prop} expanded) and classifies it:
     platform (skipped) / library (no registry lookup) / mod (looked up in tools/mod-registry/modreg.py);
  3. for each mod, asks the registry whether a build for the target exists and suggests the new coordinate in the
     author's own style (cursemaven file id, modrinth maven version, or an author-maven artifact+version);
  4. probes where each declared artifact is actually served from, so a BLOCKED host is named per dependency;
  5. --fill-local places the registry jar of every blocked-host mod under the suggested coordinate via
     tools/local-maven.py.
  6. with --fill-local, for every mod whose AUTHOR version the registry can also serve (a cursemaven file id, a
     modrinth maven version), downloads the old jar into ~/.mc-mod-upgrade/dep-cache/ and runs tools/dep-moves.py
     on (old jar, target jar): classes a dependency MOVED between the two versions (a renamed root package) are
     found by comparing the jars, before any model is spent on `package X does not exist`. --apply-moves
     rewrites the mod's sources for the unambiguous moves. A failed old-jar download is reported, never fatal.

EXIT CODES
    0  all good (optional mods without a target build are reported, never dropped)
    3  a REQUIRED mod dependency has no build for the target -- a decision is needed before porting
    4  a blocked host serves something that has no registry source (cannot be filled locally)
    2  the preflight itself could not finish (registry lookup failed): NOT a pass
Standard library only.
"""
import argparse, concurrent.futures, json, os, pathlib, re, subprocess, sys, tempfile, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODREG = ROOT / "tools/mod-registry/modreg.py"
LOCALMAVEN = ROOT / "tools/local-maven.py"
DEP_CACHE = pathlib.Path.home() / ".mc-mod-upgrade/dep-cache"
LOCAL_MAVEN = pathlib.Path.home() / ".mc-mod-upgrade/local-maven"

PLATFORM_GROUPS = ("net.neoforged", "net.minecraftforge", "net.minecraft", "minecraft")
LIBRARY_PREFIXES = ("org.", "com.google.", "io.github.llamalad7", "javax.", "jakarta.", "com.mojang", "io.netty",
                    "com.fasterxml", "commons-", "it.unimi", "net.sf.", "junit", "net.minecrell", "com.electronwill",
                    "io.leangen", "com.squareup", "com.github.", "de.javagl", "one.util")
LOADER_TOKENS = {"forge", "neoforge", "fabric", "quilt", "common", "api", "mc", "minecraft", "loader", "mod", "mods"}
DEP_CONFIGS_SKIP = {"project", "def", "if", "else", "for", "configurations", "exclude", "val", "var", "constraints",
                    "components", "modules", "println", "tasks", "ext", "buildscript", "plugins"}
WRAPPERS = ("jarJar", "annotationProcessor")


# ---------------------------------------------------------------------------------------------- text helpers
def strip_comments(t):
    """Remove // and /* */ comments without touching string literals (URLs contain //)."""
    out, i, n, q = [], 0, len(t), None
    while i < n:
        c = t[i]
        if q:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(t[i + 1]); i += 2; continue
            if t.startswith(q, i) and (len(q) == 3 or c == q):
                if len(q) == 3:
                    out.append(t[i + 1:i + 3]); i += 3
                else:
                    i += 1
                q = None; continue
            i += 1; continue
        if t.startswith('"""', i) or t.startswith("'''", i):
            q = t[i:i + 3]; out.append(q); i += 3; continue
        if c in "\"'":
            q = c; out.append(c); i += 1; continue
        if t.startswith("//", i):
            while i < n and t[i] != "\n":
                i += 1
            continue
        if t.startswith("/*", i):
            j = t.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" "); continue
        out.append(c); i += 1
    return "".join(out)


def match_close(t, i):
    """t[i] is an opening ( [ {; return the index of the matching closer, skipping strings."""
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack, n, q = [], len(t), None
    while i < n:
        c = t[i]
        if q:
            if c == "\\":
                i += 2; continue
            if c == q:
                q = None
        elif c in "\"'":
            q = c
        elif c in pairs:
            stack.append(pairs[c])
        elif stack and c == stack[-1]:
            stack.pop()
            if not stack:
                return i
        i += 1
    return n - 1


def find_blocks(t, name_re):
    """[(name_start, body_start, body_end)] for every `name {`."""
    res = []
    for m in re.finditer(r"(?<![\w.])(?:%s)\s*\{" % name_re, t):
        ob = m.end() - 1
        res.append((m.start(), ob + 1, match_close(t, ob)))
    return res


def cut(t, spans):
    for s, e in sorted(spans, reverse=True):
        t = t[:s] + " " * 0 + t[e + 1:]
    return t


def read_props(path):
    props = {}
    if path.is_file():
        for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"\s*([\w.\-]+)\s*[=:]\s*(.*?)\s*$", ln)
            if m and not ln.lstrip().startswith(("#", "!")):
                props[m.group(1)] = m.group(2)
    return props


def gradle_vars(text):
    v = {}
    for m in re.finditer(r"(?m)^\s*(?:def\s+|ext\.|String\s+)?(\w+)\s*=\s*(?:\"([^\"$]*)\"|'([^']*)')\s*$", text):
        v.setdefault(m.group(1), m.group(2) if m.group(2) is not None else m.group(3))
    return v


def expand(s, props):
    """Expand ${p} $p ${project.p}; returns (text, keys_used, unresolved)."""
    used, bad = [], []

    def sub(m):
        k = m.group(1) or m.group(2)
        k = re.sub(r"^(?:project|rootProject)\.", "", k)
        if k in props:
            used.append(k)
            return props[k]
        bad.append(k)
        return m.group(0)
    prev = None
    for _ in range(4):
        if prev == s:
            break
        prev = s
        s = re.sub(r"\$\{([\w.]+)\}|\$([A-Za-z_][\w]*(?:\.[A-Za-z_]\w*)?)", sub, s)
    return s, used, sorted(set(bad))


# ---------------------------------------------------------------------------------------------- repositories
def _filters(block):
    inc, rx, exc = [], [], []
    for m in re.finditer(r"include(?:Group|GroupAndSubgroups|Module)\s*\(?\s*['\"]([^'\"]+)['\"]", block):
        inc.append(m.group(1))
    for m in re.finditer(r"include(?:GroupByRegex|ModuleByRegex)\s*\(?\s*['\"]((?:\\.|[^'\"\\])*)['\"]", block):
        rx.append(m.group(1).replace("\\\\", "\\"))
    for m in re.finditer(r"excludeGroup\s*\(?\s*['\"]([^'\"]+)['\"]", block):
        exc.append(m.group(1))
    return inc, rx, exc


def _maven_url(block_or_call):
    m = re.search(r"\b(?:url|setUrl)\b\s*[=(]?\s*(?:uri\s*\(\s*)?['\"]([^'\"]+)['\"]", block_or_call)
    return m.group(1) if m else None


def parse_repos(text, props, kind="declared"):
    """Repositories declared in `repositories {}` blocks of `text` (comments already stripped)."""
    repos = []
    for _, bs, be in find_blocks(text, "repositories"):
        body = text[bs:be]
        # exclusiveContent { forRepository { maven { url } } filter { includeGroup } }
        excl = find_blocks(body, "exclusiveContent")
        for es, ebs, ebe in excl:
            eb = body[ebs:ebe]
            url = _maven_url(eb)
            inc, rx, exc = _filters(eb)
            if url:
                repos.append({"url": url, "includes": inc, "regex": rx, "excludes": exc, "kind": kind, "exclusive": True})
        body = cut(body, [(s, e) for s, _, e in [(a, b, c) for a, b, c in excl]])
        for m in re.finditer(r"(?<![\w.])maven\s*(\(|\{|\s+url\b)", body):
            if m.group(1) == "{":
                ob = m.end() - 1
                blk = body[ob + 1:match_close(body, ob)]
                url = _maven_url(blk)
                inc, rx, exc = _filters(blk)
            elif m.group(1) == "(":
                cp = m.end() - 1
                inner = body[cp + 1:match_close(body, cp)]
                mm = re.match(r"\s*(?:uri\s*\(\s*)?['\"]([^'\"]+)['\"]", inner)
                url = mm.group(1) if mm else None
                inc, rx, exc = [], [], []
            else:
                mm = re.match(r"\s*url\s*[:=]?\s*['\"]([^'\"]+)['\"]", body[m.start() + 5:])
                url = mm.group(1) if mm else None
                inc, rx, exc = [], [], []
            if url:
                repos.append({"url": url, "includes": inc, "regex": rx, "excludes": exc, "kind": kind, "exclusive": False})
        if re.search(r"\bmavenCentral\s*\(", body):
            repos.append({"url": "https://repo.maven.apache.org/maven2/", "includes": [], "regex": [], "excludes": [],
                          "kind": kind, "exclusive": False})
        if re.search(r"\bgradlePluginPortal\s*\(", body):
            repos.append({"url": "https://plugins.gradle.org/m2/", "includes": [], "regex": [], "excludes": [],
                          "kind": "plugin", "exclusive": False})
        if re.search(r"\bgoogle\s*\(\s*\)", body):
            repos.append({"url": "https://maven.google.com/", "includes": [], "regex": [], "excludes": [],
                          "kind": kind, "exclusive": False})
    for r in repos:
        r["url"] = expand(r["url"], props)[0]
    return repos


def repo_accepts(repo, group):
    if group in repo["excludes"]:
        return False
    if not repo["includes"] and not repo["regex"]:
        return True
    for g in repo["includes"]:
        if group == g or group.startswith(g + "."):
            return True
    for rx in repo["regex"]:
        try:
            if re.match(rx + r"\Z", group):
                return True
        except re.error:
            pass
    return False


# ---------------------------------------------------------------------------------------------- dependencies
COORD_RE = re.compile(r"^[^\s:'\"/]+:[^\s:'\"]+(?::[^\s:'\"]*){0,2}$")
STR_RE = re.compile(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'")


def statements(body):
    stmts, depth, start, q, i, n = [], 0, 0, None, 0, len(body)
    while i < n:
        c = body[i]
        if q:
            if c == "\\":
                i += 2; continue
            if c == q:
                q = None
        elif c in "\"'":
            q = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "\n" and depth <= 0:
            stmts.append(body[start:i]); start = i + 1; depth = 0
        i += 1
    stmts.append(body[start:])
    return [s.strip() for s in stmts if s.strip()]


def parse_dependencies(text, props):
    text = cut(text, [(s, e) for s, _, e in find_blocks(text, "buildscript")])
    deps, notes = [], []
    for _, bs, be in find_blocks(text, "dependencies"):
        for st in statements(text[bs:be]):
            m = re.match(r"([A-Za-z_]\w*)", st)
            if not m or m.group(1) in DEP_CONFIGS_SKIP:
                continue
            config = m.group(1)
            found = False
            for sm in STR_RE.finditer(st):
                lit = sm.group(0)[1:-1]
                if "//" in lit or not COORD_RE.match(lit):
                    continue
                prefix = st[:sm.start()]
                wr = [c for c in re.findall(r"([A-Za-z_][\w.]*)\s*\(", prefix) if c in WRAPPERS and c != config]
                parts = lit.split(":")
                ext = None
                if "@" in parts[-1]:
                    parts[-1], ext = parts[-1].split("@", 1)
                g, a = parts[0], parts[1]
                v = parts[2] if len(parts) > 2 and parts[2] != "" else None
                cl = parts[3] if len(parts) > 3 else None
                deps.append(_dep(config, wr, g, a, v, cl, lit, props))
                found = True
            if not found:
                kv = {k: re.search(r"\b%s\s*[:=]\s*(?:\"([^\"]*)\"|'([^']*)')" % k, st) for k in ("group", "name", "version", "classifier")}
                if kv["group"] and kv["name"]:
                    gv = lambda k: (kv[k].group(1) if kv[k].group(1) is not None else kv[k].group(2)) if kv[k] else None
                    wr = [c for c in re.findall(r"([A-Za-z_][\w.]*)\s*\(", st) if c in WRAPPERS and c != config]
                    deps.append(_dep(config, wr, gv("group"), gv("name"), gv("version"), gv("classifier"), st, props))
                elif STR_RE.search(st) and not re.search(r"\b(files|fileTree|project|layout|rootProject)\s*\(", st):
                    notes.append(f"not understood: {st[:90]}")
    return deps, notes


def _dep(config, wr, g, a, v, cl, raw, props):
    g2, _, bg = expand(g, props)
    a2, _, ba = expand(a, props)
    v2, vkeys, bv = expand(v, props) if v is not None else (None, [], [])
    cl2 = expand(cl, props)[0] if cl else None
    return {"group": g2, "artifact": a2, "version": v2, "classifier": cl2, "scope": "+".join([config] + wr),
            "raw": raw if len(raw) < 140 else raw[:137] + "...", "version_raw": v, "version_keys": vkeys,
            "unresolved": sorted(set(bg + ba + bv))}


def parse_mods_toml(repo, props):
    """modId -> required(True)/optional(False), from every META-INF/*mods.toml dependency block."""
    out = {}
    for f in sorted((repo / "src/main/resources/META-INF").glob("*mods.toml")) if (repo / "src/main/resources/META-INF").is_dir() else []:
        txt = f.read_text(encoding="utf-8", errors="replace")
        for blk in re.split(r"(?m)^\s*\[\[", txt)[1:]:
            if not blk.startswith("dependencies"):
                continue
            mid = re.search(r"(?m)^\s*modId\s*=\s*[\"']([^\"']+)[\"']", blk)
            if not mid:
                continue
            man = re.search(r"(?m)^\s*mandatory\s*=\s*(true|false)", blk)
            typ = re.search(r"(?m)^\s*type\s*=\s*[\"'](\w+)[\"']", blk)
            if typ:
                if typ.group(1) in ("incompatible", "discouraged"):
                    continue
                req = typ.group(1) == "required"
            else:
                req = bool(man and man.group(1) == "true")
            m = expand(mid.group(1), props)[0]
            out[m] = out.get(m, False) or req
    return out


# ---------------------------------------------------------------------------------------------- classification
def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def classify(d):
    g = d["group"] or ""
    if g.startswith(PLATFORM_GROUPS) or d["scope"].split("+")[0] == "minecraft":
        return "platform"
    if g in ("curse.maven", "maven.modrinth"):
        return "mod"
    if g.startswith("org.spongepowered") or d["scope"] == "annotationProcessor":
        return "tooling"  # the port's own build replaces the mixin annotation processor / build tooling
    if g.startswith(LIBRARY_PREFIXES):
        return "library"
    return "mod-candidate"


def mod_names(artifact, group):
    """Name variants (hyphenated, for the search query) with loader/MC-version noise stripped."""
    a = re.sub(r"\$\{[^}]*\}", "", artifact.lower())
    a = re.sub(r"(?<![\d.])(?:mc)?\d+\.\d+(?:\.\d+)*(?![\d.])", " ", a)
    toks = [t for t in re.split(r"[-_+\s]+", a) if t and t not in LOADER_TOKENS]
    out = []
    if toks:
        out.append("-".join(toks))
    seg = (group or "").split(".")[-1].lower()
    if seg and seg not in out and seg not in LOADER_TOKENS:
        out.append(seg)
    return out


def _run(cmd, timeout):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def curl_probe(url):
    """HTTP status string; '000' when curl itself failed (proxy refusal, DNS, TLS, timeout)."""
    rc, out, _ = _run(["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "-m", "20", url], 30)
    code = out.strip()[-3:] if out.strip() else "000"
    return "000" if rc != 0 else code


class RegError(Exception):
    pass


def modreg(*args):
    rc, out, err = _run([sys.executable, str(MODREG), *args], 120)
    if rc != 0:
        raise RegError((err or out).strip().splitlines()[-1][:200] if (err or out).strip() else f"exit {rc}")
    try:
        return json.loads(out)
    except ValueError:
        raise RegError("registry returned non-JSON")


def modrinth_version_number(vid):
    rc, out, _ = _run(["curl", "-sS", "-m", "20", f"https://api.modrinth.com/v2/version/{vid}"], 30)
    try:
        return json.loads(out).get("version_number") or vid
    except ValueError:
        return vid


def retry(probe, url, tries=3):
    c = probe(url)
    for i in range(tries - 1):
        if not (c == "429" or c.startswith("5")):
            break
        time.sleep(2 * (i + 1))
        c = probe(url)
    return c


def host_state(code):
    return "BLOCKED" if code in ("403", "407", "000", "") else "REACHABLE"


def artifact_url(repo, d):
    base = repo["url"].rstrip("/") + "/" + d["group"].replace(".", "/") + f"/{d['artifact']}/{d['version']}/{d['artifact']}-{d['version']}"
    return base


# ---------------------------------------------------------------------------------------------- resolution
def resolve_mod(d, search, versions_fn, target_mc, loader):
    """-> dict(provider,id,name,via,names,error,versions)."""
    g, a = d["group"], d["artifact"]
    res = {"provider": None, "id": None, "name": None, "via": None, "slug": None, "error": None, "versions": None,
           "names": set()}
    res["names"].add(norm(a))
    if g == "curse.maven":
        m = re.match(r"^(.+)-(\d+)$", a)
        if m:
            res.update(provider="curseforge", id=m.group(2), slug=m.group(1), via="cursemaven")
            res["names"].add(norm(m.group(1)))
    elif g == "maven.modrinth":
        res.update(provider="modrinth", id=a, slug=a, via="modrinth-maven")
        res["names"].add(norm(a))
    else:
        variants = mod_names(a, g)
        for v in variants:
            res["names"].add(norm(v))
        for q in variants:
            try:
                hits = search(q.replace("-", " "))
            except RegError as e:
                res["error"] = f"search failed: {e}"
                return res
            want = {norm(v) for v in variants}
            for h in hits:
                if norm(h.get("name")) in want or any(norm(p.get("slug")) in want for p in h.get("providers", [])):
                    provs = {p["provider"]: p for p in h["providers"]}
                    p = provs.get("modrinth") or provs.get("curseforge") or h["providers"][0]
                    res.update(provider=p["provider"], id=p["id"], slug=p.get("slug"), name=h.get("name"), via="search")
                    res["names"].update({norm(h.get("name")), *(norm(x.get("slug")) for x in h["providers"])})
                    break
            if res["id"]:
                break
        if not res["id"]:
            res["error"] = "no exact name/slug match in the registry"
            return res
    try:
        res["versions"] = versions_fn(res["provider"], res["id"], loader, target_mc)
        res["name"] = res["versions"].get("name") or res["name"]
        res["names"].add(norm(res["versions"].get("slug")))
        res["names"].add(norm(res["versions"].get("name")))
    except RegError as e:
        res["error"] = f"versions lookup failed: {e}"
        # curseforge without a key: try the same slug on modrinth so existence is at least known
        if res["provider"] == "curseforge" and res.get("slug"):
            try:
                for h in search(res["slug"].replace("-", " ")):
                    for p in h.get("providers", []):
                        if p["provider"] == "modrinth" and norm(p.get("slug")) == norm(res["slug"]):
                            res["versions"] = versions_fn("modrinth", p["id"], loader, target_mc)
                            res["fallback_provider"] = "modrinth"
                            res["error"] += " (existence taken from modrinth)"
                            raise StopIteration
            except StopIteration:
                pass
            except RegError:
                pass
    return res


def suggest(d, res, mc, vn_fn):
    """Suggested coordinate for the target in the author's own style (or None + reason)."""
    v = res.get("versions") or {}
    ch = v.get("chosen")
    if not v.get("has_native") or not ch:
        return {"coord": None, "change": None, "note": "no target build"}
    cl = ":" + d["classifier"] if d["classifier"] else ""
    keys = d["version_keys"]
    only_key = re.fullmatch(r"\$\{?([\w.]+)\}?", d["version_raw"] or "") if d["version_raw"] else None
    g, a = d["group"], d["artifact"]
    if res["via"] == "cursemaven":
        if res.get("fallback_provider"):
            return {"coord": None, "change": None, "note": "CurseForge file id unavailable (registry key/lookup); build exists on modrinth"}
        new = str(ch["fileId"])
        coord = f"{g}:{a}:{new}{cl}"
        change = f"gradle.properties: {only_key.group(1)}={d['version']} -> {new}" if only_key else f"build.gradle literal: {d['version']} -> {new}"
        return {"coord": coord, "change": change, "note": ch.get("fileName")}
    if res["via"] == "modrinth-maven":
        new = vn_fn(ch["fileId"])
        coord = f"{g}:{a}:{new}{cl}"
        change = f"gradle.properties: {only_key.group(1)}={d['version']} -> {new}" if only_key else f"literal: {d['version']} -> {new}"
        return {"coord": coord, "change": change, "note": ch.get("fileName")}
    # author's own maven: artifact + version from the registry file name
    art = a
    if loader_swap_needed(art):
        art = re.sub(r"(?<![a-z])forge(?![a-z])", "neoforge", art)
    art = re.sub(r"(?<![\d.])1\.\d{2}(?:\.\d+)?(?![\d.])", mc, art)
    fn = re.sub(r"\.jar$", "", ch.get("fileName") or "")
    note = ch.get("fileName")
    if fn.lower().startswith(art.lower() + "-"):
        ver = fn[len(art) + 1:]
    else:
        m = re.match(r"^(.*?)-(\d.*)$", fn)
        if not m:
            return {"coord": None, "change": None, "note": f"cannot derive a version from file name {fn!r}"}
        art, ver = m.group(1), m.group(2)
        note += " (artifact name taken from the jar file name; verify)"
    coord = f"{g}:{art}:{ver}{cl}"
    if only_key:
        change = f"gradle.properties: {only_key.group(1)}={d['version']} -> {ver}"
    elif keys:
        change = f"version uses {','.join(keys)}; set so the expansion is {ver}"
    else:
        change = f"literal: {d['version']} -> {ver}"
    if art != a:
        change += f"; artifact {a} -> {art}"
    return {"coord": coord, "change": change, "note": note}


def loader_swap_needed(art):
    return bool(re.search(r"(?<![a-z])forge(?![a-z])", art))


# ---------------------------------------------------------------------------------------------- dependency moves
def _dep_moves():
    import importlib.util
    sp = importlib.util.spec_from_file_location("dep_moves", ROOT / "tools/dep-moves.py")
    m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)
    return m


def old_source(d):
    """(provider, project id, file id) of the AUTHOR's version when the registry can serve it, else (None, reason)."""
    v = d.get("version") or ""
    if d["group"] == "curse.maven":
        m = re.match(r"^.+-(\d+)$", d["artifact"])
        return ("curseforge", m.group(1), v) if m and v.isdigit() else (None, "no numeric cursemaven file id")
    if d["group"] == "maven.modrinth" and v:
        rc, out, _ = _run(["curl", "-sS", "-m", "30", f"https://api.modrinth.com/v2/project/{d['artifact']}/version"], 40)
        try:
            for row in json.loads(out):
                if v in (row.get("id"), row.get("version_number")):
                    return "modrinth", d["artifact"], row["id"]
        except (ValueError, TypeError, AttributeError):
            pass
        return None, f"modrinth version {v!r} not found"
    return None, "author's own maven -- the registry cannot name the old file"


def fetch_jar(provider, pid, fid):
    """Download through modreg into the dep cache (reused when already there) -> (path|None, message)."""
    d = DEP_CACHE / f"{provider}-{pid}-{fid}"
    jars = sorted(d.glob("*.jar")) if d.is_dir() else []
    if not jars:
        d.mkdir(parents=True, exist_ok=True)
        rc, out, err = _run([sys.executable, str(MODREG), "download", "--provider", provider, "--id", str(pid),
                             "--file", str(fid), "--out", str(d)], 300)
        jars = sorted(d.glob("*.jar"))
        if rc != 0 or not jars:
            return None, ((err or out).strip().splitlines() or [f"exit {rc}"])[-1][:160]
    return jars[0], "ok"


def dep_moves(repo, deps, apply, fetch=fetch_jar, out=None):
    """Compare each mod's old jar with its target jar. Never fatal: every failure becomes a line."""
    res = {"pairs": [], "failures": [], "reports": [], "lines": []}
    pairs = []
    for d in deps:
        if d["kind"] != "mod" or not d.get("target") or not (d.get("suggested") or {}).get("coord"):
            continue
        name = f"{d['group']}:{d['artifact']}"
        if name in res["pairs"] or any(f.startswith(name + ":") for f in res["failures"]):
            continue                                              # declared twice (api + runtime): compare once
        oldsrc = old_source(d)
        if oldsrc[0] is None:
            res["failures"].append(f"{name}: old jar not fetched -- {oldsrc[1]}")
            continue
        ch = d["res"]["versions"]["chosen"]
        prov = d["res"].get("fallback_provider") or d["res"]["provider"]
        if (oldsrc[0], str(oldsrc[2])) == (prov, str(ch["fileId"])):
            continue                                              # the author's file IS the target file
        old, msg = fetch(*oldsrc)
        if old is None:
            res["failures"].append(f"{name}: old jar download failed -- {msg}")
            continue
        parts = d["suggested"]["coord"].split(":")
        g, a, v = parts[:3]
        new = LOCAL_MAVEN / g.replace(".", "/") / a / v / f"{a}-{v}.jar"
        if not new.is_file():
            new, msg = fetch(prov, d["res"]["id"], ch["fileId"])
            if new is None:
                res["failures"].append(f"{name}: target jar download failed -- {msg}")
                continue
        pairs.append((old, new)); res["pairs"].append(name)
    if pairs:
        try:
            res["reports"] = _dep_moves().analyse(repo, pairs, apply, res["lines"].append)
        except Exception as e:                                    # a broken jar must not fail the preflight
            res["failures"].append(f"dep-moves failed: {type(e).__name__}: {e}")
    return res


# ---------------------------------------------------------------------------------------------- main run
def run(repo, mc, loader="neoforge", fill_local=False, probe=curl_probe, search=None, versions_fn=None,
        vn_fn=modrinth_version_number, local_place=None, out=print, apply_moves=False, fetch=fetch_jar):
    repo = pathlib.Path(repo)
    bg = repo / "build.gradle"
    if not bg.is_file():
        bg = repo / "build.gradle.kts"
    if not bg.is_file():
        sys.exit(f"port-deps: no build.gradle in {repo}")
    props = read_props(repo / "gradle.properties")
    btxt = strip_comments(bg.read_text(encoding="utf-8", errors="replace"))
    props = {**gradle_vars(btxt), **props}
    sg = repo / "settings.gradle"
    stxt = strip_comments(sg.read_text(encoding="utf-8", errors="replace")) if sg.is_file() else ""

    search = search or (lambda q: modreg("search", "--query", q, "--loader", loader, "--mc", mc).get("results", []))
    versions_fn = versions_fn or (lambda p, i, l, m: modreg("versions", "--provider", p, "--id", i, "--loader", l, "--mc", m))

    # repositories: build.gradle (+ allprojects), settings.gradle (dependencyResolutionManagement = general, pluginManagement = plugin)
    repos = parse_repos(cut(btxt, [(s, e) for s, _, e in find_blocks(btxt, "publishing")]), props)  # publishing targets are not sources
    plug_spans = [(s, e) for s, _, e in find_blocks(stxt, "pluginManagement")]
    plug_txt = " ".join(stxt[bs:be] for _, bs, be in find_blocks(stxt, "pluginManagement"))
    repos += parse_repos(plug_txt, props, kind="plugin")
    repos += parse_repos(cut(stxt, plug_spans), props)
    if re.search(r"net\.minecraftforge\.gradle", btxt + stxt):
        repos.append({"url": "https://maven.minecraftforge.net/", "includes": [], "regex": [], "excludes": [], "kind": "implicit", "exclusive": False})
    if re.search(r"net\.neoforged", btxt + stxt):
        repos.append({"url": "https://maven.neoforged.net/releases/", "includes": [], "regex": [], "excludes": [], "kind": "implicit", "exclusive": False})
    repos.append({"url": "https://repo.maven.apache.org/maven2/", "includes": [], "regex": [], "excludes": [], "kind": "implicit", "exclusive": False})
    seen, uniq = {}, []
    for r in repos:
        k = r["url"].rstrip("/")
        if k in seen:
            if r["kind"] != "plugin" and seen[k]["kind"] in ("plugin", "implicit"):
                seen[k].update(kind=r["kind"], includes=r["includes"], regex=r["regex"], excludes=r["excludes"])
            continue
        seen[k] = r; uniq.append(r)
    repos = uniq

    deps, notes = parse_dependencies(btxt, props)
    toml = parse_mods_toml(repo, props)

    # 1. host reachability
    with concurrent.futures.ThreadPoolExecutor(8) as ex:
        codes = list(ex.map(lambda r: "local" if r["url"].startswith("file:") else probe(r["url"]), repos))
    for r, c in zip(repos, codes):
        r["http"], r["state"] = c, ("LOCAL" if c == "local" else host_state(c))

    # 2. classify + registry
    for d in deps:
        d["kind"] = classify(d)
        d["host_state"] = None
    mods = [d for d in deps if d["kind"] in ("mod", "mod-candidate")]

    def do_res(d):
        return resolve_mod(d, search, versions_fn, mc, loader)
    with concurrent.futures.ThreadPoolExecutor(4) as ex:
        for d, r in zip(mods, ex.map(do_res, mods)):
            d["res"] = r
            if r["id"] is None and d["kind"] == "mod-candidate":
                d["kind"] = "unresolved"
            elif r["id"] is not None:
                d["kind"] = "mod"

    # 3. where is each artifact served from
    def cand_repos(d):
        cs = [r for r in repos if r["kind"] != "plugin" and repo_accepts(r, d["group"])]
        if d["group"] == "curse.maven":
            cs = [r for r in cs if "cursemaven" in r["url"]] or cs
        if d["group"] == "maven.modrinth":
            cs = [r for r in cs if "modrinth" in r["url"]] or cs
        filt = [r for r in cs if r["includes"] or r["regex"]]
        return filt or [r for r in cs if r["kind"] != "implicit"] + [r for r in cs if r["kind"] == "implicit"]

    def do_art(d):
        if d["kind"] in ("platform", "tooling"):
            return ("n/a", [], [])
        if d["unresolved"] or not d["version"] or re.search(r"[\[\](),+*]", d["version"] or "") and "+" not in (d["version"] or ""):
            return ("UNRESOLVED", [], [])
        cs = cand_repos(d)
        if not cs:
            return ("NO-REPO", [], [])
        served, blocked, unknown, hosts = [], [], [], []
        for r in cs:
            hs = r["state"]
            u = artifact_url(r, d)
            c = retry(probe, u + ".pom")
            if c in ("404", "410"):
                c = retry(probe, u + ".jar")
            if c.startswith(("2", "3")):
                served.append(r["url"])
            elif c == "429" or c.startswith("5"):
                unknown.append(r["url"])  # rate-limited / server error: says nothing about availability
            elif c in ("403", "407", "000", "") and hs == "BLOCKED":
                blocked.append(r["url"])
            hosts.append(r["url"])
        st = "SERVED" if served else ("UNKNOWN" if unknown else "BLOCKED" if blocked else "NOT-FOUND")
        return (st, served or unknown or blocked or hosts, hosts)
    with concurrent.futures.ThreadPoolExecutor(8) as ex:
        arts = list(ex.map(do_art, deps))
    for d, (st, hosts, _) in zip(deps, arts):
        d["host_status"] = st
        d["hosts"] = [re.sub(r"^https?://([^/]+).*", r"\1", h) for h in hosts]

    # 4. requirement mapping + suggestions
    for d in deps:
        d["required"] = None
        if d["kind"] in ("mod",):
            names = d["res"]["names"] | {norm(x) for x in mod_names(d["artifact"], d["group"])}
            hit = [m for m in toml if norm(m) in names and norm(m) not in ("forge", "neoforge", "minecraft")]
            d["required"] = ("required" if any(toml[m] for m in hit) else "optional") if hit else "unknown"
            d["toml_modid"] = hit[0] if hit else None
            d["target"] = d["res"]["versions"]["has_native"] if d["res"].get("versions") else None
            d["suggested"] = suggest(d, d["res"], mc, vn_fn) if d["res"].get("versions") else {"coord": None, "change": None, "note": d["res"].get("error")}
    mapped = {d.get("toml_modid") for d in deps if d.get("toml_modid")}
    unmapped_toml = sorted(m for m in toml if m not in mapped and m not in ("forge", "neoforge", "minecraft"))

    # 5. fill-local
    filled = []
    if fill_local:
        place = local_place or _local_maven
        done = set()
        for d in deps:
            if d["kind"] == "mod" and d["host_status"] == "BLOCKED" and d.get("target") and d["suggested"]["coord"]:
                c = d["suggested"]["coord"]
                if c in done:
                    continue
                done.add(c)
                ch = d["res"]["versions"]["chosen"]
                prov = d["res"].get("fallback_provider") or d["res"]["provider"]
                ok, msg = place(c, prov, d["res"]["id"], ch["fileId"])
                d["filled"] = ok
                filled.append({"coord": c, "ok": ok, "msg": msg})

    # 5b. jars of the author's version vs the target's: classes that moved
    moves = dep_moves(repo, deps, apply_moves, fetch) if (fill_local or apply_moves) else None

    # 6. report + exit code
    req_missing = [d for d in deps if d["kind"] == "mod" and d["required"] == "required" and d.get("target") is False]
    blocked_noreg = [d for d in deps if d["host_status"] == "BLOCKED" and d["kind"] in ("library", "unresolved")]
    reg_err = [d for d in deps if d["kind"] in ("mod", "unresolved") and d.get("res", {}).get("error") and d["kind"] == "mod" and not d["res"].get("versions")]
    code = 3 if req_missing else 4 if blocked_noreg else 2 if reg_err else 0
    report = render(repo, mc, loader, repos, deps, notes, unmapped_toml, filled, req_missing, blocked_noreg, reg_err, code, out, moves)
    return code, report


def _local_maven(coord, provider, pid, fid):
    rc, o, e = _run([sys.executable, str(LOCALMAVEN), coord, "--provider", provider, "--id", str(pid), "--file", str(fid)], 300)
    return rc == 0, (o or e).strip().splitlines()[-1][:200] if (o or e).strip() else ""


def render(repo, mc, loader, repos, deps, notes, unmapped_toml, filled, req_missing, blocked_noreg, reg_err, code, out, moves=None):
    out(f"port-deps: {repo}  ->  {loader} {mc}")
    out("\nREPOSITORIES (reachability from this machine)")
    for r in repos:
        f = ""
        if r["includes"] or r["regex"]:
            f = "  [only " + ",".join(r["includes"] + r["regex"]) + "]"
        out(f"  {r['state']:9} {r['http']:>3}  {r['url']}  ({r['kind']}){f}")
    out("\nDEPENDENCIES")
    rows = [("dependency", "kind", "scope", "req", "host", "target build", "suggested coordinate")]
    for d in deps:
        if d["kind"] == "platform":
            continue
        dep = ":".join(x for x in (d["group"], d["artifact"], d["version"] or "?", d["classifier"]) if x)
        tb = "-"
        sug = "-"
        if d["kind"] == "mod":
            tb = "yes" if d.get("target") else ("NO" if d.get("target") is False else "?")
            sug = d["suggested"]["coord"] or ("(" + str(d["suggested"]["note"]) + ")")
        elif d["kind"] == "unresolved":
            tb = "no registry"
        elif d["kind"] == "library":
            tb = "library"
        hs = d["host_status"] + (" " + ",".join(d["hosts"][:1]) if d["hosts"] and d["host_status"] != "n/a" else "")
        rows.append((dep, d["kind"], d["scope"], {"required": "req", "optional": "opt", "unknown": "?", None: "-"}[d["required"]], hs, tb, sug))
    w = [min(max(len(r[i]) for r in rows), 60) for i in range(7)]
    for r in rows:
        out("  " + "  ".join(str(c)[:w[i]].ljust(w[i]) for i, c in enumerate(r)).rstrip())
    out("")
    shown = set()
    for d in deps:
        if d["kind"] == "mod" and d.get("suggested") and d["suggested"].get("change"):
            line = f"  change: {d['group']}:{d['artifact']}  {d['suggested']['change']}"
            if line not in shown:
                shown.add(line); out(line)
        if d["unresolved"]:
            out(f"  unresolved property in {d['raw']}: {', '.join(d['unresolved'])}")
        if d["kind"] == "mod" and d["res"].get("error"):
            out(f"  note: {d['artifact']}: {d['res']['error']}")
        if d["kind"] == "unresolved":
            out(f"  note: {d['group']}:{d['artifact']}: {d['res']['error']} -- not a registry mod (or not findable); treated as a library")
    for n in notes:
        out("  " + n)
    if unmapped_toml:
        out("  mods.toml dependency ids with no matching gradle dependency: " + ", ".join(unmapped_toml))
    for f in filled:
        out(f"  fill-local: {'OK ' if f['ok'] else 'FAILED '}{f['coord']}  {f['msg']}")
    for d in deps:
        if d["kind"] == "mod" and d.get("target") is False and d["required"] != "required":
            tag = "optional" if d["required"] == "optional" else "requirement UNKNOWN (no mods.toml mapping; treat as required until checked)"
            out(f"  NO TARGET BUILD ({tag}): {d['artifact']} -- decide: port the integration out, or keep compile-only against an older API")
        if d["kind"] == "mod" and d.get("target") and d["host_status"] == "BLOCKED" and not d.get("filled"):
            line = f"  blocked host, build exists: {d['artifact']} -- fillable with --fill-local"
            if line not in shown:
                shown.add(line); out(line)
    if req_missing:
        out("\n!!! DECISION NEEDED: REQUIRED mod dependency with NO build for " + mc + ":")
        for d in req_missing:
            v = d["res"]["versions"]
            out(f"!!!   {d['group']}:{d['artifact']}  (older builds: {v.get('has_older')}, newer: {v.get('has_newer')})")
    unk = [d for d in deps if d["kind"] == "mod" and d["required"] == "unknown" and d.get("target") is False]
    if unk:
        out("\n!!! requirement UNKNOWN and NO build for " + mc + " (exit stays 0 -- confirm required/optional by hand): "
            + ", ".join(f"{d['group']}:{d['artifact']} [{d['scope']}]" for d in unk))
    if blocked_noreg:
        out("\n!!! BLOCKED host serves something with no registry source (cannot fill locally):")
        for d in blocked_noreg:
            out(f"!!!   {d['group']}:{d['artifact']}:{d['version']}  via {','.join(d['hosts'])}")
    if reg_err:
        out("\n!!! registry lookup failed (preflight incomplete, NOT a pass): " + ", ".join(d["artifact"] for d in reg_err))
    if moves is not None:
        out("\nDEP MOVES (author's jar vs target jar)")
        for ln in moves["lines"]:
            out(ln)
        for ln in moves["failures"]:
            out("  not compared: " + ln)
        if not moves["pairs"] and not moves["failures"]:
            out("  no mod dependency with an old and a new jar to compare")
    out(f"\nexit {code}")
    return {"repo": str(repo), "mc": mc, "loader": loader, "exit": code,
            "repositories": [{k: r[k] for k in ("url", "kind", "http", "state", "includes", "regex")} for r in repos],
            "dependencies": [{
                "group": d["group"], "artifact": d["artifact"], "version": d["version"], "classifier": d["classifier"],
                "scope": d["scope"], "kind": d["kind"], "required": d["required"], "host_status": d["host_status"],
                "hosts": d["hosts"], "unresolved": d["unresolved"], "version_keys": d["version_keys"],
                "provider": (d.get("res") or {}).get("provider"), "id": (d.get("res") or {}).get("id"),
                "target_build": d.get("target"),
                "chosen": ((d.get("res") or {}).get("versions") or {}).get("chosen"),
                "suggested": d.get("suggested"), "filled": d.get("filled"),
                "registry_error": (d.get("res") or {}).get("error"),
            } for d in deps],
            "mods_toml_unmapped": unmapped_toml, "notes": notes, "fill_local": filled,
            "dep_moves": None if moves is None else {k: moves[k] for k in ("pairs", "failures", "reports")}}


# ---------------------------------------------------------------------------------------------- self-check
def self_check():
    fails = []

    def chk(name, cond):
        if not cond:
            fails.append(name)
    # comment stripping keeps URLs
    chk("strip", strip_comments('a "http://x" // c\nb /* z */ c') == 'a "http://x" \nb   c')
    chk("expand", expand("${a}-$b-${project.c}", {"a": "1", "b": "2", "c": "3"})[0] == "1-2-3")
    chk("names", mod_names("examplelib-neoforge-1.21.1", "x.y") [0] == "examplelib")
    chk("repo-regex", repo_accepts({"includes": [], "regex": [r"foo\.bar.*"], "excludes": []}, "foo.bar.baz"))

    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td)
        (repo / "src/main/resources/META-INF").mkdir(parents=True)
        (repo / "gradle.properties").write_text("minecraft_version=1.20.1\nexlib_version=2.0+1.20.1\nmixinx=0.3.5\n", encoding="utf-8")
        (repo / "settings.gradle").write_text("pluginManagement { repositories { gradlePluginPortal()\n maven { url = 'https://plug.example.test/' } } }\n", encoding="utf-8")
        (repo / "build.gradle").write_text('''
plugins { id 'net.minecraftforge.gradle' version '6.0' }
repositories {
    maven { url 'https://curse.example.test' }
    maven {
        name = "Author"
        url = "https://blocked.example.test/maven/"
    }
    maven {
        url = "https://filtered.example.test/m/"
        content { includeGroupByRegex("filt\\\\.ered.*") }
    }
    // maven { url 'https://commented.example.test' }
}
publishing { repositories { maven { url "file://${project.projectDir}/out" } } }
dependencies {
    minecraft "net.minecraftforge:forge:${minecraft_version}-47.3.0"
    implementation fg.deobf("filt.ered.gl:gl-forge-${minecraft_version}:1.0")
    compileOnly(fg.deobf("ex.exlib:exlib-forge:${exlib_version}:api"))
    runtimeOnly(fg.deobf("ex.exlib:exlib-forge:${exlib_version}"))
    implementation fg.deobf("curse.maven:reqmod-111:222")
    implementation fg.deobf('curse.maven:optmod-333:444')
    modImplementation group: 'ex.map', name: 'mapmod-forge', version: '1.5'
    implementation "maven.modrinth:rinthmod:ver1"
    implementation fg.deobf("curse.maven:movemod-666:700")
    implementation fg.deobf("curse.maven:movemod2-888:800")
    compileOnly(annotationProcessor("io.github.llamalad7:mixinextras-common:${mixinx}"))
    implementation(jarJar("io.github.llamalad7:mixinextras-forge:${mixinx}")) { jarJar.ranged(it, "[${mixinx},)") }
    implementation "weird.corp:mystery:3.0"
    annotationProcessor 'org.spongepowered:mixin:0.8.5:processor'
    // implementation "ex.commented:gone:1"
}
''', encoding="utf-8")
        (repo / "src/main/resources/META-INF/mods.toml").write_text('''
[[dependencies.${mod_id}]]
    modId="forge"
    mandatory=true
[[dependencies.${mod_id}]]
    modId="reqmod"
    mandatory=true
[[dependencies.${mod_id}]]
    modId="optmod"
    mandatory=false
[[dependencies.${mod_id}]]
    modId="exlib"
    mandatory=true
''', encoding="utf-8")

        def probe(u):
            if "blocked.example" in u or "curse.example" in u:
                return "403"
            if u.endswith(".pom") and "filtered.example" in u:
                return "200"
            if u.endswith(".pom") and "repo.maven.apache.org" in u and "mixinextras" in u:
                return "200"
            return "404"
        builds = {("curseforge", "111"): None, ("curseforge", "333"): None}

        def search(q):
            if q.lower().startswith("exlib"):
                return [{"name": "Other Thing", "providers": [{"provider": "modrinth", "id": "other", "slug": "other"}]},
                        {"name": "ExLib API", "providers": [{"provider": "modrinth", "id": "exlib", "slug": "exlib"}]}]
            if q.lower().startswith("mapmod"):
                return [{"name": "MapMod", "providers": [{"provider": "curseforge", "id": "555", "slug": "mapmod"}]}]
            return []

        def versions(p, i, l, m):
            tbl = {("curseforge", "111"): (False, "reqmod"), ("curseforge", "333"): (False, "optmod"),
                   ("modrinth", "exlib"): (True, "exlib-neoforge-9.5.1+1.21.1.jar"),
                   ("curseforge", "555"): (True, "mapmod-neoforge-2.0.jar"),
                   ("curseforge", "666"): (True, "movemod-1.jar"), ("curseforge", "888"): (True, "movemod2-1.jar"), ("modrinth", "rinthmod"): (True, "rinthmod-1.jar")}
            if (p, i) not in tbl:
                raise RegError("unknown")
            ok, fn = tbl[(p, i)]
            return {"name": i, "slug": i, "has_native": ok, "has_older": True, "has_newer": False,
                    "chosen": {"fileId": "999", "fileName": fn, "sha1": "x"} if ok else None}
        lines = []
        placed = []
        # dep-moves wiring: fake jars are enough (only entry names are read); one old-jar download fails
        import zipfile
        def mkjar(name, entries):
            j = pathlib.Path(td) / name
            with zipfile.ZipFile(j, "w") as z:
                for e in entries:
                    z.writestr(e, b"")
            return j
        jars = {("curseforge", "666", "700"): mkjar("old.jar", ["com/example/oldlib/Foo.class"]),
                ("curseforge", "666", "999"): mkjar("new.jar", ["com/example/newlib/Foo.class"])}
        fetched = []

        def fake_fetch(p, i, f):
            fetched.append((p, i, f))
            return (jars[(p, i, f)], "ok") if (p, i, f) in jars else (None, "boom")
        usedir = repo / "src/main/java/com/example/mod"; usedir.mkdir(parents=True)
        (usedir / "Use.java").write_text("package com.example.mod;\nimport com.example.oldlib.Foo;\nclass Use { Foo f; }\n", encoding="utf-8")
        code, rep = run(repo, "1.21.1", probe=probe, search=search, versions_fn=versions, vn_fn=lambda v: "ver2",
                        fill_local=True, local_place=lambda c, p, i, f: (placed.append(c) or (True, "ok")), out=lines.append,
                        apply_moves=True, fetch=fake_fetch)
        dm = rep["dep_moves"]
        chk("moves-pair", dm["pairs"] == ["curse.maven:movemod-666"] and dm["reports"][0]["moved"][0]["new"] == "com.example.newlib.Foo")
        chk("moves-applied", "import com.example.newlib.Foo;" in (usedir / "Use.java").read_text(encoding="utf-8"))
        chk("moves-failure-not-fatal", any("movemod2-888" in f and "boom" in f for f in dm["failures"]) and code == 3)
        chk("moves-author-maven", any("mapmod-forge" in f and "author's own maven" in f for f in dm["failures"]))
        chk("moves-render", any("DEP MOVES" in l for l in lines))
        D = {(d["group"], d["artifact"], d["classifier"]): d for d in rep["dependencies"]}
        chk("exit3", code == 3)
        chk("commented", not any(d["group"] == "ex.commented" for d in rep["dependencies"]))
        chk("platform", D[("net.minecraftforge", "forge", None)]["kind"] == "platform")
        chk("library", D[("io.github.llamalad7", "mixinextras-forge", None)]["kind"] == "library")
        chk("wrapper", D[("io.github.llamalad7", "mixinextras-common", None)]["scope"] == "compileOnly+annotationProcessor")
        chk("jarjar", D[("io.github.llamalad7", "mixinextras-forge", None)]["scope"] == "implementation+jarJar")
        chk("map", ("ex.map", "mapmod-forge", None) in D and D[("ex.map", "mapmod-forge", None)]["scope"] == "modImplementation")
        chk("req", D[("curse.maven", "reqmod-111", None)]["required"] == "required" and D[("curse.maven", "reqmod-111", None)]["target_build"] is False)
        chk("opt", D[("curse.maven", "optmod-333", None)]["required"] == "optional")
        e = D[("ex.exlib", "exlib-forge", "api")]
        chk("exlib-sug", e["suggested"]["coord"] == "ex.exlib:exlib-neoforge:9.5.1+1.21.1:api")
        chk("exlib-key", "exlib_version=2.0+1.20.1 -> 9.5.1+1.21.1" in e["suggested"]["change"])
        chk("exlib-blocked", e["host_status"] == "BLOCKED")
        chk("exlib-req", e["required"] == "required")
        chk("filled", "ex.exlib:exlib-neoforge:9.5.1+1.21.1:api" in placed and "ex.exlib:exlib-neoforge:9.5.1+1.21.1" in placed)
        chk("filtered-served", D[("filt.ered.gl", "gl-forge-1.20.1", None)]["host_status"] == "SERVED")
        chk("mixinx-served", D[("io.github.llamalad7", "mixinextras-forge", None)]["host_status"] == "SERVED")
        chk("rinth", D[("maven.modrinth", "rinthmod", None)]["suggested"]["coord"] == "maven.modrinth:rinthmod:ver2")
        chk("map-sug", D[("ex.map", "mapmod-forge", None)]["suggested"]["coord"] == "ex.map:mapmod-neoforge:2.0")
        chk("unresolved-kind", D[("weird.corp", "mystery", None)]["kind"] == "unresolved")
        chk("plugin-repo", any(r["kind"] == "plugin" and "plug.example" in r["url"] for r in rep["repositories"]))
        chk("tooling", D[("org.spongepowered", "mixin", "processor")]["kind"] == "tooling")
        chk("no-publish-repo", not any(r["url"].startswith("file:") for r in rep["repositories"]))
        chk("json", json.loads(json.dumps(rep))["exit"] == 3)

        real_sleep = time.sleep
        time.sleep = lambda s: None
        try:
            chk("retry429", retry(lambda u: "429", "x") == "429")
        finally:
            time.sleep = real_sleep
        # exit 4: only an unresolvable dep on a blocked host
        (repo / "build.gradle").write_text('''
repositories { maven { url "https://blocked.example.test/maven/" } }
dependencies { implementation "weird.corp:mystery:3.0" }
''', encoding="utf-8")
        code4, _ = run(repo, "1.21.1", probe=lambda u: "403", search=lambda q: [], versions_fn=versions, out=lambda s: None)
        chk("exit4", code4 == 4)
        # exit 0: everything reachable and available
        (repo / "build.gradle").write_text('''
repositories { maven { url "https://ok.example.test/maven/" } }
dependencies { implementation "ex.exlib:exlib-forge:1.0" }
''', encoding="utf-8")
        code0, _ = run(repo, "1.21.1", probe=lambda u: "404" if u.endswith("/") else "200", search=search, versions_fn=versions, out=lambda s: None)
        chk("exit0", code0 == 0)
    print("self-check:", "OK" if not fails else "FAIL " + ", ".join(fails))
    return 0 if not fails else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo"); ap.add_argument("--mc"); ap.add_argument("--loader", default="neoforge")
    ap.add_argument("--fill-local", action="store_true"); ap.add_argument("--apply-moves", action="store_true"); ap.add_argument("--json")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not a.repo or not a.mc:
        ap.error("--repo and --mc are required")
    code, rep = run(a.repo, a.mc, a.loader, a.fill_local, apply_moves=a.apply_moves)
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(rep, indent=2), encoding="utf-8")
    return code


if __name__ == "__main__":
    sys.exit(main())
