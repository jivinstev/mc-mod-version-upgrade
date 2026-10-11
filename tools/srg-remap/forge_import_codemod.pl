s{net\.minecraftforge\.api\.distmarker}{net.neoforged.api.distmarker}g;
s{net\.minecraftforge\.eventbus\.api}{net.neoforged.bus.api}g;
s{net\.minecraftforge\.items}{net.neoforged.neoforge.items}g;
s{net\.minecraftforge\.entity}{net.neoforged.neoforge.entity}g;
s{net\.minecraftforge\.energy}{net.neoforged.neoforge.energy}g;
s{net\.minecraftforge\.fluids}{net.neoforged.neoforge.fluids}g;
s{net\.minecraftforge\.client\.model\.data}{net.neoforged.neoforge.client.model.data}g;
s{net\.minecraftforge\.client\.extensions}{net.neoforged.neoforge.client.extensions}g;
s{net\.minecraftforge\.client\.gui\.overlay}{net.neoforged.neoforge.client.gui.overlay}g;
s{net\.minecraftforge\.client\.event}{net.neoforged.neoforge.client.event}g;
# X167: client packages NeoForge kept under the same class names (ForgeSlider was renamed ExtendedSlider)
s{net\.minecraftforge\.client\.settings}{net.neoforged.neoforge.client.settings}g;
s{net\.minecraftforge\.client\.gui\.widget\.ForgeSlider\b}{net.neoforged.neoforge.client.gui.widget.ExtendedSlider}g;
s{\bForgeSlider\b}{ExtendedSlider}g;
s{net\.minecraftforge\.client\.gui\.widget}{net.neoforged.neoforge.client.gui.widget}g;
s{net\.minecraftforge\.client\.textures}{net.neoforged.neoforge.client.textures}g;
s{net\.minecraftforge\.fml}{net.neoforged.fml}g;
s{net\.minecraftforge\.registries}{net.neoforged.neoforge.registries}g;
s{net\.minecraftforge\.event}{net.neoforged.neoforge.event}g;
s{net\.minecraftforge\.common\.MinecraftForge}{net.neoforged.neoforge.common.NeoForge}g;
s{\bMinecraftForge\.EVENT_BUS\b}{NeoForge.EVENT_BUS}g;
s{net\.minecraftforge\.common\.ForgeConfigSpec}{net.neoforged.neoforge.common.ModConfigSpec}g;
s{\bForgeConfigSpec\b}{ModConfigSpec}g;
s{net\.minecraftforge\.common\b}{net.neoforged.neoforge.common}g;
s{net\.minecraftforge\.server}{net.neoforged.neoforge.server}g;
# Slash-form (JVM descriptor) references inside mixin `target = "..."` / `method = "..."` strings. The dotted
# rules above never see these, the result still COMPILES, and the mixin then fails at APPLY time, on the
# client only for a client mixin (catalogue §H). Specific classes first, then packages.
s{Lnet/minecraftforge/common/ForgeHooks;}{Lnet/neoforged/neoforge/common/CommonHooks;}g;
s{Lnet/minecraftforge/client/ForgeHooksClient;}{Lnet/neoforged/neoforge/client/ClientHooks;}g;
s{Lnet/minecraftforge/event/ForgeEventFactory;}{Lnet/neoforged/neoforge/event/EventHooks;}g;
s{Lnet/minecraftforge/common/MinecraftForge;}{Lnet/neoforged/neoforge/common/NeoForge;}g;
s{net/minecraftforge/api/distmarker/}{net/neoforged/api/distmarker/}g;
s{net/minecraftforge/eventbus/api/}{net/neoforged/bus/api/}g;
s{net/minecraftforge/client/event/}{net/neoforged/neoforge/client/event/}g;
s{net/minecraftforge/client/extensions/}{net/neoforged/neoforge/client/extensions/}g;
s{net/minecraftforge/fml/}{net/neoforged/fml/}g;
s{net/minecraftforge/registries/}{net/neoforged/neoforge/registries/}g;
s{net/minecraftforge/event/}{net/neoforged/neoforge/event/}g;
s{net/minecraftforge/common/}{net/neoforged/neoforge/common/}g;
s{net/minecraftforge/(items|entity|energy|fluids|server)/}{net/neoforged/neoforge/$1/}g;
# A bare package PREFIX string names the loader itself, as in a "never transform these" whitelist:
# "net.minecraftforge." (or the slash form) matches nothing on NeoForge, so the list silently stops
# protecting the loader -- the code compiles and the protection is gone. Only the exact prefix is rewritten.
s{"net\.minecraftforge\."}{"net.neoforged."}g;
s{"net/minecraftforge/"}{"net/neoforged/"}g;
