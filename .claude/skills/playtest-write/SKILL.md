---
name: playtest-write
description: Write a playtest script from a scenario in plain words -- Minecraft commands for the setup and the world, `@` directives for what a player does (aim, press a key binding, click) and what must be seen or heard (sounds, subtitles, chat, titles, boss bars, screens, assertions), with the scenario's intent and each step's purpose written into the script so a failure can be judged against them. Lints it against the mod's jars and the plan's forbidden ids. With --until-pass it runs and debugs the script in a loop until it passes. Use when the user asks to write, fix or turn a scenario into a playtest script.
---

# Write a playtest script

**Which fork:** the same for every fork. A script is about the game, not the build: it runs on the installed
instances (test-installs), whichever branch the jars came from.

The input is one scenario: from a playtest-plan `PLAN.md` row, or from the user in their own words. The output is
one `.playtest` file (UTF-8). The grammar is `tools/playtest-lint.py`'s docstring (the carrier runs exactly that). In short:

- **A line without `@`** is a Minecraft command, run as the player typing it (the `/` is optional). A command that
  fails fails its step.
- **`@intent`** says what the whole script proves. **`@step <name>`** starts a step.
- **Checks:** `@assert <command>`, `@expect <kind> <match> [gone] [within <N>t|<N>s]`, `@allow error <regex>`.
- **Player inputs:** `@aim`, `@key`, `@click`.
- **Flow and evidence:** `@wait`, `@shot` (a name of letters, digits, `.`, `_` and `-`), `@end`.
- **Header only:** `@world [flat|normal] [seed=<n>]` (default flat, seed 0) and `@continue-on-fail`.
- **Automatic:** a new ERROR line in the log, or a WARN carrying an exception, fails the step unless `@allow error`
  covers it. A step's own `@intent` records its purpose. Each step's lines run one per tick.

## How to write it

1. **Intent first.** `@intent` is one sentence: what a pass proves. Each `@step` name says what that step is for, in
   the player's terms ("the wraith's music starts", not "expect 3"). Put a `#` comment above any step whose reason
   is not obvious: why this wait, why this aim. playtest-debug judges a failure against these.
2. **Only real ids.** Take every `<namespace>:<id>` from `python3 tools/playtest.py inventory --jar MOD.jar`.
   - Entities, items and blocks come from their lang keys.
   - Sounds come from `sounds.json`: a sound expectation matches the **sound event id** (`examplemod:boss_music`).
   - Subtitles match the **lang key** or its English text.
   - The plan's `forbid` ids are never named.
3. **Set the world up with commands, and do the thing the way a player would.**
   - Commands set the world up: `gamemode`, `difficulty`, `time set`, `weather clear`, `tp`, `summon`, `give`,
     `effect`, `kill @e[type=...]`.
   - The feature under test is driven through player input: `@aim entity <type>`, then `@key key.attack`. A
     `/damage` or `/kill` on the boss proves nothing about the sword.
   - Use `gamerule doImmediateRespawn true` before a death the script wants to survive (the carrier respawns the
     player anyway).
4. **Make each check observable and bounded.**
   - Every `@expect` has a `within` sized to the behaviour: music within 10s of a summon, not 2s. `gone` checks that
     something stopped.
   - `@assert execute if entity @e[type=X]` (or `unless`) checks that something exists or died.
   - For a player death, `@assert execute if entity @s[nbt={DeathTime:...}]` is fragile. Prefer
     `@expect chat <death message key or text> within 10s`, or the respawn screen with `@expect screen`.
5. **Contain the noise.** Summon near the player but not inside them (`~ ~ ~4`). Clear other mobs before a fight. Set
   `difficulty` before it matters. Kill leftovers in the next step's setup, not by luck.
6. **Evidence.** Put a `@shot` at the moment a human would want to see: the boss on screen, the item in hand, the
   death screen. Every step's end and every failure is captured anyway.

## Traps the live runs found

**Work with the mod's design, not around it.** When the mod deliberately changes how something works (its bosses
ignore `/kill`, a weapon is consumed, a mob blocks), that is not a finding. Use the player's own means instead of
commands: kill a boss with its weapon, re-arm before the next step. Do not assert what the mod chose to do
differently. Ask the owner when in doubt; do not file it. Keep commands for setting the world up.

Each of these failed a real run:

