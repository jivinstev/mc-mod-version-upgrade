package @MVPKG@;

import java.util.UUID;
import net.minecraft.core.UUIDUtil;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.nbt.IntArrayTag;

/** CompoundTag's UUID accessors, gone in 1.21.5: same int[4] storage, through UUIDUtil.CODEC. */
public final class Nbt {
    private Nbt() {}

    /** 1.21.1's getUUID threw on a missing key; this keeps that for a caller that did not check first. */
    public static UUID getUuid(CompoundTag tag, String key) {
        return tag.read(key, UUIDUtil.CODEC).orElseThrow(() -> new IllegalArgumentException("no UUID at " + key));
    }

    public static void putUuid(CompoundTag tag, String key, UUID value) {
        tag.store(key, UUIDUtil.CODEC, value);
    }

    /** 1.21.1: an IntArrayTag of exactly four ints. */
    public static boolean hasUuid(CompoundTag tag, String key) {
        return tag.get(key) instanceof IntArrayTag a && a.size() == 4;
    }
}
