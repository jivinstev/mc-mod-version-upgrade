#!/usr/bin/env python3
"""Convert Forge 1.20 GUI overlays (IGuiOverlay / ForgeGui / RegisterGuiOverlaysEvent) to NeoForge 1.21.1 GUI layers.

    python3 tools/convert-gui-overlays.py --src src/main/java --modid <modid> [--dry-run] [--self-check]

CATALOG §38 / X118. Forge 1.20 drew a HUD element as an IGuiOverlay:

    void render(ForgeGui gui, GuiGraphics g, float partialTick, int screenWidth, int screenHeight)
    event.registerAbove(VanillaGuiOverlay.PLAYER_HEALTH.id(), "armor", Overlays::armor);   // RegisterGuiOverlaysEvent

NeoForge 1.21.1 draws it as a vanilla LayeredDraw.Layer, `void render(GuiGraphics g, DeltaTracker deltaTracker)`,
registered on RegisterGuiLayersEvent under a ResourceLocation, beside a VanillaGuiLayers id. Everything the old
signature handed in is still reachable, so a layer method keeps its body and gets the old parameters back as locals:

    gui          -> Minecraft.getInstance().gui   (NeoForge's Gui carries ForgeGui's leftHeight/rightHeight)
    partialTick  -> deltaTracker.getGameTimeDeltaPartialTick(false)
    screenWidth  -> g.guiWidth()      screenHeight -> g.guiHeight()

The converter rewrites: methods of exactly that five-parameter shape; five-parameter lambdas passed to a register call;
the registration calls (a String id becomes ResourceLocation(modid, id), `rl.getPath()` becomes `rl`); VanillaGuiOverlay
ids to VanillaGuiLayers (several camera overlays share one 1.21 layer); RenderGuiOverlayEvent handlers to
RenderGuiLayerEvent (`getOverlay()` comparisons become `getName().equals(...)`, the DeltaTracker partial tick is
unwrapped, `getWindow()` comes from Minecraft); and the remaining type names. What it cannot know -- an overlay id with
no 1.21 layer, ForgeGui-only helpers such as setupOverlayRenderState -- is left as written and listed.
Standard library only.
"""
import argparse, importlib.util, pathlib, re, sys, tempfile

_spec = importlib.util.spec_from_file_location("forge_shapes", pathlib.Path(__file__).resolve().parent / "forge-shapes.py")
fs = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(fs)

