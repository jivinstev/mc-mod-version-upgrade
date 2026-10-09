# mc-mod-version-upgrade

**Install Minecraft mods, and port the ones that are stuck on an old version, by asking
[Claude Code](https://claude.com/claude-code) in plain words.**

```text
> Install Sodium
> Migrate <mod name> to Minecraft 26.2
```

The first finds the right build on Modrinth or CurseForge, resolves its dependencies, verifies the
download and puts it in your mods folder. The second ports a mod that has no build for your version:
it decompiles the old jar, rewrites it through a catalogue of ~540 known API changes, and only calls
it done once three test gates pass, the last of them a real Minecraft client.

## Two ways to run a port

**In Claude Code (recommended):** ask in plain words, e.g. `> Port <mod name> to 26.2`. Claude runs
the port script, and when a stage stops it explains the choices and asks you before doing anything by
hand. When the port finishes it delivers the jar and reports what the port cost.

**The script alone:** `python3 tools/port.py "<mod name or path to the jar>" --to 26.2` (or `--to
1.21.1`). There's no orchestrating session to pay for. When it stops, it prints what is left, what it
has spent and your choices. Rerun the same command to resume. It still needs the `claude` CLI logged
in, because the cheap fix workers it starts are Claude.

**Taking an existing port further:** `python3 tools/port.py --from-port mods/<modid> --to 26.2` starts
from a port you already finished (say, a 1.21.1 one) instead of a jar, and keeps its fixes.

Either way the port runs hop by hop (for example Forge 1.20.1 → NeoForge 1.21.1 → 26.2). Each hop
applies its recipe pack of known rewrites first, then sends what's left to small per-file workers
(Haiku first, Sonnet and Opus only as needed). The port must then pass the gates: a headless server,
behaviour tests written for the mod, a real client, and a screenshot review.

## Porting a mod in its own repository

When a mod's source is on GitHub under a licence that allows it, you can port it **in a fork** instead of
from its jar: `> Port my fork <github url> to 1.21.1` (the `port-fork` skill). The result is a branch in the
authors' own layout, `neoforge-<mc>`, with the smallest diff that works. The test harness stays outside their
tree. Then:

- **CI on the fork** runs the authors' own build and the same three gates on every push. It downloads a
  small, pinned "Port CI kit" from this repository's releases, not this whole repository.
- **A release** publishes the authors' own JARs as a pre-release, but only when every gate passes.
- **Defects the authors' original already has** are proved pre-existing (their branch, their commit), then
  either fixed in one commit each or recorded in the fork's known-defects list (`gatec-known.txt`), and filed as an issue on the fork
  either way -- never backported unvalidated. The offer and the handoff page list them. Turn Issues on in a
  new fork; GitHub starts them off.
- **An offer for the authors**: a compare link showing just the port (without our CI), the size and shape of
  the diff, what was verified, and draft words. Nothing is sent: contacting the authors is your call.
- **The ten manual tests that matter most**, in every offer: what no automated gate reaches (music, keys,
  client-to-server packets, bosses, screens, raids, worn items, custom recipes, structures, natural spawns),
  one kind each first, with steps that name real `/summon`, `/give` and `/locate` ids and what to do if one
  fails. A handoff page gathers every fork's install steps, checks and tests in one place.
- **How far the port is from what players run.** A fork is often cut from the authors' newest code, not their
  release. The offer says how many unreleased commits it sits on (the release is matched by its publish time on
  every registry the mod is on). When the gap is large, ship two branches: `neoforge-<mc>`, the port of their
  newest code, kept current by merging their new commits; and `neoforge-<mc>-release`, the port of their
  release, derived from the first instead of ported again (measured: about 30% cheaper, and still needing
  every gate), with its own tag series and install manifest.

Forge 1.20.1 → NeoForge 1.21.1 is proven on three MIT-licensed forks. 26.2 is wired but not yet proven.

## Skills: in Claude Code, or from the command line

Each job is a Claude Code skill, so inside `claude` (started in this folder) you can either ask in plain words
or type the skill's slash command. Each skill drives scripts in `tools/` that you can also run yourself,
with no Claude session.

| Skill | In Claude Code | From the command line |
|---|---|---|
| **install-mod**: install a mod and its dependencies from Modrinth or CurseForge | `> Install Sodium` or `/install-mod Sodium` | `python3 tools/mod-registry/modreg.py search --query "Sodium"`, then `versions`, `deps` and `download` (see [tools/mod-registry/README.md](tools/mod-registry/README.md)) |
| **install-mod**: install fork ports from GitHub, with their dependencies and NeoForge | `> Install these mods from owner/RepoA owner/RepoB` or `/install-mod owner/RepoA owner/RepoB` | `python3 tools/install-port.py owner/RepoA owner/RepoB [--with-optional | --with-untested] [--dry-run] [--replace] [--mods-dir DIR]`, then optionally `tools/boot-mods.sh DIR` to boot the folder headless first |
| **migrate-mod**: port a mod from its jar | `> Migrate <mod name> to 26.2` or `/migrate-mod <mod name> to 26.2` | `python3 tools/port.py "<mod name or jar>" --to 26.2` (rerun to resume; `--from-port mods/<modid>` to continue a finished port) |
| **port-fork**: port a mod in its own GitHub fork, with CI, releases and an offer | `> Port my fork <github url> to 1.21.1` or `/port-fork <github url>` | `python3 tools/port-upstream.py --repo DIR --modid ID` (the port), `tools/ci-gates.py` (gates as CI runs them, `--env full` or `minimal`), `tools/port-ci.py` (adds the CI; `--tag-suffix=-release` for a release branch), `tools/port-offer.py` (the offer, install manifest and manual tests; `--release` adds the distance from release), `tools/port-fleet.py --fleet FILE <step>` (every fork at once) |
| **port-fork**: the handoff | `> Prepare the handoff for my forks` | `tools/manual-tests.py --repo DIR` (the ten manual tests; per-port warnings from the work folder's `manual-tests.notes.json`), `tools/offer-page.py --mods FILE --out page.html` (one page for every fork) |
| **port-fork**: the authors' release vs. their newest code | `> How far is my fork from the authors' release?` / `> Make a release branch for my fork` / `> Bring my fork up to date with the authors` | `tools/port-provenance.py --repo DIR --base REF --registry modrinth:SLUG --registry curseforge:ID --mc VER --loader forge` (the gap), `tools/port-derive.py` (a release branch; `--resolve` finishes it with a model, at a stop), `tools/port-sync.py --repo DIR --port BRANCH --upstream origin/BRANCH` (merge the authors' new commits) |
| **review-contribution**: review a pull request to this repository | `> Review PR 42` or `/review-contribution 42` | `python3 tools/review-pr.py --base origin/main --head HEAD` (the mechanical checks; the judgement half is the skill) |

Each script prints its options with `--help`. Most also have `--self-check`, which runs their own tests.

Both routes run the same deterministic stage after each hop's rename tables and before any worker sees an
error (`tools/mechanical-hop.py`, called by `port.py` and `port-upstream.py`): on a Forge → 1.21.1 hop the
access transformer, Forge code shapes, SimpleChannel → payloads and Holder fixes; on a 26.2 hop the member
renames and the converters for GUI hooks, entity save/load, data attachments, changed hook signatures, tool tiers
and armour, render types and shaders, entity render state and GeckoLib. A converter added there reaches both
routes; `tools/test-port-tools.sh` fails if a route stops going through it. Two
gates guard bug classes found in shipped ports: a field-less network payload behind `StreamCodec.unit`
(`tools/audit-unit-codecs.py`), and data files the server silently rejected (a row of `tools/ci-gates.py`).

## Measured ports

Every port here was run from this repository alone:

| the mod | from → to | first port (whole session) | now: the port's workers¹ | gates passed |
|---|---|---|---|---|
| a small MCreator food mod, 46 files | NeoForge 1.21.4 → 1.21.1 | $6.88, 20 min | **$0.69** | server, behaviour, client, screenshots |
| a GeckoLib library, 53 files, 6 mixins | Forge 1.20.1 → NeoForge 1.21.1 | $30.31, 43 min | **$5.24** | server, behaviour, client, screenshots |
| a shield mod, 64 items, 45 files | Forge 1.20.1 → NeoForge 26.2 (two hops) | ≈ $30.91², 1.8 h | **$5.64**³ | server, client (4 phases), screenshots |

¹ What the per-file workers and gate checks cost, measured from their own logs. It leaves out the
session that drives the port, which the next round of full replays will measure. That cost should be
small, because the session now only starts the script and answers its stops.
² Estimated: Claude Code on a desktop records tokens, not dollars, so this one is its exact token
counts at list prices ([`tools/model-prices.tsv`](tools/model-prices.tsv)).
³ One command, both hops (`tools/port.py`): $5.40 of workers, plus $0.24 for an enchantment fix that Gate B now requires (its content census found the port had dropped three). An earlier run with hand fixes, before the script existed, cost $12.57. Details in [`docs/EVALS.md`](docs/EVALS.md).

Every port also sends back what it taught, written without the mod's name, as a pull request to the
catalogue: the first three contributed 31 lessons between them (22 new entries, 9 additions). Each
records its own cost in [`docs/port-costs.tsv`](docs/port-costs.tsv), so the next person can see what
a mod of that size is likely to take.

## Quick start: on your computer

```bash
git clone https://github.com/jivinstev/mc-mod-version-upgrade
cd mc-mod-version-upgrade && ./setup
claude
```

`./setup` finds your Minecraft, recommends an answer to every question, and ends by telling you what
to type. Installing needs Python 3. Porting is an add-on: say yes when `./setup` offers it, or run
`./setup --migrate` later; it needs Java 21 (and 25 for Minecraft 26.x).
Details: [docs/USING.md](docs/USING.md).

Setup also asks you to turn off **"Help improve Claude"** at
[claude.ai/settings/data-privacy-controls](https://claude.ai/settings/data-privacy-controls): Claude Code
reads what you work on here, including decompiled mod code. Setup can't change that setting, so it only
records it once you type `confirmed`.

### On Windows (experimental)

Native Windows, no WSL. Claude Code on Windows already needs Git for Windows, whose Git Bash runs
everything here. Install the prerequisites, then run setup from cmd or PowerShell:

```bat
winget install --id Git.Git
winget install --id Python.Python.3.12
winget install --id EclipseAdoptium.Temurin.21.JDK
git clone https://github.com/jivinstev/mc-mod-version-upgrade
cd mc-mod-version-upgrade
setup.cmd
claude
```

The Temurin JDK is only for porting. In Git Bash, `./setup` works the same as `setup.cmd`.
Python from python.org has no `python3` command, so setup adds one to Git Bash (and an `unzip`), and
tells Claude Code which Python to use. Open a new Git Bash window after the first run.

Native Windows has no Claude Code sandbox, so Claude asks before running commands. For porting, turn
on long paths if setup says they're off, and keep the workspace on the same drive as this folder.
The real-client test opens a Minecraft window on your desktop; nothing else is needed.

> **Experimental.** Windows support passes on GitHub's Windows runners (setup, every check, a full
> mod build and the real client), but it has not yet been confirmed on a real Windows PC. Please report
> anything that differs in [#32](https://github.com/jivinstev/mc-mod-version-upgrade/issues/32).

## Quick start: in the cloud

Nothing to install.

1. **Make the environment (once):** at [claude.ai/code](https://claude.ai/code), open the environment
   menu → **Add environment**. Name it `Minecraft modding`, then:
   - **Network access:** Custom. Tick **Also include default list of common package managers**.
     Paste into **Allowed domains**:
     ```text
     maven.neoforged.net
     *.minecraft.net
     *.mojang.com
     api.modrinth.com
     cdn.modrinth.com
     api.curseforge.com
     *.forgecdn.net
     maven.parchmentmc.org
     maven.fabricmc.net
     maven.blamejared.com
     maven.ithundxr.dev
     dl.cloudsmith.io
     thedarkcolour.github.io
     packages.adoptium.net
     ```
   - **Setup script:** leave it empty.
   - **Environment variables (optional):** `CURSEFORGE_API_KEY=<your key>`. To get a free key,
     sign in at [console.curseforge.com](https://console.curseforge.com/) and open **API keys**.
     Without it, mods that are only on CurseForge can't be found; everything on Modrinth still works.
2. **Start a session** on this repo (or your fork), in that environment. It opens straight away.
   Type `run ./setup --yes` (`--yes` is needed: Claude runs it without a terminal to answer
   questions). It sets up porting, checks every host and installs the two missing tools (a virtual
   screen and Java 25) the first time, which takes a minute or two. It ends with **Ready.**; then
   ask for a port in plain words, as above.

In the cloud you can port and test, but there's no Minecraft to play it in, and the session's files
are deleted when it ends. So when a port finishes, **ask Claude to send you the jar**: it arrives as a
download in the Claude app. Then put it in your Minecraft `mods` folder at home.

## Ported mods are for your own machine

Many mods are "all rights reserved": don't publish or redistribute a ported jar without the original
author's permission. The MIT licence covers this tool, never the mods it ports.

## More

| | |
|---|---|
| [docs/USING.md](docs/USING.md) | Installing vs. porting, where ports go, prerequisites |
| [CATALOG.md](CATALOG.md) | The ~540 migration lessons: pattern → error → fix |
| [SUPPORTED_VERSIONS.md](SUPPORTED_VERSIONS.md) | Which Minecraft versions are tested |
| [docs/EVALS.md](docs/EVALS.md) | How much of a port the catalogue and its recipes cover, measured per port profile |
| [CONTRIBUTING.md](CONTRIBUTING.md) | The safety gates, and sending back what your port taught |

## Licence

MIT (see [`LICENSE`](LICENSE)); vendored files carry their own headers, and the decompilers are fetched
under their own licences. Details in [CONTRIBUTING.md](CONTRIBUTING.md#licence).
