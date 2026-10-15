// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
// REQUIRES: geckolib
package @MVPKG@;

import software.bernie.geckolib.animation.AnimationController;
import software.bernie.geckolib.animation.object.PlayState;

/**
 * GeckoLib 5.4 (1.21.11) dropped AnimationController's State enum. 4.x-5.3's {@code getAnimationState() == STOPPED}
 * (4.x: {@code getControllerState()}) meant "the predicate stopped it, it never started, or a non-looping animation
 * finished": {@link PlayState#STOP} (also the state {@code reset()} leaves) or {@code hasAnimationFinished()}.
 *
 * <p>Read it BEFORE calling {@code reset()} in the same predicate: 4.x/5.3's {@code forceAnimationReset()} only flagged a
 * reload and left the state alone, while 5.4's {@code reset()} sets {@code PlayState.STOP}, so a check after a reset reads
 * "stopped" at once.
 */
public final class GeoControllers {
    private GeoControllers() {
    }

    public static boolean isStopped(AnimationController<?> controller) {
        return controller.getPlayState() == PlayState.STOP || controller.hasAnimationFinished();
    }
}
