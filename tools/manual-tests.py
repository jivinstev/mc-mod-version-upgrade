#!/usr/bin/env python3
"""The 10 highest-value MANUAL tests for a port: what no automated gate reaches, with complete steps to trigger it.

    python3 tools/manual-tests.py --repo <port clone> [--limit 10] [--format md|json]
    python3 tools/manual-tests.py --self-check

WHY.  The gates boot the game, load the data, spawn every creature and (Gate C) render it. They do not press a
key, swing a weapon in the off-hand, fight a boss to the end, open a block's screen or listen. Those paths are where
ports break unseen: a packet the client sends only on one action, a boss track that plays only in a fight. This
reads the port's own source and data and lists, per feature, the exact steps a person takes to exercise it.

WHAT IT LOOKS FOR (each a category; the list is picked ROUND-ROBIN across categories so ten tests cover ten
different kinds of thing, best candidate first in each):
  client-packet  a packet the CLIENT sends to the server (the crash class a server gate never sees); the trigger is
                 read off where it is sent from: a mixin into a vanilla input method, a keybind, a screen, an item.
  music          a streamed / music-channel sound and what plays it (the entity whose code starts it).
  boss           an entity with a boss bar: summon, fight, its bar, its death and drops.
  keybind        a key mapping the mod registers, with its default key and display name.
  screen         a custom GUI and what opens it.
  raid           a raid / wave event the mod defines, and what starts it.
  curio          an item worn in a Curios slot (needs Curios installed).
  recipe         a custom recipe type, with one real recipe from the mod's data.
  structure      a structure the mod generates: /locate it and visit.
  dimension      a dimension the mod adds.
Ids are resolved from the mod's own registration code (`CONST = X.register("id", ...)`), so steps name real
`/summon` and `/give` targets. Where a trigger cannot be resolved the step says what code to look at instead of
inventing one.

Standard library only; reads files, runs nothing.
"""
import argparse, json, pathlib, re, sys, tempfile

ORDER = ["music", "client-packet", "boss", "keybind", "screen", "raid", "curio", "recipe", "structure", "natural-spawn", "dimension"]
VANILLA_ACTIONS = {   # a mixin into one of these is how the player triggers it
    "startAttack": "attack (left-click) at nothing or a mob", "continueAttack": "hold left-click on a block",
    "startUseItem": "use (right-click) the item in hand", "pickBlock": "middle-click a block",
    "handleKeybinds": "press the mod's key", "swing": "swing the main or off hand", "attack": "attack a mob",
    "jumpFromGround": "jump", "aiStep": "move around normally (runs every tick)", "tick": "play normally (runs every tick)",
    "drop": "drop the held item (Q)", "keyPress": "press the mod's key", "onKeyPress": "press the mod's key",
    "mouseClicked": "click in the screen", "releaseUsing": "release right-click after charging",
}
EVENT_ACTIONS = {   # a packet sent from one of these client events: the player action that fires it
    "LoggingIn": "Join a world (single-player) and, if you can, a server -- it is sent on joining",
    "LoggingOut": "Leave the world back to the title screen",
    "Clone": "Die and respawn", "PlayerRespawnEvent": "Die and respawn",
    "InteractionKeyMappingTriggered": "Attack or use an item (left/right-click)",
    "Key": "Press the mod's key", "MouseButton": "Click the mouse in a world",
    "LeftClickEmpty": "Left-click at nothing (air)", "RightClickEmpty": "Right-click at nothing (air)",
    "LeftClickBlock": "Left-click a block", "RightClickItem": "Use (right-click) the item in hand",
    "ScreenEvent": "Open the screen it watches", "Post": "Play normally for a minute (it runs every tick)",
    "Pre": "Play normally for a minute (it runs every tick)",
}
FAIL_BLOCK = """### If a test fails

1. Note the test number and the step where it went wrong. Was there a crash, a disconnect ("Connection lost"), or
   just nothing happening?
2. Collect from the game folder (macOS: `~/Library/Application Support/minecraft`): the newest file in
   `crash-reports/` if there is one, and `logs/latest.log`.
3. Start Claude Code in a clone of the fork and paste: *"Manual test N failed at step S: <what happened>. Read
   logs/latest.log (and the crash report) from <game folder>, find the cause in this port, and tell me before
   changing anything."*
4. A fix is a commit on the port branch; CI re-runs every gate and a new release is published."""


def read(p):
    try:
        return pathlib.Path(p).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


