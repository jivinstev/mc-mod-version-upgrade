#!/usr/bin/env python3
"""Re-supply the 1.21.1 tool-tier and armour API on 26.2: Tier, Tiers, TieredItem, SwordItem, DiggerItem,
PickaxeItem, ArmorItem (+ ArmorItem.Type), ArmorMaterial (+ Layer) and ArmorMaterials.

    python3 tools/convert-gear-tiers.py --src src/main/java [--package my.mod.compat.gear] [--assets src/main/resources/assets] [--dry-run]
    python3 tools/convert-gear-tiers.py --self-check

WHY A SHIM, NOT A REWRITE (CATALOG §V35).  1.21.2 deleted Tier, TieredItem, SwordItem, DiggerItem, PickaxeItem and
ArmorItem; a tool or a piece of armour is a plain Item whose Properties carry components (tool, weapon, enchantable,
repairable, equippable, attribute modifiers).  The 1.21.1 shapes a mod builds on are subclassing and constructor
arguments, which differ per mod, so there is no call-site rewrite that is right everywhere.  The contracts, on the
other hand, are small and fixed, so this generates them ONCE, in the mod's own package, implemented on the 26.2
components exactly as 26.2's own ToolMaterial / Item.Properties.humanoidArmor do, and points the mod's imports at
them.  Every subclass, override and `new Tier() {...}` then compiles unchanged.

What each generated class does on 26.2 (read off ToolMaterial.applyToolProperties / applySwordProperties and
Item.Properties.humanoidArmor in the 26.2 sources):
  TieredItem(tier, p)        durability(tier.getUses()) (as 1.21.1 did), enchantable(value) when > 0, and REPAIRABLE as
                             a DELAYED component: 1.21.1 read the repair ingredient lazily, and building it at item
                             construction touches other mods' items before they are bound (§R3).
  SwordItem(tier, p)         + the 26.2 sword TOOL rules (cobweb, sword_instantly_mines, sword_efficient) + WEAPON(1)
  DiggerItem(tier, tag, p)   + tier.createToolProperties(tag) + WEAPON(2)      PickaxeItem = DiggerItem(mineable/pickaxe)
  createAttributes(...)      the 1.21.1 formula: damage + tier.getAttackDamageBonus(), on the vanilla modifier ids
  ArmorItem(holder, type, p) enchantable, EQUIPPABLE (slot, equip sound, asset), the armour attribute modifiers (built
                             by 26.2's own ArmorMaterial.createAttributes), REPAIRABLE delayed. Durability is NOT set:
                             the 1.21.1 constructor did not set it either -- the caller did.
  ArmorMaterial              the 1.21.1 record shape; its equipment asset is layers[0]'s id.
  ArmorMaterials / Tiers     the vanilla materials, adapted (CHAIN -> 26.2 CHAINMAIL, TURTLE -> TURTLE_SCUTE, ...).

REWRITES in the mod's tree (never inside the generated package): imports and fully-qualified uses of those names
-- both the 1.21.1 names and `net.minecraft.world.item.equipment.ArmorMaterial(s)`, which is what the class-move map
makes of 1.21.1's ArmorMaterial by simple name although the two are unrelated shapes -- go to the generated package.
A file that already uses ToolMaterial or ArmorType was ported by hand and is left alone (REFUSED, named).

INSTANCEOF (the half a shim cannot make faithful).  `x instanceof SwordItem` used to match every sword in the game;
against the shim it matches only this mod's.  So an UNBOUND test is rewritten to GearChecks.isSword(x) / isArmor /
isTiered / isDigger, which answers for vanilla items too (tags and components).  A BOUND test (`instanceof
ArmorItem a`) and any cast to these types are left as they are and reported as NARROWED: the code inside needs the
mod's own type, so widening the test would hand it a vanilla item and a ClassCastException.  In a file that casts to
a type, its unbound tests are not widened either, for the same reason.

ASSETS (--assets).  26.2 renders worn armour from an equipment asset, assets/<ns>/equipment/<id>.json, with textures
under textures/entity/equipment/humanoid[_leggings]/; 1.21.1 read textures/models/armor/<id>_layer_{1,2}.png.  For
every ArmorMaterial.Layer id the code names whose layer_1 texture exists, the json is written and the textures are
MOVED (layer_1 -> humanoid, layer_2 -> humanoid_leggings).  An armour rendered by GeckoLib has no such texture and is
skipped (GeckoLib draws it).

REFUSED and named: a DeferredRegister on Registries.ARMOR_MATERIAL (26.2 has no such registry), AxeItem/ShovelItem/
HoeItem subclasses (those classes still exist on 26.2 with a different constructor, so a shim of the same name would
shadow vanilla's stripping/pathing/tilling), and overrides of getDefaultAttributeModifiers.

Idempotent: a second run finds nothing to rewrite and regenerates identical files.  Standard library only.
"""
import argparse, json, pathlib, re, shutil, sys, tempfile

