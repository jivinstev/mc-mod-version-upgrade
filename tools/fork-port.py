#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Port one version branch of a fork in one command, and hand back only the residue.

    python3 tools/fork-port.py --repo DIR --target 1.21.5 [--budget 15] [--gates auto|none|ab|full]
                               [--json out.json] [--from-stage <name>] [--to-stage <name>] [--base REF] [--modid ID]
                               [--port-checkout owner/repo=DIR] [--port-release owner/repo@TAG]
                               [--max-errors N] [--confirm-spend] [--allow-undone-parent] [--allow-undone-library]
                               [--upstream REMOTE/BRANCH] [--accept-upstream-drift] [--untaken-ok FROZEN_JSON]
                               [--self-check]
    python3 tools/fork-port.py --repo DIR --target <t> --check-done
    python3 tools/fork-port.py --repo DIR --target <t> --write-test-items
    python3 tools/fork-port.py --repo DIR --chain FROM..TO [--gates ab|full|none]     # = fork-branches.py cascade

The fork-versions skill's procedure (steps 1-9) as stages, the way tools/run-port.py is the jar-port procedure, on a
fork with VERSION BRANCHES (tools/fork-branches.py): version t is the branch neoforge-<t>, a plain single-version build
at Minecraft t, with its records in .port/<t>/. The orchestrating session starts this and then reads ONE summary (the
last lines of stdout, the --json file, and .port/<t>/RESIDUE.md when it stopped) -- never the workers' transcripts or
the build logs.

A SINGLE-VERSION FORK (no version branch) is never touched: fork-port stops at once (exit 2), having written nothing --
versions are added as version branches only when the maintainer decides it (fork-branches.py add-target). A checkout
with the removed .multiversion/ layer at HEAD is refused (exit 2, tools/fork-kind.py).

Stages, each logged as one line and skipped with a reason when it does not apply:
  setup    off t's branch: fork-branches.py add-target when neoforge-<t> does not exist yet (refused, exit 18, while the
           version it stands on is not done; --allow-undone-parent is the recorded development escape), then a checkout
           of it; layer-inventory.py build when .port/INVENTORY.json is missing; layer-deps.py resolve when the authors'
           build pins dependencies (a required one with no build for t stops here, exit 15; a sibling library whose
           version is not done stops it with exit 18 -- --allow-undone-library, development only, is passed through,
           recorded in DEPS.md and the summary, and kept in the resume command; --port-checkout passes a sibling fork's
           checkout for its jar, --port-release its release, pinned for CI); then the data forms the version needs
           (1.21.2 recipes and item definitions, 26.2 entity predicates, 26.3 data, GeckoLib 5 asset paths) written into
           the branch's own src/main/resources
  stage    `./gradlew compileJava` to stage Minecraft; while createMinecraftArtifacts fails on the access transformer,
           fix-access-transformer.py --overrides-from <log> on the branch's AT (bounded: 4 rounds)
  moves    build-class-move-map.py between the parent version's staged jars and this one's -> .port/<t>/moves.generated.tsv,
           applied to the source as rows (a file the rows alone made is re-made whole, a hand-changed file in its code
           only); the 26.1.2 entityCutout swap is listed; then the converters whose change lands in the hop from the
           parent's version run (tools/mechanical-hop.py run_ladder: convert-override-signatures --crossing; a converter
           whose output needs a later version is an advisory)
  measure  compile every source set (srcsets.compile_tasks); burndown-count.sh decides whether it is a count
  loop     file-loop.py --budget N on the branch's source (its kept rows land in .port/<t>/renames.tsv)
  audit    audit-mixin-targets.py against the version's sources jar, then audit-event-listeners.py (abstract events)
           over every source tree against the branch's resolved classpath (only at 0 errors)
  gates    ci-gates.py --mc <t>: none = skip; ab = author build, Gate A, Gate B; full = + Gate C phases. After a green run, the
           mod's own log files (harness/run/logs, minus vanilla's) are compared with the parent version's last gate run: a file
           it wrote there and not here is residue (exit 11) -- a subsystem stopped running (CATALOG X47)
  cost     port-cost.py --target <t> appended to .port/<t>/COST.md: the requests that worked on <t>, from where its last
           record ended (no record: all of them on this machine), plus the loop workers' own recorded dollars when the
           loop ran

THE STOP CONTRACT. When a stage cannot finish this does not hand the rest to anyone silently: it prints a
STOPPED block and writes the RESIDUE REPORT (into --json and .port/<t>/RESIDUE.md): the remaining errors grouped by
CATALOG entry / family (not the raw log), the loop's escalation record, audit mismatches or failing gates with their
one-line reason, and the command to resume (`--from-stage <stage>`). Whoever picks it up fixes what RESIDUE.md lists,
and puts each fix in the shared library (templates/fork-layer/shared/<t>) or a converter -- not in one fork.

ONE RUN PER CHECKOUT (CATALOG X44). The run takes build/.fork-port.lock (pid, stale-pid aware) for its whole length and a
second run on the same checkout is refused at once with a message naming the holder; its own children (ci-gates.py)
are let through. A lock whose process is gone is taken over. tools/checkout-lock.py CheckoutLock is the code.

NO RESIDENT GRADLE (issue #122). Every Gradle the run starts (its own, and its children's: layer-deps, file-loop,
ci-gates) runs with -Dorg.gradle.daemon=false added to GRADLE_OPTS (unless GRADLE_OPTS already sets org.gradle.daemon),
so none of the run's daemons is still resident when Gate C's free-memory guard measures. Other checkouts' daemons are
never stopped. When Gate C is still refused (or killed) for memory, RESIDUE.md lists the processes holding the most
resident memory (PID, RSS, command).

A RESUME RE-MEASURES. `--from-stage S` keeps the earlier stages' results for the summary but never their numbers: the
compile count the loop, audit and gates rely on is always one this run measured (a stale 0 once skipped the audit and
every gate and reported the target done). A resume past setup runs on t's own checkout.

