# Loader transform: Forge → NeoForge API — symptom → fix
<!-- AXIS: loader-transform (SRC_LOADER→DST_LOADER). Applies whenever SRC_LOADER=forge,
     DST_LOADER=neoforge. SKIP this entire file if SRC_LOADER == DST_LOADER (e.g. a
     neoforge→neoforge minor-version hop). Instantiated here for the default 1.20.1/1.21.1
     target; the loader renames themselves are largely version-independent within the family. -->

NeoForge forked Forge at 1.20.1, then diverged hard. Package moves are mostly
mechanical; capabilities and networking are full rewrites. Symptom = the compile
error you'll see; Fix = what to change.

## Package / import renames (mechanical — codemod these)
| Forge | NeoForge |
|---|---|
| `net.minecraftforge.api.distmarker.*` (`Dist`, `OnlyIn`) | `net.neoforged.api.distmarker.*` |
| `net.minecraftforge.fml.common.Mod` | `net.neoforged.fml.common.Mod` |
| `net.minecraftforge.fml.*` (loading, `FMLEnvironment`, lifecycle events) | `net.neoforged.fml.*` |
| `net.minecraftforge.fml.event.lifecycle.*` (`FMLCommonSetupEvent`, `FMLClientSetupEvent`) | `net.neoforged.fml.event.lifecycle.*` |
| `net.minecraftforge.eventbus.api.*` (`IEventBus`, `SubscribeEvent`, `EventPriority`) | `net.neoforged.bus.api.*` |
| `net.minecraftforge.common.MinecraftForge` | `net.neoforged.neoforge.common.NeoForge` |
| `net.minecraftforge.registries.*` (`DeferredRegister`, `ForgeRegistries`) | `net.neoforged.neoforge.registries.*` (`DeferredRegister`, `NeoForgeRegistries`) |
| `net.minecraftforge.event.*` | `net.neoforged.neoforge.event.*` |
| `net.minecraftforge.client.event.*` | `net.neoforged.neoforge.client.event.*` |
| `net.minecraftforge.common.capabilities.*` | **removed** — see Capabilities below |
| `net.minecraftforge.network.*` | `net.neoforged.neoforge.network.*` (rewritten — see Networking) |
| `net.minecraftforge.common.ForgeConfigSpec` | `net.neoforged.neoforge.common.ModConfigSpec` |
| `net.minecraftforge.fml.config.ModConfig` | `net.neoforged.fml.config.ModConfig` |
| `net.minecraftforge.common.util.LazyOptional` | **removed** — use `java.util.Optional` / plain nullable |
| `net.minecraftforge.items.*` (`IItemHandler`, `ItemStackHandler`) | `net.neoforged.neoforge.items.*` |
| `net.minecraftforge.energy.*` / `fluids.*` | `net.neoforged.neoforge.energy.*` / `fluids.*` |
| `net.minecraftforge.common.Tags` | `net.neoforged.neoforge.common.Tags` |
| `ForgeHooks` / `ForgeEventFactory` | `net.neoforged.neoforge.common.CommonHooks` / `EventHooks` |
| `net.minecraftforge.gametest.GameTestHolder` | `net.neoforged.neoforge.gametest.GameTestHolder` |

## `@Mod` main class + event bus
Forge:
```java
@Mod("modid")
public class MyMod {
  public MyMod() {
    IEventBus bus = FMLJavaModLoadingContext.get().getModEventBus();
    MinecraftForge.EVENT_BUS.register(this);
  }
}
```
NeoForge — the mod bus and `ModContainer` are **injected into the constructor**:
```java
@Mod("modid")
public class MyMod {
  public MyMod(IEventBus modBus, ModContainer container) {   // or just (IEventBus modBus)
    NeoForge.EVENT_BUS.register(this);
    MODITEMS.register(modBus);
  }
}
```
- `FMLJavaModLoadingContext.get().getModEventBus()` → use the injected `IEventBus`.
- `ModLoadingContext.get().registerConfig(...)` → `container.registerConfig(...)`.
- `MinecraftForge.EVENT_BUS` → `NeoForge.EVENT_BUS`.

## Registration — DeferredRegister
Forge `RegistryObject<T>` → NeoForge `DeferredHolder<R,T>` (or the typed helpers).
```java
// Forge
DeferredRegister<Item> ITEMS = DeferredRegister.create(ForgeRegistries.ITEMS, MODID);
RegistryObject<Item> FOO = ITEMS.register("foo", () -> new Item(props));
// NeoForge — either keep the generic form:
DeferredRegister<Item> ITEMS = DeferredRegister.create(BuiltInRegistries.ITEM, MODID);
DeferredHolder<Item, Item> FOO = ITEMS.register("foo", () -> new Item(props));
// …or the typed convenience registers (cleaner):
DeferredRegister.Items ITEMS = DeferredRegister.createItems(MODID);
DeferredItem<Item> FOO = ITEMS.registerItem("foo", Item::new, props);
```
- `.get()` still works; `RegistryObject` type just becomes `DeferredHolder`.
- Creative tabs: register a `CreativeModeTab` via `DeferredRegister.create(Registries.CREATIVE_MODE_TAB, ...)`. `CreativeModeTabEvent` (Forge) → `BuildCreativeModeTabContentsEvent` (NeoForge).
- Entity attributes: `EntityAttributeCreationEvent` is `net.neoforged.neoforge.event.entity.EntityAttributeCreationEvent`; `EntityAttributeModificationEvent` similarly.

