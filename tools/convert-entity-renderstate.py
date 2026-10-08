#!/usr/bin/env python3
"""Convert vanilla (non-GeckoLib) entity renderers, models and render layers from the 1.21.1 API to 26.2 render states.

    python3 tools/convert-entity-renderstate.py --src src/main/java [--dry-run] [--self-check]

1.21.1 types renderers, models and layers on the ENTITY; 26.2 types them on a RENDER STATE that the renderer
fills once per frame (CATALOG §M1 read forwards, §V6, §V61, §V65b, §V78). Rules, each exact; anything else is
REFUSED by name (file:line + reason) and the whole class left untouched:

  renderer  extends EntityRenderer<E> / LivingEntityRenderer<E,M> / MobRenderer<E,M> / AgeableMobRenderer<E,M> /
            HumanoidMobRenderer<E,M> / IllagerRenderer<E> / ArrowRenderer<E>, or a vanilla concrete renderer
            (ZombieRenderer ...)
              -> the state arity is added (`MobRenderer<E, S, M>`), the model argument is retargeted to S,
                 `createRenderState()` is added, and getTextureLocation(E) / scale(E,PoseStack,float) /
                 getWhiteOverlayProgress(E,float) / setupRotations(E,PoseStack,float x4) take the state.
                 Entity reads in those bodies (`entity.getFoo()`, `entity.getBar(partialTick)`) become fields: an
                 existing field of the state when 26.2 fills one with the same meaning, otherwise a field of a
                 nested state subclass filled by an extractRenderState override (CATALOG §V61, §V65b: a plain
                 field on the state, never setRenderData, which 26.2 wipes). A getter that is not a one-call
                 chain on the parameter, or whose type cannot be read off a mod entity class, REFUSES the class.
                 `new ItemInHandLayer<>(this, <renderer>)` loses its second argument and selects ArmedEntityRenderState.
  model     extends EntityModel<T> / HumanoidModel<T> / SkeletonModel<T> / ZombieModel<T> / IllagerModel<T>, or a
            mod model already of that family
              -> the entity bound of T becomes the state, `setupAnim(T e, f, f, f, f, f)` becomes `setupAnim(T state)`
                 with the five floats bridged to walkAnimationPos / walkAnimationSpeed / ageInTicks / yRot / xRot
                 as locals (only those the body reads), and a direct EntityModel subclass gets `super(<root>)`.
                 A body that reads the entity, overrides renderToBuffer / prepareMobModel, extends
                 HierarchicalModel / ListModel / AgeableListModel, or implements ArmedModel is REFUSED.
  layer     extends RenderLayer<T, M> generically
              -> the entity bound becomes the state, `M extends EntityModel<T>` becomes `EntityModel<? super T>`, and a
                 `render(...)` whose whole body is `super.render(...)` with its own arguments becomes the six-argument
                 `submit(...)`. Any other render body is REFUSED (the recorded-draw rewrite is by hand, §V6).

Moved or renamed vanilla types (ResourceLocation -> Identifier, SkeletonModel's package) are the rename table's
job and are left as written. State fields below were read off the 26.2 sources. Idempotent; standard library only.
"""
import argparse, importlib.util, pathlib, re, sys

_here = pathlib.Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location("fs", _here / "forge-shapes.py")
fs = importlib.util.module_from_spec(_s); _s.loader.exec_module(fs)
_n = importlib.util.spec_from_file_location("ni", _here / "normalise-imports.py")
ni = importlib.util.module_from_spec(_n); _n.loader.exec_module(ni)