NAMES = ["Tier", "Tiers", "TieredItem", "SwordItem", "DiggerItem", "PickaxeItem", "ArmorItem", "ArmorMaterial",
         "ArmorMaterials"]
CHECKS = {"SwordItem": "isSword", "ArmorItem": "isArmor", "TieredItem": "isTiered", "DiggerItem": "isDigger",
          "PickaxeItem": "isPickaxe"}
FQN = re.compile(r"\bnet\.minecraft\.world\.item\.(?:equipment\.)?(" + "|".join(NAMES) + r")\b")
HAND_PORTED = re.compile(r"\b(ToolMaterial|ArmorType)\b")
LAYER_ID = re.compile(r'Layer\(\s*(?:[\w.]+\.)?(?:parse\(\s*"([a-z0-9_.-]+):([a-z0-9_/.-]+)"'
                      r'|fromNamespaceAndPath\(\s*"([a-z0-9_.-]+)"\s*,\s*"([a-z0-9_/.-]+)")')
GEN_TAG = "Generated by tools/convert-gear-tiers.py"

HEAD = """package {pkg};

// {tag}: the 1.21.1 contract, implemented on 26.2 components (CATALOG §V35).
"""

FILES = {
"Tier": """import java.util.List;
import net.minecraft.core.HolderGetter;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.tags.TagKey;
import net.minecraft.world.item.component.Tool;
import net.minecraft.world.item.crafting.Ingredient;
import net.minecraft.world.level.block.Block;

public interface Tier {
    int getUses();

    float getSpeed();

    float getAttackDamageBonus();

    TagKey<Block> getIncorrectBlocksForDrops();

    int getEnchantmentValue();

    Ingredient getRepairIngredient();

    default Tool createToolProperties(TagKey<Block> minesEfficiently) {
        HolderGetter<Block> blocks = BuiltInRegistries.acquireBootstrapRegistrationLookup(BuiltInRegistries.BLOCK);
        return new Tool(List.of(Tool.Rule.deniesDrops(blocks.getOrThrow(this.getIncorrectBlocksForDrops())),
                Tool.Rule.minesAndDrops(blocks.getOrThrow(minesEfficiently), this.getSpeed())), 1.0F, 1, true);
    }
}
""",
"Tiers": """import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.tags.TagKey;
import net.minecraft.world.item.ToolMaterial;
import net.minecraft.world.item.crafting.Ingredient;
import net.minecraft.world.level.block.Block;

public enum Tiers implements Tier {
    WOOD(ToolMaterial.WOOD), STONE(ToolMaterial.STONE), IRON(ToolMaterial.IRON), DIAMOND(ToolMaterial.DIAMOND),
    GOLD(ToolMaterial.GOLD), NETHERITE(ToolMaterial.NETHERITE);

    private final ToolMaterial material;

    Tiers(ToolMaterial material) {
        this.material = material;
    }

    public ToolMaterial material() {
        return this.material;
    }

    @Override
    public int getUses() {
        return this.material.durability();
    }

    @Override
    public float getSpeed() {
        return this.material.speed();
    }

    @Override
    public float getAttackDamageBonus() {
        return this.material.attackDamageBonus();
    }

    @Override
    public TagKey<Block> getIncorrectBlocksForDrops() {
        return this.material.incorrectBlocksForDrops();
    }

    @Override
    public int getEnchantmentValue() {
        return this.material.enchantmentValue();
    }

    @Override
    public Ingredient getRepairIngredient() {
        return Ingredient.of(BuiltInRegistries.acquireBootstrapRegistrationLookup(BuiltInRegistries.ITEM)
                .getOrThrow(this.material.repairItems()));
    }
}
""",
"TieredItem": """import net.minecraft.world.item.Item;

public class TieredItem extends Item {
    private final Tier tier;

    public TieredItem(Tier tier, Item.Properties properties) {
        super(GearProps.tiered(tier, properties));
        this.tier = tier;
    }

    public Tier getTier() {
        return this.tier;
    }
}
""",
"SwordItem": """import java.util.List;
import net.minecraft.core.HolderGetter;
import net.minecraft.core.HolderSet;
import net.minecraft.core.component.DataComponents;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.tags.BlockTags;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.component.ItemAttributeModifiers;
import net.minecraft.world.item.component.Tool;
import net.minecraft.world.item.component.Weapon;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;

public class SwordItem extends TieredItem {
    public SwordItem(Tier tier, Item.Properties properties) {
        super(tier, properties.component(DataComponents.TOOL, createToolProperties())
                .component(DataComponents.WEAPON, new Weapon(1)));
    }

    public static Tool createToolProperties() {
        HolderGetter<Block> blocks = BuiltInRegistries.acquireBootstrapRegistrationLookup(BuiltInRegistries.BLOCK);
        return new Tool(List.of(Tool.Rule.minesAndDrops(HolderSet.direct(Blocks.COBWEB.builtInRegistryHolder()), 15.0F),
                Tool.Rule.overrideSpeed(blocks.getOrThrow(BlockTags.SWORD_INSTANTLY_MINES), Float.MAX_VALUE),
                Tool.Rule.overrideSpeed(blocks.getOrThrow(BlockTags.SWORD_EFFICIENT), 1.5F)), 1.0F, 2, false);
    }

    public static ItemAttributeModifiers createAttributes(Tier tier, int attackDamage, float attackSpeed) {
        return GearProps.weaponAttributes(tier, attackDamage, attackSpeed);
    }

    public static ItemAttributeModifiers createAttributes(Tier tier, float attackDamage, float attackSpeed) {
        return GearProps.weaponAttributes(tier, attackDamage, attackSpeed);
    }
}
""",
"DiggerItem": """import net.minecraft.core.component.DataComponents;
import net.minecraft.tags.TagKey;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.component.ItemAttributeModifiers;
import net.minecraft.world.item.component.Weapon;
import net.minecraft.world.level.block.Block;

public class DiggerItem extends TieredItem {
    private final TagKey<Block> blocks;

    public DiggerItem(Tier tier, TagKey<Block> blocks, Item.Properties properties) {
        super(tier, properties.component(DataComponents.TOOL, tier.createToolProperties(blocks))
                .component(DataComponents.WEAPON, new Weapon(2)));
        this.blocks = blocks;
    }

    public TagKey<Block> getMineableBlocks() {
        return this.blocks;
    }

    public static ItemAttributeModifiers createAttributes(Tier tier, float attackDamage, float attackSpeed) {
        return GearProps.weaponAttributes(tier, attackDamage, attackSpeed);
    }
}
""",
"PickaxeItem": """import net.minecraft.tags.BlockTags;
import net.minecraft.world.item.Item;

public class PickaxeItem extends DiggerItem {
    public PickaxeItem(Tier tier, Item.Properties properties) {
        super(tier, BlockTags.MINEABLE_WITH_PICKAXE, properties);
    }
}
""",
"ArmorMaterial": """import java.util.List;
import java.util.Map;
import java.util.function.Supplier;
import net.minecraft.core.Holder;
import net.minecraft.resources.Identifier;
import net.minecraft.resources.ResourceKey;
import net.minecraft.sounds.SoundEvent;
import net.minecraft.world.item.crafting.Ingredient;
import net.minecraft.world.item.equipment.EquipmentAsset;
import net.minecraft.world.item.equipment.EquipmentAssets;

public record ArmorMaterial(Map<ArmorItem.Type, Integer> defense, int enchantmentValue, Holder<SoundEvent> equipSound,
        Supplier<Ingredient> repairIngredient, List<ArmorMaterial.Layer> layers, float toughness,
        float knockbackResistance) {

    public int getDefense(ArmorItem.Type type) {
        return this.defense.getOrDefault(type, 0);
    }

    /** The 26.2 equipment asset this material is drawn with: its first layer's id, as 1.21.1 named the texture. */
    public ResourceKey<EquipmentAsset> assetId() {
        return this.layers.isEmpty() ? null : ResourceKey.create(EquipmentAssets.ROOT_ID, this.layers.get(0).assetName());
    }

    public record Layer(Identifier assetName, String suffix, boolean dyeable) {
        public Layer(Identifier assetName) {
            this(assetName, "", false);
        }

        public Identifier texture(boolean innerModel) {
            return this.assetName.withPath(p -> "textures/models/armor/" + p + "_layer_" + (innerModel ? 2 : 1)
                    + this.suffix + ".png");
        }
    }
}
""",
"ArmorMaterials": """import java.util.EnumMap;
import java.util.List;
import java.util.Map;
import net.minecraft.core.Holder;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.item.crafting.Ingredient;

public final class ArmorMaterials {
    public static final Holder<ArmorMaterial> LEATHER = of(net.minecraft.world.item.equipment.ArmorMaterials.LEATHER, true);
    public static final Holder<ArmorMaterial> CHAIN = of(net.minecraft.world.item.equipment.ArmorMaterials.CHAINMAIL, false);
    public static final Holder<ArmorMaterial> IRON = of(net.minecraft.world.item.equipment.ArmorMaterials.IRON, false);
    public static final Holder<ArmorMaterial> GOLD = of(net.minecraft.world.item.equipment.ArmorMaterials.GOLD, false);
    public static final Holder<ArmorMaterial> DIAMOND = of(net.minecraft.world.item.equipment.ArmorMaterials.DIAMOND, false);
    public static final Holder<ArmorMaterial> TURTLE = of(net.minecraft.world.item.equipment.ArmorMaterials.TURTLE_SCUTE, false);
    public static final Holder<ArmorMaterial> NETHERITE = of(net.minecraft.world.item.equipment.ArmorMaterials.NETHERITE, false);
    public static final Holder<ArmorMaterial> ARMADILLO = of(net.minecraft.world.item.equipment.ArmorMaterials.ARMADILLO_SCUTE, false);

    private ArmorMaterials() {
    }

    private static Holder<ArmorMaterial> of(net.minecraft.world.item.equipment.ArmorMaterial vanilla, boolean dyeable) {
        Map<ArmorItem.Type, Integer> defense = new EnumMap<>(ArmorItem.Type.class);
        for (ArmorItem.Type type : ArmorItem.Type.values()) {
            defense.put(type, vanilla.defense().getOrDefault(type.vanilla(), 0));
        }
        return Holder.direct(new ArmorMaterial(defense, vanilla.enchantmentValue(), vanilla.equipSound(),
                () -> Ingredient.of(BuiltInRegistries.acquireBootstrapRegistrationLookup(BuiltInRegistries.ITEM)
                        .getOrThrow(vanilla.repairIngredient())),
                List.of(new ArmorMaterial.Layer(vanilla.assetId().identifier(), "", dyeable)),
                vanilla.toughness(), vanilla.knockbackResistance()));
    }
}
""",
"ArmorItem": """import net.minecraft.core.Holder;
import net.minecraft.util.StringRepresentable;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.equipment.ArmorType;

public class ArmorItem extends Item {
    protected final ArmorItem.Type type;
    protected final Holder<ArmorMaterial> material;

    public ArmorItem(Holder<ArmorMaterial> material, ArmorItem.Type type, Item.Properties properties) {
        super(GearProps.armor(material.value(), type, properties));
        this.material = material;
        this.type = type;
    }

    public ArmorItem.Type getType() {
        return this.type;
    }

    public EquipmentSlot getEquipmentSlot() {
        return this.type.getSlot();
    }

    public Holder<ArmorMaterial> getMaterial() {
        return this.material;
    }

    public int getDefense() {
        return this.material.value().getDefense(this.type);
    }

    public float getToughness() {
        return this.material.value().toughness();
    }

    public enum Type implements StringRepresentable {
        HELMET(ArmorType.HELMET), CHESTPLATE(ArmorType.CHESTPLATE), LEGGINGS(ArmorType.LEGGINGS),
        BOOTS(ArmorType.BOOTS), BODY(ArmorType.BODY);

        private final ArmorType vanilla;

        Type(ArmorType vanilla) {
            this.vanilla = vanilla;
        }

        public ArmorType vanilla() {
            return this.vanilla;
        }

        public EquipmentSlot getSlot() {
            return this.vanilla.getSlot();
        }

        public int getDurability(int baseDurability) {
            return this.vanilla.getDurability(baseDurability);
        }

        public String getName() {
            return this.vanilla.getName();
        }

        @Override
        public String getSerializedName() {
            return this.vanilla.getSerializedName();
        }
    }
}
""",
"GearProps": """import java.util.EnumMap;
import java.util.Map;
import net.minecraft.core.component.DataComponents;
import net.minecraft.resources.ResourceKey;
import net.minecraft.tags.TagKey;
import net.minecraft.world.entity.EquipmentSlotGroup;
import net.minecraft.world.entity.ai.attributes.AttributeModifier;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.component.ItemAttributeModifiers;
import net.minecraft.world.item.enchantment.Repairable;
import net.minecraft.world.item.equipment.ArmorType;
import net.minecraft.world.item.equipment.EquipmentAsset;
import net.minecraft.world.item.equipment.Equippable;

/** The Item.Properties each 1.21.1 constructor used to imply. */
final class GearProps {
    private GearProps() {
    }

    static Item.Properties tiered(Tier tier, Item.Properties properties) {
        Item.Properties p = properties.durability(tier.getUses());
        if (tier.getEnchantmentValue() > 0) {
            p = p.enchantable(tier.getEnchantmentValue());
        }
        return p.delayedComponent(DataComponents.REPAIRABLE, lookup -> new Repairable(tier.getRepairIngredient().getValues()));
    }

    static Item.Properties armor(ArmorMaterial material, ArmorItem.Type type, Item.Properties properties) {
        Item.Properties p = properties;
        if (material.enchantmentValue() > 0) {
            p = p.enchantable(material.enchantmentValue());
        }
        Equippable.Builder equippable = Equippable.builder(type.getSlot()).setEquipSound(material.equipSound());
        ResourceKey<EquipmentAsset> asset = material.assetId();
        if (asset != null) {
            equippable.setAsset(asset);
        }
        Map<ArmorType, Integer> defense = new EnumMap<>(ArmorType.class);
        material.defense().forEach((t, v) -> defense.put(t.vanilla(), v));
        net.minecraft.world.item.equipment.ArmorMaterial vanilla = new net.minecraft.world.item.equipment.ArmorMaterial(
                0, defense, material.enchantmentValue(), material.equipSound(), material.toughness(),
                material.knockbackResistance(), TagKey.create(net.minecraft.core.registries.Registries.ITEM,
                        net.minecraft.resources.Identifier.withDefaultNamespace("empty")),
                asset == null ? net.minecraft.world.item.equipment.EquipmentAssets.IRON : asset);
        return p.component(DataComponents.EQUIPPABLE, equippable.build())
                .attributes(vanilla.createAttributes(type.vanilla()))
                .delayedComponent(DataComponents.REPAIRABLE,
                        lookup -> new Repairable(material.repairIngredient().get().getValues()));
    }

    static ItemAttributeModifiers weaponAttributes(Tier tier, float attackDamage, float attackSpeed) {
        return ItemAttributeModifiers.builder()
                .add(Attributes.ATTACK_DAMAGE, new AttributeModifier(Item.BASE_ATTACK_DAMAGE_ID,
                        attackDamage + tier.getAttackDamageBonus(), AttributeModifier.Operation.ADD_VALUE),
                        EquipmentSlotGroup.MAINHAND)
                .add(Attributes.ATTACK_SPEED, new AttributeModifier(Item.BASE_ATTACK_SPEED_ID, attackSpeed,
                        AttributeModifier.Operation.ADD_VALUE), EquipmentSlotGroup.MAINHAND)
                .build();
    }
}
""",
"GearChecks": """import net.minecraft.core.component.DataComponents;
import net.minecraft.tags.ItemTags;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.equipment.Equippable;

/**
 * What a 1.21.1 `instanceof SwordItem` (etc.) meant: it matched VANILLA gear too, which the generated classes cannot.
 * Answered from tags and components, so this mod's items and everyone else's give the same answer.
 */
public final class GearChecks {
    private GearChecks() {
    }

    public static boolean isSword(Item item) {
        return item instanceof SwordItem || item.builtInRegistryHolder().is(ItemTags.SWORDS);
    }

    public static boolean isPickaxe(Item item) {
        return item instanceof PickaxeItem || item.builtInRegistryHolder().is(ItemTags.PICKAXES);
    }

    public static boolean isDigger(Item item) {
        return item instanceof DiggerItem || item.builtInRegistryHolder().is(ItemTags.PICKAXES)
                || item.builtInRegistryHolder().is(ItemTags.AXES) || item.builtInRegistryHolder().is(ItemTags.SHOVELS)
                || item.builtInRegistryHolder().is(ItemTags.HOES);
    }

    public static boolean isTiered(Item item) {
        return item instanceof TieredItem || item.components().has(DataComponents.TOOL);
    }

    public static boolean isArmor(Item item) {
        if (item instanceof ArmorItem) {
            return true;
        }
        Equippable equippable = item.components().get(DataComponents.EQUIPPABLE);
        return equippable != null && equippable.slot().getType() == EquipmentSlot.Type.HUMANOID_ARMOR;
    }
}
""",
}


