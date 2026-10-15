// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import com.mojang.renderpearl.api.GpuFormat;
import com.mojang.renderpearl.backend.opengl.GlTexture;

import java.util.HashMap;
import java.util.Map;

/**
 * A GL texture the mod created and manages itself (glGenTextures, its own framebuffers), seen as the GpuTexture a
 * RenderPass binds. The wrapper keeps the mod's texture alive itself: {@link #close} never marks it closed, which is
 * what {@code GlTexture} checks before deleting the GL texture (on close, or when its last view closes).
 *
 * <p>26.2 copy: {@code TextureFormat} is {@code com.mojang.blaze3d.GpuFormat} ({@code RGBA8_UNORM}) and the protected
 * constructor takes the OpenGL device's {@code FrameBufferCache} as a ninth argument (see {@link LegacyTargets}).
 */
// MV-REQUIRES: LegacyTargets
public final class RawGlTexture extends GlTexture {
    private static final Map<Integer, RawGlTexture> BY_ID = new HashMap<>();

    private RawGlTexture(int id) {
        super(0, "mv_raw_" + id, GpuFormat.RGBA8_UNORM, 1, 1, 1, 1, id, LegacyTargets.frameBufferCache());
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
