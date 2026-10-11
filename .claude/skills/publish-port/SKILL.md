---
name: publish-port
description: Publish a fork port as an unofficial community version on Modrinth and CurseForge -- set up the two projects (a generated page with the unofficial label, credit, links and licence; the config the fork's CI reads), publish a promoted GitHub Release to both with curated notes, and wire it so that promoting a release publishes it. Refuses without the authors' permission on record, for a pre-release, for a build with a crash/kick/hang still open in its fix ledger, or before a dependency is published. Use when the user asks to publish, release or put a fork port on Modrinth or CurseForge, to set up those projects, or to make promotion publish automatically.
---

# Publish a fork port on Modrinth and CurseForge

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
     description file, licence, source and issues links, logo, categories) and ask for the project ID back.
3. **Never**: publish a pre-release, upload from the chat session, post on the authors' repository (the authors
   may invite a link in their issue: offer to draft it, the user posts it), weaken a gate to get a build out.

## Public text speaks well of the original

A community port lives on the authors' goodwill. **Its project page and changelogs never list defects of the
original, link the fork's bug issues, or say "fixed", "bug", "crash" or "pre-existing"** -- on a page players read,
that is advertising someone else's bugs next to their name. The fix history stays where it belongs: the fork's
GitHub issues and fix ledgers, which still gate every publish. What a page may say about the port is how it
**differs** for a player: the Minecraft version and loader always, and a behaviour difference only if a player would
notice it (`--difference "..."`, stated neutrally -- what happens here, what happens in the original). A fix a
player cannot tell from correct behaviour is not a difference. The tool enforces this:
`port-publish.py` refuses to sync a page or upload notes that name a defect or link a numbered issue (the authors'
own words -- their description and credits -- and the permission link are exempt).

## Part 1: set up

1. `port-publish.py setup --repo <fork clone> --permission <url> --ledger-issue owner/fork#n ...` writes
   `.github/port-publish.json` and `.github/port-publish/description.md` from what the port already has:
   - the mod's own metadata (name, authors, credits, logo, licence, its required/optional dependencies);
   - the authors' own Modrinth project (summary, categories, which versions it covers);
   - the fork's newest release (which jar each platform gets: `_mr` to Modrinth and `_cf` to CurseForge when the
     build makes variants, the only jar otherwise);
   - `--ledger-issue`: every issue carrying a fix ledger that covers this mod's builds, including a shared one in a
     dependency's fork. They gate publishing; they are never shown on the page;
   - `--difference`: each difference from the original a player would notice (see above). Usually none beyond the
     loader and version.
   A dependency that is another fork port (`"port": "owner/fork"`) is linked to THAT fork's community project.
2. Read both files. The page must say "unofficial", credit and link the original and its authors, keep the
   licence and copyright, and say how the port differs -- CurseForge's moderation asks for all of it.
3. Commit them on a work branch and open a PR into the fork's port branch (with keep-current's automerge rules),
   together with the Port CI regenerated by `port-ci.py`. **Do not pass `--kit-version`**: the default is the
   newest published kit, and kits are numbered by the release workflow, not by you -- a hand-picked number can
   name an older kit that predates `port-publish.py` (it happened: `publish_setup` then fails with "can't open
   file .port-tools/tools/port-publish.py"). Check the pinned kit has it:
   `curl -sL <kit url> | tar -tz | grep port-publish`.
4. Modrinth: once merged, dispatch the fork's Port CI with `publish_setup = true`. It creates the **draft**
   project, or, when it exists, updates its title, summary and page from the fork's files (so a page edit is a PR
   plus one dispatch). CurseForge: the user creates it on the website; put the
   project ID into `curseforge.project_id` in a small PR.

## Part 2: publish a promoted release

Dispatch Port CI with `publish = <tag>`. The job refuses, and uploads nothing, unless:
- the config has a permission link;
- the tag is a full Release published by the fork's own CI, built from the port branch;
- every listed fix ledger has a column naming this tag (a column can be `<lib tag> + <mod tag>`) and no S1 mode is
  open in it -- `fix-ledger.py check`. A tag no column names is refused too: a check against nothing checks nothing;
- a dependency that is another fork port already has its pinned release published on that platform (so publish
  the library first).
It is idempotent: what is already published (a marker it appends to the release notes) is skipped. The notes
uploaded say which version of the original it is based on, any `differences`, who played it, where to report
problems (the fork, never the original author) and the permission link -- never the fixes it carries.

**Expect the first run against an old release to be refused** if its ledger shows an open S1: that is the gate
working, and the answer is to promote and publish the build that fixes it, not to edit the ledger.

## Part 3: publish on promotion

Nothing to do once Part 1 is merged: the `publish` job runs `needs: promote`, in the same run as a
`port-ci.py promote` -- a release edited with the workflow's own token starts no other workflow, so a separate
"on release" workflow would never fire for it -- and also on a release promoted by hand in the GitHub UI.
`"auto": false` in the config turns this off (then only `publish = <tag>` publishes).

**keep-current must name ledger columns after release tags.** When its PR is merged and the release run has cut
the pre-release, rename that reconcile's column (`keep-current/<date>`) to the tag, joined with ` + ` for a shared
ledger. Publishing judges exactly that column.

## After the first publish

Modrinth drafts need at least one version, then the user submits them for review on the website. Link both
platform pages from the fork's README and from the authors' issue if they asked for it (the user posts).
