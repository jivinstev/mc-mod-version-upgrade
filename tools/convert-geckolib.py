#!/usr/bin/env python3
"""Convert a mod's GeckoLib 4.8.x (MC 1.21.1) source and assets to GeckoLib 5.5.x (MC 26.2).

    python3 tools/convert-geckolib.py --src src/main/java [--assets src/main/resources/assets] [--dry-run]
                                      [--index DIR ...] [--limb-swing-as-moving]
    python3 tools/convert-geckolib.py --hooks          # the hook signature table
    python3 tools/convert-geckolib.py --self-check

Run it on the 26.2 copy of the tree (an overlay or the prepared tree), not on the 1.21.1 source: the output
only compiles against 5.x. It accepts imports in either package, so it can run before or after the pipeline's
class-move table (software.bernie.geckolib -> com.geckolib); it adds only 5.x imports and never moves one.

What it converts (CATALOG section V18 and its entries V18b-d, V61, V65b, V72, V73, V88):
  * renderer headers gain the render-state type argument: GeoEntityRenderer<T> -> <T, S>, GeoArmorRenderer<T>
    -> <T, HumanoidRenderState>, GeoBlockRenderer<T> -> <T, BlockEntityRenderState>, wildcards <?> -> <?, ?>.
    S is LivingEntityRenderState when the entity class (or the type parameter's bound) is known to extend
    LivingEntity, EntityRenderState when it is known not to. When it is unknowable the tool keeps the renderer
    generic over its own `R extends EntityRenderState` (a type parameter bounded by a non-living type) or
    falls back to EntityRenderState, which is always safe (V88), and says which it chose.
    A mod renderer base that gained a state parameter makes every subclass supply it.
  * addRenderLayer -> withRenderLayer; layer classes get (T, O, R) and their renderer parameter follows; a raw
    `new Layer(this)` becomes a diamond.
  * hook signatures (table printed by --hooks): getRenderType, applyRotations, getDeathMaxRotation,
    getTextureLocation, getPackedOverlay, shouldShowName, isShaking (moved onto the render state),
    the hide-an-armor-bone renderRecursively idiom (-> adjustModelBonesForRender), layer
    getTextureResource/getRenderType, model getModelResource/getTextureResource (state-parked tickets).
    The old parameter names are bridged by locals at the top of the body ONLY where 5.x has the value.
  * animation: AnimationController drops its animatable argument, AnimationState -> AnimationTest,
    getController().setAnimation(..) -> setAnimation(..), Animation.LoopType -> LoopType, new DataTicket -> create,
    transitionLength -> setTransitionTicks, GeoRenderProvider.getGeoArmorRenderer/getGeoItemRenderer.
  * assets (V18c, --assets): geo/** -> geckolib/models/**, animations/** -> geckolib/animations/**, and the
    "geo/x.geo.json" / "animations/x.animation.json" id strings become the bare name "x".

--index DIR (repeatable) adds a read-only source root, so a renderer or layer base that a library mod supplies
is resolved (a dependent mod's renderers extend its library's). Without it such a base is not recognised and its subclasses are left alone.

Anything else is REFUSED by name with file:line, the reason and the catalogue entry, and left untouched:
render-pass overrides (preRender, actuallyRender, renderRecursively that is not the hide idiom, reRender, layer
render bodies), bone mutators outside a render pass, setCustomAnimations, util.Color, removed DataTickets,
limb-swing reads, and so on. Idempotent; standard library only.
"""
import argparse, collections, importlib.util, os, pathlib, re, shutil, subprocess, sys, tempfile

_here = pathlib.Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location("fs", _here / "forge-shapes.py")
fs = importlib.util.module_from_spec(_s); _s.loader.exec_module(fs)
_n = importlib.util.spec_from_file_location("ni", _here / "normalise-imports.py")
ni = importlib.util.module_from_spec(_n); _n.loader.exec_module(ni)

OLD, NEW = "software.bernie.geckolib", "com.geckolib"
GLPKG = r"(?:software\.bernie\.geckolib|com\.geckolib)"

IMPORTS = {
    "LivingEntityRenderState": "net.minecraft.client.renderer.entity.state.LivingEntityRenderState",
    "EntityRenderState": "net.minecraft.client.renderer.entity.state.EntityRenderState",
    "HumanoidRenderState": "net.minecraft.client.renderer.entity.state.HumanoidRenderState",
    "BlockEntityRenderState": "net.minecraft.client.renderer.blockentity.state.BlockEntityRenderState",
    "Identifier": "net.minecraft.resources.Identifier",
    "GeoRenderState": NEW + ".renderer.base.GeoRenderState",
    "GeoRenderer": NEW + ".renderer.base.GeoRenderer",
    "RenderPassInfo": NEW + ".renderer.base.RenderPassInfo",
    "BoneSnapshots": NEW + ".renderer.base.BoneSnapshots",
    "GeoBone": NEW + ".cache.model.GeoBone",
    "DataTickets": NEW + ".constant.DataTickets",
    "DataTicket": NEW + ".constant.dataticket.DataTicket",
    "AnimationTest": NEW + ".animation.state.AnimationTest",
    "LoopType": NEW + ".animation.object.LoopType",
    "GeoArmorRenderer": NEW + ".renderer.GeoArmorRenderer",
    "GeoItemRenderer": NEW + ".renderer.GeoItemRenderer",
}

# 5.x type argument lists. entity renderers choose their state from the entity class; armor and block are fixed.
FIXED_STATE = {"armor": "HumanoidRenderState", "block": "BlockEntityRenderState"}
GL_RENDERERS = {"GeoEntityRenderer": "entity", "GeoArmorRenderer": "armor", "GeoBlockRenderer": "block",
                "DyeableGeoArmorRenderer": "armor"}
GL_LAYERS = {"GeoRenderLayer", "AutoGlowingGeoLayer"}
GL_LAYERS_REFUSED = {"BlockAndItemGeoLayer", "ItemArmorGeoLayer", "BoneFilterGeoLayer", "FastBoneFilterGeoLayer"}
GL_MODELS = {"GeoModel", "DefaultedGeoModel", "DefaultedEntityGeoModel", "DefaultedBlockGeoModel", "DefaultedItemGeoModel"}
GL_REFUSED_TYPES = {
    "GeoReplacedEntityRenderer": ("V18", "GeoReplacedEntityRenderer<T, E, R> adds the related-object and state parameters; port by hand"),
    "GeoObjectRenderer": ("V18", "GeoObjectRenderer<T, O, R> needs the related-object and state types chosen by hand"),
    "DynamicGeoEntityRenderer": ("V18", "Dynamic*GeoRenderer classes are gone in 5.x (replaced by BlockAndItemGeoLayer / ItemArmorGeoLayer)"),
    "DynamicGeoBlockRenderer": ("V18", "Dynamic*GeoRenderer classes are gone in 5.x"),
    "DynamicGeoItemRenderer": ("V18", "Dynamic*GeoRenderer classes are gone in 5.x"),
}

# vanilla entity classes whose place in the LivingEntity hierarchy decides the render-state type
LIVING = set("""LivingEntity Mob PathfinderMob Monster Animal AgeableMob TamableAnimal AbstractIllager SpellcasterIllager
Zombie Skeleton AbstractSkeleton Creeper Raider AbstractVillager Villager WaterAnimal AbstractFish FlyingMob Slime
EnderMan Witch AbstractGolem IronGolem Spider Blaze Ghast Phantom Guardian Vex Husk Drowned ZombieVillager Piglin
PiglinBrute Hoglin Zoglin Strider Wolf Cat Parrot Bat Cow Pig Sheep Chicken Horse AbstractHorse Llama Bee Fox
Player AbstractClientPlayer ServerPlayer Pillager Vindicator Evoker Illusioner Ravager Shulker WitherBoss
EnderDragon ArmorStand Squid Dolphin Turtle Axolotl Goat Frog Camel Sniffer Warden Allay Bogged Breeze""".split())
NONLIVING = set("""Entity Projectile AbstractArrow Arrow ThrowableProjectile ThrowableItemProjectile Fireball
AbstractHurtingProjectile SmallFireball LargeFireball ItemEntity AreaEffectCloud Display Marker
Boat Minecart FallingBlockEntity PrimedTnt LightningBolt EvokerFangs""".split())

# Java text helpers ---------------------------------------------------------------------------------


def balanced_angle(masked, i):
    """Index of the '>' closing the '<' at masked[i], or -1."""
    depth, j = 0, i
    while j < len(masked):
        c = masked[j]
        if c == "<":
            depth += 1
        elif c == ">" and masked[j - 1] != "-":
            depth -= 1
            if depth == 0:
                return j
        elif c in ";{}":
            return -1
        j += 1
    return -1


def base_name(t):
    """Simple class name of a type expression: generics, qualifiers and array marks stripped."""
    t = re.sub(r"<.*>", "", t.strip()).replace("[]", "").strip()
    return t.split(".")[-1]


def split_bounds(s):
    return [base_name(b) for b in re.split(r"&", s) if b.strip()]


def parse_tparams(masked, text, span):
    """[(name, [bound simple names])] for a `<...>` type-parameter list at span=(open, close_inclusive)."""
    if not span:
        return []
    out = []
    for p in fs.split_args(text[span[0] + 1:span[1] - 1]):
        p = p.strip()
        m = re.match(r"([\w$]+)\s*(?:extends\s+(.*))?$", p, re.S)
        if m:
            out.append((m.group(1), split_bounds(m.group(2)) if m.group(2) else []))
    return out


CLASS_RE = re.compile(r"(?<![\w.$])(class|interface)\s+([\w$]+)")


def class_decls(text, masked=None):
    """Class declarations with their type parameters, extends clause and body span."""
    masked = masked if masked is not None else ni.code_spans(text)
    out = []
    for m in CLASS_RE.finditer(masked):
        i = m.end()
        while i < len(masked) and masked[i].isspace():
            i += 1
        tp = None
        if i < len(masked) and masked[i] == "<":
            j = balanced_angle(masked, i)
            if j < 0:
                continue
            tp = (i, j + 1)
            i = j + 1
        while i < len(masked) and masked[i].isspace():
            i += 1
        ext = None
        if re.match(r"extends\b", masked[i:i + 8]) and m.group(1) == "class":
            i += 7
            while masked[i].isspace():
                i += 1
            tm = re.match(r"[\w.$]+", masked[i:])
            if tm:
                name_span = (i, i + tm.end())
                k = i + tm.end()
                while k < len(masked) and masked[k].isspace():
                    k += 1
                args_span = None
                if k < len(masked) and masked[k] == "<":
                    e = balanced_angle(masked, k)
                    if e > 0:
                        args_span = (k, e + 1)
                        k = e + 1
                ext = (name_span, args_span)
                i = k
        bo = masked.find("{", i)
        if bo < 0:
            continue
        bc = fs.match(masked, bo)
        if bc < 0:
            continue
        d = {"kind": m.group(1), "name": m.group(2), "start": m.start(), "name_end": m.end(), "tp": tp, "ext": ext, "open": bo, "close": bc,
             "tparams": parse_tparams(masked, text, tp)}
        if ext:
            d["ext_name"] = base_name(text[ext[0][0]:ext[0][1]])
            d["ext_args"] = ([a.strip() for a in fs.split_args(text[ext[1][0] + 1:ext[1][1] - 1])] if ext[1] else [])
        else:
            d["ext_name"], d["ext_args"] = None, []
        out.append(d)
    return out


def anon_bodies(text, masked):
    """Anonymous class bodies: `new Base<..>(..) { .. }`."""
    out = []
    for m in re.finditer(r"(?<![\w$])new\s+([\w.$]+)", masked):
        i = m.end()
        while i < len(masked) and masked[i].isspace():
            i += 1
        args = None
        if i < len(masked) and masked[i] == "<":
            e = balanced_angle(masked, i)
            if e < 0:
                continue
            args = (i, e + 1)
            i = e + 1
            while i < len(masked) and masked[i].isspace():
                i += 1
        if i >= len(masked) or masked[i] != "(":
            continue
        pc = fs.match(masked, i)
        if pc < 0:
            continue
        k = pc + 1
        while k < len(masked) and masked[k].isspace():
            k += 1
        if k < len(masked) and masked[k] == "{":
            bc = fs.match(masked, k)
            if bc > 0:
                out.append({"name": None, "base": base_name(m.group(1)), "open": k, "close": bc,
                            "args": [a.strip() for a in fs.split_args(text[args[0] + 1:args[1] - 1])] if args else []})
    return out


