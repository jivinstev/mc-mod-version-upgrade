// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import com.mojang.blaze3d.pipeline.RenderTarget;
import com.mojang.blaze3d.platform.NativeImage;
import net.minecraft.client.Screenshot;

import java.util.concurrent.atomic.AtomicReference;

/** 1.21.4's {@code Screenshot.takeScreenshot(RenderTarget)} returned the image; 1.21.5's hands it to a callback. The
 *  OpenGL backend reads the texture back inside the call, so the callback has run by the time it returns. */
public final class Screenshots {
    private Screenshots() {
    }

    public static NativeImage take(RenderTarget target) {
        AtomicReference<NativeImage> out = new AtomicReference<>();
        Screenshot.takeScreenshot(target, out::set);
        NativeImage image = out.get();
        if (image == null) {
            throw new IllegalStateException("the screenshot was not read back synchronously");
        }
        return image;
    }
}
