// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import net.minecraft.client.gui.Font;
import net.minecraft.client.gui.font.TextRenderable;
import net.minecraft.network.chat.Component;
import net.minecraft.util.FormattedCharSequence;
import org.joml.Matrix4f;

/**
 * 1.21's {@code Font.drawInBatch(text, x, y, color, shadow, pose, bufferSource, displayMode, backgroundColor, light)},
 * which 26.2 removed (world text is a {@code submitText} node, drawn by {@code TextFeatureRenderer}). This does what that
 * renderer does per text -- {@code prepareText} and a glyph visitor writing every glyph and effect into the buffer of
 * its render type for the display mode -- into the caller's {@link MultiBufferSource}, so a mod that batches its own
 * labels keeps its draw order. Returns the end x, as 1.21 did ({@code x + width}, plus one for a shadow).
 */
// MV-REQUIRES: MultiBufferSource
public final class Fonts {
    private Fonts() {
    }

    public static int drawInBatch(Font font, String text, float x, float y, int color, boolean shadow, Matrix4f pose,
                                  MultiBufferSource buffers, Font.DisplayMode mode, int backgroundColor, int light) {
        visit(font.prepareText(text, x, y, color, shadow, backgroundColor), pose, buffers, mode, light);
        return (int) x + font.width(text) + (shadow ? 1 : 0);
    }

    public static int drawInBatch(Font font, Component text, float x, float y, int color, boolean shadow, Matrix4f pose,
                                  MultiBufferSource buffers, Font.DisplayMode mode, int backgroundColor, int light) {
        return drawInBatch(font, text.getVisualOrderText(), x, y, color, shadow, pose, buffers, mode, backgroundColor, light);
    }

    public static int drawInBatch(Font font, FormattedCharSequence text, float x, float y, int color, boolean shadow, Matrix4f pose,
                                  MultiBufferSource buffers, Font.DisplayMode mode, int backgroundColor, int light) {
        visit(font.prepareText(text, x, y, color, shadow, false, backgroundColor), pose, buffers, mode, light);
        return (int) x + font.width(text) + (shadow ? 1 : 0);
    }

    private static void visit(Font.PreparedText prepared, Matrix4f pose, MultiBufferSource buffers, Font.DisplayMode mode, int light) {
        Matrix4f p = new Matrix4f(pose);
        prepared.visit(new Font.GlyphVisitor() {
            @Override
            public void acceptRenderable(TextRenderable renderable) {
                renderable.render(p, buffers.getBuffer(renderable.renderType(mode)), light, false);
            }
        });
    }
}