def line_of(text, i):
    return text.count("\n", 0, i) + 1


def word_in(name, masked):
    return re.search(r"(?<![\w$.])%s(?![\w$])" % re.escape(name), masked) is not None


def free(name, *scopes):
    n, k = name, 2
    while any(word_in(n, s) for s in scopes):
        n, k = f"{name}{k}", k + 1
    return n


def apply_edits(text, edits):
    last = len(text) + 1
    for s, e, r in sorted(edits, key=lambda x: (-x[0], -x[1])):
        if e > last:
            continue                                    # overlaps an edit already applied
        text = text[:s] + r + text[e:]
        last = s
    return text


def ptypes(m):
    return [fs.ptype(p) for p in m.params]


def ptype_base(p):
    return base_name(fs.ptype(p))


def delete_method(text, m):
    """Remove method m with its annotations, keeping a single blank line between its neighbours."""
    ds = fs.decl_span_start(text, mstart(text, m))
    me = m.body_close + 1
    if text[ds - 1:ds] == "\n" and text[me:me + 1] == "\n":
        me += 1
    return text[:ds] + text[me:]


def mstart(text, m):
    """First real character of method m's declaration (fs.methods starts a match at the indentation)."""
    i = m.start
    while text[i] in " \t":
        i += 1
    return i


def indent_of(text, pos):
    ls = text.rfind("\n", 0, pos) + 1
    m = re.match(r"[ \t]*", text[ls:pos])
    return m.group(0)


def add_imports(text, names):
    body = ni.code_spans(re.sub(r"(?m)^import[^\n]*\n", "", text))
    for n in names:
        if n in IMPORTS and re.search(r"(?<![\w.$])%s(?![\w$])" % n, body) and not re.search(r"(?m)^import\s+[\w.]+\.%s\s*;" % n, text):
            text = fs.add_import(text, IMPORTS[n])
    return text


def is_gecko(text):
    return re.search(r"\b%s\b" % GLPKG, text) is not None


# Reporting -----------------------------------------------------------------------------------------


class Report:
    def __init__(self):
        self.counts = collections.Counter()
        self.refused = []                  # (path, line, tag, why, ref)
        self.notes = []                    # (path, line, text)
        self._seen = set()

    def refuse(self, path, line, tag, why, ref):
        if (path, line, tag) not in self._seen:
            self._seen.add((path, line, tag))
            self.refused.append((path, line, tag, why, ref))


# Index of the mod's own classes -----------------------------------------------------------------------


class Index:
    def __init__(self):
        self.classes = {}                  # simple name -> decl dict + 'text'
        self.members = {}                  # (class, member) -> return type text

    def add_file(self, text):
        masked = ni.code_spans(text)
        for d in class_decls(text, masked):
            d = dict(d)
            d["text"] = text
            self.classes[d["name"]] = d

    def chain(self, name):
        seen = set()
        while name and name not in seen:
            seen.add(name)
            yield name
            c = self.classes.get(name)
            name = c["ext_name"] if c else None

    def living(self, name):
        """True / False when the hierarchy settles it, None when it cannot be known."""
        for n in self.chain(name):
            if n in LIVING:
                return True
            if n in NONLIVING and n not in self.classes:
                return False
        return None

    def bound_living(self, bounds):
        return any(self.living(b) for b in bounds)

    def family(self, name):
        for n in self.chain(name):
            if n in GL_RENDERERS:
                return "renderer"
            if n in GL_LAYERS or n in GL_LAYERS_REFUSED:
                return "layer"
            if n in GL_MODELS:
                return "model"
        return None

    def member_type(self, cls, member, call):
        """Declared type of `member` (a no-arg method when call else a field) on cls or a mod superclass."""
        mods = r"(?:(?:public|protected|private|static|final)\s+)*"
        for n in self.chain(cls):
            c = self.classes.get(n)
            if not c:
                continue
            body = c["text"][c["open"]:c["close"]]
            if call:
                rx = r"(?m)^[ \t]*" + mods + r"([\w.$<>\[\]?, ]+?)\s+%s\s*\(\s*\)\s*(?:\{|;)" % re.escape(member)
            else:
                rx = r"(?m)^[ \t]*" + mods + r"([\w.$<>\[\]?, ]+?)\s+%s\s*(?:=|;)" % re.escape(member)
            for m in re.finditer(rx, body):
                ty = m.group(1).strip()
                if ty.split()[-1] not in ("return", "new", "else", "throw", "yield", "case", "assert"):
                    return ty
        return None


# Renderer state resolution ------------------------------------------------------------------------------


def renderer_spec(idx, name, memo=None):
    """How the renderer class `name` maps onto 5.x.

    kind: entity/armor/block; state: the render-state type (a concrete name or the class's own `R`);
    extra: the class gains its own `R extends EntityRenderState` parameter; ent: index of the entity-typed
    type parameter of this class (or None); n_old: type arguments users supplied before 5.x;
    ent_arg: the entity type argument as written; unknown: the entity class could not be resolved."""
    memo = {} if memo is None else memo
    if name in memo:
        return memo[name]
    memo[name] = None
    c = idx.classes.get(name)
    if not c or not c["ext_name"]:
        return None
    tps = [t[0] for t in c["tparams"]]
    base, args = c["ext_name"], c["ext_args"]
    spec = None
    if base in GL_RENDERERS:
        kind = GL_RENDERERS[base]
        if kind != "entity":
            spec = {"kind": kind, "state": FIXED_STATE[kind], "extra": False, "ent": None, "n_old": len(tps),
                    "unknown": False, "ent_arg": args[0] if args else ""}
        else:
            spec = _entity_state(idx, c, tps, args[0] if args else "", args[1] if len(args) >= 2 else None)
    else:
        p = renderer_spec(idx, base, memo)
        if p and p["extra"]:
            converted = len(args) >= p["n_old"] + 1
            ent = args[p["ent"]] if p["ent"] is not None and p["ent"] < len(args) else ""
            spec = _entity_state(idx, c, tps, ent, args[p["n_old"]] if converted else None)
        elif p:
            spec = dict(p, extra=False, n_old=len(tps), ent=None)
            if p["ent"] is not None and p["ent"] < len(args):
                spec["ent_arg"] = args[p["ent"]]
                if args[p["ent"]] in tps:
                    spec["ent"] = tps.index(args[p["ent"]])
    memo[name] = spec
    return spec


def _entity_state(idx, c, tps, ent, state_arg):
    ent_idx = tps.index(ent) if ent in tps else None
    extra = unknown = False
    if ent_idx is not None:
        if idx.bound_living(c["tparams"][ent_idx][1]):
            state = state_arg or "LivingEntityRenderState"
        elif state_arg:
            state, extra = state_arg, state_arg in tps
        else:
            state, extra = free("R", " ".join(tps)), True
    elif state_arg:
        state = state_arg
    else:
        lv = idx.living(base_name(ent)) if ent else None
        state = "LivingEntityRenderState" if lv else "EntityRenderState"
        unknown = lv is None
    n_old = len(tps) - (1 if extra and state_arg else 0)
    return {"kind": "entity", "state": state, "extra": extra, "ent": ent_idx, "n_old": n_old, "unknown": unknown,
            "ent_arg": ent}


# Phase 1: simple rewrites ----------------------------------------------------------------------------------


def phase_simple(text, rep, path):
    n = 0
    masked = ni.code_spans(text)
    edits = []
    for m in re.finditer(r"(?<![\w$])addRenderLayer(?=\s*\()", masked):
        prev = masked[:m.start()].rstrip()
        if prev.endswith((".", ";", "{", "}", ")")):
            edits.append((m.start(), m.end(), "withRenderLayer")); n += 1
    rep.counts["addRenderLayer -> withRenderLayer"] += n
    text = apply_edits(text, edits)

    # new DataTicket<..>(  ->  DataTicket.create(
    if re.search(r"import\s+%s\.constant\.dataticket\.DataTicket\s*;" % GLPKG, text):
        masked = ni.code_spans(text)
        edits, k = [], 0
        for m in re.finditer(r"(?<![\w$])new\s+DataTicket\s*(?=[<(])", masked):
            i = m.end()
            if masked[i] == "<":
                e = balanced_angle(masked, i)
                if e < 0:
                    continue
                i = e + 1
            while masked[i].isspace():
                i += 1
            if masked[i] == "(":
                edits.append((m.start(), i, "DataTicket.create")); k += 1
        text = apply_edits(text, edits)
        rep.counts["new DataTicket -> DataTicket.create"] += k

    # GeoArmorRenderer<?> / GeoEntityRenderer<?> / GeoBlockRenderer<?>
    masked = ni.code_spans(text)
    edits, k = [], 0
    for m in re.finditer(r"(?<![\w$.])(GeoArmorRenderer|GeoEntityRenderer|GeoBlockRenderer)\s*<\s*\?\s*>", masked):
        edits.append((m.start(), m.end(), m.group(1) + "<?, ?>")); k += 1
    text = apply_edits(text, edits)
    rep.counts["renderer wildcard <?> -> <?, ?>"] += k

    text = _animation(text, rep)
    text = _loop_type(text, rep)
    return text


def _loop_type(text, rep):
    """Animation.LoopType (nested in 4.x) -> the top-level LoopType."""
    k = 0
    nested = re.compile(r"(?m)^import\s+%s\.(?:core\.)?(?:animation|cache\.animation)\.Animation\.LoopType\s*;[ \t]*\n" % GLPKG)
    if nested.search(text):
        text = nested.sub("", text, count=1)
        text = fs.add_import(text, IMPORTS["LoopType"]); k += 1
    masked = ni.code_spans(text)
    edits = [(m.start(), m.end(), "LoopType") for m in re.finditer(r"(?<![\w$.])Animation\s*\.\s*LoopType(?![\w$])", masked)]
    if edits:
        text = apply_edits(text, edits); k += len(edits)
        text = fs.add_import(text, IMPORTS["LoopType"])
    rep.counts["Animation.LoopType -> LoopType"] += k
    return text


def _animation(text, rep):
    """AnimationState -> AnimationTest, controller constructor, controller calls."""
    k = 0
    imp = re.search(r"(?m)^import\s+%s\.(?:core\.)?animation\.AnimationState\s*;[ \t]*\n" % GLPKG, text)
    has_vanilla = re.search(r"(?m)^import\s+net\.minecraft\.[\w.]*\.AnimationState\s*;", text)
    if imp and not has_vanilla:
        text = text[:imp.start()] + text[imp.end():]
        masked = ni.code_spans(text)
        edits = [(m.start(), m.end(), "AnimationTest") for m in re.finditer(r"(?<![\w$.])AnimationState(?![\w$])", masked)]
        text = apply_edits(text, edits); k += len(edits)
        text = fs.add_import(text, IMPORTS["AnimationTest"])
    # AnimationController(this, ...) -> AnimationController(...)
    masked = ni.code_spans(text)
    edits = []
    is_lit = lambda x: x.strip().startswith('"')
    for m in re.finditer(r"(?<![\w$])new\s+AnimationController\s*(<[^()]*?>)?\s*\(", masked):
        op = m.end() - 1
        cl = fs.match(masked, op)
        if cl < 0:
            continue
        args = fs.split_args(text[op + 1:cl])
        a0 = args[0].strip() if args else ""
        drop = a0 == "this" or len(args) == 4 or (len(args) == 3 and is_lit(args[1]) and not is_lit(a0))
        if not drop:
            if len(args) in (2, 3) and not is_lit(a0) and not re.match(r"[\w$.]+::|\(|\w+\s*->", a0):
                rep.notes.append((None, line_of(text, m.start()), "AnimationController first argument is not `this`; check by hand whether it is the animatable"))
            continue
        lead = len(args[0]) - len(args[0].lstrip())
        s0, e0 = op + 1 + lead, op + 1 + len(args[0]) + 1
        tail = re.match(r"[ \t]*", text[e0:]).group(0)
        e0 += len(tail)
        if text[e0:e0 + 1] == "\n" and not text[text.rfind("\n", 0, s0) + 1:s0].strip():
            s0, e0 = text.rfind("\n", 0, s0) + 1, e0 + 1
        edits.append((s0, e0, "")); k += 1
    text = apply_edits(text, edits)
    k0 = k
    # state.getController().setAnimation(x) -> state.setAnimation(x); other getController() -> controller()
    masked = ni.code_spans(text)
    edits = []
    for m in re.finditer(r"\.getController\(\)(?=\s*\.\s*(?:setAnimation|setAndContinue|isCurrentAnimation|isCurrentAnimationStage)\s*\()", masked):
        edits.append((m.start(), m.end(), "")); k += 1
    for m in re.finditer(r"\.getController\(\)", masked):
        if not any(s <= m.start() < e for s, e, _ in edits):
            edits.append((m.start(), m.end(), ".controller()")); k += 1
    if "AnimationTest" in text or "AnimationController" in text:
        text = apply_edits(text, edits)
    else:
        k = k0
    rep.counts["animation API (AnimationState/controller)"] += k
    masked = ni.code_spans(text)
    edits = [(m.start(), m.end(), ".setTransitionTicks") for m in re.finditer(r"\.transitionLength(?=\s*\()", masked)]
    text = apply_edits(text, edits)
    rep.counts["transitionLength -> setTransitionTicks"] += len(edits)
    return text


