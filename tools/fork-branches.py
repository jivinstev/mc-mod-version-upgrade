#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Stacked version branches: a multi-version fork as one real branch per Minecraft version (the default).

    python3 tools/fork-branches.py migrate     --repo DIR [--layer-ref REF] [--port-branch B] [--targets T,T] [--dry-run]
                                               [--json FILE]
    python3 tools/fork-branches.py verify      --repo DIR [--layer-ref REF] [--targets T,T] [--tip]
    python3 tools/fork-branches.py cascade     --repo DIR FROM..TO [--gates none|compile|ab|full] [--dry-run]
    python3 tools/fork-branches.py cascade     --repo DIR FROM..TO --plan|--apply [--budget-lines N] [--out DIR]
    python3 tools/fork-branches.py cascade     --repo DIR --apply-decisions FILE [--gates ...] [--out DIR]
    python3 tools/fork-branches.py review-authors --repo DIR --range A..B --reviewer WHO [--note N] [--verdict ok|not-ok]
    python3 tools/fork-branches.py add-target  --repo DIR --target T [--parent V] [--allow-undone-parent]
    python3 tools/fork-branches.py check-done  --repo DIR --target T
    python3 tools/fork-branches.py freeze      --repo DIR --target T [--upstream REMOTE/BRANCH] [--library owner/repo@TAG]
                                               [--accept-upstream-drift]
    python3 tools/fork-branches.py library-record --repo LIB --target T --dependent DIR [--slug owner/repo]
    python3 tools/fork-branches.py ci          --repo DIR --target T --modid ID [--run]
    python3 tools/fork-branches.py sync-shared --repo DIR [--target T ...]
    python3 tools/fork-branches.py --self-check

THE SHAPE. The port branch `neoforge-1.21.1` stays exactly what it is (a single-version port). Each further Minecraft
version is a branch of its own, made from the one below it along the chain: `neoforge-1.21.3` from `neoforge-1.21.1`,
`neoforge-1.21.4` from `neoforge-1.21.3`, ... `neoforge-26.3`. Each version branch holds the FULL source for its version
(no overlay, no -Pmc): its own gradle.properties versions, so a plain `./gradlew build` builds it, its own gates, its
own CI (tools/port-ci.py run on the branch: the single-version workflow at that version) and its own release (Port CI's
tag `<mod>-<ver>-mc<version>`). A fix made at a lower version reaches the higher ones by `git merge <lower>` into each
higher branch in turn (`cascade`); the authors' new code arrives the same way, merged into the port branch first.

