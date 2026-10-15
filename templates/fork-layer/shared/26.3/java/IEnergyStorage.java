// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import net.neoforged.neoforge.transfer.energy.EnergyHandler;
import net.neoforged.neoforge.transfer.transaction.Transaction;

/**
 * The int-based energy contract that 1.21.x fork code was written against, kept so that code compiles on 26.3,
 * where energy moved to the transactional transfer API ({@link EnergyHandler}). {@link #of} bridges the two.
 */
public interface IEnergyStorage {
    /** Adds up to {@code max} energy; returns how much was (or, when simulating, would be) accepted. */
    int receiveEnergy(int max, boolean simulate);

    /** Removes up to {@code max} energy; returns how much was (or, when simulating, would be) removed. */
    int extractEnergy(int max, boolean simulate);

    int getEnergyStored();

    int getMaxEnergyStored();

    boolean canExtract();

    boolean canReceive();

    /** Views a 26.3 transfer-API handler through this interface. */
    static IEnergyStorage of(EnergyHandler handler) {
        if (handler == null) {
            throw new NullPointerException("handler");
        }
        return new HandlerView(handler);
    }

    /** Adapter: every move is its own root transaction, kept only for a real (non-simulated) move. */
    final class HandlerView implements IEnergyStorage {
        private final EnergyHandler target;

        HandlerView(EnergyHandler target) {
            this.target = target;
        }

        private static int toInt(long value) {
            if (value > Integer.MAX_VALUE) return Integer.MAX_VALUE;
            if (value < Integer.MIN_VALUE) return Integer.MIN_VALUE;
            return (int) value;
        }

        @Override
        public int receiveEnergy(int max, boolean simulate) {
            if (max <= 0) return 0;
            try (Transaction tx = Transaction.openRoot()) {
                int moved = target.insert(max, tx);
                if (!simulate) tx.commit();
                return moved;
            }
        }

        @Override
        public int extractEnergy(int max, boolean simulate) {
            if (max <= 0) return 0;
            try (Transaction tx = Transaction.openRoot()) {
                int moved = target.extract(max, tx);
                if (!simulate) tx.commit();
                return moved;
            }
        }

        @Override
        public int getEnergyStored() {
            return toInt(target.getAmountAsLong());
        }

        @Override
        public int getMaxEnergyStored() {
            return toInt(target.getCapacityAsLong());
        }

        @Override
        public boolean canExtract() {
            return target.getCapacityAsLong() > 0;
        }

        @Override
        public boolean canReceive() {
            return target.getCapacityAsLong() > 0;
        }
    }
}
