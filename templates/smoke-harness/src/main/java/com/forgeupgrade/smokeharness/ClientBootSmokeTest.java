// ─── Smoke-harness client boot test (generic, namespace-parameterized). ─────────────────────────
// Unlike the per-mod migrate-mod version, this drives ARBITRARY prebuilt mods that were injected on
// the runtime classpath via -Psmokejars. It does NOT reference any mod-specific class — it iterates
// BuiltInRegistries.{ENTITY_TYPE,ITEM,BLOCK,MOB_EFFECT} filtered to the target namespace(s), which are
// passed in via -Psmokens="modid1,modid2" (→ system property smokeharness.namespaces). If no namespaces
// are given, launch/spawn/battle/gauntlet still run but simply find nothing to act on — so the run still
// validates client LOAD + world creation. There is deliberately NO OPEN_GUIS gauntlet step: we can't
// know an arbitrary jar's Screen classes at compile time.
//
// Modes (via -Ptestmode / smokeharness.testmode): LAUNCH | SPAWN | BATTLE | GAUNTLET | CENSUS. Prints
// "SMOKEHARNESS_BOOT_TEST: PASS" and exits 0 on success (the marker tools/client-boot-loop.sh greps for).
package com.forgeupgrade.smokeharness;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.function.Function;
import java.util.stream.Collectors;

import net.minecraft.client.CameraType;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.GuiGraphics;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.gui.screens.TitleScreen;
import io.netty.buffer.Unpooled;
import net.minecraft.client.server.IntegratedServer;
import net.minecraft.network.RegistryFriendlyByteBuf;
import net.minecraft.network.chat.Component;
import net.minecraft.core.BlockPos;
import net.minecraft.core.RegistryAccess;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.util.RandomSource;
import net.minecraft.world.Difficulty;
import net.minecraft.world.effect.MobEffectInstance;
import net.minecraft.world.effect.MobEffects;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.Mob;
import net.minecraft.world.entity.MobCategory;
import net.minecraft.world.entity.MobSpawnType;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.item.ArmorItem;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.TooltipFlag;
import net.minecraft.world.level.GameRules;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.LevelSettings;
import net.minecraft.world.level.WorldDataConfiguration;
import net.minecraft.world.level.levelgen.WorldDimensions;
import net.minecraft.world.level.levelgen.WorldOptions;
import net.minecraft.world.level.levelgen.presets.WorldPresets;
import net.neoforged.api.distmarker.Dist;
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.fml.common.EventBusSubscriber;
import net.neoforged.neoforge.client.event.ClientTickEvent;

/**
 * Generic client BOOT + WORLD smoke test for the smoke-harness. Drives the real client (mixins,
 * renderers, model bake, FMLClientSetupEvent, entity ticks, item render) which a headless GameTest
 * cannot. Acts only when -Dsmokeharness.boottest=true; targets the namespaces in
 * -Dsmokeharness.namespaces (comma-separated).
 */
@EventBusSubscriber(modid = SmokeHarness.MODID, value = {Dist.CLIENT})
public class ClientBootSmokeTest {
    private static final String P = SmokeHarness.MODID + ".";
    private static final String TAG = "SMOKEHARNESS_BOOT_TEST";

    private enum Mode { LAUNCH, SPAWN, BATTLE, GAUNTLET, CENSUS }

    private static final boolean ENABLED = "true".equals(System.getProperty(P + "boottest"));
    private static final Mode MODE = resolveMode();
    /** Target mod namespaces to exercise (from -Psmokens). Empty = act on nothing (LOAD-only proof). */
    private static final Set<String> NAMESPACES = parseNamespaces();
    private static final int PER_TYPE = MODE == Mode.BATTLE ? Math.max(1, Integer.getInteger(P + "battlecount", 8)) : 1;

    private static final int TITLE_SETTLE_TICKS = 60;
    private static final int INGAME_SETTLE_TICKS = 40;
    private static final int SURVIVE_TICKS = MODE == Mode.BATTLE
        ? Math.max(60, Integer.getInteger(P + "battleticks", 600)) : 200;
    private static final int LOAD_TIMEOUT_TICKS = 20 * 180;

    private enum Phase { WAIT_TITLE, WAIT_INGAME, SURVIVE, GAUNTLET, DONE }
    private static Phase phase = Phase.WAIT_TITLE;
    private static int ticks = 0;

