// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import net.minecraft.core.component.DataComponents;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.component.ItemAttributeModifiers;
import net.neoforged.bus.api.EventPriority;
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.fml.common.EventBusSubscriber;
import net.neoforged.neoforge.event.ItemAttributeModifierEvent;

import java.util.Map;
import java.util.Objects;
import java.util.concurrent.ConcurrentHashMap;

/**
 * CATALOG M68. NeoForge asks {@code Item.getDefaultAttributeModifiers(ItemStack)} only for a stack with NO
 * {@code ATTRIBUTE_MODIFIERS} component. On 1.21.1 an ArmorItem (or a TieredItem) had none, so a mod's override of that
 * method (level-based armour, a bonus per piece) ran; from 1.21.2 ArmorMaterial / Item.Properties set the component on
 * every such item and the override is dead -- silently, nothing fails. This applies it again, for this mod's items whose
 * class overrides the method, on a stack still carrying the item's own default component (a stack someone gave other
 * modifiers keeps them, as on 1.21.1). HIGHEST priority: other listeners still see and adjust the result.
 *
 * <p>An override that built on {@code super.getDefaultAttributeModifiers()} gets {@link #base} instead (the shared row
 * does that): the item's own component, which is what 1.21.1's super answered and what 1.21.2's super (EMPTY) no
 * longer does.
 */
@EventBusSubscriber(modid = "@MODID@")
public final class AttributeOverrides {
    private static final Map<Class<?>, Boolean> OVERRIDES = new ConcurrentHashMap<>();

    private AttributeOverrides() {
    }

    /** The item's own ATTRIBUTE_MODIFIERS component (EMPTY when it has none): 1.21.1's {@code super} defaults. */
    public static ItemAttributeModifiers base(Item item) {
        return item.components().getOrDefault(DataComponents.ATTRIBUTE_MODIFIERS, ItemAttributeModifiers.EMPTY);
    }

    @SubscribeEvent(priority = EventPriority.HIGHEST)
    public static void onItemAttributeModifiers(ItemAttributeModifierEvent event) {
        ItemStack stack = event.getItemStack();
        Item item = stack.getItem();
        ItemAttributeModifiers own = base(item);
        if (own.modifiers().isEmpty() || !overrides(item.getClass())
                || !"@MODID@".equals(BuiltInRegistries.ITEM.getKey(item).getNamespace())
                || !Objects.equals(stack.get(DataComponents.ATTRIBUTE_MODIFIERS), own)) {
            return;
        }
        ItemAttributeModifiers modifiers = item.getDefaultAttributeModifiers(stack);
        event.clearModifiers();
        for (ItemAttributeModifiers.Entry entry : modifiers.modifiers()) {
            event.addModifier(entry.attribute(), entry.modifier(), entry.slot());
        }
    }

    /** Whether a class below Item declares getDefaultAttributeModifiers(ItemStack). */
    private static boolean overrides(Class<?> cls) {
        return OVERRIDES.computeIfAbsent(cls, c -> {
            for (Class<?> k = c; k != null && k != Item.class; k = k.getSuperclass()) {
                try {
                    k.getDeclaredMethod("getDefaultAttributeModifiers", ItemStack.class);
                    return true;
                } catch (NoSuchMethodException ignored) {
                }
            }
            return false;
        });
    }
}
