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
import com.mojang.blaze3d.systems.RenderSystem;
import com.mojang.blaze3d.textures.FilterMode;
import com.mojang.blaze3d.vertex.MeshData;
import com.mojang.blaze3d.vertex.VertexFormat;
import net.minecraft.client.Minecraft;
import net.minecraft.client.renderer.RenderPipelines;
import net.minecraft.client.renderer.rendertype.OutputTarget;
import net.minecraft.client.renderer.rendertype.RenderSetup;
import net.minecraft.client.renderer.rendertype.RenderType;
import net.minecraft.client.renderer.texture.TextureAtlas;
import net.minecraft.resources.Identifier;

import java.util.ArrayList;
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
            LegacyGl.setShaderTexture(2, Minecraft.getInstance().gameRenderer.lightTexture().getTextureView(),
                    RenderSystem.getSamplerCache().getClampToEdge(FilterMode.LINEAR));
        }
    };

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
    /** 1.21.4's POLYGON_OFFSET_LAYERING (glPolygonOffset(-1, -10)) is a depth bias on 1.21.5+. */
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

    /** The RenderSetup vanilla's batching reads buffer size and the crumbling / sort flags from; its pipeline is never used. */
    private static RenderSetup placeholder(int bufferSize, boolean affectsCrumbling, boolean sortOnUpload) {
        RenderSetup.RenderSetupBuilder b = RenderSetup.builder(RenderPipelines.SOLID_BLOCK).bufferSize(bufferSize);
        if (affectsCrumbling) b.affectsCrumbling();
        if (sortOnUpload) b.sortOnUpload();
        return b.createRenderSetup();
    }

    private final VertexFormat format;
    private final VertexFormat.Mode mode;
    private final State state;

    private LegacyRenderType(String name, VertexFormat format, VertexFormat.Mode mode, int bufferSize,
                             boolean affectsCrumbling, boolean sortOnUpload, State state) {
        super(name, placeholder(bufferSize, affectsCrumbling, sortOnUpload));
        this.format = format;
        this.mode = mode;
        this.state = state;
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

    @Override
    public void draw(MeshData mesh) {
        setupRenderState();
        try {
            LegacyGl.draw(mesh, state.shader.shader(), state.key, getRenderTarget());
        } finally {
            clearRenderState();
        }
    }

    /** The target this type draws into. Not a vanilla hook: only {@link #draw} reads it. */
    public RenderTarget getRenderTarget() {
        return state.output.getRenderTarget();
    }

    /** The pipeline this type's current shader and state compile to. */
    public RenderPipeline getRenderPipeline() {
        Object s = state.shader.shader();
        if (s instanceof ShaderInstance i) return i.pipeline(format, mode, state.key.withBlendIfUnset(i.blend()));
        if (s instanceof LegacyGl.Vanilla v) return v.pipeline(format, mode, state.key);
        throw new IllegalStateException(this + " has no shader to build a pipeline from");
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
    public VertexFormat.Mode mode() {
        return mode;
    }

    /** 1.21.4's RenderType.CompositeState, split into what is still set up and what is baked into the pipeline. */
    public static final class State {
        final ShaderShard shader;
        final OutputTarget output;
        final List<Shard> real;
        final LegacyGl.Key key;

        private State(ShaderShard shader, OutputTarget output, List<Shard> real, LegacyGl.Key key) {
            this.shader = shader;
            this.output = output;
            this.real = real;
            this.key = key;
        }

        public static final class Builder {
            private ShaderShard shader = new ShaderShard();
            private OutputTarget output = OutputTarget.MAIN_TARGET;
            private final List<Shard> real = new ArrayList<>();
            private LegacyGl.Key key = DEFAULTS;

            public Builder setShaderState(ShaderShard s) { this.shader = s; return this; }
            public Builder setTextureState(Shard s) { real.add(s); return this; }
            public Builder setLightmapState(Shard s) { real.add(s); return this; }
            public Builder setOverlayState(Shard s) { real.add(s); return this; }
            public Builder setTexturingState(Shard s) { real.add(s); return this; }
            public Builder setLineState(Shard s) { real.add(s); return this; }
            public Builder setOutputState(OutputTarget s) { this.output = s; return this; }
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
