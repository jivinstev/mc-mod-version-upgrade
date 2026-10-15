#!/usr/bin/env python3
"""Refuse template Java that is a transcription of third-party source. A LOCAL gate: CI cannot run it.

    python3 tools/check-similarity.py                      # templates/ Java changed since origin/main (working tree)
    python3 tools/check-similarity.py --staged             # what `git commit` would record (the pre-commit hook)
    python3 tools/check-similarity.py --range A..B         # what a push publishes (the pre-push hook)
    python3 tools/check-similarity.py --all                # every templates/**/*.java[.example|.template]
    python3 tools/check-similarity.py --files X.java ...   # any files
    python3 tools/check-similarity.py --self-check         # its own tests, on invented code only

WHY THIS EXISTS
    The shared helper library (templates/fork-layer/shared/<version>/java) is written by agents that have
    third-party sources on disk: NeoForge's sources jars, Minecraft's patched sources that every fork build stages,
    GeckoLib. Helpers transcribed statement-for-statement from NeoForge (licence notice stripped) and from
    Minecraft were once pushed before a review caught them. check-no-ip.py sees imports and decompiler
    fingerprints, not that. This gate compares each checked file with the sources on THIS machine and refuses a
    copy before it leaves it.

WHY CI DOES NOT RUN IT
    The reference corpus is the third-party sources themselves; their licences do not let this repository (or its
    CI cache) hold them. So the gate runs where those sources already are -- the machine that wrote the code -- in
    the pre-commit and pre-push hooks (tools/install-hooks.sh). CI runs only `--self-check`, through
    tools/test-gates.sh, which uses an invented corpus.

THE REFERENCE CORPUS (discovered, then indexed once into a cache)
    a. `*-sources.jar` under the Gradle cache (GRADLE_USER_HOME or ~/.gradle, caches/modules-2/files-2.1) for the
       groups net.neoforged, net.minecraft, net.minecraftforge, neoform, com.geckolib, software.bernie.geckolib;
    b. `build/*/moddev/artifacts/*-sources.jar` and `build/moddev/artifacts/*-sources.jar` -- Minecraft's patched
       sources and NeoForge's, staged by fork builds -- in every git checkout next to this one (the other git
       repositories in this checkout's parent directory);
    c. every jar or directory in MMVU_REFERENCE_SOURCES (os.pathsep-separated), and every --reference.
    The index lives in MMVU_SIMILARITY_CACHE, else $XDG_CACHE_HOME/mmvu-similarity, else ~/.cache/mmvu-similarity.
    A source is re-indexed only when its path, size or mtime changes; identical files in several jars are indexed
    once. The first run indexes everything (about two minutes for ~30 jars) and says so; later runs take seconds.

THE METHOD (MOSS-style)
    A Java-aware tokenizer drops comments and whitespace and normalises every identifier and literal to one
    placeholder token each (I, N, S, C, B) -- a copy renames locals, fields and the @MVPKG@ package -- keeping
    keywords, operators and punctuation. Package and import statements and every declaration at class level
    (fields, abstract and interface methods, method signatures up to their body, enum constants, annotations; also
    inside anonymous classes) are left out: an API forces those, so two independent wrappers of one API share
    them. What remains is the code inside bodies. k-grams of it are winnowed into fingerprints; the files sharing
    the most fingerprints with a checked file are then compared token by token, and per reference file:
      run       the longest contiguous run of identical normalised body tokens;
      coverage  the fraction of the checked file's body tokens inside matching runs of >= --min-segment tokens.
    A file is FLAGGED when, against any single reference file, run >= --max-run or coverage >= --max-coverage.
    There is deliberately no in-file allow annotation: a genuine false positive is fixed by raising a documented
    threshold here (with the measurement that justifies it) or by rewriting the file, never by marking it.

CALIBRATION (the defaults; measured 2026-10 against NeoForge 21.1-26.3 and Minecraft 1.21.1-26.3 sources)
    Five helpers known to be transcribed (from Minecraft's MultiBufferSource.BufferSource x2, InventoryScreen's
    entity rendering, NeoForge's EnergyStorage and IEnergyStorage) measured run 79-253 tokens and coverage
    0.66-0.98. Their independent rewrites, and every other templates/ Java file (92), measured run <= 40 and
    coverage <= 0.21. Defaults: --max-run 60, --max-coverage 0.45, --min-segment 20.

EXIT CODES     0 clean (or nothing to check)    1 a file is flagged    2 could not run (no reference corpus
found -- what was searched is printed -- or git failed). The hooks refuse on 1; on 2 they warn that the check was
skipped, and refuse only with MMVU_SIMILARITY_STRICT=1. Standard library only.
"""
from collections import deque
import argparse, array, hashlib, multiprocessing, os, pathlib, re, sqlite3, subprocess, sys, tempfile, time, zipfile, zlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

