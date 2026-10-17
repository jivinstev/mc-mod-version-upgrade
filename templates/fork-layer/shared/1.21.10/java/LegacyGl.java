// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import com.mojang.blaze3d.buffers.GpuBuffer;
import com.mojang.blaze3d.buffers.GpuBufferSlice;
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
import com.mojang.blaze3d.textures.GpuTextureView;
import com.mojang.blaze3d.vertex.MeshData;
import com.mojang.blaze3d.vertex.VertexFormat;
import com.mojang.logging.LogUtils;
import net.minecraft.client.Minecraft;
import net.minecraft.client.renderer.texture.AbstractTexture;
import net.minecraft.resources.ResourceLocation;
import org.joml.Vector3f;
import org.joml.Vector4f;
import org.slf4j.Logger;

import java.util.HashMap;
import java.util.Map;
import java.util.OptionalDouble;
import java.util.OptionalInt;

/**
 * Immediate-mode GL state and drawing as 1.21.1-1.21.4 code wrote it, on 1.21.8's RenderPipeline/RenderPass.
 *
 * <p>1.21.5 removed RenderSystem.enableBlend/blendFunc/depthMask/enable(Disable)DepthTest/Cull/colorMask/setShader and
 * BufferUploader: every draw now carries a pipeline whose blend, depth, cull and write masks are fixed. This records
 * the state those calls used to set, and {@link #draw} folds it into a pipeline (cached per shader and state) and
 * draws through a RenderPass exactly as 1.21.8's {@code RenderType.CompositeRenderType.draw} does: the default
 * uniforms (Projection, Fog, Globals, Lighting) through {@code RenderSystem.bindDefaultUniforms}, a
 * {@code DynamicTransforms} block written from the model-view, colour, model-offset, texture matrix and line width
 * RenderSystem holds, the sampler slots -- plus the shader's own uniform blocks and samplers (see {@link ShaderInstance}).
 * 1.21.6 replaced the per-uniform setters 1.21.5 still had with those blocks, which is why this file differs from the
 * 1.21.5 one.
 *
 * <p>1.21.10 copy of the 1.21.8 file: the only change is the model offset in the DynamicTransforms block (RenderSystem no
 * longer holds one; vanilla's CompositeRenderType.draw writes a zero vector).
 *
 * <p>Not expressible on 1.21.8 and logged once: a blend equation other than ADD.
 */
// MV-REQUIRES: RawGlTexture
// MV-REQUIRES: ShaderInstance
// MV-REQUIRES: ShaderShard
// MV-REQUIRES: Uniform
// MV-REQUIRES: LegacyRenderType
public final class LegacyGl {
    private static final Logger LOGGER = LogUtils.getLogger();

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
    // what a set-up LegacyRenderType adds (see load): its depth bias and its output target (null: the main target)
    private static float biasScale, biasConstant;
    private static RenderTarget target;
    private static float[] shaderColor = {1f, 1f, 1f, 1f};

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
            LOGGER.warn("[mv] blend equation {} has no 1.21.8 pipeline equivalent; ADD is used", equation);
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
    /** 1.21.1's {@code RenderSystem.setShader(Supplier<ShaderInstance>)}, as the mod's lambdas and method refs write it. */
    public static void setShader(java.util.function.Supplier<ShaderInstance> s) { shader = s == null ? null : s.get(); }
    public static void setShaderColor(float r, float g, float b, float a) { shaderColor = new float[] {r, g, b, a}; }
    public static void recordRenderCall(Runnable r) {
        if (RenderSystem.isOnRenderThread()) r.run(); else Minecraft.getInstance().execute(r);
    }
    /** 1.21.8 binds a GpuTextureView; 1.21.1 code named the texture by its location... */
    public static void setShaderTexture(int slot, ResourceLocation texture) {
        AbstractTexture t = Minecraft.getInstance().getTextureManager().getTexture(texture);
        RenderSystem.setShaderTexture(slot, t.getTextureView());
    }

