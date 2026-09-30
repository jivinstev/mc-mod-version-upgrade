package com.forgeupgrade.smokeharness;

import net.neoforged.fml.common.Mod;

/**
 * Trivial host mod. The harness itself does nothing — it exists only so this
 * Gradle project is a valid NeoForge mod that can boot a dev client/server, into
 * which the REAL mods under test are injected on the runtime classpath via
 * {@code -Psmokejars} (see build.gradle). All the actual smoke-test driving lives
 * in {@link ClientBootSmokeTest}, which is gated on {@code -Pboottest}.
 */
@Mod(SmokeHarness.MODID)
public class SmokeHarness {
    public static final String MODID = "smokeharness";

    public SmokeHarness() {
    }
}