class Mod:
    def __init__(self, repo):
        self.repo = pathlib.Path(repo)
        self.java = {f: read(f) for f in (self.repo / "src/main/java").rglob("*.java")}
        self.res = self.repo / "src/main/resources"
        toml = read(self.res / "META-INF/neoforge.mods.toml")
        m = (re.search(r'\[\[mods\]\][^\[]*?modId\s*=\s*"([a-z0-9_.-]+)"', toml, re.S)
             or re.search(r'^mod_id\s*=\s*([a-z0-9_]+)', read(self.repo / "gradle.properties"), re.M)
             or next((re.search(r'MOD_?ID\s*=\s*"([a-z0-9_]+)"', t) for t in self.java.values()
                      if re.search(r'MOD_?ID\s*=\s*"', t)), None))
        self.modid = m.group(1) if m else "mod"
        self.lang = {}
        for f in self.res.glob("assets/*/lang/en_us.json"):
            try:
                self.lang.update(json.loads(read(f)))
            except json.JSONDecodeError:
                pass
        # CONST -> registry id, and Class -> registry id, from the mod's registration code
        self.const_id, self.class_id = {}, {}
        for t in self.java.values():
            for m in re.finditer(r'\b([A-Z][A-Z0-9_]+)\s*=\s*(?:[\w.]+\.)?register\w*\(\s*"([a-z0-9_/]+)"([^;]*);', t, re.S):
                self.const_id[m.group(1)] = m.group(2)
                c = re.search(r"\b([A-Z]\w+)::new", m.group(3))
                if c:
                    self.class_id.setdefault(c.group(1), m.group(2))

    def name(self, kind, rid):
        return self.lang.get(f"{kind}.{self.modid}.{rid}") or rid.replace("_", " ")

    def entity_ids(self):
        return {rid for cls, rid in self.class_id.items() if cls.endswith("Entity")}

    def entity_for(self, text, hint):
        """The entity a sound/feature most likely belongs to: best word overlap with `hint` among the mod's entities
        (2+ shared words), else the first entity the code mentions."""
        words = set(hint.split("_")) - {"the", "of", "a", "is", "to"}
        best = max(self.entity_ids(), key=lambda r: (len(words & set(r.split("_"))), -len(r)), default=None)
        if best and len(words & set(best.split("_"))) >= min(2, len(words)):
            return best
        return self.entity_in(text)

    def entity_in(self, text):
        """An entity registry id the code mentions (ModEntities.X or an entity class name), or None."""
        for m in re.finditer(r"\b\w*Entit\w*\.([A-Z][A-Z0-9_]+)\b", text):
            if m.group(1) in self.const_id:
                return self.const_id[m.group(1)]
        for cls, rid in self.class_id.items():
            if cls.endswith("Entity") and re.search(r"\b" + cls + r"\b", text):
                return rid
        return None

    def rel(self, f):
        return str(pathlib.Path(f).relative_to(self.repo))


def cand(cat, score, title, why, steps, expect, where):
    return {"category": cat, "score": score, "title": title, "why": why, "steps": steps, "expect": expect, "where": where}


def summon(mod, rid):
    return f"Run `/summon {mod.modid}:{rid} ~ ~ ~5` (in Creative: `/gamemode creative` first if you want to stay alive)"


