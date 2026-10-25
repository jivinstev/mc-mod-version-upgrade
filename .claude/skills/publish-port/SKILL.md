---
name: publish-port
description: Publish a fork port as an unofficial community version on Modrinth and CurseForge -- set up the two projects (a generated page with the unofficial label, credit, links and licence; the config the fork's CI reads), publish a promoted GitHub Release to both with curated notes, and wire it so that promoting a release publishes it. Refuses without the authors' permission on record, for a pre-release, for a build with a crash/kick/hang still open in its fix ledger, or before a dependency is published. Use when the user asks to publish, release or put a fork port on Modrinth or CurseForge, to set up those projects, or to make promotion publish automatically.
---

# Publish a fork port on Modrinth and CurseForge

**Which fork:** a single-version fork is the default, and Parts 1-3 below are exactly it. A fork of
**version branches** (the default for a multi-version fork: `neoforge-1.21.3`, `neoforge-1.21.4`, ..., each with
`.port/<version>/`) is published the same way, one version at a time: every version branch is a single-version port at
its own Minecraft version, with its own CI, its own release (tag `<mod>-<ver>-mc<version>`) and Parts 1-3 run on that
branch ("Version branches" below). The removed `.multiversion/` layer route is not served: `port-publish.py` refuses a
checkout with the layer at HEAD (exit 2, naming the last migrator commit that supports it; fork-versions, "A fork still
on the layer"), and a config's old `layer` section is ignored. A single-version fork's output and API calls are compared
with a recorded golden (`tools/test-port-publish.py`).

The step after `port-fork` and `keep-current`: a fork whose release a person has played and promoted goes onto
the platforms players install from. Tool: `tools/port-publish.py`. The upload itself always runs in the fork's
GitHub Actions (the `publish` job `tools/port-ci.py` generates): the platform tokens are repository secrets, never
seen by a chat session, and CurseForge's upload host is not reachable from a cloud session anyway.

## Stops -- ask the user

1. **Permission.** A permissive licence makes redistribution legal; the platforms and courtesy still want the
   authors' agreement. Ask for the link where they gave it (an issue comment, a message). No link, no project and
   no upload -- `port-publish.py` refuses, whatever the licence says. Read the link yourself: it must name the
   mods being published and the platforms, or come close enough that you can quote it.
2. **Secrets and CurseForge projects** are the user's to create. Give them exact links, per fork:
   - Modrinth token: https://modrinth.com/settings/pats -- scopes **Create projects, Read projects, Write projects,
     Create versions, Read versions** and nothing else (no Write/Delete versions: uploading never needs them). One
     token serves every fork.
   - CurseForge token: https://authors.curseforge.com/#/settings/api-tokens -- one token serves every fork.
   - Each fork's secret form: `https://github.com/<owner>/<fork>/settings/secrets/actions/new`, names
     `MODRINTH_TOKEN` and `CURSEFORGE_TOKEN`. Tell them never to paste a token into the chat.
   - CurseForge has no API to create a project: give them the fields `setup` printed (name, summary, the
     description file, licence, source and issues links, logo, categories) and ask for the project ID back, and the project's public URL (CurseForge makes the slug from the name).
     The CurseForge name must not contain game version numbers -- moderation refuses it (`<name> - Community Port
     (NeoForge)`, as setup prints it; the Modrinth title keeps `(<mc>+)`). A rename changes the slug: update
     `curseforge.slug`, and every port that depends on this one reads it from here.
3. **Never**: publish a pre-release, upload from the chat session, post on the authors' repository (the authors
   may invite a link in their issue: offer to draft it, the user posts it), weaken a gate to get a build out.

## Public text speaks well of the original

A community port lives on the authors' goodwill. **Its project page and changelogs never list defects of the
original, link the fork's bug issues, or say "fixed", "bug", "crash" or "pre-existing"** -- on a page players read,
that is advertising someone else's bugs next to their name. The fix history stays where it belongs: the fork's
GitHub issues and fix ledgers, which still gate every publish. What a page may say about the port is how it
**differs** for a player: the Minecraft version and loader always, and a behaviour difference only if a player would
notice it (`--difference "..."`). A fix a player cannot tell from correct behaviour is not a difference.

**How to word a difference -- low-key, and sized right:**
- One line per difference, as a delta: what is different *in this version*, not what is wrong with the original.
- Give its scale, so nobody over-reads it: which feature it touches (a debug item, an experimental mode, a config
  default) and, when true, that most players won't notice.
- At most a short, neutral reason ("an experimental feature that didn't play well with some other mods on
  1.21.1"). No blame, no "bug", no severity, no history.
- Be specific rather than vague. "Minor differences, see issue N" sounds more alarming than the plain fact, and
  sends readers to an issue written for maintainers.
- Example: "The item's lockdown mode and the second test object don't turn on the library's global
  "protect everything" setting in this version. That's an experimental feature that didn't play well with some
  other mods on 1.21.1."

The user reviews the wording of each difference before it goes on a page; offer a draft in this style. The tool enforces this:
`port-publish.py` refuses to sync a page or upload notes that name a defect or link a numbered issue (the authors'
own words -- their description and credits -- and the permission link are exempt).

## Part 1: set up

0. **Guard the names**: `python3 tools/guard-names.py ensure --mod-repo <fork clone>` plus one `--mod-repo` per fork it depends on, before
   anything is written or committed here.
1. `port-publish.py setup --repo <fork clone> --permission <url> --ledger-issue owner/fork#n ...` writes
   `.github/port-publish.json` and `.github/port-publish/description.md` from what the port already has:
   - the mod's own metadata (name, authors, credits, logo, licence, its required/optional dependencies);
   - the authors' own Modrinth project (summary, categories, which versions it covers);
   - the fork's newest release (which jar each platform gets: `_mr` to Modrinth and `_cf` to CurseForge when the
     build makes variants, the only jar otherwise). Each platform's jar should be one CI tested: a fork with
     variants has `port-ci.py --variant-jar` per platform glob (its `shipped jars` job), and a dependent of one has
     `--dep-jar-variant` for the platform asset its `--dep-jar` does not pin (port-fork skill, step 6);
   - `--ledger-issue`: every issue carrying a fix ledger that covers this mod's builds, including a shared one in a
     dependency's fork. They gate publishing; they are never shown on the page;
   - `--difference`: each difference from the original a player would notice (see above). Usually none beyond the
     loader and version.
   A dependency that is another fork port (`"port": "owner/fork"`) is linked to THAT fork's community project.
2. Read both files. The page must say "unofficial", credit and link the original and its authors, keep the
   licence and copyright, and say how the port differs -- CurseForge's moderation asks for all of it. The config
   also carries Modrinth's `environment` (default `client_and_server`; Modrinth keeps it per version, and every
   upload sends it) and `disclosures` (mandatory on Modrinth where they apply): setup always writes **derivative
   work** (the original, linked) and **AI-generated content** (code and text: the port's changes and the page).
   Show the user both disclosure notes and ask whether any other applies -- telemetry, system interactions
   (outside the game), paid features, advertising, photosensitivity -- and add it to `disclosures` before setup's PR.
3. Commit them on a work branch and open a PR into the fork's port branch (with keep-current's automerge rules),
   together with the Port CI regenerated by `port-ci.py`. **Do not pass `--kit-version`**: the default is the
   newest published kit, and kits are numbered by the release workflow, not by you -- a hand-picked number can
   name an older kit that predates `port-publish.py` (it happened: `publish_setup` then fails with "can't open
   file .port-tools/tools/port-publish.py"). Check the pinned kit has it:
   `curl -sL <kit url> | tar -tz | grep port-publish`.
4. Modrinth: once merged, dispatch the fork's Port CI with `publish_setup = true`. It creates the **draft**
   project, or, when it exists, updates its title, summary and page from the fork's files (so a page edit is a PR
   plus one dispatch). CurseForge: the user creates it on the website; put the
   project ID into `curseforge.project_id` and the slug from its public URL
   (`curseforge.com/minecraft/mc-mods/<slug>`) into `curseforge.slug` in a small PR -- `setup` only guesses the slug,
   and the release notes link the uploaded file through it.

