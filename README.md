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
earlier decisions and only asks about new ones. `./setup --check` changes nothing.

### Migrate a mod to a newer Minecraft version — adds a JDK and several GB of disk
When your favourite mod is stuck on an old version, port it: decompile, scaffold, rewrite through a
catalogue of known API changes, build, test, deploy. It takes hours rather than seconds, and
sometimes does not succeed. Choose `migrate` in `./setup` (or `./setup --path migrate`). Setup
creates a workspace outside this checkout (`~/.mc-mod-upgrade/work` by default) where decompiled
mods live; the migration commands run from there.

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

**Never include mod source, jars, or decompiler output** — not even in a test fixture. The migration
workspace lives outside the repository by default; please keep it there.

## Licence

Not yet chosen — deliberately. The licence boundary depends on which files the tooling copies into
users' own projects, and that set is an output of the build rather than something to guess in
advance. It will be settled, and stated here, before this repository is made public.
