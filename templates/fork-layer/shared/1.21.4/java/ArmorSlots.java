// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import net.minecraft.core.component.DataComponents;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.equipment.Equippable;

import javax.annotation.Nullable;

/**
 * 1.21.1's ArmorItem.getEquipmentSlot() / Equipable.getEquipmentSlot() (the item's own slot) is gone from 1.21.2: ArmorItem and
 * Equipable became the EQUIPPABLE component. The tempting replacement, NeoForge's IItemExtension.getEquipmentSlot(ItemStack),
 * COMPILES and is a different method: an override hook that returns null by default ("let vanilla decide"), so
 * EquipmentSlotGroup.bySlot(item.getEquipmentSlot(stack)) throws a NullPointerException -- found by a real client, from an
 * attribute-modifier listener JEI's tooltip search ran for every armour piece at startup. Read the component instead.
 *
 * <p>No rename row maps the call: a mod that declares its OWN getEquipmentSlot() (common on armour subclasses) must keep
 * calling it, and a regex cannot tell the two apart -- so a port calls this by hand.
 */
public final class ArmorSlots {
    private ArmorSlots() {
    }

    /** The slot the item's EQUIPPABLE component names, or null for an item that is not equippable. */
    @Nullable
    public static EquipmentSlot of(Item item) {
        Equippable equippable = item.components().get(DataComponents.EQUIPPABLE);
        return equippable != null ? equippable.slot() : null;
    }
}