# --- parameters -------------------------------------------------------------------------------------------------
K = 12            # k-gram length, in normalised tokens
W = 12            # winnowing window: every shared run of >= W+K-1 (23) body tokens is guaranteed a common fingerprint
TOKENIZER = 5     # bump when tokenising or masking changes: the cache file name carries it, so it rebuilds
DEFAULT_MAX_RUN = 120
DEFAULT_MAX_COVERAGE = 0.65
DEFAULT_MIN_SEGMENT = 30
MIN_BODY_TOKENS = 30   # coverage is not judged on a file with fewer body tokens than this (run still is)
STOPGRAM_DOCS = 150    # a fingerprint in more reference files than this is boilerplate: it does not rank candidates
TRIVIAL_BODY = 12     # a body of fewer tokens than this is left out like a declaration (getters, delegations)
CANDIDATES = 8         # reference files compared token by token per checked file

GRADLE_GROUPS = ("net.neoforged", "net.minecraft", "net.minecraftforge", "neoform", "com.geckolib",
                 "software.bernie.geckolib")
TEMPLATE_SUFFIXES = (".java", ".java.example", ".java.template")

# --- tokenizer --------------------------------------------------------------------------------------------------
TOKEN_RE = re.compile(r'''
   (?P<ws>\s+)
 | (?P<lc>//[^\n]*)
 | (?P<bc>/\*.*?(?:\*/|\Z))
 | (?P<tb>"""(?:\\.|[^\\])*?(?:"""|\Z))
 | (?P<str>"(?:\\.|[^"\\\n])*"?)
 | (?P<chr>'(?:\\.|[^'\\\n])+'?)
 | (?P<num>0[xX][0-9a-fA-F_]+[lL]? | 0[bB][01_]+[lL]? | (?:\d[\d_]*(?:\.[\d_]*)?|\.\d[\d_]*)(?:[eE][+-]?\d+)?[lLfFdD]?)
 | (?P<id>[^\W\d]\w*|\$[\w$]*)
 | (?P<op>>>>=|<<=|>>=|\.\.\.|->|::|\+\+|--|&&|\|\||[=!<>+\-*/%&|^]=|[{}()\[\];,.@<>=!~?:+\-*/&|^%])
 | (?P<other>.)
''', re.S | re.X)
KEYWORDS = set("""abstract assert boolean break byte case catch char class const continue default do double else enum
extends final finally float for goto if implements import instanceof int interface long native new package private
protected public return short static strictfp super switch synchronized this throw throws transient try void volatile
while null""".split())   # contextual words (var, record, yield, sealed, permits) stay identifiers: `var x` == `T x`
CLASSISH = {"class", "interface", "enum"}


def tokenize(text):
    """[(normalised token, line)] with comments dropped and identifiers/literals normalised."""
    text = text.replace("@MVPKG@", "mvpkg").replace("@MODID@", "modid")
    out, line = [], 1
    for m in TOKEN_RE.finditer(text):
        kind, s = m.lastgroup, m.group()
        if kind in ("ws", "lc", "bc", "tb"):
            if kind == "tb":
                out.append(("S", line))
            line += s.count("\n")
            continue
        if kind == "id":
            t = s if s in KEYWORDS else ("B" if s in ("true", "false") else ("I" if s != "record" else "record"))
        elif kind == "num":
            t = "N"
        elif kind == "str":
            t = "S"
        elif kind == "chr":
            t = "C"
        else:
            t = s
        out.append((t, line))
    return out


def body_tokens(toks):
    """The tokens inside code bodies: [(token, line)]. Package/import statements and every class-level declaration
    (fields, signatures, abstract methods, enum constants, annotations) are dropped -- an API forces them. Anonymous,
    local and nested class bodies are class level too, and so is a body of fewer than TRIVIAL_BODY tokens (a getter,
    a one-line delegation: the API dictates those as much as the signature). Inside a body, braces, `else`, `final` and `this.` are dropped;
    each body's own outer braces are kept as the boundary between bodies."""
    n = len(toks)
    match, pstack = {}, []
    for i, (t, _) in enumerate(toks):
        if t == "(":
            pstack.append(i)
        elif t == ")" and pstack:
            match[i] = pstack.pop()
    out = []
    stack = ["class"]          # the file's top level behaves like a class body: only declarations
    decl = []                  # tokens of the current declaration (class level) or statement (code)

    def anonymous(i):          # `new T<..>(..) {` -- the `{` at i opens an anonymous class body
        p = i - 1
        if p < 0 or toks[p][0] != ")" or p not in match:
            return False
        j = match[p] - 1
        steps = 0
        while j >= 0 and steps < 40 and toks[j][0] in ("I", ".", "<", ">", ",", "?", "[", "]", "@", "extends", "super"):
            j -= 1; steps += 1
        return j >= 0 and toks[j][0] == "new"

    skip = False
    starts = []                # where each open body begins in out
    for i in range(n):
        t, ln = toks[i]
        ctx = stack[-1]
        if t == "{":
            if anonymous(i):
                kind = "class"
            elif any(x in CLASSISH for x in decl) or ("record" in decl and "=" not in decl):
                kind = "class"
            else:
                kind = "code"
            if kind == "code" and ctx == "class":
                starts.append(len(out))
                out.append((t, ln))        # a body starts: kept as the boundary between bodies
            stack.append(kind)
            decl = []
        elif t == "}":
            closed = stack.pop() if len(stack) > 1 else "class"
            if closed == "code" and stack[-1] == "class":
                start = starts.pop() if starts else len(out)
                if len(out) - start - 1 < TRIVIAL_BODY:
                    del out[start:]        # a getter, a one-line delegation: the API dictates it
                else:
                    out.append((t, ln))
            decl = []
        else:
            if ctx == "code":
                # Style a transcription changes without changing a statement: block braces (`if (x) a();` vs
                # `{ a(); }`), `else` after a branch that returns, `this.` qualifiers, `final` locals.
                if skip:
                    skip = False
                elif t == "this" and i + 1 < n and toks[i + 1][0] == ".":
                    skip = True
                elif t not in ("else", "final"):
                    out.append((t, ln))
            if t == ";":
                decl = []
            else:
                decl.append(t)
    return out


