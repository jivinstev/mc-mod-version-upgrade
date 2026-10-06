# Evals

Measurements for issue #27 (deterministic recipes). One section per stage. Every row here is
**anonymised**: ports are named only by their profile (P1–P11, defined in #27). The per-port tables,
start snapshots and finished trees stay in the maintainer's private workspace and never enter this repo.

## Stage 0 — where the baseline ports' tokens went (2026-10-06)

`tools/context-profile.py` run on the two cloud replay baselines' own transcripts (each session ran it on
itself; P11's desktop transcript is still to come). "Carried" = a tool result's size times the requests
that re-read it. Shares are of all cache reads.

| | P1 replay | P4 replay |
|---|---|---|
| Requests | 214 | 541 |
| Context per request: first / median / max | 46k / 182k / 324k | 49k / 452k / 741k |
| Floor (first request's context, re-read by every request) | 26% | 11% |
| Tool results, carried | 30% | 36% |
| — catalogue reads | 3% | 2% |
| — skill and reference docs | 15% | 5% |
| — build output | 4% | 3% |
| — the port's source | 1% | 2% |
| — other (shell output, re-reading spilled tool output) | 7% | 24% |
| Everything else (the conversation itself: the model's own calls and replies) | 44% | 53% |
| What-if: every tool result capped at 2,000 tokens | −4% | −8% |
| What-if: 50% fewer requests | −50% | −50% |

**What it says.** Cost is driven by how many requests a port makes and how large its context has grown by
then, not by any one thing it reads. The catalogue costs 2–3%, because the agent reads it in slices; the
docs it reads at the start cost more (15% in the smaller port). In the larger port a quarter of all cache
reads came from re-reading tool output that had been too large to show and was spilled to a file. So the
levers, in order: fewer requests (recipes, and per-file fixes in small fresh contexts: H1/H5), keeping each
context short (fresh sessions per phase; summaries instead of raw logs), then a smaller starting context.

**Open question.** Both transcripts hold about 2.2× the cache reads of their sessions' own recorded usage
(38M vs 18M; 232M vs 102M) with requests de-duplicated by id, so the profiler's dollar figures ($17.87,
$74.01) are above the recorded ones ($6.88, $30.31). The shares above do not depend on which total is
right. One candidate is subagent requests being counted on one side only; not yet checked.

## Stage 1 — recipe bench baseline, recipes OFF (2026-10-06)

`tools/recipe-bench.py` puts a port's **start** (its deterministic decompile, or for an era jump the
finished 1.21.1 port with the 26.2 rename tables and overlays removed) into its own build, compiles it
against the target, and sorts every unique javac error into the catalogue entry whose **Error:** text it
fits. No model was run. 22 of 29 rows produced a count (re-measured after a correction, below); `-era` rows are the 1.21.1 → 26.2 jump of ports
that also have a 1.20 → 1.21 row.

| Profile | Rows measured | Raw errors per port | Total | Matched | Largest catalogue buckets |
|---|---|---|---|---|---|
| P1 | 1 of 2 | 45 | 45 | 35% | 70 (4), M1 (4), M27 (2) |
| P2 | 1 of 2 | 233 | 233 | 57% | V6 (39), 113 (26), 109 (21) |
| P2-era | 1 of 1 | 5,322 | 5,322 | 24% | V4 (782), 93 (243), V13 (75) |
| P3 | 1 of 1 | 9,948 | 9,948 | 35% | 54 (679), 111 (584), 138 (455) |
| P4 | 1 of 1 | 698 | 698 | 61% | 13 (80), 7 (63), 49 (40) |
| P4-era | 1 of 1 | 192 | 192 | 31% | V4 (39), V13 (5), V39 (5) |
| P5 | 4 of 4 | 418 – 1,485 | 3,744 | 46% | 9 (319), 111 (158), 138 (94) |
| P5-era | 1 of 1 | 2,733 | 2,733 | 13% | V4 (158), V18 (54), 93 (49) |
| P6 | 2 of 2 | 267 – 957 | 1,224 | 40% | 150 (173), 154 (80), 167 (48) |
| P7 | 0 of 2 | — | — | — | — |
| P8 | 2 of 2 | 282 – 2,877 | 3,159 | 38% | 9 (363), 13 (109), 6 (66) |
| P8-era | 0 of 2 | — | — | — | — |
| P9 | 5 of 5 | 45 – 243 | 676 | 43% | V4 (148), V6 (49), V50 (41) |
| P10 | 2 of 3 | 1,255 – 2,312 | 3,567 | 28% | V4 (527), V6 (158), 93 (78) |

**How to read it.** *Raw errors* is what the compiler reports with javac's 100-error cap lifted, counted by
`tools/burndown-count.sh` as unique `file:line` locations. *Matched* is the share that already maps to a
catalogue entry by its error text alone; the rest is Stage 2's input (patterns with no entry yet, or
entries whose **Error:** text is too generic to attribute anything).