# Phase 2: headers --------------------------------------------------------------------------------------


def phase_headers(text, idx, rep, path):
    """Renderer and layer class headers."""
    masked = ni.code_spans(text)
    edits, need = [], set()
    memo = {}
    for d in sorted(class_decls(text, masked), key=lambda x: -x["start"]):
        name, base, args = d["name"], d["ext_name"], d["ext_args"]
        if base is None:
            continue
        if base in GL_REFUSED_TYPES:
            continue
        fam = idx.family(name)
        if fam == "renderer":
            spec = renderer_spec(idx, name, memo)
            if not spec:
                continue
            parent = renderer_spec(idx, base, memo) if base not in GL_RENDERERS else None
            e = _renderer_header_edits(text, d, spec, parent, rep, path, need)
            edits += e
        elif fam == "layer":
            edits += _layer_header_edits(text, masked, d, idx, rep, path, need)
    text = apply_edits(text, edits)
    return add_imports(text, need)


def _renderer_header_edits(text, d, spec, parent, rep, path, need):
    args, ext = d["ext_args"], d["ext"]
    if parent is not None and not parent["extra"]:
        return []                                          # the mod base keeps its arity
    if ext[1] is None:
        rep.notes.append((path, line_of(text, d["start"]), f"{d['name']} extends {d['ext_name']} raw; give it its type arguments by hand"))
        return []
    required = 2 if parent is None else parent["n_old"] + 1
    if len(args) != required - 1:
        return []                                          # already converted, or a shape this tool does not know
    out = [(ext[1][1] - 1, ext[1][1] - 1, ", " + spec["state"])]
    if spec["extra"]:
        tp = d["tp"]
        out.append((tp[1] - 1, tp[1] - 1, f", {spec['state']} extends EntityRenderState"))
        need.add("EntityRenderState")
        rep.notes.append((path, line_of(text, d["start"]),
                          f"{d['name']}: entity type parameter is not known to be living; made the renderer generic over {spec['state']} extends EntityRenderState"))
    else:
        need.add(spec["state"])
    if spec["unknown"]:
        rep.notes.append((path, line_of(text, d["start"]),
                          f"{d['name']}: entity class is not resolvable from this tree; chose EntityRenderState (always safe, V88)"))
    rep.counts["renderer header (%s)" % spec["kind"]] += 1
    return out


def _layer_header_edits(text, masked, d, idx, rep, path, need):
    base, args, name, ext, tps = d["ext_name"], d["ext_args"], d["name"], d["ext"], d["tparams"]
    if base in GL_LAYERS_REFUSED:
        rep.refuse(path, line_of(text, d["start"]), f"layer {base}", f"{name} extends {base}, whose constructor, context and render hooks changed shape", "V18/V72")
        return []
    if ext[1] is None:
        return []
    if base in GL_LAYERS:
        if len(args) != 1:
            return []
        t0 = args[0]
        bounds = next((b for n, b in tps if n == t0), None)
        if bounds is None:                                  # a concrete animatable type
            bounds = [base_name(t0)]
            living = idx.living(bounds[0])
            if living is None and bounds[0] in idx.classes:
                living = True
            if living is not False and not idx.bound_living(bounds):
                bounds = bounds + ["Entity"]
        if any(b in ("Item", "GeoItem") for b in bounds):
            rep.refuse(path, line_of(text, d["start"]), "layer for items/armor",
                       f"{name}: the related-object type of an armor/item layer is GeoArmorRenderer.RenderData / GeoItemRenderer.RenderData; choose by hand", "V18")
            return []
        entityish = idx.bound_living(bounds) or any(b in ("BlockEntity", "GeoBlockEntity", "GeoEntity", "Entity", "Mob", "LivingEntity") for b in bounds)
        o = "Void" if entityish else "O"
        taken = " ".join(n for n, _ in tps)
        r = free("R", taken)
        add_tp = (", O" if o == "O" else "") + f", {r} extends GeoRenderState"
        out = [(ext[1][1] - 1, ext[1][1] - 1, f", {o}, {r}")]
        if d["tp"]:
            out.append((d["tp"][1] - 1, d["tp"][1] - 1, add_tp))
        else:
            out.append((d["name_end"], d["name_end"], "<" + add_tp[2:] + ">"))
        need.add("GeoRenderState"); need.add("GeoRenderer")
        body_s, body_e = d["open"], d["close"]
        for m in re.finditer(r"(?<![\w$.])GeoRenderer\s*<\s*(%s)\s*>" % re.escape(t0), masked[body_s:body_e]):
            out.append((body_s + m.start(), body_s + m.end(), f"GeoRenderer<{t0}, {o}, {r}>"))
        rep.counts["layer header"] += 1
        return out
    p = idx.classes.get(base)
    if p and idx.family(base) == "layer" and p["ext_name"] in GL_LAYERS and args:
        converted = len(p["ext_args"]) >= 3
        if converted and p["ext_args"][1] == "O":
            rep.refuse(path, line_of(text, d["start"]), "derived layer", f"{name} derives from a layer that is generic over O; choose the related-object type by hand", "V18")
            return []
        n_old = len(p["tparams"]) - (1 if converted else 0)
        if len(args) == n_old and d["tp"]:
            r = free("R", " ".join(n for n, _ in tps))
            need.add("GeoRenderState")
            rep.counts["layer header (derived)"] += 1
            return [(ext[1][1] - 1, ext[1][1] - 1, f", {r}"), (d["tp"][1] - 1, d["tp"][1] - 1, f", {r} extends GeoRenderState")]
    return []


# Phase 3: layer instantiation --------------------------------------------------------------------------


def phase_layer_new(text, idx, rep, path):
    """`new Layer(this ..)` of a converted layer becomes a diamond; `new Layer<X>(..)` gains the state argument."""
    masked = ni.code_spans(text)
    edits, k = [], 0
    layers = {n for n, c in idx.classes.items() if idx.family(n) == "layer" and c["ext_name"] not in GL_LAYERS_REFUSED
              and (c["tparams"] or (c["ext_name"] in GL_LAYERS and len(c["ext_args"]) == 1))}
    layers |= {"AutoGlowingGeoLayer"}
    for m in re.finditer(r"(?<![\w$.])new\s+([\w$]+)\s*(?=[<(])", masked):
        nm = m.group(1)
        if nm not in layers:
            continue
        i = m.end()
        if masked[i] == "(":
            edits.append((m.end(), m.end(), "<>")); k += 1
        elif masked[i] == "<" and masked[i:i + 2] != "<>":
            e = balanced_angle(masked, i)
            c = idx.classes.get(nm)
            if e < 0 or not c:
                continue
            given = [a.strip() for a in fs.split_args(text[i + 1:e])]
            converted = len(c["ext_args"]) >= 3
            n_old = len(c["tparams"]) - (1 if converted else 0)
            if len(given) == n_old:
                owner = _enclosing_renderer_state(text, masked, idx, m.start())
                if owner:
                    edits.append((e, e, ", " + owner)); k += 1
                else:
                    rep.refuse(path, line_of(text, m.start()), "layer type arguments",
                               f"new {nm}<..> needs the renderer's render-state type appended and none is known here", "V18")
    text = apply_edits(text, edits)
    rep.counts["layer instantiation (diamond / state argument)"] += k
    return text


def _enclosing_renderer_state(text, masked, idx, pos):
    best = None
    for d in class_decls(text, masked):
        if d["open"] < pos < d["close"] and idx.family(d["name"]) == "renderer":
            spec = renderer_spec(idx, d["name"])
            if spec and (best is None or d["open"] > best[0]):
                best = (d["open"], spec["state"])
    return best[1] if best else None


# Phase 4: hooks ----------------------------------------------------------------------------------------

REFUSED_RENDERER = {
    "preRender": ("V18/V61", "preRender(PoseStack, T, BakedGeoModel, ...) is a render-pass hook; become adjustRenderPose/scaleModelForRender and park what you need on the state at extract time"),
    "render": ("V18/V65b", "render(entity, ..) is the whole draw; 5.x records it: override submit/adjustRenderPose and read parked state"),
    "actuallyRender": ("V18", "actuallyRender is gone; use submitRenderTasks / preRenderPass"),
    "renderFinal": ("V18", "renderFinal is gone; use postRenderPass"),
    "renderRecursively": ("V18d", "renderRecursively that is not the hide-armor-bone idiom: per-bone work is a BoneUpdater / PerBoneRender on RenderPassInfo"),
    "reRender": ("V72", "reRender is gone; a layer re-draws through submitRenderTask / super.submitRenderTask"),
    "applyRenderLayers": ("V18", "applyRenderLayers is gone; layers are submitRenderTask"),
    "renderCubesOfBone": ("V18", "cube rendering moved to GeoBone.render; no override point"),
    "renderChildBones": ("V18", "child-bone rendering moved to GeoBone.renderChildren"),
    "renderCube": ("V18", "renderCube moved to the cuboid bone"),
    "createVerticesOfQuad": ("V18", "createVerticesOfQuad is internal in 5.x"),
    "defaultRender": ("V18", "defaultRender is gone; see performRenderPass"),
    "postRender": ("V18", "postRender -> postRenderPass(RenderPassInfo, SubmitNodeCollector)"),
    "doPostRenderCleanup": ("V18", "doPostRenderCleanup is gone"),
    "updateAnimatedTextureFrame": ("V18", "animated textures are handled by the library"),
    "renderLeash": ("V18", "leash drawing is vanilla's extract/submit now"),
    "prepForRender": ("V73", "prepForRender is gone; 5.x reads the wearer from the render state"),
    "getRenderColor": ("V18", "getRenderColor returns an int ARGB in 5.x and util.Color is gone: rewrite the body by hand"),
    "scaleModelForRender": ("V18", "scaleModelForRender(float,float,PoseStack,...) became scaleModelForRender(RenderPassInfo, float, float)"),
    "checkAndRefreshBuffer": ("V18", "buffers are recorded; there is no per-draw buffer to refresh"),
    "getTextureLocation": ("V61", "getTextureLocation reads the entity, which the render state does not carry; park the answer at extract time"),
    "isShaking": ("V18", "isShaking body is not a single `return expr;` of the entity"),
    "getRenderType": ("V18", "getRenderType body needs the entity or the buffer source, neither of which exists here"),
    "applyRotations": ("V18/V72", "applyRotations body needs the entity or changes the yaw it hands to super; 5.x takes (RenderPassInfo, PoseStack, nativeScale)"),
    "getDeathMaxRotation": ("V18", "getDeathMaxRotation reads the entity; 5.x passes the GeoRenderState"),
}
REFUSED_LAYER = {
    "render": ("V72", "layer render(..) became submitRenderTask(RenderPassInfo<R>, SubmitNodeCollector); a body that re-draws with reRender calls super.submitRenderTask"),
    "preRender": ("V72", "layer preRender(..) became preRender(RenderPassInfo<R>, SubmitNodeCollector)"),
    "renderForBone": ("V72", "renderForBone is gone; use addPerBoneRender"),
    "getTextureResource": ("V72", "layer getTextureResource reads the animatable; park the answer on the state in addRenderData"),
    "getRenderType": ("V72", "layer getRenderType reads the animatable; park the answer on the state in addRenderData"),
}
REFUSED_MODEL = {
    "setCustomAnimations": ("V18d/V75", "setCustomAnimations is gone; mutate bones in a BoneUpdater via RenderPassInfo.addBoneUpdater inside the render pass"),
    "getModelResource": ("V18", "getModelResource reads the animatable; park what it reads on the state in addAdditionalStateData"),
    "getTextureResource": ("V18", "getTextureResource reads the animatable; park what it reads on the state in addAdditionalStateData"),
    "getRenderType": ("V18", "GeoModel.getRenderType is gone; override the renderer's getRenderType"),
    "applyMolangQueries": ("V18", "applyMolangQueries(AnimationState, double) is gone; MolangQueries.setActorVariable"),
    "addAdditionalStateData": ("V18", "addAdditionalStateData(T, long, BiConsumer) became (T, Object, GeoRenderState)"),
    "handleAnimations": ("V18", "handleAnimations is gone"),
}


