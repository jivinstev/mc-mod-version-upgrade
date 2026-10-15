// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.ResourceKey;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.item.Item;
import net.minecraft.world.level.block.state.BlockBehaviour;

/**
 * 1.21.2 made every Block and Item carry its registry id in its Properties (CATALOG R25): `new Block(props)` and
 * `new Item(props)` throw "Block id not set" / "Item id not set" without it. A 1.21.1 mod builds its Properties where
 * the registry name is often not in scope (an Item subclass's own constructor), so the name is passed the other way:
 * the shared rows turn {@code REG.register("name", () -> new X(...))} into
 * {@code REG.register("name", () -> RegIds.enter("name") ? null : new X(...))} -- the supplier records the name it is
 * about to build -- and wrap every {@code new Item.Properties()} / {@code BlockBehaviour.Properties.of()} in
 * {@link #stamp}, which sets the id from that name. Overloading picks the item or block form by static type.
 *
 * <p>The name lasts only while the supplier that entered it is still running. The rows can only prefix the supplier
 * (no row finds its closing parenthesis), so there is no exit call; instead {@link #enter} records the supplier's
 * own frame and {@link #stamp} uses the name only while that frame is on the stack. Every Properties built inside
 * that supplier call (an item and a nested one, a block's constructor building its own) takes the name; a
 * registration that never entered one (a helper {@code register(String name, ...)}, whose name is a variable the
 * rows cannot read) finds none and fails with vanilla's "Item id not set" / "Block id not set" instead of silently
 * taking the PREVIOUS registration's id.
 *
 * <p>The bare {@code new Properties()} row cannot tell {@code Item.Properties} from an imported
 * {@code java.util.Properties}; the {@code java.util} overload hands that one back untouched.
 */
public final class RegIds {
    /** The entered name and the frame (class + method) of the supplier that entered it. */
    private record Entered(String name, String cls, String method) {
    }

    private static final ThreadLocal<Entered> CURRENT = new ThreadLocal<>();
    private static final StackWalker WALKER = StackWalker.getInstance();

    private RegIds() {
    }

    /** Called first in a registration supplier; always false, so {@code enter(n) ? null : new X()} builds X. */
    public static boolean enter(String name) {
        StackWalker.StackFrame caller = WALKER.walk(s -> s.skip(1).findFirst()).orElse(null);
        if (caller == null) {
            CURRENT.remove();
        } else {
            CURRENT.set(new Entered(name, caller.getClassName(), caller.getMethodName()));
        }
        return false;
    }

    /** The entered name while its supplier is still on the stack; otherwise none (and it is forgotten). */
    private static String current() {
        Entered e = CURRENT.get();
        if (e == null) {
            return null;
        }
        boolean live = WALKER.walk(s -> s.anyMatch(f -> f.getClassName().equals(e.cls()) && f.getMethodName().equals(e.method())));
        if (!live) {
            CURRENT.remove();
            return null;
        }
        return e.name();
    }

    /** A {@code java.util.Properties} the bare {@code new Properties()} row wrapped by mistake: unchanged. */
    public static java.util.Properties stamp(java.util.Properties props) {
        return props;
    }

    public static Item.Properties stamp(Item.Properties props) {
        String name = current();
        if (name == null) {
            return props;
        }
        ResourceLocation id = ResourceLocation.fromNamespaceAndPath("@MODID@", name);
        // a block item kept its block's translation key in 1.21.1; in 1.21.2 that is opt-in
        if (BuiltInRegistries.BLOCK.containsKey(id)) {
            props = props.useBlockDescriptionPrefix();
        }
        return props.setId(ResourceKey.create(Registries.ITEM, id));
    }

    public static BlockBehaviour.Properties stamp(BlockBehaviour.Properties props) {
        String name = current();
        return name == null ? props
                : props.setId(ResourceKey.create(Registries.BLOCK, ResourceLocation.fromNamespaceAndPath("@MODID@", name)));
    }
}
