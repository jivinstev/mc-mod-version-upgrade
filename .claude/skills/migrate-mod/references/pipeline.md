# Pipeline — exact commands

All paths are relative to the **migration workspace**, `$MIGRATE_WORKSPACE` (default
`~/.mc-mod-upgrade/work`): `cd "$MIGRATE_WORKSPACE"` first. `./setup --path migrate` lays it out as
`mods/<modid>/` for ports, plus `tools/`, `templates/` and `.env.local` linked back to this
checkout, so every command below works unchanged there. It lives outside any git repository on
purpose: decompiled mods must never be one `git add -A` away from a commit. `MODID` = the mod's id
(e.g. `examplemod`). Read machine paths from `.env.local`.

**Target parameters** (see SKILL.md → "Target parameters"). Commands below are written for the
default target — substitute if the caller specified otherwise:
`SRC_LOADER=forge  SRC_MC=1.20.1  →  DST_LOADER=neoforge  DST_MC=1.21.1` (`DST_DATAVERSION=3955`,
`pack_format` 34/48 for 1.21.1). Set the workspace version knobs in
`templates/neoforge-mod/gradle.properties` (`minecraft_version`/`_range`, `neo_version`, `parchment_*`)
to `DST_MC` before building. For a `DST_MC` other than 1.21.1, look up its `DataVersion` + `pack_format`
(see `references/minor-version-deltas.md`).

## Ensure tools
```bash
bash tools/download-tools.sh          # fetches + sha1-verifies tools/vineflower.jar and tools/cfr.jar
```

## 0. Locate + inspect the jar (no decompile yet)
```bash
JAR="<path to original .jar>"          # or search $MODS_SOURCE_DIR for it
unzip -l "$JAR" | grep -c '\.class$'                       # size / difficulty
unzip -p "$JAR" META-INF/mods.toml                          # metadata + deps
unzip -l "$JAR" | grep -E 'mixins\.json|jarjar/.*\.jar|shaders/'   # hard bits
```

## 1. Scaffold the workspace
```bash
mkdir -p mods/$MODID
cp -R templates/neoforge-mod/. mods/$MODID/
rm -rf mods/$MODID/test-templates   # Step 6 copies from the checkout's templates; a copy here would ship with the port
# personalize settings.gradle + gradle.properties
perl -pi -e "s/MOD_ID_PLACEHOLDER/$MODID/" mods/$MODID/settings.gradle
$EDITOR mods/$MODID/gradle.properties     # mod_id, mod_name, mod_version, group, authors, description, uses_* flags
```

## 2. Decompile + place sources and resources
```bash
mkdir -p mods/$MODID/decompiled-raw
java -jar tools/vineflower.jar -dgs=1 -rsy=1 -rbr=1 "$JAR" mods/$MODID/decompiled-raw
# Java sources -> src/main/java   (the top package dir; e.g. com/)
# (cp + find rather than rsync, which is not installed everywhere)
(cd mods/$MODID/decompiled-raw && find . -name '*.java' -exec sh -c \
      'mkdir -p "../src/main/java/$(dirname "$1")" && cp "$1" "../src/main/java/$1"' _ {} \;)
# Resources -> src/main/resources  (assets, data, pack.mcmeta, mixin/mods metadata)
cd mods/$MODID/decompiled-raw
for x in assets data pack.mcmeta *.json; do [ -e "$x" ] && cp -R "$x" ../src/main/resources/; done
# META-INF: everything EXCEPT the mods.toml. Keep the TEMPLATE's neoforge.mods.toml (parameterised from
# gradle.properties); the jar's is the old loader's, still in decompiled-raw/, and §3 converts it into the template's.
[ -d META-INF ] && find META-INF -type f ! -name '*mods.toml' -exec sh -c \
    'mkdir -p "../src/main/resources/$(dirname "$1")" && cp "$1" "../src/main/resources/$1"' _ {} \;
cd -
# Remove Forge-era metadata that Gradle regenerates or NeoForge rejects:
rm -f mods/$MODID/src/main/resources/*.refmap.json
rm -rf mods/$MODID/src/main/resources/META-INF/jarjar
rm -f  mods/$MODID/src/main/resources/META-INF/MANIFEST.MF
```
Vineflower flags: `-dgs=1` decompile generic signatures, `-rsy=1` remove synthetic,
`-rbr=1` hide bridge methods.

