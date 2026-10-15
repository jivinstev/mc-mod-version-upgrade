// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import com.mojang.logging.LogUtils;
import net.minecraft.client.Minecraft;
import net.minecraft.client.renderer.item.ItemStackRenderState;
import net.minecraft.client.resources.model.geometry.BakedQuad;
import net.minecraft.client.resources.model.geometry.ItemQuads;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.item.ItemDisplayContext;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.Level;

import org.slf4j.Logger;

import java.lang.reflect.Field;
import java.util.List;

/** 1.21.1's ItemRenderer.getModel(stack, level, entity, seed) returned the item's BakedModel (overrides applied).
 *  1.21.4 resolves an item into an ItemStackRenderState instead, and 1.21.5 has no BakedModel at all: a layer holds the
 *  model's quads. This resolves one and returns its first layer's quads (all faces, culled or not), which is what the
 *  old model answered for an ordinary item. Null when the item has no baked geometry.
 *  26.1.2 copy: BakedQuad moved to net.minecraft.client.resources.model.geometry.
 *  26.3 copy (CATALOG §V101): {@code LayerRenderState.prepareQuadList()} is gone; a layer holds an {@code ItemQuads}
 *  (all / solid / translucent) in its private {@code quads}, and {@code all()} is the old list. */
public final class ItemModels {
    // before the Field lookups below: field() logs through it during class initialisation
    private static final Logger LOGGER = LogUtils.getLogger();
    private static boolean warned;
    private static final Field LAYERS = field(ItemStackRenderState.class, "layers");
    private static final Field QUADS = field(ItemStackRenderState.LayerRenderState.class, "quads");

    private ItemModels() {
    }

    public static List<BakedQuad> quads(ItemStack stack, Level level, LivingEntity entity, int seed) {
        ItemStackRenderState state = new ItemStackRenderState();
        Minecraft.getInstance().getItemModelResolver()
                .updateForTopItem(state, stack, ItemDisplayContext.NONE, level, entity, seed);
        if (state.isEmpty() || LAYERS == null) {
            return null;
        }
        try {
            Object[] layers = (Object[]) LAYERS.get(state);
            if (layers.length == 0 || QUADS == null) return null;
            return ((ItemQuads) QUADS.get(layers[0])).all();
        } catch (ReflectiveOperationException | ClassCastException e) {
            warnOnce("reading the item layers", e);
            return null;
        }
    }

    private static Field field(Class<?> owner, String name) {
        try {
            Field f = owner.getDeclaredField(name);
            f.setAccessible(true);
            return f;
        } catch (ReflectiveOperationException | RuntimeException e) {
            warnOnce("looking up " + owner.getSimpleName() + "." + name, e);
            return null;
        }
    }

    /** A failed reflective step makes every quads() null (the caller's item draw is skipped): say so once. */
    private static void warnOnce(String what, Exception e) {
        if (!warned) {
            warned = true;
            LOGGER.warn("ItemModels: {} failed ({}); item quads resolve to null", what, e.toString());
        }
    }
}
