// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import net.minecraft.resources.Identifier;

import java.util.Locale;

/** 1.21.4's AddClientReloadListenersEvent.addListener needs a unique id per listener; 1.21.1's
 *  RegisterClientReloadListenersEvent.registerReloadListener took none. The id is the listener's class name. */
public final class ReloadIds {
    private ReloadIds() {
    }

    public static Identifier of(Object listener) {
        String name = listener.getClass().getName().replace('$', '_').replace('.', '_').toLowerCase(Locale.ROOT);
        return Identifier.fromNamespaceAndPath("@MODID@", name);
    }
}