def _owner_of(text, masked, idx, m, bodies=None):
    """(kind, spec, label) of the innermost class body containing method m."""
    decls, anons = bodies if bodies else (class_decls(text, masked), anon_bodies(text, masked))
    best = None
    for d in decls:
        if d["open"] < m.start < d["close"] and (best is None or d["open"] > best[0]["open"]):
            best = (d, "named")
    for a in anons:
        if a["open"] < m.start < a["close"] and (best is None or a["open"] > best[0]["open"]):
            best = (a, "anon")
    if not best:
        return None, None, None
    d, how = best
    if how == "named":
        fam = idx.family(d["name"])
        spec = renderer_spec(idx, d["name"]) if fam == "renderer" else None
        return fam, spec, d["name"]
    fam = idx.family(d["base"])
    if fam == "renderer":
        spec = renderer_spec(idx, d["base"])
        return "renderer", spec, "anonymous " + d["base"]
    return fam, None, "anonymous " + d["base"]


class Result:
    def __init__(self, text=None, tag=None, why=None, ref=None):
        self.text, self.tag, self.why, self.ref = text, tag, why, ref


def phase_hooks(text, idx, rep, path, opts):
    """Convert every hook that has a complete mapping, then report what is left in the 4.x shape."""
    for _ in range(500):
        masked = ni.code_spans(text)
        changed = False
        bodies = (class_decls(text, masked), anon_bodies(text, masked))
        for m in sorted(fs.methods(text), key=lambda x: x.start):
            fam, spec, label = _owner_of(text, masked, idx, m, bodies)
            if fam is None:
                continue
            res = _hook(text, masked, idx, m, fam, spec, label, rep, path, opts)
            if res is not None and res.text is not None and res.text != text:
                text = res.text
                changed = True
                break
        if not changed:
            break
    masked = ni.code_spans(text)
    bodies = (class_decls(text, masked), anon_bodies(text, masked))
    for m in sorted(fs.methods(text), key=lambda x: x.start):
        fam, spec, label = _owner_of(text, masked, idx, m, bodies)
        if fam is None:
            continue
        res = _hook(text, masked, idx, m, fam, spec, label, rep, path, opts)
        if res is not None and res.text is None and res.why:
            rep.refuse(path, line_of(text, m.start), f"{label}.{m.name}", res.why, res.ref)
    return text


def _hook(text, masked, idx, m, fam, spec, label, rep, path, opts):
    ptys = ptypes(m)
    pn = [fs.pname(p) for p in m.params]
    bmask = masked[m.body_open + 1:m.body_close]
    body = text[m.body_open + 1:m.body_close]
    name = m.name
    if fam == "renderer":
        S = spec["state"] if spec else None
        if name == "getRenderType" and len(ptys) == 4 and ptype_base(m.params[2]) == "MultiBufferSource":
            return _h_get_render_type(text, m, pn, bmask, S, rep)
        if name == "applyRotations" and len(ptys) == 5 and ptype_base(m.params[1]) == "PoseStack":
            return _h_apply_rotations(text, m, pn, bmask, S, rep)
        if name == "getDeathMaxRotation" and len(ptys) == 1 and ptype_base(m.params[0]) != "GeoRenderState":
            return _h_death_rotation(text, m, pn, bmask, rep)
        if name == "getTextureLocation" and len(ptys) == 1 and ptype_base(m.params[0]) not in ("GeoRenderState",) and not (
                spec and ptype_base(m.params[0]) == spec["state"]):
            return _h_texture_location(text, m, pn, bmask, S, rep)
        if name == "getPackedOverlay" and len(ptys) == 3 and ptys[1] == "float":
            return _h_packed_overlay(text, m, pn, bmask, rep)
        if name == "shouldShowName" and len(ptys) == 1:
            return _h_show_name(text, m, pn, bmask, rep)
        if name == "isShaking" and len(ptys) == 1:
            return _h_is_shaking(text, masked, idx, m, pn, bmask, spec, rep)
        if name == "renderRecursively" and len(ptys) == 11:
            r = _h_hide_bone(text, m, pn, body, S, rep)
            return r
        if name in ("preRender", "render", "actuallyRender", "renderFinal", "reRender", "applyRenderLayers", "renderCubesOfBone",
                    "renderChildBones", "renderCube", "createVerticesOfQuad", "defaultRender", "postRender", "doPostRenderCleanup",
                    "updateAnimatedTextureFrame", "renderLeash", "prepForRender", "getRenderColor", "scaleModelForRender",
                    "checkAndRefreshBuffer"):
            if name == "render" and not any(ptype_base(p) == "MultiBufferSource" for p in m.params):
                return None
            if name == "getRenderColor" and len(ptys) != 3:
                return None
            if name == "scaleModelForRender" and len(ptys) < 4:
                return None
            return Result(None, name, *REFUSED_RENDERER[name])
        return None
    if fam == "layer":
        lr = _layer_r(text, masked, label)
        if name == "getTextureResource" and len(ptys) == 1 and ptype_base(m.params[0]) not in ("GeoRenderState", lr):
            return _h_layer_resource(text, m, pn, bmask, lr, rep)
        if name == "getRenderType" and len(ptys) == 1 and ptype_base(m.params[0]) not in ("GeoRenderState", lr):
            return _h_layer_render_type(text, m, pn, bmask, lr, rep)
        if name == "render" and any(ptype_base(p) == "MultiBufferSource" for p in m.params):
            return Result(None, name, *REFUSED_LAYER[name])
        if name == "preRender" and any(ptype_base(p) == "MultiBufferSource" for p in m.params):
            return Result(None, name, *REFUSED_LAYER[name])
        if name == "renderForBone":
            return Result(None, name, *REFUSED_LAYER[name])
        return None
    if fam == "model":
        if name in ("getModelResource", "getTextureResource") and len(ptys) == 1 and ptype_base(m.params[0]) != "GeoRenderState":
            return _h_model_resource(text, m, pn, bmask, rep)
        if name in ("getModelResource", "getTextureResource") and len(ptys) == 2:
            return Result(None, name, "the two-argument form is gone", "V18")
        if name in ("setCustomAnimations", "getRenderType", "applyMolangQueries", "handleAnimations") and len(ptys) >= 1:
            if name == "getRenderType" and len(ptys) != 2:
                return None
            return Result(None, name, *REFUSED_MODEL[name])
        if name == "addAdditionalStateData" and len(ptys) == 3 and ptys[1] == "long":
            return Result(None, name, *REFUSED_MODEL[name])
    return None


def _override_prefix(text, m):
    """'@Override\\n<indent>' when the method has no @Override yet."""
    ds = fs.decl_span_start(text, mstart(text, m))
    if "@Override" in text[ds:mstart(text, m)]:
        return ""
    return "@Override\n" + indent_of(text, mstart(text, m))


def _rebuild(text, m, new_ret, new_params, new_body=None, prefix=None):
    """Replace parameters (and optionally return type text / body) of method m."""
    ms = mstart(text, m)
    header = text[ms:m.params_open]
    if new_ret is not None:
        mm = re.search(r"([\w$.]+(?:<[^()]*>)?)(\s+[\w$]+\s*)$", header)
        header = header[:mm.start(1)] + new_ret + mm.group(2)
    pre = _override_prefix(text, m) if prefix is None else prefix
    body_part = text[m.params_close + 1:m.body_open + 1]
    if new_body is None:
        new_body = text[m.body_open + 1:m.body_close]
    return text[:ms] + pre + header + "(" + new_params + ")" + body_part + new_body + text[m.body_close:]


def _first_line_indent(body, default="         "):
    mm = re.match(r"\s*\n([ \t]*)\S", body)
    return mm.group(1) if mm else default


def _bridge(body, locals_):
    """Insert `locals_` (list of declaration lines) after the opening brace."""
    if not locals_:
        return body
    ind = _first_line_indent(body)
    return "".join(f"\n{ind}{l}" for l in locals_) + body


def _h_get_render_type(text, m, pn, bmask, S, rep):
    if not S:
        return Result(None, "getRenderType", "enclosing renderer's state type is unknown", "V18")
    a, t, b, pt = pn
    body = text[m.body_open + 1:m.body_close]
    b2 = re.sub(r"(?<![\w$])(?:this\s*\.\s*)?getTextureLocation\s*\(\s*%s\s*\)" % re.escape(a), t, body)
    mb = ni.code_spans(b2)
    if word_in(a, mb):
        return Result(None, "getRenderType", "body reads the animatable beyond getTextureLocation(animatable)", "V18")
    if word_in(b, mb):
        return Result(None, "getRenderType", "body uses the MultiBufferSource, which does not exist in 5.x", "V18")
    st = free("renderState", mb, t)
    locals_ = [f"float {pt} = {st}.partialTick;"] if word_in(pt, mb) else []
    tn = t
    new = _rebuild(text, m, None, f"{S} {st}, Identifier {tn}", _bridge(b2, locals_))
    rep.counts["hook getRenderType"] += 1
    return Result(_with(new, S, "Identifier"))


def _with(text, *names):
    return add_imports(text, [n for n in names if n in IMPORTS])


WOBBLE = (r"\s*if\s*\(\s*(?:this\s*\.\s*)?isShaking\s*\(\s*{E}\s*\)\s*\)\s*\{{\s*{Y}\s*\+=\s*\(float\)\s*\(\s*Math\.cos\s*\(\s*\(double\)\s*{E}\.tickCount\s*\*\s*3\.25\s*\)"
          r"\s*\*\s*Math\.PI\s*\*\s*0\.4F?\s*\)\s*;\s*\}}\s*super\s*\.\s*applyRotations\s*\(\s*{E}\s*,\s*{P}\s*,\s*{A}\s*,\s*{Y}\s*,\s*{T}\s*\)\s*;\s*")


def _wobble_methods(text, masked):
    """applyRotations overrides that only add the shake wobble 5.x applies itself from DataTickets.IS_SHAKING (V72)."""
    out = []
    for m in fs.methods(text):
        if m.name == "applyRotations" and len(m.params) == 5:
            e, p, a, y, t = [fs.pname(x) for x in m.params]
            rx = WOBBLE.format(E=re.escape(e), P=re.escape(p), A=re.escape(a), Y=re.escape(y), T=re.escape(t))
            if re.fullmatch(rx, masked[m.body_open + 1:m.body_close]):
                out.append(m)
    return out


