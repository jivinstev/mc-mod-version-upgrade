---
name: test-installs
description: Test fork ports' released jars the way players run them -- on real Minecraft Launcher installs of every Minecraft version they support, with no Gradle on the test machine. Plans per version (NeoForge, Java, each fork's release jar, the sibling jars its CI pins, the registry dependencies, download and disk sizes), installs NeoForge headless and one separate instance per version (optionally a Launcher profile each and the Gate C carrier), then launches the real client one version at a time for a boot test and the Gate C phases (launch, spawn, battle, gauntlet), with a per-version report. Never touches the player's own mods folder or profiles. Use when the user asks to test a port's releases in the real game, on real installs, across versions, to boot-test or Gate-C-test released jars, or to set up test instances or profiles per Minecraft version.
---

# Test fork ports on real installs

**Which fork:** this works on **released jars**, so it is the same for a single-version fork (one `neoforge-<mc>`
branch, one release series: test that one version) and a fork of version branches (one release series per
`neoforge-<mc>` branch: test each version). It changes nothing in any fork and runs no Gradle; it only reads the
forks' branches and releases.

`tools/real-install.py` does the work in three steps: `plan` reads, `install` writes the instances, and `test`
launches the game. Each step reads the plan the step before saved, so they can run on different days.

## 1. Ask (only what is not already clear)

1. **Which forks and versions?** The forks as `owner/repo`, the library first and its dependents after it (the last
   fork names the instances). The versions are `--mc X` (repeat it), or `--all-versions`: every `neoforge-<mc>` branch
   that **all** the forks have. A library version whose dependent has no port there is skipped, and the plan says so.
2. **Pre-releases?** Use `--prerelease` while the ports only have pre-releases, which is the case until a release is
   promoted. Without the flag, a version with no full release is a plan problem that names the flag.
3. **Modrinth or CurseForge build?** Use `--variant mr` (the default) or `--variant cf`. When a release has one jar
   per platform, this picks one. It also picks the dependent workflow's `--dep-jar-variant` cf pin when the workflow
   has one.
4. **Launcher profiles?** With `install --profiles`, each version gets its own profile in the Minecraft Launcher, so
   the player can open it by hand. The Launcher must be closed for this, because it rewrites that file.
5. **Gate C?** With `--with-carrier`, the Gate C carrier goes into each instance and `test` runs the four phases after
   the boot test. Without it, `test` runs the boot test only.

## 2. Plan (read-only: GitHub, Mojang and the registries)

```bash
python3 tools/real-install.py plan --fork owner/Lib --fork owner/Mod --all-versions --prerelease [--variant cf] [--with-carrier]
```

For each Minecraft version the plan works out:

- **NeoForge:** the newest `neo_version` / `neoforge_version` in the forks' `gradle.properties` on that version
  branch. It is raised within the same line when a jar's own neoforge range needs a newer one.
- **Java:** the major version from the version JSON's `javaVersion` (21 for 1.21.x, 25 for 26.x).
- **Each fork's jar:** from the newest release whose tag ends `-mc<mc>` or `-mc<mc>-rN`, keeping to the branch's own
  `mod_version` series. It takes the `--variant` jar and checks its sha256 against the one GitHub records.
- **Sibling jars:** each sibling-port jar the fork's version-branch workflow pins (its curl line plus the sha256 on the
  line after). When that sibling is itself one of the `--fork`s, its own newest release is used instead and the pin is
  noted. `--prefer-pins` takes the pin.
- **Registry dependencies:** every dependency the jars' `neoforge.mods.toml` marks required that nothing in the set
  provides or bundles. These come from Modrinth or CurseForge for that version, sha1-verified, and are labelled "not
  tested by CI".
- **Not read:** a branch's `.github/port-install.json`. Every version branch carries the base version's copy.

The plan prints every jar with its size and source. It also prints what each version adds on disk: the client jar,
libraries and asset objects the Launcher folder lacks, the Java runtime if it is absent, and NeoForge (an estimate).
Disk is checked against all of it, and the plan refuses (exit 1) when less than `--margin-gb` (default 5) would stay
free. Show the user the plan and wait for a yes before installing. A version that lists a `PROBLEM` is skipped by
`install`, so resolve it or leave that version out.

