#!/usr/bin/env python3
"""Mechanical MC 1.20.1 -> 1.21.1 codemods (catalog §G/§I).

Only the unambiguous, high-frequency renames live here. Semantic buckets
(capabilities, networking, armor materials, enchantments) are hand work.

Usage: python3 mc121_codemod.py <src-root> [<src-root> ...]
"""
import re
import sys
import pathlib

# (regex, replacement) applied to whole-file text
def rewrite_resource_location(text):
    """`new ResourceLocation(a)` -> parse(a); `new ResourceLocation(a, b)` -> fromNamespaceAndPath(a, b)."""
    out, i, needle = [], 0, "new ResourceLocation("
    while True:
        j = text.find(needle, i)
        if j < 0:
            out.append(text[i:]); return "".join(out)
        out.append(text[i:j])
        k, depth, commas, in_str = j + len(needle), 1, 0, None
        while k < len(text) and depth:
            c = text[k]
            if in_str:
                if c == "\\": k += 1
                elif c == in_str: in_str = None
            elif c in "\"'": in_str = c
            elif c in "([{": depth += 1
            elif c in ")]}": depth -= 1
            elif c == "," and depth == 1: commas += 1
            k += 1
        name = "fromNamespaceAndPath" if commas == 1 else "parse"
        out.append("ResourceLocation." + name + "(")
        i = j + len(needle)


RULES = [
    # §G #25 ResourceLocation ctor is private
    (r'new ResourceLocation\(\s*("(?:[^"\\]|\\.)*")\s*,', r'ResourceLocation.fromNamespaceAndPath(\1,'),
    # (a non-literal `new ResourceLocation(...)` is handled by rewrite_resource_location(): it must COUNT
    #  the arguments -- one is parse(x), two is fromNamespaceAndPath(a, b). A blind regex once produced
    #  `ResourceLocation.parse(expr, x)`, an overload that does not exist.)
    # §G #37 BlockPathTypes -> PathType
    (r'\bnet\.minecraft\.world\.level\.pathfinder\.BlockPathTypes\b',
     'net.minecraft.world.level.pathfinder.PathType'),
    (r'\bBlockPathTypes\b', 'PathType'),
    # §G #41 AttributeModifier.Operation renames
    (r'\bOperation\.ADDITION\b', 'Operation.ADD_VALUE'),
    (r'\bOperation\.MULTIPLY_BASE\b', 'Operation.ADD_MULTIPLIED_BASE'),
    (r'\bOperation\.MULTIPLY_TOTAL\b', 'Operation.ADD_MULTIPLIED_TOTAL'),
    # §G #52 entity renames
    (r'\.setSecondsOnFire\(', '.igniteForSeconds('),
    (r'\.onAddedToWorld\(\)', '.onAddedToLevel()'),
    # §G/§F #44 Forge hook facades
    (r'\bForgeEventFactory\b', 'EventHooks'),
    (r'\bForgeHooksClient\b', 'ClientHooks'),
    (r'\bForgeHooks\b', 'CommonHooks'),
    (r'net\.neoforged\.neoforge\.event\.ForgeEventFactory', 'net.neoforged.neoforge.event.EventHooks'),
    (r'net\.neoforged\.neoforge\.common\.ForgeHooks', 'net.neoforged.neoforge.common.CommonHooks'),
    (r'net\.neoforged\.neoforge\.client\.ForgeHooksClient', 'net.neoforged.neoforge.client.ClientHooks'),
    # §G #33 Vanishable removed
    (r'import net\.minecraft\.world\.item\.Vanishable;\n', ''),
    (r'\s*,\s*Vanishable\b', ''),
    (r'\bimplements Vanishable\b', 'implements net.minecraft.world.item.ItemLike'),
    # §B #7 EventBusSubscriber is top-level in NeoForge
    (r'import net\.neoforged\.fml\.common\.Mod\.EventBusSubscriber;',
     'import net.neoforged.fml.common.EventBusSubscriber;'),
    (r'\bMod\.EventBusSubscriber\b', 'EventBusSubscriber'),
    # §G #45 Bus.FORGE -> Bus.GAME
    (r'\bBus\.FORGE\b', 'Bus.GAME'),
    (r'\bEventBusSubscriber\.Bus\.FORGE\b', 'EventBusSubscriber.Bus.GAME'),
    # §G #46
    (r'\.defaultDurability\(', '.durability('),
    (r'BlockBehaviour\.Properties\.copy\(', 'BlockBehaviour.Properties.ofFullCopy('),
    # §I #56/#82 partial tick
    (r'Minecraft\.getInstance\(\)\.getFrameTime\(\)',
     'Minecraft.getInstance().getTimer().getGameTimeDeltaPartialTick(true)'),
    (r'Minecraft\.getInstance\(\)\.getPartialTick\(\)',
     'Minecraft.getInstance().getTimer().getGameTimeDeltaPartialTick(true)'),
    # §G #12 ForgeSpawnEggItem
    (r'\bForgeSpawnEggItem\b', 'DeferredSpawnEggItem'),
    (r'net\.neoforged\.neoforge\.common\.ForgeSpawnEggItem',
     'net.neoforged.neoforge.common.DeferredSpawnEggItem'),
    # §K #91 IEntityAdditionalSpawnData
    (r'\bIEntityAdditionalSpawnData\b', 'IEntityWithComplexSpawn'),
    (r'net\.neoforged\.neoforge\.entity\.IEntityAdditionalSpawnData',
     'net.neoforged.neoforge.entity.IEntityWithComplexSpawn'),
    # §I #57-adjacent: ForgeMod attributes
    (r'\(Attribute\)ForgeMod\.ENTITY_REACH\.get\(\)', 'Attributes.ENTITY_INTERACTION_RANGE'),
    (r'ForgeMod\.ENTITY_REACH\.get\(\)', 'Attributes.ENTITY_INTERACTION_RANGE'),
    (r'ForgeMod\.SWIM_SPEED\.get\(\)', 'Attributes.WATER_MOVEMENT_EFFICIENCY'),
    # §G #14
    (r'\bNonNullLazy\b', 'Lazy'),
]

