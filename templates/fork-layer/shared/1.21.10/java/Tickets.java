// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import net.minecraft.server.level.TicketType;

/** 1.21.4 removed TicketType.POST_TELEPORT (vanilla no longer holds the destination chunk after a teleport). A mod
 *  that added that ticket itself gets an identical one: 5 ticks, not saved, loads and simulates. 1.21.5 made TicketType
 *  a plain record (no value type, registered only for vanilla's own), so this one is a fresh unregistered instance; it is
 *  not persisted, which is the only reason a ticket type would need a registry id.
 *  1.21.9 turned the record into (timeout, flags, forceNaturalSpawning) with bit flags: loads (2) and simulates (4), no
 *  FLAG_PERSIST, which is what the 1.21.5 (not saved, LOADING_AND_SIMULATION) instance meant. */
public final class Tickets {
    public static final TicketType POST_TELEPORT = new TicketType(5L, TicketType.FLAG_LOADING | TicketType.FLAG_SIMULATION);

    private Tickets() {
    }
}
