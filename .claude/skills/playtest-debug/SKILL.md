---
name: playtest-debug
description: Explain a failed playtest run -- read the failing step against the script's intent and the step's purpose, its observations (what sounds, subtitles, chat and screens actually appeared, and when), the frames, and the game log, and decide whose failure it is -- the script's (a wrong id, a timing too tight, an aim that missed, an expectation the mod never promised), the carrier's (an input it cannot drive) or the mod's (a crash, a missing sound, a boss that never dies when it should) -- with the one change that fixes it or the finding to report. Use when a playtest failed and the user asks why, or when playtest-write or playtest-plan loops need a verdict.
---

# Debug a failed playtest

**Which fork:** the same for every fork. It reads a run's report folder (playtest-run) and the script. It changes
nothing outside the script, unless the user asks.

## 1. Read the failure, compactly

```bash
python3 tools/playtest.py explain REPORT_DIR [--mc 1.21.1]
```

This prints, as JSON:

- the script's intent and the failed version;
- each step's verdict;
- the failed step: its lines, its `observed{}` (every sound, subtitle, chat line, title, boss bar and screen seen,
  with the time), its errors and frames;
- the run's events;
- the tails of `latest.log` and `stderr.log`;
- the frame list.

Look at the failed step's frames: the `-fail` frame shows what the player saw.

## 2. Judge it against the intent

Ask, in this order, and stop at the first yes:

1. **Did the game itself break?** A crash report, an exception from the mod's package in the log, a `FATAL` line, a
   step that failed because the game exited. Then this is **the mod's** failure, whatever the script did. Quote the
   exception's first frame in the mod and the step that led to it.
2. **Did the script ask for something the mod does not register or promise?**
   - an id missing from `playtest.py inventory`;
   - a subtitle key the lang file lacks;
   - a sound expected by its subtitle text rather than its event id.

   **Script.**
3. **Did it happen, but outside the window?** `observed` shows the sound, chat line or screen arriving after the
   `within`, or before the step started. **Script:** widen the window, or move the expectation to the step that
   caused it.
4. **Did the mod do it on purpose?** A command refused or a target unharmed can be the mod guarding something: an
   inventory-protection mixin blocking `/clear`, a boss that refuses damage until its spawn animation ends, a block
   stance. Find it in the mod's source (`@Inject` on the vanilla method, a `hurt` override) before calling it either
   the script's or the mod's. The usual verdict is **script**: work with the design.
5. **Did the input miss?** The frame shows the target off-centre, too far or behind a block, or `observed` has no hit.
   An `observed.aim` entry of `direct gameMode.attack` means the crosshair missed and the carrier hit the target
   directly. An out-of-reach failure names the distance. **Script:** bring the target within reach
   (`execute at @s rotated as @s run tp @e[...] ^ ^ ^2`), `@aim` again right before the `@key`, clear the line of
   sight.
6. **Was the world not as the script assumed?** Wrong game mode or difficulty, leftover mobs, night-time spawns, the
   player already dead. **Script:** set it up explicitly in the step.
7. **Can the carrier not do what the step needs?** A directive refused as unknown, an input it cannot press, a screen
   it cannot click. **Carrier:** name the change in `templates/smoke-harness`.
8. **Otherwise** the mod did not do what the intent says it should: the music never played, the boss did not die to
   the weapon that should one-shot it, the narration never appeared. **Mod.** Before saying so, check the mod's
   source for the behaviour (playtest-plan's grounding step) and say where it should have happened.

## 3. Say it

Give one verdict: **script**, **carrier** or **mod**. Then:

- the evidence: the observed lines, a log line, a frame;
- for script, the exact line change;
- for carrier, the change and where it goes;
- for mod, the finding written for the fork's known-issues ledger: the symptom, the version, reproduction by the
  script, and its severity by the ledger's rules.

The same failure on every version points to the script or the mod's shared code. A failure on one version points to
that version's port: say which.