def d_client_packets(mod):
    out = []
    for f, t in mod.java.items():
        for m in re.finditer(r"sendToServer\(\s*(?:new\s+)?([A-Z]\w+)", t):
            payload = m.group(1)
            if len(payload) < 4 or payload.isupper():
                continue
            line = t.count("\n", 0, m.start()) + 1
            target = re.search(r"@Mixin\(\s*\{?\s*([\w.]+)\.class", t)
            method = None
            for mm in re.finditer(r'method\s*=\s*\{?\s*"(?:L[\w/$]+;)?(\w+)', t[:m.start()]):
                method = mm.group(1)
            action = VANILLA_ACTIONS.get(method or "", None)
            if target and action:
                how = f"In a world, {action}" + (" while holding the mod's item in the off-hand" if "Hand" in payload else "")
            elif ev := [e for e in re.findall(r"\(\s*([\w.]*Event[\w.]*)\s+\w+\s*\)\s*\{", t[max(0, m.start() - 600):m.start()])]:
                how = EVENT_ACTIONS.get(ev[-1].split(".")[-1], f"Do what makes the game fire `{ev[-1]}` (the method that sends it)")
            elif re.search(r"consumeClick|isDown\(\)", t[max(0, m.start() - 1500):m.start()]):
                keys = [mod.lang.get(k, k) for k in re.findall(r'new\s+KeyMapping\(\s*"([\w.]+)"', "".join(mod.java.values()))]
                how = ("Press the mod's key" + (f" (**{keys[0]}**" + (" or another of its keys" if len(keys) > 1 else "") + ")" if keys else "")
                       + " in the situation it is for; Options → Controls → Key Binds lists them")
            elif re.search(r"onPress|Button\.builder|new\s+Button\(", t[max(0, m.start() - 1500):m.start()]):
                scr = re.search(r"class\s+(\w+)", t)
                how = f"Open the mod's screen ({scr.group(1) if scr else pathlib.Path(f).stem}) and click the button that saves or applies it"
            elif re.search(r"extends\s+\w*Screen", t):
                how = f"Open the screen in `{pathlib.Path(f).stem}` and use each of its buttons"
            else:
                how = f"Do what `{pathlib.Path(f).stem}` handles (it sends {payload}); read that method to see when"
            out.append(cand("client-packet", 95, f"Client → server: {payload}",
                            "The client sends this packet only on one player action; a broken one disconnects the player, "
                            "and no server-side test ever sends it.",
                            [how, "Do it several times, in single-player and (if you can) on a server"],
                            "No disconnect, no crash; the action has its effect.",
                            f"{mod.rel(f)}:{line}"))
    return out


def d_music(mod):
    out = []
    streamed = set()
    for sj in mod.res.glob("assets/*/sounds.json"):
        try:
            data = json.loads(read(sj))
        except json.JSONDecodeError:
            continue
        for ev, v in data.items():
            if v.get("category") in ("music", "record") or any(isinstance(s, dict) and s.get("stream") for s in v.get("sounds", [])):
                streamed.add(ev)
    consts = {c for c, rid in mod.const_id.items() if rid in streamed}
    seen = set()
    for f, t in mod.java.items():
        for c in consts:
            if re.search(r"\b\w+\." + c + r"\b", t) and c not in seen and not re.search(r'register\w*\(\s*"' + mod.const_id[c], t):
                seen.add(c)
                rid = mod.const_id[c]
                ent = mod.entity_for(t, rid)
                steps = ["Options → Music & Sounds: set **Music** above 0 (this plays on the Music channel; a muted "
                         "slider looks exactly like a broken mod)"]
                if ent:
                    steps += [summon(mod, ent) + f" -- {mod.name('entity', ent)}",
                              "Stay near it / let it fight you for 20-30 seconds", "Then kill it (`/kill @e[type=" +
                              f"{mod.modid}:{ent}]`) or walk far away"]
                    expect = f"The '{rid}' track starts within a few seconds of the fight, and stops after."
                else:
                    steps += [f"Trigger what `{pathlib.Path(f).stem}` handles (it plays the track)"]
                    expect = f"The '{rid}' track plays."
                boss_ids = {mod.class_id.get(c) for c in boss_classes(mod)}
                out.append(cand("music", 90 - len(out) + (8 if ent in boss_ids else 0), f"Music: {rid.replace('_', ' ')}",
                                "Audio can only be checked by ear: a test can prove the file loads, not that it starts "
                                "at the right moment.", steps, expect, mod.rel(f)))
    return out


def boss_classes(mod):
    bossy = {re.search(r"class\s+(\w+)", t).group(1) for t in mod.java.values()
             if re.search(r"\bnew\s+\w*Boss\w*(?:Event|Info|Bar)\w*\(", t) and re.search(r"class\s+\w+", t)}
    for _ in range(3):   # subclasses of a boss-bar class are bosses too
        bossy |= {m.group(1) for t in mod.java.values() for m in re.finditer(r"class\s+(\w+)\s+extends\s+(\w+)", t)
                  if m.group(2) in bossy}
    return bossy


def d_boss(mod):
    out = []
    for cls in sorted(boss_classes(mod)):
        rid = mod.class_id.get(cls) if cls.endswith("Entity") else None
        f = next((g for g, t in mod.java.items() if re.search(r"class\s+" + cls + r"\b", t)), None)
        if rid and f:
            out.append(cand("boss", 80, f"Boss: {mod.name('entity', rid)}",
                            "Bosses run code no spawn test reaches: phases, special attacks, the death sequence, drops.",
                            [summon(mod, rid), "Fight it in Survival until it dies (or `/effect give @s resistance 999 4` "
                             "to survive the long fight)", "Pick up what it drops"],
                            "A boss bar with its name and health shows while near it and goes away when it dies; "
                            "it drops loot; no crash during any phase.", mod.rel(f)))
    return out[:4]


