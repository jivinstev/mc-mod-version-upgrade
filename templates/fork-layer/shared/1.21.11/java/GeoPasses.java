// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
// REQUIRES: geckolib
package @MVPKG@;

import net.minecraft.client.renderer.OrderedSubmitNodeCollector;
import net.minecraft.client.renderer.rendertype.RenderType;
import software.bernie.geckolib.renderer.base.GeoRenderState;
import software.bernie.geckolib.renderer.base.RenderPassInfo;

/**
 * GeckoLib 5.4 (1.21.11) removed GeoRenderer.buildRenderTask, 5.3's "submit the model again with this render type,
 * light, overlay and colour" that a glow or effect layer used. GeoRenderer.submitRenderTasks re-submits it with the pass's
 * own light, overlay and colour only. This is the same submission (GeckoLib's own lambda: the captured pose, then
 * BakedGeoModel.render inside RenderPassInfo.renderPosed, where the bones' frame snapshots are live) with the three values
 * the layer chooses -- e.g. {@code GeoPasses.submitModel(info, collector.order(1), RenderTypes.eyes(tex), info.packedLight(),
 * OverlayTexture.NO_OVERLAY, -1)} from {@code GeoRenderLayer.submitRenderTask(RenderPassInfo, SubmitNodeCollector)}.
 */
public final class GeoPasses {
    private GeoPasses() {
    }

    public static <R extends GeoRenderState> void submitModel(RenderPassInfo<R> info, OrderedSubmitNodeCollector collector,
                                                              RenderType renderType, int packedLight, int packedOverlay,
                                                              int renderColor) {
        collector.submitCustomGeometry(info.poseStack(), renderType, (pose, vertices) -> {
            info.poseStack().pushPose();
            info.poseStack().last().set(pose);
            info.renderPosed(() -> info.model().render(info, vertices, packedLight, packedOverlay, renderColor));
            info.poseStack().popPose();
        });
    }
}