def generated(pkg):
    return {n: HEAD.format(pkg=pkg, tag=GEN_TAG) + "\n" + body for n, body in FILES.items()}


def mod_package(src):
    """The @Mod class's package: the generated package goes under it, so it ships with the mod."""
    for f in sorted(src.rglob("*.java")):
        t = f.read_text(encoding="utf-8", errors="replace")
        if re.search(r"^\s*@Mod\s*\(", t, re.M):
            m = re.search(r"^package\s+([\w.]+)\s*;", t, re.M)
            if m:
                return m.group(1)
    return None


def scan_back(text, end):
    """Start index of the expression ending at `end` (identifiers, dots, balanced parens/brackets)."""
    i = end
    while i > 0:
        c = text[i - 1]
        if c.isalnum() or c in "_.$":
            i -= 1
        elif c in ")]":
            depth, j = 0, i - 1
            while j >= 0:
                if text[j] in ")]":
                    depth += 1
                elif text[j] in "([":
                    depth -= 1
                    if depth == 0:
                        break
                j -= 1
            if j < 0:
                return None
            i = j
        else:
            break
    return i if i < end else None


def rewrite_instanceof(text, casts):
    """Unbound `expr instanceof X` -> GearChecks.isX(expr); returns (text, rewritten, narrowed-notes)."""
    out, notes, n, pos = [], [], 0, 0
    for m in re.finditer(r"\binstanceof\s+(?:[\w.]+\.)?(" + "|".join(CHECKS) + r")\b(\s+[a-z_]\w*)?", text):
        name, bound = m.group(1), m.group(2)
        line = text.count("\n", 0, m.start()) + 1
        if bound or name in casts:
            notes.append((line, f"`instanceof {name}{bound or ''}` now matches only this mod's {name}s"
                          + (" (bound variable)" if bound else " (the file casts to it)")))
            continue
        k = m.start()
        while k > 0 and text[k - 1] in " \t":
            k -= 1
        start = scan_back(text, k)
        if start is None or start < pos:
            notes.append((line, f"`instanceof {name}`: receiver not recognised, left as is"))
            continue
        expr = text[start:k]
        out.append(text[pos:start])
        out.append(f"GearChecks.{CHECKS[name]}({expr})")
        pos, n = m.end(), n + 1
    out.append(text[pos:])
    return "".join(out), n, notes


