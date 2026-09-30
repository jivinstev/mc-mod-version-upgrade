# Version-family transform: vanilla MC 1.20.x → 1.21.x — symptom → fix
<!-- AXIS: version-family (SRC_MC family → DST_MC family). Instantiated for the default
     1.20.1 → 1.21.1 hop. For a same-family minor hop (1.21.x → 1.21.y) this file mostly
     does NOT apply — use references/minor-version-deltas.md instead. These changes are
     independent of the loader. -->

These are engine changes independent of the mod loader. The biggest are the
**1.20.5 Data Components** overhaul (item NBT is gone) and the `ResourceLocation`
constructor going private. Spread across 1.20.2, 1.20.5, 1.21, 1.21.1.

## `ResourceLocation` constructor is private (everywhere)
```java
new ResourceLocation("modid", "thing")   // ❌ no longer accessible
new ResourceLocation("modid:thing")       // ❌
```
```java
ResourceLocation.fromNamespaceAndPath("modid", "thing")   // ✅ two-arg
ResourceLocation.parse("modid:thing")                      // ✅ single string
ResourceLocation.withDefaultNamespace("thing")             // ✅ minecraft:thing
```
Codemod the two-arg and colon forms; this appears hundreds of times in a big mod.

## Item data: NBT → **Data Components** (1.20.5) — major
`ItemStack` no longer has a free-form `CompoundTag`. Removed:
`getTag()`, `getOrCreateTag()`, `setTag()`, `hasTag()`, `getOrCreateTagElement`.
- Vanilla data now lives in typed components: custom name, lore, enchantments,
  damage, custom model data, food, etc. Access via
  `stack.get(DataComponents.X)` / `stack.set(DataComponents.X, v)` /
  `stack.getOrDefault(...)` / `stack.has(...)`.