WHAT A VERSION BRANCH CARRIES beside the source (nothing of .multiversion/):
  build.gradle               + one line, `apply from: '.port/branch.gradle'` (templates/fork-branches/branch.gradle):
                             the Java toolchain, the NeoForm runtime, dependency swaps and the client's game directory
                             from branch.properties; without that file it does nothing
  .port/branch.gradle        the same on every version branch
  .port/INVENTORY.json       the base build's behaviour inventory (tools/layer-inventory.py), built from the port branch
  .port/<version>/           THIS version's records -- per version, so merging a lower branch upward never touches them:
     branch.properties       minecraft_version, branch, parent, parent_minecraft_version, port_branch,
                             base_minecraft_version, mv_package, the upstream freeze (upstream, upstream_commit), library
                             pins (library.<owner>/<repo>=<tag>), library=true for a library fork, and the build keys a
                             layer target had: prop.* (also written into gradle.properties), toml.*, java_version,
                             nfrt_version, substitute.* / exclude.* / port_jar.* (tools/layer-deps.py's block),
                             strip_onlyin, mixin_add.<cfg> / mixin_remove.<cfg> (this version's mixin changes over its parent)
     renames.tsv             this version's own rename rows (reviewed); moves.generated.tsv (class moves, generated);
     shared-renames.tsv      the shared library's tables this version applies (generated); drops.txt; shared-helpers.txt
     GATES.md REVIEW.md STUBS.json COST.md DEPS.md INVENTORY-CHECK.json TEST-ITEMS.md DRIFT.md RESIDUE.md BEHAVIOUR.json
     REMERGE.json DEPENDENTS.json MIGRATED.json  -- the same records a layer target had, keyed by this branch's commits
  .port/<lower version>/     the lower versions' records as of the last merge (read-only copies; their own branch rules)
A single-version fork never gets any of it: only this tool writes .port/, and only on a version branch.

RENAME ROWS ARE A TOOL. The rows are kept per version so that when code is merged into a branch, each file the merge
changed that the rows alone made (`mechanical`: the version's file equals the rows over the port branch's file) is
re-made from the port branch's new file through the version's rows (rows_tree); a file a person changed is merged by
git, and a conflict there stops the cascade for a person.

DONE (target_done): the layer's list, keyed by this branch's commits -- a green Gate C in .port/<t>/GATES.md at a
commit HEAD contains with no jar-relevant change since; no unreviewed STUBS.json entry; REVIEW.md complete from a fresh
reviewer for the commit it names, with a row for every file that differs from the parent branch (at their merge-base;
the files the rows alone made, derived build settings, added helpers and the records excepted; resources by group);
no unresolved merge or re-merge; TEST-ITEMS.md; every base behaviour item accounted for, with the committed
INVENTORY-CHECK.json equal to a fresh check; no dependency built against a library that is not done or against a
development-only checkout jar; for a library (library=true) at least one dependent recorded as Gate C green against
this library's jar (DEPENDENTS.json, `library-record`). A record naming a LAYER commit (a migrated target) counts when
.port/<t>/MIGRATED.json maps that commit to a prepared-tree digest equal to the branch's own.

MIGRATE turns an existing layer fork into these branches: per target in chain order, a branch made (with git plumbing:
no checkout, no worktree of the person's is touched) from its parent's branch -- the first one from the port commit the
layer carries -- whose source is exactly what the layer's own `layer.py prepare` makes for that target, every source set
and the resources, with that target's records moved into .port/<t>/. It never pushes, never deletes or moves an
existing branch, and refuses a target branch that already exists with other content (one with the same content is
left as it is). `verify` re-runs the layer's prepare and compares; migrate runs it on every branch it made.

Exit codes: 0 ok; 1 a difference (verify) or a red branch (cascade); 18 not done (check-done); 2 bad input.
Standard library only.
"""
import argparse, datetime, hashlib, importlib.util, json, os, pathlib, re, shutil, subprocess, sys, tempfile, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
TPL = ROOT / "templates/fork-branches/branch.gradle"
PREPARE_SOURCES = ROOT / "templates/fork-layer/tools/prepare-sources.py"
SHARED_DEFAULT = ROOT / "templates/fork-layer/shared"


def shared_dir():
    """The shared library (templates/fork-layer/shared; MMVU_SHARED_DIR names another, for the self-check)."""
    return pathlib.Path(os.environ.get("MMVU_SHARED_DIR") or SHARED_DEFAULT)
REC = ".port"
BRANCH_PROPS = "branch.properties"
LAYER_HOOK_LINE = "apply from: '.multiversion/layer.gradle'"
BRANCH_HOOK_LINE = "apply from: '.port/branch.gradle'"
BRANCH_HOOK = ("\n// Version branch (community fork only; not the authors' code): reads .port/<minecraft_version>/branch.properties\n"
               "// for what gradle.properties cannot say (Java toolchain, dependency swaps, the client's game directory).\n"
               + BRANCH_HOOK_LINE + "\n")
UNDONE_EXIT = 18
COMMIT_ID = re.compile(r"^[0-9a-f]{7,40}$")
# the keys of branch.properties that change what is built (gates and reviews go stale when one changes); the others
# (the upstream freeze, library pins, the parent's name) are bookkeeping (issue #133, gap 2)
BUILD_KEY = re.compile(r"^(?:prop\.|toml\.|java_version$|nfrt_version$|substitute\.|exclude\.|port_jar\.|port_jar_dev\.|"
                       r"strip_onlyin$)")
# files of .port/<t>/ a review judged (a change to one makes REVIEW.md stale); everything else there is a record
REVIEWED_INPUTS = ("renames.tsv", "STUBS.json", "REMERGE.json", "moves.generated.tsv", "shared-renames.tsv", "drops.txt",
                   "shared-helpers.txt")
GATES_DOC = re.compile(r"^((?!src/)[^\n]*\.md|LICENSE[^/]*|\.github/.*)$")     # port-publish.py's: nothing in a jar
LIBRARY_PENDING = "library: no dependent has passed Gate C against this library version's jar yet"
DEV_JAR_MARK = "port_jar_dev."


def load(name, file):
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_MODS = {}


def _mod(key, file):
    if key not in _MODS:
        _MODS[key] = load(key, file)
    return _MODS[key]


def fp():
    return _mod("fork_port_fb", "fork-port.py")


def _ps():
    if "ps" not in _MODS:
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("prepare_sources_fb", PREPARE_SOURCES)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _MODS["ps"] = m
    return _MODS["ps"]


def git(repo, *args, input=None, env=None, check=False, binary=False):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, input=input,
                       env=env if env is not None else None, **({} if binary else {"text": True, "encoding": "utf-8",
                                                                                   "errors": "surrogateescape"}))
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])}: {(r.stderr or b'').strip()[-400:] if binary else r.stderr.strip()[-400:]}")
    return r


def mc_key(v):
    return tuple(int(x) if x.isdigit() else 0 for x in re.findall(r"\d+|[A-Za-z]+", v))


def branch_name(t):
    return f"neoforge-{t}"


def parse_props(text):
    out = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip().replace("\\:", ":")] = v.strip()
    return out


def read_file(p):
    p = pathlib.Path(p)
    return p.read_text(encoding="utf-8", errors="surrogateescape") if p.is_file() else ""


def set_props_text(text, updates):
    """`text` (a .properties file) with each key of updates set, every other line kept as it is; new keys appended.
    A CRLF file stays CRLF."""
    crlf = "\r\n" in (text or "")
    if crlf:
        text = text.replace("\r\n", "\n")
    lines = text.split("\n") if text else []
    trail = bool(lines) and lines[-1] == ""
    if trail:
        lines = lines[:-1]
    left = dict(updates)
    for i, line in enumerate(lines):
        if "=" in line and not line.lstrip().startswith("#"):
            k = line.split("=", 1)[0].strip()
            if k in left:
                lines[i] = f"{k}={left.pop(k)}"
    lines += [f"{k}={v}" for k, v in left.items()]
    out = "\n".join(lines) + "\n"
    return out.replace("\n", "\r\n") if crlf else out


# ---------------------------------------------------------------- what a checkout / tree is

def rec_dir(root, t):
    return pathlib.Path(root) / REC / t


def checkout_version(root):
    """The Minecraft version of a version branch checked out (or exported) at `root`: gradle.properties'
    minecraft_version when .port/<it>/branch.properties exists there; else None (the port branch, a single-version fork,
    a layer branch)."""
    mc = parse_props(read_file(pathlib.Path(root) / "gradle.properties")).get("minecraft_version")
    return mc if mc and (rec_dir(root, mc) / BRANCH_PROPS).is_file() else None


def info(root, t):
    return parse_props(read_file(rec_dir(root, t) / BRANCH_PROPS))


def chain(root, t):
    """t, its parent version, theirs ..., down to (not including) the port branch: read from the branch.properties
    copies in this tree (a version branch carries its ancestors' records). Nearest first."""
    out, v = [], t
    while v and v not in out:
        out.append(v)
        p = info(root, v)
        v = p.get("parent_minecraft_version") if p.get("parent") != p.get("port_branch") else None
    return out


def resolve_branch(repo, name):
    """A commit for branch `name`: the local branch, else origin's. None when neither exists."""
    for ref in (f"refs/heads/{name}", f"refs/remotes/origin/{name}"):
        r = git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        if r.returncode == 0:
            return r.stdout.strip()
    return None


def is_branch_fork(repo, t):
    """Has the clone a version branch for t (local or origin) carrying .port/<t>/branch.properties?"""
    c = resolve_branch(repo, branch_name(t))
    return bool(c) and git(repo, "cat-file", "-e", f"{c}:{REC}/{t}/{BRANCH_PROPS}").returncode == 0


# ---------------------------------------------------------------- trees and blobs

def tree_entries(repo, ref, prefix=None):
    """{path: (mode, blob)} of commit `ref` (optionally under `prefix`)."""
    r = git(repo, "ls-tree", "-r", "-z", "--full-tree", ref, *([prefix] if prefix else []), check=True)
    out = {}
    for ent in r.stdout.split("\0"):
        if not ent:
            continue
        meta, path = ent.split("\t", 1)
        mode, typ, blob = meta.split()
        if typ == "blob":
            out[path] = (mode, blob)
    return out


def cat_blobs(repo, blobs):
    """{blob: bytes} for each blob id, through one `git cat-file --batch`."""
    blobs = list(dict.fromkeys(blobs))
    if not blobs:
        return {}
    r = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch"], input=("\n".join(blobs) + "\n").encode(),
                       capture_output=True, check=True)
    data, out, i = r.stdout, {}, 0
    for b in blobs:
        j = data.index(b"\n", i)
        head = data[i:j].split()
        size = int(head[2])
        out[b] = data[j + 1:j + 1 + size]
        i = j + 1 + size + 1
    return out


def ref_files(repo, ref, prefix=None):
    """{path: bytes} of commit `ref` (optionally under prefix)."""
    ents = tree_entries(repo, ref, prefix)
    data = cat_blobs(repo, [b for _m, b in ents.values()])
    return {p: data[b] for p, (_m, b) in ents.items()}


def hash_blobs(files):
    """{path: blob id} of {path: bytes}, the ids git would store (no repository needed)."""
    out = {}
    for p, data in files.items():
        out[p] = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
    return out


def root_files(repo, root, work):
    """{path: bytes} of the tree at `root`: a work tree of `repo` (work=True: tracked and untracked-not-ignored files,
    each AS GIT WOULD STORE IT -- an unmodified file is its index blob, any other goes through git's clean filter, so a
    Windows checkout with core.autocrlf=true reads as the blobs do, never as CRLF bytes), or an exported directory
    (every file, the blobs' own bytes)."""
    root = pathlib.Path(root)
    if work:
        z = lambda *a: [x for x in git(root, *a, check=True).stdout.split("\0") if x]
        index = {}
        for ent in z("ls-files", "-z", "-s"):
            meta, path = ent.split("\t", 1)
            index[path] = meta.split()[1]
        dirty = set(z("ls-files", "-z", "-m")) | set(z("ls-files", "-z", "-o", "--exclude-standard"))
        blobs = {p: b for p, b in index.items() if p not in dirty and (root / p).is_file()}
        rest = sorted(p for p in dirty if (root / p).is_file())
        if rest:       # the clean form of an edited or new file: hash-object with the path's filters (written: catable)
            r = subprocess.run(["git", "-C", str(root), "hash-object", "-w", "--stdin-paths"], input="".join(f"{x}\n" for x in rest),
                               capture_output=True, text=True, encoding="utf-8", check=True)
            blobs.update(zip(rest, r.stdout.split()))
        data = cat_blobs(root, blobs.values())
        return {p: data[b] for p, b in sorted(blobs.items())}
    else:
        paths = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and ".git" not in p.relative_to(root).parts]
    out = {}
    for p in sorted(set(paths)):
        f = root / p
        if f.is_file():
            out[p] = f.read_bytes()
    return out


def root_modes(repo, root, ref, work):
    """{path: git mode} of the tree root_files reads: work=True, the index's modes, an edited or new file's from the
    file itself (its executable bit, where the file system has one); else commit `ref`'s."""
    if not work:
        return {p: m for p, (m, _b) in tree_entries(repo, ref).items()}
    root = pathlib.Path(root)
    z = lambda *a: [x for x in git(root, *a, check=True).stdout.split("\0") if x]
    out = {}
    for ent in z("ls-files", "-z", "-s"):
        meta, path = ent.split("\t", 1)
        out[path] = meta.split()[0]
    if os.name != "nt":
        for path in set(z("ls-files", "-z", "-m")) | set(z("ls-files", "-z", "-o", "--exclude-standard")):
            f = root / path
            if f.is_file() and not f.is_symlink():
                out[path] = "100755" if os.stat(f).st_mode & 0o111 else "100644"
    return out


def export(repo, ref, dest):
    """Write commit `ref`'s tree into `dest` (no worktree, no index of the clone's is touched)."""
    dest = pathlib.Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for p, data in ref_files(repo, ref).items():
        f = dest / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    return dest


# ---------------------------------------------------------------- rename rows as a tool

def rules_of(table_text):
    """prepare-sources.py's rules for a table text: (regex rules, token pattern, token table)."""
    ps = _ps()
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "t.tsv"
        f.write_text(table_text, encoding="utf-8")
        tokens, regexes = ps.split_rules(ps.load_renames(str(f)))
    pattern, table = ps.build_pattern(tokens)
    return regexes, pattern, table


def apply_rules(text, rules):
    """`text` rewritten exactly as prepare-sources.py rewrites an authors' file: regex/member rules first, in table
    order, then the type-token sweep; CRLF kept."""
    regexes, pattern, table = rules
    crlf = "\r\n" in text
    if crlf:
        text = text.replace("\r\n", "\n")
    for rx, repl, _ex in regexes:
        text = rx.sub(repl, text)
    if pattern is not None:
        text = pattern.sub(lambda m: table[m.group(1)], text)
    return text.replace("\n", "\r\n") if crlf else text


ONLYIN = re.compile(r"\n[ \t]*@OnlyIn\([^)]*\)[ \t]*")      # templates/fork-layer/tools/layer.py's


def apply_rules_to_files(paths, rules):
    """Apply `rules` to each file of `paths` in place, as bytes (line endings, and any byte that is not UTF-8, kept
    exactly). -> the number of files changed."""
    n = 0
    for f in paths:
        old = pathlib.Path(f).read_bytes()
        new = apply_rules(old.decode("utf-8", "surrogateescape"), rules).encode("utf-8", "surrogateescape")
        if new != old:
            pathlib.Path(f).write_bytes(new); n += 1
    return n


def chain_table(read, t, chain_):
    """The table a layer's prepare builds for t (fork rows nearest first: renames.tsv, then moves.generated.tsv, of
    each chain version; then the shared tables), from the .port/<v>/ files `read(path)` returns ("" when absent)."""
    rows = []
    for v in chain_:
        r = read(f"{REC}/{v}/renames.tsv")
        if r:
            rows.append(f"# ---- {v}\n" + r)
        m = read(f"{REC}/{v}/moves.generated.tsv")
        if m:
            rows.append(f"# ---- moves {v} (generated)\n" + m)
    s = read(f"{REC}/{t}/shared-renames.tsv")
    if s:
        rows.append(s)
    return "\n".join(rows) + "\n"


def shared_sections(text):
    """{'shared <v>': section text} of a shared-renames.tsv (sections start `# ---- shared <v>`)."""
    out, cur = {}, None
    for line in (text or "").split("\n"):
        m = re.match(r"^# ---- (shared \S+)", line)
        if m:
            cur = m.group(1)
            out[cur] = line + "\n"
        elif cur:
            out[cur] += line + "\n"
    return out


def own_table(read, t, parent):
    """The rows t adds over its parent version: its renames.tsv and moves.generated.tsv, and the shared tables its
    parent does not apply. What converts the parent's text to t's when the chain table cannot be used."""
    rows = []
    r = read(f"{REC}/{t}/renames.tsv")
    if r:
        rows.append(f"# ---- {t}\n" + r)
    m = read(f"{REC}/{t}/moves.generated.tsv")
    if m:
        rows.append(f"# ---- moves {t} (generated)\n" + m)
    mine, theirs = shared_sections(read(f"{REC}/{t}/shared-renames.tsv")), \
        shared_sections(read(f"{REC}/{parent}/shared-renames.tsv") if parent else "")
    rows += [v for k, v in mine.items() if k not in theirs]
    return "\n".join(rows) + "\n"


def drops_of(read, chain_):
    out = set()
    for v in chain_:
        for line in read(f"{REC}/{v}/drops.txt").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                out.add(line)
    return out


def rows_tree(repo, port_ref, read, t, chain_=None, strip=None):
    """{src/<set>/java/<rel>: bytes} -- every .java file of the port branch at `port_ref`, through t's chain table
    (and @OnlyIn stripped when t has strip_onlyin=true), the drops left out: what the rows alone make of the authors'
    code at t. A version's file equal to this is mechanical (no review row; re-made on a merge)."""
    chain_ = chain_ or [t]
    rules = rules_of(chain_table(read, t, chain_))
    drops = drops_of(read, chain_)
    strip = parse_props(read(f"{REC}/{t}/{BRANCH_PROPS}")).get("strip_onlyin") == "true" if strip is None else strip
    out = {}
    for path, data in ref_files(repo, port_ref, "src").items():
        m = re.match(r"^src/([^/]+)/java/(.+\.java)$", path)
        if not m or (m.group(1) == "main" and m.group(2) in drops):
            continue
        text = apply_rules(data.decode("utf-8", "surrogateescape"), rules)
        if strip:
            text = ONLYIN.sub("", text)
        out[path] = text.encode("utf-8", "surrogateescape")
    return out


def reader_of_root(root):
    root = pathlib.Path(root)
    return lambda p: read_file(root / p)


def reader_of_ref(repo, ref):
    def read(p):
        r = git(repo, "show", f"{ref}:{p}")
        return r.stdout if r.returncode == 0 else ""
    return read


# ---------------------------------------------------------------- derived files (no review row when exact)

def toml_rewrite(text, ranges):
    """layer.gradle's mvRewriteToml on a source file: versionRange of the [[dependencies.*]] block whose modId a key
    names ("minecraft", "neoforge") set to its range; every other line, and the line endings, kept."""
    out, mod = [], None
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        if body.strip().startswith("["):
            mod = None
        m = re.match(r'^\s*modId\s*=\s*"([^"]+)"', body)
        if m:
            mod = m.group(1)
        v = re.match(r'^(\s*versionRange\s*=\s*)"[^"]*"(.*)$', body)
        if mod is not None and mod in ranges and v:
            line = f'{v.group(1)}"{ranges[mod]}"{v.group(2)}' + line[len(body):]
        out.append(line)
    return "".join(out)


def mixin_spec(value):
    """`client:A,B;mixins:C` -> [(array, class)] (layer.py's form)."""
    out = []
    for spec in (value or "").split(";"):
        array, _, classes = spec.partition(":")
        out += [(array.strip(), c.strip()) for c in classes.split(",") if c.strip()]
    return out


def mixin_edit(obj, adds, removes):
    """layer.gradle's processResources edit on a parsed mixin config: each add appended to its array when absent, each
    remove taken out (ValueError when the config does not list it). Order kept."""
    obj = json.loads(json.dumps(obj))
    for array, c in adds:
        lst = list(obj.get(array) or [])
        if c not in lst:
            lst.append(c)
        obj[array] = lst
    for array, c in removes:
        lst = list(obj.get(array) or [])
        if c not in lst:
            raise ValueError(f"mixin remove names {array}:{c}, which the config does not list")
        obj[array] = [x for x in lst if x != c]
    return obj


def jload(x):
    """JSON from bytes or text that may start with a UTF-8 byte-order mark and need not be valid UTF-8 elsewhere."""
    if isinstance(x, bytes):
        x = x.decode("utf-8", "surrogateescape")
    return json.loads(x.lstrip("\ufeff"))


def json_like(obj, like):
    """`obj` as JSON in the indentation of `like` (the file it replaces), with its byte-order mark when it had one."""
    m = re.search(r"\n([ \t]+)\S", like or "")
    ind = m.group(1) if m else "  "
    ind = len(ind) if " " in ind else ind
    text = json.dumps(obj, indent=ind, ensure_ascii=False)
    if "\r\n" in (like or ""):
        text = text.replace("\n", "\r\n")
    nl = "\r\n" if "\r\n" in (like or "") else "\n"
    return ("\ufeff" if (like or "").startswith("\ufeff") else "") + text + (nl if (like or "\n").endswith("\n") else "")


def mixin_delta(base_obj, cur_obj):
    """(adds, removes) [(array, class)] that turn base_obj's arrays into cur_obj's."""
    adds, removes = [], []
    for array in sorted(set(base_obj) | set(cur_obj)):
        b, c = base_obj.get(array), cur_obj.get(array)
        if not (isinstance(b, list) or isinstance(c, list)):
            continue
        b, c = list(b or []), list(c or [])
        adds += [(array, x) for x in c if x not in b]
        removes += [(array, x) for x in b if x not in c]
    return adds, removes


def spec_text(pairs):
    by = {}
    for array, c in pairs:
        by.setdefault(array, []).append(c)
    return ";".join(f"{a}:{','.join(cs)}" for a, cs in by.items())


def derived_ok(path, base, cur, props):
    """Does `cur` (bytes) follow from the parent's `base` (bytes) through this version's build keys alone -- a file the
    tool writes, never a person's edit? gradle.properties (prop.*), neoforge.mods.toml (toml.*), a mixin config
    (mixin_add./mixin_remove.<file>), build.gradle (the hook line)."""
    if base is None or cur is None:
        return False
    name = path.rsplit("/", 1)[-1]
    b, c = base.decode("utf-8", "surrogateescape"), cur.decode("utf-8", "surrogateescape")
    if path == "gradle.properties":
        return set_props_text(b, {k[5:]: v for k, v in props.items() if k.startswith("prop.")}) == c
    if path == "build.gradle":
        return c in (b, b.rstrip("\n") + "\n" + BRANCH_HOOK)
    if name == "neoforge.mods.toml" and path.startswith("src/"):
        return toml_rewrite(b, {k[5:]: v for k, v in props.items() if k.startswith("toml.")}) == c
    if path.startswith("src/") and "/resources/" in path and name.endswith(".json") and \
            (f"mixin_add.{name}" in props or f"mixin_remove.{name}" in props):
        try:
            want = mixin_edit(jload(b), mixin_spec(props.get(f"mixin_add.{name}")),
                              mixin_spec(props.get(f"mixin_remove.{name}")))
            return jload(c) == want
        except ValueError:
            return False
    return False


# ---------------------------------------------------------------- what counts as a change

def jar_relevant(path, t):
    """Can a change to `path` change version t's jar? Everything but this branch's records (its branch.properties is
    judged by its build keys, other versions' record copies never), documents and CI files."""
    if path.startswith(REC + "/"):
        return path in (f"{REC}/branch.gradle", f"{REC}/{t}/{BRANCH_PROPS}")
    return not GATES_DOC.match(path)


def build_keys(props_text):
    return "\n".join(sorted(f"{k}={v}" for k, v in parse_props(props_text).items() if BUILD_KEY.match(k)))


def digests(files, t, modes=None):
    """(jar digest, review-inputs digest) of a version branch's tree {path: bytes}: what a gate run (the jar-relevant
    files and the build keys) and a review (also the reviewed inputs of .port/<t>/) judged. With `modes` ({path:
    mode}, a file without one 100644) a jar-relevant file's mode counts too, so an executable bit gained or lost
    stales the record (MIGRATED.json says "with_modes"; a record written without them is read as it was)."""
    blobs = hash_blobs(files)
    props = f"{REC}/{t}/{BRANCH_PROPS}"
    h = hashlib.sha256()
    for p in sorted(blobs):
        if jar_relevant(p, t) and p != props:
            mode = "" if modes is None else f"\0{modes.get(p, '100644')}"
            h.update(f"{p}\0{blobs[p]}{mode}\n".encode("utf-8", "surrogateescape"))
    h.update(build_keys(files.get(props, b"").decode("utf-8", "surrogateescape")).encode("utf-8"))
    r = hashlib.sha256()
    for name in REVIEWED_INPUTS:
        p = f"{REC}/{t}/{name}"
        if p in blobs:
            r.update(f"{p}\0{blobs[p]}\n".encode("utf-8"))
    return h.hexdigest(), r.hexdigest()


def changes_since(repo, root, t, c, ref, work):
    """Files that differ between commit c and the version branch at `root` (ref: its commit; work: root is the
    checkout, so edited and untracked files count too), with branch.properties counted only when its build keys
    changed. -> (jar-relevant, review-relevant) lists, or None when git cannot tell."""
    d = git(repo, "diff", "--name-only", c, ref)
    if d.returncode:
        return None
    names = {x for x in d.stdout.splitlines() if x}
    if work:
        w = git(root, "diff", "--name-only", "HEAD")
        u = git(root, "ls-files", "--others", "--exclude-standard", "--", "src", REC)
        if w.returncode or u.returncode:
            return None
        names |= {x for x in (w.stdout + "\n" + u.stdout).splitlines() if x}
    props = f"{REC}/{t}/{BRANCH_PROPS}"
    if props in names:
        old = git(repo, "show", f"{c}:{props}")
        if build_keys(old.stdout if old.returncode == 0 else "") == build_keys(read_file(pathlib.Path(root) / props)):
            names.discard(props)
    jar = sorted(n for n in names if jar_relevant(n, t))
    review = sorted(set(jar) | {n for n in names if n.startswith(f"{REC}/{t}/") and n.rsplit("/", 1)[-1] in REVIEWED_INPUTS})
    return jar, review


def migrated(root, t):
    try:
        return json.loads(read_file(rec_dir(root, t) / "MIGRATED.json") or "{}")
    except ValueError:
        return {}


def commit_current(repo, root, t, c, ref, work, kind, files=None, items=None):
    """(ok, why) for a record (kind "gates" or "review") naming commit c: c is ref or an ancestor of it with no
    jar-relevant (gates) / build-or-reviewed-input (review) change since; or c is a layer commit .port/<t>/MIGRATED.json
    maps to the digests the branch tree has now (an unchanged target that moved from the layer)."""
    if not COMMIT_ID.match(c or ""):
        return False, f"names {c!r}, which is not a commit id (7-40 hex digits)"
    known = git(repo, "rev-parse", "--verify", "--quiet", f"{c}^{{commit}}")
    if known.returncode == 0 and git(repo, "merge-base", "--is-ancestor", c, ref).returncode == 0:
        ch = changes_since(repo, root, t, c, ref, work)
        if ch is None:
            return False, f"cannot compare with {c} (git failed)"
        ch = ch[0] if kind == "gates" else ch[1]
        if ch and kind == "review" and items is not None and not set(ch) & {f for _k, f in items} \
                and not any(x.startswith(f"{REC}/{t}/") for x in ch):
            rng = authors_reviewed(repo, root, t, c, ref)      # the change is the authors' through the rows, reviewed once
            if rng:
                return True, f"current at {c}: the {len(ch)} file(s) changed since are the authors' change {rng} through " \
                             f"the rows, reviewed once for every version (AUTHORS-REVIEWS.md)"
        if ch:
            return False, f"{len(ch)} file(s) changed since {c}, e.g. {ch[0]}"
        return True, f"current at {c}"
    rec = migrated(root, t).get("recorded") or {}
    hit = next((v for k, v in rec.items() if k.startswith(c) or c.startswith(k)), None)
    if hit is None:
        return False, (f"names {c}, which is not an ancestor of this branch" if known.returncode == 0
                       else f"names {c}, which is not a commit in this clone") + " (and MIGRATED.json does not map it)"
    if hit.get("error"):
        return False, f"names layer commit {c}, whose prepared tree could not be digested at migration: {hit['error']}"
    files = files if files is not None else root_files(repo, root, work)
    jar, inputs = digests(files, t, root_modes(repo, root, ref, work) if hit.get("with_modes") else None)
    if jar != hit.get("digest"):
        return False, f"names layer commit {c}: the branch's tree no longer equals what the layer prepared there (migrated)"
    if kind == "review" and inputs != hit.get("review_inputs"):
        return False, f"names layer commit {c}: a reviewed input of .port/{t}/ changed since it was migrated"
    return True, f"current at layer commit {c} (migrated: same prepared tree)"


# ---------------------------------------------------------------- review items: the files that differ from the parent

def parent_base(repo, root, t, ref):
    """(parent version or None, commit of the parent branch this branch was last merged with) -- the merge-base of
    `ref` and the parent's branch tip, or ref's first parent when the parent's branch is not in this clone."""
    p = info(root, t)
    parent = p.get("parent") or ""
    tip = resolve_branch(repo, parent) if parent else None
    mb = git(repo, "merge-base", ref, tip).stdout.strip() if tip else ""
    if not mb:
        mb = git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^1").stdout.strip()
    pv = p.get("parent_minecraft_version") if parent != p.get("port_branch") else None
    return pv, mb or None


def helper_map(text):
    out = {}
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and not line.startswith("#"):
            out[parts[0]] = parts[1]
    return out


def helper_blobs(root, t):
    """{path: blob} of .port/<t>/shared-helpers.txt: the shared-library helpers this version carries, as installed."""
    return helper_map(read_file(rec_dir(root, t) / "shared-helpers.txt"))


def review_items(repo, root, t, ref, work, files=None, mechanical=None):
    """[(kind, file)] a fresh reviewer must give a verdict on for version t: every STUBS.json entry; every file that
    differs from the parent branch at their merge-base (`git diff <parent>..<branch>`) as a `changed` row -- resources
    as one `resources` row per group (its first three directories, with the group's digest) -- except the files the
    rows alone made (mechanical), build settings the tool derived (derived_ok), a .java file added at this version (a
    helper: no counterpart to read it against), a shared-library helper as installed (shared-helpers.txt), deletions,
    the records under .port/ and documents; plus .port/<t>/renames.tsv as one `renames` row (its digest)."""
    f_ = fp()
    files = files if files is not None else root_files(repo, root, work)
    blobs = hash_blobs(files)
    pv, mb = parent_base(repo, root, t, ref)
    base = tree_entries(repo, mb) if mb else {}
    base_data = None
    props = info(root, t)
    helpers = helper_blobs(root, t)
    mech = mechanical if mechanical is not None else mechanical_files(repo, root, t, ref, files)
    own, strip = None, props.get("strip_onlyin") == "true"
    pr = port_ref_of(repo, root, t, ref)
    authors = set(tree_entries(repo, pr, "src")) if pr else set()
    items = set()
    groups = {}
    gone_ok = None
    for p in sorted(set(blobs) | set(base)):
        if p.startswith(REC + "/") or GATES_DOC.match(p):
            continue
        if p not in blobs:
            # a file the parent has and this version does not: a `deleted` row, unless drops.txt or the version's
            # mixin_remove. key accounts for it (review finding M1: the layer never let a target lose a file silently)
            if gone_ok is None:
                gone_ok = accounted_deletions(root, t, files, props)
            if p not in gone_ok:
                items.add(("deleted", p))
            continue
        if p in base and base[p][1] == blobs[p]:
            continue
        if p.endswith(".java") and p not in authors:
            continue        # a class the authors do not have (a helper, an overlay-only file): no counterpart to read
        if helpers.get(p) == blobs[p] or p in mech:
            continue
        if p in base:
            if base_data is None:
                base_data = {}
            if p not in base_data:
                base_data[p] = cat_blobs(repo, [base[p][1]])[base[p][1]]
            if derived_ok(p, base_data[p], files[p], props):
                continue
            if re.match(r"^src/[^/]+/java/.+\.java$", p):
                # the parent's file through the rows this version adds (and @OnlyIn stripped from 1.21.6): an inherited
                # overlay the version's rows renamed, never a change of its own -- the layer asked no row for it either
                if own is None:
                    own = rules_of(own_table(reader_of_root(root), t, pv))
                text = apply_rules(base_data[p].decode("utf-8", "surrogateescape"), own)
                if (ONLYIN.sub("", text) if strip else text).encode("utf-8", "surrogateescape") == files[p]:
                    continue
        m = re.match(r"^(src/[^/]+)/resources/(.+)$", p)
        if m:
            groups.setdefault(m.group(1), []).append(("resources/" + m.group(2), blobs[p]))
        else:
            items.add(("changed", p))
    for prefix, entries in groups.items():
        items |= f_.review_file_items(prefix, entries)
    rn = f"{REC}/{t}/renames.tsv"
    if rn in blobs:
        items |= f_.review_file_items(f"{REC}/{t}", [("renames.tsv", blobs[rn])])
    try:
        stubs = json.loads(files.get(f"{REC}/{t}/STUBS.json", b"[]").decode("utf-8") or "[]")
    except ValueError:
        stubs = []
    items |= {("stub", e["file"]) for e in stubs if isinstance(e, dict) and e.get("file")}
    return sorted(items)


def accounted_deletions(root, t, files, props):
    """The paths version t may lack although its parent has them, each with its reason on record: the chain's
    drops.txt (src/main/java/<rel>), and each class a `mixin_remove.<config>` key takes off (its package from the
    config, any source set)."""
    out = {f"src/main/java/{rel}" for rel in drops_of(reader_of_root(root), chain(root, t))}
    for k, v in props.items():
        if not k.startswith("mixin_remove."):
            continue
        cfg = k[len("mixin_remove."):]
        pkgs = set()
        for p, data in files.items():
            if p.startswith("src/") and p.endswith("/" + cfg):
                try:
                    pkgs.add(json.loads(data.decode("utf-8-sig")).get("package") or "")
                except (ValueError, AttributeError):
                    pass
        for _array, c in mixin_spec(v):
            for pkg in pkgs - {""}:
                rel = pkg.replace(".", "/") + "/" + c.replace(".", "/") + ".java"
                out |= {f"src/{d.split('/')[1]}/java/{rel}" for d in files if d.startswith("src/") and d.count("/") > 1}
    return out


def port_ref_of(repo, root, t, ref):
    """The port branch commit this version branch carries (the merge-base of `ref` and the port branch)."""
    pb = info(root, t).get("port_branch") or branch_name(info(root, t).get("base_minecraft_version", "1.21.1"))
    tip = resolve_branch(repo, pb)
    if not tip:
        return None
    return git(repo, "merge-base", ref, tip).stdout.strip() or None


def mechanical_files(repo, root, t, ref, files):
    """The .java files of the tree the rows alone made: equal to rows_tree() over the port branch commit it carries."""
    pr = port_ref_of(repo, root, t, ref)
    if not pr:
        return set()
    rt = rows_tree(repo, pr, reader_of_root(root), t, chain(root, t))
    return {p for p, data in rt.items() if files.get(p) == data}


REVIEW_HEAD = ("# Review: Minecraft {t} (version branch {b})\n\n"
               "An independent review by a FRESH agent that was given only this version's changes: every file that differs\n"
               "from the parent branch ({p}) at their merge-base (`git diff <merge-base>..{b}`), read against the parent's\n"
               "file, its STUBS.json and .port/{t}/renames.tsv. One row per STUBS.json entry, per `changed` file, per\n"
               "resource group (`resources`: a directory with the digest of its changed files -- an edit makes it a new\n"
               "row) and for renames.tsv (`renames`, with its digest); the files the rename rows alone made, the build\n"
               "settings tools/fork-branches.py derives and added helper classes need no row; a file deleted here is a `deleted`\n"
               "row unless drops.txt or a mixin_remove key accounts for it. Verdict `ok` (the change keeps\n"
               "the behaviour, or the difference is right and the note says why) or `not-ok`. A hook item of the behaviour\n"
               "inventory is accounted for only by a row whose note names its id, or by an `item` row for the id; a\n"
               "reviewer may add `missing` (a file that should exist) and `shared` (a shared helper) rows. `reviewer` names\n"
               "the reviewing agent, distinct from the fixer; `commit` is the commit reviewed (`git rev-parse --short HEAD`).\n"
               "tools/fork-port.py adds a `?` row for every item it finds unreviewed; it never changes a verdict.\n\n"
               "- reviewer: ?\n- date: ?\n- commit: ?\n\n| kind | file | verdict | note |\n|---|---|---|---|\n")


def review_skeleton(repo, root, t, ref, work, unaccounted=()):
    """REVIEW.md with a `?` row for every item that has none (the header when new) and an `item` row for each
    unaccounted inventory id. Never changes a verdict. -> rows added."""
    f_ = fp()
    items = review_items(repo, root, t, ref, work)
    f = rec_dir(root, t) / "REVIEW.md"
    p = info(root, t)
    text = read_file(f) or REVIEW_HEAD.format(t=t, b=p.get("branch", branch_name(t)), p=p.get("parent", "?"))
    _fields, rows = f_.parse_review(text)
    add = [(k, x) for k, x in items if (k, x) not in rows] + [("item", i) for i in sorted(unaccounted) if ("item", i) not in rows]
    if add or not f.is_file():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text.rstrip("\n") + "\n" + "".join(f"| {k} | {x.replace('|', chr(92) + '|')} | ? | ? |\n" for k, x in add),
                     encoding="utf-8")
    return len(add)


def review_problems(repo, root, t, ref, work, files, items):
    f_ = fp()
    f = rec_dir(root, t) / "REVIEW.md"
    if not f.is_file():
        return [f"no REVIEW.md: {len(items)} item(s) need an independent reviewer's verdict (.port/{t}/REVIEW.md)"]
    fields, rows = f_.parse_review(read_file(f))
    out = [f"REVIEW.md has no {k}" for k in f_.REVIEW_FIELDS if fields.get(k, "?") in ("", "?")]
    if fields.get("date", "?") not in ("", "?") and not re.match(r"^\d{4}-\d\d-\d\d\b", fields["date"]):
        out.append(f"REVIEW.md date {fields['date']!r} is not YYYY-MM-DD")
    c = fields.get("commit", "?")
    if c not in ("", "?"):
        good, why = commit_current(repo, root, t, c, ref, work, "review", files, items)
        if not good:
            out.append(f"REVIEW.md {why}: re-review and name the new commit")
    for kind, file in items:
        v = rows.get((kind, file))
        if not v or v[0] not in f_.VERDICTS:
            out.append(f"no verdict: {kind} {file}")
        elif v[0] != "ok":
            out.append(f"reviewer says not-ok: {kind} {file} -- {v[1][:200]}")
        elif not v[1] or v[1] == "?":
            out.append(f"a verdict without a note: {kind} {file}")
    out += f_.extra_row_problems(rows, items)
    return out


def gates_problems(repo, root, t, ref, work, files):
    """[] when .port/<t>/GATES.md records a green Gate C for Minecraft t at a commit this branch carries with nothing
    jar-relevant changed since (or a layer commit MIGRATED.json maps to this tree)."""
    text = read_file(rec_dir(root, t) / "GATES.md")
    if not text:
        return ["no green Gate C recorded in GATES.md"]
    head = text.splitlines()[0]
    m = re.search(r"at `([^`]+)`", head)
    mm = re.search(r"for Minecraft (\S+)", head)
    if not m:
        return ["GATES.md names no commit it tested"]
    if "uncommitted" in m.group(1):
        return [f"GATES.md certifies an edited tree ({m.group(1)[:120]}), not a commit: commit, then re-run the full gates"]
    if not mm or mm.group(1) != t:
        return [f"GATES.md is for Minecraft {mm.group(1) if mm else '?'}, not {t}"]
    rows = [ln for ln in text.splitlines() if ln.startswith("|")]
    if any(re.search(r"\|\s*FAIL\s*\|", ln) for ln in rows):
        return ["GATES.md has a FAIL row"]
    if not any("Gate C" in ln and re.search(r"\|\s*PASS\s*\|", ln) for ln in rows):
        return ["no green Gate C recorded in GATES.md (A/B green is not done)"]
    good, why = commit_current(repo, root, t, m.group(1).split()[0], ref, work, "gates", files)
    return [] if good else [f"GATES.md {why}"]


def remerge_problems(repo, root, t, work, files=None):
    out = []
    if work:
        u = git(root, "ls-files", "-u")
        if u.stdout.strip():
            out.append("an unresolved merge in the checkout (git ls-files -u lists conflicted files): resolve or abort it")
        if git(root, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode == 0:
            out.append("a merge is in progress (MERGE_HEAD): finish or abort it")
    out += remerge_open(root, t, files if files is not None else root_files(repo, root, work))
    return out


def remerge_open(root, t, files):
    """The open entries of .port/<t>/REMERGE.json: a file the authors changed that this version could not take by
    itself (the cascade, or the layer's re-merge before the migration), still with the bytes it had then -- judged on
    the blob form ({path: bytes}), so a CRLF checkout reads as git stores it. -> one reason each."""
    f = rec_dir(root, t) / "REMERGE.json"
    if not f.is_file():
        return []
    rel = f"{REC}/{t}/REMERGE.json"
    try:
        conflicts = jload(f.read_bytes()).get("conflicts", [])
        assert isinstance(conflicts, list)
    except (ValueError, AttributeError, AssertionError):
        return [f"{rel} is not readable: cannot tell which files the authors' change did not merge into"]
    out = []
    for c in conflicts:
        path = str((c or {}).get("overlay", ""))
        if not path or path not in files:
            continue          # the file is gone: removed by a person, nothing left to merge into
        if hashlib.sha256(files[path]).hexdigest() == str(c.get("digest", "")).lower():
            out.append(f"the authors' change did not merge into {path} ({c.get('reason') or 'conflict'}, "
                       f"{c.get('since') or '?'}; {rel}): apply it by hand through this version's rows (the diff is in "
                       f"{rel}), commit, then remove the entry and re-review the file")
    return out


def inventory_problems(repo, root, t, ref, work, files, write, mechanical):
    """The behaviour inventory on version t (tools/layer-inventory.py account_branch): [] when every base item is
    accounted for AND the committed INVENTORY-CHECK.json says exactly what a fresh check says (issue #133, gap 1: the
    release-side check reads the committed file). write: rewrite the file (a run that holds the checkout)."""
    li = _mod("layer_inventory_fb", "layer-inventory.py")
    code, rep, lines = li.check_branch(repo, root, t, mechanical=mechanical, files=files, ref=ref)   # the branch judged, not HEAD
    if rep is None:
        return [ln.replace("layer-inventory: ", "behaviour inventory: ") for ln in lines] if code else []
    want = li.check_text(rep)
    p = rec_dir(root, t) / li.CHECK_FILE
    if write and read_file(p) != want:
        p.write_text(want, encoding="utf-8")
    out = [f"behaviour inventory: unaccounted {r['id']} ({r['detail']})" for r in rep["items"] if r["status"] == "unaccounted"]
    committed = git(repo, "show", f"{ref}:{REC}/{t}/{li.CHECK_FILE}")
    if not out and (committed.returncode != 0 or committed.stdout != want):
        out.append(f"behaviour inventory: the committed .port/{t}/{li.CHECK_FILE} "
                   + ("is missing" if committed.returncode else "differs from a fresh check")
                   + " -- a release reads the committed one: run tools/fork-port.py on the branch (it rewrites it) and commit it")
    return out


def target_done(repo, t, root=None, ref=None, write=False, _seen=None):
    """[] when version t is DONE (the list in this file's header); else every reason it is not. root: the tree to
    judge (default: the checkout when it is on t's branch, else an export of the branch); ref: its commit."""
    repo = pathlib.Path(repo).resolve()
    tmp = None
    try:
        if root is None:
            if checkout_version(repo) == t:
                root, ref = repo, ref or "HEAD"
            else:
                c = resolve_branch(repo, branch_name(t))
                if not c:
                    return [f"no branch {branch_name(t)} in this clone"]
                tmp = tempfile.TemporaryDirectory()
                root, ref = export(repo, c, pathlib.Path(tmp.name) / "tree"), c
                write = False
        root = pathlib.Path(root)
        work = root.resolve() == repo and (root / ".git").exists()
        ref = git(repo, "rev-parse", ref or "HEAD").stdout.strip()
        if not (rec_dir(root, t) / BRANCH_PROPS).is_file():
            return [f"{root} is not version branch {t} (no .port/{t}/{BRANCH_PROPS})"]
        files = root_files(repo, root, work)
        mech = mechanical_files(repo, root, t, ref, files)
        out = gates_problems(repo, root, t, ref, work, files)
        n_open = len(fp().unreviewed_stubs(rec_dir(root, t)))
        if n_open:
            out.append(f"{n_open} unreviewed stub signal(s) in STUBS.json")
        items = review_items(repo, root, t, ref, work, files, mech)
        out += review_problems(repo, root, t, ref, work, files, items)
        out += remerge_problems(repo, root, t, work, files)
        if not (rec_dir(root, t) / "TEST-ITEMS.md").is_file():
            out.append(f"manual-test items not generated (TEST-ITEMS.md): python3 tools/fork-port.py --repo {repo} "
                       f"--target {t} --write-test-items")
        rv = read_file(rec_dir(root, t) / "REVIEW.md")
        out += fp().drop_reported_items(inventory_problems(repo, root, t, ref, work, files, write, mech),
                                        fp().not_ok_items(rv) if rv else {})
        deps = read_file(rec_dir(root, t) / "DEPS.md")
        if fp().UNDONE_LIBRARY_MARK in deps:
            out.append("built against a library version that is not done (--allow-undone-library, DEPS.md \"Development "
                       "only\"): re-resolve with --port-release once the library's version is done")
        p = info(root, t)
        dev = sorted(k[len(DEV_JAR_MARK):] for k in p if k.startswith(DEV_JAR_MARK))
        if dev:
            out.append(f"built against a library jar copied from a checkout ({', '.join(dev)}; --port-checkout is development "
                       "only): re-resolve with --port-release owner/repo@TAG")
        allowed = p.get("allowed_undone_parent", "")
        if allowed:
            pv = allowed.split()[0]
            seen = (_seen or set()) | {t}
            if pv not in seen and resolve_branch(repo, branch_name(pv)) and target_done(repo, pv, _seen=seen):
                out.append(f"its parent {pv} is still not done (added with --allow-undone-parent: {allowed})")
        if p.get("library") == "true" and not library_tested(repo, root, t, ref, work, files):
            out.append(LIBRARY_PENDING + f" (record one: python3 tools/fork-branches.py library-record --repo {repo} "
                       f"--target {t} --dependent DIR)")
        return out
    finally:
        if tmp is not None:
            tmp.cleanup()


def library_tested(repo, root, t, ref, work, files):
    """Does .port/<t>/DEPENDENTS.json name a dependent that passed Gate C against a jar built from a library commit
    this branch carries, with nothing jar-relevant changed since (so the same jar)?"""
    try:
        deps = json.loads(read_file(rec_dir(root, t) / "DEPENDENTS.json") or "[]")
    except ValueError:
        return False
    for d in deps if isinstance(deps, list) else []:
        c = str((d or {}).get("library_commit", ""))
        if d.get("gate_c") == "PASS" and commit_current(repo, root, t, c, ref, work, "gates", files)[0]:
            return True
    return False


# ---------------------------------------------------------------- migration: a layer fork -> version branches

class Layer:
    """The layer at one commit, written into a scratch directory from its blobs (its own .multiversion/tools/layer.py
    must run there): the blobs' bytes, never a checkout's, so core.autocrlf or an eol attribute cannot change what is
    prepared and committed. checkout() gives a git worktree of it for the layer's own done check (it needs git),
    made with line-ending conversion off. close() removes both; the person's checkout is never touched."""

    def __init__(self, repo, commit, scratch):
        self.repo, self.commit = pathlib.Path(repo), commit
        self.dir = pathlib.Path(scratch) / f"layer-{commit[:12]}"
        self.wt = None
        if not self.dir.is_dir():
            export(repo, commit, self.dir)
        self.mv = self.dir / ".multiversion"
        spec = importlib.util.spec_from_file_location(f"layer_{commit[:12]}", self.mv / "tools/layer.py")
        sys.dont_write_bytecode = True
        self.lp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.lp)
        self.base = parse_props(read_file(self.mv / "base.properties"))

    def checkout(self):
        if self.wt is None:
            self.wt = self.dir.parent / f"{self.dir.name}-wt"
            git(self.repo, "-c", "core.autocrlf=false", "-c", "core.eol=lf", "worktree", "add", "--detach", "--force",
                str(self.wt), self.commit, check=True)
        return self.wt

    def close(self):
        if self.wt is not None:
            git(self.repo, "worktree", "remove", "--force", str(self.wt))
            shutil.rmtree(self.wt, ignore_errors=True)
        shutil.rmtree(self.dir, ignore_errors=True)

    def targets(self):
        d = self.mv / "targets"
        return sorted((p.name for p in d.iterdir() if (p / "target.properties").is_file()), key=mc_key) if d.is_dir() else []

    def props(self, t):
        return parse_props(read_file(self.mv / "targets" / t / "target.properties"))

    def raw_props(self, t):
        return read_file(self.mv / "targets" / t / "target.properties")

    def chain(self, t):
        return self.lp.chain(t)

    def shared_chain(self, t):
        return getattr(self.lp, "shared_chain", self.lp.chain)(t)

    def prepare(self, t, sset, out):
        r = subprocess.run([sys.executable, str(self.mv / "tools/layer.py"), "prepare", "--target", t, "--set", sset,
                            "--src", str(self.dir / "src" / sset / "java"), "--out", str(out)], cwd=self.dir,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode:
            raise RuntimeError(f"layer.py prepare --target {t} --set {sset} failed: {(r.stdout + r.stderr).strip()[-600:]}")


def layer_mixins(lay, t):
    """(adds, removes) {config: {array: [class]}} the layer at that commit applies to t: its own layer.py's mixin_set(),
    or, for a layer.py older than it, every mixin_add of the chain (farthest first), which is all such a layer read."""
    if hasattr(lay.lp, "mixin_set"):
        return lay.lp.mixin_set(t)
    adds = {}
    for v in reversed(lay.chain(t)):
        for k, val in sorted(lay.props(v).items()):
            if k.startswith("mixin_add."):
                for array, c in mixin_spec(val):
                    lst = adds.setdefault(k[len("mixin_add."):], {}).setdefault(array, [])
                    if c not in lst:
                        lst.append(c)
    return adds, {}


def ordered_targets(lay, only=None):
    """The layer's targets, each after the target it inherits (its parent's branch must exist first)."""
    names = lay.targets()
    out = []

    def add(t, seen=()):
        if t in out:
            return
        if t in seen:
            raise ValueError(f"inherits loop at {t}")
        inh = lay.props(t).get("inherits")
        if inh:
            add(inh, (*seen, t))
        out.append(t)
    for t in names:
        add(t)
    if only:
        want = set(only)
        bad = want - set(names)
        if bad:
            raise ValueError(f"not targets of the layer: {', '.join(sorted(bad))}")
        out = [t for t in out if t in want]
    return out


def strip_layer_hook(text):
    """build.gradle with make-fork-layer.py's hook (its comment lines and the apply line) taken out."""
    hook = load("make_fork_layer_fb", "make-fork-layer.py").HOOK
    if text.endswith(hook):
        return text[:-len(hook)].rstrip("\n") + "\n"
    return "".join(l for l in text.splitlines(keepends=True) if l.strip() != LAYER_HOOK_LINE)


AT_DEFAULT = "src/main/resources/META-INF/accesstransformer.cfg"


def custom_at_paths(build_gradle):
    """Access-transformer files the authors' build.gradle names other than the default path."""
    out = []
    for m in re.finditer(r"accessTransformers?[^\n]*?['\"]([^'\"\n]+\.cfg)['\"]", build_gradle):
        p = m.group(1).lstrip("./")
        if p != AT_DEFAULT:
            out.append(p)
    return sorted(set(out))


def translate_path(p, t=None):
    """A layer record's path as the version branch has it: an overlay or a prepared file -> src/<set>/java/<rel>, a
    target's resource -> src/main/resources/<rel>; anything else unchanged."""
    m = re.match(r"^\.multiversion/targets/[^/]+/(?:([\w-]+)/)?java/(.+)$", p)
    if m:
        return f"src/{m.group(1) or 'main'}/java/{m.group(2)}"
    m = re.match(r"^\.multiversion/targets/[^/]+/resources/(.+)$", p)
    if m:
        return f"src/main/resources/{m.group(1)}"
    m = re.match(r"^build/mc[^/]+/mv/(?:([\w-]+)-)?java/(.+)$", p)
    if m:
        return f"src/{m.group(1) or 'main'}/java/{m.group(2)}"
    return p


def translate_stubs(text):
    try:
        data = json.loads(text)
    except ValueError:
        return text
    if not isinstance(data, list):
        return text
    for e in data:
        if isinstance(e, dict) and isinstance(e.get("file"), str):
            e["file"] = translate_path(e["file"])
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def expected_tree(lay, t, parent, port_commit, parent_files=None):
    """{path: bytes} of version branch t as migrated from layer commit lay.commit (without the records of other
    versions, which the branch carries from its parent), and notes. parent_files: the parent version's expected tree
    (for its mixin configs; None for the first version)."""
    repo, notes = lay.repo, []
    base = ref_files(repo, port_commit)
    files = {p: d for p, d in base.items() if not p.startswith(".multiversion/") and not p.startswith(REC + "/")}
    tprops, raw = lay.props(t), lay.raw_props(t)
    ch = lay.chain(t)
    # sources: the layer's own prepare, per source set (main last, as layer.gradle does)
    sets = sorted({p.split("/")[1] for p in files if re.match(r"^src/[^/]+/java/", p)}, key=lambda s: (s == "main", s))
    with tempfile.TemporaryDirectory() as d:
        for s in sets:
            out = pathlib.Path(d) / f"{s}-java"
            lay.prepare(t, s, out)
            for p in [p for p in files if p.startswith(f"src/{s}/java/") and p.endswith(".java")]:
                del files[p]
            for f in sorted(out.rglob("*.java")):
                files[f"src/{s}/java/{f.relative_to(out).as_posix()}"] = f.read_bytes()
    # resources: the chain's resources over the authors' (main set only; the nearest target wins)
    for v in reversed(ch):
        rd = lay.mv / "targets" / v / "resources"
        for f in sorted(rd.rglob("*")) if rd.is_dir() else []:
            if f.is_file():
                files[f"src/main/resources/{f.relative_to(rd).as_posix()}"] = f.read_bytes()
    at = next((lay.mv / "targets" / v / "resources/META-INF/accesstransformer.cfg" for v in ch
               if (lay.mv / "targets" / v / "resources/META-INF/accesstransformer.cfg").is_file()), None)
    bg = strip_layer_hook(files.get("build.gradle", b"").decode("utf-8", "surrogateescape"))
    if at is not None:
        for p in custom_at_paths(bg):
            files[p] = at.read_bytes()
            notes.append(f"{t}: the target's access transformer written to {p} too (the authors' build names it)")
    # the edits layer.gradle made at processResources: mixin configs, mods.toml ranges
    adds, removes = layer_mixins(lay, t)
    deltas = {}
    for p in sorted(files):
        name = p.rsplit("/", 1)[-1]
        if not p.startswith("src/") or "/resources/" not in p and "/templates/" not in p:
            continue
        if name in adds or name in removes:
            obj = jload(files[p])
            a = [(arr, c) for arr, cs in (adds.get(name) or {}).items() for c in cs]
            r = [(arr, c) for arr, cs in (removes.get(name) or {}).items() for c in cs]
            new = mixin_edit(obj, a, r)
            if new != obj:
                files[p] = json_like(new, files[p].decode("utf-8", "surrogateescape")).encode("utf-8", "surrogateescape")
        if name == "neoforge.mods.toml":
            ranges = {k[5:]: v for k, v in tprops.items() if k.startswith("toml.")}
            if ranges:
                files[p] = toml_rewrite(files[p].decode("utf-8", "surrogateescape"), ranges).encode("utf-8", "surrogateescape")
    # this version's mixin changes over its parent's configs, for the derived-file rule
    for p in sorted(files):
        name = p.rsplit("/", 1)[-1]
        if (name in adds or name in removes) and p.startswith("src/"):
            before = (parent_files or base).get(p)
            if before is not None:
                a_, r_ = mixin_delta(jload(before), jload(files[p]))
                if a_:
                    deltas[f"mixin_add.{name}"] = spec_text(a_)
                if r_:
                    deltas[f"mixin_remove.{name}"] = spec_text(r_)
    # versions, the hook, the shared script
    gp = files.get("gradle.properties", b"").decode("utf-8", "surrogateescape")
    files["gradle.properties"] = set_props_text(gp, {k[5:]: v for k, v in tprops.items() if k.startswith("prop.")}).encode(
        "utf-8", "surrogateescape")
    files["build.gradle"] = (bg.rstrip("\n") + "\n" + BRANCH_HOOK).encode("utf-8", "surrogateescape")
    files[f"{REC}/branch.gradle"] = TPL.read_bytes()
    if (lay.mv / "INVENTORY.json").is_file():
        files[f"{REC}/INVENTORY.json"] = (lay.mv / "INVENTORY.json").read_bytes()
    files.update(records_of(lay, t, parent, port_commit, raw, deltas, files, ch))
    return files, notes


RECORD_COPY = ("GATES.md", "COST.md", "DEPS.md", "TEST-ITEMS.md", "RESIDUE.md", "BEHAVIOUR.json", "renames.tsv",
               "moves.generated.tsv", "drops.txt", "logs-known-absent.txt")
LAYER_ONLY_KEYS = re.compile(r"^(?:inherits|mixin_add\.|mixin_add_only\.|mixin_remove_only\.|shared_chain)")


def records_of(lay, t, parent, port_commit, raw, deltas, files, ch):
    """{path: bytes} of .port/<t>/: branch.properties, the target's records (paths translated), its rows."""
    tdir, rd = lay.mv / "targets" / t, f"{REC}/{t}"
    pb = branch_name(lay.base.get("minecraft_version", "1.21.1"))
    head = [f"# Version branch {branch_name(t)} (tools/fork-branches.py migrate): Minecraft {t}, from the layer target {t}",
            f"# at {lay.commit[:12]}. The build keys below are what layer.gradle applied with -Pmc={t}; .port/branch.gradle",
            "# reads them, gradle.properties holds the prop.* versions, and mixin_add/mixin_remove are this version's changes",
            "# over its parent branch's mixin configs (already written into them).",
            f"minecraft_version={t}", f"branch={branch_name(t)}", f"parent={branch_name(parent) if parent else pb}"]
    if parent:
        head.append(f"parent_minecraft_version={parent}")
    head += [f"port_branch={pb}", f"base_minecraft_version={lay.base.get('minecraft_version', '1.21.1')}",
             f"port_taken={port_commit}"]
    for k in ("mv_package", "upstream", "upstream_commit", "library"):
        if lay.base.get(k):
            head.append(f"{k}={lay.base[k]}")
    head += [f"{k}={v}" for k, v in sorted(lay.base.items()) if k.startswith("library.")]
    sc = lay.shared_chain(t)
    if sc != ch:
        head.append("shared_chain=" + ",".join(sc))
    body = [l for l in raw.splitlines() if not LAYER_ONLY_KEYS.match(l.strip()) and not l.lstrip().startswith("# Minecraft ")]
    out = {f"{rd}/{BRANCH_PROPS}": ("\n".join(head + body + [f"{k}={v}" for k, v in sorted(deltas.items())]) + "\n").encode("utf-8")}
    for name in RECORD_COPY:
        if (tdir / name).is_file():
            out[f"{rd}/{name}"] = (tdir / name).read_bytes()
    if (tdir / "STUBS.json").is_file():
        out[f"{rd}/STUBS.json"] = translate_stubs(read_file(tdir / "STUBS.json")).encode("utf-8")
    for n in (f"shipped-expect-{t}.txt", "shipped-expect.txt"):
        if (lay.mv / n).is_file():
            out[f"{rd}/shipped-expect.txt"] = (lay.mv / n).read_bytes(); break
    if (lay.mv / "shipped-jar.txt").is_file():
        out[f"{rd}/shipped-jar.txt"] = (lay.mv / "shipped-jar.txt").read_bytes()
    drift = [l for l in read_file(lay.mv / "DRIFT.md").splitlines() if re.match(rf"^- \S+ {re.escape(t)}: ", l)]
    if drift:
        out[f"{rd}/DRIFT.md"] = ("\n".join(drift) + "\n").encode("utf-8")
    shared = [f"# ---- shared {s}\n" + read_file(lay.mv / "shared" / s / "renames.tsv") for s in sc
              if (lay.mv / "shared" / s / "renames.tsv").is_file()]
    if shared:
        out[f"{rd}/shared-renames.tsv"] = ("\n".join(shared) + "\n").encode("utf-8")
    # the shared-library helpers the layer added to this target's tree (a later helper edit is a review item)
    pool = {}
    for s in reversed(sc):
        d = lay.mv / "shared" / s / "java"
        for f in d.rglob("*.java") if d.is_dir() else []:
            pool[f.relative_to(d).as_posix()] = f.read_bytes()
    own = {f.relative_to(lay.mv / "targets" / v / "java").as_posix() for v in ch
           for f in (lay.mv / "targets" / v / "java").rglob("*.java")} if pool else set()
    helpers = [f"src/main/java/{rel} {hash_blobs({rel: files[f'src/main/java/{rel}']})[rel]}" for rel in sorted(pool)
               if f"src/main/java/{rel}" in files and rel not in own and files[f"src/main/java/{rel}"] == pool[rel]]
    if helpers:
        out[f"{rd}/shared-helpers.txt"] = ("# shared-library helpers as installed (path blob): tools/fork-branches.py\n"
                                           + "\n".join(helpers) + "\n").encode("utf-8")
    rm = tdir / "REMERGE.json"
    if rm.is_file():
        try:
            data = json.loads(read_file(rm))
            for c in data.get("conflicts", []):
                ov = lay.dir / str(c.get("overlay", ""))
                new = translate_path(str(c.get("overlay", "")))
                if ov.is_file() and hashlib.sha256(ov.read_bytes()).hexdigest() == str(c.get("digest", "")).lower() \
                        and new in files:
                    c["digest"] = hashlib.sha256(files[new]).hexdigest()       # still open: the branch file's bytes
                c["overlay"] = new
            out[f"{rd}/REMERGE.json"] = (json.dumps(data, indent=1) + "\n").encode("utf-8")
        except ValueError:
            out[f"{rd}/REMERGE.json"] = rm.read_bytes()
    known = set(RECORD_COPY) | {"STUBS.json", "REMERGE.json", "target.properties", "REVIEW.md", "INVENTORY-CHECK.json"}
    for f in sorted(tdir.iterdir()) if tdir.is_dir() else []:
        if f.is_file() and f.name not in known:
            out[f"{rd}/{f.name}"] = f.read_bytes()
    return out


def translate_review(lay, t, items_now, layer_items):
    """The layer target's REVIEW.md as version branch t's, keeping only what a reviewer judged: each verdict moves to
    the branch item that stands for the same bytes -- an overlay row to its `changed` src file, a resource group's row
    (when it was current) to the branch's group when the branch group's files are among the reviewed ones, renames.tsv
    (same digest), stub/item/missing/shared rows as they are. An item no reviewer judged gets no row (the done check,
    and fork-port.py's skeleton, list it). -> text or None when the layer target has no REVIEW.md."""
    f_ = fp()
    src = lay.mv / "targets" / t / "REVIEW.md"
    if not src.is_file():
        return None
    text = read_file(src)
    fields, rows = f_.parse_review(text)
    current = set(layer_items)
    out = {}
    for (kind, file), (v, note) in rows.items():
        if kind == "overlay":
            out[("changed", translate_path(file))] = (v, note)
        elif kind == "stub":
            out[("stub", translate_path(file))] = (v, note)
        elif kind == "renames":
            out[("renames", file.replace(f".multiversion/targets/{t}/", f"{REC}/{t}/"))] = (v, note)
        elif kind == "resources":
            if (kind, file) not in current:
                continue                                    # a stale verdict carries nothing
            out[("layer-resources", file)] = (v, note)
        else:
            out[(kind, file)] = (v, note)
    # a branch resource group takes the verdict of the current layer group with the same key whose files include it
    lgroups = {f.split(" @", 1)[0].replace(f".multiversion/targets/{t}/", "src/main/"): (v, n)
               for (k, f), (v, n) in out.items() if k == "layer-resources"}
    for kind, file in items_now:
        if kind == "resources":
            key = file.split(" @", 1)[0]
            if key in lgroups:
                out[("resources", file)] = (lgroups[key][0], lgroups[key][1] + " [migrated from the layer's review of this group]")
    out = {k: v for k, v in out.items() if k[0] != "layer-resources"}
    p = {"branch": branch_name(t)}
    head = REVIEW_HEAD.format(t=t, b=p["branch"], p="its parent branch")
    head = head.replace("- reviewer: ?\n- date: ?\n- commit: ?\n",
                        f"- reviewer: {fields.get('reviewer', '?')}\n- date: {fields.get('date', '?')}\n"
                        f"- commit: {fields.get('commit', '?')}\n")
    head = head.replace("\n\n- reviewer:", f"\n\nMigrated from the layer's .multiversion/targets/{t}/REVIEW.md by "
                                            "tools/fork-branches.py: the commit is a layer commit, mapped in MIGRATED.json.\n\n- reviewer:", 1)
    return head + "".join(f"| {k} | {x.replace('|', chr(92) + '|')} | {v} | {n.replace('|', chr(92) + '|')} |\n"
                          for (k, x), (v, n) in sorted(out.items()))


def commit_tree(repo, files, modes, parent, message, env_extra=None):
    """A commit of exactly `files` ({path: bytes}; modes {path: mode} for those that keep theirs) with `parent`,
    made with a scratch index (no worktree is touched). -> sha."""
    with tempfile.TemporaryDirectory() as d:
        env = dict(os.environ, GIT_INDEX_FILE=str(pathlib.Path(d) / "index"), **(env_extra or {}))
        blobs = hash_blobs(files)
        need = [p for p in files if git(repo, "cat-file", "-e", blobs[p], env=env).returncode != 0]
        for p in need:
            r = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "--stdin", "--no-filters"], input=files[p],
                               capture_output=True, env=env, check=True)
            assert r.stdout.decode().strip() == blobs[p]
        info_lines = "".join(f"{modes.get(p, '100644')} {blobs[p]}\t{p}\n" for p in sorted(files))
        r = subprocess.run(["git", "-C", str(repo), "update-index", "--add", "-z", "--index-info"],
                           input=info_lines.replace("\n", "\0").encode("utf-8", "surrogateescape"), capture_output=True,
                           env=env)
        if r.returncode:
            raise RuntimeError(f"update-index failed: {r.stderr.decode()[-300:]}")
        tree = git(repo, "write-tree", env=env, check=True).stdout.strip()
        c = git(repo, "commit-tree", tree, "-p", parent, "-m", message, env=env, check=True).stdout.strip()
        return c


