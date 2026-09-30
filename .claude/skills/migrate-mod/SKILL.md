---
name: migrate-mod
description: Migrate a Minecraft mod between loaders and/or Minecraft versions — by default Forge 1.20.1 → NeoForge 1.21.1, but parameterized by SRC_LOADER/SRC_MC → DST_LOADER/DST_MC (it also handles 1.21.x↔1.21.y minor-version hops and downports). Given a mod JAR, decompile it, scaffold a target Gradle workspace, port the code through a build-error loop using the loader-transform + version-family + minor-version-delta references, then build and deploy. Use when the user asks to migrate/port/upgrade/downport a mod, or names a JAR to bring to a target loader+version (default NeoForge 1.21.1).
---

# Migrate a mod to NeoForge 1.21.1

You are porting a compiled mod JAR (Forge, and/or an older Minecraft version) to
**NeoForge 1.21.1**. The end state is a jar in the player's `mods/` folder that
loads without crashing. Work the process below top-to-bottom, and **loop on the
build until the error count stops dropping or hits zero** — don't stop at the
first wall; re-read the references and try the next category of fix.

## Target parameters (this skill is parameterized — nothing is hardcoded to one target)
A migration is defined by four parameters. **If the caller (or the `install-mod` skill)
doesn't specify them, use the defaults** — which is exactly the common case and makes the
rest of this doc read literally:

| param        | default   | meaning                          |
|--------------|-----------|----------------------------------|
| `SRC_LOADER` | `forge`   | the source jar's loader (`forge` \| **`fabric`** \| `neoforge`) |
| `SRC_MC`     | `1.20.1`  | the source jar's Minecraft version |
| `DST_LOADER` | `neoforge`| the target loader                |
| `DST_MC`     | `1.21.1`  | the target Minecraft version     |