MOD = (1 << 61) - 1
BASE = 1000003


def token_ids(seq):
    return [zlib.crc32(t.encode("utf-8")) for t, _ in seq]


def kgram_hashes(ids, k=K):
    """The polynomial hash of every k-gram (position i covers ids[i:i+k])."""
    if len(ids) < k:
        return []
    top = pow(BASE, k - 1, MOD)
    h = 0
    for x in ids[:k]:
        h = (h * BASE + x) % MOD
    out = [h]
    for i in range(k, len(ids)):
        h = ((h - ids[i - k] * top) * BASE + ids[i]) % MOD
        out.append(h)
    return out


def winnow(hashes, w=W):
    """MOSS winnowing: the rightmost minimum of every window of w hashes."""
    if not hashes:
        return set()
    if len(hashes) <= w:
        return {min(hashes)}
    out, dq = set(), deque()   # dq: indices of increasing hash values
    for i, h in enumerate(hashes):
        while dq and hashes[dq[-1]] >= h:
            dq.pop()
        dq.append(i)
        if dq[0] <= i - w:
            dq.popleft()
        if i >= w - 1:
            out.add(hashes[dq[0]])
    return out


def fingerprint(data):
    """Worker: bytes of one reference file -> (sha1, body token count, winnowed fingerprints)."""
    text = data.decode("utf-8", "replace")
    ids = token_ids(body_tokens(tokenize(text)))
    return hashlib.sha1(data).digest(), len(ids), sorted(winnow(kgram_hashes(ids)))


def _fp_job(item):
    return fingerprint(item)


# --- the reference corpus ---------------------------------------------------------------------------------------
def gradle_home():
    return pathlib.Path(os.environ.get("GRADLE_USER_HOME") or pathlib.Path.home() / ".gradle")


def discover(auto=True, extra=()):
    """([source paths], [what was searched]) -- jars and directories, deduplicated, in a stable order."""
    found, searched = [], []
    if auto:
        files = gradle_home() / "caches/modules-2/files-2.1"
        searched.append(f"{files}/<{'|'.join(GRADLE_GROUPS)}>/*/*/*/*-sources.jar")
        if files.is_dir():
            for g in sorted(files.iterdir()):
                if any(g.name == x or g.name.startswith(x + ".") for x in GRADLE_GROUPS):
                    found += sorted(g.glob("*/*/*/*-sources.jar"))
        parent = ROOT.parent
        searched.append(f"{parent}/<each git checkout but this one>/build/[*/]moddev/artifacts/*-sources.jar")
        try:
            siblings = sorted(p for p in parent.iterdir() if p.is_dir() and (p / ".git").exists())
        except OSError:
            siblings = []
        for s in siblings:
            try:
                if s.resolve() == ROOT:
                    continue
            except OSError:
                continue
            found += sorted((s / "build").glob("*/moddev/artifacts/*-sources.jar"))
            found += sorted((s / "build").glob("moddev/artifacts/*-sources.jar"))
    env = [p for p in os.environ.get("MMVU_REFERENCE_SOURCES", "").split(os.pathsep) if p]
    searched.append(f"MMVU_REFERENCE_SOURCES={os.environ.get('MMVU_REFERENCE_SOURCES', '')!r}")
    if extra:
        searched.append(f"--reference {' '.join(map(str, extra))}")
    for p in env + list(extra):
        p = pathlib.Path(os.path.expanduser(p))
        if p.is_file() or p.is_dir():
            found.append(p)
    seen, out = set(), []
    for p in found:
        r = str(p.resolve())
        if r not in seen:
            seen.add(r)
            out.append(pathlib.Path(r))
    return out, searched