    private static int gauntletStep = 0;
    private static int gauntletStepTicks = 0;
    private static boolean gauntletEntered = false;

    private static final List<Mob> teamA = new ArrayList<>();
    private static final List<Mob> teamB = new ArrayList<>();

    private static Mode resolveMode() {
        String m = System.getProperty(P + "testmode");
        if (m != null) {
            try {
                return Mode.valueOf(m.trim().toUpperCase(Locale.ROOT));
            } catch (IllegalArgumentException ignored) {
            }
        }
        return Mode.SPAWN;
    }

    private static Set<String> parseNamespaces() {
        String ns = System.getProperty(P + "namespaces");
        if (ns == null || ns.isBlank()) return Set.of();
        return Arrays.stream(ns.split(",")).map(String::trim)
            .filter(s -> !s.isEmpty()).collect(Collectors.toSet());
    }

    private static boolean inTarget(ResourceLocation id) {
        return NAMESPACES.isEmpty() ? false : NAMESPACES.contains(id.getNamespace());
    }

    @SubscribeEvent
    public static void onClientTick(ClientTickEvent.Post event) {
        if (!ENABLED || phase == Phase.DONE) return;
        Minecraft mc = Minecraft.getInstance();
        try {
            switch (phase) {
                case WAIT_TITLE -> waitTitle(mc);
                case WAIT_INGAME -> waitIngame(mc);
                case SURVIVE -> survive(mc);
                case GAUNTLET -> gauntlet(mc);
                default -> { }
            }
        } catch (Throwable t) {
            System.out.println(TAG + ": FAIL — smoke test threw in phase " + phase + ": " + t);
            t.printStackTrace();
            finish(mc);
        }
    }

    private static void waitTitle(Minecraft mc) {
        if (mc.screen instanceof net.minecraft.client.gui.screens.AccessibilityOnboardingScreen) {
            mc.options.onboardAccessibility = false;
            mc.options.save();
            mc.setScreen(new TitleScreen());
            return;
        }
        if (!(mc.screen instanceof TitleScreen)) return;
        if (++ticks < TITLE_SETTLE_TICKS) return;

        if (MODE == Mode.LAUNCH) {
            System.out.println(TAG + ": PASS — reached title screen, client + target jar(s) loaded without crashing");
            finish(mc);
            return;
        }
        String levelName = "smoke-" + System.currentTimeMillis();
        System.out.println(TAG + ": [" + MODE + "] reached title — creating world '" + levelName + "' (namespaces=" + NAMESPACES + ")…");
        createFreshWorld(mc, levelName);
        transition(Phase.WAIT_INGAME);
    }

    private static void createFreshWorld(Minecraft mc, String levelName) {
        LevelSettings settings = new LevelSettings(
            levelName, GameType.CREATIVE, false, Difficulty.NORMAL, true,
            new GameRules(), WorldDataConfiguration.DEFAULT);
        Function<RegistryAccess, WorldDimensions> dimensions = WorldPresets::createNormalWorldDimensions;
        mc.createWorldOpenFlows().createFreshLevel(
            levelName, settings, WorldOptions.defaultWithRandomSeed(), dimensions, new TitleScreen());
    }

    private static void waitIngame(Minecraft mc) {
        IntegratedServer server = mc.getSingleplayerServer();
        boolean inWorld = server != null && server.isReady() && mc.player != null && mc.level != null;
        if (!inWorld) {
            if (++ticks > LOAD_TIMEOUT_TICKS) {
                throw new IllegalStateException("world did not finish loading within " + (LOAD_TIMEOUT_TICKS / 20) + "s");
            }
            return;
        }
        if (++ticks < INGAME_SETTLE_TICKS) return;
        if (MODE == Mode.CENSUS) {
            // Deliberately NOT a gauntlet step here: the audit's whole value is the report, and running
            // it behind six unrelated stress steps means one crash in someone else's mod costs you the
            // answer for all of them. Census, print, done.
            contentCensus(mc);
            System.out.println(TAG + ": PASS — census clean across " + NAMESPACES.size() + " namespace(s)");
            finish(mc);
            return;
        }
        if (MODE == Mode.GAUNTLET) {
            System.out.println(TAG + ": [GAUNTLET] in-world — running " + GauntletStep.values().length + " steps…");
            transition(Phase.GAUNTLET);
            return;
        }
        if (MODE == Mode.BATTLE) {
            spawnBattle(mc, server);
        } else {
            spawnOneOfEach(mc, server);
        }
        maybeOpenJeiRecipes(mc);
        maybeVerifyJeiRecipes(mc);
        transition(Phase.SURVIVE);
    }