TRAILER = "\n\nMade by tools/fork-branches.py (mc-mod-version-upgrade)."


def compare_trees(want, have, ignore=()):
    """[(path, what)] where two {path: bytes} trees differ."""
    out = []
    for p in sorted(set(want) | set(have)):
        if any(p == i or p.startswith(i) for i in ignore):
            continue
        if p not in have:
            out.append((p, "missing on the branch"))
        elif p not in want:
            out.append((p, "only on the branch"))
        elif want[p] != have[p]:
            out.append((p, "differs"))
    return out


def layer_commit_of(repo, layer_ref):
    for ref in ([layer_ref] if layer_ref else ["HEAD", "neoforge-1.21.x", "origin/neoforge-1.21.x"]):
        c = git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").stdout.strip()
        if c and git(repo, "cat-file", "-e", f"{c}:.multiversion/base.properties").returncode == 0:
            return ref, c
    return None, None


def verify_target(lay, t, branch_ref, parent, port_commit, parent_files=None):
    """[(path, what)]: where version branch `branch_ref` differs from what layer commit lay.commit makes for t -- the
    layer's own `layer.py prepare` run again for every source set, the chain's resources, the mixin arrays the layer's
    own resolver gives, the versions and the hook. Records under .port/ are not compared (they move on)."""
    repo = lay.repo
    want, _notes = expected_tree(lay, t, parent, port_commit, parent_files)
    have = ref_files(repo, branch_ref)
    have = {p: d for p, d in have.items() if not p.startswith(REC + "/") or p == f"{REC}/branch.gradle"}
    want = {p: d for p, d in want.items() if not p.startswith(REC + "/") or p == f"{REC}/branch.gradle"}
    diffs = compare_trees(want, have)
    adds, removes = layer_mixins(lay, t)
    for cfg in sorted(set(adds) | set(removes)):
        for p in [p for p in have if p.rsplit("/", 1)[-1] == cfg and p.startswith("src/")]:
            src = next((lay.mv / "targets" / v / "resources" / p.split("/resources/", 1)[1] for v in lay.chain(t)
                        if p.startswith("src/main/resources/") and
                        (lay.mv / "targets" / v / "resources" / p.split("/resources/", 1)[1]).is_file()), lay.dir / p)
            if not src.is_file():
                continue
            if not hasattr(lay.lp, "apply_mixins"):
                continue                # an older layer.py: the arrays were compared byte for byte above
            layer_arrays = lay.lp.apply_mixins(json.loads(read_file(src)), cfg, adds, removes)
            got = jload(have[p])
            mine = {k: sorted(v) for k, v in got.items() if isinstance(v, list)}
            if {k: v for k, v in layer_arrays.items() if v or k in mine} != {k: v for k, v in mine.items() if v or k in layer_arrays}:
                diffs.append((p, f"mixin arrays differ from the layer's (layer.py mixins --target {t} --config): "
                                 f"{layer_arrays} vs {mine}"))
    return diffs


def migrate(repo, layer_ref=None, targets=None, dry_run=False, port_branch=None, say=print, jpath=None):
    """-> exit code. See the module docstring."""
    repo = pathlib.Path(repo).resolve()
    ref, lc = layer_commit_of(repo, layer_ref)
    if not lc:
        say(f"fork-branches: no layer (.multiversion/base.properties) at {layer_ref or 'HEAD / neoforge-1.21.x'}: nothing to migrate")
        return 2
    scratch = pathlib.Path(tempfile.mkdtemp(prefix="fork-branches-"))
    lay = None
    report = {"layer_ref": ref, "layer_commit": lc, "targets": []}
    try:
        lay = Layer(repo, lc, scratch)
        base_mc = lay.base.get("minecraft_version", "1.21.1")
        pb = port_branch or branch_name(base_mc)
        port_tip = resolve_branch(repo, pb)
        if not port_tip:
            say(f"fork-branches: the port branch {pb} is not in this clone (pass --port-branch)"); return 2
        port_commit = git(repo, "merge-base", lc, port_tip).stdout.strip()
        iso = git(repo, "diff", "--name-only", port_commit, lc).stdout.split()
        outside = [p for p in iso if not p.startswith(".multiversion/") and p != "build.gradle"]
        if outside:
            say(f"fork-branches: the layer at {lc[:12]} differs from the port commit it carries ({port_commit[:12]}) outside "
                f".multiversion/ ({', '.join(outside[:5])}): not an isolated layer (layer.py isolation); refusing"); return 2
        ahead = git(repo, "rev-list", "--count", f"{port_commit}..{port_tip}").stdout.strip()
        if ahead not in ("", "0"):
            say(f"fork-branches: note: {pb} has {ahead} commit(s) the layer does not carry; the version branches start from "
                f"the port commit the layer carries ({port_commit[:12]}) -- bring them up afterwards with "
                f"`fork-branches.py cascade --repo {repo} {pb}..<newest>`")
        try:
            order = ordered_targets(lay, targets)
        except ValueError as e:
            say(f"fork-branches: {e}"); return 2
        say(f"fork-branches: layer {ref} at {lc[:12]}, port {pb} at {port_commit[:12]}; versions in chain order: "
            f"{' -> '.join(order)}" + (" (dry run: nothing is written)" if dry_run else ""))
        made, expected_of, bad, planned = {}, {}, 0, set()
        for t in order:
            inh = lay.props(t).get("inherits")
            parent = inh
            if inh and inh not in order and not resolve_branch(repo, branch_name(inh)):
                say(f"fork-branches: {t} inherits {inh}, which is neither migrated nor in --targets"); return 2
            start = (made.get(inh) or resolve_branch(repo, branch_name(inh))) if inh else port_commit
            row = {"target": t, "branch": branch_name(t), "parent": branch_name(inh) if inh else pb, "start": start}
            want, notes = expected_tree(lay, t, parent, port_commit, expected_of.get(inh))
            expected_of[t] = want
            for n in notes:
                say(f"fork-branches: {n}")
            carried = {p: d for p, d in (ref_files(repo, start, REC) if git(repo, "cat-file", "-e", f"{start}:{REC}").returncode == 0
                                         else {}).items() if not p.startswith(f"{REC}/{t}/")
                       and p not in (f"{REC}/branch.gradle", f"{REC}/INVENTORY.json")}
            tree = dict(carried, **want)
            modes = {p: m for p, (m, _b) in tree_entries(repo, port_commit).items()}
            existing = resolve_branch(repo, branch_name(t))
            if existing:
                have = ref_files(repo, existing)
                diff = [d for d in compare_trees({p: v for p, v in tree.items() if not p.startswith(f"{REC}/{t}/")},
                                                 {p: v for p, v in have.items() if not p.startswith(f"{REC}/{t}/")})]
                if diff:
                    say(f"fork-branches: REFUSED {t}: {branch_name(t)} already exists with other content ({len(diff)} "
                        f"file(s), e.g. {diff[0][0]}: {diff[0][1]}); it is never moved or deleted -- rename it, or migrate "
                        "the other versions with --targets")
                    return 2
                say(f"fork-branches: {t}: {branch_name(t)} exists with the same content: left as it is")
                made[t] = existing
                row["result"] = "exists, same content"
                report["targets"].append(row)
                continue
            if dry_run:
                n_src = sum(1 for p in want if p.startswith("src/"))
                rec = sorted(p.rsplit("/", 1)[-1] for p in want if p.startswith(f"{REC}/{t}/"))
                rec += ["MIGRATED.json", "INVENTORY-CHECK.json (fresh)"] + \
                    (["REVIEW.md (the layer's verdicts)"] if (lay.mv / "targets" / t / "REVIEW.md").is_file() else [])
                at = f"{start[:12]}" if not inh or inh not in planned else "its tip once made"
                say(f"fork-branches: {t}: would make {branch_name(t)} from {row['parent']} ({at}): {n_src} source "
                    f"file(s) from the layer's prepare, records {', '.join(rec)}")
                planned.add(t)
                made[t] = start
                row["result"] = "planned"
                report["targets"].append(row)
                continue
            c1 = commit_tree(repo, tree, modes, start,
                             f"Version branch {branch_name(t)}: Minecraft {t} from the layer at {lc[:12]}\n\n"
                             f"The source is what the layer's `layer.py prepare --target {t}` makes (every source set), the\n"
                             f"resources and build settings what layer.gradle applied with -Pmc={t}, and the target's records\n"
                             f"are in .port/{t}/." + TRAILER)
            # the mapping that keeps a reviewed, gated target done: each commit its records name -> the digests of what
            # the layer prepared there
            recorded = {}
            named = set()
            for name in ("GATES.md", "REVIEW.md"):
                txt = read_file(lay.mv / "targets" / t / name)
                m = re.search(r"at `([0-9a-f]{7,40})", txt.splitlines()[0]) if name == "GATES.md" and txt else None
                if m:
                    named.add(m.group(1))
                if name == "REVIEW.md" and txt:
                    cm = fp().parse_review(txt)[0].get("commit", "")
                    if COMMIT_ID.match(cm or ""):
                        named.add(cm)
            # REVIEW.md as the branch's, from the layer's verdicts (needs the branch items, so after the tree exists)
            stage = export(repo, c1, scratch / f"stage-{t}")
            items_now = review_items(repo, stage, t, c1, False)
            try:
                layer_items = fp().review_items(lay.dir, lay.mv / "targets" / t)
            except Exception:       # noqa: BLE001 -- an old layer: no current-item test, so no resource verdict carries
                layer_items = []
            rv = translate_review(lay, t, items_now, layer_items)
            extra = {}
            if rv is not None:
                extra[f"{REC}/{t}/REVIEW.md"] = rv.encode("utf-8")
                (stage / REC / t / "REVIEW.md").write_text(rv, encoding="utf-8")
            for c in sorted(named):
                full = git(repo, "rev-parse", "--verify", "--quiet", f"{c}^{{commit}}").stdout.strip()
                if not full:
                    recorded[c] = {"error": "not a commit in this clone"}
                    continue
                if full == lc:
                    files_c = dict(tree, **extra)
                else:
                    try:
                        lc_ = Layer(repo, full, scratch)
                        pc_ = git(repo, "merge-base", full, port_tip).stdout.strip()
                        files_c, _n = expected_tree(lc_, t, parent, pc_, None)
                        lc_.close()
                    except Exception as e:      # noqa: BLE001 -- recorded: that record then does not carry
                        recorded[full] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
                        continue
                dj, dr = digests(files_c, t, modes)
                recorded[full] = {"digest": dj, "review_inputs": dr, "with_modes": True}
            jar, inputs = digests(dict(tree, **extra), t, modes)
            mig = {"tool": "tools/fork-branches.py migrate", "layer_ref": ref, "layer_commit": lc, "target": t,
                   "port_commit": port_commit, "branch_commit": c1, "digest": jar, "review_inputs": inputs,
                   "with_modes": True, "recorded": recorded}
            extra[f"{REC}/{t}/MIGRATED.json"] = (json.dumps(mig, indent=1) + "\n").encode("utf-8")
            (stage / REC / t / "MIGRATED.json").write_bytes(extra[f"{REC}/{t}/MIGRATED.json"])
            # the inventory account, fresh, so the committed file is what a check says (gap 1)
            li = _mod("layer_inventory_fb", "layer-inventory.py")
            files_s = root_files(repo, stage, False)
            code, rep, _lines = li.check_branch(repo, stage, t, mechanical=mechanical_files(repo, stage, t, c1, files_s),
                                                files=files_s, ref=c1)
            if rep is not None:
                extra[f"{REC}/{t}/{li.CHECK_FILE}"] = li.check_text(rep).encode("utf-8")
            c2 = commit_tree(repo, dict(tree, **extra), modes, c1,
                             f"{branch_name(t)}: its records migrated from the layer\n\n"
                             f"REVIEW.md keeps the layer reviewer's verdicts on the files that stand for the same bytes;\n"
                             f"MIGRATED.json maps each layer commit GATES.md and REVIEW.md name to the digest of what the\n"
                             f"layer prepared there (branch commit {c1[:12]}); INVENTORY-CHECK.json is a fresh account." + TRAILER)
            if git(repo, "branch", branch_name(t), c2).returncode != 0:
                say(f"fork-branches: could not create {branch_name(t)}"); return 2
            made[t] = c2
            diffs = verify_target(lay, t, c2, parent, port_commit, expected_of.get(inh))
            bad += bool(diffs)
            done = target_done(repo, t)
            try:
                layer_done = fp().target_done(lay.checkout(), t, write=False, prepare=True)
            except Exception as e:      # noqa: BLE001
                layer_done = [f"(the layer's own check failed: {e})"]
            row.update(result="made", commit=c2, migration_commit=c1, identical=not diffs, diffs=diffs[:20],
                       layer_done=not layer_done, branch_done=not done, not_done=done[:20], layer_not_done=layer_done[:20])
            report["targets"].append(row)
            say(f"fork-branches: {t}: made {branch_name(t)} at {c2[:12]} from {row['parent']}; "
                + ("byte-identical to the layer's prepare" if not diffs else f"DIFFERS from the layer in {len(diffs)} file(s)")
                + f"; layer {'done' if not layer_done else 'not done'}, branch {'done' if not done else 'not done'}")
            for p, what in diffs[:10]:
                say(f"fork-branches:   {p}: {what}")
            if not layer_done and done:
                for w in done[:8]:
                    say(f"fork-branches:   NOT done on the branch although done on the layer: {w}")
        if jpath:
            pathlib.Path(jpath).write_text(json.dumps(report, indent=1), encoding="utf-8")
        if not dry_run and made:
            say("fork-branches: nothing was pushed. Check each branch, then: git push origin "
                + " ".join(branch_name(t) for t in order))
            say("fork-branches: CI per version branch: python3 tools/fork-branches.py ci --repo DIR --target <t> --modid ID")
        return 1 if bad else 0
    finally:
        if lay is not None:
            lay.close()
        shutil.rmtree(scratch, ignore_errors=True)


