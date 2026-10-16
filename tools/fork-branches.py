#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Stacked version branches: a multi-version fork as one real branch per Minecraft version (the default).

    python3 tools/fork-branches.py migrate     --repo DIR [--layer-ref REF] [--port-branch B] [--targets T,T] [--dry-run]
                                               [--json FILE]
    python3 tools/fork-branches.py verify      --repo DIR [--layer-ref REF] [--targets T,T] [--tip]
    python3 tools/fork-branches.py cascade     --repo DIR FROM..TO [--gates none|compile|ab|full] [--dry-run]
    python3 tools/fork-branches.py add-target  --repo DIR --target T [--parent V] [--allow-undone-parent]
    python3 tools/fork-branches.py check-done  --repo DIR --target T
    python3 tools/fork-branches.py freeze      --repo DIR --target T [--upstream REMOTE/BRANCH] [--library owner/repo@TAG]
                                               [--accept-upstream-drift]
    python3 tools/fork-branches.py library-record --repo LIB --target T --dependent DIR [--slug owner/repo]
    python3 tools/fork-branches.py ci          --repo DIR --target T --modid ID [--run]
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
import argparse, datetime, hashlib, importlib.util, json, os, pathlib, re, shutil, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
TPL = ROOT / "templates/fork-branches/branch.gradle"
PREPARE_SOURCES = ROOT / "templates/fork-layer/tools/prepare-sources.py"
SHARED = ROOT / "templates/fork-layer/shared"
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


def read_text(p):
    p = pathlib.Path(p)
    return p.read_text(encoding="utf-8", errors="surrogateescape") if p.is_file() else ""


def set_props_text(text, updates):
    """`text` (a .properties file) with each key of updates set, every other line kept as it is; new keys appended."""
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
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- what a checkout / tree is

def rec_dir(root, t):
    return pathlib.Path(root) / REC / t


def checkout_version(root):
    """The Minecraft version of a version branch checked out (or exported) at `root`: gradle.properties'
    minecraft_version when .port/<it>/branch.properties exists there; else None (the port branch, a single-version fork,
    a layer branch)."""
    mc = parse_props(read_text(pathlib.Path(root) / "gradle.properties")).get("minecraft_version")
    return mc if mc and (rec_dir(root, mc) / BRANCH_PROPS).is_file() else None


def info(root, t):
    return parse_props(read_text(rec_dir(root, t) / BRANCH_PROPS))


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
    """{path: bytes} of the tree at `root`: a work tree of `repo` (work=True: tracked and untracked-not-ignored files),
    or an exported directory (every file)."""
    root = pathlib.Path(root)
    if work:
        r = git(root, "ls-files", "-z", "-c", "-o", "--exclude-standard", check=True)
        paths = [p for p in r.stdout.split("\0") if p]
    else:
        paths = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and ".git" not in p.relative_to(root).parts]
    out = {}
    for p in sorted(set(paths)):
        f = root / p
        if f.is_file():
            out[p] = f.read_bytes()
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
    return lambda p: read_text(root / p)


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


def json_like(obj, like):
    """`obj` as JSON in the indentation of `like` (the file it replaces)."""
    m = re.search(r"\n([ \t]+)\S", like or "")
    ind = m.group(1) if m else "  "
    ind = len(ind) if " " in ind else ind
    text = json.dumps(obj, indent=ind, ensure_ascii=False)
    return text + ("\n" if (like or "\n").endswith("\n") else "")


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
            want = mixin_edit(json.loads(b), mixin_spec(props.get(f"mixin_add.{name}")),
                              mixin_spec(props.get(f"mixin_remove.{name}")))
            return json.loads(c) == want
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


