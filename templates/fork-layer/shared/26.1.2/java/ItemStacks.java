// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import com.google.common.collect.MapMaker;
import com.mojang.blaze3d.vertex.PoseStack;
import com.mojang.blaze3d.vertex.QuadInstance;
import com.mojang.blaze3d.vertex.VertexConsumer;
import net.minecraft.client.renderer.item.ItemStackRenderState;
import net.minecraft.client.resources.model.geometry.BakedQuad;
import net.minecraft.world.item.ItemStack;

import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.WeakHashMap;

/**
 * 1.21.4 renders an item from an ItemStackRenderState that the ItemModelResolver fills from the stack; the stack is
 * not on the state. A mod that decided from the STACK while rendering (an item extension, a glint) records it here
 * from ItemModelResolver.appendItemLayers (a one-line mixin in the fork) and reads it back while the state renders.
 * One state is reused frame to frame and refilled each time, so the latest stack is the right one; {@link #claim}
 * hands it out once per fill, so a multi-layer item draws the mod's extra pass once, as 1.21.1 did.
 */
public final class ItemStacks {
    private static final Map<ItemStackRenderState, ItemStack> STACKS = Collections.synchronizedMap(new WeakHashMap<>());
    /** Weak and by identity (Guava's weakKeys map compares keys with ==): a render state that is dropped is not kept. */
    private static final Map<ItemStackRenderState, Boolean> CLAIMED = new MapMaker().weakKeys().makeMap();

    private ItemStacks() {
    }

    public static void record(ItemStackRenderState state, ItemStack stack) {
        STACKS.put(state, stack);
        CLAIMED.remove(state);
    }

    public static ItemStack claim(ItemStackRenderState state) {
        if (CLAIMED.put(state, Boolean.TRUE) != null) {
            return null;
        }
        return STACKS.get(state);
    }

    /** 1.21.1's public ItemRenderer.renderModelLists(model, stack, light, overlay, pose, consumer); 1.21.4's is private
     *  static and takes the tint array the render state already resolved instead of the stack; 1.21.5 has no model to
     *  pass, only the layer's quads (the private renderQuadList). 26.1.2 has no ItemRenderer: this does what its
     *  ItemFeatureRenderer does per quad -- the layer's tint for a tinted quad (white otherwise), then
     *  {@code VertexConsumer.putBakedQuad} with the light and overlay. */
    public static void renderModelLists(List<BakedQuad> quads, int[] tints, int light, int overlay, PoseStack pose, VertexConsumer consumer) {
        if (quads == null) {
            return;
        }
        QuadInstance instance = new QuadInstance();
        instance.setLightCoords(light);
        instance.setOverlayCoords(overlay);
        PoseStack.Pose last = pose.last();
        for (BakedQuad quad : quads) {
            BakedQuad.MaterialInfo material = quad.materialInfo();
            int layer = material.tintIndex();
            instance.setColor(material.isTinted() && tints != null && layer >= 0 && layer < tints.length ? tints[layer] : -1);
            consumer.putBakedQuad(last, quad, instance);
        }
    }
}
