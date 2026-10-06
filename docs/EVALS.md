# Evals

Measurements for issue #27 (deterministic recipes). One section per stage. Every row here is
**anonymised**: ports are named only by their profile (P1–P11, defined in #27). The per-port tables,
start snapshots and finished trees stay in the maintainer's private workspace and never enter this repo.

## Stage 1 — recipe bench baseline, recipes OFF (2026-10-06)

`tools/recipe-bench.py` puts a port's **start** (its deterministic decompile, or for an era jump the
finished 1.21.1 port with the 26.2 rename tables and overlays removed) into its own build, compiles it
against the target, and sorts every unique javac error into the catalogue entry whose **Error:** text it
fits. No model was run. 24 of 29 rows produced a count; `-era` rows are the 1.21.1 → 26.2 jump of ports
that also have a 1.20 → 1.21 row.

| Profile | Rows measured | Raw errors per port | Total | Matched | Largest catalogue buckets |
|---|---|---|---|---|---|
| P1 | 1 of 2 | 45 | 45 | 35% | 70 (4), M1 (4), M27 (2) |
| P2 | 1 of 2 | 233 | 233 | 57% | V6 (39), 113 (26), 109 (21) |
| P2-era | 1 of 1 | 4,289 | 4,289 | 28% | V4 (741), 93 (185), V13 (75) |
| P3 | 1 of 1 | 9,948 | 9,948 | 35% | 54 (679), 111 (584), 138 (455) |
| P4 | 1 of 1 | 640 | 640 | 61% | 13 (80), 7 (63), 49 (37) |
| P4-era | 1 of 1 | 380 | 380 | 37% | V4 (79), V60 (18), V6 (13) |
| P5 | 4 of 4 | 418 – 1,412 | 3,671 | 46% | 9 (316), 111 (153), 138 (93) |
| P5-era | 1 of 1 | 3,117 | 3,117 | 20% | V4 (272), V6 (64), 93 (59) |
| P6 | 2 of 2 | 267 – 957 | 1,224 | 40% | 150 (173), 154 (80), 167 (48) |
| P7 | 0 of 2 | — | — | — | — |
| P8 | 2 of 2 | 368 – 2,900 | 3,268 | 37% | 9 (363), 13 (109), 6 (66) |
| P8-era | 1 of 2 | 3,346 | 3,346 | 34% | V4 (543), V6 (165), V60 (95) |
| P9 | 5 of 5 | 45 – 192 | 620 | 53% | V4 (151), V6 (78), V50 (41) |
| P10 | 3 of 3 | 698 – 3,568 | 6,245 | 32% | V4 (1035), V6 (423), V60 (149) |

**How to read it.** *Raw errors* is what the compiler reports with javac's 100-error cap lifted, counted by
`tools/burndown-count.sh` as unique `file:line` locations. *Matched* is the share that already maps to a
catalogue entry by its error text alone; the rest is Stage 2's input (patterns with no entry yet, or
entries whose **Error:** text is too generic to attribute anything).

**Known limits of this first measurement**
- Matching is by error text only, not yet scoped by version: an entry written for one version jump can
  claim an error from another (a few of the 1.20 → 1.21 rows above list a `V` entry). Stage 3's recipe
  format adds a `scope`.
- One start per port. Two ports have no raw start (their first commit is already the finished port) and
  will need one rebuilt from the original jar. Both P7 ports could not build here: a dependency's only
  maven host is not on this cloud environment's network allowlist. One P8 era row ran out of memory on a
  15 GB machine after 7,350+ errors; that is a lower bound, not a count, so it is not in the table.
- One P8 row starts from a native NeoForge 1.21.1 build, so its errors are decompiler repair rather than
  porting work.

**Counting problems the bench found in its own tools, all fixed with self-tests in
`tools/test-port-tools.sh`.** `burndown-count.sh` printed a number in two cases where none was real:
a compile task that failed before javac ran (read as 0 errors) and a build that ran out of memory after
printing some errors (read as a total). The memory failures themselves were in the Gradle process, which
holds every diagnostic a forked javac sends back, so large starts are run with `--in-process`.

