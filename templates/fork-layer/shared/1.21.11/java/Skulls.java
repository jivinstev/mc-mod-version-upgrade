// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import net.minecraft.client.model.object.skull.SkullModelBase;
import net.minecraft.client.model.geom.EntityModelSet;
import net.minecraft.client.renderer.blockentity.SkullBlockRenderer;
import net.minecraft.world.level.block.SkullBlock;

import java.util.LinkedHashMap;
import java.util.Map;

/** 1.21.1-1.21.3's SkullBlockRenderer.createSkullRenderers(models) built every vanilla skull model at once (and
 *  posted CreateSkullModels for modded ones); 1.21.4 builds one per type with createModel(models, type), modded
 *  types included. This rebuilds the map for the vanilla types; a modded type is created on lookup. */
public final class Skulls {
    private Skulls() {
    }

    public static Map<SkullBlock.Type, SkullModelBase> createSkullRenderers(EntityModelSet models) {
        Map<SkullBlock.Type, SkullModelBase> out = new LinkedHashMap<>();
        for (SkullBlock.Types type : SkullBlock.Types.values()) {
            SkullModelBase model = SkullBlockRenderer.createModel(models, type);
            if (model != null) {
                out.put(type, model);
            }
        }
        return out;
    }
}
