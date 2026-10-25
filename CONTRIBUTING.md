# Contributing

## Safety gates

Four checks run on every pull request, and all must pass:

| gate | question it answers |
|---|---|
| `tools/check-no-ip.py` | are we about to publish somebody else's work? |
| `tools/check-catalog-fidelity.py` | did we quietly lose a migration rule? |
| `tools/gen-vendored.py --check` | do the files we copy into *your* project carry the licence the manifest says? |
| `tools/check-compat.py` | will it still run on a Mac (bash 3.2, BSD sed/grep, Python 3.9) and on Windows? See CLAUDE.md "Portability". A `macOS` CI job also runs the suites there. |
| `tools/check-readme.py` | does the README still describe every skill (and did a skill change bring its README change)? Does a change to the single-version route say what it does to a single-version fork? |

Run them locally the same way CI does:

```bash
python3 tools/check-no-ip.py
python3 tools/check-catalog-fidelity.py --base origin/main   # honours `Catalog-removed:` lines (below)
python3 tools/gen-vendored.py --check
python3 tools/check-compat.py      # macOS / Windows portability (CLAUDE.md "Portability"); ~1 s
python3 tools/check-readme.py --base origin/main   # a skill change needs a README change, or a `README-unchanged: <reason>` commit line;
                                                   # a single-version route change, a `Single-version-change:` or `-unchanged:` line
./tools/test-gates.sh          # proves each gate FAILS on a planted violation
./tools/test-setup.sh          # proves ./setup keeps its re-run promises
```

On a pull request CI judges the catalogue against the **base** branch's census, so a rule that vanished fails
even after `--update`. Two gates refuse a removal unless a commit on the branch declares it, with a reason:

```text
Catalog-removed: <file>::<id> -- <reason>               # one per entry, the exact key the fidelity gate lists
Tests-removed: tools/test-x.sh <before>-><after> -- <reason>   # review-pr: a self-test that now asserts less
```

`check-catalog-fidelity.py --base origin/main` reads the first (CI passes `--base`; without it nothing is read); a
declared key that did not disappear fails too, so does a census committed without `--update` (it must list exactly the
entries there are now), and a declaration in a commit a later commit reverts no longer counts. CI also lists the
declared removals in the job summary. `tools/review-pr.py` reads the second and accepts a fall only with
exactly the counts it measures. For an id a file repeats the keys are positional (`3`, `3#2`, ...): deleting one
shifts the later ones, so declare the keys the gate reports, not the ones you deleted by eye.

`check-no-ip.py` scans the **committed tree** rather than the diff, because a file added in an
earlier commit and pushed later is still a publication. It treats "could not run" as a failure: a
gate that quietly did not run reports identically to one that passed, and what this one guards
cannot be recalled.

## Sending a change

`main` takes pull requests only. Fork, branch, open a PR, and let the gates run:

```bash
gh repo fork jivinstev/mc-mod-version-upgrade --clone
```

Install the hooks once after cloning: `./tools/install-hooks.sh`. The pre-push hook refuses direct pushes to `main`
and runs the IP gate before anything leaves your machine. The pre-commit hook runs `tools/check-compat.py`, `tools/check-encoding.py` and the
system `bash -n` (bash 3.2 on a Mac) on the staged `.py` and `.sh`, so a portability break is refused at commit time.
The pre-commit and pre-push hooks also run
`tools/check-similarity.py`, which refuses template Java transcribed from the NeoForge, Minecraft or GeckoLib
sources on your machine; CI cannot hold those sources, so this check exists only locally. Optionally, `FORBIDDEN_NAMES_FILE=` in
`.env.local` can name a private list of mods you have ported, and the hook then refuses those names too.

**The easiest contribution is a lesson from your own migration:** the skill's retrospective collects
them, and `python3 tools/propose-learnings.py --modid <modid> --push` opens the PR, gated, with the
ported mod's identity refused.

**Never include mod source, jars, or decompiler output** — not even in a test fixture. The migration
workspace lives outside the repository by default; please keep it there.

## Licence

MIT (see [`LICENSE`](LICENSE)). The files the tooling copies into your own
mod project (listed in [`VENDORED.tsv`](VENDORED.tsv)) each carry their own MIT header, so a copied file
stays self-describing and using it puts no obligation on your mod beyond keeping that header. The
Gradle wrapper files are Gradle's, under its Apache-2.0 licence. The decompilers are not shipped here:
`tools/download-tools.sh` fetches them, sha1-verified, under their own licences (Vineflower: Apache-2.0,
CFR: MIT).

By opening a pull request you agree that your contribution is licensed under the same MIT licence.
