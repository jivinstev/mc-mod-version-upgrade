// ─── Smoke-harness PLAYTEST mode: a script of Minecraft commands and a few directives, run in the REAL client. ───
// Driven by ClientBootSmokeTest (-Dsmokeharness.testmode=playtest -Dsmokeharness.playtest=<file.playtest>), one
// line at a time on the client tick: a line finishes before the next starts, and a wait never blocks the render
// thread. The grammar is tools/playtest-lint.py's (which checks a script without Minecraft); the runner reports
//   PLAYTEST: STEP <n> <name>: PASS|FAIL -- <detail>      per step
//   PLAYTEST: PASS|FAIL -- <k>/<n> steps                   at the end
// and writes <out>/playtest-result.json (rewritten at every step end) and the screenshots beside it.
//
// The carrier builds this file for every Minecraft version through the rename tables (tools/gatec-carrier.py), so it
// keeps to APIs that are stable across 1.21.1..26.x. What moved between versions -- the GUI's private state (chat,
// titles, boss bars, subtitles), the raw input callbacks, the sound listener interface, log4j's appender -- is reached
// by reflection on Mojang's names (NeoForge runs with them), and a kind that cannot be observed on a version FAILs
// naming what was missing instead of passing silently.
package com.forgeupgrade.smokeharness;

import java.io.IOException;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.lang.reflect.Proxy;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.Deque;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.function.Consumer;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.mojang.blaze3d.platform.InputConstants;
import com.mojang.brigadier.StringReader;
import net.minecraft.client.KeyMapping;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.components.AbstractWidget;
import net.minecraft.client.gui.components.events.GuiEventListener;
import net.minecraft.client.gui.screens.DeathScreen;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.resources.sounds.SoundInstance;
import net.minecraft.client.server.IntegratedServer;
import net.minecraft.commands.CommandSource;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.arguments.EntityArgument;
import net.minecraft.core.registries.Registries;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.contents.TranslatableContents;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.level.levelgen.WorldDimensions;
import net.minecraft.world.level.levelgen.presets.WorldPresets;
import net.minecraft.world.phys.EntityHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;

final class PlaytestRunner {
    static final String TAG = "PLAYTEST";
    private static final org.slf4j.Logger LOG = com.mojang.logging.LogUtils.getLogger();
    private static final int DEFAULT_EXPECT_TICKS = 200;     // 10 s
    private static final int DEFAULT_KEY_HOLD = 2;
    private static final int COMMAND_TIMEOUT_TICKS = 200;
    private static final int RECENT = 8;

    private static void say(String line) {
        LOG.info(line);
    }

    // ═══════════════════════════════════════════════════════════════════════════════════ the script
    static final class ScriptError extends Exception {
        ScriptError(int line, String msg) { super("line " + line + ": " + msg); }
    }

    static final class Step {
        final int n;
        final String name;
        final List<Op> ops = new ArrayList<>();
        String intent = "";
        Step(int n, String name) { this.n = n; this.name = name; }
    }

    final Path script;
    final Path out;
    String intent = "";
    boolean continueOnFail = false;
    boolean flat = true;
    long seed = 0L;
    final List<Pattern> globalAllow = new ArrayList<>();
    final List<Step> steps = new ArrayList<>();
    boolean mentionsImmediateRespawn = false;

    private PlaytestRunner(Path script, Path out) {
        this.script = script;
        this.out = out;
    }

    /** Parse the script; a syntax error throws (the run FAILs before a world is made). */
    static PlaytestRunner load(String scriptPath, String outDir, Path gameDir) throws IOException, ScriptError {
        Path p = Paths.get(scriptPath).toAbsolutePath();
        String base = p.getFileName().toString().replaceFirst("\\.[^.]*$", "");
        Path o = outDir != null && !outDir.isBlank() ? Paths.get(outDir).toAbsolutePath()
            : gameDir.resolve("playtest").resolve(base).toAbsolutePath();
        PlaytestRunner r = new PlaytestRunner(p, o);
        r.parse(Files.readAllLines(p, StandardCharsets.UTF_8));
        return r;
    }

    /** The script could not be read or parsed: write a FAIL result where the runner would have, so a runner reads it. */
    static Path failEarly(String scriptPath, String outDir, Path gameDir, String why) {
        String sp = scriptPath == null ? "" : scriptPath;
        String base = sp.isEmpty() ? "playtest" : Paths.get(sp).getFileName().toString().replaceFirst("\\.[^.]*$", "");
        Path o = outDir != null && !outDir.isBlank() ? Paths.get(outDir).toAbsolutePath()
            : gameDir.resolve("playtest").resolve(base).toAbsolutePath();
        try {
            Files.createDirectories(o);
            JsonObject root = new JsonObject();
            root.addProperty("script", sp);
            root.addProperty("intent", "");
            root.addProperty("verdict", "FAIL");
            root.addProperty("finished", true);
            root.addProperty("detail", why);
            root.add("steps", new JsonArray());
            root.add("events", new JsonArray());
            Files.write(o.resolve("playtest-result.json"),
                new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create().toJson(root).getBytes(StandardCharsets.UTF_8));
        } catch (Throwable t) {
            say(TAG + ": could not write playtest-result.json: " + t);
        }
        return o.resolve("playtest-result.json");
    }

    private static final Pattern DURATION = Pattern.compile("(\\d+(?:\\.\\d+)?)(t|s)");
    static final Set<String> KINDS = Set.of("sound", "subtitle", "chat", "title", "actionbar", "bossbar", "screen", "error");

    static int ticks(String d, int line) throws ScriptError {
        Matcher m = DURATION.matcher(d.trim());
        if (!m.matches()) throw new ScriptError(line, "a duration is <N>t (ticks) or <N>s (seconds), not '" + d + "'");
        double v = Double.parseDouble(m.group(1));
        return (int) Math.round(m.group(2).equals("s") ? v * 20.0 : v);
    }

    private static String unquote(String s) {
        s = s.trim();
        if (s.length() >= 2 && s.startsWith("\"") && s.endsWith("\"")) return s.substring(1, s.length() - 1);
        return s;
    }

    void parse(List<String> lines) throws ScriptError {
        Step cur = null;
        for (int i = 0; i < lines.size(); i++) {
            int no = i + 1;
            String raw = lines.get(i);
            if (i == 0 && raw.startsWith("﻿")) raw = raw.substring(1);
            String text = raw.trim();
            if (text.isEmpty() || text.startsWith("#")) continue;
            if (text.toLowerCase(Locale.ROOT).contains("immediaterespawn") || text.contains("immediate_respawn")) {
                mentionsImmediateRespawn = true;
            }
            if (!text.startsWith("@")) {
                if (cur == null) { cur = new Step(steps.size() + 1, "main"); steps.add(cur); }
                String cmd = text.startsWith("/") ? text.substring(1) : text;
                cur.ops.add(new CommandOp(no, text, cmd, false));
                continue;
            }
            int sp = text.indexOf(' ');
            String dir = (sp < 0 ? text : text.substring(0, sp)).toLowerCase(Locale.ROOT);
            String arg = sp < 0 ? "" : text.substring(sp + 1).trim();
            switch (dir) {
                case "@end" -> { return; }
                case "@intent" -> { if (cur == null) intent = arg; else cur.intent = arg; }
                case "@continue-on-fail" -> {
                    if (cur != null) throw new ScriptError(no, "@continue-on-fail belongs in the header, before the first step");
                    continueOnFail = true;
                }
                case "@world" -> {
                    if (cur != null) throw new ScriptError(no, "@world belongs in the header, before the first step");
                    for (String w : arg.split("\\s+")) {
                        if (w.isEmpty()) continue;
                        if (w.equals("flat")) flat = true;
                        else if (w.equals("normal") || w.equals("default")) flat = false;
                        else if (w.startsWith("seed=")) {
                            try { seed = Long.parseLong(w.substring(5)); } catch (NumberFormatException e) {
                                throw new ScriptError(no, "@world seed=<integer>, not '" + w + "'");
                            }
                        } else throw new ScriptError(no, "@world takes flat|normal and seed=<n>, not '" + w + "'");
                    }
                }
                case "@step" -> {
                    if (arg.isEmpty()) throw new ScriptError(no, "@step needs a name");
                    cur = new Step(steps.size() + 1, arg);
                    steps.add(cur);
                }
                case "@allow" -> {
                    String[] a = arg.split("\\s+", 2);
                    if (a.length < 2 || !a[0].equals("error")) throw new ScriptError(no, "@allow error <regex>");
                    Pattern pt;
                    try { pt = Pattern.compile(a[1]); } catch (Exception e) {
                        throw new ScriptError(no, "@allow error: bad regex: " + e.getMessage());
                    }
                    if (cur == null) globalAllow.add(pt);
                    else cur.ops.add(new AllowOp(no, text, pt));
                }
                default -> {
                    if (cur == null) { cur = new Step(steps.size() + 1, "main"); steps.add(cur); }
                    cur.ops.add(parseOp(no, text, dir, arg));
                }
            }
        }
    }

    private static Op parseOp(int no, String text, String dir, String arg) throws ScriptError {
        switch (dir) {
            case "@assert": {
                if (arg.isEmpty()) throw new ScriptError(no, "@assert needs a command");
                return new CommandOp(no, text, arg.startsWith("/") ? arg.substring(1) : arg, true);
            }
            case "@wait":
                return new WaitOp(no, text, ticks(arg, no));
            case "@aim": {
                String[] a = arg.split("\\s+", 2);
                String rest = a.length > 1 ? a[1].trim() : "";
                switch (a[0]) {
                    case "entity":
                        if (rest.isEmpty()) throw new ScriptError(no, "@aim entity <type id or selector>");
                        return new AimOp(no, text, "entity", rest, null);
                    case "pos": {
                        String[] c = rest.split("\\s+");
                        if (c.length != 3) throw new ScriptError(no, "@aim pos <x> <y> <z>");
                        for (String x : c) coord(x, no);
                        return new AimOp(no, text, "pos", rest, c);
                    }
                    case "look": {
                        String[] c = rest.split("\\s+");
                        if (c.length != 2) throw new ScriptError(no, "@aim look <yaw> <pitch>");
                        for (String x : c) num(x, no);
                        return new AimOp(no, text, "look", rest, c);
                    }
                    default:
                        throw new ScriptError(no, "@aim entity|pos|look, not '" + a[0] + "'");
                }
            }
            case "@key": {
                String[] a = arg.split("\\s+");
                if (a.length == 0 || a[0].isEmpty()) throw new ScriptError(no, "@key <key.mapping.name> [hold <duration>]");
                int hold = DEFAULT_KEY_HOLD;
                if (a.length == 3 && a[1].equals("hold")) hold = Math.max(1, ticks(a[2], no));
                else if (a.length != 1) throw new ScriptError(no, "@key <key.mapping.name> [hold <duration>]");
                return new KeyOp(no, text, a[0], hold);
            }
            case "@click": {
                String[] a = arg.split("\\s+", 2);
                if (a.length == 2 && a[0].equals("slot")) {
                    try { return new ClickOp(no, text, Integer.parseInt(a[1].trim()), null); } catch (NumberFormatException e) {
                        throw new ScriptError(no, "@click slot <N>");
                    }
                }
                if (a.length == 2 && a[0].equals("button")) return new ClickOp(no, text, -1, unquote(a[1]));
                throw new ScriptError(no, "@click slot <N> | @click button \"<text>\"");
            }
            case "@expect":
                return ExpectOp.parse(no, text, arg);
            case "@shot": {
                if (!arg.matches("[A-Za-z0-9._-]+")) throw new ScriptError(no, "@shot <name> (letters, digits, . _ -)");
                return new ShotOp(no, text, arg);
            }
            default:
                throw new ScriptError(no, "unknown directive " + dir + " (known: @intent @step @assert @wait @aim @key @click "
                    + "@expect @shot @allow @world @continue-on-fail @end)");
        }
    }

