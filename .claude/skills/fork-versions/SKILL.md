---
name: fork-versions
description: Make a fork port build for more Minecraft versions from the same branch -- a multi-version layer (.multiversion/ plus one commented hook line) that leaves the authors' code and the 1.21.1 build untouched, ports one target (1.21.x, then 26.x) at a time through rename tables, overlays and a shared helper library, gates each target like the base, records what it cost, and feeds every new pattern back into the shared library. Use when the user asks to support another 1.21.x or 26.x version (or "the latest") in a fork, to add or port a target, to gate a target, or to measure how far a fork is from newer versions.
---

# Build a fork for more Minecraft versions

**Opt-in, and not the default.** A single-version fork (`neoforge-1.21.1` and nothing else) is the default and the
proven path; this skill runs only when the user explicitly decides a fork gets more versions. Only the forks the maintainer has opted in have it. **Do NOT use it** to port a fork to a new version (that is `port-fork`), to keep a fork current
(`keep-current`), to publish (`publish-port`), or when the user only said "the latest" for a fork that has no
`.multiversion/` -- ask first. Nothing here may change how a single-version fork builds, gates, syncs or publishes:
every behaviour below is gated on the fork having `.multiversion/` and is off by default.

The port-fork skill makes a fork build for ONE version (its `neoforge-1.21.1` branch). This one adds more versions
**without a second copy of the code**: a `neoforge-1.21.x` layer branch that is the base branch plus a
`.multiversion/` directory and one commented line at the end of the authors' `build.gradle`. Design and
measurements: issue #49. Tools: `tools/make-fork-layer.py`, the fork's `.multiversion/build.sh` and
`.multiversion/tools/layer.py`, `tools/ci-gates.py --mc`, `tools/audit-mixin-targets.py`,
`tools/fix-access-transformer.py`, `tools/burndown-count.sh`, `tools/port-cost.py`.

**This is as much about growing the library as about the port.** Every target leaves behind more shared rows,
helpers, catalogue entries and a cost figure; the next fork, and the next version, start from them.

## Stops -- ask the user