DONE IS ONE FIXED LIST: tools/fork-branches.py target_done() (the fork-versions skill's "Done"). A run whose gates are
green but that lacks the rest exits 18 and adds a `?` row to REVIEW.md for every item without one; `--check-done` only
asks (on the branch's own checkout it adds the same `?` rows, never a verdict); `--write-test-items` writes
.port/<t>/TEST-ITEMS.md alone.

THE UPSTREAM FREEZE. .port/<t>/branch.properties records the authors' branch (upstream=), the commit the buildout
started from (upstream_commit=) and each sibling library's release (library.<owner>/<repo>=<tag>); `fork-branches.py
freeze` writes it (and --upstream here, when none is recorded). Every run to the end fetches the branch and stops (exit
19) when it holds commits the checkout lacks, or when --port-release names another release of a recorded library,
naming the keep-current command; --accept-upstream-drift continues and appends the drift to .port/<t>/DRIFT.md. A
freeze whose authors' branch cannot be read (no such remote, never fetched) stops the same way: removing the remote is
never a way past it. A weekly round whose frozen input does not take the authors' code (`take_authors: false` in
round/frozen.json, read from the working directory, or the file --untaken-ok names) goes on: the drift is recorded in
DRIFT.md as left out by the plan (with take_authors true, only what came after the plan's upstream_commit, once the
checkout carries it). A library release other than the frozen one still stops. A --to-stage run is not checked.

THE SWEEP. `--chain FROM..TO` is tools/fork-branches.py cascade FROM..TO (merge each branch into the next, then the
no-model gates per branch; --gates auto means ab).

Exit codes: 0 done; 10 stopped before/in the compile (stage, moves, measure, loop); 11 Gate A/B (or the author's
build) red; 12 Gate C red; 13 mixin / event-listener audit; 14 the checkout is in use by another run; 15 a required
dependency has no build for the version (DEPS.md / the STOPPED block name it); 17 every stage passed but the version
has no green Gate C recorded (GATES.md), or the gates were not run (--gates none): A/B green is not done -- a version
is done only after its real-client run. A full gate run that is not green is written to GATES-RED.md, never over
GATES.md (the last green record stays); while it is there the version is not done and `--gates auto` runs full; a
green full run deletes it. Every run to the end (from any --from-stage) stops with exit 10 while STUBS.json holds an
unreviewed stub signal; 18 Gate C green but not done (the done list's reasons, listed), a new version over an undone
parent, or a sibling library's version that is not done (layer-deps.py resolve's exit 18); 19 the authors' branch or
a recorded library release moved since the buildout's freeze; 2 bad input, a single-version fork or a layer checkout.
Standard library only.
"""
import argparse, datetime, hashlib, importlib.util, json, os, pathlib, re, shutil, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from gitbash import BASH   # noqa: E402

STAGES = ["setup", "stage", "moves", "measure", "loop", "audit", "gates", "cost"]
STAGE_ROUNDS = 4


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
    """(skipped, to_run) for --from-stage, and --to-stage (stop after that stage: keep-current measures a version
    with `--from-stage stage --to-stage measure`)."""
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


def convert_data(repo, mc, props_text="", deps_text="", layout=True, only=None):
    """Every data conversion version `mc` needs, applied in place to `repo`'s src/main/resources: the 1.21.2 recipes and
    item definitions, the 26.2 entity predicates, the 26.3 data and the GeckoLib 5 asset paths (layer_recipes,
    layer_entity_predicates, layer_263_data, layer_client_items, layer_geckolib_assets), and first, only when its
    --verify finds data stranded in a pre-1.21 layout (Forge 1.20.1's plural directories, forge: ids),
    fix-datapack-layout.py --apply (layout=False: not that step -- fork-port's setup, on a base port that ran it).
    Each reads the tree and writes only what differs from the form `mc` needs, so a file already in that form is left
    byte-identical and a second run changes nothing. One function for a version branch (fork-port's setup stage, the
    cascade after each merge-up) and a single-version fork (port-sync after the authors' merge) (CLAUDE.md P1).
    props_text: the version's branch.properties (or port.properties), deps_text: its DEPS.md (both read for the
    GeckoLib major). only: paths under src/main/resources (the files a merge brought in): a file is written only when
    it, or what it is made from (an item's model, a GeckoLib 4.x asset), is one of them -- so a definition the next
    version up made by hand is never written here and turned into a conflict at the next merge-up.
    -> (written paths under src/main/resources, refused)"""
    repo = pathlib.Path(repo)
    refused = []
    if layout and (repo / "src/main/resources").is_dir() and subprocess.run(
            [sys.executable, str(ROOT / "tools/fix-datapack-layout.py"), str(repo), "--verify"],
            capture_output=True, text=True, encoding="utf-8").returncode == 1:
        r = subprocess.run([sys.executable, str(ROOT / "tools/fix-datapack-layout.py"), str(repo), "--apply"],
                           capture_output=True, text=True, encoding="utf-8")
        refused += [f"fix-datapack-layout: {l.strip()}" for l in (r.stdout or "").splitlines() if "REFUS" in l]
    written = []
    with tempfile.TemporaryDirectory() as d:
        mv = pathlib.Path(d) / "mv"
        tdir = mv / "targets" / mc
        tdir.mkdir(parents=True)
        (tdir / "target.properties").write_text(props_text or "", encoding="utf-8")
        if deps_text:
            (tdir / "DEPS.md").write_text(deps_text, encoding="utf-8")
        for _n, ref in (layer_recipes(repo, tdir, mc), layer_entity_predicates(repo, tdir, mc, mv),
                        layer_263_data(repo, tdir, mc, mv)):
            refused += list(ref)
        layer_client_items(repo, tdir, mc)
        layer_geckolib_assets(repo, tdir, mc)
        out = tdir / "resources"
        for f in sorted(out.rglob("*")) if out.is_dir() else []:
            if f.is_file():
                rel = f.relative_to(out).as_posix()
                if only is not None and rel not in only and _made_from(rel) not in only:
                    continue
                dest = repo / "src/main/resources" / rel
                if not dest.is_file() or dest.read_bytes() != f.read_bytes():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(f.read_bytes()); written.append(rel)
    return written, refused


def _made_from(rel):
    """The file a generated resource is made from: an item definition from its item model, a GeckoLib 5 asset from its
    4.x path; else the file itself (a recipe, an advancement: converted where it is)."""
    m = re.match(r"^assets/([^/]+)/items/(.+)$", rel)
    if m:
        return f"assets/{m.group(1)}/models/item/{m.group(2)}"
    for old_dir, _suffix, new_dir in GECKO_ASSETS:
        pre = re.match(r"^(assets/[^/]+/)" + re.escape(new_dir) + "/(.+)$", rel)
        if pre:
            return f"{pre.group(1)}{old_dir}/{pre.group(2)}"
    return rel


def data_form_of(mc, rel, data):
    """The bytes convert_data gives the resource `rel` (under src/main/resources) holding `data` at version `mc`; None
    when it leaves the file as it is or refuses it. The cascade's untaken check reads it: a version branch's data file
    in this form (or its parent version's) has taken the authors' change, though no line of theirs is left as they
    wrote it."""
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "src/main/resources" / rel
        f.parent.mkdir(parents=True)
        f.write_bytes(data)
        convert_data(d, mc, layout=False, only={rel})
        out = f.read_bytes().replace(b"\r\n", b"\n")   # a converter's text-mode write is CRLF on Windows; git stores LF
    return out if out != data.replace(b"\r\n", b"\n") else None


DATA_FORMS_REPORT = "DATA-FORMS.md"


def data_forms_after_merge(repo, mc, trailer="", say=print):
    """convert_data on `repo`'s checkout at version `mc` right after the authors' code came in (port-sync's merge on a
    single-version fork, each merge-up of the version-branch cascade): an authors' file merged in an older version's
    form would otherwise reach `mc` as it is and be dropped at load with no other error (a 1.21.1 recipe ingredient on
    1.21.2+, a 1.21 entity predicate on 26.2). What changed is committed on its own, "data forms for <mc> after the
    authors' merge"; what a converter refused, of the files the last merge brought in, is listed in
    .port/<mc>/DATA-FORMS.md (rewritten each run, removed when nothing is refused), so a second run on the same tree
    commits nothing. Never run on a tree with uncommitted changes. -> (changed paths, refused, the commit or None)"""
    repo = pathlib.Path(repo)
    if subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True, encoding="utf-8").stdout.strip():
        say(f"data forms {mc}: skipped: the working tree has uncommitted changes")
        return [], [], None
    rec = repo / ".port" / mc
    rd = lambda n: (rec / n).read_text(encoding="utf-8", errors="replace") if (rec / n).is_file() else ""
    # only the files the last merge (HEAD against its first parent) brought in are converted, and only their refusals
    # reported: a converter cannot always read its own output (a 26.3 advancement is no 1.21 predicate), and a file
    # already on the branch is in this version's form (measured: every file of 19 real version branches, 0 rewritten,
    # 7 such refusals). A re-run on our own commit reads the merge below it, so it changes nothing
    top = "HEAD^" if subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%s"], capture_output=True,
                                    text=True, encoding="utf-8").stdout.startswith(f"data forms for {mc} ") else "HEAD"
    merged = {f[len("src/main/resources/"):] for f in subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only", f"{top}^1", top, "--", "src/main/resources"],
        capture_output=True, text=True, encoding="utf-8").stdout.split()}
    _, refused = convert_data(repo, mc, rd("branch.properties") or rd("port.properties"), rd("DEPS.md"), only=merged)
    refused = [x for x in refused if x.startswith("fix-datapack-layout:") or x.split(":", 1)[0] in merged]
    report = rec / DATA_FORMS_REPORT
    if refused:
        rec.mkdir(parents=True, exist_ok=True)
        report.write_text(f"# Data the converters for {mc} could not convert\n\n"
                          "Written by tools/fork-port.py data_forms_after_merge after the authors' merge. Each file "
                          "below is in an older version's form and is dropped at load: convert it by hand.\n\n"
                          + "".join(f"- {x}\n" for x in sorted(set(refused))), encoding="utf-8")
    elif report.is_file():
        report.unlink()
    g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, encoding="utf-8")
    rel = f".port/{mc}/{DATA_FORMS_REPORT}"
    g("add", "-A", "--", "src/main/resources", *([rel] if report.is_file() or g("ls-files", "--", rel).stdout else []))
    changed = [l for l in g("diff", "--cached", "--name-status").stdout.splitlines() if l]
    if not changed:
        say(f"data forms {mc}: every merged data file is in {mc}'s form" + (f"; {len(refused)} refused, as before"
                                                                             if refused else ""))
        return [], refused, None
    r = g("commit", "-q", "-m", f"data forms for {mc} after the authors' merge\n\n"
          + "".join(f"{l}\n" for l in changed) + (trailer or "\nMade by tools/fork-port.py convert_data."))
    if r.returncode:
        say(f"data forms {mc}: the commit failed: {(r.stderr or r.stdout).strip()[-300:]}")
        return changed, refused, None
    sha = g("rev-parse", "HEAD").stdout.strip()
    say(f"data forms {mc}: {len(changed)} file(s) converted, committed {sha[:12]}"
        + (f"; {len(refused)} refused, listed in .port/{mc}/{DATA_FORMS_REPORT}" if refused else ""))
    return changed, refused, sha


RECOUNT_MARK = "# --- the capped compile hit the cap; uncapped recount (portFullErrors) ---"


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


RED_GATES = "GATES-RED.md"       # a full gate run that was not green, kept apart from the last green GATES.md


def gates_auto(tdir):
    """`--gates auto`: "full" until targets/<t>/GATES.md records a green Gate C (a PASS row naming Gate C, no FAIL row),
    then "ab". Every Minecraft version a fork supports gets one real-client run on its initial port. A red full run on
    record (GATES-RED.md) means "full" again, whatever GATES.md says."""
    if (tdir / RED_GATES).is_file():
        return "full"
    g = tdir / "GATES.md"
    text = g.read_text(encoding="utf-8", errors="replace") if g.is_file() else ""
    rows = [l for l in text.splitlines() if l.startswith("|")]
    green_c = any("Gate C" in l and "| PASS |" in l for l in rows) and not any("| FAIL |" in l for l in rows)
    return "ab" if green_c else "full"


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


# ---------------------------------------------------------------- a version's records (R1)
#
# DONE is one fixed list, tools/fork-branches.py target_done(); what follows is shared with it and with
# tools/layer-deps.py and tools/known-issues.py: the run outputs a review does not judge, the files that are not in a
# player's jar, and REVIEW.md's form. Every file here is Markdown: written after the gates, it must not make
# port-publish.py read the version's GATES.md as stale (a *.md is not in a player's jar).

REVIEW_OUTPUTS = ("GATES.md", "GATES-RED.md", "COST.md", "RESIDUE.md", "REVIEW.md", "TEST-ITEMS.md",   # not what a review judged
                  "BEHAVIOUR.json",            # the reviewer's own record beside REVIEW.md (a moved/dropped item and
                                               # why): writing it must not make the review it belongs to stale; it is
                                               # still read as a reviewed input by layer-inventory.py's account
                  "INVENTORY-CHECK.json",      # layer-inventory.py's account, rewritten by every done check
                  "DEPS.md",                   # layer-deps.py resolve's table, rewritten by every setup (machine paths)
                  "DRIFT.md",                  # drift_stop's log of drift accepted or left out by a round's plan (a
                                               # version branch's .port/<v>/DRIFT.md), appended by a run after review
                  "KNOWN-ISSUES.json")         # the known-issues ledger (tools/known-issues.py): a record of findings
# Files of a target's directory that are not in a player's jar: a change to them does not make GATES.md stale
# (tools/layer-deps.py NOT_JAR, the same list). REMERGE.json is the cascade's record of an authors' change a version did
# not take: a reviewed input (it is not in REVIEW_OUTPUTS), but not jar content.
NOT_JAR_FILES = frozenset(REVIEW_OUTPUTS) | {"STUBS.json", "BEHAVIOUR.json", "INVENTORY.json", "REMERGE.json"}
# records matched by their path from the repository root, not their name: the fork's README (a README under src/ is
# jar content). A change to one makes neither gates nor a review stale.
ROOT_RECORDS = ("README.md",)
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


def _git(repo, *args, env=None, timeout=120):
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, **({"env": env} if env is not None else {}))
        return r.returncode, (r.stdout.strip() if r.returncode == 0 or r.stdout.strip() else (r.stderr or "").strip())
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def put_residue(tdir, t, md):
    """Write fork-port's stop report to tdir/RESIDUE.md."""
    (tdir / "RESIDUE.md").write_text(md.rstrip("\n") + "\n", encoding="utf-8")


def clear_residue(tdir, t):
    """A run that ended clears its own report: tdir/RESIDUE.md goes."""
    (tdir / "RESIDUE.md").unlink(missing_ok=True)


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


UNDONE_LIBRARY_MARK = "**Development only (`--allow-undone-library`):**"     # tools/layer-deps.py render_md()


# ---------------------------------------------------------------- the authors' commit a buildout started from (R4)
#
# A version branch's .port/<t>/branch.properties records `upstream=<remote>/<branch>` (the authors' branch, as
# port-sync.py --upstream names it), `upstream_commit=<sha>` (the authors' commit the branch carried when the buildout
# started: the merge-base of HEAD and that branch, which HEAD must carry in full: freeze refuses a branch HEAD lacks
# commits of) and `library.<owner>/<repo>=<tag>` (each sibling library's release the buildout resolves against, from
# --port-release). Recorded by `fork-branches.py freeze` (the branch: --upstream, else the one `upstream` remote branch
# HEAD carries -- never the remote's default branch as such); a new version inherits its parent's record. A run to the
# end then stops with exit 19 when the authors' branch has commits after the recorded one that the checkout does not
# carry (the upstream moved: keep-current first), or -- frozen upstream or not -- when a --port-release names another
# release of a recorded library; the fetch never prompts. --accept-upstream-drift continues and records it in
# .port/<t>/DRIFT.md. A single-version fork has no version branch: nothing here runs.

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


def add_remote_hint(repo, ref):
    """When the freeze's `upstream=<remote>/<branch>` names a remote this clone does not have: how to add it, fetch-only
    (no push URL: nothing here ever pushes to the authors). The URL from the head's .github/weekly-plan.json when it
    names this fork's authors, else a placeholder. None when the remote exists (a fetch problem, not a missing remote)."""
    remote = (ref or "").split("/", 1)[0]
    if not remote or _git(repo, "remote", "get-url", remote)[0] == 0:
        return None
    url = "<the authors' repository URL>"
    for b in ("origin/HEAD", "HEAD"):
        rc, cfg = _git(repo, "show", f"{b}:.github/weekly-plan.json")
        if rc == 0:
            try:
                ups = [f.get("upstream") for f in json.loads(cfg).get("forks", []) if f.get("upstream")]
            except ValueError:
                ups = []
            if len(set(ups)) == 1:
                url = ups[0]
            break
    return (f" -- this clone has no `{remote}` remote. Add it once, fetch-only: git remote add {remote} {url} && "
            f"git remote set-url --push {remote} DISABLED && git fetch {remote}")


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


def freeze_upstream(repo, ref, libraries, accept_drift, props_file):
    """Record the authors' commit the buildout starts from (the merge-base of HEAD and `ref`, default the recorded or
    detected `upstream` branch) and each `owner/repo@TAG` library release in props_file (a version branch's
    .port/<t>/branch.properties). -> (what was recorded, None) or (None, why not).
    Pinning a library only (libraries, no `ref`, an upstream already recorded) leaves the upstream record as it is, so a
    moved authors' branch never blocks a library pin (issue #133, gap 3); accept_drift records the merge-base although
    HEAD lacks commits of the branch (the run then stops with 19 until keep-current, or continues with
    --accept-upstream-drift)."""
    repo = pathlib.Path(repo)
    bp = pathlib.Path(props_file)
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


# A weekly round that does not take the authors' code (keep-current, "The weekly round": `take_authors: false` in its
# frozen input) leaves their branch ahead of every freeze ON PURPOSE. The guard reads that decision instead of being
# got past (a deleted remote is never the way): --untaken-ok <frozen.json>, or the round's own round/frozen.json in the
# working directory, names this fork; its drift is then recorded in DRIFT.md as left out by the plan, and the run goes
# on. Only the authors' branch: a library release other than the frozen one still stops. A frozen input older than
# FROZEN_MAX_DAYS (7: a week -- last week's round) or more is not this round's decision and is ignored.
FROZEN_DEFAULT = pathlib.Path("round/frozen.json")
FROZEN_MAX_DAYS = 7


def origin_slug(repo):
    """owner/repo of the clone's origin remote (GitHub https or ssh), or None."""
    rc, url = _git(repo, "remote", "get-url", "origin")
    m = re.search(r"github\.com[/:]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", url.strip()) if rc == 0 else None
    return m.group(1) if m else None


def frozen_entry(repo, path=None, now=None):
    """(entry of this fork in a round's frozen input, the file, None) or (None, file or None, why not). path: an
    explicit --untaken-ok file; without one, round/frozen.json in the working directory when it exists."""
    explicit = path is not None
    f = pathlib.Path(path) if explicit else FROZEN_DEFAULT
    if not f.is_file():
        return None, (f if explicit else None), (f"--untaken-ok {f}: no such file" if explicit else None)
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        forks = data.get("forks") or []
    except (OSError, ValueError, AttributeError) as e:
        return None, f, f"{f} is not a frozen round input ({e})"
    at = data.get("frozen_at") if isinstance(data, dict) else None
    if not isinstance(at, str) or not at:
        return None, f, f"{f} has no frozen_at: not a round's frozen input"
    try:
        when = datetime.datetime.fromisoformat(at.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=datetime.timezone.utc)
        age = ((now or datetime.datetime.now(datetime.timezone.utc)) - when).total_seconds() / 86400
    except ValueError:
        return None, f, f"{f} has no frozen_at: not a round's frozen input"
    if age >= FROZEN_MAX_DAYS:
        return None, f, f"{f} was frozen {at} ({int(age)} days ago): not this round's input"
    slug = origin_slug(repo)
    hit = [x for x in forks if isinstance(x, dict) and slug and str(x.get("repo", "")).lower() == slug.lower()]
    if not hit:
        return None, f, f"{f} has no entry for this fork ({slug or 'no GitHub origin remote'})"
    return dict(hit[0], _issue=data.get("issue")), f, None


def untaken_ok(repo, a, rec, compared=True):
    """-> (note, None) when the round's frozen input left the authors' drift out on purpose, else (None, why not / None).
    take_authors false: every commit of theirs waits (the plan did not take them). take_authors true: only what came
    after the plan's upstream_commit, once the checkout carries that commit (pushed after the plan: next week)."""
    e, f, why = frozen_entry(repo, getattr(a, "untaken_ok", None))
    if e is None:
        return None, why
    src = f"the frozen plan {f.as_posix()}" + (f" (plan issue #{e['_issue']})" if e.get("_issue") else "")
    if e.get("take_authors") is False:
        return f"left out on purpose by {src}: take_authors false for {e.get('repo')}", None
    uc = e.get("upstream_commit")
    if not compared:
        # the plan takes the authors' code: only commits after its upstream_commit may wait, and that can be told only
        # when their branch could be read
        return None, (f"{src} takes the authors' code for {e.get('repo')}, and their branch could not be compared: "
                      "make it readable (the remote, a fetch) first")
    if uc and _git(repo, "merge-base", "--is-ancestor", uc, "HEAD")[0] == 0:
        return (f"pushed after {src} saw {uc[:12]} (taken): waits for next week's plan", None)
    return None, (f"{src} takes the authors' code for {e.get('repo')} and this checkout does not carry its "
                  f"upstream_commit {str(uc or '?')[:12]} yet: merge it first")


def drift_stop(repo, a, props_file, drift_file, fetch=True):
    """The R4 check for a run to the end on a version branch: None, or the stop for an authors' branch (or a recorded
    library release) that moved since the buildout's freeze (props_file: the branch's .port/<t>/branch.properties;
    drift_file: its DRIFT.md). Prints what it did."""
    repo = pathlib.Path(repo)
    bp = pathlib.Path(props_file)
    if not bp.is_file():
        return None
    props = read_props(bp)
    up = getattr(a, "upstream", None)
    if not props.get("upstream_commit") and up:
        rec, why = freeze_upstream(repo, up, getattr(a, "port_release", []), False, props_file)
        print(f"fork-port: upstream: " + (f"froze the buildout at {', '.join(f'{k}={v}' for k, v in rec.items())} "
                                          f"({bp.relative_to(repo).as_posix()})" if rec else f"NOT frozen: {why}"))
        props = read_props(bp)
    ref, rec = props.get("upstream"), props.get("upstream_commit")
    port = a.base.split("/", 1)[1] if "/" in a.base else a.base
    kc = (f"keep-current for this fork first (Claude Code: \"keep this fork current\"; command line: python3 "
          f"tools/port-sync.py --repo {repo} --port {port} --upstream {ref or '<remote>/<branch>'} --dry-run, then the "
          f"keep-current skill), then fork-branches.py freeze --repo {repo} --target {a.target}")
    reasons, upstream_reasons, compared = [], [], True
    if not rec:
        print(f"fork-port: upstream: WARNING no authors' commit recorded for this buildout ({bp.relative_to(repo).as_posix()} "
              "upstream/upstream_commit): an upstream that moves mid-ladder goes unseen -- "
              + f"fork-branches.py freeze --repo {repo} --target {a.target} --upstream <remote>/<branch>")
    else:
        st = upstream_drift(repo, ref, rec, fetch=fetch)
        if st.get("fetch_error"):
            print(f"fork-port: upstream: could not fetch {ref} ({st['fetch_error']}): comparing with the last fetch")
        # the authors moved since the freeze (rec..tip), not "HEAD lacks commits of the branch" (HEAD..tip): a freeze
        # records what the buildout started from, which HEAD may never have carried in full
        since = st.get("since") if st.get("since") is not None else st.get("behind")
        if st.get("error"):
            compared = False
            # a freeze that cannot be compared is a stop, not a warning: deleting the remote must never get a run
            # past the guard (the frozen plan's decision does, below)
            upstream_reasons.append(f"cannot compare with the authors' branch: {st['error']}" + (add_remote_hint(repo, ref) or ""))
        elif since and st.get("behind"):
            upstream_reasons.append(f"the authors' {ref} has {since} commit(s) since the freeze that this checkout does not carry "
                                    f"(buildout frozen at {rec[:12]}, {ref} now at {st['tip'][:12]}"
                                    + ("" if st["fetched"] else "; not fetched now, compared with the last fetch") + ")")
        elif since:
            print(f"fork-port: upstream: the checkout carries {ref} at {st['tip'][:12]}, newer than the freeze {rec[:12]} "
                  f"(keep-current merged it): record it with fork-branches.py freeze --repo {repo} --target {a.target}")
        elif st.get("behind"):
            print(f"fork-port: upstream: note: {ref} has {st['behind']} commit(s) this checkout does not carry, none of "
                  f"them after the freeze {rec[:12]}: not a stop")
    day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    df = pathlib.Path(drift_file)
    if upstream_reasons and not getattr(a, "accept_upstream_drift", False):
        note, why_not = untaken_ok(repo, a, rec, compared)
        if note:
            with open(df, "a", encoding="utf-8") as fh:
                fh.write(f"- {day} {a.target}: {note}: {'; '.join(upstream_reasons)}\n")
            print(f"fork-port: upstream: drift not taken on purpose ({note}; recorded in "
                  f"{df.relative_to(repo).as_posix()}): {'; '.join(upstream_reasons)}")
            upstream_reasons = []
        elif why_not:
            print(f"fork-port: upstream: the round's frozen input does not cover this drift: {why_not}")
    reasons += upstream_reasons
    for lib in getattr(a, "port_release", []):     # a library release is compared whether or not an upstream is frozen
        name, _, tag = lib.partition("@")
        had = props.get(f"library.{name}")
        if had and tag and had != tag:
            reasons.append(f"library {name}: this run resolves against {tag}, the buildout was frozen at {had}")
    if not reasons:
        return None
    if getattr(a, "accept_upstream_drift", False):
        with open(df, "a", encoding="utf-8") as fh:
            fh.write(f"- {day} {a.target}: accepted with --accept-upstream-drift: {'; '.join(reasons)}\n")
        print(f"fork-port: upstream: drift ACCEPTED (--accept-upstream-drift, recorded in {df.relative_to(repo).as_posix()}): {'; '.join(reasons)}")
        return None
    return {"stop": "upstream moved since the buildout started: " + "; ".join(reasons), "code": DRIFT_EXIT,
            "detail": reasons, "choices": [f"bring it in: {kc}",
                                           "a weekly round whose frozen plan does not take the authors' code: the same command "
                                           "with --untaken-ok round/frozen.json (recorded in DRIFT.md)",
                                           "or continue knowingly on the frozen code: the same command with --accept-upstream-drift "
                                           f"(recorded in {df.relative_to(repo).as_posix()})"]}


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
    the same file in a version this one stands on (parents: their .port/<v>/ dirs, nearest first) takes that review, marked
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
    prior = {}
    for pd in parents:
        try:
            for e in json.loads((pd / "STUBS.json").read_text(encoding="utf-8")) if (pd / "STUBS.json").is_file() else []:
                if e.get("reviewed"):
                    prior.setdefault(e["file"], (pd.name, e))
        except ValueError:
            continue
    for s in found:
        e = by.get(s["file"])
        if e is None or s["why"] not in e.get("why", ""):
            why = s["why"] if e is None else e["why"] + "; " + s["why"]
            src = prior.get(s["file"])
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


ERRORS_PER_DOLLAR = 100      # measured hops cleared 76-144 errors per dollar of workers (issue #49); the guard's default


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


DEPS_CHOICE = ("read the layer-deps.py lines above (the setup log in the run's work directory has them all), fix the pin "
               "or the release it names, and resume from setup")


def stop_block(stop):
    lines = [f"STOPPED at {stop['stage']}: {stop['left']} (spent ${stop.get('spent', 0)} here)."]
    lines += [f"  {d}" for d in stop.get("detail", [])]
    lines.append(f"Residue: {stop.get('residue', '(none)')}")
    lines.append("Choices (ask the person; do not pick one silently):")
    lines += [f"  - {c}" for c in stop.get("choices") or ["read the detail above and the residue file; resume with "
                                                          "--from-stage <stage> once it is fixed"]]
    return "\n".join(lines)


# A red gate that is the MACHINE's, not the code's (issue #113): tools/gate-loop.py's `killed` (the JVM was SIGKILLed with no
# verdict, usually the OOM killer) and its pre-Gate-C free-memory guard. Re-run when memory is free; never a fixer's job.
ENVIRONMENTAL = re.compile(r"JVM killed \(exit \d+|not enough free memory \(")


# Issue #122: a run's own Gradle invocations before Gate C (stage, measure, the loop's compiles, the audit's
# classpath, ci-gates' builds) left resident daemons and workers (measured: ~2.3 GB) and Gate C's free-memory guard then
# refused. Every Gradle this run starts runs daemon-less: each exits when its build ends, so nothing the run started is
# resident when the client needs the memory. Not `gradlew --stop`: that stops other checkouts' daemons too (#113).
NO_DAEMON = "-Dorg.gradle.daemon=false"


def gradle_env(env):
    """-> the environment for the run's subprocesses: `env` with NO_DAEMON in GRADLE_OPTS (unless GRADLE_OPTS already
    sets org.gradle.daemon: the person's choice stands)."""
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


RESIDUE_ERRORS_SHOWN = 25


def compile_error_lines(log, root=None, cap=RESIDUE_ERRORS_SHOWN):
    """The compile errors of a measure log as `file:line: message`, unique (Gradle echoes javac's output; a capped
    compile's uncapped recount follows RECOUNT_MARK, and only that part counts then), paths relative to `root` (the
    checkout), the first `cap` and a last line saying how many more. [] when the log has none or is missing."""
    p = pathlib.Path(log)
    if not p.is_file():
        return []
    text = p.read_text(encoding="utf-8", errors="replace")
    if RECOUNT_MARK in text:
        text = text.split(RECOUNT_MARK, 1)[1]
    # the log may name the checkout as given or resolved (a Windows 8.3 short temp path resolves to the long one)
    pres = sorted({str(q).replace("\\", "/").rstrip("/") + "/" for q in (pathlib.Path(root), pathlib.Path(root).resolve())},
                  key=len, reverse=True) if root else []
    fold = (lambda x: x.lower()) if os.name == "nt" else (lambda x: x)
    seen, out = set(), []
    for ln in text.splitlines():
        ln = ln.replace("\\", "/")
        m = re.match(r"^(.+?\.java):(\d+): error: (.+?)\s*$", ln.strip())
        if not m:
            continue
        f = m.group(1)
        for pre in pres:
            if fold(f).startswith(fold(pre)):
                f = f[len(pre):]
                break
        key = (f, m.group(2), m.group(3))
        if key not in seen:
            seen.add(key)
            out.append(f"{f}:{m.group(2)}: {m.group(3)}")
    return out[:cap] + ([f"... and {len(out) - cap} more in {p.name}"] if len(out) > cap else [])


def residue_report(stage, left, errors_view, esc, audit, gates, resume, environment=None, compile_errors=None):
    """RESIDUE.md and its JSON twin. Bounded by construction: groups and the top files, never the raw log."""
    md = [f"# Residue: {stage}", "", f"Stopped at **{stage}**: {left}.", ""]
    if compile_errors:
        md += [f"## The compile errors (file:line: message, from the measure log; the first {RESIDUE_ERRORS_SHOWN})", "", "```"]
        md += compile_errors + ["```", ""]
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
    if compile_errors:
        data["compile_errors"] = compile_errors
    return "\n".join(md), data


# ---------------------------------------------------------------- a version branch (tools/fork-branches.py)
#
# Version t is the branch neoforge-<t>, a plain single-version build at Minecraft t, with its records in .port/<t>/.
# The stages run on the checked-out branch's own tree: the compile loop edits src/ (file-loop.py), the generated class
# moves are applied to the source as rows and kept in .port/<t>/moves.generated.tsv, the gates are ci-gates.py --mc <t>
# (its branch mode), and the done list is tools/fork-branches.py target_done(). Nothing here runs for a single-version
# fork (no version branch).


def fork_branches():
    return load("fork_branches", "fork-branches.py")


# ---------------------------------------------------------------- running things


class Run:
    """One fork-port.py run on version branch t (its checkout, its .port/<t>/ records)."""

    def __init__(self, a):
        self.a = a
        self.repo = pathlib.Path(a.repo).resolve()
        self.t = a.target
        self.fb = fork_branches()
        self.tdir = self.repo / ".port" / self.t
        self.work = self.repo / "build" / f"mc{self.t}" / "fork-port"
        self.summary = {"repo": str(self.repo), "target": self.t, "stages": {},
                        "started": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")}
        self.jpath = pathlib.Path(a.json) if a.json else self.work / "fork-port.json"
        self.py = sys.executable
        self.env = gradle_env(os.environ)

    def say(self, stage, msg):
        print(f"fork-port: {stage}: {msg}", flush=True)

    def sh(self, cmd, log=None, cwd=None):
        out = open(log, "w", encoding="utf-8") if log else subprocess.PIPE
        r = subprocess.run(cmd, cwd=cwd or self.repo, stdout=out, stderr=subprocess.STDOUT, text=True,
                           encoding="utf-8", errors="replace", env=self.env)
        if log:
            out.close()
        return r

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

    def resume_cmd(self, stage):
        a = self.a
        # a loop to resume is a run that should spend: after a $0 gate re-run stopped at its spend guard, the command
        # the person is told to raise names the porting default, not --budget 0.0 (review S4)
        budget = a.budget if a.budget or stage != "loop" else default_budget(None, None)
        return (f"python3 tools/fork-port.py --repo {a.repo} --target {self.t} --from-stage {stage} "
                f"--budget {budget:g} --gates {a.gates}" + "".join(f" --port-checkout {c}" for c in getattr(a, "port_checkout", []))
                + "".join(f" --port-release {c}" for c in getattr(a, "port_release", []))
                + (" --allow-undone-library" if getattr(a, "allow_undone_library", False) else "")
                + (f" --untaken-ok {a.untaken_ok}" if getattr(a, "untaken_ok", None) else ""))

    def save(self):
        self.jpath.parent.mkdir(parents=True, exist_ok=True)
        self.jpath.write_text(json.dumps(self.summary, indent=1), encoding="utf-8")

    def library_stop(self, r):
        """layer-deps.py exit 18: a sibling library's target is not done (R3, library first). -> the stop, code 18."""
        stop = next((ln for ln in (r.stdout or "").splitlines() if "STOP" in ln), "a sibling library's target is not done")
        return {"stop": stop.replace("layer-deps: STOP ", "")[:600], "code": UNDONE_EXIT,
                "choices": ["finish and release the library's target first (its fork-port.py run to done, then its "
                            "layer-release), then resume with --port-release owner/repo@TAG",
                            "development only, against a --port-checkout: the same command with --allow-undone-library "
                            f"(recorded in DEPS.md; {self.t} is then not done until it is re-resolved with --port-release)"]}

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
        gap = self.shared_gap()
        if gap:
            self.say("measure", gap)
        return {"done": f"{n} error(s) across {len(tasks)} compile task(s) (starting count)", "errors": n, "log": str(log), "this_run": True}

    def error_view(self, log):
        cs = load("compile_summary", "compile-summary.py")
        text = pathlib.Path(log).read_text(encoding="utf-8", errors="replace")
        code, why = cs.validate(log)
        if code != 0:
            return [f"NOT A COUNT: {w}" for w in why[:3]], set()
        s = cs.summarise(text, cs.rb.signatures())
        return cs.render(s, None, 8, 8), set(s["files"])

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
        summ.unlink(missing_ok=True)      # an earlier run's summary must never stand for this run's (GATES.md, GATES-RED.md)
        cmd = [self.py, str(ROOT / "tools/ci-gates.py"), "--repo", str(self.repo), "--modid", modid, "--base",
               self.a.base, "--mc", self.t, "--summary", str(summ), "--work-dir", str(self.work / "gates")]
        if self.a.gates == "ab":
            cmd += ["--gatec", ""]
        if self.shipped_expected():    # the fork lists what its shipped jar must log
            cmd += ["--shipped"]
        r = self.sh(cmd, self.work / "ci-gates.log")
        lines = (self.work / "ci-gates.log").read_text(encoding="utf-8", errors="replace").splitlines()
        rows = [re.sub(r"^\[ci-gates\] ", "", l) for l in lines if l.startswith("[ci-gates]") and ": " in l]
        failed = [l for l in rows if re.search(r": FAIL\b", l)]
        passed = [l for l in rows if re.search(r": PASS\b", l)]
        if r.returncode != 0 and self.a.gates == "full":
            # a full run that is not green is recorded on its own (GATES-RED.md), never over the last green record:
            # GATES.md stays as it was, and while GATES-RED.md is there `--gates auto` runs full and the target is not
            # done (an older green record must not report done on a client that just failed)
            self.record_red(summ, failed)
        if r.returncode == 0:
            lost, prev = self.lost_logs()
            if lost:
                msg = "; ".join(f"the mod wrote {f} on {prev} and not on {self.t}: a subsystem stopped running" for f in lost)
                if self.a.gates == "full":
                    self.record_red(summ, [f"log file {f} written on {prev}, absent on {self.t}" for f in lost])
                return {"stop": f"gates green but {msg}", "code": 11, "gates": [f"log file {f} (written on {prev}, absent on {self.t})" for f in lost],
                        "detail": [msg[:240]],
                        "choices": ["find what writes that file (grep the source for its name), and why it no longer runs on this target "
                                    "(CATALOG X47: a registration the new loader ignores is the usual cause)",
                                    "if the file is gone on purpose, list it in .port/%s/logs-known-absent.txt with the reason" % self.t]}
            note = f"; log files match {prev}" if prev else ""
            if self.a.gates == "full" and summ.is_file():
                # the record `--gates auto` reads: this version had its real-client run
                (self.tdir / "GATES.md").write_text(summ.read_text(encoding="utf-8"), encoding="utf-8")
                (self.tdir / RED_GATES).unlink(missing_ok=True)
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

    def record_red(self, summ, failed):
        """Write the red full gate run to GATES-RED.md: the commit tested, when, and its summary (or the failed rows)."""
        head = _git(self.repo, "rev-parse", "--short", "HEAD")[1].strip() or "?"
        when = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
        body = summ.read_text(encoding="utf-8", errors="replace") if summ.is_file() else \
            "".join(f"- {l}\n" for l in failed) or "- (no gate rows: read the run's ci-gates.log)\n"
        self.tdir.mkdir(parents=True, exist_ok=True)
        (self.tdir / RED_GATES).write_text(
            f"Red full gate run at `{head}` for Minecraft {self.t} ({when}). GATES.md, the last green record, is kept as it "
            "was; while this file is here the target is not done and `--gates auto` runs the full gates. A green full run "
            "deletes it.\n\n" + body, encoding="utf-8")

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
                        "code": 15 if r.returncode == 15 else 2, "choices": [DEPS_CHOICE]}
            did.append("layer-deps: " + ((r.stdout or "").strip().splitlines() or [""])[-1].split(": ", 2)[-1])
        n = self.data_forms()
        if n:
            did.append(f"{n} resource file(s) written in {self.t}'s data forms")
        return {"done": ", ".join(did) or "on its branch"}

    def data_forms(self):
        """The layer's data conversions, written into the branch's own src/main/resources (convert_data)."""
        props = self.fb.read_file(self.tdir / "branch.properties")
        deps = self.fb.read_file(self.tdir / "DEPS.md") if (self.tdir / "DEPS.md").is_file() else ""
        return len(convert_data(self.repo, self.t, props, deps, layout=False)[0])

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
        work, why = fb.stage_checkout(self.repo, pv)
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
        # the files the rows alone make now (before this version's new moves): re-made whole below, so they stay
        # mechanical for the cascade; every other file gets the moves in its code only, its comments untouched
        try:
            mech = {str(self.repo / p) for p in fb.mechanical_files(self.repo, self.repo, self.t, "HEAD",
                                                                    fb.root_files(self.repo, self.repo, True))}
        except Exception:      # noqa: BLE001 -- no port commit to compare with: every file is treated as changed by hand
            mech = set()
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
        changed = fb.apply_moves_to_files(sorted(self.repo.glob("src/*/java/**/*.java")), fb.rules_of(text), mech)
        self.sh([self.py, str(ROOT / "tools/fix-access-transformer.py"), "--work", str(self.repo), "--class-map", str(raw)])
        cut = ""
        if fb.cutout_crossing(self.t, fb.cutout_below(p)):
            # the 26.1.2 entityCutout swap (CATALOG V60), checked and listed (file:line) before any compile error is shown
            # to a model -- never renamed here: this stage runs on every fork-port.py run, on a branch whose own lines may
            # be deliberate (add-target and the cascade's merge rename what they bring; cutout-check --fix renames the rest)
            pref = fb.resolve_branch(self.repo, p.get("parent") or fb.branch_name(pv))
            pref = pref and (fb.git(self.repo, "merge-base", "HEAD", pref).stdout.strip() or pref)
            cx = fb.cutout_tree(self.repo, pref, fix=False)
            fb.cutout_report(cx, fb.branch_name(self.t), False, print)
            n_dec = len(cx["decide"]) + len(cx["fresh"])
            cut = (f"; entityCutout swap: {len(cx['fixed'])} call(s) to rename (fork-branches.py cutout-check --branch "
                   f"{fb.branch_name(self.t)} --fix), {n_dec} to decide: " + "; ".join(fb.cutout_lines(cx, False))[:1500]
                   if cx["fixed"] or n_dec else "")
        # the converters whose change lands in this hop (mechanical-hop.py LADDER), after the rows and the moves
        conv, steps, adv, stop = self.hop_converters(pv)
        if stop:
            return stop
        return {"done": f"{kept} unambiguous move(s) {pv}->{self.t} -> .port/{self.t}/moves.generated.tsv"
                        f"{f' ({dup} already in a table)' if dup else ''}, applied to {changed} source file(s){cut}{conv}",
                "moves": kept, **({"converters": [s["summary"] for s in steps], "advisories": adv} if steps or adv else {})}

    def hop_converters(self, pv, run=None):
        """branch_converters for this hop -> (line, steps, advisories, None), or (.., a stop) when a converter crashed:
        a stop with what it printed, never a refusal and never a traceback out of the stage."""
        try:
            return (*branch_converters(self.repo, pv, self.t, self.work, run=run), None)
        except RuntimeError as e:
            return "", [], [], {"stop": f"a converter crashed on the {pv}->{self.t} hop: {str(e)[:400]}", "code": 10,
                                "detail": [str(e)[:600]],
                                "choices": ["fix the converter (tools/convert-*.py, with a self-check case for that input), then "
                                            "resume with " + self.resume_cmd("moves")]}

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
            errs = compile_error_lines(self.work / "measure.log", self.repo)
            return {"stop": "spend guard: " + why, "code": 10, "errors": start, "this_run": True,
                    "detail": [why] + errs[:5], "compile_errors": errs,
                    "choices": [f"raise it on purpose: {self.resume_cmd('loop')} --confirm-spend (or --max-errors N / a larger --budget)",
                                "or fix the listed errors by hand on the branch (RESIDUE.md lists them), then resume"]}
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
        """Gradle's own resolved compile classpath of the branch build (tools/qtc-init.gradle), through the branch's
        plain ./gradlew. Never rebuilt by hand (CATALOG X25b-ii)."""
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

    def shared_gap(self):
        """A version branch has no shared_chain: its shared tables are the `# ---- shared <v>` sections of
        .port/<t>/shared-renames.tsv, one per library version above the base up to t (fork-branches.py add-target /
        sync-shared); the gap is a library version with a table that the branch has no section of."""
        return branch_shared_gap(self.repo, self.t, self.props(), self.fb)


def branch_converters(repo, frm, to, work=None, run=None):
    """A version-branch hop's converters: tools/mechanical-hop.py run_ladder over the branch's source sets, the LADDER
    rows whose change lands in frm -> to (each idempotent, so a resumed run converts nothing twice). Each one's output
    goes to <work>/convert-<label>.out. Only Run.s_moves calls this: a single-version fork never reaches it. Each run APPENDS its output, under a dated header, so a resumed run keeps the earlier one. A converter
    that crashes raises RuntimeError (s_moves turns it into a stop). -> ("; converters ..." for the stage line -- each
    one's REFUSED and NOTE counts in it -- or "", steps, advisories)."""
    mh = load("mechanical_hop_fp", "mechanical-hop.py")
    repo = pathlib.Path(repo)
    dirs = sorted(p for p in repo.glob("src/*/java") if p.is_dir())
    steps, adv = mh.run_ladder(repo, dirs, frm, to, say=lambda s: None, **({"run": run} if run else {}))
    if work:
        pathlib.Path(work).mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for s in steps:
            with open(pathlib.Path(work) / f"convert-{s['step']}.out", "a", encoding="utf-8", newline="\n") as f:
                f.write(f"# ---- {stamp} {frm}->{to}\n{s['output']}\n")
    if not steps and not adv:
        return "", steps, adv
    line = "; converters landing in " + f"{frm}->{to}: " + ("; ".join(s["summary"] for s in steps) or "none run")
    if adv:
        line += "; ADVISORY " + " / ".join(adv)
    return line, steps, adv


def branch_shared_gap(repo, t, props, fb, shared_root=None, stable=None):
    """The version-branch check behind Run.shared_gap: a warning naming the stable shared library versions in
    (base, t] that have a renames.tsv and no section in .port/<t>/shared-renames.tsv (sync-shared's span), or None."""
    base = props.get("base_minecraft_version") or "1.21.1"
    shared_root = pathlib.Path(shared_root) if shared_root else fb.shared_dir()
    if stable is None:
        stable = [l["mc"] for l in fb.versions_table()["lines"] if l.get("stable")]
    have = {k.split(" ", 1)[1] for k in fb.shared_sections(fb.read_file(pathlib.Path(repo) / ".port" / t / "shared-renames.tsv"))}
    vs = [v for v in stable if (shared_root / v / "renames.tsv").is_file()]
    miss = sorted((v for v in vs if mc_key(base) < mc_key(v) <= mc_key(t) and v not in have), key=mc_key)
    if not miss:
        return None
    return (f"WARNING .port/{t}/shared-renames.tsv has no section for the shared librar{'y' if len(miss) == 1 else 'ies'} "
            f"{', '.join(miss)}: their rows are NOT applied (python3 tools/fork-branches.py sync-shared --repo {repo} "
            f"--target {t}, then review the change)")


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


RECHECK_STAGES = ("measure", "audit", "gates", "cost")


def default_budget(budget, from_stage):
    """--budget when none is given: $15 for a run that ports (from the start, setup, stage, moves, or the loop asked for
    by name), $0 for a re-run --from-stage measure/audit/gates/cost -- a gate re-run on a ported tree. With a budget such
    a re-run that met a compile error started the file loop, whose workers and its 0-error scan and probe rounds
    rewrote files outside the error (weekly round 2026-10-21); with none it stops red at the spend guard, the tree as it
    was. A fixer passes its budget explicitly (every resume command fork-port prints names one)."""
    if budget is not None:
        return budget
    return 0.0 if from_stage in RECHECK_STAGES else 15.0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo"); ap.add_argument("--target")
    ap.add_argument("--budget", type=float, help="dollars the compile loop's workers may spend (default 15; 0 for a "
                    "re-run --from-stage measure, audit, gates or cost: a gate re-run fixes nothing unless given a budget)")
    ap.add_argument("--gates", default="auto", choices=["auto", "none", "ab", "full"],
                    help="auto (default): full -- Gate C, a real client, included -- until the target's GATES.md records a "
                         "green Gate C, then ab. A target gets one real-client run on its initial port; CI keeps its own policy")
    ap.add_argument("--json"); ap.add_argument("--from-stage"); ap.add_argument("--modid")
    ap.add_argument("--to-stage", help="stop after this stage (default: run to the end)")
    ap.add_argument("--base", default="origin/neoforge-1.21.1", help="the authors' ref, for ci-gates.py --base")
    ap.add_argument("--max-errors", type=int, help="the loop refuses to start above this many errors (default: 100 per dollar of --budget)")
    ap.add_argument("--confirm-spend", action="store_true", help="start the loop although the error count exceeds the spend guard")
    ap.add_argument("--port-checkout", action="append", default=[],
                    help="owner/repo=DIR: a sibling fork this one depends on; its jar for this version comes from DIR "
                         "(tools/layer-deps.py)")
    ap.add_argument("--port-release", action="append", default=[],
                    help="owner/repo@TAG: a sibling fork's release holding its jar for this version; pinned (url + sha256) "
                         "in .port/<t>/branch.properties for the version branch's CI (tools/layer-deps.py)")
    ap.add_argument("--allow-undone-parent", action="store_true",
                    help="development only: add a NEW version although the version it stands on is not done (recorded in its "
                         "branch.properties as allowed_undone_parent; the new version is then not done until its parent is)")
    ap.add_argument("--allow-undone-library", action="store_true",
                    help="development only: resolve against a sibling library's --port-checkout whose version is not done "
                         "(passed to layer-deps.py resolve, recorded in DEPS.md and the summary; the version is then not done "
                         "until it is re-resolved with --port-release)")
    ap.add_argument("--upstream", help="<remote>/<branch>: the authors' branch, recorded with the commit the buildout starts "
                                       "from in .port/<t>/branch.properties when no freeze is recorded there")
    ap.add_argument("--accept-upstream-drift", action="store_true",
                    help="continue although the authors' branch (or a recorded library release) moved since the buildout's "
                         "freeze (exit 19 otherwise); recorded in .port/<t>/DRIFT.md")
    ap.add_argument("--untaken-ok", metavar="FROZEN_JSON",
                    help="a weekly round's frozen input (known-issues.py read-plan --out): the authors' commits it leaves "
                         "out on purpose (take_authors false, or pushed after the plan) do not stop the run; recorded in "
                         "DRIFT.md (default: round/frozen.json in the working directory, when it exists)")
    ap.add_argument("--chain", metavar="FROM..TO",
                    help="the sweep: tools/fork-branches.py cascade FROM..TO (merge upward, then the no-model gates per "
                         "version branch)")
    ap.add_argument("--check-done", action="store_true",
                    help="only say whether --target is done (tools/fork-branches.py target_done): exit 0, or 18 with the "
                         "reasons; on the branch's own checkout it adds REVIEW.md's `?` rows for the reviewer (never a verdict)")
    ap.add_argument("--write-test-items", action="store_true",
                    help="only write --target's .port/<t>/TEST-ITEMS.md (its manual-test items, no model), on the branch's "
                         "checkout, holding the checkout lock; no stage or gate runs")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    a.budget = default_budget(a.budget, a.from_stage)
    if not a.repo or not (a.target or a.chain):
        ap.error("--repo and --target (or --chain) are required")
    if not pathlib.Path(a.repo).is_dir():
        print(f"fork-port: no such directory: {a.repo}"); return 2
    why = load("fork_kind", "fork-kind.py").refuse_layer(a.repo, "fork-port")
    if why:
        print(why); return 2
    if not fork_branches().version_branches(a.repo) and fork_branches().checkout_version(pathlib.Path(a.repo)) is None:
        # principle 2: nothing multi-version runs on a single-version fork -- it stops here, having written nothing
        print(f"fork-port: {a.repo} has no version branch: a single-version fork (one version; nothing to port or sweep "
              "here; its done list is tools/fork-branches.py check-done). Versions are added as version branches when "
              "the maintainer decides it (the fork-versions skill: fork-branches.py add-target).")
        return 2
    if a.chain:
        if a.target:
            ap.error("--chain takes --repo and no --target")
        # the sweep on version branches is a cascade: merge each branch into the next, no-model gates per branch
        print("fork-port: --chain on version branches is tools/fork-branches.py cascade (merge upward, then gates)")
        gates = {"auto": "ab", "none": "none"}.get(a.gates, a.gates)
        return fork_branches().cascade(a.repo, a.chain, gates, untaken_ok=a.untaken_ok)
    if not a.check_done and fork_branches().checkout_version(pathlib.Path(a.repo)) != a.target \
            and a.from_stage and a.from_stage != "setup":
        print(f"fork-port: --from-stage {a.from_stage} runs on {fork_branches().branch_name(a.target)}'s checkout: check it "
              "out first (or start from setup, which does)"); return 2
    if a.check_done:
        # the done list; on the branch's own checkout it also adds REVIEW.md's `?` rows (no verdict), off it nothing
        return fork_branches().check_done(a.repo, a.target, "fork-port")
    if a.write_test_items:
        return write_test_items_branch(a)
    try:
        skipped, todo = select_stages(a.from_stage, a.to_stage)
    except ValueError as e:
        print(f"fork-port: {e}"); return 2
    run = Run(a)
    os.environ["GRADLE_OPTS"] = run.env["GRADLE_OPTS"]     # the in-process helpers start Gradle too: daemon-less as well
    if a.gates == "auto":
        a.gates = gates_auto(run.tdir)
        print(f"fork-port: gates: auto -> {a.gates} ({'Gate C already recorded in GATES.md' if a.gates == 'ab' else 'no recorded Gate C pass yet: one real-client run on the initial port'})")
    cl = load("checkout_lock", "checkout-lock.py")
    lock = cl.CheckoutLock(run.repo, "fork-port.py", a.target)
    busy = lock.acquire()
    if busy:
        print(f"fork-port: {busy}")
        return 14
    if cl.LOCK_ENV in os.environ:
        # run.env was copied before the lock was taken: the children (ci-gates.py --mc, layer-deps.py) must see the
        # holder, or they refuse their own parent's checkout
        run.env[cl.LOCK_ENV] = os.environ[cl.LOCK_ENV]
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
    lock = load("checkout_lock", "checkout-lock.py").CheckoutLock(repo, "fork-port.py", a.target)
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
    # R4: a run to the end first checks the authors' branch against the buildout's freeze (a partial run is
    # keep-current's own measuring, after it merged the authors' code)
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
                                  environment=stop.get("environment"), compile_errors=stop.get("compile_errors"))
        rpath = run.tdir / "RESIDUE.md"
        if run.tdir.is_dir():
            put_residue(run.tdir, a.target, md)
        stop["residue"] = str(rpath.relative_to(run.repo)) if run.tdir.is_dir() else "(no target dir)"
        run.summary["residue"] = data
        run.summary["stop"] = {"stage": stop["stage"], "code": stop["code"], "left": stop["left"], "resume": resume}
        run.save()
        print(stop_block(stop))
        print(f"fork-port: summary in {run.jpath}")
        return stop["code"]
    if not partial:           # only a run to the end clears an earlier stop's report
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
    # a version-branch hop runs the converters whose change lands in it (mechanical-hop.py LADDER); nothing else does
    import inspect as _insp, types as _ty
    expect("self.hop_converters(" in _insp.getsource(Run.s_moves)
           and all("hop_converters" not in _insp.getsource(getattr(Run, m)) for m in ("s_setup", "s_stage", "s_measure", "s_loop",
                                                                                       "s_audit", "s_gates", "s_cost")),
           "only the moves stage of a version-branch hop runs the ladder's converters")
    with tempfile.TemporaryDirectory() as d:
        repo, work = pathlib.Path(d) / "r", pathlib.Path(d) / "w"
        j = repo / "src/main/java/com/example/mod"
        j.mkdir(parents=True)
        item = ("package com.example.mod;\n\npublic class Blade extends Item {\n    @Override\n    public void inventoryTick(ItemStack s, "
                "Level l, Entity e, int slot, boolean selected) {\n        if (selected && !l.isClientSide) e.igniteForSeconds(1);\n    }\n}\n")
        (j / "Blade.java").write_text(item, encoding="utf-8")
        line, steps, adv = branch_converters(repo, "1.21.5", "1.21.8", work)
        expect(line == "" and steps == [] and (j / "Blade.java").read_text(encoding="utf-8") == item,
               "a hop no converter's change lands in runs none and changes nothing")
        line, steps, adv = branch_converters(repo, "1.21.4", "1.21.5", work)
        now = (j / "Blade.java").read_text(encoding="utf-8")
        expect(steps and steps[0]["changed"] and "ServerLevel portedLevel" in now and "inventoryTick 1" in line and "1 NOTE" in line
               and (work / "convert-override-signatures.out").is_file() and adv == [],
               f"a hop over 1.21.5 runs convert-override-signatures, its NOTE counted, no gear advisory without gear: {line}")
        line2, steps2, _ = branch_converters(repo, "1.21.4", "1.21.5", work)
        outs = (work / "convert-override-signatures.out").read_text(encoding="utf-8")
        expect(not steps2[0]["changed"] and (j / "Blade.java").read_text(encoding="utf-8") == now and outs.count("# ---- ") == 2
               and "inventoryTick 1" in outs, "a re-run converts nothing twice and keeps the first run's output")
        (j / "Edge.java").write_text("package com.example.mod;\n\npublic class Edge extends SwordItem {}\n", encoding="utf-8")
        expect(any("gear-tiers" in x for x in branch_converters(repo, "1.21.4", "1.21.5", work)[2]),
               "a tree naming a 1.21.x gear class gets the gear-tiers advisory")
        fake = _ty.SimpleNamespace(repo=repo, t="1.21.5", work=work, resume_cmd=lambda s: f"--from-stage {s}")
        *_r, stop = Run.hop_converters(fake, "1.21.4", run=lambda *a: (1, "Traceback (most recent call last):\nboom"))
        expect(stop and stop["code"] == 10 and "crashed" in stop["stop"] and "--from-stage moves" in stop["choices"][0],
               f"a crashing converter stops the moves stage (exit 10, resume named), not passed off as a refusal: {stop}")
        *_r, stop = Run.hop_converters(fake, "1.21.4", run=lambda *a: (7, "died"))
        expect(stop and "exit 7" in stop["stop"], "an unexpected exit code is a stop too, not a traceback out of s_moves")
    # a re-run from measure (a gate re-run) has no compile-loop budget unless given one; a porting run keeps $15
    expect(default_budget(None, None) == 15.0 and default_budget(None, "setup") == 15.0 and default_budget(None, "loop") == 15.0
           and default_budget(None, "moves") == 15.0 and all(default_budget(None, x) == 0.0 for x in RECHECK_STAGES)
           and default_budget(7.5, "measure") == 7.5 and default_budget(0.0, None) == 0.0,
           "default_budget: $0 for a re-run from measure/audit/gates/cost, $15 otherwise, an explicit --budget kept")
    import types as _ty
    _ns = argparse.Namespace(repo="r", gates="full", budget=0.0)
    _rc = lambda st: Run.resume_cmd(_ty.SimpleNamespace(a=_ns, t="1.21.4"), st)
    expect("--budget 15 " in _rc("loop") and "--budget 0 " in _rc("measure")
           and "--budget 7.5 " in Run.resume_cmd(_ty.SimpleNamespace(a=argparse.Namespace(repo="r", gates="ab", budget=7.5),
                                                                     t="1.21.4"), "loop"),
           f"resume_cmd: after a $0 run, the loop to resume names the porting default: {_rc('loop')} / {_rc('measure')}")
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
    with tempfile.TemporaryDirectory() as d_:
        lg = pathlib.Path(d_) / "m.log"
        lg.write_text("> Task :compileJava\n/w/A.java:1: error: cannot find symbol\n1,000 errors\n> Task :compileJava FAILED\nBUILD FAILED\n\n"
                      + RECOUNT_MARK + "\nPORT_FULL_ERRORS compileJava: uncapped javac over 1 file(s)\n/w/A.java:1: error: cannot find symbol\n"
                      "/w/B.java:2: error: cannot find symbol\n2 errors\nPORT_FULL_ERRORS compileJava: javac exit 1\nBUILD SUCCESSFUL in 3s\n", encoding="utf-8")
        n_, _w = Run.count(None, lg)
        expect(n_ == 2, f"a recounted log is counted, not read as clean from the recount run's BUILD SUCCESSFUL: {n_}")
    with tempfile.TemporaryDirectory() as d_:
        lg = pathlib.Path(d_) / "measure.log"
        rows = "".join(f"{d_}/w/src/main/java/a/F{i}.java:{i}: error: cannot find symbol\n" for i in range(30))
        lg.write_text("> Task :compileJava\n" + rows + rows + "30 errors\n", encoding="utf-8")
        ce = compile_error_lines(lg, pathlib.Path(d_) / "w")
        md, data = residue_report("loop", "spend guard: 30 errors against a $0 budget", ["30 errors against a $0 budget"], None,
                                  [], [], "r", compile_errors=ce)
        expect(len(ce) == RESIDUE_ERRORS_SHOWN + 1 and ce[0] == "src/main/java/a/F0.java:0: cannot find symbol"
               and ce[-1] == "... and 5 more in measure.log" and "src/main/java/a/F24.java:24: cannot find symbol" in md
               and "## The compile errors (file:line: message" in md and data["compile_errors"] == ce
               and compile_error_lines(pathlib.Path(d_) / "none.log") == [],
               f"a spend-guard stop's RESIDUE.md lists the compile errors (file:line: message, unique, capped): {ce[:3]} {md[:400]}")
    with tempfile.TemporaryDirectory() as d_:
        fb_ = fork_branches()
        sh = pathlib.Path(d_) / "shared"
        for v in ("1.21.3", "1.21.4", "1.21.6"):
            (sh / v).mkdir(parents=True); (sh / v / "renames.tsv").write_text("a\tb\n", encoding="utf-8")
        (pathlib.Path(d_) / ".port/1.21.5").mkdir(parents=True)
        srt = pathlib.Path(d_) / ".port/1.21.5/shared-renames.tsv"
        srt.write_text("# ---- shared 1.21.3\na\tb\n", encoding="utf-8")
        st_ = ["1.21.3", "1.21.4", "1.21.5", "1.21.6"]
        gap = branch_shared_gap(d_, "1.21.5", {"base_minecraft_version": "1.21.1"}, fb_, sh, st_)
        srt.write_text("# ---- shared 1.21.4\na\tb\n# ---- shared 1.21.3\na\tb\n", encoding="utf-8")
        expect(gap and "1.21.4" in gap and "1.21.6" not in gap and "sync-shared" in gap and "shared_chain" not in gap
               and branch_shared_gap(d_, "1.21.5", {"base_minecraft_version": "1.21.1"}, fb_, sh, st_) is None,
               f"a version branch: no shared_chain warning; a missing shared-renames.tsv section is named (sync-shared): {gap}")
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
           {"event": "worker", "model": "opus", "files": [".port/1.21.5/java/m/A.java"], "usd": 1.0},
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
                    "residue": ".port/1.21.5/RESIDUE.md", "choices": ["a", "b"]})
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
            (repo / ".port/1.21.4").mkdir(parents=True)
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
    # ---- recipes: derived into the overlay, a hand overlay never touched, an authors' edit reaches a generated one
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d); rd = repo / "src/main/resources/data/examplemod/recipe"; rd.mkdir(parents=True)
        (rd / "a.json").write_text(json.dumps({"type": "minecraft:crafting_shapeless", "ingredients": [{"item": "minecraft:stick"}],
                                               "result": {"id": "examplemod:a"}}), encoding="utf-8")
        (rd / "b.json").write_text(json.dumps({"type": "minecraft:crafting_shapeless", "ingredients": [{"item": "minecraft:dirt"}],
                                               "result": {"id": "examplemod:b"}}), encoding="utf-8")
        td = repo / ".port/1.21.4"; hand = td / "resources/data/examplemod/recipe/b.json"
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
        td = repo / ".port/1.21.4"
        keep = td / "resources/assets/examplemod/items/special.json"; keep.parent.mkdir(parents=True)
        keep.write_text('{"model": {"type": "minecraft:special"}}', encoding="utf-8")
        n = layer_client_items(repo, td, "1.21.4")
        expect(n == 1 and (keep.parent / "sword.json").is_file() and "special" in keep.read_text(encoding="utf-8"),
               f"client items: the missing one written, the overlay's own kept ({n})")
        expect(layer_client_items(repo, td, "1.21.4") == 0, "client items: idempotent")
        expect(layer_client_items(repo, repo / ".port/1.21.1x", "1.21.1") == 0, "control: none before 1.21.2")
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
        td = repo / ".port/1.21.5"; td.mkdir(parents=True)
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
        t4 = repo / ".port/1.21.4"; t4.mkdir(parents=True)
        (t4 / "DEPS.md").write_text("| `curse.maven:geckolib-388172` | required | `1` | curse.maven:geckolib-388172:3 | "
                                    "CurseForge file 3 (geckolib-neoforge-1.21.4-4.8.5.jar) |\n", encoding="utf-8")
        expect(layer_geckolib_assets(repo, t4, "1.21.4") == 0 and not (t4 / "resources").exists(),
               "control: a GeckoLib 4.x target gets no 5.x copies")
        t0 = repo / ".port/1.21.3"; t0.mkdir(parents=True)
        expect(layer_geckolib_assets(repo, t0, "1.21.3") == 0, "control: a target that names no GeckoLib gets none")
        for line_, want in (("geckolib-neoforge-1.21.10-5.3-alpha-3.jar", 5), ("software.bernie.geckolib:geckolib-neoforge-1.21.1:4.7.1", 4),
                            ("prop.geckolib_version=5.2.2", 5)):
            (t0 / "target.properties").write_text(line_ + "\n", encoding="utf-8")
            expect(target_geckolib_major(t0) == want, f"geckolib version from {line_!r}: {target_geckolib_major(t0)}")
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
        repo = pathlib.Path(d); (repo / ".port/1.21.4").mkdir(parents=True)
        a_ = argparse.Namespace(repo=str(repo), target="1.21.4", json=None, budget=1.0, gates="ab", modid="m", base="x",
                                direct=False, from_stage="cost", to_stage=None, max_errors=None, confirm_spend=False, port_checkout=[])
        run = Run(a_); run.s_cost = lambda: {"skipped": "fake"}
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            rc_ab = drive(run, a_, [s_ for s_ in STAGES if s_ != "cost"], ["cost"])
            (repo / ".port/1.21.4/GATES.md").write_text("| Gate C -- real client, `launch` | PASS | ok |\n",
                                                          encoding="utf-8")
            run.done = lambda prepare=False: ["REVIEW.md: none"]; run.skeleton = lambda: 0
            run.test_items = lambda: (0, None); run.review_note = lambda added: "x"
            rc_c = drive(run, a_, [s_ for s_ in STAGES if s_ != "cost"], ["cost"])
            run.done = lambda prepare=False: []
            rc_rev = drive(run, a_, [s_ for s_ in STAGES if s_ != "cost"], ["cost"])
        # R1: Gate C green is not done while the done list is open (exit 18); with it closed, done (0). The done list
        # itself is tools/fork-branches.py target_done(), checked there.
        expect(rc_ab == 17 and rc_c == UNDONE_EXIT and rc_rev == 0,
               f"done needs a recorded Gate C and the done list: A/B only -> {rc_ab}, with Gate C and an open list -> {rc_c} "
               f"(want {UNDONE_EXIT}), with it closed -> {rc_rev}")
        # --from-stage cost over an older GATES.md with an unreviewed stub: not done (exit 10, the stub named)
        (repo / ".port/1.21.4/STUBS.json").write_text(json.dumps([{"file": "A.java", "why": "emptied f()",
                                                                                  "reviewed": None}]), encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc_stub = drive(run, a_, [s_ for s_ in STAGES if s_ != "cost"], ["cost"])
        expect(rc_stub == 10 and "A.java" in buf.getvalue(), f"a run resumed past the gates with an open stub: exit {rc_stub}")
        (repo / ".port/1.21.4/STUBS.json").unlink()
        # --gates none is not done, even with a Gate C on record; a partial run (--to-stage) still ends 0
        a_.gates = "none"
        with contextlib.redirect_stdout(io.StringIO()):
            rc_none = drive(run, a_, [], list(STAGES[-2:]))
            a_.to_stage = "audit"; run.s_audit = lambda: {"done": "fake"}
            rc_part = drive(run, a_, [], ["audit"])
        a_.to_stage, a_.gates = None, "ab"
        expect(rc_none == 17 and rc_part == 0, f"--gates none: exit {rc_none} (want 17); --to-stage audit: {rc_part} (want 0)")
    # ---- a full gate run that is not green is recorded apart (GATES-RED.md) and never removes or rewrites the last green
    # GATES.md (weekly-round dry run, 2026-10-18); while it is there `--gates auto` runs full again and the target is not
    # done; a green full run deletes it
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d); td = repo / ".port/1.21.4"; td.mkdir(parents=True)
        green_md = "Gates at `abc1234` for Minecraft 1.21.4\n\n| Gate C -- real client, `launch` | PASS | ok |\n"
        for gates, code, want_red in (("full", 1, True), ("ab", 1, True), ("full", 0, False)):
            if code:
                (td / "GATES.md").write_text(green_md, encoding="utf-8")
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
            run.work.mkdir(parents=True, exist_ok=True)
            (run.work / "ci-gates-summary.md").write_text("STALE summary of an earlier run\n", encoding="utf-8")
            run.s_gates()
            red = td / RED_GATES
            expect((td / "GATES.md").is_file() and red.is_file() == want_red
                   and (code == 0 or (td / "GATES.md").read_text(encoding="utf-8") == green_md)
                   and gates_auto(td) == ("full" if want_red else "ab")
                   and (not want_red or "Red full gate run" in red.read_text(encoding="utf-8"))
                   and "STALE" not in (td / "GATES.md").read_text(encoding="utf-8")
                   and (not red.is_file() or "STALE" not in red.read_text(encoding="utf-8")),
                   f"--gates {gates} exit {code}: GATES.md kept{' byte for byte' if code else ''}, "
                   f"GATES-RED.md {'written' if want_red else 'gone'}")
    # ---- issue #113: a Gate C client the OOM killer killed is environmental -- RESIDUE.md says re-run, no fixer
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d); td = repo / ".port/1.21.4"; td.mkdir(parents=True)
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
    # ---- issue #122: the run's own Gradle leaves no resident daemon before Gate C, and a refused
    # Gate C names what holds the memory
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d)
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
        expect("-Dorg.gradle.daemon=false" in opts and kw["env"].get("PATH") == os.environ.get("PATH"),
               f"a version branch's Gradle runs daemon-less (GRADLE_OPTS), with the rest of the environment: {opts!r}")
    if "gradle_env" in globals():
        e_ = gradle_env({"GRADLE_OPTS": "-Xmx1g -Dorg.gradle.daemon=true", "A": "1"})
        expect(e_["GRADLE_OPTS"] == "-Xmx1g -Dorg.gradle.daemon=true", "an explicit org.gradle.daemon in GRADLE_OPTS stands")
        expect(gradle_env({"GRADLE_OPTS": "-Xmx1g"})["GRADLE_OPTS"] == "-Xmx1g -Dorg.gradle.daemon=false"
               and gradle_env({})["GRADLE_OPTS"] == "-Dorg.gradle.daemon=false", "NO_DAEMON is appended to GRADLE_OPTS")
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
        repo = pathlib.Path(d) / "r"; (repo / ".port/1.21.4").mkdir(parents=True)
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
        (a / "STUBS.json").write_text(json.dumps([{"file": "src/main/java/X.java", "why": "removes 3 call(s)",
                                                   "reviewed": "same message API"}]), encoding="utf-8")
        got = record_stubs(b, [{"file": "src/main/java/X.java", "why": "removes 3 call(s)"},
                               {"file": "src/main/java/Y.java", "why": "removes 3 call(s)"}], parents=[a])
        expect(got[0]["reviewed"] == "(inherited from 1.21.5) same message API" and got[1]["reviewed"] is None,
               f"an identical signal inherits the parent's review, another file does not: {got}")
        got = record_stubs(mv / "1.21.10", [{"file": "src/main/java/X.java", "why": "removes 4 call(s)"}],
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
            f = repo / "src/main/java/m/LrMixin.java"; f.parent.mkdir(parents=True, exist_ok=True)
            if text is None:
                f.unlink(missing_ok=True)
            else:
                f.write_text(text, encoding="utf-8")
            return record_stubs(mv / t, [{"file": "src/main/java/m/LrMixin.java", "why": sig}],
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
        expect(target("1.21.11", None, ["1.21.3"])[0]["recheck"].endswith("to (none)"),
               "the file gone on the inheriting target: it hooks nothing, flagged")
    # the guard stops the loop stage before any worker runs
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d); (repo / ".port/1.21.4").mkdir(parents=True)
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
    # ---- one run per checkout: a live holder refuses a second run (exit 14, naming it); a dead one is taken over.
    # The checkout is a version branch's (gradle.properties' version with its .port/<v>/branch.properties); drive() is
    # stubbed, so no stage runs
    cl = load("checkout_lock", "checkout-lock.py")

    def vb_checkout(d):
        repo = pathlib.Path(d)
        (repo / ".port/1.21.4").mkdir(parents=True)
        (repo / ".port/1.21.4/branch.properties").write_text("minecraft_version=1.21.4\n", encoding="utf-8")
        (repo / "gradle.properties").write_text("minecraft_version=1.21.4\n", encoding="utf-8")
        return repo

    def run_main(argv, drive_=None):
        seen, real_drive, real_argv = {}, globals()["drive"], sys.argv
        def fake_drive(run, *_):
            seen["env"] = run.env
            seen["holder"] = run.env.get(cl.LOCK_ENV)
            return 0
        globals()["drive"] = drive_ or fake_drive
        sys.argv = ["fork-port.py", *argv]
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = main()
        finally:
            globals()["drive"], sys.argv = real_drive, real_argv
        return rc, buf.getvalue(), seen
    with tempfile.TemporaryDirectory() as d:
        repo = vb_checkout(d)
        holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            (repo / "build").mkdir()
            (repo / "build/.fork-port.lock").write_text(json.dumps({"pid": holder.pid, "tool": "fork-port.py", "target": "1.21.3",
                                                                    "started": "2026-01-01T00:00:00Z"}), encoding="utf-8")
            rc, out_, _s = run_main(["--repo", str(repo), "--target", "1.21.4"])
            expect(rc == 14 and str(holder.pid) in out_ and "one gate run per checkout" in out_,
                   f"a second run is refused with exit 14 naming the holder: rc={rc} {out_[:200]}")
            expect(json.loads((repo / "build/.fork-port.lock").read_text(encoding="utf-8"))["pid"] == holder.pid,
                   "the refused run leaves the holder's lock alone")
        finally:
            holder.kill(); holder.wait()
        rc, out_, _s = run_main(["--repo", str(repo), "--target", "1.21.4"], drive_=lambda *_: 2)
        expect(rc == 2 and not (repo / "build/.fork-port.lock").exists(),
               f"a dead holder's lock is taken over and released at the end: rc={rc}")
    # ---- the children see the lock holder: they run with run.env (GRADLE_OPTS without a daemon), copied before the
    # lock was taken
    with tempfile.TemporaryDirectory() as d:
        repo = vb_checkout(d)
        rc, _o, seen = run_main(["--repo", str(repo), "--target", "1.21.4", "--gates", "none"])
        expect(seen.get("holder") == str(os.getpid()) and "-Dorg.gradle.daemon=false" in seen["env"].get("GRADLE_OPTS", ""),
               f"a child sees this run as the lock holder, daemon-less: {seen.get('holder')}")
    ok = self_check_freeze() and ok
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def self_check_freeze():
    """R4 (the upstream freeze) on a version branch's .port/<t>/branch.properties, the PR #131 review's freeze findings,
    and the single-version path: no version branch, so fork-port stops at once and writes nothing."""
    import contextlib, io
    from subprocess import CompletedProcess
    ok = True

    def expect(c, msg):
        nonlocal ok
        if not c:
            ok = False; print("FAIL:", msg)

    def run_case(name, fn):
        try:
            fn()
        except Exception as e:      # noqa: BLE001 -- a missing function is a FAIL line, not a crash
            expect(False, f"{name}: {type(e).__name__}: {e}")

    def git_repo(d):
        pathlib.Path(d).mkdir(parents=True, exist_ok=True)
        g = lambda *x: subprocess.run(["git", "-C", str(d), *x], capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()
        g("init", "-q", "-b", "main"); g("config", "user.email", "a@b"); g("config", "user.name", "a")
        return g

    def w(p, text):
        p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text, encoding="utf-8")

    # ---- R4: the authors' commit the buildout started from; a moved upstream stops a run to the end (exit 19)
    with tempfile.TemporaryDirectory() as d:
        up, fork = pathlib.Path(d) / "authors", pathlib.Path(d) / "fork"
        gu = git_repo(up); w(up / "A.java", "a\n"); gu("add", "-A"); gu("commit", "-qm", "authors 1")
        subprocess.run(["git", "clone", "-q", "-o", "upstream", str(up), str(fork)], check=True, capture_output=True)
        gf = lambda *x: subprocess.run(["git", "-C", str(fork), *x], capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()
        gf("config", "user.email", "a@b"); gf("config", "user.name", "a")
        vbp, vdrift = fork / ".port/1.21.3/branch.properties", fork / ".port/1.21.3/DRIFT.md"
        w(vbp, "minecraft_version=1.21.3\nmv_package=com.example.mod.mv\n")
        gf("add", "-A"); gf("commit", "-qm", "version branch")
        a_ = argparse.Namespace(repo=str(fork), target="1.21.3", base="origin/neoforge-1.21.1", port_release=["owner/lib@v1"],
                                upstream="upstream/main", accept_upstream_drift=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            st = drift_stop(fork, a_, vbp, vdrift)
        a_.upstream = None
        bp = read_props(vbp)
        expect(st is None and bp.get("upstream") == "upstream/main" and bp.get("upstream_commit") == gu("rev-parse", "HEAD")
               and bp.get("library.owner/lib") == "v1" and bp.get("mv_package") == "com.example.mod.mv",
               f"--upstream with no freeze recorded freezes the authors' commit and each library release, other keys kept: {bp} {buf.getvalue()}")
        gf("commit", "-qam", "freeze")
        w(up / "A.java", "a2\n"); gu("commit", "-qam", "authors 2")
        with contextlib.redirect_stdout(io.StringIO()):
            st = drift_stop(fork, a_, vbp, vdrift)
        expect(st and st["code"] == DRIFT_EXIT and "1 commit(s)" in st["stop"]
               and any("port-sync.py" in c and "--upstream upstream/main" in c and "--port neoforge-1.21.1" in c for c in st["choices"])
               and any("--accept-upstream-drift (recorded in .port/1.21.3/DRIFT.md)" in c for c in st["choices"]),
               f"the authors pushed: exit 19 naming the keep-current command and the version's DRIFT.md: {st}")
        a_.accept_upstream_drift = True
        with contextlib.redirect_stdout(io.StringIO()):
            st = drift_stop(fork, a_, vbp, vdrift)
        expect(st is None and "accepted" in vdrift.read_text(encoding="utf-8"),
               "--accept-upstream-drift continues, recorded in DRIFT.md")
        vdrift.unlink()
        a_.accept_upstream_drift = False
        gf("merge", "-q", "--no-edit", "upstream/main")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            st = drift_stop(fork, a_, vbp, vdrift)
        expect(st is None and "newer than the freeze" in buf.getvalue()
               and f"fork-branches.py freeze --repo {fork} --target 1.21.3" in buf.getvalue(),
               f"after keep-current merged it: no stop, a re-freeze hint (fork-branches.py freeze): {buf.getvalue()}")
        # a freeze naming a remote the clone lacks says how to add it, fetch-only
        w(vbp, "minecraft_version=1.21.3\nupstream=authors/main\nupstream_commit=" + bp.get("upstream_commit", "") + "\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            st = drift_stop(fork, a_, fetch=False, props_file=vbp, drift_file=fork / ".port/1.21.3/DRIFT.md")
        said = buf.getvalue() + (st or {}).get("stop", "")
        expect("no `authors` remote" in said and "git remote add authors" in said
               and "git remote set-url --push authors DISABLED" in said,
               f"upstream=authors/... with no authors remote: how to add it, fetch-only: {said}")
        expect(add_remote_hint(fork, "upstream/main") is None, "an existing remote gets no hint")
        # the weekly round's dry run (2026-10-18): a missing remote is a stop, never the way past the guard; a round whose
        # frozen plan does not take the authors' code goes on, the drift recorded in DRIFT.md as left out by the plan
        with contextlib.redirect_stdout(io.StringIO()):
            st = drift_stop(fork, a_, fetch=False, props_file=vbp, drift_file=vdrift)
        expect(st and st["code"] == DRIFT_EXIT and "no `authors` remote" in st["stop"] and not vdrift.exists()
               and any("--untaken-ok" in c for c in st["choices"])
               and any("--accept-upstream-drift (recorded in .port/1.21.3/DRIFT.md)" in c for c in st["choices"])
               and not any(".multiversion/" in c for c in st["choices"]),
               f"a freeze naming a remote the clone lacks: exit 19 (removing the remote is no escape), nothing recorded, "
               f"the version's own DRIFT.md named: {st}")
        gf("remote", "add", "origin", "https://github.com/owner/examplemod.git")
        now = datetime.datetime.now(datetime.timezone.utc)
        frozen = pathlib.Path(d) / "frozen.json"

        def freeze_file(take, at=now, repo_="owner/examplemod", uc=None):
            frozen.write_text(json.dumps({"issue": 7, "frozen_at": at.isoformat(timespec="seconds"), "forks": [
                {"repo": repo_, "take_authors": take, "upstream_commit": uc}]}), encoding="utf-8")
        head_ = gf("rev-parse", "HEAD")
        for case, args_, want_stop in (("take_authors false", dict(take=False), False),
                                       ("another fork's entry", dict(take=False, repo_="owner/other"), True),
                                       ("last week's frozen input", dict(take=False, at=now - datetime.timedelta(days=30)), True),
                                       ("frozen exactly 7 days ago", dict(take=False, at=now - datetime.timedelta(days=7)), True),
                                       ("take_authors true, upstream_commit not carried", dict(take=True, uc="f" * 40), True),
                                       ("take_authors true, carried, but the branch cannot be compared",
                                        dict(take=True, uc=head_), True)):
            freeze_file(**args_)
            a_.untaken_ok = str(frozen)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                st = drift_stop(fork, a_, fetch=False, props_file=vbp, drift_file=vdrift)
            expect((st is not None) == want_stop and (st is None or st["code"] == DRIFT_EXIT),
                   f"--untaken-ok, {case}: {'a stop' if want_stop else 'goes on'}: {st} {buf.getvalue()}")
        frozen.write_text(json.dumps({"frozen_at": 20261018, "forks": [{"repo": "owner/examplemod", "take_authors": False}]}),
                          encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            st = drift_stop(fork, a_, fetch=False, props_file=vbp, drift_file=vdrift)
        expect(st and st["code"] == DRIFT_EXIT, f"a frozen_at that is not a string is no frozen input: {st}")
        freeze_file(False, at=now - datetime.timedelta(days=6, hours=23))
        with contextlib.redirect_stdout(io.StringIO()):
            st = drift_stop(fork, a_, fetch=False, props_file=vbp, drift_file=vdrift)
        expect(st is None, f"a frozen input under a week old is this round's: {st}")
        rec_ = vdrift.read_text(encoding="utf-8") if vdrift.exists() else ""
        expect(rec_.count("\n") == 2 and "left out on purpose by the frozen plan" in rec_ and "plan issue #7" in rec_,
               f"the accepted drift is recorded once in the version's DRIFT.md, naming the plan: {rec_!r}")
        # round/frozen.json in the working directory is read without the flag
        a_.untaken_ok = None
        freeze_file(False)
        (pathlib.Path(d) / "round").mkdir(exist_ok=True)
        shutil.copy(frozen, pathlib.Path(d) / "round/frozen.json")
        old_cwd = os.getcwd()
        try:
            os.chdir(d)
            with contextlib.redirect_stdout(io.StringIO()):
                st = drift_stop(fork, a_, fetch=False, props_file=vbp, drift_file=vdrift)
        finally:
            os.chdir(old_cwd)
        expect(st is None and vdrift.read_text(encoding="utf-8").count("\n") == 3,
               f"the round's round/frozen.json is read from the working directory: {st}")
        # a real authors' branch ahead of the freeze (the remote exists and is fetched): a plan that does not take their
        # code goes on; one that takes it goes on only for what came after the commit it took, once that is carried
        rbp = fork / ".port/1.21.4/branch.properties"
        rdrift = fork / ".port/1.21.4/DRIFT.md"
        w(rbp, f"minecraft_version=1.21.4\nupstream=upstream/main\nupstream_commit={gf('rev-parse', 'upstream/main')}\n")
        w(up / "A.java", "a3\n"); gu("commit", "-qam", "authors 3")
        a_.untaken_ok = str(frozen)
        for case, args_, want_stop in (("take_authors false, the authors ahead", dict(take=False), False),
                                       ("take_authors true, their new tip not carried", dict(take=True, uc=gu("rev-parse", "HEAD")), True),
                                       ("take_authors true, the plan's commit carried", dict(take=True, uc=gf("rev-parse", "HEAD")), False)):
            freeze_file(**args_)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                st = drift_stop(fork, a_, fetch=True, props_file=rbp, drift_file=rdrift)
            expect((st is not None) == want_stop and "1 commit(s)" in (buf.getvalue() + (st or {}).get("stop", "")),
                   f"--untaken-ok against a real authors' branch, {case}: {st} {buf.getvalue()}")
        rbp.unlink(); rdrift.unlink(missing_ok=True); a_.untaken_ok = None
        freeze_file(False)
        # a library release other than the frozen one still stops, whatever the plan says about the authors
        a_.untaken_ok, a_.port_release = str(frozen), ["owner/lib@v9"]
        w(vbp, vbp.read_text(encoding="utf-8") + "library.owner/lib=v1\n")
        with contextlib.redirect_stdout(io.StringIO()):
            st = drift_stop(fork, a_, fetch=False, props_file=vbp, drift_file=vdrift)
        expect(st and st["code"] == DRIFT_EXIT and "owner/lib" in st["stop"] and "authors" not in st["stop"],
               f"--untaken-ok covers the authors' branch only, never another library release: {st}")
        a_.untaken_ok, a_.port_release = None, ["owner/lib@v1"]
        gf("remote", "remove", "origin")
        # a stop without choices (a layer-deps failure did this on a dependent) prints instead of raising KeyError
        expect("Choices" in stop_block({"stage": "setup", "left": "layer-deps.py resolve: x", "code": 2}),
               "stop_block: a stop with no choices still prints")
        import ast
        tree = ast.parse((ROOT / "tools/fork-port.py").read_text(encoding="utf-8"))
        bare = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Dict)
                and {"stop", "code"} <= {k.value for k in n.keys if isinstance(k, ast.Constant)}
                and "choices" not in {k.value for k in n.keys if isinstance(k, ast.Constant)}]
        expect(not bare, f"every stop this tool returns names its choices (lines {bare})")

    def f5():
        with tempfile.TemporaryDirectory() as d:
            up, fork = pathlib.Path(d) / "authors", pathlib.Path(d) / "fork"
            gu = git_repo(up); w(up / "A.java", "a\n"); gu("add", "-A"); gu("commit", "-qm", "authors 1")
            gu("branch", "port-base"); w(up / "A.java", "a2\n"); gu("commit", "-qam", "authors 2 (default branch only)")
            subprocess.run(["git", "clone", "-q", "-o", "upstream", str(up), str(fork)], check=True, capture_output=True)
            gf = lambda *x: subprocess.run(["git", "-C", str(fork), *x], capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()
            gf("config", "user.email", "a@b"); gf("config", "user.name", "a")
            gf("checkout", "-q", "-b", "port", "upstream/port-base")
            vbp = fork / ".port/1.21.3/branch.properties"
            w(vbp, "minecraft_version=1.21.3\n"); gf("add", "-A"); gf("commit", "-qm", "version branch")
            expect(detect_upstream(fork) == "upstream/port-base", f"the branch the port carries, not upstream/HEAD: {detect_upstream(fork)}")
            rec, why = freeze_upstream(fork, "upstream/main", (), False, vbp)
            expect(rec is None and "keep-current" in (why or ""), f"freeze refuses a branch HEAD lacks commits of: {rec} {why}")
            expect("upstream_commit" not in read_props(vbp), "nothing recorded")
            write_props(vbp,
                        {"upstream": "upstream/main", "upstream_commit": gu("rev-parse", "main")})
            a_ = argparse.Namespace(repo=str(fork), target="1.21.3", base="origin/neoforge-1.21.1", port_release=[],
                                    upstream=None, accept_upstream_drift=False)
            with contextlib.redirect_stdout(io.StringIO()):
                st = drift_stop(fork, a_, vbp, fork / ".port/1.21.3/DRIFT.md", fetch=False)
            expect(st is None, f"nothing moved since the recorded commit: no stop, though HEAD lacks it: {st}")
            gu("checkout", "-q", "main"); w(up / "A.java", "a3\n"); gu("commit", "-qam", "authors 3")
            with contextlib.redirect_stdout(io.StringIO()):
                st = drift_stop(fork, a_, vbp, fork / ".port/1.21.3/DRIFT.md", fetch=True)
            expect(st and st["code"] == DRIFT_EXIT and "1 commit(s)" in st["stop"], f"the authors moved since the freeze: 19: {st}")
    run_case("finding 5", f5)

    def f6():
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d)
            vbp = repo / ".port/1.21.3/branch.properties"
            w(vbp, "minecraft_version=1.21.3\nlibrary.owner/lib=v1\n")
            a_ = argparse.Namespace(repo=str(repo), target="1.21.3", base="x", port_release=["owner/lib@v2"],
                                    upstream=None, accept_upstream_drift=False)
            with contextlib.redirect_stdout(io.StringIO()):
                st = drift_stop(repo, a_, vbp, repo / ".port/1.21.3/DRIFT.md", fetch=False)
            expect(st and st["code"] == DRIFT_EXIT and "owner/lib" in st["stop"], f"a library-only freeze: 19 on v2: {st}")
            a_.port_release = ["owner/lib@v1"]
            with contextlib.redirect_stdout(io.StringIO()):
                expect(drift_stop(repo, a_, vbp, repo / ".port/1.21.3/DRIFT.md", fetch=False) is None, "the recorded release: no stop")
    run_case("finding 6", f6)

    def f14():
        with tempfile.TemporaryDirectory() as d:
            up, fork = pathlib.Path(d) / "authors", pathlib.Path(d) / "fork"
            gu = git_repo(up); w(up / "A.java", "a\n"); gu("add", "-A"); gu("commit", "-qm", "authors 1")
            subprocess.run(["git", "clone", "-q", "-o", "upstream", str(up), str(fork)], check=True, capture_output=True)
            vbp = fork / ".port/1.21.3/branch.properties"
            w(vbp, f"upstream=upstream/main\nupstream_commit={gu('rev-parse', 'HEAD')}\n")
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
                    st = drift_stop(fork, a_, vbp, fork / ".port/1.21.3/DRIFT.md")
            finally:
                subprocess.run = real
            env = (calls[0].get("env") or {}) if calls else {}
            expect(calls and env.get("GIT_TERMINAL_PROMPT") == "0" and (calls[0].get("timeout") or 999) <= 60,
                   f"the fetch cannot prompt and gives up quickly: {calls}")
            expect(st is None and "could not fetch" in buf.getvalue(), f"says so and compares with the last fetch: {buf.getvalue()}")
    run_case("finding 14", f14)

    # ---- single-version: no version branch -- fork-port stops at once (exit 2), nothing written, nothing of a
    # version branch said; --chain, --check-done and --write-test-items the same
    def single():
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d); g = git_repo(repo)
            w(repo / "gradle.properties", "minecraft_version=1.21.1\n"); w(repo / "build.gradle", "plugins {}\n")
            g("add", "-A"); g("commit", "-qm", "port")
            before = sorted(str(p) for p in repo.rglob("*") if ".git" not in p.parts)
            for extra in ([], ["--from-stage", "moves"], ["--check-done"], ["--write-test-items"]):
                buf, real_argv = io.StringIO(), sys.argv
                sys.argv = ["fork-port.py", "--repo", str(repo), "--target", "1.21.4", *extra]
                try:
                    with contextlib.redirect_stdout(buf):
                        rc = main()
                finally:
                    sys.argv = real_argv
                out_ = buf.getvalue()
                expect(rc == 2 and "single-version fork" in out_ and not any(k in out_ for k in ("upstream", "REVIEW", "NOT done")),
                       f"single-version {extra}: exit 2, nothing of a version branch said: {rc} {out_}")
            buf, real_argv = io.StringIO(), sys.argv
            sys.argv = ["fork-port.py", "--repo", str(repo), "--chain", "1.21.3..1.21.4"]
            try:
                with contextlib.redirect_stdout(buf):
                    rc = main()
            finally:
                sys.argv = real_argv
            expect(rc == 2 and "single-version fork" in buf.getvalue(), f"single-version --chain: exit 2: {buf.getvalue()}")
            expect(sorted(str(p) for p in repo.rglob("*") if ".git" not in p.parts) == before, "single-version: nothing written")
            expect(detect_upstream(repo) is None, "single-version clone with no upstream remote: nothing detected")
    run_case("single-version", single)

    # ---- the removed layer: a checkout with .multiversion/ at HEAD is refused with the one message (tools/fork-kind.py)
    def layer():
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d); git_repo(repo)
            w(repo / ".multiversion/base.properties", "minecraft_version=1.21.1\n")
            buf, real_argv = io.StringIO(), sys.argv
            sys.argv = ["fork-port.py", "--repo", str(repo), "--target", "1.21.4"]
            try:
                with contextlib.redirect_stdout(buf):
                    rc = main()
            finally:
                sys.argv = real_argv
            fk = load("fork_kind", "fork-kind.py")
            expect(rc == 2 and fk.LAST_LAYER_COMMIT[:12] in buf.getvalue() and "fork-branches.py migrate" in buf.getvalue(),
                   f"a layer at HEAD: refused, naming the last commit with the layer: {buf.getvalue()}")
    run_case("layer refused", layer)

    def gap7():
        # issue #133 gap 7: an item the reviewer marked not-ok is reported once, with its note
        text = ("# Review: Minecraft 1.21.4\n\n- reviewer: r\n- date: d\n- commit: abc1234\n\n| kind | file | verdict | note |\n"
                "|---|---|---|---|\n| item | main:A.java#mixin:X | not-ok | the hook is gone, behaviour lost |\n")
        inv = ["behaviour inventory: unaccounted main:A.java#mixin:X (not in the target's file)",
               "behaviour inventory: unaccounted main:C.java#event:E (not in the target's file)"]
        got = drop_reported_items(inv, not_ok_items(text))
        expect(got == inv[1:], f"an item the reviewer marked not-ok is reported once, with its note: {got}")
    run_case("gap 7 review rows", gap7)
    return ok


if __name__ == "__main__":
    sys.exit(main())