# 26.2 render states: short name (without "RenderState") -> (parent, public fields). Read from the 26.2 sources.
STATES = {
    "Allay": ("ArmedEntity", "isDancing isSpinning spinningProgress holdingAnimationProgress"),
    "Armadillo": ("LivingEntity", "isHidingInShell rollOutAnimationState rollUpAnimationState peekAnimationState"),
    "ArmedEntity": ("LivingEntity", "mainArm attackArm rightArmPose rightHandItemState rightHandItemStack leftArmPose leftHandItemState leftHandItemStack swingAnimationType attackTime"),
    "ArmorStand": ("Humanoid", "yRot wiggle isMarker isSmall showArms showBasePlate headPose bodyPose leftArmPose rightArmPose leftLegPose rightLegPose"),
    "Arrow": ("Entity", "xRot yRot shake"),
    "Avatar": ("Humanoid", "skin capeFlap capeLean capeLean2 arrowCount stingerCount isSpectator showHat showJacket showLeftPants showRightPants showLeftSleeve showRightSleeve showCape fallFlyingTimeInTicks shouldApplyFlyingYRot flyingYRot parrotOnLeftShoulder parrotOnRightShoulder id showExtraEars heldOnHead"),
    "Axolotl": ("LivingEntity", "variant playingDeadFactor movingFactor inWaterFactor onGroundFactor swimAnimation walkAnimationState walkUnderWaterAnimationState idleUnderWaterAnimationState idleUnderWaterOnGroundAnimationState idleOnGroundAnimationState playDeadAnimationState"),
    "Bat": ("LivingEntity", "isResting flyAnimationState restAnimationState"),
    "Bee": ("LivingEntity", "rollAmount hasStinger isOnGround isAngry hasNectar"),
    "BlockDisplayEntity": ("DisplayEntity", "blockModel"),
    "Boat": ("Entity", "yRot hurtDir hurtTime damageTime bubbleAngle isUnderWater rowingTimeLeft rowingTimeRight"),
    "Bogged": ("Skeleton", "isSheared"),
    "Breeze": ("LivingEntity", "idle shoot slide slideBack inhale longJump"),
    "Camel": ("LivingEntity", "saddle isRidden jumpCooldown sitAnimationState sitPoseAnimationState sitUpAnimationState idleAnimationState dashAnimationState"),
    "Cat": ("Feline", "texture isLyingOnTopOfSleepingPlayer collarColor"),
    "Chicken": ("LivingEntity", "flap flapSpeed variant"),
    "CopperGolem": ("ArmedEntity", "weathering copperGolemState idleAnimationState interactionGetItem interactionGetNoItem interactionDropItem interactionDropNoItem blockOnAntenna"),
    "Cow": ("LivingEntity", "variant"),
    "Creaking": ("LivingEntity", "invulnerabilityAnimationState attackAnimationState deathAnimationState eyesGlowing canMove"),
    "Creeper": ("LivingEntity", "swelling isPowered"),
    "DisplayEntity": ("Entity", "renderState interpolationProgress entityYRot entityXRot cameraYRot cameraXRot"),
    "Dolphin": ("HoldingEntity", "isMoving"),
    "Donkey": ("Equine", "hasChest"),
    "EndCrystal": ("Entity", "showsBottom beamOffset"),
    "EnderDragon": ("Entity", "flapTime deathTime hasRedOverlay beamOffset isLandingOrTakingOff isSitting distanceToEgg partialTicks flightHistory"),
    "Enderman": ("Humanoid", "isCreepy carriedBlock"),
    "Entity": ("", "NO_OUTLINE entityType x y z ageInTicks boundingBoxWidth boundingBoxHeight eyeHeight distanceToCameraSq isInvisible isDiscrete displayFireAnimation lightCoords outlineColor passengerOffset nameTag scoreText nameTagAttachment leashStates shadowRadius shadowPieces partialTick"),
    "Equine": ("LivingEntity", "saddle bodyArmorItem isRidden animateTail eatAnimation standAnimation feedingAnimation"),
    "EvokerFangs": ("Entity", "yRot biteProgress"),
    "Evoker": ("Illager", "isCastingSpell"),
    "ExperienceOrb": ("Entity", "icon"),
    "FallingBlock": ("Entity", "movingBlockRenderState"),
    "Feline": ("LivingEntity", "isCrouching isSprinting isSitting lieDownAmount lieDownAmountTail relaxStateOneAmount"),
    "FireworkRocket": ("Entity", "isShotAtAngle item"),
    "FishingHook": ("Entity", "lineOriginOffset"),
    "Fox": ("HoldingEntity", "headRollAngle crouchAmount isCrouching isSleeping isSitting isFaceplanted isPouncing variant"),
    "Frog": ("LivingEntity", "isSwimming jumpAnimationState croakAnimationState tongueAnimationState swimIdleAnimationState texture"),
    "Ghast": ("LivingEntity", "isCharging"),
    "Goat": ("LivingEntity", "hasLeftHorn hasRightHorn rammingXHeadRot"),
    "Guardian": ("LivingEntity", "spikesAnimation tailAnimation eyePosition lookDirection lookAtPosition attackTargetPosition attackTime attackScale"),
    "HappyGhast": ("LivingEntity", "bodyItem isRidden isLeashHolder"),
    "Hoglin": ("LivingEntity", "attackAnimationRemainingTicks isConverting"),
    "HoldingEntity": ("LivingEntity", "heldItem"),
    "Horse": ("Equine", "variant markings"),
    "Humanoid": ("ArmedEntity", "swimAmount speedValue maxCrossbowChargeDuration ticksUsingItem useItemHand isCrouching isFallFlying isVisuallySwimming isPassenger isUsingItem elytraRotX elytraRotY elytraRotZ headEquipment chestEquipment legsEquipment feetEquipment"),
    "Illager": ("Undead", "isRiding isAggressive mainArm armPose maxCrossbowChargeDuration ticksUsingItem attackAnim"),
    "Illusioner": ("Illager", "illusionOffsets isCastingSpell"),
    "IronGolem": ("LivingEntity", "attackTicksRemaining offerFlowerTick flowerBlock crackiness"),
    "ItemCluster": ("Entity", "item count seed shouldSpread"),
    "ItemDisplayEntity": ("DisplayEntity", "item"),
    "ItemEntity": ("ItemCluster", "bobOffset shouldBob"),
    "ItemFrame": ("Entity", "direction frameModel item rotation isGlowFrame mapId mapRenderState"),
    "LightningBolt": ("Entity", "seed"),
    "LivingEntity": ("Entity", "bodyRot yRot xRot deathTime walkAnimationPos walkAnimationSpeed scale ageScale ticksSinceKineticHitFeedback isUpsideDown isFullyFrozen isBaby isInWater isAutoSpinAttack hasRedOverlay isInvisibleToPlayer bedOrientation pose headItem wornHeadAnimationPos wornHeadType wornHeadProfile"),
    "Llama": ("LivingEntity", "variant hasChest bodyItem isTraderLlama"),
    "LlamaSpit": ("Entity", "yRot xRot"),
    "Minecart": ("Entity", "xRot yRot offsetSeed hurtDir hurtTime damageTime displayOffset displayBlockModel isNewRender renderPos posOnRail frontPos backPos"),
    "MinecartTnt": ("Minecart", "fuseRemainingInTicks"),
    "MushroomCow": ("LivingEntity", "variant mushroomModel"),
    "Nautilus": ("LivingEntity", "saddle bodyArmorItem variant"),
    "Painting": ("Entity", "direction variant lightCoordsPerBlock"),
    "Panda": ("HoldingEntity", "variant isUnhappy isSneezing sneezeTime isEating isScared isSitting sitAmount lieOnBackAmount rollAmount rollTime"),
    "Parrot": ("LivingEntity", "variant flapAngle pose"),
    "Phantom": ("LivingEntity", "flapTime size"),
    "Pig": ("LivingEntity", "saddle variant"),
    "Piglin": ("Humanoid", "isBrute isConverting maxCrossbowChageDuration armPose"),
    "PolarBear": ("LivingEntity", "standScale"),
    "Pufferfish": ("LivingEntity", "puffState"),
    "Rabbit": ("LivingEntity", "jumpCompletion isToast variant hopAnimationState idleHeadTiltAnimationState"),
    "Ravager": ("LivingEntity", "stunnedTicksRemaining attackTicksRemaining roarAnimation"),
    "Salmon": ("LivingEntity", "variant"),
    "Sheep": ("LivingEntity", "headEatPositionScale headEatAngleScale isSheared woolColor isJebSheep"),
    "ShulkerBullet": ("Entity", "xRot yRot"),
    "Shulker": ("LivingEntity", "renderOffset color peekAmount yHeadRot yBodyRot attachFace"),
    "Skeleton": ("Humanoid", "isAggressive isShaking isHoldingBow"),
    "Slime": ("LivingEntity", "squish size"),
    "Sniffer": ("LivingEntity", "isSearching diggingAnimationState sniffingAnimationState risingAnimationState feelingHappyAnimationState scentingAnimationState"),
    "SnowGolem": ("LivingEntity", "headBlock"),
    "Squid": ("LivingEntity", "tentacleAngle xBodyRot zBodyRot"),
    "Strider": ("LivingEntity", "saddle isSuffocating isRidden"),
    "SulfurCube": ("Slime", "containedBlock fuseRemainingTicks"),
    "TextDisplayEntity": ("DisplayEntity", "textRenderState cachedInfo"),
    "ThrownItem": ("Entity", "item"),
    "ThrownTrident": ("Entity", "xRot yRot isFoil"),
    "TippableArrow": ("Arrow", "isTipped"),
    "Tnt": ("Entity", "fuseRemainingInTicks blockState"),
    "TropicalFish": ("LivingEntity", "pattern baseColor patternColor"),
    "Turtle": ("LivingEntity", "isOnLand isLayingEgg hasEgg"),
    "Undead": ("Humanoid", ""),
    "Vex": ("ArmedEntity", "isCharging"),
    "Villager": ("HoldingEntity", "isUnhappy villagerData"),
    "Warden": ("LivingEntity", "tendrilAnimation heartAnimation roarAnimationState sniffAnimationState emergeAnimationState diggingAnimationState attackAnimationState sonicBoomAnimationState"),
    "Witch": ("HoldingEntity", "entityId isHoldingItem isHoldingPotion"),
    "Wither": ("LivingEntity", "xHeadRots yHeadRots invulnerableTicks isPowered"),
    "WitherSkull": ("Entity", "isDangerous modelState"),
    "Wolf": ("LivingEntity", "isAngry isSitting tailAngle headRollAngle shakeAnim wetShade texture collarColor bodyArmorItem"),
    "Zombie": ("Undead", "isAggressive isConverting"),
    "ZombieVillager": ("Zombie", "villagerData"),
    "ZombifiedPiglin": ("Undead", "isAggressive"),
}
# concrete vanilla renderers a mod subclasses -> the state 26.2's renderer creates
LEAF = {
    "AllayRenderer": "Allay", "ArmadilloRenderer": "Armadillo", "ArmorStandRenderer": "ArmorStand",
    "AxolotlRenderer": "Axolotl", "BatRenderer": "Bat", "BeeRenderer": "Bee", "BlazeRenderer": "LivingEntity",
    "BoggedRenderer": "Bogged", "BreezeRenderer": "Breeze", "CamelHuskRenderer": "Camel", "CamelRenderer": "Camel",
    "CatRenderer": "Cat", "ChickenRenderer": "Chicken", "CodRenderer": "LivingEntity",
    "CopperGolemRenderer": "CopperGolem", "CowRenderer": "Cow", "CreakingRenderer": "Creaking",
    "CreeperRenderer": "Creeper", "DolphinRenderer": "Dolphin", "DonkeyRenderer": "Donkey",
    "DragonFireballRenderer": "Entity", "DrownedRenderer": "Zombie", "EndCrystalRenderer": "EndCrystal",
    "EnderDragonRenderer": "EnderDragon", "EndermanRenderer": "Enderman", "EndermiteRenderer": "LivingEntity",
    "EvokerFangsRenderer": "EvokerFangs", "EvokerRenderer": "Evoker", "ExperienceOrbRenderer": "ExperienceOrb",
    "FallingBlockRenderer": "FallingBlock", "FireworkEntityRenderer": "FireworkRocket",
    "FishingHookRenderer": "FishingHook", "FoxRenderer": "Fox", "FrogRenderer": "Frog", "GhastRenderer": "Ghast",
    "GiantMobRenderer": "Zombie", "GoatRenderer": "Goat", "GuardianRenderer": "Guardian",
    "HappyGhastRenderer": "HappyGhast", "IllusionerRenderer": "Illusioner", "IronGolemRenderer": "IronGolem",
    "ItemEntityRenderer": "ItemEntity", "ItemFrameRenderer": "ItemFrame", "LeashKnotRenderer": "Entity",
    "LightningBoltRenderer": "LightningBolt", "LlamaRenderer": "Llama", "LlamaSpitRenderer": "LlamaSpit",
    "MagmaCubeRenderer": "Slime", "MinecartRenderer": "Minecart", "MushroomCowRenderer": "MushroomCow",
    "NautilusRenderer": "Nautilus", "NoopRenderer": "Entity", "OcelotRenderer": "Feline",
    "OminousItemSpawnerRenderer": "ItemCluster", "PaintingRenderer": "Painting", "PandaRenderer": "Panda",
    "ParchedRenderer": "Skeleton", "ParrotRenderer": "Parrot", "PhantomRenderer": "Phantom", "PigRenderer": "Pig",
    "PiglinRenderer": "Piglin", "PillagerRenderer": "Illager", "PolarBearRenderer": "PolarBear",
    "PufferfishRenderer": "Pufferfish", "RabbitRenderer": "Rabbit", "RavagerRenderer": "Ravager",
    "SalmonRenderer": "Salmon", "SheepRenderer": "Sheep", "ShulkerBulletRenderer": "ShulkerBullet",
    "ShulkerRenderer": "Shulker", "SilverfishRenderer": "LivingEntity", "SkeletonRenderer": "Skeleton",
    "SlimeRenderer": "Slime", "SnifferRenderer": "Sniffer", "SnowGolemRenderer": "SnowGolem",
    "SpectralArrowRenderer": "Arrow", "SpiderRenderer": "LivingEntity", "SquidRenderer": "Squid",
    "StrayRenderer": "Skeleton", "StriderRenderer": "Strider", "SulfurCubeRenderer": "SulfurCube",
    "TadpoleRenderer": "LivingEntity", "ThrownItemRenderer": "ThrownItem", "ThrownTridentRenderer": "ThrownTrident",
    "TippableArrowRenderer": "TippableArrow", "TntMinecartRenderer": "MinecartTnt", "TntRenderer": "Tnt",
    "TropicalFishRenderer": "TropicalFish", "TurtleRenderer": "Turtle", "UndeadHorseRenderer": "Equine",
    "VexRenderer": "Vex", "VillagerRenderer": "Villager", "VindicatorRenderer": "Illager",
    "WanderingTraderRenderer": "Villager", "WardenRenderer": "Warden", "WindChargeRenderer": "Entity",
    "WitchRenderer": "Witch", "WitherBossRenderer": "Wither", "WitherSkeletonRenderer": "Skeleton",
    "WitherSkullRenderer": "WitherSkull", "WolfRenderer": "Wolf", "ZombieNautilusRenderer": "Nautilus",
    "ZombieRenderer": "Zombie", "ZombieVillagerRenderer": "ZombieVillager",
    "ZombifiedPiglinRenderer": "ZombifiedPiglin",
    "HuskRenderer": "Zombie", "CaveSpiderRenderer": "LivingEntity", "ElderGuardianRenderer": "Guardian",
    "GlowSquidRenderer": "Squid", "HoglinRenderer": "Hoglin", "ZoglinRenderer": "Hoglin",
}
IMPORT_PKG = "net.minecraft.client.renderer.entity.state."
# bases whose generics gain the state: base -> (state arg position, default state, fills humanoid fields, has texture hook)
GEN_BASES = {"EntityRenderer": (1, "Entity", False, False), "LivingEntityRenderer": (1, "LivingEntity", False, True),
             "MobRenderer": (1, "LivingEntity", False, True), "AgeableMobRenderer": (1, "LivingEntity", False, True),
             "HumanoidMobRenderer": (1, "Humanoid", True, True)}
# abstract vanilla renderers that are generic on the entity only and gain a FIXED state argument (no model arg)
SPECIAL_BASES = {"IllagerRenderer": ("Illager", True, True), "ArrowRenderer": ("Arrow", False, True)}
# vanilla models, generic on the entity in 1.21.1: smallest state they accept in 26.2
VMODELS = {"EntityModel": "Entity", "HumanoidModel": "Humanoid", "SkeletonModel": "Skeleton", "ZombieModel": "Zombie",
           "IllagerModel": "Illager"}
# entity bases (vanilla) -> state that carries what a renderer of it needs
LIVING_ENTITY = ("LivingEntity Mob PathfinderMob Monster Animal AgeableMob TamableAnimal FlyingMob WaterAnimal AbstractGolem "
                 "Raider Witch Blaze Ghast Slime Spider Enderman Villager AbstractVillager SpellcasterIllager").split()
PLAIN_ENTITY = ("Entity Projectile AbstractArrow ThrowableProjectile ThrowableItemProjectile AbstractHurtingProjectile Fireball "
                "SmallFireball ItemEntity").split()
ENTITY_STATE = {**{k: "LivingEntity" for k in LIVING_ENTITY}, **{k: "Entity" for k in PLAIN_ENTITY},
                "AbstractSkeleton": "Skeleton", "Skeleton": "Skeleton", "Stray": "Skeleton", "WitherSkeleton": "Skeleton",
                "Zombie": "Zombie", "Husk": "Zombie", "Drowned": "Zombie", "AbstractIllager": "Illager",
                "Vindicator": "Illager", "Pillager": "Illager", "Creeper": "Creeper", "AbstractPiglin": "Piglin",
                "Piglin": "Piglin", "SpellcasterIllager": "Illager"}
# getter on the entity -> (field on the state, state it lives on, needs the humanoid renderer to fill it)
FIELD_MAP = {"isBaby": ("isBaby", "LivingEntity", False), "isInWater": ("isInWater", "LivingEntity", False),
             "isInvisible": ("isInvisible", "Entity", False), "getPose": ("pose", "LivingEntity", False),
             "isCrouching": ("hasPose(Pose.CROUCHING)", "LivingEntity", False), "isUsingItem": ("isUsingItem", "Humanoid", True),
             "isFallFlying": ("isFallFlying", "Humanoid", True), "getSwimAmount": ("swimAmount", "Humanoid", True)}