    /**
     * If -Psmokejeiverify is set AND JEI is loaded, assert JEI shows at least one recipe producing each
     * listed output item. Unlike opening a category by UID, this works for mods with NO custom JEI plugin
     * whose recipes ride vanilla categories (crafting/smithing/…). Throws (FAILs) if JEI shows none.
     */
    private static void maybeVerifyJeiRecipes(Minecraft mc) {
        String prop = System.getProperty(P + "jeiverify");
        if (prop == null || prop.isBlank()) return;
        List<String> items = Arrays.stream(prop.split(",")).map(String::trim).filter(s -> !s.isEmpty()).collect(Collectors.toList());
        if (items.isEmpty()) return;
        if (!net.neoforged.fml.ModList.get().isLoaded("jei")) {
            System.out.println(TAG + ": [JEI] -Psmokejeiverify set but JEI is not loaded — skipping recipe-output check");
            return;
        }
        if (!JeiRecipeProbe.runtimeReady()) {
            throw new IllegalStateException("[JEI] JEI is loaded but its runtime never became available — plugin init failed?");
        }
        java.util.Map<String, Integer> counts = JeiRecipeProbe.countRecipesProducing(items);
        List<String> zero = new ArrayList<>();
        counts.forEach((id, n) -> {
            System.out.println(TAG + ": [JEI] recipes JEI shows producing " + id + " = " + n);
            if (n == 0) zero.add(id);
        });
        if (!zero.isEmpty()) {
            throw new IllegalStateException("[JEI] JEI shows NO recipe producing: " + zero + " (mod's recipes not visible in JEI)");
        }
        System.out.println(TAG + ": [JEI] verified " + items.size() + " output item(s) have visible JEI recipes: " + items);
    }

    /**
     * If -Psmokejeirecipes is set AND JEI is loaded, open those recipe categories in JEI's GUI so the
     * target mod's category setRecipe()/draw() render code actually runs during the SURVIVE ticks
     * (registration alone never calls it). Throws if a requested category isn't in JEI — i.e. the
     * plugin failed to register it — which fails the phase. No-op (with a log line) when JEI is absent.
     */
    private static void maybeOpenJeiRecipes(Minecraft mc) {
        String prop = System.getProperty(P + "jeirecipes");
        if (prop == null || prop.isBlank()) return;
        List<String> uids = Arrays.stream(prop.split(",")).map(String::trim).filter(s -> !s.isEmpty()).collect(Collectors.toList());
        if (uids.isEmpty()) return;
        if (!net.neoforged.fml.ModList.get().isLoaded("jei")) {
            System.out.println(TAG + ": [JEI] -Psmokejeirecipes set but JEI is not loaded — skipping recipe-GUI probe");
            return;
        }
        if (!JeiRecipeProbe.runtimeReady()) {
            throw new IllegalStateException("[JEI] JEI is loaded but its runtime never became available — plugin init failed?");
        }
        List<String> missing = JeiRecipeProbe.openRecipes(uids);
        if (!missing.isEmpty()) {
            throw new IllegalStateException("[JEI] recipe categories NOT found in the JEI runtime (plugin did not register them): " + missing);
        }
        System.out.println(TAG + ": [JEI] opened " + uids.size()
            + " recipe categor(ies) in the JEI GUI — setRecipe()/draw() now rendering: " + uids);
    }

    /** All spawnable target-namespace creatures (non-MISC so projectiles/effects are skipped). */
    private static List<EntityType<?>> targetCreatureTypes() {
        List<EntityType<?>> types = new ArrayList<>();
        for (var entry : BuiltInRegistries.ENTITY_TYPE.entrySet()) {
            if (!inTarget(entry.getKey().location())) continue;
            EntityType<?> type = entry.getValue();
            if (type.getCategory() == MobCategory.MISC) continue;
            types.add(type);
        }
        return types;
    }