def convert_file(text, pkg):
    """(new text, findings) for one mod source file; text unchanged when nothing applies."""
    notes, refused = [], []
    uses = set(FQN.findall(text))
    simple = {n for n in NAMES if re.search(r"\b" + n + r"\b", text)}
    wildcard = re.search(r"^import\s+net\.minecraft\.world\.item\.\*\s*;", text, re.M)
    if not uses and not (wildcard and simple):
        return text, 0, notes, refused
    if HAND_PORTED.search(text):
        refused.append("already uses ToolMaterial/ArmorType (ported by hand); left alone")
        return text, 0, notes, refused
    if re.search(r"Registries\.ARMOR_MATERIAL\b", text):
        refused.append("registers into Registries.ARMOR_MATERIAL, which 26.2 does not have; use Holder.direct")
    for m in re.finditer(r"\bextends\s+(AxeItem|ShovelItem|HoeItem)\b", text):
        refused.append(f"extends {m.group(1)}: still a vanilla class on 26.2 (ToolMaterial, float, float, Properties)")
    if re.search(r"\bgetDefaultAttributeModifiers\s*\(", text):
        refused.append("overrides getDefaultAttributeModifiers: 26.2 reads the ATTRIBUTE_MODIFIERS component")
    new = FQN.sub(lambda m: f"{pkg}.{m.group(1)}", text)
    if wildcard:
        have = set(re.findall(r"^import\s+" + re.escape(pkg) + r"\.(\w+)\s*;", new, re.M))
        add = [n for n in NAMES if n in simple and n not in have
               and not re.search(r"^import\s+[\w.]+\." + n + r"\s*;", new, re.M)]
        if add:
            new = new[:wildcard.end()] + "".join(f"\nimport {pkg}.{n};" for n in add) + new[wildcard.end():]
    casts = {n for n in CHECKS if re.search(r"\(\s*" + n + r"\s*\)\s*[\w(]", new)}
    new, nchk, inotes = rewrite_instanceof(new, casts)
    notes += inotes
    if nchk and not re.search(r"^import\s+" + re.escape(pkg) + r"\.GearChecks\s*;", new, re.M):
        m = re.search(r"^package\s+[\w.]+\s*;\s*\n", new, re.M)
        if m:
            new = new[:m.end()] + f"\nimport {pkg}.GearChecks;" + new[m.end():]
    return new, nchk, notes, refused


