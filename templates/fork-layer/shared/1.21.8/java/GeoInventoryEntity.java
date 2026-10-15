// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
// REQUIRES: geckolib
package @MVPKG@;

import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.GuiGraphics;
import net.minecraft.client.gui.screens.inventory.InventoryScreen;
import net.minecraft.client.renderer.LightTexture;
import net.minecraft.client.renderer.entity.EntityRenderer;
import net.minecraft.client.renderer.entity.state.EntityRenderState;
import net.minecraft.world.entity.LivingEntity;
import org.joml.Quaternionf;
import org.joml.Vector3f;
import software.bernie.geckolib.constant.DataTickets;
import software.bernie.geckolib.renderer.base.GeoRenderState;
import software.bernie.geckolib.renderer.base.GeoRenderer;

/**
 * The three {@code InventoryScreen.renderEntityInInventory*} entry points, safe for a GeckoLib entity on 1.21.8
 * (CATALOG M62): vanilla builds the preview's state with the no-argument {@code createRenderState()} (GeckoLib 5.1/5.2
 * answer null) and the GUI path never sets GeckoLib's {@code PACKED_LIGHT}, so a GeckoLib preview crashes.
 *
 * <p>Any other renderer goes straight to vanilla's method of the same name. A {@link GeoRenderer} gets a preview of
 * its own here: the state from {@code createRenderState(entity, partialTick)}, {@code PACKED_LIGHT} set to full
 * bright (the light the GUI's entity renderer draws with), submitted through
 * {@link GuiGraphics#submitEntityRenderState}. Its pose is this helper's own, close to vanilla's, not identical.
 */
public final class GeoInventoryEntity {
    /** How far (degrees) the body, the head and the pitch turn at a full-strength angle input. */
    private static final float BODY_TURN = 25.0F;
    private static final float HEAD_TURN = 45.0F;
    private static final float PITCH_TURN = 20.0F;

    private GeoInventoryEntity() {
    }

    /** The entity in the box (x1, y1)-(x2, y2), looking toward the mouse. */
    public static void renderEntityInInventoryFollowsMouse(GuiGraphics graphics, int x1, int y1, int x2, int y2, int scale,
            float yOffset, float mouseX, float mouseY, LivingEntity entity) {
        if (!isGeo(entity)) {
            InventoryScreen.renderEntityInInventoryFollowsMouse(graphics, x1, y1, x2, y2, scale, yOffset, mouseX, mouseY, entity);
            return;
        }
        // the mouse's offset from the box centre, as a share of the box's half size, kept to [-1, 1]
        float halfW = Math.max((x2 - x1) / 2.0F, 1.0F);
        float halfH = Math.max((y2 - y1) / 2.0F, 1.0F);
        float lookX = clampUnit(((x1 + x2) / 2.0F - mouseX) / halfW);
        float lookY = clampUnit(((y1 + y2) / 2.0F - mouseY) / halfH);
        renderEntityInInventoryFollowsAngle(graphics, x1, y1, x2, y2, scale, yOffset, lookX, lookY, entity);
    }

    /** The entity in the box, turned sideways by {@code angleX} and tilted by {@code angleY} (each about [-1, 1]). */
    public static void renderEntityInInventoryFollowsAngle(GuiGraphics graphics, int x1, int y1, int x2, int y2, int scale,
            float yOffset, float angleX, float angleY, LivingEntity entity) {
        if (!isGeo(entity)) {
            InventoryScreen.renderEntityInInventoryFollowsAngle(graphics, x1, y1, x2, y2, scale, yOffset, angleX, angleY, entity);
            return;
        }
        float pitchRad = (float) Math.toRadians(angleY * PITCH_TURN);
        // GUI space has y pointing down: turn the model over, then tilt it toward the viewer
        Quaternionf pose = new Quaternionf().rotateZ((float) Math.PI).rotateX(pitchRad);
        Quaternionf camera = new Quaternionf().rotateX(pitchRad);

        // face the viewer, turned by the angles; put the entity's own rotations back afterwards
        float bodyRot = entity.yBodyRot, bodyRotO = entity.yBodyRotO;
        float yRot = entity.getYRot(), yRotO = entity.yRotO;
        float xRot = entity.getXRot(), xRotO = entity.xRotO;
        float headRot = entity.yHeadRot, headRotO = entity.yHeadRotO;
        try {
            float body = 180.0F + angleX * BODY_TURN;
            float head = 180.0F + angleX * HEAD_TURN;
            float pitch = -angleY * PITCH_TURN;
            entity.yBodyRot = entity.yBodyRotO = body;
            entity.setYRot(head);
            entity.yRotO = head;
            entity.setXRot(pitch);
            entity.xRotO = pitch;
            entity.yHeadRot = entity.yHeadRotO = head;

            float fit = entity.getScale() > 0.0F ? scale / entity.getScale() : scale;
            Vector3f offset = new Vector3f(0.0F, entity.getBbHeight() / 2.0F + yOffset, 0.0F);
            renderEntityInInventory(graphics, x1, y1, x2, y2, fit, offset, pose, camera, entity);
        } finally {
            entity.yBodyRot = bodyRot;
            entity.yBodyRotO = bodyRotO;
            entity.setYRot(yRot);
            entity.yRotO = yRotO;
            entity.setXRot(xRot);
            entity.xRotO = xRotO;
            entity.yHeadRot = headRot;
            entity.yHeadRotO = headRotO;
        }
    }

    /** The entity in the box with the given scale, translation, model rotation and (nullable) camera orientation. */
    public static void renderEntityInInventory(GuiGraphics graphics, int x1, int y1, int x2, int y2, float scale,
            Vector3f translation, Quaternionf pose, Quaternionf cameraOrientation, LivingEntity entity) {
        EntityRenderer<? super LivingEntity, ?> renderer = Minecraft.getInstance().getEntityRenderDispatcher().getRenderer(entity);
        if (!(renderer instanceof GeoRenderer<?, ?, ?>)) {
            InventoryScreen.renderEntityInInventory(graphics, x1, y1, x2, y2, scale, translation, pose, cameraOrientation, entity);
            return;
        }
        EntityRenderState state = renderer.createRenderState(entity, 1.0F);
        if (state == null) {
            return;
        }
        if (state instanceof GeoRenderState geo) {
            geo.addGeckolibData(DataTickets.PACKED_LIGHT, LightTexture.FULL_BRIGHT);
        }
        state.hitboxesRenderState = null;   // no debug hitboxes in a GUI preview
        graphics.submitEntityRenderState(state, scale, translation, pose, cameraOrientation, x1, y1, x2, y2);
    }

    private static boolean isGeo(LivingEntity entity) {
        return Minecraft.getInstance().getEntityRenderDispatcher().getRenderer(entity) instanceof GeoRenderer<?, ?, ?>;
    }

    private static float clampUnit(float v) {
        return v < -1.0F ? -1.0F : Math.min(v, 1.0F);
    }
}
