// OVERLAY-ONLY: shared fork-layer helper (1.21.4: AddClientReloadListenersEvent replaces RegisterClientReloadListenersEvent) from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import com.mojang.logging.LogUtils;
import net.minecraft.server.packs.resources.ResourceManager;
import net.minecraft.server.packs.resources.ResourceManagerReloadListener;
import net.neoforged.api.distmarker.Dist;
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.fml.ModList;
import net.neoforged.fml.common.EventBusSubscriber;
import net.minecraft.resources.ResourceLocation;
import net.neoforged.neoforge.client.event.AddClientReloadListenersEvent;
import org.slf4j.Logger;

import java.util.ArrayList;
import java.util.List;

/** Posts {@link RegisterShadersEvent} on the mod's mod bus at every client resource reload (1.21.1 did it via NeoForge). */
@EventBusSubscriber(modid = "@MODID@", bus = EventBusSubscriber.Bus.MOD, value = Dist.CLIENT)
public final class ShaderReloader implements ResourceManagerReloadListener {
    private static final Logger LOGGER = LogUtils.getLogger();
    private static final List<ShaderInstance> LOADED = new ArrayList<>();

    private ShaderReloader() {
    }

    @SubscribeEvent
    public static void onRegisterReloadListeners(AddClientReloadListenersEvent event) {
        event.addListener(ResourceLocation.fromNamespaceAndPath("@MODID@", "mv_shaders"), new ShaderReloader());
    }

    @Override
    public void onResourceManagerReload(ResourceManager resourceManager) {
        for (ShaderInstance old : LOADED) {
            try {
                old.close();
            } catch (RuntimeException e) {
                LOGGER.warn("@MODID@: closing shader {} failed", old.getName(), e);
            }
        }
        LOADED.clear();
        RegisterShadersEvent event = new RegisterShadersEvent(resourceManager);
        try {
            ModList.get().getModContainerById("@MODID@").orElseThrow().getEventBus().post(event);
        } catch (RuntimeException e) {
            LOGGER.error("@MODID@: shader registration failed", e);
        }
        LOADED.addAll(event.registered);
    }
}
