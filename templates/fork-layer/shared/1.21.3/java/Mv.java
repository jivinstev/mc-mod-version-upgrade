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

    private static net.minecraft.client.renderer.FogParameters savedFog;

    /** 1.21.1 code saved RenderSystem's fog start/end and pushed both to MAX_VALUE; 1.21.2 holds one FogParameters. */
    public static void suspendFog() {
        net.minecraft.client.renderer.FogParameters fog = com.mojang.blaze3d.systems.RenderSystem.getShaderFog();
        savedFog = fog;
        com.mojang.blaze3d.systems.RenderSystem.setShaderFog(new net.minecraft.client.renderer.FogParameters(
                Float.MAX_VALUE, Float.MAX_VALUE, fog.shape(), fog.red(), fog.green(), fog.blue(), fog.alpha()));
    }

    public static void restoreFog() {
        if (savedFog != null) {
            com.mojang.blaze3d.systems.RenderSystem.setShaderFog(savedFog);
            savedFog = null;
        }
    }
}