    private static void spawnOneOfEach(Minecraft mc, IntegratedServer server) {
        final BlockPos center = mc.player.blockPosition();
        server.execute(() -> {
            ServerLevel level = server.overworld();
            List<ResourceLocation> spawned = new ArrayList<>();
            List<ResourceLocation> failed = new ArrayList<>();
            int i = 0;
            for (EntityType<?> type : targetCreatureTypes()) {
                int col = i % 8, row = i / 8;
                i++;
                BlockPos pos = center.offset(-14 + col * 4, 1, -8 + row * 4);
                Entity e = type.spawn(level, pos, MobSpawnType.COMMAND);
                (e != null ? spawned : failed).add(EntityType.getKey(type));
            }
            System.out.println(TAG + ": spawned " + spawned.size() + " creature type(s): " + spawned);
            if (!failed.isEmpty()) System.out.println(TAG + ": (spawn returned null for: " + failed + ")");
        });
    }

    private static void spawnBattle(Minecraft mc, IntegratedServer server) {
        final BlockPos center = mc.player.blockPosition();
        server.execute(() -> {
            ServerLevel level = server.overworld();
            RandomSource rand = level.getRandom();
            teamA.clear();
            teamB.clear();
            ServerPlayer sp = server.getPlayerList().getPlayer(mc.player.getUUID());
            if (sp != null) {
                sp.setInvulnerable(true);
                int buff = (INGAME_SETTLE_TICKS + SURVIVE_TICKS + 200);
                sp.addEffect(new MobEffectInstance(MobEffects.DAMAGE_RESISTANCE, buff, 4, false, false));
                sp.addEffect(new MobEffectInstance(MobEffects.FIRE_RESISTANCE, buff, 0, false, false));
            }
            List<EntityType<?>> types = targetCreatureTypes();
            int total = 0, toggle = 0;
            for (EntityType<?> type : types) {
                for (int n = 0; n < PER_TYPE; n++) {
                    double ang = rand.nextDouble() * Math.PI * 2.0;
                    double rad = 4.0 + rand.nextDouble() * 6.0;
                    BlockPos pos = center.offset(
                        (int) Math.round(Math.cos(ang) * rad), 1, (int) Math.round(Math.sin(ang) * rad));
                    Entity e = type.spawn(level, pos, MobSpawnType.COMMAND);
                    if (e instanceof Mob mob) {
                        (toggle++ % 2 == 0 ? teamA : teamB).add(mob);
                        total++;
                    }
                }
            }
            System.out.println(TAG + ": [BATTLE] spawned " + total + " mobs — team A " + teamA.size()
                + " vs team B " + teamB.size() + " (" + types.size() + " type(s) × " + PER_TYPE + ")");
        });
    }

    private static void survive(Minecraft mc) {
        IntegratedServer server = mc.getSingleplayerServer();
        if (server == null || mc.player == null || mc.level == null) {
            throw new IllegalStateException("world/server vanished after spawning — likely a server-thread crash (see log above)");
        }
        if (MODE == Mode.BATTLE) server.execute(ClientBootSmokeTest::driveBattle);
        if (++ticks < SURVIVE_TICKS) return;
        if (MODE == Mode.BATTLE) {
            server.execute(() -> {
                long aliveA = teamA.stream().filter(m -> m != null && m.isAlive()).count();
                long aliveB = teamB.stream().filter(m -> m != null && m.isAlive()).count();
                System.out.println(TAG + ": PASS — battle ran " + SURVIVE_TICKS
                    + " ticks with no crash (survivors: team A " + aliveA + ", team B " + aliveB + ")");
            });
        } else {
            System.out.println(TAG + ": PASS — created world, spawned target creatures, world ticked "
                + SURVIVE_TICKS + " ticks with no crash");
        }
        finish(mc);
    }

    private static void driveBattle() {
        assignEnemies(teamA, teamB);
        assignEnemies(teamB, teamA);
    }

    private static void assignEnemies(List<Mob> team, List<Mob> enemies) {
        for (Mob m : team) {
            if (m == null || !m.isAlive()) continue;
            LivingEntity cur = m.getTarget();
            if (cur != null && cur.isAlive() && enemies.contains(cur)) continue;
            LivingEntity foe = nearestAlive(enemies, m);
            if (foe != null) {
                m.setTarget(foe);
                m.setLastHurtByMob(foe);
            }
        }
    }

    private static LivingEntity nearestAlive(List<Mob> list, Mob from) {
        LivingEntity best = null;
        double bestSq = Double.MAX_VALUE;
        for (Mob e : list) {
            if (e == null || !e.isAlive()) continue;
            double d = e.distanceToSqr(from);
            if (d < bestSq) {
                bestSq = d;
                best = e;
            }
        }
        return best;
    }

