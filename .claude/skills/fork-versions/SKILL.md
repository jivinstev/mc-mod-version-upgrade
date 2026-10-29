---
name: fork-versions
description: Make a fork port build for more Minecraft versions as stacked version branches -- one real branch per version (neoforge-1.21.3 made from neoforge-1.21.1, neoforge-1.21.4 from neoforge-1.21.3, ... neoforge-26.3), each a full single-version port at its own version with its own build, gates, CI and release, a fix or the authors' new code merged upward through the chain with the rename rows applied as a tool -- port one version at a time through the shared rename tables and helper library, gate it like the base, record what it cost and feed every new pattern back into the shared library. A fork still on the removed `.multiversion/` layer is refused by every tool and migrated with the last migrator commit that supports it. Use when the user asks to support another 1.21.x or 26.x version (or "the latest") in a fork, to add, port, gate or check a version, to carry a fix up the versions, to migrate a layer fork (pointed at that commit), or to measure how far a fork is from newer versions.
---

# Build a fork for more Minecraft versions

**Say first which kind of fork this is** (`python3 tools/fork-kind.py detect --repo DIR`), and default to single-version:

- **Single-version** (the default): one port branch, `neoforge-1.21.1`, built, gated, synced and
  published by port-fork, keep-current and publish-port. Nothing in this skill touches it. Do not turn a single-version
  fork into a multi-version one unless the user explicitly decides it gets more versions; then it becomes a fork of
  **version branches** (below), never a layer.
- **Version branches** (the default for a multi-version fork): the port branch plus one branch per further version,
  `neoforge-<version>`, each carrying `.port/<version>/branch.properties`. Everything below is for these.
- **A layer fork** (`.multiversion/base.properties` or `.multiversion/targets/` at HEAD): the overlay route was removed.
  Every tool that takes a fork checkout refuses it (exit 2), naming the last migrator commit that supports it -- see
  "A fork still on the layer" at the end. A version-branch fork whose old layer branch still exists is fine: `detect`
  lists that branch as `legacy_layer=` and everything ignores it; a checkout sitting on that branch is refused too
  (exit 2), told to check out its head branch.

Tools: `tools/known-issues.py` (the known-issues ledger), `tools/fork-kind.py` (detect), `tools/fork-branches.py` (versions, add-target, cascade -- plain, `--plan`, `--apply`, `--apply-decisions`, `--port-ref` --, untaken, review-authors, sync-shared, check-done, freeze, library-record, remake, ci, ci-check),
`tools/fork-port.py` (the stages and the done list, on a version branch), `tools/layer-inventory.py`,
`tools/layer-deps.py`, `tools/ci-gates.py --mc`, `tools/port-ci.py`, `tools/audit-mixin-targets.py`,
`tools/fix-access-transformer.py`, `tools/burndown-count.sh`, `tools/port-cost.py`.

**This is as much about growing the library as about the port.** Every version leaves behind more shared rows,
helpers, catalogue entries and a cost figure; the next fork, and the next version, start from them.

## The shape: one branch per version

- **Branches.** The port branch `neoforge-1.21.1` stays exactly the single-version port it is. Each further version
  is a real branch made from the one below it along the chain -- `neoforge-1.21.3` from `neoforge-1.21.1`,
  `neoforge-1.21.4` from `neoforge-1.21.3`, ..., `neoforge-26.3` -- and holds the **full source for that version**: its
  own gradle.properties versions (a plain `./gradlew build` builds it, no `-Pmc`), its own gates, its own CI (the
  single-version workflow at its version) and its own release. A version can stand on the port branch directly (a
  jump): it then applies the shared library of every targeted version in between.
- **Merges only.** A fix made at a lower version reaches the higher ones by `git merge <lower>` into each higher branch
  in turn (`fork-branches.py cascade`); the authors' new code arrives on the port branch (keep-current) and cascades
  up the same way. Never a rebase, a force-push, or a deleted or moved branch.
- **Rename rows are a tool.** The rows (the shared library's tables, the version's own `renames.tsv`, the generated
  class moves) are kept per version so that a merge can apply them: each file the merge changed that both sides had
  from the rows alone ("mechanical": the version's file equals its rows over the port branch's file) is re-made from the
  port branch's new file through the version's rows, and committed with the merge; a file a person changed is merged
  by git and then again with the parent's two sides put through the rows the version adds (code only, never the
  version's own text), so the authors' new lines arrive in the version's names -- counted as mechanical in the plan --
  and a conflict through the rows too stops the cascade for a person; a shared helper a converted line now names is
  installed with the merge. **That person's merge gets the same step:** a plain
  `git merge <lower>` skips it, so merge with `--no-commit`, run `python3 tools/fork-branches.py remake --repo <the
  version's checkout> --branch neoforge-<v>` (the rows-only files re-made through the version's rows, a conflict there
  included; each other file the merge changed carried through the rows the version adds, code only; staged; the files
  still in conflict listed, exit 1), resolve those by hand through the rows, run it again, then commit.
- **Records, per version.** Branch `neoforge-<v>` carries `.port/<v>/` -- `branch.properties` (the version, its parent,
  the port branch, the upstream freeze, the library pins, the build keys: `prop.*` also written into gradle.properties,
  `toml.*`, `java_version`, `nfrt_version`, `substitute.*`/`exclude.*`/`port_jar.*`, `strip_onlyin`, `library=true` for
  a library fork), `renames.tsv`, `moves.generated.tsv`, `shared-renames.tsv`, `drops.txt`, `shared-helpers.txt`, and
  the records: `GATES.md`, `REVIEW.md`, `STUBS.json`, `COST.md`, `DEPS.md`, `INVENTORY-CHECK.json`, `TEST-ITEMS.md`,
  `DRIFT.md`, `RESIDUE.md`, `BEHAVIOUR.json`, `REMERGE.json`, `DEPENDENTS.json`, `MIGRATED.json` -- plus
  `.port/branch.gradle` (applied by one line at the end of build.gradle: the Java toolchain, the NeoForm runtime, the
  dependency swaps and the client's game directory `build/mc<v>/run`) and `.port/INVENTORY.json` (the base build's
  behaviour inventory). A version's records sit in a directory of its own so that merging the lower branch upward never
  touches them; a branch therefore also carries its ancestors' `.port/<lower>/` as read-only copies. A single-version
  fork never gets any of it: only `fork-branches.py` writes `.port/`, and only on a version branch.
- **A repository holding several mods**: one branch per version holding all of them is fine; records per mod
  (`.port/<v>/<mod>/`) are not built yet -- such a repository is not given version branches until they are (a follow-up).

## Stops -- ask the user

1. Which fork, and which versions ("every stable 1.21.x" is the default, then 26.x).
2. Merging a version branch's PR, unless the fork's fleet entry allows automerge (keep-current's rules).
3. **Never**: push to the authors' repository, force-push, delete or move a version branch, promote or publish a
   release, merge on this repository (only its maintainer merges).

## Done: one definition, fixed before the buildout starts

A version is **done** when, and only when, all of these hold (`fork-branches.py target_done()`; ask it with
`python3 tools/fork-port.py --repo DIR --target <v> --check-done` -- or `fork-branches.py check-done` -- exit 0 or 18 with
every open reason; on the branch's own checkout it also adds a `?` row to `REVIEW.md` for every item without one (and an
`item` row per unaccounted inventory id) for the fresh reviewer, never a verdict; off the branch it reads the branch's
own commit and writes nothing; the parent branch is read from origin when the local one is behind it, after a fetch):