def digests(files, t):
    """(jar digest, review-inputs digest) of a version branch's tree {path: bytes}: what a gate run (the jar-relevant
    files and the build keys) and a review (also the reviewed inputs of .port/<t>/) judged."""
    blobs = hash_blobs(files)
    props = f"{REC}/{t}/{BRANCH_PROPS}"
    h = hashlib.sha256()
    for p in sorted(blobs):
        if jar_relevant(p, t) and p != props:
            h.update(f"{p}\0{blobs[p]}\n".encode("utf-8", "surrogateescape"))
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
        if build_keys(old.stdout if old.returncode == 0 else "") == build_keys(read_text(pathlib.Path(root) / props)):
            names.discard(props)
    jar = sorted(n for n in names if jar_relevant(n, t))
    review = sorted(set(jar) | {n for n in names if n.startswith(f"{REC}/{t}/") and n.rsplit("/", 1)[-1] in REVIEWED_INPUTS})
    return jar, review


def migrated(root, t):
    try:
        return json.loads(read_text(rec_dir(root, t) / "MIGRATED.json") or "{}")
    except ValueError:
        return {}


def commit_current(repo, root, t, c, ref, work, kind, files=None):
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
    jar, inputs = digests(files, t)
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


def helper_blobs(root, t):
    """{path: blob} of .port/<t>/shared-helpers.txt: the shared-library helpers this version carries, as installed."""
    out = {}
    for line in read_text(rec_dir(root, t) / "shared-helpers.txt").splitlines():
        parts = line.split()
        if len(parts) == 2 and not line.startswith("#"):
            out[parts[0]] = parts[1]
    return out


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
    items = set()
    groups = {}
    for p in sorted(set(blobs) | set(base)):
        if p not in blobs or p.startswith(REC + "/") or GATES_DOC.match(p):
            continue
        if p in base and base[p][1] == blobs[p]:
            continue
        if p not in base and p.endswith(".java"):
            continue
        if helpers.get(p) == blobs[p] or p in mech:
            continue
        if p in base:
            if base_data is None:
                base_data = {}
            if p not in base_data:
                base_data[p] = cat_blobs(repo, [base[p][1]])[base[p][1]]
            if derived_ok(p, base_data[p], files[p], props):
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
               "settings tools/fork-branches.py derives and added helper classes need no row. Verdict `ok` (the change keeps\n"
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
    text = read_text(f) or REVIEW_HEAD.format(t=t, b=p.get("branch", branch_name(t)), p=p.get("parent", "?"))
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
    fields, rows = f_.parse_review(read_text(f))
    out = [f"REVIEW.md has no {k}" for k in f_.REVIEW_FIELDS if fields.get(k, "?") in ("", "?")]
    if fields.get("date", "?") not in ("", "?") and not re.match(r"^\d{4}-\d\d-\d\d\b", fields["date"]):
        out.append(f"REVIEW.md date {fields['date']!r} is not YYYY-MM-DD")
    c = fields.get("commit", "?")
    if c not in ("", "?"):
        good, why = commit_current(repo, root, t, c, ref, work, "review", files)
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
    text = read_text(rec_dir(root, t) / "GATES.md")
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


