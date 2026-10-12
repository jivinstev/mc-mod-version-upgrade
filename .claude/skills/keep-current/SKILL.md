---
name: keep-current
description: Keep a fork port current with its authors, unattended or on request -- bring in their new commits and releases (and new releases of the forks it depends on), reconcile every defect fix the port carries against what the authors changed (take their fix when it is proven to cover ours, keep or shrink ours when not), port the new code, gate it, and push it as a PR on the fork, with each fork issue's fix ledger kept current. Also sets this up as a nightly Claude Code routine on the user's own subscription. Use when the user asks to update, sync or keep current a fork port, to reconcile with the authors' new version, to check whether the authors fixed a bug we fixed, or to schedule that.
---

# Keep a fork port current

**Which fork:** a single-version fork (one `neoforge-1.21.1`-style branch) is the default and the proven path; everything in
"Run" below is for it. A fork with the opt-in multi-version layer (`.multiversion/`) gets the extra step in "A fork
with the layer" after step 6, and nothing else changes for it. `tools/layer-sync.py detect --repo DIR` says which.

The port-fork skill makes a port; this one keeps it alive. A community fork is judged by the people who install
it: **if it crashes them, kicks them from a server, hangs, damages a save or breaks their other mods, the fork is
"busted", whoever's code did it.** Everything below serves that.

