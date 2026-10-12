// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import com.mojang.blaze3d.opengl.GlDevice;
import com.mojang.blaze3d.opengl.GlStateManager;
import com.mojang.blaze3d.opengl.GlTexture;
import com.mojang.blaze3d.pipeline.RenderTarget;
import com.mojang.blaze3d.systems.RenderSystem;

/**
 * A 1.21.4 RenderTarget was a GL framebuffer object the mod could name ({@code target.frameBufferId}) and bind
 * ({@code target.bindWrite(viewport)}). On 1.21.5 it is a pair of GpuTextures, and the FBO that joins them is created and
 * cached on the colour texture by the OpenGL backend; this asks for it. Mods that blit between framebuffers by hand
 * (a screen filter copying the depth buffer) read the id here and keep their own GL calls.
 */
public final class LegacyTargets {
    private LegacyTargets() {
    }

    /** The GL framebuffer holding the target's colour (and depth, if it has one): what frameBufferId was. */
    public static int fbo(RenderTarget target) {
        GlDevice device = (GlDevice) RenderSystem.getDevice();
        GlTexture color = (GlTexture) target.getColorTexture();
        return color.getFbo(device.directStateAccess(), target.useDepth ? target.getDepthTexture() : null);
    }

    /** RenderTarget.bindWrite(setViewport): bind the target for drawing, optionally resetting the viewport to it. */
    public static void bindWrite(RenderTarget target, boolean setViewport) {
        GlStateManager._glBindFramebuffer(36160, fbo(target));   // GL_FRAMEBUFFER
        if (setViewport) {
            GlStateManager._viewport(0, 0, target.viewWidth, target.viewHeight);
        }
    }
}
