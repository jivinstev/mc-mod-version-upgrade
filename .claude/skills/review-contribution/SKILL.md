---
name: review-contribution
description: Review a pull request contributed to this migrator repository and decide whether it can be merged — IP (no mod code, jars, decompiler output or mod names), security (anything that downloads, executes or widens permissions), gate integrity (no weakened check), catalogue quality (every lesson findable from its symptom, evidenced, generalised) and scope. Produces one verdict — APPROVE, REQUEST CHANGES (with a message the contributing agent can act on directly) or REJECT. Use when asked to review, vet, check or triage a PR or branch for this repo, including the learnings PRs that tools/propose-learnings.py opens.
---

# Review a contribution

The repository is public and its value is the catalogue, so a review has two jobs that pull in
opposite directions: **refuse anything that publishes someone else's work or weakens a gate**, and
**make it easy for a good lesson to get in**. A contributor is often another Claude agent running
the migrate-mod skill, so a "request changes" must be something that agent can act on without a
human translating it.

## Step 1 — the mechanical pass (never skip it, never replace it with reading)

```bash
git fetch origin main pull/<N>/head:pr-<N>        # or the branch name
python3 tools/review-pr.py --base origin/main --head pr-<N>
```

It runs every gate on the PR's own tree in a temporary worktree (your checkout is untouched) and
reads the diff for what the gates cannot see. Two rules about its output:

- **BLOCKING findings are not negotiable in review.** The fix is the contributor's; you may explain
  it, never waive it. A gate that is wrong gets its own PR, with its own review.
- **It must have run with the name list.** If it reports `no forbidden-name list configured`, set
  `FORBIDDEN_NAMES_FILE` in `.env.local` (the list lives in the owner's private repository) and re-run.
  A review without the name check is not a review of the one thing this repository most needs to refuse.

## Step 2 — the judgement pass (what no script can decide)

Read the whole diff (`git diff origin/main...pr-<N>`). For each area it touches:

**Identity.** The name list only knows mods that were ported privately. Read every added line for a
mod the list could not know: a mod or modpack name, a mod id, a Java package root that is not
`net.minecraft`/`net.neoforged`/`com.mojang`/a published library, a distinctive entity/item/class name,
a jar name, a CurseForge/Modrinth id, a path from someone's workspace or home. Published *libraries*
(GeckoLib, JEI, Registrate, Curios, Mixin, …) and the decompilers may be named — they are how porters
search. Everything else is described ("a small MCreator food mod", "a ~750-file boss mod").

**Security.** For every added or changed script: what does it download, from where, and is it pinned by
sha1 and verified before use? Does it execute anything it fetched? Write outside the workspace, the
checkout or `$TMPDIR`? Read credentials or environment it does not need? For workflows: any new
permission, any `pull_request_target`, any step that can no longer fail? The mechanical pass flags the
patterns; you decide whether each flagged line is justified, and an unjustified one is REJECT, not a nit.

**Gates.** A change to `tools/check-*.py`, `tools/test-*.sh`, `tools/review-pr.py`, `VENDORED.tsv`,
`CODEOWNERS` or `.github/` is read line by line. The question for every removed line: *what can now get
through that could not before?* A gate may be sharpened, refactored or given a new case; it may not get
quietly narrower. Widening an allowlist needs the owner, by name, in the PR.

**Catalogue.** For each new or augmented entry:
- **Findable from the symptom?** The `**Error:**`/`**Runtime:**`/`**Symptom:**` text should be what a porter
  will actually paste or grep: the compiler message, the exception line, the API name. "It crashed" is not
  a symptom.
- **Evidenced?** A rule should say where it was seen and what it measured (a count, a version, an error
  code) — generalised, but concrete. A plausible rule with no evidence is how a catalogue fills with
  folklore.
- **Correct and not a duplicate?** Search the catalogue for the API name and the error text. If an entry
  already covers it, the right change is an `AUGMENT` to that entry, not a new number.
- **Placed right?** In the section for its axis (loader, version family, runtime, silent-resource, …).
- **Nothing lost.** The fidelity gate (run against the base census) settles this mechanically.

**A port to a new target version.** If the PR's port (its learnings commit names it) targeted a
Minecraft version that `SUPPORTED_VERSIONS.tsv` does not list as `tested`, check the evidence rule in
`SUPPORTED_VERSIONS.md`: the exact target, and Gate A + Gate B passing on it. With the evidence, add (or
extend) that version's `reported` row naming this PR, and promote it to `tested` when the bar there is
met. Without it, ask for it in REQUEST CHANGES. Never promote a beta loader to `tested`.

**Scope.** Does the PR do one thing? A learnings PR should touch `CATALOG.md` and the census only. A
tool PR should include its self-test. Unrelated changes get split out, not waved through.

## Step 3 — the verdict (exactly one)

**APPROVE** — no blocking finding, every attention item resolved, and you would be happy to be the
person who merged it. Say what you checked, in one short paragraph.

**REQUEST CHANGES** — fixable. Address the contributor directly, and make every item actionable on its
own: *where* (file and line or entry id), *what is wrong*, *what to do*. For an agent contributor, end
with the command that re-checks the fix. For example:

> Entry R27: the symptom says "it crashes on load"; replace it with the exception line from your log
> (`IllegalStateException: …`) so a porter can find it by pasting the error. Then re-run
> `python3 tools/propose-learnings.py --modid <modid> --dry-run` and push to the same branch.

**REJECT** — not fixable by revision: third-party code, a jar or decompiler output in the history
(it is published the moment it is pushed, so say so and tell the owner), a security problem that looks
deliberate, or a change whose purpose is to weaken a gate. Explain once, without arguing.

If an IP leak is in the PR's history, removing it in a new commit does not unpublish it: tell the owner
in the review, because the branch (and possibly the PR) has to be deleted, not fixed.

## Posting

Post the verdict as a single PR review (not scattered comments). End it with the attribution footer
if the repository asks for one. **Never merge or approve on the owner's behalf unless the owner asked
you to**; the verdict is a recommendation, and the owner decides.
