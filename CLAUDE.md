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

## Single-version first

A fork port is single-version by default: one `neoforge-<mc>` branch, built, gated, synced and published as it is
today. The multi-version layer (`.multiversion/`, the `fork-versions` skill) is **opt-in per fork, by an explicit
decision**; for now only Epic Core API and The Last Sword have it, and Dungeons Libraries and Dungeons Mobs stay
single-version. So:

- Every multi-version behaviour (layer branch CI, `fork-port.py`, direct jumps, `full_gates`, the checkout lock) is gated
  on the fork having `.multiversion/` and defaults off. A change to a shared tool (`ci-gates.py`, `port-ci.py`,
  `port-sync.py`, `port-publish.py`, the skills) must leave a single-version fork's behaviour byte-for-byte as it was.
- Check every such change against a single-version fixture as well as a layer fixture, and say so in its self-check or
  `tools/test-port-tools.sh` test (a layer-only assertion proves nothing about the default path).
- `fork-versions`, `keep-current` and `publish-port` say in their first lines which kind of fork they are on and default
  to single-version; do not turn a single-version fork into a layer fork without being asked.