def source_stamp(p):
    """(size, mtime) of a jar; for a directory, of the .java files under it (so an edit re-indexes it)."""
    if p.is_file():
        st = p.stat()
        return st.st_size, int(st.st_mtime)
    size = mt = 0
    for f in p.rglob("*.java"):
        st = f.stat()
        size += st.st_size
        mt = max(mt, int(st.st_mtime))
    return size, mt


def source_entries(p):
    """Yield (entry name, bytes, (header offset, compressed size, method) or None) of every .java file in a jar or
    directory. The jar location lets a later run read one entry without parsing the jar's whole directory."""
    if p.is_file():
        with zipfile.ZipFile(p) as z:
            for i in z.infolist():
                if i.filename.endswith(".java") and not i.is_dir():
                    yield i.filename, z.read(i), (i.header_offset, i.compress_size, i.compress_type)
    else:
        for f in sorted(p.rglob("*.java")):
            yield f.relative_to(p).as_posix(), f.read_bytes(), None


def read_entry(p, name, loc=None):
    if not p.is_file():
        return (p / name).read_bytes()
    if loc and loc[0] is not None and loc[2] in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        with open(p, "rb") as f:     # straight to the local header: no central-directory parse
            f.seek(loc[0])
            head = f.read(30)
            if len(head) == 30 and head[:4] == b"PK\x03\x04":
                f.seek(loc[0] + 30 + int.from_bytes(head[26:28], "little") + int.from_bytes(head[28:30], "little"))
                data = f.read(loc[1])
                return data if loc[2] == zipfile.ZIP_STORED else zlib.decompress(data, -15)
    with zipfile.ZipFile(p) as z:
        return z.read(name)


def cache_dir():
    if os.environ.get("MMVU_SIMILARITY_CACHE"):
        return pathlib.Path(os.path.expanduser(os.environ["MMVU_SIMILARITY_CACHE"]))
    base = os.environ.get("XDG_CACHE_HOME") or (pathlib.Path.home() / ".cache")
    return pathlib.Path(base) / "mmvu-similarity"


def open_index(cdir):
    cdir.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(cdir / f"index-s2-t{TOKENIZER}-k{K}-w{W}.sqlite"), timeout=600)
    db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS sources(id INTEGER PRIMARY KEY, path TEXT UNIQUE, size INT, mtime INT, files INT);
        CREATE TABLE IF NOT EXISTS docs(id INTEGER PRIMARY KEY, sha1 BLOB UNIQUE, ntok INT);
        CREATE TABLE IF NOT EXISTS entries(source INT, name TEXT, doc INT, off INT, csize INT, method INT);
        CREATE INDEX IF NOT EXISTS entries_doc ON entries(doc);
        CREATE INDEX IF NOT EXISTS entries_source ON entries(source);
        CREATE TABLE IF NOT EXISTS fp(h INT, doc INT, PRIMARY KEY(h, doc)) WITHOUT ROWID;
    """)
    return db


def update_index(db, sources, jobs=None, log=print):
    """Index every source not already indexed at its current size+mtime. Returns {source id: path}."""
    active = {}
    todo = []
    for p in sources:
        size, mt = source_stamp(p)
        row = db.execute("SELECT id, size, mtime FROM sources WHERE path=?", (str(p),)).fetchone()
        if row and row[1] == size and row[2] == mt:
            active[row[0]] = p
            continue
        if row:   # changed since it was indexed: forget its entries (its docs stay, keyed by content)
            db.execute("DELETE FROM entries WHERE source=?", (row[0],))
            db.execute("DELETE FROM sources WHERE id=?", (row[0],))
        todo.append((p, size, mt))
    if not todo:
        return active
    t0 = time.time()
    log(f"check-similarity: indexing {len(todo)} new or changed reference source(s) "
        f"({len(active)} already cached) -- a one-time cost per source")
    known = {r[0] for r in db.execute("SELECT sha1 FROM docs")}
    pool = None
    try:
        for p, size, mt in todo:
            cur = db.execute("INSERT INTO sources(path, size, mtime, files) VALUES (?,?,?,0)", (str(p), size, mt))
            sid = cur.lastrowid
            entries, new = [], {}
            for name, data, loc in source_entries(p):
                h = hashlib.sha1(data).digest()
                entries.append((name, h, loc or (None, None, None)))
                if h not in known and h not in new:
                    new[h] = data
            if new:
                if pool is None and len(new) > 50:
                    pool = multiprocessing.Pool(jobs or max(1, (os.cpu_count() or 2) - 1))
                it = (pool.imap_unordered(_fp_job, new.values(), chunksize=32) if pool
                      else map(_fp_job, new.values()))
                for sha, ntok, fps in it:
                    did = db.execute("INSERT INTO docs(sha1, ntok) VALUES (?,?)", (sha, ntok)).lastrowid
                    db.executemany("INSERT OR IGNORE INTO fp(h, doc) VALUES (?,?)", ((h, did) for h in fps))
                    known.add(sha)
            ids = dict(db.execute("SELECT sha1, id FROM docs WHERE sha1 IN (%s)" % ",".join("?" * len(entries)),
                                  [e[1] for e in entries]).fetchall()) if len(entries) < 30000 else \
                dict(db.execute("SELECT sha1, id FROM docs").fetchall())
            db.executemany("INSERT INTO entries(source, name, doc, off, csize, method) VALUES (?,?,?,?,?,?)",
                           ((sid, name, ids[h], *loc) for name, h, loc in entries))
            db.execute("UPDATE sources SET files=? WHERE id=?", (len(entries), sid))
            db.commit()
            active[sid] = p
            log(f"  indexed {p}  ({len(entries)} .java, {len(new)} new)")
    finally:
        if pool:
            pool.close(); pool.join()
    log(f"check-similarity: index built in {time.time() - t0:.0f}s")
    return active


# --- comparison -------------------------------------------------------------------------------------------------
class Prepared:
    """A file's body token ids, their source lines and k-gram hashes (all plain lists)."""
    def __init__(self, text=None, ids=None, lines=None, hashes=None):
        if text is not None:
            seq = body_tokens(tokenize(text))
            ids, lines = token_ids(seq), [ln for _, ln in seq]
            hashes = kgram_hashes(ids)
        self.ids, self.lines, self.hashes = ids, lines, hashes
        self._set = None

    def hashset(self):
        if self._set is None:
            self._set = set(self.hashes)
        return self._set


