// JeiRecipeProbe — an isolated JEI-touching helper for the client boot smoke test.
//
// WHY: registering a JEI plugin (categories + recipes) is NOT the same as its GUI working. JEI calls a
// category's setRecipe()/draw() only when a user actually OPENS that recipe — so a render-time bug
// (bad blit, NPE in draw, a broken slot layout) is invisible to a log-grep of registration. This helper
// opens the target categories via the JEI runtime so that render path actually runs on-screen.
//
// SAFETY: this class references mezz.jei.api.* — so it must ONLY be loaded when JEI is present. JEI
// discovers the @JeiPlugin below (and calls onRuntimeAvailable) only when JEI is installed; and the
// harness only calls openRecipes()/runtimeReady() after ModList.get().isLoaded("jei"). With JEI absent
// the class is never referenced, so it never loads (no NoClassDefFoundError).
package com.forgeupgrade.smokeharness;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import mezz.jei.api.IModPlugin;
import mezz.jei.api.JeiPlugin;
import mezz.jei.api.constants.VanillaTypes;
import mezz.jei.api.recipe.IFocus;
import mezz.jei.api.recipe.RecipeIngredientRole;
import mezz.jei.api.recipe.RecipeType;
import mezz.jei.api.recipe.category.IRecipeCategory;
import mezz.jei.api.runtime.IJeiRuntime;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.item.ItemStack;

@JeiPlugin
public class JeiRecipeProbe implements IModPlugin {
    private static IJeiRuntime runtime;

    @Override
    public ResourceLocation getPluginUid() {
        return ResourceLocation.fromNamespaceAndPath("smokeharness", "recipe_probe");
    }

    @Override
    public void onRuntimeAvailable(IJeiRuntime jeiRuntime) {
        runtime = jeiRuntime;
    }

    /** True once JEI has handed us its runtime (i.e. JEI finished loading its plugins). */
    public static boolean runtimeReady() {
        return runtime != null;
    }

    /**
     * Resolve each recipe-type UID against JEI's runtime and open the recipe GUI for the resolved ones —
     * which triggers each category's setRecipe()/draw() as JEI renders the screen.
     *
     * @return the UIDs that could NOT be resolved (empty list = every category was found and opened).
     */
    public static List<String> openRecipes(List<String> uids) {
        if (runtime == null) {
            throw new IllegalStateException("JEI runtime not available (onRuntimeAvailable never fired)");
        }
        List<RecipeType<?>> types = new ArrayList<>();
        List<String> missing = new ArrayList<>();
        for (String uid : uids) {
            Optional<RecipeType<?>> type = runtime.getRecipeManager().getRecipeType(ResourceLocation.parse(uid));
            if (type.isPresent()) {
                types.add(type.get());
            } else {
                missing.add(uid);
            }
        }
        if (!types.isEmpty()) {
            // Opens JEI's IRecipesGui for these categories → JEI builds + renders their layouts.
            runtime.getRecipesGui().showTypes(types);
        }
        return missing;
    }

    /**
     * For each item id, count how many recipes across ALL JEI categories produce it (an OUTPUT focus).
     * This proves a mod's recipes are actually visible in JEI even when they have **no custom category**
     * and ride the vanilla ones (crafting/smithing/smelting/…) — the case `openRecipes` (open-by-UID)
     * can't cover, since there's no `<modid>:…` category to open.
     *
     * @return itemId → number of recipes JEI shows that output it (0 = JEI shows none for that item).
     */
    public static Map<String, Integer> countRecipesProducing(List<String> itemIds) {
        if (runtime == null) {
            throw new IllegalStateException("JEI runtime not available (onRuntimeAvailable never fired)");
        }
        Map<String, Integer> counts = new LinkedHashMap<>();
        for (String id : itemIds) {
            ItemStack stack = new ItemStack(BuiltInRegistries.ITEM.get(ResourceLocation.parse(id)));
            IFocus<ItemStack> focus = runtime.getJeiHelpers().getFocusFactory()
                .createFocus(RecipeIngredientRole.OUTPUT, VanillaTypes.ITEM_STACK, stack);
            List<IFocus<?>> focuses = List.of(focus);
            int count = 0;
            for (IRecipeCategory<?> category : runtime.getRecipeManager()
                    .createRecipeCategoryLookup().limitFocus(focuses).get().toList()) {
                count += countInCategory(category, focuses);
            }
            counts.put(id, count);
        }
        return counts;
    }

    private static <R> int countInCategory(IRecipeCategory<R> category, List<IFocus<?>> focuses) {
        return (int) runtime.getRecipeManager()
            .createRecipeLookup(category.getRecipeType()).limitFocus(focuses).get().count();
    }
}