    // ─────────────────────────── GAUNTLET mode (client/item axis) ───────────────────────────
    // No OPEN_GUIS step: an arbitrary jar's Screen classes are unknown at compile time.
    private enum GauntletStep {
        TOOLTIPS(10),
        RENDER_ITEMS(40),
        OPEN_CREATIVE(40),   // open the REAL creative inventory -> triggers EVERY mod's
                             // BuildCreativeModeTabContentsEvent handler + renders each tab's entries
        CREATIVE_PICKUP(20), // encode every creative stack the way PICKING ONE UP does -> catches a
                             // stack the client can BUILD but not SEND (catalog R24)
        CONTENT_CENSUS(20),  // does every targeted mod HAVE what it claims to have? (catalog S4/S5)
        EQUIP_ARMOR(50),
        PLACE_BLOCKS(60),
        USE_ITEMS(30),
        APPLY_EFFECTS(60);
        final int durationTicks;
        GauntletStep(int d) { this.durationTicks = d; }
    }

    private static boolean stepWantsScreen(GauntletStep step) {
        return step == GauntletStep.RENDER_ITEMS || step == GauntletStep.OPEN_CREATIVE;
    }

    private static void gauntlet(Minecraft mc) {
        IntegratedServer server = mc.getSingleplayerServer();
        if (server == null || mc.player == null || mc.level == null) {
            throw new IllegalStateException("world/server vanished during gauntlet — likely a crash (see log above)");
        }
        GauntletStep[] steps = GauntletStep.values();
        if (gauntletStep >= steps.length) {
            System.out.println(TAG + ": PASS — gauntlet ran " + steps.length + " item/block/use/effect steps with no crash");
            finish(mc);
            return;
        }
        GauntletStep step = steps[gauntletStep];
        if (!gauntletEntered) {
            gauntletEntered = true;
            System.out.println(TAG + ": [GAUNTLET] step " + (gauntletStep + 1) + "/" + steps.length + " — " + step);
            if (!stepWantsScreen(step) && mc.screen != null) mc.setScreen(null);
            enterGauntletStep(step, mc, server);
        }
        if (++gauntletStepTicks >= step.durationTicks) {
            exitGauntletStep(step, mc);
            gauntletStep++;
            gauntletStepTicks = 0;
            gauntletEntered = false;
        }
    }

    private static void enterGauntletStep(GauntletStep step, Minecraft mc, IntegratedServer server) {
        switch (step) {
            case TOOLTIPS -> buildAllTooltips(mc);
            case RENDER_ITEMS -> {
                giveAllItems(mc, server);
                mc.setScreen(new AllItemsScreen(targetItems()));
            }
            case OPEN_CREATIVE -> {
                // Constructing the creative inventory rebuilds ALL tab contents, firing every mod's
                // BuildCreativeModeTabContentsEvent handler (add/remove) + rendering the entries — the
                // path a custom item-list screen does NOT exercise (this caught a tab-remove crash).
                System.out.println(TAG + ": [GAUNTLET] opening real creative inventory (builds all tabs)");
                mc.setScreen(new net.minecraft.client.gui.screens.inventory.CreativeModeInventoryScreen(
                    mc.player, mc.level.enabledFeatures(), false));
            }
            case CREATIVE_PICKUP -> encodeCreativeStacks(mc);
            case CONTENT_CENSUS -> contentCensus(mc);
            case EQUIP_ARMOR -> {
                equipAllArmor(mc, server);
                mc.options.setCameraType(CameraType.THIRD_PERSON_BACK);
            }
            case PLACE_BLOCKS -> placeAllBlocks(mc, server);
            case USE_ITEMS -> useAllItems(mc, server);
            case APPLY_EFFECTS -> applyAllEffects(mc, server);
        }
    }

    private static void exitGauntletStep(GauntletStep step, Minecraft mc) {
        if (stepWantsScreen(step) && mc.screen != null) mc.setScreen(null);
        if (step == GauntletStep.EQUIP_ARMOR) mc.options.setCameraType(CameraType.FIRST_PERSON);
    }