def d_keybind(mod):
    out = []
    for f, t in mod.java.items():
        for m in re.finditer(r'new\s+KeyMapping\(\s*"([\w.]+)"[^;]*?GLFW_KEY_(\w+)', t, re.S):
            key, default = m.group(1), m.group(2)
            label = mod.lang.get(key, key)
            out.append(cand("keybind", 75, f"Key: {label}",
                            "A keybind's handler runs only when a person presses it.",
                            [f"Options → Controls → Key Binds: find **{label}** (default key: {default}); rebind if it "
                             "clashes with another mod", f"In a world, press {default} in the situation it is for"],
                            "Its action happens; no crash or disconnect.", mod.rel(f)))
    return out


def d_screen(mod):
    out = []
    for f, t in mod.java.items():
        m = re.search(r"class\s+(\w+Screen)\s+extends\s+(\w*Screen)\b", t)
        if not m:
            continue
        cls = m.group(1)
        opener = next((mod.rel(g) for g, u in mod.java.items() if g != f and re.search(r"new\s+" + cls + r"\(", u)), None)
        out.append(cand("screen", 70 - len(out), f"Screen: {cls}",
                        "Screens render and react to clicks only on a real client with a person using them.",
                        [f"Open it" + (f" (it is opened from `{opener}`)" if opener else ""),
                         "Click every button, scroll, and close it with Esc"],
                        "It draws without missing textures or overlapping text; buttons do what they say; no crash.",
                        mod.rel(f)))
    return out


def d_raid(mod):
    out = []
    raiders = [mod.const_id.get(c, c.lower()) for t in mod.java.values()
               for c in re.findall(r"EnumProxy<\s*Raid\.RaiderType\s*>\s+([A-Z][A-Z0-9_]+)", t)]
    if raiders:
        out.append(cand("raid", 72, "Village raid with the mod's raiders",
                        "The mod adds its mobs to vanilla raid waves; that list is only read when a raid starts.",
                        ["Create a world (Survival, Normal difficulty)", "`/locate structure minecraft:village_plains` "
                         "and teleport there", "`/effect give @s minecraft:bad_omen 6000 0` and walk into the village "
                         "(it becomes Raid Omen and starts a raid)", "`/effect give @s resistance 999 4` and watch "
                         "several waves arrive"],
                        "The waves include the mod's raiders (" + ", ".join(r.replace("_", " ") for r in raiders[:6]) +
                        "); the raid bar fills and ends cleanly.", "META-INF/enumextensions.json"))
    for f, t in mod.java.items():
        m = re.search(r"class\s+(\w*Raid\w*)\s+extends\s+(\w*Raid\w*)", t)
        if not m:
            continue
        cls = m.group(1)
        users = [mod.rel(g) for g, u in mod.java.items() if g != f and re.search(r"\b" + cls + r"\b", u)]
        out.append(cand("raid", 70, f"Raid: {cls}",
                        "Waves, their spawning and the raid's end only run when the raid is actually started.",
                        [f"Start it (see what starts it in: {', '.join(users[:2]) or mod.rel(f)})",
                         "Survive or `/effect give @s resistance 999 4`; let every wave come", "Win, or walk away to end it"],
                        "Each wave spawns, any sound or bar for it plays and stops, and the raid ends cleanly.",
                        mod.rel(f)))
    return out


def d_curio(mod):
    out, ids = [], []
    for tag in mod.res.glob("data/curios/tags/item/*.json"):
        try:
            for v in json.loads(read(tag)).get("values", []):
                vid = v if isinstance(v, str) else v.get("id", "")
                if vid.startswith(mod.modid + ":") and not vid.startswith("#"):
                    ids.append((vid, tag.stem))
        except json.JSONDecodeError:
            pass
    for vid, slot in ids[:3]:
        rid = vid.split(":", 1)[1]
        out.append(cand("curio", 60 - len(out), f"Curio: {mod.name('item', rid)}",
                        "A worn curio's effect runs only while a player wears it, and needs the Curios mod.",
                        ["Install Curios API (the mod lists it as optional)", f"`/give @s {vid}`",
                         f"Open the inventory and put it in the **{slot}** curio slot", "Play for a minute, then take it off"],
                        "Its effect applies while worn and stops when removed; no crash.", "data/curios/tags/item/" + slot + ".json"))
    return out