    /** ...or by the GL id of a texture it made itself (see {@link RawGlTexture}). */
    public static void setShaderTexture(int slot, int glId) {
        RenderSystem.setShaderTexture(slot, glId <= 0 ? null
                : RenderSystem.getDevice().createTextureView(RawGlTexture.of(glId)));
    }

    public static void setShaderTexture(int slot, GpuTextureView texture) {
        RenderSystem.setShaderTexture(slot, texture);
    }

    /** BufferUploader.drawWithShader: the current shader and the state set above, into the main render target -- or,
     *  while a {@link LegacyRenderType} is set up, with its shader and state into its output target (see {@link #load}). */
    public static void drawWithShader(MeshData mesh) {
        draw(mesh, shader, current(), target != null ? target : Minecraft.getInstance().getMainRenderTarget());
    }

    // ---- state as a value -------------------------------------------------------------------------------------
    /** One draw's fixed-function state: what a 1.21.8 pipeline bakes in. Part of the pipeline cache key. */
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
                depthWrite, writeColor, writeAlpha, cull, biasScale, biasConstant);
    }

    /** The state {@link #load} replaced, put back by {@link #restore}. */
    record Saved(boolean blend, BlendFunction blendFunction, boolean depthTest, DepthTestFunction depthFunction,
                 boolean depthWrite, boolean writeColor, boolean writeAlpha, boolean cull, float biasScale,
                 float biasConstant, RenderTarget target) {}

    /**
     * 1.21.4's RenderType.setupRenderState() ran every shard of its CompositeState: the ShaderStateShard set the shader,
     * and the transparency / depth-test / write-mask / cull / layering shards set GL state. 1.21.1-1.21.4 code that sets
     * a type up and then draws with BufferUploader.drawWithShader (a mod's own render queue) drew with all of it.
     * {@link LegacyRenderType#setupRenderState} loads the type's shader and state here so {@link #drawWithShader} does
     * too, into the type's output target; {@link LegacyRenderType#clearRenderState} restores the state (the shader stays
     * set, as 1.21.4's ShaderStateShard left it).
     */
    static Saved load(Key key, Object shaderObj, RenderTarget into) {
        Saved s = new Saved(blend, blendFunction, depthTest, depthFunction, depthWrite, writeColor, writeAlpha, cull,
                biasScale, biasConstant, target);
        blend = key.blend() != null;
        if (blend) blendFunction = key.blend();
        depthTest = key.depth() != DepthTestFunction.NO_DEPTH_TEST;
        if (depthTest) depthFunction = key.depth();
        depthWrite = key.depthWrite();
        writeColor = key.writeColor();
        writeAlpha = key.writeAlpha();
        cull = key.cull();
        biasScale = key.biasScale();
        biasConstant = key.biasConstant();
        shader = shaderObj;
        target = into;
        return s;
    }

    static void restore(Saved s) {
        blend = s.blend();
        blendFunction = s.blendFunction();
        depthTest = s.depthTest();
        depthFunction = s.depthFunction();
        depthWrite = s.depthWrite();
        writeColor = s.writeColor();
        writeAlpha = s.writeAlpha();
        cull = s.cull();
        biasScale = s.biasScale();
        biasConstant = s.biasConstant();
        target = s.target();
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
            // 1.21.1's BufferUploader/RenderType draws called shader.apply() right before drawing and shader.clear()
            // after; a mod's ShaderInstance subclass overrides apply() to push its per-draw state (a mask, an opacity
            // written into COLOR_MODULATOR after super.apply()), so it has to run here too
            if (own != null) {
                own.setDefaultUniforms();   // 1.21.1's VertexBuffer._drawWithShader ran this right before apply()
                own.apply();
            }
            // 1.21.1's ShaderInstance.apply() uploaded RenderSystem's colour into COLOR_MODULATOR and a mod could
            // overwrite it afterwards; 1.21.8 puts the colour in the DynamicTransforms block, so use the mod's value
            // when it wrote one
            boolean override = own != null && own.COLOR_MODULATOR != null && own.COLOR_MODULATOR.touched();
            float[] c = override ? own.COLOR_MODULATOR.floats() : shaderColor;
            GpuBufferSlice transforms = RenderSystem.getDynamicUniforms().writeTransform(
                    own == null ? RenderSystem.getModelViewMatrix() : own.transformMatrix(own.MODEL_VIEW_MATRIX, RenderSystem.getModelViewMatrix()),
                    new Vector4f(c[0], c[1], c[2], c[3]), new Vector3f(),   // 1.21.9 dropped RenderSystem.getModelOffset(); vanilla's own draws pass a zero vector
                    own == null ? RenderSystem.getTextureMatrix() : own.transformMatrix(own.TEXTURE_MATRIX, RenderSystem.getTextureMatrix()),
                    own == null ? RenderSystem.getShaderLineWidth() : own.lineWidth(RenderSystem.getShaderLineWidth()));
            // as CompositeRenderType.draw: an output override (a post-effect pass) wins over the target's own views
            GpuTextureView colorView = RenderSystem.outputColorTextureOverride != null
                    ? RenderSystem.outputColorTextureOverride : target.getColorTextureView();
            GpuTextureView depthView = target.useDepth
                    ? (RenderSystem.outputDepthTextureOverride != null ? RenderSystem.outputDepthTextureOverride : target.getDepthTextureView())
                    : null;
            java.util.List<GpuBuffer> temps = new java.util.ArrayList<>();
            try (RenderPass pass = RenderSystem.getDevice().createCommandEncoder().createRenderPass(
                    () -> "mv draw", colorView, OptionalInt.empty(), depthView, OptionalDouble.empty())) {
                pass.setPipeline(pipeline);
                RenderSystem.bindDefaultUniforms(pass);
                pass.setUniform("DynamicTransforms", transforms);
                if (own != null) {
                    // a built-in the mod wrote replaces vanilla's block for this draw (see ShaderInstance.globalsBuffer)
                    GpuBuffer projection = own.projectionBuffer(temps);
                    if (projection != null) pass.setUniform("Projection", projection);
                    GpuBuffer globals = own.globalsBuffer(temps);
                    if (globals != null) pass.setUniform("Globals", globals);
                }
                pass.setVertexBuffer(0, vertices);
                var scissor = RenderSystem.getScissorStateForRenderTypeDraws();
                if (scissor.enabled()) pass.enableScissor(scissor.x(), scissor.y(), scissor.width(), scissor.height());
                for (int i = 0; i < 12; i++) {
                    GpuTextureView t = RenderSystem.getShaderTexture(i);
                    if (t != null) pass.bindSampler("Sampler" + i, t);
                }
                if (own != null) {
                    own.bind(pass, compiled instanceof GlRenderPipeline g ? g.program().getProgramId() : -1, temps);
                }
                pass.setIndexBuffer(indices, indexType);
                pass.drawIndexed(0, 0, m.drawState().indexCount(), 1);
            } finally {
                for (GpuBuffer tb : temps) tb.close();
                if (own != null) own.clear();
                if (override) own.COLOR_MODULATOR.consume();   // 1.21.1 re-uploaded the default on every apply()
                if (own != null) own.consumeBuiltIns();
            }
        }
    }

    /** The missing-texture sprite: what a declared but never-bound sampler reads (see ShaderInstance.bind). */
    static GpuTextureView missingTexture() {
        net.minecraft.client.renderer.texture.AbstractTexture t = net.minecraft.client.Minecraft.getInstance().getTextureManager()
                .getTexture(net.minecraft.client.renderer.texture.MissingTextureAtlasSprite.getLocation());
        return t == null ? null : t.getTextureView();
    }

    /** A sampler named SamplerN with nothing bound to it explicitly takes RenderSystem's texture slot N. */
    static GpuTextureView slotTexture(String sampler) {
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
                        .withUniform("DynamicTransforms", UniformType.UNIFORM_BUFFER)   // ModelViewMat, ColorModulator
                        .withUniform("Projection", UniformType.UNIFORM_BUFFER)           // ProjMat
                        .withVertexFormat(format, mode);
                for (String s : samplers) b.withSampler(s);
                return k.apply(b).build();
            });
        }
    }
}
