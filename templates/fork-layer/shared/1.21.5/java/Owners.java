// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import net.minecraft.world.entity.OwnableEntity;

import java.util.UUID;

/** 1.21.5's OwnableEntity answers getOwnerReference() (a nullable EntityReference), not getOwnerUUID(). */
public final class Owners {
    private Owners() {
    }

    /** The owner's UUID, or null for an unowned entity: what getOwnerUUID() returned. */
    public static UUID uuid(OwnableEntity entity) {
        var ref = entity.getOwnerReference();
        return ref == null ? null : ref.getUUID();
    }
}
