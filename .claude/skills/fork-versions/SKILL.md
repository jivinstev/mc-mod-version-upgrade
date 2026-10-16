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

## Done: one definition, fixed before the buildout starts

A target is **done** when, and only when, all of these hold (`tools/fork-port.py` `target_done()`; ask it with
`python3 tools/fork-port.py --repo DIR --target <t> --check-done`, exit 0 or 18 with every open reason):

1. it compiles at 0 errors in every source set and the mixin and event-listener audits are clean (the gates stage runs
   only then);
2. the full gates are green **including Gate C**, recorded in `.multiversion/targets/<t>/GATES.md` at a commit (not
   an edited tree) that HEAD carries, with nothing that can change the target's jar changed since -- the authors'
   `src/`, its chain's `targets/` and `shared/` (documents and run outputs aside): the rule `layer-deps.py` applies to a
   library's checkout;
3. no `STUBS.json` entry is unreviewed;
4. `.multiversion/targets/<t>/REVIEW.md` is complete for the commit it names: an **independent fresh-context review**
   with one verdict (`ok` / `not-ok`) and a note per row, and a row for each of:
   - every `STUBS.json` entry and every whole-file overlay (read against the authors' file it replaces). A whole-file
     overlay is one definition, `tools/layer-sync.py` `whole_file_overlays()`: a `.java` file of the target's own
     `java/` (or `<set>/java/`) that replaces an authors' file -- exactly the files `layer-sync.py overlays` counts
     against the budget, reported everywhere as "whole-file overlays (N) + overlay-only files (M)"; an overlay-only
     file (a helper the authors do not have) needs no row and is not budgeted;
   - every **resource group** of the target's own `resources/` (kind `resources`): its first three directories
     (`assets/<ns>/items`, `data/<ns>/recipe`, `data/<ns>/tags`, ...), or the file itself when it is shallower, with
     the digest of the group's files in the row (`<path> @<12 hex>`, written by `fork-port.py`'s skeleton) -- read each
     file against the authors' 1.21.1 resource it stands for (an item definition against the model, its `overrides`
     and the spawn egg's colours; a recipe or tag against the code that uses it);
   - the target's `renames.tsv` as a whole (kind `renames`, with its digest): every row read as a rename the target
     needs. An edit to a group or to `renames.tsv` changes its digest, so it is a new row with no verdict even when
     `commit` was moved on. One row per group rather than per file: a root target carried ~150 resource files, ~130 of
     them generated item definitions with no ledger line, and four of the ten regressions its first review found (a
     missing repair tag, spawn-egg tints, an item-model condition, trim selects) were in those files -- a "generated,
     no row" exemption would have hidden three of them, a row per file is ~150 notes, and a digested group row makes
     the reviewer read them and every later edit re-reviewed. (A file that is missing, like that repair tag, is found
     from the overlay that uses it; the group's row is how the fix gets reviewed.)

   Plus `reviewer`, `date` and `commit`: `reviewer` names the reviewing agent, distinct from the fixer (e.g. `fresh
   agent <session id>, not the fixer`); `commit` is the full or abbreviated hex sha of the commit reviewed, 7 to 40
   hex digits (`git rev-parse --short HEAD`) -- `HEAD`, a branch or a tag name is refused. Nothing the target is built
   from has changed since that commit -- its directory, the authors' `src/`, its chain's `targets/` and `shared/` (the
   run's own `GATES.md`/`COST.md`/`RESIDUE.md`/`TEST-ITEMS.md`/`INVENTORY-CHECK.json`/`DEPS.md` and the reviewer's own
   `REVIEW.md`/`BEHAVIOUR.json` aside; `BEHAVIOUR.json` is still what the inventory account reads); and no overlay
   `layer-sync.py` recorded in the target's `REMERGE.json` as not taking the authors' change still has the bytes it had
   then;
5. the target's manual-test items are generated (`TEST-ITEMS.md`, from `tools/layer-test-plan.py`; a run of
   `fork-port.py` to the end writes it, and `python3 tools/fork-port.py --repo DIR --target <t> --write-test-items`
   writes it alone -- no stage or gate, under the checkout lock -- which `--check-done` names when it is missing);
