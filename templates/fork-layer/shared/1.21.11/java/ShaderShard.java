// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
// MV-REQUIRES: LegacyGl
// MV-REQUIRES: LegacyRenderType
// MV-REQUIRES: ShaderInstance
package @MVPKG@;

import java.util.function.Supplier;

/**
 * 1.21.4's RenderStateShard.ShaderStateShard, gone in 1.21.5 (a RenderType's shader is part of its pipeline). Setting it
 * up makes the supplied shader the one {@link LegacyGl} draws with; the mod's own subclasses still override
 * setupRenderState to push uniforms first, exactly as before. Not final: mods subclass it anonymously.
 *
 * <p>1.21.11 copy: RenderStateShard is gone, so this extends {@link LegacyRenderType.Shard}.
 */
public class ShaderShard extends LegacyRenderType.Shard {
    private final Supplier<?> shader;

    public ShaderShard(Supplier<?> shader) {
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
