#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""keep-current for a MULTI-VERSION fork: carry the authors' new code from the port branch into the layer branch and
say, per target, what it did.

    python3 tools/layer-sync.py detect --repo DIR
    python3 tools/layer-sync.py merge  --repo DIR --port-ref REF [--layer-branch neoforge-1.21.x]
    python3 tools/layer-sync.py run    --repo DIR [--targets 1.21.5,1.21.8] [--budget 15] [--changed-from REF]
                                       [--json out.json] [--md out.md]
    python3 tools/layer-sync.py overlays --repo DIR [--targets 1.21.4] [--json out.json]
    python3 tools/layer-sync.py remerge --repo DIR --old REF [--new HEAD] [--targets 1.21.4] [--dry-run] [--json out.json]
    python3 tools/layer-sync.py ledger --ledger ledger.json --json out.json --column keep-current/2026-10-12
    python3 tools/layer-sync.py --self-check

Single-version forks are the default and are NOT touched: every command except `detect` prints "not a multi-version
fork" and exits 0 having changed nothing when the checkout has no .multiversion/ (base.properties + at least one
target). The keep-current skill only calls this for a fork that has the layer.

  detect  prints layer=true|false (and the targets). A fork with VERSION BRANCHES (a `neoforge-<v>` branch carrying
          `.port/<v>/branch.properties`, tools/fork-branches.py) is a version-branch fork even when a legacy layer
          branch with .multiversion/ is still there: version branches win, that layer branch is history and the round
          ignores it, so detect prints `layer=false kind=version-branches versions=...` (with `legacy_layer=` naming
          the ignored branches) and exits 0, `--ref <layer branch>` included. A fork with neither prints as before.
  merge   merge REF (the 1.21.1 port branch, or the keep-current work branch cut from it) into the checked-out layer
          branch with a MERGE COMMIT (--no-ff; never a rebase, never a force-push), then run the layer's isolation rule
          against REF. Refuses a dirty tree. A conflict is aborted and listed: it is a person's.
  run     for every target (oldest first), with tools/fork-port.py's own stages:
            measure   `--from-stage stage --to-stage measure`  staging, the generated class-move table, the compile count
            audit     `--from-stage audit --to-stage audit`    mixin + event-listener audits, when the count is 0
            loop      `--from-stage loop --to-stage audit`     the compile loop (budget $N), then the audits, when it is not
          and classify it:
            untouched  0 errors and clean audits straight after the merge: upstream's change did not reach this target
            touch-up   errors after the merge, 0 errors and clean audits after the loop: the loop cleared it
            rework     anything left (errors, an audit mismatch, a stage that would not run): fork-port.py wrote
                       .multiversion/targets/<t>/RESIDUE.md; THE RUN STOPS FOR A PERSON (exit 10). A rework target is
                       never skipped, silently or otherwise, and never reported as done.
          It also lists, per target, the upstream-changed files that target OVERLAYS (an overlay replaces the whole
          file, so a clean compile does not say the overlay still does what upstream's new version does).
          Writes a JSON record and the markdown table (target | errors before | errors after | class | residue) that
          goes in the PR body.
          When the authors' code moved (HEAD is the merge, or --changed-from), `run` first RE-MERGES the overlays
          (`remerge` below, OLD = the merge's first parent, NEW = HEAD) so the compile sees them: a target with an
          overlay that did not merge is rework (its hunks in RESIDUE.md), one whose overlays merged is at least a
          touch-up. The table's notes add the re-merge and each target's overlay budget (`overlays` below); a target
          with no overlay gets no such note, so the output is as before.
  overlays  the overlay budget, per target: "whole-file overlays (N) + overlay-only files (M)" (the one wording, also
          tools/fork-port.py's) and the whole-file ones' lines, each against what it replaces (the
          authors' file through the rename table alone, or the overlay it inherits for the same path) -- real,
          mechanical (names only: rename rows would do), redundant (identical), overlay-only (replaces nothing, not
          counted) --, how many are mixins / client code, and a WARNING above the budget (default 30;
          .multiversion/ci.properties `overlay_budget=N`). Changes nothing. With `inherited_overlay_rows=true` in
          .multiversion/base.properties (opt-in; the fork's layer.py then runs an inherited overlay through the rows
          of the targets after its owner) the overlay it inherits is read through those rows, as the target's tree
          gets it, so a renamed copy they produce is redundant -- in either mode only when deleting it changes no
          target's tree (a later target that takes the path from it may have rows written against it: then it is
          renamed-copy / mechanical with `kept_for`) --; the re-merge retires a copy on the same condition. A
          layer.py older than the key ignores it: read as off, with a warning to run make-fork-layer.py sync-shared.
  remerge for each target overlay whose authors' file changed between OLD and NEW (the layer branch before and after
          `merge`: its src/ is the port branch's), a three-way merge with `git merge-file` semantics: base = the
          authors' file at OLD PREPARED for the target (its rename chain and strip_onlyin, no overlay), ours = the
          overlay, theirs = the authors' file at NEW prepared the same way (an overlay of a path an older target in
          its chain also overlays: that overlay before / after its own re-merge, renamed by the target's own rows; when
          that older overlay did not change in this run -- it conflicted, or its target is not in --targets -- the
          authors' files prepared for this target, as for an overlay of its own, so the copy is never skipped). A
          clean merge rewrites the overlay; one that becomes what it replaces is deleted (retired); a conflict, or a
          clean merge that declares a member twice (the loop's stand-in plus the authors' own), leaves the overlay AS
          IT WAS, writes its hunks to the target's RESIDUE.md and an entry to .multiversion/targets/<t>/REMERGE.json
          ({"conflicts": [{"overlay", "digest" (sha256 of its bytes), "reason", "since": "<old>..<new>"}]}, deleted
          when empty; fork-port.py does not call the target done while an entry's overlay is unedited) (exit 10). A
          target that inherits an older target's overlay AS IT IS (no copy of its own) gets an entry naming that
          overlay when it did not take the change ("inherits an overlay that did not take the authors' change"). An
          overlay edited since it was recorded is a person's resolution: left alone and its entry dropped; an
          unedited one is merged again against the authors' file at its `since`, and its entry goes when that merges.
          --dry-run writes nothing.
  ledger  writes the table into a fix ledger's JSON ("layer"), so `fix-ledger.py render --into` puts it in the issue.

Gates are not run here: the layer's CI runs the full gates on the bookends and compile + audits on every middle
target, and keep-current's gate step runs `ci-gates.py --mc` for the targets it releases.
Environment (tests): LAYER_SYNC_FORK_PORT replaces tools/fork-port.py. Standard library only.
"""
import argparse, importlib.util, json, os, pathlib, re, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
CLASSES = ("untouched", "touch-up", "rework")


def _layer_ci():
    spec = importlib.util.spec_from_file_location("layer_ci_for_sync", HERE / "layer-ci.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def git(repo, *a, check=False):
    r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode:
        raise SystemExit(f"git {' '.join(a)}: {(r.stderr or r.stdout).strip()[:300]}")
    return r


# ---------------------------------------------------------------- pure helpers (covered by --self-check)

def layer_targets(repo):
    """The fork's layer targets, oldest first; [] when it has no layer (a single-version fork)."""
    p = _layer_ci().plan(repo)
    return list(p["targets"]) if p["layer"] == "true" else []


def classify(before, audit_ok_before, after, audit_ok_after, stopped):
    """-> class. before/after: compile error counts (None = no count); audit_ok_*: True/False/None (not run).
    stopped: a stage refused to finish (it wrote a RESIDUE.md)."""
    if stopped:
        return "rework"
    if before == 0 and audit_ok_before:
        return "untouched"
    if before is not None and before > 0 and after == 0 and audit_ok_after:
        return "touch-up"
    return "rework"


def shadowed(mv, t, changed):
    """Upstream-changed files that target t (or a target it inherits) overlays: changed is a list of repo paths; an
    overlay lives at .multiversion/targets/<x>/java/<path under src/main/java>."""
    ch, seen, x = [], set(), t
    while x and x not in seen:
        seen.add(x); ch.append(x)
        props = pathlib.Path(mv) / "targets" / x / "target.properties"
        x = None
        if props.is_file():
            m = re.search(r"(?m)^\s*inherits\s*=\s*(\S+)", props.read_text(encoding="utf-8"))
            x = m.group(1) if m else None
    out = []
    for f in changed:
        m = re.match(r"src/(?:main|[\w-]+)/java/(.+)$", f)
        if not m:
            continue
        for c in ch:
            sub = pathlib.Path(mv) / "targets" / c / ("java" if f.startswith("src/main/") else f"{f.split('/')[1]}/java")
            if (sub / m.group(1)).is_file():
                out.append(f)
                break
    return sorted(out)


def table(rows):
    cell = lambda v: "-" if v is None else str(v)
    md = ["| target | errors before | errors after | class | residue |", "|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['target']} | {cell(r['before'])} | {cell(r['after'])} | {r['class']} | "
                  f"{('`' + r['residue'] + '`') if r.get('residue') else '-'} |")
    notes = [f"- `{r['target']}` overlays {len(r['shadowed'])} file(s) upstream changed: {', '.join(r['shadowed'][:6])}"
             f"{' ...' if len(r['shadowed']) > 6 else ''} -- read each overlay against upstream's new version"
             for r in rows if r.get("shadowed")]
    for r in rows:
        rm = r.get("remerge")
        if rm and (rm["rewritten"] or rm["retired"] or rm["conflicts"]):
            notes.append(f"- `{r['target']}` overlays re-merged with the authors' change: {len(rm['rewritten'])} rewritten, "
                         f"{len(rm['retired'])} retired (now identical to the prepared authors' file), "
                         f"{len(rm['conflicts'])} did not merge" + (" (hunks in its RESIDUE.md)" if rm["conflicts"] else ""))
    notes += [f"- `{r['target']}` overlay budget: {overlay_note(r['overlays'])}" for r in rows if overlay_note(r.get("overlays"))]
    notes += [f"- `{r['target']}`: {r['note']}" for r in rows if r.get("note")]
    return "\n".join(md + ([""] + notes if notes else []))


def stopped_block(rows):
    bad = [r for r in rows if r["class"] == "rework"]
    if not bad:
        return ""
    lines = [f"STOPPED for a person: {len(bad)} target(s) need rework -- never skipped, never released as is:"]
    for r in bad:
        lines.append(f"  {r['target']}: {r['note'] or 'residue left'}" + (f" -> {r['residue']}" if r.get("residue") else ""))
    lines += ["Read each RESIDUE.md, fix what it lists in the shared library (templates/fork-layer/shared/<t>) or a",
              "converter -- not in one fork -- and resume with: python3 tools/layer-sync.py run --repo DIR --targets "
              + ",".join(r["target"] for r in bad)]
    return "\n".join(lines)


# ---------------------------------------------------------------- overlays: the budget (R6a) and the re-merge (R6b)

DEFAULT_OVERLAY_BUDGET = 30          # whole-file overlays per target; .multiversion/ci.properties `overlay_budget=N`
TEMPLATE_TOOLS = HERE.parent / "templates" / "fork-layer" / "tools"
RESIDUE_SECTION = "## Overlays the authors' change did not merge into (layer-sync)"
JAVA_WORDS = set("""abstract assert boolean break byte case catch char class const continue default do double else enum
extends final finally float for goto if implements import instanceof int interface long native new package private
protected public return short static strictfp super switch synchronized this throw throws transient try void volatile
while var record yield sealed permits true false null""".split())
TOKEN = re.compile(r"[A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)*|\S")
MODS = r"(?:public|protected|private|static|final|abstract|synchronized|native|default|transient|volatile)"
MEMBER = re.compile(rf"^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:{MODS}\s+)+(?:<[^>]*>\s+)?([\w$.<>\[\],?\s]+?)\s+([\w$]+)\s*"
                    r"(\(([^)]*)\))?\s*(?:throws\s+[\w$.,\s]+)?(\{|;|=)")


def _module(path, name):
    sys.dont_write_bytecode = True        # never leave a __pycache__ in the fork's .multiversion/tools
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def _read(p):
    with open(p, encoding="utf-8", errors="surrogateescape", newline="") as fh:
        return fh.read()


def _write(p, text):
    p = pathlib.Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", errors="surrogateescape", newline="") as fh:
        fh.write(text)


def _lf(text):
    return None if text is None else text.replace("\r\n", "\n")


def git_blob(repo, ref, path):
    """The file at REF (bytes kept, CRLF and all), or None when REF has no such file."""
    r = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:{path}"], capture_output=True)
    return r.stdout.decode("utf-8", "surrogateescape") if r.returncode == 0 else None


INHERITED_KEY = "inherited_overlay_rows"


def inherited_rows_setting(mv):
    """(key_on, supported): .multiversion/base.properties says `inherited_overlay_rows=true`, and the fork's
    .multiversion/tools/layer.py knows the key (an older copy prepares as if it were off). The setting is in effect
    only when both hold; Preparer, tools/layer-test-plan.py and tools/fork-port.py read it the same way."""
    mv = pathlib.Path(mv)
    bp, lp = mv / "base.properties", mv / "tools" / "layer.py"
    key_on = bp.is_file() and _layer_ci().read_props(bp).get(INHERITED_KEY, "").lower() == "true"
    supported = lp.is_file() and f"def {INHERITED_KEY}(" in lp.read_text(encoding="utf-8", errors="replace")
    return key_on, supported


def inherited_rows_unsupported(mv):
    """The warning when base.properties sets the key and the fork's layer.py ignores it, else ""."""
    key_on, supported = inherited_rows_setting(mv)
    return (f"WARNING: .multiversion/base.properties sets {INHERITED_KEY}=true, but .multiversion/tools/layer.py "
            f"predates {INHERITED_KEY} and IGNORES it -- run sync-shared (python3 tools/make-fork-layer.py sync-shared "
            f"--repo DIR) to bring the template's layer.py in") if key_on and not supported else ""


class Preparer:
    """Prepare single authors' files for one target as the fork's own `layer.py prepare` does for the whole tree --
    the same rename table (the target's chain, its generated moves and the shared tables), the fork's own
    prepare-sources.py, and strip_onlyin when the target sets it -- but with no overlay: the PREPARED authors' file an
    overlay replaces. The table is the one the fork's layer.py hands prepare-sources.py: its cmd_prepare runs with that
    one call caught, so whatever version of layer.py the fork carries, the table is its own."""

    def __init__(self, repo):
        self.repo = pathlib.Path(repo)
        self.mv = self.repo / ".multiversion"
        own = self.mv / "tools" / "layer.py"
        if not own.is_file():
            raise RuntimeError(f"{own} is missing: the layer cannot prepare a target")
        self.layer = _module(own, "layer_fork_for_sync")
        ps = self.mv / "tools" / "prepare-sources.py"
        self.ps = ps if ps.is_file() else TEMPLATE_TOOLS / "prepare-sources.py"
        self.tables = {}
        # base.properties `inherited_overlay_rows=true` (opt-in; read by the fork's own layer.py, so a layer.py older
        # than the key keeps it off): an overlay a target inherits reaches its tree through hop_table's rows
        opt = getattr(self.layer, "inherited_overlay_rows", None)
        self.inherited_rows = bool(opt and opt(self.mv))
        warn = inherited_rows_unsupported(self.mv)
        if warn:
            print(f"layer-sync: {warn}", file=sys.stderr)

    def rename_table(self, t):
        if t in self.tables:
            return self.tables[t]
        import contextlib, io, types
        got = {}

        def call(args, *_a, **_k):
            if "--renames" in args:
                got["table"] = pathlib.Path(args[args.index("--renames") + 1]).read_text(encoding="utf-8")
            return 1                                      # stop cmd_prepare there: no tree is written
        real, cwd = self.layer.subprocess, os.getcwd()
        self.layer.subprocess = types.SimpleNamespace(call=call)
        try:
            os.chdir(self.repo)
            with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
                self.layer.cmd_prepare(argparse.Namespace(target=t, src="src/main/java", out=str(pathlib.Path(d) / "out"),
                                                          set="main"))
        except SystemExit as e:
            raise RuntimeError(f"layer.py prepare --target {t}: {e}")
        finally:
            self.layer.subprocess = real
            os.chdir(cwd)
        if "table" not in got:
            raise RuntimeError(f"layer.py prepare --target {t} never ran prepare-sources.py with a rename table")
        self.tables[t] = got["table"]
        return got["table"]

    def chain(self, t):
        return self.layer.chain(t, self.mv)

    def props(self, t):
        return _layer_ci().read_props(self.mv / "targets" / t / "target.properties")

    def hop_table(self, t, anc):
        """The rows t's table has and anc's (an older target in t's chain) does not -- the sections layer.py writes as
        `# ---- <t>`, `# ---- moves <t> (generated)`, `# ---- shared <t>`: what turns a file prepared for anc into one
        prepared for t. An overlay t inherits from anc reaches t's tree verbatim (overlays are not run through the
        table), so t's own copy of the path is anc's overlay with these rows applied by hand, plus t's real changes.
        With inherited_overlay_rows=true layer.py runs anc's overlay through exactly these rows (inherited())."""
        def sections(text):
            out, cur = {}, None
            for line in text.split("\n"):
                if line.startswith("# ---- "):
                    cur = line; out[cur] = []
                elif cur is not None:
                    out[cur].append(line)
            return out
        mine, theirs = sections(self.rename_table(t)), sections(self.rename_table(anc))
        return "\n".join(h + "\n" + "\n".join(body) for h, body in mine.items() if h not in theirs) + "\n"

    def inherited(self, t, anc, files):
        """{rel: anc's overlay text} -> {rel: what t's tree gets for that path when t has no copy of its own}: the
        overlay verbatim (the default), or, with inherited_overlay_rows=true, run through hop_table(t, anc) as
        layer.py prepare does (then strip_onlyin when t sets it, as for any file of t's tree)."""
        return self.prepare(t, files, self.hop_table(t, anc)) if self.inherited_rows and files else dict(files)

    def tree_files(self, u, owner, files):
        """{rel: owner's overlay text} -> {rel: exactly what u's tree holds for that path when `owner` (u itself, or an
        older target of u's chain) is its nearest owner, in either mode: the overlay as it is -- or, with
        inherited_overlay_rows=true and an older owner, through hop_table(u, owner) -- then strip_onlyin when u sets
        it (layer.py strips u's whole prepared tree, every overlay in it included)."""
        if owner != u and self.inherited_rows:
            return self.inherited(u, owner, files)                       # prepare() strips too
        return self.strip(u, files)

    def strip(self, u, files):
        """{rel: text} with layer.py's own strip_onlyin applied, on files in a temporary tree, when u sets
        strip_onlyin=true (as prepare() does) -- the same bytes layer.py writes, CRLF files included."""
        if not files or self.props(u).get("strip_onlyin") != "true" or not hasattr(self.layer, "strip_onlyin"):
            return dict(files)
        import contextlib, io
        with tempfile.TemporaryDirectory() as d:
            for rel, text in files.items():
                _write(pathlib.Path(d) / rel, text)
            with contextlib.redirect_stdout(io.StringIO()):
                self.layer.strip_onlyin(d)
            return {rel: _read(pathlib.Path(d) / rel) for rel in files}

    def targets(self):
        """Every layer target (a targets/<t>/target.properties), sorted by name."""
        td = self.mv / "targets"
        return sorted(d.name for d in td.iterdir() if (d / "target.properties").is_file()) if td.is_dir() else []

    def reach(self, t, s, rel, owns):
        """The targets whose tree takes path (s, rel) from target t's overlay: t, and every target that has t in its
        chain with no nearer owner of the path (owns(x) -> True when target x has an overlay of it)."""
        out = []
        for u in self.targets():
            ch = self.chain(u)
            if t in ch and not any(owns(x) for x in ch[:ch.index(t)]):
                out.append(u)
        return out

    def prepare(self, t, files, table=None):
        """{rel: authors' text} -> {rel: that text prepared for t (renames + chain, no overlay)}; with `table`, those
        rows instead of t's whole table (hop_table)."""
        if not files:
            return {}
        import contextlib, io
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            for rel, text in files.items():
                _write(d / "src" / rel, text)
            (d / "renames.tsv").write_text(self.rename_table(t) if table is None else table, encoding="utf-8")
            r = subprocess.run([sys.executable, str(self.ps), "--src", str(d / "src"), "--out", str(d / "out"),
                                "--renames", str(d / "renames.tsv")], capture_output=True, text=True, encoding="utf-8",
                               errors="replace")
            if r.returncode:
                raise RuntimeError(f"prepare-sources for {t}: {(r.stderr or r.stdout).strip()[-300:]}")
            if self.props(t).get("strip_onlyin") == "true" and hasattr(self.layer, "strip_onlyin"):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.layer.strip_onlyin(d / "out")
            return {rel: _read(d / "out" / rel) for rel in files}


def overlay_sets(mv, t):
    """[(source set, overlay dir)]: main's overlays are targets/<t>/java, another set's targets/<t>/<set>/java."""
    td = pathlib.Path(mv) / "targets" / t
    out = [("main", td / "java")] if (td / "java").is_dir() else []
    if td.is_dir():
        out += [(d.name, d / "java") for d in sorted(td.iterdir()) if d.name != "java" and (d / "java").is_dir()]
    return out


def own_overlays(mv, t):
    """{(set, rel): file} for the whole-file .java overlays target t itself carries (not the ones it inherits)."""
    return {(s, f.relative_to(d).as_posix()): f for s, d in overlay_sets(mv, t) for f in sorted(d.rglob("*.java"))}


def src_path(s, rel):
    return f"src/{s}/java/{rel}"


def whole_file_overlays(repo, t):
    """THE definition of a whole-file overlay, one for every tool: a .java file target t itself carries in
    targets/<t>/java (main) or targets/<t>/<set>/java that replaces the authors' src/<set>/java/<same path>. An own
    overlay whose authors' file does not exist replaces nothing (overlay-only: a compat class), inherited or not.
    overlay_report() counts these against the budget; tools/fork-port.py gives each one a REVIEW.md row and audits it
    for stub signals. -> [(set, rel, overlay path, authors' path)], sorted."""
    repo = pathlib.Path(repo)
    return [(s, rel, f, repo / src_path(s, rel)) for (s, rel), f in sorted(own_overlays(repo / ".multiversion", t).items())
            if (repo / src_path(s, rel)).is_file()]


def overlay_path(t, s, rel):
    return f".multiversion/targets/{t}/{'java' if s == 'main' else s + '/java'}/{rel}"


def rename_only(ref, ours):
    """True when `ours` differs from `ref` only by identifiers swapped for identifiers, line for line -- what a rename
    row (a `member:` row for `x.old` -> `x.new`) would produce, so the overlay is mechanical and should be rows."""
    a, b = _lf(ref).split("\n"), _lf(ours).split("\n")
    if len(a) != len(b) or a == b:
        return False
    for x, y in zip(a, b):
        if x == y:
            continue
        tx, ty = TOKEN.findall(x), TOKEN.findall(y)
        if len(tx) != len(ty):
            return False
        for p, q in zip(tx, ty):
            if p != q and not (re.match(r"[A-Za-z_$]", p) and re.match(r"[A-Za-z_$]", q)
                               and p not in JAVA_WORDS and q not in JAVA_WORDS):
                return False
    return True


def overlay_budget(mv):
    raw = _layer_ci().read_props(pathlib.Path(mv) / "ci.properties").get("overlay_budget", "")
    return int(raw) if raw.strip().isdigit() else DEFAULT_OVERLAY_BUDGET


def _mc_key(v):
    return tuple(int(x) if x.isdigit() else 0 for x in re.findall(r"\d+|[A-Za-z]+", v))


def overlay_report(repo, t, prep=None):
    """Target t's own whole-file overlays, each against what it replaces (the authors' file prepared for t by the
    rename table alone, or the overlay t inherits for the same path):
      real        differs from it in more than names: a real change (the reason overlays exist)
      mechanical  differs from the prepared authors' file only by identifiers swapped for identifiers: rename rows
                  would do it
      renamed-copy  differs from the overlay it inherits only by names: a copy kept because overlays are not run
                  through the table (rows cannot reach it)
      redundant   identical to it, and deleting it changes no target's tree: every later target that takes the path
                  from this overlay (no nearer copy of its own) gets the same file from what it replaces -- rows of a
                  later target can be written against this copy (they are then not applied to the older file the same
                  way: the token sweep does not re-scan its own output), so a copy identical at its own target is
                  `renamed-copy` (or, against the authors' file, `mechanical`) with `kept_for` naming those targets
    With .multiversion/base.properties `inherited_overlay_rows=true` what an inherited path is read against is the
    older overlay run through the rows after it (Preparer.inherited), as the target's tree would get it: a renamed copy
    those rows produce is then redundant.
      overlay-only  replaces no authors' file (a compat class; whole_file_overlays() is the one definition):
                  listed, not counted against the budget, and not a REVIEW.md row
    plus lines, and which are mixins / client code. `overlays` (counted against the budget) excludes overlay-only."""
    repo = pathlib.Path(repo); mv = repo / ".multiversion"
    prep = prep or Preparer(repo)
    chain = prep.chain(t)[1:]
    own = own_overlays(mv, t)
    whole = {(s, rel) for s, rel, _f, _src in whole_file_overlays(repo, t)}
    inherited = {}
    for k in own:
        anc = next((a for a in chain if (mv.parent / overlay_path(a, *k)).is_file()), None)
        if anc:
            inherited[k] = anc
    authors = {f"{s}/{rel}": _read(repo / src_path(s, rel)) for (s, rel) in own
               if (s, rel) not in inherited and (repo / src_path(s, rel)).is_file()}
    prepared = prep.prepare(t, authors)
    anc_text = {}
    for (s, rel), a in inherited.items():
        anc_text.setdefault(a, {})[f"{s}/{rel}"] = _read(mv.parent / overlay_path(a, s, rel))
    via = {(a, k): v for a, fs in anc_text.items() for k, v in prep.inherited(t, a, fs).items()}
    mine = {f"{s}/{rel}": _read(f) for (s, rel), f in own.items()}
    at_t = prep.tree_files(t, t, mine) if prep.inherited_rows else mine     # t's own copies as t's tree holds them
    files, kept_for = [], {}
    for (s, rel), f in sorted(own.items()):
        ours = _read(f)
        if (s, rel) not in whole:
            ref, against = None, ""                       # replaces no authors' file: overlay-only, as fork-port.py reads it
        elif (s, rel) in inherited:
            ref, against = via[(inherited[(s, rel)], f"{s}/{rel}")], f"overlay of {inherited[(s, rel)]}"
        else:
            ref, against = prepared.get(f"{s}/{rel}"), "authors' file"
        kind = ("overlay-only" if ref is None else "redundant" if _lf(ref) == _lf(at_t[f"{s}/{rel}"])
                else ("renamed-copy" if (s, rel) in inherited else "mechanical") if rename_only(ref, ours) else "real")
        files.append({"file": overlay_path(t, s, rel), "lines": len(_lf(ours).splitlines()), "kind": kind,
                      "against": against if ref is not None else "",
                      "mixin": "@Mixin(" in ours or "/mixin/" in rel.lower(),
                      "client": "/client/" in rel.lower() or "Dist.CLIENT" in ours})
    # redundant only when deleting it changes no later target's tree either (the targets that take the path from t)
    cand = {(s, rel) for (s, rel) in own if overlay_path(t, s, rel) in {x["file"] for x in files if x["kind"] == "redundant"}}
    later = {}                                          # (u, owner below t or None) -> {key: (copy, what replaces it)}
    for (s, rel) in sorted(cand):
        owns = lambda x, s=s, rel=rel: (mv.parent / overlay_path(x, s, rel)).is_file()
        for u in prep.reach(t, s, rel, owns):
            if u != t:
                later.setdefault((u, inherited.get((s, rel))), set()).add((s, rel))
    for (u, below), keys in sorted(later.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        ks = {f"{s}/{rel}": (s, rel) for s, rel in keys}
        with_ = prep.tree_files(u, t, {k: mine[k] for k in ks})
        if below:
            without = prep.tree_files(u, below, {k: _read(mv.parent / overlay_path(below, *ks[k])) for k in ks})
        else:
            without = prep.prepare(u, {k: _read(repo / src_path(*ks[k])) for k in ks})
        for k, sk in ks.items():
            if _lf(with_[k]) != _lf(without[k]):
                kept_for.setdefault(sk, []).append(u)
    for x in files:
        sk = next(k for k in own if overlay_path(t, *k) == x["file"])
        if sk in kept_for:
            x["kind"] = "renamed-copy" if sk in inherited else "mechanical"
            x["kept_for"] = sorted(kept_for[sk], key=_mc_key)
    counted = [x for x in files if x["kind"] != "overlay-only"]
    budget = overlay_budget(mv)
    n = lambda k: sum(1 for x in files if x["kind"] == k)
    return {"target": t, "overlays": len(counted), "lines": sum(x["lines"] for x in counted),
            "real": n("real"), "mechanical": n("mechanical"), "renamed_copy": n("renamed-copy"), "redundant": n("redundant"),
            "overlay_only": n("overlay-only"),
            "mixin": sum(1 for x in counted if x["mixin"]), "client": sum(1 for x in counted if x["client"]),
            "budget": budget, "over": len(counted) > budget, "files": files}


def overlay_counts(o):
    """The one wording of a target's overlay counts, here and in tools/fork-port.py: the whole-file overlays (budgeted,
    one REVIEW.md row each) and the overlay-only files (replace nothing: neither)."""
    return f"whole-file overlays ({o['overlays']}) + overlay-only files ({o['overlay_only']})"


def overlay_note(o):
    if not o or not (o["overlays"] or o["overlay_only"]):
        return ""
    s = (f"{overlay_counts(o)}; the whole-file ones {o['lines']} lines: {o['real']} real, {o['mechanical']} mechanical "
         f"(rename rows would do), {o['renamed_copy']} renamed copies of an inherited overlay, {o['redundant']} redundant "
         f"(identical to what they replace); {o['mixin']} mixin, {o['client']} client")
    return s + (f" -- OVER the overlay budget of {o['budget']}" if o["over"] else "")


def merge_file(ours, base, theirs):
    """git merge-file semantics on LF text -> (merged text, conflict count). The overlay's own line endings are kept."""
    crlf = "\r\n" in ours
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for name, text in (("ours", ours), ("base", base), ("theirs", theirs)):
            p = pathlib.Path(d) / name; _write(p, _lf(text)); paths.append(str(p))
        r = subprocess.run(["git", "merge-file", "-p", "--diff3", "-L", "overlay", "-L", "authors' file before (prepared)",
                            "-L", "authors' file now (prepared)", *paths], capture_output=True)
    if r.returncode < 0 or r.returncode > 127:
        raise RuntimeError(f"git merge-file failed: {r.stderr.decode('utf-8', 'replace').strip()[:200]}")
    out = r.stdout.decode("utf-8", "surrogateescape")
    return (out.replace("\n", "\r\n") if crlf else out), r.returncode


def conflict_hunks(merged):
    hunks, cur = [], None
    for line in _lf(merged).split("\n"):
        if line.startswith("<<<<<<< "):
            cur = [line]
        elif cur is not None:
            cur.append(line)
            if line.startswith(">>>>>>> "):
                hunks.append("\n".join(cur)); cur = None
    return hunks


def _members(text):
    from collections import Counter
    c = Counter()
    lines = _lf(text).split("\n")
    for i, line in enumerate(lines):
        m = MEMBER.match(line)
        if m:
            if m.group(3) is not None:   # a method: name + parameter types
                types = ",".join(" ".join(p.split()[:-1]) for p in m.group(4).split(",") if p.strip())
                c[f"method {m.group(2)}({types})"] += 1
            else:
                c[f"field {m.group(2)}"] += 1
        st = line.strip()
        if st.startswith("@") and i + 1 < len(lines) and lines[i + 1].strip() == st:
            c[f"annotation {st} twice in a row"] += 2
    return c


def duplicate_members(merged, ours, theirs):
    """What a CLEAN merge declares more often than either side did: a member the overlay (the compile loop's stand-in)
    already had and the authors' change added again, at another place -- the merge cannot see that, javac can."""
    m, o, t = _members(merged), _members(ours), _members(theirs)
    return sorted(k for k, n in m.items() if n > 1 and n > max(o[k], t[k]))


def remerge(repo, old, new, targets=None, write=True, prep=None):
    """Carry the authors' change OLD -> NEW into every overlay that replaces a file it touched: a three-way merge per
    overlay (base = the authors' file at OLD prepared for the target, ours = the overlay, theirs = the authors' file at
    NEW prepared for the target; for an overlay of a path an older target in its chain also overlays, base/theirs are
    that overlay before/after its own re-merge in this run, renamed by this target's own rows -- hop_table -- and,
    when that older overlay did not change in this run (it conflicted, it already carried the change, or its target is
    outside `targets`), the authors' files prepared for this target, as for an overlay of its own). OLD/NEW: the layer
    branch before and after `merge` (its src/ is the port branch's at the last and at the new merge). Per target ->
      rewritten   merged cleanly: the overlay now holds the authors' change (a touch-up)
      retired     merged into exactly what it replaces, and deleting it changes no later target's tree either (one that
                  takes the path from this overlay): the overlay is deleted
      conflicts   did not merge (or merged into a duplicate member): the overlay is LEFT AS IT WAS (no conflict marker
                  ever reaches the tree), its hunks go to the target's RESIDUE.md and an entry to its REMERGE.json --
                  rework. Also an overlay the target INHERITS as it is (no copy of its own) when the older target's
                  overlay did not take the change: the target's tree builds that overlay, so it is the target's too.
      resolved    recorded by an earlier re-merge and edited since (its digest moved): a person's resolution, left alone
    An overlay REMERGE.json still lists unedited is merged again against the authors' file at the commit it was recorded
    against (its `since`), so a later range never drops the earlier change. write=False is a dry run: nothing is
    written."""
    repo = pathlib.Path(repo); mv = repo / ".multiversion"
    prep = prep or Preparer(repo)
    ts = layer_targets(repo)
    want = set(targets or ts)
    look = set(want)                      # an ancestor outside `targets` is looked at (never written): its children ask
    for t in want:
        look.update(prep.chain(t)[1:])
    shorts = {}
    sn = lambda ref: shorts.setdefault(ref, short(repo, ref))
    commit = lambda ref: git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode == 0
    orig, now, out = {}, {}, {}
    took = {}                # (t, set, rel) -> True (the overlay carries the change) or why it does not
    owned = {(x, *k) for x in ts for k in own_overlays(mv, x)}       # every overlay of the layer, before this run
    gone = set()                                                       # (x, set, rel) retired in this run

    def retire_to(t, s, rel, text):
        """What t's tree gets for (s, rel) once t's overlay is deleted -- the nearest older overlay still there (one
        retired in this run is not), through the rows after it, else the authors' file at NEW prepared for t -- when
        deleting it (holding `text`) changes no tree: t's, and every later target's that takes the path from t's
        overlay (rows of a later target can be written against t's copy). None when it would change one."""
        key = f"{s}/{rel}"
        has = lambda x: (x, s, rel) in owned and (x, s, rel) not in gone
        authors = git_blob(repo, new, src_path(s, rel))
        got = None
        for u in [t] + [x for x in prep.reach(t, s, rel, has) if x != t]:
            below = next((a for a in prep.chain(u)[prep.chain(u).index(t) + 1:] if has(a)), None)
            # t itself is judged as before (off: the texts as they are); a later target by exactly what its tree holds
            exact = u != t or prep.inherited_rows
            if below:
                without = (prep.tree_files if exact else prep.inherited)(u, below, {key: now[(below, s, rel)]})[key]
            elif authors is None:
                return None
            else:
                without = prep.prepare(u, {key: authors})[key]
            with_ = prep.tree_files(u, t, {key: text})[key] if exact else text
            if _lf(with_) != _lf(without):
                return None
            got = without if got is None else got
        return got
    for t in ts:
        own = own_overlays(mv, t)
        for k, f in own.items():
            orig[(t, *k)] = now[(t, *k)] = _read(f)
        if t not in look:
            continue
        live = t in want
        rec = {"rewritten": [], "retired": [], "conflicts": [], "resolved": [], "checked": 0}
        if live:
            out[t] = rec
        left = remerge_left(repo, t)                   # what an earlier re-merge left for a person, and its digest then
        chain = prep.chain(t)[1:]
        jobs, need_old, need_new, hop = [], {}, {}, {}
        for (s, rel) in sorted(own):
            path, ours, key = overlay_path(t, s, rel), now[(t, s, rel)], f"{s}/{rel}"
            anc = next((a for a in chain if (a, s, rel) in orig), None)
            pend = left.get(path)
            if pend and pend.get("digest") != digest(ours):
                rec["checked"] += 1; rec["resolved"].append(path); took[(t, s, rel)] = True
                continue                               # a person edited it since it was left: theirs now
            since_old = (pend or {}).get("since", "").split("..")[0]
            if pend and since_old and not commit(since_old):
                rec["checked"] += 1; took[(t, s, rel)] = "it did not merge before"
                rec["conflicts"].append({"file": path, "why": f"{pend.get('reason') or 'did not merge'} (recorded against "
                                         f"{since_old}, which is not a commit here: not re-merged)", "hunks": [],
                                         "digest": pend["digest"], "since": pend.get("since", "")})
                continue
            if anc and not pend and orig[(anc, s, rel)] != now[(anc, s, rel)]:
                jobs.append({"s": s, "rel": rel, "anc": anc, "hop": True, "since": f"{sn(old)}..{sn(new)}"})
                hop.setdefault(anc, ({}, {}))
                hop[anc][0][key] = orig[(anc, s, rel)]
                hop[anc][1][key] = now[(anc, s, rel)]
                continue
            # an own overlay, or a copy of an inherited one whose older overlay did not change in this run (it
            # conflicted, already carried the change, or was not re-merged): against the authors' files for t
            base_ref = since_old if pend and since_old else old
            o, n = git_blob(repo, base_ref, src_path(s, rel)), git_blob(repo, new, src_path(s, rel))
            if o == n:
                if pend:                               # nothing left to take: the entry goes
                    rec["checked"] += 1; took[(t, s, rel)] = True
                continue
            if o is not None:
                need_old.setdefault(base_ref, {})[key] = o
            if n is not None:
                need_new[key] = n
            jobs.append({"s": s, "rel": rel, "anc": anc, "hop": False, "base_ref": base_ref, "o": o is not None,
                         "n": n is not None, "since": f"{sn(base_ref)}..{sn(new)}"})
        p_old = {ref: prep.prepare(t, files) for ref, files in need_old.items()}
        p_new = prep.prepare(t, need_new)
        h_old, h_new = {}, {}       # an inherited overlay before / after its re-merge, renamed by t's own rows
        for anc, (bo, bn) in hop.items():
            tab = prep.hop_table(t, anc)
            h_old.update({(anc, k): v for k, v in prep.prepare(t, bo, tab).items()})
            h_new.update({(anc, k): v for k, v in prep.prepare(t, bn, tab).items()})
        for j in jobs:
            rec["checked"] += 1
            s, rel, anc = j["s"], j["rel"], j["anc"]
            key, ours = f"{s}/{rel}", now[(t, s, rel)]
            path = overlay_path(t, s, rel)

            def conflict(why, hunks=()):
                rec["conflicts"].append({"file": path, "why": why, "hunks": list(hunks), "digest": digest(ours),
                                         "since": j["since"]})
                took[(t, s, rel)] = why

            def change(kind, text):                    # outside `targets` nothing is written: it did not take it
                took[(t, s, rel)] = True if live else f"{t} was not re-merged in this run (not in --targets)"
                if not live:
                    return
                rec[kind].append(path); now[(t, s, rel)] = text
                if kind == "retired":
                    gone.add((t, s, rel))
                if write and kind == "retired":
                    (repo / path).unlink()
                elif write:
                    _write(repo / path, text)
            if j["hop"]:
                base, theirs = h_old[(anc, key)], h_new[(anc, key)]
            else:
                base = p_old[j["base_ref"]].get(key) if j["o"] else None
                theirs = p_new.get(key) if j["n"] else None
            if theirs is None:
                conflict("the authors deleted the file this overlay replaces")
                continue
            if base is None and _lf(ours) == _lf(theirs):
                gets = None if anc else retire_to(t, s, rel, ours)
                if gets is None:
                    took[(t, s, rel)] = True           # already the authors' file; the tree keeps this copy
                else:
                    change("retired", gets)
                continue
            if base is None:
                conflict("the authors added a file at this overlay's path (it replaced nothing before)")
                continue
            merged, n = merge_file(ours, base, theirs)
            if n:
                conflict(f"{n} conflicting hunk(s)", conflict_hunks(merged))
                continue
            dups = duplicate_members(merged, ours, theirs)
            if dups:
                conflict("merged cleanly into a duplicate declaration (the overlay already had it and the authors added "
                         "it again): " + "; ".join(dups))
                continue
            # retired: the target's tree -- and every later target's that takes the path from this overlay -- gets the
            # same file without it: the prepared authors' file, or the nearest older overlay still there VERBATIM (an
            # inherited overlay is not renamed by this target's rows), or, with inherited_overlay_rows=true, through the
            # rows after it (Preparer.inherited); retire_to() says
            gets = retire_to(t, s, rel, merged)
            if gets is not None:
                change("retired", gets)
            elif _lf(merged) == _lf(ours):
                took[(t, s, rel)] = True               # already carries it (a re-run)
            else:
                change("rewritten", merged)
        if not live:
            continue
        # an overlay t inherits AS IT IS (no copy of its own) is built into t's tree: when the nearest older overlay of
        # that path did not take the authors' change, t did not either -- never skipped
        inherited = {}
        for a in chain:
            for (s, rel) in own_overlays(mv, a):
                if (s, rel) not in own and (s, rel) not in inherited and (a, s, rel) in orig:
                    inherited[(s, rel)] = a
        for (s, rel), a in sorted(inherited.items()):
            apath, why = overlay_path(a, s, rel), took.get((a, s, rel))
            pend = left.get(apath)
            if why is True:
                continue
            if why is None:                            # untouched in this run: an earlier flag stays while unedited
                if pend and (repo / apath).is_file() and pend.get("digest") == digest(_read(repo / apath)):
                    rec["conflicts"].append({"file": apath, "why": pend.get("reason") or INHERITS, "hunks": [],
                                             "digest": pend["digest"], "since": pend.get("since", "")})
                continue
            rec["conflicts"].append({"file": apath, "why": f"{INHERITS} ({a}: {why})", "hunks": [],
                                     "digest": digest(now[(a, s, rel)]), "since": f"{sn(old)}..{sn(new)}"})
    return out


def digest(text):
    """sha256 of the overlay file's bytes (text as _read() returns it: line endings and all), as REMERGE.json records it
    and tools/fork-port.py's target_done() compares it."""
    import hashlib
    return hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest()


INHERITS = "inherits an overlay that did not take the authors' change"


def remerge_file(repo, t):
    return pathlib.Path(repo) / ".multiversion/targets" / t / "REMERGE.json"


def remerge_left(repo, t):
    """{overlay path: entry} an earlier re-merge left unmerged for target t: its REMERGE.json ({"conflicts": [{"overlay",
    "digest", "reason", "since": "<old>..<new>"}]}), else the digests in its RESIDUE.md section. A malformed REMERGE.json
    stops the run: it is a reviewed input, and reading it as empty would forget what it holds."""
    f = remerge_file(repo, t)
    if f.is_file():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            return {c["overlay"]: c for c in data["conflicts"] if c.get("overlay") and c.get("digest")}
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise RuntimeError(f"{f}: not a REMERGE.json ({e}); fix or delete it by hand")
    return {p: {"overlay": p, "digest": d, "reason": "", "since": ""} for p, d in residue_left(repo, t).items()}


def put_remerge(repo, t, rec):
    """Write target t's REMERGE.json from a remerge() record: one entry per overlay that did not take the authors'
    change; the file is deleted when nothing is left."""
    f = remerge_file(repo, t)
    entries = {}
    for c in (rec or {}).get("conflicts", []):
        entries.setdefault(c["file"], {"overlay": c["file"], "digest": c["digest"], "reason": c["why"],
                                       "since": c.get("since", "")})
    if not entries:
        if f.is_file():
            f.unlink()
        return
    text = json.dumps({"conflicts": [entries[k] for k in sorted(entries)]}, indent=1) + "\n"
    if not f.is_file() or f.read_text(encoding="utf-8") != text:
        _write(f, text)


DIGEST_LINE = re.compile(r"^<!-- layer-sync overlay (\S+) digest ([0-9a-f]+) -->$", re.M)


def residue_left(repo, t):
    """{overlay path: digest} that the target's RESIDUE.md says an earlier re-merge left for a person. An overlay whose
    content has changed since is a person's resolution: the re-merge does not touch it again (a hand merge would
    otherwise conflict with the same base forever)."""
    f = pathlib.Path(repo) / ".multiversion/targets" / t / "RESIDUE.md"
    text = f.read_text(encoding="utf-8") if f.is_file() else ""
    i = text.find(RESIDUE_SECTION)
    if i < 0:
        return {}
    j = text.find("\n## ", i + len(RESIDUE_SECTION))
    return dict(DIGEST_LINE.findall(text[i:j if j >= 0 else None]))


def residue_section(t, rec, old, new):
    if not rec or not rec["conflicts"]:
        return ""
    cap = 200
    lines = [RESIDUE_SECTION, "",
             f"The authors' code moved ({old} -> {new}). Each overlay below replaces a file they changed and was re-merged "
             f"three ways (base = their file before, prepared for {t}; ours = the overlay; theirs = their file now, prepared "
             f"for {t}). It did not merge, so it is UNCHANGED in the tree. Carry their change into the overlay by hand (or "
             f"retire the overlay into rename rows), then rerun `python3 tools/layer-sync.py remerge --repo DIR --old {old} "
             f"--new {new} --targets {t}` (it leaves alone an overlay edited since it was listed here, and drops it from "
             f"this list) and `python3 tools/layer-sync.py run --repo DIR --targets {t}`.", ""]
    for c in rec["conflicts"]:
        lines.append(f"### `{c['file']}` -- {c['why']}" + (f" (since {c['since']})" if c.get("since") else ""))
        lines.append(f"<!-- layer-sync overlay {c['file']} digest {c['digest']} -->")
        if c["hunks"]:
            body = "\n\n".join(c["hunks"]).split("\n")
            lines += ["", "```"] + body[:cap] + (["", f"... {len(body) - cap} more line(s)"] if len(body) > cap else []) + ["```"]
        lines.append("")
    return "\n".join(lines)


def put_residue_section(repo, t, section):
    """Replace (or remove) layer-sync's section of .multiversion/targets/<t>/RESIDUE.md; fork-port.py's part stays."""
    f = pathlib.Path(repo) / ".multiversion/targets" / t / "RESIDUE.md"
    text = f.read_text(encoding="utf-8") if f.is_file() else ""
    i = text.find(RESIDUE_SECTION)
    if i >= 0:
        j = text.find("\n## ", i + len(RESIDUE_SECTION))
        before, rest = text[:i].rstrip("\n"), (text[j + 1:] if j >= 0 else "")
        text = before + ("\n\n" if before and rest else "\n" if before else "") + rest
    header = f"# {t}: residue\n"
    if section:
        text = (text.rstrip("\n") + "\n\n" if text.strip() else header + "\n") + section.rstrip("\n") + "\n"
    if not text.strip() or text == header:
        if f.is_file():
            f.unlink()
        return
    if text != (f.read_text(encoding="utf-8") if f.is_file() else None):
        f.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------- commands

def not_a_layer(repo):
    print(f"layer-sync: {repo} is not a multi-version fork (no .multiversion/ with a target): nothing to do, nothing changed")
    return 0


def ref_targets(repo, ref):
    """Layer targets on REF without checking it out (a target is a .multiversion/targets/<t>/target.properties)."""
    r = git(repo, "ls-tree", "-r", "--name-only", ref, "--", ".multiversion/targets")
    if r.returncode:
        return None
    ts = {m.group(1) for m in (re.fullmatch(r"\.multiversion/targets/([^/]+)/target\.properties", l)
                               for l in r.stdout.split()) if m}
    return sorted(ts, key=lambda t: [int(x) if x.isdigit() else x for x in re.split(r"[.]", t)])


def layer_refs(repo):
    """Every local or remote branch that carries a layer target (so a check of the wrong branch cannot pass silently)."""
    rows = git(repo, "for-each-ref", "--format=%(refname:short)\t%(symref)", "refs/heads", "refs/remotes").stdout.splitlines()
    # skip symbolic refs (origin/HEAD, which --format=short prints as a bare "origin"): an alias, not a branch
    return [r for r, _, sym in (l.partition("\t") for l in rows) if r and not sym and ref_targets(repo, r)]


def version_refs(repo):
    """{version: branch} of the fork's version branches (local first, then origin's): a `neoforge-<v>` branch that
    carries .port/<v>/branch.properties (tools/fork-branches.py version_branches)."""
    out = {}
    for pat in ("refs/heads/neoforge-*", "refs/remotes/origin/neoforge-*"):
        for line in git(repo, "for-each-ref", "--format=%(refname:short)", pat).stdout.split():
            v = line.split("neoforge-", 1)[1]
            if v not in out and git(repo, "cat-file", "-e", f"{line}:.port/{v}/branch.properties").returncode == 0:
                out[v] = line
    return out


def cmd_detect(a):
    repo = pathlib.Path(a.repo).resolve()
    vb = version_refs(repo)
    if vb:
        # version branches win over a legacy layer branch: the layer is history and keep-current ignores it
        if a.ref and ref_targets(repo, a.ref) is None:
            print(f"layer-sync: unknown ref {a.ref}")
            return 2
        legacy = sorted(set(layer_refs(repo)) | ({a.ref} if a.ref and ref_targets(repo, a.ref) else set()))
        vs = sorted(vb, key=lambda t: [int(x) if x.isdigit() else x for x in re.split(r"[.]", t)])
        print("layer=false kind=version-branches versions=" + ",".join(vs)
              + (f" legacy_layer={','.join(legacy)}" if legacy else ""))
        if legacy:
            print(f"layer-sync: this fork has version branches (.port/<v>/branch.properties), so it is a version-branch "
                  f"fork: version branches win over the legacy layer branch{'es' if len(legacy) > 1 else ''} "
                  f"{', '.join(legacy)} (.multiversion/), which {'are' if len(legacy) > 1 else 'is'} history and ignored "
                  "-- keep-current takes \"A fork with version branches\", not the layer step")
        return 0
    if a.ref:
        ts = ref_targets(repo, a.ref)
        if ts is None:
            print(f"layer-sync: unknown ref {a.ref}")
            return 2
    else:
        ts = layer_targets(repo)
    behind = ""
    if ts and a.ref and a.port_ref:
        # Is the layer owed a merge? The port branch can move with nothing new upstream (a fix of ours, or a night whose
        # layer step was skipped), so "anything new?" must ask this as well as asking the authors.
        n = git(repo, "rev-list", "--count", f"{a.ref}..{a.port_ref}")
        if n.returncode:
            print(f"layer-sync: unknown ref {a.port_ref}")
            return 2
        behind = f" layer_behind={n.stdout.strip()}"
    print(f"layer={'true' if ts else 'false'}" + (f" targets={','.join(ts)}" if ts else "") + behind)
    if ts or a.ref:
        return 0
    # The checked-out branch has no layer. On a single-version fork no branch has one either, so nothing changes; but
    # a layer fork checked out on its port branch would otherwise read as single-version and skip the layer step.
    others = layer_refs(repo)
    if others:
        print(f"layer-sync: the checked-out branch has no layer, but {', '.join(others)} carr"
              f"{'ies' if len(others) == 1 else 'y'} .multiversion/ -- this fork HAS a layer; rerun with --ref <layer branch>"
              " (the fork's layer_branch in .github/weekly-plan.json)")
        return 3
    return 0


def cmd_merge(a):
    repo = pathlib.Path(a.repo).resolve()
    if not layer_targets(repo):
        return not_a_layer(repo)
    if git(repo, "status", "--porcelain").stdout.strip():
        print("layer-sync: the working tree is not clean -- commit or stash first")
        return 2
    cur = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if a.layer_branch and cur != a.layer_branch:
        print(f"layer-sync: checked out {cur}, not the layer branch {a.layer_branch}")
        return 2
    if git(repo, "rev-parse", "--verify", a.port_ref + "^{commit}").returncode:
        print(f"layer-sync: unknown ref {a.port_ref}")
        return 2
    sha = git(repo, "rev-parse", "--short", a.port_ref).stdout.strip()
    if git(repo, "merge-base", "--is-ancestor", a.port_ref, "HEAD").returncode == 0:
        print(f"layer-sync: {cur} already contains {a.port_ref} ({sha}): nothing to merge")
        return 0
    r = git(repo, "merge", "--no-ff", "-m", f"Merge {a.port_ref} ({sha}) into {cur}: the authors' new code, carried to the layer", a.port_ref)
    if r.returncode:
        files = git(repo, "diff", "--name-only", "--diff-filter=U").stdout.split()
        git(repo, "merge", "--abort")
        print(f"layer-sync: merging {a.port_ref} conflicts in {len(files)} file(s) -- aborted, nothing changed: "
              f"{', '.join(files[:8])}\n  the layer branch differs from the port branch only in .multiversion/ and the "
              "hook line at the end of build.gradle, so a conflict elsewhere means that rule was already broken")
        return 1
    iso = subprocess.run([sys.executable, str(repo / ".multiversion/tools/layer.py"), "isolation", "--base", a.port_ref],
                         cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(iso.stdout.strip())
    print(f"layer-sync: merged {a.port_ref} ({sha}) into {cur} at {git(repo, 'rev-parse', '--short', 'HEAD').stdout.strip()}")
    return 0 if iso.returncode == 0 else 1


class Runner:
    def __init__(self, repo, budget):
        self.repo, self.budget = pathlib.Path(repo), budget
        self.tool = os.environ.get("LAYER_SYNC_FORK_PORT") or str(HERE / "fork-port.py")

    def call(self, t, frm, to):
        r = subprocess.run([sys.executable, self.tool, "--repo", str(self.repo), "--target", t, "--budget", str(self.budget),
                            "--gates", "none", "--from-stage", frm, "--to-stage", to],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return r.returncode, r.stdout

    def summary(self, t):
        p = self.repo / "build" / f"mc{t}" / "fork-port" / "fork-port.json"
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"stages": {}}


def short(repo, ref):
    return git(repo, "rev-parse", "--short", ref).stdout.strip() or ref


def residue_of(repo, t):
    f = pathlib.Path(repo) / ".multiversion/targets" / t / "RESIDUE.md"
    return f".multiversion/targets/{t}/RESIDUE.md" if f.is_file() else ""


def first_stop(summ):
    st = summ.get("stop") or {}
    return f"{st.get('stage')}: {st.get('left')}" if st else ""


def run_target(repo, t, runner):
    rec = {"target": t, "before": None, "after": None, "class": "rework", "residue": "", "note": ""}
    rc, out = runner.call(t, "stage", "measure")
    s = runner.summary(t)
    meas = s.get("stages", {}).get("measure", {})
    if rc != 0 or not isinstance(meas.get("errors"), int):
        rec["note"] = first_stop(s) or f"fork-port exit {rc}"
        rec["residue"] = residue_of(repo, t)
        return rec
    rec["before"] = meas["errors"]
    if rec["before"] == 0:
        rc, out = runner.call(t, "audit", "audit")
        s = runner.summary(t)
        audit_ok = rc == 0
        rec["after"] = 0
        rec["class"] = classify(0, audit_ok, 0, audit_ok, not audit_ok and bool(first_stop(s) or rc))
        if rec["class"] == "rework":
            rec["note"] = first_stop(s) or f"audit exit {rc}"
            rec["residue"] = residue_of(repo, t)
        return rec
    rc, out = runner.call(t, "loop", "audit")
    s = runner.summary(t)
    loop = s.get("stages", {}).get("loop", {})
    rec["after"] = loop.get("errors") if isinstance(loop.get("errors"), int) else (0 if rc == 0 else None)
    rec["class"] = classify(rec["before"], rc == 0, rec["after"], rc == 0, rc != 0 and rec["after"] == 0)
    if rec["class"] == "rework":
        rec["note"] = first_stop(s) or f"fork-port exit {rc}"
        rec["residue"] = residue_of(repo, t)
    return rec


def default_changed_from(repo):
    """The first parent of a merge HEAD (what the layer branch was before `merge`), else None."""
    r = git(repo, "rev-parse", "--verify", "HEAD^2")
    return "HEAD^1" if r.returncode == 0 else None


def cmd_run(a):
    repo = pathlib.Path(a.repo).resolve()
    ts = layer_targets(repo)
    if not ts:
        return not_a_layer(repo)
    want = [x for x in a.targets.split(",") if x] if a.targets else ts
    unknown = [x for x in want if x not in ts]
    if unknown:
        print(f"layer-sync: no such target(s): {', '.join(unknown)} (the layer has {', '.join(ts)})")
        return 2
    base = a.changed_from or default_changed_from(repo)
    changed = git(repo, "diff", "--name-only", base, "HEAD").stdout.split() if base else []
    runner = Runner(repo, a.budget)
    prep = Preparer(repo)
    # the authors' code moved (base -> HEAD): carry it into every overlay of a file it touched BEFORE measuring, so the
    # compile sees the re-merged overlays; what did not merge stays as it was and makes its target rework
    merged = remerge(repo, base, "HEAD", want, write=True, prep=prep) if base and changed else {}
    for t, rm in merged.items():
        if rm["rewritten"] or rm["retired"] or rm["conflicts"]:
            print(f"layer-sync: {t}: overlays re-merged with the authors' change: {len(rm['rewritten'])} rewritten, "
                  f"{len(rm['retired'])} retired, {len(rm['conflicts'])} did not merge", flush=True)
    rows = []
    for t in [x for x in ts if x in want]:
        print(f"layer-sync: {t} ...", flush=True)
        rec = run_target(repo, t, runner)
        rec["shadowed"] = shadowed(repo / ".multiversion", t, changed)
        rm = merged.get(t)
        if rm and (rm["rewritten"] or rm["retired"] or rm["conflicts"]):
            rec["remerge"] = rm
            if rm["conflicts"]:
                rec["class"] = "rework"
                left = f"{len(rm['conflicts'])} overlay(s) did not merge the authors' change"
                rec["note"] = f"{rec['note']}; {left}" if rec["note"] else left
            elif rec["class"] == "untouched":
                rec["class"] = "touch-up"             # the overlays changed, even though nothing failed to compile
        if t in merged:          # the re-merge ran: its REMERGE.json and RESIDUE.md section say what it left
            put_remerge(repo, t, rm)
            put_residue_section(repo, t, residue_section(t, rm, short(repo, base), short(repo, "HEAD")))
            if rec["class"] == "rework":
                rec["residue"] = residue_of(repo, t)
        rec["overlays"] = overlay_report(repo, t, prep)
        if rec["overlays"]["over"]:
            print(f"layer-sync: WARNING {t}: {overlay_counts(rec['overlays'])}: the whole-file ones over the overlay budget "
                  f"of {rec['overlays']['budget']} (.multiversion/ci.properties overlay_budget)", flush=True)
        print(f"layer-sync: {t}: {rec['before']} -> {rec['after']} errors, {rec['class']}", flush=True)
        rows.append(rec)
    head = git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
    md = table(rows)
    data = {"head": head, "changed_from": base, "targets": rows}
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(data, indent=1), encoding="utf-8")
    if a.md:
        pathlib.Path(a.md).write_text(md + "\n", encoding="utf-8")
    print("\n" + md)
    stop = stopped_block(rows)
    if stop:
        print("\n" + stop)
        return 10
    return 0


def _want(repo, a):
    ts = layer_targets(repo)
    want = [x for x in (a.targets or "").split(",") if x] or ts
    bad = [x for x in want if x not in ts]
    if bad:
        print(f"layer-sync: no such target(s): {', '.join(bad)} (the layer has {', '.join(ts)})")
        return None
    return want


def cmd_overlays(a):
    repo = pathlib.Path(a.repo).resolve()
    if not layer_targets(repo):
        return not_a_layer(repo)
    want = _want(repo, a)
    if want is None:
        return 2
    prep = Preparer(repo)
    reps = [overlay_report(repo, t, prep) for t in want]
    print("| target | whole-file overlays | lines | real | mechanical | renamed copy | redundant | mixin | client | "
          "overlay-only files | budget |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for o in reps:
        print(f"| {o['target']} | {o['overlays']} | {o['lines']} | {o['real']} | {o['mechanical']} | {o['renamed_copy']} | "
              f"{o['redundant']} | "
              f"{o['mixin']} | {o['client']} | {o['overlay_only']} | {o['budget']}{' OVER' if o['over'] else ''} |")
    for o in reps:
        for k, what in (("redundant", "identical to what it replaces: delete it"),
                        ("mechanical", "differs only in names: make it rename rows and delete it")):
            fs = [x["file"] for x in o["files"] if x["kind"] == k]
            if fs:
                print(f"\n{o['target']}: {len(fs)} {k} overlay(s) -- {what}:\n  " + "\n  ".join(fs))
        if o["over"]:
            print(f"\nWARNING {o['target']}: {overlay_counts(o)}: the whole-file ones over the overlay budget of {o['budget']} "
                  "(default %d; .multiversion/ci.properties overlay_budget=N)" % DEFAULT_OVERLAY_BUDGET)
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(reps, indent=1), encoding="utf-8")
    return 0


def cmd_remerge(a):
    repo = pathlib.Path(a.repo).resolve()
    if not layer_targets(repo):
        return not_a_layer(repo)
    want = _want(repo, a)
    if want is None:
        return 2
    for ref in (a.old, a.new):
        if git(repo, "rev-parse", "--verify", ref + "^{commit}").returncode:
            print(f"layer-sync: unknown ref {ref}")
            return 2
    res = remerge(repo, a.old, a.new, want, write=not a.dry_run)
    if not a.dry_run:
        for t in want:
            put_remerge(repo, t, res.get(t))
            put_residue_section(repo, t, residue_section(t, res.get(t), short(repo, a.old), short(repo, a.new)))
    print("| target | overlays of changed files | rewritten | retired | resolved by hand | did not merge |")
    print("|---|---|---|---|---|---|")
    for t in want:
        r = res[t]
        print(f"| {t} | {r['checked']} | {len(r['rewritten'])} | {len(r['retired'])} | {len(r['resolved'])} | "
              f"{len(r['conflicts'])} |")
    for t in want:
        for c in res[t]["conflicts"]:
            print(f"  {t}: {c['file']}: {c['why']}")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(res, indent=1), encoding="utf-8")
    n = sum(len(r["conflicts"]) for r in res.values())
    print(f"layer-sync: {'dry run, nothing written; ' if a.dry_run else ''}"
          + (f"{n} overlay(s) did not merge -- a person's (REMERGE.json, RESIDUE.md)" if n else "every overlay merged"))
    return 10 if n else 0


def cmd_ledger(a):
    led_path = pathlib.Path(a.ledger)
    led = json.loads(led_path.read_text(encoding="utf-8"))
    data = json.loads(pathlib.Path(a.json).read_text(encoding="utf-8"))
    led["layer"] = {"column": a.column, "targets": [
        {k: r.get(k) for k in ("target", "before", "after", "class", "residue")} for r in data["targets"]]}
    led_path.write_text(json.dumps(led, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"layer-sync: {len(data['targets'])} target(s) written into {led_path} under column {a.column}")
    return 0


# ---------------------------------------------------------------- self-check

def self_check():
    ok = True

    def expect(c, msg):
        nonlocal ok
        if not c:
            ok = False; print("FAIL:", msg)
    expect(classify(0, True, 0, True, False) == "untouched", "0 errors, audits clean: untouched")
    expect(classify(7, None, 0, True, False) == "touch-up", "errors cleared by the loop: touch-up")
    expect(classify(7, None, 3, False, True) == "rework", "errors left: rework")
    expect(classify(0, False, 0, False, True) == "rework", "a failing audit at 0 errors: rework")
    expect(classify(None, None, None, None, True) == "rework", "a stage that would not run: rework")
    expect(classify(7, None, 0, False, False) == "rework", "loop cleared the errors but the audit is red: rework")
    with tempfile.TemporaryDirectory() as d:
        mv = pathlib.Path(d)
        for t, inh in (("1.21.4", None), ("1.21.5", "1.21.4")):
            (mv / "targets" / t).mkdir(parents=True)
            (mv / "targets" / t / "target.properties").write_text(f"inherits={inh}\n" if inh else "x=1\n", encoding="utf-8")
        (mv / "targets/1.21.4/java/net/m").mkdir(parents=True)
        (mv / "targets/1.21.4/java/net/m/A.java").write_text("x", encoding="utf-8")
        (mv / "targets/1.21.5/java/net/m").mkdir(parents=True)
        (mv / "targets/1.21.5/java/net/m/B.java").write_text("x", encoding="utf-8")
        ch = ["src/main/java/net/m/A.java", "src/main/java/net/m/B.java", "src/main/java/net/m/C.java", "README.md"]
        expect(shadowed(mv, "1.21.4", ch) == ["src/main/java/net/m/A.java"], "own overlay only")
        expect(shadowed(mv, "1.21.5", ch) == ["src/main/java/net/m/A.java", "src/main/java/net/m/B.java"], "inherited overlays count")
    rows = [{"target": "1.21.4", "before": 0, "after": 0, "class": "untouched", "residue": "", "note": "", "shadowed": []},
            {"target": "1.21.8", "before": 12, "after": 3, "class": "rework", "residue": ".multiversion/targets/1.21.8/RESIDUE.md",
             "note": "loop: 3 compile errors", "shadowed": ["src/main/java/net/m/A.java"]}]
    t = table(rows)
    expect("| 1.21.4 | 0 | 0 | untouched | - |" in t and "| 1.21.8 | 12 | 3 | rework | `.multiversion/targets/1.21.8/RESIDUE.md` |" in t
           and "overlays 1 file(s)" in t, "table")
    expect("STOPPED for a person" in stopped_block(rows) and stopped_block(rows[:1]) == "", "stop block only for rework")
    expect("re-merged" not in t and "overlay budget" not in t, "no overlay note for rows without overlays (output as before)")
    # R6: the overlay helpers
    ref = "class A {\n    Foo x = a.old();\n}\n"
    expect(rename_only(ref, ref.replace("Foo", "Bar").replace("a.old", "a.renamed")), "names swapped only: mechanical")
    expect(not rename_only(ref, ref.replace("a.old()", "a.old(1)")), "an argument added: real")
    expect(not rename_only(ref, ref.replace("Foo x", "Foo x, y")), "a token added: real")
    expect(not rename_only(ref, ref.replace("class", "interface")), "a keyword changed: real")
    expect(not rename_only(ref, ref), "identical is redundant, not mechanical")
    base = "class A {\n  int a;\n  int b;\n  int c;\n  int d;\n}\n"
    m, n = merge_file(base.replace("int a;", "long a;"), base, base.replace("int d;", "long d;"))
    expect(n == 0 and "long a;" in m and "long d;" in m, "merge_file: a clean three-way merge")
    m, n = merge_file(base.replace("int a;", "long a;").replace("\n", "\r\n"), base, base.replace("int d;", "long d;"))
    expect(n == 0 and m.count("\r\n") == m.count("\n"), "merge_file keeps the overlay's CRLF")
    m, n = merge_file(base.replace("int a;", "long a;"), base, base.replace("int a;", "short a;"))
    h = conflict_hunks(m)
    expect(n == 1 and len(h) == 1 and "long a;" in h[0] and "short a;" in h[0] and h[0].startswith("<<<<<<< overlay"),
           "merge_file: a conflict, and its hunk")
    ours = base.replace("{\n", "{\n  public void x() {}\n", 1)
    theirs = base.replace("int d;\n", "int d;\n  public void x() {}\n")
    expect(duplicate_members(ours.replace("int d;\n", "int d;\n  public void x() {}\n"), ours, theirs) == ["method x()"],
           "a member both sides added at different places: duplicate")
    expect(duplicate_members(ours, ours, theirs) == [], "no duplicate")
    expect(duplicate_members("  @Override\n  @Override\n  public void y() {}\n", "  @Override\n  public void y() {}\n", "")
           == ["annotation @Override twice in a row"], "a doubled annotation")
    expect(duplicate_members("  private int f = 1;\n  private int f = 2;\n", "  private int f = 1;\n", "  private int f = 2;\n")
           == ["field f"], "a field declared twice")
    with tempfile.TemporaryDirectory() as d:
        (pathlib.Path(d) / ".multiversion/targets/1.21.4").mkdir(parents=True)
        res = pathlib.Path(d) / ".multiversion/targets/1.21.4/RESIDUE.md"
        res.write_text("# Residue\n\n## Compile errors\nx\n", encoding="utf-8")
        rec = {"conflicts": [{"file": "F.java", "why": "1 conflicting hunk(s)", "hunks": ["<<<<<<< overlay\na\n>>>>>>> b"],
                              "digest": "abc123"}]}
        put_residue_section(d, "1.21.4", residue_section("1.21.4", rec, "o1", "n1"))
        put_residue_section(d, "1.21.4", residue_section("1.21.4", rec, "o1", "n1"))
        txt = res.read_text(encoding="utf-8")
        expect(txt.count(RESIDUE_SECTION) == 1 and "## Compile errors\nx" in txt and residue_left(d, "1.21.4") == {"F.java": "abc123"},
               f"RESIDUE.md: one section, fork-port's part kept, the digest read back: {txt}")
        put_residue_section(d, "1.21.4", "")
        expect(res.read_text(encoding="utf-8") == "# Residue\n\n## Compile errors\nx\n", "the section removed, the rest as it was")
        res.unlink()
        put_residue_section(d, "1.21.4", residue_section("1.21.4", rec, "o1", "n1"))
        put_residue_section(d, "1.21.4", "")
        expect(not res.exists(), "a RESIDUE.md that held only the section is removed with it")
        # REMERGE.json: the reviewed record tools/fork-port.py's target_done() reads
        rj = remerge_file(d, "1.21.4")
        two = {"conflicts": [dict(rec["conflicts"][0], since="o1..n1"),
                             {"file": "A.java", "why": INHERITS + " (1.21.3: x)", "hunks": [], "digest": "d2", "since": "o1..n1"}]}
        put_remerge(d, "1.21.4", two)
        got = json.loads(rj.read_text(encoding="utf-8"))
        expect(got == {"conflicts": [{"overlay": "A.java", "digest": "d2", "reason": INHERITS + " (1.21.3: x)", "since": "o1..n1"},
                                     {"overlay": "F.java", "digest": "abc123", "reason": "1 conflicting hunk(s)", "since": "o1..n1"}]}
               and set(remerge_left(d, "1.21.4")) == {"A.java", "F.java"}, f"REMERGE.json: one sorted entry per overlay: {got}")
        put_remerge(d, "1.21.4", {"conflicts": []})
        expect(not rj.exists(), "an empty REMERGE.json is deleted")
        rj.write_text("{", encoding="utf-8")
        try:
            remerge_left(d, "1.21.4"); expect(False, "a malformed REMERGE.json is not read as empty")
        except RuntimeError:
            pass
        expect(digest("a\r\nb") == __import__("hashlib").sha256(b"a\r\nb").hexdigest(), "digest: sha256 of the file's bytes")
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    if "--self-check" in sys.argv:
        return self_check()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1] if __doc__ else "")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("detect"); p.add_argument("--repo", default=".")
    p.add_argument("--ref", help="read the layer from this branch/ref (the fork's layer_branch) without checking it out")
    p.add_argument("--port-ref", help="with --ref: also print layer_behind=N, the port-branch commits the layer has not merged")
    p = sub.add_parser("merge"); p.add_argument("--repo", default="."); p.add_argument("--port-ref", required=True)
    p.add_argument("--layer-branch")
    p = sub.add_parser("run"); p.add_argument("--repo", default="."); p.add_argument("--targets")
    p.add_argument("--budget", type=float, default=15.0); p.add_argument("--changed-from")
    p.add_argument("--json"); p.add_argument("--md")
    p = sub.add_parser("overlays"); p.add_argument("--repo", default="."); p.add_argument("--targets", "--target")
    p.add_argument("--json")
    p = sub.add_parser("remerge"); p.add_argument("--repo", default="."); p.add_argument("--old", required=True)
    p.add_argument("--new", default="HEAD"); p.add_argument("--targets", "--target"); p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json")
    p = sub.add_parser("ledger"); p.add_argument("--ledger", required=True); p.add_argument("--json", required=True)
    p.add_argument("--column", required=True)
    a = ap.parse_args()
    return {"detect": cmd_detect, "merge": cmd_merge, "run": cmd_run, "overlays": cmd_overlays, "remerge": cmd_remerge,
            "ledger": cmd_ledger}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
