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

## Stage 3–4 — the recipe engine and the first measured packs (2026-10-07)

`tools/apply-recipes.py` is the engine. A recipe pack is a `prepare-sources.py` rename table (so every
§X guard still applies), with rows grouped under `#@ <catalogue entry> <kind>`:

- `auto` groups rewrite in place. They may be scoped with `#? only-if <regex>`, so a bare library name
  is renamed only in files importing that library.
- `choice` groups list each site with its named options (H3).
- `manual` groups list each site with the one entry to read.

Each run reports rewrites per entry and names any group that matched nothing.

Measured on the bench as **start compile errors**, javac's cap lifted, every count validated by
`burndown-count.sh`. Errors are not cost: one fix can clear a cascade, and fixing a layer can unmask
the next. They are, though, the work queue the model would otherwise start from.

**To 1.21.1, Forge 1.20 starts (9 rows):** raw → this repo's two existing codemods → those plus the
first pack (`tools/recipes/forge-1.20-to-neoforge-1.21.1.recipes.tsv`, 20 auto groups and 8 manual
pointers, each row a catalogue entry's own Pattern → Fix):

| Profile | Raw | Existing codemods | + Stage-4 pack | Pack's cut |
|---|---|---|---|---|
| P2 | 233 | 228 | 192 | −16% |
| P3 | 9,948 | 7,661 | 3,485 | −55% |
| P4 | 698 | 556 | 476 | −14% |
| P5 (4 rows) | 3,744 | 2,909 | 2,136 | −27% (0% to −58% per row) |
| P8 (2 rows) | 3,159 | 2,568 | 2,420 | −6% |
| **all 9** | **17,782** | **13,922** | **8,709** | **−37%** (−51% from raw) |

**Era jump to 26.2 (10 rows with a count both ways):** raw → the multi-version template's inherited
rename table with the class-move map, run as one recipe (`tools/recipes/era-26.2.tsv`):

| Profile | Raw | Template table | Cut |
|---|---|---|---|
| P9 (5 rows) | 676 | 433 | −36% (−18% to −76% per row) |
| P10 (2 rows) | 3,567 | 2,479 | −31% |
| `-era` rows (3) | 8,247 | 2,197 | −73% |
| **all 10** | **12,490** | **5,109** | **−59%** |

What the per-family check found (`compile-summary.py` compares each row's error groups before and
after the pack):

- **One real regression, now fixed.** The pack's bare `RenderUtils → RenderUtil` row (GeckoLib, §138)
  also renamed a mod's OWN class of the same name, so the row went up by 9 errors. That is §X7's trap,
  and it produced the `only-if` scope above. After the fix, that row is back to its starting count.
- **Swaps rather than regressions.** On two rows a recipe exchanged one error for another at the same
  count: `setMaxUpStep` on a non-living entity, which has no attribute map, and a GeckoLib wildcard
  import that leaves `GeoBone` unimported. These are candidates to downgrade from `auto` to `choice`.
- **Unmasking.** The other new groups are the next layer a fix exposed (§144); the totals still fell.
- **Pack coverage is uneven.** Of the 20 auto groups, 1–9 fire on any one row. The rows the pack barely
  moves (0–6%) have few sites for its 20 entries; most of their errors sit in families the pack does not
  cover yet (`compile-summary.py` names them). The rows it moves most
  are large, uniform ones with many identical sites (55–58%).

## Step 6/7 spike — the per-file loop with cheaper models (2026-10-07)

`tools/file-loop.py` on two ports from the same starts as their published baselines (P4 after the
Stage-4 recipe pack). Each worker is a headless Claude Code session in a clean context: about 30k
tokens of floor, no inherited project memory, no shell. It gets only its files' errors and the
catalogue entries they match. Dollars are Claude Code's own per-worker totals; they exclude the
orchestrating session.

| | P1 (downport, 45 start errors) — Haiku first | P1 — Sonnet first | P4 (library, 468 after recipes) |
|---|---|---|---|
| Compile rounds | 1 | 1 | 3 per-file (Haiku → Sonnet → Opus) + 1 subsystem (Sonnet) |
| Dead overrides (override probe) | 18 found and fixed, one repair round | 16 found and fixed | probe too noisy on a library (see below) |
| Gate B | all 6 tests pass | all 6 tests pass | all 2 pass after 3 load-crash fixes (§R1 ×2, a client class on the server) |
| Workers | 7 Haiku | 5 Sonnet | 26 Haiku, 17 Sonnet, 7 Opus, 1 subsystem Sonnet, 3 gate fixes |
| Worker dollars | $0.82 | $0.88 | $15.33 |
| Published baseline (whole port, incl. Gate C) | $6.88 | $6.88 | $30.31 |

P4's rounds: Haiku took 468 → 175 errors ($7.12), Sonnet 175 → 45 ($4.05), Opus 45 → 33 ($2.70). The
last 33 were the capabilities → attachments and packets → payloads rewrites, which span files: one
Sonnet worker allowed to edit any file finished them in a single pass ($1.11).

What the spike showed:

- **The loop reaches a clean compile and a passing Gate B on both ports, for half (P4) to an eighth
  (P1) of the published whole-port cost.** The published runs also did Gate C and wrote the port's
  notes, which these numbers do not include.
- **Haiku and Sonnet cost about the same per fixed error** (P4: $0.024 vs $0.031). Haiku also needed
  repair rounds. Escalating per file sent P4's cross-file work through Opus for little gain. A
  subsystem round as soon as workers report `NEEDS` would have saved most of that $2.70.
- **Compile-clean is not done.** P1 still had 16–18 overrides that no longer overrode anything, which
  the finished port had fixed by hand; the probe round fixed them. P4 still had three load crashes.
  Two of those were §R1 cases that a static scan finds before any server boots.
- **Gaps found:** the override probe needs supertype awareness on library mods (164 hits on P4, 60
  of them naming a vanilla method, nearly all false). Fix-once-apply-everywhere (H1) did not get a fair
  test: workers wrote their `RULE` rows as prose, and the parser now tolerates that.

## Single-shot workers vs the spike's agent workers (2026-10-07)

`tools/singleshot.py`, the default in `tools/file-loop.py`, works differently from the spike's agents.
A script gathers each file's errors, the catalogue entries they match, and the target's own declarations
of the types those errors name. Coverage includes nested types, enum constants and the members the code
calls. A worker with no tools and a ~1k-token system prompt then answers once with SEARCH/REPLACE edits.

Every edit is checked before it lands:

- **All or nothing:** it applies whole or not at all, and only to the worker's own file.
- **Indentation-tolerant:** the search text may be matched ignoring indentation, but only if the match is
  unique.
- **Guarded:** edits are rejected if they unbalance brackets, delete a large share of the file, comment
  code out, add `UnsupportedOperationException` or empty method bodies.
- **Contained:** a round that still breaks a file's syntax reverts only that file.
- **Escalated:** files that keep failing move up the ladder: Haiku single-shot (no thinking) → Sonnet
  single-shot → Sonnet agent → Opus agent.

`tools/run-port.py` runs recipes → loop → static scan → override probe → Gate B loop. Same starts as the
spike; the dollars are workers only.

| | P1 agent spike | P1 single-shot | P4 agent spike | P4 single-shot |
|---|---|---|---|---|
| Compile | 45 → 0 | 45 → 0 | 468 → 0 | 468 → 0 |
| Static scan / dead overrides fixed | — / 16–18 | — / 16 | — (by hand at Gate B) / — | 7 / 8 |
| Gate B | green | green on the first run | green after 3 hand-driven fixes | green on the first run |
| Worker dollars | $0.82–0.88 | **$0.51** | $15.33 | **$6.83** |
| Published whole-port baseline | $6.88 | | $30.31 | |

P4 single-shot by tier:

| Tier | Calls | Edits applied | Cost |
|---|---|---|---|
| Haiku single-shot, no thinking | 101 | 92 | $0.67 |
| Sonnet single-shot | 46 | 45 | $1.21 |
| Agent workers (subsystem, scan, fallbacks) | 7 | — | $4.95 |

**One subsystem worker** (the cross-file capabilities → attachments and packets → payloads rewrite) cost
**$3.69, 54% of the run**. It is the next thing to make cheaper.

Two fixes the first attempts forced, both now in the tool:

- **Haiku re-indented its search text.** 9 of 10 edits missed until the whitespace-tolerant match was
  added.
- **Thinking was 90% of a Haiku answer.** On one 12-error file it cost $0.057 with thinking and $0.0064
  without, and both edits applied.

**Worker-proposed rewrites (H1), tested with no model spend.** Each rewrite row the spike's P4 workers
proposed was applied alone to P4's post-recipe start and compiled. Of 33 rows, 13 matched nothing and 15
did not reduce errors. The 3 that helped saved 1–9 errors each, out of 468. Rewrites written by workers
are not worth chasing; repeated diffs would need to be turned into rules by the script itself, which is
not built.

## The cross-file worker as one request (2026-10-07)

The single-shot A/B left one large cost: the cross-file change (capabilities → data attachments,
packets → payloads) still went to a tool-using subsystem agent, $3.69 of P4's $6.83. That job is now
`singleshot.run_multi`: one Sonnet request that is shown every file in the group, plus the mod classes
the errors and the workers' NEEDS notes name. It answers with `FILE` / `NEW FILE` / `DELETE FILE`
blocks. The agent runs only if nothing in the answer can be applied. Load-crash scan findings take the
same route.

**P4, from a fresh post-recipe start (468 errors): 0 errors, Gate B green on its first run, $4.99.**

| | spike (agent workers) | single-shot | cross-file as one request |
|---|---|---|---|
| P4 worker spend | $15.33 | $6.83 | **$4.99** |
| cross-file job | — | $3.69 (agent) | $1.33 over 5 rounds (0.88 + 0.10 + 0.16 + 0.10 + scan 0.09) |
| Gate B | green after 3 fixes | green first run | green first run, $0 |

It took four runs to get there, and each failure taught something:

1. **All-or-nothing across files threw away good answers.** One stale SEARCH, or the deletion guard
   refusing a capability attacher that §13 says to remove, sent the whole 30-file answer to the agent
   ($4.40). Now each file is accepted or skipped on its own, and the next compile checks the result.
   The deletion guard is waived only when what goes is Forge API that has no NeoForge form
   (`REMOVED_API`). `DELETE FILE` is allowed only for a file built on that API.
2. **The file cap hid most of the group.** With 12 files shown out of 32, the answer named 8 sibling
   attachers it had never seen. The cap is now 48 files and about 240k characters.
3. **Wiring.** Files still failing after a cross-file pass were moved up the per-file ladder as if an
   agent had handled them, which sent 33 files to per-file Opus agents ($5.52). Now they get one more
   cross-file request with the new errors, then the Sonnet subsystem agent, and never Opus one file at
   a time. Two loop exits were also wrong: a round with only a cross-file job counted as "nothing to
   do", and one round without progress counted as a plateau before the higher tiers had been tried.
   Both are fixed; the loop now stops after two stalled rounds in a row.

The final run hit the second exit bug at 2 errors ($4.66). It was resumed in the same workspace after
the fix: $0.33, including the scan and probe rounds. So the $4.99 is two invocations stitched together,
not one clean run.

**Correctness read of the diff** (post-recipe tree against the final tree, 114 files changed):
- 4 attachers deleted and 5 reduced to registration stubs. That is the §13 rewrite.
- The tool item class lost `TierSortingRegistry` (removed in 1.21) and `canApplyAtEnchantingTable` (§63).
- One optional-mod integration branch was left as a `TODO`, because the mod is not on the classpath.
- One judgement call to flag: the armour item's `getMaxDamage` now returns `getType().getDurability(15)`, a
  constant where the 1.20 code read the configured material. The reference port drops the configured
  materials for vanilla CHAIN, so it loses the same behaviour, but a constant is easy to miss in review.

No `UnsupportedOperationException`, no emptied methods beyond one registration hook, and no
commented-out code.

Against the spike, P4 is now about 3× cheaper ($15.33 → $4.99); against the Opus-baseline estimate for
worker spend, about 6×. P1 was already about 13×.

## Gate C without an agent, and mod-specific checks that are now scripted (2026-10-07)

Gate C used to be run and judged by the orchestrating agent. It is now four scripted pieces:
- `scaffold-gatec.py` writes the client harness from the template;
- `gate-loop.py --gatec` runs each phase under Xvfb, with a fix worker only on failure;
- `behaviour-tests.py` writes mod-specific outcome tests;
- `visual-review.py` reviews the frames the harness now saves.

All of it runs from finished workspaces (P1 $0.51 and P4 $4.99 to compile and pass Gate B), so these
numbers are what each addition costs on top of that.

| | P1 (tiny generated mod) | P4 (library) |
|---|---|---|
| Gate C, four phases | green first time, $0 | green first time, $0 |
| Behaviour tests | 7 written, all pass, $0.14 | 7 written, all pass after 1 compile repair, $0.21 |
| Visual review (11 frames) | Haiku $0.036 / Sonnet $0.081 | script only: nothing to find (0 items, 2 short-lived entities) |
| Found | **one real defect** (below) | nothing |

**The behaviour tests check outcomes, not existence.**
- P1: each mob drops its item, attribute values match the code, two foods restore hunger, and both
  mobs ignore fluid push and drowning.
- P4: the soul orb's damage, icon, discard and save/load paths, a totem's effect-then-death cycle, and
  the library's attribute defaults.

They are written as optional GameTests, so a failure is a finding and Gate B stays green.

**The visual review found a defect every gate had passed.** In P1 one block's model names an empty
texture path, so the block renders as the missing-texture cube once placed. The reference port has
shipped it since its first commit, which means it came from the original mod. It is invisible to the
compile, Gate A, Gate B and a crash-only Gate C. The game log has one WARN line about it, and nobody
had read that line.

**Getting usable frames took four harness fixes**, all now in the template:
- the item sheet was drawn under a second blurred background, so it was unreadable in every Gate C;
- spawn frames showed terrain, because the mobs spawn behind the default camera; a flat stage is now
  cleared and the camera moved a few blocks from the nearest mob;
- toasts covered part of the frames;
- a cleared toast still appeared in the same tick's frame, so toasts are now cleared two ticks earlier.

**Haiku vs Sonnet on the same frames.** Both flagged the real defect on all three frames it appears
in. Haiku also claimed missing textures that are not there:
- four times across earlier runs (red borders drawn into a mod texture, a green jungle, a GUI frame);
- once on the final run.

Every such claim is now checked against the frame's measured magenta share. Under 0.05% the claim is
dismissed and kept in the report as dismissed, which removed all of Haiku's false missing-texture
claims. With that check Haiku catches what Sonnet does for under half the cost, so it stays the
default; `--model sonnet` is the option when a port's frames are busy.

These checks still cannot judge behaviour that needs a person: whether a mechanic *feels* right, or
whether an effect looks the way the author meant. That stays in `MANUAL_VALIDATION.md`.