def convert_assets(assets, layer_ids, dry):
    """equipment/<id>.json + texture moves for each code-named layer id with a 1.21.1 armour texture."""
    done = []
    for ns, path in sorted(layer_ids):
        base = assets / ns / "textures/models/armor"
        l1, l2 = base / f"{path}_layer_1.png", base / f"{path}_layer_2.png"
        eq = assets / ns / "equipment" / f"{path}.json"
        if not l1.is_file() or eq.exists():
            continue
        layers = {"humanoid": [{"texture": f"{ns}:{path}"}]}
        if l2.is_file():
            layers["humanoid_leggings"] = [{"texture": f"{ns}:{path}"}]
        done.append(f"{ns}:{path}")
        if dry:
            continue
        eq.parent.mkdir(parents=True, exist_ok=True)
        eq.write_text(json.dumps({"layers": layers}, indent=2) + "\n", encoding="utf-8")
        for src, kind in ((l1, "humanoid"), (l2, "humanoid_leggings")):
            if src.is_file():
                dst = assets / ns / "textures/entity/equipment" / kind / f"{path}.png"
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
    return done


def run(src, pkg=None, assets=None, dry=False):
    src = pathlib.Path(src)
    if pkg is None:
        base = mod_package(src)
        if base is None:
            if not any(convert_file(f.read_text(encoding="utf-8"), "x.y")[0] != f.read_text(encoding="utf-8") for f in src.rglob("*.java")):
                print("convert-gear-tiers: nothing to do")
                return 0
            print("convert-gear-tiers: no @Mod class found; pass --package")
            return 2
        pkg = base + ".compat.gear"
    gen_dir = src / pkg.replace(".", "/")
    changed, checks, all_notes, all_refused, layer_ids = [], 0, [], [], set()
    for f in sorted(src.rglob("*.java")):
        if f.parent == gen_dir:
            continue
        t = f.read_text(encoding="utf-8")
        new, n, notes, refused = convert_file(t, pkg)
        rel = f.relative_to(src)
        all_notes += [f"  NARROWED {rel}:{ln}: {msg}" for ln, msg in notes]
        all_refused += [f"  REFUSED {rel}: {msg}" for msg in refused]
        for m in LAYER_ID.finditer(new):
            layer_ids.add((m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4)))
        if new != t:
            checks += n
            changed.append(str(rel))
            if not dry:
                f.write_text(new, encoding="utf-8")
    used = bool(changed) or gen_dir.is_dir()
    if used and not dry:
        gen_dir.mkdir(parents=True, exist_ok=True)
        for name, body in generated(pkg).items():
            (gen_dir / f"{name}.java").write_text(body, encoding="utf-8")
    moved = convert_assets(pathlib.Path(assets), layer_ids, dry) if assets and used else []
    print(f"convert-gear-tiers: {len(changed)} file(s) changed, {checks} instanceof test(s) widened, "
          f"{len(moved)} armour asset(s) converted" + (f"; package {pkg}" if used else "; nothing to do"))
    for line in all_refused + all_notes:
        print(line)
    for a in moved:
        print(f"  asset {a}: equipment json written, textures moved")
    return 1 if all_refused else 0


