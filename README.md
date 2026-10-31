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
(`MMVU_GATEC_MIN_FREE_MB` overrides; `tools/gate-loop.py`, on every route, single-version and version-branch forks alike). Only one
real client runs per machine: a second Gate C client waits for the first's lock (`~/.mmvu/locks/gatec.lock`), saying
whose run holds it.

## Porting a mod in its own repository

When a mod's source is on GitHub under a licence that allows it, you can port it **in a fork** instead of
from its jar: `> Port my fork <github url> to 1.21.1` (the `port-fork` skill). The result is a branch in the
authors' own layout, `neoforge-<mc>`, with the smallest diff that works. The test harness stays outside their
tree. Then:

- **CI on the fork** runs the authors' own build and the same three gates on every push. It downloads a
  small, pinned "Port CI kit" from this repository's releases, not this whole repository. A fork with more versions
  has one branch per version, and each gets the same workflow at its own version, with every gate and its own release
  (`fork-branches.py ci`).
- **One jar per platform** (opt-in): for a mod whose build ships several jars, `port-ci.py --variant-jar GLOB` (one
  per platform) adds a `shipped jars` job that builds once and boots each jar in a real NeoForge server, checking
  the mod's own logs against `.port/<mc>/shipped-expect-<variant>.txt` (`cf` for `*_cf.jar`), else
  `.port/<mc>/shipped-expect.txt`, on every kind of fork (none: the jar is only booted, as before; a `.multiversion/`
  left at HEAD with these files is refused, exit 2, by every tool, told to move them to `.port/<mc>/`); the release needs it. A dependent of such a mod adds `--dep-jar-variant` (the other platform's asset of the same pinned
  release), which runs one more `gates (full, <label>)` leg against that jar. Without either, the workflow is unchanged.
- **Single-version first.** A fork port is one `neoforge-<mc>` branch by default. More versions are opt-in per fork,
  and then they are **stacked version branches**: `neoforge-1.21.3` made from `neoforge-1.21.1`, `neoforge-1.21.4`
  from `neoforge-1.21.3`, ..., each the full source for its version with its own build, gates, CI and release, a fix
  or the authors' new code merged upward branch by branch with the rename rows applied as a tool
  (`tools/fork-branches.py`). The machinery for several versions (the cascade, per-version CI, the ladder hops) is
  switched on only where a version branch's `.port/<mc>/branch.properties` exists. Fixes, checks and savings are meant
  for both kinds of fork ([CLAUDE.md](CLAUDE.md), "Single-version and version-branch forks"), so a single-version
  fork's behaviour does change, deliberately and said in the PR (`Single-version-change:` commit lines): dependency
  bumps are explicit for every fork, re-cut releases are tagged `-rN`, a weekly-round merge waits for the review, and
  report-only audit rows appear when they find something (the mixin-target and event-listener audits among them,
  which also say when an input is missing), a stale Port CI kit is warned about (`port-ci.py check`), a
  port branch behind origin's is fast-forwarded before `port-sync.py` merges (a diverged one refused), the round's
  preflight checks `origin/HEAD` and the authors' remote, the shipped-jar files are read from `.port/<mc>/`, and
  Tuesday's plan shows a trial merge of the authors' new commits into the head (in the plan's throwaway checkout) and
  lists the files that conflict, and the weekly round runs on the driver, `tools/round.py` (#161 U4: the authors' merge
  by `port-sync.py`, one gate pass after the review, a library's merge only after its dependent's gates against its
  candidate jar, the pre-release dispatched right after the merge), and has the same done list as a version branch,
  its records in `.port/<mc>/` (#161 U5: `fork-branches.py check-done --repo DIR --target MC --base REF` -- GATES.md
  certifying the commit, REVIEW.md against the change's base, STUBS.json when there is one, the behaviour inventory,
  TEST-ITEMS.md -- reported only in the first round, then holding the PR back until done; a re-pin to a library
  pre-release that is not done marks `.port/<mc>/DEPS.md`, which promotion and publishing refuse; `library=true` in
  `.port/<mc>/port.properties` skips Gate C's spawn and battle, the round's dependent gate standing in), and the
authors' merged data files are put in the port's version's forms right after the merge (#161 U6: `port-sync.py` runs
the version-branch data converters -- 1.21.2 recipes, 26.2 entity predicates, 26.3 data, a stranded pre-1.21 layout --
on the files the merge brought in and commits what changed on its own, "data forms for <mc> after the authors'
merge", a refused file listed in `.port/<mc>/DATA-FORMS.md`; `port-sync.py --repo DIR --data-forms` after a resolved
conflict; the version-branch cascade does the same after each merge-up, which before converted data only when a
version was set up). Some fixes, checks and savings still reach
  version-branch forks only (#161 lists the rest). The `.multiversion/` **layer** route (one branch building every version) was removed (#161 U7): every
  tool refuses a checkout with the layer at HEAD, naming commit `baa092b62eae`, the last that supports it -- check it
  out to migrate such a fork to version branches (`fork-branches.py migrate`; fork-versions, "A fork still on the layer").
- **A release** publishes the authors' own JARs as a pre-release, but only when every gate passes (a `release` run on
  an unchanged `mod_version` is cut as `-r2`, `-r3`, ...: a name whose release or tag already exists is skipped, and a
  tag that points at another commit than the build stops the job, nothing released). Each pre-release also carries
  every environment's gate summary as written by `ci-gates.py` (`gates-full.md`, `gates-minimal.md`,
  `gates-<env>-<variant>.md`), the gate record of the commit it was built from. Port CI's `release = true` with
  `from_run = <run id>` releases the jars and gate logs of an earlier successful push or pull_request run of the same
  workflow instead of building again (no gates job runs): the release job checks that run's head commit and the tree
  its gates recorded are this commit's tree, and releases nothing otherwise; each attached summary's first line
  then names this commit and the one tested (a pull_request run tests GitHub's merge commit). `dep_override =
  "owner/repo@tag/asset.jar=group:artifact:version@sha256"` (a release asset) or
  `"owner/repo#RUN_ID/ARTIFACT/asset.jar=group:artifact:version@sha256"` (a jar in another repository's run artifact),
  space-separated, is the dependent gate: every gates leg and the shipped-jar job build against that candidate in
  place of the pinned jar with the same coordinates, and such a run never releases. A workflow rendered before these
  two inputs is reported stale by `port-ci.py check` and `fork-branches.py ci-check`, like an older kit. Once a person
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

## Testing released ports on real installs

CI tests a port in a dev client. The `test-installs` skill tests the **released jars** in the real game instead, on
real Minecraft Launcher installs of every Minecraft version the port supports, with no Gradle on the test machine:
`> Test my forks' releases on real installs of every version`. It works in three steps (`tools/real-install.py`):

- **plan** reads the forks' version branches and releases, Mojang's manifests and the registries, and writes nothing
  but its own work folder (`~/.mmvu/real-install/<label>`). For each version it works out:
  - the NeoForge version, from the branch's `gradle.properties`;
  - the Java major, from the version JSON (21 for 1.21.x, 25 for 26.x);
  - each fork's jar, from the newest release of that branch's tag series (`--prerelease` admits pre-releases;
    `--variant mr|cf` picks the platform jar);
  - the sibling-port jars that version's workflow pins, each with its sha256;
  - the registry dependencies the jars' `neoforge.mods.toml` requires (from Modrinth or CurseForge, sha1-checked).

  `--all-versions` takes every `neoforge-<mc>` branch that all the forks have; a library-only version is skipped and
  said so. The plan prints every jar and what each version adds on disk, and refuses when less than `--margin-gb`
  (default 5) would stay free.
- **install** does four things per version:
  - fetches whatever Minecraft files the Launcher never downloaded, and the Java runtime if it is absent;
  - installs NeoForge headless with its own installer;
  - creates one instance per version, `<minecraft>/instances/<label>-<mc>/`, whose `mods/` holds exactly that
    version's set;
  - with `--profiles`, adds a Launcher profile per version (after a backup, and never while the Launcher runs).

  The player's own mods folder and profiles are never touched.
- **test** launches the real client from each instance, one at a time, with the command the Launcher builds. The boot
  test passes when the mods under test are in the loaded mod list, no crash report appeared, stderr shows no
  exception and the title screen was reached. With the carrier installed (`--with-carrier`), the Gate C phases follow
  (`launch`, `spawn`, `battle`, `gauntlet`), one launch each. Results go to a per-version, per-phase table and to
  `report.json` and `report.md`.

**The Gate C carrier** is this repository's own mod-agnostic Gate C driver, `templates/smoke-harness` (mod id
`smokeharness`). This repository's CI builds it as one jar per supported Minecraft version and publishes the jars as
the release `gatec-carrier-vN`, with assets `gatec-carrier-<mc>.jar`, `SHA256SUMS` and `MANIFEST.txt` (the NeoForge and
Java versions per jar). A new release is cut whenever a version's generated sources change. Forks carry nothing for
it. To use it by hand, put the jar for your version in an instance's `mods/` and start the client with
`-Dsmokeharness.boottest=true -Dsmokeharness.testmode=<launch|spawn|battle|gauntlet>
-Dsmokeharness.namespaces=<mod ids>`; it prints `SMOKEHARNESS_BOOT_TEST: PASS|FAIL ...` and quits. The runner uses the
newest release by default, checked against SHA256SUMS. `--carrier-release TAG` picks another release,
`--carrier-dir DIR` takes a folder of jars (such as a CI artifact), and `--carrier-jar [MC=]JAR` takes one jar.