# Forge 1.20.1 VanillaGuiOverlay -> NeoForge 1.21.1 VanillaGuiLayers (both read from their jars)
LAYERS = {
    "VIGNETTE": "CAMERA_OVERLAYS", "SPYGLASS": "CAMERA_OVERLAYS", "HELMET": "CAMERA_OVERLAYS",
    "FROSTBITE": "CAMERA_OVERLAYS", "PORTAL": "CAMERA_OVERLAYS",
    "HOTBAR": "HOTBAR", "CROSSHAIR": "CROSSHAIR", "BOSS_EVENT_PROGRESS": "BOSS_OVERLAY",
    "PLAYER_HEALTH": "PLAYER_HEALTH", "ARMOR_LEVEL": "ARMOR_LEVEL", "FOOD_LEVEL": "FOOD_LEVEL",
    "AIR_LEVEL": "AIR_LEVEL", "MOUNT_HEALTH": "VEHICLE_HEALTH", "JUMP_BAR": "JUMP_METER",
    "EXPERIENCE_BAR": "EXPERIENCE_BAR", "ITEM_NAME": "SELECTED_ITEM_NAME", "SLEEP_FADE": "SLEEP_OVERLAY",
    "POTION_ICONS": "EFFECTS", "DEBUG_TEXT": "DEBUG_OVERLAY", "FPS_GRAPH": "DEBUG_OVERLAY",
    "RECORD_OVERLAY": "OVERLAY_MESSAGE", "TITLE_TEXT": "TITLE", "SUBTITLES": "SUBTITLE_OVERLAY",
    "SCOREBOARD": "SCOREBOARD_SIDEBAR", "CHAT_PANEL": "CHAT", "PLAYER_LIST": "TAB_LIST",
}
OLD_PKGS = r"net\.(?:minecraftforge|neoforged\.neoforge)\.client"
LAYERS_FQN = "net.neoforged.neoforge.client.gui.VanillaGuiLayers"
TRIGGER = re.compile(r"\b(ForgeGui|IGuiOverlay|RegisterGuiOverlaysEvent|RenderGuiOverlayEvent|VanillaGuiOverlay)\b")
IMPORTS = {   # old simple name -> (new simple name, new fqn)
    "ForgeGui": ("Gui", "net.minecraft.client.gui.Gui"),
    "IGuiOverlay": ("LayeredDraw.Layer", "net.minecraft.client.gui.LayeredDraw"),
    "RegisterGuiOverlaysEvent": ("RegisterGuiLayersEvent", "net.neoforged.neoforge.client.event.RegisterGuiLayersEvent"),
    "RenderGuiOverlayEvent": ("RenderGuiLayerEvent", "net.neoforged.neoforge.client.event.RenderGuiLayerEvent"),
    "VanillaGuiOverlay": ("VanillaGuiLayers", LAYERS_FQN),
}
SHAPE = ["GuiGraphics", "float", "int", "int"]
# ForgeGui's own helpers: none is a public method of NeoForge 1.21.1's Gui (checked with javap -p)
FORGE_ONLY = ("setupOverlayRenderState", "shouldDrawSurvivalElements", "renderHealth", "renderFood", "renderAir",
              "renderHealthMount", "renderToolHighlight", "renderHUDText", "renderFPSGraph", "renderRecordOverlay",
              "renderSubtitles", "renderPlayerList", "renderExperience", "renderBossHealth")


def prologue(gui, pt, w, h, g, dt, body, ind):
    """The old parameters back as locals, only those the body still reads."""
    used = lambda n: re.search(r"(?<![\w$.])%s(?![\w$])" % re.escape(n), body) is not None
    out = []
    if used(gui):
        out.append(f"net.minecraft.client.gui.Gui {gui} = net.minecraft.client.Minecraft.getInstance().gui;")
    if used(pt):
        out.append(f"float {pt} = {dt}.getGameTimeDeltaPartialTick(false);")
    if used(w):
        out.append(f"int {w} = {g}.guiWidth();")
    if used(h):
        out.append(f"int {h} = {g}.guiHeight();")
    return "".join(f" {s}" for s in out) if ind is None else "".join(f"\n{ind}{s}" for s in out)


def body_indent(text, brace, decl):
    """Indentation of the first statement inside the block opening at `brace` (else the declaration's + 4)."""
    m = re.match(r"\{[ \t]*\n(?:[ \t]*\n)*([ \t]*)\S", text[brace:])
    if m:
        return m.group(1)
    return re.match(r"[ \t]*", text[text.rfind("\n", 0, decl) + 1:]).group(0) + "    "


def layer_methods(text, flags, path):
    edits, n = [], 0
    for mt in fs.methods(text):
        if len(mt.params) != 5:
            continue
        types = [fs.ptype(p).rsplit(".", 1)[-1] for p in mt.params]
        if types[0] not in ("ForgeGui", "Gui") or types[1:] != SHAPE:
            continue
        gui, g, pt, w, h = [fs.pname(p) for p in mt.params]
        body = text[mt.body_open:mt.body_close + 1]
        dt = fs.fresh("deltaTracker", body)
        ind = body_indent(text, mt.body_open, mt.start)
        pro = prologue(gui, pt, w, h, g, dt, body, ind)
        gp = mt.params[1]
        edits.append((mt.params_open + 1, mt.params_close, f"{gp}, net.minecraft.client.DeltaTracker {dt}"))
        if pro:
            edits.append((mt.body_open + 1, mt.body_open + 1, pro))
        n += 1
    return fs.edit_spans(text, edits), n


