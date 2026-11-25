---
name: player-install
description: Give players one command that installs a mod from its GitHub releases into their own Minecraft -- NeoForge, the mod's newest release for their Minecraft version, and the mods it depends on -- interactively (pick the version, the release, a new profile or an existing NeoForge one, bail before anything is written) or headless with --yes. Also publishes the command (a player-install-vN release of this repository) and prints the one-liners a repository's README shows. Use when the user asks for an install command or script for players, a one-line install for a mod's README or site, to install a mod from a repository's releases into their own launcher, or to release a new version of the player install.
---

# Player install: one command from a repository's releases into a player's Minecraft

`tools/player-install.py` installs a repository's released mod into the player's **own** Minecraft (unlike
`real-install.py`, which builds separate test instances). Players never run it from a checkout: they paste the
one-liner a README shows, which downloads this repository's `player-install-vN` release bundle, checks its sha256 and
runs the installer.

## The one-liners (what a README shows)

Replace `vN` with the newest `player-install-vN` release of this repository, and `OWNER/REPO` with the mod's
repository (`OWNER/REPO:MOD` for a repository that releases several mods, e.g. one world of a worlds repository):

```
# macOS / Linux -- asks at each step
curl -fsSL https://github.com/jivinstev/mc-mod-version-upgrade/releases/download/player-install-vN/install.sh | bash -s -- OWNER/REPO
# macOS / Linux -- every default, no questions
curl -fsSL https://github.com/jivinstev/mc-mod-version-upgrade/releases/download/player-install-vN/install.sh | bash -s -- OWNER/REPO --yes

# Windows (PowerShell) -- asks at each step
& ([scriptblock]::Create((irm https://github.com/jivinstev/mc-mod-version-upgrade/releases/download/player-install-vN/install.ps1))) OWNER/REPO
# Windows -- every default
& ([scriptblock]::Create((irm https://github.com/jivinstev/mc-mod-version-upgrade/releases/download/player-install-vN/install.ps1))) OWNER/REPO --yes
```

Every option below goes after `OWNER/REPO` on any of these lines.

## What the player gets

Interactive (the default), it asks, and `q` at any question stops with nothing written:
1. which mod, when the repository releases several and none was named;
2. the Minecraft version, from the versions the repository has released (a version with only pre-releases is
   labelled so; choosing one asks before taking the pre-release);
3. where it goes: **a new launcher profile with its own game folder** (default; the player's other profiles and mods
   are untouched) or one of their existing NeoForge profiles for that Minecraft version;
4. a plan (every jar with its release, NeoForge's version, the folders), then "Install it?".

`--yes` takes every default and asks nothing. It refuses, saying why, where no default exists: a multi-mod repository
with no `:MOD`, or a Minecraft version with no full release yet (`--pre` takes the pre-release).

Options: `--mc 1.21.8`, `--release TAG`, `--pre`, `--instance new|"Profile name"|FOLDER`, `--minecraft-dir DIR`,
`--dry-run`, `--list` (released mods and versions), `--with python` (Python 3 on the PATH for a mod's optional
feature, installed for this user only), `--boot-test` (boot the installed profile headless and check the mods load).

## What it reads (nothing new in the mod's repository)

- **Releases** tagged `<series>-<version>-mc<minecraft>` (a re-release adds `-r<N>`), one series per mod; the
  release's one `.jar`, verified by sha256 from GitHub's asset digest or the release's `SHA256SUMS`. Nothing it
  cannot verify is installed, and every jar is downloaded and checked before anything is written.
- **Dependencies** pinned at the release's tag: a `*.lock` file in the mod's folder or the repository root with
  `<name>_repo`, `<name>_tag`, `<name>_asset`, `<name>_sha256` (an exact release and sha256: the player never picks
  the dependency separately), and a fork port's `.github/port-install.json`. Followed recursively; a pinned release
  for another Minecraft version is refused.
- **NeoForge**: `neo_version` in the release's `gradle.properties` at the tag (the version it was built on), else the
  newest NeoForge of that Minecraft line at or above every jar's own floor.
- **An after-install note**, optional: `player-install.txt` in the mod's folder or `.github/player-install.txt` at
  the release's tag, up to 12 lines printed after the install (how to turn on an optional feature in game, say).
  A dependency's note is printed too, so a world that pins a core shows the core's note.

## What it writes

NeoForge with its own installer, headless, using the Minecraft Launcher's Java (fetched from Mojang when absent) and
the vanilla files it needs; the jars into the instance's `mods/` (a jar of the same mod id is moved to
`mods-replaced/`; nothing else in the folder changes, so a re-run updates in place); the launcher profile (a new one,
or the chosen existing profile's NeoForge version when it needs a newer one). It refuses while the Minecraft Launcher
is running, because the Launcher rewrites its profiles file.

## Publishing a new version of the command

The bundle is built by `python3 tools/player-install.py --bundle DIR --tag player-install-vN` (the installer and the
tools it composes, deterministic; both bootstraps with that tag and the bundle's sha256 written in). Publish it by
running the `player-install` workflow on `main` with `tag` = the next `player-install-vN`: it runs the checks and the
real-release acceptance installs first, and refuses a tag that already exists. A README keeps naming its tag until
its owner moves it.

## When a repository doesn't fit

The installer says why and stops. The usual causes: tags that are not `<mod>-<version>-mc<minecraft>`; a release
with several jars; no sha256 (no GitHub digest and no `SHA256SUMS`: add `SHA256SUMS` to the release); a dependency
not pinned by any lock file or port manifest at the tag. Propose the smallest change to the mod's release process,
not a change to the installer, unless the shape is common.
