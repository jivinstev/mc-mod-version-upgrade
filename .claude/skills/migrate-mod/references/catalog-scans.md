# Catalog sweep — the MANDATED static cross-check against every §S/§R pattern

This is not advisory. Every migration MUST run this whole sweep — **twice**: once at pre-flight
(SKILL.md Step 4, before trusting a green compile) and once as a completion gate (Step 6, before
calling the port done — patterns get reintroduced during the build loop). Run **all** of it, not a
sample. Under each header, **any file path printed is a HIT**: either fix it, or record it in
`MIGRATION.md` as a verified false-positive with one line of why. A port is not done until every
section is clean-or-explained.

Why static scans when the gates catch these dynamically: the gates *confirm*, the sweep *mandates
coverage*. Gate C needs a display (often unavailable); the sweep catches R6–R12's static signatures
headlessly regardless. Detail + fix for each id is in repo `CATALOG.md` **§R** and the API references
(§A/§S). **Maintenance rule: every new §R/§S pattern that has a grep signature MUST be added here.**

## The sweep — run from `mods/<modid>/`

`bash ../../tools/run-catalog-scans.sh` (from the port directory) extracts the block below and runs
it; you do not need to copy it out by hand.

```bash
SRC=src/main/java; RES=src/main/resources
hit(){ printf '\n── %s ──\n' "$1"; }

hit "§A/#3b  hoisted SPEC = BUILDER.build() above the .define()s (runtime: 'before spec is built')"
for f in $(grep -rl 'BUILDER.build()' $SRC 2>/dev/null); do
  spec=$(grep -nE 'SPEC *= *[A-Za-z_]*BUILDER\.build\(\)' "$f" | head -1 | cut -d: -f1)
  last=$(grep -nE '\.define|\.comment' "$f" | tail -1 | cut -d: -f1)
  [ -n "$spec" ] && [ -n "$last" ] && [ "$spec" -lt "$last" ] && echo "  HOISTED: $f (SPEC@$spec before last define@$last)"
done

hit "§S  SRG remnants — unmapped m_###_/f_###_ members (Forge 1.20.x jars only; must be 0)"
grep -rlE '\bm_[0-9]+_|\bf_[0-9]+_' $SRC 2>/dev/null

hit "R1  @EventBusSubscriber / EVENT_BUS.register(...) with NO @SubscribeEvent method"
# match the ANNOTATION at line-start only — not the `import ...EventBusSubscriber;` line or a comment
grep -rlE '^[[:space:]]*@EventBusSubscriber' $SRC 2>/dev/null | while read f; do \
  [ "$(grep -cE '^[[:space:]]*@SubscribeEvent' "$f")" -eq 0 ] && echo "  no-handler subscriber: $f"; done
grep -rn 'EVENT_BUS.register(this)\|bus\.register(' $SRC 2>/dev/null

hit "R2  config .get() reached from a DeferredRegister supplier / item / ArmorMaterial ctor (REVIEW each)"
grep -rnE '[A-Za-z]*Config\.[A-Za-z_]+\.get\(\)' $SRC 2>/dev/null | grep -iE 'register|Init\.|new .*Item|ArmorMaterial|Properties\('

hit "R3  wrapAsHolder(X.get()) / cross-registry .get() forced at registration"
grep -rn 'wrapAsHolder(' $SRC 2>/dev/null

hit "R5  CLIENT config .get() reachable from server/common code (crashes a dedicated server)"
grep -rnE '[A-Za-z]*ClientConfig\.[a-z_]+\.get\(\)' $SRC 2>/dev/null

hit "R6  advancement lookup handed straight to getOrStartProgress (null NPE on every player tick)"
grep -rn 'getOrStartProgress\|getAdvancements()\.get(' $SRC 2>/dev/null

hit "R7  custom packet decoder stashing the live FriendlyByteBuf for a deferred read"
grep -rnE 'this\.\w*[Bb]uf *= *buf|readBuf *= *buf' $SRC 2>/dev/null

hit "R8  this.entityData / a setter touched INSIDE defineSynchedData(Builder) (ctor NPE)"
python3 - <<'PY'
import re, glob
for f in glob.glob('src/main/java/**/*.java', recursive=True):
    s=open(f).read()
    m=re.search(r'void\s+defineSynchedData\s*\([^)]*\)\s*\{', s)
    if not m: continue
    i=m.end(); d=1; j=i
    while j<len(s) and d>0: d+=s[j]=='{'; d-=s[j]=='}'; j+=1
    body=s[i:j-1]
    if re.search(r'this\.entityData\s*\.\s*(get|set)\s*\(', body) or re.search(r'this\.(setFlag|set[A-Z]\w*)\s*\(', body):
        print("  entityData/setter in defineSynchedData:", f)
PY

hit "R9  create*Attributes() from Mob.createMobAttributes() with no ATTACK_DAMAGE (melee crash if it can doHurtTarget)"
for f in $(grep -rl 'create.*Attributes' $SRC 2>/dev/null); do \
  grep -q 'Mob.createMobAttributes()' "$f" && ! grep -q 'Attributes.ATTACK_DAMAGE' "$f" && echo "  no ATTACK_DAMAGE: $f"; done

hit "R10  new ClipContext(..., (Entity)null) — isDescending() NPE (use CollisionContext.empty())"
grep -rn 'new ClipContext(' $SRC 2>/dev/null | grep -i 'null'

hit "R11  ItemProperties.register lambda that dereferences an NBT-derived getter (render NPE) — REVIEW each"
grep -rn 'ItemProperties.register(' $SRC 2>/dev/null

hit "R12  putUUID(...) that can pass a null UUID (chunk-save NPE) — REVIEW each for a null path"
grep -rn 'putUUID(' $SRC 2>/dev/null

hit "non-fatal  forge: model-loader id not renamespaced to neoforge:"
grep -rln '"loader": *"forge:' $RES 2>/dev/null

hit "non-fatal  optional cross-mod data w/o a requiredMods gate (Unknown registry key spam) — REVIEW"
grep -rln 'byNameCodec()' $SRC 2>/dev/null

hit "non-fatal  custom armor ArmorMaterial.Layer ids — verify textures/models/armor/<id>_layer_{1,2}.png exist"
grep -rhoE 'ArmorMaterial\.Layer\(ResourceLocation\.[A-Za-z]+\("[^"]+", *"[^"]+"\)' $SRC 2>/dev/null
echo "  (^ for each layer id, confirm the two _layer PNGs are shipped under $RES/assets/<ns>/textures/models/armor/)"

hit "§K/#93  new ClipContext(..., (Entity)null) — compiles but R10-NPEs; swap to CollisionContext.empty() (catches qualified casts too)"
grep -rn 'new ClipContext(' $SRC 2>/dev/null | grep -iE 'Entity\)null|, *null *\)'

hit "§K/#98  EntityDimensions accessor double-apply — .width()()/.height()()"
grep -rnE '\.(width|height)\(\)\(\)' $SRC 2>/dev/null

hit "§K/#90  Recipe serializer still returning a plain Codec where a MapCodec is required (also #102 StructureType)"
grep -rnE 'RecordCodecBuilder\.create\(' $SRC 2>/dev/null | grep -iE 'codec\(\)|Structure|Recipe'

hit "§K/#103  Attribute Operation old enum names (MULTIPLY_BASE/MULTIPLY_TOTAL)"
grep -rnE 'Operation\.MULTIPLY_(BASE|TOTAL)' $SRC 2>/dev/null

hit "§L/#104  renderToBuffer with 4 trailing color floats (should be one packed ARGB int) — REVIEW (colorFromFloat calls are already correct)"
grep -rnE 'renderToBuffer\(' $SRC 2>/dev/null | grep -vE 'ARGB32|colorFromFloat' | grep -E ',[^)]*,[^)]*,[^)]*,[^)]*,[^)]*,[^)]*F'

hit "§L/#105  old VertexConsumer builder chain / endVertex() (renamed to addVertex/setColor/…; endVertex removed)"
grep -rnE '\.endVertex\(\)|\.overlayCoords\(|\.uv2\(' $SRC 2>/dev/null

hit "§A  Vineflower un-decompiled method bodies (void=silent no-op, non-void=missing return)"
grep -rn "Couldn't be decompiled" $SRC 2>/dev/null

hit "§A/anon-blowup  MCreator (new Object(){…}).m() blocks — javac OOM death-spiral if many-per-method (hoist to static)"
grep -rc 'new Object() {' $SRC 2>/dev/null | awk -F: '$2>=5{print "  "$2" blocks: "$1}'

hit "R13  Registry.register(BuiltInRegistries.*) / CriteriaTriggers.register from commonSetup — frozen-registry crash (move to DeferredRegister)"
grep -rnE 'Registry\.register\(BuiltInRegistries\.|CriteriaTriggers\.register\(' $SRC 2>/dev/null

hit "R14  mixin @Accessor / CAPTURE_FAILHARD — REVIEW each target field/method against 1.21 vanilla (mixin-apply crash)"
grep -rnE '@Accessor|CAPTURE_FAILHARD' $SRC 2>/dev/null | grep -iE 'mixin'

hit "R14b  RegisterBrewingRecipesEvent — must be on NeoForge.EVENT_BUS (game bus), not modEventBus"
grep -rn 'modEventBus.*RegisterBrewingRecipesEvent\|addListener.*[Bb]rewing' $SRC 2>/dev/null

hit "R15  datapack uniform IntProvider with the removed 1.20 'value' wrapper — REVIEW for nested value"
grep -rln '"type": *"minecraft:uniform"' $RES/data 2>/dev/null

hit "R17  CLIENT mixin @Inject/@Invoker/@Shadow/@Accessor — REVIEW each target vs 1.21 vanilla (client mixin-apply crash; Gate B can't see it)"
grep -rnE '@Inject|@Invoker|@Shadow|@Accessor' $SRC 2>/dev/null | grep -iE 'mixin' | grep -iE 'render|gui|screen|jigsaw|buffer|hitbox|GameRenderer|DeltaTracker'

hit "R18  Forge setCustomClientFactory call leftover (removed in 1.21) -> needs a dist-aware entity factory"
grep -rn '\.setCustomClientFactory(' $SRC 2>/dev/null

hit "R19  LayeredDraw.Layer overlay draws opaque — setColor(alpha)+blit/fill with no enableBlend — REVIEW each overlay draw"
grep -rnE 'setColor\([^)]*(alpha|Alpha)|\.blit\(|graphics\.fill\(' $SRC 2>/dev/null | grep -iE 'overlay'

hit "R20  custom-shader VertexBuffer render path (drawWithShader) — may render nothing in 1.21 — REVIEW"
grep -rn 'drawWithShader' $SRC 2>/dev/null

hit "§O/#123  RAW varargs array in a builder chain — unchecked invocation ERASES the whole chain's return type"
grep -rnE '\.[a-zA-Z]+\(\s*new [A-Z][A-Za-z0-9_.]*\[\]\s*\{' $SRC 2>/dev/null

hit "§O/#124  decompiler raw ((XBuilder)…)/((XEntry)…) casts — break inference on the NEXT chained call"
grep -rnE '\(\([A-Z][A-Za-z0-9_]*(Builder|Entry|Registrate)\)' $SRC 2>/dev/null

hit "§O/#125  MissingMappingsEvent (REMOVED in NeoForge — no registry-remap API; drop the handler + record it)"
grep -rn 'MissingMappingsEvent' $SRC 2>/dev/null

hit "§O/#126  Pack.readMetaAndCreate with the 1.20 arg soup -> AddPackFindersEvent#addPackFinders"
grep -rn 'readMetaAndCreate(' $SRC 2>/dev/null

hit "§O/#127  BlockState#use(Level,Player,InteractionHand,BlockHitResult) -> useWithoutItem(Level,Player,BlockHitResult)"
grep -rnE '\.use\([a-zA-Z]*[Ll]evel *,' $SRC 2>/dev/null

hit "R21  no-arg readNbt() — throws 'Not a compound tag' in 1.21 if the writer emits a ListTag (client-only, Gate C)"
# A HIT is only real when the matching writer emits a NON-compound. The symmetric
# writeNbt(x.getCompound())/readNbt() pair is correct — verify the writer before changing anything.
# Match the CALL (`.readNbt()`), not prose — a bare "readNbt()" in a comment is not a hit.
for f in $(grep -rlE '^[^/*]*\.readNbt\(\)' $SRC 2>/dev/null); do
  printf '  %s\n' "$f"
  grep -nE 'writeNbt\(|encodeStart' "$f" | grep -vE '^\s*[0-9]+:\s*(//|\*)' | sed 's/^/      writer: /'
done

hit "§O/#128  SpecialRecipeBuilder.special(<serializer>) -> special(<Recipe>::new)"
grep -rn 'SpecialRecipeBuilder.special(' $SRC 2>/dev/null | grep -v '::new'

hit "§O/#129  1.21 screen/GUI cluster: Matrix4fStack modelview, mouseScrolled arity, ModelPart.render colour floats"
grep -rnE 'getModelViewStack\(\)|mulPoseMatrix\(|mouseScrolled\(double *[a-zA-Z]+, *double *[a-zA-Z]+, *double *[a-zA-Z]+\)' $SRC 2>/dev/null
grep -rnE '\.render\([a-zA-Z]+, *[a-zA-Z]+, *[a-zA-Z]+, *[a-zA-Z]+, *[0-9a-zA-Z.]+F, *[0-9a-zA-Z.]+F' $SRC 2>/dev/null

hit "§F/#44b  ForgeHooks.getBurnTime -> stack.getBurnTime(recipeType) (NOT a CommonHooks/EventHooks move)"
grep -rn 'getBurnTime(' $SRC 2>/dev/null | grep -E 'ForgeHooks|CommonHooks'

hit "§G/#59b  ITag / getReverseTag / .tags().getTag() -> HolderSet.Named + registry.wrapAsHolder(v).tags()"
grep -rnE 'ITag<|getReverseTag\(|\.tags\(\)\.getTag\(' $SRC 2>/dev/null

hit "non-fatal  #forge: data tags -> #c: (NeoForge common) convention (silently-empty tag refs)"
# (anchored so it does not match inside every neoforge:add_spawns / neoforge:any id)
grep -rhoE '(^|[^a-z])#?forge:[a-z_/]+' $RES/data 2>/dev/null | sed -E 's/^[^#f]//' | sort -u

hit "non-fatal  custom core shader stuck on GLSL 110 (varying/gl_FragColor/texture2D) -> version 150"
grep -rlnE '#version 1[012]0|varying |gl_FragColor|texture2D' $RES/assets 2>/dev/null | grep -iE 'shaders'

hit "non-fatal  model face 'forge_data' key -> 'neoforge_data'"
grep -rln '"forge_data"' $RES 2>/dev/null

hit "§122a  1.21 datapack dirs SINGULARIZED — old plural dirs load NOTHING (silent). rename to singular"
find $RES/data -mindepth 2 -maxdepth 2 -type d \( -name recipes -o -name advancements -o -name loot_tables -o -name structures -o -name predicates -o -name item_modifiers \) 2>/dev/null
find $RES/data -type d \( -name blocks -o -name items -o -name entity_types -o -name fluids -o -name functions -o -name game_events \) 2>/dev/null | while read d; do [ "$(basename "$(dirname "$d")")" = tags ] && echo "$d"; done

hit "S4  declares creative tab(s) in lang but registers NONE — a library's build() stopped registering (silent: no tab)"
# The mod's own lang file is the manifest: an `itemGroup.*` key is a promise there is a tab. A library
# major-bump can quietly turn "build and register" into "build" (Resourceful Lib 2->3 did exactly that to
# a space-exploration mod), and the rename compiles clean. KNOWN FALSE POSITIVE: Registrate-style libraries register on
# your behalf (a Registrate-based framework library and the furniture mod built on it) — confirm those with the runtime CONTENT_CENSUS, not this grep.
if grep -rhq '"itemGroup\.' $RES/assets/*/lang/en_us.json 2>/dev/null; then
  grep -rq 'Registries.CREATIVE_MODE_TAB\|BuiltInRegistries.CREATIVE_MODE_TAB' $SRC 2>/dev/null \
    || echo "  declares itemGroup lang key(s) but NO CREATIVE_MODE_TAB registration in $SRC"
fi

hit "§122b  old recipe RESULT format ({"item":X} or bare string) -> {"id":X} (fails 1.21 codec once loaded)"
# NOTE: a LINE-WISE grep MISSES a pretty-printed multiline `"result": {\n  "item": ... }` block, which is
# how most datagen'd mod recipes are formatted (it slipped past this sweep once). Slurp the whole file.
find $RES/data -name '*.json' 2>/dev/null -exec perl -0777 -ne 'print "  1.20 result shape: $ARGV\n" if /"result"\s*:\s*(?:"|\{[^{}]*"item")/s' {} \;

hit "§122c  1.21.1 ingredients must stay JSON OBJECTS ({"item"/"tag"}) — bare strings/"#tag" are a 1.21.2+ shape"
find $RES/data -name '*.json' 2>/dev/null -exec perl -0777 -ne 'print "  1.21.2+ ingredient shape on 1.21.1: $ARGV\n" if /"ingredients"\s*:\s*\[\s*"/s' {} \;

hit "§122d  forge: -> c: tag ids are PLURAL nouns (c:leathers, not c:leather) — a wrong id is silently empty"
grep -rhoE '"(#?)c:[a-z_/]+"' $RES/data 2>/dev/null | sort -u
echo "  (^ confirm each exists in data/c/tags/ inside the NeoForge universal jar)"

hit "§130  ForgeRegistries under ANY package (does not exist in NeoForge 1.21.1) -> BuiltInRegistries.X (singular names)"
grep -rn 'ForgeRegistries' $SRC 2>/dev/null | grep -v NeoForgeRegistries

hit "§131  DeferredRegister entry typed Supplier<Attribute|MobEffect> (must be DeferredHolder — 1.21 Holder-ified both)"
grep -rnE 'Supplier<(Attribute|MobEffect)>' $SRC 2>/dev/null

hit "§132  MobType (REMOVED in 1.21) -> entity-type tags"
grep -rn 'MobType' $SRC 2>/dev/null | grep -v MobTypeCompat

hit "§133  Raid.RaiderType.create (REMOVED — RaiderType is an IExtensibleEnum, JSON enum-extension)"
grep -rn 'RaiderType.create(' $SRC 2>/dev/null

hit "§134  SpawnPlacements.register / SpawnPlacements.Type (-> SpawnPlacementTypes + RegisterSpawnPlacementsEvent)"
grep -rnE 'SpawnPlacements\.register\(|SpawnPlacements\.Type' $SRC 2>/dev/null

hit "§135  Event#isCancelable() (REMOVED — cancellability is ICancellableEvent, and some events stopped being cancellable)"
grep -rn 'isCancelable()' $SRC 2>/dev/null

hit "§136  MIXIN @Inject whose params still mirror a CHANGED vanilla signature (compiles; kills mod load at mixin APPLY)"
grep -rn -A8 'method = {"finalizeSpawn"}\|method = {"defineSynchedData"}' $SRC 2>/dev/null | grep -E 'CompoundTag|CallbackInfo' 
echo "  (^ finalizeSpawn must NOT take CompoundTag; defineSynchedData MUST take SynchedEntityData.Builder)"

hit "§137  private final boolean <flag> = true — a COMPILE-TIME CONSTANT, so the super-ctor guard it was written for never fires"
grep -rnE 'private final boolean [A-Za-z_]+ *= *(true|false);' $SRC 2>/dev/null

hit "§138  GeckoLib 4.7 packages / RenderUtils / 4-float colour tail (4.8 flattened core.* and packed colour to an int)"
grep -rn 'geckolib\.core\.\|RenderUtils\.\|MolangParser' $SRC 2>/dev/null

hit "§141  removed entity/mob methods: getAttackReachSqr, setMaxUpStep, getStandingEyeHeight(Pose,EntityDimensions), broadcastBreakEvent, getExploder, getMobGriefingEvent"
grep -rnE 'getAttackReachSqr\(|setMaxUpStep\(|getStandingEyeHeight\(|broadcastBreakEvent\(|getExploder\(|getMobGriefingEvent\(' $SRC 2>/dev/null

hit "§139  ACCESS TRANSFORMER sanity — a dead AT line TRUNCATES the Minecraft recompile (and Gradle caches it). Healthy 1.21.1 = 9766 classes"
J=build/neoForm/*/steps/recompile/outputs.jar
for j in $J; do [ -f "$j" ] && echo "  MC recompile classes: $(unzip -l "$j" 2>/dev/null | grep -c '\.class$')  (expect 9766; a lower number means a poisoned AT)"; done

hit "§143  1.21 loot-table renames — looting_enchant / random_chance_with_looting / set_nbt / entity:killer (parse-fails SILENTLY; mob drops nothing)"
grep -rln 'minecraft:looting_enchant\|minecraft:random_chance_with_looting\|minecraft:set_nbt\|"entity": *"killer' $RES/data 2>/dev/null

hit "§144  every <modid>: id referenced from data/ must be a REGISTERED id (a dead one hard-fails registry load once §142 makes the file actually load)"
MODID=$(grep -m1 '^mod_id=' gradle.properties 2>/dev/null | cut -d= -f2)
[ -n "$MODID" ] && grep -rho "\"$MODID:[a-z_]*\"" $RES/data 2>/dev/null | tr -d '"' | sort -u
echo "  (^ cross-check each against the mod's registries; stale ids are common in the upstream mod's own data)"

hit "§P/#145  FABRIC source leftovers — fabric.mod.json / accesswidener / Fabric JiJ (must all be gone)"
ls $RES/fabric.mod.json $RES/*.accesswidener 2>/dev/null; find $RES/META-INF/jars -type f 2>/dev/null

hit "§P/#146  INTERMEDIARY remnants — unmapped class_###/method_###/field_### (Fabric jars only; must be 0)"
grep -rlE '\bclass_[0-9]+|\bmethod_[0-9]+|\bfield_[0-9]+' $SRC 2>/dev/null

hit "§P/#147  Fabric entrypoints / dist markers / loader API still referenced"
grep -rnE 'net\.fabricmc\.|ModInitializer|ClientModInitializer|@Environment\(|EnvType\.|FabricLoader' $SRC 2>/dev/null

hit "§P/#148  eager Registry.register from a mod ctor/init (NeoForge registries are frozen -> use DeferredRegister)"
grep -rn 'Registry\.register(' $SRC 2>/dev/null | grep -v DeferredRegister

hit "§P/#149  Cloth Config / AutoConfig still referenced (Fabric-only; port to a gson shim, NOT ModConfigSpec)"
grep -rn 'me\.shedaniel\|autoconfig\|jankson' $SRC 2>/dev/null | grep -v 'compat\.autoconfig'

hit "§P/#151  Fabric accesswidener entries not translated to an AT (and check each target still exists in 1.21)"
find . -name '*.accesswidener' 2>/dev/null

hit "§P/#152  🔴 MIXIN targets still in YARN names — invisible to javac, hard crash at mixin APPLY"
grep -rhoE 'method *= *\{?"[a-zA-Z_$][A-Za-z0-9_$]*"' $SRC 2>/dev/null | sort -u
echo "  (^ every one of these must be a 1.21 MOJANG-OFFICIAL member name. Yarn tells: *ScreenHandler*,"
echo "     *Entity(Renderer)?Mixin for a vanilla class, isAcceptableItem, getMaxUseTime, onEntityHit,"
echo "     damage/tick/writeCustomDataToNbt. Verify each with javap against the recompile jar.)"
grep -rn --include='*.json' '"compatibilityLevel" *: *"JAVA_8"' $RES 2>/dev/null
grep -rn --include='*.json' '"refmap"' $RES 2>/dev/null   # mixin configs are JSON; a toml comment is not a hit

hit "§P/#153  data/fabric/** and Fabric-only tag namespaces (silently empty on NeoForge)"
find $RES/data -maxdepth 1 -type d \( -name fabric -o -name origins \) 2>/dev/null

hit "§P/#153b  🔴 1.21 enchantable/* tags — a GEAR mod that skips these has gear NO enchantment can apply to (silent)"
if ls $RES/assets/*/models/item >/dev/null 2>&1; then
  ls $RES/data/minecraft/tags/item/enchantable/ 2>/dev/null || echo "  MISSING data/minecraft/tags/item/enchantable/ — if this mod ships weapons/armour, vanilla enchantments will NEVER apply to them"
fi

hit "§P/#154  Enchantment SUBCLASSES (impossible in 1.21 — enchantments are a datapack registry)"
grep -rn 'extends Enchantment\b\|EnchantmentCategory' $SRC 2>/dev/null
echo "  (^ if the mod defines enchantments, confirm data/<ns>/enchantment/*.json exist and are tagged"
echo "     into #minecraft:tags/enchantment/{in_enchanting_table,on_random_loot,tradeable,non_treasure})"

hit "§P/#155  ItemStack#enchant / registry lookup from code with no Level — confirm a null-safe access helper"
grep -rn '\.enchant(' $SRC 2>/dev/null

hit "§N/#114  MobEffect overrides — on 1.21.1 applyEffectTick is (LivingEntity,int)->boolean (NOT ServerLevel), addAttributeModifiers is (AttributeMap,int)"
grep -rnE 'applyEffectTick\(|isDurationEffectTick\(|addAttributeModifiers\(|removeAttributeModifiers\(' $SRC 2>/dev/null

hit "§159  ItemAttributeModifiers built from ctor args must be a STATIC helper (else 'cannot reference this before supertype ctor')"
grep -rn 'ItemAttributeModifiers.builder()' $SRC 2>/dev/null

hit "§162  MIXIN 'this instanceof X' / '(X) this' — the mixin class does not extend its target (javac error)"
grep -rn 'this instanceof \|([A-Za-z]*Entity) this\|(Player) this' $SRC 2>/dev/null | grep -v '(Object) this'

hit "§163  🔴 CLIENT-only import in SHARED code (I18n, Minecraft.getInstance, …) — dedicated-server class-load crash"
grep -rn 'import net\.minecraft\.client\.' $SRC 2>/dev/null | grep -v '/client/'
echo "  (^ any hit OUTSIDE a Dist.CLIENT-gated package crashes runGameTestServer with"
echo "     'Attempted to load class … for invalid dist DEDICATED_SERVER'. I18n -> net.minecraft.locale.Language)"

hit "§164  Gate-B test joining ALL failures into one assert message (>1024 chars crashes chunk-save + MASKS the failures)"
grep -rn 'String.join' $SRC/*/*/test/*.java $SRC/*/*/*/test/*.java 2>/dev/null | grep -iE 'assertTrue|fail\('

hit "§165  MIXIN method names must be javap-verified against build/neoForm/*/steps/recompile/outputs.jar (§P #152 done-gate)"
ls build/neoForm/*/steps/recompile/outputs.jar 2>/dev/null >/dev/null \
  && echo "  recompile jar present — RUN the javap cross-check (catalog #165) over every @Mixin target + method string" \
  || echo "  (no recompile jar yet — run ./gradlew compileJava first, then the #165 javap cross-check)"

hit "§S6  a texture that is a JPEG/WebP NAMED .png — renders as the missing cube, ERROR at load, every gate green"
find src/main/resources -name '*.png' -exec file {} + 2>/dev/null | grep -v "PNG image"
echo "  (^ file(1) reads magic bytes, so a hit is a FACT, not a candidate. Measured 3/21 ports."
echo "     Re-encoding is a decision about the author's art — RECORD it in MIGRATION.md if not yours to change.)"

hit "§W10d/W15  a SCRIPT that builds, boots or picks a jar without FORWARDING the target (only in a §W two-target tree)"
for f in $(grep -rlE '\./gradlew|build/libs/\*\.jar' tools deploy scripts 2>/dev/null); do
  grep -qE -- '-Pmc|mc_gradle_args|MC=|\$\{?MC\b' "$f" || echo "  $f  (runs Gradle / picks a jar, never names a target)"
  grep -qE 'build/libs/\*\.jar' "$f" && echo "  $f  (globs build/libs directly — a non-default target builds into build-mc<t>/libs)"
done
echo "  (^ a hit is a CANDIDATE: a script that only ever means the default target is fine. Measured on the"
echo "     ~840-file content mod's 1.21.4 port: 6 scripts, found ONE AT A TIME over three days — gate-b, deploy/build,"
echo "     e2e, rehearse-world-jar (an upgrade rehearsal that would have rehearsed 1.21.1 -> 1.21.1),"
echo "     client-test (reachable only via GRADLE_ARGS) and stock-panel-gate.)"

hit "§W18  a pack_format typed as a LITERAL — it stays the old version's number through the port"
grep -rnE 'pack_format["\\]*[[:space:]]*[:=][[:space:]]*[0-9]+' src/main/java tools deploy 2>/dev/null \
  | grep -vE '/test/|GameTest|ClientTest'
grep -rnE "pack_format['\"]?[[:space:]]*:[[:space:]]*[0-9]+" --include=*.py . 2>/dev/null | grep -v '/test' | head -20
echo "  (^ a hit is a CANDIDATE: a fixture pack in a test may pin a number on purpose. Shipped writers"
echo "     should read the running game's number (SharedConstants…getPackVersion) or take the target in."
echo "     Measured on a ~840-file content mod's 1.21.4 port: two writers said 48 and an install-time rewrite hid both.)"

printf '\n── sweep complete — every path above is a HIT to fix-or-explain ──\n'
```

## Coverage note
Grep can't fully decide the **REVIEW** ones (R2, R11, R12, the requiredMods/armor checks) — they
print candidates a human/agent must judge. Everything else is a definite hit. The dynamic gates
(GameTest + client boot loop) are still required — they catch what has no static signature and
*confirm* the fixes — but this sweep guarantees no catalogued pattern is skipped by omission.
