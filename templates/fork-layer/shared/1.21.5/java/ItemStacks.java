// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import com.mojang.blaze3d.vertex.PoseStack;
import com.mojang.blaze3d.vertex.VertexConsumer;
import net.minecraft.client.renderer.entity.ItemRenderer;
import net.minecraft.client.renderer.item.ItemStackRenderState;
import net.minecraft.client.renderer.block.model.BakedQuad;
import net.minecraft.world.item.ItemStack;

import java.lang.reflect.Method;
import java.util.Collections;
import java.util.List;
import java.util.IdentityHashMap;
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
    private static final Map<ItemStackRenderState, Boolean> CLAIMED = Collections.synchronizedMap(new IdentityHashMap<>());

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

    private static final Method RENDER_MODEL_LISTS = renderModelLists();

    private static Method renderModelLists() {
        try {
            Method m = ItemRenderer.class.getDeclaredMethod("renderQuadList", PoseStack.class, VertexConsumer.class, List.class,
                    int[].class, int.class, int.class);
            m.setAccessible(true);
            return m;
        } catch (ReflectiveOperationException | RuntimeException e) {
            return null;
        }
    }

    /** 1.21.1's public ItemRenderer.renderModelLists(model, stack, light, overlay, pose, consumer); 1.21.4's is private
     *  static and takes the tint array the render state already resolved instead of the stack; 1.21.5 has no model to
     *  pass, only the layer's quads (the private renderQuadList). */
    public static void renderModelLists(List<BakedQuad> quads, int[] tints, int light, int overlay, PoseStack pose, VertexConsumer consumer) {
        if (RENDER_MODEL_LISTS == null || quads == null) {
            return;
        }
        try {
            RENDER_MODEL_LISTS.invoke(null, pose, consumer, quads, tints, light, overlay);
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException("ItemRenderer.renderQuadList", e);
        }
    }
}