    private static double num(String s, int no) throws ScriptError {
        try { return Double.parseDouble(s); } catch (NumberFormatException e) { throw new ScriptError(no, "not a number: " + s); }
    }

    private static void coord(String s, int no) throws ScriptError {
        if (s.startsWith("~")) { if (s.length() > 1) num(s.substring(1), no); } else num(s, no);
    }

    // ═══════════════════════════════════════════════════════════════════════════════════ the ops
    static final class Res {
        final boolean ok;
        final String detail;
        Res(boolean ok, String detail) { this.ok = ok; this.detail = detail; }
    }

    static Res pass(String d) { return new Res(true, d); }
    static Res fail(String d) { return new Res(false, d); }

    abstract static class Op {
        final int no;
        final String text;
        Op(int no, String text) { this.no = no; this.text = text; }
        /** Called once; may return a result at once (a one-tick op). */
        Res start(PlaytestRunner r, Minecraft mc) throws Exception { return null; }
        /** Called every tick after start until it returns a result. */
        abstract Res tick(PlaytestRunner r, Minecraft mc) throws Exception;
    }

    static final class AllowOp extends Op {
        final Pattern p;
        AllowOp(int no, String text, Pattern p) { super(no, text); this.p = p; }
        @Override Res start(PlaytestRunner r, Minecraft mc) { r.stepAllow.add(p); return pass("allowing errors matching /" + p.pattern() + "/"); }
        @Override Res tick(PlaytestRunner r, Minecraft mc) { return pass(""); }
    }

    static final class WaitOp extends Op {
        final int ticks;
        int t;
        WaitOp(int no, String text, int ticks) { super(no, text); this.ticks = ticks; }
        @Override Res start(PlaytestRunner r, Minecraft mc) { t = 0; return ticks <= 0 ? pass("waited 0 ticks") : null; }
        @Override Res tick(PlaytestRunner r, Minecraft mc) { return ++t >= ticks ? pass("waited " + ticks + " ticks") : null; }
    }

    /** A command's outcome on the integrated server: the result callback's verdict and what the command said. */
    static final class CmdResult {
        volatile Boolean success;      // null: the command reported no result
        volatile int value;
        final List<String> messages = Collections.synchronizedList(new ArrayList<>());
        String said() {
            synchronized (messages) { return messages.isEmpty() ? "" : String.join(" | ", messages); }
        }
    }

    static final class CommandOp extends Op {
        final String cmd;
        final boolean isAssert;
        volatile CmdResult res;
        int waited;
        CommandOp(int no, String text, String cmd, boolean isAssert) { super(no, text); this.cmd = cmd; this.isAssert = isAssert; }
        @Override Res start(PlaytestRunner r, Minecraft mc) {
            res = null; waited = 0;
            r.runCommand(mc, cmd, x -> res = x);
            return null;
        }
        @Override Res tick(PlaytestRunner r, Minecraft mc) {
            CmdResult x = res;
            if (x == null) {
                return ++waited > COMMAND_TIMEOUT_TICKS ? fail("/" + cmd + ": no result from the server within "
                    + (COMMAND_TIMEOUT_TICKS / 20) + "s") : null;
            }
            String said = x.said();
            if (x.success == null) {
                if (isAssert || !said.isEmpty()) {
                    return fail("/" + cmd + (said.isEmpty() ? ": nothing ran (no result)" : ": " + said));
                }
                return pass("/" + cmd + " (no result reported)");
            }
            if (!x.success) return fail("/" + cmd + " failed" + (said.isEmpty() ? "" : ": " + said));
            return pass("/" + cmd + " -> " + x.value + (said.isEmpty() ? "" : " (" + said + ")"));
        }
    }

    static final class AimOp extends Op {
        final String how, arg;
        final String[] nums;
        volatile List<double[]> found;       // selector results from the server: {id, x, y, z}
        volatile String serverError;
        int waited;
        AimOp(int no, String text, String how, String arg, String[] nums) { super(no, text); this.how = how; this.arg = arg; this.nums = nums; }

        @Override Res start(PlaytestRunner r, Minecraft mc) throws Exception {
            found = null; serverError = null; waited = 0;
            var p = mc.player;
            if (how.equals("look")) {
                setRot(p, (float) Double.parseDouble(nums[0]), (float) Double.parseDouble(nums[1]));
                r.aimed = null;
                return pass("looking yaw " + nums[0] + " pitch " + nums[1] + "; crosshair " + r.pickDesc(mc));
            }
            if (how.equals("pos")) {
                double[] v = new double[3];
                double[] base = {p.getX(), p.getY(), p.getZ()};
                for (int i = 0; i < 3; i++) {
                    String s = nums[i];
                    v[i] = s.startsWith("~") ? base[i] + (s.length() > 1 ? Double.parseDouble(s.substring(1)) : 0) : Double.parseDouble(s);
                }
                lookAt(p, new Vec3(v[0], v[1], v[2]));
                r.aimed = null;
                return pass(String.format(Locale.ROOT, "looking at %.1f %.1f %.1f; crosshair %s", v[0], v[1], v[2], r.pickDesc(mc)));
            }
            if (arg.startsWith("@")) {
                r.selectEntities(mc, arg, this);
                return null;
            }
            String want = arg.contains(":") ? arg : "minecraft:" + arg;
            Entity best = null;
            double bestSq = Double.MAX_VALUE;
            List<String> near = new ArrayList<>();
            for (Entity e : mc.level.getEntities(p, p.getBoundingBox().inflate(64.0), x -> true)) {
                String id = typeId(e);
                double d = e.distanceToSqr(p);
                if (d > 64.0 * 64.0) continue;
                if (near.size() < 6 && !near.contains(id)) near.add(id);
                if (id.equals(want) && d < bestSq) { best = e; bestSq = d; }
            }
            if (best == null) return fail("no " + want + " within 64 blocks (near: " + (near.isEmpty() ? "nothing" : String.join(", ", near)) + ")");
            return r.aimAt(mc, best);
        }

        @Override Res tick(PlaytestRunner r, Minecraft mc) {
            if (serverError != null) return fail("selector " + arg + ": " + serverError);
            List<double[]> f = found;
            if (f == null) return ++waited > 100 ? fail("selector " + arg + ": no answer from the server") : null;
            var p = mc.player;
            Entity best = null;
            double bestSq = Double.MAX_VALUE;
            for (double[] e : f) {
                Entity ce = mc.level.getEntity((int) e[0]);
                if (ce == null) continue;
                double d = ce.distanceToSqr(p);
                if (d <= 64.0 * 64.0 && d < bestSq) { best = ce; bestSq = d; }
            }
            if (best == null) return fail("selector " + arg + " matched " + f.size() + " entit(ies), none within 64 blocks of the player");
            return r.aimAt(mc, best);
        }
    }

    static final class KeyOp extends Op {
        final String name;
        final int hold;
        KeyMapping km;
        int t;
        boolean released;
        String how = "";
        KeyOp(int no, String text, String name, int hold) { super(no, text); this.name = name; this.hold = hold; }

        @Override Res start(PlaytestRunner r, Minecraft mc) {
            t = 0; released = false; how = "";
            km = null;
            List<String> names = new ArrayList<>();
            for (KeyMapping k : mc.options.keyMappings) {
                names.add(k.getName());
                if (k.getName().equals(name)) km = k;
            }
            if (km == null) return fail("no key mapping named " + name + "; nearest: " + nearest(name, names));
            if (km == mc.options.keyAttack && r.aimed != null) {
                r.pick(mc);
                HitResult hr = mc.hitResult;
                if (!(hr instanceof EntityHitResult ehr) || ehr.getEntity() != r.aimed) {
                    // The crosshair is not on the aimed entity (moved, too far, behind something): hit it directly the way
                    // a real attack does, and say so -- the observation records which path was taken.
                    String on = r.pickDesc(mc);
                    // The server drops an attack beyond the player's reach without a word (measured: two "hits" from 6
                    // blocks, the target untouched) -- so say it here, where the script can be fixed.
                    if (!mc.player.canInteractWithEntity(r.aimed.getBoundingBox(), 1.0)) {
                        return fail(name + ": " + typeId(r.aimed) + " is out of reach (" + String.format("%.1f", mc.player.distanceTo(r.aimed))
                                + " blocks; reach " + String.format("%.1f", mc.player.entityInteractionRange()) + "): summon it or tp the player closer");
                    }
                    mc.gameMode.attack(mc.player, r.aimed);
                    R.call(mc.player, "swing", InteractionHand.MAIN_HAND);   // cosmetic; 26.3's swing takes more arguments
                    how = "direct gameMode.attack on " + typeId(r.aimed) + " #" + r.aimed.getId() + " (crosshair " + on + ")";
                    r.note("aim", "attack: " + how);
                    released = true;
                    return pass(name + ": " + how);
                }
                how = "crosshair on " + typeId(r.aimed) + " #" + r.aimed.getId() + "; ";
                r.note("aim", "attack: crosshair hit " + typeId(r.aimed));
            }
            how += Input.press(mc, km, true);
            return null;
        }

        @Override Res tick(PlaytestRunner r, Minecraft mc) {
            if (released) return pass(name + ": " + how);
            if (++t >= hold) {
                Input.press(mc, km, false);
                released = true;
            }
            return null;
        }
    }