### 2b-fabric. Remap INTERMEDIARY names → official (when `SRC_LOADER = fabric`)
A compiled **Fabric** mod is remapped to the *intermediary* namespace (`MANIFEST.MF:
Fabric-Mapping-Namespace: intermediary`), so the decompile is full of `net.minecraft.class_1799`,
`method_7909`, `field_8125` — **not** SRG. `tools/srg-remap` is a no-op on it. Use its Fabric twin:
```bash
python3 tools/intermediary-remap/build_mapping.py "$SRC_MC" tools/intermediary-remap/intermediary2official-"$SRC_MC".json
python3 tools/intermediary-remap/apply_mapping.py  tools/intermediary-remap/intermediary2official-"$SRC_MC".json mods/$MODID/src/main/java
grep -rc '\b\(class\|method\|field\)_[0-9]\+' mods/$MODID/src/main/java   # expect 0
```
Then read catalog **§P (#145–#161)** for the rest of the Fabric axis (entrypoints, eager registration,
Cloth config, the Fabric API table, accesswidener→AT, **Yarn mixin `method=` targets**, `data/fabric/tags`).

### 2b. Remap SRG member names → official (CRITICAL for Forge ≤1.20.x jars; SKIP for 1.20.5+/1.21.x sources)
A compiled Forge ≤1.20.x mod uses **official class names but SRG method/field names**
(`m_20615_`, `f_19853_`) — the decompiled source is full of them and will NOT
compile against NeoForge's official names until remapped. SRG ids are globally
unique, so a flat text replacement is safe. Uses `SRC_MC` (default `1.20.1`):
```bash
python3 tools/srg-remap/build_mapping.py "$SRC_MC" /tmp/srg2official-"$SRC_MC".json    # SRC_MC default 1.20.1
python3 tools/srg-remap/apply_mapping.py  /tmp/srg2official-"$SRC_MC".json mods/$MODID/src/main/java
```
**SRG-skip rule:** skip this entire step when the source is already official-mapped — any **1.20.5+ /
1.21.x** jar, or a NeoForge source (e.g. a 1.21.x→1.21.y downport). Confirm with the grep below (a
handful/zero ⇒ skip; thousands ⇒ remap).
Quick check it's needed / worked: `grep -rc '\bm_[0-9]\+_' mods/$MODID/src/main/java`
(thousands before, a handful of Forge-injected leftovers after). Skip this step
only if a `grep` shows the decompiled output already has no `m_*/f_*` names (rare;
a NeoForge/Fabric-official-mapped jar).

### 2c. Static-pattern pre-flight (scan the decompiled source BEFORE compiling)
**Run the full mandated sweep in `catalog-scans.md` — the whole thing, every §S/§R scan.** The
snippets below are the highest-value ones inline; `catalog-scans.md` is the authoritative, complete,
runnable list (and the one you re-run as a done-gate in Step 6). Some breaks compile fine and only
bite at runtime — catch them now by grepping. The worst is systematic, so fix it across the whole
tree in one pass (catalog **§A #3b**):

```bash
# 🔴 Decompiler HOISTS `SPEC = BUILDER.build()` above the .define() fields in ModConfigSpec
# classes -> spec builds empty -> every ConfigValue gets a null spec -> runtime NPE
# "Cannot get config value before spec is built" on first .get(). List the offenders:
for f in $(grep -rl 'BUILDER.build()' src/main/java); do
  spec=$(grep -nE 'SPEC *= *[A-Za-z_]*BUILDER\.build\(\)' "$f" | head -1 | cut -d: -f1)
  last=$(grep -nE '\bBUILDER\b' "$f" | grep -vE 'new Builder|\.build\(\)' | tail -1 | cut -d: -f1)
  [ -n "$spec" ] && [ -n "$last" ] && [ "$spec" -lt "$last" ] && echo "HOISTED: $f"
done
```
Fix each by moving the `public static final ModConfigSpec SPEC = BUILDER.build();` line to
**immediately before the class's closing `}`** (after every `define/comment/push/pop`).
⚠️ Do NOT "insert after the last line containing BUILDER" — multi-line `defineList(` has
`BUILDER` on its opening line and you'll split the statement. Then re-run the scan → 0.

Also eyeball for the runtime-only registration traps (catalog **§R**) so you fix them while
editing rather than in a crash loop later:
- `EVENT_BUS.register(this|X.class)` **OR** a `@EventBusSubscriber` annotation on a class with **no**
  `@SubscribeEvent` methods → remove it (R1). ⚠️ a `@EventBusSubscriber(value={Dist.CLIENT})` one
  crashes only the *client* — a server GameTest passes. Scan both:
  `grep -rln 'EVENT_BUS.register\|@EventBusSubscriber' src/main/java` then for each `@EventBusSubscriber`
  file check `grep -cE '^[[:space:]]*@SubscribeEvent'` is > 0.
- Config `.get()` inside a `DeferredRegister` supplier / item / `ArmorMaterial` **constructor** → bake the `.define(..,default)` value (R2).
- `wrapAsHolder(OtherInit.X.get())` (forcing a cross-registry Holder) in a registration supplier → pass the `DeferredHolder` lazily (R3).
- A CLIENT config `.get()` (`*ClientConfig.X.get()`) reachable from server-tickable code (entity `tick`/`baseTick`, common event) without an `isClientSide`/`@OnlyIn(CLIENT)` guard → crashes dedicated servers (R5).

## 3. Metadata conversion
- Move `src/main/resources/META-INF/mods.toml` → `neoforge.mods.toml`, edit per
  the template's version (drop the `SRC_LOADER`/`forge` dep, add `DST_LOADER`/`neoforge`
  + `minecraft` `DST_MC`, default `1.21.1`). (If `SRC_LOADER == DST_LOADER`, the toml is
  already the right shape — just bump the version ranges.)