- **Custom per-item data** (a mod's own NBT) → define a `DataComponentType<T>`
  registered against `Registries.DATA_COMPONENT_TYPE`, with a `Codec` and a
  `StreamCodec`. Replace every `getOrCreateTag().putX/getX` with component get/set.
- Free-form leftover NBT bridge: `CustomData` component
  (`DataComponents.CUSTOM_DATA`) wraps a `CompoundTag` — the fastest way to port a
  mod that stored an ad-hoc tag: `stack.get(DataComponents.CUSTOM_DATA)` →
  `CustomData`, `.copyTag()` to read, `CustomData.update(type, stack, tag->…)` to write.

## Enchantments / mob effects / other registry objects are `Holder<>`-wrapped
- Enchantments are data-driven (1.20.5). `Enchantment` references become
  `Holder<Enchantment>`, looked up from the registry
  (`registryAccess.registryOrThrow(Registries.ENCHANTMENT).getHolderOrThrow(key)`).
  `EnchantmentHelper.getItemEnchantmentLevel(enchantment, stack)` now takes a Holder.
- `MobEffectInstance` takes `Holder<MobEffect>`, not `MobEffect`. `MobEffects.X`
  are already `Holder<MobEffect>`. Custom effects: register and wrap in a Holder.
- `Potion`, `Attribute`, `DamageType` similarly Holder-based.

## Entity data + save/load signatures
- `defineSynchedData()` → `defineSynchedData(SynchedEntityData.Builder builder)`;
  inside, `builder.define(ACCESSOR, default)` instead of `this.entityData.define(...)`.
- `Entity`/`BlockEntity` NBT hooks gained a `HolderLookup.Provider`:
  `addAdditionalSaveData(CompoundTag)` → `(CompoundTag, HolderLookup.Provider)`
  (entities) and BlockEntity `saveAdditional(CompoundTag)` →
  `saveAdditional(CompoundTag, HolderLookup.Provider)`, `load(CompoundTag)` →
  `loadAdditional(CompoundTag, HolderLookup.Provider)`.
- `CompoundTag` (de)serialization of registry objects now often goes through codecs
  with the provider.

## Registration / registry access
- `Registry.register(...)` and `BuiltInRegistries.*` for static registries;
  `Registries.*` for `ResourceKey<Registry<>>` constants.
- `registryAccess().registryOrThrow(Registries.X)` to resolve dynamic registries at
  runtime (enchantments, damage types, worldgen).
- `RegistryObject`/`ForgeRegistries` — see the Forge→NeoForge reference.

## Rendering / client
- GUI drawing uses `GuiGraphics` (since 1.20) — if porting from ≤1.19 code you'll
  also need `PoseStack`→`GuiGraphics`. From 1.20.1 this is already the case.
- `RenderType`, `VertexConsumer`, `PoseStack`, `MultiBufferSource` mostly stable.
  `Sheets`, `RenderType.entityCutout(...)` stable.
- `ItemProperties.register` stable; predicate now may involve components.
- Particle registration: `RegisterParticleProvidersEvent` (NeoForge client event).
- Font/`Component` API stable; `Component.translatable/literal` unchanged.
- Custom `ShaderInstance` / core shaders: the JSON `blend`/`vertex` format and some
  uniforms changed across 1.20→1.21; post-processing `.fsh/.vsh` may need uniform
  and `#moj_import` fixups. Expect to iterate against runtime shader-parse errors.

## Damage sources
- `DamageSource` is created from the registry: `level.damageSources().X(...)`
  (already true in 1.20.1). Custom damage types are data-driven `DamageType`
  entries (`Holder<DamageType>`), referenced by `ResourceKey<DamageType>`.

## Misc frequently-hit
- `Level.getBlockEntity`, `BlockPos`, `Vec3` stable.
- `AttributeSupplier.Builder` + `LivingEntity.createLivingAttributes()` stable, but
  new attributes exist in 1.21 (`Attributes.MAX_ABSORPTION`, movement/step split);
  `Attribute` refs are `Holder<Attribute>` in some APIs.
- `CreativeModeTab.Builder` stable; content fill via
  `BuildCreativeModeTabContentsEvent` (NeoForge).
- `SoundEvent.createVariableRangeEvent` / `createFixedRangeEvent` for registration.
- `ResourceKey`/`TagKey` construction takes the private-constructor
  `ResourceLocation` — use the factory methods above.
- `net.minecraft.util.RandomSource` (already 1.19+) for randomness.
- `pack.mcmeta`: `pack_format` 34 (resource, 1.21.1) / 48 (data, 1.21.1); mixed
  packs can list `supported_formats` ranges.

## `FriendlyByteBuf.readNbt()` now rejects non-compound tags — SILENT until a client connects

1.21 tightened the no-arg overload to throw on anything that isn't a `CompoundTag`:

```java
public static CompoundTag readNbt(ByteBuf buffer) {
    Tag tag = readNbt(buffer, NbtAccounter.create(2097152L));
    if (tag != null && !(tag instanceof CompoundTag)) throw new DecoderException("Not a compound tag: " + tag);
    ...
}
```

`writeNbt(@Nullable Tag)` still accepts **any** tag, so a `ListTag` round-trip that worked in 1.20.x
now encodes fine and blows up on decode. Fix — use the accounter overload, the one still typed `Tag`:

```java
Tag tag = buf.readNbt(NbtAccounter.create(2097152L));   // not buf.readNbt()
```

**Why this one is expensive to find.** It compiles clean (both overloads exist), survives Gate B
(a dedicated server never decodes its own clientbound payload), and only fires when a real client
receives the packet — so the whole cost lands in Gate C. In a space-exploration mod it presented as
`Failed decoding custom payload <modid>:main/v1/<modid>/sync_planets` followed by the client being
disconnected and the harness timing out with the *misleading* `world did not finish loading within
180s`. **Read past the timeout to the DecoderException above it** — the timeout is the symptom, not
the fault.

Only flag a site whose writer actually emits a non-compound. The symmetric
`writeNbt(x.getCompound())` / `readNbt()` pair is correct and must be left alone.

## Client extension hooks that are declared WITHOUT `@Override` — SILENT, and the worst class here

**This is the single most expensive failure family in this repo.** Three separate instances in a space-exploration mod (~550 files)
alone, each of which compiled clean, ran clean, logged nothing, and quietly removed a feature.

The loader's client hooks (`DimensionSpecialEffects` sky/cloud rendering, `IItemExtension`,
`IClientItemExtensions`, `IClientBlockExtensions`, `IClientFluidTypeExtensions`) are **overridable
methods and interface defaults, not abstract ones**. So when a signature changes — or the hook is
deleted outright — an out-of-date declaration is just *an unused method on your class*. Nothing
errors. The loader calls its own default instead, and you get vanilla behaviour where the mod
intended custom behaviour.