    static final class ClickOp extends Op {
        final int slot;
        final String button;
        ClickOp(int no, String text, int slot, String button) { super(no, text); this.slot = slot; this.button = button; }

        @Override Res start(PlaytestRunner r, Minecraft mc) throws Exception {
            Screen screen = mc.screen;
            if (screen == null) return fail("no screen is open");
            if (button == null) {
                var menu = mc.player.containerMenu;
                if (slot < 0 || slot >= menu.slots.size()) return fail("slot " + slot + " out of range: the open menu has " + menu.slots.size() + " slots");
                Method m = R.method(mc.gameMode.getClass(), "handleInventoryMouseClick", 5);
                if (m == null) m = R.method(mc.gameMode.getClass(), "handleContainerInput", 5);
                if (m == null) return fail("no MultiPlayerGameMode slot-click method on this version");
                Class<?> ct = m.getParameterTypes()[3];
                Object pickup = ct.isEnum() ? enumValue(ct, "PICKUP") : null;
                m.invoke(mc.gameMode, menu.containerId, slot, 0, pickup, mc.player);
                return pass("clicked slot " + slot + " of " + screen.getClass().getSimpleName() + "; carried "
                    + menu.getCarried().getHoverName().getString() + " x" + menu.getCarried().getCount());
            }
            List<String> seen = new ArrayList<>();
            AbstractWidget hit = null;
            for (GuiEventListener l : screen.children()) {
                if (!(l instanceof AbstractWidget w)) continue;
                String msg = w.getMessage().getString();
                seen.add("\"" + msg + "\"");
                if (hit == null && w.visible && w.active && msg.toLowerCase(Locale.ROOT).contains(button.toLowerCase(Locale.ROOT))) hit = w;
            }
            if (hit == null) return fail("no active widget whose message contains \"" + button + "\" on " + screen.getClass().getSimpleName() + " (widgets: " + seen + ")");
            double x = hit.getX() + hit.getWidth() / 2.0, y = hit.getY() + hit.getHeight() / 2.0;
            String how = Input.clickScreen(screen, x, y);
            return pass("clicked \"" + hit.getMessage().getString() + "\" at " + (int) x + "," + (int) y + " (" + how + ")");
        }

        @Override Res tick(PlaytestRunner r, Minecraft mc) { return pass(""); }
    }

    static final class ShotOp extends Op {
        final String name;
        Shot shot;
        int waited;
        ShotOp(int no, String text, String name) { super(no, text); this.name = name; }
        @Override Res start(PlaytestRunner r, Minecraft mc) { waited = 0; shot = r.takeShot(mc, name); return null; }
        @Override Res tick(PlaytestRunner r, Minecraft mc) {
            if (shot.done) return shot.error == null ? pass("saved " + shot.path) : fail("screenshot " + shot.file + ": " + shot.error);
            return ++waited > 200 ? fail("screenshot " + shot.file + " was not written within 10s") : null;
        }
    }

    static final class ExpectOp extends Op {
        final String kind, match;
        final boolean gone, explicitWithin;
        final int within;
        int t, sinceTick;
        ExpectOp(int no, String text, String kind, String match, boolean gone, int within, boolean explicitWithin) {
            super(no, text);
            this.kind = kind; this.match = match; this.gone = gone; this.within = within; this.explicitWithin = explicitWithin;
        }

        static ExpectOp parse(int no, String text, String arg) throws ScriptError {
            List<String> tok = new ArrayList<>(List.of(arg.trim().split("\\s+")));
            if (tok.isEmpty() || tok.get(0).isEmpty()) throw new ScriptError(no, "@expect <kind> <match> [gone] [within <duration>]");
            String kind = tok.remove(0).toLowerCase(Locale.ROOT);
            if (!KINDS.contains(kind)) throw new ScriptError(no, "unknown @expect kind '" + kind + "' (kinds: " + String.join(" ", new java.util.TreeSet<>(KINDS)) + ")");
            int within = DEFAULT_EXPECT_TICKS;
            boolean explicit = false;
            if (tok.size() >= 2 && tok.get(tok.size() - 2).equals("within")) {
                within = ticks(tok.remove(tok.size() - 1), no);
                tok.remove(tok.size() - 1);
                explicit = true;
            }
            boolean gone = false;
            if (!tok.isEmpty() && tok.get(tok.size() - 1).equals("gone")) { gone = true; tok.remove(tok.size() - 1); }
            String match = unquote(String.join(" ", tok));
            if (match.isEmpty() && !kind.equals("error")) throw new ScriptError(no, "@expect " + kind + " needs a match");
            if (match.length() >= 2 && match.startsWith("/") && match.endsWith("/")) {
                try { Pattern.compile(match.substring(1, match.length() - 1)); } catch (Exception e) {
                    throw new ScriptError(no, "@expect: bad /regex/: " + e.getMessage());
                }
            }
            return new ExpectOp(no, text, kind, match, gone, within, explicit);
        }

        @Override Res start(PlaytestRunner r, Minecraft mc) {
            t = 0;
            sinceTick = r.tick;
            return null;
        }

        @Override Res tick(PlaytestRunner r, Minecraft mc) {
            Observer o = r.observers.get(kind);
            String unsupported = o.unsupported(r, mc);
            if (unsupported != null) return fail(kind + " cannot be observed on this version: " + unsupported);
            boolean event = o.isEvent();
            int window = event && gone && !explicitWithin ? 0 : within;
            String what = kind + " " + (match.isEmpty() ? "(any)" : "'" + match + "'");
            if (!gone) {
                String hit = o.holds(r, mc, match, sinceTick);
                if (hit != null) return pass(what + " seen: " + hit);
                if (t++ >= window) return fail(what + " not seen within " + ticksText(window) + "; observed: " + o.recentText());
                return null;
            }
            String hit = event ? o.holds(r, mc, match, r.stepStartTick) : o.present(r, mc, match);
            if (event) {
                if (hit != null) return fail(what + " was seen (expected none): " + hit);
                return t++ >= window ? pass(what + ": none" + (window > 0 ? " for " + ticksText(window) : "")) : null;
            }
            if (hit == null) return pass(what + " gone");
            if (t++ >= window) return fail(what + " still present after " + ticksText(window) + ": " + hit);
            return null;
        }
    }

    static String ticksText(int t) {
        return t % 20 == 0 ? (t / 20) + "s" : t + "t";
    }

    // ═══════════════════════════════════════════════════════════════════════════════════ observers
    /**
     * One kind of observation. Adding a kind = one subclass here + its name in {@link #KINDS} (and in tools/playtest-lint.py):
     * poll() runs every tick and records what appeared; holds() answers an @expect; present() answers @expect ... gone.
     */
    abstract static class Observer {
        final String name;
        final Deque<String> recent = new ArrayDeque<>();
        Observer(String name) { this.name = name; }
        /** Is this a stream of events (chat, error) rather than a state that comes and goes? */
        boolean isEvent() { return false; }
        /** null when this version lets us observe the kind; else what is missing. */
        String unsupported(PlaytestRunner r, Minecraft mc) { return null; }
        abstract void poll(PlaytestRunner r, Minecraft mc);
        /** The matching observation as text, or null. */
        abstract String holds(PlaytestRunner r, Minecraft mc, String match, int sinceTick);
        String present(PlaytestRunner r, Minecraft mc, String match) { return holds(r, mc, match, Integer.MIN_VALUE); }
        String recentText() {
            synchronized (recent) { return recent.isEmpty() ? "nothing" : String.join(" ; ", recent); }
        }
    }

    final Map<String, Observer> observers = new LinkedHashMap<>();

    private void registerObservers() {
        add(new ScreenObs());
        add(new ChatObs());
        add(new TitleObs());
        add(new ActionbarObs());
        add(new BossbarObs());
        add(new SubtitleObs());
        add(new SoundObs());
        add(new ErrorObs());
    }

    private void add(Observer o) { observers.put(o.name, o); }

    /** Record an observation of a kind: the kind's recent list (for FAIL details) and the step's "observed". */
    void note(String kind, String what) {
        String line = "+" + worldMs() + "ms " + what;
        Observer o = observers.get(kind);
        if (o != null) {
            synchronized (o.recent) {
                o.recent.addLast(line);
                while (o.recent.size() > RECENT) o.recent.removeFirst();
            }
        }
        synchronized (stepObserved) {
            Deque<String> d = stepObserved.computeIfAbsent(kind, k -> new ArrayDeque<>());
            d.addLast(line);
            while (d.size() > 20) d.removeFirst();
        }
    }

    /** Every text a component carries: its rendered string, then each translation key in its tree (args, siblings). */
    static List<String> texts(Component c) {
        List<String> out = new ArrayList<>();
        if (c == null) return out;
        out.add(c.getString());
        keys(c, out, 0);
        return out;
    }

    private static void keys(Component c, List<String> out, int depth) {
        if (c == null || depth > 8) return;
        if (c.getContents() instanceof TranslatableContents tc) {
            out.add(tc.getKey());
            for (Object a : tc.getArgs()) if (a instanceof Component ac) keys(ac, out, depth + 1);
        }
        for (Component s : c.getSiblings()) keys(s, out, depth + 1);
    }

    static String show(Component c) {
        List<String> t = texts(c);
        if (t.isEmpty()) return "";
        String s = "\"" + t.get(0) + "\"";
        return t.size() > 1 ? s + " " + t.subList(1, t.size()) : s;
    }

    /** match: a case-insensitive substring of any text, or /regex/ (case-insensitive find). */
    static boolean matches(String match, Collection<String> texts) {
        if (match == null || match.isEmpty()) return true;
        if (match.length() >= 2 && match.startsWith("/") && match.endsWith("/")) {
            Pattern p = Pattern.compile(match.substring(1, match.length() - 1), Pattern.CASE_INSENSITIVE);
            for (String t : texts) if (t != null && p.matcher(t).find()) return true;
            return false;
        }
        String m = match.toLowerCase(Locale.ROOT);
        for (String t : texts) if (t != null && t.toLowerCase(Locale.ROOT).contains(m)) return true;
        return false;
    }

    final class ScreenObs extends Observer {
        String last = "none";
        ScreenObs() { super("screen"); }
        String desc(Minecraft mc) {
            Screen s = mc.screen;
            return s == null ? "none" : s.getClass().getSimpleName() + " \"" + s.getTitle().getString() + "\"";
        }
        @Override void poll(PlaytestRunner r, Minecraft mc) {
            String d = desc(mc);
            if (!d.equals(last)) {
                note(name, "screen " + d);
                event("screen " + last + " -> " + d);
                last = d;
            }
        }
        @Override String holds(PlaytestRunner r, Minecraft mc, String match, int since) {
            Screen s = mc.screen;
            if (match.equalsIgnoreCase("none")) return s == null ? "no screen" : null;
            if (s == null) return null;
            List<String> t = new ArrayList<>(texts(s.getTitle()));
            t.add(s.getClass().getSimpleName());
            return matches(match, t) ? desc(mc) : null;
        }
    }

