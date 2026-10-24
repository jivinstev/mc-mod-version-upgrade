# Notes for Claude working in this repository

## A skill change is a README change

The README's "Skills" table and the sections above it are how people find what this repository can do. When a
change adds, removes or renames a skill under `.claude/skills/`, gives a skill a new job, or adds or changes a
script a skill drives (a new `tools/*.py`, a new flag people will type, a step that now runs by itself), update
`README.md` in the same pull request:

- the skill's row in the "Skills" table: what to ask in Claude Code, and the command-line equivalent;
- the section that describes the workflow, if what the user gets has changed;
- every `tools/` path the README names must still exist.

`tools/check-readme.py` enforces this: the pre-push hook (`tools/install-hooks.sh`) and CI run it. A skill edit
that genuinely needs no README change (a typo, an internal step) says so with a commit line
`README-unchanged: <reason>`.

Describe only what the code does: if a step runs on one route and not another, say which.

## Single-version and version-branch forks

A fork is single-version (one `neoforge-<mc>` branch) unless the maintainer opts it into version branches
(`fork-versions`); never convert one unasked. `fork-versions`, `keep-current` and `publish-port` say first which kind.

1. **Better for one fork is better for every fork**: a change to a port's, or its process's, quality, correctness,
   safety, cost, durability or efficiency works, and is tested, on both kinds.
2. **Multi-version machinery stays out, provably**: ladder hops, the cascade, per-version CI, the cross-version gate
   lock and direct jumps are never reached on a single-version fork, and a single-version fixture test shows it.
3. **Multi-version-only code costs single-version forks nothing**: no added complexity or nondeterminism, and a change
   to what an existing single-version fork does is said plainly, never silent.
4. **Simple wins ties**: decide, and record why in the PR; ask only for a real quality/cost/simplicity trade-off or a
   change a fork's owner would notice. If the layout can separate the concerns, change the layout.

- P1. One route check (`tools/fork-kind.py detect`); multi-version code sits behind it.
- P2. A PR touching the single-version route has a single-version fixture test and a `Single-version-change: <reason>`
  or `Single-version-unchanged: <reason>` commit line; the enforced file list lives in `tools/check-readme.py`.
- P3. Per-version files in `.port/<mc>/` on every kind of fork (the target; today mostly version branches).
- P4. One round and one driver (`round.py`) for any fork set (target; today `round.py` takes version-branch sets only).
- P5. A head branch is a single-version branch: what is built for it runs on single-version forks too, unless it reads
  another version's branch.
- P6. Done (#161 U7): the `.multiversion/` layer route is removed. A checkout with the layer at HEAD is refused by every
  tool that takes a fork checkout (`tools/fork-kind.py` `refuse_layer`, exit 2), naming `LAST_LAYER_COMMIT`, the last
  commit that supports it, where its migration runs. No new layer fork.
- P7. Docs say which route: what the code does on each (the rule above).

## Merging

Only the repository's human maintainer merges pull requests into main. An agent opens the PR, subscribes to it and
drives it to green (CI, review comments), but never merges, approves or enables auto-merge on this repository -- even
when every check is green and even when the merge is needed to publish a kit.

## Keep fork identities out of this repository

This repository names no mod, mod id, mod package, author or mod repository: not in files, fixtures, comments or
commit messages. Every agent working on a mod or fork runs, before its first commit here, one command for the mod
and every fork it depends on: `python3 tools/guard-names.py ensure --mod-repo DIR [--mod-repo DIR ...]` (or `--jar JAR`). It merges
their names into the list OUTSIDE this repository (`FORBIDDEN_NAMES_FILE`, default `~/.mmvu/forbidden-names.txt`),
sets `FORBIDDEN_NAMES_FILE` in `.env.local` when unset and installs the hooks (`tools/install-hooks.sh`); the pre-push
hook then refuses a push whose commit messages or added lines contain any listed name, and the IP gate scans the
whole tree for them. `guard-names.py check` with the same arguments must pass before committing or pushing here.
Use invented placeholders (`examplemod`, `com/example/mod`) in fixtures and self-checks.