## DistExecutor — removed
```java
DistExecutor.unsafeRunWhenOn(Dist.CLIENT, () -> ClientStuff::init);   // Forge
```
Replace with an explicit environment check, keeping client-only refs in a separate
class so the classloader never touches them on a server:
```java
if (FMLEnvironment.dist == Dist.CLIENT) ClientStuff.init();
```
`@OnlyIn(Dist.CLIENT)` still exists but prefer real separation.

## Capabilities — complete rewrite
Forge's `Capability<T>`, `@CapabilityInject`, `ICapabilityProvider`,
`AttachCapabilitiesEvent`, `LazyOptional<T>` are **all gone**. Two replacements:
- **Behavioral caps** (item handlers, energy, fluid on blocks/items/entities):
  `BlockCapability` / `EntityCapability` / `ItemCapability` + register in
  `RegisterCapabilitiesEvent`; query with `level.getCapability(cap, pos, side)`.
  `LazyOptional` → the query returns a plain nullable object.
- **Stored mod data** that used a capability just to persist state:
  use **Data Attachments** — `AttachmentType<T>` registered on
  `NeoForgeRegistries.Keys.ATTACHMENT_TYPES`; `obj.getData(TYPE)` /
  `obj.setData(TYPE, val)`; serialization via the attachment's codec. This is the
  common case for mods that hung custom fields off entities/players/levels.

## Networking — rewritten (payload system)
Forge `SimpleChannel` / `channel.registerMessage(...)` → NeoForge payloads:
```java
// register on RegisterPayloadHandlersEvent (mod bus)
public record MyPayload(int x) implements CustomPacketPayload {
  static final Type<MyPayload> TYPE = new Type<>(ResourceLocation.fromNamespaceAndPath(MODID,"my"));
  static final StreamCodec<FriendlyByteBuf, MyPayload> CODEC =
      StreamCodec.composite(ByteBufCodecs.INT, MyPayload::x, MyPayload::new);
  public Type<MyPayload> type() { return TYPE; }
}
// event.registrar("1").playToClient(MyPayload.TYPE, MyPayload.CODEC, handler);
```
Menus opened over the network: `NetworkHooks.openScreen(...)` → `player.openMenu(...)`
with an `IMenuProvider` writing extra data via the `RegisterMenuScreensEvent`/
`IMenuTypeExtension.create` server-data buffer.

## Config
`ForgeConfigSpec` → `ModConfigSpec` (same builder API). Register via the injected
`ModContainer.registerConfig(ModConfig.Type.COMMON, spec)`.

## Events that moved or renamed (common ones)
- `RegisterCommandsEvent`, `EntityJoinLevelEvent`, `LivingHurtEvent`,
  `PlayerEvent.*`, `TickEvent.*` → under `net.neoforged.neoforge.event.*`.
  `TickEvent` split into `ServerTickEvent.Pre/Post`, `LevelTickEvent`,
  `PlayerTickEvent`, `ClientTickEvent` in NeoForge.
- `RenderLevelStageEvent`, `EntityRenderersEvent.RegisterRenderers`,
  `RegisterClientReloadListenersEvent`, `RegisterKeyMappingsEvent` →
  `net.neoforged.neoforge.client.event.*`.
- `FMLClientSetupEvent` / `FMLCommonSetupEvent` — same names, `net.neoforged.fml.*`.

## Mixins under NeoForge
NeoForge ships Mixin and **runs on official mappings**, so mixins target readable
names directly and need **no refmap**. Declare the config in `neoforge.mods.toml`
(`[[mixins]] config="<modid>.mixins.json"`), delete the `refmap` key from the json,
set `compatibilityLevel` ≥ `JAVA_21`. Then each `@Mixin(TargetClass.class)` must
still resolve: if the vanilla target changed between 1.20 and 1.21 (method renamed,
signature changed, class split), update the `@Inject`/`@Redirect` target — see the
1.20→1.21 reference. Accessor mixins (`IMixin*`) usually just work.

## Reliable web sources when a mapping is unknown
- NeoForge docs: https://docs.neoforged.net/  (Registration, Capabilities, Networking, Datacomponents pages).
- "NeoForge 1.20.1 → 1.21 primer" and the official NeoForged/MDK examples on GitHub.
- Parchment-named vanilla sources make the target signatures readable in-workspace.
