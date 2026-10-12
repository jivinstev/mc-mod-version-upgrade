// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import com.mojang.blaze3d.buffers.GpuBuffer;
import com.mojang.blaze3d.opengl.GlProgram;
import com.mojang.blaze3d.opengl.GlRenderPipeline;
import com.mojang.blaze3d.pipeline.BlendFunction;
import com.mojang.blaze3d.pipeline.CompiledRenderPipeline;
import com.mojang.blaze3d.pipeline.RenderPipeline;
import com.mojang.blaze3d.pipeline.RenderTarget;
import com.mojang.blaze3d.platform.DepthTestFunction;
import com.mojang.blaze3d.platform.DestFactor;
import com.mojang.blaze3d.platform.SourceFactor;
import com.mojang.blaze3d.shaders.UniformType;
import com.mojang.blaze3d.systems.RenderPass;
import com.mojang.blaze3d.systems.RenderSystem;
import com.mojang.blaze3d.textures.GpuTexture;
import com.mojang.blaze3d.vertex.MeshData;
import com.mojang.blaze3d.vertex.VertexFormat;
import com.mojang.logging.LogUtils;
import net.minecraft.client.Minecraft;
import net.minecraft.client.renderer.texture.AbstractTexture;
import net.minecraft.resources.ResourceLocation;
import org.slf4j.Logger;

import java.util.HashMap;
import java.util.Map;
import java.util.OptionalDouble;
import java.util.OptionalInt;
import java.util.Set;

/**
 * Immediate-mode GL state and drawing as 1.21.1-1.21.4 code wrote it, on 1.21.5's RenderPipeline/RenderPass.
 *
 * <p>1.21.5 removed RenderSystem.enableBlend/blendFunc/depthMask/enable(Disable)DepthTest/Cull/colorMask/setShader and
 * BufferUploader: every draw now carries a pipeline whose blend, depth, cull and write masks are fixed. This records
 * the state those calls used to set, and {@link #draw} folds it into a pipeline (cached per shader and state) and
 * draws through a RenderPass exactly as vanilla's RenderType.draw does -- plus the shader's own uniform values and
 * samplers, which the RenderPass has to be handed by name.
 *
 * <p>Not expressible on 1.21.5 and logged once: a blend equation other than ADD.
 */
public final class LegacyGl {
    private static final Logger LOGGER = LogUtils.getLogger();
    static final Set<String> BUILT_IN = GlProgram.BUILT_IN_UNIFORMS;

    private static boolean blend;
    private static BlendFunction blendFunction = BlendFunction.TRANSLUCENT;
    private static boolean depthTest = true;
    private static DepthTestFunction depthFunction = DepthTestFunction.LEQUAL_DEPTH_TEST;
    private static boolean depthWrite = true;
    private static boolean writeColor = true;
    private static boolean writeAlpha = true;
    private static boolean cull = true;
    private static Object shader;
    private static boolean warnedEquation;

    private LegacyGl() {}

