// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
// MV-REQUIRES: LegacyGl
// MV-REQUIRES: ShaderInstance
// MV-REQUIRES: ShaderShard
package @MVPKG@;

import com.mojang.renderpearl.api.pipeline.PrimitiveTopology;
import com.mojang.renderpearl.api.pipeline.BlendFunction;
import com.mojang.renderpearl.api.pipeline.RenderPipeline;
import com.mojang.blaze3d.pipeline.RenderTarget;
import com.mojang.renderpearl.api.pipeline.BlendFactor;
import com.mojang.renderpearl.api.pipeline.CompareOp;
import com.mojang.blaze3d.systems.RenderSystem;
import com.mojang.blaze3d.systems.ScissorState;
import com.mojang.logging.LogUtils;
import com.mojang.renderpearl.api.textures.FilterMode;
import com.mojang.blaze3d.vertex.MeshData;
import com.mojang.renderpearl.api.vertex.VertexFormat;
import net.minecraft.client.Minecraft;
import net.minecraft.client.renderer.RenderPipelines;
import net.minecraft.client.renderer.rendertype.PreparedRenderType;
import net.minecraft.client.renderer.rendertype.RenderSetup;
import net.minecraft.client.renderer.rendertype.RenderType;
import net.minecraft.client.renderer.texture.TextureAtlas;
import net.minecraft.resources.Identifier;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.Set;
import java.util.List;
import java.util.function.UnaryOperator;

/**
 * 1.21.4's {@code RenderType.create(name, format, mode, size, crumbling, sort, CompositeState)} on 1.21.11, where a
 * RenderType is a name plus a {@code RenderSetup} around one fixed RenderPipeline and RenderStateShard no longer exists.
 * The builder keeps the 1.21.4 shape: the blend/depth/cull/write-mask/layering state becomes the pipeline {@link LegacyGl}
 * derives at draw time, the shader is a {@link ShaderShard}, and what is left (texture, lightmap, overlay) are the small
 * {@link Shard}s below, which bind {@link LegacyGl}'s texture slots.
 *
 * <p>1.21.11 made RenderType's only constructor private and its pipeline immutable; the fork's access transformer
 * opens {@code RenderType(String, RenderSetup)}, and this subclass hands it a placeholder {@code RenderSetup} (only its
 * buffer size and crumbling / sort flags are read by vanilla's batching) and overrides {@code draw}, {@code format},
 * {@code mode} and {@code pipeline} with the legacy ones, as the 1.21.10 copy did.
 *
 * <p>26.1.2 copy: the depth-test shards are {@code CompareOp}s ({@link LegacyGl.Key}; null = no depth test), and
 * 1.21.4's {@code LIGHTMAP} binds {@code GameRenderer.lightmap()} (26.1 removed {@code lightTexture()}).
 *
 * <p>26.2 copy: {@code RenderType.draw(MeshData)} and {@code mode()} are gone. A RenderType is now drawn from the frame's
 * one {@code StagedVertexBuffer}: vanilla's feature renderers call {@code prepare()} when geometry is RECORDED and
 * {@code PreparedRenderType.drawFromBuffer} when the frame executes. So this type keeps {@link #draw} as its own method
 * (what {@link MultiBufferSource} and the mod's direct draws call -- the full {@link LegacyGl} draw, the shader's own
 * uniform blocks included) and overrides {@link #prepare()} for the vanilla path: the legacy pipeline and the textures
 * the shards and the shader bind, snapshotted then. That path binds only vanilla's blocks (Projection, Fog, Globals,
 * Lighting, DynamicTransforms), so a shader with uniform blocks of its own drawn through it is logged once (and fails
 * GL validation in a dev client): draw such a type through {@link MultiBufferSource} or {@link LegacyGl}.
 * Reverse-Z (see {@link LegacyGl}): the LEQUAL/GREATER shards are {@code GREATER_THAN_OR_EQUAL}/{@code LESS_THAN} and
 * POLYGON_OFFSET_LAYERING's bias is {@code (1, 10)}, as vanilla's 26.2 pipelines.
 *
 * <p>26.3 copy (CATALOG §V101): {@code OutputTarget} and {@code RenderSetupBuilder.setOutputTarget} are gone (a feature
 * renderer draws into the pass the frame opened), and {@code PreparedRenderType} takes the type's name and an optional
 * OIT pipeline set ({@code null}: this type has none). So the state's output is a {@code RenderTarget} -- the main one
 * unless {@code setOutputState(target)} named another -- which only this type's own {@link #draw} reads.
 */
