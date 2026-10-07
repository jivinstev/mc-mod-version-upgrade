"""
Backend providers for the mod-registry CLI: Modrinth (no key) and CurseForge
(x-api-key from .env.local or the environment). Both sit behind one `Provider`
interface so the rest of the pipeline is backend-agnostic.

ToS / security contract (see README.md):
  * No persistent cache. Providers make LIVE HTTP calls and return normalized
    dicts to the caller; nothing is written to disk here. (An in-process memo of
    a project's version list within a single CLI invocation is fine — it is
    discarded when the process exits.)
  * The CurseForge API key is read from .env.local, else from the process
    environment, and sent ONLY in the x-api-key header. It is never printed,
    logged, or included in any output or exception message.
  * The per-author CurseForge download opt-out (downloadUrl == null) is honored:
    download() returns {"status": "opt-out", ...} instead of trying to scrape.
"""
import json
import os
import urllib.parse
import urllib.request
import urllib.error
import ssl

# The python.org Python on macOS ships with an EMPTY certificate store until its "Install Certificates"
# step is run, so every HTTPS call fails with CERTIFICATE_VERIFY_FAILED -- and a search that fails reads as
# "no results". Fall back to certifi or the operating system's bundle when the default store is empty.
CA_BUNDLES = ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt")


def ca_bundle():
    """-> a CA bundle path to use, or None when Python's own store already works (or SSL_CERT_FILE is set)."""
    if os.environ.get("SSL_CERT_FILE"):
        return None
    if ssl.create_default_context().cert_store_stats().get("x509_ca", 0) > 0:
        return None
    try:
        import certifi
        return certifi.where()
    except ImportError:
        pass
    return next((b for b in CA_BUNDLES if os.path.isfile(b)), None)


_CTX = None


def ssl_context():
    global _CTX
    if _CTX is None:
        b = ca_bundle()
        _CTX = ssl.create_default_context(cafile=b) if b else ssl.create_default_context()
    return _CTX

MODRINTH_API = "https://api.modrinth.com/v2"
CURSEFORGE_API = "https://api.curseforge.com/v1"
MC_GAME_ID = 432
USER_AGENT = "mc-mod-version-upgrade/1.0 (+https://github.com/jivinstev/mc-mod-version-upgrade)"

# CurseForge modLoaderType enum (search filter) and the file-level modLoader enum
# (latestFilesIndexes / file.gameVersions) share these ids.
CF_LOADER_BY_NAME = {"forge": 1, "cauldron": 2, "liteloader": 3, "fabric": 4, "quilt": 5, "neoforge": 6}
CF_LOADER_NAME = {v: k for k, v in CF_LOADER_BY_NAME.items()}
# CurseForge relationType on file dependencies.
CF_RELATION = {1: "embedded", 2: "optional", 3: "required", 4: "tool", 5: "incompatible", 6: "include"}


def _http_json(url, headers=None, data=None, method=None):
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=60, context=ssl_context()) as r:
        return json.load(r)


def find_env_local(all_matches=False):
    """Walk up from cwd and this file's dir looking for a .env.local.

    Returns EVERY match in search order when all_matches is set, because the two
    repos each carry their own .env.local and they do not carry the same keys. This
    used to return the first file only, and then read just that one -- so running a
    migrator tool from a sibling mod's directory found that mod's .env.local (paths, no
    API key), stopped, and reported CURSEFORGE_API_KEY as "not set" while it sat in
    the migrator's own .env.local one directory over. A file that does not MENTION a
    key must not shadow a file that defines it.
    """
    seen = set()
    found = []
    starts = [os.getcwd(), os.path.dirname(os.path.abspath(__file__))]
    for start in starts:
        d = start
        while d and d not in seen:
            seen.add(d)
            cand = os.path.join(d, ".env.local")
            if os.path.isfile(cand):
                if not all_matches:
                    return cand
                found.append(cand)
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    return found if all_matches else None


def _clean_env_value(val):
    """Normalize a value from either source. Never printed by callers."""
    val = val.strip()
    if not val:
        return None
    if val.startswith("~"):
        val = os.path.expanduser(val)
    return val


def read_env_local_key(key):
    """Read a single key from the .env.local files, nearest first.

    Nearest-first keeps a machine-local override authoritative; falling through to
    the next file is what stops a .env.local that never mentions this key from
    hiding one that defines it. Never printed by callers.
    """
    for path in find_env_local(all_matches=True):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if line.startswith(f"{key}="):
                    val = _clean_env_value(line[len(key) + 1:])
                    if val:
                        return val
    return None


def read_env_key(key):
    """Read a single key from .env.local, else from the process environment.

    .env.local wins so a machine-local override stays authoritative. The
    environment fallback is what makes an ephemeral container work: there the
    variable is injected by the environment config and no .env.local exists,
    and without this the key reads as absent — which degrades silently
    (CurseForge is skipped and you get Modrinth-only results) rather than
    failing loudly. Never printed by callers.
    """
    return read_env_local_key(key) or _clean_env_value(os.environ.get(key) or "")


