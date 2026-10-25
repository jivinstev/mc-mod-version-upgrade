// Stand-in for JeiRecipeProbe on a Minecraft version JEI publishes no build for (no row in
// templates/gatec-carrier/jei.tsv; tools/gatec-carrier.py writes this file instead of the real probe).
//
// ClientBootSmokeTest calls the probe only after ModList.get().isLoaded("jei"), and JEI cannot be installed on such a
// version, so the carrier behaves as on every other version with JEI absent: -Dsmokeharness.jeirecipes /
// -Dsmokeharness.jeiverify print "JEI is not loaded" and are skipped. These methods are never reached.
package com.forgeupgrade.smokeharness;

import java.util.List;
import java.util.Map;

public class JeiRecipeProbe {
    public static boolean runtimeReady() {
        return false;
    }

    public static List<String> openRecipes(List<String> uids) {
        throw new IllegalStateException("JEI has no build for this Minecraft version");
    }

    public static Map<String, Integer> countRecipesProducing(List<String> itemIds) {
        throw new IllegalStateException("JEI has no build for this Minecraft version");
    }
}