public class LegacyRenderType extends RenderType {
    /** What 1.21.4's RenderStateShard was to this layer: something set up before a draw and cleared after it. */
    public abstract static class Shard {
        public abstract void setupRenderState();

        public void clearRenderState() {
        }
    }

    /** A 1.21.4 shard 1.21.11 folded into the pipeline: it edits the draw state instead of setting GL state. */
    public static final class Modifier extends Shard {
        final UnaryOperator<LegacyGl.Key> edit;

        Modifier(String name, UnaryOperator<LegacyGl.Key> edit) {
            this.edit = edit;
        }

        @Override
        public void setupRenderState() {
        }
    }

    /** 1.21.4's TextureStateShard: names the texture bound to sampler slot 0 (the texture's own sampler decides filtering). */
    public static class TextureStateShard extends Shard {
        private final Identifier texture;

        public TextureStateShard(Identifier texture, boolean mipmap) {
            this.texture = texture;
        }

        @Override
        public void setupRenderState() {
            LegacyGl.setShaderTexture(0, texture);
        }
    }

    /** 1.21.4's {@code RenderType.BLOCK_SHEET_MIPPED}. */
    public static final TextureStateShard BLOCK_SHEET_MIPPED = new TextureStateShard(TextureAtlas.LOCATION_BLOCKS, true);

    /** 1.21.4's {@code RenderType.OVERLAY}: the entity overlay texture in sampler slot 1. */
    public static final Shard OVERLAY = new Shard() {
        @Override
        public void setupRenderState() {
            LegacyGl.setShaderTexture(1, Minecraft.getInstance().gameRenderer.overlayTexture().getTextureView(),
                    RenderSystem.getSamplerCache().getClampToEdge(FilterMode.LINEAR));
        }
    };

    /** 1.21.4's {@code RenderType.LIGHTMAP}: the light texture in sampler slot 2. */
    public static final Shard LIGHTMAP = new Shard() {
        @Override
        public void setupRenderState() {
            LegacyGl.setShaderTexture(2, Minecraft.getInstance().gameRenderer.lightmap(),
                    RenderSystem.getSamplerCache().getClampToEdge(FilterMode.LINEAR));
        }
    };

    private static Modifier blend(String name, BlendFunction f) {
        return new Modifier(name, k -> new LegacyGl.Key(f, k.depth(), k.depthWrite(), k.writeColor(), k.writeAlpha(), k.cull(), k.biasScale(), k.biasConstant()));
    }

    private static Modifier depth(String name, CompareOp f) {
        return new Modifier(name, k -> new LegacyGl.Key(k.blend(), f, k.depthWrite(), k.writeColor(), k.writeAlpha(), k.cull(), k.biasScale(), k.biasConstant()));
    }

    private static Modifier write(String name, boolean color, boolean depth) {
        return new Modifier(name, k -> new LegacyGl.Key(k.blend(), k.depth(), depth, color, color, k.cull(), k.biasScale(), k.biasConstant()));
    }

    private static Modifier cull(String name, boolean cull) {
        return new Modifier(name, k -> new LegacyGl.Key(k.blend(), k.depth(), k.depthWrite(), k.writeColor(), k.writeAlpha(), cull, k.biasScale(), k.biasConstant()));
    }

