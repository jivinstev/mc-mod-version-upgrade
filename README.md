# mc-mod-version-upgrade

**Install Minecraft mods, and port the ones that are stuck on an old version, by asking
[Claude Code](https://claude.com/claude-code) in plain words.**

```text
> Install Sodium
> Migrate <mod name> to Minecraft 26.2
```

The first finds the right build on Modrinth or CurseForge, resolves its dependencies, verifies the
download and puts it in the right mods folder. The second is for a mod with no build for your version:
it decompiles the old jar, rewrites it through a catalogue of ~540 known API changes, builds it, and
only calls it done once three test gates pass, the last of them a real Minecraft client that loads the
mod, spawns its mobs and uses its items.

### Measured ports

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

## Quick start

```bash
git clone https://github.com/jivinstev/mc-mod-version-upgrade
cd mc-mod-version-upgrade && ./setup
claude
```

`./setup` asks a few questions, each with a recommended answer, and ends by telling you what to type.
Two kinds of use, and most people only want the first:

### Install mods — needs only Python 3 and a network
Search Modrinth and CurseForge behind one interface, resolve dependencies recursively, check whether
a build for your Minecraft version already exists, verify downloads by checksum, and deploy into the
right mods folder.

`./setup` finds your Minecraft install and says what is in it, recommends an answer to every
question (Enter accepts it), and ends with a command to try. Re-running it is safe: it keeps your
earlier decisions and only asks about new ones (`./setup --review` goes through them all again).
`./setup --check` changes nothing.

### Migrate a mod to a newer Minecraft version — adds a JDK and several GB of disk
When your favourite mod is stuck on an old version, port it: decompile, scaffold, rewrite through a
catalogue of known API changes, build, test, deploy. It takes hours rather than seconds, and
sometimes does not succeed. It is an add-on to installing: answer **yes** to "Also set up migration?"
in `./setup` (or run `./setup --migrate` any time later). Installing still uses an existing build
whenever there is one; migration only runs for a mod that has none for your version. Setup
creates a workspace outside this checkout (`~/.mc-mod-upgrade/work` by default) where decompiled
mods live; the migration commands run from there.

**Tested targets: Minecraft 1.21.1 and 26.2 on NeoForge** (`SUPPORTED_VERSIONS.md`). A version becomes
tested once 10 different mods have been ported to it with the real-client gate passing. You can port to
any version: setup and the tools show how proven it is, and your port counts toward it.

**Ported mods are for your own machine.** This tool changes someone else's mod so it runs on your
Minecraft. Many mods are "all rights reserved": do not publish or redistribute a ported jar without
the original author's permission. The MIT licence below covers this tool, never the mods it ports.

A finished migration delivers two things to two places. The port's source goes to your **mods
destination**, any folder or git repository you name in setup (`tools/finish-port.py` commits it on a
`port/<modid>` branch there). What the port *taught*, stated without the mod's name, goes back to this
repository as a pull request against the catalogue (`tools/propose-learnings.py`), so the next person's
port is faster. Each delivered port also records what it cost (model, effort, tokens, time, and dollars
where Claude Code records them) in `docs/port-costs.tsv`, so the next person can see what a mod of that
size is likely to take.

### If you don't have the prerequisites

| | macOS | Windows | Debian / Ubuntu | Fedora |
|---|---|---|---|---|
| git | `xcode-select --install` | `winget install --id Git.Git` | `sudo apt install git` | `sudo dnf install git` |
| Python 3 | `brew install python` | `winget install --id Python.Python.3.12` | `sudo apt install python3` | `sudo dnf install python3` |
| JDK 21 *(migrate only)* | `brew install --cask temurin@21` | `winget install --id EclipseAdoptium.Temurin.21.JDK` | `sudo apt install openjdk-21-jdk` | `sudo dnf install java-21-openjdk-devel` |

Minecraft 26.x targets need Java **25** as well. The tooling is bash + Python, so on Windows run
it from WSL or Git Bash.

---

## The migration catalogue

[`CATALOG.md`](CATALOG.md) is the knowledge base the migrate skill works from: ~540 entries, each a
real migration failure written as **pattern → error → fix**, with the measurement that established
it. The mods each lesson came from are described rather than named; the evidence is kept verbatim.

## Safety gates

Three checks run on every pull request, and all must pass:

| gate | question it answers |
|---|---|
| `tools/check-no-ip.py` | are we about to publish somebody else's work? |
| `tools/check-catalog-fidelity.py` | did we quietly lose a migration rule? |
| `tools/gen-vendored.py --check` | do the files we copy into *your* project carry the licence the manifest says? |

Run them locally the same way CI does:

```bash
python3 tools/check-no-ip.py
python3 tools/check-catalog-fidelity.py
python3 tools/gen-vendored.py --check
./tools/test-gates.sh          # proves each gate FAILS on a planted violation
./tools/test-setup.sh          # proves ./setup keeps its re-run promises
```

`check-no-ip.py` scans the **committed tree** rather than the diff, because a file added in an
earlier commit and pushed later is still a publication. It treats "could not run" as a failure: a
gate that quietly did not run reports identically to one that passed, and what this one guards
cannot be recalled.

## Contributing

`main` takes pull requests only. Fork, branch, open a PR, and let the gates run:

```bash
gh repo fork jivinstev/mc-mod-version-upgrade --clone
```

Install the hook once after cloning: `./tools/install-hooks.sh`. It refuses direct pushes to `main`
and runs the IP gate before anything leaves your machine. Optionally, `FORBIDDEN_NAMES_FILE=` in
`.env.local` can name a private list of mods you have ported, and the hook then refuses those names too.

**The easiest contribution is a lesson from your own migration:** the skill's retrospective collects
them, and `python3 tools/propose-learnings.py --modid <modid> --push` opens the PR, gated, with the
ported mod's identity refused.

**Never include mod source, jars, or decompiler output** — not even in a test fixture. The migration
workspace lives outside the repository by default; please keep it there.

## Licence

MIT (see [`LICENSE`](LICENSE)). The files the tooling copies into your own
mod project (listed in [`VENDORED.tsv`](VENDORED.tsv)) each carry their own MIT header, so a copied file
stays self-describing and using it puts no obligation on your mod beyond keeping that header. The
Gradle wrapper files are Gradle's, under its Apache-2.0 licence. The decompilers are not shipped here:
`tools/download-tools.sh` fetches them, sha1-verified, under their own licences (Vineflower: Apache-2.0,
CFR: MIT).

By opening a pull request you agree that your contribution is licensed under the same MIT licence.