LAMBDA = re.compile(r"\(\s*(\w+)\s*,\s*(\w+)\s*,\s*(\w+)\s*,\s*(\w+)\s*,\s*(\w+)\s*\)\s*->\s*")


def layer_lambdas(text, flags, path):
    """Five-parameter lambdas: an IGuiOverlay written inline (a register argument, a field, a local). The file is already
    known to use the overlay API (TRIGGER), and no other common functional interface of that era takes five arguments."""
    n, pos = 0, 0
    while True:
        lm = LAMBDA.search(text, pos)
        if not lm:
            return text, n
        gui, g, pt, w, h = lm.groups()
        start = lm.end()
        if text[start] == "{":
            bend = fs.match(text, start)
            body = text[start:bend + 1]
            dt = fs.fresh("deltaTracker", body)
            ind = body_indent(text, start, lm.start())
            new = f"({g}, {dt}) -> {{" + prologue(gui, pt, w, h, g, dt, body, ind) + body[1:]
            text = text[:lm.start()] + new + text[bend + 1:]
        else:   # expression lambda: runs to the `,` `;` or closing bracket that ends it
            depth, i = 0, start
            while i < len(text):
                k = fs._skip(text, i)
                if k != i:
                    i = k; continue
                c = text[i]
                if c in "([{":
                    depth += 1
                elif c in ")]}":
                    if depth == 0:
                        break
                    depth -= 1
                elif c in ",;" and depth == 0:
                    break
                i += 1
            expr = text[start:i].rstrip()
            dt = fs.fresh("deltaTracker", expr)
            pro = prologue(gui, pt, w, h, g, dt, expr, None)
            new = f"({g}, {dt}) -> {{{pro} {expr}; }}"
            text = text[:lm.start()] + new + text[start + len(expr):]
        pos = lm.start() + len(new)
        n += 1


def registrations(text, modid, flags, path):
    n = 0
    out, pos = [], 0
    for cm in re.finditer(r"\.(registerAbove|registerBelow|registerAboveAll|registerBelowAll)\s*\(", text):
        if cm.start() < pos:
            continue
        close = fs.match(text, cm.end() - 1)
        args = fs.split_args(text[cm.end():close])
        k = 0 if cm.group(1).endswith("All") else 1
        if len(args) <= k:
            continue
        a = args[k].strip()
        new = None
        if re.fullmatch(r'"[^"]*"', a):
            new = f"net.minecraft.resources.ResourceLocation.fromNamespaceAndPath(\"{modid}\", {a})"
        else:
            m = re.fullmatch(r"(.+?)\.getPath\(\)", a)
            if m:
                new = m.group(1)
        if new is None:
            continue
        args = [x.strip() for x in args]
        args[k] = new
        out.append(text[pos:cm.end()] + ", ".join(args) + ")")
        pos = close + 1
        n += 1
    out.append(text[pos:])
    return "".join(out), n


def layer_ids(text, flags, path):
    n = 0
    def rep(m):
        nonlocal n
        new = LAYERS.get(m.group(1))
        if not new:
            flags.append(f"{path}:{fs.line_of(text, m.start())} VanillaGuiOverlay.{m.group(1)} has no 1.21 layer")
            return m.group(0)
        n += 1
        return f"VanillaGuiLayers.{new}"
    text = re.sub(r"(?<![\w$])(?:[\w.]+\.)?VanillaGuiOverlay\.([A-Z_]+)(?:\s*\.\s*(?:id|type)\(\))?", rep, text)
    return text, n