    // 1.21.4's RenderStateShard transparency constants, as the blend functions they set
    public static final Modifier NO_TRANSPARENCY = blend("no_transparency", null);
    public static final Modifier ADDITIVE_TRANSPARENCY = blend("additive_transparency", BlendFunction.ADDITIVE);
    public static final Modifier LIGHTNING_TRANSPARENCY = blend("lightning_transparency", BlendFunction.LIGHTNING);
    public static final Modifier GLINT_TRANSPARENCY = blend("glint_transparency", BlendFunction.GLINT);
    public static final Modifier CRUMBLING_TRANSPARENCY = blend("crumbling_transparency",
            new BlendFunction(BlendFactor.DST_COLOR, BlendFactor.SRC_COLOR, BlendFactor.ONE, BlendFactor.ZERO));
    public static final Modifier OVERLAY_TRANSPARENCY = blend("overlay_transparency", BlendFunction.OVERLAY);
    public static final Modifier TRANSLUCENT_TRANSPARENCY = blend("translucent_transparency", BlendFunction.TRANSLUCENT);
    public static final Modifier VIGNETTE_TRANSPARENCY = blend("vignette_transparency",
            new BlendFunction(BlendFactor.ZERO, BlendFactor.ONE_MINUS_SRC_COLOR));
    public static final Modifier NO_DEPTH_TEST = depth("no_depth_test", null);
    public static final Modifier EQUAL_DEPTH_TEST = depth("equal_depth_test", CompareOp.EQUAL);
    // 26.2 is reverse-Z: 1.21's "nearer or equal" (GL_LEQUAL) is GREATER_THAN_OR_EQUAL, and its GL_GREATER is LESS_THAN
    public static final Modifier LEQUAL_DEPTH_TEST = depth("lequal_depth_test", CompareOp.GREATER_THAN_OR_EQUAL);
    public static final Modifier GREATER_DEPTH_TEST = depth("greater_depth_test", CompareOp.LESS_THAN);
    public static final Modifier COLOR_DEPTH_WRITE = write("color_depth_write", true, true);
    public static final Modifier COLOR_WRITE = write("color_write", true, false);
    public static final Modifier DEPTH_WRITE = write("depth_write", false, true);
    public static final Modifier CULL = cull("cull", true);
    public static final Modifier NO_CULL = cull("no_cull", false);
    /** 1.21.4's POLYGON_OFFSET_LAYERING (glPolygonOffset(-1, -10)) is a depth bias on 1.21.5+; on reverse-Z 26.2 its sign
     *  flips, as vanilla's own layering pipelines (DepthStencilState(..., 1.0F, 10.0F)). */
    public static final Modifier POLYGON_OFFSET_LAYERING = new Modifier("polygon_offset_layering",
            k -> new LegacyGl.Key(k.blend(), k.depth(), k.depthWrite(), k.writeColor(), k.writeAlpha(), k.cull(), 1.0F, 10.0F));

    /** 1.21.4's defaults for a shard nobody set: no blending, LEQUAL depth test (reverse-Z: GREATER_THAN_OR_EQUAL), colour
     *  and depth written, culled. */
    private static final LegacyGl.Key DEFAULTS = new LegacyGl.Key(null, CompareOp.GREATER_THAN_OR_EQUAL, true, true, true, true, 0, 0);

    public static State.Builder state() {
        return new State.Builder();
    }

    /** {@code bufferSize} is kept for the 1.21.4 shape and ignored: 26.2's RenderSetup has no buffer size (geometry is
     *  staged in one frame-wide buffer). */
    public static LegacyRenderType create(String name, VertexFormat format, PrimitiveTopology mode, int bufferSize,
                                          boolean affectsCrumbling, boolean sortOnUpload, State state) {
        return new LegacyRenderType(name, format, mode, affectsCrumbling, sortOnUpload, state);
    }

    /** The RenderSetup vanilla's batching reads the crumbling / sort flags and the output target from; its pipeline is
     *  never drawn with ({@link #pipeline()} and {@link #prepare()} answer with the legacy one). */
    private static RenderSetup placeholder(boolean affectsCrumbling, boolean sortOnUpload) {
        RenderSetup.RenderSetupBuilder b = RenderSetup.builder(RenderPipelines.SOLID_BLOCK);
        if (affectsCrumbling) b.affectsCrumbling();
        if (sortOnUpload) b.sortOnUpload();
        return b.createRenderSetup();
    }

