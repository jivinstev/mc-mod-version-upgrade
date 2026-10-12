// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code). Helpers that stand in for
// vanilla methods removed in 1.21.2, so the authors' call sites need only a rename row.
package @MVPKG@;

import com.mojang.blaze3d.platform.NativeImage;
import net.minecraft.client.Minecraft;
import net.minecraft.client.renderer.entity.EntityRenderer;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.phys.AABB;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

public final class Mv {
    private Mv() {
    }

    /** 1.21.1's NativeImage.asByteArray(): the image encoded as PNG. 1.21.2 only writes PNGs to files. */
    public static byte[] pngBytes(NativeImage image) throws IOException {
        Path tmp = null;
        try {
            tmp = Files.createTempFile("mv-png", ".png");
            image.writeToFile(tmp);
            return Files.readAllBytes(tmp);
        } finally {
            if (tmp != null) {
                try {
                    Files.deleteIfExists(tmp);
                } catch (IOException ignored) {
                }
            }
        }
    }

    /** 1.21.1's RenderSystem.runAsFancy only forced fancy leaves for a draw; 1.21.2 removed it. */
    public static void run(Runnable draw) {
        draw.run();
    }

    /** 1.21.1's Entity.getBoundingBoxForCulling(); in 1.21.2 the (protected) renderer method answers it. */
    public static AABB cullingBox(Entity entity) {
        EntityRenderer<?, ?> renderer = Minecraft.getInstance().getEntityRenderDispatcher().getRenderer(entity);
        if (renderer != null && CULLING_BOX != null) {
            try {
                return (AABB) CULLING_BOX.invoke(renderer, entity);
            } catch (ReflectiveOperationException | RuntimeException ignored) {
            }
        }
        return entity.getBoundingBox();
    }

    private static final java.lang.reflect.Method CULLING_BOX = cullingBoxMethod();

    private static java.lang.reflect.Method cullingBoxMethod() {
        try {
            java.lang.reflect.Method m = EntityRenderer.class.getDeclaredMethod("getBoundingBoxForCulling", Entity.class);
            m.setAccessible(true);
            return m;
        } catch (ReflectiveOperationException | RuntimeException e) {
            return null;
        }
    }

    private static volatile boolean fogSuspended;

    /**
     * 1.21.1 code pushed fog start/end to MAX_VALUE for the draws in between. 1.21.2-1.21.5 held one FogParameters in
     * RenderSystem that could be swapped (see the 1.21.3 helper); from 1.21.6 the fog is a uniform block uploaded by
     * {@code FogRenderer.updateBuffer}, with nothing to swap, so this only raises a flag. A mixin on
     * {@code FogRenderer.updateBuffer} has to read {@link #isFogSuspended()} and push the distances out -- the mod's
     * own, since only it knows where it suspends fog.
     */
    public static void suspendFog() {
        fogSuspended = true;
    }

    public static void restoreFog() {
        fogSuspended = false;
    }

    public static boolean isFogSuspended() {
        return fogSuspended;
    }
}
