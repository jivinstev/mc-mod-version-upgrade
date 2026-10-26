---
name: playtest-plan
description: Plan playtests for a mod on two tracks by default -- technical coverage (the top N, default 10, scenarios that between them reach the most distinct code paths: each boss and its music or narration, each signature item's effect, each mob's attack on a survival player, death and respawn, GUIs and keybinds) and, when the mod has one, its story progression (one end-to-end script that plays the mod's quest line stage by stage, read from its advancement tree and quest code) -- grounded in what the mod's jars actually register, honouring the user's own must-cover list and never-touch list. With --build it then writes, runs and debugs each script in a loop (playtest-write, playtest-run, playtest-debug) until it passes or the failure is proven to be the mod's. Use when the user asks what to playtest, for a smoke test, a playtest list or a story playthrough for a mod, or to build and get playtest scripts right.
---

# Plan playtests

A playtest is a script the Gate C carrier runs in the real client: Minecraft commands plus `@` directives
(`tools/playtest.py`'s docstring is the reference). This skill decides **what** to test. playtest-write turns one
scenario into a script, playtest-run runs it on the installed versions, and playtest-debug explains a failure.

**Which fork:** playtests run on released or pre-released jars in the instances that `tools/real-install.py` installed
(test-installs), so a single-version fork and a fork of version branches are planned the same way. Nothing here
changes a fork's build or branches.

## 1. Ask (only what is not already clear)

1. **Which mod, and where do its scripts live?** Default: `playtests/` at the root of the mod's port branch, a folder
   its build never reads. A user's private script can live anywhere outside this repository (or in a gitignored
   folder). Scripts and plans never go into this repository: they name the mod.
2. **How many scenarios?** Default 10.
3. **The user's own list.** Things the user said must be covered become scenarios first, in their words, and are
   never dropped to make room. Things the user said never to touch (a debug entity, a test item) go in the plan's
   `forbid` list, and every script is linted against it.
4. **`--build`?** Without it, the plan is the deliverable. With it, go on to step 4.
5. **Which tracks?** Both by default: technical coverage and story progression. `--no-story` skips the story track;
   `--story-only` plans just the story script.

## 2. Ground the plan in the jars

Use the instances that `real-install.py install --with-carrier` set up (its plan, `~/.mmvu/real-install/<label>/plan.json`,
lists each instance). Take the mod's jar and the jars of its dependencies from one instance's `mods/` and run:

```bash
python3 tools/playtest.py inventory --jar MOD.jar --jar DEP.jar
```

This lists, per namespace, the entities, items, blocks, sound events, lang keys grouped by kind (`subtitles`,
`bossshow`, `dialogue`, `message`, `death`, `key`, `gui`, ...), and the data files (loot tables, advancements,
recipes). A scenario names only ids from this list. Where the mod's source is at hand (the fork checkout), read the
classes behind the ids a scenario relies on before promising a behaviour:

- an item's `hurtEnemy` or `use` (a one-shot weapon, a right-click attack);
- an entity's `tick`, `die` and `hurt` (boss music, a narration on death, an entity that refuses to die);
- the client handlers that play music or subtitles.
- **the registration itself:** an id with a lang key can still be registered only when a companion mod is present
  (an `if (CompatCheck...)` around the `register` call). Plan only ids registered unconditionally, or say which
  companion mod the scenario needs.
- **what the scenario expects to see:** a boss bar needs a `ServerBossEvent` in the entity, and music needs the
  handler that plays it. Do not promise either from the entity's name alone.

## 3. Choose the scenarios

Rank candidate scenarios by **distinct code paths reached**, not by feature count. A scenario that reaches a path
no other one reaches outranks a second scenario on a path already covered. Cover, in roughly this order:

- **The user's list**, each item as a scenario or as a step of one.
- **Each boss or unique mob:** summoned, its music or narration observed (`@expect sound|subtitle|chat|bossbar`), and
  killed (or proven not to die, when that is its design).
- **Each signature item:** obtained the way a player would (creative inventory or `/give`), used (`@key key.attack`,
  `@key key.use`), its effect checked with `@assert`.
- **Survival:** a hostile mob kills the player, the player respawns, and the run carries on. Death, respawn and
  inventory loss are their own code paths.
- **Each mod keybind and GUI:** opened (`@key`), clicked, and closed.
- **Load and save:** one scenario that relogs or reloads the world, when the mod stores data on entities or players.

For each scenario write: a name, the intent (one sentence, what it proves), the steps in plain words, the code paths
it reaches (ids and classes), and what would count as the mod's failure as against the script's.

Save the plan as `playtests/PLAN.md` next to the scripts, with a front-matter-like header: the mod, the date, `N`, the
`forbid` list, and the jars it was grounded in (names and versions). Show the user the table, scenario by row.

## 3b. The story track (when the mod has one)

Technical coverage tests the parts. The story track tests the journey: can a player walk the mod's intended
progression from its first stage to its last?

1. **Find it.** `playtest.py inventory` prints a `progression:` line per namespace: the advancement count, how many
   are granted by the mod's own code (trigger `minecraft:impossible`, marked `*`), and the longest parent chains.
   - "A story to play through" means a code-granted chain of four or more stages.
   - "No story chain" means the mod is items and mobs. Say so in the plan and make no story script.
2. **Map each stage from the source.** For each stage, find:
   - the `grant(...)` call or criterion that awards it;
   - what the player must do there: talk to an NPC, pick a dialogue option, enter a structure, win a raid, read a
     note;
   - which parts commands may set up (a structure's location through `locate` and a teleport, a summoned NPC) and
     which must be player input (`@key key.use` on the NPC, `@click button "<option label>"`, a fight with the mod's
     weapon).
   - what would break the stage in a test world: a structure that only worldgen places, a village, another
     dimension, a companion mod, a long timer.
   Write the map into `PLAN.md` as its own section, stage by stage.
3. **One script, `NN-story.playtest`.** One step per stage, each gated on the stage's advancement:
   `@assert execute if entity @s[advancements={<ns>:<id>=true}]`.
   - Travel to a structure is `@goto structure <id or #tag> [in <dimension>]` (vanilla `/locate`, then the player inside
     its start piece, where the structure's \"inside\" checks count); the stage's mechanic never is a command.
   - **`/place structure` does not do.** It builds the blocks but registers no structure, so a mod's "inside the
     structure" checks (and vanilla's location advancements) never fire there. Only a naturally generated one counts.
   - The world: vanilla's superflat with `@world flat structures` (structures are off in the default flat world, for a
     clean field) generates villages and strongholds in the overworld,
     and the Nether and the End generate normally, mods' structures included. A stage whose structure only the
     normal overworld generates needs `@world normal`, or is a named step saying why it is not reached.
   - A stage the script cannot reach is a `@step` that says why (`# needs a village: not scriptable here`), not a
     silent skip.
   - Rank it alongside the technical scenarios: it counts as one of the N.

## 4. `--build`: write, run and debug until each script is right

For each scenario, in plan order, starting on one fast version (the base version, say 1.21.1) and one newest version:

1. **playtest-write:** the scenario becomes `playtests/<NN>-<slug>.playtest`. Lint it with
   `python3 tools/playtest.py lint SCRIPT --jar MOD.jar --forbid NS:ID ...` until it is clean.
2. **playtest-run:** `python3 tools/playtest.py run SCRIPT --mc BASE --mc NEWEST`.
3. **playtest-debug** on every FAIL. It says whether the script is wrong (a wrong id, a timing too tight, an aim that
   misses, an expectation the mod never promised) or the mod is. Fix the script and go back to 2. A failure that is
   the mod's is a **finding**: it stays in the script, and the plan records it (with the known-issues ledger when the
   fork has one).
4. Stop a scenario after 5 rounds without progress, and say why.

Then run every passing script on all installed versions (`playtest-run` with `--video` when the user wants
recordings), and report:

- the plan;
- each script with its verdict per version;
- each finding;
- what changed in the scripts between rounds, and why.

**Feed lessons back.** A failure mode that playtest-debug had to work out from scratch, and that the next mod will hit
too (a timing, a directive that needed a missing option, a carrier gap), goes into this skill, playtest-write or the
carrier in the same pull request. That pull request is to this repository, never with the mod's names
(`tools/guard-names.py check` must pass).
