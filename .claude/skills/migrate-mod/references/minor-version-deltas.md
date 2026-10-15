# Specialized minor-version deltas: 1.21.x → 1.21.y — symptom → fix
<!-- AXIS: specialized minor-version delta (same family, e.g. 1.21.1↔1.21.4). Consult this
     ONLY for a same-family hop (up-port or down-port). The loader-transform + version-family
     files cover the big jumps; these are the SMALL, per-minor-version API deltas that don't
     belong in either. Companion catalog section: CATALOG.md §M. -->

## ⚠️ 1.21.x → 26.x is NOT a minor hop — it is an ERA JUMP. See CATALOG.md §V.
Minecraft left the `1.x` scheme after **1.21.11**: the next releases are **26.1** (2026-03-24)
and **26.2** (2026-06-16), with NeoForge tracking them as `26.1.x` / `26.2.x`. Despite the
version numbers looking adjacent to nothing familiar, this is a *larger* break than 1.20→1.21:
a required **JDK 25**, a mandatory toolchain move off NeoGradle to **ModDevGradle**, a
**sub-package reorganisation** of ~400 classes, `ResourceLocation`→**`Identifier`**, the
removal of the **`@GameTest` annotation** (so Gate B needs a harness rewrite before it can run
at all — §V20 is the mechanical answer), and a client-render API rebuilt around **recorded render
state** (`net.minecraft.client.renderer.state`, 45 classes that did not exist in 1.21.1).
⚠️ **Not "rewritten around Vulkan"** — that was an early inference from spotting
`blaze3d.vulkan` in the jar, and it is wrong: OpenGL is still the default backend and mods write
no per-backend code. §V6/V6b carry the correction and the reasoning error behind it.

If the mod must keep running on the OLD version as well, the architecture decision comes first
and is hard to retrofit: **CATALOG.md §W** (one source tree, several targets) and **§X** (codemod
hygiene), with the scaffold at `templates/multi-version/`.

Measured, with the generated rename map, in **CATALOG.md §V**. Tooling:
`tools/build-class-move-map.py` (diff two real compile classpaths → a move map, plus
`.ambiguous.txt` and `.removed.txt`). The maps are **not shipped** — they are derived from Mojang's
mapping data — so generate one per version pair into `$MIGRATE_WORKSPACE/moves/`; you already have
both classpaths if you are migrating between those versions.

## Status: filled from real crossings, and still growing
This started as an empty scaffold and now holds every delta a real port has crossed (M- and U- entries
below). The deep corpus in this repo is still the
**Forge 1.20.1 → NeoForge 1.21.1** path (loader-transform + version-family + CATALOG.md §A–§L/§R).
Minor-version deltas between 1.21.x releases are **small and situational**, so we do NOT pre-write
them — we capture each the first time a real migration actually crosses that boundary, via the
Step-4b retrospective (see SKILL.md). That keeps this file accurate instead of speculative.

## When you cross a 1.21.x → 1.21.y boundary (the procedure)
1. **Confirm you need this file at all.** A 1.21.1 target from a Forge/older source uses the main
   corpora, not this file. This file is for: a **downport** (e.g. a mod that only ships 1.21.4 →
   your 1.21.1 instance) or an **up-port** (1.21.1 → a newer 1.21.x instance).
2. **Set the target knobs** (SKILL.md → Target parameters): `templates/neoforge-mod/gradle.properties`
   `minecraft_version` / `minecraft_version_range` / `neo_version` / `parchment_*` for `DST_MC`, and the
   GameTest `DataVersion` for `DST_MC`. `pack_format` only changes at specific releases — verify for the pair.
3. **SKIP the SRG remap** (Step 2b) — a 1.21.x source jar is already official-mapped (SKILL.md SRG-skip rule).
4. **SKIP the loader transform** if the source is already NeoForge (same `SRC_LOADER`/`DST_LOADER`).
5. **Compile-loop** as usual. Every NEW API delta you hit for this pair → append it BELOW in the
   standard 3-line format AND (if it has a static signature) add a grep to `references/catalog-scans.md`,
   then mirror it into CATALOG.md **§M** so the next hop across the same boundary is one-shot.

## Known reference points (background, not deltas)
- `DataVersion` (GameTest empty structure) by release — **1.21.1 = 3955**. Look up the exact value for any
  other `DST_MC` (it changes every release) before generating the fixture.
- `pack_format` — 1.21.1 = 34 (resources) / 48 (data). Re-verify per release; several 1.21.x bumps exist.

## Deltas (append here as they're discovered)
**First crossing: a small MCreator food mod, NeoForge 1.21.4 → 1.21.1 downport**. Full detail +
3-line format in CATALOG.md **§M** (M1–M6). Summary (a **downport removes** the 1.21.2+ API and
restores the 1.21.1 one; an **up-port** applies each forward):

- **M1 · Entity render-state system (1.21.2+, client — biggest bucket):** `MobRenderer<T,RenderState,M>` (3
  args) + `create/extractRenderState` and `EntityModel<LivingEntityRenderState>` → 1.21.1 `MobRenderer<T,M>`
  (2 args) + `HierarchicalModel<T>` with `root()` + `setupAnim(T, float×5)`. `net.minecraft.client.renderer.
  entity.state` doesn't exist on 1.21.1.