## 3. Install

```bash
python3 tools/real-install.py install [--profiles] [--with-carrier] [--dry-run]
```

For each version, install does these steps:

1. **Minecraft files:** fetches the files the Launcher never downloaded for that version from Mojang's manifests,
   sha1-checked. A version that was never launched therefore works.
2. **Java:** fetches the Java runtime the version names, when it is absent.
3. **NeoForge:** installs it with its own installer, headless, on the Launcher's Java (tools/install-port.py's code
   path). `launcher_profiles.json` is restored byte for byte afterwards, which undoes the installer's edit to the
   player's "NeoForge" profile. A NeoForge install refuses while the Launcher is running.
4. **The instance:** writes `<minecraft>/instances/<label>-<mc>/` (or `--instances-dir`). Its `mods/` holds exactly
   that version's set, plus `gatec-carrier-<mc>.jar` with `--with-carrier`. A stray jar is moved to `mods-removed/`
   and is not deleted. A folder this tool did not create is refused.

`--profiles` then adds one profile per version (`mmvu-test-<label>-<mc>`, gameDir = the instance) after backing up
`launcher_profiles.json`, and refuses while the Launcher is running. The player's own mods folder, profiles and
settings are never changed. Install is idempotent: running it again fetches nothing that is already there.
`--use-installed` writes only the instances, so Minecraft, Java and NeoForge must already be in the Launcher folder.

## 4. Test (one launch at a time; a game window opens for each)

```bash
python3 tools/real-install.py test [--phases launch,spawn,battle,gauntlet] [--boot-only] [--timeout 300] [--phase-timeout 900] [--report DIR]
```

Each launch uses the command the Launcher itself builds:

- the version JSON chain;
- the libraries for this OS and architecture;
- the NeoForge version's own jar, never vanilla's (with vanilla's, the module system refuses two `minecraft` modules);
- `-XstartOnFirstThread` on macOS;
- an offline player;
- the Java the version names, from the Launcher's `runtime/` folder;
- a watchdog timeout.

**Boot test, every version.** It passes only when all of these hold:

- every mod under test is in the loaded mod list;
- no new `crash-reports/` file appeared;
- stderr shows no exception;
- the title screen was reached (`Sound engine started` and a texture atlas created).

The game is then stopped. Stderr matters because a JVM that dies before logging is up leaves its only trace there:
the Launcher shows nothing, and the player sees an instant exit. A native crash is named by its frame. On macOS a
crash in GLFW means there was no awake, unlocked display to open a window on.

**Gate C, with the carrier.** Each phase is its own launch with `-Dsmokeharness.boottest=true
-Dsmokeharness.testmode=<phase> -Dsmokeharness.namespaces=<mod ids under test>`. The carrier's
`SMOKEHARNESS_BOOT_TEST: PASS|FAIL` line, on stdout or in `logs/latest.log`, decides the phase. When the boot test
fails, that version's phases are not run.

Afterwards a per-version, per-phase table is printed, and `report.json` and `report.md` are written with each run's
command, stdout, stderr, latest.log and any crash report. The exit code is 0 only when everything passed. Linux
runs under Xvfb when there is no display.

## The Gate C carrier

The carrier is a mod-agnostic jar per Minecraft version, built from `templates/smoke-harness` (mod id `smokeharness`)
and published by this repository's CI as the release `gatec-carrier-v<N>`. Its assets are `gatec-carrier-<mc>.jar`,
`SHA256SUMS`, and `MANIFEST.txt` (NeoForge and Java per jar). The newest release is used by default, and its jar is
checked against SHA256SUMS. Other sources:

- `--carrier-release TAG`: one particular release;
- `--carrier-dir DIR`: a folder of jars, such as a CI artifact (its SHA256SUMS is checked when present);
- `--carrier-jar [MC=]JAR`: one jar.

Forks carry nothing for it.

## Never

- Write to the player's mods folder or change a profile that is not `mmvu-test-*`.
- Run two launches at once.
- Install while the plan's disk check fails.
- `pkill java` to tidy up. The player's own game runs from the same Launcher Java, and it would die too. The tool
  stops only the process it started.