    /** Chat: every message the chat component received (system and player chat, death messages), with the tick. */
    final class ChatObs extends Observer {
        final List<Object[]> got = new ArrayList<>();     // {tick, List<String> texts, String shown}
        Object lastHead;
        String missing;
        ChatObs() { super("chat"); }
        @Override boolean isEvent() { return true; }
        @Override String unsupported(PlaytestRunner r, Minecraft mc) { return missing; }
        @Override void poll(PlaytestRunner r, Minecraft mc) {
            List<?> all = chatMessages(mc);
            if (all == null) { missing = "the chat component's message list (GuiMessage) was not found"; return; }
            missing = null;
            List<Object> fresh = new ArrayList<>();
            for (Object m : all) {
                if (m == lastHead) break;
                fresh.add(m);
            }
            if (!all.isEmpty()) lastHead = all.get(0);
            Collections.reverse(fresh);
            for (Object m : fresh) {
                Component c = R.componentOf(m);
                if (c == null) continue;
                got.add(new Object[]{r.tick, texts(c), show(c)});
                note(name, "chat " + show(c));
            }
            while (got.size() > 400) got.remove(0);
        }
        @Override String holds(PlaytestRunner r, Minecraft mc, String match, int since) {
            int from = Math.min(since, r.stepStartTick);
            for (int i = got.size() - 1; i >= 0; i--) {
                Object[] g = got.get(i);
                if ((int) g[0] < from) break;
                @SuppressWarnings("unchecked") List<String> t = (List<String>) g[1];
                if (matches(match, t)) return (String) g[2];
            }
            return null;
        }
        /** The first message received since tick that carries a death.* key and names the player, or null. */
        String deathOf(String player, int tick) {
            for (Object[] g : got) {
                if ((int) g[0] < tick) continue;
                @SuppressWarnings("unchecked") List<String> t = (List<String>) g[1];
                // the player is the VICTIM: a death message starts with its victim ("<player> was slain by ..."); one that
                // only names the player as the killer ("<mob> was destroyed by <player>") is a mob's death
                if (t.isEmpty() || !t.get(0).startsWith(player + " ")) continue;
                for (String k : t.subList(1, t.size())) if (k.startsWith("death.")) return k + " " + g[2];
            }
            return null;
        }

        /** The newest message carrying a death.* key received since tick, or null. */
        String deathSince(int tick) {
            for (int i = got.size() - 1; i >= 0; i--) {
                Object[] g = got.get(i);
                if ((int) g[0] < tick) break;
                @SuppressWarnings("unchecked") List<String> t = (List<String>) g[1];
                for (String k : t.subList(1, t.size())) if (k.startsWith("death.")) return k + " " + g[2];
            }
            return null;
        }
    }

    private List<?> chatMessages(Minecraft mc) {
        Object chat = R.reachable(mc, o -> o.getClass().getSimpleName().equals("ChatComponent"), "ChatComponent");
        if (chat == null) return null;
        for (Field f : R.fields(chat.getClass())) {
            if (!List.class.isAssignableFrom(f.getType())) continue;
            String g = f.getGenericType().getTypeName();
            if (g.endsWith("GuiMessage>")) {
                try { return new ArrayList<>((List<?>) f.get(chat)); } catch (Exception e) { return null; }
            }
        }
        return null;
    }

    /** Object holding Gui's title state (fields title, subtitle, titleTime), found once. */
    private Object titleHolder(Minecraft mc) {
        return R.reachable(mc, o -> R.field(o.getClass(), "titleTime") != null && R.field(o.getClass(), "title") != null, "title");
    }

    final class TitleObs extends Observer {
        String last = "";
        TitleObs() { super("title"); }
        @Override String unsupported(PlaytestRunner r, Minecraft mc) { return titleHolder(mc) == null ? "no GUI object with title/titleTime fields" : null; }
        List<Component> shown(Minecraft mc) {
            Object g = titleHolder(mc);
            List<Component> out = new ArrayList<>();
            if (g == null) return out;
            Object time = R.get(g, "titleTime");
            if (time instanceof Integer i && i <= 0) return out;
            for (String f : new String[]{"title", "subtitle"}) {
                if (R.get(g, f) instanceof Component c) out.add(c);
            }
            return out;
        }
        @Override void poll(PlaytestRunner r, Minecraft mc) {
            StringBuilder sb = new StringBuilder();
            for (Component c : shown(mc)) sb.append(show(c)).append(' ');
            String now = sb.toString().trim();
            if (!now.equals(last) && !now.isEmpty()) note(name, "title " + now);
            last = now;
        }
        @Override String holds(PlaytestRunner r, Minecraft mc, String match, int since) {
            for (Component c : shown(mc)) if (matches(match, texts(c))) return show(c);
            return null;
        }
    }

    final class ActionbarObs extends Observer {
        String last = "";
        ActionbarObs() { super("actionbar"); }
        Object holder(Minecraft mc) {
            return R.reachable(mc, o -> R.field(o.getClass(), "overlayMessageTime") != null, "actionbar");
        }
        @Override String unsupported(PlaytestRunner r, Minecraft mc) { return holder(mc) == null ? "no GUI object with overlayMessageString/Time fields" : null; }
        Component shown(Minecraft mc) {
            Object g = holder(mc);
            if (g == null) return null;
            Object time = R.get(g, "overlayMessageTime");
            if (time instanceof Integer i && i <= 0) return null;
            return R.get(g, "overlayMessageString") instanceof Component c ? c : null;
        }
        @Override void poll(PlaytestRunner r, Minecraft mc) {
            Component c = shown(mc);
            String now = c == null ? "" : show(c);
            if (!now.equals(last) && !now.isEmpty()) note(name, "actionbar " + now);
            last = now;
        }
        @Override String holds(PlaytestRunner r, Minecraft mc, String match, int since) {
            Component c = shown(mc);
            return c != null && matches(match, texts(c)) ? show(c) : null;
        }
    }

    final class BossbarObs extends Observer {
        String last = "";
        BossbarObs() { super("bossbar"); }
        Object overlay(Minecraft mc) { return R.reachable(mc, o -> o.getClass().getSimpleName().equals("BossHealthOverlay"), "BossHealthOverlay"); }
        @Override String unsupported(PlaytestRunner r, Minecraft mc) { return overlay(mc) == null ? "no BossHealthOverlay" : null; }
        List<Component> shown(Minecraft mc) {
            List<Component> out = new ArrayList<>();
            Object ov = overlay(mc);
            if (ov == null) return out;
            for (Field f : R.fields(ov.getClass())) {
                if (!Map.class.isAssignableFrom(f.getType())) continue;
                try {
                    for (Object ev : new ArrayList<>(((Map<?, ?>) f.get(ov)).values())) {
                        if (R.call(ev, "getName") instanceof Component c) out.add(c);
                    }
                } catch (Exception ignored) {
                }
            }
            return out;
        }
        @Override void poll(PlaytestRunner r, Minecraft mc) {
            StringBuilder sb = new StringBuilder();
            for (Component c : shown(mc)) sb.append(show(c)).append(' ');
            String now = sb.toString().trim();
            if (!now.equals(last) && !now.isEmpty()) note(name, "bossbar " + now);
            last = now;
        }
        @Override String holds(PlaytestRunner r, Minecraft mc, String match, int since) {
            for (Component c : shown(mc)) if (matches(match, texts(c))) return show(c);
            return null;
        }
    }

    /** The subtitle overlay's entries (subtitles are switched on for the run); a mod's own subtitle line in chat counts too. */
    final class SubtitleObs extends Observer {
        String last = "";
        SubtitleObs() { super("subtitle"); }
        Object overlay(Minecraft mc) { return R.reachable(mc, o -> o.getClass().getSimpleName().equals("SubtitleOverlay"), "SubtitleOverlay"); }
        @Override String unsupported(PlaytestRunner r, Minecraft mc) { return overlay(mc) == null ? "no SubtitleOverlay" : null; }
        List<Component> shown(Minecraft mc) {
            List<Component> out = new ArrayList<>();
            Object ov = overlay(mc);
            if (ov == null) return out;
            for (Field f : R.fields(ov.getClass())) {
                if (!List.class.isAssignableFrom(f.getType())) continue;
                try {
                    for (Object e : new ArrayList<>((List<?>) f.get(ov))) {
                        Component c = R.componentOf(e);
                        if (c != null) out.add(c);
                    }
                } catch (Exception ignored) {
                }
            }
            return out;
        }
        @Override void poll(PlaytestRunner r, Minecraft mc) {
            StringBuilder sb = new StringBuilder();
            for (Component c : shown(mc)) sb.append(show(c)).append(' ');
            String now = sb.toString().trim();
            if (!now.equals(last) && !now.isEmpty()) note(name, "subtitle " + now);
            last = now;
        }
        @Override String holds(PlaytestRunner r, Minecraft mc, String match, int since) {
            for (Component c : shown(mc)) if (matches(match, texts(c))) return show(c);
            String chat = observers.get("chat").holds(r, mc, match, since);
            return chat != null ? "(in chat) " + chat : null;
        }
        @Override String present(PlaytestRunner r, Minecraft mc, String match) {
            for (Component c : shown(mc)) if (matches(match, texts(c))) return show(c);
            return null;
        }
    }