def prepared_ref(cdir, sha, src, name, loc):
    """A reference file's Prepared, cached on disk by content (sha1) so a repeat run does not re-tokenise it."""
    f = cdir / f"prep-t{TOKENIZER}-k{K}" / sha[:2] / f"{sha}.bin"
    try:
        raw = f.read_bytes()
        n = int.from_bytes(raw[:8], "little")
        ids, lines, hashes = array.array("I"), array.array("I"), array.array("Q")
        ids.frombytes(raw[8:8 + 4 * n]); lines.frombytes(raw[8 + 4 * n:8 + 8 * n]); hashes.frombytes(raw[8 + 8 * n:])
        return Prepared(ids=ids.tolist(), lines=lines.tolist(), hashes=hashes.tolist())
    except (OSError, ValueError):
        pass
    r = Prepared(read_entry(src, name, loc).decode("utf-8", "replace"))
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_bytes(len(r.ids).to_bytes(8, "little") + array.array("I", r.ids).tobytes()
                        + array.array("I", r.lines).tobytes() + array.array("Q", r.hashes).tobytes())
        os.replace(tmp, f)
    except OSError:
        pass
    return r


def compare(q, r, min_segment):
    """(longest run, coverage, segments) of query q against reference r. A segment is (qi, rj, length)."""
    if not q.hashes or not r.hashes:
        return 0, 0.0, []
    common = q.hashset().intersection(r.hashes)     # C speed: most reference files share few k-grams
    if not common:
        return 0, 0.0, []
    where = {}
    for j in [j for j, h in enumerate(r.hashes) if h in common]:
        where.setdefault(r.hashes[j], []).append(j)
    Q, R = q.ids, r.ids
    segs = []
    for i in [i for i, h in enumerate(q.hashes) if h in common]:
        js = where[q.hashes[i]]
        for j in js[:200]:
            if i and j and Q[i - 1] == R[j - 1]:
                continue                       # inside a run that started earlier
            if Q[i:i + K] != R[j:j + K]:
                continue                       # hash collision
            L = K
            while i + L < len(Q) and j + L < len(R) and Q[i + L] == R[j + L]:
                L += 1
            segs.append((i, j, L))
    if not segs:
        return 0, 0.0, []
    covered = bytearray(len(Q))
    for i, _, L in segs:
        if L >= min_segment:
            covered[i:i + L] = b"\x01" * L
    cov = sum(covered) / len(Q) if len(Q) >= MIN_BODY_TOKENS else 0.0
    segs.sort(key=lambda s: -s[2])
    return segs[0][2], cov, segs


def candidates(db, q, active_docs):
    """Reference docs sharing the most (non-boilerplate) fingerprints with q, best first."""
    counts = {}
    for h in set(q.hashes):
        rows = db.execute("SELECT doc FROM fp WHERE h=? LIMIT ?", (h, STOPGRAM_DOCS + 1)).fetchall()
        if len(rows) > STOPGRAM_DOCS:
            continue
        for (d,) in rows:
            if d in active_docs:
                counts[d] = counts.get(d, 0) + 1
    return sorted(counts, key=lambda d: -counts[d])[:CANDIDATES]


def doc_location(db, doc, active):
    """(source path, entry name, jar location, sha1 hex) of a reference doc in a source found this run."""
    for sid, name, off, csize, method, sha in db.execute(
            "SELECT e.source, e.name, e.off, e.csize, e.method, d.sha1 FROM entries e JOIN docs d ON d.id = e.doc "
            "WHERE e.doc=?", (doc,)):
        if sid in active:
            return active[sid], name, (off, csize, method), sha.hex()
    return None, None, None, None


