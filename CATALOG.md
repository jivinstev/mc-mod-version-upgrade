# Migration Pattern Catalog

**This is the heart of the repo.** It is a growing cookbook of every migration
issue we've hit, in a fixed format so we can (a) recognize the pattern in source
*before* compiling, (b) apply the known fix, and (c) still recognize it from the
compile error if it slips through.

### Protocol — keep this catalog alive (do this every session)
- **Read it before writing migration code.** Scan the decompiled source for these
  patterns and fix them proactively — the goal is to one-shot to a compile without
  discovering the same lesson twice.
- **Append the moment you resolve a NEW pattern** — whether you caught it in source
  or hit it at compile time. Never fix a novel API break without recording it here.
- **Format (strict):** each entry is three lines —
  **Pattern:** the old (Forge / MC ≤1.20) code shape ·
  **Error:** the `compileJava` message that reveals it ·
  **Fix:** the NeoForge 1.21.1 replacement.
- **Runtime patterns are ACTIVE (extension d):** we now have runtime tests — NeoForge
  **GameTests** (`./gradlew runGameTestServer`, headless dedicated server that loads the
  mod and runs `@GameTest` methods). Some breaks compile cleanly and only blow up at
  mod-load/registration — record those in **§R. Runtime patterns** with a **Runtime:**
  line (the crash/log signature) instead of a compile **Error:**. Run the GameTest server
  as the last gate of every port; a green `build` is necessary but not sufficient.
- If this catalog grows unwieldy, split it into `references/patterns.md` and keep a
  one-line index here — but keep it loaded during migrations either way.

Entries verified against NeoForge 21.1.228 (MC 1.21.1). "→" means "becomes".

## A. Decompile prep (do these FIRST, before any Java edits)
> **Axis:** decompile-prep — decompiler/source-mapping artifacts; run BEFORE any transform.
1. **SRG member names** (biggest; every Forge ≤1.20.x jar) ·
   **Pattern:** `Minecraft.m_91087_()`, `entity.f_19853_` — SRG method/field ids everywhere ·
   **Error:** `cannot find symbol: method m_91087_ / variable f_19853_` (thousands) ·
   **Fix:** run `tools/srg-remap` (build SRG→official dict for the MC version, text-replace). Do this before anything else — the raw decompile is otherwise uncompilable.
2. **Vineflower assert artifact** ·
   **Pattern:** `if (!<unrepresentable>.$assertionsDisabled && cond) throw new AssertionError();` ·
   **Error:** `illegal start of expression` / `not a statement` (parse fails early, masking real errors) ·
   **Fix:** replace `<unrepresentable>.$assertionsDisabled` → `true` (asserts are off at runtime, so the guard is inert).
3. **Vineflower interface-constant split** ·
   **Pattern:** interface with `Predicate<X> FOO;` (no initializer) + a `static { FOO = ...; }` block ·
   **Error:** `= expected` / `initializers not allowed in interfaces` ·
   **Fix:** inline the initializer into the field decl; delete the illegal `static{}` block.
3b. **🔴 Vineflower hoists `SPEC = BUILDER.build()` above the config fields** (compiles fine, crashes at runtime — hit in **35** config classes of one port at once) ·
   **Pattern:** a `ModConfigSpec` config class where the decompiler reordered static fields so `public static final ModConfigSpec SPEC = BUILDER.build();` sits at the TOP, *before* the `BUILDER.define(...)` ConfigValue fields. Static fields init in source order, so the spec builds while `BUILDER` is empty and every ConfigValue defined afterward gets a **null spec**. ·
   **Runtime:** `java.lang.NullPointerException: Cannot get config value before spec is built` the first time any of those config values is read (`.get()`), e.g. from an entity's `createAttributes`. Registration/loading looks fine; the config TOMLs even generate. ·
   **Fix:** move the `SPEC = BUILDER.build();` line to the **end of the class** (after every `BUILDER.define/comment/push/pop`). ⚠️ Do NOT insert it "after the last line containing `BUILDER`" — multi-line `defineList(` calls have `BUILDER` on their opening line, so you'll split a statement; place SPEC immediately before the class's closing `}` instead. Scan every config class for this at decompile time — it's systematic, not one-off.

3c. **🔴 Vineflower strands `BUILDER.push/pop` in a trailing `static {}` block → every config value in ONE flat section, and same-named values in different sections silently SHARE one entry** (hit in a weapon mod (~240 files): 150 values, 15 names reused up to 9×) ·
   **Pattern:** a `ModConfigSpec` class whose `define(...)` calls are field initialisers (fixing #3b by moving `SPEC` to the end leaves this intact) while `BUILDER.push("Section")`/`pop()` sit alone in a `static { }` at the bottom — they now run AFTER `build()` and do nothing. ·
   **Runtime:** none. `ModConfigSpec.Builder.define` overwrites an existing path without complaint, so `Cooldown`/`Range`/`Damage Multiplier` defined under different sections all read and write ONE key (and a type mismatch between them surfaces later as a `ClassCastException` at a `.get()` call site). The TOML generates, flat. · **Fix:** re-interleave from the bytecode — CFR the original jar (`--jarfilter <cfg class>`), which keeps `<clinit>`'s order: declare the fields uninitialised and assign them inside ONE `static {}` with the pushes/pops in their original positions, `SPEC = BUILDER.build()` last. **Sweep:** `grep -oE '\.define(InRange|List)?\("[^"]+"' <cfg>.java | sort | uniq -d` — any duplicate name plus a trailing push/pop block is this. Guard with a GameTest that reflects every `ConfigValue` field and asserts distinct `getPath()`s.

A4. **🔴 Vineflower MERGES distinct locals that shared one JVM slot into a single variable of
   the wrong type — and the errors land on lines that have nothing to do with each other.** ·
   **Pattern:** one method declares `String properTitle = (String)entry.getValue();` and then, in
   different branches, uses it as a `ValueSpec`, as a `String`, and as a `String[]`, with a cast at
   every use (`((Object[])properTitle)[i]`). javac's `-g:vars` info is not in a compiled jar, so the
   decompiler cannot know two same-slot locals were different variables and invents one. ·
   **Error:** a *cluster* of unrelated-looking `incompatible types` on one method —
   `String cannot be converted to ValueSpec`, `String[] cannot be converted to String`,
   `String cannot be converted to Object[]` — five errors across four lines here. ·
   **Fix:** read the method as a whole and split the slot back into one variable per branch with
   its real type (`ValueSpec spec` / `String[] words` / `String properTitle`), which also lets the
   `instanceof` become a pattern variable. **Do not chase the errors one at a time** — each cast you
   "fix" moves the contradiction to the next use, because there is only one variable to type. ·
   **The tell that distinguishes it from an ordinary generics artifact (§O #123/#124):** the same
   identifier is cast to two types that have no relationship, in branches of one `if`. (a config library's
   `ConfigLangGeneratorHelper.forValues`.) ·
   ⚠ **This has NO usable static signature, and the attempt is worth recording because it is the
   §S5b mistake in miniature.** A scan for "one variable cast to two types" measured **0 false
   positives on the config library** — the mod it was written against — and was about to ship. Run over
   the other 13 ports it produced **~35 hits and zero real findings**: every one was a loop or
   branch variable legitimately narrowed to different SUBTYPES (`entity` as `Mob` here and
   `LivingEntity` there; `hitResult` as `BlockHitResult`/`EntityHitResult`), which is ordinary
   correct Java. The real artifact's types are *unrelated*; the noise shares a supertype, and a
   grep cannot know the hierarchy. So it is deliberately NOT in `catalog-scans.md`: a warning
   that is permanently non-zero is decoration (X10), and this one would have been non-zero on 12
   of 14 ports. · **It does not need a scan anyway, which is the point:** unlike every §S/§R
   pattern, this one is a hard COMPILE error — the sweep exists for what the compiler cannot see.
   What it needs is *recognition*, so you fix the one merged variable instead of chasing five
   casts that each move the contradiction along.

## B. Loader core (Forge → NeoForge)
> **Axis:** loader-transform (SRC_LOADER→DST_LOADER, e.g. Forge→NeoForge). Skip if SRC_LOADER==DST_LOADER.
4. **Forge packages** · **Pattern:** `import net.minecraftforge.*` · **Error:** `package net.minecraftforge.X does not exist` · **Fix:** codemod: `api.distmarker`→`net.neoforged.api.distmarker`, `eventbus.api`→`net.neoforged.bus.api`, `fml`→`net.neoforged.fml`, `event`→`net.neoforged.neoforge.event`, `client.event`→`net.neoforged.neoforge.client.event`, `registries`→`net.neoforged.neoforge.registries`, `common.MinecraftForge`→`net.neoforged.neoforge.common.NeoForge`, `items/energy/fluids/entity`→`net.neoforged.neoforge.*`. (See `tools/srg-remap/forge_import_codemod.pl`.)
5. **@Mod constructor / mod bus** · **Pattern:** `public MyMod(){ IEventBus bus = FMLJavaModLoadingContext.get().getModEventBus(); }` · **Error:** `cannot find symbol: class FMLJavaModLoadingContext` · **Fix:** inject: `public MyMod(IEventBus bus, ModContainer container){...}` (imports `net.neoforged.bus.api.IEventBus`, `net.neoforged.fml.ModContainer`). `MinecraftForge.EVENT_BUS`→`NeoForge.EVENT_BUS`.
6. **DistExecutor** · **Pattern:** `DistExecutor.unsafeRunWhenOn(Dist.CLIENT, ()->Client::init)` · **Error:** `cannot find symbol: class DistExecutor` · **Fix:** `if (FMLEnvironment.dist == Dist.CLIENT) Client.init();`.
7. **@Mod.EventBusSubscriber import** · **Pattern:** `import net.neoforged.fml.common.Mod.EventBusSubscriber;` (nested under Mod) · **Error:** `cannot find symbol: class EventBusSubscriber` · **Fix:** `import net.neoforged.fml.common.EventBusSubscriber;` (top-level, not nested); `.Bus` likewise. Note: NeoForge default bus is GAME (was FORGE).
8. **Config spec/registration** · **Pattern:** `ForgeConfigSpec`; `ModLoadingContext.get().registerConfig(Type.X, SPEC, file)` · **Error:** `cannot find symbol: class ForgeConfigSpec` / `method registerConfig` · **Fix:** `ModConfigSpec` (`net.neoforged.neoforge.common.ModConfigSpec`); register via the injected `container.registerConfig(Type.X, SPEC, file)` (`Type` = `net.neoforged.fml.config.ModConfig.Type`).

## C. Registration
> **Axis:** loader-transform (SRC_LOADER→DST_LOADER). Skip if SRC_LOADER==DST_LOADER.
9. **RegistryObject (get-only)** · **Pattern:** `RegistryObject<Item> FOO = ITEMS.register(...)` used only via `.get()` · **Error:** `cannot find symbol: class RegistryObject` · **Fix:** `Supplier<Item> FOO = ...` (import `java.util.function.Supplier`) — NeoForge `register()` returns a `DeferredHolder` which is a `Supplier`. If `.getKey()/.getId()` are used, use `DeferredHolder<R,T>` instead.
10. **ForgeRegistries in create()** · **Pattern:** `DeferredRegister.create(ForgeRegistries.ITEMS, MODID)` · **Error:** `cannot find symbol: class ForgeRegistries` · **Fix:** vanilla keys (singular): `DeferredRegister.create(Registries.ITEM, MODID)` (`net.minecraft.core.registries.Registries`). Map: ITEMS→ITEM, BLOCKS→BLOCK, ENTITY_TYPES→ENTITY_TYPE, MOB_EFFECTS→MOB_EFFECT, PARTICLE_TYPES→PARTICLE_TYPE, SOUND_EVENTS→SOUND_EVENT, BLOCK_ENTITY_TYPES→BLOCK_ENTITY_TYPE.
11. **ForgeRegistries direct access** · **Pattern:** `ForgeRegistries.ITEMS.getValue(rl)` / `.tags()` · **Error:** `cannot find symbol: class ForgeRegistries` · **Fix:** `BuiltInRegistries.ITEM.get(rl)` (`net.minecraft.core.registries.BuiltInRegistries`); tags via `BuiltInRegistries.X.getTag(tagKey)`.
12. **ForgeSpawnEggItem** · **Pattern:** `new ForgeSpawnEggItem(...)` · **Error:** `cannot find symbol: class ForgeSpawnEggItem` · **Fix:** `DeferredSpawnEggItem` (`net.neoforged.neoforge.common.DeferredSpawnEggItem`).
12b. **Registrate (tterrag) RegistryEntry arity `<T>`→`<R,S>`** · **Pattern:** a Registrate-based mod/facade with `RegistryEntry<Item> FOO` / `class MyEntry<T> extends com.tterrag.registrate.util.entry.RegistryEntry<T>` / `AbstractRegistrate.get()/simple()/entry()` used with 1 generic arg, ctor taking a Forge `RegistryObject`. · **Error:** `wrong number of type arguments; required 2` on `com.tterrag.registrate.util.entry.RegistryEntry`; `cannot find symbol: class RegistryObject`. · **Fix:** Registrate MC1.21 changed `RegistryEntry<T>` → **`RegistryEntry<R, S extends R> extends net.neoforged.neoforge.registries.DeferredHolder<R,S>`** and every `AbstractRegistrate` method to `<R, T extends R>` (`get`/`simple`/`entry`/`getAll`/`makeRegistry`); the ctor now takes a `DeferredHolder<R,S>`, not `RegistryObject`. Re-parameterize your own entry/holder/builder classes to the 2-arg form (facade's `<TYPE, VALUE extends TYPE>` maps to `<R, T extends R>`, so `RegistryEntry<VALUE>`→`RegistryEntry<TYPE, VALUE>`) and replace `RegistryObject<T>`→`DeferredHolder<R,T>`. Also: Registrate's custom-registry path (`createRegistry`/`makeRegistry`) dropped Forge `IForgeRegistry`/`NewRegistryEvent`/`RegistryBuilder.setName` — `makeRegistry` is now `makeRegistry(String, Function<ResourceKey<Registry<R>>, RegistryBuilder<R>>)`. Registrate NeoForge 1.21.1 = `com.tterrag.registrate:Registrate:MC1.21-1.3.0+67` from `https://maven.ithundxr.dev/snapshots` (NOT maven.tterrag.com, which stops at MC1.20). **This is a framework-redflag lib — the arity change alone ripples through every entry/holder/builder class; budget a multi-session port.** (Seen: a Registrate-based framework library.)
12c. **A bundled "library" jar is NOT a mod — never declare it as a dependency** · **Pattern:** porting a mod built on a code library (Registrate, a shaded helper lib) and carrying the Forge-era `[[dependencies.x]] modId="registrate"` across, or adding one because the library is jarJar'd. · **Runtime:** the dedicated server refuses to boot — `Mod <yours> requires registrate 1.3.0 or above / Currently, registrate is not installed` — even though the classes are on the classpath and the code compiled cleanly. · **Fix:** check the library jar for `META-INF/neoforge.mods.toml` (`unzip -l lib.jar | grep mods.toml`). **Registrate MC1.21-1.3.0+67 has none** — it is a plain library, not a loadable mod, so FML can never satisfy a mod-dependency on it. Remove the `[[dependencies]]` block and bundle it via `jarJar` instead. **Only Gate B catches this**; a clean compile and a green Gate A both pass. (Seen: a Registrate-based framework library's 1.20.1 port.)
12d. **Interface-default `super` calls decompile as plain `super.x()`** · **Pattern:** a class `implements SomeInterface` and the decompiled source calls `super.get()` / `super.build()` for methods that are **default methods on that interface**, not on the superclass. · **Error:** `cannot find symbol: method get()` / `method build()` — pointing at `super`. · **Fix:** qualify with the interface: `SomeInterface.super.get()`. Java only allows `super.x()` for a *class* supertype. (Seen: a Registrate-based framework library's `AbstractBuilder implements Builder` — 8 sites at once.)
12e. **Capability provider removed ⇒ you MUST re-register it** · **Pattern:** a 1.20.1 BlockEntity exposed an inventory by overriding `getCapability(ForgeCapabilities.ITEM_HANDLER, side)`; the port deletes that override (the API is gone). · **Runtime:** silent — hoppers, droppers and comparators simply see no inventory. Compiles clean, loads clean, no crash. · **Fix:** register per-`BlockEntityType` on `RegisterCapabilitiesEvent`: `event.registerBlockEntity(Capabilities.ItemHandler.BLOCK, type, (be, side) -> be instanceof MyInv inv ? inv.getItemHandler() : null)`. In a *framework* mod do it centrally in the block-entity builder so every dependent BE gets it. (Seen: a Registrate-based framework library's `InventoryBlockEntity`.)

## D. Capabilities → Data Attachments (a full rewrite)
> **Axis:** loader-transform (SRC_LOADER→DST_LOADER). Skip if SRC_LOADER==DST_LOADER.
13. **Capability provider / LazyOptional** · **Pattern:** `class XProvider implements ICapabilitySerializable<CompoundTag>{ Capability<X> CAP = CapabilityManager.get(new CapabilityToken<>(){}); LazyOptional<X> opt = LazyOptional.of(...); }` + `AttachCapabilitiesEvent` handler · **Error:** `package net.neoforged.neoforge.common.capabilities does not exist`; `cannot find symbol: LazyOptional / Capability / ICapabilitySerializable / CapabilityToken / AttachCapabilitiesEvent / RegisterCapabilitiesEvent` · **Fix:** convert to **data attachments**: `AttachmentType<X>` in a `DeferredRegister.create(NeoForgeRegistries.Keys.ATTACHMENT_TYPES, MODID)`; build with `AttachmentType.builder(X::new).serialize(IAttachmentSerializer).build()` (bridge the data class's `saveNBTData`/`loadNBTData`); player state adds `.copyOnDeath()`. Access `holder.getData(TYPE)` / `setData(TYPE,v)`. **Delete** the provider + `AttachCapabilities` handlers (attachments auto-attach on first `getData`). For call sites, an `Optional`-returning compat helper (`e instanceof LivingEntity ? Optional.of(e.getData(TYPE)) : Optional.empty()`) keeps `.ifPresent`/`.isPresent` working; `getCapability(CAP).resolve()` → the value directly.
· **AUGMENT — where the old attach-gating goes:** `AttachCapabilitiesEvent` handlers usually attached only to some holders (`instanceof LivingEntity` / `Player` / `Mob` / `Projectile` / `ServerPlayer`). Attachments attach lazily to anything `getData` is called on, so move the gate into the helper: `entity instanceof Mob ? entity.getData(TYPE) : new X()` — this keeps the 1.20 "throwaway default for the wrong holder" behaviour and stops, e.g., a ServerPlayer-only capability from being created (and serialised) on the client player. `INBTSerializable` gained a `HolderLookup.Provider` on both methods: `serializeNBT(Provider)` / `deserializeNBT(Provider, CompoundTag)`; `AttachmentType.serializable(X::new)` then works unchanged.
14. **NonNullLazy** · **Pattern:** `NonNullLazy<X> = NonNullLazy.of(sup)` · **Error:** `cannot find symbol: class NonNullLazy` · **Fix:** `Lazy<X>` / `Lazy.of(...)` (`net.neoforged.neoforge.common.util.Lazy`).

166. **A Forge capability on an `ItemStack` has no data-attachment equivalent** · **Pattern:** `forgeBus.addGenericListener(ItemStack.class, Attacher::attach)` + `AttachCapabilitiesEvent<ItemStack>` + `stack.getCapability(CAP).orElse(new X(stack))` · **Error:** `package net.neoforged.neoforge.common.capabilities does not exist` / `cannot find symbol: class AttachCapabilitiesEvent` · **Fix:** NeoForge attachments exist for entities, block entities, chunks and levels only — never stacks. If the "capability" was DERIVED from the item (a gear library's per-item config), make the helper return a freshly computed view (`new X(stack)`) and drop the provider; if it held per-stack mutable state, define a `DataComponentType<T>` with a Codec and StreamCodec. Record that mutations on the view no longer persist. (a gear/combat library: its built-in-enchantments capability)

## E. Networking → Payloads (SimpleChannel is gone)
> **Axis:** loader-transform (SRC_LOADER→DST_LOADER). Skip if SRC_LOADER==DST_LOADER.
15. **SimpleChannel** · **Pattern:** `SimpleChannel INSTANCE = ChannelBuilder.named(rl)...simpleChannel(); INSTANCE.messageBuilder(P.class,id,NetworkDirection.PLAY_TO_X).decoder(P::new).encoder(P::toBytes).consumerNetworkThread(P::handle).add();` · **Error:** `package net.minecraftforge.network.simple does not exist`; `cannot find symbol: SimpleChannel / NetworkDirection / NetworkRegistry` · **Fix:** register on `RegisterPayloadHandlersEvent` (mod bus): `PayloadRegistrar r = event.registrar("1"); r.playToServer/playToClient(P.TYPE, P.STREAM_CODEC, P::handle);`. Wire `bus.addListener(Messages::register)`.
16. **Packet class → CustomPacketPayload** · **Pattern:** class with `P(FriendlyByteBuf)`, `toBytes(FriendlyByteBuf)`, `boolean handle(Supplier<NetworkEvent.Context>)` · **Error:** `package net.minecraftforge.network does not exist` (NetworkEvent.Context) · **Fix:** `implements CustomPacketPayload`; add `static final CustomPacketPayload.Type<P> TYPE = new CustomPacketPayload.Type<>(ResourceLocation.fromNamespaceAndPath(MODID,name))`; `static final StreamCodec<FriendlyByteBuf,P> STREAM_CODEC = StreamCodec.ofMember(P::toBytes, P::new)`; `public CustomPacketPayload.Type<? extends CustomPacketPayload> type(){return TYPE;}`; handler `public void handle(IPayloadContext ctx)`.
17. **NetworkEvent.Context methods** · **Pattern:** `ctx.getSender()`, `ctx.getDirection()`, `ctx.setPacketHandled(true)`, `supplier.get()` · **Error:** `cannot find symbol: method getSender/getDirection/setPacketHandled` on `IPayloadContext` · **Fix:** `ctx.getSender()`→`(ServerPlayer) ctx.player()`; `getDirection()`→`ctx.flow()`; drop `setPacketHandled`; `enqueueWork` stays.
18. **NetworkHooks entity spawn packet** · **Pattern:** `public Packet<ClientGamePacketListener> getAddEntityPacket(){ return NetworkHooks.getEntitySpawningPacket(this); }` · **Error:** `package net.minecraftforge.network does not exist` (NetworkHooks) · **Fix:** `getAddEntityPacket(ServerEntity se){ return new ClientboundAddEntityPacket(this, se); }` (new 1.21 signature). Extra spawn data → `implements IEntityWithComplexSpawn`.
19. **PacketDistributor** · **Pattern:** `INSTANCE.send(PacketDistributor.PLAYER.with(()->player), msg)`, `PacketDistributor.ALL.noArg()`, `PacketTarget` · **Error:** `cannot find symbol: class PacketTarget` · **Fix:** static helpers `PacketDistributor.sendToPlayer(player, payload)` / `sendToAllPlayers(payload)` / `sendToServer(payload)` (`net.neoforged.neoforge.network.PacketDistributor`).

## F. Events
> **Axis:** loader-transform (SRC_LOADER→DST_LOADER) + some 1.20→1.21 event renames.
20. **TickEvent split** · **Pattern:** `onTick(TickEvent.PlayerTickEvent e){ if(e.phase==Phase.END && e.side==LogicalSide.CLIENT){...} e.player...}` · **Error:** `package net.neoforged.neoforge.event.TickEvent does not exist`; `cannot find symbol: Phase` · **Fix:** `PlayerTickEvent.Post` (`net.neoforged.neoforge.event.tick.PlayerTickEvent`); drop the phase check (Pre/Post is the phase); `e.player`→`e.getEntity()`; client check → `e.getEntity().level().isClientSide`. Also `ServerTickEvent`/`LevelTickEvent` in `event.tick`, `ClientTickEvent.Post` in `client.event`.
21. **LivingEvent.LivingTickEvent** · **Pattern:** `onTick(LivingEvent.LivingTickEvent e)` using `e.getEntity()` as LivingEntity · **Error:** `cannot find symbol: class LivingTickEvent` · **Fix:** `EntityTickEvent.Post e` (`event.tick`); `e.getEntity()` now returns `Entity`, so guard `if(!(e.getEntity() instanceof LivingEntity living)) return;` and use `living`. (Only guard if it truly needs LivingEntity — `EntityTickEvent` fires for all entities.)
22. **LivingHurtEvent** · **Pattern:** `LivingHurtEvent` · **Error:** `cannot find symbol: class LivingHurtEvent` · **Fix:** `LivingIncomingDamageEvent` (same package; `getAmount/setAmount/getSource` unchanged).
23. **MobSpawnEvent.FinalizeSpawn** · **Pattern:** `MobSpawnEvent.FinalizeSpawn` · **Error:** `cannot find symbol: FinalizeSpawn` · **Fix:** `FinalizeSpawnEvent` (`event.entity.living`).
24. **ForgeEventFactory / ForgeHooks** · **Pattern:** `ForgeEventFactory.getMobGriefingEvent(l,e)`, `onLivingConvert`, `onProjectileImpact`…; `ForgeHooks.getLootingLevel`, `onLivingDrops` · **Error:** `cannot find symbol: class ForgeEventFactory / ForgeHooks` · **Fix:** `EventHooks.*` (`net.neoforged.neoforge.event.EventHooks`) / `CommonHooks.*`. Known: `getMobGriefingEvent`→`EventHooks.canEntityGrief(level,e)`; `onLivingConvert`→`EventHooks.onLivingConvert`; `getLootingLevel` **removed** (looting is data-driven → neutralize to `0`); `onLivingDrops`→`EventHooks.onLivingDrop`. Verify each signature against the NeoForge sources jar.

## G. Vanilla MC 1.20.1 → 1.21.1
> **Axis:** version-family (1.20.x→1.21.x). For 1.21.x↔1.21.y minor hops see §M.
25. **ResourceLocation constructor** · **Pattern:** `new ResourceLocation(ns,path)` / `new ResourceLocation("mod:x")` · **Error:** `ResourceLocation() has private access` · **Fix:** `ResourceLocation.fromNamespaceAndPath(ns,path)` / `ResourceLocation.parse("mod:x")` / `.withDefaultNamespace(x)`.
26. **defineSynchedData** · **Pattern:** `defineSynchedData(){ this.entityData.define(ACC,def); }` · **Error:** `method does not override` / abstract `defineSynchedData(SynchedEntityData.Builder)` not implemented · **Fix:** `defineSynchedData(SynchedEntityData.Builder b){ b.define(ACC,def); }`.
27. **BlockEntity save-load gained Provider (⚠️ Entities did NOT)** · **Pattern:** `saveAdditional(CompoundTag)`, `load(CompoundTag)` on a **BlockEntity** · **Error:** `does not override` · **Fix:** BlockEntity gets a `HolderLookup.Provider` param — `saveAdditional(CompoundTag, HolderLookup.Provider)`, `loadAdditional(CompoundTag, HolderLookup.Provider)`. **IMPORTANT:** `Entity.addAdditionalSaveData(CompoundTag)` / `readAdditionalSaveData(CompoundTag)` are **still 1-arg** in 1.21.1 — do NOT add a Provider to entity overrides (verified with `javap`). Entity save/load errors are almost always #28 (ItemStack) inside the method body, not the signature.
28. **Item NBT → DataComponents** · **Pattern:** `stack.getOrCreateTag()/getTag()/setTag()` · **Error:** `cannot find symbol: method getOrCreateTag` · **Fix:** components. Fast bridge: `DataComponents.CUSTOM_DATA` (`CustomData` — `.copyTag()` / `CustomData.update`). Structured data: define a `DataComponentType<T>`.
29. **Enchantment API (data-driven)** · **Pattern:** `EnchantmentHelper.getItemEnchantmentLevel(Enchantments.KNOCKBACK, stack)`, `getDamageBonus(stack, mobType)`, `Enchantments.X` used as an `Enchantment` · **Error:** `incompatible types: ResourceKey<Enchantment> cannot be converted to Enchantment`; `method getDamageBonus not found` · **Fix:** resolve a `Holder<Enchantment>` from `level.registryAccess().registryOrThrow(Registries.ENCHANTMENT).getHolderOrThrow(Enchantments.X)`; `getItemEnchantmentLevel(Holder, stack)`. `getDamageBonus(MobType)` **removed** — vanilla applies smite/etc. via the ServerLevel damage path; neutralize custom bonus calcs.
30. **MobType** · **Pattern:** `public MobType getMobType(){ return MobType.UNDEAD; }`, `entity.getMobType()` · **Error:** `cannot find symbol: class MobType` · **Fix:** delete the override; tag the entity `minecraft:undead` (entity-type tag); checks → `entity.getType().is(EntityTypeTags.UNDEAD)`. · **AUGMENT (many call sites read `getMobType()` for damage/potion logic, not just a tag check):** when a blanket tag rewrite would touch dozens of sites, a compat shim keeps the diff local — declare a `HasMobType` interface + a small local `MobType` enum + a static `MobType.of(Entity)` that maps vanilla types/tags (`UNDEAD`/`ARTHROPOD`/`ILLAGER`/`WATER`) to it; implement `HasMobType` on your own entities, and route every removed `x.getMobType()` → `MobType.of(x)`. (a ~750-file boss mod: 21 entities + one helper; keeps the semantic logic intact rather than deleting it.)
31. **FriendlyByteBuf.readItem/writeItem** · **Pattern:** `buf.readItem()` / `buf.writeItem(stack)` · **Error:** `cannot find symbol: method readItem/writeItem` · **Fix:** item stacks serialize via `ItemStack.STREAM_CODEC` / `ItemStack.OPTIONAL_STREAM_CODEC` on a **`RegistryFriendlyByteBuf`** — type the packet's `StreamCodec` on `RegistryFriendlyByteBuf`, not `FriendlyByteBuf`, when it carries items.
32. **ItemStack.hurtAndBreak** · **Pattern:** `stack.hurtAndBreak(n, entity, e -> e.broadcastBreakEvent(slot))` · **Error:** `EquipmentSlot is not a functional interface` / method not applicable · **Fix:** two 1.21.1 overloads. The **common mob/player form** is `stack.hurtAndBreak(n, livingEntity, EquipmentSlot.MAINHAND/OFFHAND)` — pass the wielder + the slot directly (`broadcastBreakEvent` is now automatic). The other is `stack.hurtAndBreak(n, serverLevel, serverPlayer, item -> {})` (`ServerLevel` + `Consumer<Item>`). Use the `(int, LivingEntity, EquipmentSlot)` overload wherever the old callback just broadcast a break for a held item.
33. **Vanishable** · **Pattern:** `class Foo extends Item implements Vanishable` · **Error:** `cannot find symbol: class Vanishable` · **Fix:** remove `implements Vanishable` + import (removed in 1.20.5).
34. **DyeableLeatherItem** · **Pattern:** `item instanceof DyeableLeatherItem`, `((DyeableLeatherItem)item).getColor(stack)` · **Error:** `cannot find symbol: class DyeableLeatherItem` · **Fix:** `stack.has(DataComponents.DYED_COLOR)` / `DyedItemColor.getOrDefault(stack, default)`.
35. **RecordItem** · **Pattern:** `new RecordItem(comparatorOut, soundSup, props, lengthTicks)` · **Error:** `cannot find symbol: class RecordItem` · **Fix:** music discs are data-driven — `new Item(props)` + the `JUKEBOX_PLAYABLE` component + a `JukeboxSong` datapack entry.
36. **ForgeMod attributes** · **Pattern:** `(Attribute)ForgeMod.ENTITY_REACH.get()`, `ForgeMod.SWIM_SPEED.get()` · **Error:** `cannot find symbol: class ForgeMod` · **Fix:** vanilla holders: `Attributes.ENTITY_INTERACTION_RANGE`, `Attributes.WATER_MOVEMENT_EFFICIENCY` — drop the `(Attribute)` cast (1.21 attribute APIs take `Holder<Attribute>`; `Attributes.X` are already Holders).
37. **BlockPathTypes** · **Pattern:** `BlockPathTypes.WALKABLE` · **Error:** `cannot find symbol: class BlockPathTypes` · **Fix:** `PathType` (`net.minecraft.world.level.pathfinder.PathType`).
38. **GUI overlays** · **Pattern:** `IGuiOverlay`, `RegisterGuiOverlaysEvent`, `ForgeGui`, `event.registerAbove(...)` · **Error:** `package net.neoforged.neoforge.client.gui.overlay does not exist`; `cannot find symbol: IGuiOverlay` · **Fix:** `LayeredDraw.Layer` + `RegisterGuiLayersEvent` (`event.registerAbove(VanillaGuiLayers.X, rl, layer)`); `ForgeGui`→`net.minecraft.client.gui.Gui`.
39. **Particle Deserializer** · **Pattern:** `ParticleOptions.Deserializer` + a `Codec` field · **Error:** `cannot find symbol: class Deserializer` · **Fix:** 1.20.5 particle options expose `MapCodec` + `StreamCodec` static fields (no `Deserializer`).
41. **AttributeModifier.Operation renames** · **Pattern:** `AttributeModifier.Operation.ADDITION` (also `MULTIPLY_BASE` / `MULTIPLY_TOTAL`) · **Error:** `cannot find symbol: variable ADDITION` · **Fix:** `ADD_VALUE`, `ADD_MULTIPLIED_BASE`, `ADD_MULTIPLIED_TOTAL` respectively. (Also: `AttributeModifier(UUID,String,double,Operation)` → `AttributeModifier(ResourceLocation id, double, Operation)` in 1.21 — the UUID+name pair became a single `ResourceLocation`.)
42. **Registry objects passed where `Holder<>` is now required (MobEffect etc.)** · **Pattern:** `new MobEffectInstance((MobEffect)EffectInit.X.get(), …)`, `entity.hasEffect((MobEffect)X.get())`, `instance.getEffect() == X.get()` · **Error:** `incompatible types: MobEffect cannot be converted to Holder<MobEffect>` · **Fix:** 1.21 APIs take `Holder<MobEffect>`. Declare the registry fields as `Holder<MobEffect>` (or `DeferredHolder<MobEffect,MobEffect>`, which is both a `Holder` and a `Supplier` — keep this if `.get()` is still needed elsewhere) and pass the field **directly** (drop the `(MobEffect)…get()`). `getEffect()` now returns a `Holder`, so `== X` compares holders. Same pattern applies to `Potion`, `Attribute`, `Enchantment`, `DamageType` — they're all `Holder`-wrapped now.
43. **defineSynchedData Builder (companion to #26)** · **Pattern:** `super.defineSynchedData();` in an override · **Error:** `method defineSynchedData in class X cannot be applied to given types` (0 args vs `SynchedEntityData.Builder`) · **Fix:** thread the builder: `super.defineSynchedData(builder);` (the override's param).
44. **Forge hook facades renamed** · **Pattern:** `ForgeEventFactory.X(...)`, `ForgeHooks.X(...)`, `ForgeHooksClient.X(...)` · **Error:** `cannot find symbol: class ForgeEventFactory / ForgeHooks / ForgeHooksClient` · **Fix:** `EventHooks.*` (`net.neoforged.neoforge.event.EventHooks`), `CommonHooks.*` (`net.neoforged.neoforge.common.CommonHooks`), `ClientHooks.*` (`net.neoforged.neoforge.client.ClientHooks`). Most method names carry over; known exceptions: `ForgeEventFactory.getMobGriefingEvent`→`EventHooks.canEntityGrief`; `ForgeHooks.getLootingLevel` **removed** (looting is data-driven → replace with `0`); `CommonHooks.onLivingDrops` dropped its `lootingLevel` arg (now `(entity, source, drops, recentlyHit)`); `ClientHooks.getArmorTexture` changed signature. Verify each against the NeoForge sources jar in the gradle cache. · **AUGMENT — some hooks did NOT move to `CommonHooks`/`EventHooks`, they became ItemStack extension methods:** `ForgeHooks.getBurnTime(stack, recipeType)` → **`stack.getBurnTime(recipeType)`** (`IItemStackExtension`). Grep each `ForgeHooks.` call individually rather than blanket-renaming the class — `firePlayerSmeltedEvent` *is* on `EventHooks`, `getBurnTime` is not. (a furniture mod built on that framework library: `OvenBlockEntity` / `OvenMenu`)
45. **EventBusSubscriber bus enum** · **Pattern:** `@EventBusSubscriber(bus = Bus.FORGE)` · **Error:** `cannot find symbol: variable FORGE` · **Fix:** `Bus.GAME` (Forge's `FORGE` bus is NeoForge's `GAME` bus; `MOD` is unchanged).
46. **Item.Properties.defaultDurability / BlockBehaviour.Properties.copy** · **Pattern:** `.defaultDurability(n)`, `BlockBehaviour.Properties.copy(block)` · **Error:** `cannot find symbol: method defaultDurability / copy` · **Fix:** `.durability(n)`; `Properties.ofFullCopy(block)`.
47. **Item `maxDamage` field write** · **Pattern:** override `getMaxDamage(ItemStack)` that also does `this.maxDamage = value;` · **Error:** `cannot find symbol: variable maxDamage` · **Fix:** delete the assignment — durability is a data component now; the `getMaxDamage(ItemStack)` override returning the value is the whole fix.
48. **Registry `.getCodec()`** · **Pattern:** `ForgeRegistries.ENTITY_TYPES.getCodec()` · **Error:** `cannot find symbol: method getCodec` · **Fix:** `BuiltInRegistries.ENTITY_TYPE.byNameCodec()`.
49. **Registry `.tags().getTag(TagKey)`** · **Pattern:** `ForgeRegistries.MOB_EFFECTS.tags().getTag(tag).stream().toList()` (expects `List<MobEffect>`) · **Error:** `package ForgeRegistries does not exist` / method chain not found · **Fix:** `BuiltInRegistries.MOB_EFFECT.getOrCreateTag(tagKey)` (or `getTag(...)` → `Optional<HolderSet.Named<>>`) yields `Holder<>`s → add `.stream().map(Holder::value).toList()`. Dynamic registries (BIOMES) have no `BuiltInRegistries` entry — resolve via `registryAccess()`.
50. **finalizeSpawn dropped CompoundTag** · **Pattern:** `mob.finalizeSpawn(level, difficulty, MobSpawnType.X, spawnData, (CompoundTag)null)` · **Error:** `method finalizeSpawn in class Mob cannot be applied to given types` · **Fix:** drop the trailing `CompoundTag` arg (now 4-arg: `(ServerLevelAccessor, DifficultyInstance, MobSpawnType, SpawnGroupData)`).
51. **AttributeModifier ctor (UUID+name → ResourceLocation)** · **Pattern:** `new AttributeModifier(SOME_UUID, "name", value, Operation.X)` · **Error:** `constructor AttributeModifier in record AttributeModifier cannot be applied` · **Fix:** `new AttributeModifier(ResourceLocation id, value, Operation.X)` (3-arg). Vanilla base ids: `Item.BASE_ATTACK_DAMAGE_ID` / `Item.BASE_ATTACK_SPEED_ID`; for custom modifiers make a `ResourceLocation.fromNamespaceAndPath(MODID, "...")`.
52. **Entity/Explosion/Minecraft vanilla renames** · **Pattern → Fix** (each: `cannot find symbol: method …`): `entity.setSecondsOnFire(n)`→`igniteForSeconds((float)n)`; `entity.onAddedToWorld()`→`onAddedToLevel()`; `explosion.getExploder()`→`getDirectSourceEntity()` (Entity) / `getIndirectSourceEntity()` (LivingEntity); `Minecraft.getFrameTime()`→`getTimer().getGameTimeDeltaPartialTick(true)`; `livingEntity.dropExperience()`→`dropExperience(Entity attacker)` (0-arg → pass the killer); `this.doEnchantDamageEffects(a,t)` **removed** — enchant effects auto-apply on the ServerLevel damage path (neutralize the call).
53. **ItemStack NBT compat helper (companion to #28)** · **Pattern:** `stack.getTag()/getOrCreateTag()/setTag(t)/hasTag()` scattered across many item classes · **Error:** `cannot find symbol: method getTag/getOrCreateTag/setTag/hasTag` · **Fix:** a small `ItemNbtCompat` helper over `DataComponents.CUSTOM_DATA` + codemod call sites (`x.getTag()`→`ItemNbtCompat.getTag(x)` etc.). ⚠️ `getOrCreateTag()` returns a COPY (components are immutable) — write sites that mutated the tag in place must add `ItemNbtCompat.setTag(stack, tag)` after; this is a **runtime**-correctness item to verify with tests. Codemod pitfall: a receiver-capturing regex can swallow a leading `(` from `if (x.hasTag())` → `if ItemNbtCompat.hasTag((x))`; re-grep for `ItemNbtCompat.\w+((` and fix. · **AUGMENT — it recurs, and the re-grep is the WEAK form of the fix; the strong one is structural.** Hit again verbatim on a ~750-file boss mod's UUID pair: a receiver class of `[\w.()\[\]]+` captured `(compound` out of `if (compound.hasUUID(..))`. **Do not put `(` in the receiver character class at all** — `([\w.]+(?:\(\))?)` covers a field chain and a no-arg call and cannot reach backwards past an opening paren. Two things made it expensive rather than instant: it broke the CANONICAL target while the new one went green (§X8 predicted exactly this, which is §W2's whole point), and it is a PARSE error, so the burn-down read 3 where the truth was 0 (§X5b).

167. **Datapack-loaded JSON that names an enchantment cannot resolve it at parse time** · **Pattern:** a `SimpleJsonResourceReloadListener`/`SimplePreparableReloadListener` whose Codec builds `new EnchantmentInstance(ForgeRegistries.ENCHANTMENTS.getValue(rl), level)` · **Error:** `package ForgeRegistries does not exist`; after swapping to vanilla registries, `Enchantment cannot be converted to Holder<Enchantment>` (enchantments are a DATAPACK registry, absent from `BuiltInRegistries`) · **Fix:** store the key, not the holder: a small `record EnchantmentData(ResourceKey<Enchantment> enchantment, int level)` with `ResourceKey.codec(Registries.ENCHANTMENT)`; resolve at use time with `registries.lookup(Registries.ENCHANTMENT).flatMap(l -> l.get(key))` (tooltips: `ItemTooltipEvent#getContext().registries()`), compare with `holder.is(key)`. A plain `JsonOps` reload listener has no `RegistryOps`, so a `Holder` codec there cannot work. (a gear/combat library's gear configs)

169. **Item stats that a 1.20 mod RELOADED by mutating `Item` fields (accessor mixins) — durability and attribute modifiers are stack-level in 1.21** · **Pattern:** `@Accessor @Mutable void setMaxDamage(int)` on `Item`, `setDefense/setToughness/setKnockbackResistance` on `ArmorItem`, a `Multimap<Attribute,AttributeModifier> defaultModifiers` field rebuilt in a `reload()` called from a datapack listener, returned from `getDefaultAttributeModifiers(EquipmentSlot)` · **Error:** `cannot find symbol: method setMaxDamage` / `EquipmentSlot cannot be converted to ItemStack` / `Multimap<Attribute,AttributeModifier> cannot be converted to …`; at runtime `InvalidAccessorException: No candidates were found matching maxDamage` (the fields are gone) · **Fix:** keep the reload, change where it is read: hold an `ItemAttributeModifiers` + an int and override NeoForge's stack-sensitive hooks — `getDefaultAttributeModifiers(ItemStack)` (and the no-arg one) and `getMaxDamage(ItemStack)`. `ItemStack#isDamageableItem` requires a `MAX_DAMAGE` component, so give the Properties a placeholder `.durability(n)` in the constructor. Modifier ids: `Item.BASE_ATTACK_DAMAGE_ID`/`BASE_ATTACK_SPEED_ID` for the two base stats, a stable `modid:kind.index` id otherwise (1.21 modifiers with equal ids REPLACE each other). Catalog #47's "delete the assignment" silently drops the configured durability for this shape. (a gear/combat library's melee/armour/bow/crossbow/artifact base classes)

170. **Gear base classes lose enchantability in 1.21 unless they answer for it — the item is not in any `#minecraft:enchantable/*` tag** · **Pattern:** `class ModBow extends BowItem`, `class ModArmor extends ArmorItem`, `class ModMelee extends TieredItem` in a LIBRARY whose concrete items are registered by dependent mods; 1.20 enchantability came from the class (EnchantmentCategory) · **Symptom:** none at compile or load — enchanting tables and anvils refuse every enchantment on the dependents' gear. The §P/#153b sweep does not fire for a library (it keys on `assets/*/models/item`, which a base-class library does not ship) · **Fix:** override `supportsEnchantment(ItemStack, Holder<Enchantment>)` and `isPrimaryItemFor(...)` in each base class to also answer "as the equivalent vanilla item would": `enchantment.value().isSupportedItem(new ItemStack(Items.BOW))` (`DIAMOND_SWORD`, `DIAMOND_PICKAXE`, `CROSSBOW`, the matching `DIAMOND_<slot>` for armour). This also replaces the removed `canApplyAtEnchantingTable` (#63).

171. **1.21 moved bow/crossbow multishot, piercing, power, punch and flame into ENCHANTMENT EFFECTS — a Forge mod's re-implementations are redundant and their mixin targets are gone** · **Pattern:** `@Inject(method="performShooting", at=HEAD, cancellable=true)` that cancels vanilla firing and re-shoots N arrows; `@ModifyConstant(method="tryLoadProjectiles", constant=@Constant(intValue=3))` for extra multishot; `@Inject(method="releaseUsing", at=INVOKE_ASSIGN "customArrow", locals=CAPTURE_FAILSOFT)` capturing `ArrowItem`/`AbstractArrow` locals; invokers for `getChargedProjectiles`, `getShotPitches`, `onCrossbowShot`, `getStartSound(int)`, 10-arg `shootProjectile` · **Error:** compile: `cannot find symbol` on the invokers' callers, `containsChargedProjectile`, `setCharged`; runtime (if left): mixin APPLY failures (`InvalidInjectionException`, `No candidates were found`) · **Fix:** drop the re-implementations — 1.21 `ProjectileWeaponItem#draw/shoot` apply `PROJECTILE_COUNT`/`PROJECTILE_SPREAD`/piercing/damage/knockback/ignite effects for ANY weapon, and crits at full power. Keep only genuinely extra behaviour, re-pointed: one `@Inject(method="createProjectile", at=@At("RETURN"))` on `ProjectileWeaponItem` for per-arrow tweaks (crossbows call super for arrows); velocity via `@Redirect` of `CrossbowItem#getShootingPower(Lnet/minecraft/world/item/component/ChargedProjectiles;)F` in `use`; charge time via `getChargeDuration(ItemStack, LivingEntity)`; charging sounds via an invoker for `getChargingSounds(ItemStack)`. (a gear/combat library's BowItem/CrossbowItem mixins)

174. **`optionalFieldOf(name, new ArrayList())` with a RAW default makes the whole RecordCodecBuilder group raw on 1.21's DFU** · **Pattern:** `ModifierConfig.CODEC.listOf().optionalFieldOf("attributes", new ArrayList()).forGetter(Config::getAttributes)` (decompiler drops the diamond) · **Error:** `incompatible types: invalid method reference … method getAttributes in class X cannot be applied to given types; required: no arguments`, plus `cannot find symbol: variable materialResource … location: variable armorConfig of type Object` on NEIGHBOURING fields · **Fix:** `new ArrayList<>()` (or `List.of()`); every error in the group disappears at once. · **Scan:** `grep -rn 'optionalFieldOf([^)]*new ArrayList())' src/main/java`

175. **Small removed/renamed members (cluster)** · **Pattern:** the 1.20 member on the left of each pair below · **Error:** `cannot find symbol` on that member (except where noted) · **Fix:** `DamageSource#isIndirect()` → `!source.isDirect()`; `SwordItem#getDamage()` → sum the stack's main-hand `ATTACK_DAMAGE` modifiers (`stack.getAttributeModifiers().forEach(EquipmentSlot.MAINHAND, …)`); `Entity#isMovementNoisy()` (dead override, no error — found by override-probe) → `getMovementEmission()` returning `Entity.MovementEmission.NONE`; `EntityEvent.Size#setNewEyeHeight` → `event.setNewSize(event.getNewSize().scale(f))` (eye height rides on the record); `LivingChangeTargetEvent#getNewTarget()` → `getNewAboutToBeSetTarget()`; `LevelChunk#getStatus()` → `getPersistedStatus()` with `net.minecraft.world.level.chunk.status.ChunkStatus`; `FriendlyByteBuf#writeNullable(x, FriendlyByteBuf::writeUUID)` → inference fails, write a presence boolean + `writeUUID`; `RegisterClientExtensionsEvent` is in `net.neoforged.neoforge.client.extensions.common`; `KeyConflictContext` is `net.neoforged.neoforge.client.settings`; `IGlobalLootModifier#codec()` returns a `MapCodec` and `GLOBAL_LOOT_MODIFIER_SERIALIZERS` holds `MapCodec<? extends IGlobalLootModifier>` (build with `RecordCodecBuilder.mapCodec`); a HUD layer's `RenderSystem.setShaderTexture(0, "textures/gui/icons.png")` compiles and logs `Failed to load texture: minecraft:textures/gui/icons.png` (the atlas is gone since 1.20.2 — delete the rebind).

176. **`SoundEvent.CODEC` inside a JSON data-manager codec cannot parse `"minecraft:…"` in 1.21** · **Pattern:** a mod's datapack material/config codec with `SoundEvent.CODEC.fieldOf("equip_sound")`, decoded by a `SimpleJsonResourceReloadListener` with plain `JsonOps.INSTANCE` · **Runtime:** `Not a JSON object: "minecraft:item.armor.equip_iron"` — the entry is logged and skipped, so every datapack material/config using it silently fails to load (compiles clean; nothing crashes) · **Fix:** `SoundEvent.CODEC` is a `RegistryFileCodec` (needs `RegistryOps` to accept a reference); reference static-registry entries by id with `BuiltInRegistries.SOUND_EVENT.holderByNameCodec()` (or `byNameCodec()` for a bare `SoundEvent`). Found by a Gate B test that parses a sample JSON through the mod's codec. · **Scan:** `grep -rn 'SoundEvent.CODEC\|ParticleTypes.CODEC' src/main/java` then check who decodes it and with which ops.

## I. Long-tail vanilla renames + strategy (added while porting one port)
> **Axis:** version-family (1.20.x→1.21.x) long-tail.
54. **Unmapped SRG leftovers** · **Pattern:** a handful of `m_NNNN_`/`f_NNNN_` survive the srg-remap (Forge-injected members not in base MCP) · **Error:** `cannot find symbol: method m_7654_()` · **Fix:** look each up by owner with `javap` against the recompiled MC jar (in `~/.gradle/caches/ng_execute/**/outputs.jar`). Seen: `m_7654_`/`m_20194_`→`getServer()` (Entity/Level), `m_129889_`→`getAdvancements()` (MinecraftServer), `m_11083_`→`OldUsersConverter.convertMobOwnerIfNecessary`.
· **AUGMENT — one more unmapped SRG id:** `m_129880_` → `MinecraftServer#getLevel(ResourceKey<Level>)` (a `server.m_129880_(key)` in capability code that re-resolves an entity after load).
55. **Loot table lookup** · **Pattern:** `server.getLootData().getLootTable(resourceLocation)` (or its SRG form `m_278653_`) · **Error:** `cannot find symbol: method getLootData` / lookup takes wrong type · **Fix:** `server.reloadableRegistries().getLootTable(ResourceKey.create(Registries.LOOT_TABLE, resourceLocation))` — the lookup now takes a `ResourceKey<LootTable>`, not a `ResourceLocation`. · **AUGMENT — the entity/block override side:** `getLootTable()` now **returns `ResourceKey<LootTable>`** too (was `ResourceLocation`); a custom mob/BE that overrides it must return `ResourceKey.create(Registries.LOOT_TABLE, id)`, and callers that `getLootTable()` then look it up feed that key straight into `reloadableRegistries().getLootTable(key)`.
56. **getPartialTick / getFrameTime (client)** · **Pattern:** `Minecraft.getInstance().getPartialTick()` / `getFrameTime()` · **Error:** `cannot find symbol: method getPartialTick/getFrameTime` · **Fix:** `Minecraft.getInstance().getTimer().getGameTimeDeltaPartialTick(true)` (both go through the new `DeltaTracker`) · **AUGMENT — `getDeltaFrameTime()` is the third alias:** the same removal covers `Minecraft.getDeltaFrameTime()` (a `BlockEntityWithoutLevelRenderer` reaching for a partial tick) → `getTimer().getGameTimeDeltaPartialTick(false)` (pass `false` when you want the *non*-paused/real tick delta a BEWLR wants). (a furniture mod built on that framework library: `ModBlockEntityWithoutLevelRenderer`)
57. **Removed extensible enums → feature-drop strategy** · **Pattern:** a mod extends a vanilla enum that became a data-driven registry — most commonly `MapDecoration.Type` (custom map markers), also some sound/particle enums — usually via a mixin that `valueOf("MY_VALUE")`s a value it injected · **Error:** `cannot find symbol: class Type` / `no enum constant`, dozens of cascading errors · **Fix (pragmatic):** for a *first* port, DISABLE the feature — delete the injecting mixins, remove them from `<modid>.mixins.json`, delete now-dead helper methods, and record the dropped feature in `MIGRATION.md`. A faithful port would register the new registry type (`MapDecorationType` etc.) via `DeferredRegister` + datapack and rewrite the mixins; do that only if the feature matters. Don't silently drop — log it.

58. **ItemStack serialization** · **Pattern:** `stack.save(new CompoundTag())`, `ItemStack.of(tag)` · **Error:** `CompoundTag cannot be converted to Provider` / `method of not found` · **Fix:** `stack.save(entity.registryAccess())` (returns a `Tag`; there's also `save(Provider, Tag)`); `ItemStack.of(tag)` → `ItemStack.parseOptional(entity.registryAccess(), tag)` (returns `ItemStack`, empty on failure) or `ItemStack.parse(provider, tag)` (returns `Optional`). `Entity.registryAccess()` and `Level.registryAccess()` both exist.
59. **Static registry tag access** · **Pattern:** `ForgeRegistries.ITEMS.tags().getTag(tagKey).size()` / `.stream().toList()` · **Error:** `package ForgeRegistries does not exist` · **Fix:** `BuiltInRegistries.ITEM.getOrCreateTag(tagKey)` returns a `HolderSet.Named<>` directly (no Optional) — `.size()` works; for a `List<T>` add `.stream().map(Holder::value).toList()`. (Dynamic registries — ENCHANTMENT, BIOME — are NOT in `BuiltInRegistries`; resolve via `level.registryAccess().registryOrThrow(Registries.X)`, or refactor `Holder<>`-based tag checks to `holder.is(tagKey)`.) · **AUGMENT — the two other NeoForge tag facades are gone too:** `ITag<T>` (`net.neoforged.neoforge.registries.tags.ITag`) → vanilla **`HolderSet.Named<T>`**, so `getTag(...).stream().map(Item::getDefaultInstance)` becomes `.stream().map(Holder::value).map(Item::getDefaultInstance)` (the stream is of `Holder<T>` now); and `IReverseTag`/`registry.tags().getReverseTag(value).map(t -> t.getTagKeys())` → **`registry.wrapAsHolder(value).tags()`** (a `Stream<TagKey<T>>` straight off the Holder). (a furniture mod built on that framework library: its crafting-station block and menu screen)
60. **ProjectileDispenseBehavior restructured** · **Pattern:** `new AbstractProjectileDispenseBehavior() { protected Projectile getProjectile(...){...} }` · **Error:** `cannot find symbol: class AbstractProjectileDispenseBehavior` · **Fix:** renamed to `ProjectileDispenseBehavior`, and it's **no longer an override-based abstract class** — its constructor takes a `ProjectileItem`. The dispensed item must `implements ProjectileItem` (with `asProjectile(...)`), then `new ProjectileDispenseBehavior(theItem)`. A per-site rework, not a rename.

## Deeper clusters seen in one port (each a mini-refactor, flagged for the next porter)
- **Enchantment API** (1.20.5, data-driven): `Enchantments.X` are `ResourceKey<Enchantment>`; `EnchantmentHelper.getItemEnchantmentLevel`/`getDamageBonus` take `Holder<Enchantment>` from `registryAccess()`; tag checks → `holder.is(TagKey)`. Threads `registryAccess`/`Holder` through combat + util methods and their callers.
- **Brewing / potions**: `BrewingRecipeRegistry.addRecipe` → `RegisterBrewingRecipesEvent` + `PotionBrewing.Builder`; `PotionUtils.getPotion/setPotion` → `DataComponents.POTION_CONTENTS` (`PotionContents`), `Holder<Potion>`. ⚠️ **`RegisterBrewingRecipesEvent` fires on the GAME bus (`NeoForge.EVENT_BUS`), NOT the mod bus** — it `extends Event`, not `IModBusEvent`. Registering the listener on `modEventBus` compiles fine but crashes mod construction: *"this bus only accepts subclasses of IModBusEvent"*. (Most `Register*` events are mod-bus; this one isn't.)
- **Armor materials**: `ArmorMaterial` is now a registered record; `ArmorItem` ctor takes `Holder<ArmorMaterial>`; custom materials need `DeferredRegister` registration + the armor-rendering layer (`getArmorTexture`/model) updated.
- **GUI overlays**: `IGuiOverlay`/`RegisterGuiOverlaysEvent`/`ForgeGui` → `LayeredDraw.Layer` + `RegisterGuiLayersEvent`.
- **Custom particle options**: `ParticleOptions.Deserializer` + `Codec` → `MapCodec` + `StreamCodec` static fields on the options type.

61. **EntityType.Builder.of inference failure** · **Pattern:** `EntityType.Builder.of(MyEntity::new, cat).sized(..).build(id)` where `MyEntity`'s ctor is `(EntityType<? extends MyEntity>, Level)` · **Error:** `cannot infer type-variable(s) T` / `invalid constructor reference … no suitable constructor for MyEntity(EntityType<Entity>,Level)` · **Fix:** add an explicit type witness — `Builder.<MyEntity>of(MyEntity::new, cat)`. (The `? extends` wildcard on the ctor blocks T inference; pinning T fixes it. `build(String)` is unchanged in 1.21.1.)
62. **Enchantment level without registryAccess** · **Pattern:** `EnchantmentHelper.getItemEnchantmentLevel(Enchantments.X, stack)` (X is now `ResourceKey<Enchantment>`) · **Error:** `ResourceKey<Enchantment> cannot be converted to Holder<Enchantment>` · **Fix:** if a `Level`/registryAccess is in scope, resolve the `Holder`. If NOT (e.g. a static `(ItemStack)` helper), read the component directly: iterate `stack.getOrDefault(DataComponents.ENCHANTMENTS, ItemEnchantments.EMPTY).entrySet()` and `entry.getKey().is(theResourceKey)` → `entry.getIntValue()`. `Holder.is(ResourceKey)` avoids needing the registry.
63. **Item enchanting-restriction hooks removed** · **Pattern:** overrides of `canApplyAtEnchantingTable(ItemStack, Enchantment)` / `isBookEnchantable(ItemStack, ItemStack)` · **Error:** `method does not override` / `cannot find symbol: class Enchantment` in the param · **Fix:** these Forge item hooks are gone — enchantability is data-driven in 1.21 (the enchantment's `supported_items`/`primary_items` tags). Delete the overrides; move the restriction to a datapack enchantment tag if it matters.

## I2. Pre-1.20 source deltas (surfaced porting a Forge 1.19.2 mod — a single-mob MCreator mod (~15 files))
> **Axis:** version-family (1.19.x→1.20.x). These are gone-by-1.20.1 changes the main 1.20.1→1.21.1 corpus doesn't carry; they hit when `SRC_MC` is **older than 1.20** (an increasingly common install-mod case).
108. **`Item.Properties.tab(CreativeModeTab)` removed** (1.19.3+) · **Pattern:** `new Item.Properties().tab(CreativeModeTab.TAB_MISC)` · **Error:** `cannot find symbol: method tab(CreativeModeTab)` (+ `TAB_MISC`) · **Fix:** creative-tab membership is data/event-driven — drop `.tab(...)`, then add the item in a mod-bus `@SubscribeEvent BuildCreativeModeTabContentsEvent` handler: `if (event.getTabKey() == CreativeModeTabs.SPAWN_EGGS) event.accept(ITEM.get());` (`net.neoforged.neoforge.event.BuildCreativeModeTabContentsEvent`; tab keys in `net.minecraft.world.item.CreativeModeTabs`). (the single-mob mod's item + tab registration classes)
109. **`dropCustomDeathLoot` gained `ServerLevel` + dropped the looting int** (1.21) · **Pattern:** `protected void dropCustomDeathLoot(DamageSource source, int looting, boolean recentlyHit)` · **Error:** `method does not override…` / `incompatible types: DamageSource cannot be converted to ServerLevel` (on the `super` call) · **Fix:** `protected void dropCustomDeathLoot(ServerLevel level, DamageSource source, boolean recentlyHit)` — looting is data-driven (drop the int); thread `level` into the `super` call. (the single-mob mod's entity class)
110. **`DamageSource.<CONST>` constants removed → `source.is(DamageTypes.X)`** (1.19.4/1.20) · **Pattern:** `source == DamageSource.FALL` / `DamageSource.CACTUS` / `DROWN` / `LIGHTNING_BOLT` · **Error:** `cannot find symbol: variable FALL` on `DamageSource` · **Fix:** damage is data-driven — compare with `source.is(DamageTypes.FALL)` (`net.minecraft.world.damagesource.DamageTypes`, a `ResourceKey<DamageType>`). (the single-mob mod's entity class)

## J. Second wave — one port driven to compile-clean (0 errors from ~2800)
> **Axis:** provenance/history (one port) — real patterns, both axes.
These are the tail patterns that surfaced clearing the last ~130 errors. Several are **codemod pitfalls** — blanket renames that were *wrong* for 1.21.1.

64. **Item-carrying network packets** · **Pattern:** `StreamCodec<FriendlyByteBuf, P>` + `buf.readItem()` / `buf.writeItem(stack)` · **Error:** `cannot find symbol: method readItem/writeItem` · **Fix:** the buffer must be a `RegistryFriendlyByteBuf` and items go through the item stream codec: change the codec type to `StreamCodec<RegistryFriendlyByteBuf, P>`, the `(FriendlyByteBuf buf)` ctor/`toBytes` params to `RegistryFriendlyByteBuf`, and `buf.readItem()` → `ItemStack.OPTIONAL_STREAM_CODEC.decode(buf)`, `buf.writeItem(x)` → `ItemStack.OPTIONAL_STREAM_CODEC.encode(buf, x)`. (`registrar.playToServer/Client` accepts a codec on `RegistryFriendlyByteBuf`.)
65. **SpawnEggItem.getType arg** · **Pattern:** `spawnEgg.getType(itemstack.getTag())` (or a `getType(CompoundTag)` codemod'd form) · **Error:** `CompoundTag cannot be converted to ItemStack` · **Fix:** `DeferredSpawnEggItem.getType(...)` now reads the entity from data components — it takes the **`ItemStack`**: `this.getType(itemstack)`.
66. **Entity.getGravity() is final → getDefaultGravity()** · **Pattern:** `protected float getGravity(){ return 0.05F; }` overriding gravity on a projectile · **Error:** `getGravity() in X cannot override getGravity() in Entity` / `overridden method is final` · **Fix:** the overridable hook is now `protected double getDefaultGravity()` (Entity.getGravity() = `isNoGravity() ? 0 : getDefaultGravity()`, and is final). Rename the override and make it return `double`.
67. **NodeEvaluator path methods → PathfindingContext** · **Pattern:** a custom `WalkNodeEvaluator` subclass overriding `getBlockPathType(BlockGetter, int,int,int)` and/or calling `nodeEvaluator.getPathType(level, x,y,z)` / `WalkNodeEvaluator.getBlockPathTypeStatic(levelReader, mutablePos)` · **Error:** `Level cannot be converted to PathfindingContext` / `cannot find symbol: getBlockPathTypeStatic` · **Fix:** everything routes through `PathfindingContext(CollisionGetter level, Mob mob)`. Override `getPathType(PathfindingContext ctx, int,int,int)` (read blocks via `ctx.getBlockState(pos)`); the static is `WalkNodeEvaluator.getPathTypeStatic(ctx, mutablePos)` or the convenience `getPathTypeStatic(Mob, BlockPos)`. Build a context with `new PathfindingContext(mob.level(), mob)`.
68. **DyeColor diffuse colors** · **Pattern:** `dyeColor.getTextureDiffuseColors()` (returned `float[3]`) · **Error:** `cannot find symbol: method getTextureDiffuseColors` · **Fix:** replaced by `int getTextureDiffuseColor()` (packed ARGB). Unpack: `FastColor.ARGB32.red/green/blue(c) / 255.0F`. (We added a `DyeColorCompat.diffuse(DyeColor)` helper returning the old `float[3]`.)
69. **ENTITY_EFFECT particle carries color** · **Pattern:** `level.addParticle(ParticleTypes.ENTITY_EFFECT, x,y,z, r,g,b)` (color passed as the velocity args) · **Error:** `ParticleType<ColorParticleOption> cannot be converted to ParticleOptions` · **Fix:** `ENTITY_EFFECT` is now `ParticleType<ColorParticleOption>`; the color moved into the option: `addParticle(ColorParticleOption.create(ParticleTypes.ENTITY_EFFECT, (float)r,(float)g,(float)b), x,y,z, 0,0,0)`.
70. **⚠️ CODEMOD PITFALL — `Registry.get` was NOT renamed to `getValue` in 1.21.1** · **Pattern:** a blanket `.get(` → `.getValue(` on registry lookups · **Error:** `cannot find symbol: method getValue(ResourceLocation)` · **Fix:** in 1.21.1 the nullable lookup is still `registry.get(ResourceLocation)` (returns `T`); `getValue` is a 1.21.2+ rename. Revert those. (Do NOT confuse with `DataResult.get()`, which *was* removed — see #74.)
71. **⚠️ CODEMOD PITFALL — raw target goal erases the predicate type** · **Pattern:** `new NearestAttackableTargetGoal(this, LivingEntity.class, 20, false, false, entity -> entity.getType()....)` — note the **raw** goal (no `<>`) · **Error (round 1):** `cannot find symbol: method getType()` (the raw `Predicate` SAM param is `Object`) → if you then add explicit `(LivingEntity entity)` you get **`incompatible parameter types in lambda expression`** · **Fix:** parameterize the goal, not the lambda: `new NearestAttackableTargetGoal<LivingEntity>(...)`. The predicate is `Predicate<LivingEntity>` (single-arg) — do **not** convert it to a 2-arg lambda; `TargetingConditions.Selector` (2-arg `(LivingEntity, ServerLevel)`) is a *different* interface used elsewhere. Don't conflate them.
72. **BaseEntityBlock abstract codec()** · **Pattern:** a concrete `BaseEntityBlock` subclass (custom block-entity block) · **Error:** `X is not abstract and does not override abstract method codec() in BaseEntityBlock` · **Fix:** add `public static final MapCodec<X> CODEC = simpleCodec(props -> new X(...));` and `protected MapCodec<X> codec(){ return CODEC; }`. If the ctor takes extra args, close over fixed values (`simpleCodec` only supplies `Properties`).
73. **Skull owner is a ResolvableProfile component** · **Pattern:** `NbtUtils.readGameProfile(tag.getCompound("SkullOwner"))` then `SkullBlockRenderer.getRenderType(type, gameProfile)` · **Error:** `cannot find symbol: readGameProfile` / `GameProfile cannot be converted to ResolvableProfile` · **Fix:** the head's owner lives in `DataComponents.PROFILE` as a `ResolvableProfile`: `ResolvableProfile p = stack.get(DataComponents.PROFILE); getRenderType(type, p);`.
74. **DataResult.get() removed** · **Pattern:** `codec.decode(ops, el).get().ifLeft(...).ifRight(...)` · **Error:** `cannot find symbol: method get()` · **Fix:** `codec.decode(ops, el).resultOrPartial(msg -> LOGGER.error(..., msg)).ifPresent(pair -> ...)` (the `Either`-returning `get()` is gone; `resultOrPartial` takes an error consumer and returns `Optional`).
75. **ItemUtils.onContainerDestroyed takes Iterable** · **Pattern:** `ItemUtils.onContainerDestroyed(itemEntity, stream)` where `stream` is a `Stream<ItemStack>` · **Error:** `Stream<R> cannot conform to Iterable<ItemStack>` · **Fix:** the param is now `Iterable<ItemStack>` — append `.toList()` to the stream. (Also `ItemStack.of(tag)` inside → `ItemStack.parse(entity.level().registryAccess(), tag).orElse(ItemStack.EMPTY)`, see #58.)
76. **Fireball / AbstractHurtingProjectile ctors use Vec3 movement** · **Pattern:** `super(TYPE, owner, dx, dy, dz, level)` or `super(TYPE, x, y, z, dx, dy, dz, level)` · **Error:** `LivingEntity cannot be converted to double` / `no suitable constructor` · **Fix:** the six-double forms collapsed to a `Vec3 movement`: `super(TYPE, owner, new Vec3(dx,dy,dz), level)` and `super(TYPE, x, y, z, new Vec3(dx,dy,dz), level)`. · **AUGMENT — the `xPower/yPower/zPower` fields are also GONE:** `AbstractHurtingProjectile` now steers purely from delta movement (an internal `accelerationPower` scales it each tick). Any code that read/wrote `proj.xPower`/`yPower`/`zPower` (e.g. a mob spell re-aiming a live fireball) won't compile — keep those as **your own** fields if you need them, and apply direction via `proj.setDeltaMovement(dir)`. To resync a re-aimed projectile to clients mid-flight (the old power-setter path did this implicitly), broadcast it yourself: a `ClientboundSetEntityMotionPacket` via `ServerChunkCache.broadcast`, or a custom update message on `PacketDistributor.TRACKING_ENTITY`.
77. **CrossbowAttackMob.shootCrossbowProjectile removed** · **Pattern:** `if (user instanceof CrossbowAttackMob m) m.shootCrossbowProjectile(target, stack, projectile, angle)` · **Error:** `cannot find symbol: shootCrossbowProjectile` · **Fix:** gone from the interface. For a custom crossbow, shoot the projectile manually (view-vector + `Quaternionf` angle offset, then `projectile.shoot(...)`) for every user type — mobs already face their target.
78. **Explosion.getDamageSource() removed; damageSource private** · **Pattern:** a custom `Explosion` subclass calling `this.getDamageSource()` · **Error:** `cannot find symbol: getDamageSource` · **Fix:** `Explosion.damageSource` is private with no getter — the ctor already receives the `DamageSource`, so store your own `public final DamageSource mmDamageSource` and use it.
79. **Screen.renderBackground signature** · **Pattern:** `this.renderBackground(guiGraphics)` · **Error:** `method renderBackground cannot be applied … required: GuiGraphics,int,int,float` · **Fix:** pass the render args: `this.renderBackground(guiGraphics, mouseX, mouseY, partialTick)`.
80. **EditBox.tick() removed** · **Pattern:** `this.myEditBox.tick()` in a screen's tick · **Error:** `cannot find symbol: method tick()` · **Fix:** cursor blink is time-based now — just delete the call.
81. **InventoryScreen.renderEntityInInventoryFollowsMouse signature** · **Pattern:** `renderEntityInInventoryFollowsMouse(gg, x, y, scale, angX, angY, entity)` · **Error:** `method … cannot be applied to given types` · **Fix:** takes a rectangle + mouse now: `renderEntityInInventoryFollowsMouse(gg, x1, y1, x2, y2, scale, yOffset, mouseX, mouseY, entity)`.
82. **Minecraft.getDeltaFrameTime()/getDeltaTracker() don't exist — use getTimer()** · **Pattern:** `Minecraft.getInstance().getDeltaFrameTime()` · **Error:** `cannot find symbol: getDeltaFrameTime` (and `getDeltaTracker` is *also* wrong) · **Fix:** the `DeltaTracker` is reached via `Minecraft.getInstance().getTimer()`; frame delta = `.getGameTimeDeltaTicks()`, partial tick = `.getGameTimeDeltaPartialTick(true)`. (Sharpens #56 — the accessor is `getTimer()`, not `getDeltaTracker()`.)
83. **Holder .get() → .value()** · **Pattern:** `soundEvent.getEvent().get()`, `level.getBiome(pos).get()` · **Error:** `cannot find symbol: method get()` · **Fix:** unwrap a `Holder` with `.value()`, not `.get()`.
84. **LivingDamageEvent split into Pre/Post** · **Pattern:** `@SubscribeEvent void f(LivingDamageEvent e){ … e.getAmount() … }` · **Error:** `cannot find symbol: method getAmount()` (on the container type) · **Fix:** the event is `LivingDamageEvent.Pre` (or `.Post`); damage getter is `getNewDamage()` (settable via `setNewDamage`). `getOriginalDamage()` is the pre-reduction value.
85. **HierarchicalModel.ANIMATION_VECTOR_CACHE is private** · **Pattern:** `KeyframeAnimations.animate(this, anim, t, scale, ANIMATION_VECTOR_CACHE)` in a model subclass · **Error:** `ANIMATION_VECTOR_CACHE has private access in HierarchicalModel` · **Fix:** it's just a reusable scratch vector — declare your own `private static final org.joml.Vector3f ANIMATION_VECTOR_CACHE = new org.joml.Vector3f();` in the subclass.

## K. Third wave — a large boss mod (~750 files, mixin-heavy) driven to compile-clean (1024 → 0)
> **Axis:** provenance/history (the boss mod port) — real patterns, both axes.
A large mixed mod (609 files, mixins, capabilities, networking, custom registry, a multi-part boss with a full
custom renderer stack). Most of its errors were already-catalogued patterns (§A–§J applied cleanly); the entries
below are the genuinely new categories it surfaced. Migrated with parallel agents over disjoint file-set buckets +
a single integrator (see "Parallel agent orchestration" below).

86. **Custom registry: `IForgeRegistry` → vanilla `Registry` + `RegistryBuilder` + `NewRegistryEvent`** · **Pattern:** `new RegistryBuilder<>(key).setName(id).create()` stored as `IForgeRegistry<T>`, populated via `DeferredRegister<T>` on the Forge registry, and a `@SubscribeEvent NewRegistry`/`RegistryEvent` · **Error:** `cannot find symbol: class IForgeRegistry` / `RegistryBuilder` method shape wrong · **Fix:** custom registries are now plain `net.minecraft.core.Registry<T>`: `ResourceKey<Registry<T>> KEY = ResourceKey.createRegistryKey(id); Registry<T> REG = new RegistryBuilder<>(KEY).sync(true).create();` and register it in a `NewRegistryEvent` handler on the **mod** bus — `event.register(REG)`. Entries register via a `DeferredRegister<>(KEY, modid)` bound to that key. (the boss mod's spell-type registry.)
87. **`EntityDataSerializer` → StreamCodec-based** · **Pattern:** a custom `EntityDataSerializer<T>` implementing `write(FriendlyByteBuf,T)` / `read(FriendlyByteBuf)` / `copy` · **Error:** `EntityDataSerializer is not abstract … does not override codec()` / write/read no longer the interface methods · **Fix:** the serializer is now defined by a `StreamCodec`: `EntityDataSerializer.forValueType(streamCodec)` for a simple value, or implement `codec()` returning a `StreamCodec<? super RegistryFriendlyByteBuf, T>` (build it from `ByteBufCodecs.*` / `ByteBufCodecs.registry(key)` / `StreamCodec.composite`). Register it in the `DATA_SERIALIZERS` registry as before.
88. **Advancement/criterion triggers → Codec-based `SimpleInstance`** · **Pattern:** `extends SimpleCriterionTrigger<Instance>` with `getId()`, `createInstance(JsonObject, DeserializationContext, ...)`, and an `Instance` holding `EntityPredicate.Composite` fields · **Error:** `getId()`/`createInstance` don't override anything; `Composite`/`fromJson` gone · **Fix:** 1.21 criteria are data-driven via `Codec`. Drop `getId()` (the id is the registration key) and `createInstance`; make `Instance` a **record implementing `SimpleCriterionTrigger.SimpleInstance`** with a `public static final Codec<Instance> CODEC = RecordCodecBuilder.create(...)` (predicate fields become `Optional<ContextAwarePredicate>` via `EntityPredicate.ADVANCEMENT_CODEC.optionalFieldOf(...)`), and override `public Codec<Instance> codec(){ return Instance.CODEC; }`. Register with the **two-arg** `CriteriaTriggers.register(String id, Trigger)`. `matches` runs off a `LootContext` from `EntityPredicate.createContext(player, entity)`.
89. **`ITeleporter`/`PortalInfo` → `DimensionTransition`** · **Pattern:** a custom `ITeleporter` returning `new PortalInfo(pos, speed, yRot, xRot)`, and `entity.changeDimension(serverLevel, teleporter)` · **Error:** `cannot find symbol: class ITeleporter / PortalInfo` / `changeDimension(ServerLevel, ITeleporter)` no longer exists · **Fix:** dimension travel is a single `net.minecraft.world.level.portal.DimensionTransition`: build `new DimensionTransition(destLevel, Vec3 pos, Vec3 speed, float yRot, float xRot, DimensionTransition.PostDimensionTransition postCallback)` (the `postCallback` — e.g. `PLAY_PORTAL_SOUND` or your own — replaces per-teleporter placement hooks), and call `entity.changeDimension(transition)`.
90. **`Recipe<Container>` → `Recipe<RecipeInput>` + Codec/StreamCodec serializer** · **Pattern:** `implements Recipe<Container>` with `getId()`, `matches(Container, Level)`, `assemble(Container)`, `getResultItem()`, and a `RecipeSerializer` doing `fromJson(JsonObject)`/`fromNetwork`/`toNetwork` · **Error:** `Recipe<Container>` type args wrong; `getId`/no-arg `assemble`/`getResultItem` don't override; serializer methods gone · **Fix:** parameterize on the new input (`Recipe<RecipeInput>`, or `CraftingRecipe`/`CustomRecipe<CraftingInput>` for crafting); **drop `getId()`** (recipes are keyed by `RecipeHolder`); `assemble(RecipeInput, HolderLookup.Provider)` and `getResultItem(HolderLookup.Provider)` take a provider. The serializer is now two codecs — `MapCodec<T> codec()` (build with `RecordCodecBuilder.mapCodec`, ingredients via `Ingredient.CODEC_NONEMPTY`, results via `ItemStack.CODEC`) and `StreamCodec<RegistryFriendlyByteBuf,T> streamCodec()` (`Ingredient.CONTENTS_STREAM_CODEC`, `ItemStack.STREAM_CODEC`, `ByteBufCodecs.registry(...)`). Look recipes up with `level.getRecipeManager().getRecipeFor(TYPE, input, level)` → a `RecipeHolder`.
91. **`IEntityAdditionalSpawnData` → `IEntityWithComplexSpawn`** · **Pattern:** `implements IEntityAdditionalSpawnData` with `writeSpawnData(FriendlyByteBuf)` / `readSpawnData(FriendlyByteBuf)` · **Error:** `cannot find symbol: IEntityAdditionalSpawnData` · **Fix:** renamed to `net.neoforged.neoforge.entity.IEntityWithComplexSpawn`; the methods are `writeSpawnData(RegistryFriendlyByteBuf)` / `readSpawnData(RegistryFriendlyByteBuf)` (registry-aware buffer, so items/holders can ride along via their stream codecs).
92. **Forge event/ability renames — `ToolAction`→`ItemAbility`, `@Cancelable`→`ICancellableEvent`, `ProjectileImpactEvent.ImpactResult`** · **Pattern:** `ToolAction`/`ToolActions.AXE_STRIP`; `@Cancelable class MyEvent extends Event`; `event.setImpactResult(ProjectileImpactEvent.ImpactResult.SKIP_ENTITY)` · **Error:** `cannot find symbol: ToolAction / ImpactResult` / `@Cancelable` not found · **Fix:** `ToolAction`→`net.neoforged.neoforge.common.ItemAbility` (`ItemAbilities.AXE_STRIP`, `ItemAbility.get("name")`); a cancellable event now **implements `ICancellableEvent`** (drop the `@Cancelable` annotation — `isCanceled()/setCanceled(bool)` come from the interface); `ProjectileImpactEvent` lost `ImpactResult` — it's a plain cancellable now, so `setCanceled(true)` to skip the vanilla impact.
93. **`new ClipContext(..., null)` is ambiguous → cast, but prefer `CollisionContext.empty()`** · **Pattern:** `new ClipContext(from, to, Block.COLLIDER, Fluid.NONE, null)` for an entity-independent raycast · **Error (compile):** `reference to ClipContext is ambiguous` — a bare `null` matches both the `Entity` and `CollisionContext` overloads · **Fix:** the minimal compile fix is a cast — `(Entity)null` — **but that reintroduces the R10 runtime NPE** (`Entity.isDescending()` on null at `ClipContext.<init>`). Prefer the fix that satisfies BOTH: pass `CollisionContext.empty()` (the `CollisionContext` overload), exactly what vanilla uses for "no entity". If you took the `(Entity)null` shortcut to get compiling, the R10 scan will (correctly) flag it — go back and swap it to `CollisionContext.empty()`.
94. **`getExperienceReward()` → `getExperienceReward(ServerLevel, @Nullable Entity killer)`** · **Pattern:** `entity.getExperienceReward()` / an override `protected int getExperienceReward()` · **Error:** `method getExperienceReward … cannot be applied to given types` / doesn't override · **Fix:** the hook takes the level + the killer now: `getExperienceReward((ServerLevel)entity.level(), killer)` (pass `null` for the killer if none). Update both overrides and call sites; a static helper must thread a `ServerLevel` in rather than use `this.level()`.
95. **`LootContextParams.KILLER` → `ATTACKING_ENTITY` / `DIRECT_ATTACKING_ENTITY`** · **Pattern:** `builder.withParameter(LootContextParams.KILLER, attacker)` / `.THIS_ENTITY`-adjacent kill params · **Error:** `cannot find symbol: KILLER / DIRECT_KILLER` · **Fix:** renamed — `KILLER`→`ATTACKING_ENTITY`, `DIRECT_KILLER`→`DIRECT_ATTACKING_ENTITY` (`LAST_DAMAGE_PLAYER` unchanged). Feed them from `source.getEntity()` / `source.getDirectEntity()`.
96. **`PaintingVariant` is a datapack registry + 3-arg ctor** · **Pattern:** `new PaintingVariant(w, h)` registered via `DeferredRegister` on `PAINTING_VARIANT` with a `registerName` · **Error:** `constructor PaintingVariant … cannot be applied` (2 args) / registry access wrong · **Fix:** the record ctor is now `new PaintingVariant(int width, int height, ResourceLocation assetId)` (the third arg is the texture id, `modid:variant_name`), and the variant is registered through a **datapack registry** (`RegistrySetBuilder` / a JSON under `data/<ns>/painting_variant/`) — code registration is just the holder key.
97. **Block ctor arg-order — the type/material arg moved FIRST** · **Pattern:** `new DoorBlock(Properties, BlockSetType)`, `new FenceGateBlock(Properties, WoodType)`, `new StandingSignBlock(Properties, WoodType)`, `new ButtonBlock(Properties, BlockSetType, ticks, arrows)` · **Error:** `constructor … cannot be applied to given types` (types don't line up) · **Fix:** 1.21 moved the `BlockSetType`/`WoodType` **before** `Properties`: `new DoorBlock(BlockSetType, Properties)`, `new FenceGateBlock(WoodType, Properties)`, `new StandingSignBlock(WoodType, Properties)`, `new ButtonBlock(BlockSetType, ticksToStayPressed, Properties)`. Check each wood/type block's argument order individually — the rename tools won't reorder args.
98. **`EntityDimensions` accessors + `getDimensions()` is final → `getDefaultDimensions` + `withEyeHeight`** · **Pattern:** `dims.width`/`dims.height` field reads; an override of `public EntityDimensions getDimensions(Pose)`; `EntityDimensions.scalable(w,h)` then setting eye height separately · **Error:** `width has private access` / `getDimensions() … is final, cannot override` · **Fix:** `EntityDimensions` is a record — read via `dims.width()` / `dims.height()` (accessors). `getDimensions` is final; override **`protected EntityDimensions getDefaultDimensions(Pose)`** instead. Eye height rides on the dimensions: `EntityDimensions.scalable(w,h).withEyeHeight(e)` (there's no separate `getEyeHeight` override to set it). ⚠️ a `.width`→`.width()` codemod can double-apply to already-fixed `.width()` → `.width()()`; re-grep and fix `s#\.(width|height)\(\)\(\)#.$1()#g`.
99. **`LogicalSidedProvider` removed → `Minecraft.getInstance().level`** · **Pattern:** `LogicalSidedProvider.INSTANCE.get(LogicalSide.CLIENT)` / `.getWorld(side)` to reach the client level from common code · **Error:** `cannot find symbol: class LogicalSidedProvider` · **Fix:** it's gone. On the client, `Minecraft.getInstance().level`; on the server use the passed `ServerLevel`/`server.getLevel(key)`. Guard client-only reaches behind a dist check (`FMLEnvironment.dist == Dist.CLIENT`) so they don't load client classes server-side.
100. **`CrossbowItem` charged projectiles are a component; shoot via `createProjectile`** · **Pattern:** `CrossbowItem.getChargedProjectiles(stack)` / `setChargedProjectiles` / `addChargedProjectile` reading NBT · **Error:** `cannot find symbol` on those statics · **Fix:** charged ammo is `DataComponents.CHARGED_PROJECTILES` (a `ChargedProjectiles` component): `stack.set/get(DataComponents.CHARGED_PROJECTILES, ChargedProjectiles.of(list))`, `component.isEmpty()/getItems()`. Custom firing overrides `createProjectile(...)` / `shootProjectile(...)` on the new `CrossbowItem` shape (see also #77).
101. **`Explosion` ctor gained particle/sound args; `ProtectionEnchantment` dampener helper removed** · **Pattern:** `new Explosion(level, source, damageSource, calc, x,y,z, radius, fire, interaction)`; `ProtectionEnchantment.getExplosionKnockbackAfterDampener(entity, strength)` · **Error:** `constructor Explosion … cannot be applied` / `cannot find symbol: getExplosionKnockbackAfterDampener` · **Fix:** the ctor now also takes the two `ParticleOptions` (small + large explosion particles) and a `Holder<SoundEvent>`: `new Explosion(level, source, damageSource, calc, x,y,z, radius, fire, interaction, smallParticle, largeParticle, soundHolder)` (use `ParticleTypes.EXPLOSION`/`EXPLOSION_EMITTER`, `SoundEvents.GENERIC_EXPLODE`). The enchantment dampener helper is gone — knockback dampening is data-driven now; drop the call (or apply your own factor).
102. **`StructureType.codec()` must return a `MapCodec`** · **Pattern:** a custom `Structure` subclass whose `StructureType` supplies `Codec<MyStructure> codec()` (via `RecordCodecBuilder.create`) · **Error:** `codec() in … cannot implement codec() in StructureType; return type Codec is not compatible with MapCodec` · **Fix:** build the structure codec with **`RecordCodecBuilder.mapCodec`** (not `.create`) and return `MapCodec<MyStructure>`; include the `Structure.settingsCodec(inst)` group as the first field. (Same MapCodec-vs-Codec split as `BaseEntityBlock.codec()`, #72.)
103. **Small vanilla-signature renames (cluster)** · **Fixes (each a one-liner):** `NbtUtils.readBlockPos(tag)` → `NbtUtils.readBlockPos(tag, key)` returning **`Optional<BlockPos>`** (`.orElse(...)`); `entity.canChangeDimensions()` → `canChangeDimensions(Level from, Level to)`; `getAddEntityPacket()` → `getAddEntityPacket(ServerEntity)`; `Operation.MULTIPLY_BASE` → `AttributeModifier.Operation.ADD_MULTIPLIED_BASE` (and `MULTIPLY_TOTAL`→`ADD_MULTIPLIED_TOTAL`); `SpawnPlacements.Type`/`register` → `SpawnPlacementTypes.*` + an **instance** `isSpawnPositionOk` · **AUGMENT — `SpawnPlacements.register` is now PRIVATE in NeoForge 1.21.1** (`register(...) has private access in SpawnPlacements`): move the registration out of `FMLCommonSetupEvent`/an `init()` and into a mod-bus `@SubscribeEvent RegisterSpawnPlacementsEvent` handler — `event.register(entityType, SpawnPlacementTypes.ON_GROUND, Heightmap.Types.MOTION_BLOCKING_NO_LEAVES, predicate, RegisterSpawnPlacementsEvent.Operation.REPLACE)` (`net.neoforged.neoforge.event.entity.RegisterSpawnPlacementsEvent`; enum is `AND`/`OR`/`REPLACE`, NOT `REPLACE_OR_ADD`). The `(entityType, level, reason, pos, random)` predicate lambda is unchanged. (the single-mob mod's entity class); `MerchantOffer`/`MerchantOffers` (de)serialization → `MerchantOffers.CODEC` / the stream codec; `SuspiciousStewEffects` is a component (`DataComponents.SUSPICIOUS_STEW_EFFECTS`); `Animal.setTame(bool)` → `setTame(bool, bool)`; `mob.convertTo(...)` needs a raw/unchecked cast on the result; `Wolf` collar-color setter is private (drop the override or use the component); `AbstractSkeleton.getArrow(stack, ...)` now takes the **weapon** stack; `populateDefaultEquipmentEnchantments(RandomSource, DifficultyInstance)` → `(ServerLevelAccessor, RandomSource, DifficultyInstance)`; `IronGolem.Crackiness` → `Crackiness.Level` + `Crackiness.GOLEM.byFraction(frac)`.

## L. Client rendering — the boss mod's biggest single bucket (0 → clean)
> **Axis:** version-family (1.20→1.21 client-render API).
The whole `VertexConsumer`/`renderToBuffer` API changed shape in 1.21; a mod with a custom renderer stack
(this one has ~40 model/renderer/layer files) hits every one of these. They compile-fail loudly, so they're
"apply mechanically," but there are a LOT and two are easy to get subtly wrong.

104. **`renderToBuffer` takes a packed ARGB `int`, not 4 floats** · **Pattern:** `model.renderToBuffer(pose, buffer, light, overlay, r, g, b, a)` (four trailing `float`s) · **Error:** `method renderToBuffer … cannot be applied … required: PoseStack,VertexConsumer,int,int,int` · **Fix:** the four color floats collapsed to one packed int: `renderToBuffer(pose, buffer, light, overlay, FastColor.ARGB32.colorFromFloat(a, r, g, b))` (note the **A,R,G,B** order). For opaque white pass `0xFFFFFFFF`. Same for `Model.renderToBuffer` overloads across every model/layer.
105. **`VertexConsumer` builder chain renamed + `endVertex()` dropped** · **Pattern:** `buffer.vertex(matrix,x,y,z).color(r,g,b,a).uv(u,v).overlayCoords(o).uv2(l).normal(mat3,nx,ny,nz).endVertex();` · **Error:** `cannot find symbol: method vertex/color/uv/overlayCoords/uv2/endVertex` · **Fix:** every builder method was renamed and `endVertex()` removed (each `addVertex` finalizes): `addVertex(pose, x,y,z)` · `.setColor(r,g,b,a)` (floats **or** a packed int) · `.setUv(u,v)` · `.setOverlay(o)` · `.setLight(l)` · `.setNormal(pose, nx,ny,nz)` — **normal now takes the `PoseStack.Pose`**, not a `Matrix3f` (`pose.last()` where you used `matrix.normal()`); drop the trailing `.endVertex()`. A blanket sed handles the renames; the `setNormal(pose, …)` arg change and the `endVertex()` deletion need care.
106. **`ModelPart` fields are final; `BufferBuilder`/`Tesselator` reshaped to `MeshData`** · **Pattern:** reassigning `modelPart.x/y/z`/`xRot` on a `final` field; `Tesselator.getInstance().getBuilder()` then `builder.end()` → `BufferBuilder.RenderedBuffer` · **Error:** `cannot assign a value to final variable` / `cannot find symbol: getBuilder / RenderedBuffer` · **Fix:** mutate pose via the setters (`part.setPos`, `part.setRotation`, or `part.x = …` only where still allowed — most are now assigned through helpers). Buffers: `Tesselator.getInstance().begin(Mode, VertexFormat)` returns a `BufferBuilder`; `builder.build()` yields a **`MeshData`** (was `RenderedBuffer`); upload via `BufferUploader.drawWithShader(mesh)`. `RenderSystem.getInverseViewRotationMatrix()` is gone — reconstruct from the camera/pose if you needed it (flagged for visual re-verify, not a crash).
107. **A custom `VertexConsumer` wrapper must implement the new interface** · **Pattern:** a decorator like a `TiledTextureGenerator`/`SheetedDecalTextureGenerator` implementing `VertexConsumer` with the old `vertex/color/uv/endVertex` methods · **Error:** `X is not abstract and does not override abstract method addVertex/setColor/… in VertexConsumer` · **Fix:** implement the renamed surface (`addVertex`, `setColor`, `setUv`, `setUv1`, `setUv2`, `setOverlay`, `setLight`, `setNormal`) and delegate; there's no `endVertex` to forward. `DefaultedVertexConsumer` was removed, so a wrapper that extended it must implement `VertexConsumer` directly.

107b. **🔴 A `VertexConsumer` kept across another `getBuffer(...)` crashes on 1.21 — it used to write into the wrong batch quietly** · **Pattern:** a `MultiBufferSource` wrapper (a "phasing"/"ghost"/tint effect) that returns `new Wrapper(delegate.getBuffer(otherType))`, typically re-routing a solid/cutout entity type to a translucent one from the SHARED buffer, while the renderer (GeckoLib above all) keeps that consumer for the whole model and a layer asks the same source for a different type mid-render · **Runtime (1.21):** `IllegalStateException: Not building!` from `BufferBuilder.ensureBuilding` ← the wrapper's `addVertex` ← `GeoRenderer.createVerticesOfQuad`, under "Rendering entity in world": the shared source closed the first type's builder when the second was requested, and on 1.21 each type gets its own short-lived builder. On 1.20 the shared builder object was reused for the next type, so the stale consumer silently drew into the wrong batch (a wrong-texture glitch nobody reported). Only fires when the wrapped path runs (here: a mob under the mod's own effect), so a gauntlet that dresses one mob finds it on some runs and not others · **Fix:** hold the SOURCE and the type, and fetch the buffer again at the start of every vertex (`addVertex`): the same type returns the same builder, another type a fresh one, and each whole vertex lands in the right batch. **Scan:** `catalog-scans.md` "§L/107b". **Gate:** the gauntlet now gives every mod effect to every mod mob in view (both harness templates).

## §A augment — Vineflower "Couldn't be decompiled" methods (its `$VF` marker) → recover with CFR
Add to §A (decompile prep): Vineflower sometimes emits a method body as a bare comment — Vineflower's `$VF` marker, a colon, then `Couldn't be decompiled` —
(complex control flow, or a **huge tool-generated method** it won't lift). **Void** ones compile as empty
no-ops (silently wrong — the method does nothing, e.g. an entity whose entire `aiStep` became a stub); **non-void**
ones fail with `missing return statement`. **Two common sources:** (1) genuinely complex control flow; (2)
**machine-generated giants** — e.g. Blockbench-exported model `createBodyModel` methods with thousands of `addBox`
calls (a ~750-file boss mod's boss body models were ~2,000–5,800 cubes each — Vineflower choked on all of them).
**Scan:** `grep -rn "Couldn't be decompiled" src/main/java`.

**Recovery — prefer CFR over hand-reconstruction (the reliable path):** a *different* decompiler usually succeeds
where Vineflower failed. `tools/cfr.jar` (CFR) decompiled every one of the boss mod's failures cleanly (the
un-decompilable `aiStep`, 2 per-level state-manager methods, and all 9 giant `*BodyModel` classes — 26,565 `addBox` calls
total). Workflow, same for logic OR geometry:
1. `java -jar tools/cfr.jar <original.jar> --outputdir <tmp> <fully/qualified/ClassName>` (SRG-named output).
2. SRG-remap it: `python3 tools/srg-remap/apply_mapping.py tools/srg-remap/srg2official-1.20.1.json <tmp>` → official names.
3. Splice the recovered method (logic) or **replace the whole file** (a single-method generated class like a BodyModel)
   into `src/main/java`. Apply any per-method API fixes (e.g. catalog #69 `ENTITY_EFFECT`→`ColorParticleOption`) and add imports.
4. Compile. (A geometry class is pure data — `CubeListBuilder.create/texOffs/addBox`, `PartDefinition.addOrReplaceChild`,
   `PartPose` — all **unchanged 1.20→1.21**, so a faithful CFR+remap drops straight in.)
Only stub-and-note (`// 1.21 MIGRATION: <what it did>`) as a last resort if CFR *also* fails; then list every stub
in `MIGRATION.md` so nothing ships silently broken. **A void stub is a correctness bomb** — the catalog-scans
sweep flags `"Couldn't be decompiled"` for exactly this reason; treat any remaining stub as an open bug, not "done".

## §A augment — MCreator `(new Object(){…}).m()` anon-class blocks → javac OOM death-spiral → hoist to static methods
· **Pattern:** MCreator emits "local functions" as an immediately-invoked anonymous class:
`X _v = (new Object(){ public X getArrow(Level l, float d, int k){ … return e; } }).getArrow(args);`. A boss's
tick procedure can hold **dozens in one method** (a ~380-file MCreator mob mod: 79 in one 2161-line method, 131 across 3 files).
· **Symptom (NOT a compile error):** once the *surrounding* errors are fixed, `compileJava` **hangs then OOMs**
(that mod: old gen pinned 99.8%, 800s+ in full GC, OOM at ~22 min). Thread dump sits in
`com.sun.tools.javac.code.Types.isSubtype`/`containsTypeRecursive` on wildcard types — javac fully type-infers
every anon-class poly expression, and the cost is super-linear in count-per-method. **The trap hides behind other
errors:** while errors remain javac bails early (fast); it only blows up on the *clean* pass, so it surfaces last.
· **Diagnosis:** if a compile pins CPU with no log progress, `jstat -gcutil <pid>` (FGCT climbing, O≈100) + `jstack`
confirm it. Bisect by **stubbing the giant method bodies** (keep signatures so callers still resolve → clean compile
→ if fast, the bodies are the cause); this also *unmasks the real remaining errors* the OOM was hiding.
· **Fix (NOT more heap — allocation is unbounded; forked 4g javac still OOM'd):** hoist each anon block into a plain
`private static <RET> __spawn_helper_N(<params>){ <body> }` and replace the site with `__spawn_helper_N(args)`. The
bodies capture no enclosing locals (params + statics only), so extraction is mechanical. A brace/paren-matching
codemod (regex can't handle nested-brace bodies or `})`-then-newline-`.method()`) does all N at once → seconds-long
compile. Reference implementation: a mod's `scratchpad/extract_robust.py` in that MCreator mob mod's port.
· **AUGMENT to `build.gradle`:** set `-Xmaxerrs 2000` (javac's default 100 cap hides the true error count on a big
port) and `options.fork=true, forkOptions.memoryMaximumSize='4g'` (isolates the compiler heap from the Gradle daemon).

## Parallel agent orchestration (used to clear a ~750-file boss mod's ~1000 errors fast)
A big compile-error backlog parallelizes well once the mechanical codemods are done and only per-file semantic
work remains. What worked: (1) bucket the remaining files into **disjoint file-sets** by subsystem (networking /
items / blocks / mixins / events+util / entities+AI / renderers / models / buffers / client-GUI) so no two agents
edit the same file; (2) hand every agent the **same cheat-sheet** (the applied-pattern list — i.e. this catalog)
so fixes stay consistent; (3) agents **edit only, never run gradle** — a single **integrator** compiles once at the
end and resolves cross-bucket **orphan files** (a file both buckets thought the other owned) + interface mismatches;
(4) if agents die at a token/context limit, **commit their WIP**, recompile **uncapped** (`-Xmaxerrs 100000` via an
init script — javac caps error *display* at ~100, so the raw count looks far smaller than it is), and **resume** each
agent via SendMessage with its context intact. Honesty note: always quote the **uncapped** error count — the capped
number understated the boss mod's remaining work ~5× (150 files / 1024 errors read as "~28").

## H. Mixins
> **Axis:** loader-transform (mixin mechanics). Re-verify each target against DST_MC vanilla.
40. **Refmap + SRG targets** · **Pattern:** `<modid>.mixins.json` has `"refmap": "...refmap.json"`; `@Inject(method="m_12345_", ...)` · **Error:** (runtime mixin-apply failure; also compile if targets renamed) · **Fix:** delete the `refmap` key (NeoForge runs on official mappings); SRG-remap the `method=` target strings; set `compatibilityLevel` ≥ `JAVA_21`; declare `[[mixins]] config="<modid>.mixins.json"` in `neoforge.mods.toml`. Re-target any `@Inject/@Redirect` whose vanilla member changed 1.20→1.21.
41. **Mixin `this` self-cast** · **Pattern:** inside a `@Mixin(Entity.class) class EntityMixin`, `return (Entity)this;` · **Error:** `incompatible types: EntityMixin cannot be converted to Entity` · **Fix:** the mixin class isn't the target at compile time — double-cast through Object: `(Entity)(Object)this`.
42. **⚠️ Accessor return type must be fully generic** · **Pattern:** `@Accessor("instanceToChannel") Map examplemod$getInstanceToChannel();` (raw `Map`) · **Error:** downstream `Object cannot be converted to SoundInstance` at every `.keySet()`/`.get()` use · **Fix:** declare the real generic type on the accessor (`Map<SoundInstance, ChannelAccess.ChannelHandle>`), not a raw type.
43. **🔴 RUNTIME BUG — register every new mixin in the config** · **Pattern:** you add an `*Accessor`/injector mixin (e.g. `EntityRenderDispatcherAccessor`) and cast to it in code, but forget to list it in `<modid>.mixins.json` · **Error:** compiles fine; at runtime the interface is never woven onto the target → `ClassCastException: target cannot be cast to XAccessor`. **Invisible to javac.** · **Fix:** add the class to the `mixins`/`client`/`server` array. **Guard it with a test** — see `MixinConfigIntegrityTest` (asserts every name in the config has a compiled `.class`). This test caught exactly this omission while porting one port.

168. **Mixin that added "extra" enchantment levels by injecting into `EnchantmentHelper` has no 1.21 target — use `GetEnchantmentLevelEvent`** · **Pattern:** `@Inject(method="getTagEnchantmentLevel(Lnet/minecraft/world/item/enchantment/Enchantment;Lnet/minecraft/world/item/ItemStack;)I", at=@At(value="RETURN", ordinal=1), locals=CAPTURE_FAILHARD)` and `@Redirect(method="runIterationOnItem…", at=@At(target="…ItemStack;getAllEnchantments()Ljava/util/Map;"))` · **Error:** `incompatible types: ResourceKey<Enchantment> cannot be converted to Enchantment` in the handler; at runtime the targets do not exist (`runIterationOnItem` now iterates the `ENCHANTMENTS` component) · **Fix:** delete the mixin and subscribe to NeoForge's `GetEnchantmentLevelEvent` (game bus): `if (event.isTargetting(key)) event.getHolder(key).ifPresent(h -> event.getEnchantments().set(h, event.getEnchantments().getLevel(h) + extra))`. It fires for both the single-level and the all-enchantments query, so every vanilla enchantment effect sees the added levels. (a gear/combat library's built-in enchantments)

172. **The Forge import codemod does not touch slash-form descriptors inside mixin `target`/`method` strings** · **Pattern:** `@At(value="INVOKE", target="Lnet/minecraftforge/common/ForgeHooks;onEmptyLeftClick(Lnet/minecraft/world/entity/player/Player;)V")`, `target="Lnet/minecraftforge/client/event/InputEvent$InteractionKeyMappingTriggered;shouldSwingHand()Z"` · **Runtime:** compiles clean (strings), then `InvalidInjectionException … could not find any targets` at mixin APPLY — for a `client` mixin only on the client (Gate C `launch`), never on Gate B · **Fix:** run `tools/srg-remap/forge_import_codemod.pl`, which now rewrites the slash form too (it did not when this was found): `Lnet/minecraftforge/common/ForgeHooks;` → `Lnet/neoforged/neoforge/common/CommonHooks;`, `Lnet/minecraftforge/client/event/` → `Lnet/neoforged/neoforge/client/event/`, then confirm the call still exists in the patched method (`sed -n '/private boolean startAttack/,/^    }/p' Minecraft.java` from the NeoGradle `patchUserDev` sources jar). · **Scan:** `grep -rn 'Lnet/minecraftforge/' src/main/java`

173. **NeoForge's own patch can remove a mixin's injection point** · **Pattern:** a Forge 1.20 mod `@WrapOperation`s `ItemStack.is(Item)` inside `AbstractSkeleton#reassessWeaponGoal` to make it accept modded bows · **Runtime:** with `"injectors": {"defaultRequire": 1}` the mixin fails to apply (`expected 1 invocation(s) but 0 succeeded`) — NeoForge 21.1 already rewrote that line to `itemstack.getItem() instanceof BowItem`, so there is no `ItemStack.is` call left to wrap · **Fix:** read the target in the PATCHED sources (NeoGradle `build/neoForm/*/steps/patchUserDev/outputs.jar`, not vanilla), and delete the mixin when NeoForge already does what it did. (a gear/combat library's AbstractSkeleton mixin)

## R. Runtime patterns (compile clean, crash at run) — found via `runGameTestServer`
> **Axis:** runtime (compile-clean, crash-at-run) — caught by the gates, not the compiler.
These NEVER show up at `compileJava`. The only way to catch them is to actually load the mod
— run the headless GameTest server, or the **client world-driving boot loop** (§ below). Format
uses **Runtime:** (the crash/log line) in place of the compile **Error:**. R1–R4 crash at
**mod-load**; R5 on a **dedicated server tick**; **R6–R11 need a player in a live world** (found by
the client boot loop that creates a world + spawns every mob, NOT by a headless GameTest — R8/R9/R10
needed the **battle** stress mode with mobs fighting to the death, and R11 needed a human opening the
creative inventory mid-run); **R12 is headless again** — the persistence round-trip GameTest catches it.
All confirmed while getting one port to load + run in 1.21.1.

R1. **Registering a class/object with no `@SubscribeEvent` methods** · **Pattern (two forms):** (a) an explicit `NeoForge.EVENT_BUS.register(this)` / `bus.register(X.class)`; (b) a **`@EventBusSubscriber`** annotation on a class that has no static `@SubscribeEvent` methods — NeoForge's `AutomaticEventSubscriber` registers annotated classes for you, and `register` then throws. In both, the class's real listeners (if any) are `bus.addListener(this::method)` calls, so the annotation/register is redundant. · **Runtime:** `IllegalArgumentException: class X has no @SubscribeEvent methods, but register was called anyway` — crashes mod construction. (Forge silently tolerated it; NeoForge throws.) · **Fix:** remove the `register(...)` call **or** the `@EventBusSubscriber` annotation (whichever applies). If the class genuinely should have handlers, annotate them `@SubscribeEvent` instead. · **⚠️ Dist trap — this can be a CLIENT-ONLY crash a server GameTest misses:** `@EventBusSubscriber(value = {Dist.CLIENT})` only runs on the client, so `runGameTestServer` (a dedicated server) loads fine while `runClient` crashes. This is a poster child for the client boot smoke test (§ "Running the runtime gate"). **Scan for it:** `grep -rl '@EventBusSubscriber' src/main/java | while read f; do [ "$(grep -cE '^[[:space:]]*@SubscribeEvent' "$f")" -eq 0 ] && echo "no-handler subscriber: $f"; done` (found the port's main `@Mod` class itself — annotated `@EventBusSubscriber(bus=MOD, value={Dist.CLIENT})` with zero handlers).
R2. **Reading a config value during registration / `RegisterEvent`** · **Pattern:** a `DeferredRegister` entry's supplier or an item/`ArmorMaterial` constructor reads `SomeConfig.X.get()` — e.g. `properties.durability(cfg.value.get())`, or an `ArmorMaterial` record built from config · **Runtime:** `NullPointerException: Cannot get config value before spec is built` — `RegisterEvent` fires before configs load. · **Fix:** don't read config at registration. Bake the config **default** as a constant (matching the `.define(..., <default>)` value) and document that field as no-longer-live-configurable. (Behavioral methods — `use`, `hurtEnemy`, tick — run at runtime *after* config load, so config reads there are fine.)
R3. **Forcing a cross-registry `Holder` to resolve during registration** · **Pattern:** `BuiltInRegistries.SOUND_EVENT.wrapAsHolder(SoundInit.X.get())` (or any `.get()` on another registry's `DeferredHolder`) inside a registration supplier — e.g. an `ArmorMaterial`'s equip sound · **Runtime:** `IllegalStateException: Trying to access unbound value: ResourceKey[...]` — the referenced registry (SOUND_EVENT) hasn't populated yet when this registry (ARMOR_MATERIAL) builds. · **Fix:** pass the `DeferredHolder` **directly** as the `Holder<T>` (it resolves lazily at use-time). The mod may type the field `Supplier<T>` even though the object is a `DeferredHolder` (a `Holder`), so cast through Object: `(Holder<SoundEvent>)(Object)SoundInit.X`.
R4. **Hoisted `SPEC = BUILDER.build()`** — see **§A #3b**. A decompile artifact, but it only manifests at runtime (`Cannot get config value before spec is built` on first `.get()`), so it belongs on this list too. Systematic — scan *every* config class.
R5. **🔴 Reading a CLIENT config value in common/server-tickable code** (crashes every dedicated/multiplayer server) · **Pattern:** server-reachable code — an entity's `tick`/`baseTick`, a common event handler — reads `SomethingClientConfig.X.get()` (e.g. a mob's `baseTick` consulting a `show_health_bars_on` client toggle while adding `ServerPlayer`s to a boss bar). Compiles fine and works in **singleplayer** (integrated server = client, so client configs are loaded in-process). · **Runtime:** on a **dedicated server** client configs are NEVER loaded → `IllegalStateException: Cannot get config value before config is loaded` on the first tick → the entity (and the server) crash. Note the wording: "before config is **loaded**" (client config absent), vs R2/#3b's "before **spec is built**". · **Fix:** don't read client config server-side. Guard the read so it's inert when the client spec isn't loaded — `SomethingClientConfig.SPEC.isLoaded() && ...get()` (honors it in singleplayer, skips it on a dedicated server), or gate the whole block on `FMLEnvironment.dist == Dist.CLIENT` / `level().isClientSide` when it's purely a client concern. **Find them all:** `grep -rnE '[A-Za-z]*ClientConfig\.[a-z_]+\.get\(\)'` and check each isn't reachable from server tick/event code that lacks an `isClientSide`/`@OnlyIn(CLIENT)` guard. The GameTest server *is* a dedicated server, so `runGameTestServer` (esp. a spawn-every-mob test that lets them tick) catches this.
R6. **Advancement lookup handed to `getOrStartProgress(null)`** (crashes on player tick) · **Pattern:** a helper does `player.getAdvancements().getOrStartProgress(server.getAdvancements().get(id))` where `id` doesn't resolve (advancement was renamed/removed, or a data-pack path is wrong). In 1.21 `ServerAdvancementManager.get(ResourceLocation)` returns a **`@Nullable AdvancementHolder`**, and `getOrStartProgress`/`startProgress` immediately deref it → `NullPointerException: Cannot invoke "AdvancementHolder.value()" because "advancement" is null`. Fired from a `PlayerTickEvent.Post` handler here (`MiscUtils.hasAdvancement` on an `examplemod:.../kill_all_mutants` id), so it crashed **every player tick** the moment you entered a world. · **Fix:** null-guard the holder before use — `var adv = server.getAdvancements().get(id); if (adv == null) return false; return player.getAdvancements().getOrStartProgress(adv).isDone();` (the sibling `award(...)` helper already null-checked; the query path just forgot). **Scan:** `grep -rn 'getOrStartProgress\|getAdvancements().get(' src/main/java`. · **Why GameTest missed it:** no player ticks in a bare GameTest — needs a real player in a world (the client boot loop).
R7. **Custom packet decoder that doesn't fully drain the buffer** (client disconnect / crash on receive) · **Pattern:** a `CustomPacketPayload`'s `StreamCodec` read-constructor reads the header fields but **stashes the live `FriendlyByteBuf`** (`this.readBuf = buf`) to read the rest later in `handle()` (a lazy/deferred read — e.g. an animation payload that can only be parsed once the target object is resolved client-side). · **Runtime:** `io.netty ... DecoderException: Packet clientbound/minecraft:custom_payload was larger than I expected, found N bytes extra whilst reading packet` → the client disconnects and crashes. NeoForge's StreamCodec pipeline **requires the decoder to consume the ENTIRE buffer**, and the pooled buffer is recycled before `handle()` runs anyway — so both the "leftover bytes" check and the deferred read are broken. (Old Forge's `NetworkEvent` handed you a buffer you could stash; NeoForge does not.) · **Fix:** in the decoder, copy the remaining bytes into a buffer you **own** and replay it later: `this.readBuf = new FriendlyByteBuf(buf.readBytes(buf.readableBytes()));` — this drains the source (satisfying the full-consume check) and survives past decode. **Scan:** `grep -rn 'this\.\w*[Bb]uf *= *buf\|readBuf = buf' src/main/java`. · **Why GameTest missed it:** the payload only sends when an animated entity syncs to a **client** — needs a real client receiving it (the boot loop).
R8. **Touching `this.entityData` inside `defineSynchedData(Builder)`** (NPE in the entity constructor) · **Pattern:** an entity's `defineSynchedData(SynchedEntityData.Builder builder)` — after the `builder.define(ACCESSOR, default)` calls — invokes a **getter/setter that reads or writes `this.entityData`**, e.g. `this.setFlag(32, true)` (whose body does `this.entityData.get(FLAGS)` / `.set(...)`) or `this.entityData.set(...)` directly. In 1.20 `defineSynchedData()` ran with `this.entityData` already present; the 1.21 signature takes a **Builder** and `this.entityData` is **not assigned until after `defineSynchedData` returns** (it's built from the builder). · **Runtime:** `NullPointerException: Cannot invoke "SynchedEntityData.get(...)" because "this.entityData" is null` at `Entity.<init>` → **every construction of that entity crashes**. · **Fix:** in `defineSynchedData` you may ONLY `builder.define(...)`. Bake any initial non-default value into the **define default** instead of setting it afterward — e.g. `builder.define(FLAGS, (byte)(32 | 128))` rather than `define(FLAGS, (byte)0); setFlag(32,true); setFlag(128,true)`. **Scan (parse each method body, not a line grep):** find every `void defineSynchedData(` and check its body for `this.entityData.(get|set)(` or `this.set…(` calls. · **Why the earlier gates missed it:** this entity (a small summoned mob) is only ever constructed mid-combat — one boss mob's rod projectile turns into one on block-hit — so only the **battle** stress mode (mobs actually fighting) triggered the code path.
R9. **`AttributeSupplier` rebuilt from `Mob.createMobAttributes()` drops a vanilla attribute** (crash on the missing attribute's first use — usually melee) · **Pattern:** a mob's `createAttributes()`/`createConfiguredAttributes()` was rewritten to start from `Mob.createMobAttributes()` and re-`.add(...)` MAX_HEALTH/ARMOR/etc. from config — but the mob subclasses a vanilla type whose own `createAttributes()` included **`ATTACK_DAMAGE`** (every `Monster.createMonsterAttributes()` adds it; `Mob.createMobAttributes()` does NOT). Any mob that ever calls `doHurtTarget` (a `MeleeAttackGoal`, a swoop, etc.) but is missing `ATTACK_DAMAGE` from its supplier crashes. · **Runtime:** `IllegalArgumentException: Can't find attribute minecraft:generic.attack_damage` at `AttributeSupplier.getAttributeInstance` → `Mob.doHurtTarget` → `MeleeAttackGoal.tick`. · **Fix:** add the dropped attribute back — `.add(Attributes.ATTACK_DAMAGE, <value>)` (equivalently, base the builder on `Monster.createMonsterAttributes()`, which is exactly `Mob.createMobAttributes().add(ATTACK_DAMAGE)`). Adding it to a mob that never melees is harmless (unused). **Scan:** for each `create*Attributes()` that uses `Mob.createMobAttributes()` and lacks `Attributes.ATTACK_DAMAGE`, check whether the mob can reach `doHurtTarget` (a melee/target goal) — in one port, 9 mobs had dropped it. · **Why the earlier gates missed it:** the attribute is only read when the mob **lands a melee hit**, so only the **battle** stress mode (mobs fighting to the death) reached it — and only for the mob whose RNG put it in melee range that run.
R9b. **⚠ AUGMENT to R9 — the ERA-JUMP direction: the vanilla base GAINED an attribute, so a
supplier that was correct for years becomes a crash without changing a line.** R9 is about a
rewrite that DROPS something (`Mob.createMobAttributes()` in place of `Monster`'s). The 1.21.1 →
26.2 form is the opposite and is invisible to every burn-down: 26.2 added
`Attributes.TEMPT_RANGE` and put it in **`Animal.createAnimalAttributes()`** (neither the
attribute nor that method exists on 1.21.1). `TemptGoal` and `TemptingSensor` read it with
`getAttributeValue`, which THROWS rather than defaulting. · **Runtime:**
`IllegalArgumentException: Can't find attribute minecraft:tempt_range` at
`Mob.serverAiStep` → `goalSelector.tick` → the mob's own `aiStep`, i.e. the first AI tick after
the mob spawns. · **Who it hits:** every mod mob that subclasses a vanilla ANIMAL while basing
its attributes on `Monster.createMonsterAttributes()` — the standard shape for a "hostile
version of a friendly mob". a ~750-file boss mod had **nine** (bee, cat, chicken, cow, mushroom cow,
goat, parrot, pig, wolf); only the bee crashed in the run, because only the bee inherits its
parent's goals via `super.registerGoals()` — the other eight were one goal-registration edit
away from the same crash and were fixed together, per R9's own "a defect found is a defect class
found". · **Fix in a §W tree:** a compat pair (`MobsAttributes.hostileAnimal()`), because the
1.21.1 half cannot name an attribute that does not exist there. NOT a rename row and NOT shared
source. · **The general check, and it is one command per superclass:** for every mod entity, diff
its vanilla superclass's OWN `createAttributes()` base against the base the mod substituted —
`grep -A8 "static AttributeSupplier.Builder create" <vanilla>.java` on both targets' sources
jars. The same sweep found `Zombie` gained nothing but confirmed `SPAWN_REINFORCEMENTS_CHANCE`
is still needed (a `getAttribute(...)` in `finalizeSpawn` that NPEs, not throws — a second
failure mode from one missing entry). · **Why no cheap gate sees it:** registration succeeds, the
entity constructs, and Gate B's spawn test can pass if the mob never gets an AI tick with the
relevant goal running. It surfaced in Gate C's **`spawn`** phase, 24 entity types in.

· ⚠ **AUGMENT — the same crash from a MIXIN on the VANILLA animal, and it does not need the mob to be
yours.** a ~390-file mob mod's `CowEntityMixin` injected at `RETURN` of `Cow.createAttributes` with a handler
that built a brand-new `Mob.createMobAttributes().add(MAX_HEALTH).add(MOVEMENT_SPEED).add(ATTACK_DAMAGE)`
— on 1.21.1 exactly vanilla's cow plus attack damage, so nobody noticed it REPLACED rather than
extended. On 26.2 the vanilla cow is built on `Animal.createAnimalAttributes()`, which adds
`TEMPT_RANGE`, and the replacement dropped it: **every vanilla cow in every world crashed the server on
its first AI tick** (`Can't find attribute minecraft:tempt_range` from `TemptGoal.canUse`). Found not
by the mob mod's own gates, which spawn only that mod's mobs, but by a DEPENDENT's Gate B that
seated a vanilla cow in an airplane. · **Fix:** mutate the builder vanilla returns
(`callbackInfo.getReturnValue().add(ATTACK_DAMAGE, 2.0)`), never rebuild it, so each version keeps
whatever its own vanilla puts there. Identical attribute set on 1.21.1. · **Sweep:** any mixin into a
vanilla `createAttributes` whose handler NAMES `create*Attributes()` itself is this bug waiting for the
next era. · **And a bisect trap worth knowing:** a Gate-B wrapper whose preflight refuses to run with a
jar missing (exit 2 before Gradle starts) leaves the PREVIOUS run's log in place, so a bisect that
greps the log afterwards reports the old result for every split. Run Gradle directly when bisecting,
and print how many mods the run actually attached next to each verdict.

R10. **`new ClipContext(..., (Entity)null)` NPEs in 1.21** (raycast on the server, often from an AoE/attack entity) · **Pattern:** a raycast helper builds `new ClipContext(from, to, Block.COLLIDER, Fluid.NONE, (Entity)null)` to do an entity-independent line-of-sight/collision check. In 1.21 the `Entity` overload delegates to `CollisionContext.of(entity)` → `EntityCollisionContext` → `entity.isDescending()`, which **NPEs on a null entity** (old Forge's `ClipContext` tolerated null). · **Runtime:** `NullPointerException: Cannot invoke "Entity.isDescending()" because "entity" is null` at `ClipContext.<init>` (from `Level.clip`). · **Fix:** use the **`CollisionContext` overload** with an empty context: `new ClipContext(from, to, Block.COLLIDER, Fluid.NONE, CollisionContext.empty())` — this is exactly what vanilla does for "no entity" (`entity == null ? CollisionContext.empty() : CollisionContext.of(entity)`). **Scan:** `grep -rn 'new ClipContext(' src/main/java | grep -i 'null'`. · **Why the earlier gates missed it:** the call sat in an `AreaDamage` (AoE field) entity's `baseTick` — only spawned by a mob's area attack, so only **battle** combat reached it.
R11. **Render-time `ItemProperties` override dereferences an NBT-derived value without a null-guard** (crash while rendering the item) · **Pattern:** a `clientSetup` `ItemProperties.register(item, id, (stack, level, living, seed) -> …)` predicate reads a value derived from the stack's NBT — e.g. `(float)MyItem.getAmmoType(stack).index` — but the getter returns **null** when the stack has no NBT (a fresh/default item, as in the **creative inventory**). · **Runtime:** `NullPointerException: Cannot read field "index" because the return value of "…getAmmoType(ItemStack)" is null` under `…Screen`/`Rendering screen` (the property predicate runs every time the model is baked/rendered, for ANY stack). · **Fix (prefer the ROOT, not per-override):** the shared NBT getter is what's actually unsafe — multiple overrides (and use/shoot paths) call it. Make **`getAmmoType`/the getter itself never return null** by falling back to the default (index 0, which is also what `getInt("ProjectileType")` yields when the tag is present): `int t = tag != null ? tag.getInt("ProjectileType") : 0; var a = TYPES.get(t); return a != null ? a : TYPES.get(0);`. Guarding one property override is whack-a-mole — a *second* override (`pull` → `getUseDuration` → `getChargeDuration` → `getAmmoType(stack).getLoadDuration()`) crashed the very next run. Verify **no caller null-checks** the getter first (they didn't). **Scan:** `grep -rn 'getAmmoType\|<nbtGetter>' src/main/java` and confirm every deref is safe. · **Why the earlier gates missed it:** it only fires when the item's model is actually **rendered** — here, a human opening the **creative inventory** during the stress test (a great argument for letting someone poke at the game while it runs, not just watching the automated fight).

R12. **NBT save code that NPEs on a null field** (entity/block-entity silently won't persist; a real world-save errors the chunk) · **Pattern:** `addAdditionalSaveData`/`saveAdditional` writes a nullable field unconditionally — classically `tag.putUUID(key, owner != null ? owner.getUUID() : null)`. `CompoundTag.putUUID(key, null)` **NPEs** (it calls `uuid.getMostSignificantBits()`); same risk for any `tag.putX(key, <nullable>)`. Works in normal play (the field — e.g. a projectile's owner — is always set there) but crashes for an instance created/`/summon`ed without it. · **Runtime:** `NullPointerException` in `addAdditionalSaveData` → `EntityStorage: An Entity type <id> has thrown an exception trying to write state. It will not persist`. · **Fix:** only write the key when non-null (`if (owner != null) tag.putUUID(key, owner.getUUID());`); the read side should already guard with `tag.contains(key)`. **Scan:** `grep -rn 'putUUID(' src/main/java` for any passing a nullable. · **Found by:** the **persistence round-trip GameTest** (Gate B, headless) — it saves→loads→re-saves every serializable entity + block entity, so it creates the ownerless instances normal play never persists. This whole class (asymmetric or null-crashing save/load) is invisible to every other gate because nothing else serializes.

R13. **🔴 Registering into a vanilla registry during `commonSetup` — "Registry is already frozen"** (mod-load crash; found by Gate B on the very first server boot) · **Pattern:** an `init` class does `Registry.register(BuiltInRegistries.STRUCTURE_PIECE / CUSTOM_STAT / TRIGGER_TYPES, id, value)` (or the vanilla helper `CriteriaTriggers.register(String, trigger)`, which *is* `Registry.register(TRIGGER_TYPES,…)`) from `FMLCommonSetupEvent` (or its `enqueueWork`). Forge 1.20 tolerated late writes to these registries; **NeoForge freezes them after the `RegisterEvent` phase**, which is *before* `commonSetup`. · **Runtime:** `IllegalStateException: Registry is already frozen (trying to add key ResourceKey[minecraft:<registry> / <modid>:<name>])` during `FMLCommonSetup` / `DeferredWorkQueue`. · **Fix:** move every such registration to a **`DeferredRegister` on the mod bus** (flushes at `RegisterEvent`, before freeze): `DeferredRegister.create(Registries.STRUCTURE_PIECE/CUSTOM_STAT/TRIGGER_TYPE, modid)` + `.register(modEventBus)` in the mod ctor. Keep the old static fields as the values via a `register(name, value)` helper that returns the value (so call sites are unchanged; `DeferredHolder` fields need `.get()`). Any *formatter/interning* side call (e.g. `Stats.CUSTOM.get(id, fmt)`) can stay in `enqueueWork` — that touches a non-frozen map, not the registry. **This bit a ~750-file boss mod THREE times** (structure pieces, custom stats, all 9 criteria triggers). **Scan:** `grep -rn 'Registry\.register(BuiltInRegistries\.\|CriteriaTriggers\.register(' src/main/java` and check none run from `commonSetup`/`enqueueWork`.
R14. **🔴 Mixin targeting a vanilla member that changed 1.20→1.21** (mod-load crash at mixin-apply; §H, but only surfaces at load) · **Pattern (two shapes):** (a) an **`@Accessor`** for a vanilla field whose **name/type changed** — `AreaEffectCloud.effects` (`List<MobEffectInstance>` → a `PotionContents potionContents` component), `ZombieVillager.tradeOffers` (`CompoundTag` → `MerchantOffers`), `SynchedEntityData` (`itemsById` `Int2ObjectMap` → `DataItem<?>[]`; the `ReadWriteLock lock` **removed**); (b) an **`@Inject(locals = CAPTURE_FAILHARD)`** whose captured local-variable list no longer matches — `AbstractSkeleton.performRangedAttack` gained a second `ItemStack` local from the `getArrow(projectile, pitch, weapon)` refactor. · **Runtime:** `InvalidAccessorException: No candidates were found matching <field>:<desc>` (accessor), or `InjectionError: LVT … has incompatible changes at opcode N` (local capture) — mixin **APPLY** fails, aborting mod load. **Invisible to javac** (mixins aren't type-checked against the target). · **Fix:** re-point the accessor to the new field name/type (or delete it and use a now-public method — `AreaEffectCloud.setPotionContents(EMPTY)` replaced the `effects.clear()` accessor), and for a local capture, update the callback signature to the new LVT (add/reorder the captured locals; you don't have to *use* the extra ones). Verify the target vanilla method/field in the decompiled 1.21 source before trusting any pre-existing mixin. **Note the dedicated-server split:** only `mixins`+`server` config entries load under `runGameTestServer`; `client` mixins defer to Gate C — audit the common ones first. **Scan:** `grep -rn '@Accessor\|CAPTURE_FAILHARD' src/main/java/**/mixin` and check each target against 1.21 vanilla.
R15. **Datapack `IntProvider`/number-provider dropped its `value` wrapper** (registry-load crash) · **Pattern:** a mod JSON (here `dimension_type/<id>.json` `monster_spawn_light_level`) uses the 1.20 uniform shape `{"type":"minecraft:uniform","value":{"min_inclusive":0,"max_inclusive":7}}`. · **Runtime:** `Failed to parse … Not a number … No key max_inclusive/min_inclusive … Failed to load registries` at `GameTestServer.create` (or dedicated-server datapack load). · **Fix:** 1.21 flattened it — the bounds sit directly on the object: `{"type":"minecraft:uniform","min_inclusive":0,"max_inclusive":7}` (drop the `"value"` nesting). **Scan:** `grep -rln '"type": *"minecraft:uniform"' src/main/resources/data` and check for a nested `"value"`.
R16. **A modded block reusing a vanilla block-entity type isn't added to ALL the right BE types** (place/save crash; found by the persistence GameTest) · **Pattern:** the mod extends `BlockEntityType.SIGN` with its custom-wood sign blocks via a valid-blocks patch, but its custom-wood **hanging** signs (`CeilingHangingSignBlock`/`WallHangingSignBlock`) produce a **`HangingSignBlockEntity`** and were never added to `BlockEntityType.HANGING_SIGN`. A BE whose block isn't in its type's valid set is "invalid". · **Runtime:** `IllegalStateException: Invalid block entity minecraft:hanging_sign // HangingSignBlockEntity state at <pos>, got Block{<modid>:custom_hanging_sign}` — on placement/save, in real gameplay too (not just the test). · **Fix:** add the blocks to **every** BE type they instantiate — a second `addToBlockEntityType(BlockEntityType.HANGING_SIGN, …)` alongside the `SIGN` one. **Found by:** the persistence round-trip GameTest placing every BE block. **Scan:** for each vanilla BE type the mod reuses, confirm all its blocks (regular AND hanging signs, standing AND wall variants) are registered to it.

R17. **🔴 CLIENT mixin whose vanilla target changed 1.20→1.21** (mixin-apply crash at client load — the client cousin of R14; **Gate B can't see it, only Gate C's `launch` phase**) · **Pattern (four shapes, all in the mixin config's `client` array):** (a) **`@Inject` descriptor changed** — the target method's params changed, so the callback's captured params no longer match: `GameRenderer.renderLevel(float,long,PoseStack)` → `renderLevel(DeltaTracker)`; `EntityRenderDispatcher.renderHitbox(...,Entity,float)` gained `float red,green,blue` (hitbox tint) after the partialTick. (b) **`@Shadow` of a removed field** — `Gui.screenWidth`/`screenHeight` are gone. (c) **`@Invoker` of a private method whose params changed** — `JigsawPlacement.addPieces(...)` gained trailing `PoolAliasLookup` + `LiquidSettings` (this one fires on **world-create/worldgen**, i.e. Gate C's `spawn`). (d) **`@Accessor` for a field a whole-class rewrite deleted** — `BufferBuilder.buffer` (`ByteBuffer`) no longer exists (BufferBuilder→MeshData rewrite). · **Runtime:** `InvalidInjectionException: … @Inject … Expected (…) but found (…)` / `InvalidAccessorException: No candidates … <field>` / `InvalidMixinException: @Shadow field … was not located` — mixin **APPLY** fails → client crash on load (a `launch`-phase watchdog kill if it hangs on the fmlearlywindow error). **Invisible to javac AND to `runGameTestServer`** (a dedicated server never loads `client` mixins). · **Fix:** update each to the 1.21 vanilla member — add the new `@Inject` params to the callback (you needn't use them), point the `@Shadow` at the replacement (`Gui` reads size from the `GuiGraphics` arg: `graphics.guiWidth()/guiHeight()`), add the new `@Invoker` params + pass sane defaults at the call site (`PoolAliasLookup.EMPTY`, `LiquidSettings.APPLY_WATERLOGGING`), and **delete a dead accessor** whose feature was already re-done another way (`BufferBuilder.buffer` → the mod now uses `ByteBufferBuilder`). **Scan:** `grep -rn '@Inject\|@Invoker\|@Shadow\|@Accessor' <client-mixin-package>` and verify each target against 1.21 vanilla. (a ~750-file boss mod: 4 of these cleared the `launch`/`spawn` phases.) · **AUGMENT — shape (e): vanilla ADDED a parameter to the target.** `LivingEntityRenderer#setupRotations` gained a trailing **`float scale`** in 1.21, so a 5-param `@Inject` callback no longer matches (`could not find target method`). Same class as (a), but easier to miss because nothing in the mod's own code changed and the compile stays green. **Also re-verify every `ordinal=`/`target=` on the `@At`** when you fix the arity: an added/removed call in the target body shifts the ordinal count (`mulPose(Quaternionf)` ordinal 6 still lands on the sleeping rotation in 1.21 — verified by counting the calls in the decompiled body, not assumed). (a furniture mod built on that framework library: `LivingRendererMixin`)
· **AUGMENT — shape (f): the target's RETURN TYPE changed, and the parameters did not.** 26.2's
`SoundManager.play(SoundInstance)` returns `SoundEngine.PlayResult` where 1.21.1 returned void, so
`@Inject(method = "play(L…/SoundInstance;)V")` binds to nothing:
`Critical injection failure: @Inject annotation on … could not find any targets matching …`. Mixin
resolves by the WHOLE descriptor, so this is a hard apply failure — and it is the one shape a
parameters-only audit reports as all clear (see **X27**). **Both halves of the injection change
with it**: the descriptor in `method` AND the callback type (`CallbackInfo` →
`CallbackInfoReturnable<T>`), so in a §W tree no rename row can express it and the mixin class
itself becomes a per-target overlay. Keep the DECISION in a shared helper and let the overlay be
thin wiring, or the port carries two copies of the logic. · **Cancelling then has to answer a
question it never had to before.** `cir.setReturnValue(...)` needs a value, so read who consumes
it: here exactly one caller does (`MusicManager.startPlaying`, which shows a "now playing" toast on
`STARTED`), and the mod really does start a replacement sound — so `STARTED` is the honest answer
and `NOT_STARTED` would have silently suppressed that toast for every track played in space.
R18. **Forge's `EntityType.Builder.setCustomClientFactory` was removed** (client renders a `ClassCastException` for a client-substituted entity) · **Pattern:** the mod registers an entity with a **client-only subclass** for rendering — in Forge, `EntityType.Builder.of(Server::new, cat).setCustomClientFactory((spawnPkt, level) -> new ClientVariant(type, level))`, and its renderer casts to `ClientVariant`. 1.21/NeoForge **removed `setCustomClientFactory`**, so the single `EntityType.EntityFactory` builds the base class on both sides; the renderer's cast then fails. · **Runtime:** `ClassCastException: Can only render instances of <ClientVariant>` (`Rendering entity in world`) the first time the entity renders — here a debris-cluster entity (a boss-thrown visual) whose renderer requires its client-only subclass. · **Fix:** make the entity factory **dist-aware** and isolate the client class so it never loads on a dedicated server: `Builder.of((type, level) -> level.isClientSide ? DistExecutor.unsafeCallWhenOn(Dist.CLIENT, () -> () -> new ClientVariant(type, level)) : new Base(type, level), cat)`. The client-class reference must live **only inside the dist-guarded supplier** (the same nested `() -> () ->` idiom the mod's packet handlers use), or class verification loads it on the server → `NoClassDefFoundError`. **Re-run Gate B (`runGameTestServer`) after this** to confirm the dedicated server still loads (the definitive dist-safety check). · **Why the earlier gates missed it:** only fires when the substituted entity actually **renders** — Gate C's `battle` (mobs spawning the visual mid-combat).
R19. **A `LayeredDraw.Layer` HUD overlay that relied on implicit blend renders OPAQUE in 1.21** (full-screen overlay blanks the HUD) · **Pattern:** a HUD overlay ported from Forge's `IGuiOverlay` to 1.21's `LayeredDraw.Layer`/`RegisterGuiLayersEvent` does `graphics.setColor(1,1,1,alpha); graphics.blit(fullScreenTex, …)` (or `graphics.fill(...)`) with an alpha but **no `RenderSystem.enableBlend()`**. In the old `IGuiOverlay` HUD context blend was already enabled, so the same code (faithful migration!) blended fine; the 1.21 `LayeredDraw` pipeline does **not** enable blend for you, so `GuiGraphics.blit` ignores the alpha and draws **opaque**. · **Runtime:** a translucent effect (a tractor-beam vignette, a screen tint) fills the whole screen solid, hiding the game + hotbar. No crash — pure visual. · **Fix:** wrap the draw in explicit blend: `RenderSystem.enableBlend(); RenderSystem.defaultBlendFunc(); … blit/fill …; RenderSystem.disableBlend();`. (Watch for **inconsistency**: a ~750-file boss mod's sibling `renderSolidOverlay` *was* given `enableBlend` during migration but `renderTextureOverlay` was not.) **Scan:** `grep -rn 'setColor(.*alpha\|\.blit(\|\.fill(' <overlay classes>` and confirm each alpha-blended overlay draw has an `enableBlend` around it. · **Why the earlier gates missed it:** only visible when the overlay actually shows (an in-game effect triggers) — a human at the screen, not a headless server.
R20. **Custom core-shader `VertexBuffer` / `drawWithShader` render path draws nothing in 1.21** (a mod's own cached/instanced GPU render is invisible) · **Pattern:** the mod does its own GPU-buffered rendering — builds a `VertexBuffer` and draws it with a **custom core shader** via `buffer.drawWithShader(stack.last().pose(), RenderSystem.getProjectionMatrix(), RenderSystem.getShader())` (e.g. an instanced "render this huge mesh once, redraw the buffer" optimization). The code migrates faithfully (the call is unchanged from 1.20), the shader compiles, and the buffer builds without error — but **nothing draws** while a normal `MultiBufferSource` path of the same geometry works. It's a 1.21 render-pipeline behavior change around `drawWithShader`/core-shader uniforms/the pose-vs-camera-modelview split, **not** a migration typo. · **Runtime:** the buffered geometry is invisible; no crash, no log error (shader "could not find uniform" warnings for declared-but-unused uniforms are a red herring). · **Fix (open — see the repo issue):** likely the modelview passed to `drawWithShader` needs the camera view composed in (`new Matrix4f(RenderSystem.getModelViewMatrix()).mul(stack.last().pose())`), since 1.21's entity-render `PoseStack` is camera-relative-position only and `drawWithShader` applies solely the matrix you pass (the `MultiBufferSource` path applies `getModelViewMatrix()` automatically). **Interim workaround:** if the mod gates this behind a config (the boss mod's `vertexBufferRendering`), flip it off to fall back to the direct `MultiBufferSource` path (renders correctly; loses the caching perf). · **Why the earlier gates missed it:** custom shader + GPU buffer only render on a real client with the entity in view — Gate C `spawn`/`battle`, and only visible to a human.
R21. **`AbstractArrow` ctor with `ItemStack.EMPTY` as `firedFromWeapon`** (`IllegalArgumentException: Invalid weapon firing an arrow`) · **Pattern:** porting a custom arrow's constructors to 1.21's new `AbstractArrow(EntityType, LivingEntity/double×3, Level, ItemStack pickupItem, ItemStack firedFromWeapon)` signatures (catalog §I), you fill the two new trailing `ItemStack` args with `ItemStack.EMPTY, ItemStack.EMPTY` — compiles clean, and the base `(EntityType,Level)` ctor path (spawn-egg/`type.spawn`) never touches them, so **Gate B stays green**. · **Runtime:** the first time the arrow is *fired by a mob's ranged attack* (`XEntity.shoot(...)` → `new XEntity(type, shooter, level, EMPTY, EMPTY)`), 1.21's `AbstractArrow` ctor runs `if (firedFromWeapon != null && firedFromWeapon.isEmpty()) throw new IllegalArgumentException("Invalid weapon firing an arrow")` — it accepts **null** (no weapon) or a **real** weapon, but not a non-null *empty* stack. Crashes the server thread ticking the attacker (MCreator "shoot projectile" procedures hit this en masse). · **Fix:** pass **`null`** for `firedFromWeapon` (the `@Nullable` "no weapon" case): `super(type, shooter, level, <pickup>, null)`. **Scan:** `grep -rn 'ItemStack.EMPTY, .*ItemStack.EMPTY)' src/main/java` in `extends AbstractArrow` files. · **Why the earlier gates missed it:** only fires on the *ranged-attack* path (a mob actually shooting), not on plain spawn — Gate C's `spawn`/`battle`. · **⚠️ AUGMENT — the `pickupItem` must ALSO be non-empty (a SECOND, save-time crash the original note got wrong):** leaving `pickupItem = ItemStack.EMPTY` (and/or `getDefaultPickupItem()` returning `ItemStack.EMPTY`) compiles + spawns + fights fine, but **crashes on SAVE** — `AbstractArrow.addAdditionalSaveData` does `pickupItemStack.save(provider)`, and `ItemStack.EMPTY.save()` throws `IllegalStateException: Cannot encode empty ItemStack`. Signature: `ReportedException: Saving entity NBT` → `EntityStorage: An Entity type <id> has thrown an exception trying to write state. It will not persist.` (a class-**R12** save crash, but specific to custom arrows). Fires on any world/chunk save while a projectile is airborne. **Fix:** give a non-empty pickup — `new ItemStack(Items.ARROW)` in both the ctor pickup arg **and** `getDefaultPickupItem()`; pickup mode is `DISALLOWED` by default so players never actually pick it up (verify no `Pickup.ALLOWED`). Caught by the **persistence round-trip GameTest** or a `battle` stress that runs long enough to hit an autosave/chunk-unload. **Scan:** `grep -rnE 'getDefaultPickupItem\(\).*ItemStack.EMPTY|ItemStack.EMPTY, null\)' src/main/java` in `extends AbstractArrow` files. (a ~380-file MCreator mob mod: 25 projectile entities.)

R22. **A `BuildCreativeModeTabContentsEvent` handler that mutates the entry sets wrongly** (crash on creative-inventory open) · **Pattern (from an integration that REMOVES another mod's item from a tab):** `event.getParentEntries().removeIf(...)` / `getSearchEntries().removeIf(...)` — the natural-looking way to drop an entry. · **Runtime:** those `ObjectSortedSet<ItemStack>` are **read-only for structural edits** → `UnsupportedOperationException` → `ModLoadingException: <mod> encountered an error while dispatching BuildCreativeModeTabContentsEvent` the moment the creative inventory is opened (`CreativeModeInventoryScreen.<init>` → `tryRebuildTabContents`). Compiles clean; **also NOT caught by a headless GameTest** (creative tabs build client-side) **nor by a gauntlet that renders items via a custom screen** (only the REAL creative inventory triggers the event). · **Fix:** collect the target stacks by iterating the entry sets (reading is fine), then remove each via the event's own API: `event.remove(stack, CreativeModeTab.TabVisibility.PARENT_AND_SEARCH_TABS)`. (To ADD, use `event.accept(...)`.) · **Gate:** the smoke-harness gauntlet now has an **OPEN_CREATIVE** step that constructs the real `CreativeModeInventoryScreen`, firing every mod's tab handlers — that's what catches this + any tab-build/tab-render crash. (the builder mod's suppression of another mod's boss.) · **⚠️ AUGMENT — the MIGRATION harness needed it too:** `templates/neoforge-mod/test-templates/ClientBootSmokeTest.java.example` (and every already-migrated mod copied from it) had **no** OPEN_CREATIVE step, so a whole green Gate C could still ship an R22 crash. The step is now in the template + both Fabric gear mods' `mods/<modid>` harnesses: `mc.setScreen(new CreativeModeInventoryScreen(player, player.connection.enabledFeatures(), true))` after forcing the *server* player to `GameType.CREATIVE` (the screen only builds tab contents for a creative player), held open for the step's duration via `stepWantsScreen`. **Back-port it to any mod whose harness predates this.**

R23. **`deployToMods` overwriting the jar in place while Minecraft is RUNNING** (corrupts the live session) · **Pattern:** the obvious deploy task — `copy { from("build/libs/<jar>"); into(modsDir) }` — writes the new jar directly over the old one. · **Runtime:** a running game holds its mod jars **open** and reads entries **lazily**, so overwriting mid-session makes it read a half-written zip: `java.util.zip.ZipException`, `ClassNotFoundException`/`NoClassDefFoundError` for classes that loaded fine a minute earlier, missing/purple textures. The symptoms look like a *code* bug, which is what makes it expensive — you go hunting in the mod instead of realising the jar under the running game changed. Caught by no gate (it's a deploy-time, game-is-running hazard). · **Fix:** deploy **atomically** — `Files.copy` to a temp file *in the target dir* (same filesystem, so the move can be atomic), then `Files.move(..., ATOMIC_MOVE, REPLACE_EXISTING)` with a fallback to a plain `move` on `AtomicMoveNotSupportedException`, deleting the temp in a `finally`. The game then sees either the whole old jar or the whole new one — never a partial file. Also guard: mods dir missing/not-a-directory → warn + skip; built jar absent → fail loudly. **Both templates ship this** (`templates/neoforge-mod`, `templates/smoke-harness`), so newly-migrated mods inherit it; already-migrated `mods/*/build.gradle` still carry the plain `copy`. **Still restart Minecraft** to load a new build — atomicity prevents corruption, it doesn't hot-swap.

R24. **A registry `Holder` borrowed from the WRONG SIDE's registry copy** (player is DISCONNECTED mid-game the moment they take the item out of the creative menu) · **Pattern:** a helper that reaches for "whatever registries are available" from code that only has an `ItemStack` — the 1.21 datapack-registry rework forces this on any port that stamps enchantments/potions/trim patterns onto a stack, above all in `Item#getDefaultInstance()` (which is what fills creative tabs). a Fabric weapons mod's read `if (ServerLifecycleHooks.getCurrentServer() != null) return server.registryAccess();` — reasonable-looking, and wrong on a singleplayer **render thread**. · **Runtime:** in singleplayer the client and the integrated server hold **two separate copies** of every datapack registry, so a `Holder.Reference` from one is an unknown object to the other. The stack builds, renders, and tooltips **perfectly** — the failure is deferred to the moment the client sends it: taking it out of the creative menu encodes `serverbound/set_creative_mode_slot` against the CLIENT registry → `EncoderException: Failed to encode packet` / `IllegalArgumentException: Can't find id for 'Reference{…}'` → Netty kills the connection and the player is dumped to the menu. **No crash report is written**, and the log line names the *packet*, not the mod — it reads like a network problem. Hit that weapons mod on **111 of its 152 items** (vanilla `minecraft:fire_aspect` holders included), in the field, after a fully green Gate A+B+C. · **Fix:** dispatch on the **calling thread**, not on "a server exists": `if (server != null && server.isSameThread()) return server.registryAccess();` then fall through to `Minecraft.getInstance().level.registryAccess()` on the client dist, and only then back to the server (off-thread on a dedicated server). Never cache a provider at load — that hands out stale holders across a world reload. · **Gate:** the gauntlet's **`CREATIVE_PICKUP`** step (both templates + the weapons mod's `mods/<modid>`) runs the real `ItemStack.OPTIONAL_STREAM_CODEC` over every item's `getDefaultInstance()` against `mc.level.registryAccess()`. **R22's OPEN_CREATIVE is not enough** — it proves the tabs BUILD; this proves they can be SENT. Verified as a true A/B (pre-fix `111 of 152 items`; post-fix `encoded 152`). · **Generalisation: "the client can BUILD it" ≠ "the client can SEND it."** Any component holding a registry object is a candidate; the bug is invisible to Gates A/B, which have no client registry to disagree with.

R25. **🔴 26.x requires `Properties.setId(...)` on every block and item — registration NPEs, and the
compile is clean** · **Pattern:** ordinary `DeferredRegister` content —
`BLOCKS.register("x", () -> new Block(BlockBehaviour.Properties.of()...))` · **Runtime:**
`NullPointerException: Block id not set` from `RegisterEvent`, followed by a cascade of
`NullPointerException: Trying to access unbound value: ResourceKey[minecraft:block / <modid>:<name>]`
for everything registered after it — the mod does not load · **Fix:** `Properties` carries a
`ResourceKey` now and it must be set: `.setId(ResourceKey.create(Registries.BLOCK, id))`, and
`Item.Properties` the same. This is **§M13 read the other way round** — that entry records dropping
`setId` as the 1.21.4 → 1.21.1 *downport* step, so going up it is mandatory. · **In a §W tree it is a
compat pair, not a rename rule**, because the argument has to be constructed per site from that
site's own registry name: a helper that stamps the id on the new target and returns the Properties
unchanged on the old one. (the builder mod: 84 block + 131 item sites over 26 files.)
· ⚠ **AUGMENT — MCreator builds the Properties where the registry name is NOT in scope, and
guessing it from the class name is wrong.** The builder mod's sites all sit in a registration file
with the name beside them; an MCreator mod's do not. Each item is
`REGISTRY.register("mbb", () -> new MbbItem())`, and `MbbItem`'s own no-argument constructor calls
`super(new Properties().durability(100))` — a file that has never heard of the string `"mbb"`.
Deriving it from the class name works until `Widget2Item` registers as `widget_2`. · **Recover the
map from the registration file and pass the name explicitly**: one regex over
`register\(\s*"([a-z0-9_]+)"\s*,\s*\(\)\s*->\s*new\s+(\w+)\(` gives class → name for all 29 here,
1:1 with no duplicates, and the rewrite is then `super(TbmProps.item("mbb", new Properties())…)`.
The eggs are the other shape — their Properties IS built at the register site — and want the same
helper with the name taken from the adjacent literal. · **Two of 49 needed a second pass** because
the decompiler had put the name on its own line above the lambda: §X10's rule again, `\s*` at every
link of a chain, and the count (47 of 49) is the only thing that said so. · **Why the
error count cannot warn you:** the old code is still perfectly legal Java on the new version; the
requirement is a runtime invariant, not a signature. It is invisible to Gate A too unless the test
tree actually compiles — see X14.
⚠ *It is required from **1.21.2**, and on 1.21.1↔1.21.4 NeoForge's `DeferredRegister.Items.registerItem`/`Blocks.registerBlock` (identical on both, stamping the id on 21.4) make it shared source with no pair (minor-version-deltas U10).*

R26. **🔴 NeoForge posts `EntityJoinLevelEvent` from INSIDE `PersistentEntitySectionManager.addEntity`
— so a listener that registers the joining entity anywhere vanilla is about to register it crashes the
world load.** · **Pattern:** a mod whose API "revives" or re-inserts an entity into the level's
containers (section storage, tick list, `ChunkMap` tracking) is called from an `EntityJoinLevelEvent`
listener on that same entity — a weapon mod's test entity calls the core API's `setInvulnerable(te, true)` from
its own join hook, and a core-API mod's revive ends in `onTrackingStart` · **Runtime:**
`IllegalStateException: Entity is already tracked!` from `ChunkMap.addEntity`, on WORLD LOAD whenever a
saved instance is in range (and on the first spawn). The event fires before `addEntityWithoutEvent` has
put the entity in a section, so the listener's work is done first and vanilla's is then a duplicate ·
**Fix:** treat an entity as JOINING for the whole duration of `addEntity` (a `@Inject` at HEAD marks it,
one at RETURN clears it, a weak set holds it) and make the revive path a no-op for a joining entity —
vanilla is about to do exactly that work. Fix it in the library, not in each caller: the listener is
reasonable code, and the next dependant will write the same one. · ⚠ **The same crash has a SECOND
door, and the join guard does not close it:** vanilla tracks an entity only while its section is
ACCESSIBLE, and calls `startTracking` itself the moment `updateChunkStatus` makes it so. A revive that
re-tracks whatever is missing from `chunkMap.entityMap` also re-tracks an entity vanilla has
DELIBERATELY left untracked (its chunk is not loaded to entity level), so the next chunk-status change
throws the same `already tracked!` from `ChunkMap.onFullChunkStatusChange`. Found by Gate C's
`gauntlet` (the player moving around loads and unloads chunks), after Gate B and the join fix were green.
Guard with vanilla's own rule from `addEntityWithoutEvent`: re-track only if the entity's section
`getStatus().isAccessible()` (or `isAlwaysTicking()`). · ⚠ **And a THIRD door, at shutdown:** `ServerLevel.close()` →
`saveAll()` → `processPendingLoads()` fires `EntityJoinLevelEvent` too, and a listener that adds a
force-load ticket there (`forceChunk(add=true)` does a synchronous `getChunk`) deadlocks the server
thread in `managedBlock`: the client never exits and no crash report is written. Guard ticket-adding on
`server.isRunning()`. Found by `jstack` on a client left alive 45 min after its Gate C phase ended. · **Why the cheap gates miss it:** the
Gate-B spawn path runs it once, and it only crashes when the mod that calls the API is loaded together
with the mod that owns it. Only the DEPENDENT's Gate B had both.


R27. **🔴 An OPTIONAL dependency that is required in practice: a listener class whose method TYPES name
the optional mod** · **Pattern:** `neoforge.mods.toml` declares a mod `type = "optional"`, the
registration path is guarded with `ModList.get().isLoaded(...)`, and yet an `@EventBusSubscriber` class
has a helper method (or a lambda — javac gives it a synthetic method) whose parameter or return type comes
from that mod: `private static void setupEnchants(LivingEntity e, …, MobEnchantCapability cap)`.
· **Runtime:** with the optional mod absent, mod construction fails:
`Failed to register automatic subscribers` ← `NoClassDefFoundError: <optional pkg>/<Class>` ←
`Class.getDeclaredMethods` ← `AutomaticEventSubscriber.inject`. NeoForge scans every method of a
subscriber class, and resolving a method's descriptor loads every type in it, guard or no guard. The
same mod also had a mixin into a vanilla `Entity` method calling the optional mod's registry class
unguarded, which crashed on the first entity tick. All three were in the author's own 1.20 code; the
port only made them visible. · **Fix:** move the method out of the subscriber class into a plain helper
class (calls are rewritten to `Helper.method(...)`; the helper is only loaded when called, and every call
is already behind the guard), or register the listener from the guarded block instead of by annotation;
guard a mixin body with the same `isLoaded` check the author used elsewhere. **Codemod:**
`tools/fix-optional-listeners.py` does the move for static helpers and synthetic lambdas' enclosing
methods, and refuses what it cannot move safely. · **Detection, no game:** `tools/optional-dep-scan.py`
reads the compiled classes and fails on this shape (and reports mixins that reach an optional mod),
and fork CI's `minimal` environment runs it before Gate B, then boots without the optional mods. On the
released port it named the three crashes the minimal Gate B then hit one at a time.

R28. **🔴 `StreamCodec.unit(new X())` on a field-less payload CLASS rejects every send — the player is
disconnected, and no server-side gate can see it.** · **Pattern:** the natural port of a Forge "signal" packet with
no fields: `public class SwitchHand implements CustomPacketPayload { static final StreamCodec<…, SwitchHand>
STREAM_CODEC = StreamCodec.unit(new SwitchHand()); }`, sent as `PacketDistributor.sendToServer(new SwitchHand())`.
· **Runtime (client, at send):** `EncoderException: Failed to encode packet 'serverbound/minecraft:custom_payload'`
← `IllegalStateException: Can't encode 'X@f05c125', expected 'X@675c6cdf'`, and the client drops out of the world.
`StreamCodec.unit` writes nothing and refuses any value not `equals()` to the one it was built with; a plain class
has identity equality, so the codec accepts only that one object. · **Fix:** `public record X() implements
CustomPacketPayload` — a record with no components makes every instance equal, a one-word diff with every call site
unchanged. (Or one shared `INSTANCE`, used to build the codec and at every send.) · **Why the gates missed it:**
it compiles, Gate B's server never sends a client packet, and Gate C sends it only if something triggers the
action (here an off-hand attack while dual-wielding). Caught by a player, after green CI on the release.
**Control:** `tools/audit-unit-codecs.py` (a ci-gates row; matches the call across line breaks, which a plain
grep did not) — exit 1 on a non-record, non-enum class with no `equals`.

R29. **🔴 A mixin class that EXTENDS a vanilla parent inherits that parent's covariant bridges — and on 1.21 two
of them can collide on the target, so mixin apply fails at startup.** · **Pattern:** `@Mixin(MushroomCow.class)
abstract class X extends Cow` — the usual way to reach `goalSelector` and the parent's methods from a mixin. On
1.21.1 `Cow` and `MushroomCow` each declare `getBreedOffspring(ServerLevel, AgeableMob)` with their own covariant
return, so each has a synthetic bridge with the same descriptor. · **Runtime:** `InvalidMixinException:
Conflicting synthetic bridge target method descriptor in synthetic bridge method getBreedOffspring(…)… Existing:
(…)MushroomCow; Incoming: (…)Cow` — the mod refuses to load. Compiles clean. · **Fix:** extend the nearest ancestor
that has what the mixin uses and no override of the target's covariant methods (`PathfinderMob` for goal
selectors), keeping the constructor's `EntityType<? extends …>` in step. · **Why it hid:** it surfaced in CI's
Gate B and not in two local Gate B runs of the same commit and kit, i.e. it depends on when the target class is
first loaded. A green local Gate B is not proof a mixin applies; the CI run is the second sample. Found on a
release-aligned branch, where `port-derive` had listed the released mixin again (see §W19).

**Non-fatal runtime issues (log errors / wrong visuals, not a crash — fix during the boot loop, they won't fail the gate):**
- **Forge biome modifier not renamespaced/retyped** (mob silently stops spawning naturally) · **Pattern:** `data/<ns>/forge/biome_modifier/*.json` with `"type": "forge:add_spawns"` (also `add_features`, `remove_spawns`) · **Log:** usually silent (a datapack registry the mod's own code doesn't read) → the entity just never spawns in its biomes. · **Fix:** move the file to `data/<ns>/**neoforge**/biome_modifier/` and rename the type `forge:add_spawns` → `neoforge:add_spawns` (the `{biomes, spawners:{type,weight,minCount,maxCount}}` body is unchanged). **Scan:** `grep -rln '"forge:add_spawns"\|/forge/biome_modifier/' src/main/resources` and `find src/main/resources/data/*/forge/biome_modifier`. (a single-mob MCreator mod (~15 files): deep_dark + dark_forest spawns.)
- **`forge:` model-loader id not renamespaced** · **Log:** `Model loader 'forge:separate_transforms' not found. Registered loaders: neoforge:separate_transforms, …` → the item bakes as the missing-model (black/purple), no crash. · **Fix:** renamespace the `"loader"` id in the item-model JSONs, `forge:<x>` → `neoforge:<x>` (`separate_transforms`, `composite`, `obj`, `item_layers`, …). **Scan:** `grep -rln '"loader": *"forge:' src/main/resources`. (Was 8 models here: crossbow variants, a lance, a hammer, a scimitar.)
- **Optional cross-mod data spamming `Unknown registry key` every datapack load** · **Log:** `Failed to parse data json for <id> due to: Unknown registry key in ResourceKey[minecraft:root / minecraft:entity_type]: <othermod>:<entity>` — a data entry references an entity/registry id from a mod that isn't installed; the whole codec decode fails (`byNameCodec()` throws) and the entry is dropped. · **Fix:** if the data format declares a `"requiredMods"` array (or similar), **gate before decode**: in the reload-listener's `apply()`, skip any entry whose `requiredMods` aren't all `ModList.get().isLoaded(...)` — `byNameCodec()` throws long before the codec ever reaches that field, so a post-decode check is too late. Keeps the optional integration working when the mod IS present, silent when it isn't.
- **Custom armor renders purple — texture not moved to the `ArmorMaterial.Layer` path** · **Log:** `Failed to load texture: <modid>:textures/models/armor/<id>_layer_1.png` (and `_layer_2`) → the worn armor renders as the missing-texture magenta. · **Cause:** old Forge armor used a per-item `getArmorTexture()` override (+ a custom `AdvancedArmourLayer`) pointing at an arbitrary path like `textures/armour/<name>.png`. 1.21/NeoForge replaced that with `new ArmorMaterial.Layer(ResourceLocation(modid, id))`, which resolves the texture to a FIXED path: `textures/models/armor/<id>_layer_1.png` (helmet/chest/boots) and `_layer_2.png` (leggings). The migration ports the material + even the custom armor MODEL (`IClientItemExtensions.getHumanoidArmorModel` still exists in 1.21) but silently drops the texture, since the getArmorTexture override is gone. · **Fix:** put the original armor texture(s) at `textures/models/armor/<id>_layer_1.png` **and** `_layer_2.png` (same file if the custom model uses one texture for all slots — its UVs map into whatever texture is bound, so a non-64×32 custom texture is fine here). **If binaries are gitignored** (this repo keeps copyrighted mod PNGs out of git — see `.gitignore`), don't commit them: **derive them at build** via a `processResources` `from/rename` block that copies the re-extracted `textures/armour/<name>.png` → the `_layer_{1,2}.png` paths (model: `mods/<modid>/build.gradle`), so a fresh clone regenerates them after re-extraction. **Scan:** `grep -rn 'ArmorMaterial.Layer' src/main/java` for each material's id, then confirm `textures/models/armor/<id>_layer_{1,2}.png` exist. · **Why the earlier gates missed it:** only fires when the armor is actually *worn and rendered* — the `gauntlet` mode's EQUIP_ARMOR + 3rd-person step caught it.
- **`#forge:` data tags → `#c:` (NeoForge common) convention** · **Log:** `Couldn't load tag <id> as it is missing following references: #forge:is_snowy` (biome), `#forge:cobblestone` (block), etc. — the tag entry silently drops, so whatever it drove is **empty** (a structure won't spawn in that biome; a block-list the mod checks is empty; a block-conversion rule never matches). · **Fix:** NeoForge dropped the `forge:` tag namespace for the `c:` convention. Rewrite every `#forge:X` in `src/main/resources/data/**` — biome: `forge:is_desert/is_snowy` → `c:is_desert/is_snowy`; block: `forge:cobblestone`→`c:cobblestones`, `glass`→`c:glass_blocks`, `sand`→`c:sands`, `sandstone`→`c:sandstone/blocks`, `stone`→`c:stones`, `gravel`→`c:gravels` (**several are renamed/pluralized**, verify each against `net.neoforged.neoforge.common.Tags`), and the many namespace-only ones (`ores`, `chests`, `barrels`, `bookshelves`, `fences`, `glass_panes`). **Scan:** `grep -rhoE '#?forge:[a-z_/]+' src/main/resources/data | sort -u`. · **Why the gates missed it:** it's a datapack-load WARN, not a crash — only shows as wrong gameplay (missing structures / inert block rules) a human notices.
- **Custom core shader stuck on GLSL `#version 110`** · **Log:** `ChainedJsonException: Couldn't compile … program : ERROR: … version '110' is not supported … 'varying' : syntax error` → the shader fails to compile and (if it's on a load path) breaks client startup. · **Fix:** modernize the `.vsh`/`.fsh` to core-profile GLSL 150 (what 1.21 uses): `#version 110` → `#version 150`; `varying` → `in` (fragment) / `out` (vertex); `texture2D(...)` → `texture(...)`; `gl_FragColor` → declare `out vec4 fragColor` and assign it; rename any local shadowing a GLSL builtin. (a ~750-file boss mod: `program/example_distortion.fsh`; the `core/*` shaders were already 150.) **Scan:** `grep -rln '#version 1[012]0\|varying \|gl_FragColor\|texture2D' src/main/resources/assets/**/shaders`.
- **Item/block model face `"forge_data"` key** · **Log:** `JsonParseException: forge_data should be replaced by neoforge_data` → the model fails to load (purple missing-model). · **Fix:** rename the `"forge_data"` element-face key to `"neoforge_data"` in the model JSONs. **Scan:** `grep -rln '"forge_data"' src/main/resources`.

R27. **Gate C `spawn` passes with NOTHING spawned when every mod entity is `MobCategory.MISC`** · **Pattern:** a library/utility mod whose only entities are orbs, markers, projectiles or visual effects (all `MobCategory.MISC`) · **Symptom:** `<MODID>_BOOT_TEST: spawned 0 creature type(s): []` followed by `PASS — created world, spawned … world ticked 200 ticks with no crash` — green, while every rewritten entity renderer (VertexConsumer chain, GeckoLib colour packing) went unexercised · **Fix:** read the `spawned N` line, not just PASS; when the namespace has no non-MISC types, let `modCreatureTypes()` fall back to the namespace's MISC entities (the shipped `ClientBootSmokeTest` template now does this, and FAILS a mod with entities that spawned none). A green phase that exercised nothing is the §S5 "a count is not a comparison" trap in the client gate.

### Running the runtime gate
1. Add a `gameTestServer` run to `build.gradle`'s `runs {}` with `systemProperty 'neoforge.enabledGameTestNamespaces', project.mod_id`.
2. Ship an empty test structure at `src/main/resources/data/<modid>/structure/<name>.nbt` (gzipped structure NBT: `size`, `palette`, `blocks` floor layer, empty `entities`, `DataVersion` 3955). NeoForge 21.1 has no `@EmptyTemplate`; the framework throws `Missing test structure` without a real `.nbt`.
3. Put `@GameTest`s in the **main** source set (`src/main/java/.../test/`, `@GameTestHolder(modid)` + `@PrefixGameTestTemplate(false)`) — they're scanned at runtime, not the JUnit test set. Spawn a mob (`helper.spawnWithNoFreeWill`), resolve+use a spawn egg (`SpawnEggItem.getType`), and drive an item (`makeMockPlayer` → `player.attack`) to exercise ctor+attributes+goals, the egg→type link, and the combat path.
4. `./gradlew runGameTestServer` → boots a dedicated server, runs the tests, exits. Iterate: each load crash points at the next §R pattern. "All N required tests passed :)" = the mod actually loads and those paths run.
5. **Client world-driving boot loop** (catches R6–R21-class client crashes a headless server can't): needs a display for OpenGL, but **on macOS the agent launches it itself** with `open mods/<modid>/tools/run-gatec.command` (LaunchServices → GUI session, no consent prompt; NOT `osascript`, whose Automation dialog blocks). Headless/no-GUI: the human runs `mods/<modid>/tools/client-validate.sh` from the mod dir (or `xvfb-run …` on Linux CI). Either entrypoint chains **four modes** (ascending intensity; each a superset gate) via the per-mod `client-boot-loop.sh`, each launching `runClient -Pboottest -Ptestmode=MODE` to drive `ClientBootSmokeTest`, a `ClientTickEvent.Post` state machine:
   - **`launch`** — reach the title screen and stop. Validates the whole client-LOAD surface (mixins, renderer/particle/layer registration, `FMLClientSetupEvent`, model bake).
   - **`spawn`** (default) — `launch` + **create a fresh world** (`mc.createWorldOpenFlows().createFreshLevel(name, LevelSettings(CREATIVE, difficulty=NORMAL so hostile mobs persist), WorldOptions.defaultWithRandomSeed(), WorldPresets::createNormalWorldDimensions, …)`) and **spawn one of every `<modid>:*` non-`MISC` entity** in a grid around the player (on the server thread via `server.execute`), tick ~10s. Validates each entity's ctor + AI goals + client renderer/model.
   - **`battle`** — the **combat stress test**: N of every creature (`-Pbattlecount`, default 8) split into two teams, spawned in a tight ring **around the player**, re-targeted onto the nearest enemy-team mob **every tick** (`mob.setTarget` + `setLastHurtByMob`; `HurtByTargetGoal` sustains it) so they actually fight; tick ~30s (`-Pbattleticks`/`BATTLE_SECONDS` to lengthen for watching). Player is Creative + `setInvulnerable(true)` + Resistance/Fire-Resistance (hostiles ignore a Creative player, so they fight each other). Stresses high entity counts, combat, AoE attacks, particles and death sequences **inside the camera frustum** — the paths a lone idle mob never exercises. Found R8/R9/R10.
   - **`gauntlet`** — the **item/UI stress test** (the *client* axis the mob modes never touch — where a human opening the inventory found R11). A fixed-order step machine (`ClientBootSmokeTest.GauntletStep`) that, in one world, runs each once: **build every item's tooltip**; **render EVERY item's model + `ItemProperties` overrides + decorations** (a throwaway `AllItemsScreen` draws all items each frame — not just a 36-slot screenful); **open the mod's custom GUIs** (a brewing-style GUI, then a mob-inspection GUI via a transient client-side menu+entity); **wear every armor piece in 3rd-person** so both attribute modifiers AND render layers run; **place every block** (block-entity tick + render); **`use()` every item** (right-click behaviour path); and **apply every mob effect** to the player + a mob. Each step is self-contained and holds a few seconds so deferred render/tick paths run. This is the axis where render-time (`ItemProperties`), tooltip, GUI, block-entity, item-use, and effect-tick crashes live — invisible to both a headless server and the mob-combat modes. **Coverage note:** render is now *exhaustive* (every item, not a screenful) after a first pass only rendered the 36 that fit in the inventory.

   All modes print `<MODID>_BOOT_TEST: PASS` and `mc.stop()` on success; any crash before PASS = a real runtime issue. Each phase writes a crash digest to the per-mod signal dir `/tmp/<modid>-clientloop/crash.log` (stamped with the phase), blocks on `crash.ready`, and relaunches when a fixer touches `fix.done`; `client-validate.sh` advances to the next phase on each PASS and touches `all-done` when all four pass. **Two axes of crash surface: `spawn`/`battle` stress server/simulation (entity ctor, AI, combat); `gauntlet` stresses client/UI + items (render, tooltips, use, effects). Run all four — that's what the one command does.**

---

## N. GeckoLib 3→4 + 1.20.5 item/effect + MCreator raid deltas (an MCreator + GeckoLib mob mod port)
> **Axis:** mixed — GeckoLib-lib-version + version-family (1.20.5). Surfaced porting a 270-class MCreator + GeckoLib mob mod (~220 files, Forge 1.20.1). All confirmed against NeoForge 21.1.228 / GeckoLib 4.8.4.
111. **GeckoLib 3 → 4 package reorg** · **Pattern:** `import software.bernie.geckolib.core.animation.*` / `core.object.PlayState` / `core.animatable.instance.*` / `core.animatable.model.CoreGeoBone` · **Error:** `package software.bernie.geckolib.core.* does not exist` · **Fix:** `core.animation.*`→`animation.*`; `core.object.PlayState`→`animation.PlayState`; `core.animatable.instance.*`→`animatable.instance.*`; `core.animatable.model.CoreGeoBone`→`cache.object.GeoBone` (+ rename the type `CoreGeoBone`→`GeoBone`). `cache.object.BakedGeoModel`, `model.data.EntityModelData`, `constant.DataTickets`, `util.GeckoLibUtil` are unchanged.
112. **GeckoLib 4 `reRender`/`preRender`/`render` color: 4 floats → packed int** · **Pattern:** `renderer.reRender(model, pose, buf, animatable, type, vc, partialTick, light, overlay, 1.0F, 1.0F, 1.0F, 1.0F)`; overrides `preRender(..., float red, float green, float blue, float alpha)` + `super.preRender(..., red, green, blue, alpha)` · **Error:** `method reRender/preRender … cannot be applied … required: …,int` · **Fix:** the trailing `float red,green,blue,alpha` collapsed to one `int` colour (same as vanilla §L#104). Signatures `, int packedColor)`; passthrough calls `, packedColor)`; literal white `1.0F×4` → `0xFFFFFFFF`. ⚠️ **multi-line signatures**: the 4 floats are often on separate lines — a single-line regex desyncs (renames the call but not the sig → "variable packedColor" undeclared). Use a slurped (`-0777`) regex.
113. **`Entity.setMaxUpStep(float)` removed → `Attributes.STEP_HEIGHT`** · **Pattern:** `this.setMaxUpStep(0.6F)` in an entity ctor · **Error:** `cannot find symbol: method setMaxUpStep` · **Fix:** step height is an attribute since 1.20.5 (present in every LivingEntity's default supplier): `this.getAttribute(Attributes.STEP_HEIGHT).setBaseValue(0.6D)`.
114. **`MobEffect` override signatures (1.20.5)** · **Pattern:** `public void applyEffectTick(LivingEntity, int)`; `public boolean isDurationEffectTick(int,int)`; `removeAttributeModifiers(LivingEntity, AttributeMap, int)` · **Error:** `method does not override…` / `removeAttributeModifiers … cannot be applied` · **Fix:** `isDurationEffectTick`→**`shouldApplyEffectTickThisTick(int,int)`**; the `removeAttributeModifiers(LivingEntity, AttributeMap, int)` override is gone (1.21.1 has `removeAttributeModifiers(AttributeMap)` — on-expiry hooks are managed by the effect's registered attribute modifiers; a custom on-remove procedure has no direct 1.21 hook). · **⚠️ AUGMENT — CORRECTION, this entry's `applyEffectTick` signature is 1.21.4+, NOT 1.21.1.** On **NeoForge 1.21.1** the signature is **`boolean applyEffectTick(LivingEntity, int)`** — it gained the `boolean` return but **kept** the 2-arg shape; the `ServerLevel` first parameter arrives in a later 1.21.x. Writing the 3-arg form gives `method does not override or implement a method from a supertype` + `applyEffectTick cannot be applied to given types`. **Verify with `javap`, don't trust the entry:** `unzip -p build/neoForm/*/steps/recompile/outputs.jar net/minecraft/world/effect/MobEffect.class > /tmp/M.class && javap -p /tmp/M.class`. Same trap on the sibling: **`addAttributeModifiers` DROPPED its `LivingEntity`** in 1.21.1 (`addAttributeModifiers(AttributeMap, int)`), so an override that used the entity for a side effect (e.g. also applying Invisibility to the wearer) has nowhere to put it — move that to the new **`onEffectStarted(LivingEntity, int)`** hook, which exists for exactly this case. (the Fabric weapons mod's `statuseffects/*`; caught by compiling, and a reminder that a §M-style minor-version delta can silently poison a §N entry.)
115. **`ArmorMaterial` is a final record; `ArmorItem` takes `Holder<ArmorMaterial>`** · **Pattern:** `super(new ArmorMaterial(){ getDurabilityForType/getDefenseForType/getEquipSound/getName/getToughness/… }, type, props)` · **Error:** `cannot inherit from final ArmorMaterial` / `constructor ArmorMaterial … cannot be applied` · **Fix:** build the record `new ArmorMaterial(Map<Type,Integer> defense, int enchantmentValue, Holder<SoundEvent> equipSound, Supplier<Ingredient> repair, List<ArmorMaterial.Layer> layers, float toughness, float knockbackResistance)` wrapped in `Holder.direct(...)`; **durability moved to the item** — `properties.durability(type.getDurability(base))`; the material "name" becomes an `ArmorMaterial.Layer(ResourceLocation)` (texture at `textures/models/armor/<path>_layer_{1,2}.png` — see §R armor-texture note).
116. **`SwordItem`/`Tier` (1.20.5)** · **Pattern:** `super(new Tier(){ …getLevel()… }, damage, speed, props)` · **Error:** `Tier … does not override getIncorrectBlocksForDrops()` / `no suitable constructor SwordItem(Tier,int,float,Properties)` · **Fix:** `Tier.getLevel()`→**`getIncorrectBlocksForDrops()`** returning a `TagKey<Block>` (`BlockTags.INCORRECT_FOR_IRON_TOOL` etc.); the sword ctor is now `SwordItem(Tier, Properties)` with damage/speed via `Properties.attributes(SwordItem.createAttributes(tier, damage, speed))` — extract the anonymous Tier to a field so it's referenced twice.
117. **`Item.appendHoverText` gained `Item.TooltipContext`** · **Pattern:** `appendHoverText(ItemStack, Level, List<Component>, TooltipFlag)` · **Error:** `method does not override` / `Level cannot be converted to TooltipContext` · **Fix:** the `Level` param became **`Item.TooltipContext`** (`appendHoverText(ItemStack, Item.TooltipContext, List<Component>, TooltipFlag)`); `super` call unchanged.
118. **`Item.getDefaultAttributeModifiers(EquipmentSlot)` removed** · **Pattern:** override returning `Multimap<Attribute, AttributeModifier>` built with `ImmutableMultimap.builder().putAll(super.getDefaultAttributeModifiers(slot)).put(Attributes.X, new AttributeModifier(...))` · **Error:** `Holder<Attribute> cannot be converted to Attribute` / `EquipmentSlot cannot be converted to ItemStack` (the super overload changed) · **Fix:** item attribute modifiers are the `DataComponents.ATTRIBUTE_MODIFIERS` component now — set them on `Properties.attributes(ItemAttributeModifiers.builder()…build())` at registration and **delete the override** (dropping it entirely is the quick first-port path — the item loses its custom modifiers but loads).
119. **`Raider.RaiderType.create` (Forge enum extension) removed** · **Pattern:** `Raid.RaiderType.create("name", entityType, int[] countsPerWave)` to add a mob to vanilla raids · **Error:** `cannot find symbol: method create(String,EntityType,int[])` · **Fix:** NeoForge dropped the extensible-enum raid-wave registration — **no direct equivalent**; drop the call (the mob still exists via egg/natural spawn, just not in vanilla raids). Also `Raider.applyRaidBuffs(int,bool)` gained a **ServerLevel**: `applyRaidBuffs(ServerLevel, int, boolean)`.
120. **Small MCreator/vanilla renames (cluster)** · **Fixes:** `Wolf` subclass with a custom `String getTexture()`/`setTexture()` **clashes** with vanilla `Wolf.getTexture()` → rename the mod's accessor (`getTextureName`/`setTextureName`) + its callers; `LivingChangeTargetEvent.getOriginalTarget()`→**`getOriginalAboutToBeSetTarget()`**; `Entity.getPassengersRidingOffset()` override removed (passenger attachment API — drop it); `PlayerTickEvent.Post` has no `.player` field → `event.getEntity()`; `AbstractArrow.setKnockback(int)`/`setPierceLevel(byte)` are gone/private (drop the calls — minor arrow-tweak loss); `BrewingRecipeRegistry.addRecipe(IBrewingRecipe)` (static) → mod-bus `RegisterBrewingRecipesEvent` handler + `event.getBuilder().addRecipe(recipe)`.
121. **JEI plugin re-port (JEI 15/1.20.1 → JEI 19/1.21.1) — cluster** · **Pattern:** a `@JeiPlugin implements IModPlugin` with custom `IRecipeCategory<T>` classes, deferred/removed during the first migration pass (recorded in `MIGRATION.md`), now restored. · **Errors/Fixes (each an independent break):**
   · `mezz.jei.library.gui.ingredients.RecipeSlot` (drawing slot backgrounds by casting slot views in `draw()`) → **`package mezz.jei.library… does not exist`** (the `-api` artifact has no `library` internals): give each slot its own background via **`builder.addSlot(role,x,y).setBackground(slotDrawable, -1, -1)`** in `setRecipe`, and delete the `draw()` slot-blit loop. Build the drawable with `IGuiHelper.createDrawable(texture, u, v, w, h)`.
   · `RecipeManager.getAllRecipesFor(type)` now returns **`List<RecipeHolder<T>>`**, not `List<T>` → `for (T r : …)` / `addRecipes(jeiType, …)` fail with **`Object/RecipeHolder cannot be converted to T`**: unwrap with `.stream().map(RecipeHolder::value)` (raw-cast the vanilla `RecipeType` as before).
   · `recipe.getId()` used to filter recipes (e.g. skip a placeholder) → **`cannot find symbol: getId`** (1.21 recipes dropped it; the id is on the `RecipeHolder`): filter at **registration** time on `holder.id()` before unwrapping, and delete the category's `isHandled(recipe)` override.
   · `InventoryScreen.renderEntityInInventoryFollowsMouse(g, x, y, scale, mouseX, mouseY, entity)` → **cannot be applied**: the 1.21 signature takes a **box** — `(g, x1, y1, x2, y2, scale, yOffset, mouseX, mouseY, entity)`; frame a box around the old centre point.
   · `ForgeSpawnEggItem.fromEntityType(type)` → **`SpawnEggItem.byId(EntityType<?>)`** (vanilla; returns null if none). · `new ResourceLocation(ns,path)` → `ResourceLocation.fromNamespaceAndPath` (JEI plugin UID + category IDs + textures too). · `GuiGraphics.blit(rl, x, y, 0/*z*/, u, v, w, h, tw, th)` → drop the int z: `blit(rl, x, y, u, v, w, h, tw, th)`.
   · **No change needed (verify, don't touch):** `IRecipeCategory.getBackground()/getWidth()/getHeight()` are still JEI-19 **default** methods; `IVanillaRecipeFactory.createAnvilRecipe(List,List,List)` (no-uid overload) still exists. (a large boss mod (~750 files, mixin-heavy), `client/jei/`, 4 classes — the mod's JEI compat plugin + three custom-block categories, `…Category`/`…ItemCrafting`/`…Summoning`.)
122. **Datapack content is NOT migrated by the code port — `data/` folders + recipe JSON (1.20.x → 1.21), silent** · **Pattern:** a 1.20.x mod's `data/<ns>/{recipes,advancements,loot_tables,structures}/…` + old-format recipe JSON, copied verbatim by the migration. Bumping `pack_format` to 48 does NOT fix it. · **Runtime — INVISIBLE, in TWO layers:**
   (a) **1.21 singularized every datapack registry directory:** `recipes→recipe`, `advancements→advancement`, `loot_tables→loot_table`, `structures→structure`, `predicates→predicate`, `item_modifiers→item_modifier`, and under `tags/`: `blocks→block`, `items→item`, `entity_types→entity_type`, `fluids→fluid`, `functions→function`, `game_events→game_event`. Old plural dirs are **silently ignored — NO error, no crash** — so every recipe/advancement/loot table/tag in them just doesn't exist (can't craft in-game; absent from JEI).
   (b) once the folder is fixed so they actually LOAD, 1.21's stricter codec **rejects the old JSON**: a result `{"item":X,"count":N}` → **`{"id":X,"count":N}`** (error `No key id in MapLike`); a **bare-string** result (custom recipes; legacy stonecutting) → an ItemStack object **`{"id":X}`** (error `Not a JSON object`), moving stonecutting's top-level `"count"` INTO the result. NeoForge keeps a compat codec for `{"item":X}` *ingredients*, so **only results** need `item→id`. **⚠️ Do NOT over-correct the ingredients:** on **1.21.1** an ingredient is still a JSON *object* (`{"item":X}` / `{"tag":X}`); the bare-string / `"#tag"` shorthand is a **1.21.2+** form and blows up here with `Failed to parse either. First: Not a json array: "#c:leather"; Second: Not a JSON object` → `No ingredients for shapeless recipe`. Fix the namespace, not the shape. Custom-entity SNBT in summon-style recipes (`{Age:-1200,CustomName:{text:"x"}}`) also needs the 1.21 tag format. · **Fix:** `git mv` the dirs to singular (but NOT the nested `advancement/recipes` subfolder — that's an advancement group, not a registry dir); **also re-namespace `data/forge/tags/**` → `data/c/tags/**`** — and note NeoForge's common tags are **PLURAL nouns**, so `forge:leather` → **`c:leathers`** (not `c:leather`). A wrong-but-well-formed tag id is the same silent failure as a wrong directory: it loads, matches nothing, and only shows up as `Not all defined tags … are present in data pack: c:leather` in the log. Check each id against `data/c/tags/` inside the NeoForge universal jar; then a JSON codemod over every `data/*/recipe/**.json` normalizing the top-level `result`. · **Why it hid so long + how to catch it:** headless load and a spawn/battle GameTest never touch recipes, so nothing flagged it. **JEI's focus lookup is what surfaces it** — `JeiRecipeProbe.countRecipesProducing(itemId)` (`-Psmokejeiverify`, §N step 5) asserts JEI shows ≥1 recipe producing an item, which reads **0** when the recipe silently didn't load. **Add a datapack-layout check to every migration's Step 4** (scan for old plural dirs) — found across **5** migrated mods here (a large boss mod (~750 files, mixin-heavy): 98 recipe-parse-errors → **0**). · **(c) SNBT-string NBT fields in custom recipes:** a custom recipe with a stringified-SNBT field (`"nbt": "{Age:-1200,CustomName:'{\"text\":\"x\"}'}"`, the 1.20.x storage form) whose migrated serializer reads it with `CompoundTag.CODEC` → `Not a compound tag` (that codec wants a JSON object). **Fix:** give that field a `Codec.STRING.comapFlatMap(TagParser::parseTag, CompoundTag::toString)` codec so it parses the SNBT string; and migrate the NBT *content* to 1.21 (entity `CustomName` is a **stringified text component** now — `CustomName:'{"text":"x"}'` — not a compound). (the boss mod's re-summon recipe / `summon_pig` — the last of the 98.)

## O. Fourth wave — a furniture mod built on a Registrate-based framework library, i.e. on a *ported library* (86 → 0), and the generics-erasure trap
> **Axis:** decompile-prep + version-family. This port's distinguishing feature: the mod is a thin content
layer over a **builder-DSL library** (a Registrate-based framework library → Registrate), so almost every registration line is a long
generic fluent chain. Vineflower can't reproduce those generics, and its two lossy habits — **raw casts**
and **raw varargs arrays** — silently erase whole chains. ~60 of the 86 residual errors were ONE root cause.

123. **Raw varargs array = *unchecked invocation* = the whole chain's return type is ERASED** · **Pattern:** the decompiler renders a varargs call as an explicit **raw** array: `builder.tag(new TagKey[]{Vanilla.MINEABLE_WITH_AXE}).noOcclusion().isValidSpawn(BlockHelper::never)` (also `validBlock(new NonNullSupplier[]{...})`) · **Error:** the failure surfaces **far downstream, on a later link in the chain**, as `incompatible types: invalid method reference … found: Object` or `Object cannot be converted to EntityType<?>` — never on the `tag(...)` line itself · **Fix:** delete the array and pass real varargs: `.tag(Vanilla.MINEABLE_WITH_AXE)`. **Why:** passing a raw `TagKey[]` to `tag(TagKey<Block>...)` makes the call an *unchecked invocation*; per JLS 15.12.2.6 the result type is then the **erasure** of the declared return type, so `BlockBuilder<OWNER,BLOCK,PARENT>` becomes raw `BlockBuilder` and every subsequent `.transform(X::y)` / lambda loses inference. Codemod it tree-wide **before** hand-fixing individual errors — one `perl`/python pass over `(\s*new (TagKey|NonNullSupplier|...)\[\]\{(.*?)\}\s*)` fixed 29 sites and 40+ errors here. (the furniture mod's `AllBlocks`, `AllItems`, `BlockTransformers`)

124. **Decompiler raw `((Builder)…)` casts break inference on the NEXT call** · **Pattern:** Vineflower drops type arguments on every synthetic cast it inserts into a fluent chain: `((BlockBuilder)REGISTRATE.object("x").block(Foo::new).transform(T::applyDefaults)).transform(T::mineablePickaxe)` · **Error:** `incompatible types: invalid method reference … required: BlockBuilder<BasicRegistrate,BLOCK,BasicRegistrate>; found: Object; reason: cannot infer type-variable(s) BLOCK` · **Fix:** restore the type arguments on the cast. In a registration class the right arguments are **already written on the field/method declaration** — `BlockEntry<OvenBlock.Dyeable> EXAMPLE_OVEN = …` ⇒ `(BlockBuilder<BasicRegistrate, OvenBlock.Dyeable, BasicRegistrate>)`. That makes it a **mechanical codemod, not a per-site judgement call**: walk the file tracking the enclosing `XEntry<T> NAME =` / `XBuilder<..,T,..> method(` declaration and rewrite `((XBuilder)` → `((XBuilder<OWNER, T, OWNER>)`. 198 casts across two files here, in one pass. (the furniture mod's `AllBlocks`/`AllItems`/`BlockBuilders`; same fix proven earlier in the framework library's `ItemBuilder`)


124b. 🔴 **HALF-restoring a generic is WORSE than leaving it raw — a raw ARGUMENT makes the whole
call unchecked, so a restored diamond infers from an erased context and the lambda beside it fails
to LINK.** · **Pattern:** the decompile is raw the whole way down —
`BlockEntityRenderers.register((BlockEntityType)X.get(), context -> new MyRenderer(new MyModel(e ->
…, …)))` — and the port tidies the constructors into diamonds (`new MyRenderer<>`, `new MyModel<>`)
while leaving the cast, because the cast is what silenced the original warning. ·
**Error:** none. javac is *entitled* to say nothing: the raw argument makes the invocation
**unchecked**, so `T` infers to the bound's erasure (`BlockEntity`) and every lambda in the call is
adapted against that. · **Runtime:**
`LambdaConversionException: Type mismatch for dynamic parameter 0: class BlockEntity is not a
subtype of interface software.bernie.geckolib.animatable.GeoAnimatable`, wrapped in a
`BootstrapMethodError` at the moment the CALL SITE links. ·
**Fix:** delete the cast rather than adding type arguments to it. It is almost never needed — a
`DeferredHolder`/`RegistryEntry` already carries the parameter — and with it gone `T` infers to the
real type, which satisfies the intersection bound (`T extends BlockEntity & GeoAnimatable`).
· **Two things make it expensive rather than obvious.** It is a **link**-time failure, so it fires
only when that exact call site first executes — here inside
`BlockEntityRenderDispatcher`'s resource-reload listener, i.e. §V70's shape: the client's INITIAL
reload completes exceptionally, `onResourceLoadFinished` never runs, and the client sits at a
loading screen forever behind one INFO line (`Caught error loading resourcepacks`). And it is
**not a version-family problem at all** — it fails identically on 1.21.1, which the §X15 control
column says in one run and which is what tells you to look at the port rather than at 26.2.
· **The sweep is one shape, and it is narrower than "find the raw casts"**: a raw cast feeding a
method that is NOT generic (`((Block)X.get()).defaultBlockState()`, a `super(...)` taking
`BlockEntityType<?>`) is harmless and there will be dozens. The dangerous form is **a raw argument
in a call that ALSO takes a lambda or method reference**, because that is the only time the
erasure reaches a functional interface's descriptor. On this mod that was 4 of ~20 raw casts: one
renderer registration and three `createTickerHelper(blockEntityType, (BlockEntityType)X.get(),
Y::tick)` — and a fifth site had already been written without the cast, which is what made the
inconsistency visible. (a ~320-file boss mod; found by Gate C's `launch` phase, over a green
Gate A, a green Gate B and a clean compile on both targets.)

125. **`MissingMappingsEvent` was REMOVED from NeoForge (no registry-remap API at all)** · **Pattern:** `@SubscribeEvent onMissingMappings(MissingMappingsEvent e)` with `e.getMappings(Registries.BLOCK, modid)` + `mapping.remap(newBlock)` — the standard legacy-id table a long-lived mod carries · **Error:** `cannot find symbol: class MissingMappingsEvent` (`net.neoforged.neoforge.registries`) · **Fix:** there is **no replacement** in NeoForge 1.21.1 (and no `MappedRegistry#addAlias`). Delete the handler and record the drop — it only ever affected worlds saved by an older version of that mod, which a fresh 1.21.1 install doesn't have. Note it in `MANUAL_VALIDATION.md` as "old-world block/item ids will be lost, not remapped". (the furniture mod's `CommonForgeEvents`, 130 remap cases dropped)

126. **Mod-bundled built-in resource pack: `Pack.readMetaAndCreate` reshaped** · **Pattern:** `event.addRepositorySource(c -> c.accept(Pack.readMetaAndCreate(id, Component, required, path -> new PathPackResources(path, resource, true), PackType, Position, PackSource)))` in an `AddPackFindersEvent` handler · **Error:** `method readMetaAndCreate … cannot be applied to given types` (it is now `readMetaAndCreate(PackLocationInfo, ResourcesSupplier, PackType, PackSelectionConfig)`) · **Fix:** don't rebuild the argument soup — NeoForge ships the helper: `event.addPackFinders(ResourceLocation.fromNamespaceAndPath(modid, "<dir under resources/>"), PackType.CLIENT_RESOURCES, Component.literal(name), PackSource.BUILT_IN, alwaysActive, Pack.Position.TOP)`. (the furniture mod's `CommonModEvents`, the ctm/xycraft compat packs)

127. **`BlockState#use` → `useWithoutItem(Level, Player, BlockHitResult)`** · **Pattern:** a block delegating to another block's interaction: `otherState.use(level, player, hand, hitResult)` · **Error:** `cannot find symbol: method use(Level,Player,InteractionHand,BlockHitResult)` · **Fix:** the **state-level** counterpart of the well-known `Block#use`→`useWithoutItem` split drops the `InteractionHand` (an item-in-hand interaction is `useItemOn` now): `otherState.useWithoutItem(level, player, hitResult)`. Easy to miss because the *block* override was already fixed by the bulk `use`→`useWithoutItem` codemod. (the furniture mod's `WardrobeTopBlock`)
· 🔴 **AUGMENT — the OVERRIDE itself can survive the port in its 1.20 shape, and then it is dead on
BOTH targets.** `public InteractionResult use(BlockState, Level, BlockPos, Player, InteractionHand,
BlockHitResult)` with no `@Override` still compiles in 1.21 (it is just a new public method), overrides
nothing, and right-clicking the block silently does nothing. Measured 2026-09-23: **seven** blocks of a space-exploration mod (~550 files)
(every machine through `MachineBlock`, plus globe, detector, energizer, radio, flag, sliding
door), a large boss mod's (~750 files, mixin-heavy) custom pumpkin block (shears carving) and two tables in a weapon mod (~240 files) — with
every gate green on both targets, because no gate RIGHT-CLICKED anything. · **Fix:** `useWithoutItem`
when the body ignores the held item, `useItemOn` (returning `ItemInteractionResult`) when it reads
`player.getItemInHand(hand)`. Put `@Override` on it so the next shape change is a compile error. ·
**Sweep:** `grep -rnE "InteractionResult use\(BlockState \w+, Level \w+, BlockPos \w+, Player \w+,
InteractionHand" src/`. · **Wired control:** the space mod's gauntlet now has an `OPEN_GUIS` step that
right-clicks every placed block whose block entity is a menu and requires its screen to appear and
draw — 0 of 11 before, 10 of 10 after (the energizer is exempt BY NAME: its right-click moves items,
not a menu). The weapon mod's two are fixed too (`useWithoutItem`), with a GameTest that drives the real
`BlockState#useWithoutItem` and requires the action to be consumed. It builds in a cloud session with
`--init-script ../<modid>/tools/central-mirror.init.gradle`; "NeoGradle does not resolve here" was a stale note.

128. **`SpecialRecipeBuilder.special` takes the recipe CONSTRUCTOR, not the serializer** · **Pattern:** `SpecialRecipeBuilder.special(MyRecipes.FOO.get()).save(provider, id)` (a `RecipeSerializer`) · **Error:** `incompatible types: RecipeSerializer cannot be converted to Function<CraftingBookCategory,Recipe<?>>` · **Fix:** `SpecialRecipeBuilder.special(MyRecipe::new)` — it now builds the recipe directly (`factory.apply(CraftingBookCategory.MISC)`), matching `CustomRecipe(CraftingBookCategory)` and `SimpleCraftingRecipeSerializer.Factory<T>`. Both places want the same `CraftingBookCategory -> T` constructor reference. (the furniture mod's `DataGenerators` / `AllRecipeSerializers`)

129. **Screen/GUI cluster (1.21)** · **Fixes (each a one-liner):** `RenderSystem.getModelViewStack()` returns a **JOML `Matrix4fStack`**, not a `PoseStack` — `pushPose/popPose/mulPoseMatrix(m)` → `pushMatrix/popMatrix/mul(m)` (`incompatible types: Matrix4fStack cannot be converted to PoseStack`); `mouseScrolled(double,double,double)` gained a **horizontal** delta → `mouseScrolled(double mouseX, double mouseY, double scrollX, double scrollY)` and the vertical value is the LAST arg (`required: double,double,double,double`); `ModelPart.render(pose, buffer, light, overlay, r,g,b,a)` collapsed its 4 colour floats to one packed ARGB int exactly like `Model#renderToBuffer` (#104) — `render(pose, buffer, light, overlay, color)`, `-1` = opaque white. (the furniture mod's crafting-station menu screen and two plant block models)

130. **`ForgeRegistries` does not exist in NeoForge 1.21.1 AT ALL — not even under `net.neoforged.neoforge.registries`** · **Pattern:** the Forge→NeoForge import codemod rewrites `net.minecraftforge.registries.ForgeRegistries` → `net.neoforged.neoforge.registries.ForgeRegistries`, which looks plausible and is wrong · **Error:** `package net.neoforged.neoforge.registries.ForgeRegistries does not exist` / `cannot find symbol: class ForgeRegistries` · **Fix:** `net.minecraft.core.registries.BuiltInRegistries.X`, with **SINGULAR** member names (`ENTITY_TYPES`→`ENTITY_TYPE`, `ITEMS`→`ITEM`, `ATTRIBUTES`→`ATTRIBUTE`, `BLOCKS`→`BLOCK`, `MOB_EFFECTS`→`MOB_EFFECT`, `SOUND_EVENTS`→`SOUND_EVENT`, `PARTICLE_TYPES`→`PARTICLE_TYPE`); `registry.getValue(rl)` → `registry.get(rl)`. `ForgeRegistries.Keys.X` → `NeoForgeRegistries.Keys.X` (NeoForge-owned registries) or plain `Registries.X` (vanilla). (a mob-framework library's `ParticleInit`/`AttributeRegistry`, a ~390-file mob mod's `ModEntities`/`ModSoundEvents`, 44 sites)

131. **Declare `DeferredRegister` entries as `DeferredHolder`, not `Supplier` — 1.21 Holder-ified attributes and effects** · **Pattern:** `public static final Supplier<Attribute> FOO = ATTRIBUTES.register(...)` then `entity.getAttribute((Attribute)FOO.get())` (same shape for `Supplier<MobEffect>` + `hasEffect((MobEffect)X.get())`) · **Error:** `incompatible types: Attribute cannot be converted to Holder<Attribute>` / `MobEffect cannot be converted to Holder<MobEffect>` — on `LivingEntity#getAttribute`/`#hasEffect`/`#addEffect`, `AttributeSupplier.Builder#add`, `EntityAttributeModificationEvent#has/add`, `new MobEffectInstance(...)` · **Fix:** change the FIELD TYPE to `DeferredHolder<Attribute, Attribute>` / `DeferredHolder<MobEffect, MobEffect>` (a `DeferredHolder` **is** a `Holder`) and delete the `(Attribute)X.get()` / `(MobEffect)X.get()` casts at every call site — `.get()` still works where a raw value is genuinely wanted. Fixing the declaration once removes dozens of call-site errors; casting at each site does not (there is no `Attribute`→`Holder` conversion). (the mob-framework library's `AttributeRegistry`, the ~390-file mob mod's `ModEffects`)

132. **`MobType` was REMOVED in 1.21 — "mob type" is entity-type tags now** · **Pattern:** `entity.getMobType() == MobType.UNDEAD` in an `isAlliedTo`/target check, plus a `public MobType getMobType()` override on each custom mob · **Error:** `cannot find symbol: class MobType` / `method getMobType()` (`net.minecraft.world.entity`) · **Fix:** there is no drop-in. Damage bonuses moved to enchantment effect components, and identity moved to data-driven tags — so replace the *predicate* with a tag test: `entity.getType().is(EntityTypeTags.UNDEAD)` / `EntityTypeTags.ILLAGER` / `EntityTypeTags.ARTHROPOD`, and **delete the `getMobType()` overrides** (a custom mob declares its type by being in the tag JSON). A tiny `MobTypeCompat` helper keeps the call sites one-line and documents the mapping. (a ~390-file mob mod, 16 files)

133. **`Raid.RaiderType.create(...)` is gone — raider types are an EXTENSIBLE ENUM (JSON), not an imperative call** · **Pattern:** `RaiderType.create("my_raider", MY_TYPE.get(), new int[]{...})` at mod init, the standard Forge way to add a village-raid wave member · **Error:** `cannot find symbol: method create(String,EntityType,int[])` in `Raid.RaiderType` · **Fix:** in NeoForge 1.21.1 `Raid$RaiderType implements IExtensibleEnum`: entries are added **declaratively** via an enum-extension JSON (`enumExtensions="META-INF/enumextensions.json"` in `neoforge.mods.toml`) plus an `@EnumProxy` constant supplying `(EntityType<? extends Raider>, int[])`. There is no runtime `create`. If raids are not the point of the port, dropping the integration is a clean documented feature loss — the mobs still spawn naturally, from eggs and from spawners. (the ~390-file mob mod's `RaidEntries`, 11 raiders)

134. **`SpawnPlacements.register` is no longer public API — placements come from `RegisterSpawnPlacementsEvent`** · **Pattern:** `SpawnPlacements.register(TYPE.get(), SpawnPlacements.Type.ON_GROUND, Heightmap.Types.MOTION_BLOCKING_NO_LEAVES, Mob::checkMobSpawnRules)` from `FMLCommonSetupEvent.enqueueWork`, plus a custom `SpawnPlacements.Type.create(name, predicate)` · **Error:** `cannot find symbol: variable Type` / `method register(...)` on `SpawnPlacements` · **Fix:** three separate changes. (a) `SpawnPlacements.Type` → the **`SpawnPlacementType` interface**; vanilla constants live in **`SpawnPlacementTypes`** (`ON_GROUND`, `IN_WATER`, `IN_LAVA`, `NO_RESTRICTIONS`). (b) A custom placement type is no longer registered — it is just an implementation of `SpawnPlacementType` (one `isSpawnPositionOk` method), so `Type.create(...)` becomes a plain constant. (c) Registration moves to NeoForge's **`RegisterSpawnPlacementsEvent` on the MOD bus**: `modEventBus.addListener(X::init)` then `event.register(type, placement, heightmap, predicate, RegisterSpawnPlacementsEvent.Operation.REPLACE)`. Also `BlockState#isValidSpawn` dropped its `SpawnPlacementType` argument. (the ~390-file mob mod's `EntitySpawnPlacement`, 25 registrations)

135. **`Event#isCancelable()` is gone — cancellability is static (`ICancellableEvent`), and several events stopped being cancellable** · **Pattern:** the defensive `if (cond && event.isCancelable()) event.setCanceled(true);` idiom, applied uniformly to a dozen events · **Error:** `cannot find symbol: method isCancelable()` (and, once that is deleted, `cannot find symbol: method setCanceled(boolean)` on the events that are not cancellable) · **Fix:** drop the `isCancelable()` guard — an event either implements `ICancellableEvent` (so `setCanceled` exists and always works) or it does not. Then **delete the handlers whose events are no longer cancellable**; in NeoForge 1.21.1 that includes `PlayerInteractEvent.RightClickEmpty`/`.LeftClickEmpty` (notification-only) and the `LivingEntityUseItemEvent` **base** (the cancellable one is `.Start`). `FillBucketEvent` was removed outright (bucket use flows through `RightClickItem`). Check each target with `javap … | grep ICancellableEvent` rather than guessing. (the ~390-file mob mod's `EntityEvents`, 13 handlers → 9)

136. **Vanilla signature changes that a MIXIN `@Inject` must track too — compiles clean, dies at mixin APPLY** · **Pattern:** `@Inject(method = "finalizeSpawn") private void x(ServerLevelAccessor a, DifficultyInstance b, MobSpawnType c, SpawnGroupData d, CompoundTag e, CallbackInfoReturnable<SpawnGroupData> cir)` — the mixin still mirrors the 1.20 parameter list after `finalizeSpawn` dropped its trailing `CompoundTag` (same class of break for `defineSynchedData`, which GAINED a `SynchedEntityData.Builder`) · **Runtime:** `InvalidInjectionException: Invalid descriptor on …@Inject::finalizeSpawn(…Lnet/minecraft/nbt/CompoundTag;…)! Expected (…) but found (…)` → `MixinApplyError` → **the mod refuses to load entirely** · **Fix:** apply the same signature change to the injected method that you applied to the overrides. **javac cannot see this** (a mixin method's parameters are just a method's parameters); only a runtime gate catches it. When a codemod fixes an override signature, ALWAYS re-grep `src/main/java/**/mixin/` for the same method name. (the ~390-file mob mod's `PiglinEntityMixin`, caught by Gate B)

137. **`private final boolean isConstructed = true` is a COMPILE-TIME CONSTANT — the guard it was written for never fires** · **Pattern:** a decompiled mob guards a method the SUPERCLASS CONSTRUCTOR calls (`reassessWeaponGoal`, `refreshDimensions`, …) with a `private final boolean` field initialised to a literal · **Runtime:** `NullPointerException: Cannot invoke "Object.hashCode()" because "this.goal" is null` (or similar) on the first spawn — `AbstractSkeleton`'s ctor calls `reassessWeaponGoal()` **before** the subclass's field initialisers run, so `meleeGoal`/`bowGoal`/`crossbowGoal` are still null · **Fix:** javac inlines `private final X = <literal>` at every use (JLS 4.12.4 constant variable), so the guard reads as literal `true` and is not a guard at all. Make it a **plain non-final field** (default `false`) assigned at the END of each constructor. Worth grepping for on every entity mod: `grep -rn 'private final boolean.*= *true;' src/main/java`. (the ~390-file mob mod's skeleton-variant mob, caught by the Gate-B spawn-every-mob test)

138. **GeckoLib 4.7 → 4.8 (hits EVERY GeckoLib mod on 1.21.1)** · **Pattern:** the whole `software.bernie.geckolib.core.*` tree, plus the 4-float colour tail on every renderer hook · **Error:** `package software.bernie.geckolib.core.animation does not exist`; `method reRender/renderCube/renderRecursively/preRender/renderFinal … cannot be applied to given types (required: …,int  found: …,float,float,float,float)`; `cannot find symbol: class RenderUtils` · **Fix:** the `core` subtree was flattened into the main package and colours were packed. Codemod: `…geckolib.core.animation.*`→`…geckolib.animation.*`; `…geckolib.core.animatable.*`→`…geckolib.animatable.*`; `…geckolib.core.object.PlayState`→`…geckolib.animation.PlayState`; `…geckolib.core.object.Color`→`…geckolib.util.Color`; `…geckolib.core.animatable.model.CoreGeoBone`→`…geckolib.cache.object.GeoBone`; `…geckolib.util.RenderUtils`→`RenderUtil` (singular). **Every** `GeoRenderer` hook (`preRender`/`actuallyRender`/`renderFinal`/`renderRecursively`/`renderCubesOfBone`/`renderChildBones`/`renderCube`/`reRender`) replaced `float red, float green, float blue, float alpha` with a single **`int colour`** (build with `FastColor.ARGB32.color(a,r,g,b)`; `-1` = opaque white) — the same packing vanilla did to `Model#renderToBuffer` (#104). Also `GeoModel#applyMolangQueries` now takes an `AnimationState<T>` and the global `MolangParser.INSTANCE` singleton is GONE. (the mob-framework library + the ~390-file mob mod, 57 files)

139. **An AT line for a member that no longer exists TRUNCATES the whole Minecraft recompile — and Gradle CACHES it** · **Pattern:** a converted 1.20 access transformer carrying an entry whose target changed in 1.21, e.g. `public net.minecraft.world.entity.LivingEntity breakItem(Lnet/minecraft/world/item/ItemStack;)V` · **Error:** ~11,000 BOGUS errors of the form `package net.minecraft.core.registries does not exist` / `cannot find symbol: class LivingEntity` — i.e. "the whole of Minecraft is missing", identical in shape to the fork/`-Xmaxerrs` scoping trap already noted in the template's build.gradle · **Fix:** **diagnose by counting classes, not by reading errors**: `unzip -l build/neoForm/*/steps/recompile/outputs.jar | grep -c '\.class$'` — a healthy 1.21.1 recompile is **9766**; a poisoned one was 5284. Then bisect the AT by removing entries. Delete (or re-target) the dead line, `rm -rf build/neoForm build/classes`, and rebuild with `--no-build-cache` once, since the truncated jar is cached. An AT is a *prerequisite* for a readable error count: fix it before bucketing anything. (a ~390-file mob mod, 11938 bogus → 671 real errors)
· ⚠ **AUGMENT — MODDEVGRADLE TOLERATES a dead AT entry where NeoGradle truncated, so on a §W tree
this stops being a build failure and becomes a SILENT one.** Measured on a ~660-file GeckoLib mob mod, whose 41-entry
AT carries **14 entries that resolve against nothing on 26.2** — all six `client.model.PlayerModel`
lines (the class moved to `client.model.player` and `ear` is gone entirely, §V86), `PlayerRenderer
.getArmPose`, two `ItemInHandRenderer` methods, `GameRenderer.darkenWorldAmount` and the three
`HumanoidArmorLayer` ones — and whose staged jars are both HEALTHY: **9766 classes on 1.21.1 and
10963 on 26.2**, against the 5284 a poisoned NeoGradle recompile produced. MDG applies what resolves
and drops the rest without a word.
· **So the class count is still the right instrument and is no longer the alarm.** Under MDG the
entry does not announce itself at all: the widening simply does not happen, and the first thing that
notices is either a `has protected access` compile error naming the exact site (the lucky half) or,
where the member was widened for a MIXIN or for reflection, nothing until runtime.
· **The fix is the same and is now cheap to justify:** derive the AT per target with
`tools/gen-at.py`, keeping only what resolves against THAT target's jar, declare it with
`accessTransformers.from(src/<overlay>/resources/META-INF/accesstransformer.cfg)`, and wire
`gen-at.py --check` into `check`. **A mod that declares no `accessTransformers` block at all is
relying on MDG's convention pickup of `src/main/resources/META-INF/accesstransformer.cfg`** — which
means both targets get the same file and neither gets a per-target answer, so the drift is invisible
by construction. ⚠ Declaring one re-stages the Minecraft artifacts, so do not do it while anything
else is reading them (§X25b-iii).
· **AUGMENT — CORRECTION for 1.21.1, and a NeoGradle template trap:** `LivingEntity#breakItem(ItemStack)` still EXISTS on 1.21.1 (`private void breakItem(net.minecraft.world.item.ItemStack)` by `javap` on the recompile jar), and `public net.minecraft.world.entity.LivingEntity breakItem(Lnet/minecraft/world/item/ItemStack;)V` in the AT leaves a healthy **9766**-class recompile — dropping it because of this entry gives `breakItem(ItemStack) has private access in LivingEntity`. Check each AT line with `javap -p` instead. Separately, the NeoGradle (`net.neoforged.gradle.userdev`) template does NOT pick `META-INF/accesstransformer.cfg` up by convention: every widened member stays private (`cooldowns has private access in ItemCooldowns`, `CooldownInstance is not public`) until `minecraft.accessTransformers.file rootProject.file('src/main/resources/META-INF/accesstransformer.cfg')` is added. A converted Forge AT also still carries SRG names (`f_41515_`, `m_21278_`) — `srg-remap/apply_mapping.py` only rewrites `.java`.

140. **`@Inject`ing into a vanilla inner GOAL class needs the qualified `outer.new Inner(...)` form** · **Pattern:** `new RaiderOpenDoorGoal(this, this)` / `new HoldGroundAttackGoal(this, this, 10.0F)` — the decompiler renders the synthetic outer-instance parameter as an ordinary first argument · **Error:** `constructor RaiderOpenDoorGoal in class AbstractIllager.RaiderOpenDoorGoal cannot be applied to given types` (plus `has protected access in AbstractIllager`) · **Fix:** two things. (a) These are **non-static inner classes**, so the source form is `this.new RaiderOpenDoorGoal(this)` / `this.new HoldGroundAttackGoal(this, 10.0F)` — the outer instance is the receiver, not an argument. (b) They are `protected`, so a subclass in another package still needs an AT (`public net.minecraft.world.entity.monster.AbstractIllager$RaiderOpenDoorGoal`). (the ~390-file mob mod's two vindicator-variant mobs)

141. **Melee-reach cluster: `getAttackReachSqr` → `isWithinMeleeAttackRange` (a BOOLEAN, not a distance)** · **Pattern:** `if (distToEnemySqr <= this.getAttackReachSqr(target))` inside a custom `MeleeAttackGoal.checkAndPerformAttack` · **Error:** `cannot find symbol: method getAttackReachSqr(LivingEntity)` · **Fix:** 1.21 folded reach + entity size into `Mob#isWithinMeleeAttackRange(LivingEntity)`, which returns a **boolean** — so the surrounding distance comparison must be DELETED, not adapted (a mechanical rename leaves `double <= boolean`). The method is `protected` on `Mob`, so a goal in another class calls it through the mob field (`this.mob.isWithinMeleeAttackRange(target)`) or needs an AT. Sibling renames in the same pass: `Entity#setMaxUpStep(f)` → the `Attributes.STEP_HEIGHT` attribute; `LivingEntity#getStandingEyeHeight(Pose,EntityDimensions)` → `EntityDimensions#eyeHeight()`; `Entity#getDimensions(Pose)` → `getDefaultDimensions(Pose)`; `Entity#getGravity()` is **final** now (override `getDefaultGravity()`); `LivingEntity#broadcastBreakEvent` is gone (`ItemStack#hurtAndBreak` emits it); `Explosion#getExploder` → `getDirectSourceEntity`; `EventHooks.getMobGriefingEvent(Level,Entity)` → `canEntityGrief(ServerLevel,Entity)`. (the ~390-file mob mod, ~20 sites)

142. **1.21 datapack directories were SINGULARIZED — the old plural dirs load NOTHING, silently** · **Pattern:** a 1.20 mod's `data/<ns>/loot_tables/`, `data/<ns>/structures/`, `data/<ns>/tags/entity_types/`, `data/<ns>/tags/items/` · **Runtime:** no error at all — every loot table / structure / tag in those folders is simply never read, so mobs drop nothing and tags are empty · **Fix:** rename to the singular: `loot_tables`→`loot_table`, `structures`→`structure`, `recipes`→`recipe`, `advancements`→`advancement`, `predicates`→`predicate`, `item_modifiers`→`item_modifier`, and under `tags/`: `items`→`item`, `blocks`→`block`, `entity_types`→`entity_type`, `fluids`→`fluid`, `functions`→`function`, `game_events`→`game_event`. **Additionally** the NeoForge-owned data namespaces moved: `data/<ns>/forge/biome_modifier|structure_modifier` → `data/<ns>/neoforge/…`, `data/forge/loot_modifiers` → `data/neoforge/loot_modifiers`, the modifier/condition TYPE ids `forge:add_spawns|and|or|not|loot_table_id` → `neoforge:…`, the model-loader id `"loader": "forge:…"` → `"neoforge:…"`, and common tag refs `#forge:x` → `#c:x`. This is covered by the §122a/§122d scans — **run the sweep**, because nothing else surfaces it. (the ~390-file mob mod, 36 loot tables + 27 modifier JSONs + 4 item models; the mob-framework library's `tags/items`)

143. **1.21 LOOT-TABLE cluster — every 1.20 mob-drop table fails to parse (silently, at data load)** · **Pattern:** the standard 1.20 mob drop table: `{"function":"minecraft:looting_enchant","count":{...}}` and `{"condition":"minecraft:random_chance_with_looting","chance":C,"looting_multiplier":M}`, plus `{"function":"minecraft:set_nbt","tag":"{Potion:\"minecraft:poison\"}"}` and `"entity":"killer"` · **Runtime:** `Couldn't parse element …loot_table]:<id> - Failed to parse either. First: Unknown registry key in …loot_function_type]: minecraft:looting_enchant` — logged as an ERROR at data load and then **ignored**: the mob simply drops nothing. No crash, no gate failure · **Fix:** four renames, all mechanical (do them with a JSON walker, not sed — these nest inside `pools[].entries[].functions[]`): `looting_enchant` → **`enchanted_count_increase`** + an explicit `"enchantment": "minecraft:looting"`; `random_chance_with_looting{chance, looting_multiplier}` → **`random_chance_with_enchanted_bonus`**`{unenchanted_chance, enchanted_chance: {type: "minecraft:linear", base, per_level_above_first}, enchantment}`; `set_nbt{tag}` → **`set_components`**`{components: {...}}` (item NBT is data components); loot-context entity target `"killer"` → **`"attacker"`** (and `killer_player` → `attacking_player`). **You only SEE these once §142's directory rename is done** — under `loot_tables/` the files were never read at all. (the ~390-file mob mod, 36 tables)

144. **Fixing §142 UNMASKS latent data bugs — expect a second wave, and treat it as progress** · **Pattern:** after renaming `loot_tables`→`loot_table` / `forge/`→`neoforge/`, data that had been silently inert starts loading — and hard-fails · **Runtime:** `IllegalStateException: Unknown registry key in ResourceKey[minecraft:root / minecraft:entity_type]: <modid>:<id>` → `Failed to load registries` → **server won't start**; and a flood of `Couldn't parse element …loot_table]` (see #143) · **Fix:** these are usually **pre-existing bugs in the upstream mod's own data** (stale ids the author left behind, e.g. a structure modifier spawning `gold_armored_vindicator` when only `armored_vindicator` was ever registered), not something the migration introduced — the 1.20 path just never read the file. Cross-check every `<modid>:` id referenced from `data/` against the registry (`grep -rho '"<modid>:[a-z_]*"' src/main/resources/data | sort -u`) and delete or re-point the dead ones. Sequence matters: rename dirs → fix the hard registry failures → fix the parse errors → re-run Gate B, and **read the data-load ERROR lines even when the gate is green**, because a loot-table parse failure never fails a test. (the ~390-file mob mod's `mansion_spawns.json`) · ⚠ **AUGMENT — it recurred in a FORK port of the same mod** (the jar-pipeline port's fix does not carry over), and Gate B's `DataLivenessGameTest` is what now forces the second wave to surface: a structure modifier that names a dead id stops the server loading its registries, so the gate fails by name. Re-point a dropped variant at the entity that replaced it rather than deleting the spawn, so the author's intent survives (see S9).

## P. FABRIC → NeoForge — a WHOLE NEW AXIS (surfaced porting two Fabric gear mods)
> **Axis:** loader-transform where `SRC_LOADER = fabric`. Everything in §B–§E/§H assumes a *Forge*
> source and **does not apply**; `tools/srg-remap` does not apply either. Read this section first when
> the source jar is Fabric. Confirmed against NeoForge 21.1.228 / MC 1.21.1.

145. **🔴 DETECT IT AT TRIAGE — a Fabric jar has no `mods.toml`, and its classes are INTERMEDIARY-mapped** ·
   **Pattern:** the intake triage says "Forge, converts `META-INF/mods.toml`" but `unzip -l` shows **no** `mods.toml`; instead `fabric.mod.json`, a `*.accesswidener`, `META-INF/jars/*.jar` (Fabric JiJ), and `META-INF/MANIFEST.MF` containing **`Fabric-Mapping-Namespace: intermediary`** ·
   **Error:** the decompile is full of `net.minecraft.class_1799`, `method_7909`, `field_8125` — **not** SRG `m_*/f_*` and **not** official names, so `tools/srg-remap` is a no-op and every single Minecraft reference is `cannot find symbol` ·
   **Fix:** run the **triage commands, not the triage summary** — `unzip -l "$JAR" | grep -E 'mods.toml|fabric.mod.json|accesswidener|META-INF/jars'` and `unzip -p "$JAR" META-INF/MANIFEST.MF | grep Mapping-Namespace`. Then take the Fabric path: remap with **`tools/intermediary-remap/`** (entry #146) and treat §P as the loader corpus. A handed-down triage is a hypothesis; two minutes of `unzip` is the fact. (two Fabric gear mods: the intake described them as Forge with `mods.toml`; both are pure Fabric.)

146. **Intermediary → official remap (the Fabric twin of `tools/srg-remap`) — the single most important step** ·
   **Pattern:** decompiled Fabric source: `class_1799 stack`, `stack.method_7909()`, `import net.minecraft.class_1322.class_1323;` ·
   **Error:** thousands of `cannot find symbol: class class_1799 / method method_7909` ·
   **Fix:** `tools/intermediary-remap/` — same principle as SRG (intermediary ids are **globally unique**, so a flat text replacement is safe and complete), different join. `build_mapping.py <mc> <out.json>` joins **Mojang official mappings** (official↔obf, from the version manifest) with **Fabric's `intermediary` tiny-v2** (obf↔intermediary, from `maven.fabricmc.net/net/fabricmc/intermediary/<mc>/intermediary-<mc>-v2.jar`) on the **obfuscated** names — the method descriptor for the join is rebuilt from the proguard signature exactly as `srg-remap` does. 1.20.1 yields **7388 classes / 67048 members**. `apply_mapping.py` then does three ordered passes: fully-qualified `net.minecraft.class_A[.class_B…]` → the official FQ dotted name, then bare `class_N` → the official **simple** name, then `method_N`/`field_N` → the member name.
   ⚠️ **Inner classes are the one subtlety:** intermediary writes them `class_759$class_5773`, but Vineflower emits them **dotted** (`import net.minecraft.class_1322.class_1323;`), so the FQ pass must accept a dotted chain and key the lookup on the `$` form. Verify with `grep -rc '\b(class|method|field)_[0-9]+\b' src/main/java` → **0** (a Fabric weapons mod: 172 files, fq=1281 bare=2695 members=2546, zero leftovers; a Fabric armour mod: 66 files, zero leftovers).

147. **Fabric entrypoints → `@Mod`** · **Pattern:** `class X implements ModInitializer { public void onInitialize(){ …} }` + `class XClient implements ClientModInitializer` declared in `fabric.mod.json`'s `entrypoints` · **Error:** `package net.fabricmc.api does not exist` · **Fix:** `@Mod(MODID) class X { public X(IEventBus modBus, ModContainer container) { … } }` (§B #5). The client entrypoint becomes either `@Mod(value=MODID, dist=Dist.CLIENT)` or an `@EventBusSubscriber(value=Dist.CLIENT)` — **but see R1**: never annotate a class that has no `@SubscribeEvent` methods. `@Environment(EnvType.CLIENT)` → `@OnlyIn(Dist.CLIENT)`; `FabricLoader.getInstance().isModLoaded(x)` → `ModList.get().isLoaded(x)`.

148. **🔴 Fabric registers eagerly, NeoForge registers on an event — and this bites hardest in CONFIG-DRIVEN mods** ·
   **Pattern:** `Registry.register(BuiltInRegistries.ITEM, id, item)` called straight from `onInitialize()`, often inside a loop gated on config (`for (WeaponID w : values()) if (w.getIsEnabled()) register(…)`) ·
   **Runtime:** `IllegalStateException: Registry is already frozen` (the same family as **R13**) — vanilla registries are frozen long before a NeoForge mod could imperatively write to them ·
   **Fix:** `DeferredRegister` on the mod bus. The **loop survives intact** — call `ITEMS.register(name, () -> makeItem())` from the `@Mod` constructor, because a DeferredRegister is populated at construction and flushed at `RegisterEvent`. That makes the config read happen at *construction*, which is only safe because of #149.

149. **Fabric mods read config during registration BY DESIGN — so port the config to a plain file, NOT a `ModConfigSpec`** ·
   **Pattern:** a Cloth Config / `me.shedaniel.autoconfig` POJO (`@Config`, `ConfigData`, `PartitioningSerializer`, Jankson `@Comment`) whose values decide **what gets registered** ·
   **Error:** `package me.shedaniel.autoconfig does not exist` — and the obvious port (rewrite as a NeoForge `ModConfigSpec`) walks straight into **R2** (`Cannot get config value before spec is built`), because a `ModConfigSpec` is not built until after `RegisterEvent` ·
   **Fix:** don't port the config *mechanism*, port the config *file*. Keep every POJO unchanged and add a ~150-line **gson shim** in the mod's own package (`<pkg>.compat.autoconfig`) exposing the same surface — `AutoConfig.register/getConfigHolder/getConfig`, plus `@Config`/`@ConfigData`/`@ConfigEntry`/`@Comment` as inert annotations — that reads/writes `config/<modid>.json` under `FMLPaths.CONFIGDIR`. The POJOs already fill their defaults in their constructors, so gson deserializing over a fresh instance yields correct defaults for a missing/partial file. Load it in the `@Mod` constructor, before the DeferredRegisters. **Dropped feature:** the in-game config GUI (Cloth's whole other half) — record it. Codemod the imports to the shim's package rather than squatting `me.shedaniel.*`, so a real Cloth Config install can't collide. (the Fabric weapons mod: 7 config classes; the Fabric armour mod: 6.)

150. **Fabric API → NeoForge equivalents (cluster)** · **Error:** `package net.fabricmc.fabric.api.* does not exist` · **Fix**, one per subsystem:
   · `itemgroup.v1.ItemGroupEvents.modifyEntriesEvent(tab).register(entries -> entries.accept(x))` — Fabric lets an **item add itself to a creative tab from its own constructor**, which NeoForge cannot do at all. Collect into a `Map<ResourceKey<CreativeModeTab>, List<Supplier<Item>>>` and replay it from one **`BuildCreativeModeTabContentsEvent`** handler. Store a `Supplier<Item>`, not an `Item` — entries queued during `DeferredRegister` setup do not exist yet. (See **R22**: build the tab with `event.accept(...)`, never by mutating the entry sets.)
   · `itemgroup.v1.FabricItemGroup.builder()` → vanilla `CreativeModeTab.builder()` in a `DeferredRegister<CreativeModeTab>`; keep the original `ResourceKey` ids so the `itemGroup.<ns>.<path>` lang keys still resolve.
   · `object.builder.v1.entity.FabricEntityTypeBuilder.create(cat, F::new).dimensions(EntityDimensions.fixed(w,h)).build()` → vanilla `EntityType.Builder.of(F::new, cat).sized(w,h).build(name)`; the attributes that Fabric attached to the builder move to NeoForge's **`EntityAttributeCreationEvent`**.
   · `particle.v1.FabricParticleTypes.simple(true)` → plain `new SimpleParticleType(true)`; `client.particle.v1.ParticleFactoryRegistry` → `RegisterParticleProvidersEvent`.
   · `client.rendering.v1.EntityRendererRegistry` → `EntityRenderersEvent.RegisterRenderers`.
   · `loot.v2.LootTableEvents.MODIFY` → `LootTableLoadEvent` on `NeoForge.EVENT_BUS`.
   · `object.builder.v1.trade.TradeOfferHelper` → `VillagerTradesEvent` / `WandererTradesEvent`.
   · `object.builder.v1.block.FabricBlockSettings` → `BlockBehaviour.Properties`.
   · `client.keybinding.v1.KeyBindingHelper` → `RegisterKeyMappingsEvent`; `client.event.lifecycle.v1.ClientTickEvents` → `ClientTickEvent.Post`.
   · `{client.,}networking.v1.{Client,Server}PlayNetworking` + `PacketByteBufs` → the payload system (§E #15–#19), **and R7: the decoder must fully drain the buffer**.
   · `resource.conditions.v1.ResourceConditions` (`"fabric:load_conditions"` in recipe/advancement JSON, used to config-gate recipes) — NeoForge's condition system is `"neoforge:conditions"` with different condition types. For a first port, **strip the key** (the recipes then always load) and record the dropped gating.

151. **Fabric `accesswidener` → NeoForge access transformer** · **Pattern:** a `<modid>.accesswidener` referenced from `fabric.mod.json`, with intermediary targets: `accessible	class	net/minecraft/class_759$class_5773` · **Error:** `X is not public in Y; cannot be accessed from outside package` · **Fix:** translate each line to an AT entry against the **1.21 official** name (`public net.minecraft.client.renderer.ItemInHandRenderer$HandRenderSelection`) — and **check the target still exists**, because several AW entries are for members the 1.21 rework deleted outright (`EnchantmentHelper$EnchantmentVisitor` is gone). Read **#139** before adding any AT line: one entry for a member that no longer exists silently truncates the whole Minecraft recompile and yields ~12,000 bogus "Minecraft is missing" errors — diagnose by counting classes in the recompile jar (healthy 1.21.1 = **9766**), never by reading the errors.

152. **🔴 A Fabric mixin's `method=` strings are YARN names and are INVISIBLE to javac** ·
   **Pattern:** `@Inject(method = "isAcceptableItem", …)`, `@Accessor("tradeOffers")` — Fabric mixins target **Yarn** member names and ship a `*-refmap.json` that remaps them at load. The migration deletes the refmap (NeoForge runs on official mappings), and the remapper in #146 rewrites *code* but **not** annotation string literals ·
   **Runtime:** `InvalidInjectionException` / `InvalidAccessorException: No candidates were found matching …` at mixin APPLY → the mod refuses to load. Compiles perfectly ·
   **Fix:** retarget **every** `method=` / `@Accessor` / `@Invoker` string to the 1.21 Mojang-official member, and re-verify the descriptor (the same class of break as **#136**/**R14**/**R17**, but for the whole mixin set at once rather than a few). The mixin *class* names are Yarn too and are a useful map of what each targets (`PersistentProjectileEntityMixin`→`AbstractArrow`, `PlayerEntityMixin`→`Player`, `CraftingScreenHandlerMixin`→`CraftingMenu`, `PiglinBrainMixin`→`PiglinAi`, `InGameHudMixin`→`Gui`, `HeldItemRendererMixin`→`ItemInHandRenderer`, `ModelPredicateProviderRegistryMixin`→`ItemProperties`, `ItemEntryMixin`→`LootItem`, `ArmorFeatureRendererMixin`→`HumanoidArmorLayer`, `InGameOverlayRendererMixin`→`ScreenEffectRenderer`). Also set `compatibilityLevel` from Fabric's habitual `JAVA_8` to `JAVA_21`, delete the `refmap` key, and delete any `plugin` (`IMixinConfigPlugin`) whose only job was gating Fabric-only mixins. **A mixin you cannot faithfully retarget is better DELETED (and recorded) than left mistargeted** — a mistargeted mixin is a load crash, a missing one is a missing feature.

153. **Fabric datapack namespaces: `data/fabric/tags/items/*` → vanilla + `c:` (and the 1.21 `enchantable/*` trap)** ·
   **Pattern:** `data/fabric/tags/items/{swords,axes,pickaxes,bows,crossbows,shields,helmets,chestplates,leggings,boots}.json` — the Fabric conventional-tag namespace, plus optional `data/{origins,curios,environmentz}/tags/items/` ·
   **Runtime:** silent — the `fabric:` namespace means nothing on NeoForge, so every tag is empty ·
   **Fix:** the *Fabric* analogue of §142/§453's `#forge:`→`#c:` rule, and it has a **bonus trap on 1.21**: point the gear at the **vanilla** tags first (`#minecraft:swords`/`axes`/`pickaxes`, `#minecraft:{head,chest,leg,foot}_armor`), mirror to NeoForge's `c:tools/{bow,crossbow,shield,spear,melee_weapon,mining_tool}` / `c:armors/*`, and — the part nothing else will tell you — **populate `#minecraft:enchantable/*`** (`weapon`, `sword`, `sharp_weapon`, `fire_aspect`, `mining`, `mining_loot`, `bow`, `crossbow`, `durability`, `armor`, `equippable`, `foot_armor`). 1.21 drives enchantability entirely off those tags, so a gear mod that skips them ships weapons and armour to which **no vanilla enchantment can ever be applied** — no error, no crash, and no gate catches it except an explicit assert. (the Fabric weapons mod: 151 weapons; the Fabric armour mod: 284 armour pieces.)

154. **A Fabric gear mod's custom `Enchantment` subclasses are USUALLY empty — which makes the brutal 1.21 enchantment rework easy** ·
   **Pattern:** dozens of `class FooEnchantment extends Enchantment { protected FooEnchantment(Rarity, EnchantmentCategory, EquipmentSlot[]) { super(...); } }` registered in a big `switch`, with all the actual behaviour living in mixins + an effects class keyed on level ·
   **Error:** `cannot inherit from final Enchantment` / `constructor Enchantment cannot be applied` / `cannot find symbol: class EnchantmentCategory` — 1.21 made enchantments a **datapack registry** with no subclassing at all ·
   **Fix:** check the bodies before despairing. If they are metadata-only (the Fabric weapons mod: 53 of 53; the Fabric armour mod: 19 of 19), the port is mechanical: **delete every subclass**, emit one `data/<ns>/enchantment/<id>.json` each (`description` as a `{"translate": …}` component so the existing lang keys still work, `supported_items`/`primary_items` as `#minecraft:enchantable/*` tags, `weight` from the old `Rarity`, `max_level`, `min_cost`/`max_cost`, `anvil_cost`, `slots`), add them to `#minecraft:tags/enchantment/{in_enchanting_table,on_random_loot,tradeable,non_treasure,…}`, and replace the registry map with an `EnumMap<Id, ResourceKey<Enchantment>>`. · **The key trick for the call sites:** a level lookup needs **no registry** — a stack's enchantments live in the `ENCHANTMENTS`/`STORED_ENCHANTMENTS` components keyed by `Holder<Enchantment>`, and `Holder#is(ResourceKey)` tests them, so `getLevel(Id, ItemStack)` works from item-property predicates, tooltip builders and mixins that have no `Level` in hand. That one helper collapses ~76 call sites in the Fabric weapons mod. Only *applying* an enchantment needs a real `Holder` (see #155). · **Behaviour change to record:** a per-enchantment `isEnabled` config can no longer decide whether the enchantment is *registered* (a datapack registry is fixed at load) — it can still gate every effect, so a disabled enchantment does nothing, but it is still present in the registry.
· **AUGMENT — the numbers, for enchantments that were real 1.20 subclasses:** `weight`/`anvil_cost` come from the old `Rarity` as COMMON 10/1, UNCOMMON 5/2, RARE 2/4, VERY_RARE 1/8; `slots` `MAINHAND`+`OFFHAND` is `"hand"`. The old cost methods are linear, so read them off: `getMinCost(l) = a + b*(l-1)` is `{"base": a, "per_level_above_first": b}`, and the very common `getMaxCost(l) = super.getMinCost(l) + 50` is `Enchantment`'s default `1 + 10*l` plus 50, i.e. `{"base": 61, "per_level_above_first": 10}`. An enchantment category defined by `item instanceof MyItem` becomes an item tag of the mod's own (`#<ns>:enchantable/<kind>`), and one whose items were damageable should also be added to `#minecraft:enchantable/durability` (Unbreaking/Mending) — leaving out items with no `MAX_DAMAGE`. Adding the enchantments to `#minecraft:non_treasure` is enough for table, random loot and trades on 26.2 (each of those tags includes it). (a shield-adding item mod: three shield enchantments whose behaviour lived in the subclasses and moved to a damage-event handler.)

155. **`ItemStack#enchant` needs a `Holder`, and some callers have no `Level` — the registry-access escape hatch** ·
   **Pattern:** an item overriding `getDefaultInstance()` to hand out an innately-enchanted stack (common in gear mods), or any static helper that enchants without a world ·
   **Error:** `incompatible types: ResourceKey<Enchantment> cannot be converted to Holder<Enchantment>` with no `HolderLookup.Provider` anywhere in scope ·
   **Fix:** a tiny `<pkg>.ModRegistryAccess`-style helper: `ServerLifecycleHooks.getCurrentServer().registryAccess()`, falling back on the client to `Minecraft.getInstance().level.registryAccess()`, **returning null** when neither exists. Callers treat null as "skip the enchanting" and return the plain stack. ⚠️ Do **not** cache the provider in a static at mod load — it must be re-read per call, or you hand out holders from a previous world after a reload.

156. **Fabric-only extended-reach mods (`reach-entity-attributes`) → vanilla 1.21 attributes** · **Pattern:** an item that grants extra reach branches on `FabricLoader.isModLoaded("reach-entity-attributes")`, adding `ReachEntityAttributes.REACH` + `.ATTACK_RANGE` when present and its own registered fallback attribute otherwise; a mixin also injects both into `LivingEntity.createLivingAttributes()` · **Error:** `package com.jamieswhiteshirt.reachentityattributes does not exist` · **Fix:** MC 1.21 has **both** natively — `Attributes.BLOCK_INTERACTION_RANGE` (the old `REACH`) and `Attributes.ENTITY_INTERACTION_RANGE` (the old `ATTACK_RANGE`) — and both are already in every player's default supplier, so **delete the mod's own attribute registry, delete the createLivingAttributes mixin, and delete the `isModLoaded` branch**, aliasing the mod's constants to the vanilla `Holder<Attribute>`s. ⚠️ **Faithfulness check:** grant BOTH, not just the attack one. The natural minimal port keeps only `ENTITY_INTERACTION_RANGE` (that is what "attack range" reads like) and silently drops block reach, which the Fabric build DID grant whenever the reach mod was installed. (the Fabric weapons mod's whip/spear/glaive/staff.)

157. **A 1.20 override that merely reimplements what vanilla LATER adopted should be DELETED, not ported** · **Pattern:** `ShieldItem` subclass overriding `getDescriptionId(ItemStack)` to append the banner colour, reading it out of block-entity NBT (`BlockItem.getBlockEntityData(stack)` + `getColor(stack)`) · **Error:** `cannot find symbol: method getColor(ItemStack)` / `getBlockEntityData(ItemStack)` · **Fix:** delete the override. 1.21's own `ShieldItem.getDescriptionId(ItemStack)` is now exactly that logic driven off the `BASE_COLOR` data component. Generalise the check: when an override exists only to add behaviour vanilla has since absorbed, deleting it is the *faithful* port — porting it re-adds a now-redundant (and possibly divergent) implementation. Confirm by reading the 1.21 vanilla method before deleting. (the Fabric weapons mod's shield base class.)

158. **`Item.TooltipContext` collides with the decompiler's nested-only `Item.Properties` import (companion to #117)** · **Pattern:** Vineflower imports only the nested type — `import net.minecraft.world.item.Item.Properties;` — and never `Item` itself, because the 1.20 source only used `Properties` · **Error:** after the #117 codemod, `package Item does not exist` at `Item.TooltipContext` (javac reads `Item.TooltipContext` as a package-qualified name) · **Fix:** add `import net.minecraft.world.item.Item;` **alongside** the nested import (both may coexist). Fold this into the #117 codemod: any file that gains `Item.TooltipContext` needs the top-level `Item` import. (the Fabric weapons mod: 18 of one agent's 77 errors were this single cause.)

159. **`ItemAttributeModifiers` must be built in a STATIC helper, and it REPLACES rather than extends (companion to #118)** · **Pattern:** #118 says "set the modifiers on `Properties.attributes(...)` at construction" — but the values come from the constructor's own parameters, so they must be computed *inside* the `super(...)` call · **Error:** `cannot reference this before supertype constructor has been called` · **Fix:** put the builder in a **`static`** helper on a shared base class and call it in the `super(...)` argument list. Three sub-traps: (a) vanilla's `SwordItem/DiggerItem.createAttributes(tier, dmg, spd)` **already adds `tier.getAttackDamageBonus()`**, so pass the bonus-free damage exactly as the old ctor took it or you double-count; (b) `ItemAttributeModifiers.builder()` is not super-aware, so an item adding a THIRD modifier must re-add the damage/speed pair itself — which matches the old `getDefaultAttributeModifiers` override, that also fully replaced the super map; (c) every modifier needs its own `ResourceLocation` id (#51) — `Item.BASE_ATTACK_DAMAGE_ID`/`BASE_ATTACK_SPEED_ID` for the vanilla pair, a mod-namespaced one for extras. (the Fabric weapons mod's custom-weapon base class.)

160. **`hurtAndBreak` keyed by an `InteractionHand`, not a fixed slot (augment of #32)** · **Pattern:** `stack.hurtAndBreak(1, player, p -> p.broadcastBreakEvent(context.getHand()))` — the callback broadcasts for the *hand the interaction used*, so #32's blanket `EquipmentSlot.MAINHAND` substitution is wrong for `useOn`/`use` paths · **Error:** `EquipmentSlot is not a functional interface` · **Fix:** `stack.hurtAndBreak(1, player, LivingEntity.getSlotForHand(context.getHand()))` (public static in 1.21). (the Fabric weapons mod's sword base class, `useOn`.)

161. **`CrossbowItem` charged-projectile statics are components (augment of #100)** · **Pattern:** `CrossbowItem.containsChargedProjectile(stack, Items.FIREWORK_ROCKET)`, `CrossbowItem.isCharged(stack)` · **Error:** `cannot find symbol: method containsChargedProjectile` · **Fix:** `stack.getOrDefault(DataComponents.CHARGED_PROJECTILES, ChargedProjectiles.EMPTY)` then `.contains(Item)` / `.isEmpty()`. Also `CrossbowItem.getChargeDuration(stack)` and `ItemStack.getUseDuration()` both gained a trailing `LivingEntity`. (the Fabric weapons mod's crossbow class and client item-property predicates.)

162. **Mixin `this instanceof <Target>` / `(Target) this` — the mixin class does not extend its target** · **Pattern:** a Yarn/Fabric mixin body written against the target as if it *were* the target: `if (this instanceof Player user) {…}`, `if (this instanceof ServerPlayer p)`, `((LivingEntity) this).getHealth()`. Perfectly legal in the ORIGINAL project (its mixin declared `abstract class LivingEntityMixin extends LivingEntity`), but a decompiled/re-scaffolded mixin usually declares a bare `public class XMixin` with `@Mixin(X.class)`. · **Error:** `incompatible types: LivingEntityMixin cannot be converted to Player` / `… to ServerPlayer` / `… to LivingEntity` — javac only, always the LAST bucket left because mixins are fixed last. · **Fix:** cast through `Object`, the standard mixin idiom: `(Object) this instanceof Player user`, `(LivingEntity) (Object) this`. (Alternative: make the mixin `abstract class XMixin extends X` — but that drags in every abstract member, so the `(Object)` cast is preferred.) **Scan:** `grep -rn 'this instanceof\|(\w*Entity) this' src/main/java | grep -v '(Object) this'` under `mixin/`. (the Fabric weapons mod's `LivingEntityMixin` — the final 6 of 636 errors.)

163. **Client-only `I18n` reached from SHARED (common) code — dedicated-server class-load crash** · **Pattern:** a tooltip/name helper in a common package does `net.minecraft.client.resources.language.I18n.exists(key)` (or `I18n.get`) to discover how many numbered tooltip lines a key family has: `for (…; I18n.exists(key + i); i++)`. Faithful from Fabric, where the same class also lives client-side and the mod's `Environment` annotations were dropped in the port. · **Runtime:** `RuntimeException: Attempted to load class net/minecraft/client/resources/language/I18n for invalid dist DEDICATED_SERVER` — one per item, the instant anything builds a tooltip server-side. Compiles clean (the class exists at compile time); NeoForge's dist-stripping only trips at *load* time. · **Fix:** `I18n` is a thin client-side delegate — `I18n.exists(k)` is literally `Language.getInstance().has(k)` and `I18n.get(k)` is `Language.getInstance().getOrDefault(k)`. Swap to **`net.minecraft.locale.Language`** (common, present on both dists) for byte-identical client behaviour and a server that loads. · **Scan:** `grep -rn 'import net\.minecraft\.client\.' src/main/java | grep -v '/client/'` — any client import outside a dist-gated client package is a hit. **Gate:** Gate B (`runGameTestServer`) — it is the only headless gate that actually loads these classes on a dedicated server. (the Fabric weapons mod's tooltip helper; independently hit by the sibling Fabric armour mod.)

164. **Gate-B failure messages over 1024 chars crash chunk-save and MASK the real failure** · **Pattern:** the recommended "parameterize over every registered item/entity" gate collects failures into a list and asserts `helper.assertTrue(failures.isEmpty(), String.join("\n", failures))`. With 150+ items failing for one shared reason, the message is tens of KB. · **Runtime:** GameTest writes the failure message into a **lectern book** `ItemStack` component; the 1.21 codec caps that string at 1024 — `DataResult$Error.getOrThrow: "<message>" is too long: 22082, expected range [0-1024]` thrown from `LecternBlockEntity.saveAdditional` during `ChunkMap.save` at server shutdown. The build fails with a *chunk-save* stack trace and the actual per-item errors are buried above it. · **Fix:** log the full list (`LOGGER.error` per failure) and assert on a **truncated summary**: `helper.fail(summary.length() > 512 ? summary.substring(0,512) : summary)`. Make this the default shape of every namespace-parameterized gate test. **Scan:** `grep -rn 'String.join' src/main/java/**/test/*.java`. (both Fabric gear mods' Gate B.)

165. **Verify every mixin `method = {"…"}` name with `javap` against the NeoForm recompile jar (the §P #152 done-gate)** · **Pattern:** after a Yarn→Mojang mixin retarget you have dozens of `method = {"actuallyHurt"}`-style strings. javac cannot check them; a wrong one is a **hard crash at mixin APPLY** (or, worse, a silent no-op if `require = 0`), and Gate B only proves the mixins whose target classes the *dedicated server* happens to load — every client-only target (`HumanoidArmorLayer`, `ItemInHandRenderer`, `GameRenderer`) is unverified until Gate C. · **Fix:** don't eyeball them — resolve them mechanically. `build/neoForm/*/steps/recompile/outputs.jar` is the exact 1.21.1 Mojang-mapped Minecraft the mod compiles against; parse each `@Mixin(X.class)` + its `method` strings out of the source and check them (walking superclasses) with `javap -p -cp <that jar> <fqn>`. For a mixin that pins a **full descriptor** (`method = {"renderArmorPiece(Lcom/mojang/…;…)V"}`), diff it against `javap -p -s` output — an overload added in 1.21 makes the bare name ambiguous, which is why the descriptor is there, and a stale descriptor fails to bind. Run it as a done-gate before Gate C so a client mixin can't be the thing that crashes the client. (the Fabric weapons mod: 26 targets; the Fabric armour mod: 12 targets + the `HumanoidArmorLayer.renderArmorPiece` descriptor — all confirmed.)


## N. Re-porting a deferred OPTIONAL integration (recipe viewers: JEI / REI / EMI, and similar)
> **Axis:** an optional cross-mod integration the first-pass migration **parked** (compiled the mod without it) to reach a green build. Recorded at defer-time in `MIGRATION.md` ("JEI plugin removed (N classes) — faithful re-port deferred") and/or `MANUAL_VALIDATION.md`. This is the come-back-and-light-it-up playbook. Companion catalog entry: **#121** (the JEI API deltas).

**1. Find it.** The dep is `type="optional"` in the ORIGINAL `mods.toml` (JEI=`jei`, REI=`roughlyenoughitems`, EMI=`emi`, Jade=`jade`, TOP=`theoneprobe`). Grep the `decompiled-raw` toml **and** the migration's `MIGRATION.md` for "deferred"/"removed … integration". The code is **client-only**, usually under `client/jei/` — a `@JeiPlugin implements IModPlugin` + `IRecipeCategory` classes. (Note: many MCreator mods declare `jei` optional but ship **no plugin** — JEI auto-shows their crafting/smelting/brewing recipes, so "lighting up" is just re-adding the toml dep, nothing to port.)

**2. Recover the original source.** If the migration deleted it (`decompiled-raw` is gitignored/gone), pull the classes + textures from git rather than re-decompiling: `git show <removal-commit>^:<path>` for each file (find the commit via `git log --oneline -- mods/<modid> | grep -i jei`).

**3. Wire the dependency — compile against the API, supply at runtime.** Add the viewer's maven (JEI = `https://maven.blamejared.com`) and:
   `compileOnly "mezz.jei:jei-${minecraft_version}-neoforge-api:${jei_version}"` + `runtimeOnly "mezz.jei:jei-${minecraft_version}-neoforge:${jei_version}"`.
   Use the **`-api`** artifact for compile so you physically **cannot** lean on JEI internals — that's what forces the `setBackground` fix instead of `mezz.jei.library.RecipeSlot` (#121). Re-add the optional dep to `neoforge.mods.toml` (`type="optional"`; `side="CLIENT"` for a recipe viewer). Match `jei_version` to the version the player runs, else the newest for that MC.

**4. Port the plugin** — apply catalog **#121** (the JEI 15→19 / 1.21 API deltas).

**5. Test it BOTH ways — the whole point of an optional dep is that it works present AND absent:**
   - **Server, viewer ABSENT** (`runGameTestServer` with the mod + its REQUIRED deps, but NOT JEI): proves the `@JeiPlugin` + optional dep don't break load when the viewer isn't installed. Must stay green.
   - **Client, viewer PRESENT** (`client-boot-loop` `launch spawn`, with the mod + required deps + **the JEI jar** added to `-Psmokejars`): **a non-crash is NOT sufficient, and neither is registration alone** — JEI calls a category's `setRecipe()`/`draw()` only when the recipe is actually **opened**, so a render-time bug (bad `blit`, NPE in `draw`, the entity-preview render) is invisible until then. **Two levels of assertion, do BOTH:**
     - *Registration (necessary):* grep `$SIG_DIR/launch.log` for `Registering categories: <modid>:<plugin>` **and** an `Adding recipes: RecipeType[uid=<modid>:…, recipeClass=…<your recipe class>]` line per category, with **zero** `error/exception/Caught` lines naming your `client.jei` classes. (`spawn` is required, not just `launch` — `registerRecipes` runs off the loaded world's `RecipeManager`.)
     - *Render (the real proof for a CUSTOM category):* actually **open** the categories so `setRecipe()`/`draw()` run. The smoke-harness has this built in — **`JeiRecipeProbe`** (a dormant `@JeiPlugin` that captures JEI's runtime) + **`-Psmokejeirecipes="<modid>:<recipe_uid>,…"`** (env `SMOKEJEIRECIPES`). After world load it resolves each uid via `IRecipeManager.getRecipeType` and calls `IRecipesGui.showTypes(...)` — **throws (FAILs) if a uid isn't in JEI**, else the recipe GUI renders for the SURVIVE ticks. The pass line `[JEI] opened N recipe categor(ies) … setRecipe()/draw() now rendering` + a clean `PASS — … no crash` proves the render path works. (Set `SMOKENS=""` to skip spawning and focus on the recipe GUI.) This proves it renders without crashing/erroring; it does NOT judge visual correctness (slot spacing, entity-preview look) — that stays a human eyeball check.
     - *Content (does JEI actually SHOW the recipes?):* a category can register + open yet be **empty** (e.g. its recipe JSON didn't load — catalog #122). And a mod with **no custom category** (MCreator: recipes ride vanilla crafting/smithing) has no uid to open. For both, use **`-Psmokejeiverify="<modid>:<output_item_id>,…"`** (env `SMOKEJEIVERIFY`) → `JeiRecipeProbe.countRecipesProducing` builds an OUTPUT `IFocus` and counts recipes across ALL categories producing each item; **FAILs on 0**. This is the check that caught 5 mods' recipes silently not loading. Verify at least one output item per recipe kind the mod adds.

**6. Deploy caveat — don't over-claim.** If the player hasn't installed the recipe viewer, the integration is **dormant**: the rebuilt jar changes nothing visible until they add JEI/REI/EMI. Say so plainly; don't promise a visible change that depends on a mod they don't have.

## M. Minor-version deltas (1.21.x → 1.21.y) — filled on demand
> **Axis:** specialized minor-version delta (same family, e.g. 1.21.1↔1.21.4). Companion: `.claude/skills/migrate-mod/references/minor-version-deltas.md`.

**Filled from the first real 1.21.x↔1.21.y crossing** (a small MCreator food mod, a NeoForge **1.21.4 → 1.21.1 downport** —
`mods/<modid>/`). The main corpus is still the Forge 1.20.1 → NeoForge 1.21.1 path
(§A–§L/§R above). When you do a 1.21.x↔1.21.y up-port/downport: SKIP the SRG remap (source is official-mapped) and
the loader transform (same loader), set the `DST_MC` knobs (gradle.properties versions, GameTest DataVersion,
pack_format), then append each new delta here in the standard 3-line format and mirror it into
`references/minor-version-deltas.md`. **A downport applies these in reverse** (remove the 1.21.2+ API, restore the
1.21.1 one); an up-port applies them forward.

M1. **Entity render-state system (1.21.2+, client — the biggest downport bucket)** · **Pattern (1.21.2+):**
`MobRenderer<T, LivingEntityRenderState, M>` (3 type args) + `createRenderState()`/`extractRenderState(T,state,pt)`;
model `extends EntityModel<LivingEntityRenderState>` with `EntityModel(ModelPart root)` super ctor +
`setupAnim(LivingEntityRenderState)`; `getTextureLocation(RenderState)` · **Error (downport):** `package
net.minecraft.client.renderer.entity.state does not exist`; `MobRenderer` `wrong number of type arguments; required
2`; model `is not abstract and does not override abstract method setupAnim(…,float,float,float,float,float)` ·
**Fix (→1.21.1):** `MobRenderer<T, M>` (2 args), delete `create/extractRenderState`; model `extends
HierarchicalModel<T extends Entity>` storing a `root` field + `root()` override + `setupAnim(T entity,
float limbSwing, float limbSwingAmount, float ageInTicks, float netHeadYaw, float headPitch)`; render-state field
reads (`state.ageInTicks`) become the method params; `getTextureLocation`/`isShaking` take the entity. Fold any
renderer-side `AnimatedModel.setupAnim` animation into the model's `setupAnim`.
M2. **`Registry.getValue(ResourceLocation)` (1.21.2+ rename)** · **Pattern (1.21.2+):**
`BuiltInRegistries.X.getValue(rl)` · **Error (downport):** `cannot find symbol: method getValue(ResourceLocation)`
· **Fix (→1.21.1):** the nullable lookup is `registry.get(ResourceLocation)` (the mirror of catalog #70; `getValue`
was the 1.21.2 rename). (Do NOT touch `AttributeInstance.getValue()` / `DataResult.getOrThrow` — unrelated.)
M3. **`Entity.spawnAtLocation(ServerLevel, ItemStack)` (1.21.2+ added the level arg)** · **Pattern (1.21.2+):**
`this.spawnAtLocation(serverLevel, stack)` · **Error (downport):** `no suitable method found for
spawnAtLocation(ServerLevel,ItemStack)` · **Fix (→1.21.1):** drop the `ServerLevel` — `spawnAtLocation(stack)`
(the level is implicit via `this.level()`); same for the `ItemLike`/`(stack,float)` overloads.
M4. **`EntityType.Builder.build(ResourceKey)` (1.21.2+)** · **Pattern (1.21.2+):**
`builder.build(ResourceKey.create(Registries.ENTITY_TYPE, id))` · **Error (downport):** `no instance(s) of type
variable(s) T exist so that ResourceKey<T> conforms to String` · **Fix (→1.21.1):** `builder.build(String id)` —
pass the plain registry-name string.
M5. **`new SpawnEggItem(EntityType, Properties)` (1.21.2+ dropped the color args)** · **Pattern (1.21.2+):**
2-arg `new SpawnEggItem(type, properties)` (colors live in the `assets/<ns>/items/*.json` tints) · **Error
(downport):** `constructor SpawnEggItem cannot be applied to given types` (+ `EntityType<Entity> cannot be
converted to EntityType<? extends Mob>`) · **Fix (→1.21.1):** `new SpawnEggItem(EntityType<? extends Mob> type,
int backgroundColor, int highlightColor, Properties)` — recover the two ARGB ints from the mod's
`items/*_spawn_egg.json` `tints`; drop any raw `(EntityType)` cast so the `? extends Mob` bound is kept. **Also add
a `models/item/<egg>.json` (`parent: minecraft:item/template_spawn_egg`)** — 1.21.1 ignores the `items/` client-item
dir and renders eggs from `models/item/`, so a 1.21.4 egg that shipped only an `items/` def is missing-texture
without it.
· **AUGMENT — the same raw `(EntityType)` cast breaks spawn placements:** `RegisterSpawnPlacementsEvent.register((EntityType) X.get(), …, (t, w, r, p, rand) -> Mob.checkMobSpawnRules(t, w, r, p, rand), …)` fails with `incompatible types: EntityType<Entity> cannot be converted to EntityType<? extends Mob>`. Drop the raw cast there too, so the type argument infers as the concrete mob type (2 sites in one downport).
M6. **Recipe ingredient bare-string shorthand (1.21.2+ data)** · **Pattern (1.21.2+ JSON):** `"ingredients":
["ns:id", …]` / smelting `"ingredient": "ns:id"` (bare strings; `"#ns:tag"` for tags) · **Runtime (downport, NOT a
compile error — silently no-loads):** `Failed to parse either. First: Not a json array … Second: Not a JSON object …
No ingredients for shapeless recipe` at datapack load · **Fix (→1.21.1):** every ingredient must be an **object** —
`{"item": "ns:id"}` (tag → `{"tag": "ns:tag"}`, alternatives → array of those objects). Recipe **results** are
`{"id": X, "count": N}` on BOTH versions — leave them. (Same shape as catalog §J #93's ingredient note; a
`runGameTestServer` reload surfaces it — grep the log for `JsonParseException`/`No ingredients`.) `pack.mcmeta`
pack_format for 1.21.4 = data **61** / resources **46**; 1.21.1 = data **48** / resources **34** (§W18).

M26. **Block light overrides changed shape in 1.21.2, so a DOWNPORT orphans them silently** · **Pattern:** a 1.21.2+ block overriding `public boolean propagatesSkylightDown(BlockState state)` and `public int getLightBlock(BlockState state)`, usually with no `@Override` (the decompiler drops it) · **Symptom:** none at compile, load or in a spawn test: on 1.21.1 the one-argument methods override nothing, vanilla's defaults run, and a see-through block (a plate of food, a banner) darkens what is under it · **Fix:** `propagatesSkylightDown(BlockState, BlockGetter, BlockPos)` and `getLightBlock(BlockState, BlockGetter, BlockPos)`, with `@Override` (verify with `javap` on `build/neoForm/*/steps/recompile/outputs.jar`). Measured on a blind replay of a small MCreator mod downported 1.21.4 → 1.21.1: 16 methods in 8 blocks, and a GameTest asserting light transparency fails on the old shape and passes on the new. · **Scan:** `python3 tools/override-probe.py .` finds this whole class of dead override, not only these two methods.

M27. **`MoveControl.Operation` is a protected nested type on 1.21.1** · **Pattern:** 1.21.2+ MCreator mob code importing `import net.minecraft.world.entity.ai.control.MoveControl.Operation;` and using it inside a `new MoveControl(this) { … }` subclass · **Error:** `Operation has protected access in MoveControl` (at the import) · **Fix:** delete the import; inside the anonymous `MoveControl` subclass `Operation` resolves as an inherited member type (or write `MoveControl.Operation.MOVE_TO`). Measured: 2 mobs in one downport.

M28. **MCreator's generic payload registration fails wildcard capture on 1.21.1** · **Pattern:** MCreator's network setup iterating a `Map<CustomPacketPayload.Type<?>, NetworkMessage<?>>` and calling `registrar.playBidirectional(id, message.reader(), message.handler())`, where the record stores a `StreamCodec<? extends FriendlyByteBuf, T>` · **Error:** `method playBidirectional in class PayloadRegistrar cannot be applied to given types` · **Fix:** 1.21.1 wants `StreamCodec<? super RegistryFriendlyByteBuf, T>` and the two wildcard captures cannot be unified, so raw-cast the three arguments: `registrar.playBidirectional((CustomPacketPayload.Type) id, (StreamCodec) reader, (IPayloadHandler) handler)` (confirm the signature with `javap` on the NeoForge universal jar). Every MCreator 1.21.2+ downport carries this boilerplate; in the measured mod the map was empty, so the fix is compile-only.

### 1.21.4 → 1.21.1 (downport) — filled from the Registrate-based framework library / furniture mod downport (M7-M25)
Full details + the exact 1.21.1 signatures are in `references/minor-version-deltas.md`. `pack_format` 1.21.4 data=61 → 1.21.1 34/48.
- **M7. ScheduledTickAccess split (1.21.2)** — `cannot find symbol: ScheduledTickAccess`; in 1.21.1 the scheduled-tick methods are on `LevelAccessor` (no `createTick`). Drop the type/param.
- **M8. `Orientation` on neighbor methods (1.21.2)** — `cannot find symbol: Orientation`; `neighborChanged`/`updateShape`/`updateNeighborsAt*` use `BlockPos` in 1.21.1, not `net.minecraft.world.level.redstone.Orientation`.
- **M9. `Block.updateShape` signature (1.21.2)** — 1.21.1 is `updateShape(BlockState, Direction, BlockState, LevelAccessor, BlockPos, BlockPos)` (no ScheduledTickAccess/RandomSource; different arg order).
- **M10. `BlockBehaviour.useItemOn` return (1.21.4)** — `cannot override ... useItemOn`; 1.21.1 returns `net.minecraft.world.ItemInteractionResult`, not `InteractionResult`.
- **M11. `ServerEntityGetter` (1.21.2)** — `cannot find symbol: ServerEntityGetter`; the nearest-player/entity queries are on `EntityGetter` in 1.21.1 (no `Level.getLevel()`).
- **M12. Furnace `RecipeAccess`/`FuelValues` (1.21.2)** — new furnace/recipe types; 1.21.1 uses `RecipeManager` + `AbstractFurnaceBlockEntity.getFuel()`.
- **M13. Loot/lock/id API (1.21.2)** — `Properties.setId` (drop), `LockCode.fromTag/addToTag` are 1-arg (no Provider), `EntitySpawnReason`→`MobSpawnType`.
- **M14. Datagen reorg (1.21.4)** — `net.minecraft.client.data.models` / `net.minecraft.util.context` don't exist in 1.21.1; exclude the build-time datagen tree (`sourceSets.main.java.exclude`).
- **M15. `getCloneItemStack` (1.21.2)** — 1.21.1 has no trailing `boolean includeData`.

## S. Silent-resource patterns (compile clean, run clean, *silently wrong*)
> **Axis:** neither the compiler nor the crash gates can see these. Java has a compiler; resources do
> not, and a mis-filed data file produces **no error, no crash, and no log line** — it simply doesn't
> exist. §R is "compile clean, crash at run". This is the third axis: everything green, mod broken.

**S1. Pre-1.21 datapack directories — the whole data layer, silently dead.** · **Pattern:** a ported
mod keeps `data/<ns>/{recipes,advancements,loot_tables,structures}` and `data/<ns>/tags/{blocks,items,
fluids,entity_types}` · **Symptom:** *nothing.* It compiles, loads, spawns mobs, passes GameTests, and
every recipe/advancement/loot table/tag inside those folders does not exist. · **Fix:**
`tools/fix-datapack-layout.py <mod> --apply` (renames + the 1.21 recipe-codec rewrite; see §N (b) for
the JSON shapes). · **Detection:** `--verify` is wired into the template's `check`. Positively, compare
`Loaded N recipes` on boot with and without the mod — the delta is the mod's real contribution, and
absence of errors proves nothing. · **Swept 2026-08-10:** all 19 ports scanned; only a space-exploration mod (1795
files) and a weapon mod (61) were affected — the rest were already clean, as were all 1362 recipes
across the repo (no legacy `{"item":…}` or bare-string results left anywhere). Three ports still carry
`data/forge/**` (the space mod 43, a ~750-file boss mod 7, one other port 4); those are in the correct singular folders
and NeoForge's own `c:` umbrellas bridge `#forge:*` with `required:false`, so they work — legacy, not
broken.

**S1b. …and the same file's OTHER half — §143's loot codecs — was documented, unenforced, and live in
two shipped ports.** The directory rename above is only step one; fixing it is what *unmasks* §143
(catalog §144), and nothing checked the second half. Measured 2026-09-03, found not by a scan but by
finally running a Gate C with the mod actually loaded: **10 loot tables in a ~750-file boss mod and 1 in
one other port**, i.e. every "infected"-variant mob in a widely played mod dropped **nothing**, for weeks,
with all three gates green. The failure is a data-load ERROR line followed by silence — the table is
logged and then ignored, so there is no crash and no test to fail.
`tools/fix-datapack-layout.py` now rewrites `looting_enchant` → `enchanted_count_increase` (+ an
explicit `enchantment`), `random_chance_with_looting` → `random_chance_with_enchanted_bonus`, and the
`killer`/`killer_player` loot-context targets — **and `--verify` fails on them**, so the enforcement
matches the rename's. It runs even when the layout is already clean, because a port that passed the
directory check months ago can still be shipping dead tables.
· **One rule is deliberately NOT automated:** `set_nbt` → `set_components` changes payload *shape*,
not just name, so a rename alone leaves a function with no argument — valid JSON, still dead, and now
it *looks* fixed. It is reported for a human instead. **A codemod that quietly produces a wrong
result is worse than one that refuses**, and this is the §X1 dead-rule detector's mirror image: there,
a rule that matches nothing is invisible; here, a rule that matches too eagerly is invisible.

**S2. The lesson that actually generalises: a step you must *remember* is not a control.** §N already
documented S1's rename, named the exact fix, and said *"add a datapack-layout check to every migration's
Step 4"* — and the very next port shipped broken anyway. Nothing was forgotten because anyone was
careless; the compile loop has a forcing function (an error count that must reach zero) and the datapack
has none, so it loses every time. **When a lesson lands, don't write it down — wire it into something
that runs.** A space-exploration mod (~550 files) reached a seven-year-old as "I can't work out how to build a rocket" (there was no
recipe in his game to find), and a weapon mod (~240 files) shipped 17 recipes that never loaded.

**S3. Corollary — a dependent mod can mask the diagnosis.** The first fix attempt shimmed the missing
tags in from a *different* mod (tags merge, so it worked). It restored 82 tags and left 1713 recipes,
advancements and loot tables dead, while making the symptom go away. If a fix lives outside the mod
that's broken, it's a workaround; put it where the defect is.

**S4. A LIBRARY method that quietly stopped having a side effect — the code-side cousin of S1.**
· **Pattern:** the port bumps a bundled library across a **major** version (it must: the old one doesn't
run on 1.21), and the compile loop resolves each break at the level the compiler complains at — usually a
**rename**. A space-exploration mod (~550 files): Resourceful Lib 2.x's `ResourcefulCreativeTab#build()` **registered** the tab as
part of building it; 3.x's `ResourcefulCreativeModeTab#build()` only **constructs** a `CreativeModeTab`
and leaves registering to the mod. The migration changed the class name, compiled clean, and produced a
tab object that was in **no registry at all** — 423 items with nowhere to be found in the creative menu.
· **Symptom:** *nothing.* No log line, no crash, no missing-texture. The mod looks like a mod that never
had a creative tab. · **Fix:** put it through the registry the rest of the mod already uses
(`ResourcefulRegistry<CreativeModeTab>` + `init()`; a `DeferredRegister` elsewhere).
· **The generalisation, which is the point:** **javac checks names and types; it cannot check side
effects.** Wherever a library's contract was "…and this also registers/subscribes/schedules it", a
rename-level fix silently deletes the "also". A MC-version jump forces a major bump of *every* library at
once (resourcefullib, a fluid/energy transfer library, a Registrate-based framework library/Registrate, citadel, puzzleslib, balm, curios, GeckoLib…), so
this surface is as wide as the dependency list. · **Step 4b duty:** for each library bumped a major
version, diff the **changelog/API of the specific calls the mod makes** — not the whole library — and ask
of each one *"did this used to do something for me besides return a value?"* Registration, event
subscription, tab/menu/renderer binding and data-generator hookup are the usual carriers.
· **Static scan:** catalog-scans.md `S4` (declares `itemGroup.*` lang keys but registers no tab). It has a
known false positive — Registrate-style libraries register on your behalf (a Registrate-based framework library and the furniture mod built on it) —
which is exactly why the real control is the runtime census below.

**S5. The control: a port-completeness CENSUS, because a count is not a comparison.** Every gate in this
repo is a *crash* gate, and none of §S crashes. What was missing was any gate that asks **"is everything
the mod claims to have actually there?"** — and a space-exploration mod (~550 files) proves the difference: that port's Gate C log
literally reads *"creative tabs built"*, because `OPEN_CREATIVE` prints
`BuiltInRegistries.CREATIVE_MODE_TAB.size()` — a count of **everyone's** tabs, which cannot see the
absence of ours. · **The oracle that generalises:** the mod's own `assets/<modid>/lang/en_us.json` is a
**manifest written by the original author and carried through the port untouched**, so it's an
expectation the migration cannot silently edit. Every `item.`/`block.`/`entity.`/`effect.<modid>.<path>`
key must resolve to a registered id, and a file declaring any `itemGroup.*` key must own at least one
registered creative tab. · **Wired in (per S2):** the `CONTENT_CENSUS` gauntlet step in
`templates/neoforge-mod/test-templates/ClientBootSmokeTest.java.example`. Keys that aren't exactly
`<type>.<modid>.<path>` are skipped rather than reported (tooltips/subtitles/nested paths live there
too — a census that cries wolf gets switched off), and a genuinely dead key goes in
`CENSUS_KNOWN_ABSENT` **with a line in MIGRATION.md**. **Back-port it to any mod whose harness predates
this.** · **Wider reading:** the same "expected vs actual" shape covers the rest of §S — the port's own
`data/` tree and its recipe/loot/advancement counts are a manifest too.

**S5b. MEASURED: the tab half gates, the content half is advisory — a lang file is a *display-name*
file, not a registry manifest.** First real run of the census (`templates/smoke-harness`, `-Ptestmode=census`,
23 jars = the 17 ports + their required deps, **2026-08-11**): the creative-tab rule was right on **17/17**
namespaces, and the item/block/entity rule produced **135 flags and ZERO real findings.** Every flag was one
of five benign shapes, each verified by asking whether the id ever existed in the decompiled original
(`git log -S<id> -- mods/<m>/src/main/java` — a port's first commit *is* the original, so no hits means the
original never registered it either):
· **unimplemented content** the author shipped strings for — a Fabric weapons mod's `examplemod:sword_a`/`bow_b`/`hammer_c`
are in no version of that mod's Java;
· **per-colour / armour VARIANTS of one registered thing** — 16 `*_example_shield` keys and 16 models
over **one** item; a ~390-file mob mod's `examplemod:armored_example_skeleton` is a texture on its base skeleton mob, not an entity
(40 of that mod's 138 keys are this);
· **optional-integration items** — a space mod's `examplemod:guidebook` is the Patchouli guidebook, registered only with Patchouli;
· **stale names** — a weapon mod's `examplemod:sword_wraith` is really `examplemod:end_sword_wraith`, which IS registered;
· **MCreator leftovers** — keys for deleted elements (spawn eggs in an MCreator + GeckoLib mob mod and a large MCreator ocean mod).
An `itemGroup` key carries none of that ambiguity: a mod defines a tab or it doesn't. **So the census fails
only on the tab rule and prints content gaps as advisory.** Two lessons worth more than the numbers: an
oracle needs its false-positive rate *measured on a real corpus* before it is allowed to gate anything (this
one was written, documented, and shipped as a hard gate before anyone had run it — the §S2 mistake in a new
costume); and `git log -S<id>` against a port's own history is the cheap decisive test for "did we lose this,
or was it never there?"

**⚠️ ...but `git log -S` is only decisive on a COMPLETE clone, and a cloud session's is not.** The inference
above — *a port's first commit IS the original, so no hits means the original never registered it either* —
holds only when that first commit is in the history you are actually searching. Claude Code on the web clones
**shallow**: measured 2026-08-25, this repo arrived with **87 commits** and a `.git/shallow` present, and the
sibling content mod's repo arrived with **70 of its 411**. Against a truncated history the search returns no hits for an
id the original really did register, so the census verdict inverts — a genuine migration loss gets written off
as one of the five benign shapes, in the one direction that looks like good news. That is strictly worse than
the same bug in the sibling repo's wish corpus (shallow: 68 commits examined, **0** wishes mined; complete:
316 examined, **154** mined), because an empty result is visible and a false negative is not. So check before
you trust it — `[ -f .git/shallow ] && echo TRUNCATED` — and fix it with `git fetch --unshallow`, which cost
1.5s and 2.4MB on a repo this size. The general form is §S4's: the answer is wrong, not missing, and nothing
about the output says so.

**S6. A texture that is a JPEG (or WebP) NAMED `.png` — the author's mistake, inherited silently
by every port.** · **Pattern:** `textures/item/foo.png` whose magic bytes are JFIF or RIFF/WebP.
MCreator projects collect art by drag-and-drop, so a downloaded JPEG keeps its `.png` filename and
nobody notices · **Runtime:** `Using missing texture, unable to load <ns>:item/foo` +
`java.io.IOException: Bad PNG Signature` at resource load — an ERROR line the reload survives, so
the item just renders as the magenta cube and every gate stays green · **Fix:** re-encode, which
is a decision about the author's art rather than a migration step — so RECORD it (§V58b's
named-known-absent shape) unless the mod is yours to change.
· **Why it belongs here rather than in a mod's own notes:** it is invisible to a compile, to Gate
A, to Gate B, and to a `launch`-only Gate C, and a port CARRIES it across without ever touching
the file — so the first person to see it is a player wondering why one item is purple. It is also
the one §S check with no judgement in it at all: `file(1)` reads magic bytes, so a hit is a fact.
· **Measured across all 21 ports in this repo, 3 findings and 0 false positives** — two JPEGs in
a ~380-file MCreator mob mod (one of them `layer0` for TWO items) and a WebP in an MCreator + GeckoLib mob mod (~220 files), none
of which anyone knew about. The whole sweep:

```bash
find src/main/resources -name '*.png' -exec file {} + | grep -v "PNG image"
```

**S7. 🔴 A `META-INF/services` PROVIDER FILE dropped by the decompile — the service interface and
its implementation both port perfectly, and the wiring between them is gone.** · **Pattern:** a
mod using `ServiceLoader` for a platform abstraction (the standard Architectury/multiloader shape:
`ICapabilityHelper` + `NeoCapabilityHelper` + a static `load(...)` field) · **Symptom:** *nothing*
at compile time — both classes are ordinary Java and javac has no opinion about who declares whom.
At runtime `ServiceLoader.load(...).findFirst()` is empty, the holder's static initialiser throws,
and every caller after that gets `NoClassDefFoundError: Could not initialize class …Services`
rather than the original `NullPointerException`, so the message names a class that is fine and not
the file that is missing · **Fix:** `cp decompiled-raw/META-INF/services/* src/main/resources/
META-INF/services/`. The pristine decompile has it; the port's `src/main/resources` copy step is
what skipped it.
· **Measured on a ~320-file boss mod:** three blocks (`examplemod:block_a`, `block_b`,
`block_c`) crashed the moment they were placed — in real play, not only in a test — and
the port had been through a full compile, Gate A and a mixin audit without a word. **It failed
identically on BOTH targets**, which is X15's control column doing its job: a red that reproduces
on the canonical version was never the era jump's doing, and reading it as one would have sent the
afternoon into 26.2's capability rewrite (§V50) for a bug the 1.21.1 port shipped.
· **The check is one command and belongs in every port's Step 4**, next to the datapack layout:
`diff <(ls decompiled-raw/META-INF/services 2>/dev/null) <(ls src/main/resources/META-INF/services
2>/dev/null)`. It is §S1's shape in a different directory — a resource the game reads by
convention, silently absent, with the compiler structurally unable to care.
· **Swept, and the result bounds it:** three ports here call `ServiceLoader.load` (a space-exploration mod,
a small shared-API library, the boss mod). The other two ship their provider files — the space mod's four
api interfaces, the shared-API library's four platform helpers — so this was one port's loss rather than a
systematic step everyone skips. ⚠ Only two ports still HAVE a `decompiled-raw` to diff against (it
is gitignored and gets cleaned), so on a port without one the check is "does the mod call
ServiceLoader, and does it ship a provider for every interface it loads" rather than a diff.
· **What FOUND it is the more reusable half:** a baseline GameTest that places every block the live
registry reports in the mod's namespace. No hand-written roster would have covered it, because the
blocks registered perfectly; only *using* one reached the service.


**S7b. 🔴 A NESTED BINARY RESOURCE (a Java-agent `agent.jar`, any `*.jar`/`*.so` read via
`getResourceAsStream`) is dropped by the decompile — the loader catches the failure and the mod boots
with its whole bytecode layer inert.** · **Pattern:** `AgentLoader` copies `/com/example/examplemod/agent/agent.jar`
out of its own jar and self-attaches it (`VirtualMachine.attach(pid).loadAgent(...)`) · **Runtime:**
nothing in `latest.log`; only the mod's OWN log file says
`FileNotFoundException: Agent resource not found`, then "No Instrumentation, skipping" for every
transformer — and Gate A/B/C all stay green because the mod was written to degrade silently. ·
**Fix:** diff the original jar's non-class entries against `src/main/resources`
(`unzip -Z1 orig.jar | grep -v '\.class$'`); rebuild a lost agent jar from the port's own compiled
agent classes in a Gradle `Jar` task with the original manifest (`Agent-Class`/`Premain-Class`/
`Can-Retransform-Classes`) and route it into `processResources`. Then READ the mod's own agent/coremod
log on a Gate B run — "loaded without crashing" is not "the agent attached". (a core-API mod with a Java agent: its main class transformer,
ContainerReplacementTransformer and the loading-screen transformer had never run in the port.)

· ⚠ **AUGMENT (S7c) — rebuilding the resource is only half. A coremod (`ITransformationService`) is
found ONLY in a mods folder, so a dependent's DEV run that puts the library on the classpath never
loads its coremod at all.** · **Pattern:** the dependent declares the library as
`localRuntime files('libs/examplelib.jar')` (NeoGradle) or a plain runtime dependency, which is how every
other required mod is wired · **Runtime:** `[<MODID>] Agent and JVMTI all failed` and no transformer runs.
The mod half loads (its `@Mod` class sits on the classpath), so nothing crashes. FML's
`ModDirTransformerDiscoverer` scans only `<gameDir>/mods` for `META-INF/services/…ITransformationService`,
and classpath entries are loaded as mods and never as transformation services · **Fix:** keep the
library `compileOnly`, exclude it from `localRuntime`, and have a small Gradle task copy the jar into
`run/{client,server,gameTestServer}/mods/` before each run (deleting older copies first so two versions
never sit side by side). Then read the library's own log for `Agent load succeeded`. A green run with
the transformer layer silently off is exactly what S7b warns about.

**S8. 🔴 26.2 parses a mod's JSON STRICTLY and 1.21.1 does not — so an author's typo loads on the
version people play and SKIPS THE WHOLE FILE on the new one.** · **Pattern:** any shipped `.json`
carrying a missing comma, a colon inside the key's quotes (`"key:" "value"`), a key closed with a
single quote, a value missing its opening quote, a raw TAB inside a string, or a UTF-8 BOM — none of
which a port ever opens, because the file compiled nothing and broke nothing · **Symptom:** one WARN
and then silence — `Skipped language file: <ns>:lang/fr_fr.json (MalformedJsonException: Unterminated
object at line 199)` — after which that language, model or book page simply does not exist. No crash,
no failing test, and Gate A, Gate B and a `launch`-only Gate C are all green either side · **Fix:**
repair the typo. There is nothing version-specific to express: the file was always meant to be JSON,
so one shared file serves both targets and 1.21.1 is unaffected (its parser already accepted it).
· **This is a FACT check, not a heuristic, which is what lets it gate** — the same property that makes
§S6's `file(1)` sweep worth having. A strict parser either accepts the bytes or it does not, so a hit
is never a judgement call. `tools/audit-json-strict.py <dir>`, wired into `check`, with §X27's
`checked == 0` guard exiting 2 rather than printing a pass over an empty scope, and A/B'd both ways.
· **Measured across all 21 ports: 13101 shipped JSON files, 5 strict-invalid, 0 false positives** —
a large boss mod's `fr_fr.json` (1 typo) and `pl_pl.json` (**9**, so Polish players had no translations at
all on 26.2), one port's weapon item model (a stray trailing `}`, i.e. an item model that renders as
the magenta cube), a Registrate-based framework library's `ja_jp.json` (a UTF-8 BOM) and one space-mod guidebook page (a raw tab
inside a string). Four of the five are in mods nobody was looking at, which is the argument for
sweeping the shape rather than fixing the port that surfaced it.
· ⚠ **AUGMENT — the commonest shape is not a typo at all: JSONC `//` COMMENTS, written on
purpose.** §S8's original five findings were all slips (a stray brace, a BOM, a raw tab, a missing
comma). A ~660-file GeckoLib mob mod's eight bad worldgen files are none of those: the author commented his template
pools with `// ****` banner lines, which 1.21.1's lenient GSON strips and 26.2's strict parser
rejects. The consequence is bigger than a typo's, because a template pool that fails to parse takes
its whole STRUCTURE with it — here the mod's headline build. **So expect this in
hand-authored worldgen and hand-authored lang, not in generated data**, and note that it is the one
S8 shape a person will defend as intentional. The fix is still to make the file JSON: strip only
lines whose stripped form starts with `//` (a `//` inside a string is a URL, not a comment), in
binary mode, and re-parse before writing.
· ⚠ **AUGMENT — and the QUEUE is per FILE, not per repo.** §S8 already records that a parser stops
at its first error. Measured again here: `ru_ru.json` needed three rounds and **three different
shapes** — two missing commas, then a missing OPENING quote on a value (`"key": Привет",`), which
the comma-shaped repair loop correctly REFUSED rather than guessing at. A repair loop that handles
one shape and refuses the rest is what turns the second and third defects into findings instead of
silent mangling (§S1b), and the refusal is the part that has to be written first.

· ⚠ **Repair in BINARY mode, and this cost a revert.** Reading a CRLF file as text and writing it
back rewrites every line ending: the first pass at `pl_pl.json` produced **532 insertions and 532
deletions** for what is really nine one-character fixes — a diff no reviewer can read, hiding the nine
edits that matter. Read bytes, split on the file's own terminator, join with it.
· ⚠ **And expect a QUEUE, exactly as §V42c records for the resource layer.** A parser stops at its
first error, so each fix reveals the next: `pl_pl.json` went missing-comma → colon-inside-quotes →
missing-comma → … over nine rounds. Fix in a loop that names each edit and **REFUSES an unrecognised
shape** rather than guessing (§S1b) — mine stopped twice on shapes it had not seen, which is how the
third and fourth were found rather than silently mangled.
· **How it was found is the reusable half:** not by a sweep but by the §X15 control column. The 26.2
`spawn` log had two lines the 1.21.1 log did not, on the same commit — `Skipped language file` ×2 and
one `Couldn't load tag` — and a `grep -c` of each against both logs is what separated "the port did
this" from "upstream always did this" (the 13 `Missing subtitle` lines are 13 on both, so they are
neither). **Diff the two targets' logs, not just their verdicts.**
· ⚠ **AUGMENT — the strict/lenient split is not only MOJANG's parser: a bundled LIBRARY's major
bump moves the same line, and a literal `NaN` is the shape to know.** Two of a ~660-file GeckoLib mob mod's animation
JSONs carry bare `NaN` keyframe values (one mob's ×3, another's ×4). GeckoLib 4.8.4 parses each
expression with `Double.parseDouble`, and `parseDouble("NaN")` is **valid** — verified in the
bytecode, not assumed — so 1.21.1 has been baking NaN channels for years. GeckoLib 5's loader
rejects the file, and rejecting an animation file loses the WHOLE animation, silently, behind one
logged line. · **So the §S8 sweep has a second axis:** for every library you bumped a major version
(§S4's list), ask what its parser tolerates now that it did not before — and read the PASSING log
for it (§X41), because nothing fails. · **And the fix is a §W6 JUDGEMENT, not a rename:** replacing
`NaN` with `0` changes 1.21.1's behaviour deliberately. Say so in the commit; a NaN keyframe is not
a value anyone authored, but it is a value the shipping version has been using.

**S9. 🔴 Forge's DATA NAMESPACE is dead on NeoForge, and the converter used to call it clean.** §142
lists the renames; this is why they still shipped. · **Pattern:** a Forge 1.20 mod's
`data/<ns>/forge/biome_modifier/*.json` (`"type": "forge:add_spawns"`), `forge/structure_modifier`,
`data/forge/loot_modifiers/global_loot_modifiers.json` with `"condition": "forge:loot_table_id"`,
`"loader": "forge:separate_transforms"` item models, `"type": "forge:conditional"` recipes, `#forge:is_*`
biome tags and `data/forge/tags/**` · **Symptom:** nothing. The folders are never read and the ids are
unknown, so mobs never spawn naturally, global loot modifiers never apply, items render as the missing
model, and every crash gate passes (mobs still come from eggs and structures). Measured on a ported
fork: 28 dead files, after it had passed Gate A, Gate B and Gate C, and after
`fix-datapack-layout --verify` had printed "1.21-clean", because it checked plural/singular folder names
and only *warned* about `data/forge`. · **Fix:** `tools/fix-datapack-layout.py <mod> --apply` now
converts all of it: the folders move to `neoforge/`, `forge:X` becomes `neoforge:X` for the X NeoForge
21.1 registers, a conditional recipe becomes the recipe with `neoforge:conditions`, and Forge tags map
through a table checked against the NeoForge jar (`is_peak` → `c:is_mountain/peak`,
`needs_netherite_tool` → `neoforge:`). It swaps only the changed literals, so the author's formatting
and the diff stay small. Anything not in the tables is refused by name, and `--verify` fails while
anything is left. · **The runtime control:** Gate B's `DataLivenessGameTest` (template) asserts every
biome and structure modifier file of the mod registered, every loot modifier decodes, and nothing sits
in a Forge-only folder; fork CI also runs the static `--verify` as its own row, because client assets
(the model loader) are invisible to a server test. · **Then expect §144 at once:** the same fork's
server refused to start the moment its structure modifiers loaded, on two entity ids the mod had
dropped years before. A port of this same mod through the jar pipeline had hit both, and the lesson
did not reach the fork pipeline until the converter enforced it — §S2 again.

**S10. 🔴 `"item"` → `"id"` reaches every ItemStack in data, not just recipe RESULTS — advancement icons and a
mod's OWN JSON formats too.** · **Pattern:** a 1.20.1 data file naming a stack `{"item": "ns:x"}` anywhere the
reader now uses `ItemStack.CODEC`: an advancement's `display.icon`, and any mod-specific format whose loader the
port moved from hand-written `GsonHelper` reads (which accepted `item`) to the codec (which wants `id`).
· **Runtime:** one ERROR per file — `Couldn't parse data file '<ns>:<adv>' … No key id in MapLike[{"item":…}]`, or the
mod's own "Failed to load …" with the same cause — and the advancement or recipe is DROPPED. No crash; every gate
green. Measured on one port after green CI: 38 advancements and 62 custom smithing recipes, all dead. · **Fix:**
the key, and only the key: `fix-datapack-layout.py --apply` now swaps it inside advancement icons as a literal
(file formatting kept), and `--verify` fails on any left. A mod-specific format is the mod's own business: swap the
key in its files AND, when players write their own files in that format (a `config/` folder of recipes), give the
codec `Codec.withAlternative(ItemStack.CODEC, <legacy {item, count}>)` so files written for 1.20.1 still load.
· **Control for the whole class:** ci-gates now reads Gate B's server log and fails the row "Data loads" on any
rejection line naming the mod's namespace — the only check that sees a format no converter knows.

**S5c. Corollary — scope a cross-mod audit to the mods you are auditing.** The first census run loaded the
whole 66-jar instance and never reached the census: one third-party mod requires NeoForge 21.1.233 and the harness
runtime is 21.1.228, so one unrelated third-party mod aborted mod loading for all 17 ports. Passing the
**ports plus their required dependency closure** (read from each port's own `neoforge.mods.toml`) is both
faster and strictly more informative — anything the report then flags is *our* migration's doing rather than
some third-party mod's own quirk.

## V. THE ERA JUMP: 1.21.x → 26.x (calendar versioning) — measured on a ~460-file builder mod's port
> **Axis:** version-family, but a *bigger* one than §G's 1.20→1.21. Mojang left the `1.x`
> scheme entirely: after **1.21.11** (2025-12-09) came **26.1** (2026-03-24), 26.1.1, 26.1.2,
> and **26.2** (2026-06-16). NeoForge follows with a matching line (`26.2.0.75`), exactly as
> `21.1.x` tracked MC 1.21.1. Everything here was measured by building both versions and
> diffing their real compile classpaths — not remembered. **MC 26.2 post-dates the assistant
> knowledge cutoff, so treat every 26.x API claim as something to verify against the
> decompiled jar, never to recall.**
>
> **Companions:** if the mod must keep running on the OLD version too, read **§W** (one source
> tree, two Minecraft versions) before writing any code — the architecture decision comes first
> and is hard to retrofit. **§X** is the codemod-hygiene section an era jump depends on, because
> the work is done by mass rewrites and a rewrite that matches nothing is silent.
> The moving parts of both are checked in at **`templates/multi-version/`** (README, the
> source-preparation pipeline, the GameTest adapter) rather than only described here — §S2.

**V0. Pin the target from the manifest, not from what someone says the latest is.**
· **Pattern:** a request to port to "the latest Minecraft", named as a number ·
· **Trap:** `26.3` looks like a release and is not one — `piston-meta` reports
`latest.release = 26.2`, `latest.snapshot = 26.3-pre-1`. Porting to a pre-release means
chasing an API that is still moving, and NeoForge may have no stable line for it ·
· **Fix:** `curl -s https://piston-meta.mojang.com/mc/game/version_manifest_v2.json | jq .latest`
and cross-check that NeoForge actually publishes that line
(`maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml`).
⚠️ **`launchermeta.mojang.com` is blocked by the egress policy here; `piston-meta.mojang.com`
is reachable.** They serve the same manifest — use piston-meta, don't conclude "no network".

**V1. 🔴 MC 26.x needs JAVA 25. Check before anything else.**
· **Pattern:** the whole 1.21 corpus is Java 21, so a port inherits `languageVersion.of(21)` ·
· **Error:** toolchain resolution failure, or a JDK download attempt mid-build ·
· **Fix:** the version manifest states it —
`javaVersion: {component: java-runtime-epsilon, majorVersion: 25}` for 26.2 against
`java-runtime-delta / 21` for 1.21.1. Install a JDK 25 and set
`java.toolchain.languageVersion = JavaLanguageVersion.of(25)`. A **multi-version** build
therefore needs *both* JDKs present, and the per-version module must pin its own.

**V2. 🔴 NeoGradle is a DEAD END for 26.x — the toolchain itself must change to ModDevGradle.**
· **Pattern:** a 1.21.1 mod on `id 'net.neoforged.gradle.userdev' version '7.1.36'` ·
· **Error:** with Gradle 9.x, `Failed to query the value of property 'postSyncTasks'` →
`Could not create task ':writeMinecraftClasspathClient'` →
`Cannot invoke "TaskProvider.flatMap(...)" because "this.userdevClasspathElementProducer" is null`.
It reads like a corrupt cache or a missing artifact; **it is neither** — the `neoforge` userdev
jar was present and cached and the plugin marker resolved ·
· **Fix:** move to **ModDevGradle** (`id 'net.neoforged.moddev' version '2.0.146'`), which is
NeoForge's current toolchain and takes the version as data:
`neoForge { version = "26.2.0.75" }`. **MEASURED: the same MDG scaffold builds BOTH
`21.1.228` (JDK 21) and `26.2.0.75` (JDK 25)**, so the multi-version build wants exactly one
plugin, parameterised — do not keep NeoGradle for the old version and MDG for the new.

**V3. The bulk of the jump is a SUB-PACKAGE REORGANISATION — generate the map, never hand-fix it.**
· **Pattern:** flat packages split into per-family sub-packages ·
· **Error:** `cannot find symbol: class Zombie / Cow / Villager / Minecart / GameRules` — a
long tail that looks like mass deletion and is not ·
· **Fix:** they moved with an **identical simple name**:
`monster.Zombie`→`monster.zombie.Zombie`, `monster.Drowned`/`Husk`→`monster.zombie.*`,
`monster.Evoker`/`Illusioner`/`Pillager`→`monster.illager.*`,
`monster.Skeleton`/`AbstractSkeleton`→`monster.skeleton.*`,
`monster.MagmaCube`→`monster.cubemob.MagmaCube`,
`animal.Cow`/`MushroomCow`→`animal.cow.*`, `animal.Rabbit`→`animal.rabbit.Rabbit`,
`animal.IronGolem`/`SnowGolem`→`animal.golem.*`, `npc.Villager*`→`npc.villager.*`,
`vehicle.Minecart`→`vehicle.minecart.Minecart`, `level.GameRules`→`level.gamerules.GameRules`,
`client.renderer.RenderType`→`client.renderer.rendertype.RenderType`,
`item.ArmorMaterials`→`item.equipment.ArmorMaterials`, `net.minecraft.Util`→`net.minecraft.util.Util`,
`advancements.critereon.*`→`advancements.triggers.*` / `advancements.predicates.*`.
**Do not discover these one compile error at a time.** `tools/build-class-move-map.py` diffs the
two real compile classpaths and emits the map; the measured 1.21.1→26.2 result is committed at
`references/moves-1.21.1-to-26.2.tsv` — **401 unambiguous moves**, plus a `.ambiguous.txt`
(10 cases where one simple name landed in several packages, e.g. `Variant`, `Input`) that must be
hand-picked and is deliberately never auto-applied, and a `.removed.txt` (2636) that is the real work.

**V4. 🔴 `ResourceLocation` → `Identifier` (Mojang adopted the Yarn name).**
· **Pattern:** every `ResourceLocation` — and in a cross-mod-integration codebase that is the
*primary* idiom, since optional mods are resolved by id at runtime ·
· **Error:** `cannot find symbol: class ResourceLocation` in `net.minecraft.resources` ·
· **Fix:** `net.minecraft.resources.Identifier`, and it is a **pure type rename**: `javap`
confirms the statics are identical — `fromNamespaceAndPath`, `parse`, `withDefaultNamespace`,
`tryParse`, `getNamespace`, `getPath`, `CODEC`, `STREAM_CODEC`. So a whole-tree
`ResourceLocation`→`Identifier` codemod is correct with **no call-site reshaping** (the builder mod: 433
occurrences over 88 files). `ResourceKey` is unchanged and stays in `net.minecraft.resources`.

**V5. 🔴 The `@GameTest` ANNOTATION is gone — tests became data-driven `GameTestInstance`s.**
· **Pattern:** the whole Gate-B corpus this repo depends on —
`@GameTest`, `@GameTestHolder`, `@PrefixGameTestTemplate` ·
· **Error:** `cannot find symbol: class GameTest` / `GameTestHolder` / `PrefixGameTestTemplate` ·
· **Fix:** `net.minecraft.gametest.framework.GameTestHelper` **survives unchanged**, so the
*bodies* of the tests port cleanly; what is gone is the annotation-driven discovery, replaced by
`GameTestInstance` / `GameTestInstances` / `FunctionGameTestInstance` / `BlockBasedTestInstance`
registered as data. **Budget this explicitly**: it is not one fix, it is a harness rewrite that
touches every `@GameTest` file (the builder mod: 56), and until it is done **Gate B cannot run at all on
26.x** — i.e. the very gate this repo leans on hardest is the one the era jump takes away first.
Port the harness before porting the mod, or the port has no runtime gate.

**V6. The client render API moved to RECORDED RENDER STATE (not "because of Vulkan").**
· **Pattern:** any HUD, overlay, baked-model or entity-renderer code ·
· **Error:** `cannot find symbol` for `GuiGraphics`, `MultiBufferSource`, `BakedModel`,
`ItemOverrides`, `LightTexture`, `ChunkRenderTypeSet` — all genuinely absent from the 26.2
compile classpath (verified against the *authoritative* classpath, not a guessed jar) ·
· **Fix:** there is no rename to apply; this is the §L rewrite one era further on. The driver,
measured: **`net.minecraft.client.renderer.state` holds 45 classes in 26.2 and DID NOT EXIST in
1.21.1** — `BlitRenderState`, `ColoredRectangleRenderState`, `GlyphRenderState`. Drawing no
longer issues immediate calls; it **records** them into a `GuiRenderState` that a backend
replays later. So `GuiGraphics` became **`GuiGraphicsExtractor`** (ctor
`(Minecraft, GuiRenderState, int, int)`), and its `pose()` returns a 2D `Matrix3x2fStack`
rather than a 3D `PoseStack`. Port immediate-mode → recorded-state; the per-call-site work is
mechanical once the model is understood. Survivors that only *moved* are in the map
(`BakedQuad`→`client.resources.model.geometry`, `ItemTransforms`→`client.resources.model.cuboid`).

⚠️ **CORRECTION, and the reasoning error is the lesson.** This entry first said the rewrite was
*caused* by 26.2 shipping a Vulkan backend — inferred from spotting `com.mojang.blaze3d.vulkan`
(69 classes) in the jar next to `blaze3d.opengl`. That is a plausible story fitted to one
observation, and it is wrong: Vulkan is a *beneficiary* of recorded state (it is what makes a
second backend practical), not the reason the types changed. **A co-occurring new subsystem is
not a cause** — the cheap check that settles it is whether the replaced types actually mention
the new one. Here they do not, which is also the answer to the question that matters:

**V6b. You do NOT write per-backend code, and Vulkan is NOT the default.** Measured from
`PreferredGraphicsApi.getBackendsToTry()` bytecode: `DEFAULT` and `OPENGL` both return
`{GlBackend, VulkanBackend}` — **OpenGL first**, Vulkan only as a fallback — and only an
explicit `VULKAN` reverses the pair. `com.mojang.blaze3d.systems.GpuBackend` is an *interface*
with the two as implementations, and the mod-facing types (`GuiGraphicsExtractor`,
`RenderSystem`, `RenderType`) carry **zero** references to either concrete backend, while
`GuiGraphicsExtractor` implements a NeoForge extension interface. So a port targets the
abstraction once: there is no renderer matrix to test and no second code path to keep. A Gate C
run under Mesa llvmpipe (OpenGL) is therefore testing what the default user actually runs.

**V7. Item/interaction cluster (small, mechanical once known).**
· `world.item.UseAnim` → **`world.item.ItemUseAnimation`**
· `world.InteractionResultHolder` **and** `world.ItemInteractionResult` → collapsed into the
  single **`world.InteractionResult`** (the 1.21.1 three-type split is over — note §O #127 and
  §M M10 were tracking this convergence one step at a time)
· `world.entity.MobSpawnType`, `world.item.ArmorItem`, `world.item.component.Unbreakable`,
  `neoforge.common.DeferredSpawnEggItem`, `neoforge.event.AddReloadListenerEvent` — all absent
· `neoforge.client.model.data.ModelData` → **`neoforge.model.data.ModelData`**

**V8. THE SCOPING NUMBER, and why it is the useful one.** Before estimating an era jump, diff the
mod's own import set against the target's real classpath — it converts "10 Minecraft versions of
change" into a count. Measured for a builder mod (458 main source files, ~81k LOC, **0 mixins**),
of **362 distinct `net.minecraft`/`net.neoforged` imports**:

| bucket | count | share | what it costs |
|---|---|---|---|
| unchanged on 26.2 | 312 | **86%** | nothing |
| mechanical package move | 32 | 8% | one codemod from the generated map |
| real API work | 18 | **4%** | the actual port |

and the 18 collapse into just five themes: `Identifier` (V4), the GameTest harness (V5), client
rendering (V6), the item/interaction cluster (V7), and one reload-listener event. **A mod whose
bulk is world-building against `setBlock`/`BlockState`/`BlockPos`/`Blocks` is far more portable
across an era jump than its line count suggests** — those APIs were verified unchanged by
compiling the same probe file against both versions. Conversely a *renderer-heavy* mod is
mostly V6, and its line count understates it. **Count imports, not lines.**

**V9. Environment note that will otherwise cost an hour: Maven Central rate-limits (HTTP 429).**
· **Error:** `Could not GET 'https://repo.maven.apache.org/...'. Received status code 429` on
`net.neoforged:neoform` / `dsl-userdev` — artifacts that do **not** live on Central at all ·
· **Fix:** put `maven.neoforged.net` (and `libraries.minecraft.net`) **before** `mavenCentral()`
in both `pluginManagement` and `dependencyResolutionManagement`. 429 is a rate limit, not a
policy denial, so unlike a 403 it is worth retrying — but not resolving through Central is better.
· ⚠ **AUGMENT — repository ORDER does not reach NeoGradle's own detached configurations** (mergetool →
`asm-util`, `org.jetbrains:annotations`), which still hit `repo.maven.apache.org` and 429. Point the
whole build at Google's Central mirror with an init script that rewrites any repo URL containing
`repo.maven.apache.org`/`repo1.maven.org` to `https://maven-central.storage-download.googleapis.com/maven2/`
(`./gradlew --init-script mirror.gradle …`, applied to settings + every project's repositories). And in
Groovy write the exclude regex as `'net\\.neoforged.*'` — a single `\.` inside a single-quoted Groovy
string is a settings-file COMPILE error (`Unexpected character: '\''`), which two ports shipped.
· **AUGMENT — the init script ships as `tools/central-mirror.init.gradle`, and a per-build `--init-script` does not reach builds that other scripts start:** the Gate C loop runs its own `./gradlew runClient`, so on a rate-limited machine its first launch dies at configuration with `Could not resolve commons-io:commons-io:2.11.0 … 429` even though your own builds pass. Measured on a blind replay in a cloud session. Install the script into `~/.gradle/init.d/` (every build on the machine) and remove it to undo.

**V10. A required library may simply not exist yet for the new era — check before promising parity.**
· **Pattern:** a compile-time library bundled via JarJar (this repo's canonical case is GeckoLib) ·
· **Fix:** check the *registry*, not just the maven. GeckoLib **5.5.4 does support 26.2** — but it
is published on Modrinth and returns **404 on the cloudsmith maven** the 1.21.1 build resolves from
(`geckolib-neoforge-26.2` is absent there while `geckolib-neoforge-1.21.1` is present). So the
port needs a different acquisition path for the same dependency. And note the major bump
(4.8.4 → 5.5.4) is its own API migration on top of §138's 4.7→4.8 — see §V18.
· **Acquisition, resolved:** the 26.2 jar is now **pinned by sha1 in
`<consuming-mod>/testmods/lockfile-26.2.tsv`** and fetched by `./tools/fetch-testmods.sh --mc 26.2`
into `testmods/26.2/`. Point a port's `compileOnly`/`runtimeOnly` at that file rather than at a
maven coordinate that does not exist. **Five of the fourteen queued ports need it** (a mob-framework library,
a ~390-file mob mod, a ~380-file MCreator mob mod, a ~660-file GeckoLib mob mod, an aquatic mob mod), so the package map §V18 asks for
should be generated **once** and checked in here, not re-derived per mod.
· **The generalisation:** "does the library support the new version" and "can this build machine
GET it" are different questions, and the second is the one that stops work. Ask the registry
(`tools/mod-registry/modreg.py versions --provider modrinth --id <slug> --mc <ver>`), not the maven
the old build happened to use.

**V11. 🔴 `ColorCollection` — the 16 per-colour constants collapsed into record accessors, and for a
builder mod this is the single biggest bucket.**
· **Pattern:** `Blocks.WHITE_CONCRETE`, `Blocks.LIME_TERRACOTTA`, `Items.RED_WOOL`, `Items.BLUE_DYE` ·
· **Error:** `cannot find symbol: variable WHITE_CONCRETE` — several hundred of them ·
· **Fix:** 26.x groups each dyed family into a `ColorCollection<T>` whose members are accessors:
`Blocks.CONCRETE.white()`, `Blocks.TERRACOTTA.lime()`, `Items.WOOL.red()`, `Items.DYE.blue()`
(also `DYED_BUNDLE`, `HARNESS`, `GLAZED_TERRACOTTA`, `CONCRETE_POWDER`, `SHULKER_BOX`, the wools,
carpets, beds, candles, banners, stained glass and panes). **Generate the 16 × families rows into
the rename table** — that is 224 block + 240 item rows for the builder mod, and hand-fixing them is both
unreviewable and where the next bug comes from. ⚠️ **Longest-key-first matching is load-bearing**:
without it `WHITE_TERRACOTTA` clips `WHITE_GLAZED_TERRACOTTA` and `CONCRETE` clips
`CONCRETE_POWDER`, producing `Blocks.CONCRETE.white()_POWDER` — which does not compile, so you find
it, but the same class of clip on a pair where both halves parse would not announce itself.

**V12. 🔴 NBT accessors return `Optional`, and the `*Or` forms are the faithful port — but the regex
that applies them is dangerous in two specific ways.**
· **Pattern:** `tag.getInt("k")`, `getString`, `getBoolean`, `getCompound`, `getList(k, Tag.TAG_X)`,
`getUUID`/`putUUID` ·
· **Error:** `incompatible types: Optional<Integer> cannot be converted to int` ·
· **Fix:** 26.x returns `Optional<T>` from the bare getters and adds `getXOr(key, default)`, which
preserves 1.21.1 semantics **exactly** (the old `getInt` returned 0 for an absent key, which is what
`getIntOr(k, 0)` does) — so `.getInt(k)` → `.getIntOr(k, 0)`, `.getString(k)` → `.getStringOr(k, "")`,
`.getBoolean(k)` → `.getBooleanOr(k, false)`, `.getCompound(k)` → `.getCompoundOrEmpty(k)`, and
`getList` lost its element-type argument so **both arities collapse to `getListOrEmpty(k)`**.
⚠️ **Two traps, both silent.** (a) A regex on `\.getString\(` will happily rewrite Brigadier's
`StringArgumentType.getString(ctx, "name")` into `getStringOr(ctx, "name", "")` — a two-argument call
on a completely different type. Exclude any call containing a comma: `\.getString\(([^,()]+)\)`.
(b) `CompoundTag.getUUID/putUUID` have **no 26.x equivalent at all** (a UUID rides a codec now:
`tag.store(k, UUIDUtil.CODEC, v)` / `tag.read(k, UUIDUtil.CODEC)`), and the obvious `\.getUUID\(` rule
also matches the ~50 unrelated no-arg `Entity.getUUID()` calls. Require an argument in the pattern and
route the tag form through a per-version compat helper.

**V13. Interaction results collapsed into ONE `InteractionResult` (the end of the 1.21.1 three-type
split) — and one row of the mapping is a judgement, not a rename.**
· **Pattern:** `InteractionResultHolder.success(stack)` / `.sidedSuccess(stack, level.isClientSide)`;
`ItemInteractionResult.PASS_TO_DEFAULT_BLOCK_INTERACTION` ·
· **Error:** `cannot find symbol: class InteractionResultHolder / ItemInteractionResult` ·
· **Fix:** everything folds into `net.minecraft.world.InteractionResult`. The `Holder` forms carried a
payload (the resulting `ItemStack`) that the new API does not take, so **the argument is dropped** —
vanilla reads the stack back off the player. `ItemInteractionResult.PASS_TO_DEFAULT_BLOCK_INTERACTION`
is exactly what `InteractionResult.TRY_WITH_EMPTY_HAND` now expresses.
⚠️ **`sidedSuccess(x, isClientSide)` meant "SUCCESS on the client, CONSUME on the server"**; 26.x folds
that decision into `SUCCESS` itself. That is the faithful mapping and it is also the one row here that
is a behavioural judgement rather than a pure rename — **verify it in-game (the arm swing on use)
rather than trusting the compile.** Flag such rows in the rename table so a later reader knows which
line to distrust.

**V14. 🔴 `GameRules` was overhauled, and the SERIALIZED IDS changed too — so name resemblance is not
enough to port a rule.**
· **Pattern:** `GameRules.RULE_DAYLIGHT`, `RULE_DOMOBSPAWNING`, `RULE_WEATHER_CYCLE` ·
· **Error:** `cannot find symbol: variable RULE_DAYLIGHT`; also `GameRules()` no longer has a no-arg
constructor ·
· **Fix:** constants lost the `RULE_` prefix and are typed `GameRule<T>` — `GameRules.ADVANCE_TIME`,
`SPAWN_MOBS`, `ADVANCE_WEATHER`. **The ids moved with them**: `doDaylightCycle` → `advance_time`,
`doMobSpawning` → `spawn_mobs`. So a mod that names a gamerule as a **string** (a `/gamerule` command
it runs, a datapack, a config default) is silently broken with a green compile. Resolve each one by
reading its `registerBoolean("<id>", …)` call out of `GameRules`' static initialiser in the
decompiled 26.x source; do not map by how the constant reads.

**V15. Numeric command permission levels → named `PermissionCheck` constants.**
· **Pattern:** `.requires(s -> s.hasPermission(2))` — the standard OP-gate on every admin command ·
· **Error:** `cannot find symbol: method hasPermission(int)` on `CommandSourceStack` ·
· **Fix:** `.requires(Commands.hasPermission(Commands.LEVEL_GAMEMASTERS))`. The five constants live on
`Commands`, are typed `net.minecraft.server.permissions.PermissionCheck`, and map the old levels in
order: `LEVEL_ALL`(0) · `LEVEL_MODERATORS`(1) · `LEVEL_GAMEMASTERS`(2) · `LEVEL_ADMINS`(3) ·
`LEVEL_OWNERS`(4). A whole-lambda regex handles it (the builder mod: all 19 OP-gated commands in one rule).

**V16. `Player.displayClientMessage(text, boolean)` split — the boolean now picks a METHOD, so NO
rename can express it.**
· **Pattern:** `player.displayClientMessage(component, true)` (action bar) / `…, false)` (chat) ·
· **Error:** `cannot find symbol: method displayClientMessage` ·
· **Fix:** `sendOverlayMessage(component)` and `sendSystemMessage(component)` respectively. This is the
canonical shape that a rename table **cannot** carry and that belongs in a per-version compat helper
(`Msg.to(player, text, actionBar)`), because the argument is a compile-time constant at some call
sites and a variable at others. Rewrite the call sites for **both** targets, not just the new one —
if the old target still calls vanilla directly, the shared tree drifts the moment someone edits it.

**V17. Small renames confirmed against the real 26.2 classpath (cluster).** `RegistryAccess.registryOrThrow`
→ **`lookupOrThrow`** · `Entity.moveTo(x,y,z[,yaw,pitch])` → **`absSnapTo(...)`** (⚠️ the bare `snapTo`
overloads also exist and are the *relative* forms — `absSnapTo` is what `moveTo` did) ·
`ServerPlayer.serverLevel()` → **`level()`** (covariant on `ServerPlayer`) · `ResourceKey.location()`
→ **`identifier()`** (following V4) · `Minecraft.setScreen` → **`setScreenAndShow`** ·
`world.item.UseAnim` → **`world.item.ItemUseAnimation`** · `Level.isClientSide` became a **private
field with a public accessor**, so field reads need `()` — and the rewrite must not touch a call that
is *already* `isClientSide()`, i.e. `\.isClientSide\b(?!\s*\()`.

**V18. GeckoLib 4.8.4 → 5.5.4 moves the WHOLE library to `com.geckolib.*` — and its render-state
change is only an ARITY change.**
· **Pattern:** every `software.bernie.geckolib.*` import; `class R extends GeoEntityRenderer<T>` ·
· **Error:** `package software.bernie.geckolib.animatable does not exist`; then
`GeoEntityRenderer wrong number of type arguments; required 2` ·
· **Fix:** the root package is now **`com.geckolib`**, with a few types also changing sub-package
(`animation.AnimatableManager` → `animatable.manager.AnimatableManager`, `animation.PlayState` →
`animation.object.PlayState`). Generate the map by diffing the two real jars, same technique as V3.
For the renderers: 26.x threads a render state through `EntityRenderer<T, S>` and GeckoLib 5 follows
with `GeoEntityRenderer<T, R>` — but it **keeps the `(Context, GeoModel<T>)` constructor** and it
**mixes `GeoRenderState` into vanilla's `EntityRenderState`**, so any vanilla state satisfies `R`.
A living entity's renderer therefore needs nothing but `LivingEntityRenderState` in the second slot;
this is a one-line regex per renderer, not the rewrite the error message implies.

· **MEASURED, and the map is now checked in** (`.claude/skills/migrate-mod/references/
moves-geckolib-4.8.4-to-5.5.4.tsv`, generated by `tools/build-class-move-map.py` from the two real
jars and promoted into `templates/multi-version/versions/26.2.renames.tsv`, so the five queued ports
that need GeckoLib inherit it rather than re-deriving it): **190 classes → 232, of which 142 are
unambiguous moves, 2 ambiguous and 46 removed.** Many types change sub-package as well as root —
`animation.AnimatableManager` → `animatable.manager.AnimatableManager`, `animation.PlayState` →
`animation.object.PlayState`, `animation.Animation` → `cache.animation.Animation`.
· ⚠ **Those 142 are the MECHANICAL half, and the sentence above understates the rest.** The 46
removals are not a long tail, they are two whole subsystems: 5.5.4 rebuilt the renderer around its
own **`GeoRenderState`** (with `Compile*RenderStateEvent` / `Compile*RenderLayersEvent` per
animatable kind, and mixins into vanilla's `EntityRenderState`) — mirroring MC 26.x's own
recorded-state shift — and **replaced the model/animation loading layer wholesale**
(`loading.definition.*` in place of `loading.json.raw.*` + `loading.object.*`). So
`AnimationState`, all three keyframe EVENT types (`SoundKeyframeEvent`, `ParticleKeyframeEvent`,
`CustomInstructionKeyframeEvent`), `GeckoLibCache`, `util.Color`, the `cache.texture.*` classes and
the `renderer.specialty.Dynamic*` renderers are simply gone. **Read the `.removed.txt` before
estimating a GeckoLib port** — a green rename pass is not a ported renderer, and the arity note
above is true of exactly one line of it.

**V18b. 🔴 GeckoLib 5.5.4 CANNOT BE COMPILED AGAINST WITHOUT NEOFORGE INTERFACE INJECTION — and
javac blames YOUR renderer for it.** · **Pattern:** any GeckoLib 5 renderer or layer, i.e. the whole
point of the library · **Error:** `type argument EntityRenderState is not within bounds of
type-variable R`, on your own class, naming no build setting whatsoever ·
**Why:** 5.5.4 declares `GeoEntityRenderer<T, R extends EntityRenderState> implements
GeoRenderer<T, Void, R extends GeoRenderState>` — two bounds on one type variable — and vanilla's
`EntityRenderState` does not implement `GeoRenderState`. That is not an oversight and it is not
fixable in your source: **GeckoLib's own generics are unsatisfiable against a stock Minecraft
jar**, and it closes the gap with `META-INF/interface_injections.json`, which NeoForge applies at
runtime and ModDevGradle applies at COMPILE time — if you wire it up.
· **Fix:** read the file out of whichever jar the target resolves and hand it to MDG:
`neoForge { interfaceInjectionData.from(<file>) }` (`ModDevExtension.interfaceInjectionData`,
a `DataFileCollection`). In a §W tree do it by SWEEPING the resolved jars for the entry rather
than naming GeckoLib — 4.8.4 ships no such file, so the 1.21.1 target then finds nothing and
injects nothing, with no version branch in the build.
· **The reason it costs an afternoon rather than five minutes** is that every instinct is wrong.
The error is on your file, so you rewrite your renderer's bounds; `R extends EntityRenderState &
GeoRenderState` even compiles, and then nothing can instantiate it. The honest check is to look at
what the LIBRARY declares (`javap` its own `GeoEntityRenderer` and `GeoRenderer` and compare the
bounds) — if the library itself could not compile against the jar you are giving javac, the
problem is the jar, not you.
· **The general shape, and it is §V27 pointed at a dependency:** when a library's public generics
do not close, ask whether it ships loader METADATA that closes them. Interface injection, access
transformers and enum extensions are all invisible in a decompile and all change what compiles.
`unzip -l <lib>.jar | grep META-INF` is the whole check.
· **Two smaller GeckoLib 5 notes from the same port**, both of which read as bigger problems than
they are: `DataTicket`'s constructor is package-private now, so a mod builds one with the
`DataTicket.create(String, Class)` factory; and a renderer overrides
`getRenderType(R, Identifier)` — `GeoRenderer` declares exactly one, and the
`GeoRenderState`-typed sibling visible on `GeoEntityRenderer` is the compiler's own bridge, so
overriding THAT is a name clash rather than an override.


**V18c. 🔴 GeckoLib 5 MOVED WHERE IT LOOKS — every geo model and animation a 4.x mod ships is in a
directory 5.x never scans, so EVERY GeckoLib entity renders with NO GEOMETRY and nothing fails.** ·
**Pattern:** the 4.x layout every GeckoLib mod has —
`assets/<ns>/geo/<name>.geo.json`, `assets/<ns>/animations/<name>.animation.json`, and ids written
as the FILE PATH (`ResourceLocation.fromNamespaceAndPath(ns, "geo/comet.geo.json")`) ·
**Symptom:** one INFO line at reload — `Loaded 0 models and 0 animations from resources` — and then,
per frame, an ERROR naming whichever model a renderer asked for
(`Unable to find model: <ns>:geo/comet.geo.json`, plus a `Superfluous prefix or suffix` line whose
suggestion is only half the fix). No crash, no failed reload, nothing red ·
**Fix, read out of `GeckoLibResources`' bytecode rather than inferred from the message:** 5.5.4 does
`listResources("geckolib/models")` and `listResources("geckolib/animations")`, and derives each id
by stripping `^(geckolib/)((animations/)|(models/))?` and `((\.geo)|((\.animation)s?))?(\.json)$`.
So **both halves move**: the assets go under `assets/<ns>/geckolib/{models,animations}/`, and the id
becomes the **bare name** (`<ns>:comet`) — not the `geo/comet` the warning suggests, which is why
following the message alone leaves it broken.
· **In a §W tree neither half is a rename row on its own.** The ids are (`re:"geo/([a-z_0-9/]+)\.geo\.json"` → `"\1"`, and the animation twin), but the assets are a
DERIVED relocation at `processResources` time for the new target only — `from(".../geo") { into
"assets/<ns>/geckolib/models" }` — rather than a second copy committed under
`src/mc26/resources`, because the files are byte-identical and a committed copy is §W14's mistake:
every future edit has to be made twice with nothing to say it drifted.
· 🔴 **Why no gate sees it, and this is the part to carry.** The compile is clean (an id is a
string), Gate A and Gate B never render, and **Gate C's own `N of N drawn` is green too** — because
the honest question a spawn phase can cheaply ask is *which renderer does the dispatcher hand back
for this entity*, and that answers identically whether or not the renderer's model resolved. The
harness's own wording was the misleading part and has been corrected to "reached the client with a
renderer bound". What actually found it was **diffing the two targets' spawn LOGS on the same
commit** (§X15): 58 error lines on 26.2 against 0 on 1.21.1.
· **Swept: 4 of the 9 GeckoLib ports here are affected** — a ~320-file boss mod (6 geo,
6 animations), a mob-framework library (2/3), a ~390-file mob mod (32/26, some nested under `animations/armor/`) and
a ~380-file MCreator mob mod (61/67). The other five ship no geo assets of their own. A GeckoLib port that has
not been through this is shipping invisible mobs on 26.x.
· ⚠ **AUGMENT (2026-09-23) — the sweep was written down and three of those four were never FIXED,
and the evidence was in a log nobody diffed.** Only the boss mod (and a ~660-file GeckoLib mob mod)
carried the relocation; the mob-framework library, the ~390-file mob mod and the MCreator mob mod shipped to `releases/26.2/`
with **zero** files under `geckolib/models`, behind green gates on both targets. It surfaced from
the builder mod's 26.2 Gate C logs — `Unable to find model: examplemod:geo/example_mob.geo.json` ×316,
`othermod:geo/example_golem.geo.json` ×230 — with the builder mod's OWN airplane, cable car and nine vehicles
in the same list, because the builder mod is a GeckoLib mod too. §S2 again: a finding recorded as a sweep result
is not a control. **Now wired**: every `mods/*/tools/gatec.sh` (and the builder mod's shared
`tools/lib/gate-c-env.sh`, as an EXIT trap over every launcher) turns a PASS into a FAIL when the log
holds any `Unable to find (model|animation)` line. · **Two traps in applying it:** an mc26 OVERLAY is
not run through the rename rows (§W8), so a GeckoLib model class that is itself an overlay keeps its
`geo/…` ids — one of the mob-framework library's model classes did; fix it by hand. And a Gate C asset check written
as `getResource("geo/x.geo.json")` passes on 26.2 WHETHER OR NOT the relocation shipped (the 4.x
copies still ride along), so repoint it at `geckolib/models/…` for the new target — the check then
proves the relocation instead of the original file.


**V18d. 🔴 GeckoLib 5's `bone.frameSnapshot` is NULL OUTSIDE A RENDER PASS — so routing 4.8.4's
mutable-bone API onto it silently imports a LIFETIME constraint no rename row can advertise.** ·
**Pattern:** the natural port of §V18's removed half — `bone.setHidden(x)` → `bone.frameSnapshot
.skipRender(x)`, `bone.getRotX()` → `bone.frameSnapshot.getRotX()`, and the rest of the family ·
**Runtime:** `NullPointerException` on `frameSnapshot`, but only from a call site that is not inside
a render pass · **Read out of the bytecode rather than inferred:** `GeoBone`'s constructor does
`aconst_null; putfield frameSnapshot`; `BoneSnapshot.apply()` is the ONLY writer of that field in
the whole jar and `cleanup()` sets it back to null; both are driven by `RenderPassInfo` around the
`BoneUpdater` calls. On 4.8.4 the pose lived on the bone permanently and could be touched from a
tick handler, an ability, a network callback — anywhere.
· **The check is one question per call site and it is not a grep**: *can this be reached from
anywhere but a render pass?* Measured on a ~660-file GeckoLib mob mod the answer was yes-by-one-link and safe only by
accident — two of its ability classes (a tunnelling and a boulder-roll ability) write bone rotations from
`server/ability/`, which reads exactly like tick-path code and is in fact reached from
its biped model's `setCustomAnimations`, i.e. render time. A later edit that moves one of those onto a
tick turns a silent no-op into a client crash, so it belongs in the mod's MANUAL_VALIDATION.md too.
· **It also constrains the SEAM**: whatever replaces `setCustomAnimations` has to keep those calls
inside the pass, which `RenderPassInfo.addBoneUpdater` does — one more reason that is the right
shape rather than merely a convenient one.
· **The general form, and it is §V41's with the roles swapped:** when an API moves state from a
long-lived object onto a per-operation one, the rename is the easy half and the LIFETIME is the
part that will crash. Ask who nulls the new field, and when.

· 🔴 **AUGMENT — the LIFETIME is not only the bone's: 26.2 DEFERS the whole draw, so everything
the 4.8.4 code read off `this` at call time is gone inside the callback.** §V18d is about a field
that is null outside a pass. The same port's `battle` phase then died twice more for the same
reason one layer out: a §W5 seam that re-supplies the removed `MultiBufferSource` (§V66) hands
GeckoLib's `submitCustomGeometry` a lambda, and that lambda runs **at the render frame, not at the
call** — by which time the renderer's own `animatable` field has been cleared, the collector the
caller pushed has been popped, and the camera with it. Symptoms:
`NullPointerException` on `getEntity()`, then *"No SubmitNodeCollector is bound."* — two different
messages, one cause, and neither names deferral. · **Fix: CAPTURE the context before the lambda and
RE-BIND it for the callback's own duration, restoring in a `finally`.** That means the renderer
needs a SETTER for its animatable (an interface method on the pair, not a field poke), and §V66's
"the seam must be a stack carrying the camera" applies to the deferred callback exactly as it does
to a nested renderer. · **The general check for any port that meets a record-then-replay API:** for
every value your callback reads off `this`, ask *who owns it between the call and the frame*. A
field that is correct at call time is not a captured value, and nothing in the type system says so.
(a ~660-file GeckoLib mob mod's GeckoLib-pass compat helper.)


**V19. `EntityType.Builder.build(String)` → `build(ResourceKey<EntityType<?>>)`** — §M's M4, reconfirmed
on 26.2. Write the replacement **fully qualified** in a rename table: a rewrite that needs three new
imports per file has to edit import blocks, and a table that only rewrites expressions cannot.

**V20. 🔴 The GameTest port (V5) has a MECHANICAL answer: generate the registrar from the annotations
you already have.**
· **The 26.x model, measured:** discovery is registration, in two halves — the **function** is a
`Consumer<GameTestHelper>` in the `Registries.TEST_FUNCTION` registry, supplied via
`TestFunctionLoader.registerLoader(out -> out.accept(fn(id), Klass::method))` from a static block; the
**instance** is a `GameTestInstance` (use `FunctionGameTestInstance`) carrying
`new TestData<>(env, structureId, maxTicks, setupTicks, required)`, registered from NeoForge's
`RegisterGameTestsEvent` — `e.registerTest(id, instance)`, with an environment obtained from
`e.registerEnvironment(id("default"), new TestEnvironmentDefinition.AllOf(List.of()))`.
`GameTestHelper` itself is **unchanged**, which is the whole opportunity: the test *bodies* port for
free and only the wiring is missing.
· **So do not rewrite the test files.** Keep the shared tree annotated (which is what the 1.21.1 target
needs anyway), and run a generator over the *prepared* 26.x copy that strips the three dead annotations
plus their imports and emits one registrar class from what those annotations said. One source of truth,
and a test added later is wired with no extra work. (the builder mod: 241 tests across 56 files, `tools/gametest_adapter.py`.)
· **Two guards the generator must have, both learned the hard way.** A test id must be
`<class>/<method>`, **not** the bare method name — 9 of the builder mod's 241 names are reused across classes, and
a duplicate id silently shadows one test with another rather than failing. And a `Identifier` path
accepts only `[a-z0-9_./-]`, so **camelCase method names must be snake_cased** and then asserted with
`re.fullmatch` — an illegal id throws at registration, i.e. at mod load, long after the build was green.

**V21. `EntityType`'s 158 constants moved to a new `EntityTypes` holder class.** ·
**Pattern:** `EntityType.ZOMBIE`, `EntityType.COW`, `EntityType.LIGHTNING_BOLT` ·
**Error:** `cannot find symbol: variable ZOMBIE` — location `class EntityType` ·
**Fix:** the same `Blocks`/`Items` shape arrives for entities: the constants live in
`net.minecraft.world.entity.EntityTypes` while `EntityType` itself keeps 2 statics, so the class
stays and only the constant references move. Note the sub-package moves ride along (`EntityTypes.COW`
is an `EntityType<animal.cow.Cow>`), so V3's map is a prerequisite. In a rename table write the
replacement **fully qualified** (V19): a table that rewrites expressions cannot add the import a bare
`EntityTypes.X` would need.

**V22. `MobEffects` took the players' names — and `Attributes` did NOT, which makes a bare member
rename actively dangerous.** · **Pattern:** `MobEffects.MOVEMENT_SPEED` / `MOVEMENT_SLOWDOWN` /
`DAMAGE_RESISTANCE` · **Error:** `cannot find symbol: variable MOVEMENT_SPEED` ·
**Fix:** `SPEED` / `SLOWNESS` / `RESISTANCE`. · ⚠ **`Attributes.MOVEMENT_SPEED` still exists in
26.2, unchanged**, so a bare `member:MOVEMENT_SPEED` rule renames both and silently breaks every
attribute call site — measured, 16 of them. Anchor on the owner. See **X7**.

**V23. 🔴 `getMaxBuildHeight()` (EXCLUSIVE) → `getMaxY()` (INCLUSIVE) — a silent off-by-one that
compiles.** · **Pattern:** `level.getMinBuildHeight()`, `level.getMaxBuildHeight()`, usually as loop
bounds · **Error:** `cannot find symbol` on both ·
**Fix:** `getMinY()` is a straight rename. `getMaxY()` is **not**: 1.21.1's `getMaxBuildHeight()`
returned `minY + height` and 26.2's `getMaxY()` returns `minY + height - 1` (verified in the
`LevelHeightAccessor` default's bytecode: `getMinY + getHeight - 1`). A rename alone shortens every
loop by one block, with a clean compile and no test that necessarily notices. Port it as
`getMaxY() + 1` to preserve meaning; write NEW code as `<= getMaxY()`. This is the archetype for
**W6**: flag such a row in the rename table, because six months later nobody remembers which rows
were judgements.
⚠ *Dated too late: this arrives at **1.21.2** (minor-version-deltas U7).* 

**V24. NeoForge's `DeferredSpawnEggItem` is gone, and a spawn egg is now a DATA COMPONENT plus a
client-side tint.** · **Pattern:** `new DeferredSpawnEggItem(typeSupplier, bgColor, hlColor, props)` ·
**Error:** `cannot find symbol: class DeferredSpawnEggItem` (confirm with `find-member.py` that it is
gone rather than moved) · **Fix:** two changes, neither a rename. The entity rides on the item —
`new SpawnEggItem(props.spawnEgg(type))`, where `Properties.spawnEgg` writes
`DataComponents.ENTITY_DATA` as a `TypedEntityData` and `SpawnEggItem.getType(stack)` reads it back;
the constructor takes only `Properties`. And the **two colours left Java entirely** (since 1.21.2 the
egg is tinted by `assets/<ns>/items/<id>.json`), so there is no argument to carry them. **Keep taking
the type as a `Supplier`** even though 26.x wants it eagerly — resolving at item-registration time is
the unbound-holder crash at **R3**; `Properties.spawnEgg` only stores it, so `get()` inside the item's
own supplier is late enough. With ~60 call sites in one file this is a **W5 compat pair**, not an
overlay: an overlay would mean two copies of a long file that drift the first time a mob is added.

**V25. Day and time became a whole subsystem — `setDayTime` is GONE, not renamed.** ·
**Pattern:** `level.setDayTime(1000)` / `getDayTime()` (the usual `/day`,`/night` command) ·
**Error:** `cannot find symbol: method setDayTime()` ·
**Fix:** there is no drop-in. 26.x has `net.minecraft.world.clock.ServerClockManager` and
`net.minecraft.world.timeline.Timeline`, and vanilla's own `/time set` now goes through
`ServerClockManager.moveToTimeMarker` / `commandTimeMarkersForClock`; `ServerLevelData` exposes
`getDayTimeFraction`/`setDayTimeFraction` and `get/setDayTimePerTick`. Budget per-site work, and read
`TimeCommand`'s bytecode for the intended path rather than guessing at the fraction API.

**V26. `Registry.get(ResourceLocation)` → `getValue`, arriving from the other direction.** This is
**M2** seen from a 1.21.1 base rather than a 1.21.4 one: on 26.2 `get(...)` returns
`Optional<Holder.Reference<T>>` and the plain-value lookup is `getValue(...)`, with
`getValueOrThrow(ResourceKey)` alongside. `getHolderOrThrow` is gone. Symptom is not "cannot find
symbol" but `incompatible types: Optional<Reference<Item>> cannot be converted to Item`, which is
easy to file under something else. Anchor any rewrite on the registry receiver — a bare `.get(` rule
will happily rewrite `Map.get` and `Optional.get`.

**V27. The technique that made V21–V26 cheap: `tools/find-member.py`.** An era jump is mostly "the
name is gone — what replaced it", and the honest answer is in the target's own jar. Point it at
whatever the target compiles against (ModDevGradle stages it at
`build/moddev/artifacts/minecraft-patched-<ver>-merged.jar`; NeoGradle at
`build/neoForm/**/outputs.jar`) and it prints every class declaring a matching member — or says the
member is **declared nowhere**, which is the answer that actually changes the plan, because "gone"
and "moved" need completely different work. **It is also the guard against V6's mistake**, where a
plausible cause was fitted to a single observation and stood as fact until it was checked.

**V28. The client's god-object split, and the four members every Gate-C harness reaches for.**
`Minecraft.screen` is **gone as a field**: 26.x split `Gui` so that `Minecraft.gui` keeps the
SCREEN stack (`screen()` / `setScreen`) and delegates the heads-up display to `Gui.hud`, a
separate `Hud` class. So `mc.screen` → `mc.gui.screen()` and `mc.gui.getBossOverlay()` →
`mc.gui.hud.getBossOverlay()`. The HUD visibility flag moved with it and became **a toggle with
no setter** (`Hud.toggle()` / `isHidden()`), so `mc.options.hideGui = true` has no
single-expression equivalent and must be a compat pair — and the pair must **read before
toggling**, because a bare `toggle()` on an already-hidden HUD turns it back *on* and the
photograph comes back with the hotbar across it. `Minecraft.getMainRenderTarget()` is gone too:
the target is `mc.gameRenderer.mainRenderTarget()`. Measured on the builder mod: 65 + 12 + 22
sites, i.e. **a quarter of the whole port's client-side error count is these four members.**

**V29. 🔴 `SavedData` → `SavedDataType` + a `Codec` — and the port NOT to take.**
· **Pattern:** a `SavedData` subclass with `save(CompoundTag, HolderLookup.Provider)` and a
static `load(CompoundTag, Provider)`, fetched via
`level.getDataStorage().computeIfAbsent(new SavedData.Factory<>(X::new, X::load), NAME)` ·
· **Error:** `method does not override…` on `save`; `Factory` not found ·
· **Fix:** 26.x declares a store as `SavedDataType(Identifier, Supplier<T>, Codec<T>)` handed to
`SavedDataStorage.computeIfAbsent`. **Do not write a `RecordCodecBuilder` codec per store.**
Those classes usually exist to be *defensive* — skip a corrupt record rather than abort,
tolerate a legacy unversioned file, tolerate a future version, never crash the reload — and a
generated codec does the opposite by default: it fails the whole decode on one bad field. That
is a silent regression per store, in precisely the behaviour they were written for, visible only
when a player's world is already damaged. Instead make the codec an **adapter over
`CompoundTag.CODEC`**: `CompoundTag.CODEC.xmap(tag -> load(tag, null), d -> d.save(new
CompoundTag()))`. The tag round-trips untouched, the hand-written load/save do the work, the
on-disk format is unchanged, and there is no rewrite to get wrong. Pass `null` for the provider
only after checking no store reads it; one that does needs the level-sensitive
`SavedDataType.Factory` overload instead. (the builder mod: six stores, one ~40-line helper.)
· ⚠ **AUGMENT — now a converter, and it found a silent data loss the adapter alone does not cover.**
`tools/convert-saved-data.py` (run by the 26.2 mechanical stage) generates `<pkg>.SavedDataCompat` and repoints
three shapes, leaving every load and save body as written: `extends SavedData` → `extends SavedDataCompat.Base`
(which re-declares the removed `save(CompoundTag, Provider)`, so each `@Override` stays valid);
`SavedData.Factory` → `SavedDataCompat.Factory` (the same record shape); and
`<storage>.computeIfAbsent/get(factory, name)` → `(SavedDataCompat.type(factory, name))`, where the receiver is a
`getDataStorage()` call or a variable declared `DimensionDataStorage` (a `Map.computeIfAbsent` is never touched).
· **The provider question is answered, not guessed:** NeoForge 26.2 patches `SavedDataType` so its constructor
and codec factories receive the `ServerLevel`, so the bridge hands BOTH load and save `level.registryAccess()`,
as 1.21.1 did. No store has to be checked for whether it reads the provider.
· 🔴 **The file MOVES, and that is a world reset with a clean compile and a green gate.** 26.2 saves a store at
`<dimension>/data/<namespace>/<name>.dat`; 1.21.1 wrote `<dimension>/data/<name>.dat`. Ported by the adapter
alone, every 1.21.1 world opens on 26.2 with each store EMPTY (a boss counted as never beaten, a ban list gone)
and nothing logs. The bridge's constructor factory runs only when no 26.2 file exists, so it is the one place
to carry the old file across: it reads `<dimension>/data/<name>.dat` with `SavedDataStorage.readTagFromDisk`,
loads the `data` compound with the store's own load, marks it dirty (so it is written at the new path) and
logs one line. The old file is left in place. A hand port that wrote the adapter recorded this as an accepted
loss; it does not have to be one.
· **REFUSED by name:** `storage.set(name, data)` (26.2's `set` needs the type), a storage call that is not
`(factory, name)`, and a subclass with no `save(CompoundTag, Provider)` (which `Base` turns into a compile
error rather than a silent no-op).
· **Measured:** a boss-effects library's 5 store files, 15 sites: every `SavedData` error gone, 1378 → 1372
(the stores' remaining errors are unrelated). Compiled against the patched 26.2 classpath in isolation, all
converted shapes — a storage variable, a nested `Factory` import, fully qualified names, a lambda loader —
produce classes. Two converter bugs it shipped without and the real tree found: a `computeIfAbsent(...)` inside a
COMMENT, and an argument splitter that read a lambda's `->` as a closing generic bracket. A self-check over
hand-written samples had neither shape; the first real tree had both.

**V30. `EntityType.create(Level)` gained an `EntitySpawnReason`, and the reason is INERT — but
`canSpawn` is not.** Read the 26.2 body before agonising over which reason to pass: `create`
wraps it in an `EntitySpawnRequest` whose reason is never consulted, so `TRIGGERED` and
`COMMAND` are indistinguishable here. (It is *not* inert in `spawn(...)`, which forwards it to
`finalizeSpawn`.) What genuinely changed is the gate beside it: `canSpawn` now returns **false
on PEACEFUL** for anything not `allowedInPeaceful`, so `create` returns **null** where 1.21.1
handed back an entity. Null-guard every call site — a mob-summoning command that silently does
nothing on a Peaceful world reads as a broken command, not as a difficulty setting.

**V31. 🔴 Entity AND BlockEntity save/load moved to `ValueOutput`/`ValueInput` — and the two want
OPPOSITE treatments.** · **Pattern:** `saveAdditional(CompoundTag, HolderLookup.Provider)` /
`loadAdditional(...)` on a BlockEntity; `addAdditionalSaveData(CompoundTag)` /
`readAdditionalSaveData(CompoundTag)` on an Entity · **Error:** `does not override`, then
`CompoundTag cannot be converted to ValueOutput` throughout the body ·
**Fix, and the split is the point:** `ValueOutput`/`ValueInput` are codec-shaped — scalars plus
codec-typed children — so a body that writes only `putInt/putLong/putString` translates *verbatim*
(the parameter can even keep its name, so `super` calls and every statement are untouched), while a
body that writes a **nested compound** (a block state, a blueprint list) has no direct translation
at all. Count them before choosing: rule the scalar-only ones, and give the rest a §W5 pair that
keeps the tag. `ValueOutput.store(CompoundTag)` is NeoForge's own extension and merges entries at the
top level, so the on-disk layout is byte-identical to what 1.21.1 wrote; the read side has no such
helper and goes through the same `MapCodec.assumeMapUnsafe(CompoundTag.CODEC)` route NeoForge itself
uses for `ValueInput.keySet()`. · **Do NOT invent the provider on the save side.** `ValueOutput`
carries none, and manufacturing one from a nullable `level` invites a future body to depend on
something that is not really there; `ValueInput.lookup()` genuinely supplies one, so an asymmetric
pair (`saveNbt(tag)` / `loadNbt(tag, registries)`) is the honest shape. · **The test-side cousin:**
`BlockEntity.loadWithComponents(tag, provider)` takes a `ValueInput` now, so every persistence
round-trip test needs `TagValueInput.create(ProblemReporter.DISCARDING, provider, tag)`.

**V32. Villagers: a datapack-registry change that a rename table CANNOT express.** ·
**Pattern:** `VillagerProfession.BUTCHER` in a switch, `villagerData.setProfession(...)` ·
**Error:** `ResourceKey<VillagerProfession> cannot be converted to VillagerProfession`, and
`setProfession`/`setType`/`setLevel` all missing · **Fix:** 26.x made `VillagerProfession` and
`VillagerType` datapack registries, so every familiar constant changed TYPE and `VillagerData` became
a record of `Holder`s with `withProfession(provider, key)` in place of setters. **The reason no rule
works** is that a shared `professionFor(store)` switch would have to *return a different type* on
each target — the thing Java has no alias for. Name the job with **your own enum in shared source**
and put the translation in the §W5 pair; the mod's own code then stops naming a vanilla type at all,
which is better code on the old version too.

**V33. Copper is V11's collection shape applied to weathering.** `Blocks.OXIDIZED_COPPER` and the
other fifteen per-family constants collapsed into one `WeatheringCopperCollection`, picked by
`weathering()`/`waxed()` then `unaffected()`/`exposed()`/`weathered()`/`oxidized()`. Same
longest-key-first ordering trap as V11: rewrite `WAXED_` and `OXIDIZED_` **before** the bare
`COPPER_BLOCK` rule, or it clips their prefixes.

**V34. 🔴 A rename that changes the VALUE is not a rename — `MapColor.calculateRGBColor` →
`calculateARGBColor`.** · **Pattern:** any code unpacking a map colour · **Error:** `cannot find
symbol: calculateRGBColor` · **Fix, and read both bodies before believing the name:** 1.21.1's
returns `0xFF000000 | blue << 16 | green << 8 | red` — **ABGR**, the byte order the map TEXTURE
wants — while 26.x's returns `ARGB.scaleRGB(ARGB.opaque(col))`, genuinely ARGB. Callers that
compensated for the old packing (a ~840-file content mod had one, after a red shopfront banner rendered
blue) are left **double-swapping** by a bare rename, with every gate green, because a map full of the
wrong colours is still a map full of colours. Put it in a §W5 pair whose method answers the QUESTION
("what colour is this, as 0xRRGGBB") and let each side pack it its own way. · **The general test:** a
name that changes to describe the same value is a rename; a name that changes because the value
changed is a behaviour change wearing a rename's clothes, and only `javap`-ing both bodies tells them
apart.
⚠ *Dated too late: the value change arrives at **1.21.2** (minor-version-deltas U9).* 

**V35. `ArmorItem` is gone — armour is a plain `Item` plus components.** `Properties.humanoidArmor
(material, type)` writes the EQUIPPABLE and attribute components; `ArmorMaterials` moved to
`world.item.equipment` and its members are plain `ArmorMaterial` rather than `Holder`. **The question
"is this a boot?" moves with it**, from an `instanceof ArmorItem` + `getType()` test to the slot the
item's own `Equippable` component names. Both halves belong in one pair.

**V36. Reload listeners changed at BOTH ends.** `SimpleJsonResourceReloadListener` became generic and
codec-driven (`super(Codec<T>, FileToIdConverter.json(dir))`, with `apply` receiving parsed values),
and `AddReloadListenerEvent` became `AddServerReloadListenersEvent` whose `addListener` takes a
**name** — listeners are a dependency graph in 26.x, so each has to be addressable. · **Ask for the
RAW map unless you want the framework deciding**: a reader that parses defensively by hand (because
the files are another mod's format, and one malformed file must not cost the rest) gets that decision
taken away by a codec-typed listener, quietly, in the direction of dropping data.
`ExtraCodecs.JSON` + `FileToIdConverter.json(dir)` reproduces the old raw behaviour.

**V37. `SoundEvents` is a MIX, on both versions — never blanket-`.value()` it.** Sounds with variants
are `Holder.Reference<SoundEvent>`; the rest are plain `SoundEvent`, and *which* is which changed for
a handful of constants between 1.21.1 and 26.2. Measured on one mod: **2 of the 53** constants it
names bare moved plain → Holder. A blanket rule turned a 230-error build into a 289-error one. Diff
the two jars (`javap … SoundEvents | grep Holder$Reference`) and name the difference. ·
`ServerPlayer.playNotifySound` is also gone: its body was a `ClientboundSoundPacket` down that one
player's connection, which is worth keeping rather than downgrading to `level.playSound` — a rank-up
fanfare is addressed to the person it happened to, not the neighbourhood.

**V38. Small 26.2 moves confirmed against the jar (cluster).** `Entity.getServer()` removed (go
through `level()`, which on `ServerPlayer` is covariantly a `ServerLevel`) · `Entity.hurt` split into
`hurtServer(ServerLevel, …)` / `hurtClient(…)` · fall distance is a `double` in `Block.fallOn` and
`Entity.causeFallDamage` · `isControlledByLocalInstance` → `isLocalInstanceAuthoritative` (**this row is
wrong as a plain rename — see V49**) ·
`LivingEntity.kill()` → `kill(ServerLevel)` · `EntityType.is(tag)` → `getType().builtInRegistryHolder()
.is(tag)` · `ItemCooldowns.addCooldown` is keyed by the ItemStack (or a cooldown-group `Identifier`)
rather than the Item · `Inventory.items` → `getNonEquipmentItems()` · `ChunkPos(BlockPos)` removed ·
`Level.isDay()` → `isBrightOutside()` · `RegistryAccess.registry(k)` → `lookup(k)` ·
`BlockState.isSolidRender(level,pos)` → `isSolidRender()` · `ClickEvent` is an interface with a record
per action · authlib's `GameProfile` is a record, so `getName()` is gone (`Player.nameAndId().name()`)
· `MobEffects` took three more players' names (`DIG_SPEED`→`HASTE`, `JUMP`→`JUMP_BOOST`,
`DAMAGE_BOOST`→`STRENGTH`) · `EntitySpawnReason.SPAWN_EGG` → `SPAWN_ITEM_USE` ·
`DataComponents.UNBREAKABLE` is a marker (`Unit`) rather than a record · `ThrowableItemProjectile`
moved sub-package **and** takes its stack explicitly instead of resolving `getDefaultItem()` ·
`FMLEnvironment.dist` is a method · FML's `@EventBusSubscriber` lost `bus` entirely (inferred from the
event) · `RegisterColorHandlersEvent.Item`/`.Block` became `ItemTintSources`/`BlockTintSources`.
· `LevelRenderer.getLightColor(level, pos)` → **`LightCoordsUtil.getLightCoords(...)`** (a real
rename — same two arguments, same packed pair, and 26.2's `BlockAndTintGetter` extends the
`BlockAndLightGetter` the parameter widened to; `LevelRenderer` itself still exists) ·
`RenderShape` lost `ENTITYBLOCK_ANIMATED`, leaving only `INVISIBLE` and `MODEL` (**see V64 — the
obvious replacement compiles on 1.21.1 too and is wider there**) ·
`Minecraft.useAmbientOcclusion()` → `options.ambientOcclusion().get()` ·
`ModelManager.getAtlas` → `AtlasManager.getAtlasOrThrow` · `Model.renderToBuffer` and `Model.root()`
became **final**, so a shared drawable model needs a differently-named hook behind a §W5 pair ·
a 26.2 overlay needs `org.jspecify.annotations.Nullable` for the TYPE_USE positions in
`extractRenderState`'s signature.
⚠ *`SPAWN_EGG` → `SPAWN_ITEM_USE` is **1.21.2**, and so are `kill(ServerLevel)`, `hurtServer` and the cooldown key (minor-version-deltas U2/U11).* 

**V38b. ⚠ AUGMENT to V38 — FML's `@EventBusSubscriber` lost `bus()`, and the class is NOT in the
jar you will look in.** V38 lists the removal in passing; the part that costs time is finding it.
`net.neoforged.fml.common.EventBusSubscriber` does not live in the `neoforge-*-universal` jar at
all — it is in **`net.neoforged.fancymodloader:loader`**, so `unzip`ing the obvious artifact
answers `filename not matched` and reads as "the class is gone entirely". `javap` the loader jars
of both targets side by side (`loader-4.0.42.jar` against `loader-11.0.16.jar`) and the diff is one
line: 26.x infers the bus from the event type and dropped the attribute.
· **The general form, and it is X25b's:** the NeoForge "stack" is several artifacts, and which one
holds a given class is not guessable from its package — `fml.*` is loader, `bus.api.*` is eventbus,
`api.distmarker.*` is mergetool-api. When `find-member.py` or an `unzip` says a loader class is
declared nowhere, widen to every jar on the resolved classpath before believing it.

**V39. 🔴 `moveTo` → `snapTo`, and V17's version of this was BACKWARDS — the archetype of a rename
that compiles everywhere and changes behaviour.** · **Pattern:** `entity.moveTo(x, y, z, yaw, pitch)`,
the ordinary "put this entity here" call every summon/teleport/spawn path uses · **Error:** `cannot
find symbol: moveTo` — but **only at the `BlockPos` and `Vec3` call sites**, which is the trap ·
**Fix:** 26.2 renamed **both** placement families in lockstep, and the honest mapping is the boring one:

| 1.21.1 | 26.2 | overloads |
|---|---|---|
| `moveTo` | **`snapTo`** | 5 — `(x,y,z)`, `(x,y,z,yaw,pitch)`, `(BlockPos,yaw,pitch)`, `(Vec3)`, `(Vec3,yaw,pitch)` |
| `absMoveTo` | `absSnapTo` | 2 — doubles only |
| `absRotateTo` | `absSnapRotationTo` | — |

Verified by reading both bodies: 1.21.1's `moveTo(x,y,z,yRot,xRot)` is **byte-for-byte** 26.2's
`snapTo(...)` (`setPosRaw` → `setYRot`/`setXRot` → `setOldPosAndRot` → `reapplyPosition`).
`absSnapTo` is a *different* method that clamps to the ±3e7 world border and goes through `setPos()`.
· **Why it is worth its own entry rather than a row in V38:** `absSnapTo` looks like the right answer
(it is the one with "abs" in it, and `snapTo` reads like a relative nudge), so V17 recorded exactly
that, and the mistake is nearly invisible — `absSnapTo` **has the two double-argument overloads**, so
every call passing doubles compiles and silently gets clamping plus a different position pipeline.
Measured on one mod: **33 of 35 call sites compiled green under the wrong mapping**; the 2 that failed
did so only because `absSnapTo` has no `BlockPos` overload. Without those two, the rule would have
shipped. · **The generalisation, and it is the cheap check:** when a renamed method exists in *two*
similarly-named families, do not pick by which name reads right — **diff the OVERLOAD SETS**. The
family that kept all five overloads is the rename; a family with a strict subset is a different method
that merely accepts some of the same arguments. Same discipline as V34 (`calculateRGBColor` →
`calculateARGBColor`, where the *value* changed) and V27 (ask the jar): a name that describes the same
operation is a rename; anything else is a behaviour change wearing a rename's clothes.

**V40. `NearestAttackableTargetGoal`'s filter changed ARITY — `Predicate<LivingEntity>` →
`TargetingConditions.Selector`.** · **Pattern:** the standard filtered target goal,
`new NearestAttackableTargetGoal<>(mob, Monster.class, 120, true, false, candidate -> …)` ·
**Error:** `incompatible types: incompatible parameter types in lambda expression` ·
**Fix:** 26.2's 4-arg and 6-arg constructors take `TargetingConditions.Selector`, a **two**-argument
SAM `(LivingEntity candidate, ServerLevel level)`; 1.21.1's took a one-argument
`Predicate<LivingEntity>`. The 3-arg and 4-arg-boolean forms are unchanged, so only *filtered* goals
break. · **Note what this does to catalog #71**, which warns — correctly, for 1.21.1 — *"the predicate
is single-arg; do not convert it to a 2-arg lambda; `TargetingConditions.Selector` is a **different**
interface used elsewhere."* On 26.2 that different interface is the one this constructor takes. A
lesson pinned to a version is not a lesson about the API; date-stamp them.
· **In a §W multi-version tree this is a compat pair, not a rename** — a rewrite cannot add a
parameter to a lambda. Have both sides take the 1.21.1 shape (a one-argument predicate on the
candidate) and let the 26.2 implementation adapt with `(candidate, level) -> filter.test(candidate)`.
**Do not widen the shared signature to carry the level** just because one version offers it: that puts
a 26.2-only concept into shared source to serve filters that — measured across 13 call sites here —
never look at it.

**V41. 🔴 A behavioural HOOK became a DATA PROPERTY — `updateEntityAfterFallOn` → `bounceRestitution`,
and what a coefficient cannot say.** · **Pattern:** a block that bounces you, overriding
`Block.updateEntityAfterFallOn(BlockGetter, Entity)` to set the outgoing velocity outright (what
1.21.1's SlimeBlock and BedBlock do, and what any custom bouncy block copies) ·
**Error:** `method does not override or implement a method from a supertype`; `find-member.py` then
reports the name **declared nowhere** — gone, not moved ·
**Fix:** 26.2 moved bouncing out of the block entirely. `Entity.restituteMovementAfterCollisions`
does it, driven by `BlockBehaviour.Properties.bounceRestitution` — a **coefficient**, so 26.2's own
SlimeBlock has no bounce code left at all. · **The migration question is not "what is the new name"
but "can a coefficient say what my hook said"**, and often it cannot: a proportional multiplier has
no way to express a FLOOR (a one-block drop should still bounce properly) or a CAP (a fall from the
build limit must not fire you out of the world), which is exactly what a hand-written bounce usually
has. Vanilla alone would force a behaviour change here. · **What saves it is a NeoForge extension
that keeps the entity in scope:** `IBlockExtension.getBounceRestitution(Level, BlockPos, BlockState,
Entity)`. Because the block is handed the entity, it can read the speed the entity is *actually*
arriving at and return `desiredOutgoing / -incomingY` — reproducing floor, cap and any other curve
from a coefficient. The ratio cannot run away, either: vanilla only bounces once
`-motion.y >= getEffectiveGravity()`, bounding the divisor at roughly 0.08.
· **The general shape, which recurs across this era jump:** when a hook disappears into a property,
check whether the property is a *constant* or a *callback*. A constant is a feature loss to be
recorded; a callback (vanilla's own, or the loader's) is a full recovery. Answer it from the
consumer's bytecode — reading who calls `getBounceRestitution` and with what is what turned this from
"the bounce is gone" into a compat pair. · **In a §W tree it is a pair, and note the pair is over an
OVERRIDE rather than a call** (§W5's usual shape): Java cannot declare a method conditionally, so the
shared block subclasses a per-target abstract base that carries the version's own override and
delegates to one shared question — *given how fast you arrived, how fast should you leave?* That
keeps the ~90-line block itself entirely shared instead of duplicated per target.

**V42. 🔴 Item TINTS left Java for JSON — and the assertion you replace them with must not be
weaker.** · **Pattern:** `RegisterColorHandlersEvent.Item` with an `ItemColor` lambda
`(stack, tintIndex) -> argb`, the standard way a mod tints one greyscale sprite into a dozen
flavours · **Error:** `cannot find symbol: class Item` in `RegisterColorHandlersEvent` ·
**Fix:** 26.x replaced the per-item Java callback with **`ItemTintSources`**: you register a named
`ItemTintSource` (one method, `calculate(ItemStack, ClientLevel, LivingEntity)`) and each item's
**client item definition** — `assets/<ns>/items/<id>.json` — names which sources tint which layer.
· **No expressiveness is lost, and it is worth stating plainly rather than filing as a regression:**
the old callback got `(stack, tintIndex)`; a source gets the stack **plus the level and the holding
entity**, which is strictly more. What IS lost until you generate the JSON is *any tint at all* —
the Java half compiles and registers, the item renders greyscale, and nothing logs. Budget the
asset generation in the same change. **Spawn-egg colours are the same shift** (V24): the two ARGB
ints left the constructor and live only in that JSON.
· **The pattern that ports it cleanly** is to move the tint TABLE into shared source — a list of
`(item, layer, ToIntFunction<ItemStack>)` — and let each target's compat pair install it its own
way. The 1.21.1 side loops the table into `ItemColor` lambdas; the 26.2 side registers one
`ItemTintSource` per layer and the JSON points at it. One table, two bindings, and the colours are
reviewable in one place instead of scattered across a dozen client classes.
· **Doing that found a handler that had never been subscribed to anything.** The builder mod's battle-egg
`registerItemColors` had existed since the egg landed and was on no bus, so the egg had been
rendering untinted the whole time — the §S4 shape exactly: javac checks names and types, never
whether anything calls you.

**V42b. 🔴 CORRECTION AND ESCALATION of V42 — it is not the tints that moved, it is the ITEM→MODEL
BINDING, and a mod that ships no `items/<id>.json` renders EVERY item as the missing cube.**
V42 records that tints became a field in `assets/<ns>/items/<id>.json` and says to budget the asset
generation. That is true and it badly undersold the finding: `tints` is one optional field of a file
whose *purpose* is to bind the item to its model at all.
· **Up to 1.21.1 the binding was CONVENTION** — an item resolved `models/item/<same id>.json` and
nothing declared it anywhere. **From 1.21.2 it is DATA**: `ClientItemInfoLoader` reads
`assets/<ns>/items/` (via `FileToIdConverter.json("items")`) and `ModelManager.getItemModel` looks
the item up in that map. The miss path is explicit in the bytecode —
`LOGGER.warn("Missing item model for location {}")` then `return this.missingModels.item()`. There
is **no fallback to the same-id model.**
· **Measured, and the shape of the number is the proof:** The builder mod shipped **231 item models
and zero client item definitions**, and its 26.2 client logged **exactly 231** `Missing item model
for location examplemod:…` lines against **0** on 1.21.1, same commit. Every item in the mod was
the magenta cube.
· **Why nothing caught it, and this is the reusable part.** Gate C's asset check asserted
`models=6/6` — and it was *right*: the model resources exist. What had gone was the **binding**, and
no assertion anywhere was phrased as a question about a binding. "The resource is present" and "the
game can find it" became different claims in 1.21.2, and every check in the repo was written when
they were the same claim. This is §S4's rule in the resource layer: javac checks names, and a
resource check that asks "does the file exist" cannot see that nothing points at it any more.
· **Vanilla ships 1538 of these files** for its own items, generated by its own datagen. A mod has
to do the same — there is no runtime hook: NeoForge's `RegisterItemModelsEvent` registers model
*types* (`minecraft:model`, `minecraft:select`), never a per-item binding, exactly as
`ItemTintSources` registers source *types* and not per-item tints.
· **The generator wants ONE non-obvious design decision.** The naive generator has to know which
items are tinted — a roster that lives in Java, built by loops over flavour enums, so copying it
into the generator is a fifth place for it to drift. It does not need to know: declare the mod's own
tint source on **every layer of every item**, and have the shared tint table answer **opaque white**
for any (item, layer) no rule claims, which is exactly "no tint". The Java table stays the single
source of truth for which items are tinted, adding a flavour later needs no regeneration at all, and
the generator's only input is each model's own `textures` block — `layer0..layerN` gives the tint
count, and a parent-based model (a block item) gets none. ⚠ Index that lookup: 1.21.1 binds a
handler per item and never consults the table at render time, so a list scan was free; 26.x calls it
per rendered quad on every item, so an O(rules) scan becomes ~75 comparisons per item on screen.
· **Gate it from BOTH ends, because the two failures are different.** A Gate-B test over the LIVE
REGISTRY catches an item that has no definition (including one registered with no model at all); a
Gate-A test comparing the two shipped directories catches a forgotten regeneration in seconds rather
than in minutes, and asserts the tint-array length equals the model's layer count, since the array
is indexed *by* layer — too few and the last sprite can never be coloured. Run the registry test on
**both** targets even though only one reads the files: on the old version the directory is inert,
and asserting it there anyway is what stops the check rotting on the version nobody runs today.

**V42c. 🔴 26.x DELETED the vanilla spawn-egg template, both its textures, AND any spawn-egg tint
source — every modded egg must now supply its own art. (Completes V24, and is the second layer
V42b unmasked.)** · **Pattern:** the universal modded-egg model,
`{"parent": "minecraft:item/template_spawn_egg"}`, which on 1.21.1 is a two-layer greyscale egg
(`item/spawn_egg` + `item/spawn_egg_overlay`) that the game tints per mob from the two colours
passed to the item constructor · **Symptom:** the model fails to resolve and the egg is the missing
cube — and on a port it will not even be the FIRST thing you see ·
**Fix:** verified against the 26.2 client jar — `models/item/template_spawn_egg.json`, `textures/
item/spawn_egg.png` and `textures/item/spawn_egg_overlay.png` are **all three gone**, and
`ItemTintSources` registers `constant / dye / firework / grass / map_color / potion / team_color
/ custom_model_data` and **nothing for spawn eggs**. Vanilla stopped needing them: each of its ~80
eggs is now a hand-drawn sprite (`zombie_spawn_egg` is plain `item/generated` over
`item/zombie_spawn_egg`). A mod with 38 eggs cannot follow that, so it draws the two greyscale
layers ONCE itself and tints them through its own tint source (V42b) with the same two colours the
constructor already takes. **Draw them, do not copy them out of the 1.21.1 jar** — that would be
shipping Mojang's art in the mod jar; a 16×16 ellipse plus speckles is ~60 lines against `zlib` and
`struct`, needing no image library.
· **In a §W tree it is ONE overlaid resource, not 38.** Point every egg at
`<modid>:item/template_spawn_egg` in the shared tree and overlay only that template: the old target
parents it to vanilla's (so the shipping version is byte-identical and keeps vanilla's own tinting),
the new one gives it your two layers. The colours then move from "accepted and ignored" in the
compat pair to two real tint-table rules, registered at item-construction time — which is common to
both dists, so a dedicated-server gate can see them without driving any client-only registration.
· **🔴 THE SEQUENCING LESSON, and it is §144's shape one era later:** none of this was visible
while V42b's definitions were missing, because `getItemModel` returns the missing model **before**
resolving the model chain — so 231 missing-binding warnings *masked* 38 dead parents completely.
Fixing the first defect is what surfaced the second, and the client log went from "one problem" to
"a different problem" rather than to green. **Expect a second wave after any resource-layer fix, and
read it as progress**: a short-circuit upstream hides everything downstream of it, so the count of
distinct failures is not a measure of how much is left.

**V43. A REMOVED client mechanism needs an `Optional`-returning compat pair, not a weaker
assertion.** · **Pattern:** a Gate-C test asserting on something whose whole mechanism is gone —
`DimensionSpecialEffects` (26.x has a `DimensionType.Skybox` **enum** instead, so there is no
effects class to name and no `effects` id to match a renderer against), or
`IClientItemExtensions.getCustomRenderer` (item rendering moved to the client item definition,
the same Java→JSON shift as V42) · **Error:** `cannot find symbol` on the class the assertion
names · **Fix:** make the compat pair answer with `Optional<String>` — the old target returns the
real class or renderer name, the new one returns `empty()` — and have the test **skip that leg
explicitly** when it is empty, saying so in its output. · **Why not just delete the assertion or
soften it:** both leave a green run that means less than it used to and says nothing about the
change. An empty `Optional` is a statement — *this Minecraft binds nothing here in Java* — that a
reader can check, and it keeps the 1.21.1 leg asserting exactly what it always did. This is the
test-side counterpart of the rule at V41: establish whether the thing is *gone* or merely *moved*
before choosing what to write, and record which one you found.

**V44. `MushroomCow` is a SIBLING of `Cow` in 26.2, not a subclass — a sub-package move can change
the hierarchy.** · **Pattern:** `instanceof Cow` used to catch mooshrooms too (the milk machine's
"is this milkable" test) · **Error:** none — it compiles and silently stops matching ·
**Fix:** both moved under `world.entity.animal.cow`, but they now share an `AbstractCow` parent
rather than one extending the other, so a `Cow` check no longer sees a mooshroom. · **The
generalisation for §V3's move map:** the map is generated by matching SIMPLE NAMES across the two
classpaths, so it is blind to a changed supertype. Every `instanceof` on a moved vanilla type is
worth one `javap` — the rename lands, the compile is green, and the behaviour quietly narrows.


**V45. 🔴 THE DATAPACK is an era jump too, and one file can serve BOTH versions — because a Mojang
codec ignores fields it does not know.** · **Pattern:** a mod's own `dimension_type`,
`worldgen/noise_settings`, `worldgen/biome` JSON, carried across unchanged ·
**Runtime:** not silent this time — `Failed to parse <id> from pack mod/<modid>` per element, then
`Failed to load registries`, and the server refuses to start. (Contrast §S1: the 1.20→1.21
directory renames were silent. A *required field* is loud; a *moved* one is not — see the third
bullet.) · **Measured on 26.2, 114 elements:**
· `dimension_type` gained a REQUIRED `has_ender_dragon_fight`.
· the noise router replaced `initial_density_without_jaggedness` with `preliminary_surface_level`,
  also required. Vanilla's own NETHER uses a plain `0.0` — the right value for any world whose
  surface rules do not key off a preliminary surface level; the overworld's elaborate
  `find_top_surface` tree is not a default to copy.
· 🔴 **and the quiet half:** `bed_works` / `natural` / `ultrawarm` / `piglin_safe` / `has_raids` /
  `respawn_anchor_works` MOVED OUT of `dimension_type` into a namespaced `attributes` map. Those
  keys still parse and are now **ignored**, so the file loads and the flags stop meaning anything.
  Nothing reports it. Check every key you carry across against the target's own vanilla file, not
  only the ones the parser complained about.
· ⚠ **but an invented attribute is a HARD failure, not an ignored field** — `attributes` values are
  resolved against a registry (`Unknown registry key in ...environment_attribute`). `piglin_safe`
  has no counterpart at all: 26.2 splits the idea into `nether_portal_spawns_piglin` and
  `piglins_zombify`. Read the vanilla files for the legal set.

**THE RULE THIS ESTABLISHES, and it is the datapack counterpart of §W:** a `RecordCodecBuilder`
codec ignores unknown fields, so **one data file can carry the UNION of two versions' schemas and
parse correctly on both**. That is why a multi-version mod usually needs no per-version data at all.

**The exception, and how to recognise it:** the union fails when the same key needs INCOMPATIBLE
TYPES on the two versions and is required on both. Measured: biome `carvers` is a **map** keyed by
carving step on 1.21.1 and a **list** on 26.2. No value parses twice, and omitting it is not open
either. Such files go to a per-version **resource overlay** — the resource-side twin of
`src/mc21` vs `src/mc26` — and only those files; everything else stays shared. (the builder mod:
35 biomes overlaid, ~250 other data files shared.)

⚠ **There are TWO exceptions, and the second one is far bigger than the first.** The one above is a
*type* clash — same key, map vs list. The other is a **FORM** clash: the same value written two
ways, where each version actively REJECTS the other's spelling rather than ignoring it. The measured
case is a recipe **ingredient**: an object (`{"item": "minecraft:diamond"}`, `{"tag": "x"}`) on
1.21.1, a bare string (`"minecraft:diamond"`, `"#x"`) from 1.21.2 (§M6, read forwards). That is not
a rare corner: on one mod it was **116 of 117 recipe files**, against exactly one biome for the type
clash. So "the union usually works" stays true per FILE KIND and can be very wrong per FILE COUNT —
check ingredients early, because they decide whether a port needs a resource-overlay *mechanism* or
just a couple of overlaid files.

**And an overlay of a hundred files must be DERIVED, not written.** Hand-copying is §W14's mistake in
the data layer: a second copy of a file that is otherwise identical, which every future edit has to
be made to twice with nothing to say it drifted. Generate the target's form from the shared file
with a named transform + a `--check` wired into `check`. Two rules that transform needs, both of
which are §S1b's *"a codemod that quietly produces a wrong result is worse than one that refuses"*:
name the ingredient positions **per recipe type**, read off each serializer's own `MapCodec` — a
shape-matcher for `{"item": X}` will also rewrite a result (`{"id": X}`) or a loot entry
(`{"name": X}`) — and make anything unrecognised REFUSE by name. Then assert afterwards that no
old-shape value survived, which is the one thing a per-type table cannot check about itself.

**V45b. ⚠ AUGMENT to V45 — the FORM clash is not confined to recipe ingredients: an ADVANCEMENT's
`display.icon` is an ItemStack, and 1.21.2 renamed the stack's item field there too.** ·
**Pattern:** `"icon": {"item": "ns:id"}`, which is what every 1.21.1 advancement writes ·
**Runtime:** `Couldn't parse data file '<ns>:<adv>' … No key id in MapLike[{"item":…}]; Second: Not
a string` — and then the whole advancement is dropped, with a green gate either side of it (§144) ·
**Fix:** `{"id": "ns:id"}`. This is catalog **§122(b)**'s `item` → `id` rename for recipe RESULTS
arriving one layer up, so a mod that has already fixed its recipes has not fixed this. · **It needs
its own transform rather than riding on the ingredient one**, and the reason is the distinction
§V45 draws: an INGREDIENT becomes a bare string on 26.2, while a STACK stays an object (it can
carry `count` and `components` beside the id). One rule cannot produce both, and a shape-matcher
that tried would rewrite each into the other's form. · **The count is the tell**: 9 data files
failed to parse and the ingredient transform matched 8 of them. A transform that reports what it
matched, against a failure list you have actually read, is what turns "one more error, probably the
same thing" into a second named cause (§X32).

**V46. 🔴 A NeoForge builder that handed out a MUTABLE collection now hands out a BUILDER — and the
rename that "fixed" it is the most dangerous shape a rule can take.** · **Pattern:**
`builder.getMobSpawnSettings().getSpawner(category).clear()` in a `BiomeModifier` — the standard
way to empty a spawn category · **Error:** on 26.2 `getSpawner` returns
`WeightedList.Builder<SpawnerData>`, which has `removeIf` and no `clear` · **Fix:** a §W5 compat
pair over `removeIf(w -> true)`.
· **What actually happened, and it is the lesson.** A rename rule got the old call compiling as
`getSpawner(c).build().unwrap().clear()`. It type-checks. The error count falls. And it is wrong
twice over: `build()` produces a NEW list, so the clear could never have reached the builder even
if it worked; and that list is immutable, so it throws `UnsupportedOperationException` and takes
the server down at world load. **A burn-down rewards that rule exactly as much as a correct one**,
which is X14 with a sharper edge — the blind spot is not only "legal Java that is wrong at
runtime", it is *a rule you wrote to make the count fall*.
· **The rule of thumb it earns:** when a rename can only be made to compile by **APPENDING CALLS**
to reach a type that fits, stop. The API did not get renamed; it changed shape, and that is a
compat pair. A rename table row should replace a name, never grow a chain.

**V47. 🔴 CORRECTION to V20: a mod CANNOT register a 26.x GameTest function, and the failure looks
like 242 broken tests.** V20 has the adapter supply each test body through
`TestFunctionLoader.registerLoader`, from a static block. **That cannot work.** `runLoaders` is
called from `BuiltinTestFunctions.bootstrap` — a *registry bootstrap*, which runs before mod
construction — so the `TEST_FUNCTION` registry is already built by the time any mod lifecycle hook
could add to it. No amount of moving the call earlier fixes it.
· **The symptom is exact and misleading:** every INSTANCE registers, so the suite is discovered and
all of them are attempted, and every single one fails with
`Trying to access missing test function: <modid>:<id>`. It reads as a wholesale test-suite
breakage; it is one piece of missing wiring.
· **The fix is to skip the registry.** `GameTestInstance` is abstract over
`run(GameTestHelper)` — subclass it and hold the method reference.
· ⚠ **CORRECTION — this entry first said `codec()` was "a serialization concern for datapack tests"
and that a unit codec of `this` would do. That is WRONG and cost a Gate-C run.** `TEST_INSTANCE` is
a **synced** registry, so joining a world packs every entry, and packing requires each instance's
codec to be registered in `TEST_INSTANCE_TYPE`. An unregistered one fails the join with
`Unregistered holder in ResourceKey[minecraft:root / minecraft:test_instance_type]`; the client
never enters the world and the test times out reporting **"world did not load in time"** — which
points at worldgen, not at a test-registry codec. Register a real `MapCodec` for the subclass.
It cannot carry the method reference and should not pretend to: encode the `TestData` and the
name, and let a decoded instance be inert. That is honest rather than lossy, because the only
decoder is the CLIENT's copy of a synced registry and tests execute server-side.
· **The general form:** when a vanilla subsystem splits into "a thing in a registry" plus "a
reference to it", check WHEN that registry is built before designing around it. Bootstrap-time
registries are closed to mods, and the reference half will happily register and point at nothing.


**V48. 🔴 `Block.onRemove` → `affectNeighborsAfterRemoval` is a rename that also CHANGED WHEN IT RUNS —
the block entity is already gone.** · **Pattern:** the standard "clean up after myself" override, which
reads its own block entity to find what to tear down: `onRemove(state, level, pos, newState, moved)` →
`if (level.getBlockEntity(pos) instanceof MyBE be) { … }` · **Error:** `method does not override…`, fixed
by a rename rule to `affectNeighborsAfterRemoval(BlockState, ServerLevel, BlockPos, boolean)` — after
which it **compiles, loads and does nothing** · **Fix:** read the CALL SITE, not just the signature.
`LevelChunk.setBlockState` reordered around it:

| | 1.21.1 | 26.2 |
|---|---|---|
| 1 | `oldState.onRemove(level, pos, newState, moved)` — **block entity still alive** | `blockEntity.preRemoveSideEffects(pos, oldState)` |
| 2 | (vanilla's own `BaseEntityBlock.onRemove` then removes the BE) | `this.removeBlockEntity(pos)` |
| 3 | | `oldState.affectNeighborsAfterRemoval(serverLevel, pos, movedByPiston)` — **block entity gone** |

The name is honest about it — the hook is now for the NEIGHBOURS, not for you — and 26.x adds
**`BlockEntity.preRemoveSideEffects(BlockPos, BlockState)`** as the place to read your own state on the
way out (vanilla drops container contents there). So anything that consulted its own block entity moves
from the block to the block entity. · **In a §W tree it is a compat pair over the BLOCK ENTITY, and the
shape is pleasing:** give the per-target base class a `beforeRemoval()` no-op, have the 26.x half
override `preRemoveSideEffects` to call it, and leave the block's (now inert) 1.21.1 hook alone — its
`getBlockEntity(pos)` guard is already null and makes it a no-op on 26.x, so exactly one of the two fires
on each version with no version test in shared code. Comment the guard, or the next reader deletes it as
redundant and breaks 26.x. · **Why no gate above Gate B can catch it:** the override is called, with the
right arguments, and returns normally. The builder mod's cable car left its rope and its car in the sky
when a tower was mined — and because the orphan car then hung around, **two OTHER tests in neighbouring
plots failed instead**, one reporting a car parked at the wrong station and one reporting a car it had
just refused to create. Three red tests, one cause, and none of the three messages was about removal.

**V49. 🔴 CORRECTION to V38: `isControlledByLocalInstance` → `isLocalInstanceAuthoritative` INVERTS on
the server for a player-ridden entity, and the old hook is now client-only.** · **Pattern:** any
pilotable vehicle — `travel()` gated on `getControllingPassenger() instanceof Player p &&
isControlledByLocalInstance()`; and any headless test that drives one, which works only because the mock
player claims `isLocalPlayer() → true` (vanilla's own GameTest mock does exactly this: `iconst_1`) ·
**Error:** none. The rename compiles and the method still exists · **Fix:** compare the bodies, because
the *predicate* changed shape entirely:

```java
// 1.21.1 — one question, answered by the RIDER
isControlledByLocalInstance() = getControllingPassenger() instanceof Player p ? p.isLocalPlayer()
                                                                             : isEffectiveAi();
// 26.2 — two questions, chosen by SIDE; and it is FINAL
isLocalInstanceAuthoritative() = level.isClientSide() ? isLocalClientAuthoritative()   // → isLocalPlayer()
                                                      : !isClientAuthoritative();      // → Player: always true
```

On a **server** 26.2 never consults `isLocalPlayer()` at all: it asks `Player.isClientAuthoritative()`,
which is unconditionally `true`, so a vehicle with anyone aboard reports "the client owns this" and
`LivingEntity.travelRidden` zeroes its motion instead of calling `travel()`. A mock that overrode
`isLocalPlayer()` goes **inert**, and the test says `moved 0.0` — a message with nothing in it about
players, riding or authority. · **The fix is on the mock, not the vehicle** (a real dedicated server
genuinely should not simulate a player-driven vehicle — that is why Gate C exists): override
`isClientAuthoritative() → false` as well, which is also the honest answer for a mock, since there is no
client to defer to. `isLocalInstanceAuthoritative` itself is **final**, so it cannot be overridden
directly; the siblings `isLocalClientAuthoritative()` and `isClientAuthoritative()` are the hooks. ·
**The generalisation, and it is V39's rule again:** a predicate that keeps its meaning is a rename; one
that keeps its NAME-shape while splitting by side is a behaviour change. `find-member.py` says the name
exists; only reading both bodies says whether it still answers the same question.

**V50. 🔴 26.x REPLACED the capability handler API with a TRANSACTION-based one — and the loader
shipped ONE-WAY adapters, which is the difference between a wrapper call and a 1,300-line rewrite.**
· **Pattern:** any mod that reads or provides an energy / item / fluid capability —
`level.getCapability(Capabilities.EnergyStorage.BLOCK, …)`, a class `implements IItemHandler` ·
**Error:** `cannot find symbol: variable EnergyStorage / FluidHandler / ItemHandler` — location
`class Capabilities` ·
**Fix, and read the second half before doing any work:** `Capabilities` now exposes only `Energy`,
`Fluid` and `Item`, and each is typed on a new `net.neoforged.neoforge.transfer` API —
`EnergyHandler`, `ResourceHandler<ItemResource>`, `ResourceHandler<FluidResource>` — where a
`TransactionContext` replaces the old `simulate` boolean and the caller decides afterwards whether
to commit. There is **no legacy compat capability**.

That looks like every consumer and provider must be rewritten. It is not, because the legacy
interfaces **survive as adapters**:

| direction | available | how |
|---|---|---|
| new → legacy (READING a capability) | ✅ | `IEnergyStorage.of(EnergyHandler)`, `IItemHandler.of(ResourceHandler<ItemResource>)`, `IFluidHandler.of(ResourceHandler<FluidResource>)` |
| legacy → new (PROVIDING one) | ❌ | nothing — verified by scanning every non-client class in the universal jar for a method taking `IItemHandler`/`IEnergyStorage`/`IFluidHandler` and returning a new-API handler |

So the consumer side is a wrapper call per lookup and the provider side is a real reimplementation.
Measured on a fluid/energy transfer library — a mod whose ENTIRE PURPOSE is this abstraction, 17 files and ~1,300 lines
touching those interfaces: the actual work was **~160 lines across five adapters**.
· **Rollback has a hook, and a cross-loader mod probably already fits it.**
`SnapshotJournal<T>` (`createSnapshot`/`revertToSnapshot`/`onRootCommit`, plus `updateSnapshots(tx)`
before mutating) is the participant contract. The library's own `createSnapshot()` /
`Snapshot.loadSnapshot()` mapped onto it one-to-one, because a library that also targets Fabric
already models transactions. Mutations are then REAL and reverted on abort — do **not** try to
emulate `simulate` by doing the work twice. Fire the mod's "I changed" callback from
`onRootCommit` only: a nested transaction that later aborts must not have marked a chunk dirty.
· **ITEM capabilities also gained a CONTEXT.** `Capabilities.Item/Energy/Fluid.ITEM` are
`ItemCapability<T, ItemAccess>` where they were `<T, Void>`, so `stack.getCapability(cap)` needs
`ItemAccess.forStack(stack)`. Contain that at the boundary — widening your own lookup interface to
carry an `ItemAccess` pushes a 26.x-only concept into code that has no use for it.
· **The generalisation, and it is V41's rule pointed at a whole subsystem:** when an API is
*replaced* rather than renamed, the question is not "how do I rewrite my call sites" but **"did the
loader ship an adapter, and in which direction"**. Ask the jar before estimating. Reasoning from
"the capability type changed" to "rewrite everything" is a plausible story fitted to one
observation — the same mistake V6 records.
· **AUGMENT — providing an ITEM energy capability needs no transaction code at all:** when the energy lives on the stack, NeoForge 26.2 ships `net.neoforged.neoforge.transfer.energy.ItemAccessEnergyHandler(ItemAccess, DataComponentType<Integer> energy, capacity, maxInsert, maxExtract)`. Store the charge in a `persistent(ExtraCodecs.NON_NEGATIVE_INT)` data component (it replaces the old `"Energy"` NBT key) and register `ev.registerItem(Capabilities.Energy.ITEM, (stack, access) -> new ItemAccessEnergyHandler(access, ENERGY.get(), cap, in, out), item)`. The "reimplement the provider side" cost in this entry is for block/entity storage; an item provider is one line. Reading it back is `stack.getCapability(Capabilities.Energy.ITEM, ItemAccess.forStack(stack)).getAmountAsLong()`. (a shield-adding item mod's powered shields: an `ICapabilityProvider` + anonymous `IEnergyStorage` became that one line.)

**V51. Small 26.2 removals confirmed against the jar (cluster).** `BlockEntityType.Builder` is
**gone** — the public constructors `(BlockEntitySupplier, Block...)` / `(…, Set<Block>)` replace it,
and there is no shared form with 1.21.1, whose constructor demands a DataFixer `Type` ·
`INBTSerializable` removed outright (nothing replaces it; attachments persist through their owner) ·
`AttachmentType.Builder.serialize(Codec)` → `serialize(MapCodec)` ·
`IClientFluidTypeExtensions` **lost** `getStillTexture`/`getFlowingTexture`/`getTintColor`: a fluid's
appearance is a baked `FluidModel`, reached via
`Minecraft.getModelManager().getFluidStateModelSet().get(state)` → `.stillMaterial().sprite()` and
`.fluidTintSource().colorAsStack(stack)` (use `colorAsStack`, not `color(FluidState)`, when drawing a
GUI tank — a tint source may answer differently for a stack) · `FluidStack.getDisplayName()` →
`getHoverName()` · `TextureSheetParticle` folded into `SingleQuadParticle`, which also renamed
`getLightColor` → `getLightCoords` and replaced `getRenderType` with an abstract `getLayer()`
returning a `SingleQuadParticle.Layer` (TERRAIN_SHEET's successor is `TRANSLUCENT_TERRAIN`; the
Layer names the atlas AND the render pipeline, so a particle now states its translucency) ·
`ParticleProvider.createParticle` gained a trailing `RandomSource` ·
`ItemStack.getBurnTime` gained a `FuelValues` (burn times are server DATA now, so with no server
there is no honest answer — 0 is the one callers already handle) ·
`InvWrapper` → `VanillaContainerWrapper.of(Container)`, which is instance-CACHED per Container, so
delegate to it rather than extending it or two wrappers over one container keep separate ideas of
what has been rolled back · `FluidBucketWrapper` → `new BucketResourceHandler(ItemAccess.forStack(s))`.
⚠ *`BlockEntityType.Builder`'s removal is **1.21.2**, not 26.x (minor-version-deltas U11).* 

**V52. 🔴 `ItemProperties` and `ItemBlockRenderTypes` are DECLARED NOWHERE — and for a LIBRARY the
right port is a loud no-op, not a deletion and not a silent one.** · **Pattern:** a helper a library
offers its dependants — `ClientHooks.registerItemProperty(item, id, fn)`,
`setRenderLayer(block, type)` · **Error:** `cannot find symbol: class ItemProperties /
ItemBlockRenderTypes` · **Fix:** 26.x finished moving item appearance into data (the V42/V42b
shift): property overrides are the client item definition at `assets/<ns>/items/<id>.json`, and a
block's render layer is the `render_type` field of its block model. There is no Java equivalent, so
there are three options and two of them are wrong. **Deleting** the methods breaks every dependent
mod's compile. **Silently doing nothing** is the §S4 failure — javac checks names, nobody checks
side effects, and the dependant ships with every item as the magenta missing cube while all three
gates stay green. Keep the signature, do nothing, and **say so once per call site**, naming the file
to ship instead: the author is told, nothing crashes, and no caller is left believing a registration
happened.
· **⚠ SHARPENING — that only works when the removal is inside the method BODY.** When the removed
types are in the **public SIGNATURE**, there is no signature left to keep and the loud no-op is
not available: a config library's 3D-screen base class offers `render3D(PoseStack, MultiBufferSource, …)` to
subclasses, and on 26.2 `MultiBufferSource` is off the classpath entirely, so a 26.2 dependent
could not write that override even against a shim. There the honest options collapse to a real
port or a recorded DROP. **The test is where the removed name appears**: in a body → V52's
no-op; in a parameter, return type or supertype → a drop, recorded. · Two things make such a
drop safe rather than merely necessary, and both are worth establishing BEFORE writing any of
it: no dependent references it, and it is not reachable in a shipped game (that library's whole
3D layer hangs off a menu installed only when `!FMLEnvironment.isProduction()`). · And a file
can still be mostly portable — `RenderUtil` lost exactly three methods and every other one
compiled unchanged, because they take a `PoseStack` and a `VertexConsumer` and both survive.
Overlay the file and no-op the three; do not drop what still works.
· **AUGMENT — a property with no vanilla equivalent still needs one Java class:** when a 1.20 `ItemProperties.register(item, id, fn)` computed something vanilla has no `minecraft:` property for (e.g. "this energy item is empty"), write a `record X() implements ConditionalItemModelProperty` (`get(stack, level, owner, seed, displayContext)` + `MapCodec.unit`), register it from `RegisterConditionalItemModelPropertyEvent.register(id, MAP_CODEC)`, and name it as `"property": "<ns>:<id>"` in a `minecraft:condition` item definition; nest `minecraft:using_item` inside it for the blocking pose. A per-item `ItemColor` for one layer becomes `"tints": [{"type": "minecraft:constant", "value": 16777215}, {"type": "minecraft:constant", "value": <rgb>}]` on each leaf model (the value is RGB; the codec makes it opaque). (a shield-adding item mod: the `blocking` and `disabled` overrides on 64 items, 5 tinted items.)

**V53. 🔴 THE WHOLE GUI HOOK FAMILY WAS RENAMED `render*` → `extract*` — and the right answer is a
compat BASE CLASS, not a rewritten screen.** §V6 records that `GuiGraphics` became
`GuiGraphicsExtractor` and that drawing is recorded rather than issued. What it does not say is that
**every hook was renamed to match**, so a screen that compiles on 1.21.1 silently overrides nothing
on 26.2. Measured off both jars:

| 1.21.1 | 26.2 | note |
|---|---|---|
| `Renderable.render(g,mx,my,pt)` | `extractRenderState(...)` | same order |
| `Screen.render` / `AbstractContainerScreen.render` | `extractRenderState` | same order |
| `Screen.renderBackground(g,mx,my,pt)` | `extractBackground(g,mx,my,pt)` | same order |
| `AbstractContainerScreen.renderBg(g,`**`pt,mx,my`**`)` | **gone** — draw in `extractBackground` | ⚠ **arguments reordered** |
| `renderLabels(g,mx,my)` / `renderTooltip(g,mx,my)` | `extractLabels` / `extractTooltip` | same order |
| `AbstractWidget.renderWidget` | `extractWidgetRenderState` | same order |
| `Screen.renderWithTooltip` | `extractRenderStateWithTooltipAndSubtitles` | |
| `renderMenuBackground` / `renderPanorama` / `renderBlurredBackground` | `extractMenuBackground` / `extractPanorama` / `extractBlurredBackground` | |
| `mouseClicked(double,double,int)` | `mouseClicked(MouseButtonEvent, boolean)` | ⚠ **event record** |
| `mouseReleased` / `mouseDragged` / `keyPressed` / `charTyped` | take `MouseButtonEvent` / `KeyEvent` / `CharacterEvent` | ⚠ |
| `mouseScrolled(double,double,double,double)` · `isMouseOver` | unchanged | |

· **`renderBg` is the trap.** It is not renamed, it is *deleted*: 26.2's own `AbstractFurnaceScreen`
draws its texture in `extractBackground` after `super.extractBackground(...)`, which is precisely
where 1.21.1's `AbstractContainerScreen.renderBackground` called `renderBg`. So the *place* survives
and the *argument order* does not — V34's shape again, and no rename-table row can express it.
· **The move that makes this cheap: give the mod ONE base class per screen kind and let it present
the 1.21.1 hooks.** A compat `AbstractContainerCursorScreen` that overrides `extractRenderState` /
`extractBackground` / `extractLabels` and calls `render` / `renderBg(g,pt,mx,my)` / `renderLabels`
leaves every subclass **shared and unedited**. Measured on a space-exploration mod (~550 files): 19 screens, and only 2 of them
name the base directly — so this is two files of adaptation instead of nineteen files of rewrite,
with no second copy of any screen to drift.
· **Do not blanket-rename `render(`.** In one client tree the four-argument `.render(...)` call sites
split 27 model/entity renders (`poseStack, buffer, light, overlay`) against 7 GUI ones; anchoring on
the first argument being `graphics` separates them exactly. And with the compat base in place the
screens want **no** rename at all — only widgets extending vanilla `AbstractWidget` do.

· ⚠ **AUGMENT — a SINGLE-target port (an upstream branch that only has to build on 26.x) wants the
rewrite, not the compat base, and it is mechanical: `tools/convert-gui-hooks.py`.** It renames the
`render*` overrides, turns `renderBg(g, pt, mx, my)` into `extractBackground(g, mx, my, pt)` after a
`super.extractBackground` call, and moves the input handlers onto `MouseButtonEvent`/`KeyEvent`/`CharacterEvent`
while keeping each body byte for byte (the old parameter names become locals read from the event; a
forwarded call passes the event on). Two guards came from its first real run: only `@Override` methods with
the exact 1.21 parameter types are touched, and a render CALL is renamed only at the hook's arity AND when it
passes the GUI method's own parameters through — the mod had a `render(g, font, x, y)` helper of the same
arity that a looser rule renamed. 63 sites, GUI errors 170 → 75.

**V54. Client removals that are NOT renames — check each with `find-member.py` before writing a
rule.** All four report *declared nowhere* on 26.2: **`MultiBufferSource`** (the world-render buffer
handed to every block-entity and entity renderer), **`RenderSystem.setShaderColor` /
`enableBlend` / `disableBlend`** (blending and tint are properties of the `RenderPipeline` now, and
colour rides as the `int` argument on `blit`/`renderToBuffer` — V6's packed-colour change seen from
the other end), **`Sheets.cutoutBlockSheet()`**, and **`GuiGraphics`** itself. Only
`Screen.hasShiftDown()` is a plain move (`Minecraft.getInstance().hasShiftDown()`, plus a default on
`InputWithModifiers`). The lesson is V27's, and it is worth re-stating because a burn-down rewards
guessing: a missing client name is more often a *removed subsystem* than a moved class, and the two
want opposite work.

**V55. A LIBRARY that deletes half its client layer is a bounded job — diff the jars class-for-class
and count what YOUR mod actually names.** Resourceful Lib 5.0.4 (the 26.2 build) drops **17** client
classes present in 3.0.12 — the cursor screens, the widget package, the whole `scissor` package,
`CloseablePoseStack`, `RenderUtils`, and `ScreenUtils#setTooltip` — and adds a small GUI-only
`closables` package in their place. That reads as "the library is gone"; the useful number is that
the space mod names **12** of the 17, which is a morning's work, not a rewrite.

```bash
for J in old.jar new.jar; do unzip -l $J | awk '{print $4}' | grep '^com/…/client' \
    | grep '\.class$' | grep -v '\$' | sort > /tmp/$J.txt; done
comm -23 /tmp/old.jar.txt /tmp/new.jar.txt      # deleted
grep -rhoE 'com\.…\.client\.[A-Za-z0-9_.]+' src/main/java | sort | uniq -c | sort -rn   # what you use
```

· **Re-supply them in the NEW target's overlay and point the imports there with rename rows.** One
row per class maps `com.<lib>.client.X` → `<yourmod>.client.compat.<lib>.X`; on the old target no
rule fires and the imports still resolve to the library's own classes, so `src/main/java` is
untouched. That is strictly better than an overlay of the *screens*, which would duplicate gameplay.
· **The cascade is why this pays first.** One absent superclass makes every inherited member an
error: `leftPos` 41, `topPos` 16, `font` 15, `blitSprite` 14 — 86 errors from one missing import,
none of which is a real defect. The space mod's count went **1235 → 1038** on this change alone, and the
remaining client errors were finally readable.


**V56. 🔴 `Level.getRecipeManager()` → `recipeAccess()`, and on the CLIENT it no longer hands back a
recipe manager at all — so the "rename" silently removes a capability.** · **Pattern:** any code that
enumerates or matches recipes off a `Level` — `level.getRecipeManager().getAllRecipesFor(TYPE)` behind a
machine's fluid-tank filter, `getRecipeFor(TYPE, input, level)` behind a `Slot#mayPlace` · **Error:**
`cannot find symbol: method getRecipeManager()` · **Fix, and read the RETURN TYPES before writing a rule,
because they differ by side:**

| | 1.21.1 | 26.2 |
|---|---|---|
| `Level` | `RecipeManager getRecipeManager()` | `RecipeAccess recipeAccess()` |
| `ClientLevel` | (same manager — recipes are synced) | `RecipeAccess` — **`propertySet` + `stonecutterRecipes`, nothing else** |
| `ServerLevel` | (same manager) | `RecipeManager recipeAccess()` — covariant, the real thing |

So the recipe book is no longer synced to the client, and a `member:getRecipeManager -> recipeAccess`
row compiles on the server paths and **deletes the query on the client ones**. `RecipeManager.createCheck`
and its `CachedCheck` survive unchanged, so a block entity that already used a quick-check needs no work —
it is the ad-hoc `getAllRecipesFor` / `getRecipeFor` calls that break.
· **In a §W tree it is a compat pair, and the interesting part is that the two questions want OPPOSITE
off-server defaults.** *"List the recipes"* must answer **empty** — a menu that shows nothing is a visible,
recorded degradation, where inventing entries would be a lie. *"Could this input ever match?"* must answer
**true**, because it backs `Slot#mayPlace`: a false negative there makes a perfectly good slot refuse items
in the GUI, which reads as a broken machine. Write the pair as those two questions rather than as a
manager-shaped accessor, or the caller has to make that decision at every site.
· **The generalisation, and it is V49's shape one layer up:** when an accessor's return type became an
*interface* that only one side implements fully, the compile tells you nothing on the side that still
works. Ask `javap` for the return type on `ClientLevel` and `ServerLevel` separately — a covariant
override is exactly how a capability disappears from one side without a single error.


**V60. 🔴 `entityCutout` / `entityCutoutNoCull` SWAPPED MEANING — the same name, the opposite
face culling, and the rename that reads right is the wrong one.** · **Pattern:** any model or
layer picking its render type — `RenderType.entityCutout(tex)` / `RenderType.entityCutoutNoCull(tex)`
· **Error:** `cannot find symbol: RenderType` only, because the CLASS moved
(`client.renderer.RenderType` → `client.renderer.rendertype.RenderTypes`, §V3) — the *methods*
still exist under both names, so the obvious table rows are `entityCutout → entityCutout` and
`entityCutoutNoCull → entityCutoutNoCull`, and one of those does not exist while the other is a
different render type · **Fix, read off the PIPELINES and not the names:**

| 1.21.1 | culls? | 26.2 equivalent | culls? |
|---|---|---|---|
| `entityCutout` | yes | **`entityCutoutCull`** | yes |
| `entityCutoutNoCull` | no | **`entityCutout`** | no — `RenderPipelines.ENTITY_CUTOUT` is `.withCull(false)` |

So the correct rows CROSS: `entityCutoutNoCull(` → `entityCutout(` and `entityCutout(` →
`entityCutoutCull(`. · **Why it is worth its own entry rather than a row in §V38:** every other
name in this family is a plain move, so the eye reads `entityCutout → entityCutout` as obviously
safe and stops there. It compiles, it is a legal render type, and it renders every affected model
with its back faces the wrong way round — visible only to a human looking at a client. It is
**V39's rule with the two candidates sharing a name instead of a prefix**: do not pick by which
name reads right; ask what the thing actually does. Here that is one `grep` for `withCull` in
`RenderPipelines`, which settles it in seconds and is the only thing that distinguishes the two
mappings. · Same discipline as V34 (a value change wearing a rename's clothes) and V23 (an
inclusive/exclusive bound change wearing one).

**V57. 🔴 A vanilla model's PART HIERARCHY moved, and a mesh is data validated at BAKE — so it
compiles, loads, and kills the client.** · **Pattern:** any humanoid mesh, which every mod that
reskins a piglin/villager/player copies —
`partdefinition.addOrReplaceChild("hat", CubeListBuilder.create(), PartPose.ZERO)` at the ROOT ·
**Runtime:** `IllegalArgumentException: Failed to create model for <modid>:<mob>` caused by
`NoSuchElementException: Can't find part hat`, thrown while the client bakes its entity models ·
**Fix:** 1.21.1's `HumanoidModel` reads `root.getChild("hat")`; 26.2 reads
`head.getChild("hat")` — the part moved DOWN the hierarchy, and nothing else about the class
changed. In a §W tree it is a compat pair over a *builder* rather than a call: the mesh asks for a
hat (`CompatHumanoidModel.hat(root, head)`) and each target's half decides where it goes.
· **Two things worth carrying.** A mesh is not type-checked against the model class that will read
it — the relationship is by STRING, resolved at bake — so this whole family is invisible to javac
and to a dedicated server, and lands squarely in Gate C. And **check each model's actual
superclass before assuming the family is uniform**: of five affected files here, one put its hat
under `head` already and was right to, because it extends `VillagerModel`, whose hierarchy did not
change. A blanket rewrite would have broken the one file that was correct.

**V58. 🔴 A FLUID'S APPEARANCE LEFT `initializeClient` ENTIRELY — and the old method still
compiles, so nothing tells you.** V51 records that `IClientFluidTypeExtensions` lost
`getStillTexture` / `getFlowingTexture` / `getTintColor`. What it does not say is what that does to
the mod that used them. · **Pattern:** the standard NeoForge fluid client hook —
`FluidType.initializeClient(Consumer<IClientFluidTypeExtensions>)` accepting an anonymous class that
returns the still, flowing and overlay textures and the tint · **Error:** none. On 26.2
`FluidType` has **no `initializeClient` at all** and the interface keeps only the fog and
screen-overlay hooks, so the method overrides nothing and its anonymous class is dead code — and an
extra method on an anonymous class is perfectly legal Java (§S4: javac checks names and types, never
whether anything calls you) · **Runtime:** every fluid renders as the missing texture, and the only
trace is one `Missing FluidModel for fluid '<ns>:<id>'` line per fluid at resource load.
· **Fix:** a fluid is a baked `FluidModel` now, registered on NeoForge's `RegisterFluidModelsEvent`:
`event.register(new FluidModel.Unbaked(still, flowing, overlay, FluidTintSources.constant(tint)),
fluid)`. ⚠ `Material` takes the id ALONE on 26.2 (the atlas is implied) where 1.21.1 took an atlas
and an id.
· **In a LIBRARY, register by SWEEP rather than by bookkeeping.** A fluid/energy transfer library's fix walks
`BuiltInRegistries.FLUID` and gives a model to anything whose type is its own, so no dependent mod
has to remember to call anything — which is the assumption that produced the bug in the first place.
· **Measured, and the shape of the number is the point:** ten fluids on a space-exploration mod (~550 files) (oxygen, hydrogen,
oil, fuel, cryo fuel and their flowing forms — the resource the whole mod is about), invisible to
Gate A, Gate B and a `launch`-only Gate C, and invisible to the library's OWN gates because a pure
library's Gate C is "not applicable". Only a dependent mod's client, in a world, looking, could see
it. That is the argument for X29's four-phase Gate C in one finding.

**V58b. ⚠ AUGMENT to V58 — the fix is to give the LIBRARY its own Gate C, and it is cheap.**
V58's finding is that the space mod's ten fluids rendered as the missing texture because the only gate
that could see it belonged to a dependent mod, and a pure library's Gate C reads as "not
applicable". That conclusion is wrong wherever the port's whole delta is client-side, which for a
GeckoLib library is *by construction*: a renderer, its model, its layer stack, a particle, an
armour renderer, and not one line of it reachable from a dedicated server.
· **Two phases are enough and the third and fourth should be REFUSED, out loud.** `launch` proves
the client-LOAD surface (mixin apply, resource reload, model bake, the registrations that fail
silently); `spawn` puts a real entity in front of the player and lets it draw for a few seconds,
which is the only thing that drives `extractRenderState` / `adjustRenderPose` / `getRenderType`
and the layers. A reduced library that registers no items and no blocks has nothing for `battle`
or `gauntlet` to look at, and **saying so beats shipping two green phases that never had a
subject** — a phase that asserts nothing is X27's `checked == 0` wearing a gate's clothes.
· **Ask the dispatcher about the REAL entity, not a constructed stand-in.** "A renderer is
registered for this type" and "this instance is being drawn by it" are different claims, and only
the second has been exercised. It is also the only version that WORKS: at the title screen there
is no level, and `EntityType.create(null)` NPEs — so the check belongs in the spawn phase anyway.
· **Measured on a mob-framework library, and the finding is what justifies the phase**: the run named three
GeckoLib assets the model asks for and the jar does not ship. The X15 control question then
settled the attribution in one command — the file is SHARED source and `git log --all` shows the
original decompile brought in that summoning effect's geo and animation and never its texture, so it is
a pre-existing 1.21.1 gap on the model's DEFAULT branch, not a 26.2 regression. Record it as a
named known-absent (the §S5 `CENSUS_KNOWN_ABSENT` shape, with a line that fires if it ever
appears) rather than deleting it from the list, which is how a gap becomes invisible.
· ⚠ **`git log -S` needs a COMPLETE clone to answer that** (§S5b): this session's was shallow, and
against a truncated history the search returns nothing for a file that really was there — the
verdict inverts in the reassuring direction. `[ -f .git/shallow ] && git fetch --unshallow` first;
it cost seconds.

**V59. Blockbench exports have no `particle` texture, and 26.2 stopped forgiving it.** ·
**Pattern:** a model with `"textures": {"0": …}` and faces on `#0`, exactly as Blockbench writes it ·
**Runtime:** `Missing texture references in model <ns>:block/<id>: particle` at resource load, and
the block renders as the magenta cube — the *whole* model, not just its particles · **Fix:** add
`"particle": "#0"` to the PARENT model, so every child that supplies `#0` inherits it; it is inert on
1.21.1, which resolved a particle without being told. Sweep for it rather than fixing what the log
named: a model only warns once something actually bakes it, so the placed-blocks phase found 10 of
the 14 that were wrong. (the space mod: 12 block + 2 item models, 4 parents.)

**V61. Three mixin targets that MOVED on 26.2, and the one where the obvious substitute is
wrong.** A mixin's `@Mixin` target, its `@Invoker` parameters and its `@Accessor` field types are
all invisible to javac (§R14/§R17), so these surface only from an audit or from a dead client.

· **`PointedDripstoneBlock.isStalagmite` / `spawnFallingStalactite` moved UP into a new abstract
superclass** — 26.2 factors the block onto `SpeleothemBlock`, and both private statics went with
it. **An `@Invoker` does not inherit**: Mixin resolves the member on the class named in `@Mixin`,
so the TARGET CLASS has to change. That makes the mixin an overlay rather than a rename row — a
rewrite cannot retarget an annotation's argument and its import and still compile on the other
version. Keep the mixin's own class name, method names and parameters identical and every shared
call site stays untouched; the whole version difference lives in one file.

· **`ShulkerBullet`**: `finalTarget` became an `EntityReference<Entity>` (so an `@Accessor`
setter's parameter had to change with the FIELD) and `selectNextMoveDirection` gained the target
entity. Both in one interface, both behind a §W5 pair, so the shared caller names neither shape.

· 🔴 **`PigRenderer.getTextureLocation(Pig)` → `(PigRenderState)`, and `state.nameTag` is NOT a
substitute for `hasCustomName()`.** The render-state split takes the entity away from the method
that picks a texture, and the one-line port is to read the name off the state. Vanilla only fills
`nameTag` when the tag is actually being DRAWN (`extractNameTags` gates on distance and on
`shouldShowName`, which for an ordinary named mob means the crosshair is on it) — so a texture
chosen that way appears and disappears as you look around. Compute the answer in
`extractRenderState`, the one hook that still has both, and carry it on the state via NeoForge's
**render-state data** (`BaseRenderState.get/setRenderData(ContextKey<T>)`), which exists for
exactly this and needs no `@Unique` field plumbed through an accessor interface.
· 🔴 **CORRECTION (2026-09-23, measured): the park half of that advice is WRONG.** Render data
set inside `extractRenderState` is **wiped before anything reads it**. 26.2's final
`EntityRenderer.createRenderState(entity, pt)` runs extract → finalize → NeoForge's
`onUpdateEntityRenderState`, and the first instruction of that is `resetRenderData()`, a
`Map.clear()`. *Compute* the answer where the entity is in scope, but *park* it from a
`RegisterRenderStateModifiersEvent` modifier, which runs after the reset. See **§V65b**, which
has the A/B and the list of shipped ports this reaches.
· **The general shape, and it is V49's:** the render-state split does not merely move fields, it
changes *when* each field is true. A state carries what the RENDERER needed, not what the entity
is — so before substituting a state field for an entity call, read who populates it and under
what condition.


**V62. 🔴 `tryExtractRenderState` is the convenience method that CANNOT be used, and it answers
`null` rather than saying why.** · **Pattern:** 26.2 splits a draw into extract-on-tick and
submit-at-draw, so a renderer that builds a subject inline — a large boss mod's (~750 files, mixin-heavy) debris cloud, which
deserialises whole block entities from NBT moments before drawing them — has nothing in the
dispatcher's extraction pass and reaches for the obvious helper · **Symptom:** no error, no log
line, nothing drawn. It gates on the renderer's `shouldRenderOffScreen` matching the caller's idea
of global rendering **and** on the camera position the dispatcher was last `prepare`d with, and a
nested draw satisfies neither · **Fix:** run the three steps yourself —
`createRenderState` / `extractRenderState` / `submit` back to back — behind a §W5 pair whose
1.21.1 half is the old one-line `render` call. · **The generalisation, and it is V27/V39 pointed at
HELPERS rather than renames:** a convenience method encodes the caller vanilla had in mind. Read its
body before adopting it; here the failure mode is a silent no-draw, which no gate below a human
looking at a client can see.

**V63. 🔴 `BlockEntityRenderState.extractBase` recomputes the LIGHT for you, from
`tile.getLevel()` — and silently discards a caller that was passing a different one.** ·
**Pattern:** a 1.21.1 caller supplying its own packed light because the subject is not where the
tile thinks it is (the debris cloud reads it from its own fake `BlockAndTintGetter`) ·
**Symptom:** compiles, runs, wrong picture — the tile is lit as if it were still in the ground it
came from · **Fix:** overwrite the state's light after `extractBase`, or do not use `extractBase`.
· **The general shape:** when a new API does for you something the old one made you PASS IN, check
whether your caller was passing something unusual. A parameter that became a computation is a
default that has silently replaced your value.

**V64. 🔴 The successor spelling existing on BOTH versions is a TRAP, not a convenience.** ·
**Pattern:** `RenderShape.ENTITYBLOCK_ANIMATED` is gone on 26.2 (only `INVISIBLE` and `MODEL`
remain), and the natural replacement `state.hasBlockEntity()` **compiles on 1.21.1 too** ·
**Symptom:** the tempting shared fix is one line and no pair — and it silently WIDENS the canonical
target, because `hasBlockEntity()` is true of things `ENTITYBLOCK_ANIMATED` was not (a beacon would
start drawing its beam inside a debris cloud) · **Fix:** a §W5 pair asking the question the caller
means — *is this drawn by a block-entity renderer?* — answering `ENTITYBLOCK_ANIMATED` on 1.21.1 and
`hasBlockEntity()` on 26.2, with the widening recorded. · **This is §W2/§X8 with a novel trigger:**
usually the canonical target catches you because the new code does not compile there. Here the
AVAILABILITY of the new name on the old version is exactly what makes the wrong answer attractive,
and nothing complains.

⚠ **AUGMENT to V64 — the OVERRIDE side of the same removal, where the tempting substitute is
`INVISIBLE` and it is wrong for a different reason.** V64 covers a CALLER comparing against
`ENTITYBLOCK_ANIMATED`. A block that RETURNS it from `getRenderShape` is the commoner case, and the
obvious port is `INVISIBLE`: both skip the chunk model (`SectionCompiler` renders only on `MODEL`,
verified on both versions), so they read as interchangeable. **They are not.** Eight vanilla sites
ask `getRenderShape() != RenderShape.INVISIBLE` to mean *"this block has visible geometry"*, and
they gate break particles, terrain particles, footstep and running effects, the suffocation
overlay, and minecart/falling-block rendering. An `ENTITYBLOCK_ANIMATED` block passes every one; an
`INVISIBLE` one fails them all, silently and only in play.
· **The answer is `MODEL`**, which is what vanilla's own former-`ENTITYBLOCK_ANIMATED` blocks do
now — chest returns it explicitly, and bed, conduit, shulker box and ender chest stopped overriding
`getRenderShape` at all. **The price is the half that bites:** with `MODEL` the block's model really
is drawn, so a BER-only block must ship a model with **no elements** — a `particle` texture and
nothing else. A mod whose block model is a leftover `cube_all` has been getting away with it
precisely because `ENTITYBLOCK_ANIMATED` never rendered it, and on 26.2 that cube appears behind
the BER. Measured on a large boss mod: one of three blocks already had the particle-only model and two
were stray `cube_all`s.
· **In a §W tree it is a pair plus a shared resource edit, and the split is worth noting:** the pair
keeps 1.21.1 byte-identical, while stripping the two models is provably inert there (those quads
were never rendered under `ENTITYBLOCK_ANIMATED`) and load-bearing on 26.2. A change that is a
no-op on the canonical target and required on the new one can go in shared source with that
reasoning written down — it does not need an overlay.

**V65. A layer bolted onto SOMEONE ELSE'S renderer reaches its entity through
`RegisterRenderStateModifiersEvent`.** V61 covers a renderer you own — compute it in
`extractRenderState`, park it with `setRenderData`. A layer added to every vanilla living renderer
has no `extractRenderState` to hook, so it needs NeoForge's modifier event. Two facts make that safe,
both read out of `RenderStateExtensions`' bytecode rather than assumed: the class match is
`isAssignableFrom`, so **one** registration against `LivingEntityRenderer.class` really does cover
every living renderer; and `resetRenderData()` runs at the START of `onUpdateEntityRenderState`,
before the modifiers, so parked data cannot leak into a frame where the modifier did not run.
⚠ The sweep must skip renderers whose state is not what you expect — 26.2's mannequin among them;
1.21.1 has no mannequin, so nothing is lost by excluding it.

**V65b. 🔴 `setRenderData` inside `extractRenderState` is WIPED before drawing, including from
a renderer you OWN. The modifier event is the ONLY hook that parks data that survives.** ·
**Pattern:** the natural 26.2 port of "the renderer needs something off the entity":
`@Override extractRenderState(e, state, pt) { super…; state.setRenderData(KEY, e.foo()); }`, or a
mixin `@Inject(at = TAIL)` doing the same into a vanilla renderer's extract · **Runtime:**
nothing. `getRenderData(KEY)` answers `null` in `submit`, in `getTextureLocation` and in every
layer, and a reader written as `Boolean.TRUE.equals(...)` or with a default fallback silently
takes the fallback every frame: the default texture, no rotation, no animation. ·
**Fix:** read out of the patched sources and NeoForge's bytecode rather than inferred:

```
EntityRenderer.createRenderState(T entity, float pt)   // final
  extractRenderState(entity, state, pt);
  finalizeRenderState(entity, state);
  RenderStateExtensions.onUpdateEntityRenderState(this, entity, state);
      state.resetRenderData();          // BaseRenderState: extensions.clear()
      for (modifier : ENTITY_CACHE.get(rendererClass)) modifier.accept(entity, state);
```

`EntityRenderDispatcher` calls exactly this for every entity it draws. The only code that runs
after the clear is the modifier list, so a value must be parked there:
`event.registerEntityModifier(MyRenderer.class, (e, s) -> s.setRenderData(KEY, …))`. The value
can still be *computed* in `extractRenderState`, but only a modifier can *store* it. §V65's
"parked data cannot leak into a frame where the modifier did not run" is the same reset seen
from its good side.
· **MEASURED as an A/B on a small mob-variant mod's (~30 files) 26.2 Gate C `spawn`:** parked from the modifier event it
read `layer draws 142`. Moved to an `@Inject(at = TAIL)` into
`LivingEntityRenderer.extractRenderState` it read `FAIL: … never drew a charged mob`, with 0
draws, no other change, and no mixin error. The config sets `defaultRequire: 1`, so an inject
that bound nothing would have crashed the load instead of passing it.
· **Found live in three shipped ports on 2026-09-23, and FIXED the same day.** They had been
written from §V61's advice, compiled, passed every gate, and fell back silently:
a ~750-file boss mod's `MixinPigRenderer` (the named-pig texture, i.e. §V61's own worked example);
a mob-framework library's `ProjectileRenderer` (yaw/pitch), a summoning-effect renderer (lifetime) and
its model base's park helper (keyframe animation states); a ~390-file mob mod's `CustomZombieRenderer`
(texture, big husk), `CustomSkeletonRenderer` (mossy texture), the orb, mine, trap, trident-storm
and tornado renderers (lifetime, yaw), and, through the two park helpers, **10 more renderers**
that call the model-state or piglin-texture park helper from their extract. That is the
**keyframe animation states** of that mod's bosses and casters, the illagers, and both piglin textures. **Count call sites through the
helpers, not `setRenderData` lines**: the line count says 16 and hides the ten renderers that
reach it indirectly. A large boss mod's render-state holder, a ~660-file GeckoLib mob mod's `PlayerRenderStates`, the ~390-file mob mod's
web-shoot render state and the ~750-file boss mod's living-layer class already used the modifier event.
· **The fix that scales: a `RenderDataCarry` helper, not a modifier per renderer.** A modifier gets
`(entity, state)` and no renderer, and moving 21 computations out of the renderers that own them
would have been 21 rewrites. Instead each site keeps its code and swaps
`state.setRenderData(k, v)` → `RenderDataCarry.set(state, k, v)`. That writes the value (so a caller
that runs `extractRenderState` by hand, with no reset, still sees it) AND records it in a
`WeakHashMap<BaseRenderState, …>`. ONE modifier registered against `EntityRenderer.class` puts the
record back after the reset (the class match is `isAssignableFrom`, so it covers every renderer,
GeckoLib's included). Weak keys, so a state that never meets the reset cannot leak. The codemod is
one regex over the affected files, and `grep -rn 'setRenderData(' src/mc26` afterwards should show
only modifier lambdas.
· **The Gate-C check that proves it is generic, and it was A/B'd on two ports.** A
`RenderDataProbe` compat pair (26.2 real, 1.21.1 answers -1 = "no render state") builds each spawned
entity's state twice with its own renderer: once through the real, final
`createRenderState(entity, pt)`, and once by hand (a state from the real path, `resetRenderData()`,
then `extractRenderState` alone). Every key the by-hand state holds must survive on the real one.
The keys are read out of `BaseRenderState.extensions` by reflection, so the check names no key and
covers renderers written later. Measured: a mob-framework library 3 values, a ~390-file mob mod **55 values across
34 types**, all surviving; with the restore disabled, both fail naming every lost key. ⚠ **Do not
build the by-hand state with the no-argument `createRenderState()`**: GeckoLib's is `final` and
returns **null** by design (it picks the state class from the entity), so the probe NPEs on every
GeckoLib renderer. And GeckoLib's own `createRenderState(T, float)` still runs NeoForge's reset,
through `GeckoLibClient.handleEntityRenderStateExtraction`, so the bug does apply there.
· **Expect a second wave (§V42c/§144): fixing it UNMASKED a crash.** Keyframe states reaching the
model for the first time meant 26.2 baked animations it had never baked, and one named a bone its
model lacks: `IllegalArgumentException: Cannot animate mob_cape, which does not exist in model`,
the first frame one illager-style mob was drawn. See §V75's augment.
· **Why no gate caught it**, and it is §X14's blind spot in the render layer: a Gate-C
`spawn` asserts that the entity reached the client and has a renderer, never that the
renderer's *choice* matched the entity. A default texture is a perfectly good picture. The
mob-variant mod's harness caught its own case only because it counts real draws of a layer that is
gated on the parked flag. **That is the assertion shape to copy**: make the test observe a side
effect that happens only when the parked value is true.
· **Sweep:** `grep -rn -B8 'setRenderData' src/mc26 | grep extractRenderState`. Every hit is
a finding unless its file also registers a modifier.

**V66. The collector seam must be a STACK, not a slot — and it has to carry the camera.**
A §W5 pair that re-supplies the removed `MultiBufferSource` (§V54/§V55) binds 26.2's
`SubmitNodeCollector` for the duration of a draw. Renderers NEST: an entity renderer can draw whole
block entities inside its own `submit`, one of which may be the mod's own, so a set/clear pair
unbinds the OUTER collector when the inner one pops. Push and pop. It must also carry the
`CameraRenderState`, because a nested `submit` needs one and 26.2 hands a camera only to `submit`
itself — so a slot holding just the collector cannot serve the nested call at all.


**V67. 🔴 `ItemStack.CODEC` refuses an item whose components are not bound yet — and RECIPES are
parsed before they are.** · **Pattern:** a custom `RecipeSerializer` whose `MapCodec` reads its
result with `ItemStack.CODEC.fieldOf("result")`, which is what every 1.21.1 recipe does ·
**Runtime:** `Couldn't parse data file '<ns>:<recipe>': DataResult.Error['Item <ns>:<item> does not
have components yet']` — an ERROR line and then silence, so the recipe is simply gone, no gate
fails, and the message names the ITEM rather than the codec · **Fix:** 26.2 reads its `id` through
`Item.CODEC_WITH_BOUND_COMPONENTS`, and added **`ItemStackTemplate`** for exactly this case —
`MAP_CODEC` reads the IDENTICAL JSON (`{"id":…, "count":…, "components":…}`) through the plain
`Item.CODEC` and defers building the stack to `create()`, when components exist. Every vanilla 26.2
recipe uses it.
· 🔴 **The DEFERRAL is the fix, so the template must be HELD — and the obvious compat pair is not
one.** The tempting §W5 shape is a pair returning the version's own `Codec<ItemStack>`
(`ItemStack.CODEC` on 1.21.1, `ItemStackTemplate.CODEC.xmap(ItemStackTemplate::create, …)` on 26.2),
which keeps every serializer's field an `ItemStack` and changes one line. **It fails identically**,
because `xmap`'s mapper runs during decode: `NullPointerException: Components not bound yet` out of
`ItemStack.<init>` ← `ItemStackTemplate.create` ← `RecipeManager.prepare`. A different message
naming a different class for the same cause, which is exactly how a wrong fix survives review. Read
vanilla's own recipe before designing the pair: `ShapelessRecipe` declares an **`ItemStackTemplate`
field** and calls `create()` from `assemble`. So the pair is over the RESULT TYPE, not the codec —
a small `Result` holding a stack on 1.21.1 and a template on 26.2, materialised (and cached) on
first use. The serializer's field type changes; every reader keeps its `ItemStack`-returning
accessor. ⚠ **Do not pair the STREAM codecs too** — `ItemStack.STREAM_CODEC` carries no such
validation and is right on both, because network sync happens long after components bind; only the
`map` to and from `Result` differs.
· **The reason it is worth its own entry:** **no edit to any JSON can avoid it.** The failure is on
the `id` field's own codec, so it happens whatever the file says — which is what makes it look like
a data bug and sends you into the datapack for hours. Ask which codec the SERIALIZER names before
touching a single recipe file. · **And the general shape, which is V41's:** when an API defers work
that used to happen eagerly, the deferral is the API — a wrapper that undoes it to keep your old
type is not a port, it is the same bug with a longer stack trace.

**V69. 🔴 `ClientTickEvent` is GATED on `gameLoadFinished` on 26.2 — so a Gate-C harness driven
by it goes SILENT in exactly the failure it exists to name.** · **Pattern:** the standard client
harness — `@EventBusSubscriber(Dist.CLIENT)` + `@SubscribeEvent onClientTick(ClientTickEvent.Post)`,
with the §X16/§X28 heartbeat that prints which screen is blocking · **Symptom:** the client boots,
renders, `jstack` puts the render thread in `FramerateLimiter.limitDisplayFPS` (alive and looping,
which is what a *healthy* idle client also looks like), the log stops mid-resource-reload, and the
harness prints **nothing at all** — not PASS, not FAIL, not the heartbeat. From the outside it is
indistinguishable from a harness that was never registered · **Cause, read out of 26.2's
`Minecraft.java`:** `tick()` opens with `if (this.gameLoadFinished)
ClientHooks.fireClientTickPre();` and closes with the matching `fireClientTickPost()`;
`gameLoadFinished` is set only by `onResourceLoadFinished`, i.e. when the INITIAL reload completes.
1.21.1 has the same field and no such guard. So a client stuck in its first reload fires no client
tick, ever.
· **Fix the INSTRUMENT, not the assertion.** `RenderFrameEvent.Post` is fired from
`Minecraft.renderFrame` with no gate, exists under the same name on both targets, and so is shared
source with no rename row and no compat pair. A heartbeat on it — printing the frame count,
`Minecraft.isGameLoadFinished()` (present on both) and the current screen, and only while no tick
has yet arrived — turns "the client hung" into a sentence. Measured: it took a stall that three
rounds of `jstack` and source reading could not name into one line,
`frame 200 and not one client tick yet … gameLoadFinished=false screen=…TitleScreen`, which also
**corrected** the first diagnosis — the title screen is up as the SCREEN while the LoadingOverlay
is still the overlay, so "it reached the title" and "the reload finished" are different claims.
· **The X15 control is what separates the two readings**, and it is why it must be run before any
theorising: the same commit PASSed Gate C `launch` on 1.21.1 in 47 s while 26.2 sat silent to its
600 s watchdog. Harness broken → both silent; client stuck → exactly this split.
· **The generalisation, and it is §X11's:** an instrument whose value is that it normally prints
something must be checked for the case where it CANNOT print. Ask which event your harness rides on
and under what condition the target version fires it — a gate added to a hook you depend on is
invisible in a diff, in a compile, and in every green run.

**V70. 🔴 A FAILED INITIAL RESOURCE RELOAD presents as a HANG, not a crash — and 26.2 fails
reloads that 1.21.1 merely complained about.** · **Symptom:** the client boots, renders, sits at the
title screen forever; no crash report, no error screen, and the log stops at whatever reload step
happened to be last · **What is really happening:** the initial reload completes EXCEPTIONALLY, so
`LoadingOverlay`'s callback takes `Util.ifElse`'s throwable branch — `rollbackResourcePacks` — which
does **not** call `onResourceLoadFinished`. `gameLoadFinished` therefore stays false forever, which
on 26.2 also means no `ClientTickEvent` ever fires (V69), so every tick-driven harness is silent
too. The one log line that names it is an **INFO**: `Caught error loading resourcepacks, removing
all selected resourcepacks`, with the real cause as its `Caused by:`. Grep for it before anything
else; `jstack` cannot see this at all, because the client is genuinely idle and healthy.
· **Three causes measured on one mod, each fatal to the whole reload on 26.2 and none of them fatal
on 1.21.1:**
  1. **`neoforge:separate_transforms` was REMOVED.** The loaders 26.2 registers are
     `neoforge:conditional`, `neoforge:empty`, `neoforge:obj`, `neoforge:composite` — nothing else.
     A model naming any other loader throws `JsonParseException: Unknown loader`. (This is the §R
     "renamespace `forge:` → `neoforge:`" note one era on: the id is no longer wrong, the loader is
     gone.) In a §W tree it is a per-target RESOURCE overlay of that one model with the `base` half
     inlined, and the lost perspectives are a recorded gap.
  2. **A model may not span two atlases.** `CuboidItemModelWrapper.validateAtlasUsage` throws
     `Multiple atlases used in model, expected …/items.png, but also got …/blocks.png`. The offender
     is the ordinary habit of a BLOCK model reusing an ITEM sprite. Fix it by DERIVING the sprite
     onto the other atlas in `processResources` rather than committing a second PNG — one source of
     truth for the art, and correct on both targets.
  3. **V70's own headline, below.**
· **The trap that took longest, and it is V39's shape:** 1.21.1's `InventoryMenu.BLOCK_ATLAS`
answers *two* questions with one value — "which TEXTURE is the block atlas"
(`minecraft:textures/atlas/blocks.png`, what a `RenderType` binds) and "which ATLAS is the block
atlas". 26.2 splits them: the texture is still `TextureAtlas.LOCATION_BLOCKS`, but `AtlasManager`
keys on a bare atlas id (`minecraft:blocks`, `AtlasIds.BLOCKS`) and **throws
`IllegalArgumentException: Invalid atlas id`** on the texture path. A §W5 compat pair that offers
one `blockAtlas()` is therefore right at every call site that binds a texture and wrong at the one
that looks the atlas up — measured here, four right and one wrong. **Split the pair into the two
questions**, and note that on the canonical target both answer the same constant, which is exactly
why the collapse is invisible there (§W2/§X8).
· **Why that one is worse than it sounds:** the failing call sits in a block-entity renderer's
CONSTRUCTOR, and `BlockEntityRenderers.createEntityRenderers` runs *inside* the reload — so a single
bad id in one renderer fails the entire resource reload and stalls the whole client.
· **Expect a QUEUE, and read it as progress (§V42c):** each of the three masked the next, because a
reload aborts at its first failure. The log went "unknown loader" → "multiple atlases" → "invalid
atlas id" over three runs, and only the third looked like the real bug.

**V68. `EntityPredicate` became a `Codec.dispatchedMap`, and most of the old field names still
parse — which is what makes it dangerous.** · **Pattern:** an advancement or loot condition
carrying a 1.21.1 `EntityPredicate` (`type`, `type_specific`, plus `nbt`, `flags`, `equipment`, …) ·
**Runtime:** a parse failure naming only `type` · **Fix:** the predicate is dispatched on a
registered sub-predicate id now: `type` becomes **`entity_type`**, and `type_specific`'s contents
**fold up into the map under their own key**. Everything else keeps working because those field
names happen already to be registered sub-predicate ids. · **The trap is the survivors:** only two
keys break, so the rest of the file reads as proof it is fine, and a fix that only chases the error
message leaves a predicate that parses and matches nothing. Diff the whole object against the
target's own vanilla files, not just the field the parser complained about.

**V68b. ⚠ AUGMENT to V68 — an EntityPredicate reaches a data file through TWO carriers, and a
transform that knows one of them reports a clean run over the other.** V68 records the `type` →
`entity_type` change. The half that costs time is finding every FILE it applies to: the predicate
arrives either as a **loot condition**
(`{"condition": "minecraft:entity_properties", "predicate": {…}}`) or as an **advancement
criterion** (`criteria.<name>.conditions.<field>`), and the two look nothing alike.
· **Measured:** the shared `entity_predicates_as_dispatched_map` transform knew only the first, so
on four boss-defeat advancements it **matched 20 files and changed 0**. The tool's scope was
narrower than both its name and its include glob suggested — §X27's invariant arriving in the
resource layer rather than in a mixin audit.
· **What caught it was the generator refusing a rule that changes nothing** (§X1's dead-rule
detector, applied to transforms). A generator that had simply written 20 identical files would
have reported success and shipped four advancements that cannot load.
· **Widen it with an ALLOW-LIST plus a refusal, never a blanket rewrite.** The criterion fields
vanilla types as an EntityPredicate are enumerable (`entity`, `player`, `victim`, `attacker`,
`child`, `parent`, `projectile`, …); anything else carrying a namespaced `type` under a criterion's
`conditions` is the signature of a carrier the list does not know, and must stop the build by name
(§S1b — a data file has no compiler behind it, so a codemod that quietly produces a wrong result is
strictly worse than one that refuses).
· ⚠ **And the criterion branch must NOT return early.** The first cut did, which stopped the walk
reaching loot-condition predicates nested deeper in the same criterion and left four of a ~750-file boss mod's
overlays underived — caught immediately by that generator's ORPHAN check (an overlay no transform
derives). Two independent guards on one tool, each catching the other's blind spot, is what made
the widening safe to sync to every port that uses it.

**V71. Small 26.2 moves confirmed against the jar — the mob-framework library cluster.**
`Mob.isWithinRestriction(BlockPos)` → **`isWithinHome`** (both bodies verified identical, so a real
rename, not V34's shape) · `Entity.getCommandSenderWorld()` removed (`level()`) ·
`AbstractHorse.getOwnerUUID()` → `getOwnerReference()` then `EntityReference.getUUID()`, and the
class moved to `world.entity.animal.equine` · `NeutralMob.setRemainingPersistentAngerTime(0)` →
**`setTimeToRemainAngry(0L)`** · `ItemCooldowns.addCooldown` is keyed by the `ItemStack` (or a
cooldown-group id), so an `Item`-keyed call needs `new ItemStack(item)` ·
`Entity.hurt` is gone and **`hurtServer(ServerLevel, DamageSource, float)` is ABSTRACT**, so an
entity that simply inherited 1.21.1's concrete `Entity.hurt` needs a §W5b BASE-CLASS pair
reproducing that body (`isInvulnerableToBase(source)` guard — NOT `isInvulnerableTo`, which does
not exist — `markHurt()`, always `false`) · `ParticleTypes.ENTITY_EFFECT`'s colour carrier is now
`SpellParticleOption.create(instant, r, g, b, power)`, which is §J#69 one era on and carries a
POWER as well as a colour · `TextureSheetParticle` folded into `SingleQuadParticle`, whose sprite
is set in the CONSTRUCTOR (`getRenderType` → an abstract `getLayer()`), and
`ParticleProvider.createParticle` gained a trailing `RandomSource`.

**V72. 🔴 A GeckoLib port can be a SIMPLIFICATION — check what the new version absorbed before
porting your own workaround.** Three of this port's hand-written renderer pieces were
reimplementations of things 5.5.4 now does itself, and porting them faithfully would have shipped
a second, divergent copy of each (§P#157's rule, in a library rather than in vanilla):
`ItemInHandGeoLayer` already applies the −90° X rotation and the shield offsets the mod's
`ItemLayer` hand-rolled; `ItemArmorGeoLayer` takes a `List<RenderData>` of (bone, segment) pairs
where the mod had three parallel switches; and `GeoArmorRenderer` no longer needs either of the two
overrides that existed to work around 4.8.4. · **What genuinely has no successor is worth keeping**,
and the mechanism for it is not obvious: a pulsing glow layer whose colour changes per frame swaps
`DataTickets.RENDER_COLOR` around the `super.submitRenderTask` call, which is exactly the trick
`AutoGlowingGeoLayer` uses for `PACKED_LIGHT`. Read the library's own layers before concluding a
feature is lost. · **And the layers MOVED**: `AutoGlowingGeoLayer`, `ItemInHandGeoLayer` and
`BlockAndItemGeoLayer` are under `com.geckolib.renderer.layer.builtin`, not `…renderer.layer`, so
the §V18 map's "not found" for them is a sub-package move rather than a removal.

**V73. `GeoRenderProvider.createGeoRenderer` is the portable item hook, and it dodges §S4b.**
An item that renders through GeckoLib has to attach a renderer, and the obvious 1.21.1 route is
NeoForge's `Item.initializeClient(Consumer<IClientItemExtensions>)` — which **§S4b records as
declared nowhere on 26.2**, silently doing nothing. GeckoLib's own
`createGeoRenderer(Consumer<GeoRenderProvider>)` exists with the same shape on BOTH versions, so
routing through it is shared source with no pair, no rename row, and no dead hook to notice later.
When a library offers a hook that parallels a loader one, prefer the library's: it is the one whose
lifetime you can check with a single `javap` of two jars.

**V74. 🔴 26.2 REFUSES `SynchedEntityData.defineId` against an entity class a mixin has touched —
and the crash is thrown from VANILLA's own `<clinit>`, so it names a line no mod wrote.** ·
**Pattern:** the ordinary way a mixin gives a vanilla mob a synched flag — a
`private static final EntityDataAccessor<Boolean> X = SynchedEntityData.defineId(Spider.class, …)`
in the mixin, which Mixin merges into `Spider` · **Runtime:**
`IllegalStateException: Identified an attempt to add synced data to a foreign entity`, with
`Mixins into entity class: …SpiderEntityMixin` and a stack whose deepest frame is
`Spider.<clinit>(Spider.java:49)` — i.e. vanilla's OWN defineId is what trips. It fires from
`EntityAttributeCreationEvent`, so the mod does not load at all ·
**Fix, read off `CommonHooks.verifyEntityDataAccessorRegistration`'s bytecode rather than guessed:**
when the declaring class IS the entity class, it scans that class's `getDeclaredFields()` for one
carrying `@MixinMerged` whose type is **assignable to `EntityDataAccessor`**, and throws if it
finds any. So the flag must move to a **synced data attachment** (which is what the message tells
you), and — the half the message does not tell you — **the mixin must not declare a field of that
type at all**, even one holding `null`.
· 🔴 **The 1.21.1 half then makes this a §W5 pair with a load-bearing FIELD TYPE, which is not a
shape the catalogue had.** On 1.21.1 the accessor MUST be allocated inside the owning vanilla
class's own `<clinit>`, and a merged mixin static field is exactly what does that. Allocate it
lazily instead — on first use, from `defineSynchedData` in the constructor — and a SUBCLASS of the
owner has already taken the next id out of `SynchedEntityData`'s pool, so the late `defineId` hands
out an id that is already held: measured, `fungus_thrower` and `zombified_fungus_thrower` died at
spawn with `ArrayIndexOutOfBoundsException: Index 20 out of bounds for length 20`. So the field is
required on the old target and forbidden-as-an-accessor on the new one.
· **The pair is therefore over the field's TYPE**: an `allocate(EntityFlag)` returning **`Object`**
on both halves — the accessor on 1.21.1, `null` on 26.2 — with the mixin field declared `Object`.
That is also the honest type: it is an allocation anchor and is never read as an accessor anywhere.
· **Two things worth carrying.** The verifier keys on the field TYPE, not on the presence of a
mixin-merged field, so the mod's ordinary mixin fields (goals, counters) are untouched — check that
before concluding a mixin cannot add fields at all. And this is §X8 in its purest form: restoring
the field fixed the canonical target and broke the new one in the same commit, so neither run alone
could have shown both.

**V75. An `AnimationDefinition` bone is resolved as a DESCENDANT of the model's root, and 26.2
moved that resolution from per-FRAME to the CONSTRUCTOR.** · **Pattern:** a model that hands a
CHILD in as its root — `super(root.getChild("redstonecube"), RedstoneCubeAnimations.WALK)` — while
the animation names that very part · **Runtime on 1.21.1:**
`IllegalArgumentException: Cannot animate redstonecube, which does not exist in model`, thrown the
first time the entity is DRAWN, so it is invisible to every gate that never opens a window ·
**Runtime on 26.2:** the same message, thrown from `AnimationDefinition.bake` inside the model
CONSTRUCTOR — which runs inside `EntityRenderers.createEntityRenderers` during the resource reload,
so one latent defect fails the whole INITIAL reload and the client sits at the title screen forever
(§V70) · **Fix:** pass the true root; the child stays a field. Passing a child as the root is
perfectly legitimate and common (`"everything"`, `"root"`) — what makes it wrong is only when the
animation names that part rather than a descendant of it, so fix the one model, not the pattern.
· **The generalisation, and it is §V67's:** when a version moves work from lazy to eager, every
latent defect of that kind moves from "a crash someone might see" to "the reload fails", which is
the failure mode that does not look like a failure.

· ⚠ **AUGMENT — the other way a bone goes missing, and the two targets disagree about it.**
§V75 is a bone that exists but sits outside the root you passed. The commoner case is a bone that
does not exist at all, because the animation file was authored against a fuller model: the Royal
Guard's walk animates a `mob_cape` its model never had. **1.21.1 tolerated this** —
`KeyframeAnimations.animate` does `getAnyDescendantWithName(bone).ifPresent(...)`, a silent skip
per frame — while **26.2's `KeyframeAnimation.bake` THROWS**. So a shipped animation that has worked
for years crashes the first time a 26.2 client draws it. · **Fix, keeping 1.21.1's behaviour:** filter
the definition before baking —
`root.createPartLookup()`, keep only the bones it resolves, then
`new AnimationDefinition(length, looping, kept).bake(root)` (a `bakeLenient` in the model base).
Fixing the data would be tidier but means auditing every animation of every mob, and the lenient
bake is exactly the semantics every one of them was authored against. · **Why it hid:** on
a ~390-file mob mod nothing baked these animations on 26.2 at all, because the keyframe states never
reached the model (§V65b). A fix that makes a code path run for the first time is a new code path,
with that path's latent bugs.

**V76. 🔴 A RETURN VALUE cannot be added by an argument-level rename — but the SIGNATURE LINE can
become a two-method shim, and that is the cheapest correct row in a §W table.** ·
**Pattern:** `Item.releaseUsing(ItemStack, Level, LivingEntity, int)` and
`MobEffect.applyEffectTick(ServerLevel, LivingEntity, int)` both gained a **boolean return** on
26.2 while keeping their parameters · **Error:** `releaseUsing(...) in XItem cannot override
releaseUsing(...) in Item … return type void is not compatible with boolean` — 25 files for the
first, 2 for the second · **Fix, and the shape generalises to every void→value change:** do not
rewrite the bodies. Rewrite only the line that DECLARES them, into the override vanilla now calls
plus a private continuation the original body stays attached to:

```
re:public void releaseUsing\(ItemStack itemstack, Level world, LivingEntity entityLiving, int timeLeft\) \{
   → public boolean releaseUsing(…) { this.mod$releaseUsing(…); return false; }\n\n   private void mod$releaseUsing(…) {
```

The body is copied verbatim by being left where it is, so nothing inside 25 files is touched and
nothing can drift; `$` is a legal Java identifier character, so the private name cannot collide with
anything the mod or vanilla declares. · **Pick the constant from the platform, not from taste:**
26.2's own `Item.releaseUsing` returns `false` by default and every one of these bodies is void with
no early exit, so none of them ever meant "I consumed this"; `applyEffectTick` returns `true` for
"the effect continues", which is what a void 1.21.1 body always did. Getting that backwards is a
behaviour change with a clean compile — §W6, flag the row as a judgement. · **When it does NOT
apply:** a body with early `return;` statements, which the shim would turn into early exits from the
continuation rather than from the override. Grep for `return;` inside the family before using it.

**V77. MCreator output is EXTREMELY uniform, so MEASURE the duplication before writing a single
rule (§W7 with a number on it).** · Hash each candidate method body with the per-file names
normalised away and count the distinct values. Measured on one 500-file mod: **19 projectile
renderers with byte-identical `render` bodies**, **11 mob renderers identical but for a shadow
radius**, and **18 models whose `renderToBuffer` was the same loop over their own parts**. 26.2
changes three separate things inside the first family alone (`MultiBufferSource` is declared
nowhere, `EntityRenderer` gained a render-state type parameter, drawing became record-then-submit),
i.e. one API change costing 19 hand-edits or 19 duplicated overlay files. · **Four §W5b base-class
pairs collapsed 48 files to four or five lines each**, and the burn-down went **195 → 24** on that
one change. The alternative — an overlay per renderer — is §W14's mistake at scale: 48 second
copies that every future edit has to be made to twice with nothing to say they drifted. · **The
command is three lines and worth running on any decompiled mod before the rename table is opened:**

```bash
for f in *.java; do sed -n '/void render(/,/^   }/p' $f \
  | sed -E 's/Model[A-Za-z0-9_]+/M/g;s/[A-Za-z0-9_]+Entity/E/g' | md5sum; done | sort | uniq -c
```

**V78. The vanilla MODEL layer: `EntityModel` is keyed on the RENDER STATE and `renderToBuffer` is
final — and the mod's own override is exactly equivalent to the one that replaced it.** §V38 records
that `Model.renderToBuffer` and `Model.root()` became final. What it does not say is what to do with
the ~20 models that overrode the first. · 1.21.1: `EntityModel<T extends Entity>`, `renderToBuffer`
abstract, and a decompiled model implements it by rendering each named part. 26.2:
`EntityModel<S extends EntityRenderState> extends Model<S>`, constructor takes the `ModelPart` root,
and the final `renderToBuffer` renders `root` — which renders every one of those parts, because they
are all children of it. So the two are the same drawing and the override is **deleted**, not ported.
· **The pair's type parameter is the whole trick.** Declare `ModEntityModel<T extends Entity>` on
both halves — extending `EntityModel<T>` on 1.21.1 and `EntityModel<EntityRenderState>` on 26.2,
accepting and ignoring `T` there — and every shared model keeps ONE declaration
(`class ModelExampleMob<T extends Entity> extends ModEntityModel<T>`) that compiles against both supertypes.
Extending the base state rather than `LivingEntityRenderState` is deliberate: `MobRenderer` wants
`EntityModel<? super S>`, so one model class then serves the projectile and the mob renderer families
alike. · **An empty `setupAnim` is a free pass**: every one of these models shipped one, so nothing
is lost by 26.2 not calling it. Check that before assuming animation has to be ported.

**V79. 🔴 `@OnlyIn` no longer STRIPS anything on 26.x, and the warning it raises is FATAL on a
dedicated server — so the annotation that used to buy you something now costs you the whole mod.** ·
**Pattern:** the class-level interface-stripping form MCreator emits on every custom arrow —
`@OnlyIn(value = Dist.CLIENT, _interface = ItemSupplier.class)` plus `@OnlyIn(Dist.CLIENT)` on the
`getItem()` it guards · **Runtime:** `Failed to wait for future Registry initialization, 1 errors
found` → a crash report whose only detail is `Failure message: loadwarning.neoforge.onlyin`, with
`Mod file: <No mod information provided>` — i.e. it names neither the mod nor the class nor the
annotation's target. Gate B dies before a single test runs · **Fix:** delete both annotations. §X16
already records that one `@OnlyIn` puts NeoForge's `LoadingErrorScreen` up on a CLIENT; this is the
same warning arriving on a dedicated server, where there is no screen to click past and mod loading
simply fails. Nothing is lost: 26.x does not strip, and on 1.21.1 the strip bought nothing either
here, because both the interface (`world.entity.projectile.ItemSupplier`) and the return type
(`ItemStack`) are common classes. **So it is a SHARED edit, not a rename row or a pair** — better
code on the old version too, which is the test §W5 asks of every extraction.
· **Two things worth carrying.** The count is what makes it findable — `grep -rl '@OnlyIn'` returned
**25 files**, all the same two annotations, so this is a whole-family sweep rather than a judgement
call per site. And **check what the annotation actually guards before deleting it**: if the stripped
interface or a parameter type really is client-only, deleting the annotation moves the failure from
mod-load to `NoClassDefFoundError` on a server, which is worse.

· ⚠ **AUGMENT — the severity above is DEV-ONLY, and the sweep is the part that matters.** Read
`OnlyInWarningsHandler`'s bytecode rather than the symptom: it opens with
`if (FMLEnvironment.isProduction()) return;`, so a **shipped jar is inert and a player never sees
this**. What it costs is every gate, because every gate here runs in dev — Gate B can die before a
test runs, and on a client `ClientModLoader` raises `loadwarning.neoforge.onlyin` and puts
NeoForge's `LoadingErrorScreen` between the client and the title screen. The suppression is a
system property (`neoforge.warnings.onlyin.hide`), i.e. the launcher's, not the mod's.
· 🔴 **AND A RENAME ROW CANNOT FINISH THE JOB — §W8 guarantees a residue.** A row strips SHARED
source and is structurally unable to reach an **overlay**, because a `src/mc26` file is
deliberately not run through the rename table. Measured across the ports here: a large boss mod's row took
**182 occurrences to 3**, and the three survivors were exactly its three mc26 overlays. So a port
can carry a correct, firing, `dead=0` §V79 row and still raise the warning — and every overlay
written afterwards is a fresh chance to reintroduce it, one file at a time, with nothing to say so.
`tools/audit-onlyin.py` (wired into `check` on all eleven multi-version ports and in
`templates/multi-version/`) reads the PREPARED tree, refuses when it is absent rather than falling
back to `src/main/java` — that is the canonical dialect and a different question — prints how many
files it looked at, exits 2 on zero (§X27), and is silent on a pre-26 target where the annotation
still strips and is doing a job. A/B'd both ways. Sweep result across all eleven: **4 ports
affected, 49 occurrences, 0 false positives** — and one near-miss worth copying, that a bare
`grep -c '@OnlyIn'` counts the word inside a *comment* (a transfer library's and a mob mod's overlays each
carry a javadoc paragraph explaining why they dropped it), so the check matches the annotation in
CODE POSITION only.
· ⚠ **The multi-line `_interface` form is what a single-line rule leaves behind**, which is §X10 in
this family: a ~390-file mob mod's projectile entity spreads `@OnlyIn(\n value = Dist.CLIENT,\n
_interface = ItemSupplier.class\n)` over four lines, so a row anchored on one line misses the one
annotation §V79 is actually named for. `[^)]*` matches newlines and covers both shapes.

**V80. A resource-layer generator that reads SHARED source must accept the SHARED spelling — and a
guard that refuses is the only reason you find out.** · **Pattern:** `gen-client-items.py` recovers
each spawn egg's two tint colours by scanning the registration call, deliberately from
`src/main/java` because that is the single place the mod states them (§V42b's design note) ·
**Symptom:** `--check` fails with *"matched no spawn-egg registrations … the call shape this script
looks for has changed"* — which is the tool working exactly as intended, because the alternative is
49 untinted eggs shipping as a green build · **Three separate pins, all of which made it match
nothing:** the `DeferredRegister` field was assumed to be called `ITEMS` (MCreator names it
`REGISTRY`); the two colours were matched as `(\d+)`, and `-6711040` is a perfectly ordinary ARGB
egg colour; and the call was matched only in its COMPAT-PAIR spelling
(`…SpawnEggs.spawnEgg(`) when shared source still says `new DeferredSpawnEggItem(` — the rewrite to
the pair happens downstream, in the prepared tree the generator does not read.
· **The general shape, and it is §X1's mirror:** every §W generator sits on ONE side of the rename
pipeline, and which side it reads is a fact about the tool that its regex has to agree with. Ask it
explicitly. · Fixed in the mod and in `templates/multi-version` (§X24).

**V82. 🔴 A SHARED STATIC became a PER-ENTITY PROPERTY whose default is the INERT one — so the
faithful-looking rename is a silent regression.** · **Pattern:** the standard "float on the fluid
surface" idiom every custom boss copies from vanilla's Strider —
`collisionContext.isAbove(LiquidBlock.STABLE_SHAPE, this.blockPosition(), true)` ·
**Error:** `cannot find symbol: variable STABLE_SHAPE` ·
**Fix:** 26.2 deleted the constant and moved the decision onto the mob:
`LiquidBlock.getCollisionShape` now asks the colliding `LivingEntity` for
`getLiquidCollisionShape()`, and **`LivingEntity`'s default is `Shapes.empty()`**. So the obvious
port — reconstruct the box as your own constant and keep reading it — compiles, reads as faithful,
and **sinks every mob that used to float**: the mod's own "am I standing on lava" test answers yes
while the block hands it nothing to stand on. The two halves have to move together. Have each
floating mob DECLARE the shape (1.21.1's own `Block.box(0,0,0,16,8,16)`; 26.2's Strider spells the
same thing `Block.column(16.0, 0.0, 8.0)`, which does not exist on 1.21.1) and have the check ask
for it.
· **In a §W tree this needs no pair and no rename row, which is the pleasing part.** 1.21.1's
`LivingEntity` has no `getLiquidCollisionShape`, so a shared `public VoxelShape
getLiquidCollisionShape()` — **with no `@Override`**, which would not compile there — is an inert
declaration on the old target and a real override on the new one, while the block goes on using its
own identical constant. Because the float check calls that one method on both targets, the mod's
test and the block's collision cannot disagree on either version. **The general shape:** a method
the OLD supertype does not declare is free to sit in shared source; it is only the reverse (§S4b, a
hook the NEW version deleted) that goes silently dead.
· **The cheap check that settles the whole family, and it is §V27's:** find who vanilla's own
equivalent mob is and diff its body. 26.2's `Strider.floatStrider()` is byte-for-byte the mod's
float method with that one substitution — which named both halves of the fix in one read, where
reasoning from the error alone would have produced only the first. And **read the DEFAULT of the
new property before believing a rename**: a property whose default preserves the old behaviour is a
rename, and one whose default is empty/zero/false is a feature you now have to opt into.
· Do not put the declaration on a shared BASE class to save typing: only 4 of that base's subclasses
float, and widening the shape to the rest is a behaviour change nothing would report. (a large boss mod:
four of its bosses.)

**V83. 🔴 A MODEL BASE that hands vanilla an EMPTY root makes every `renderToBuffer` CALL inert —
so the fix for the base is only half the port, and the other half is silent.** §V78 records that
26.2's `Model.renderToBuffer` is final and renders the part given to the constructor, and that a
base whose geometry is not a vanilla `ModelPart` tree must therefore pass an empty part and expose
its own drawing seam (`modRender` / `libRender`). That fixes the *models*. What it does not say
is what happens to the CALLERS. · **Pattern:** any renderer or layer still spelling
`model.renderToBuffer(pose, buffer, light, overlay, colour)` · **Symptom:** none. The method is
public and final, so the call compiles on both targets, runs, and on 26.2 draws the empty part —
nothing. 1.21.1 is unaffected, because there the base either overrides `renderToBuffer` or forwards
it, so the §X15 control column is green too and the burn-down never had an opinion. · **Fix:**
rewrite the call sites to the seam. **Measured on a large boss mod: 123 of 145 shared call sites were
inert** — the whole item-render, block-entity-render, projectile and layer surface of the mod, i.e.
a client that would have loaded, passed a `launch` Gate C, and rendered almost nothing.
· **Find them by RESOLVING THE RECEIVER, not by grepping the method.** The discriminator is the
receiver's static type, so build the class → superclass map from the tree, mark every class
transitively rooted at a base that passes an empty part, then for each `X.renderToBuffer(` resolve
`X` from the file's own field/local declarations (plus the `((Cast)…)` form, which is how a layer
reaches its parent model). That also gives you the 22 sites that are CORRECT — a humanoid or skull
base passing a real root — and a blanket rewrite would have broken those.
· ⚠ **A GENERIC layer has no static type to resolve, and that is the residue.** Three of these
layers are parameterised over the entity and are attached to renderers whose models are a MIX of two
different empty-root bases, so no static call exists on either. They need a §W5 pair that dispatches
per INSTANCE (`instanceof` each base, falling through to vanilla's method for a genuinely vanilla
model) — and, upstream of that, the layer base's `M` has to become unbounded on BOTH halves, because
a bound of `M extends EntityModel<T>` forces every layer to name a model type legal on both targets
and these three cannot.
· **The general shape, and it is §S4's with the roles swapped:** javac checks names and types, never
side effects — and here the side effect that disappeared is *drawing*. Whenever a port gives a base
class a renamed seam because the vanilla one became final, the very next question is **who still
calls the vanilla one**, and the answer is not a compile error.

· 🔴 **AUGMENT — and "who still calls it" has an answer no grep can reach: VANILLA does.** §V83 was
written about the mod's own call sites, and it is only half the blast radius. On 26.2
`LivingEntityRenderer.submit` draws the mob with
`submitNodeCollector.submitModel(this.model, …)` → `ModelFeatureRenderer` →
**`model.renderToBuffer(…)`** (traced through the shipped sources, `SubmitNodeStorage.java:80`). So
a `MobRenderer` whose model is one of these bases draws **nothing at all**, with no
`renderToBuffer` anywhere in the mod to find and nothing for javac to say. Measured on a ~660-file GeckoLib mob mod:
**eight mobs — most of its non-GeckoLib roster, a debug tester among them — rendered as nothing on a clean compile**, green on both targets, on a port whose burn-down
had reached the client tail.
· **The ANIMATION goes with it, and that is a second silent failure inside the first.** 26.2 calls
only `model.setupAnim(S state)`, which on `Model` is `resetPose()` (`Model.java:52`) — a no-op on an
empty root. So even a mob you fix into visibility stands in bind pose until the pose is driven too.
Fixing both together (a private first layer that poses in the 1.21.1 shape and draws through the
seam, **at vanilla's own model pose** so no pose arithmetic is duplicated) is what that
port did.
· **The COUNT of affected renderers is the count of `MobRenderer`s whose model is such a base** —
resolve it through the transitive supertype map, not by grepping the base's name, because the models
are usually two or three subclasses down (`AdvancedModelBase` → `BasicModelBase` → the compat base).
· 🔴 **The corollary that made this expensive to find, and it generalises past rendering: an INERT
CALL IS NOT AN ERROR, so it is invisible to every list a port works from.** Buckets cut from the
error list cannot contain it; the burn-down never mentions it; the canonical control stays green
because on the old target the call is correct. Four more inert sites in that port belonged to no
bucket for exactly that reason. **Ask for a NUMBER of such sites across the WHOLE tree, from
whoever finds the first one** — a yes/no answer scoped to one bucket is what leaves the other four.

**V81. 🔴 `PayloadRegistrar.commonBidirectional`'s THIRD ARGUMENT changed meaning, and the call
that is right on 1.21.1 kills a 26.2 client at load.** · **Pattern:** the ordinary bidirectional
payload registration a cross-loader mod writes once —
`event.registrar(ns).optional().commonBidirectional(type, codec, handler)` ·
**Error:** none, on either target. Same name, same arity, same argument types ·
**Runtime (26.2 CLIENT only):** `IllegalStateException: Some clientbound payloads are missing
client-side handlers: [<ns>:<payload>]`, thrown from `ClientNetworkRegistry.setup()` during
`ClientModLoader.finish` — so the client dies before the title screen, and the message names a
payload and **no mod** ·
**Fix, read off both bodies rather than the signature list:**

| | 1.21.1 | 26.2 |
|---|---|---|
| `commonBidirectional(type, codec, handler)` | the ONLY overload; that handler serves **both** directions | delegates to a 4-arg form with **`clientHandler = null`** — the handler is the SERVER's |
| `commonBidirectional(type, codec, server, client)` | does not exist | the real one |

26.2 also added `RegisterClientPayloadHandlersEvent` and a `ClientNetworkRegistry` whose `setup()`
walks every registration and refuses any CLIENTBOUND one with no client-side handler. Pass the same
handler twice and 1.21.1's behaviour is restored exactly — which is usually right, because a handler
written for a bidirectional payload already branches on `ctx.flow()` to decide which side it is on.
· **In a §W tree it is a compat pair and cannot be anything else**: 1.21.1 has no 4-argument
overload to call, so no shared spelling exists and no rename row can carry it.
· **Why it is worth its own entry rather than a row in §V38, and it is §V39/§V34's shape at its
purest:** every signal a port normally reads says nothing. javac is silent because the types did not
move; the burn-down was already at zero; **Gate B is green on both targets**, because a dedicated
server never runs the client registry's validation; and the 1.21.1 client is green too. Only a real
26.2 client, at load, says a word — which is §V58b's argument for giving even a pure LIBRARY its own
Gate C, arriving in the networking layer instead of the fluid layer. (a small shared-API library, whose whole
client surface is two mixins and a particle base.)
· **The cheap check, and it generalises to every "bidirectional"/"common"/"either side" API:** when
one version offers `f(a, b, h)` and the other offers both `f(a, b, h)` and `f(a, b, h1, h2)`, the
shorter form on the newer version is almost never the older one's synonym — it is the longer one
with a default. `javap` is not enough here (the overload sets differ, which reads as a pure
addition); decompile the short form and see what it passes.


**V84. 🔴 26.2 has NO particle `RenderType` at all — so a self-drawing particle must go through an
ENTITY one, whose vertex format wants two elements the particle body never emits, and the compat
pair that hands it `null` instead is a hard render-thread crash.** ·
**Pattern:** the common "particle that draws its own geometry" — a lightning arc, a sword trail, an
expanding ring — which on 1.21.1 is handed vanilla's particle-sheet buffer
(`ParticleRenderType.PARTICLE_SHEET_TRANSLUCENT` → `DefaultVertexFormat.PARTICLE`: position, uv,
colour, light) and writes `buffer.addVertex(...).setUv(..).setColor(..).setLight(..)` into it ·
**Runtime:** `NullPointerException: Cannot invoke "VertexConsumer.addVertex(float, float, float)"`
from the particle's own render method, on the render thread, the first frame one is alive ·
**Fix, and it has two halves that are easy to do only one of:**
1. **Supply a real consumer.** 26.2 replaced particle drawing with `ParticleGroup` +
   `QuadParticleRenderState`, and that pipeline is unreachable from a custom group — `RenderTypes`
   declares no particle type whatsoever (checked, not assumed: 54 `public static RenderType`
   factories, none of them particle). The nearest thing that draws a particle-atlas sprite is
   `RenderTypes.entityTranslucent(TextureAtlas.LOCATION_PARTICLES)`, fetched through whatever
   synchronous-over-deferred seam the port already has for the removed `MultiBufferSource` (§V54/§V66).
2. **Close the FORMAT gap, or every vertex throws.** An entity format additionally wants an overlay
   and a normal, and 26.2's `BufferBuilder` does not default them — it throws
   `IllegalStateException: Missing elements in vertex: ...`. So the consumer handed to the particle
   is a thin decorator that completes each `addVertex` with `OverlayTexture.NO_OVERLAY` and an up
   normal. Element ORDER does not matter to `BufferBuilder`; only that every element the format
   declares is written before the next vertex begins.
· ⚠ **AUGMENT — the entity render type WRITES DEPTH, and the particle type it replaces usually does
not.** §V84 prescribes `RenderTypes.entityTranslucent(atlas)` as the nearest workable path. That is
right about the geometry and wrong about one record field: 1.21.1's particle sheet is routinely used
through a `depthMask(false)` variant (a mod's own `PARTICLE_SHEET_TRANSLUCENT_NO_DEPTH` is the
common shape), while `RenderPipelines.ENTITY_TRANSLUCENT` writes depth. Ported verbatim, a mod's
translucent VFX start OCCLUDING EACH OTHER — a plausible picture, no error, and nothing below a
person looking at a client can see it. · **The fix is one field, and vanilla shows you the
expression**: `RenderPipelines.ENTITY_TRANSLUCENT.toBuilder()` with the depth-write flag flipped,
using the same form vanilla's own `WEATHER_NO_DEPTH_WRITE` uses against `DepthStencilState.DEFAULT`,
registered through `RegisterRenderPipelinesEvent` — where a failure is loud. Everything else (blend,
cull, shaders, format, cutout) stays whatever vanilla says. · **And the group registration is not
optional**: `ParticleEngine.extract` iterates the render-ORDER list that `RegisterParticleGroupsEvent`
appends to, so an unregistered type is created, ticked, and never drawn — the same silent no-draw
§V84 is written to prevent, arriving one layer up. · **One difference that is in the mod's favour and
should be recorded rather than fixed**: 1.21.1's `particle.fsh` discards on FINAL alpha < 0.1
(texture x vertex x modulator); 26.2's `entity.fsh` ALPHA_CUTOUT tests the TEXTURE's alpha alone. A
fading particle now fades further instead of popping out. Worth checking deliberately on any mod
whose particles cap alpha low — measured on a ~660-file GeckoLib mob mod, two of its ten sat right on the old
threshold and were already being cut. (that mod: 4 of 10 particles draw geometry vanilla cannot
express, so the whole §V84 shape was needed.)

· **The mistake that produced the null is worth more than the fix, because it is a shape rather than
a slip.** The pair's own javadoc said *"the consumer is deliberately null — all eight ignore it,
verified, not assumed"*, and offered the good argument that a no-op consumer would buy the silent
failure instead of a loud one. Every word of that was true **about a different class**: the port had
since moved the eight from the mod's custom particle base (which extends `Particle` directly) onto a sibling base
that keeps `TextureSheetParticle`'s sprite machinery, and **seven of the eight write into the
consumer**. The base the claim was written about ended with **zero subclasses**.
· **The check is a count and it takes one command** — `grep -rc <base> src/main/java`, plus, for each
subclass, whether the parameter appears in the override's body at all. A verification that names a
CLASS is only as good as that class still being the one with the subclasses; a verification that
names a NUMBER survives the refactor that invalidates it. Same discipline as §X27's `checked == 0`
and §X15's batch composition: **print the scope, not only the finding.**
· **And it is a §X14 blind spot in its purest form:** the burn-down was at 0 on both targets, Gate A
and Gate B were green on both, and Gate C `launch` passed on both — because none of them draws a
frame with a mod particle in it. Only the `spawn` phase, with a real client rendering a real mob,
says a word. (a large boss mod: its custom particle base's `Group`, 8 particles.)


**V85. A vanilla BLOCK ID was renamed and no rename table can see it, because the reference is in
DATA — `minecraft:chain` → `minecraft:iron_chain`.** · **Pattern:** a mod block/item tag naming a
vanilla id (`"values": ["minecraft:soul_lantern", "minecraft:chain", …]`) · **Runtime:**
`Couldn't load tag <ns>:<tag> as it is missing following references: minecraft:chain (from mod/<ns>)`
— and a missing REQUIRED reference fails the **whole tag**, so the tag is empty rather than short.
Whatever it drove is then dead: here a boss's interaction with chandelier blocks, on a mod nothing
else complained about · **Fix, and it needs no overlay:** 26.2 renamed `Blocks.CHAIN` to `IRON_CHAIN`
(registry id `minecraft:iron_chain`) and added a `COPPER_CHAIN` weathering collection beside it —
§V11/§V33's shape reaching another family. One SHARED file carries both as optional entries, which is
§V45's union principle exactly: `{"id": "minecraft:chain", "required": false}` next to
`{"id": "minecraft:iron_chain", "required": false}`. Each target resolves one and skips the other, and
nothing needs a per-target resource.
· **Why the port's instruments are all blind to it:** the rename table rewrites JAVA, and this
reference is a string in a data file; the burn-down was at zero; Gate B loads tags but a tag failing
to build is an ERROR line, not a test failure. It surfaced only from **diffing the two targets' Gate-C
logs** (§X15 applied to logs rather than verdicts) — one `Couldn't load tag` on 26.2, zero on 1.21.1.
· **The general check when a data file names a vanilla id:** `grep -rhoE '"minecraft:[a-z_/]+"'
src/main/resources/data | sort -u` and ask the new target's registry about each. It is the §142/§144
sweep one era on, and the failure mode is the same — silent on the old version, fatal or empty on the
new one.

**V86. 🔴 `PlayerRenderer` was RENAMED to `AvatarRenderer`, `PlayerModel` stopped being GENERIC, and
`LivingEntityRenderer` grew a THIRD type parameter — one change that lands in the move map as a
DELETION, one that the map cannot describe at all, and one that manufactures a hundred phantom
"missing field" errors.** · **Pattern:** any mod that subclasses, wraps or layers the player
renderer — a custom first-person renderer, a cape/elytra/parrot layer, a "render the player as
something else" hook · **Error:** three families that look unrelated and are one change:
`cannot find symbol: class PlayerRenderer`; `type PlayerModel does not take parameters`; and a
long tail of `wrong number of type arguments; required 3` / `cannot find symbol: variable model`
· **Fix, read off the jar rather than the error text:**

| | 1.21.1 | 26.2 |
|---|---|---|
| renderer | `client.renderer.entity.player.PlayerRenderer` | **`…player.AvatarRenderer<E extends Avatar & ClientAvatarEntity> extends LivingEntityRenderer<E, AvatarRenderState, PlayerModel>`** |
| model | `client.model.PlayerModel<T extends LivingEntity>` | **`client.model.player.PlayerModel extends HumanoidModel<AvatarRenderState>`** — no type parameter |
| living renderer | `LivingEntityRenderer<T, M>` | `LivingEntityRenderer<T, S extends LivingEntityRenderState, M extends EntityModel<? super S>>` |
| layer | `RenderLayer<T, M>`, `render(PoseStack, MultiBufferSource, int, T, …)` | `RenderLayer<S extends EntityRenderState, M>`, **`submit(PoseStack, SubmitNodeCollector, int, S, float, float)`** |
| skin | `client.resources.PlayerSkin` | `world.entity.player.PlayerSkin` |

plus a new `world.entity.Avatar` that `Player` now extends, and `client.entity.ClientAvatarEntity`
/ `ClientAvatarState` / `entity.state.AvatarRenderState extends HumanoidRenderState`.
· 🔴 **`protected M model` and `protected final List<RenderLayer<S,M>> layers` STILL EXIST on
`LivingEntityRenderer`** — verified with `javap`. So every `cannot find symbol: variable model` in
this family is a **consequence of the wrong type-argument count**, not a removed field: javac
cannot resolve a member on a type it could not construct. Measured on a ~660-file GeckoLib mob mod, ~38 of them
evaporated on fixing the arity. **Chasing a `variable model` error as a removal is a whole
afternoon spent restoring a field that was never gone**, so before believing any
`cannot find symbol: variable X` on a generic vanilla class, fix the arity errors in the same file
FIRST and re-measure — §X9's per-family bucketing applied within one file.
· 🔴 **The MOVE MAP classifies this rename as a DELETION, and that is structural rather than a
bug in the map.** `tools/build-class-move-map.py` matches SIMPLE NAMES across the two classpaths,
so a class whose simple name changed cannot be matched and lands in `.removed.txt`
(`…entity.player.PlayerRenderer`, line 469), while `PlayerModel` — whose simple name survived —
lands in the `.tsv` as an ordinary sub-package move (line 154) **with nothing to say it stopped
being generic**. The map is therefore silent in both directions on the same change. Same blindness
as §V44's changed supertype: the map answers *where did this name go*, never *what is it now*.
· **The cheap test that separates a rename from a removal, and it is not a grep:** ask the jar for
the class that occupies the same ROLE — here `javap`-ing anything in
`client.renderer.entity.player` at all lists `AvatarRenderer` beside `PlayerRenderer`'s absence,
and its declared supertype names the model and state in one line. `unzip -l <jar> | grep
'entity/player/'` is the whole search. A name in `.removed.txt` is a **question**, not an answer
(§V27).
· **AUGMENT — the rest of the family, measured on the same port.** `PlayerSkin` moved
`client.resources` → **`world.entity.player.PlayerSkin`**, and the skin ENUM `PlayerSkin.Model`
became **`world.entity.player.PlayerModelType`**; on NeoForge's `EntityRenderersEvent.AddLayers`
the accessor `getSkin(skin)` became **`getPlayerRenderer(skin)`**, and `getRenderer`'s bound went
from `EntityRenderer<T>` to `EntityRenderer<T,?>`. A single `AddLayers` loop therefore carries an
enum rename, a method rename and two arity changes at once, which is why it wants a §W5 pair rather
than four table rows — and the arity change surfaces there **inside an `instanceof`**
(`LivingEntityRenderer<?,?>` → `<?,?,?>`), not as a field access, so it looks nothing like the
`variable model` family above.
· 🔴 **`AvatarRenderer` draws MANNEQUINS as well as players, and that is a behaviour WIDENING on
exactly one target.** `Mannequin extends Avatar extends LivingEntity` and `ClientMannequin
implements ClientAvatarEntity`, so one renderer serves both; 1.21.1 has no mannequin at all. Every
player layer a mod adds is now offered a decoration block to draw on, the canonical target stays
correct, and §X15's control column therefore **cannot see it**. Decide per layer rather than
inheriting the widening by default, and enforce the decision STRUCTURALLY: test
`instanceof AbstractClientPlayer` when recovering the entity, and iterate `getPlayerRenderer` only,
never `getMannequinRenderer` — the event keeps the two maps separate, so a later edit cannot quietly
widen it, where a filter can be deleted.
· **`AvatarRenderer.getArmPose` is `private static` on 26.2** and needs no access transformer:
`extractRenderState` has already put both poses on `ArmedEntityRenderState.rightArmPose` /
`leftArmPose`, which is where vanilla's own model reads them.
· ⚠ **A rename row for `PlayerModel` can never be sufficient on its own**, whatever the package map
says, because the class stopped being generic — so every `PlayerModel<T>` in shared source breaks
*after* the row fires correctly. That is why such a file becomes an overlay, and it is worth knowing
before writing the row rather than after (§W1: a REAL difference wearing a MECHANICAL one's clothes).


**V87. 🔴 26.2 VALIDATES A CONFIG VALUE'S OWN DEFAULT when the spec is built, and 1.21.1 does not —
so a validator that asks a REGISTRY rejects the mod's own items and the mod refuses to load.** ·
**Pattern:** the ordinary "is this a real item id" config validator —
`RESOURCE_LOCATION_PREDICATE.and(s -> BuiltInRegistries.ITEM.containsKey(ResourceLocation.parse(s)))`
— on a `define(path, <one of the mod's own items>, predicate)` · **Runtime:**
`IllegalArgumentException: Configuration value <path> defined in config <modid>-common.toml has a
validator that does not accept its own default value of <modid>:<item>`, at mod construction, so
nothing loads · **Fix, and check WHICH version is the odd one before touching the predicate:** grep
both NeoForge jars for that message — it is present only in 26.2's `ModConfigSpec`. A spec is built
during mod CONSTRUCTION, before `RegisterEvent`, so at that moment **vanilla's** registry entries
exist and **the mod's own do not**. Every default that names one of the mod's own items therefore
fails its own validator on 26.2 and passes on 1.21.1 by never being asked.
· **Relax the predicate NARROWLY, in the mod's own namespace only** — `containsKey(id) ||
MODID.equals(id.getNamespace())`. An id in any other namespace must still exist, which is what the
check is for: a user naming an item from a mod they have not installed still gets told. The mod's
own ids are guaranteed registered long before anything READS the config, and they are exactly the
set that cannot be resolved at spec-build time, so the exemption is the shape of the problem rather
than a hole in the check.
· **The general form, and it is §V82's read backwards:** an era jump can add a check that runs
EARLIER than the state it depends on. Ask not only "did this API change" but "did the version move
WHEN it is evaluated" — a validator, a codec, a registry lookup or a default that was lazy on one
target and eager on the other is a defect with a clean compile on both. §V67 is the same shape for
`ItemStack.CODEC` and recipes; this is it for configs.
· ⚠ **It arrives in a QUEUE with the rest of §X38**, because each mod-load failure hides the next.
On a ~660-file GeckoLib mob mod the order was: §A#3b's hoisted config fields (twice — the objects above their
builders, then the predicates below their consumers), then §W13's literal version ranges in a
decompiled `neoforge.mods.toml`, then this, then §R25's unset `Properties` id. **Five distinct
runtime defects behind a burn-down that had reached zero on both targets** — which is §X14's blind
spot measured rather than asserted, and the reason "the compiler has nothing left to say" is a
milestone and not a verdict.

**V88. 🔴 A GeckoLib 5 renderer's render-state bound must be `EntityRenderState`, not
`LivingEntityRenderState`, the moment ONE animatable it serves is not a `LivingEntity` — and the
obvious fix moves the same crash somewhere it reads as a different bug.** ·
**Pattern:** the natural port of §V18's arity change — a mod's own `GeoEntityRenderer` base
declared `GeoEntityRenderer<T, LivingEntityRenderState>` because every mob it draws is living ·
**Runtime:** `ClassCastException: EntityRenderState cannot be cast to LivingEntityRenderState`,
from the mod's own `submit`, the first frame a NON-living animatable is on screen. GeckoLib
answers `animatable instanceof LivingEntity ? new LivingEntityRenderState() : new
EntityRenderState()`, so an effect entity — a rock sling, a pillar, a geomancy shape — gets the
base state and the bridge cast fails ·
**Fix:** widen the bound to `EntityRenderState`. That IS GeckoLib's intended parameterisation:
every living-only step inside the library is already behind an `instanceof
LivingEntityRenderState`, so nothing is lost and the living renderers keep working unchanged.
· ⚠ **The tempting fix is wrong and costs a Gate-C round.** Overriding `createRenderState()` to
always answer a `LivingEntityRenderState` compiles, looks narrower and merely MOVES the cast into
GeckoLib's own `extractRenderState`, which then casts the ENTITY —
`EntityPillar$EntityPillarSculptor cannot be cast to LivingEntity`. A different class, a different
method, the same defect; record the dead end in the class comment or the next reader re-tries it.
· **Budget it as ONE pass, not one file.** The bound cascades to the layer base and to every
renderer that names the living state — measured on a ~660-file GeckoLib mob mod, ~8 renderers and 17 compile
errors — and a half-applied widening is §X8's shape with the targets swapped: the new target goes
green while the tree does not compile at all. Finish it and compile BOTH targets before committing.
· **Why no cheap gate sees it:** it needs a real client with a non-living animatable actually
drawn, i.e. Gate C's `spawn` or `battle`. A mod whose effect entities only appear mid-combat will
pass `launch` and `spawn` and die in `battle`. (the GeckoLib mob mod's own GeckoLib renderer base / layer base.)


**V89. 🔴 `Brain`'s memory map holds a `MemorySlot` per type on 26.2, not an
`Optional<ExpirableValue>` — and an `@Accessor` that still declares the old generic compiles, binds,
and crashes the SERVER.** · **Pattern:** a mod that injects behaviour into a vanilla brain registers
the memory types it needs by writing into the private map through an accessor:
`((IMixinBrain) brain).getMemories().put(type, Optional.empty())` — exactly what 1.21.1's own `Brain`
constructor does · **Runtime:** `ClassCastException: Optional cannot be cast to MemorySlot` from
`Brain.forEach`, on the injected entity's first brain TICK ("Ticking entity" — the server stops) and
again at SAVE ("It will not persist"). A large boss mod (~750 files, mixin-heavy) injects into every VILLAGER, so any 26.2 world
with the mod and a village went down. · **Why the compile is clean:** the accessor's return type is
`Map`, and generics erase — the stale `Map<MemoryModuleType<?>, Optional<? extends ExpirableValue<?>>>`
binds to 26.2's `Map<MemoryModuleType<?>, MemorySlot<?>>` field by descriptor, and every caller then
sees the accessor's declared type, never vanilla's, so javac happily accepts an `Optional` put. ·
**Fix:** retype the accessor per target (one rename row) and route the one write through a §W5 pair
whose 26.2 half puts `MemorySlot.create()` (26.2's own "known, nothing stored"). · **The audit now
catches the shape** — §X27 fault #22. And the gate that found it was, again, a DEPENDENT's: the builder
mod's mall builds a crowd of villagers, so two of its mall tests failed as "0 of 97 shops have a
shopkeeper". Nothing in either message names a brain.

**V90. 🔴 `Screenshot.grab` completes ASYNCHRONOUSLY on 26.x — a photo harness that counts frames the
tick after it shot reports "done" with zero photographs.** · **Pattern:** a Gate-C photo helper that
calls `Screenshot.grab(...)` on its shutter tick and then, on the next tick, reads how many frames it
wrote to decide the shot is finished — correct on 1.21.1, where the capture is synchronous · **Symptom:**
on 26.2 every single-shot caller reads `frames().size() == 0` and moves on (or fails with "no
photograph"), while the PNG lands a few frames later with nobody waiting for it. A burst of several
shots loses only its last ones, which reads as flakiness · **Cause:** 26.x's render backend records the
frame and hands the readback to the GPU device, so the callback fires when the copy finishes, not when
`grab` returns (§V6's recorded-state model, reaching the screenshot path) · **Fix:** count captures IN
FLIGHT — increment before `grab`, decrement in its callback — and report busy until the count is zero,
with a bounded wait so a lost callback fails loudly rather than hanging. That is correct on BOTH
targets (on 1.21.1 the count simply returns to zero inside the same call), so it is shared code, not a
compat pair. · **Measured on the builder mod's `PhotoShoot`**: every single-shot caller on 26.2 reported no
photograph over a render that was fine. · **While you are in there:** set the time of day before
each shot (`time set 6000` is valid on both versions). A tour through thirty-four worlds crosses night,
and 21 of 68 frames came back black and failed the darkness gate for a reason nothing to do with the mod.

**V91. 🔴 A 26.x custom sky is chosen by an ENVIRONMENT ATTRIBUTE, so a dimension that never
names it gets vanilla's sky with nothing logged — bind it from `ExtractLevelRenderStateEvent`
instead.** · **Pattern:** the §V43 successor to `DimensionSpecialEffects` — a
`CustomSkyboxRenderer` registered on `RegisterCustomEnvironmentEffectRendererEvent` under an id, and
each `dimension_type` opting in with `"neoforge:custom_skybox": "<id>"` (clouds and weather the same,
via `neoforge:custom_clouds` / `neoforge:custom_weather_effects`) · **Runtime:** none. A dimension
without the attribute — above all one shipped by a DEPENDENT mod whose data was written for 1.21.1,
where the binding was the `effects` id — renders a vanilla sky disc from its biome's `SKY_COLOR`,
which on an airless world is flat black with no stars; the builder mod's 34 worlds did exactly this
over a space-exploration mod's (~550 files) own correctly-bound planets · **Fix:** read out of `LevelExtractor`: NeoForge resolves
`levelRenderState.customSkyboxRenderer` / `customCloudsRenderer` / `customWeatherEffectRenderer`
from the attributes and then posts `ExtractLevelRenderStateEvent` on the GAME bus, so a listener can
simply overwrite the three fields for the levels the mod owns — keyed on whatever 1.21.1 keyed on
(for the space mod, its planet-renderer JSON's dimension id). Leave the attribute in your own data; it is
just no longer load-bearing. · **Why it is worth an entry:** the attribute route is the documented
one and it is correct for the mod's OWN data, so every gate the mod has goes green — the failure
belongs entirely to data somebody else wrote, and only that somebody's client can see it. When a
binding moves from "the loader asks you" to "the data names you", ask who else's data relied on the
old question. · **Two parity traps in the same port:** vanilla's `CELESTIAL` pipeline is ADDITIVE
(`BlendFunction.OVERLAY`), so reusing it for a planet that 1.21.1 drew with blending off makes it
see-through; and do not copy vanilla's `-90 Y` / `starAngle` rotation onto a mod's star field that
1.21.1 drew fixed in camera space. (the space mod's `ModDimensionSpecialEffects.onExtract`,
`ModPipelines.SKY_RENDERABLE_*`; Gate C asserts it via `SkyProbe` in the gauntlet's
`PLANET_SKY` step.)

V92. **🔴 `ShieldItem` is an EMPTY SHELL on 26.2 — blocking is the `BLOCKS_ATTACKS` item component, so a subclass that only `extends ShieldItem` cannot block at all** · **Pattern:** `class MyShield extends ShieldItem { MyShield(Properties p) { super(p.durability(n)); } }`, plus call sites testing `stack.canPerformAction(ToolActions.SHIELD_BLOCK)` / `ItemAbilities.SHIELD_BLOCK` · **Error:** `cannot find symbol: variable SHIELD_BLOCK` (location: class ItemAbilities) for the call sites; the item class itself compiles, and then **Symptom:** right-clicking the shield does nothing, it never raises, `isBlocking()` is always false, with every gate green · **Fix:** read vanilla `Items.SHIELD` in the 26.2 sources and copy its properties onto every shield: `.equippableUnswappable(EquipmentSlot.OFFHAND)`, `.delayedComponent(DataComponents.BLOCKS_ATTACKS, ctx -> new BlocksAttacks(0.25F, 1.0F, List.of(new BlocksAttacks.DamageReduction(90.0F, Optional.empty(), 0.0F, 1.0F)), new BlocksAttacks.ItemDamageFunction(3.0F, 1.0F, 1.0F), Optional.of(ctx.getOrThrow(DamageTypeTags.BYPASSES_SHIELD)), Optional.of(SoundEvents.SHIELD_BLOCK), Optional.of(SoundEvents.SHIELD_BREAK)))`, `.component(DataComponents.BREAK_SOUND, SoundEvents.SHIELD_BREAK)`, and `BANNER_PATTERNS = EMPTY` on any shield that takes banners; replace every `SHIELD_BLOCK` test with `stack.has(DataComponents.BLOCKS_ATTACKS)`. `ShieldItem` itself now only names the banner colour. · **Two consequences to check:** `isBlocking()` reads the component rather than the use animation, so an item that used to stop blocking by returning `UseAnim.NONE` (an energy shield with no charge) must now call `stopUsingItem()` itself; and a shield without `MAX_DAMAGE` simply takes no durability from `BlocksAttacks.hurtBlockingItem`, which is the right shape for an energy shield. (a shield-adding item mod: 64 shield items, one base class.) · **Scan:** `grep -rn "extends ShieldItem" src/main/java` then confirm each class (or its base) sets `DataComponents.BLOCKS_ATTACKS`; `grep -rn "SHIELD_BLOCK" src/main/java`

V93. **A custom SHIELD-BANNER recipe is vanilla DATA on 26.2 — delete the serializer (§P#157 for recipes)** · **Pattern:** `class MyBannerRecipe extends ShieldDecorationRecipe` registered as a custom `RecipeSerializer`, one JSON per shield (`{"type": "<ns>:apply_banner", "shield": "<ns>:<shield>"}`) · **Error:** `constructor ShieldDecorationRecipe in class ShieldDecorationRecipe cannot be applied to given types` (26.2's takes `(Ingredient banner, Ingredient target, ItemStackTemplate result)`) · **Fix:** 26.2's `ShieldDecorationRecipe` takes its target as data, so each file becomes `{"type": "minecraft:crafting_special_shielddecoration", "banner": "#minecraft:banners", "target": "<ns>:<shield>", "result": {"id": "<ns>:<shield>"}}` and the Java class and serializer are deleted. Give the shield `DataComponents.BANNER_PATTERNS = BannerPatternLayers.EMPTY` (the recipe refuses a target that already has layers, and reads patterns/base colour into components). (a shield-adding item mod: 29 recipe files, one class.) · **Scan:** `grep -rln "extends ShieldDecorationRecipe" src/main/java`

V94. **Porting an item BEWLR (`IClientItemExtensions.getCustomRenderer`) to 26.2 is a `SpecialModelRenderer` NAMED BY DATA — and three details are easy to get wrong** · **Pattern:** `class X extends BlockEntityWithoutLevelRenderer implements ResourceManagerReloadListener` with a `renderByItem(stack, ctx, pose, buffer, light, overlay)` that picks a model and texture per item, returned from `initializeClient`; its item model JSON has `"parent": "builtin/entity"` · **Error:** `cannot find symbol: class BlockEntityWithoutLevelRenderer` / `MultiBufferSource`; on the resource side the `builtin/entity` parent no longer exists · **Fix:** `implements SpecialModelRenderer<DataComponentMap>` (`extractArgument` = `stack.immutableComponents()`, then `submit(args, pose, collector, light, overlay, hasFoil, outline)` + `getExtents`), with a `record Unbaked(...) implements SpecialModelRenderer.Unbaked<DataComponentMap>` whose `MapCodec` carries whatever picks the variant (an item-id string works, keeping the per-item tables in Java); register it in `RegisterSpecialModelRendererEvent`; each item's `assets/<ns>/items/<id>.json` is `{"type": "minecraft:special", "base": "<ns>:item/<display model>", "model": {"type": "<ns>:<renderer>", …}}`. Model the whole thing on vanilla `ShieldSpecialRenderer`. **(1)** the BEWLR's `pose.scale(1, -1, -1)` moves OUT of Java into the item definition's `"transformation": {"scale": [1, -1, -1], …}`, as vanilla's shield does; keeping both flips it back. **(2)** `BannerRenderer.submitPatterns` takes a `Model<S>`, not a `ModelPart`, and draws the pattern on the WHOLE model — wrap just the plate in `new Model.Simple(platePart, renderType)` or the patterns land on every extra part whose UVs overlap the plate's. **(3)** the base model loses `builtin/entity`: keep only `gui_light`, `display` and a `particle` texture. Per-part render types (a glowing layer on `RenderType.eyes`) become `collector.order(n).submitModel(model, Unit.INSTANCE, pose, RenderTypes.eyes(tex), light, overlay, argbColour, null, outline, null)`. (a shield-adding item mod: 30 tower shields, 10 model shapes.) · **Scan:** `grep -rln "BlockEntityWithoutLevelRenderer\|getCustomRenderer" src/main/java; grep -rl '"builtin/entity"' src/main/resources/assets`

V95. **Wrapping a `ShapedRecipe` on 26.2: SUBCLASS it with your own `MapCodec`, and cast the serializer** · **Pattern:** `class X implements CraftingRecipe, IShapedRecipe<CraftingContainer> { final ShapedRecipe internal; … }` whose serializer delegates to `RecipeSerializer.SHAPED_RECIPE.fromJson/fromNetwork` · **Error:** `cannot find symbol: variable SHAPED_RECIPE` / `method fromJson … does not override`; `RecipeSerializer` is a record `(MapCodec, StreamCodec)` · **Fix:** `class X extends ShapedRecipe` with a `MapCodec` built from the same public pieces vanilla uses (`Recipe.CommonInfo.MAP_CODEC`, `CraftingRecipe.CraftingBookInfo.MAP_CODEC`, `ShapedRecipePattern.MAP_CODEC`, `ItemStackTemplate.CODEC.fieldOf("result")`) and the matching `StreamCodec.composite`; keep the `ItemStackTemplate` in a field of your own (ShapedRecipe's is private; `commonInfo`/`bookInfo`/`pattern` are reachable); override `assemble(CraftingInput)` calling `super.assemble` first. `ShapedRecipe.getSerializer()` is typed `RecipeSerializer<ShapedRecipe>`, so the override returns `(RecipeSerializer<ShapedRecipe>)(RecipeSerializer<?>)` your registered one — the generics are invariant, the cast is sound because the codec produces your subclass. Do not use `ItemStack.CODEC` for the result (§V67). (a shield-adding item mod's energy-carrying upgrade recipe.) · **Scan:** `grep -rn "IShapedRecipe\|SHAPED_RECIPE" src/main/java`

V96. **`Direction.getNearest(x, y, z)` → `getApproximateNearest`** · **Pattern:** `Direction.getNearest(look.x, look.y, look.z)` in a ray-trace helper · **Error:** `no suitable method found for getNearest(double,double,double)` · **Fix:** `Direction.getApproximateNearest(double, double, double)` (also `(float…)` and `(Vec3)` overloads); same semantics. · **Scan:** `grep -rn "Direction.getNearest(" src/main/java`

V97. **🔴 On 26.2 an `Item`'s components are NOT BOUND until a world's registries load — reading them at the title screen throws** · **Pattern:** client code that runs before any world exists (a boot harness at `TitleScreen`, a model/texture check in a client setup or reload listener) reading `item.components()`, `item.getDefaultInstance()` or `new ItemStack(item).get(…)` · **Runtime:** `java.lang.NullPointerException: Components not bound yet` at `Holder$Reference.components(Holder.java)` ← `Item.components(Item.java)` · **Fix:** before a world is loaded, work from the item's registry id instead (`BuiltInRegistries.ITEM.getKey(item)` is the default `ITEM_MODEL` id, so `ModelManager.getItemModel(id)` answers "is this item's model bound" without touching components); defer anything that needs a stack until in-world. This is §V67's cause (components bind late, with the datapack) seen from the client side. (a shield-adding item mod's Gate C `launch` model check.) · **Scan:** `grep -rn "\.components()\|getDefaultInstance()" src/main/java | grep -i "client\|title\|reload\|boot"`

V98. **Gate C on 26.2: a synthetic client CANNOT hold up a shield through its own input path — raise it on the SERVER player and hold the use key from `ClientTickEvent.Pre`** · **Pattern:** a Gate-C `battle` phase that raises a shield with `mc.options.keyUse.setDown(true)` and/or `mc.gameMode.useItem(player, OFF_HAND)` from `ClientTickEvent.Post`, then checks `player.isBlocking()` · **Symptom:** `isUsingItem()` is true but `isBlocking()` never is — `getUseItemRemainingTicks()` sits at 72000 (or 71999) forever, so the 5-tick block delay of `BlocksAttacks` is never reached; **a vanilla `minecraft:shield` in the same harness behaves identically**, which is the control that proves it is the harness, not the port · **Cause:** `setDown` is not a click (use starts from `consumeClick()`), the key state is refreshed from the real keyboard before each tick, and `Minecraft.handleKeybinds` releases the used item every tick whose key is up — so each Post-tick restart resets the timer · **Fix:** keep the key down by setting it in `ClientTickEvent.Pre` (after the refresh, before `handleKeybinds`), and start the use on the server: `server.execute(() -> { if (!sp.isUsingItem()) sp.startUsingItem(InteractionHand.OFF_HAND); })`; judge blocking by the SERVER player (`sp.isBlocking()`, `Stats.ITEM_USED` for the shield counts real blocks). Measured once green: 27 zombie hits blocked, shield bashes accepted over real packets. (a shield-adding item mod.)

**V92. 🔴 `RenderType.create(..., CompositeState)` → `RenderType.create(name, RenderSetup)` + an immutable
`RenderPipeline` — mechanical, so CONVERT it (`tools/convert-rendertypes.py`).** · **Pattern:** the 1.21 7-argument
`RenderType.create(name, format, mode, size, crumbling, sort, CompositeState.builder()...createCompositeState(outline))`
· **Error:** `cannot find symbol: CompositeState / TRANSLUCENT_TRANSPARENCY / NO_CULL / COLOR_WRITE / LEQUAL_DEPTH_TEST`
and `VertexFormat.Mode`, dozens per file · **Fix:** what a shard SWITCHED (blend, depth test + write, colour mask,
cull, depth bias, vertex format, topology) moved into the pipeline; what is BOUND per draw (textures, lightmap,
overlay, crumbling, sorting, layering, output target, outline) stays on `RenderSetup`. The table is exact and the
tool refuses anything outside it, because two rows are traps: **26.x is reverse-Z**, so `LEQUAL_DEPTH_TEST` is
`CompareOp.GREATER_THAN_OR_EQUAL` (a guess renders nothing), and `POLYGON_OFFSET_LAYERING` became a depth BIAS on
`DepthStencilState` (`-1.0F, -10.0F`), not a layering transform. `NEW_ENTITY` → `ENTITY`; `PrimitiveTopology`
lives in `com.mojang.blaze3d`, not `.vertex`. A custom shader shard has no vanilla meaning, so the port supplies a
state type with `pipeline(name, UnaryOperator<RenderPipeline.Builder>)`; key pipelines by name so a factory method
called per texture does not build one per call. **Measured: 71 of 71 on the first mod tried, output kept in the
author's multi-line layout.** Texture shards' blur/mipmap flags are dropped (26.x samples with the texture's own
sampler) and the tool says so per site.

**V93. 🔴 A custom `ShaderInstance` → a `RenderPipeline` + a std140 uniform BLOCK, and what binds the block is a
two-line mixin — but WHEN you snapshot the values is the part that decides faithfulness.** · **Pattern:** a JSON
core program + GLSL 150 with loose `uniform float X;`, fetched with `getUniform("X").set(...)` in a shard's
`setupRenderState()` · **Error:** `ShaderInstance`, `RegisterShadersEvent`, `Uniform.set` all gone · **Fix:**
measured on 26.2's own sources —
· A RenderType draw (`PreparedRenderType.drawFromBuffer`) binds only `Projection`, `Fog`, `Globals`, `Lighting`
  (via `RenderSystem.bindDefaultUniforms`) and `DynamicTransforms`. No NeoForge hook binds more, so a
  `@WrapOperation` on that `bindDefaultUniforms` call binds the mod's block. GL validation (on in dev) throws
  `Missing uniform` / `Missing sampler` for anything the pipeline declares or the program uses and nobody bound:
  bind EVERY declared sampler, falling back to `Sampler0`'s view.
· 🔴 **Snapshot at `RenderType.prepare()`, not at draw.** 26.x calls `prepare()` when geometry is RECORDED
  (`RenderTypeFeatureRenderer.getOrAddDraw`) and draws at flush. A mod that sets per-entity state around each
  render call (colour keys, UV bounds, masks) must have its block written then — write it with a
  `DynamicUniformStorage`, key the slice by the returned `PreparedRenderType` instance, bind it in the draw
  mixin. ⚠ `DynamicUniformStorage.writeUniform` reuses the last slice when the new value `equals` the last one,
  so the snapshot must COPY the floats (a record over mutable uniform objects serves stale values).
· 🔴 **Which uniforms bind to vanilla is decided by what 1.21 DID, not by the name.** 1.21's
  `ShaderInstance.setDefaultUniforms` ran at draw AFTER the mod's `setupRenderState`, so a mod's own
  `GameTime`/`ScreenSize`/`GlintAlpha` were overwritten and the shader always saw vanilla's — 26.x's `Globals` block
  carries the same values (`(gameTime % 24000 + partial) / 24000`), so those bind to vanilla. `ColorModulator`
  stays the mod's: 26.x's copy in `DynamicTransforms` is always white for a RenderType draw, while 1.21 let a mod
  override it after.
· **GLSL: offline when the shaders are static files** (`tools/convert-core-shaders.py` rewrites them into a
  generated include), **at load when the mod builds shaders at runtime** (preset packs, in-game generators): a
  mixin RETURN on `ShaderManager$CompilationCache.getShaderSource` serves the upgraded text, leaving the author's
  GLSL untouched. 26.x shader ids carry the `core/` prefix (`"ns:x"` in a 1.21 JSON is `ns:core/x`).
· **Register early:** `RegisterRenderPipelinesEvent` fires before resource packs load, and RenderType fields are
  built at class init, so create the shader objects there from the mod's own jar and rebuild them on each reload.

**V92. 🔴 The tool-tier and armour classes are GONE, and the class-move map makes it worse by
renaming `ArmorMaterial` to a different type.** · **Pattern:** `implements Tier`, `Tiers.IRON`,
`extends SwordItem/TieredItem/PickaxeItem/ArmorItem`, `SwordItem.createAttributes(tier, 3, -2.4F)`,
`Holder.direct(new ArmorMaterial(defense, ench, sound, () -> repair, List.of(new ArmorMaterial.Layer(id)), t, kb))`
· **Error:** `cannot find symbol: class Tier / TieredItem / SwordItem / ArmorItem`, and then
`constructor ArmorMaterial cannot be applied` on every material. The second error is the move map's doing:
it matches classes by simple name, so 1.21.1 `item.ArmorMaterial` becomes 26.2 `item.equipment.ArmorMaterial`
(a record with a durability, an `ArmorType` map, a repair TAG and an `EquipmentAsset` key). That is §V44's
blind spot again: same name, unrelated shape. · **Fix:** `tools/convert-gear-tiers.py` re-supplies the
1.21.1 contracts in the mod's own package (`<mod>.compat.gear`), built on the 26.2 components exactly as
`ToolMaterial.applyToolProperties` / `applySwordProperties` and `Item.Properties.humanoidArmor` build them,
and points the mod's imports there. Subclasses, overrides and anonymous `new Tier() {...}` then compile
unchanged. Two details are what make it faithful. **Repair is a DELAYED component**
(`Properties.delayedComponent(REPAIRABLE, ...)`), because 1.21.1 read the repair ingredient lazily and building
it at item construction touches other items before they are bound (§R3). **Armour durability is not set**,
because the 1.21.1 `ArmorItem` constructor did not set it either; the caller did. · ⚠ **`instanceof` is the
half a shim cannot make faithful.** `x instanceof SwordItem` used to match every sword in the game, and against
the shim it matches only the mod's own. An UNBOUND test is widened to `GearChecks.isSword(x)` (tags and
components, so vanilla gear answers too). A BOUND test or a cast is left alone and reported as NARROWED,
because widening the guard in front of a cast hands it a vanilla item and a `ClassCastException`. · **Armour
textures move too:** 26.2 draws worn armour from `assets/<ns>/equipment/<id>.json` and
`textures/entity/equipment/humanoid[_leggings]/`, not `textures/models/armor/<id>_layer_{1,2}.png`; the
converter writes the json and moves the textures for every layer id the code names. · **Refused, named:**
`AxeItem`/`ShovelItem`/`HoeItem` subclasses (still vanilla classes on 26.2, with a
`(ToolMaterial, float, float, Properties)` constructor), `Registries.ARMOR_MATERIAL` (no such registry), and
`getDefaultAttributeModifiers` overrides. **Measured:** −102 errors on a 467-error library port, 0 errors
inside the generated package.

**V93. NeoForge's attachment serializer changed shape, and its bridge keeps every body byte-identical.**
· **Pattern:** `new IAttachmentSerializer<CompoundTag, T>() { T read(IAttachmentHolder h, CompoundTag t,
HolderLookup.Provider p) {...} CompoundTag write(T x, HolderLookup.Provider p) {...} }`, and data classes
`implements INBTSerializable<CompoundTag>` · **Error:** `wrong number of type arguments; required 1`,
`method does not override` on read/write/serializeNBT/deserializeNBT, `cannot find symbol: class
INBTSerializable` · **Fix:** 26.2 is `IAttachmentSerializer<T>`: `T read(IAttachmentHolder, ValueInput)` and
`boolean write(T, ValueOutput)` (false = nothing to save, which is what 1.21.1's `return null` meant).
`tools/convert-attachment-io.py` changes only the headers. `read` opens with
`CompoundTag t = NbtBridge.tag(in)` (`in.read(MapCodec.assumeMapUnsafe(CompoundTag.CODEC))`) and
`HolderLookup.Provider p = in.lookup()`. `write` calls the original body, moved verbatim into a private method,
and hands its tag to `ValueOutput.store(CompoundTag)`, which merges entries at the top level, so the save is
byte-identical (§V31). `INBTSerializable` is re-supplied as the same two-method interface, so `implements`,
`@Override` and bounds like `<T extends INBTSerializable<CompoundTag>>` stay as written. · ⚠ **`write` is
handed no registry lookup on 26.2.** The bridge supplies the running server's; a body that uses its provider is
reported, so the reader knows where the value now comes from. **Measured:** −61 errors on the same library
port.

**V94. 🔴 A class that becomes a RECORD reports its field reads as "has private access", not "cannot find
symbol" — and the fix is the accessor.** · **Pattern:** `instance.enchantment`, `instance.level` on an
`EnchantmentInstance` (a public-final-field class on 1.21.1, a record on 26.2) · **Error:**
`enchantment has private access in EnchantmentInstance`. No `symbol:`/`location:` lines follow it, so a
tool keyed on `cannot find symbol` never sees it · **Fix:** the record's accessor has the field's name:
`instance.enchantment()`. A text rule for `.level` would also hit every `this.level` in the mod, so
`tools/fix-missing-members.py` now reads this error form too. javac names the owner AND puts its caret on the
exact `.`, so a row `EnchantmentInstance  level  level()` rewrites that site and no other. It skips a call
that already has parentheses and an assignment target. **Measured:** 30 sites in one library, all fixed in
one pass.

**V96. The general form of §V76: an override whose hook SIGNATURE changed keeps its body in a private 1.21.1-shaped
copy, and a table says how to adapt each hook.** · **Pattern:** the item hooks every content mod overrides —
measured over our ports and the 13-mod corpus: ~350 `appendHoverText`, 48 `releaseUsing`, 39 `inventoryTick`,
33 `hurtEnemy`, nearly all in one 1.21.1 shape each · **Error:** `method does not override or implement a method
from a supertype`, the largest single bucket in every 26.2 baseline · **Fix:** `tools/convert-override-signatures.py`.
The new override adapts the arguments at the boundary and calls `ported$<hook>(<old args>)`, whose body is the
original, untouched. Only `super.<hook>(...)` inside it is rewritten to the 26.2 call. The rows, read off both jars:
`appendHoverText` takes `TooltipDisplay` + `Consumer<Component>` in place of the list (the copy fills a list; the
override forwards it in order); `releaseUsing` returns boolean (false, vanilla's default); `hurtEnemy` returns void
(`return super.hurtEnemy(..)` becomes `super.hurtEnemy(..); return true`); `inventoryTick` takes
`(ItemStack, ServerLevel, Entity, EquipmentSlot)` — `isSelected` becomes `slot == MAINHAND`, and a body reading the
old slot INDEX or branching on `isClientSide` is NOTED, because 26.2 passes no index and ticks on the server only.
A `super` call used as a value where the new one is void is REFUSED. **Adding a hook is a table row**, which is the
point: a §V entry that says "this hook changed shape" should land as a row here, not as per-port judgement.
**Measured:** 7 overrides in one library, −14 errors, 0 at the rewritten sites.

**V95. Small 26.2 removals found by zero-model baselines (cluster).** · **Pattern:** the 1.21.1 calls listed
below, each a one-line shape · **Error:** `cannot find symbol` on the old member (`putUUID`, `getShort`,
`getCommandSenderWorld`, `ResourceKey::location`, `FastColor`, `LazyLoadedValue`, `Ingredient.of(ItemStack)`), or
`incompatible types: int cannot be converted to short` · **Fix:** each is a row in the 26.2 rename table or the
member table, checked against the 26.2 jar:
· `CompoundTag/ValueOutput.putUUID(k, v)` → `store(k, UUIDUtil.CODEC, v)`; `getUUID(k)` →
`read(k, UUIDUtil.CODEC).orElseThrow()`; `hasUUID(k)` → `read(...).isPresent()` (§V12; `UUIDUtil.CODEC`
writes the same int array, so saves read back). These are member rows rather than text rows, so the no-arg
`Entity.getUUID()` is never touched.
· `ValueInput.getShort(k)` → `(short) getShortOr(k, (short) 0)`: it returns `int` on 26.2.
`ValueInput.contains(k)` → `keySet().contains(k)`.
· `Entity.getCommandSenderWorld()` → `level()` (§V71), per owner type javac names.
· `ResourceKey::location` as a METHOD REFERENCE → `ResourceKey::identifier`. The §V17 row matches only a call.
· `FastColor.ARGB32.color/colorFromFloat/...` → `ARGB.*`, same (a, r, g, b) order and packing (`ABGR32`
is not covered).
· `LazyLoadedValue` → `java.util.function.Supplier` built by Guava's `Suppliers.memoize(...)` (computes once,
on first `get()`).
· `Ingredient.of(new ItemStack(x))` → `Ingredient.of(x)`: the `ItemStack` overload is gone.

**V92. Four 26.2 shapes the fork and jar routes now port with no model — reload listeners, weights, client hooks,
and MultiBufferSource — each by generating the removed 1.21.1 contract into the mod and repointing names, so no
method body is edited.** · **Pattern:** `event.addListener(X)` on `AddServerReloadListenersEvent`; a class
extending `SimpleJsonResourceReloadListener` with `super(gson, dir)`; `implements WeightedEntry` /
`Weight.of(n)` / `WeightedRandom.getRandomItem(r, list)`; `extends TextureSheetParticle`; an `initializeClient`
override on an Item/Block/MobEffect; `ItemProperties.register(...)`; a `MultiBufferSource` parameter or
`renderBuffers().bufferSource()` · **Error:** `method addListener in class SortedReloadListenerEvent cannot be
applied`, `cannot find symbol: class Weight / WeightedEntry / TextureSheetParticle / ItemProperties /
ItemPropertyFunction / MultiBufferSource`, `cannot find symbol: variable xd/yd/zd/lifetime` (the particle
superclass cascade), `method does not override` on `initializeClient` · **Fix:** run by the shared mechanical
stage (`tools/mechanical-hop.py`), in this order:
· `tools/convert-reload-weighted.py` (§V36): the listener gains the `Identifier` 26.2 requires, named from `X`
(unique across the tree); a JSON listener asks for the RAW map (`super(ExtraCodecs.JSON,
FileToIdConverter.json(dir))`), so `apply` and its defensive per-file parsing stay as written; `Weight` /
`WeightedEntry` are generated with their 1.21.1 contract and every `WeightedRandom` call gains
`e -> e.getWeight().asInt()`.
· `tools/convert-client-hooks.py` (§V51, §V52, §S4b): a generated `TextureSheetParticle` keeps both 1.21.1
constructors over `SingleQuadParticle` (the sprite starts null and is picked later, exactly as before); a
constant `getRenderType()` becomes `getLayer()`; `getLightColor` → `getLightCoords`; `createParticle` gains its
`RandomSource`. `initializeClient` overrides keep their bodies and a generated `ClientExtensionHooks` registers
them from `RegisterClientExtensionsEvent`, which exists on both versions (a super call to the removed vanilla
method goes; one to the mod's own superclass stays). `ItemProperties` becomes a LOGGED no-op that names the
`assets/<ns>/items/<id>.json` to write instead, and its `PROPERTIES` map answers empty rather than null.
· `tools/convert-buffer-seam.py` (§V54, §V66, X52): the recording `Buffers` seam four hand ports wrote, plus
vanilla's own `MultiBufferSource` shape; each render-hook override that took one is listed as NEEDS SUBMIT.
· **Measured, zero-model, on the 26.2 hop of real ports:** a mob library 232 → 159 (−31%: −23 reload/weights,
−36 client hooks, −14 seam); a boss-effects library 1431 → 1378 (seam only: it has no listeners, particles or
item properties, and the other two correctly do nothing).
· **What none of them claims:** a render hook that took a `MultiBufferSource` became `submit(...)` over a
render STATE, not the entity, and that port is per class; a property override is a client item model; neither is
expressible as a rewrite, so both are named rather than half-done.

**V93. 🔴 No 26.2 port in this project binds a mod's OWN uniform block — so the block `convert-core-shaders`
lays out has no proven runtime path yet, and a "Java shader converter" built on it would compile and draw
nothing.** · **Pattern:** a mod with Java-driven core shaders: `ShaderInstance` fields,
`getUniform("X").set(...)` per frame, `RegisterShadersEvent`, a custom `ShaderStateShard` in its render types
(one measured library: 12 effect classes, ~150 `getUniform` calls) · **Error:** `cannot find symbol: class
ShaderInstance / RegisterShadersEvent / Uniform`, and `convert-rendertypes` refusing the custom
`ShaderStateShard` for want of a state type · **Symptom, if forced to compile with stand-ins:** every gate
green and every effect invisible — no log line, because nothing failed. · **Why, read off the jar:** a
`RenderType` is a name plus a `RenderSetup` (textures, texture transform) over a `RenderPipeline`; the draw path
binds vanilla's `DynamicTransforms`/`Projection`/`Globals` and nothing else, and neither `RenderSetup` nor
`RenderType` has a hook to bind another uniform buffer. Swept the shipped 26.2 overlays: the only `setUniform`
is one port writing vanilla's `DynamicTransforms` on its own render pass. · **Fix (not yet automated):** port
ONE such effect by hand — drawn through its own `RenderPass` with `setUniform(<block>, slice)` (or a NeoForge
hook if one exists) — prove it with a Gate C photograph, and only then generalise it into a converter. The GLSL
half (`convert-core-shaders`) and the RenderType half given a state type (`convert-rendertypes`) are already
automated; the piece in between has never been done here.

## W. ONE SOURCE TREE, TWO MINECRAFT VERSIONS — the shape that makes an era jump survivable
> **Axis:** build architecture. Everything above ports a mod *from* A *to* B and leaves A behind.
> This is what to do when the mod must keep running on **both** — which is the normal case for a
> mod someone actually plays, because the new Minecraft lands months before the mod pack does.
> Built and measured on a builder mod (458 files, 1.21.1 + 26.2). **Scaffold + scripts:
> `templates/multi-version/`** — copy them rather than rewriting the pipeline per mod.

**W1. The two kinds of difference are not the same kind of problem, and conflating them is what
produces per-version `#if`-style rot.**
· **MECHANICAL** — a type or package whose *name* changed while its API did not
  (`ResourceLocation`→`Identifier`, `monster.Zombie`→`monster.zombie.Zombie`). Java has no type
  alias, so one shared `.java` cannot name both. This is **data**: a rename table per target.
· **REAL** — a changed signature, a split method, a rewritten render call. This is **code**: an
  overlay file per target that REPLACES the shared file wholesale.
Everything else — measured at **86% of the builder mod's imports** (V8) — is written once and shared.
**The pipeline:** shared tree → apply this target's rename table → let this target's overlay replace
whole files → compile. `build/generated/sources/<overlay>/java` becomes the only `srcDirs`.

**W2. 🔴 Run the CANONICAL version through the same pipeline too.**
The tempting shortcut is "1.21.1 compiles the shared tree directly; only 26.2 gets preprocessed".
Then the mechanism is exercised only by the target nobody is running today, and it rots. Both
targets go through `prepare-sources.py` identically — the canonical one starting with an **empty**
rename table, so its prepared tree is byte-identical to `src/main/java` and a two-line `diff -r`
turns the whole preprocessor into something a normal build proves every time.
⚠️ **That invariant weakens the moment the first compat helper lands (W5)** and it is worth being
precise about rather than quietly dropping: the canonical table stops being empty, because the
helper's call sites must be rewritten for *both* targets. The honest form of the check is then
**"the canonical target's diff against `src/main/java` contains nothing but compat-helper
routing"** — which is still one grep, and still fails loudly if a 26.x-shaped rule ever leaks into
the canonical table. The builder mod: 101 rewrites, all of them `Msg.tell` / `Nbt.getUuid` / `Nbt.putUuid`.

**W3. Version data belongs in a file per target, not in `if (mc == …)` in `build.gradle`.**
`versions/<target>.properties` carries `minecraft_version`, `minecraft_version_range`, `neo_version`,
`java_version`, the library versions, `overlay`, and any per-target switch (how GeckoLib is acquired,
whether the GameTest adapter runs). The build loads the file named by `-Pmc=<target>` into `ext` and
**never branches on the version itself** — adding a third target is a new properties file, a rename
table and an overlay directory, with no build logic touched. Make the missing-file error list the
known targets; that is the whole discoverability story.
Two consequences worth wiring at the same time: `archivesName = "<mod>-mc<version>"` so the two jars
can coexist in one `mods/` staging dir, and a **version-sensitive deploy** keyed on
`MINECRAFT_MODS_DIR_<version with dots as underscores>` so `deployToMods` cannot put a 26.2 jar in a
1.21.1 instance.

**W4. `java.toolchain` must come from the target, and both JDKs must be present.** MC 26.x needs
**Java 25** and 1.21.1 needs **21** (V1), so a multi-version build is genuinely a two-JDK machine.
Read `java_version` out of the properties file; do not pin it in the build.

**W5. A per-version compat helper is the escape hatch, and it must be used by BOTH versions.**
When a change cannot be a rename (V16's `displayClientMessage` boolean picking a method; V12's UUID
tag accessors), put a tiny helper in `<pkg>.compat` with one implementation per overlay, and rewrite
the call sites **for every target including the canonical one** — the old target's helper simply
forwards to vanilla. If the old target keeps calling vanilla directly, the shared tree drifts back
apart the first time someone edits it, and nothing complains until the next era jump.

**W5b. 🔴 A pair over an OVERRIDE, written as a pair over a CALL, recurses forever — and it dies on
exactly one target.** · **Pattern:** the natural way to route an override through W5 —
`public AABB getBoundingBoxForCulling() { return Entities.cullingBox(this).inflate(3.0); }`, whose
1.21.1 half is `return entity.getBoundingBoxForCulling();` · **Symptom:** `StackOverflowError` on
the CANONICAL target only, because the 26.2 half answers with a different vanilla method and never
re-enters. It compiles on both, so the burn-down cannot see it (§X14) and only the §X15 control run
of a real client finds it — measured, in Gate C's `spawn` phase, on a mod whose 26.2 client had
already gone green through all four phases. · **Why it happens rather than being an obvious slip:**
the honest 1.21.1 answer is `super.getBoundingBoxForCulling()` (`LivingEntity` overrides it for
anything wearing a dragon head), and **a static helper cannot make a `super` call** — so the only
expression available to a call-shaped pair is the one that recurses. The fix is §V41's: a per-target
BASE CLASS the entity extends, carrying that target's override and delegating to one shared question
(here `cullingInflation()`), which also keeps the value stated once for the target that no longer has
the hook. · **The test before you write the pair:** *is the thing I am routing an override?* If yes,
ask whether the 1.21.1 half would need `super` — if it would, a static helper cannot express it and
a base class is the only correct shape. · **Wired, not written down (§S2):**
`tools/audit-compat-recursion.py` indexes every pair method whose body calls a method on its own
first parameter and reports any shared-source override that hands `this` to one that calls it back.
Target-independent (it reads every `src/mc*` half), a `check` task, in `templates/multi-version/`,
and A/B'd BOTH ways — a `--self-check` that plants the offender and then the fixed form, plus the
real pre-fix file, which it names. It reads `checked 110 pair method(s) … 0 finding(s)`, so the
count says the scope is real rather than only that nothing was found (§X27).


**W5c. 🔴 A PAIR THAT ADAPTS A FUNCTIONAL ARGUMENT MUST PRESERVE `null` — because `null` is a
VALUE there, and a wrapper converts it into a non-null object that crashes on first use.** ·
**Pattern:** the ordinary §V40 pair, converting 1.21.1's `Predicate<LivingEntity>` into 26.2's
two-argument `TargetingConditions.Selector` — `return (candidate, level) -> filter.test(candidate);`
· **Symptom:** compiles on both targets, and on 26.2 crashes the SERVER THREAD the first time any
mob looks for a target: `Ticking entity` ←
`NullPointerException: Cannot invoke "Predicate.test(Object)" because "filter" is null`, from
inside the pair · **Why it is not the caller's bug:** `null` means *"no filter"* to both versions,
and vanilla says so — 26.2's `TargetingConditions.test` null-guards the selector before calling it
(`getfield selector` / `ifnull`, read out of the bytecode), and the 1.21.1 half returns the
predicate unchanged so a null stays a null. The wrapper is the only thing that changes the meaning:
it satisfies vanilla's guard with a perfectly non-null `Selector` and moves the dereference inside.
· **Fix:** `return filter == null ? null : (candidate, level) -> filter.test(candidate);`
· **The general rule, and it applies to EVERY adapting pair, not just this one:** when a pair wraps
a functional argument, ask what the CALLEE does with a null one. If null is legal there — and for
an optional predicate, comparator, listener or callback it usually is — the pair has to pass it
through. A pair that merely *forwards* (the 1.21.1 half here) is null-safe by construction; it is
the half that WRAPS that silently changes the contract, and only on the target that wraps.
· **Swept, which is the part worth copying.** One regex over every `src/mc*` pair — a
`public static` returning a lambda that calls a method on one of its own parameters, with no
`== null` anywhere in the method — found **3 hits across 11 ports and no noise**: this one live, and
the same shape latent in a large boss mod's `ModTargeting.selector` and a ~750-file boss mod's `Targeting.sel`,
where whether it fires depends on whether any caller ever passes null. Both were fixed rather than
argued about: preserving null costs nothing and cannot be wrong, while proving no path passes null
is a claim about every call site.
· **Found by Gate C's `spawn` phase.** Gate B spawns entities and ticks them, and did not catch it —
a filtered `NearestAttackableTargetGoal` only reaches its selector when there is a player in range
to scan for, which a GameTest plot has not got.


**W6. Keep the rename table honest about what it is.** Every row is a claim that the API is
*identical* either side. Anything that is a judgement (V13's `sidedSuccess`) gets an explicit
comment saying so, because the compile cannot distinguish a faithful mapping from a plausible one,
and six months later nobody remembers which rows were which.

**W7. 🔴 MEASURE THE DUPLICATION BEFORE YOU WRITE RULES — the cheapest errors to delete are the
ones that are the same error N times.** The builder mod's 25 Gate-C client tests each carried
their own copy of the same seven-line world-creation block, and 26.x changes three separate
things inside it (`LevelSettings` lost its `GameRules` argument and folded hardcore+difficulty
into a `DifficultySettings` record; `GameRules` lost its no-arg constructor; `createFreshLevel`
takes a `Function<HolderLookup.Provider, …>` where 1.21.1 takes `Function<RegistryAccess, …>`).
That is **50 compile errors and 50 hand-edits for one API change**, and no rename rule can
express any of the three. Extracting one compat pair deleted 342 lines, closed 63 errors, and is
the right code on the old version too.

So before reaching for the rename table, sort the error locations by the *shape* of the line
they sit on, not just by package. A family of identical call sites is an extraction; only a
family of genuinely different ones is a rule. And extract it as a **pair used by both targets**
(W5) — if the canonical version keeps calling vanilla directly, the next person adds a test by
copying an existing one and the duplication grows straight back, which is how it reached 25.

**W8. An OVERLAY file is NOT run through the rename table — by design, and it will catch you once.**
The pipeline is *shared tree → renames → overlay REPLACES whole files*, so an overlay is expected to
be written in the TARGET's dialect already. That is right, and it means the natural way to create one
— copy the 1.21.1 file and edit the few lines that differ — leaves every *other* version-specific
name untouched. Measured: an overlay derived by `sed` from its 1.21.1 original still said
`ResourceLocation`, and the error surfaced in the overlay rather than in the shared tree, which is
the one place a reader is not expecting a rename to be missing. When you derive an overlay from the
canonical file, apply the target's renames to it yourself.

**W8b. ⚠ AUGMENT to W8 — a rename row DIES when its call sites move into an overlay, and the
dead-rule report is the only thing that says so.** W8 records that an overlay is not run through
the rename table. The consequence for the TABLE is easy to miss: rows serving code that later
becomes an overlay stop matching anything, silently, and the row still reads as correct.
· Measured: `FastColor.ARGB32` → `net.minecraft.util.ARGB` was a right mapping, checked V34's way
(both `color(int,int,int,int)` bodies `javap`'d, both packing `a<<24 | r<<16 | g<<8 | b`), and it
went dead the moment the render layers became overlays — every call site now sits in `src/mc21`,
which the table never sees.
· **Delete such rows, and prove the deletion was a no-op the X11/X31 way**: `renameRewrites`
unchanged (127 either side here) and `diff -r` clean on the prepared tree. Leave a comment saying
the mapping was verified and where it went, so the next person who moves that code back into
shared source restores the row rather than re-deriving it — the mapping is the expensive part, the
row is not.
· **The general shape:** the overlay mechanism and the rename table are coupled in exactly one
direction that nothing checks. Every file you promote to an overlay is a potential dead rule, so
read the dead-rule line on the build right after promoting one, not a week later.

**W9. ModDevGradle runs unit tests from `build/minecraft-junit`, where NeoGradle ran them from the
project directory.** · **Pattern:** any test that locates its own project via
`System.getProperty("user.dir")` — which is the shape of every `MixinConfigIntegrityTest` this repo's
template has ever produced · **Runtime:** `NoSuchFileException:
…/build/minecraft-junit/src/main/resources/<modid>.mixins.json`, i.e. a path that has never existed,
naming a file that is present · **Fix:** hand the test its project directory explicitly
(`systemProperty '<modid>.projectDir', projectDir`) rather than depending on a working directory a
toolchain is free to change. This hits **every** mod in this repo when it moves to MDG (V2), because
they all carry the same template test.

**W10. Anything that takes a target as a parameter must FORWARD it, not just consume it.**
`tools/build-releases.sh` honoured `MC=26.2` for the *output folder* and never passed `-Pmc` to the
build, so it would have filled `releases/26.2/` with 1.21.1 jars — a wrong artifact under a right
name, which no downstream consumer can detect. That is the same omission §X15 records for the Gate-C
launcher that read `versions/1.21.1.properties` and ignored the build's own `-Pmc`. It now forwards
the flag when the port has a `versions/<mc>.properties`, and **SKIPS with a reason** otherwise —
a single-target port silently producing a jar for the wrong version is the failure being removed.

**W10b. 🔴 W10 AGAIN, one step further on: forwarding the target to the BUILD is not enough if
the thing that picks the ARTIFACT still ignores it.** `tools/build-releases.sh` was fixed to pass
`-Pmc=$MC` (W10) and then chose which jar to stage with `ls -S … | head -1` — **by SIZE**. A
multi-version workspace's `build/libs/` accumulates one jar per target it has ever built, so
`MC=26.2` staged a config library's **1.21.1** jar into `releases/26.2/`.
· **The bias is the nasty part:** the 26.2 jar is SMALLER precisely because that target drops
files, so a size heuristic systematically prefers the OLD version — the failure gets likelier the
more a port had to drop. The space mod and a fluid/energy transfer library escaped only because their 26.2 jars happen to be
the larger ones.
· **Fix:** select by the name the multi-version build guarantees (`*-mc<MC>-*.jar`, from W3's
per-target `archivesName`), and FAIL loudly if it is absent rather than falling back to a
heuristic. Keep the size fallback only for a single-target port, which has one jar anyway.
· **The check that catches the whole family in one line**, and it is worth running after any
staging change because the filename is the only thing a downstream consumer looks at:
`unzip -p <jar> META-INF/neoforge.mods.toml | grep -A3 'modId="minecraft"' | grep versionRange`
— ask the ARTIFACT what it is, never the path it sits at.


**W12. 🔴 A registration nothing calls is invisible to every gate that does not JOIN A WORLD —
and on 26.x that is most of them.** V47 records that a mod's `GameTestInstance` needs a real
`MapCodec` registered in `TEST_INSTANCE_TYPE`, because the registry is SYNCED. The generated
adapter had the codec, had a `DeferredRegister` for it, and had a `public static void
register(IEventBus)` with a comment saying to call it from the mod constructor. **Nothing called
it.** Java does not type-check "somebody invokes this", so:

| gate | verdict | why it could not see it |
|---|---|---|
| A — unit | PASS | no registries |
| B — GameTest | `All 16 required tests passed` | a dedicated server never syncs registries to a client |
| C — `launch` | PASS | the title screen never joins a world |
| C — `spawn`/`battle`/`gauntlet` | **hangs on the loading screen forever** | the join is the first thing that packs the registry |

The failure is `IllegalArgumentException: Failed to serialize ResourceKey[minecraft:test_instance /
<modid>:…]: Unregistered holder`, thrown handling `ServerboundSelectKnownPacks` — so the client sits
on `LevelLoadingScreen` at high CPU and the run times out. **It reads exactly like slow worldgen**,
which is what it was first written up as here; a superflat world made no difference, and the log
line naming the registry is the only thing that settles it. Every phase past `launch` is blocked by
it, which is why a `launch`-only Gate C can be green for weeks over a mod a player cannot enter a
world with.

· **The fix is to make the registration unforgettable rather than documented**: register the codec
from a `@SubscribeEvent RegisterEvent` listener inside the generated registrar (FML subscribes it
for you) instead of from a `DeferredRegister` a constructor has to attach. A generator that emits
"now go and call this" emits a bug with instructions.
· **The general shape is §S4's** — javac checks names and types, never whether anything calls you —
and the reason it survived is X15's: the gate that would have caught it had never been run on that
target.

**W13. 🔴 A decompiled `neoforge.mods.toml` carries LITERAL version ranges, and on a
multi-version port that is a runtime load failure on the target you are not looking at.** ·
**Pattern:** the scaffold's `processResources` expands `${minecraft_version_range}` and friends
into the toml — but a toml recovered from a shipped jar has the literals the author built with
(`versionRange="[1.21,1.21.1)"`), and there is nothing to expand. · **Runtime:**
`Mod <id> requires minecraft 1.21 or above, and below 1.21.1` → `Missing or unsupported mandatory
dependencies` → FML refuses to start. Gate A and the compile are perfectly green: the file is
valid TOML saying something false. · **Fix:** put the placeholders back
(`loaderVersion`, the `[[mods]] version`, the `neoforge` and `minecraft` `versionRange`s, and the
mixin `config` name), which is a scaffold step a decompile-first port skips because the file
already exists. · **Why it hides:** a SINGLE-target port with literals is simply correct, so this
is invisible until the day a second target exists — and then it fails only on the new one, which
is the target whose gates you are least likely to have run yet (§X15). Check the built jar rather
than the source: `unzip -p build/libs/<jar> META-INF/neoforge.mods.toml | grep versionRange`.
· ⚠ **AUGMENT: the literal can also be OPEN-ENDED, and then it is a lie on the NEW target rather
than a refusal.** A small mob-variant mod's (~30 files) decompiled toml says `minecraft versionRange="[1.21.1,)"`, so
upstream claimed to load on every later Minecraft, 26.2 included. FML would therefore accept the
1.21.1 jar on 26.2 and let it die at mixin apply or on the first missing class, instead of
refusing it up front with a readable message. The fix is the same (template it), but the
failure moves from "the new target refuses to start" to "the new target starts and crashes
somewhere that names no dependency". A range with no upper bound is worth a second look on
every decompile.


**W14. 🔴 A dropped file is a mixin config entry too — and the config is the ONE resource a
§W tree cannot leave shared.** `versions/<t>.drops.txt` keeps a file out of that target's
compile (§V52's recorded drop). If the dropped file is a mixin, `<modid>.mixins.json` still
names it, and **Mixin fails at APPLY on a class that does not exist** — the client dies at
start on the target whose gates get run least (§X15). Gate B never sees it (`client` mixins
do not load on a dedicated server) and Gate A's integrity test does not either, because that
test reads `src/main/resources`.
· **Do not hand-write the overlay copy.** It is a second 60-entry list that must be edited
every time a mixin is added, dropped or renamed, and nothing would ever say it had drifted —
§S2's shape exactly. DERIVE it: `tools/gen-mixin-config.py --ns <modid>` writes
`src/<overlay>/resources/<modid>.mixins.json` as the shared config minus that target's drops,
and `--check` is a `check` task, so the drops list stays the single source of truth. A port
whose drop list grows later needs no second edit.
· **Two rules that are easy to get wrong by hand.** A target that drops NO mixins must have NO
overlay copy — an identical second list silently SHADOWS the shared one, so a later edit to the
shared config never reaches that target. And a duplicate entry (this mod's shared config listed
`MixinBee` twice) is harmless where a human wrote it and reads as deliberate in a generated
file; drop it in both.
· ⚠ **The integrity test has to move with it.** Once a target drops a mixin, "the shared
config" and "the config that ships" are different files, so a test pinned to
`src/main/resources` fails on a perfectly good 26.2 build while asserting nothing about what
the game loads. Pass the overlay name to the test JVM beside the project directory (§W9) and
resolve overlay-first, in the same order `build.gradle` adds the resource dirs. *(And check
that the projectDir property is actually READ — here it was passed and the test still used
`user.dir`, which is the W9 bug still live behind its own fix.)*

**W10c. 🔴 RENAMING a port's jar to the multi-version scheme LEAVES THE OLD ONE, and downstream
copies both.** W10/W10b are about a staging script that ignores the target. This is what happens
when it finally honours it: `archivesName = "<mod>-mc<version>"` (§W3) means a port that was
staged BEFORE it went multi-version now has TWO jars in `releases/<mc>/` — the legacy
`examplelib-1.2.0.jar` and the new `examplelib-mc1.21.1-1.2.0.jar` — both declaring the same
`modId`.
· **That is not cosmetic:** a consuming mod's `tools/fetch-testmods.sh` copies everything in the
folder and skips only what the lockfile pins, so the test-mod directory ends up with two jars
claiming one mod and **FML refuses to start**. The script's own comment records that failure for
a config library; the rename manufactures it again from the other direction, and the port that causes
it is green on every gate, because nothing in the port can see its own release folder.
· **Fix:** `git rm` the superseded jar and regenerate `SHA1SUMS` in the same commit as the first
multi-version staging. The ports that were multi-version from the start never had a legacy jar, so
this bites exactly the ports that existed first — check `ls releases/<mc>/` for two entries sharing
a mod name, not for the one you just added.
· **The general shape, and it is W10b's:** the filename is the only thing a downstream consumer
looks at, so ask the ARTIFACTS what they are —
`for j in releases/<mc>/*.jar; do unzip -p "$j" META-INF/neoforge.mods.toml | grep -m1 modId; done
| sort | uniq -d` names every collision in one line.
· ⚠ **AUGMENT — the trap is LATENT, not live, which is why "check `ls releases/<mc>/`" kept not
happening.** The collision does not exist while the legacy jar sits there alone; it is *created by
the next staging run*, when `archivesName` mints the `-mc<ver>-` name beside it. So a repo can look
fine for weeks and then break the moment somebody stages an unrelated port. Measured: re-staging
with the space mod and a fluid/energy transfer library still under their legacy 1.21.1 names produced exactly that pair.
· **So it is WIRED into `tools/build-releases.sh` rather than written down again** (§S2 — a step you
must remember is not a control): staging now asks every jar in the output folder what modId it
declares and **exits 1** naming both offenders. A/B'd both ways — planted collision `exit=1`, clean
`exit=0`. The fix for a hit is still `git rm` the superseded jar and regenerate `SHA1SUMS` in the
same commit.
· **And beware measuring the collision you just made.** The first reading here was taken against a
working tree the staging run had already written into, so it reported the space mod and the transfer library as
*live* collisions in the repo. `git ls-tree HEAD releases/<mc>/` said otherwise: HEAD had exactly one
jar for each. A finding about the repository has to be read from the repository, not from a tree your
own tooling has just modified — X8b's stale-tree rule pointed at artifacts instead of source.

**W10d. ⚠ AUGMENT to W10: a script that uses the target for its OWN decisions has not forwarded it.**
The Gate B wrapper in a ~840-file content mod's 1.21.4 port read `MC=1.21.4` to pick its label and to refuse the
Create tiers (Create has no 1.21.4 build), and never passed `-Pmc` to Gradle. The run printed
`1.21.4 has no Create` above a log that said `Minecraft 1.21.1`, and a test written to go red on 1.21.4
went green, because it ran on the target where the bug does not exist. · **Two checks, and the second
is the one that generalises.** Forward the target to every child that builds or boots anything. Then
ask the RUN what it was: the build prints one line naming its target at configuration, and the wrapper
compares that against the target it was given and exits as a rig fault on a mismatch. The label is the
wrapper's opinion; the log line is the build's fact. · **A test seen green where you expected red is
the cheap alarm.** Before believing that a fix was unnecessary, check that the run you read happened
on the target you meant (§W11's per-target run directory name is one command away:
`ls run/ | grep <target>`).

· ⚠ **AUGMENT — FIXING ONE SCRIPT IS FINDING ONE SCRIPT (the content mod's 1.21.4 port, T11.6–T11.8).** After
`gate-b.sh` was fixed, five more scripts had the identical defect, and each was found separately,
days apart, by a run that looked fine: the image builder (`deploy/build.sh` built and baked the
default jar whatever `MC` said), the e2e harness (all three arms), the **upgrade rehearsal**
(`rehearse-world-jar.sh MC=1.21.4 <1.21.1 world>` would have "rehearsed" 1.21.1 → 1.21.1 and
passed), the client-test launcher (reachable only through `GRADLE_ARGS=-Pmc=…`, a spelling nobody
guesses) and the stock-panel gate. **Sweep the first time:** `catalog-scans.md`'s §W10d/W15 grep
lists every script that runs Gradle or globs `build/libs/*.jar` without naming a target — a
CANDIDATE list (38 on that repo, most of them legitimately default-only), read once, not a fix
list. Put the three moves in ONE sourced helper (`mc-target.sh`: the default read from the build
file, the libs dir, the `-Pmc` argument) so the next script cannot re-derive the path and get it
wrong, and make each script ask the RUN — the jar's `neoforge.mods.toml` range, the server's
`Starting minecraft server version` line — rather than trust the variable it set.

**W11. One run directory per TARGET, or the second target's evidence overwrites the first's.**
`gameDirectory = run/<runName>` is the scaffold default and it is shared across targets, which is
three latent bugs at once: a 26.2 world sitting where 1.21.1 will read it, one `options.txt`
deciding which target meets vanilla's first-run prompt (X28's asymmetry, which is exactly how the
same commit hangs on one target and passes on the other), and — the moment Gate C starts
photographing — the second run silently replacing the first run's frames, which is the one thing a
cross-version comparison cannot survive. `run/<runName>-<mcTarget>` costs one world regeneration
and removes all three.
· ⚠ **AUGMENT — the build honouring W11 is not the rig honouring it.** Any script that WRITES into a
run directory before the build launches (a `server.properties` with a private port, an
`options.txt`, a whitelist) has to compute the same per-target name the build uses, or it writes
into the directory the build no longer reads. Measured on the content mod's 1.21.4 port: the
stock-join gate's server helper wrote `server-port=25681` into `run/server-stockjoin`, the build ran
from `run/server-stockjoin-mc1.21.4`, the server came up on vanilla's default 25565, and the gate
reported **"no join — deployment 1 is closed"** over a server that was fine. · **Two fixes, and the
second is the general one.** Derive the name from the build's own default-target line rather than
retyping it. Then ask the RUN whether it took the setting: the helper now reads
`Starting Minecraft server on *:<port>` out of the server log and exits as a rig fault on a
mismatch. A config file you wrote is your intention; the log line is what happened.

**W15. 🔴 A COMPANION JAR BUILT FOR ANOTHER MINECRAFT IS A BOOT CRASH THAT READS AS YOUR PORT'S.**
A dev rig that attaches companion mods automatically (an integration target, a library, the
content mod's own companion) attaches them to EVERY target unless told otherwise, and a companion has
exactly one Minecraft. Measured on the content mod's 1.21.4 port: Gate C's launcher attached Create
(1.21.1 only) to the 1.21.4 client, which died before the title screen with
`Error during pre-loading phase: Mod ponder requires minecraft 1.21.1` — a jar-in-jar dependency's
name, not Create's, in a log whose last lines were about THIS mod. It reads as "the 1.21.4 client is
broken". · **Fix:** ask each companion jar which Minecraft it is for (its `neoforge.mods.toml`
`minecraft` versionRange — the same read an installer should trust) and DROP a mismatch **by name**,
out loud, so the agent or test is told the mod is missing — which is the truth on that target — and
so the run's report says what it did not have. Never "fall back" to the other target's jar. The
Gate B wrapper already refused the Create TIERS for the new target (W10d); the client launcher, a
different script, had its own copy of the resolver and no such check. **Same rule as W10d: a guard
lives in one script, the resolver lives in several.**

**W16. 🔴 MOVING A VERSION PROPERTY INTO PER-TARGET FILES SILENTLY REMOVES IT FROM THE DEPENDENCY BOT.**
§W's `versions/<t>.properties` takes `neo_version` out of `gradle.properties`, and Renovate's Gradle
manager resolves `implementation "…:${neo_version}"` ONLY from `gradle.properties` — so from the
commit that split the build, NeoForge (and any `-${minecraft_version}` artifact name, e.g. GeckoLib)
simply VANISHED from the extract: no warning, no error, a green job, no more update PRs. Measured with
`renovate --platform=local --dry-run=extract` (pinned 41.173.1) on the tree just before and just
after the split: `net.neoforged:neoforge 21.1.228` present, then absent. · **Fix:** a `regex`
custom manager over `versions/[^/]+\.properties` (`minecraft_version=(?<depName>…)[\s\S]*?neo_version=(?<currentValue>…)`,
`packageName` the real coordinate, `depName` = `neoforge-mc<target>`) — AND over every other place
the same pin is copied (an installer's version table), under the SAME depName, so one PR edits both
and a check that holds them equal stays green. `allowedVersions` per target holds each to its own
NeoForge line (21.1.x / 21.4.x — moving a line is a new target, not an update), and the rule must sit
AFTER any "automerge every custom.regex minor/patch" rule, because Renovate's later rule wins.
`--dry-run=lookup` then shows the proposals: measured 1.21.1 → 21.1.251, 1.21.4 → none (21.4.157 is
that line's last). **Re-run the extract after ANY build-file restructure** — the bot reports what it
found, never what it stopped finding.

**W17. 🔴 FLIPPING THE DEFAULT TARGET LEAVES `build/` FULL OF THE OLD TARGET'S CLASSES — A RED GATE A
OVER CORRECT SOURCE.** Under §W the default target builds into `build/` and every other target into
`build-mc<t>/`. Flip the default and `build/` still holds the old default's classes, and Gradle's
up-to-date checks judge the new target's inputs against them. Measured on the content mod's flip
(1.21.1 → 1.21.4): the first Gate A on the new default went red with **3 failures** —
`NoSuchMethodError` from classes compiled against 1.21.1 and an `IOException` from a resource the
new build had not re-processed — on source that was correct and compiled green in a clean clone. It
reads as a port regression and it is the build directory. · **Fix:** STAMP the directory. Write
`build/.mc-target` on every configuration; when the stamp names another target, delete that
directory's compiled outputs (`classes`, `generated`, `resources`, `tmp`, `test-results`, `reports`
— never the slow-to-rebuild neoForm cache) and SAY so in one line. An unstamped `build/` that
already holds classes predates the stamp and is the OLD default's. Reproduce it before trusting the
fix: fill `build/` with the pre-flip commit's build, then run the post-flip Gate A. It is red
without the stamp and green with it. **The flip also moves every "the default" in the gates:** a
merge gate that ran one target's full suite becomes one run per target. The companion-only tiers
(W15) go to the companion's target, read off its jar, and never follow the default.

**W18. 🔴 A VERSION FACT HARDCODED OUTSIDE THE BUILD SURVIVES THE PORT — `pack_format` 48 IN A 1.21.4
PACK.** The port corrects everything the compiler sees. A number typed into a TOOL does not move: a
bundled external compiler (the content mod's CBScript fork, Python) and a Java pack writer both wrote
`"pack_format": 48` on 1.21.4, where the number is **61**. Nothing failed. A data pack with an older
number still loads, flagged "made for an older version". The content mod's install-time rewrite (which
puts every stored pack into this build's dialect) then corrected it on the way into the world, so
every gate stayed green and no test reached the writer. · **Fix:** in Java, read the number from
the running game: `SharedConstants.getCurrentVersion().getPackVersion(PackType.SERVER_DATA)` (the
same for `CLIENT_RESOURCES`). An external tool cannot do that, so the caller passes the target in
(an env var, as the content mod does with `CBSCRIPT_MC`), the tool maps it through a table
(1.21/1.21.1 → 48, 1.21.2/1.21.3 → 57, 1.21.4 → 61), and a test drives the REAL writer rather than
the table. The writer test is red on the old code (48 ≠ 61). **Sweep:** `catalog-scans.md` §W18. A
rewrite at install is a backstop, and while it exists the bug stays invisible.

**W19. A RELEASE-ALIGNED BRANCH, derived instead of re-ported — and the three things the derivation got wrong.**
· **Pattern:** a fork port built on the author's development tip, offered or installed as if it were their
release · **Symptom:** players run code the author never released (half-finished features, their bugs), and
nothing in the offer says so; a derived release branch then compiles yet drops released behaviour or crashes at
start · **Fix:** measure the distance (`tools/port-provenance.py`), derive the release branch with
`tools/port-derive.py` and read its loss list, then run every gate. A fork cut from an author's development tip ships code players have never run (measured: 52 commits, +4877 lines
past the release). `tools/port-derive.py` replays the port's commits onto the release commit, drops files that
exist only in unreleased code, and `--resolve` hands each still-failing file to one worker with the release copy
beside it. Measured on one mod: 58 errors → 0 for $1.77 of model spend, against $2.55 for the original port. It is
not a free lunch, and the gates were what made it safe: (a) clearing an error, the model DELETED two released
behaviours whose declaration the replay had lost (a config option, a registry registration) -- now flagged by
`release_losses`, which lists every removed line the release also has; (b) the replay took the development
branch's mixin config, naming ten unreleased mixins and dropping two released ones -- now fixed by the derive
itself; (c) one released mixin re-listed that way does not apply on the new version at all (R29). Read the
loss list, run every gate, and let CI be the second sample. Match the release by publish time over EVERY registry
the mod is on (`tools/port-provenance.py`), and find the project by its `displayName`: a similarly named mod once
put a 1-commit distance at 34.

## X. CODEMOD HYGIENE — a rule that matches nothing is invisible, and that is the whole problem
> **Axis:** instrument quality. Every large migration here is driven by mass rewrites, and this
> section is the failure mode they share: the rewrite *runs*, reports nothing, changes nothing, and
> the resulting compile errors look like unported API rather than a broken tool. Three distinct bugs
> of this shape landed in one afternoon on the builder mod's port; the fix for all three was the same
> **detector**, not the same patch.

**X1. 🔴 BUILD THE DEAD-RULE DETECTOR FIRST.** After a run, report every rule that matched **zero**
times. A rename table is a set of assertions about the source; a rule that never fires is either
(a) genuinely unused, or (b) malformed — and those are indistinguishable from the output otherwise.
The builder mod's detector found bugs X2, X3 and X4 within minutes of first running, after two of them had
already survived a code review and a full compile. Add a marker (`#!exhaustive`) for blocks that are
*generated* and expected to be mostly dead — 400 generated colour rows will otherwise drown the one
warning you needed to see.

**X2. The lookbehind that blocks the very thing you meant to match.** A type rename must not fire
after a dot (or `a.b.Foo` gets half-rewritten by a rule for `Foo`), so `(?<![\w.])` is correct — and
it silently makes every **member** rename impossible, because a member rename fires *only* after a
dot. One anchor cannot serve both. Split the table into rule *kinds* (`plain` type / `member:` /
`re:` raw regex) with the right anchoring baked into each, rather than one regex flavour with an
exception list.

**X3. `printf %b` mangles a regex.** Emitting rename rows through a shell `printf` turns `\b` into a
backspace and `\1` into a control character. The rules then match nothing, in a file that looks
perfect when you `cat` it. Generate rule files from Python (or `printf '%s'`), and if a tool writes
tab-separated data, write a literal tab rather than `\t`.

**X4. 🔴 ORDER: specific before general, and getting it wrong is silent.** Regex rules encode whole
call *shapes*; token rules are a broad type sweep. Run the sweep first and the shape rules can never
match — `InteractionResultHolder` is renamed to `InteractionResult` before the
`InteractionResultHolder\.sidedSuccess\(…\)` rule ever sees it, so the payload-dropping rewrite never
happens and you are left with a call that looks ported and is not. Regex first, tokens second, and
within the tokens **longest key first** (V11).
· **AUGMENT — a call-shape regex must also be IDEMPOTENT, i.e. anchored on its left:** `re:PacketDistributor\.sendToServer\(` → `net.neoforged.neoforge.client.network.ClientPacketDistributor.sendToServer(` also matches INSIDE its own output, so running the table over code that is already 26.2-correct (a file written fresh for 26.2, or a second pass) produces `Clientnet.neoforged.neoforge.client.network.ClientPacketDistributor…` and a `package … does not exist` error. Anchor every `re:` rule whose replacement contains its own pattern, and fold a fully-qualified spelling into the same rule: `re:(?<![\w.])(?:net\.neoforged\.neoforge\.network\.)?PacketDistributor\.sendToServer\(`. A quick self-check for a table: apply it twice and diff; any change on the second pass is a non-idempotent rule. (found writing a fresh 26.2 Gate C harness for a shield-adding item mod.)

**X7. 🔴 A MEMBER rename must name its OWNER unless the member name is globally unique.** A rule like
`member:MOVEMENT_SPEED -> SPEED` is correct for `MobEffects` and catastrophic for `Attributes`, which
still has that constant (V22) — so the rewrite performs the rename it was asked for and silently
corrupts an unrelated class that merely shares a member name. Measured: 16 broken call sites from one
row. The check is one command — `javap <the other likely owner>` — and the anchored form
(`re:\bMobEffects\.MOVEMENT_SPEED\b`) costs nothing. · **Why it is worse than a dead rule (X1):** an
unfired rule leaves the original error, so the count simply fails to fall. An over-eager rule
*creates* errors elsewhere, and it only surfaced here because the count went the wrong way on that
family — a row that had merely made an existing error *look different* would have been invisible.
The two together are the pair to instrument: count matches per rule, and compare the error count
per FAMILY across runs rather than only in total.

**X7b. ⚠ AUGMENT to X7 — a lookbehind on the RECEIVER EXPRESSION guesses how the owner was
SPELT, which is not the same as naming the owner.** X7 says a member rename must name its owner.
The inherited table's `moveTo` → `snapTo` row (§V39) tries to, and does it by excluding the one
receiver the author had seen: `(?<!Navigation\(\))\.moveTo\(` — which excludes the CALL CHAIN
`getNavigation().moveTo(` and nothing else. A navigation held in a FIELD sails straight through:
`this.navigator.moveTo(this.owner, this.followSpeed)` was rewritten to a method `PathNavigation`
does not have, on a port whose only crime was storing its navigator in a variable.
· **The stopgap is more lookbehinds** (`(?<![Nn]avigator)(?<![Nn]avigation)`) and it is worth
labelling as one in the table, because it is guessing at identifiers rather than at types. **The
real fix is a rule kind the table does not have** — one that can ask what the receiver's TYPE is —
and until it does, any `member:` rule on a name two unrelated classes share is a lookbehind arms
race. The tell that you are in one: your exclusion list is made of NAMES people chose, not of
types the compiler knows.
· It fails loudly here (`PathNavigation` has no `snapTo`), which is the lucky half. The same shape
on a pair where both halves happen to compile is X7's silent corruption again.

**X5. Two ways to count wrong, both of which report GOOD NEWS.** *(All four ways — X5a/X5b/X13 and the double-count — are enforced by `tools/burndown-count.sh`, which asserts the compile RAN, classifies errors by KIND, and counts unique `file:line`. Use it rather than a hand-rolled `grep -c`: each of the four cost a session before it was written down, and every one of them reads as good news.)*
· **"0 errors" from a build that never compiled.** A Groovy typo in `build.gradle`
  (`ext.has(...)` is not a method — use `project.hasProperty(...)`) fails at *configuration*, so the
  log contains no `error:` lines at all and a `grep -c 'error:'` prints **0**. Counting the absence of
  errors is not counting zero errors. Always assert the build reached compilation — check for the
  task, or for a known-nonzero baseline — before believing a drop.
· **Gradle echoes compiler output twice**, so `grep -c 'error:'` double-counts (the builder mod: 4508 reported
  for 2254 real). Count **unique `file:line` locations**, not lines. A halving that comes from fixing
  the counter is indistinguishable in a progress log from a halving that comes from fixing the code.

**X5b. 🔴 A FOURTH way to count zero and be wrong, and it is the most flattering: javac
ABORTED AT PARSE.** X5 covers a build that never compiled and the double-counting; X13 covers
the preprocessor failing. All three leave the compiler's own output absent, so a guard that asks
"did compileJava run" catches them. This one defeats that guard, because compileJava *did* run
and javac *did* report a number.
· **Measured:** a burn-down at 1595 errors, five rename rows added, next pass reports **3**. One
of the rows was a regex that also rewrote the IMPORT line — `import ...properties.EnumProperty
<net.minecraft.core.Direction>;` — which is a syntax error. javac stops at parse when a file will
not parse, so it never attributes and never sees the other 1592. Three parse errors in three files
read as a 99.8% burn-down.
· **The tell is the ERROR KIND, not the count.** `';' expected`, `<identifier> expected`, `class,
interface, enum, or record expected`, `illegal start of expression`, `reached end of file while
parsing` are PARSE errors; everything a port normally fixes (`cannot find symbol`, `incompatible
types`, `does not override`) is ATTRIBUTION. A pass whose errors are mostly the first kind has not
made progress, it has broken a file — so the counter flags it instead of printing a number.
· **A second instrument caught the same bug from the other side, and neither was conclusive
alone.** The dead-rule report said `dead=1`, naming the PLAIN row the regex had pre-empted
(regexes run before token rules, §X4). From there it reads as a redundant row to delete —
tidiness — while the count read as triumph. Only together do they say "one rule is doing something
it should not". **When two instruments disagree about whether a pass went well, that is the
finding**, not noise to reconcile.
· **The fix was to use no regex at all.** A plain type row is already anchored `(?<![\w.])`
(§X2), so the FQ row rewrites the import and the bare row rewrites the uses — the two-row
`ResourceLocation`/`Identifier` shape at the top of every inherited table. A regex that has to
re-do what a plain row does is a rule in the wrong class.

**X9. Count errors PER FAMILY, not just in total — it is what makes an over-eager rule visible.**
X7 says a member rename must name its owner; X1 says report rules that matched nothing. Neither
catches the shape that actually bit twice: a rule written from **one observation** generalised to a
family that is not uniform. Seeing `playSound(..., Reference<SoundEvent>, ...)` fail is not evidence
that every `SoundEvents` constant is a Holder — and the blanket fix took a 230-error build to 289.
· The total moving the wrong way is the alarm, but only for a rule big enough to swamp everything
else fixed in the same batch. Bucket the count by error message and by file, compare buckets across
runs, and a rule that *created* a family is obvious even when the total went down. · The underlying
discipline is V27's: when a name is missing, ask the JAR what replaced it — and when a rule is about
to generalise, ask the jar whether the family is uniform, rather than assuming the one member you
looked at speaks for the rest.

**X8. An extraction can break the CANONICAL target while the new one goes green — which is the
whole argument for W2.** Routing six `SavedData` stores through a compat helper meant passing
their `save` as a method reference. On 26.2 that compiled; on **1.21.1** it did not, because
`SavedData` there declares both `save(CompoundTag, Provider)` and `save(File, Provider)` and
`X::save` is ambiguous. Nothing about the 26.2 work suggested the old version was at risk, and a
build that only exercised the new target would have shipped it. Fix: an explicit lambda
(`(d, tag) -> d.save(tag, null)`), which also documents that the provider is unused.
· **The general shape:** a helper's parameter is a *functional interface*, and which overload a
method reference resolves to is a property of **each** version's class hierarchy. Overload sets
diverge between versions far more quietly than signatures do, because no error names them.

**X6. Progress on a big port is a number you must be able to trust every hour.** With the counter
fixed and the detector in place, the builder mod's 1.21.1→26.2 run reads 2254 → 2020 → 1501 → 1238 → 987 → 964
→ 747 → 684 → 627 → 507 → 457 → 433 → 421 → 376 → 313 → 290 → 265 → 248 → 240 → 230 →
**289** → 216 → 169 → 118 → 74 → 41 → 19 → 4 → **0**, each step naming what moved. That sequence is the
artefact that makes a multi-thousand-error port a schedulable task instead of an open-ended one —
and it is only worth anything if the number means the same thing at both ends.
· **The 289 is not a typo and is left in on purpose:** one over-eager rule (X9) put 59 errors
  back, and a burn-down that quietly omits its own regressions is a progress report rather than
  an instrument.
· **Count UNIQUE `file:line`, not `error:` lines** (Gradle echoes compiler output twice), and
assert the build actually reached compilation before believing a drop (X5).
· **The curve is not linear and should not be read as one.** The early steps are mass renames;
the late ones are per-family design work (a persistence rewrite, a render-state rewrite, a
library major bump) where 20 errors can cost more than 200 did. A burn-down that flattens is
usually the port reaching its real content, not stalling.

**X10. 🔴 A dead-rule report you do not READ is a dead-rule detector you do not have.** X1 says build
the detector first. It is not enough. Measured on one port: the detector had been printing
**"WARNING: 20 rename rule(s) matched nothing"** on every single run for hours while eight of those
twenty were real bugs — rules written, reviewed, committed, and never once firing — and the count went
down only because someone finally read the line. Three things make the difference between a warning
that works and one that scrolls past:

1. **Print the WHOLE list, not the first N.** The truncation (`... and 8 more`) is what hid half of
   them, and the half it hid was not the boring half — truncation ordering is arbitrary, so it hides
   real and benign rules alike.
2. **Drive the count to ZERO and keep it there**, using the `#!exhaustive` marker for rules that are
   dead *by design*: absorbed by a compat pair, or defensive coverage of an API this mod happens not
   to use. A warning that is permanently non-zero is decoration; a warning that is normally zero is an
   alarm. (17 of that 20 were legitimately dead-by-design and had simply never been marked.)
3. **Read it every run**, or make the build fail on a non-`#!exhaustive` dead rule.

**The eight real bugs, because the SHAPES recur:**
· **X4 ordering, again, and it is subtle in the regex direction too.** A regex anchored on the NEW
name can never fire, because the token rename that produces that name runs *after* every regex —
`re:EntitySpawnReason\.SPAWN_EGG` is unreachable while the source still says `MobSpawnType.SPAWN_EGG`.
Anchor a regex on what the source says at THAT point in the pipeline, not on what it will say later.
· **`[^()]*` forbids an argument that has its own parens.** `sidedSuccess(level.isClientSide())` and
`isSolidRender(level, at.below())` both defeated it. Allow one nesting level:
`\((?:[^()]|\([^()]*\))*\)`.
· **A rule written against the shape you IMAGINED, not the shape the formatter produced.** Three
rules required `\)\.set\(` or `\.Builder\.of\(` to be adjacent, and the real source breaks every one
of those chains across lines with indentation between. Put `\s*` at every link of a fluent chain as a
habit — it costs nothing and is the single commonest cause of a dead rule.
· **A rule genuinely subsumed by a more specific one** written later, and a **literal duplicate** of an
existing rule. Both are harmless and both are noise, which is exactly why they must be removed rather
than tolerated: they are what trains you to stop reading the warning.

**X11. 🔴 A SUPPRESSION MARKER whose scope disagrees with how people use it silently disables the
whole check.** X10 says read the dead-rule report. This is the failure one layer under that: the
report was being read, said "1 dead rule", and was **looking at 20 of 719 rules**.

`#!exhaustive` marks a generated block where a rule matching nothing is expected. Its scope in the
parser was **sticky until an explicit `#!strict`**; every author had used it as **per-rule** (three
consecutive markers in the file, each re-marking the next single row — which only makes sense if you
believe the previous one has expired). One block whose closer was never written therefore exempted
**699 of 719 rules**, and the warning went on reporting all-clear over the remaining twenty for the
length of the port. That is worse than having no detector at all: a check looking at 3% of the table
prints exactly what one looking at all of it prints.

Three things fix it, and the first is the general one:

1. **An unclosed suppression block is a HARD ERROR.** Scope that can leak silently will leak
   silently. Failing at load turns the leak into something you cannot ship past.
2. **Let the marker carry a reason** — `#!exhaustive  # generated, most rows unused`. The one marker
   in this table that *did* explain itself parsed as an ordinary comment (`line.strip() ==
   '#!exhaustive'` is false once anything follows it) and so did nothing at all. The marker with the
   best documentation was the one that was inert, which is a hard failure to spot by reading.
3. **Print the coverage, not just the violations.** `checked=187 exempt=527` on every run makes a
   collapse to `checked=20` visible; "1 dead rule" does not.

**Pointing it at the table immediately paid**: seven dead rules surfaced, five of them real — four
REDUNDANT (a broader, earlier rule already did the work, including two added in the previous commit
by someone who did not know rule 742 covered them) and one naming a parameter type the mod has no
override for. **Deleting all five left the rewrite count at exactly 2653, unchanged** — which is the
proof they were doing nothing, and the cheap check to run after any such deletion. The two survivors
are dead by design and now say why.

**The reusable form:** any switch that turns a check OFF — a suppression marker, a
`@SuppressWarnings`, a lint exclusion, a skipped-test annotation — needs its blast radius asserted,
because nothing else will ever tell you it grew. Ask your tooling how much it is checking, not only
what it found.

**X12. A negative lookbehind cannot exclude the word you are matching.** · **Pattern:** a rule that
must skip `super.foo(...)` while catching `x.foo(...)`, written as
`(?<!super)(\w+)\.addAdditionalSaveData\(` · **Symptom:** it matches `super.` anyway. ·
**Why:** the lookbehind is evaluated at the position where `(\w+)` *starts*, so it asks what precedes
`super`, not what `super` is. `\w+` then happily consumes the word you meant to exclude. · **Fix:**
put the exclusion where the word actually is — `(?<![\w.])(?!super\b)(\w+)\.` — a lookbehind for the
boundary, a look**ahead** for the word. This is a rename-table bug that produces a *wrong rewrite*
rather than a dead rule, so X1 will not catch it; the compile does, but only if the wrong rewrite
happens not to be valid Java.

**X13. A third way to count zero errors and be wrong: the PREPROCESSOR failed, so nothing compiled.**
X5 covers a configuration-time failure and the double-counting. In a §W multi-version build there is
a step in between — the source-preparation task — and when *it* fails the log has no `error:` lines,
no compiler output, and a task list that stops one short. The count reads **0**. · **The check that
distinguishes all three, and it is one line:** confirm `compileJava` actually appears as an executed
task, that javac printed its own `N errors` line (or nothing), and only then trust the unique
`file:line` count. `BUILD SUCCESSFUL` is not the same claim — it is true of a build that did nothing.


**X14. 🔴 A compile-error burn-down is BLIND to wiring — and in a §W tree the gate that isn't is
usually the one you forgot to build.** The burn-down (X6) is the instrument that makes a
multi-thousand-error port schedulable, and its blind spot is everything that is legal Java and wrong
at runtime: §R in general, and above all **registration**. Two measured on one port, both at zero
errors:

· **R1** — a class registered on the event bus whose last `@SubscribeEvent` had been moved out by a
  refactor. `register(SomeClass.class)` is not type-checked against what the class contains, so it
  compiled on BOTH targets. It is a hard crash at mod construction *on the shipping version*.
· **R25** — every block and item needing `Properties.setId` on 26.x.

**The trap that let the first one sit:** in a multi-version build the source-preparation step is
usually wired for `sourceSets.main` only, so `src/test/java` is raw and the new target's Gate A
fails at `compileTestJava` — i.e. it has *never run*, which looks identical to it not being reached
yet. Route the test tree through the same pipeline (it shares the rename table; it wants no overlay,
because a test needing per-version code is telling you the thing it tests needs a compat pair). Here
that was **twelve rewrites**, and it immediately turned up R25.

· **Recognising R1 from the log is worth knowing, because it does not look like a test failure.**
  FML dies inside the JUnit launcher, so Gradle reports `Could not start Gradle Test Executor N:
  java.lang.reflect.InvocationTargetException` and the sentence naming the class is **four
  `Caused by:` levels down**. Read to the bottom of the chain before concluding the harness is
  broken.
· **Guard it with a SOURCE SWEEP in Gate A, not a runtime check** — the runtime "check" is the crash.
  A text sweep for `EVENT_BUS.register(X.class)` against a `@SubscribeEvent` grep over every root
  (overlays included) costs milliseconds and fails by name.
· ⚠ **Such a guard cannot be A/B'd by reintroducing the bug** — doing so crashes FML before JUnit
  starts, so the "failing" run never executes the test and proves nothing. Give it a **self-check**
  that plants the offender in a temp dir, asserts it is caught, then adds a handler and asserts it
  goes quiet. Otherwise green is equally consistent with a sweep matching nothing — the X11 failure
  wearing different clothes.

**The rule: a zero on the burn-down is a milestone, not a verdict.** The first thing to do at zero is
run the cheapest gate that BOOTS the thing, and the first thing to check is that the gate can build
at all on the target you just fixed.


**X8b. ⚠ AUGMENT to X8 — while PARALLEL AGENTS are editing, a canonical-control reading is not
attributable, and it fails looking exactly like X8.** X8's whole value is that the canonical target
catches an extraction the new one accepts, so a 0 → N on it is the loudest signal in a §W port.
Measured here: an integrator's control compile went **0 → 7**, every error a `cannot find symbol` on
a compat class in another agent's bucket — a textbook X8, and it was about to be reported as one.
The compat class **existed on disk**; the agent had written it seconds after `prepareSources` copied
the overlay, so the prepared tree was simply older than the source tree. Re-running gave 0.
· **The tell is cheap and specific:** if a `cannot find symbol` names a file that is present in
`src/`, you have a stale prepared tree, not a missing implementation. Check before you attribute —
a wrong regression report costs an agent its context and you the trust in your own instrument.
· **The rule:** a burn-down number taken while anything else is writing to the tree is a number you
may act on but must not QUOTE. Either quiesce the tree first, or say in the same breath that another
writer was live — which is also why the 695 → 691 in the commit beside this says the delta is not
one author's to claim.

**X15. 🔴 RUN EVERY GATE ON BOTH TARGETS — the old version's run is not a formality, it is the only
thing that makes the new version's number readable.** Measured on one era jump, same commit:

| gate | 1.21.1 | 26.2 |
|---|---|---|
| A — unit | 606 pass, 0 fail | 606 pass, 0 fail |
| B — GameTest | **All 242 passed** | **236 of 242** |
| C — real client | PASS | boots fully; harness never activates |

**Without the left-hand column "236 of 242" is unreadable.** Six red tests on a fresh port is
equally consistent with six migration regressions, six tests that were already broken, and six
flakes — and those need completely different work. The control run on the *same commit* settles it
in one command. (It also caught the reverse: one of the six is a test this repo already records as
flaky, so it does not count until it is re-run.)

· **A gate that has never RUN on the new target is not a gate that is red — it is missing**, and the
  two look identical from a task list. Three separate harness layers in one port were pinned to the
  old version and had to be pointed at the new one before they could report anything: the test
  source tree (X14), the GameTest wiring (V47), and the Gate-C launcher, which read
  `versions/1.21.1.properties` and never forwarded the build's own `-Pmc`. Each was a one-line
  omission that presented as "the new target has no results yet".
· **Read a partial gate result for its SHAPE, not its count.** The five real failures above were
  three cable-car tests, a car that moved 0.0 blocks under full throttle, and a house built from
  gravel and stone instead of its own blocks — entity movement and block placement, not five
  unrelated defects. A count says how much is left; the clustering says what to look at.
· **The arc is the reassuring part, and it is worth recording next to the first measurement.** The
  same era jump finished at **every gate green on both targets at one commit** — so full parity
  across a calendar-versioning era jump is reachable, not aspirational:

  | gate | 1.21.1 | 26.2 |
  |---|---|---|
  | A — unit | PASS | PASS |
  | B — GameTest | `All 242 required tests passed` | `All 243` (see the batch note below) |
  | C — real client | PASS | PASS — **byte-identical result string** |

  Gate C returning the *same characters* on both targets is the strongest single line in the table:
  it is a real 26.2 client rendering the mod's models, resolving its names, filling the creative
  menu and dropping a live player thirty blocks onto a custom block unharmed, scored by the same
  assertions the old target passes. Of the original six red, **three were real port bugs** (V48 a removal hook
  that stopped seeing its own block entity, V49 an inverted authority predicate, X18 two big-build
  tests grown into one another), **two were latent flakes the port merely exposed** (X19 a live
  sibling inside a 192-block radius, X21 a fixture that could kill its own subject) and **one was a
  regression I introduced while fixing X19**. So "six failures" was one third migration work, one
  third pre-existing debt, one sixth self-inflicted — which is exactly the split the control column
  exists to reveal and which no amount of staring at the 26.2 log alone could have produced.
· **⚠ Compare BATCH COMPOSITION, not the totals — 243 against 242 is not a discrepancy.** The two
  numbers differ and the coverage does not: both targets run the identical **242** mod tests
  (50/50/50/50/42), and 26.2 runs **one extra vanilla test** in a `minecraft:default` environment
  that its data-driven harness (V47) materialises and 1.21.1's annotation discovery never had. The
  totals invite exactly the wrong conclusion in both directions — "the new target runs one MORE, so
  something is duplicated" or, had it gone the other way, "one of my tests silently stopped being
  registered". `grep -oE "Running test .*batch [0-9]+ \([0-9]+ tests\)"` answers it in one command,
  because 26.x prints the environment name alongside each batch and vanilla's is `minecraft:`.
  A generated registrar is precisely where a test CAN go missing without anything failing, so this
  is the check to run rather than to skip.
  ⚠ **AUGMENT — vanilla is not the only extra contributor, and a DEPENDENCY is a bigger one.**
  Measured on a large boss mod (~750 files, mixin-heavy): 1.21.1 ran **5**, 26.2 ran **8**, and the composition reads
  `examplemod:default (5)` + `minecraft:default (1)` + **`examplelib:default (2)`** — the
  same 5 mod tests either side, plus vanilla's one AND two belonging to a *dependency mod on the
  runtime classpath*. 26.x's data-driven harness registers every TEST_INSTANCE it can see, where
  1.21.1's annotation discovery is filtered by `neoforge.enabledGameTestNamespaces`. So a 60%
  higher total can mean **zero** change in what the mod is actually covered by, and the only thing
  that says so is the per-environment breakdown. `grep -aE "Running test (batch|environment)"`
  prints it on both targets, and the namespace prefix is the whole answer.

· **A client that boots and reports nothing is a RESULT, and a good one.** On an era jump whose
  biggest bucket is the render rewrite, "every texture atlas built, the title screen reached, only
  deprecation warnings" is most of what Gate C exists to prove. Record it as reached-and-then-stalled
  with the specific thing that did not happen, rather than as a failure; "the client crashed" and
  "the client is fine and my harness did not start" are opposite findings and only the log tells
  them apart.


**X38. A COMPILE-CLEAN burn-down that reaches zero has not been told about the RESOURCE layer or
the REGISTRATION layer, and on a §W port those two arrive in that order, each masking the next.**
§X14 says a zero is a milestone rather than a verdict and names R1/R25 as the blind spot. Measured
end to end on one MCreator port, the sequence after the count hit 0 was:

| gate | what it said | cause |
|---|---|---|
| Gate A 1.21.1 | `checkClientItemDefinitions FAILED` | the egg-tint scan matched nothing (§V80) |
| Gate A 1.21.1 | `checkSpawnEggArt FAILED` | no per-target egg template (§V42c) |
| Gate B 26.2 | `loadwarning.neoforge.onlyin`, **0 tests run** | 25 `@OnlyIn` annotations (§V79) |
| Gate B 26.2 | `NullPointerException: Item id not set`, **0 tests run** | 78 unstamped Properties (§R25) |

**Every one of those was invisible at zero errors, and each was only reachable once the one before
it was fixed** — §V42c's sequencing rule, arriving in the gates rather than in the resource loader.
So the honest reading of a burn-down reaching zero is *"the compiler has nothing left to say"*, and
the useful next number is how many distinct gate failures are still ahead, which is unknowable until
you run them. · **The corollary for scheduling a port:** do not report a port as nearly done at zero
errors. Two of these four were multi-file sweeps (25 annotations, 78 call sites), i.e. real work
that the burn-down had already counted as finished.

**X30. Before writing a single rule, ask what the DEPENDENT actually uses — on a library it can
decide the whole shape of the port.** V8 gives the scoping number (which imports changed); this is
the cheaper question that comes first, and it is one grep:
`grep -rhoE '<lib package>\.[A-Za-z0-9_.]+' <dependent>/src/main/java | sort | uniq -c`.
· Measured on a config library: its dependent, a ~750-file boss mod, names the config-screen system and `CompatHelper`,
and the 3D-screen layer **zero times** — while **68 of the 98 errors were in that 3D layer**. So
the 30 errors that mattered went first and finished clean, and the 68 became a separate, much
easier decision (a recorded drop) rather than the bulk of the work. Sorted the other way round,
the port spends its afternoon rebuilding a developer debug screen nothing runs.
· **The general form:** an error count measures WORK, never VALUE, and on a library the two can be
close to inverted. Split the count by "does anything downstream reach this" before letting it set
your order — the answer is also the number the §V52 drop decision needs anyway.

**X16. 🔴 A Gate-C client that "hangs" is usually FINE — and NeoForge's warning screen is enough to
stall the whole gate.** · **Pattern:** a client test times out; the launcher reports
`NO RESULT — client died before reporting`; the log stops dead after mod loading ·
**What it actually is:** `jstack` put the render thread in
`Minecraft.runTick → renderFrame → FramerateLimiter.limitDisplayFPS` — the *idle loop*. The client
was alive and ticking the whole time, showing a screen nobody dismissed. On MC 26.x that screen was
`net.neoforged.neoforge.client.gui.LoadingErrorScreen`, which NeoForge raises for **warnings**, not
only errors, and which a human tester clicks past without noticing.
· **The trigger can be one annotation.** A single `@OnlyIn` in the whole mod raised a mod-loading
warning on 26.x — where the annotation no longer strips members, so it buys nothing — and that one
warning put the screen up. Removing it left **eight more** from third-party mods deprecating
`logoFile`, which the port cannot fix. So the harness must click past the screen, not the mod.
· **Put it in the SHARED guard, not the test.** Whatever class every client test already relies on
(this repo's is a `pauseOnLostFocus` guard) is where a "dismiss the benign blocking screen" rule
belongs — one edit covers all 25 tests and the 26th. Match the screen by **class name** rather than
by import when the file is shared across versions: no per-version import, no rename-table row.
· 🔴 **And make the wait NAME what is blocking it.** `waitTitle` sat at an unexpected screen until
the launcher's timeout and reported the client had died. Printing
`stuck on <screen class>` on timeout is a two-line change that turned an open-ended hunt into an
immediate answer. **"The client crashed" and "the client is fine and my harness never started" are
opposite findings that only the log distinguishes** — so make the harness say which.

**X17. A partial gate result is not a fixed number — re-run before you quote it.** The same 26.2
Gate B produced **6 failures on one run and 7 on the next**, on identical code: one entity test
appeared in the second run and not the first. This repo already records two of its GameTests as
flaky, and the honest reading is that the *stable* set is what matters (here: three cable-car
tests, a car that moved 0.0 blocks, a house built from the wrong blocks) with the rest quarantined
until re-run. Quoting a single run's count as "N regressions" attributes the flakes to the port.


**X18. 🔴 PORTING THE TEST HARNESS RESHUFFLES THE PLOTS — every test isolated by "my origin plus a big
constant" was only isolated by accident.** Two independent isolation habits in a mature GameTest suite
are both luck, and both stop working the moment the harness changes:

- **"I build far away, at plot origin + 15000."** Every test's plot origin is *different*, so two tests
  naming the same offset are separated by however far apart the framework happened to put their plots —
  a number neither test knows. The builder mod's boss-proof-house test and one of its landmark tests
  both build at `+15000, +15000`. On 1.21.1 that was fine. On 26.2 it was not, and the house test
  reported *"the storm would eat `[gravel, stone, andesite, cobblestone]`"* — a landmark's materials,
  which the house has never placed. Nothing in that message points at another test.
- **"I look for my entity within N blocks of my origin."** Plots sit ~14 blocks apart, so an
  `inflate(16)` search reads BOTH neighbours. It only ever passed because no neighbour happened to leave
  an entity behind; the moment a real bug (V48) leaked one, **three** cable-car tests went red together
  and two of them were innocent bystanders reporting impossible things ("a car parked at the wrong
  station", "a car I refused to create").

**Why the port is what triggers it.** §V20 replaces annotation discovery with a generated registrar, so
the ORDER tests are registered in changes — and order is what decides which test is whose neighbour and
where each plot lands. Nothing about that is visible in a diff, and the resulting failures land on
*victims* rather than on the change. (The framework's own spacing constants were byte-identical between
the two versions here; it was purely the ordering.)

**Fix both with isolation that is structural rather than numeric:** search `helper.getBounds()` (present
in both versions) instead of a radius, and give each big-build test its own **lane** on an axis nothing
else uses, with the reason written above the constant. The builder mod's own notes already recorded the
lane rule after an earlier collision; what it had not recorded is that **a lane collision is a property
of the pair of tests, so the rule has to be applied to every big-build test at once** — one test moving
into a clear lane does nothing if a second later moves into it too.

**And the general shape, which is X7/X11 in yet another costume:** a check whose scope you never asserted
is a check whose scope will drift. Here the scope is *physical* — the volume a test believes it owns —
and no compiler, gate or code review has any way to see two tests agreeing on the same coordinates.

**X19. A test that leaves a PERSISTENT entity behind poisons a REUSED world forever — and the era jump
is when you find out.** A GameTest world is reused between runs and never reset. The framework clears
each PLOT before a test, so an entity that stays put is harmless; one that wanders a few blocks out
survives every future run of every test. The builder mod's biome bosses are `setPersistenceRequired`
(they must never despawn in play), four GameTests spawn one and none discarded it, and the rule they
test has a **192-block** separation radius against ~14-block plot spacing — so a single stray poisons
rival checks across the entire suite. It presented as one long-standing "known flaky" test asserting
*"the spot is not free"* about a spot nothing in that run had ever occupied, and, most likely, as a
second unrelated test whose Baby Dragon kept mysteriously dying.

**The fix is to DISCARD WHAT YOU SPAWN, and only that.** The obvious second half — sweep the radius
before measuring it, to cure a world already poisoned — is a trap, and it was tried and measured:
GameTests in a batch run **concurrently**, so an unconditional sweep deletes a LIVE entity belonging to
whichever sibling test is running alongside. Here it took out two boss tests that have nothing to do
with spawn rules, and the failures landed on *them*. (A player mock in the same repo solves the same
problem with an **age** — anything older than a few hundred ticks is necessarily abandoned — but only
because a player mock mints its own birth tick; a vanilla entity carries nothing to age it by, and
`tickCount` is not persisted.) An already-poisoned world is cleaned by deleting it, which is an
operator action, not a test one.

**Why it belongs in a MIGRATION catalog rather than a testing one:** a stray accumulates one per run, so
the failure rate climbs with the number of runs — and a port is when a suite gets run twenty times in a
week. The reflex is to blame the new Minecraft version. The tell that it is not: the failure moves
between tests run to run, and `rm -rf run/` makes it go away.

**⚠ AND THE STRAY WAS ONLY HALF OF IT — check for a LIVE sibling before believing the stray theory.**
Deleting the 5 GB accumulated world did not fix the test. The other half is X18's radius problem with
the numbers reversed: the rule under test has a **192-block** separation and plots sit **14** apart, so
`BossOfTheForestGameTest`'s perfectly healthy, currently-running boss is inside the radius
`BossSpawnRulesGameTest` is measuring. There was no stray at all in that run; the neighbour was alive.
· **The fix is to measure a radius the plot OWNS.** Where a check is about the free/occupied
*transition* it is asked at 8 blocks, not 192 — the distance is a separate assertion. Where a check is
genuinely about the distance (or drives production code that hardcodes it), it moves **four
separations clear of the whole plot grid**, taking its own subject with it; "just past the radius" is
not far enough, because a probe there still has the rest of the batch inside *its* radius. ·
**Generalisable rule:** any test asserting over a radius larger than the plot spacing is asserting
about its neighbours. Compare the two numbers before writing the assertion — they are both constants,
and nothing else will ever compare them for you.

⚠ **CORRECTION to the second half: "move it four separations clear" BROKE a test that was green, and
the failure landed on a different test from the one being fixed.** The narrowing half is right and
held — the flaky `killing_a_boss_frees_its_space_again` went green the moment its transition check
asked at 8 blocks instead of 192. The *moving* half did not. Relocating the one test that genuinely
drives a 192-block rule meant hand-building its subject 768 blocks out —
`EntityType.create(level)` + `setPos` + `addFreshEntity` — and the boss was then **invisible to
`Level.getEntities`**, so the rule under test kept answering "the spot is free" and the assertion
that had passed for months started failing. Cause unestablished; what IS established is that
`GameTestHelper.spawn` works and the hand-rolled equivalent did not, so **relocate by spawning
through the framework, or do not relocate.**
· **Two things worth more than the specific bug.** A test suite has a *previously-green* set, and a
change made to fix flakiness can quietly move a failure from a flaky test onto a stable one — the
count stays similar and reads like slow progress. **Diff the failing NAMES between runs, not the
count** (X17 says re-run before quoting a number; this says compare the sets). And when a
neighbour-proofing fix cannot be made safely, the fallback is not to weaken the assertion but to make
its failure NAME the neighbour: the "before" leg now lists every boss inside the separation radius, so
the next occurrence identifies itself instead of reading as this test's own setup being wrong.

⚠ **A THIRD option, and it is the one to reach for first: assert what is TRUE, not what your
neighbours allow.** X19 offers narrowing the radius (worked) and relocating the subject (broke a
green test). Neither fits when the radius is a **production constant** the test exists to exercise.
Measured on the same suite: a mall's crowd-LOD sweep puts a shopper to sleep when no player is
within `ACTIVE_RADIUS` **56**, and the test asserted "with nobody about, every shopper sleeps" — a
premise it cannot possibly guarantee, because plots sit ~14 apart and **eight sibling tests create a
real `ServerPlayer` in the real player list**. Adding two entirely unrelated tests elsewhere turned
it red, reproducibly, without a line of mall code changing.
· **The fix is to measure the situation first and assert the rule against it:** find the nearest
player, then require *asleep* beyond the radius and *awake* inside it. Both branches are real
assertions about the behaviour under test; only which one applies is out of the test's hands. It
cannot be wrong, it never weakens into a no-op, and it gets stronger rather than weaker when a
neighbour interferes — where "skip if a player is near" would have quietly stopped testing anything.
· **Watch for the PLAYER version of the radius trap specifically.** An entity search is the obvious
case and gets caught by `helper.getBounds()`; `level.players()` is not scoped to a plot at all and
no bounds helper exists for it, so any rule keyed on "distance to the nearest player" is measuring
the whole batch by construction. A repo whose mock-player helper deliberately joins the real player
list — which is usually the right call, since a fake one breaks on join — has this everywhere.

**X20. 🔴 The gate itself runs out of memory, and the stack trace names an innocent test.** A port is
run through Gate B twenty times in a week, and X19's reused world grows the whole time (measured here:
**5.0 GB / 5624 region files**). Two things then compound. A GameTest JVM inherits the JVM's *default*
max heap — a fraction of the machine's RAM, chosen by ergonomics and never stated anywhere in the
build — and a big-build test that forces real worldgen (an ancient city, five villages) makes chunk
saving expensive at exactly the moment the world is largest.
· **The symptom is a stack trace with no mod in it:** `OutOfMemoryError: Java heap space` in
`PalettedContainer.reencodeContents`, on thread **`IO-Worker-7`** — a chunk-save worker. It surfaces
under whichever test happened to be running, so it reads as that test's bug, and it moves run to run.
Nothing in it points at either the world's size or the heap setting, which are the two actual causes.
· **Fix both halves, because either alone comes back:** `rm -rf run/<gametest world>` (5.0 GB → 115 MB
here) and **state the heap in the run config** — `jvmArgument '-Xmx4G'` on the `gameTestServer` run —
so the gate's memory does not depend on the machine it lands on. Write the reason above the line; the
next reader has no way to know an unstated default was ever the problem.
· **The generalisable shape, and it is X13/X5 again in a different costume:** an **unstated default** is
not a setting, it is a variable you cannot see. The heap was never chosen, the world was never reset,
and both were invisible until the failure blamed a third thing. When a gate fails in infrastructure
code (an IO worker, a serialiser, a scheduler) rather than in the mod, ask what the harness is doing
*around* the test before reading the test.

**X21. 🔴 A test whose FIXTURE is an adversary of its subject is a coin toss — and a port is what makes
it land tails.** X19 and X18 are about a test's *neighbours*; this is the same statistical trap with
nothing external involved, entirely inside one test. · **Pattern:** a combat/interaction test spawns a
live hostile as the thing to act upon, then asserts the subject is still alive at the end —
`spawn(BABY_DRAGON)` (16 HP) one block from `spawn(ZOMBIE)` (3 damage a swing), tick 60, assert the
dragon survived · **Symptom:** it passes almost always, and the message when it does not
(*"Baby Dragon should survive attacking"*) describes the subject rather than the fixture, so it reads
as a defect in the thing under test · **Fix:** the fixture is a **punching bag, not an opponent** —
`zombie.setNoAi(true)`. It still takes the damage the real assertion measures; it just cannot swing
back. If a test's own name says the subject *should survive*, the test must not contain something able
to kill it.
· **Why it belongs in a MIGRATION catalogue rather than a testing one, and it is X19's argument
generalised:** a test that fails one run in twenty is invisible during ordinary development and
near-certain during a port, because a port is when the whole suite gets run twenty times in a week.
Worse, the failures are then *attributed to the port* — the reflex is to go looking for what the new
Minecraft version changed about combat, and here nothing had. **The tell is the same one as X19: the
failure moves between runs, and it reproduces on the OLD target too.** Which is exactly what the X15
control run is for: a red test on both targets at the same commit was never the port's doing.
· **Make the failure name the alternative explanation.** Rather than weakening the assertion, the
`fail` branch now lists every `LivingEntity` within 24 blocks — so the next occurrence answers, in its
own message, whether the fixture, a sibling test's entity (X18/X19) or the subject's own logic is
responsible. A one-line diagnostic is worth more than a re-run, because the state that caused it is
gone by the time you go looking.

**X22. 🔴 ONE rename table, SEVERAL source roots — a per-root dead-rule check is guaranteed noise,
and it drowns the one number that took work to earn.** X10 says read the dead-rule report; X11 says
assert its scope. This is the third way the same instrument fails, and it is the one that survives
both of those fixes. · **Pattern:** the §W pipeline is run once per *source root* — `src/main/java`
and, per X14, `src/test/java` — sharing one table, and each run reports its own dead rules ·
**Symptom:** the main tree reports `dead=0` and the test tree reports **184 dead rules on every
single build**, printed two lines apart. Nothing is wrong. 72 test files simply do not touch most of
a 719-row API table · **Why it is worse than it looks:** a rule is dead if it matches nothing
*anywhere*, so the per-root number is not a weaker version of the right answer, it is a different
question — and a permanently non-zero warning sitting beside the meaningful zero teaches the reader
to skip **both**. That is X10's "a warning that is never zero is decoration" arriving through a door
X10 does not cover, because each individual root's check was implemented correctly.
· **Fix:** the first root **collects** which rules fired (`--hits-out`), the last root **merges** and
is the only one that reports (`--hits-in`), with an explicit task dependency so the order is real
rather than incidental. A missing hits file must **exit non-zero** — silently reporting one root's
dead set as if it covered every root is the exact failure being removed, and it fails in the
reassuring direction, inventing dead rules rather than hiding them. (Verified by pointing `--hits-in`
at a path that does not exist; cf. X14 on guards you cannot A/B.)
· **The result is worth quoting because BOTH numbers now mean something:** 26.2
`checked=186 exempt=527 dead=0`, and 1.21.1 `checked=3 exempt=0 dead=0` — where that 3 is the **W2
invariant made visible**, the canonical table containing nothing but compat-helper routing. A
canonical target whose checked count ever exceeds its compat-helper count has had a
new-target-shaped rule leak into it.
· **The generalisation, and it is the one to carry to any shared analysis:** when a single
configuration is applied to N inputs, "unused" is a property of the **union**, never of any one
input. Reporting it per input is not a conservative approximation — it is a false positive per
input, at a rate that grows with N, on precisely the check whose value depends on normally reading
zero.

**X23. 🔴 An INHERITED rename table is mostly dead by design — and the marker that says so must wrap
the WHOLE sweep, because any inner `#!strict` closes it early.** `templates/multi-version/versions/
26.2.renames.tsv` is a whole-API sweep promoted from a 458-file mod. Point it at a 101-file library
and the honest result is that most rules match nothing: measured, `checked=173 dead=164` on a port
with exactly two rules of its own. That is X10's failure arriving through the front door — a
permanently large dead count sitting next to the number that has to stay zero teaches the reader to
skip both.
· **The fix is not per-rule marking.** The inherited table already carried inner
`#!exhaustive`/`#!strict` pairs (from the mod it was promoted from, around its generated colour
rows), so an `#!exhaustive` added at the top was closed by the first inner `#!strict` and exempted
2 more rules out of ~700. The whole inherited region is exempt **as one block**, with the inner
markers removed and a comment saying why there is deliberately no inner closer; each mod's own rows
go after a single trailing `#!strict`.
· **Both numbers then mean something again** — measured on a fluid/energy transfer library: `checked=2 exempt=698 dead=0`,
where the 2 are its own rows and both fire. Same shape as X22's canonical-target reading: a count
you can compare against what you expect, rather than a warning you have learned to scroll past.
· **The general form:** when a shared configuration is inherited wholesale by a consumer that uses
a fraction of it, "unused" stops being evidence of a bug and the check has to be told so *at the
granularity it was inherited* — otherwise the exemption you add is smaller than the thing you meant
to exempt, and nothing says so.
· ⚠ **AUGMENT — the exemption then SWALLOWS a new row placed where a new row most wants to go.**
X23 makes the whole inherited region one block, which is right, and it has a consequence nobody
states: the most natural place to write a row is **beside the row it corrects**, and every row
worth correcting is inside that block. Measured on a ~660-file GeckoLib mob mod — five new rows added next to the
inherited `.location()` row they narrow, and the dead-rule check went `checked=70` → `checked=71`
for five rows. It is not a warning and not a failure; **the COUNT is the only thing that says so**
(§X11's invariant again: assert the scope, not the findings). Moved past the trailing `#!strict`
it read `checked=75 exempt=837 dead=0`, all five firing.
· **So read `checked` after every edit to the table and expect it to move by the number of rows you
added.** The file's own header says where mod rows go; the pull toward the row you are correcting
is stronger than the header, so leave a **pointer comment** beside that row naming the rows that
narrow it and where they live — the next reader needs both halves and only one of them can be in
the exempt block.

**X24. A shared TEMPLATE can carry a superseded approach, and the mod that copies it inherits a bug
the catalogue already records as impossible.** `templates/multi-version/tools/gametest_adapter.py`
still emitted the `TestFunctionLoader` wiring that **§V47 records as unworkable from a mod** — the
fix had landed in the mod it was promoted from and never in the template. The tell was a compile
error (`TestFunctionLoader is not a functional interface`), which is the lucky case; had the template
been one step less stale it would have compiled and produced §V47's exact symptom instead: every test
discovered, every test failing with `Trying to access missing test function`.
· **Fix, and it is the reusable part:** the template now generates BOTH halves — the registrar and
the `DirectGameTest` class it refers to — parameterised by modid and package, so a port cannot
hand-copy the compat class and get it subtly wrong, and there is one place to fix next time.
· **When you correct a pattern in a mod, diff the template against it.** A catalogue entry that says
"this cannot work" does not reach the generator that keeps emitting it.

**X26. 🔴 The dead-rule report was wired to a task Gradle never reaches DURING a burn-down.**
X22 fixed *where* the report is computed (merge the hits across every source root, report once from
the last). It left a hole that only shows up on a port that is not yet green: the reporting root is
the TEST tree, so its task hangs off `compileTestJava` — and Gradle never gets there while
`compileJava` is failing. Measured on a space-exploration mod (~550 files): through five burn-down passes and 23 newly written
rules, `dead-rule check` printed **not once**. The check existed, was correctly implemented, was
never executed, and the build said nothing about that.
· **Two changes, and both are needed.** `prepareSources` must `finalizedBy` the reporting task, so
it runs whether or not the compile then succeeds — a finalizer runs on failure, which is the whole
point. And that task must be `outputs.upToDateWhen { false }`, because its real product is not the
files it writes but the LINE it prints: with unchanged inputs Gradle skips it and the report
silently vanishes again, which is the same failure wearing the other hat. (Both were needed here —
the finalizer alone still printed nothing, because the test tree was up to date.)
· **The generalisation:** a check whose output is a side effect of a cacheable task is a check with
an off switch nobody chose. Ask *when* your instrument runs, not only whether it is correct — and
the answer has to include the failing case, because that is the case it exists for. The space mod now
reads `checked=23 exempt=698 dead=0` on every compile attempt, red or green.

**X25. 🔴 The two targets' compile classpaths are PACKAGED DIFFERENTLY, so "diff the merged jars"
silently scopes a port wrong.** V8's scoping method — diff the mod's own import set against the
target's real classpath — is the number that turns an era jump into an estimate, and it is only as
good as the jars you point it at. · **Pattern:** ModDevGradle stages 1.21.1 as a single
`build/moddev/artifacts/neoforge-21.1.228-merged.jar` containing Minecraft **and** NeoForge, and
26.2 as `minecraft-patched-26.2.0.75-merged.jar` — Minecraft **only** — with NeoForge arriving
separately as `neoforge-26.2.0.75-universal.jar` from the Gradle module cache. Both are called
"merged"; they do not merge the same things. · **Symptom:** every single `net.neoforged.*` import
reads as REMOVED. Measured on a space-exploration mod (~550 files): 82% unchanged / 58 removed against the truth of 85% / 39,
i.e. **19 phantom names on the work list**, including whole subsystems (`RegisterKeyMappingsEvent`,
`RenderLevelStageEvent`, `EntityAttributeCreationEvent`) that are perfectly fine. · **Fix:** build
the target classpath as a SET over every jar the compile actually uses, and **assert it** — one
line, `count of classes under net/neoforged/ > 0`, is enough to make the packaging difference
impossible to miss. · **The generalisation, and it is V27 pointed at your own tooling:** an
instrument that reads a filename is trusting a naming convention across a version boundary, which
is exactly the thing an era jump does not preserve. This one fails in the *alarming* direction —
inflating the work rather than hiding it — which is the good half of the luck; the same mistake
against a jar that happened to be a superset would have under-scoped the port instead, and nothing
about the output would have said so.

**X25b. ⚠ SHARPENING of X25: a COUNT is not an assertion — assert CANARIES, one per artifact.**
X25 says build the target classpath as a set over every jar and assert it, and offers
"`count of classes under net/neoforged/ > 0`" as the one-line check. That check **passes on an
incomplete classpath**, and did: scoping a large boss mod (~750 files, mixin-heavy), the set had `neoforge-universal` and
`fmlloader` and so counted 2056 `net.neoforged` classes — while the **eventbus** artifact was
missing entirely, so `SubscribeEvent`, `IEventBus`, `Event` and `ICancellableEvent` all read as
REMOVED. Five phantom names on the work list, in the alarming direction, from a check that had
been written specifically to stop that.
· **The NeoForge stack is several artifacts** — `neoforge-*-universal`, `fmlloader`/`loader-*`,
`bus-*`, and the distmarker api (`Dist` lives in `mergetool-*-api`) — so a count over the shared
package prefix cannot tell you which of them you are missing.
· **Assert one known-present class per artifact instead**, and fail by naming the ones absent:
`net.minecraft.world.item.Item`, `net.neoforged.neoforge.common.NeoForge`,
`net.neoforged.fml.loading.FMLEnvironment`, `net.neoforged.bus.api.SubscribeEvent`,
`net.neoforged.api.distmarker.Dist`. A canary says *which* jar is missing; a count only says the
prefix is represented.
· **The general form, and it is X11's:** a check whose value depends on its SCOPE must assert the
scope, not a symptom of it. "Some of the right classes are here" is not "all the right jars are
here", and the difference is invisible in the number.


**X25b-ii. ⚠ SHARPENING of X25b, learned by hitting it TWICE in a tool written to avoid it: a
canary set is only as good as your enumeration of artifact FAMILIES, and "missing" is not the
only way a classpath lies — "duplicated" is worse.** X25b says assert one canary per artifact
rather than counting classes. Building `tools/quick-typecheck.sh` — a fast javac screen over the
prepared tree, written with X25b's canaries in it from the first line — the same failure landed
twice more, both in the alarming direction:

1. **A family I had not enumerated.** The canaries covered NeoForge's artifacts; the mod takes
   JEI from a local `libs/` fileTree, not the module cache. So 73 phantom `package mezz.jei…
   does not exist` errors, in four real files, indistinguishable from a half-ported JEI plugin.
   **Any import from a non-`java`/`minecraft`/`neoforged` root package is a family**, so the
   canary sweep is now derived from the tree's own imports instead of a hand-written list.
2. 🔴 **A family present TWICE, at two versions — which no canary can catch, because every
   canary resolves.** `~/.gradle/caches/modules-2` holds every version both targets ever
   resolved, so a `find(1)` sweep put two Guavas and two `fmlloader`s on the path and javac took
   whichever sorted first. That made `FMLEnvironment.dist` (a FIELD on 1.21.1, a method on 26.2)
   and Guava's `buildKeepingLast` read as **missing on the canonical target**. Thirteen
   confident findings against a tree that compiles clean.

**The fix is to stop reconstructing the classpath at all.** Gradle's resolved graph is the
authority and it will print it: a one-task init script (`tools/qtc-init.gradle`) dumps
`sourceSets.main.compileClasspath` for a target, cached per target and invalidated by
`build.gradle`/`versions/*.properties`, after which each javac pass is seconds. **Refuse to fall
back to a hand-built path** — a screen that is wrong in the alarming direction is worse than no
screen, because its output is exactly the shape of real work.

**The arc is the lesson: 86 phantom → 13 phantom → 0.** X25c's identity case is what caught it —
pointed at the version the source is written FOR, the answer is known in advance and is empty.
Run it before trusting any number a new instrument gives you, and prefer *asking the build* over
*reconstructing what the build would have done*, every time.

**X25c. 🔴 A DIFFER HAS A FREE SELF-CHECK: point it at the version the source is written
FOR, and it must report zero work.** X25 and X25b are both about the classpath a scoping tool
reads. This is about the tool's own arithmetic, and it is the cheaper of the two checks because
it needs no reasoning at all — just run the scope against the CANONICAL target.
· **Measured:** `tools/scope-port.py` reported a ~750-file boss mod as **95 removed imports on 1.21.1**
— against the very version its source compiles clean on. Every one was a **nested class**:
`EntityRendererProvider.Context` is `EntityRendererProvider$Context.class`, not
`EntityRendererProvider/Context.class`, so a straight dot→slash conversion misses all of them.
One import, `EntityRendererProvider.Context`, was 33 of the 95 by itself. Fixed by walking the
trailing dots into `$` before giving up (§P #146's inner-class subtlety, in a different tool).
· **The direction of the error is the thing.** Like X25 it fails ALARMING — inflating the work,
never hiding it — which is the good half of the luck twice running. A differ that resolved too
LOOSELY would under-scope a port and nothing about its output would say so. Do not rely on
getting the safe direction a third time; run the identity case.
· **The general form, and it costs one command:** any analyser that compares A against B has an
identity case, A against A, whose right answer is known in advance and is *empty*. That is an
oracle you always have and never have to construct — the X15 control column pointed at the
INSTRUMENT rather than at the gate. A tool with no identity case (`--list removed` against the
canonical target printing nothing) is a tool whose numbers you are taking on trust.
· `tools/scope-port.py <mod> --mc <target>` prints the canary line and the three buckets;
`--list removed` is the work list. It is checked in rather than re-derived per port, because
the queue has six more mods and the last two both re-invented this by hand.

**X25b-iii. ⚠ ONE MORE LAYER of X25b-ii: the classpath can be Gradle's own and still name a file
that is STALE, because one entry on it is a TASK OUTPUT your screen never triggers.** X25b-ii's
fix is "stop reconstructing the classpath, ask the build" — and `quick-typecheck.sh` does exactly
that. It is still not enough. · **Pattern:** ModDevGradle stages Minecraft into
`build/moddev/artifacts/` with the mod's **access transformers already applied**, and the AT files
are a declared INPUT to `createMinecraftArtifacts`. A real `./gradlew compileJava` re-stages the jar
whenever an AT changes; a screen that only READS the resolved path does not, so after an AT edit it
type-checks against a jar where every member you just widened is **still private**. ·
**Symptom:** `life has private access in AbstractArrow` — which is indistinguishable from a member
that genuinely moved or was made private, i.e. the exact shape of real porting work. Measured on
a large boss mod: **6 phantom errors out of 637**, every one of them an AT'd field, all gone the instant
the artifact was re-staged with no source change at all. · **Fix, and it is the §S2 form rather than
a note:** the screen compares each AT file's mtime against the staged jars and says so, naming the
one command that fixes it. A/B'd both ways (touch the AT → warning; touch the jar → silence), and
synced to all four copies of the tool (§X35). · **The generalisation, and it is the third time this
shape has appeared:** "I asked the build for the classpath" answers *which files*, never *how old
they are*. Any input your instrument shares with a Gradle task — an AT, a generated source root, a
prepared tree (§X8b) — is a file the task keeps fresh and your reader does not. Ask when it was last
written, not only whether it is on the path.
· ⚠ **And the reason the re-stage failed the first time is worth its own line, because it looks
like a broken build and is not:** `createMinecraftArtifacts` exited 1 with
`java.net.ConnectException` from `downloadManifest`. The egress proxy had **restarted on a new
port** mid-session and `/root/.gradle/gradle.properties` still pinned the old one — the trap the
content mod's own notes record, arriving here. Derive the port from `$HTTPS_PROXY`, WRITE the file
(never `sed` it — on a missing file `sed` is a silent no-op), then `./gradlew --stop`. A daemon
started while the port was right goes on working until something makes it fetch, which is why this
surfaces on the first network-touching task rather than at the edit that broke it.

**X27. 🔴 An instrument that has never RUN is not a green gate — and this repo's mixin auditor had
four independent faults at once, each of which alone would have hidden the bug it exists for.**
`tools/audit-mixin-targets.py` was written for exactly the failure at **R17**, is documented in
§165 as a done-gate, and reported *"all targets match"* over a mixin that killed the 26.2 client at
load. Every fault is a shape worth recognising elsewhere:

1. **It compared PARAMETER types and not the RETURN type.** Its own docstring says so, as a
   deliberate sharpening over an earlier name-only version — the fix that made it good is the same
   reasoning that stopped one step short. Mixin resolves by the whole descriptor, so a method that
   keeps its name and parameters and changes what it returns is a hard apply failure this could
   not see (R17 shape (f)).
2. **It could not FIND a sources jar.** It knew only NeoGradle's `build/neoForm/*/sources.jar`, and
   26.x builds only under ModDevGradle (V2), which stages Minecraft at
   `build/moddev/artifacts/*-sources.jar`. So on every multi-version port it exited *"no sources.jar
   found"* — a MISSING gate, which from a task list is indistinguishable from one not yet reached
   (X15). ⚠ And the two targets are named on different axes: 26.2 is
   `minecraft-patched-26.2.0.75-sources.jar` (the Minecraft version) while 1.21.1 is
   `neoforge-21.1.228-sources.jar` (the NeoForge version), so a matcher keyed on `minecraft_version`
   finds one and not the other. Try both keys the properties file already carries.
3. **It read `src/main/java`** — which on a §W tree is the CANONICAL version's dialect. Pointed at
   the other target's jar that is not a weaker check, it is a DIFFERENT one: 18 mismatches, all
   noise, on a check whose entire value is normally reading zero (X10). A multi-version port must
   audit the PREPARED tree (`build/generated/sources/<overlay>/java`, renames applied and overlays
   merged — what actually compiles and what the mixin config actually loads) against that target's
   own jar, and be REFUSED rather than guessed at when no target is named.
4. **It was wired into no build anywhere**, so it had run only when someone remembered — which is
   §S2's rule with a name attached: the compile loop has a forcing function and a hand-run script
   has none, so the script loses every time. It is a `check` task on both targets now, and in
   `templates/multi-version/`.

**A/B it, don't assume.** Restoring the pre-fix mixin makes it print
`mixin: void (SoundInstance)` against `vanilla: PlayResult (SoundInstance)`, which is the only
thing that distinguishes a fixed detector from one that still matches nothing.

⚠ **AUGMENT — two MORE faults in the same tool, found the next port, and both printed a PASS over
zero work.** X27's four were about which jar and which tree; these two are about the SCOPE INSIDE
the tree, and they are the reason the count must be read and not just the verdict.

5. **The file glob knew only the PLURAL package name** (`**/mixins/**`). a config library's package is
   `mixin` — the Fabric/Yarn spelling, and at least as common on Forge — so the tool globbed zero
   files. Both spellings, and both a nested and a flat layout, are matched now.
6. **The declared-name capture was `(\w+)`, and Python's `\w` excludes `$`.** Mixin's own
   convention is to prefix members with `<modid>$` to avoid collisions
   (`examplemod$getZoom`), so **every `@Invoker`/`@Accessor` written the recommended way slipped
   the regex** — the tool read the file, resolved the `@Mixin` target, matched the vanilla source,
   and then matched no members at all. Widened to `([\w$]+)`, with the `<modid>$` prefix stripped
   before the vanilla name is derived.

⚠ **AUGMENT — a SEVENTH fault, and this one is the opposite failure: a false POSITIVE.** Java 8
TYPE_USE annotations sit INSIDE a qualified name, and 26.x vanilla uses that spelling —
`selectNextMoveDirection(Direction.@Nullable Axis avoidAxis, …)`. The tool's `simple()` strips the
annotation and then splits on `.`, which leaves **`' Axis'` with a leading space**, so it compares
unequal to a mixin's plain `Axis`. The report then prints two sides that read IDENTICAL —
`mixin: (Axis, Entity)` against `vanilla: void ( Axis, Entity)` — and invites you to "fix" a mixin
that was already correct. One `.strip()` on the split result. **A checker's noise costs the same
trust its silence does** (X10): six faults that printed a pass and one that printed a failure are
the same bug, which is that nobody had A/B'd the instrument in either direction. Do both — plant a
real mismatch AND confirm a correct mixin reports clean.

⚠ **AUGMENT — an EIGHTH and NINTH fault, both found by a Gate A run on the very next port, and
both had printed "all targets match".** They are the same invariant failing twice more, and each
is a shape worth naming because neither is exotic:

8. **It never checked a FIELD's type**, and said so in a comment — *"deliberately loose about the
   type, which an accessor is free to widen."* An `@Accessor` is **not** free to widen: Mixin
   resolves a field accessor by name AND descriptor, so a field whose TYPE changed under a stable
   name is a hard APPLY failure —
   `No candidates were found matching lastHurtByPlayer:Lnet/minecraft/world/entity/player/Player;`.
   26.2 retyped `LivingEntity.lastHurtByPlayer` to `EntityReference<Player>` (the same change
   `ShulkerBullet.finalTarget` took, §V61). Teaching it to compare the accessor's declared type
   against the field's — the return type for a getter, the single parameter for a setter — found
   **three more** real mismatches on the same port at once (`CubeDefinition.origin`/`dimensions`,
   `Vector3f` → JOML's read-only `Vector3fc`; and `RenderSystem.shaderLightDirections`,
   `Vector3f[]` → `GpuBufferSlice`), with **zero false positives across 51 targets** on the
   canonical version.
9. **It silently skipped `@Mixin(targets = {"a.b.Outer$Inner"})`.** That spelling is not exotic —
   it is the ONLY way to name a package-private or inner class, so any mod touching a vanilla goal
   or a nested state uses it. The regex looked for `@Mixin(X.class)` and `continue`d, so those
   mixins were never counted and never checked. Here that hid a `@Shadow` of
   `Bee$BeePollinateGoal.findNearestBlock`, which 26.2 **inlined away** — a mod-load crash that
   only Gate A reported. Resolve the outer class's file from the string (it is already fully
   qualified, so there is no import to look up) and search all of it; being loose about which of
   the two classes declares the member errs toward a false negative rather than crying wolf.

**A/B BOTH ways, and against the PREPARED tree.** The 26.2 leg of the first A/B attempt reported
`checked 0` — correctly, because the un-prepared shared file still names the 1.21.1 package and
its outer class does not exist in the 26.2 jar. That is the `checked == 0` guard doing its job and
it is *not* the true-positive test; apply the target's renames first, or you are A/Bing the
preparation step rather than the detector.

⚠ **AUGMENT — a TWELFTH fault, and it is the first one the tool was RIGHT about and still wrong.**
The `@Shadow` field check added at fault #8 compares ERASURES, deliberately: a field accessor binds
by name and descriptor, and `List<String>` and `List<Component>` share the descriptor
`Ljava/util/List;`. So the check answered "will it bind" — correctly — and printed *all targets
match* over a shadow that binds and is **not the same type**. 26.2 retyped
`SplashManager.splashes` from a mutable `List<String>` to an immutable `List<Component>`; the shared
mixin applied cleanly and then called `add` on it, which threw from inside a reload listener's
`apply`, failed the whole initial resource reload and left the client at the title screen forever
with no crash and no line naming the mod (V70).
· **Two questions, both worth asking, and only one of them is about binding.** The erasure check
stays as the hard failure; when it passes, the tool now also compares the FULL generic type and
reports a mismatch separately (`binds, but is not the same type`). A/B'd in both directions — the
planted pre-fix shadow is caught, the fixed one is clean — and swept across four ports on both
targets for **zero** new findings, which is the X10 condition for letting a check gate anything.
· **The general shape:** a check written against the failure you were chasing answers that failure's
question. Ask what ELSE the same declaration can be wrong about — here, "does it resolve" and "is it
the same thing" are different questions with the same syntax, and erasure is precisely the mechanism
that separates them.

⚠ **AUGMENT — faults TWENTY and TWENTY-ONE, one of each kind, found by a closing bucket rather
than by a failure.** They are worth recording together because they are the two halves of §X10's
rule: a checker's noise costs exactly the trust its silence does.

20. **A NESTED class named via a dotted IMPORT was skipped, whole file.** Fault #9 covers
    `@Mixin(targets = {"a.b.Outer$Inner"})`, the string form. This is the other spelling —
    `@Mixin({Properties.class})` with `import …BlockBehaviour.Properties;` — which resolves to
    `…/BlockBehaviour/Properties.java`, not a file, so the tool fell through its "not a vanilla
    class" guard and checked nothing in that file. It is at least as common as the string form: a
    mixin into a vanilla `Properties`, `Builder` or nested goal is written exactly this way.
    Measured on a ~660-file GeckoLib mob mod, where `BlockBehaviourMixin` is the ONLY surviving 26.2 mixin, so the
    tool printed `checked 0 … NO TARGETS CHECKED` — the §X27 guard firing correctly over a
    blindness one layer above it. Fixed by collapsing trailing UPPERCASE path segments and
    searching the outermost class's file, exactly as the `targets=` branch already did.
21. **A RECORD ACCESSOR read as `no such method in vanilla` — a FALSE POSITIVE.** 26.2 made
    `ArmorMaterial` a record, so `public float toughness()` exists at runtime and is *generated*;
    a source-text scan finds no declaration and the tool cries wolf over a correct mixin. The era
    jump turned a great many vanilla classes into records, so this would have fired on port after
    port. Fixed by falling back to the record HEADER and returning each component as the
    no-argument accessor it really is.

**Swept over all 12 multi-version ports × both targets after both fixes: 22 of 24 runs clean, zero
new findings, zero noise.** The two that are not clean are one mod that genuinely ships no mixins,
where `checked == 0` exiting 2 is the guard doing its job — and that port deliberately does not wire
the gate, with the reason in its build.gradle. That is the shape to copy: a permanently-red check is
decoration, so a mod with an empty scope opts out **in writing** rather than by weakening the tool.

⚠ **AUGMENT — fault TWENTY-TWO: the generic check (fault #12) covered `@Shadow` fields and not
`@Accessor`s, and the accessor is the one every caller reads.** A `@Shadow` field is at least used
inside the mixin; an accessor's declared type is what the whole mod programs against. A ~750-file boss mod's
brain accessor `getMemories()` kept 1.21.1's `Optional` generic on 26.2 (§V89) and the audit printed
*all targets match* over a server crash. The accessor branch now asks fault #12's second question —
the erasures agree, so it binds; is it the same TYPE? — for the getter's return or the setter's
parameter. **A/B'd both ways** (the pre-fix tree exits 1 naming `memories`; the fixed one is clean)
and **swept over all 11 ports that ship mixins × both targets: zero new findings**, which is §X10's
condition for letting it gate.

**The fix that makes the next one visible is neither of those: `checked == 0` now EXITS 2 and says
"NO TARGETS CHECKED -- this is NOT a pass."** Six independent faults in one instrument, five of
which reported *all targets match*, is not six unlucky bugs — it is one missing invariant, and the
invariant is X11's: **assert the scope, not only the findings.** A check whose value depends on
normally reading zero must distinguish "I looked and found nothing wrong" from "I looked at
nothing", because those print the same word. Measured after: `checked 6 … all targets match`, and a
planted `@Accessor("zoomXXX")` is caught (exit 1) — the A/B above, which is the only thing that
tells a fixed detector from a still-blind one. Measured after:
54 targets on 1.21.1, 40 on 26.2 (ten mixins are dropped there) — and **compare that composition
rather than the totals**, exactly as X15 says for test counts.

⚠ **AUGMENT — a TENTH fault, found by Gate C over a green audit, and it is the widest one yet.**
The tool skipped every `@Inject` whose target is named **without a descriptor**, on the reasoning
`# name-only target: mixin resolves it loosely`. That is half true and the wrong half: a name-only
target IS resolved by name, and Mixin then validates the **callback's own parameter list** against
the target's — a mismatch is a hard `InvalidInjectionException: Invalid descriptor ... Expected
(...) but found (...)`. So a name-only `@Inject` is exactly as checkable as a pinned one; the
descriptor to compare is simply the handler's parameters up to its `CallbackInfo` (everything from
there on is Mixin's — the CallbackInfo itself, and with `locals = CAPTURE_*` the captured locals
after it). **Measured on a ~750-file boss mod: 49 of its 53 `@Inject` targets are name-only**, so the
audit was covering **8%** of them and printing *all targets match* over a 26.2 client that died at
load on `GameRenderer.bobView` (`(PoseStack, float)` → `(CameraRenderState, PoseStack)`).
· **Fixing it found two more in the same file set**, both real and both masked by the first
(§V42c's sequencing rule — a hard failure short-circuits everything downstream of it):
`ClientLevel.getSkyDarken` (gone from `ClientLevel`; only `Level`'s `int` form survives, and an
`@Inject` resolves on the TARGET class, never its superclass) and `Gui.renderSpyglassOverlay`
(moved to `Hud` and renamed `extractSpyglassOverlay`, §V28 + §V53).
· **A/B'd in both directions and against the identity case (X25c):** the true positive is the live
mismatch above; the canonical target went from 51 to **64** targets checked with **zero** new
findings, which is the reading that says the widening added coverage and not noise.
· **The invariant this keeps failing, stated once more:** every fault in this list but one printed
a PASS, and each was a SCOPE bug rather than a logic bug — the wrong jar, the wrong tree, the wrong
package spelling, the wrong member syntax, and now the wrong *subset of the annotation's own
grammar*. `checked == 0` exits 2, but `checked == 4 of 53` reads exactly like `checked == 53`.
**Print the count, compare it against what you expect the corpus to contain, and treat a number
that is small for the tree as a finding** — the same discipline X15 applies to test totals.


**S4b. 🔴 A LOADER HOOK that stopped existing is S4 with no library bump to warn you — `Item.initializeClient` is gone on 26.2.** · **Pattern:** the standard NeoForge way to attach client behaviour to an item, `public void initializeClient(Consumer<IClientItemExtensions> consumer)` on the Item subclass · **Symptom:** *nothing.* On 26.2 that method is a NeoForge patch into vanilla `Item` and is **declared nowhere** — checked in both jars — so the override overrides nothing. An extra public method on a class is perfectly legal Java, so it compiles, ships, and is never called: the item's overlay, tooltip or scope texture is simply absent. · **Fix:** `RegisterClientExtensionsEvent` — and the happy part is that it exists with the **same `registerItem(IClientItemExtensions, Item...)` signature on BOTH targets**, so this is one shared mod-bus listener rather than a compat pair, and it is the better code on the old version too. · **Why it survived a full port here:** the override carried no `@Override`, which is the migrator's own rule from the space mod's sky (put `@Override` on every loader hook, because with it a changed hook is a compile error and without it the feature silently disappears) — and worse, a careful 26.2 **overlay of the extensions class** had already been written and reviewed, so the port looked *more* finished than it was. A per-target overlay of the thing a dead hook returns is a strong smell: check that anything still calls the hook.
· **The neighbouring win to look for:** while confirming this, both targets' NeoForge turned out to route the spyglass scope texture through `IClientItemExtensions.getScopeOverlayTexture` — so a mixin `@ModifyArg` on `Gui.renderSpyglassOverlay`'s `blit` had been redundant on 1.21.1 all along (#157). Deleting it removed a pinned `blit` descriptor from the port, which is exactly the kind of string the next version breaks silently.


**X33. 🔴 A LISTENER is resolved by its PARAMETER TYPE, and javac has no opinion about whether the
bus will accept it — so an event that became ABSTRACT is a compile-clean crash.** ·
**Pattern:** any `@SubscribeEvent` (or `addListener`) naming a vanilla/loader event · **Runtime:**
`IllegalArgumentException: Cannot register listeners for abstract class
net.neoforged.neoforge.client.event.RenderLevelStageEvent. Register a listener to one of its
subclasses instead!` at client load. 26.2 split that event into one subclass per stage; the source
compiles unchanged on both targets · **Fix:** name a concrete subclass. In a §W tree the parameter
type is the one thing a rename row **cannot** carry, so a listener that must differ moves into the
compat pair that already owns the subsystem (§W5) rather than getting a whole overlay.
· 🔴 **AUGMENT — that fix PASSES THIS ENTRY'S OWN GATE AND LEAVES THE FEATURE DEAD, at least for
`RenderLevelStageEvent`, which is the very event the entry is written about.** The abstractness is
real and is not the reason a mod cannot use it: on 26.2 the event **carries no pose stack and no
collector**, so there is nothing to submit geometry with. All seven post sites in `LevelRenderer`
are inside the frame-graph *execute* pass and pass **`null`** for the pose stack —
`post(new RenderLevelStageEvent.AfterOpaqueFeatures(this, levelRenderState, null, modelViewMatrix,
this.visibleSections))` — and `javap` shows the class declaring only `getLevelRenderer`,
`getLevelRenderState`, `getPoseStack`, `getModelViewMatrix` and `getRenderableSections`: **no
`getStage`, no `getCamera`, no `getRenderTick`**. So naming a concrete subclass compiles,
registers, satisfies `audit-event-listeners.py`, and draws nothing.
· **The successor is `SubmitCustomGeometryEvent`**, posted from `LevelRenderer.submitFeatures` on
the line after `submitBlockDestroyAnimation`, carrying a real `SubmitNodeCollector`, the same
identity `PoseStack` vanilla's own submits use, and the `LevelRenderState`. Ordering stops being the
listener's choice: `SubmitNodeCollection.submitCustomGeometry` branches on
`renderType.hasBlending()`.
· **And the general rule, which is why this belongs in §X rather than §V:** a gate that checks
whether a registration will be ACCEPTED cannot check whether the thing registered can still do its
job. An event that survives as a type but loses the arguments its listeners needed goes green on
every such check. **When a check passes on a subsystem an era jump rewrote, ask what the listener
DOES with what it is handed, not only whether it will be handed anything** — the audit answers
"the bus will take this", never "this can still draw".
· **It gets a SWEEP rather than a lesson because it is the third compile-clean registration failure
in one port** — R1 (a class registered with no handlers), R25 (the unset `Properties` id), and now
this — which is X14's blind spot three times over. `tools/audit-event-listeners.py` reads the
PREPARED tree, resolves each listener's parameter through the file's own imports, and asks that
target's jars whether the class is abstract; it is a `check` task on both targets and in
`templates/multi-version/`.
· ⚠ **Its first A/B FAILED, and the cause is X25b-ii verbatim** — the tool globbed
`~/.gradle/caches` for jars, picked up BOTH NeoForge versions, kept whichever sorted first, and
audited the 26.2 tree against the **1.21.1** jar. Every listener read as fine, including the
abstract one it exists to catch. It now reads Gradle's own resolved classpath
(`build/qtc-classpath-<target>.txt`, which `quick-typecheck.sh` writes) and **refuses to
reconstruct one**, because a screen that is wrong in the reassuring direction is worse than no
screen. Two jars, two versions, one prefix: the canary set (X25b) cannot catch it, since every
canary resolves.
· ⚠ **And its second failure was a STALE PREPARED TREE (X8b)**: the fix was in `src/`, committed and
correct, while `build/generated/sources/mc26` still held the old file, so the guard reported two
findings against source that no longer existed. The tell is X8b's: a finding that names a construct
the source no longer contains is a stale tree, not a defect. `--rerun-tasks` on `prepareSources`
settles it.

**X28. 🔴 A Gate-C client that "hangs" is usually FINE, and which screen blocks it depends on the
RUN DIRECTORY — so the same commit passes on one target and hangs on the other.** X16 records
NeoForge's `LoadingErrorScreen` (raised for WARNINGS, so one third-party deprecation notice is
enough). There is a second, and it is worse because it is asymmetric: vanilla's
**`AccessibilityOnboardingScreen`** appears only when `run/<target>/options.txt` does not yet exist.
A target whose run directory has been used before goes straight to the title; a freshly-created one
sits on a prompt nobody clicks, rendering frames, until the watchdog. Measured here: 26.2 PASSed in
10 s while 1.21.1 sat for over three minutes on the same code.

- **Make the wait NAME what is blocking it.** A heartbeat printing
  `tick N on <screen class>` every hundred ticks answered in one run what three rounds of `jstack`
  and theory had not. The stack said `Screen.renderBlurredBackground` → the render thread was busy,
  which is true of a healthy client and of a stuck one; the screen's NAME is the datum. **"The
  client crashed" and "the client is fine and my harness never started" are opposite findings that
  only the log distinguishes** — so make the harness say which.
- **Dismiss both, by simple CLASS NAME rather than by import**, which also keeps the file shared
  across targets: no per-version import, no rename row. Persist the accessibility choice
  (`options.onboardAccessibility = false; options.save()`) so a second launch skips the detour.
- **Turn the menu blur off** (`options.menuBackgroundBlurriness().set(0)`, present on both
  versions). Under llvmpipe that post-chain costs seconds a frame, and a client that is merely
  drawing very slowly is indistinguishable from one that has hung. It changes nothing the gate
  measures.
- ⚠ **The template already knew about the onboarding screen, with a comment saying exactly this**,
  and the new port's hand-written harness did not — X24 pointed the other way. A §W port cannot
  copy the template's harness verbatim (it names `mc.screen`/`mc.setScreen`, which V28 moved), so
  the knowledge has to be carried across deliberately. Both screens are in the template now.

**X34. 🔴 A HARNESS THAT SAMPLES ONCE MEASURES THE MOMENT IT SAMPLED, NOT THE BEHAVIOUR — and
the failure message describes the subject.** · **Pattern:** a Gate-C phase that spawns something,
waits N ticks and then asks whether it is there · **Symptom:** `the client never received the
summon_spot entity, so nothing rendered it` — about an entity the client HAD received, drawn, and
then correctly buried, because it discards itself after 18 ticks and the harness looked at 100 ·
**Fix:** sample every tick and assert on whether it was EVER there. That is also the honest
question — *did this ever render* is the claim a render gate is making; *is it still there* is a
different one, and for anything with a lifetime it is the wrong one.
· **The reason it is worth an entry rather than a shrug** is the shape of the lie: a
single-sample harness fails in the ACCUSING direction, naming the subject, so the first instinct
is to go looking in the entity or the renderer. Nothing in the message says "your window was
wrong". The same trap sits under any assertion about a projectile, a particle, an explosion, a
one-shot animation, or a mob a sibling test will kill — i.e. most of what a spawn phase is for.
· **The cheap check before writing the wait:** read the subject's own tick for a `discard` /
`remove` / lifetime, and compare that number against your window, exactly as §X19 says to compare
a test's radius against the plot spacing. Both constants are right there and nothing else will
ever compare them for you.

**X29. 🔴 A CROSS-VERSION VISUAL GATE NEEDS A BUILT BACKDROP — you cannot compare photographs of
a world the two versions do not generate the same way.** The four-phase Gate C is what proves an
era jump did not break rendering, and on a §W tree its most valuable output is a PAIR of
photographs: the same named frame, from the same pose, on both targets. That only means something
if everything in the frame is under the harness's control.

- **Build a photo studio, do not photograph the world.** Two Minecraft versions do not produce the
  same terrain from the same seed, so a shot of the spawn point compares nothing. The harness
  builds a lit stone room at fixed absolute coordinates high above the terrain, stands the cast on
  marks inside it, and shoots from a fixed pose — so every pixel that is not the mod is identical
  on both targets, and a difference is the mod.
- **Lighting is arithmetic, and an unlit room passes every check.** Light falls a level per block,
  so glowstone in a 14-high ceiling arrives at the floor at level 1: a black photograph that is a
  valid PNG of the right size in the right place. Light the FLOOR as well as the roof.
- 🔴 **A `LivingEntity`'s body and head rotate INDEPENDENTLY of `yRot`.** `moveTo(x,y,z,180,0)` put
  the whole cast on its marks facing away from the camera, and every assertion passed — the
  entities existed, were alive, were where they were told. Set `setYBodyRot` and `setYHeadRot`
  (and their `O` previous-tick twins, or the first frame interpolates from wherever they were).
  This is the visual cousin of R-series: the code ran, returned normally, and produced the wrong
  picture.
- **Freeze the subjects like the camera.** The pose rules (hide the HUD at the START of the settle,
  re-apply every tick, count misplaced frames) are well known; extend rule 2 to the CAST, or the
  two targets photograph different moments of the same scene and every diff is noise.
- **A screenful proves it for a screenful.** The item sheet exists because 1.21.2 moved the
  item→model binding into data (V42b) and a mod that ships none renders every item as the magenta
  cube. At 1:1 a 410-item sheet runs off the bottom at any GUI scale, so it must be SCALED to fit —
  a picture of two thirds of the items is a check on two thirds of the items.
- ⚠ **Do not inherit the template's `MobCategory.MISC` filter without reading what it drops.** It
  is there to skip projectiles; on this mod it skipped the rover, all four rockets and the lander —
  the entities people install the mod FOR. Include everything and let the spawn attempt report what
  it could not build.
- 🔴 **A SPECTATOR sees an INVISIBLE mob as a translucent ghost, so a spectator camera photographs
  every invisible carrier on every arm, and the comparison is a perfect MATCH about the ghost.** Any
  mod that dresses a vanilla mob (invisible, scaled, carrying display entities) is exposed. Measured
  on the content mod: nine creature rows across 1.21.1, 1.21.4 and 26.2 read MATCH with the margin rule
  holding, and the committed crops showed a pale box where a blue dragon should be. The control arm
  had the ghost too, which is exactly why it matched: **a control that is TRUE OF THE BUG is not a
  control.** Photograph such a subject as a creative player with the HUD hidden (F1 through the
  client driver) and a barrier under the camera so it cannot fall. The same probe, toggling one
  client between the two modes at one pose, shows the dragon in creative and the ghost in spectator.
  **Open the PNG before quoting the table**: every number was consistent, and the picture said the
  opposite.
- ⚠ **When each arm LOADS the world rather than building it, freeze the subject ONCE, in a saved
  copy every arm loads.** Freezing per arm, after load, lets a mob take a few steps first, so every
  arm freezes it somewhere else and the pose check (rightly) calls it a rig fault. Boot the world
  once, freeze it (no AI, invulnerable), stop the server cleanly so the save holds the frozen pose,
  and hand that copy to every arm.

**X31. 🔴 A DUPLICATE rename row is invisible to the dead-rule detector — and for a token rule the
second copy silently DISCARDS the first one's replacement.** X1/X10/X11/X22/X23 are all about making
the dead-rule report mean something; this is the one shape that survives every one of them, because
the row in question is not dead. · **Pattern:** two rows in `versions/<t>.renames.tsv` with the same
`from` field, added months apart by people who each grepped for the wrong half of it ·
**Symptom:** none. Hits are keyed by the pattern TEXT, so the first row's match marks the second one
live and the detector reports `dead=0` over a rule that has never independently fired. Measured: two
such pairs sitting in the inherited table AND in `templates/multi-version/` since it was promoted ·
**The half that is a real bug, not just noise:** `build_pattern` collapses the token table with
`{f: t for f, t, _ in renames}`, so when the two rows disagree on the REPLACEMENT the later one wins
and the earlier is dropped with no warning anywhere — a rename you wrote, reviewed and committed
simply does not happen. Both halves fail in the reassuring direction. · **Fix:** a duplicate `from`
is a HARD ERROR at table load, with two different messages: same replacement → "delete one, it is
invisible to the dead-rule check"; different replacement → "only the last takes effect and nothing
reports the loss". A warning would be the X10 failure again. · **The check that proves a deletion was
safe** is X11's and costs one command: `renameRewrites` and the prepared tree must be **identical**
before and after (measured here: 2291 either way, `diff -r` clean, `exempt` 846 → 844). · **The
generalisation:** every instrument in §X keys its bookkeeping on something, and *that key is an
assumption about uniqueness nobody stated*. Ask what your detector would do with two identical
inputs — if the answer is "report them as one", it has a blind spot exactly the size of your
duplicates.


**X32. 🔴 A FALLING error count can hide a SECOND cause wearing the same message — diff the failing
FILE SETS, not the counts.** X15 says compare batch composition rather than test totals; this is the
same discipline for a *data-load* burn-down, where nothing is even pretending to be a test. ·
**Measured:** a 26.2 target logged **140** `Couldn't parse data file` lines. Fixing the datapack half
(§V45's ingredient FORM clash) took it to **36** — which reads as *104 fixed, 36 of the same thing
left*, and is wrong: those 36 had been failing for a second, independent reason all along (§V67, a
Java codec), and the ingredient failure had merely been reported first for each file. The two causes
had **stacked inside one message**, so the count could not distinguish "the same bug, mostly fixed"
from "one bug fixed and another unmasked". · **The instrument is free and is not the count:** capture
the SET of failing ids and the first line of each error, and diff both between runs. A file that
leaves the set is fixed; a file that stays with a **different message** is a second cause you have
just uncovered, and a file that stays with the same one is genuinely unfinished. Three outcomes the
number renders as one. · **Why it recurs on a port specifically:** a resource-layer short-circuit
hides everything downstream of it (§V42c), so a fix is *expected* to change what the remaining
failures are about. A burn-down over data therefore has to be read for its message mix at every step,
where a compile burn-down (§X6) can mostly be read as a number — and the one place a compile
burn-down cannot is exactly the same shape (§X9's per-family bucketing).


**X35. A HANDSHAKE PATH HELD IN TWO PLACES is a green run with no result file.** The Gate-C
launcher watches `$SIG_DIR` and the harness writes its verdict to its own default — two constants
for one path, and nothing compares them. The launcher greps the LOG, so it passes; anything reading
the FILE sees nothing on a perfectly green run, which reads as "the gate did not report" and is the
§X15 confusion in miniature. Pass the launcher's own `SIG_DIR` through (`-Psigdir=…`) rather than
agreeing on a default. Same rule as §X11: a scope you never asserted is a scope that will drift,
and here the two halves drift the first time a multi-version port makes the directory per-target
(§W11) on one side only.
· ⚠ **AUGMENT — and it is not one launcher, it is however many copies of the launcher the repo has.**
The fix landed in the port where X35 was found and in NO other, so a sweep six ports later showed
**17 of 19** `client-boot-loop.sh` copies still not forwarding it. Only the ports whose
`build.gradle` actually READS `-Psigdir` are affected (4 of 19 — the rest ignore an unread `-P`), and
two of those were shipping green Gate-C runs whose verdict file landed in the generic path while the
launcher watched a per-target one. **A per-mod tool is a per-mod bug**: the moment you fix one, ask
the other copies (`for f in mods/*/tools/<script>; do ... done`) and scope the fix by what each
consumer can actually see, rather than patching every copy for symmetry.

· ⚠ **AUGMENT — the FIX to an instrument needs the §X15 both-targets treatment as much as the gate
does, and a green board actively hides that it did not get it.** The `-Psigdir` forwarding above
landed between the two Gate-C legs of one port, so 26.2's verdict went to the per-target dir and
1.21.1's went to the old shared default — both legs printed `PASS mode=spawn`, both were genuinely
green, and the board therefore said nothing at all about a fix that had only ever executed on one
target. **The tell is not in the result, it is in WHERE the result is:** a per-target sig dir
holding a `spawn.log` and no `result` is the whole finding, and it is only visible if you look at
the directory rather than at the verdict. · **Assert the negative too** — after re-running, the
generic path must be ABSENT, not merely stale. "A result exists somewhere" and "the result exists
where the launcher looks" are the two claims this bug sits between, and only the second is the one
the handshake makes. · **The general form, which is X27's invariant one level up:** when you fix an
instrument mid-port, the fix inherits every scope question the instrument had. Re-run it everywhere
it is supposed to work before recording it as fixed, because a fix verified on one target is
indistinguishable, on every dashboard, from one verified on both.

**X36. ⚠ AUGMENT to X27 — a THIRTEENTH fault, and the migration's own guidance is what produces
it: a `@Mixin` target written FULLY QUALIFIED inline is skipped, silently, whole file.**
`@Mixin({net.minecraft.world.entity.animal.cow.AbstractCow.class})` has no import to resolve, so
`audit-mixin-targets.py` `continue`d and never checked that file at all. That is not an exotic
spelling: §V19/§V21 tell a §W rename table to write its replacements fully qualified, because a
table that rewrites expressions cannot add an import — so **every multi-version port produces this
shape the moment a `@Mixin` target moves**, which is exactly when the audit matters most. Measured
on a ~390-file mob mod: 26.2 checked 17 targets against 1.21.1's 19, and one of the two missing was
`CowEntityMixin @Inject createAttributes` — the very target §V44's `AbstractCow` retarget was for.
· **Fix:** treat a dotted target whose first segment starts lowercase as already qualified (a
package segment is lowercase; `Outer.Inner.class` is not). A/B'd both ways and swept over six ports
× two targets for zero new findings.
· **And it was found by COMPARING THE COUNTS, not by a failure** — 19 against 17 on the same mixin
set, with every member count identical in source. §X15's discipline applied to an instrument rather
than to a gate: print the scope, compare it against what you expect, and treat a number that is
small for the tree as the finding.

**X39. ⚠ AUGMENT to X27 — a FOURTEENTH fault, and a §W overlay is what MANUFACTURES it: the
pinned-descriptor block matched `method = {"` with literal single spaces, so a target whose
descriptor sits on its own line was never checked.** · **Pattern:** the two `method=` regexes in
`audit-mixin-targets.py` disagreed with each other — the name-only block (added at fault #10) reads
`method\s*=\s*\{?\s*"`, the pinned-descriptor block still read `method = \{?"`. **That asymmetry
inside one file is the tell** · **Symptom:** X27's usual one — *all targets match*, over a target the
tool never looked at ·
· **Why a multi-version port is where it bites:** a decompiled SHARED file keeps the decompiler's
one-line `method = {"…"}`, while a hand-written per-target OVERLAY wraps the descriptor onto its own
line — so the file the audit skips is, by construction, the one written *because that descriptor
changed*. Measured on a ~320-file boss mod: 26.2 checked **7** targets against 1.21.1's **8**,
and the missing one was `ExplosionMixin @ModifyVariable explode`, whose whole reason to exist is
26.2's changed `ServerLevel.explode` signature (a `WeightedList` argument AND a void return, §R17
shape (f)). It is §X10's *"put `\s*` at every link"* arriving inside the instrument rather than
inside a rename table.
· **Found by COMPARING THE COUNTS, exactly as X36 was** — 8 against 7 over an identical annotation
set, with no failure anywhere. Both halves of the X15 discipline paid: the count is the finding, and
instrumenting *which* targets each target checks is what named it in one run.
· **A/B'd both ways, and the first attempt was invalid in a way worth repeating:** planting the
mismatch in `src/` proved nothing, because the tool reads the PREPARED tree — X27's own A/B note and
§X8b's stale-tree rule, both live at once. Planted in `build/generated/sources/mc26/java` it exits 1
naming the method; restored, exit 0. Swept over all 9 multi-version ports × both targets:
**zero new findings**, which is X10's condition for letting a widening gate anything.
· Fixed in `tools/` and synced into `templates/multi-version/` (§X24).

**X40. 🔴 A RECEIVER-anchored row copied the TYPE-row lookbehind, so it rewrote HALF its family
and the dead-rule detector called it live.** Every §X entry about the dead-rule report — X1, X10,
X11, X22, X23 — is about a row that matches *nothing*. X31 is the first about a row that is not
dead and is still wrong. This is the second, and it is cheaper to make than X31's duplicate.
· **Pattern:** a `member:`-style row anchored on a RECEIVER NAME rather than a type, written with
the `(?<![\w.])` every type row in the table uses:
`re:(?<![\w.])(camera|renderInfo)\.getPosition\(\)` → `\1.position()` ·
**Symptom:** 17 of the 20 call sites are rewritten and three are not. The three are the ones
that reach the receiver through a dot —
`this.entityRenderDispatcher.camera.getPosition()` — which the lookbehind forbids by construction.
The row fires, so `dead=0`; the burn-down falls, so the pass reads as a success; and the three
survivors sit in the error list looking like an unrelated missing member.
· **Why the anchor is right on one kind of row and wrong on the other, which is the whole finding:**
a TYPE row must not fire after a dot or `a.b.Foo` gets half-rewritten by a rule for `Foo` (X2). A
RECEIVER row must, because a receiver is normally reached through one. **The two rule kinds want
opposite anchors and the table offers one idiom**, so the safe-looking copy is the wrong one.
`(?<![\w])` is the receiver form: it still cannot clip `mycamera.getPosition()`, which is what the
anchor is actually for, and it does reach `x.y.camera.getPosition()`.
· **The check, and it is the one X1 cannot do for you:** for a receiver-anchored row, count the
call sites in the SHARED tree yourself (`grep -rnoP '(?:[A-Za-z0-9_]+\.)*receiver\.member\(\)'`)
and compare that against the row's own hit count. A dead-rule detector answers "did this fire";
only that comparison answers "did it fire everywhere it should have". Measured here: 20 sites, 17
rewrites, `dead=0` — a 15% miss that reads as three unrelated errors.

⚠ **AUGMENT — the anchor depends on WHERE THE PATTERN STARTS, and getting that wrong reintroduces
X40 in the act of fixing it.** X40's own example puts the receiver NAME in the pattern
(`(camera|renderInfo)\.getPosition\(\)`), and there `(?<![\w])` is right — it stops the row
clipping `mycamera`. A row that starts at the **dot** is a different animal: the character before it
is the receiver's last character, which at every ordinary call site is a word char, so the same
lookbehind forbids precisely the sites the row exists for. Measured, in the session that wrote X40:
`re:(?<![\w])\.getNormal\(\)` rewrote `getAttachmentFacing().getNormal()` (preceded by `)`) and
skipped `face.getNormal()`, 1 of 2, with `dead=0` throughout. · **The rule:** a lookbehind protects
the START of the pattern, so ask what legally precedes that character — before an identifier the
answer is "not another identifier character"; before a `.` the answer is "anything", and the row
wants no lookbehind at all. · And the check that caught it is X40's own and cost one command:
count the call sites in shared source, count the rewrites in the prepared tree, and require them
equal (`grep -rn '\.getNormal()' src/main/java | wc -l` against
`grep -rn 'getUnitVec3i' <prepared> | wc -l` — 2 and 2 once fixed).

⚠⚠ **AUGMENT — the same arms race in its OTHER direction, where the row does not miss sites but
CLAIMS them, and so creates errors that read as unported API.** X40 and X7b are both about a
receiver-anchored row firing too NARROWLY. Measured on a large boss mod, the same shape fired too WIDELY:
a row routing NBT access through a compat helper was anchored
`(?<![\w.])(compound|tag|listtag|p_\d+_)\.put\(` — and `p_\d+_` is a **decompiler parameter
name**, not a type, so it matched a `HashMap` lambda parameter in two skull renderers. Six map
inserts became `CmValueIo.put(HashMap, Types, Identifier)`.
· **It is X7's over-eager row, and it hides better than a dead one:** the row fires, so
`dead=0`; the errors it creates are `no suitable method found`, which is exactly what real
unported API looks like; and they sit in files nobody associates with NBT. Two full burn-down
passes went by with those six in the list.
· **The fix is to anchor on something the API itself guarantees, not on a name a person or a
decompiler chose.** The NBT API is keyed by `String`, and every one of this mod's 21 `contains` /
14 `put` sites passes a **string literal** while the map inserts pass an enum constant — so
`\.put\("` is the honest discriminator and no future parameter name can slip it. (Keep the
receiver capture free of `()` in the same row: `s.toLowerCase().contains("x")` is four more sites
that must not be touched.)
· **The general test, which is the one to apply before writing any receiver-anchored row:** ask
what your row would match if every identifier in the tree were renamed. If the answer changes,
the row is guessing; look for a discriminator in the ARGUMENTS or the method name instead. And
prove the change either way with X11's count — here `renameRewrites` 5612 → 5606 with a `diff -r`
showing exactly those six lines restored.

**X42. 🔴 An audit can be BOTH blind and unwired, and the two hide each other — `audit-event-listeners.py`
printed a clean pass over the live §X33 defect it exists for, on 6 ports that never ran it anyway.**
§X27 catalogues fourteen faults in one instrument; this is the same invariant in a second tool, and
its value is that both halves were measured rather than argued.

· **The blindness.** Its matcher opened `@SubscribeEvent\b[^\n]*\n` — consume to end of line, then
a run of further annotations. Vineflower emits `@SubscribeEvent(\n   priority = EventPriority.HIGHEST\n)`
for any listener carrying a priority, so `[^\n]*` ate the `(`, the next line was not an annotation,
the match failed, and **that listener was never checked**. It is §X10's *"put `\s*` at every link of
a fluent chain"* arriving inside the instrument instead of inside a rename table. Measured on
a ~660-file GeckoLib mob mod: 64 `@SubscribeEvent` in the prepared tree, **6 in the wrapped form**, tool reported
`checked 58` — and one of the six was `onRenderLevelStage(RenderLevelStageEvent)`, which is
`public abstract` on 26.2 and is exactly the crash the tool was written for. It printed *"no listener
names an abstract event"*.
· **The wiring.** The catalogue said it "is a `check` task on both targets". It was, on **6 of 12**
multi-version ports. So the other six had a correct-but-blind tool that also never ran — §S2's rule
(a step you must remember is not a control) and §X27's fault #4, together.
· **A/B'd in both directions and against §X25c's identity case:** `checked 58` all-clear → `checked 64`
naming the defect; on 1.21.1, the version the source is written FOR, the answer is known in advance
and is clean. Exit codes checked separately and it is worth saying why — a tool that exits 0 on a
finding makes `check` decoration, and `$?` after a pipeline is the exit of `tail`, not of the tool.
· **Swept across all 12 ports × both targets: exactly ONE new finding, zero noise** — which is §X10's
condition for letting a widening gate anything. The wrapped form turned out to be RARE (0% on nine
ports, 4% and 9% on two), which is the sharpest part of the lesson: **a matcher's blind spot does not
have to be common to be fatal, because the code most likely to sit in it is the code someone bothered
to annotate specially.** A listener carrying an explicit `priority` is, by construction, one that
matters.

**X43. 🔴 A rename row that rewrites a SUPER call into the new name recurses forever when a compat
base maps the new name back to the old.** · **Pattern:** the §V53 compat-base design — the 26.2 base's
`extractRenderState` is `final` and calls `this.render(...)`, and its own `render` calls
`super.extractRenderState(...)` — plus the row `re:\.render\(graphics, → .extractRenderState(graphics, `,
which also matches a SUBCLASS's `super.render(graphics, ...)` · **Runtime:** `StackOverflowError`
alternating `Screen.render` ↔ `CompatBase.extractRenderState`, the first frame the screen is shown ·
**Fix:** exempt the super call — `re:(?<!super)\.render\(graphics, ` — because `super.render` IS the
base's 1.21.1-shaped method, which is exactly the right target. · **Measured on a space-exploration mod (~550 files):** the planet
screen, every machine GUI and the radio — i.e. every way to use the mod's content — crashed the 26.2
client, found only when the builder mod's space Gate C opened the planet screen. · **The general form,
and it is §W5b's from the other side:** a compat pair that adapts an OVERRIDE installs a two-way
mapping between an old and a new name; a rename row is one-way. Any row that rewrites the old name to
the new one at a `super.` call site closes the loop. Before writing a rename row for a method some
compat base also re-exposes, ask what `super.<old>` will resolve to after the rewrite.

**X37. A launcher that greps a client log must pass `grep -a`, or a PASS reads as a dead harness.**
A Minecraft client log carries control bytes (the Gradle progress bar, Mixin's own output), so GNU
grep decides the file is BINARY and prints `binary file matches` instead of the line. The Gate-C
launcher then takes its no-verdict branch and reports **"NO VERDICT — the harness never reported"**
over a run that printed `PASS` — manufacturing, from inside the instrument, the exact
crashed-versus-never-started confusion that branch exists to remove (§X16/§X28). Measured on
a ~390-file mob mod on 26.2. One flag, and it belongs on every grep in the launcher, not just the verdict one.


**X41. 🔴 A GUARD THAT MAKES A GATE ROBUST IS A GUARD THAT HIDES A DEFECT — so read a PASSING log
for what the harness quietly handled, not only a failing one.** §X16 and §X28 record that a Gate-C
client which "hangs" is usually fine, sitting on a screen nobody dismissed, and prescribe the fix:
have the harness dismiss `LoadingErrorScreen` and `AccessibilityOnboardingScreen` by class name, in
the shared guard, so every future client test inherits it. That is right, and it silently converts
one whole class of defect from *a stalled run* into *a green run*.
· **Measured:** a large boss mod's 26.2 `spawn` phase passed with `103/103 spawned, 98 drawn` and the log
carried `EXAMPLEMOD_BOOT_TEST: dismissing LoadingErrorScreen` — raised by
`loadwarning.neoforge.onlyin` from **two** mods at once (§V79). Four ports out of eleven were
raising it, one of them a library every dependant loads, and the whole board had been green through
Gate A, Gate B and Gate C on both targets. Nothing had to fail for the evidence to be there; it was
one line in a log nobody re-read after it said PASS.
· **The general form, and it is §X11's invariant pointed at the harness instead of at a check:**
every dismissal, retry, timeout bump, cooldown reset and "wait for it to settle" in a test harness
is an assertion you deleted. They are usually the right call — a gate that fails on someone else's
deprecation notice is a gate people switch off — but each one needs to leave a **line in the log
saying it acted**, and that line has to be read on green runs. A guard that handles something
silently is indistinguishable from a run where the thing never happened.
· **The cheap habit:** after any PASS, `grep -i 'dismiss\|retry\|skip\|WARN\|ERROR'` the log and
ask, of each hit, *what would this have cost if the harness had not swallowed it?* Here the answer
was Gate B dying before a test runs on a dedicated server. Same discipline as §X15's log diff — the
verdict is the smallest thing a run produces.


**X44. 🔴 Under llvmpipe, a Gate C is a TIMING measurement — run ONE at a time, with nothing else
building, and never swap the jars it is reading.** Software GL (the only GL a cloud session has; see the
"every gate is headless" house rule) shares the CPU with everything else on the machine. A second client, a
`compileJava`, or a Gate B beside a running Gate C does not make it fail cleanly — it makes the client
run *behind*, and a client that is behind produces findings that look exactly like physics or sync bugs.
Measured on one 26.2 sweep, all with the code unchanged: a tidal wave 40 blocks behind on the client,
a rider's seat height read mid-tick, a train "not there" beside a freshly teleported player, and a
landing sampled mid-fall. Every one went green when the same test ran alone.
· **And a second, worse form: re-fetching test mods while a client runs.** `fetch-testmods.sh` (and any
staging step) overwrites jars in place, and a running client reads its mod jars LAZILY, so it
half-loads a jar that changed under it — `ZipException`, `NoClassDefFoundError` for a class that
loaded a minute ago, missing textures (§R23, arriving from the harness instead of from a deploy).
· **The rule:** serialise Gate C, stage jars only between runs, and when a client result looks like a
race, re-run it ALONE before diagnosing. Where a check genuinely samples a moving thing, take a median
over a window or wait for the state rather than reading one tick (§X34) — that is a fix to the test,
and it is correct under contention and without it.

**X45. 🔴 A provenance recorder that re-stamps EVERY jar in the folder makes a FAILED rebuild read as
current.** `releases/<mc>/PROVENANCE.tsv` exists to answer "is this committed jar still the source?"
(its tree hash). `build-releases.sh` called `releases-provenance.py record` once at the end, and
`record` stamped every jar in the folder with the port's CURRENT tree hash. So when one port's build
failed (a Central 429 on `javazoom:jlayer`) and its old jar stayed in place, that old jar was recorded
against the new source and `check` reported it current. That is the exact lie the file exists to
prevent, and it fails in the reassuring direction. · **Fix:** `record --only <jar…>` re-stamps only the
jars this run actually copied and keeps every other row as it was. `build-releases.sh` collects those
names as it copies. · **The general form:** a bookkeeping step that runs after a batch must be scoped to
what the batch DID, not to what the folder HOLDS, or a partial failure is written down as a success.

**X46. A GameTest that USES an item on the plot floor must clear the column above first — the
template's floor is not air.** · **Pattern:** a spawn-egg `useOn` test aimed at a grid of positions on
the test structure's floor · **Symptom:** one egg returns `FAIL` and reads as an egg bug: the level-13
wraith egg "refuses to spawn". It had landed on the template's polished andesite, and that egg correctly
refuses a non-replaceable spot (vanilla eggs just spawn inside the block) · **Fix:** set the two blocks
above the target to AIR before counting, and log the block that was there when a use fails. One egg in
a list of eight failing is the tell. A mod bug would usually fail the others too.

## Legality / scope note
Migrations here are for running mods on the user's own machine. Many mods are
"all rights reserved"; do not redistribute a ported jar publicly without the
original author's permission.

NN. **`defineId` registered against the WRONG class (runtime crash, decompile artifact)** · **Pattern:** `public static final EntityDataAccessor<Integer> LIFE_TICKS = SynchedEntityData.defineId(SomeOtherEntity.class, EntityDataSerializers.INT);` in a class that is **not** `SomeOtherEntity` · **Runtime:** `java.lang.IllegalArgumentException: Data value id is too big with 28! (Max is 8)` thrown from `SynchedEntityData$Builder.define` inside the entity's `defineSynchedData`, i.e. **at construction** — so the server dies the moment that entity is ever spawned (a ~390-file mob mod: every time one golem boss summoned a mine) · **Fix:** `defineId` must always name **the class that declares the field**; ids are allocated per class-hierarchy, so naming another class hands you that class's id while your own builder is sized for yours. **Scan:** `grep -rn 'SynchedEntityData.defineId(' src/main/java` and flag any file whose `defineId(X.class` does not match its own class name — EXCEPT mixins, which legitimately define against the vanilla class they inject into. **Gate:** a `@GameTest` that simply *spawns* the entity catches it (verified to fail with the exact message on the unfixed code).

**X47. 🔴 Gates that only ever compile `src/main` cannot fail on the author's OTHER artifacts — end every
port with the author's own build, untouched.** · **Pattern:** a mod that ships several jars from one tree (a
CurseForge variant, a "pro" build, a JVMTI backend), each from its own source set, all assembled by the author's
`build` · **Symptom:** none, on every gate. An upstream port reported green Gate A, Gate B and Gate C while
`./gradlew build` failed with 38 errors in two variant source sets that no tool, burn-down or gate had compiled
— so the first contributor to clone the branch would have hit BUILD FAILED · **Fix:** every tool, compile count
and burn-down covers every source set the build declares (`tools/srcsets.py`), and the pipeline's last check is
the author's `build` plus every Jar task they registered, run with NONE of the port's harness (only the
Central-mirror init script, which rewrites repository URLs). Two more things the same check surfaced, both silent:
a worker "fixed" a moved interface by deleting `implements` and calling the type gone (it had moved to
`net.neoforged.neoforgespi.earlywindow`; the `META-INF/services` file had to move with it), and a
never-transform whitelist still named `"net.minecraftforge."`, which on NeoForge protects nothing — now a
codemod rule. And pass the TARGET from the build: a tool default had told 26.x workers they were porting to
1.21.1.

**X48. 🔴 A deterministic stage wired into ONE porting route is a cost the other route pays to a model — keep
each hop's tail in one shared stage both routes call.** · **Pattern:** a converter or fix-up tool built while
measuring one route (here the fork route, `port-upstream.py`) and called from that route's own code · **Symptom:**
none on the route that has it; on the other route (the jar route, `port.py`) every error the converter would have
removed goes to the compile loop's workers, priced like real porting. Nothing fails: the burn-down is just higher.
It was found by noticing a README sentence could not be written truthfully for both routes · **Measured** (26.2
era hop on finished jar-route 1.21.1 ports, no model): 145 → 78 errors (−46%) and 506 → 73 (−86%), almost all of
it the tool-tier/armour converter and the changed-hook-signature converter. The same audit found the jar route's
Forge hop missing the access-transformer fix (a Forge AT in SRG names was copied unconverted, §139), Forge code
shapes, SimpleChannel → payloads and the Holder fixes · **Fix:** `tools/mechanical-hop.py` holds both stages with
one list each; both routes call it, and `tools/test-port-tools.sh` fails if either stops, runs a stage tool
itself, or a converter on disk is in no list. · **Also found:** the jar route's era hop leaves GeckoLib at its
1.21.1 version, so a GeckoLib mod's 26.2 build asks for an artifact that does not exist (§V10) and the compile
never starts; the fork route bumps it in `tools/targets.py`. Same shape, one layer down: a build step one route has.

**X49. 🔴 A gate that only asks "did it crash" passes over everything the game CAUGHT — read a green client's
log for the mod's own lost assets and logged exceptions, and record what the original already had.** ·
**Pattern:** a Gate C verdict taken from the harness's PASS line alone · **Symptom:** none; every phase green.
Measured on one full mod set (a 200-mob battle, 60 s): a library's force-removal threw
`UnsupportedOperationException` 18 times and logged it, leaving entities half-removed; one mod had two entity
textures, a sound file and a sound event missing, a GeckoLib animation that failed to parse (`'-'` as a value)
and an entity with a spawn entry but no spawn placement; another had armour icons whose trim textures were
never added to the block atlas. All behind PASS, and the logged exception sat 30 lines above a disconnect a
player hit later · **Fix:** `tools/gate-loop.py`'s Gate C verdict now reads the passing log too
(`log_findings`): the mod's own `Failed to load texture`, `Missing textures in model`, `File <ns>:sounds/…
does not exist`, `Missing sound for event`, GeckoLib `Unable to find model/animation` and `Unable to parse
animation` (attributed by searching the mod's own animation files), entities listed as lacking a spawn
placement, and any logged exception whose first non-platform frame is in a package of the mod's `@Mod`
class (frames are read past the `LAYER/module@ver/` prefix; harness classes are ignored). Every pattern is
scoped to the mod's namespace or package, so another mod's noise is never the port's · **Pre-existing is not
the same as fine, and not the port's to hide:** all of the findings above were in the authors' original
1.20.1 code (checked against their branches, and for the removal against 1.20.1's own bytecode). A defect
the original already has goes in the fork's `.github/gatec-known.txt` -- one substring per line with
`# why` -- so the run lists it, says so in its detail, and stays green; the same line is the trail for
anyone who later fixes it. A finding with no such line is red. · **And the trigger can be in another mod:**
a library's own gates may never call the broken path (here the removal bug fired only when a dependent's mob
used the library's API), so a dependent's run lists logged exceptions thrown in OTHER mods' code as a
non-failing warning (`foreign_findings`; ci-gates prints them and names one in its summary row). File each on
that mod's fork -- it is not the dependent's failure, and dropping it is how this one stayed hidden.

**X50. 🔴 A scope guard keyed by a NON-UNIQUE id switches off every group sharing it — `#? only-if` in a
recipe pack silently disabled two unrelated groups.** · **Pattern:** several recipe groups implement the same
catalogue entry (`#@ R21`, `#@ 104`, `#@ 103` each appear more than once), and one of them carries an
`only-if` · **Symptom:** the other groups report DEAD on files they plainly match; a standalone test of the
row matches and the pack does not. Measured: a new `getMobArrow` row (R21) died because the arrow-pickup
group (also R21) is scoped to arrow files, and a `renderColoredCutoutModel` row (104) died behind the
`renderToBuffer` group's scope. On a real port this also kept an existing networking group off, so two
`PacketDistributor` rewrites it was written to make never happened · **Fix:** `tools/apply-recipes.py` keys
groups by `<id>@<line>`, so a scope belongs to the group that declares it. · **General form:** any
per-item switch must be keyed by something the item owns. An id that names a *lesson* is shared by design;
a scope applies to *rows*.

**X51. 🔴 A pre-stage that PARKS what the compiler will choke on makes every later count lie in the good
direction — `park-optional` parked the source loader's own API as an "optional integration".** · **Pattern:**
the parker calls a file an integration when it imports a package outside the platform list, and
`net.minecraftforge.` was not on it, so any file still naming a Forge type at setup (networking on
`SimpleChannel`, a `ConfigScreenFactory`, commands) was moved out of the build · **Symptom:** a LOWER error
count and a port that compiles without its networking. Measured: 17 files of one library (its whole
`message/` package, its commands and client events) and the config-screen handler of a mob mod, whose main
class then failed on the missing import. The SimpleChannel converter, which exists for exactly those files,
reported "0 files changed" because they were already gone · **Fix:** `net.minecraftforge.` is platform
(`tools/park-optional.py`): it is what the recipe pack and forge-shapes port. · **How to measure across such
a change:** compare the errors in files BOTH versions compiled, and report the newly-visible files as their
own number. Here the like-for-like count fell (57 → 56) while the total rose by 142 errors that the old
setup had been hiding, not causing. A before/after whose denominators differ is not a regression report.

**X52. 🔴 A seam that stands in for a removed vanilla INTERFACE must keep its shape AND its simple name —
repoint the import, never rename the type.** · **Pattern:** the natural first cut of a MultiBufferSource
converter rewrites every `MultiBufferSource` to the seam class, `Buffers` · **Symptom:** a parse abort and a
cascade, measured on a boss-effects library: `MultiBufferSource forced = ignored -> delegate.getBuffer(...)` is a LAMBDA
(vanilla's type is a functional interface), which a class cannot be the target of; and
`MultiBufferSource.BufferSource x = MultiBufferSource.immediate(new ByteBufferBuilder(4096))` lost a closing
paren to a lazy `[^;]*?\)` rewrite · **Fix:** generate `<package>.MultiBufferSource` with vanilla's own shape
(`@FunctionalInterface`, a nested `BufferSource` with `endBatch`, a static `immediate(Object)`), have the recorder
implement it, and change only the import. Every lambda, cast, generic bound and nested-type reference then stays
as written, and the diff is one line per file. · **The general rule:** when a removed API was an interface, the
replacement must be one too, under the same simple name — a mod uses an interface in more syntactic positions
than a grep for the name shows. And a call rewrite that has to cross nested parentheses needs a paren matcher,
not a lazy regex: `tools/burndown-count.sh` exited 4 (parse abort) on the first run, which is the only reason
this read as a bug and not as "53 errors fewer".


**X53. 🔴 Gradle's own javac integration is QUADRATIC in the error count — past a few thousand errors the
count does not come back at all.** · **Pattern:** an uncapped compile (`-Xmaxerrs 100000`, which is what a
burn-down needs) of a large raw decompile · **Symptom:** no result. The javac worker finishes in seconds and
then blocks forever writing its diagnostics back to the daemon, whose 6 GB heap is full; the caller times out
40 minutes later and reports a stage failure. Measured on a 1,640-file 26.2 port with 2,448 errors: a cap of
2,000 finished in 56 s and still wrote a 266 MB `build/reports/problems/problems-report.html`, while the
uncapped run hung · **Cause:** Gradle 9 keeps every diagnostic as a `Problem` whose details carry the compiler
output accumulated so far, so memory grows with the square of the count. There is no switch for it
(`org.gradle.internal.problem.summary.threshold` changes nothing). · **Fix:** never ask Gradle for the whole
list. `tools/maxerrs.init.gradle` caps the integrated compile at 1000 and adds `portFullErrors`, which runs the
SAME javac (the task's toolchain, classpath, sources, args) outside Gradle; `port_gates.compile_log()` runs it
only when the cap was hit and appends it to the same log. The direct run took 10 s for the full 2,448.
`tools/burndown-count.sh` exits 6 on a log that hit the cap with no recount, so a prefix cannot pass as a count.

**X54. A datagen package stays in the build when MAIN code wires it — park the wiring, not the class.** ·
**Pattern:** the mod's main class holds `private void generateData(GatherDataEvent e)` (registered with
`addListener(this::generateData)`), or registers a separate subscriber (`addListener(DataGen::gather)`) ·
**Symptom:** the never-park-what-main-code-names rule (which keeps runtime code out of `parked/`) keeps every
provider, because main code names them through the handler. Measured: 18 datagen files on a 26.2 port, nine of
them against NeoForge's removed `client.model.generators` — real-looking errors a worker would be paid to "fix"
in code the game never runs · **Fix:** `tools/park-optional.py` cuts the handler method or the registration
line (plus the imports left unused), records it in MIGRATION.md, and judges references on the cut text. A class
in the datagen package that runtime code still names (a block entity reading a loot-table key) stays.

**X55. 🔴 Dependencies come from the JAR'S OWN toml as well as the registry, and each one is checked against the
target before the port starts.** · **Pattern:** a registry project page lists fewer required dependencies than
the jar declares, or lists a dependency whose newest build is for another Minecraft · **Symptom:** a compile
full of "package does not exist" that looks like porting work, or a port finished against a library that has
no build for the target and can never load · **Fix:** `tools/port.py` resolves every required mod id from
`neoforge.mods.toml`/`mods.toml` the registry missed (accepting only a jar whose own toml declares that id),
reads each dependency jar's own `minecraft` versionRange, and STOPS (exit 21) naming any required dependency
with no usable build for the target. An open-ended range (`[1.21,)`) cannot prove a mismatch, so it is accepted.

**X56. The source build is a choice, and a wrong one costs a whole unpacked hop.** · **Pattern:** a project
publishes several loaders and Minecraft versions; the newest build is not always the one a route can use, and
a "NeoForge" listing can be a Fabric jar or a jar with no loader metadata at all · **Fix:** `tools/port.py`
identifies a jar by its sha1 (`modreg identify`), prefers a build whose route has a recipe pack (same Minecraft
on another loader first, then older versions newest-first) and says which it chose, reads the loader from the
jar (Fabric when `fabric.mod.json`/`quilt.mod.json`), and stops (exit 23) on a jar that carries no loader
metadata instead of assuming Forge.

**X57. A NeoGradle execution cache records ABSOLUTE paths into the workspace that first ran it.** · **Pattern:**
port workspaces are created, run and deleted one after another (a sweep, a bench) · **Symptom:**
`NoSuchFileException …/.gradle/repositories/ng_dummy_ng/…` at the Minecraft recompile of the NEXT port, which
reads like a broken build of that port · **Fix:** `tools/mechanical-hop.py` heals it: on that error it copies the
referenced files to a stable place under the Gradle home, rewrites `caches/ng_execute/*/libraries.txt` and the
workspace's own copies, stops the daemon (which caches the entry) and retries. If the mod's OWN sources fail
there instead, it says so rather than blaming the recompile.

**X58. Decompiler output that does not parse is fixed at setup, before any count.** · **Pattern:** Vineflower
emits a local class as `class 1Name` and an assertion guard as `<unrepresentable>.$assertionsDisabled` (§A2)
· **Symptom:** a parse abort, so the burn-down reports a handful of errors in a tree that has thousands
(burndown-count exit 4) · **Fix:** `tools/port.py` setup renames digit-prefixed local classes to `_Name` and
replaces the guard with `true`, and records how many it fixed.

**X59. Splitting a two-phase tick handler leaves an `if (true)` / `if (false)` else-chain to fold, and a fold
that deletes too much compiles.** · **Pattern:** a Forge `onClientTick(TickEvent.ClientTickEvent e)` with
`if (e.phase == START) {…} else if (e.phase == END) {…}`, which `tools/forge-shapes.py` splits into `Pre` and
`Post` handlers by substituting the phase test with a constant · **Symptom:** an unbalanced brace (a parse abort),
or code before the `if` on the same line silently removed · **Fix:** forge-shapes folds `if (false) {X} else REST`
to REST and `if (true) {X} else <whole chain>` to X, walking the chain to its real end, and never touches text
before the `if`.

**X60. Registry metadata is a hint; the JAR decides — six ways a sweep found it wrong.** · **Pattern:** a port
starts from what a registry (Modrinth/CurseForge) says about a build: its loader, its Minecraft version, its
required dependencies · **Symptom:** a port that stops on a dependency it can have, routes as the wrong loader,
or reads the wrong mod id — each found on a popular mod, each reading as "this mod cannot be ported" · **Fix**
(`tools/port.py`, with a self-check for each):
- *Dependency data can be the other loader's* (a Forge build listing a Fabric project as required). The jar's own
  (neo)forge toml is the authority: a registry-only dependency with no target build is a note, a toml-required id
  with no build still stops, and dependencies are wired before setup so that stop is cheap.
- *One project per loader*: the dependency points at `<slug>` (Fabric) while the NeoForge build is `<slug>-forge`.
  Sibling slugs are tried before a dependency is called missing.
- *A toml-only dependency id the full-text search cannot find* (`farmersdelight`): try it as a slug with the hyphens
  put back, then search it with underscores as spaces; a build is accepted only if its own toml declares the id.
- *Single-quoted TOML is legal* (`modId = 'x'`); a reader that only takes double quotes misses the id.
- *A multi-loader jar carries a toml and a `fabric.mod.json`*: the toml is the one the port runs on; the Fabric file
  only fills gaps (its id can differ and can be illegal for NeoForge).
- *A version range's trailing zero* (`[26.2.0,)`) must admit `26.2`.
- *A mislabelled upload* (a `…-FORGE-….jar` that holds only `fabric.mod.json`) is named in the stop.

**X61. Four kinds of jar that are not "a Forge or NeoForge mod with Java in it", each with its own answer.** ·
- *NeoForge 1.20.1 IS Forge*: NeoForge's first line was the Forge fork (mods.toml, `net.minecraftforge`, SRG), and some
  such jars carry a `neoforge.mods.toml` too. The CLASSES decide: Forge references and no `net.neoforged.neoforge`
  ones take the Forge route, and source selection rates a NeoForge 1.20.1 build by that route from the start.
- *Resource-only* (a structure or datapack bundle, `modLoader = 'lowcodefml'`, no classes): setup takes `assets/` and
  `data/` from the jar, runs the data conversions, and generates a one-line `@Mod` entry class so it loads as javafml
  and the gates have a package.
- *Self-loading multi-loader* (a Forge `IModLocator` service plus nested per-version jars): there is no single mod
  source; the port stops saying so.
- *Fabric labelled as Forge*: X60's last item.

**X62. Vineflower can leave a constructor split: `X v = new X;` … `v.<init>(args)`.** · **Pattern:** a constructor
whose arguments needed temporaries (a switch expression per argument) · **Symptom:** `'(' or '[' expected` — a parse
abort, so the count is not a count · **Fix:** setup removes the bare `new X;` and rewrites the `<init>` call as
`X v = new X(args);`, when nothing between them touches `v` (the statements between compute the arguments).

**X63. A converter that forwards calls by NAME rewrites calls into the mod's own classes.** · **Pattern:** a
converted 26.x input handler (`mouseClicked(MouseButtonEvent, boolean)`) forwards to `child.mouseClicked(...)`,
and `child` is the mod's own widget whose method still has the 1.21 `(double, double, int)` shape · **Symptom:** the
converter RAISES the count (77 conversions, 437 → 475 on one port), as `required: double,double,int` · **Fix:**
`tools/convert-gui-hooks.py` collects hook names the mod declares itself in the old shape and rewrites only
`super.*` calls to those. `tools/mechanical-hop.py` now marks any step that raises the count, because a step that
makes things worse otherwise reads as one more number in a list.

**X64. A machine that ports many mods in a row runs out of disk and memory unless each port cleans up.** ·
**Symptom:** `No space left on device` mid-sweep, and `Gradle build daemon disappeared unexpectedly` (the kernel's
OOM killer) on a compile that is fine alone · **Cause:** every Forge-hop port leaves its own ~73 MB Minecraft
recompile in `~/.gradle/caches/ng_execute` (its access transformer changes the hash), and every NeoGradle or MDG
port leaves an idle daemon holding gigabytes for hours · **Fix:** `tools/port.py` stops its workspace's daemons
when a port ends, however it ends. Prune per-port `ng_execute` / `neoformruntime/intermediate_results` entries
between ports on a long run.

**X65. A Kotlin mod decompiles to Kotlin.** · **Pattern:** a mod written in Kotlin (it depends on Kotlin for
Forge) · **Symptom:** setup produces `.java` files full of `var x: T = …` and backtick names, the compile is a wall of
parse errors, and the stage fails far from the cause · **Fix:** `tools/port.py` counts classes carrying
`kotlin/Metadata` and stops at triage when they are the majority: porting it into Java would be a rewrite, not a
port. Measured: 5 of 40 mods in one popularity batch.

**X66. Vineflower can leave a Java 9+ string concatenation unfolded.** · **Pattern:** a `"…" + x` concatenation
compiled to `invokedynamic` · **Symptom:** `StringConcatFactory.makeConcatWithConstants<"makeConcatWithConstants",
"<recipe>">(args)`, a parse abort · **Fix:** setup rebuilds it from the recipe (each `\u0001` is the next argument,
the rest literal text): `("" + "lit" + (a) + …)`. A recipe with `\u0002` (a bootstrap constant) is left alone.

**X67. An access transformer that widens a vanilla method breaks NeoForge's own override of it.** · **Pattern:**
the mod's AT makes `SpawnEggItem.getDefaultType()` public · **Symptom:** Minecraft's recompile fails in
`net/neoforged/neoforge/common/DeferredSpawnEggItem.java: attempting to assign weaker access privileges` · **Fix:**
NeoForge's classes are recompiled in the same step, so `tools/fix-access-transformer.py` widens their overrides
exactly like Minecraft's (it only read `net/minecraft` paths).

**X68. What a mod's toml calls optional, and what its own package is, have to be read from the code.** ·
- *`mandatory=false` on a dependency hundreds of classes use* (MCreator's default): park-optional parked 706 files
  of one mod as "optional integrations". A declared dependency referenced by at least 20 classes (or a tenth) is now
  required: wired onto the classpath, and its code kept. GeckoLib is kept whenever the build supplies it.
- *A second root package*: an `@Mod` class in `org.x.zetaimplforge` made `org.x.zeta.*`, the mod's core, look
  foreign, and 312 of its own files were parked; a shaded library bundled in the jar likewise. A package present in
  the decompiled tree is the mod's own.
- *The `@Mod` class itself* importing an optional API (Curios) was parked, taking the entry point and the package
  every converter needs with it. It is never parked; its integration references are compile-loop work.
- *`mods = [ { modId = … } ]`* (an inline array of tables) is legal TOML; metadata is read with a TOML parser now.

**X69. A rename row's argument must be a BALANCED argument list, and every unquoted parse error is a parse
abort.** · **Pattern:** `re:InteractionResultHolder\.success\([^;]*\)` on
`c ? InteractionResultHolder.success(s) : super.use(level, player, hand);` · **Symptom:** the greedy class ran to the
last `)` and deleted the else branch (`c ? InteractionResult.SUCCESS;`), and burndown-count, which knew only quoted
`'x' expected` parse errors, read javac's `: expected` as a count of **1** on a 1,000-file port · **Fix:** such rows
match `\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)`; burndown-count matches the whole `<tokens> expected` family. An
audit of every earlier sweep result for a hidden parse abort found only the three already known.

**X70. Three more decompile leftovers, and a report of what setup could not repair.** ·
- *Diamond cast*: `(Map<>)x` is not legal Java (`illegal start of type`). Setup drops the empty type arguments.
- *An interface with an empty assertion-status `static {}`*: the guard removal from X59 leaves `static { }` in an
  interface, which javac rejects (`initializers not allowed in interfaces`, now in the parse family). Setup deletes
  it.
- *Whatever is left*: setup now counts the decompiler's failure markers by kind (an unrecovered method body, an
  unresugared constructor, an unfolded concat) and puts the counts in its line and in the port info as
  `decompile_markers_left`. A marker that survives setup is a method that is wrong or empty, not a compile error,
  so the count is the only place it shows.
- *A library the build supplies from `libs/`* (a jar or a nested jarJar) is not foreign either: park-optional reads
  the packages in those jars as well as the decompiled tree, so a shaded or bundled library stops being parked.

**X71. Three pipeline steps that silently did nothing on one route.** ·
- *The type-aware member renames never applied on a 26.2 hop.* javac reports errors against the generated copy
  (`build/generated/sources/<overlay>/java`), and `fix-missing-members.py` skipped every file outside `src/`. It
  printed `0 member site(s) renamed` on every sweep, which reads like a mod with nothing to rename. Errors on the
  generated copy are now mapped back to the source file they came from (the overlay's copy first).
- *An access transformer that breaks Minecraft's recompile stopped the stage before the AT loop could repair it*:
  the start compile "never reached javac", which was treated as fatal. With an AT present it now goes to the loop.
- *The stale NeoGradle cache heal (X57) ran only for a mod with an access transformer*; a mod without one stopped
  on the same stale entry. The heal now runs either way.

**X72. An Architectury multi-loader jar's `@ExpectPlatform` is inert, and it must not cost the mod its own
facade.** · **Pattern:** a shared class `PlatformUtil` whose static methods carry `@ExpectPlatform`
(`dev.architectury.injectables.annotations`) · **Symptom:** park-optional parked the class as an integration (the
annotation's package is foreign), and 76 references to the mod's own platform facade became errors · **Fix:** the
build already rewrote each annotated method to call `<pkg>.<loader>.<Name>Impl` (the bytecode says
`invokestatic …/forge/PlatformUtilImpl`), so setup strips the annotation and its import, and the class is ordinary
code. Google's `@AutoService` is the same case (build-time only; setup already copies the `META-INF/services`
file it generated) and is stripped too.

**X73. The Forge route had no owner-resolved member renames at all.** · **Pattern:** one of the commonest Forge
leftovers, `FoodProperties.Builder.saturationMod(f)` (`saturationModifier` since 1.20.5), sat in 8 of the swept mods
with ~500 sites, alongside `alwaysEat`, `AttributeModifier.Operation.ADDITION`, `Holder.get()`, the Forge reach
getters on `Player` · **Symptom:** they reach the compile loop as unported API · **Fix:** a text rule for a bare name
like `.get(` or `.normal(` would hit every other class's member of that name, so these go in
`tools/recipes/forge-1.21.1-members.tsv`, applied by `fix-missing-members.py` (as on the 26.2 route) only where
javac names the owner. The Forge stage runs it beside `fix-holders` until neither changes anything. Each row is
checked against the 1.21.1 jar; a rename whose target depends on arity (`overlayCoords`, `uv2`) stays with
`forge-shapes`, which sees the arguments.

**X74. A library Minecraft itself ships is not an optional integration.** · **Pattern:** a mod's config class
imports `joptsimple.internal.Strings` (jopt-simple is on every Minecraft classpath) · **Symptom:** park-optional
called it foreign and parked the config class; ~100 references to it became errors · **Fix:** the platform list now
names every library root the 1.21 / 26.x game classpath carries (jopt-simple, lz4, noexception, MixinExtras' new
group, jline, beside Guava, fastutil, Netty, JOML…).

**X75. A universal jar has no single source tree.** · **Pattern:** one jar for Forge, NeoForge and Fabric across
Minecraft versions (a `mods.toml`, a `neoforge.mods.toml` and a `fabric.mod.json`; classes calling all three loaders'
APIs, Fabric's in intermediary names; mixin packages per era, `legacy`/`modern`/`vintage`) that picks a path at
runtime · **Symptom:** the 26.2 hop "ran" and left 318 errors, nearly all old Forge packages and 306 unmapped names,
none of it portable code · **Fix:** triage counts the loader families the classes reference (each in at least two
classes) and stops on all three, pointing at the source repository, which builds each target separately. Over every
jar swept so far, exactly one reached three; a jar with two (Forge plus Fabric references, or NeoForge plus Fabric
intermediary names) is ported as before.

**X76. A (Neo)Forge jar can carry intermediary-named code.** · **Pattern:** a multi-loader build whose NeoForge jar
bundles classes still in Fabric's intermediary names (`net.minecraft.class_3675`, `method_1234`) · **Symptom:** setup
reported 1951 and 394 "unmapped names left" on two NeoForge jars, and every such class failed to compile · **Fix:**
intermediary ids are globally unique, so setup now applies the intermediary remap to whatever survives the route's
own remap, on every route.

**X77. The name-map builders dropped every member of an UNobfuscated class.** · **Pattern:** Mojang's mappings
leave a few classes unobfuscated (`net.minecraft.server.MinecraftServer -> net.minecraft.server.MinecraftServer`);
the "obfuscated" name then keeps its dots, while `joined.tsrg` and tiny files write it with slashes · **Symptom:** the
SRG and intermediary joins missed the class and every descriptor naming it: ~900 SRG names, all of
`MinecraftServer`'s methods (`getPlayerList`, `getLevel`, `getCommands`…) among them, left as `m_129921_` — 295 in
one mod, 221 in another. CATALOG §I #54 recorded a handful of them as "Forge-injected" and fixed them by hand ·
**Fix:** both builders normalise obfuscated names to slashes (the SRG map grows 65368 → 66271, no existing entry
changes), stamp the map `"__schema__": 2`, and `port.py` rebuilds a cached map without it. Two network facts came
with it: both builders now read the version manifest from piston-meta (launchermeta is blocked, §V0), and
`maven.fabricmc.net` is not reachable here, so the intermediary builder falls back to FabricMC's own repository
(`raw.githubusercontent.com/FabricMC/intermediary`, tiny v1, converted) — without it the intermediary remap had
never been able to run in a cloud session.

**X78. A jar with one tree per loader, and parked mixins left in their configs.** ·
- *A tree per loader*: one NeoForge jar kept a full Fabric copy of the mod relocated under `fabric.com.x…` beside
  `neoforge.com.x…`. park-optional parked only the Fabric files that import a Fabric API; the rest of that copy
  (its packets, its utilities) stayed and failed. A `fabric/` or `quilt/` directory with a `neoforge/` or `forge/`
  sibling is now parked whole.
- *A parked mixin was still listed in `<mod>.mixins.json`*: Mixin fails at load on an entry whose class is gone,
  and only Gate A would have said so. park-optional now drops parked classes from every mixin config and records
  them in MIGRATION.md.

**X79. Vineflower can run out of memory and silently drop a class.** · **Pattern:** a 3-MB mod jar with a
1300-line block-registration class · **Symptom:** the decompile "succeeded"; the log held only the tail of an
`OutOfMemoryError` (setup kept the last 4000 characters of each tool's output), and the registration class simply
did not exist, so 2401 of 3273 errors were `package BlockRegistration does not exist`. The previous run of the same
jar had the class: under memory pressure, which class is lost varies · **Fix:** Vineflower gets `-Xmx6g -Xss16m`
(the JVM default, a quarter of a 15-GB machine, was not enough); setup then compares the jar's top-level classes
with the files written and retries any missing one, with Vineflower at 8 GB and then CFR (`--jarfilter`), and logs
what is still missing. Tool output in setup.log keeps its first 2000 characters too, where a stack trace names its
exception.

**X80. A mixin's self-cast loses its `(Object)` hop in the decompile.** · **Pattern:** source `(Player)(Object)this`
in a `@Mixin` class compiles to one `checkcast`, so Vineflower writes `(Player)this` · **Symptom:**
`incompatible types: ServerPlayerMixin cannot be converted to Player` (§162), in every mod that does it ·
**Fix:** setup restores `(X)(Object)this` in every file carrying `@Mixin`.
