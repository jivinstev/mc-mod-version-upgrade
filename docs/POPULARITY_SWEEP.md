# The zero-model popularity sweep (October 2026)

A shakedown of the migrator on mods it had never seen. Each mod was ported up to the point where a model would
take over, and no further, so the whole run cost compute and no model tokens. The question was not "how cheaply can
these mods be migrated" but "where does the deterministic pipeline break, and what is worth automating next".

## How the mods were chosen

Two directions, each taken from Modrinth's download ranking, most-downloaded first (`tools/sweep/candidates.py`):

| Direction | Selected when |
|---|---|
| Forge 1.20.1 → NeoForge 1.21.1 | the mod has a Forge 1.20.1 build and **no** NeoForge 1.21.1 build |
| NeoForge 1.21.1 → 26.2 | the mod has a NeoForge 1.21.1 build and **no build for any 26.x** |

A mod already on any 26.x build is maintained by its authors and was left out entirely, not just a 26.2 one. Mods
already ported in the private ports repository, or swept in an earlier batch, were skipped. Library mods were kept,
since a dependent port often waits on one. The list was walked in batches of 40 (20 per direction, interleaved),
eleven batches in all: **384 mods**. Nothing was hand-picked, so the sample is as diverse as the ranking: content
mods, libraries, client tweaks, MCreator exports, multi-loader (Architectury) builds, and a few Fabric jars.

## How each mod was run

```bash
python3 tools/sweep/sweep.py <out-dir> "Mod Title|1.21.1" "Other Mod|26.2" ...
```

which runs `tools/port.py <mod> --to <target> --budget 0 --gate-budget 0 --gatec-budget 0 --stop-after
hop1-mechanical` for each mod, serially: download, dependency resolution, decompile, the recipe pack, every
converter, the owner-resolved member renames and the compile loop. It records the compile errors left at the
point a worker would start, then deletes the workspace. Exit codes: 0 the mechanical stage ran · 20 a native build
already exists · 21 a required dependency has no build for the target · 22 no recipe pack for the hop (e.g. a
Fabric source) · 23 a triage stop (Kotlin, a universal multi-loader jar, no metadata) · 25 the stage itself failed.

Between batches, every new failure shape was fixed in the pipeline (CATALOG X53–X170). After the last batch, all
384 mods were re-run with the final pipeline. That full rerun is the measurement below.

## What the full rerun found

| | |
|---|---|
| reached the compile stage | 258 of 384 (67%) |
| stopped on a dependency with no build for the target | 99 (26%) — no converter can fix this |
| zero errors left before any worker | 38 |
| 1–20 errors left | 80 |

**Did the fixes help?** The 200 mods that compiled on both their first run and the rerun:

| Hop | Mods | Errors left: first run → rerun | Improved | Worse |
|---|---|---|---|---|
| Forge 1.20 → NeoForge 1.21.1 | 120 | 39,252 → 22,855 (−42%) | 105 | 2 |
| 1.21.1 → 26.2 | 80 | 22,495 → 18,974 (−16%) | 70 | 1 |

One of the three "worse" mods was a real regression, found by this rerun and fixed (X170). The other two were the
compiler getting further and reporting errors that were already there.

**Is the rule library overfit?** Each rule's evidence is kept, so this is measured, not argued
(`tools/sweep/overfit-report.py`). The number of mods each rule rewrote something in:

| Rules | Fired at least once | In 1 mod | In 4+ mods |
|---|---|---|---|
| Forge recipe groups | 60 | 2 | 48 |
| Converters | 15 | — | all in 4–127 mods |
| Owner-resolved member renames | 97 | 39 | 35 |
| 26.2 rename rows added in this sweep (excluding one generated completeness block) | 68 | 29 | 29 |

Single-mod rules concentrate in the rename and member tables. They are API facts ("this method was renamed"), applied
only where javac names the owner type, so a rarely used one costs little. The real overfitting risk is in
shape-matching transforms, and the defence is the one used here: an A/B self-check per transform plus a full rerun
of the corpus after a batch of them.

## What to automate next

`tools/sweep/families.py` measures named error families over the rerun. Fourteen are worth a converter: one GitHub
issue each ([#55–#68](https://github.com/jivinstev/mc-mod-version-upgrade/issues?q=label%3Asweep-candidate), labelled
`sweep-candidate`), with prevalence, catalog references and a build estimate, ranked by value. Together they
cover roughly 12% of the errors left; the rest is a long tail of mod-specific code, which is what the model stage is
for. Families judged not worth a converter, with the reason:

- the 26.2 entity/block-entity render-state rewrite (the largest bucket, 15% of 26.2 errors): every renderer is
  its own design decision;
- recipe classes and serializers: each codec needs judgement;
- the 1.21 enchantment rework: the enchantments become data, and their behaviour lives in the mod's code;
- brewing recipes, music discs, `canBreatheUnderwater`: each needs data generated from the mod's own ids, for
  under 125 errors apiece;
- `Event.Result` and `Block.onRemove`: the right replacement depends on what the mod meant.

## Reproducing it

The per-mod evidence — summaries, `port.log`, per-rule hit files, recipe reports, and each mod's final compile log
(gzipped) — is archived in the private ports repository under `sweeps/2026-10-popularity/`, with the batch lists,
console output and the pipeline commit each run used. Its README holds the mod lists. With that archive:

```bash
python3 tools/sweep/rank-patterns.py <archive>/runs/rerunall --examples   # remaining errors, by mods hit
python3 tools/sweep/families.py      <archive>/runs/rerunall              # the candidate-converter table
python3 tools/sweep/overfit-report.py <archive>/runs/rerunall             # how many mods each rule serves
```

To repeat the sweep itself: `tools/sweep/candidates.py` draws the list, and `tools/sweep/sweep.py` runs it. Allow
about 2.5 minutes per mod that compiles. The run must be serial, and it resumes where it stopped.
