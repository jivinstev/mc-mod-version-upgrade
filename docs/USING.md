# Using mc-mod-version-upgrade

`./setup` asks a few questions, each with a recommended answer, and ends by telling you what to type.
Two kinds of use, and most people only want the first:

## Install mods — needs only Python 3 and a network
Search Modrinth and CurseForge behind one interface, resolve dependencies recursively, check whether
a build for your Minecraft version already exists, verify downloads by checksum, and deploy into the
right mods folder.

`./setup` finds your Minecraft install and says what is in it, recommends an answer to every
question (Enter accepts it), and ends with a command to try. Re-running it is safe: it keeps your
earlier decisions and only asks about new ones (`./setup --review` goes through them all again).
`./setup --check` changes nothing.

## Migrate a mod to a newer Minecraft version — adds a JDK and several GB of disk
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

## If you don't have the prerequisites

| | macOS | Windows | Debian / Ubuntu | Fedora |
|---|---|---|---|---|
| git | `xcode-select --install` | `winget install --id Git.Git` | `sudo apt install git` | `sudo dnf install git` |
| Python 3 | `brew install python` | `winget install --id Python.Python.3.12` | `sudo apt install python3` | `sudo dnf install python3` |
| JDK 21 *(migrate only)* | `brew install --cask temurin@21` | `winget install --id EclipseAdoptium.Temurin.21.JDK` | `sudo apt install openjdk-21-jdk` | `sudo dnf install java-21-openjdk-devel` |

Minecraft 26.x targets need Java **25** as well. On Windows, see the README's
[Windows quick start](../README.md#on-windows-experimental) (experimental): the tooling runs in Git Bash (part of Git for
Windows, which Claude Code needs anyway), and `setup.cmd` starts setup from cmd or PowerShell.

## In a Claude Code cloud session

The README's cloud quick start is the whole setup. Two notes:

- **One environment serves both this repo and
  [mc-buddy-builder](https://github.com/jivinstev/mc-buddy-builder):** its allowed-domains list is the
  union of what both need, so make it once and use it for either.
- **Getting the jar home.** The session is deleted when it ends, and `*.jar` is gitignored. Ask Claude
  to send you the jar (a download in the Claude app). If that isn't available, ask it to commit the jar
  with `git add -f` to a branch of your fork, then pull that branch at home.