**Correction (same day).** The first version of this table compiled the starts of the five multi-version
ports through their own finished 1.21.1 overlay and rename table, which leaked part of the answer into the
start. Those rows were re-run with them removed, and the 26.x rows now start from the prepared 1.21.1 tree;
the 26.x rows also now have every library jar they need, which uncovers errors a missing library had
hidden. Two large 26.x rows ran out of memory on this 15 GB machine in the re-run and are left out.

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
| P2 | 2 | 1,403 | 50% | 2 | 0 |
| P2-era | 1 | 1,997 | 65% | 10 | 0 |
| P3 | 1 | 6,673 | 53% | 0 | 0 |
| P4 | 1 | 177 | 55% | 4 | 143 |
| P4-era | 1 | 98 | 65% | 30 | 0 |
| P5 | 4 | 2,819 | 54% | 27 | 106 |
| P5-era | 1 | 908 | 48% | 90 | 0 |
| P6 | 2 | 934 | 49% | 17 | 74 |
| P7 | 2 | 902 | 43% | 3 | 10 |
| P8 | 2 | 5,222 | 29% | 30 | 37 |
| P8-era | 2 | 5,358 | 60% | 156 | 17 |
| P9 | 5 | 449 | 66% | 24 | 6 |
| P10 | 3 | 3,281 | 49% | 198 | 18 |
| **all** | 29 | 30,285 | 50% | 592 | 411 |

**Most frequent entries** (hunks, and in how many of the 29 rows):

| Entry | Hunks | Ports | Profiles |
|---|---|---|---|
| V4 | 2,840 | 13 | P2-era, P4-era, P5-era, P8-era, P9, P10 |
| 25 | 890 | 11 | P3, P4, P5, P6, P7, P8 |
| 1 | 803 | 7 | P3, P4, P5, P8 |
| V6 | 731 | 7 | P2-era, P8-era, P9, P10 |
| 32 | 609 | 19 | P2, P2-era, P3, P5, P5-era, P6, P7, P8, P8-era, P10 |
| 13 | 601 | 12 | P3, P4, P5, P6, P7, P8, P8-era, P9 |
| 9 | 390 | 5 | P4, P5, P6, P7, P8 |
| 103 | 385 | 13 | P3, P4, P5, P6, P8, P8-era, P9, P10 |
| 136 | 356 | 9 | P2-era, P4-era, P5-era, P8-era, P9, P10 |
| V12 | 323 | 10 | P4-era, P5-era, P8-era, P9, P10 |
| 132 | 303 | 4 | P3, P5, P6 |
| V13 | 301 | 9 | P2-era, P4-era, P5-era, P8-era, P9, P10 |
| 130 | 278 | 14 | P4, P4-era, P5, P5-era, P6, P7, P8, P8-era, P10 |
| V21 | 254 | 9 | P2-era, P4-era, P5-era, P8-era, P9, P10 |
| 175 | 240 | 12 | P1, P4, P5, P5-era, P6, P8, P8-era, P10 |
| 12d | 239 | 11 | P3, P4, P5, P6, P7, P8 |
| 16 | 223 | 9 | P3, P4, P5, P6, P7, P8 |
| 37 | 203 | 3 | P3, P4, P5 |
| V31 | 201 | 7 | P5-era, P8-era, P9, P10 |
| V94 | 189 | 6 | P8-era, P9, P10 |
| 104 | 185 | 11 | P2, P3, P5, P6, P8, P8-era, P9 |
| 120 | 161 | 4 | P2, P3, P6, P8 |
| V38 | 155 | 7 | P2-era, P5-era, P8-era, P9, P10 |
| 111 | 146 | 3 | P3, P5, P10 |
| 98 | 142 | 3 | P3, P5, P8 |