def _h_apply_rotations(text, m, pn, bmask, S, rep):
    if any(w.start == m.start for w in _wobble_methods(text, ni.code_spans(text))):
        new = delete_method(text, m)
        rep.counts["hook applyRotations (shake wobble: 5.x applies it from IS_SHAKING) -> removed"] += 1
        return Result(new)
    if not S:
        return Result(None, "applyRotations", "enclosing renderer's state type is unknown", "V18")
    ent, ps, age, yaw, ptk = pn
    body = text[m.body_open + 1:m.body_close]
    mb = ni.code_spans(body)
    ident = re.fullmatch(r"\s*(?:float\s+(\w+)\s*=\s*1(?:\.0)?F?\s*;\s*)?%s\s*\.\s*scale\s*\(\s*(?:\1|1(?:\.0)?F?)\s*,\s*(?:\1|1(?:\.0)?F?)\s*,\s*(?:\1|1(?:\.0)?F?)\s*\)\s*;\s*super\s*\.\s*applyRotations\s*\(\s*%s\s*,\s*%s\s*,\s*%s\s*,\s*%s\s*,\s*%s\s*\)\s*;\s*"
                         % tuple(re.escape(x) for x in (ps, ent, ps, age, yaw, ptk)), mb) if True else None
    if ident:
        rep.counts["hook applyRotations (identity scale) -> removed"] += 1
        return Result(delete_method(text, m))
    sup = re.search(r"(?<![\w$])super\s*\.\s*applyRotations\s*\(", mb)
    info, scale = free("renderPassInfo", mb, ps), free("nativeScale", mb, ps)
    b2 = body
    if sup:
        op = sup.end() - 1
        cl = fs.match(mb, op)
        args = [x.strip() for x in fs.split_args(body[op + 1:cl])]
        if args != [ent, ps, age, yaw, ptk]:
            return Result(None, "applyRotations", "super.applyRotations is not called with the method's own unchanged parameters (yaw/age changes cannot be handed to 5.x)", "V18/V72")
        b2 = body[:op + 1] + f"{info}, {ps}, {scale}" + body[cl:]
    elif not re.search(r"(?<![\w$])return\b", mb) and False:
        pass
    mb2 = ni.code_spans(b2)
    if word_in(ent, mb2):
        return Result(None, "applyRotations", "body reads the entity, which the render state does not carry", "V18/V61")
    if re.search(r"(?<![\w$])%s\s*(?:[-+*/]?=(?!=)|\+\+|--)" % re.escape(yaw), mb2):
        return Result(None, "applyRotations", "body changes the yaw it was given; 5.x derives the yaw inside applyRotations (override the ticket ENTITY_BODY_YAW at extract time instead)", "V18/V72")
    locals_ = []
    if word_in(age, mb2):
        locals_.append(f"float {age} = {info}.renderState().ageInTicks;")
    if word_in(yaw, mb2):
        locals_.append(f"float {yaw} = {info}.renderState().getOrDefaultGeckolibData(DataTickets.ENTITY_BODY_YAW, 0.0F);")
    if word_in(ptk, mb2):
        locals_.append(f"float {ptk} = {info}.renderState().partialTick;")
    new = _rebuild(text, m, None, f"RenderPassInfo<{S}> {info}, PoseStack {ps}, float {scale}", _bridge(b2, locals_))
    rep.counts["hook applyRotations"] += 1
    return Result(_with(new, "RenderPassInfo", "DataTickets") if word_in("DataTickets", ni.code_spans(new)) else _with(new, "RenderPassInfo"))


def _h_death_rotation(text, m, pn, bmask, rep):
    if word_in(pn[0], bmask):
        return Result(None, "getDeathMaxRotation", "body reads the entity", "V18")
    st = free("renderState", bmask)
    new = _rebuild(text, m, None, f"GeoRenderState {st}")
    rep.counts["hook getDeathMaxRotation"] += 1
    return Result(_with(new, "GeoRenderState"))


def _h_texture_location(text, m, pn, bmask, S, rep):
    if not S:
        return Result(None, "getTextureLocation", "enclosing renderer's state type is unknown", "V61")
    body = text[m.body_open + 1:m.body_close]
    p = pn[0]
    if re.fullmatch(r"\s*return\s+super\s*\.\s*getTextureLocation\s*\(\s*%s\s*\)\s*;\s*" % re.escape(p), ni.code_spans(body)):
        rep.counts["hook getTextureLocation (bare delegation) -> removed"] += 1
        return Result(delete_method(text, m))
    b2 = re.sub(r"(?<![\w$])super\s*\.\s*getTextureLocation\s*\(\s*%s\s*\)" % re.escape(p), "super.getTextureLocation(@@ST@@)", body)
    mb = ni.code_spans(b2)
    if word_in(p, mb):
        return Result(None, "getTextureLocation", "body reads the entity, which the render state does not carry", "V61")
    st = free("renderState", mb)
    b2 = b2.replace("@@ST@@", st)
    ret = "Identifier" if base_name(m.ret) in ("ResourceLocation", "Identifier") else None
    if ret is None:
        return Result(None, "getTextureLocation", "unexpected return type", "V61")
    new = _rebuild(text, m, "Identifier", f"{S} {st}", b2)
    rep.counts["hook getTextureLocation"] += 1
    return Result(_with(new, S, "Identifier"))


def _h_packed_overlay(text, m, pn, bmask, rep):
    ro = free("relatedObject", bmask)
    params = m.params[:1] + [f"Void {ro}"] + m.params[1:]
    new = _rebuild(text, m, None, ", ".join(params))
    rep.counts["hook getPackedOverlay"] += 1
    return Result(new)


def _h_show_name(text, m, pn, bmask, rep):
    d = free("distanceSqr", bmask)
    new = _rebuild(text, m, None, ", ".join(m.params + [f"double {d}"]))
    rep.counts["hook shouldShowName"] += 1
    return Result(new)


def _h_is_shaking(text, masked, idx, m, pn, bmask, spec, rep):
    if not spec:
        return Result(None, "isShaking", "renderer state type is unknown", "V18")
    p = pn[0]
    body = text[m.body_open + 1:m.body_close].strip()
    mm = re.fullmatch(r"return\s+(.+?);", body, re.S)
    if not mm:
        return Result(None, "isShaking", "body is not a single `return expr;`", "V18")
    ent_param_t = spec.get("ent_arg") or fs.ptype(m.params[0])
    wob = [(w.start, w.body_close) for w in _wobble_methods(text, masked)]
    outside = [c for c in re.finditer(r"(?<![\w$])isShaking\s*\(", masked)
               if not (m.start <= c.start() <= m.body_close) and not any(a <= c.start() <= b for a, b in wob)]
    if outside:
        return Result(None, "isShaking", f"isShaking is also called at line {line_of(text, outside[0].start())}; port the callers together", "V18/V72")
    ent = free("entity", mm.group(1))
    expr = re.sub(r"(?<![\w$.])%s(?![\w$])" % re.escape(p), ent, mm.group(1))
    S = spec["state"]
    ind = indent_of(text, mstart(text, m))
    ds = fs.decl_span_start(text, mstart(text, m))
    me = m.body_close + 1
    ex = [x for x in fs.methods(text) if x.name == "extractRenderState" and len(x.params) == 3]
    if ex:
        x = ex[0]
        xb = ni.code_spans(text)[x.body_open + 1:x.body_close]
        sc = re.search(r"super\s*\.\s*extractRenderState\s*\([^;]*\);", xb)
        if not sc:
            return Result(None, "isShaking", "extractRenderState exists without a super call to anchor on", "V18")
        en, rs = fs.pname(x.params[0]), fs.pname(x.params[1])
        e2 = re.sub(r"(?<![\w$.])%s(?![\w$])" % re.escape(ent), en, expr)
        add = f"\n{indent_of(text, mstart(text, x))}   {rs}.addGeckolibData(DataTickets.IS_SHAKING, {e2});"
        if text[ds - 1:ds] == "\n" and text[me:me + 1] == "\n":
            me += 1
        new = apply_edits(text, [(x.body_open + 1 + sc.end(), x.body_open + 1 + sc.end(), add), (ds, me, "")])
    else:
        gen = (f"\n{ind}@Override\n{ind}public void extractRenderState({ent_param_t} {ent}, {S} renderState, float partialTick) {{\n"
               f"{ind}   super.extractRenderState({ent}, renderState, partialTick);\n"
               f"{ind}   renderState.addGeckolibData(DataTickets.IS_SHAKING, {expr});\n{ind}}}")
        new = apply_edits(text, [(ds, me, gen)])
    rep.counts["hook isShaking -> IS_SHAKING ticket"] += 1
    return Result(_with(new, S, "DataTickets"))


def _h_hide_bone(text, m, pn, body, S, rep):
    if not S:
        return Result(None, "renderRecursively", "enclosing renderer's state type is unknown", "V18d")
    bone = pn[2]
    mb = ni.code_spans(body)
    mm = re.fullmatch(r"\s*if\s*\((?P<cond>.+?)\)\s*\{\s*%s\s*\.\s*setHidden\s*\(\s*true\s*\)\s*;\s*\}\s*super\s*\.\s*renderRecursively\s*\((?P<args>[^;]*)\)\s*;\s*" % re.escape(bone), mb, re.S)
    if not mm:
        return Result(None, "renderRecursively", *REFUSED_RENDERER["renderRecursively"])
    others = [x for i, x in enumerate(pn) if i != 2]
    cond = body[mm.start("cond"):mm.end("cond")]
    if any(word_in(o, ni.code_spans(cond)) for o in others):
        return Result(None, "renderRecursively", "hide condition reads more than the bone", "V18d")
    if any(x.name == "adjustModelBonesForRender" for x in fs.methods(text)):
        return Result(None, "renderRecursively", "adjustModelBonesForRender already overridden; merge by hand", "V18d")
    cond = re.sub(r"(?<![\w$])%s\s*\.\s*getName\s*\(\s*\)" % re.escape(bone), f"{bone}.name()", cond)
    ind = indent_of(text, mstart(text, m))
    gen = (f"@Override\n{ind}public void adjustModelBonesForRender(RenderPassInfo<{S}> renderPassInfo, BoneSnapshots snapshots) {{\n"
           f"{ind}   super.adjustModelBonesForRender(renderPassInfo, snapshots);\n\n"
           f"{ind}   for (GeoBone {bone} : renderPassInfo.model().boneLookup().get().values()) {{\n"
           f"{ind}      if ({cond}) {{\n{ind}         snapshots.get({bone}).skipRender(true);\n{ind}      }}\n{ind}   }}\n{ind}}}")
    ds = fs.decl_span_start(text, mstart(text, m))
    new = text[:ds] + "\n" + ind + gen + text[m.body_close + 1:]
    new = re.sub(r"(?<![\w$])%s\s*\.\s*getName\s*\(\s*\)" % re.escape(bone), f"{bone}.name()", new)
    rep.counts["hook renderRecursively (hide bone) -> adjustModelBonesForRender"] += 1
    return Result(_with(new, S, "RenderPassInfo", "BoneSnapshots", "GeoBone"))


def _layer_r(text, masked, label):
    """The R type argument of the (already converted) layer class `label` as it stands in `text`."""
    for d in class_decls(text, masked):
        if d["name"] == (label or "").replace("anonymous ", ""):
            return d["ext_args"][2] if len(d["ext_args"]) >= 3 else None
    return None


def _h_layer_resource(text, m, pn, bmask, R, rep):
    if not R:
        return Result(None, "getTextureResource", *REFUSED_LAYER["getTextureResource"])
    if word_in(pn[0], bmask):
        return Result(None, "getTextureResource", *REFUSED_LAYER["getTextureResource"])
    st = free("renderState", bmask)
    new = _rebuild(text, m, "Identifier", f"{R} {st}")
    rep.counts["hook layer getTextureResource"] += 1
    return Result(_with(new, "Identifier"))


def _h_layer_render_type(text, m, pn, bmask, R, rep):
    if not R or word_in(pn[0], bmask):
        return Result(None, "getRenderType", *REFUSED_LAYER["getRenderType"])
    st = free("renderState", bmask)
    new = _rebuild(text, m, None, f"{R} {st}")
    rep.counts["hook layer getRenderType"] += 1
    return Result(new)


# models ----------------------------------------------------------------------------------------------------

BOX = {"int": "Integer", "boolean": "Boolean", "float": "Float", "double": "Double", "long": "Long", "short": "Short",
       "byte": "Byte", "char": "Character"}
DEFAULT = {"int": "0", "boolean": "false", "float": "0.0F", "double": "0.0D", "long": "0L", "short": "0", "byte": "0",
           "char": "'\\0'"}


def _snake(s):
    s = re.sub(r"^(get|is|has)(?=[A-Z])", "", s)
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", s).upper() or "VALUE"


def _h_model_resource(text, m, pn, bmask, rep):
    """Only the entity-free form converts here; bodies that read the entity are parked by _gen_models."""
    if word_in(pn[0], bmask):
        return None
    st = free("renderState", bmask)
    ret = "Identifier" if base_name(m.ret) in ("ResourceLocation", "Identifier") else None
    new = _rebuild(text, m, ret, f"GeoRenderState {st}")
    rep.counts["hook model resource (unused entity)"] += 1
    return Result(_with(new, "GeoRenderState", "Identifier"))