- In `<modid>.mixins.json`, delete the `"refmap"` line; ensure `compatibilityLevel`
  is at least `JAVA_21`.
- `pack.mcmeta`: set `pack_format` for `DST_MC` — **1.21.1 = 34 (assets) / 48 (data)**.
  Re-verify for any other `DST_MC` (several 1.21.x bumps exist).

## 4. Build loop
```bash
cd mods/$MODID
./gradlew --offline compileJava --console=plain 2>&1 | tee /tmp/$MODID.log   # (drop --offline on first run)
grep -E '\.java:[0-9]+: error' /tmp/$MODID.log | wc -l                        # error count
grep -E '\.java:[0-9]+: error' /tmp/$MODID.log | sed -E 's/.*error: //' | sort | uniq -c | sort -rn | head -40   # buckets
```
**First build: Maven Central can answer HTTP 429.** A rate limit, not a policy denial; retrying rarely
helps. Point the whole build (including NeoGradle's detached configurations, which repository order does
not reach -- catalogue V9) at a Central mirror with the shipped init script:
`./gradlew --init-script "$(cd -P ../../tools && pwd)/central-mirror.init.gradle" compileJava`.

**A dependency whose maven this machine cannot reach** (an `EXTRA_MAVENS` host off the egress allowlist):
fetch the jar through the registry instead -- `python3 ../../tools/mod-registry/modreg.py download ... --out libs/` -- and depend on it with `compileOnly files('libs/<jar>')` (+ `localRuntime` for the run
configs). `libs/` is gitignored by the template: the jar is third-party and never leaves the workspace;
MIGRATION.md records the exact modreg command so the next session can fetch it again.

Mechanical codemods, run once over the mod's sources before bucketing errors (Forge sources only for
the first; the second for any 1.20.x → 1.21 port):
```bash
grep -rl 'net.minecraftforge\|net/minecraftforge' src/main/java \
  | xargs perl -pi ../../tools/srg-remap/forge_import_codemod.pl    # packages, dotted AND descriptor (L.../...;) form
python3 ../../tools/srg-remap/mc121_codemod.py src/main/java          # 1.20 -> 1.21 renames, incl. new ResourceLocation(..)
```
`mc121_codemod.py` rewrites `new ResourceLocation(a)` to `parse(a)` and `new ResourceLocation(a, b)` to
`fromNamespaceAndPath(a, b)` by counting top-level arguments, so a comma inside a nested call is safe. Both
are text rewrites: re-grep afterwards, and read `forge-to-neoforge.md` for what they do not cover.

## 5. Build the jar
```bash
./gradlew build --console=plain      # compiles + assembles build/libs/$MODID-<version>.jar
# verify the jar is self-contained:
JAR=$(ls build/libs/*.jar | grep -viE 'sources|slim' | head -1)
unzip -l "$JAR" | grep -E 'mixins\.json|neoforge.mods.toml|META-INF/jarjar/.*\.jar'
```
A green build proves the mod **compiles** — NOT that it loads. Do not deploy or call it
"done" before the two test gates below pass.

