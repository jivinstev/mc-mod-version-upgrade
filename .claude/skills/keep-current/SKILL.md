---
name: keep-current
description: Keep a fork port current with its authors, unattended or on request -- bring in their new commits and releases (and new releases of the forks it depends on), reconcile every defect fix the port carries against what the authors changed (take their fix when it is proven to cover ours, keep or shrink ours when not), port the new code, gate it, and push it as a PR on the fork, with the known-issues ledger and each fork issue's fix ledger kept current. Also sets up the weekly cycle on the user's own accounts: a zero-model GitHub Actions plan issue every Tuesday 08:00 Pacific addressed to the fork owner, and the Wednesday-morning round as a Claude scheduled task that reads it. Use when the user asks to update, sync or keep current a fork port, to reconcile with the authors' new version, to check whether the authors fixed a bug we fixed, to run this week's round, or to schedule that.
---

# Keep a fork port current

**Which fork:** a single-version fork (one `neoforge-1.21.1`-style branch) is the default; everything in
"Run" below is for it. A multi-version fork has **version branches** (one branch per version, `neoforge-1.21.3`,
`neoforge-1.21.4`, ..., each carrying `.port/<version>/branch.properties`) and gets one extra step after step 6,
"A fork with version branches", nothing else changing for it. `python3 tools/fork-kind.py detect --repo DIR` says which:
`kind=single-version`, or `kind=version-branches versions=...` (local or origin's branches, so a clone checked out on the
port branch reads right). A checkout with the removed `.multiversion/` layer at HEAD is refused by detect and by every
tool (exit 2, naming the last migrator commit that supports it: fork-versions, "A fork still on the layer"); stop and
tell the user. **Dependency updates are explicit for every kind of fork** (a deliberate change to
single-version forks, decided by the maintainer): no step re-picks a pinned CurseForge/Modrinth dependency by itself --
`layer-deps.py resolve` keeps every pin, and a single-version fork's build.gradle pins move only by `layer-deps.py
resolve --bump` -- so a dependency moves only when the plan's "Dependency updates" box is ticked (or the user asks). A
version-branch fork is also kept on this repository's shared tables and helpers each round (`fork-branches.py sync-shared`,
run by `round.py`); a single-version fork has no shared library to sync, and nothing of this changes it.
A fork that has version branches AND a legacy layer branch with `.multiversion/` (one migrated to branches) is a
version-branch fork; the layer branch is history and is ignored, by the round and by every run. detect says so:
`kind=version-branches versions=... legacy_layer=<branch>`, exit 0.

The port-fork skill makes a port; this one keeps it alive. A community fork is judged by the people who install
it: **if it crashes them, kicks them from a server, hangs, damages a save or breaks their other mods, the fork is
"busted", whoever's code did it.** Everything below serves that.

Tools: `tools/port-sync.py` (bring the authors' commits in), `tools/fix-ledger.py` (the worksheet and the ledger),
`tools/port-upstream.py` + the file loop (port the new code), `tools/ci-gates.py` (gates, no model),
`tools/port-ci.py` (CI; `promote` is never run here), `tools/round-preflight.py` (what the round needs, checked first), `tools/port-fleet.py` (an older fleet file: only a migration input here),
`tools/weekly-plan.py` (the weekly cycle's setup and its plan workflow), `tools/known-issues.py` (the ledger, the plan
issue: `render-plan`, `read-plan`; the README Ports block: `render-ports`), `tools/port-cost.py` (cost records, the
round's table).

## Stops -- ask the user

1. Setting up the weekly cycle (once, and whenever it changes): which forks (the library first), what the unattended
   round may do without asking (the `allow` list, "Setup" step 1: none by default), and the round's model (Opus
   recommended; see "Setup"). The days are fixed by the tools: the plan Tuesday 08:00 America/Los_Angeles, the round
   Wednesday morning there, at a minute jittered per fork set (`round.time` in `.github/weekly-plan.json`).
2. A reconcile decision the rule below does not settle (both outcomes would ship an S1 mode, or the only fix is a
   design change of the authors' feature).
3. Merging a keep-current PR into the port branch -- unless that fork says `"automerge": true` in
   `.github/weekly-plan.json` (setup writes it exactly when the owner allowed `merge-fork-prs`). The user may allow it
   for every fork whose releases they promote by hand: the pre-release a merge produces is not what players are
   pointed at until a person has played it and promoted it, so merging on green is safe.
4. **Never**: promoting a release (a person must have played it -- `port-ci.py promote` is theirs), publishing to
   Modrinth/CurseForge, pushing to or commenting on the authors' repository, force-pushing.

## The fork set and its order

**The single source is `.github/weekly-plan.json` on the first fork's (the library's) head branch**, written only by
`weekly-plan.py install` ("Setup"). A fresh session reads it from there; nothing else holds the fork set. An older
fleet file (`tools/port-fleet.py`'s, outside this repository) is only an input to the first setup that moves its
values into flags; after that it is not read. Order is **dependencies first** (the `forks` list: the library first).
Per fork: `repo`, `head`, `role` (`library` / `dependent`; empty for a fork alone), `upstream` and `upstream_branch`
(the authors' repository and branch), `depends_on` (a dependent's dependencies in the set), `ledger_issues` (the fork
issues that carry fix ledgers), `automerge` (merge its keep-current PR once CI is green: true exactly when `allow` has
`merge-fork-prs`). For the set: `allow`, `budget`, `plan_owner`, `round` (its name,
time and cron). A dependent's CI pins its dependency by exact release asset (`--dep-jar`, written into the dependent's
`port-ci.yml`), so read the exact pins from there, not from memory. A dependent is not touched until its dependency's
run is green, and it is then gated against the dependency's NEW build.

## Run (per fork, in the set's order)

0. **Guard the names** (once per session, every kind of fork alike): `python3 tools/guard-names.py ensure --mod-repo <clone>` with one
   `--mod-repo` per fork of the set it depends on. A run that commits learnings here first passes `guard-names.py check`.
1. **Nothing new? Stop cheaply.** `port-sync.py --dry-run` for the authors' branch, and for each dependency
   "has it a newer green build than the dependent pins?". No change anywhere: exit, no model work.
2. **Merge** on a work branch `keep-current/<date>` cut from the port branch, by port-sync itself (a merge, never a
   rebase): `port-sync.py --repo DIR --port keep-current/<date> --base <port branch> --upstream <remote>/<branch>
   [--commit <sha>]`. It first compares the port branch (`--base`) with origin's (the version-branch cascade's check,
   `tools/origin-sync.py`, every kind of fork): strictly behind origin, it is fast-forwarded; diverged, refused (exit 2:
   merge origin's branch into it, then run again); never a reset or a force. It then cuts the work branch from it (an
   existing work branch that lacks the port branch's tip is refused: it was cut from a stale base), fetches the
   authors' remote and merges, naming the exact commit in the message ("Merge <remote>/<branch> at <sha> into <work
   branch>", read from git, never from memory: the authors can push while a reconcile is running, and the next run
   decides from what this one says it merged). `--dry-run` lists the incoming commits, reads origin's port branch when
   the local one is behind, and moves nothing. **To merge one exact commit** (the weekly round's frozen
   `upstream_commit`): `--commit <sha>` merges exactly that commit, never a newer tip, and refuses a commit the
   `--upstream` branch does not reach.
   **The merged data files are then put in the port's version's forms** (every kind of fork; #161 U6): a recipe,
   advancement or loot table the authors wrote for an older Minecraft is dropped at load with no other error (a
   1.21.1 ingredient on 1.21.2+, a 1.21 entity predicate on 26.2). port-sync runs the version-branch data converters
   (`tools/fork-port.py` `convert_data`, and `fix-datapack-layout.py --apply` when its `--verify` finds a pre-1.21
   layout) on the files the merge brought in, and commits what changed on its own, "data forms for <mc> after the
   authors' merge"; a file a converter refuses is listed in `.port/<mc>/DATA-FORMS.md` -- convert it by hand. After
   resolving a conflict and committing the merge, run port-sync again (or `port-sync.py --repo DIR --data-forms`):
   it converts that merge's files. The version-branch cascade does the same after each merge-up.
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
   5. **Record it in the ledger.** A fork whose config lists no `ledger_issues` keeps ONE ledger, the known-issues
      file (`.port/KNOWN-ISSUES.json` on the head branch), committed with the work: each defect's status at each
      version is `fixed@<sha>` of the fix that ships there -- ours, or the authors' commit taken -- and the decision,
      the evidence and, for drop/accept/adjust, the A/B arms go in its `notes`. Nothing is written per release:
      publishing proves from git that every S1 recorded fixed at a version has its fix commit in the release
      (`port-publish.py`, `known-issues.py` `fixes_missing`), and `fix-ledger.py check` does not apply. A fork that
      still lists `ledger_issues` records it in the ledger of each issue, as follows
      (`fix-ledger.py render --into`, posted with the GitHub connector):
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
   client gate -- Gate B passes even when it fails to apply). `ci-gates.py` checks them for you on every kind of
   fork: a `Mixin targets` or `Event listeners` REPORT row in its summary names each stale target or abstract-event
   listener (fix each before the PR), and `skipped: <why>` means that audit could not run (run
   `tools/audit-mixin-targets.py` or `tools/audit-event-listeners.py` by hand then).
5. **Gate** (`ci-gates.py`, full and minimal; one run at a time -- the software-rendered client gives false
   failures under CPU contention, and the A/B worktrees share `~/.gradle`, so they count too; in the weekly round the
   driver runs it under the machine-wide gate lock, and a worker runs any full gate run as `round.py gate -- <command>`). Then each mode's reproduction, for the modes this run decided.
   **A shipped-jar expectation that fails right after an upstream merge is not yet a regression.** The authors may
   have reworded the log line the expectation reads (e.g. a class logged by its internal name,
   `net/minecraft/server/level/ServerLevel`, where it was `ServerLevel`). Before deciding, bisect it with the same
   file: `tools/shipped-gate.py --jar <previous release's jar> --expect-file <the fork's expectations file>` and the
   same for the new build, then read both runs' mod logs (`<work>/server/logs/`). The line there in another form: the
   subsystem ran -- widen the pattern to accept both forms, still anchored to the effect (the same class, a count),
   prove it on both jars, and say so in the commit. The line absent: a regression -- a fix-ledger mode, as in step 3.
6. **Push the work branch and open a PR into the port branch** -- the fork's versioned branch, e.g.
   `neoforge-1.21.1` -- with: the incoming commits, the reconcile table (mode, severity, decision, evidence), the
   gate tables, the ledger changes. Merge the port branch into the work branch first if it moved (a CI-only
   commit, say), so the PR is current. With `ledger_issues`, run `fix-ledger.py check --release keep-current/<date>`
   first: an S1 mode open on a branch that ships blocks the PR, and the run stops and reports (with the known-issues
   ledger alone, an open S1 at a shipped version blocks the same way: `known-issues.py blocks-publish`).
   **In the weekly round the merge and the release run wait for the round's step 4** for that version: its gates, its
   fresh review and the grading of the review's `not-ok` rows (an S1/S2 there blocks both). Green CI alone is never
   enough there. For a version-branch fork this holds for the head's PR too, which still merges before the cascade
   starts -- the cascade starts from the merged port branch ("A fork with version branches" step 1) -- so the head is
   gated, reviewed, merged, then cascaded, and each version above in turn.
   **Auto-merge** (the fork's `"automerge": true` in `.github/weekly-plan.json`; in the weekly round only once step 4
   allows it, above): wait for the PR's own CI -- every gate, full and minimal --
   and merge with a merge commit (never squash or rebase: the authors' commits must stay commits). A red or
   missing check is not merged: fix it on the work branch, or stop and report. **Then wait for the CI run the merge
   itself triggers on the target branch** (a push run on the port branch, or the version branch for a version's PR) and
   report its result: a green PR does not prove the merged branch is green. A job that failed before any gate body
   ran (setup, dependency download -- e.g. "could not list the runtime's mods") is re-run once; a second failure, or
   any failure inside a gate, is reported as waiting on the user with the failing job's summary line. Never report
   a merge as done while its push run is red or still running. Start the fork's release run (`release=true`) right
   after the merge, without waiting for the push run first: it cuts a pre-release, which dependents re-pin to (step 7),
   and its release job needs that run's own gates on the merged commit (the same workflow template on every kind of
   fork). Wait for the push run and the release run together, in one blocking command. Promotion stays a
   person's (stop 4). Without `automerge`, leave the PR open for the user.
   Once the pre-release exists, rename this reconcile's ledger column (`keep-current/<date>`) to its tag (joined
   with ` + ` in a ledger shared with a dependency): publishing (the publish-port skill) judges the column that
   names the tag it uploads, and refuses a tag no column names.
   **Known-issues ledger** (a fork with `.port/KNOWN-ISSUES.json` on its head branch; the fork-versions skill,
   "Known issues"): grade each new finding into it with `tools/known-issues.py add` (only an S1 reopens a version
   that is done: `known-issues.py reopens`; a crash, kick, data loss or hang on a path nothing calls today is
   `--reach latent`, S2 -- never graded lower by the round: `grade --override` refuses it below S2 without the owner's
   `/grade`, applied with `--owner`), commit the ledger alone on the head branch (Port CI ignores a
   ledger-only push -- when its workflow is one port-ci.py rendered since the ledger exists; on a version-branch fork
   `fork-branches.py ci-check` names a head whose workflow does not), and take the release notes' known-issues section from it: `known-issues.py release-notes
   --repo <fork> --version <mc> --tag <tag>` (S1-S3; S4 omitted). A fork without the file keeps the fix ledgers
   above, unchanged; a fork with the file may drop its fix-ledger issues (remove `ledger_issues` from both configs,
   once each mode has its known-issues entry), and then this file is its only ledger (step 3.5). In the weekly round these steps run inside "The weekly round" below, on its frozen input.
7. **Dependents next.** Gate each dependent locally against this build (its `_mr`-style jar put into mavenLocal
   the way its CI does). Its pin (`port-ci.py --dep-jar`, a release asset + sha256) can only move once this
   dependency's PR is merged and its CI has cut a pre-release: put the exact re-pin command in the dependent's
   PR as a blocking item, and re-pin then (`port-ci.py args --repo DIR` gives the current command; change the tag
   and asset of `--dep-jar` AND of any `--dep-jar-variant` together -- a mismatch is refused -- and rerun it).
   **Don't exceed your dependency.** A dependent MAY cut its own pre-release pinned to the dependency's
   pre-release -- do it: a library is only really tested through a dependent, so that pair of pre-releases is what
   the owner plays. A dependent MUST NOT be promoted to a full Release, nor published, while a dependency it pins
   is still a pre-release; promoting both together is fine (`port-ci.py promote --with-dependency <owner/repo>`, and
   the dependency promoted too). `port-ci.py promote` refuses otherwise, and publishing refuses a dependency that is
   not published. The install manifest (`.github/port-install.json`) must follow: a promotion
   through Port CI refreshes it by itself; a re-pin without a promotion does not, so once the re-pinned workflow is
   on the port branch run `python3 tools/port-manifest.py refresh --repo DIR --commit` there and push (it says
   "unchanged" when nothing moved; it refuses, writing nothing, when the newest full Release cannot be named).
8. **Report once** on the fork set's tracking issue (in the weekly round: the plan issue): what came in, what was decided, what is waiting for a person
   (a PR to merge, a build to play and promote). Silent when nothing changed.

## A fork with version branches (skip this for a single-version fork)

Only for a fork whose `neoforge-<version>` branches carry `.port/<version>/branch.properties` (even when a legacy layer
branch with `.multiversion/` is still there: it is history, and ignored). The port branch's PR (steps 1-6) brings the authors' code in at the base version;
each version branch must take it, in chain order, and nothing fixes a version unless this is run. There is no R6
re-merge here: git's own merge does it, with the rename rows applied as a tool.

1. **Plan the whole chain first, in one pass.** Once the port branch has the authors' code (its PR merged into it -- the
   cascade starts from the port branch itself; a `keep-current/<date>` work branch is not accepted as the start):
   `python3 tools/fork-branches.py cascade --repo DIR neoforge-1.21.1..<newest version> --plan`. It runs every merge,
   port branch through the newest version, on a throwaway clone (no real branch changes, no model, git and the rows
   only), never stops at a conflict, and writes `build/fork-branches/plan/CASCADE-PLAN.json` and `CASCADE-PLAN.md`:
   the files merged mechanically, and each decision -- one per file and authors' hunk, however many versions it shows
   at -- with the authors' diff, our code at each affected version, its class and the default decision with its
   reasoning (fork-versions, "Planning the cascade"). With an escalated item it also writes `ISSUE.md`.
2. **Apply, decide, ask once.** `cascade ... --apply --gates ab` runs the same merges on the real branches: mechanical
   items resolved; a default the tool can carry out itself (an optional integration whose dependency that version's
   DEPS.md drops: ours kept, logged in `.port/DECISIONS.md`, the authors' change a deferred REMERGE.json entry that
   counts against done again once the dependency returns); every other decision file merged without the authors' change and recorded open in
   `.port/<v>/REMERGE.json` (the entry travels up with the file) -- every other file and every version above goes
   on, gates included, and only a version with an open entry stays not done. Then:
   - the **default decisions** are yours (the fixer agent's), by the policy: keep the authors' design intent; their fix
     is taken, adapted through the rows; a deliberate port change of ours (a stand-in, an API rewrite) is kept and
     their change applied around it; an optional integration is kept (stubbed where it is absent). Write each as an
     answer (`take`, `keep`, `patch` with a diff in the port branch's names, or `content`) with its reasoning;
   - the **escalated items** -- keeping the authors' intent is not feasible at that version, or the change is over
     `--budget-lines` (default 80) -- go to the owner as ONE issue on the fork: file `ISSUE.md` as its body. Never one
     issue per version, never a question per stop.
   - When the answers are in (the issue's `cascade-decisions` block saved to a file, plus your own):
     `cascade --repo DIR --apply-decisions FILE --gates ab` applies each one at every version that holds it, oldest
     first, clears the entries, logs them in `.port/DECISIONS.md`, merges up and writes the new plan; an answer that
     raises a new conflict higher up (rare) comes back as at most one follow-up issue. Run straight after `read-plan`
     (the weekly round: no `--apply` before it), it runs this step's `--apply` first so each decision has its entry. A
     `patch` with no diff -- read-plan's default for a decision nobody answered -- is what `--apply` does with it: the
     file stays merged without the authors' change and its entry open (the version not done); write the diff (in the
     port branch's names, from the entry or `CASCADE-PLAN.md`) or choose take/keep/content, and run it again.
   A red branch still stops the apply: it names its `.port/<v>/RESIDUE.md`; on that branch run `python3
   tools/fork-port.py --repo DIR --target <v> --from-stage measure` with a budget, commit, and apply on from there.
   Never skip a version. (Plain `cascade` without `--plan`/`--apply` still stops at the first conflict, for a fix
   carried up by hand; an authors' change it could not take is offered again by the next run, from the version's
   `port_taken`.)
3. **One version at a time, lowest first, each waiting for the one below it.** For version v: its gates and its review
   (the round's step 4) on the merged branch, then push it as a work branch and open its PR into the version branch
   (`git push origin neoforge-<v>:keep-current/<date>-<v>`, the cascade's table in the body; the cascade's closing
   line says the same in a round -- `--apply`, `--untaken-ok` or `round/frozen.json` -- never push `neoforge-<v>` itself), merge it by step 6's rule
   and wait for that merge's push run -- together with the version's release run, in one blocking command, when it cuts a
   pre-release (the weekly round's step 4: the release is dispatched right after the merge). **`keep-current/<date>-<v>`
   is pushed only after the PR of the version below it is merged and its push run is green** -- never stacked on an unmerged lower PR: a higher PR that carries a lower
   version's unmerged merge would merge that version's code past its own review and CI. After the lower merge, `git
   fetch origin` (the done check and the review rows read origin's branch when the local one is behind) and continue the
   cascade from there (`cascade --repo DIR <lower>..<newest>`: what is already merged is up to date, a merge it left
   ungated is gated first). **The 1.21.x -> 26.x boundary** (the rename between 1.21.11 and 26.1.2 changes method and
   class names the rows do not cover) usually conflicts. With the **plain cascade** (a fix carried up, `cascade
   <at>..<newest>`) it stops at 26.1.2 with the files named; resolve them by hand inside the cascade -- in that version's worktree (`build/fork-branches/26.1.2`), `git merge
   --no-commit neoforge-1.21.11`, then `python3 tools/fork-branches.py remake --repo build/fork-branches/26.1.2 --branch
   neoforge-26.1.2` (the cascade's rows step, which a plain `git merge` skips: every rows-only file re-made through
   26.1.2's rows, each other changed file carried through the rows 26.1.2 adds; it lists what is still in conflict),
   resolve each listed file through 26.1.2's rows and the rename (the 26.x name, the lower version's change), run
   `remake` again, commit, then `cascade --repo DIR 26.1.2..<newest>` -- and say in its PR which files were resolved by hand
   (they are review rows). Before that commit, `python3 tools/fork-branches.py cutout-check --repo
   build/fork-branches/26.1.2 --branch neoforge-26.1.2 --fix` (remake and the cascade's merge run it themselves; run it
   again after resolving by hand): every `entityCutout` / `entityCutoutNoCull` / `entityCutoutCull` call surely paired
   with its 1.21.11 line named by what that line meant (CATALOG V60); decide each `decide <file>:<line>` it lists (paired
   by position only, no parent line, or a culling fix this version made on purpose, which a cascade holds). With `--apply` (the head cascade of the authors' code) nothing stops there: a file it cannot
   merge becomes a decision with an open REMERGE.json entry, answered through step 2's decisions (`--apply-decisions`),
   never by hand. Every version whose gated files changed reads not done until its gates (`--gates full` once
   before a release) run again; where its own files changed it needs a fresh review, and where it took the authors'
   change through the rows only, one review of that change covers every such version (`python3
   tools/fork-branches.py review-authors --repo DIR --range <old port commit>..<new port commit> --reviewer ...`).
4. **Move each version's freeze in its PR, not after it**: on the version's checkout, once its code is in and before its
   work branch is pushed, `python3 tools/fork-branches.py freeze --repo DIR --target <v>` (and `--library owner/repo@TAG`
   for a re-pinned library release, already published by then; a round that leaves the authors' code out passes
   `--untaken-ok round/frozen.json`, so their drift does not refuse the freeze), and commit `.port/<v>/branch.properties`
   there, so the PR carries it. The freeze keys are bookkeeping: no gate or review goes stale (fork-versions, "Done" item 2), so it
   needs no gate run of its own. Committed alone onto `neoforge-<v>` after the merge, it was one more full Port CI run
   per version (2026-10-19 round: five, 8-14 minutes each) -- a path filter sees only that `branch.properties`, a build
   input, changed, never which key. Only a version whose freeze could not move before the merge (an authors' commit
   named after it) commits it afterwards, alone.
5. **Dependents.** A library's version releases from its own branch (Port CI's `release` on `neoforge-<v>`); re-pin the
   dependent's same version to it (`layer-deps.py resolve --repo DEP --target <v> --port-release owner/lib@<tag>` on the
   dependent's checkout of that branch, then `fork-branches.py ci --repo DEP --target <v> --modid ID` for its workflow), and
   once the dependent's Gate C is green against it, `fork-branches.py library-record` in the library (fork-versions,
   "Library first"). The library's version is not done before that record, and that is expected: `resolve` takes its
   pre-release when the gates are green at the tag (marked in the dependent's DEPS.md), never `--allow-undone-library`;
   the dependent's version is done, and may be promoted, only once the library's is.

## The weekly cycle (both halves installed only by "Setup" below)

The cycle is weekly, not nightly, for every fork set (a library and its dependents, or one fork alone), single-version
and version-branch forks alike:

- **Tuesday 08:00 America/Los_Angeles: the plan, with no model.** A GitHub Actions workflow on the first fork's (the
  library's) head branch only -- the dependents' repositories get no workflow and no config
  (`.github/workflows/weekly-plan.yml`, from `templates/weekly-plan.yml`; config `.github/weekly-plan.json`)
  fetches each fork's authors' upstream, runs `fork-branches.py cascade --plan` for each version-branch fork (when the
  authors have new commits, with `--head-merge <their commit>`: the round's head merge simulated on the plan's
  throwaway ref first -- git only, no push -- so the decisions their code raises up the versions are on Tuesday's plan;
  a file that conflicts in the head merge itself is listed as "Head merge needs an agent: N file(s)"; a preview that
  cannot run is listed as "Could not preview" and the plan goes on; a single-version fork, with no cascade, shows the
  fetch step's trial merge of the authors' new commits into the head instead: its conflicting files listed the same way
  in its Decisions section, the merge said clean, or "Could not preview" with git's error), `known-issues.py select`, `known-issues.py
  render-plan`, and opens **one plan issue** on the library's repository with a section per fork, assigned to the fork
  owner with an @mention. Its first line is the deadline: the round's start, Wednesday morning Pacific; with no answer
  the defaults apply. It lists the authors' new commits (a box to take them, ticked by default) -- for a version-branch
  fork also the authors' code the head already has that a version branch has not taken (`fork-branches.py untaken`:
  commits of some version's `port_taken..head` reachable from their branch, or merges of it; a head that merged their
  release earlier, with no cascade since, shows it with nothing new upstream), under the same box -- the known issues as
  checkboxes pre-ticked for the defaults (every open S1, then S2s up to the budget), each with its lowest open version
  (the status map; `lowest` is only the first affected one; on a version-branch fork `known-issues.py lowest` checks
  the branches below it with git alone: an entry's `pattern` matching lower is shown as "may be at <v> (unconfirmed)",
  the fix left where it is; a listed file byte-identical lower is shown as "lowest may be lower (unchecked)"), the escalated cascade decisions (the head cascade's: they
  apply only with the authors' box ticked) with their options
  (`/decide <id> <choice>`), the **dependency updates** (`weekly-plan.py deps`: per Minecraft version the fork builds,
  each pinned CurseForge/Modrinth dependency with a newer compatible build, current -> new, ticked by default and
  unticked with the reason when the new build is an alpha/beta, a major-version jump, drops a Minecraft version the fork
  builds or switches provider; on a version-branch fork also each dependency a version dropped that has a build for it
  now, an unticked **restore** box -- ticked, the round pins it again with the same `--bump`, and that version's
  integration code then needs compiling, porting and review; a failed lookup is listed "could not check"), and grade overrides (`/grade <ID> <S1-S4>
  [reason]`); its title starts `[decision needed]` when the plan escalates something. It costs no model quota, so it opens whether or not the owner has any left.
- **Wednesday morning America/Los_Angeles: the round** ("The weekly round" below), a Claude scheduled task with a fresh
  session each fire, at `round.time` in `.github/weekly-plan.json` (a minute below 08:30, jittered per fork set from the
  task's name -- read it there, never assume a fixed time). It reads the plan issue once, at its start, posts progress on it and closes it with the outcome.
- **No answer:** the defaults apply. **No round** (no quota, the session never started): nothing is lost -- next
  Tuesday's workflow sees the plan issue no round closed, carries its ticks and replies into the new issue, says "last
  week's round did not run", and closes the old one as superseded.
- **A failed plan run** fails visibly in Actions (GitHub emails the owner) and opens no issue; fix the cause (usually a
  fork that is no longer public, or a renamed branch: re-run setup) and run it by hand (Actions -> Weekly plan -> Run
  workflow; a second run in the same week opens no second issue).

Who "the owner" is: `plan_owner` in `.github/weekly-plan.json` (setup writes the login of whoever ran it, or an
`@org/team`), else the repository's owner when that is a user. The same owner is @mentioned by the round's comments, and
`read-plan` counts replies only from them (or members of the owner team) and from users with write access; anything
else is listed as ignored.

## The weekly round (what the Wednesday task runs; a fresh agent follows this alone)

Say first which kinds of fork the set holds (single-version unless `.port/<v>/branch.properties` branches exist;
those win over a legacy layer branch with `.multiversion/`, which is history and ignored -- `fork-kind.py detect` says
`kind=version-branches`). The fork set, its upstreams, pins, ledger issues and `allow` come from
`.github/weekly-plan.json` on the library's head branch, nothing else. The "Run" steps above are the per-fork mechanics;
this is the order and the rules around them. The round runs this repository's **current main** (its prompt clones it,
so it gets the tools' fixes); only Tuesday's plan workflow is pinned, at `migrator_sha` in that file, so that a plan is
reproducible from its commit. The two meet only at the plan issue: if `read-plan` cannot read it, stop and report.

**What the round may do unattended is the `allow` list** in that file (the prompt repeats it verbatim), and nothing
more: `push-version-branches` (commit to the fork's head branch -- its known-issues ledger and README Ports block
included -- and its `neoforge-<v>` branches; never force-push), `pre-release` (dispatch Port CI with `release=true`;
never promote, never publish to Modrinth or CurseForge), `merge-fork-prs` (push the round's own `keep-current/<date>`
work branches on the fork repositories, open PRs from them, and merge those PRs once CI is green; never the migrator
repository). **Always allowed, whatever the list says, because they are how the round reports:** comments on the plan
issue (and closing it at step 6), and edits to the ledger issues the config lists (`ledger_issues`). **Nothing on the
migrator repository, apart from reading it, is ever allowed.** **Any other action is a stop that asks the owner,
through the plan issue:** a comment @mentioning the owner with the action and why; the round does not take it, goes on
with what the list allows and names it in the closing comment. An empty list means every push (the work branches
included), PR, pre-release and merge asks.

**Keep the orchestrator small, and wait in one command** (every fork kind; the round-task prompt repeats it). Measured on
the 2026-10-19 round (one session ported a dependent fork across four versions plus a library fix, about $105): ~48% of
the spend was cache reads -- the orchestrator's ~565k-token context re-read on every turn --, ~39% cache writes -- the
whole context written again after each wait on gates or CI longer than the 5-minute cache --, ~14% output.

- **The orchestrator never does a version's porting, gating or review in its own context.** Each version's fixer and its
  reviewer are fresh subagents that return a short summary -- at most ~40 lines: what changed, the gate verdicts, the
  open rows, the commit shas -- and the orchestrator keeps only those (never a worker's transcript, a build log or a
  diff). It reads the plan, decides the order, starts the workers, merges, dispatches and reports.
- **A worker MAY carry the same change across several versions** -- a re-pin, one fix cascaded up -- while its context
  stays modest (measured: one worker carrying one fix through five versions cost ~$4 per version). Distinct porting work
  at each version (a cascade's decisions, a RESIDUE.md, a version's review fixes) gets a fresh worker per version
  (fork-versions, "One fresh residue fixer per Minecraft version": one fixer carried across six versions grew to ~650k
  tokens, ~$120). A reviewer is always fresh, one per version.
- **Wait in ONE blocking command, never by polling turn by turn**: `gh run watch <run id> --exit-status` (REST) for a CI
  run, or one until-loop over `gh api repos/<owner>/<repo>/actions/runs/<id> --jq .status` (a token's `curl` when `gh` is
  broken) run in the background or under Monitor; a local gate run started in the background, its completion awaited, not
  its log read as it grows. Wait for runs that go together (a merge's push run and its release run) in the same
  command. Each idle gap longer than 5 minutes re-writes the whole cached context, so fewer, longer waits on a small
  context are what keep the round cheap.
- **If the orchestrator's context grows large** (past ~200k tokens), it writes the round's state -- the frozen input, each
  version's status, the open rows and findings, the shas, what comes next -- to `round/NOTES.md` and continues from a
  fresh subagent (or session) that starts from that file, instead of carrying on.

**Every fork set runs on its driver, `tools/round.py`; the session never does a step by hand** -- a set of
single-version forks, of version-branch forks, or both mixed (#161 U4; round-task renders this into the prompt of every
set, and `--version-branches`, which older setups pass, changes nothing). On the 2026-10-21 round (two version-branch forks, 19 versions, about $215)
the orchestrator still reached ~549k tokens although its prompt said to hand off at ~200k, and cache writes were ~51%:
idle gaps on gates, CI and parallel workers, and three container restarts, each re-wrote it. A prompt rule did not keep
it small, so the orchestration is a tool, which replaces the four rules above:

- **`round.py init --frozen round/frozen.json --fork <owner/repo>=<clone> ...`** (after step 1's freeze; every fork, the
  library first) builds `round/STATE.json`: per fork and version, in ladder order, the steps below as typed items with
  their dependencies -- the library before its dependents, each version after the one below it merged, the head before
  the versions. A version gets items only when it has work this round -- the head cascade of the authors' code the
  plan takes, a selected fix at or below it, a library release of this round to re-pin to, a ticked dependency bump
  at it, or this repository's shared library moved past it (below) -- so a plan that selects nothing, on forks already
  on the current shared library, builds nothing but the close. It refuses a clone whose origin is not that fork. A
  version-branch dependent's version BRANCH (not its head) that depends on a single-version library is never re-pinned
  by the driver (`layer-deps.py resolve --port-release` takes only a version branch's release): `init` refuses the set
  only when the library releases this round AND that branch's Port CI workflow pins it; otherwise the queue is built and
  the close job names the branch to re-pin by hand. **State is the round's only memory:** after a container restart, or whenever this session's context
  has grown, continue from a fresh session with `round.py resume` (an interrupted mechanical step runs again; each one
  checks what is already done first).
- **The round keeps the forks on this repository's shared library.** `init` reads every version branch against the
  shared tables and helpers (`fork-branches.py sync-shared --check`: read-only, no checkout touched, seconds per fork).
  A version a sync would change gets a `sync` step first in its segment -- `fork-branches.py sync-shared --target <v>`
  as its own commit on `neoforge-<v>`, before its merge-up and long before its gates, so the review and the gates see
  it -- and then the whole version: gates, PR, merge, release, the dependents' re-pin. When the sync is a version's only
  change, its review job is the narrow one (did the fork depend on the old row or helper behaviour), not a full review.
  A table section that differs while the library's history cannot be read is treated as edited (reconcile), never
  rewritten on a guess. (Evidence: a dependent's 26.3 copy
  of a shared helper still crashed with a fix this repository had shipped a week earlier, because no round ran
  sync-shared.) A copy the fork edited by hand while the template moved is never overwritten: it is a `reconcile` job
  (bring the template's change into the fork's copy, then list the copy against the template it was reconciled with). A
  copy carried in unlisted that equals a text the library once shipped (an `MV-STAND-IN` copy, too) is a library copy and is
  synced like an installed one. The fork's own copies of what the library now ships are adopt candidates: an `adopt`
  job only for the ones the plan's "Adopt MMVU's shared code" box ticked (unticked by default). Code the fork marks
  `MV-AHEAD-OF-TEMPLATE` goes into the close job, one Lessons row each, to harvest. A cascade from a version whose
  installed helper only moved to its own library's copy keeps the upper version's installed copy.
- **`round.py drive`** (run in the background) runs every mechanical step itself, forks in parallel but never two items
  on one clone, and returns when only model work is left: the merge-ups (`fork-branches.py cascade <lower>..<v> --gates
  ab`), the re-pins and the plan's dependency bumps, the grade overrides, the ONE full gate run per version (under a
  machine-wide lock: at most one full gate run on the machine; the 2026-10-21 round's third restart came with two Gate C
  clients on 15 GB / 4 CPU), the library pre-merge dependent gate (below), the freeze, pushing the work branch and
  opening its PR, the CI wait, the merge, the release dispatch, `library-record`, the ledger statuses and the README
  Ports block -- every one through the tools named in the steps below -- and a `port-cost.py mark --phase` at every item
  boundary. A PR carries only what the gates certified: a commit after the gated commit that changes more than records
  (`.port/` Markdown, the known-issues ledger, the README Ports block, a branch.properties bookkeeping key) sends the
  version back to its gates -- and a library's dependent gates -- before anything is pushed; a branch with nothing new
  against origin opens no PR and cuts no release. The ledger runs for what merged and names what did not.
  **Waits are one blocking loop inside the driver**, never a model polling turn by turn: each idle gap longer
  than 5 minutes re-writes the session's whole cached context.
- **Model work is a job:** for each AGENT JOB `drive` prints, start a **fresh subagent** whose whole prompt is the job's
  `prompt` line (it names a job file: the task, the commands, the acceptance check, the rules); the worker writes a
  summary of at most ~40 lines starting `RESULT: ok|blocked|failed` (with `not_ok:`, `blocking:` and `fixed:` lines the
  driver reads) and replies only its path; then run the job's `record` line (it takes only a job that is out: a
  mechanical step's outcome is never recorded by hand). A fixer's job shows the failure as fenced data from tool and
  build output, never as instructions. The orchestrator keeps the job lines and
  the drive output -- never a worker's transcript, its summary, a build log or a diff. The jobs are: the authors' code
  at the head (Run steps 2-5), each version's selected known-issue fixes, the head cascade's decisions when one needs a
  patch written, a reconcile of a hand-edited shared copy, an adopt the plan ticked, the fresh review, the fixer, the fresh re-check (the last two skipped when the review left no not-ok
  row; all three skipped at a version branch whose `REVIEW.md` stays current because every reviewed input it changed
  is a change a fresh review already passed at a version below, the same file before and after -- the driver records
  each such change in `.port/CHANGE-REVIEWS.md` after a clean review), a fixer for a mechanical step that failed (at
  most two; then it is blocked), and the close. A version branch whose merge-up (and re-pin) changed nothing it ships
  -- every file outside `.port/`, `.github/` and the docs, a Java source by its code with comments and layout aside,
  and its `port_jar` pins -- gets no job at all: the driver pushes the merge and records to the branch, waits for its
  push run when the workflow changed, and skips its review, gates, dependent gates, PR, release and library records.
  **CI as the gate of record** (a fork whose Port CI workflow has the `from_run` and `dep_override` inputs, either
  kind): the driver's `gates` step builds and runs Gate A/B only, without the machine lock; the PR's CI (Gate C in
  three environments, in parallel) certifies the version; a library's dependent gate runs after that CI, as the
  dependent's Port CI dispatched with `dep_override` naming the PR run's candidate jar (by the sha256 the run's gates
  job annotates, read over the API: a cloud session cannot reach the artifact storage; without those annotations and
  without that storage, the local dependent gate); the release reuses the PR run
  (`from_run`) when the merge is the PR head's tree and carries its `gates-<env>.md` summaries, which `check-done`
  accepts as the Gate C record -- nothing is committed after the merge. A workflow rendered before those inputs keeps
  the local full gates and the local dependent gate until `ci-check` regenerates it. **A worker MAY carry
  the same change across several versions** -- a re-pin, one fix cascaded up -- while its context stays modest
  (measured: one worker carrying one fix through five versions cost ~$4 per version); distinct porting work at each
  version (a cascade's decisions, a RESIDUE.md, a version's review fixes) gets a fresh worker per version (fork-versions,
  "One fresh residue fixer per Minecraft version": one fixer carried across six versions grew to ~650k tokens, ~$120).
  A reviewer is always fresh, one per version. Workers are the session's own subagents (the Agent tool): a headless
  `claude -p` worker would need unattended permissions of its own, which the session's permission check refuses as an
  unsupervised agent.
- **`drive` exit 3 is a stop for the session**: its BLOCKED lines name each item that cannot go on (a graded S1/S2, a
  fixer that failed twice, a step the `allow` list does not hold -- the driver has already asked the owner on the plan
  issue for that one). Everything that does not depend on a blocked item goes on; the close job comes once nothing else
  can run -- but not over an item the owner can still put back (a job out of attempts, a step waiting on a permission)
  that holds other work: then `drive` exits 3 and the session asks the owner on the plan issue, retry or close. Once the
  owner answered (a re-grade below S2 recorded, that one step allowed, or `retry` in a plan-issue reply),
  `round.py retry <id> --why "<the answer>"` (`--allow` for the allowed step) puts it back, and a close handed out
  before goes back to the queue; an answer `close` is `round.py close-anyway --why "<the answer>"`, and the next
  `drive` hands out the close, which reports those items as not done. A graded S1/S2 needs no answer: the close reports
  it and the next plan takes it. A job whose worker a restart killed is offered again by `round.py resume` and never
  counts as an attempt (its earlier attempt's summary is set aside as `<job>.prevN.md`). `round.py status` is a
  compact table.
- **The library pre-merge dependent gate.** A library version merges only after the dependent's Gate C passed against
  the library's candidate jar: after the library version's own gates and review, the driver gates each dependent that
  has that version on a throwaway worktree of the dependent's branch, `fork-port.py --from-stage setup --gates full
  --port-checkout <library>=<the library version's checkout> --allow-undone-library` (the jar the library's own full run
  just built from the gated commit -- the checkout's old jars are removed before that run, and a jar older than it, or a
  checkout that moved on with code, is refused; development only, never committed). It is green only with exit 0 or 18
  and a GATES.md in the dependent's worktree certifying its HEAD with every row green. A red run is a blocking finding on the LIBRARY version -- its fixer
  grades it and fixes it there, and the library's full gates and this gate run again -- instead of a reopen, a `-r2`
  re-release and a dependent re-pin after the merge (the 2026-10-21 round, the second week running). After the release,
  the dependent re-pins to it and `library-record` records its Gate C on the released jar, as before. **A single-version
  library has the same gate** before its head merges, and so has a version-branch library's head or version wherever a
  single-version dependent has that version: the dependent's HEAD (origin's, on a throwaway worktree) is gated by
  `ci-gates.py` full and minimal -- the head path's commands, with its workflow's own `--base` -- under the machine-wide
  gate lock, against a PRIVATE mavenLocal (`round/work/depgate-<dependent>-<v>-m2`: a copy of `~/.m2/repository`, Gradle
  pointed at it with `-Dmaven.repo.local` through `PORT_GRADLE_ARGS`, which every Gradle call of a gate run carries)
  holding the library's candidate jar (the one the library's own gate pass just built, its old jars removed before that
  pass; the built jar of the pinned asset's name and variant) at the coordinates the dependent's Port CI puts the
  library's release (`port-ci.py --dep-jar`). `~/.m2` itself is never written, so a kill or a restart mid-gate, or
  another fork's build meanwhile, never meets the candidate there. It is green
  only when both runs exit 0; a red run is a blocking finding on the library, as above. A set of version-branch forks
  only keeps its head without this gate (unchanged for that set), and a head has no `library-record`.