FIXTURE = {
"com/example/gear/GearMod.java": """package com.example.gear;

@Mod("gear")
public class GearMod {
}
""",
"com/example/gear/Swords.java": """package com.example.gear;

import net.minecraft.world.item.Item;
import net.minecraft.world.item.SwordItem;
import net.minecraft.world.item.Tier;
import net.minecraft.world.item.Tiers;

public class Swords extends SwordItem {
    public Swords(Tier tier, Item.Properties p) {
        super(Tiers.IRON, p.attributes(SwordItem.createAttributes(tier, 3, -2.4F)));
    }

    static boolean a(Item i) {
        return i instanceof SwordItem;
    }

    static boolean b(Item i) {
        return !(stack(i).getItem() instanceof net.minecraft.world.item.TieredItem);
    }

    static net.minecraft.world.item.ItemStack stack(Item i) {
        return null;
    }
}
""",
"com/example/gear/Armour.java": """package com.example.gear;

import java.util.List;
import net.minecraft.core.Holder;
import net.minecraft.world.item.*;
import net.minecraft.world.item.equipment.ArmorMaterial;

public class Armour extends ArmorItem {
    public Armour(Type type, Item.Properties p) {
        super(Holder.direct(new ArmorMaterial(null, 1, null, null,
                List.of(new ArmorMaterial.Layer(Identifier.parse("gear:steel"))), 0, 0)), type, p);
    }

    static int c(Item i) {
        return i instanceof ArmorItem a ? a.getDefense() : (i instanceof ArmorItem ? 1 : 0);
    }
}
""",
"com/example/gear/Hand.java": """package com.example.gear;

import net.minecraft.world.item.ToolMaterial;
import net.minecraft.world.item.Tier;
""",
}


