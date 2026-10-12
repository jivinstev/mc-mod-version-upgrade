// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import com.mojang.serialization.Codec;
import net.minecraft.core.HolderLookup;
import net.minecraft.core.RegistryAccess;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.saveddata.SavedData;
import net.minecraft.world.level.saveddata.SavedDataType;
import net.minecraft.world.level.storage.DimensionDataStorage;

import java.util.function.BiFunction;
import java.util.function.Supplier;

/**
 * 1.21.1-1.21.4's {@code storage.computeIfAbsent(new SavedData.Factory<>(ctor, load, null), name)} on 1.21.5+, where a
 * store is a SavedDataType (id, constructor, Codec). The codec here is an adapter over the raw tag, so the mod's own
 * {@code save(CompoundTag, Provider)} and {@code load(CompoundTag, Provider)} keep doing the work and the file
 * (data/&lt;name&gt;.dat, same "data" compound) is the one 1.21.4 wrote: a defensive loader stays defensive (CATALOG §V29).
 */
public final class SavedDatas {
    private SavedDatas() {
    }

    public static <T extends SavedData> T computeIfAbsent(DimensionDataStorage storage, Supplier<T> constructor,
                                                          BiFunction<CompoundTag, HolderLookup.Provider, T> load, String name) {
        SavedDataType<T> type = new SavedDataType<>(name, level -> constructor.get(), level -> codec(level, load));
        return storage.computeIfAbsent(type);
    }

    private static <T extends SavedData> Codec<T> codec(ServerLevel level, BiFunction<CompoundTag, HolderLookup.Provider, T> load) {
        HolderLookup.Provider registries = level != null ? level.registryAccess() : RegistryAccess.EMPTY;
        return CompoundTag.CODEC.xmap(tag -> load.apply(tag, registries), data -> save(data, registries));
    }

    /** SavedData.save(CompoundTag, Provider) is the mod's own method now (it no longer overrides anything). */
    private static CompoundTag save(SavedData data, HolderLookup.Provider registries) {
        try {
            var m = data.getClass().getMethod("save", CompoundTag.class, HolderLookup.Provider.class);
            m.setAccessible(true);
            return (CompoundTag) m.invoke(data, new CompoundTag(), registries);
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException(data.getClass().getName() + " has no save(CompoundTag, HolderLookup.Provider)", e);
        }
    }
}
