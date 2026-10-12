// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import com.mojang.blaze3d.opengl.GlTexture;
import com.mojang.blaze3d.textures.TextureFormat;

import java.util.HashMap;
import java.util.Map;

/**
 * A GL texture the mod created and manages itself (glGenTextures, its own framebuffers), seen as the GpuTexture a
 * 1.21.5 RenderPass binds. Uses NeoForge 21.5's "external" GlTexture: closing it never deletes the mod's texture.
 */
public final class RawGlTexture extends GlTexture {
    private static final Map<Integer, RawGlTexture> BY_ID = new HashMap<>();

    private RawGlTexture(int id) {
        super("mv_raw_" + id, TextureFormat.RGBA8, 1, 1, 1, id, true);
    }

    public static RawGlTexture of(int glId) {
        return BY_ID.computeIfAbsent(glId, RawGlTexture::new);
    }

    /** The mod deleted the texture: forget the wrapper so a reused id is not bound as the old one. */
    public static void forget(int glId) {
        BY_ID.remove(glId);
    }
}