def write_download(out_path, file_name, data):
    """Write a downloaded file and return where it went. `--out` may be a file path, or an
    existing folder (or one written with a trailing slash), in which case the file keeps its
    own name inside it. Passing a folder used to end in a raw IsADirectoryError."""
    if out_path.endswith(("/", os.sep)) or os.path.isdir(out_path):
        if not file_name:
            raise ValueError("--out is a folder and the registry gave no file name: pass a file path")
        out_path = os.path.join(out_path, os.path.basename(file_name))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "wb") as out:
        out.write(data)
    return out_path


class Provider:
    """Common interface. loader/mc are always arguments — never constants."""
    name = "base"

    def search(self, query, loader=None, mc=None, limit=20):
        raise NotImplementedError

    def list_files(self, project_id):
        """Return list of normalized file dicts:
        {fileId, fileName, loaders:[str], mcs:[str], sha1:str|None,
         downloadUrl:str|None, datePublished:str|None}
        For CurseForge, downloadUrl/sha1 may be absent here (fetched in download())."""
        raise NotImplementedError

    def file_deps(self, project_id, file_id):
        """Return [{id, name, type}] where type in required|optional|embedded|incompatible|tool."""
        raise NotImplementedError

    def project(self, project_id):
        """Return {id, slug, name}."""
        raise NotImplementedError

    def download(self, project_id, file_id, out_path):
        """Return {status:'ok', path, sha1} or {status:'opt-out', websiteUrl} or raises."""
        raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# Modrinth
# ─────────────────────────────────────────────────────────────────────────────
class ModrinthProvider(Provider):
    name = "modrinth"

    def __init__(self):
        self._versions_memo = {}

    def _get(self, path):
        return _http_json(MODRINTH_API + path)

    def search(self, query, loader=None, mc=None, limit=20):
        facets = [["project_type:mod"]]
        if loader:
            facets.append([f"categories:{loader}"])
        if mc:
            facets.append([f"versions:{mc}"])
        q = urllib.parse.urlencode({
            "query": query, "limit": limit, "index": "relevance",
            "facets": json.dumps(facets),
        })
        res = self._get(f"/search?{q}")
        out = []
        for h in res.get("hits", []):
            out.append({
                "provider": self.name,
                "id": h["slug"],
                "slug": h["slug"],
                "name": h["title"],
                "downloads": h.get("downloads", 0),
                "description": h.get("description", "")[:200],
                "categories": h.get("categories", []),
                "url": f"https://modrinth.com/mod/{h['slug']}",
            })
        return out

    def _versions(self, project_id):
        if project_id not in self._versions_memo:
            self._versions_memo[project_id] = self._get(f"/project/{project_id}/version")
        return self._versions_memo[project_id]

    def list_files(self, project_id):
        out = []
        for v in self._versions(project_id):
            primary = None
            for f in v.get("files", []):
                if f.get("primary"):
                    primary = f
                    break
            if primary is None and v.get("files"):
                primary = v["files"][0]
            if primary is None:
                continue
            out.append({
                "fileId": v["id"],
                "fileName": primary.get("filename"),
                "loaders": v.get("loaders", []),
                "mcs": v.get("game_versions", []),
                "sha1": primary.get("hashes", {}).get("sha1"),
                "downloadUrl": primary.get("url"),
                "datePublished": v.get("date_published"),
            })
        return out

    def file_deps(self, project_id, file_id):
        for v in self._versions(project_id):
            if v["id"] == file_id:
                deps = []
                for d in v.get("dependencies", []):
                    dtype = d.get("dependency_type", "required")
                    pid = d.get("project_id")
                    name = pid
                    if pid:
                        try:
                            p = self._get(f"/project/{pid}")
                            name = p.get("slug", pid)
                        except Exception:
                            pass
                    deps.append({"id": name, "name": name, "type": dtype})
                return deps
        return []

    def project(self, project_id):
        p = self._get(f"/project/{project_id}")
        return {"id": p["slug"], "slug": p["slug"], "name": p["title"]}

    def download(self, project_id, file_id, out_path):
        import hashlib
        for v in self._versions(project_id):
            if v["id"] == file_id:
                f = next((x for x in v.get("files", []) if x.get("primary")), None) or v["files"][0]
                url = f["url"]
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=120, context=ssl_context()) as r:
                    data = r.read()
                out_path = write_download(out_path, f.get("filename"), data)
                sha1 = hashlib.sha1(data).hexdigest()
                expected = f.get("hashes", {}).get("sha1")
                return {"status": "ok", "path": out_path, "sha1": sha1,
                        "sha1_ok": (expected is None or expected == sha1), "expected_sha1": expected}
        raise ValueError(f"file {file_id} not found for {project_id}")


