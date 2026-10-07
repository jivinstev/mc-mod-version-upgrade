# Evals

Measurements for issue #27 (deterministic recipes). One section per stage. Every row here is
**anonymised**: ports are named only by their profile (P1–P11, defined in #27). The per-port tables,
start snapshots and finished trees stay in the maintainer's private workspace and never enter this repo.

## The plan these measurements produced (2026-10-06)

Savings are a share of a port's cost, estimated from the measurements below; ranges overlap and do not
add. Each later step acts on what the earlier ones leave.

| Order | Step | What it does | Evidence | Expected saving |
|---|---|---|---|---|
| 1 | 5a: fresh session per phase (**shipped**: `port-handoff.py`) | resume from `MIGRATION.md` at each phase boundary, so context restarts near its 37–49k floor instead of growing to a median 180–450k | Stage 0: the conversation itself is 44–60% of cache reads | 20–40% |
| 2 | 5a: no spilled-output re-reads; summaries not raw logs (**shipped**: `compile-summary.py`) | grep spilled tool output instead of reading it; bucketed error summaries | Stage 0: 7–24% of cache reads | 5–20% |
| 3 | 3: recipe format + engine | `auto` / `choice` / `scaffold` / `manual`, type-aware, with the §X guards | enabling step | — |
| 4 | 4: recipes in bulk | apply the mechanical changes with no model, most frequent first (V4, V3 moves, V17, V12, 25, 32, 13...) | Stage 2: 35–50% of 1.20→1.21 hunks and 45–60% of 26.x hunks are mechanical; Stage 3 prep: the 50 most common cross-port edits cover 21% of to-1.21.x hunks and 55% of 26.x hunks | 35–70% |
| 5 | 5b: per-file residual loop (H5) | each remaining fix in a small fresh context with that file, its errors and the compiler; cheaper model | Stage 2.5: 87% of hunks are in a file that fails the first compile | 50–80% of what is left |
| 6 | 5b: fix once, apply everywhere (H1) | a hand fix becomes a rewrite applied to every matching site | Stage 2.5: 65% of hunks repeat an earlier one in the same port | 30–50% of what is left |
| 7 | 3/6: choice points (H3) and scaffolds (H4) | the model picks an option or fills a skeleton instead of writing code | H3 measured 2026-10-07: for 38 of 84 frequent entries, 3 options cover 80%+ of real fixes | 5–15% of the judgement work |
| 8 | scope menu (shipped) | the user leaves optional chunks out | Stage 2.5: chunk estimates within ~2 points | 0–40%, the user's choice |
| — | 6: replays + model tiering | measures all of the above, recipes on vs off, model mix | — | measured, not assumed |

Dropped for low value: a catalogue index (the catalogue is 2–3% of cache reads), a smaller starting context
(1–2%), general tool-result caps (4–8%, replaced by row 2), H2, H7, H8.

## Stage 0 — where the baseline ports' tokens went (2026-10-06)

`tools/context-profile.py` on the three published baselines' transcripts: the two cloud replays (each
session ran it on itself) and the desktop P11 port. "Carried" = a tool result's size times the requests
that re-read it. Shares are of all cache reads.

