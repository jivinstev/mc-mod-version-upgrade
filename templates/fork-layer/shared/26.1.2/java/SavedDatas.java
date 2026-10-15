// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import com.mojang.logging.LogUtils;
import com.mojang.serialization.Codec;
import net.minecraft.SharedConstants;
import net.minecraft.core.HolderLookup;
import net.minecraft.core.RegistryAccess;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.saveddata.SavedData;
import net.minecraft.world.level.saveddata.SavedDataType;
import net.minecraft.resources.Identifier;
import net.minecraft.resources.ResourceKey;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.dimension.DimensionType;
import net.minecraft.world.level.storage.LevelResource;
import net.minecraft.world.level.storage.SavedDataStorage;

import org.slf4j.Logger;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.function.BiFunction;
import java.util.function.Supplier;

/**
 * 1.21.1-1.21.4's {@code storage.computeIfAbsent(new SavedData.Factory<>(ctor, load, null), name)} on 1.21.5+, where a
 * store is a SavedDataType (id, constructor, Codec). The codec here is an adapter over the raw tag, so the mod's own
 * {@code save(CompoundTag, Provider)} and {@code load(CompoundTag, Provider)} keep doing the work and the file
 * (data/&lt;name&gt;.dat, same "data" compound) is the one 1.21.4 wrote: a defensive loader stays defensive (CATALOG §V29).
 *
 * <p>26.1.2 copy: DimensionDataStorage is SavedDataStorage and a SavedDataType's id is an Identifier, which names the
 * file {@code data/<namespace>/<path>.dat}. The mod's name goes under the mod's own namespace (vanilla's own stores moved
 * to {@code data/minecraft/} the same way).
 *
 * <p>THE FILE MOVES, so this carries the old one across (CATALOG §V29, as tools/convert-saved-data.py's bridge does). 26.1's
 * world-upgrade file fix moves only vanilla's own stores and leaves a mod's {@code <name>.dat} where 1.21.x wrote it; read
 * as written, a 1.21.x world's mod data would silently reset. So when a store has no 26.x file yet (the type's
 * constructor factory runs), the 1.21.x file of the same dimension is read, loaded with the store's own load, marked dirty
 * (written at the new path on the next save) and logged once; the old file is left in place. The 1.21.x folder is NOT
 * 26.1's {@code DimensionType.getStorageFolder} for the three vanilla dimensions: 1.21.x kept the overworld's data in
 * {@code <world>/data}, the nether's in {@code DIM-1/data} and the end's in {@code DIM1/data} (26.1 moved those dimensions
 * to {@code dimensions/minecraft/<dim>/}); any other dimension was {@code dimensions/<ns>/<path>/data} then and now.
 */
public final class SavedDatas {
    private static final Logger LOGGER = LogUtils.getLogger();

    private SavedDatas() {
    }

    public static <T extends SavedData> T computeIfAbsent(SavedDataStorage storage, Supplier<T> constructor,
                                                          BiFunction<CompoundTag, HolderLookup.Provider, T> load, String name) {
        SavedDataType<T> type = new SavedDataType<>(id(name), level -> create(storage, constructor, load, name, level),
                level -> codec(level, load));
        return storage.computeIfAbsent(type);
    }

    /** The 1.21.x store name as an Identifier in the mod's namespace (a character an Identifier path refuses becomes _). */
    static Identifier id(String name) {
        return Identifier.fromNamespaceAndPath("@MODID@", name.toLowerCase(java.util.Locale.ROOT).replaceAll("[^a-z0-9/._-]", "_"));
    }

    /** Where 1.21.x kept a dimension's saved data (its DimensionType.getStorageFolder, plus "data"). */
    static Path legacyDataFolder(ResourceKey<Level> dimension, Path worldRoot) {
        if (Level.OVERWORLD.equals(dimension)) return worldRoot.resolve("data");
        if (Level.NETHER.equals(dimension)) return worldRoot.resolve("DIM-1").resolve("data");
        if (Level.END.equals(dimension)) return worldRoot.resolve("DIM1").resolve("data");
        return DimensionType.getStorageFolder(dimension, worldRoot).resolve("data");
    }

    /** A fresh store, or the one a 1.21.x world saved at &lt;dimension&gt;/data/&lt;name&gt;.dat. */
    private static <T extends SavedData> T create(SavedDataStorage storage, Supplier<T> constructor,
                                                  BiFunction<CompoundTag, HolderLookup.Provider, T> load, String name,
                                                  ServerLevel level) {
        if (level != null && level.getServer() != null) {
            Path legacy = legacyDataFolder(level.dimension(), level.getServer().getWorldPath(LevelResource.ROOT))
                    .resolve(name + ".dat");
            if (Files.isRegularFile(legacy)) {
                try {
                    CompoundTag root = storage.readTagFromDisk(legacy, null,
                            SharedConstants.getCurrentVersion().dataVersion().version());
                    T data = load.apply(root.getCompoundOrEmpty("data"), level.registryAccess());
                    data.setDirty();
                    LOGGER.info("[mv] read 1.21.x saved data {} for {}; it is saved as {} from now on", legacy, name, id(name));
                    return data;
                } catch (IOException | RuntimeException e) {
                    LOGGER.error("[mv] could not read 1.21.x saved data {}; starting it empty", legacy, e);
                }
            }
        }
        return constructor.get();
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
