// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import com.mojang.blaze3d.IndexType;
import com.mojang.blaze3d.PrimitiveTopology;
import com.mojang.blaze3d.buffers.GpuBuffer;
import com.mojang.blaze3d.buffers.GpuBufferSlice;
import com.mojang.blaze3d.opengl.GlRenderPipeline;
import com.mojang.blaze3d.pipeline.BlendFunction;
import com.mojang.blaze3d.pipeline.ColorTargetState;
import com.mojang.blaze3d.pipeline.CompiledRenderPipeline;
import com.mojang.blaze3d.pipeline.DepthStencilState;
import com.mojang.blaze3d.pipeline.RenderPipeline;
import com.mojang.blaze3d.pipeline.RenderTarget;
import com.mojang.blaze3d.pipeline.BindGroupLayout;
import com.mojang.blaze3d.platform.BlendFactor;
import com.mojang.blaze3d.platform.CompareOp;
import com.mojang.blaze3d.GpuFormat;
import com.mojang.blaze3d.shaders.UniformType;
import com.mojang.blaze3d.systems.RenderPass;
import com.mojang.blaze3d.systems.RenderSystem;
import com.mojang.blaze3d.textures.FilterMode;
import com.mojang.blaze3d.textures.GpuSampler;
import com.mojang.blaze3d.textures.GpuTexture;
import com.mojang.blaze3d.textures.GpuTextureView;
import com.mojang.blaze3d.vertex.MeshData;
import com.mojang.blaze3d.vertex.VertexFormat;
import com.mojang.logging.LogUtils;
import net.minecraft.client.Minecraft;
import net.minecraft.client.renderer.rendertype.PreparedRenderType;
import net.minecraft.client.renderer.rendertype.RenderType;
import net.minecraft.client.renderer.texture.AbstractTexture;
import net.minecraft.resources.Identifier;
import org.joml.Matrix4f;
import org.joml.Vector3f;
import org.joml.Vector4f;
import org.slf4j.Logger;

