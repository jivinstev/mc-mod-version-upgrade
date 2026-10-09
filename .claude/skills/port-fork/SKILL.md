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
**Turn Issues on in the new fork** (GitHub turns them off for forks): Settings > General > Features >
Issues. Step 4b files the authors' pre-existing defects there, and no tool here can change that setting --
ask the user to tick it. `tools/port-offer.py` reminds you when it is still off.

Dependencies that are themselves forks being ported: port those first (step 3 per dependency), then
pass `--provide <authors' coordinate prefix>=<mavenLocal coordinate>` to the dependent port. The
dependency's own build publishes it with `./gradlew publishToMavenLocal`.

## 2. Estimate (no model)

```bash
python3 tools/port-profile.py ~/forks/<repo>
python3 tools/port-deps.py --repo ~/forks/<repo> --mc <target>
```
The profile names the cost-driving patterns and the tool covering each; the deps check finds blocked
hosts and dependencies with no build for the target **before** anything is spent. Add `--api` (and
`--provide OLD=NEW` / `--api-tree <ported dep's src/main/java>` for a sibling port) to see, per dependency,
what the mod imports, which of its overrides changed signature, and whether the ids it names still exist:
"integrates with a dozen mods" is often a handful of unchanged APIs plus string ids. Measured fork ports
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
The authors' own `build`, Gate A, Gate B and Gate C (a real client under Xvfb: `launch`, `spawn`, `battle`
-- every mob in two teams fighting -- and `gauntlet` -- every item used, block placed, effect applied), with
nothing fixed. A client that passes is still red when its log shows the mod's own textures, models, sounds or
GeckoLib animations failing to load, or an exception thrown in the mod's own code that something caught and
only logged (CATALOG §X49). A defect the authors' original already has is recorded, not hidden: one substring
per line in the fork's `.github/gatec-known.txt`, with `# why` -- the run then lists it and stays green. This is exactly what the fork's CI will run, so a red here is a red there. Gate C needs
Xvfb + Mesa (`apt-get install -y xvfb libgl1-mesa-dri`; the CI workflow installs the same).