    /** Every sound the SoundManager starts (a SoundEventListener), kept with its instance so "gone" can ask isActive. */
    final class SoundObs extends Observer {
        final List<Object[]> started = Collections.synchronizedList(new ArrayList<>());   // {tick, id, SoundInstance}
        String missing = "the sound listener was not installed yet";
        SoundObs() { super("sound"); }
        @Override String unsupported(PlaytestRunner r, Minecraft mc) {
            if (missing != null) return missing;
            Object engine = R.get(mc.getSoundManager(), "soundEngine");
            if (engine != null && Boolean.FALSE.equals(R.get(engine, "loaded"))) {
                return "the sound engine is not loaded (no audio device: the game logged 'Error starting SoundSystem'); on headless Linux "
                    + "start the client with ALSOFT_DRIVERS=null";
            }
            return null;
        }
        void install(Minecraft mc) {
            Object sm = mc.getSoundManager();
            Method add = R.method(sm.getClass(), "addListener", 1);
            if (add == null) { missing = "SoundManager has no addListener on this version"; return; }
            Class<?> iface = add.getParameterTypes()[0];
            InvocationHandler h = (proxy, m, args) -> {
                if (m.getDeclaringClass() == Object.class) return R.objectMethod(proxy, m, args, "playtest-sound-listener");
                if (args != null && args.length > 0 && args[0] instanceof SoundInstance si) {
                    Object id = R.call(si, "getLocation");
                    if (id == null) id = R.call(si, "getIdentifier");
                    String sid = String.valueOf(id);
                    Component sub = args.length > 1 && args[1] != null && R.call(args[1], "getSubtitle") instanceof Component c ? c : null;
                    started.add(new Object[]{tick, sid, si});
                    while (started.size() > 256) started.remove(0);
                    note(name, "sound " + sid + (sub != null ? " " + show(sub) : ""));
                }
                return R.defaultValue(m.getReturnType());
            };
            try {
                add.invoke(sm, Proxy.newProxyInstance(iface.getClassLoader(), new Class<?>[]{iface}, h));
                missing = null;
            } catch (Exception e) {
                missing = "adding a sound listener failed: " + e;
            }
        }
        boolean idMatches(String match, String id) {
            if (match.startsWith("/") && match.endsWith("/") && match.length() >= 2) return matches(match, List.of(id));
            String m = match.contains(":") || match.equals("*") ? match : "minecraft:" + match;
            if (m.endsWith("*")) return id.startsWith(m.substring(0, m.length() - 1));
            return id.equals(m);
        }
        boolean active(Minecraft mc, Object si) {
            Object a = R.call(mc.getSoundManager(), "isActive", si);
            return Boolean.TRUE.equals(a);
        }
        @Override void poll(PlaytestRunner r, Minecraft mc) { }
        @Override String holds(PlaytestRunner r, Minecraft mc, String match, int since) {
            synchronized (started) {
                for (int i = started.size() - 1; i >= 0; i--) {
                    Object[] s = started.get(i);
                    if (!idMatches(match, (String) s[1])) continue;
                    if ((int) s[0] >= since) return s[1] + " (started +" + ((int) s[0] - since) + "t)";
                    if (active(mc, s[2])) return s[1] + " (playing)";
                }
            }
            return null;
        }
        @Override String present(PlaytestRunner r, Minecraft mc, String match) {
            synchronized (started) {
                for (Object[] s : started) if (idMatches(match, (String) s[1]) && active(mc, s[2])) return s[1] + " (playing)";
            }
            return null;
        }
    }

    /** ERROR/FATAL log lines, and WARN lines that carry an exception, from a log4j appender on the root logger. */
    final class ErrorObs extends Observer {
        final List<Object[]> lines = Collections.synchronizedList(new ArrayList<>());   // {tick, text}
        String missing = "the log appender was not installed yet";
        ErrorObs() { super("error"); }
        @Override boolean isEvent() { return true; }
        @Override String unsupported(PlaytestRunner r, Minecraft mc) { return missing; }
        void install() {
            try {
                Class<?> lm = Class.forName("org.apache.logging.log4j.LogManager");
                Object ctx = lm.getMethod("getContext", boolean.class).invoke(null, false);
                Object conf = ctx.getClass().getMethod("getConfiguration").invoke(ctx);
                Object root = conf.getClass().getMethod("getRootLogger").invoke(conf);
                ClassLoader cl = ctx.getClass().getClassLoader();
                Class<?> app = Class.forName("org.apache.logging.log4j.core.Appender", false, cl);
                Class<?> state = Class.forName("org.apache.logging.log4j.core.LifeCycle$State", false, cl);
                Object started = enumValue(state, "STARTED");
                InvocationHandler h = (proxy, m, args) -> {
                    if (m.getDeclaringClass() == Object.class) return R.objectMethod(proxy, m, args, "playtest-errors");
                    switch (m.getName()) {
                        case "append" -> { record(args[0]); return null; }
                        case "getName" -> { return "smokeharness-playtest"; }
                        case "isStarted", "ignoreExceptions" -> { return true; }
                        case "isStopped" -> { return false; }
                        case "getState" -> { return started; }
                        default -> { return R.defaultValue(m.getReturnType()); }
                    }
                };
                Object proxy = Proxy.newProxyInstance(app.getClassLoader(), new Class<?>[]{app}, h);
                Method add = null;
                for (Method m : root.getClass().getMethods()) {
                    if (m.getName().equals("addAppender") && m.getParameterCount() == 3 && m.getParameterTypes()[0] == app) add = m;
                }
                if (add == null) { missing = "log4j LoggerConfig.addAppender not found"; return; }
                add.invoke(root, proxy, null, null);
                ctx.getClass().getMethod("updateLoggers").invoke(ctx);
                missing = null;
            } catch (Throwable t) {
                missing = "installing a log4j appender failed: " + t;
            }
        }
        void record(Object ev) {
            try {
                String level = String.valueOf(R.call(ev, "getLevel"));
                Object thrown = R.call(ev, "getThrown");
                boolean err = level.equals("ERROR") || level.equals("FATAL");
                if (!err && !(level.equals("WARN") && thrown != null)) return;
                Object msg = R.call(ev, "getMessage");
                String text = "[" + level + "] [" + R.call(ev, "getLoggerName") + "] " + (msg == null ? "" : R.call(msg, "getFormattedMessage"))
                    + (thrown != null ? " -- " + thrown : "");
                if (text.contains(TAG + ":")) return;   // our own report
                lines.add(new Object[]{tick, text});
                while (lines.size() > 400) lines.remove(0);
                note(name, text.length() > 300 ? text.substring(0, 300) + "..." : text);
            } catch (Throwable ignored) {
            }
        }
        boolean allowed(String text) {
            for (Pattern p : globalAllow) if (p.matcher(text).find()) return true;
            for (Pattern p : stepAllow) if (p.matcher(text).find()) return true;
            return false;
        }
        List<String> since(int tick, boolean includeAllowed) {
            List<String> out = new ArrayList<>();
            synchronized (lines) {
                for (Object[] l : lines) {
                    if ((int) l[0] < tick) continue;
                    String t = (String) l[1];
                    if (allowed(t)) { if (includeAllowed) out.add(t + " (allowed)"); } else out.add(t);
                }
            }
            return out;
        }
        @Override void poll(PlaytestRunner r, Minecraft mc) { }
        @Override String holds(PlaytestRunner r, Minecraft mc, String match, int since) {
            for (String t : since(Math.min(since, r.stepStartTick), false)) {
                if (match.isEmpty() || match.equals("*") || matches(match, List.of(t))) return t;
            }
            return null;
        }
    }

    // ═══════════════════════════════════════════════════════════════════════════════════ running
    int tick = 0;
    int stepStartTick = 0;
    private long worldStartMs = 0L;
    private int stepIdx = 0, opIdx = 0;
    private Op current;
    private boolean stepFailed = false;
    private String stepDetail = "";
    private long stepStartMs, lineStartMs;
    private final List<Pattern> stepAllow = new ArrayList<>();
    private final Map<String, Deque<String>> stepObserved = new LinkedHashMap<>();
    private final List<JsonObject> stepLines = new ArrayList<>();
    private final List<String> stepShots = new ArrayList<>();
    private final List<JsonObject> stepResults = new ArrayList<>();
    private final JsonArray events = new JsonArray();
    private final List<Shot> pendingShots = new ArrayList<>();
    private int shotIndex = 0;
    private int passed = 0;
    private boolean stopRequested = false;
    private boolean done = false;
    private boolean verdict = false;
    private enum Stage { SETUP, LINES, ENDING, FINISHED }
    private Stage stage = Stage.SETUP;
    private int endingTicks;
    private final List<String> setupCommands = new ArrayList<>();
    private volatile CmdResult setupRes;
    Entity aimed;
    private Object deadPlayer;
    private int deathTick = -1;
    private boolean respawnPending = false;

    long worldMs() { return worldStartMs == 0 ? 0 : System.currentTimeMillis() - worldStartMs; }

    void event(String what) {
        JsonObject e = new JsonObject();
        e.addProperty("ms", worldMs());
        e.addProperty("what", what);
        events.add(e);
    }

    /** The player is in the world: set the client up for a deterministic run and start the setup commands. */
    void begin(Minecraft mc) {
        worldStartMs = System.currentTimeMillis();
        try { Files.createDirectories(out); } catch (IOException e) { say(TAG + ": cannot create " + out + ": " + e); }
        registerObservers();
        ((ErrorObs) observers.get("error")).install();
        ((SoundObs) observers.get("sound")).install(mc);
        mc.options.pauseOnLostFocus = false;
        mc.options.showSubtitles().set(true);
        forceVolumes(mc);
        if (!mentionsImmediateRespawn) {
            // Death is handled by respawning at once; the gamerule's name changed in 1.21.11, so try both spellings.
            setupCommands.add("gamerule doImmediateRespawn true|gamerule minecraft:immediate_respawn true|gamerule immediate_respawn true");
        }
        say(TAG + ": script " + script + " (" + steps.size() + " step(s), intent: " + (intent.isEmpty() ? "-" : intent) + "), output " + out);
        event("world entered");
    }

    private void forceVolumes(Minecraft mc) {
        try {
            Class<?> ss = Class.forName("net.minecraft.sounds.SoundSource");
            Method m = R.method(mc.options.getClass(), "getSoundSourceOptionInstance", 1);
            if (m == null) return;
            for (Object src : ss.getEnumConstants()) {
                Object opt = m.invoke(mc.options, src);
                Object v = R.call(opt, "get");
                String n = ((Enum<?>) src).name();
                if (v instanceof Double d && (d < 0.05 || n.equals("MASTER") || n.equals("MUSIC")) && d < 1.0) {
                    R.call(opt, "set", 1.0);
                }
            }
        } catch (Throwable t) {
            say(TAG + ": could not raise the sound volumes: " + t);
        }
    }

    /** One client tick; true when the run is over (the verdict is then in {@link #passed()}). */
    boolean tick(Minecraft mc) {
        if (done) return true;
        tick++;
        for (Observer o : observers.values()) {
            try { o.poll(this, mc); } catch (Throwable ignored) { }
        }
        handleDeath(mc);
        switch (stage) {
            case SETUP -> setup(mc);
            case LINES -> lines(mc);
            case ENDING -> ending(mc);
            case FINISHED -> { }
        }
        return done;
    }