    // ---- the RenderSystem calls 1.21.5 removed ----------------------------------------------------------------
    public static void enableBlend() { blend = true; }
    public static void disableBlend() { blend = false; }
    public static void defaultBlendFunc() {
        blendFunction = new BlendFunction(SourceFactor.SRC_ALPHA, DestFactor.ONE_MINUS_SRC_ALPHA, SourceFactor.ONE, DestFactor.ZERO);
    }
    public static void blendFunc(SourceFactor src, DestFactor dst) { blendFunction = new BlendFunction(src, dst); }
    public static void blendFuncSeparate(SourceFactor src, DestFactor dst, SourceFactor srcA, DestFactor dstA) {
        blendFunction = new BlendFunction(src, dst, srcA, dstA);
    }
    public static void blendEquation(int equation) {
        if (equation != 32774 && !warnedEquation) {   // GL_FUNC_ADD
            warnedEquation = true;
            LOGGER.warn("[mv] blend equation {} has no 1.21.5 pipeline equivalent; ADD is used", equation);
        }
    }
    public static void enableDepthTest() { depthTest = true; }
    public static void disableDepthTest() { depthTest = false; }
    public static void depthFunc(int glFunc) {
        depthFunction = switch (glFunc) {
            case 514 -> DepthTestFunction.EQUAL_DEPTH_TEST;
            case 513 -> DepthTestFunction.LESS_DEPTH_TEST;
            case 516 -> DepthTestFunction.GREATER_DEPTH_TEST;
            case 519 -> DepthTestFunction.NO_DEPTH_TEST;   // GL_ALWAYS
            default -> DepthTestFunction.LEQUAL_DEPTH_TEST;
        };
    }
    public static void depthMask(boolean write) { depthWrite = write; }
    public static void colorMask(boolean r, boolean g, boolean b, boolean a) { writeColor = r || g || b; writeAlpha = a; }
    public static void enableCull() { cull = true; }
    public static void disableCull() { cull = false; }
    public static void setShader(ShaderInstance s) { shader = s; }
    public static void setShader(Vanilla s) { shader = s; }
    public static void recordRenderCall(Runnable r) {
        if (RenderSystem.isOnRenderThread()) r.run(); else Minecraft.getInstance().execute(r);
    }
    /** 1.21.5 binds a GpuTexture; 1.21.1 code named the texture by its location... */
    public static void setShaderTexture(int slot, ResourceLocation texture) {
        AbstractTexture t = Minecraft.getInstance().getTextureManager().getTexture(texture);
        RenderSystem.setShaderTexture(slot, t.getTexture());
    }

    /** ...or by the GL id of a texture it made itself (see {@link RawGlTexture}). */
    public static void setShaderTexture(int slot, int glId) {
        RenderSystem.setShaderTexture(slot, glId <= 0 ? null : RawGlTexture.of(glId));
    }

    public static void setShaderTexture(int slot, GpuTexture texture) {
        RenderSystem.setShaderTexture(slot, texture);
    }

    /** BufferUploader.drawWithShader: the current shader and the state set above, into the main render target. */
    public static void drawWithShader(MeshData mesh) {
        draw(mesh, shader, current(), Minecraft.getInstance().getMainRenderTarget());
    }

    // ---- state as a value -------------------------------------------------------------------------------------
    /** One draw's fixed-function state: what a 1.21.5 pipeline bakes in. Part of the pipeline cache key. */
    public record Key(BlendFunction blend, DepthTestFunction depth, boolean depthWrite, boolean writeColor,
                      boolean writeAlpha, boolean cull, float biasScale, float biasConstant) {
        RenderPipeline.Builder apply(RenderPipeline.Builder b) {
            if (blend != null) b.withBlend(blend); else b.withoutBlend();
            b.withDepthTestFunction(depth).withDepthWrite(depthWrite).withColorWrite(writeColor, writeAlpha).withCull(cull);
            if (biasScale != 0 || biasConstant != 0) b.withDepthBias(biasScale, biasConstant);
            return b;
        }

        Key withBlendIfUnset(BlendFunction b) {
            return blend != null || b == null ? this : new Key(b, depth, depthWrite, writeColor, writeAlpha, cull, biasScale, biasConstant);
        }
    }

    static Key current() {
        return new Key(blend ? blendFunction : null, depthTest ? depthFunction : DepthTestFunction.NO_DEPTH_TEST,
                depthWrite, writeColor, writeAlpha, cull, 0, 0);
    }