Tools: `tools/port-sync.py` (bring the authors' commits in), `tools/fix-ledger.py` (the worksheet and the ledger),
`tools/port-upstream.py` + the file loop (port the new code), `tools/ci-gates.py` (gates, no model),
`tools/port-ci.py` (CI; `promote` is never run here), `tools/port-fleet.py` (every fork, dependency order).

## Stops -- ask the user

1. Setting up the schedule (once): which forks, the time, what may be pushed unattended.
2. A reconcile decision the rule below does not settle (both outcomes would ship an S1 mode, or the only fix is a
   design change of the authors' feature).
3. Merging a keep-current PR into the port branch -- unless that fork's fleet entry says `"automerge": true`. The
   user may set it for every fork whose releases they promote by hand: the pre-release a merge produces is not
   what players are pointed at until a person has played it and promoted it, so merging on green is safe.
4. **Never**: promoting a release (a person must have played it -- `port-ci.py promote` is theirs), publishing to
   Modrinth/CurseForge, pushing to or commenting on the authors' repository, force-pushing.

## The fleet and its order

The fleet file (outside this repository: it names other people's mods) is `tools/port-fleet.py`'s. Order is
**dependencies first**. Per-fork fields this skill reads beyond port-fleet's: `"upstream"` (the authors' repo and
branch), `"automerge"` (merge its keep-current PR once CI is green), `"ledger_issues"` (the fork issues that carry
fix ledgers), and -- only for a fork with the multi-version layer -- `"layer_branch"` (the branch carrying
`.multiversion/`, where `layer-sync.py detect` runs; a single-version entry has no such key). A dependent's CI pins its dependency by exact release asset (`--dep-jar`, written into
the dependent's `port-ci.yml`), so read the edges from those pins, not from memory. A dependent is not touched
until its dependency's run is green, and it is then gated against the dependency's NEW build.

## Run (per fork, in fleet order)

1. **Nothing new? Stop cheaply.** `port-sync.py --dry-run` for the authors' branch, and for each dependency
   "has it a newer green build than the dependent pins?". No change anywhere: exit, no model work.
2. **Merge** on a work branch `keep-current/<date>` cut from the port branch: `port-sync.py` (a merge, never a
   rebase). Fetch immediately before merging and name the exact commit merged in the message, read from git
   (`git rev-parse --short <remote>/<branch>`), never from memory: the authors can push while a reconcile is
   running, and the next run decides from what this one says it merged (`port-sync.py` now puts it in the message).
   **A conflict in a file one of our fix commits touched** cannot wait for step 3 -- the merge cannot be committed,
   and the A/B needs a tree that compiles. Resolve it to the AUTHORS' side, say in the merge message that the
   resolution is provisional ("decision and A/B: <issue> ledger"), and if step 3's A/B then fails, commit our fix
   back on top. Also bring the fork's mirror of the authors' branch up to the merged commit (a push: only with the
   user's allowance from stop 1) -- CI judges Gate A's exemptions against that base.
3. **Reconcile** -- the step that is easy to do badly. Do all of it, every run that brings anything in:
   1. **Read every issue, the text not your memory of it**: this fork's AND every dependency's and dependent's
      fork, open and closed, plus each fix commit's message. `fix-ledger.py worksheet` lists our fix commits,
      which files the incoming change (and each dependency's) touches next to them, and what it adds there.
   2. **Split each issue into failure modes**, each with a severity and a reproduction:
      S1 crash / kick / hang / save damage / other mods broken -- S2 this mod's feature broken -- S3 cosmetic.
      One issue is usually several modes (a disconnect, a feature switched off, a state that survives a reload),
      and the authors' change commonly covers some of them and not others.
   3. **Cross the dependency boundary both ways.** A dependency's new release can cover part of what a fix in
      this mod was for (it now clears a state on logout), and our fix in the dependency can make a dependent's
      fix unnecessary or wrong. Ask it for every fix of ours, in both directions.
   4. **Decide per mode, by the rule**:
      - the authors' (or the dependency's) change resolves the mode, **proven by an A/B with two arms**: the
        CONTROL (neither fix -- our fix reverted, theirs not applied, nothing else changed) must SHOW the mode,
        and the TREATMENT (theirs) must not. A control that does not show it means the reproduction does not
        reproduce -- fix the reproduction, do not drop the fix. (A library can mask its own bug: one coremod
        replaced the very list the bug mutated, so the failure only existed where that coremod was absent, and
        a first control printed "mode absent" over a bug it could not reach.) Then **drop ours**, take theirs;
      - it resolves part of it: **shrink ours** to the residual (often a one-line guard that does what the
        authors' own comment says the code meant to do);
      - otherwise **keep ours**.
      "The authors touched that file" is not evidence, and neither is a code reading alone when the A/B can be
      run. A mode that would ship as S1 is never accepted to save diff. A file the authors now ship
      **byte-identical** to our fix is their adoption of it: the merge drops our diff by itself; record it as
      fixed by them, with the comparison as the evidence.
      **The authors' fix is written for THEIR loader.** When it hooks something that does not exist on ours (a
      Forge networking class, say), it is not a port but our re-implementation of their intent: decision
      `adjust`, with its own two-arm A/B -- their evidence does not carry over. If two re-implementations both
      pass, prefer the smaller diff against their code and the one proven in the runtime where the mode happens
      (a real client for a networking or rendering mode; a GameTest can only approximate one).
      **Some modes need a real installed instance to reproduce** (a library whose transformation backend only
      loads from a mods folder, not a dev classpath). A `keep` needs no A/B; a `drop`/`accept` of such a mode
      stays `keep` until the reproduction has been run where it can happen -- by a person if no gate can.
      **An A/B is only evidence if it ran on the tree you think it did.** Cut it on its own throwaway branch
      (`git worktree add -b ab/<what> <dir> <work branch>` -- never the work branch itself, which is checked out
      already and makes `worktree add` fail), stop on any setup failure (`|| exit 1`, never `;`), and before
      reading the result assert the reverted code really is absent (grep for the line the fix removed or added).
   5. **Record it in the ledger** of each issue (`fix-ledger.py render --into`, posted with the GitHub connector):
      per mode, per branch, `fixed` / `fixed-unconfirmed` (shipped, repro not re-run where it reproduced) /
      `open` / `not-affected`, by whom (`ours <sha>`, `upstream <repo>@<sha>`, `dependency <repo>@<tag>`), the
      decision, the evidence, and for drop/accept/adjust the A/B arms (`"ab": {"control", "treatment"}`, or
      `"identical"` for a byte-identical adoption). An issue closes only when every mode is fixed
      or not-affected on every branch that ships. `fix-ledger.py check` must pass. A defect that spans two
      forks (a library and a mod that uses it) has ONE ledger, in the library's issue; the other fork's issue
      links it. Name each branch column for what a player can tell apart: the released build (`r6`), upstream
      (their version, their Minecraft), and the pending reconcile -- named after the work branch
      (`keep-current/<date>`), which is the column `check --release` takes (it fails if the name matches none).
      **Judge the released column too.** Splitting an issue finely can show an S1 mode already open in what
      players have now (latent in a library: reachable by any other caller). That is a stop: tell the user, and
      offer to expedite this reconcile's PR and pre-release.
4. **Port the new code**: the authors' new commits are written for their Minecraft version. (An authors' `gradlew`
   committed without its exec bit: run `bash gradlew`, do not chmod it -- that is diff in their tree.) Compile
   (`tools/burndown-count.sh`), run the mechanical stage, fix the rest with the catalogue. Mixins they added need
   their targets re-checked against this version (a mixin into networking or rendering is only exercised by a
   client gate -- Gate B passes even when it fails to apply).
5. **Gate** (`ci-gates.py`, full and minimal; one run at a time -- the software-rendered client gives false
   failures under CPU contention, and the A/B worktrees share `~/.gradle`, so they count too). Then each mode's reproduction, for the modes this run decided.
6. **Push the work branch and open a PR into the port branch** -- the fork's versioned branch, e.g.
   `neoforge-1.21.1` -- with: the incoming commits, the reconcile table (mode, severity, decision, evidence), the
   gate tables, the ledger changes. Merge the port branch into the work branch first if it moved (a CI-only
   commit, say), so the PR is current. Run `fix-ledger.py check --release keep-current/<date>` first: an S1 mode
   open on a branch that ships blocks the PR, and the run stops and reports.
   **Auto-merge** (fleet entry `"automerge": true`): wait for the PR's own CI -- every gate, full and minimal --
   and merge with a merge commit (never squash or rebase: the authors' commits must stay commits). A red or
   missing check is not merged: fix it on the work branch, or stop and report. After merging, start the fork's
   release run (`release=true`): it cuts a pre-release, which dependents re-pin to (step 7). Promotion stays a
   person's (stop 4). Without `automerge`, leave the PR open for the user.
   Once the pre-release exists, rename this reconcile's ledger column (`keep-current/<date>`) to its tag (joined
   with ` + ` in a ledger shared with a dependency): publishing (the publish-port skill) judges the column that
   names the tag it uploads, and refuses a tag no column names.
7. **Dependents next.** Gate each dependent locally against this build (its `_mr`-style jar put into mavenLocal
   the way its CI does). Its pin (`port-ci.py --dep-jar`, a release asset + sha256) can only move once this
   dependency's PR is merged and its CI has cut a pre-release: put the exact re-pin command in the dependent's
   PR as a blocking item, and re-pin then. A pre-release may be pinned for CI; the dependent's own release waits
   for the dependency's to be promoted.
8. **Report once** on the fleet's tracking issue: what came in, what was decided, what is waiting for a person
   (a PR to merge, a build to play and promote). Silent when nothing changed.

## A fork with the layer (opt-in; skip this for a single-version fork)

Only when `layer-sync.py detect` says `layer=true`. The port branch's PR (steps 1-6) is the authors' code at the base
version; the layer branch must carry it to every target, and nothing fixes a middle target unless this is run.

1. Cut `keep-current/<date>-layer` from the layer branch and `python3 tools/layer-sync.py merge --repo DIR --port-ref
   keep-current/<date>` (the port work branch, or the port branch once its PR is merged): a MERGE COMMIT, never a
   rebase or force-push; a conflict is aborted and listed (it means the layer's isolation rule was already broken).
2. `python3 tools/layer-sync.py run --repo DIR --json t.json --md t.md`: per target, oldest first, fork-port.py's
   `stage..measure`, then the audits at 0 errors or the compile loop (`--budget`) otherwise. Each target is
   **untouched** (0 errors, clean audits right after the merge), **touch-up** (the loop cleared it) or **rework**
   (residue left: `.multiversion/targets/<t>/RESIDUE.md`). It also lists the upstream-changed files each target
   OVERLAYS: an overlay replaces the whole file, so read it against upstream's new version even when it compiles.
3. **A rework target stops the run (exit 10) for a person.** It is never skipped, never released as is, and never
   reported as done; fix what its RESIDUE.md lists in the shared library, then `layer-sync.py run --targets <t>`.
4. Put `t.md` (target | errors before | errors after | class | residue) in the layer PR body and link it from the port
   PR; `layer-sync.py ledger --ledger L.json --json t.json --column keep-current/<date>` then `fix-ledger.py render
   --ledger L.json --into BODY` puts the same table in the fix ledger's issue.
5. Gates: the layer's CI runs the full gates on the base and the newest target and compile + audits on the middle
   ones; a target you intend to release gets `ci-gates.py --mc <t>` (full) first.

## Setup: run it nightly on the user's subscription

A Claude Code routine fires a fresh cloud session on a schedule under the user's own account. Create one (with
the user's go-ahead, stop 1) whose prompt is: run the keep-current skill over the fleet, in run mode, with the
pushes the user allowed. A cloud routine starts from a fresh container, so a fleet file in this session's home
directory is not there when it fires: put the fleet JSON in the routine's prompt. The environment needs the forks
in its repository scope and the build hosts in its allowed domains (`cloud/allowed-domains.txt`). Cheap nights
matter: step 1 exits with no model work.

**A routine session cannot add repositories: they must be configured ON THE ROUTINE.** Measured twice: a fired
routine session searched for `add_repo` at 8 s, and in a second run polled for it for over 3 minutes -- it never
appears in a routine session, while the routine's own config showed `sources: []`. The tool that creates a routine
from a session (`create_trigger`) has no way to set its repositories, so:

- **The user adds the repositories to the routine.** Give them the exact link and steps, never just "the Routines
  page": open https://claude.ai/code/routines, click the routine by name, open the menu next to its name, choose
  **Edit**, then use the small **Select a repository** control at the bottom-left of the **Instructions** box (easy to
  miss: it is not a separate section of the form) to add each repository (list them as `owner/repo`), and save. Each one is cloned on every run. Until they
  have, the routine cannot work, however its prompt is written.
- **The prompt checks, it does not wait.** It looks for the clones it expects; if one is missing, it still does step
  1, then stops and names the missing repository and where to add it. It never polls for `add_repo`.
- **Step 1 needs no repository.** The "anything new?" check only reads: `git ls-remote` the public repositories over
  HTTPS (the session's git proxy serves public reads). A night with nothing new ends there either way.
- **After creating or editing the routine, fire it once** (`fire_trigger`) and read the run's first events: it must
  find its repositories. A routine that has never fired cleanly is not set up.

**A time like "2am" means the user's local time, and a cloud session runs in UTC.** Do not ask for the zone;
read it from git. The user's own commits keep the offset of the machine they were made on, while commits made in
a cloud session or on github.com say `+0000`:

    git log --all --author=<user's email> --format='%ad' --date=format:'%Y-%m %z' | grep -v '+0000' | sort | uniq -c

Map the offset, together with the months it was seen in, to an IANA zone. Daylight saving changes the offset, so
an offset seen in both summer and winter months narrows it down. If more than one zone still fits (`-0700` in
October is both Los Angeles and Phoenix), pick the most populous one, write the cron as
`CRON_TZ=<zone> <min> <hour> * * *` so daylight saving is handled, and say in one line which zone was assumed and
why. The user corrects it if wrong. Ask only when no commit carries a non-UTC offset.

## Evidence that this is needed (and the mistake it prevents)

Reconciling two forks with their authors' new versions, the first pass judged each issue as a whole: "the authors
fixed the disconnect, the rest is theirs" -- and kept two fixes in the dependent mod without asking whether the
library's new "clear the switch on logout" had covered one of them, while missing that a saved entity re-arms it on
every world load despite the authors' own comment saying it should only happen at spawn. Splitting the issue into
modes, crossing the dependency boundary and running the A/B is what turned that into decisions that hold.