## Playtests: scripted play in the real game

The boot test and the Gate C phases prove that a port loads and survives a fight. A **playtest** proves that the
mod does what a player expects: the boss's music starts when it is summoned and stops when it dies, the signature
sword kills it in one hit, a narration line appears. A playtest is a script that runs in the same real-client
instances as test-installs, on every installed version, through the Gate C carrier's `playtest` mode
(`-Dsmokeharness.testmode=playtest -Dsmokeharness.playtest=<script>`). Scripts belong to the mod, never to this
repository. Keep them in `playtests/` at the root of the fork's port branch, or anywhere private.

A script (`.playtest`, UTF-8) is Minecraft commands plus `@` directives:

```
@intent The boss's music plays while it lives and stops when the sword kills it
@step setup
gamemode survival
difficulty normal
@step summon the boss
summon examplemod:boss ~ ~ ~5
@expect sound examplemod:boss_music within 10s
@step kill it with the sword
give @s examplemod:blade
@aim entity examplemod:boss
@key key.attack
@assert execute unless entity @e[type=examplemod:boss]
@expect sound examplemod:boss_music gone within 10s
@shot after the kill
```

A command that fails fails its step. The directives are:

- `@intent` and `@step <name>`;
- checks: `@assert <command>`, and `@expect sound|subtitle|chat|title|actionbar|bossbar|screen|error <match> [gone]
  [within 10s]`;