    // ---- drawing ----------------------------------------------------------------------------------------------
    static void draw(MeshData mesh, Object shaderObj, Key key, RenderTarget target) {
        if (mesh == null) return;
        try (MeshData m = mesh) {
            if (shaderObj == null) {
                LOGGER.warn("[mv] a draw with no shader set was skipped");
                return;
            }
            VertexFormat format = m.drawState().format();
            VertexFormat.Mode mode = m.drawState().mode();
            RenderPipeline pipeline;
            CompiledRenderPipeline compiled;
            ShaderInstance own = shaderObj instanceof ShaderInstance s ? s : null;
            if (own != null) {
                Key k = key.withBlendIfUnset(own.blend());
                pipeline = own.pipeline(format, mode, k);
                compiled = RenderSystem.getDevice().precompilePipeline(pipeline, own::sourceFor);
            } else {
                pipeline = ((Vanilla) shaderObj).pipeline(format, mode, key);
                compiled = RenderSystem.getDevice().precompilePipeline(pipeline);
            }
            if (!compiled.isValid()) return;   // the device has logged why
            GpuBuffer vertices = format.uploadImmediateVertexBuffer(m.vertexBuffer());
            GpuBuffer indices;
            VertexFormat.IndexType indexType;
            if (m.indexBuffer() == null) {
                RenderSystem.AutoStorageIndexBuffer seq = RenderSystem.getSequentialBuffer(mode);
                indices = seq.getBuffer(m.drawState().indexCount());
                indexType = seq.type();
            } else {
                indices = format.uploadImmediateIndexBuffer(m.indexBuffer());
                indexType = m.drawState().indexType();
            }
            try (RenderPass pass = RenderSystem.getDevice().createCommandEncoder().createRenderPass(
                    target.getColorTexture(), OptionalInt.empty(),
                    target.useDepth ? target.getDepthTexture() : null, OptionalDouble.empty())) {
                pass.setPipeline(pipeline);
                pass.setVertexBuffer(0, vertices);
                if (RenderSystem.SCISSOR_STATE.isEnabled()) pass.enableScissor(RenderSystem.SCISSOR_STATE);
                for (int i = 0; i < 12; i++) {
                    GpuTexture t = RenderSystem.getShaderTexture(i);
                    if (t != null) pass.bindSampler("Sampler" + i, t);
                }
                if (own != null) {
                    own.bind(pass, compiled instanceof GlRenderPipeline g ? g.program().getProgramId() : -1);
                }
                pass.setIndexBuffer(indices, indexType);
                pass.drawIndexed(0, m.drawState().indexCount());
            }
        }
    }

    /** A sampler named SamplerN with nothing bound to it explicitly takes RenderSystem's texture slot N. */
    static GpuTexture slotTexture(String sampler) {
        if (sampler.startsWith("Sampler")) {
            try {
                return RenderSystem.getShaderTexture(Integer.parseInt(sampler.substring(7)));
            } catch (NumberFormatException ignored) {
            }
        }
        return null;
    }

    // ---- vanilla core shaders (1.21.4 CoreShaders.*) ----------------------------------------------------------
    /** A vanilla core shader the mod bound directly (1.21.4's CoreShaders.X), drawn with the current state. */
    public static final class Vanilla {
        public static final Vanilla POSITION = new Vanilla("core/position");
        public static final Vanilla POSITION_COLOR = new Vanilla("core/position_color");
        public static final Vanilla POSITION_TEX = new Vanilla("core/position_tex", "Sampler0");
        public static final Vanilla POSITION_TEX_COLOR = new Vanilla("core/position_tex_color", "Sampler0");

        private final String path;
        private final String[] samplers;
        private final Map<java.util.List<Object>, RenderPipeline> pipelines = new HashMap<>();

        private Vanilla(String path, String... samplers) {
            this.path = path;
            this.samplers = samplers;
        }

        RenderPipeline pipeline(VertexFormat format, VertexFormat.Mode mode, Key key) {
            return pipelines.computeIfAbsent(java.util.List.of(key, format, mode), l -> {
                Key k = key;
                RenderPipeline.Builder b = RenderPipeline.builder()
                        .withLocation("mv/" + path.replace('/', '_') + "_" + pipelines.size())
                        .withVertexShader(path).withFragmentShader(path)
                        .withUniform("ModelViewMat", UniformType.MATRIX4X4)
                        .withUniform("ProjMat", UniformType.MATRIX4X4)
                        .withUniform("ColorModulator", UniformType.VEC4)
                        .withVertexFormat(format, mode);
                for (String s : samplers) b.withSampler(s);
                return k.apply(b).build();
            });
        }
    }
}
