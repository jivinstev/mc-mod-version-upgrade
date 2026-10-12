// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import net.minecraft.client.renderer.RenderStateShard;

import java.util.function.Supplier;

/**
 * 1.21.4's RenderStateShard.ShaderStateShard, gone in 1.21.5 (a RenderType's shader is part of its pipeline). Setting it
 * up makes the supplied shader the one {@link LegacyGl} draws with; the mod's own subclasses still override
 * setupRenderState to push uniforms first, exactly as before. Not final: mods subclass it anonymously.
 */
public class ShaderShard extends RenderStateShard {
    private final Supplier<?> shader;

    public ShaderShard(Supplier<?> shader) {
        super("mv_shader", () -> {}, () -> {});
        this.shader = shader;
    }

    public ShaderShard() {
        this(() -> null);
    }

    Object shader() {
        return shader.get();
    }

    @Override
    public void setupRenderState() {
        Object s = shader.get();
        if (s instanceof ShaderInstance i) LegacyGl.setShader(i);
        else if (s instanceof LegacyGl.Vanilla v) LegacyGl.setShader(v);
    }

    @Override
    public void clearRenderState() {
    }
}