def render_events(text, flags, path):
    n = 0
    # getOverlay() comparisons -> the layer's name
    text, k = re.subn(r"\.getOverlay\(\)\s*\.\s*id\(\)", ".getName()", text); n += k
    text, k = re.subn(r"(\w+(?:\.\w+\(\))?)\.getOverlay\(\)\s*==\s*(VanillaGuiLayers\.\w+)", r"\1.getName().equals(\2)", text); n += k
    text, k = re.subn(r"(\w+(?:\.\w+\(\))?)\.getOverlay\(\)\s*!=\s*(VanillaGuiLayers\.\w+)", r"!\1.getName().equals(\2)", text); n += k
    text, k = re.subn(r"(\w+(?:\.\w+\(\))?)\.getOverlay\(\)\s*\.\s*equals\(\s*(VanillaGuiLayers\.\w+)\s*\)", r"\1.getName().equals(\2)", text); n += k
    # in a RenderGuiOverlayEvent/RenderGuiEvent handler: the partial tick is a DeltaTracker; the window comes from Minecraft
    for mt in reversed(fs.methods(text)):
        if len(mt.params) != 1 or not re.search(r"\bRender(?:GuiOverlay|GuiLayer|Gui)Event\b", fs.ptype(mt.params[0])):
            continue
        e = fs.pname(mt.params[0])
        body = text[mt.body_open:mt.body_close + 1]
        nb = re.sub(r"(?<![\w$.])%s\.getPartialTick\(\)(?!\s*\.)" % re.escape(e),
                    f"{e}.getPartialTick().getGameTimeDeltaPartialTick(false)", body)
        nb = re.sub(r"(?<![\w$.])%s\.getWindow\(\)" % re.escape(e), "net.minecraft.client.Minecraft.getInstance().getWindow()", nb)
        if nb != body:
            text = text[:mt.body_open] + nb + text[mt.body_close + 1:]
            n += 1
    return text, n


def types_and_imports(text):
    for old, (new, fqn) in IMPORTS.items():
        had = re.search(r"(?m)^import\s+%s\.[\w.]*\b%s\s*;" % (OLD_PKGS, old), text)
        text = re.sub(r"(?m)^import\s+%s\.[\w.]*\b%s(?:\.\w+)?\s*;[ \t]*\n" % (OLD_PKGS, old), "", text)
        text = re.sub(r"(?<![\w$])(?:%s\.[\w.]*?)?%s(?![\w$])" % (OLD_PKGS, old), new, text)
        if had or re.search(r"(?<![\w$.])%s\b" % re.escape(new.split(".")[0]), text):
            text = fs.add_import(text, fqn)
    if "VanillaGuiLayers." in text:
        text = fs.add_import(text, LAYERS_FQN)
    return text


def convert(text, modid, path="X.java"):
    flags = []
    if not TRIGGER.search(text):
        return text, {}, flags
    counts = {}
    text, counts["layer-methods"] = layer_methods(text, flags, path)
    text, counts["layer-lambdas"] = layer_lambdas(text, flags, path)
    text, counts["registrations"] = registrations(text, modid, flags, path) if "RegisterGuiOverlaysEvent" in text else (text, 0)
    text, counts["layer-ids"] = layer_ids(text, flags, path)
    text, counts["render-events"] = render_events(text, flags, path)
    text = types_and_imports(text)
    for m in re.finditer(r"\.(%s)\(" % "|".join(FORGE_ONLY), text):
        flags.append(f"{path}:{fs.line_of(text, m.start())} ForgeGui.{m.group(1)} is not on 1.21's Gui; port by hand")
    return text, counts, flags


def run(src, modid, dry=False):
    total, flags, files = {}, [], 0
    for f in sorted(pathlib.Path(src).rglob("*.java")):
        t = f.read_text(encoding="utf-8", errors="replace")
        new, counts, fl = convert(t, modid, str(f))
        flags += fl
        if new != t:
            files += 1
            for k, v in counts.items():
                total[k] = total.get(k, 0) + v
            if not dry:
                f.write_text(new, encoding="utf-8")
    return files, total, flags


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src"); ap.add_argument("--modid"); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not a.src or not a.modid:
        ap.error("--src and --modid are required")
    files, total, flags = run(a.src, a.modid, a.dry_run)
    print(f"convert-gui-overlays: {files} file(s) changed" + "".join(f", {v} {k}" for k, v in total.items() if v))
    for f in flags:
        print(f"  FLAG {f}")
    return 0


