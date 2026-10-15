// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
// MV-REQUIRES: LegacyGl
// MV-REQUIRES: ShaderInstance
// MV-REQUIRES: ShaderShard
package @MVPKG@;

import com.mojang.blaze3d.pipeline.BlendFunction;
import com.mojang.blaze3d.pipeline.RenderPipeline;
import com.mojang.blaze3d.pipeline.RenderTarget;
import com.mojang.blaze3d.platform.DepthTestFunction;
import com.mojang.blaze3d.platform.DestFactor;
import com.mojang.blaze3d.platform.SourceFactor;
import com.mojang.blaze3d.vertex.MeshData;
import com.mojang.blaze3d.vertex.VertexFormat;
import net.minecraft.client.renderer.RenderStateShard;
import net.minecraft.client.renderer.RenderType;

import java.util.ArrayList;
import java.util.List;
import java.util.function.UnaryOperator;

/**
 * 1.21.4's {@code RenderType.create(name, format, mode, size, crumbling, sort, CompositeState)} on 1.21.8, where a
 * RenderType's blend/depth/cull/write-mask state and its shader became one RenderPipeline. The builder keeps the
 * 1.21.4 shape: the shards 1.21.8 still has (texture, lightmap, overlay, layering, output, texturing, line) are
 * set up as before, and the ones it folded into the pipeline (shader, transparency, depth test, write mask, cull)
 * become the pipeline {@link LegacyGl} derives at draw time.
 *
 * <p>What 1.21.8 asks of a RenderType subclass is exactly three abstract methods: {@code draw(MeshData)},
 * {@code format()} and {@code mode()}. Everything else a buffer source needs -- {@code bufferSize()},
 * {@code affectsCrumbling()}, {@code sortOnUpload()}, {@code canConsolidateConsecutiveGeometry()}, {@code outline()}
 * (empty) -- has a working default, and its own {@code CompositeRenderType} (package-private, so not extendable) keeps
 * its pipeline in a private field and draws inside {@code draw}: set up the shards, write the DynamicTransforms
 * block, run a RenderPass with the default uniforms and the 12 sampler slots, tear the shards down. This class does
 * the same in {@code draw} through {@link LegacyGl#draw}. Nothing in 1.21.8 calls {@link #getRenderTarget()} or
 * {@link #getRenderPipeline()} (1.21.5 had hooks of those names that vanilla's batching read); they are kept as plain
 * accessors for the draw path and for a mod that wants the pipeline, and are deliberately NOT {@code @Override}.
 */
public class LegacyRenderType extends RenderType {
    /** A 1.21.4 shard 1.21.8 folded into the pipeline: it edits the draw state instead of setting GL state. */
    public static final class Modifier extends RenderStateShard {
        final UnaryOperator<LegacyGl.Key> edit;

        Modifier(String name, UnaryOperator<LegacyGl.Key> edit) {
            super(name, () -> {}, () -> {});
            this.edit = edit;
        }
    }

    private static Modifier blend(String name, BlendFunction f) {
        return new Modifier(name, k -> new LegacyGl.Key(f, k.depth(), k.depthWrite(), k.writeColor(), k.writeAlpha(), k.cull(), k.biasScale(), k.biasConstant()));
    }

    private static Modifier depth(String name, DepthTestFunction f) {
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
            new BlendFunction(SourceFactor.DST_COLOR, DestFactor.SRC_COLOR, SourceFactor.ONE, DestFactor.ZERO));
    public static final Modifier OVERLAY_TRANSPARENCY = blend("overlay_transparency", BlendFunction.OVERLAY);
    public static final Modifier TRANSLUCENT_TRANSPARENCY = blend("translucent_transparency", BlendFunction.TRANSLUCENT);
    public static final Modifier VIGNETTE_TRANSPARENCY = blend("vignette_transparency",
            new BlendFunction(SourceFactor.ZERO, DestFactor.ONE_MINUS_SRC_COLOR));
    public static final Modifier NO_DEPTH_TEST = depth("no_depth_test", DepthTestFunction.NO_DEPTH_TEST);
    public static final Modifier EQUAL_DEPTH_TEST = depth("equal_depth_test", DepthTestFunction.EQUAL_DEPTH_TEST);
    public static final Modifier LEQUAL_DEPTH_TEST = depth("lequal_depth_test", DepthTestFunction.LEQUAL_DEPTH_TEST);
    public static final Modifier GREATER_DEPTH_TEST = depth("greater_depth_test", DepthTestFunction.GREATER_DEPTH_TEST);
    public static final Modifier COLOR_DEPTH_WRITE = write("color_depth_write", true, true);
    public static final Modifier COLOR_WRITE = write("color_write", true, false);
    public static final Modifier DEPTH_WRITE = write("depth_write", false, true);
    public static final Modifier CULL = cull("cull", true);
    public static final Modifier NO_CULL = cull("no_cull", false);
    /** 1.21.4's POLYGON_OFFSET_LAYERING (glPolygonOffset(-1, -10)) is a depth bias on 1.21.5. */
    public static final Modifier POLYGON_OFFSET_LAYERING = new Modifier("polygon_offset_layering",
            k -> new LegacyGl.Key(k.blend(), k.depth(), k.depthWrite(), k.writeColor(), k.writeAlpha(), k.cull(), -1.0F, -10.0F));