    private static final org.slf4j.Logger LOGGER = LogUtils.getLogger();
    private static final Set<String> WARNED = new HashSet<>();

    private final VertexFormat format;
    private final PrimitiveTopology mode;
    private final State state;

    private LegacyRenderType(String name, VertexFormat format, PrimitiveTopology mode,
                             boolean affectsCrumbling, boolean sortOnUpload, State state) {
        super(name, placeholder(affectsCrumbling, sortOnUpload));
        this.format = format;
        this.mode = mode;
        this.state = state;
    }

    /**
     * Another mod's copy of this class (CATALOG V117). Every mod built with the fork layer carries its own
     * LegacyRenderType, relocated into its own package, so a render type a DEPENDENT made and handed to a library's helper
     * ({@code MultiBufferSource}, {@code LegacyGl.setupRenderState}, a render queue) is no {@code instanceof} of the
     * library's copy: routed as a vanilla type its shards and shader are never set up, and vanilla's draw binds none of
     * its shader's own uniforms ("Missing uniform MV_LocalUvMin"). A copy is told by a superclass named LegacyRenderType
     * outside this package and driven through its own public methods, looked up once per class; any other type is none.
     */
    public static final class Copies {
        private static final java.lang.reflect.Method[] NONE = new java.lang.reflect.Method[0];
        private static final Object DONE = new Object();
        private static final ClassValue<java.lang.reflect.Method[]> METHODS = new ClassValue<>() {
            @Override
            protected java.lang.reflect.Method[] computeValue(Class<?> type) {
                for (Class<?> c = type; c != null && c != Object.class; c = c.getSuperclass()) {
                    if (c == LegacyRenderType.class) {
                        return NONE;                    // this mod's own: its callers use it directly
                    }
                    if (c.getSimpleName().equals("LegacyRenderType") && RenderType.class.isAssignableFrom(c)) {
                        return new java.lang.reflect.Method[] {find(c, "draw", MeshData.class), find(c, "setupRenderState"),
                                find(c, "clearRenderState"),
                                find(c, "draw", MeshData.class, com.mojang.renderpearl.api.commands.RenderPass.class),
                                find(c, "drawsIntoMainTarget")};
                    }
                }
                return NONE;
            }
        };

        private Copies() {
        }

        /** The copy's own public instance method (declared by the copy, never vanilla's), or null. */
        private static java.lang.reflect.Method find(Class<?> c, String name, Class<?>... args) {
            try {
                java.lang.reflect.Method m = c.getMethod(name, args);
                if (java.lang.reflect.Modifier.isStatic(m.getModifiers()) || m.getDeclaringClass().getName().startsWith("net.minecraft.")) {
                    return null;
                }
                m.trySetAccessible();
                return m;
            } catch (NoSuchMethodException | SecurityException e) {
                return null;
            }
        }

        /** Calls method {@code which} of another mod's copy -> its result (DONE for a void one), or null when {@code type}
         *  is no copy or its copy has no such method. */
        private static Object call(RenderType type, int which, Object... args) {
            java.lang.reflect.Method[] ms = type == null ? NONE : METHODS.get(type.getClass());
            java.lang.reflect.Method m = which < ms.length ? ms[which] : null;
            if (m == null) {
                return null;
            }
            try {
                Object r = m.invoke(type, args);
                return m.getReturnType() == void.class ? DONE : r;
            } catch (java.lang.reflect.InvocationTargetException e) {
                if (e.getCause() instanceof RuntimeException r) throw r;
                if (e.getCause() instanceof Error err) throw err;
                throw new IllegalStateException(e.getCause());
            } catch (IllegalAccessException e) {
                throw new IllegalStateException("cannot call " + m + " of another mod's LegacyRenderType", e);
            }
        }

        /** Whether {@code type} is another mod's copy of this class. */
        public static boolean isCopy(RenderType type) {
            return type != null && METHODS.get(type.getClass()).length > 0;
        }

        /** {@code draw(mesh)} of another mod's copy (it closes the mesh) -> true; false when {@code type} is none. */
        public static boolean draw(RenderType type, MeshData mesh) {
            return call(type, 0, mesh) != null;
        }

