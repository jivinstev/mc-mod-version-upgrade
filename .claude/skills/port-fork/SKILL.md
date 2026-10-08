---
name: port-fork
description: Port a mod IN ITS OWN SOURCE REPOSITORY (a GitHub fork) to a newer NeoForge, as a least-diff branch in the authors' layout, then prove it with CI, publish the built JAR as a pre-release, and prepare (never send) a link and write-up the authors can look at. Use when the user names a mod's GitHub repository or fork rather than a jar, asks to "port the fork", "upgrade my fork of X", "make a branch the author could merge", or wants CI/releases or an upstream offer for a fork port. For porting from a jar into this repository, use migrate-mod instead.
---

# Port a mod in its own repository (fork → branch → CI → release → offer)

The deliverable is a branch in the mod's own repository, `neoforge-<mc>`, that a maintainer could read
as a normal change: their layout, their build, the smallest diff that works. Test harnesses never enter
their tree. Everything below is a script; you run them in order, read what each prints, and stop where
this file says to stop. The scripts do not need you to edit the mod by hand.

Targets: `neoforge-1.21.1` is proven: three forks ported with every gate green, CI from step 5 green on the
forks it has run on. `neoforge-26.2` is wired but
**unproven** — it starts from a finished 1.21.1 branch of the same fork. Say so when the user asks for it.

## Stops — ask the user before each of these

1. **Before any model stage.** Show the licence result and the estimate (step 2) and wait for a yes.
2. **Before pushing anything** to GitHub (the port branch, the CI commit, the offer branch).
3. **Before a release run.** It publishes a downloadable JAR under the user's name.
4. **Never** open a PR, issue or comment on the authors' repository, and never contact anyone. Step 7
   produces links and drafts; sending them is the user's decision.

If a stage costs well over the estimate, stop and say so with the numbers before resuming.

## 0. Is the port needed, and is it allowed?

```bash
python3 tools/mod-registry/modreg.py versions --provider modrinth --id <slug> --loader neoforge --mc <target>
```
`has_native: true` means the authors already ship it: no port. Then the licence — `port-upstream.py`'s
first stage checks it and stops on all-rights-reserved or unknown unless `--permission <url>` names the
authors' written permission. Only MIT/Apache/LGPL-style licences go ahead without it.

## 1. Fork and clone

The user forks on GitHub (or you do, if they ask). Clone the fork *outside* this repository; the
mod's code never enters it:

```bash
git clone https://github.com/<user>/<repo> ~/forks/<repo> && cd ~/forks/<repo> && git fetch --all
```
Dependencies that are themselves forks being ported: port those first (step 3 per dependency), then
pass `--provide <authors' coordinate prefix>=<mavenLocal coordinate>` to the dependent port. The
dependency's own build publishes it with `./gradlew publishToMavenLocal`.

## 2. Estimate (no model)

```bash
python3 tools/port-profile.py ~/forks/<repo>
python3 tools/port-deps.py --repo ~/forks/<repo> --mc <target>
```
The profile names the cost-driving patterns and the tool covering each; the deps check finds blocked
hosts and dependencies with no build for the target **before** anything is spent. Measured fork ports
for comparison (Forge 1.20.1 → NeoForge 1.21.1, as each port's `COST.md` recorded it): a library of 198
Java files and 25 mixins, $5.86; a mob mod of 353 Java files and 25 mixins that depends on it, $8.94. A port well outside those sizes, with coremods,
custom shaders or many mixins, costs more — say so.

## 3. Port

```bash
python3 tools/port-upstream.py --repo ~/forks/<repo> --modid <modid> --branch neoforge-<mc> \
    [--provide OLD=NEW] [--budget 20] \
    --trailer "Co-Authored-By: ..."      # the attribution lines this session requires
```
Stages: licence, deps, designer, branch, build, metadata, mechanical, burndown, gates (A and B),
author-build, normalise, reviewer, provenance, report. It stops at the first stage that fails and says
why; fix the cause it names, then resume with `--from <stage>`. A stage run by hand is recorded with
`--record <stage>=<usd>`. The work dir is `~/.mc-mod-upgrade/upstream/<repo>-<branch>/`: `DESIGN.md`,
`REVIEW.md` (read its "needs a person" list to the user), `COST.md`, `state.json`.

## 4. Verify like CI will (no model)

```bash
python3 tools/ci-gates.py --repo ~/forks/<repo> --modid <modid> --base origin/<authors' branch>
```
The authors' own `build`, Gate A, Gate B and Gate C (a real client under Xvfb: `launch`, `spawn`), with
nothing fixed. This is exactly what the fork's CI will run, so a red here is a red there. Gate C needs
Xvfb + Mesa (`apt-get install -y xvfb libgl1-mesa-dri`; the CI workflow installs the same).

## 5. CI and release

```bash
python3 tools/port-ci.py --repo ~/forks/<repo> --modid <modid> [--dep <user>/<lib>@neoforge-<mc>] \
    --upstream https://github.com/<authors>/<repo> --trailer "..."
git -C ~/forks/<repo> push -u origin neoforge-<mc>          # stop 2
```
This adds `.github/workflows/port-ci.yml` as a commit of its own. It downloads the **Port CI kit**
(a release asset of this repository holding only the gate runner, pinned by version and sha256) and runs
step 4 on every push. Wait for the run to go green, then release (stop 3): **Actions → Port CI → Run
workflow → branch `neoforge-<mc>`, release: true**, or `gh workflow run port-ci.yml --ref neoforge-<mc>
-f release=true`. It publishes the authors' own JARs as a pre-release named `<modid>-<version>-mc<mc>`,
only if every gate passes. Give the user the release link; they smoke-test it on their machine.

## 6. (dependents) CI that needs another fork

A dependent mod's CI builds its dependency's fork into mavenLocal first (`--dep`). Prefer pointing it at
the dependency's **released** JAR once that exists, so the dependent's CI does not rebuild a branch that
can move under it.

## 7. Offer it to the authors (nothing is sent)

```bash
python3 tools/port-offer.py --repo ~/forks/<repo> --push       # stop 2: pushes <branch>-upstream
```
Makes `neoforge-<mc>-upstream` — the port branch without our CI commit — and writes `OFFER.md` in the
work dir: the compare link to send (authors' code → the port), a pre-filled "propose a PR" link on the
authors' repository that opens nothing by itself, the size and shape of the diff, what was verified (read
from `state.json`, never paraphrased), and a draft message and PR description. Hand the user the file and
the links. Whether, when and how to contact the authors is theirs to decide.

## 8. Close out

- Report the total cost (`COST.md`, plus any hand-run stages) against the step-2 estimate.
- Lessons for the catalogue go through the normal route (`CONTRIBUTING.md`), written without the mod's
  name — this repository never names or links third-party mods (`tools/check-no-ip.py`).
- Delete `run/` in the clone once done; GameTest worlds are reused and only grow.