# Models are generated in a second step (needs the whole class); see _gen_models.


def _gen_models(text, idx, rep, path, opts):
    """Models whose resource methods read simple accessors: park them as tickets, per class, in one go."""
    masked = ni.code_spans(text)
    for cls in sorted(class_decls(text, masked), key=lambda d: -d["start"]):
        if idx.family(cls["name"]) != "model" or cls["ext_name"] not in GL_MODELS or not cls["ext_args"]:
            continue
        masked = ni.code_spans(text)
        cls = next((d for d in class_decls(text, masked) if d["name"] == cls["name"]), None)
        if not cls:
            continue
        ms = [m for m in fs.methods(text) if cls["open"] < m.start < cls["close"]
              and m.name in ("getModelResource", "getTextureResource") and len(m.params) == 1
              and ptype_base(m.params[0]) != "GeoRenderState"]
        if not ms:
            continue
        ent_t = cls["ext_args"][0]
        bound = next((b for n, b in cls["tparams"] if n == ent_t), None)
        ent_cls = (bound[0] if bound else None) or base_name(ent_t)
        tickets = collections.OrderedDict()
        plans, bad = [], None
        for m in ms:
            p = fs.pname(m.params[0])
            bm = ni.code_spans(text)[m.body_open + 1:m.body_close]
            for u in re.finditer(r"(?<![\w$.])%s(?![\w$])" % re.escape(p), bm):
                mm = re.match(r"\s*\.\s*([\w$]+)(\s*\(\s*\))?", bm[u.end():])
                if not mm:
                    bad = "body passes the entity along; park what it needs by hand"
                    break
                member, call = mm.group(1), bool(mm.group(2))
                if re.match(r"\s*[.(\[]", bm[u.end() + mm.end():]):
                    bad = f"`{p}.{member}` is chained further; park a simple value by hand"
                    break
                ty = idx.member_type(ent_cls, member, call)
                if ty is None or re.search(r"[<\[]", ty):
                    bad = f"cannot resolve a simple type for {ent_cls}.{member} in this tree"
                    break
                tickets[(member, call)] = ty
            if bad:
                break
            plans.append(m)
        if bad or any(x.name == "addAdditionalStateData" for x in fs.methods(text) if cls["open"] < x.start < cls["close"]):
            why = bad or "addAdditionalStateData already overridden; merge by hand"
            for m in ms:
                rep.refuse(path, line_of(text, m.start), f"{cls['name']}.{m.name}", why, "V18")
            continue
        # constants + extractor; names are unique per member
        decl_lines, put_lines, names = [], [], {}
        taken = " ".join(ni.code_spans(text)[cls["open"]:cls["close"]].split())
        for (member, call), ty in tickets.items():
            const = free(_snake(member), taken)
            taken += " " + const
            names[(member, call)] = (const, ty)
            box = BOX.get(ty, ty)
            decl_lines.append(f"private static final DataTicket<{box}> {const} = DataTicket.create(\"{cls['name']}.{member}\", {box}.class);")
            put_lines.append(f"renderState.addGeckolibData({const}, animatable.{member}{'()' if call else ''});")
        # rewrite the resource methods
        for m in sorted(ms, key=lambda x: -x.start):
            p = fs.pname(m.params[0])
            body = text[m.body_open + 1:m.body_close]
            st = free("renderState", ni.code_spans(body))
            def sub(mm_):
                member = mm_.group(1)
                call = bool(mm_.group(2))
                const, ty = names[(member, call)]
                if ty in DEFAULT:
                    return f"{st}.getOrDefaultGeckolibData({const}, {DEFAULT[ty]})"
                return f"{st}.getGeckolibData({const})"
            nb = re.sub(r"(?<![\w$.])%s\s*\.\s*([\w$]+)(\s*\(\s*\))?" % re.escape(p), sub, body)
            text = _rebuild(text, m, "Identifier" if base_name(m.ret) in ("ResourceLocation", "Identifier") else None,
                            f"GeoRenderState {st}", nb)
            masked = ni.code_spans(text)
        cls = next(d for d in class_decls(text, masked) if d["name"] == cls["name"])
        if tickets:
            ind = indent_of(text, cls["open"]) + "   "
            inner = indent_of(text, cls["open"]) + "   "
            gen = ("\n" + "".join(f"{inner}{l}\n" for l in decl_lines) + "\n"
                   + f"{inner}@Override\n{inner}public void addAdditionalStateData({ent_t} animatable, Object relatedObject, GeoRenderState renderState) {{\n"
                   + f"{inner}   super.addAdditionalStateData(animatable, relatedObject, renderState);\n"
                   + "".join(f"{inner}   {l}\n" for l in put_lines) + f"{inner}}}\n")
            text = text[:cls["open"] + 1] + gen + text[cls["open"] + 1:]
        rep.counts["model resource methods (%d ticket(s))" % len(tickets)] += len(ms)
        text = _with(text, "GeoRenderState", "Identifier", "DataTicket")
    return text


# GeoRenderProvider -------------------------------------------------------------------------------------


def phase_provider(text, rep, path):
    for _ in range(20):
        masked = ni.code_spans(text)
        hit = False
        for m in fs.methods(text):
            if m.name == "getGeoArmorRenderer" and len(m.params) == 4 and ptype_base(m.params[0]) not in ("ItemStack",):
                ent, stack, slot, orig = [fs.pname(p) for p in m.params]
                body = text[m.body_open + 1:m.body_close]
                b2 = re.sub(r"\n[ \t]*[\w$.]+\s*\.\s*prepForRender\s*\([^;]*\);[ \t]*(?=\n)", "", body)
                mb = ni.code_spans(b2)
                if word_in(ent, mb) or word_in(orig, mb):
                    rep.refuse(path, line_of(text, m.start), "GeoRenderProvider.getGeoArmorRenderer",
                               "body still reads the wearer or the vanilla model after prepForRender is removed; 5.x reads the wearer from the state", "V73")
                    continue
                params = f"{m.params[1]}, {m.params[2]}"
                text = _rebuild(text, m, "GeoArmorRenderer<?, ?>", params, b2)
                text = re.sub(r"<\w+ extends LivingEntity>\s+(?=GeoArmorRenderer<\?, \?> getGeoArmorRenderer)", "", text)
                for imp in ("net.minecraft.client.model.HumanoidModel", "net.minecraft.world.entity.LivingEntity"):
                    text = fs.remove_import_if_unused(text, imp)
                text = _with(text, "GeoArmorRenderer")
                rep.counts["GeoRenderProvider.getGeoArmorRenderer"] += 1
                hit = True
                break
            if m.name == "getGeoItemRenderer" and base_name(m.ret) == "BlockEntityWithoutLevelRenderer":
                text = _rebuild(text, m, "GeoItemRenderer<?>", ", ".join(m.params))
                text = fs.remove_import_if_unused(text, "net.minecraft.client.renderer.BlockEntityWithoutLevelRenderer")
                text = _with(text, "GeoItemRenderer")
                rep.counts["GeoRenderProvider.getGeoItemRenderer"] += 1
                hit = True
                break
        if not hit:
            break
    return text


# Whole-file refusal scan -------------------------------------------------------------------------------

REMOVED_TICKETS = "ACTIVE ANIM ANIM_STATE BLOCK_ENTITY CLOSED DIRECTION ENTITY ENTITY_MODEL_DATA EQUIPMENT_SLOT ITEMSTACK OPEN SERIALIZABLE_TICKETS USE_TICKS".split()
BONE_API = (r"\.(?:setHidden|isHidden|setChildrenHidden|setRot[XYZ]|getRot[XYZ]|setPos[XYZ]|getPos[XYZ]|setScale[XYZ]|getScale[XYZ]|"
            r"updateRotation|updatePosition|updateScale|setPivot[XYZ]|updatePivot|getInitialSnapshot|getModelPosition|getLocalPosition|"
            r"getWorldPosition|setModelSpaceMatrix|getModelSpaceMatrix|getLocalSpaceMatrix|getWorldSpaceMatrix|getName)\s*\(")


def phase_scan(text, rep, path, opts):
    masked = ni.code_spans(text)

    def hit(pos, tag, why, ref):
        rep.refuse(path, line_of(text, pos), tag, why, ref)
    if re.search(r"(?m)^import\s+%s\.(?:cache\.object|cache\.model)\.(?:Core)?GeoBone\s*;" % GLPKG, text) or \
       re.search(r"(?m)^import\s+%s\.core\.animatable\.model\.CoreGeoBone\s*;" % GLPKG, text):
        bones = set(re.findall(r"(?:Core)?GeoBone\s+(\w+)", masked))
        edits = []
        for b in bones:
            for m in re.finditer(r"(?<![\w$.])%s(\.\s*)(getName|getParent)(\s*\(\s*\))" % re.escape(b), masked):
                edits.append((m.start(2), m.end(3), {"getName": "name()", "getParent": "parent()"}[m.group(2)]))
        if edits:
            text = apply_edits(text, edits)
            rep.counts["GeoBone.getName/getParent -> name()/parent()"] += len(edits)
            masked = ni.code_spans(text)
        for b in bones:
            for m in re.finditer(r"(?<![\w$.])%s%s" % (re.escape(b), BONE_API), masked):
                api = m.group(0)[len(b) + 1:].split("(")[0].strip()
                if api in ("getName",):
                    continue
                hit(m.start(), f"GeoBone.{api}", "bone pose is a per-pass BoneSnapshot in 5.x (frameSnapshot is null outside a render pass)", "V18d")
    for t in REMOVED_TICKETS:
        for m in re.finditer(r"(?<![\w$])DataTickets\s*\.\s*%s(?![\w$])" % t, masked):
            hit(m.start(), f"DataTickets.{t}", "this ticket does not exist in 5.x", "V18")
    for pat, tag, why, ref in (
        (r"(?<![\w$.])(?:getAnimData|setAnimData)\s*\(", "getAnimData/setAnimData", "SerializableDataTicket is gone; sync with an entity data accessor or attachment", "V18"),
        (r"\bSerializableDataTicket\b", "SerializableDataTicket", "SerializableDataTicket is gone in 5.x", "V18"),
        (r"(?<![\w$.])(?:Color)\s*\.\s*\w+\(", "util.Color", "util.Color is gone; colours are packed ARGB ints", "V18"),
        (r"\bEntityModelData\b", "EntityModelData", "EntityModelData is gone; read the state (ENTITY_YAW/ENTITY_PITCH tickets)", "V18"),
        (r"\bGeckoLibCache\b", "GeckoLibCache", "GeckoLibCache is gone; model and animation loading was replaced", "V18"),
        (r"\bAutoGlowingTexture\b", "AutoGlowingTexture", "AutoGlowingTexture is gone; use AutoGlowingGeoLayer", "V18"),
        (r"(?<![\w$.])getAnimationProcessor\s*\(", "getAnimationProcessor", "the animation processor no longer exposes bones; use BoneUpdater in a render pass", "V18d/V75"),
        (r"(?<![\w$.])(?:getCurrentEntity|getAnimatable)\s*\(\s*\)", "renderer.getAnimatable/getCurrentEntity", "5.x renderers hold no animatable between passes; read the state", "V18"),
    ):
        if pat.startswith("(?<![\\w$.])(?:Color)") and not re.search(r"import\s+%s\.util\.Color\s*;" % GLPKG, text):
            continue
        if tag == "renderer.getAnimatable/getCurrentEntity" and not re.search(r"\bGeo\w*Renderer\b", masked):
            continue
        for m in re.finditer(pat, masked):
            hit(m.start(), tag, why, ref)
    for tname, (ref, why) in GL_REFUSED_TYPES.items():
        for m in re.finditer(r"(?<![\w$.])%s\b" % tname, masked):
            if re.match(r"\s*(?:\.|;)", masked[m.end():]) and "import" in masked[max(0, m.start() - 60):m.start()]:
                continue
            hit(m.start(), tname, why, ref)
            break
    lim = re.compile(r"(?<![\w$.])(\w+)\s*\.\s*getLimbSwing(?:Amount)?\s*\(\s*\)")
    if opts.get("limb_swing_as_moving"):
        for m in sorted(re.finditer(r"(\w+)\s*\.\s*getLimbSwingAmount\(\)\s*>\s*-0\.15F?\s*&&\s*\1\s*\.\s*getLimbSwingAmount\(\)\s*<\s*0\.15F?", masked), key=lambda x: -x.start()):
            text = text[:m.start()] + f"!{m.group(1)}.isMoving()" + text[m.end():]
            rep.counts["limb swing idle test -> !isMoving()"] += 1
        masked = ni.code_spans(text)
    for m in lim.finditer(masked):
        hit(m.start(), "getLimbSwing*", "AnimationTest has no limb swing; isMoving() uses a distance threshold of 0.015 (judgement, --limb-swing-as-moving rewrites the idle test)", "V18")
    # string ids that are built, not literal
    for s, e in iter_strings(text):
        lit = text[s + 1:e - 1]
        if re.search(r"(?:^|:)geo/|\.geo\.json$|(?:^|:)animations/|\.animation\.json$", lit) and not re.fullmatch(r"(?:[a-z0-9_.-]+:)?(?:geo/.+\.json|animations/.+\.json)", lit):
            hit(s, "geo/animation id built dynamically", "5.x ids are bare names under geckolib/{models,animations}: strip the prefix and suffix by hand", "V18c")
    return text