        /** {@code setupRenderState()} of another mod's copy -> true; false when {@code type} is none. */
        public static boolean setupRenderState(RenderType type) {
            return call(type, 1) != null;
        }

        /** {@code clearRenderState()} of another mod's copy -> true; false when {@code type} is none. */
        public static boolean clearRenderState(RenderType type) {
            return call(type, 2) != null;
        }

        /** {@code draw(mesh, into)} of another mod's copy (26.3: into an open pass, null: a pass of its own); false when
         *  {@code type} is no copy or has no such method (the mesh untouched). */
        public static boolean draw(RenderType type, MeshData mesh, com.mojang.renderpearl.api.commands.RenderPass into) {
            return into == null ? draw(type, mesh) : call(type, 3, mesh, into) != null;
        }

        /** Whether another mod's copy draws into an output target of its own (not the main one); false for every other type. */
        public static boolean ownTarget(RenderType type) {
            Object main = call(type, 4);
            return main instanceof Boolean b && !b;
        }
    }

    /** 1.21.4's RenderType.setupRenderState: the texture / lightmap / overlay shards, then the shader and the
     *  transparency / depth / write-mask / cull / layering state it folded into the pipeline, loaded into {@link LegacyGl}
     *  so a following {@code LegacyGl.drawWithShader} draws with them (see {@link LegacyGl#load}), then the shader shard's
     *  own setup (the mod's override pushes its uniforms there, as in 1.21.4). */
    public void setupRenderState() {
        state.real.forEach(Shard::setupRenderState);
        LegacyGl.load(this, state.key, state.shader.shader(), getRenderTarget());
        try {
            state.shader.setupRenderState();
        } catch (RuntimeException | Error e) {   // the mod's shard override threw: nothing of this type stays loaded
            LegacyGl.restore(this);
            throw e;
        }
    }

    public void clearRenderState() {
        state.shader.clearRenderState();
        LegacyGl.restore(this);
        state.real.forEach(Shard::clearRenderState);
    }

    /** 1.21's {@code RenderType.draw(MeshData)} (26.2 removed it): the type's shards and shader, one LegacyGl draw. */
    public void draw(MeshData mesh) {
        setupRenderState();
        try {
            LegacyGl.draw(mesh, state.shader.shader(), state.key, getRenderTarget());
        } finally {
            clearRenderState();
        }
    }

    /**
     * 26.3: {@link #draw(MeshData)} INTO a render pass that is already open (an in-pass {@code RenderLevelStageEvent}'s
     * {@code getRenderPass()}, or one vanilla opened on an offscreen texture), which stays open. The pass is the caller's
     * choice: the type's own output target ({@link #getRenderTarget}) is not consulted.
     */
    public void draw(MeshData mesh, com.mojang.renderpearl.api.commands.RenderPass into) {
        setupRenderState();
        try {
            LegacyGl.draw(mesh, state.shader.shader(), state.key, null, null, into, LegacyGl.passColorFormat(into));
        } finally {
            clearRenderState();
        }
    }

    /** Whether this type draws into the main target (no {@code setOutputState} target of its own). */
    public boolean drawsIntoMainTarget() {
        return state.output == null;
    }

    /**
     * 26.2's vanilla draw path (a feature renderer recording this type's geometry): the legacy pipeline, the
     * DynamicTransforms and the textures this type's shards and shader bind, taken now -- as vanilla's own
     * {@code RenderType.prepare()} snapshots its state when geometry is recorded, not when the frame draws.
     */
    @Override
    public PreparedRenderType prepare() {
        setupRenderState();
        try {
            Object s = state.shader.shader();
            RenderPipeline pipeline = LegacyGl.pipelineFor(s, format, mode, state.key);
            if (pipeline == null) {
                throw new IllegalStateException(this + " has no shader to build a pipeline from");
            }
            if (s instanceof ShaderInstance i && i.hasOwnBlocks() && WARNED.add(name)) {
                LOGGER.warn("[mv] {} is drawn through 26.2's feature renderers, which bind only vanilla's uniform blocks; "
                        + "its shader's own uniforms are not bound there (draw it through MultiBufferSource or LegacyGl)", name);
            }
            List<PreparedRenderType.Texture> textures = new ArrayList<>(LegacyGl.textures(s));
            for (int i = 0; i < 12; i++) {
                String sampler = "Sampler" + i;
                LegacyGl.Slot slot = LegacyGl.slotTexture(sampler);
                if (slot != null && textures.stream().noneMatch(t -> t.name().equals(sampler))) {
                    textures.add(new PreparedRenderType.Texture(sampler, slot.view(), slot.sampler()));
                }
            }
            return new PreparedRenderType(name, pipeline, null, LegacyGl.transforms(s instanceof ShaderInstance i ? i : null),
                    new ScissorState(RenderSystem.getScissorStateForRenderTypeDraws()), List.copyOf(textures));
        } finally {
            clearRenderState();
        }
    }

