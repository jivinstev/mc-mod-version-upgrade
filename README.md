# mc-mod-version-upgrade

**Install Minecraft mods, and port the ones that are stuck on an old version, by asking
[Claude Code](https://claude.com/claude-code) in plain words.**

```text
> Install Sodium
> Migrate <mod name> to Minecraft 26.2
```

The first finds the right build on Modrinth or CurseForge, resolves its dependencies, verifies the
download and puts it in your mods folder. The second ports a mod that has no build for your version:
it decompiles the old jar, rewrites it through a catalogue of ~540 known API changes, and only calls
it done once three test gates pass, the last of them a real Minecraft client.

## Two ways to run a port

**In Claude Code (recommended):** ask in plain words, e.g. `> Port <mod name> to 26.2`. Claude runs
the port script, and when a stage stops it explains the choices and asks you before doing anything by
hand. When the port finishes it delivers the jar and reports what the port cost.

**The script alone:** `python3 tools/port.py "<mod name or path to the jar>" --to 26.2` (or `--to
1.21.1`). There's no orchestrating session to pay for. When it stops, it prints what is left, what it
has spent and your choices. Rerun the same command to resume. It still needs the `claude` CLI logged
in, because the cheap fix workers it starts are Claude.

**Taking an existing port further:** `python3 tools/port.py --from-port mods/<modid> --to 26.2` starts
from a port you already finished (say, a 1.21.1 one) instead of a jar, and keeps its fixes.

Either way the port runs hop by hop (for example Forge 1.20.1 → NeoForge 1.21.1 → 26.2). Each hop
applies its recipe pack of known rewrites first, then sends what's left to small per-file workers
(Haiku first, Sonnet and Opus only as needed). The port must then pass the gates: a headless server,
behaviour tests written for the mod, a real client, and a screenshot review. A real-client run the machine kills (the
OOM killer: exit 137 and no verdict) is reported as `killed` -- environmental, "re-run when memory is free", with the
evidence -- never as a crash for a fix worker, and a client is not started with less than 3.5 GB free
(`MMVU_GATEC_MIN_FREE_MB` overrides; `tools/gate-loop.py`, on every route, single-version and layer forks alike).

## Porting a mod in its own repository

When a mod's source is on GitHub under a licence that allows it, you can port it **in a fork** instead of
from its jar: `> Port my fork <github url> to 1.21.1` (the `port-fork` skill). The result is a branch in the
authors' own layout, `neoforge-<mc>`, with the smallest diff that works. The test harness stays outside their
tree. Then:

- **CI on the fork** runs the authors' own build and the same three gates on every push. It downloads a
  small, pinned "Port CI kit" from this repository's releases, not this whole repository. If the fork also has a
  multi-version layer branch (`.multiversion/`), the same workflow file runs there too (`port-ci.py --layer-branch`)
  with extra jobs: the branch differs from the 1.21.1 port only in `.multiversion/` and one hook line, the hook
  changes no jar, every middle target still compiles and passes the mixin and event-listener audits, and the newest
  target passes every gate. Those jobs are skipped on the 1.21.1 port branch.
- **One jar per platform** (opt-in): for a mod whose build ships several jars, `port-ci.py --variant-jar GLOB` (one
  per platform) adds a `shipped jars` job that builds once and boots each jar in a real NeoForge server; the release
  needs it. A dependent of such a mod adds `--dep-jar-variant` (the other platform's asset of the same pinned
  release), which runs one more `gates (full, <label>)` leg against that jar. Without either, the workflow is unchanged.
- **Single-version first.** A fork port is one `neoforge-<mc>` branch by default, and that is the proven path. The
  multi-version layer is opt-in per fork (only the forks the maintainer has opted in), every layer feature is switched on only where `.multiversion/` exists, and a single-version fork
  builds, gates, syncs and publishes exactly as before.
- **A release** publishes the authors' own JARs as a pre-release, but only when every gate passes. Once a person
  has played it, `port-ci.py promote` makes it a full Release that records who played it; the newest full Release
  is the fork's official baseline.
- **Defects the authors' original already has** are proved pre-existing (their branch, their commit), then
  either fixed in one commit each or recorded in the fork's known-defects list (`gatec-known.txt`), and filed as an issue on the fork
  either way -- never backported unvalidated. The offer and the handoff page list them. Turn Issues on in a
  new fork; GitHub starts them off.
- **An offer for the authors**: a compare link showing just the port (without our CI), the size and shape of
  the diff, what was verified, and draft words. Nothing is sent: contacting the authors is your call.
- **The ten manual tests that matter most**, in every offer: what no automated gate reaches (music, keys,
  client-to-server packets, bosses, screens, raids, worn items, custom recipes, structures, natural spawns),
  one kind each first, with steps that name real `/summon`, `/give` and `/locate` ids and what to do if one
  fails. A handoff page gathers every fork's install steps, checks and tests in one place.
- **How far the port is from what players run.** A fork is often cut from the authors' newest code, not their
  release. The offer says how many unreleased commits it sits on (the release is matched by its publish time on
  every registry the mod is on). When the gap is large, ship two branches: `neoforge-<mc>`, the port of their
  newest code, kept current by merging their new commits; and `neoforge-<mc>-release`, the port of their
  release, derived from the first instead of ported again (measured: about 30% cheaper, and still needing
  every gate), with its own tag series and install manifest.

Forge 1.20.1 → NeoForge 1.21.1 is proven on three MIT-licensed forks. 26.2 is wired but not yet proven.

## Skills: in Claude Code, or from the command line

Each job is a Claude Code skill, so inside `claude` (started in this folder) you can either ask in plain words
or type the skill's slash command. Each skill drives scripts in `tools/` that you can also run yourself,
with no Claude session.

Every skill that works on a specific mod (migrate-mod, install-mod when it migrates, port-fork, fork-versions,
keep-current, publish-port) first runs `python3 tools/guard-names.py ensure --mod-repo DIR` (or `--jar JAR`), for
the mod and every fork it depends on: it derives the mod's names (its toml, `gradle.properties`, root package and
GitHub remote), merges them into the forbidden-names list outside this repository (`FORBIDDEN_NAMES_FILE`, default
`~/.mmvu/forbidden-names.txt`; an existing line is never dropped or reordered, and a list inside a git repository is
refused), writes that path into `.env.local` when it is unset, installs the git hooks when they are missing or stale,
and checks this tree with `check-no-ip.py --strict`. `--dry-run` writes nothing; `guard-names.py check` (no writes)
must pass before a skill commits or pushes here. A short or ordinary-word id becomes a whole-word `re:` line, and an
ordinary word this repository's own text uses is kept only in mod-specific forms (`<id>:`, `assets/<id>/`) or, for a
display name or author, left out -- each reported.

