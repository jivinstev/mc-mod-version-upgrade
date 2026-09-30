package com.forgeupgrade.smokeharness;

import net.minecraft.gametest.framework.GameTest;
import net.minecraft.gametest.framework.GameTestHelper;
import net.neoforged.neoforge.gametest.GameTestHolder;
import net.neoforged.neoforge.gametest.PrefixGameTestTemplate;

/**
 * Trivial always-pass GameTest. Its whole purpose is to give {@code runGameTestServer} at least one
 * test so the dedicated server boots ALL mods to the ready state and then exits cleanly with
 * "All required tests passed" — instead of aborting with "No test functions were given!".
 *
 * The real value is the boot itself: the target jars injected via -Psmokejars are loaded + fully
 * registered (RegisterEvent, common setup, spawn placements, attributes) BEFORE this test runs, so
 * any §R load-time crash (frozen registry, unbound holder, config-at-registration, missing attribute)
 * fails the boot and this gate before the assertion ever executes.
 */
@GameTestHolder(SmokeHarness.MODID)
@PrefixGameTestTemplate(false)
public class SmokeGameTest {
    @GameTest(template = "smoke_test")
    public static void serverLoads(GameTestHelper helper) {
        // Reaching here means the server booted with all mods loaded. Nothing else to assert.
        helper.succeed();
    }
}
