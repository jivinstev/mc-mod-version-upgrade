# mc-mod-version-upgrade

> ⚠️ **Under construction.** Not yet published; please don't rely on it until this notice goes away.

**Install Minecraft mods, and port the ones stuck on an old version, by asking
[Claude Code](https://claude.com/claude-code) in plain words.**

```text
> Install Sodium
> Migrate <mod name> to Minecraft 26.2
```

The first finds the right build on Modrinth or CurseForge, resolves its dependencies, verifies the
download and puts it in your mods folder. The second ports a mod that has no build for your version:
it decompiles the old jar, rewrites it through a catalogue of ~540 known API changes, and only calls
it done once three test gates pass, the last of them a real Minecraft client.

## Measured ports

Every port here was run blind, from this repository alone, by a fresh Claude Code session:

| the mod | from → to | time | cost | gates passed |
|---|---|---|---|---|
| a small MCreator food mod, 46 files | NeoForge 1.21.4 → 1.21.1 | 20 min | $6.88 | unit, server, client |
| a GeckoLib library, 53 files, 6 mixins | Forge 1.20.1 → NeoForge 1.21.1 | 43 min | $30.31 | unit, server, client |
| a shield mod, 64 items, 45 files | Forge 1.20.1 → NeoForge 26.2 | 1.8 h | not recorded¹ | unit, server, client (4 phases) |

Every port also sends back what it taught, written without the mod's name, as a pull request to the
catalogue: those three contributed 31 lessons between them (22 new entries, 9 additions). Each records its own cost in
[`docs/port-costs.tsv`](docs/port-costs.tsv), so the next person can see what a mod of that size is
likely to take. ¹ Claude Code on a desktop records tokens, not dollars: 221k output tokens.

## Quick start: on your computer

```bash
git clone https://github.com/jivinstev/mc-mod-version-upgrade
cd mc-mod-version-upgrade && ./setup
claude
```

`./setup` finds your Minecraft, recommends an answer to every question, and ends by telling you what
to type. Installing needs Python 3; porting also needs Java 21 (and 25 for Minecraft 26.x).
Details: [docs/USING.md](docs/USING.md).

## Quick start: in the cloud

Nothing to install. One environment works for this repo and for
[mc-buddy-builder](https://github.com/jivinstev/mc-buddy-builder).

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
2. **Start a session** on this repo (or your fork), in that environment. It opens straight away.
   Type `run ./setup`: it checks every host and installs the two missing tools (a virtual screen and
   Java 25) the first time, which takes a minute or two.

In the cloud you can port and test, but there's no Minecraft to install into: take the finished jar
home.

## Ported mods are for your own machine

Many mods are "all rights reserved": don't publish or redistribute a ported jar without the original
author's permission. The MIT licence covers this tool, never the mods it ports.

## More

| | |
|---|---|
| [docs/USING.md](docs/USING.md) | Installing vs. porting, where ports go, prerequisites |
| [CATALOG.md](CATALOG.md) | The ~540 migration lessons: pattern → error → fix |
| [SUPPORTED_VERSIONS.md](SUPPORTED_VERSIONS.md) | Which Minecraft versions are tested |
| [CONTRIBUTING.md](CONTRIBUTING.md) | The safety gates, and sending back what your port taught |

## Licence

MIT (see [`LICENSE`](LICENSE)); vendored files carry their own headers, and the decompilers are fetched
under their own licences. Details in [CONTRIBUTING.md](CONTRIBUTING.md#licence).