    private void setup(Minecraft mc) {
        if (setupRes == null && !setupCommands.isEmpty() && !"running".equals(setupState)) {
            String[] alts = setupCommands.get(0).split("\\|");
            setupState = "running";
            runCommand(mc, alts[setupAlt], x -> setupRes = x);
            return;
        }
        if ("running".equals(setupState)) {
            CmdResult x = setupRes;
            if (x == null) return;
            String[] alts = setupCommands.get(0).split("\\|");
            if (Boolean.TRUE.equals(x.success)) {
                event("setup: /" + alts[setupAlt]);
            } else if (setupAlt + 1 < alts.length) {
                setupAlt++; setupRes = null; setupState = ""; return;
            } else {
                event("setup: no immediate-respawn gamerule accepted (" + x.said() + "); deaths are respawned by the runner");
            }
            setupCommands.remove(0); setupAlt = 0; setupRes = null; setupState = "";
            return;
        }
        if (!setupCommands.isEmpty()) return;
        if (steps.isEmpty()) {
            finishRun(false, "the script has no steps");
            return;
        }
        stage = Stage.LINES;
        startStep(mc);
    }

    private String setupState = "";
    private int setupAlt = 0;

    private void startStep(Minecraft mc) {
        Step s = steps.get(stepIdx);
        opIdx = 0;
        current = null;
        stepFailed = false;
        stepDetail = "";
        stepStartTick = tick;
        stepStartMs = worldMs();
        stepAllow.clear();
        synchronized (stepObserved) { stepObserved.clear(); }
        stepLines.clear();
        stepShots.clear();
        say(TAG + ": STEP " + s.n + " " + s.name + " -- started" + (s.intent.isEmpty() ? "" : " (" + s.intent + ")"));
    }

    private void lines(Minecraft mc) {
        Step s = steps.get(stepIdx);
        // a few ops finish in start(): run up to 16 such in one tick, so header-ish lines cost nothing
        for (int guard = 0; guard < 16; guard++) {
            if (current == null) {
                if (opIdx >= s.ops.size() || stopRequested) { endStep(mc); return; }
                current = s.ops.get(opIdx);
                lineStartMs = worldMs();
                say(TAG + ":   [" + current.no + "] " + current.text);
                Res r;
                try { r = current.start(this, mc); } catch (Throwable t) { r = fail("threw " + t); }
                if (r == null) return;
                if (!lineDone(mc, r)) return;
                continue;
            }
            Res r;
            try { r = current.tick(this, mc); } catch (Throwable t) { r = fail("threw " + t); }
            if (r == null) return;
            if (!lineDone(mc, r)) return;
        }
    }

    /** Record a finished line; false when the step stops here. */
    private boolean lineDone(Minecraft mc, Res r) {
        JsonObject l = new JsonObject();
        l.addProperty("line", current.no);
        l.addProperty("text", current.text);
        l.addProperty("verdict", r.ok ? "PASS" : "FAIL");
        l.addProperty("detail", r.detail);
        l.addProperty("ms", worldMs());
        l.addProperty("took_ms", worldMs() - lineStartMs);
        stepLines.add(l);
        if (!r.ok) {
            say(TAG + ":   [" + current.no + "] FAIL -- " + r.detail);
            stepFailed = true;
            stepDetail = "line " + current.no + ": " + current.text + " -- " + r.detail;
            Shot sh = takeShot(mc, "step-" + steps.get(stepIdx).n + "-fail");
            stepShots.add(sh.file);
            current = null;
            endStep(mc);
            return false;
        }
        if (current instanceof ShotOp so) stepShots.add(so.shot.file);
        if (!r.detail.isEmpty()) say(TAG + ":   [" + current.no + "] ok -- " + r.detail);
        current = null;
        opIdx++;
        return true;
    }

    private void endStep(Minecraft mc) {
        Step s = steps.get(stepIdx);
        ErrorObs eo = (ErrorObs) observers.get("error");
        List<String> errs = eo.missing == null ? eo.since(stepStartTick, true) : List.of("(errors not observable: " + eo.missing + ")");
        if (!stepFailed && eo.missing == null) {
            List<String> bad = eo.since(stepStartTick, false);
            if (!bad.isEmpty()) {
                stepFailed = true;
                stepDetail = bad.size() + " new ERROR log line(s) in the step (allow with @allow error <regex>): " + bad.get(0);
            }
        }
        Shot end = takeShot(mc, "step-" + s.n + "-end");
        stepShots.add(end.file);
        if (!stepFailed) {
            passed++;
            stepDetail = s.ops.size() + " line(s)" + (stopRequested ? " (stopped by @end)" : "");
        }
        say(TAG + ": STEP " + s.n + " " + s.name + ": " + (stepFailed ? "FAIL" : "PASS") + " -- " + stepDetail);
        JsonObject j = new JsonObject();
        j.addProperty("n", s.n);
        j.addProperty("name", s.name);
        j.addProperty("intent", s.intent);
        j.addProperty("verdict", stepFailed ? "FAIL" : "PASS");
        j.addProperty("detail", stepDetail);
        j.addProperty("started_ms", stepStartMs);
        j.addProperty("ended_ms", worldMs());
        JsonArray ls = new JsonArray();
        for (JsonObject l : stepLines) ls.add(l);
        j.add("lines", ls);
        JsonObject obs = new JsonObject();
        synchronized (stepObserved) {
            for (Map.Entry<String, Deque<String>> e : stepObserved.entrySet()) {
                JsonArray a = new JsonArray();
                for (String x : e.getValue()) a.add(x);
                obs.add(e.getKey(), a);
            }
        }
        j.add("observed", obs);
        JsonArray sh = new JsonArray();
        for (String f : stepShots) sh.add(f);
        j.add("shots", sh);
        JsonArray er = new JsonArray();
        for (String e : errs) er.add(e);
        j.add("errors", er);
        stepResults.add(j);
        writeJson(null);
        boolean failed = stepFailed;
        stepIdx++;
        if (stepIdx >= steps.size() || stopRequested || (failed && !continueOnFail)) {
            stage = Stage.ENDING;
            endingTicks = 0;
            return;
        }
        startStep(mc);
    }

    private void ending(Minecraft mc) {
        boolean waiting = false;
        for (Shot s : pendingShots) if (!s.done) waiting = true;
        if (waiting && ++endingTicks < 200) return;
        finishRun(passed == steps.size(), null);
    }

    private void finishRun(boolean ok, String why) {
        verdict = ok;
        done = true;
        stage = Stage.FINISHED;
        writeJson(why);
        say(TAG + ": " + (ok ? "PASS" : "FAIL") + " -- " + passed + "/" + steps.size() + " steps" + (why != null ? " (" + why + ")" : "")
            + "; result " + out.resolve("playtest-result.json"));
    }

    boolean passed() { return verdict; }

    String summary() {
        return "playtest " + script.getFileName() + ": " + passed + "/" + steps.size() + " steps passed (" + out.resolve("playtest-result.json") + ")";
    }

    /** Fail the whole run before any step ran (no world, a lost player): still writes the JSON. */
    void abort(String why) {
        if (worldStartMs == 0) worldStartMs = System.currentTimeMillis();
        finishRun(false, why);
    }

    private void writeJson(String why) {
        try {
            Files.createDirectories(out);
            JsonObject root = new JsonObject();
            root.addProperty("script", script.toString());
            root.addProperty("intent", intent);
            boolean finished = done;
            // mid-run (rewritten at every step end) the verdict is FAIL until the run finishes: a client that dies mid-run failed
            root.addProperty("verdict", finished && verdict ? "PASS" : "FAIL");
            root.addProperty("finished", finished);
            if (why != null) root.addProperty("detail", why);
            root.addProperty("passed", passed);
            root.addProperty("total", steps.size());
            root.addProperty("continue_on_fail", continueOnFail);
            root.addProperty("out", out.toString());
            JsonArray st = new JsonArray();
            for (JsonObject j : stepResults) st.add(j);
            for (int i = stepResults.size(); i < steps.size(); i++) {
                if (!finished) break;
                JsonObject j = new JsonObject();
                j.addProperty("n", steps.get(i).n);
                j.addProperty("name", steps.get(i).name);
                j.addProperty("intent", steps.get(i).intent);
                j.addProperty("verdict", "SKIPPED");
                j.addProperty("detail", "not run: an earlier step failed and the script has no @continue-on-fail");
                j.addProperty("started_ms", -1);
                j.addProperty("ended_ms", -1);
                j.add("lines", new JsonArray());
                j.add("observed", new JsonObject());
                j.add("shots", new JsonArray());
                j.add("errors", new JsonArray());
                st.add(j);
            }
            root.add("steps", st);
            root.add("events", events);
            String text = new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create().toJson(root);
            Path tmp = out.resolve("playtest-result.json.tmp");
            Files.write(tmp, text.getBytes(StandardCharsets.UTF_8));
            Files.move(tmp, out.resolve("playtest-result.json"), StandardCopyOption.REPLACE_EXISTING);
        } catch (Throwable t) {
            say(TAG + ": could not write playtest-result.json: " + t);
        }
    }

    // ═══════════════════════════════════════════════════════════════════════════════════ death
    private void handleDeath(Minecraft mc) {
        var p = mc.player;
        if (p == null) return;
        // The death message about the player is the one sign every version gives: with immediate respawn the client can
        // be handed its new player before it ever saw health 0 (measured on 1.21.4, 1.21.5 and 26.1.2).
        String dm = ((ChatObs) observers.get("chat")).deathOf(p.getName().getString(), lastDeathScan);
        lastDeathScan = tick;
        if (dm != null && tick - lastDeathEvent > 40) {
            lastDeathEvent = tick;
            deathRecorded = true;
            event("death at step " + currentStepNo() + ": " + dm);
            note("death", dm);
            say(TAG + ": event player died at step " + currentStepNo() + ": " + dm);
        }
        if (p.isDeadOrDying() && p != deadPlayer) {
            deadPlayer = p;
            deathTick = tick;
            respawnPending = true;
            deathRecorded = tick - lastDeathEvent <= 40;
            if (!deathRecorded) say(TAG + ": event player died at step " + currentStepNo());
            return;
        }
        if (!respawnPending) return;
        if (p == deadPlayer) {
            // a few ticks for the kill packet (its death message), then respawn; again every 2 s while still dead
            int since = tick - deathTick;
            if (since == 5 || (since > 5 && since % 40 == 0)) {
                if (!deathRecorded) recordDeath(mc);
                p.respawn();
            }
            return;
        }
        if (!p.isDeadOrDying()) {
            if (!deathRecorded) recordDeath(mc);
            respawnPending = false;
            if (mc.screen instanceof DeathScreen) mc.setScreen(null);
            event("respawned at step " + currentStepNo());
            say(TAG + ": event player respawned at step " + currentStepNo());
        }
    }

    private boolean deathRecorded = false;
    private int lastDeathScan = 0, lastDeathEvent = -1000;

