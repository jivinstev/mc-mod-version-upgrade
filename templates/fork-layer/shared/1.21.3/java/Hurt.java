// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.damagesource.DamageSource;
import net.minecraft.world.entity.Entity;

/**
 * 1.21.1's {@code boolean Entity.hurt(source, amount)}: since 1.21.2 {@code hurt} is a {@code final void} that forwards
 * to {@code hurtServer} on the server, so a caller that used its result ({@code if (target.hurt(...))}) gets
 * {@code hurtServer}'s answer from here -- whether the hit landed, the same thing 1.21.1 returned. On the client it is
 * false, as 1.21.1's living entities answered there. (CATALOG M66: never stand in a "did health drop?" comparison,
 * which miscounts an absorbed, blocked or cancelled hit.)
 */
public final class Hurt {
    private Hurt() {
    }

    public static boolean server(Entity target, DamageSource source, float amount) {
        return target.level() instanceof ServerLevel level && target.hurtServer(level, source, amount);
    }
}
