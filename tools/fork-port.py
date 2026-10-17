#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Port one fork-layer target in one command, and hand back only the residue.

    python3 tools/fork-port.py --repo DIR --target 1.21.5 [--budget 15] [--gates auto|none|ab|full]
                               [--json out.json] [--from-stage <name>] [--base REF] [--modid ID] [--direct]
                               [--port-checkout owner/repo=DIR] [--port-release owner/repo@TAG]
                               [--max-errors N] [--confirm-spend] [--allow-undone-parent] [--allow-undone-library]
                               [--upstream REMOTE/BRANCH] [--accept-upstream-drift] [--self-check]
    python3 tools/fork-port.py --repo DIR --target <t> --check-done
    python3 tools/fork-port.py --repo DIR --target <t> --write-test-items
    python3 tools/fork-port.py --repo DIR --chain FROM..TO [--from-stage gates] [--gates auto|ab|full]

The fork-versions skill's procedure (steps 1-9) as stages, the way tools/run-port.py is the jar-port procedure.
The orchestrating session starts this and then reads ONE summary (the last lines of stdout, the --json file, and
.multiversion/targets/<t>/RESIDUE.md when it stopped) -- never the workers' transcripts or the build logs.

Stages, each logged as one line and skipped with a reason when it does not apply:
  setup    layer-inventory.py build first when .multiversion/INVENTORY.json is missing (once per layer: the base build's
           behaviour inventory the done list accounts for), then
           layer-deps.py resolve --dry-run, when the authors' build pins dependencies (a required one with no
           build for the target stops here, exit 15, before anything is written; a sibling library whose target is not
           done stops it with exit 18 and layer-deps' reason -- --allow-undone-library, development only, is passed
           through, recorded in DEPS.md and the summary, and kept in the resume command; --port-checkout passes a sibling
           layer fork's checkout for its per-target jar, --port-release its release, pinned for CI), then
           make-fork-layer.py add-target (when the target dir is missing; --direct passes `add-target --direct`: a jump
           that inherits nothing but still takes the shared library of every targeted version in between, see the
           fork-versions skill) + sync-shared + layer-deps.py resolve (the target's own build of each dependency into
           target.properties, the drops into DEPS.md); installs layer.py's support for the generated moves table and for
           shared_chain when the fork's copy is a verbatim older copy of the template
  stage    one `.multiversion/build.sh <t> compileJava` to stage Minecraft; while createMinecraftArtifacts fails
           on the access transformer, fix-access-transformer.py --overrides-from <log> on the TARGET's own AT
           (bounded: 4 rounds)
  moves    build-class-move-map.py between the target this one inherits (or the base) and this one, from the
           staged merged jars -> .multiversion/targets/<t>/moves.generated.tsv, which layer.py reads as one more
           rename table. Generated per fork per target from Mojang's data, so not a map anyone commits upstream.
  measure  compile every source set (srcsets.compile_tasks); burndown-count.sh decides whether it is a count
  loop     file-loop.py --layer <t> --budget N (its kept rows land in the target's renames.tsv)
  audit    audit-mixin-targets.py against the target's sources jar and prepared tree, then audit-event-listeners.py (abstract
           events) over every prepared tree against the target's resolved classpath (only at 0 errors)
  gates    ci-gates.py --mc <t>: none = skip; ab = author build, Gate A, Gate B; full = + Gate C phases. After a green run, the
           mod's own log files (harness/run/logs, minus vanilla's) are compared with the inherited target's last gate run: a file
           it wrote there and not here is residue (exit 11) -- a subsystem stopped running (CATALOG X47)
  cost     port-cost.py --target <t> appended to targets/<t>/COST.md: only the requests that worked on <t> (one target per
           request, by what its tool calls touched; shared work on its own line), from where the target's last record ended
           (no record: all of them on this machine), plus the loop workers' own recorded dollars when the loop ran

THE STOP CONTRACT. When a stage cannot finish this does not hand the rest to anyone silently: it prints a
STOPPED block and writes the RESIDUE REPORT (into --json and .multiversion/targets/<t>/RESIDUE.md): the
remaining errors grouped by CATALOG entry / family (not the raw log), the loop's escalation record, audit
mismatches or failing gates with their one-line reason, and the command to resume (`--from-stage <stage>`).
Whoever picks it up fixes what RESIDUE.md lists, and puts each fix in the shared library
(templates/fork-layer/shared/<t>) or a converter -- not in one fork.

ONE RUN PER CHECKOUT (CATALOG X44). The run takes build/.fork-port.lock (pid, stale-pid aware) for its whole length and a
second run on the same checkout is refused at once with a message naming the holder; its own children (ci-gates.py)
are let through. A lock whose process is gone is taken over. tools/layer-ci.py CheckoutLock is the code.

NO RESIDENT GRADLE (issue #122). Every Gradle the run starts (its own, and its children's: layer-deps, file-loop,
ci-gates) runs with -Dorg.gradle.daemon=false added to GRADLE_OPTS (unless GRADLE_OPTS already sets org.gradle.daemon),
so none of the run's daemons is still resident when Gate C's free-memory guard measures. Other checkouts' daemons are
never stopped. When Gate C is still refused (or killed) for memory, RESIDUE.md lists the processes holding the most
resident memory (PID, RSS, command).

A RESUME RE-MEASURES. `--from-stage S` keeps the earlier stages' results for the summary but never their numbers: the
compile count the loop, audit and gates rely on is always one this run measured (a stale 0 once skipped the audit and
every gate and reported the target done).

DONE IS ONE FIXED LIST (target_done(), the fork-versions skill's "Done"): a green Gate C in GATES.md, no unreviewed
STUBS.json entry, REVIEW.md complete for the commit it names (an independent fresh-context reviewer's verdict + note on
every STUBS.json entry, every whole-file overlay, each group of its resource overlays and its renames.tsv -- the last
two with a digest of their files, so an edit is a new row -- with reviewer, date and commit; nothing in the target's
directory changed since, its run outputs and the reviewer's BEHAVIOUR.json aside), TEST-ITEMS.md (the target's
manual-test items, from tools/layer-test-plan.py, written by a run to the end or alone by `--write-test-items`), every item of .multiversion/INVENTORY.json accounted for on the target (tools/layer-inventory.py, its account in
the target's INVENTORY-CHECK.json -- a run output like GATES.md, never "changed since the review"), and no dependency
resolved against an undone library target (--allow-undone-library in DEPS.md). A run whose gates are green but that lacks the rest exits 18 and adds a `?` row to REVIEW.md for every item without
one (and an `item` row for each inventory item left unaccounted); `--check-done` only asks. A NEW target is refused (exit 18, before anything is written; make-fork-layer.py
add-target refuses it too) while the target it stands on is not done; `--allow-undone-parent` is the development escape,
recorded as allowed_undone_parent= in the new target's target.properties, and that target is not done until its parent is.

THE UPSTREAM FREEZE. .multiversion/base.properties records the authors' branch (upstream=), the commit the buildout
started from (upstream_commit=, the merge-base of HEAD and that branch) and each sibling library's release
(library.<owner>/<repo>=<tag>, from --port-release). make-fork-layer.py init --upstream / freeze writes it; a run that adds
the layer's first target writes it when it is missing. Every run to the end fetches the branch and stops (exit 19) when it
holds commits the checkout lacks, or when --port-release names another release of a recorded library, naming the
keep-current command; --accept-upstream-drift continues and appends the drift to .multiversion/DRIFT.md. A --to-stage
run (layer-sync.py's measuring) is not checked.

THE SWEEP. `--chain FROM..TO --from-stage S` (default gates) runs this tool for every target from FROM up TO's inherits
chain, oldest first, from stage S with no model (--budget 0 --max-errors 0), checks the upstream once, stops at the first
red target (any exit but 0, 17, 18) naming its RESIDUE.md and the command that sweeps on, and prints the table. A bug
class found late is fixed at the lowest target where it exists, then swept up; one fixer only where the sweep goes red.

Exit codes: 0 done; 10 stopped before/in the compile (stage, moves, measure, loop); 11 Gate A/B (or the author's
build) red; 12 Gate C red; 13 mixin / event-listener audit; 14 the checkout is in use by another run; 15 a required dependency has no build for
the target (DEPS.md / the STOPPED block name it); 17 every stage passed but the target has no green Gate C recorded
(GATES.md), or the gates were not run (--gates none): A/B green is not done -- a version is done only after its
real-client run. A full gate run that is not green removes GATES.md. Every run to the end (from any --from-stage)
stops with exit 10 while STUBS.json holds an unreviewed stub signal; 18 Gate C green but not done (REVIEW.md /
TEST-ITEMS.md / the behaviour inventory / an undone parent or library, listed), a new target over an undone parent, or
a sibling library's target that is not done (layer-deps.py resolve's exit 18); 19 the authors' branch or a recorded
library release moved since the buildout's freeze; 2 bad input.
Standard library only.
"""
import argparse, datetime, hashlib, importlib.util, json, os, pathlib, re, shutil, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from gitbash import BASH   # noqa: E402

STAGES = ["setup", "stage", "moves", "measure", "loop", "audit", "gates", "cost"]
STAGE_ROUNDS = 4
INSERT = ('            m = MV / "targets" / t / "moves.generated.tsv"   # tools/fork-port.py: derived from the staged jars\n'
          '            if m.exists():\n'
          '                rows.append(f"# ---- moves {t} (generated)\\n" + m.read_text(encoding="utf-8"))\n')
ANCHOR = ('            if r.exists():\n'
          '                rows.append(f"# ---- {t}\\n" + r.read_text(encoding="utf-8"))\n')


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / file)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------- pure helpers (covered by --self-check)

def gate_evidence(text, limit=4):
    """The first few concrete reasons a gate log is red: javac `file:line: error: message` lines (path shortened to
    its last three parts), else failing JUnit/GameTest lines. [] when the log says neither."""
    out, seen = [], set()
    for m in re.finditer(r"(?m)^\s*(\S+\.java):(\d+): error: (.+)$", text):
        key = (m.group(1), m.group(2))
        if key in seen:
            continue
        seen.add(key)
        out.append("%s:%s: %s" % ("/".join(m.group(1).split("/")[-3:]), m.group(2), m.group(3).strip()))
        if len(out) >= limit:
            return out
    if out:
        return out
    for m in re.finditer(r"(?m)^.*(?:FAILED|failed after|Test .* failed|Unregistered holder|Missing test).*$", text):
        out.append(m.group(0).strip()[:200])
        if len(out) >= limit:
            break
    return out


def mod_log_files(d):
    """The log files the MOD wrote in a Gate B run's logs/ directory: every regular file except vanilla's own
    latest.log / debug.log, the dated archives log4j rolls them into (2026-10-12-1.log.gz), and crash dumps.
    set() when the directory is absent."""
    d = pathlib.Path(d)
    if not d.is_dir():
        return set()
    out = set()
    for f in d.iterdir():
        n = f.name
        if not f.is_file() or n in ("latest.log", "debug.log") or n.endswith(".gz"):
            continue
        if re.match(r"^\d{4}-\d{2}-\d{2}-\d+\.log$", n) or n.startswith("crash-"):
            continue
        out.add(n)
    return out


def lost_log_files(prev_dir, cur_dir, known_absent=()):
    """Log files the mod wrote on the previous target and did not write on this one (CATALOG X47), minus the ones
    the fork has recorded as deliberately gone. [] when there is no previous run to compare with."""
    if prev_dir is None or not pathlib.Path(prev_dir).is_dir():
        return []
    return sorted(mod_log_files(prev_dir) - mod_log_files(cur_dir) - set(known_absent))


def select_stages(from_stage, to_stage=None):
    """(skipped, to_run) for --from-stage, and --to-stage (stop after that stage: tools/layer-sync.py measures a
    target with `--from-stage stage --to-stage measure` and audits it with `--from-stage audit --to-stage audit`)."""
    if from_stage is None:
        skipped, run = [], list(STAGES)
    elif from_stage not in STAGES:
        raise ValueError(f"unknown stage {from_stage!r}; stages: {', '.join(STAGES)}")
    else:
        i = STAGES.index(from_stage)
        skipped, run = STAGES[:i], STAGES[i:]
    if to_stage is not None:
        if to_stage not in STAGES:
            raise ValueError(f"unknown stage {to_stage!r}; stages: {', '.join(STAGES)}")
        if STAGES.index(to_stage) < STAGES.index(run[0]):
            raise ValueError(f"--to-stage {to_stage} comes before --from-stage {from_stage}")
        run = run[:STAGES.index(to_stage) - STAGES.index(run[0]) + 1]
    return skipped, run


def read_props(path):
    out = {}
    p = pathlib.Path(path)
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def chain(mv, target):
    out, t = [], target
    while t and t not in out:
        out.append(t)
        t = read_props(mv / "targets" / t / "target.properties").get("inherits")
    return out   # nearest first


RECOUNT_MARK = "# --- the capped compile hit the cap; uncapped recount (portFullErrors) ---"


def hit_cap(text):
    """Did the Gradle-integrated compile stop at tools/maxerrs.init.gradle's cap (javac printing '1,000 errors')?"""
    cap = load("port_gates", "port_gates.py").GRADLE_ERROR_CAP
    return re.search(r"(?m)^\s*%s errors\s*$" % format(cap, ",").replace(",", ",?"), text) is not None


def capped_tasks(text):
    """The compile tasks whose javac stopped at the cap: each '1,000 errors' line belongs to the last
    '> Task :compile...Java' before it. Only those are recounted -- recounting a task whose dependency (main's
    compileJava, for a source set built on main) failed would not run at all."""
    cap = load("port_gates", "port_gates.py").GRADLE_ERROR_CAP
    out, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^> Task :(compile\w*Java)\b", line)
        if m:
            cur = m.group(1)
        elif re.match(r"^%s errors\s*$" % format(cap, ",").replace(",", ",?"), line.strip()) and cur and cur not in out:
            out.append(cur)
    return out


def mc_key(v):
    return tuple(int(x) if x.isdigit() else 0 for x in re.findall(r"\d+|[A-Za-z]+", v))


def shared_chain(mv, target):
    """The targets whose shared library applies to `target`, newest first: its inherits chain, plus every version named by
    a `shared_chain=` key in that chain (a direct jump). The same rule as templates/fork-layer/tools/layer.py
    shared_chain(), checked against it in --self-check; without the key it is chain()."""
    names = chain(mv, target)
    extra = []
    for x in names:
        extra += [s.strip() for s in read_props(mv / "targets" / x / "target.properties").get("shared_chain", "").split(",") if s.strip()]
    return sorted(set(names) | set(extra), key=mc_key, reverse=True) if extra else names


def drop_harvested_rows(mv, target):
    """Rows in targets/<t>/renames.tsv that a shared library the target applies now carries word for word (a row
    harvested into templates/fork-layer/shared, fork-versions step 8). Left in both places they are a duplicate rule,
    which layer.py refuses at table load. Removes them (with a `# X<n>` comment line right above, if it is the loop's).
    -> the rows removed."""
    table = mv / "targets" / target / "renames.tsv"
    if not table.is_file():
        return []
    shared = set()
    for v in shared_chain(mv, target):
        f = mv / "shared" / v / "renames.tsv"
        if f.is_file():
            shared |= {ln.strip() for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")}
    regexes = []
    for r in shared:
        if r.startswith("re:") and "\t" in r:
            pat, rep = r[3:].split("\t", 1)
            try:
                regexes.append((re.compile(pat), rep))
            except re.error:
                pass

    def subsumed(row):
        """A plain target row whose `to` a shared regex already makes of its `from`: both would apply, and the second
        rewrites the first one's result (measured: isClientSide()() from a target row plus the shared row that replaced it)."""
        if row.startswith(("re:", "member:")) or "\t" not in row:
            return False
        frm, to = row.split("\t", 1)
        return any(rx.sub(rep, frm) == to for rx, rep in regexes if rx.search(frm))
    lines, out, gone = table.read_text(encoding="utf-8").splitlines(), [], []
    for ln in lines:
        if ln.strip() and not ln.startswith("#") and (ln.strip() in shared or subsumed(ln.strip())):
            gone.append(ln)
            if out and out[-1].startswith("# X") and "proposed by a file-loop worker" in out[-1]:
                out.pop()
            continue
        out.append(ln)
    if gone:
        table.write_text("\n".join(out) + "\n", encoding="utf-8")
    return gone


def gates_auto(tdir):
    """`--gates auto`: "full" until targets/<t>/GATES.md records a green Gate C (a PASS row naming Gate C, no FAIL row),
    then "ab". Every Minecraft version a fork supports gets one real-client run on its initial port."""
    g = tdir / "GATES.md"
    text = g.read_text(encoding="utf-8", errors="replace") if g.is_file() else ""
    rows = [l for l in text.splitlines() if l.startswith("|")]
    green_c = any("Gate C" in l and "| PASS |" in l for l in rows) and not any("| FAIL |" in l for l in rows)
    return "ab" if green_c else "full"


def drop_shadowing_overlays(mv, target):
    """Overlays in targets/<t>/java that are byte-for-byte a shared helper the target applies (the same file name in a
    shared/<v>/java tree of its shared_chain): written while the shared library was not synced yet, they then SHADOW it,
    and a later fix to the shared helper never reaches this fork. -> the overlays removed."""
    tj = mv / "targets" / target / "java"
    if not tj.is_dir():
        return []
    shared = {}
    for v in shared_chain(mv, target):
        for f in (mv / "shared" / v / "java").rglob("*.java") if (mv / "shared" / v / "java").is_dir() else []:
            shared.setdefault(f.relative_to(mv / "shared" / v / "java").as_posix(), f.read_bytes())
    gone = []
    for rel, data in shared.items():
        ov = tj / rel
        if ov.is_file() and ov.read_bytes() == data:
            ov.unlink(); gone.append(rel)
    return gone


def layer_client_items(repo, tdir, target):
    """1.21.2+ binds an item to its model through assets/<ns>/items/<id>.json; without one an item is the magenta cube,
    and no gate before Gate C sees it (CATALOG V42b). Write the missing definitions into the TARGET's resource overlay
    (never the authors' tree) with the multi-version generator era-hop.py uses: it skips parent templates and counts
    layers. A definition the overlay or the authors already have (a special renderer's) is never touched. A spawn egg
    gets its two 1.21.1 colours as constant tints (read from the authors' registration call: DeferredSpawnEggItem is
    gone, so nothing else colours it), and a model's 1.21.1 `overrides` become the select/condition that picks the
    same variant (trim_type -> minecraft:trim_material; a mod predicate -> minecraft:condition); one it cannot
    express is printed and keeps the plain model (CATALOG M67). -> written."""
    if mc_key(target) < mc_key("1.21.2"):
        return 0
    import runpy
    written = 0
    for nsdir in sorted((repo / "src/main/resources/assets").glob("*")) if (repo / "src/main/resources/assets").is_dir() else []:
        ns = nsdir.name
        if not (nsdir / "models/item").is_dir():
            continue
        argv, sys.argv = sys.argv, ["gen-client-items.py", "--ns", ns, "--root", str(repo)]
        try:
            g = runpy.run_path(str(ROOT / "templates/multi-version/tools/gen-client-items.py"), run_name="gen_client_items")
        finally:
            sys.argv = argv
        models = g["load_models"]()
        eggs = g["scan_egg_tints"](str(repo / "src/main/java"))
        trims = g["trim_index"](str(repo))
        unconverted = []
        parented = {m.get("parent", "").split("/", 1)[1] for m in models.values()
                    if (m.get("parent") or "").startswith(ns + ":item/")}
        out = tdir / "resources/assets" / ns / "items"
        for item_id in sorted(models):
            if (out / f"{item_id}.json").exists() or (nsdir / "items" / f"{item_id}.json").exists() \
                    or (item_id in parented and not models[item_id].get("textures")):
                continue
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{item_id}.json").write_text(
                g["render"](g["definition_for"](item_id, models, eggs, trims, unconverted)), encoding="utf-8")
            written += 1
        if unconverted:
            print(f"fork-port: {len(unconverted)} item model(s) with 1.21.1 overrides no item-definition form was "
                  f"written for (they show the plain model): {', '.join(unconverted[:8])}")
    return written


def whole_file_overlays(repo, tdir):
    """The target's own Java overlays that replace a whole authors' file: targets/<t>/java/<path> for
    src/main/java/<path>, targets/<t>/<set>/java/<path> for src/<set>/java/<path>. An overlay-only file (a helper the
    authors do not have) is not one. One definition, tools/layer-sync.py whole_file_overlays(): the files its overlay
    report counts against the budget are the files REVIEW.md needs a row for. -> [(overlay, authors' file)] as paths."""
    if not tdir.is_dir():
        return []
    ls = load("layer_sync", "layer-sync.py")
    return [(ov, src) for _s, _rel, ov, src in ls.whole_file_overlays(pathlib.Path(repo), tdir.name)]


def overlay_stubs(repo, tdir):
    """Stub signals of every Java overlay of the target against the authors' file it replaces (src/<set>/java/<same
    path>). The loop's own audit sees only the files ITS run changed, so an earlier run's loss -- or a person's -- was
    never compared again (measured: five client-extension overrides deleted with their renderers, unflagged). Counts,
    not text, are compared, so the renames an overlay carries do not read as a change. -> [{file, why}]"""
    ss = load("singleshot", "singleshot.py")
    out = []
    for ov, src in whole_file_overlays(repo, tdir):
        why = ss.stub_signals(src.read_text(encoding="utf-8", errors="replace"),
                              ov.read_text(encoding="utf-8", errors="replace"), audit=True)
        if why:
            out.append({"file": ov.relative_to(repo).as_posix(), "why": "; ".join(why)})
    return out


def layer_recipes(repo, tdir, target):
    """1.21.2 made a recipe ingredient "id" / "#tag" where 1.21.1 wrote {"item": id} / {"tag": tag}; a 1.21.1 recipe then
    fails to parse and the recipe is gone (measured: 17 of a mod's recipes at Gate B). Derive the 1.21.2 form of every
    authors' recipe into the TARGET's resource overlay with the multi-version template's transform
    (gen-resource-overlays.py ingredients_as_strings). A recipe it refuses (an unknown type, an ingredient beside
    other keys) is left for the residue; an overlay this did not write (no line in targets/<t>/GENERATED.txt) is never
    touched, and one it did write is rewritten each run, so an authors' edit reaches it. -> (written, refused paths)"""
    if mc_key(target) < mc_key("1.21.2"):
        return 0, []
    import runpy
    g = runpy.run_path(str(ROOT / "templates/multi-version/tools/gen-resource-overlays.py"), run_name="gen_resource_overlays")
    res = repo / "src/main/resources"
    own = sorted(p.name for p in (res / "data").glob("*")) if (res / "data").is_dir() else []
    ledger = tdir / "GENERATED.txt"
    mine = set(ledger.read_text(encoding="utf-8").split()) if ledger.is_file() else set()
    written, refused, keep = 0, [], set()
    for f in sorted(res.glob("data/*/recipe/**/*.json")):
        rel = f.relative_to(res).as_posix()
        out = tdir / "resources" / rel
        if out.exists() and rel not in mine:
            continue                      # a person's (or an earlier fixer's) overlay
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
            new = g["ingredients_as_strings"](json.loads(json.dumps(doc)), rel, {"own_namespaces": own})
        except g["Refused"] as e:
            refused.append(f"{rel}: {e}"); continue
        except ValueError:
            continue
        if new == doc:
            if rel in mine and out.exists():
                out.unlink()              # the authors moved to the new form themselves
            continue
        text = g["rendered"](new)
        keep.add(rel)
        if not out.exists() or out.read_text(encoding="utf-8") != text:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8"); written += 1
    if keep or mine:          # exactly the recipes this run derived: one the authors fixed themselves drops out
        write_ledger(ledger, keep, owns=lambda r: r.startswith("data/") and "/recipe/" in r)
    return written, refused


ENTITY_PREDICATE_DATA = ("advancement", "loot_table")


def layer_entity_predicates(repo, tdir, target, mv=None):
    """26.2 made EntityPredicate a `Codec.dispatchedMap` over the entity_sub_predicate_type registry (CATALOG V68): a
    1.21 predicate's `type` is `entity_type` and `type_specific` folds into its own key, else the advancement or loot
    table fails to parse at load and is silently missing (measured: a boss-kill advancement's
    `conditions.entity.type` at Gate B). Derive the 26.2 form of every authors' advancement and loot table into the
    TARGET's resource overlay with the multi-version template's transform (gen-resource-overlays.py
    entity_predicates_as_dispatched_map, both carriers: a criterion field and an entity_properties condition). The
    input is the nearest inherited target's overlay of that file, else the authors' file. A file it refuses (a key
    with no 26.2 id, an unknown carrier) is left for the residue; an overlay this did not write (no line in
    GENERATED.txt) is never touched. -> (written, refused paths)"""
    if mc_key(target) < mc_key("26.2") or mc_key(target) >= mc_key("26.3"):
        return 0, []                          # from 26.3 layer_263_data derives these files (this transform first)
    import runpy
    g = runpy.run_path(str(ROOT / "templates/multi-version/tools/gen-resource-overlays.py"), run_name="gen_resource_overlays")
    res = repo / "src/main/resources"
    mv = mv or tdir.parent.parent
    parents = [mv / "targets" / x / "resources" for x in chain(mv, tdir.name)[1:]]
    ledger = tdir / "GENERATED.txt"
    mine = set(ledger.read_text(encoding="utf-8").split()) if ledger.is_file() else set()
    owns = lambda r: r.startswith("data/") and len(r.split("/")) > 3 and r.split("/")[2] in ENTITY_PREDICATE_DATA
    written, refused, keep = 0, [], set()
    for kind in ENTITY_PREDICATE_DATA:
        for f in sorted(res.glob(f"data/*/{kind}/**/*.json")):
            rel = f.relative_to(res).as_posix()
            out = tdir / "resources" / rel
            if out.exists() and rel not in mine:
                continue                      # a person's (or an earlier fixer's) overlay
            base = next((p / rel for p in parents if (p / rel).is_file()), f)
            try:
                doc = json.loads(base.read_text(encoding="utf-8"))
                new = g["entity_predicates_as_dispatched_map"](json.loads(json.dumps(doc)), rel, {})
            except g["Refused"] as e:
                refused.append(f"{rel}: {e}"); continue
            except ValueError:
                continue
            if new == doc:
                if rel in mine and out.exists():
                    out.unlink()              # nothing to change any more (the authors moved to the new form)
                continue
            text = g["rendered"](new)
            keep.add(rel)
            if not out.exists() or out.read_text(encoding="utf-8") != text:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(text, encoding="utf-8"); written += 1
    if keep or any(owns(r) for r in mine):
        write_ledger(ledger, keep, owns)
    return written, refused


TRIM_PALETTE_KEY_121 = "trims/color_palettes/trim_palette"
VANILLA_TRIM_PALETTES = {"amethyst", "copper", "diamond", "emerald", "gold", "iron", "lapis", "netherite", "quartz",
                         "redstone", "resin"}


def _owns_263(r):
    p = r.split("/")
    return (r.startswith("data/") and len(p) > 3 and p[2] in ("worldgen", "trim_material") + ENTITY_PREDICATE_DATA) \
        or r in ("assets/minecraft/atlases/items.json", "assets/minecraft/atlases/blocks.json",
                 "assets/minecraft/atlases/armor_trims.json") \
        or (r.startswith("assets/") and "/textures/palettes/trim/" in r)


def layer_263_data(repo, tdir, target, mv=None):
    """26.3 re-spelled two data shapes a 1.21 datapack writes, and a parse failure in either stops the server at
    registry load (CATALOG V106): a block state in JSON is a bare id or {"id", "properties"} (1.21: {"Name",
    "Properties"} -- template pools' processors, processor lists, features), and a trim material is {palette_id,
    description}, its palette a PNG under textures/palettes/ (1.21: an asset_name the atlases' paletted permutations
    mapped to textures/trims/color_palettes/<x>.png). Derive the 26.3 form into the TARGET's resource overlay with the
    multi-version template's transforms (block_states_as_id, trim_material_palette), the input being the nearest
    inherited overlay of the file else the authors' file, and with the trims: copy each palette PNG to
    textures/palettes/trim/, move the item-trim permutation source of the authors' blocks atlas to the items atlas
    (palette key minecraft:trim_base), and empty the armor_trims atlas 26.3 no longer has (armour trims read the
    material's palette directly). And every advancement and loot table in 26.3's loot grammar (CATALOG V107,
    loot_grammar_263, after 26.2's entity_predicates_as_dispatched_map, whose job this step takes over from 26.3): a
    dispatch keyed on "type", one condition / modifier where 1.21 had lists, typed number providers, criterion entity
    conditions as loot conditions. A file it refuses is left for the residue; an overlay this did not write (no line in
    GENERATED.txt) is never touched. -> (written, refused paths)"""
    if mc_key(target) < mc_key("26.3"):
        return 0, []
    import runpy
    g = runpy.run_path(str(ROOT / "templates/multi-version/tools/gen-resource-overlays.py"), run_name="gen_resource_overlays")
    res = repo / "src/main/resources"
    mv = mv or tdir.parent.parent
    parents = [mv / "targets" / x / "resources" for x in chain(mv, tdir.name)[1:]]
    ledger = tdir / "GENERATED.txt"
    mine = set(ledger.read_text(encoding="utf-8").split()) if ledger.is_file() else set()
    written, refused, keep = 0, [], set()

    def base(rel):
        return next((p / rel for p in parents if (p / rel).is_file()), res / rel)

    def put(rel, data):
        nonlocal written
        out = tdir / "resources" / rel
        if out.exists() and rel not in mine:
            return False                      # a person's (or an earlier fixer's) overlay
        keep.add(rel)
        if not out.exists() or out.read_bytes() != data:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data); written += 1
        return True

    def load_json(rel):
        try:
            return json.loads(base(rel).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    # the 1.21 palette permutations: asset name -> "ns:trims/color_palettes/x", from every atlas the authors ship
    perms, atlases = {}, {}
    for f in sorted(res.glob("assets/*/atlases/*.json")):
        rel = f.relative_to(res).as_posix()
        doc = load_json(rel)
        if isinstance(doc, dict) and isinstance(doc.get("sources"), list):
            atlases[rel] = doc
            for src in doc["sources"]:
                if isinstance(src, dict) and str(src.get("type", "")).removeprefix("minecraft:") == "paletted_permutations":
                    perms.update({k: v for k, v in (src.get("permutations") or {}).items() if isinstance(v, str)})
    moved = {}                                # old palette texture id -> new palette id

    def palette_of(name, where):
        old = perms.get(name)
        if old is None:
            return f"minecraft:trim/{name}" if name in VANILLA_TRIM_PALETTES else None
        ns, _, path = old.rpartition(":")
        ns = ns or "minecraft"
        if ns == "minecraft":
            return f"minecraft:trim/{path.rsplit('/', 1)[-1]}"
        png = f"assets/{ns}/textures/{path}.png"
        src = base(png)
        if not src.is_file():
            return None
        new = f"{ns}:trim/{path.rsplit('/', 1)[-1]}"
        if put(f"assets/{ns}/textures/palettes/trim/{path.rsplit('/', 1)[-1]}.png", src.read_bytes()):
            moved[old] = new
        return new

    jobs = [(f, "block_states_as_id") for f in sorted(res.glob("data/*/worldgen/**/*.json"))] \
        + [(f, "trim_material_palette") for f in sorted(res.glob("data/*/trim_material/*.json"))] \
        + [(f, "loot_grammar_263") for kind in ENTITY_PREDICATE_DATA for f in sorted(res.glob(f"data/*/{kind}/**/*.json"))]
    for f, tf in jobs:
        rel = f.relative_to(res).as_posix()
        doc = load_json(rel)
        if doc is None:
            continue
        try:
            new = json.loads(json.dumps(doc))
            if tf == "loot_grammar_263":      # 26.2's entity predicates first (a no-op on an inherited 26.2 overlay)
                new = g["entity_predicates_as_dispatched_map"](new, rel, {})
            new = g[tf](new, rel, {"palette_of": palette_of})
        except g["Refused"] as e:
            refused.append(f"{rel}: {e}"); continue
        if new != doc:
            put(rel, g["rendered"](new).encode("utf-8"))

    if moved:                                 # the atlases, only when a trim palette moved
        item_srcs = []
        for rel in ("assets/minecraft/atlases/blocks.json", "assets/minecraft/atlases/armor_trims.json"):
            doc = atlases.get(rel)
            if doc is None:
                continue
            rest = []
            for src in doc["sources"]:
                ptype = str(src.get("type", "")).removeprefix("minecraft:") if isinstance(src, dict) else ""
                if ptype == "paletted_permutations" and str(src.get("palette_key", "")).removeprefix("minecraft:") == TRIM_PALETTE_KEY_121:
                    if rel.endswith("blocks.json"):  # 1.21 item trims lived in the blocks atlas; 26.3's are in items
                        item_srcs.append({"type": "minecraft:paletted_permutations", "palette_key": "minecraft:trim_base",
                                          "permutations": {k: moved.get(v, v) for k, v in src["permutations"].items()},
                                          "textures": src.get("textures", [])})
                    continue                 # armour trims: no atlas on 26.3, the material's palette is read directly
                rest.append(src)
            if rest != doc["sources"]:
                put(rel, g["rendered"]({**doc, "sources": rest}).encode("utf-8"))
        if item_srcs:
            rel = "assets/minecraft/atlases/items.json"
            doc = atlases.get(rel) or {"sources": []}
            put(rel, g["rendered"]({**doc, "sources": doc["sources"] + item_srcs}).encode("utf-8"))

    for rel in sorted(r for r in mine if _owns_263(r) and r not in keep):
        (tdir / "resources" / rel).unlink(missing_ok=True)   # nothing to derive any more
    if keep or any(_owns_263(r) for r in mine):
        write_ledger(ledger, keep, _owns_263)
    return written, refused


def write_ledger(ledger, keep, owns):
    """targets/<t>/GENERATED.txt is shared by every derived-resource step: rewrite only the lines this step OWNS (`owns`
    says which), so one step's run never drops another step's record -- a dropped line would turn that step's own
    generated file into a "hand overlay" it may no longer refresh."""
    other = {r for r in (ledger.read_text(encoding="utf-8").split() if ledger.is_file() else []) if not owns(r)}
    ledger.write_text("".join(f"{r}\n" for r in sorted(other | set(keep))), encoding="utf-8")


GECKO_VERSION = (re.compile(r"geckolib[\w-]*?-1\.\d+(?:\.\d+)?-(\d+)\.\d+"),        # a jar name: geckolib-neoforge-1.21.5-5.1.0.jar
                 re.compile(r"geckolib[\w-]*?-1\.\d+(?:\.\d+)?:(\d+)\.\d+"),        # a maven coordinate: ...-1.21.5:5.1.0
                 re.compile(r"\bgeckolib_version\s*=\s*(\d+)\.\d+"))                   # a -P / gradle property


def target_geckolib_major(tdir):
    """The GeckoLib major version a target builds against, read from what layer-deps.py resolved (DEPS.md) or the
    target's own properties; None when the target names no GeckoLib (or names it without a version)."""
    for f in (tdir / "DEPS.md", tdir / "target.properties"):
        text = f.read_text(encoding="utf-8", errors="replace") if f.is_file() else ""
        for line in text.splitlines():
            if "geckolib" not in line.lower():
                continue
            for rx in GECKO_VERSION:
                m = rx.search(line)
                if m:
                    return int(m.group(1))
    return None


GECKO_ASSETS = (("geo", ".geo.json", "geckolib/models"), ("animations", ".animation.json", "geckolib/animations"))


def layer_geckolib_assets(repo, tdir, target):
    """GeckoLib 5 (the 1.21.5+ lines) loads models and animations ONLY from assets/<ns>/geckolib/{models,animations}/
    (GeckoLibResources lists exactly those two folders; the id is the path under them without .geo.json /
    .animation.json); 4.x's assets/<ns>/geo/ and animations/ are never scanned, so every GeckoLib entity is drawn with no
    model and nothing crashes (CATALOG V18c, M60). Copy each authors' 4.x file, byte for byte, into the TARGET's
    resource overlay at the 5.x path (sub-folders kept). The ids the code asks for are the shared rows' job (the bare
    name). A file the overlay has that this step did not write (no line in GENERATED.txt) is a person's and never
    touched; a generated one is rewritten when the authors' file changes and removed when it is gone. A target whose
    GeckoLib is 4.x, or that has none, gets nothing. -> files written"""
    major = target_geckolib_major(tdir)
    if major is None or major < 5:
        return 0
    res = repo / "src/main/resources"
    ledger = tdir / "GENERATED.txt"
    mine = set(ledger.read_text(encoding="utf-8").split()) if ledger.is_file() else set()
    owns = lambda r: r.startswith("assets/") and "/geckolib/" in r
    written, keep = 0, set()
    for nsdir in sorted((res / "assets").glob("*")) if (res / "assets").is_dir() else []:
        for old_dir, suffix, new_dir in GECKO_ASSETS:
            src_dir = nsdir / old_dir
            for f in sorted(src_dir.rglob("*" + suffix)) if src_dir.is_dir() else []:
                rel = f"assets/{nsdir.name}/{new_dir}/{f.relative_to(src_dir).as_posix()}"
                if (res / rel).exists():
                    continue                  # the authors ship the 5.x path themselves
                out = tdir / "resources" / rel
                if out.exists() and rel not in mine:
                    continue                  # a person's overlay
                keep.add(rel)
                data = f.read_bytes()
                if not out.exists() or out.read_bytes() != data:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(data); written += 1
    for rel in sorted(r for r in mine if owns(r) and r not in keep):
        (tdir / "resources" / rel).unlink(missing_ok=True)   # its authors' file is gone
    if keep or any(owns(r) for r in mine):
        write_ledger(ledger, keep, owns)
    return written


CLIENT_WIDEN = re.compile(r"\b(?:Player|Level)\s+\w+\s*=\s*(?:net\.minecraft\.client\.)?Minecraft\.getInstance\(\)\.(?:player|level)\s*;")
CLASS_ONLYIN = re.compile(r"@OnlyIn\(\s*(?:Dist\.)?CLIENT\s*\)\s*(?:@[\w.]+(?:\([^)]*\))?\s*)*(?:public\s+|final\s+|abstract\s+)*"
                          r"(?:class|interface|enum|record)\b")


CLIENT_ONLY_CLASS = re.compile(r"@(?:EventBusSubscriber|Mod)\s*\([^)]*\b(?:value|dist)\s*=\s*(?:\{\s*)?(?:Dist\.)?CLIENT\b")


def client_mixin(text):
    """A mixin whose @Mixin target is a net.minecraft.client class (by its import or written in full)."""
    m = re.search(r"@Mixin\s*\(\s*(?:value\s*=\s*)?\{?\s*([\w.]+)\.class", text)
    if not m:
        return False
    name = m.group(1)
    if name.startswith("net.minecraft.client."):
        return True
    imp = re.search(r"(?m)^import\s+([\w.]+)\." + re.escape(name.split(".")[0]) + r"\s*;", text)
    return bool(imp and imp.group(1).startswith("net.minecraft.client"))


def blank_comments(text):
    """`text` with every // and /* */ comment (javadoc included) replaced by spaces, newlines kept, string and char
    literals left alone: a scan for code shapes must not match an example in a comment (CATALOG M52's audit once
    flagged a javadoc line of the shared ClientRefs helper), and line numbers stay right."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1]); i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i)); i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join(ch if ch == "\n" else " " for ch in text[i:j])); i = j
        else:
            out.append(c); i += 1
    return "".join(out)


def client_refs_in_common(repo, prepared):
    """From 1.21.6 strip_onlyin removes @OnlyIn, so a former @OnlyIn(CLIENT) method of a COMMON class is verified on a
    dedicated server, and `Player p = Minecraft.getInstance().player;` there loads LocalPlayer: NoClassDefFoundError in
    RegisterEvent (CATALOG M52). The shared rows route that through ClientRefs, but rows never reach an overlay, and
    Gate B names one class per run. -> ["<file>:<line>"] for every such assignment outside client code (a path with a
    `client` segment, or a class the authors marked @OnlyIn(CLIENT) as a whole)."""
    out = []
    for f in sorted(pathlib.Path(prepared).rglob("*.java")):
        rel = f.relative_to(prepared).as_posix()
        if "/client/" in f"/{rel}":
            continue
        text = blank_comments(f.read_text(encoding="utf-8", errors="replace"))
        if not CLIENT_WIDEN.search(text):
            continue
        orig = repo / "src/main/java" / rel
        if orig.is_file() and CLASS_ONLYIN.search(orig.read_text(encoding="utf-8", errors="replace")):
            continue
        if CLIENT_ONLY_CLASS.search(text) or client_mixin(text):
            continue                # registered for the client only, or a mixin into a client class: never on a server
        out += [f"{rel}:{text.count(chr(10), 0, m.start()) + 1}" for m in CLIENT_WIDEN.finditer(text)]
    return out


def unreviewed_stubs(tdir):
    """The entries of tdir/STUBS.json nobody has reviewed yet."""
    try:
        have = json.loads((tdir / "STUBS.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [e for e in have if isinstance(e, dict) and not e.get("reviewed")]


# ---------------------------------------------------------------- a target's done record (R1)
#
# DONE is one fixed list, the same for every target, checked by target_done(): (1) a green Gate C recorded in GATES.md
# at a commit HEAD carries, nothing that can change the jar changed since (gates_current: port-publish.py's
# listed_green, the rule layer-deps.py applies to a library), (2) no unreviewed STUBS.json entry, (3) REVIEW.md
# complete for the reviewed commit: an independent fresh-context reviewer's verdict on every STUBS.json entry, every
# whole-file overlay (against the authors' file), each resource group and renames.tsv (review_file_items), with the reviewer, the date and the commit id reviewed, nothing the
# target is built from (its directory, src/, its chain's targets/ and shared/) changed since that commit, and no
# overlay layer-sync.py recorded in REMERGE.json as not taking the authors' change still unedited, (4) the
# target's manual-test items generated (TEST-ITEMS.md, from tools/layer-test-plan.py), (5) a parent that was skipped
# with --allow-undone-parent done by now, (6) every item of the base build's behaviour inventory accounted for
# (tools/layer-inventory.py inventory_unaccounted(), its account in INVENTORY-CHECK.json), and (7) no dependency
# resolved against a library target that is not done (--allow-undone-library, DEPS.md). Compile and the audits are implied: the gates stage runs only at 0 errors and
# after the audit. Every file here is Markdown: written after the gates, it must not make port-publish.py read the
# target's GATES.md as stale (a *.md is not in a player's jar).

REVIEW_OUTPUTS = ("GATES.md", "COST.md", "RESIDUE.md", "REVIEW.md", "TEST-ITEMS.md",   # not what a review judged
                  "BEHAVIOUR.json",            # the reviewer's own record beside REVIEW.md (a moved/dropped item and
                                               # why): writing it must not make the review it belongs to stale; it is
                                               # still read as a reviewed input by layer-inventory.py's account
                  "INVENTORY-CHECK.json",      # layer-inventory.py's account, rewritten by every done check
                  "DEPS.md",                   # layer-deps.py resolve's table, rewritten by every setup (machine paths)
                  "KNOWN-ISSUES.json")         # the known-issues ledger (tools/known-issues.py): a record of findings
# Files of a target's directory that are not in a player's jar: a change to them does not make GATES.md stale
# (tools/layer-deps.py NOT_JAR, the same list). REMERGE.json is layer-sync's record of an overlay the authors' change
# did not merge into: a reviewed input (it is not in REVIEW_OUTPUTS), but not jar content.
NOT_JAR_FILES = frozenset(REVIEW_OUTPUTS) | {"STUBS.json", "BEHAVIOUR.json", "INVENTORY.json", "REMERGE.json"}
COMMIT_ID = re.compile(r"^[0-9a-f]{7,40}$")
VERDICTS = ("ok", "not-ok")
# REVIEW.md row kinds: stub / overlay / resources / renames are what review_items() requires a verdict on (a version
# branch's `changed` file stands for a layer's `overlay`, and `deleted` names a file it lacks that its parent has:
# tools/fork-branches.py); `item` names one behaviour-inventory
# item id (review_skeleton lists the unaccounted ones; layer-inventory.py reads it); a reviewer adds `missing` for a
# file that should exist and does not (a repair tag, a model) and `shared` for a shared-library helper the target uses.
# Any row a reviewer marks not-ok keeps the target not done, whatever its kind; a `missing` or `shared` row needs a
# verdict once written.
REVIEW_KINDS = ("stub", "overlay", "resources", "renames", "item", "changed", "deleted", "missing", "shared")
REVIEWER_KINDS = ("missing", "shared")
REVIEW_FIELDS = ("reviewer", "date", "commit")
REVIEW_HEAD = ("# Review: Minecraft {t}\n\n"
               "An independent review by a FRESH agent that was given only this target's files (STUBS.json, its overlays,\n"
               "the authors' files they replace, its resource overlays and renames.tsv). One row per STUBS.json entry,\n"
               "per whole-file overlay, per resource group (`resources`: a directory of the target's resources/, with the\n"
               "digest of its files -- an edit makes it a new row) and for renames.tsv (`renames`, with its digest);\n"
               "verdict `ok` (the change keeps the authors' behaviour, or the difference is right and the note says why)\n"
               "or `not-ok` (it does not: the target is not done). A hook item of the behaviour inventory (a mixin target,\n"
               "an injection, an event listener, a shader uniform/sampler) is accounted for only by a row whose note names\n"
               "its id, or by an `item` row for the id. `reviewer` names the reviewing agent, distinct from the fixer\n"
               "(e.g. `fresh agent <id>, not the fixer`). `commit` is the commit reviewed, a hex id of 7-40 digits\n"
               "(`git rev-parse --short HEAD`). tools/fork-port.py adds a `?` row for every item it finds unreviewed\n"
               "(an `item` row for every unaccounted inventory item); it never changes a verdict.\n\n"
               "- reviewer: ?\n- date: ?\n- commit: ?\n\n| kind | file | verdict | note |\n|---|---|---|---|\n")


def _git(repo, *args, env=None, timeout=120):
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, **({"env": env} if env is not None else {}))
        return r.returncode, (r.stdout.strip() if r.returncode == 0 or r.stdout.strip() else (r.stderr or "").strip())
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def jar_chain(mv, t):
    """t, its inherits chain, then every `shared_chain` version: the targets whose targets/<c>/ and shared/<c>/ can
    change t's jar (tools/layer-deps.py _chain_of, the same rule)."""
    out = chain(mv, t)
    for x in list(out):
        out += [s.strip() for s in read_props(mv / "targets" / x / "target.properties").get("shared_chain", "").split(",")
                if s.strip() and s.strip() not in out]
    return out


def changed_since(repo, sha, paths=()):
    """Files that differ between commit `sha` and the working tree (committed since, edited, or untracked under src/
    or .multiversion/: an untracked overlay or source is built into the jar), limited to `paths` when given.
    -> sorted list, or None when git cannot tell."""
    rc, diff = _git(repo, "diff", "--name-only", sha, "--", *paths)
    rc2, new = _git(repo, "ls-files", "--others", "--exclude-standard", "--", *(paths or ("src", ".multiversion")))
    if rc or rc2:
        return None
    return sorted({x for x in (diff + "\n" + new).splitlines() if x})


def gates_current(repo, t):
    """(current, why) for targets/<t>/GATES.md against the checkout: it names the commit it tested (not an edited
    tree), that commit is HEAD or an ancestor, and nothing that can change t's jar changed since -- port-publish.py's
    listed_green() on the files changed since that commit, with this file list's documents and run outputs aside: the
    rule tools/layer-deps.py applies to a sibling library's checkout, so a library and its dependents agree."""
    repo = pathlib.Path(repo)
    g = repo / ".multiversion/targets" / t / "GATES.md"
    text = g.read_text(encoding="utf-8", errors="replace") if g.is_file() else ""
    if not text:
        return False, "no GATES.md"
    m = re.search(r"at `([^`]+)`", text.splitlines()[0])
    if not m:
        return False, "GATES.md names no commit it tested"
    if "uncommitted" in m.group(1):
        return False, (f"GATES.md certifies an edited tree ({m.group(1)[:120]}), not a commit: commit, then re-run the "
                       "full gates (--from-stage gates --gates full)")
    sha = m.group(1).split()[0]
    if not COMMIT_ID.match(sha) or _git(repo, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")[0] != 0:
        return False, f"GATES.md names {sha}, which is not a commit in this checkout"
    if _git(repo, "merge-base", "--is-ancestor", sha, "HEAD")[0] != 0:
        return False, f"GATES.md names {sha}, which is not an ancestor of HEAD"
    changed = changed_since(repo, sha)
    if changed is not None:
        changed = [f for f in changed if f.rsplit("/", 1)[-1] not in NOT_JAR_FILES]
    green, why = load("port_publish", "port-publish.py").listed_green(t, text, changed, jar_chain(repo / ".multiversion", t))
    return green, ("GATES.md (at " + sha + "): " + why) if not green else why


SYNC_RESIDUE_SECTION = "## Overlays the authors' change did not merge into (layer-sync)"   # tools/layer-sync.py RESIDUE_SECTION


def _split_residue(text):
    """RESIDUE.md -> (fork-port's part, layer-sync's section or "")."""
    i = text.find(SYNC_RESIDUE_SECTION)
    if i < 0:
        return text, ""
    j = text.find("\n## ", i + len(SYNC_RESIDUE_SECTION))
    sec = text[i:j + 1] if j >= 0 else text[i:]
    return (text[:i] + (text[j + 1:] if j >= 0 else "")).rstrip("\n"), sec.rstrip("\n") + "\n"


def put_residue(tdir, t, md):
    """Write fork-port's stop report to tdir/RESIDUE.md, keeping layer-sync's section (an overlay the authors' change
    did not merge into) when the file holds one."""
    f = tdir / "RESIDUE.md"
    _mine, sec = _split_residue(f.read_text(encoding="utf-8") if f.is_file() else "")
    f.write_text(md.rstrip("\n") + "\n" + (f"\n{sec}" if sec else ""), encoding="utf-8")


def clear_residue(tdir, t):
    """A run that ended clears its own report: tdir/RESIDUE.md goes, unless it holds layer-sync's section, which stays
    (alone, under layer-sync's header) until layer-sync removes it."""
    f = tdir / "RESIDUE.md"
    if not f.is_file():
        return
    _mine, sec = _split_residue(f.read_text(encoding="utf-8"))
    if not sec:
        f.unlink()
        return
    text = f"# {t}: residue\n\n{sec}"
    if f.read_text(encoding="utf-8") != text:
        f.write_text(text, encoding="utf-8")


def remerge_open(repo, tdir):
    """The overlays tools/layer-sync.py recorded in tdir/REMERGE.json as not taking the authors' change, still with
    the bytes they had then (a person who merged one changed it). -> one reason each; an unreadable file is one."""
    f = tdir / "REMERGE.json"
    if not f.is_file():
        return []
    rel = f.relative_to(repo).as_posix()
    try:
        conflicts = json.loads(f.read_text(encoding="utf-8")).get("conflicts", [])
        assert isinstance(conflicts, list)
    except (ValueError, AttributeError, AssertionError):
        return [f"{rel} is not readable: cannot tell which overlays the authors' change did not merge into"]
    out = []
    for c in conflicts:
        ov = repo / str((c or {}).get("overlay", ""))
        if not (c or {}).get("overlay") or not ov.is_file():
            continue          # the overlay is gone: removed by a person, nothing left to merge into
        if hashlib.sha256(ov.read_bytes()).hexdigest() == str(c.get("digest", "")).lower():
            out.append(f"the authors' change did not merge into {c['overlay']} ({c.get('reason') or 'conflict'}, "
                       f"{c.get('since') or '?'}; {rel}): merge it by hand (the hunks are in RESIDUE.md), then re-review it")
    return out


PREPARE_INPUTS = ("java", "renames.tsv", "moves.generated.tsv", "drops.txt", "target.properties")


def prepared_tree(repo, t, prepare=False):
    """[] when build/mc<t>/mv/java (what the behaviour inventory reads) exists and is newer than every file it is
    prepared from; else the one reason it is not. prepare: re-prepare it (layer.py prepare, no compile) when missing
    or stale -- only for a caller that holds the checkout (fork-port.py's own run)."""
    repo = pathlib.Path(repo)
    mv = repo / ".multiversion"
    src = repo / "src/main/java"
    if not src.is_dir() or not (mv / "INVENTORY.json").is_file():
        return []        # nothing prepared to judge (layer-inventory.py says what is missing)
    out = repo / "build" / f"mc{t}" / "mv/java"
    cmd = f"python3 .multiversion/tools/layer.py prepare --target {t} --src src/main/java --out build/mc{t}/mv/java"
    # every other source set with its own src/<set>/java is prepared too (layer.gradle does: build/mc<t>/mv/<set>-java),
    # and the inventory reads each one's prepared tree, so a missing one cannot be judged
    others = sorted(d.parent.name for d in (repo / "src").glob("*/java") if d.is_dir() and d.parent.name != "main")
    set_out = lambda s_: out.parent / f"{s_}-java"
    set_cmd = lambda s_: (f"python3 .multiversion/tools/layer.py prepare --target {t} --set {s_} --src src/{s_}/java "
                          f"--out build/mc{t}/mv/{s_}-java")

    def newest():
        roots = [src, mv / "tools/layer.py", mv / "tools/prepare-sources.py"] + [repo / "src" / s_ / "java" for s_ in others]
        for c in jar_chain(mv, t):
            roots += [mv / "targets" / c / x for x in PREPARE_INPUTS] + [mv / "shared" / c / "java", mv / "shared" / c / "renames.tsv"]
        best = (0, None)
        for r in roots:
            for f in ([r] if r.is_file() else r.rglob("*") if r.is_dir() else []):
                if f.is_file() and f.stat().st_mtime_ns > best[0]:
                    best = (f.stat().st_mtime_ns, f)
        return best

    def tree_time():
        return max([out.stat().st_mtime_ns] + [f.stat().st_mtime_ns for f in out.rglob("*") if f.is_file()])
    n_time, n_file = newest()
    why = None if out.is_dir() and tree_time() >= n_time else (
        "missing" if not out.is_dir() else f"older than {n_file.relative_to(repo).as_posix()}")
    # inherited_overlay_rows (opt-in): layer.py prepare stamps an on-mode tree (java.inherited_overlay_rows beside it);
    # a tree prepared in the other mode than the layer now says is stale. Off and unstamped -- every default layer --
    # reads as before
    key_on, supported = load("layer_sync", "layer-sync.py").inherited_rows_setting(mv)
    stamp = out.parent / "java.inherited_overlay_rows"
    # a stamp counts only when written after the tree it sits beside: a layer.py that predates the key re-prepares the
    # tree without touching it, and an off tree it left behind must not read as on forever
    on_tree = stamp.is_file() and out.is_dir() and stamp.stat().st_mtime_ns >= tree_time()
    on_now = key_on and supported
    if not why and on_now != on_tree:
        why = (f"prepared with inherited_overlay_rows {'on' if on_tree else 'off'}, and the layer now has it "
               f"{'on' if on_now else 'off'}")
    missing_sets = [s_ for s_ in others if not set_out(s_).is_dir()]
    if (why or missing_sets) and prepare and (mv / "tools/layer.py").is_file():
        jobs = ([("main", src, out)] if why else []) + [(s_, repo / "src" / s_ / "java", set_out(s_))
                                                         for s_ in others if why or s_ in missing_sets]
        if why:
            stamp.unlink(missing_ok=True)      # layer.py writes a new one when it prepares on
        for s_, sd, od in jobs:
            r = subprocess.run([sys.executable, str(mv / "tools/layer.py"), "prepare", "--target", t, "--set", s_,
                                "--src", str(sd), "--out", str(od)], cwd=str(repo), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=900)
            if r.returncode or not od.is_dir():
                return [f"behaviour inventory: re-preparing build/mc{t}/mv/{od.name} failed (exit {r.returncode}): "
                        + " ".join((r.stdout + r.stderr).strip().splitlines()[-2:])[:300]]
        print(f"fork-port: {t}: re-prepared " + ", ".join(f"build/mc{t}/mv/{od.name}" for _s, _d, od in jobs)
              + f" ({why or 'missing'}; layer.py prepare, no compile) for the done check")
        return []
    if not why and missing_sets:
        return [f"behaviour inventory: no prepared tree build/mc{t}/mv/{s_}-java (the {s_} source set), so it cannot be "
                f"judged: prepare it (no compile): {set_cmd(s_)}" for s_ in missing_sets]
    if not why:
        return []
    if why == "missing":
        return [f"behaviour inventory: no prepared tree build/mc{t}/mv/java (a fresh clone, or never built here), so it "
                f"cannot be judged: prepare it (no compile): {cmd}"]
    return [f"behaviour inventory: the prepared tree build/mc{t}/mv/java is {why}, so its account would be about older "
            f"code: re-prepare it (no compile): {cmd}"]


def blob_ids(repo, files):
    """{file: git blob id} -- what git stores for each file (`git hash-object`, so the checkout's line-ending
    conversion applies and a Windows checkout gets the ids the GitHub tree lists); outside a git repo, the id of the
    bytes as they are."""
    files = list(files)
    if not files:
        return {}
    try:
        r = subprocess.run(["git", "-C", str(repo), "hash-object", "--stdin-paths"], capture_output=True, text=True,
                           input="".join(f"{f}\n" for f in files), encoding="utf-8", errors="replace", timeout=120)
        ids = r.stdout.split()
        if r.returncode == 0 and len(ids) == len(files):
            return dict(zip(files, ids))
    except (OSError, subprocess.TimeoutExpired):
        pass
    out = {}
    for f in files:
        data = pathlib.Path(f).read_bytes()
        out[f] = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
    return out


def resource_group(rel):
    """The REVIEW.md group of a file of a target's resources/ (rel: its path under resources/): its first three
    directories (assets/<ns>/items, data/<ns>/recipe, data/<ns>/tags), or the file itself when it is shallower."""
    parts = rel.split("/")
    return "/".join(parts[:3]) if len(parts) > 3 else rel


def review_file_items(tprefix, entries):
    """The `resources` and `renames` review items of a target from its files: entries [(path under the target's
    directory, git blob id)], tprefix `.multiversion/targets/<t>`. One `resources` item per resource group, one
    `renames` item for renames.tsv, each `<path> @<digest>` (12 hex of a sha256 over the group's paths and blob ids):
    an edit gives a new item, so a verdict on the old bytes never counts for the new ones. One definition, used on a
    checkout (review_items) and on a release's tree (tools/layer-deps.py). -> {(kind, file)}"""
    groups = {}
    for rel, blob in entries:
        if rel.startswith("resources/"):
            groups.setdefault(("resources", "resources/" + resource_group(rel[len("resources/"):])), []).append((rel, blob))
        elif rel == "renames.tsv":
            groups[("renames", rel)] = [(rel, blob)]
    out = set()
    for (kind, g), fs in groups.items():
        dig = hashlib.sha256("".join(f"{r}\0{b}\n" for r, b in sorted(fs)).encode("utf-8")).hexdigest()[:12]
        out.add((kind, f"{tprefix}/{g} @{dig}"))
    return out


def review_items(repo, tdir):
    """What the reviewer must give a verdict on: every STUBS.json entry, every whole-file overlay of the target, each
    group of its resource overlays and its renames.tsv (review_file_items). -> sorted [(kind, file relative to the
    repo)]."""
    items = set()
    try:
        stubs = json.loads((tdir / "STUBS.json").read_text(encoding="utf-8")) if (tdir / "STUBS.json").is_file() else []
    except ValueError:
        stubs = []
    items |= {("stub", e["file"]) for e in stubs if isinstance(e, dict) and e.get("file")}
    items |= {("overlay", ov.relative_to(repo).as_posix()) for ov, _src in whole_file_overlays(repo, tdir)}
    files = sorted(p for p in (tdir / "resources").rglob("*") if p.is_file()) if (tdir / "resources").is_dir() else []
    files += [tdir / "renames.tsv"] if (tdir / "renames.tsv").is_file() else []
    ids = blob_ids(repo, files)
    items |= review_file_items(tdir.relative_to(repo).as_posix(), [(f.relative_to(tdir).as_posix(), ids[f]) for f in files])
    return sorted(items)


def review_counts(repo, tdir, items):
    """'S stub(s), whole-file overlays (N) + overlay-only files (M) (those need no row), R resource group(s),
    renames.tsv' for a message: the overlay words are tools/layer-sync.py overlay_counts()'s."""
    n = lambda k: sum(1 for kk, _ in items if kk == k)
    ls = load("layer_sync", "layer-sync.py")
    own = len(ls.own_overlays(pathlib.Path(repo) / ".multiversion", tdir.name))
    s = (f"{n('stub')} stub(s), {ls.overlay_counts({'overlays': n('overlay'), 'overlay_only': max(own - n('overlay'), 0)})}"
         " (those need no row)")
    return s + f", {n('resources')} resource group(s)" + (", renames.tsv" if n("renames") else "")


def _cells(line):
    """The cells of one Markdown table row; `\\|` is a literal bar."""
    s = line.strip()
    s = s[1:] if s.startswith("|") else s
    s = s[:-1] if s.endswith("|") and not s.endswith("\\|") else s
    return [p.strip().replace("\\|", "|") for p in re.split(r"(?<!\\)\|", s)]


def parse_review(text):
    """REVIEW.md -> (fields {reviewer, date, commit}, rows {(kind, file): (verdict, note)})."""
    fields, rows = {}, {}
    for line in text.splitlines():
        m = re.match(r"^\s*-\s*(reviewer|date|commit)\s*:\s*(.*?)\s*$", line)
        if m and m.group(1) not in fields:
            fields[m.group(1)] = m.group(2).strip("` ")
        elif line.lstrip().startswith("|"):
            c = _cells(line)
            if len(c) >= 4 and c[0] in REVIEW_KINDS:
                rows[(c[0], c[1].strip("`"))] = (c[2].strip("` ").lower(), "|".join(c[3:]).strip())
    return fields, rows


def review_problems(repo, tdir):
    """[] when tdir/REVIEW.md is complete for the commit it names; else what is missing, one line each."""
    repo = pathlib.Path(repo)
    items = review_items(repo, tdir)
    f = tdir / "REVIEW.md"
    if not f.is_file():
        return [f"no REVIEW.md: {len(items)} item(s) need an independent reviewer's verdict "
                f"({review_counts(repo, tdir, items)})"]
    fields, rows = parse_review(f.read_text(encoding="utf-8", errors="replace"))
    out = [f"REVIEW.md has no {k}" for k in REVIEW_FIELDS if fields.get(k, "?") in ("", "?")]
    if fields.get("date", "?") not in ("", "?") and not re.match(r"^\d{4}-\d\d-\d\d\b", fields["date"]):
        out.append(f"REVIEW.md date {fields['date']!r} is not YYYY-MM-DD")
    c = fields.get("commit", "?")
    if c not in ("", "?"):
        rel = tdir.relative_to(repo).as_posix()
        mv = repo / ".multiversion"
        jc = jar_chain(mv, tdir.name) if tdir.parent == mv / "targets" else [tdir.name]
        paths = ["src", *[f".multiversion/targets/{x}" for x in jc], *[f".multiversion/shared/{x}" for x in jc]]
        if not COMMIT_ID.match(c):
            out.append(f"REVIEW.md names commit {c!r}, which is not a commit id (a name such as HEAD or a branch moves): "
                       "name the reviewed commit with `git rev-parse --short HEAD`")
        elif _git(repo, "rev-parse", "--verify", "--quiet", f"{c}^{{commit}}")[0] != 0:
            out.append(f"REVIEW.md names commit {c}, which is not in this checkout")
        elif _git(repo, "merge-base", "--is-ancestor", c, "HEAD")[0] != 0:
            out.append(f"REVIEW.md names commit {c}, which is not an ancestor of HEAD")
        else:
            # the target's own files, and what it is built from: the authors' src/, its chain's overlays and shared
            # library (the target's own run outputs aside; another target's not-jar files aside)
            changed = changed_since(repo, c, paths)
            changed = None if changed is None else [
                x for x in changed if x.rsplit("/", 1)[-1] not in (REVIEW_OUTPUTS if x.startswith(rel + "/") else NOT_JAR_FILES)]
            if changed is None:
                out.append(f"cannot compare {rel} with the reviewed commit {c} (git failed)")
            elif changed:
                out.append(f"{len(changed)} file(s) the target is built from changed since the reviewed commit {c}, e.g. "
                           f"{changed[0]}: re-review them and name the new commit")
    for kind, file in items:
        v = rows.get((kind, file))
        if not v or v[0] not in VERDICTS:
            out.append(f"no verdict: {kind} {file}")
        elif v[0] != "ok":
            out.append(f"reviewer says not-ok: {kind} {file} -- {v[1][:200]}")
        elif not v[1] or v[1] == "?":
            out.append(f"a verdict without a note: {kind} {file}")
    out += extra_row_problems(rows, items)
    return out


def extra_row_problems(rows, items):
    """The rows a reviewer wrote beyond the required items: a not-ok one of any kind (an `item` row too) is a reason
    with its note -- never folded into "unaccounted" -- and a `missing` / `shared` row with no verdict is open."""
    need = set(items)
    out = []
    for (kind, file), (verdict, note) in sorted(rows.items()):
        if (kind, file) in need:
            continue
        if verdict == "not-ok":
            out.append(f"reviewer says not-ok: {kind} {file} -- {note[:200]}")
        elif kind in REVIEWER_KINDS and verdict not in VERDICTS:
            out.append(f"no verdict: {kind} {file}")
    return out


def not_ok_items(text):
    """{inventory item id: note} of REVIEW.md's `item` rows a reviewer marked not-ok."""
    return {f: n for (k, f), (v, n) in parse_review(text)[1].items() if k == "item" and v == "not-ok"}


def drop_reported_items(lines, ids):
    """The behaviour-inventory reasons minus those for item ids a not-ok `item` row already reports (with its note)."""
    return [ln for ln in lines if not any(ln.startswith(f"behaviour inventory: unaccounted {i} (") for i in ids)]


def review_skeleton(repo, tdir, t):
    """Give REVIEW.md a `?` row for every item that has none (the header too when the file is new), and an `item` row
    for every behaviour-inventory item the target leaves unaccounted (tools/layer-inventory.py's account; sorted by id,
    so a file's items stand together): the hooks a file-level row does not account for are named, so the reviewer
    sees them. Never changes a verdict a reviewer wrote. -> rows added."""
    items = review_items(pathlib.Path(repo), tdir)
    f = tdir / "REVIEW.md"
    text = f.read_text(encoding="utf-8") if f.is_file() else REVIEW_HEAD.format(t=t)
    _fields, rows = parse_review(text)
    try:
        _code, rep, _lines = load("layer_inventory", "layer-inventory.py").check(repo, t)
    except Exception:          # noqa: BLE001 -- the skeleton is a help; the done check itself reports the inventory
        rep = None
    unacc = sorted((rep or {}).get("unaccounted") or [])
    add = [(k, x) for k, x in items if (k, x) not in rows] + [("item", i) for i in unacc if ("item", i) not in rows]
    if add or not f.is_file():
        tdir.mkdir(parents=True, exist_ok=True)
        f.write_text(text.rstrip("\n") + "\n" + "".join(f"| {k} | {x.replace('|', chr(92) + '|')} | ? | ? |\n"
                                                         for k, x in add), encoding="utf-8")
    return len(add)


def write_test_items(repo, tdir, t):
    """The target's manual-test items (tools/layer-test-plan.py's items that apply to t, no model) into TEST-ITEMS.md,
    rewritten only when they changed. -> (count, None) or (None, why it could not be generated)."""
    try:
        plan = load("layer_test_plan", "layer-test-plan.py").plan_fork(pathlib.Path(repo))
        items = [g for g in (plan.get("layer") or {}).get("items", []) if t in (g.get("targets") or [])]
    except Exception as e:      # noqa: BLE001 -- a broken plan is a stop with its reason, never a silent pass
        return None, f"tools/layer-test-plan.py failed: {type(e).__name__}: {e}"[:300]
    lines = [f"# Manual-test items: Minecraft {t}", "",
             "Generated by tools/fork-port.py from tools/layer-test-plan.py (no model): what a person must look at on this",
             "version because no gate judges it. The fork-versions skill's step 11 turns them into the test plan.", ""]
    for g in items:
        what = next((str(g[k]) for k in ("overlay", "file", "row", "dependency") if g.get(k)), g.get("kind", "?"))
        extra = [f"range {g['range']}" if g.get("range") else "", f"first {g['first']}" if g.get("first") else "",
                 f"status {g['status']}" if g.get("status") else ""]
        lines.append(f"- {g.get('kind', '?')}: `{what}`" + (f" ({', '.join(x for x in extra if x)})" if any(extra) else ""))
    if not items:
        lines.append("- none: no stub, overlay, gate-blind rename, dropped integration or preview applies to this target")
    text = "\n".join(lines) + "\n"
    f = tdir / "TEST-ITEMS.md"
    if not f.is_file() or f.read_text(encoding="utf-8") != text:
        f.write_text(text, encoding="utf-8")
    return len(items), None


def parent_target(mv, t):
    """The target `t` stands on: its `inherits`, else (a new target, or a direct jump) the nearest lower target the
    layer has. None for the first target."""
    inh = read_props(mv / "targets" / t / "target.properties").get("inherits")
    if inh:
        return inh
    have = [p.name for p in (mv / "targets").iterdir() if p.is_dir() and p.name != t] if (mv / "targets").is_dir() else []
    lower = sorted((x for x in have if mc_key(x) < mc_key(t)), key=mc_key)
    return lower[-1] if lower else None


def target_done(repo, t, _seen=None, write=True, prepare=False):
    """[] when target t is DONE by the one definition above; else every reason it is not, one line each.
    write=False: nothing is written (--check-done only asks). prepare: re-prepare a missing or stale prepared tree for
    the inventory check (no compile) -- only a caller holding the checkout passes it."""
    repo = pathlib.Path(repo)
    mv = repo / ".multiversion"
    tdir = mv / "targets" / t
    if not tdir.is_dir():
        return [f"{t} is not a target of this layer"]
    out = []
    if gates_auto(tdir) != "ab":
        out.append("no green Gate C recorded in GATES.md")
    else:
        cur, why = gates_current(repo, t)
        if not cur:
            out.append(why)
    n_open = len(unreviewed_stubs(tdir))
    if n_open:
        out.append(f"{n_open} unreviewed stub signal(s) in STUBS.json")
    out += review_problems(repo, tdir)
    out += remerge_open(repo, tdir)       # layer-sync.py: an overlay the authors' change did not merge into
    if not (tdir / "TEST-ITEMS.md").is_file():
        out.append(f"manual-test items not generated (TEST-ITEMS.md): write them, no gate run: "
                   f"python3 tools/fork-port.py --repo {repo} --target {t} --write-test-items")
    # (6) every base behaviour item accounted for (tools/layer-inventory.py; [] on a single-version fork). It writes
    # the target's INVENTORY-CHECK.json, a run output like GATES.md (REVIEW_OUTPUTS), unless write=False. Its prepared
    # tree must be current, or its answer is about older code
    tree = prepared_tree(repo, t, prepare=prepare)
    rv = tdir / "REVIEW.md"
    notok = not_ok_items(rv.read_text(encoding="utf-8", errors="replace")) if rv.is_file() else {}
    out += tree or drop_reported_items(load("layer_inventory", "layer-inventory.py").inventory_unaccounted(repo, t, write=write),
                                       notok)
    # (7) R3: a target resolved against a library target that was not done (--allow-undone-library) is development only
    deps_md = tdir / "DEPS.md"
    if deps_md.is_file() and UNDONE_LIBRARY_MARK in deps_md.read_text(encoding="utf-8", errors="replace"):
        out.append("built against a library target that is not done (--allow-undone-library, DEPS.md \"Development only\"): "
                   "re-resolve with --port-release once the library's target is done")
    allowed = read_props(tdir / "target.properties").get("allowed_undone_parent", "")
    if allowed:
        p = allowed.split()[0]
        seen = (_seen or set()) | {t}
        if p not in seen and (mv / "targets" / p).is_dir() and target_done(repo, p, seen, write=write):
            out.append(f"its parent {p} is still not done (added with --allow-undone-parent: {allowed})")
    return out


UNDONE_LIBRARY_MARK = "**Development only (`--allow-undone-library`):**"     # tools/layer-deps.py render_md()


def undone_parent_stop(repo, t, allow=False, parent=None, prepare=False):
    """Before a NEW target is added: (parent, reasons) when the target it would stand on is not done, else None.
    With allow, the same answer, for the caller to record. parent: the target it will inherit, when the caller has
    resolved it (make-fork-layer.py add-target --inherits); else parent_target(). Read-only (write=False); prepare as
    target_done()."""
    mv = pathlib.Path(repo) / ".multiversion"
    if (mv / "targets" / t).is_dir():
        return None
    p = parent or parent_target(mv, t)
    if not p or not (mv / "targets" / p).is_dir():
        return None
    why = target_done(repo, p, write=False, prepare=prepare)
    return (p, why) if why else None


# ---------------------------------------------------------------- the authors' commit a buildout started from (R4)
#
# .multiversion/base.properties records `upstream=<remote>/<branch>` (the authors' branch, as port-sync.py --upstream
# names it), `upstream_commit=<sha>` (the authors' commit the layer branch carried when the buildout started: the
# merge-base of HEAD and that branch, which HEAD must carry in full: freeze refuses a branch HEAD lacks commits of) and
# `library.<owner>/<repo>=<tag>` (each sibling library's layer release the buildout resolves against, from
# --port-release). Recorded at `make-fork-layer.py init --upstream` / `freeze`, or by fork-port.py when it adds the
# first target of a layer that has no record (the branch: --upstream, else the one `upstream` remote branch HEAD
# carries -- never the remote's default branch as such). A run to the end then stops with exit 19 when the authors'
# branch has commits after the recorded one that the checkout does not carry (the upstream moved: keep-current first),
# or -- frozen upstream or not -- when a --port-release names another release of a recorded library; the fetch never
# prompts. --accept-upstream-drift continues and records it in
# .multiversion/DRIFT.md. A single-version fork has no .multiversion/: nothing here runs.

DRIFT_EXIT = 19
UNDONE_EXIT = 18


def detect_upstream(repo):
    """The authors' branch the port follows, as `<remote>/<branch>`: the one branch of the clone's `upstream` remote
    whose tip HEAD carries (the branch keep-current / port-sync.py merge into the port). Not the remote's HEAD: that is
    the authors' default branch, often not the one the port is based on, and freezing it would stop every run (19).
    None when no branch, or more than one, qualifies: the caller then names it with --upstream."""
    rc, out = _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/remotes/upstream/")
    refs = [x for x in out.splitlines() if x and x != "upstream" and not x.endswith("/HEAD")] if rc == 0 else []
    carried = [r for r in refs if _git(repo, "merge-base", "--is-ancestor", r, "HEAD")[0] == 0]
    return carried[0] if len(carried) == 1 else None


# a fetch must never wait for a password: no terminal prompt, no askpass helper, and a short timeout
FETCH_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo", "SSH_ASKPASS": "echo"}
FETCH_TIMEOUT = 45


def upstream_drift(repo, ref, recorded=None, fetch=True):
    """-> {tip, behind: authors' commits on `ref` the checkout lacks, since: commits on `ref` after the recorded one,
    fetched: bool, error}. `fetch` first fetches the branch (a failed fetch compares with what was fetched last)."""
    out = {"ref": ref, "fetched": False}
    if fetch and "/" in ref:
        remote, branch = ref.split("/", 1)
        rc, msg = _git(repo, "fetch", "--quiet", remote, branch, env={**os.environ, **FETCH_ENV}, timeout=FETCH_TIMEOUT)
        out["fetched"] = rc == 0
        if rc:
            out["fetch_error"] = (msg.strip().splitlines() or [f"exit {rc}"])[-1][:200]
    rc, tip = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if rc != 0:
        out["error"] = f"{ref} is not a branch this clone knows (git fetch {ref.split('/', 1)[0]})"
        return out
    out["tip"] = tip
    rc, n = _git(repo, "rev-list", "--count", f"HEAD..{tip}")
    out["behind"] = int(n) if rc == 0 and n.isdigit() else None
    if recorded:
        rc, n = _git(repo, "rev-list", "--count", f"{recorded}..{tip}")
        out["since"] = int(n) if rc == 0 and n.isdigit() else None
    return out


def write_props(path, updates):
    """Set keys in a .properties file, keeping every other line (and the order) as it is."""
    p = pathlib.Path(path)
    lines = p.read_text(encoding="utf-8").splitlines() if p.is_file() else []
    left = dict(updates)
    for i, line in enumerate(lines):
        k = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if k in left:
            lines[i] = f"{k}={left.pop(k)}"
    lines += [f"{k}={v}" for k, v in left.items()]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def freeze_upstream(repo, ref=None, libraries=(), accept_drift=False, props_file=None):
    """Record the authors' commit the buildout starts from (the merge-base of HEAD and `ref`, default the recorded or
    detected `upstream` branch) and each `owner/repo@TAG` library release in base.properties (props_file: another
    properties file -- a version branch's .port/<t>/branch.properties). -> (what was recorded, None) or (None, why not).
    Pinning a library only (libraries, no `ref`, an upstream already recorded) leaves the upstream record as it is, so a
    moved authors' branch never blocks a library pin (issue #133, gap 3); accept_drift records the merge-base although
    HEAD lacks commits of the branch (the run then stops with 19 until keep-current, or continues with
    --accept-upstream-drift)."""
    repo = pathlib.Path(repo)
    bp = pathlib.Path(props_file) if props_file else repo / ".multiversion/base.properties"
    props = read_props(bp)
    if libraries and not ref and props.get("upstream_commit"):
        ref = None                              # a library pin alone: the upstream record stays
    else:
        ref = ref or props.get("upstream") or detect_upstream(repo)
    rec = {}
    if ref:
        rc, base = _git(repo, "merge-base", "HEAD", ref)
        if rc != 0 or not base:
            return None, f"no common commit between HEAD and {ref} (fetch it first: git fetch {ref.split('/', 1)[0]})"
        rc, n = _git(repo, "rev-list", "--count", f"HEAD..{ref}")
        if rc == 0 and n.isdigit() and int(n) and not accept_drift:
            return None, (f"HEAD lacks {n} commit(s) of {ref}: a buildout frozen there would stop at once (exit 19). Bring "
                          "them in with keep-current first (port-sync.py), or name the authors' branch the port actually "
                          "follows with --upstream <remote>/<branch>, or record it anyway with --accept-upstream-drift")
        rec.update({"upstream": ref, "upstream_commit": base})
    for lib in libraries:
        name, _, tag = lib.partition("@")
        if name and tag:
            rec[f"library.{name}"] = tag
    if not rec:
        return None, ("no authors' branch to record: pass --upstream <remote>/<branch> (no single branch of the clone's "
                      "`upstream` remote is carried by HEAD)")
    write_props(bp, rec)
    return rec, None


def drift_stop(repo, a, fetch=True, props_file=None, drift_file=None):
    """The R4 check for a run to the end on a layer fork: None, or the stop for an authors' branch (or a recorded
    library release) that moved since the buildout's freeze. Records the freeze first when the layer has none and this
    run adds its first target (or names --upstream). Prints what it did. props_file / drift_file: a version branch's
    .port/<t>/branch.properties and DRIFT.md instead of the layer's base.properties and DRIFT.md."""
    if os.environ.get(DRIFT_CHECKED_ENV) == "1":      # a --chain sweep's own child: the sweep checked once
        return None
    repo = pathlib.Path(repo)
    mv = repo / ".multiversion"
    bp = pathlib.Path(props_file) if props_file else mv / "base.properties"
    if not bp.is_file():
        return None
    props = read_props(bp)
    if props_file:
        new_first = False
    else:
        have_targets = any(p.is_dir() for p in (mv / "targets").iterdir()) if (mv / "targets").is_dir() else False
        new_first = not (mv / "targets" / a.target).is_dir() and not have_targets
    up = getattr(a, "upstream", None)
    if not props.get("upstream_commit") and (new_first or up):
        rec, why = freeze_upstream(repo, up, getattr(a, "port_release", []), props_file=props_file)
        print(f"fork-port: upstream: " + (f"froze the buildout at {', '.join(f'{k}={v}' for k, v in rec.items())} "
                                          f"({bp.relative_to(repo).as_posix()})" if rec else f"NOT frozen: {why}"))
        props = read_props(bp)
    ref, rec = props.get("upstream"), props.get("upstream_commit")
    port = a.base.split("/", 1)[1] if "/" in a.base else a.base
    kc = (f"keep-current for this fork first (Claude Code: \"keep this fork current\"; command line: python3 "
          f"tools/port-sync.py --repo {repo} --port {port} --upstream {ref or '<remote>/<branch>'} --dry-run, then the "
          f"keep-current skill), then " + (f"fork-branches.py freeze --repo {repo} --target {a.target}" if props_file
                                             else f"make-fork-layer.py freeze --repo {repo}"))
    reasons = []
    if not rec:
        print(f"fork-port: upstream: WARNING no authors' commit recorded for this buildout ({bp.relative_to(repo).as_posix()} "
              "upstream/upstream_commit): an upstream that moves mid-ladder goes unseen -- "
              + (f"fork-branches.py freeze --repo {repo} --target {a.target}" if props_file else f"make-fork-layer.py freeze --repo {repo}")
              + " --upstream <remote>/<branch>")
    else:
        st = upstream_drift(repo, ref, rec, fetch=fetch)
        if st.get("fetch_error"):
            print(f"fork-port: upstream: could not fetch {ref} ({st['fetch_error']}): comparing with the last fetch")
        # the authors moved since the freeze (rec..tip), not "HEAD lacks commits of the branch" (HEAD..tip): a freeze
        # records what the buildout started from, which HEAD may never have carried in full
        since = st.get("since") if st.get("since") is not None else st.get("behind")
        if st.get("error"):
            print(f"fork-port: upstream: WARNING cannot compare with the authors' branch: {st['error']}")
        elif since and st.get("behind"):
            reasons.append(f"the authors' {ref} has {since} commit(s) since the freeze that this checkout does not carry "
                           f"(buildout frozen at {rec[:12]}, {ref} now at {st['tip'][:12]}"
                           + ("" if st["fetched"] else "; not fetched now, compared with the last fetch") + ")")
        elif since:
            print(f"fork-port: upstream: the checkout carries {ref} at {st['tip'][:12]}, newer than the freeze {rec[:12]} "
                  f"(keep-current merged it): record it with make-fork-layer.py freeze --repo {repo}")
        elif st.get("behind"):
            print(f"fork-port: upstream: note: {ref} has {st['behind']} commit(s) this checkout does not carry, none of "
                  f"them after the freeze {rec[:12]}: not a stop")
    for lib in getattr(a, "port_release", []):     # a library release is compared whether or not an upstream is frozen
        name, _, tag = lib.partition("@")
        had = props.get(f"library.{name}")
        if had and tag and had != tag:
            reasons.append(f"library {name}: this run resolves against {tag}, the buildout was frozen at {had}")
    if not reasons:
        return None
    if getattr(a, "accept_upstream_drift", False):
        day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        df = pathlib.Path(drift_file) if drift_file else mv / "DRIFT.md"
        with open(df, "a", encoding="utf-8") as fh:
            fh.write(f"- {day} {a.target}: accepted with --accept-upstream-drift: {'; '.join(reasons)}\n")
        print(f"fork-port: upstream: drift ACCEPTED (--accept-upstream-drift, recorded in {df.relative_to(repo).as_posix()}): {'; '.join(reasons)}")
        return None
    return {"stop": "upstream moved since the buildout started: " + "; ".join(reasons), "code": DRIFT_EXIT,
            "detail": reasons, "choices": [f"bring it in: {kc}",
                                           "or continue knowingly on the frozen code: the same command with --accept-upstream-drift (recorded in .multiversion/DRIFT.md)"]}


INJECTORS =r'(?:Inject|Redirect|ModifyArg|ModifyArgs|ModifyVariable|ModifyConstant|ModifyReturnValue|' \
            r'ModifyExpressionValue|WrapOperation|WrapWithCondition)'


def _paren(text, i):
    """The text inside the parenthesis that opens at text[i] (balanced, strings skipped)."""
    depth, j, q = 0, i, None
    while j < len(text):
        c = text[j]
        if q:
            if c == "\\":
                j += 1
            elif c == q:
                q = None
        elif c in "\"'":
            q = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[i + 1:j]
        j += 1
    return text[i + 1:]


def hook_targets(text):
    """What a file hooks, as a sorted list of one-line strings: each mixin class target (@Mixin), each injector
    (@Inject/@Redirect/@Modify*/@Wrap*) as its kind, method= and every @At (value, target, shift, ordinal...), each
    @Overwrite by name, and each @SubscribeEvent handler by its event type. A stub review is a judgement about where an
    effect is hooked, and this is that: when it differs, the review no longer speaks for the file (measured: a "moved,
    same effect" review inherited across six targets after the handler had moved from HEAD/TAIL injections to an event
    that does not fire in every dimension). Handler BODIES are deliberately not hashed: an overlay's body carries that
    target's renames, so a hash would change on nearly every hop and reopen every review (losing the measured saving)
    while saying nothing about where the code runs. Whitespace and comments are ignored."""
    ss = load("singleshot", "singleshot.py")
    t = ss.COMMENTS.sub("", text or "")
    sq = lambda x: re.sub(r"\s+", "", x)
    out = []
    for m in re.finditer(r"@Mixin\s*\(", t):
        body = _paren(t, m.end() - 1)
        out += [f"@Mixin {c}" for c in re.findall(r"([\w.$]+)\.class", body)]
        tg = re.search(r'\btargets\s*=\s*(\{[^}]*\}|"[^"]*")', body)
        out += [f"@Mixin {c}" for c in re.findall(r'"([^"]*)"', tg.group(1))] if tg else []
    for m in re.finditer(r"@(" + INJECTORS + r")\s*\(", t):
        body = _paren(t, m.end() - 1)
        meth = re.search(r'\bmethod\s*=\s*(\{[^}]*\}|"[^"]*")', body)
        methods = ",".join(sorted(re.findall(r'"([^"]*)"', meth.group(1)))) if meth else ""
        ats = []
        for a in re.finditer(r"@At\s*\(", body):
            at = sq(_paren(body, a.end() - 1))
            ats.append("@At(" + ("value=" + at if at.startswith('"') else at) + ")")   # @At("X") is @At(value="X")
        out.append(f"@{m.group(1)} method={methods} " + " ".join(sorted(ats)))
    out += [f"@Overwrite {n}" for n in re.findall(r"@Overwrite\b(?:\s+@[\w.]+(?:\([^)]*\))?)*\s+(?:(?:public|private|"
                                                   r"protected|static|final)\s+)*[\w.<>\[\], ?]+\s+(\w+)\s*\(", t)]
    out += [f"@SubscribeEvent {e}" for e in re.findall(
        r"@SubscribeEvent\b(?:\s*\([^)]*\))?(?:\s+@[\w.]+(?:\([^)]*\))?)*\s+(?:(?:public|private|protected|static|final|"
        r"synchronized)\s+)*[\w.<>\[\], ?]+\s+\w+\s*\(\s*(?:final\s+)?([\w.$]+)", t)]
    return sorted(out)


def _hooks_of(repo, rel_file):
    """hook_targets of repo/rel_file; a file that is gone hooks nothing ([]); None when there is no repo to look in."""
    if repo is None:
        return None
    f = pathlib.Path(repo) / rel_file
    try:
        return hook_targets(f.read_text(encoding="utf-8", errors="replace")) if f.is_file() else []
    except OSError:
        return None


def stub_line(e):
    """One residue line for a STUBS.json entry: its file, its signals and, when an inherited review was reopened, why."""
    return f"stub: {e.get('file')}: {str(e.get('why', ''))[:200]}" + (f" -- {e['recheck'][:300]}" if e.get("recheck") else "")


def record_stubs(tdir, found, parents=(), repo=None):
    """Merge this run's stub signals into tdir/STUBS.json ([{file, why, reviewed, hooks}]) and return every entry. An
    entry a reviewer marked ("reviewed": "<why the change is right>") stays reviewed unless a later run flags the file
    again for a new reason. A NEW entry whose every signal, word for word (counts included), was already reviewed for
    the same file in a target this one inherits (parents: their target dirs, nearest first) takes that review, marked
    as inherited: the same loss re-flagged one version on is not a new question (measured: six of a target's eight).
    With repo (the fork checkout the entries' paths are relative to), each entry also records "hooks", the file's
    hook_targets() when it was recorded -- what a review of it is made against -- and an inherited review is taken
    only when this target's file hooks the same: otherwise the entry stays unreviewed with "inherited_review" and
    "recheck" ("re-justify: hook targets changed from ... to ..."). A parent entry recorded before "hooks" existed is
    compared through the parent's file on disk; only when that file is gone too is the hook set unknown, and the
    review is inherited as before. Without repo, nothing about hooks is read or written."""
    path = tdir / "STUBS.json"
    try:
        have = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    except ValueError:
        have = []
    by = {e["file"]: e for e in have}
    rel = lambda f: re.sub(r"/targets/[^/]+/", "/targets/*/", f)
    prior = {}
    for pd in parents:
        try:
            for e in json.loads((pd / "STUBS.json").read_text(encoding="utf-8")) if (pd / "STUBS.json").is_file() else []:
                if e.get("reviewed"):
                    prior.setdefault(rel(e["file"]), (pd.name, e))
        except ValueError:
            continue
    for s in found:
        e = by.get(s["file"])
        if e is None or s["why"] not in e.get("why", ""):
            why = s["why"] if e is None else e["why"] + "; " + s["why"]
            src = prior.get(rel(s["file"]))
            new = {"file": s["file"], "why": why, "reviewed": None}
            now = _hooks_of(repo, s["file"])
            if now is not None:
                new["hooks"] = now
            if src and all(part in src[1]["why"] for part in why.split("; ")):
                r = src[1]["reviewed"]
                r = r if r.startswith("(inherited") else f"(inherited from {src[0]}) {r}"
                then = src[1].get("hooks")
                if then is None and repo is not None and (pathlib.Path(repo) / src[1]["file"]).is_file():
                    then = _hooks_of(repo, src[1]["file"])      # recorded before "hooks": the parent's file as it is
                if now is not None and then is not None and sorted(then) != sorted(now):
                    new.update({"inherited_review": r, "recheck": f"re-justify: hook targets changed from "
                                f"{'; '.join(sorted(then)) or '(none)'} to {'; '.join(now) or '(none)'}"})
                else:
                    new["reviewed"] = r
            by[s["file"]] = new
        elif repo is not None and not e.get("reviewed") and not e.get("recheck"):
            now = _hooks_of(repo, s["file"])                   # still open: a review will be made against the file as it is now
            if now is not None:
                e["hooks"] = now
    out = sorted(by.values(), key=lambda e: e["file"])
    if out:
        tdir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    return out


def merge_shared_at(mv, target, at):
    """Add every access-transformer line the target's shared libraries need (.multiversion/shared/<v>/accesstransformer.cfg
    for each version in shared_chain) to the target's own AT, unless an equal line (comment aside) is there. A helper
    that extends a constructor vanilla made private compiles only with its line; inheriting the line from the previous
    target's AT misses a direct jump, which has no previous target. -> the lines added."""
    want = []
    for v in shared_chain(mv, target):
        f = mv / "shared" / v / "accesstransformer.cfg"
        if f.is_file():
            for ln in f.read_text(encoding="utf-8").splitlines():
                if not ln.strip() or ln.lstrip().startswith("#"):
                    continue
                # `# until <version>`: a line for a class that a later Minecraft removed reaches targets up to that version
                # only (measured: a 1.21.3 constructor line was merged into every target through 26.3)
                until = re.search(r"#.*\buntil (\d[\w.]*)", ln)
                if until and mc_key(target) > mc_key(until.group(1)):
                    continue
                want.append(ln)
    have = at.read_text(encoding="utf-8") if at.exists() else ""
    rule = lambda ln: " ".join(ln.split("#", 1)[0].split())
    known = {rule(ln) for ln in have.splitlines()}
    add = []
    for ln in want:
        if rule(ln) not in known:
            known.add(rule(ln)); add.append(ln)
    if add:
        at.parent.mkdir(parents=True, exist_ok=True)
        at.write_text(have + ("" if have.endswith("\n") or not have else "\n") + "\n".join(add) + "\n", encoding="utf-8")
    return add


SHARED_DIR = ROOT / "templates/fork-layer/shared"
ERRORS_PER_DOLLAR = 100      # measured hops cleared 76-144 errors per dollar of workers (issue #49); the guard's default


def skipped_shared(mv, target, shared_root=SHARED_DIR):
    """Shared libraries (versions) strictly between what `target` inherits (or the base) and itself that its shared chain
    does not include: a target that skips versions without `shared_chain` would miss their rows and helpers."""
    ch = chain(mv, target)
    low = ch[1] if len(ch) > 1 else read_props(mv / "base.properties").get("minecraft_version", "0")
    have = set(shared_chain(mv, target))
    vs = [p.name for p in shared_root.iterdir() if p.is_dir()] if shared_root.is_dir() else []
    return sorted((v for v in vs if mc_key(low) < mc_key(v) < mc_key(target) and v not in have), key=mc_key)


def spend_guard(errors, budget, max_errors=None, confirm=False):
    """None, or the reason the loop must not start: more errors than the budget can plausibly clear."""
    limit = max_errors if max_errors is not None else int(budget * ERRORS_PER_DOLLAR)
    if confirm or errors is None or errors <= limit:
        return None
    return (f"{errors} errors against a ${budget:g} budget (limit {limit} = {ERRORS_PER_DOLLAR} errors per dollar, the measured "
            f"range of earlier hops is 76-144)")


def parse_modid(toml_text):
    m = re.search(r'(?s)\[\[mods\]\].*?modId\s*=\s*"([^"]+)"', toml_text)
    return m.group(1) if m else None


def plain_froms(text):
    """The `from` of every plain (type-name) row of a rename table."""
    out = set()
    for l in text.splitlines():
        if not l.strip() or l.lstrip().startswith("#") or "\t" not in l:
            continue
        f = l.split("\t", 1)[0].strip()
        if not f.startswith(("re:", "member:")):
            out.add(f)
    return out


def moves_table(raw_rows, existing, prev, target):
    """The generated rename table. Rows whose `from` another table of the chain already carries are dropped:
    a duplicate `from` is a hard error at table load (CATALOG X31). The block is #!exhaustive (most moves are
    for classes this mod does not use) and CLOSED with #!strict -- an unclosed block exempts every later rule."""
    kept = [(a, b) for a, b in raw_rows if a not in existing]
    text = [f"# GENERATED by tools/fork-port.py: Minecraft/NeoForge classes that kept their simple name and moved",
            f"# package between {prev} and {target} (build-class-move-map.py over the two staged merged jars).",
            "# Derived from Mojang's data: regenerate (fork-port.py --from-stage moves), do not edit. Committed in the fork (its build reads it); never copied into the migrator's shared library",
            "# as a map. A real API change is never in here (it is in the .removed / .ambiguous lists).",
            "#!exhaustive  generated: most rows are for classes this mod does not use"]
    text += [f"{a}\t{b}" for a, b in kept]
    text.append("#!strict")
    return "\n".join(text) + "\n", len(kept), len(raw_rows) - len(kept)


def read_pairs(path):
    out = []
    for l in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        if "\t" in l and not l.startswith("#"):
            a, b = l.split("\t")[:2]
            out.append((a.strip(), b.strip()))
    return out


def layer_history(rel="templates/fork-layer/tools/layer.py"):
    """Every committed version of the template's layer.py (git log of the migrator), newest first; [] without git."""
    r = subprocess.run(["git", "-C", str(ROOT), "log", "--format=%H", "--", rel], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = []
    for h in r.stdout.split() if r.returncode == 0 else []:
        s = subprocess.run(["git", "-C", str(ROOT), "show", f"{h}:{rel}"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if s.returncode == 0:
            out.append(s.stdout)
    return out


def layer_patch(fork_text, template_text, history=(), need_shared_chain=False):
    """('current'|'replace'|'diverged', text). The fork's layer.py is the migrator's file copied in by `init`. It is
    replaced only when it is a verbatim OLDER copy of the template (the template minus the moves lines, or any version in
    `history`); anything else a human merges. A fork whose layer.py already reads the generated moves is `current` unless a
    target needs `shared_chain` (a direct jump) and the copy does not know it -- so a fork that does not jump is
    never touched."""
    if fork_text == template_text:
        return "current", fork_text
    if "moves.generated.tsv" in fork_text and not (need_shared_chain and "shared_chain" not in fork_text):
        return "current", fork_text
    if INSERT in template_text and fork_text == template_text.replace(INSERT, ""):
        return "replace", template_text
    if fork_text in set(history):
        return "replace", template_text
    return "diverged", fork_text


def below_java(p):
    return p.replace("\\", "/").rsplit("/java/", 1)[-1]


def loop_events(log, all_runs=False):
    """The events of the LAST run in a (resumable, appended-to) file-loop log; every run's with all_runs (stub signals:
    an earlier run's are still residue)."""
    p = pathlib.Path(log)
    if not p.is_file():
        return []
    ev = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    starts = [i for i, e in enumerate(ev) if e.get("event") == "start"]
    return ev[starts[-1]:] if starts and not all_runs else ev


def escalation(events, failing_files, top="opus"):
    """What the loop tried: files that reached the top model tier and still fail, rows it kept and reverted."""
    tiers = {}
    order = ["haiku", "sonnet", "opus"]
    for e in events:
        if e.get("event") == "worker":
            for f in e.get("files") or []:
                m = e.get("model")
                if m in order and order.index(m) >= order.index(tiers.get(below_java(f), "haiku")):
                    tiers[below_java(f)] = m
    still = sorted(f for f, m in tiers.items() if m == top and f in failing_files)
    reverted = [(e.get("row"), str(e.get("why"))[:100]) for e in events if e.get("event") == "rule" and not e.get("kept")]
    kept = [e.get("row") for e in events if e.get("event") == "rule" and e.get("kept")]
    spent = max([e.get("spent", 0) for e in events if "spent" in e] or [0])
    return {"top_tier_still_failing": still, "rows_kept": kept, "rows_reverted": reverted,
            "stubs": [s["file"] for e in events if e.get("event") == "stub-signals" for s in e.get("files", [])],
            "spent": spent}


def pins_dependencies(repo):
    """True when the authors' build.gradle pins a mod tools/layer-deps.py re-resolves per target (cursemaven, Modrinth's
    maven, or a sibling port the fork's CI installs into mavenLocal)."""
    b = repo / "build.gradle"
    text = b.read_text(encoding="utf-8", errors="replace") if b.is_file() else ""
    wf = repo / ".github/workflows/port-ci.yml"
    return bool(re.search(r"[\"'](curse\.maven|maven\.modrinth):", text)) or (
        wf.is_file() and "release into mavenLocal (pinned by sha256)" in wf.read_text(encoding="utf-8", errors="replace"))


def stop_block(stop):
    lines = [f"STOPPED at {stop['stage']}: {stop['left']} (spent ${stop.get('spent', 0)} here)."]
    lines += [f"  {d}" for d in stop.get("detail", [])]
    lines.append(f"Residue: {stop.get('residue', '(none)')}")
    lines.append("Choices (ask the person; do not pick one silently):")
    lines += [f"  - {c}" for c in stop["choices"]]
    return "\n".join(lines)


# A red gate that is the MACHINE's, not the code's (issue #113): tools/gate-loop.py's `killed` (the JVM was SIGKILLed with no
# verdict, usually the OOM killer) and its pre-Gate-C free-memory guard. Re-run when memory is free; never a fixer's job.
ENVIRONMENTAL = re.compile(r"JVM killed \(exit \d+|not enough free memory \(")


# Issue #122: a layer run's own Gradle invocations before Gate C (stage, measure, the loop's compiles, the audit's
# classpath, ci-gates' builds) left resident daemons and workers (measured: ~2.3 GB) and Gate C's free-memory guard then
# refused. Every Gradle this run starts runs daemon-less: each exits when its build ends, so nothing the run started is
# resident when the client needs the memory. Not `gradlew --stop`: that stops other checkouts' daemons too (#113).
NO_DAEMON = "-Dorg.gradle.daemon=false"


def gradle_env(env, layer):
    """-> the environment for the run's subprocesses: on a layer fork `env` with NO_DAEMON in GRADLE_OPTS (unless
    GRADLE_OPTS already sets org.gradle.daemon: the person's choice stands); otherwise None (inherit, unchanged)."""
    if not layer:
        return None
    env = dict(env)
    opts = env.get("GRADLE_OPTS", "")
    if "org.gradle.daemon" not in opts:
        env["GRADLE_OPTS"] = f"{opts} {NO_DAEMON}".strip()
    return env


GRADLE_MAINS = re.compile(r"(?:^|[./])(GradleDaemon|GradleWorkerMain|GradleMain|KotlinCompileDaemon|GradleStart\w*)$")


def memory_holders(proc="/proc", top=5):
    """-> the processes holding the most resident memory, largest first, as "PID 123 2300 MB <command>" ([] where
    there is no /proc). A Java command is named by its main class (GradleDaemon, GradleWorkerMain, ...) when it has one."""
    rows = []
    for d in pathlib.Path(proc).glob("[0-9]*"):
        try:
            st = (d / "status").read_text(encoding="utf-8", errors="replace")
            argv = [x for x in (d / "cmdline").read_bytes().decode("utf-8", "replace").split("\0") if x]
        except OSError:
            continue
        m = re.search(r"(?m)^VmRSS:\s+(\d+)\s*kB", st)
        if not m or not argv:
            continue
        main = next((g.group(1) for x in argv if (g := GRADLE_MAINS.search(x))), None)
        exe = pathlib.PurePath(argv[0]).name
        if not main and exe.startswith("java"):      # a JVM's flags say nothing: its main class (or -jar) does
            it = iter(argv[1:])
            for x in it:
                if x in ("-cp", "-classpath", "--class-path", "-p", "--module-path"):
                    next(it, None)
                elif x == "-jar":
                    main = "-jar " + pathlib.PurePath(next(it, "")).name
                    break
                elif not x.startswith("-") and not x.startswith("@"):
                    main = x
                    break
        cmd = " ".join((f"{exe} ... {main}" if main else " ".join([exe] + argv[1:])).split())
        rows.append((int(m.group(1)) // 1024, int(d.name), cmd[:100]))
    rows.sort(key=lambda r: (-r[0], r[1]))
    return [f"PID {pid} {mb} MB {cmd}" for mb, pid, cmd in rows[:top]]


def environmental_stop(failed, code, resume, free_now=None, holders=None):
    """-> the stop for gates that are ALL red for an environmental reason, or None when any is a code failure.
    `holders` (memory_holders()) names what held the memory, so the next step is obvious."""
    if not failed or not all(ENVIRONMENTAL.search(l) for l in failed):
        return None
    evidence = [l[:300] for l in failed]
    if free_now:
        evidence.append(f"free memory now: {free_now[0]} MB ({free_now[1]})")
    if holders:
        evidence.append("memory held by (largest resident first): " + "; ".join(holders))
    return {"stop": "environmental, not a code failure: " + "; ".join(l[:120] for l in failed[:2]), "code": code,
            "gates": [l[:200] for l in failed], "detail": evidence, "environment": evidence,
            "choices": [f"re-run when memory is free (stop other Gradle/Minecraft runs on this machine first): {resume}",
                        "do NOT send a fixer after the code or exclude a dependency for this: nothing in the code failed"]}


def residue_report(stage, left, errors_view, esc, audit, gates, resume, environment=None):
    """RESIDUE.md and its JSON twin. Bounded by construction: groups and the top files, never the raw log."""
    md = [f"# Residue: {stage}", "", f"Stopped at **{stage}**: {left}.", ""]
    if errors_view:
        md += ["## Remaining compile errors (compile-summary.py, grouped by CATALOG entry / family)", "", "```"]
        md += errors_view + ["```", ""]
    if esc:
        md += ["## The loop's record", ""]
        md += [f"- spent ${esc['spent']}"]
        md += [f"- still failing at the top tier: {', '.join(esc['top_tier_still_failing']) or 'none'}"]
        md += [f"- rows kept: {len(esc['rows_kept'])}"]
        md += [f"- row tried and reverted: `{r}` ({w})" for r, w in esc["rows_reverted"][:10]]
        if esc["stubs"]:
            md += [f"- STUB WARNING (a feature may have been deleted to clear an error): {', '.join(esc['stubs'][:10])}"]
        md.append("")
    if audit:
        md += ["## Mixin audit mismatches", "", "```"] + audit[:14] + ["```", ""]
    if environment:
        md += ["## Environmental: re-run when memory is free", "",
               "The run was killed or refused for lack of memory; nothing in the code failed. Do not send a fixer after "
               "it and do not exclude a dependency for it. Evidence:", ""] + [f"- {e}" for e in environment[:8]] + [""]
    elif gates:
        md += ["## Failing gates", ""] + [f"- {g}" for g in gates[:10]] + [""]
    md += ["## To resume", "", f"`{resume}`", "",
           "Re-run this when memory is free (stop other Gradle daemons and Minecraft runs on the machine first)."
           if environment else
           "Fix what is listed here, and put every fix in the shared library "
           "(templates/fork-layer/shared/<t>) or a converter, not in one fork.", ""]
    data = {"stage": stage, "left": left, "errors": errors_view, "loop": esc, "audit": audit, "gates": gates,
            "resume": resume}
    if environment:
        data["environment"] = environment
    return "\n".join(md), data


# ---------------------------------------------------------------- running things

class Run:
    def __init__(self, a):
        self.a = a
        self.repo = pathlib.Path(a.repo).resolve()
        self.mv = self.repo / ".multiversion"
        self.t = a.target
        self.tdir = self.mv / "targets" / self.t
        self.work = self.repo / "build" / f"mc{self.t}" / "fork-port"
        self.summary = {"repo": str(self.repo), "target": self.t, "stages": {},
                        "started": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")}
        self.jpath = pathlib.Path(a.json) if a.json else self.work / "fork-port.json"
        self.py = sys.executable
        self.env = gradle_env(os.environ, (self.mv / "base.properties").exists())    # None: not a layer fork, inherit

    def say(self, stage, msg):
        print(f"fork-port: {stage}: {msg}", flush=True)

    def sh(self, cmd, log=None, cwd=None):
        out = open(log, "w", encoding="utf-8") if log else subprocess.PIPE
        r = subprocess.run(cmd, cwd=cwd or self.repo, stdout=out, stderr=subprocess.STDOUT, text=True,
                           encoding="utf-8", errors="replace", env=self.env)
        if log:
            out.close()
        return r

    def gradle(self, tasks, log):
        init = [str(x) for x in ("--init-script", ROOT / "tools/maxerrs.init.gradle",
                                 "--init-script", ROOT / "tools/central-mirror.init.gradle")]
        return self.sh([BASH, str(self.mv / "build.sh"), self.t, *tasks, "--continue", "--console=plain", *init,
                        "-Dorg.gradle.jvmargs=-Xmx6g"], log)

    def count(self, log):
        """(errors or None, reason). BUILD SUCCESSFUL is 0; otherwise only burndown-count.sh may call it a count."""
        text = pathlib.Path(log).read_text(encoding="utf-8", errors="replace")
        if "BUILD SUCCESSFUL" in text.split(RECOUNT_MARK)[0]:       # the recount's own run SUCCEEDS: not a clean compile
            return 0, ""
        r = subprocess.run([BASH, str(ROOT / "tools/burndown-count.sh"), str(log)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            lines = r.stdout.strip().splitlines()      # the verdict, then (parse abort) the files that broke: keep both
            return None, " ".join(lines[:2] + [re.sub(r"^.*/mv/", "mv/", x) for x in lines[2:8]])
        m = re.search(r"errors = (\d+)", r.stdout)
        return (int(m.group(1)) if m else None), ""

    def artifacts(self):
        d = self.repo / "build" / f"mc{self.t}" / "moddev" / "artifacts"
        return sorted(d.glob("*merged.jar")), sorted(j for j in d.glob("*-sources.jar") if "resources" not in j.name)

    def resume_cmd(self, stage):
        a = self.a
        return (f"python3 tools/fork-port.py --repo {a.repo} --target {self.t} --from-stage {stage} "
                f"--budget {a.budget} --gates {a.gates}" + "".join(f" --port-checkout {c}" for c in getattr(a, "port_checkout", []))
                + "".join(f" --port-release {c}" for c in getattr(a, "port_release", []))
                + (" --allow-undone-library" if getattr(a, "allow_undone_library", False) else ""))

    def save(self):
        self.jpath.parent.mkdir(parents=True, exist_ok=True)
        self.jpath.write_text(json.dumps(self.summary, indent=1), encoding="utf-8")

    # ---- stages: each returns a result dict; a "stop" key ends the run

    def s_setup(self):
        if not (self.mv / "base.properties").exists():
            return {"stop": "no .multiversion/ in the fork", "code": 2,
                    "choices": [f"python3 tools/make-fork-layer.py init --repo {self.repo}"]}
        did = []
        if not (self.mv / "INVENTORY.json").is_file():
            # R2: the base build's behaviour inventory, once per layer (a layer made before it existed gets one here);
            # done (target_done) needs every item accounted for, so it is built before the parent's done check
            r = self.sh([self.py, str(ROOT / "tools/layer-inventory.py"), "build", "--repo", str(self.repo)])
            if r.returncode != 0:
                return {"stop": "layer-inventory.py build failed: " + " ".join((r.stdout or "").strip().splitlines()[-2:])[:300],
                        "code": 2, "choices": [f"python3 tools/layer-inventory.py build --repo {self.repo}"]}
            did.append("behaviour inventory: " + ((r.stdout or "").strip().splitlines() or ["built"])[-1]
                       .replace("layer-inventory: ", "").split(" -> ")[0] + " (commit .multiversion/INVENTORY.json)")
        if not self.tdir.is_dir():          # R1: never start target N+1 before N is done
            und = undone_parent_stop(self.repo, self.t, prepare=True)    # this run holds the checkout lock
            if und and not getattr(self.a, "allow_undone_parent", False):
                p, why = und
                return {"stop": f"{p}, the target {self.t} would stand on, is not done: {'; '.join(why[:3])}"[:600],
                        "code": UNDONE_EXIT, "detail": why[:8],
                        "choices": [f"finish {p} first: python3 tools/fork-port.py --repo {self.a.repo} --target {p} "
                                    "(and its review: fork-versions skill, \"Done\")",
                                    f"development only: the same command with --allow-undone-parent (recorded in {self.t}'s "
                                    f"target.properties; {self.t} is then not done until {p} is)"]}
            if und:
                did.append(f"WARNING parent {und[0]} not done, allowed (--allow-undone-parent, recorded)")
        allow_lib = getattr(self.a, "allow_undone_library", False)
        deps = [self.py, str(ROOT / "tools/layer-deps.py"), "resolve", "--repo", str(self.repo), "--target", self.t,
                *[x for c in getattr(self.a, "port_checkout", []) for x in ("--port-checkout", c)],
                *[x for c in getattr(self.a, "port_release", []) for x in ("--port-release", c)],
                *(["--allow-undone-library"] if allow_lib else [])]
        if allow_lib:     # R3's development escape: recorded in the summary here, in DEPS.md by layer-deps.py
            self.summary["allow_undone_library"] = True
        if pins_dependencies(self.repo):
            r = self.sh([*deps, "--dry-run"])
            if r.returncode == UNDONE_EXIT:
                return self.library_stop(r)
            if r.returncode == 15:
                stop = next((ln for ln in (r.stdout or "").splitlines() if "STOP" in ln), "a required dependency has no build")
                return {"stop": stop.replace("layer-deps: STOP ", "")[:600], "code": 15,
                        "choices": [f"skip {self.t}: the next target jumps over it (fork-port.py --direct)",
                                    "or find a build for it and pin it in the target's target.properties by hand"]}
            if r.returncode != 0:
                return {"stop": "layer-deps.py failed: " + " ".join((r.stdout or "").strip().splitlines()[-2:])[:300], "code": 2}
        if not self.tdir.is_dir():
            r = self.sh([self.py, str(ROOT / "tools/make-fork-layer.py"), "add-target", "--repo", str(self.repo),
                         "--target", self.t, *(["--direct"] if self.a.direct else []),
                         *(["--allow-undone-parent"] if getattr(self.a, "allow_undone_parent", False) else [])])
            if r.returncode != 0:
                return {"stop": "add-target refused: " + ((r.stdout or "").strip().splitlines() or ["?"])[-1][:300],
                        "code": UNDONE_EXIT if r.returncode == UNDONE_EXIT else 2,
                        "choices": ["make-fork-layer.py versions lists the targeted lines and why others are skipped"]}
            did.append("add-target")
        r = self.sh([self.py, str(ROOT / "tools/make-fork-layer.py"), "sync-shared", "--repo", str(self.repo)])
        did.append("sync-shared")
        div = [ln.split("DIVERGED ", 1)[1].split(" ", 1)[0] for ln in (r.stdout or "").splitlines() if "DIVERGED " in ln]
        if div:   # an edited machinery file is never written over: say so, and the layer_patch guard below decides layer.py
            did.append(f"left alone (edited, not a template copy -- merge by hand): {', '.join(div)}")
        # every table in the inherits chain: a row harvested from a parent target is a duplicate in this target's merge too
        gone = [r for x in chain(self.mv, self.t) if (self.mv / "targets" / x).is_dir() for r in drop_harvested_rows(self.mv, x)]
        if gone:
            did.append(f"{len(gone)} row(s) now in the shared library dropped from the target's table")
        shadow = drop_shadowing_overlays(self.mv, self.t)
        if shadow:
            did.append(f"{len(shadow)} overlay(s) identical to a shared helper dropped")
        n_rec, refused = layer_recipes(self.repo, self.tdir, self.t)
        if n_rec or refused:
            did.append(f"{n_rec} recipe(s) in the 1.21.2 ingredient form" + (f", {len(refused)} refused (Gate B will name them)"
                                                                               if refused else ""))
        n_pred, refused = layer_entity_predicates(self.repo, self.tdir, self.t, self.mv)
        if n_pred or refused:
            did.append(f"{n_pred} advancement/loot table(s) with 26.2 entity predicates"
                       + (f", {len(refused)} refused (Gate B will name them)" if refused else ""))
        n_263, refused = layer_263_data(self.repo, self.tdir, self.t, self.mv)
        if n_263 or refused:
            did.append(f"{n_263} file(s) in the 26.3 data form (block states, trim palettes, loot grammar)"
                       + (f", {len(refused)} refused (Gate B will name them)" if refused else ""))
        n_items = layer_client_items(self.repo, self.tdir, self.t)
        if n_items:
            did.append(f"{n_items} client item definition(s)")
        if pins_dependencies(self.repo):
            r = self.sh(deps)
            if r.returncode == UNDONE_EXIT:
                return self.library_stop(r)
            if r.returncode != 0:
                return {"stop": "layer-deps.py resolve: " + " ".join((r.stdout or "").strip().splitlines()[-2:])[:600],
                        "code": 15 if r.returncode == 15 else 2}
            did.append("layer-deps: " + ((r.stdout or "").strip().splitlines() or [""])[-1].split(": ", 2)[-1])
            if allow_lib and "LIBRARY NOT DONE" in (r.stdout or ""):
                did.append("WARNING built against a library target that is not done (--allow-undone-library, recorded "
                           "in DEPS.md; this target is not done until it is re-resolved with --port-release)")
        n_geo = layer_geckolib_assets(self.repo, self.tdir, self.t)
        if n_geo:
            did.append(f"{n_geo} GeckoLib model/animation file(s) at the 5.x paths")
        lp, tp = self.mv / "tools/layer.py", ROOT / "templates/fork-layer/tools/layer.py"
        need_sc = any("shared_chain" in read_props(self.mv / "targets" / x / "target.properties")
                      for x in chain(self.mv, self.t) if (self.mv / "targets" / x / "target.properties").is_file())
        fork_text = lp.read_text(encoding="utf-8")
        verdict, text = layer_patch(fork_text, tp.read_text(encoding="utf-8"),
                                    layer_history() if "moves.generated.tsv" not in fork_text or need_sc else (), need_sc)
        if verdict == "replace":
            lp.write_text(text, encoding="utf-8"); did.append("layer.py: replaced by the template's (shared_chain / generated-moves support)")
        elif verdict == "diverged":
            return {"stop": ".multiversion/tools/layer.py lacks " + ("shared_chain" if need_sc and "moves.generated.tsv" in fork_text
                                                                       else "moves.generated.tsv") +
                            " support and is not a verbatim older copy of the template, so it cannot be replaced safely",
                    "code": 2,
                    "choices": ["merge templates/fork-layer/tools/layer.py's changes (generated moves, shared_chain) into the fork's copy"]}
        warn = load("layer_sync", "layer-sync.py").inherited_rows_unsupported(self.mv)
        if warn:    # the key is set and the fork's (edited, so not replaced) layer.py ignores it: say so every run
            print(f"fork-port: {warn}", flush=True); did.append(warn)
        stale = load("layer_ci", "layer-ci.py").only_mixins_unsupported(self.repo, self.t)
        if stale:   # issue #119: an edited (so not synced) copy would keep an _only mixin on every later target in silence
            return {"stop": f"{self.t}'s chain uses mixin_add_only/mixin_remove_only and {', '.join(stale)} does not read them",
                    "code": 2, "choices": ["merge templates/fork-layer's layer.gradle / tools/layer.py (mixin_add_only) into the fork's copy"]}
        return {"done": ", ".join(did)}

    def library_stop(self, r):
        """layer-deps.py exit 18: a sibling library's target is not done (R3, library first). -> the stop, code 18."""
        stop = next((ln for ln in (r.stdout or "").splitlines() if "STOP" in ln), "a sibling library's target is not done")
        return {"stop": stop.replace("layer-deps: STOP ", "")[:600], "code": UNDONE_EXIT,
                "choices": ["finish and release the library's target first (its fork-port.py run to done, then its "
                            "layer-release), then resume with --port-release owner/repo@TAG",
                            "development only, against a --port-checkout: the same command with --allow-undone-library "
                            f"(recorded in DEPS.md; {self.t} is then not done until it is re-resolved with --port-release)"]}

    def s_stage(self):
        ch = chain(self.mv, self.t)
        at = self.tdir / "resources/META-INF/accesstransformer.cfg"
        if not at.exists():     # the nearest AT in the chain, else the authors': the target's file replaces them
            src = next((self.mv / "targets" / x / "resources/META-INF/accesstransformer.cfg" for x in ch[1:]
                        if (self.mv / "targets" / x / "resources/META-INF/accesstransformer.cfg").exists()),
                       self.repo / "src/main/resources/META-INF/accesstransformer.cfg")
            if src.exists():
                at.parent.mkdir(parents=True, exist_ok=True)
                at.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        merge_shared_at(self.mv, self.t, at)
        self.work.mkdir(parents=True, exist_ok=True)
        for rnd in range(1, STAGE_ROUNDS + 1):
            log = self.work / f"stage-{rnd}.log"
            self.gradle(["compileJava"], log)
            text = log.read_text(encoding="utf-8", errors="replace")
            merged, sources = self.artifacts()
            if "createMinecraftArtifacts FAILED" not in text and merged and sources:
                return {"done": f"Minecraft staged ({len(merged)} merged jar(s)), {rnd} round(s)", "rounds": rnd}
            if not at.exists():
                break
            # fix-access-transformer edits src/*/resources/META-INF/accesstransformer.cfg under --work: give it the
            # TARGET's file in a scratch tree, then take the result back. The authors' AT is never touched.
            with tempfile.TemporaryDirectory() as tmp:
                scratch = pathlib.Path(tmp) / "src/main/resources/META-INF/accesstransformer.cfg"
                scratch.parent.mkdir(parents=True)
                scratch.write_text(at.read_text(encoding="utf-8"), encoding="utf-8")
                r = self.sh([self.py, str(ROOT / "tools/fix-access-transformer.py"), "--work", tmp,
                             "--overrides-from", str(log)])
                added = re.search(r"(\d+) override widening", r.stdout or "")
                if not added or int(added.group(1)) == 0:
                    break
                at.write_text(scratch.read_text(encoding="utf-8"), encoding="utf-8")
        tail = [l for l in text.splitlines() if "What went wrong" in l or "error" in l.lower()][:4]
        return {"stop": "Minecraft would not stage (createMinecraftArtifacts)", "code": 10, "detail": tail,
                "choices": ["read build/mc%s/fork-port/stage-*.log for the first failure, fix the access "
                            "transformer by hand in targets/%s/resources/META-INF/" % (self.t, self.t)]}

    def s_moves(self):
        ch = chain(self.mv, self.t)
        prev = ch[1] if len(ch) > 1 else read_props(self.mv / "base.properties").get("minecraft_version", "base")
        if len(ch) > 1:
            old_dir = self.repo / "build" / f"mc{prev}" / "moddev" / "artifacts"
        else:
            old_dir = self.repo / "build" / "moddev" / "artifacts"
        old = sorted(old_dir.glob("*merged.jar"))
        if not old:     # the previous target (or, on a direct jump / first target, the base) was never staged here
            self.sh([BASH, str(self.mv / "build.sh"), prev, "createMinecraftArtifacts", "--console=plain"],
                    self.work / "stage-prev.log")
            old = sorted(old_dir.glob("*merged.jar"))
        new, _ = self.artifacts()
        if not old or not new:
            return {"stop": f"no staged merged jar for {prev if not old else self.t}", "code": 10,
                    "choices": [f"stage it: .multiversion/build.sh {prev if not old else self.t} createMinecraftArtifacts"]}
        self.work.mkdir(parents=True, exist_ok=True)
        cps = []
        for name, jars in (("old", old), ("new", new)):
            p = self.work / f"cp-{name}.txt"
            p.write_text("\n".join(str(j) for j in jars) + "\n", encoding="utf-8")
            cps.append(p)
        raw = self.work / f"moves-{prev}-to-{self.t}.tsv"
        r = self.sh([self.py, str(ROOT / "tools/build-class-move-map.py"), "--from-cp", str(cps[0]), "--to-cp",
                     str(cps[1]), "--out", str(raw)])
        if r.returncode != 0 or not raw.exists():
            return {"stop": "build-class-move-map.py failed: " + (r.stdout or "").strip()[-200:], "code": 10,
                    "choices": ["see build-class-move-map.py's usage; check both jars hold net.minecraft classes"]}
        existing = set()
        for x in dict.fromkeys(ch + shared_chain(self.mv, self.t)):      # a direct jump's shared tables count too (X31)
            for f in (self.mv / "targets" / x / "renames.tsv", self.mv / "shared" / x / "renames.tsv"):
                if f.is_file():
                    existing |= plain_froms(f.read_text(encoding="utf-8"))
        text, kept, dup = moves_table(read_pairs(raw), existing, prev, self.t)
        gen = self.tdir / "moves.generated.tsv"
        gen.write_text(text, encoding="utf-8")
        at = self.tdir / "resources/META-INF/accesstransformer.cfg"
        fixed = ""
        if at.is_file():    # an AT entry for a class vanilla moved matches nothing; rewrite those to the new names
            with tempfile.TemporaryDirectory() as tmp:
                scratch = pathlib.Path(tmp) / "src/main/resources/META-INF/accesstransformer.cfg"
                scratch.parent.mkdir(parents=True)
                scratch.write_text(at.read_text(encoding="utf-8"), encoding="utf-8")
                self.sh([self.py, str(ROOT / "tools/fix-access-transformer.py"), "--work", tmp, "--class-map", str(raw)])
                new_at = scratch.read_text(encoding="utf-8")
                if new_at != at.read_text(encoding="utf-8"):
                    at.write_text(new_at, encoding="utf-8"); fixed = ", access transformer re-pointed at moved classes"
        return {"done": f"{kept} unambiguous move(s) {prev}->{self.t} -> {gen.relative_to(self.repo)}"
                        f"{f' ({dup} already in a table)' if dup else ''}{fixed}", "moves": kept}

    def s_measure(self):
        ss = load("srcsets", "srcsets.py")
        tasks = ss.compile_tasks(self.repo)
        log = self.work / "measure.log"
        self.work.mkdir(parents=True, exist_ok=True)
        self.gradle(tasks, log)
        capped = capped_tasks(pathlib.Path(log).read_text(encoding="utf-8", errors="replace"))
        if capped:
            # the capped compile is not a count (tools/maxerrs.init.gradle): re-run the same javac uncapped and append
            # it to the same log, as tools/port_gates.py compile_log() does (measured: a 26.3 hop stopped here)
            full = self.work / "measure.full.log"
            self.gradle(["portFullErrors", "-PportFullErrorsTasks=" + ",".join(capped)], full)
            with open(log, "a", encoding="utf-8") as fh:
                fh.write("\n" + RECOUNT_MARK + "\n")
                fh.write(full.read_text(encoding="utf-8", errors="replace"))
        n, why = self.count(log)
        if n is None:
            return {"stop": "the compile does not count: " + why, "code": 10, "errors": None, "detail": [why],
                    "choices": ["read build/mc%s/fork-port/measure.log; fix what keeps javac from attributing" % self.t]}
        sk = skipped_shared(self.mv, self.t)
        if sk:
            self.say("measure", f"WARNING {self.t} skips shared librar{'y' if len(sk) == 1 else 'ies'} {', '.join(sk)} "
                                f"and has no shared_chain: their rows and helpers are NOT applied (add-target --direct)")
        return {"done": f"{n} error(s) across {len(tasks)} compile task(s) (starting count)", "errors": n, "log": str(log), "this_run": True}

    def error_view(self, log):
        cs = load("compile_summary", "compile-summary.py")
        text = pathlib.Path(log).read_text(encoding="utf-8", errors="replace")
        code, why = cs.validate(log)
        if code != 0:
            return [f"NOT A COUNT: {w}" for w in why[:3]], set()
        s = cs.summarise(text, cs.rb.signatures())
        return cs.render(s, None, 8, 8), set(s["files"])

    def s_loop(self):
        start = self.compile_errors()      # a count THIS run measured; a resume re-measures rather than trusting the last run's
        if start == 0:
            # an earlier run's loop may have left stub signals: they are still residue at 0 errors
            flog = self.work / "file-loop.jsonl"
            open_ = [e for e in record_stubs(self.tdir, [x for e in (loop_events(flog, all_runs=True) if flog.is_file() else [])
                                                         if e.get("event") == "stub-signals" for x in e.get("files", [])]
                                                    + overlay_stubs(self.repo, self.tdir),
                              parents=[self.mv / "targets" / x for x in chain(self.mv, self.t)[1:]], repo=self.repo)
                     if not e.get("reviewed")]
            if open_:
                detail = [stub_line(e) for e in open_]
                return {"stop": f"0 errors, but {len(open_)} unreviewed stub signal(s) from earlier runs (STUBS.json)",
                        "code": 10, "errors": 0, "detail": detail, "view": detail,
                        "choices": ["port each flagged file's lost behaviour, or set its \"reviewed\" in "
                                    "targets/<t>/STUBS.json to why the change is right; then resume with --from-stage loop"]}
            return {"skipped": "already at 0 errors", "errors": 0}
        why = spend_guard(start, self.a.budget, self.a.max_errors, self.a.confirm_spend)
        if why:
            skipped = skipped_shared(self.mv, self.t)
            return {"stop": "spend guard: " + why, "code": 10, "errors": start, "this_run": True,
                    "detail": [why] + ([f"the target skips shared librar{'y' if len(skipped) == 1 else 'ies'} {', '.join(skipped)}: "
                                        f"a direct jump needs shared_chain (add-target --direct)"] if skipped else []),
                    "choices": [f"raise it on purpose: {self.resume_cmd('loop')} --confirm-spend (or --max-errors N / a larger --budget)",
                                "a jump that skips versions: add shared_chain= (make-fork-layer.py add-target --direct) so the shared "
                                "libraries clear most of these errors for free"]}
        flog = self.work / "file-loop.jsonl"
        out = self.work / "file-loop.out"
        r = self.sh([self.py, str(ROOT / "tools/file-loop.py"), "--work", str(self.repo), "--layer", self.t,
                     "--budget", str(self.a.budget), "--log", str(flog)], out)
        ev = loop_events(flog)
        end = next((e for e in reversed(ev) if e.get("event") == "end"), None)
        n = end.get("errors") if end else None
        clog = self.repo / "build" / f"mc{self.t}" / "file-loop-compile.log"
        res = {"errors": n, "start_errors": start, "exit": r.returncode, "log": str(flog), "this_run": True}
        # every run's stub signals are kept in targets/<t>/STUBS.json until someone reviews them: a resumed run used to
        # forget the previous run's (measured: ten flagged files vanished from RESIDUE.md on the next --from-stage loop)
        stubs_all = record_stubs(self.tdir, [s for e in loop_events(flog, all_runs=True) if e.get("event") == "stub-signals"
                                             for s in e.get("files", [])] + overlay_stubs(self.repo, self.tdir),
                              parents=[self.mv / "targets" / x for x in chain(self.mv, self.t)[1:]], repo=self.repo)
        if n == 0:
            # 0 errors is not done when a worker got there by emptying code or by forking the shared library: the
            # loop's own stub audit, and any overlay of a shared helper, are residue (measured on the first
            # unattended target: both happened at "0 errors" and the run went on to the audit)
            stubs = [s for s in stubs_all if not s.get("reviewed")]
            shared = self.shared_helper_overlays()
            if stubs or shared:
                detail = [stub_line(s) for s in stubs] + \
                         [f"shared helper overlaid in this fork: {f}" for f in shared]
                res.update({"stop": f"0 errors, but {len(stubs)} stub signal(s) and {len(shared)} overlay(s) of the "
                                    f"shared library", "code": 10, "detail": detail, "view": detail,
                            "spent": end.get("spent", 0),     # the loop did spend to get to 0: report it, not $0
                            "choices": ["port each flagged method to the new signature (never drop @Override), or, when the "
                                    "change is right, set its \"reviewed\" in targets/<t>/STUBS.json to why",
                                        "fix a shared helper in templates/fork-layer/shared/<t> and delete the "
                                        "fork's overlay of it; then resume with --from-stage loop"]})
                return res
            res["done"] = f"{start} -> 0 errors, ${end.get('spent', 0)} of workers"
            return res
        view, files = self.error_view(clog) if clog.is_file() else ([], set())
        esc = escalation(ev, files)
        res.update({"stop": f"{n} compile errors" if n is not None else "the loop ended without a count (file-loop.out)",
                    "code": 10, "spent": esc["spent"], "view": view, "esc": esc, "detail": view[:6],
                    "choices": [f"continue: {self.resume_cmd('loop')} with a higher --budget",
                                "fix the listed groups by hand, as shared rows / helpers (not in this fork)"]})
        return res

    def shared_helper_overlays(self):
        """Overlays (this target's java/) of a class the shared library supplies: a fix there belongs in the library."""
        mv_pkg = read_props(self.repo / ".multiversion/base.properties").get("mv_package", "")
        names = {p.name for p in (self.repo / ".multiversion/shared").glob("*/java/**/*.java")}
        root = self.tdir / "java" / mv_pkg.replace(".", "/")
        return sorted(str(p.relative_to(self.repo)) for p in root.glob("*.java") if p.name in names) if mv_pkg else []

    def compile_errors(self):
        """The compile count the audit and gates rely on, FROM THIS RUN. A count carried over from an earlier run's
        summary is not trusted: measured, a resume from 'audit' read a stale count, skipped the audit and every
        gate, and reported the target done. So unless this run measured (or looped), measure again now."""
        st = self.summary["stages"]
        for k in ("loop", "measure"):
            v = st.get(k, {})
            if v.get("this_run") and isinstance(v.get("errors"), int):
                return v["errors"]
        res = self.s_measure()
        res["this_run"] = True
        st["measure"] = {k: v for k, v in res.items() if k not in ("view", "esc", "stop", "choices", "code", "detail")}
        self.say("measure", res.get("done") or res.get("stop") or "re-measured")
        return res.get("errors")

    def not_compiled(self, n, stage):
        """Never skip the audit or the gates quietly: a target that does not compile is not done."""
        return {"stop": f"{stage} needs a clean compile; this run measured {n if n is not None else 'no count'}",
                "code": 10, "detail": [f"compile errors: {n}"],
                "choices": [f"resume from measure: {self.resume_cmd('measure')}"]}

    def s_audit(self):
        n = self.compile_errors()
        if n != 0:
            return self.not_compiled(n, "audit")
        neo = read_props(self.tdir / "target.properties").get("prop.neo_version", "")
        jars = [j for j in self.artifacts()[1] if neo in j.name] or self.artifacts()[1]
        if not jars:
            return {"stop": "no staged sources jar to audit against", "code": 13, "choices": ["stage Minecraft first"]}
        prepared = self.repo / "build" / f"mc{self.t}" / "mv" / "java"
        r = self.sh([self.py, str(ROOT / "tools/audit-mixin-targets.py"), str(self.repo), str(jars[0]),
                     f"--sources={prepared}"])
        lines = (r.stdout or "").strip().splitlines()
        if r.returncode == 0:
            if read_props(self.tdir / "target.properties").get("strip_onlyin") == "true" or any(
                    read_props(self.mv / "targets" / x / "target.properties").get("strip_onlyin") == "true"
                    for x in chain(self.mv, self.t)[1:]):
                hits = client_refs_in_common(self.repo, prepared)
                if hits:
                    return {"stop": f"{len(hits)} client lookup(s) in common classes will crash a dedicated server once "
                                    "@OnlyIn is stripped (CATALOG M52)", "code": 13, "audit": hits, "detail": hits[:6],
                            "choices": ["in each overlay: call @MVPKG@.ClientRefs.player()/level() (the shared rows' form), "
                                        "or move the lookup into a nested client-only class"]}
            ev = self.audit_event_listeners()
            if ev.get("stop"):
                return ev
            return {"done": ((lines[-1] if lines else "all targets match")[:120] + "; " + ev["done"])[:200]}
        if r.returncode == 2 or any("NO TARGETS CHECKED" in l for l in lines):
            # the audit looked at nothing: a scope fault in this run, never a verdict on the mixins (§X27)
            return {"stop": "the mixin audit checked no targets (wrong jar or tree?) -- not a pass",
                    "code": 13, "audit": lines[:8], "detail": lines[:4],
                    "choices": ["fix the audit's inputs in tools/fork-port.py and resume with --from-stage audit"]}
        # keep each mismatch WITH its header line (the mixin file and the annotated target): a "mixin:/vanilla:"
        # pair alone does not say which file to fix -- measured, the first residue report could not be acted on
        bad = [l for l in lines if l.strip() and not l.strip().startswith(("ok ", "checked ")) and "all targets" not in l] or lines
        return {"stop": f"{len([l for l in lines if 'mixin:' in l]) or len(bad)} mixin target(s) do not match vanilla",
                "code": 13, "audit": bad, "detail": bad[:6],
                "choices": ["retarget each mixin in a per-target overlay or with mixin_add, or keep it to one target with mixin_add_only / mixin_remove_only (fork-versions step 5)",
                            "a recurring shape belongs in the shared library"]}

    def target_classpath(self):
        """Gradle's own resolved compile classpath for this layer target, cached under the run's work dir (the code
        is tools/layer-ci.py's, shared with the fork's layer CI). Never rebuilt by hand (X25b-ii)."""
        return load("layer_ci", "layer-ci.py").target_classpath(
            self.repo, self.t, self.work, [ROOT / "tools/central-mirror.init.gradle"], BASH)

    def audit_event_listeners(self):
        """audit-event-listeners.py (CATALOG X33/X42) over every prepared tree of the target, against its own classpath:
        a listener naming an ABSTRACT event compiles everywhere and kills mod loading (measured on 1.21.8)."""
        trees = sorted(str(p) for p in (self.repo / "build" / f"mc{self.t}" / "mv").glob("*java") if p.is_dir())
        if not trees:
            return {"stop": "no prepared tree to audit event listeners in", "code": 13,
                    "choices": ["build the target once so build/mc<t>/mv exists"]}
        cp, why = self.target_classpath()
        if cp is None:
            return {"stop": "event-listener audit: " + why, "code": 13, "detail": [why],
                    "choices": ["fix the classpath resolution; this audit refuses to run on a guessed one"]}
        r = self.sh([self.py, str(ROOT / "tools/audit-event-listeners.py"), str(self.repo), f"--cp-file={cp}",
                     f"--tree={','.join(trees)}", f"--mc={self.t}"])
        lines = (r.stdout or "").strip().splitlines()
        if r.returncode == 0:
            return {"done": (lines[0] if lines else "event listeners ok")[:100]}
        if r.returncode == 2:
            return {"stop": "the event-listener audit checked no listeners (wrong tree or classpath?) -- not a pass",
                    "code": 13, "audit": lines[:8], "detail": lines[:4],
                    "choices": ["fix the audit's inputs in tools/fork-port.py and resume with --from-stage audit"]}
        bad = [l for l in lines if l.strip() and not l.startswith("checked ")]
        return {"stop": f"{sum('ABSTRACT' in l for l in bad)} event listener(s) name an abstract event "
                        f"(the bus refuses them at load)",
                "code": 13, "audit": bad, "detail": bad[:6],
                "choices": ["port each listener to a concrete subclass of the event in a per-target overlay "
                            "(CATALOG X33: check the subclass still carries what the listener uses)"]}

    def modid(self):
        if self.a.modid:
            return self.a.modid
        f = self.repo / "src/main/resources/META-INF/neoforge.mods.toml"
        return parse_modid(f.read_text(encoding="utf-8")) if f.is_file() else None

    def s_gates(self):
        if self.a.gates == "none":
            return {"skipped": "--gates none"}
        n = self.compile_errors()
        if n != 0:
            return self.not_compiled(n, "gates")
        modid = self.modid()
        if not modid:
            return {"stop": "cannot read the mod id from neoforge.mods.toml (pass --modid)", "code": 2, "choices": ["--modid ID"]}
        summ = self.work / "ci-gates-summary.md"
        # a fresh gate work dir every run: ci-gates reuses an existing harness, and a harness generated by an
        # older version of the tooling (measured: one from before the 1.21.5 GameTest adapter) fails the same way
        # forever while the tool that would fix it is never asked
        shutil.rmtree(self.work / "gates", ignore_errors=True)
        cmd = [self.py, str(ROOT / "tools/ci-gates.py"), "--repo", str(self.repo), "--modid", modid, "--base",
               self.a.base, "--mc", self.t, "--summary", str(summ), "--work-dir", str(self.work / "gates")]
        if self.a.gates == "ab":
            cmd += ["--gatec", ""]
        if self.shipped_expected():    # the fork lists what its shipped jar must log
            cmd += ["--shipped"]
        r = self.sh(cmd, self.work / "ci-gates.log")
        if self.a.gates == "full":
            # a full run that is not green leaves no Gate C record behind: an older GATES.md would make the next
            # `--gates auto` drop to A/B and report the target done on a client that just failed
            (self.tdir / "GATES.md").unlink(missing_ok=True)
        lines = (self.work / "ci-gates.log").read_text(encoding="utf-8", errors="replace").splitlines()
        rows = [re.sub(r"^\[ci-gates\] ", "", l) for l in lines if l.startswith("[ci-gates]") and ": " in l]
        failed = [l for l in rows if re.search(r": FAIL\b", l)]
        passed = [l for l in rows if re.search(r": PASS\b", l)]
        if r.returncode == 0:
            lost, prev = self.lost_logs()
            if lost:
                msg = "; ".join(f"the mod wrote {f} on {prev} and not on {self.t}: a subsystem stopped running" for f in lost)
                return {"stop": f"gates green but {msg}", "code": 11, "gates": [f"log file {f} (written on {prev}, absent on {self.t})" for f in lost],
                        "detail": [msg[:240]],
                        "choices": ["find what writes that file (grep the source for its name), and why it no longer runs on this target "
                                    "(CATALOG X47: a registration the new loader ignores is the usual cause)",
                                    "if the file is gone on purpose, list it in .multiversion/targets/%s/logs-known-absent.txt with the reason" % self.t]}
            note = f"; log files match {prev}" if prev else ""
            if self.a.gates == "full" and summ.is_file():
                # the record port-publish.py's layer path reads (and `--gates auto` reads): this target had its real-client run
                (self.tdir / "GATES.md").write_text(summ.read_text(encoding="utf-8"), encoding="utf-8")
                note += "; recorded in GATES.md"
            # green gates are not done while a stub signal is unreviewed: only the loop stage asked, so a run resumed
            # past it (--from-stage audit/gates) reported a target done with one open (measured on a 26.x target)
            open_ = unreviewed_stubs(self.tdir)
            if open_:
                detail = [stub_line(e) for e in open_]
                return {"stop": f"{len(passed)} gate(s) green{note}, but {len(open_)} unreviewed stub signal(s) in STUBS.json",
                        "code": 10, "detail": detail, "view": detail,
                        "choices": ["port each flagged method to the new signature, or, when the change is right, set its "
                                    "\"reviewed\" in targets/<t>/STUBS.json to why; then resume with --from-stage loop"]}
            return {"done": f"{len(passed)} gate(s) green ({self.a.gates}){note}"}
        code = 12 if any("Gate C" in l for l in failed) else 11
        linux = sys.platform.startswith("linux")
        env = environmental_stop(failed, code, self.resume_cmd("gates"),
                                 load("gl", "gate-loop.py").mem_available_mb() if linux else None,
                                 memory_holders() if linux else None)
        if env:
            return env
        evidence = []       # why each red gate is red: its own log's first compile errors / failing tests
        for l in failed[:2]:
            lp = re.search(r"see (\S+?)(?:[;)\s]|$)", l)
            lp = pathlib.Path(lp.group(1)) if lp else None
            if lp and lp.is_file():
                evidence += [f"{l.split(':')[0][:40]}: {e}" for e in gate_evidence(lp.read_text(encoding="utf-8", errors="replace"))]
        return {"stop": f"{len(failed) or 'a'} gate(s) red: " + "; ".join(l[:90] for l in failed[:2]), "code": code,
                "gates": [l[:200] for l in failed], "detail": [l[:200] for l in failed[:4]] + [e[:240] for e in evidence],
                "choices": ["run the same gates without --mc as the control: a red that is red on the base is not this target's",
                            "read build/mc%s/fork-port/ci-gates.log and the gate's own log" % self.t]}

    def lost_logs(self):
        """(files, previous target) -- the mod's own log files from the inherited target's last gate run that this run lacks."""
        inh = read_props(self.tdir / "target.properties").get("inherits")
        if not inh:
            return [], None
        prev = self.repo / "build" / f"mc{inh}" / "fork-port" / "gates" / "harness" / "run" / "logs"
        if not prev.is_dir():
            return [], None
        known = pathlib.Path(self.tdir / "logs-known-absent.txt")
        absent = [l.split("#", 1)[0].strip() for l in known.read_text(encoding="utf-8").splitlines()] if known.is_file() else []
        return lost_log_files(prev, self.work / "gates" / "harness" / "run" / "logs", [a for a in absent if a]), inh

    def s_cost(self):
        modid = self.modid()
        if not modid:
            return {"skipped": "no mod id"}
        f = self.tdir / "COST.md"
        old = f.read_text(encoding="utf-8") if f.is_file() else ""
        # port-cost charges each request to one target by what it touched, so the window need not be this run's: it
        # starts where the target's last record ended (none: every request on this machine that worked on the target),
        # which counts a fixer's own work between runs once, and never a resumed run's requests twice
        since = cost_since(old)
        cmd = [self.py, str(ROOT / "tools/port-cost.py"), modid, "--path", str(self.repo), "--target", self.t, "--print"]
        r = self.sh(cmd + (["--since", since] if since else []))
        if r.returncode != 0 or not (r.stdout or "").strip():
            return {"skipped": "no transcripts for this checkout (port-cost exit %d)" % r.returncode}
        extra = loop_spend_line(loop_events(self.work / "file-loop.jsonl")) if self.summary["stages"].get("loop", {}).get("this_run") else ""
        f.write_text(old.rstrip("\n") + ("\n\n" if old else "") + f"## fork-port.py run since {self.summary['started']}\n\n"
                     + r.stdout.strip() + "\n" + extra, encoding="utf-8")
        return {"done": f"appended to {f.relative_to(self.repo)}"}


    # ---- what the end of a run asks (a version branch's BranchRun answers from tools/fork-branches.py)

    def done(self, prepare=False):
        return target_done(self.repo, self.t, prepare=prepare)

    def shipped_expected(self):
        return any((self.repo / ".multiversion").glob("shipped-expect*.txt"))

    def skeleton(self):
        return review_skeleton(self.repo, self.tdir, self.t)

    def test_items(self):
        return write_test_items(self.repo, self.tdir, self.t)

    def review_note(self, added):
        return (f"REVIEW.md lists every item{f', {added} new' if added else ''}: "
                f"{review_counts(self.repo, self.tdir, review_items(self.repo, self.tdir))}; STUBS.json, the overlays and the "
                "authors' files they replace")

    def drift(self, a):
        return drift_stop(self.repo, a)

    def records_rel(self):
        return f".multiversion/targets/{self.t}/"


# ---------------------------------------------------------------- a version branch (tools/fork-branches.py)
#
# A fork migrated to stacked version branches (or started as one) has no .multiversion/: version t is the branch
# neoforge-<t>, a plain single-version build at Minecraft t, with its records in .port/<t>/. The same stages run on it
# -- setup, stage, moves, measure, loop, audit, gates, cost -- on the checked-out branch's own tree: the compile loop
# edits src/ (file-loop.py's plain mode), the generated class moves are applied to the source as rows and kept in
# .port/<t>/moves.generated.tsv, the gates are ci-gates.py --mc <t> (its branch mode), and the done list is
# tools/fork-branches.py target_done(). Nothing here runs for a single-version fork (no version branch) or a layer.

def fork_branches():
    return load("fork_branches", "fork-branches.py")


def branch_mode(repo, t):
    """Is (repo, t) a version branch run? -- no layer at HEAD, and the checkout is on t's version branch or the clone
    has version branches (t's own, or others t would be added beside)."""
    repo = pathlib.Path(repo)
    if (repo / ".multiversion/base.properties").is_file():
        return False
    fb = fork_branches()
    return fb.checkout_version(repo) == t or bool(fb.version_branches(repo))


class BranchRun(Run):
    def __init__(self, a):
        super().__init__(a)
        self.fb = fork_branches()
        self.tdir = self.repo / ".port" / self.t
        self.env = gradle_env(os.environ, True)

    def props(self):
        return self.fb.info(self.repo, self.t)

    def gradle(self, tasks, log):
        init = [str(x) for x in ("--init-script", ROOT / "tools/maxerrs.init.gradle",
                                 "--init-script", ROOT / "tools/central-mirror.init.gradle")]
        return self.sh([BASH, "gradlew", *tasks, "--continue", "--console=plain", *init, "-Dorg.gradle.jvmargs=-Xmx6g"], log)

    def artifacts(self, root=None):
        d = (root or self.repo) / "build" / "moddev" / "artifacts"
        return sorted(d.glob("*merged.jar")), sorted(j for j in d.glob("*-sources.jar") if "resources" not in j.name)

    def records_rel(self):
        return f".port/{self.t}/"

    def s_setup(self):
        did = []
        fb = self.fb
        if fb.checkout_version(self.repo) != self.t:
            if not fb.resolve_branch(self.repo, fb.branch_name(self.t)):
                rc_out = []
                rc = fb.add_target(self.repo, self.t, allow_undone_parent=getattr(self.a, "allow_undone_parent", False),
                                   say=rc_out.append)
                if rc:
                    return {"stop": "add-target refused: " + " ".join(rc_out)[:600], "detail": rc_out[:8],
                            "code": UNDONE_EXIT if rc == UNDONE_EXIT else 2,
                            "choices": ["finish the version it stands on first (fork-versions skill, \"Done\"), or for "
                                        "development only --allow-undone-parent (recorded)"]}
                did.append("add-target: " + (rc_out[-1].split(": ", 1)[-1][:160] if rc_out else "made"))
            r = subprocess.run(["git", "-C", str(self.repo), "checkout", "-q", fb.branch_name(self.t)], capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            if r.returncode or fb.checkout_version(self.repo) != self.t:
                return {"stop": f"cannot check out {fb.branch_name(self.t)}: {(r.stderr or '').strip()[-300:]}", "code": 2,
                        "choices": ["commit or stash the checkout's changes, then resume"]}
            did.append(f"checked out {fb.branch_name(self.t)}")
        if not (self.repo / ".port/INVENTORY.json").is_file():
            r = self.sh([self.py, str(ROOT / "tools/layer-inventory.py"), "build", "--repo", str(self.repo)])
            if r.returncode != 0:
                return {"stop": "layer-inventory.py build failed: " + " ".join((r.stdout or "").strip().splitlines()[-2:])[:300],
                        "code": 2, "choices": [f"python3 tools/layer-inventory.py build --repo {self.repo}"]}
            did.append("behaviour inventory built from the port branch (commit .port/INVENTORY.json)")
        allow_lib = getattr(self.a, "allow_undone_library", False)
        deps = [self.py, str(ROOT / "tools/layer-deps.py"), "resolve", "--repo", str(self.repo), "--target", self.t,
                *[x for c in getattr(self.a, "port_checkout", []) for x in ("--port-checkout", c)],
                *[x for c in getattr(self.a, "port_release", []) for x in ("--port-release", c)],
                *(["--allow-undone-library"] if allow_lib else [])]
        if allow_lib:
            self.summary["allow_undone_library"] = True
        if pins_dependencies(self.repo):
            r = self.sh(deps)
            if r.returncode == UNDONE_EXIT:
                return self.library_stop(r)
            if r.returncode != 0:
                return {"stop": "layer-deps.py resolve: " + " ".join((r.stdout or "").strip().splitlines()[-2:])[:600],
                        "code": 15 if r.returncode == 15 else 2}
            did.append("layer-deps: " + ((r.stdout or "").strip().splitlines() or [""])[-1].split(": ", 2)[-1])
        n = self.data_forms()
        if n:
            did.append(f"{n} resource file(s) written in {self.t}'s data forms")
        return {"done": ", ".join(did) or "on its branch"}

    def data_forms(self):
        """The layer's data conversions (1.21.2 recipes and item definitions, 26.2 entity predicates, 26.3 data, GeckoLib
        5 asset paths), written into the branch's own src/main/resources: each reads the branch's resources (already in
        its parent's form, so a file already converted is left as it is) and writes what this version needs."""
        n = 0
        with tempfile.TemporaryDirectory() as d:
            mv = pathlib.Path(d) / "mv"
            tdir = mv / "targets" / self.t
            tdir.mkdir(parents=True)
            (tdir / "target.properties").write_text(self.fb.read_file(self.tdir / "branch.properties"), encoding="utf-8")
            if (self.tdir / "DEPS.md").is_file():
                (tdir / "DEPS.md").write_text(self.fb.read_file(self.tdir / "DEPS.md"), encoding="utf-8")
            layer_recipes(self.repo, tdir, self.t)
            layer_entity_predicates(self.repo, tdir, self.t, mv)
            layer_263_data(self.repo, tdir, self.t, mv)
            layer_client_items(self.repo, tdir, self.t)
            layer_geckolib_assets(self.repo, tdir, self.t)
            out = tdir / "resources"
            for f in sorted(out.rglob("*")) if out.is_dir() else []:
                if f.is_file():
                    dest = self.repo / "src/main/resources" / f.relative_to(out)
                    if not dest.is_file() or dest.read_bytes() != f.read_bytes():
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(f.read_bytes()); n += 1
        return n

    def s_stage(self):
        at = self.repo / "src/main/resources/META-INF/accesstransformer.cfg"
        self.work.mkdir(parents=True, exist_ok=True)
        text = ""
        for rnd in range(1, STAGE_ROUNDS + 1):
            log = self.work / f"stage-{rnd}.log"
            self.gradle(["compileJava"], log)
            text = log.read_text(encoding="utf-8", errors="replace")
            merged, sources = self.artifacts()
            if "createMinecraftArtifacts FAILED" not in text and merged and sources:
                return {"done": f"Minecraft staged ({len(merged)} merged jar(s)), {rnd} round(s)", "rounds": rnd}
            if not at.exists():
                break
            r = self.sh([self.py, str(ROOT / "tools/fix-access-transformer.py"), "--work", str(self.repo),
                         "--overrides-from", str(log)])
            added = re.search(r"(\d+) override widening", r.stdout or "")
            if not added or int(added.group(1)) == 0:
                break
        tail = [l for l in text.splitlines() if "What went wrong" in l or "error" in l.lower()][:4]
        return {"stop": "Minecraft would not stage (createMinecraftArtifacts)", "code": 10, "detail": tail,
                "choices": [f"read build/mc{self.t}/fork-port/stage-*.log for the first failure, fix the access transformer "
                            "in src/main/resources/META-INF/ on the branch"]}

    def parent_version(self):
        p = self.props()
        return p.get("parent_minecraft_version") if p.get("parent") != p.get("port_branch") else None

    def s_moves(self):
        fb = self.fb
        p = self.props()
        pv = self.parent_version() or p.get("base_minecraft_version", "1.21.1")
        work, why = fb.worktree_for(self.repo, pv)
        if work is None:
            return {"stop": f"cannot check out {fb.branch_name(pv)} to stage it: {why}", "code": 10,
                    "choices": [f"stage it by hand: git worktree add <dir> {fb.branch_name(pv)} && ./gradlew createMinecraftArtifacts"]}
        old = self.artifacts(work)[0]
        if not old:
            self.sh([BASH, "gradlew", "createMinecraftArtifacts", "--console=plain"], self.work / "stage-prev.log", cwd=work)
            old = self.artifacts(work)[0]
        new, _ = self.artifacts()
        if not old or not new:
            return {"stop": f"no staged merged jar for {pv if not old else self.t}", "code": 10,
                    "choices": [f"stage it: ./gradlew createMinecraftArtifacts on {fb.branch_name(pv if not old else self.t)}"]}
        self.work.mkdir(parents=True, exist_ok=True)
        cps = []
        for name, jars in (("old", old), ("new", new)):
            cp = self.work / f"cp-{name}.txt"
            cp.write_text("\n".join(str(j) for j in jars) + "\n", encoding="utf-8")
            cps.append(cp)
        raw = self.work / f"moves-{pv}-to-{self.t}.tsv"
        r = self.sh([self.py, str(ROOT / "tools/build-class-move-map.py"), "--from-cp", str(cps[0]), "--to-cp",
                     str(cps[1]), "--out", str(raw)])
        if r.returncode != 0 or not raw.exists():
            return {"stop": "build-class-move-map.py failed: " + (r.stdout or "").strip()[-200:], "code": 10,
                    "choices": ["see build-class-move-map.py's usage; check both jars hold net.minecraft classes"]}
        read = fb.reader_of_root(self.repo)
        existing = set()
        for v in fb.chain(self.repo, self.t):
            for name in ("renames.tsv", "moves.generated.tsv"):
                existing |= plain_froms(read(f".port/{v}/{name}"))
        existing |= plain_froms(read(f".port/{self.t}/shared-renames.tsv"))
        gen = self.tdir / "moves.generated.tsv"
        had = read(f".port/{self.t}/moves.generated.tsv")
        text, kept, dup = moves_table(read_pairs(raw), existing - plain_froms(had), pv, self.t)
        gen.write_text(text, encoding="utf-8")
        # rows as a tool: the moves are applied to the branch's source (a version branch holds its own source)
        changed = fb.apply_rules_to_files(sorted(self.repo.glob("src/*/java/**/*.java")), fb.rules_of(text))
        self.sh([self.py, str(ROOT / "tools/fix-access-transformer.py"), "--work", str(self.repo), "--class-map", str(raw)])
        return {"done": f"{kept} unambiguous move(s) {pv}->{self.t} -> .port/{self.t}/moves.generated.tsv"
                        f"{f' ({dup} already in a table)' if dup else ''}, applied to {changed} source file(s)", "moves": kept}

    def s_loop(self):
        start = self.compile_errors()
        parents = [self.repo / ".port" / v for v in self.fb.chain(self.repo, self.t)[1:]]
        flog = self.work / "file-loop.jsonl"
        if start == 0:
            open_ = [e for e in record_stubs(self.tdir, [x for e in (loop_events(flog, all_runs=True) if flog.is_file() else [])
                                                         if e.get("event") == "stub-signals" for x in e.get("files", [])],
                                             parents=parents, repo=self.repo) if not e.get("reviewed")]
            if open_:
                detail = [stub_line(e) for e in open_]
                return {"stop": f"0 errors, but {len(open_)} unreviewed stub signal(s) from earlier runs (STUBS.json)",
                        "code": 10, "errors": 0, "detail": detail, "view": detail,
                        "choices": [f"port each flagged file's lost behaviour, or set its \"reviewed\" in .port/{self.t}/STUBS.json "
                                    "to why the change is right; then resume with --from-stage loop"]}
            return {"skipped": "already at 0 errors", "errors": 0}
        why = spend_guard(start, self.a.budget, self.a.max_errors, self.a.confirm_spend)
        if why:
            return {"stop": "spend guard: " + why, "code": 10, "errors": start, "this_run": True, "detail": [why],
                    "choices": [f"raise it on purpose: {self.resume_cmd('loop')} --confirm-spend (or --max-errors N / a larger --budget)"]}
        out = self.work / "file-loop.out"
        r = self.sh([self.py, str(ROOT / "tools/file-loop.py"), "--work", str(self.repo), "--budget", str(self.a.budget),
                     "--log", str(flog)], out)
        ev = loop_events(flog)
        end = next((e for e in reversed(ev) if e.get("event") == "end"), None)
        n = end.get("errors") if end else None
        kept = [e.get("row") for e in ev if e.get("event") == "rule" and e.get("kept") and e.get("row")]
        if kept:       # a row the loop kept is part of this version's table: the next merge applies it too
            rn = self.tdir / "renames.tsv"
            have = self.fb.read_file(rn)
            add = [row for row in kept if row not in have.splitlines()]
            if add:
                rn.write_text(have.rstrip("\n") + ("\n" if have.strip() else "") + "\n".join(add) + "\n", encoding="utf-8")
        res = {"errors": n, "start_errors": start, "exit": r.returncode, "log": str(flog), "this_run": True}
        stubs_all = record_stubs(self.tdir, [x for e in loop_events(flog, all_runs=True) if e.get("event") == "stub-signals"
                                             for x in e.get("files", [])], parents=parents, repo=self.repo)
        if n == 0:
            stubs = [x for x in stubs_all if not x.get("reviewed")]
            if stubs:
                detail = [stub_line(x) for x in stubs]
                res.update({"stop": f"0 errors, but {len(stubs)} stub signal(s)", "code": 10, "detail": detail, "view": detail,
                            "spent": end.get("spent", 0),
                            "choices": [f"port each flagged method, or set its \"reviewed\" in .port/{self.t}/STUBS.json to why"]})
                return res
            res["done"] = f"{start} -> 0 errors, ${end.get('spent', 0)} of workers"
            return res
        clog = self.repo / "file-loop-compile.log"
        view, files = self.error_view(clog) if clog.is_file() else ([], set())
        esc = escalation(ev, files)
        res.update({"stop": f"{n} compile errors" if n is not None else "the loop ended without a count (file-loop.out)",
                    "code": 10, "spent": esc["spent"], "view": view, "esc": esc, "detail": view[:6],
                    "choices": [f"continue: {self.resume_cmd('loop')} with a higher --budget",
                                "fix the listed groups by hand on the branch, and a generic shape in the shared library"]})
        return res

    def s_audit(self):
        n = self.compile_errors()
        if n != 0:
            return self.not_compiled(n, "audit")
        neo = self.props().get("prop.neo_version", "")
        jars = [j for j in self.artifacts()[1] if neo in j.name] or self.artifacts()[1]
        if not jars:
            return {"stop": "no staged sources jar to audit against", "code": 13, "choices": ["stage Minecraft first"]}
        prepared = self.repo / "src/main/java"
        r = self.sh([self.py, str(ROOT / "tools/audit-mixin-targets.py"), str(self.repo), str(jars[0]), f"--sources={prepared}"])
        lines = (r.stdout or "").strip().splitlines()
        if r.returncode == 0:
            if self.props().get("strip_onlyin") == "true":
                hits = client_refs_in_common(self.repo, prepared)
                if hits:
                    return {"stop": f"{len(hits)} client lookup(s) in common classes will crash a dedicated server once "
                                    "@OnlyIn is stripped (CATALOG M52)", "code": 13, "audit": hits, "detail": hits[:6],
                            "choices": ["call <mv_package>.ClientRefs.player()/level(), or move the lookup into a client-only class"]}
            ev = self.audit_event_listeners()
            if ev.get("stop"):
                return ev
            return {"done": ((lines[-1] if lines else "all targets match")[:120] + "; " + ev["done"])[:200]}
        if r.returncode == 2 or any("NO TARGETS CHECKED" in l for l in lines):
            return {"stop": "the mixin audit checked no targets (wrong jar or tree?) -- not a pass", "code": 13,
                    "audit": lines[:8], "detail": lines[:4], "choices": ["fix the audit's inputs and resume with --from-stage audit"]}
        bad = [l for l in lines if l.strip() and not l.strip().startswith(("ok ", "checked ")) and "all targets" not in l] or lines
        return {"stop": f"{len([l for l in lines if 'mixin:' in l]) or len(bad)} mixin target(s) do not match vanilla",
                "code": 13, "audit": bad, "detail": bad[:6],
                "choices": ["retarget each mixin on the branch (and on the branches above it by cascade)"]}

    def audit_event_listeners(self):
        trees = sorted(str(p) for p in self.repo.glob("src/*/java") if p.is_dir())
        if not trees:
            return {"stop": "no source tree to audit event listeners in", "code": 13, "choices": ["-"]}
        cp, why = self.target_classpath()
        if cp is None:
            return {"stop": "event-listener audit: " + why, "code": 13, "detail": [why],
                    "choices": ["fix the classpath resolution; this audit refuses to run on a guessed one"]}
        r = self.sh([self.py, str(ROOT / "tools/audit-event-listeners.py"), str(self.repo), f"--cp-file={cp}",
                     f"--tree={','.join(trees)}", f"--mc={self.t}"])
        lines = (r.stdout or "").strip().splitlines()
        if r.returncode == 0:
            return {"done": (lines[0] if lines else "event listeners ok")[:100]}
        if r.returncode == 2:
            return {"stop": "the event-listener audit checked no listeners -- not a pass", "code": 13, "audit": lines[:8],
                    "detail": lines[:4], "choices": ["fix the audit's inputs and resume with --from-stage audit"]}
        bad = [l for l in lines if l.strip() and not l.startswith("checked ")]
        return {"stop": f"{sum('ABSTRACT' in l for l in bad)} event listener(s) name an abstract event", "code": 13,
                "audit": bad, "detail": bad[:6], "choices": ["port each listener to a concrete subclass of the event (CATALOG X33)"]}

    def target_classpath(self):
        """Gradle's own resolved compile classpath of the branch build (tools/qtc-init.gradle), as layer-ci.py resolves
        a layer target's, through the branch's plain ./gradlew."""
        self.work.mkdir(parents=True, exist_ok=True)
        cmd = [BASH, "gradlew", "-I", str(ROOT / "tools/qtc-init.gradle"), "-I", str(ROOT / "tools/central-mirror.init.gradle"),
               "-q", "qtcClasspath", "--console=plain"]
        r = subprocess.run(cmd, cwd=self.repo, capture_output=True, text=True, encoding="utf-8", errors="replace", env=self.env)
        m = re.search(r"QTC_CLASSPATH_BEGIN\n(.*?)QTC_CLASSPATH_END", r.stdout or "", re.S)
        jars = [l.strip() for l in m.group(1).splitlines() if l.strip()] if m else []
        if not jars:
            return None, f"could not resolve the {self.t} classpath: {(r.stdout or '')[-300:]}"
        cp = self.work / f"qtc-classpath-{self.t}.txt"
        cp.write_text("\n".join(jars) + "\n", encoding="utf-8")
        return cp, ""

    def shipped_expected(self):
        return (self.tdir / "shipped-expect.txt").is_file()

    def lost_logs(self):
        pv = self.parent_version()
        if not pv:
            return [], None
        prev = self.repo / "build" / f"mc{pv}" / "fork-port" / "gates" / "harness" / "run" / "logs"
        if not prev.is_dir():
            return [], None
        known = self.tdir / "logs-known-absent.txt"
        absent = [l.split("#", 1)[0].strip() for l in known.read_text(encoding="utf-8").splitlines()] if known.is_file() else []
        return lost_log_files(prev, self.work / "gates" / "harness" / "run" / "logs", [x for x in absent if x]), pv

    def done(self, prepare=False):
        return self.fb.target_done(self.repo, self.t, write=True)

    def skeleton(self):
        li = load("layer_inventory", "layer-inventory.py")
        try:
            _c, rep, _l = li.check_branch(self.repo, self.repo, self.t)
        except Exception:      # noqa: BLE001 -- the skeleton is a help; the done check reports the inventory itself
            rep = None
        return self.fb.review_skeleton(self.repo, self.repo, self.t, "HEAD", True, (rep or {}).get("unaccounted") or [])

    def test_items(self):
        return self.fb.write_test_items(self.repo, self.t)

    def review_note(self, added):
        items = self.fb.review_items(self.repo, self.repo, self.t, "HEAD", True)
        n = lambda k: sum(1 for kk, _ in items if kk == k)
        return (f"REVIEW.md lists every item{f', {added} new' if added else ''}: {n('stub')} stub(s), {n('changed')} file(s) "
                f"changed from {self.props().get('parent', 'the parent branch')}, {n('resources')} resource group(s)"
                f"{', renames.tsv' if n('renames') else ''}; the parent's files they change")

    def drift(self, a):
        return drift_stop(self.repo, a, props_file=self.tdir / "branch.properties", drift_file=self.tdir / "DRIFT.md")


def cost_since(cost_md):
    """Where the next COST.md record starts: just after the last record's `- window: ... to <end>` (port-cost.py's exact
    end), or a minute after an older record's `(<start> to <end>)`; None when the target has no record yet."""
    w = re.findall(r"(?m)^- window: requests .*? to (\d{4}-\d\d-\d\dT[\d:.]+Z)", cost_md)
    o = re.findall(r"\((\d{4}-\d\d-\d\dT\d\d:\d\dZ) to (\d{4}-\d\d-\d\dT\d\d:\d\dZ)\)", cost_md)
    if w:
        end, step = w[-1], datetime.timedelta(milliseconds=1)
    elif o:
        end, step = o[-1][1], datetime.timedelta(minutes=1)
    else:
        return None
    t = datetime.datetime.fromisoformat(end.replace("Z", "+00:00")) + step
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def loop_spend_line(events):
    """The compile loop's workers' own recorded dollars (file-loop.jsonl), as one line: the most exact figure there is.
    Their transcripts are among those port-cost.py priced above, so it is a part of that total, not added to it."""
    w = [e for e in events if e.get("event") == "worker"]
    if not w:
        return ""
    usd = sum(e.get("usd") or 0 for e in w)
    est = sum(1 for e in w if e.get("usd_unknown"))
    return (f"- of which this run's compile-loop workers recorded ${usd:.2f} over {len(w)} worker(s) (file-loop.jsonl"
            + (f"; {est} with no result, charged an estimate" if est else "") + ") -- their transcripts, when on this machine, are among those priced above, so it is not added\n")


def carry_over(old_stages, skipped):
    """What a resume keeps of an earlier run's stage results: the results of the stages it skips, MINUS `this_run`. A
    number carried over from another run is a record, never a measurement: compile_errors() re-measures unless this
    run did (measured: a resume trusted an old 0, skipped the audit and every gate, and reported the target done)."""
    return {k: {x: y for x, y in v.items() if x != "this_run"} for k, v in old_stages.items() if k in skipped}


def execute(run, todo):
    """Run the stages in order; -> the stop dict (with stage/left) of the first stage that cannot finish, or None."""
    for s in todo:
        res = getattr(run, "s_" + s)()
        run.summary["stages"][s] = {k: v for k, v in res.items() if k not in ("view", "esc", "stop", "choices", "code", "detail")}
        if "stop" in res:
            run.summary["stages"][s]["stopped"] = res["stop"]
            return dict(res, stage=s, left=res["stop"])
        run.say(s, res.get("done") or ("skipped: " + res.get("skipped", "")))
    return None


# ---------------------------------------------------------------- fix lowest, then sweep up (R5)

SWEEP_GREEN = (0, 17, UNDONE_EXIT)     # the stages it re-ran are green; 17/18 say what else "done" still needs
DRIFT_CHECKED_ENV = "FORK_PORT_DRIFT_CHECKED"     # a sweep checks the upstream once, not once per target


def chain_targets(mv, frm, to):
    """The targets from `frm` up to `to` along `to`'s inherits chain, oldest first. ValueError when `frm` is not in it."""
    if not (mv / "targets" / to).is_dir():
        raise ValueError(f"{to} is not a target of this layer")
    ch = list(reversed(chain(mv, to)))
    if frm not in ch:
        raise ValueError(f"{frm} is not in {to}'s inherits chain ({' -> '.join(ch)})")
    return ch[ch.index(frm):]


def sweep_green(rc, summ):
    """Did the target's stages run green? 0 and 17 say so; an 18 only when the child ended "Gate C green, NOT done"
    (its summary lists not_done, no stop) -- an 18 with a stop is a refusal before anything was built (a library
    target not done, an undone parent), and an 18 with no summary written this run cannot say: both are red."""
    if rc == UNDONE_EXIT:
        return bool(summ) and "stop" not in summ and summ.get("not_done") is not None
    return rc in SWEEP_GREEN


def sweep_result(rc, summ):
    if rc == 0:
        return "done"
    if rc == 17:
        return "green, NOT done: Gate C not run"
    if rc == UNDONE_EXIT and sweep_green(rc, summ):
        nd = summ.get("not_done") or []
        return f"green, NOT done: {nd[0] if nd else 'review / test items'}" + (f" (+{len(nd) - 1})" if len(nd) > 1 else "")
    st = summ.get("stop") or {}
    return f"RED at {st.get('stage', '?')}: {st.get('left', f'exit {rc}')}"


def sweep(a, runner=None, out=print):
    """`--chain FROM..TO --from-stage S`: re-run stage S onward (no model: --budget 0 --max-errors 0) for every target
    from FROM up TO's inherits chain, oldest first; stop at the first red target and name its RESIDUE.md. A bug class
    fixed at the lowest target where it exists is swept up this way, and a fixer is started only where it goes red.
    -> the first red target's exit code, else 0 when every target is done, else the first not-done code (17/18)."""
    repo = pathlib.Path(a.repo).resolve()
    mv = repo / ".multiversion"
    if not (mv / "base.properties").is_file():
        out("fork-port: --chain needs a layer fork (.multiversion/); a single-version fork has one version and nothing to sweep")
        return 2
    frm, sep, to = a.chain.partition("..")
    try:
        if not sep or not frm or not to:
            raise ValueError(f"--chain wants FROM..TO (e.g. 1.21.3..26.3), not {a.chain!r}")
        targets = chain_targets(mv, frm, to)
        stage = a.from_stage or "gates"
        select_stages(stage, a.to_stage)
    except ValueError as e:
        out(f"fork-port: {e}"); return 2
    if runner is None:
        def runner(cmd, env):
            return subprocess.run(cmd, env=env).returncode
    lc = load("layer_ci", "layer-ci.py")
    lock = lc.CheckoutLock(repo, "fork-port.py --chain", a.chain)
    busy = lock.acquire()
    if busy:
        out(f"fork-port: {busy}"); return 14
    try:
        a.target = to
        st = drift_stop(repo, a)
        if st:
            out(stop_block(dict(st, stage="chain", left=st["stop"], residue="(none: nothing ran)")))
            return st["code"]
        env = dict(os.environ, **{DRIFT_CHECKED_ENV: "1"})
        out(f"fork-port: chain {' -> '.join(targets)}: --from-stage {stage} --gates {a.gates}, no model (--budget 0 --max-errors 0)")
        rows, first_bad, first_nd = [], None, None
        for t in targets:
            cmd = [sys.executable, str(pathlib.Path(__file__).resolve()), "--repo", str(repo), "--target", t,
                   "--from-stage", stage, "--gates", a.gates, "--budget", "0", "--max-errors", "0", "--base", a.base,
                   *(["--to-stage", a.to_stage] if a.to_stage else []), *(["--modid", a.modid] if a.modid else []),
                   *[x for c in a.port_checkout for x in ("--port-checkout", c)],
                   *[x for c in a.port_release for x in ("--port-release", c)],
                   *(["--allow-undone-library"] if getattr(a, "allow_undone_library", False) else [])]
            out(f"fork-port: chain: {t} ...")
            jp = repo / "build" / f"mc{t}" / "fork-port" / "fork-port.json"
            before = jp.stat().st_mtime_ns if jp.is_file() else None
            rc = runner(cmd, env)
            try:      # only the summary this child wrote: an older run's (a child that stopped before saving) misleads
                summ = json.loads(jp.read_text(encoding="utf-8")) if jp.stat().st_mtime_ns != before else {}
            except (OSError, ValueError):
                summ = {}
            green = sweep_green(rc, summ)
            res = (mv / "targets" / t / "RESIDUE.md")
            rows.append((t, rc, sweep_result(rc, summ),
                         res.relative_to(repo).as_posix() if not green and res.is_file() else ""))
            if not green:
                first_bad = rc
                break
            if rc and first_nd is None:
                first_nd = rc
        out("")
        out("| target | exit | result | residue |")
        out("|---|---|---|---|")
        for t, rc, r, p in rows:
            out(f"| {t} | {rc} | {r[:160]} | {p or '-'} |")
        left = targets[len(rows):]
        if left:
            out(f"| {', '.join(left)} | - | not run: the sweep stops at the first red target | - |")
        if first_bad is not None:
            t = rows[-1][0]
            out(f"\nfork-port: chain STOPPED at {t} (exit {first_bad}): start ONE fixer there, with only "
                f"{rows[-1][3] or f'.multiversion/targets/{t}/'}; then sweep on: python3 tools/fork-port.py --repo {a.repo} "
                f"--chain {t}..{to} --from-stage {stage} --gates {a.gates}")
            return first_bad
        return first_nd or 0
    finally:
        lock.release()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo"); ap.add_argument("--target")
    ap.add_argument("--budget", type=float, default=15.0, help="dollars the compile loop's workers may spend")
    ap.add_argument("--gates", default="auto", choices=["auto", "none", "ab", "full"],
                    help="auto (default): full -- Gate C, a real client, included -- until the target's GATES.md records a "
                         "green Gate C, then ab. A target gets one real-client run on its initial port; CI keeps its own policy")
    ap.add_argument("--json"); ap.add_argument("--from-stage"); ap.add_argument("--modid")
    ap.add_argument("--to-stage", help="stop after this stage (default: run to the end)")
    ap.add_argument("--base", default="origin/neoforge-1.21.1", help="the authors' ref, for ci-gates.py --base")
    ap.add_argument("--max-errors", type=int, help="the loop refuses to start above this many errors (default: 100 per dollar of --budget)")
    ap.add_argument("--confirm-spend", action="store_true", help="start the loop although the error count exceeds the spend guard")
    ap.add_argument("--direct", action="store_true",
                    help="a NEW target that jumps: no inherits, shared_chain= pulls the shared library of every targeted version "
                         "in between (make-fork-layer.py add-target --direct); ignored when the target already exists")
    ap.add_argument("--port-checkout", action="append", default=[],
                    help="owner/repo=DIR: a sibling layer fork this one depends on; its per-target jar comes from DIR "
                         "(tools/layer-deps.py)")
    ap.add_argument("--port-release", action="append", default=[],
                    help="owner/repo@TAG: a sibling layer fork's release holding its per-target jar; pinned (url + sha256) "
                         "in the target's target.properties for CI's `layer-deps.py fetch` (tools/layer-deps.py)")
    ap.add_argument("--allow-undone-parent", action="store_true",
                    help="development only: add a NEW target although the target it stands on is not done (recorded in its "
                         "target.properties as allowed_undone_parent; the new target is then not done until its parent is)")
    ap.add_argument("--allow-undone-library", action="store_true",
                    help="development only: resolve against a sibling library's --port-checkout whose target is not done "
                         "(passed to layer-deps.py resolve, recorded in DEPS.md and the summary; the target is then not done "
                         "until it is re-resolved with --port-release)")
    ap.add_argument("--upstream", help="<remote>/<branch>: the authors' branch, recorded with the commit the buildout starts "
                                       "from in .multiversion/base.properties (default: the recorded one, else the one branch "
                                       "of the clone's `upstream` remote that HEAD carries)")
    ap.add_argument("--accept-upstream-drift", action="store_true",
                    help="continue although the authors' branch (or a recorded library release) moved since the buildout's "
                         "freeze (exit 19 otherwise); recorded in .multiversion/DRIFT.md")
    ap.add_argument("--chain", metavar="FROM..TO",
                    help="sweep: re-run --from-stage (default gates) onward for every target from FROM up TO's inherits chain, "
                         "oldest first, no model; stops at the first red target with its RESIDUE.md and prints the table")
    ap.add_argument("--check-done", action="store_true",
                    help="only say whether --target is done (Gate C, stubs, REVIEW.md, TEST-ITEMS.md, the behaviour inventory): "
                         "exit 0, or 18 with the reasons; writes nothing")
    ap.add_argument("--write-test-items", action="store_true",
                    help="only write --target's TEST-ITEMS.md (its manual-test items, from tools/layer-test-plan.py, no "
                         "model), holding the checkout lock; no stage or gate runs (layer forks only)")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if a.chain:
        if not a.repo or a.target:
            ap.error("--chain takes --repo and no --target")
        if not pathlib.Path(a.repo).is_dir():
            print(f"fork-port: no such directory: {a.repo}"); return 2
        if not (pathlib.Path(a.repo) / ".multiversion/base.properties").is_file() and fork_branches().version_branches(a.repo):
            # version branches: the sweep is a cascade (merge each branch into the next, no-model gates per branch)
            print("fork-port: --chain on version branches is tools/fork-branches.py cascade (merge upward, then gates)")
            gates = {"auto": "ab", "none": "none"}.get(a.gates, a.gates)
            return fork_branches().cascade(a.repo, a.chain, gates)
        return sweep(a)        # --gates auto passes through: each target is full until its own GATES.md records Gate C
    if not a.repo or not a.target:
        ap.error("--repo and --target are required")
    if not pathlib.Path(a.repo).is_dir():
        print(f"fork-port: no such directory: {a.repo}"); return 2
    branch = branch_mode(a.repo, a.target)
    if branch and not a.check_done and fork_branches().checkout_version(pathlib.Path(a.repo)) != a.target \
            and a.from_stage and a.from_stage != "setup":
        print(f"fork-port: --from-stage {a.from_stage} runs on {fork_branches().branch_name(a.target)}'s checkout: check it "
              "out first (or start from setup, which does)"); return 2
    if branch and a.check_done:
        why = fork_branches().target_done(a.repo, a.target)      # only asks: writes nothing
        print(f"fork-port {a.target}: " + ("done" if not why else f"NOT done ({len(why)} open):\n" + "\n".join(f"  - {w}" for w in why)))
        return UNDONE_EXIT if why else 0
    if branch and a.write_test_items:
        return write_test_items_branch(a)
    if a.check_done:
        if not (pathlib.Path(a.repo) / ".multiversion/base.properties").is_file():
            print("fork-port: --check-done needs a layer fork (.multiversion/)"); return 2
        why = target_done(a.repo, a.target, write=False)      # only asks: writes nothing
        print(f"fork-port {a.target}: " + ("done" if not why else f"NOT done ({len(why)} open):\n" + "\n".join(f"  - {w}" for w in why)))
        return UNDONE_EXIT if why else 0
    if a.write_test_items:
        return write_test_items_only(a)
    try:
        skipped, todo = select_stages(a.from_stage, a.to_stage)
    except ValueError as e:
        print(f"fork-port: {e}"); return 2
    run = BranchRun(a) if branch else Run(a)
    if run.env:     # the in-process helpers (target_classpath: layer-ci.py) start Gradle too: daemon-less as well
        os.environ["GRADLE_OPTS"] = run.env["GRADLE_OPTS"]
    if a.gates == "auto":
        a.gates = gates_auto(run.tdir)
        print(f"fork-port: gates: auto -> {a.gates} ({'Gate C already recorded in GATES.md' if a.gates == 'ab' else 'no recorded Gate C pass yet: one real-client run on the initial port'})")
    lc = load("layer_ci", "layer-ci.py")
    lock = lc.CheckoutLock(run.repo, "fork-port.py", a.target)
    busy = lock.acquire()
    if busy:
        print(f"fork-port: {busy}")
        return 14
    if run.env is not None and lc.LOCK_ENV in os.environ:
        # run.env was copied before the lock was taken: the children (ci-gates.py --mc, layer-deps.py) must see the
        # holder, or they refuse their own parent's checkout
        run.env[lc.LOCK_ENV] = os.environ[lc.LOCK_ENV]
    try:
        return drive(run, a, skipped, todo)
    finally:
        lock.release()


def write_test_items_branch(a):
    """--write-test-items on a version branch: .port/<t>/TEST-ITEMS.md alone, under the checkout lock."""
    fb = fork_branches()
    repo = pathlib.Path(a.repo).resolve()
    if fb.checkout_version(repo) != a.target:
        print(f"fork-port: check out {fb.branch_name(a.target)} first: TEST-ITEMS.md is written on its branch"); return 2
    lock = load("layer_ci", "layer-ci.py").CheckoutLock(repo, "fork-port.py", a.target)
    busy = lock.acquire()
    if busy:
        print(f"fork-port: {busy}"); return 14
    try:
        n, why = fb.write_test_items(repo, a.target)
    finally:
        lock.release()
    if why:
        print(f"fork-port {a.target}: manual-test items not written: {why}"); return 2
    print(f"fork-port {a.target}: {n} manual-test item(s) -> .port/{a.target}/TEST-ITEMS.md")
    return 0


def write_test_items_only(a):
    """--write-test-items: TEST-ITEMS.md for --target and nothing else, under the checkout lock (a run in progress
    owns the target's files). Exit 0 written, 14 the checkout is in use, 2 not a layer target or the plan failed."""
    repo = pathlib.Path(a.repo)
    tdir = repo / ".multiversion/targets" / a.target
    if not (repo / ".multiversion/base.properties").is_file():
        print("fork-port: --write-test-items needs a layer fork (.multiversion/)"); return 2
    if not tdir.is_dir():
        print(f"fork-port: {a.target} is not a target of this layer"); return 2
    lock = load("layer_ci", "layer-ci.py").CheckoutLock(repo, "fork-port.py", a.target)
    busy = lock.acquire()
    if busy:
        print(f"fork-port: {busy}"); return 14
    try:
        n, why = write_test_items(repo, tdir, a.target)
    finally:
        lock.release()
    if why:
        print(f"fork-port {a.target}: manual-test items not written: {why}"); return 2
    print(f"fork-port {a.target}: {n} manual-test item(s) -> {(tdir / 'TEST-ITEMS.md').relative_to(repo).as_posix()}")
    return 0


def drive(run, a, skipped, todo):
    """Everything after the checkout lock is held: resume bookkeeping, the stages, the stop report."""
    if run.jpath.is_file() and skipped:    # a resume keeps what the earlier stages found
        try:
            old = json.loads(run.jpath.read_text(encoding="utf-8"))
            run.summary["stages"] = carry_over(old.get("stages", {}), skipped)
            run.summary["started"] = old.get("started", run.summary["started"])
        except ValueError:
            pass
    for s in skipped:
        run.summary["stages"].setdefault(s, {"skipped": f"before --from-stage {a.from_stage}"})
    partial = bool(a.to_stage) and todo[-1] != STAGES[-1]          # a partial run is not "the port is done"
    # R4: a run to the end first checks the authors' branch against the buildout's freeze (layer forks only; a partial
    # run is keep-current's own measuring, after it merged the authors' code)
    stop = None if partial else run.drift(a)
    if stop:
        stop.update(stage=todo[0], left=stop["stop"])
        run.summary["stages"].setdefault("upstream", {"stopped": stop["stop"]})
    else:
        stop = execute(run, todo)
    open_ = unreviewed_stubs(run.tdir) if not stop and not partial else []
    if open_:
        # every successful end asks, not only the gates stage: a run resumed past it (--from-stage cost) with an older
        # GATES.md on record otherwise ended done with an unreviewed stub signal open
        detail = [stub_line(e) for e in open_]
        stop = {"stage": todo[-1], "left": f"{len(open_)} unreviewed stub signal(s) in STUBS.json", "code": 10,
                "detail": detail, "view": detail,
                "choices": ["port each flagged method to the new signature, or, when the change is right, set its "
                            "\"reviewed\" in targets/<t>/STUBS.json to why; then resume with --from-stage loop"]}
        stop["stop"] = stop["left"]
    run.summary["done"] = stop is None
    if partial:
        run.summary["to_stage"] = a.to_stage
    if stop:
        stop.setdefault("spent", 0)
        resume = run.resume_cmd(stop["stage"])
        view = stop.get("view") or stop.get("detail") or []
        md, data = residue_report(stop["stage"], stop["left"], view if stop["stage"] in ("loop", "measure", "stage", "moves") else [],
                                  stop.get("esc"), stop.get("audit") or [], stop.get("gates") or [], resume,
                                  environment=stop.get("environment"))
        rpath = run.tdir / "RESIDUE.md"
        if run.tdir.is_dir():
            put_residue(run.tdir, a.target, md)       # layer-sync.py's section (an unmerged overlay) stays
        stop["residue"] = str(rpath.relative_to(run.repo)) if run.tdir.is_dir() else "(no target dir)"
        run.summary["residue"] = data
        run.summary["stop"] = {"stage": stop["stage"], "code": stop["code"], "left": stop["left"], "resume": resume}
        run.save()
        print(stop_block(stop))
        print(f"fork-port: summary in {run.jpath}")
        return stop["code"]
    if not partial:           # only a run to the end clears an earlier stop's report (never layer-sync's section)
        clear_residue(run.tdir, a.target)
    # a version is DONE only after its real-client run: green A/B with no Gate C on record is reported as such, never as done
    no_gates = not partial and a.gates == "none"      # no gates is not done either: the same exit as A/B without Gate C
    pending_c = not partial and (no_gates or gates_auto(run.tdir) != "ab")
    if pending_c:
        run.summary["done"] = False
        run.summary["gate_c"] = "not run"
    if no_gates:
        run.summary["gates"] = "not run (--gates none)"
    run.save()
    if no_gates:
        print(f"fork-port {a.target}: stages ok, gates not run (--gates none) -- NOT done: "
              f"{run.resume_cmd('gates').replace(' --gates none', ' --gates full')} -- summary in {run.jpath}")
        return 17
    if pending_c:
        print(f"fork-port {a.target}: A/B green, Gate C not run -- NOT done until a real client has run it: "
              f"{run.resume_cmd('gates').replace(' --gates ' + a.gates, ' --gates full')} -- summary in {run.jpath}")
        return 17
    if not partial:
        # R1: Gate C green is not done either until the target's manual-test items are generated and an independent
        # fresh-context review of every stub and whole-file overlay is recorded in REVIEW.md for the reviewed commit
        n_items, why = run.test_items()
        problems = (["manual-test items: " + why] if why else []) + run.done(prepare=True)
        if problems:
            added = run.skeleton()
            run.summary.update({"done": False, "not_done": problems})
            run.save()
            print(f"fork-port {a.target}: Gate C green, NOT done ({len(problems)} open):")
            print("\n".join(f"  - {p}" for p in problems[:20]) + (f"\n  ... and {len(problems) - 20} more" if len(problems) > 20 else ""))
            print(f"  Next: start a FRESH reviewer agent, not the fixer, with only {run.records_rel()} "
                  f"({run.review_note(added)}); it fills REVIEW.md (reviewer: the "
                  f"reviewing agent, e.g. `fresh agent <id>, not the fixer`; date; commit; one verdict + note per row, "
                  f"naming each hook `item` id it accounts for) and commits it. Then: "
                  f"python3 tools/fork-port.py --repo {a.repo} --target {a.target} --check-done")
            print(f"fork-port: summary in {run.jpath}")
            return UNDONE_EXIT
        if n_items is not None:
            run.summary["test_items"] = n_items
            run.save()
    print(f"fork-port {a.target}: {'stages ' + ', '.join(todo) + ' ok' if partial else 'done'} -- summary in {run.jpath}")
    return 0


def self_check():
    ok = True

    def expect(c, msg):
        nonlocal ok
        if not c:
            ok = False; print("FAIL:", msg)
    # cost: each record starts where the last one ended, so a resumed run never counts a request twice
    expect(cost_since("") is None, "a target with no COST.md record counts every request that worked on it")
    expect(cost_since("## r\n- **≈ $1.00** over 1 session(s): 0.1 h active, 0.2 h wall (2026-10-01T10:00Z to 2026-10-01T11:30Z)\n")
           == "2026-10-01T11:31:00.000Z", "an older record (no window line) ends a minute after its last request")
    expect(cost_since("## a\n- window: requests from x to 2026-10-01T09:00:00.000Z (r)\n## b\n- (2026-10-01T10:00Z to 2026-10-01T12:00Z)\n"
                      "- window: requests from 2026-10-01T09:00:00.001Z to 2026-10-02T08:15:30.250Z (r)\n")
           == "2026-10-02T08:15:30.251Z", "the next record starts 1 ms after the last record's exact window end")
    ln = loop_spend_line([{"event": "start"}, {"event": "worker", "usd": 0.5}, {"event": "worker", "usd": 0.25, "usd_unknown": True},
                          {"event": "multi-rejected", "multi_usd": 9}])
    expect("$0.75 over 2 worker(s)" in ln and "1 with no result" in ln and loop_spend_line([{"event": "start"}]) == "",
           f"the loop workers' own recorded dollars are one line, and none without workers: {ln!r}")
    # stage selection
    expect(hit_cap("> Task :compileJava\n1,000 errors\n") and hit_cap("1000 errors\n") and not hit_cap("999 errors\n")
           and not hit_cap("21,000 errors\n"), "hit_cap reads javac's capped total, with or without the comma")
    with tempfile.TemporaryDirectory() as d_:
        lg = pathlib.Path(d_) / "m.log"
        lg.write_text("> Task :compileJava\n/w/A.java:1: error: cannot find symbol\n1,000 errors\n> Task :compileJava FAILED\nBUILD FAILED\n\n"
                      + RECOUNT_MARK + "\nPORT_FULL_ERRORS compileJava: uncapped javac over 1 file(s)\n/w/A.java:1: error: cannot find symbol\n"
                      "/w/B.java:2: error: cannot find symbol\n2 errors\nPORT_FULL_ERRORS compileJava: javac exit 1\nBUILD SUCCESSFUL in 3s\n", encoding="utf-8")
        n_, _w = Run.count(None, lg)
        expect(n_ == 2, f"a recounted log is counted, not read as clean from the recount run's BUILD SUCCESSFUL: {n_}")
    expect(capped_tasks("> Task :compileJava\nA.java:1: error: x\n1,000 errors\n> Task :compileJava FAILED\n> Task :compileJvmtiJava\n"
                        "B.java:1: error: y\n1 error\n") == ["compileJava"], "only the task that hit the cap is recounted")
    expect(select_stages(None) == ([], STAGES), "no --from-stage runs everything")
    expect(select_stages(None, None) == ([], STAGES), "--to-stage absent changes nothing")
    expect(select_stages("stage", "measure") == (["setup"], ["stage", "moves", "measure"]), "from stage to measure")
    expect(select_stages("audit", "audit") == (STAGES[:5], ["audit"]), "a single stage")
    expect(select_stages(None, "moves") == ([], ["setup", "stage", "moves"]), "to-stage alone")
    for bad_ in (("loop", "stage"), ("loop", "nope")):
        try:
            select_stages(*bad_); expect(False, f"{bad_} must raise")
        except ValueError:
            pass
    sk, td = select_stages("loop")
    expect(sk == ["setup", "stage", "moves", "measure"] and td[0] == "loop" and td[-1] == "cost", "from-stage loop")
    try:
        select_stages("nope"); expect(False, "unknown stage must raise")
    except ValueError:
        pass
    # the moves table: existing rows dropped (X31), exhaustive block closed (X11), tab-separated
    raw = [("net.minecraft.a.Zombie", "net.minecraft.a.z.Zombie"), ("net.minecraft.a.Cow", "net.minecraft.a.c.Cow")]
    text, kept, dup = moves_table(raw, {"net.minecraft.a.Cow"}, "1.21.4", "1.21.5")
    lines = text.splitlines()
    expect(kept == 1 and dup == 1 and "net.minecraft.a.Zombie\tnet.minecraft.a.z.Zombie" in lines
           and "net.minecraft.a.Cow\tnet.minecraft.a.c.Cow" not in lines, "moves table rows")
    expect(lines.index("#!strict") > max(i for i, l in enumerate(lines) if l.startswith("#!exhaustive")), "block closed")
    expect(plain_froms("a.B\tc.D\nre:x\ty\nmember:z\tq\n# c\n") == {"a.B"}, "plain_froms skips re:/member:")
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d, "m.tsv"); p.write_text(text, encoding="utf-8")
        expect(read_pairs(p) == [("net.minecraft.a.Zombie", "net.minecraft.a.z.Zombie")], "table reads back as pairs")
    # layer.py patch decision, against the real template
    tpl = (ROOT / "templates/fork-layer/tools/layer.py").read_text(encoding="utf-8")
    expect(INSERT in tpl, "template layer.py carries the moves.generated.tsv block")
    old = tpl.replace(INSERT, "")
    expect(layer_patch(old, tpl)[0] == "replace" and layer_patch(tpl, tpl)[0] == "current"
           and layer_patch(old + "# hand edit\n", tpl)[0] == "diverged", "layer.py patch verdicts")
    expect(ANCHOR + INSERT in tpl, "moves block sits right after the target's own rows")
    # chain / modid
    with tempfile.TemporaryDirectory() as d:
        mv = pathlib.Path(d)
        for t, inh in (("1.21.4", None), ("1.21.5", "1.21.4")):
            (mv / "targets" / t).mkdir(parents=True)
            (mv / "targets" / t / "target.properties").write_text(f"inherits={inh}\n" if inh else "x=1\n", encoding="utf-8")
        expect(chain(mv, "1.21.5") == ["1.21.5", "1.21.4"], "chain")
    expect(parse_modid('[[mods]]\nmodId = "examplemod"\n[[dependencies.examplemod]]\nmodId = "neoforge"\n') == "examplemod", "modid")
    # the residue builder over a canned compile log, plus a canned loop log
    cs = load("compile_summary", "compile-summary.py")
    log = ("> Task :compileJava\n/w/build/mc1.21.5/mv/java/m/A.java:3: error: cannot find symbol\n  symbol:   class Foo\n"
           "/w/build/mc1.21.5/mv/java/m/A.java:9: error: cannot find symbol\n  symbol:   class Foo\n"
           "/w/build/mc1.21.5/mv/java/m/B.java:4: error: incompatible types: Bar cannot be converted to Baz\n"
           "3 errors\nBUILD FAILED\n")
    s = cs.summarise(log, cs.rb.signatures())
    view = cs.render(s, None, 8, 8)
    expect(s["total"] == 3 and view and "3 errors in 2 files" in view[0], "canned compile summary")
    evs = [{"event": "start", "errors": 3},
           {"event": "worker", "model": "haiku", "files": ["x/java/m/A.java"], "usd": 0.1},
           {"event": "worker", "model": "opus", "files": [".multiversion/targets/1.21.5/java/m/A.java"], "usd": 1.0},
           {"event": "rule", "row": "a\tb", "kept": False, "why": "errors grew", "errors": 5},
           {"event": "rule", "row": "c\td", "kept": True, "why": "", "errors": 2},
           {"event": "stub-signals", "files": [{"file": "m/B.java", "why": "emptied"}]},
           {"event": "end", "errors": 3, "spent": 1.1}]
    esc = escalation(evs, set(s["files"]))
    expect(esc["top_tier_still_failing"] == ["m/A.java"] and esc["rows_kept"] == ["c\td"]
           and esc["rows_reverted"][0][0] == "a\tb" and esc["stubs"] == ["m/B.java"], "escalation record")
    md, data = residue_report("loop", "3 compile errors", view, esc, [], [], "python3 tools/fork-port.py --from-stage loop")
    expect("## Remaining compile errors" in md and "--from-stage loop" in md and "m/A.java" in md
           and "## The loop's record" in md and "BUILD FAILED" not in md, "residue md")
    expect(len(md.splitlines()) < 60 and data["resume"].endswith("--from-stage loop"), "residue is bounded")
    b = stop_block({"stage": "loop", "left": "3 compile errors", "spent": 1.1, "detail": ["x"],
                    "residue": ".multiversion/targets/1.21.5/RESIDUE.md", "choices": ["a", "b"]})
    expect(b.startswith("STOPPED at loop: 3 compile errors") and "Residue: " in b and "do not pick one silently" in b, "stop block")
    with tempfile.TemporaryDirectory() as d:
        lg = pathlib.Path(d, "l.jsonl")
        lg.write_text("\n".join(json.dumps(e) for e in [{"event": "start"}, {"event": "worker", "usd": 9}] + evs) + "\n", encoding="utf-8")
        expect(loop_events(lg)[0]["event"] == "start" and len(loop_events(lg)) == len(evs), "only the last run's events")
    ev = gate_evidence("noise\n/w/x/y/java/com/example/mod/gatetest/BaselineGameTest.java:15: error: cannot find symbol\n"
                       "/w/x/y/java/com/example/mod/gatetest/BaselineGameTest.java:15: error: cannot find symbol\n"
                       "  /w/x/y/java/com/example/mod/Other.java:9: error: incompatible types: A cannot be converted to B\n")
    expect(ev == ["mod/gatetest/BaselineGameTest.java:15: cannot find symbol",
                  "example/mod/Other.java:9: incompatible types: A cannot be converted to B"], "gate_evidence compile errors: %s" % ev)
    expect(gate_evidence("> Task :test FAILED\n")[0].endswith("FAILED") and gate_evidence("all fine") == [], "gate_evidence tests/none")
    # CATALOG X47: the mod's own log files, compared between the inherited target and this one
    with tempfile.TemporaryDirectory() as d:
        prev, cur = pathlib.Path(d, "prev"), pathlib.Path(d, "cur")
        for n in ("latest.log", "debug.log", "2026-10-12-1.log.gz", "2026-10-12-2.log", "ExampleBootstrap.log", "ExampleAgent.log"):
            (prev.mkdir(exist_ok=True) or prev / n).write_text("x", encoding="utf-8")
        for n in ("latest.log", "debug.log"):
            (cur.mkdir(exist_ok=True) or cur / n).write_text("x", encoding="utf-8")
        expect(mod_log_files(prev) == {"ExampleBootstrap.log", "ExampleAgent.log"}, "mod_log_files skips vanilla's and the dated archives: %s" % mod_log_files(prev))
        expect(lost_log_files(prev, cur) == ["ExampleAgent.log", "ExampleBootstrap.log"], "A: the 1.21.10 state (mod logs missing) is flagged")
        expect(lost_log_files(prev, cur, ["ExampleAgent.log"]) == ["ExampleBootstrap.log"], "a recorded known-absent file is not flagged")
        for n in ("ExampleBootstrap.log", "ExampleAgent.log", "NewOnlyHere.log"):
            (cur / n).write_text("x", encoding="utf-8")
        expect(lost_log_files(prev, cur) == [], "B: after the fix (same set, plus a new file) it passes")
        expect(lost_log_files(None, cur) == [] and lost_log_files(pathlib.Path(d, "nope"), cur) == [], "no previous run: nothing to compare")
    # a resume must not trust a carried-over compile count (measured: it skipped audit + gates and said "done")
    r_ = object.__new__(Run)
    r_.summary = {"stages": {"measure": {"errors": 0}}}            # an earlier run's 0, no this_run
    r_.s_measure = lambda: {"done": "5 error(s)", "errors": 5, "this_run": True}
    r_.say = lambda *a_: None
    r_.resume_cmd = lambda st: "resume " + st
    expect(r_.compile_errors() == 5, "a stale count is re-measured")
    res_ = Run.not_compiled(r_, 5, "audit")
    expect(res_.get("code") == 10 and "stop" in res_, "not compiled is a stop, never a skip")
    r_.summary = {"stages": {"measure": {"errors": 0, "this_run": True}}}
    r_.s_measure = lambda: (_ for _ in ()).throw(AssertionError("must not re-measure"))
    expect(r_.compile_errors() == 0, "this run's own count is used as is")
    # ---- every --from-stage resume re-measures: a carried-over count is a record, never a measurement
    import contextlib, io, types
    from subprocess import CompletedProcess

    def resumed(stage, fresh, stale=0):
        """Resume from `stage` over a summary whose every stage says `stale` errors (this_run and all), with a measure
        that now answers `fresh`. -> (stop dict or None, how many times it measured, the commands it ran, summary)."""
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d)
            (repo / ".multiversion/targets/1.21.4").mkdir(parents=True)
            a_ = argparse.Namespace(repo=str(repo), target="1.21.4", json=None, budget=1.0, gates="ab", modid="m",
                                    base="x", direct=False, from_stage=stage, max_errors=None, confirm_spend=False)
            run = Run(a_)
            run.jpath.parent.mkdir(parents=True, exist_ok=True)
            run.jpath.write_text(json.dumps({"started": "2026-01-01T00:00:00+00:00", "stages": {
                s: {"done": "earlier run", "errors": stale, "this_run": True} for s in STAGES}}), encoding="utf-8")
            calls, cmds = [], []
            run.s_measure = lambda: (calls.append(1), {"done": f"{fresh} error(s)", "errors": fresh, "this_run": True})[1]
            for s in ("setup", "stage", "moves"):
                setattr(run, "s_" + s, lambda: {"done": "fake"})
            run.s_cost = lambda: {"skipped": "fake"}
            def fake_sh(cmd, log=None, cwd=None):
                cmds.append(" ".join(map(str, cmd)))
                if log:
                    pathlib.Path(log).parent.mkdir(parents=True, exist_ok=True)
                    pathlib.Path(log).write_text("[ci-gates] Gate A: PASS -- fake\n", encoding="utf-8")
                return CompletedProcess(cmd, 0 if fresh == 0 else 1, "all targets match\n", "")
            run.sh = fake_sh
            run.artifacts = lambda: ([], [pathlib.Path("neo-sources.jar")])
            run.audit_event_listeners = lambda: {"done": "listeners ok"}
            run.modid = lambda: "m"
            skipped, todo = select_stages(stage)
            run.summary["stages"] = carry_over(json.loads(run.jpath.read_text(encoding="utf-8"))["stages"], skipped)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                stop = execute(run, todo)
            return stop, len(calls), cmds, run.summary
    expect(carry_over({"measure": {"errors": 0, "this_run": True}, "loop": {"errors": 0}}, ["measure"])
           == {"measure": {"errors": 0}}, "carry_over drops this_run and keeps only skipped stages")
    for stage in STAGES:
        stop, n_meas, cmds, summ = resumed(stage, fresh=7)
        if stage in ("loop", "audit", "gates"):
            expect(n_meas == 1, f"--from-stage {stage} measured again (stale 0 not trusted): measured {n_meas}x")
            expect(stop is not None and stop["stage"] == stage, f"--from-stage {stage} with 7 real errors must stop at {stage}, got {stop and stop['stage']}")
        if stage in ("audit", "gates"):
            expect(stop and stop["code"] == 10 and not any("audit-mixin" in c or "ci-gates" in c for c in cmds),
                   f"--from-stage {stage}: no audit or gate runs over 7 errors")
        if stage == "loop":
            expect(summ["stages"]["loop"].get("start_errors") == 7, "the loop started from the fresh 7, not the stale 0")
            expect(not any("file-loop" in c and "already" in c for c in cmds), "loop was not skipped as 'already at 0'")
        if stage in ("setup", "stage", "moves", "measure"):
            expect(n_meas == 1, f"--from-stage {stage} runs the measure stage itself once")
        expect(summ["stages"].get("measure", {}).get("errors") in (7, 0) , "summary holds a number")
    # a fresh 0 over a stale 7 DOES proceed to the audit (the re-measure is not a stop in disguise)
    stop, n_meas, cmds, _s = resumed("audit", fresh=0, stale=7)
    expect(n_meas == 1 and stop is None or (stop and stop["stage"] != "audit"), "fresh 0 lets the audit run")
    expect(any("audit-mixin-targets" in c for c in cmds), "the audit tool ran over the fresh 0")
    # the single-run (no resume) path is untouched: the measure of THIS run is used as is
    stop, n_meas, cmds, _s = resumed("setup", fresh=0)
    expect(n_meas == 1 and stop is None or (stop and stop["stage"] != "measure"), "full run: one measure, then on")
    # ---- spend guard and the skipped-shared warning
    expect(spend_guard(900, 15) is None and spend_guard(1501, 15) and spend_guard(1501, 15, confirm=True) is None
           and spend_guard(50, 0.1) and spend_guard(50, 0.1, max_errors=60) is None, "spend guard thresholds")
    with tempfile.TemporaryDirectory() as d:
        mv = pathlib.Path(d) / "mv"; sh = pathlib.Path(d) / "shared"
        for v in ("1.21.3", "1.21.4", "1.21.5", "1.21.11"):
            (sh / v).mkdir(parents=True)
        (mv / "targets/1.21.11").mkdir(parents=True)
        (mv / "base.properties").write_text("minecraft_version=1.21.1\n", encoding="utf-8")
        (mv / "targets/1.21.11/target.properties").write_text("x=1\n", encoding="utf-8")
        expect(skipped_shared(mv, "1.21.11", sh) == ["1.21.3", "1.21.4", "1.21.5"], "a jump without shared_chain names the skipped libraries")
        (mv / "targets/1.21.11/target.properties").write_text("shared_chain=1.21.3,1.21.4,1.21.5\n", encoding="utf-8")
        expect(skipped_shared(mv, "1.21.11", sh) == [], "shared_chain silences it")
        (mv / "targets/1.21.4").mkdir(); (mv / "targets/1.21.4/target.properties").write_text("x=1\n", encoding="utf-8")
        (mv / "targets/1.21.5").mkdir(); (mv / "targets/1.21.5/target.properties").write_text("inherits=1.21.4\n", encoding="utf-8")
        expect(skipped_shared(mv, "1.21.5", sh) == [], "an ordinary hop (inherits the nearest lower target) skips nothing")
    # ---- a row harvested into the shared library leaves the target's table (else: a duplicate rule at table load)
    with tempfile.TemporaryDirectory() as d:
        mv = pathlib.Path(d); (mv / "targets/1.21.4").mkdir(parents=True); (mv / "shared/1.21.4").mkdir(parents=True)
        (mv / "shared/1.21.4/renames.tsv").write_text("re:a\tb\n", encoding="utf-8")
        (mv / "targets/1.21.4/renames.tsv").write_text("# head\nKeep\tMe\n# X3: proposed by a file-loop worker, kept\nre:a\tb\n",
                                                       encoding="utf-8")
        expect(drop_harvested_rows(mv, "1.21.4") == ["re:a\tb"]
               and (mv / "targets/1.21.4/renames.tsv").read_text(encoding="utf-8") == "# head\nKeep\tMe\n",
               "a harvested row (and its loop comment) leaves the target's table; the rest stays")
        (mv / "shared/1.21.4/renames.tsv").write_text("re:\\.isClientSide\\b(?!\\s*\\()\t.isClientSide()\n", encoding="utf-8")
        (mv / "targets/1.21.4/renames.tsv").write_text("e.level().isClientSide\te.level().isClientSide()\nKeep\tMe\n", encoding="utf-8")
        expect(drop_harvested_rows(mv, "1.21.4") == ["e.level().isClientSide\te.level().isClientSide()"],
               "a target row a shared regex already makes is dropped (else the two rewrite each other)")
    # ---- recipes: derived into the overlay, a hand overlay never touched, an authors' edit reaches a generated one
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d); rd = repo / "src/main/resources/data/examplemod/recipe"; rd.mkdir(parents=True)
        (rd / "a.json").write_text(json.dumps({"type": "minecraft:crafting_shapeless", "ingredients": [{"item": "minecraft:stick"}],
                                               "result": {"id": "examplemod:a"}}), encoding="utf-8")
        (rd / "b.json").write_text(json.dumps({"type": "minecraft:crafting_shapeless", "ingredients": [{"item": "minecraft:dirt"}],
                                               "result": {"id": "examplemod:b"}}), encoding="utf-8")
        td = repo / ".multiversion/targets/1.21.4"; hand = td / "resources/data/examplemod/recipe/b.json"
        hand.parent.mkdir(parents=True); hand.write_text("{\"hand\": true}", encoding="utf-8")
        n, ref = layer_recipes(repo, td, "1.21.4")
        a_ = json.loads((td / "resources/data/examplemod/recipe/a.json").read_text(encoding="utf-8"))
        expect(n == 1 and a_["ingredients"] == ["minecraft:stick"] and "hand" in hand.read_text(encoding="utf-8") and not ref,
               f"recipes: a.json derived, the hand overlay b.json kept ({n}, {ref})")
        (rd / "a.json").write_text(json.dumps({"type": "minecraft:crafting_shapeless", "ingredients": [{"item": "minecraft:bone"}],
                                               "result": {"id": "examplemod:a"}}), encoding="utf-8")
        layer_recipes(repo, td, "1.21.4")
        expect(json.loads((td / "resources/data/examplemod/recipe/a.json").read_text(encoding="utf-8"))["ingredients"] == ["minecraft:bone"],
               "recipes: an authors' edit reaches the generated overlay")
        expect(layer_recipes(repo, td, "1.21.1") == (0, []), "control: no recipe overlay before 1.21.2")
    # ---- 26.2 entity predicates: advancement + loot table derived (from the nearest parent overlay), hand overlay kept
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d); res = repo / "src/main/resources/data/examplemod"
        (res / "advancement").mkdir(parents=True); (res / "loot_table/entities").mkdir(parents=True)
        adv = {"criteria": {"k": {"trigger": "minecraft:player_killed_entity",
                                  "conditions": {"entity": {"type": "examplemod:boss"}}}}}
        (res / "advancement/a.json").write_text(json.dumps(adv), encoding="utf-8")
        (res / "advancement/b.json").write_text(json.dumps(adv), encoding="utf-8")
        (res / "advancement/plain.json").write_text(json.dumps({"criteria": {"t": {"trigger": "minecraft:tick"}}}), encoding="utf-8")
        loot = {"pools": [{"conditions": [{"condition": "minecraft:entity_properties", "entity": "this",
                                           "predicate": {"type": "minecraft:player"}}]}]}
        (res / "loot_table/entities/boss.json").write_text(json.dumps(loot), encoding="utf-8")
        mv = repo / ".multiversion"
        for t, inh in (("26.1.2", ""), ("26.2", "26.1.2")):
            (mv / "targets" / t).mkdir(parents=True)
            (mv / "targets" / t / "target.properties").write_text(f"inherits={inh}\n" if inh else "", encoding="utf-8")
        par = mv / "targets/26.1.2/resources/data/examplemod/advancement/a.json"; par.parent.mkdir(parents=True)
        par.write_text(json.dumps({"criteria": {"k": {"trigger": "minecraft:player_killed_entity",
                                                      "conditions": {"entity": {"type": "examplemod:other"}}}}}), encoding="utf-8")
        td = mv / "targets/26.2"; hand = td / "resources/data/examplemod/advancement/b.json"
        hand.parent.mkdir(parents=True); hand.write_text("{\"hand\": true}", encoding="utf-8")
        n, ref = layer_entity_predicates(repo, td, "26.2", mv)
        a_ = json.loads((td / "resources/data/examplemod/advancement/a.json").read_text(encoding="utf-8"))
        l_ = json.loads((td / "resources/data/examplemod/loot_table/entities/boss.json").read_text(encoding="utf-8"))
        expect(n == 2 and not ref and a_["criteria"]["k"]["conditions"]["entity"] == {"entity_type": "examplemod:other"}
               and l_["pools"][0]["conditions"][0]["predicate"] == {"entity_type": "minecraft:player"}
               and "hand" in hand.read_text(encoding="utf-8")
               and not (td / "resources/data/examplemod/advancement/plain.json").exists(),
               f"26.2 entity predicates: criterion + loot carriers derived from the parent overlay, hand overlay kept ({n}, {ref})")
        expect(layer_entity_predicates(repo, td, "26.2", mv) == (0, []), "26.2 entity predicates: idempotent")
        expect(layer_entity_predicates(repo, mv / "targets/26.1.2", "26.1.2", mv) == (0, []),
               "control: no entity-predicate overlay before 26.2")
    # ---- 26.3 data: block states as {id, properties}, trim material palette_id + palette PNG + item-trim atlas moved
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d); res = repo / "src/main/resources"
        pool = res / "data/examplemod/worldgen/template_pool/p.json"; pool.parent.mkdir(parents=True)
        pool.write_text(json.dumps({"elements": [{"element": {"processors": {"processors": [
            {"processor_type": "minecraft:block_ignore", "blocks": [{"Name": "minecraft:structure_block"}]}]}}}]}),
            encoding="utf-8")
        (res / "data/examplemod/worldgen/template_pool/plain.json").write_text('{"elements": []}', encoding="utf-8")
        tm = res / "data/examplemod/trim_material/gem.json"; tm.parent.mkdir(parents=True)
        tm.write_text(json.dumps({"asset_name": "gem", "description": {"translate": "x"}, "ingredient": "examplemod:gem"}),
                      encoding="utf-8")
        pal = res / "assets/examplemod/textures/trims/color_palettes/gem.png"; pal.parent.mkdir(parents=True)
        pal.write_bytes(b"\x89PNGfake")
        at = res / "assets/minecraft/atlases"; at.mkdir(parents=True)
        perm = {"type": "paletted_permutations", "palette_key": "trims/color_palettes/trim_palette",
                "permutations": {"gem": "examplemod:trims/color_palettes/gem"}}
        (at / "blocks.json").write_text(json.dumps({"sources": [dict(perm, textures=["trims/items/helmet_trim"])]}), encoding="utf-8")
        (at / "armor_trims.json").write_text(json.dumps({"sources": [dict(perm, textures=["trims/models/armor/coast"])]}), encoding="utf-8")
        lt = res / "data/examplemod/loot_table/chests/c.json"; lt.parent.mkdir(parents=True)
        lt.write_text(json.dumps({"type": "minecraft:chest", "pools": [{"rolls": {"min": 1, "max": 2}, "conditions": [
            {"condition": "minecraft:survives_explosion"}], "entries": [
            {"type": "minecraft:item", "name": "minecraft:stone", "functions": [{"function": "set_count", "count": {"min": 1.0, "max": 3}}]},
            {"type": "minecraft:tag", "name": "examplemod:gems", "expand": True}]}]}), encoding="utf-8")
        adv = res / "data/examplemod/advancement/a.json"; adv.parent.mkdir(parents=True)
        adv.write_text(json.dumps({"parent": "examplemod:root", "display": {"background": "x", "title": "t"}, "criteria": {
            "k": {"trigger": "minecraft:player_killed_entity", "conditions": {"entity": {"type": "examplemod:boss"}}}}}),
            encoding="utf-8")
        (res / "data/examplemod/loot_table/weird.json").write_text(json.dumps({"pools": [{"rolls": 1, "entries": [
            {"type": "examplemod:custom"}]}]}), encoding="utf-8")
        mv = repo / ".multiversion"
        for t, inh in (("26.2", ""), ("26.3", "26.2")):
            (mv / "targets" / t).mkdir(parents=True)
            (mv / "targets" / t / "target.properties").write_text(f"inherits={inh}\n" if inh else "", encoding="utf-8")
        td = mv / "targets/26.3"; o = td / "resources"
        n, ref = layer_263_data(repo, td, "26.3", mv)
        p_ = json.loads((o / "data/examplemod/worldgen/template_pool/p.json").read_text(encoding="utf-8"))
        t_ = json.loads((o / "data/examplemod/trim_material/gem.json").read_text(encoding="utf-8"))
        i_ = json.loads((o / "assets/minecraft/atlases/items.json").read_text(encoding="utf-8"))
        l_ = json.loads((o / "data/examplemod/loot_table/chests/c.json").read_text(encoding="utf-8"))
        a_ = json.loads((o / "data/examplemod/advancement/a.json").read_text(encoding="utf-8"))
        expect(l_["pools"] == [{"rolls": {"type": "minecraft:uniform", "min": 1, "max": 2},
                                "condition": {"type": "minecraft:survives_explosion"}, "entries": [
            {"type": "minecraft:item", "name": "minecraft:stone",
             "modifier": {"type": "set_count", "count": {"type": "minecraft:uniform", "min": 1, "max": 3}}},
            {"type": "minecraft:tag", "expand": True, "items": "#examplemod:gems"}]}]
               and a_["display"] == {"title": "t"}
               and a_["criteria"]["k"]["conditions"]["entity"] == {"type": "minecraft:entity_properties", "entity": "this",
                                                                    "predicate": {"entity_type": "examplemod:boss"}}
               and [r.split(":")[0] for r in ref] == ["data/examplemod/loot_table/weird.json"]
               and not (o / "data/examplemod/loot_table/weird.json").exists(),
               f"26.3 loot grammar: conditions/functions/numbers/tag entry/criterion entity derived, an unknown entry refused ({ref})")
        ref = [r for r in ref if "weird" not in r]
        # an untyped {min,max} the transform does not convert (set_damage's damage) is refused, not passed through;
        # a predicate's range in a condition is not a number provider and is left alone
        import runpy
        gro = runpy.run_path(str(ROOT / "templates/multi-version/tools/gen-resource-overlays.py"), run_name="gro")
        try:
            gro["loot_grammar_263"]({"pools": [{"rolls": 1, "entries": [{"type": "minecraft:item", "name": "x:y", "functions": [
                {"function": "minecraft:set_damage", "damage": {"min": 0.1, "max": 0.5}}]}]}]}, "data/x/loot_table/d.json", {})
            refused_dmg = False
        except gro["Refused"]:
            refused_dmg = True
        rng = gro["loot_grammar_263"]({"pools": [{"rolls": 1, "entries": [{"type": "minecraft:item", "name": "x:y", "functions": [
            {"function": "minecraft:set_count", "count": 2, "conditions": [{"condition": "minecraft:entity_properties",
             "entity": "this", "predicate": {"distance": {"absolute": {"min": 1}}}}]}]}]}]}, "data/x/loot_table/r.json", {})
        expect(refused_dmg and rng["pools"][0]["entries"][0]["modifier"]["condition"]["predicate"] == {"distance": {"absolute": {"min": 1}}},
               "26.3 loot grammar: an unconverted untyped number provider is refused; a predicate range is kept")
        # a glowstone-shaped table: limit_count's limit is an IntLimit (bounds are int providers, so untyped and bare
        # ints are right), and minecraft:filtered's item_filter count is a range -- both convert; an untyped
        # {min,max} provider nested in limit_count's bound is still a provider and is typed
        glow = gro["loot_grammar_263"]({"pools": [{"rolls": 1, "entries": [{"type": "minecraft:item", "name": "x:y", "functions": [
            {"function": "minecraft:set_count", "count": {"min": 2.0, "max": 4.0, "type": "minecraft:uniform"}},
            {"function": "minecraft:limit_count", "limit": {"min": 1, "max": 4}},
            {"function": "minecraft:limit_count", "limit": {"max": {"min": 2, "max": 3}}},
            {"function": "minecraft:filtered", "item_filter": {"items": "x:y", "count": {"min": 1, "max": 8}},
             "modifier": {"type": "minecraft:set_count", "count": 1}}]}]}]}, "data/x/loot_table/g.json", {})
        gm = glow["pools"][0]["entries"][0]["modifier"]
        try:
            gro["loot_grammar_263"]({"pools": [{"rolls": 1, "entries": [{"type": "minecraft:item", "name": "x:y", "functions": [
                {"function": "minecraft:enchant_with_levels", "levels": {"min": 5, "max": 10}}]}]}]}, "data/x/loot_table/e.json", {})
            refused_lv = False
        except gro["Refused"]:
            refused_lv = True
        expect(gm[1] == {"type": "minecraft:limit_count", "limit": {"min": 1, "max": 4}}
               and gm[2]["limit"] == {"max": {"type": "minecraft:uniform", "min": 2, "max": 3}}
               and gm[3]["item_filter"] == {"items": "x:y", "count": {"min": 1, "max": 8}} and refused_lv,
               "26.3 loot grammar: limit_count's IntLimit and filtered's item_filter range convert; an untyped provider elsewhere is still refused")
        expect(not ref and p_["elements"][0]["element"]["processors"]["processors"][0]["blocks"] == [{"id": "minecraft:structure_block"}]
               and not (o / "data/examplemod/worldgen/template_pool/plain.json").exists()
               and t_ == {"palette_id": "examplemod:trim/gem", "description": {"translate": "x"}}
               and (o / "assets/examplemod/textures/palettes/trim/gem.png").read_bytes() == b"\x89PNGfake"
               and i_["sources"] == [{"type": "minecraft:paletted_permutations", "palette_key": "minecraft:trim_base",
                                      "permutations": {"gem": "examplemod:trim/gem"}, "textures": ["trims/items/helmet_trim"]}]
               and json.loads((o / "assets/minecraft/atlases/blocks.json").read_text(encoding="utf-8")) == {"sources": []}
               and json.loads((o / "assets/minecraft/atlases/armor_trims.json").read_text(encoding="utf-8")) == {"sources": []},
               f"26.3 data: block state, trim palette, atlases derived ({n}, {ref})")
        n2, ref2 = layer_263_data(repo, td, "26.3", mv)
        expect(n2 == 0 and len(ref2) == 1, f"26.3 data: idempotent ({n2}, {ref2})")
        expect(layer_entity_predicates(repo, td, "26.3", mv) == (0, []), "26.3: the 26.2 entity-predicate step steps aside")
        expect(layer_263_data(repo, mv / "targets/26.2", "26.2", mv) == (0, []) and not (mv / "targets/26.2/resources").exists(),
               "control: no 26.3 data overlay before 26.3")
    # ---- client item definitions: written into the target's overlay, an existing one is never touched, 1.21.1 gets none
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d); md = repo / "src/main/resources/assets/examplemod/models/item"; md.mkdir(parents=True)
        (md / "sword.json").write_text('{"parent": "item/handheld", "textures": {"layer0": "examplemod:item/sword"}}', encoding="utf-8")
        (md / "special.json").write_text('{"parent": "item/generated", "textures": {"layer0": "examplemod:item/special"}}', encoding="utf-8")
        td = repo / ".multiversion/targets/1.21.4"
        keep = td / "resources/assets/examplemod/items/special.json"; keep.parent.mkdir(parents=True)
        keep.write_text('{"model": {"type": "minecraft:special"}}', encoding="utf-8")
        n = layer_client_items(repo, td, "1.21.4")
        expect(n == 1 and (keep.parent / "sword.json").is_file() and "special" in keep.read_text(encoding="utf-8"),
               f"client items: the missing one written, the overlay's own kept ({n})")
        expect(layer_client_items(repo, td, "1.21.4") == 0, "client items: idempotent")
        expect(layer_client_items(repo, repo / ".multiversion/targets/1.21.1x", "1.21.1") == 0, "control: none before 1.21.2")
        # spawn-egg colours from the authors' registration (hex literals), 1.21.1 overrides as select/condition
        jd = repo / "src/main/java/com/example/mod"; jd.mkdir(parents=True)
        (jd / "ModItems.java").write_text('ITEMS.register("mob_spawn_egg",\n    () -> new DeferredSpawnEggItem(ModEntities.MOB, '
                                          '0x4B0082, -1, new Item.Properties()));\n', encoding="utf-8")
        (md / "mob_spawn_egg.json").write_text('{"parent": "item/template_spawn_egg"}', encoding="utf-8")
        (md / "plate.json").write_text(json.dumps({"parent": "item/generated", "textures": {"layer0": "examplemod:item/plate"},
            "overrides": [{"predicate": {"trim_type": 0.1}, "model": "examplemod:item/plate_quartz_trim"},
                          {"predicate": {"trim_type": 0.6}, "model": "examplemod:item/plate_gold_trim"},
                          {"predicate": {"trim_type": 1.1}, "model": "examplemod:item/plate_gem_trim"}]}), encoding="utf-8")
        (md / "jar.json").write_text(json.dumps({"parent": "item/generated", "textures": {"layer0": "examplemod:item/jar"},
            "overrides": [{"predicate": {"examplemod:full": 1}, "model": "examplemod:item/jar_full"}]}), encoding="utf-8")
        (md / "bow.json").write_text(json.dumps({"parent": "item/generated", "textures": {"layer0": "examplemod:item/bow"},
            "overrides": [{"predicate": {"pulling": 1}, "model": "examplemod:item/bow_pulling"}]}), encoding="utf-8")
        tm = repo / "src/main/resources/data/examplemod/trim_material"; tm.mkdir(parents=True)
        (tm / "gem.json").write_text('{"asset_name": "gem", "item_model_index": 1.1}', encoding="utf-8")
        layer_client_items(repo, td, "1.21.4")
        rd = lambda n: json.loads((keep.parent / f"{n}.json").read_text(encoding="utf-8"))["model"]
        egg, plate, jar, bow = rd("mob_spawn_egg"), rd("plate"), rd("jar"), rd("bow")
        when = {c["when"]: c["model"]["model"] for c in plate.get("cases", [])}
        expect(egg.get("tints") == [{"type": "minecraft:constant", "value": 0x4B0082}, {"type": "minecraft:constant", "value": -1}],
               f"client items: a spawn egg gets its two 1.21.1 colours, hex literals read ({egg})")
        expect(plate.get("type") == "minecraft:select" and plate.get("property") == "minecraft:trim_material"
               and when.get("minecraft:quartz") == "examplemod:item/plate_quartz_trim"
               and when.get("minecraft:iron") == "examplemod:item/plate_quartz_trim"
               and when.get("minecraft:diamond") == "examplemod:item/plate_gold_trim"
               and when.get("examplemod:gem") == "examplemod:item/plate_gem_trim"
               and plate["fallback"]["model"] == "examplemod:item/plate",
               f"client items: trim_type overrides -> select on trim_material, 1.21.1's last-match-below rule ({when})")
        expect(jar.get("type") == "minecraft:condition" and jar.get("property") == "examplemod:full"
               and jar["on_true"]["model"] == "examplemod:item/jar_full" and jar["on_false"]["model"] == "examplemod:item/jar",
               f"client items: a mod predicate -> condition on that property ({jar})")
        expect(bow == {"type": "minecraft:model", "model": "examplemod:item/bow"},
               f"control: a predicate with no item-definition form keeps the plain model ({bow})")
    # ---- GeckoLib 5 assets: copied to geckolib/{models,animations}, a hand overlay kept, other ledger lines kept, 4.x gets none
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d); ad = repo / "src/main/resources/assets/examplemod"
        (ad / "geo").mkdir(parents=True); (ad / "animations/armor").mkdir(parents=True)
        (ad / "geo/mob.geo.json").write_text('{"m": 1}', encoding="utf-8")
        (ad / "geo/hand.geo.json").write_text('{"m": 2}', encoding="utf-8")
        (ad / "animations/armor/cape.animation.json").write_text('{"a": 1}', encoding="utf-8")
        td = repo / ".multiversion/targets/1.21.5"; td.mkdir(parents=True)
        (td / "DEPS.md").write_text("| `curse.maven:geckolib-388172` | required | `1` | curse.maven:geckolib-388172:2 | "
                                    "CurseForge file 2 (geckolib-neoforge-1.21.5-5.1.0.jar) |\n", encoding="utf-8")
        gm = td / "resources/assets/examplemod/geckolib/models"
        (gm / "hand.geo.json").parent.mkdir(parents=True); (gm / "hand.geo.json").write_text('{"hand": true}', encoding="utf-8")
        (td / "GENERATED.txt").write_text("data/examplemod/recipe/a.json\n", encoding="utf-8")
        n = layer_geckolib_assets(repo, td, "1.21.5")
        led = (td / "GENERATED.txt").read_text(encoding="utf-8").split()
        expect(n == 2 and (gm / "mob.geo.json").read_text(encoding="utf-8") == '{"m": 1}'
               and (td / "resources/assets/examplemod/geckolib/animations/armor/cape.animation.json").is_file()
               and "hand" in (gm / "hand.geo.json").read_text(encoding="utf-8")
               and "data/examplemod/recipe/a.json" in led and "assets/examplemod/geckolib/models/mob.geo.json" in led
               and "assets/examplemod/geckolib/models/hand.geo.json" not in led,
               f"geckolib 5: copied to the 5.x paths (sub-folder kept), the hand overlay and the recipe ledger line kept ({n}, {led})")
        expect(layer_geckolib_assets(repo, td, "1.21.5") == 0, "geckolib 5: idempotent")
        (ad / "geo/mob.geo.json").write_text('{"m": 3}', encoding="utf-8")
        layer_geckolib_assets(repo, td, "1.21.5")
        expect((gm / "mob.geo.json").read_text(encoding="utf-8") == '{"m": 3}', "geckolib 5: an authors' edit reaches the copy")
        (ad / "geo/mob.geo.json").unlink()
        layer_geckolib_assets(repo, td, "1.21.5")
        expect(not (gm / "mob.geo.json").exists() and (gm / "hand.geo.json").exists(),
               "geckolib 5: a copy whose authors' file is gone is removed; the hand overlay stays")
        t4 = repo / ".multiversion/targets/1.21.4"; t4.mkdir(parents=True)
        (t4 / "DEPS.md").write_text("| `curse.maven:geckolib-388172` | required | `1` | curse.maven:geckolib-388172:3 | "
                                    "CurseForge file 3 (geckolib-neoforge-1.21.4-4.8.5.jar) |\n", encoding="utf-8")
        expect(layer_geckolib_assets(repo, t4, "1.21.4") == 0 and not (t4 / "resources").exists(),
               "control: a GeckoLib 4.x target gets no 5.x copies")
        t0 = repo / ".multiversion/targets/1.21.3"; t0.mkdir(parents=True)
        expect(layer_geckolib_assets(repo, t0, "1.21.3") == 0, "control: a target that names no GeckoLib gets none")
        for line_, want in (("geckolib-neoforge-1.21.10-5.3-alpha-3.jar", 5), ("software.bernie.geckolib:geckolib-neoforge-1.21.1:4.7.1", 4),
                            ("prop.geckolib_version=5.2.2", 5)):
            (t0 / "target.properties").write_text(line_ + "\n", encoding="utf-8")
            expect(target_geckolib_major(t0) == want, f"geckolib version from {line_!r}: {target_geckolib_major(t0)}")
    # ---- the overlay audit: an overlay that deleted an authors' override is flagged, a renamed copy is not
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d); sd = repo / "src/main/java/m"; sd.mkdir(parents=True)
        (sd / "A.java").write_text("class A extends B {\n @Override\n public void initializeClient(C c) { c.x(); }\n"
                                   " @Override\n public int f() { return 1; }\n}\n", encoding="utf-8")
        (sd / "Same.java").write_text("class S extends B {\n @Override\n public int f() { return 1; }\n}\n", encoding="utf-8")
        td = repo / ".multiversion/targets/1.21.4"; od = td / "java/m"; od.mkdir(parents=True)
        (od / "A.java").write_text("class A extends B {\n @Override\n public int f() { return 2; }\n}\n", encoding="utf-8")
        (od / "Same.java").write_text("class S extends Bee {\n @Override\n public int g() { return 1; }\n}\n", encoding="utf-8")
        got = overlay_stubs(repo, td)
        expect([g["file"] for g in got] == [".multiversion/targets/1.21.4/java/m/A.java"] and "initializeClient" in got[0]["why"],
               f"overlay audit: a deleted override is flagged, a renamed one is not: {got}")
    # ---- a shared AT line marked `# until <v>` reaches targets up to <v> only
    with tempfile.TemporaryDirectory() as td:
        mvd = pathlib.Path(td) / ".multiversion"
        (mvd / "shared/1.21.3").mkdir(parents=True)
        for t, inh in (("1.21.3", None), ("1.21.4", "1.21.3"), ("1.21.5", "1.21.4")):
            (mvd / "targets" / t).mkdir(parents=True)
            (mvd / "targets" / t / "target.properties").write_text(f"inherits={inh}\n" if inh else "x=1\n", encoding="utf-8")
        (mvd / "shared/1.21.3/accesstransformer.cfg").write_text(
            "public a.B <init>(I)V # helper (until 1.21.4)\npublic a.C f\n", encoding="utf-8")
        got = {t: merge_shared_at(mvd, t, pathlib.Path(td) / f"at-{t}.cfg") for t in ("1.21.4", "1.21.5")}
        expect(len(got["1.21.4"]) == 2 and got["1.21.5"] == ["public a.C f"], f"AT until ceiling: {got}")
    # ---- green gates are not done with an unreviewed stub (a resume past the loop never asked)
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        expect(unreviewed_stubs(td) == [], "no STUBS.json: nothing open")
        (td / "STUBS.json").write_text(json.dumps([{"file": "A.java", "why": "x", "reviewed": None},
                                                   {"file": "B.java", "why": "y", "reviewed": "fine"}]), encoding="utf-8")
        expect([e["file"] for e in unreviewed_stubs(td)] == ["A.java"], "only the unreviewed entry is open")
    # ---- stub signals survive a resume and stay a stop until reviewed
    with tempfile.TemporaryDirectory() as d:
        td = pathlib.Path(d)
        record_stubs(td, [{"file": "A.java", "why": "emptied a method"}])
        got = record_stubs(td, [])
        expect([e["file"] for e in got] == ["A.java"] and got[0]["reviewed"] is None, "a resumed run keeps an earlier run's stub")
        e = json.loads((td / "STUBS.json").read_text(encoding="utf-8")); e[0]["reviewed"] = "the API is gone; same effect"
        (td / "STUBS.json").write_text(json.dumps(e), encoding="utf-8")
        expect(record_stubs(td, [{"file": "A.java", "why": "emptied a method"}])[0]["reviewed"], "a reviewed entry stays reviewed")
        expect(record_stubs(td, [{"file": "A.java", "why": "deleted a file"}])[0]["reviewed"] is None, "a new reason reopens it")
    # ---- a version is done only after its real-client run: green A/B without a Gate C record exits 17, with one 0
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d); (repo / ".multiversion/targets/1.21.4").mkdir(parents=True)
        a_ = argparse.Namespace(repo=str(repo), target="1.21.4", json=None, budget=1.0, gates="ab", modid="m", base="x",
                                direct=False, from_stage="cost", to_stage=None, max_errors=None, confirm_spend=False, port_checkout=[])
        run = Run(a_); run.s_cost = lambda: {"skipped": "fake"}
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            rc_ab = drive(run, a_, [s_ for s_ in STAGES if s_ != "cost"], ["cost"])
            (repo / ".multiversion/targets/1.21.4/GATES.md").write_text("| Gate C -- real client, `launch` | PASS | ok |\n",
                                                                          encoding="utf-8")
            rc_c = drive(run, a_, [s_ for s_ in STAGES if s_ != "cost"], ["cost"])
        # R1: Gate C green is not done without the review record (exit 18); with it, for the commit reviewed, done
        expect(rc_ab == 17 and rc_c == UNDONE_EXIT, f"done needs a recorded Gate C and the review: A/B only -> {rc_ab}, "
                                                    f"with Gate C but no REVIEW.md -> {rc_c} (want {UNDONE_EXIT})")
        g_ = lambda *x: subprocess.run(["git", "-C", str(repo), *x], capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()
        (repo / ".gitignore").write_text("build/\n", encoding="utf-8")      # as every fork's
        g_("init", "-q"); g_("config", "user.email", "a@b"); g_("config", "user.name", "a"); g_("add", "-A"); g_("commit", "-qm", "t")
        (repo / ".multiversion/targets/1.21.4/GATES.md").write_text(       # the gates ran at that commit
            f"Gates for `m` at `{g_('rev-parse', '--short', 'HEAD')}` for Minecraft 1.21.4 (local environment):\n\n| Gate C -- real client, `launch` | PASS | ok |\n",
            encoding="utf-8")
        rv = repo / ".multiversion/targets/1.21.4/REVIEW.md"
        rv.write_text(rv.read_text(encoding="utf-8").replace("- reviewer: ?", "- reviewer: fresh agent r1")
                      .replace("- date: ?", "- date: 2026-10-16").replace("- commit: ?", "- commit: " + g_("rev-parse", "--short", "HEAD")),
                      encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            rc_rev = drive(run, a_, [s_ for s_ in STAGES if s_ != "cost"], ["cost"])
        expect(rc_rev == 0 and (repo / ".multiversion/targets/1.21.4/TEST-ITEMS.md").is_file(),
               f"Gate C + a complete review (nothing to judge) + test items generated: done, exit {rc_rev} {target_done(repo, '1.21.4')}")
        # --from-stage cost over an older GATES.md with an unreviewed stub: not done (exit 10, the stub named)
        (repo / ".multiversion/targets/1.21.4/STUBS.json").write_text(json.dumps([{"file": "A.java", "why": "emptied f()",
                                                                                  "reviewed": None}]), encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc_stub = drive(run, a_, [s_ for s_ in STAGES if s_ != "cost"], ["cost"])
        expect(rc_stub == 10 and "A.java" in buf.getvalue(), f"a run resumed past the gates with an open stub: exit {rc_stub}")
        (repo / ".multiversion/targets/1.21.4/STUBS.json").unlink()
        # --gates none is not done, even with a Gate C on record; a partial run (--to-stage) still ends 0
        a_.gates = "none"
        with contextlib.redirect_stdout(io.StringIO()):
            rc_none = drive(run, a_, [], list(STAGES[-2:]))
            a_.to_stage = "audit"; run.s_audit = lambda: {"done": "fake"}
            rc_part = drive(run, a_, [], ["audit"])
        a_.to_stage, a_.gates = None, "ab"
        expect(rc_none == 17 and rc_part == 0, f"--gates none: exit {rc_none} (want 17); --to-stage audit: {rc_part} (want 0)")
    # ---- a full gate run that is not green removes the Gate C record, so `--gates auto` runs full again
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d); td = repo / ".multiversion/targets/1.21.4"; td.mkdir(parents=True)
        for gates, code, want_record in (("full", 1, False), ("ab", 1, True), ("full", 0, True)):
            (td / "GATES.md").write_text("| Gate C -- real client, `launch` | PASS | ok |\n", encoding="utf-8")
            a_ = argparse.Namespace(repo=str(repo), target="1.21.4", json=None, budget=1.0, gates=gates, modid="m", base="x",
                                    direct=False, from_stage="gates", to_stage=None, max_errors=None, confirm_spend=False,
                                    port_checkout=[])
            run = Run(a_); run.compile_errors = lambda: 0; run.modid = lambda: "m"; run.lost_logs = lambda: ([], None)
            def gsh(cmd, log=None, cwd=None, code=code, gates=gates):
                pathlib.Path(log).parent.mkdir(parents=True, exist_ok=True)
                pathlib.Path(log).write_text("[ci-gates] Gate C -- real client: " + ("PASS" if code == 0 else "FAIL") + "\n",
                                             encoding="utf-8")
                if code == 0:
                    (run.work / "ci-gates-summary.md").write_text("| Gate C -- real client, `launch` | PASS | ok |\n", encoding="utf-8")
                return CompletedProcess(cmd, code, "", "")
            run.sh = gsh
            run.s_gates()
            expect((td / "GATES.md").is_file() == want_record and gates_auto(td) == ("ab" if want_record else "full"),
                   f"--gates {gates} exit {code}: GATES.md {'kept/written' if want_record else 'removed'}")
    # ---- issue #113: a Gate C client the OOM killer killed is environmental -- RESIDUE.md says re-run, no fixer
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d); td = repo / ".multiversion/targets/1.21.4"; td.mkdir(parents=True)
        a_ = argparse.Namespace(repo=str(repo), target="1.21.4", json=None, budget=1.0, gates="full", modid="m", base="x",
                                direct=False, from_stage="gates", to_stage=None, max_errors=None, confirm_spend=False,
                                port_checkout=[])
        for row, env_want in (("JVM killed (exit 137, OOM confirmed: cgroup oom_kill 3 -> 4) after M_BOOT_TEST: spawned 11; "
                               "2900 MB free at start -- environmental, not a code failure: re-run when memory is free", True),
                              ("not enough free memory (900 MB free, need 3584; read from MemAvailable): ...", True),
                              ("java.lang.IllegalStateException: Missing registry: x", False)):
            run = Run(a_); run.compile_errors = lambda: 0; run.modid = lambda: "m"
            def ksh(cmd, log=None, cwd=None, row=row):
                pathlib.Path(log).parent.mkdir(parents=True, exist_ok=True)
                pathlib.Path(log).write_text(f"[ci-gates] Gate C -- real client, `spawn`: FAIL -- {row}\n", encoding="utf-8")
                return CompletedProcess(cmd, 1, "", "")
            run.sh = ksh
            st = run.s_gates()
            expect(st.get("code") == 12 and bool(st.get("environment")) == env_want,
                   f"a Gate C row {row[:30]!r}: environmental={env_want} (got {st.get('environment')!r})")
            if env_want:
                md, data = residue_report("gates", st["stop"], [], None, [], st["gates"], "python3 tools/fork-port.py x",
                                          environment=st["environment"])
                expect("re-run when memory is free" in md and "Fix what is listed here" not in md
                       and "## Failing gates" not in md and data.get("environment")
                       and not any("fix" in c.lower() and "not" not in c.lower() for c in st["choices"]),
                       "RESIDUE.md for a killed client: re-run when memory is free, with the evidence, no fixer")
                expect(any("free" in e for e in st["environment"]), "the evidence carries the free-memory figure")
    md, _d = residue_report("gates", "x", [], None, [], ["Gate B: FAIL -- tests"], "r")
    expect("Fix what is listed here" in md and "Environmental" not in md, "a code failure's RESIDUE.md is unchanged")
    # ---- issue #122: the run's own Gradle leaves no resident daemon before Gate C (layer forks only), and a refused
    # Gate C names what holds the memory
    with tempfile.TemporaryDirectory() as d:
        for layer in (True, False):
            repo = pathlib.Path(d) / ("layer" if layer else "single"); repo.mkdir()
            if layer:
                (repo / ".multiversion").mkdir(); (repo / ".multiversion/base.properties").write_text("mc=1.21.1\n", encoding="utf-8")
            a_ = argparse.Namespace(repo=str(repo), target="1.21.4", json=None, budget=1.0, gates="ab", modid="m", base="x",
                                    direct=False, from_stage=None, to_stage=None, max_errors=None, confirm_spend=False,
                                    port_checkout=[])
            seen = []
            real_run = subprocess.run
            subprocess.run = lambda cmd, **k: (seen.append((cmd, k)), CompletedProcess(cmd, 0, "", ""))[1]
            try:
                Run(a_).gradle(["compileJava"], str(repo / "g.log"))
            finally:
                subprocess.run = real_run
            cmd, kw = seen[0]
            opts = (kw.get("env") or {}).get("GRADLE_OPTS", "")
            if layer:
                expect("-Dorg.gradle.daemon=false" in opts and kw["env"].get("PATH") == os.environ.get("PATH"),
                       f"a layer fork's Gradle runs daemon-less (GRADLE_OPTS), with the rest of the environment: {opts!r}")
            else:
                expect(kw.get("env") is None and "--no-daemon" not in cmd,
                       f"a single-version checkout's environment is inherited unchanged: {kw.get('env')!r}")
    if "gradle_env" in globals():
        e_ = gradle_env({"GRADLE_OPTS": "-Xmx1g -Dorg.gradle.daemon=true", "A": "1"}, True)
        expect(e_["GRADLE_OPTS"] == "-Xmx1g -Dorg.gradle.daemon=true", "an explicit org.gradle.daemon in GRADLE_OPTS stands")
        expect(gradle_env({"GRADLE_OPTS": "-Xmx1g"}, True)["GRADLE_OPTS"] == "-Xmx1g -Dorg.gradle.daemon=false"
               and gradle_env({}, True)["GRADLE_OPTS"] == "-Dorg.gradle.daemon=false", "NO_DAEMON is appended to GRADLE_OPTS")
    with tempfile.TemporaryDirectory() as d:
        proc = pathlib.Path(d) / "proc"
        for pid, rss, argv in ((4242, 2300 * 1024, ["/usr/lib/jvm/bin/java", "-Xmx6g", "-cp", "/x/gradle-daemon.jar",
                                                      "org.gradle.launcher.daemon.bootstrap.GradleDaemon", "8.10"]),
                               (4250, 700 * 1024, ["java", "-cp", "a.jar", "worker.org.gradle.process.internal.worker.GradleWorkerMain", "w1"]),
                               (5000, 900 * 1024, ["/opt/jdk/bin/java", "-Xmx4g", "-cp", "c.jar", "net.example.client.Main", "--x"]),
                               (6000, 50 * 1024, ["/usr/bin/fakeproc", "--a", "b\nc"]),
                               (7000, None, ["kthread"])):
            (proc / str(pid)).mkdir(parents=True)
            (proc / str(pid) / "status").write_text("Name:\tx\n" + (f"VmRSS:\t {rss} kB\n" if rss else ""), encoding="utf-8")
            (proc / str(pid) / "cmdline").write_bytes("\0".join(argv).encode() + b"\0")
        (proc / "self").mkdir()
        fake = None
        if "memory_holders" in globals():
            fake = memory_holders(str(proc), top=3)
            expect(fake == ["PID 4242 2300 MB java ... GradleDaemon", "PID 5000 900 MB java ... net.example.client.Main",
                            "PID 4250 700 MB java ... GradleWorkerMain"], f"memory_holders: largest first, named by main class: {fake}")
        repo = pathlib.Path(d) / "r"; (repo / ".multiversion/targets/1.21.4").mkdir(parents=True)
        a_ = argparse.Namespace(repo=str(repo), target="1.21.4", json=None, budget=1.0, gates="full", modid="m", base="x",
                                direct=False, from_stage="gates", to_stage=None, max_errors=None, confirm_spend=False,
                                port_checkout=[])
        run = Run(a_); run.compile_errors = lambda: 0; run.modid = lambda: "m"
        def gsh(cmd, log=None, cwd=None):
            pathlib.Path(log).parent.mkdir(parents=True, exist_ok=True)
            pathlib.Path(log).write_text("[ci-gates] Gate C -- real client, `spawn`: FAIL -- not enough free memory (3230 MB "
                                         "free, need 3584; read from MemAvailable): ...\n", encoding="utf-8")
            return CompletedProcess(cmd, 1, "", "")
        run.sh = gsh
        real_mh = globals().get("memory_holders")
        if real_mh:
            globals()["memory_holders"] = lambda *x, **k: fake
        try:
            st = run.s_gates()
        finally:
            if real_mh:
                globals()["memory_holders"] = real_mh
        md, _d = residue_report("gates", st["stop"], [], None, [], st.get("gates", []), "r", environment=st.get("environment"))
        held = [l for l in md.splitlines() if "memory held by" in l]
        expect(sys.platform != "linux" or (held and "PID 4242 2300 MB java ... GradleDaemon" in held[0]),
               f"a refused Gate C's RESIDUE.md names what holds the memory (PID, RSS, command): {held}")
    # ---- --gates auto: full until GATES.md records a green Gate C
    with tempfile.TemporaryDirectory() as d:
        td = pathlib.Path(d)
        expect(gates_auto(td) == "full", "no GATES.md: full")
        (td / "GATES.md").write_text("| Gate B -- server | PASS | x |\n", encoding="utf-8")
        expect(gates_auto(td) == "full", "an A/B-only record: still full")
        (td / "GATES.md").write_text("| Gate B -- server | PASS | x |\n| Gate C -- real client, `launch` | PASS | ok |\n", encoding="utf-8")
        expect(gates_auto(td) == "ab", "a green Gate C recorded: ab")
        (td / "GATES.md").write_text("| Gate C -- real client, `launch` | PASS | ok |\n| Gate C -- real client, `spawn` | FAIL | x |\n", encoding="utf-8")
        expect(gates_auto(td) == "full", "a red row: full again")
    # ---- an overlay identical to a shared helper is dropped (it would shadow later fixes); a changed one stays
    with tempfile.TemporaryDirectory() as d:
        mv = pathlib.Path(d); (mv / "targets/26.1.2/java/p/mv").mkdir(parents=True); (mv / "shared/26.1.2/java/p/mv").mkdir(parents=True)
        for n, body in (("Same.java", "class Same {}\n"), ("Mine.java", "class Mine {}\n")):
            (mv / "shared/26.1.2/java/p/mv" / n).write_text(body, encoding="utf-8")
        (mv / "targets/26.1.2/java/p/mv/Same.java").write_text("class Same {}\n", encoding="utf-8")
        (mv / "targets/26.1.2/java/p/mv/Mine.java").write_text("class Mine { int x; }\n", encoding="utf-8")
        expect(drop_shadowing_overlays(mv, "26.1.2") == ["p/mv/Same.java"] and (mv / "targets/26.1.2/java/p/mv/Mine.java").is_file(),
               "a byte-identical overlay of a shared helper is dropped, a changed one kept")
    # ---- a client lookup widened to a common type in a common class is found before Gate B; client code is not
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d); pr = repo / "build/mc1.21.8/mv/java/m"; (pr / "client").mkdir(parents=True)
        (pr / "Item.java").write_text("class Item {\n void t() {\n  Player p = Minecraft.getInstance().player;\n }\n}\n", encoding="utf-8")
        (pr / "client/Hud.java").write_text("class Hud { void t() { Player p = Minecraft.getInstance().player; } }\n", encoding="utf-8")
        (pr / "Ok.java").write_text("class Ok { void t() { Player p = p.mv.ClientRefs.player(); } }\n", encoding="utf-8")
        src = repo / "src/main/java/m"; src.mkdir(parents=True)
        (src / "Screen.java").write_text("@OnlyIn(Dist.CLIENT)\npublic class Screen {}\n", encoding="utf-8")
        (pr / "Screen.java").write_text("public class Screen { void t() { Level l = Minecraft.getInstance().level; } }\n", encoding="utf-8")
        (pr / "Ev.java").write_text("@EventBusSubscriber(modid = \"m\", value = Dist.CLIENT)\nclass Ev { void t() { Player p = Minecraft.getInstance().player; } }\n", encoding="utf-8")
        (pr / "GrMixin.java").write_text("import net.minecraft.client.renderer.GameRenderer;\n@Mixin(GameRenderer.class)\nclass GrMixin { void t() { Player p = Minecraft.getInstance().player; } }\n", encoding="utf-8")
        (pr / "EnMixin.java").write_text("import net.minecraft.world.entity.Entity;\n@Mixin(Entity.class)\nclass EnMixin { void t() { Player p = Minecraft.getInstance().player; } }\n", encoding="utf-8")
        # an example in a javadoc or a comment is not code (issue #133 gap 8: the shared ClientRefs helper's javadoc)
        (pr / "Doc.java").write_text("/**\n * Player p = Minecraft.getInstance().player;\n */\nclass Doc {\n"
                                     " // Level l = Minecraft.getInstance().level;\n String s = \"/* not a comment\";\n"
                                     " void t() { Player q = Minecraft.getInstance().player; }\n}\n", encoding="utf-8")
        hits = client_refs_in_common(repo, repo / "build/mc1.21.8/mv/java")
        expect(hits == ["m/Doc.java:7", "m/EnMixin.java:3", "m/Item.java:3"],
               f"client lookups in common classes (comments and javadoc ignored, a string with /* kept as code): {hits}")
    # ---- a review carries one version on when the file and every signal are the same; a changed count does not
    with tempfile.TemporaryDirectory() as d:
        mv = pathlib.Path(d); a, b = mv / "1.21.5", mv / "1.21.8"; a.mkdir(); b.mkdir()
        (a / "STUBS.json").write_text(json.dumps([{"file": ".multiversion/targets/1.21.5/java/X.java", "why": "removes 3 call(s)",
                                                   "reviewed": "same message API"}]), encoding="utf-8")
        got = record_stubs(b, [{"file": ".multiversion/targets/1.21.8/java/X.java", "why": "removes 3 call(s)"},
                               {"file": ".multiversion/targets/1.21.8/java/Y.java", "why": "removes 3 call(s)"}], parents=[a])
        expect(got[0]["reviewed"] == "(inherited from 1.21.5) same message API" and got[1]["reviewed"] is None,
               f"an identical signal inherits the parent's review, another file does not: {got}")
        got = record_stubs(mv / "1.21.10", [{"file": ".multiversion/targets/1.21.10/java/X.java", "why": "removes 4 call(s)"}],
                           parents=[b, a])
        expect(got[0]["reviewed"] is None, "control: a different count is a new question")
        expect(all("hooks" not in e for e in json.loads((b / "STUBS.json").read_text(encoding="utf-8"))),
               "without a repo nothing about hooks is read or written: STUBS.json keeps its earlier shape")
    # ---- an inherited review is re-opened when what the file hooks changed (issue #111); the same hooks keep it
    expect(hook_targets('@Mixin(LevelRenderer.class) class M { // @Inject(method = "gone", at = @At("HEAD"))\n'
                        '@Inject(method = "renderLevel", at = @At("HEAD")) void a(CallbackInfo ci) { x(); } }')
           == hook_targets('@Mixin( LevelRenderer.class )\nclass M {\n  @Inject(method="renderLevel",\n   at=@At(value = "HEAD"))\n'
                           '  private void a(CallbackInfo c) { y(); z(); } }')
           == ["@Inject method=renderLevel @At(value=\"HEAD\")", "@Mixin LevelRenderer"],
           f"hooks ignore comments, layout, bodies and @At's value= shorthand: {hook_targets('@Mixin(A.class) class M {}')}")
    expect(hook_targets('@Redirect(method = {"b", "a"}, at = @At(value = "INVOKE", target = "Lx;y()V", ordinal = 1)) void r() {}\n'
                        '@SubscribeEvent public static void on(final RenderLevelStageEvent e) {}\n@Overwrite public void tick() {}')
           == ["@Overwrite tick", "@Redirect method=a,b @At(value=\"INVOKE\",target=\"Lx;y()V\",ordinal=1)",
               "@SubscribeEvent RenderLevelStageEvent"], "redirect targets, event handlers and overwrites are hooks")
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d); mv = repo / ".multiversion/targets"
        mix = lambda at, meth="renderLevel", body="a();": ("@Mixin(LevelRenderer.class)\nclass LrMixin {\n"
                                                         f'  @Inject(method = "{meth}", at = @At("{at}"))\n'
                                                         f"  void h(CallbackInfo ci) {{ {body} }}\n}}\n")
        sig = "removes 2 mixin injection handler(s): check each still applies somewhere, with the same effect"
        def target(t, text, inh=()):
            f = mv / t / "java/m/LrMixin.java"; f.parent.mkdir(parents=True, exist_ok=True)
            if text is None:
                f.unlink(missing_ok=True)
            else:
                f.write_text(text, encoding="utf-8")
            return record_stubs(mv / t, [{"file": f".multiversion/targets/{t}/java/m/LrMixin.java", "why": sig}],
                                parents=[mv / x for x in inh], repo=repo)
        got = target("1.21.3", mix("HEAD"))
        expect(got[0]["reviewed"] is None and got[0]["hooks"] == ["@Inject method=renderLevel @At(value=\"HEAD\")",
                                                                 "@Mixin LevelRenderer"], f"a new entry records its hooks: {got}")
        e = json.loads((mv / "1.21.3/STUBS.json").read_text(encoding="utf-8")); e[0]["reviewed"] = "moved, same effect"
        (mv / "1.21.3/STUBS.json").write_text(json.dumps(e), encoding="utf-8")
        got = target("1.21.4", mix("HEAD", body="b(); c();"), ["1.21.3"])
        expect(got[0]["reviewed"] == "(inherited from 1.21.3) moved, same effect" and not got[0].get("recheck")
               and unreviewed_stubs(mv / "1.21.4") == [], f"same signal, same hooks (body renamed): inherited: {got}")
        got = target("1.21.5", mix("TAIL"), ["1.21.4", "1.21.3"])
        expect(got[0]["reviewed"] is None and got[0]["inherited_review"] == "(inherited from 1.21.3) moved, same effect"
               and got[0]["recheck"].startswith("re-justify: hook targets changed from @Inject method=renderLevel "
                                                "@At(value=\"HEAD\")") and "@At(value=\"TAIL\")" in got[0]["recheck"]
               and [x["file"] for x in unreviewed_stubs(mv / "1.21.5")] == [got[0]["file"]]
               and "re-justify" in stub_line(got[0]), f"same signal, changed @At: flagged and unreviewed: {got}")
        got2 = target("1.21.5", mix("TAIL"), ["1.21.4", "1.21.3"])
        expect(got2 == got, "a resumed run keeps the flag rather than re-inheriting")
        got = target("1.21.6", mix("HEAD", meth="renderSky"), ["1.21.4", "1.21.3"])
        expect(got[0]["reviewed"] is None and "renderSky" in got[0]["recheck"], f"same signal, changed method: flagged: {got}")
        # a STUBS.json from before "hooks": the parent's file on disk is what its review was made against
        (mv / "1.21.7").mkdir(); (mv / "1.21.7/java/m").mkdir(parents=True)
        (mv / "1.21.7/java/m/LrMixin.java").write_text(mix("HEAD"), encoding="utf-8")
        (mv / "1.21.7/STUBS.json").write_text(json.dumps([{"file": ".multiversion/targets/1.21.7/java/m/LrMixin.java",
                                                            "why": sig, "reviewed": "moved, same effect"}]), encoding="utf-8")
        expect(target("1.21.8", mix("HEAD"), ["1.21.7"])[0]["reviewed"] == "(inherited from 1.21.7) moved, same effect",
               "an old entry without hooks, the same hooks on disk: inherited")
        expect(target("1.21.9", mix("RETURN"), ["1.21.7"])[0]["recheck"].startswith("re-justify"),
               "an old entry without hooks, changed hooks on disk: flagged, never silently passed")
        (mv / "1.21.7/java/m/LrMixin.java").unlink()
        expect(target("1.21.10", mix("RETURN"), ["1.21.7"])[0]["reviewed"] == "(inherited from 1.21.7) moved, same effect",
               "an old entry whose file is gone too: hooks unknown, inherited as before")
        expect(target("1.21.11", None, ["1.21.3"])[0]["recheck"].endswith("to (none)"),
               "the file gone on the inheriting target: it hooks nothing, flagged")
    # ---- the shared libraries' access-transformer lines reach a direct jump's AT, once, comment aside
    with tempfile.TemporaryDirectory() as d:
        mv = pathlib.Path(d)
        (mv / "targets/1.21.4").mkdir(parents=True)
        (mv / "targets/1.21.4/target.properties").write_text("shared_chain=1.21.3,1.21.4\n", encoding="utf-8")
        (mv / "shared/1.21.3").mkdir(parents=True)
        (mv / "shared/1.21.3/accesstransformer.cfg").write_text("# why\npublic a.B <init>(I)V # helper\n", encoding="utf-8")
        at = mv / "targets/1.21.4/resources/META-INF/accesstransformer.cfg"
        at.parent.mkdir(parents=True); at.write_text("public c.D e()V\n", encoding="utf-8")
        expect(merge_shared_at(mv, "1.21.4", at) == ["public a.B <init>(I)V # helper"] and merge_shared_at(mv, "1.21.4", at) == []
               and at.read_text(encoding="utf-8") == "public c.D e()V\npublic a.B <init>(I)V # helper\n",
               "a direct jump's AT gets its shared libraries' lines, once")
        (mv / "targets/1.21.4/target.properties").write_text("x=1\n", encoding="utf-8")
        at.write_text("public c.D e()V\n", encoding="utf-8")
        expect(merge_shared_at(mv, "1.21.4", at) == [], "control: a target whose chain lacks that version gets nothing")
    # ---- setup: pinned dependencies are resolved per target; a required one with no build stops before add-target
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d); (repo / ".multiversion/tools").mkdir(parents=True)
        (repo / ".multiversion/base.properties").write_text("minecraft_version=1.21.1\n", encoding="utf-8")
        (repo / "build.gradle").write_text('dependencies {\n    implementation "curse.maven:somelib-1:2"\n}\n', encoding="utf-8")
        (repo / ".multiversion/INVENTORY.json").write_text('{"items": []}\n', encoding="utf-8")   # built: no build call
        a_ = argparse.Namespace(repo=str(repo), target="1.21.4", json=None, budget=1.0, gates="ab", modid="m", base="x",
                                direct=False, from_stage=None, max_errors=None, confirm_spend=False,
                                port_checkout=["owner/lib=/x/lib"], port_release=["owner/rel@v1"])
        run = Run(a_); ran = []
        def sh15(cmd, log=None, cwd=None):
            ran.append(" ".join(map(str, cmd)))
            return CompletedProcess(cmd, 15 if "layer-deps.py" in ran[-1] else 0, "layer-deps: STOP required: curse.maven:somelib-1\n", "")
        run.sh = sh15
        res = run.s_setup()
        expect(res.get("code") == 15 and "somelib" in res["stop"] and len(ran) == 1 and "--dry-run" in ran[0]
               and "--port-checkout owner/lib=/x/lib" in ran[0] and "--port-release owner/rel@v1" in ran[0],
               f"a required dependency with no build stops setup first: {res} {ran}")
        expect("--port-checkout owner/lib=/x/lib" in run.resume_cmd("setup") and "--port-release owner/rel@v1" in run.resume_cmd("setup"),
               "the resume command keeps --port-checkout and --port-release")
        expect(pins_dependencies(repo), "a cursemaven pin is a pinned dependency")
        (repo / "build.gradle").write_text('dependencies {\n    implementation "net.example:lib:${lib_version}"\n}\n', encoding="utf-8")
        expect(not pins_dependencies(repo), "control: a build with no pinned mod resolves nothing (setup runs as before)")
    # the guard stops the loop stage before any worker runs
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d); (repo / ".multiversion/targets/1.21.4").mkdir(parents=True)
        (repo / ".multiversion/base.properties").write_text("minecraft_version=1.21.1\n", encoding="utf-8")
        a_ = argparse.Namespace(repo=str(repo), target="1.21.4", json=None, budget=1.0, gates="ab", modid="m", base="x",
                                direct=False, from_stage=None, max_errors=None, confirm_spend=False)
        run = Run(a_); ran = []
        run.s_measure = lambda: {"done": "x", "errors": 5000, "this_run": True}
        run.summary["stages"]["measure"] = {"errors": 5000, "this_run": True}
        run.sh = lambda *x, **k: ran.append(x)
        res = run.s_loop()
        expect(res.get("code") == 10 and "spend guard" in res["stop"] and not ran, "spend guard stops before file-loop runs")
        a_.confirm_spend = True
        run.sh = lambda *x, **k: (ran.append(x), CompletedProcess(x, 1, "", ""))[1]
        run.s_loop()
        expect(ran, "--confirm-spend lets the loop start")
    # ---- one run per checkout: a live holder refuses a second run (exit 14, naming it); a dead one is taken over
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d)
        holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            (repo / "build").mkdir()
            (repo / "build/.fork-port.lock").write_text(json.dumps({"pid": holder.pid, "tool": "fork-port.py", "target": "1.21.3",
                                                                    "started": "2026-01-01T00:00:00Z"}), encoding="utf-8")
            buf = io.StringIO()
            real_argv = sys.argv
            sys.argv = ["fork-port.py", "--repo", str(repo), "--target", "1.21.4"]
            try:
                with contextlib.redirect_stdout(buf):
                    rc = main()
            finally:
                sys.argv = real_argv
            expect(rc == 14 and str(holder.pid) in buf.getvalue() and "one gate run per checkout" in buf.getvalue(),
                   f"a second run is refused with exit 14 naming the holder: rc={rc} {buf.getvalue()[:200]}")
            expect(json.loads((repo / "build/.fork-port.lock").read_text(encoding="utf-8"))["pid"] == holder.pid,
                   "the refused run leaves the holder's lock alone")
        finally:
            holder.kill(); holder.wait()
        buf = io.StringIO()
        sys.argv = ["fork-port.py", "--repo", str(repo), "--target", "1.21.4"]
        try:
            with contextlib.redirect_stdout(buf):
                rc = main()          # the holder is dead now: stale, taken over; it then stops at setup (no .multiversion) with 2
        finally:
            sys.argv = real_argv
        expect(rc == 2 and not (repo / "build/.fork-port.lock").exists(),
               f"a dead holder's lock is taken over and released at the end: rc={rc}")
    # ---- the children see the lock holder: on a layer fork they run with run.env (GRADLE_OPTS without a daemon),
    # copied before the lock was taken; a single-version checkout's children inherit os.environ (run.env is None)
    lc = load("layer_ci", "layer-ci.py")
    for layer in (True, False):
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d)
            if layer:
                (repo / ".multiversion").mkdir()
                (repo / ".multiversion/base.properties").write_text("mc=1.21.1\n", encoding="utf-8")
            seen, real_drive, real_argv = {}, globals()["drive"], sys.argv
            def fake_drive(run, *_):
                seen["env"] = run.env
                seen["holder"] = (run.env if run.env is not None else os.environ).get(lc.LOCK_ENV)
                return 0
            globals()["drive"] = fake_drive
            sys.argv = ["fork-port.py", "--repo", str(repo), "--target", "1.21.4", "--gates", "none"]
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    main()
            finally:
                globals()["drive"], sys.argv = real_drive, real_argv
            expect(seen.get("holder") == str(os.getpid()) and (seen.get("env") is not None) == layer,
                   f"{'layer' if layer else 'single-version'}: a child sees this run as the lock holder: {seen.get('holder')}")
    # ---- --direct and shared_chain: layer.py's rule and the tool's mirror of it agree
    lay = load("layer_tpl", "../templates/fork-layer/tools/layer.py")
    with tempfile.TemporaryDirectory() as d:
        mv = pathlib.Path(d)
        for t_, props in (("1.21.3", "x=1\n"), ("1.21.5", "inherits=1.21.3\n"),
                          ("1.21.11", "shared_chain=1.21.4, 1.21.5,1.21.8,1.21.10\n"), ("1.21.12", "inherits=1.21.11\n")):
            (mv / "targets" / t_).mkdir(parents=True)
            (mv / "targets" / t_ / "target.properties").write_text(props, encoding="utf-8")
        for t_ in ("1.21.3", "1.21.5", "1.21.11", "1.21.12"):
            expect(shared_chain(mv, t_) == lay.shared_chain(t_, mv), f"fork-port and layer.py agree on shared_chain({t_})")
        expect(shared_chain(mv, "1.21.5") == chain(mv, "1.21.5") == ["1.21.5", "1.21.3"], "no key: the inherits chain, as before")
        expect(shared_chain(mv, "1.21.11") == ["1.21.11", "1.21.10", "1.21.8", "1.21.5", "1.21.4"], "direct: newest first")
        expect(shared_chain(mv, "1.21.12")[:2] == ["1.21.12", "1.21.11"] and "1.21.8" in shared_chain(mv, "1.21.12"),
               "a target inheriting a direct one sees what its parent saw")
    # layer.py replacement: only a verbatim older copy; a fork that does not jump is never touched
    older = tpl.replace(INSERT, "")
    expect(layer_patch(tpl, tpl, [], True)[0] == "current", "template copy is current")
    withmoves_nosc = tpl.replace("shared_chain", "shared_chaiN")
    expect(layer_patch(withmoves_nosc, tpl, [withmoves_nosc], False)[0] == "current", "moves-aware fork that needs no jump is untouched")
    expect(layer_patch(withmoves_nosc, tpl, [withmoves_nosc], True)[0] == "replace", "a verbatim older copy is replaced when a jump needs shared_chain")
    expect(layer_patch(withmoves_nosc + "# hand edit\n", tpl, [withmoves_nosc], True)[0] == "diverged", "an edited copy is never replaced")
    expect(layer_patch(older, tpl, [])[0] == "replace", "the pre-moves copy is still replaced")
    hist = layer_history()
    if len(hist) >= 2:
        expect(layer_patch(hist[-1], tpl, hist, True)[0] in ("replace", "current"), "the oldest committed template copy is replaceable")
    ok = self_check_done_freeze_sweep() and ok
    ok = self_check_review131() and ok
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def self_check_done_freeze_sweep():
    """R1 (the done record, the undone-parent refusal), R4 (the upstream freeze) and R5 (the sweep), each on a layer
    fixture AND a single-version one (no .multiversion/: nothing of this runs, the output and exit are as before)."""
    import contextlib, io
    from subprocess import CompletedProcess
    ok = True

    def expect(c, msg):
        nonlocal ok
        if not c:
            ok = False; print("FAIL:", msg)

    def git_repo(d):
        pathlib.Path(d).mkdir(parents=True, exist_ok=True)
        g = lambda *x: subprocess.run(["git", "-C", str(d), *x], capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()
        g("init", "-q", "-b", "main"); g("config", "user.email", "a@b"); g("config", "user.name", "a")
        return g

    def w(p, text):
        p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text, encoding="utf-8")

    green_c = "Gates for `m` at `{at}` for Minecraft {t} (local environment):\n\n| Gate C -- real client, `launch` | PASS | ok |\n"
    # ---- R1: REVIEW.md -- one verdict per STUBS.json entry and per whole-file overlay, for the commit reviewed
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d); g = git_repo(repo)
        td = repo / ".multiversion/targets/1.21.4"
        w(repo / ".multiversion/base.properties", "minecraft_version=1.21.1\n")
        w(repo / "src/main/java/com/example/mod/A.java", "class A { void f() { x(); } }\n")
        w(repo / "src/main/java/com/example/mod/B.java", "class B {}\n")
        w(td / "java/com/example/mod/A.java", "class A { void f() { y(); } }\n")             # replaces an authors' file
        w(td / "java/com/example/mod/mv/Helper.java", "class Helper {}\n")                    # overlay-only: not reviewed
        w(td / "STUBS.json", json.dumps([{"file": ".multiversion/targets/1.21.4/java/com/example/mod/A.java",
                                          "why": "removes 1 call(s)", "reviewed": "same effect"}]))
        w(td / "target.properties", "x=1\n"); w(td / "GATES.md", green_c.format(t="1.21.4", at="x"))
        li = load("layer_inventory", "layer-inventory.py")       # R2: the base inventory (nothing hooked here) and a
        with contextlib.redirect_stdout(io.StringIO()):         # prepared tree, so the done list's item (6) holds
            li.cmd_build(argparse.Namespace(repo=str(repo), dry_run=False, out=None))
        (repo / "build/mc1.21.4/mv/java").mkdir(parents=True)
        items = review_items(repo, td)
        expect(items == [("overlay", ".multiversion/targets/1.21.4/java/com/example/mod/A.java"),
                         ("stub", ".multiversion/targets/1.21.4/java/com/example/mod/A.java")],
               f"review items: every stub entry and every whole-file overlay, no overlay-only helper: {items}")
        expect(review_problems(repo, td) == ["no REVIEW.md: 2 item(s) need an independent reviewer's verdict (1 stub(s), "
                                             "whole-file overlays (1) + overlay-only files (1) (those need no row), "
                                             "0 resource group(s))"], f"no REVIEW.md: not done: {review_problems(repo, td)}")
        expect(review_skeleton(repo, td, "1.21.4") == 2 and review_skeleton(repo, td, "1.21.4") == 0,
               "the skeleton gets a ? row per item, once")
        pr = review_problems(repo, td)
        expect(sum(p.startswith("no verdict") for p in pr) == 2 and any("no reviewer" in p for p in pr)
               and any("no commit" in p for p in pr), f"a skeleton is not a review: {pr}")
        g("add", "-A"); g("commit", "-qm", "port 1.21.4"); head = g("rev-parse", "--short", "HEAD")
        w(td / "GATES.md", green_c.format(t="1.21.4", at=head))      # the gates ran at the committed port
        txt = (td / "REVIEW.md").read_text(encoding="utf-8")
        txt = (txt.replace("- reviewer: ?", "- reviewer: fresh agent (session x)").replace("- date: ?", "- date: 2026-10-16")
               .replace("- commit: ?", f"- commit: {head}")
               .replace("| overlay | .multiversion/targets/1.21.4/java/com/example/mod/A.java | ? | ? |",
                        "| overlay | .multiversion/targets/1.21.4/java/com/example/mod/A.java | ok | y() is x()'s 1.21.4 name \\| same |")
               .replace("| stub | .multiversion/targets/1.21.4/java/com/example/mod/A.java | ? | ? |",
                        "| stub | .multiversion/targets/1.21.4/java/com/example/mod/A.java | ok | the call moved, same effect |"))
        (td / "REVIEW.md").write_text(txt, encoding="utf-8")
        expect(review_problems(repo, td) == [], f"a complete review for HEAD: {review_problems(repo, td)}")
        expect(parse_review(txt)[1][("overlay", ".multiversion/targets/1.21.4/java/com/example/mod/A.java")][1]
               == "y() is x()'s 1.21.4 name | same", "an escaped bar stays in the note")
        expect(target_done(repo, "1.21.4") == [f"manual-test items not generated (TEST-ITEMS.md): write them, no gate run: "
                                               f"python3 tools/fork-port.py --repo {repo} --target 1.21.4 --write-test-items"],
               f"done also needs the test items, naming the command that writes them: {target_done(repo, '1.21.4')}")
        # --write-test-items: TEST-ITEMS.md only, under the checkout lock; refused (14) while another run holds it
        lc_ = load("layer_ci", "layer-ci.py")
        held = lc_.CheckoutLock(repo, "fork-port.py", "1.21.5", pid=os.getppid(), env={})    # another live run
        expect(held.acquire() is None, "the fixture takes the checkout lock")
        argv_ = sys.argv
        try:
            sys.argv = ["fork-port.py", "--repo", str(repo), "--target", "1.21.4", "--write-test-items"]
            with contextlib.redirect_stdout(io.StringIO()) as buf_:
                rc_busy = main()
            held.release()
            expect(rc_busy == 14 and not (td / "TEST-ITEMS.md").exists(), f"--write-test-items waits for the checkout: "
                                                                          f"{rc_busy} {buf_.getvalue()}")
            before_ = sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*") if ".git" not in p.parts)
            with contextlib.redirect_stdout(io.StringIO()) as buf_:
                rc_w = main()
            after_ = sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*") if ".git" not in p.parts)
        finally:
            sys.argv = argv_
        expect(rc_w == 0 and set(after_) - set(before_) == {".multiversion/targets/1.21.4/TEST-ITEMS.md"}
               and "A.java" in (td / "TEST-ITEMS.md").read_text(encoding="utf-8") and not (repo / "build/.fork-port.lock").exists(),
               f"--write-test-items writes TEST-ITEMS.md from layer-test-plan.py and nothing else: {rc_w} "
               f"{set(after_) - set(before_)} {buf_.getvalue()}")
        n_, why_ = write_test_items(repo, td, "1.21.4")
        expect(why_ is None and n_ >= 1, f"test items generated from layer-test-plan.py: {n_} {why_}")
        expect(target_done(repo, "1.21.4") == [], f"done: {target_done(repo, '1.21.4')}")
        # BEHAVIOUR.json is the reviewer's own record beside REVIEW.md: writing it after the review is not a change
        # the review missed (it is still what the inventory account reads)
        w(td / "BEHAVIOUR.json", json.dumps([{"item": "main:com/example/mod/B.java#*", "status": "dropped", "reason": "x"}]))
        expect(target_done(repo, "1.21.4") == [] and "BEHAVIOUR.json" in REVIEW_OUTPUTS,
               f"a BEHAVIOUR.json written after the review leaves it current: {target_done(repo, '1.21.4')}")
        (td / "BEHAVIOUR.json").unlink()
        # resource overlays and renames.tsv are review items: one row per resource group and one for renames.tsv, each
        # with its digest -- an edit (a spawn-egg tint, a missing tag added) is a new row, never covered by the old verdict
        w(td / "resources/assets/examplemod/items/egg.json", '{"model": {"type": "minecraft:model"}}\n')
        w(td / "resources/assets/examplemod/items/sub/b.json", '{"model": {}}\n')
        w(td / "resources/examplemod.mixins.json", '{}\n')
        w(td / "renames.tsv", "x\ty\n")
        g("add", "-A"); g("commit", "-qm", "resources"); head2 = g("rev-parse", "--short", "HEAD")
        w(td / "GATES.md", green_c.format(t="1.21.4", at=head2))
        os.utime(repo / "build/mc1.21.4/mv/java")     # prepared after renames.tsv: the inventory reads a current tree
        ri = [x for x in review_items(repo, td) if x[0] in ("resources", "renames")]
        expect([(k, x.rsplit(" @", 1)[0]) for k, x in ri] == [
            ("renames", ".multiversion/targets/1.21.4/renames.tsv"),
            ("resources", ".multiversion/targets/1.21.4/resources/assets/examplemod/items"),
            ("resources", ".multiversion/targets/1.21.4/resources/examplemod.mixins.json")]
               and all(re.fullmatch(r"[0-9a-f]{12}", x.rsplit(" @", 1)[1]) for _k, x in ri),
               f"one row per resource group (sub-folders in it) and one for renames.tsv, each with a digest: {ri}")
        txt2 = txt.replace(f"- commit: {head}", f"- commit: {head2}")
        (td / "REVIEW.md").write_text(txt2, encoding="utf-8")
        pr = target_done(repo, "1.21.4")
        expect(sorted(p.split(" @")[0] for p in pr) == [
            "no verdict: renames .multiversion/targets/1.21.4/renames.tsv",
            "no verdict: resources .multiversion/targets/1.21.4/resources/assets/examplemod/items",
            "no verdict: resources .multiversion/targets/1.21.4/resources/examplemod.mixins.json"],
               f"an unreviewed resource overlay or renames.tsv: not done: {pr}")
        expect(review_skeleton(repo, td, "1.21.4") == 3, "the skeleton gives each its `?` row")
        txt2 = txt2 + "".join(f"| {k} | {x} | ok | compared with the authors' 1.21.1 files |\n" for k, x in ri)
        (td / "REVIEW.md").write_text(txt2, encoding="utf-8")
        expect(target_done(repo, "1.21.4") == [], f"every group and renames.tsv reviewed: done: {target_done(repo, '1.21.4')}")
        w(td / "resources/assets/examplemod/items/egg.json", '{"model": {"type": "minecraft:model", "tints": [1]}}\n')
        g("add", "-A"); g("commit", "-qm", "tint"); head3 = g("rev-parse", "--short", "HEAD")
        w(td / "GATES.md", green_c.format(t="1.21.4", at=head3))
        (td / "REVIEW.md").write_text(txt2.replace(f"- commit: {head2}", f"- commit: {head3}"), encoding="utf-8")
        pr = target_done(repo, "1.21.4")
        expect(len(pr) == 1 and pr[0].startswith("no verdict: resources .multiversion/targets/1.21.4/resources/assets/examplemod/items @")
               and pr[0] not in [f"no verdict: {k} {x}" for k, x in ri],
               f"a group edited after its verdict (the commit moved on): its new digest has no verdict: {pr}")
        (td / "REVIEW.md").write_text(txt, encoding="utf-8")
        g("rm", "-rq", ".multiversion/targets/1.21.4/resources", ".multiversion/targets/1.21.4/renames.tsv")
        g("commit", "-qm", "drop the fixture's resources"); head4 = g("rev-parse", "--short", "HEAD")
        txt = txt.replace(f"- commit: {head}", f"- commit: {head4}"); head = head4
        (td / "REVIEW.md").write_text(txt, encoding="utf-8"); w(td / "GATES.md", green_c.format(t="1.21.4", at=head))
        expect(target_done(repo, "1.21.4") == [], f"back to the plain fixture: done: {target_done(repo, '1.21.4')}")
        (td / "java/com/example/mod/A.java").write_text("class A { void f() { z(); } }\n", encoding="utf-8")
        expect(any("changed since the reviewed commit" in p for p in target_done(repo, "1.21.4")),
               "an overlay edited after the review: not done (re-review, name the new commit)")
        g("checkout", "-q", "--", "."); w(td / "GATES.md", green_c.format(t="1.21.4", at=head))
        os.utime(repo / "build/mc1.21.4/mv/java")      # the overlay is as built again: the prepared tree is current
        (td / "REVIEW.md").write_text(txt.replace("| ok | the call moved", "| not-ok | the call moved"), encoding="utf-8")
        expect(any(p.startswith("reviewer says not-ok") for p in target_done(repo, "1.21.4")), "a not-ok verdict: not done")
        (td / "REVIEW.md").write_text(txt.replace(f"- commit: {head}", "- commit: 0123456"), encoding="utf-8")
        expect(any("not in this checkout" in p for p in target_done(repo, "1.21.4")), "an unknown reviewed commit: not done")
        (td / "REVIEW.md").write_text(txt, encoding="utf-8")
        # ---- the next target is refused while 1.21.4 is not done; --allow-undone-parent is recorded and keeps it undone
        (td / "TEST-ITEMS.md").unlink()
        stop_ = undone_parent_stop(repo, "1.21.5")
        expect(stop_ and stop_[0] == "1.21.4" and "TEST-ITEMS" in stop_[1][0], f"1.21.5 stands on an undone 1.21.4: {stop_}")
        a_ = argparse.Namespace(repo=str(repo), target="1.21.5", json=None, budget=1.0, gates="ab", modid="m", base="x",
                                direct=False, from_stage=None, to_stage=None, max_errors=None, confirm_spend=False,
                                port_checkout=[], port_release=[], allow_undone_parent=False)
        run = Run(a_); ran = []
        run.sh = lambda cmd, log=None, cwd=None: (ran.append(" ".join(map(str, cmd))), CompletedProcess(cmd, 0, "", ""))[1]
        res = run.s_setup()
        expect(res.get("code") == UNDONE_EXIT and "1.21.4" in res["stop"] and not ran and not run.tdir.exists()
               and any("--allow-undone-parent" in c for c in res["choices"]),
               f"setup refuses a new target over an undone parent before anything runs: {res} {ran}")
        a_.allow_undone_parent = True
        try:
            run.s_setup()          # past add-target the fixture has no layer machinery: only the add-target call matters
        except OSError:
            pass
        expect(any("--allow-undone-parent" in c for c in ran if "add-target" in c), f"the escape reaches add-target: {ran}")
        write_test_items(repo, td, "1.21.4")
        expect(undone_parent_stop(repo, "1.21.5") is None and undone_parent_stop(repo, "1.21.4") is None,
               "a done parent (and an existing target) is never refused")
        w(repo / ".multiversion/targets/1.21.5/target.properties", "inherits=1.21.4\nallowed_undone_parent=1.21.4 2026-10-16: x\n")
        w(repo / ".multiversion/targets/1.21.5/GATES.md", green_c.format(t="1.21.5", at=head))
        (td / "TEST-ITEMS.md").unlink()
        expect(any("parent 1.21.4 is still not done" in p for p in target_done(repo, "1.21.5")),
               "a target added over an undone parent is not done while that parent is not")
    # ---- R4: the authors' commit the buildout started from; a moved upstream stops a run to the end (exit 19)
    with tempfile.TemporaryDirectory() as d:
        up, fork = pathlib.Path(d) / "authors", pathlib.Path(d) / "fork"
        gu = git_repo(up); w(up / "A.java", "a\n"); gu("add", "-A"); gu("commit", "-qm", "authors 1")
        subprocess.run(["git", "clone", "-q", "-o", "upstream", str(up), str(fork)], check=True, capture_output=True)
        gf = lambda *x: subprocess.run(["git", "-C", str(fork), *x], capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()
        gf("config", "user.email", "a@b"); gf("config", "user.name", "a")
        w(fork / ".multiversion/base.properties", "minecraft_version=1.21.1\nmv_package=com.example.mod.mv\n")
        gf("add", "-A"); gf("commit", "-qm", "layer")
        a_ = argparse.Namespace(repo=str(fork), target="1.21.3", base="origin/neoforge-1.21.1", port_release=["owner/lib@v1"],
                                upstream=None, accept_upstream_drift=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            st = drift_stop(fork, a_)
        bp = read_props(fork / ".multiversion/base.properties")
        expect(st is None and bp.get("upstream") == "upstream/main" and bp.get("upstream_commit") == gu("rev-parse", "HEAD")
               and bp.get("library.owner/lib") == "v1" and bp.get("mv_package") == "com.example.mod.mv",
               f"the first target freezes the authors' commit and each library release, other keys kept: {bp} {buf.getvalue()}")
        gf("commit", "-qam", "freeze"); (fork / ".multiversion/targets/1.21.3").mkdir(parents=True)
        w(up / "A.java", "a2\n"); gu("commit", "-qam", "authors 2")
        with contextlib.redirect_stdout(io.StringIO()):
            st = drift_stop(fork, a_)
        expect(st and st["code"] == DRIFT_EXIT and "1 commit(s)" in st["stop"]
               and any("port-sync.py" in c and "--upstream upstream/main" in c and "--port neoforge-1.21.1" in c for c in st["choices"]),
               f"the authors pushed: exit 19 naming the keep-current command: {st}")
        a_.accept_upstream_drift = True
        with contextlib.redirect_stdout(io.StringIO()):
            st = drift_stop(fork, a_)
        expect(st is None and "accepted" in (fork / ".multiversion/DRIFT.md").read_text(encoding="utf-8"),
               "--accept-upstream-drift continues, recorded in DRIFT.md")
        a_.accept_upstream_drift = False
        gf("merge", "-q", "--no-edit", "upstream/main")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            st = drift_stop(fork, a_)
        expect(st is None and "newer than the freeze" in buf.getvalue(), f"after keep-current merged it: no stop, a re-freeze hint: {buf.getvalue()}")
        a_.port_release = ["owner/lib@v2"]
        with contextlib.redirect_stdout(io.StringIO()):
            st = drift_stop(fork, a_)
        expect(st and st["code"] == DRIFT_EXIT and "owner/lib" in st["stop"], f"a library resolved against another release: {st}")
        os.environ[DRIFT_CHECKED_ENV] = "1"
        try:
            expect(drift_stop(fork, a_) is None, "a sweep's child does not check again")
        finally:
            del os.environ[DRIFT_CHECKED_ENV]
    # ---- R5: --chain FROM..TO re-runs the stage for each target oldest first, no model, stops at the first red
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d); mv = repo / ".multiversion"
        w(mv / "base.properties", "minecraft_version=1.21.1\n")
        for t_, inh in (("1.21.3", None), ("1.21.4", "1.21.3"), ("1.21.5", "1.21.4"), ("1.21.8", "1.21.5")):
            w(mv / "targets" / t_ / "target.properties", f"inherits={inh}\n" if inh else "x=1\n")
        expect(chain_targets(mv, "1.21.4", "1.21.8") == ["1.21.4", "1.21.5", "1.21.8"], "the range along the inherits chain")
        codes, seen = {"1.21.3": 0, "1.21.4": UNDONE_EXIT, "1.21.5": 12, "1.21.8": 0}, []

        def runner(cmd, env):
            t_ = cmd[cmd.index("--target") + 1]; seen.append((t_, cmd, env))
            summ = {"stop": {"stage": "gates", "left": "1 gate(s) red: Gate C"}} if codes[t_] == 12 else (
                {"not_done": ["no REVIEW.md"]} if codes[t_] == UNDONE_EXIT else {})
            w(repo / "build" / f"mc{t_}" / "fork-port" / "fork-port.json", json.dumps(summ))
            if codes[t_] == 12:
                w(mv / "targets" / t_ / "RESIDUE.md", "# Residue: gates\n")
            return codes[t_]
        sa = argparse.Namespace(repo=str(repo), chain="1.21.3..1.21.8", from_stage=None, to_stage=None, gates="full",
                                base="origin/neoforge-1.21.1", modid=None, port_checkout=[], port_release=[],
                                upstream=None, accept_upstream_drift=False, target=None)
        lines = []
        with contextlib.redirect_stdout(io.StringIO()):
            rc = sweep(sa, runner, out=lines.append)
        txt = "\n".join(lines)
        expect(rc == 12 and [s[0] for s in seen] == ["1.21.3", "1.21.4", "1.21.5"],
               f"oldest first, on past a green-but-unreviewed target, stopped at the first red: {rc} {[s[0] for s in seen]}")
        c0 = seen[0][1]
        expect(c0[c0.index("--from-stage") + 1] == "gates" and c0[c0.index("--budget") + 1] == "0"
               and c0[c0.index("--max-errors") + 1] == "0" and c0[c0.index("--gates") + 1] == "full"
               and seen[0][2].get(DRIFT_CHECKED_ENV) == "1", f"each target: --from-stage gates, no model: {c0}")
        expect("| 1.21.5 | 12 | RED at gates: 1 gate(s) red: Gate C | .multiversion/targets/1.21.5/RESIDUE.md |" in txt
               and "| 1.21.8 | - | not run" in txt and "--chain 1.21.5..1.21.8" in txt and "ONE fixer" in txt,
               f"the table names the red target's RESIDUE.md and the command to sweep on: {txt}")
        codes["1.21.5"] = 0; seen.clear(); lines.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            rc = sweep(sa, runner, out=lines.append)
        expect(rc == UNDONE_EXIT and len(seen) == 4, "all green, one unreviewed: exit 18")
        sa.chain = "1.21.9..1.21.8"
        expect(sweep(sa, runner, out=lines.append) == 2, "FROM outside TO's chain: bad input")
        expect(not (repo / "build/.fork-port.lock").exists(), "the sweep releases its checkout lock")
    # ---- wiring (1): done includes the base behaviour inventory (layer-inventory.py) -- a layer fixture and a
    # single-version one
    mixin_src = ("package com.example.mod.mixin;\n@Mixin(Foo.class)\npublic class FooMixin {\n"
                 "    @Inject(method = \"tick\", at = @At(\"HEAD\"))\n    private void onTick(CallbackInfo ci) {}\n}\n")
    for layer in (True, False):
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d); td = repo / ".multiversion/targets/1.21.4"
            w(repo / "src/main/java/com/example/mod/mixin/FooMixin.java", mixin_src)
            w(td / "target.properties", "x=1\n"); w(td / "GATES.md", green_c.format(t="1.21.4", at="x"))
            w(repo / "build/mc1.21.4/mv/java/com/example/mod/mixin/FooMixin.java", "package com.example.mod.mixin;\npublic class FooMixin {}\n")
            if layer:
                w(repo / ".multiversion/base.properties", "minecraft_version=1.21.1\n")
                inv_why = [x for x in target_done(repo, "1.21.4") if x.startswith("behaviour inventory")]
                expect(len(inv_why) == 1 and "layer-inventory.py build" in inv_why[0],
                       f"layer: no INVENTORY.json is not done, naming the build: {inv_why}")
                li = load("layer_inventory", "layer-inventory.py")
                with contextlib.redirect_stdout(io.StringIO()):
                    li.cmd_build(argparse.Namespace(repo=str(repo), dry_run=False, out=None))
            why = target_done(repo, "1.21.4")
            inv_why = [x for x in why if x.startswith("behaviour inventory")]
            if layer:
                expect(len(inv_why) == 2 and all("unaccounted" in x and "FooMixin" in x for x in inv_why)
                       and (td / "INVENTORY-CHECK.json").is_file(),
                       f"layer: a mixin and an injection the prepared tree lost are unaccounted: not done: {inv_why}")
                # the skeleton names each unaccounted item in an `item` row, a file's items together, so the reviewer
                # sees the hooks a file-level row does not account for
                review_skeleton(repo, td, "1.21.4")
                rows_ = [k for k in parse_review((td / "REVIEW.md").read_text(encoding="utf-8"))[1] if k[0] == "item"]
                expect(rows_ == [("item", "main:com/example/mod/mixin/FooMixin.java#injection:@Inject method=tick @At(value=\"HEAD\")"),
                                 ("item", "main:com/example/mod/mixin/FooMixin.java#mixin:@Mixin Foo")],
                       f"layer: review_skeleton lists the unaccounted item ids: {rows_}")
                (td / "REVIEW.md").unlink()
                w(td / "BEHAVIOUR.json", json.dumps([{"item": "main:com/example/mod/mixin/FooMixin.java#*",
                                                      "status": "dropped", "reason": "the hook is gone on 1.21.4"}]))
                expect(not [x for x in target_done(repo, "1.21.4") if x.startswith("behaviour inventory")],
                       "layer: each item recorded in BEHAVIOUR.json: the inventory is accounted for")
                expect("INVENTORY-CHECK.json" in REVIEW_OUTPUTS, "INVENTORY-CHECK.json is a run output, not a reviewed file")
            else:
                expect(not inv_why and not (td / "INVENTORY-CHECK.json").exists(),
                       f"single-version: no inventory requirement, nothing written: {why}")
                review_skeleton(repo, td, "1.21.4")
                expect(not [k for k in parse_review((td / "REVIEW.md").read_text(encoding="utf-8"))[1] if k[0] == "item"],
                       "single-version: no inventory, so the skeleton names no item")
    # ---- wiring (2): setup builds .multiversion/INVENTORY.json once when it is missing (layer fork only)
    for layer in (True, False):
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d); (repo / ".multiversion/targets/1.21.4").mkdir(parents=True)
            if layer:
                w(repo / ".multiversion/base.properties", "minecraft_version=1.21.1\n")
            a_ = argparse.Namespace(repo=str(repo), target="1.21.4", json=None, budget=1.0, gates="ab", modid="m", base="x",
                                    direct=False, from_stage=None, to_stage=None, max_errors=None, confirm_spend=False,
                                    port_checkout=[], port_release=[], allow_undone_parent=False, allow_undone_library=False)
            run = Run(a_); ran = []
            def ish(cmd, log=None, cwd=None, code=0):
                ran.append(" ".join(map(str, cmd)))
                return CompletedProcess(cmd, code, "layer-inventory: 3 item(s) from the base (mixin 1) -> x\n", "")
            run.sh = ish
            for _ in range(2):
                try:
                    res = run.s_setup()   # past sync-shared the fixture has no layer machinery: only the calls matter
                except OSError:
                    res = {}
                if layer:
                    (repo / ".multiversion/INVENTORY.json").write_text("{}\n", encoding="utf-8")   # what the build wrote
            builds = [c for c in ran if "layer-inventory.py build" in c]
            if layer:
                expect(len(builds) == 1 and ran[0] == builds[0], f"layer: the inventory is built once, first: {ran}")
                (repo / ".multiversion/INVENTORY.json").unlink(); ran.clear()
                run.sh = lambda cmd, log=None, cwd=None: ish(cmd, log, cwd, code=2)
                try:
                    res = run.s_setup()
                except OSError as e:     # went on past it: not stopped
                    res = {"went_on": str(e)}
                expect(res.get("code") == 2 and "layer-inventory.py build failed" in res["stop"] and len(ran) == 1,
                       f"layer: a failed inventory build stops setup: {res}")
            else:
                expect(not ran and res.get("code") == 2 and "no .multiversion" in res["stop"],
                       f"single-version: setup stops as before, no inventory build: {res} {ran}")
    # ---- wiring (3): layer-deps exit 18 (a library target not done) stops setup with 18 and its message;
    # --allow-undone-library is passed through, recorded, kept in the resume command, and keeps the target not done
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d); td = repo / ".multiversion/targets/1.21.4"; td.mkdir(parents=True)
        w(repo / ".multiversion/base.properties", "minecraft_version=1.21.1\n")
        w(repo / ".multiversion/INVENTORY.json", '{"items": []}\n')
        w(repo / "build.gradle", 'dependencies {\n    implementation "curse.maven:somelib-1:2"\n}\n')
        a_ = argparse.Namespace(repo=str(repo), target="1.21.4", json=None, budget=1.0, gates="ab", modid="m", base="x",
                                direct=False, from_stage=None, to_stage=None, max_errors=None, confirm_spend=False,
                                port_checkout=["owner/lib=/x/lib"], port_release=[], allow_undone_parent=False,
                                allow_undone_library=False)
        run = Run(a_); ran = []
        msg18 = "layer-deps: STOP library first: sibling library targets not done: owner/lib 1.21.4 (A/B only)\n"
        def sh18(cmd, log=None, cwd=None, on_dry=True):
            ran.append(" ".join(map(str, cmd)))
            hit = "layer-deps.py" in ran[-1] and (("--dry-run" in ran[-1]) == on_dry)
            return CompletedProcess(cmd, 18 if hit else 0, msg18 if hit else "", "")
        run.sh = sh18
        def setup_or_crash():
            try:
                return run.s_setup()
            except OSError as e:      # went on past the resolve into the (absent) layer machinery: not stopped
                return {"went_on": str(e)}
        res = setup_or_crash()
        expect(res.get("code") == UNDONE_EXIT and res["stop"].startswith("library first") and "owner/lib" in res["stop"]
               and any("--allow-undone-library" in c for c in res["choices"]) and len(ran) == 1,
               f"the dry run's exit 18 stops setup with 18 and layer-deps' reason: {res} {ran}")
        ran.clear(); run.sh = lambda cmd, log=None, cwd=None: sh18(cmd, log, cwd, on_dry=False)
        res = setup_or_crash()
        expect(res.get("code") == UNDONE_EXIT and "library first" in res["stop"],
               f"the resolve's own exit 18 stops setup with 18 too (was 2): {res}")
        a_.allow_undone_library = True; ran.clear()
        run.sh = lambda cmd, log=None, cwd=None: (ran.append(" ".join(map(str, cmd))), CompletedProcess(cmd, 0, "", ""))[1]
        try:
            run.s_setup()
        except OSError:
            pass
        deps_calls = [c for c in ran if "layer-deps.py resolve" in c]
        expect(len(deps_calls) == 2 and all("--allow-undone-library" in c for c in deps_calls)
               and run.summary.get("allow_undone_library") is True and "--allow-undone-library" in run.resume_cmd("setup"),
               f"--allow-undone-library: passed to layer-deps, recorded, in the resume command: {deps_calls} {run.resume_cmd('setup')}")
        a_.allow_undone_library = False
        expect("--allow-undone-library" not in run.resume_cmd("setup"), "control: no flag, not in the resume command")
        w(td / "DEPS.md", "# Dependencies\n\n" + UNDONE_LIBRARY_MARK + " `com.example:lib` was built from a checkout\n")
        expect(any("--allow-undone-library" in x for x in target_done(repo, "1.21.4")),
               "a target resolved against an undone library is not done")
        w(td / "DEPS.md", "# Dependencies\n")
        expect(not any("--allow-undone-library" in x for x in target_done(repo, "1.21.4")), "control: a released library")
    # ---- single-version fork: no .multiversion/ -- no freeze, no review, no sweep; fork-port stops at setup as before
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d); git_repo(repo)
        w(repo / "build.gradle", "plugins {}\n")
        a_ = argparse.Namespace(repo=str(repo), target="1.21.4", base="origin/neoforge-1.21.1", port_release=[],
                                upstream="upstream/main", accept_upstream_drift=False)
        expect(drift_stop(repo, a_) is None and not (repo / ".multiversion").exists(), "single-version: no upstream check, nothing written")
        lines = []
        sa = argparse.Namespace(repo=str(repo), chain="1.21.3..1.21.4", from_stage=None, to_stage=None, gates="auto",
                                base="x", modid=None, port_checkout=[], port_release=[], target=None)
        expect(sweep(sa, lambda *x: 0, out=lines.append) == 2 and "single-version" in lines[0], "single-version: --chain is bad input")
        buf, real_argv = io.StringIO(), sys.argv
        sys.argv = ["fork-port.py", "--repo", str(repo), "--target", "1.21.4"]
        try:
            with contextlib.redirect_stdout(buf):
                rc = main()
        finally:
            sys.argv = real_argv
        out_ = buf.getvalue()
        expect(rc == 2 and "no .multiversion/ in the fork" in out_ and not any(k in out_ for k in ("upstream", "REVIEW", "NOT done"))
               and not (repo / ".multiversion").exists(), f"single-version: setup stops as before, exit 2, nothing new said: {out_}")
    return ok


def self_check_review131():
    """The PR #131 review's fork-port findings, each on a layer fixture, plus the single-version path (no .multiversion/:
    nothing of this runs). Every case is wrapped, so a missing function is a FAIL line, not a crash."""
    import contextlib, hashlib, io, types
    from subprocess import CompletedProcess
    ok = True

    def expect(c, msg):
        nonlocal ok
        if not c:
            ok = False; print("FAIL:", msg)

    def case(name, fn):
        nonlocal ok
        try:
            fn()
        except Exception as e:      # noqa: BLE001 -- a case that cannot run is a failed case
            ok = False; print(f"FAIL: {name}: {type(e).__name__}: {e}")

    def git_repo(d):
        pathlib.Path(d).mkdir(parents=True, exist_ok=True)
        g = lambda *x: subprocess.run(["git", "-C", str(d), *x], capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()
        g("init", "-q", "-b", "main"); g("config", "user.email", "a@b"); g("config", "user.name", "a")
        return g

    def w(p, text):
        p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text, encoding="utf-8")

    def gates_md(t, at):
        return (f"Gates for `examplemod` at `{at}` for Minecraft {t} (local environment) -- run by CI:\n\n"
                "| check | result | detail |\n|---|---|---|\n| Gate C -- real client, `launch` | PASS | ok |\n")

    def done_fixture(d):
        """A layer with 1.21.3 and 1.21.4 (inherits 1.21.3), each DONE: gates at HEAD, a complete review, test items,
        the inventory, a prepared tree. -> (repo, git)"""
        repo = pathlib.Path(d); g = git_repo(repo); mv = repo / ".multiversion"
        w(mv / "base.properties", "minecraft_version=1.21.1\n"); w(repo / ".gitignore", "build/\n")
        w(repo / "src/main/java/com/example/mod/A.java", "class A { void f() { x(); } }\n")
        w(repo / "src/main/java/com/example/mod/B.java", "class B {}\n")
        w(mv / "shared/1.21.4/renames.tsv", "# shared rows\n")
        for t, inh in (("1.21.3", None), ("1.21.4", "1.21.3")):
            td = mv / "targets" / t
            w(td / "target.properties", f"inherits={inh}\n" if inh else "x=1\n")
            w(td / "java/com/example/mod/A.java", "class A { void f() { y" + t.replace(".", "") + "(); } }\n")
            w(td / "TEST-ITEMS.md", f"# Manual-test items: Minecraft {t}\n\n- none\n")
        li = load("layer_inventory", "layer-inventory.py")
        with contextlib.redirect_stdout(io.StringIO()):
            li.cmd_build(argparse.Namespace(repo=str(repo), dry_run=False, out=None))
        g("add", "-A"); g("commit", "-qm", "port"); head = g("rev-parse", "--short", "HEAD")
        for t in ("1.21.3", "1.21.4"):
            td = mv / "targets" / t
            w(td / "GATES.md", gates_md(t, head))
            w(td / "REVIEW.md", REVIEW_HEAD.format(t=t).replace("- reviewer: ?", "- reviewer: fresh agent")
              .replace("- date: ?", "- date: 2026-10-16").replace("- commit: ?", f"- commit: {head}")
              + f"| overlay | .multiversion/targets/{t}/java/com/example/mod/A.java | ok | the call's {t} name |\n")
            w(repo / f"build/mc{t}/mv/java/com/example/mod/A.java", "class A {}\n")
        return repo, g

    def baseline(repo):
        for t in ("1.21.3", "1.21.4"):
            expect(target_done(repo, t) == [], f"the fixture's {t} is done: {target_done(repo, t)}")

    # ---- finding 1: a re-merge conflict layer-sync recorded in REMERGE.json keeps the target not done until the
    # overlay changed (a person merged it); fork-port keeps layer-sync's RESIDUE.md section
    def f1():
        with tempfile.TemporaryDirectory() as d:
            repo, g = done_fixture(d); baseline(repo)
            ov = repo / ".multiversion/targets/1.21.4/java/com/example/mod/A.java"
            rel = ov.relative_to(repo).as_posix()
            w(repo / ".multiversion/targets/1.21.4/REMERGE.json", json.dumps({"conflicts": [
                {"overlay": rel, "digest": hashlib.sha256(ov.read_bytes()).hexdigest(), "reason": "a hunk did not apply",
                 "since": "o1..n1"}]}))
            why = target_done(repo, "1.21.4")
            expect(any("did not merge" in x and rel in x for x in why), f"an unmerged overlay on record: not done: {why}")
            expect("REMERGE.json" not in REVIEW_OUTPUTS, "REMERGE.json is a reviewed input, not a run output")
            ov.write_text("class A { void f() { merged(); } }\n", encoding="utf-8")
            expect(not any("did not merge" in x for x in target_done(repo, "1.21.4")),
                   "the overlay changed since the conflict was recorded (a person merged it): no longer a reason")
            (repo / ".multiversion/targets/1.21.4/REMERGE.json").write_text("{not json", encoding="utf-8")
            expect(any("REMERGE.json" in x for x in target_done(repo, "1.21.4")), "an unreadable REMERGE.json fails closed")
        with tempfile.TemporaryDirectory() as d:
            td = pathlib.Path(d) / "t"; td.mkdir()
            sec = SYNC_RESIDUE_SECTION + "\n\n- `F.java` (digest abc123): 1 hunk\n"
            w(td / "RESIDUE.md", "# 1.21.4: residue\n\n" + sec)
            put_residue(td, "1.21.4", "# Residue: gates\n\nfork-port's part\n")
            txt = (td / "RESIDUE.md").read_text(encoding="utf-8")
            expect("fork-port's part" in txt and sec.strip() in txt, f"fork-port's report keeps layer-sync's section: {txt}")
            put_residue(td, "1.21.4", "# Residue: loop\n\nnewer\n")
            txt = (td / "RESIDUE.md").read_text(encoding="utf-8")
            expect("newer" in txt and "fork-port's part" not in txt and txt.count(SYNC_RESIDUE_SECTION) == 1,
                   f"a second report replaces fork-port's part only: {txt}")
            clear_residue(td, "1.21.4")
            txt = (td / "RESIDUE.md").read_text(encoding="utf-8") if (td / "RESIDUE.md").is_file() else ""
            expect(sec.strip() in txt and "newer" not in txt, f"a run that ends clears its part, never layer-sync's: {txt!r}")
            put_residue(td, "1.21.4", "# Residue: x\n"); (td / "RESIDUE.md").write_text("# Residue: x\n", encoding="utf-8")
            clear_residue(td, "1.21.4")
            expect(not (td / "RESIDUE.md").exists(), "a RESIDUE.md that holds only fork-port's part is removed, as before")
            ls = load("layer_sync", "layer-sync.py")
            expect(getattr(ls, "RESIDUE_SECTION", SYNC_RESIDUE_SECTION) == SYNC_RESIDUE_SECTION,
                   "fork-port's copy of layer-sync's section heading matches layer-sync.py")
    case("finding 1", f1)

    # ---- finding 3: done follows GATES.md's commit like listed_green (src/, a parent's overlay, shared/ invalidate
    # it; documents do not), and the review is stale on the same files
    def f3():
        with tempfile.TemporaryDirectory() as d:
            repo, g = done_fixture(d); baseline(repo)
            w(repo / "README.md", "docs\n"); g("add", "README.md"); g("commit", "-qm", "docs")
            expect(target_done(repo, "1.21.4") == [], f"a documentation commit after the gates keeps it done: {target_done(repo, '1.21.4')}")
            for path, text in (("src/main/java/com/example/mod/B.java", "class B { int authors; }\n"),
                               (".multiversion/targets/1.21.3/java/com/example/mod/A.java", "class A { void f() { p(); } }\n"),
                               (".multiversion/shared/1.21.4/renames.tsv", "# shared rows\nOld\tNew\n")):
                g("checkout", "-q", "main"); g("checkout", "-q", "-B", "probe")
                w(repo / path, text); g("commit", "-qam", f"change {path}")
                why = target_done(repo, "1.21.4")
                expect(any("GATES.md" in x and "changed since" in x for x in why),
                       f"{path} committed after the gates ran: the gates are stale: {why}")
                expect(any("since the reviewed commit" in x for x in why), f"{path} changed after the review: re-review: {why}")
            g("checkout", "-q", "main")
            w(repo / "src/main/java/com/example/mod/C.java", "class C {}\n")
            expect(any("GATES.md" in x for x in target_done(repo, "1.21.4")), "an untracked source file is built in: stale")
            (repo / "src/main/java/com/example/mod/C.java").unlink()
            head = g("rev-parse", "--short", "HEAD")
            w(repo / ".multiversion/targets/1.21.4/GATES.md", gates_md("1.21.4", f"{head} + uncommitted: src/x.java"))
            expect(any("edited tree" in x for x in target_done(repo, "1.21.4")), "gates on an edited tree certify no commit")
    case("finding 3", f3)

    # ---- finding 4: REVIEW.md's commit must be a commit id, not a name that moves (HEAD, a branch)
    def f4():
        with tempfile.TemporaryDirectory() as d:
            repo, g = done_fixture(d); baseline(repo)
            rv = repo / ".multiversion/targets/1.21.4/REVIEW.md"
            txt = rv.read_text(encoding="utf-8")
            for name in ("HEAD", "main"):
                rv.write_text(re.sub(r"- commit: \S+", f"- commit: {name}", txt), encoding="utf-8")
                expect(any("commit id" in x for x in target_done(repo, "1.21.4")), f"commit: {name} is refused")
    case("finding 4", f4)

    # ---- finding 5: auto-detection takes the authors' branch the checkout carries, not their default; freeze refuses
    # a branch the checkout lacks commits of; the stop is "moved since the freeze", not HEAD..tip
    def f5():
        with tempfile.TemporaryDirectory() as d:
            up, fork = pathlib.Path(d) / "authors", pathlib.Path(d) / "fork"
            gu = git_repo(up); w(up / "A.java", "a\n"); gu("add", "-A"); gu("commit", "-qm", "authors 1")
            gu("branch", "port-base"); w(up / "A.java", "a2\n"); gu("commit", "-qam", "authors 2 (default branch only)")
            subprocess.run(["git", "clone", "-q", "-o", "upstream", str(up), str(fork)], check=True, capture_output=True)
            gf = lambda *x: subprocess.run(["git", "-C", str(fork), *x], capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()
            gf("config", "user.email", "a@b"); gf("config", "user.name", "a")
            gf("checkout", "-q", "-b", "port", "upstream/port-base")
            w(fork / ".multiversion/base.properties", "minecraft_version=1.21.1\n"); gf("add", "-A"); gf("commit", "-qm", "layer")
            expect(detect_upstream(fork) == "upstream/port-base", f"the branch the port carries, not upstream/HEAD: {detect_upstream(fork)}")
            rec, why = freeze_upstream(fork, "upstream/main")
            expect(rec is None and "keep-current" in (why or ""), f"freeze refuses a branch HEAD lacks commits of: {rec} {why}")
            expect("upstream_commit" not in read_props(fork / ".multiversion/base.properties"), "nothing recorded")
            (fork / ".multiversion/targets/1.21.3").mkdir(parents=True)
            write_props(fork / ".multiversion/base.properties",
                        {"upstream": "upstream/main", "upstream_commit": gu("rev-parse", "main")})
            a_ = argparse.Namespace(repo=str(fork), target="1.21.3", base="origin/neoforge-1.21.1", port_release=[],
                                    upstream=None, accept_upstream_drift=False)
            with contextlib.redirect_stdout(io.StringIO()):
                st = drift_stop(fork, a_, fetch=False)
            expect(st is None, f"nothing moved since the recorded commit: no stop, though HEAD lacks it: {st}")
            gu("checkout", "-q", "main"); w(up / "A.java", "a3\n"); gu("commit", "-qam", "authors 3")
            with contextlib.redirect_stdout(io.StringIO()):
                st = drift_stop(fork, a_, fetch=True)
            expect(st and st["code"] == DRIFT_EXIT and "1 commit(s)" in st["stop"], f"the authors moved since the freeze: 19: {st}")
    case("finding 5", f5)

    # ---- finding 6: a library-only freeze still stops on another --port-release
    def f6():
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d)
            w(repo / ".multiversion/base.properties", "minecraft_version=1.21.1\nlibrary.owner/lib=v1\n")
            (repo / ".multiversion/targets/1.21.3").mkdir(parents=True)
            a_ = argparse.Namespace(repo=str(repo), target="1.21.3", base="x", port_release=["owner/lib@v2"],
                                    upstream=None, accept_upstream_drift=False)
            with contextlib.redirect_stdout(io.StringIO()):
                st = drift_stop(repo, a_, fetch=False)
            expect(st and st["code"] == DRIFT_EXIT and "owner/lib" in st["stop"], f"a library-only freeze: 19 on v2: {st}")
            a_.port_release = ["owner/lib@v1"]
            with contextlib.redirect_stdout(io.StringIO()):
                expect(drift_stop(repo, a_, fetch=False) is None, "the recorded release: no stop")
    case("finding 6", f6)

    # ---- finding 7: DEPS.md (rewritten by every setup) is a run output: it does not make a review stale
    def f7():
        expect("DEPS.md" in REVIEW_OUTPUTS, "DEPS.md is a run output")
        with tempfile.TemporaryDirectory() as d:
            repo, g = done_fixture(d)
            w(repo / ".multiversion/targets/1.21.4/DEPS.md", "# Dependencies\n\nresolved on this machine\n")
            g("add", "-A"); g("commit", "-qm", "deps")
            w(repo / ".multiversion/targets/1.21.4/DEPS.md", "# Dependencies\n\nresolved on another machine\n")
            expect(target_done(repo, "1.21.4") == [], f"a rewritten DEPS.md keeps the review current: {target_done(repo, '1.21.4')}")
    case("finding 7", f7)

    # ---- finding 11: --check-done writes nothing; a missing or stale prepared tree is said precisely, or re-prepared
    # (no compile) where the caller holds the checkout
    def f11():
        with tempfile.TemporaryDirectory() as d:
            repo, g = done_fixture(d)
            chk = repo / ".multiversion/targets/1.21.4/INVENTORY-CHECK.json"
            chk.unlink(missing_ok=True)
            expect(target_done(repo, "1.21.4", write=False) == [] and not chk.exists(), "write=False: nothing written")
            shutil.rmtree(repo / "build/mc1.21.4")
            why = target_done(repo, "1.21.4", write=False)
            expect(len(why) == 1 and "layer.py prepare" in why[0] and "no compile" in why[0],
                   f"a fresh clone: the reason names the prepare command: {why}")
            for f in ("layer.py", "prepare-sources.py"):
                w(repo / ".multiversion/tools" / f, (ROOT / "templates/fork-layer/tools" / f).read_text(encoding="utf-8"))
            with contextlib.redirect_stdout(io.StringIO()):
                why = target_done(repo, "1.21.4", prepare=True)
            expect((repo / "build/mc1.21.4/mv/java/com/example/mod/A.java").is_file(), f"prepare=True re-prepares the tree: {why}")
            past = (repo / "build/mc1.21.4/mv/java").stat().st_mtime - 3600
            for f in [repo / "build/mc1.21.4/mv/java", *(repo / "build/mc1.21.4/mv/java").rglob("*")]:
                os.utime(f, (past, past))        # built an hour before the overlay's last edit
            why = target_done(repo, "1.21.4")
            expect(any("older than" in x for x in why), f"sources newer than the prepared tree: stale: {why}")
            with contextlib.redirect_stdout(io.StringIO()):
                target_done(repo, "1.21.4", prepare=True)
            expect(not any("older than" in x for x in target_done(repo, "1.21.4")), "re-prepared: current again")
            # inherited_overlay_rows (opt-in): base.properties is not an input of the tree (an edit of another key --
            # freeze's upstream= -- leaves it current, as before); the mode the tree was prepared in (layer.py's stamp
            # beside it) is: a switch either way makes it stale, and a re-prepare makes it current again. A layer.py
            # that predates the key ignores it, so the key alone changes nothing there.
            bp = repo / ".multiversion/base.properties"
            keep_bp = bp.read_text(encoding="utf-8") if bp.is_file() else None
            stamp = repo / "build/mc1.21.4/mv/java.inherited_overlay_rows"
            later = (repo / "build/mc1.21.4/mv/java").stat().st_mtime + 60
            mode = lambda why: [x for x in why if "inherited_overlay_rows" in x or "older than" in x]

            def step(body, want, label, reprep=False, layer_text=None):
                w(bp, body); os.utime(bp, (later, later))
                if layer_text is not None:
                    w(repo / ".multiversion/tools/layer.py", layer_text)
                    past = (repo / "build/mc1.21.4/mv/java").stat().st_mtime + 1
                    os.utime(repo / ".multiversion/tools/layer.py", (past - 7200, past - 7200))
                if reprep:
                    with contextlib.redirect_stdout(io.StringIO()):
                        target_done(repo, "1.21.4", prepare=True)
                got = mode(target_done(repo, "1.21.4"))
                expect([x for x in got if want in x] if want else not got, f"{label}: {got}")
            tpl_layer = (ROOT / "templates/fork-layer/tools/layer.py").read_text(encoding="utf-8")
            step("minecraft_version=1.21.1\nupstream=origin/x\n", None, "another key edited: current")
            step("minecraft_version=1.21.1\ninherited_overlay_rows=true\n", "prepared with inherited_overlay_rows off, and "
                 "the layer now has it on", "key set, tree prepared off: stale")
            step("minecraft_version=1.21.1\ninherited_overlay_rows=true\n", None, "re-prepared on: current", reprep=True)
            expect(stamp.is_file(), "an on-mode prepare stamps the tree")
            step("minecraft_version=1.21.1\n", "prepared with inherited_overlay_rows on, and the layer now has it off",
                 "key removed, tree prepared on: stale")
            step("minecraft_version=1.21.1\n", None, "re-prepared off: current", reprep=True)
            expect(not stamp.exists(), "an off-mode prepare removes the stamp (and writes none)")
            old_layer = tpl_layer.replace("def inherited_overlay_rows(", "def _gone_(")
            step("minecraft_version=1.21.1\ninherited_overlay_rows=true\n", None,
                 "a layer.py that predates the key ignores it: the off tree stays current", layer_text=old_layer)
            # downgrade: an on-mode tree (stamped), then a layer.py that predates the key re-prepares it (as the Gradle
            # build does) and never touches the stamp -- the tree is off now, and current, not stale forever
            w(repo / ".multiversion/tools/layer.py", tpl_layer)
            step("minecraft_version=1.21.1\ninherited_overlay_rows=true\n", None, "on again: current", reprep=True)
            expect(stamp.is_file(), "stamped again")
            older = (tpl_layer.replace("def inherited_overlay_rows(", "def _predates_it(")
                     .replace("        if inherited_overlay_rows():", "        if False:")
                     .replace("        if stamp is not None and stamp.is_file():\n            stamp.unlink()", "")
                     .replace("        if rc == 0 and inherited_hits is not None and stamp is not None:\n"
                              "            stamp.write_text(f\"{INHERITED_ROWS}=true\\n\", encoding=\"utf-8\")", ""))
            expect(older.count("stamp.unlink()") == 0 and "stamp.write_text" not in older, "the older layer.py stand-in")
            w(repo / ".multiversion/tools/layer.py", older)
            __import__("time").sleep(0.05)
            r = subprocess.run([sys.executable, str(repo / ".multiversion/tools/layer.py"), "prepare", "--target", "1.21.4",
                                "--src", "src/main/java", "--out", "build/mc1.21.4/mv/java"], cwd=str(repo),
                               capture_output=True, text=True, encoding="utf-8")
            expect(r.returncode == 0 and stamp.is_file(), f"the older layer.py prepared and left the stamp: {r.stderr[-300:]}")
            got = mode(target_done(repo, "1.21.4"))
            expect(not [x for x in got if "inherited_overlay_rows" in x],
                   f"a stamp older than the tree beside it is ignored (an older layer.py re-prepared it off): {got}")
            w(repo / ".multiversion/tools/layer.py", tpl_layer)
            if keep_bp is None:
                bp.unlink()
            else:
                w(bp, keep_bp)
            buf, real_argv = io.StringIO(), sys.argv
            chk.unlink(missing_ok=True)
            sys.argv = ["fork-port.py", "--repo", str(repo), "--target", "1.21.4", "--check-done"]
            try:
                with contextlib.redirect_stdout(buf):
                    main()
            finally:
                sys.argv = real_argv
            expect(not chk.exists(), f"--check-done only asks: no INVENTORY-CHECK.json written: {buf.getvalue()}")
    case("finding 11", f11)

    # ---- finding 12: a sweep stops on a refusal 18 (a stop, nothing built) and never reads an older run's summary
    def f12():
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d); mv = repo / ".multiversion"
            w(mv / "base.properties", "minecraft_version=1.21.1\n")
            for t_, inh in (("1.21.3", None), ("1.21.4", "1.21.3"), ("1.21.5", "1.21.4")):
                w(mv / "targets" / t_ / "target.properties", f"inherits={inh}\n" if inh else "x=1\n")
            w(repo / "build/mc1.21.4/fork-port/fork-port.json", json.dumps({"stop": {"stage": "gates", "left": "OLD RUN"}}))
            seen = []

            def runner(cmd, env):
                t_ = cmd[cmd.index("--target") + 1]; seen.append(t_)
                if t_ == "1.21.3":
                    w(repo / "build/mc1.21.3/fork-port/fork-port.json", json.dumps(
                        {"stop": {"stage": "setup", "code": UNDONE_EXIT, "left": "library first: owner/lib 1.21.3"}}))
                return UNDONE_EXIT
            sa = argparse.Namespace(repo=str(repo), chain="1.21.3..1.21.5", from_stage="setup", to_stage=None, gates="ab",
                                    base="x", modid=None, port_checkout=[], port_release=[], upstream=None,
                                    accept_upstream_drift=False, target=None)
            lines = []
            with contextlib.redirect_stdout(io.StringIO()):
                rc = sweep(sa, runner, out=lines.append)
            expect(rc == UNDONE_EXIT and seen == ["1.21.3"] and "RED at setup" in "\n".join(lines),
                   f"a library refusal (18 with a stop) is red: the sweep stops: {rc} {seen} {lines}")
            seen.clear(); lines.clear(); sa.chain = "1.21.4..1.21.5"
            with contextlib.redirect_stdout(io.StringIO()):
                rc = sweep(sa, lambda cmd, env: (seen.append(1), 14)[1], out=lines.append)
            expect(rc == 14 and "OLD RUN" not in "\n".join(lines), f"a child that wrote no summary: no stale reason: {lines}")
    case("finding 12", f12)

    # ---- findings 1 + 13 in drive(): the end message, and RESIDUE.md handling around layer-sync's section
    def f13():
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d); td = repo / ".multiversion/targets/1.21.4"
            w(td / "GATES.md", gates_md("1.21.4", "abc1234"))
            sec = SYNC_RESIDUE_SECTION + "\n\n- `F.java` (digest abc): 1 hunk\n"
            w(td / "RESIDUE.md", "# Residue: gates\n\nold fork-port part\n\n" + sec)
            run = types.SimpleNamespace(repo=repo, tdir=td, jpath=repo / "fp.json", summary={"stages": {}},
                                        save=lambda: None, resume_cmd=lambda s: f"resume {s}",
                                        drift=lambda a: drift_stop(repo, a), test_items=lambda: write_test_items(repo, td, "1.21.4"),
                                        done=lambda prepare=False: target_done(repo, "1.21.4", prepare=prepare),
                                        skeleton=lambda: review_skeleton(repo, td, "1.21.4"),
                                        records_rel=lambda: ".multiversion/targets/1.21.4/", review_note=lambda n: "")
            a_ = argparse.Namespace(repo=str(repo), target="1.21.4", from_stage=None, to_stage=None, gates="ab")
            g_ = globals(); keep = {k: g_[k] for k in ("execute", "drift_stop", "write_test_items", "target_done")}
            g_.update(execute=lambda r, t: None, drift_stop=lambda r, a: None, write_test_items=lambda r, t, x: (0, None),
                      target_done=lambda *x, **k: [])
            try:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = drive(run, a_, [], list(STAGES))
                txt = (td / "RESIDUE.md").read_text(encoding="utf-8") if (td / "RESIDUE.md").is_file() else ""
                expect(rc == 0 and "fork-port 1.21.4: done" in buf.getvalue(), f"the end message: {buf.getvalue()}")
                expect(sec.strip() in txt and "old fork-port part" not in txt, f"done keeps layer-sync's section: {txt!r}")
                a_.to_stage = "moves"; buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    drive(run, a_, [], ["setup", "stage", "moves"])
                expect("fork-port 1.21.4: stages setup, stage, moves ok" in buf.getvalue(), f"partial: {buf.getvalue()}")
                a_.to_stage = None
                g_["execute"] = lambda r, t: {"stage": "gates", "left": "1 gate(s) red", "stop": "1 gate(s) red", "code": 12,
                                               "choices": ["fix it"]}
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = drive(run, a_, [], list(STAGES))
                txt = (td / "RESIDUE.md").read_text(encoding="utf-8")
                expect(rc == 12 and "1 gate(s) red" in txt and sec.strip() in txt, f"a stop's report keeps the section: {txt}")
            finally:
                g_.update(keep)
    case("finding 13", f13)

    # ---- finding 14: the fetch never prompts (GIT_TERMINAL_PROMPT=0, a short timeout) and says it could not fetch
    def f14():
        with tempfile.TemporaryDirectory() as d:
            up, fork = pathlib.Path(d) / "authors", pathlib.Path(d) / "fork"
            gu = git_repo(up); w(up / "A.java", "a\n"); gu("add", "-A"); gu("commit", "-qm", "authors 1")
            subprocess.run(["git", "clone", "-q", "-o", "upstream", str(up), str(fork)], check=True, capture_output=True)
            w(fork / ".multiversion/base.properties", f"upstream=upstream/main\nupstream_commit={gu('rev-parse', 'HEAD')}\n")
            (fork / ".multiversion/targets/1.21.3").mkdir(parents=True)
            real, calls = subprocess.run, []

            def fake(cmd, *x, **k):
                if "fetch" in cmd:
                    calls.append(k)
                    return CompletedProcess(cmd, 128, "", "fatal: could not read Username")
                return real(cmd, *x, **k)
            a_ = argparse.Namespace(repo=str(fork), target="1.21.3", base="x", port_release=[], upstream=None,
                                    accept_upstream_drift=False)
            subprocess.run = fake
            try:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    st = drift_stop(fork, a_)
            finally:
                subprocess.run = real
            env = (calls[0].get("env") or {}) if calls else {}
            expect(calls and env.get("GIT_TERMINAL_PROMPT") == "0" and (calls[0].get("timeout") or 999) <= 60,
                   f"the fetch cannot prompt and gives up quickly: {calls}")
            expect(st is None and "could not fetch" in buf.getvalue(), f"says so and compares with the last fetch: {buf.getvalue()}")
    case("finding 14", f14)

    # ---- single-version: no .multiversion/ -- none of it runs, the same answers as before
    def single():
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d); git_repo(repo)
            a_ = argparse.Namespace(repo=str(repo), target="1.21.4", base="x", port_release=["owner/lib@v2"], upstream=None,
                                    accept_upstream_drift=False)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                st = drift_stop(repo, a_)
            expect(st is None and not buf.getvalue() and not (repo / ".multiversion").exists(), "single-version: no drift check")
            expect(target_done(repo, "1.21.4") == ["1.21.4 is not a target of this layer"], "single-version: no target")
            expect(detect_upstream(repo) is None, "single-version clone with no upstream remote: nothing detected")
    case("single-version", single)

    def gap7():
        # issue #133 gap 7: a not-ok `item` row is the reviewer's verdict with its note, not "unaccounted"; a `missing`
        # (a file that should exist) or `shared` (a shared helper) row needs a verdict, and a not-ok one of any kind blocks
        text = (REVIEW_HEAD.format(t="1.21.4") + "| item | main:A.java#mixin:X | not-ok | the hook is gone, behaviour lost |\n"
                "| missing | src/main/resources/data/m/tags/item/repair.json | ? | ? |\n"
                "| shared | LegacyGl | ok | read against 1.21.1's draw |\n| changed | src/main/java/B.java | not-ok | wrong id |\n")
        _f, rows = parse_review(text)
        got = extra_row_problems(rows, [("changed", "src/main/java/B.java")])
        expect(got == ["reviewer says not-ok: item main:A.java#mixin:X -- the hook is gone, behaviour lost",
                       "no verdict: missing src/main/resources/data/m/tags/item/repair.json"],
               f"extra review rows: {got}")
        inv = ["behaviour inventory: unaccounted main:A.java#mixin:X (not in the target's file)",
               "behaviour inventory: unaccounted main:C.java#event:E (not in the target's file)"]
        got = drop_reported_items(inv, not_ok_items(text))
        expect(got == inv[1:], f"an item the reviewer marked not-ok is reported once, with its note: {got}")
    case("gap 7 review rows", gap7)
    return ok


if __name__ == "__main__":
    sys.exit(main())