    private void recordDeath(Minecraft mc) {
        deathRecorded = true;
        lastDeathEvent = tick;
        String cause = deathCause(mc);
        event("death at step " + currentStepNo() + ": " + cause);
        note("death", cause);
    }

    private String deathCause(Minecraft mc) {
        if (mc.screen instanceof DeathScreen ds && R.get(ds, "causeOfDeath") instanceof Component c) return show(c);
        String chat = ((ChatObs) observers.get("chat")).deathSince(deathTick - 2);
        return chat != null ? chat : "(no death message seen)";
    }

    private int currentStepNo() { return stepIdx < steps.size() ? steps.get(stepIdx).n : steps.size(); }

    // ═══════════════════════════════════════════════════════════════════════════════════ commands
    /** Run a command as the player on the integrated server (its own command source, so the result and failure text come back). */
    void runCommand(Minecraft mc, String cmd, Consumer<CmdResult> done) {
        IntegratedServer server = mc.getSingleplayerServer();
        CmdResult res = new CmdResult();
        if (server == null || mc.player == null) {
            res.success = false;
            res.messages.add("no integrated server / player");
            done.accept(res);
            return;
        }
        UUID id = mc.player.getUUID();
        server.execute(() -> {
            try {
                ServerPlayer sp = server.getPlayerList().getPlayer(id);
                if (sp == null) {
                    res.success = false;
                    res.messages.add("the server has no such player");
                } else {
                    CommandSourceStack src = sp.createCommandSourceStack().withSource(sink(sp, res.messages))
                        .withCallback((ok, n) -> {
                            res.success = Boolean.TRUE.equals(res.success) || ok;
                            res.value += n;
                        });
                    server.getCommands().performPrefixedCommand(src, cmd);
                }
            } catch (Throwable t) {
                res.success = false;
                res.messages.add("threw " + t);
            }
            done.accept(res);
        });
    }

    /** A command source that keeps every message the command sends (its failure text) and shows it to the player too. */
    private static CommandSource sink(ServerPlayer sp, List<String> into) {
        InvocationHandler h = (proxy, m, args) -> {
            if (m.getDeclaringClass() == Object.class) return R.objectMethod(proxy, m, args, "playtest-command-source");
            if (args != null && args.length == 1 && args[0] instanceof Component c) {
                into.add(c.getString());
                sp.sendSystemMessage(c);
                return null;
            }
            if (m.getReturnType() == boolean.class) return m.getName().startsWith("accepts");
            return R.defaultValue(m.getReturnType());
        };
        return (CommandSource) Proxy.newProxyInstance(CommandSource.class.getClassLoader(), new Class<?>[]{CommandSource.class}, h);
    }

    /** Resolve an entity selector on the server as the player; the AimOp gets {id, x, y, z} per entity. */
    void selectEntities(Minecraft mc, String selector, AimOp op) {
        IntegratedServer server = mc.getSingleplayerServer();
        UUID id = mc.player.getUUID();
        server.execute(() -> {
            try {
                ServerPlayer sp = server.getPlayerList().getPlayer(id);
                var sel = EntityArgument.entities().parse(new StringReader(selector));
                List<double[]> out = new ArrayList<>();
                for (Entity e : sel.findEntities(sp.createCommandSourceStack())) out.add(new double[]{e.getId(), e.getX(), e.getY(), e.getZ()});
                op.found = out;
            } catch (Throwable t) {
                op.serverError = t.getMessage() != null ? t.getMessage() : t.toString();
            }
        });
    }

    // ═══════════════════════════════════════════════════════════════════════════════════ aim
    static String typeId(Entity e) {
        return String.valueOf(EntityType.getKey(e.getType()));
    }

    Res aimAt(Minecraft mc, Entity e) {
        Vec3 target = e instanceof LivingEntity ? e.getEyePosition() : e.getBoundingBox().getCenter();
        lookAt(mc.player, target);
        aimed = e;
        String crosshair = pickDesc(mc);
        note("aim", "aimed at " + typeId(e) + " #" + e.getId() + "; crosshair " + crosshair);
        return pass(String.format(Locale.ROOT, "aimed at %s #%d, %.1f blocks; crosshair %s", typeId(e), e.getId(),
            Math.sqrt(e.distanceToSqr(mc.player)), crosshair));
    }

    static void lookAt(Entity p, Vec3 target) {
        Vec3 eye = p.getEyePosition();
        double dx = target.x() - eye.x(), dy = target.y() - eye.y(), dz = target.z() - eye.z();
        float yaw = (float) (Math.toDegrees(Math.atan2(dz, dx)) - 90.0);
        float pitch = (float) (-Math.toDegrees(Math.atan2(dy, Math.sqrt(dx * dx + dz * dz))));
        setRot(p, yaw, pitch);
    }

    static void setRot(Entity p, float yaw, float pitch) {
        p.setYRot(yaw);
        p.setXRot(pitch);
        p.yRotO = yaw;
        p.xRotO = pitch;
        if (p instanceof LivingEntity le) {
            le.setYHeadRot(yaw);
            le.yHeadRotO = yaw;
        }
    }

    /** Re-pick the crosshair target now (the game does it each tick; this makes it current for the next key). */
    void pick(Minecraft mc) {
        try {
            Method m = R.method(mc.gameRenderer.getClass(), "pick", 1);
            if (m != null) m.invoke(mc.gameRenderer, 1.0F);
        } catch (Throwable ignored) {
        }
    }

    String pickDesc(Minecraft mc) {
        pick(mc);
        HitResult hr = mc.hitResult;
        if (hr instanceof EntityHitResult ehr) return "on " + typeId(ehr.getEntity()) + " #" + ehr.getEntity().getId();
        return hr == null ? "on nothing" : "on " + hr.getType().name().toLowerCase(Locale.ROOT);
    }

    // ═══════════════════════════════════════════════════════════════════════════════════ screenshots
    static final class Shot {
        String file, path, error;
        volatile boolean done;
    }

    Shot takeShot(Minecraft mc, String label) {
        Shot shot = new Shot();
        int idx = ++shotIndex;
        shot.file = String.format(Locale.ROOT, "%02d-%s.png", idx, label);
        Path dest = out.resolve(shot.file);
        shot.path = dest.toString();
        pendingShots.add(shot);
        String name = "playtest-" + UUID.randomUUID().toString().substring(0, 8) + "-" + label;
        Path written = mc.gameDirectory.toPath().resolve("screenshots").resolve("gatec-" + name + ".png");
        try {
            // The shape of the neoforge-mod template's shot(): the version rename tables (1.21.8's downscale argument,
            // 26.2's render target) anchor on it. The callback runs once the file is written.
            net.minecraft.client.Screenshot.grab(mc.gameDirectory, "gatec-" + name + ".png",
                mc.getMainRenderTarget(), msg -> {
                    try {
                        Files.createDirectories(dest.getParent());
                        Files.move(written, dest, StandardCopyOption.REPLACE_EXISTING);
                    } catch (Throwable t) {
                        shot.error = "not written (" + (msg == null ? "" : msg.getString()) + "): " + t;
                    }
                    shot.done = true;
                });
            say(TAG + ":   [SHOT] " + dest);
        } catch (Throwable t) {
            shot.error = "screenshot threw " + t;
            shot.done = true;
        }
        return shot;
    }

    // ═══════════════════════════════════════════════════════════════════════════════════ world
    /** The flat world preset's dimensions (the playtest's default world: deterministic terrain, fixed seed). */
    static WorldDimensions flatDimensions(net.minecraft.core.HolderLookup.Provider registries) {
        return registries.lookupOrThrow(Registries.WORLD_PRESET).getOrThrow(WorldPresets.FLAT).value().createWorldDimensions();
    }

    // ═══════════════════════════════════════════════════════════════════════════════════ input
    /** Real input: a key reaches the game through KeyboardHandler.keyPress (screens, InputEvent.Key, key mappings), a mouse
     *  button through MouseHandler's button callback; where a version's callback cannot be found, the mapping is set directly. */
    static final class Input {
        static final int PRESS = 1, RELEASE = 0;

        static String press(Minecraft mc, KeyMapping km, boolean down) {
            InputConstants.Key key = km.getKey();
            if (key == null || key.equals(InputConstants.UNKNOWN) || key.getValue() < 0) {
                direct(km, down);
                return "unbound: mapping set directly";
            }
            int action = down ? PRESS : RELEASE;
            long win = window(mc);
            try {
                if (key.getType() == InputConstants.Type.MOUSE) {
                    if (mouseButton(mc, win, key.getValue(), action)) return "mouse button " + key.getValue() + " via MouseHandler";
                    staticSet(key, down);
                    return "mouse button " + key.getValue() + " via KeyMapping.set/click (no MouseHandler callback found)";
                }
                boolean scancode = key.getType().name().equals("SCANCODE");   // 26.3 has no SCANCODE type
                int code = scancode ? -1 : key.getValue();
                int scan = scancode ? key.getValue() : 0;
                if (keyboard(mc, win, code, scan, action)) return "key " + key.getName() + " via KeyboardHandler.keyPress";
                staticSet(key, down);
                return "key " + key.getName() + " via KeyMapping.set/click (no KeyboardHandler.keyPress found)";
            } catch (Throwable t) {
                direct(km, down);
                return "mapping set directly (input path threw " + t + ")";
            }
        }

        static long window(Minecraft mc) {
            Object w = mc.getWindow();
            for (String n : new String[]{"getWindow", "handle", "getHandle", "window"}) {
                Method m = R.method(w.getClass(), n, 0);
                if (m != null && m.getReturnType() == long.class) {
                    try { return (long) m.invoke(w); } catch (Exception ignored) { }
                }
            }
            return 0L;
        }

        static boolean keyboard(Minecraft mc, long win, int code, int scan, int action) throws Exception {
            Object kh = mc.keyboardHandler;
            for (Method m : R.methods(kh.getClass())) {
                if (!m.getName().equals("keyPress")) continue;
                Class<?>[] p = m.getParameterTypes();
                if (p.length == 5 && p[0] == long.class) { m.invoke(kh, win, code, scan, action, 0); return true; }
                if (p.length == 3 && p[0] == long.class && p[1] == int.class) {
                    m.invoke(kh, win, action, build(p[2], new ArrayDeque<>(List.of((double) code, (double) scan, 0.0)))); return true;
                }
            }
            return false;
        }

        static boolean mouseButton(Minecraft mc, long win, int button, int action) throws Exception {
            Object mh = mc.mouseHandler;
            for (Method m : R.methods(mh.getClass())) {
                if (!m.getName().equals("onPress") && !m.getName().equals("onButton")) continue;
                Class<?>[] p = m.getParameterTypes();
                if (p.length == 4 && p[0] == long.class) { m.invoke(mh, win, button, action, 0); return true; }
                if (p.length == 3 && p[0] == long.class && !p[1].isPrimitive() && p[2] == int.class) {
                    m.invoke(mh, win, build(p[1], new ArrayDeque<>(List.of((double) button, 0.0))), action); return true;
                }
            }
            return false;
        }