## Part 2: publish a promoted release

Dispatch Port CI with `publish = <tag>`. The job refuses, and uploads nothing, unless:
- the config has a permission link;
- the tag is a full Release published by the fork's own CI, built from the port branch;
- every listed fix ledger has a column naming this tag (a column can be `<lib tag> + <mod tag>`) and no S1 mode is
  open in it -- `fix-ledger.py check`. A tag no column names is refused too: a check against nothing checks nothing;
- a dependency that is another fork port already has its pinned release published on that platform (so publish
  the library first). "Pinned" means the dependency the release was promoted with, as the install manifest records
  it when it describes this tag -- not the workflow's current pin, which keep-current moves to a newer pre-release
  (so an overnight re-pin never blocks publishing a release that was played with the previous one).
It is idempotent: what is already published (a marker it appends to the release notes) is skipped. The notes
uploaded say which version of the original it is based on, any `differences`, who played it, where to report
problems (the fork, never the original author) and the permission link -- never the fixes it carries.

**Known issues** (a fork with `.port/KNOWN-ISSUES.json` on its head branch, `tools/known-issues.py`): the notes'
known-issues section comes from the ledger -- `known-issues.py release-notes --repo <fork> --ref <head branch>
--version <mc> --tag <tag> [--platform mr|cf]` lists what is open for that version at the tag, S1-S3 (with
`--platform`, only what affects that platform's jar). **Publishing judges each platform by the ledger itself**:
`port-publish.py publish` runs `known-issues.py blocks-publish` for each platform it is asked for (the head branch's
ledger: on a version branch the one `port_branch` in `.port/<mc>/branch.properties` names) and uploads only the
platforms with no open S1 at that Minecraft version; a blocked one is **held back** with a warning naming the S1s that
hold it (an issue scoped to `"platforms": ["cf"]` holds back CurseForge alone, and Modrinth still goes), and a run where
every platform asked is held back fails. `--platform mr` or `--platform cf` (default both) publishes one platform alone
-- a later run for the held-back one, once its S1 is fixed in a newer build, or by hand. A ledger that cannot be read
holds every platform back. The fix ledgers above are still checked as before, and a fork with no
`.port/KNOWN-ISSUES.json` publishes as it always did.

**Expect the first run against an old release to be refused** if its ledger shows an open S1: that is the gate
working, and the answer is to promote and publish the build that fixes it, not to edit the ledger.

**A library and the ports that need it -- the order that works:**
1. **Gate and play what each platform gets.** When the build makes a jar per platform (`_mr` for Modrinth, `_cf` for
   CurseForge, built by different tasks), Port CI must boot every one as shipped (`port-ci.py --variant-jar`, and
   `--dep-jar-variant` on each port that depends on it), and the person plays the jar each platform will receive --
   a `_cf` jar nobody launched can crash every start while the `_mr` jar is fine. The dev-classpath gates cannot see
   that (a ModLauncher service in the jar is only discovered in a real launch).
2. **Promote the library first, then the port that needs it** (or both together), each with its own column in every
   ledger it is checked against (a column carried from the previous tag cites the diff between the two tags). Don't
   exceed your dependency: the port MAY cut pre-releases pinned to the library's pre-release -- the pair is what gets
   played -- but it is never promoted or published while a library release it pins is still a pre-release
   (`port-ci.py promote` refuses unless `--with-dependency owner/lib` says the library is promoted alongside). When the person
   played the port with a sibling's pre-release, that sibling is promoted too, with the same note -- otherwise the
   port's publish is refused because its dependency is not published.
3. **CurseForge waits for approval.** A file on CurseForge cannot name a project that is not approved yet
   (errorCode 1018 "Invalid slug in project relations ... does not exist, is not accessible"). A new library's
   first CurseForge file puts its project into moderation; publish the dependent port to CurseForge only after the
   library's project is approved -- re-run `publish = <tag>`: what is already on Modrinth is skipped (its version
   number is found there), so only CurseForge uploads. Modrinth has no such wait.