Everywhere the references below say a concrete literal ("Forge", "1.20.1", "NeoForge",
"1.21.1", `pack_format` 34/48, `DataVersion 3955`), that is the **default instantiation**.
When a different target is requested, substitute the parameter. The knowledge splits by axis:
- **Loader transform** (`SRC_LOADER`→`DST_LOADER`, e.g. Forge→NeoForge): `references/forge-to-neoforge.md`
  + catalog §B/§C/§D/§E/§H. Skip entirely if `SRC_LOADER == DST_LOADER`.
  - **`SRC_LOADER = fabric` is a DIFFERENT corpus — read catalog §P (#145–#161), not §B–§E.** A Fabric
    jar has **no `mods.toml`** (it has `fabric.mod.json` + a `*.accesswidener` + `META-INF/jars/`), and its
    classes are **intermediary**-mapped (`class_1799`/`method_7909`), so **`tools/srg-remap` does not apply** —
    use **`tools/intermediary-remap/`** at step 2b instead. **Verify the loader with `unzip`, never from a
    handed-down triage** (two Fabric gear mods were both described as Forge and were both pure Fabric).
- **Version-family transform** (e.g. 1.20.x→1.21.x): `references/mc-1.20-to-1.21.md` + catalog §G/§I/§L.
- **Specialized minor-version deltas** (1.21.x→1.21.y, e.g. 1.21.1↔1.21.4): `references/minor-version-deltas.md`
  + catalog §M. Consult these ONLY for a same-family hop; most are small.

**Concrete per-target knobs to set** (the rest is knowledge, not config):
`templates/neoforge-mod/gradle.properties` → `minecraft_version` / `minecraft_version_range` /
`neo_version` / `parchment_*` for `DST_MC`; the SRG step (below) takes `SRC_MC` as an argument;
`pack_format` and the GameTest `DataVersion` follow `DST_MC` (1.21.1 → 34/48, DataVersion 3955).

**Is `DST_MC` a tested target?** Look it up in `SUPPORTED_VERSIONS.tsv` BEFORE starting. If it is not
`tested`, say so to the user in one plain sentence first -- "Minecraft X is not a tested target yet: the
catalogue and templates were built for 1.21.1 and 26.2, so expect to find API changes nobody has written
down, and budget more time" -- and a beta loader (NeoForge `-beta`) is worth naming too. Then go ahead if
they want. Such a port is also how a version becomes tested: record the exact target in MIGRATION.md
(`minecraft_version`, NeoForge version, which gates passed), and let `propose-learnings.py` carry it into
the learnings PR (SUPPORTED_VERSIONS.md says what the reviewer does with it).

**Mapping rule (three cases, decide with `unzip -p "$JAR" META-INF/MANIFEST.MF`):**
**(1) Forge ≤1.20.x** → SRG member names → `tools/srg-remap` (Step 2b below).
**(2) Fabric (any version)** → `Fabric-Mapping-Namespace: intermediary` → **`tools/intermediary-remap/`**
(same two-script shape: `build_mapping.py <mc> <out.json>` then `apply_mapping.py <out.json> <src>`; verify
with `grep -rc '\b(class|method|field)_[0-9]\+_\?' src/main/java` → 0). See catalog §P #146.
**(3) already official** (1.20.5+/1.21.x Forge/NeoForge) → skip remapping entirely.

**SRG-skip rule:** Step 2b (SRG→official remap) exists because compiled **Forge ≤1.20.x** jars carry
SRG member names. It applies when `SRC_MC` is a ≤1.20.x Forge jar. **Skip Step 2b entirely** when the
source is already official-mapped — i.e. any 1.20.5+ / 1.21.x jar, or a NeoForge source (a 1.21.x→1.21.y
downport/upport). A quick `grep -rc '\bm_[0-9]\+_' src/main/java` of the raw decompile confirms: thousands
⇒ remap needed; a handful/zero ⇒ skip.

## References (read these — they are the actual porting knowledge)
- **The repo-root `CATALOG.md` ("Migration Pattern Catalog")** is the PRIMARY,
  authoritative reference: every issue we've hit as (Pattern → compile Error → Fix).
  **Read it first, apply its fixes proactively while reading the decompiled source
  (before compiling), and APPEND every new pattern you resolve** in the same format.
- `references/forge-to-neoforge.md` — **loader-transform** corpus (Forge→NeoForge). Skip if `SRC_LOADER == DST_LOADER`.
- `references/mc-1.20-to-1.21.md` — **version-family** corpus (1.20.x→1.21.x).
- `references/minor-version-deltas.md` — **specialized minor-version deltas** (1.21.x↔1.21.y). Only for same-family hops/downports; a stub filled on demand.
- `references/pipeline.md` — exact commands for decompile / scaffold / build loop.
- `references/catalog-scans.md` — the **MANDATED sweep**: one runnable block of every §S/§R scan.
  Run it in full at pre-flight (Step 4) and again as a done-gate (Step 6). Not optional, not a sample —
  this is how "use the catalog comprehensively" becomes enforced instead of hoped-for.

## Operating model — run autonomously through Gate B, AND self-launch Gate C on macOS
Get as far as possible WITHOUT the human. Gate C needs a real display, but **on macOS you can launch it
yourself** (LaunchServices — see part 2), so on a logged-in Mac the whole pipeline through Gate C is autonomous;
only genuinely headless environments fall back to a human-run command. Three parts:

1. **Autonomous (headless — no human, no display). Do all of this end-to-end without stopping:**
   - **Local history first:** `cd mods/<modid> && git init -q` (Step 4b reads this history). It stays
     LOCAL: the workspace is deliberately outside every repository, and Step 7 copies the source — not
     this history — into the mods destination.
   - Steps 0–4: locate/triage → scaffold → decompile+remap → the build-error loop to a clean compile.
   - **Run the mandated catalog sweep** (`references/catalog-scans.md`) — fix or explain every hit.
   - **Step 4b — the compile-clean retrospective** (mandatory): the instant errors hit 0, categorize every
     fix (a/b/c/d) and record each (b)/(c) in `$MIGRATE_WORKSPACE/catalog-additions.md`. This is the backstop that keeps
     the knowledge base comprehensive when the in-flight "append as you go" rule slips under load.
   - **Gate A** (`./gradlew test`) + **Gate B** (`./gradlew runGameTestServer` — load, spawn-every-mob,
     subsystems, **persistence round-trip**), the subsystem/round-trip tests built **against the Step 4b
     churn target list** (`references/test-targets.md`). Iterate BOTH to green, fixing each §R crash; `deployToMods`.
   - Commit as you go (in the port's local history). This whole span is autonomous — run it to green and report status.
2. **Wire + LAUNCH Gate C:**
   - The stress-test scripts + harness are **per-mod**: `mods/<modid>/tools/{client-validate.sh,client-boot-loop.sh,
     run-gatec.command}` and `mods/<modid>/src/.../test/ClientBootSmokeTest.java` (copy from `templates/neoforge-mod/`
     — its `tools/` maps to the mod's `tools/`; the scripts derive `<modid>` from their own location, so no edits
     there. Adapt the harness's `<modid>` literals + the gauntlet OPEN_GUIS step — **delete that step + its
     `Screen`/`Menu` imports if the mod has no GUIs**, or the client compile fails). The `client` run wiring is
     already in the template `build.gradle` (parameterized by `mod_id`). General tooling (vineflower, srg-remap,
     gen-empty-structure, download-tools) stays at repo-root `tools/`; only the stress test is per-mod.
   - **Arm the phase monitor** (Step 5 Gate C) over `$SIG_DIR`, THEN launch:
     - **macOS (autonomous — DO THIS):** `open mods/<modid>/tools/run-gatec.command`. LaunchServices starts the
       loop in the logged-in GUI session with display access (works from the sandboxed agent shell, no consent
       prompt). Do NOT use `osascript`/AppleEvents — its Automation-consent dialog blocks (`-1712`) until a human
       clicks Allow. **The only prereq is a live GUI session** (`pgrep -x WindowServer`); a **LOCKED screen and a
       SLEPT display do NOT block Gate C** — verified: GLFW boots to title with `CGSSessionScreenIsLocked=True`
       (see pipeline.md §6e for the deterministic checks + why). Don't gate on unlocked/awake.
     - **Linux with no display (a cloud session, CI) — autonomous too:** install Xvfb + Mesa once
       (`apt-get update && apt-get install -y xvfb mesa-utils libgl1-mesa-dri`; update FIRST, a bare install fails
       silently), then run `PHASES="launch spawn" ./tools/client-validate.sh` from the mod dir; the loop wraps itself
       in `xvfb-run` with software GL. On a rate-limited machine (HTTP 429 from Maven Central) the loop's own
       `./gradlew runClient` cannot take `--init-script`, so install `tools/central-mirror.init.gradle` into
       `~/.gradle/init.d/` first.
     - **Any other headless machine:** hand the human the ONE command (from the mod dir): `./tools/client-validate.sh`.
3. **Monitored client run (you launched it; you fix every phase):** `client-validate.sh` loops
   **launch → spawn → battle → gauntlet**, each crash → you fix → relaunch, advancing on each PASS. Your
   monitor fires on every `crash.ready` (the digest names the phase); you read the digest, fix the source,
   `./gradlew compileJava`, `touch $SIG_DIR/fix.done`; the phase relaunches. On `all-done`, every phase passed.

## Step 0 — Locate the JAR and triage difficulty
1. Resolve the input jar. If given a bare name, look in `$MODS_SOURCE_DIR`
   (from `.env.local`) and the CurseForge instances under it.
2. Inspect it WITHOUT decompiling yet (fast): `unzip -l` the jar and read
   `META-INF/mods.toml`. Record, in the mod's `MIGRATION.md`:
   - modId, version, MC/loader version range, declared dependencies.
   - Class count (`unzip -l | grep -c '\.class$'`).
   - **Mixins?** presence of `*.mixins.json` — hard; each mixin targets a vanilla
     class that may have changed 1.20→1.21.
   - **JarJar-nested mods?** (`META-INF/jarjar/*.jar`) — each nested lib is its own
     migration; check if a NeoForge 1.21.1 build of it already exists before porting.
   - **Custom shaders?** (`assets/*/shaders/`) — GLSL uniforms/format changed; risky.
   - **Capabilities / networking?** grep decompiled output later; both are full
     rewrites in NeoForge (see reference).
3. **Set expectations honestly.** Score difficulty: a small mod (tens of classes,
   no mixins/caps/networking) is a realistic same-session port. A large mod
   (hundreds of classes + mixins + capabilities + custom rendering, e.g. a ~750-file
   boss mod) is a multi-session effort — say so, and offer to prove the
   pipeline on a simpler mod first if the user wants a quick win. Then proceed.

## Step 1 — Scaffold the per-mod workspace
Create `mods/<modid>/` from `templates/neoforge-mod/` (see `references/pipeline.md`
for commands). Fill `gradle.properties` (mod_id, name, version, group, authors,
description) and flip `uses_mixins` / `uses_jarjar` / `uses_geckolib` as triage found.

## Step 2 — Decompile into the workspace, then remap SRG names
Decompile the jar with `tools/vineflower.jar` into `mods/<modid>/src/main/java`,
and copy `assets/` + `data/` into `src/main/resources/`. Keep a pristine raw copy
under `mods/<modid>/decompiled-raw/` (gitignored) so you can diff against it.

**Then remap SRG → official (do not skip for Forge 1.20.x jars).** A compiled
Forge 1.20.x mod uses official class names but **SRG member names**
(`m_20615_`, `f_19853_`); the raw decompile is full of them and won't compile
until remapped. Run `tools/srg-remap/` (see its README + `references/pipeline.md`
step 2b). Verify with `grep -rc '\bm_[0-9]\+_' src/main/java` — should drop from
thousands to a handful. This is the single most important step; everything else
assumes official names.

## Step 3 — Convert mod metadata
- `META-INF/mods.toml` → `src/main/resources/META-INF/neoforge.mods.toml`:
  loader stays `javafml`; drop the `forge` dependency, add `neoforge` + `minecraft`
  1.21.1 ranges; keep other deps but bump/verify their ranges.
- If mixins: add `[[mixins]] config="<modid>.mixins.json"` to the toml, copy the
  mixin json but **delete its `refmap` key** (NeoForge runs on official mappings).
- `pack.mcmeta`: bump `pack_format` to 34 (resources) / 48 (data) for 1.21.1.
- Delete `*.refmap.json` and the old `META-INF/jarjar/` (rebuilt by gradle).

## Step 4 — The build-error loop (the real work)
Iterate. Each pass: run the compile, bucket the errors, fix by category, repeat.

```
./gradlew compileJava --console=plain 2>&1 | tee /tmp/build.log
```
- First run downloads NeoForge + decompiles/recompiles Minecraft — slow (minutes),
  then cached. If it fails at the *dependency/setup* stage, fix the workspace
  (gradle/neoforge versions, repos) before touching mod code.
- Extract errors: `grep -E 'error:|\.java:[0-9]+:' /tmp/build.log`.
- **Bucket by symptom** and fix the whole bucket at once using `CATALOG.md`
  **Migration Pattern Catalog** (Pattern → Error → Fix). Any error NOT already in
  the catalog: fix it, then **record the new pattern in `$MIGRATE_WORKSPACE/catalog-additions.md`
  immediately** (format in Step 4b; the catalogue itself only changes through Step 7's PR).
  Highest-frequency, most-mechanical first:
  1. Import/package renames (`net.minecraftforge.*` → `net.neoforged.*`).
  2. `new ResourceLocation(...)` → `ResourceLocation.fromNamespaceAndPath/parse`.
  3. Event bus / `@Mod` constructor / `DeferredRegister` shape.
  4. `SynchedEntityData` builder, `saveAdditional`/`load` HolderLookup params.
  5. DataComponents (item NBT is gone), enchantments/effects as `Holder<>`.
  6. Capabilities → NeoForge capability + data-attachment system.
  7. Networking → payload/`StreamCodec` system.
  8. Mixins last — verify each target vanilla class/method still exists.
- Prefer **codemods over hand-edits** for the mechanical buckets: a scoped
  `grep -rl` + `sed`/`perl -pi` across `src/main/java` fixes hundreds of identical
  imports in one shot. Verify with a re-grep. Do the semantic buckets by hand.
- Track error count each pass in `MIGRATION.md`. If a pass doesn't reduce it,
  switch categories rather than grinding the same one.

### Static-pattern pre-flight — run the MANDATED catalog sweep (before you trust a green compile)
Many breaks compile clean and only crash at load/render/save. **Run the entire sweep in
`references/catalog-scans.md` now** — every §S/§R scan, not a sample. Under each header, any file it
prints is a HIT: fix it, or record it in `MIGRATION.md` as a verified false-positive with one line of
why (a REVIEW hit like a null-guarded advancement call, or a non-melee mob flagged for `ATTACK_DAMAGE`,
is "explain, don't fix"). The single worst offender it catches is the systematic **hoisted
`SPEC = BUILDER.build()`** decompiler artifact (catalog §A #3b — NPE `before spec is built` on first
config read). This sweep is the enforcement mechanism: the catalog is only useful if every entry is
actually checked, so checking is a mandated step, not left to judgment. Run it with
`bash ../../tools/run-catalog-scans.sh` from the port directory.

**Then run the override probe: `python3 ../../tools/override-probe.py .`** A decompiler drops `@Override`,
so when the target changed a method's parameters the old-shaped method still compiles as a NEW method that
nothing calls, and vanilla's default runs instead — no error, no crash, wrong behaviour. The probe adds
`@Override` everywhere it is missing, compiles, lists every method that overrides nothing, and restores the
files. Fix each one that was meant to override (correct its signature and keep the `@Override`); leave the
ones that were never overrides. Measured on a blind replay of a small MCreator downport: 16 dead
light-transparency overrides in 8 blocks, which the original port had shipped with. On a LIBRARY expect a longer
list that is mostly legitimate: a library's base classes declare methods for dependants to call, and those
override nothing by design. Read each hit rather than counting them: measured on a helper-heavy library,
154 candidates and 2 real. A fast filter is to grep each name in the patched Minecraft/NeoForge sources
(and any library jar the port extends); a name found nowhere there cannot be an override.

## Step 4b — Compile-clean retrospective (MANDATORY, the moment the error count hits 0)
The in-flight rule is "append every NEW pattern the moment you resolve it" — but under the pressure of a
1000-error backlog (and across parallel agents), that gets applied inconsistently. So the catalog does **not**
stay comprehensive on the honor system. The instant `compileJava` is clean, **stop and run this retrospective
as an explicit pass** — it is the backstop that guarantees every migration feeds its finds back, whether or not
they were logged live. Don't defer it to Step 6; do it here, while the diffs are fresh, before the gates.

**Inputs:** every commit made during this port (`git log --oneline` for the branch) **and** every parallel
agent's report. Walk them end to end — do not sample.

**For each distinct pattern you fixed, categorize it into exactly one bucket:**
- **(a) Known pattern, executed as documented** — it's already in the catalog (§A–§L / §R) and you applied the
  documented fix. No action. (Expect most fixes to be (a) — that's the catalog working.)
- **(b) Known pattern, but you had to go beyond the docs** — the entry existed but was incomplete, subtly wrong,
  had a second overload/case, or needed a non-obvious technique the entry didn't mention. **Action: AUGMENT the
  existing numbered entry** — write a `### augment <ENTRY-ID>` block whose text starts `· **AUGMENT — …:**`;
  don't add a new number.
- **(c) Net-new pattern worth documenting** — not in the catalog, and general enough that the next mod could hit
  it. **Action: a new numbered entry** — a `### new <SECTION>` block in the standard format (below), grouping
  tiny sibling renames into one cluster entry rather than one-per-line.
- **(d) Truly bespoke to this mod** — un-decompilable generated geometry, a mod-specific reconstructed method,
  one integration-wiring quirk. **Action: none in the catalog** (note it in `MIGRATION.md` if it matters for that
  mod's resume, but it's not general knowledge).

**Standard format for (b)/(c)** — the same three-line shape every catalog entry uses, so entries stay greppable:
`NN. **Short title** · **Pattern:** <old code shape> · **Error:** <the exact compileJava message> · **Fix:** <the NeoForge 1.21.1 replacement>`
(runtime-only patterns use `**Runtime:**` in place of `**Error:**` and go in §R). Cite a real file from this port.

**Then, for every (b)/(c) entry that has a static signature, ADD its grep to `references/catalog-scans.md`** (under
a `hit "§X/#NN …"` header) — an entry the sweep can't check is an entry the next migration will silently skip.

**A retrospective often finds a real bug, not just a doc gap:** writing an entry forces you to state the *correct*
fix, which can reveal that the port's actual fix was only good enough to compile. (A ~750-file boss mod: documenting the
ClipContext ambiguity — §K #93 — exposed 7 `(Entity)null` casts that compiled but leave the R10 runtime NPE
latent; the retrospective both wrote the entry and fixed the 7 sites.) When this happens, fix it now and note it.

Output a short (a)/(b)/(c)/(d) tally in `MIGRATION.md`. The lessons wait in `catalog-additions.md` until Step 7.

**Where lessons go, and the one rule that matters.** Write them to `$MIGRATE_WORKSPACE/catalog-additions.md`,
one block per lesson:

```
### new R
R99. **Short title** · **Pattern:** <old code shape> · **Runtime:** <crash line> · **Fix:** <fix>

### augment M6
· **AUGMENT — <what was missing>:** <the extra case, stated by its symptom>
```

**Describe the mod, never name it** ("a small MCreator food mod", not its name or id) and state every lesson by
its SYMPTOM (the error text a porter will search for). `tools/propose-learnings.py` refuses a lesson that names
the port it came from. A grep that finds the pattern goes in the entry as `· **Scan:** <grep>`; the reviewer
moves it into `references/catalog-scans.md`.

**Same pass, second output — harvest the TEST TARGETS, not just the patterns.** The commit history +
agent reports also tell you *which paths to test*: the files you changed most are where the migration
risk concentrated, so that's what the gates must actually exercise (not whatever the generic templates
happen to hit). Run the churn method in **`references/test-targets.md`** (rank files by how many commits
touched them → bucket by subsystem → map each heavy bucket to the cheapest gate + symmetry oracle) and
write the ranked **P0/P1/P2 target list into `MIGRATION.md`**. Core rule: **the highest-frequency pattern
deserves the highest-coverage test** — a change spread across N files (e.g. `StreamCodec` on every payload)
is proven by ONE parameterized round-trip over all N, worth far more than a bespoke test of one file. This
list is the input to Step 5 — build the gate tests against it.

## Step 5 — Three test gates (escalating), then deploy (a clean compile is NOT a clean load)
All three gates are required before calling a port done — see pipeline.md §6 for exact setup.
The progression climbs the crash surface: **compile → static scan → Gate A (pure logic) → Gate B
(headless server load + tick) → Gate C (real client: load → entities → combat → items/UI).** Each
gate catches a class the one before it structurally cannot — a clean compile lies, a green GameTest
never renders a frame, and an idle mob never lands a melee hit. The runtime-crash catalog these
gates exist to catch is repo `CATALOG.md` **§R (R1–R11)** — read it; every entry names the gate that
finds it.

**Build the gate tests against the churn-derived target list, not the generic template as-is.** Step 4b
already wrote the P0/P1/P2 target list into `MIGRATION.md` (method: `references/test-targets.md`) — the
subsystems you modified most, mapped to the cheapest gate + symmetry oracle. Fill the templates below so
they **parameterize over those buckets** (all payloads, all serializable types, all `Codec`s), so coverage
tracks what the port actually rewrote. The templates are the *shape*; `test-targets.md` decides *what goes in*.
- **Gate A — Minecraft-free JUnit** (`./gradlew test`, part of `build`; the template already wires JUnit):
  - **Every port:** `ResourceIntegrityTest` — no pre-1.21 plural datapack dirs, every JSON strictly valid,
    recipe ingredients in the target's form. These are the data-layer failures that load clean and leave the
    content silently missing.
  - **Mods with mixins, additionally:** `MixinConfigIntegrityTest` — every mixin in `<modid>.mixins.json` has
    a compiled class (catalog R/#43, the invisible `ClassCastException`).
  - Plus pure-logic tests for anything the churn list (Step 4b) flags as portable.
  Copy the templates from `templates/neoforge-mod/test-templates/` and set their package line.
- **Gate B — GameTest runtime load** (`./gradlew runGameTestServer`): boots a headless
  server with the mod, spawns a mob, resolves+uses a spawn egg, and drives an item via a
  mock player. This is the ONLY thing that catches the §R runtime crashes (config-at-
  registration, unbound holders, register-with-no-subscribe, **and client-config-read-on-
  server, R5**). Needs a run config + an empty structure NBT
  (`python3 tools/gen-empty-structure.py <modid>`). Iterate on the log until
  **"All N required tests passed :)"**.
- **Then extend Gate B over the complex subsystems** (standard, not optional — pipeline.md §6c):
  a second `@GameTest` class **built against the P0/P1 target list Step 4b wrote into `MIGRATION.md`**
  (method: `references/test-targets.md`) — i.e. the buckets *this* mod rewrote most, parameterized over
  the whole bucket. Typically: **spawn EVERY mob and let them tick** (catches R5), data-attachment
  round-trips, networking `StreamCodec` symmetry, particle/recipe/criteria codecs, config-driven
  attributes/tiers. This is where a clean compile lies; each failure is a real bug (one port's spawn-all
  found a dedicated-server crash). Reuse the `RegistryFriendlyByteBuf` + `BuiltInRegistries.ENTITY_TYPE`
  idioms from a comprehensive subsystems GameTest.
- **And a persistence round-trip GameTest** (standard — `test-templates/PersistenceGameTest.java.example`;
  a **P0 target** in every mod's list, since every serializable type rewrote `saveAdditional` for the new
  `HolderLookup.Provider`): save→load→re-save **every serializable entity and every block entity**
  (namespace-driven, no per-mod edits). Catches the whole NBT-serialization crash class (**R12**) — `addAdditionalSaveData`/`saveAdditional`
  that NPE on a null field or are asymmetric with their read — which NOTHING else exercises, since no other
  gate serializes. It creates the ownerless/default instances that normal play never saves (one port's
  found an icicle projectile that crashed chunk-save when it had no owner).
- **Write `MANUAL_VALIDATION.md`** (standard deliverable — pipeline.md §6d): a priority-tagged
  (P0/P1/P2) checklist of what GameTest can't reach — rendering, client mixins, audio, GUIs,
  ability kits, mutation/effect mechanics, live MP payload dispatch, and each dropped feature.
  Model: a per-port `MANUAL_VALIDATION.md` (P0/P1/P2 checklist).
  - **⚠️ MAINTAIN IT AS A LIVING DOCUMENT DURING THE PORT — do NOT reconstruct it only at Step 5.**
    Create `MANUAL_VALIDATION.md` at Step 1 and **update it as you migrate each risky subsystem**:
    the moment you touch networking / rendering / a mixin / a shader / capabilities / a GUI, add (or
    tick) its P0/P1 client-verification line + which Gate-C mode drives it, and record every **feature
    dropped or design decision** (e.g. "capability mixin removed → verify entity-extension persistence
    in the attachment path") while the change is fresh. Why: on a big multi-turn/multi-session port,
    end-of-run reconstruction silently misses subsystems and forgets the drops; a living plan keeps
    Gate-C coverage tracking exactly what the port actually rewrote, and doubles as the resume record
    of what still needs client verification. The churn analysis (Step 4b) then *augments* this living
    plan, it doesn't create it from scratch.
- **Gate C — client boot loop, 4 escalating modes** (standard, not optional — pipeline.md §6e): GameTest
  is a headless dedicated server; it never runs a client mixin, bakes a model, renders an entity, or
  ticks an item property. Gate C is a property-gated client tick hook (`ClientBootSmokeTest`) that drives
  the real client with **no manual input**. The human runs **ONE command — from the mod dir,
  `./tools/client-validate.sh`** (per-mod: `mods/<modid>/tools/client-validate.sh`) — which chains all four
  phases (via the sibling `client-boot-loop.sh`), each looping crash → you fix → relaunch until it passes,
  then advancing. The 4 modes climb the client surface — each a superset gate:
    - **`launch`** — reach the title screen + auto-quit. The client-LOAD surface: all client mixins applied,
      every renderer/layer/particle registration fired, models baked, `FMLClientSetupEvent` ran. (Caught
      the `@EventBusSubscriber(Dist.CLIENT)`-with-no-`@SubscribeEvent` crash a server GameTest can't — R1.)
    - **`spawn`** — create a world + spawn ONE of every `<modid>:*` entity, tick ~10s. Each entity's ctor,
      AI goals, and client renderer/model. (R6: advancement NPE on player tick; R7: custom-packet decoder.)
    - **`battle`** — N of every creature in two teams fighting around the player, ~30s. Combat, AoE, projectiles,
      death sequences, particles — all in-frustum. (R8 entityData-in-defineSynchedData, R9 missing ATTACK_DAMAGE,
      R10 null-entity ClipContext — none reachable without mobs actually landing hits.)
    - **`gauntlet`** — the item/UI axis: build every tooltip, render every item model + `ItemProperties` overrides,
      open custom GUIs, wear every armor piece in 3rd-person, place every block, `use()` every item, apply every
      effect. (R11 render-time property-override NPE; missing custom-armor textures — only visible when worn.)
  Needs a real display (GLFW fails headless) — run it in a desktop terminal, or `xvfb-run` on Linux CI. When you
  truly can't run a client, leave the P0 items on the manual list — but Gate C is where most post-load bugs live.
  **You (the agent) can't run it yourself** (no display), so: arm a monitor over the shared signal dir, hand the
  human the one command, and fix crashes as they surface across ALL phases. **Use `tools/watch-gatec.sh`** — do
  NOT hand-roll the poll loop (a loop ENDING in `[ -f "$SIG/crash.ready" ]` exits 1 on the SUCCESS path, so a
  passing run is mislabeled "failed"). The script exits with an accurate code — **0 = all-done (passed), 1 =
  crash, 3 = timeout** — and prints the crash digest:
  ```bash
  SIG_DIR=/tmp/<modid>-clientloop ./tools/watch-gatec.sh 900   # background it; re-invoke after each fix
  ```
  On a `RESULT=CRASH` (exit 1): read `$SIG_DIR/crash.log` (it names the phase + the crash), fix the source,
  `./gradlew compileJava` (the loop's relaunch recompiles too), then `touch $SIG_DIR/fix.done` to relaunch that
  phase, and re-run the watcher. Repeat until `RESULT=ALL-DONE` (exit 0). Run it as a background watch so you're
  notified per crash, not polling.
- Then `./gradlew deployToMods` (runs `build`, which includes Gate A's JUnit tests — **not** Gate B, which you
  run separately with `runGameTestServer` — then copies to `$MINECRAFT_MODS_DIR`; **atomic rename, so it's safe
  to deploy while a game is open** — MC just needs a restart to pick it up).
- Client-only surface (rendering, client mixins, GUIs) still needs a real client: launch
  `./gradlew runClient` or read `$MINECRAFT_LOGS_DIR/latest.log`, then tick the P0 items in
  `MANUAL_VALIDATION.md`.

## Step 6 — Final catalog sweep + record status
- **Re-run the full `references/catalog-scans.md` sweep** — the build loop reintroduces patterns
  (a re-added config `.get()`, a new packet, a new entity). The port is not done until every section
  is clean or every hit is a recorded false-positive. This is the pre-flight sweep's second, mandatory pass.
- **Re-run the Step 4b retrospective for anything the gates surfaced.** Step 4b harvested the compile-fixing
  work; the gates (and the client boot loop) resolve their own §R runtime patterns after that. Walk the
  gate-phase fixes through the same (a)/(b)/(c)/(d) buckets and **augment/append** the catalog + `catalog-scans.md`
  for every (b)/(c) — same standard format, runtime ones use `**Runtime:**` and land in §R. The catalog only
  stays comprehensive if every migration feeds its finds back, at both the compile line AND the runtime gates.
- Keep `mods/<modid>/MIGRATION.md` current: what's done, current error count, the specific blockers,
  which gates are green, and the next concrete action. This is the resume point for the next session —
  a big port survives across many runs through this file.

## Step 7 — Deliver (MANDATORY — the port is NOT done until both of these ran)
Two separate deliveries, to two separate places. Exact commands: `references/pipeline.md` §9.

1. **The port → the mods destination.** `python3 tools/finish-port.py <modid>` copies the port's source
   (not build output, runs or the pristine decompile) into `$MOD_OUTPUT_REPO/mods/<modid>/` and commits it
   on the branch `port/<modid>` there; `--push` pushes it. With no destination configured it says so and
   leaves the port in the workspace. It REFUSES a destination inside this repository: ported mods are
   somebody else's code and this repository is public.
2. **The lessons → this repository, as a PR.** `python3 tools/propose-learnings.py --modid <modid> --push`
   applies `catalog-additions.md` to `CATALOG.md` on a `learnings/*` branch, runs the IP and fidelity gates
   with this port's own identity added to the forbidden names, and opens the PR. A port with no (b)/(c)
   lessons skips this — say so in `MIGRATION.md`.

**Why this is mandatory.** Finished ports have sat on local-only branches for weeks, including one whose
jar the user was actively playing with, and one in a `/tmp` directory the OS purges on its own schedule. A
port costs hours to rebuild and cannot be recovered from a jar. And a lesson that never leaves the
workspace is paid for again by the next porter.

- **A deploy is not delivery.** If the jar is in the user's `mods/` folder, its source must already be in
  the destination.
- Never leave the only copy of a port in `/tmp`.

## Step 8 — Abandoning a path (MANDATORY ritual — abandoned ≠ deleted, ≠ left lying around)
Stopping a port is often correct. Leaving it *indistinguishable from live work* never is. Full
checklist in `references/pipeline.md` §10. The three required actions:

1. **In the mods destination, merge to `main`, then DELETE the branch** (local + `origin`). Verify
   `git rev-list --count origin/main..<branch>` is `0` first. Merging is what gets the source into
   history; the dangling branch is pure ambiguity.
2. **DELETE `mods/<modid>/` from `main` too** — `git rm -r`, and record the **recovery SHA** (the
   last commit containing it) in the relevant `plan.json`/status file, with the literal
   `git checkout <sha> -- mods/<modid>` line. **This does not lose the source** — it stays in git
   history on `origin`. Abandoned ports must not sit in the working tree: a directory that looks
   exactly like a live port is precisely what makes a superseded path get mistaken for real work.
   Before removing, confirm (a) nothing outside it references it, and (b) whatever knowledge it
   produced (catalog deltas, docs) already lives **outside** the directory.
3. **Fix every status file that now lies** — `plan.json`, roadmaps, READMEs.

Write `ABANDONED.md` *before* removing if you want the reasoning captured in history at the recovery
SHA, but the durable record is the status file that survives: it must state the decisive reason
(strategic beats technical — *"even green it yields no content"* > *"281 errors"*), what replaced it,
and the recovery SHA.

**Why:** a `plan.json` frozen mid-story called the framework-library + furniture-mod 1.20.1 path `"abandoned_path"` after a
pivot. The work later went back and *finished* that path — it is what ships in the player's game — but
the file was never updated. Ten days on, that stale label led a session to merge the superseded
downport as "a GREEN library port" without noticing it had **no consumer**. Pure documentation cost.

## When you get stuck
- Re-read the two API references — most walls are a category you haven't applied yet.
- Search the web for the specific NeoForge 1.21.1 replacement of an API (the
  ecosystem is well documented; NeoForge docs + the primer are authoritative).
- A missing nested/library dependency (e.g. a config library the mod bundles) blocks everything —
  migrate or locate a NeoForge build of it first, as its own `mods/<lib>/`.
- Genuinely stuck (e.g. an API with no 1.21 equivalent) → stub it minimally to keep
  compiling, note it in `MIGRATION.md`, and surface it to the user.
