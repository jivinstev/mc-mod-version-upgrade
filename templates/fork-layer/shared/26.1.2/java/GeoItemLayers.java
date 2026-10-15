// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
// REQUIRES: geckolib
package @MVPKG@;

import com.geckolib.renderer.base.GeoRenderState;
import com.geckolib.renderer.layer.builtin.BlockAndItemGeoLayer;
import com.geckolib.util.RenderUtil;
import net.minecraft.client.renderer.item.ItemModelResolver;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.item.ItemDisplayContext;
import net.minecraft.world.item.ItemStack;

import java.util.List;

/**
 * GeckoLib 5.5 (26.1.x) moved a {@code BlockAndItemGeoLayer}'s item model resolution from render time to extraction
 * time (CATALOG M64). The layer is now
 * {@code new XLayer<>(EntityRendererProvider.Context, renderer)}, {@code getRelevantBones(T, O, R, float partialTick)}
 * returns {@code RenderData.item(bone, displayContext, ItemStackRenderState)} built from the entity, and the hook a
 * held-item offset overrides is {@code submitItemStackRender(PoseStack, GeoBone, ItemStackRenderState,
 * ItemDisplayContext, R, SubmitNodeCollector, int packedLight)}.
 *
 * <p>The silent part: 5.5's {@code addRenderData} is ABSTRACT on BlockAndItemGeoLayer and nothing in the base calls
 * {@code getRelevantBones}: {@code addPerBoneRender} only reads {@link BlockAndItemGeoLayer#CONTENTS} from the render
 * state. A layer whose {@code addRenderData} records its own tickets and does not publish the list compiles, loads and
 * draws no item. Call {@link #publish} at the end of every such {@code addRenderData} (GeckoLib's own
 * ItemInHandGeoLayer does the same).
 *
 * <p>The pose is still NOT moved to the bone's pivot before {@code submitItemStackRender} (as from 5.3, CATALOG M53/M55):
 * an override whose offsets were tuned on 4.x-5.2 starts with {@code RenderUtil.translateAndRotateMatrixForBone(pose,
 * bone)}. The base hook draws with {@code OverlayTexture.NO_OVERLAY} and the entity state's outline colour.
 */
public final class GeoItemLayers {
    private GeoItemLayers() {
    }

    /** One bone's item, resolved now (extraction) the way 5.5's ItemInHandGeoLayer resolves a held item. */
    public static BlockAndItemGeoLayer.RenderData item(String bone, ItemDisplayContext displayContext, ItemStack stack,
                                                      ItemModelResolver resolver, Entity holder) {
        return BlockAndItemGeoLayer.RenderData.item(bone, displayContext,
                RenderUtil.createRenderStateForItem(stack, resolver, displayContext, holder));
    }

    /** What 5.5's abstract addRenderData has to do for addPerBoneRender to draw anything. */
    public static void publish(GeoRenderState renderState, List<BlockAndItemGeoLayer.RenderData> bones) {
        if (!bones.isEmpty()) {
            renderState.addGeckolibData(BlockAndItemGeoLayer.CONTENTS, bones);
        }
    }
}