4. **CurseForge reviews each file, and a library's can take days** (a coremod or network code goes to manual
   review). The dependent's project relation names the library's *project*, not a file, so a dependent file approved
   first would be live while no library file for that Minecraft version is, and a player's game would not load. Set
   `"manual_release": true` under `curseforge` in the DEPENDENT's config: its files then go through review in
   parallel with the library's but are uploaded held (`isMarkedForManualRelease`) -- approved, not released. Tell the
   user each held file and its library file; once the library's file for that version is live, the user clicks
   Release on the dependent's file page (CurseForge has no API to release it). Modrinth reviews projects, not
   versions: nothing to hold there.

**A publish that stops half-way is re-run, not repaired by hand:** Modrinth uploads first; when CurseForge then fails,
fix the cause (often a new CurseForge requirement -- the error names it) and dispatch `publish = <tag>` again.

## Part 3: publish on promotion

Nothing to do once Part 1 is merged: the `publish` job runs `needs: promote`, in the same run as a
`port-ci.py promote` -- a release edited with the workflow's own token starts no other workflow, so a separate
"on release" workflow would never fire for it -- and also on a release promoted by hand in the GitHub UI.
`"auto": false` in the config turns this off (then only `publish = <tag>` publishes).

**keep-current must name ledger columns after release tags.** When its PR is merged and the release run has cut
the pre-release, rename that reconcile's column (`keep-current/<date>`) to the tag, joined with ` + ` for a shared
ledger. Publishing judges exactly that column.

## Version branches: one release per version

Each version branch publishes its own promoted release through Parts 1-3, unchanged: the config
(`.github/port-publish.json`, made once on the port branch) reaches every version branch with the cascade, so all versions
go to the same Modrinth and CurseForge projects; the tag's `-mc<version>` gives each upload its game version and its
version number `<ver>+<version>`. Dispatch Port CI **on that version's branch** (ref `neoforge-<version>`) with
`publish = <tag>`: "built from the branch" is judged against the checked-out branch, so a version's tag must be on its own
branch. A version's ledger column names its own tag; a port dependency must have published its release for the same
Minecraft version first (the version branch's workflow pins that release, `fork-branches.py ci`). Promote only a version
that is done (fork-versions "Done") and has been played: run `fork-port.py --check-done` on that branch yourself first, as
neither `port-ci.py promote` nor publishing runs the done check; their only done-related refusal is a build pinned to a
library pre-release that was not done.

## After the first publish

Modrinth drafts need at least one version, then the user submits them for review on the website. Before that,
the project's publishing checklist should show nothing required: `publish_setup` sets the title, summary, page and
disclosures, and each upload sets the version's environment. What stays the user's: **Submit for review** (a person
puts it in front of the moderators), and the optional **gallery image** (a screenshot from their own play -- never
an AI image, which Modrinth's rules forbid). A version uploaded by a kit older than the one that sends the
environment shows "Select an environment": the token cannot edit versions (no Write versions scope), so the user
sets it once in the version's settings, `client_and_server` for a content mod. CurseForge reviews a new project after
its first file. Link both platform pages from the fork's README and from the authors' issue if they asked for it
(the user posts).