    private static final class AllItemsScreen extends Screen {
        private final List<Item> items;
        AllItemsScreen(List<Item> items) {
            super(Component.literal("smoke-all-items"));
            this.items = items;
        }
        @Override public boolean isPauseScreen() { return false; }
        @Override public void render(GuiGraphics g, int mouseX, int mouseY, float partialTick) {
            this.renderBackground(g, mouseX, mouseY, partialTick);
            int perRow = Math.max(1, (this.width - 16) / 18);
            int x = 8, y = 8, col = 0;
            for (Item item : items) {
                ItemStack stack = new ItemStack(item);
                g.renderItem(stack, x, y);
                g.renderItemDecorations(this.font, stack, x, y);
                if (++col >= perRow) { col = 0; x = 8; y += 18; } else { x += 18; }
            }
            super.render(g, mouseX, mouseY, partialTick);
        }
    }

    private static List<Item> targetItems() {
        List<Item> items = new ArrayList<>();
        for (Item item : BuiltInRegistries.ITEM) {
            if (inTarget(BuiltInRegistries.ITEM.getKey(item))) items.add(item);
        }
        return items;
    }

    /**
     * Encode every creative stack the way PICKING ONE UP does — catches a stack the client can
     * BUILD but cannot SEND (catalog R24).
     *
     * <p>OPEN_CREATIVE only proves the tabs build. Taking an item out of one makes the client send
     * {@code serverbound/set_creative_mode_slot}, which serialises the stack against the CLIENT's
     * registries. In singleplayer the client and the integrated server hold SEPARATE copies of every
     * datapack registry (enchantments, potions, trim patterns…), so a component holding a
     * {@code Holder} borrowed from the server copy renders and tooltips perfectly and then throws
     * {@code EncoderException: Can't find id for Reference{…}} at pick-up — Netty drops the
     * connection mid-game with no crash report. Invisible to Gates A/B (no client registry) and to
     * OPEN_CREATIVE (no packet). Caught a Fabric weapons-mod port on 111 of its 152 items.
     */
    private static void encodeCreativeStacks(Minecraft mc) {
        if (mc.level == null) return;
        List<String> failures = new ArrayList<>();
        int encoded = 0;
        for (Item item : targetItems()) {
            ItemStack stack = item.getDefaultInstance();
            RegistryFriendlyByteBuf buf =
                    new RegistryFriendlyByteBuf(Unpooled.buffer(), mc.level.registryAccess());
            try {
                ItemStack.OPTIONAL_STREAM_CODEC.encode(buf, stack);
                encoded++;
            } catch (Throwable t) {
                failures.add(BuiltInRegistries.ITEM.getKey(item) + " -> " + t);
            } finally {
                buf.release();
            }
        }
        if (!failures.isEmpty()) {
            throw new IllegalStateException("picking these up in creative would DISCONNECT the player ("
                    + failures.size() + " of " + (encoded + failures.size()) + " items): "
                    + failures.subList(0, Math.min(5, failures.size())));
        }
        System.out.println(TAG + ": [GAUNTLET] encoded " + encoded
                + " creative stack(s) against the CLIENT registry — creative pick-up is safe");
    }

    private static void buildAllTooltips(Minecraft mc) {
        Item.TooltipContext ctx = Item.TooltipContext.of(mc.level);
        int n = 0;
        for (Item item : targetItems()) {
            new ItemStack(item).getTooltipLines(ctx, mc.player, TooltipFlag.Default.ADVANCED);
            n++;
        }
        System.out.println(TAG + ": [GAUNTLET] built tooltips for " + n + " item(s)");
    }

    private static void giveAllItems(Minecraft mc, IntegratedServer server) {
        final java.util.UUID uuid = mc.player.getUUID();
        server.execute(() -> {
            ServerPlayer sp = server.getPlayerList().getPlayer(uuid);
            if (sp == null) return;
            int n = 0;
            for (Item item : targetItems()) {
                sp.getInventory().add(new ItemStack(item));
                n++;
            }
            System.out.println(TAG + ": [GAUNTLET] gave " + n + " item(s) to the player");
        });
    }

    private static void equipAllArmor(Minecraft mc, IntegratedServer server) {
        final java.util.UUID uuid = mc.player.getUUID();
        server.execute(() -> {
            ServerPlayer sp = server.getPlayerList().getPlayer(uuid);
            if (sp == null) return;
            int n = 0;
            for (Item item : targetItems()) {
                if (item instanceof ArmorItem armor) {
                    sp.setItemSlot(armor.getEquipmentSlot(), new ItemStack(item));
                    n++;
                }
            }
            System.out.println(TAG + ": [GAUNTLET] equipped " + n + " armor piece(s)");
        });
    }