CI runs it twice: `--env full` (every mod the build puts on the runtime) and `--env minimal` (only the
mods the port's `neoforge.mods.toml` requires). Run both. In minimal, `tools/optional-dep-scan.py` reads
the compiled classes before Gate B and fails when a listener class names an optional mod in a method's
types (NeoForge cannot even scan it without that mod); a mixin that reaches one is reported, because it
runs whether or not the mod is installed and is safe only behind a "mod loaded" check.

## 4b. Defects the authors' original already has

The gates (above all the X49 log check) find defects the port did not cause. Run the gates with every
Gate C phase first, and once more with the mod together with what players install it with (its dependents
too): a defect a mod shows only in company (one mod's code calling another's) never appears in its own run.
Then, for each finding:

0. **Trace it to its cause** -- the asset, registration or code line the log line points at (a texture path
   built from a prefix, a sounds.json entry, a sound event id, an animation value, a missing placement). A
   finding is one cause; several log lines can share it.
1. **Prove it is pre-existing** before calling it that: the same code or asset on the authors' own branch
   (`git show origin/<authors' branch>:<path>`, with the commit), and where vanilla behaviour decides it, the
   old Minecraft version's own classes (`javap` on its server jar). "Probably was like that" is not evidence.
   Check the authors' NEWEST code too: they may have fixed it after the release (take their fix, and say so),
   or their fix may have a side effect of its own (fix that as well, in the same commit, and say so).
2. **Clear-cut fix** (one obvious correct change: a missing registration, a dead `sounds.json` entry, a
   malformed value, a missing atlas source) -- fix it on the port branch as **one commit per defect**, subject
   `Pre-existing upstream defect: <what>`, the body naming the authors' file and commit that has it, the fix,
   and the gate that now proves it. One commit each keeps the port's own diff readable and lets anyone
   cherry-pick a single fix.
3. **No clear-cut fix** (it needs art, or it is the authors' design, or the choice is theirs) -- change no
   code. Add a line to the fork's `.github/gatec-known.txt` (`<substring>  # pre-existing upstream, #<issue>`)
   so the gate lists it and stays green. Ask the user first when the call is not obvious.
4. **File an issue on the fork for every one, fixed or not**, titled `Pre-existing: <symptom>`: that it is
   pre-existing (with the authors' file:line and commit), the symptom and log lines, the impact, and either
   the fix commit or the options. A fixed one is filed and closed by its commit; an open one is the record.
5. **Do not backport to the authors' old branch.** There is no test setup for their Minecraft version here,
   and an unvalidated fix in their branch costs more than the bug. The issue and the single commit are the
   hand-off; backport only when a maintainer asks or someone plays that version, and then build Gates A/B
   for it first.

**Bring the user one decision table before changing anything**: per defect, the mod, what a player sees,
the proof it is pre-existing, and either the clear-cut fix (flag any that change behaviour, e.g. new spawn
rules) or the options when there is none. Ask about the non-obvious ones; a fix that needs art may have a
stand-in worth trying -- look at it in a real client (a Gate C photo) before proposing it.

**The handoff shows them.** `tools/port-offer.py` adds a "Defects the authors' original already has"
section -- the `Pre-existing upstream defect:` commits, the `gatec-known.txt` lines and the fork's
`Pre-existing:` issues -- and `tools/offer-page.py` puts it on the handoff page, so whoever installs or
reviews the port sees what was wrong before it, what was fixed, and what is still open.

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

A dependent mod's CI needs its dependency in mavenLocal. Once the dependency has a release (step 5), use
that JAR, pinned by sha256 the same way as the kit:

```bash
python3 tools/port-ci.py --repo ~/forks/<dependent> --modid <id> \
    --dep-jar <user>/<lib>@<lib release tag>/<lib jar>=<group>:<artifactId>:<version>
```
with the coordinates the dependent's `build.gradle` asks for. `--dep <user>/<lib>@<branch>` (build the
dependency's branch first) works only when that build publishes those exact coordinates. A build with no
`rootProject.name` publishes under its checkout directory's name, so it breaks quietly; prefer `--dep-jar`.

## 7. Offer it to the authors (nothing is sent)

```bash
python3 tools/port-offer.py --repo ~/forks/<repo> --push       # stop 2: pushes <branch>-upstream
```
Makes `neoforge-<mc>-upstream` — the port branch without our CI commit — and writes `OFFER.md` in the
work dir: the compare link to send (authors' code → the port), a pre-filled "propose a PR" link on the
authors' repository that opens nothing by itself, the size and shape of the diff, what was verified (read
from `state.json`, never paraphrased), and a draft message and PR description. Whether, when and how to
contact the authors is the user's decision.

**Standing rule: every offer carries "How to install", per target, and so does anything built from it.**
`port-offer.py` writes it from what was TESTED, never the newest file on a registry: the NeoForge version the
build names (with its installer), this fork's release jar, each required dependency at the exact version the
ported build declares (`neoforge.mods.toml` decides required vs optional) with a direct file link, sibling
ports from the exact release asset CI installed, optional integrations, and the build's other runtime mods
(usually a library an optional one needs). When a release has one jar per platform, pass
`--variant <substring>=<why>`: platform builds can differ in content, not only packaging (one library's
CurseForge jar leaves out the Java agent its Modrinth jar carries), so a dependent must be installed with the
variant its CI tested. An install list the user cannot follow end to end on a fresh instance is a bug.

`OFFER.md` lives in the work dir, outside both repositories: it is never committed and nothing reads it
back. Once the `-upstream` branch is pushed (so its links resolve), offer to publish it as a **private
artifact**: a page the user can copy from that outlives the session (a cloud session's work dir does
not). It may name the mod, being outside this repository; the no-names rule covers this repository only.

**Standing rule: every handoff carries the 10 manual tests that matter most, with full steps.** No gate
presses a key, listens to music, fights a boss to the end or opens a screen, and those are where ports break
unseen (a client-to-server packet sent on one action took a player's game down after every gate was green).
`port-offer.py` runs `tools/manual-tests.py` on the checkout and puts the list in `OFFER.md` ("Manual tests")
and `manual-tests.json`. The list is picked round-robin across kinds -- music first, then client packets,
bosses, keys, screens, raids, curios, custom recipes, structures, natural spawns -- so ten tests cover ten
different things, and each step names a real `/summon`, `/give` or `/locate` id read from the mod's own
registration code. Read it before handing over: where a step says "see what starts it in <file>", open that
file and write the real trigger in. Ends with what to do when one fails (which logs, the prompt to paste).

**A test that is only safe in some setting gets a warning, and the warning lives in the work folder.** The
generator reads code; it cannot know that a debug mob wrecks the world it is summoned into (one did: summoning
it switched off every other content mod and disconnected the player, by the author's design). Write
`manual-tests.notes.json` next to `OFFER.md` -- `{"<test title>": {"warning": "...", "steps_before": ["..."]}}` --
and `port-offer.py` merges it on every regeneration; the warning shows above the steps in `OFFER.md` and on the
handoff page. Whenever a manual test turns out to be hazardous, add the note before the next handoff.

**When the fork is not on the release** (see "Two branches" below), pass `--release <commit>` or
`--published <ISO time>`: the offer then says how many unreleased commits the port sits on, how big they are
and what they are (`tools/port-provenance.py`). An offer that hides that is offering code nobody has played.

**The handoff page:** `tools/offer-page.py --mods <mods.json> --out <page.html>` builds one page from every
fork's `OFFER.md` + `manual-tests.json` (links, install steps, distance from release, checks, manual tests,
draft words). Publish it as the private artifact; update it in place after each release.

## Two branches: the author's release, and their newest code

A fork is usually cut from the author's development tip, and that can be far from what players run (measured:
52 commits, +4877 lines). Count it first:

```bash
python3 tools/port-provenance.py --repo ~/forks/<repo> --base $(git -C ~/forks/<repo> merge-base origin/HEAD neoforge-<mc>) \
    --registry modrinth:<slug> --registry curseforge:<id> --mc <author's mc> --loader <loader>   # or --published / --release
```
The release commit is matched by publish time (the last commit before the upload); say so when quoting it.
Name every registry the mod is on (the newest file wins: one can lag a version behind) and find the project by
the `displayName` in its mods.toml -- a similarly named mod once put a 1-commit distance at 34.
Small distances (one or two commits) are fine to ship as-is, with the provenance in the offer. For a large
one, ship BOTH:

- `neoforge-<mc>` -- the development port, on the author's newest code. Keep it current with
  `tools/port-sync.py --repo <clone> --port neoforge-<mc> --upstream origin/<branch>` (stop 2 before pushing):
  it merges (never rebases) the author's new commits, stops on a conflict with the files named, and says what
  to re-port. This is what to hand the author to maintain.
- `neoforge-<mc>-release` -- the port of what they released, derived with no re-porting:

```bash
python3 tools/port-derive.py --repo <clone> --branch neoforge-<mc> --base <fork point> --release <release commit> \
    --new-branch neoforge-<mc>-release
python3 tools/port-derive.py --repo <clone> --resolve --release <release commit> --record <work>/resolve.json   # stop 1: a model
```
The replay drops files that exist only in unreleased code and the port's handlers for them; `--resolve` then
gives each file still failing to one worker with the release copy beside it, and lists every removed line the
release also has. **Read that list**: a model clearing an error can delete a released option or registration
(measured: a config option and a registry registration, both restored by hand). Then the gates, same as any
port (they found two more: a mixin config naming unreleased mixins, and a latent release crash). Give the
release branch its own CI tag series and install manifest. When the author releases again, re-derive; never
sync the release branch.

## Every fork at once

`tools/port-fleet.py` runs the same step over every fork you maintain, in dependency order, with each
fork's flags in one fleet file kept outside this repository (it names the mods):

```bash
python3 tools/port-fleet.py --fleet ~/forks.json status
python3 tools/port-fleet.py --fleet ~/forks.json gates --env minimal
python3 tools/port-fleet.py --fleet ~/forks.json ci --push          # stop 2; then dispatch releases (stop 3)
python3 tools/port-fleet.py --fleet ~/forks.json offer --push
```
Use it whenever the kit or the offer changes, so no fork is left on an old one.

## 8. Close out

- Report the total cost (`COST.md`, plus any hand-run stages) against the step-2 estimate.
- Lessons for the catalogue go through the normal route (`CONTRIBUTING.md`), written without the mod's
  name — this repository never names or links third-party mods (`tools/check-no-ip.py`).
- Delete `run/` in the clone once done; GameTest worlds are reused and only grow.