PRIMS = {"boolean", "byte", "short", "int", "long", "float", "double", "char", "String"}
KEEP_ENTITY = {"getBlockLightLevel", "getSkyLightLevel", "shouldShowName", "isEntityUpsideDown", "getBoundingBoxForCulling",
               "shouldRender", "getRenderOffset"}
REFUSE_METHODS = {"render": "render(...) is the immediate-mode draw, replaced by submit(...) (CATALOG §V6)",
                  "renderNameTag": "renderNameTag is extractNameTags/submit now (CATALOG §V6)",
                  "isShaking": "isShaking takes the state (rewrite by hand)", "getFlipDegrees": "unsupported override",
                  "renderToBuffer": "Model.renderToBuffer is final in 26.2 (CATALOG §V78)",
                  "prepareMobModel": "prepareMobModel is gone; pose lives on the state (CATALOG §V78)"}
LISTM = ("ListModel", "AgeableListModel")
HIER = {"HierarchicalModel", "ListModel", "AgeableListModel", "AgeableHierarchicalModel", "QuadrupedModel", "AgeableModel"}


def sub(a, b):
    """State a is b or inherits from it."""
    while a:
        if a == b:
            return True
        a = STATES.get(a, ("", ""))[0]
    return False


def specific(a, b):
    return a if sub(a, b) else b if sub(b, a) else None


def state_fields(s):
    out = set()
    while s:
        out |= set(STATES.get(s, ("", ""))[1].split())
        s = STATES.get(s, ("", ""))[0]
    return out


def ident(name, text):
    return re.search(r"(?<![\w$.])%s(?![\w$])" % re.escape(name), text) is not None


class Refuse(Exception):
    pass


class Cls:
    def __init__(self):
        self.methods = []


def parse_type(s):
    s = s.strip()
    m = re.match(r"([\w.$]+)\s*(?:<(.*)>)?\s*$", s, re.S)
    if not m:
        return None
    args = None if m.group(2) is None else [a.strip() for a in fs.split_args(m.group(2))]
    return m.group(1).rsplit(".", 1)[-1], args


def simple(t):
    p = parse_type(t)
    return p[0] if p else t.strip()


def tparams_of(text, start):
    """([(name, bound, bound_start, bound_end)], index of the closing '>') for the `<...>` at text[start]."""
    depth, i = 0, start
    while i < len(text):
        if text[i] == "<":
            depth += 1
        elif text[i] == ">":
            depth -= 1
            if depth == 0:
                break
        i += 1
    inner, base = text[start + 1:i], start + 1
    out, off = [], 0
    for seg in fs.split_args(inner):
        m = re.match(r"(\s*)([A-Za-z_]\w*)(\s+extends\s+)?", seg)
        name = m.group(2)
        if m.group(3):
            bs = base + off + m.end()
            out.append((name, seg[m.end():].strip(), bs, bs + len(seg[m.end():].rstrip())))
        else:
            out.append((name, "", None, None))
        off += len(seg) + 1
    return out, i


CLASS_RE = re.compile(r"(?<![\w$.])(class|interface|enum|record)\s+([A-Za-z_]\w*)")


def index_text(path, text):
    masked = ni.code_spans(text)
    out = []
    for m in CLASS_RE.finditer(masked):
        c = Cls()
        c.kind, c.name, c.path, c.head = m.group(1), m.group(2), path, m.start()
        pos = m.end()
        c.tparams, c.tp_start = [], None
        w = re.match(r"\s*", masked[pos:]).end()
        if masked[pos + w:pos + w + 1] == "<":
            c.tp_start = pos + w
            c.tparams, tend = tparams_of(masked, c.tp_start)
            pos = tend + 1
        brace = masked.find("{", pos)
        if brace < 0:
            continue
        header = masked[pos:brace]
        c.ext, c.impls, c.ext_start, c.ext_end = None, [], None, None
        em = re.search(r"\bextends\s+(.*?)(?=\bimplements\b|\Z)", header, re.S) if c.kind == "class" else None
        if em:
            raw = em.group(1)
            c.ext_start, c.ext_end = pos + em.start(1), pos + em.start(1) + len(raw.rstrip())
            c.ext = parse_type(text[c.ext_start:c.ext_end])
        im = re.search(r"\bimplements\s+(.*)", header, re.S)
        if im:
            c.impls = [simple(a) for a in fs.split_args(text[pos + im.start(1):brace])]
        c.body_open, c.body_close = brace, fs.match(masked, brace)
        if c.body_close < 0:
            continue
        out.append(c)
    for mt in fs.methods(text):
        best = None
        for c in out:
            if c.body_open < mt.start < c.body_close and (best is None or c.body_open > best.body_open):
                best = c
        if best:
            best.methods.append(mt)
    return out


class Tree:
    def __init__(self, files, status=None, ro=()):
        self.files, self.by, self.classes, self.status, self.ro = files, {}, [], status if status is not None else {}, set(ro)
        for p, t in files.items():
            for c in index_text(p, t):
                self.classes.append(c)
                self.by.setdefault(c.name, []).append(c)

    def get(self, name):
        v = self.by.get(name)
        if v and len(v) > 1:
            v = [x for x in v if x.path in self.ro] or v          # the ported (context) twin is the truth
        return v[0] if v and len(v) == 1 else None

    def ent_state(self, name, depth=0):
        if name in ENTITY_STATE:
            return ENTITY_STATE[name]
        c = self.get(name)
        if not c or not c.ext or depth > 20:
            return None
        return self.ent_state(c.ext[0], depth + 1)

    def getter_type(self, ent, getter, nargs, depth=0):
        c = self.get(ent)
        if not c or depth > 20:
            return None
        for mt in c.methods:
            if mt.name == getter and len(mt.params) == nargs:
                return " ".join(w for w in mt.ret.split() if w not in fs.MODIFIERS)
        return self.getter_type(c.ext[0], getter, nargs, depth + 1) if c.ext else None

    def is_model(self, c, depth=0):
        if not c.ext or depth > 20:
            return False
        if c.ext[0] in VMODELS or c.ext[0] in HIER:
            return True
        n = self.get(c.ext[0])
        return bool(n) and self.is_model(n, depth + 1)

    def is_layer(self, c, depth=0):
        if not c.ext or depth > 20:
            return False
        if c.ext[0] in ("RenderLayer", "EyesLayer"):
            return True
        n = self.get(c.ext[0])
        return bool(n) and self.is_layer(n, depth + 1)

    def renderer_root(self, c, depth=0):
        if not c.ext or depth > 20:
            return None
        b = c.ext[0]
        if b in GEN_BASES or b in SPECIAL_BASES or b in LEAF:
            return b
        n = self.get(b)
        return self.renderer_root(n, depth + 1) if n else None


def indent_of(text, i):
    s = text.rfind("\n", 0, i) + 1
    return re.match(r"[ \t]*", text[s:]).group(0)


def member_indent(text, c):
    ci = indent_of(text, c.head)
    for mt in c.methods:
        ind = indent_of(text, mt.start)
        if mt.start > c.body_open and ind != ci:
            return ind
    return ci + "    "


def ptype_of(p):
    return fs.ptype(re.sub(r"\bfinal\s+", "", p)).strip()


def overridden(text, m):
    return "@Override" in text[fs.decl_span_start(text, m.start):m.start]


def entity_reads(body, ent, pt_names):
    """Reads of the entity parameter in `body`: ([(start, end, getter, args)], [(reason, pos)])."""
    masked = ni.code_spans(body)
    chains, esc = [], []
    for m in re.finditer(r"(?<![\w$.])%s(?![\w$])" % re.escape(ent), masked):
        mm = re.match(r"\s*\.\s*([A-Za-z_]\w*)\s*(\()?", masked[m.end():])
        if not mm:
            if re.match(r"\s+instanceof\b", masked[m.end():]):
                esc.append(("tests `instanceof` on the entity (a state has no entity; carry the answer as a field by hand)", m.start()))
            else:
                esc.append(("passes the entity on bare", m.start()))
            continue
        if not mm.group(2):
            esc.append((f"reads the field `{ent}.{mm.group(1)}`", m.start()))
            continue
        po = m.end() + mm.end() - 1
        pc = fs.match(masked, po)
        if re.match(r"\s*\.", masked[pc + 1:]):
            esc.append((f"getter chain after `{ent}.{mm.group(1)}()`", m.start()))
            continue
        norm = []
        for a in (x.strip() for x in fs.split_args(body[po + 1:pc])):
            if a in pt_names:
                norm.append("PT")
            elif re.fullmatch(r"-?\d+[LlFfDd]?|-?\d*\.\d+[FfDd]?|true|false", a):
                norm.append(a)
            else:
                esc.append((f"argument `{a}` of `{ent}.{mm.group(1)}(...)` is neither the partial tick nor a literal", m.start()))
        chains.append((m.start(), pc + 1, mm.group(1), tuple(norm)))
    return chains, esc


def apply_edits(text, edits):
    last = len(text) + 1
    for s, e, r in sorted(edits, key=lambda x: (-x[0], -x[1])):
        if e > last:
            raise Refuse("overlapping edits (nested classes converted in one file)")
        text = text[:s] + r + text[e:]
        last = s
    return text


STATE_IMPORTS = {f"{k}RenderState": IMPORT_PKG + f"{k}RenderState" for k in STATES}
STATE_IMPORTS["SubmitNodeCollector"] = "net.minecraft.client.renderer.SubmitNodeCollector"
STATE_IMPORTS["EntityModel"] = "net.minecraft.client.model.EntityModel"
STATE_IMPORTS["Pose"] = "net.minecraft.world.entity.Pose"


def add_imports(text):
    body = ni.code_spans(re.sub(r"(?m)^import[^\n]*\n", "", text))
    for simple_, fq in STATE_IMPORTS.items():
        if re.search(r"(?<![\w.])%s\b" % simple_, body):
            text = ni.add_import(text, fq)
    for gone in ("ListModel", "AgeableListModel", "HierarchicalModel", "ImmutableList"):
        for m in re.finditer(r"(?m)^import\s+([\w.]+\.%s)\s*;" % gone, text):
            text = fs.remove_import_if_unused(text, m.group(1))
    return text


def sname(s):
    return s + "RenderState"


def lower_first(s):
    return s[:1].lower() + s[1:]


class Defer(Exception):
    pass


class Reads:
    """Entity getters read by converted bodies -> state fields (existing, or custom ones on a nested state)."""

    def __init__(self, tree, ent, state, humanoid_fill, custom_ok, ctext):
        self.tree, self.ent, self.state, self.hum, self.custom_ok, self.ctext = tree, ent, state, humanoid_fill, custom_ok, ctext
        self.map, self.custom = {}, {}

    def field(self, key):
        if key in self.map:
            return self.map[key]
        getter, args = key
        fm = FIELD_MAP.get(getter)
        if fm and args == (("PT",) if getter == "getSwimAmount" else ()):
            f, on, hum = fm
            if hum and not self.hum:
                raise Refuse(f"`{getter}()` maps to {sname(on)}.{f}, which only the humanoid renderers fill")
            if not sub(self.state, on):
                raise Refuse(f"`{getter}()` maps to {sname(on)}.{f}, but the state is {sname(self.state)}")
            self.map[key] = f
            return f
        if not self.custom_ok:
            raise Refuse(f"entity getter `{getter}(...)` has no 26.2 state field (a model may not carry custom fields)")
        ty = self.tree.getter_type(self.ent, getter, len(args))
        if ty is None:
            raise Refuse(f"cannot type getter `{getter}(...)`: not declared with a body on a mod entity class in this tree")
        if ty not in PRIMS and not re.search(r"(?m)^import\s+[\w.]+\.%s\s*;" % re.escape(simple(ty)), self.ctext):
            raise Refuse(f"getter `{getter}` returns `{ty}`, which this file does not import")
        base = lower_first(getter[3:]) if re.match(r"get[A-Z]", getter) else getter
        name, taken = base, state_fields(self.state) | set(self.custom)
        while name in taken:
            name += "Value"
        self.map[key] = name
        self.custom[name] = (ty, key)
        return name


