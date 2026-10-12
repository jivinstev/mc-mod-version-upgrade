#!/usr/bin/env python3
"""Measure named error FAMILIES -- candidate converters -- over a zero-model sweep: for each, the mods it appears in
and its share of the errors left. Families are matched first-wins in the order below, so an error counts once.

    python3 tools/sweep/families.py <run dir>...

The table is the October 2026 sweep's candidate list (docs/POPULARITY_SWEEP.md); edit it to measure others.
Standard library only.
"""
import collections, re, sys
from sweeplib import mod_dirs, summary, final_log

F = { # name: (target, regex over "error line || code line || symbol || location")
 "1.21 renderToBuffer 4 floats -> packed int": ("1.21.1", r"renderToBuffer"),
 "1.21 vertex chain (normal/getBuilder/begin/endVertex/vertex/uv2)": ("1.21.1", r"method (normal|getBuilder|begin|endVertex|vertex|uv2|overlayCoords|color)\(.*(VertexConsumer|Tesselator|BufferBuilder)"),
 "1.21 MobEffect.applyEffectTick returns boolean": ("1.21.1", r"applyEffectTick"),
 "1.21 PotionUtils -> PotionContents": ("1.21.1", r"PotionUtils"),
 "1.21 Supplier.getId() (registry holder typed Supplier)": ("1.21.1", r"method getId\(\).*of type Supplier|getId\(\) location: variable \w+ of type Supplier"),
 "1.21 FMLJavaModLoadingContext outside the mod constructor": ("1.21.1", r"FMLJavaModLoadingContext"),
 "1.21 Block.use(...) -> useWithoutItem/useItemOn": ("1.21.1", r"method use\(|use\(BlockState,Level,BlockPos,Player,InteractionHand,BlockHitResult\)"),
 "1.21 Enchantment subclass/EnchantmentCategory/Rarity": ("1.21.1", r"EnchantmentCategory|final Enchantment|class Rarity.*Enchantment|Enchantment.Rarity"),
 "1.21 RecordItem -> jukebox song": ("1.21.1", r"RecordItem"),
 "1.21 Forge networking leftovers": ("1.21.1", r"net\.minecraftforge\.network|NetworkEvent|PacketDistributor\.(PLAYER|ALL|TRACKING|NEAR)"),
 "1.21 Event.Result / setResult": ("1.21.1", r"class Result.*Event|Event\.Result|setResult"),
 "1.21 hasCustomHoverName": ("1.21.1", r"hasCustomHoverName"),
 "1.21 FoodProperties.Builder.meat()": ("1.21.1", r"method meat\(\)"),
 "1.21 getDefaultAttributeModifiers / Multimap": ("1.21.1", r"ImmutableMultimap<Attribute|getDefaultAttributeModifiers|Multimap<Attribute"),
 "1.21 BrewingRecipeRegistry.addRecipe": ("1.21.1", r"BrewingRecipeRegistry"),
 "1.21 FriendlyByteBuf readItem/writeItem": ("1.21.1", r"method (readItem|writeItem)\("),
 "1.21 playerWillDestroy returns BlockState": ("1.21.1", r"playerWillDestroy"),
 "1.21 SavedData computeIfAbsent(load,create,name)": ("1.21.1", r"computeIfAbsent in class|SavedData.*Factory"),
 "1.21 Recipe<Container>/serializer fromJson/streamCodec": ("1.21.1", r"Recipe<Container>|fromJson|streamCodec\(\)|method codec\(\) in RecipeSerializer|Ingredient\.fromJson|toNetwork|fromNetwork"),
 "1.21 canBreatheUnderwater final": ("1.21.1", r"canBreatheUnderwater"),
 "26.2 Block.updateShape signature": ("26.2", r"updateShape"),
 "26.2 BE save/load ValueInput/ValueOutput": ("26.2", r"ValueInput|ValueOutput"),
 "26.2 RenderSystem blend/shader-colour/texture calls removed": ("26.2", r"method (enableBlend|disableBlend|defaultBlendFunc|setShaderColor|setShaderTexture|setShader|enableDepthTest|disableDepthTest)\(.*RenderSystem"),
 "26.2 GuiGraphics.blit takes a RenderPipeline": ("26.2", r"method found for blit\(|blit\(Identifier|lossy conversion from float to int"),
 "26.2 EntityType.Builder.build(String)": ("26.2", r"ResourceKey<EntityType"),
 "26.2 Minecraft.screen field": ("26.2", r"symbol: +variable screen.*\|\| location: .*Minecraft"),
 "26.2 Item.appendHoverText signature": ("26.2", r"appendHoverText"),
 "26.2 Block.onRemove -> affectNeighborsAfterRemoval": ("26.2", r"method onRemove|onRemove\("),
 "26.2 Entity/BlockEntity renderer render-state rewrite": ("26.2", r"EntityRenderer|BlockEntityRenderer|renderToBuffer|not within bounds of type-variable|wrong number of type arguments; required [23]|RenderState|type \w+ does not take parameters|ItemRenderer"),
}
ERR = re.compile(r"^(/\S+\.java):(\d+): error: (.*)$")


def main(runs):
    res = {k: [set(), 0] for k in F}; tot = collections.Counter()
    for slug, d in mod_dirs(*runs).items():
        tgt = summary(d).get("target"); text = final_log(d)
        if not text:
            continue
        L = text.split("\n"); seen = set()
        for i, l in enumerate(L):
            m = ERR.match(l)
            if not m or (m[1], m[2]) in seen:
                continue
            seen.add((m[1], m[2])); tot[tgt] += 1
            blob = m[3] + " || " + " || ".join(x.strip() for x in L[i + 1:i + 6])
            for k, (t, rx) in F.items():
                if t == tgt and re.search(rx, blob):
                    res[k][0].add(slug); res[k][1] += 1
                    break
    print("errors left:", dict(tot))
    for k, (ms, e) in sorted(res.items(), key=lambda kv: -kv[1][1]):
        print(f"{len(ms):4d} mods {e:6d} errs ({e / max(tot[F[k][0]], 1) * 100:4.1f}% of {F[k][0]})  {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