def d_recipe(mod):
    out = []
    types = {}
    for f in mod.res.glob(f"data/{mod.modid}/recipe/**/*.json"):
        try:
            d = json.loads(read(f))
        except json.JSONDecodeError:
            continue
        typ = d.get("type", "") if isinstance(d, dict) else ""
        if typ.startswith(mod.modid + ":"):
            n = len(set(re.findall(r'"[a-z0-9_]+:[a-z0-9_/]+"', json.dumps(d))))
            if typ not in types or n > types[typ][2]:      # the example that names the most ingredients
                types[typ] = (f, d, n)
    for typ, (f, d, _) in sorted(types.items(), key=lambda kv: -kv[1][2])[:3]:
        items = sorted(set(re.findall(r'"(?:item|id)"\s*:\s*"([a-z0-9_]+:[a-z0-9_/]+)"', json.dumps(d)))
                       or {v for v in re.findall(r'"([a-z0-9_]+:[a-z0-9_/]+)"', json.dumps(d)) if v != typ})
        out.append(cand("recipe", 55, f"Recipe type: {typ}",
                        "A custom recipe type parses, syncs to the client and crafts through code a gate never runs.",
                        [f"`/give @s` each of: {', '.join(items[:6])}" if items else f"Its JSON names no ingredients (the rule is in the mod's code for `{typ}`): find the item or station that uses it",
                         f"Use them at the mod's station for `{typ}` (example: {mod.rel(f)})"],
                        "The recipe shows in the station and produces its result; no disconnect when joining a world.",
                        mod.rel(f)))
    return out


def d_structure(mod):
    out = []
    for f in sorted(mod.res.glob(f"data/{mod.modid}/worldgen/structure/**/*.json"))[:3]:
        sid = f"{mod.modid}:{f.stem}"
        out.append(cand("structure", 50, f"Structure: {f.stem.replace('_', ' ')}",
                        "Structures generate only in new chunks of a fresh world.",
                        ["Create a NEW world", f"`/locate structure {sid}`", "Teleport to the coordinates it prints "
                         "(click them, or `/tp @s X ~ Z`)", "Walk through it; open its chests"],
                        "It is found, looks complete, and its chests and mobs are there.", mod.rel(f)))
    return out


def d_spawns(mod):
    out = []
    for f in sorted(mod.res.glob(f"data/{mod.modid}/neoforge/biome_modifier/**/*.json")):
        try:
            d = json.loads(read(f))
        except json.JSONDecodeError:
            continue
        if d.get("type") != "neoforge:add_spawns":
            continue
        sp = d.get("spawners")
        sp = sp if isinstance(sp, list) else [sp] if sp else []
        ents = [x.get("type", "") for x in sp if isinstance(x, dict)]
        biomes = d.get("biomes")
        biome = biomes if isinstance(biomes, str) else (biomes[0] if isinstance(biomes, list) and biomes else "")
        if not ents or not biome:
            continue
        loc = (f"`/locate biome {biome}`" if not biome.startswith("#") else
               f"find a biome in the tag `{biome}` (e.g. `/locate biome` one of its members)")
        out.append(cand("natural-spawn", 48 - len(out), f"Natural spawning: {ents[0].split(':')[-1].replace('_', ' ')}",
                        "Natural spawns come from data a spawn test bypasses entirely.",
                        ["Create a NEW world (Survival, Normal)", loc + " and teleport there",
                         "`/time set night`, wait 1-2 minutes and look around (spawns need darkness and room)"],
                        f"{', '.join(e.split(':')[-1].replace('_', ' ') for e in ents)} appear naturally.", mod.rel(f)))
    return out[:3]


def d_dimension(mod):
    return [cand("dimension", 45, f"Dimension: {f.stem}", "A dimension loads only when someone enters it.",
                 [f"`/execute in {mod.modid}:{f.stem} run tp @s ~ 100 ~`", "Look around, then return"],
                 "It loads, renders and you can leave again.", mod.rel(f))
            for f in sorted(mod.res.glob(f"data/{mod.modid}/dimension/*.json"))[:2]]


DETECTORS = [d_client_packets, d_music, d_boss, d_keybind, d_screen, d_raid, d_curio, d_recipe, d_structure, d_spawns, d_dimension]


