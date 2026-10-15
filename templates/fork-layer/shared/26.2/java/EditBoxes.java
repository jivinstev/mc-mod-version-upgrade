// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import com.mojang.logging.LogUtils;
import net.minecraft.client.gui.components.EditBox;
import org.slf4j.Logger;

import java.lang.reflect.Field;
import java.util.function.Consumer;
import java.util.function.Predicate;

/**
 * 1.21's {@code EditBox.setFilter(Predicate<String>)}: NeoForge added it back through 26.1.2 and 26.2 has it no more
 * (EditBox keeps only {@code setResponder}). This stands in with a responder that puts back the last accepted value when an
 * edit produces text the filter rejects -- the text the user sees is what 1.21's filter let through. It takes the box's
 * responder slot: a responder the mod set BEFORE this is kept and called with every accepted value (read from EditBox's
 * private {@code responder} field; if that read fails, a warning is logged once and the earlier responder is lost), but a
 * responder the mod sets AFTER this replaces the filter (as {@code setResponder} replaced any responder in 1.21) -- give
 * such a box its responder first, or pass it to {@link #setFilter(EditBox, Predicate, Consumer)}.
 */
public final class EditBoxes {
    private static final Logger LOGGER = LogUtils.getLogger();
    private static Field responderField;
    private static boolean warned;

    private EditBoxes() {
    }

    public static void setFilter(EditBox box, Predicate<String> filter) {
        setFilter(box, filter, currentResponder(box));
    }

    @SuppressWarnings("unchecked")
    private static Consumer<String> currentResponder(EditBox box) {
        try {
            if (responderField == null) {
                Field f = EditBox.class.getDeclaredField("responder");
                f.setAccessible(true);
                responderField = f;
            }
            return (Consumer<String>) responderField.get(box);
        } catch (ReflectiveOperationException | RuntimeException e) {
            if (!warned) {
                warned = true;
                LOGGER.warn("EditBoxes.setFilter: cannot read EditBox.responder ({}); a responder set before the filter is replaced", e.toString());
            }
            return null;
        }
    }

    public static void setFilter(EditBox box, Predicate<String> filter, Consumer<String> responder) {
        box.setResponder(new Filtered(box, filter, responder));
    }

    /** The responder this installs; a later setFilter on the same box replaces the filter and keeps {@link #inner}. */
    private static final class Filtered implements Consumer<String> {
        final EditBox box;
        final Predicate<String> filter;
        final Consumer<String> inner;
        String last;
        /** True while {@link #accept} puts {@link #last} back: that setValue calls this responder again, and the
         *  chained responder already has that value, so the call is not passed on. */
        boolean restoring;

        Filtered(EditBox box, Predicate<String> filter, Consumer<String> inner) {
            this.box = box;
            this.filter = filter;
            this.inner = inner instanceof Filtered f ? f.inner : inner;
            this.last = filter.test(box.getValue()) ? box.getValue() : "";
        }

        @Override
        public void accept(String value) {
            if (restoring) {
                return;
            }
            if (filter.test(value)) {
                last = value;
                if (inner != null) inner.accept(value);
            } else if (!value.equals(last)) {
                restoring = true;
                try {
                    box.setValue(last);
                } finally {
                    restoring = false;
                }
            }
        }
    }
}
