// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import net.minecraft.server.level.TicketType;

/** 1.21.4 removed TicketType.POST_TELEPORT (vanilla no longer holds the destination chunk after a teleport). A mod
 *  that added that ticket itself gets an identical one: Integer key (the entity id), level 1 is the caller's, 5 ticks. */
public final class Tickets {
    public static final TicketType<Integer> POST_TELEPORT = TicketType.create("@MODID@_post_teleport", Integer::compareTo, 5);

    private Tickets() {
    }
}
