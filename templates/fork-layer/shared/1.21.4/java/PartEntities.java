// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.world.entity.Entity;

/** NeoForge 21.1-21.3 kept a client-side id -> PartEntity map (ClientLevel.partEntities); 21.4 keeps only the list
 *  ClientLevel.dragonParts(), which holds every multipart entity's parts. Lookups by id scan it. */
public final class PartEntities {
    private PartEntities() {
    }

    public static Entity byId(ClientLevel level, int id) {
        for (Entity part : level.dragonParts()) {
            if (part.getId() == id) {
                return part;
            }
        }
        return null;
    }
}