def pick(cands, limit):
    """Round-robin across categories (in ORDER), best first within each: diversity before depth."""
    seen, uniq = set(), []
    for x in cands:
        key = re.sub(r"\s*\d+$", "", x["title"])          # "Artifact Slot 1/2/3" is one test
        if key not in seen:
            seen.add(key); uniq.append(x)
    by = {c: sorted([x for x in uniq if x["category"] == c], key=lambda x: -x["score"]) for c in ORDER}
    out = []
    while len(out) < limit and any(by.values()):
        for c in ORDER:
            if by[c] and len(out) < limit:
                out.append(by[c].pop(0))
    return out


def tests(repo, limit=10):
    mod = Mod(repo)
    cands = [c for d in DETECTORS for c in d(mod)]
    return mod, pick(cands, limit), len(cands)


def markdown(mod, chosen, total):
    L = [f"## Manual tests (the {len(chosen)} highest-value)", "",
         f"What no automated gate reaches, picked for variety from {total} candidates found in this port's own code. "
         "Each is a few minutes; together they cover the paths where ports break unseen.", ""]
    for i, t in enumerate(chosen, 1):
        L += [f"### {i}. {t['title']}", "", f"*Why:* {t['why']}", ""]
        L += [f"{n}. {s}" for n, s in enumerate(t["steps"], 1)]
        L += ["", f"**Expect:** {t['expect']}", "", f"<sub>code: `{t['where']}`</sub>", ""]
    return "\n".join(L + [FAIL_BLOCK, ""])


FIXTURE = {
    "src/main/resources/META-INF/neoforge.mods.toml": '[[mods]]\nmodId="ex"\n',
    "src/main/resources/assets/ex/sounds.json": json.dumps({"boss_theme": {"sounds": [{"name": "ex:boss_theme", "stream": True}]}}),
    "src/main/resources/assets/ex/lang/en_us.json": json.dumps({"key.ex.dash": "Dash", "entity.ex.golem": "Stone Golem"}),
    "src/main/resources/data/curios/tags/item/ring.json": json.dumps({"values": ["ex:lucky_ring"]}),
    "src/main/resources/data/ex/worldgen/structure/tower.json": "{}",
    "src/main/java/com/example/Reg.java": 'class Reg {\n  static final Object GOLEM = ENTITIES.register("golem", () -> EntityType.Builder.of(GolemEntity::new));\n'
                                          '  static final Object BOSS_THEME = SOUNDS.register("boss_theme", () -> x);\n}\n',
    "src/main/java/com/example/GolemEntity.java": 'class GolemEntity {\n  ServerBossEvent bar = new ServerBossEvent(name);\n'
                                                 '  void tick() { play(ModSounds.BOSS_THEME); }\n}\n',
    "src/main/java/com/example/MinecraftMixin.java": '@Mixin(Minecraft.class)\nclass MinecraftMixin {\n'
                                                     '  @Inject(method = "startAttack", at = @At("HEAD"))\n'
                                                     '  void a() { PacketDistributor.sendToServer(new SwitchHandMessage()); }\n}\n',
    "src/main/java/com/example/Keys.java": 'class Keys { static final KeyMapping DASH = new KeyMapping("key.ex.dash", GLFW.GLFW_KEY_R, "c"); }\n',
}


def self_check():
    ok = True

    def chk(label, cond):
        nonlocal ok
        if not cond:
            print("FAIL:", label)
            ok = False

    with tempfile.TemporaryDirectory() as d:
        for rel, body in FIXTURE.items():
            p = pathlib.Path(d, rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
        mod, chosen, total = tests(d, 10)
        cats = [t["category"] for t in chosen]
        chk("one of each found category, music first", cats[:2] == ["music", "client-packet"]
            and set(cats) == {"client-packet", "music", "boss", "keybind", "curio", "structure"})
        md = markdown(mod, chosen, total)
        chk("mixin trigger read off the vanilla method", "attack (left-click)" in md and "off-hand" in md)
        chk("music names the Music slider and the summon", "**Music** above 0" in md and "/summon ex:golem" in md)
        chk("keybind shows its label and default", "**Dash** (default key: R)" in md)
        chk("curio and structure steps", "/give @s ex:lucky_ring" in md and "/locate structure ex:tower" in md)
        chk("failure block", "### If a test fails" in md)
        chk("cap honoured", len(tests(d, 3)[1]) == 3)
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo"); ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--format", choices=["md", "json"], default="md"); ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not a.repo:
        ap.error("--repo is required")
    mod, chosen, total = tests(a.repo, a.limit)
    print(json.dumps(chosen, indent=1) if a.format == "json" else markdown(mod, chosen, total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