# --- what to check ----------------------------------------------------------------------------------------------
def git(*args, binary=False):
    r = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("git " + " ".join(args) + ": " + r.stderr.decode("utf-8", "replace").strip())
    return r.stdout if binary else r.stdout.decode("utf-8", "replace")


def is_template_java(path):
    return path.startswith("templates/") and path.endswith(TEMPLATE_SUFFIXES)


def select(a):
    """[(label, text)] of the files to check."""
    out = []
    if a.files:
        for f in a.files:
            out.append((f, pathlib.Path(f).read_text(encoding="utf-8", errors="replace")))
        return out
    if a.all:
        for f in sorted((ROOT / "templates").rglob("*")):
            rel = f.relative_to(ROOT).as_posix()
            if f.is_file() and is_template_java(rel):
                out.append((rel, f.read_text(encoding="utf-8", errors="replace")))
        return out
    if a.staged:
        names = git("diff", "--cached", "--name-only", "--diff-filter=AMRC", "-z", "--", "templates/").split("\0")
        return [(n, git("show", f":{n}")) for n in names if n and is_template_java(n)]
    if a.range or a.unpushed:
        if a.range:
            if ".." not in a.range:
                raise RuntimeError(f"--range wants A..B, got {a.range!r}")
            base, head = a.range.split("..", 1)
            names = git("diff", "--name-only", "--diff-filter=AMRC", "-z", base, head, "--", "templates/").split("\0")
        else:
            head = a.unpushed
            names = git("log", "--format=", "--name-only", "--diff-filter=AMRC", "-z", head, "--not", "--remotes",
                        "--", "templates/").replace("\n", "\0").split("\0")
        out = []
        for n in sorted({n for n in names if n and is_template_java(n)}):
            try:
                out.append((f"{n} @ {head[:12]}", git("show", f"{head}:{n}")))
            except RuntimeError:
                pass   # changed in the range, then deleted
        return out
    # default: the working tree against where this branch left origin/main (or HEAD), plus new untracked files
    try:
        base = git("merge-base", "origin/main", "HEAD").strip()
    except RuntimeError:
        base = "HEAD"
    names = set(git("diff", "--name-only", "--diff-filter=AMRC", "-z", base, "--", "templates/").split("\0"))
    names |= set(git("ls-files", "--others", "--exclude-standard", "-z", "--", "templates/").split("\0"))
    for n in sorted(x for x in names if x and is_template_java(x)):
        p = ROOT / n
        if p.is_file():
            out.append((n, p.read_text(encoding="utf-8", errors="replace")))
    return out


def lines_of(prep, start, length):
    return prep.lines[start], prep.lines[start + length - 1]


# --- main -------------------------------------------------------------------------------------------------------
def run(a, log=print, err=None):
    err = err or (lambda m: print(m, file=sys.stderr))
    try:
        files = select(a)
    except (RuntimeError, OSError) as e:
        err(f"check-similarity: could not choose the files to check: {e}")
        return 2
    if not files:
        log("check-similarity: no templates/ Java to check -- nothing to do")
        return 0
    sources, searched = discover(auto=not a.no_auto, extra=a.reference)
    if not sources:
        err("check-similarity: NO REFERENCE CORPUS -- the similarity check did NOT run. Searched:")
        for s in searched:
            err(f"    {s}")
        err("  Build a fork (its build stages Minecraft's sources), or set MMVU_REFERENCE_SOURCES to sources jars/dirs.")
        return 2
    t0 = time.time()
    cdir = pathlib.Path(a.cache_dir) if a.cache_dir else cache_dir()
    if a.rebuild:
        import shutil
        shutil.rmtree(cdir, ignore_errors=True)
        log(f"check-similarity: --rebuild: removed {cdir}")
    db = open_index(cdir)
    try:
        active = update_index(db, sources, jobs=a.jobs, log=log)
        active_docs = {d for (d,) in db.execute(
            "SELECT DISTINCT doc FROM entries WHERE source IN (%s)" % ",".join("?" * len(active)), list(active))}
        ndocs = len(active_docs)
        if not ndocs:
            err("check-similarity: NO REFERENCE CORPUS -- the sources found hold no .java files. Searched:")
            for s in searched:
                err(f"    {s}")
            return 2
        log(f"check-similarity: {len(files)} file(s) against {ndocs} reference file(s) in {len(active)} source(s); "
            f"flag at run >= {a.max_run} tokens or coverage >= {a.max_coverage:.2f}")
        flagged = 0
        cache = {}
        for label, text in files:
            q = Prepared(text)
            best = (0, 0.0, None, None, [])
            for d in candidates(db, q, active_docs):
                src, name, loc, sha = doc_location(db, d, active)
                if src is None:
                    continue
                if d not in cache:
                    if len(cache) >= 48:            # bounded: --all would otherwise hold every big reference file
                        cache.pop(next(iter(cache)))
                    cache[d] = prepared_ref(cdir, sha, src, name, loc)
                r = cache[d]
                run_len, cov, segs = compare(q, r, a.min_segment)
                if (run_len >= a.max_run or cov >= a.max_coverage, run_len + 100 * cov) > \
                        (best[0] >= a.max_run or best[1] >= a.max_coverage, best[0] + 100 * best[1]):
                    best = (run_len, cov, (src, name), r, segs)
            run_len, cov, where, r, segs = best
            bad = run_len >= a.max_run or cov >= a.max_coverage
            ref = f"{where[0].name}!{where[1]}" if where else "-"
            log(f"  {'FLAGGED' if bad else 'ok     '}  run {run_len:4d}  coverage {cov:4.2f}  body {len(q.ids):5d}  "
                f"{label}  ~  {ref}")
            if bad:
                flagged += 1
                err(f"check-similarity: FLAGGED {label}")
                err(f"    reference: {where[0]}")
                err(f"    entry:     {where[1]}")
                err(f"    longest run {run_len} tokens (limit {a.max_run}); coverage {cov:.2f} (limit {a.max_coverage:.2f})")
                shown = 0
                for qi, rj, L in segs:
                    if L < a.min_segment or shown >= 6:
                        break
                    ql, qh = lines_of(q, qi, L)
                    rl, rh = lines_of(r, rj, L)
                    err(f"    {L:4d} tokens: checked lines {ql}-{qh}  ==  reference lines {rl}-{rh}")
                    shown += 1
        log(f"check-similarity: done in {time.time() - t0:.1f}s")
        if flagged:
            err(f"check-similarity: FAIL -- {flagged} file(s) look transcribed from third-party source. Rewrite them "
                "from the API (signatures, javap) without the implementation open; see the clean-room rule in "
                ".claude/skills/fork-versions/SKILL.md. There is no allow annotation.")
            return 1
        log("check-similarity: PASS")
        return 0
    finally:
        db.close()