def iter_strings(text):
    i, n = 0, len(text)
    while i < n:
        j = fs._skip(text, i)
        if j != i:
            if text[i] == '"' and not text.startswith('"""', i):
                yield i, j
            i = j
        else:
            i += 1


# ids -------------------------------------------------------------------------------------------------------

GEO_ID = re.compile(r"^((?:[a-z0-9_.-]+:)?)geo/(.+?)(?:\.geo)?\.json$")
ANIM_ID = re.compile(r"^((?:[a-z0-9_.-]+:)?)animations/(.+?)(?:\.animations?)?\.json$")


def phase_ids(text, rep):
    edits = []
    for s, e in iter_strings(text):
        lit = text[s + 1:e - 1]
        m = GEO_ID.match(lit) or ANIM_ID.match(lit)
        if m:
            edits.append((s, e, '"' + m.group(1) + m.group(2) + '"'))
    rep.counts["geo/animation id strings"] += len(edits)
    return apply_edits(text, edits)


# assets ------------------------------------------------------------------------------------------------------


def _in_git(p):
    try:
        r = subprocess.run(["git", "-C", str(p), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, encoding="utf-8")
        return r.returncode == 0 and r.stdout.strip() == "true"
    except OSError:
        return False


def _tracked(p):
    r = subprocess.run(["git", "-C", str(p.parent), "ls-files", "--error-unmatch", p.name], capture_output=True, text=True, encoding="utf-8")
    return r.returncode == 0


def move_assets(assets, dry, rep):
    assets = pathlib.Path(assets)
    moved = 0
    git = _in_git(assets)
    for ns in sorted(p for p in assets.iterdir() if p.is_dir()):
        for src_name, dst_rel in (("geo", "geckolib/models"), ("animations", "geckolib/animations")):
            src = ns / src_name
            if not src.is_dir():
                continue
            for f in sorted(x for x in src.rglob("*") if x.is_file()):
                if f.suffix != ".json":
                    rep.refuse(str(f), 0, "asset", "not a .json file; 5.x does not read it from here", "V18c")
                    continue
                dst = ns / dst_rel / f.relative_to(src)
                if dst.exists():
                    rep.refuse(str(f), 0, "asset", f"destination {dst} already exists", "V18c")
                    continue
                moved += 1
                if dry:
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                if git and _tracked(f):
                    r = subprocess.run(["git", "-C", str(f.parent), "mv", f.name, os.path.relpath(dst, f.parent)] if False else
                                       ["git", "-C", str(assets), "mv", str(f.relative_to(assets)), str(dst.relative_to(assets))],
                                       capture_output=True, text=True, encoding="utf-8")
                    if r.returncode != 0:
                        shutil.move(str(f), str(dst))
                else:
                    shutil.move(str(f), str(dst))
            if not dry:
                for d in sorted((x for x in src.rglob("*") if x.is_dir()), key=lambda x: -len(x.parts)) + [src]:
                    try:
                        d.rmdir()
                    except OSError:
                        pass
    rep.counts["asset files moved to geckolib/{models,animations}"] += moved


# driver -----------------------------------------------------------------------------------------------------


def convert_text(text, idx, path, rep, opts=None):
    opts = opts or {}
    original = text
    if not is_gecko(text) and not any(idx.family(d["name"]) for d in class_decls(text)):
        return text
    text = phase_simple(text, rep, path)
    text = phase_headers(text, idx, rep, path)
    text = phase_layer_new(text, idx, rep, path)
    text = phase_hooks(text, idx, rep, path, opts)
    text = _gen_models(text, idx, rep, path, opts)
    text = phase_provider(text, rep, path)
    text = phase_ids(text, rep)
    if text != original:
        for imp in ("net.minecraft.client.renderer.MultiBufferSource", "com.mojang.blaze3d.vertex.VertexConsumer",
                    "com.mojang.blaze3d.vertex.PoseStack", "net.minecraft.resources.ResourceLocation",
                    OLD + ".cache.object.BakedGeoModel", NEW + ".cache.model.BakedGeoModel"):
            text = fs.remove_import_if_unused(text, imp)
    text = phase_scan(text, rep, path, opts)
    return text


def build_index(files):
    idx = Index()
    for f in files:
        idx.add_file(f.read_text(encoding="utf-8"))
    return idx


def run(src, assets=None, dry=False, opts=None, index_roots=()):
    rep = Report()
    files = sorted(pathlib.Path(src).rglob("*.java"))
    idx = build_index(files + [f for r in index_roots for f in sorted(pathlib.Path(r).rglob("*.java"))])
    gl = 0
    for f in files:
        t = f.read_text(encoding="utf-8")
        if not is_gecko(t) and not any(idx.family(d["name"]) for d in class_decls(t)):
            continue
        gl += 1
        out = convert_text(t, idx, str(f), rep, opts)
        if out != t:
            rep.counts["files changed"] += 1
            if not dry:
                f.write_text(out, encoding="utf-8")
    rep.counts["geckolib files seen"] = gl
    if assets:
        move_assets(assets, dry, rep)
    return rep


HOOKS = """\
renderer hook                                                   -> 5.x
  getRenderType(T, ResourceLocation, MultiBufferSource, float)  -> getRenderType(S state, Identifier texture)
  applyRotations(T, PoseStack, float age, float yaw, float pt)  -> applyRotations(RenderPassInfo<S>, PoseStack, float nativeScale)
  getDeathMaxRotation(T)                                        -> getDeathMaxRotation(GeoRenderState)
  getTextureLocation(T)                                         -> getTextureLocation(S state) : Identifier
  getPackedOverlay(T, float, float)                             -> getPackedOverlay(T, Void, float, float)
  shouldShowName(T)                                             -> shouldShowName(T, double)
  isShaking(T)                                                  -> extractRenderState + DataTickets.IS_SHAKING
  renderRecursively(.. hide-armor-bone idiom ..)                -> adjustModelBonesForRender(RenderPassInfo<S>, BoneSnapshots)
layer hook
  getTextureResource(T) / getRenderType(T)                      -> getTextureResource(R) / getRenderType(R)
model hook
  getModelResource(T) / getTextureResource(T)                   -> (GeoRenderState); accessors parked as DataTickets
bridged locals: ageInTicks <- state.ageInTicks, partialTick <- state.partialTick, yaw <- ENTITY_BODY_YAW ticket
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src")
    ap.add_argument("--assets")
    ap.add_argument("--index", action="append", default=[], metavar="DIR",
                    help="extra source root read only, to resolve classes a library mod supplies (repeatable)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--hooks", action="store_true", help="print the hook signature table")
    ap.add_argument("--limb-swing-as-moving", action="store_true",
                    help="rewrite the `limbSwingAmount in (-0.15, 0.15)` idle test to !isMoving() (a judgement: V18)")
    a = ap.parse_args()
    if a.hooks:
        print(HOOKS)
        return 0
    if a.self_check:
        return self_check()
    if not a.src:
        ap.error("--src is required")
    rep = run(a.src, a.assets, a.dry_run, {"limb_swing_as_moving": a.limb_swing_as_moving}, a.index)
    print("convert-geckolib:" + (" (dry run)" if a.dry_run else ""))
    for k, v in sorted(rep.counts.items()):
        print(f"  {v:5d}  {k}")
    for p, line, text in rep.notes:
        print(f"  note   {p or ''}:{line}: {text}")
    by = collections.Counter(r[2].split(".")[-1] for r in rep.refused)
    print(f"  REFUSED {len(rep.refused)} site(s); by kind: " + ", ".join(f"{k} x{v}" for k, v in by.most_common(12)))
    for p, line, tag, why, ref in rep.refused:
        print(f"  REFUSED {p}:{line}: {tag}: {why} [{ref}]")
    return 0


# self-check ---------------------------------------------------------------------------------------------------


def fx(s):
    return s.replace("@GL@", NEW).replace("@OLD@", OLD)


ENTITY_SRC = fx('''package m;

import net.minecraft.world.entity.monster.Monster;

public class ModEntity extends Monster {
    public int getVariant() { return 1; }
    public boolean isAngry() { return false; }
}
''')
ARROW_SRC = fx('''package m;

import net.minecraft.world.entity.projectile.Projectile;

public class ModBolt extends Projectile {
}
''')
RENDERER_SRC = fx('''package m;

import com.mojang.blaze3d.vertex.PoseStack;
import @GL@.renderer.GeoEntityRenderer;
import @GL@.cache.object.GeoBone;
import net.minecraft.client.renderer.MultiBufferSource;
import net.minecraft.client.renderer.RenderType;
import net.minecraft.client.renderer.entity.EntityRendererProvider;
import net.minecraft.resources.ResourceLocation;

public class FooRenderer extends GeoEntityRenderer<ModEntity> {
   public FooRenderer(EntityRendererProvider.Context ctx) {
      super(ctx, new FooModel());
      this.addRenderLayer(new GlowLayer(this, "tex"));
   }

   public RenderType getRenderType(ModEntity animatable, ResourceLocation texture, MultiBufferSource bufferSource, float partialTick) {
      return RenderType.entityTranslucent(this.getTextureLocation(animatable));
   }

   protected void applyRotations(ModEntity entity, PoseStack stack, float ageInTicks, float rotationYaw, float partialTick) {
      stack.scale(1.5F + ageInTicks * 0.0F, 1.5F, 1.5F);
      super.applyRotations(entity, stack, ageInTicks, rotationYaw, partialTick);
   }

   protected float getDeathMaxRotation(ModEntity e) {
      return 0.0F;
   }

   public boolean isShaking(ModEntity e) {
      return e.isAngry();
   }

   public int getPackedOverlay(ModEntity a, float u, float v) {
      return 0;
   }

   public void renderRecursively(PoseStack poseStack, ModEntity animatable, GeoBone bone, RenderType renderType,
      MultiBufferSource bufferSource, com.mojang.blaze3d.vertex.VertexConsumer buffer, boolean isReRender, float partialTick, int light, int overlay, int colour) {
      if (bone.getName().startsWith("armor")) {
         bone.setHidden(true);
      }
      super.renderRecursively(poseStack, animatable, bone, renderType, bufferSource, buffer, isReRender, partialTick, light, overlay, colour);
   }

   public void preRender(PoseStack poseStack, ModEntity animatable, Object model, MultiBufferSource bufferSource, Object buffer, boolean isReRender, float partialTick, int l, int o, int colour) {
      poseStack.scale(2.0F, 2.0F, 2.0F);
   }
}
''')
GENERIC_SRC = fx('''package m;

import @GL@.animatable.GeoAnimatable;
import @GL@.renderer.GeoEntityRenderer;
import net.minecraft.world.entity.Entity;

public class BoltRenderer<T extends Projectile & GeoAnimatable> extends GeoEntityRenderer<T> {
}
''')
SUB_SRC = fx('''package m;

public class BoltSubRenderer extends BoltRenderer<ModBolt> {
}
''')
LAYER_SRC = fx('''package m;

import @GL@.renderer.GeoRenderer;
import @GL@.renderer.layer.AutoGlowingGeoLayer;
import net.minecraft.client.renderer.RenderType;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.entity.LivingEntity;
import @GL@.animatable.GeoEntity;

public class GlowLayer<T extends LivingEntity & GeoEntity> extends AutoGlowingGeoLayer<T> {
   public ResourceLocation textureLocation;

   public GlowLayer(GeoRenderer<T> renderer, String s) {
      super(renderer);
   }

   protected ResourceLocation getTextureResource(T animatable) {
      return this.textureLocation;
   }

   protected RenderType getRenderType(T animatable) {
      return RenderType.eyes(this.textureLocation);
   }
}
''')
MODEL_SRC = fx('''package m;

import @GL@.model.GeoModel;
import net.minecraft.resources.ResourceLocation;

public class FooModel extends GeoModel<ModEntity> {
   public ResourceLocation getModelResource(ModEntity entity) {
      return entity.getVariant() == 1 ? ResourceLocation.parse("m:geo/a.geo.json") : ResourceLocation.parse("m:geo/b/c.geo.json");
   }

   public ResourceLocation getTextureResource(ModEntity entity) {
      return ResourceLocation.parse("m:textures/foo.png");
   }

   public ResourceLocation getAnimationResource(ModEntity entity) {
      return ResourceLocation.parse("m:animations/a.animation.json");
   }
}
''')
ANIM_SRC = fx('''package m;

import @OLD@.animatable.GeoEntity;
import @OLD@.animation.Animation.LoopType;
import @OLD@.animation.AnimatableManager.ControllerRegistrar;
import @OLD@.animation.AnimationController;
import @OLD@.animation.AnimationState;
import @OLD@.animation.PlayState;
import @OLD@.animation.RawAnimation;
import @OLD@.constant.dataticket.DataTicket;

public class AnimEntity {
   static final DataTicket<Integer> T1 = new DataTicket<>("t1", Integer.class);

   public void registerControllers(ControllerRegistrar controllers) {
      controllers.add(new AnimationController<>(this, "controller", 2, this::predicate));
   }

   private <P extends GeoEntity> PlayState predicate(AnimationState<P> event) {
      event.getController().setAnimation(RawAnimation.begin().then("a", LoopType.LOOP));
      return PlayState.CONTINUE;
   }
}
''')
PROVIDER_SRC = fx('''package m;

import java.util.function.Consumer;
import net.minecraft.client.model.HumanoidModel;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.item.ItemStack;
import @GL@.animatable.client.GeoRenderProvider;
import @GL@.renderer.GeoArmorRenderer;

public class ArmorThing {
   public void createGeoRenderer(Consumer<GeoRenderProvider> consumer) {
      consumer.accept(new GeoRenderProvider() {
         private GeoArmorRenderer<?> renderer;

         @Override
         public <T extends LivingEntity> HumanoidModel<?> getGeoArmorRenderer(
               T entityLiving, ItemStack itemStack, EquipmentSlot armorSlot, HumanoidModel<T> original) {
            if (this.renderer == null) {
               this.renderer = new ArmorRenderer<>();
            }

            this.renderer.prepForRender(entityLiving, itemStack, armorSlot, original);
            return this.renderer;
         }
      });
   }
}
''')


WOBBLE_SRC = fx('''package m;

import com.mojang.blaze3d.vertex.PoseStack;
import @GL@.renderer.GeoEntityRenderer;
import net.minecraft.client.renderer.entity.EntityRendererProvider;
import net.minecraft.resources.ResourceLocation;

public class WobbleRenderer extends GeoEntityRenderer<ModEntity> {
   public WobbleRenderer(EntityRendererProvider.Context ctx) {
      super(ctx, new FooModel());
   }

   public boolean isShaking(ModEntity e) {
      return e.isAngry();
   }

   protected void applyRotations(ModEntity entityLiving, PoseStack matrixStackIn, float ageInTicks, float rotationYaw, float partialTicks) {
      if (this.isShaking(entityLiving)) {
         rotationYaw += (float)(Math.cos((double)entityLiving.tickCount * 3.25) * Math.PI * 0.4F);
      }

      super.applyRotations(entityLiving, matrixStackIn, ageInTicks, rotationYaw, partialTicks);
   }

   public ResourceLocation getTextureLocation(ModEntity entity) {
      return super.getTextureLocation(entity);
   }
}
''')
IDENT_SRC = fx('''package m;

import com.mojang.blaze3d.vertex.PoseStack;
import @GL@.renderer.GeoEntityRenderer;
import net.minecraft.client.renderer.entity.EntityRendererProvider;

public class IdentRenderer extends GeoEntityRenderer<ModEntity> {
   public IdentRenderer(EntityRendererProvider.Context ctx) {
      super(ctx, new FooModel());
   }

   protected void applyRotations(ModEntity entityLiving, PoseStack matrixStackIn, float ageInTicks, float rotationYaw, float partialTicks) {
      float scaleFactor = 1.0F;
      matrixStackIn.scale(scaleFactor, scaleFactor, scaleFactor);
      super.applyRotations(entityLiving, matrixStackIn, ageInTicks, rotationYaw, partialTicks);
   }

   protected void other(ModEntity e) {
      int x = getAnimatable().tickCount;
   }
}
''')


def self_check():
    miss = []
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td) / "java" / "m"
        root.mkdir(parents=True)
        files = {"ModEntity": ENTITY_SRC, "ModBolt": ARROW_SRC, "FooRenderer": RENDERER_SRC, "BoltRenderer": GENERIC_SRC,
                 "BoltSubRenderer": SUB_SRC, "GlowLayer": LAYER_SRC, "FooModel": MODEL_SRC, "AnimEntity": ANIM_SRC,
                 "ArmorThing": PROVIDER_SRC, "WobbleRenderer": WOBBLE_SRC, "IdentRenderer": IDENT_SRC}
        for n, t in files.items():
            (root / f"{n}.java").write_text(t, encoding="utf-8")
        assets = pathlib.Path(td) / "assets"
        (assets / "m" / "geo" / "b").mkdir(parents=True)
        (assets / "m" / "geo" / "a.geo.json").write_text("{}", encoding="utf-8")
        (assets / "m" / "geo" / "b" / "c.geo.json").write_text("{}", encoding="utf-8")
        (assets / "m" / "animations").mkdir()
        (assets / "m" / "animations" / "a.animation.json").write_text("{}", encoding="utf-8")
        rep = run(pathlib.Path(td) / "java", assets)
        out = {n: (root / f"{n}.java").read_text(encoding="utf-8") for n in files}

        def want(n, *frags):
            for f in frags:
                if f not in out[n]:
                    miss.append(f"{n}: missing {f!r}")

        def absent(n, *frags):
            for f in frags:
                if f in out[n]:
                    miss.append(f"{n}: still has {f!r}")
        want("FooRenderer", "extends GeoEntityRenderer<ModEntity, LivingEntityRenderState>", "this.withRenderLayer(new GlowLayer<>(this",
             "public RenderType getRenderType(LivingEntityRenderState renderState, Identifier texture)", "RenderType.entityTranslucent(texture)",
             "protected void applyRotations(RenderPassInfo<LivingEntityRenderState> renderPassInfo, PoseStack stack, float nativeScale)",
             "super.applyRotations(renderPassInfo, stack, nativeScale);", "float ageInTicks = renderPassInfo.renderState().ageInTicks;",
             "protected float getDeathMaxRotation(GeoRenderState renderState)", "public int getPackedOverlay(ModEntity a, Void relatedObject, float u, float v)",
             "public void extractRenderState(ModEntity entity, LivingEntityRenderState renderState, float partialTick)",
             "renderState.addGeckolibData(DataTickets.IS_SHAKING, entity.isAngry());",
             "public void adjustModelBonesForRender(RenderPassInfo<LivingEntityRenderState> renderPassInfo, BoneSnapshots snapshots)",
             "if (bone.name().startsWith(\"armor\"))", "snapshots.get(bone).skipRender(true);",
             "import net.minecraft.client.renderer.entity.state.LivingEntityRenderState;")
        absent("FooRenderer", "isShaking", "bone.setHidden")
        if not any("preRender" in r[2] for r in rep.refused):
            miss.append("preRender was not refused")
        want("BoltRenderer", "BoltRenderer<T extends Projectile & GeoAnimatable, R extends EntityRenderState> extends GeoEntityRenderer<T, R>")
        want("BoltSubRenderer", "extends BoltRenderer<ModBolt, EntityRenderState>")
        want("GlowLayer", "GlowLayer<T extends LivingEntity & GeoEntity, R extends GeoRenderState> extends AutoGlowingGeoLayer<T, Void, R>",
             "GeoRenderer<T, Void, R> renderer", "protected Identifier getTextureResource(R renderState)", "protected RenderType getRenderType(R renderState)")
        want("FooModel", "public Identifier getModelResource(GeoRenderState renderState)", "renderState.getOrDefaultGeckolibData(VARIANT, 0) == 1",
             "DataTicket<Integer> VARIANT", "renderState.addGeckolibData(VARIANT, animatable.getVariant());", '"m:a"', '"m:b/c"', '"m:a"')
        want("FooModel", "public Identifier getTextureResource(GeoRenderState renderState)")
        absent("FooModel", "geo/", ".animation.json")
        want("AnimEntity", "new AnimationController<>(\"controller\", 2, this::predicate)", "AnimationTest<P> event", "event.setAnimation(",
             "DataTicket.create(\"t1\", Integer.class)", "import " + NEW + ".animation.object.LoopType;")
        absent("AnimEntity", "AnimationState", "getController()", "Animation.LoopType")
        want("WobbleRenderer", "extends GeoEntityRenderer<ModEntity, LivingEntityRenderState>", "IS_SHAKING, entity.isAngry()")
        absent("WobbleRenderer", "applyRotations", "isShaking", "getTextureLocation", "\n\n\n")
        absent("IdentRenderer", "applyRotations")
        if not any("getAnimatable" in r[2] for r in rep.refused):
            miss.append("getAnimatable use was not reported")
        if not any(r[0].endswith("FooRenderer.java") and "preRender" in r[2] for r in rep.refused):
            miss.append("refusal lost its file")
        want("ArmorThing", "public GeoArmorRenderer<?, ?> getGeoArmorRenderer(ItemStack itemStack, EquipmentSlot armorSlot)", "GeoArmorRenderer<?, ?> renderer")
        absent("ArmorThing", "prepForRender", "import net.minecraft.client.model.HumanoidModel;")
        for sub, dst in (("geo", "geckolib/models"), ("animations", "geckolib/animations")):
            if (assets / "m" / sub).exists():
                miss.append(f"asset dir {sub} not moved")
            if not (assets / "m" / dst).is_dir():
                miss.append(f"asset dir {dst} missing")
        if not (assets / "m" / "geckolib" / "models" / "b" / "c.geo.json").is_file():
            miss.append("nested geo asset not moved")
        # tracked files move with git mv
        gitroot = pathlib.Path(td) / "g" / "assets"
        (gitroot / "n" / "geo").mkdir(parents=True)
        (gitroot / "n" / "geo" / "z.geo.json").write_text("{}", encoding="utf-8")
        try:
            subprocess.run(["git", "init", "-q", str(gitroot.parent)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(gitroot.parent), "add", "-A"], check=True, capture_output=True)
            move_assets(gitroot, False, Report())
            ls = subprocess.run(["git", "-C", str(gitroot.parent), "ls-files"], capture_output=True, text=True, encoding="utf-8").stdout
            if "geckolib/models/z.geo.json" not in ls or "geo/z.geo.json" in ls.replace("geckolib/models/z", ""):
                miss.append("git mv not used for a tracked asset: " + ls.strip())
        except (OSError, subprocess.CalledProcessError):
            pass                                             # git unavailable: the plain-move path was already checked above
        # idempotent
        before = {n: (root / f"{n}.java").read_text(encoding="utf-8") for n in files}
        rep2 = run(pathlib.Path(td) / "java", assets)
        for n in files:
            if (root / f"{n}.java").read_text(encoding="utf-8") != before[n]:
                miss.append(f"{n}: not idempotent")
        if rep2.counts.get("asset files moved to geckolib/{models,animations}"):
            miss.append("assets not idempotent")
    print("self-check:", "OK" if not miss else "FAIL " + "; ".join(miss))
    return 0 if not miss else 1


if __name__ == "__main__":
    sys.exit(main())