6. every item of the base build's behaviour inventory (`.multiversion/INVENTORY.json`) is accounted for on the
   target -- `tools/layer-inventory.py`'s check, run by `target_done()` itself and written to the target's
   `INVENTORY-CHECK.json` (see "A version accounts for the base's behaviour"); a layer with no `INVENTORY.json`, or a
   target whose prepared tree (`build/mc<t>/mv/java`) is missing or older than what it is prepared from, is not done
   -- a run of `fork-port.py` re-prepares it (no compile), `--check-done` names the `layer.py prepare` command and writes
   nothing;
7. no dependency of the target was resolved against a library target that is not done (`--allow-undone-library`,
   recorded under "Development only" in its `DEPS.md`): re-resolve with `--port-release` once the library's is.

A run whose gates are green but that misses 4-7 exits **18** ("Gate C green, NOT done") and adds a `?` row to
`REVIEW.md` for every item without a verdict, and an `item` row (`| item | <inventory id> | ? | ? |`, a file's ids
together) for every behaviour-inventory item the target leaves unaccounted; it never writes a verdict. **The list does not grow during a buildout**:
each bar added after targets were done swept every finished target again (measured: 3-7 reopenings per target). A new
requirement is a change to `target_done()` and to this section, made between buildouts.

Four rules around the list keep a done target done: **library first** (a dependent's target resolves only against a
library target that is done and released: `layer-deps.py resolve` exit 18, which `fork-port.py` stops on with 18; see
"Library first"); **upstream frozen** (the buildout records the authors' commit and each library release it started
from, and a run to the end stops with exit 19 when either moved: Procedure step 0); **fix lowest, then sweep** (a late
bug class is fixed at the lowest target and re-gated upward with `fork-port.py --chain`, one fixer only where it goes
red: "A bug class found late"); **few, self-updating overlays** (`layer-sync.py overlays` reports each target's
overlays against `overlay_budget`, and `layer-sync.py run` re-merges each whole-file overlay three ways when the
authors' code moves: Procedure step 4).

**Never start target N+1 before N is done.** `make-fork-layer.py add-target` and `fork-port.py --target <new>` refuse a
new target (exit 18, nothing written) while the target it stands on (its `inherits`, or for `--direct` the nearest
lower target) is not done, naming what is open. `--allow-undone-parent` is for development only: it is recorded as
`allowed_undone_parent=<parent> <date>: <reason>` in the new target's `target.properties`, and that target is not done
until its parent is.

**The reviewer is a fresh agent with only that target's files**: `.multiversion/targets/<t>/` (`REVIEW.md` lists every
item, `STUBS.json`, the overlays, `resources/`, `renames.tsv`) and the authors' files the overlays and resources stand
for -- never the agent that ported or fixed the target, never one carried over from another target, and not shown the
parent's reviews (an inherited "moved, same effect" review that was wrong reached eight targets before anyone looked
again). **The coordinator starts it** with only those files in its prompt; no tool can check that it did, so it is the
coordinator's job, and `reviewer:` must name the reviewing agent distinctly from the fixer (e.g. `fresh agent <session
id>, not the fixer`). It fills `reviewer`, `date` and `commit` (`git rev-parse --short HEAD` of the committed state it
read: a full or abbreviated hex sha, 7-40 digits), gives each row a verdict and a note that says what it compared --
and for each hook it accounts for (a mixin target, an injection, an event listener, a shader uniform or sampler of the
behaviour inventory) names the item id in the file's note or fills that id's `item` row: an `ok` row for a file accounts
for its non-hook items only -- and the result is committed. A `not-ok` goes to the target's fixer; after
the fix, a fresh reviewer reviews again against the new commit.

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
it -- re-resolve with `--port-release` before the dependent's layer CI can go green (`fetch` fails on such an unpinned
sibling substitute and prints the re-resolve command). A target's shipped-jar gate (`ci-gates.py --mc <t> --shipped`)
boots the sibling ports' per-target jars from mavenLocal beside the built jar, as the port branch's `shipped jars` job does. `fork-port.py` passes both through
(`--port-release owner/repo@TAG`, kept in its resume command), and a later resolve that names the sibling with neither
keeps the existing `port_jar.` pin while it still pins the version `port-ci.yml` installs. `resolve --dry-run` builds,
fetches and installs no sibling jar. A dependency the fork's `port-install.json` does not name is required when it is
`implementation`/`api` (fail closed); a Modrinth pin by slug is matched to the manifest's project id.

**Library first.** A dependent fork's target T is ported only after the library fork's T is **done** -- its
`GATES.md` records a green Gate C at the commit the jar comes from, and the rest of the done list ("Done") holds --
and released: the dependent builds against the library's released per-target jar (`--port-release
owner/repo@TAG`), never against a library target still being fixed (measured: porting the two in lockstep reopened
every library target 3-7 times, each library fix re-gating the dependent). So finish the library's T first, then start
the dependent's T. `layer-deps.py resolve` enforces it for a sibling named by `--port-checkout` or `--port-release`:
a sibling target that is not done stops the resolve with exit 18, naming why (no `GATES.md`, A/B only, a FAIL row, an
edited tree, a file that can change the jar changed since the gated commit -- an untracked file under `.multiversion/`
in a checkout included -- then whatever `fork-port.py`'s done list says: for a `--port-checkout` it calls `fork-port.py`'s
own `target_done()` in that checkout, so a library target with no `REVIEW.md` is not done; for a `--port-release` it
reads the same list at the tag: an `ok` row with a note in `REVIEW.md` for every `STUBS.json` entry, every whole-file
overlay, resource group and `renames.tsv` in the tag's tree (the digests from the tree's blob ids, as a checkout's), the review's commit a hex id that is the tag or an ancestor of it with nothing under `src/`,
the chain's `targets/` or `shared/` changed since, `TEST-ITEMS.md`, the inventory account, no "Development only" mark,
an `--allow-undone-parent` parent done). `layer-deps.py library-done --target T --port-checkout owner/repo=DIR` (or
`--port-release owner/repo@TAG`) asks the same question alone (exit 0 done, 18 not). Developing the two together on
purpose is `--allow-undone-library`, for a `--port-checkout` only: the resolve goes ahead and `DEPS.md` records the
override ("Development only"); never release a target resolved that way -- re-resolve with `--port-release` once the
library's target is done. `fork-port.py`'s setup stage stops with the same exit 18 and layer-deps' reason, takes
`--allow-undone-library` and passes it through (recorded in its summary and kept in its resume command), and such a
target is not done ("Done", item 7).

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
only when a stage cannot finish: a STOPPED block, exit 10 (compile), 11 (Gate A/B), 12 (Gate C), 13 (mixin
audit), 18 (a new target over an undone parent, or a library target it resolves against is not done --
`--allow-undone-library` is the recorded development escape, passed to `layer-deps.py` and kept in the resume command) or 19 (the authors' branch moved since the freeze: step 0), and `.multiversion/targets/<t>/RESIDUE.md` -- the remaining errors by CATALOG entry, what the compile
loop tried (files still failing at the top model tier, rows it reverted), the audit mismatches or failing gates,
and the `--from-stage` command that resumes. **You handle only what RESIDUE.md lists** -- do not read the build
logs -- and **every fix you make goes into the shared library (`templates/fork-layer/shared/<t>`) or a converter,
not into one fork** (step 8). The class moves between versions are generated, not written: `moves.generated.tsv`
is rebuilt per fork per target from the two staged Minecraft jars and is never committed upstream as a map.
The steps below are the reference for what each stage does and for fixing its residue by hand.

**One fresh residue fixer per Minecraft version.** When the residue goes to a worker agent (rather than being fixed
in your own session), start a NEW agent for each target, with only that target's `RESIDUE.md` / `STUBS.json`, the
rules (edit only `.multiversion/targets/<t>/`, generic shapes to the shared library, no commits) and the resume command.
Resume that agent only for the same target's next round (its Gate C phases); never carry one agent
from target to target, and never let it write the target's `REVIEW.md` (that is another fresh agent: "Done"). Its context compounds: measured, one fixer resumed across six targets of a dependent fork
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

**A version accounts for the base's behaviour.** Before the first target, `python3 tools/layer-inventory.py build --repo
DIR` writes `.multiversion/INVENTORY.json` from the authors' base sources: every mixin target and injection (method,
`@At`), event listener by event type, render-path class, render API use, core shader with its uniforms and samplers,
and registry the mod adds to (commit it; re-run it after a keep-current merge brings new authors' code -- `check`
warns when `src/` changed since). For each target, once it compiles, `python3 tools/layer-inventory.py check --repo
DIR --target <t>` reads the target's prepared tree and says, per item, `same` (also through the rename tables),
`changed` / `moved` (accounted only by a reviewed `STUBS.json` entry for the file, a row of the target's `REVIEW.md` with verdict `ok` and a
note naming the file or the item id -- a `?` or `not-ok` row, or a mention in a note, does not; for a hook item (mixin,
injection, overwrite, event, shader uniform/sampler) a file's row accounts only when its note names the item id, an
`item` row for the id always does -- or a `BEHAVIOUR.json` entry with a `where` and a `review`), `dropped` (a `drops.txt` comment, or a `BEHAVIOUR.json` entry with a
`reason`) or `unaccounted`: exit 16 lists the unaccounted ones, and the target is not done until it exits 0 (the account
is written to `.multiversion/targets/<t>/INVENTORY-CHECK.json`; a layer fork with no `INVENTORY.json` counts as not
accounted). Record each
in `.multiversion/targets/<t>/BEHAVIOUR.json` (`[{"item": "<id from the list>", "status": "dropped", "reason": "..."}]`, or
`"status": "moved", "where": "...", "review": "..."`; an `item` with `*` covers a group); a later target inherits its
parents' entries. Up to 1.21.4 a core shader's json declares its uniforms and samplers; from 1.21.5 the json is no longer
read, so each must be named by the target's Java (its `RenderPipeline`) unless vanilla supplies it -- the check that
finds a missing sampler before a client does. `fork-port.py` runs both: its setup stage builds `INVENTORY.json` when the
layer has none (an existing layer gets one on its next run; commit it), and its done check (`target_done()`: the end
of every run, `--check-done`, and the parent check before a new target) runs the check and lists each unaccounted item
as a not-done reason (exit 18). Running `check` yourself at 0 errors shows the same list sooner.

**Why Gate C is on the list ("Done", item 2).** Gates A/B and the audits cannot see what only a client builds --
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

**A bug class found late: fix it at the lowest target, then sweep up.** Fix it once, at the lowest target where it
exists (and in the shared library when it is generic), commit, then re-gate the inherited range with no model:

    python3 tools/fork-port.py --repo DIR --chain <lowest>..<newest> --from-stage gates [--gates full]

It runs `--from-stage gates` (any stage may be named) for each target from `<lowest>` up `<newest>`'s inherits chain,
oldest first, with `--budget 0 --max-errors 0` (no worker can start), checks the upstream once, stops at the **first red**
target naming its `RESIDUE.md` and the command that sweeps on from there, and prints a table (target | exit | result |
residue). A green-but-not-done target (exit 17/18) does not stop it; the table says what it still needs. An 18 that is a
refusal before anything was built (a library target not done, an undone parent) does stop it. **Start one
fixer only where the sweep went red** -- never one per target per wave. A target whose files the fix changed needs a fresh
review again ("Done", item 4).

## Procedure

0. **Upstream first, then freeze it** (before a buildout, single fork or library + dependents): run keep-current's
   detect for the fork AND each library fork it depends on (`port-sync.py --dry-run`, `layer-sync.py detect`), and
   bring in what it finds with the keep-current skill -- a ladder started on stale code carries the authors' later
   changes into every whole-file overlay by hand. Then record where the buildout starts:
   `python3 tools/make-fork-layer.py freeze --repo DIR --upstream <remote>/<branch> [--library owner/repo@TAG ...]`
   (or `init --upstream`; `fork-port.py` writes it itself when it adds the layer's first target and exactly one branch
   of the clone's `upstream` remote is carried by HEAD -- not the remote's default branch as such; `freeze` refuses a
   branch HEAD lacks commits of). `.multiversion/base.properties` then holds `upstream=`, `upstream_commit=` and one
   `library.<owner>/<repo>=<tag>` per library release. Every `fork-port.py` run to the end fetches that branch and
   **stops with exit 19** when it has commits after the recorded one that the checkout lacks (or, frozen upstream or
   not, a `--port-release` names another release of a recorded library; a fetch that fails never prompts, it says so
   and compares with the last fetch), naming the keep-current command; `--accept-upstream-drift` continues knowingly and appends the
   drift to `.multiversion/DRIFT.md`. After keep-current merged the new code, `freeze` again.
0a. **Read first**: CATALOG §M and `references/minor-version-deltas.md` (the U and L sections). The first layer
   port re-derived five rows that were already there.
0b. **Guard the names**: `python3 tools/guard-names.py ensure --mod-repo DIR` plus one `--mod-repo` per fork it depends on (its names go into
   the list outside this repository; the hook refuses them in shared rows, helpers and catalogue entries). Before a
   commit here, `guard-names.py check` with the same arguments must pass.
1. **Measure** every targeted line against the base source before porting anything (one compile per line with
   `-Pminecraft_version/-Pneo_version` and the AT re-derived, `burndown-count.sh`): the counts decide the order
   and set expectations. Post them on the fork's tracking issue.
2. **Set up**: `make-fork-layer.py init --repo DIR` (once) and `layer-inventory.py build --repo DIR` (once, see "A
   version accounts for the base's behaviour"), then `add-target --repo DIR --target <t>` -- versions,
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
     PREPARED file (`build/mc<t>/mv/java`, already renamed): overlays are not run through the table. Every overlay
     is a copy of the authors' file that their next release must be merged into, so keep few:
     `python3 tools/layer-sync.py overlays --repo DIR [--target <t>]` counts each target's whole-file overlays and
     lines, classes each against what it replaces (the authors' file through the rename table alone, or the overlay
     it inherits) as **real**, **mechanical** (differs only in names: make it rename rows and delete it) or
     **redundant** (identical: delete it), says how many are mixins / client code, and warns above the budget
     (default 30 per target; `overlay_budget=N` in `.multiversion/ci.properties`). When the authors' code moves,
     `layer-sync.py run` re-merges each overlay of a changed file three ways (keep-current, "A fork with the layer").
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

11. **Hand off a manual test plan** (when the last target of a buildout is done -- one fork, or a library fork
   and the forks that depend on it; each target's own `TEST-ITEMS.md` is already written, "Done" item 5). The gates never look at what a port changed on screen, so the maintainer needs to
   know what to test on which version. Run `python3 tools/layer-test-plan.py --repo DIR [--repo DIR ...] --json` (no
   model, no network). Per fork it gives the versions and, for a layer fork, items per target, each with its sources,
   the targets it applies to, their range and the version where it first appears (a change shared by several targets is
   one item): `stub` (each `STUBS.json` entry, with its review and its `hooks` / `recheck` fields), `overlay` (an
   overlay that replaces a whole authors' file, flagged `mixin` / `client`), `catalogue` (a rename row whose note, or
   the CATALOG entry it cites, says the gates do not cover it, and that fired on the target), `dropped-dep` (a `DEPS.md`
   row that dropped an optional integration), `preview` (a target marked `preview=true`) and, when the fork has
   `.multiversion/INVENTORY.json`, `behaviour` (each base inventory item that is not the same on the target --
   changed, moved, dropped or unaccounted -- with its detail). A single-version fork is
   its one version with no layer section. Add the fork's open issues labelled for the layer (`gh issue list --repo
   OWNER/FORK --label <label> --state open`). Then publish a **Claude Code artifact** (never checked in) for the
   maintainer: per Minecraft version, the changes that need a human eye -- for each, **what changed** and which version
   introduced it, **how to see it** (where to go in game, what to do, what correct and broken look like) and **why the
   gates did not cover it** -- written by you from the item's sources (read the overlay against the authors' file, the
   stub's review). Each shared change is listed once, at its first version, with its range. End each version with a
   checklist of the four or five things to test, in the order an install session reaches them. A mechanical overlay
   (renames only, no `mixin`/`client` flag, nothing drawn) needs no entry: say how many you left out.

## Models and cost

The per-error loop of a layer target is `tools/file-loop.py --layer <t>` (Haiku -> Sonnet -> Opus; a fix lands
as a row in the target's renames.tsv or an overlay, never in the authors' tree), which `tools/fork-port.py` runs.
Keep the session's own context small -- most of a long session's cost is cache reads (`tools/context-profile.py`).

First measured target (a large library mod, 1.21.1 -> 1.21.3, 282 -> 0 errors, 142 mixin targets, Gates A/B green):
about $30 on Opus alone, ~1.3 h, covering the measurement of all lines, the layer tooling, the port and its gates.