    private static void placeAllBlocks(Minecraft mc, IntegratedServer server) {
        final BlockPos center = mc.player.blockPosition();
        server.execute(() -> {
            ServerLevel level = server.overworld();
            int i = 0, n = 0;
            for (var block : BuiltInRegistries.BLOCK) {
                if (!inTarget(BuiltInRegistries.BLOCK.getKey(block))) continue;
                BlockPos pos = center.offset(-4 + (i % 8), 0, 4 + (i / 8));
                i++;
                level.setBlockAndUpdate(pos, block.defaultBlockState());
                n++;
            }
            System.out.println(TAG + ": [GAUNTLET] placed " + n + " block(s)");
        });
    }

    private static void useAllItems(Minecraft mc, IntegratedServer server) {
        final java.util.UUID uuid = mc.player.getUUID();
        server.execute(() -> {
            ServerPlayer sp = server.getPlayerList().getPlayer(uuid);
            if (sp == null) return;
            ServerLevel level = server.overworld();
            int n = 0;
            for (Item item : targetItems()) {
                sp.setItemInHand(InteractionHand.MAIN_HAND, new ItemStack(item));
                item.use(level, sp, InteractionHand.MAIN_HAND);
                sp.stopUsingItem();
                n++;
            }
            System.out.println(TAG + ": [GAUNTLET] use()'d " + n + " item(s)");
        });
    }

    private static void applyAllEffects(Minecraft mc, IntegratedServer server) {
        final BlockPos center = mc.player.blockPosition();
        final java.util.UUID uuid = mc.player.getUUID();
        server.execute(() -> {
            ServerPlayer sp = server.getPlayerList().getPlayer(uuid);
            ServerLevel level = server.overworld();
            Mob mob = null;
            List<EntityType<?>> creatures = targetCreatureTypes();
            if (!creatures.isEmpty() && creatures.get(0).spawn(level, center.offset(2, 1, 2), MobSpawnType.COMMAND) instanceof Mob m) {
                mob = m;
            }
            final Mob target = mob;
            int[] n = {0};
            BuiltInRegistries.MOB_EFFECT.holders()
                .filter(h -> inTarget(h.key().location()))
                .forEach(h -> {
                    if (sp != null) sp.addEffect(new MobEffectInstance(h, 100, 0, false, true));
                    if (target != null) target.addEffect(new MobEffectInstance(h, 100, 0, false, true));
                    n[0]++;
                });
            System.out.println(TAG + ": [GAUNTLET] applied " + n[0] + " effect(s) to player + a mob");
        });
    }