def rewrite_reads(body, ent, pt_names, reads, st):
    chains, esc = entity_reads(body, ent, pt_names)
    if esc:
        raise Refuse(esc[0][0] if "`" in esc[0][0] else f"{esc[0][0]} (`{ent}`)")
    for s, e, getter, args in sorted(chains, key=lambda x: -x[0]):
        body = body[:s] + f"{st}.{reads.field((getter, args))}" + body[e:]
    return body


def fresh(name, *scopes):
    n, k = name, 2
    while any(ident(n, s) for s in scopes):
        n, k = f"{name}{k}", k + 1
    return n


def entity_name_of(c, E):
    for n, b, _, _ in c.tparams:
        if n == E:
            return simple(b.split("&")[0]) if b else None
    return simple(E)


def model_state(tree, mc):
    for n, b, _, _ in mc.tparams:
        if b.endswith("RenderState"):
            return simple(b)[:-len("RenderState")]
    if not mc.tparams and mc.ext and mc.ext[1] and mc.ext[1][0].endswith("RenderState"):
        return simple(mc.ext[1][0])[:-len("RenderState")]
    return None


def layer_state(tree, lc):
    return model_state(tree, lc)


def has_ctor_call(masked_body, name):
    return re.search(r"(?<![\w$.])%s\s*\(" % name, masked_body)


# ------------------------------------------------------------------------------------------------ renderers
HANDLED = {"getTextureLocation": 1, "scale": 3, "getWhiteOverlayProgress": 2, "setupRotations": 6}


def conv_renderer(tree, c, text):
    if not c.ext:
        return None
    base, args = c.ext
    root = tree.renderer_root(c)
    if root is None:
        return None
    if base not in GEN_BASES and base not in SPECIAL_BASES and base not in LEAF:
        raise Refuse(f"extends the mod renderer `{base}`; its state type and arity are its base's (convert that class by hand)")
    masked = ni.code_spans(text)
    body_m = masked[c.body_open:c.body_close]
    kind = "gen" if base in GEN_BASES else "special" if base in SPECIAL_BASES else "leaf"
    E, Mtxt, S = None, None, None
    humanoid, texhook = False, True
    if kind == "gen":
        _, dflt, humanoid, texhook = GEN_BASES[base]
        old = 1 if base == "EntityRenderer" else 2
        if args is not None and len(args) == old + 1:
            return "already"
        if not args or len(args) != old:
            raise Refuse(f"`{base}` is written with {len(args or [])} type argument(s); expected {old}")
        E = args[0]
        Mtxt = args[1] if old == 2 else None
        S = dflt
    elif kind == "special":
        S, humanoid, texhook = SPECIAL_BASES[base]
        if args is not None and len(args) == 2:
            return "already"
        if not args or len(args) != 1:
            raise Refuse(f"`{base}` is written with {len(args or [])} type argument(s); expected 1")
        E = args[0]
    else:
        S = LEAF[base]
        humanoid = sub(S, "Humanoid")
    ent = entity_name_of(c, E) if E else None
    if kind in ("gen", "special") and base != "EntityRenderer" and ent:
        es = tree.ent_state(ent)
        if es:
            sp = specific(S, es)
            if sp is None:
                raise Refuse(f"entity `{ent}` needs {sname(es)} but `{base}` implies {sname(S)}")
            S = sp
    for mt in c.methods:
        if mt.name in REFUSE_METHODS and any(ptype_of(p) in (E, ent) for p in mt.params if E):
            raise Refuse(f"{mt.name}(...): {REFUSE_METHODS[mt.name]}")
        if mt.name in ("render",) and kind == "leaf" and mt.params and not E:
            raise Refuse(f"render(...): {REFUSE_METHODS['render']}")
    # item-in-hand layers
    edits = []
    for m in re.finditer(r"new\s+ItemInHandLayer\b", body_m):
        s = c.body_open + m.start()
        po = masked.index("(", s + m.end() - m.start())
        pc = fs.match(masked, po)
        a = [x.strip() for x in fs.split_args(text[po + 1:pc])]
        if len(a) == 2 and a[0] == "this" and re.fullmatch(r"\w+\.getItemInHandRenderer\(\)", a[1]):
            edits.append((s, pc + 1, "new ItemInHandLayer<>(this)"))
            sp = specific(S, "ArmedEntity")
            if sp is None or (kind == "leaf" and sp != S):
                raise Refuse(f"ItemInHandLayer needs ArmedEntityRenderState; {sname(S)} does not extend it")
            S = sp
        elif len(a) != 1:
            raise Refuse("ItemInHandLayer constructor arguments are not `(this, <ctx>.getItemInHandRenderer())`")
    # the model argument
    Mnew = None
    if Mtxt is not None:
        mp = parse_type(Mtxt)
        mc = tree.get(mp[0]) if mp else None
        if mc is not None and tree.is_model(mc):
            cons = model_state(tree, mc)
            if cons is None:
                why = tree.status.get((mc.path, mc.name), (0, 0, ""))[2]
                raise Refuse(f"model `{mp[0]}` is not render-state typed" + (f" (it was refused: {why})" if why else ""))
        elif mp and mp[0] in VMODELS and mp[1]:
            cons = VMODELS[mp[0]]
        elif mc is not None:
            raise Refuse(f"model `{mp[0]}` extends outside this tree (pass its library with --context so the chain can be followed)")
        else:
            raise Refuse(f"model `{mp[0] if mp else Mtxt}` is a vanilla model type (moved/non-generic in 26.2; rewrite by hand)")
        if sub(S, cons):
            pass
        elif sub(cons, S):
            S = cons
        else:
            raise Refuse(f"state {sname(S)} does not satisfy the model's bound {sname(cons)}")
        if humanoid and not sub(S, "Humanoid"):
            S = "Humanoid"
        mnew_args = None if mp[1] is None else ["@S@" if a == E else a for a in mp[1]]
        Mnew = Mtxt.strip() if mnew_args is None else Mtxt.strip().split("<", 1)[0] + "<" + ", ".join(mnew_args) + ">"
    # overridden methods
    reads = Reads(tree, ent, S, humanoid, kind != "leaf", text)
    mods = []
    ent_text = E
    for mt in c.methods:
        if mt.name in HANDLED:
            want = HANDLED[mt.name]
            ptypes = [ptype_of(p) for p in mt.params]
            if len(mt.params) == want and ptypes and ptypes[0].endswith("RenderState"):
                continue                                   # already converted
            if not texhook and mt.name != "getTextureLocation":
                raise Refuse(f"{mt.name}(...) is not a hook of `{base}`")
            if len(mt.params) != want:
                raise Refuse(f"{mt.name}(...) has an unexpected parameter list")
            want_types = {"getTextureLocation": [None], "scale": [None, "PoseStack", "float"], "getWhiteOverlayProgress": [None, "float"],
                          "setupRotations": [None, "PoseStack", "float", "float", "float", "float"]}[mt.name]
            if any(w is not None and w != t for w, t in zip(want_types, ptypes)):
                raise Refuse(f"{mt.name}(...) has an unexpected parameter list")
            if kind == "leaf":
                if ent_text and ptypes[0] != ent_text:
                    raise Refuse("overrides take different entity types")
                ent_text = ptypes[0]
            elif ptypes[0] not in (E, ent):
                raise Refuse(f"{mt.name}({ptypes[0]}, ...) does not take the renderer's entity type")
            mods.append(mt)
        elif mt.name in KEEP_ENTITY or mt.name in ("createRenderState", "extractRenderState", "getArmPose"):
            if mt.name == "getArmPose":
                raise Refuse("getArmPose(...): the arm pose is a state field filled by the renderer now (CATALOG §V6)")
            continue
        elif any(E and ptype_of(p) in (E, ent) for p in mt.params):
            raise Refuse(f"{mt.name}(...) takes the entity; only getTextureLocation/scale/getWhiteOverlayProgress/setupRotations are converted")
    has_create = any(mt.name == "createRenderState" and not mt.params for mt in c.methods)
    has_extract = any(mt.name == "extractRenderState" for mt in c.methods)
    unit = member_indent(text, c)[len(indent_of(text, c.head)):] or "    "
    mi = member_indent(text, c)
    for mt in mods:
        names = [fs.pname(p) for p in mt.params]
        ep = names[0]
        body = text[mt.body_open + 1:mt.body_close]
        scope = text[mt.start:mt.body_close]
        st = fresh("renderState", scope)
        pt = {"scale": names[2] if mt.name == "scale" else None, "getWhiteOverlayProgress": names[1] if mt.name == "getWhiteOverlayProgress" else None,
              "setupRotations": names[4] if mt.name == "setupRotations" else None}.get(mt.name)
        newargs = [st]
        if mt.name == "setupRotations":
            newargs = [st, names[1], names[3], names[5]]
        elif mt.name == "scale":
            newargs = [st, names[1]]
        mb = ni.code_spans(body)
        for sm in sorted(re.finditer(r"(?<![\w$.])super\s*\.\s*%s\s*\(" % mt.name, mb), key=lambda x: -x.start()):
            spo = sm.end() - 1
            spc = fs.match(mb, spo)
            if [x.strip() for x in fs.split_args(body[spo + 1:spc])] == names:
                body = body[:spo + 1] + ", ".join(newargs) + body[spc:]
                mb = ni.code_spans(body)
        try:
            newbody = rewrite_reads(body, ep, {pt} if pt else set(), reads, st)
        except Refuse as e:
            raise Refuse(f"{mt.name}(...): {e}")
        prolog = []
        if pt and ident(pt, newbody):
            prolog.append(f"float {pt} = {st}.partialTick;")
        if mt.name == "setupRotations":
            age = names[2]
            if ident(age, newbody):
                prolog.append(f"float {age} = {st}.ageInTicks;")
            params = [f"@S@ {st}", mt.params[1], mt.params[3], mt.params[5]]
        elif mt.name == "scale":
            params = [f"@S@ {st}", mt.params[1]]
        else:
            params = [f"@S@ {st}"]
        ind = indent_of(text, mt.body_open) + unit
        pro = "".join(f"\n{ind}{p}" for p in prolog)
        mods_edit = [(mt.params_open + 1, mt.params_close, ", ".join(params)), (mt.body_open + 1, mt.body_close, pro + newbody)]
        edits += mods_edit
        has_override = overridden(text, mt)
        if not has_override and (texhook):
            ls = text.rfind("\n", 0, mt.start) + 1
            edits.append((ls, ls, indent_of(text, mt.start) + "@Override\n"))
    # state class
    custom = reads.custom
    stem = c.name[:-len("Renderer")] if c.name.endswith("Renderer") else c.name
    if custom:
        if kind == "leaf":
            raise Refuse("needs custom state fields, but a vanilla leaf renderer's createRenderState cannot return a subclass the overrides would accept")
        if has_extract:
            raise Refuse("already declares extractRenderState; merge the entity reads by hand")
        cname = fresh(stem + "RenderState", text)
        sref = cname
    else:
        sref = sname(S)
    # header
    if kind == "gen":
        new_ext = f"{base}<{E}, {sref}, {Mnew.replace('@S@', sref)}>" if Mnew is not None else f"{base}<{E}, {sref}>"
    elif kind == "special":
        new_ext = f"{base}<{E}, {sref}>"
    else:
        new_ext = None
    if new_ext:
        edits.append((c.ext_start, c.ext_end, new_ext))
    for i, (s_, e_, r_) in enumerate(edits):
        edits[i] = (s_, e_, r_.replace("@S@", sref))
    # fields/locals typed `Model<E>` follow the model's retarget
    if Mtxt is not None or kind == "special":
        spans = [(a_, b_) for a_, b_, _ in edits if a_ >= c.body_open]
        for m in re.finditer(r"(?<![\w$.])(\w+)\s*<\s*%s\s*>" % re.escape(E), masked[c.body_open:c.body_close]):
            mc2 = tree.get(m.group(1))
            s0 = c.body_open + m.start()
            if mc2 is not None and tree.is_model(mc2) and not any(a_ <= s0 < b_ for a_, b_ in spans):
                gs = c.body_open + m.start() + m.group(0).index("<") + 1
                edits.append((gs, c.body_open + m.end() - 1, sref))
    # members
    ci = indent_of(text, c.head)
    add_start, add_end = "", ""
    if custom:
        fields = "".join(f"\n{mi}{unit}public {ty} {n};" for n, (ty, _) in custom.items())
        add_start = f"\n{mi}public static class {cname} extends {sname(S)} {{{fields}\n{mi}}}\n"
    if kind != "leaf" and not has_create:
        add_end += f"\n{mi}@Override\n{mi}public {sref} createRenderState() {{\n{mi}{unit}return new {sref}();\n{mi}}}\n"
    if custom:
        ev = fresh("entity", text[c.body_open:c.body_close])
        pt = fresh("partialTick", text[c.body_open:c.body_close])
        st = fresh("renderState", text[c.body_open:c.body_close])
        etype = E
        lines = [f"{mi}{unit}super.extractRenderState({ev}, {st}, {pt});"]
        for n, (ty, (getter, a)) in custom.items():
            lines.append(f"{mi}{unit}{st}.{n} = {ev}.{getter}({', '.join(pt if x == 'PT' else x for x in a)});")
        add_end += (f"\n{mi}@Override\n{mi}public void extractRenderState({etype} {ev}, {sref} {st}, float {pt}) {{\n"
                    + "\n".join(lines) + f"\n{mi}}}\n")
    if add_start:
        edits.append((c.body_open + 1, c.body_open + 1, add_start))
    if add_end:
        close_ls = text.rfind("\n", 0, c.body_close) + 1
        at = close_ls if not text[close_ls:c.body_close].strip() else c.body_close
        edits.append((at, at, add_end))
    if not edits:
        return "already"
    return edits, [sref]