        static void staticSet(InputConstants.Key key, boolean down) throws Exception {
            Method set = null, click = null;
            for (Method m : KeyMapping.class.getDeclaredMethods()) {
                if (!Modifier.isStatic(m.getModifiers())) continue;
                Class<?>[] p = m.getParameterTypes();
                if (m.getName().equals("set") && p.length == 2 && p[0] == InputConstants.Key.class) set = m;
                if (m.getName().equals("click") && p.length == 1 && p[0] == InputConstants.Key.class) click = m;
            }
            if (set == null || click == null) throw new IllegalStateException("no KeyMapping.set/click");
            set.setAccessible(true);
            click.setAccessible(true);
            set.invoke(null, key, down);
            if (down) click.invoke(null, key);
        }

        static void direct(KeyMapping km, boolean down) {
            km.setDown(down);
            if (down) {
                Field f = R.field(KeyMapping.class, "clickCount");
                try { if (f != null) f.setInt(km, f.getInt(km) + 1); } catch (Exception ignored) { }
            }
        }

        /** A click on a screen at (x, y) GUI coordinates through its mouseClicked/mouseReleased, whatever their shape. */
        static String clickScreen(Screen screen, double x, double y) throws Exception {
            for (String n : new String[]{"mouseClicked", "mouseReleased"}) {
                Method use = null;
                for (Method m : screen.getClass().getMethods()) {
                    if (!m.getName().equals(n)) continue;
                    Class<?>[] p = m.getParameterTypes();
                    if (p.length == 3 && p[0] == double.class) use = m;
                    else if (use == null && p.length >= 1 && !p[0].isPrimitive()) use = m;
                }
                if (use == null) throw new IllegalStateException("no " + n + " on " + screen.getClass().getSimpleName());
                Class<?>[] p = use.getParameterTypes();
                if (p.length == 3 && p[0] == double.class) use.invoke(screen, x, y, 0);
                else {
                    Object[] args = new Object[p.length];
                    args[0] = build(p[0], new ArrayDeque<>(List.of(x, y, 0.0, 0.0)));
                    for (int i = 1; i < p.length; i++) args[i] = R.defaultValue(p[i]);
                    use.invoke(screen, args);
                }
            }
            return "mouseClicked + mouseReleased";
        }

        /** Build a value of type t (a record/value class of numbers, possibly nested) from the numbers in order. */
        static Object build(Class<?> t, Deque<Double> vals) throws Exception {
            if (t == double.class) return vals.isEmpty() ? 0.0 : vals.poll();
            if (t == float.class) return (float) (vals.isEmpty() ? 0.0 : vals.poll());
            if (t == int.class) return (int) (vals.isEmpty() ? 0.0 : vals.poll());
            if (t == long.class) return (long) (vals.isEmpty() ? 0.0 : vals.poll());
            if (t == boolean.class) return false;
            Constructor<?> best = null;
            for (Constructor<?> c : t.getDeclaredConstructors()) {
                boolean ok = true;
                for (Class<?> p : c.getParameterTypes()) if (!p.isPrimitive() && p.getName().startsWith("java.")) ok = false;
                if (ok && (best == null || c.getParameterCount() > best.getParameterCount())) best = c;
            }
            if (best == null) throw new IllegalStateException("cannot build " + t.getName());
            best.setAccessible(true);
            Object[] args = new Object[best.getParameterCount()];
            Class<?>[] p = best.getParameterTypes();
            for (int i = 0; i < p.length; i++) args[i] = build(p[i], vals);
            return best.newInstance(args);
        }
    }

    // ═══════════════════════════════════════════════════════════════════════════════════ reflection
    static Object enumValue(Class<?> e, String name) {
        for (Object c : e.getEnumConstants()) if (((Enum<?>) c).name().equals(name)) return c;
        return null;
    }

    static String nearest(String want, List<String> names) {
        List<String> sorted = new ArrayList<>(names);
        sorted.sort((a, b) -> Integer.compare(dist(want, a), dist(want, b)));
        return String.join(", ", sorted.subList(0, Math.min(6, sorted.size())));
    }

    static int dist(String a, String b) {
        int[] prev = new int[b.length() + 1], cur = new int[b.length() + 1];
        for (int j = 0; j <= b.length(); j++) prev[j] = j;
        for (int i = 1; i <= a.length(); i++) {
            cur[0] = i;
            for (int j = 1; j <= b.length(); j++) {
                cur[j] = Math.min(Math.min(cur[j - 1] + 1, prev[j] + 1), prev[j - 1] + (a.charAt(i - 1) == b.charAt(j - 1) ? 0 : 1));
            }
            int[] t = prev; prev = cur; cur = t;
        }
        return prev[b.length()] - (b.contains(a) || a.contains(b) ? 100 : 0);
    }

    /** Reflection on Mojang's names, which NeoForge runs with (and 26.x ships). */
    static final class R {
        private static final Map<String, Object> FOUND = new LinkedHashMap<>();

        static List<Field> fields(Class<?> c) {
            List<Field> out = new ArrayList<>();
            for (Class<?> k = c; k != null && k != Object.class; k = k.getSuperclass()) {
                for (Field f : k.getDeclaredFields()) {
                    if (Modifier.isStatic(f.getModifiers())) continue;
                    try { f.setAccessible(true); } catch (Throwable t) { continue; }
                    out.add(f);
                }
            }
            return out;
        }

        static Field field(Class<?> c, String name) {
            for (Class<?> k = c; k != null && k != Object.class; k = k.getSuperclass()) {
                try {
                    Field f = k.getDeclaredField(name);
                    f.setAccessible(true);
                    return f;
                } catch (NoSuchFieldException ignored) {
                } catch (Throwable t) {
                    return null;
                }
            }
            return null;
        }

        static Object get(Object o, String name) {
            if (o == null) return null;
            Field f = field(o.getClass(), name);
            try { return f == null ? null : f.get(o); } catch (Exception e) { return null; }
        }

        static List<Method> methods(Class<?> c) {
            List<Method> out = new ArrayList<>();
            for (Class<?> k = c; k != null && k != Object.class; k = k.getSuperclass()) {
                for (Method m : k.getDeclaredMethods()) {
                    try { m.setAccessible(true); } catch (Throwable t) { continue; }
                    out.add(m);
                }
            }
            for (Method m : c.getMethods()) if (!out.contains(m)) out.add(m);
            return out;
        }

        static Method method(Class<?> c, String name, int params) {
            for (Method m : methods(c)) if (m.getName().equals(name) && m.getParameterCount() == params) return m;
            return null;
        }

        /** o.name(args) for the first method of that name and arity whose parameters accept the args; null if none / it threw. */
        static Object call(Object o, String name, Object... args) {
            if (o == null) return null;
            for (Method m : methods(o.getClass())) {
                if (!m.getName().equals(name) || m.getParameterCount() != args.length) continue;
                Class<?>[] p = m.getParameterTypes();
                boolean ok = true;
                for (int i = 0; i < p.length; i++) {
                    if (args[i] != null && !box(p[i]).isInstance(args[i])) ok = false;
                }
                if (!ok) continue;
                try { return m.invoke(o, args); } catch (Exception e) { return null; }
            }
            return null;
        }

        static Class<?> box(Class<?> p) {
            if (!p.isPrimitive()) return p;
            if (p == int.class) return Integer.class;
            if (p == long.class) return Long.class;
            if (p == double.class) return Double.class;
            if (p == float.class) return Float.class;
            if (p == boolean.class) return Boolean.class;
            if (p == short.class) return Short.class;
            if (p == byte.class) return Byte.class;
            return Character.class;
        }

        static Object defaultValue(Class<?> t) {
            if (t == boolean.class) return false;
            if (t == int.class) return 0;
            if (t == long.class) return 0L;
            if (t == double.class) return 0.0;
            if (t == float.class) return 0f;
            if (t == short.class) return (short) 0;
            if (t == byte.class) return (byte) 0;
            if (t == char.class) return (char) 0;
            return null;
        }

        static Object objectMethod(Object proxy, Method m, Object[] args, String name) {
            return switch (m.getName()) {
                case "equals" -> proxy == args[0];
                case "hashCode" -> System.identityHashCode(proxy);
                default -> name;
            };
        }

        /** The Component a GUI entry carries: its no-argument method returning one (GuiMessage.content(), Subtitle.getText()). */
        static Component componentOf(Object o) {
            if (o == null) return null;
            if (o instanceof Component c) return c;
            for (String n : new String[]{"content", "getText", "text", "getMessage"}) {
                Method m = method(o.getClass(), n, 0);
                if (m != null && Component.class.isAssignableFrom(m.getReturnType())) {
                    try { return (Component) m.invoke(o); } catch (Exception ignored) { }
                }
            }
            for (Method m : methods(o.getClass())) {
                if (m.getParameterCount() == 0 && Component.class.isAssignableFrom(m.getReturnType())) {
                    try { return (Component) m.invoke(o); } catch (Exception ignored) { }
                }
            }
            for (Field f : fields(o.getClass())) {
                if (Component.class.isAssignableFrom(f.getType())) {
                    try { return (Component) f.get(o); } catch (Exception ignored) { }
                }
            }
            return null;
        }

        /** The first object reachable from root through the game's own fields (3 levels) that passes the test; cached by key. */
        static Object reachable(Object root, java.util.function.Predicate<Object> test, String key) {
            Object hit = FOUND.get(key);
            if (hit != null) return hit;
            Map<Object, Boolean> seen = new IdentityHashMap<>();
            List<Object> level = new ArrayList<>(List.of(root));
            for (int depth = 0; depth <= 3 && !level.isEmpty(); depth++) {
                List<Object> next = new ArrayList<>();
                for (Object o : level) {
                    if (seen.put(o, Boolean.TRUE) != null) continue;
                    try {
                        if (test.test(o)) { FOUND.put(key, o); return o; }
                    } catch (Throwable ignored) {
                    }
                    for (Field f : fields(o.getClass())) {
                        if (f.getType().isPrimitive()) continue;
                        Object v;
                        try { v = f.get(o); } catch (Throwable t) { continue; }
                        if (v == null) continue;
                        String cn = v.getClass().getName();
                        if (cn.startsWith("net.minecraft.") || cn.startsWith("com.mojang.")) next.add(v);
                    }
                }
                level = next;
            }
            return null;
        }
    }
}
