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
