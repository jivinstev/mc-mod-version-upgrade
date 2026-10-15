// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import com.mojang.blaze3d.opengl.GlTexture;
import com.mojang.blaze3d.textures.TextureFormat;

import java.util.HashMap;
import java.util.Map;

/**
 * A GL texture the mod created and manages itself (glGenTextures, its own framebuffers), seen as the GpuTexture a
 * RenderPass binds. 26.1.2 copy: NeoForge's GlTexture lost the 1.21.11 "external" flag (the constructor takes eight
 * arguments again), so this wrapper keeps the mod's texture alive itself: {@link #close} never marks it closed, which is
 * what {@code GlTexture} checks before deleting the GL texture (on close, or when its last view closes).
 */
public final class RawGlTexture extends GlTexture {
    private static final Map<Integer, RawGlTexture> BY_ID = new HashMap<>();

    private RawGlTexture(int id) {
        super(0, "mv_raw_" + id, TextureFormat.RGBA8, 1, 1, 1, 1, id);
    }

    /** The mod owns the GL texture and deletes it itself: closing the wrapper must not. */
    @Override
    public void close() {
    }

    public static RawGlTexture of(int glId) {
        return BY_ID.computeIfAbsent(glId, RawGlTexture::new);
    }

    /** The mod deleted the texture: forget the wrapper so a reused id is not bound as the old one. */
    public static void forget(int glId) {
        BY_ID.remove(glId);
    }
}