    @Override
    public boolean hasBlending() {
        return state.key.blend() != null;
    }

    /** The target this type draws into. Not a vanilla hook: only {@link #draw} reads it. */
    public RenderTarget getRenderTarget() {
        return state.output != null ? state.output : Minecraft.getInstance().gameRenderer.mainRenderTarget();
    }

    /** The pipeline this type's current shader and state compile to. */
    public RenderPipeline getRenderPipeline() {
        Object s = state.shader.shader();
        RenderPipeline p = LegacyGl.pipelineFor(s, format, mode, state.key);
        if (p == null) throw new IllegalStateException(this + " has no shader to build a pipeline from");
        return p;
    }

    /** {@code pipeline()} is what the buffer source batches on; it answers with the pipeline {@link #getRenderPipeline()}
     *  derives, which is what {@link #draw} draws with. */
    @Override
    public RenderPipeline pipeline() {
        return getRenderPipeline();
    }

    @Override
    public VertexFormat format() {
        return format;
    }

    @Override
    public PrimitiveTopology primitiveTopology() {
        return mode;
    }

    /** 1.21's {@code RenderType.mode()} (26.2: {@code primitiveTopology()}), for code written against this type. */
    public PrimitiveTopology mode() {
        return mode;
    }

    /** 1.21.4's RenderType.CompositeState, split into what is still set up and what is baked into the pipeline. */
    public static final class State {
        final ShaderShard shader;
        final RenderTarget output;   // null: the main target
        final List<Shard> real;
        final LegacyGl.Key key;

        private State(ShaderShard shader, RenderTarget output, List<Shard> real, LegacyGl.Key key) {
            this.shader = shader;
            this.output = output;
            this.real = real;
            this.key = key;
        }

        public static final class Builder {
            private ShaderShard shader = new ShaderShard();
            private RenderTarget output;
            private final List<Shard> real = new ArrayList<>();
            private LegacyGl.Key key = DEFAULTS;

            public Builder setShaderState(ShaderShard s) { this.shader = s; return this; }
            public Builder setTextureState(Shard s) { real.add(s); return this; }
            public Builder setLightmapState(Shard s) { real.add(s); return this; }
            public Builder setOverlayState(Shard s) { real.add(s); return this; }
            public Builder setTexturingState(Shard s) { real.add(s); return this; }
            public Builder setLineState(Shard s) { real.add(s); return this; }
            /** 26.3 has no OutputTarget: a type's own draw goes into this target (default: the main one). */
            public Builder setOutputState(RenderTarget s) { this.output = s; return this; }
            public Builder setLayeringState(Shard s) { return add(s); }
            public Builder setTransparencyState(Modifier m) { return add(m); }
            public Builder setDepthTestState(Modifier m) { return add(m); }
            public Builder setWriteMaskState(Modifier m) { return add(m); }
            public Builder setCullState(Modifier m) { return add(m); }

            private Builder add(Shard s) {
                if (s instanceof Modifier m) key = m.edit.apply(key); else real.add(s);
                return this;
            }

            /** The outline flag selected a separate outline render type in 1.21.4; this compat layer draws none. */
            public State createCompositeState(boolean outline) {
                return new State(shader, output, List.copyOf(real), key);
            }
        }
    }
}
