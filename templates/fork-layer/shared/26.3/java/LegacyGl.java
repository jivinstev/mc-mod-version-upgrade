// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import com.mojang.renderpearl.api.pipeline.IndexType;
import com.mojang.renderpearl.api.pipeline.PrimitiveTopology;
import com.mojang.renderpearl.api.buffers.GpuBuffer;
import com.mojang.renderpearl.api.buffers.GpuBufferSlice;
import com.mojang.renderpearl.backend.opengl.GlRenderPipeline;
import com.mojang.renderpearl.frontend.FrontendCommandEncoder;
import com.mojang.renderpearl.frontend.FrontendRenderPipeline;
import com.mojang.renderpearl.api.pipeline.ShaderSource;
import com.mojang.blaze3d.pipeline.PipelineCache;
import com.mojang.renderpearl.api.pipeline.BlendFunction;
import com.mojang.renderpearl.api.pipeline.ColorTargetState;
import com.mojang.renderpearl.api.pipeline.CompiledRenderPipeline;
import com.mojang.renderpearl.api.pipeline.DepthStencilState;
import com.mojang.renderpearl.api.pipeline.RenderPipeline;
import com.mojang.blaze3d.pipeline.RenderTarget;
import com.mojang.renderpearl.api.pipeline.BindGroupLayout;
import com.mojang.renderpearl.api.pipeline.BlendFactor;
import com.mojang.renderpearl.api.pipeline.CompareOp;
import com.mojang.renderpearl.api.GpuFormat;
import com.mojang.renderpearl.api.pipeline.UniformType;
import com.mojang.renderpearl.api.commands.RenderPass;
import com.mojang.blaze3d.systems.RenderSystem;
import com.mojang.renderpearl.api.textures.FilterMode;
import com.mojang.renderpearl.api.textures.GpuSampler;
import com.mojang.renderpearl.api.textures.GpuTexture;
import com.mojang.renderpearl.api.textures.GpuTextureView;
import com.mojang.blaze3d.vertex.MeshData;
import com.mojang.renderpearl.api.vertex.VertexFormat;
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
 * <p>26.3 copy of the 26.2 file. 26.3 moved the GPU API to {@code com.mojang.renderpearl} (the class moves are generated)
 * and changed four things this file uses, each read from the 26.3 sources (CATALOG §V101):
 * <ul>
 * <li><b>No synchronous precompile.</b> {@code GpuDevice.precompilePipeline(pipeline[, source])} is gone: a pipeline is
 * compiled with {@code compilePipeline(pipeline, ShaderSource, executor)} (a future of a {@code Pending} whose
 * {@code finishCompile()} gives the {@code CompiledRenderPipeline}, null on failure), and {@code RenderPass.setPipeline}
 * takes the COMPILED pipeline. Vanilla looks compiled pipelines up in {@code RenderSystem}'s current {@code PipelineCache}
 * ({@code getCompiledPipeline}), which compiles a miss from vanilla's own shader sources -- so a pipeline built from a
 * {@link ShaderInstance}'s sources is compiled here ({@link #compiled}) and inserted into the current cache, where
 * {@code PreparedRenderType.drawFromBuffer} finds it. A shader reload closes the cache and everything in it; a closed
 * pipeline is compiled again on its next use.</li>
 * <li><b>Samplers are bind-group uniforms</b> ({@code withUniform(name, UniformType.COMBINED_IMAGE_SAMPLER)};
 * {@code withSampler} is gone) and a pass binds one with {@code setUniform(name, view, sampler)} ({@code bindTexture} is
 * gone). Every block or sampler a shader declares must be in the pipeline's layouts, and (strict validation, a dev
 * client) every one a layout declares must be bound at the draw, so a pipeline declares exactly what its GLSL declares:
 * the core shaders' blocks are listed with them ({@link Vanilla}), a {@link ShaderInstance}'s are read from its sources.</li>
 * <li><b>No output override.</b> {@code RenderSystem.outputColor/DepthTextureOverride} and {@code RenderTarget.useDepth}
 * are gone: a draw goes into the target's own views ({@code getDepthTextureView()} is null without depth), and a
 * render pass refuses a pipeline whose colour format is not the attachment's, so the pipeline takes the target's
 * colour format.</li>
 * <li>A compiled pipeline is a {@code FrontendRenderPipeline}; the GL program is its {@code backendRenderPipeline()}'s.</li>
 * </ul>
 *
 * <p>26.3 has ONE command encoder, and it refuses to open a pass (or write a buffer) while one is open; NeoForge posts
 * every {@code RenderLevelStageEvent} stage but AfterSky and AfterLevel inside the main pass ({@link RenderPasses}). So
 * a draw has three forms: into a pass of its own on a target ({@link #drawWithShader(MeshData)}) or on given views
 * ({@link #draw(MeshData, GpuTextureView, GpuTextureView)}), which asserts no pass is open with a message naming the
 * stages it may run from; or INTO a pass that is already open ({@link #draw(MeshData, RenderPass)}): an in-pass stage
 * event's {@code getRenderPass()}, or the pass vanilla opened on an offscreen texture (26.3 draws every GUI, tooltip,
 * oversized and picture-in-picture item into an atlas texture, in a pass of its own). A draw into an open pass uploads
 * its geometry into the encoder's transient memory (mapped: no encoder command, which an open pass would refuse) and
 * takes the colour format of the pass's attachment for its pipeline.
 *
 * <p>Not expressible on 1.21.8 and logged once: a blend equation other than ADD.
 */
// MV-REQUIRES: RawGlTexture
// MV-REQUIRES: RenderPasses
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
    }

    /** 1.21.4's {@code RenderType.clearRenderState()}. */
    public static void clearRenderState(RenderType type) {
        if (type instanceof LegacyRenderType legacy) legacy.clearRenderState();
    }

    /** BufferUploader.drawWithShader: the current shader and the state set above, into the main render target. */
    public static void drawWithShader(MeshData mesh) {
        draw(mesh, shader, current(), Minecraft.getInstance().gameRenderer.mainRenderTarget());
    }

    /**
     * The current shader and state, drawn INTO a render pass that is already open (it stays open): an in-pass
     * {@code RenderLevelStageEvent}'s {@code getRenderPass()}, or one vanilla opened on an offscreen texture (26.3's GUI
     * item atlas). The pipeline takes the colour format of the pass's attachment.
     */
    public static void draw(MeshData mesh, RenderPass into) {
        draw(mesh, into, passColorFormat(into));
    }

    /** As {@link #draw(MeshData, RenderPass)}, for a pass whose colour attachment has {@code colorFormat}. */
    public static void draw(MeshData mesh, RenderPass into, GpuFormat colorFormat) {
        if (into == null) throw new IllegalArgumentException("[mv] LegacyGl.draw into a null render pass");
        draw(mesh, shader, current(), null, null, into, colorFormat);
    }

    /** The current shader and state, drawn into {@code colour} (and {@code depth}; null: no depth) in a pass of its own:
     *  a texture other than the main target. No pass may be open ({@link RenderPasses#requireNoOpenPass}). */
    public static void draw(MeshData mesh, GpuTextureView colour, GpuTextureView depth) {
        if (colour == null) throw new IllegalArgumentException("[mv] LegacyGl.draw into a null colour view");
        draw(mesh, shader, current(), colour, depth, null, colour.texture().getFormat());
    }

    /** Whether the device's command encoder has a render pass open (only a pass-free stage or code outside the level
     *  may open one; see {@link RenderPasses}). */
    public static boolean inRenderPass() {
        return RenderSystem.getDevice().createCommandEncoder() instanceof FrontendCommandEncoder f && f.isInRenderPass();
    }

    private static java.lang.reflect.Field colorAttachments;

    /** The colour format of an open pass's first attachment ({@code FrontendRenderPass} keeps its attachments private);
     *  the main target's when it cannot be read. */
    static GpuFormat passColorFormat(RenderPass pass) {
        try {
            if (colorAttachments == null || colorAttachments.getDeclaringClass() != pass.getClass()) {
                java.lang.reflect.Field f = pass.getClass().getDeclaredField("colorAttachments");
                f.setAccessible(true);
                colorAttachments = f;
            }
            if (colorAttachments.get(pass) instanceof List<?> l && !l.isEmpty()
                    && l.get(0) instanceof com.mojang.renderpearl.api.commands.RenderPassDescriptor.Attachment<?> a
                    && a.textureView() != null) {
                return a.textureView().texture().getFormat();
            }
        } catch (ReflectiveOperationException | RuntimeException ignored) {
        }
        GpuTexture main = Minecraft.getInstance().gameRenderer.mainRenderTarget().getColorTexture();
        return main == null ? GpuFormat.RGBA8_UNORM : main.getFormat();
    }

    // ---- state as a value -------------------------------------------------------------------------------------
    /** One draw's fixed-function state: what a pipeline bakes in. Part of the pipeline cache key. {@code depth} null is
     *  1.21.11's NO_DEPTH_TEST (the depth test disabled). */
    public record Key(BlendFunction blend, CompareOp depth, boolean depthWrite, boolean writeColor,
                      boolean writeAlpha, boolean cull, float biasScale, float biasConstant) {
        RenderPipeline.Builder apply(RenderPipeline.Builder b, GpuFormat colorFormat) {
            int mask = (writeColor ? ColorTargetState.WRITE_COLOR : 0) | (writeAlpha ? ColorTargetState.WRITE_ALPHA : 0);
            b.withColorTargetState(new ColorTargetState(Optional.ofNullable(blend), colorFormat, mask));
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
                depthWrite, writeColor, writeAlpha, cull, 0, 0);
    }

    // ---- drawing ----------------------------------------------------------------------------------------------
    /** The pipeline a draw with this shader object and state uses, compiled from the shader's own sources (null: no shader). */
    static RenderPipeline pipelineFor(Object shaderObj, VertexFormat format, PrimitiveTopology topology, Key key) {
        return pipelineFor(shaderObj, format, topology, key, GpuFormat.RGBA8_UNORM);   // the main target's colour format
    }

    /** As above, for a target whose colour texture has {@code colorFormat}. A pipeline of the shader's own is compiled
     *  (and put where vanilla's draw path looks it up) here, as 26.2's precompilePipeline did. */
    static RenderPipeline pipelineFor(Object shaderObj, VertexFormat format, PrimitiveTopology topology, Key key, GpuFormat colorFormat) {
        if (shaderObj instanceof ShaderInstance own) {
            RenderPipeline pipeline = own.pipeline(format, topology, key.withBlendIfUnset(own.blend()), colorFormat);
            compiled(pipeline, own);
            return pipeline;
        }
        if (shaderObj instanceof Vanilla v) return v.pipeline(format, topology, key, colorFormat);
        return null;
    }

    private static final Map<RenderPipeline, CompiledRenderPipeline> COMPILED = new java.util.IdentityHashMap<>();
    private static final java.util.Set<String> FAILED = new java.util.HashSet<>();

    /**
     * The compiled form of a pipeline: a vanilla shader's from RenderSystem's pipeline cache; one of a
     * {@link ShaderInstance}'s compiled from its own sources (once; again after a reload closed it) and inserted into the
     * current pipeline cache, so vanilla's {@code getCompiledPipeline} answers with it instead of compiling it from
     * vanilla's sources (which do not have the shader). Null when it does not compile (logged once per pipeline).
     */
    static CompiledRenderPipeline compiled(RenderPipeline pipeline, ShaderInstance own) {
        if (own == null) return RenderSystem.getCompiledPipelineNullable(pipeline);
        CompiledRenderPipeline c = COMPILED.get(pipeline);
        if (c == null || c.isClosed()) {
            c = RenderSystem.getDevice().compilePipeline(pipeline, own.shaderSource(), Runnable::run).join().finishCompile();
            if (c == null) {
                if (FAILED.add(pipeline.getLocation().toString())) {
                    LOGGER.error("[mv] {}: pipeline {} did not compile (the device logged why)", own.getName(), pipeline.getLocation());
                }
                COMPILED.remove(pipeline);
                return null;
            }
            COMPILED.put(pipeline, c);
        }
        // the current cache (replaced on every shader reload) is only reachable through its setter, which returns it
        PipelineCache cache = RenderSystem.setCurrentPipelineCache(null);
        RenderSystem.setCurrentPipelineCache(cache);
        if (cache != null) cache.insert(pipeline, c);
        return c;
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

    /** What a mesh is drawn from: its vertices (and indices: its own, or the sequential ones for its topology), uploaded.
     *  {@code baseVertex} / {@code firstIndex} place the mesh in the buffers (0 except for a transient upload). */
    record Uploaded(GpuBuffer vertices, GpuBuffer indices, IndexType indexType, int indexCount, int baseVertex, int firstIndex) {
        static Uploaded of(MeshData m) {
            GpuBuffer vertices = uploadVertices(m.vertexBuffer());
            if (m.indexBuffer() == null) {
                RenderSystem.AutoStorageIndexBuffer seq = RenderSystem.getSequentialBuffer(m.drawState().primitiveTopology());
                return new Uploaded(vertices, seq.getBuffer(m.drawState().indexCount()), seq.type(), m.drawState().indexCount(), 0, 0);
            }
            return new Uploaded(vertices, uploadIndices(m.indexBuffer()), m.drawState().indexType(), m.drawState().indexCount(), 0, 0);
        }

        /**
         * For a draw into a pass that is already open, where the encoder refuses {@code writeToBuffer}: the geometry goes
         * into the encoder's transient memory through a MAPPED allocation (a CPU write, no encoder command; valid for the
         * frame). An index buffer binds without an offset, so the allocation's offset becomes the first index (and the
         * vertex one the base vertex, for vanilla's {@code drawFromBuffer}, which binds the whole buffer).
         */
        static Uploaded inPass(MeshData m) {
            int vertexSize = m.drawState().format().getVertexSize();
            GpuBufferSlice v = transient_(m.vertexBuffer(), vertexSize, GpuBuffer.USAGE_VERTEX);
            int baseVertex = RenderPasses.firstElement(v.offset(), vertexSize);
            if (m.indexBuffer() == null) {
                RenderSystem.AutoStorageIndexBuffer seq = RenderSystem.getSequentialBuffer(m.drawState().primitiveTopology());
                // grows with device.createBuffer(initial data), which an open pass allows
                return new Uploaded(v.buffer(), seq.getBuffer(m.drawState().indexCount()), seq.type(), m.drawState().indexCount(), baseVertex, 0);
            }
            IndexType type = m.drawState().indexType();
            GpuBufferSlice i = transient_(m.indexBuffer(), Math.max(4, type.bytes), GpuBuffer.USAGE_INDEX);
            return new Uploaded(v.buffer(), i.buffer(), type, m.drawState().indexCount(), baseVertex,
                    RenderPasses.firstElement(i.offset(), type.bytes));
        }

        private static GpuBufferSlice transient_(ByteBuffer data, int alignment, int usage) {
            try (GpuBufferSlice.MappedView view = RenderSystem.getDevice().createCommandEncoder().transientMemory()
                    .allocateGpuMapped(data.remaining(), alignment, usage)) {
                view.data().put(data.duplicate());
                return view.slice();
            }
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
        GpuTexture colorTexture = target.getColorTexture();
        if (colorTexture == null) {
            mesh.close();
            return;
        }
        // 26.3 has no output override: the target's own views (no depth view when it has no depth)
        draw(mesh, shaderObj, key, target.getColorTextureView(), target.getDepthTextureView(), null, colorTexture.getFormat());
    }

    /** A draw of {@code shaderObj} with {@code key}: into {@code into} when it is given (an open pass, left open), else in
     *  a pass of its own on {@code colorView} / {@code depthView}. The pipeline is built for {@code colorFormat}. */
    static void draw(MeshData mesh, Object shaderObj, Key key, GpuTextureView colorView, GpuTextureView depthView,
                     RenderPass into, GpuFormat colorFormat) {
        if (mesh == null) return;
        try (MeshData m = mesh) {
            if (shaderObj == null) {
                LOGGER.warn("[mv] a draw with no shader set was skipped");
                return;
            }
            // before anything is compiled or uploaded, so the message is this one and not the encoder's
            if (into == null) RenderPasses.requireNoOpenPass(inRenderPass(), "LegacyGl.draw");
            VertexFormat format = m.drawState().format();
            PrimitiveTopology topology = m.drawState().primitiveTopology();
            ShaderInstance own = shaderObj instanceof ShaderInstance s ? s : null;
            RenderPipeline pipeline = pipelineFor(shaderObj, format, topology, key, colorFormat);
            CompiledRenderPipeline compiled = compiled(pipeline, own);
            if (compiled == null) return;   // logged (or, for a vanilla shader, by the device)
            Uploaded up = into == null ? Uploaded.of(m) : Uploaded.inPass(m);
            // 1.21.1's BufferUploader/RenderType draws called shader.apply() right before drawing and shader.clear()
            // after; a mod's ShaderInstance subclass overrides apply() to push its per-draw state (a mask, an opacity
            // written into COLOR_MODULATOR after super.apply()), so it has to run here too
            if (own != null) {
                own.setDefaultUniforms();   // 1.21.1's VertexBuffer._drawWithShader ran this right before apply()
                own.apply();
            }
            boolean override = own != null && own.COLOR_MODULATOR != null && own.COLOR_MODULATOR.touched();
            GpuBufferSlice transforms = transforms(own);
            java.util.List<GpuBuffer> temps = new java.util.ArrayList<>();
            boolean replacedDefaults = false;
            RenderPass pass = into != null ? into : RenderSystem.getDevice().createCommandEncoder().createRenderPass(
                    () -> "mv draw", colorView, Optional.empty(), depthView, OptionalDouble.empty());
            try {
                pass.setPipeline(compiled);
                RenderSystem.bindDefaultUniforms(pass);
                pass.setUniform("DynamicTransforms", transforms);
                if (own != null) {
                    // a built-in the mod wrote replaces vanilla's block for this draw (see ShaderInstance.globalsBuffer)
                    GpuBuffer projection = own.projectionBuffer(temps);
                    if (projection != null) pass.setUniform("Projection", projection);
                    GpuBuffer globals = own.globalsBuffer(temps);
                    if (globals != null) pass.setUniform("Globals", globals);
                    replacedDefaults = projection != null || globals != null;
                }
                pass.setVertexBuffer(0, up.vertices().slice());   // the whole buffer: baseVertex places the mesh
                var scissor = RenderSystem.getScissorStateForRenderTypeDraws();
                if (scissor.enabled()) pass.enableScissor(scissor.x(), scissor.y(), scissor.width(), scissor.height());
                for (int i = 0; i < 12; i++) {
                    Slot t = SLOTS[i];
                    if (t != null) pass.setUniform("Sampler" + i, t.view(), t.sampler());
                }
                if (own != null) {
                    own.bind(pass, compiled instanceof FrontendRenderPipeline f && f.backendRenderPipeline() instanceof GlRenderPipeline g
                            ? g.program().getProgramId() : -1, temps);
                }
                pass.setIndexBuffer(up.indices(), up.indexType());
                pass.drawIndexed(up.indexCount(), 1, up.firstIndex(), up.baseVertex(), 0);
            } finally {
                try {
                    if (into == null) {
                        pass.close();   // a pass handed in stays open: its owner closes it
                    } else if (replacedDefaults) {
                        // a pass keeps its uniforms by name across pipelines (FrontendRenderPass re-applies them on
                        // setPipeline), and the replacement blocks are closed just below: the owner's next draws
                        // (translucent terrain binds no default uniforms of its own) would read a closed buffer
                        RenderSystem.bindDefaultUniforms(into);
                    }
                } finally {
                    for (GpuBuffer tb : temps) tb.close();
                    if (own != null) own.clear();
                    if (override) own.COLOR_MODULATOR.consume();   // 1.21.1 re-uploaded the default on every apply()
                    if (own != null) own.consumeBuiltIns();
                }
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
        // the blocks each 26.3 core shader declares (shaders/core/<name>.vsh/.fsh and their includes)
        private static final List<String> DT_PROJ = List.of("DynamicTransforms", "Projection");
        public static final Vanilla POSITION = new Vanilla("core/position", List.of("DynamicTransforms", "Projection", "Fog"));
        public static final Vanilla POSITION_COLOR = new Vanilla("core/position_color", DT_PROJ);
        public static final Vanilla POSITION_TEX = new Vanilla("core/position_tex", DT_PROJ, "Sampler0");
        public static final Vanilla POSITION_TEX_COLOR = new Vanilla("core/position_tex_color", DT_PROJ, "Sampler0");

        private final String path;
        private final List<String> blocks;
        final String[] samplers;
        private final Map<java.util.List<Object>, RenderPipeline> pipelines = new HashMap<>();

        private Vanilla(String path, List<String> blocks, String... samplers) {
            this.path = path;
            this.blocks = blocks;
            this.samplers = samplers;
        }

        RenderPipeline pipeline(VertexFormat format, PrimitiveTopology topology, Key key, GpuFormat colorFormat) {
            return pipelines.computeIfAbsent(java.util.List.of(key, format, topology, colorFormat), l -> {
                // 26.3: exactly the blocks and samplers the shader declares
                BindGroupLayout.Builder layout = BindGroupLayout.builder();
                for (String b : blocks) layout.withUniform(b, UniformType.UNIFORM_BUFFER);
                for (String s : samplers) layout.withUniform(s, UniformType.COMBINED_IMAGE_SAMPLER);
                RenderPipeline.Builder b = RenderPipeline.builder()
                        .withLocation("mv/" + path.replace('/', '_') + "_" + pipelines.size())
                        .withVertexShader(path).withFragmentShader(path)
                        .withBindGroupLayout(layout.build())
                        .withVertexBinding(0, format)
                        .withPrimitiveTopology(topology);
                return key.apply(b, colorFormat).build();
            });
        }
    }
}