def verify(repo, layer_ref=None, targets=None, tip=False, say=print):
    """Compare each version branch with what the layer makes (verify_target). The branch's migration commit (named in
    MIGRATED.json) is compared unless tip. -> 0 identical, 1 a difference, 2 bad input."""
    repo = pathlib.Path(repo).resolve()
    ref, lc = layer_commit_of(repo, layer_ref)
    if not lc:
        say("fork-branches: no layer to compare with (--layer-ref)"); return 2
    scratch = pathlib.Path(tempfile.mkdtemp(prefix="fork-branches-"))
    lay = Layer(repo, lc, scratch)
    try:
        pb = branch_name(lay.base.get("minecraft_version", "1.21.1"))
        port_commit = git(repo, "merge-base", lc, resolve_branch(repo, pb) or lc).stdout.strip()
        bad, prev = 0, {}
        for t in ordered_targets(lay, targets):
            b = resolve_branch(repo, branch_name(t))
            if not b:
                say(f"fork-branches: {t}: no branch {branch_name(t)}"); bad += 1; continue
            mig = json.loads(git(repo, "show", f"{b}:{REC}/{t}/MIGRATED.json").stdout or "{}") if not tip else {}
            at = mig.get("branch_commit") or b
            inh = lay.props(t).get("inherits")
            diffs = verify_target(lay, t, at, inh, port_commit, prev.get(inh))
            prev[t] = expected_tree(lay, t, inh, port_commit, prev.get(inh))[0]
            say(f"fork-branches: {t}: {branch_name(t)} at {at[:12]} " + ("byte-identical to the layer's prepare" if not diffs
                                                                         else f"DIFFERS in {len(diffs)} file(s)"))
            for p, what in diffs[:20]:
                say(f"fork-branches:   {p}: {what}")
            bad += bool(diffs)
        return 1 if bad else 0
    finally:
        lay.close()
        shutil.rmtree(scratch, ignore_errors=True)



# ---------------------------------------------------------------- cascade: merge a lower branch up the chain

def chain_read(read, t):
    """chain() through a reader (a tree that is not on disk)."""
    out, v = [], t
    while v and v not in out:
        out.append(v)
        p = parse_props(read(f"{REC}/{v}/{BRANCH_PROPS}"))
        v = p.get("parent_minecraft_version") if p.get("parent") != p.get("port_branch") else None
    return out


def versions_to(repo, to):
    """(port branch name, [versions oldest first]) of `to`'s chain, read from its branch tip."""
    c = resolve_branch(repo, branch_name(to))
    if not c:
        raise ValueError(f"no branch {branch_name(to)}")
    read = reader_of_ref(repo, c)
    ch = chain_read(read, to)
    if not ch or not read(f"{REC}/{to}/{BRANCH_PROPS}"):
        raise ValueError(f"{branch_name(to)} is not a version branch (no {REC}/{to}/{BRANCH_PROPS})")
    port = parse_props(read(f"{REC}/{ch[-1]}/{BRANCH_PROPS}")).get("port_branch")
    return port, ch[::-1]


def blob_at(repo, ref, path):
    r = git(repo, "show", f"{ref}:{path}", binary=True)
    return r.stdout if r.returncode == 0 else None


# generated per branch (tools/port-ci.py on that branch; the install manifest its promote job refreshes there)
BRANCH_OWNED = (".github/workflows/port-ci.yml", ".github/port-install.json")


UNION_FILES = (f"{REC}/DECISIONS.md", f"{REC}/AUTHORS-REVIEWS.md")      # append-only records: a conflict is a union
DEFAULT_BUDGET_LINES = 80
CLASS_ORDER = ("escalate", "port-change", "authors-fix", "optional-integration")


def merge_file(ours, base, theirs):
    """git merge-file on three byte strings -> (clean, merged bytes)."""
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        for n, data in (("o", ours), ("b", base), ("t", theirs)):
            (d / n).write_bytes(data)
        r = subprocess.run(["git", "merge-file", "-p", "-q", str(d / "o"), str(d / "b"), str(d / "t")], capture_output=True)
        return r.returncode == 0, r.stdout


def port_taken(read, t):
    """The port branch commit version t has fully taken (.port/<t>/branch.properties port_taken=): the next carry starts
    there, so an authors' change a run did not take is offered again. None on a branch written before the key."""
    return parse_props(read(f"{REC}/{t}/{BRANCH_PROPS}")).get("port_taken") or None


def remerge_entries(data):
    try:
        return [c for c in (jload(data).get("conflicts", []) if data else []) if isinstance(c, dict)]
    except (ValueError, AttributeError):
        return []


def write_remerge(work, t, new):
    """Replace the entries for the files in `new` (a list of entries) in work's .port/<t>/REMERGE.json, keep the rest."""
    rm = work / REC / t / "REMERGE.json"
    try:
        data = jload(rm.read_bytes()) if rm.is_file() else {}
    except ValueError:
        data = {}
    files = {c["overlay"] for c in new}
    data["conflicts"] = [c for c in data.get("conflicts", []) if c.get("overlay") not in files] + new
    rm.parent.mkdir(parents=True, exist_ok=True)
    rm.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    git(work, "add", "--", rm.relative_to(work).as_posix())


def append_decisions(work, rows):
    """Append rows to .port/DECISIONS.md (append-only: the decision log the cascade's defaults and the owner's answers
    go to), creating it with its header."""
    p = work / REC / "DECISIONS.md"
    text = read_file(p) or ("# Cascade decisions (append-only; tools/fork-branches.py cascade)\n\n"
                            "| date | decision | file | version | choice | by | reasoning |\n|---|---|---|---|---|---|---|\n")
    today = datetime.date.today().isoformat()
    for r in rows:
        cells = [today, r["id"], f"`{r['file']}`", r["version"], r["choice"], r.get("by", "?"),
                 str(r.get("reasoning", "")).replace("|", "/").replace("\n", " ")[:400]]
        text += "| " + " | ".join(cells) + " |\n"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    git(work, "add", "--", p.relative_to(work).as_posix())


def diff_size(diff):
    return sum(1 for ln in diff.splitlines() if ln[:1] in "+-" and not ln.startswith(("+++", "---")))