| | P1 replay | P4 replay | P11 (desktop) |
|---|---|---|---|
| Requests | 214 | 541 | 306 |
| Context per request: first / median / max | 46k / 182k / 324k | 49k / 452k / 741k | 37k / 397k / 596k |
| Floor (first request's context, re-read by every request) | 26% | 11% | 10% |
| Tool results, carried | 30% | 36% | 30% |
| — catalogue reads | 3% | 2% | 3% |
| — skill and reference docs | 15% | 5% | 2% |
| — build output | 4% | 3% | 3% |
| — the port's source | 1% | 2% | 2% |
| — other (shell output, re-reading spilled tool output) | 7% | 24% | 20% |
| Everything else (the conversation itself: the model's own calls and replies) | 44% | 53% | 60% |
| What-if: every tool result capped at 2,000 tokens | −4% | −8% | −5% |
| What-if: 50% fewer requests | −50% | −50% | −50% |

**What it says.** Cost is driven by how many requests a port makes and how large its context has grown by
then, not by any one thing it reads. The catalogue costs 2–3% in all three, because the agent reads it in
slices. The largest single tool results in the two bigger ports were re-reads of tool output that had been
too large to show and was spilled to a file. So the levers, in order: fewer requests (recipes, and
per-file fixes in small fresh contexts: H1/H5), keeping each context short (fresh sessions per phase;
summaries instead of raw logs; no spilled-output re-reads), then a smaller starting context.

**Which totals to trust.** The two cloud replays were archived and later resumed so they could profile
themselves, and their transcripts then held about 2.2× the cache reads of the sessions' own recorded usage
(38M vs 18M; 232M vs 102M), most likely history duplicated on resume. On a session that was never resumed
the transcript and the record agree (checked: 162.6M vs 160.4M). So for the replays the recorded totals
($6.88, $30.31) stand; the shares above hold either way. The profiler's dollars also run ~7% above a
session's recorded cost on the same tokens (list-price estimate vs billed).

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

## Steps 1–2 shipped, and the shape data for Stage 3 (2026-10-07)

**Step 1, fresh context per phase.** `tools/port-handoff.py <mod> --done <phase>` rewrites one
`## Hand-off` section of the port's `MIGRATION.md` from the workspace (compile state, local history,
uncommitted files, the user's Scope choice, waiting catalogue additions) and prints a resume prompt for
a fresh subagent or session. The migrate-mod skill now runs setup, compile, gates and delivery each in a
fresh context, and the compile loop again every ~10 passes on a big port. Its saving is measured in
Stage 6, not assumed.

**Step 2, summaries instead of raw logs.** `tools/compile-summary.py <log>` prints the validated count,
the errors grouped by catalogue entry and by family, the worst files, and what grew since the previous
pass, in a fixed size. Measured on three real first-compile logs from the bench:

| Profile | Start errors | Raw log | `grep error:` dump | Summary |
|---|---|---|---|---|
| P1 | 45 | 46 KB | 20 KB | 1.9 KB |
| P2 | 233 | 248 KB | 122 KB | 2.6 KB |
| P4 | 698 | 702 KB | 392 KB | 2.5 KB |

So a pass's error view is ~10× smaller on a tiny port and ~150× on a mid-size one, and it stays flat as
the port grows. The summary also names the catalogue entries to read, so the model reads those entries
instead of searching.

**H1x: do edits recur ACROSS ports?** H1 (Stage 2.5) only showed a port repeating itself. A recipe pays
off if the same edit shows up in other mods. Measured on the census: the share of code hunks whose loose
shape (entry or cluster plus removed and added identifiers) appears in 2+ different mods on the SAME
axis. A mod's 1.21.1 and 26.2 rows count once. Era-jump rows (P9, P10 and the `-era` rows) only match
each other, because a recipe pack is written per axis.

| Rows | Code hunks | In 2+ mods | In 3+ mods | Unattributed hunks in 2+ mods |
|---|---|---|---|---|
| all 29 | 30,285 | 39% | 33% | 18% |
| to 1.21.x (P1–P8), range by profile | 18,194 | 11–37% | 5–26% | 3–50% |
| era jump to 26.2 (P9, P10, `-era`), range by profile | 12,091 | 50–76% | 43–73% | 19–63% |

The yield curve, cross-port shapes ranked by how many hunks they cover:

| Recipes (top N shapes) | 10 | 25 | 50 | 100 | all cross-port |
|---|---|---|---|---|---|
| to-1.21.x hunks covered (190 shapes) | 12% | 18% | 21% | 23% | 24% |
| era-jump hunks covered (185 shapes) | 37% | 47% | 55% | 60% | 63% |

What this changes for Stage 4:

- **26.x is where recipes pay first.** 50 recipes cover over half of an era jump's hunks. The
  multi-version template's inherited rename table already holds many of them, so the first recipe
  is that table itself, applied to non-template ports.
- **To-1.21.x ports need recipes AND the per-file loop.** Cross-port recipes reach about a fifth of
  the hunks, and the curve flattens after ~50. Most of the rest repeats within a port (H1, 65%), so
  fix-once-apply-everywhere (step 6) matters more there than a bigger recipe library.
- **Unattributed but cross-port hunks (18%) are catalogue gaps.** These are edits real ports made in
  2+ mods that no catalogue entry describes, and they are the first candidates for new entries and
  recipes.

**H3: are an entry's fixes a few repeatable options?** For each catalogue entry hit in 3+ mods with 20+
hunks (84 entries, 14,395 hunks), the share of its hunks covered by its three most common loose shapes
(which identifiers were removed and added):

| Top-3 shapes cover | Entries | Their share of those 84 entries' hunks |
|---|---|---|
| 50%+ of the entry's fixes | 66 of 84 | 94% |
| 80%+ of the entry's fixes | 38 of 84 | 66% |

So most attributed work is not open-ended: for two thirds of it, a recipe that offers three named
options (or applies the single dominant one) matches what real ports did at least 80% of the time. The
most-hit entries split cleanly into two kinds. Some are effectively one edit: V4 is 88% one shape, 25 is
95%, 136 is 98% and 111 is 99%. These are `auto` recipes. Others are a short menu: entry 1 is 46% one
shape but 92% within three, and V6 and 13 behave the same way. These are `choice` recipes. A few stay
open (entry 32 at 54%, entry 16 at 43%) and keep the model.