FIXTURE = '''package a;

import net.minecraft.client.gui.GuiGraphics;
import net.neoforged.neoforge.client.event.RegisterGuiOverlaysEvent;
import net.neoforged.neoforge.client.event.RenderGuiOverlayEvent;
import net.neoforged.neoforge.client.gui.overlay.ForgeGui;
import net.neoforged.neoforge.client.gui.overlay.IGuiOverlay;
import net.neoforged.neoforge.client.gui.overlay.VanillaGuiOverlay;

public class Overlays {
   public static final IGuiOverlay HUD = (gui, graphics, partialTick, width, height) -> draw(graphics, width);

   public static void register(RegisterGuiOverlaysEvent event) {
      event.registerAbove(VanillaGuiOverlay.PLAYER_HEALTH.id(), "armor", Overlays::armor);
      event.registerAbove(ID.getPath() == null ? null : VanillaGuiOverlay.MOUNT_HEALTH.id(), ID.getPath(), Overlays::armor);
      event.registerAboveAll("debug", (gui, graphics, partialTick, width, height) -> {
         gui.leftHeight += 10;
      });
   }

   private static void armor(ForgeGui gui, GuiGraphics graphics, float partialTicks, int screenWidth, int screenHeight) {
      int y = screenHeight - gui.leftHeight;
      graphics.drawString(gui.getFont(), "x", screenWidth / 2, y, -1);
   }

   public static void onRender(RenderGuiOverlayEvent.Pre event) {
      if (event.getOverlay() == VanillaGuiOverlay.ARMOR_LEVEL.type()) {
         event.setCanceled(true);
      }
      float pt = event.getPartialTick();
   }
}
'''


def self_check():
    out, counts, flags = convert(FIXTURE, "mymod")
    want = [
        "private static void armor(GuiGraphics graphics, net.minecraft.client.DeltaTracker deltaTracker) {",
        "net.minecraft.client.gui.Gui gui = net.minecraft.client.Minecraft.getInstance().gui;",
        "int screenWidth = graphics.guiWidth();", "int screenHeight = graphics.guiHeight();",
        'event.registerAbove(VanillaGuiLayers.PLAYER_HEALTH, net.minecraft.resources.ResourceLocation.fromNamespaceAndPath("mymod", "armor"), Overlays::armor);',
        'event.registerAboveAll(net.minecraft.resources.ResourceLocation.fromNamespaceAndPath("mymod", "debug"), (graphics, deltaTracker) -> {',
        "gui.leftHeight += 10;",
        "public static void register(RegisterGuiLayersEvent event) {",
        "public static final LayeredDraw.Layer HUD = (graphics, deltaTracker) -> {",
        "if (event.getName().equals(VanillaGuiLayers.ARMOR_LEVEL)) {",
        "public static void onRender(RenderGuiLayerEvent.Pre event) {",
        "float pt = event.getPartialTick().getGameTimeDeltaPartialTick(false);",
        "import net.minecraft.client.gui.LayeredDraw;", "import " + LAYERS_FQN + ";",
        "import net.neoforged.neoforge.client.event.RegisterGuiLayersEvent;",
    ]
    gone = ["ForgeGui", "IGuiOverlay", "VanillaGuiOverlay", "RegisterGuiOverlaysEvent", "RenderGuiOverlayEvent",
            "float partialTicks = ", "getOverlay()"]
    miss = [w for w in want if w not in out] + [f"still has {g!r}" for g in gone if g in out]
    # a lambda assigned to an IGuiOverlay FIELD is not inside a register call: it must still convert via its type
    if "(gui, graphics, partialTick, width, height)" in out:
        miss.append("field lambda left with five parameters")
    print("self-check:", "OK" if not miss else "FAIL " + "\n".join(miss) + "\n" + out)
    return 0 if not miss else 1


if __name__ == "__main__":
    sys.exit(main())