    /** 1.21.4's defaults for a shard nobody set: no blending, LEQUAL depth test, colour and depth written, culled. */
    private static final LegacyGl.Key DEFAULTS = new LegacyGl.Key(null, DepthTestFunction.LEQUAL_DEPTH_TEST, true, true, true, true, 0, 0);

    public static State.Builder state() {
        return new State.Builder();
    }

    public static LegacyRenderType create(String name, VertexFormat format, VertexFormat.Mode mode, int bufferSize,
                                          boolean affectsCrumbling, boolean sortOnUpload, State state) {
        return new LegacyRenderType(name, format, mode, bufferSize, affectsCrumbling, sortOnUpload, state);
    }

    private final VertexFormat format;
    private final VertexFormat.Mode mode;
    private final State state;

    private LegacyRenderType(String name, VertexFormat format, VertexFormat.Mode mode, int bufferSize,
                             boolean affectsCrumbling, boolean sortOnUpload, State state) {
        super(name, bufferSize, affectsCrumbling, sortOnUpload,
                () -> state.real.forEach(RenderStateShard::setupRenderState),
                () -> state.real.forEach(RenderStateShard::clearRenderState));
        this.format = format;
        this.mode = mode;
        this.state = state;
    }

    @Override
    public void draw(MeshData mesh) {
        setupRenderState();
        try {
            state.shader.setupRenderState();   // the mod's override pushes its uniforms here, as in 1.21.4
            LegacyGl.draw(mesh, state.shader.shader(), state.key, getRenderTarget());
        } finally {
            state.shader.clearRenderState();
            clearRenderState();
        }
    }

    /** The target this type draws into (its OutputStateShard's). Not a 1.21.8 hook: only {@link #draw} reads it. */
    public RenderTarget getRenderTarget() {
        return state.output.getRenderTarget();
    }

    /** The pipeline this type's current shader and state compile to. Not a 1.21.8 hook: a plain accessor. */
    public RenderPipeline getRenderPipeline() {
        Object s = state.shader.shader();
        if (s instanceof ShaderInstance i) return i.pipeline(format, mode, state.key.withBlendIfUnset(i.blend()));
        if (s instanceof LegacyGl.Vanilla v) return v.pipeline(format, mode, state.key);
        throw new IllegalStateException(this + " has no shader to build a pipeline from");
    }

    @Override
    public VertexFormat format() {
        return format;
    }

    @Override
    public VertexFormat.Mode mode() {
        return mode;
    }

    /** 1.21.4's RenderType.CompositeState, split into what 1.21.8 still sets up and what it bakes into the pipeline. */
    public static final class State {
        final ShaderShard shader;
        final RenderStateShard.OutputStateShard output;
        final List<RenderStateShard> real;
        final LegacyGl.Key key;

        private State(ShaderShard shader, RenderStateShard.OutputStateShard output, List<RenderStateShard> real, LegacyGl.Key key) {
            this.shader = shader;
            this.output = output;
            this.real = real;
            this.key = key;
        }

        public static final class Builder {
            private ShaderShard shader = new ShaderShard();
            private RenderStateShard.OutputStateShard output = RenderStateShard.MAIN_TARGET;
            private final List<RenderStateShard> real = new ArrayList<>();
            private LegacyGl.Key key = DEFAULTS;

            public Builder setShaderState(ShaderShard s) { this.shader = s; return this; }
            public Builder setTextureState(RenderStateShard s) { real.add(s); return this; }
            public Builder setLightmapState(RenderStateShard s) { real.add(s); return this; }
            public Builder setOverlayState(RenderStateShard s) { real.add(s); return this; }
            public Builder setTexturingState(RenderStateShard s) { real.add(s); return this; }
            public Builder setLineState(RenderStateShard s) { real.add(s); return this; }
            public Builder setOutputState(RenderStateShard.OutputStateShard s) { this.output = s; return this; }
            public Builder setLayeringState(RenderStateShard s) { return add(s); }
            public Builder setTransparencyState(Modifier m) { return add(m); }
            public Builder setDepthTestState(Modifier m) { return add(m); }
            public Builder setWriteMaskState(Modifier m) { return add(m); }
            public Builder setCullState(Modifier m) { return add(m); }

            private Builder add(RenderStateShard s) {
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