# ForgeRegistries.X -> Registries.Y (only inside DeferredRegister.create)
DR_MAP = {
    'ITEMS': 'ITEM', 'BLOCKS': 'BLOCK', 'ENTITY_TYPES': 'ENTITY_TYPE',
    'MOB_EFFECTS': 'MOB_EFFECT', 'PARTICLE_TYPES': 'PARTICLE_TYPE',
    'SOUND_EVENTS': 'SOUND_EVENT', 'BLOCK_ENTITY_TYPES': 'BLOCK_ENTITY_TYPE',
    'ATTRIBUTES': 'ATTRIBUTE', 'POTIONS': 'POTION', 'ENCHANTMENTS': 'ENCHANTMENT',
    'RECIPE_SERIALIZERS': 'RECIPE_SERIALIZER', 'RECIPE_TYPES': 'RECIPE_TYPE',
    'MENU_TYPES': 'MENU', 'FLUIDS': 'FLUID', 'PAINTING_VARIANTS': 'PAINTING_VARIANT',
    'STRUCTURE_TYPES': 'STRUCTURE_TYPE', 'ARMOR_MATERIALS': 'ARMOR_MATERIAL',
    'DATA_SERIALIZERS': 'DATA_SERIALIZER',
}


def transform(text: str) -> str:
    for pat, rep in RULES:
        text = re.sub(pat, rep, text)
    text = rewrite_resource_location(text)

    def dr(m):
        name = m.group(1)
        return 'DeferredRegister.create(Registries.%s' % DR_MAP.get(name, name)
    text = re.sub(r'DeferredRegister\.create\(ForgeRegistries\.([A-Z_]+)', dr, text)

    # RegistryObject -> Supplier (catalog #9). Import + type usages.
    if 'RegistryObject' in text:
        text = text.replace(
            'import net.neoforged.neoforge.registries.RegistryObject;',
            'import java.util.function.Supplier;')
        text = re.sub(r'\bRegistryObject<', 'Supplier<', text)
        text = re.sub(r'\bRegistryObject\b', 'Supplier', text)
        if 'import java.util.function.Supplier;' not in text:
            text = re.sub(r'(?m)^(package [^\n]*\n)', r'\1\nimport java.util.function.Supplier;\n',
                          text, count=1)

    # Registries / BuiltInRegistries imports where we introduced them
    if 'Registries.' in text and 'import net.minecraft.core.registries.Registries;' not in text:
        text = re.sub(r'(?m)^(package [^\n]*\n)',
                      r'\1\nimport net.minecraft.core.registries.Registries;\n', text, count=1)
    return text


def main(roots):
    changed = 0
    for root in roots:
        for f in pathlib.Path(root).rglob('*.java'):
            src = f.read_text(encoding='utf-8')
            out = transform(src)
            if out != src:
                f.write_text(out, encoding='utf-8')
                changed += 1
    print('codemod: rewrote %d files' % changed)


if __name__ == '__main__':
    main(sys.argv[1:])
