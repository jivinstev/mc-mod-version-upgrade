# mc-mod-version-upgrade

> ⚠️ **Under construction.** Not yet published; please don't rely on it until this notice goes away.

Two things in one repo, and most people only want the first:

### Install mods — needs only Python 3 and a network
Search Modrinth and CurseForge behind one interface, resolve dependencies recursively, check whether
a build for your Minecraft version already exists, verify downloads by checksum, and deploy into the
right mods folder.

```bash
git clone https://github.com/jivinstev/mc-mod-version-upgrade
cd mc-mod-version-upgrade && ./setup
```

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

[`CATALOG.md`](CATALOG.md) is the knowledge base the migrate skill works from: ~450 entries, each a
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
and runs the IP gate before anything leaves your machine. The forbidden-name list is kept private,
so the hook's name check runs only if `.env.local` names a list with `FORBIDDEN_NAMES_FILE=`.

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
