// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import com.mojang.blaze3d.opengl.DirectStateAccess;
import com.mojang.blaze3d.opengl.FrameBufferCache;
import com.mojang.blaze3d.opengl.GlStateManager;
import com.mojang.blaze3d.opengl.GlTexture;
import com.mojang.blaze3d.pipeline.RenderTarget;
import com.mojang.blaze3d.systems.GpuDevice;
import com.mojang.blaze3d.systems.RenderSystem;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.Arrays;

/**
 * A 1.21.4 RenderTarget was a GL framebuffer object the mod could name ({@code target.frameBufferId}) and bind
 * ({@code target.bindWrite(viewport)}). On 1.21.5 it is a pair of GpuTextures, and the FBO that joins them is created and
 * cached by the OpenGL backend; this asks for it. Mods that blit between framebuffers by hand (a screen filter copying
 * the depth buffer) read the id here and keep their own GL calls.
 *
 * <p>26.2 copy: {@code GlTexture.getFbo} and {@code GraphicsWorkarounds} are gone. FBOs live in the OpenGL device's
 * {@code FrameBufferCache} ({@code getFbo(dsa, colorAttachments, depthAttachment)}, keyed by GL ids and mip levels, so the
 * one vanilla's render passes made for the target's views is the one returned here), and the device that owns it and
 * its {@code DirectStateAccess} is the package-private {@code GlDevice} behind {@code GpuDevice}'s private
 * {@code backend}: both are reached reflectively. Only the OpenGL backend has FBOs; on another backend this throws.
 */
public final class LegacyTargets {
    private static GpuDevice cachedFor;
    private static FrameBufferCache cache;
    private static DirectStateAccess dsa;

    private LegacyTargets() {
    }

    private static void resolve() {
        GpuDevice device = RenderSystem.getDevice();
        if (cachedFor == device && cache != null) return;
        try {
            Field f = GpuDevice.class.getDeclaredField("backend");
            f.setAccessible(true);
            Object backend = f.get(device);
            Method fbc = backend.getClass().getMethod("frameBufferCache");
            Method ds = backend.getClass().getMethod("directStateAccess");
            fbc.setAccessible(true);
            ds.setAccessible(true);
            cache = (FrameBufferCache) fbc.invoke(backend);
            dsa = (DirectStateAccess) ds.invoke(backend);
            cachedFor = device;
        } catch (ReflectiveOperationException | RuntimeException e) {
            throw new IllegalStateException("[mv] the render device is not the OpenGL one; a GL framebuffer id cannot be had", e);
        }
    }

    /** The OpenGL device's framebuffer cache (what a {@link RawGlTexture} is created against). */
    static FrameBufferCache frameBufferCache() {
        resolve();
        return cache;
    }

    /** The GL framebuffer holding the target's colour (and depth, if it has one): what frameBufferId was. */
    public static int fbo(RenderTarget target) {
        resolve();
        GlTexture color = (GlTexture) target.getColorTexture();
        GlTexture depth = target.useDepth ? (GlTexture) target.getDepthTexture() : null;
        return cache.getFbo(dsa, Arrays.asList(color), depth);
    }

    /** RenderTarget.bindWrite(setViewport) (1.21.9 removed viewWidth/viewHeight; width/height are the same size): bind the target for drawing, optionally resetting the viewport to it. */
    public static void bindWrite(RenderTarget target, boolean setViewport) {
        GlStateManager._glBindFramebuffer(36160, fbo(target));   // GL_FRAMEBUFFER
        if (setViewport) {
            GlStateManager._viewport(0, 0, target.width, target.height);
        }
    }
}