import java.nio.ByteBuffer;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.OptionalDouble;

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
 * <p>26.1.2 copy of the 1.21.11 file: 26.1 replaced the pipeline's DepthTestFunction / depth write / depth bias with one
 * {@code DepthStencilState(CompareOp, writeDepth, biasScale, biasConstant)} (absent = depth test off, which also stops
 * depth writes, as GL did with the test disabled) and its blend / colour / alpha write with one
 * {@code ColorTargetState(Optional<BlendFunction>, writeMask)}. A pipeline built without a depth state now has NO depth
 * test (1.21.11's default was LEQUAL), so {@link Key#apply} always sets one. 26.1.2 is not reverse-Z: LEQUAL is
 * {@code LESS_THAN_OR_EQUAL}, as vanilla's {@code DepthStencilState.DEFAULT}.
 *
 * <p>26.2 copy of the 26.1.2 file. 26.2 changed four things under it, each read from the 26.2 sources:
 * <ul>
 * <li><b>Reverse-Z.</b> {@code DepthStencilState.DEFAULT} is {@code GREATER_THAN_OR_EQUAL} and vanilla's depth biases are
 * positive ({@code 1, 10} where 26.1.2 had {@code -1, -10}): nearer is now GREATER. So a 1.21 {@code depthFunc(GL_LEQUAL)}
 * ("nearer or equal passes") is {@code GREATER_THAN_OR_EQUAL} here, {@code GL_LESS} is {@code GREATER_THAN}, and so on
 * ({@link #depthFunc}); EQUAL, NEVER and ALWAYS are unchanged.</li>
 * <li><b>Blend factors</b> are one enum, {@code BlendFactor} (SourceFactor/DestFactor are gone), and a
 * {@code ColorTargetState} names its texture format ({@code GpuFormat.RGBA8_UNORM}, as vanilla's own).</li>
 * <li><b>Pipelines declare bind group layouts</b> ({@code withBindGroupLayout(BindGroupLayout)}) instead of
 * {@code withUniform}/{@code withSampler}, a vertex BINDING ({@code withVertexBinding(0, format)}) and a
 * {@code PrimitiveTopology} (1.21's {@code VertexFormat.Mode}, now {@code com.mojang.blaze3d.PrimitiveTopology}).</li>
 * <li><b>No immediate upload.</b> {@code VertexFormat.uploadImmediateVertexBuffer/IndexBuffer} are gone (26.2 stages
 * geometry in one {@code StagedVertexBuffer} per frame); this keeps one growable vertex and one index buffer of its own
 * and writes each draw into them with {@code CommandEncoder.writeToBuffer}, as vanilla's {@code WeatherEffectRenderer}
 * does. {@code RenderPass.drawIndexed} takes {@code (indexCount, instanceCount, firstIndex, baseVertex, firstInstance)}
 * and {@code setVertexBuffer} a slice.</li>
 * </ul>
 * {@code Minecraft.getMainRenderTarget()} is {@code gameRenderer.mainRenderTarget()}, and
 * {@code RenderSystem.getModelViewMatrix()} is {@code getModelViewMatrixCopy()}.
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
    private static CompareOp depthFunction = CompareOp.GREATER_THAN_OR_EQUAL;   // 26.2 is reverse-Z: 1.21's LEQUAL
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
        blendFunction = new BlendFunction(BlendFactor.SRC_ALPHA, BlendFactor.ONE_MINUS_SRC_ALPHA, BlendFactor.ONE, BlendFactor.ZERO);
    }
    public static void blendFunc(BlendFactor src, BlendFactor dst) { blendFunction = new BlendFunction(src, dst); }
    public static void blendFuncSeparate(BlendFactor src, BlendFactor dst, BlendFactor srcA, BlendFactor dstA) {
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
    /** A 1.21 GL depth function, as 26.2's reverse-Z compare (nearer is GREATER): the ordered ones are mirrored. */
    public static void depthFunc(int glFunc) {
        depthFunction = switch (glFunc) {
            case 512 -> CompareOp.NEVER_PASS;              // GL_NEVER
            case 513 -> CompareOp.GREATER_THAN;            // GL_LESS
            case 514 -> CompareOp.EQUAL;                   // GL_EQUAL
            case 516 -> CompareOp.LESS_THAN;               // GL_GREATER
            case 517 -> CompareOp.NOT_EQUAL;               // GL_NOTEQUAL
            case 518 -> CompareOp.LESS_THAN_OR_EQUAL;      // GL_GEQUAL
            case 519 -> CompareOp.ALWAYS_PASS;             // GL_ALWAYS: tested (and written) but always passes
            default -> CompareOp.GREATER_THAN_OR_EQUAL;    // GL_LEQUAL
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
    /**
     * A texture bound to a SamplerN slot with the sampler it is read through. 1.21.11 removed RenderSystem's twelve
     * shader-texture slots (a RenderPass binds texture AND sampler by name), so this layer keeps them itself.
     */
    public record Slot(GpuTextureView view, GpuSampler sampler) {}

    private static final Slot[] SLOTS = new Slot[12];

    /** 1.21.11 binds a GpuTextureView with a sampler; 1.21.1 code named the texture by its location... */
    public static void setShaderTexture(int slot, Identifier texture) {
        AbstractTexture t = Minecraft.getInstance().getTextureManager().getTexture(texture);
        SLOTS[slot] = new Slot(t.getTextureView(), t.getSampler());
    }

    /** ...or by the GL id of a texture it made itself (see {@link RawGlTexture}). */
    public static void setShaderTexture(int slot, int glId) {
        SLOTS[slot] = glId <= 0 ? null
                : new Slot(RenderSystem.getDevice().createTextureView(RawGlTexture.of(glId)),
                        RenderSystem.getSamplerCache().getClampToEdge(FilterMode.LINEAR));
    }

    public static void setShaderTexture(int slot, GpuTextureView texture, GpuSampler sampler) {
        SLOTS[slot] = texture == null ? null : new Slot(texture, sampler);
    }

    public static void setShaderTexture(int slot, GpuTextureView texture) {
        setShaderTexture(slot, texture, RenderSystem.getSamplerCache().getClampToEdge(FilterMode.LINEAR));
    }

    /** 1.21.4's {@code RenderSystem.getShaderTexture(slot)}: what was last bound to the slot (null when nothing was). */
    public static GpuTextureView getShaderTexture(int slot) {
        return SLOTS[slot] == null ? null : SLOTS[slot].view();
    }

    /** 1.21.4's {@code RenderType.setupRenderState()}: the type's texture / lightmap / overlay shards and its shader. */
    public static void setupRenderState(RenderType type) {
        if (type instanceof LegacyRenderType legacy) legacy.setupRenderState();
        else LegacyRenderType.Copies.setupRenderState(type);   // another mod's relocated copy (CATALOG V117)
    }

    /** 1.21.4's {@code RenderType.clearRenderState()}. */
    public static void clearRenderState(RenderType type) {
        if (type instanceof LegacyRenderType legacy) legacy.clearRenderState();
        else LegacyRenderType.Copies.clearRenderState(type);   // another mod's relocated copy (CATALOG V117)
    }

    /** BufferUploader.drawWithShader: the current shader and the state set above, into the main render target -- or,
     *  while a {@link LegacyRenderType} is set up, with its shader and state into its output target (see {@link #load}). */
    public static void drawWithShader(MeshData mesh) {
        draw(mesh, shader, current(), target != null ? target : Minecraft.getInstance().gameRenderer.mainRenderTarget());
    }

    // ---- state as a value -------------------------------------------------------------------------------------
    /** One draw's fixed-function state: what a pipeline bakes in. Part of the pipeline cache key. {@code depth} null is
     *  1.21.11's NO_DEPTH_TEST (the depth test disabled). */
    public record Key(BlendFunction blend, CompareOp depth, boolean depthWrite, boolean writeColor,
                      boolean writeAlpha, boolean cull, float biasScale, float biasConstant) {
        RenderPipeline.Builder apply(RenderPipeline.Builder b) {
            int mask = (writeColor ? ColorTargetState.WRITE_COLOR : 0) | (writeAlpha ? ColorTargetState.WRITE_ALPHA : 0);
            b.withColorTargetState(new ColorTargetState(Optional.ofNullable(blend), GpuFormat.RGBA8_UNORM, mask));
            b.withDepthStencilState(depth == null ? Optional.empty()
                    : Optional.of(new DepthStencilState(depth, depthWrite, biasScale, biasConstant)));
            b.withCull(cull);
            return b;
        }

        Key withBlendIfUnset(BlendFunction b) {
            return blend != null || b == null ? this : new Key(b, depth, depthWrite, writeColor, writeAlpha, cull, biasScale, biasConstant);
        }
    }

    static Key current() {
        return new Key(blend ? blendFunction : null, depthTest ? depthFunction : null,
                depthWrite, writeColor, writeAlpha, cull, biasScale, biasConstant);
    }

    /** The state {@link #load} replaced, put back by {@link #restore}; {@code owner} is the type that loaded it. */
    record Saved(Object owner, boolean blend, BlendFunction blendFunction, boolean depthTest, CompareOp depthFunction,
                 boolean depthWrite, boolean writeColor, boolean writeAlpha, boolean cull, float biasScale,
                 float biasConstant, RenderTarget target, Object shader) {}

    /** Every set-up type's entry, oldest first. ONE stack for every type (not one per type), so set-ups and clears that
     *  do not nest -- A set up, B set up, A cleared, B cleared -- still end with nothing of either loaded. */
    private static final java.util.ArrayList<Saved> LOADED = new java.util.ArrayList<>();

    /**
     * 1.21.4's RenderType.setupRenderState() ran every shard of its CompositeState: the ShaderStateShard set the shader,
     * and the transparency / depth-test / write-mask / cull / layering shards set GL state. 1.21.1-1.21.4 code that sets
     * a type up and then draws with BufferUploader.drawWithShader (a mod's own render queue) drew with all of it.
     * {@link LegacyRenderType#setupRenderState} loads the type's shader and state here so {@link #drawWithShader} does
     * too, into the type's output target; {@link LegacyRenderType#clearRenderState} restores the state and the shader it
     * replaced. Only a shader object this layer draws with (a {@link ShaderInstance} or a {@link Vanilla}) is loaded: a type
     * with no shader state of its own keeps the one {@code setShader} set, as 1.21.4's did. As the shared 1.21.5-1.21.10 copies do.
     */
    static Saved load(Object owner, Key key, Object shaderObj, RenderTarget into) {
        Saved s = new Saved(owner, blend, blendFunction, depthTest, depthFunction, depthWrite, writeColor, writeAlpha, cull,
                biasScale, biasConstant, target, shader);
        LOADED.add(s);
        blend = key.blend() != null;
        if (blend) blendFunction = key.blend();
        depthTest = key.depth() != null;
        if (depthTest) depthFunction = key.depth();
        depthWrite = key.depthWrite();
        writeColor = key.writeColor();
        writeAlpha = key.writeAlpha();
        cull = key.cull();
        biasScale = key.biasScale();
        biasConstant = key.biasConstant();
        if (shaderObj instanceof ShaderInstance || shaderObj instanceof Vanilla) shader = shaderObj;
        target = into;
        return s;
    }

    /** Puts back what {@code owner}'s newest {@link #load} replaced, and drops that entry and every one loaded after it;
     *  an owner with nothing loaded changes nothing. Once nothing is loaded, what only a set-up type adds (its depth bias,
     *  its output target) is back at the default. */
    static void restore(Object owner) {
        int i = LOADED.size() - 1;
        while (i >= 0 && LOADED.get(i).owner() != owner) i--;
        if (i < 0) return;
        Saved s = LOADED.get(i);
        LOADED.subList(i, LOADED.size()).clear();
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
        shader = s.shader();
        if (LOADED.isEmpty()) {
            biasScale = 0;
            biasConstant = 0;
            target = null;
        }
    }

    // ---- drawing ----------------------------------------------------------------------------------------------
    /** The pipeline a draw with this shader object and state uses, compiled from the shader's own sources (null: no shader). */
    static RenderPipeline pipelineFor(Object shaderObj, VertexFormat format, PrimitiveTopology topology, Key key) {
        if (shaderObj instanceof ShaderInstance own) {
            RenderPipeline pipeline = own.pipeline(format, topology, key.withBlendIfUnset(own.blend()));
            RenderSystem.getDevice().precompilePipeline(pipeline, own::sourceFor);
            return pipeline;
        }
        if (shaderObj instanceof Vanilla v) return v.pipeline(format, topology, key);
        return null;
    }

    /** The DynamicTransforms block a draw with this shader binds: the model-view (or the mod's ModelViewMat), the colour
     *  (or the mod's ColorModulator), a zero model offset, the texture matrix (or the mod's TextureMat). */
    static GpuBufferSlice transforms(ShaderInstance own) {
        boolean override = own != null && own.COLOR_MODULATOR != null && own.COLOR_MODULATOR.touched();
        float[] c = override ? own.COLOR_MODULATOR.floats() : shaderColor;
        Matrix4f modelView = RenderSystem.getModelViewMatrixCopy();
        return RenderSystem.getDynamicUniforms().writeTransform(
                own == null ? modelView : own.transformMatrix(own.MODEL_VIEW_MATRIX, modelView),
                new Vector4f(c[0], c[1], c[2], c[3]), new Vector3f(),
                own == null ? new Matrix4f() : own.transformMatrix(own.TEXTURE_MATRIX, new Matrix4f()));
    }

    private static GpuBuffer vertexBuffer;
    private static GpuBuffer indexBuffer;

    /** 26.2 has no {@code uploadImmediateVertexBuffer}: one growable buffer of this layer's own, rewritten per draw (as
     *  vanilla's WeatherEffectRenderer does). */
    static GpuBuffer uploadVertices(ByteBuffer data) {
        vertexBuffer = upload(vertexBuffer, data, GpuBuffer.USAGE_VERTEX | GpuBuffer.USAGE_COPY_DST, "mv immediate vertices");
        return vertexBuffer;
    }

    static GpuBuffer uploadIndices(ByteBuffer data) {
        indexBuffer = upload(indexBuffer, data, GpuBuffer.USAGE_INDEX | GpuBuffer.USAGE_COPY_DST, "mv immediate indices");
        return indexBuffer;
    }

    private static GpuBuffer upload(GpuBuffer buffer, ByteBuffer data, int usage, String label) {
        if (buffer == null || buffer.isClosed() || buffer.size() < data.remaining()) {
            if (buffer != null && !buffer.isClosed()) buffer.close();
            buffer = RenderSystem.getDevice().createBuffer(() -> label, usage, Math.max(data.remaining(), 65536));
        }
        RenderSystem.getDevice().createCommandEncoder().writeToBuffer(buffer.slice(0, data.remaining()), data);
        return buffer;
    }

    /** What a mesh is drawn from: its vertices (and indices: its own, or the sequential ones for its topology), uploaded. */
    record Uploaded(GpuBuffer vertices, GpuBuffer indices, IndexType indexType, int indexCount) {
        static Uploaded of(MeshData m) {
            GpuBuffer vertices = uploadVertices(m.vertexBuffer());
            if (m.indexBuffer() == null) {
                RenderSystem.AutoStorageIndexBuffer seq = RenderSystem.getSequentialBuffer(m.drawState().primitiveTopology());
                return new Uploaded(vertices, seq.getBuffer(m.drawState().indexCount()), seq.type(), m.drawState().indexCount());
            }
            return new Uploaded(vertices, uploadIndices(m.indexBuffer()), m.drawState().indexType(), m.drawState().indexCount());
        }
    }

    /** The textures a draw with this shader binds: the shader's own samplers (with the slot / unit-0 / missing-texture
     *  fallbacks {@link ShaderInstance#bind} uses) or, for a vanilla shader, its declared Sampler slots. */
    static List<PreparedRenderType.Texture> textures(Object shaderObj) {
        List<PreparedRenderType.Texture> out = new java.util.ArrayList<>();
        if (shaderObj instanceof ShaderInstance own) {
            for (String s : own.samplers()) {
                Slot t = own.samplerSlot(s);
                if (t != null) out.add(new PreparedRenderType.Texture(s, t.view(), t.sampler()));
            }
        } else if (shaderObj instanceof Vanilla v) {
            for (String s : v.samplers) {
                Slot t = slotTexture(s);
                if (t == null) t = missingTextureSlot();
                if (t != null) out.add(new PreparedRenderType.Texture(s, t.view(), t.sampler()));
            }
        }
        return out;
    }

    static void draw(MeshData mesh, Object shaderObj, Key key, RenderTarget target) {
        if (mesh == null) return;
        try (MeshData m = mesh) {
            if (shaderObj == null) {
                LOGGER.warn("[mv] a draw with no shader set was skipped");
                return;
            }
            VertexFormat format = m.drawState().format();
            PrimitiveTopology topology = m.drawState().primitiveTopology();
            ShaderInstance own = shaderObj instanceof ShaderInstance s ? s : null;
            RenderPipeline pipeline = pipelineFor(shaderObj, format, topology, key);
            CompiledRenderPipeline compiled = own != null
                    ? RenderSystem.getDevice().precompilePipeline(pipeline, own::sourceFor)
                    : RenderSystem.getDevice().precompilePipeline(pipeline);
            if (!compiled.isValid()) return;   // the device has logged why
            Uploaded up = Uploaded.of(m);
            // 1.21.1's BufferUploader/RenderType draws called shader.apply() right before drawing and shader.clear()
            // after; a mod's ShaderInstance subclass overrides apply() to push its per-draw state (a mask's uniforms
            // and sampler), so it has to run here too. A COLOR_MODULATOR write made inside apply() (an opacity, after
            // super.apply()) is not kept: on 1.21.1 it came after apply()'s upload and the next draw's
            // setDefaultUniforms overwrote it, so it never reached the GPU. A write made before the draw is kept.
            if (own != null) {
                own.setDefaultUniforms();   // 1.21.1's VertexBuffer._drawWithShader ran this right before apply()
                Uniform cm = own.COLOR_MODULATOR;
                boolean wrote = cm != null && cm.touched();
                float[] before = wrote && cm.floats() != null ? cm.floats().clone() : null;
                own.apply();
                if (cm != null) {
                    if (before != null) cm.set(before);
                    else if (!wrote) cm.consume();
                }
            }
            // a COLOR_MODULATOR the mod wrote before the draw (kept above) replaces RenderSystem's colour; 1.21.8 puts
            // the colour in the DynamicTransforms block
            boolean override = own != null && own.COLOR_MODULATOR != null && own.COLOR_MODULATOR.touched();
            GpuBufferSlice transforms = transforms(own);
            // as PreparedRenderType.drawFromBuffer: an output override (a post-effect pass) wins over the target's own views
            GpuTextureView colorView = RenderSystem.outputColorTextureOverride != null
                    ? RenderSystem.outputColorTextureOverride : target.getColorTextureView();
            GpuTextureView depthView = target.useDepth
                    ? (RenderSystem.outputDepthTextureOverride != null ? RenderSystem.outputDepthTextureOverride : target.getDepthTextureView())
                    : null;
            java.util.List<GpuBuffer> temps = new java.util.ArrayList<>();
            try (RenderPass pass = RenderSystem.getDevice().createCommandEncoder().createRenderPass(
                    () -> "mv draw", colorView, Optional.empty(), depthView, OptionalDouble.empty())) {
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
                pass.setVertexBuffer(0, up.vertices().slice());
                var scissor = RenderSystem.getScissorStateForRenderTypeDraws();
                if (scissor.enabled()) pass.enableScissor(scissor.x(), scissor.y(), scissor.width(), scissor.height());
                for (int i = 0; i < 12; i++) {
                    Slot t = SLOTS[i];
                    if (t != null) pass.bindTexture("Sampler" + i, t.view(), t.sampler());
                }
                if (own != null) {
                    own.bind(pass, compiled instanceof GlRenderPipeline g ? g.program().getProgramId() : -1, temps);
                }
                pass.setIndexBuffer(up.indices(), up.indexType());
                pass.drawIndexed(up.indexCount(), 1, 0, 0, 0);
            } finally {
                for (GpuBuffer tb : temps) tb.close();
                if (own != null) own.clear();
                if (override) own.COLOR_MODULATOR.consume();   // 1.21.1 re-uploaded the default on every apply()
                if (own != null) own.consumeBuiltIns();
            }
        }
    }

    /** The missing-texture sprite's view and sampler: what a declared sampler nothing bound falls back to (1.21.11's
     *  ShaderInstance calls it; 26.1.2 inherits that helper, so this copy must have it too). */
    static Slot missingTextureSlot() {
        net.minecraft.client.renderer.texture.AbstractTexture t = net.minecraft.client.Minecraft.getInstance().getTextureManager()
                .getTexture(net.minecraft.client.renderer.texture.MissingTextureAtlasSprite.getLocation());
        return t == null ? null : new Slot(t.getTextureView(), t.getSampler());
    }

    /** A sampler named SamplerN with nothing bound to it explicitly takes RenderSystem's texture slot N. */
    static Slot slotTexture(String sampler) {
        if (sampler.startsWith("Sampler")) {
            try {
                int slot = Integer.parseInt(sampler.substring(7));
                return slot >= 0 && slot < SLOTS.length ? SLOTS[slot] : null;
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
        final String[] samplers;
        private final Map<java.util.List<Object>, RenderPipeline> pipelines = new HashMap<>();

        private Vanilla(String path, String... samplers) {
            this.path = path;
            this.samplers = samplers;
        }

        RenderPipeline pipeline(VertexFormat format, PrimitiveTopology topology, Key key) {
            return pipelines.computeIfAbsent(java.util.List.of(key, format, topology), l -> {
                BindGroupLayout.Builder layout = BindGroupLayout.builder()
                        .withUniform("DynamicTransforms", UniformType.UNIFORM_BUFFER)   // ModelViewMat, ColorModulator
                        .withUniform("Projection", UniformType.UNIFORM_BUFFER);          // ProjMat
                for (String s : samplers) layout.withSampler(s);
                RenderPipeline.Builder b = RenderPipeline.builder()
                        .withLocation("mv/" + path.replace('/', '_') + "_" + pipelines.size())
                        .withVertexShader(path).withFragmentShader(path)
                        .withBindGroupLayout(layout.build())
                        .withVertexBinding(0, format)
                        .withPrimitiveTopology(topology);
                return key.apply(b).build();
            });
        }
    }
}
