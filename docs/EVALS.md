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
