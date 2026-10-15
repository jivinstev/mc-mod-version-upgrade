// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import net.minecraft.world.entity.Entity;

/**
 * 26.2's {@code Entity.getId()} throws {@code IllegalStateException("Tried to access entity ID before ID assignment")}
 * while the id is still 0: a client-side entity has 0 for its whole constructor (only {@code ServerLevel} hands out ids;
 * the add-entity packet calls {@code setId} afterwards), and a GUI preview entity that is never added to a level keeps 0
 * for good. There is no {@code hasId()}. 1.21 handed every entity a fresh counter id, which no id-keyed table held yet,
 * so a lookup simply missed. {@link #peek} gives that back: the id, or {@link #UNASSIGNED} (0, which neither
 * {@code ServerLevel} nor the network ever assigns), and the caller treats 0 as "no entry" -- a set lookup misses, and a
 * write keyed by it is skipped so no two unassigned entities ever share an entry (CATALOG §V104).
 */
public final class EntityIds {
    /** The id 26.2 reports as unassigned; never a real entity's id. */
    public static final int UNASSIGNED = 0;

    private EntityIds() {
    }

    /** The entity's id, or {@link #UNASSIGNED} when 26.2 has not given it one yet (never throws for that). */
    public static int peek(Entity entity) {
        try {
            return entity.getId();
        } catch (IllegalStateException unassigned) {
            return UNASSIGNED;
        }
    }

    /** Whether the entity has an id an id-keyed table can hold. */
    public static boolean assigned(Entity entity) {
        return peek(entity) != UNASSIGNED;
    }
}
