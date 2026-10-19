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
 * <p>The pose is ALREADY at the bone's pivot, its rotation applied, when {@code submitItemStackRender} runs: 5.5.2 (as
 * 5.4.5 on 1.21.11) runs the per-bone task after {@code RenderUtil.transformToBone}, whose last step is the bone's
 * {@code translateToPivotPoint} (read from the 5.5.2 jar). Only 5.3 (1.21.10) left the pose away from the pivot, which
 * a 1.21.10 override compensated for with {@code RenderUtil.translateAndRotateMatrixForBone(pose, bone)} (CATALOG
 * M53); carried on to 5.4.5+ that call applies the pivot twice and draws a held item off the hand (a dependent fork:
 * weapons about 0.85 block away, wrist chains about 2), so drop it there, and any {@code bone.translateToPivotPoint}
 * before the offsets (CATALOG M55). The base hook draws with {@code OverlayTexture.NO_OVERLAY} and the entity state's
 * outline colour.
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
