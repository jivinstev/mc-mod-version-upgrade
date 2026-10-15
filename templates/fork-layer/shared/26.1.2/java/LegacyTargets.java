// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import com.mojang.blaze3d.GraphicsWorkarounds;
import com.mojang.blaze3d.opengl.DirectStateAccess;
import com.mojang.blaze3d.opengl.GlStateManager;
import com.mojang.blaze3d.opengl.GlTexture;
import com.mojang.blaze3d.pipeline.RenderTarget;
import com.mojang.blaze3d.systems.GpuDevice;
import com.mojang.blaze3d.systems.RenderSystem;
import org.lwjgl.opengl.GL;

import java.util.HashSet;

/**
 * A 1.21.4 RenderTarget was a GL framebuffer object the mod could name ({@code target.frameBufferId}) and bind
 * ({@code target.bindWrite(viewport)}). On 1.21.5 it is a pair of GpuTextures, and the FBO that joins them is created and
 * cached on the colour texture by the OpenGL backend; this asks for it. Mods that blit between framebuffers by hand
 * (a screen filter copying the depth buffer) read the id here and keep their own GL calls.
 *
 * <p>26.1.2 copy: {@code GlDevice} is package-private, so its {@code DirectStateAccess} is out of reach; this keeps one of
 * its own from the public {@code DirectStateAccess.create} (the same choice of core or emulated DSA vanilla makes) and
 * hands it to {@code GlTexture.getFbo}, which still caches the FBO on the colour texture.
 */
public final class LegacyTargets {
    private static DirectStateAccess dsa;
    private static GpuDevice dsaDevice;

    private LegacyTargets() {
    }

    private static DirectStateAccess dsa() {
        GpuDevice device = RenderSystem.getDevice();
        if (dsa == null || dsaDevice != device) {
            dsa = DirectStateAccess.create(GL.getCapabilities(), new HashSet<>(), GraphicsWorkarounds.get(device));
            dsaDevice = device;
        }
        return dsa;
    }

    /** The GL framebuffer holding the target's colour (and depth, if it has one): what frameBufferId was. */
    public static int fbo(RenderTarget target) {
        GlTexture color = (GlTexture) target.getColorTexture();
        return color.getFbo(dsa(), target.useDepth ? target.getDepthTexture() : null);
    }

    /** RenderTarget.bindWrite(setViewport) (1.21.9 removed viewWidth/viewHeight; width/height are the same size): bind the target for drawing, optionally resetting the viewport to it. */
    public static void bindWrite(RenderTarget target, boolean setViewport) {
        GlStateManager._glBindFramebuffer(36160, fbo(target));   // GL_FRAMEBUFFER
        if (setViewport) {
            GlStateManager._viewport(0, 0, target.width, target.height);
        }
    }
}
