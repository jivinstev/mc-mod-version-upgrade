---
name: install-mod
description: Install a Minecraft mod into the player's instance end-to-end — search (Modrinth + CurseForge), check version compatibility with the target (default NeoForge 1.21.1), recursively resolve dependencies, download, migrate any that need porting (via the migrate-mod skill), smoke-test in isolation, then install. Handles open-ended requests ("a mod that adds a giant squid boss"), asks the right questions when a case isn't straightforward (a newer-1.21.x-only build, a CurseForge download opt-out, an ambiguous search), and keeps a resumable plan. Use when the user wants to install/find/add a Minecraft mod, or asks for a mod containing some feature/mob/item.
---

# Install a Minecraft mod (search → install → test)

You take a request — a mod name, or an open-ended "a mod that has X in it" — and drive it all
the way to a **smoke-tested jar installed in the player's instance**, migrating it first if no
compatible build exists. You resolve dependencies recursively, and you **keep a resumable plan**
so a long dependency/migration chain never loses its place.

## Target & tools
- **Default target:** `neoforge` / `1.21.1` (the player's live instance at `MINECRAFT_MODS_DIR`).
  Everything is parameterized `(loader, mc)` — if the user names a different target, use it.
- **Registry CLI:** `tools/mod-registry/modreg.py` — `search | versions | deps | download |
  resolve-modid | scan-instance`. JSON to stdout; honors the CurseForge ToS (no disk cache,
  key only in a header, never printed). See `references/backends.md`.
- **Plan artifact:** `tools/mod-registry/plan.py` — `init | render | show` over
  `installs/<slug>/plan.json` (machine state) + `PLAN.md` (human mirror). See `references/resolution.md`.
- **Migration:** the **`migrate-mod` skill** (invoke it; don't re-implement porting).
- **Smoke test:** `templates/smoke-harness/` — boots arbitrary prebuilt jars headless + client.
- **Exact commands for every step: `references/pipeline.md`. Read it.**

## From a fork port (a GitHub repo, not a registry)
When the user names GitHub repositories holding fork ports (made with the `port-fork` skill), do
not search the registries. Each port's branch carries `.github/port-install.json`, listing the
released jar and every dependency CI tested it with, each with a URL and sha256:

```bash
python3 tools/install-port.py owner/RepoA owner/RepoB --dry-run      # show the plan
python3 tools/install-port.py owner/RepoA owner/RepoB                # install into MINECRAFT_MODS_DIR
```

It follows sibling ports a port depends on, installs each mod once (newest file when ports
disagree), verifies every sha256, skips mods already in the instance, and installs the NeoForge the
ports were tested on when it is missing (its own installer, headless, with the Launcher's Java; the
Launcher must have been opened once). `--no-neoforge` skips that. `--with-optional` adds the optional mods CI loaded; `--mods-dir` picks another
instance; it also adds what an optional mod itself requires (read from that mod's jar). `--full` installs
exactly what CI's full pass loaded. Before writing to the real instance (confirm first, as below), install
into a scratch folder and boot it:

```bash
python3 tools/install-port.py owner/RepoA owner/RepoB --mods-dir /tmp/try/mods
tools/boot-mods.sh /tmp/try/mods            # headless server; PASS = the set loads together
```

## Operating model
Run autonomously through resolution + download + migrate + smoke-test. **Stop and ask** only at
the three question gates (below) and **before writing to the real instance** (the final install is
an outward change — confirm unless the user already said "just install it"). Commit nothing to the
player's instance until the smoke test is green.

Create the work dir once: `installs/<slug>/` (slug = a short kebab id for the request). Initialize
the plan with `plan.py init`, and after **every** state change update `plan.json` and re-run
`plan.py render`. The plan is the resume point — on re-invocation, `plan.py show` tells you the next
actionable node; re-query live (nothing is cached) and continue from there.

## The three question gates (stop, ask, record the answer in plan.json `questions[]`, resume)
1. **ambiguous-search** — the query is open-ended or returns several plausible mods. Present the top
   candidates (name, backend(s), downloads, MC/loader availability, the compat classification) and
   let the user pick. Never silently guess which mod they meant.
2. **needs-downport** — the only builds are a NEWER 1.21.x than the target (e.g. 1.21.4). Ask: downport
   to 1.21.1 (recommended — keeps the instance pinned), pick a different mod, or skip. Downport = a
   `migrate-mod` run with `SRC_MC` = the newer version (see stage e).
3. **cf-opt-out** — CurseForge returns no download URL (author disabled third-party distribution) and
   the mod isn't on Modrinth. You **cannot** auto-download it (ToS). Give the user the website link and
   ask them to download it manually into `installs/<slug>/jars/`, then continue.

## The pipeline — stages (a) → (g)
Work them in order; `references/pipeline.md` has the exact commands.

- **(a) SEARCH.** `modreg.py search --query "<request>"` across both backends. For an open-ended
  request, extract the feature/mob/item and search that. Present candidates → **ambiguous-search gate**
  → user picks the mod (provider + id). Record the root node in plan.json.
  - **⚠️ Don't stop at the top hit — a 1.21.1 build may live on a SEPARATE project.** For a popular mod
    whose canonical project is stuck on an older MC (so stage (b) says `needs-migrate`), a community
    **"(Unofficial Port)"** / "1.21.1 port" project often carries a native 1.21.1 build under a *different*
    project id (measured: one popular cave mod's official CurseForge listing is 1.20.1-only, while a
    separate "(Unofficial Port)" listing ships a native 1.21.1 build). Before committing to a big migration, **re-search** `"<name> port"` /
    `"<name> 1.21.1"` and scan several hits for a native one. If the user has the jar locally (a CF-UI
    install), the definitive resolver is the **CurseForge fingerprint API** — Murmur2 (whitespace bytes
    9/10/13/32 stripped, seed 1) → `POST /v1/fingerprints` returns the exact `{modId, fileId}` (that's
    how the CF launcher identifies a jar). Surface both the official + any port variant at the gate so
    the user chooses migrate-official vs install-native-port.
- **(b) VERSION CHECK.** `modreg.py versions --provider <p> --id <id>` → `classification`:
  - `native` → download only (stage d).
  - `needs-migrate` → download the chosen older source, then migrate (stage e).
  - `needs-downport` → **needs-downport gate**, then download the newer source + migrate-as-downport.
  - `none` → tell the user; stop for this node.
- **(c) DEPENDENCY CHECK (recursive).** First `modreg.py scan-instance` → the set of modIds already in
  the instance. Then for the chosen file, `modreg.py deps` → for each **required** dep: skip if its
  modId is already installed (`present-in-instance`); else `versions`-classify it, add a `dependency`
  node (with `requiredBy`), and recurse into ITS deps. Dedup/cycle-guard by modId. Order the plan
  **deps-first**. (Optional deps: list them, install only if the user wants.)
- **(d) DOWNLOAD.** For every `native`/`needs-migrate`/`needs-downport` node, `modreg.py download`
  (Modrinth preferred; CF fallback). Into `installs/<slug>/jars/`. Verify the sha1 (the CLI does).
  A CF opt-out → **cf-opt-out gate**.
- **(e) MIGRATE (only nodes that need it), deps-first.** First check the migration add-on is set up:
  `SETUP_PATH=migrate` and `MIGRATE_WORKSPACE` in `.env.local`, and `java -version` works. If not, do
  NOT start porting: stop and tell the user plainly -- "<mod> has no build for Minecraft <version>, so
  it would have to be ported. That needs the migration add-on (a JDK and several GB): run
  `./setup --migrate`, then ask again." Finish the `native` nodes that do not depend on it. Otherwise,
  for each `needs-migrate`/`needs-downport` node, **invoke the `migrate-mod` skill** (see handoff below). On its green build, record the output
  jar in the node. `native` nodes skip this stage.
- **(g) SMOKE-TEST in isolation — BEFORE installing. Test DEPTH depends on compat (this matters):**
  Collect all final jars + their target modIds, plus **every required dep jar** (even
  `present-in-instance` ones — the harness is isolated; omitting a dep = `NoClassDefFoundError`).
  Always run **headless** load first (`runGameTestServer`). Then the **client** boot — and how far you
  drive it depends on whether the node was migrated:
  - **`native-1.21.1` (downloaded, NOT migrated):** a **light client smoke — `launch spawn`** is
    sufficient. It's a released build the author already QA'd against 1.21.1; we're only confirming it
    loads + renders cleanly alongside your instance. No reason to do more.
  - **`needs-migrate` / `needs-downport` (MIGRATED):** run the **FULL migrate-mod Gate C suite —
    `launch spawn battle gauntlet`.** Migration rewrites combat, item, render, effect, and save paths
    that `launch`/`spawn` never exercise, so the extra phases earn their keep: **battle** (mobs fighting
    to the death — AoE, projectiles, death sequences, R8/R9/R10) and **gauntlet** (build every tooltip,
    render every item model, wear every armor piece, place every block, `use()` every item, apply every
    effect — R11). This is the same bar every migrate-mod port is held to; a migrated jar isn't done
    until all four phases are green.
  A crash → **crash/fix cycle**: read `$SIG/crash.log`, fix the mod SOURCE, **rebuild the mod jar**
  (`./gradlew build` in `mods/<modid>/`, since the harness loads it as an external jar), `touch
  $SIG/fix.done` to relaunch. **Catalog every new §R runtime pattern you fix** (Step-4b protocol) — the
  battle/gauntlet phases are exactly where the compile-clean-but-crashes patterns hide.
- **(f) INSTALL — the outward change, last.** Copy the smoke-tested jars into `MINECRAFT_MODS_DIR`
  (atomic). Confirm with the user first unless they pre-authorized. Mark nodes `done`; final
  `plan.py render`.

## Invoking migrate-mod (stage e handoff)
For a node needing a port, invoke the **migrate-mod skill** with a prompt of this shape:

> Migrate `installs/<slug>/jars/<file>.jar` from `<srcLoader> <srcMC>` to `<dstLoader> <dstMC>`.
> Use workspace `mods/<modid>`. On a green build + gates, the jar is at
> `mods/<modid>/build/libs/<modid>-<ver>.jar` — then set, in `installs/<slug>/plan.json` node `<id>`:
> `migrate.status="built"`, `migrate.outputJar=<that path>`.

- A `needs-migrate` Forge-1.20.1 node needs no extra params (migrate-mod defaults = forge/1.20.1 →
  neoforge/1.21.1). Other sources: pass the real `SRC_LOADER`/`SRC_MC` (migrate-mod's SRG-skip +
  minor-version-delta handling take over). A `needs-downport` node passes `SRC_MC` = the newer 1.21.x.
- **Scope is asked inside migrate-mod (its Step 3b):** full port, minimal (core only), or chosen chunks,
  each with its estimated share of the work. If the user already said how much they want ("just the
  mobs", "everything"), pass that in the prompt so the question is answered rather than asked twice.
  A dependency node the user's mod needs in FULL (an API library) is ported in full without asking.
- **Resume is two-layer:** the chain lives in `plan.json` (which nodes, their edges, coarse status);
  the detail of one port lives in migrate-mod's own `mods/<modid>/MIGRATION.md` + its git branch. Don't
  duplicate — record only `migrate.status` here and let migrate-mod resume itself.

## Done
A node is `done` when its jar is smoke-tested and installed. The request is done when every node is
`done` (or a skipped optional/opt-out is recorded). `PLAN.md` should show the whole tree green.