| Skill | In Claude Code | From the command line |
|---|---|---|
| **install-mod**: install a mod and its dependencies from Modrinth or CurseForge | `> Install Sodium` or `/install-mod Sodium` | `python3 tools/guard-names.py ensure --jar JAR` for each jar it migrates, then `python3 tools/mod-registry/modreg.py search --query "Sodium"`, then `versions`, `deps` and `download` (see [tools/mod-registry/README.md](tools/mod-registry/README.md)) |
| **install-mod**: install fork ports from GitHub, with their dependencies and NeoForge | `> Install these mods from owner/RepoA owner/RepoB` or `/install-mod owner/RepoA owner/RepoB` | `python3 tools/install-port.py owner/RepoA owner/RepoB [--with-optional | --with-untested] [--dry-run] [--replace] [--mods-dir DIR]` (installs the files each port's manifest names, which CI tested together; when a manifest names an older release than the fork's newest full Release, the output opens with `NOT THE NEWEST RELEASE` naming both), then optionally `tools/boot-mods.sh DIR` to boot the folder headless first |
| **migrate-mod**: port a mod from its jar | `> Migrate <mod name> to 26.2` or `/migrate-mod <mod name> to 26.2` | `python3 tools/guard-names.py ensure --mod-repo DIR` or `--jar JAR` first (the mod and every fork it depends on), then `python3 tools/port.py "<mod name or jar>" --to 26.2` (rerun to resume; `--from-port mods/<modid>` to continue a finished port) |
| **port-fork**: port a mod in its own GitHub fork, with CI, releases and an offer | `> Port my fork <github url> to 1.21.1` or `/port-fork <github url>` | `python3 tools/guard-names.py ensure --mod-repo DIR` or `--jar JAR` first (the mod and every fork it depends on), then `python3 tools/port-upstream.py --repo DIR --modid ID` (the port), `tools/ci-gates.py` (gates as CI runs them, `--env full` or `minimal`; `--shipped` adds the shipped-jar row; `--gatec none` skips Gate C), `tools/shipped-gate.py --repo DIR --jar BUILT.jar [--mc T --neo V] [--expect-log FILE:REGEX]` (boots the built jar in a real NeoForge server's `mods/`, as a player installs it, and checks the mod's own logs -- dev gates run from the class path and cannot see a subsystem that is inert when shipped), `tools/port-ci.py` (adds the CI; `--tag-suffix=-release` for a release branch; `--variant-jar GLOB` boots each platform jar the build ships; `--dep-jar-variant SPEC` adds a gates leg on a dependency's other platform jar; `port-ci.py args --repo DIR` prints the command an installed workflow was generated with), `tools/port-offer.py` (the offer, install manifest and manual tests; `--release` adds the distance from release; `--push` commits the install manifest, `port-install.json` in the fork's `.github` folder, to the port branch), `tools/port-manifest.py refresh --repo DIR [--tag TAG] [--variant SUBSTRING] [--check] [--commit]` (points that manifest at the newest full Release -- the same platform jar -- and at the sibling jars the workflow pins, sha256 recomputed, every other entry untouched; prints `unchanged` when it is current; refuses, writing nothing, when there is no manifest or the jar cannot be chosen or fetched; run it after a re-pin, since only a promotion through Port CI refreshes it by itself), `tools/port-fleet.py --fleet FILE <step>` (every fork at once) |
| **port-fork**: promote a played pre-release to a full Release | `> Promote my fork's release, I played it` | `python3 tools/port-ci.py promote --fork owner/repo --tag TAG --played-by LOGIN [--notes "..."]` (dispatches the fork's Port CI workflow; prints the dispatch when `gh` is not logged in; on the port branch, never the layer branch, that run then refreshes the install manifest with `tools/port-manifest.py` in a `[skip ci]` commit when the branch has one) |
| **fork-versions**: build a fork for more 1.21.x and 26.x versions from one branch -- an inert `.multiversion/` layer, a shared rename/helper library, per-target gates, a cost record, and every new pattern harvested back | `> Make my fork build for 1.21.4 too` or `/fork-versions <github url>` | `python3 tools/guard-names.py ensure --mod-repo DIR` or `--jar JAR` first (the mod and every fork it depends on), then `python3 tools/make-fork-layer.py versions [--refresh]` (which 1.21.x and 26.x versions are targeted; beta-only NeoForge lines skipped; 26.x targets get Java 25, ranges to the next patch release, and GeckoLib's new Maven group as a substitution), `make-fork-layer.py init --repo DIR`, `make-fork-layer.py add-target --repo DIR --target 1.21.4` (`--direct` for a target that jumps over middle versions: no `inherits`, and `shared_chain=` still applies the shared libraries of the versions in between; off by default), a target's `target.properties` adds mixins with `mixin_add.<config>=client:Name` (inherited by every later target) or keeps one to that target alone with `mixin_add_only.` / `mixin_remove_only.` (`.multiversion/tools/layer.py mixins --target 1.21.4` prints the set), `make-fork-layer.py sync-shared --repo DIR [--check]` (also brings the layer's `layer.gradle`, `build.sh` and `.multiversion/tools/` up to the template when the fork's copy is a verbatim earlier template version; an edited copy is reported and left alone, and fails `--check`), then in the fork `.multiversion/build.sh 1.21.4 build` (each target builds into `build/mc<t>/` and runs its client in `build/mc<t>/run`, so one target's mod configs never reach another's; without `-Pmc` the authors' `run/` is unchanged); `tools/ci-gates.py --repo DIR --modid ID --mc 1.21.4` (the gates for that target); `.multiversion/tools/layer.py isolation --base origin/neoforge-1.21.1` and `compare-jars` (the base build is untouched); `python3 tools/port-ci.py --repo DIR --modid ID --layer-branch neoforge-1.21.x` (the fork's one `port-ci.yml`, installed on both branches, which on the layer branch also runs `tools/layer-ci.py`: isolation, inertness, a compile-only pass with the mixin and event-listener audits of every middle target, and the full gates on the newest target by version sort, plus the same full gates for each released middle target listed in `.multiversion/ci.properties` as `full_gates=1.21.4,...` (empty by default); all skipped on `neoforge-1.21.1`; a `release` dispatched on the layer branch makes `layer-release`'s one pre-release with the base jars and every target's `-mc<t>` jar once isolation, inertness and the layer gates are green (`layer-compile` and `layer-gates-extra` may be skipped, never red), and each layer job first runs `tools/layer-deps.py fetch` for the sibling ports' jars a dependent fork's targets pin with `layer-deps.py resolve --port-release owner/repo@TAG`); `tools/layer-deps.py resolve --repo DIR --target 1.21.4 [--port-checkout owner/repo=DIR] [--port-release owner/repo@TAG] [--dry-run]` (a fork whose build pins mods -- cursemaven, Modrinth's maven, a sibling port -- gets each one's build for the target as `substitute.`/`exclude.` lines in the target's `target.properties` and a `DEPS.md` table; a required mod (per the fork's install manifest, port-install.json, matched by project id -- a Modrinth slug is looked up -- and every `implementation`/`api`/`jarJar` dependency the manifest does not name) with no build stops with exit 15, any other is dropped and recorded; an earlier `--port-release` pin is kept by a resolve that does not name that sibling, and its jar installed into mavenLocal with `fetch`'s sha256-checked download; `--dry-run` writes, builds, fetches and installs nothing; a sibling layer fork's per-target jar is built in its checkout -- or, while another run holds that checkout's `build/.fork-port.lock`, taken unbuilt from it and marked so in `DEPS.md`; `fork-port.py` runs it in its setup stage); `tools/port-cost.py ID --path DIR --target 1.21.4 --since TIME --print` (the target's cost, sized by its overlays: each request charged to one target at most, by the paths and target its tool calls name, with the checkout's shared work on its own line and not in the total, so concurrent targets and forks add up); or all of it in one command: `python3 tools/fork-port.py --repo DIR --target 1.21.5 [--budget 15] [--gates auto|none|ab|full] [--port-checkout owner/repo=DIR] [--port-release owner/repo@TAG]` (`auto`, the default: full gates -- Gate C, a real client, included -- until the target's `GATES.md` records a green Gate C, which a green full run writes and a full run that is not green removes; then A/B; a target is reported done only with that green Gate C on record and no unreviewed stub signal in its `STUBS.json`, whatever stage the run resumed from -- a run that ends green at A/B, or with `--gates none`, exits 17, not done) (setup -- which also resolves the fork's pinned dependencies per target, and from 1.21.2 writes the client item definitions and the 1.21.2-form recipes into the target's resource overlay (from 26.2 also the advancements and loot tables whose entity predicates need 26.2's dispatched-map form; from 26.3 also the worldgen JSON whose block states need 26.3's `{id, properties}` form and the trim materials in 26.3's `palette_id` form, with their palette PNGs and the item-trim atlas source moved to the items atlas, and every advancement and loot table in 26.3's loot grammar: one `condition`/`modifier`, typed number providers, criterion entity conditions as loot conditions), adds the access-transformer lines the shared helpers need, and drops rows the shared library now carries --, staging with the access transformer, a generated class-move table `targets/<t>/moves.generated.tsv`, the compile loop in layer mode, mixin audit, gates -- which also compare the log files the mod itself wrote in the Gate B run with the inherited target's, so a subsystem that stopped running is residue, exit 11 -- cost, appended to the target's `COST.md` from where its last record ended, or every request on this machine that worked on the target when it has none, plus the loop workers' own recorded dollars), which stops with `targets/<t>/RESIDUE.md` listing only what is left (and keeps every run's stub signals, plus an audit of each overlay against the authors' file, in `targets/<t>/STUBS.json` until each is reviewed; a review of the same file and signals in an inherited target carries over only while the file hooks the same mixin targets, `method` and `@At`, and event handlers -- else it is flagged `re-justify: hook targets changed from ... to ...` and counts as unreviewed) (exit 10 compile, 11 Gate A/B, 12 Gate C, 13 audit, 15 a required dependency has no build for the target) and the `--from-stage` command to resume (a resume re-measures the compile count; `--direct` adds a new target as a jump; the loop refuses to start when the starting error count exceeds `--max-errors` (default 100 per dollar of `--budget`) unless `--confirm-spend`; one run per checkout: a second `fork-port.py` or `ci-gates.py --mc` run is refused with exit 14) |
| **port-fork**: the handoff | `> Prepare the handoff for my forks` | `tools/manual-tests.py --repo DIR` (the ten manual tests; per-port warnings from the work folder's `manual-tests.notes.json`), `tools/offer-page.py --mods FILE --out page.html` (one page for every fork) |
| **port-fork**: the authors' release vs. their newest code | `> How far is my fork from the authors' release?` / `> Make a release branch for my fork` / `> Bring my fork up to date with the authors` | `tools/port-provenance.py --repo DIR --base REF --registry modrinth:SLUG --registry curseforge:ID --mc VER --loader forge` (the gap), `tools/port-derive.py` (a release branch; `--resolve` finishes it with a model, at a stop), `tools/port-sync.py --repo DIR --port BRANCH --upstream origin/BRANCH` (merge the authors' new commits) |
| **keep-current**: keep a fork port current with its authors -- merge their new code, reconcile every fix we carry against theirs (take theirs when an A/B proves it covers ours), port, gate, open a PR into the fork's versioned branch (merged on green where the fork allows); nightly as a routine, on the model set at setup (Opus recommended; the compile loop keeps its own Haiku -> Sonnet -> Opus ladder) | `> Keep my forks current` / `> Did the authors fix the bugs we fixed?` / `> Run keep-current nightly` | `python3 tools/guard-names.py ensure --mod-repo DIR` or `--jar JAR` first (the mod and every fork it depends on), then `python3 tools/port-sync.py --repo DIR --port BRANCH --upstream REMOTE/BRANCH`, then `python3 tools/fix-ledger.py worksheet --repo DIR --port BRANCH --upstream REMOTE/BRANCH` (the reconcile worksheet), `fix-ledger.py render --ledger FILE` (the fix ledger for a fork issue), `fix-ledger.py check --ledger FILE --release BRANCH` (refuses a dropped fix without an A/B, or a crash/kick left open on a released branch); for a fork with the opt-in multi-version layer only: `python3 tools/layer-sync.py merge --repo DIR --port-ref REF` (merge commit into the layer branch), `layer-sync.py run --repo DIR` (every target measured, audited and looped, classed untouched / touch-up / rework with a table for the PR body and the ledger; a rework target stops the run with its `RESIDUE.md`; a single-version fork is left untouched) |
| **publish-port**: publish a fork port's promoted releases as an unofficial community version on Modrinth and CurseForge -- set up both projects (generated page and config), publish a release, and publish automatically on promotion; refuses without the authors' permission, for a pre-release, or with a crash/kick/hang open in the fix ledger | `> Publish my fork on Modrinth and CurseForge` / `> Set up the community projects for my fork` | `python3 tools/guard-names.py ensure --mod-repo DIR` or `--jar JAR` first (the mod and every fork it depends on), then `python3 tools/port-publish.py setup --repo DIR --permission URL --ledger-issue OWNER/FORK#N` (the project page and the publish config in the fork: the page is titled `<name> - Community Port (<mc>+)`, links the original on Modrinth, CurseForge and GitHub at the top, and gives the original's credits unchanged, naming the file they come from; the config also holds Modrinth's environment, sent with every upload, and the content disclosures -- derivative work and AI-generated content -- set by `publish_setup`), then the fork's Port CI does the rest: `publish_setup = true` (the Modrinth draft), `publish = TAG` (one release), and every promote publishes; a fork with the opt-in multi-version layer and a `layer` config section publishes one file per target whose gates are green at the released commit (jars named `-mc<target>`, per-target game versions and dependency versions) and refuses the whole publish naming a target that is not; a single-version fork publishes exactly as before |
| **review-contribution**: review a pull request to this repository | `> Review PR 42` or `/review-contribution 42` | `python3 tools/review-pr.py --base origin/main --head HEAD` (the mechanical checks; the judgement half is the skill) |

Each script prints its options with `--help`. Most also have `--self-check`, which runs their own tests.

Both routes run the same deterministic stage after each hop's rename tables and before any worker sees an
error (`tools/mechanical-hop.py`, called by `port.py` and `port-upstream.py`): on a Forge → 1.21.1 hop the
access transformer, Forge code shapes, SimpleChannel → payloads, MCreator variables, GUI overlays → layers, Forge
capabilities → a mod-local compat layer wired into NeoForge (`tools/convert-capabilities.py`) and Holder fixes; on a 26.2 hop the member
renames and the converters for GUI hooks, entity save/load, data attachments, changed hook signatures, tool tiers
and armour, render types and shaders, entity render state and GeckoLib. A converter added there reaches both
routes; `tools/test-port-tools.sh` fails if a route stops going through it. Two
gates guard bug classes found in shipped ports: a field-less network payload behind `StreamCodec.unit`
(`tools/audit-unit-codecs.py`), and data files the server silently rejected (a row of `tools/ci-gates.py`).

The shared helper library (`templates/fork-layer/shared/<version>/java`) is written next to third-party sources --
NeoForge's sources jars, the Minecraft sources every fork build stages, GeckoLib -- so one more gate runs **only on
your machine**: `python3 tools/check-similarity.py` compares template Java with those sources (comments dropped,
identifiers and literals normalised, API-forced declarations and one-line bodies left out) and refuses a file whose
longest matching run or matched fraction passes a calibrated limit, printing the reference jar, entry and line
ranges. It finds the sources by itself (the Gradle cache, `build/*/moddev/artifacts` in the git checkouts next to
this one, and `MMVU_REFERENCE_SOURCES`) and indexes them once into `~/.cache/mmvu-similarity` (under a minute; then
well under a second per commit). `./tools/install-hooks.sh` installs a pre-commit hook that runs it on the staged
template Java (`--staged`; a commit with none returns at once), and the pre-push hook runs it on the pushed range.
Where no sources are found it warns that it did not run, and refuses only with `MMVU_SIMILARITY_STRICT=1`. CI does
not run it -- the licences of those sources keep them out of CI -- only its `--self-check`, on invented code.

## Measured ports

Every port here was run from this repository alone:

| the mod | from → to | first port (whole session) | now: the port's workers¹ | gates passed |
|---|---|---|---|---|
| a small MCreator food mod, 46 files | NeoForge 1.21.4 → 1.21.1 | $6.88, 20 min | **$0.69** | server, behaviour, client, screenshots |
| a GeckoLib library, 53 files, 6 mixins | Forge 1.20.1 → NeoForge 1.21.1 | $30.31, 43 min | **$5.24** | server, behaviour, client, screenshots |
| a shield mod, 64 items, 45 files | Forge 1.20.1 → NeoForge 26.2 (two hops) | ≈ $30.91², 1.8 h | **$5.64**³ | server, client (4 phases), screenshots |

¹ What the per-file workers and gate checks cost, measured from their own logs. It leaves out the
session that drives the port, which the next round of full replays will measure. That cost should be
small, because the session now only starts the script and answers its stops.
² Estimated: Claude Code on a desktop records tokens, not dollars, so this one is its exact token
counts at list prices ([`tools/model-prices.tsv`](tools/model-prices.tsv)).
³ One command, both hops (`tools/port.py`): $5.40 of workers, plus $0.24 for an enchantment fix that Gate B now requires (its content census found the port had dropped three). An earlier run with hand fixes, before the script existed, cost $12.57. Details in [`docs/EVALS.md`](docs/EVALS.md).

Every port also sends back what it taught, written without the mod's name, as a pull request to the
catalogue: the first three contributed 31 lessons between them (22 new entries, 9 additions). Each
records its own cost in [`docs/port-costs.tsv`](docs/port-costs.tsv), so the next person can see what
a mod of that size is likely to take.

## Quick start: on your computer

```bash
git clone https://github.com/jivinstev/mc-mod-version-upgrade
cd mc-mod-version-upgrade && ./setup
claude
```

`./setup` finds your Minecraft, recommends an answer to every question, and ends by telling you what
to type. Installing needs Python 3. Porting is an add-on: say yes when `./setup` offers it, or run
`./setup --migrate` later; it needs Java 21 (and 25 for Minecraft 26.x).
Details: [docs/USING.md](docs/USING.md).

Setup also asks you to turn off **"Help improve Claude"** at
[claude.ai/settings/data-privacy-controls](https://claude.ai/settings/data-privacy-controls): Claude Code
reads what you work on here, including decompiled mod code. Setup can't change that setting, so it only
records it once you type `confirmed`.

### On Windows

Native Windows, no WSL. Open **PowerShell as yourself, not as Administrator**: from the Start menu,
a plain click (not "Run as administrator"). If the title bar says *Administrator:*, close it and open
a normal one. (Don't go by the starting folder: a normal window can open in `C:\WINDOWS\system32` too.
The script checks for you and stops if the window is elevated.) `cd` to the folder you keep code in
(for example `cd ~\dev`), then paste:

```powershell
irm https://raw.githubusercontent.com/jivinstev/mc-mod-version-upgrade/main/install.ps1 | iex
```

[`install.ps1`](install.ps1) installs Git for Windows and Python 3.12 if they are missing (Windows may
ask for permission: click Yes), asks where to put this repository (press Enter for
`mc-mod-version-upgrade` inside the folder you ran it from, or type another folder), clones it, and runs
`setup.cmd --install-missing`: the same setup as on macOS and Linux, which on Windows also installs
Claude Code and, if you say yes to porting, the Temurin JDK 21, with winget. Then it starts Claude Code in the clone. No new
window is needed: it picks up the new commands itself. Next time, open PowerShell, `cd` to the clone
and run `claude`. Running the line again is safe: it skips what is already there and updates the clone
(run it from inside the clone and it uses that one without asking). It refuses to run as Administrator,
because the tools and settings would then land where your normal shell and Claude Code don't look, and
prints what it checked.

<details><summary>The same steps by hand</summary>

```powershell
winget install -e --source winget --id Git.Git
winget install -e --source winget --id Python.Python.3.12
# close PowerShell and open a new (not Administrator) one, so it finds git and Python
git clone https://github.com/jivinstev/mc-mod-version-upgrade
cd mc-mod-version-upgrade
.\setup.cmd --install-missing
claude
```

`--source winget` matters: without it winget also asks the Microsoft Store, and if that fails
(`0x8a15005e`, "The server certificate did not match", common on VMs and behind network filtering) it
installs nothing. In PowerShell it is `.\setup.cmd`; in cmd, `setup.cmd`; in Git Bash, `./setup`.
</details>

Python from python.org has no `python3` command, so setup adds one to Git Bash (and an `unzip`), and
tells Claude Code which Python to use. Open a new Git Bash window after the first run.

Native Windows has no Claude Code sandbox, so Claude asks before running commands. For porting, setup
turns on git's long paths; turn on Windows' too if setup says they're off (that one needs an
Administrator PowerShell, once: setup prints the line), and keep the workspace on the same drive as this folder.
The real-client test opens a Minecraft window on your desktop; nothing else is needed.

## Quick start: in the cloud

Nothing to install.

1. **Make the environment (once):** at [claude.ai/code](https://claude.ai/code), open the environment
   menu → **Add environment**. Name it `Minecraft modding`, then:
   - **Network access:** Custom. Tick **Also include default list of common package managers**.
     Paste into **Allowed domains**:
     ```text
     maven.neoforged.net
     *.minecraft.net
     *.mojang.com
     api.modrinth.com
     cdn.modrinth.com
     api.curseforge.com
     cursemaven.com
     *.forgecdn.net
     maven.parchmentmc.org
     maven.fabricmc.net
     maven.blamejared.com
     maven.ithundxr.dev
     dl.cloudsmith.io
     thedarkcolour.github.io
     packages.adoptium.net
     ```
   - **Setup script:** leave it empty.
   - **Environment variables (optional):** `CURSEFORGE_API_KEY=<your key>`. To get a free key,
     sign in at [console.curseforge.com](https://console.curseforge.com/) and open **API keys**.
     Without it, mods that are only on CurseForge can't be found; everything on Modrinth still works.
2. **Start a session** on this repo (or your fork), in that environment. It opens straight away.
   Type `run ./setup --yes` (`--yes` is needed: Claude runs it without a terminal to answer
   questions). It sets up porting, checks every host and installs the two missing tools (a virtual
   screen and Java 25) the first time, which takes a minute or two. It ends with **Ready.**; then
   ask for a port in plain words, as above.

In the cloud you can port and test, but there's no Minecraft to play it in, and the session's files
are deleted when it ends. So when a port finishes, **ask Claude to send you the jar**: it arrives as a
download in the Claude app. Then put it in your Minecraft `mods` folder at home.

## Ported mods are for your own machine

Many mods are "all rights reserved": don't publish or redistribute a ported jar without the original
author's permission. The MIT licence covers this tool, never the mods it ports.

## More

| | |
|---|---|
| [docs/USING.md](docs/USING.md) | Installing vs. porting, where ports go, prerequisites |
| [CATALOG.md](CATALOG.md) | The ~540 migration lessons: pattern → error → fix |
| [SUPPORTED_VERSIONS.md](SUPPORTED_VERSIONS.md) | Which Minecraft versions are tested |
| [docs/EVALS.md](docs/EVALS.md) | How much of a port the catalogue and its recipes cover, measured per port profile |
| [docs/POPULARITY_SWEEP.md](docs/POPULARITY_SWEEP.md) | A zero-model run of the pipeline over 384 popular mods: how they were chosen, what it found, and the tools to repeat it (`tools/sweep/`) |
| [CONTRIBUTING.md](CONTRIBUTING.md) | The safety gates, and sending back what your port taught |

## Licence

MIT (see [`LICENSE`](LICENSE)); vendored files carry their own headers, and the decompilers are fetched
under their own licences. Details in [CONTRIBUTING.md](CONTRIBUTING.md#licence).