    /**
     * Catalog <b>S4/S5</b> — the port-completeness census, run over every targeted namespace at once.
     *
     * <p><b>Why the harness owns this and not each mod.</b> The question "is everything this mod claims
     * to have actually here?" needs no knowledge of the mod: the manifest and the registries are both
     * global. So one boot with N jars in {@code -Psmokejars} audits all N, with zero edits to any of
     * their sources — which is also how you find out <i>which</i> ports need work before opening one.
     *
     * <p><b>Why any gate was needed.</b> Every other step here is a crash gate, and content that
     * silently failed to register does not crash — it is absent, which looks exactly like a mod that
     * never had it. A space-exploration mod shipped a 1.21 port whose creative tab was constructed but never registered
     * (Resourceful Lib 3.x stopped registering inside {@code build()}, so the migration's class rename
     * compiled clean and dropped the side effect): 423 items with nowhere to be found. The Gate-C log for
     * that very run said <i>"creative tabs built"</i>, because {@code OPEN_CREATIVE} reports
     * {@code CREATIVE_MODE_TAB.size()} — everyone's tabs. <b>A count is not a comparison.</b>
     *
     * <p><b>The oracle.</b> {@code assets/<ns>/lang/en_us.json} is a manifest written by the mod's
     * original author and carried through a port untouched, so it is an expectation the migration cannot
     * silently edit. Every {@code item.}/{@code block.}/{@code entity.}/{@code effect.<ns>.<path>} key
     * must resolve to a registered id, and a namespace declaring any {@code itemGroup.*} key must own at
     * least one registered creative tab.
     *
     * <p><b>Only the TAB rule fails the run. The content list is advisory — measured, not assumed.</b>
     * Run over 17 real ports it produced 135 content flags and <b>zero</b> real findings, because a lang
     * file is a <i>display-name</i> file, not a registry manifest: mods ship keys for unimplemented
     * content (an item id with a lang key that is in no version of that mod's Java), for per-colour/armour
     * VARIANTS of one registered thing (16 per-colour shield keys, one item; an
     * armoured-variant texture on one entity), for optional integrations
     * (a guidebook item that registers only with Patchouli), under stale names
     * (a lang key whose id was renamed long ago and is registered under a longer name), and MCreator
     * mods keep keys for deleted elements. An {@code itemGroup} key carries none of that ambiguity — a
     * mod defines a tab or it does not — which is why it is the half that gates. A gate that cries wolf
     * gets switched off, and then it protects nothing.
     *
     * <p>Keys that are not exactly {@code <type>.<ns>.<path>} are skipped rather than reported. It prints
     * the FULL per-mod table before failing, because as an auditor its value is the table, not the first
     * thing that broke.
     */
    private static void contentCensus(Minecraft mc) {
        if (NAMESPACES.isEmpty()) {
            System.out.println(TAG + ": [GAUNTLET] census skipped — no -Psmokens namespaces to audit");
            return;
        }
        List<String> broken = new ArrayList<>();
        System.out.println(TAG + ": [GAUNTLET] ── content census over " + NAMESPACES.size() + " namespace(s) ──");

        for (String ns : NAMESPACES.stream().sorted().toList()) {
            com.google.gson.JsonObject lang;
            try (java.io.BufferedReader reader = mc.getResourceManager()
                    .openAsReader(ResourceLocation.fromNamespaceAndPath(ns, "lang/en_us.json"))) {
                lang = com.google.gson.JsonParser.parseReader(reader).getAsJsonObject();
            } catch (Exception noManifest) {
                // A library mod may ship no lang file at all. That is not a failure — it is nothing to check.
                System.out.println(TAG + ":   " + ns + " — no lang manifest, nothing to verify");
                continue;
            }

            List<String> missing = new ArrayList<>();
            int declaredTabs = 0;
            int checked = 0;
            for (String key : lang.keySet()) {
                if (key.startsWith("itemGroup.")) { declaredTabs++; continue; }
                String[] part = key.split("\\.");
                if (part.length != 3 || !ns.equals(part[1])) continue;
                net.minecraft.core.Registry<?> registry = switch (part[0]) {
                    case "item" -> BuiltInRegistries.ITEM;
                    case "block" -> BuiltInRegistries.BLOCK;
                    case "entity" -> BuiltInRegistries.ENTITY_TYPE;
                    case "effect" -> BuiltInRegistries.MOB_EFFECT;
                    default -> null;
                };
                if (registry == null) continue;
                ResourceLocation id;
                try {
                    id = ResourceLocation.fromNamespaceAndPath(part[1], part[2]);
                } catch (Exception malformed) {
                    continue;   // never an id-shaped claim in the first place
                }
                checked++;
                if (!registry.containsKey(id)) missing.add(part[0] + " " + id);
            }

            long ownTabs = BuiltInRegistries.CREATIVE_MODE_TAB.keySet().stream()
                    .filter(id -> ns.equals(id.getNamespace())).count();
            boolean tabLost = declaredTabs > 0 && ownTabs == 0;
            if (tabLost) {
                missing.add("NO CREATIVE TAB (declares " + declaredTabs
                        + " itemGroup key(s), registers none — constructed but never registered?)");
            }

            System.out.println(TAG + ":   " + ns + " — " + checked + " declared id(s), "
                    + missing.size() + " unregistered (advisory); tabs " + ownTabs + "/" + declaredTabs
                    + (tabLost ? "  <<< NO CREATIVE TAB" : "  OK")
                    + (missing.isEmpty() ? "" : "  [" + missing.subList(0, Math.min(6, missing.size())) + "]"));
            if (tabLost) broken.add(ns);
        }

        System.out.println(TAG + ": [GAUNTLET] ── census done ──");
        if (!broken.isEmpty()) {
            throw new IllegalStateException("census: these port(s) declare a creative tab and register NONE: "
                    + broken + " — their content exists but a player cannot find it");
        }
    }

    private static void transition(Phase next) {
        phase = next;
        ticks = 0;
    }

    private static void finish(Minecraft mc) {
        phase = Phase.DONE;
        mc.stop();
    }
}
