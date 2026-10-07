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

Either way the port runs hop by hop (for example Forge 1.20.1 → NeoForge 1.21.1 → 26.2). Each hop
applies its recipe pack of known rewrites first, then sends what's left to small per-file workers
(Haiku first, Sonnet and Opus only as needed). The port must then pass the gates: a headless server,
behaviour tests written for the mod, a real client, and a screenshot review.

## Measured ports

Every port here was run from this repository alone:

| the mod | from → to | first port (whole session) | now: the port's workers¹ | gates passed |
|---|---|---|---|---|
| a small MCreator food mod, 46 files | NeoForge 1.21.4 → 1.21.1 | $6.88, 20 min | **$0.69** | server, behaviour, client, screenshots |
| a GeckoLib library, 53 files, 6 mixins | Forge 1.20.1 → NeoForge 1.21.1 | $30.31, 43 min | **$5.24** | server, behaviour, client, screenshots |
| a shield mod, 64 items, 45 files | Forge 1.20.1 → NeoForge 26.2 | ≈ $30.91², 1.8 h | **$12.57**³ | server, client (4 phases), screenshots |

¹ What the per-file workers and gate checks cost, measured from their own logs. It leaves out the
session that drives the port, which the next round of full replays will measure. That cost should be
small, because the session now only starts the script and answers its stops.
² Estimated: Claude Code on a desktop records tokens, not dollars, so this one is its exact token
counts at list prices ([`tools/model-prices.tsv`](tools/model-prices.tsv)).
³ An early run, before the one-command script existed. It needed some hand fixes in its session,
which this figure leaves out. The fixes it prompted are now built in. Details in
[`docs/EVALS.md`](docs/EVALS.md).

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