1. Which fork, and whether to target "every stable 1.21.x" (the default) or a subset.
2. Merging the layer branch's PR, unless the fork's fleet entry allows automerge (keep-current's rules).
3. **Never**: edit anything outside `.multiversion/` and the hook line on the layer branch, promote or publish a
   release, push to the authors' repository.

## Direct jumps (opt-in per target)

By default a target is one hop: `add-target` points `inherits` at the nearest lower target, and `layer.py prepare`
applies the shared rename tables and helpers of every target in that `inherits` chain. A fork that wants 1.21.1 -> 1.21.11
without ever shipping the middle versions would then miss `shared/1.21.3` ... `shared/1.21.10`. For that, **add the target
with `--direct`**:

    python3 tools/make-fork-layer.py add-target --repo DIR --target 1.21.11 --direct     # or fork-port.py ... --direct

It writes no `inherits` and one key in `targets/1.21.11/target.properties`:
`shared_chain=1.21.3,1.21.4,1.21.5,1.21.8,1.21.10,1.21.11` (every targeted version above the base up to the target, from
`versions.json`; hand-edit it to take fewer). `sync-shared` installs the shared library of each listed version, and
`layer.py prepare` applies them **newest first**: rows nearest first, the nearest helper wins -- the order a hop-by-hop
chain has, so for shared-only content a direct 1.21.11 prepares the same tree as 1.21.3 > ... > 1.21.11 (a self-check
proves it, and a control proves the test can tell). A target that inherits a direct one sees what its parent saw. A fork's
own old `layer.py` ignores the key, so `fork-port.py` setup replaces it when it is a verbatim older copy of the template
and stops (exit 2) when it is not; `add-target --direct` warns.

When to use it: the middle versions will never be released, and you would otherwise spend a full port + gates + CI minutes
on each. Measured per hop with the shared libraries in place (issue #49): 1.21.4 62 errors, 1.21.5 888, 1.21.8 185 ($1.30),
1.21.10 251 ($3.31), 1.21.11 972 ($6.76). A direct jump skips the middle targets' own overlays and gates; it does **not**
skip their API changes (the 1.21.11 target must now absorb all of them in one overlay set), and a direct-jump cost has **not
been measured yet** -- run it on a throwaway clone first and record the figure. The hop chain stays the better choice when
a middle version ships to players (each hop is small, each is gated, and a regression is attributable to one hop).

## Which versions (and why some are skipped)

`templates/fork-layer/versions.json` is the policy, in data: a 1.21.x version is targeted only if NeoForge
published a **stable** line for it (players install stable NeoForge; a jar for a beta line pins a loader few run).
1.21.2, 1.21.6, 1.21.7 and 1.21.9 had beta-only NeoForge lines, each superseded within weeks, and are skipped --
the reason is recorded per line and `add-target` refuses them. `make-fork-layer.py versions` prints the table;
`versions --refresh` re-reads the NeoForge maven (and names any stable line versions.json does not have yet). A
target's jar declares exactly its own Minecraft and NeoForge ranges (`[1.21.3,1.21.4)`), so a skipped version is never
claimed.

**The 26.x calendar line** follows 1.21.11 in the same table: 26.1.2, 26.2 and 26.3 are targeted (26.1 and 26.1.1 had
beta-only NeoForge and are skipped). Three things differ, and `add-target` handles each from versions.json: NeoForge
numbers 26.x by the whole Minecraft version (26.2 is NeoForge `26.2.0.B`, 26.1.2 is `26.1.2.B`), so every Minecraft
release is its own line and both ranges stop at the next patch release (`[26.2,26.2.1)`, `[26.1.2,26.1.3)`); the
target gets `java_version=25` (CATALOG §V1); and GeckoLib 5.5+ is published as `com.geckolib:geckolib-neoforge-<mc>`, so a
fork that names `software.bernie.geckolib` gets an escaped `substitute.` line for it. 26.2 and 26.3 also get `nfrt_version=2.0.31`: the NeoForm runtime that ModDevGradle 2.0.146 brings (2.0.27)
cannot stage NeoForge 26.2 at all, and `layer.gradle` sets the newer one without touching the authors' plugins block. The default chain is one hop on:
26.1.2 inherits 1.21.11. Read CATALOG §V (the 1.21.1 -> 26.2 era jump) before porting a 26.x target; its rename table
(`templates/multi-version/versions/26.2.renames.tsv`) is a 1.21.1 table, so most of what it does a 1.21.11 tree already
has -- the shared libraries for 26.x start empty and grow from measured hops.

## The layer's rules (each is checked, not remembered)

- **Inert**: without `-Pmc` (or with the base version) `layer.gradle` returns at once. CI builds the base jars with
  and without the hook line: `layer.py compare-jars` must say identical for every jar.
- **Isolated**: `layer.py isolation --base origin/neoforge-1.21.1` -- only `.multiversion/` and the appended
  commented hook differ. Every merge from the base branch is then conflict-free.
- **Per-target build dir** `build/mc<t>/` (inside the authors' ignored `build/`): targets never share classes, and the
  client runs in `build/mc<t>/run`, so one target's mod configs never reach another's client (a single-version build
  and the base keep the authors' `run/`).
- **Versions come from `-P`**: `.multiversion/build.sh <t> <tasks>` passes the target's set; `layer.gradle`
  refuses a `-Pmc` whose versions did not arrive.

## CI on the layer branch

The layer branch may differ from `neoforge-1.21.1` only inside `.multiversion/` and the one hook line, so its CI
cannot be a workflow file of its own: it is the SAME `.github/workflows/port-ci.yml` on both branches, generated by
`python3 tools/port-ci.py --repo DIR --modid ID --layer-branch neoforge-1.21.x` (never hand-edited), and installed
on `neoforge-1.21.1` first (a PR), then merged into the layer branch. With `--layer-branch` the workflow also
triggers on that branch and carries six `layer-*` jobs; on `neoforge-1.21.1` all of them are skipped, and a fork
without `.multiversion/` (or without `--layer-branch`) behaves exactly as before. The `gates` jobs also run on the
layer branch: they are its 1.21.1 bookend.

| job | runs | what |
|---|---|---|
| `layer-plan` | layer branch only | `layer-ci.py plan`: the targets in `.multiversion/targets`; the NEWEST by version sort (1.21.11 is newer than 1.21.8; a lexical sort gets it backwards), the middle ones as a matrix, each target's JDK from `java_version` (following `inherits`, else 21) |
| `layer-isolation` | after plan | `.multiversion/tools/layer.py isolation --base origin/neoforge-1.21.1` |
| `layer-inertness` | after plan | `layer-ci.py inertness`: `clean assemble` plus every Jar task the authors registered, with and without the hook line, then `layer.py compare-jars` on each pair (zero jars, or no hook line to remove, fails) |
| `layer-compile` (matrix: every target except the newest) | after plan | `layer-ci.py compile`: `.multiversion/build.sh <t>` over every source set's compile task (`srcsets.py`), then `audit-mixin-targets.py` against that target's staged sources jar and `audit-event-listeners.py` against Gradle's own resolved classpath |
| `layer-gates` | after plan | `ci-gates.py --mc <newest>`: author's build, Gate A, data, payloads, Gate B and Gate C under Xvfb, plus `--shipped` when `.multiversion/shipped-expect*.txt` exists; the gate summary is uploaded as `layer-gates-<newest>` |
| `layer-gates-extra` (matrix, `full_gates` only) | after plan | the same gates for each released middle target in `.multiversion/ci.properties`; no job when the list is empty |
| `layer-release` | `release` dispatched on the layer branch | after the base gates, `layer-isolation`, `layer-inertness`, `layer-gates` and `layer-gates-extra` are green (`layer-compile` and `layer-gates-extra` may be skipped -- no middle target, no `full_gates` -- never red): ONE pre-release holding the base jars and each target's `<name>-mc<t>.jar` (built by `layer-compile`/`layer-gates` when releasing) -- the release publish-port's layer path uploads per target. A target marked `preview=true` in its own `target.properties` (not inherited) is built, gated and attached the same way; the notes name it as a preview (`layer-ci.py plan` lists them), and publish-port never uploads it. The main `release` job steps aside on the layer branch |

**A dependent fork's sibling jars in CI.** Every layer job first runs `layer-deps.py fetch --repo . --target <t>`: it installs
into mavenLocal each sibling port's per-target jar the target's `target.properties` pins (`port_jar.<g:a>=<url> <sha256>`),
checked against its sha256 -- a no-op for a target that pins none. The pins are written by
`layer-deps.py resolve --port-release owner/repo@TAG` against the sibling's layer release (its `layer-release` job);
locally `--port-checkout owner/repo=DIR` builds the jar from a checkout instead and pins nothing, so CI cannot fetch
it -- re-resolve with `--port-release` before the dependent's layer CI can go green. `fork-port.py` passes both through
(`--port-release owner/repo@TAG`, kept in its resume command), and a later resolve that names the sibling with neither
keeps the existing `port_jar.` pin while it still pins the version `port-ci.yml` installs. `resolve --dry-run` builds,
fetches and installs no sibling jar. A dependency the fork's `port-install.json` does not name is required when it is
`implementation`/`api` (fail closed); a Modrinth pin by slug is matched to the manifest's project id.

**Writing `.multiversion/shipped-expect*.txt`:** each `FILE:REGEX` line proves a subsystem ran in the jar a player
installs, so match the effect, not the exact wording: the class it acted on and what it did, accepting the forms a
mod logs a class by (`(net/minecraft/server/level/)?ServerLevel\b`, a dotted or slashed package). An upstream update
that only rewords a line must not turn the gate red; a line that disappears must. When it does turn red after an
upstream merge, keep-current's layer step 7 says how to tell the two apart.

**CI policy (data, not memory).** The bookends -- the base branch's 1.21.1 and the newest target -- get every gate on every
push. Every middle target gets compile + mixin audit + event-listener audit only, because Gate C costs about 10 minutes of
runner time per target and a middle target that stopped applying shows in the compile in minutes. **A middle target that is
RELEASED gets the full gates too**: list it in the fork's `.multiversion/ci.properties`,

    full_gates=1.21.4,1.21.8        # comma list of middle targets; absent or empty = none (the default)

and `layer-ci.py plan` gives it a `layer-gates-extra` job (the same `ci-gates.py --mc` the newest target gets; it is still
compiled in the middle matrix). A name that is not a target fails the plan, so a typo cannot mean "no gates". The new job
needs the Port CI kit that carries this `layer-ci.py` (a fork pinned to an older kit simply never runs it; the kit is published when the maintainer merges to main, so such a change waits for that merge), and
`port-ci.py --layer-branch` regenerates the workflow that holds it. Take a target off the list when it stops being released.

**One run per checkout (CATALOG X44).** `fork-port.py` takes `build/.fork-port.lock` (pid; a dead pid is stale and taken
over) for its whole run and a second run on the same checkout is refused at once, exit 14, naming the holder; `ci-gates.py
--mc <t>` takes the same lock (its parent's children are let through, so `fork-port.py`'s gates stage works). A run without
`--mc` -- a single-version fork -- takes no lock. Delete the file by hand only if its pid is not really that run.

**Spend guard.** The measure stage prints the starting error count; before the loop, `fork-port.py` refuses to start (exit 10,
spend guard) when it exceeds `--max-errors` (default 100 per dollar of `--budget`; earlier hops cleared 76-144/$) unless
`--confirm-spend` is given. A target that skips versions with shared libraries and has no `shared_chain` gets a warning
naming them (use `--direct`).

**A resume re-measures.** `fork-port.py --from-stage S` keeps the earlier stages' results for the summary but never their
counts: the loop, the audit and the gates start from a compile count measured by THIS run, so a stale 0 cannot skip them.

All of them download the same pinned Port CI kit as the other jobs (the kit carries `layer-ci.py`, the event-listener
audit, `qtc-init.gradle` and `shipped-gate.py`). `fork-port.py` and the compile job share `layer-ci.py`'s
`target_classpath()`, so the CI audit and the audit stage cannot drift apart. A target added later needs no CI edit:
the plan reads the directory. The middle targets are compile-only on purpose (a change that breaks one overlay shows
in minutes); a full run of every target's gates is `fork-port.py --gates full`, before a release.

## Run the tool; handle only the residue

`python3 tools/fork-port.py --repo DIR --target <t> [--budget 15] [--gates auto|none|ab|full]` runs steps 2-7 below
as stages (setup, stage, moves, measure, loop, audit, gates, cost) and prints one line per stage. It stops
only when a stage cannot finish: a STOPPED block, exit 10 (compile), 11 (Gate A/B), 12 (Gate C) or 13 (mixin
audit), and `.multiversion/targets/<t>/RESIDUE.md` -- the remaining errors by CATALOG entry, what the compile
loop tried (files still failing at the top model tier, rows it reverted), the audit mismatches or failing gates,
and the `--from-stage` command that resumes. **You handle only what RESIDUE.md lists** -- do not read the build
logs -- and **every fix you make goes into the shared library (`templates/fork-layer/shared/<t>`) or a converter,
not into one fork** (step 8). The class moves between versions are generated, not written: `moves.generated.tsv`
is rebuilt per fork per target from the two staged Minecraft jars and is never committed upstream as a map.
The steps below are the reference for what each stage does and for fixing its residue by hand.

**One fresh residue fixer per Minecraft version.** When the residue goes to a worker agent (rather than being fixed
in your own session), start a NEW agent for each target, with only that target's `RESIDUE.md` / `STUBS.json`, the
rules (edit only `.multiversion/targets/<t>/`, generic shapes to the shared library, no commits) and the resume command.
Resume that agent only for the same target's next round (its Gate C phases, a stub review); never carry one agent
from target to target. Its context compounds: measured, one fixer resumed across six targets of a dependent fork
grew to ~650k tokens and cost about $120 -- most of it re-reading earlier targets' work. What a later target
needs from an earlier one is already in the tree (the parent's overlays, `STUBS.json` reviews it inherits -- only while the flagged file hooks the same mixin targets, `@At`s and events; a changed hook set reopens the entry as `re-justify: hook targets changed ...`) and in the
catalogue entries the earlier fixer wrote.

**Clean-room rule for shared helpers.** A fixer that writes or rewrites a shared helper re-implementing a third-party
class (a Minecraft or NeoForge class a version removed, a GeckoLib type) may read that class's API -- its signatures,
`javap -p` output, its javadoc -- but not its implementation bodies, and writes the bodies from the API and the
behaviour it needs. Three helpers were once transcribed statement-for-statement from NeoForge and Minecraft sources
with the licence notice stripped. Reading implementations stays fine for porting the authors' own code into a
fork's overlays: that code is theirs, ported in their fork. The similarity gate (`tools/check-similarity.py`) runs in
the pre-commit and pre-push hooks and refuses template Java that matches the NeoForge/Minecraft/GeckoLib sources on
the machine; a refusal is fixed by rewriting from the API, never by an annotation. Give a worker fixer this rule.

**A version is done only after Gates A, B AND C.** Gates A/B and the audits cannot see what only a client builds --
models, render layers, shaders -- and every dependent-fork version so far that was green at A/B still failed in a real
client (a missing model part at launch, a missing shader sampler at spawn). So `fork-port.py` never reports a target
done without a green Gate C recorded in its `GATES.md`: a run that ends green at A/B exits 17 ("A/B green, Gate C not
run -- NOT done") with the command that runs the full gates, and so does a run with `--gates none`. Nor with a stub
signal in `STUBS.json` still unreviewed: every run to the end then stops with exit 10, from whatever `--from-stage` it
resumed (even `cost`). This is the development loop, not CI: CI stays
bookends-in-full, middle targets compile + audits.

**One real-client run per version, on the initial port.** `--gates auto` (the default) runs the FULL gates -- Gate C
under Xvfb -- until the target's `.multiversion/targets/<t>/GATES.md` records a green Gate C; a green full run writes that
file (the same record publish-port's layer path reads), and a full run that is not green removes it, so the next
`auto` run is full again. After that a re-run is A/B. CI keeps its own policy (bookends in
full, middle targets compile + audits): this is the port's one look at each version in a real client, not a CI cost.

**Commit a fix before the gate run that certifies it.** `GATES.md` names the commit it tested; a run on an edited
checkout records `<sha> + uncommitted: <files>` (the run's own GATES.md/COST.md/RESIDUE.md/STUBS.json do not count),
because the bare parent commit does not hold the code that went green. So a residue fixer commits its edits (locally
is enough), then runs the gates, and the GATES.md the run writes is committed after it, naming the fix's commit.

## Procedure

0. **Read first**: CATALOG §M and `references/minor-version-deltas.md` (the U and L sections). The first layer
   port re-derived five rows that were already there.
0b. **Guard the names**: `python3 tools/guard-names.py ensure --mod-repo DIR` plus one `--mod-repo` per fork it depends on (its names go into
   the list outside this repository; the hook refuses them in shared rows, helpers and catalogue entries). Before a
   commit here, `guard-names.py check` with the same arguments must pass.
1. **Measure** every targeted line against the base source before porting anything (one compile per line with
   `-Pminecraft_version/-Pneo_version` and the AT re-derived, `burndown-count.sh`): the counts decide the order
   and set expectations. Post them on the fork's tracking issue.
2. **Set up**: `make-fork-layer.py init --repo DIR` (once), then `add-target --repo DIR --target <t>` -- versions,
   Java, GeckoLib and `inherits` (the nearest lower target) come from versions.json, and `sync-shared` installs the
   shared library (`.multiversion/shared/<t>/`: rows every fork inherits + helper classes in the fork's
   `mv_package`) and refreshes the layer's machinery (`layer.gradle`, `build.sh`, `.multiversion/tools/`) from the
   template when the fork's copy is a verbatim earlier template version (this migrator's git history), so a layer made
   before a `layer.gradle` change (a target's `nfrt_version`) still builds the way new targets assume; a copy someone
   edited is reported (`DIVERGED`) and left alone for a human to merge. `sync-shared --check` fails when the fork's copy
   of either drifted or diverged. A target that jumps over middle versions is added with
   `--direct` (see "Direct jumps" above); the default is one hop.
   **CI, once per fork, before the first target lands:** `python3 tools/port-ci.py --repo DIR --modid ID --layer-branch
   <layer branch>`, as a PR on the port branch, then merged into the layer branch (see "CI on the layer branch"). From
   then on every push gets the bookends in full (1.21.1 and the newest target) and compile + audits for the middle
   targets. A layer branch without this has no gate of its own.
3. **Access transformer**: the base AT names classes vanilla moved. Re-derive it into
   `targets/<t>/resources/META-INF/accesstransformer.cfg` with `fix-access-transformer.py --overrides-from <log>`
   until `createMinecraftArtifacts` succeeds. Never widen a method vanilla subclasses override to a protected
   one's caller -- the recompile fails (`cannot override ... weaker access`); call it reflectively instead.
4. **Burn down** (`.multiversion/build.sh <t> compileJava`, count with `burndown-count.sh`), cheapest first:
   - shared rows already apply; add a **row** in `targets/<t>/renames.tsv` for this mod's own call shapes. A
     regex runs before the type renames, so write it against the ORIGINAL text (`DimensionTransition`, not
     `TeleportTransition`); identifiers can contain `$` (`[\w$]+`, mixin handler names).
   - a **helper** class when a removed method can be stood in for (`net.<mod>.mv`, overlay-only).
   - an **overlay** (whole file, `targets/<t>/java/...`) only for a real semantic change. Derive it from the
     PREPARED file (`build/mc<t>/mv/java`, already renamed): overlays are not run through the table.
   - a mixin new to the target: add it with `mixin_add.<config>=client:Name` in target.properties.
     `mixin_add` reaches every target that inherits this one too. A hook that only THIS version's method shape takes
     (the next target changes the method) uses `mixin_add_only.<config>=client:Name` instead, and an authors' mixin
     that fails on this version alone is taken off with `mixin_remove_only.<config>=mixins:Name`: both apply to that
     target alone, after the inherited `mixin_add` chain. `layer.py` leaves such a class out of every other
     target's prepared tree (so their compile and mixin audit never see it) and a removed one out of this target's;
     `layer.py mixins --target <t>` prints what a target resolves. A `mixin_remove_only` the config does not list
     stops the build. Single-version forks have no `.multiversion/`: none of this applies.
   - **Every source set** goes through the table (`mvPrepare<Set>Sources`): a mod that builds `cf`/`pro`
     variants needs them compiled too, and their overlays live at `targets/<t>/<set>/java`. A target is not at
     0 errors until `compile<Set>Java` is, for every set.
   - After a run of failed compiles, delete `build/mc<t>/classes` before trusting a green compile: a partial
     class dir (33 of 864 classes once) makes every set built on main fail with `package ... does not exist`.
   - Read the dead-rule line every build: a row that went dead when its file became an overlay is deleted; a row
     that never fired is usually written against already-renamed text.
5. **Mixins**: at 0 errors, `audit-mixin-targets.py DIR <target sources.jar> --sources=build/mc<t>/mv/java` must say
   all targets match. javac cannot see a mixin target; the first port had 16 load-fatal ones at a clean compile.
6. **Gates**: `ci-gates.py --repo DIR --modid ID --base <author ref> --mc <t>` (author's build, Gate A, data,
   payloads, Gate B, Gate C phases). Run the SAME gates without `--mc` as the control in the same session: a red
   that is red on the base too is not the target's. Read the mod's own logs on green runs (an agent or coremod
   that degrades silently). A harness compile error on a new target is fixed once in
   `templates/upstream-harness/versions/<t>.renames.tsv`, for every fork.
7. **Record the cost** (required): `python3 tools/port-cost.py <modid> --path DIR --target <t> --since <start> --print` for the
   target's work window (`fork-port.py`'s cost stage does this itself, starting where the target's last record ended);
   put the block in `.multiversion/targets/<t>/COST.md` with what the window covered (port only, or port + tooling)
   and the model mix. With `--target`, each request is charged to one target at most, by what its own tool calls
   touched (a subagent or loop worker by its prompt); this checkout's shared work and other targets' are listed on the
   `- attribution:` line, not in the total, so concurrent targets' and forks' records add up instead of each holding
   the whole session. The figure is what tells us whether the library and the
   cheaper models are making targets cheaper.
8. **Harvest** (required, before calling the target done): move every row and helper that is not specific to this
   mod into `templates/fork-layer/shared/<t>/` (`@MVPKG@`/`@MODID@` placeholders), re-sync the fork and show its
   prepared tree unchanged but for names; write each new pattern as a CATALOG §M entry and an L entry in
   `references/minor-version-deltas.md`.
9. Commit on the layer branch (`neoforge-1.21.x`), push, and report: errors before/after, gates per target with the
   control, cost, what was harvested, what is left.
10. **Keep it current** (once per fork, when its first target is green): give the fork's keep-current fleet entry
   `"layer_branch": "<layer branch>"`, and if a nightly routine runs it, put that key in the routine's fleet JSON
   too (the routine reads its prompt, not this repo's copy). Without the key, the nightly run checks the port branch,
   reads the fork as single-version, and the layer falls behind. Then check it the way the run will:
   `layer-sync.py detect --repo DIR --ref origin/<layer branch> --port-ref origin/<port branch>` must print
   `layer=true` with every target and `layer_behind=0` (or the count the next run will merge).

## Models and cost

The per-error loop of a layer target is `tools/file-loop.py --layer <t>` (Haiku -> Sonnet -> Opus; a fix lands
as a row in the target's renames.tsv or an overlay, never in the authors' tree), which `tools/fork-port.py` runs.
Keep the session's own context small -- most of a long session's cost is cache reads (`tools/context-profile.py`).

First measured target (a large library mod, 1.21.1 -> 1.21.3, 282 -> 0 errors, 142 mixin targets, Gates A/B green):
about $30 on Opus alone, ~1.3 h, covering the measurement of all lines, the layer tooling, the port and its gates.