# ------------------------------------------------------------------------------------------------ models
def conv_model(tree, c, text):
    if not c.ext or not tree.is_model(c):
        return None
    b, args = c.ext
    if b in HIER and b not in LISTM and b != "HierarchicalModel":
        raise Refuse(f"extends {b}: needs the root passed to Model's constructor and its animation helpers moved (CATALOG §V78)")
    if "ArmedModel" in c.impls:
        raise Refuse("implements ArmedModel: translateToHand changed shape (takes the state) -- by hand")
    if not args:
        raise Refuse(f"`{b}` is written raw")
    masked = ni.code_spans(text)
    b0, lm, hier_field = b, [], None
    if b in LISTM:
        lm = list_model_edits(c, text, masked, b)
        lm.append((c.ext_start, c.ext_start + len(b), "EntityModel"))
        b = "EntityModel"
    elif b == "HierarchicalModel":
        lm, hier_field = hier_edits(c, text, masked)
        lm.append((c.ext_start, c.ext_start + len(b), "EntityModel"))
        b = "EntityModel"
    a0 = args[0]
    tp = next((t for t in c.tparams if t[0] == a0), None)
    concrete = tp is None
    if (tp and tp[1].endswith("RenderState")) or (concrete and a0.endswith("RenderState")):
        return "already"
    if tp and not tp[1]:
        raise Refuse("the entity type parameter has no bound")
    if tp and "&" in tp[1] and (b in VMODELS or tree.get(b) is None):
        raise Refuse(f"intersection bound `{tp[1]}`: the extra bound does not exist on a render state")
    ent = simple(tp[1].split("&")[0]) if tp else simple(a0)
    removed_api_check(c, text, masked)
    if b in VMODELS:
        if b == "EntityModel":
            new = tree.ent_state(ent)
            if new is None:
                raise Refuse(f"entity `{ent}` is not a known vanilla or mod entity class (cannot choose a state)")
        else:
            new = VMODELS[b]
    else:
        bc = tree.get(b)
        new = model_state(tree, bc) if bc else None
        if new is None:
            if bc is not None:
                raise Defer()
            raise Refuse(f"base model `{b}` is unknown")
    edits = list(lm)
    if tp:
        edits.append((tp[2], tp[3], sname(new)))
    else:
        i = text.index("<", c.ext_start) + 1
        j = text.index(a0, i)
        edits.append((j, j + len(a0), sname(new)))
    ents = {tp[0] if tp else a0, ent}
    for mt in c.methods:
        if mt.name in REFUSE_METHODS and mt.name != "render":
            raise Refuse(f"{mt.name}(...): {REFUSE_METHODS[mt.name]}")
    reads = Reads(tree, ent, new, False, False, text)
    unit = member_indent(text, c)[len(indent_of(text, c.head)):] or "    "
    has_setup = False
    for mt in c.methods:
        types = [ptype_of(p) for p in mt.params]
        if mt.name == "setupAnim" and len(mt.params) == 6 and types[0] in ents and types[1:] == ["float"] * 5:
            has_setup = True
            names = [fs.pname(p) for p in mt.params]
            body = text[mt.body_open + 1:mt.body_close]
            if hier_field and hier_field != "root":
                body = re.sub(r"(?<![\w$.])(?:this\s*\.\s*)?%s(?![\w$])" % hier_field, "this.root", body)
            st = fresh("renderState", text[mt.start:mt.body_close])
            mbody = ni.code_spans(body)
            # super.setupAnim(e, f1..f5) -> super.setupAnim(state)
            for m in sorted(re.finditer(r"(?<![\w$.])super\s*\.\s*setupAnim\s*\(", mbody), key=lambda x: -x.start()):
                po = m.end() - 1
                pc = fs.match(mbody, po)
                if [x.strip() for x in fs.split_args(body[po + 1:pc])] != names:
                    raise Refuse("super.setupAnim(...) is called with other values than the model's own parameters")
                body = body[:po + 1] + st + body[pc:]
                mbody = ni.code_spans(body)
            body = rewrite_bare(body, names[0], st, c, ents)
            body = rewrite_reads(body, names[0], set(), reads, st)
            prolog = []
            bridge = [("walkAnimationPos", "LivingEntity"), ("walkAnimationSpeed", "LivingEntity"), ("ageInTicks", "Entity"),
                      ("yRot", "LivingEntity"), ("xRot", "LivingEntity")]
            for nm, (fld, on) in zip(names[1:], bridge):
                if ident(nm, body):
                    if not sub(new, on):
                        raise Refuse(f"reads `{nm}` ({fld}), which {sname(new)} does not have")
                    prolog.append(f"float {nm} = {st}.{fld};")
            direct = b == "EntityModel"
            if direct and not re.search(r"super\s*\.\s*setupAnim", body):
                prolog.insert(0, f"super.setupAnim({st});")
            ind = indent_of(text, mt.body_open) + unit
            pro = "".join(f"\n{ind}{p}" for p in prolog)
            edits.append((mt.params_open + 1, mt.params_close, f"{mt.params[0].rsplit(None, 1)[0]} {st}"))
            edits.append((mt.body_open + 1, mt.body_close, pro + body))
            if not overridden(text, mt):
                ls = text.rfind("\n", 0, mt.start) + 1
                edits.append((ls, ls, indent_of(text, mt.start) + "@Override\n"))
        elif mt.name == "setupAnim" and len(mt.params) == 6:
            raise Refuse("setupAnim(...) has an unexpected parameter list")
        elif any(t in ents for t in types) and mt.name != "setupAnim":
            nm = [fs.pname(p) for p, t in zip(mt.params, types) if t in ents]
            for n_ in nm:
                if re.search(r"(?<![\w$.])%s\s*\." % re.escape(n_), ni.code_spans(text[mt.body_open:mt.body_close])):
                    raise Refuse(f"{mt.name}(...) reads its entity parameter `{n_}`")
    if b0 == "EntityModel":
        edits += model_ctors(c, text, masked)
    return edits, [sname(new)]


ANIM_API = ("animateWalk", "animate", "applyStatic")
GONE_FIELDS = ("riding", "young", "attackTime")


def removed_api_check(c, text, masked):
    own = {mt.name for mt in c.methods}
    body = masked[c.body_open:c.body_close]
    for name in ANIM_API:
        if name not in own and re.search(r"(?<![\w$])%s\s*\(" % name, body):
            raise Refuse(f"calls HierarchicalModel's `{name}(...)`: 26.2 bakes a KeyframeAnimation once per definition and applies it "
                         "to an AnimationState carried on the render state (CATALOG §V65b, §V78) -- by hand, as the library model does")
    for f in GONE_FIELDS:
        if not re.search(r"\b(?:boolean|float|int)\s+%s\b" % f, body) and re.search(r"(?<![\w$])this\s*\.\s*%s\b" % f, body):
            raise Refuse(f"reads EntityModel.{f}, which is gone in 26.2 (a render-state field now) -- by hand")


