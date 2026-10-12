// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import net.minecraft.client.Minecraft;
import net.minecraft.client.renderer.item.ItemStackRenderState;
import net.minecraft.client.renderer.block.model.BakedQuad;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.item.ItemDisplayContext;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.Level;

import java.lang.reflect.Field;
import java.util.List;

/** 1.21.1's ItemRenderer.getModel(stack, level, entity, seed) returned the item's BakedModel (overrides applied).
 *  1.21.4 resolves an item into an ItemStackRenderState instead, and 1.21.5 has no BakedModel at all: a layer holds the
 *  model's quads. This resolves one and returns its first layer's quads (all faces, culled or not), which is what the
 *  old model answered for an ordinary item. Null when the item has no baked geometry. */
public final class ItemModels {
    private static final Field LAYERS = field(ItemStackRenderState.class, "layers");

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
            return layers.length == 0 ? null : ((ItemStackRenderState.LayerRenderState) layers[0]).prepareQuadList();
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
