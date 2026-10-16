// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import com.mojang.renderpearl.api.GpuFormat;
import com.mojang.renderpearl.api.textures.GpuTexture;

import java.util.List;

/**
 * The rules 26.3's render passes impose on the fork-layer helpers, kept free of any client class so they can be checked
 * without a client (CATALOG §V101).
 *
 * <ul>
 * <li><b>One open pass.</b> The device has ONE {@code FrontendCommandEncoder}; while a pass is open it refuses
 * {@code createRenderPass} and every buffer / texture write ("Close the existing render pass..."). NeoForge 26.3 posts
 * every {@code RenderLevelStageEvent} stage inside the main render pass except {@link #PASS_FREE_STAGES}; the in-pass
 * stages hand that pass out ({@code event.getRenderPass()}). So a helper that opens its own pass may only run from a
 * pass-free stage (or outside the level), and a draw from an in-pass stage goes INTO the event's pass
 * ({@code LegacyGl.draw(mesh, event.getRenderPass())}, {@code MultiBufferSource.BufferSource.endBatch(pass)}).
 * {@link #requireNoOpenPass} says so instead of the encoder's message.</li>
 * <li><b>Real texture shapes.</b> {@code createRenderPass} requires {@code USAGE_RENDER_ATTACHMENT} on every attachment
 * and strict validation (a dev client) {@code USAGE_TEXTURE_BINDING} on every sampled texture, and views take their size
 * from the texture: a wrapper of a mod-made GL texture ({@link RawGlTexture}) carries its real size, format and usage
 * ({@link #shape}, {@link #glShape}).</li>
 * </ul>
 */
public final class RenderPasses {
    /** The RenderLevelStageEvent stages NeoForge 26.3 posts with NO render pass open (the others expose theirs). */
    public static final List<String> PASS_FREE_STAGES = List.of("AfterSky", "AfterLevel");

    /** The RenderLevelStageEvent stages NeoForge 26.3 posts inside the main render pass ({@code getRenderPass()}). */
    public static final List<String> IN_PASS_STAGES = List.of("AfterOpaqueBlocks", "AfterOpaqueFeatures",
            "AfterTranslucentBlocks", "AfterTranslucentFeatures", "AfterTranslucentParticles", "AfterWeather", "OitTranslucent");

    private RenderPasses() {
    }

    /** What a helper that opens a pass of its own says when one is already open. */
    public static String openPassMessage(String what) {
        return "[mv] " + what + " opens a render pass of its own, but one is already open: NeoForge 26.3 posts every "
                + "RenderLevelStageEvent stage except " + String.join(" and ", PASS_FREE_STAGES) + " inside the main render "
                + "pass (" + String.join(", ", IN_PASS_STAGES) + "). From those stages draw into the event's pass -- "
                + "LegacyGl.draw(mesh, event.getRenderPass()) or MultiBufferSource.BufferSource.endBatch(event.getRenderPass()) "
                + "-- or draw from " + String.join(" / ", PASS_FREE_STAGES) + " or outside the level.";
    }

    /** Throws {@link #openPassMessage} when {@code passOpen}: called before anything is uploaded or opened. */
    public static void requireNoOpenPass(boolean passOpen, String what) {
        if (passOpen) {
            throw new IllegalStateException(openPassMessage(what));
        }
    }

    /** The element index a transient allocation at {@code offset} starts at (an index buffer has no offset of its own). */
    public static int firstElement(long offset, int elementBytes) {
        if (elementBytes <= 0 || offset % elementBytes != 0) {
            throw new IllegalStateException("[mv] offset " + offset + " is not a multiple of the element size " + elementBytes);
        }
        return Math.toIntExact(offset / elementBytes);
    }

    /** A texture's shape as its GpuTexture declares it. */
    public record Shape(int width, int height, GpuFormat format, int usage) {
        public boolean attachable() {
            return (usage & GpuTexture.USAGE_RENDER_ATTACHMENT) != 0;
        }

        public boolean bindable() {
            return (usage & GpuTexture.USAGE_TEXTURE_BINDING) != 0;
        }
    }

    /** What a mod-made GL texture of a known format can be used as: sampled, attached, copied to and from. */
    public static final int RAW_USAGE = GpuTexture.USAGE_TEXTURE_BINDING | GpuTexture.USAGE_RENDER_ATTACHMENT
            | GpuTexture.USAGE_COPY_SRC | GpuTexture.USAGE_COPY_DST;

    /** The shape of a texture the mod allocated as {@code width x height} of {@code format}. */
    public static Shape shape(int width, int height, GpuFormat format) {
        if (width <= 0 || height <= 0 || format == null) {
            throw new IllegalArgumentException("[mv] a texture shape needs a size and a format: " + width + "x" + height + " " + format);
        }
        return new Shape(width, height, format, RAW_USAGE);
    }

    /**
     * The shape of a GL texture from what GL reports for it (level 0's width, height and internal format). A format this
     * does not know is wrapped as RGBA8 that can only be sampled (it cannot be vouched for as an attachment); an
     * unallocated texture (0 x 0) as 1 x 1.
     */
    public static Shape glShape(int width, int height, int glInternalFormat) {
        GpuFormat f = formatOfGl(glInternalFormat);
        int w = Math.max(1, width);
        int h = Math.max(1, height);
        return f == null ? new Shape(w, h, GpuFormat.RGBA8_UNORM, GpuTexture.USAGE_TEXTURE_BINDING) : new Shape(w, h, f, RAW_USAGE);
    }

    /** A GL internal format as 26.3's GpuFormat; null when it has none. The unsized GL_RGBA / GL_DEPTH_COMPONENT are
     *  what drivers allocate them as (RGBA8, a 24-bit depth reported as D24 with no stencil is not a GpuFormat). */
    public static GpuFormat formatOfGl(int glInternalFormat) {
        return switch (glInternalFormat) {
            case 0x8058, 0x1908 -> GpuFormat.RGBA8_UNORM;      // GL_RGBA8, GL_RGBA
            case 0x8051, 0x1907 -> GpuFormat.RGB8_UNORM;       // GL_RGB8, GL_RGB
            case 0x8229, 0x1903 -> GpuFormat.R8_UNORM;         // GL_R8, GL_RED
            case 0x822B -> GpuFormat.RG8_UNORM;                // GL_RG8
            case 0x881A -> GpuFormat.RGBA16_FLOAT;             // GL_RGBA16F
            case 0x8814 -> GpuFormat.RGBA32_FLOAT;             // GL_RGBA32F
            case 0x822D -> GpuFormat.R16_FLOAT;                // GL_R16F
            case 0x822E -> GpuFormat.R32_FLOAT;                // GL_R32F
            case 0x8C3A -> GpuFormat.RG11B10_FLOAT;            // GL_R11F_G11F_B10F
            case 0x8059 -> GpuFormat.RGB10A2_UNORM;            // GL_RGB10_A2
            case 0x8CAC -> GpuFormat.D32_FLOAT;                // GL_DEPTH_COMPONENT32F
            case 0x8CAD -> GpuFormat.D32_FLOAT_S8_UINT;        // GL_DEPTH32F_STENCIL8
            case 0x88F0 -> GpuFormat.D24_UNORM_S8_UINT;        // GL_DEPTH24_STENCIL8
            case 0x81A5 -> GpuFormat.D16_UNORM;                // GL_DEPTH_COMPONENT16
            default -> null;                                   // GL_DEPTH_COMPONENT24 / unsized depth: no D24 GpuFormat
        };
    }
}