def hier_edits(c, text, masked):
    """HierarchicalModel with a kept root: `root()` returns field F, constructors assign it first."""
    roots = [mt for mt in c.methods if mt.name == "root" and not mt.params]
    if len(roots) != 1:
        raise Refuse("extends HierarchicalModel without a single root() override (CATALOG §V78)")
    rm = roots[0]
    mm = re.fullmatch(r"return(?:this\.)?(\w+);", re.sub(r"\s+", "", masked[rm.body_open + 1:rm.body_close]))
    if not mm:
        raise Refuse("root() does something other than return a field (CATALOG §V78)")
    F = mm.group(1)
    fm = list(re.finditer(r"(?m)^[ \t]*(?:(?:private|protected|public)\s+)?(?:final\s+)?ModelPart\s+%s\s*;[ \t]*\n(?:[ \t]*\n)?" % F,
                          masked[c.body_open:c.body_close]))
    if len(fm) != 1:
        raise Refuse(f"root() returns `{F}`, which is not a `ModelPart` field of this class (CATALOG §V78)")
    s = fs.decl_span_start(text, rm.start)
    s -= 1 if s > 0 and text[s - 1] == "\n" else 0
    out = [(c.body_open + fm[0].start(), c.body_open + fm[0].end(), ""), (s, rm.body_close + 1, "")]
    spans = [(a, b_) for a, b_, _ in out]
    spans += [(mt.body_open, mt.body_close + 1) for mt in c.methods if mt.name == "setupAnim" and len(mt.params) == 6]
    ctors = list(ctor_spans(c, text, masked))
    if not ctors:
        raise Refuse("no explicit constructor to carry super(root) (Model takes the root in 26.2)")
    for m, po, pc, bo, bc in ctors:
        first = masked[bo + 1:bc].lstrip()
        if first.startswith("this("):
            if re.match(r"this\(\s*null\b", first):
                raise Refuse("a constructor delegates a null root; Model needs a real root part in 26.2")
            continue
        am = re.match(r"(?:this\s*\.\s*)?%s\s*=\s*" % F, first)
        if not am:
            raise Refuse(f"a constructor does not start with `this.{F} = <root>;` (Model takes the root in 26.2)")
        s0 = bo + 1 + (len(masked[bo + 1:bc]) - len(first))
        e0 = fs_semicolon(masked, s0 + am.end())
        out.append((s0, e0 + 1, f"super({text[s0 + am.end():e0].strip()});"))
        spans.append((s0, e0 + 1))
    if F != "root":
        for m in re.finditer(r"(?<![\w$.])(?:this\s*\.\s*)?%s(?![\w$])" % F, masked[c.body_open:c.body_close]):
            a = c.body_open + m.start()
            if any(x <= a < y for x, y in spans):
                continue
            if re.match(r"\s*=(?!=)", masked[c.body_open + m.end():]):
                raise Refuse(f"`{F}` is assigned outside a constructor's first statement")
            out.append((a, c.body_open + m.end(), "this.root"))
    return out, F


def ctor_spans(c, text, masked):
    pat = re.compile(r"(?<![\w$.])%s\s*\(" % re.escape(c.name))
    for m in pat.finditer(masked, c.body_open, c.body_close):
        if re.search(r"\bnew\s*$", masked[max(0, m.start() - 8):m.start()]):
            continue
        po = m.end() - 1
        pc = fs.match(masked, po)
        rest = re.match(r"\s*(?:throws[^{]*)?\{", masked[pc + 1:])
        if rest:
            bo = pc + 1 + rest.end() - 1
            yield m, po, pc, bo, fs.match(masked, bo)


def list_model_edits(c, text, masked, base):
    """ListModel/AgeableListModel with the single-root shape: `root` field + parts()/bodyParts() returning it."""
    fields = [m for m in re.finditer(r"(?m)^[ \t]*(?:(?:private|protected|public)\s+)?(?:final\s+)?ModelPart\s+(\w+)\s*;[ \t]*\n(?:[ \t]*\n)?",
                                    masked[c.body_open:c.body_close])]
    if len(fields) != 1 or fields[0].group(1) != "root":
        raise Refuse(f"extends {base}, but the class does not have exactly one `ModelPart root` field (CATALOG §V78)")
    out = [(c.body_open + fields[0].start(), c.body_open + fields[0].end(), "")]
    want = {"ListModel": {"parts": "ImmutableList.of(this.root)"},
            "AgeableListModel": {"headParts": "ImmutableList.of()", "bodyParts": "ImmutableList.of(this.root)"}}[base]
    seen = set()
    for mt in c.methods:
        if mt.name in want and not mt.params:
            body = re.sub(r"\s+", "", ni.code_spans(text[mt.body_open + 1:mt.body_close]))
            if body.replace("this.", "") != f"return{want[mt.name]};".replace("this.", ""):
                raise Refuse(f"{mt.name}() does something other than return the root (CATALOG §V78)")
            seen.add(mt.name)
            s = fs.decl_span_start(text, mt.start)
            s -= 1 if s > 0 and text[s - 1] == "\n" else 0
            out.append((s, mt.body_close + 1, ""))
    if seen != set(want):
        raise Refuse(f"extends {base} without the {'/'.join(sorted(want))} override that returns the root (CATALOG §V78)")
    ctors = list(ctor_spans(c, text, masked))
    if not ctors:
        raise Refuse("no explicit constructor to carry super(root) (Model takes the root in 26.2)")
    for m, po, pc, bo, bc in ctors:
        first = masked[bo + 1:bc].lstrip()
        am = re.match(r"(?:this\s*\.\s*)?root\s*=\s*", first)
        if first.startswith("this("):
            continue
        if not am:
            raise Refuse("a constructor does not start with `this.root = <expr>;` (Model takes the root in 26.2)")
        s = bo + 1 + (len(masked[bo + 1:bc]) - len(first))
        e = fs_semicolon(masked, s + am.end())
        out.append((s, e + 1, f"super({text[s + am.end():e].strip()});"))
    return out