1. it compiles at 0 errors in every source set and the mixin and event-listener audits are clean (the gates stage runs
   only then);
2. the full gates are green **including Gate C**, recorded in `.port/<v>/GATES.md` at a commit HEAD contains (not an
   edited tree), with nothing that can change the jar changed since: everything but the records, documents and CI files,
   and `branch.properties` only by its build keys (a library pin or the freeze does not make it stale);
3. no `STUBS.json` entry is unreviewed;
4. `.port/<v>/REVIEW.md` is complete for the commit it names: an **independent fresh-context review** with one verdict
   (`ok` / `not-ok`) and a note per row, and a row for each of:
   - every `STUBS.json` entry;
   - every **file that differs from the parent branch** at their merge-base (`git diff <merge-base>..neoforge-<v>`),
     kind `changed`, read against the parent's file -- except the files the rows alone made (mechanical: the version's
     rows over the port branch's file, or the parent's file through the rows this version adds, `@OnlyIn` stripped from
     1.21.6 -- an inherited overlay the rows renamed), the build files the tool derives (gradle.properties' versions, the
     mods.toml ranges, a mixin config's `mixin_add`/`mixin_remove` arrays, the hook line), a `.java` file the port
     branch does not have (a helper or a version-only class: no authors' counterpart), a
     shared-library helper as installed (`shared-helpers.txt`), documents and the records;
   - every file the parent has and this version **deletes** (kind `deleted`), except one the chain's `drops.txt`
     names or a `mixin_remove.` key in its `branch.properties` accounts for (a mixin class the version drops by its config);
   - every **resource group** that differs (kind `resources`: its first three directories, with the digest of its
     changed files -- an edit is a new row);
   - `.port/<v>/renames.tsv` as a whole (kind `renames`, with its digest).

   Plus `reviewer` (the reviewing agent, distinct from the fixer: `fresh agent <session id>, not the fixer`), `date`
   and `commit` (7-40 hex digits; `HEAD` or a branch name is refused), with nothing jar-relevant or reviewed (the version's
   rows, STUBS.json, REMERGE.json) changed since. Any row a reviewer marks `not-ok` -- of any kind, an `item` row too --
   keeps it not done and is reported with its note -- except one whose note names a known-issues ledger issue graded
   S3/S4, open at that version, that lists that file (a ledgered finding: keep-current's "an S3/S4 row is ledgered and
   does not block"); a reviewer may add `missing` (a file that should exist and does not:
   a repair tag, a model) and `shared` (a shared helper the version uses) rows, each then needing a verdict;
5. no unresolved merge (a conflicted file, a merge in progress) and no `REMERGE.json` entry whose file is unedited;
6. the version's manual-test items are generated (`TEST-ITEMS.md`; a run to the end writes it, `--write-test-items`
   writes it alone on the branch);
7. every item of the base build's behaviour inventory (`.port/INVENTORY.json`) is accounted for on the version
   (`layer-inventory.py`'s account of the branch's own tree) AND the committed `INVENTORY-CHECK.json` says exactly what a
   fresh check says (a release is judged by the committed one: a run rewrites it, commit it -- the run that rewrote it
   still lists it, saying `commit ... (no re-run needed)`: commit it and the next check passes; do not re-run for it);
8. no dependency was built against a library version that is not done (`--allow-undone-library`, DEPS.md
   "Development only") or against a jar copied from a library checkout (`port_jar_dev.`, `--port-checkout`);
9. **a library** (`library=true`): at least one dependent's version passed Gate C against this library's jar, recorded
   in `DEPENDENTS.json` (`fork-branches.py library-record`) for a commit whose jar is current. **A record counts for the
   commit (and tag) it names, never for a later one**: when the library branch moved on with jar-relevant changes after
   the recorded tag, the done check says `library: the dependents' record is for <tag> (<commit>), the branch is at
   <sha> ...` -- release the new commit and record a dependent's Gate C against that release; a branch that moved only
   in records (the record itself, reviews, notes) still has the same jar, and the record counts. Gate C on a library alone
   checks nothing (its spawn and battle phases spawn no entity of its own, so ci-gates.py skips them there) -- nor the
   library's render helpers, which nothing draws through until a dependent's entity does: a library 26.3 Gate C was
   green while a dependent's Gate C crashed in its `LegacyGl.draw` (CATALOG V111). The dependent's Gate C on the
   library's jar, recorded with `library-record`, is what proves them.

A record that names a **layer commit** (a migrated version's GATES.md and REVIEW.md) counts when `MIGRATED.json` maps
that commit to a prepared-tree digest equal to the branch's own: an unchanged, reviewed version stays done when it moves.

A run whose gates are green but that misses 4-9 exits **18** ("Gate C green, NOT done") and adds a `?` row to
`REVIEW.md` for every item without a verdict, and an `item` row for each unaccounted inventory item; it never writes a
verdict. **The list does not grow during a buildout**: a new requirement is a change to `target_done()` and to this
section, made between buildouts.

Four rules keep a done version done: **library first** (a dependent's version builds only against a library version
that is done and released -- the library rule's own reason aside, which the dependent is the proof of: "Library
first"); **upstream frozen** (`branch.properties` records the authors' commit and each library release the buildout
started from; a run to the end stops with exit 19 when either moved: Procedure step 0); **fix lowest, then cascade**
("A bug class found late"); **few, mechanical changes** (prefer rows to hand edits: a hand-changed file is a review row
on every version that changes it, and a merge conflict on every upstream change to it).

**Never start version N+1 before N is done.** `fork-branches.py add-target` and `fork-port.py --target <new>` refuse a new
version (exit 18, nothing written) while the version it stands on is not done, naming what is open.
`--allow-undone-parent` is for development only: recorded as `allowed_undone_parent=` in its `branch.properties`, and the
new version is not done until its parent is.

**The reviewer is a fresh agent with only that version's changes**: `REVIEW.md` lists them; give it the parent's files
the changes stand for, `STUBS.json` and `renames.tsv` -- never the agent that ported or fixed the version, never one
carried over from another version, and not the parent's reviews (an inherited "moved, same effect" review that was
wrong reached eight versions before anyone looked again). **The coordinator starts it**; no tool can check that it did.
It fills `reviewer`, `date` and `commit`, gives each row a verdict and a note that says what it compared -- and for each
hook it accounts for (a mixin target, an injection, an event listener, a shader uniform or sampler of the behaviour
inventory) names the item id in the file's note or fills that id's `item` row -- and the result is committed. A `not-ok`
goes to the version's fixer; after the fix, a fresh reviewer reviews again against the new commit.

## Which versions (and why some are skipped)

`templates/fork-layer/versions.json` is the policy, in data: a 1.21.x version is targeted only if NeoForge published a
**stable** line for it. 1.21.2, 1.21.6, 1.21.7 and 1.21.9 had beta-only NeoForge lines and are skipped (`add-target`
refuses them; `fork-branches.py versions` prints the table, `versions --refresh` re-reads the NeoForge maven). A
version's jar declares exactly its own Minecraft and NeoForge ranges (`[1.21.3,1.21.4)`), so a skipped version is never
claimed. **The 26.x calendar line** follows 1.21.11: 26.1.2, 26.2 and 26.3 are targeted; NeoForge numbers them by the
whole Minecraft version, so both ranges stop at the next patch release (`[26.2,26.2.1)`); they need Java 25 (CATALOG §V1,
`java_version=25`: a JDK 25 on the machine -- `shipped-gate.py` finds it at `JAVA_HOME_25`, `/usr/lib/jvm`, `/opt`,
`~/.gradle/jdks`, ...; the 26.3 client also needs `libEGL.so.1` (`libegl1`) and every client an `XDG_RUNTIME_DIR`:
`tools/round-preflight.py` says what is missing); GeckoLib 5.5+ is `com.geckolib:geckolib-neoforge-<mc>` (a `substitute.` line); 26.2 and 26.3 need
`nfrt_version=2.0.31`. `add-target` writes each of these from versions.json. Read CATALOG §V before porting a 26.x version.

**The 26.1.2 entityCutout swap (CATALOG V60) is decided by the parent's line, never by a row or a person.** From 26.1.2
`entityCutout` does not cull (it is 1.21's `entityCutoutNoCull`), `entityCutoutCull` does, `entityCutoutNoCull` is gone
and `entityCutoutNoCullZOffset` is `entityCutoutZOffset`. A swap row cannot carry it (applied twice it swaps back; a file
changed by hand is never re-made through the rows). On a version branch at 26.1.2 or above whose parent builds below it,
`python3 tools/fork-branches.py cutout-check --repo <its checkout> --branch neoforge-26.1.2 [--fix]` pairs every call with
its parent version's line and names it by what the parent's call meant; `--fix` renames the ones whose meaning was lost
(a right tree is left as it is). Only a sure pairing is renamed -- an equal line, or a parent call with the same argument
text; a call paired by position only is listed `decide <file>:<line>` with the parent call it may be. It runs by itself
where code crosses that boundary -- `add-target`, the rows' own output, the cascade's merge and `remake` rename;
`fork-port.py`'s moves stage only lists -- so run it by hand after resolving a boundary conflict yourself. **A culling
fix made on purpose on the 26.x branch** (a defect inherited from the parent, fixed only there) is held by the cascade
and `remake` as a decision, not reverted, while the parent's line is unchanged; `cutout-check --fix` would rename it, so
read its list before `--fix`. Decide each listed call: one with no parent line was written on 26.x (keep `entityCutout`
when the model's back faces must show, else `entityCutoutCull`); a held one is kept when the version changed it on
purpose. A single-version fork, or 26.2 over 26.1.2, never takes it.

**Direct jumps.** A version whose middle versions will never ship can stand on a lower branch directly (`add-target
--parent <lower>`, or on the port branch): it applies the shared library of every targeted version in between, newest
first. It skips the middle versions' own gates, not their API changes; its cost has not been measured yet -- try it on a
throwaway clone first.

## Library first (dependencies across repositories)

A dependent fork's version T is ported only after the library fork's T is **done** (its list above, the library rule's
own reason aside) and **released**: the library's version branch cuts its own pre-release (Port CI's `release` on
`neoforge-T`, tag `<mod>-<ver>-mcT`), and the dependent pins that release's jar by sha256 in `.port/T/branch.properties`
(`port_jar.<g:a>=<url> <sha256>` beside `substitute.<g:a>=<g:a:ver-mcT>`): `python3 tools/layer-deps.py resolve --repo
DIR --target T --port-release owner/lib@<tag>` on the dependent's T checkout. **A pin is kept:** every later resolve
(`fork-port.py`'s setup, a cascade's) keeps each CurseForge/Modrinth pin the block holds as it is, with no lookup; only
`--bump DEP[=VERSION]` / `--bump-all` moves one (the weekly plan's "Dependency updates" boxes say which; a single-version
fork follows the same rule). `layer-deps.py library-done --target T
--port-release owner/lib@<tag>` asks the library's done list at that tag -- the same `target_done()`, run on the tag's
tree with the library's own history (a bare clone under `~/.mmvu/cache/git/`), so the release-side answer and the
checkout's agree. **A dependent never builds against a live library checkout**: `--port-checkout owner/lib=DIR` only
copies the jar that checkout already built (`build/libs/<asset>`, the checkout on `neoforge-T`) and pins its sha256 as
`port_jar_dev.` -- development only: CI cannot fetch it and the dependent's done check refuses it until it is re-resolved
with `--port-release`. Once the dependent's T passes Gate C against the library's jar, record it in the library:
`python3 tools/fork-branches.py library-record --repo LIB --target T --dependent DEP` (both checkouts on their T
branches; it reads the dependent's green GATES.md and its pin of this library's release) -- commit the library's
`.port/T/DEPENDENTS.json`; that is what makes the library's version done.

**Don't exceed your dependency.** A dependent's version T may cut its pre-release pinned to the library's T
pre-release (that pair is what the owner plays; the library is only really tested through it). This is how the library
rule's loop is broken: `layer-deps.py resolve --port-release owner/lib@<tag>` takes a version-branch library's
**pre-release whose gates are green at the tag** (GATES.md, a green Gate C) although that version is not done yet -- no
`--allow-undone-library` -- and marks it in the dependent's `DEPS.md` ("Library pre-release (not done)"). The dependent
then gates and cuts its pre-release against it, `library-record` records its Gate C in the library (which makes the
library's version done once the rest of its list holds), and the dependent re-resolves the same tag: its version is not
done while the mark stands. A person who played the pair may still promote both (`port-ci.py promote` notes the mark,
it does not refuse), and `port-publish.py` publishes the dependent's tag only once each library release its workflow pins
at the tag is promoted and published and is still the jar its build checked (sha256). A full Release, or a pre-release with red
gates, is still refused. The dependent's T must
not be promoted to a full Release, nor published, while the library release it pins is still a pre-release; promote
both together (`port-ci.py promote --with-dependency owner/lib`, the library promoted too), or the library first.

## CI per version branch

Each version branch gets the same workflow as a single-version port, at its version and with the full gate set:
`python3 tools/fork-branches.py ci --repo DIR --target <v> --modid ID` prints the `tools/port-ci.py` command (the port
branch's workflow read back, each sibling `--dep-jar` replaced by this version's pinned
library release and each `--dep-jar-variant` of it moved to the same release and coordinates -- refused, exit 2, when
the release's asset names do not show which asset the variant is -- a library's Gate C without spawn and battle, and
the newest published Port CI kit rather than the port branch's older pin; `--kit-version N` pins kit N; and
`--branch neoforge-<v>`, so the triggers name the version branch even when the command runs on a `keep-current/*` work
branch -- `port-ci.py` alone also takes `neoforge-<mc>` on a checkout holding `.port/<mc>/`); `--run` runs
it in a worktree of the branch and commits there. Push the branch; nothing is pushed for you. A tag `<mod>-<ver>-mc<v>` on the branch releases that version (publish-port's single-version path). The
cascade keeps each branch's own `.github/workflows/port-ci.yml` and install manifest when it merges the lower branch:
regenerate them with `ci` when a lower branch's change to them should reach this one. `ci` adds a `--variant-jar
*_<v>.jar` for each `.port/<v>/shipped-expect-<v>.txt` the branch keeps (`cf` for `shipped-expect-cf.txt`), so the
workflow's `shipped jars` job boots that jar too -- a workflow that boots only `_mr` is green while the local `_cf`
check is red. `ci --all` does every version branch; `ci-check --repo DIR` warns about each branch whose workflow pins an
older kit than the newest published one or lacks such a job (the weekly round runs it), and about **the head branch**
when its workflow pins an older kit or its push trigger ignores no records (`ci --head --run` regenerates the head's
from itself, only the kit moved; it refuses a `--modid` other than the workflow's, or an origin/HEAD that is not the
workflow's authors' branch); `sync-shared --ci MODID` regenerates the synced branches' workflows, and without `--ci`
prints the same warnings. **A version branch's push trigger also ignores its round records** -- `.port/*.md` and
`.port/*/*.md` (GATES.md, REVIEW.md, COST.md, DEPS.md, DRIFT.md, TEST-ITEMS.md, `.port/DECISIONS.md`: Markdown outside
`src/`, never jar content, never read by a gate) beside the ledger and the root README every workflow ignores -- so a
push of only those runs no full Port CI; `branch.properties`, the rows, STUBS.json, REMERGE.json and the shipped-jar
expectations still run it, as do every pull request, tag and dispatched release. Only where the checkout holds
`.port/<mc>/branch.properties` (port-ci.py renders it there; a single-version fork's `.port/<mc>/shipped-expect-*.txt`
does not count): a single-version fork's workflow is byte-for-byte what it was. `ci-check` names a version branch whose
workflow lacks it, and counts the head in its summary.

## Run the tool; handle only the residue

`python3 tools/fork-port.py --repo DIR --target <v> [--budget 15] [--gates auto|none|ab|full]` works on branch
`neoforge-<v>`: its setup checks the branch out (making it with `fork-branches.py add-target` when it is new) and runs
the stages -- setup (the behaviour inventory from the port branch when missing, `layer-deps.py resolve`, the data
conversions a version needs written into its own resources), stage (Minecraft staged, the access transformer fixed in
place), moves (the class moves from the parent version, staged in a worktree, kept in `moves.generated.tsv` AND applied
to the source: a file the rows alone made is re-made whole, a hand-changed file in its code only, its comments as written;
then the converters whose change lands in this hop, `tools/mechanical-hop.py`'s `LADDER` -- the hook-signature rows of
`convert-override-signatures.py --crossing <parent>..<v>` (1.21.2: `releaseUsing`, `customServerAiStep`; 1.21.5:
`appendHoverText`, `hurtEnemy`, `inventoryTick`), each body kept in a private 1.21.1-shaped copy, so the compile loop
never hand-ports them; below 26.1.2 a hop over 1.21.2 / 1.21.5 names `convert-gear-tiers.py` as an ADVISORY in the stage
line -- port those sites by hand by CATALOG V92's rules (a sword's tool rules, `instanceof` widened only where unbound) --
because its shim needs 26.1.2's API (only when the tree names a 1.21.x gear class); a vetoing `hurtEnemy` (one that can
return false, which spared the durability on 1.21.1) is REFUSED and named, never converted; a crashing converter stops
the stage, exit 10; idempotent, so a resume converts nothing twice. **At the hop only:** the cascade's merges do not run
the converters, so authors' code merged up later with an old-shape hook goes to the compile loop -- run
`fork-port.py --target <v> --from-stage moves` on that version to convert it), measure, loop (the compile loop edits the branch's source; a row it keeps joins `renames.tsv`), audit,
gates (`ci-gates.py --mc <v>`), cost. It stops only when a stage cannot finish: a STOPPED block, exit 10 (compile), 11
(Gate A/B), 12 (Gate C), 13 (mixin audit), 14 (the checkout is in use), 18 (an undone parent or library) or 19 (the
authors' branch moved since the freeze), and `.port/<v>/RESIDUE.md` with the remaining errors by CATALOG entry and the
`--from-stage` command that resumes (a resume runs on the branch's checkout: check it out first). **You handle only what
RESIDUE.md lists**, and **every fix you make that is not specific to this mod goes into the shared library
(`templates/fork-layer/shared/<v>`) or a converter** (step 8).

**One fresh residue fixer per Minecraft version**, with only that version's RESIDUE.md / STUBS.json, the rules (edit only
this branch, generic shapes to the shared library, no pushes) and the resume command. Never carry one agent from version
to version for distinct porting work (measured: one fixer resumed across six versions grew to ~650k tokens, ~$120) and
never let it write the version's REVIEW.md. The exception is **the same change carried up**: one worker MAY take one
re-pin, or one fix cascaded up, through several versions while its context stays modest (measured: one fix through five
versions, ~$4 per version); start a fresh one when a version needs porting work of its own. Each worker hands back a short
summary (at most ~40 lines: what changed, the gate verdicts, the open rows, the commit shas), and the session that started
it keeps only that -- the orchestrator never ports, gates or reviews a version in its own context, and waits for a gate
run or a CI run in one blocking command, never by polling (keep-current, "The weekly round": each idle gap over the
5-minute cache re-writes the whole context). **The cheapest correct gate sequence** per version (the done list decides what
is stale): the build-changing inputs first (a re-pin), the cascade's `--gates ab`, the review and its fixes, then ONE
`fork-port.py --repo DIR --target <v> --from-stage measure --gates full` on the final tree (a re-run from `measure` has no
compile-loop budget unless `--budget` gives one: it gates, it never edits); a records-only commit
(GATES.md, REVIEW.md, COST.md, the ledger, the README, the freeze or a library record) re-runs nothing, a re-pin re-runs the
gates but not the review (keep-current, "The weekly round" step 4).

**Clean-room rule for shared helpers.** A fixer that writes a shared helper re-implementing a third-party class may read
that class's API (signatures, `javap -p`, javadoc) but not its implementation bodies; the similarity gate
(`tools/check-similarity.py`) refuses template Java that matches the sources on the machine. Reading implementations
stays fine for porting the authors' own code in their fork.

**A version accounts for the base's behaviour.** `.port/INVENTORY.json` lists every mixin target and injection, event
listener, render-path class, render API use, core shader with its uniforms and samplers, and registry of the base build
(the port branch: `layer-inventory.py build --repo DIR` on a version branch builds it from there). For each version,
`python3 tools/layer-inventory.py check --repo DIR --target <v>` (on its checkout) says per item `same` (also through the
rows), `changed`/`moved` (accounted only by a reviewed STUBS.json entry, an `ok` REVIEW.md row naming the file -- for a
hook item, naming the id -- or a `BEHAVIOUR.json` entry with `where` and `review`), `dropped` (a `drops.txt` comment or a
`BEHAVIOUR.json` reason) or `unaccounted` (exit 16). A file counts as changed when it is not what the rows alone make of
the port branch's. Record each in `.port/<v>/BEHAVIOUR.json`; a version reads its ancestors' copies too.

**Why Gate C is on the list.** Gates A/B and the audits cannot see what only a client builds (models, render layers,
shaders); every dependent version so far that was green at A/B still failed in a real client. `--gates auto` runs the full
gates until the version's GATES.md records a green Gate C, then A/B; a full run that is not green is written to
`GATES-RED.md` and never touches GATES.md (the last green record stays): while GATES-RED.md is there the version is not
done and `auto` runs full again; a green full run deletes it.

**Commit a fix before the gate run that certifies it**: GATES.md names the commit it tested, and an edited checkout is
recorded as `<sha> + uncommitted: <files>` (the run's own records aside).

**One run per checkout, one Gate C per machine.** `fork-port.py` and `ci-gates.py --mc` take `build/.fork-port.lock`
(exit 14 for a second run on the same checkout); every Gate C client takes the machine-wide lock
(`~/.mmvu/locks/gatec.lock`, its holder named in `gatec.lock.holder`) and a second one waits -- at most
`MMVU_GATEC_LOCK_TIMEOUT` seconds (default three hours), then stops as an environmental failure naming the holder. One agent per checkout; agents on one machine use their own scratch
directories and PID files (a unique name per agent, and the command line checked before signalling a PID -- one agent
once killed another's memory loop through a shared `dropcaches.pid`).

**Spend guard.** Before the loop, `fork-port.py` refuses (exit 10) when the starting error count exceeds `--max-errors`
(default 100 per dollar of `--budget`) unless `--confirm-spend`. **A resume re-measures**: a `--from-stage` run never
trusts an earlier run's compile count.

## A bug class found late: fix it at the lowest version, then cascade

Fix it once, at the lowest version where it exists (and in the shared library when it is generic), commit on that
branch, then:

    python3 tools/fork-branches.py cascade --repo DIR <lowest>..<newest> [--gates none|compile|ab|full]

(`fork-port.py --repo DIR --chain <lowest>..<newest>` runs the same on version branches.) It merges the lowest branch into
the next one up, that into the next, ... with a merge commit each, re-makes the mechanical files through each version's
rows (and merges each hand-changed file again through the rows the version adds, so the authors' new lines arrive in
the version's names; a shared helper a converted line names is installed), runs the no-model gates on each merged branch (fork-port.py from the measure stage, no budget; default `ab`), and
stops at the first conflict (the merge undone, the files named) or red branch, naming the command that cascades on from
there. A file the authors changed (the port branch's diff) that the parent version does not carry reaches the version
through no merge, so the cascade carries it itself: a `.java` file the rows alone made is re-made through the version's
rows, a new one the rows keep is added, a resource whose copy is still the authors' takes their new one, and a file
the version dropped stays dropped (a resource it dropped too, where git would stop on a modify/delete conflict). One
the version **changed by hand** is not touched: the merge is committed with the version's file as it was, the file
gets an open entry in `.port/<v>/REMERGE.json` holding the authors' diff, and the cascade stops there and prints that
diff. Apply it by hand through the version's rows (the diff is in the port branch's names), commit, and cascade on
(`cascade <v>..<newest>`). **One invariant decides when an authors' change counts as taken at a version**: its added
lines, through the version's rows, are in the version's file (in order), or an explicit clear names it
(`cascade --apply-decisions`, or removing its entry). An entry never closes because its file changed for another
reason, and `port_taken=` (below) moves on only after every change in the range is taken or held by an entry: a merge
that is undone commits nothing, so the next run offers the change again, and a merge a person makes by hand (keeping
ours) is caught by the next cascade, which records the change it lacks. With a real conflict as well, the merge is
undone and the same diffs are printed to apply after the hand merge. Start **one fixer only where it stopped**. A merge the cascade made and did not gate green (red, or stopped for a hand re-apply) stays
on that version's branch and worktree (`build/fork-branches/<v>`); the cascade records it (`mmvu-cascade-ungated.json` in
the clone's git directory), so the next run gates that version before it calls it up to date, and clears it once green
(`--gates none` says "NOT gated" and leaves it). Its worktree is reused as the stopped run left it when the only
uncommitted changes are that run's own records in `.port/<v>/` (GATES.md, GATES-RED.md, RESIDUE.md, COST.md, ...); a
worktree whose directory was removed is pruned and made again; any other edit there is refused, naming the files. Every version the cascade merged a jar-relevant change into is **not done** afterwards: its `GATES.md` and
`REVIEW.md` name a commit before the merge, so it needs a green full Gate C again and a fresh reviewer who re-reads the
files the merge changed and names the merge commit (the tool carries no verdict forward by itself; its rows are still
only the files that differ from the parent at the new merge-base, so a file the version took unchanged from its parent
is no row). Nothing is pushed: push the merged branches (or open their PRs) yourself -- one version at a time,
lowest first, a version's PR opened only after the one below it merged (keep-current, "A fork with version branches"
step 3). A conflict at the 1.21.x -> 26.x boundary (the rename between 1.21.11 and 26.1.2) is expected: resolve it by hand
in that version's worktree, through its rows, run `cutout-check --fix` there (above), commit, and cascade on.

**The shared library moved** (a row or a helper changed in `templates/fork-layer/shared/<v>/`): `python3
tools/fork-branches.py sync-shared --repo DIR [--target <v> ...]` rewrites each version's `shared <v>` sections of
`.port/<v>/shared-renames.tsv` from the library, re-makes the files the old tables alone made, updates each installed
helper the version has not edited (an edited one is named and left alone), adds a helper the tree now uses and
commits on each version branch (in its checkout, or `build/fork-branches/<v>`); it pushes nothing. Every targeted
version from the base up to each branch's own counts, so a version whose library has helpers and no table is synced
too. Without `--target` it syncs every version (the default, and the safe one): a version left out keeps its old copy
of the tables, so a cascade from below re-makes the synced files back through them, and a partial run names those
versions in a WARNING. Then cascade. A section of `shared-renames.tsv` edited by hand (equal to no text the library's
table ever had) is left alone and named: move the fork's own rows to `.port/<v>/renames.tsv` and sync again; so is a
section that differs while the library's history cannot be read (a shallow clone that cannot be deepened): an edit
cannot be ruled out, so it is never rewritten on a guess. An UNLISTED helper copy that equals a text the library once
shipped (an `MV-STAND-IN` copy too) is a library copy and is updated; a listed copy that differs from its
listing (an edit, or a revert to an older library text) stays edited. `sync-shared --check [--json FILE]` writes nothing and touches no checkout: per version what a sync
would change, each hand-edited copy whose template moved (reconcile it by hand), the fork's own copies of what the
library ships (adopt candidates) and the code marked `MV-AHEAD-OF-TEMPLATE` (ahead of the template: a lesson to
harvest); the weekly round runs it at `init` and syncs only what it names. A cascade keeps a version's installed helper
copy when the version below only moved its own installed copy to its library's (two versions' copies are never merged).

## Known issues: one ledger at the head, graded S1-S4

Every known defect of the fork lives in **one ledger**, `.port/KNOWN-ISSUES.json`, committed on the **head branch**
(the root port branch, `neoforge-1.21.1`; a single-version fork's only branch). A multi-mod repository keeps one per
mod, `.port/<mod>/KNOWN-ISSUES.json` (`--mod <mod>`). `tools/known-issues.py` writes it (atomically, sorted keys,
2-space indent); nobody edits it by hand. It is a record, not port code: Port CI's push trigger `paths-ignore`s it
(a workflow port-ci.py rendered since the ledger exists: `fork-branches.py ci-check` names a head branch whose workflow
is older, and `fork-branches.py ci --repo DIR --head --run` regenerates it, only its kit moved),
it never makes gates or a review stale, and `fork-branches.py cascade` never carries it to a version branch, so a
ledger-only commit goes straight onto the head branch. README and ledger edits never trigger CI or make gates
stale (the root `README.md` only; a README under `src/` is jar content). Read it from there: `--repo DIR --ref neoforge-1.21.1`.

Each entry: id, title, the severity and the rubric inputs it came from, an owner `override` (recorded with its
reason), the lowest affected version, a status per Minecraft version (`open`, `fixed@<commit>`, `wontfix:<reason>`,
`duplicate:<ID>` -- tracked by another issue of the ledger, rendered `dup of <ID>`, never open --, `n/a`; a version not listed is not affected), the source (authors, review, gate, player) and notes. `grade` derives
the severity from the inputs:

| Severity | Rubric inputs | Meaning |
|---|---|---|
| **S1** | failure crash, kick, data, hang, security or build (anything else aside), reach not latent | a crash, kick/disconnect, world or player data lost, a hang, a security hole, a version that does not build; blocks publishing |
| **S2** | one of those failures with reach latent; or failure functional, reach normal, visibility always/common, no workaround | a crash on a path nothing calls yet (fixed by the next rounds, never parked, not blocking publishing); a core feature broken in normal play, e.g. invisible GUI text |
| **S3** | anything else: cosmetic (S3 at most), rare, one specific feature, or a workaround | cosmetic, an uncommon path, or avoidable |
| **S4** | visibility invisible, or reach unusual or latent | invisible to players, or needs an unusual setup, other mods or debug options |

`latent`: nothing calls the code today, so no player can reach it yet. A crash, kick, data loss, hang, security or build
failure is never graded below S2 by an override unless it is the owner's: `grade --override S3|S4` refuses one without
`--owner`, which the weekly round passes only for the owner's `/grade` reply on the plan issue.

`security`: an exposed credential or secret, a token leak, or an exploit or privilege path. `build`: the version does
not compile or produce a jar. `functional`: a feature does not work, or information or controls the player needs are missing, unreadable or
unusable (invisible text, a missing UI element). `cosmetic`: the look is wrong but nothing the player needs is lost
(a wrong colour tint, a texture seam); it is S3 at most.

An entry may be scoped to one platform's jar, `"platforms": ["cf"]` (or `["mr"]`; `add --platform cf`): only that
jar is affected. Without the key, every platform is. `list` and `release-notes` take `--platform`; `known-issues.py
blocks-publish --version V --platform P` prints the open S1s against that platform's jar and exits 1 when there is
one. `reopens` ignores platforms.

**Only an S1 reopens a done version.** A finding at a version that is already done (`fork-port.py --check-done`)
reopens it only when it is an S1: fix it at the lowest affected version and cascade only that fix. Anything else goes
into the ledger at its lowest affected version and the run goes on. `known-issues.py reopens --version V --severity S`
(or `--id ID`) answers: exit 0 yes, exit 1 no.

    python3 tools/known-issues.py add     --repo DIR --title T --failure crash|kick|data|hang|security|build|functional|cosmetic \
        --visibility always|common|rare|invisible --reach normal|feature|unusual|latent --workaround yes|no \
        --lowest 1.21.4 --status 1.21.4=open --status 1.21.5=open [--source review] [--file PATH] [--notes N]
    python3 tools/known-issues.py grade   --repo DIR KI-3 [--reach feature] [--override S2 --reason WHY --by owner [--owner]]
    python3 tools/known-issues.py status  --repo DIR KI-3 --set 1.21.4=fixed@<commit> --set 1.21.5=wontfix:<reason>
    python3 tools/known-issues.py status  --repo DIR KI-3 --set 1.21.10,1.21.11=fixed@<commit> --set 26.1.2..26.3=fixed@<c2>
    python3 tools/known-issues.py list    --repo DIR --ref neoforge-1.21.1 [--version 1.21.5] [--min S2] [--open]
    python3 tools/known-issues.py select  --repo DIR [--budget 3] [--touched PATH ...] [--version V ...] [--add ID] [--remove ID]
    python3 tools/known-issues.py render-issue  --repo DIR          # the pinned issue: issue by version, by severity
    python3 tools/known-issues.py release-notes --repo DIR --version 1.21.5 --tag <tag> [--min S3] [--platform mr|cf]
    python3 tools/known-issues.py page --repo DIR --fork owner/repo   # the project page's Known issues: open S2s, S3/S4 behind a link
    python3 tools/known-issues.py issue --repo DIR --slug owner/repo  # create/update (and pin) the Known issues issue from the ledger
    python3 tools/known-issues.py blocks-publish --repo DIR --version 1.21.5 --platform mr|cf   # exit 1: an open S1

`add` numbers a new entry in the ledger's own numbering (its most used prefix, zero-padded as it is: `EXM-025` after
`EXM-024`; `KI-1` for a new ledger) unless `--id` names it, and warns on stderr about a likely duplicate -- an entry that
names the same `--file` with a similar title, or any with nearly the same title: read it, and add to that entry instead
when it is the same defect. `status --set` takes one version, a comma list or `FROM..TO` (both ends and every version
the entry lists between them).

`select` is the default choice of what a run takes: every open S1, then S2s lowest affected version first up to
`--budget`, then any S3 in a file the run already touches; the owner's `--add` / `--remove` apply last and the output
says who chose each item. `release-notes` lists the issues open for that version at the tag, S1-S3 by default (with
`--repo`, a `fixed@<commit>` that is not in the tag counts as open there).

The ledger stays on the head branch everywhere: `cascade` never merges it up, and `add-target` never copies
it (nor the weekly plan's `.github/workflows/weekly-plan.yml` and `.github/weekly-plan.json`) onto a version branch
they make from a head that has it. The weekly cycle (keep-current, "The weekly cycle") runs `select` every Tuesday in
a zero-model plan issue (`known-issues.py render-plan`) that the owner answers, and the Wednesday round starts from
`known-issues.py read-plan`; `known-issues.py render-ports --write` keeps the README's Ports block (one row per
version, between `<!-- ports:begin -->` and `<!-- ports:end -->`) current. `port-publish.py publish` holds back a
platform an open S1 blocks (`blocks-publish`) and uploads the other.

## Planning the cascade: every decision in one pass, the owner asked once

A cascade must never become "stop at one version, ask, resume, stop at the next". The authors' new code (keep-current)
goes up the chain this way:

1. `python3 tools/fork-branches.py cascade --repo DIR neoforge-1.21.1..<newest> --plan [--budget-lines 80] [--out DIR]`
   -- every merge, the port branch through the newest version, on a throwaway bare clone (the real branches and the
   checkout untouched; no model: git and the rows only, a few seconds on the self-check's four versions). It never stops
   at a conflict: a version above an unresolved file is planned with that file still open. It writes
   `CASCADE-PLAN.json` and `CASCADE-PLAN.md` (default `build/fork-branches/plan/`): the files merged mechanically, and
   one **decision** per file and authors' hunk, however many versions it shows at, each with the authors' diff, our code
   at each affected version, its class and the default decision with its reasoning; and `ISSUE.md` when anything is
   escalated.
2. **The classes** (the tool's mechanical rules; the agent applies the policy behind them):
   - **mechanical** -- resolved by the tool: `git merge-file` of ours with the authors' change, over the version's rows,
     is clean (line drift, or a difference only the rows make).
   - **optional integration** -- a file under `compat/` or `integration/`, or one calling `isLoaded(`. **Absent** at that
     version -- the file names a dependency (its module as a word, e.g. `isLoaded("<id>")`, or its group as a package)
     that the version's `DEPS.md` records as `dropped:`; an unrelated dropped row does not count: the tool keeps ours
     (our stubbed calls work now), logs it in `.port/DECISIONS.md`, and holds the authors' change as a **deferred**
     `REMERGE.json` entry (`"deferred": "<group:module>"`). A deferred entry does not count against done while that
     dependency stays dropped there (an unrelated edit of the file never closes it); once the version's `DEPS.md` no
     longer drops it, the entry counts as open like any
     other, so the authors' change is applied when the integration returns (answer it with `--apply-decisions`).
     **Present**: keep it, apply the authors' change around it.
   - **port change** -- the file has a `STUBS.json` entry (a stand-in, an API rewrite of ours): keep ours, apply the
     authors' change around it.
   - **authors' fix** -- anything else: take their change, adapted through the rows, ours re-applied around it.
     The same deferred default covers a file OUTSIDE the integration package whose authors' change names only classes
     the version drops (`drops.txt`) as one dropped dependency's integration (their package, `compat/<name>/`, names
     it): the plan shows it as `deferred:<group:module>`.
   - **escalated** -- keeping the authors' intent is not feasible at that version (their added code names a class the
     version drops in `drops.txt` that no single dropped dependency owns), or the change is over `--budget-lines`
     changed lines (default 80). Only these go to the owner. The agent may escalate a default too when it finds the
     intent infeasible, saying why.
   The rule behind every default: **keep the authors' design intent**. The fixer agent makes the defaults and records
   each with its reasoning (the answers file below; the tool writes them to `.port/DECISIONS.md`, append-only).
3. `cascade ... --apply [--gates ab]` -- the same merges on the real branches. Mechanical items are resolved; a default
   the tool can carry out is carried out and logged; every other decision file is merged **without** the authors'
   change and gets an open `.port/<v>/REMERGE.json` entry (the id, class, default, the diff). Everything else goes on,
   the versions above included: blocking is per file, not per version. An open entry travels up with its file (a
   higher version that takes the file from its parent carries the entry open too, never a silent loss), and only a
   version with an open entry stays not done. A red branch still stops (start one fixer there). Every cascade (and
   `--apply-decisions`) first fetches `origin` and compares each branch it reads: a stale local branch behind origin is
   fast-forwarded (a checked-out one only when clean; `--plan` reads origin's commit), and one that diverged refuses the
   run -- merge or reset it first, never cascade from a wrong merge base.
4. **Ask once.** File `ISSUE.md` as ONE issue on the fork: each escalated item with its versions, the authors' change,
   ours at each version, the options (`take`: the authors' file through each version's rows; `keep`: ours, their change
   not taken; `patch`: a diff in the port branch's names, applied through each version's rows; `content`: the file, per
   version or for all; `deferred:<dependency>`: ours kept and the authors' change held as a deferred entry while the
   version drops that dependency -- refused at a version whose `DEPS.md` does not drop it) and a recommendation, plus a
   machine-readable `cascade-decisions` block.
5. **Resume in one shot.** `cascade --repo DIR --apply-decisions FILE` (the block saved to a file, the agent's own
   default answers added to its `decisions` list with `by` and `reason`) applies each answer at every version holding
   that decision, oldest first, clears the entries, logs each in `.port/DECISIONS.md`, then runs `--apply` over the
   range (merging the answers up) and writes the new plan. An answer that makes a new conflict higher up comes back
   through the same flow as at most one follow-up issue. With no earlier `--apply` (the weekly round runs it straight
   after `read-plan`), it runs `--apply` first, so each decision has its entry; a `patch` with no diff (read-plan's
   default for an unanswered decision) leaves its entry open, as `--apply` does, until the fixer writes the diff.
6. **Review once.** A version that took the authors' change through its rows only (no file of it a review row) needs
   no fresh full review: `python3 tools/fork-branches.py review-authors --repo DIR --range <port A>..<port B> --reviewer
   "fresh agent <id>, not the fixer" --note ...` records one review of that diff (by digest) in
   `.port/AUTHORS-REVIEWS.md` on every version branch, and each such version's done check accepts its `REVIEW.md` as
   current through it. A version whose own files changed still needs its fresh review. Gate C still runs per version.
7. **Carry a fix, not the head.** A cascade from a version branch (`cascade --repo DIR 1.21.4..26.3`) carries only what
   that branch took: each merge's port commit is its merge-base with the port branch, so the head's untaken code (the
   authors' release merged into the head and not cascaded yet: `fork-branches.py untaken --repo DIR --upstream
   REMOTE/BRANCH` lists it) reaches no version. A fix at the head's own version, made while that code is not to be
   taken, goes on a branch cut from the lowest version's `port_taken` and up with `cascade --repo DIR
   neoforge-1.21.1..26.3 --port-ref fix/<id>` (refused while the branch holds authors' code that version has not taken),
   then into the head by its own PR.

`port_taken=` in `.port/<v>/branch.properties` (written by add-target and every cascade merge, and by the migration on a migrated fork) is the port
commit the version has fully taken: the carry of the authors' change to files the parent does not carry starts there,
so a change a run did not take (a merge undone, then merged by hand) is offered again by the next run, and an open
entry spans from the earliest change it has not taken. A branch written before the key starts from its merge-base.

## Procedure

0. **Upstream first, then freeze it** (before a buildout, single fork or library + dependents): run keep-current for the
   fork AND each library fork it depends on, so the port branch carries the authors' newest code. Then record where the
   buildout starts, on each version branch as it is made: `python3 tools/fork-branches.py freeze --repo DIR --target <v>
   [--upstream <remote>/<branch>] [--library owner/repo@TAG ...]` (the checkout on that branch; a new version inherits
   its parent's record). Every run to the end stops with exit 19 when the authors' branch has commits after the
   recorded one, or a `--port-release` names another release of a recorded library; `--accept-upstream-drift` continues
   and appends to `.port/<v>/DRIFT.md`. A freeze whose authors' branch cannot be read (the remote missing or never fetched)
   stops the same way: never remove a remote to get past it. A weekly round that does not take the authors' code (its
   `round/frozen.json` says `take_authors: false`) is not drift: `fork-port.py` reads `round/frozen.json` from the working
   directory (or `--untaken-ok FILE`), records the drift in `DRIFT.md` as left out by the plan and goes on, and `cascade
   --untaken-ok round/frozen.json` passes it to each version's gates; another library release still stops. A `--library`
   pin alone never re-checks the authors' branch; re-freezing a moved upstream needs `--accept-upstream-drift`.
0a. **Read first**: CATALOG §M and `references/minor-version-deltas.md` (the U and L sections). The first port re-derived
   five rows that were already there.
0b. **Guard the names**: `python3 tools/guard-names.py ensure --mod-repo DIR` plus one `--mod-repo` per fork it depends
   on. Before a commit here, `guard-names.py check` with the same arguments must pass.
1. **Measure** every targeted line against the base source before porting anything (one compile per line with the
   versions set and the access transformer re-derived, `burndown-count.sh`): the counts decide the order and set
   expectations. Post them on the fork's tracking issue.
2. **Set up**: `python3 tools/fork-branches.py add-target --repo DIR --target <v>` makes `neoforge-<v>` from the nearest
   lower version branch (or the port branch): versions, ranges, Java, NeoForm and GeckoLib from versions.json, the shared
   library of each targeted version in between applied (its rows over the source, its helpers in the fork's
   `mv_package`, its access-transformer lines), `@OnlyIn` stripped from 1.21.6 (CATALOG M35). `fork-port.py --target <v>`
   does it as its setup. **CI, once per version, before it is released:** `fork-branches.py ci` (see "CI per version
   branch").
3. **Access transformer**: re-derive the branch's own `src/main/resources/META-INF/accesstransformer.cfg` with
   `fix-access-transformer.py --overrides-from <log>` until `createMinecraftArtifacts` succeeds (the stage stage does
   this, bounded). Never widen a method vanilla subclasses override to a protected one's caller.
4. **Burn down** (`./gradlew compileJava` on the branch, count with `burndown-count.sh`), cheapest first:
   - shared rows already apply; add a **row** to `.port/<v>/renames.tsv` for this mod's own call shapes AND apply it to
     the source (rows are kept so the next merge applies them too). A regex runs before the type renames; identifiers
     can contain `$`.
   - a **helper** class when a removed method can be stood in for (in `mv_package`, a shared one in the library).
   - an **edit** of the file only for a real semantic change: every hand-changed file is a review row and a future merge
     conflict, so keep few.
   - a mixin new to the version: add it to the mixin config on the branch (it reaches the versions above by the cascade);
     one that only this version's method shape takes is removed again on the next branch up.
   - **Every source set** compiles (`compile<Set>Java` for each), and after a run of failed compiles delete
     `build/classes` before trusting a green compile.
5. **Mixins**: at 0 errors, `audit-mixin-targets.py DIR <sources.jar> --sources=src/main/java` must say all targets match
   (the audit stage).
6. **Gates**: `ci-gates.py --repo DIR --modid ID --base <author ref> --mc <v>` on the branch (author's build, Gate A,
   data, payloads, Gate B, Gate C phases). Run the same gates on the port branch as the control: a red that is red there
   too is not the version's. A harness compile error on a new version is fixed once in
   `templates/upstream-harness/versions/<v>.renames.tsv`, for every fork.
7. **Record the cost** (required): `fork-port.py`'s cost stage appends `port-cost.py --target <v>` to `.port/<v>/COST.md`
   for the version's work window.
8. **Harvest** (required, before calling the version done): move every row and helper that is not specific to this mod
   into `templates/fork-layer/shared/<v>/` (`@MVPKG@`/`@MODID@` placeholders); write each new pattern as a CATALOG §M
   entry and an L entry in `references/minor-version-deltas.md`.
9. Commit on the version branch, push it (a PR when the fork's rules want one), and report: errors before/after, gates
   with the control, cost, what was harvested, what is left.
10. **Keep it current** (once per fork, when its first version is green): give the fork's keep-current fleet entry
   `"version_branches": true`, and re-run keep-current's setup for its fork set (the weekly plan workflow and the
   Wednesday round). keep-current then merges the authors' code into the port branch and cascades it up every version
   branch ("A fork with version branches"), from the plan issue's frozen input ("The weekly round").
11. **Hand off a manual test plan** (when the last version of a buildout is done): each version's `TEST-ITEMS.md` lists
   what no gate judges (its stubs, its changed files flagged mixin / client, dropped integrations, behaviour items that
   are not the same). Publish a **Claude Code artifact** (never checked in) for the maintainer: per Minecraft version, the
   changes that need a human eye -- what changed and in which version, how to see it in game, why the gates did not
   cover it -- written from the items' sources, each shared change once at its first version, and a four- or five-item
   checklist per version in the order an install session reaches them.

## A fork still on the layer

The `.multiversion/` overlay route (one layer branch building every version) was removed. On a checkout with the layer at
HEAD every tool stops with exit 2 and says so, naming **baa092b62eae**, the last commit of this repository that supports
it (`LAST_LAYER_COMMIT` in `tools/fork-kind.py`). To move such a fork to version branches, when the maintainer decides it:
check that commit out in a separate clone of this repository and run its migration there -- `python3
tools/fork-branches.py migrate --repo DIR --layer-ref origin/neoforge-1.21.x --dry-run`, then without `--dry-run`, as
that commit's fork-versions skill ("Migrating a layer fork to version branches") describes -- and push the branches it
made. From then on use this version: the migrated branches' `.port/<v>/MIGRATED.json` keeps their layer-era GATES.md
and REVIEW.md counting, and the old layer branch is history (`fork-kind.py detect` lists it as `legacy_layer=`). Do not
make a new fork a layer.

## Testing the tool itself

`python3 tools/fork-branches.py --self-check` is the fast suite CI runs: every named case (the review findings
t6, t7, t8 and t11 included), the line-ending unit cases, on the frozen version-branch fixture (`tools/fixtures/version-branches.fi`, a migrated
fork's branches) imported once per run.
Before a release of the tool (or after changing the cascade, the REMERGE records or `port_taken`) run it with
`--deep`: the four-version cascade `--plan` / `--apply` / `--apply-decisions` flow, 200 random
cascade sequences on a four-version fixture, each step checked against the invariant (`FB_RANDOM_SEQUENCES=N` for
another count), and the whole self-check again under `core.autocrlf=true`. CI never runs these.

## Models and cost

The compile loop is `tools/file-loop.py` (Haiku -> Sonnet -> Opus; on a version branch it edits the branch's source),
which `fork-port.py` runs. Keep the session's own context small -- most of a long session's cost is cache reads
(`tools/context-profile.py`). First measured target (a large library mod, 1.21.1 -> 1.21.3, 282 -> 0 errors, 142 mixin
targets, Gates A/B green): about $30 on Opus alone, ~1.3 h, covering the measurement of all lines, the tooling, the port
and its gates.