- **M2 · `Registry.getValue(rl)` → `Registry.get(rl)`** (1.21.2 renamed the nullable lookup; mirror of catalog #70).
- **M3 · `spawnAtLocation(ServerLevel, ItemStack)` → `spawnAtLocation(ItemStack)`** (1.21.2 added the level arg).
- **M4 · `EntityType.Builder.build(ResourceKey)` → `build(String id)`** (1.21.2 changed the arg type).
- **M5 · `new SpawnEggItem(type, props)` → `new SpawnEggItem(type, int bg, int hl, props)`** (1.21.2 moved egg
  colors out of the ctor into the `items/*.json` tints — recover the ints from there); also add a
  `models/item/<egg>.json` (parent `template_spawn_egg`) since 1.21.1 renders eggs from `models/item/`, not `items/`.
- **M6 · Recipe ingredient bare-strings (1.21.2+ data, RUNTIME not compile):** `"ingredients":["ns:id"]` /
  `"ingredient":"ns:id"` → object form `{"item":"ns:id"}` (tag → `{"tag":…}`); results stay `{"id":X,"count":N}`
  on both. Surfaces as `No ingredients for shapeless recipe` on a `runGameTestServer` reload. `pack.mcmeta`
  pack_format 1.21.4 = 61, 1.21.1 = 34.

### 1.21.4 → 1.21.1 (downport) — from a framework-library + furniture-mod downport
`pack_format`: 1.21.4 data = **61** → 1.21.1 = **34/48**. `DataVersion` 1.21.1 = 3955.

M7. **ScheduledTickAccess split (1.21.2)** · **Pattern:** methods take/return `net.minecraft.world.level.ScheduledTickAccess`; `tickAccess.createTick(...)`; a `DelegatedScheduledTickAccess extends ScheduledTickAccess`. · **Error:** `cannot find symbol: class ScheduledTickAccess`. · **Fix:** 1.21.2 split the scheduled-tick methods (`getBlockTicks`/`getFluidTicks`/`scheduleTick`) into a new `ScheduledTickAccess` super-interface of `LevelAccessor`; **in 1.21.1 they live directly on `LevelAccessor`** and there is no `createTick`. Drop the `ScheduledTickAccess` type + param (use `LevelAccessor`), delete `createTick` overrides.
M8. **`Orientation` on neighbor methods (1.21.2)** · **Pattern:** `neighborChanged(BlockState, Level, BlockPos, Block, Orientation, boolean)`, `updateNeighborsAt(BlockPos, Block, Orientation)`, `net.minecraft.world.level.redstone.Orientation`. · **Error:** `cannot find symbol: class Orientation`. · **Fix:** 1.21.2 replaced the neighbor `BlockPos fromPos` with `Orientation`; **1.21.1 uses `BlockPos`** (the neighbor pos). Revert the param to `BlockPos neighborPos`; drop the `updateNeighborsAt(...,Orientation)` overload (1.21.1 has the 2-arg form only).
M9. **`Block.updateShape` signature (1.21.2)** · **Pattern:** `updateShape(BlockState, LevelReader, ScheduledTickAccess, BlockPos, Direction, BlockPos, BlockState, RandomSource)`. · **Error:** override doesn't match / `cannot find symbol: ScheduledTickAccess`. · **Fix:** 1.21.1 signature is **`updateShape(BlockState state, Direction direction, BlockState neighborState, LevelAccessor level, BlockPos pos, BlockPos neighborPos)`** (no `ScheduledTickAccess`, no `RandomSource`, different arg order). Reverting a wrapper/component chain means updating every hook + forwarder + vanilla override together.
M10. **`BlockBehaviour.useItemOn` return type (1.21.4 unification)** · **Pattern:** `InteractionResult useItemOn(ItemStack, BlockState, Level, BlockPos, Player, InteractionHand, BlockHitResult)`. · **Error:** `useItemOn(...) cannot override useItemOn(...) in BlockBehaviour`. · **Fix:** 1.21.4 unified the return to `InteractionResult`; **1.21.1 `useItemOn` returns `net.minecraft.world.ItemInteractionResult`** (enum: SUCCESS/CONSUME/PASS_TO_DEFAULT_BLOCK_INTERACTION/SKIP_DEFAULT_BLOCK_INTERACTION/FAIL). Change the return type; `.consumesAction()` still exists.
M11. **`ServerEntityGetter` (1.21.2)** · **Pattern:** `implements ServerEntityGetter`, `getLevel()` returning `ServerLevel`, nearest-player/entity queries. · **Error:** `cannot find symbol: class ServerEntityGetter`. · **Fix:** 1.21.2 split `ServerEntityGetter` out of `Level`; **in 1.21.1 the `getNearestPlayer`/`getNearestEntity`/`getNearbyPlayers`/`getNearbyEntities` methods are on `EntityGetter`** (`Level.getLevel()` doesn't exist). Retarget to `EntityGetter`, drop `getLevel()`.
M12. **Furnace `RecipeAccess`/`FuelValues` (1.21.2)** · **Pattern:** `recipeAccess()`, `fuelValues()`, `RecipeAccess`, `FuelValues`. · **Error:** `cannot find symbol: class RecipeAccess/FuelValues`. · **Fix:** these are 1.21.2 additions to the furnace/recipe path; **1.21.1** uses `RecipeManager` lookups + `AbstractFurnaceBlockEntity.getFuel()`/`isFuel()`. Revert to the 1.21.1 recipe-check API.
M13. **Loot/lock/id API (1.21.2)** · **Pattern:** `Properties.setId(ResourceKey)`, `setLootTable(ResourceKey<LootTable>[, long])`/`setLootTableSeed`/`noLootTable`, `LockCode.fromTag/addToTag` with a `HolderLookup.Provider`, `EntitySpawnReason`. · **Error:** `cannot find symbol: method setId/setLootTable/...` / `class EntitySpawnReason`. · **Fix:** `Item/Block.Properties.setId(...)` is 1.21.2 (drop — ids set at registration in 1.21.1); `LockCode.fromTag(tag)`/`addToTag(tag)` are 1-arg in 1.21.1 (no Provider); **`EntitySpawnReason` → `MobSpawnType`** (renamed in 1.21.2).
M14. **Datagen package reorg (1.21.4)** · **Pattern:** `import net.minecraft.client.data.models.*` (model providers), `import net.minecraft.util.context.*` (loot ContextKey). · **Error:** `package net.minecraft.client.data.models / net.minecraft.util.context does not exist`. · **Fix:** these are 1.21.2/1.21.4 datagen reorgs. Datagen is build-time only and the generated `assets`/`data` already ship, so the cheapest downport is to **exclude the datagen source tree** (`sourceSets.main.java.exclude '.../data/**'`) rather than back-port the provider API. If runtime code needs it, 1.21.1 model datagen is `net.minecraft.data.models.*` and loot context is `LootContextParam(Set)`.
M15. **`getCloneItemStack` (1.21.2)** · **Pattern:** `getCloneItemStack(LevelReader, BlockPos, BlockState, boolean)`. · **Error:** `no suitable method found for getCloneItemStack(...)`. · **Fix:** 1.21.1 is `getCloneItemStack(BlockGetter/LevelReader, BlockPos, BlockState)` (no `boolean includeData`); drop the trailing arg.

#### Additional 1.21.4 -> 1.21.1 deltas (found completing the framework-library downport to green + Gate B)
M16. **Block.onExplosionHit narrowed to ServerLevel (1.21.2)** · **Pattern:** a mixin `@Inject` (or override) on `onExplosionHit(BlockState, ServerLevel, BlockPos, Explosion, BiConsumer, CallbackInfo)`. · **Runtime:** `InvalidInjectionException: Invalid descriptor ... Expected (...Lnet/minecraft/world/level/Level;...) but found (...Lnet/minecraft/server/level/ServerLevel;...)` — the dedicated server REFUSES TO BOOT. · **Fix:** 1.21.1 takes `Level`, not `ServerLevel`. **A clean compile cannot catch this** (the mixin compiles fine against its own signature); only Gate B does. After any downport, boot `runGameTestServer` before believing the mixins.
M17. **Level constructor + explode + getRecipeManager (1.21.2)** · **Pattern:** `super(levelData, dimension, registryAccess, dimensionTypeRegistration, isClientSide, isDebug, biomeZoomSeed, maxChainedNeighborUpdates)`; `public void explode(...)`; `recipeAccess()`/`fuelValues()`. · **Error:** `constructor Level cannot be applied`; `boolean cannot be converted to Supplier<ProfilerFiller>`; `X is not abstract and does not override abstract method getRecipeManager()`; `cannot find symbol: RecipeAccess/FuelValues`. · **Fix:** 1.21.1's ctor takes a **`Supplier<ProfilerFiller>` as the 5th arg** (use `delegate.getProfilerSupplier()`); **`Level.explode(...)` returns `Explosion`**, not `void` (add `return`); implement the abstract **`getRecipeManager()`**; delete `recipeAccess()`/`fuelValues()` (1.21.2+). Also `Level.dragonParts()` and `setSpawnSettings(boolean)` differ — 1.21.1 has `setSpawnSettings(boolean hostile, boolean peaceful)` and no `dragonParts()` on `Level`.
M18. **LevelHeightAccessor renames (1.21.2)** · **Pattern:** `getMinY()/getMaxY()/getMinSectionY()/getMaxSectionY()/isInsideBuildHeight(int)`. · **Error:** `cannot find symbol: method getMinY()`. · **Fix:** 1.21.1 names are **`getMinBuildHeight()`/`getMaxBuildHeight()`/`getMinSection()`/`getMaxSection()`**; `isInsideBuildHeight` does not exist (use `!isOutsideBuildHeight`).
M19. **neighborShapeChanged arg order (1.21.2)** · **Pattern:** `neighborShapeChanged(Direction, BlockPos pos, BlockPos neighborPos, BlockState neighborState, int, int)`. · **Error:** `incompatible types: BlockPos cannot be converted to BlockState`. · **Fix:** 1.21.1 order is **`(Direction, BlockState neighborState, BlockPos pos, BlockPos neighborPos, int, int)`**.
M20. **Delegating LevelAccessor: "inherits unrelated defaults"** · **Pattern:** a hand-written `DelegatedScheduledTickAccess` (mirroring the 1.21.2 `ScheduledTickAccess` split) mixed into an interface that also extends `LevelAccessor`. · **Error:** `types X and LevelAccessor are incompatible; inherits unrelated defaults for scheduleTick(BlockPos,Fluid,int)`. · **Fix:** in 1.21.1 `LevelAccessor` **already defaults `scheduleTick(...)`** in terms of `getBlockTicks()`/`getFluidTicks()`. **Delete** the separate delegate interface and just delegate those two getters. Same class of clash hits `LevelReader` vs `LevelHeightAccessor` for `getMinBuildHeight/getMaxBuildHeight/getHeight` — the junction interface must override them explicitly to disambiguate.
M21. **Small vanilla signature cluster (1.21.2)** · `GuiGraphics.blitSprite(RenderType::guiTextured, sprite, ...)` -> **drop the RenderType arg** (`blitSprite(sprite, ...)`); `AbstractContainerMenu.addStandardInventorySlots(Inventory,int,int)` **doesn't exist** (lay out the 3x9 + hotbar yourself); `Property.getPossibleValues()` returns **`Collection`** not `List` (no `getFirst()`); `BlockState.getNullableValue(p)` -> **`getOptionalValue(p).orElse(null)`**; `Registry.getOrThrow(key)` returns the **value** (use **`getHolderOrThrow`** for a `Holder.Reference`); `Block.updateEntityMovementAfterFallOn` -> **`updateEntityAfterFallOn`**; `VoxelShape.move(Vec3)` -> **`move(double,double,double)`**; `CollisionGetter` has no `noCollision(Entity,AABB,boolean)`, `clipIncludingBorder`, or `getBlockAndLiquidCollisions`; `RenderLevelStageEvent.getLevel()` doesn't exist (use `Minecraft.getInstance().level`); `BlockPos.breadthFirstTraversal` takes a **`Predicate<BlockPos>`** (the `TraversalNodeStatus` enum is 1.21.4).
M22. **Registration cluster (1.21.2)** · `Item/Block.Properties.setId(ResourceKey)` and `.useBlockDescriptionPrefix()` **don't exist** (drop them — ids come from registration); `EntityType.Builder.build(ResourceKey)` -> **`build(String)`**; `EntityType.create(Level, EntitySpawnReason)` -> **`create(Level)`**; `EntityType.Builder.noLootTable()` is 1.21.2+ (drop); `new BlockEntityType(factory, blocks)` needs a **3rd `Type<?>` arg** (`null`); `new SpawnEggItem(type, props)` -> **`new SpawnEggItem(type, bgColor, hlColor, props)`**.
M23. **Mixin accessor interfaces need an Object hop** · **Pattern:** `((MyLevelAccessor)this)` inside a class that extends the mixin's target. · **Error:** `incompatible types: FakeLevel cannot be converted to LevelAccessor`. · **Fix:** the accessor interface is only added at runtime, so cast through Object: **`((MyLevelAccessor)(Object)this)`**.
M24. **`ItemTintSource` has no 1.21.1 equivalent (1.21.4)** · **Pattern:** `implements net.minecraft.client.color.item.ItemTintSource` + `RegisterColorHandlersEvent.ItemTintSources`. · **Error:** `cannot find symbol: class ItemTintSource/ItemTintSources`. · **Fix:** this is the 1.21.4 **data-driven item-model tint** system; 1.21.1 has only `RegisterColorHandlersEvent.Item` + `ItemColor` registered per-item. If the tint is only cosmetic, DROP it and record the visual gap; otherwise re-register an `ItemColor` for each affected item.
M25. **⚠️ The 1.21.4 MODEL-DATAGEN rewrite is the big one** · **Pattern:** `net.minecraft.client.data.models.*` (`BlockModelGenerators`, `ModelTemplates`, `TextureMapping`, `ModelLocationUtils`, `MultiVariant`, `PropertyDispatch`, `Variant`/`VariantProperties`), `net.minecraft.util.context.*` loot context. · **Error:** `package net.minecraft.client.data.models does not exist` (x many). · **Fix:** 1.21.4 moved AND rewrote model datagen (`net.minecraft.data.models.*` in 1.21.1, with a different builder shape) as part of the item-model JSON overhaul. **This is by far the largest 1.21.1<->1.21.4 delta.** If the datagen is build-time only and the generated assets already ship, **exclude the datagen source tree** instead (`sourceSets.main.java.exclude`). If a *dependent* mod's public API is built on it (e.g. a furniture-set DSL), you cannot exclude it — budget a full subsystem rewrite.

### 1.21.1 → 1.21.4 (UP-PORT) — from a ~840-file content mod (NeoGradle), the first forward crossing
Measured before any fixing (burndown-count.sh, uncapped): **332 unique error sites in 122 files**; the
import-level scope (`scope-port.py`) says 98% of imports unchanged, 4 removed — so nearly the whole hop
is MEMBER-level and an import scope alone reads as trivial. Quote both. Healthy 1.21.4 recompile =
**10329** classes (1.21.1 = 9766). Move map (generated, not shipped): 37 moves, 201 removed.

U1. **Run type `data` split into `clientData` + `serverData` (NeoForge 21.4 userdev)** · **Pattern:** a
NeoGradle `runs { data { … } }` block · **Error (configuration, not compile):** `(The run type 'data' was
not found) null` — which reads like the "consequence of an unrelated failed configuration" noise several
CLAUDE.md files warn about, and here it is the real cause · **Fix:** rename to `clientData` (assets) and/or
`serverData` (data). Check the list rather than guessing: `unzip -p neoforge-<v>-userdev.jar config.json`
→ `runs` = client, clientData, serverData, gameTestServer, server, junit.

**The rest of the burn-down (332 → 0, both trees), in the order it was worth doing.** Every row below was
read off BOTH jars (`javap` + the `patchUserDev` sources), not recalled, and each is tagged with how it was
carried in a §W tree: **row** (a `versions/1.21.1.renames.tsv` reverse row), **pair** (a §W5 compat class),
or **shared** (a spelling both versions accept, so no mechanism at all — always look for one first).

U2. **`MobSpawnType` → `EntitySpawnReason`, and `SPAWN_EGG` is already `SPAWN_ITEM_USE` here** · **Error:**
`cannot find symbol: class MobSpawnType` · **Fix:** rename (row). ⚠ This corrects **§V38**, which dates
`SPAWN_ITEM_USE` to 26.2: it is 1.21.2. 1.21.4 also ADDS `LOAD` and `DIMENSION_TRAVEL`, which shared source
must not name. `EntityType.create(level)` gained the reason (`create(level, reason)`); on 1.21.4 the reason
is inert there (no Peaceful gate yet — that is 26.x, §V30), so `COMMAND` everywhere + a regex row dropping it.

U3. **Registry lookups: `get` → `getValue`, and `get` now answers `Optional<Holder.Reference>`** (§V26/M2,
1.21.2) · **Error:** `Optional<Reference<Item>> cannot be converted to Item` — easy to file as something else ·
**Fix:** `getValue(id|key)` (row, anchored on the `BuiltInRegistries.X` receiver with `\s*` for split lines);
`RegistryAccess.registryOrThrow` → `lookupOrThrow` (row — but ANCHOR it: 1.21.1 also has a `lookupOrThrow`,
inherited from `HolderLookup.Provider`, returning a HolderLookup that legitimate 1.21.1 code uses);
`holders()` is gone. **Shared spellings exist and cost nothing:** `asHolderIdMap()` (Iterable of Holders),
`getOptional(id)`, `wrapAsHolder(v)`, `containsKey`, `getKey`, `getResourceKey`. Reach check (§X40): 35
`getValue` in shared source, 0 left in the prepared 1.21.1 tree.

U4. **`NativeImage.getPixelRGBA` (ABGR) → `getPixel` (ARGB)** · **Fix:** pair (`Pixels.abgr`), because the
byte order changed with the name — §V34's shape (below, U9).

U5. **`Entity.createCommandSourceStack()` gone for non-players** · **Fix:** pair. The replacement
`createCommandSourceStackForNameResolution(ServerLevel)` answers `CommandSource.NULL` at permission **0** —
not the entity at its own level. A behaviour change wearing a rename's clothes; safe only because every
caller set `withPermission(2)` itself (checked, 15 sites). `ServerPlayer.createCommandSourceStack()` survives.

U6. **`ServerPlayer.teleportTo(level,x,y,z,yaw,pitch)` gone** · **Fix:** pair over
`teleportTo(level,x,y,z,Set<Relative>,yaw,pitch,boolean setCamera)`. 1.21.1's six-argument body did
`setCamera` AND `stopRiding`; the eight-argument form does only the camera — **dismount yourself** or a
teleported rider arrives still mounted. (`RelativeMovement` → `Relative`.)

U7. **`getMaxBuildHeight` (EXCLUSIVE) → `getMaxY` (INCLUSIVE); `getMinBuildHeight` → `getMinY`** · **Fix:**
pair named by meaning (`topExclusive` = `getMaxY() + 1`). ⚠ This is **§V23**, which dates it to 26.x: it
arrives at 1.21.2.

U8. **`Item.use` returns `InteractionResult`; `InteractionResultHolder` and `sidedSuccess` are gone** · **Fix:**
a BASE-CLASS pair (§W5b — a changed RETURN type of an override cannot be carried by a static helper).
Faithful mapping of `sidedSuccess(c)` is `c ? SUCCESS : CONSUME` (1.21.4's CONSUME = success with no swing),
**not** vanilla 1.21.4's bare `SUCCESS` — one mod's watch comment says a server-side success closes the
container it just opened. Judgement row; confirm in a client.

U9. **`MapColor.calculateRGBColor` (returns ABGR!) → `calculateARGBColor` (genuinely ARGB)** · **Fix:** pair.
⚠ This is **§V34**, dated there to 26.x: the value change arrives at 1.21.2.

U10. **Properties must carry their id — R25 arrives at 1.21.2, and on 1.21.1↔1.21.4 it needs no pair** ·
**Runtime:** `Item id not set` at registration, clean compile · **Fix (shared):** NeoForge **21.1 and 21.4
both** have `DeferredRegister.Items.registerItem(name, ctor, props)` / `Blocks.registerBlock` /
`registerSimpleBlockItem`, with identical signatures; 21.4's stamp `setId` for you, 21.1's just construct.
Moving the registrations onto them is shared source. Keep `Supplier<Item>` fields with a type witness:
`ITEMS.<Item>registerItem(...)`. Also: `Item.getDescriptionId()` is `final` (id-derived) — an override that
returned the default is simply deleted.

U11. **The cluster** (each one line, each verified on both jars):
`BlockEntityType.Builder` → public ctor `(supplier, Block...)` (pair; **§V51 dates this to 26.x — it is
1.21.2**) · `EntityType.Builder.build(String)` → `build(ResourceKey)` (M4 read forward; register through the
`Function<ResourceLocation,…>` form so the id is in scope) · `loadEntityRecursive` gained an
`EntitySpawnReason` · `LivingEntity.kill()` → `kill(ServerLevel)` · `hurt` became `final void` —
`hurtServer(ServerLevel,…)` returns the boolean · `ItemCooldowns.addCooldown(Item,int)` →
`(ItemStack|ResourceLocation, int)` · `Snowball(level, owner)` → `(level, owner, ItemStack)` ·
`BlockState.isSolidRender(level,pos)` → `isSolidRender()` · `Direction.getNearest(d,d,d)` →
`getApproximateNearest` · `client.ParticleStatus` → `server.level.ParticleStatus` ·
`Minecraft.getToasts()` → `getToastManager()` · `RecipeHolder.id()` is a `ResourceKey<Recipe<?>>` (so
`holder.id().toString()` changes SILENTLY — grep for it) · `ClientboundTeleportEntityPacket.getId()` → `id()`
(record) · `WalkAnimationState.update(s,f)` → `update(s,f,positionScale)` · `PotionContents` gained
`Optional<String> customName` · the client `Input`'s six booleans → `ClientInput.keyPresses` (an immutable
`Input` record; `Input.EMPTY`) · `GuiGraphics.blit(RL,…)` → `blit(RenderType::guiTextured, RL,…)` ·
`CustomModelData(int)` → four lists; keep an int in `floats[0]` (where a ViaProxy-translated 1.21.1 int
lands, so one pack serves both — exact below 2^24) · `GameRules()` needs a `FeatureFlagSet`, and
`createFreshLevel` takes `Function<HolderLookup.Provider, …>` (a §W7 extraction: 13 duplicated client-test
blocks → one pair) · an `EntityRenderer` is `<T, S extends EntityRenderState>` with `createRenderState()`,
and its base `render` now draws leash + name tag — an override that meant "draw nothing" must not call super.

U12. **The item-model mechanism moved into data — do not try to port it call by call** · `ItemOverrides`,
`BakedModel.getOverrides/isCustomRenderer`, `ItemRenderer.getModel`, `RegisterColorHandlersEvent.Item`,
`Minecraft.getItemColors` are all gone: an item's model, its `custom_model_data` dispatch and its tints are
`assets/<ns>/items/<id>.json` + `ItemTintSource`s (§V42/§V42b, which arrive here, not at 26.x). For a
compile burn-down the honest move is a **recorded per-target drop** of the Java that implemented the old
mechanism (§V52), owned by whoever ships the JSON. The one question tests genuinely need — "does this stack
resolve, and to which sprite" — has a small pair: 1.21.4 resolves through
`getItemModelResolver().updateForTopItem(state, …)` and a missing binding shows as the `missingno` particle
from `state.pickParticleIcon(...)`.

**Measured end state:** 1.21.4 main + test compile clean; 1.21.1 still green with the reverse table at
`checked=17 dead=0`; 19 pairs, 17 rows, 8 main + 1 test recorded drops. The burn-down, in commits:
332 → 285 → 186 → 145 → 106 → 73 → 53 → 1 → 0.

### 1.21.1 → 1.21.3 (fork multi-version layer) — from the first layer fork, an ~89k-line library mod with its own shader engine
Measured with every stable NeoForge line compiled against the 1.21.1 source (issue #49): 1.21.3 **282**,
1.21.4 **342**, 1.21.5 ≥1028, 1.21.8 ≥1132, 1.21.10 ≥1346, 1.21.11 ≥2300 unique error sites. Carried in a
**fork layer** (`.multiversion/`, templates/fork-layer): the authors' `src/` is never edited, so every
difference is a rename row, a whole-file overlay or a helper class. The generic rows and helpers are the
shared library `templates/fork-layer/shared/1.21.3/` that every fork inherits. U2/U3/U4/U6/U11 above apply
unchanged; these are the NEW ones. ⚠ **Read the U section before starting a 1.21.2+ hop** — this port
re-derived five of its rows by hand.

L1. **`ShaderInstance` is gone; programs are `CompiledShaderProgram`s the vanilla `ShaderManager` compiles
from `ShaderProgram` keys** · **Error:** `cannot find symbol: class ShaderInstance` (85 sites), then
`ShaderProgram is not a functional interface` on `ShaderStateShard(() -> shader)` and
`RegisterShadersEvent.getResourceProvider()` missing · **Fix (shared helper, no rewrite):** a
`ShaderInstance extends CompiledShaderProgram` that links its own program from the 1.21.1-format JSON through
any `ResourceProvider` (a mod's preset provider included), `#moj_import` via `GlslPreprocessor`, and applies
the JSON `blend` in `apply()` (1.21.2 has no `BlendMode`); its private `CompiledShaderProgram(int)` is widened
by the target's AT. A `RegisterShadersEvent` stand-in posted on the mod's bus at every client reload
(`ResourceManagerReloadListener`) keeps the authors' listeners; a `ShaderStateShard` subclass binds the
program in `setupRenderState`. The old-format JSON still PARSES in 1.21.2's codec (unknown keys ignored), so
leftover `shaders/core/*.json` do not break the reload.

L2. **Render types and projection** · `TextureStateShard(rl, boolean blur, boolean mipmap)` →
`(rl, TriState, boolean)` · `RenderSystem.setProjectionMatrix(m, VertexSorting.DISTANCE_TO_ORIGIN |
ORTHOGRAPHIC_Z)` → `ProjectionType.PERSPECTIVE | ORTHOGRAPHIC` · `RenderSystem.applyModelViewMatrix()` removed
(delete the call) · `GameRenderer::getPositionColorShader` & co. → `CoreShaders.POSITION_COLOR` (a
`ShaderProgram`, passed as a value) · `GlStateManager._clear(mask, boolean)` → `_clear(mask)` ·
`LevelRenderer.renderLineBox` → `ShapeRenderer.renderLineBox` · `FastColor.ARGB32` → `ARGB` ·
`RenderSystem.runAsFancy` removed (just run the draw) · `GuiGraphics.bufferSource()` → the field (AT).

L3. **Fog is one `FogParameters` value** · `RenderSystem.get/setShaderFogStart/End/Shape` → `getShaderFog()` /
`setShaderFog(FogParameters)` · a `FogRenderer` mixin: `setupColor` (static `fogRed/Green/Blue` @Shadows) →
`computeFogColor` RETURNS a `Vector4f` (LevelRenderer clears with it); `setupFog` RETURNS `FogParameters` and
gained a `Vector4f fogColor` arg — modify the return value at RETURN instead of writing RenderSystem state.

L4. **Entity render state reaches the authors' layers** · `RenderLayer.render(pose, buf, light, S state,
yRot, xRot)`; `LivingEntityRenderer<T, S, M>` · a layer that decides from the ENTITY reads it from the state:
park it with `RegisterRenderStateModifiersEvent` against `EntityRenderer.class` (§V65b: data set in
`extractRenderState` is wiped) — shared helper `EntityOf`. `getOverlayCoords(state, f)` takes the state.
`EntityRenderer<? super E>` → `<? super E, ?>`; `Entity.getBoundingBoxForCulling()` moved to the renderer as
`protected` — ⚠ widening it by AT breaks the RECOMPILE (every vanilla override "assigns weaker access"); call
it reflectively. `EntityRenderDispatcher.render` dropped its yaw argument.

L5. **The frame graph: sky and clouds are passes that run LATER** · `LevelRenderer.renderSky`/`renderClouds`
are gone; `addSkyPass`/`addCloudsPass` only RECORD. A hook at `addSkyPass` draws before the sky; a flag set
while rendering entities is not yet set when `addCloudsPass` runs. Draw after the sky from
`RenderLevelStageEvent` `AFTER_SKY` (fired inside the sky pass); skip clouds with a mixin on
`CloudRenderer.render` (runs inside the cloud pass, after the entities). `renderLevel` gained a leading
`GraphicsResourceAllocator`.

L6. **Mixin targets javac cannot see (16 in one mod)** · `LivingEntity.hurt` → `hurtServer(ServerLevel, …)`;
`actuallyHurt` gained `ServerLevel` first · `Entity.changeDimension(DimensionTransition)` →
`teleport(TeleportTransition)` (and `ServerPlayer`'s returns `ServerPlayer`: its descriptor differs) ·
`ServerPlayer.teleportTo(ServerLevel,DDDFF)V` → `(ServerLevel,DDD,Set,FF,Z)Z` (U6) · `ItemRenderer.render`'s
push/transform/pop moved to `renderItemModelRaw(…, boolean, float)` · `FallingBlockRenderer.render` takes a
`FallingBlockRenderState`. Run `audit-mixin-targets.py` against the target's sources jar after every compile
that reaches 0 — a mod with `defaultRequire: 1` dies at load on each.

L7. **Small ones** · `DummySavedData` removed: `DimensionDataStorage.cache` is `Map<String, Optional<SavedData>>`
(empty = failed read) · `NativeImage.asByteArray()` removed (write a temp PNG) · GeckoLib 4.7.3/4.8.5
`GeoRenderLayer.preRender/render` gained a trailing `int` colour · `Minecraft.getTimer()` → `getDeltaTracker()`.

### 1.21.3 → 1.21.4 (fork multi-version layer) — the same library, its second target (62 errors after 1.21.3's work)
Shared rows/helpers: `templates/fork-layer/shared/1.21.4/` (a 1.21.4 target inherits 1.21.3's as well).

L8. **Client reload listeners are a named graph** · `RegisterClientReloadListenersEvent.registerReloadListener(l)` →
`AddClientReloadListenersEvent.addListener(ResourceLocation id, l)`; a unique id per listener (shared `ReloadIds`:
the listener's class name).

L9. **Things NeoForge 21.4 / Minecraft 1.21.4 dropped that a mod reached into** · `ClientLevel.partEntities` (id →
part map) → only `dragonParts()` (a list; shared `PartEntities.byId`) · `TicketType.POST_TELEPORT` → gone (shared
`Tickets` recreates it: Integer key, 5 ticks) · `TextureManager.register(String prefix, DynamicTexture)` (it NAMED the
texture, `minecraft:dynamic/<prefix>_<n>`) → only `register(ResourceLocation, AbstractTexture)` (shared `Textures`
keeps the naming) · `SkullBlockRenderer.createSkullRenderers(models)` → per-type `createModel(models, type)` ·
`InventoryMenu.BLOCK_ATLAS` → `TextureAtlas.LOCATION_BLOCKS` · FML 6's `SimpleBufferBuilder(int)` → `(String, int)` ·
`LevelRenderer.renderLevel` lost its `LightTexture` (a mixin `@At` descriptor and every `@Inject` callback).

L10. **Item rendering is an `ItemStackRenderState` (U12's mechanism, from the renderer's side)** · `ItemRenderer.render(
stack, …, BakedModel)` and `getModel(stack, level, entity, seed)` are gone; `renderStatic(...)` resolves and draws.
A hook that decided from the STACK while drawing has no stack: record it at `ItemModelResolver.appendItemLayers` (a
one-line mixin) and read it where the layer draws — `ItemStackRenderState$LayerRenderState.render`, before its
`popPose` — once per item, not per layer (shared `ItemStacks`). `ItemRenderer.renderModelLists` is private static and
takes the layer's resolved `int[]` tints instead of the stack. Shared `ItemModels.resolve` answers the old
`getModel` from a resolved state's first layer.

L11. **GUI** · `AbstractScrollWidget` → `AbstractTextAreaWidget` / `AbstractScrollArea` (`getMaxScrollAmount` →
`maxScrollAmount`, `withinContentAreaPoint` gone, `updateWidgetNarration` protected; the scrollbar is the
superclass's — 6 px wide, its own sprites) · `AbstractWidget.clicked(x,y)` → `isMouseOver(x,y)` (same test) ·
`AbstractSelectionList.getScrollbarPosition()` → `scrollBarX()`.

L12. **Animation metadata is a record with a codec** · `AnimationMetadataSection.forEachFrame`/`getDefaultFrameTime`/
`isInterpolatedFrames`/`EMPTY`/`SERIALIZER` → `frames()` (Optional list of `AnimationFrame`, `timeOr(default)`),
`defaultFrameTime()`, `interpolatedFrames()`, `CODEC`/`TYPE`; `MetadataSectionSerializer` → `MetadataSectionType`.
`EMPTY` overrode `calculateFrameSize` to answer the whole image; the record cannot, so "no metadata" has to be
handled by the caller. The codec rejects malformed `.mcmeta` the old serializer skipped.

L13. **The layer itself: every source set, and a clean class dir after failed compiles** · a mod with more than
`main` (a fork builds several packaging variants against main's classes) needs each `src/<set>/java`
through the same table — the first 1.21.3 pass prepared only main, and `pro` met a 1.21.4 change. And after a
string of failed compiles a target's incremental state can leave `classes/` partial (33 of 864 classes): the next
compile "succeeds" and every set built on main fails with `package … does not exist`. Delete `build/mc<t>/classes`
and recompile before believing either.

### 1.21.4 → 1.21.5 (fork multi-version layer) — the RenderPipeline hop (888 errors at the start)
Shared rows/helpers: `templates/fork-layer/shared/1.21.5/` (NBT Optional rows, `Nbt`, the render compat classes).

L14. **NBT getters return `Optional`** · the same change 26.2 carries (CATALOG §V12), arriving here. The 26.2 rows
match `.getInt(<anything>)` and so also rewrite a `ByteBuffer` read (`bytes.getInt(at)`); the shared 1.21.5 rows
anchor on a string-literal or CONSTANT key instead. A key in a variable, and a ListTag index, need the fork's own
rows (a shared row cannot know the receiver's type). `getUUID/putUUID/hasUUID` are gone: `Nbt` (UUIDUtil.CODEC).

L15. **Immediate-mode GL → `RenderPipeline` + `RenderPass`** · `RenderSystem.enableBlend/blendFunc/depthMask/
enable(Disable)DepthTest/Cull/colorMask/setShader`, `BufferUploader`, `CoreShaders`, `ShaderStateShard`, and the
transparency/depth/write/cull shards are gone; blend, depth, cull and masks are fixed per pipeline, and a draw is a
RenderPass. Custom uniforms are set on the pass by name; the standard ones (`GlProgram.BUILT_IN_UNIFORMS`) are
filled automatically; every sampler the program declares must be bound. A mod can compile its own shader sources
into a pipeline with `GpuDevice.precompilePipeline(pipeline, sourceFunction)` (public, cached by pipeline) — no
mixin. The shared compat (`LegacyGl`, `LegacyRenderType`, `ShaderInstance`, `ShaderShard`, `Uniform`,
`RawGlTexture`) keeps the 1.21.4 surface. Not expressible: a blend equation other than ADD (logged).
`GlTexture` gained an `external` constructor (NeoForge 21.5): a mod's own GL texture id wraps as a GpuTexture.

L16. **Mixin targets that moved (found by the audit, fixed per mod)** · `CloudRenderer.render` dropped its two
`Matrix4f` parameters (`(int, CloudStatus, float, Vec3, float)`); `Player` no longer declares `setItemSlot` (only
`LivingEntity`/`Mob` do, through `EntityEquipment`) — retarget to `LivingEntity` with an `instanceof Player` guard.
Deliberately NOT shared rows: a regex over a handler's parameter list cannot tell a handler that uses the matrices.

### 1.21.5 → 1.21.8 (fork multi-version layer) — uniform blocks, ValueOutput, the 2D GUI (185 errors at the start)
Shared rows/helpers: `templates/fork-layer/shared/1.21.8/` (renames.tsv, `ShaderInstance`/`Uniform`/`LegacyGl` blocks, `Mv`,
`EntityOf`, `ShaderReloader`). Measured on the first layer fork (369 shared files, 56 overlays at the end, 93 `@OnlyIn` stripped);
every gate green. NeoForge 21.6/21.7 are beta-only, so 1.21.6 and 1.21.7 changes ALL arrive here. Each entry names the 26.2
entry it anticipates ("arrives at 1.21.x, filed under 26.2 as §V..") -- see the tally at the end.

L17. **Plain GLSL uniforms → std140 uniform BLOCKS (1.21.6)** · `uniform mat4 ModelViewMat;` is no longer a program
uniform: a pipeline declares uniform BUFFERS (`withUniform("X", UniformType.UNIFORM_BUFFER)`) and the vanilla ones live in
`DynamicTransforms` (ModelViewMat, ColorModulator, ModelOffset, TextureMat, LineWidth), `Projection`, `Fog`, `Globals`
(ScreenSize, GlintAlpha, GameTime, MenuBlurRadius) and `Lighting`; `FogStart/FogEnd/FogShape` became differently named
members. · **What breaks:** compile-clean (the shader is a resource) and load-clean: the program links, then every draw fails
GL validation (`Missing uniform`) or reads zeros. · **Fix:** the shared `ShaderInstance` rewrites the shader SOURCE when it
reads it (`legacyUniforms`): a built-in declaration becomes the vanilla block (member ORDER is the std140 layout, spelled out,
not `#moj_import`ed, so a generated-shader provider still compiles), a fog name becomes a `#define`, any other
`uniform vec4 X;` becomes a one-member block `MV_X` that `Uniform` uploads per draw. 1.21.1 semantics kept: `setDefaultUniforms`
overwrote every built-in right before `apply()`, so a built-in written BEFORE a draw (a fork writes `GameTime` in a shard's
setupRenderState) is vanilla's on 1.21.1 AND here; one written in the mod's own `apply()` override wins (`ShaderInstance.
setDefaultUniforms/globalsBuffer/projectionBuffer`); light and fog writes cannot be honoured (logged once). · **Anticipates:**
arrives at 1.21.6, filed under 26.2 as §V93 (a custom ShaderInstance → pipeline + std140 block) and §V6/§V54 (state lives
in the pipeline).

L18. **`DynamicTransforms` and `CompositeRenderType.draw` (1.21.6)** · a draw is `createRenderPass(label, colorView, ..)`,
`RenderSystem.bindDefaultUniforms(pass)`, `pass.setUniform("DynamicTransforms", RenderSystem.getDynamicUniforms().
writeTransform(modelView, colour, modelOffset, textureMatrix, lineWidth))`, `drawIndexed(0, 0, count, 1)`; views replace
textures, and an `outputColorTextureOverride` (post-effect pass) wins over the target's. · **What breaks:** the 1.21.5
`LegacyGl.draw` (`pass.bindSampler(name, GpuTexture)`, no DynamicTransforms) → `cannot find symbol` / `NoSuchMethodError`.
· **Fix:** the shared 1.21.8 `LegacyGl.draw` mirrors `CompositeRenderType.draw` line for line and keeps the 1.21.5 surface
(`setShaderTexture(slot, GpuTextureView)`, scissor via `getScissorStateForRenderTypeDraws`); a mod-written ColorModulator
feeds the transform. · **Anticipates:** arrives at 1.21.6, filed under 26.2 as §V6 (recorded render state) and §V92
(`RenderType.create` → `RenderSetup`).

L19. **`RenderLevelStageEvent` is ABSTRACT; one subclass per stage (1.21.6+)** · `getStage()` and the `Stage` enum are
gone; `AfterSky`, `AfterOpaqueBlocks`, `AfterEntities`, `AfterBlockEntities`, `AfterTranslucentBlocks`, `AfterTripwireBlocks`,
`AfterParticles`, `AfterWeather`, `AfterLevel` are concrete. · **What breaks:** `@SubscribeEvent onX(RenderLevelStageEvent e)`
compiles, then `IllegalArgumentException: Cannot register listeners for abstract class` at client load (§X33 caught by
`tools/audit-event-listeners.py`). · **Fix:** name the stage class and drop the `getStage()` test; a listener that tested two
stages becomes two. **Unlike 26.2, the 1.21.8 event STILL carries `getPoseStack()`, `getCamera()` and the modelview matrix**
(`javap` the class: 1.21.8 has all three, 26.2 has none and passes `null` to its posts), so the listener body is unchanged
and the §X33 augment ("compiles, registers, draws nothing") does NOT apply here; verify before assuming it does on 1.21.10+.
First fork: 55 listeners audited clean, the stage ones in its client lifecycle class, an editor's client events and a sky hook.
· **Anticipates:** arrives at 1.21.6, filed under 26.2 as §X33 (and its augment, which is the 26.2-only half).

L20. **`SoundManager.play(SoundInstance)` returns `SoundEngine.PlayResult` (by 1.21.8)** · void → `PlayResult`; a mixin
`@Inject(method = "play")` with `CallbackInfo` no longer binds. · **What breaks:** mixin APPLY failure at client load;
compile-clean (R17 shape (f)); `audit-mixin-targets.py`'s callback-kind check reports it. · **Fix:** per-target overlay of the
mixin: `CallbackInfoReturnable<PlayResult>` and `setReturnValue(PlayResult.NOT_STARTED)`. **Choosing the value:** the only
consumer of the result, `MusicManager.startPlaying`, shows the "now playing" toast on `STARTED`. A mixin that blocks a sound
and starts nothing in its place answers `NOT_STARTED` (a fork's combat-music lock: its own track plays through a separate
call); one that starts a replacement answers `STARTED`. · **Anticipates:** arrives by 1.21.8, filed under 26.2 as R17 (f)
(the 26.2 entry already names this exact target).

L21. **`@OnlyIn` is a mod-LOADING ERROR in dev from 1.21.6 (`loadwarning.neoforge.onlyin`)** · NeoForge stopped stripping
`@OnlyIn` members and warns on every use; in a dev run that stops Gate B before a test runs and puts `LoadingErrorScreen` up
on a client. Production jars are inert, which is why nobody ships it as a bug. · **Fix:** the layer's `strip_onlyin=true`
target property removes the annotation from the whole PREPARED tree (shared files and overlays alike -- a rename row cannot
reach an overlay); first fork: 93 stripped. Nothing is lost: the strip bought nothing on the common classes. ·
**Anticipates:** arrives at 1.21.6, filed under 26.2 as §V79.

L22. **`@EventBusSubscriber` lost `bus()` (FML 9, 21.6+)** · the bus follows from the event class (a mod-bus event is
delivered on the mod bus, anything else on the game bus). · **What breaks:** `cannot find symbol: bus` / `Bus`. ·
**Fix:** three shared rows drop `bus = Bus.MOD|GAME` (either argument position) and the `EventBusSubscriber.Bus` import; the
subscriber stays where it was because the event type decides. The class is in the FML `loader` jar, not in `neoforge-universal`.
· **Anticipates:** arrives at 1.21.6, filed under 26.2 as §V38b.

L23. **Entity (and LivingEntity) save/load → `ValueOutput` / `ValueInput` (1.21.6)** · `addAdditionalSaveData(CompoundTag)`
→ `(ValueOutput)`, `readAdditionalSaveData(CompoundTag)` → `(ValueInput)` (BlockEntity moved too). · **What breaks:** `does not
override`, then `CompoundTag cannot be converted to ValueOutput` through the body; a mixin `@Inject` into either mismatches its
descriptor at apply. · **Fix:** two shared rows retype the mixin handler's parameter; a body that only PUTS scalars keeps
its text (`putInt/putString/putBoolean` exist), one that reads needs `ValueInput`'s `Optional`/`...Or` getters and has no
`contains()` -- that one is a per-fork overlay. · **Anticipates:** arrives at 1.21.6, filed under 26.2 as §V31.

L24. **GUI drawing is 2D: `GuiGraphics.pose()` is a `Matrix3x2fStack` (1.21.6)** · `pose().pushPose()/translate(x,y,z)/
scale(x,y,z)/mulPose(..)` → `pushMatrix()/translate(x,y)/scale(x,y)/rotate(angle)`; z is gone (draw order is submission order).
· **What breaks:** `incompatible types` on every GUI/HUD render with a `PoseStack`. · **Fix:** no shared row (a GUI's z use
is a design decision); the fork's GUI files become overlays or are rewritten to the 2D calls; `Screenshot.grab` and the Gate C
harness's GUI hooks need the same treatment. · **Anticipates:** arrives at 1.21.6, filed under 26.2 as §V6 (`pose()` returns
a 2D `Matrix3x2fStack`).

L25. **`LevelRenderer.renderLevel` has a new signature (1.21.5-1.21.8 frame graph + fog buffer)** · `(DeltaTracker, boolean,
Camera, GameRenderer, LightTexture, Matrix4f, Matrix4f)` → `(GraphicsResourceAllocator, DeltaTracker, boolean, Camera, Matrix4f,
Matrix4f, GpuBufferSlice, Vector4f, boolean)`: the GameRenderer and LightTexture are gone, and the fog buffer, fog colour and a
"render the sky" flag arrived. · **What breaks:** mixin APPLY (`Invalid descriptor … Expected … found`), compile-clean (R17
(a)). · **Fix:** two shared rows rewrite the `method = "renderLevel(...)"` descriptor and an `@Inject` handler's nine-parameter
list in one go (the handler may keep ignoring the new parameters). · **Anticipates:** arrives at 1.21.6, filed under 26.2 as R17
(a)/(e) and M20 (the frame graph).

L26. **`ServerCommonPacketListenerImpl.send` takes a netty `ChannelFutureListener` (1.21.6)** · was `PacketSendListener`.
· **What breaks:** a mixin on `send(Packet, PacketSendListener)` fails at apply; callers passing a `PacketSendListener` do not
compile. · **Fix:** two shared rows retype the descriptor and the handler parameter (`io.netty.channel.ChannelFutureListener`).
· **Anticipates:** arrives at 1.21.6; **no 26.2 entry exists** (a 26.2 port should expect the same row).

L27. **Bidirectional payloads register only the SERVER handler from the 3-argument form (21.8; CATALOG §V81)** ·
`playBidirectional / commonBidirectional / configurationBidirectional(type, codec, serverHandler)` now delegates with a null
client handler; the client's `ClientNetworkRegistry` refuses to load: "Some clientbound payloads are missing client-side
handlers: [..]". Compile-clean, Gate B green (a dedicated server never validates it), found by Gate C `launch`. ·
**Fix:** a shared row repeats the handler (method reference, or an expression lambda -- block lambdas are left to the author)
on the 3-argument shape only, so `playBidirectional(T, C, H)` → `(T, C, H, H)`; a handler for a bidirectional payload already
branches on `ctx.flow()`. The first fork's per-fork network-handler overlay is gone: the row plus a second one
(`PacketDistributor.sendToServer` → `net.neoforged.neoforge.client.network.ClientPacketDistributor.sendToServer`, fully
qualified because a row cannot add an import) reproduce it exactly (diff of the prepared file against the old overlay: the
import and a comment). · **Anticipates:** arrives at 21.8 (1.21.8), filed under 26.2 as §V81.

L28. **FML 10 removed ModLauncher: a coremod is a `ClassProcessor`, registered by a `ClassProcessorProvider` (21.9; CATALOG M42, X53)** ·
`cpw.mods.modlauncher.api.ITransformationService` / `ITransformer` and `META-INF/services/cpw.mods.modlauncher.api.ITransformationService`
are gone. The replacement is `net.neoforged.neoforgespi.transformation.ClassProcessor` (`name()`, `handlesClass(SelectionContext)`,
`processClass(TransformationContext)`, ordering via `runsBefore/runsAfter/orderingHint`) handed to FML by a
`ClassProcessorProvider.createProcessors(Context, Collector)`; FML builds the processor set in `FMLLoader.createClassProcessorSet`
with `ServiceLoader.load(ClassProcessorProvider.class)` on the launch class loader, i.e. BEFORE the game layer exists, so the provider is
found only where that loader can see a `META-INF/services/net.neoforged.neoforgespi.transformation.ClassProcessorProvider` file: the dev
classpath, or a jar FML appends to it (manifest `FMLModType: LIBRARY`, which `FMLLoader.loadPlugins` appends; a `MOD` jar's services are
NOT scanned for this interface, and the early-service jar scan does not list it). · **What breaks:** nothing at compile or load. The old
services file is simply never read, so the mod boots without its transformations; Gate A and B pass. Found only because the mod's own
the mod's own bootstrap log was missing from the Gate B run (X53). · **Fix:** a per-target provider class (a `ClassProcessor` provider class, an overlay
file with no counterpart in the authors' tree) touching the old service class first (its static initialiser starts the bootstrap log) and
calling `collector.add(<the mod's transformer>)`, plus the services resource in the target's `resources/`. `SelectionContext.type()` is an
ASM `Type` (`getClassName()` is dotted); `processClass` edits the passed `ClassNode` in place and returns the `ComputeFlags` to recompute.
· **Anticipates:** arrives at 21.9 (1.21.9/1.21.10); 26.x inherits it unchanged (no dedicated 26.2 entry).

L29. **A SHIPPED jar cannot register an FML 10 class processor at all** · `ClassProcessorProvider` is found with
`ServiceLoader` while the thread context loader is the EARLY-SERVICES loader, which holds only top-level `mods/` jars
declaring one of five early services (candidate locator, file reader, dependency locator, graphics bootstrapper,
immediate window). A jarJar'd library is never scanned for them, and making the mod jar itself an early-service jar
masks it out of the transforming loader (its mixins stop applying). So a coremod that worked from `mods/` on 1.21.1
(ModLauncher scanned the mods dir) runs on 1.21.10 only in DEV. · **Measured with `tools/shipped-gate.py`** (a real
server, the built jar in mods/): 1.21.1 and 1.21.8 PASS, 1.21.10 FAIL while every dev gate was green. · **Fix used
for a mod that also has an agent backend:** initialise the backend from `FMLConstructModEvent` (the agent retransforms,
so it need only be selected before the first transformed class loads) -- restores 1.21.1's shipped behaviour; a
coremod-only mod has no shipped path on FML 10 and should say so loudly.

L30. **GeckoLib 5.3 (1.21.10 line): `GeoRenderEvent.*` render-layer events are INTERFACES (CATALOG M44)** ·
`GeoRenderEvent.Entity/ReplacedEntity/Block.CompileRenderLayers` are interfaces; the postable events are the concrete classes
`software.bernie.geckolib.event.entity.CompileEntityRenderLayersEvent`, `…event.replacedentity.CompileReplacedEntityRenderLayersEvent`,
`…event.block.CompileBlockRenderLayersEvent`. · **Symptom:** `addListener` on the interface compiles; the bus calls `getSuperclass()`
on it, gets null and NPEs, so mod loading breaks on the CLIENT only. A secondary `IllegalStateException: No value present` from a
mod-container lookup in a reload listener was fallout. Found by Gate C `launch`, not Gate B. · **Fix:** listen on the concrete classes.
· **Guard:** `tools/audit-event-listeners.py` now flags an interface parameter as well as an abstract class, checks every
event type a dependency jar declares (it was `net.neoforged`/`net.minecraft` only, so GeckoLib's were never looked at), and
resolves nested types (`GeoRenderEvent.Entity.CompileRenderLayers` → `…$Entity$CompileRenderLayers`). A/B'd on the first fork at 1.21.10:
the pre-fix file is caught (3 listeners, exit 1), the fixed one is clean; 1.21.5–1.21.11 sweep: no new findings.

L31. **1.21.11: `ResourceLocation` → `Identifier` and 329 class moves (CATALOG M45)** · `net.minecraft.resources.Identifier` is a pure
rename (§V4 arrives here, not at 26.x); `FriendlyByteBuf.readResourceLocation/writeResourceLocation` → `readIdentifier/writeIdentifier`;
`ResourceKey.location()` → `identifier()`; **`TagKey.location()` is unchanged**, so never a bare `.location()` row. 329 classes moved
(the start of the 26.x sub-package split). Mixin descriptors name classes in slash form (`Lnet/minecraft/...;`), which a dotted move
row cannot reach: give those moves slash-form rows. Shared helpers and inherited overlays are not rewritten by the rows (CATALOG X54).
· **Anticipates:** §V4, §V3.

L32. **1.21.11 signature changes found by the mixin audit (CATALOG M46)** · `Camera.setup(Level, …)` (was `BlockGetter`);
`CloudRenderer.render` has a `long gameTime` before the partial tick; `FogRenderer.setupFog` lost `boolean thickFog`;
`LevelRenderer.isSectionCompiled` → `isSectionCompiledAndVisible`; `RenderType` → `net.minecraft.client.renderer.rendertype`
(`RenderTypes.*`; here `entityCutout`/`entityCutoutNoCull` still mean what they did, §V60's swap comes later); `SkullModelBase` →
`client.model.object.skull`; `SavedDataType.Factory` takes a `ServerLevel` and `SavedData.Context` is gone; command
`hasPermission(2)` → `Commands.hasPermission(Commands.LEVEL_GAMEMASTERS)`. · **Anticipates:** §V15, §V60.

L33. **GeckoLib 5.4.5 (1.21.11): no hidden state on `GeoBone`; hooks take `RenderPassInfo<R>` (CATALOG M47)** · `setHidden`,
`getChildBones`, `renderBone` and `buildRenderTask` are gone; layer hooks take `RenderPassInfo<R>`; `frameSnapshot` is live only
inside `RenderPassInfo.renderPosed`. · **Fix:** hide bones with a `BoneUpdater` added in `preRender`; draw with
`renderPassInfo.model().render(...)` inside `renderPosed`. · **Anticipates:** extends §V18d.

L34. **GeckoLib 4.x → 5.1 at 1.21.5: render-state renderers without interface injection (CATALOG M49)** · 5.1.0's
`GeoEntityRenderer<T, R extends EntityRenderState & GeoRenderState>` closes only at runtime (a mixin), and its jar has no
`interface_injections.json`. · **Fix:** keep `R` a type variable on each renderer and layer (`<R extends EntityRenderState &
GeoRenderState>`, `new Layer<>(this)`); capture entity data in `addRenderData` as `DataTicket`s; `renderForBone` →
`addPerBoneRender`; `BlockAndItemGeoLayer.getRelevantBones`. Class moves (`renderer.GeoRenderer` → `renderer.base`,
`animation.AnimatableManager`/`AnimationController` → `animatable.manager`/`animatable.processing`, `animation.AnimationState`
→ `animatable.processing.AnimationTest`) and `getControllerState()` → `getAnimationState()` are shared 1.21.5 rows. ·
**Anticipates:** §V18b (5.5 on 26.x, where the jar does ship the injection).

L35. **1.21.8 residue from a fork port (CATALOG M50-M52)** · Jade 19's `Element` sizes by the `width`/`height` fields
(`getSize()` gone); `ScreenEffectRenderer.renderScreenEffect(boolean, float)` is an instance method that also draws the
totem animation; and with `@OnlyIn` stripped, a common class's former client-only method that widens
`Minecraft.getInstance().player`/`.level` to `Player`/`Level` fails server verification -- shared rows route it through
`ClientRefs`. · **Anticipates:** §V79.

L36. **1.21.10 residue from a fork port: GeckoLib 5.3 layer hooks and the 1.21.9 submit pipeline (CATALOG M53, M54)** ·
`renderStackForBone` → `submitItemStackRender` (+ render colour), which 5.3 calls WITHOUT the bone-pivot transform 5.1/5.2
applied; `addPerBoneRender` gains `didRenderModel`; `getGeoArmorRenderer(ItemStack, EquipmentSlot)`. Mixins: `render` →
`submit` on entity renderers and `EntityRenderDispatcher`, `setupRender` → `cullTerrain`, `renderScreenEffect(..., SubmitNodeCollector)`;
a see-through-entity `MultiBufferSource` wrapper becomes a `SubmitNodeCollector` wrapper. · **Anticipates:** §V6 (26.x).

L37. **GeckoLib 5.4.5 (1.21.11), from a fork port (CATALOG M55)** · `GeoModel.setCustomAnimations` gone (head look →
renderer `adjustModelBonesForRender(RenderPassInfo, BoneSnapshots)`); `preRender`/layer hooks take `RenderPassInfo`; no
controller `State` (`GeoControllers.isStopped`: `getPlayState() == STOP || hasAnimationFinished()`, read BEFORE any `reset()`), `forceAnimationReset()` → `reset()` (which sets STOP); `buildRenderTask` →
`GeoPasses.submitModel`; `GeoBone.isHidden()` → `frameSnapshot.isHidden()`. · **Anticipates:** §V18d.

**Tally (1.21.5 → 1.21.8 against the 26.2 era jump):** of the 11 changes above, 10 have a 26.2 counterpart (§V93, §V6, §V92,
§X33, R17 (f), §V79, §V38b, §V31, §V6, R17 (a), §V81); 1 does not (L26, `send`'s listener). Adding L16 (Player.setItemSlot,
no 26.2 entry) the minor hops 1.21.6-1.21.8 carry roughly a dozen of the changes 26.2 then lists as "era jump" changes:
the era jump is partly a reading of the four unreleased 1.21.x lines in between.

### Runtime deltas (Gate B over a clean compile, 1.21.1 → 1.21.4)
**Measured:** the first 1.21.4 Gate B run was **27 red** with main + test compiling clean on both targets.
Every one was one of the six below. After them: `ci,default` 613 passed, batch composition identical to the
1.21.1 control (122/122 batches), 1.21.1 `--all` 846/846 on the same commit.

U13. **`custom_model_data` in command/SNBT TEXT is a record** · **Pattern:** `give @p x[custom_model_data=3]`,
`putInt("minecraft:custom_model_data", 3)` in entity/item NBT · **Runtime:** `Malformed
'minecraft:custom_model_data' component: 'Not a map: 3'` at command parse, and, from NBT written with
`putInt`, the later and further-away `Tried to load invalid item`. Neither message names the version ·
**Fix:** 1.21.4's component is `{floats:[3f],flags:[],strings:[],colors:[]}`. A pair that RENDERS the
spelling (`snbt(int)`, `put(CompoundTag,int)`) plus a corrector at every text choke point (commands the
mod runs, datapack sources it compiles), working both directions so text written for either version
survives on the other.

U14. **Attribute ids lost `generic.` / `player.` / `zombie.` (1.21.2)** · **Pattern:** `minecraft:generic.armor`
in a command, a datapack, a lesson · **Runtime:** the command fails to parse on 1.21.4 (and the short id
fails on 1.21.1) · **Fix:** a pair naming the id per target, and a BIDIRECTIONAL corrector that accepts a
candidate only if the live registry resolves it: strip a prefix, or try each prefix, never rewrite by
pattern alone.

U15. **A bundled tool pinned to one version silently UNDOES the Java-side fix** · **Pattern:** a compiler
or generator the mod shells out to (here CBScript's `attribute_ids.py`) with its own version table ·
**Runtime:** none at compile; the tool rewrote `minecraft:armor` back to `minecraft:generic.armor` after the
mod had corrected it, and the datapack died at load · **Fix:** pass the target in (`CBSCRIPT_MC`) and make
the tool's table version-aware. **Every tool the mod shells out to is a third dialect table.**

U16. **NeoForge custom ingredients dispatch on `neoforge:ingredient_type` (21.4), not `type` (21.1)** ·
**Pattern:** `{"type":"neoforge:components","items":[…],"components":{…},"strict":false}` ·
**Runtime:** `Input does not contain a key [neoforge:ingredient_type]` behind two `Failed to parse either`
layers, and the recipe is dropped at load · **Fix:** one-constant pair for the key. The body keys
(`items`/`components`/`strict`) are unchanged. Read from 21.4.157's `IngredientCodecs`.

U17. **A 1.21.4 GameTest single-test batch runs ~20,000 ticks a second** · **Pattern:** a test that waits on
work done on ANOTHER thread (an agent, a queue, a debounce) with a tick budget · **Runtime:** the budget is
spent before the work starts, so the test reads as a dead feature · **Fix:** poll with a small real-time
pause per failed check (a `succeedWhen` that sleeps a few ms), not a wider tick budget.

U18. **A shared "clear stale fixtures" sweep removes a sibling's live fixture** · **Pattern:** every test
calls a helper that removes all mock players at its start · **Runtime:** in a concurrent batch it removed
a mock another test was halfway through using. The victim failed naming its own feature ("Nobody is in the
world to hang the picture"), not the sibling · **Fix:** ownership, not presence. Record which
`GameTestInfo` made each fixture and treat it as stale only once that test `isDone()`. This is not
version-specific; 1.21.4's faster batches (U17) are what made the overlap likely.

### Runtime deltas past Gate B (a real server, stored content)

U19. **An EMPTY dedicated server stops ticking after 60 s (1.21.2, `pause-when-empty-seconds`)** ·
**Pattern:** any headless harness that drives a real `runServer` with nobody in the player list and
watches from a `ServerTickEvent` handler (a FakePlayer is NOT in the player list) · **Runtime:** one INFO
line, `Server empty for 60 seconds, pausing`, and then the tick handler never runs again. Queued server
TASKS still run, so the work finishes (here a wish reached DONE 16 s after the pause) and nothing reports
it. The run looks hung until its own timeout, which is itself tick-driven, so that never fires either ·
**Fix:** seed `pause-when-empty-seconds=0` into the DEV run's `server.properties` before every
`runServer` (a `doFirst`). The key is unknown to 1.21.1 and inert there, so no version branch is needed.
Leave a real server's default alone unless someone decides otherwise: pausing an empty server is the
intended behaviour. **Diagnosis in one step:** `jstack` shows the Server thread idle in
`waitUntilNextTick`, which is also what a healthy idle server looks like, so grep the log for `pausing`.

U20. **A STORED compiled datapack is in the dialect of the build that compiled it** · **Pattern:** a mod
that ships or keeps compiled datapack zips (a starter library, a per-world store) and copies them into a
world at boot · **Runtime:** U13/U14's text, frozen in a zip: `Malformed 'minecraft:custom_model_data'
component: 'Not a map: 8'` at pack load, four functions dropped, and the pack still reports enabled.
Every gate stays green, because a correction at the COMPILE choke point never sees a zip compiled on
another day · **Fix:** correct on the COPY into the world, not in the store: every `.mcfunction`
through the same corrector the command path uses, and `pack.mcmeta`'s `pack_format` set to
`SharedConstants.getCurrentVersion().getPackVersion(PackType.SERVER_DATA)` (48 on 1.21.1, 61 on 1.21.4;
the accessor is identical on both). Leaving the stored zip untouched keeps it readable by the build that
wrote it and gives an upgrade census nothing to migrate. Recompiling from source is tidier only if the
source was kept; the starter zips here carried none. **The test that sees it:** parse each installed
function with `CommandFunction.fromLines(id, server.getCommands().getDispatcher(), source, lines)`, the
call `ServerFunctionLibrary` makes. It needs no reload, so it is safe inside a GameTest batch.
· ⚠ **AUGMENT — the crossing itself, measured (T11.7), and why the census cannot see this.** Gate B's
1.21.1 world (952 stored artifacts, 173 datapacks) opened by the 1.21.4 jar: the data fixer takes it
3955 → 4189 and the byte-for-byte artifact census resolves **952/952 — while the FIRST load refuses 181
functions** (160 `custom_model_data` int, 20 `generic.scale`). The world's own `datapacks/` copies are
still the old dialect; the census compares STORED artifacts, never whether their functions load. The
boot-time install then rewrites 172 packs and reloads, leaving 1 refusal — the same Create-only item
the 1.21.1 control refuses. So a crossing heals on its first boot **only for packs that are also in the
store**: a pack that exists solely in the world's `datapacks/` keeps its old spelling. Count the
refusals on the reload AFTER the install, beside the census, and compare with the old jar on the same
world. · **And the TEST for it stayed green with the fix switched off — twice — for two reasons worth
keeping (T11.8):** (1) a `give` on a pack not yet loaded installs it, answers "Loaded — do that
again!" and returns SUCCESS, so a replay straight after boot never runs the function it is testing;
(2) a GameTest world is REUSED between runs, so the packs a previous (fixed) run installed were still
loaded, and a "load if missing" step never exercised this build's install path. The replay has to
REINSTALL through the production install call every run and wait for THAT reload. And a third, which
was a product bug: the command runner counted a command that refused with `sendFailure` + `return 0`
as a success (only a thrown `CommandSyntaxException` was a failure), so a starter whose function never
loaded was a granted wish. Treat `0` **and** a red (`sendFailure`) line as a refusal.

### Background
_(the on-demand corpus; the first real 1.21.x↔1.21.y migration populates it)_

Format (same as the main catalog):
`NN. **Short title** · **Pattern:** <old shape> · **Error:** <compileJava message> · **Fix:** <the 1.21.y replacement>`
(runtime-only patterns use **Runtime:** in place of **Error:**.)
