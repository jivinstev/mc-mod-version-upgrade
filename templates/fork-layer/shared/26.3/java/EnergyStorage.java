// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
// MV-REQUIRES: IEnergyStorage
package @MVPKG@;

import net.minecraft.world.level.storage.ValueInput;
import net.minecraft.world.level.storage.ValueOutput;
import net.neoforged.neoforge.common.util.ValueIOSerializable;

/**
 * A plain int energy buffer for 26.3 fork code that kept the 1.21.x buffer type: a capacity, per-call receive and
 * extract limits, and the amount held (saved under "energy").
 */
public class EnergyStorage implements IEnergyStorage, ValueIOSerializable {
    protected int energy;
    protected int capacity;
    protected int maxReceive;
    protected int maxExtract;

    public EnergyStorage(int capacity) {
        this(capacity, capacity, capacity, 0);
    }

    public EnergyStorage(int capacity, int maxTransfer) {
        this(capacity, maxTransfer, maxTransfer, 0);
    }

    public EnergyStorage(int capacity, int maxReceive, int maxExtract) {
        this(capacity, maxReceive, maxExtract, 0);
    }

    public EnergyStorage(int capacity, int maxReceive, int maxExtract, int energy) {
        this.capacity = capacity;
        this.maxReceive = maxReceive;
        this.maxExtract = maxExtract;
        this.energy = energy < 0 ? 0 : Math.min(energy, Math.max(capacity, 0));
    }

    @Override
    public int receiveEnergy(int max, boolean simulate) {
        if (!canReceive() || max <= 0) return 0;
        int room = Math.max(capacity - energy, 0);
        int accepted = Math.min(Math.min(maxReceive, room), max);
        if (!simulate) energy += accepted;
        return accepted;
    }

    @Override
    public int extractEnergy(int max, boolean simulate) {
        if (!canExtract() || max <= 0) return 0;
        int given = Math.min(Math.min(maxExtract, Math.max(energy, 0)), max);
        if (!simulate) energy -= given;
        return given;
    }

    @Override
    public int getEnergyStored() {
        return energy;
    }

    @Override
    public int getMaxEnergyStored() {
        return capacity;
    }

    @Override
    public boolean canExtract() {
        return maxExtract > 0;
    }

    @Override
    public boolean canReceive() {
        return maxReceive > 0;
    }

    @Override
    public void serialize(ValueOutput output) {
        output.putInt("energy", energy);
    }

    @Override
    public void deserialize(ValueInput input) {
        energy = input.getIntOr("energy", 0);
    }
}