def classify(f, t, t_pre, diff, read, budget):
    """The decision class of an authors' change version t could not take mechanically (the policy fork-versions states):
    -> (class, escalated, default text, recommended choice, reasoning). Mechanical rules only: no model."""
    stubs = read(f"{REC}/{t}/STUBS.json")
    deps = read(f"{REC}/{t}/DEPS.md")
    ours = (t_pre or b"").decode("utf-8", "replace")
    integ = "/compat/" in f or "/integration/" in f or "isLoaded(" in ours
    absent = integ and re.search(r"\|\s*dropped:", deps)
    added = [ln[1:] for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    gone = sorted({pathlib.PurePosixPath(x).stem for x in drops_of(read, chain_read(read, t))}
                  - {pathlib.PurePosixPath(f).stem})
    uses = [n for n in gone if any(re.search(rf"\b{re.escape(n)}\b", ln) for ln in added)]
    size = diff_size(diff)
    if absent:
        return ("optional-integration", False, "keep ours: the integration is absent at this version (DEPS.md: dropped), "
                "its calls stubbed so it works once it is available", "keep",
                f"{t} has no build of the integrated mod; our file already runs without it, and the authors' change "
                "reaches it again when the integration returns")
    if uses:
        return ("escalate", True, "", "keep",
                f"the authors' change uses {', '.join(uses)}, which {t} drops (drops.txt): their intent cannot be kept here as is")
    if size > budget:
        return ("escalate", True, "", "patch",
                f"the authors' change is {size} changed lines, over the budget of {budget} (--budget-lines): adapting it "
                "around our change costs more than a default decision may")
    if integ:
        return ("optional-integration", False, "keep the integration, apply the authors' change around it", "patch",
                "an optional integration: kept, with the authors' design")
    if f in stubs or pathlib.PurePosixPath(f).name in stubs:
        return ("port-change", False, "keep our deliberate change (STUBS.json), apply the authors' change around it", "patch",
                "a deliberate port change of ours (a stand-in, an API rewrite) is kept; the authors' change goes around it")
    return ("authors-fix", False, "take the authors' change through the rows, our change re-applied around it", "patch",
            "their fix, adapted through the rows (the authors' design intent)")


def merge_up(repo, work, t, parent_name, parent_ref, port_name, parent_version, say=print, resolve=None):
    """Merge `parent_ref` into version t's branch checked out at `work` (a clean worktree), with the rows as a tool:
    every .java file the merge changed that both sides had from the rows alone (the parent's equal to its rows over
    the port branch it now carries, t's equal to t's rows over the port branch it carried) is re-made from the port
    branch's new file through t's rows; a file t dropped stays dropped; a conflicting derived file (gradle.properties,
    mods.toml, a mixin config) is the parent's with t's build keys applied; t's own generated workflow is kept; the
    append-only records (DECISIONS.md, AUTHORS-REVIEWS.md) are a union. The authors' change to a file the parent does
    not carry is carried from port_taken (the port commit t fully took) up, so a change a run did not take is offered
    again; when the parent is already in t only that carry runs.
    resolve=None: any other conflict aborts the merge, and a carried file t changed by hand gets an open REMERGE.json
    entry. resolve={"budget": N} (cascade --plan / --apply): nothing aborts -- each such file is merged mechanically
    when git merge-file over t's rows resolves it, else kept as t had it and returned as a decision item (an open
    entry, or a default the tool carries out itself, logged in DECISIONS.md); the parent's open entries travel up.
    -> (ok, notes, unresolved files, pending {file: diff}, items, mechanical)."""
    notes, items, mech = [], [], []
    pre = git(work, "rev-parse", "HEAD", check=True).stdout.strip()
    port_tip = resolve_branch(repo, port_name)
    already = git(repo, "merge-base", "--is-ancestor", parent_ref, pre).returncode == 0
    port_new = parent_ref if parent_version is None else git(repo, "merge-base", parent_ref, port_tip).stdout.strip()
    port_mb = git(repo, "merge-base", pre, port_tip).stdout.strip()
    taken = port_taken(reader_of_ref(repo, pre), t)
    port_from = port_mb
    if taken and git(repo, "merge-base", "--is-ancestor", taken, port_new).returncode == 0 \
            and git(repo, "merge-base", "--is-ancestor", taken, port_mb).returncode == 0:
        port_from = taken
    if already:
        msg = (f"{branch_name(t)}: carry {port_name}'s change {port_from[:12]}..{port_new[:12]} (tools/fork-branches.py "
               f"cascade)\n\nThe authors' change to files {parent_name} does not carry." + TRAILER)
        conflicted, changed = set(), set()
    else:
        msg = (f"Merge {parent_name} into {branch_name(t)} (tools/fork-branches.py cascade)\n\n"
               f"Each file the rows alone made was re-made from {port_name}'s new code through {t}'s rows." + TRAILER)
        r = git(work, "merge", "--no-ff", "--no-commit", "-m", msg, parent_ref)
        if r.returncode not in (0, 1):
            return False, [f"git merge failed: {(r.stdout + r.stderr).strip()[-300:]}"], [], {}, [], []
        conflicted = {x for x in git(work, "diff", "--name-only", "--diff-filter=U").stdout.splitlines() if x}
        changed = {x for x in git(work, "diff", "--name-only", pre).stdout.splitlines() if x} | conflicted
    java = sorted(f for f in changed if re.match(r"^src/[^/]+/java/.+\.java$", f))
    # the authors' own change since t last took the port branch: a file the PARENT does not carry (it drops it, or
    # removes its mixin) never shows in the parent's merge, yet t has it and must take the change
    port_diff = [] if port_from == port_new else [
        f for f in git(repo, "diff", "--name-only", port_from, port_new).stdout.splitlines()
        if f not in changed and blob_at(repo, parent_ref, f) is None]
    port_java = sorted(f for f in port_diff if re.match(r"^src/[^/]+/java/.+\.java$", f))
    port_res = sorted(f for f in port_diff if re.match(r"^src/[^/]+/resources/", f))
    resolved, unmerged = set(), []
    for f in port_res:              # a resource the parent dropped and t keeps: no rows, so t's copy is the authors' or not
        t_pre, n = blob_at(repo, pre, f), blob_at(repo, port_new, f)
        if t_pre is None or t_pre == n:
            continue                                            # dropped on t (or new and dropped by the parent), or taken
        if t_pre in (blob_at(repo, port_mb, f), blob_at(repo, port_from, f)):
            if n is None:
                git(work, "rm", "-q", "-f", "--", f)
            else:
                (work / f).parent.mkdir(parents=True, exist_ok=True)
                (work / f).write_bytes(n)
                git(work, "add", "--", f)
            notes.append(f"{f}: the authors' change taken ({parent_name} does not carry the file)")
        else:
            unmerged.append(f)
    read_t = reader_of_ref(repo, pre)
    rows_cache = {}

    def rows(which):
        if which not in rows_cache:
            if which == "new":
                rn = reader_of_root(work) if not conflicted & {f for f in changed if f.startswith(REC + "/")} else read_t
                rows_cache[which] = rows_tree(repo, port_new, rn, t, chain_read(rn, t))
            elif which == "old":
                rows_cache[which] = rows_tree(repo, port_mb, read_t, t, chain_read(read_t, t))
            elif which == "from":
                rows_cache[which] = rows("old") if port_from == port_mb else rows_tree(repo, port_from, read_t, t, chain_read(read_t, t))
            else:                                               # the parent's rows over the port commit it carries
                rp = reader_of_ref(repo, parent_ref)
                rows_cache[which] = rows_tree(repo, port_new, rp, parent_version, chain_read(rp, parent_version))
        return rows_cache[which]
    remade = 0

    def remake(f):
        new = rows("new").get(f)
        if new is None:
            if (work / f).exists() or f in conflicted:
                git(work, "rm", "-q", "-f", "--", f)
        else:
            (work / f).parent.mkdir(parents=True, exist_ok=True)
            (work / f).write_bytes(new)
            git(work, "add", "--", f)
    for f in java:
        t_pre, p_new = blob_at(repo, pre, f), blob_at(repo, parent_ref, f)
        if t_pre is None and blob_at(repo, port_mb, f) is not None:
            if (work / f).exists() or f in conflicted:
                git(work, "rm", "-q", "-f", "--", f)
            resolved.add(f)
            notes.append(f"{f}: dropped on {t} (absent there, present on {port_name}): kept dropped")
            continue
        p_mech = parent_version is None or p_new == rows("parent").get(f)
        t_mech = t_pre == rows("old").get(f)
        if p_mech and t_mech:
            remake(f)
            resolved.add(f)
            remade += 1
    for f in port_java:
        t_pre = blob_at(repo, pre, f)
        if t_pre is None:
            if blob_at(repo, port_mb, f) is not None or blob_at(repo, port_from, f) is not None:
                continue                                        # dropped on t as well: stays dropped
            if f in rows("new"):                                # a new authors' file t's rows keep
                remake(f); remade += 1
            continue
        if t_pre == rows("new").get(f):
            continue                                            # taken already
        if t_pre in (rows("old").get(f), rows("from").get(f)):
            remake(f); remade += 1
        else:
            unmerged.append(f)        # t changed it by hand and the parent cannot carry the change
    if remade:
        notes.append(f"{remade} file(s) the rows alone make re-made from {port_name}'s code through {t}'s rows")
    if unmerged and resolve is None:
        notes.append(f"{len(unmerged)} file(s) the authors changed that {parent_name} does not carry and {t} changed by "
                     f"hand: {', '.join(unmerged[:4])}")
    props = parse_props(read_file(work / REC / t / BRANCH_PROPS))
    for f in sorted(conflicted - resolved):
        if f in UNION_FILES:
            ours, theirs = blob_at(repo, pre, f) or b"", blob_at(repo, parent_ref, f) or b""
            have = set(ours.splitlines())
            extra = [ln for ln in theirs.splitlines() if ln not in have]
            (work / f).write_bytes(ours + (b"" if ours.endswith(b"\n") or not ours else b"\n")
                                   + b"".join(ln + b"\n" for ln in extra))
            git(work, "add", "--", f)
            resolved.add(f)
            continue
        if re.match(r"^src/[^/]+/resources/", f) and blob_at(repo, pre, f) is None and blob_at(repo, port_mb, f) is not None:
            git(work, "rm", "-q", "-f", "--", f)                 # t dropped the resource, as for a .java file: stays dropped
            resolved.add(f)
            notes.append(f"{f}: dropped on {t} (the authors changed it): kept dropped")
            continue
        theirs = blob_at(repo, parent_ref, f)
        name = f.rsplit("/", 1)[-1]
        if theirs is None:
            continue
        text = theirs.decode("utf-8", "surrogateescape")
        new = None
        if f == "gradle.properties":
            new = set_props_text(text, {k[5:]: v for k, v in props.items() if k.startswith("prop.")})
        elif name == "neoforge.mods.toml" and f.startswith("src/"):
            new = toml_rewrite(text, {k[5:]: v for k, v in props.items() if k.startswith("toml.")})
        elif f.startswith("src/") and name.endswith(".json") and (f"mixin_add.{name}" in props or f"mixin_remove.{name}" in props):
            try:
                new = json_like(mixin_edit(jload(text), mixin_spec(props.get(f"mixin_add.{name}")),
                                           mixin_spec(props.get(f"mixin_remove.{name}"))), text)
            except ValueError:
                new = None
        if new is not None:
            (work / f).write_bytes(new.encode("utf-8", "surrogateescape"))      # bytes: no newline translation
            git(work, "add", "--", f)
            resolved.add(f)
            notes.append(f"{f}: {parent_name}'s, with {t}'s build keys")
    for f in BRANCH_OWNED:
        if f in changed:
            if blob_at(repo, pre, f) is not None:
                git(work, "checkout", pre, "--", f)
            else:
                git(work, "rm", "-q", "-f", "--", f)
            resolved.add(f)
            notes.append(f"{f}: {t}'s own kept (regenerate it on {branch_name(t)} with tools/fork-branches.py ci if {parent_name} "
                         "changed it on purpose)")
    old_entries = {c.get("overlay"): c for c in remerge_entries(blob_at(repo, pre, f"{REC}/{t}/REMERGE.json"))}

    def span(f, frm):
        """(from commit, the authors' diff from it to port_new): an entry still open for f keeps its earlier start."""
        c = old_entries.get(f)
        t_pre = blob_at(repo, pre, f) or b""
        if c and c.get("from") and hashlib.sha256(t_pre).hexdigest() == str(c.get("digest", "")).lower() \
                and git(repo, "merge-base", "--is-ancestor", c["from"], frm).returncode == 0:
            frm = c["from"]
        return frm, git(repo, "diff", frm, port_new, "--", f).stdout

    def entry(f, frm, diff, extra=None):
        body = git(work, "show", f":{f}", binary=True).stdout if (work / f).exists() else (blob_at(repo, pre, f) or b"")
        return {"overlay": f, "digest": hashlib.sha256(body).hexdigest(),
                "reason": f"the authors changed it; {parent_name} does not carry it and {t} changed it by hand",
                "since": f"{frm[:12]}..{port_new[:12]}", "from": frm, "to": port_new, "diff": diff, **(extra or {})}
    left = sorted(conflicted - resolved)
    pending = {}
    new_entries = []
    if resolve is None:
        for f in sorted(set(unmerged)):
            frm, diff = span(f, port_from)
            pending[f] = diff
            new_entries.append(entry(f, frm, diff))
        if left:
            git(work, "merge", "--abort")         # nothing committed: port_taken stays, so the carry is offered again
            return False, notes, left, pending, [], []
    else:
        decided = []
        for f in sorted(set(left) | set(unmerged)):
            carried = f in unmerged
            t_pre = blob_at(repo, pre, f)
            java_f = f.endswith(".java")
            frm = port_from if carried else port_mb
            base = (rows("from" if carried else "old").get(f) if java_f else blob_at(repo, frm, f))
            theirs = rows("new").get(f) if java_f else blob_at(repo, port_new, f)
            p_ok = carried or parent_version is None or (
                blob_at(repo, parent_ref, f) == (rows("parent").get(f) if java_f else theirs))
            if t_pre is not None and base is not None and theirs is not None and p_ok:
                clean, merged_b = merge_file(t_pre, base, theirs)
                if clean:
                    (work / f).parent.mkdir(parents=True, exist_ok=True)
                    (work / f).write_bytes(merged_b)
                    git(work, "add", "--", f)
                    resolved.add(f)
                    mech.append({"file": f, "version": t, "how": "the authors' change merged into ours over the rows "
                                 "(line drift, or a difference only the rows make)"})
                    continue
            frm, diff = span(f, frm)
            source = "authors"
            if not diff.strip():
                mb_p = git(repo, "merge-base", pre, parent_ref).stdout.strip()
                diff, source = git(repo, "diff", mb_p, parent_ref, "--", f).stdout, "parent"
            klass, esc, default, choice, why = classify(f, t, t_pre, diff, read_t, resolve.get("budget", DEFAULT_BUDGET_LINES))
            did = hashlib.sha1(f"{f}\0{diff}".encode("utf-8", "surrogateescape")).hexdigest()[:12]
            if t_pre is None:
                git(work, "rm", "-q", "-f", "--", f)
            else:
                (work / f).parent.mkdir(parents=True, exist_ok=True)
                (work / f).write_bytes(t_pre)
                git(work, "add", "--", f)
            resolved.add(f)
            item = {"id": did, "file": f, "version": t, "class": klass, "escalate": esc, "default": default,
                    "choice": choice, "reasoning": why, "diff": diff, "source": source, "from": frm, "to": port_new,
                    "ours": (t_pre or b"").decode("utf-8", "replace")[:6000]}
            if klass == "optional-integration" and choice == "keep":
                item["executed"] = True             # a default the tool carries out itself: logged, no open entry
                decided.append({"id": did, "file": f, "version": t, "choice": "keep", "by": "cascade default",
                                "reasoning": why})
            else:
                new_entries.append(entry(f, frm, diff, {"id": did, "class": klass, "escalate": esc,
                                                         "default": default, "choice": choice, "reasoning": why,
                                                         "source": source}))
            items.append(item)
        if decided:
            append_decisions(work, decided)
        left = []
    # the parent's open entries travel up with their file: t carries the file without the authors' change too
    if parent_version is not None:
        mine = {c["overlay"] for c in new_entries}
        for c in remerge_entries(blob_at(repo, parent_ref, f"{REC}/{parent_version}/REMERGE.json")):
            f = c.get("overlay", "")
            pb = blob_at(repo, parent_ref, f)
            if not f or f in mine or pb is None or hashlib.sha256(pb).hexdigest() != str(c.get("digest", "")).lower():
                continue
            if not (work / f).exists():
                continue
            old = old_entries.get(f)
            if old and old.get("diff") == c.get("diff") and hashlib.sha256(blob_at(repo, pre, f) or b"").hexdigest() == old.get("digest"):
                continue                                # carried already, still open
            if f.endswith(".java") and git(work, "show", f":{f}", binary=True).stdout == rows("new").get(f):
                continue                                # t has the authors' change after all
            inh = {k: v for k, v in c.items() if k not in ("digest",)}
            inh["inherited_from"] = parent_version
            body = git(work, "show", f":{f}", binary=True).stdout
            inh["digest"] = hashlib.sha256(body).hexdigest()
            new_entries.append(inh)
            if resolve is not None:
                items.append({"id": c.get("id") or hashlib.sha1(f"{f}\0{c.get('diff', '')}".encode()).hexdigest()[:12],
                              "file": f, "version": t, "class": c.get("class", "escalate" if c.get("escalate") else "authors-fix"),
                              "escalate": bool(c.get("escalate")), "default": c.get("default", ""),
                              "choice": c.get("choice", "patch"), "reasoning": c.get("reasoning", c.get("reason", "")),
                              "diff": c.get("diff", ""), "source": c.get("source", "authors"), "inherited_from": parent_version,
                              "ours": body.decode("utf-8", "replace")[:6000]})
    if new_entries:
        write_remerge(work, t, new_entries)
        if pending:
            notes.append(f"{len(pending)} file(s) recorded in .port/{t}/REMERGE.json with the authors' diff: apply by hand")
    # this version has now taken the port branch up to port_new (an open entry carries its own start)
    bp = work / REC / t / BRANCH_PROPS
    if bp.is_file() and props.get("port_taken") != port_new and (resolved or new_entries or not already or remade
                                                                  or git(work, "diff", "--cached", "--quiet").returncode):
        bp.write_bytes(set_props_text(read_file(bp), {"port_taken": port_new}).encode("utf-8", "surrogateescape"))
        git(work, "add", "--", bp.relative_to(work).as_posix())
    if already and git(work, "diff", "--cached", "--quiet").returncode == 0:
        return True, notes + ["nothing changed"], [], pending, items, mech
    r = git(work, "commit", "-q", "--no-edit", "-m", msg)
    if r.returncode:
        if "nothing to commit" in r.stdout + r.stderr:
            return True, notes + ["nothing changed"], [], pending, items, mech
        return False, notes + [f"commit failed: {(r.stdout + r.stderr).strip()[-200:]}"], [], pending, items, mech
    return True, notes, [], pending, items, mech


def worktree_for(repo, t):
    """A clean checkout of version t's branch: the person's checkout when it is on that branch and clean, else a
    worktree under build/fork-branches/ (kept between runs, so its Gradle caches stay warm). -> (path, why not)."""
    repo = pathlib.Path(repo).resolve()
    b = branch_name(t)
    cur = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if cur == b:
        if git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip():
            return None, f"the checkout is on {b} with uncommitted changes: commit or stash them first"
        return repo, None
    w = repo / "build" / "fork-branches" / t
    if w.is_dir() and (w / ".git").exists():
        if git(w, "status", "--porcelain", "--untracked-files=no").stdout.strip():
            return None, f"the worktree {w} has uncommitted changes"
        if git(w, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() != b:
            r = git(w, "checkout", "-q", b)
            if r.returncode:
                return None, f"cannot check out {b} in {w}: {r.stderr.strip()[-200:]}"
        return w, None
    if not git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{b}").stdout.strip():
        o = resolve_branch(repo, b)
        if not o:
            return None, f"no branch {b}"
        git(repo, "branch", b, o)
    w.parent.mkdir(parents=True, exist_ok=True)
    r = git(repo, "worktree", "add", str(w), b)
    if r.returncode:
        return None, f"cannot make a worktree for {b}: {r.stderr.strip()[-300:]}"
    return w, None


def run_gates(work, t, gates, say=print):
    """The no-model gates on version t's checkout: tools/fork-port.py from the measure stage with no budget."""
    if gates == "none":
        return 0
    cmd = [sys.executable, str(ROOT / "tools/fork-port.py"), "--repo", str(work), "--target", t, "--from-stage", "measure",
           "--budget", "0", "--max-errors", "0"]
    cmd += ["--to-stage", "audit"] if gates == "compile" else ["--gates", gates]
    say(f"fork-branches: cascade: {t}: {' '.join(cmd[1:])}")
    return subprocess.run(cmd).returncode


def cascade_nodes(repo, spec):
    """(port branch, [None, versions...] of TO's chain, start index, TO) for `FROM..TO`; ValueError for bad input."""
    frm, sep, to = spec.partition("..")
    if not sep or not frm or not to:
        raise ValueError(f"cascade wants FROM..TO (e.g. neoforge-1.21.1..26.3 or 1.21.3..1.21.5), not {spec!r}")
    to = to[len("neoforge-"):] if to.startswith("neoforge-") else to
    port, versions = versions_to(repo, to)
    nodes = [None] + versions
    if frm in ("port", port):
        i = 0
    elif frm in versions:
        i = nodes.index(frm)
    elif frm.startswith("neoforge-") and frm[len("neoforge-"):] in versions:
        i = nodes.index(frm[len("neoforge-"):])
    else:
        raise ValueError(f"{frm} is not on {to}'s chain ({port} -> {' -> '.join(versions)})")
    return port, nodes, i, to


def needs_carry(repo, t, tref, pref, pv, port):
    """Is t up to date with its parent but short of the port commit the parent carries (port_taken behind it)? Then
    the cascade still carries the authors' change to the files the parent does not carry."""
    taken = port_taken(reader_of_ref(repo, tref), t)
    if not taken:
        return False
    port_new = pref if pv is None else git(repo, "merge-base", pref, resolve_branch(repo, port)).stdout.strip()
    return taken != port_new and git(repo, "merge-base", "--is-ancestor", taken, port_new).returncode == 0


def cascade(repo, spec, gates="ab", dry_run=False, say=print, runner=None, mode=None, budget=DEFAULT_BUDGET_LINES,
            out_dir=None):
    """`FROM..TO`: merge FROM's branch into the next branch up TO's chain, then that one into the next, ... up to TO,
    running the no-model gates on each branch a merge changed. FROM is a version or the port branch (its name, or
    `port`). Pushes nothing.
    mode None: stop at the first conflict or red branch. mode "plan": every merge, port branch through TO, on a
    throwaway clone (no real branch changes), never stopping at a decision; the decisions grouped and classified into
    CASCADE-PLAN.json / CASCADE-PLAN.md / ISSUE.md under out_dir (default build/fork-branches/plan). mode "apply": the
    same merges on the real branches, each decision file merged without the authors' change and an open REMERGE.json
    entry (or a default the tool carries out, logged in .port/DECISIONS.md); everything else goes on, the versions
    above included; a red branch still stops. -> 0, 1 (stopped), 2 (bad input)."""
    repo = pathlib.Path(repo).resolve()
    try:
        port, nodes, i, to = cascade_nodes(repo, spec)
    except ValueError as e:
        say(f"fork-branches: {e}"); return 2
    if mode == "plan":
        return cascade_plan(repo, spec, budget, out_dir, say)
    runner = runner or run_gates
    rows, merged, items, mech = [], [], [], []
    resolve = {"budget": budget} if mode in ("apply", "plan-run") else None
    for k in range(i, len(nodes) - 1):
        pv, t = nodes[k], nodes[k + 1]
        pname = port if pv is None else branch_name(pv)
        pref, tref = resolve_branch(repo, pname), resolve_branch(repo, branch_name(t))
        if not pref or not tref:
            say(f"fork-branches: no branch {pname if not pref else branch_name(t)}"); return 2
        if git(repo, "merge-base", "--is-ancestor", pref, tref).returncode == 0 and not needs_carry(repo, t, tref, pref, pv, port):
            rows.append((t, "up to date", ""))
            continue
        if dry_run:
            n = git(repo, "rev-list", "--count", f"{tref}..{pref}").stdout.strip()
            rows.append((t, f"would merge {pname} ({n} commit(s))" if n != "0" else
                         "would carry the authors' change to files the parent does not carry", ""))
            continue
        work, why = worktree_for(repo, t)
        if work is None:
            rows.append((t, "STOPPED", why)); break
        ok, notes, left, pending, its, mm = merge_up(repo, work, t, pname, pref, port, pv, say, resolve)
        items += its
        mech += mm
        for n in notes:
            say(f"fork-branches: cascade: {t}: {n}")

        def show_pending():
            for f, diff in pending.items():
                say(f"fork-branches: the authors' change to {f} ({pname} does not carry it, {t} changed it by hand) -- "
                    f"apply it by hand through {t}'s rows (the diff is in the port branch's names):")
                for ln in diff.splitlines():
                    say("    " + ln)
        if not ok:
            rows.append((t, "STOPPED: conflict", ", ".join((left or list(pending))[:6]) or "; ".join(notes[-1:])))
            say(f"fork-branches: cascade STOPPED at {t}: {pname} does not merge into {branch_name(t)} by itself -- "
                f"{', '.join(left[:6])}. Merge it by hand (git checkout {branch_name(t)} && git merge {pname}), resolve, "
                f"commit, then: python3 tools/fork-branches.py cascade --repo {repo} {t}..{to}"
                + (" -- the authors' change below reaches no merge: apply it too, or let the next cascade offer it again "
                   "(port_taken)" if pending else "") + " (or plan the whole chain first: cascade --plan, then --apply)")
            show_pending()
            break
        merged.append(t)
        if pending and resolve is None:
            rows.append((t, "STOPPED: merged, the authors' change to re-apply by hand", ", ".join(list(pending)[:6])))
            say(f"fork-branches: cascade STOPPED at {t}: merged, but the authors' change to {', '.join(list(pending)[:6])} "
                f"reaches {t} through no merge and {t} changed the file by hand. On {branch_name(t)}: apply each diff "
                f"below through {t}'s rows, commit, remove its entry from .port/{t}/REMERGE.json (the done check lists it "
                f"until the file changes), commit, then: python3 tools/fork-branches.py cascade --repo {repo} {t}..{to}")
            show_pending()
            break
        rc = runner(work, t, gates, say) if mode != "plan-run" else 0
        if rc not in (0, 17, UNDONE_EXIT):
            rows.append((t, f"merged, RED (fork-port exit {rc})", f".port/{t}/RESIDUE.md"))
            say(f"fork-branches: cascade STOPPED at {t}: red after the merge -- start ONE fixer there (python3 tools/fork-port.py "
                f"--repo {work} --target {t} --from-stage measure, with a budget), commit, then cascade {t}..{to}")
            break
        n_open = sum(1 for x in its if not x.get("executed"))
        rows.append((t, "merged" + ("" if gates == "none" or mode == "plan-run" else f", gates exit {rc}")
                     + (f", {n_open} decision(s) open" if n_open else ""), ""))
    say("")
    say("| version | result | detail |")
    say("|---|---|---|")
    for t, res, det in rows:
        say(f"| {t} | {res} | {det or '-'} |")
    if mode in ("apply", "plan-run"):
        cascade_run_result.update(rows=rows, items=items, mech=mech, merged=merged)
    if mode == "apply":
        plan = plan_of(spec, rows, items, mech, 0.0, budget)
        write_plan(repo, plan, out_dir, say)
    if merged and mode != "plan-run":
        say(f"fork-branches: nothing was pushed: git push origin {' '.join(branch_name(t) for t in merged)}")
    return 1 if rows and rows[-1][1].startswith(("STOPPED", "merged, RED")) else 0


cascade_run_result = {}


def plan_of(spec, rows, items, mech, elapsed, budget):
    """The decisions of a cascade, grouped: one per (file, authors' hunk) whatever versions it shows at."""
    groups = {}
    for it in items:
        g_ = groups.setdefault(it["id"], {"id": it["id"], "file": it["file"], "diff": it["diff"], "source": it["source"],
                                          "versions": [], "per_version": {}, "class": it["class"], "escalate": False,
                                          "default": it["default"], "choice": it["choice"], "reasoning": it["reasoning"],
                                          "executed": True})
        if it["version"] not in g_["versions"]:
            g_["versions"].append(it["version"])
        g_["per_version"][it["version"]] = {k: it.get(k) for k in ("class", "default", "choice", "reasoning", "ours",
                                                                     "inherited_from", "executed")}
        g_["executed"] = g_["executed"] and bool(it.get("executed"))
        if it["escalate"]:
            g_["escalate"] = True
        if CLASS_ORDER.index(it["class"]) < CLASS_ORDER.index(g_["class"]) or (it["escalate"] and g_["class"] != "escalate"):
            g_.update({k: it[k] for k in ("class", "default", "choice", "reasoning")})
    decisions = sorted(groups.values(), key=lambda x: (not x["escalate"], x["file"]))
    for d in decisions:
        d["status"] = ("escalated: the owner decides (ISSUE.md)" if d["escalate"] else
                       "decided by the cascade (logged in DECISIONS.md)" if d["executed"] else
                       "default: the fixer agent carries it out (cascade --apply-decisions) and logs it")
    return {"tool": "tools/fork-branches.py cascade", "range": spec, "elapsed_s": round(elapsed, 3), "budget_lines": budget,
            "versions": [{"version": t, "result": res, "detail": det} for t, res, det in rows],
            "mechanical": mech, "decisions": decisions,
            "open": sum(1 for d in decisions if not d["executed"]),
            "escalated": sum(1 for d in decisions if d["escalate"])}


DECISIONS_BEGIN, DECISIONS_END = "<!-- cascade-decisions:begin -->", "<!-- cascade-decisions:end -->"


def write_plan(repo, plan, out_dir, say=print):
    """CASCADE-PLAN.json, CASCADE-PLAN.md and (with an escalated decision) ISSUE.md -- the one issue for the owner,
    with a machine-readable block --apply-decisions reads back."""
    out = pathlib.Path(out_dir) if out_dir else pathlib.Path(repo) / "build/fork-branches/plan"
    out.mkdir(parents=True, exist_ok=True)
    (out / "CASCADE-PLAN.json").write_text(json.dumps(plan, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    md = [f"# Cascade plan: {plan['range']}", "",
          f"{len(plan['mechanical'])} file(s) merged mechanically, {len(plan['decisions'])} decision(s): "
          f"{plan['escalated']} escalated to the owner, {plan['open']} open in all. Planned in {plan['elapsed_s']} s "
          "(git and the rows only, no model).", "", "| version | result |", "|---|---|"]
    md += [f"| {v['version']} | {v['result']} |" for v in plan["versions"]]
    for m in plan["mechanical"]:
        md.append(f"- mechanical: `{m['file']}` at {m['version']} -- {m['how']}")
    for d in plan["decisions"]:
        md += ["", f"## {d['id']}: `{d['file']}` ({d['class']}) -- {', '.join(d['versions'])}", "",
               f"- {d['status']}", f"- default: {d['default'] or '-'}; recommended choice: `{d['choice']}`",
               f"- reasoning: {d['reasoning']}", "", "The authors' change:" if d["source"] == "authors" else
               "The parent version's change:", "", "```diff", d["diff"].rstrip("\n"), "```"]
        for v, pv in d["per_version"].items():
            md += ["", f"Ours at {v}" + (f" (carried from {pv['inherited_from']})" if pv.get("inherited_from") else "") + ":",
                   "", "```java" if d["file"].endswith(".java") else "```", "\n".join((pv.get("ours") or "").splitlines()[:60]), "```"]
    (out / "CASCADE-PLAN.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    esc = [d for d in plan["decisions"] if d["escalate"]]
    issue = out / "ISSUE.md"
    if esc:
        body = [f"# Cascade {plan['range']}: {len(esc)} decision(s) for the owner", "",
                "The cascade carried the authors' new code up every version and decided everything it could (the "
                "fork-versions decision policy). These items need you: keeping the authors' design here is not feasible "
                "as is, or it costs more than the budget. Each affected file is merged without the authors' change and "
                "recorded open; every other file and version went on.", "",
                "Answer by editing `choice` in the block at the end (`take`: the authors' file through each version's "
                "rows, our change there dropped; `keep`: ours, the authors' change not taken; `patch`: a diff in the "
                "port branch's names applied at each version; `content`: the file text, per version or for all), then "
                "the agent runs `tools/fork-branches.py cascade --apply-decisions <the block saved to a file>`.", ""]
        for d in esc:
            body += [f"## {d['id']}: `{d['file']}` at {', '.join(d['versions'])}", "", f"Why it is yours: {d['reasoning']}", "",
                     "Options: `take` / `keep` / `patch` / `content`. Recommendation: " + f"`{d['choice']}`.", "",
                     "<details><summary>the authors' change</summary>", "", "```diff", d["diff"].rstrip("\n"), "```", "",
                     "</details>", ""]
            for v, pv in d["per_version"].items():
                body += [f"<details><summary>ours at {v}</summary>", "", "```", "\n".join((pv.get("ours") or "").splitlines()[:80]),
                         "```", "", "</details>", ""]
        block = {"range": plan["range"], "decisions": [{"id": d["id"], "file": d["file"], "versions": d["versions"],
                                                        "choice": d["choice"], "recommended": d["choice"], "by": "owner",
                                                        "reason": ""} for d in esc]}
        body += [DECISIONS_BEGIN, "```json", json.dumps(block, indent=1), "```", DECISIONS_END]
        issue.write_text("\n".join(body) + "\n", encoding="utf-8")
    elif issue.is_file():
        issue.unlink()
    say(f"fork-branches: cascade plan: {out / 'CASCADE-PLAN.json'} ({len(plan['decisions'])} decision(s), "
        f"{plan['escalated']} escalated" + (f"; the owner's issue body: {issue}" if esc else "") + ")")
    return out


def cascade_plan(repo, spec, budget, out_dir, say=print):
    """cascade --plan: the whole chain merged on a throwaway bare clone (its objects shared, its refs its own), no gates,
    nothing stopped by a decision; the real branches untouched. Writes the plan files. -> 0, 1 (a merge could not run)."""
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="cascade-plan-") as d:
        clone = pathlib.Path(d) / "plan.git"
        r = git(repo, "clone", "-q", "--bare", "--shared", str(repo), str(clone))
        if r.returncode:
            say(f"fork-branches: cascade --plan: cannot make a throwaway clone: {r.stderr.strip()[-200:]}"); return 1
        git(clone, "fetch", "-q", str(repo), "+refs/remotes/*:refs/remotes/*")
        git(clone, "symbolic-ref", "HEAD", "refs/heads/__cascade-plan-none__")   # no branch "checked out" in the clone
        git(clone, "config", "user.name", git(repo, "config", "user.name").stdout.strip() or "cascade-plan")
        git(clone, "config", "user.email", git(repo, "config", "user.email").stdout.strip() or "cascade-plan@localhost")
        cascade_run_result.clear()
        lines = []
        rc = cascade(clone, spec, "none", say=lines.append, mode="plan-run", budget=budget)
        res = dict(cascade_run_result)
    plan = plan_of(spec, res.get("rows", []), res.get("items", []), res.get("mech", []), time.monotonic() - t0, budget)
    for ln in lines:
        if ln.startswith("| ") or "STOPPED" in ln:
            say(ln.replace(str(clone), str(repo)))
    write_plan(repo, plan, out_dir, say)
    say(f"fork-branches: cascade --plan: {plan['elapsed_s']} s; no real branch changed")
    return 1 if rc == 1 else 0


def read_decisions(path):
    """The decisions JSON: a file holding it, or an issue body / comment with the cascade-decisions block."""
    text = pathlib.Path(path).read_text(encoding="utf-8")
    if DECISIONS_BEGIN in text:
        text = text.split(DECISIONS_BEGIN, 1)[1].split(DECISIONS_END, 1)[0]
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip())
    data = json.loads(text)
    return data if isinstance(data, dict) else {"decisions": data}


def apply_decisions(repo, path, gates="ab", say=print, budget=DEFAULT_BUDGET_LINES, out_dir=None, runner=None):
    """cascade --apply-decisions FILE: each answer (by decision id) applied at every version whose REMERGE.json holds
    that decision, oldest first -- take (the authors' file through the version's rows), keep, patch (a diff in the port
    branch's names, through the version's rows) or content -- its entry cleared, the choice logged in that branch's
    .port/DECISIONS.md, one commit per version; then the cascade --apply over the range, which merges the commits up
    and writes the new plan (a new decision it raises goes into a follow-up ISSUE.md). Pushes nothing. -> exit code."""
    repo = pathlib.Path(repo).resolve()
    try:
        data = read_decisions(path)
    except (OSError, ValueError) as e:
        say(f"fork-branches: --apply-decisions {path}: {e}"); return 2
    spec = data.get("range") or ""
    try:
        port, nodes, _i, to = cascade_nodes(repo, spec)
    except ValueError as e:
        say(f"fork-branches: --apply-decisions: {e}"); return 2
    answers = {d["id"]: d for d in data.get("decisions", []) if d.get("id") and d.get("choice")}
    bad = [i for i, d in answers.items() if d["choice"] not in ("take", "keep", "patch", "content")]
    if bad:
        say(f"fork-branches: --apply-decisions: unknown choice for {', '.join(bad)} (take, keep, patch or content)"); return 2
    done_ids, failed = set(), []
    for t in nodes[1:]:
        tref = resolve_branch(repo, branch_name(t))
        ents = remerge_entries(blob_at(repo, tref, f"{REC}/{t}/REMERGE.json"))
        mine = [c for c in ents if c.get("id") in answers]
        if not mine:
            continue
        work, why = worktree_for(repo, t)
        if work is None:
            say(f"fork-branches: --apply-decisions {t}: {why}"); return 2
        read = reader_of_root(work)
        pr = port_ref_of(repo, work, t, git(work, "rev-parse", "HEAD", check=True).stdout.strip())
        logged, cleared = [], []
        for c in mine:
            a, f = answers[c["id"]], c["overlay"]
            choice = a["choice"]
            if choice == "take":
                if f.endswith(".java"):
                    new = rows_tree(repo, pr, read, t, chain(work, t)).get(f)
                else:
                    new = blob_at(repo, pr, f)
                if new is None:
                    failed.append(f"{c['id']} at {t}: the authors' {f} is not on the port commit {t} carries")
                    continue
                (work / f).parent.mkdir(parents=True, exist_ok=True)
                (work / f).write_bytes(new)
            elif choice == "content":
                body = a.get("content")
                body = body.get(t) if isinstance(body, dict) else body
                if body is None:
                    failed.append(f"{c['id']} at {t}: no content for {t}"); continue
                (work / f).parent.mkdir(parents=True, exist_ok=True)
                (work / f).write_bytes(body.encode("utf-8", "surrogateescape"))
            elif choice == "patch":
                patch = a.get("patch") or ""
                patch = patch.get(t, "") if isinstance(patch, dict) else patch
                if f.endswith(".java"):
                    patch = apply_rules(patch, rules_of(chain_table(read, t, chain(work, t))))
                r = git(work, "apply", "--recount", "-", input=patch)
                if r.returncode:
                    failed.append(f"{c['id']} at {t}: the patch does not apply to {f}: {r.stderr.strip()[-200:]}"); continue
            git(work, "add", "-A", "--", f)
            cleared.append(c)
            logged.append({"id": c["id"], "file": f, "version": t, "choice": choice, "by": a.get("by") or "?",
                           "reasoning": a.get("reason") or c.get("reasoning", "")})
            done_ids.add(c["id"])
        if not cleared:
            continue
        rm = work / REC / t / "REMERGE.json"
        keep_ = [c for c in remerge_entries(rm.read_bytes() if rm.is_file() else b"") if c not in cleared]
        rm.write_text(json.dumps({"conflicts": keep_}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        git(work, "add", "--", rm.relative_to(work).as_posix())
        append_decisions(work, logged)
        r = git(work, "commit", "-q", "-m", f"{branch_name(t)}: cascade decisions {', '.join(sorted({x['id'] for x in logged}))}"
                f" applied\n\nLogged in .port/DECISIONS.md." + TRAILER)
        if r.returncode:
            say(f"fork-branches: --apply-decisions {t}: commit failed: {(r.stdout + r.stderr).strip()[-200:]}"); return 2
        say(f"fork-branches: --apply-decisions {t}: {len(logged)} decision(s) applied, their entries cleared")
    for x in failed:
        say(f"fork-branches: --apply-decisions: NOT applied: {x}")
    missing = sorted(set(answers) - done_ids)
    if missing:
        say(f"fork-branches: --apply-decisions: no open entry holds {', '.join(missing)} (answered already, or a stale block)")
    say("fork-branches: --apply-decisions: merging the decisions up and re-planning (cascade --apply)")
    rc = cascade(repo, spec, gates, say=say, mode="apply", budget=budget, out_dir=out_dir, runner=runner)
    return 1 if failed or rc else 0


def review_authors(repo, rng, reviewer, note, verdict="ok", say=print):
    """One review of the authors' change between two port branch commits (`A..B`), recorded as a row of
    .port/AUTHORS-REVIEWS.md on every version branch (one commit each): a version whose only reviewed-input change
    since its REVIEW.md commit is that change through its rows (no file of it a review row) stays reviewed by it."""
    repo = pathlib.Path(repo).resolve()
    a, sep, b = rng.partition("..")
    ca, cb = (git(repo, "rev-parse", "--verify", "--quiet", f"{x}^{{commit}}").stdout.strip() for x in (a, b))
    if not sep or not ca or not cb or verdict not in ("ok", "not-ok") or not reviewer.strip():
        say("fork-branches: review-authors wants --range A..B (two commits), --reviewer and --verdict ok|not-ok"); return 2
    dg = authors_digest(repo, ca, cb)
    row = f"| {dg} | {ca[:12]}..{cb[:12]} | {verdict} | {reviewer.replace('|', '/')} | {note.replace('|', '/')} |\n"
    for t in sorted(version_branches(repo), key=mc_key):
        work, why = worktree_for(repo, t)
        if work is None:
            say(f"fork-branches: review-authors {t}: {why}"); return 2
        p = work / REC / "AUTHORS-REVIEWS.md"
        text = read_file(p) or ("# Reviews of the authors' change (tools/fork-branches.py review-authors; append-only)\n\n"
                                "| digest | port range | verdict | reviewer | note |\n|---|---|---|---|---|\n")
        if dg in text:
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + row, encoding="utf-8")
        git(work, "add", "--", p.relative_to(work).as_posix())
        git(work, "commit", "-q", "-m", f"{branch_name(t)}: review of the authors' change {ca[:12]}..{cb[:12]}" + TRAILER)
    say(f"fork-branches: review-authors: {ca[:12]}..{cb[:12]} ({verdict}) recorded on every version branch")
    return 0


def authors_digest(repo, a, b):
    return hashlib.sha256(git(repo, "diff", a, b, "--", "src", binary=True).stdout).hexdigest()[:24]


def authors_reviewed(repo, root, t, c, ref):
    """Is the change since commit c the authors' change through the rows only, with one review of it on record
    (AUTHORS-REVIEWS.md)? -> the range when so, else None."""
    pa, pb = port_ref_of(repo, root, t, c), port_ref_of(repo, root, t, ref)
    if not pa or not pb or pa == pb:
        return None
    dg = authors_digest(repo, pa, pb)
    for ln in read_file(pathlib.Path(root) / REC / "AUTHORS-REVIEWS.md").splitlines():
        cells = [x.strip() for x in ln.strip().strip("|").split("|")]
        if len(cells) >= 4 and cells[0] == dg and cells[2] == "ok" and cells[3]:
            return f"{pa[:12]}..{pb[:12]}"
    return None


def write_test_items(repo, t):
    """.port/<t>/TEST-ITEMS.md on version t's checkout: what a person must look at on this version because no gate
    judges it (no model): each STUBS.json entry, each file changed from the parent branch (flagged mixin / client),
    each dependency DEPS.md says was dropped, a preview version, and each base behaviour item that is not the same
    here. Rewritten only when it changed. -> (count, None) or (None, why)."""
    repo = pathlib.Path(repo).resolve()
    try:
        files = root_files(repo, repo, True)
        items = review_items(repo, repo, t, "HEAD", True, files)
        lines = [f"# Manual-test items: Minecraft {t}", "",
                 "Generated by tools/fork-port.py on the version branch (no model): what a person must look at on this",
                 "version because no gate judges it. The fork-versions skill's step 11 turns them into the test plan.", ""]
        n = 0
        for kind, f in items:
            if kind == "renames":
                continue
            text = files.get(f, b"").decode("utf-8", "replace")
            flags = [x for x, hit in (("mixin", "@Mixin" in text), ("client", "/client/" in f or "net.minecraft.client" in text)) if hit]
            lines.append(f"- {'stub' if kind == 'stub' else 'changed' if kind == 'changed' else kind}: `{f}`"
                         + (f" ({', '.join(flags)})" if flags else ""))
            n += 1
        for row in read_file(rec_dir(repo, t) / "DEPS.md").splitlines():
            if row.startswith("|") and re.search(r"\|\s*(?:excluded|dropped)\b", row, re.I):
                lines.append(f"- dropped-dep: {row.strip()[:200]}"); n += 1
        if info(repo, t).get("preview") == "true":
            lines.append("- preview: this version is a preview (never published)"); n += 1
        li = _mod("layer_inventory_fb", "layer-inventory.py")
        _c, rep, _l = li.check_branch(repo, repo, t, files=files)
        for r in (rep or {}).get("items", []):
            if r.get("status") != "same":
                lines.append(f"- behaviour: `{r['id']}` ({r.get('status')}: {str(r.get('detail', ''))[:160]})"); n += 1
    except Exception as e:      # noqa: BLE001 -- a broken plan is a stop with its reason, never a silent pass
        return None, f"{type(e).__name__}: {e}"[:300]
    if not n:
        lines.append("- none: no stub, changed file, dropped integration or behaviour change applies to this version")
    text = "\n".join(lines) + "\n"
    f = rec_dir(repo, t) / "TEST-ITEMS.md"
    if read_file(f) != text:
        f.write_text(text, encoding="utf-8")
    return n, None


# ---------------------------------------------------------------- a new version branch

def version_branches(repo):
    """{version: commit} of every version branch in the clone (local first, then origin's)."""
    out = {}
    for pat in ("refs/heads/neoforge-*", "refs/remotes/origin/neoforge-*"):
        for line in git(repo, "for-each-ref", "--format=%(refname:short) %(objectname)", pat).stdout.splitlines():
            name, c = line.rsplit(" ", 1)
            v = name.split("neoforge-", 1)[1]
            if v not in out and git(repo, "cat-file", "-e", f"{c}:{REC}/{v}/{BRANCH_PROPS}").returncode == 0:
                out[v] = c
    return out


def shared_helpers_for(versions_, mv_pkg, modid, build_text):
    """{rel path under src/main/java: text} of the shared library's helper classes for `versions_` (nearest first:
    the nearest version's copy wins), placeholders filled; a helper whose `// REQUIRES: <token>` the build does not
    name is left out (make-fork-layer.py's rule)."""
    pool = {}
    for v in reversed(versions_):
        d = shared_dir() / v / "java"
        for f in sorted(d.glob("*.java")) if d.is_dir() else []:
            text = f.read_text(encoding="utf-8")
            if any(n not in build_text for n in re.findall(r"(?m)^// REQUIRES: (\S+)", text)):
                continue
            pool[f"{mv_pkg.replace('.', '/')}/{f.name}"] = text.replace("@MVPKG@", mv_pkg).replace("@MODID@", modid)
    return pool


def used_helpers(pool, java_texts):
    """The helpers of `pool` the tree uses, transitively (templates/fork-layer/tools/layer.py add_shared_helpers)."""
    text = "\n".join(java_texts)
    fqn = lambda rel: rel[:-5].replace("/", ".")
    added, changed = {}, True
    while changed:
        changed = False
        for rel, body in pool.items():
            if rel in added:
                continue
            name = rel.rsplit("/", 1)[-1][:-5]
            if fqn(rel) in text or (fqn(rel).rsplit(".", 1)[0] + ".*") in text or f"MV-REQUIRES: {name}" in text:
                added[rel] = body
                text += "\n" + body
                changed = True
    return added


def add_target(repo, t, parent=None, allow_undone_parent=False, port_branch=None, say=print):
    """Make version branch neoforge-<t> from its parent's (the nearest lower version branch, or --parent, else the port
    branch): its versions from templates/fork-layer/versions.json written into gradle.properties, branch.properties
    and the mods.toml ranges; the shared library of every targeted version above the parent up to t applied -- its
    rows over the source (a file the rows alone made at the parent is re-made from the port branch through t's
    whole chain, any other through the rows t adds), its helper classes the tree uses, its access-transformer lines;
    @OnlyIn stripped from 1.21.6. Refused (exit 18, nothing written) while the parent version is not done, unless
    allow_undone_parent (recorded). Plumbing only: no checkout is touched. -> exit code."""
    repo = pathlib.Path(repo).resolve()
    mfl = load("make_fork_layer_fb", "make-fork-layer.py")
    ln = mfl.line(t)
    if ln is None or not ln.get("stable"):
        say(f"fork-branches: {t} is not a targeted line of templates/fork-layer/versions.json"
            + (f": {ln['skip']}" if ln and ln.get("skip") else "")); return 2
    if resolve_branch(repo, branch_name(t)):
        say(f"fork-branches: {branch_name(t)} exists already"); return 2
    have = version_branches(repo)
    base_mc = mfl.versions()["base"]
    pb = port_branch or (parse_props(reader_of_ref(repo, next(iter(have.values())))(f"{REC}/{next(iter(have))}/{BRANCH_PROPS}"))
                         .get("port_branch") if have else None) or branch_name(base_mc)
    if parent in ("port", pb):
        parent = None                                   # a jump from the port branch itself
    elif parent is None:
        lower = sorted((v for v in have if mc_key(v) < mc_key(t)), key=mc_key)
        parent = lower[-1] if lower else None
    elif parent not in have:
        say(f"fork-branches: --parent {parent}: no version branch {branch_name(parent)}"); return 2
    start = have.get(parent) if parent else resolve_branch(repo, pb)
    if not start:
        say(f"fork-branches: no branch {branch_name(parent) if parent else pb}"); return 2
    allowed = ""
    if parent:
        why = target_done(repo, parent)
        if why and not allow_undone_parent:
            say(f"fork-branches: REFUSED {t}: the version it stands on, {parent}, is not done:")
            for w in why[:12]:
                say(f"  - {w}")
            say(f"fork-branches: finish {parent} first (python3 tools/fork-port.py --repo {repo} --target {parent} --check-done), "
                "or for development only pass --allow-undone-parent (recorded)")
            return UNDONE_EXIT
        if why:
            allowed = f"{parent} {datetime.date.today().isoformat()}: {why[0][:160]}"
    files = ref_files(repo, start)
    modes = {p: m for p, (m, _b) in tree_entries(repo, start).items()}
    port_now = git(repo, "merge-base", start, resolve_branch(repo, pb)).stdout.strip() if parent else start
    pp = parse_props(files.get(f"{REC}/{parent}/{BRANCH_PROPS}", b"").decode("utf-8", "surrogateescape")) if parent else {}
    gp = parse_props(files.get("gradle.properties", b"").decode("utf-8", "surrogateescape"))      # often ISO-8859-1
    toml = next((files[p] for p in sorted(files) if p.endswith("META-INF/neoforge.mods.toml") and p.startswith("src/")), b"")
    modid = (re.search(rb'(?m)^\s*modId\s*=\s*"([^"]+)"', toml) or [None, gp.get("mod_id", "").encode("utf-8", "surrogateescape")])[1].decode("utf-8", "replace")
    first = next((files[p] for p in sorted(files) if p.startswith("src/main/java/") and p.endswith(".java")), b"")
    pkg = re.search(rb"(?m)^package\s+([\w.]+)\s*;", first)
    mv_pkg = pp.get("mv_package") or ".".join((pkg.group(1).decode("utf-8", "replace").split(".") if pkg else ["mv"])[:2]) + ".mv"
    order = [l["mc"] for l in mfl.versions()["lines"] if l.get("stable")]
    lo = parent or base_mc
    new_versions = [v for v in order if mc_key(lo) < mc_key(v) <= mc_key(t)][::-1]        # nearest first
    neo = ln["neo"]
    mc_range, neo_range = mfl.ranges(t, neo)
    props = {"prop.minecraft_version": t, "prop.neo_version": neo}
    if ln.get("geckolib") and "geckolib_version" in gp:
        props["prop.geckolib_version"] = ln["geckolib"]
    keys = [f"toml.minecraft={mc_range}", f"toml.neoforge={neo_range}", f"java_version={ln.get('java', 21)}"]
    if ln.get("nfrt"):
        keys.append(f"nfrt_version={ln['nfrt']}")
    if ln.get("geckolib_group") and "geckolib_version" in gp:
        old = f"software.bernie.geckolib:geckolib-neoforge-{t}"
        keys.append("substitute." + mfl.substitute_line(f"{old}={ln['geckolib_group']}:geckolib-neoforge-{t}:{ln['geckolib']}"))
    strip = mc_key(t) >= mc_key("1.21.6")
    if strip:
        keys.append("strip_onlyin=true")
    head = [f"# Version branch {branch_name(t)} (tools/fork-branches.py add-target): Minecraft {t} on NeoForge {neo}.",
            f"minecraft_version={t}", f"branch={branch_name(t)}", f"parent={branch_name(parent) if parent else pb}"]
    if parent:
        head.append(f"parent_minecraft_version={parent}")
    head += [f"port_branch={pb}", f"base_minecraft_version={pp.get('base_minecraft_version', base_mc)}", f"mv_package={mv_pkg}",
             f"port_taken={port_now}"]
    for k in ("upstream", "upstream_commit", "library"):
        if pp.get(k):
            head.append(f"{k}={pp[k]}")
    head += [f"{k}={v}" for k, v in sorted(pp.items()) if k.startswith("library.")]
    head += [f"{k}={v}" for k, v in props.items()] + keys
    if allowed:
        head.append(f"allowed_undone_parent={allowed}")
    rd, rd_parent = f"{REC}/{t}", f"{REC}/{parent}"
    sub = lambda x: x.replace("@MVPKG@", mv_pkg).replace("@MODID@", modid)
    sections = [f"# ---- shared {v}\n" + sub((shared_dir() / v / "renames.tsv").read_text(encoding="utf-8"))
                for v in new_versions if (shared_dir() / v / "renames.tsv").is_file()]
    inherited = files.get(f"{REC}/{parent}/shared-renames.tsv", b"").decode("utf-8", "surrogateescape") if parent else ""
    files[f"{rd}/{BRANCH_PROPS}"] = ("\n".join(head) + "\n").encode("utf-8")
    files[f"{rd}/renames.tsv"] = f"# {t}: from<TAB>to (plain type, member:, re:) -- see prepare-sources.py\n".encode("utf-8")
    shared_text = "\n".join(sections + ([inherited.rstrip("\n")] if inherited.strip() else [])) + "\n"
    if shared_text.strip():
        files[f"{rd}/shared-renames.tsv"] = shared_text.encode("utf-8")
    read = lambda p: files[p].decode("utf-8", "surrogateescape") if p in files else ""
    ch = [t] + (chain_read(read, parent) if parent else [])
    rows_t = rows_tree(repo, port_now, read, t, ch, strip=strip)
    rows_p = rows_tree(repo, port_now, read, parent, chain_read(read, parent)) if parent else None
    own = rules_of(own_table(read, t, parent))
    remade = moved = 0
    for path in [p for p in files if re.match(r"^src/[^/]+/java/.+\.java$", p)]:
        mech = rows_p is None or files[path] == rows_p.get(path)
        if mech and path in rows_t:
            new = rows_t[path]; remade += 1
        else:
            text = apply_rules(files[path].decode("utf-8", "surrogateescape"), own)
            new = (ONLYIN.sub("", text) if strip else text).encode("utf-8", "surrogateescape"); moved += 1
        files[path] = new
    gp_text = files.get("gradle.properties", b"").decode("utf-8", "surrogateescape")
    files["gradle.properties"] = set_props_text(gp_text, {k[5:]: v for k, v in props.items()}).encode("utf-8", "surrogateescape")
    for p in [p for p in files if p.startswith("src/") and p.endswith("META-INF/neoforge.mods.toml")]:
        files[p] = toml_rewrite(files[p].decode("utf-8", "surrogateescape"), {"minecraft": mc_range, "neoforge": neo_range}).encode("utf-8", "surrogateescape")
    if not parent:
        bg = files.get("build.gradle", b"").decode("utf-8", "surrogateescape")
        if BRANCH_HOOK_LINE not in bg:
            files["build.gradle"] = (bg.rstrip("\n") + "\n" + BRANCH_HOOK).encode("utf-8")
        files[f"{REC}/branch.gradle"] = TPL.read_bytes()
    build_text = files.get("build.gradle", b"").decode("utf-8", "replace") + files["gradle.properties"].decode("utf-8", "replace")
    pool = shared_helpers_for(new_versions + [v for v in order if mc_key(v) <= mc_key(lo) and mc_key(v) > mc_key(base_mc)][::-1],
                              mv_pkg, modid, build_text)
    installed = helper_map(read(f"{rd_parent}/shared-helpers.txt")) if parent else {}
    java = [files[p].decode("utf-8", "replace") for p in files if p.startswith("src/main/java/") and p.endswith(".java")]
    helpers = []
    for rel, body in used_helpers(pool, java).items():
        dest = f"src/main/java/{rel}"
        if dest in files and installed.get(dest) != hash_blobs({dest: files[dest]})[dest]:
            continue                                    # the fork's own class of that name, or an edited helper, wins
        files[dest] = body.encode("utf-8")
        helpers.append(f"{dest} {hash_blobs({dest: files[dest]})[dest]}")
    if helpers:
        files[f"{rd}/shared-helpers.txt"] = ("# shared-library helpers as installed (path blob): tools/fork-branches.py\n"
                                             + "\n".join(sorted(helpers)) + "\n").encode("utf-8")
    at_lines = [l for v in new_versions if (shared_dir() / v / "accesstransformer.cfg").is_file()
                for l in sub((shared_dir() / v / "accesstransformer.cfg").read_text(encoding="utf-8")).splitlines()
                if l.strip() and not l.startswith("#")]
    if at_lines:
        cur = files.get(AT_DEFAULT, b"").decode("utf-8", "surrogateescape")
        add = [l for l in at_lines if l not in cur.splitlines()]
        if add:
            files[AT_DEFAULT] = (cur.rstrip("\n") + ("\n" if cur.strip() else "") + "\n".join(add) + "\n").encode("utf-8")
    if not parent and f"{REC}/INVENTORY.json" not in files:
        li = _mod("layer_inventory_fb", "layer-inventory.py")
        with tempfile.TemporaryDirectory() as d:
            base = export(repo, start, pathlib.Path(d) / "b")
            inv = li.build_inventory(base)
        inv["base"] = base_mc
        inv["src_tree"] = git(repo, "rev-parse", f"{start}:src").stdout.strip() or None
        files[f"{REC}/INVENTORY.json"] = (json.dumps(inv, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
    c = commit_tree(repo, files, modes, start,
                    f"Version branch {branch_name(t)}: Minecraft {t} from {branch_name(parent) if parent else pb}\n\n"
                    f"Versions from versions.json (NeoForge {neo}); the shared library of {', '.join(new_versions) or 'no version'}"
                    f" applied: {remade} file(s) re-made from {pb} through {t}'s rows, {moved} carried through the rows {t} adds,"
                    f" {len(helpers)} helper(s)." + TRAILER)
    git(repo, "branch", branch_name(t), c, check=True)
    say(f"fork-branches: made {branch_name(t)} at {c[:12]} from {branch_name(parent) if parent else pb}: NeoForge {neo}, shared "
        f"library {', '.join(new_versions) or '-'}, {len(helpers)} helper(s); next: git checkout {branch_name(t)} && python3 "
        f"tools/fork-port.py --repo {repo} --target {t}")
    return 0



def sync_shared(repo, targets=None, say=print):
    """Bring version branches up to the shared library's current tables and helpers (make-fork-layer.py sync-shared's
    job on a layer): per version, oldest first, on a clean checkout of its branch -- each `# ---- shared <v>` section of
    .port/<t>/shared-renames.tsv rewritten from templates/fork-layer/shared/<v>/renames.tsv; every .java file the rows
    alone made (equal to the old tables over the port branch) re-made through the new ones; each shared helper it carries
    as installed (unedited since) replaced by the library's copy, and a helper the tree now uses added; committed on
    the branch. The tables and shared-helpers.txt are reviewed inputs, so the version's review goes stale (re-review the
    change); the next cascade remakes mechanical files through the new rows. Pushes nothing. -> exit code."""
    repo = pathlib.Path(repo).resolve()
    have = version_branches(repo)
    order = sorted(have, key=mc_key)
    todo = [t for t in order if not targets or t in targets]
    if targets and set(targets) - set(order):
        say(f"fork-branches: no version branch for {', '.join(sorted(set(targets) - set(order)))}"); return 2
    above = [t for t in order if t not in todo and todo and mc_key(t) > mc_key(todo[0])]
    if above:
        say(f"fork-branches: sync-shared: WARNING: {', '.join(above)} not synced -- each keeps its old copy of the shared "
            f"tables, so a cascade from below re-makes the synced files back through them; sync them too (run "
            f"sync-shared without --target, the default: every version)")
    mfl = load("make_fork_layer_fb", "make-fork-layer.py")
    stable = [l["mc"] for l in mfl.versions()["lines"] if l.get("stable")]
    for t in todo:
        work, why = worktree_for(repo, t)
        if work is None:
            say(f"fork-branches: sync-shared {t}: {why}"); return 2
        files = root_files(repo, work, True)
        props = info(work, t)
        mv_pkg = props.get("mv_package") or "mv"
        toml = next((files[p] for p in sorted(files) if p.endswith("META-INF/neoforge.mods.toml") and p.startswith("src/")), b"")
        modid = (re.search(rb'(?m)^\s*modId\s*=\s*"([^"]+)"', toml) or [None, b""])[1].decode("utf-8", "replace")
        sub = lambda x: x.replace("@MVPKG@", mv_pkg).replace("@MODID@", modid)
        sp = work / REC / t / "shared-renames.tsv"
        old_text = read_file(sp)
        # every targeted version above the base up to t, nearest first (as add-target writes them): a version whose
        # library gained a table since gets its section, and its helpers count even with no table at all
        base_v = props.get("base_minecraft_version") or (stable[0] if stable else t)
        span = [v for v in stable if mc_key(base_v) < mc_key(v) <= mc_key(t)][::-1]
        old_secs = {k.split(" ", 1)[1]: b for k, b in shared_sections(old_text).items()}
        versions_ = span + [v for v in old_secs if v not in span]
        new_sections, kept = [], []
        for ver in versions_:
            f = shared_dir() / ver / "renames.tsv"
            if f.is_file():
                new_sections.append(f"# ---- shared {ver}\n" + sub(f.read_text(encoding="utf-8")))
            elif ver in old_secs:
                new_sections.append(old_secs[ver].rstrip("\n")); kept.append(ver)
        new_text = ("\n".join(x.rstrip("\n") for x in new_sections) + "\n") if new_sections else old_text
        pr = port_ref_of(repo, work, t, git(work, "rev-parse", "HEAD", check=True).stdout.strip())   # work's HEAD, not the clone's
        read = reader_of_root(work)
        rows_old = rows_tree(repo, pr, read, t, chain(work, t)) if pr else {}
        changed = []
        if new_text != old_text:
            sp.write_bytes(new_text.encode("utf-8")); changed.append(sp.relative_to(work).as_posix())
            rows_new = rows_tree(repo, pr, reader_of_root(work), t, chain(work, t)) if pr else {}
            for p, data in rows_old.items():
                if files.get(p) == data and rows_new.get(p) not in (None, data):
                    (work / p).write_bytes(rows_new[p]); changed.append(p)
        # helpers: an installed, unedited one follows the library; a newly used one is added
        listed = helper_blobs(work, t)
        build_text = files.get("build.gradle", b"").decode("utf-8", "replace") + files.get("gradle.properties", b"").decode("utf-8", "replace")
        pool = shared_helpers_for(sorted(set(versions_), key=mc_key, reverse=True), mv_pkg, modid, build_text)
        cur = root_files(repo, work, True)
        java = [d.decode("utf-8", "replace") for p, d in cur.items() if p.startswith("src/main/java/") and p.endswith(".java")]
        lines = dict(listed)
        for rel, body in used_helpers(pool, java).items():
            dest = f"src/main/java/{rel}"
            data = body.encode("utf-8")
            if dest in cur and listed.get(dest) != hash_blobs({dest: cur[dest]})[dest]:
                if cur[dest] != data:        # the fork's own class of that name, or an edited helper: left alone, said
                    say(f"fork-branches: sync-shared {t}: {dest} "
                        + ("was edited since it was installed" if dest in listed else "is the fork's own class, not the installed helper")
                        + ": left alone (the library's copy differs; merge it by hand if wanted)")
                continue
            if cur.get(dest) != data:
                (work / dest).parent.mkdir(parents=True, exist_ok=True)
                (work / dest).write_bytes(data); changed.append(dest)
            lines[dest] = hash_blobs({dest: data})[dest]
        if lines != listed:
            (work / REC / t / "shared-helpers.txt").write_bytes(
                ("# shared-library helpers as installed (path blob): tools/fork-branches.py\n"
                 + "".join(f"{k} {v}\n" for k, v in sorted(lines.items()))).encode("utf-8"))
            changed.append(f"{REC}/{t}/shared-helpers.txt")
        if not changed:
            say(f"fork-branches: sync-shared {t}: current"); continue
        git(work, "add", "-A", "--", *sorted(set(changed)), check=True)
        mm = git(ROOT, "rev-parse", "--short", "HEAD").stdout.strip() or "?"
        r = git(work, "commit", "-q", "-m", f"{branch_name(t)}: the shared library at mc-mod-version-upgrade {mm}\n\n"
                f"Tables {', '.join(v for v in versions_ if (shared_dir() / v / 'renames.tsv').is_file() or v in old_secs) or '-'} re-synced; the files the rows alone made re-made through them; the "
                f"shared helpers as installed updated." + TRAILER)
        if r.returncode:
            say(f"fork-branches: sync-shared {t}: commit failed: {(r.stdout + r.stderr).strip()[-200:]}"); return 2
        say(f"fork-branches: sync-shared {t}: {len(set(changed))} file(s) changed and committed on {branch_name(t)}"
            + (f" (no library copy of {', '.join(kept)}: kept as it was)" if kept else "")
            + "; its review is stale until a fresh reviewer reads the change")
    return 0


# ---------------------------------------------------------------- freeze, library record, CI

def freeze(repo, t, upstream=None, libraries=(), accept_drift=False, say=print):
    """The upstream freeze of version t's branch (tools/fork-port.py freeze_upstream into .port/<t>/branch.properties):
    the checkout must be on that branch. A --library pin alone never re-checks the authors' branch (issue #133, gap 3)."""
    repo = pathlib.Path(repo).resolve()
    if checkout_version(repo) != t:
        say(f"fork-branches: freeze writes .port/{t}/{BRANCH_PROPS}: check out {branch_name(t)} first"); return 2
    rec, why = fp().freeze_upstream(repo, upstream, libraries, accept_drift=accept_drift,
                                    props_file=rec_dir(repo, t) / BRANCH_PROPS)
    if not rec:
        say(f"fork-branches: freeze: {why}"); return 2
    say("fork-branches: freeze: " + ", ".join(f"{k}={v}" for k, v in rec.items()) + f" (.port/{t}/{BRANCH_PROPS}; commit it)")
    return 0


def origin_slug(repo):
    url = git(repo, "remote", "get-url", "origin").stdout.strip()
    m = re.search(r"github\.com[/:]([\w.-]+/[\w.-]+?)(?:\.git)?$", url)
    return m.group(1) if m else None


def library_record(lib, t, dependent, slug=None, say=print):
    """Record in library version t's DEPENDENTS.json that a dependent's version t passed Gate C against this
    library's jar: the dependent's .port/<t>/GATES.md (green Gate C, current at its HEAD) and its pin of the
    library's release jar (port_jar.<g:a>=<url> <sha256>, the URL naming this library's repository) -> the release
    tag, the library commit it was built from, the jar's sha256. Both checkouts on their version-t branches. The
    library version is done (the library rule) only with such an entry for a commit whose jar is current."""
    lib, dep = pathlib.Path(lib).resolve(), pathlib.Path(dependent).resolve()
    for d, what in ((lib, "library"), (dep, "dependent")):
        if checkout_version(d) != t:
            say(f"fork-branches: the {what} checkout {d} is not on {branch_name(t)}"); return 2
    slug = slug or origin_slug(lib)
    if not slug:
        say("fork-branches: cannot read the library's GitHub repository from its origin: pass --slug owner/repo"); return 2
    files = root_files(dep, dep, True)
    head = git(dep, "rev-parse", "HEAD").stdout.strip()
    g = gates_problems(dep, dep, t, head, True, files)
    if g:
        say(f"fork-branches: the dependent's Gate C is not green and current: {g[0]}"); return 2
    gates_commit = re.search(r"at `([0-9a-f]{7,40})", read_file(rec_dir(dep, t) / "GATES.md")).group(1)
    pins = [(k, v) for k, v in info(dep, t).items() if k.startswith("port_jar.")]
    hit = None
    for k, v in pins:
        url, _, sha = v.partition(" ")
        m = re.match(r"^https://github\.com/([^/]+/[^/]+)/releases/download/([^/]+)/", url)
        if m and m.group(1).lower() == slug.lower():
            hit = (k[len("port_jar."):], url, sha.strip(), m.group(2))
    if not hit:
        say(f"fork-branches: the dependent pins no jar of {slug}'s releases (port_jar.* in .port/{t}/{BRANCH_PROPS}): it was not "
            "built against a release of this library"); return 2
    ga, url, sha, tag = hit
    git(lib, "fetch", "-q", "--tags", "origin", env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    lc = git(lib, "rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}").stdout.strip()
    if not lc:
        say(f"fork-branches: the library clone has no tag {tag} (git fetch --tags)"); return 2
    f = rec_dir(lib, t) / "DEPENDENTS.json"
    try:
        data = json.loads(read_file(f) or "[]")
    except ValueError:
        data = []
    entry = {"dependent": origin_slug(dep) or str(dep), "dependent_commit": head, "gates_commit": gates_commit,
             "gate_c": "PASS", "library": ga, "library_tag": tag, "library_commit": lc, "jar_sha256": sha,
             "recorded": datetime.date.today().isoformat()}
    data = [e for e in data if not (e.get("dependent") == entry["dependent"] and e.get("library_commit") == lc)] + [entry]
    f.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    say(f"fork-branches: recorded in {f.relative_to(lib)}: {entry['dependent']} passed Gate C at {gates_commit} against "
        f"{tag} ({sha[:16]}...); commit it on {branch_name(t)}")
    return 0


def ci_args(repo, t, modid):
    """The tools/port-ci.py arguments that give version branch t the single-version workflow at its version: the port
    branch's installed workflow read back (port-ci.py recover_args), without --layer-branch, its --dep-jar specs for
    the sibling libraries replaced by this version's pins (the library's per-version release, installed under the
    coordinate branch.gradle substitutes), and a library's Gate C without the spawn and battle phases."""
    t_ref = resolve_branch(repo, branch_name(t))
    read = reader_of_ref(repo, t_ref)
    p = parse_props(read(f"{REC}/{t}/{BRANCH_PROPS}"))
    pc = load("port_ci_fb", "port-ci.py")
    wf = git(repo, "show", f"{resolve_branch(repo, p.get('port_branch', 'neoforge-1.21.1'))}:.github/workflows/port-ci.yml")
    args = pc.recover_args(wf.stdout) if wf.returncode == 0 else ["--modid", modid, "--gatec", "launch,spawn,battle,gauntlet"]
    out, i = [], 0
    while i < len(args):
        if args[i] == "--layer-branch":
            i += 2; continue
        if args[i] == "--modid":
            out += ["--modid", modid or args[i + 1]]; i += 2; continue
        if args[i] == "--dep-jar":
            ga = ":".join(args[i + 1].rsplit("=", 1)[1].split(":")[:2])
            pin = p.get(f"port_jar.{ga}")
            subst = p.get(f"substitute.{ga}")
            if pin and subst:
                url = pin.split()[0]
                m = re.match(r"^https://github\.com/([^/]+/[^/]+)/releases/download/([^/]+)/(.+)$", url)
                out += ["--dep-jar", f"{m.group(1)}@{m.group(2)}/{m.group(3)}={subst}"]
            else:
                out += ["--dep-jar", args[i + 1]]
            i += 2; continue
        if args[i] == "--gatec" and p.get("library") == "true":
            out += ["--gatec", ",".join(x for x in args[i + 1].split(",") if x not in ("spawn", "battle"))]; i += 2; continue
        out.append(args[i]); i += 1
    return out


def ci(repo, t, modid, run=False, say=print):
    repo = pathlib.Path(repo).resolve()
    if not resolve_branch(repo, branch_name(t)):
        say(f"fork-branches: no branch {branch_name(t)}"); return 2
    args = ci_args(repo, t, modid)
    say(f"fork-branches: {branch_name(t)}: python3 tools/port-ci.py --repo <its checkout> {' '.join(args)}")
    if not run:
        say(f"fork-branches: run it on a checkout of {branch_name(t)} (or pass --run: a worktree under build/fork-branches/), then push")
        return 0
    work, why = worktree_for(repo, t)
    if work is None:
        say(f"fork-branches: {why}"); return 2
    rc = subprocess.run([sys.executable, str(ROOT / "tools/port-ci.py"), "--repo", str(work), *args]).returncode
    if rc == 0:
        say(f"fork-branches: committed on {branch_name(t)} in {work}; nothing pushed: git push origin {branch_name(t)}")
    return rc


# ---------------------------------------------------------------- CLI

def main():
    if "--self-check" in sys.argv:
        return self_check()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    m = sub.add_parser("migrate"); m.add_argument("--repo", required=True); m.add_argument("--layer-ref")
    m.add_argument("--port-branch"); m.add_argument("--targets"); m.add_argument("--dry-run", action="store_true")
    m.add_argument("--json")
    v = sub.add_parser("verify"); v.add_argument("--repo", required=True); v.add_argument("--layer-ref")
    v.add_argument("--targets"); v.add_argument("--tip", action="store_true", help="compare the branch tips, not the migration commits")
    c = sub.add_parser("check-done"); c.add_argument("--repo", required=True); c.add_argument("--target", required=True)
    k = sub.add_parser("cascade"); k.add_argument("--repo", required=True)
    k.add_argument("range", metavar="FROM..TO", nargs="?", help="(not with --apply-decisions: the block names its range)")
    k.add_argument("--gates", default="ab", choices=["none", "compile", "ab", "full"],
                   help="the no-model gates per merged branch (default ab: compile, audits, Gate A/B)")
    k.add_argument("--dry-run", action="store_true")
    km = k.add_mutually_exclusive_group()
    km.add_argument("--plan", action="store_true", help="every merge on a throwaway clone, every decision found, grouped and "
                    "classified into the plan files; no real branch changes")
    km.add_argument("--apply", action="store_true", help="every merge on the real branches: mechanical and default items "
                    "resolved, an escalated file merged without the authors' change (an open REMERGE.json entry); the "
                    "versions above go on")
    km.add_argument("--apply-decisions", metavar="FILE", help="the answers (the issue's cascade-decisions block, or JSON) "
                    "applied at every version holding them, then --apply")
    k.add_argument("--budget-lines", type=int, default=DEFAULT_BUDGET_LINES,
                   help=f"an authors' change larger than this (changed lines) is escalated, not a default (default {DEFAULT_BUDGET_LINES})")
    k.add_argument("--out", help="where the plan files go (default build/fork-branches/plan)")
    ra = sub.add_parser("review-authors"); ra.add_argument("--repo", required=True); ra.add_argument("--range", required=True)
    ra.add_argument("--reviewer", required=True); ra.add_argument("--note", default="")
    ra.add_argument("--verdict", default="ok", choices=["ok", "not-ok"])
    n = sub.add_parser("add-target"); n.add_argument("--repo", required=True); n.add_argument("--target", required=True)
    n.add_argument("--parent", help="the version it is made from, or `port` for the port branch (a jump; default: the "
                                    "nearest lower version branch, else the port branch)")
    n.add_argument("--port-branch"); n.add_argument("--allow-undone-parent", action="store_true")
    f = sub.add_parser("freeze"); f.add_argument("--repo", required=True); f.add_argument("--target", required=True)
    f.add_argument("--upstream"); f.add_argument("--library", action="append", default=[])
    f.add_argument("--accept-upstream-drift", action="store_true")
    lr = sub.add_parser("library-record"); lr.add_argument("--repo", required=True); lr.add_argument("--target", required=True)
    lr.add_argument("--dependent", required=True); lr.add_argument("--slug")
    ss = sub.add_parser("sync-shared"); ss.add_argument("--repo", required=True)
    ss.add_argument("--target", action="append", default=[], help="a version to sync (repeat; default every version branch)")
    ci_ = sub.add_parser("ci"); ci_.add_argument("--repo", required=True); ci_.add_argument("--target", required=True)
    ci_.add_argument("--modid", required=True); ci_.add_argument("--run", action="store_true")
    a = ap.parse_args()
    if a.cmd == "add-target":
        return add_target(a.repo, a.target, a.parent, a.allow_undone_parent, a.port_branch)
    if a.cmd == "freeze":
        return freeze(a.repo, a.target, a.upstream, a.library, a.accept_upstream_drift)
    if a.cmd == "library-record":
        return library_record(a.repo, a.target, a.dependent, a.slug)
    if a.cmd == "ci":
        return ci(a.repo, a.target, a.modid, a.run)
    if a.cmd == "sync-shared":
        return sync_shared(a.repo, a.target or None)
    if a.cmd == "review-authors":
        return review_authors(a.repo, a.range, a.reviewer, a.note, a.verdict)
    if a.cmd == "cascade":
        if a.apply_decisions:
            return apply_decisions(a.repo, a.apply_decisions, a.gates, budget=a.budget_lines, out_dir=a.out)
        if not a.range:
            ap.error("cascade needs FROM..TO")
        return cascade(a.repo, a.range, a.gates, a.dry_run, mode="plan" if a.plan else "apply" if a.apply else None,
                       budget=a.budget_lines, out_dir=a.out)
    if a.cmd == "migrate":
        return migrate(a.repo, a.layer_ref, a.targets.split(",") if a.targets else None, a.dry_run, a.port_branch,
                       jpath=a.json)
    if a.cmd == "verify":
        return verify(a.repo, a.layer_ref, a.targets.split(",") if a.targets else None, a.tip)
    if a.cmd == "check-done":
        why = target_done(a.repo, a.target)
        print(f"fork-branches {a.target}: " + ("done" if not why else f"NOT done ({len(why)} open):\n" + "\n".join(f"  - {w}" for w in why)))
        return UNDONE_EXIT if why else 0
    ap.print_help()
    return 2


# ---------------------------------------------------------------- self-check


def _fixture(d):
    """A small layer fork: port branch neoforge-1.21.1 and layer branch neoforge-1.21.x with three targets."""
    d = pathlib.Path(d); r = d / "fork"; r.mkdir(parents=True)
    def g(*a, cwd=r): return subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()
    def w(p, t):
        f = r / p; f.parent.mkdir(parents=True, exist_ok=True); f.write_text(t, encoding="utf-8")
    g("init", "-q", "-b", "neoforge-1.21.1"); g("config", "user.email", "a@b"); g("config", "user.name", "a")
    w("gradle.properties", "minecraft_version=1.21.1\nneo_version=21.1.228\nmod_id=examplemod\n")
    w("build.gradle", "plugins { id 'java' }\njava.toolchain.languageVersion = JavaLanguageVersion.of(21)\n")
    w(".gitignore", "build/\nrun/\n")
    w("gradlew", "#!/bin/sh\necho gradle\n"); (r / "gradlew").chmod(0o755)
    w("src/main/java/com/example/mod/ExampleMod.java", "package com.example.mod;\nimport old.Thing;\npublic class ExampleMod { Thing t; void a() { x.oldCall(); } }\n")
    w("src/main/java/com/example/mod/Plain.java", "package com.example.mod;\npublic class Plain { int v = 1; }\n")
    w("src/main/java/com/example/mod/Over.java", "package com.example.mod;\nimport old.Thing;\npublic class Over { Thing t; int keep = 1; }\n")
    w("src/main/java/com/example/mod/mixin/AMixin.java", "package com.example.mod.mixin;\n@Mixin(Foo.class)\nclass AMixin {\n @Inject(method = \"tick\", at = @At(\"HEAD\"))\n void h() {}\n}\n")
    w("src/cf/java/com/example/mod/cf/Cf.java", "package com.example.mod.cf;\nimport old.Thing;\nclass Cf { Thing t; }\n")
    w("src/main/resources/META-INF/neoforge.mods.toml", 'modLoader="javafml"\n[[mods]]\nmodId="examplemod"\n[[dependencies.examplemod]]\nmodId="minecraft"\nversionRange="${minecraft_version_range}"\n[[dependencies.examplemod]]\nmodId="neoforge"\nversionRange="[21.1,)"\n')
    w("src/main/resources/examplemod.mixins.json", json.dumps({"package": "com.example.mod.mixin", "mixins": ["AMixin"], "client": []}, indent=2) + "\n")
    w("src/main/resources/assets/examplemod/models/item/a.json", '{"parent": "item/generated"}\n')
    w("src/main/resources/data/examplemod/recipe/r.json", '{"type": "x"}\n')
    g("add", "-A"); g("commit", "-qm", "port 1.21.1")
    g("checkout", "-q", "-b", "neoforge-1.21.x")
    py = sys.executable
    def run(*a):
        x = subprocess.run([py, *a], capture_output=True, text=True, encoding="utf-8")
        if x.returncode: raise RuntimeError(f"{a}: {x.stdout}{x.stderr}")
        return x.stdout
    run(str(ROOT / "tools/make-fork-layer.py"), "init", "--repo", str(r), "--mv-package", "com.example.mod.mv")
    mv = r / ".multiversion"
    for t in ("1.21.3", "1.21.4", "1.21.5"):
        run(str(ROOT / "tools/make-fork-layer.py"), "add-target", "--repo", str(r), "--target", t, "--allow-undone-parent")
    # rows, overlays (main and cf), a resource overlay, a mixin_add, a shared helper the code names
    w(".multiversion/targets/1.21.3/renames.tsv", "old.Thing\tnew.Thing\nmember:oldCall\tnewCall\n")
    w(".multiversion/targets/1.21.3/java/com/example/mod/Over.java", "package com.example.mod;\nimport new.Thing;\npublic class Over { Thing t; int keep = 3; }\n")
    w(".multiversion/targets/1.21.3/cf/java/com/example/mod/cf/Cf.java", "package com.example.mod.cf;\nimport new.Thing;\nclass Cf { Thing t; int c3; }\n")
    w(".multiversion/targets/1.21.3/java/com/example/mod/mixin/BMixin.java", "package com.example.mod.mixin;\nclass BMixin {}\n")
    with open(mv / "targets/1.21.3/target.properties", "a", encoding="utf-8") as f: f.write("mixin_add.examplemod.mixins.json=client:BMixin\n")
    w(".multiversion/targets/1.21.4/resources/assets/examplemod/items/a.json", '{"model": {"type": "minecraft:model", "model": "examplemod:item/a"}}\n')
    w(".multiversion/targets/1.21.4/renames.tsv", "member:newCall\tnewerCall\n")
    w(".multiversion/targets/1.21.5/java/com/example/mod/Over.java", "package com.example.mod;\nimport new.Thing;\npublic class Over { Thing t; int keep = 5; }\n")
    # an overlay-only class (the authors have none) changed at a later target, and @OnlyIn stripped at 1.21.5 from an
    # overlay it inherits: neither is a review row on the layer, so neither may be one on the branch
    w(".multiversion/targets/1.21.3/java/com/example/mod/OnlyHere.java", "package com.example.mod;\nclass OnlyHere { int v = 3; }\n")
    w(".multiversion/targets/1.21.5/java/com/example/mod/OnlyHere.java", "package com.example.mod;\nclass OnlyHere { int v = 5; }\n")
    w(".multiversion/targets/1.21.4/cf/java/com/example/mod/cf/Cf.java",
      "package com.example.mod.cf;\nimport new.Thing;\nclass Cf {\n    @OnlyIn(Dist.CLIENT)\n    Thing t;\n}\n")
    with open(mv / "targets/1.21.5/target.properties", "a", encoding="utf-8") as f: f.write("strip_onlyin=true\n")
    # 1.21.4 alone takes an authors' mixin off (mixin_remove_only): the class is absent there, present again at 1.21.5
    with open(mv / "targets/1.21.4/target.properties", "a", encoding="utf-8") as f: f.write("mixin_remove_only.examplemod.mixins.json=mixins:AMixin\n")
    run(str(ROOT / "tools/layer-inventory.py"), "build", "--repo", str(r))
    g("add", "-A"); g("commit", "-qm", "layer: three targets")
    return r, g, run

def _fixture_shared(r, g):
    def w(p, t):
        f = r / p; f.parent.mkdir(parents=True, exist_ok=True); f.write_text(t, encoding="utf-8")
    w(".multiversion/shared/1.21.3/renames.tsv", "#!exhaustive\nold.Shared\tnew.Shared\n#!strict\n")
    w(".multiversion/shared/1.21.3/java/com/example/mod/mv/Helper.java", "package com.example.mod.mv;\npublic class Helper { int v = 3; }\n")
    w(".multiversion/shared/1.21.4/java/com/example/mod/mv/Helper.java", "package com.example.mod.mv;\npublic class Helper { int v = 4; }\n")
    g("add", "-A"); g("commit", "-qm", "shared library")
    g("checkout", "-q", "neoforge-1.21.1")
    w("src/main/java/com/example/mod/UsesHelper.java", "package com.example.mod;\nimport old.Shared;\nclass UsesHelper { com.example.mod.mv.Helper h; Shared s; }\n")
    g("add", "-A"); g("commit", "-qm", "port: a class naming a helper")
    g("checkout", "-q", "neoforge-1.21.x"); g("merge", "-q", "--no-edit", "neoforge-1.21.1")


def _fixture_done(r, g, t):
    """Give layer target t complete done records at HEAD (GATES.md, REVIEW.md, TEST-ITEMS.md), committed."""
    fp_ = fp()
    tdir = r / ".multiversion/targets" / t
    head = g("rev-parse", "--short", "HEAD")
    (tdir / "GATES.md").write_text(f"Gates for `examplemod` at `{head}` for Minecraft {t} (full environment) -- run by CI:\n\n"
                                   "| check | result | detail |\n|---|---|---|\n| Gate C -- real client, `launch` | PASS | ok |\n", encoding="utf-8")
    (tdir / "TEST-ITEMS.md").write_text(f"# Manual-test items: Minecraft {t}\n\n- none\n", encoding="utf-8")
    fp_.prepared_tree(r, t, prepare=True)
    fp_.review_skeleton(r, tdir, t)
    text = (tdir / "REVIEW.md").read_text(encoding="utf-8")
    text = text.replace("- reviewer: ?", "- reviewer: fresh agent x, not the fixer").replace("- date: ?", "- date: 2026-10-16")
    text = text.replace("- commit: ?", f"- commit: {head}").replace("| ? | ? |", "| ok | read against the authors' file |")
    (tdir / "REVIEW.md").write_text(text, encoding="utf-8")
    fp_.target_done(r, t, write=True, prepare=True)
    g("add", "-A"); g("commit", "-qm", f"{t}: done records")
    return fp_.target_done(r, t, write=False, prepare=True)


def _migrated_fixture(d):
    r, g, run = _fixture(pathlib.Path(d) / "a")
    _fixture_shared(r, g)
    for t in ("1.21.3", "1.21.4", "1.21.5"):
        _quiet(_fixture_done, r, g, t)
    assert _quiet(migrate, r)[0] == 0
    return r, g, run


def _authors(r, g, edits, msg):
    """A commit on the port branch: {path: (old, new)} replacements or {path: text}."""
    g("checkout", "-q", "neoforge-1.21.1")
    for p_, e in edits.items():
        f = r / p_
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(e if isinstance(e, str) else f.read_text(encoding="utf-8").replace(*e), encoding="utf-8")
    g("add", "-A"); g("commit", "-qm", msg)
    g("checkout", "-q", "neoforge-1.21.x")


def _hand(w, edits, msg):
    for p_, e in edits.items():
        f = w / p_
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(e if isinstance(e, str) else f.read_text(encoding="utf-8").replace(*e), encoding="utf-8")
    git(w, "add", "-A", check=True); git(w, "commit", "-qm", msg, check=True)


def _check_port_taken(expect):
    """The review findings t6/t7: an authors' change no run took is offered again (port_taken), and an open entry
    spans from the earliest change it has not taken."""
    am = "src/main/java/com/example/mod/mixin/AMixin.java"
    em = "src/main/java/com/example/mod/ExampleMod.java"
    rm5 = lambda r: remerge_entries(ref_files(r, resolve_branch(r, "neoforge-1.21.5")).get(".port/1.21.5/REMERGE.json", b""))
    # t6: a second stop on the same file keeps the first change in the entry (HEAD->TAIL and tick->tock)
    with tempfile.TemporaryDirectory() as d:
        r, g, _run = _migrated_fixture(d)
        w5, _w = worktree_for(r, "1.21.5")
        (w5 / am).write_bytes((w5 / am).read_bytes().replace(b"void h() {}", b"void h() { mine(); }").replace(b"\n", b"\r\n"))
        git(w5, "commit", "-qam", "1.21.5's own AMixin (CRLF)", check=True)
        _authors(r, g, {am: ('@At("HEAD")', '@At("TAIL")')}, "authors: HEAD->TAIL")
        rc1, _o = _quiet(cascade, r, "port..1.21.5", "none")
        _authors(r, g, {am: ('method = "tick"', 'method = "tock"')}, "authors: tick->tock")
        rc2, out = _quiet(cascade, r, "port..1.21.5", "none")
        ents = [c for c in rm5(r) if c.get("overlay") == am]
        expect(rc1 == 1 and rc2 == 1 and len(ents) == 1 and '-\x20@Inject(method = "tick", at = @At("HEAD"))' in ents[0].get("diff", "")
               and '+\x20@Inject(method = "tock", at = @At("TAIL"))' in ents[0].get("diff", ""),
               f"t6: a second stop on a still-open file keeps the first change in its entry (both diffs): "
               f"{[c.get('since') for c in ents]} {out[-300:]}")
    # t7: a real conflict in the same merge (merge undone): after the hand merge the next cascade offers the change again
    with tempfile.TemporaryDirectory() as d:
        r, g, _run = _migrated_fixture(d)
        w5, _w = worktree_for(r, "1.21.5")
        _hand(w5, {am: ("void h() {}", "void h() { mine(); }"), em: ("Thing t;", "Thing t; int h5;")}, "hand 1.21.5")
        _authors(r, g, {am: ('@At("HEAD")', '@At("TAIL")'), em: ("Thing t;", "Thing t; int authors;")}, "authors")
        rc, out = _quiet(cascade, r, "port..1.21.5", "none")
        expect(rc == 1 and "STOPPED at 1.21.5" in out and em in out and not rm5(r),
               f"t7: a conflict and a carried file in one merge: the merge undone, nothing written yet: {out[-300:]}")
        git(w5, "merge", "--no-edit", "neoforge-1.21.4")
        (w5 / em).write_text("package com.example.mod;\nimport new.Thing;\npublic class ExampleMod { Thing t; int authors; int h5; "
                             "void a() { x.newCall(); } }\n", encoding="utf-8")
        git(w5, "add", "-A", check=True); git(w5, "commit", "-qm", "resolved by hand", check=True)
        rc, out = _quiet(cascade, r, "port..1.21.5", "none")
        ents = [c for c in rm5(r) if c.get("overlay") == am]
        why = target_done(r, "1.21.5")
        expect(rc == 1 and "STOPPED at 1.21.5" in out and len(ents) == 1 and '+\x20@Inject(method = "tick", at = @At("TAIL"))' in ents[0].get("diff", "")
               and any("did not merge into " + am in w for w in why),
               f"t7: after the hand merge the next cascade offers the authors' AMixin change again and records it open: "
               f"{out[-400:]} {why}")


def _check_cascade_plan(expect):
    """cascade --plan / --apply / --apply-decisions on a 4-version fixture: one conflict at two versions grouped into one
    decision, a mechanical one resolved, an optional integration absent at a version decided by default (logged), an
    escalated one open at its versions while the versions above go on, and the answers clearing it everywhere."""
    am = "src/main/java/com/example/mod/mixin/AMixin.java"
    mf = "src/main/java/com/example/mod/M.java"
    oc = "src/main/java/com/example/mod/compat/OtherCompat.java"
    bg = "src/main/java/com/example/mod/Big.java"
    with tempfile.TemporaryDirectory() as d:
        r, g, _run = _migrated_fixture(d)
        _quiet(add_target, r, "1.21.8", allow_undone_parent=True)
        w8, _w = worktree_for(r, "1.21.8")
        _hand(w8, {".port/1.21.8/renames.tsv": "# 1.21.8\nFoo\tBar\n",
                   ".port/1.21.8/DEPS.md": "# Dependencies for Minecraft 1.21.8\n\n| dependency | role | base pin | for this "
                   "target | from |\n|---|---|---|---|---|\n| `com.example:other` | optional | `1.0` | dropped: no build "
                   "for 1.21.8 | - |\n"}, "1.21.8: a row and a dropped integration")
        _authors(r, g, {mf: "package com.example.mod;\nclass M {\n Foo f;\n\n\n\n int y = 1;\n}\n",
                        oc: "package com.example.mod.compat;\nclass OtherCompat {\n boolean on() { return ModList.get()"
                            ".isLoaded(\"other\"); }\n void hook() { Other.call(); }\n}\n",
                        bg: "package com.example.mod;\nclass Big {\n int x = 1;\n}\n"}, "authors: three classes")
        # a REVIEW.md naming 1.21.4's branch commit, so one review of the authors' change can cover it (item 5)
        w4, _w = worktree_for(r, "1.21.4")
        rv = w4 / ".port/1.21.4/REVIEW.md"
        h4 = git(w4, "rev-parse", "--short", "HEAD").stdout.strip()
        rv.write_text(re.sub(r"(?m)^- commit: \S+", f"- commit: {h4}", rv.read_text(encoding="utf-8")), encoding="utf-8")
        git(w4, "commit", "-qam", "1.21.4: review at its branch commit", check=True)
        port0 = resolve_branch(r, "neoforge-1.21.1~1")
        rc, out = _quiet(cascade, r, "port..1.21.8", "none")
        expect(rc == 0, f"plan fixture: the new classes reach every version: {out[-300:]}")
        port_a = resolve_branch(r, "neoforge-1.21.1")
        why = target_done(r, "1.21.4")
        expect(any(w.startswith("REVIEW.md") and "changed since" in w for w in why),
               f"control: the authors' change merged since 1.21.4's review makes it stale: {why}")
        rc, out = _quiet(review_authors, r, f"{port0}..{port_a}", "fresh agent X, not the fixer", "the three new classes read")
        why = target_done(r, "1.21.4")
        expect(rc == 0 and not any(w.startswith("REVIEW.md") for w in why),
               f"one review of the authors' change (AUTHORS-REVIEWS.md) covers a version that took it through the rows only: {why}")
        w3, _w = worktree_for(r, "1.21.3")
        _hand(w3, {am: ("void h() {}", "void h() { three(); }")}, "1.21.3's own AMixin")
        w5, _w = worktree_for(r, "1.21.5")
        _hand(w5, {am: ("void h() {}", "void h() { five(); }"), bg: ("int x = 1;", "int x = 5;")}, "1.21.5's own AMixin and Big")
        _hand(w8, {mf: ("int y = 1;", "int y = 8;"), oc: ("void hook() { Other.call(); }", "void hook() { /* other is absent at 1.21.8 */ }")},
              "1.21.8's own M and the stubbed integration")
        _authors(r, g, {am: ("void h() {}", "void h() { authors(); }"), mf: ("Foo f;", "Foo f, g;"),
                        oc: ("void hook() { Other.call(); }", "void hook() { Other.call(); Other.more(); }"),
                        bg: ("int x = 1;\n", "int x = 2;\n" + "".join(f" int extra{i} = {i};\n" for i in range(100)))},
                 "authors: four changes")
        refs = g("for-each-ref", "--format=%(refname) %(objectname)")
        pdir = pathlib.Path(d) / "plan"
        rc, out = _quiet(cascade, r, "port..1.21.8", mode="plan", out_dir=pdir)
        plan = json.loads((pdir / "CASCADE-PLAN.json").read_text(encoding="utf-8")) if (pdir / "CASCADE-PLAN.json").is_file() else {}
        by_file = {x["file"]: x for x in plan.get("decisions", [])}
        expect(rc == 0 and g("for-each-ref", "--format=%(refname) %(objectname)") == refs and not g("status", "--porcelain"),
               f"cascade --plan changes no real branch and no checkout: {out[-300:]}")
        a_ = by_file.get(am, {})
        expect(len([x for x in plan.get("decisions", []) if x["file"] == am]) == 1
               and {"1.21.3", "1.21.5"} <= set(a_.get("versions", [])) and not a_.get("escalate"),
               f"one conflict at 1.21.3 and 1.21.5 (and carried to 1.21.8) is one decision: {a_.get('versions')}")
        expect(any(m["file"] == mf and m["version"] == "1.21.8" for m in plan.get("mechanical", [])) and mf not in by_file,
               f"a difference only 1.21.8's rows make (Foo->Bar) plus a hand edit elsewhere: merged mechanically: {plan.get('mechanical')}")
        o_ = by_file.get(oc, {})
        expect(o_.get("class") == "optional-integration" and o_.get("executed") and o_.get("versions") == ["1.21.8"],
               f"the integration absent at 1.21.8: decided by default (keep, stubbed), not open: {o_}")
        b_ = by_file.get(bg, {})
        expect(b_.get("escalate") and set(b_.get("versions", [])) == {"1.21.5", "1.21.8"} and plan.get("escalated") == 1,
               f"an authors' change over the budget: escalated, at 1.21.5 and carried to 1.21.8: {b_.get('versions')}")
        issue = (pdir / "ISSUE.md").read_text(encoding="utf-8") if (pdir / "ISSUE.md").is_file() else ""
        expect(b_.get("id", "?") in issue and a_.get("id", "?") not in issue and DECISIONS_BEGIN in issue
               and isinstance(plan.get("elapsed_s"), (int, float)),
               "the owner's issue holds only the escalated item, with a machine-readable block; the plan says its run time")
        say_time = plan.get("elapsed_s")
        # --apply: everything merged; only the files with a decision are held, per file, and travel up
        rc, out = _quiet(cascade, r, "port..1.21.8", "none", mode="apply", out_dir=pdir)
        f8 = ref_files(r, resolve_branch(r, "neoforge-1.21.8"))
        f5 = ref_files(r, resolve_branch(r, "neoforge-1.21.5"))
        open_ = {t: [c["overlay"] for c in remerge_entries(ref_files(r, resolve_branch(r, branch_name(t))).get(f".port/{t}/REMERGE.json", b""))]
                 for t in ("1.21.3", "1.21.4", "1.21.5", "1.21.8")}
        expect(rc == 0 and git(r, "merge-base", "--is-ancestor", resolve_branch(r, "neoforge-1.21.5"),
                               resolve_branch(r, "neoforge-1.21.8")).returncode == 0
               and b"Bar f, g;" in f8[mf] and b"y = 8" in f8[mf] and b"absent at 1.21.8" in f8[oc]
               and b"int x = 5;" in f5[bg] and b"five();" in f5[am],
               f"--apply: every version merged (1.21.8 above the held ones too), M merged mechanically, the integration kept: {out[-500:]}")
        expect(open_["1.21.4"] == [] and set(open_["1.21.5"]) == {am, bg} and set(open_["1.21.8"]) >= {am, bg} and am in open_["1.21.3"],
               f"open entries only where a decision is held (1.21.4 has none; 1.21.8 carries 1.21.5's): {open_}")
        expect(not any("did not merge into" in w for w in target_done(r, "1.21.4"))
               and any("did not merge into " + bg in w for w in target_done(r, "1.21.8")),
               "only the versions with an open entry are held by it")
        expect(oc in f8.get(".port/DECISIONS.md", b"").decode() and "cascade default" in f8.get(".port/DECISIONS.md", b"").decode(),
               "the default decision is logged in .port/DECISIONS.md with its reasoning")
        # the answers: the owner's (Big: our value kept, by content) and the fixer's default (AMixin: take)
        block = read_decisions(pdir / "ISSUE.md")
        for x in block["decisions"]:
            x.update(choice="content", content="package com.example.mod;\nclass Big {\n int x = 5; // ours kept, the "
                     "authors' extras not wanted here\n}\n", reason="our value is the port's; the extras are 1.21.1 only")
        block["decisions"].append({"id": a_.get("id"), "choice": "take", "by": "fixer agent (default)",
                                   "reason": "their fix through the rows; our one-line hooks are not needed any more"})
        ans = pathlib.Path(d) / "answers.json"
        ans.write_text(json.dumps(block), encoding="utf-8")
        rc, out = _quiet(apply_decisions, r, ans, "none", out_dir=pdir)
        files = {t: ref_files(r, resolve_branch(r, branch_name(t))) for t in ("1.21.3", "1.21.5", "1.21.8")}
        left_ = {t: remerge_entries(fl.get(f".port/{t}/REMERGE.json", b"")) for t, fl in files.items()}
        plan2 = json.loads((pdir / "CASCADE-PLAN.json").read_text(encoding="utf-8"))
        expect(rc == 0 and not any(left_.values()) and all(b"authors();" in fl[am] and b"three()" not in fl[am]
                                                           and b"five()" not in fl[am] for fl in files.values())
               and b"Bar.class" in files["1.21.8"][am] and b"ours kept" in files["1.21.8"][bg] and b"ours kept" in files["1.21.5"][bg]
               and plan2.get("open") == 0 and not (pdir / "ISSUE.md").exists(),
               f"--apply-decisions: each answer at every version holding it, the entries cleared, the re-plan empty: {left_} {out[-500:]}")
        log8 = files["1.21.8"].get(".port/DECISIONS.md", b"").decode()
        expect(b_.get("id", "?") in log8 and a_.get("id", "?") in log8 and "owner" in log8,
               f"the answers are logged in .port/DECISIONS.md, carried up with the merge: {log8[-400:]}")
        expect(isinstance(say_time, (int, float)) and say_time < 120, f"the plan is cheap: {say_time} s")
        print(f"cascade --plan on the 4-version fixture: {say_time} s")
    # a single-version fork: the plan refuses, nothing written
    with tempfile.TemporaryDirectory() as d:
        sv = pathlib.Path(d)
        gs = lambda *a: subprocess.run(["git", "-C", str(sv), *a], capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()
        gs("init", "-q", "-b", "neoforge-1.21.1"); gs("config", "user.email", "a@b"); gs("config", "user.name", "a")
        (sv / "gradle.properties").write_text("minecraft_version=1.21.1\n", encoding="utf-8")
        gs("add", "-A"); gs("commit", "-qm", "port")
        refs = gs("for-each-ref")
        rc, out = _quiet(cascade, sv, "port..1.21.4", mode="plan")
        expect(rc == 2 and gs("for-each-ref") == refs and not (sv / "build").exists() and not gs("status", "--porcelain"),
               f"a single-version fork: cascade --plan refuses, nothing written: {out}")


def _quiet(fn, *a, **k):
    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = fn(*a, **k)
    return rc, buf.getvalue()


def self_check():
    ok = True

    def expect(c, msg):
        nonlocal ok
        if not c:
            ok = False
            print("FAIL:", msg)
    # a mixin config with a byte-order mark (and CRLF) is read, and written back with both
    bom = "\ufeff{\r\n  \"package\": \"p\",\r\n  \"mixins\": [\"A\"]\r\n}\r\n"
    got = json_like(mixin_edit(jload(bom.encode("utf-8")), [("mixins", "B")], []), bom)
    expect(got.startswith("\ufeff{\r\n") and got.endswith("}\r\n") and jload(got)["mixins"] == ["A", "B"],
           f"a BOM'd CRLF mixin config: read, edited, written in its own form: {got!r}")
    # line endings: a CRLF gradle.properties stays CRLF, and rows applied to a CRLF source keep CRLF (the moves stage)
    expect(set_props_text("a=1\r\nminecraft_version=1.21.1\r\n", {"minecraft_version": "1.21.4"})
           == "a=1\r\nminecraft_version=1.21.4\r\n", "set_props_text keeps a CRLF file CRLF")
    with tempfile.TemporaryDirectory() as e:
        f = pathlib.Path(e) / "A.java"
        f.write_bytes(b"import old.Thing;\r\nclass A { Thing t; String s = \"\xe9\"; }\r\n")
        n = apply_rules_to_files([f], rules_of("old.Thing\tnew.Thing\n"))
        expect(n == 1 and f.read_bytes() == b"import new.Thing;\r\nclass A { Thing t; String s = \"\xe9\"; }\r\n",
               f"rows applied as bytes keep CRLF and a non-UTF-8 byte: {f.read_bytes()!r}")
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        r, g, _run = _fixture(d / "a")
        _fixture_shared(r, g)
        for t in ("1.21.3", "1.21.4", "1.21.5"):
            why = _quiet(_fixture_done, r, g, t)[0]
            expect(not why, f"fixture: layer target {t} done: {why}")
        # layer mode, a fresh clone (no build/): the done check's re-prepare makes every source set's tree, the cf set's
        # too; without it the inventory refuses rather than read the raw src/cf/java (every cf overlay item "same")
        shutil.rmtree(r / "build/mc1.21.5")
        expect(fp().prepared_tree(r, "1.21.5", prepare=True) == [] and (r / "build/mc1.21.5/mv/cf-java").is_dir()
               and b"@OnlyIn" not in (r / "build/mc1.21.5/mv/cf-java/com/example/mod/cf/Cf.java").read_bytes(),
               "layer re-prepare for the done check: main and the cf source set (its overlay, @OnlyIn stripped)")
        shutil.rmtree(r / "build/mc1.21.5/mv/cf-java")
        miss = fp().prepared_tree(r, "1.21.5", prepare=False)
        li_ = _mod("layer_inventory_fb", "layer-inventory.py")
        try:
            li_.account(r, "1.21.5"); raised = ""
        except ValueError as e:
            raised = str(e)
        expect(any("mv/cf-java" in m for m in miss) and "mv/cf-java" in raised,
               f"a missing prepared cf set: the done check and the inventory refuse to judge it: {miss} {raised!r}")
        fp().prepared_tree(r, "1.21.5", prepare=True)
        refs0 = g("for-each-ref", "--format=%(refname) %(objectname)")
        head0 = g("rev-parse", "HEAD")
        rc, out = _quiet(migrate, r, dry_run=True)
        expect(rc == 0 and "would make neoforge-1.21.4" in out and g("for-each-ref", "--format=%(refname) %(objectname)") == refs0,
               f"migrate --dry-run prints the plan and writes nothing: {out[-400:]}")
        rc, out = _quiet(migrate, r)
        expect(rc == 0 and out.count("byte-identical to the layer's prepare") == 3 and out.count("layer done, branch done") == 3,
               f"migrate: three byte-identical branches, each done as its layer target was: {out[-900:]}")
        expect(g("rev-parse", "HEAD") == head0 and not g("status", "--porcelain"),
               "migrate never touches the checkout (HEAD and the work tree are as they were)")
        expect(all(resolve_branch(r, branch_name(t)) for t in ("1.21.3", "1.21.4", "1.21.5")), "the three branches exist")
        b4, b5 = resolve_branch(r, "neoforge-1.21.4"), resolve_branch(r, "neoforge-1.21.5")
        expect(git(r, "merge-base", "--is-ancestor", b4, b5).returncode == 0
               and git(r, "merge-base", "--is-ancestor", resolve_branch(r, "neoforge-1.21.1"), resolve_branch(r, "neoforge-1.21.3")).returncode == 0,
               "stacked: each branch is made from the one below it, the first from the port branch")
        f5 = ref_files(r, b5)
        expect(not any(p.startswith(".multiversion/") for p in f5) and BRANCH_HOOK_LINE in f5["build.gradle"].decode()
               and LAYER_HOOK_LINE not in f5["build.gradle"].decode() and b"minecraft_version=1.21.5" in f5["gradle.properties"],
               "a version branch carries no .multiversion/, the branch hook instead of the layer's, and its own versions")
        expect(b"keep = 5" in f5["src/main/java/com/example/mod/Over.java"] and b"new.Thing" in f5["src/main/java/com/example/mod/ExampleMod.java"]
               and b"@OnlyIn" not in f5["src/cf/java/com/example/mod/cf/Cf.java"] and b"Thing t;" in f5["src/cf/java/com/example/mod/cf/Cf.java"] and b'"[1.21.5,1.21.6)"' in f5["src/main/resources/META-INF/neoforge.mods.toml"]
               and b"BMixin" in f5["src/main/resources/examplemod.mixins.json"] and b"v = 4" in f5["src/main/java/com/example/mod/mv/Helper.java"]
               and "src/main/resources/assets/examplemod/items/a.json" in f5,
               "1.21.5's tree: its own overlay, the rows, the cf set's inherited overlay, the toml range, the inherited mixin, the nearest helper, the inherited resource")
        expect(all(p in f5 for p in (".port/1.21.5/GATES.md", ".port/1.21.5/REVIEW.md", ".port/1.21.5/MIGRATED.json",
                                    ".port/1.21.5/INVENTORY-CHECK.json", ".port/1.21.5/TEST-ITEMS.md", ".port/1.21.4/GATES.md")),
               "records under .port/<version>/ (the lower versions' as carried copies)")
        with tempfile.TemporaryDirectory() as e:
            items = review_items(r, export(r, b5, pathlib.Path(e) / "t"), "1.21.5", b5, False)
        expect(("changed", "src/main/java/com/example/mod/Over.java") in items
               and not any("OnlyHere" in f or "Cf.java" in f for _k, f in items),
               f"1.21.5's review rows: its own overlay of an authors' file; not an overlay-only class, not the parent's file "
               f"with @OnlyIn stripped: {items}")
        rc, out = _quiet(verify, r)
        expect(rc == 0 and out.count("byte-identical") == 3, f"verify: the layer's prepare run again equals every branch: {out[-300:]}")
        rc, out = _quiet(migrate, r)
        expect(rc == 0 and out.count("exists with the same content") == 3, f"migrate again: nothing to do: {out[-300:]}")
        # the digest mapping is what keeps a moved target done: without it, its layer-commit records do not count
        with tempfile.TemporaryDirectory() as e:
            root = export(r, b5, pathlib.Path(e) / "t")
            (root / REC / "1.21.5/MIGRATED.json").unlink()
            why = target_done(r, "1.21.5", root=root, ref=b5)
            expect(any("GATES.md names" in w and "MIGRATED.json does not map it" in w for w in why)
                   and any(w.startswith("REVIEW.md names") for w in why),
                   f"control: without MIGRATED.json the layer commits in GATES.md/REVIEW.md do not count: {why}")
        # a change after the migration makes the gates and the review stale; a mechanical file edited becomes a review row
        g("checkout", "-q", "neoforge-1.21.4")
        expect(checkout_version(r) == "1.21.4" and target_done(r, "1.21.4") == [], "checked out: done in place")
        # a mode change alone (an executable bit) stales the migrated records too
        if os.name != "nt":
            pm = r / "src/main/java/com/example/mod/Plain.java"
            pm.chmod(0o755)
            why = target_done(r, "1.21.4")
            expect(any(w.startswith("GATES.md") and "no longer equals" in w for w in why),
                   f"a file made executable: the migrated Gate C record no longer matches: {why}")
            pm.chmod(0o644)
        # the library rule: a library version is done only once a dependent passed Gate C against its jar
        bp = r / REC / "1.21.4" / BRANCH_PROPS
        bp.write_text(read_file(bp) + "library=true\n", encoding="utf-8")
        why = target_done(r, "1.21.4")
        expect(len(why) == 1 and why[0].startswith(LIBRARY_PENDING), f"library=true and no dependent recorded: not done: {why}")
        (r / REC / "1.21.4" / "DEPENDENTS.json").write_text(json.dumps([{"dependent": "owner/dep", "gate_c": "PASS",
                                                                         "library_commit": g("rev-parse", "HEAD")}]), encoding="utf-8")
        expect(target_done(r, "1.21.4") == [], "a dependent recorded green against this jar's commit: done")
        (r / REC / "1.21.4" / "DEPENDENTS.json").write_text(json.dumps([{"dependent": "owner/dep", "gate_c": "PASS",
                                                                         "library_commit": "0" * 40}]), encoding="utf-8")
        expect(target_done(r, "1.21.4")[:1] and target_done(r, "1.21.4")[0].startswith(LIBRARY_PENDING),
               "control: a dependent recorded against another commit does not count")
        g("checkout", "-q", "--", "."); (r / REC / "1.21.4" / "DEPENDENTS.json").unlink()
        (r / "src/main/java/com/example/mod/ExampleMod.java").write_text("package com.example.mod;\nclass ExampleMod { int edited; }\n",
                                                                       encoding="utf-8")
        why = target_done(r, "1.21.4")
        expect(any(w.startswith("GATES.md") and ("changed since" in w or "no longer equals" in w) for w in why)
               and "no verdict: changed src/main/java/com/example/mod/ExampleMod.java" in why,
               f"an edited file: Gate C stale, and the file (no longer the rows' alone) needs a review row: {why}")
        # a file the parent has and this version deletes is a review row; one drops.txt or a mixin_remove key accounts
        # for needs none (the migrated 1.21.4 lacks AMixin by its mixin_remove key and is done, above)
        (r / "src/main/resources/data/examplemod/recipe/r.json").unlink()
        why = target_done(r, "1.21.4")
        expect("no verdict: deleted src/main/resources/data/examplemod/recipe/r.json" in why
               and not any("AMixin" in w for w in why), f"a deleted recipe needs a review row; AMixin (mixin_remove) none: {why}")
        g("checkout", "-q", "--", "."); g("checkout", "-q", "neoforge-1.21.x")
        # cascade: the authors' change arrives on the port branch; each version's mechanical files are re-made from it
        # through that version's rows (a conflict git alone would have on the renamed line resolved), a new file too
        g("checkout", "-q", "neoforge-1.21.1")
        (r / "src/main/java/com/example/mod/ExampleMod.java").write_text(
            "package com.example.mod;\nimport old.Thing;\npublic class ExampleMod { Thing t; Thing u; void a() { x.oldCall(); } }\n",
            encoding="utf-8")
        (r / "src/main/java/com/example/mod/NewOne.java").write_text("package com.example.mod;\nimport old.Thing;\nclass NewOne { Thing t; }\n",
                                                                    encoding="utf-8")
        amixin = "src/main/java/com/example/mod/mixin/AMixin.java"
        (r / amixin).write_text((r / amixin).read_text(encoding="utf-8").replace("void h() {}", "void h() { fixed(); }"), encoding="utf-8")
        g("add", "-A"); g("commit", "-qm", "authors: a field and a class")
        g("checkout", "-q", "neoforge-1.21.x")
        rc, out = _quiet(cascade, r, "neoforge-1.21.1..1.21.5", "none")
        e5 = ref_files(r, resolve_branch(r, "neoforge-1.21.5"))
        expect(rc == 0 and out.count("| merged |") == 3
               and e5["src/main/java/com/example/mod/ExampleMod.java"] == b"package com.example.mod;\nimport new.Thing;\npublic class ExampleMod { Thing t; Thing u; void a() { x.newCall(); } }\n"
               and b"import new.Thing;" in e5["src/main/java/com/example/mod/NewOne.java"] and b"keep = 5" in e5["src/main/java/com/example/mod/Over.java"],
               f"cascade port..1.21.5: the authors' change reaches every version through its rows, overlays kept: {out[-500:]}")
        expect(b"fixed();" in e5[amixin] and amixin not in ref_files(r, resolve_branch(r, "neoforge-1.21.4"), "src"),
               "the authors' fix to a class 1.21.4 takes off (mixin_remove_only) still reaches 1.21.5, which has it")
        why = target_done(r, "1.21.5")
        expect(any(w.startswith("GATES.md") for w in why) and not any(w.startswith("no verdict") for w in why),
               f"after the merge the gates are stale, and no rows-only file needs a review row: {why}")
        # a fix made at the lowest version reaches the higher ones by merge
        w3, _why = worktree_for(r, "1.21.3")
        (w3 / "src/main/java/com/example/mod/Plain.java").write_text("package com.example.mod;\npublic class Plain { int v = 2; }\n",
                                                                     encoding="utf-8")
        git(w3, "commit", "-qam", "fix at 1.21.3", check=True)
        rc, out = _quiet(cascade, r, "1.21.3..1.21.5", "none")
        expect(rc == 0 and all(b"v = 2" in ref_files(r, resolve_branch(r, branch_name(t)), "src")["src/main/java/com/example/mod/Plain.java"]
                               for t in ("1.21.4", "1.21.5")), f"cascade 1.21.3..1.21.5 carries a fix up: {out[-400:]}")
        why = target_done(r, "1.21.4")
        expect("no verdict: changed src/main/java/com/example/mod/Plain.java" not in why,
               f"the fix is 1.21.3's change: on 1.21.4 the file equals its parent's, so no row there: {why}")
        why3 = target_done(r, "1.21.3")
        expect("no verdict: changed src/main/java/com/example/mod/Plain.java" in why3, f"... and a row on 1.21.3, where it was made: {why3}")
        # ... but where 1.21.5 changed that class by hand, the authors' change cannot arrive through 1.21.4: the merge is
        # committed with 1.21.5's file as it was, the authors' diff recorded open in REMERGE.json and printed, and a stop
        w5, _why = worktree_for(r, "1.21.5")
        pre_edit5 = resolve_branch(r, "neoforge-1.21.5")
        (w5 / amixin).write_text((w5 / amixin).read_text(encoding="utf-8").replace("fixed();", "fixed(); mine();"), encoding="utf-8")
        git(w5, "commit", "-qam", "1.21.5's own AMixin edit", check=True)
        g("checkout", "-q", "neoforge-1.21.1")
        (r / amixin).write_text((r / amixin).read_text(encoding="utf-8").replace("fixed();", "fixed(); again();"), encoding="utf-8")
        g("commit", "-qam", "authors: AMixin again")
        g("checkout", "-q", "neoforge-1.21.x")
        before5 = resolve_branch(r, "neoforge-1.21.5")
        rc, out = _quiet(cascade, r, "port..1.21.5", "none")
        f5 = ref_files(r, resolve_branch(r, "neoforge-1.21.5"))
        rm5 = jload(f5.get(".port/1.21.5/REMERGE.json", b"{}")).get("conflicts", [])
        expect(rc == 1 and "STOPPED at 1.21.5" in out and amixin in out and "+" in out and "again();" in out
               and resolve_branch(r, "neoforge-1.21.5") != before5 and b"mine();" in f5[amixin] and b"again();" not in f5[amixin]
               and [c["overlay"] for c in rm5] == [amixin] and "again();" in rm5[0].get("diff", ""),
               f"the authors' change to a class the parent does not carry and 1.21.5 changed by hand: merged without it, "
               f"recorded open in REMERGE.json with the diff, printed, a stop: {out[-700:]}")
        why = target_done(r, "1.21.5")
        expect(any("did not merge into " + amixin in w for w in why), f"... and 1.21.5 is not done while it is open: {why}")
        rc, out = _quiet(cascade, r, "port..1.21.5", "none")
        expect(rc == 0 and "| 1.21.5 | up to date" in out and any("did not merge into " + amixin in w for w in target_done(r, "1.21.5")),
               f"a later cascade has nothing more to merge, and the entry stays open (the change is not lost): {out[-300:]}")
        # the person applies it through 1.21.5's rows, removes the entry, commits: the cascade goes on, nothing open
        (w5 / amixin).write_text((w5 / amixin).read_text(encoding="utf-8").replace("fixed(); mine();", "fixed(); again(); mine();"),
                                 encoding="utf-8")
        (w5 / ".port/1.21.5/REMERGE.json").write_text(json.dumps({"conflicts": []}) + "\n", encoding="utf-8")
        git(w5, "commit", "-qam", "1.21.5: the authors' AMixin change applied by hand", check=True)
        why = target_done(r, "1.21.5")
        rc, out = _quiet(cascade, r, "1.21.4..1.21.5", "none")
        expect(not any("did not merge into" in w for w in why) and rc == 0 and "| 1.21.5 | up to date" in out,
               f"applied and the entry removed: nothing open, the cascade resumes: {why} {out[-200:]}")
        git(w5, "reset", "-q", "--hard", pre_edit5)
        before5 = resolve_branch(r, "neoforge-1.21.5")
        # a conflict in a file a person changed stops the cascade there, the merge undone
        g("checkout", "-q", "neoforge-1.21.1")
        (r / "src/main/java/com/example/mod/Over.java").write_text("package com.example.mod;\nimport old.Thing;\npublic class Over { Thing t; int keep = 9; }\n",
                                                                   encoding="utf-8")
        g("commit", "-qam", "authors: Over changed")
        g("checkout", "-q", "neoforge-1.21.x")
        before = resolve_branch(r, "neoforge-1.21.3")
        rc, out = _quiet(cascade, r, "port..1.21.5", "none")
        expect(rc == 1 and "STOPPED at 1.21.3" in out and "src/main/java/com/example/mod/Over.java" in out
               and resolve_branch(r, "neoforge-1.21.3") == before and not git(w3, "status", "--porcelain").stdout.strip(),
               f"a conflict on a version's own change stops the cascade, nothing committed, the worktree clean: {out[-500:]}")
        rc, out = _quiet(cascade, r, "1.21.4..1.21.5", "none", dry_run=True)
        expect(rc == 0 and "| 1.21.5 | would merge neoforge-1.21.4" in out and resolve_branch(r, "neoforge-1.21.5") == before5,
               f"cascade --dry-run (1.21.5 still lacks what stopped it): rc {rc}: {out[-300:]!r}")
        for t in ("1.21.3", "1.21.4", "1.21.5"):
            git(r, "worktree", "remove", "--force", str(r / "build/fork-branches" / t))
        # a new version branch: refused over an undone parent (nothing written), made with the escape (recorded)
        refs = g("for-each-ref")
        rc, out = _quiet(add_target, r, "1.21.8")
        expect(rc == UNDONE_EXIT and "REFUSED 1.21.8" in out and g("for-each-ref") == refs,
               f"add-target over an undone parent: exit 18, nothing written: {out[-300:]}")
        rc, out = _quiet(add_target, r, "1.21.8", allow_undone_parent=True)
        f8 = ref_files(r, resolve_branch(r, "neoforge-1.21.8")) if rc == 0 else {}
        p8 = parse_props(f8.get(".port/1.21.8/branch.properties", b"").decode())
        expect(rc == 0 and p8.get("parent") == "neoforge-1.21.5" and p8.get("prop.neo_version") == "21.8.54"
               and p8.get("strip_onlyin") == "true" and p8.get("allowed_undone_parent", "").startswith("1.21.5 ")
               and b"minecraft_version=1.21.8" in f8["gradle.properties"] and b'"[1.21.8,1.21.9)"' in f8["src/main/resources/META-INF/neoforge.mods.toml"]
               and b"# ---- shared 1.21.8" in f8[".port/1.21.8/shared-renames.tsv"] and b"keep = 5" in f8["src/main/java/com/example/mod/Over.java"],
               f"add-target 1.21.8 from 1.21.5: versions, ranges, the shared 1.21.8 table, the parent's own changes kept: {out[-300:]}")
        # an ISO-8859-1 gradle.properties (a Latin-1 comment byte) on the port branch: add-target keeps the byte
        lw = r / "build/latin"
        git(r, "worktree", "add", "-q", "-b", "latin-port", str(lw), "neoforge-1.21.1", check=True)
        gpl = lw / "gradle.properties"
        gpl.write_bytes(b"# caf\xe9\n" + gpl.read_bytes())
        git(lw, "commit", "-qam", "latin-1 comment", check=True)
        try:
            rc, out = _quiet(add_target, r, "1.21.10", parent="port", port_branch="latin-port", allow_undone_parent=True)
        except UnicodeError as ex:
            rc, out = 99, repr(ex)
        fl = ref_files(r, resolve_branch(r, "neoforge-1.21.10")) if rc == 0 else {}
        expect(rc == 0 and fl.get("gradle.properties", b"").startswith(b"# caf\xe9\n")
               and b"minecraft_version=1.21.10" in fl.get("gradle.properties", b""),
               f"add-target over an ISO-8859-1 gradle.properties: the byte kept, the versions written: rc {rc}: {out[-300:]}")
        git(r, "worktree", "remove", "--force", str(lw))
        g("branch", "-D", "latin-port", "neoforge-1.21.10")
        # sync-shared: the shared library moved (a row and a helper): 1.21.3 takes both, its rows-only files re-made
        with tempfile.TemporaryDirectory() as e:
            lib = pathlib.Path(e)
            (lib / "1.21.3/java").mkdir(parents=True)
            (lib / "1.21.3/renames.tsv").write_text("#!exhaustive\nold.Shared\tnewer.Shared\n#!strict\n", encoding="utf-8")
            (lib / "1.21.3/java/Helper.java").write_text("package @MVPKG@;\npublic class Helper { int v = 33; }\n", encoding="utf-8")
            saved = os.environ.get("MMVU_SHARED_DIR")
            os.environ["MMVU_SHARED_DIR"] = str(lib)
            try:
                rc, out = _quiet(sync_shared, r, ["1.21.3"])
            finally:
                os.environ.pop("MMVU_SHARED_DIR") if saved is None else os.environ.__setitem__("MMVU_SHARED_DIR", saved)
            f3 = ref_files(r, resolve_branch(r, "neoforge-1.21.3"))
            expect(rc == 0 and b"newer.Shared" in f3["src/main/java/com/example/mod/UsesHelper.java"]
                   and b"v = 33" in f3["src/main/java/com/example/mod/mv/Helper.java"]
                   and b"newer.Shared" in f3[".port/1.21.3/shared-renames.tsv"]
                   and helper_map(f3[".port/1.21.3/shared-helpers.txt"].decode()).get("src/main/java/com/example/mod/mv/Helper.java")
                   == hash_blobs({"x": f3["src/main/java/com/example/mod/mv/Helper.java"]})["x"],
                   f"sync-shared 1.21.3: the new row re-makes the rows-only file, the installed helper follows the library: {out[-400:]}")
            why = target_done(r, "1.21.3")
            expect(any(w.startswith("REVIEW.md") and ("changed since" in w or "reviewed input" in w or "no longer equals" in w) for w in why),
                   f"the synced tables are reviewed inputs: the review goes stale: {why}")
            expect("WARNING: 1.21.4, 1.21.5" in out and " not synced" in out, f"a partial sync-shared names the versions above it: {out[-400:]}")
            # a version with helpers and no table (1.21.4) is synced too; an edited helper is named, never overwritten
            (lib / "1.21.4/java").mkdir(parents=True)
            (lib / "1.21.4/java/Helper.java").write_text("package @MVPKG@;\npublic class Helper { int v = 44; }\n", encoding="utf-8")
            w5s, _w = worktree_for(r, "1.21.5")
            pre5s = resolve_branch(r, "neoforge-1.21.5")
            hp = w5s / "src/main/java/com/example/mod/mv/Helper.java"
            hp.write_text(hp.read_text(encoding="utf-8").replace("int v", "int edited, v"), encoding="utf-8")
            git(w5s, "commit", "-qam", "1.21.5 edits its helper", check=True)
            os.environ["MMVU_SHARED_DIR"] = str(lib)
            try:
                rc, out = _quiet(sync_shared, r)
            finally:
                os.environ.pop("MMVU_SHARED_DIR") if saved is None else os.environ.__setitem__("MMVU_SHARED_DIR", saved)
            f4 = ref_files(r, resolve_branch(r, "neoforge-1.21.4"))
            f5s = ref_files(r, resolve_branch(r, "neoforge-1.21.5"))
            expect(rc == 0 and "WARNING" not in out and b"v = 44" in f4["src/main/java/com/example/mod/mv/Helper.java"]
                   and b"int edited, v = 4;" in f5s["src/main/java/com/example/mod/mv/Helper.java"]
                   and "sync-shared 1.21.5: src/main/java/com/example/mod/mv/Helper.java was edited since it was installed" in out,
                   f"sync-shared (every version): 1.21.4's helper-only library synced, 1.21.5's edited helper named and kept: {out[-600:]}")
            git(w5s, "reset", "-q", "--hard", pre5s)
            for v in ("1.21.4", "1.21.5", "1.21.8"):
                if (r / "build/fork-branches" / v).is_dir():
                    git(r, "worktree", "remove", "--force", str(r / "build/fork-branches" / v))
        git(r, "worktree", "remove", "--force", str(r / "build/fork-branches/1.21.3"))
        # a branch that exists with other content is refused, nothing moved
        g("branch", "-f", "neoforge-1.21.5", "neoforge-1.21.1")
        before = g("rev-parse", "neoforge-1.21.5")
        rc, out = _quiet(migrate, r, targets=["1.21.5"])
        expect(rc == 2 and "REFUSED 1.21.5" in out and g("rev-parse", "neoforge-1.21.5") == before,
               f"an existing branch with other content: refused, never moved: {out[-300:]}")
        # a single-version fork: nothing to migrate, nothing written
        sv = d / "single"; sv.mkdir()
        gs = lambda *a: subprocess.run(["git", "-C", str(sv), *a], capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()
        gs("init", "-q", "-b", "neoforge-1.21.1"); gs("config", "user.email", "a@b"); gs("config", "user.name", "a")
        (sv / "gradle.properties").write_text("minecraft_version=1.21.1\n", encoding="utf-8")
        gs("add", "-A"); gs("commit", "-qm", "port")
        refs = gs("for-each-ref")
        rc, out = _quiet(migrate, sv)
        expect(rc == 2 and "nothing to migrate" in out and gs("for-each-ref") == refs and not (sv / REC).exists()
               and checkout_version(sv) is None,
               f"single-version fork: migrate refuses (exit 2), no branch, no .port/: {out}")
    # the port branch moved past the layer (the authors' new code not merged into it yet): each migrated version is
    # judged against the port commit IT carries, never the clone's HEAD -- done from a checkout of the port branch too
    with tempfile.TemporaryDirectory() as d:
        r, g, _run = _fixture(pathlib.Path(d) / "a")
        _fixture_shared(r, g)
        _run(str(ROOT / "tools/layer-inventory.py"), "build", "--repo", str(r))     # current for the layer's port commit
        g("add", "-A"); g("commit", "-qm", "layer: inventory current")
        for t in ("1.21.3", "1.21.4", "1.21.5"):
            _quiet(_fixture_done, r, g, t)
        g("checkout", "-q", "neoforge-1.21.1")
        (r / "src/main/java/com/example/mod/Later.java").write_text("package com.example.mod;\nclass Later {}\n", encoding="utf-8")
        uh = r / "src/main/java/com/example/mod/UsesHelper.java"
        uh.write_text(uh.read_text(encoding="utf-8").replace("Shared s;", "Shared s; int later;"), encoding="utf-8")
        g("add", "-A"); g("commit", "-qm", "authors: a newer commit on the port branch")
        rc, out = _quiet(migrate, r, layer_ref="neoforge-1.21.x")       # run from the port branch's checkout
        expect(rc == 0 and out.count("layer done, branch done") == 3,
               f"migrate (from a checkout of the port branch, ahead of the layer): each branch done as on the layer: {out[-600:]}")
        for t in ("1.21.3", "1.21.4", "1.21.5"):
            why = target_done(r, t)
            expect(why == [], f"{t} judged from a checkout of the port branch (ahead of it): done: {why}")
        # sync-shared from that checkout: 1.21.3's rows-only file is judged against the port commit 1.21.3 carries
        with tempfile.TemporaryDirectory() as e:
            lib = pathlib.Path(e)
            (lib / "1.21.3").mkdir(parents=True)
            (lib / "1.21.3/renames.tsv").write_text("#!exhaustive\nold.Shared\tnewer.Shared\n#!strict\n", encoding="utf-8")
            saved = os.environ.get("MMVU_SHARED_DIR")
            os.environ["MMVU_SHARED_DIR"] = str(lib)
            try:
                rc, out = _quiet(sync_shared, r, ["1.21.3"])
            finally:
                os.environ.pop("MMVU_SHARED_DIR") if saved is None else os.environ.__setitem__("MMVU_SHARED_DIR", saved)
            u3 = ref_files(r, resolve_branch(r, "neoforge-1.21.3"))["src/main/java/com/example/mod/UsesHelper.java"]
            expect(rc == 0 and b"newer.Shared" in u3 and b"later" not in u3,
                   f"sync-shared from a checkout of the port branch ahead: the rows-only file re-made from 1.21.3's own port commit: {u3!r} {out[-300:]}")
        # a resource 1.21.4 deletes (kept dropped there when the authors change it) and 1.21.5 keeps still takes the
        # authors' later change (no merge carries it)
        rj = "src/main/resources/data/examplemod/recipe/r.json"
        w4, _w = worktree_for(r, "1.21.4")
        git(w4, "rm", "-q", rj, check=True); git(w4, "commit", "-qm", "1.21.4 drops the recipe", check=True)
        rc, out = _quiet(cascade, r, "1.21.4..1.21.5", "none")
        w5, _w = worktree_for(r, "1.21.5")
        git(w5, "checkout", "neoforge-1.21.1~1", "--", rj, check=True)
        git(w5, "commit", "-qm", "1.21.5 keeps the recipe", check=True)
        (r / rj).write_text('{"type": "authors-new"}\n', encoding="utf-8")
        g("commit", "-qam", "authors: the recipe changed")
        rc, out = _quiet(cascade, r, "port..1.21.5", "none")
        f5 = ref_files(r, resolve_branch(r, "neoforge-1.21.5"))
        expect(rc == 0 and f5.get(rj) == b'{"type": "authors-new"}\n' and rj not in ref_files(r, resolve_branch(r, "neoforge-1.21.4")),
               f"a resource the parent dropped and 1.21.5 keeps: the authors' change reaches 1.21.5: {f5.get(rj)!r} {out[-400:]}")
    _check_port_taken(expect)
    _check_cascade_plan(expect)
    if not os.environ.get("FB_SELF_CHECK_CRLF"):
        # the same checks with core.autocrlf=true (a Windows checkout's default): a version branch checked out with CRLF
        # line endings must read as its blobs (root_files), and a layer's prepare must see the blobs' bytes (Layer)
        with tempfile.TemporaryDirectory() as d:
            cfg = pathlib.Path(d) / "gitconfig"
            cfg.write_text("[core]\n\tautocrlf = true\n[user]\n\tname = a\n\temail = a@b\n", encoding="utf-8")
            r = subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve()), "--self-check"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               env={**os.environ, "GIT_CONFIG_GLOBAL": str(cfg), "FB_SELF_CHECK_CRLF": "1"})
            expect("self-check: OK" in r.stdout, "under core.autocrlf=true: " + " | ".join(
                l for l in r.stdout.splitlines() if l.startswith("FAIL"))[:1500])
    print("self-check:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