Two real ones, both found only by looking at the game:

| Hook | 1.20.x | 1.21.1 | Symptom |
|---|---|---|---|
| `DimensionSpecialEffects#renderSky` | `(…, float, **PoseStack**, Camera, …)` | `(…, float, **Matrix4f** modelViewMatrix, Camera, …)` | **every custom sky is vanilla** — no Earth over the Moon, no Phobos over Mars, and vanilla clouds drift over airless worlds |
| `Item#initializeClient(Consumer<IClientItemExtensions>)` | exists | **REMOVED** — replaced by `RegisterClientExtensionsEvent` | every item drawn by a `BlockEntityWithoutLevelRenderer` has **no inventory icon**: a space mod's four rockets, its rover and several machine and decorative blocks |

`renderClouds` moved the same way in the same release (it gained a `modelViewMatrix`, so 8 params
became 9).

### The fixes

```java
// renderSky: 1.21 hands over a raw matrix. Rebuild the PoseStack exactly as vanilla's
// LevelRenderer.renderSky does, and the existing renderer body needs no other change.
@Override
public boolean renderSky(ClientLevel level, int ticks, float partialTick, Matrix4f modelViewMatrix,
                         Camera camera, Matrix4f projectionMatrix, boolean isFoggy, Runnable setupFog) {
    PoseStack poseStack = new PoseStack();
    poseStack.mulPose(modelViewMatrix);
    ...
}

// item renderers: register through the event, and do not assume it fires after FMLClientSetupEvent
@SubscribeEvent
public static void onRegisterClientExtensions(RegisterClientExtensionsEvent event) {
    if (ITEM_RENDERERS.isEmpty()) populate();
    ITEM_RENDERERS.forEach((item, renderer) -> event.registerItem(new IClientItemExtensions() {
        @Override public BlockEntityWithoutLevelRenderer getCustomRenderer() { return renderer; }
    }, item));
}
```

### The rule, and how to find these

**Put `@Override` on every loader hook you implement, and treat a missing one as a defect.** With the
annotation, a changed or deleted hook is a *compile error* — which is the entire point. Without it the
port ships and the feature is gone. Add it during the port, not after.

Sweep a finished port for the ones already written:

```bash
# public methods on client-facing classes that override nothing — the candidate set
grep -rn -B2 "public boolean render\(Sky\|Clouds\|SnowAndRain\)\|public void initializeClient" src/main/java   | grep -v "@Override"
```

Then confirm each against the loader's own source, which is the authority:

```bash
SJ=$(find ~/.gradle/caches -name "neoforge-21.1.*-sources.jar" | head -1)
unzip -p "$SJ" net/neoforged/neoforge/client/extensions/IDimensionSpecialEffectsExtension.java
unzip -l "$SJ" | grep -i IItemExtension     # absent method == the hook was removed
```

### Why no automated gate catches it

Gate A (compile) passes by definition — that is the bug. Gate B (headless server) never renders and
has no inventory. **Only Gate C sees it, and only if something asks the right question.** The space mod's
sky gate initially reported PASS because it asserted *which class* the effects object was — which was
genuinely correct; the class was installed, its methods were simply never called. What found it was
**photographing the sky and looking**, and then a **control** — photographing a known-good vanilla
space-mod planet in the same run — to tell "our data is wrong" from "the port is broken". A
registry-or-class check needs a known-good control beside it or it cannot distinguish "zero" from
"my check does not work".

## Strategy for the semantic buckets
Data Components and Holder-wrapping touch a lot of call sites but each fix is
local. Do them file-by-file where the compiler points. For a mod that only used
item NBT as a scratchpad, the `CustomData` component bridge is by far the fastest
route to a compiling port — reserve full typed `DataComponentType`s for data the
mod actually reads structurally.