def fs_semicolon(masked, i):
    depth = 0
    while i < len(masked):
        ch = masked[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == ";" and depth == 0:
            return i
        i += 1
    raise Refuse("unterminated statement in constructor")


def rewrite_bare(body, ep, st, c, ents):
    """A bare use of the entity parameter is legal only as an argument a method of this class takes as T."""
    masked = ni.code_spans(body)
    for m in sorted(re.finditer(r"(?<![\w$.])%s(?![\w$])(?!\s*\.)" % re.escape(ep), masked), key=lambda x: -x.start()):
        depth, i, idx = 0, m.start() - 1, 0
        while i >= 0:
            ch = masked[i]
            if ch in ")]}":
                depth += 1
            elif ch in "([{":
                if depth == 0:
                    break
                depth -= 1
            elif ch == "," and depth == 0:
                idx += 1
            i -= 1
        nm = re.search(r"([A-Za-z_]\w*)\s*$", masked[:i]) if i >= 0 and masked[i] == "(" else None
        ok = False
        if nm:
            for mt in c.methods:
                if mt.name == nm.group(1) and len(mt.params) > idx and ptype_of(mt.params[idx]) in ents:
                    ok = True
        if not ok:
            raise Refuse(f"passes the entity `{ep}` on to something other than this class's own methods")
        body = body[:m.start()] + st + body[m.end():]
    return body


def model_ctors(c, text, masked):
    out = []
    pat = re.compile(r"(?<![\w$.])%s\s*\(" % re.escape(c.name))
    found = False
    for m in pat.finditer(masked, c.body_open, c.body_close):
        if re.search(r"\bnew\s*$", masked[max(0, m.start() - 8):m.start()]):
            continue
        po = m.end() - 1
        pc = fs.match(masked, po)
        rest = re.match(r"\s*(?:throws[^{]*)?\{", masked[pc + 1:])
        if not rest:
            continue
        found = True
        bo = pc + 1 + rest.end() - 1
        params = [x.strip() for x in fs.split_args(text[po + 1:pc])]
        first = masked[bo + 1:fs.match(masked, bo)].strip()
        if first.startswith("this("):
            continue
        if first.startswith("super("):
            if re.match(r"super\(\s*\w+\s*\)", first) and any(ptype_of(p) == "ModelPart" and fs.pname(p) == re.match(r"super\(\s*(\w+)", first).group(1) for p in params):
                continue
            raise Refuse("constructor already calls super(...) with something other than the root part")
        roots = [fs.pname(p) for p in params if ptype_of(p) == "ModelPart"]
        if len(roots) != 1:
            raise Refuse("constructor has no single ModelPart parameter to pass to super(root) (Model takes the root in 26.2)")
        ind = indent_of(text, m.start()) + (member_indent(text, c)[len(indent_of(text, c.head)):] or "    ")
        out.append((bo + 1, bo + 1, f"\n{ind}super({roots[0]});"))
    if not found:
        raise Refuse("no explicit constructor to carry super(root) (Model takes the root in 26.2)")
    return out


# ------------------------------------------------------------------------------------------------ layers
def conv_layer(tree, c, text):
    if not c.ext or not tree.is_layer(c):
        return None
    b, args = c.ext
    if not args or len(args) < 2:
        raise Refuse(f"`{b}` is written raw or non-generic (vanilla layer/model types: by hand)")
    a0 = args[0]
    tp = next((t for t in c.tparams if t[0] == a0), None)
    if tp is None:
        raise Refuse("the layer is not generic on the entity (vanilla entity type in the extends clause: by hand)")
    if tp[1].endswith("RenderState"):
        return "already"
    if not tp[1]:
        raise Refuse("the entity type parameter has no bound")
    if "&" in tp[1] and (b in ("RenderLayer", "EyesLayer") or tree.get(b) is None):
        raise Refuse(f"intersection bound `{tp[1]}`: the extra bound does not exist on a render state")
    if b in ("RenderLayer", "EyesLayer"):
        new = tree.ent_state(simple(tp[1]))
        if new is None:
            raise Refuse(f"entity `{simple(tp[1])}` is not a known vanilla or mod entity class (cannot choose a state)")
    else:
        bc = tree.get(b)
        new = layer_state(tree, bc) if bc else None
        if new is None:
            if bc is not None:
                raise Defer()
            raise Refuse(f"base layer `{b}` is unknown")
    edits = [(tp[2], tp[3], sname(new))]
    if b == "RenderLayer":
        for n, bound, bs, be in c.tparams:
            if n == args[1] and re.fullmatch(r"EntityModel\s*<\s*%s\s*>" % re.escape(a0), bound):
                edits.append((bs, be, f"EntityModel<? super {a0}>"))
    ents = {a0, simple(tp[1].split("&")[0])}
    unit = member_indent(text, c)[len(indent_of(text, c.head)):] or "    "
    for mt in c.methods:
        types = [ptype_of(p) for p in mt.params]
        if mt.name == "render":
            names = [fs.pname(p) for p in mt.params]
            body = ni.code_spans(text[mt.body_open + 1:mt.body_close]).strip()
            m = re.fullmatch(r"super\s*\.\s*render\s*\((.*)\)\s*;", body, re.S)
            ok = (len(mt.params) == 10 and types[1] == "MultiBufferSource" and types[3] in ents
                  and types[2] == "int" and types[4:] == ["float"] * 6 and m
                  and [x.strip() for x in fs.split_args(m.group(1))] == names)
            if not ok:
                raise Refuse("render(...) does something of its own: the recorded-draw rewrite to submit(...) is by hand (CATALOG §V6)")
            n = names
            new = (f"public void submit(PoseStack {n[0]}, SubmitNodeCollector {n[1]}, int {n[2]}, {types[3]} {n[3]}, float {n[8]}, float {n[9]}) {{\n"
                   f"{indent_of(text, mt.start)}{unit}super.submit({n[0]}, {n[1]}, {n[2]}, {n[3]}, {n[8]}, {n[9]});\n{indent_of(text, mt.start)}}}")
            ls = text.rfind("\n", 0, mt.start) + 1
            pre = "" if overridden(text, mt) else indent_of(text, mt.start) + "@Override\n"
            if pre:
                edits.append((ls, ls, pre))
            lead = len(text[mt.start:]) - len(text[mt.start:].lstrip(" \t"))
            edits.append((mt.start + lead, mt.body_close + 1, new))
        elif any(t in ents for t in types):
            for p, t in zip(mt.params, types):
                if t in ents and re.search(r"(?<![\w$.])%s\s*\." % re.escape(fs.pname(p)), ni.code_spans(text[mt.body_open:mt.body_close])):
                    raise Refuse(f"{mt.name}(...) reads its entity parameter `{fs.pname(p)}`")
    return edits, [sname(new), "SubmitNodeCollector"]


# ------------------------------------------------------------------------------------------------ driver
PHASES = (("model", conv_model), ("layer", conv_layer), ("renderer", conv_renderer))
POINTER = {"model": "CATALOG §V78", "layer": "CATALOG §V6", "renderer": "CATALOG §V6/§V61"}


def convert_files(files, ro=()):
    status = {}
    files = dict(files)
    for kind, fn in PHASES:
        pending, progress = set(), True
        while progress:
            progress = False
            tree = Tree(files, status, ro)
            touched = set()
            for c in tree.classes:
                key = (c.path, c.name)
                if key in status or c.path in touched or c.path in tree.ro:
                    continue
                if any(x.path in tree.ro for x in tree.by.get(c.name, ())):
                    status[key] = (kind, "already", "ported in --context")
                    continue
                try:
                    r = fn(tree, c, files[c.path])
                    if r is None:
                        continue
                    if r == "already":
                        status[key] = (kind, "already", "")
                        continue
                    edits, _ = r
                    files[c.path] = apply_edits(files[c.path], edits)
                except Defer:
                    pending.add(key)
                    continue
                except Refuse as e:
                    status[key] = (kind, "refused", str(e))
                    continue
                status[key] = (kind, "converted", "")
                touched.add(c.path)
                progress = True
        for key in pending:
            if key not in status:
                status[key] = (PHASES[[k for k, _ in PHASES].index(kind)][0], "refused", "its base class was not converted (see that class)")
    return files, status


def run(src, dry=False, context=()):
    root = pathlib.Path(src)
    orig = {}
    for f in sorted(root.rglob("*.java")):
        orig[f] = f.read_text(encoding="utf-8")
    ro = set()
    for d in context:
        for f in sorted(pathlib.Path(d).rglob("*.java")):
            if f not in orig:
                orig[f] = f.read_text(encoding="utf-8")
                ro.add(f)
    new, status = convert_files(orig, ro)
    changed = 0
    for f, t in new.items():
        if f not in ro and t != orig[f]:
            t = add_imports(t)
            changed += 1
            if not dry:
                f.write_text(t, encoding="utf-8")
    return status, orig, changed


def report(status, orig, changed, dry, out=print):
    counts = {}
    reasons = {}
    for (path, name), (kind, st, why) in sorted(status.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
        counts.setdefault(kind, {}).setdefault(st, 0)
        counts[kind][st] += 1
        m = re.search(r"(?<![\w$.])(?:class|record)\s+%s\b" % re.escape(name), ni.code_spans(orig[path]))
        loc = f"{path}:{fs.line_of(orig[path], m.start()) if m else 1}"
        if st == "refused":
            out(f"  REFUSED {kind} {loc} {name}: {why} [{POINTER[kind]}]")
            reasons.setdefault(kind, {}).setdefault(re.sub(r"`[^`]*`", "`_`", why), 0)
            reasons[kind][re.sub(r"`[^`]*`", "`_`", why)] += 1
        elif st == "converted":
            out(f"  converted {kind} {loc} {name}")
    for kind in ("renderer", "model", "layer"):
        c = counts.get(kind, {})
        out(f"convert-entity-renderstate: {kind}s: {c.get('converted', 0)} converted, {c.get('refused', 0)} refused, "
            f"{c.get('already', 0)} already converted" + (" (dry run)" if dry else ""))
        for why, n in sorted(reasons.get(kind, {}).items(), key=lambda kv: -kv[1])[:5]:
            out(f"    {n:3d} x {why}")
    out(f"convert-entity-renderstate: {changed} file(s) {'would change' if dry else 'changed'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src")
    ap.add_argument("--context", action="append", default=[], help="read-only source tree(s) of a library the --src classes extend")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not a.src:
        ap.error("--src is required")
    status, orig, changed = run(a.src, a.dry_run, a.context)
    report(status, orig, changed, a.dry_run)
    return 0


# ------------------------------------------------------------------------------------------------ self-check
FIX = {
"entity/FooEntity.java": '''package m.entity;

import net.minecraft.world.entity.monster.Monster;

public class FooEntity extends Monster {
    public float getCharge(float pt) {
        return pt;
    }

    public int getKind() {
        return 2;
    }

    public boolean isAngry() {
        return true;
    }
}
''',
"entity/PlainEntity.java": '''package m.entity;

import net.minecraft.world.entity.Entity;

public class PlainEntity extends Entity {
}
''',
"client/FooModel.java": '''package m.client;

import net.minecraft.client.model.EntityModel;
import net.minecraft.client.model.geom.ModelPart;
import m.entity.FooEntity;

public class FooModel<T extends FooEntity> extends EntityModel<T> {
    private final ModelPart head;

    public FooModel(ModelPart root) {
        this.head = root.getChild("head");
    }

    public void setupAnim(T entity, float limbSwing, float limbSwingAmount, float ageInTicks, float netHeadYaw, float headPitch) {
        this.head.yRot = netHeadYaw * 0.017F;
        this.head.xRot = Mth.cos(limbSwing) * limbSwingAmount + ageInTicks;
    }
}
''',
"client/BarModel.java": '''package m.client;

import net.minecraft.client.model.EntityModel;
import m.entity.FooEntity;

public class BarModel<T extends FooEntity> extends FooModel<T> {
    public BarModel(ModelPart root) {
        super(root);
    }

    @Override
    public void setupAnim(T entity, float a, float b, float c, float d, float e) {
        super.setupAnim(entity, a, b, c, d, e);
        this.tail.xRot = c;
    }
}
''',
"client/BadModel.java": '''package m.client;

import net.minecraft.client.model.EntityModel;
import m.entity.FooEntity;

public class BadModel<T extends FooEntity> extends EntityModel<T> {
    public BadModel(ModelPart root) {
    }

    public void setupAnim(T entity, float a, float b, float c, float d, float e) {
        this.head.yRot = entity.getHealth();
    }
}
''',
"client/HierModel.java": '''package m.client;

import net.minecraft.client.model.HierarchicalModel;
import m.entity.FooEntity;

public class HierModel<T extends FooEntity> extends HierarchicalModel<T> {
}
''',
"client/FooLayer.java": '''package m.client;

import net.minecraft.client.renderer.entity.layers.RenderLayer;
import net.minecraft.world.entity.LivingEntity;

public class FooLayer<T extends LivingEntity, M extends EntityModel<T>> extends RenderLayer<T, M> {
    public FooLayer(RenderLayerParent<T, M> parent) {
        super(parent);
    }

    public void render(PoseStack pose, MultiBufferSource buf, int light, T entity, float a, float b, float c, float d, float yaw, float pitch) {
        super.render(pose, buf, light, entity, a, b, c, d, yaw, pitch);
    }
}
''',
"client/BusyLayer.java": '''package m.client;

import net.minecraft.client.renderer.entity.layers.RenderLayer;
import net.minecraft.world.entity.LivingEntity;

public class BusyLayer<T extends LivingEntity, M extends EntityModel<T>> extends RenderLayer<T, M> {
    public void render(PoseStack pose, MultiBufferSource buf, int light, T entity, float a, float b, float c, float d, float yaw, float pitch) {
        pose.pushPose();
    }
}
''',
"client/FooRenderer.java": '''package m.client;

import net.minecraft.client.renderer.entity.MobRenderer;
import net.minecraft.resources.ResourceLocation;
import m.entity.FooEntity;

public class FooRenderer extends MobRenderer<FooEntity, FooModel<FooEntity>> {
    private static final ResourceLocation TEX = loc("a.png");
    private static final ResourceLocation ANGRY = loc("b.png");

    public FooRenderer(Context ctx) {
        super(ctx, new FooModel<>(ctx.bakeLayer(L)), 0.5F);
        this.addLayer(new FooLayer<>(this));
    }

    protected void scale(FooEntity foo, PoseStack stack, float pt) {
        float f = foo.getCharge(pt);
        stack.scale(f, f, f);
    }

    public ResourceLocation getTextureLocation(FooEntity foo) {
        return foo.isAngry() ? ANGRY : TEX;
    }
}
''',
"client/BabyRenderer.java": '''package m.client;

import net.minecraft.client.renderer.entity.MobRenderer;
import m.entity.FooEntity;

public class BabyRenderer extends MobRenderer<FooEntity, BarModel<FooEntity>> {
    public BabyRenderer(Context ctx) {
        super(ctx, new BarModel<>(ctx.bakeLayer(L)), 0.5F);
    }

    protected float getWhiteOverlayProgress(FooEntity e, float pt) {
        return e.isBaby() ? 1.0F : 0.0F;
    }

    public ResourceLocation getTextureLocation(FooEntity e) {
        return TEX;
    }
}
''',
"client/DrawRenderer.java": '''package m.client;

import net.minecraft.client.renderer.entity.EntityRenderer;
import m.entity.PlainEntity;

public class DrawRenderer extends EntityRenderer<PlainEntity> {
    public DrawRenderer(Context ctx) {
        super(ctx);
    }

    public void render(PlainEntity e, float yaw, float pt, PoseStack stack, MultiBufferSource buf, int light) {
    }

    public ResourceLocation getTextureLocation(PlainEntity e) {
        return null;
    }
}
''',
"client/VanillaRenderer.java": '''package m.client;

import net.minecraft.client.renderer.entity.MobRenderer;
import m.entity.FooEntity;

public class VanillaRenderer extends MobRenderer<FooEntity, CreeperModel<FooEntity>> {
    public ResourceLocation getTextureLocation(FooEntity e) {
        return TEX;
    }
}
''',
"client/NullRenderer.java": '''package m.client;

import net.minecraft.client.renderer.entity.EntityRenderer;
import m.entity.PlainEntity;

public class NullRenderer extends EntityRenderer<PlainEntity> {
    public NullRenderer(Context ctx) {
        super(ctx);
    }

    public ResourceLocation getTextureLocation(PlainEntity e) {
        return null;
    }
}
''',
"client/ListyModel.java": '''package m.client;

import net.minecraft.client.model.ListModel;
import m.entity.PlainEntity;

public class ListyModel<T extends PlainEntity> extends ListModel<T> {
    private final ModelPart root;

    public ListyModel(ModelPart part) {
        this.root = part.getChild("root");
    }

    public void setupAnim(T e, float a, float b, float c, float d, float f) {
    }

    public Iterable<ModelPart> parts() {
        return ImmutableList.of(this.root);
    }
}
''',
"client/WalkerRenderer.java": '''package m.client;

import net.minecraft.client.renderer.entity.HumanoidMobRenderer;
import m.entity.FooEntity;

public class WalkerRenderer<T extends FooEntity> extends HumanoidMobRenderer<T, WalkerModel<T>> {
    private final WalkerModel<T> spare;

    public WalkerRenderer(Context ctx) {
        super(ctx, new WalkerModel<>(ctx.bakeLayer(L)), 0.5F);
        this.addLayer(new ItemInHandLayer<>(this, ctx.getItemInHandRenderer()));
    }

    protected void setupRotations(T e, PoseStack stack, float age, float yaw, float pt, float scale) {
        super.setupRotations(e, stack, age, yaw, pt, scale);
        float swim = e.getSwimAmount(pt);
        stack.mulPose(rot(age, swim, yaw));
    }

    public ResourceLocation getTextureLocation(T e) {
        return TEX;
    }
}
''',
"client/WalkerModel.java": '''package m.client;

import net.minecraft.client.model.HumanoidModel;
import m.entity.FooEntity;

public class WalkerModel<T extends FooEntity> extends HumanoidModel<T> {
    public WalkerModel(ModelPart root) {
        super(root);
    }
}
''',
"client/GuardRenderer.java": '''package m.client;

import net.minecraft.client.renderer.entity.IllagerRenderer;
import m.entity.FooEntity;

public class GuardRenderer<T extends FooEntity> extends IllagerRenderer<T> {
    public ResourceLocation getTextureLocation(T e) {
        return e.isAngry() ? ANGRY : TEX;
    }
}
''',
"client/RootedModel.java": '''package m.client;

import net.minecraft.client.model.HierarchicalModel;
import m.entity.FooEntity;

public class RootedModel<T extends FooEntity> extends HierarchicalModel<T> {
    private final ModelPart rootPart;

    public RootedModel(ModelPart root) {
        this.rootPart = root;
        this.rootPart.getChild("a");
    }

    public ModelPart root() {
        return this.rootPart;
    }

    public void setupAnim(T entity, float limbSwing, float limbSwingAmount, float ageInTicks, float netHeadYaw, float headPitch) {
        this.rootPart.y = ageInTicks;
    }
}
''',
"client/AnimModel.java": '''package m.client;

import net.minecraft.client.model.HierarchicalModel;
import m.entity.FooEntity;

public class AnimModel<T extends FooEntity> extends HierarchicalModel<T> {
    private final ModelPart root;

    public AnimModel(ModelPart root) {
        this.root = root;
    }

    public ModelPart root() {
        return this.root;
    }

    public void setupAnim(T entity, float a, float b, float c, float d, float e) {
        this.animateWalk(WALK, a, b, 3.0F, 2.0F);
    }
}
''',
"client/RideModel.java": '''package m.client;

import net.minecraft.client.model.EntityModel;
import m.entity.FooEntity;

public class RideModel<T extends FooEntity> extends EntityModel<T> {
    public RideModel(ModelPart root) {
    }

    public void setupAnim(T entity, float a, float b, float c, float d, float e) {
        this.head.xRot = this.riding ? 1.0F : 0.0F;
    }
}
''',
"client/KfModel.java": '''package m.client;

import m.entity.FooEntity;
import m.entity.Marker;

public class KfModel<T extends FooEntity & Marker> extends BaseKf<T> {
    public KfModel(ModelPart root) {
        super(root);
    }

    public void setupAnim(T entity, float limbSwing, float limbSwingAmount, float ageInTicks, float netHeadYaw, float headPitch) {
        super.setupAnim(entity, limbSwing, limbSwingAmount, ageInTicks, netHeadYaw, headPitch);
        this.after(entity, limbSwing, ageInTicks);
    }

    protected void after(T subject, float limbSwing, float ageInTicks) {
    }
}
''',
"client/BaseKf.java": '''package m.client;

public abstract class BaseKf<T extends Entity & Marker> extends HierarchicalModel<T> {
}
''',
"client/KfRenderer.java": '''package m.client;

import net.minecraft.client.renderer.entity.MobRenderer;
import m.entity.FooEntity;

public class KfRenderer extends MobRenderer<FooEntity, KfModel<FooEntity>> {
    public ResourceLocation getTextureLocation(FooEntity e) {
        return TEX;
    }
}
''',
"client/PillRenderer.java": '''package m.client;

import net.minecraft.client.renderer.entity.ZombieRenderer;
import net.minecraft.world.entity.monster.Zombie;

public class PillRenderer extends ZombieRenderer {
    public ResourceLocation getTextureLocation(Zombie z) {
        return z.isBaby() ? BABY : ADULT;
    }
}
''',
"client/GhostRenderer.java": '''package m.client;

import net.minecraft.client.renderer.entity.ZombieRenderer;
import net.minecraft.world.entity.monster.Zombie;

public class GhostRenderer extends ZombieRenderer {
    public ResourceLocation getTextureLocation(Zombie z) {
        return z instanceof Husk ? HUSK : super.getTextureLocation(z);
    }
}
''',
}


FIX_CTX = {
"BaseKf.java": '''package m.client;

import net.minecraft.client.model.EntityModel;
import net.minecraft.client.renderer.entity.state.LivingEntityRenderState;

public abstract class BaseKf<T extends LivingEntityRenderState> extends EntityModel<T> {
}
''',
}


def self_check():
    import tempfile
    bad = []
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        for rel, txt in FIX.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(txt, encoding="utf-8")
        ctx = pathlib.Path(td + "-ctx")
        ctx.mkdir()
        for rel, txt in FIX_CTX.items():
            (ctx / rel).write_text(txt, encoding="utf-8")
        status, orig, changed = run(root, context=[ctx])
        st = {name: (s, why) for (_, name), (_, s, why) in status.items()}
        for name in ("FooModel", "BarModel", "FooLayer", "FooRenderer", "BabyRenderer", "NullRenderer", "ListyModel", "WalkerRenderer", "PillRenderer", "GuardRenderer", "RootedModel", "KfModel", "KfRenderer"):
            if st.get(name, ("?",))[0] != "converted":
                bad.append(f"{name} not converted: {st.get(name)}")
        for name, frag in (("BadModel", "getHealth"), ("HierModel", "HierarchicalModel"), ("BusyLayer", "by hand"),
                           ("DrawRenderer", "render(...)"), ("VanillaRenderer", "vanilla model"), ("GhostRenderer", "instanceof"), ("AnimModel", "animateWalk"), ("RideModel", "riding")):
            s, why = st.get(name, ("?", ""))
            if s != "refused" or frag not in why:
                bad.append(f"{name} should be refused with `{frag}`: {st.get(name)}")
        rd = lambda n: (root / "client" / f"{n}.java").read_text(encoding="utf-8")
        want = {
            "FooModel": ["FooModel<T extends LivingEntityRenderState> extends EntityModel<T>", "super(root);",
                         "public void setupAnim(T renderState) {", "super.setupAnim(renderState);",
                         "float limbSwing = renderState.walkAnimationPos;", "float netHeadYaw = renderState.yRot;",
                         "float ageInTicks = renderState.ageInTicks;", "@Override\n    public void setupAnim",
                         "import net.minecraft.client.renderer.entity.state.LivingEntityRenderState;"],
            "BarModel": ["BarModel<T extends LivingEntityRenderState> extends FooModel<T>", "super.setupAnim(renderState);",
                         "float c = renderState.ageInTicks;"],
            "FooLayer": ["FooLayer<T extends LivingEntityRenderState, M extends EntityModel<? super T>> extends RenderLayer<T, M>",
                         "public void submit(PoseStack pose, SubmitNodeCollector buf, int light, T entity, float yaw, float pitch) {",
                         "super.submit(pose, buf, light, entity, yaw, pitch);", "import net.minecraft.client.renderer.SubmitNodeCollector;"],
            "FooRenderer": ["MobRenderer<FooEntity, FooRenderState, FooModel<FooRenderState>>",
                            "public static class FooRenderState extends LivingEntityRenderState {", "public float charge;", "public boolean isAngry;",
                            "protected void scale(FooRenderState renderState, PoseStack stack) {", "float f = renderState.charge;",
                            "return renderState.isAngry ? ANGRY : TEX;", "public ResourceLocation getTextureLocation(FooRenderState renderState)",
                            "public FooRenderState createRenderState() {", "return new FooRenderState();",
                            "public void extractRenderState(FooEntity entity, FooRenderState renderState, float partialTick) {",
                            "super.extractRenderState(entity, renderState, partialTick);", "renderState.charge = entity.getCharge(partialTick);",
                            "renderState.isAngry = entity.isAngry();"],
            "BabyRenderer": ["MobRenderer<FooEntity, LivingEntityRenderState, BarModel<LivingEntityRenderState>>",
                             "protected float getWhiteOverlayProgress(LivingEntityRenderState renderState) {", "renderState.isBaby ? 1.0F : 0.0F",
                             "public LivingEntityRenderState createRenderState() {"],
            "NullRenderer": ["EntityRenderer<PlainEntity, EntityRenderState>", "public EntityRenderState createRenderState() {"],
        }
        for n, frags in want.items():
            t = rd(n)
            for fr in frags:
                if fr not in t:
                    bad.append(f"{n}: missing `{fr}`")
        want["RootedModel"] = ["RootedModel<T extends LivingEntityRenderState> extends EntityModel<T>", "super(root);",
                               "this.root.getChild(\"a\");", "this.root.y = ageInTicks;",
                               "float ageInTicks = renderState.ageInTicks;", "public void setupAnim(T renderState)"]
        want["KfModel"] = ["KfModel<T extends LivingEntityRenderState> extends BaseKf<T>", "super.setupAnim(renderState);",
                           "this.after(renderState, limbSwing, ageInTicks);", "float limbSwing = renderState.walkAnimationPos;"]
        want["KfRenderer"] = ["MobRenderer<FooEntity, LivingEntityRenderState, KfModel<LivingEntityRenderState>>"]
        for n in ("RootedModel", "KfModel", "KfRenderer"):
            for fr in want[n]:
                if fr not in rd(n):
                    bad.append(f"{n}: missing `{fr}`")
        rooted = rd("RootedModel")
        if "ModelPart rootPart" in rooted or "ModelPart root()" in rooted or "this.rootPart" in rooted:
            bad.append("RootedModel: the root field/override/uses were not removed")
        if st.get("BaseKf", ("?", ""))[1] != "ported in --context":
            bad.append(f"BaseKf: the src twin of a context class was not skipped: {st.get('BaseKf')}")
        if "BabyRenderer" in want and "@Override\n    public LivingEntityRenderState createRenderState" not in rd("BabyRenderer"):
            bad.append("BabyRenderer: createRenderState lacks @Override")
        before = {p: p.read_text(encoding="utf-8") for p in root.rglob("*.java")}
        status2, _, changed2 = run(root, context=[ctx])
        if changed2:
            bad.append("not idempotent: a second run changed files")
        refused_after = {n for (_, n), (_, s, _) in status2.items() if s == "refused"}
        if refused_after != {"BadModel", "HierModel", "BusyLayer", "DrawRenderer", "VanillaRenderer", "GhostRenderer", "AnimModel", "RideModel"}:
            bad.append(f"second-run refusals differ: {sorted(refused_after)}")
        if before != {p: p.read_text(encoding="utf-8") for p in root.rglob("*.java")}:
            bad.append("second run rewrote a file")
    print("self-check:", "OK" if not bad else "FAIL\n  " + "\n  ".join(bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