**Unattributed clusters seen in five or more rows** are the backlog for new entries or wider detectors.
`A -> B`: identifiers removed → added. `reshape:` means the hunk keeps its identifiers and changes shape
(a cast, a call form, a package segment); shown as context, then `[old] => [new]` tokens.

| Hunks | Ports | Unattributed cluster |
|---|---|---|
| 37 | 10 | `reshape: mc . [] => [gui .]` |
| 23 | 10 | `EventBusSubscriber -> -` |
| 94 | 9 | `- -> getRandom` |
| 802 | 8 | `reshape: . isClientSide [] => [( )]` |
| 15 | 8 | `reshape:  [] => [return true ;]` |
| 60 | 7 | `- -> EntitySpawnReason,TRIGGERED` |
| 55 | 7 | `- -> Holder` |
| 36 | 7 | `reshape: LivingEntity ) [] => [( Object )]` |
| 21 | 7 | `reshape:  [] => [@ Override]` |
| 258 | 6 | `CLIENT,OnlyIn -> -` |
| 192 | 6 | `CompoundTag -> ValueOutput` |
| 89 | 6 | `reshape: projectile . [] => [arrow .]` |
| 35 | 6 | `reshape: minecraft . [] => [util .]` |
| 35 | 6 | `reshape: npc . [] => [villager .]` |
| 14 | 6 | `reshape: . x [] => [( )]` |
| 157 | 5 | `getDouble -> getDoubleOr` |
| 109 | 5 | `MobEffect -> -` |
| 92 | 5 | `- -> hurtLevel,hurtServer` |
| 83 | 5 | `reshape: import [software . bernie] => [com]` |
| 35 | 5 | `reshape: animal . [] => [golem .]` |
| 21 | 5 | `reshape: projectile . [] => [throwableitemprojectile .]` |
| 21 | 5 | `- -> randomUUID` |
| 19 | 5 | `reshape:  [}] => []` |
| 16 | 5 | `- -> SectionPos,blockToSectionCoord` |
| 12 | 5 | `reshape: monster . [] => [zombie .]` |
| 10 | 5 | `reshape: renderer . [] => [rendertype .]` |
| 10 | 5 | `CONFUSION -> NAUSEA` |
| 7 | 5 | `Minecraft -> -` |
| 7 | 5 | `reshape: . monster [] => [. zombie]` |