- **A single-version fork on the driver is its head alone** (principle 2: nothing of the ladder -- the lowest check,
  sync-shared, the cascade, the freeze, `library-record` -- is built or read for it), in the single-version round's
  order: `ci-check` (`port-ci.py check` on origin's head: a WARNING never blocks; `setup` then regenerates the workflow
  on the work branch at the newest kit, its own options read back, so the PR carries it), `setup` (the work branch
  `keep-current/<date>` from origin's head, the ticked `dep_bumps`), `upstream` (the authors' merge, mechanical:
  `port-sync.py --port keep-current/<date> --base <head> --upstream <the remote fetching the plan's upstream>/<branch>
  --commit <upstream_commit>`; a conflict stops it red and becomes its fixer's job -- resolve, commit the merge -- after
  which it finds nothing left to merge), `authors` (an agent: Run steps 3-5, reconcile and port to green A/B),
  `repin-head` (an agent, a dependent: Run step 7's `port-ci.py args` edit to the library's release of this round),
  `backlog`, the fresh `review`, `fix` and `recheck` (on `.port/<mc>/REVIEW.md`, step 4 below), `records` (#161 U5:
  `fork-branches.py check-done` writes the done list's records -- TEST-ITEMS.md, INVENTORY-CHECK.json, any `?` row the
  review left -- and, when the head was re-pinned to a library pre-release whose version is not done, DEPS.md's
  "Library pre-release (not done)" mark; committed before the gates), then `gates` (ONE `ci-gates.py` full and minimal
  pass on the reviewed tree, under the machine-wide lock; the full run is committed as `.port/<mc>/GATES.md`; a fork
  marked a library in `.port/<mc>/port.properties` with no dependent gate in the set is refused, since its Gate C skips
  spawn and battle), `depgate:<dependent>` (a library), `done` (the done list, `fork-branches.py target_done`: the
  first round on a head with no `.port/<mc>/REVIEW.md` only reports it and makes the records; after that a version
  that is not done does not go out -- the library pre-release mark excepted, which stops promotion and publishing,
  never the pre-release), `pr` (only what that pass gated),
  `ci`, `merge` (no push-run wait) and `release` (dispatched right after the merge; the merge's push run and the release
  run awaited together), and the final `ledger` (statuses; the README Ports block only on a fork with
  `.port/KNOWN-ISSUES.json`). A version-branch fork's head keeps its own order (gates before the review, the push run
  awaited at the merge).

