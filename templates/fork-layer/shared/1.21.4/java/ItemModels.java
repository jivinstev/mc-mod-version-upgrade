// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import com.mojang.logging.LogUtils;
import net.minecraft.client.Minecraft;
import net.minecraft.client.renderer.item.ItemStackRenderState;
import net.minecraft.client.resources.model.BakedModel;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.item.ItemDisplayContext;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.Level;

import org.slf4j.Logger;

import java.lang.reflect.Field;

/** 1.21.1's ItemRenderer.getModel(stack, level, entity, seed) returned the item's BakedModel (overrides applied);
 *  1.21.4 resolves an item into an ItemStackRenderState instead. This resolves one and returns its first layer's
 *  model, which is what the old call answered for an ordinary item. Null when the item has no baked model. */
public final class ItemModels {
    // before the Field lookups below: field() logs through it during class initialisation
    private static final Logger LOGGER = LogUtils.getLogger();
    private static boolean warned;
    private static final Field LAYERS = field(ItemStackRenderState.class, "layers");
    private static final Field MODEL = field(ItemStackRenderState.LayerRenderState.class, "model");

    private ItemModels() {
    }

    public static BakedModel resolve(ItemStack stack, Level level, LivingEntity entity, int seed) {
        ItemStackRenderState state = new ItemStackRenderState();
        Minecraft.getInstance().getItemModelResolver()
                .updateForTopItem(state, stack, ItemDisplayContext.NONE, false, level, entity, seed);
        if (state.isEmpty() || LAYERS == null || MODEL == null) {
            return null;
        }
        try {
            Object[] layers = (Object[]) LAYERS.get(state);
            return layers.length == 0 ? null : (BakedModel) MODEL.get(layers[0]);
        } catch (ReflectiveOperationException e) {
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
