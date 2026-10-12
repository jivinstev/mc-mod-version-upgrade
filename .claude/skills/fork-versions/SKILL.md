---
name: fork-versions
description: Make a fork port build for more Minecraft versions from the same branch -- a multi-version layer (.multiversion/ plus one commented hook line) that leaves the authors' code and the 1.21.1 build untouched, ports one 1.21.x target at a time through rename tables, overlays and a shared helper library, gates each target like the base, records what it cost, and feeds every new pattern back into the shared library. Use when the user asks to support another 1.21.x version (or "the latest") in a fork, to add or port a target, to gate a target, or to measure how far a fork is from newer versions.
---

# Build a fork for more Minecraft versions

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

## Which versions (and why some are skipped)

`templates/fork-layer/versions.json` is the policy, in data: a 1.21.x version is targeted only if NeoForge
published a **stable** line for it (players install stable NeoForge; a jar for a beta line pins a loader few run).
1.21.2, 1.21.6, 1.21.7 and 1.21.9 had beta-only NeoForge lines, each superseded within weeks, and are skipped --
the reason is recorded per line and `add-target` refuses them. `make-fork-layer.py versions` prints the table;
`versions --refresh` re-reads the NeoForge maven. A target's jar declares exactly its own Minecraft and NeoForge
ranges (`[1.21.3,1.21.4)`), so a skipped version is never claimed.

## The layer's rules (each is checked, not remembered)

- **Inert**: without `-Pmc` (or with the base version) `layer.gradle` returns at once. CI builds the base jars with
  and without the hook line: `layer.py compare-jars` must say identical for every jar.
- **Isolated**: `layer.py isolation --base origin/neoforge-1.21.1` -- only `.multiversion/` and the appended
  commented hook differ. Every merge from the base branch is then conflict-free.
- **Per-target build dir** `build/mc<t>/` (inside the authors' ignored `build/`): targets never share classes.
- **Versions come from `-P`**: `.multiversion/build.sh <t> <tasks>` passes the target's set; `layer.gradle`
  refuses a `-Pmc` whose versions did not arrive.

## Procedure

0. **Read first**: CATALOG §M and `references/minor-version-deltas.md` (the U and L sections). The first layer
   port re-derived five rows that were already there.
1. **Measure** every targeted line against the base source before porting anything (one compile per line with
   `-Pminecraft_version/-Pneo_version` and the AT re-derived, `burndown-count.sh`): the counts decide the order
   and set expectations. Post them on the fork's tracking issue.
2. **Set up**: `make-fork-layer.py init --repo DIR` (once), then `add-target --repo DIR --target <t>` -- versions,
   Java, GeckoLib and `inherits` (the nearest lower target) come from versions.json, and `sync-shared` installs the
   shared library (`.multiversion/shared/<t>/`: rows every fork inherits + helper classes in the fork's
   `mv_package`). `sync-shared --check` fails when the fork's copy drifted.
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
   - Read the dead-rule line every build: a row that went dead when its file became an overlay is deleted; a row
     that never fired is usually written against already-renamed text.
5. **Mixins**: at 0 errors, `audit-mixin-targets.py DIR <target sources.jar> --sources=build/mc<t>/mv/java` must say
   all targets match. javac cannot see a mixin target; the first port had 16 load-fatal ones at a clean compile.
6. **Gates**: `ci-gates.py --repo DIR --modid ID --base <author ref> --mc <t>` (author's build, Gate A, data,
   payloads, Gate B, Gate C phases). Run the SAME gates without `--mc` as the control in the same session: a red
   that is red on the base too is not the target's. Read the mod's own logs on green runs (an agent or coremod
   that degrades silently). A harness compile error on a new target is fixed once in
   `templates/upstream-harness/versions/<t>.renames.tsv`, for every fork.
7. **Record the cost** (required): `python3 tools/port-cost.py <modid> --path DIR --since <start> --print` for the
   target's work window; put the block in `.multiversion/targets/<t>/COST.md` with what the window covered
   (port only, or port + tooling) and the model mix. The figure is what tells us whether the library and the
   cheaper models are making targets cheaper.
8. **Harvest** (required, before calling the target done): move every row and helper that is not specific to this
   mod into `templates/fork-layer/shared/<t>/` (`@MVPKG@`/`@MODID@` placeholders), re-sync the fork and show its
   prepared tree unchanged but for names; write each new pattern as a CATALOG §M entry and an L entry in
   `references/minor-version-deltas.md`.
9. Commit on the layer branch (`neoforge-1.21.x`), push, and report: errors before/after, gates per target with the
   control, cost, what was harvested, what is left.

## Models and cost

Today the per-error loop of a layer target is done in the session itself. The pipeline's Haiku -> Sonnet -> Opus
escalation (`tools/file-loop.py`) edits a work tree directly, which a layer forbids (fixes must become rows,
helpers or overlays), so it cannot drive a layer target yet; that "layer mode" is the next tool to build. Until
it exists, work the cheap mechanical buckets first (shared rows, then fork rows) and keep the session's context
small -- most of a long session's cost is cache reads (`tools/context-profile.py` shows where).

First measured target (Epic Core API, 1.21.1 -> 1.21.3, 282 -> 0 errors, 142 mixin targets, Gates A/B green):
about $30 on Opus alone, ~1.3 h, covering the measurement of all lines, the layer tooling, the port and its gates.