0. **Preflight, before anything else:** `python3 tools/round-preflight.py --fork <owner/repo>=<clone> ...` (every fork of
   the set; read-only). It prints one line per need and `MISSING` with what to do: **each fork's clone** (a scheduled
   session cannot attach a repository -- `add_repo` is refused there -- so a fork that is not in the task's repositories
   has no clone: stop now, before freezing, with one comment on the plan issue that names the repository and says to add
   it to the scheduled task's repositories; the plan issue stays open for the next run), the GitHub way it will use
   (GITHUB_TOKEN wins, then GH_TOKEN, both over REST; `gh` only when neither is set, so a broken `gh` login does not matter
   when a token is set), a JDK for each version (21 for 1.21.x, **25 for 26.x**: found at `JAVA_HOME_25`, `/usr/lib/jvm`,
   `/opt`, `~/.gradle/jdks`, ...), and for the client gates **`libEGL.so.1`** (the 26.3 client loads it: `libegl1`) and
   **`XDG_RUNTIME_DIR`** (unset in a cloud session: `export XDG_RUNTIME_DIR=$(mktemp -d) && chmod 700
   "$XDG_RUNTIME_DIR"`); on every kind of fork, its **`origin/HEAD`** (a WARNING, not missing: a fresh cloud clone may lack it, `git
   remote set-head origin -a`); on a version-branch fork, **the remote each version's upstream freeze names** (`upstream=<remote>/<branch>`: without it the gates stop
   with exit 19; it prints the fetch-only `git remote add`); on a single-version fork, **a remote fetching its authors'
   repository** as the set's `.github/weekly-plan.json` names it (`upstream`, read from the clones given, so pass the
   library's too), which `port-sync.py` merges from (missing, with the fetch-only `git remote add`). A shallow single-branch clone of a version branch has its other version branches
   read from origin by name (`git ls-remote`), so their JDK and libEGL are asked too. Install or set what it names that the session can (apt, an export, the git commands) and run it again; anything
   left missing other than a clone is reported in the first comment and the versions that need it wait.