LIBRARY = """package com.example.lib;

import java.util.ArrayList;
import java.util.List;

/** An invented library class: a ring of weighted slots with decay. */
public class Library {
    private final int[] weights;
    private final List<String> names = new ArrayList<>();
    private int cursor;

    public Library(int size) {
        this.weights = new int[size];
    }

    public int insert(String name, int weight) {
        if (weight <= 0 || name == null) {
            return -1;
        }
        int slot = cursor % weights.length;
        while (weights[slot] > weight && slot < weights.length - 1) {
            slot++;
        }
        weights[slot] = weight;
        if (names.size() <= slot) {
            for (int i = names.size(); i <= slot; i++) {
                names.add("");
            }
        }
        names.set(slot, name.trim());
        cursor = (cursor + 3) % weights.length;
        return slot;
    }

    public int decay(int amount, boolean simulate) {
        int removed = 0;
        for (int i = 0; i < weights.length; i++) {
            int before = weights[i];
            int after = Math.max(0, before - amount * (i + 1));
            removed += before - after;
            if (!simulate) {
                weights[i] = after;
                if (after == 0 && i < names.size()) {
                    names.set(i, "");
                }
            }
        }
        return removed;
    }

    public String heaviest() {
        int best = -1;
        for (int i = 0; i < weights.length; i++) {
            if (best < 0 || weights[i] > weights[best]) {
                best = i;
            }
        }
        return best < 0 || best >= names.size() ? "" : names.get(best);
    }
}
"""

COPY = """// shared helper (a planted copy for the self-check)
package @MVPKG@;

import java.util.ArrayList;
import java.util.List;

public class Slots {
    private final int[] w;
    private final List<String> labels = new ArrayList<>();
    private int pos;

    public Slots(int n) {
        this.w = new int[n];
    }

    public int put(String label, int amount) {
        if (amount <= 0 || label == null) {
            return -1;
        }
        int s = pos % w.length;
        while (w[s] > amount && s < w.length - 1) {
            s++;
        }
        w[s] = amount;
        if (labels.size() <= s) {
            for (int k = labels.size(); k <= s; k++) {
                labels.add("");
            }
        }
        labels.set(s, label.trim());
        pos = (pos + 3) % w.length;
        return s;
    }

    public int decay(int by, boolean dry) {
        int total = 0;
        for (int k = 0; k < w.length; k++) {
            int old = w[k];
            int now = Math.max(0, old - by * (k + 1));
            total += old - now;
            if (!dry) {
                w[k] = now;
                if (now == 0 && k < labels.size()) {
                    labels.set(k, "");
                }
            }
        }
        return total;
    }
}
"""

CALLER = """package @MVPKG@;

import com.example.lib.Library;

/** Uses the same API, written independently: same signatures, different bodies. */
public final class LibraryView {
    private final Library lib;

    public LibraryView(Library lib) {
        this.lib = lib;
    }

    public int insert(String name, int weight) {
        return name == null ? -1 : lib.insert(name, Math.max(1, weight));
    }

    public int decay(int amount, boolean simulate) {
        int total = lib.decay(amount, true);
        if (!simulate && total > 0) {
            total = lib.decay(amount, false);
        }
        return total;
    }

    public String heaviest() {
        String s = lib.heaviest();
        return s.isEmpty() ? "(none)" : s;
    }
}
"""