## Stage 2 — pattern census: what the ports actually changed (2026-10-06)

`tools/hunk-census.py` diffs each port's start against its finished tree (`-U0` hunks, Java only) and
attributes every hunk to a catalogue entry, or marks it unattributed. No build and no model; all 29 rows
(P7 included, which could not build in Stage 1) took about three minutes.

An entry is detected from the code-shaped identifiers in its **Pattern:**/**Error:** (old side) and
**Fix:** (new side) fragments, plus prose renames written `A` → `B`. A hunk matches when it removes
an old-side identifier that is gone from the finished tree, or adds a specific new-side one. §W and §X
entries cite renames only as examples and are not used as detectors. §V entries are used only for era
rows: the catalogue has no machine-readable scope yet, and without this rule a §V entry claimed
1.20 → 1.21 hunks. *Code hunks* excludes comment-only hunks and hunks over 300 lines (regenerated models,
recovered methods).

| Profile | Rows | Code hunks | Attributed | New files | Deleted files |
|---|---|---|---|---|---|
| P1 | 2 | 64 | 62% | 1 | 0 |
| P2 | 2 | 1,072 | 61% | 2 | 0 |
| P2-era | 1 | 2,349 | 61% | 0 | 0 |
| P3 | 1 | 6,673 | 53% | 0 | 0 |
| P4 | 1 | 173 | 56% | 4 | 143 |
| P4-era | 1 | 272 | 68% | 1 | 0 |
| P5 | 4 | 2,785 | 55% | 27 | 106 |
| P5-era | 1 | 1,558 | 53% | 5 | 6 |
| P6 | 2 | 934 | 49% | 17 | 74 |
| P7 | 2 | 902 | 43% | 3 | 10 |
| P8 | 2 | 4,628 | 33% | 30 | 37 |
| P8-era | 2 | 6,818 | 56% | 9 | 17 |
| P9 | 5 | 533 | 66% | 3 | 6 |
| P10 | 3 | 4,343 | 51% | 26 | 18 |
| **all** | 29 | 33,104 | 51% | 128 | 417 |

**Most frequent entries** (hunks, and in how many of the 29 rows):

| Entry | Hunks | Ports | Profiles |
|---|---|---|---|
| V4 | 3,089 | 13 | P2-era, P4-era, P5-era, P8-era, P9, P10 |
| V6 | 940 | 9 | P2-era, P4-era, P5-era, P8-era, P9, P10 |
| 25 | 890 | 11 | P3, P4, P5, P6, P7, P8 |
| 1 | 803 | 7 | P3, P4, P5, P8 |
| 32 | 686 | 20 | P2, P2-era, P3, P4-era, P5, P5-era, P6, P7, P8, P8-era, P10 |
| 13 | 608 | 14 | P3, P4, P5, P5-era, P6, P7, P8, P8-era, P9, P10 |
| 103 | 406 | 15 | P3, P4, P5, P6, P8, P8-era, P9, P10 |
| 9 | 391 | 6 | P4, P5, P6, P7, P8, P10 |
| 136 | 356 | 9 | P2-era, P4-era, P5-era, P8-era, P9, P10 |
| V12 | 354 | 10 | P4-era, P5-era, P8-era, P9, P10 |
| 132 | 303 | 4 | P3, P5, P6 |
| V13 | 301 | 9 | P2-era, P4-era, P5-era, P8-era, P9, P10 |
| 130 | 284 | 15 | P4, P4-era, P5, P5-era, P6, P7, P8, P8-era, P10 |
| 175 | 269 | 15 | P1, P4, P4-era, P5, P5-era, P6, P8, P8-era, P10 |
| V31 | 266 | 9 | P4-era, P5-era, P8-era, P9, P10 |
| V94 | 261 | 10 | P2-era, P4-era, P5-era, P8-era, P9, P10 |
| V21 | 255 | 9 | P2-era, P4-era, P5-era, P8-era, P9, P10 |
| 12d | 238 | 11 | P3, P4, P5, P6, P7, P8, P8-era |
| 16 | 223 | 9 | P3, P4, P5, P6, P7, P8 |
| 104 | 208 | 16 | P2, P2-era, P3, P4-era, P5, P5-era, P6, P8, P8-era, P9, P10 |
| 37 | 203 | 3 | P3, P4, P5 |
| V86 | 187 | 9 | P2-era, P4-era, P5-era, P8-era, P9, P10 |
| 111 | 175 | 6 | P3, P4-era, P5, P5-era, P10 |
| V38 | 175 | 8 | P2-era, P5-era, P8-era, P9, P10 |
| 79 | 168 | 13 | P2-era, P3, P5, P5-era, P7, P8, P8-era, P9, P10 |

**Unattributed clusters seen in five or more rows** are the backlog for new entries or wider detectors.
`A -> B`: identifiers removed → added. `reshape:` means the hunk keeps its identifiers and changes shape
(a cast, a call form, a package segment); shown as context, then `[old] => [new]` tokens.

| Hunks | Ports | Unattributed cluster |
|---|---|---|
| 38 | 10 | `reshape: mc . [] => [gui .]` |
| 28 | 10 | `reshape:  [] => [@ Override]` |
| 23 | 10 | `EventBusSubscriber -> -` |
| 12 | 9 | `- -> SuppressWarnings` |
| 794 | 8 | `reshape: . isClientSide [] => [( )]` |
| 94 | 8 | `- -> getRandom` |
| 61 | 8 | `- -> EntitySpawnReason,TRIGGERED` |
| 15 | 8 | `reshape:  [] => [return true ;]` |
| 11 | 8 | `GameRules -> -` |
| 164 | 7 | `CLIENT,OnlyIn -> -` |
| 55 | 7 | `- -> Holder` |
| 36 | 7 | `reshape: LivingEntity ) [] => [( Object )]` |
| 15 | 7 | `reshape: . x [] => [( )]` |
| 10 | 7 | `Minecraft -> -` |
| 194 | 6 | `CompoundTag -> ValueOutput` |
| 90 | 6 | `reshape: projectile . [] => [arrow .]` |
| 73 | 6 | `CompoundTag -> -` |
| 35 | 6 | `reshape: minecraft . [] => [util .]` |
| 35 | 6 | `reshape: npc . [] => [villager .]` |
| 32 | 6 | `getPosition -> -` |
| 22 | 6 | `- -> randomUUID` |
| 10 | 6 | `Entity -> -` |
| 157 | 5 | `getDouble -> getDoubleOr` |
| 109 | 5 | `MobEffect -> -` |
| 92 | 5 | `- -> hurtLevel,hurtServer` |
| 85 | 5 | `reshape: import [software . bernie] => [com]` |
| 35 | 5 | `reshape: animal . [] => [golem .]` |
| 22 | 5 | `reshape: projectile . [] => [throwableitemprojectile .]` |
| 19 | 5 | `reshape:  [}] => []` |
| 16 | 5 | `- -> SectionPos,blockToSectionCoord` |
| 14 | 5 | `- -> RandomSource` |
| 12 | 5 | `reshape: monster . [] => [zombie .]` |
| 11 | 5 | `RenderSystem,blaze3d -> -` |
| 11 | 5 | `reshape: renderer . [] => [rendertype .]` |
| 10 | 5 | `CONFUSION -> NAUSEA` |
| 7 | 5 | `reshape: . monster [] => [. zombie]` |
| 6 | 5 | `LivingEntity -> -` |
| 5 | 5 | `reshape:  [import java . util . List ;] => []` |
| 5 | 5 | `GameRules -> DifficultySettings` |
| 5 | 5 | `TextureSheetParticle -> SingleQuadParticle` |
| 5 | 5 | `getMainCamera -> mainCamera` |
| 5 | 5 | `Builder -> -` |

**What this says for Stage 3/4.** Several of the largest unattributed clusters are entries that already
exist but are written in prose the detector cannot use: a field read becoming a call (V17's
`isClientSide` → `isClientSide()`, 794 hunks in 8 rows), the 26.x sub-package moves (V3's map),
GeckoLib's root-package move (V18), and the NBT `getX` → `getXOr` family (V12). Each is one recipe
away from attributed and applied. Attribution precision has only been spot-checked so far; Stage 3's
fixtures will measure it.
