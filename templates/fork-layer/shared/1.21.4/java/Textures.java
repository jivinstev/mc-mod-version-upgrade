// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import net.minecraft.client.renderer.texture.AbstractTexture;
import net.minecraft.client.renderer.texture.TextureManager;
import net.minecraft.resources.ResourceLocation;

import java.util.HashMap;
import java.util.Locale;
import java.util.Map;

/** 1.21.1-1.21.3's TextureManager.register(String prefix, DynamicTexture) named the texture itself
 *  (minecraft:dynamic/<prefix>_<n>, n counting per prefix) and returned the id; 1.21.4 only has
 *  register(ResourceLocation, AbstractTexture). Same naming, same counter semantics. */
public final class Textures {
    private static final Map<String, Integer> COUNTERS = new HashMap<>();

    private Textures() {
    }

    public static synchronized ResourceLocation register(TextureManager manager, String prefix, AbstractTexture texture) {
        int n = COUNTERS.merge(prefix, 1, Integer::sum);
        ResourceLocation id = ResourceLocation.withDefaultNamespace(String.format(Locale.ROOT, "dynamic/%s_%d", prefix, n));
        manager.register(id, texture);
        return id;
    }
}