- player inputs: `@aim entity|pos|look`, `@key <key mapping> [hold 5t]`, `@click slot N | button "text"`;
- travel and loops: `@goto structure <id>`, and bounded `@repeat … @end-repeat` blocks (also once per entity, `{it}`);
- control and evidence: `@wait`, `@allow error <regex>`, `@shot <name>`, `@continue-on-fail` and `@end`.
- header only: `@world [flat|normal] [structures] [seed=<n>]` (default flat, seed 0; `structures` adds the flat world's villages and strongholds) and `@continue-on-fail`. An `@intent` inside a
  step is that step's purpose.

The grammar and its checker are `tools/playtest-lint.py` (`python3 tools/playtest-lint.py FILE`); `playtest.py lint`
runs the same checker and adds the mod checks. A `@shot` name is letters, digits, `.`, `_` and `-`. The carrier's CI
runs the vanilla fixtures in `templates/gatec-carrier/playtests/` on every carrier version
(`python3 tools/gatec-carrier.py client-smoke --mode playtest --playtest FILE`).

The carrier takes a frame at each step's end and wherever a step fails, and respawns the player after a death. It turns subtitles on and raises any muted volume, so a quiet `options.txt` cannot hide a sound. Each step's end also checks the log: a new ERROR line, or a WARN carrying an exception, fails the step unless `@allow error` covers it.

There are four skills, each driving `tools/playtest.py`:

- **playtest-plan** reads what the mod's jars register (`playtest.py inventory`) and plans two tracks by default:
  - **technical coverage:** the top N scenarios (default 10) by distinct code paths reached;
  - **story progression:** when the mod has one, a single script that plays its quest line stage by stage. The
    inventory finds the story in the advancement tree: the parent chains, and the stages the mod's own code grants.
    A mod with no such chain gets no story script.

  `--no-story` and `--story-only` pick one track. The user's own must-cover list comes first, and their never-touch
  ids become a `--forbid` list. With `--build` it writes, runs and debugs each scenario in a loop until the script is
  right or the failure is shown to be the mod's.
- **playtest-write** turns one scenario into a script. It writes the intent and each step's purpose into the script,
  and lints it against the jars and the forbid list (`playtest.py lint`). With `--until-pass` it runs and debugs the
  script in a loop.
- **playtest-run** runs a script on each installed version, one at a time, and grades every step. It writes
  `report.md` and a self-contained `report.html` with the frames, to publish as a Claude Code artifact. `--video`
  also records the screen on macOS (see "Recording video on macOS" below).
- **playtest-debug** reads one failed run (`playtest.py explain`) against the script's intent and says whose failure
  it is: the script's, the carrier's or the mod's. It gives the fix or the finding.

On macOS the game window needs the user's session on the console with the screen unlocked.

**Recording video on macOS.** The key frames need no permission; the carrier takes them inside the game. A screen
recording (`--video`) does. macOS grants Screen Recording to the **app** you run the tool from (Terminal, iTerm,
the Claude desktop app, your IDE), never to `python3`. A command run inside that app gets no prompt of its own: it is
refused silently. Once per app:

1. Open **System Settings > Privacy & Security > Screen & System Audio Recording**.
2. Turn on the app you run the tool from. If it is not listed, add it with **+**.
3. **Quit and reopen that app.** macOS applies the change only to a fresh start, so the session that asked does not
   get it.

Before the first game starts, `playtest.py run --video` takes one silent test still. If macOS refuses it, the runner
names the app to allow (the outermost `.app` above the process, such as `Claude.app` or `Terminal.app`) and carries on
with key frames only. Each version's report notes the same.

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

In short -- what to ask, and the command it runs (the full detail is in the table after this one):

| Skill | Ask | Command |
|---|---|---|
| **install-mod** | `> Install Sodium` | `python3 tools/mod-registry/modreg.py search --query "Sodium"`; fork ports: `python3 tools/install-port.py owner/Repo` |
| **migrate-mod** | `> Migrate <mod name> to 26.2` | `python3 tools/port.py "<mod name or jar>" --to 26.2` |
| **port-fork** | `> Port my fork <github url> to 1.21.1` | `python3 tools/port-upstream.py --repo DIR --modid ID`, then `python3 tools/port-ci.py --repo DIR --modid ID` |
| **fork-versions** | `> Make my fork build for 1.21.4 too` | `python3 tools/fork-branches.py add-target --repo DIR --target 1.21.4`, then `python3 tools/fork-port.py --repo DIR --target 1.21.4`; the 26.1.2 entityCutout swap: `python3 tools/fork-branches.py cutout-check --repo DIR --branch neoforge-26.1.2 [--fix]` |
| **keep-current** | `> Keep my forks current` / `> Set up the weekly round` | setup: `python3 tools/weekly-plan.py install --repo DIR --fork owner/repo@neoforge-1.21.1[=URL#BRANCH] --plan-owner LOGIN [--allow WHAT ...]` and `weekly-plan.py round-task --existing NAME ...`; the round: `python3 tools/known-issues.py read-plan --latest --slug owner/repo`, then, for every fork set (single-version, version-branch or mixed), its driver: `python3 tools/round.py init --frozen round/frozen.json --fork owner/repo=DIR ... [--through owner/repo=VERSION|none]` (`--through` makes a trial round: that fork's ladder stops at VERSION, or the fork is left out; it checks each selected fix below its version first, `python3 tools/known-issues.py lowest --repo DIR --ref origin/HEAD_BRANCH [ID ...]`; a pattern hit or a file present lower goes to one `lowest` agent job that confirms it, and a confirmed lower version moves the fix down only when that reopens no done version -- an S1, or versions with work this round anyway -- else it is recorded in the ledger for the next plan) and `python3 tools/round.py drive --follow` until it prints `ROUND DONE` (versions pipelined: each one's review, gates and CI overlap the next one's merge-up) (a shallow or single-branch clone has origin's `neoforge-*` branches fetched at init, so a version-branch fork is never taken for single-version; the round adds every new finding its jobs grade to the ledger itself and commits it, and the next plan lists those entries under "New in the ledger since the last plan"; each AGENT JOB it prints goes to a fresh subagent, which it records from the job's summary file (`round.py record ID --status failed` only when a worker wrote none); `round.py status`, `round.py resume` after a restart, `round.py retry ID` or `round.py close-anyway` on the owner's answer to a stop; each ticked dependency update is applied once by it: `python3 tools/layer-deps.py resolve --repo DIR --target MC --bump DEP=VERSION`; each version behind this repository's shared library is synced by it: `python3 tools/fork-branches.py sync-shared --repo DIR --target V`, what it would change read first with `python3 tools/fork-branches.py sync-shared --repo DIR --check [--json FILE]`); a single-version fork's done list, its records in `.port/<mc>/`: `python3 tools/fork-branches.py check-done --repo DIR --target MC [--base REF]`; its lessons, harvested here afterwards: `python3 tools/propose-learnings.py --from-plan owner/repo#N` |
| **publish-port** | `> Publish my fork on Modrinth and CurseForge` | `python3 tools/port-publish.py setup --repo DIR --permission URL`; CI: `port-publish.py publish --tag TAG [--platform mr\|cf]` |
| **test-installs** | `> Test my forks' releases on real installs of every version` | `python3 tools/real-install.py plan --fork owner/Lib --fork owner/Mod --all-versions --prerelease`, then `real-install.py install [--profiles] [--with-carrier]` and `real-install.py test` |
| **playtest-plan** | `> Plan playtests for my mod and build them` | `python3 tools/playtest.py inventory --jar MOD.jar`, then playtest-write, -run and -debug per scenario |
| **playtest-write** | `> Write a playtest: summon the boss, kill it with the sword` | `python3 tools/playtest.py lint SCRIPT --jar MOD.jar [--forbid NS:ID]` |
| **playtest-run** | `> Run the playtests on every installed version, with video` | `python3 tools/playtest.py run SCRIPT [--mc V] [--video]` |
| **playtest-debug** | `> Why did that playtest fail?` | `python3 tools/playtest.py explain REPORT_DIR [--mc V]` |
| **review-contribution** | `> Review PR 42` | `python3 tools/review-pr.py --base origin/main --head HEAD` |

**The weekly cycle (keep-current).** Every Tuesday at 08:00 Pacific a GitHub Actions workflow on the fork
(`templates/weekly-plan.yml`, no model, no secrets, no cost) opens one plan issue assigned to the fork owner: the
authors' new commits (for a version-branch fork also the authors' code the head already has that no version branch
took, under the same box), the cascade plan (its decisions apply only when that box is ticked; when the authors have
new commits it is planned with their merge into the head simulated on a throwaway ref first, `fork-branches.py cascade
--plan --head-merge`, so the decisions their code raises up the versions are on Tuesday's plan, and a file that
conflicts in that head merge itself is listed as needing an agent; a single-version fork, which has no cascade, gets
the plan's trial merge of their commits into the head instead, its conflicting files listed the same way or the merge
said clean), and the known issues the round will take, each with its lowest open version (for a version-branch fork
checked below with `tools/known-issues.py lowest`, git only: an entry's `pattern` matching on a lower branch is shown as "may be at <v> (unconfirmed)" -- evidence, not proof: the fix
stays where it is -- and a listed file unchanged below is flagged "lowest may be lower (unchecked)"), as checkboxes pre-ticked for the defaults, with
`/decide <id> <choice>` and `/grade <ID> <S1-S4>` replies. Unticked, the round cascades nothing from the head: each
fix is made at its lowest open version and cascaded from there. Under "Dependency updates" it lists, per fork and per
Minecraft version the fork builds, each pinned CurseForge/Modrinth dependency with a newer compatible build (current ->
new): ticked by default, unticked with the reason shown when the new build is an alpha or beta, a major-version jump,
drops a Minecraft version the fork builds or switches provider; for a version-branch fork also each dependency a
version DROPPED (DEPS.md "dropped: no <v> build") that has a build for it now, as an unticked **restore** box (ticked,
the same `--bump` pins it again, and its integration code then has to compile and be reviewed); a lookup that fails is
listed as "could not check" and never fails the plan. The round applies the ticked ones once, at setup, with `tools/layer-deps.py resolve --bump`, and
nothing else moves a dependency: every resolve (fork-port.py's setup included) keeps a pinned one as it is, and a
single-version fork's pins (its build.gradle coordinates) move only by `--bump` too -- the same for every kind of fork, a
deliberate change to single-version forks, decided by the maintainer. For a version-branch fork the issue also has
"Adopt MMVU's shared code" (`fork-branches.py sync-shared --check`, read-only): which versions the round will bring up
to this repository's current shared tables and helpers, the copies the fork edited by hand while the template moved (an
agent reconciles them; nothing overwrites them), a box per hand-written copy of what the shared library now ships --
NOT ticked by default, as adopting changes the fork's code -- and the code the fork marks `MV-AHEAD-OF-TEMPLATE`, which
the closing comment's Lessons block lists to harvest; a single-version fork's issue has no such section. Wednesday morning Pacific a Claude scheduled
task runs the round from that issue, at a minute jittered per fork set (`round.time` in the fork's config). Every fork
set -- single-version, version-branch or mixed -- runs on a driver, `tools/round.py` (`weekly-plan.py round-task` puts it
in the prompt; `--version-branches`, which older setups pass, changes nothing): from the frozen plan and the clones it builds `round/STATE.json` (per fork and version,
in ladder order, the library before its dependents) and runs every mechanical step itself -- merge-ups, re-pins, the one
full gate run per version (one at a time on the machine), PRs, CI waits, merges, release dispatch, records, cost marks
-- while the session only starts a fresh subagent for each job it prints (the authors' code, the selected fixes, the
fresh review, the fixer, the re-check, a fixer for a step that failed, the close) and records its short summary.
On a version branch whose merge-up changed nothing it ships (only records, the workflow, docs, or Java comments), the
driver pushes the merge and skips that version's review, gates, PR and release, still waiting for CI when the workflow
changed; a version whose only changes are ones a fresh review already passed lower down, file for file
(`.port/CHANGE-REVIEWS.md`), skips its fresh review. On a fork whose Port CI workflow takes the `from_run` and
`dep_override` inputs (both kinds of fork; `ci-check` regenerates an older workflow once), the PR's CI is the gate of
record: the round builds and runs Gate A/B locally with no machine lock, Gate C runs in CI's three environments in
parallel, the release reuses that PR run's jars and attaches its gate summaries (`gates-<env>.md`), which the done
check (`check-done`) reads as the version's Gate C record when no committed `GATES.md` is current -- no commit after the
merge; a library's pre-merge dependent gate is the dependent's CI dispatched with the PR run's candidate jar, named
by the sha256 the run's gates job annotates (read over api.github.com: a cloud session's proxy denies the artifact
storage); a run without those annotations whose artifact cannot be downloaded falls back to the local dependent gate. At
`init` it reads each fork against this repository's shared library (`sync-shared --check`, no checkout touched): a
version a sync would change gets work this round even with nothing else -- first a `sync` step (`fork-branches.py
sync-shared` as its own commit on the version branch), then its gates, PR, merge, release and the dependents' re-pin
(a narrow review when the sync is its only change);
a hand-edited copy whose template moved is a reconcile job instead, the file untouched; a copy the plan ticked to adopt
is an adopt job; a version with none of this and no other work is not queued. When the version below only moved its
own installed helper copy to its library's, a cascade keeps this version's installed copy instead of merging the two.
State
survives a restart (`round.py resume`). A single-version fork is its head alone, in the single-version round's order:
`port-ci.py check` on origin's head (a stale workflow is regenerated on the work branch, never a stop), the authors'
merge (`port-sync.py --port keep-current/DATE --base HEAD_BRANCH --commit SHA`; a conflict is a fixer's job), the
reconcile and port (an agent), the review (`.port/<mc>/REVIEW.md`, written by `fork-branches.py check-done --base`),
its fixes and re-check, the done list's records (TEST-ITEMS.md, INVENTORY-CHECK.json, a library pre-release mark in
DEPS.md) committed, ONE `ci-gates.py` full and minimal pass on the reviewed tree (committed as `.port/<mc>/GATES.md`;
before it, each sibling release jar the head's Port CI workflow installs with a `--dep-jar` step, if any, goes into
`~/.m2` at the step's coordinate, sha256-checked as the step checks it; a different jar there is replaced and said),
the done list (report only while origin's head has no `.port/<mc>/REVIEW.md`, then blocking), the PR (only what that
pass gated), its CI, the merge and the pre-release dispatched right after it; no
lowest check, shared-library sync, cascade, freeze or library record is built for it. The driver never re-pins a
version branch of a version-branch dependent to a single-version library: `round.py init` refuses the set when that
library releases this round and the branch pins it, and otherwise the close names the branch to re-pin by hand. Each library version
-- a version-branch library's version, and a single-version library's head -- merges only after each dependent at that
version passed its gates against the candidate jar (a version branch: Gate C through `fork-port.py --port-checkout`; a
dependent's head: `ci-gates.py` full and minimal; each against a private copy of mavenLocal, `-Dmaven.repo.local`,
holding the jar where its Port CI pins the library; `~/.m2` is never written), so a library crash is a finding before the merge, not a re-release after it; a set of
version-branch forks only keeps its head without that gate. With no
answer the defaults apply; a round that did not run is carried into the next week's issue. Only the library's (first
fork's) repository gets the workflow and the config (`weekly-plan.json`, under `.github` on its head branch), which is
the fork set's only source for the round: forks, upstreams, dependencies, ledger issues. What the round may do without
asking is the owner's `allow` list there -- `push-version-branches` (commits to the head branch, its ledger and README
Ports block included, and to `neoforge-<v>`), `pre-release`, `merge-fork-prs` (the round's own `keep-current/<date>`
work branches, their PRs, and merging them on green), none by default; comments on the plan issue and edits to the
listed ledger issues are always allowed (that is how the round reports), nothing on this repository but reading it
ever is, and anything else it asks on the plan issue. Its closing comment ends with a `### Lessons for the migrator` table -- one row per candidate lesson (version, symptom, the fork's fix commit, why any port to that version would meet it), for single-version and version-branch forks alike; text in the comment, not something the tools do. A later session with write access here runs `python3 tools/propose-learnings.py --from-plan owner/repo#N [--out FILE]`, which turns that block into a checklist (evidence links, the catalogue entries each symptom already matches) and writes nothing to this repository: the curator verifies each row and writes the catalogue entries. The plan workflow runs this repository's tools pinned at a
commit (`migrator_sha` in the config), so a plan is reproducible; the round clones current main, so it gets the
tools' fixes. Both are installed, and re-installed, only by keep-current's setup (`> Set up the weekly round`); a
re-install rebuilds the config from its flags and warns, naming the flag, for each value it would drop -- `plan_owner`
included (`--keep-existing` keeps them).

| Skill | In Claude Code | From the command line |
|---|---|---|
| **install-mod**: install a mod and its dependencies from Modrinth or CurseForge | `> Install Sodium` or `/install-mod Sodium` | `python3 tools/guard-names.py ensure --jar JAR` for each jar it migrates, then `python3 tools/mod-registry/modreg.py search --query "Sodium"`, then `versions`, `deps` and `download` (`ports --name "Sodium"` finds a native port on another project when the mod's own has none) (see [tools/mod-registry/README.md](tools/mod-registry/README.md)) |
| **install-mod**: install fork ports from GitHub, with their dependencies and NeoForge | `> Install these mods from owner/RepoA owner/RepoB` or `/install-mod owner/RepoA owner/RepoB` | `python3 tools/install-port.py owner/RepoA owner/RepoB [--with-optional | --with-untested] [--dry-run] [--replace] [--mods-dir DIR]` (installs the files each port's manifest names, which CI tested together; when a manifest names an older release than the fork's newest full Release, the output opens with `NOT THE NEWEST RELEASE` naming both); one version of a fork of version branches: `owner/repo@neoforge-<mc> --mc <mc>` (that branch's own manifest), then optionally `tools/boot-mods.sh DIR` to boot the folder headless first |
| **test-installs**: test fork ports' released jars on real Launcher installs of every version they support -- NeoForge installed headless, one instance per version (optionally a Launcher profile each and the Gate C carrier), a boot test and the Gate C phases per version, no Gradle | `> Test my forks' releases on real installs of every version` or `/test-installs owner/Lib owner/Mod` | `python3 tools/real-install.py plan --fork owner/Lib --fork owner/Mod (--mc 1.21.1 ... \| --all-versions) [--prerelease] [--variant mr\|cf] [--prefer-pins] [--with-carrier [--carrier-release TAG \| --carrier-dir DIR \| --carrier-jar [MC=]JAR]] [--margin-gb 5]` (read-only; prints jars, sizes and the disk check), then `real-install.py install [--profiles] [--use-installed] [--dry-run]` and `real-install.py test [--phases launch,spawn,battle,gauntlet] [--boot-only] [--timeout 300] [--phase-timeout 900] [--report DIR]` (exit 0 only when every boot and phase passed) |
| **playtest-plan**: plan two tracks by default, the top N (default 10) scenarios by distinct code paths reached and, when the mod has a quest line, one story-progression script that plays it stage by stage (`--no-story`, `--story-only`), grounded in what the mod's jars register, with the user's must-cover list first and their never-touch ids forbidden; `--build` writes, runs and debugs each one in a loop | `> Plan playtests for my mod and build them` or `/playtest-plan --build` | `python3 tools/playtest.py inventory --jar MOD.jar [--jar DEP.jar ...] [--json]` (entities, items, blocks, sound events, attribute, effect and enchantment ids, lang keys by kind, data files, and the advancement tree as `progression`: its longest chains and the stages the mod's code grants, per namespace) |
| **playtest-write**: turn a scenario into a `.playtest` script (Minecraft commands plus `@` directives) with its intent and each step's purpose; `--until-pass` runs and debugs it in a loop | `> Write a playtest: summon the boss, kill it with the sword` or `/playtest-write` | `python3 tools/playtest.py lint SCRIPT [--jar JAR ...] [--forbid NS:ID ...]` (directives and durations; with `--jar`, every id the mod's namespace names must be registered) |
| **playtest-run**: run a playtest in the real client of each installed version (test-installs' instances, the Gate C carrier's `playtest` mode), grade each step, take key frames, optionally record the screen, and write an HTML report to publish as an artifact | `> Run the playtests on every installed version, with video` or `/playtest-run SCRIPT` | `python3 tools/playtest.py run SCRIPT [--plan PLAN] [--mc V ...] [--video] [--carrier-dir DIR \| --carrier-jar [MC=]JAR] [--timeout 900] [--report DIR]` (exit 0 only when every version passed) |
| **playtest-debug**: explain a failed playtest against the script's intent -- the script's, the carrier's or the mod's failure, with the fix or the finding | `> Why did that playtest fail?` or `/playtest-debug REPORT_DIR` | `python3 tools/playtest.py explain REPORT_DIR [--mc V]` (the failed step's lines, observations, frames and log tails, as JSON) |
| **migrate-mod**: port a mod from its jar | `> Migrate <mod name> to 26.2` or `/migrate-mod <mod name> to 26.2` | `python3 tools/guard-names.py ensure --mod-repo DIR` or `--jar JAR` first (the mod and every fork it depends on), then `python3 tools/port.py "<mod name or jar>" --to 26.2` (rerun to resume; `--from-port mods/<modid>` to continue a finished port) |
| **port-fork**: port a mod in its own GitHub fork, with CI, releases and an offer | `> Port my fork <github url> to 1.21.1` or `/port-fork <github url>` | `python3 tools/guard-names.py ensure --mod-repo DIR` or `--jar JAR` first (the mod and every fork it depends on), then `python3 tools/port-upstream.py --repo DIR --modid ID` (the port; its build stage moves each dependency the authors pinned for another version to the target's build, and keeps one already pinned to a target build), `python3 tools/layer-deps.py resolve --repo DIR --target MC --bump DEP[=VERSION] [--bump-all]` (the only way a single-version fork's pinned CurseForge/Modrinth dependency moves: its build.gradle coordinate, or the gradle.properties key that coordinate's version reads, rewritten; `layer-deps.py updates --repo DIR` lists what a bump would take), `tools/ci-gates.py` (gates as CI runs them, `--env full` or `minimal`; `--shipped` adds the shipped-jar row, booting the jar `.port/<mc>/shipped-jar.txt` names, else the only one built; `--gatec none` skips Gate C), `tools/shipped-gate.py --repo DIR --jar BUILT.jar [--mc T --neo V] [--expect-log FILE:REGEX] [--variant cf] [--settle 10]` (boots the built jar in a real NeoForge server's `mods/`, as a player installs it, and checks the mod's own logs -- dev gates run from the class path and cannot see a subsystem that is inert when shipped; with no `--expect-file` the expectations come from `.port/<mc>/shipped-expect-<variant>.txt`, else `.port/<mc>/shipped-expect.txt`, on every kind of fork, none when there is no such file; the server runs `--settle` seconds after "Done" before `stop`, and a crash report, `crash-reports/` file or exit before `stop` fails it), `tools/port-ci.py` (adds the CI, triggered on the checked-out branch -- on a version branch, a checkout holding `.port/<mc>/` for its own version, on `neoforge-<mc>` whichever branch is checked out; `--branch NAME` names another; `--tag-suffix=-release` for a release branch; `--variant-jar GLOB` boots each platform jar the build ships; `--dep-jar-variant SPEC` adds a gates leg on a dependency's other platform jar; `port-ci.py args --repo DIR` prints the command an installed workflow was generated with; `port-ci.py check --repo DIR [--kit-version N]` warns, exit 0, when it pins an older kit or ignores no round records, and prints the command that regenerates it at the newest kit), `tools/port-offer.py` (the offer, install manifest and manual tests; `--release` adds the distance from release; `--push` commits the install manifest, `port-install.json` in the fork's `.github` folder, to the port branch), `tools/port-manifest.py refresh --repo DIR [--tag TAG] [--variant SUBSTRING] [--check] [--commit]` (points that manifest at the newest full Release -- the same platform jar -- and at the sibling jars the workflow pins, sha256 recomputed, every other entry untouched; prints `unchanged` when it is current; refuses, writing nothing, when there is no manifest or the jar cannot be chosen or fetched; run it after a re-pin, since only a promotion through Port CI refreshes it by itself), `tools/port-fleet.py --fleet FILE <step>` (every fork at once) |
| **port-fork**: promote a played pre-release to a full Release | `> Promote my fork's release, I played it` | `python3 tools/port-ci.py promote --fork owner/repo --tag TAG --played-by LOGIN [--notes "..."] [--with-dependency owner/lib]` (best effort, read with `gh api` or from a local clone (`--repo DIR`), else a note: refuses while a dependency release the branch's workflow pins is still a pre-release -- a dependent may cut pre-releases on a dependency's pre-release, never be promoted ahead of it -- unless `--with-dependency` names each one promoted together with it; a tag built against a library pre-release that was not done (DEPS.md's mark) gets a note, not a refusal, since a person who played the pair may promote it; dispatches the fork's Port CI workflow; prints the dispatch when `gh` is not logged in; on the port branch, that run then refreshes the install manifest with `tools/port-manifest.py` in a `[skip ci]` commit when the branch has one) |
| **fork-versions**: build a fork for more 1.21.x and 26.x versions as stacked version branches -- one real branch per version, each made from the one below and holding the full source for its version, with its own build, gates, CI and release; a shared rename/helper library applied per version; fixes and the authors' code merged upward; a cost record; every new pattern harvested back | `> Make my fork build for 1.21.4 too` or `/fork-versions <github url>`; `> Is 1.21.4 done?`; `> Carry the fix up from 1.21.3`; `> I merged 1.21.11 into 26.1.2 by hand: put it through the rows`; `> Check 26.1.2's entityCutout calls`; `> What should I test by hand on each version?` | `python3 tools/guard-names.py ensure --mod-repo DIR` or `--jar JAR` first (the mod and every fork it depends on), then `python3 tools/fork-branches.py add-target --repo DIR --target 1.21.4 [--parent V \| port] [--allow-undone-parent]` (makes `neoforge-1.21.4` from the nearest lower version branch with git plumbing -- no checkout touched, nothing pushed: versions, ranges, Java, NeoForm and GeckoLib from versions.json into gradle.properties and `.port/1.21.4/branch.properties`, the shared library of each targeted version in between applied -- its rows over the source, its helpers, its access-transformer lines; refused, exit 18 and nothing written, while the version it stands on is not done), `python3 tools/fork-port.py --repo DIR --target 1.21.4 [--budget 15] [--gates auto\|none\|ab\|full] [--port-release owner/repo@TAG]` (the same stages as below, on branch `neoforge-1.21.4`; before the first stage that builds -- a `--from-stage measure` re-run too -- each jar `.port/1.21.4/branch.properties` pins (`port_jar.`, at its `substitute.` coordinate; the pins the branch's Port CI `--dep-jar` steps are rendered from) goes into `~/.m2`, sha256-checked, one already there with that sha256 kept and a different one replaced and said; a re-run `--from-stage measure`, `audit`, `gates` or `cost` has no compile-loop budget unless `--budget` gives one, so a gate re-run never edits a file -- a compile error stops it red -- and the round's gate runs pass `--budget 0` themselves: setup checks it out, the generated class moves are applied to its source -- a hand-changed file in its code only, its comments as written -- and kept in `.port/1.21.4/moves.generated.tsv`, then the converters whose change lands in the hop from the parent's version run on it (`tools/mechanical-hop.py`'s ladder: `python3 tools/convert-override-signatures.py --src DIR --crossing 1.21.4..1.21.5` moves only the item / mob hook overrides whose signature changed in that range -- `releaseUsing` and `customServerAiStep` at 1.21.2, `appendHoverText`, `hurtEnemy` and `inventoryTick` at 1.21.5 -- each body kept; a hop over 1.21.2 or 1.21.5 below 26.1.2 names `convert-gear-tiers.py` as an advisory, its shim compiling only from 26.1.2; a `hurtEnemy` that can return false -- on 1.21.1 that spared the durability -- is refused and named; idempotent, each run's output appended to `convert-<name>.out`; at the hop only: a cascade merge runs no converter), the compile loop edits its source, the gates are `ci-gates.py --mc 1.21.4` on it; its records live in `.port/1.21.4/`), `fork-port.py --repo DIR --target 1.21.4 --check-done` (or `fork-branches.py check-done`: the same done list -- on the branch's own checkout it also adds `REVIEW.md`'s `?` rows for the reviewer, never a verdict, and reads the parent branch from origin when the local one is behind -- keyed by the branch's commits -- Gate C green at a commit HEAD carries with nothing jar-relevant changed since, `branch.properties` judged by its build keys only; stubs reviewed; a fresh reviewer's `REVIEW.md` with a row for every file that differs from the parent branch at their merge-base except those the rename rows alone made, and for every file the parent has that the version deletes unless `drops.txt` or a `mixin_remove.` key accounts for it, resources by group, `renames.tsv` by digest; no unresolved merge; `TEST-ITEMS.md`; every base behaviour item accounted for and the committed `INVENTORY-CHECK.json` equal to a fresh check; no development-only library jar; for a library, a dependent recorded green against its jar -- exit 0 or 18; it reads the branch's commit when the checkout is elsewhere and writes nothing), `python3 tools/fork-branches.py cascade --repo DIR 1.21.3..26.3 [--gates none\|compile\|ab\|full] [--dry-run]` (or `fork-port.py --chain`: merges each branch into the next one up, a merge commit each; every file both sides had from the rows alone is re-made from the port branch's new code through that version's rows, every other file the merge changed is merged again with the parent's two sides put through the rows that version adds (the authors' new lines in its names, its own text untouched; a conflict there still stops) and a shared helper a converted line names is installed, a conflicting derived file gets the version's build keys, the version's own workflow and install manifest are kept; an authors' change to a file the parent version lacks is carried too -- a `.java` file through the version's rows, a resource whose copy is still the authors' -- and one the version changed by hand is merged without it, recorded open in `.port/<v>/REMERGE.json` with the authors' diff, printed, and stops the cascade there (the version is not done until a person applies it); the no-model gates run per merged branch; stops at the first conflict -- the merge undone -- or red branch, naming the command to go on; a merge it made and did not gate green is recorded in the clone's git directory, and the next run gates it before it calls that version up to date, reusing the stopped run's worktree when only that run's own records in it changed (a removed one is pruned and re-made); pushes nothing; every merged version then needs a fresh Gate C and review; with an `origin` remote it first fetches it and compares each branch it reads: a local branch behind origin is fast-forwarded -- a checked-out one only when its checkout is clean; `--plan` reads origin's commit instead and changes nothing -- one ahead is used as it is, and one that diverged, or is behind with a dirty checkout, refuses the run, exit 2), `fork-branches.py remake --repo <the version's checkout> --branch neoforge-26.1.2` (for a merge made by hand -- `git merge --no-commit <lower>` where the cascade stopped at a conflict -- the cascade's rows step, which a plain `git merge` skips: each `.java` file the merge changed that both sides had from the rows alone re-made from the port branch through the version's rows, a conflict in it included, every other changed file carried through the rows the version adds, in its code only; staged, never committed; a file still holding conflict markers is named and left, exit 1; refused, exit 2, off a version branch or with no merge in progress or just committed), `fork-branches.py cutout-check --repo <the version's checkout> --branch neoforge-26.1.2 [--fix]` (the 26.1.2 entityCutout swap, CATALOG V60: on a version branch at 26.1.2 or above whose parent builds below it, each `entityCutout(` / `entityCutoutNoCull(` / `entityCutoutCull(` call -- method references and the ZOffset pair too -- is paired with its parent version's line and named by what the parent's call meant: 1.21's `entityCutout` -> `entityCutoutCull`, `entityCutoutNoCull` -> `entityCutout`; only a sure pairing -- a line the line diff finds equal, or a parent call with the same argument text -- is renamed by `--fix`, never committed, and a right tree is left as it is; a call paired by position only, or with no parent line (written on 26.x), is listed `decide <file>:<line>` with a one-line hint; exit 1 with calls to fix and no `--fix`; a single-version checkout, or a version whose parent is 26.1.2 or above, has nothing to check; the same step renames by itself in `add-target`, the rows' own output, the cascade's merge and `remake` -- the last two hold, as a decision, a line the version itself changed against an unchanged parent line, so a culling fix made there on purpose stays -- and `fork-port.py`'s moves stage lists only), `fork-branches.py cascade --repo DIR neoforge-1.21.1..26.3 --plan [--budget-lines 80] [--out DIR]` (keep-current's route: every merge on a throwaway clone, no real branch changed, no model; never stops at a conflict; `CASCADE-PLAN.json`/`.md` with the mechanically merged files and one decision per file and authors' hunk across versions -- the authors' diff, ours at each version, its class: optional integration, port change, authors' fix or escalated -- and the default with its reasoning; `ISSUE.md`, the one owner issue with a machine-readable block, when anything is escalated: their intent infeasible at a version, or over `--budget-lines`), `cascade ... --apply` (the same merges on the real branches: its closing line names keep-current's per-version work-branch PR, `git push origin neoforge-<v>:keep-current/<date>-<v>` -- as it does with `--untaken-ok` or a `round/frozen.json` in the working directory -- never a direct push of the version branch; mechanical items resolved, a default the tool can carry out logged in `.port/DECISIONS.md` (an integration whose dependency the version's DEPS.md drops: ours kept, the authors' change a deferred entry that re-opens once the dependency returns; the same for any file whose authors' change uses only classes the version drops -- drops.txt -- as one such dependency's integration, its package naming it; the plan proposes it as `deferred:<dependency>`), every other decision file merged without the authors' change and held open in `.port/<v>/REMERGE.json` -- the entry travels up with the file -- while every other file and version goes on), `cascade --repo DIR --apply-decisions FILE` (the owner's answers and the fixer's defaults -- take, keep, patch, content, or `deferred:<dependency>`: ours kept and the entry left as a deferred one while that version's DEPS.md drops the dependency, logged `keep (deferred on <dependency>)`, refused for a dependency not dropped there; `/decide <id> deferred:<dependency>` in the plan issue -- applied at every version holding them, the entries cleared and logged, then merged up and re-planned; run straight after `read-plan` it first runs the `--apply` that records the entries, and a `patch` with no diff -- read-plan's default -- leaves its entry open for the fixer, as `--apply` does), `fork-branches.py cascade --repo DIR neoforge-1.21.1..26.3 --port-ref fix/ID [--upstream REMOTE/BRANCH]` (a fix made at the head's version on a branch cut from the lowest version's `port_taken`, carried up in place of the head, so none of the head's untaken authors' code goes with it: refused when the branch holds any; a cascade from a version branch, `1.21.4..26.3`, carries only what that branch took), `fork-branches.py untaken --repo DIR --upstream REMOTE/BRANCH [--json]` (the authors' commits the head has that a version branch has not taken -- in its `port_taken..head` and reachable from their branch, or a merge of it), `fork-branches.py review-authors --repo DIR --range A..B --reviewer WHO` (one review of the authors' change between two port commits, recorded in `.port/AUTHORS-REVIEWS.md` on every version branch: a version that took it through its rows only keeps its review current), `fork-branches.py sync-shared --repo DIR [--target 1.21.4 ...]` (after the shared library moved; every version by default, a partial run warning that a cascade would undo it on the versions above: each version's `shared <v>` table sections rewritten, every targeted version up to it counted, the files the old tables alone made re-made, unedited installed helpers updated (an edited one named and left alone) and newly used ones added, one commit per version branch; pushes nothing), `fork-branches.py freeze --repo DIR --target 1.21.4 [--upstream REMOTE/BRANCH] [--library owner/repo@TAG] [--accept-upstream-drift] [--untaken-ok FROZEN_JSON]` (the buildout's freeze in that version's `branch.properties`; a round's frozen input that leaves the authors' code out -- `--untaken-ok`, or `round/frozen.json` in the working directory, which the round passes -- freezes at the merge-base instead of refusing; a library pin alone never re-checks the authors' branch), `fork-branches.py library-record --repo LIB --target 1.21.4 --dependent DIR` (records in the library's `.port/1.21.4/DEPENDENTS.json` that a dependent passed Gate C against its released jar: a library version is done only then -- a record counts for the tag and commit it names, and once the branch moved on with jar-relevant changes the done check says `record is for <tag> (<commit>), the branch is at <sha>` --, and its Gate C skips spawn and battle), `fork-branches.py ci --repo DIR --target 1.21.4 --modid ID [--kit-version N] [--run]` (the `port-ci.py` command that gives the branch the single-version workflow at its version, with `--branch neoforge-1.21.4` so it runs right on a keep-current work branch too, its library pins included -- each `--dep-jar-variant` moved to the pinned release with its `--dep-jar` -- and the newest published Port CI kit unless `--kit-version` names one; a `--variant-jar *_<v>.jar` for each `.port/1.21.4/shipped-expect-<v>.txt`, so CI boots the jar the local gates check; `--all` for every version branch; `--head` for the head branch, its own workflow read back with only the kit moved to the newest -- `--run` regenerates and commits it in the worktree that already holds the head when that one is clean, and refuses, writing nothing, when it is dirty --, `--modid` optional there and refused when it is not the workflow's, as is an origin/HEAD that is not the workflow's authors' branch; it reads the newer of the local and origin's branch, and warns when a pinned library release has a newer re-cut, `<tag>-r<N>`, to re-pin first; on a version branch -- a checkout holding `.port/<mc>/branch.properties` -- the push trigger also ignores the round's records, `.port/*.md` and `.port/*/*.md` -- GATES.md, REVIEW.md, COST.md and the like, never jar content -- so a push of only those runs no full Port CI, while a single-version fork's workflow is byte-for-byte what it was), `fork-branches.py ci-check --repo DIR [--kit-version N]` (warns about each version branch whose workflow pins an older kit, boots no jar of a variant it keeps expectations for or ignores none of the round's records, and about the head branch when its workflow pins an older kit or its push trigger ignores no records -- each known-issues ledger or README Ports-block commit then runs the full Port CI there -- naming `ci --head --run`; the weekly round runs it; when the release list cannot be read -- api.github.com refused, a 403 -- the newest kit is found by probing the release downloads, `port-ci-kit-vN/SHA256SUMS`, upward from the highest kit the branches pin, and a `FALLBACK` line says so, as `port-ci.py` does from the checkout's workflows; `sync-shared --ci MODID` regenerates the synced branches' workflows, and without it prints the same warnings), `python3 tools/layer-deps.py resolve --repo DIR --target 1.21.4 --port-release owner/lib@TAG` on the version's checkout (pins the library's per-version release jar by sha256 in `branch.properties`; TAG may be a re-cut, `<mod>-<ver>-mc<v>-r<N>`; a re-resolve keeps a pin's "Library pre-release" mark -- from the block, or from `DEPS.md` when the block lacks it -- while the pin is unchanged, also when the same tag is named again and GitHub's release API cannot be read; the library's done list is read at the tag with its own history; `--port-checkout owner/lib=DIR` only copies the jar that checkout built, pinned as development only -- a dependent never builds a library checkout; a re-resolve -- `fork-port.py`'s setup runs one -- keeps every CurseForge/Modrinth pin the block holds as it is, with no lookup, and only `--bump DEP[=VERSION]` or `--bump-all` moves one; `layer-deps.py updates --repo DIR [--json]` lists each pinned dependency with a newer compatible build and the reason to look first, and on a version branch each dropped one that has a build for it now, as `restore`); a checkout with the removed `.multiversion/` layer at HEAD is refused by every tool (exit 2), naming the last migrator commit that supports it, where `fork-branches.py migrate` moves it to version branches (`tools/fork-kind.py detect --repo DIR` says which kind a fork is) |
| **fork-versions**: the known-issues ledger -- every known defect graded S1-S4 from recorded rubric inputs (crash, kick, data, hang, security and build are S1; S2 on a path nothing calls yet, `--reach latent`, and never lower but by the owner), optionally scoped to one platform's jar (an owner override recorded), with its status per Minecraft version, in `.port/KNOWN-ISSUES.json` on the head branch (`.port/<mod>/` for a multi-mod repository); only an S1 reopens a done version; Port CI ignores a ledger-only push, it never makes gates or a review stale and the cascade never carries it | `> Record this bug in my fork's known issues` / `> Which known issues should this run take?` / `> Write the known-issues section of the 1.21.5 release notes` | `python3 tools/known-issues.py add --repo DIR --title T --failure F --visibility V --reach R --workaround yes/no --lowest MC`, `grade ID [--override S --reason WHY [--owner]]`, `status ID --set MC=STATE` (STATE `open`, `fixed@<commit>`, `wontfix:<reason>`, `duplicate:<ID>` -- that version tracked by another issue of the ledger, never open -- or `n/a`; also `MC,MC=STATE` or `FROM..TO=STATE` in one `--set`; `add` numbers a new entry in the ledger's own prefix, `EXM-025` after `EXM-024`, and warns about a likely duplicate: the same file and a similar title; `add --suspected` marks one added without a reproduction or a version comparison -- `S3?` in `list`, "(suspected)" in the pinned and plan issues -- `confirm ID [--evidence TEXT]` removes the mark, and `disprove ID --version V --kind no-regression\|api-unchanged\|test-passes --evidence TEXT [--check]` closes a suspected S3/S4 as `wontfix:not a defect (disproved, KIND): TEXT`, refused for a confirmed entry, an S1/S2 or evidence citing no commit sha, `path:line` or test name), `list [--version MC] [--min S]`, `select [--budget N] [--touched PATH] [--add ID] [--remove ID]`, `render-issue` (the pinned issue), `release-notes --version MC --tag TAG [--min S3]`, `reopens --version MC --severity S`, `blocks-publish --version MC --platform mr/cf` (exit 1: an open S1 against that jar); `--ref <head branch>` reads the committed ledger; `render-plan --input FILE --out FILE` and `read-plan --issue N\|--latest --slug O/R` (the weekly plan issue and its frozen answers: checkboxes, and `/decide` / `/grade` replies counted only from the fork owner, a member of the owner team or a user with write access); `render-ports --repo DIR --head BRANCH [--fetch-releases] [--pinned-issue N] --write` (the README's Ports block between `<!-- ports:begin -->` and `<!-- ports:end -->`, one row per Minecraft version; nothing outside the markers changes); `add-target` never copies the ledger, nor the weekly plan's files, onto a version branch |
| **port-fork**: the handoff | `> Prepare the handoff for my forks` | `tools/manual-tests.py --repo DIR` (the ten manual tests; per-port warnings from the work folder's `manual-tests.notes.json`), `tools/offer-page.py --mods FILE --out page.html` (one page for every fork) |
| **port-fork**: the authors' release vs. their newest code | `> How far is my fork from the authors' release?` / `> Make a release branch for my fork` / `> Bring my fork up to date with the authors` | `tools/port-provenance.py --repo DIR --base REF --registry modrinth:SLUG --registry curseforge:ID --mc VER --loader forge` (the gap), `tools/port-derive.py` (a release branch; `--resolve` finishes it with a model, at a stop), `tools/port-sync.py --repo DIR --port BRANCH --upstream origin/BRANCH` (merge the authors' new commits, after fast-forwarding a local BRANCH behind origin's; a diverged one is refused; the merged data files then put in the port's version's forms, their own commit, refusals in `.port/<mc>/DATA-FORMS.md`; `--data-forms` alone after a resolved conflict) |
| **keep-current**: keep a fork port current with its authors -- merge their new code, reconcile every fix we carry against theirs (take theirs when an A/B proves it covers ours), port, gate, open a PR into the fork's versioned branch (merged on green where the fork allows); weekly: a zero-model plan issue every Tuesday 08:00 Pacific for the fork owner, and the round Wednesday morning Pacific as a Claude scheduled task on the model set at setup (Opus recommended; the compile loop keeps its own Haiku -> Sonnet -> Opus ladder) | `> Keep my forks current` / `> Did the authors fix the bugs we fixed?` / `> Set up the weekly round` / `> Run this week's round` | setup: `python3 tools/weekly-plan.py install --repo DIR --fork owner/repo@HEAD[=UPSTREAM_URL#BRANCH] [--fork ...] [--plan-owner LOGIN\|@org/team] [--allow push-version-branches\|pre-release\|merge-fork-prs ...] [--ledger-issues REPO=N,N] [--depends-on REPO=REPO] [--replace-task NAME ...] [--trailer "KEY: VALUE" ...] [--default-branch-too] [--keep-existing]` (without `=UPSTREAM_URL#BRANCH` the authors' upstream is the GitHub fork parent's default branch; `--allow` is what the unattended round may do without asking, none by default, carried verbatim in the round prompt, and `automerge` follows `merge-fork-prs`; `--trailer` adds attribution lines to the commit; a re-run over an existing config warns on stderr for each `allow`, `ledger_issues` or `depends_on` its flags would drop, naming the flag to re-pass, and `--keep-existing` keeps those values; commits, on the library's repository only, the fork's workflow `weekly-plan.yml` -- `templates/weekly-plan.yml` with this repository pinned at a commit, golden `tools/golden/weekly-plan.yml` -- and its config `weekly-plan.json`, both under the fork's `.github`, on the head branch in one `[skip ci]` commit; refuses an organization-owned fork without `--plan-owner`, and exits 3 when the head branch is not the default branch, which is the only branch GitHub runs a schedule from, unless `--default-branch-too`) and `weekly-plan.py round-task --repo DIR --existing NAME ...` (the Wednesday task as JSON: `CRON_TZ=America/Los_Angeles`, a minute below 08:30 jittered per fork set, a fresh session per fire, push and email notifications, the `allow` list in its prompt, and `replaces`: exact task names -- its own, each `--replace-task`, and each `--existing` name that starts with keep-current and names a fork of the set; `repositories`, every fork, each of which must be attached to the task, since a scheduled session cannot attach one; and `model`, the model to set on it, the most capable one priced in `tools/model-prices.tsv`; its prompt checks the clones first and stops before freezing when one is missing, leaving the plan issue open, and keeps the round cheap on its driver, for every fork set: the orchestrating session ports, gates and reviews nothing itself -- `tools/round.py` runs every mechanical step and waits in its own loop, since each idle gap over the 5-minute cache re-writes the whole context; each AGENT JOB it prints goes to a fresh subagent that hands back a summary of at most ~40 lines, recorded with its `record` line; and after a restart, or past ~100k tokens, a fresh session continues from `round/STATE.json` with `round.py resume`); `read-plan` also says per fork whether `take_authors` was the plan's default or the owner's; every GitHub call of `known-issues.py` (`read-plan`, `render-ports --fetch-releases`) is bounded by `MMVU_GH_TIMEOUT` (default 60 s) and a read falls back to REST (a token's call to `gh`, `gh` to anonymous REST); the plan workflow's steps are `weekly-plan.py fetch`, `cascade`, `select`, `deps` (each pinned CurseForge/Modrinth dependency with a newer compatible build, per Minecraft version the fork builds -- Modrinth's public API, no key; a failed lookup is "could not check", never a failed plan), `previous` and `open` around `known-issues.py render-plan` (its "Dependency updates" boxes: ticked by default, unticked with the reason for an alpha/beta, a major-version jump, a dropped Minecraft version or a provider switch); `read-plan` returns each fork's ticked ones as `dep_bumps`, each with its `layer-deps.py resolve --bump` command, which the round runs once at setup -- on a single-version fork too, where it rewrites the build.gradle coordinate or its gradle.properties key -- and no other step moves a dependency; the round starts with `python3 tools/round-preflight.py --fork owner/repo=DIR ... [--versions 1.21.1,26.3] [--no-client]` (`--versions` names the versions instead of reading each clone's, `--no-client` skips the client's needs for a round without Gate C; one line per need, `MISSING` with what to do: each fork's clone -- a scheduled session cannot attach a repository, so a missing one stops the round before it freezes --, the GitHub way, a JDK per version, 25 for 26.x -- looked for, as `tools/shipped-gate.py` does, at `JAVA_HOME` first, then `JAVA_HOME_<n>`, `/usr/lib/jvm`, `/opt`, `~/.gradle/jdks`, `~/.jdks`, SDKMAN and macOS's JavaVirtualMachines -- and for the client `libEGL.so.1` at 26.3 and `XDG_RUNTIME_DIR`; on every kind of fork its `origin/HEAD` -- a warning, not counted missing; fix: `git remote set-head origin -a` --; for a version-branch fork the remote each version's upstream freeze names, which the gates stop without, exit 19, with the fetch-only `git remote add` to run; for a single-version fork a remote fetching the authors' repository its entry in the set's `weekly-plan.json` (in the library's `.github` folder) names, read from the clones given, which `port-sync.py` merges from, missing with the fetch-only `git remote add` when none does; a shallow single-branch clone of a version branch has its other version branches read from origin by name, `git ls-remote`, at their version's JDK), then `python3 tools/known-issues.py read-plan --latest --slug owner/repo --out frozen.json --decisions-dir DIR [--now]` (before the plan's deadline it warns, as an early freeze cuts the owner's window; `--now` is an early start the owner asked for, recorded in `frozen.json` as `early_start`; per version-branch fork it says whether the head is cascaded -- `head_cascade`, false when the owner unticked the authors' code, and then no decisions file is written -- and, per selected issue, the version its fix is made at, the lowest one still open, and the one cascade that carries it) and ends with `python3 tools/port-cost.py round-table --since TIME --fork owner/repo=DIR ...` (per fork, per version, totals; a scheduled session whose transcripts touch no checkout path: `round-table --since TIME --session-cwd DIR` (or `--session DIR`, that session's transcript directory), that session's own requests split per version at the marks `port-cost.py mark --fork owner/repo --version V [--phase upstream\|backlog\|review\|overhead]` wrote to `round/marks.tsv` as each version, and each kind of work on it, started; with phased marks the table is per fork x version x phase -- upstream the authors' cascade and its conflicts, backlog the selected known-issue fixes, review the fresh reviews and their fixes, overhead pins, gates, CI waits and records -- with each version's total and per-phase totals, a marks file without phases giving the table as before; the closing comment adds, per version, the authors' change size (commits, files, changed lines) and the files the cascade merged mechanically versus by an agent, for cost per authors' changed line week over week); per fork, `python3 tools/guard-names.py ensure --mod-repo DIR` or `--jar JAR` first (the mod and every fork it depends on), then `python3 tools/port-sync.py --repo DIR --port BRANCH --upstream REMOTE/BRANCH [--base PORT_BRANCH] [--commit SHA]` (first compares BRANCH -- or, with `--base`, the port branch the work branch BRANCH is cut from, which it then cuts -- with origin's, as the version-branch cascade does -- `tools/origin-sync.py`: strictly behind, fast-forwarded; diverged, refused, exit 2; never a reset or force; an existing work branch lacking the base's tip is refused; `--commit SHA` merges exactly that commit, which `--upstream` must reach, the weekly round's frozen `upstream_commit`; `--dry-run` reads origin's commit and moves nothing) and, once per round on a single-version fork, `python3 tools/port-ci.py check --repo DIR` (a warning, never a stop, when the port branch's workflow pins an older Port CI kit or ignores no round records, with the command that regenerates it at the newest kit: the check `fork-branches.py ci-check` makes of a version-branch fork's head), then `python3 tools/fix-ledger.py worksheet --repo DIR --port BRANCH --upstream REMOTE/BRANCH` (the reconcile worksheet), `fix-ledger.py render --ledger FILE` (the fix ledger for a fork issue), `fix-ledger.py check --ledger FILE --release BRANCH` (refuses a dropped fix without an A/B, or a crash/kick left open on a released branch); for a fork of version branches: `python3 tools/fork-branches.py cascade --repo DIR neoforge-1.21.1..<newest> --plan` once the port branch has the authors' code (every decision found in one pass on a throwaway clone and written to `CASCADE-PLAN.json`/`.md`), then `cascade ... --apply --gates ab` (each version branch takes it in chain order by a merge commit, the files the rows alone made re-made through each version's rows, mechanical items resolved, a file needing a decision merged without the authors' change and held open in `.port/<v>/REMERGE.json` while everything else goes on, the no-model gates per branch), the defaults decided by the agent and the escalated items filed as ONE issue (`ISSUE.md`), and `cascade --apply-decisions FILE` with the answers, then a PR per merged version branch with `fork-branches.py freeze --repo DIR --target <v>` committed in it (the freeze is bookkeeping: no gate goes stale, and it gets no push run of its own after the merge), the version's gates in their cheapest correct order -- a re-pin first, the cascade's `--gates ab`, the review and its fixes, then one `fork-port.py --repo DIR --target <v> --from-stage measure --gates full` on the final tree, nothing re-run for a records-only commit -- and its pre-release dispatched right after its merge, the merge's push run and the release run awaited together in one blocking command (the release job needs its own run's full gates on exactly the released tree); a single-version fork's head the same on the driver (`round.py`), its done list `python3 tools/fork-branches.py check-done --repo DIR --target MC --base REF` (the change's base recorded in `.port/<mc>/REVIEW.md`), a version-branch fork's head keeping its push run first; after a weekly round, in a session that can write here: `python3 tools/propose-learnings.py --from-plan owner/repo#N [--comments-file FILE] [--out FILE]` (the round's `### Lessons for the migrator` block, from its closing comment on the plan issue, as a checklist: each row's evidence link and the catalogue entries its symptom already matches; a row naming a mod on the `FORBIDDEN_NAMES_FILE` list is flagged; writes nothing here, `--out` refused inside this checkout; exit 1 when the issue has no such block) |
| **publish-port**: publish a fork port's promoted releases as an unofficial community version on Modrinth and CurseForge -- set up both projects (generated page and config), publish a release, and publish automatically on promotion; refuses without the authors' permission, for a pre-release, or with a crash/kick/hang open in the fix ledger | `> Publish my fork on Modrinth and CurseForge` / `> Set up the community projects for my fork` | `python3 tools/guard-names.py ensure --mod-repo DIR` or `--jar JAR` first (the mod and every fork it depends on), then `python3 tools/port-publish.py setup --repo DIR --permission URL --ledger-issue OWNER/FORK#N` (the project page and the publish config in the fork: the Modrinth project is titled `<name> - Community Port (<mc>+)` and the page is headed `<name> - Community Port (NeoForge)`, the CurseForge name, since the same page is pasted there, links the original on Modrinth, CurseForge and GitHub at the top, and gives the original's credits unchanged, naming the file they come from; the config also holds Modrinth's environment, sent with every upload, and the content disclosures -- derivative work and AI-generated content -- set by `publish_setup`), then the fork's Port CI does the rest: `publish_setup = true` (the Modrinth draft), `publish = TAG` (one release), and every promote publishes; a fork of version branches publishes each version from its own branch exactly like a single-version fork (dispatch Port CI on `neoforge-<v>` with `publish = <its tag>`: the tag's `-mc<v>` gives the game version); a single-version fork publishes exactly as before; `port-publish.py publish --tag TAG --platform mr\|cf` (default both) uploads one platform alone, and each platform asked is judged by the head branch's known-issues ledger (`known-issues.py blocks-publish`, and every S1 recorded `fixed@<commit>` at that version must have its commit in the tag -- git proves it, so nothing is written per release; fix-ledger issues are optional, checked only when the config lists `ledger_issues`): one an open S1 or a missing fix blocks is held back and named with why while the other goes on (a cf-only S1 publishes to Modrinth and skips CurseForge); every platform held back fails the run; the page's Known issues section lists each open S2 of the known-issues ledger in its players' words (`known-issues.py add --player` or `player ID TEXT`) with the versions it is open at, leaves S3 and S4 out, and links the fork's pinned Known issues issue for them (else its issue tracker) -- `python3 tools/known-issues.py issue --repo DIR --slug owner/repo [--dry-run]` creates that issue from the ledger and pins it, or updates its body in place, and `python3 tools/known-issues.py page --repo DIR --fork owner/repo [--details-url URL]` prints the section again for the page when an S2 opens or is closed; a dependency is judged by the pin of the workflow at the tag (or the install manifest when it describes the tag), never the branch's moved-on pin; a release built against a library pre-release that was not done (DEPS.md's "Library pre-release (not done)" mark, on either kind of fork) publishes only with that exact library release -- each library jar the tag's workflow pins is a promoted Release, published on that platform, and still the asset the tag's build checked by sha256 -- and the library's own done record is not asked; a port that depends on a library CurseForge reviews file by file sets `"manual_release": true` under `curseforge` in its publish config (off by default, so publishing stays fully automatic): its CurseForge files then go through review in parallel with the library's but are uploaded held (approved, not shown to players), the run's log says so, and the owner clicks Release on each file's page once the library's file for that Minecraft version is live -- CurseForge has no API to release one; Modrinth reviews projects, not versions, so nothing is held there |
| **review-contribution**: review a pull request to this repository | `> Review PR 42` or `/review-contribution 42` | `python3 tools/review-pr.py --base origin/main --head HEAD` (the mechanical checks; the judgement half is the skill). A PR that removes a catalogue entry or self-test assertions on purpose declares each in a commit line, `Catalog-removed: <file>::<id> -- <reason>` or `Tests-removed: <script> <before>-><after> -- <reason>` ([CONTRIBUTING.md](CONTRIBUTING.md)) |

Each script prints its options with `--help`. Most also have `--self-check`, which runs their own tests.
`python3 tools/fork-branches.py --self-check --deep` adds the passes CI does not run -- the cascade plan/apply/decisions
flow, `migrate --library`, 200 random cascade sequences checked against the taken-or-recorded invariant
(`FB_RANDOM_SEQUENCES=N` sets the count) and the whole self-check again under `core.autocrlf=true`, about 12 minutes:
run it before a release of the tool.

Both routes run the same deterministic stage after each hop's rename tables and before any worker sees an
error (`tools/mechanical-hop.py`, called by `port.py` and `port-upstream.py`): on a Forge → 1.21.1 hop the
access transformer, Forge code shapes, SimpleChannel → payloads, MCreator variables, GUI overlays → layers, Forge
capabilities → a mod-local compat layer wired into NeoForge (`tools/convert-capabilities.py`) and Holder fixes; on a 26.2 hop the member
renames and the converters for GUI hooks, entity save/load, data attachments, changed hook signatures, tool tiers
and armour, render types and shaders, entity render state and GeckoLib. A converter added there reaches both
routes; `tools/test-port-tools.sh` fails if a route stops going through it. A version-branch fork (fork-versions) hops
one version at a time instead, so `fork-port.py`'s moves stage runs only the converters a change of which lands in that
hop (`tools/mechanical-hop.py`'s `LADDER`: today the hook signatures, with `--crossing`); the era stage and a
single-version fork run exactly what they ran before. Two
gates guard bug classes found in shipped ports: a field-less network payload behind `StreamCodec.unit`
(`tools/audit-unit-codecs.py`), and data files the server silently rejected (a row of `tools/ci-gates.py`).
Three static checks only report (a `REPORT` row in GATES.md when they find something, nothing when they do not, never
a red gate). Report-only rows also appear on a single-version fork when a check finds something; a clean tree's
GATES.md is unchanged. A fork's CI runs them from the first Port CI kit release that ships them (`tools/port-ci-kit.txt`):
the data row also names location-predicate keys 1.20.5 renamed
(`structure`/`biome`, ignored by 1.21, so an advancement is granted anywhere; `tools/fix-datapack-layout.py --apply`
renames them); `tools/audit-wiring.py` lists event subscribers and capability registrations the tree lost against a
version branch's parent branch, else the authors' ref (`--base`), and providers answering null for a null side; and
`tools/audit-descriptors.py` checks vanilla class names and descriptors kept as strings outside the mixins (coremods,
ASM transformers, reflection) against the build's sources jar. Two more report the same way on every kind of fork, so a
single-version round checks the authors' newly merged code too: `tools/audit-mixin-targets.py` (every mixin injection
point against the sources jar the build staged, run once per `src/*/java` source root holding mixins) and `tools/audit-event-listeners.py` (no event listener names an abstract
or interface event, asked of Gradle's own compile classpath, `tools/qtc-init.gradle`, with the JDK's `javap`). Each runs
only when the tree has something for it (a mixin; a listener); with an input missing -- no staged sources jar, no
classpath, no `javap`, a timeout (300 s for the classpath, 600 s per audit run), or a mixin a config lists that the
audit cannot read (Kotlin, or outside a `mixin/` or `mixins/` package) -- its row says `skipped: <why>`, and an audit
that crashes writes no row. On a version branch,
`fork-port.py`'s audit stage still gates on both before its gates stage runs `ci-gates.py`, so there a row appears only
when that stage was skipped or the branch's own Port CI runs the gates. A fork's CI gets these two rows from the next
Port CI kit release (the kit now ships `tools/audit-event-listeners.py` and `tools/qtc-init.gradle` again). One more
static check reports on every route: `tools/audit-zero-uses.py` names a tool tier with 0 uses fed to `durability()`
(`getUses()` 0, an enum tier constant, `ForgeTier` / `SimpleTier` / `ToolMaterial` with 0, `durability(0)`: an
unbreakable tool that breaks on its first hit in survival, CATALOG 177) -- a REPORT row of `ci-gates.py` on a
single-version fork and a version branch alike (a fork's CI from the next kit release), a hit of the catalogue sweep
(`tools/run-catalog-scans.sh`), and a WARNING in `fork-port.py`'s audit stage line; never a red gate or a stop.

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