def self_check():
    ok = True

    def chk(label, cond):
        nonlocal ok
        if not cond:
            print("FAIL:", label)
            ok = False

    with tempfile.TemporaryDirectory() as t:
        root = pathlib.Path(t)
        src, assets = root / "java", root / "assets"
        for rel, body in FIXTURE.items():
            (src / rel).parent.mkdir(parents=True, exist_ok=True)
            (src / rel).write_text(body, encoding="utf-8")
        arm = assets / "gear/textures/models/armor"
        arm.mkdir(parents=True)
        (arm / "steel_layer_1.png").write_bytes(b"1")
        (arm / "steel_layer_2.png").write_bytes(b"2")
        rc = run(src, assets=assets)
        pkg = "com.example.gear.compat.gear"
        gen = src / "com/example/gear/compat/gear"
        sw = (src / "com/example/gear/Swords.java").read_text(encoding="utf-8")
        ar = (src / "com/example/gear/Armour.java").read_text(encoding="utf-8")
        chk("refused the hand-ported file -> exit 1", rc == 1)
        chk("hand-ported file untouched", (src / "com/example/gear/Hand.java").read_text(encoding="utf-8") == FIXTURE["com/example/gear/Hand.java"])
        chk("generated package", sorted(p.stem for p in gen.glob("*.java")) == sorted(FILES))
        chk("imports retargeted", f"import {pkg}.SwordItem;" in sw and f"import {pkg}.Tiers;" in sw)
        chk("unbound instanceof widened", "return GearChecks.isSword(i);" in sw)
        chk("receiver with a call widened", f"!(GearChecks.isTiered(stack(i).getItem()))" in sw)
        chk("GearChecks imported", f"import {pkg}.GearChecks;" in sw)
        chk("equipment.ArmorMaterial (the move map's mistake) retargeted", f"import {pkg}.ArmorMaterial;" in ar)
        chk("wildcard: explicit compat import added", f"import {pkg}.ArmorItem;" in ar)
        chk("bound instanceof left (narrowed)", "i instanceof ArmorItem a" in ar)
        chk("unbound beside a bound one widened", "GearChecks.isArmor(i)" in ar)
        chk("equipment json", json.loads((assets / "gear/equipment/steel.json").read_text(encoding="utf-8"))["layers"]["humanoid"][0]["texture"] == "gear:steel")
        chk("textures moved", (assets / "gear/textures/entity/equipment/humanoid_leggings/steel.png").read_bytes() == b"2"
            and not (arm / "steel_layer_1.png").exists())
        snap = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        run(src, assets=assets)
        chk("idempotent", snap == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()})
        chk("generated code names only 26.2 vanilla types", not any(re.search(r"net\.minecraft\.world\.item\.(" + "|".join(NAMES) + r")\b", b) for b in FILES.values()))
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src")
    ap.add_argument("--package", help="where the generated classes go (default: <the @Mod class's package>.compat.gear)")
    ap.add_argument("--assets", help="src/main/resources/assets: convert 1.21.1 armour textures to equipment assets")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not a.src:
        ap.error("--src is required")
    return run(a.src, a.package, a.assets, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