def remerge_problems(repo, root, t, work):
    out = []
    if work:
        u = git(root, "ls-files", "-u")
        if u.stdout.strip():
            out.append("an unresolved merge in the checkout (git ls-files -u lists conflicted files): resolve or abort it")
        if git(root, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode == 0:
            out.append("a merge is in progress (MERGE_HEAD): finish or abort it")
    out += fp().remerge_open(pathlib.Path(root), rec_dir(root, t))
    return out


def inventory_problems(repo, root, t, ref, work, files, write, mechanical):
    """The behaviour inventory on version t (tools/layer-inventory.py account_branch): [] when every base item is
    accounted for AND the committed INVENTORY-CHECK.json says exactly what a fresh check says (issue #133, gap 1: the
    release-side check reads the committed file). write: rewrite the file (a run that holds the checkout)."""
    li = _mod("layer_inventory_fb", "layer-inventory.py")
    code, rep, lines = li.check_branch(repo, root, t, mechanical=mechanical, files=files)
    if rep is None:
        return [ln.replace("layer-inventory: ", "behaviour inventory: ") for ln in lines] if code else []
    want = li.check_text(rep)
    p = rec_dir(root, t) / li.CHECK_FILE
    if write and read_text(p) != want:
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
        out += remerge_problems(repo, root, t, work)
        if not (rec_dir(root, t) / "TEST-ITEMS.md").is_file():
            out.append(f"manual-test items not generated (TEST-ITEMS.md): python3 tools/fork-port.py --repo {repo} "
                       f"--target {t} --write-test-items")
        rv = read_text(rec_dir(root, t) / "REVIEW.md")
        out += fp().drop_reported_items(inventory_problems(repo, root, t, ref, work, files, write, mech),
                                        fp().not_ok_items(rv) if rv else {})
        deps = read_text(rec_dir(root, t) / "DEPS.md")
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
        deps = json.loads(read_text(rec_dir(root, t) / "DEPENDENTS.json") or "[]")
    except ValueError:
        return False
    for d in deps if isinstance(deps, list) else []:
        c = str((d or {}).get("library_commit", ""))
        if d.get("gate_c") == "PASS" and commit_current(repo, root, t, c, ref, work, "gates", files)[0]:
            return True
    return False


# ---------------------------------------------------------------- migration: a layer fork -> version branches

class Layer:
    """The layer at one commit, checked out in a scratch worktree (its own .multiversion/tools/layer.py must run).
    Removed with close(); the person's checkout is never touched."""

    def __init__(self, repo, commit, scratch):
        self.repo, self.commit = pathlib.Path(repo), commit
        self.dir = pathlib.Path(scratch) / f"layer-{commit[:12]}"
        if not self.dir.is_dir():
            git(repo, "worktree", "add", "--detach", "--force", str(self.dir), commit, check=True)
        self.mv = self.dir / ".multiversion"
        spec = importlib.util.spec_from_file_location(f"layer_{commit[:12]}", self.mv / "tools/layer.py")
        sys.dont_write_bytecode = True
        self.lp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.lp)
        self.base = parse_props(read_text(self.mv / "base.properties"))

    def close(self):
        git(self.repo, "worktree", "remove", "--force", str(self.dir))
        shutil.rmtree(self.dir, ignore_errors=True)

    def targets(self):
        d = self.mv / "targets"
        return sorted((p.name for p in d.iterdir() if (p / "target.properties").is_file()), key=mc_key) if d.is_dir() else []

    def props(self, t):
        return parse_props(read_text(self.mv / "targets" / t / "target.properties"))

    def raw_props(self, t):
        return read_text(self.mv / "targets" / t / "target.properties")

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
    adds, removes = lay.lp.mixin_set(t)
    deltas = {}
    for p in sorted(files):
        name = p.rsplit("/", 1)[-1]
        if not p.startswith("src/") or "/resources/" not in p and "/templates/" not in p:
            continue
        if name in adds or name in removes:
            obj = json.loads(files[p].decode("utf-8"))
            a = [(arr, c) for arr, cs in (adds.get(name) or {}).items() for c in cs]
            r = [(arr, c) for arr, cs in (removes.get(name) or {}).items() for c in cs]
            new = mixin_edit(obj, a, r)
            if new != obj:
                files[p] = json_like(new, files[p].decode("utf-8")).encode("utf-8")
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
                a_, r_ = mixin_delta(json.loads(before.decode("utf-8")), json.loads(files[p].decode("utf-8")))
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
    head += [f"port_branch={pb}", f"base_minecraft_version={lay.base.get('minecraft_version', '1.21.1')}"]
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
        out[f"{rd}/STUBS.json"] = translate_stubs(read_text(tdir / "STUBS.json")).encode("utf-8")
    for n in (f"shipped-expect-{t}.txt", "shipped-expect.txt"):
        if (lay.mv / n).is_file():
            out[f"{rd}/shipped-expect.txt"] = (lay.mv / n).read_bytes(); break
    if (lay.mv / "shipped-jar.txt").is_file():
        out[f"{rd}/shipped-jar.txt"] = (lay.mv / "shipped-jar.txt").read_bytes()
    drift = [l for l in read_text(lay.mv / "DRIFT.md").splitlines() if re.match(rf"^- \S+ {re.escape(t)}: ", l)]
    if drift:
        out[f"{rd}/DRIFT.md"] = ("\n".join(drift) + "\n").encode("utf-8")
    shared = [f"# ---- shared {s}\n" + read_text(lay.mv / "shared" / s / "renames.tsv") for s in sc
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
            data = json.loads(read_text(rm))
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
    text = read_text(src)
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
    adds, removes = lay.lp.mixin_set(t)
    for cfg in sorted(set(adds) | set(removes)):
        for p in [p for p in have if p.rsplit("/", 1)[-1] == cfg and p.startswith("src/")]:
            src = next((lay.mv / "targets" / v / "resources" / p.split("/resources/", 1)[1] for v in lay.chain(t)
                        if p.startswith("src/main/resources/") and
                        (lay.mv / "targets" / v / "resources" / p.split("/resources/", 1)[1]).is_file()), lay.dir / p)
            if not src.is_file():
                continue
            layer_arrays = lay.lp.apply_mixins(json.loads(read_text(src)), cfg, adds, removes)
            got = json.loads(have[p].decode("utf-8"))
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
        made, expected_of, bad = {}, {}, 0
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
                say(f"fork-branches: {t}: would make {branch_name(t)} from {row['parent']} ({start[:12]}): {n_src} source "
                    f"file(s) from the layer's prepare, records {', '.join(rec)}")
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
                txt = read_text(lay.mv / "targets" / t / name)
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
                dj, dr = digests(files_c, t)
                recorded[full] = {"digest": dj, "review_inputs": dr}
            jar, inputs = digests(dict(tree, **extra), t)
            mig = {"tool": "tools/fork-branches.py migrate", "layer_ref": ref, "layer_commit": lc, "target": t,
                   "port_commit": port_commit, "branch_commit": c1, "digest": jar, "review_inputs": inputs,
                   "recorded": recorded}
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
                layer_done = fp().target_done(lay.dir, t, write=False, prepare=True)
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
        git(repo, "worktree", "prune")
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
        git(repo, "worktree", "prune")
        shutil.rmtree(scratch, ignore_errors=True)


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
    a = ap.parse_args()
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
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        r, g, _run = _fixture(d / "a")
        _fixture_shared(r, g)
        for t in ("1.21.3", "1.21.4", "1.21.5"):
            why = _quiet(_fixture_done, r, g, t)[0]
            expect(not why, f"fixture: layer target {t} done: {why}")
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
               and b"c3" in f5["src/cf/java/com/example/mod/cf/Cf.java"] and b'"[1.21.5,1.21.6)"' in f5["src/main/resources/META-INF/neoforge.mods.toml"]
               and b"BMixin" in f5["src/main/resources/examplemod.mixins.json"] and b"v = 4" in f5["src/main/java/com/example/mod/mv/Helper.java"]
               and "src/main/resources/assets/examplemod/items/a.json" in f5,
               "1.21.5's tree: its own overlay, the rows, the cf set's inherited overlay, the toml range, the inherited mixin, the nearest helper, the inherited resource")
        expect(all(p in f5 for p in (".port/1.21.5/GATES.md", ".port/1.21.5/REVIEW.md", ".port/1.21.5/MIGRATED.json",
                                    ".port/1.21.5/INVENTORY-CHECK.json", ".port/1.21.5/TEST-ITEMS.md", ".port/1.21.4/GATES.md")),
               "records under .port/<version>/ (the lower versions' as carried copies)")
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
        (r / "src/main/java/com/example/mod/ExampleMod.java").write_text("package com.example.mod;\nclass ExampleMod { int edited; }\n",
                                                                       encoding="utf-8")
        why = target_done(r, "1.21.4")
        expect(any(w.startswith("GATES.md") and ("changed since" in w or "no longer equals" in w) for w in why)
               and "no verdict: changed src/main/java/com/example/mod/ExampleMod.java" in why,
               f"an edited file: Gate C stale, and the file (no longer the rows' alone) needs a review row: {why}")
        g("checkout", "-q", "--", "."); g("checkout", "-q", "neoforge-1.21.x")
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
    print("self-check:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