1. **Freeze the input.** `python3 tools/known-issues.py read-plan --latest --slug <library owner/repo> --out
   round/frozen.json --decisions-dir round/decisions`. It reads GitHub through `GITHUB_TOKEN` when that is set (REST), else
   `GH_TOKEN`, else the `gh` command line -- so a `gh` whose own login is broken does not matter while `GITHUB_TOKEN` is
   set; it says on stderr which one it used. No open plan issue: stop and report. Per fork, `frozen.json` says whether to take the authors' commits (`take_authors`, and the exact
   `upstream_commit` the plan saw), which known issues are `selected`, the decisions with their choices, and the grade
   overrides; read-plan says per fork whether `take_authors` is the plan's default (the box found ticked), the owner's
   (unticked) or nothing to take, and a version-branch fork's entry records it as `take_authors_by` -- put it in the first
   comment. **These are the run's only inputs.** Nothing new enters the run except an S1 found during it; the
   authors' commits pushed after the plan wait for next week (merge `upstream_commit`, not the branch tip:
   Run step 2, "To merge one exact commit"). **Freeze at
   the deadline, not before:** read-plan run before the plan's deadline warns -- the plan workflow and the round are
   normally 24 hours apart, and an early freeze cuts the owner's window to tick and reply. Only when the owner asked to
   start now (on the plan issue, or in this session) add `--now`: `frozen.json` records it as `early_start`, and the first
   comment says so. Each
   grade override is applied first (the driver's `grades` item): `known-issues.py grade --repo <fork> <ID> --override <S>
   --reason "<reason> (plan #N)" --by <who>` on the head branch, committed alone. Post a first comment on the plan issue:
   the frozen selection, @owner. Then `round.py init` (above). **Dependency updates** (the driver's `setup` items, and
   on a version branch its `repin` items): each fork's `dep_bumps` in `frozen.json` (the boxes left ticked) are applied exactly once, at
   that version's setup and before its gates: run each entry's `command` (`layer-deps.py resolve --repo <checkout of
   neoforge-<mc>> --target <mc> --bump <g:m>=<coord>`) in the checkout of that version's branch and commit the changed
   `branch.properties` (a version branch) or `build.gradle` / `gradle.properties` (a single-version fork, or the port
   branch) as its own commit; on a version branch it goes in with the re-pins (step 4 below: what changes the build goes
   in first). An unticked one (`dep_kept`) stays as it is. After that no step re-picks a dependency: `fork-port.py`'s
   setup and every other resolve keep the pins, so `branch.properties` does not move mid-round.
2. **Order: the library first, then each dependent.** A dependent starts only once the library's versions it pins are
   green and released (Run step 7, "Don't exceed your dependency").
3. **Per fork**, from its entry in `frozen.json`; say which case it is. **The authors' code is taken only when the plan
   says so (`take_authors`), and a done version is reopened only for an S1.**
   - **A single-version fork:** when `take_authors`, Run steps 2-6 with the frozen `upstream_commit` (step 2's
     `port-sync.py ... --base <head> --commit <upstream_commit>`; merge, reconcile,
     port, gate, PR into the head), with step 6's merge and release run only after step 4 below (review and grading). Each selected known issue is fixed on the port branch. Nothing else changes for it. On the driver
     the merge is its `upstream` step and the reconcile and port its `authors` job ("A single-version fork on the
     driver", above).
   - **A version-branch fork with `head_cascade: true`** (the authors' box ticked, or the head has none of their code
     untaken): when `take_authors`, Run steps 2-6 with the frozen `upstream_commit` (when only the head's untaken code is
     to take and nothing is new upstream, the merge is empty: go on), step 6's merge only after step 4 below for the
     head's version (its review and grading), and the cascade after that merge. Then "A fork with version branches" from the head:
     write the default decisions' `patch` diffs into `round/decisions/<owner>__<repo>.decisions.json` (its step 2), then
     `cascade --repo DIR --apply-decisions round/decisions/<owner>__<repo>.decisions.json --gates ab` -- the head cascade
     `<head>..<newest>`; with no earlier `--apply` it runs that `--apply` first, then the answers. A `patch` left without
     a diff stays open (what `--apply` does with it) until you write it and run `--apply-decisions` again. A version
     the cascade leaves red (`cascade STOPPED at <v>: red after the merge`: the authors' new code meeting `<v>`'s API)
     is the head cascade job's to port, not the owner's: it runs the fixer the cascade names there (`fork-port.py
     --from-stage measure --budget 15 --gates ab`), commits, and runs the cascade again; it returns blocked only for a
     version still red after two such passes.
   - **A version-branch fork with `head_cascade: false`** (the owner unticked the box while the head has authors' code
     no version took): **no cascade from the head, and no `--apply-decisions`** -- read-plan wrote no decisions file,
     since every decision of the plan is about the head cascade (`cascade.not_applied`). Only the selected fixes move.
   - **The upstream freeze when the authors' code is not taken** (`take_authors: false`, either case): every version's
     freeze is behind the authors' branch on purpose. `fork-port.py` reads `round/frozen.json` from the round's working
     directory and records that drift in the version's `DRIFT.md` as left out by the plan instead of stopping (exit 19);
     pass `--untaken-ok round/frozen.json` to every `fork-branches.py cascade` and to a `fork-port.py` run started
     elsewhere. Never remove or rename the `authors` remote to get past the guard (a freeze that cannot be compared stops
     too), and never use `--accept-upstream-drift` in a round.
   - **Each selected known issue** (a version-branch fork, either case) goes where `frozen.json`'s `fixes` says: it is
     made at `at`, the **lowest version whose ledger status is `open`** (not the `lowest` field: an issue first seen at
     1.21.1 may be open only at 26.3), and carried up by the one cascade named there, never fixed version by version.
     **Check below `at` before fixing:** `python3 tools/known-issues.py lowest --repo DIR --ref origin/<head> <ID>` (git
     only, no checkout) greps the entry's `pattern` on every lower version branch the ledger leaves unset -- a hit is
     evidence, not proof (the same text can be right at another version): read the branch to confirm it; with no
     pattern, read the lower branches yourself (`git show origin/neoforge-<v>:<file>`). Confirmed lower: record it
     (`known-issues.py status <ID> --set <versions>=open --lowest <v>`), and fix it at the lowest version that has it
     and cascade from there when that reopens no done version (an S1, or versions that have work this round anyway;
     never onto the head when the head is not cascaded) -- otherwise the next plan fixes it there. On a version-branch
     set the driver does this itself (round.py init, then one `lowest` job per fork that confirms the candidates). Give
     a new entry a `--pattern` when its defect has a recognisable text. Where the fix is made:
     at a version branch, the fix on `neoforge-<at>` and `python3 tools/fork-branches.py cascade --repo DIR
     <at>..<newest>` (from a version branch, a cascade carries only what that branch took -- each merge's port commit is
     its merge-base with the head -- never the head's untaken code); at the head's own version with `head_cascade: true`,
     the fix on the head and the head cascade carries it; at the head's version with `head_cascade: false`, cut `fix/<ID>`
     from the lowest version's `port_taken` (`.port/<v>/branch.properties`), fix there, `cascade --repo DIR
     <head>..<newest> --port-ref fix/<ID>` (refused while `fix/<ID>` holds authors' code that version has not taken;
     `--upstream REMOTE/BRANCH` names their branch when the freeze's `upstream=` does not resolve), and merge `fix/<ID>`
     into the head too, by its own PR. Work the versions in ladder order, oldest affected first.
     `fork-branches.py untaken --repo DIR --upstream <remote>/<branch>` lists, at any point, the authors' code the head
     has that a version has not taken: after a `head_cascade: false` round it must still list what the plan listed.
4. **Per version, in ladder order**, once its code is in -- first `python3 tools/port-cost.py mark --fork <owner/repo>
   --version <v>` (appends the time to `round/marks.tsv`: the cost table splits this session's usage at these marks; the
   driver writes it, with `--phase`, at every item boundary).
   **Mark every change of the kind of work too**, with `--phase` (same fork and version; one more column in the file):
   `upstream` -- the authors' cascade and resolving its conflicts and decisions (step 3's head cascade is marked
   `--version <the lowest version it reaches> --phase upstream` when it starts), `backlog` -- the selected known-issue
   fixes, `review` -- the fresh reviews and the fixes they cause, `overhead` -- pins, gates, CI waits, records. The
   round-table then splits each version by phase and adds per-phase totals, so the cost of taking the authors' code
   can be compared week over week (marks without `--phase` keep the table as it was):
   - (a version-branch fork, once per round, before the round's first commit to it -- step 1's grade overrides included) `python3 tools/fork-branches.py ci-check --repo DIR`:
     a WARNING names a branch whose Port CI workflow is older than the kit or boots no jar of a variant its
     `.port/<v>/shipped-expect-<variant>.txt` expects -- CI green there proves nothing about that jar. Regenerate it
     (`fork-branches.py ci --repo DIR --all --modid ID --run`, pushed under `push-version-branches`; else ask) before
     trusting a green PR run, and name it in the closing comment. It names **the head branch** too when its workflow pins
     an older kit or its push trigger ignores no records: every ledger and README Ports-block commit the round makes there
     is then a full Port CI run (2026-10-19 round: nine, 8-14 minutes each, on a head still on a kit-v23 workflow --
     re-pins keep a workflow's kit, and nothing regenerated the head). Regenerate it first, before the round's first
     ledger commit: `fork-branches.py ci --repo DIR --head --run` (the head's own pins, variants and gates read
     back, only the kit moved to the newest), pushed under `push-version-branches`; else ask;
   - (a single-version fork, once per round, before its first commit) `python3 tools/port-ci.py check --repo DIR`: the
     same check of the port branch's workflow (an older kit -- new gates reach a fork only from the first kit that ships
     them -- or no records ignore), a WARNING that never blocks, with the command that regenerates it at the newest kit
     (its own options read back). The driver's `ci-check` item runs it on origin's head and its `setup` regenerates the
     workflow on the work branch, so it goes out with the work branch's PR (under `merge-fork-prs`; else that PR asks);
     name it in the closing comment;
   **The order is fixed: gates and the review, then the findings graded, then the merge, then the pre-release** (on a
   version branch the review starts from the cascade's green A/B gates and the one full run follows its fixes, below). The
   review is of the version's work branch (`keep-current/<date>-<v>`, or the version branch's checkout before it is
   pushed), never of what is already merged: a reviewer who reads a version after its PR merged reviews code players can
   already have.
   - gates A/B/C in full, the shipped-jar expectations included (`.port/<v>/shipped-expect-<variant>.txt`, and the
     jar `.port/<v>/shipped-jar.txt` names, on every kind of fork). **On a
     single-version fork, the cheapest correct sequence** -- one full gate pass, on the tree that merges: what changes
     the build (a re-pin, the ticked `dep_bumps`) is committed first; the review starts from a green compile and Gate A/B
     (`ci-gates.py --repo DIR --modid ID --base <upstream_commit> --gatec none`); after the review's fixes and their
     re-check, ONE pass of `ci-gates.py ... --env full` then `--env minimal` (the commands `round.py`'s head path runs);
     only a red run, or a jar-relevant change after it, needs another, and a ledger or README Ports-block commit needs
     none. **On a version branch, the cheapest correct
     sequence** -- one full run (~20 minutes) per version where the 2026-10-19 round ran two or three, every gate still
     green on the exact tree that merges:
     1. what changes the build goes in first: a library re-pin (`layer-deps.py resolve --port-release`, then
        `fork-branches.py ci` for the workflow) and the plan's ticked dependency bumps (`dep_bumps`, `resolve --bump`)
        before the version is gated, never after its gates;
     2. the cascade's `--gates ab` (compile, audits, Gate A/B) green is what the review starts from: the review needs no
        Gate C run before it;
     3. the review, the fixer's fixes of its `not-ok` rows and their re-check (below), committed;
     4. ONE full run on that final tree, on the version's checkout: `fork-port.py --repo DIR --target <v> --from-stage
        measure --gates full` (the compile re-measured, the audits and every gate run; setup, staging and the class moves
        not redone). It fixes nothing: a re-run from `measure` has no compile-loop budget unless given one (`--budget`),
        so a compile error stops it red with the tree as it was, for the fixer. Only a red run, or a jar-relevant change after it, needs another; a fix made after the review gets
        its changed rows re-checked like any other;
     5. a records-only commit after it -- GATES.md, REVIEW.md, COST.md, TEST-ITEMS.md, the ledger, the root README, the
        freeze or a `library.` record in branch.properties -- stales no gate and no review: run nothing again for it
        (`--check-done` confirms). Never `--gates auto` to save a run: once any Gate C is recorded it runs A/B only,
        which leaves a stale Gate C stale;
   - **one full review against the fixed rubric, before the merge**: on a version branch, `fork-port.py --repo DIR --target
     <v> --check-done` on the version's checkout writes REVIEW.md's `?` rows; on a single-version fork (#161 U5)
     `fork-branches.py check-done --repo DIR --target <mc> --base <the commit the change started from: the merge-base of
     origin's head and keep-current/<date>>` does the same in `.port/<mc>/REVIEW.md` -- it records that base (a new base
     starts the file afresh) and writes a row for every file `git diff --name-only <base>..HEAD` lists (an added file
     too, records and documents aside) and an `item` row for each hook of the base's behaviour inventory that is gone,
     plus TEST-ITEMS.md and INVENTORY-CHECK.json, so a single-version fork has the same done list in `.port/<mc>/` as a
     version branch (GATES.md certifying the commit, the rubric, STUBS.json when it has one, the inventory,
     TEST-ITEMS.md), instead of a review table in the PR body; a fresh reviewer agent fills `REVIEW.md` -- every row fork-versions
     "Done" item 4 lists, plus the behaviour-inventory account (item 7) -- then the fixer fixes the `not-ok` rows and
     **one re-check of the changed rows only** follows (the rows `check-done` reports stale since the review). That is
     the stopping rule: a second full review is not run; whatever the re-check still finds is graded (step 5).
     **The round must run where it can start subagents** -- one fresh reviewer per version, never the fixer reviewing
     its own work; a scheduled task's cloud session can. If this session cannot start one, stop at the review step for
     that version: comment on the plan issue (@owner) that the version is gated and waits for its review, go on only with
     what does not depend on it, and name it in the closing comment. Never skip the review, and never fill `REVIEW.md`
     yourself. Commit REVIEW.md on the work branch, so the PR carries it;
   - **a `not-ok` row blocks by its grade**: each `not-ok` row the re-check leaves is a finding, graded with
     `known-issues.py add` (step 5) at its lowest affected version. One graded **S1 or S2 at the version being worked**
     blocks that version's merge and its pre-release until it is fixed (and the re-check passes) or re-graded below S2
     with the rubric's inputs (`known-issues.py grade`, the reason recorded; an owner's `/grade` from the plan counts;
     a crash, kick, data loss or hang is never re-graded below S2 but by the owner -- one nothing calls yet is S2 with
     `--reach latent`, and it blocks like any S2);
     the versions above it wait too (they carry it by the cascade). An S3/S4 row is ledgered and does not block, and a
     `not-ok` row that is an issue already in the ledger at S3/S4 that the plan left unticked is not the fixer's to fix
     this round: only the round's selected fixes and an S1/S2 at the version block (the driver's fix job says so);
   - the merge of the version's PR (step 6's rule and "A fork with version branches" step 3), then its push run green
     (when it cuts a pre-release, awaited together with the release run: next item);
   - a pre-release through Port CI: dispatch the branch's workflow with `release=true` (never promote) -- only with
     `pre-release` in `allow`; without it, ask on the plan issue. Never before the review and its S1/S2 findings above.
     **Dispatch it right after the merge** (every kind of fork; for now a version-branch fork's HEAD is the exception:
     the driver waits for its push run at the merge, "A single-version fork on the driver" above) instead of after the
     push run alone: the release run
     checks out the merged commit and its release job needs that run's own full gates (full, minimal and every variant
     leg), so no pre-release is cut without a green full-gate run on exactly the released tree. Wait for the push run
     and the release run together, in one blocking command, and report both; a red push run is handled by step 6's rule
     (one re-run of a job that failed before any gate body; otherwise reported as blocking that pre-release's promotion),
     never ignored because the release run passed. That makes two serial CI waits per version (the PR's run, then the
     merge's push and release runs side by side) where the 2026-10-19 round had three (PR, push, then freeze push and
     release);
   - a library's version: BEFORE its merge, each dependent's Gate C against its candidate jar (the library pre-merge
     dependent gate above; red blocks the merge); after the release, `fork-branches.py library-record --repo LIB --target
     <v> --dependent DEP` once the dependent's same version passed Gate C on that exact released jar;
   - its cost: `fork-port.py`'s cost stage appends to `.port/<v>/COST.md` (tokens, dollars, wall time and a price tier,
     never a model name); work done outside it is appended with `python3 tools/port-cost.py <modid> --path DIR --target
     <v> --since <round start> --print >> .port/<v>/COST.md`, committed with the version's records.
   Post a progress comment on the plan issue as each version finishes (gates, review, the pre-release tag; the
   driver posts it when the version's release item ends).
5. **New findings.** Grade each with `known-issues.py add` (the rubric's inputs, `--lowest` its lowest affected
   version, `--file` each file it is in, `--pattern` a text whose presence means the defect is there, so a later
   `known-issues.py lowest` finds it on other branches with git alone); a WARNING that it may repeat an entry means read that entry first and record
   the finding there when it is the same defect. At a version that is already done, `known-issues.py reopens --version <v> --id <ID>` decides: only an S1
   reopens it (fixed at its lowest open version, only that fix cascaded, by step 3's rule: from a version branch, or
   `--port-ref fix/<ID>` at the head's version when the plan does not take the authors' code); anything else stays in the
   ledger with its lowest version and the round goes on. At the version being worked: S2 fixed now, S3 only if cheap
   and local, S4 ledgered.
   **On the driver (`tools/round.py`) the round ledgers every new finding itself:** the fixer and the re-checker write
   one `finding: {...}` line per defect the ledger does not have (the rubric's inputs, its lowest version, the fix
   commit when they fixed it), and the fork's ledger step adds each with `known-issues.py add` -- graded by the rubric,
   open from its lowest version up to where it was found, `fixed@` where its fix reached -- and commits it with the
   round's statuses on the head, the ledger file alone. Nothing is left uncommitted on a clone for the owner to find.
   The ledger step sets `fixed@<commit>` only at the versions whose branch carries that commit (a backlog job's
   `fixed:` line, a review or fix job's for a ledgered ID, or a commit of the round whose subject names the ID); a
   version the fix did not reach stays open, and the note names it. The next plan lists the entries added since the
   last plan issue under "New in the ledger since the last plan" (a plan with none is unchanged).
   **A round that stops** (a missing clone, a stop that needs the owner, no quota) **leaves the plan issue open**: say why in
   one comment; next Tuesday's plan carries its ticks and replies forward. Only step 6 closes it.
6. **Close.** On the plan issue, one closing comment with the outcome -- per fork: the merged PRs, the pre-releases cut,
   the ledger changes and the new findings with their grades -- and the **cost and time table**:
   `python3 tools/port-cost.py round-table --since <round start> --session-cwd <the round's working directory>` (this
   session's own requests, its subagents included, per fork and version at the `mark`s, with totals; the COST.md form,
   `--fork <owner/repo>=<checkout> ...`, measures only what fork-port's cost stage recorded, which a scheduled session's
   transcripts may not reach -- it read $0.00 for every version), split per phase where the round marked phases (it
   did: above), with the per-phase totals, and under it **the size of the authors' change** per version: commits
   (`git rev-list --count <old port_taken>..<new port_taken>` of that version's `.port/<v>/branch.properties`, or of the
   head's merge for a single-version fork), files and changed lines (`git diff --shortstat` over the same range), and
   how many files the cascade merged **mechanically** versus **by an agent** (`CASCADE-PLAN.json`: `mechanical` against
   the files of its `decisions`, plus any resolved by hand) -- so cost per authors' changed line and the mechanical
   share can be compared week over week -- and, before you close the issue, a **Lessons for
   the migrator** block in that same comment. It applies to every round, a single-version fork's and a version-branch
   fork's alike, and it is only text in the comment: no tool reads or writes anything for it, and **nothing is written
   to the migrator repository** (this session can only read it). Under the heading `### Lessons for the migrator`, a
   table `| version | symptom | fork commit | why it looks general |` with one row per candidate lesson the round met:
   a finding or fix whose cause looks like the Minecraft version, NeoForge, a library's new line or a shared helper or
   rename row, not this mod's own code (a crash, a silent behaviour change, a fix a compile loop made that changed what
   the code does). The symptom is what a porter would search for -- the error or log text, else the behaviour; the
   commit is the fix as `owner/repo@<sha>`; the last column says in a few words why any port to that version would
   meet it. On a version-branch set the close job also lists each file a fork marks `MV-AHEAD-OF-TEMPLATE` (its copy of
   a shared helper is ahead of the migrator's template): one row each, `ahead of MMVU's shared template: harvest`.
   A round that met none writes one row `| none | | | |`. A later session with write access to the migrator
   turns the rows into catalogue entries, starting from `python3 tools/propose-learnings.py --from-plan
   <owner/repo>#<N>` (a worklist; it writes nothing). Then close the issue (state completed). An issue the round
   closed is never carried forward.
7. **Afterwards:** set the ledger statuses on each head branch (`known-issues.py status <ID> --set <v>,<v>=fixed@<commit>`,
   or `--set <from>..<to>=fixed@<commit>`, for every version a fix reached, in one call per issue, committed alone), then refresh the README Ports block ("README Ports block" below).

## README Ports block

`python3 tools/known-issues.py render-ports --repo DIR --head neoforge-1.21.1 --fetch-releases --write` writes one row
per Minecraft version (its branch, newest pre-release or release, done state from `check-done`) and the links to the
known issues (the pinned issue with `--pinned-issue N`, and `.port/KNOWN-ISSUES.json` on the head branch) between
`<!-- ports:begin -->` and `<!-- ports:end -->` in the fork's root README; it never changes a byte outside the markers
(the authors' README text merges as before), and a root README edit runs no Port CI and stales no gate. When it runs:
on the **head branch** after every round and every release; on the fork's **default branch** (where visitors land) only
once the chain reaches its newest version, and after that on every release. Commit it alone. Every GitHub call it
makes is bounded (`MMVU_GH_TIMEOUT`, default 60 s) and a read falls back (a token's REST call to `gh`, `gh` to
anonymous REST): where `gh` hangs (its GraphQL blocked) it fails or answers in bounded time, never hangs the round.

## Setup: the weekly cycle on the user's accounts

Setup makes both halves, and re-running it recreates both and removes stale ones. Never create, edit or delete the
workflow or the round task by hand: a schedule change is a change to `tools/weekly-plan.py` or this section.

**Before step 1, per fork** (each answer is a flag of step 1; none is guessed):

- **Fetch first:** `git fetch origin` in each checkout, the library's on its head branch and up to date with origin's
  (`git pull --ff-only`). `install` commits on the local head branch, and with `--default-branch-too` it builds the
  default branch's commit on the LOCAL branch of that name when one exists (origin's only when none does), so a stale
  local default branch is brought up to origin's first (`git fetch origin <default>:<default>`) or deleted.
- **Which kind of fork:** `python3 tools/fork-kind.py detect --repo <checkout>`: `kind=single-version` or
  `kind=version-branches` (version branches win over a legacy layer branch). A layer at HEAD is refused (exit 2):
  stop. Say which kind each fork is; a single-version fork stays one.
- **The default branch:** `gh repo view <owner/repo> --json defaultBranchRef -q .defaultBranchRef.name` (or `git
  remote set-head origin -a` and `git symbolic-ref --short refs/remotes/origin/HEAD`). When it differs from the head
  branch, ask the user: make the head branch the default (Settings -> Branches), or pass `--default-branch-too`.
- **`ledger_issues`:** the fork's open issues whose body has a `fix-ledger` block (`<!-- fix-ledger:begin -->` ...
  `<!-- fix-ledger:end -->`, which `fix-ledger.py parse --body FILE` reads back) -- the GitHub search
  `repo:<owner/repo> is:issue is:open "fix-ledger" in:body` (`search_issues`, or `gh search issues`). Pass their
  numbers as `--ledger-issues <owner/repo>=N,N`; closed ones are left out. None found: no flag (the known-issues
  ledger is then the fork's only ledger, step 3.5).

1. **The plan workflow** (with the user's go-ahead, stop 1), on a checkout of the first fork's head branch:

       python3 tools/weekly-plan.py install --repo <library checkout> \
           --fork <owner/lib>@neoforge-1.21.1=<authors' repo URL>#<their branch> \
           --fork <owner/dep>@neoforge-1.21.1 \
           --plan-owner <login of the person running setup> \
           [--allow push-version-branches --allow pre-release --allow merge-fork-prs] \
           [--ledger-issues <owner/dep>=12,15] [--depends-on <owner/dep>=<owner/lib>] \
           [--replace-task "<old task name>"] \
           [--default-branch-too] [--keep-existing] [--trailer "<Key>: <value>" ...]

   One `--fork` per fork, the library first (a fork alone: one). A `--fork` without `=URL#BRANCH` takes the authors'
   upstream from the GitHub fork parent and its default branch (refused for a repository that is not a fork: name it).
   `--allow` is the owner's choice for this fork set, asked at stop 1 and passed as they answered; it is fork config,
   never a default -- with none the round asks before every push, pre-release and merge. `automerge` follows
   `merge-fork-prs`. A dependent depends on the library unless `--depends-on` says otherwise. Moving from an old fleet
   file: pass its `upstream` and `ledger_issues` values as these flags, once; after that the config is
   the only source. **Re-running setup rebuilds the config from the flags alone:** for each `allow`, `ledger_issues`,
   or `depends_on` the existing config holds and the new flags leave out, `install` warns on stderr and
   names the flag to re-pass; re-pass it, or add `--keep-existing` to keep every such value (a flag given still wins).
   Read the warnings before pushing. An agent passes its session's attribution trailers with `--trailer` (one per line), so the commit
   carries them; without it, amend the commit's message before pushing. It commits `.github/workflows/weekly-plan.yml`
   (this repository pinned at origin/main's commit, recorded as `migrator_sha`) and `.github/weekly-plan.json` in one
   `[skip ci]` commit, on the library's repository only (the dependents get nothing); an unchanged set commits
   nothing. It refuses an organization-owned fork without `--plan-owner`, and exits 3 when the head branch
   is not the repository's default branch (GitHub runs a schedule only from the default branch): ask the user to make
   the head branch the default, or rerun with `--default-branch-too`, which commits the same workflow file to the
   default branch too. Push what it names. The other forks must be public (the workflow reads them without a token).
2. **The round task:** list the scheduled tasks (`list_triggers`) first, then `python3 tools/weekly-plan.py round-task
   --repo <library checkout> --existing "<name>" ...` with every name the listing returned: the round runs on
   `tools/round.py` for every fork set (`--version-branches`, which older setups pass, is accepted and changes nothing).
   It prints the task as JSON.
   Its `replaces` holds exact task names, never a description: the round's own name, each `--replace-task` name, and
   each listed name that starts with "keep-current" and names a fork of this set. Delete a task only when its name is
   exactly one of them -- no match, no deletion -- then `create_trigger` with its `name`, `cron_expression`
   (`CRON_TZ=America/Los_Angeles <minute> 8 * * 3`, the minute jittered per fork set below 08:30), `create_new_session_on_fire:
   true`, `notifications` (push and email on), `initiation` and `prompt` (the fork set and the `allow` list in it),
   unchanged. The JSON also holds `model` -- the model to set in step 3, the most capable one priced in
   `tools/model-prices.tsv`, read from that file -- and `repositories`, the forks to attach in step 4. Say in one line that the round is Wednesday at `round.time` from `.github/weekly-plan.json`, Pacific.
   **When the owner defers the task** (they will create it later, or have it created another time): create nothing;
   write the `round-task` JSON to a file outside every fork checkout (the session's scratch directory, never a clone that
   a commit could pick it up from) and give its path, and put in the report the model they chose at stop 1 and
   the repositories to attach (`repositories` in the JSON); then stop. Do steps 3 to 5 when the task is created.
3. **Set the round's model; never leave it on the account default.** A task created without one runs on whatever the
   account defaults to (measured: a routine created without one ran on Sonnet, and the first dry run ran on the default
   because the field was empty), and `create_trigger` takes no model -- set it right after creating, with `update_trigger`
   `model`, to the `model` of the round-task JSON unless the user chose another at stop 1; when the owner creates the task
   themselves (it is deferred, or a connector cannot set it), tell them to set that model on it. Recommend Opus:
   the session's own model does all of this skill's judgement -- the merges, the reconcile (take theirs, keep or shrink
   ours, the A/B), the reviews it starts, the grades and every merge-on-green call. The mechanical work keeps its own
   models whatever the session runs on: the compile loop (`tools/file-loop.py`) climbs Haiku -> Sonnet -> Opus only on
   failure, `tools/port-upstream.py` and `tools/gate-loop.py` default to Sonnet, the visual review to Haiku. Say in one
   line which model was set. (Changing an existing task's model is the user's call: ask, then `update_trigger`.)
4. **Repositories on the task -- every fork of the set must be attached.** A scheduled session cannot add
   repositories (measured three times: `add_repo` never appears in one, or the session's permission check refuses it),
   and `create_trigger`/`update_trigger` have no repository field. So the setup checklist has one line per fork, and is
   not done until each is attached:
   - **Attach only what the round pushes to: the user's forks** (`repositories` in the JSON). This repository is
     public and only read: the prompt clones it itself; never ask the user to attach it.
   - **The user adds the forks.** Give them the exact link and steps: open https://claude.ai/code/routines, click the
     task by name, open the menu next to its name, choose **Edit**, then use the small **Select a repository** control
     at the bottom-left of the **Instructions** box (easy to miss: it is not a separate section of the form) to add each
     fork (`owner/repo`), and save. If they have the edit form open while you change the prompt with `update_trigger`,
     ask them to close and reopen it first, or saving the form writes the old prompt back. (From a local CLI,
     `/schedule` does this.)
   - **The prompt checks first, it does not wait:** its first step is `round-preflight.py`; a missing clone is named in
     one comment on the plan issue and the round stops before freezing anything, the plan issue left open.
5. **Check it.** Run the plan workflow once by hand (Actions -> Weekly plan -> Run workflow, or `gh workflow run
   weekly-plan.yml --repo <owner/lib> --ref neoforge-1.21.1`): it costs no model and must open the plan issue, assigned
   to the owner. Read the round task back (`get_trigger`): cron, a fresh session per fire, notifications and the model
   that was set. Do not fire the round to test it -- a fire is a whole round; it runs on Wednesday against the plan.
   The environment needs the build hosts in its allowed domains (`cloud/allowed-domains.txt`), and what the round's
   gates run on: JDK 21 and JDK 25 (every 26.x version builds and boots on 25), `libegl1` (the 26.3 client) and an
   `XDG_RUNTIME_DIR`, in the environment's setup script; `python3 tools/round-preflight.py --fork <owner/repo>=<clone> ...`
   says what is missing (the round runs it first).

## Evidence that this is needed (and the mistake it prevents)

Reconciling two forks with their authors' new versions, the first pass judged each issue as a whole: "the authors
fixed the disconnect, the rest is theirs" -- and kept two fixes in the dependent mod without asking whether the
library's new "clear the switch on logout" had covered one of them, while missing that a saved entity re-arms it on
every world load despite the authors' own comment saying it should only happen at spawn. Splitting the issue into
modes, crossing the dependency boundary and running the A/B is what turned that into decisions that hold.
