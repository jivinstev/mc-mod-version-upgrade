// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import net.minecraft.client.Minecraft;
import net.minecraft.client.renderer.item.ItemStackRenderState;
import net.minecraft.client.resources.model.BakedModel;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.item.ItemDisplayContext;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.Level;

import java.lang.reflect.Field;

/** 1.21.1's ItemRenderer.getModel(stack, level, entity, seed) returned the item's BakedModel (overrides applied);
 *  1.21.4 resolves an item into an ItemStackRenderState instead. This resolves one and returns its first layer's
 *  model, which is what the old call answered for an ordinary item. Null when the item has no baked model. */
public final class ItemModels {
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
            return null;
        }
    }

    private static Field field(Class<?> owner, String name) {
        try {
            Field f = owner.getDeclaredField(name);
            f.setAccessible(true);
            return f;
        } catch (ReflectiveOperationException | RuntimeException e) {
            return null;
        }
    }
}
