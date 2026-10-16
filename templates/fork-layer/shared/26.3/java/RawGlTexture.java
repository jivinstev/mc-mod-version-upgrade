// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import com.mojang.blaze3d.systems.RenderSystem;
import com.mojang.renderpearl.api.GpuFormat;
import com.mojang.renderpearl.api.textures.GpuTexture;
import com.mojang.renderpearl.backend.opengl.GlStateManager;
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
 *
 * <p>26.3 copy: the wrapper carries the texture's REAL shape ({@link RenderPasses.Shape}). 26.3's {@code createRenderPass}
 * refuses an attachment without {@code USAGE_RENDER_ATTACHMENT} and strict validation (a dev client) a sampled texture
 * without {@code USAGE_TEXTURE_BINDING}, and a view takes its size from the texture, so 26.2's usage-0 1x1 RGBA8 wrapper
 * could neither be attached nor sampled there. {@link #of(int)} reads level 0's size and internal format from GL (again on
 * every call, so a texture the mod re-allocated at a new size is re-wrapped); {@link #of(int, int, int, GpuFormat)} takes
 * them from the caller. A texture the mod need not allocate by hand is better allocated as a proper GpuTexture
 * ({@link #allocate}), whose GL id ({@link #glId}) its raw GL blits can still use.
 */
// MV-REQUIRES: LegacyTargets
// MV-REQUIRES: RenderPasses
public final class RawGlTexture extends GlTexture {
    private static final Map<Integer, RawGlTexture> BY_ID = new HashMap<>();

    private final RenderPasses.Shape shape;

    private RawGlTexture(int id, RenderPasses.Shape shape) {
        super(shape.usage(), "mv_raw_" + id, shape.format(), shape.width(), shape.height(), 1, 1, id, LegacyTargets.frameBufferCache());
        this.shape = shape;
    }

    /** The mod owns the GL texture and deletes it itself: closing the wrapper must not. */
    @Override
    public void close() {
    }

    /** The wrapper of the mod's GL texture {@code glId}, its shape read from GL. */
    public static RawGlTexture of(int glId) {
        return of(glId, query(glId));
    }

    /** The wrapper of the mod's GL texture {@code glId}, which it allocated as {@code width x height} of {@code format}. */
    public static RawGlTexture of(int glId, int width, int height, GpuFormat format) {
        return of(glId, RenderPasses.shape(width, height, format));
    }

    private static RawGlTexture of(int glId, RenderPasses.Shape shape) {
        RawGlTexture t = BY_ID.get(glId);
        if (t == null || !t.shape.equals(shape)) {
            t = new RawGlTexture(glId, shape);
            BY_ID.put(glId, t);
        }
        return t;
    }

    /** The mod deleted the texture: forget the wrapper so a reused id is not bound as the old one. */
    public static void forget(int glId) {
        BY_ID.remove(glId);
    }

    /** Level 0's width, height and internal format, as GL reports them (the 2D binding is put back afterwards). */
    private static RenderPasses.Shape query(int glId) {
        int previous = GlStateManager._getInteger(0x8069);   // GL_TEXTURE_BINDING_2D
        GlStateManager._bindTexture(glId);
        try {
            int w = GlStateManager._getTexLevelParameter(0x0DE1, 0, 0x1000);    // GL_TEXTURE_2D, GL_TEXTURE_WIDTH
            int h = GlStateManager._getTexLevelParameter(0x0DE1, 0, 0x1001);    // GL_TEXTURE_HEIGHT
            int f = GlStateManager._getTexLevelParameter(0x0DE1, 0, 0x1003);    // GL_TEXTURE_INTERNAL_FORMAT
            return RenderPasses.glShape(w, h, f);
        } finally {
            GlStateManager._bindTexture(previous);
        }
    }

    /**
     * A texture for what a mod used to allocate by hand: a proper GpuTexture of {@code format} that can be sampled,
     * attached and copied to and from, closed by its owner as any other. Its GL id ({@link #glId}) still serves raw GL
     * blits.
     */
    public static GpuTexture allocate(String label, int width, int height, GpuFormat format) {
        RenderPasses.Shape s = RenderPasses.shape(width, height, format);
        return RenderSystem.getDevice().createTexture(label, s.usage(), s.format(), s.width(), s.height(), 1, 1);
    }

    /** The GL texture behind a GpuTexture of the OpenGL backend (a RawGlTexture's is the mod's own id). */
    public static int glId(GpuTexture texture) {
        if (texture instanceof GlTexture gl) {
            return gl.glId();
        }
        throw new IllegalStateException("[mv] " + texture + " is not an OpenGL texture; it has no GL id");
    }
}
