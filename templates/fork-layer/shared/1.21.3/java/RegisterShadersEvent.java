// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;
// MV-REQUIRES: ShaderReloader  (posts this event at every client resource reload)

import net.minecraft.server.packs.resources.ResourceProvider;
import net.neoforged.bus.api.Event;
import net.neoforged.fml.event.IModBusEvent;

import java.util.ArrayList;
import java.util.List;
import java.util.function.Consumer;

/**
 * Stands in for 1.21.1's NeoForge RegisterShadersEvent, which handed mods a ResourceProvider and took finished
 * ShaderInstances. 1.21.2's event only takes ShaderProgram keys for the vanilla ShaderManager, so the mod's listeners
 * receive this instead: {@link ShaderReloader} posts it on the mod's mod bus at every client resource reload, after
 * closing the programs the previous reload registered.
 */
public class RegisterShadersEvent extends Event implements IModBusEvent {
    private final ResourceProvider provider;
    final List<ShaderInstance> registered = new ArrayList<>();

    public RegisterShadersEvent(ResourceProvider provider) {
        this.provider = provider;
    }

    public ResourceProvider getResourceProvider() {
        return provider;
    }

    public void registerShader(ShaderInstance shader, Consumer<ShaderInstance> onLoaded) {
        registered.add(shader);
        onLoaded.accept(shader);
    }
}