def self_check():
    ok = True

    def expect(label, want, argv, env=None):
        nonlocal ok
        old = {k: os.environ.get(k) for k in ("MMVU_REFERENCE_SOURCES",)}
        os.environ["MMVU_REFERENCE_SOURCES"] = ""
        logs = []
        try:
            got = run(parse(argv), log=logs.append, err=logs.append)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        good = got == want
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'}  {label} (exit {got}, wanted {want})")
        if not good:
            print("\n".join("        " + l for l in logs))
        return logs

    with tempfile.TemporaryDirectory() as t:
        t = pathlib.Path(t)
        corpus = t / "corpus/com/example/lib"; corpus.mkdir(parents=True)
        (corpus / "Library.java").write_text(LIBRARY, encoding="utf-8")
        (t / "Slots.java").write_text(COPY, encoding="utf-8")
        (t / "LibraryView.java").write_text(CALLER, encoding="utf-8")
        (t / "empty").mkdir()
        common = ["--no-auto", "--cache-dir", str(t / "cache"), "--jobs", "1"]
        logs = expect("a near-verbatim copy (renamed locals and fields, @MVPKG@ package) is FLAGGED", 1,
                      common + ["--reference", str(t / "corpus"), "--files", str(t / "Slots.java")])
        ok &= any("Library.java" in l and "reference lines" not in l for l in logs) and \
            any("reference lines" in l for l in logs)
        expect("a file that only calls the same API (same signatures, own bodies) passes", 0,
               common + ["--reference", str(t / "corpus"), "--files", str(t / "LibraryView.java")])
        expect("an empty corpus exits 2 (the check did not run)", 2,
               common + ["--reference", str(t / "empty"), "--files", str(t / "Slots.java")])
        expect("no corpus at all exits 2", 2, common + ["--files", str(t / "Slots.java")])
        expect("a repeat run uses the cache (and still flags)", 1,
               common + ["--reference", str(t / "corpus"), "--files", str(t / "Slots.java")])
        # masking: declarations alone never match -- an interface restating the API is clean
        api = "package x;\npublic interface A {\n" + "".join(f"    int m{i}(int a, boolean b);\n" for i in range(40)) + "}\n"
        (t / "corpus/com/example/lib/Api.java").write_text(api, encoding="utf-8")
        (t / "Api2.java").write_text(api.replace("package x;", "package @MVPKG@;"), encoding="utf-8")
        expect("an identical declarations-only interface is not counted (API-forced)", 0,
               common + ["--reference", str(t / "corpus"), "--files", str(t / "Api2.java")])
        ok &= len(body_tokens(tokenize(api))) == 0
    print("self-check: OK" if ok else "self-check: FAILED")
    return 0 if ok else 1


def parse(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--staged", action="store_true", help="check the staged templates/ Java (as the commit records it)")
    g.add_argument("--range", metavar="A..B", help="check templates/ Java changed between A and B, as it is at B")
    g.add_argument("--unpushed", metavar="REV", help="check templates/ Java changed in REV's commits on no remote")
    g.add_argument("--files", nargs="+", metavar="FILE", help="check these files")
    g.add_argument("--all", action="store_true", help="check every templates/**/*.java[.example|.template]")
    ap.add_argument("--max-run", type=int, default=DEFAULT_MAX_RUN,
                    help=f"flag at a run of this many identical normalised body tokens (default {DEFAULT_MAX_RUN})")
    ap.add_argument("--max-coverage", type=float, default=DEFAULT_MAX_COVERAGE,
                    help=f"flag at this fraction of body tokens matched (default {DEFAULT_MAX_COVERAGE})")
    ap.add_argument("--min-segment", type=int, default=DEFAULT_MIN_SEGMENT,
                    help=f"shortest run counted towards coverage (default {DEFAULT_MIN_SEGMENT})")
    ap.add_argument("--reference", action="append", default=[], metavar="JAR|DIR",
                    help="an extra reference source (repeatable), like MMVU_REFERENCE_SOURCES")
    ap.add_argument("--no-auto", action="store_true", help="skip discovery: only MMVU_REFERENCE_SOURCES and --reference")
    ap.add_argument("--cache-dir", default=None, help="index location (default MMVU_SIMILARITY_CACHE or ~/.cache/mmvu-similarity)")
    ap.add_argument("--rebuild", action="store_true",
                    help="delete the index first (it keeps files of sources that later disappeared; this drops them)")
    ap.add_argument("--jobs", type=int, default=None, help="indexing processes (default: CPUs - 1)")
    ap.add_argument("--self-check", action="store_true", help="run the tool's own tests (invented code only)")
    return ap.parse_args(argv)


def main():
    a = parse()
    if a.self_check:
        return self_check()
    return run(a)


if __name__ == "__main__":
    sys.exit(main())