# ─────────────────────────────────────────────────────────────────────────────
# CurseForge
# ─────────────────────────────────────────────────────────────────────────────
class CurseForgeProvider(Provider):
    name = "curseforge"

    def __init__(self, api_key=None):
        # Key comes from .env.local or the environment; kept only in memory,
        # only ever sent as a header.
        self._key = api_key or read_env_key("CURSEFORGE_API_KEY")

    def available(self):
        return bool(self._key)

    def _headers(self):
        if not self._key:
            raise RuntimeError(
                "CURSEFORGE_API_KEY not set (looked in .env.local and the environment)"
            )
        return {"x-api-key": self._key}

    def _get(self, path):
        return _http_json(CURSEFORGE_API + path, headers=self._headers())

    def search(self, query, loader=None, mc=None, limit=20):
        params = {"gameId": MC_GAME_ID, "searchFilter": query, "classId": 6,
                  "sortField": 2, "sortOrder": "desc", "pageSize": min(limit, 50)}
        if loader and loader in CF_LOADER_BY_NAME:
            params["modLoaderType"] = CF_LOADER_BY_NAME[loader]
        if mc:
            params["gameVersion"] = mc
        q = urllib.parse.urlencode(params)
        res = self._get(f"/mods/search?{q}")
        out = []
        for m in res.get("data", []):
            out.append({
                "provider": self.name,
                "id": str(m["id"]),
                "slug": m.get("slug"),
                "name": m.get("name"),
                "downloads": int(m.get("downloadCount", 0)),
                "description": (m.get("summary") or "")[:200],
                "categories": [c.get("slug") for c in m.get("categories", [])],
                "url": (m.get("links") or {}).get("websiteUrl"),
            })
        return out

    # CF file loader is carried in gameVersions as a name string ("Forge"/"NeoForge"/"Fabric"/"Quilt").
    _LOADER_STRINGS = {"forge": "forge", "neoforge": "neoforge", "fabric": "fabric", "quilt": "quilt"}

    def list_files(self, project_id):
        # NOTE: `latestFilesIndexes` on /mods/{id} is NOT complete — it only lists the newest file per
        # (gameVersion, loader) slice the API chooses to surface, and it MISSES builds (e.g. one popular cave mod's
        # 1.21.1 file was absent, so a native mod mis-classified as needs-migrate). Page the full /files
        # list instead, parsing each file's gameVersions for both MC version(s) + loader(s).
        out = []
        index, page_size, max_pages = 0, 50, 6
        for _ in range(max_pages):
            res = self._get(f"/mods/{project_id}/files?index={index}&pageSize={page_size}")
            data = res.get("data", [])
            if not data:
                break
            for f in data:
                mcs, loaders = [], []
                for gv in f.get("gameVersions", []):
                    low = gv.lower()
                    if gv and gv[0].isdigit():
                        mcs.append(gv)
                    elif low in self._LOADER_STRINGS:
                        loaders.append(self._LOADER_STRINGS[low])
                sha1 = None
                for h in f.get("hashes", []):
                    if h.get("algo") == 1:
                        sha1 = h.get("value")
                out.append({
                    "fileId": f["id"], "fileName": f.get("fileName"),
                    "loaders": sorted(set(loaders)), "mcs": sorted(set(mcs)),
                    "sha1": sha1, "downloadUrl": f.get("downloadUrl"),
                    "datePublished": f.get("fileDate"),
                })
            total = res.get("pagination", {}).get("totalCount", 0)
            index += page_size
            if index >= total:
                break
        return out

    def _file(self, project_id, file_id):
        return self._get(f"/mods/{project_id}/files/{file_id}")["data"]

    def file_deps(self, project_id, file_id):
        f = self._file(project_id, file_id)
        deps = []
        for d in f.get("dependencies", []):
            rel = CF_RELATION.get(d.get("relationType"), "required")
            mod_id = d.get("modId")
            name = str(mod_id)
            try:
                dm = self._get(f"/mods/{mod_id}")["data"]
                name = dm.get("slug") or dm.get("name") or name
            except Exception:
                pass
            deps.append({"id": str(mod_id), "name": name, "type": rel})
        return deps

    def project(self, project_id):
        m = self._get(f"/mods/{project_id}")["data"]
        return {"id": str(m["id"]), "slug": m.get("slug"), "name": m.get("name")}

    def download(self, project_id, file_id, out_path):
        import hashlib
        f = self._file(project_id, file_id)
        url = f.get("downloadUrl")
        if not url:
            # Author opted out of third-party API distribution — honor it (ToS).
            m = self._get(f"/mods/{project_id}")["data"]
            return {"status": "opt-out",
                    "websiteUrl": (m.get("links") or {}).get("websiteUrl"),
                    "fileName": f.get("fileName")}
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=120, context=ssl_context()) as r:
            data = r.read()
        out_path = write_download(out_path, f.get("fileName"), data)
        sha1 = hashlib.sha1(data).hexdigest()
        # CF exposes hashes as [{value, algo}] where algo 1=Sha1, 2=Md5.
        expected = None
        for h in f.get("hashes", []):
            if h.get("algo") == 1:
                expected = h.get("value")
        return {"status": "ok", "path": out_path, "sha1": sha1,
                "sha1_ok": (expected is None or expected == sha1), "expected_sha1": expected}


def get_provider(name):
    if name == "modrinth":
        return ModrinthProvider()
    if name == "curseforge":
        return CurseForgeProvider()
    raise ValueError(f"unknown provider: {name}")