- **A command that changes nothing fails.** `difficulty normal` on a Normal world, `gamemode creative` in Creative,
  `clear` on an empty inventory and `kill @e[...]` with nothing to kill all fail their step. Make them change
  something (`difficulty hard`, then `difficulty normal`), or wrap them: `execute if entity @e[type=X] run kill
  @e[type=X]`.
- **Melee needs reach**, 3 blocks for a survival player. Summon a boss further away so its entrance and music get room,
  then bring it to the player before the hit: `execute at @s rotated as @s run tp @e[type=X,limit=1,sort=nearest] ^ ^
  ^2`, `@wait 5t` (the client sees the move a few ticks after the server makes it, and `@aim` reads the client), `@aim
  entity X`, `@key key.attack`. The carrier fails an out-of-reach attack and gives the distance.
- **Put the tool in hand.** `/give` fills the first free slot, and after a respawn picked-up loot can be in the
  selected one (measured: a boss took a fist's damage from a "one-shot" sword run). Use `item replace entity @s
  weapon.mainhand with X` right before the step that swings or uses it.
- **Immediate respawn skips the death screen**, and the carrier turns it on. Expect the death message instead:
  `@expect chat death.attack within 60s`. Protect the player before the step after a death (Resistance V,
  `effect give @s minecraft:resistance 120 4 true`), or the same mobs kill it again on respawn.
- **`@e` sees only living entities.** A boss revived as an NPC, or one the mod keeps in a death state, can stand
  in plain view while `execute if entity @e[type=X]` says no. To show it is still there, aim by type id (`@aim entity
  X` reads the client's world) and interact with it (`@key key.use`, then `@expect screen /./`).
- **A raw key has a name too.** Close a screen with `@key key.keyboard.escape`. No key mapping is called
  `key.escape`.
- **Find where the message goes.** `displayClientMessage(msg, true)` is the action bar and `false` is chat.
  `sendSystemMessage` is chat. Read the call before choosing `@expect chat` or `@expect actionbar`.
- **Strike until it falls with a loop, not copies.** A mob that takes many capped hits, or every raider of a wave:
  ```
  @repeat each @e[type=X,distance=..48] max 40 until execute unless entity {it}
  execute at @s rotated as @s run tp {it} ^ ^ ^2
  @wait 5t
  @aim entity {it}
  @key key.attack
  @wait 10t
  @end-repeat
  ```
  `{it}` is one entity's UUID, so each gets its own strikes (no "nearest of a type" left standing), and the block stops
  as soon as it's dead. The bound is required. Set it from the mob's health and the per-hit cap, with room to spare.
  A bound that's reached means the mob outlasted the script, and the step fails naming it.
- **Bosses can shrug off `/kill`.** A mod that keeps its own health value can report "Killed" and leave the entity
  standing. Remove a boss through the mod's own path (its weapon), or move the player out of range.
- **Freeze a mob before the kill step when its own fight is already proven.** A dashing mob leaves reach between
  the `tp` and the hit. `execute as @e[type=X] run data merge entity @s {NoAI:1b}` keeps it there.
- **Wait out death animations.** A boss with a long death (a `getDeathAnimationDuration`, say 160 ticks) is "dying"
  until it ends. It is then invisible to `@e` and refuses interaction. Wait the remaining ticks before the next step.
- **The inventory is a hint, not a promise.** It reads lang keys, and a mod names items there that it registers only
  when a companion mod is present. `Unknown item` at run time means picking another item, not a finding.
- **Mods guard the player.** An inventory-protection item can make `/clear` find nothing, by design. Empty slots
  with `item replace entity @s hotbar.N with minecraft:air` instead, and check the mod's mixins before calling it a
  bug.

Then lint it:

```bash
python3 tools/playtest.py lint SCRIPT --jar MOD.jar [--jar DEP.jar ...] [--forbid NS:ID ...]
```

Fix until it says PASS. A misspelt id, a bad duration and an unknown directive each name their line.

## `--until-pass`

Run (`playtest-run` on one fast version, then the newest), and on a FAIL run `playtest-debug`. Apply its verdict:

- **The script is wrong:** fix the line it names and run again.
- **The mod is wrong:** stop and report it as a finding. The script stays as written, and the user decides.
- **The carrier cannot do it** (a directive it lacks, an input it cannot drive): say so, and propose the carrier
  change. Do not work around it with a command that bypasses the behaviour under test.

Stop after 5 rounds without progress. Report each round's change and why.