## 6. Tests — TWO gates (both required before "done")
A clean compile hides two whole classes of bug. Add both gates to every port.
**What to put IN the subsystem/round-trip tests is decided by the churn-derived P0/P1/P2 target list from
Step 4b** (`MIGRATION.md`; method in `references/test-targets.md`) — build against what this mod rewrote
most, not against the template's example cases. The mixin/baseline-load gates below are universal; the
subsystem coverage (§6c) is churn-driven.

### 6a. Gate A — Minecraft-free JUnit (resource integrity for every port; mixin integrity if it has mixins)
The template's `build.gradle` already declares JUnit and a `test` task that passes `migrate.projectDir` and
`migrate.minecraftVersion` to the tests. Copy `test-templates/ResourceIntegrityTest.java.template` for EVERY
port (plural datapack dirs, strict JSON, recipe-ingredient form for the target).

The mixin half guards the invisible-to-javac crash where a mixin is added but not listed in
`<modid>.mixins.json` (catalog R/#43 → runtime `ClassCastException`). For a build.gradle that does not
come from the template, the wiring is:
```groovy
dependencies {
    testImplementation platform('org.junit:junit-bom:5.10.2')
    testImplementation 'org.junit.jupiter:junit-jupiter'
    testRuntimeOnly 'org.junit.platform:junit-platform-launcher'
}
tasks.named('test', Test) { useJUnitPlatform(); testLogging { events 'passed','failed','skipped' } }
```
Copy `templates/neoforge-mod/test-templates/MixinConfigIntegrityTest.java.template` to
`src/test/java/<pkg>/MixinConfigIntegrityTest.java`; set its package + the
`<modid>.mixins.json` filename. It's wired into `build`, so `deployToMods` gates on it.
```bash
./gradlew test --console=plain        # "everyMixinInConfigHasACompiledClass() PASSED"
```

### 6b. GameTest — real headless runtime load (the gate that actually loads the mod)
Boots a dedicated server with the mod, runs `@GameTest` methods, exits non-zero on failure.
This is the ONLY thing that catches the §R runtime crashes. Set it up once:
```bash
# (i) run config — the template's runs {} already has it; for another build.gradle add:
#     gameTestServer { systemProperty 'neoforge.enabledGameTestNamespaces', project.mod_id }
# (ii) empty test structure (NeoForge 21.1 has no @EmptyTemplate; framework needs a real .nbt):
#      last arg is DST_DATAVERSION (1.21.1 = 3955; look up the value for any other DST_MC):
python3 ../../tools/gen-empty-structure.py $MODID empty_test 9 "${DST_DATAVERSION:-3955}"
#     -> src/main/resources/data/$MODID/structure/empty_test.nbt
```
Write `src/main/java/<pkg>/test/<Mod>GameTest.java` (in the **main** source set — GameTests
are scanned at runtime, not the JUnit set). Model it on
`templates/neoforge-mod/test-templates/ModGameTest.java.example`
(`@GameTestHolder(modid)` + `@PrefixGameTestTemplate(false)`, `@GameTest(template="empty_test")`).
Cover the highest-signal runtime paths:
- **spawn a mob** — `helper.spawnWithNoFreeWill(TYPE, pos)` runs ctor + AttributeSupplier + `registerGoals` (where most 1.21 goal/pathfinding breaks live)
- **use a spawn egg** — assert `SpawnEggItem`, `egg.getType(new ItemStack(egg))` resolves, spawn it
- **use an item** — `helper.makeMockPlayer(GameType.SURVIVAL)` → set item in hand → `player.attack(mob)` drives the combat path

```bash
./gradlew runGameTestServer --console=plain 2>&1 | tee /tmp/$MODID-gt.log
```
Debug loop — each load crash points at the next pattern; grep the log and fix:
```bash
grep -nE 'Cannot get config value|unbound value|has no @SubscribeEvent|Failed to start|ModLoadingException' /tmp/$MODID-gt.log
grep -E 'com\.<group>\..*\.<init>|lambda\$' /tmp/$MODID-gt.log | sort -u   # the offending class:line
```
Repeat until: **"All N required tests passed :)"** — the mod loads on a real server and
those code paths run. (Server-side only; client-only mixins still need a client.)

### 6c. Cover the complex subsystems (comprehensive GameTests — standard, not optional)
The baseline three prove the mod *loads*. Now aim tests at the **most-rewritten** subsystems —
that's where a compile lies. **"Most-rewritten" is not a guess: use the churn-derived P0/P1/P2 target
list that Step 4b wrote into `MIGRATION.md`** (method + worked example in `references/test-targets.md`).
Parameterize each test over the *whole* bucket it targets (every payload, every serializable type, every
`Codec`) so coverage tracks what the port actually changed. Add a second `@GameTest` class (model:
`test-templates/ModGameTest.java.example` + a comprehensive subsystems GameTest)
covering whichever of these the mod has:
- **Spawn EVERY mob, not one** — iterate `BuiltInRegistries.ENTITY_TYPE`, filter
  `namespace==modid && getCategory()!=MISC`, `helper.spawn` each, assert alive, `discard()`.
  **Let them tick** (don't discard same-tick for at least one) — this is what catches **R5**
  (client-config read in `baseTick` crashing a dedicated server).
- **Data attachments** (ex-capabilities): reachable via `entity.getData(TYPE)`; the data class's
  `saveNBTData`/`loadNBTData` round-trips. (Mind clamps — assert with in-range values.)
- **Networking payloads**: `STREAM_CODEC.encode` → `decode` → re-`encode`, assert the two byte
  arrays match (symmetric; needs no getters). Build the buf with
  `new RegistryFriendlyByteBuf(Unpooled.buffer(), helper.getLevel().registryAccess())`. Exercises
  the item codec (`ItemStack.OPTIONAL_STREAM_CODEC`) for item-carrying packets.
- **Custom particle codecs**: round-trip the `MapCodec`/`StreamCodec`.
- **Config-driven attributes/tiers**: assert a mob's `getMaxHealth()` matches its configured
  default, and any config-reloadable object actually got populated (a Tier reading config in an
  event listener silently stays at 0 if the bus/event wiring is wrong — assert non-zero).
- **Persistence round-trip** (its own class — `test-templates/PersistenceGameTest.java.example`):
  save→load→re-save **every serializable entity and every block entity** (namespace-driven, drop-in).
  `type.canSerialize()` filter; `entity.saveWithoutId(tag)` → `type.create(level).load(tag)` → re-save;
  block entities via `helper.setBlock` + `saveWithFullMetadata`/`loadWithComponents`. Catches **R12**
  (NBT save NPE on a null field / asymmetric save↔load) — a whole class nothing else touches, since no
  other gate serializes. Creates the ownerless/default instances normal play never saves.

Each failure is a real bug that compiled cleanly (one port's spawn-all test found the R5
dedicated-server crash; the persistence test found an icicle projectile that crashed chunk-save with
no owner; the tier test found a config-reload wiring risk). Iterate to green.

### 6d. Write `MANUAL_VALIDATION.md` (standard deliverable — the untestable rest)
GameTest is a headless dedicated server: it can't see rendering, client mixins, audio, GUIs, or
long AI-driven behavior. Enumerate those in `mods/$MODID/MANUAL_VALIDATION.md` as a **priority-
tagged checklist** (P0 crash/unusable, P1 feature silently broken, P2 cosmetic) so a human (or a
client launch) can finish the job. Model: a per-port `MANUAL_VALIDATION.md` (P0/P1/P2 checklist). Typical buckets:
client load + per-mob rendering + layers, armour/skull rendering, particles' *looks*, audio &
music (sound-accessor mixins), GUI screens, each mob's ability kit, the mutation/effect mechanics,
attachment persistence across death, live multiplayer payload dispatch, and each **dropped**
feature (confirm absent-without-crash). Re-check the P0 items against a real client/MP session.

### 6e. Gate C — client boot loop (STANDARD, not optional): load → entities → combat → items/UI
GameTest is a headless dedicated server: no client mixins, no model bake, no rendering, no item
property ticks. Gate C is a client-only, property-gated tick hook (`ClientBootSmokeTest`) that
drives the REAL client with no manual input, escalating through 4 modes. Set it up once, run each mode.
- **The harness** (`test-templates/ClientBootSmokeTest.java.example` → `src/main/java/.../test/ClientBootSmokeTest.java`):
  a `ClientTickEvent.Post` state machine, inert unless `-D<modid>.boottest=true`. Mode via `-D<modid>.testmode=`.
  It creates a fresh Creative world (difficulty NORMAL so hostiles persist) with
  `mc.createWorldOpenFlows().createFreshLevel(...)`, drives the mode, prints `PASS`, and `mc.stop()`s.
  Most of it is modid-agnostic (it iterates `BuiltInRegistries.{ENTITY_TYPE,ITEM,BLOCK,MOB_EFFECT}`
  filtered to your namespace); the only mod-specific part is the `gauntlet` OPEN_GUIS step — point it
  at your mod's custom `Screen`s or **delete it entirely (step + enum entry + `Screen`/`Menu` imports) if the
  mod ships no GUIs** — leaving imports for classes that don't exist fails the client compile (a ~380-file MCreator mob mod:
  no GUIs → deleted the step).

- **⚠️ ANTI-HANG CHECKLIST (Gate C's failure mode is a hang, not a crash — every silent-hang cause below is
  now handled by the template/harness; this is why + how to verify, since a regression reads as "still loading"):**
  1. **build.gradle `-P`→systemProperty wiring** (below) MUST be present. It is now in the template `client` run
     block (parameterized by `mod_id`), so a fresh scaffold has it. **Without it the smoke test never activates**
     (`ENABLED` stays false) → the client boots to the title and just SITS there → the 600s watchdog fires. This
     compiles + launches fine, so it's the easiest hang to misdiagnose (the first large port to hit it blamed a locked screen).
  2. **`caffeinate`** — a slept/locked macOS display stalls GLFW/OpenGL and the client never reaches the title
     (a large boss mod's *first* real hang). `client-boot-loop.sh` now prefixes `runClient` with `caffeinate -dimsu`
     when available; still, tell the human to keep the Mac awake + unlocked for the run.
  3. **AccessibilityOnboardingScreen auto-dismiss** — a fresh `run/` dir has `onboardAccessibility=true`, so MC
     opens that screen *before* the title; it is not a `TitleScreen`, so a boot test that only waits for `TitleScreen`
     waits forever. The template smoke test dismisses it (`onboardAccessibility=false` → save → `setScreen(TitleScreen)`).
  4. **Watchdog + world-load ceiling are the backstop** — `client-boot-loop.sh` kills a launch that never PASSes
     (600s launch / 900s battle), and the smoke test throws after a 3-min in-world ceiling, so a genuine hang ends
     with a digest instead of an infinite wait. If you see the watchdog fire with no crash report, suspect #1 first.
- **The one command** — PER-MOD at `mods/<modid>/tools/client-validate.sh` (copy from the template; the scripts
  derive `<modid>` from their own location, so no edits). Run from the mod dir: `./tools/client-validate.sh`. It
  chains all four phases (launch → spawn → battle → gauntlet) via `client-boot-loop.sh`, advancing on each PASS.
  Signal dir (`$SIG_DIR`, per-mod default `/tmp/<modid>-clientloop`): `phase` (live phase), `crash.log`+`crash.ready`
  (a crash, digest names the phase), `fix.done` (you signal a fix → relaunch), `all-done` (every phase passed), `stop`.
  (Only the stress test is per-mod; general tooling — vineflower/srg-remap/gen-empty-structure — stays at root `tools/`.)
- **The per-phase engine** (`mods/<modid>/tools/client-boot-loop.sh`, called by the above; also runnable alone as
  `client-boot-loop.sh <mode>`): launches `runClient -Pboottest -Ptestmode=<mode>`; on a crash writes the digest,
  blocks on `crash.ready`, relaunches when you `touch fix.done`. The phases, each a superset:
  - `launch` — title screen + quit (client-LOAD surface). `spawn` — world + one of every entity, ~10s.
  - `battle` — N of every creature fighting in two teams around the player, ~30s (combat/AoE/death paths).
  - `gauntlet` — tooltips + render every item + custom GUIs + wear every armor (3rd-person) + place every
    block + `use()` every item + apply every effect (the render/UI/item/effect axis).
- **build.gradle `client` run wiring** (now shipped in the template, parameterized by `mod_id` — verify it's present
  after scaffolding; see anti-hang #1 above for why its absence is a silent hang):
  ```
  if (project.hasProperty('boottest'))    systemProperty "${project.mod_id}.boottest", 'true'
  if (project.hasProperty('testmode'))    systemProperty "${project.mod_id}.testmode", project.property('testmode')
  if (project.hasProperty('battlecount')) systemProperty "${project.mod_id}.battlecount", project.property('battlecount')
  if (project.hasProperty('battleticks')) systemProperty "${project.mod_id}.battleticks", project.property('battleticks')
  ```
- **Needs a real display session** — GLFW fails "Failed to find a primary monitor" from a headless/agent shell,
  and a child the agent spawns inherits that. **But on macOS the agent CAN kick Gate C off itself** — it just
  can't spawn the GUI child directly; it hands the launch to a service already in the logged-in Aqua session:
  - **DO:** `open mods/<modid>/tools/run-gatec.command` (LaunchServices). This starts `client-validate.sh` inside
    the GUI session with full display access, works from the (even sandboxed) agent shell, and needs **no**
    consent prompt. Then arm the monitor on `$SIG_DIR` and fix crashes as they surface — fully autonomous, the
    human never runs a command. (`run-gatec.command` ships in the template `tools/`; it's also Finder-double-clickable.)
  - **DON'T** rely on `osascript -e 'tell application "Terminal" to do script …'` (AppleEvents): it triggers a
    one-time **Automation-consent** dialog and the send times out (`AppleEvent timed out -1712`) until a human
    clicks Allow — so it is NOT autonomous. `open` is the autonomous path; keep `osascript` only as a manual fallback.
  - **The ONLY real prereq** is a **live on-console Aqua GUI session** — `WindowServer` running and the session
    owning the physical display. That is all GLFW needs. **A LOCKED screen and a SLEPT display do NOT block it**
    (empirically verified: the client boots GLFW → title screen with `CGSSessionScreenIsLocked = True`; the
    compositor renders offscreen behind the lock, and `caffeinate -u` wakes a slept display). So do NOT gate on
    "unlocked"/"awake" — gate only on "is there a GUI session at all." **Deterministic checks (no assuming):**
    ```bash
    pgrep -x WindowServer >/dev/null && echo "GUI session present"        # the gate that matters
    python3 - <<'PY'   # lock state (informational only — NOT a blocker), via CoreGraphics, no pyobjc
    import ctypes,ctypes.util
    cg=ctypes.CDLL(ctypes.util.find_library('CoreGraphics')); cf=ctypes.CDLL(ctypes.util.find_library('CoreFoundation'))
    cg.CGSessionCopyCurrentDictionary.restype=ctypes.c_void_p; d=cg.CGSessionCopyCurrentDictionary()
    cf.CFStringCreateWithCString.restype=ctypes.c_void_p; cf.CFStringCreateWithCString.argtypes=[ctypes.c_void_p,ctypes.c_char_p,ctypes.c_uint32]
    cf.CFDictionaryGetValue.restype=ctypes.c_void_p; cf.CFDictionaryGetValue.argtypes=[ctypes.c_void_p,ctypes.c_void_p]
    cf.CFBooleanGetValue.restype=ctypes.c_ubyte; cf.CFBooleanGetValue.argtypes=[ctypes.c_void_p]
    def b(n):
        k=cf.CFStringCreateWithCString(None,n.encode(),0x08000100); v=cf.CFDictionaryGetValue(d,k)
        return None if not v else bool(cf.CFBooleanGetValue(v))
    print("locked =", b("CGSSessionScreenIsLocked"), " OnConsole =", b("kCGSSessionOnConsoleKey"))
    PY
    ```
    Optionally `caffeinate -u -t 5` before launch to wake a slept display (nice for a human watching; not required).
    On headless Linux CI use `xvfb-run ./tools/client-validate.sh`. Only if there is **no GUI session at all** (pure
    SSH, no logged-in desktop, no xvfb) is Gate C blocked — then leave the P0 items on the manual list and say so;
    this is where most post-load bugs (R6–R21) actually live.

## 7. Deploy + client verification
```bash
./gradlew deployToMods          # runs build (=> both test gates) then copies the jar to $MINECRAFT_MODS_DIR
```
GameTest proves server-side load + core gameplay. Rendering, client-only mixins, and GUIs
still need a real client — the user launches it; you read the log:
```bash
./gradlew runClient             # dev client (or user launches the vanilla instance)
tail -n 300 "$MINECRAFT_LOGS_DIR/latest.log"    # watch for mixin-apply / render / registration crashes
```

## 8. Record status
Update `mods/$MODID/MIGRATION.md`: compile-clean? both test gates green? which §R crashes
were fixed? what's still client-gated? Be honest about scope — "compiles + GameTest passes +
client unverified" is a precise, useful state.

## 9. Deliver — MANDATORY, the port is NOT done until this runs

Two deliveries, to two places. Run from the migrator checkout (or the workspace, whose `tools/` links
back to it).

```bash
# 1. the port -> the mods destination ($MOD_OUTPUT_REPO from .env.local), on branch port/$MODID
python3 tools/finish-port.py $MODID --dry-run      # what it will copy and where
python3 tools/finish-port.py $MODID --push          # copy, commit, push the branch
# then merge port/$MODID in the destination the way that repository likes (a PR, or a local merge)

# 2. the lessons -> a PR against this repository (skip if Step 4b/6 recorded none)
python3 tools/propose-learnings.py --modid $MODID --dry-run   # review the catalogue diff first
python3 tools/propose-learnings.py --modid $MODID --push
```

`finish-port.py` leaves build output, run directories, Gradle caches, the pristine decompile and the
port's local `.git` behind, refuses a destination inside the migrator checkout, and refuses to overwrite
uncommitted work in the destination. With `MOD_OUTPUT_REPO=none` it keeps the port in the workspace and
says so. `propose-learnings.py` refuses a lesson that names the port it came from, a new entry without
**Pattern:**, **Fix:** and a symptom (**Error:**/**Runtime:**/**Symptom:**), and anything that fails the
IP or fidelity gate — leaving no branch behind when it refuses.

**Why this step exists.** It was missing, and the drift was real: several completed ports sat on
local-only branches for weeks. One of them — **a Fabric armour mod** — was a jar the user was actively PLAYING
with, whose only source copy was an unpushed branch; another lived in a `/private/tmp` worktree that
macOS purges on its own schedule. A port is expensive to rebuild and impossible to recover from a jar.

**Rules:**
- **Never** leave a port's only copy in `/tmp` or `/private/tmp`.
- **A deploy is not delivery.** If `deployToMods` put a jar in the user's mods folder, the source that
  built it must already be in the destination.
- Gates green but not ready to merge in the destination? **Still push the branch.** An unpushed branch
  is the failure mode; an unmerged one is just a queue.

## 10. Abandoning a path — MANDATORY ritual (abandoned ≠ deleted, and ≠ silently left lying around)

Ports get abandoned for good reasons: a blocked subsystem, a better path found, or the target turning
out not to be worth it. That is fine. What is **not** fine is leaving an abandoned workspace looking
identical to a live one — the next session (or the next you) will mistake it for real work.

**When you stop pursuing a path, do all three:**

1. **In the mods destination, merge the branch to `main`, then DELETE the branch** (local *and* `origin`). Never delete a
   branch that is not fully merged — check `git rev-list --count origin/main..<branch>` is `0`.
   Merging is what puts the source safely in history; the dangling branch is pure ambiguity.
2. **DELETE the `mods/<modid>/` directory from `main` as well.**
   ```bash
   git rm -r mods/<modid>
   # then record, in the relevant plan.json / status file:
   #   recovery_sha: <the commit that still contained it>
   #   recover_with: git checkout <sha> -- mods/<modid>
   ```
   **This does not lose the source.** It remains in git history on `origin`, fully recoverable. The
   point is that `mods/` should contain only live ports: a directory that looks identical to a real
   workspace is exactly what causes a superseded path to be mistaken for current work — which is the
   failure this whole section exists to prevent.

   Two pre-checks before removing:
   - nothing **outside** the directory references it (`grep -rIl <modid> --exclude-dir=.git .`);
   - whatever it produced — catalog deltas, docs, tooling — already lives **outside** it, so the
     knowledge survives even though the workspace doesn't.

   Optionally write `mods/<modid>/ABANDONED.md` *before* removing, so the reasoning is captured in
   history at the recovery SHA. But the record that actually has to survive is the status file, and
   it must carry: the **decisive** reason (strategic beats technical — "even green, it yields no
   content" beats "281 compile errors"), the honest build state, what replaced it by exact
   path/version, and the recovery SHA.
3. **Correct any status file that now lies.** `installs/<install>/plan.json`, roadmaps, READMEs —
   whatever recorded the old plan. A plan file frozen mid-story is worse than no plan file.

**The case that motivated this rule.** `plan.json` labelled the framework-library + furniture-mod **1.20.1 → 1.21.1** path
`"abandoned_path"` and moved to a **1.21.4 → 1.21.1** downport. Then FF 21.4.112 turned out to be
framework-only, so the work went *back* to the 1.20.1 path and finished it — successfully, and it is
what ships in the player's game today. `plan.json` was never updated. Ten days later that stale label
caused a session to merge the superseded downport describing it as "a GREEN library port", without
noticing it had **no consumer at all**. The confusion cost was entirely avoidable and entirely
documentation.

## Resuming a partial port
Everything needed to resume lives in `mods/$MODID/` (git-tracked `src/` + tests) plus its
`MIGRATION.md`. `decompiled-raw/` is gitignored; re-derive it from the jar with step 2 if you
need the pristine baseline to diff against.