**What this says for Stage 3/4.** Several of the largest unattributed clusters are entries that already
exist but are written in prose the detector cannot use: a field read becoming a call (V17's
`isClientSide` → `isClientSide()`, 802 hunks in 8 rows), the 26.x sub-package moves (V3's map),
GeckoLib's root-package move (V18), and the NBT `getX` → `getXOr` family (V12). Each is one recipe
away from attributed and applied. Attribution precision has only been spot-checked so far; Stage 3's
fixtures will measure it.

## Stage 2.5 — can the judgement work be made smaller? (2026-10-06)

Hypotheses from the #27 plan update, checked with no model over the Stage 1 and Stage 2 data
(`tools/census-hypotheses.py`, profile-level output only).

| Profile | Rows | Code hunks | H1 repeats (exact / loose) | H2 via a port-added helper | H1 among unattributed | H5 in a file with a start error | H7 in optional-integration code |
|---|---|---|---|---|---|---|---|
| P1 | 2 | 64 | 45% / 56% | 0% | 33% | 75% (1 rows) | 0% |
| P2 | 2 | 1,403 | 85% / 86% | 0% | 87% | 97% (1 rows) | 0% |
| P2-era | 1 | 1,997 | 93% / 97% | 0% | 93% | 100% (1 rows) | 0% |
| P3 | 1 | 6,673 | 93% / 94% | 0% | 94% | 97% (1 rows) | 0% |
| P4 | 1 | 177 | 10% / 19% | 3% | 8% | 94% (1 rows) | 0% |
| P4-era | 1 | 98 | 22% / 60% | 0% | 15% | 100% (1 rows) | 0% |
| P5 | 4 | 2,819 | 46% / 59% | 4% | 39% | 89% (4 rows) | 1% |
| P5-era | 1 | 908 | 64% / 86% | 0% | 66% | 100% (1 rows) | 0% |
| P6 | 2 | 934 | 34% / 43% | 7% | 34% | 90% (2 rows) | 0% |
| P7 | 2 | 902 | 51% / 67% | 0% | 51% | — | 0% |
| P8 | 2 | 5,222 | 44% / 62% | 3% | 44% | 61% (2 rows) | 3% |
| P8-era | 2 | 5,358 | 64% / 86% | 8% | 60% | — | 3% |
| P9 | 5 | 449 | 23% / 51% | 0% | 20% | 87% (5 rows) | 0% |
| P10 | 3 | 3,281 | 54% / 82% | 24% | 56% | 98% (2 rows) | 1% |
| **all** | 29 | 30,285 | 65% / 78% | 5% | 62% | 87% (22 rows) | 1% |

- **H1 — fix one site, propagate it.** 65% of code hunks repeat an earlier hunk's exact normalised shape in
  the same row (78% if only the removed/added identifiers must match), and 62% of the hunks no catalogue
  entry covers do. Generated-code mods are 85–94%; small libraries (P4, P9) 10–23%. **Strongly supported:**
  a model that fixes one site and has the rest applied by a generated rewrite would write a fraction of the
  edits.
- **H5 — fixes land in the file that fails to compile.** 87% of code hunks are in a file with at least one
  start compile error, and the ports changed 3,049 of the 3,456 files that had one. **Strongly supported:**
  a per-file loop (one file, its errors, the compiler) sees most of the work. The exception is a port that
  starts from a native build (P8's decompile-repair row), where most changes are not compile-driven.
- **H2 — a shared compat library.** 5% of code hunks call a helper class the port added (20% in P10, where
  the §W compat pairs live), and only 2 helper names recur across rows. **Weak as stated:** ports do not
  converge on the same helpers by themselves, so a shared library would have to define them.
- **H7 — defer optional integrations.** 1% of code hunks, 1 of 592 new files. **Weak for cost**, still
  useful for scope (see the scope menu below).
- **H8 — choose the decompiler per class.** Vineflower beat CFR on both jar-start rows (45 vs 73 and 337
  vs 1,291 start errors). Taking CFR only for the 17 files where it did better cut a 1.21.1 decompile from
  337 to 312 errors (−7%; the per-file estimate had said −13%, because errors cross files); on the small row
  it gained nothing. **Weak:** keep Vineflower, with CFR as a per-class fallback.
**Decision:** H2, H7 and H8 are dropped from the plan as separate workstreams. The 26.x compat pairs
(H2's only signal) are written as part of the era recipes anyway; optional integrations (H7) are already
offered as chunks by the scope menu; CFR stays only as the fallback for methods Vineflower cannot
decompile (catalogue §A).
- H3 (choice-point recipes) and H4 (scaffolds) need a model or a skeleton design and are scheduled for
  Stages 3–6.

**Scope-menu accuracy** (`tools/scope-menu.py`, migrate-mod Step 3b). Across 39 optional chunks in 10
finished ports, a chunk's predicted share of the port was compared with its share of what the port actually
changed. Share of start errors alone: correlation 0.77. Share of lines alone: 0.88. **The mean of the two:
0.91, mean absolute error 1.9 points**, which is what the menu now reports. Mixin chunks ran about 1.5×
their estimate, because much of their work never shows as a compile error; the menu says so.
