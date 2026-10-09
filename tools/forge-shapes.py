#!/usr/bin/env python3
"""Rewrite the Forge 1.20.1 code SHAPES a rename table cannot express, for NeoForge 1.21.1.

    python3 tools/forge-shapes.py --src src/main/java --modid <modid> [--only t1,t2] [--dry-run] [--json]
    python3 tools/forge-shapes.py --self-check

The recipe pack (tools/apply-recipes.py) renames; this tool restructures. Each transform below is one
catalogue entry whose fix is the same edit at every site but needs the code's structure -- a method's
parameter names, a matching parenthesis, which phase a handler checked -- so no regex row can carry it.
Measured on two source-first ports, these were ~45% of the errors left after the pack.

Every transform:
  * rewrites only the shape it recognises completely, and LISTS (never guesses) the near-misses, so a
    worker sees them by name;
  * is idempotent (running twice changes nothing the second time);
  * is reported per transform; one that changed nothing is shown as such (the §X1 detector).

Run it AFTER the recipe pack: it expects the package renames (net.neoforged.*) already applied.
Standard library only.
"""
import argparse, collections, json, pathlib, re, sys

# --------------------------------------------------------------------------- Java text helpers

def _skip(text, i):
    """If text[i] opens a string, char literal or comment, return the index just past it, else i."""
    c = text[i]
    if text.startswith('"""', i):
        j = text.find('"""', i + 3)
        return len(text) if j < 0 else j + 3
    if c in "\"'":
        j = i + 1
        while j < len(text) and text[j] != c:
            j += 2 if text[j] == "\\" else 1
        return j + 1
    if text.startswith("//", i):
        j = text.find("\n", i)
        return len(text) if j < 0 else j
    if text.startswith("/*", i):
        j = text.find("*/", i + 2)
        return len(text) if j < 0 else j + 2
    return i


def match(text, i):
    """Index of the bracket closing the one at text[i] ( ( [ { or < is not handled )."""
    pairs = {"(": ")", "{": "}", "[": "]"}
    stack = [pairs[text[i]]]
    j = i + 1
    while j < len(text):
        k = _skip(text, j)
        if k != j:
            j = k
            continue
        c = text[j]
        if c in pairs:
            stack.append(pairs[c])
        elif c in ")}]":
            if not stack or stack.pop() != c:
                return -1
            if not stack:
                return j
        j += 1
    return -1


def split_args(s):
    """Split an argument list at top-level commas (generics-aware enough for call sites)."""
    out, depth, angle, cur, j = [], 0, 0, [], 0
    while j < len(s):
        k = _skip(s, j)
        if k != j:
            cur.append(s[j:k]); j = k; continue
        c = s[j]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "<" and j and (s[j - 1].isalnum() or s[j - 1] == "_") and re.match(r"\s*[\w?]", s[j + 1:]):
            angle += 1                       # a generic, not a comparison
        elif c == ">" and angle and s[j - 1] != "-":
            angle -= 1
        if c == "," and depth == 0 and angle == 0:
            out.append("".join(cur)); cur = []
        else:
            cur.append(c)
        j += 1
    if "".join(cur).strip():
        out.append("".join(cur))
    return out


class Method:
    __slots__ = ("start", "name", "ret", "params_open", "params_close", "body_open", "body_close", "params")


HEADER = re.compile(r"(?<![\w.])(?P<ret>[\w$.<>\[\]?, ]+?)\s+(?P<name>[a-z_$][\w$]*)\s*\(")
MODIFIERS = {"public", "protected", "private", "static", "final", "abstract", "synchronized", "default", "native"}
KEYWORDS = {"return", "new", "throw", "else", "case", "if", "while", "for", "switch", "catch", "synchronized", "yield"}


def methods(text):
    """Method declarations with a body: name, return type, param/body spans. Lambdas and calls excluded."""
    out = []
    for m in HEADER.finditer(text):
        ret = m.group("ret").strip()
        while " " in ret and ret.split(None, 1)[0] in MODIFIERS:
            ret = ret.split(None, 1)[1]
        last = ret.split()[-1] if ret.split() else ""
        if last in KEYWORDS or ret in KEYWORDS or "=" in ret or m.group("name") in KEYWORDS:
            continue
        p0 = m.end() - 1
        p1 = match(text, p0)
        if p1 < 0:
            continue
        rest = re.match(r"\s*(?:throws\s+[\w.$, ]+)?\s*\{", text[p1 + 1:])
        if not rest:
            continue
        b0 = p1 + rest.end()
        b1 = match(text, b0)
        if b1 < 0:
            continue
        x = Method()
        x.start = m.start("ret"); x.name = m.group("name"); x.ret = ret
        x.params_open, x.params_close, x.body_open, x.body_close = p0, p1, b0, b1
        x.params = [a.strip() for a in split_args(text[p0 + 1:p1])]
        out.append(x)
    return out


def pname(param):
    return param.split()[-1] if param.split() else ""


def ptype(param):
    t = re.sub(r"@\w+(\([^)]*\))?\s*", "", param).replace("final ", "").strip()
    return t.rsplit(None, 1)[0] if " " in t else t


def has_import(text, fqn):
    return re.search(r"(?m)^import\s+%s\s*;" % re.escape(fqn), text) is not None


def add_import(text, fqn):
    if has_import(text, fqn):
        return text
    simple = fqn.rsplit(".", 1)[-1]
    pkg = (re.search(r"(?m)^package\s+([\w.]+)\s*;", text) or [None, ""])[1]
    if fqn.rsplit(".", 1)[0] == pkg:
        return text
    if re.search(r"(?m)^import\s+[\w.]+\.%s\s*;" % re.escape(simple), text):
        return text                      # a different class of that name is imported: caller keeps it qualified
    imps = list(re.finditer(r"(?m)^import\s+(static\s+)?([\w.*]+)\s*;[ \t]*\n", text))
    if not imps:
        pm = re.search(r"(?m)^package\s+[\w.]+\s*;[ \t]*\n", text)
        at = pm.end() if pm else 0
        return text[:at] + "\n" + f"import {fqn};\n" + text[at:]
    # after the last import sharing the longest prefix, else after the last import
    best, blen = imps[-1], -1
    for m in imps:
        if m.group(1):
            continue
        a, b = m.group(2).split("."), fqn.split(".")
        n = next((k for k in range(min(len(a), len(b))) if a[k] != b[k]), min(len(a), len(b)))
        if n > blen or (n == blen and m.group(2) < fqn):
            best, blen = m, n
    return text[:best.end()] + f"import {fqn};\n" + text[best.end():]


def remove_import_if_unused(text, fqn):
    simple = fqn.rsplit(".", 1)[-1]
    m = re.search(r"(?m)^import\s+%s\s*;[ \t]*\n" % re.escape(fqn), text)
    if not m:
        return text
    body = text[:m.start()] + text[m.end():]
    if re.search(r"(?<![\w.$])%s(?![\w$])" % re.escape(simple), re.sub(r"(?m)^import .*$", "", body)):
        return text
    return body


def fresh(name, scope):
    n, k = name, 2
    while re.search(r"(?<![\w$])%s(?![\w$])" % re.escape(n), scope):
        n, k = f"{name}{k}", k + 1
    return n


def decl_span_start(text, i):
    """Index of the newline before a declaration's first line, its annotation lines included."""
    s = text.rfind("\n", 0, i)
    while s > 0:
        prev = text.rfind("\n", 0, s)
        if re.fullmatch(r"[ \t]*@\w+(\([^\n]*\))?[ \t]*", text[prev + 1:s]):
            s = prev
        else:
            break
    return max(s, 0)


def line_of(text, i):
    return text.count("\n", 0, i) + 1


class Result:
    def __init__(self):
        self.count = collections.Counter()     # transform -> rewrites
        self.flags = collections.defaultdict(list)   # transform -> ["file:line what"]


def edit_spans(text, edits):
    """Apply (start, end, replacement) edits that do not overlap, last first."""
    for s, e, r in sorted(edits, key=lambda x: -x[0]):
        text = text[:s] + r + text[e:]
    return text


# --------------------------------------------------------------------------- T0 @Mod constructor (§5/§8)

BUS_CALL = r"(?:FMLJavaModLoadingContext\.get\(\)\.getModEventBus\(\)|(?:net\.neoforged\.fml\.)?ModLoadingContext\.get\(\)\.getActiveContainer\(\)\.getEventBus\(\))"


def t_modctor(path, text, ctx, res):
    cm = re.search(r"@Mod\s*\([^)]*\)\s*(?:public\s+)?(?:final\s+)?class\s+(\w+)", text)
    if not cm:
        return text
    cls = cm.group(1)
    m = re.search(r"(?m)^([ \t]*)public\s+%s\s*\(\s*\)\s*\{" % re.escape(cls), text)
    if not m:
        return text
    b0 = m.end() - 1; b1 = match(text, b0)
    body = text[b0:b1 + 1]
    bus = fresh("modEventBus", body) if not re.search(r"IEventBus\s+modEventBus\s*=\s*" + BUS_CALL, body) else "modEventBus"
    nb = re.sub(r"\n[ \t]*(?:final\s+)?IEventBus\s+%s\s*=\s*%s\s*;[ \t]*" % (re.escape(bus), BUS_CALL), "", body)
    nb = re.sub(BUS_CALL, bus, nb)
    nb = re.sub(r"(?:net\.neoforged\.fml\.)?ModLoadingContext\.get\(\)(?:\.getActiveContainer\(\))?\.registerConfig\(",
                "modContainer.registerConfig(", nb)
    text = text[:m.start()] + f"{m.group(1)}public {cls}(IEventBus {bus}, ModContainer modContainer) " + nb + text[b1 + 1:]
    ctx["imports"] |= {"net.neoforged.bus.api.IEventBus", "net.neoforged.fml.ModContainer"}
    for f in ("net.neoforged.fml.javafmlmod.FMLJavaModLoadingContext", "net.minecraftforge.fml.javafmlmod.FMLJavaModLoadingContext"):
        text = remove_import_if_unused(text, f)
    res.count["modctor"] += 1
    return text


# --------------------------------------------------------------------------- T1 tick events (§20/§21)

TICK = {  # old nested type -> (new fqn, accessor renames)
    "ServerTickEvent": ("net.neoforged.neoforge.event.tick.ServerTickEvent", {}),
    "PlayerTickEvent": ("net.neoforged.neoforge.event.tick.PlayerTickEvent", {"player": "getEntity()"}),
    "LevelTickEvent": ("net.neoforged.neoforge.event.tick.LevelTickEvent", {"level": "getLevel()"}),
    "ClientTickEvent": ("net.neoforged.neoforge.client.event.ClientTickEvent", {}),
    "RenderTickEvent": ("net.neoforged.neoforge.client.event.RenderFrameEvent", {}),
}
PHASE_CMP = r"%s\.phase\s*(==|!=)\s*(?:TickEvent\.)?Phase\.(START|END)"


def _simplify(body):
    """Fold the constants a removed phase check leaves behind. Only the shapes it produces."""
    for _ in range(4):
        b = body
        body = re.sub(r"\(\s*true\s*&&\s*", "(", body)
        body = re.sub(r"\(\s*false\s*\|\|\s*", "(", body)
        body = re.sub(r"\s*&&\s*true\s*\)", ")", body)
        body = re.sub(r"\s*\|\|\s*false\s*\)", ")", body)
        body = re.sub(r"\n[ \t]*if\s*\(\s*false\s*\)\s*return\s*;[ \t]*", "", body)
        body = re.sub(r"\n[ \t]*if\s*\(\s*false\s*\)\s*\{\s*return\s*;\s*\}[ \t]*", "", body)
        m = re.search(r"\n[ \t]*if\s*\(\s*false\s*\)\s*\{", body)
        if m:
            k = match(body, m.end() - 1)
            if k > 0 and not re.match(r"\s*else\b", body[k + 1:]):
                body = body[:m.start()] + body[k + 1:]
                continue
        m = re.search(r"\bif\s*\(\s*true\s*\)\s*", body)
        if m:
            j = m.end()
            if body[j] == "{":
                k = match(body, j)
                if k > 0 and not re.match(r"\s*else\b", body[k + 1:]):
                    inner = body[j + 1:k]
                    ind = re.match(r"[ \t]*", body[body.rfind("\n", 0, m.start()) + 1:]).group(0)
                    inner = re.sub(r"(?m)^" + re.escape(ind) + r"    ", ind, inner).lstrip("\n").rstrip()
                    line_start = body.rfind("\n", 0, m.start()) + 1
                    body = body[:line_start] + inner + body[k + 1:]
            else:
                body = body[:m.start()] + body[j:]
        if body == b:
            break
    return body


def t_tick(path, text, ctx, res):
    edits = []
    for mt in methods(text):
        if len(mt.params) != 1:
            continue
        p = mt.params[0]
        typ, name = ptype(p), pname(p)
        m = re.fullmatch(r"(?:TickEvent\.)?(\w+TickEvent)", typ)
        if typ in ("LivingEvent.LivingTickEvent", "LivingTickEvent"):
            body = text[mt.body_open:mt.body_close + 1]
            living = fresh("living", body)
            nb = re.sub(r"(?<![\w$.])%s\.getEntity\(\)" % re.escape(name), living, body)
            ind = re.match(r"[ \t]*", text[text.rfind("\n", 0, mt.start) + 1:]).group(0) + "    "
            nb = ("{\n" + f"{ind}if (!({name}.getEntity() instanceof net.minecraft.world.entity.LivingEntity {living})) return;"
                  + nb[1:])
            edits.append((mt.params_open + 1, mt.params_close, p.replace(typ, "EntityTickEvent.Pre")))
            edits.append((mt.body_open, mt.body_close + 1, nb))
            ctx["imports"].add("net.neoforged.neoforge.event.tick.EntityTickEvent")
            res.count["tick"] += 1
            continue
        if not m or m.group(1) not in TICK:
            continue
        kind = m.group(1)
        fqn, acc = TICK[kind]
        body = text[mt.body_open:mt.body_close + 1]
        phases = {(a, b) for a, b in re.findall(PHASE_CMP % re.escape(name), body)}
        used = {b for _a, b in phases}
        if len(used) > 1:
            ann = text[decl_span_start(text, mt.start):mt.start]
            if "@SubscribeEvent" not in ann or re.search(r"::%s\b" % re.escape(mt.name), text):
                res.flags["tick"].append(f"{path}:{line_of(text, mt.start)} {mt.name}: checks both phases and is not a "
                                         "@SubscribeEvent method; split it by hand")
                continue
            new_simple = TICK[kind][0].rsplit(".", 1)[-1]
            halves = []
            for ph, sub, nm in (("START", "Pre", mt.name + "Pre"), ("END", "Post", mt.name)):
                nb = re.sub(PHASE_CMP % re.escape(name),
                            lambda q: "true" if (q.group(1) == "==") == (q.group(2) == ph) else "false", body)
                nb = _simplify(nb)
                nb = re.sub(r"\n[ \t]*return\s*;\s*\n(?:[ \t]*\n)*([ \t]*\})$", r"\n\1", nb)   # a void method's last return
                for old, new in TICK[kind][1].items():
                    nb = re.sub(r"(?<![\w$.])%s\.%s(?![\w$(])" % (re.escape(name), old), f"{name}.{new}", nb)
                head = text[decl_span_start(text, mt.start):mt.params_open]
                head = re.sub(r"\b%s(\s*)$" % re.escape(mt.name), nm + r"\1", head)
                halves.append(head + "(" + p.replace(typ, f"{new_simple}.{sub}") + ") " + nb)
            edits.append((decl_span_start(text, mt.start), mt.body_close + 1, "\n".join(halves)))
            ctx["imports"].add(TICK[kind][0])
            res.count["tick"] += 1
            continue
        phase = used.pop() if used else None
        sub = "Pre" if phase == "START" else "Post"
        if kind == "RenderTickEvent":
            sub = "Pre" if phase == "START" else "Post"
        nb = re.sub(PHASE_CMP % re.escape(name), lambda q: "true" if q.group(1) == "==" else "false", body)
        nb = _simplify(nb)
        for old, new in acc.items():
            nb = re.sub(r"(?<![\w$.])%s\.%s(?![\w$(])" % (re.escape(name), old), f"{name}.{new}", nb)
        if re.search(r"(?<![\w$.])%s\.(side|phase|type)\b" % re.escape(name), nb):
            res.flags["tick"].append(f"{path}:{line_of(text, mt.start)} {mt.name}: reads .side/.phase/.type; port by hand")
            continue
        if phase is None:
            res.flags["tick"].append(f"{path}:{line_of(text, mt.start)} {mt.name}: ran on BOTH phases; "
                                     f"rewritten to {kind}.Post (check it did not rely on START)")
        new_simple = fqn.rsplit(".", 1)[-1]
        edits.append((mt.params_open + 1, mt.params_close, p.replace(typ, f"{new_simple}.{sub}")))
        if nb != body:
            edits.append((mt.body_open, mt.body_close + 1, nb))
        ctx["imports"].add(fqn)
        res.count["tick"] += 1
    text = edit_spans(text, edits)
    if edits:
        text = re.sub(r"(?m)^import\s+net\.(?:neoforged\.neoforge|minecraftforge)\.event\.TickEvent\.\w+\s*;[ \t]*\n", "", text)
        text = remove_import_if_unused(text, "net.neoforged.neoforge.event.TickEvent")
        text = remove_import_if_unused(text, "net.minecraftforge.event.TickEvent")
        text = remove_import_if_unused(text, "net.neoforged.neoforge.event.entity.living.LivingEvent")
    return text


# --------------------------------------------------------------------------- T2 DistExecutor (§6)

def t_dist(path, text, ctx, res):
    out, i, n = [], 0, 0
    pat = re.compile(r"DistExecutor\.(unsafeRunWhenOn|safeRunWhenOn|runWhenOn)\(")
    while True:
        m = pat.search(text, i)
        if not m:
            out.append(text[i:]); break
        p0 = m.end() - 1
        p1 = match(text, p0)
        args = split_args(text[p0 + 1:p1]) if p1 > 0 else []
        sup = args[1].strip() if len(args) == 2 else ""
        dist = args[0].strip() if args else ""
        call = None
        mm = re.fullmatch(r"\(\)\s*->\s*\(\)\s*->\s*(.+)", sup, re.S)
        if mm:
            call = mm.group(1).strip()
            if call.startswith("{"):
                call = None
        mm2 = re.fullmatch(r"\(\)\s*->\s*([\w.$]+)::(\w+)", sup, re.S)
        if mm2:
            call = f"{mm2.group(1)}.{mm2.group(2)}()"
        if call is None or dist not in ("Dist.CLIENT", "Dist.DEDICATED_SERVER"):
            res.flags["dist"].append(f"{path}:{line_of(text, m.start())} DistExecutor shape not recognised")
            out.append(text[i:m.end()]); i = m.end(); continue
        cond = f"FMLEnvironment.dist == {dist}"
        before = text[:m.start()]
        after = text[p1 + 1:]
        lam = re.search(r"\(\)\s*->\s*$", before)
        if lam:                                     # () -> DistExecutor...(…)   : make it a block lambda
            out.append(text[i:m.start()]); out.append(f"{{ if ({cond}) {call}; }}")
        elif re.match(r"\s*;", after) and re.search(r"(^|[;{}])\s*$", before):   # statement
            out.append(text[i:m.start()]); out.append(f"if ({cond}) {call}")
        else:
            res.flags["dist"].append(f"{path}:{line_of(text, m.start())} DistExecutor used as a value")
            out.append(text[i:m.end()]); i = m.end(); continue
        i = p1 + 1; n += 1
    if n:
        res.count["dist"] += n
        text = "".join(out)
        ctx["imports"].add("net.neoforged.fml.loading.FMLEnvironment")
        ctx["imports"].add("net.neoforged.api.distmarker.Dist")
        for f in ("net.neoforged.fml.DistExecutor", "net.minecraftforge.fml.DistExecutor"):
            text = remove_import_if_unused(text, f)
    return text


# --------------------------------------------------------------------------- T3 AttributeModifier (§41/§51)

def _rl_path(s):
    return re.sub(r"[^a-z0-9_./-]", "_", s.lower())


def t_attrmod(path, text, ctx, res):
    edits, idmap, n = [], {}, 0
    # declared ResourceLocation names in this file (a Curios id parameter after T5, a constant)
    rl_names = set(re.findall(r"(?:ResourceLocation|Identifier)\s+(\w+)", text))
    for m in re.finditer(r"new\s+AttributeModifier\(", text):
        p0 = m.end() - 1
        p1 = match(text, p0)
        if p1 < 0:
            continue
        args = split_args(text[p0 + 1:p1])
        if len(args) != 4:
            continue
        a0, a1 = args[0].strip(), args[1].strip()
        lit = re.fullmatch(r'"([^"\\]+)"', a1)
        if a0 in rl_names:
            rl = a0
        elif lit:
            rl = f'ResourceLocation.fromNamespaceAndPath("{ctx["modid"]}", "{_rl_path(lit.group(1))}")'
            ctx["imports"].add("net.minecraft.resources.ResourceLocation")
        else:
            res.flags["attrmod"].append(f"{path}:{line_of(text, m.start())} AttributeModifier name is not a literal")
            continue
        if re.fullmatch(r"[\w.]+", a0) and a0 not in rl_names:
            idmap[a0] = rl
        lead = re.match(r"\s*", args[0]).group(0)
        edits.append((p0 + 1, p1, lead + rl + "," + ",".join(args[2:])))
        n += 1
    # removeModifier(UUID) / getModifier(UUID) / hasModifier(UUID) with a UUID we just mapped
    for m in re.finditer(r"\.(removeModifier|getModifier|hasModifier|removePermanentModifier)\(\s*([\w.]+)\s*\)", text):
        if m.group(2) in idmap:
            s = m.start(2)
            edits.append((s, s + len(m.group(2)), idmap[m.group(2)])); n += 1
        elif re.search(r"UUID\s+%s\b" % re.escape(m.group(2)), text):
            res.flags["attrmod"].append(f"{path}:{line_of(text, m.start())} {m.group(1)}({m.group(2)}): UUID with no "
                                        "modifier in this file to take its id from")
    text = edit_spans(text, edits)
    if n:
        res.count["attrmod"] += n
    # 1.21 keys every attribute map by Holder<Attribute> (vanilla and Curios alike)
    t2 = re.sub(r"Multimap<Attribute,\s*AttributeModifier>", "Multimap<Holder<Attribute>, AttributeModifier>", text)
    if t2 != text:
        text = t2; res.count["attrmod"] += 1
        ctx["imports"].add("net.minecraft.core.Holder")
    return text


# --------------------------------------------------------------------------- T4 item NBT (§28/§53)

ITEMSTACK_CALLS = r"(?:getItem|getMainHandItem|getOffhandItem|getItemInHand|getItemBySlot|getUseItem|getCarried|getResult|copy|getStackInSlot|getSelected|getPickResult)\([^()]*\)"
NBT_HELPER = """package {pkg};

import net.minecraft.core.component.DataComponents;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.component.CustomData;

/**
 * 1.20's ItemStack tag, on 1.21's {{@link DataComponents#CUSTOM_DATA}} component (NeoForge port).
 * Reads return a COPY: write through {{@link #update}} or {{@link #setTag}}, never by mutating a read.
 */
public final class ItemNbt {{
    private ItemNbt() {{}}

    public static boolean hasTag(ItemStack stack) {{
        return stack.has(DataComponents.CUSTOM_DATA);
    }}

    /** The tag, or null when the stack has none (as 1.20's getTag()). */
    public static CompoundTag getTag(ItemStack stack) {{
        CustomData data = stack.get(DataComponents.CUSTOM_DATA);
        return data == null ? null : data.copyTag();
    }}

    /** A copy of the tag, empty when absent. Writes to it are LOST unless passed to setTag. */
    public static CompoundTag getOrCreateTag(ItemStack stack) {{
        CompoundTag tag = getTag(stack);
        return tag == null ? new CompoundTag() : tag;
    }}

    public static void setTag(ItemStack stack, CompoundTag tag) {{
        if (tag == null || tag.isEmpty()) stack.remove(DataComponents.CUSTOM_DATA);
        else stack.set(DataComponents.CUSTOM_DATA, CustomData.of(tag));
    }}

    public static void update(ItemStack stack, java.util.function.Consumer<CompoundTag> edit) {{
        CustomData.update(DataComponents.CUSTOM_DATA, stack, edit);
    }}

    public static CompoundTag getTagElement(ItemStack stack, String key) {{
        CompoundTag tag = getTag(stack);
        return tag != null && tag.contains(key, 10) ? tag.getCompound(key) : null;
    }}
}}
"""


def _stack_receivers(text):
    names = set(re.findall(r"\bItemStack\s+(\w+)\s*[=;,)]", text))
    names |= set(re.findall(r"\bItemStack\s+(\w+)\s*:", text))         # for (ItemStack s : …)
    return names


def t_nbt(path, text, ctx, res):
    names = _stack_receivers(text)
    recv = r"(?P<r>(?<![\w$.])(?:%s)|[\w$.]+\.%s|(?<![\w$.])%s)" % (
        "|".join(map(re.escape, sorted(names, key=len, reverse=True))) or "(?!x)x", ITEMSTACK_CALLS, ITEMSTACK_CALLS)
    n, H = 0, "ItemNbt"

    def rw(pattern, repl):
        nonlocal text, n
        text, k = re.subn(pattern, repl, text)
        n += k

    # writes chained straight off getOrCreateTag(): a CustomData.update keeps them
    rw(recv + r"\.getOrCreateTag\(\)\.(?P<w>put\w*|remove)\((?P<a>(?:[^()]|\([^()]*\))*)\)",
       lambda m: f"{H}.update({m.group('r')}, t -> t.{m.group('w')}({m.group('a')}))")
    rw(recv + r"\.getTag\(\)\.(?P<w>put\w*|remove)\((?P<a>(?:[^()]|\([^()]*\))*)\)",
       lambda m: f"{H}.update({m.group('r')}, t -> t.{m.group('w')}({m.group('a')}))")
    rw(recv + r"\.(?P<f>getOrCreateTag|getTag|hasTag)\(\)", lambda m: f"{H}.{m.group('f')}({m.group('r')})")
    rw(recv + r"\.setTag\(", lambda m: f"{H}.setTag({m.group('r')}, ")
    rw(recv + r"\.getTagElement\(", lambda m: f"{H}.getTagElement({m.group('r')}, ")
    rw(recv + r"\.removeTagKey\((?P<a>[^()]*)\)", lambda m: f"{H}.update({m.group('r')}, t -> t.remove({m.group('a')}))")
    # an ItemNbt.setTag(x, ) left by a no-arg setTag cannot happen; a getOrCreateTag() stored and then mutated can:
    for m in re.finditer(r"CompoundTag\s+(\w+)\s*=\s*ItemNbt\.getOrCreateTag\((\w+)\)\s*;", text):
        tail = text[m.end():m.end() + 4000]
        if re.search(r"\b%s\.(put\w*|remove)\(" % re.escape(m.group(1)), tail) and \
           not re.search(r"ItemNbt\.setTag\(\s*%s\s*,\s*%s\s*\)" % (re.escape(m.group(2)), re.escape(m.group(1))), tail):
            res.flags["nbt"].append(f"{path}:{line_of(text, m.start())} {m.group(1)} = getOrCreateTag() is mutated "
                                    "afterwards: add ItemNbt.setTag(stack, tag) after the writes")
    if n:
        res.count["nbt"] += n
        ctx["imports"].add(ctx["nbt_fqn"])
        ctx["need_nbt_helper"] = True
    for m in re.finditer(r"\.(getOrCreateTag|getTag|hasTag)\(\)", text):
        pre = text[max(0, m.start() - 40):m.start()]
        if not pre.endswith(("ItemNbt", "Handle")) and "ItemNbt." not in pre[-12:]:
            ln = text[text.rfind("\n", 0, m.start()) + 1:text.find("\n", m.start())].strip()
            if re.search(r"ItemStack|stack|Stack", ln):
                res.flags["nbt"].append(f"{path}:{line_of(text, m.start())} {m.group(1)}() on a receiver not known "
                                        "to be an ItemStack: " + ln[:90])
    return text


# --------------------------------------------------------------------------- T5 hook signatures

def _enclosing(ms, i):
    best = None
    for x in ms:
        if x.body_open < i < x.body_close and (best is None or x.body_open > best.body_open):
            best = x
    return best


def _extends(text, *bases):
    return re.search(r"\bclass\s+\w+(?:<[^{]*?>)?\s+extends\s+(?:[\w.]+\.)?(%s)\b" % "|".join(bases), text)


BE_BASES = ("BlockEntity", "BaseContainerBlockEntity", "RandomizableContainerBlockEntity", "SyncedBlockEntity")


def t_hooks(path, text, ctx, res):
    edits = []
    ms = methods(text)
    flag = lambda i, s: res.flags["hooks"].append(f"{path}:{line_of(text, i)} {s}")

    def sig(mt, new_params, name=None, ret=None):
        edits.append((mt.params_open + 1, mt.params_close, ", ".join(new_params)))
        if name or ret:
            hs = mt.start
            old = text[hs:mt.params_open]
            new = old
            if ret:
                new = re.sub(r"%s(\s+%s\s*)$" % (re.escape(mt.ret), re.escape(mt.name)), ret + r"\1", new)
            if name:
                new = re.sub(r"\b%s(\s*)$" % re.escape(mt.name), name + r"\1", new)
            edits.append((hs, mt.params_open, new))

    def body_sub(mt, pattern, repl, count=0):
        b = text[mt.body_open:mt.body_close + 1]
        nb = re.sub(pattern, repl, b, count=count)
        if nb != b:
            edits.append((mt.body_open, mt.body_close + 1, nb))
        return nb

    is_be = _extends(text, *BE_BASES)
    is_saved = _extends(text, "SavedData")
    for mt in ms:
        P, names = mt.params, [pname(p) for p in mt.params]
        types = [ptype(p) for p in P]
        body = text[mt.body_open:mt.body_close + 1]
        if mt.name == "mouseScrolled" and types == ["double", "double", "double"]:
            sx = fresh("scrollX", body)
            sig(mt, P[:2] + [f"double {sx}", P[2]])
            body_sub(mt, r"super\.mouseScrolled\(([^,()]+),([^,()]+),([^,()]+)\)",
                     lambda m: f"super.mouseScrolled({m.group(1)},{m.group(2)}, {sx},{m.group(3)})")
            res.count["hooks"] += 1
        elif mt.name == "finalizeSpawn" and len(P) == 5 and types[-1] == "CompoundTag":
            tag = names[-1]
            nb = re.sub(r"super\.finalizeSpawn\(((?:[^()]|\([^()]*\))*?),\s*%s\s*\)" % re.escape(tag),
                        r"super.finalizeSpawn(\1)", body)
            if re.search(r"(?<![\w$.])%s(?![\w$])" % re.escape(tag), nb):
                flag(mt.start, f"finalizeSpawn reads its removed CompoundTag '{tag}'"); continue
            sig(mt, P[:4])
            if nb != body:
                edits.append((mt.body_open, mt.body_close + 1, nb))
            res.count["hooks"] += 1
        elif mt.name == "getStandingEyeHeight" and len(P) == 2:
            m = re.fullmatch(r"\{\s*return\s+(.+?);\s*\}", body, re.S)
            if not m:
                flag(mt.start, "getStandingEyeHeight has a body that is not one return"); continue
            pose, dims = names
            expr = re.sub(r"(?<![\w$.])%s\.(width|height)(?![\w$(])" % re.escape(dims), dims + r".\1()", m.group(1))
            ind = re.match(r"[ \t]*", text[text.rfind("\n", 0, mt.start) + 1:]).group(0)
            nb = (f"{{\n{ind}    EntityDimensions {dims} = super.getDefaultDimensions({pose});\n"
                  f"{ind}    return {dims}.withEyeHeight({expr});\n{ind}}}")
            hs = text.rfind("\n", 0, mt.start) + 1
            head = text[hs:mt.params_open]
            head = re.sub(r"\bfloat\s+getStandingEyeHeight", "EntityDimensions getDefaultDimensions", head)
            edits.append((hs, mt.params_open, head))
            edits.append((mt.params_open + 1, mt.params_close, P[0]))
            edits.append((mt.body_open, mt.body_close + 1, nb))
            res.count["hooks"] += 1
        elif mt.name == "getMobType" and not P:
            if re.fullmatch(r"\{\s*return\s+MobType\.UNDEFINED\s*;\s*\}", body):
                edits.append((decl_span_start(text, mt.start), mt.body_close + 1, ""))
                res.count["hooks"] += 1
            else:
                flag(mt.start, "getMobType() returns a real type: tag the entity type (§30) and delete the override")
        elif mt.name == "getAddEntityPacket" and not P and "NetworkHooks" in body:
            edits.append((decl_span_start(text, mt.start), mt.body_close + 1, ""))
            res.count["hooks"] += 1
        elif mt.name == "getExperienceReward" and not P:
            sig(mt, ["ServerLevel level", "@Nullable Entity killer"]); res.count["hooks"] += 1
            ctx["imports"] |= {"net.minecraft.server.level.ServerLevel", "javax.annotation.Nullable",
                               "net.minecraft.world.entity.Entity"}
        elif mt.name == "dropExperience" and not P:
            sig(mt, ["@Nullable Entity attacker"]); res.count["hooks"] += 1
            ctx["imports"] |= {"javax.annotation.Nullable", "net.minecraft.world.entity.Entity"}
        elif mt.name == "getGravity" and not P and mt.ret == "float":
            hs = text.rfind("\n", 0, mt.start) + 1
            head = text[hs:mt.params_open]
            edits.append((hs, mt.params_open, re.sub(r"\bfloat\s+getGravity", "double getDefaultGravity", head)))
            res.count["hooks"] += 1
        elif mt.name == "getAttributeModifiers" and len(P) == 3 and types[0] == "SlotContext" and types[1] == "UUID":
            sig(mt, [P[0], P[1].replace("UUID", "ResourceLocation"), P[2]])
            ctx["imports"].add("net.minecraft.resources.ResourceLocation")
            res.count["hooks"] += 1
        elif mt.name == "getArmorTexture" and len(P) == 4 and types[3] == "String":
            if re.search(r"(?<![\w$.])%s(?![\w$])" % re.escape(names[3]), body):
                flag(mt.start, "getArmorTexture reads its 'type' string: map it to the ArmorMaterial.Layer by hand"); continue
            nb = re.sub(r"\breturn\s+(?!null\b)([^;]+);", r"return ResourceLocation.parse(\1);", body)
            sig(mt, P[:3] + ["ArmorMaterial.Layer layer", "boolean innerModel"], ret="ResourceLocation")
            edits.append((mt.body_open, mt.body_close + 1, nb))
            ctx["imports"] |= {"net.minecraft.resources.ResourceLocation", "net.minecraft.world.item.ArmorMaterial"}
            res.count["hooks"] += 1
        elif is_be and mt.name in ("load", "saveAdditional") and types == ["CompoundTag"]:
            reg = fresh("registries", body)
            nb = re.sub(r"super\.(load|saveAdditional)\(\s*%s\s*\)" % re.escape(names[0]),
                        lambda m: f"super.{'loadAdditional' if m.group(1) == 'load' else 'saveAdditional'}({names[0]}, {reg})", body)
            nb = re.sub(r"ContainerHelper\.(loadAllItems|saveAllItems)\(([^,()]+),([^,()]+)\)",
                        lambda m: f"ContainerHelper.{m.group(1)}({m.group(2)},{m.group(3)}, {reg})", nb)
            sig(mt, [P[0], f"HolderLookup.Provider {reg}"], name="loadAdditional" if mt.name == "load" else None)
            edits.append((mt.body_open, mt.body_close + 1, nb))
            ctx["imports"].add("net.minecraft.core.HolderLookup")
            res.count["hooks"] += 1
        elif is_be and mt.name == "getUpdateTag" and not P:
            reg = fresh("registries", body)
            sig(mt, [f"HolderLookup.Provider {reg}"])
            body_sub(mt, r"(saveWithFullMetadata|saveWithoutMetadata|saveWithId)\(\)", r"\1(%s)" % reg)
            ctx["imports"].add("net.minecraft.core.HolderLookup")
            res.count["hooks"] += 1
        elif is_saved and mt.name == "save" and types == ["CompoundTag"]:
            reg = fresh("registries", body)
            sig(mt, [P[0], f"HolderLookup.Provider {reg}"])
            ctx["imports"].add("net.minecraft.core.HolderLookup")
            res.count["hooks"] += 1
        elif mt.name == "use" and types == ["BlockState", "Level", "BlockPos", "Player", "InteractionHand", "BlockHitResult"]:
            hand = names[4]
            if re.search(r"(?<![\w$.])%s(?![\w$])" % re.escape(hand), body):
                flag(mt.start, "Block.use reads its hand: port to useItemOn (returns ItemInteractionResult, §127)"); continue
            nb = re.sub(r"super\.use\(([^,()]+),([^,()]+),([^,()]+),([^,()]+),[^,()]+,([^,()]+)\)",
                        r"super.useWithoutItem(\1,\2,\3,\4,\5)", body)
            sig(mt, P[:4] + [P[5]], name="useWithoutItem")
            if nb != body:
                edits.append((mt.body_open, mt.body_close + 1, nb))
            res.count["hooks"] += 1
    # renderBackground(g) inside render(g, mx, my, pt)
    for m in re.finditer(r"\b(this\.|super\.)?renderBackground\(\s*(\w+)\s*\)", text):
        mt = _enclosing(ms, m.start())
        if mt and mt.name == "render" and len(mt.params) == 4:
            g, mx, my, pt = [pname(p) for p in mt.params]
            edits.append((m.start(), m.end(), f"{m.group(1) or ''}renderBackground({m.group(2)}, {mx}, {my}, {pt})"))
            res.count["hooks"] += 1
        else:
            flag(m.start(), "renderBackground(g) outside render(g, mouseX, mouseY, partialTick)")
    return edit_spans(text, _dedupe(edits, res, path))


def _dedupe(edits, res, path):
    """Drop an edit that overlaps an earlier one (two transforms on one span); report it."""
    kept, spans = [], []
    for e in edits:
        if any(not (e[1] <= s or e[0] >= t) and not (e[0] == s and e[1] == t) for s, t in spans):
            # nested edits on one method (signature + body) never overlap; anything else is a conflict
            if not any(e[0] >= s and e[1] <= t for s, t in spans):
                res.flags["internal"].append(f"{path}: overlapping edits at {e[0]}")
            continue
        kept.append(e); spans.append((e[0], e[1]))
    return kept


# --------------------------------------------------------------------------- T6 GeckoLib 4.8 colour (§112/§138)

GEO_COLOUR = ("preRender", "actuallyRender", "renderRecursively", "renderFinal", "renderCubesOfBone",
              "renderChildBones", "renderCube", "postRender", "applyRenderLayers", "render")


def t_geo(path, text, ctx, res):
    if "geckolib" not in text:
        return text
    edits = []
    for mt in methods(text):
        P = mt.params
        if mt.name in GEO_COLOUR and len(P) >= 4 and [ptype(p) for p in P[-4:]] == ["float"] * 4 \
                and [pname(p) for p in P[-4:]] == ["red", "green", "blue", "alpha"]:
            body = text[mt.body_open:mt.body_close + 1]
            nb = re.sub(r",\s*red,\s*green,\s*blue,\s*alpha\s*\)", ", colour)", body)
            if re.search(r"(?<![\w$.])(red|green|blue|alpha)(?![\w$])", nb):
                flag = f"{path}:{line_of(text, mt.start)} {mt.name} reads red/green/blue/alpha: unpack 'colour' by hand"
                res.flags["geo"].append(flag); continue
            edits.append((mt.params_open + 1, mt.params_close, ", ".join(P[:-4] + ["int colour"])))
            if nb != body:
                edits.append((mt.body_open, mt.body_close + 1, nb))
            res.count["geo"] += 1
        elif mt.name == "getPackedOverlay" and len(P) == 2 and ptype(P[1]) == "float":
            pt = fresh("partialTick", text[mt.body_open:mt.body_close + 1])
            edits.append((mt.params_open + 1, mt.params_close, ", ".join(P + [f"float {pt}"])))
            res.count["geo"] += 1
    text = edit_spans(text, edits)
    # call sites passing four float colour arguments last: reRender(…, 1, 1, 1, 1)
    def call(m):
        p0 = m.end() - 1
        return p0
    out, i = [], 0
    for m in re.finditer(r"\.reRender\(", text):
        p0 = m.end() - 1; p1 = match(text, p0)
        args = split_args(text[p0 + 1:p1]) if p1 > 0 else []
        if len(args) >= 13:
            c = [a.strip() for a in args[-4:]]
            if all(re.fullmatch(r"1(\.0*)?[fFdD]?", x) for x in c):
                packed = "-1"
            else:
                packed = f"net.minecraft.util.FastColor.ARGB32.colorFromFloat({c[3]}, {c[0]}, {c[1]}, {c[2]})"
            out.append(text[i:p0 + 1] + ",".join(args[:-4]) + ", " + packed + ")")
            i = p1 + 1; res.count["geo"] += 1
    out.append(text[i:])
    return "".join(out)


# --------------------------------------------------------------------------- T7 VertexConsumer implementations (§107)

VC = {"vertex": ("addVertex", "float"), "color": ("setColor", None), "uv": ("setUv", None),
      "overlayCoords": ("setUv1", None), "uv2": ("setUv2", None), "normal": ("setNormal", None)}


def t_vc(path, text, ctx, res):
    if not re.search(r"\bimplements\s+(?:[\w.,\s]*,\s*)?VertexConsumer\b", text):
        return text
    edits = []
    for mt in methods(text):
        if mt.name in ("endVertex", "defaultColor", "unsetDefaultColor"):
            edits.append((decl_span_start(text, mt.start), mt.body_close + 1, "")); res.count["vc"] += 1
        elif mt.name in VC and mt.ret in ("VertexConsumer",):
            new, ptyp = VC[mt.name]
            P = mt.params
            if ptyp and all(ptype(p) == "double" for p in P):
                P = [p.replace("double", ptyp, 1) for p in P]
            hs = text.rfind("\n", 0, mt.start) + 1
            edits.append((hs, mt.params_open, re.sub(r"\b%s(\s*)$" % mt.name, new + r"\1", text[hs:mt.params_open])))
            edits.append((mt.params_open + 1, mt.params_close, ", ".join(P)))
            body = text[mt.body_open:mt.body_close + 1]
            nb = re.sub(r"\.vertex\(", ".addVertex(", body)
            if nb != body:
                edits.append((mt.body_open, mt.body_close + 1, nb))
            res.count["vc"] += 1
    return edit_spans(text, edits)


# --------------------------------------------------------------------------- T8 source shapes the pack leaves

DEAD_IMPORTS = ("net.neoforged.neoforge.registries.ForgeRegistries", "net.neoforged.neoforge.common.capabilities.ForgeCapabilities",
                "net.neoforged.neoforge.network.NetworkHooks", "net.minecraft.world.entity.MobType",
                "net.neoforged.neoforge.event.TickEvent", "net.neoforged.fml.DistExecutor")


def t_source(path, text, ctx, res):
    n = 0
    if re.search(r"@EventBusSubscriber\b", text) and not re.search(r"import\s+net\.neoforged\.fml\.common\.(EventBusSubscriber|\*)\s*;", text) \
            and not re.search(r"@net\.neoforged\.fml\.common\.EventBusSubscriber", text):
        ctx["imports"].add("net.neoforged.fml.common.EventBusSubscriber"); n += 1
    for f in DEAD_IMPORTS:
        t2 = remove_import_if_unused(text, f)
        if t2 != text:
            text, n = t2, n + 1
    if n:
        res.count["source"] += n
    return text



# --------------------------------------------------------------------------- T9 changed vanilla signatures (catalogue §B-§J)

# Overrides whose PARAMETER LIST changed 1.20.1 -> 1.21.1 in a way a body need not notice: the new
# parameter is added under a fresh name (or a dropped one removed when the body never reads it), and the
# `super` call inside is rewritten to match. Each row: (method, old param types, new param list template,
# super-call args template). {pN} is the Nth old parameter's NAME, {new} the fresh name. Verified with
# javap on the NeoForge 21.1 recompile (catalogue #50, #103, #109, #119, R17e).
SIG_OVERRIDES = [
    ("applyRaidBuffs", ["int", "boolean"],
     "net.minecraft.server.level.ServerLevel {new}, int {p0}, boolean {p1}", "{new}, {p0}, {p1}", "serverLevel"),
    ("populateDefaultEquipmentEnchantments", ["RandomSource", "DifficultyInstance"],
     "net.minecraft.world.level.ServerLevelAccessor {new}, RandomSource {p0}, DifficultyInstance {p1}", "{new}, {p0}, {p1}", "level"),
    ("setupRotations", ["*", "PoseStack", "float", "float", "float"],
     "{t0} {p0}, PoseStack {p1}, float {p2}, float {p3}, float {p4}, float {new}", "{p0}, {p1}, {p2}, {p3}, {p4}, {new}", "scale"),
    ("getExperienceReward", [],
     "net.minecraft.server.level.ServerLevel {new}, @javax.annotation.Nullable net.minecraft.world.entity.Entity killer", "{new}, killer", "level"),
]
# Overrides that DROP a parameter: (method, old types, index dropped, new prefix param, new prefix super arg).
SIG_DROPS = [
    ("finalizeSpawn", ["ServerLevelAccessor", "DifficultyInstance", "MobSpawnType", "SpawnGroupData", "CompoundTag"], 4, None),
    ("dropCustomDeathLoot", ["DamageSource", "int", "boolean"], 1, ("net.minecraft.server.level.ServerLevel", "level")),
]


def _strip_ann(p):
    return re.sub(r"@[\w.]+(\([^)]*\))?\s*", "", p).replace("final ", "").strip()


def _types_match(params, want):
    if len(params) != len(want):
        return False
    for p, w in zip(params, want):
        t = ptype(_strip_ann(p)).split(".")[-1]
        if w != "*" and t != w:
            return False
    return True


def t_sigs(path, text, ctx, res):
    edits = []
    for mt in methods(text):
        body = text[mt.body_open:mt.body_close + 1]
        for name, want, tmpl, sup, base in SIG_OVERRIDES:
            if mt.name != name or not _types_match(mt.params, want):
                continue
            names = [pname(p) for p in mt.params]
            new = fresh(base, body + " ".join(names))
            fmt = {f"p{i}": n for i, n in enumerate(names)} | {"new": new,
                   "t0": ptype(_strip_ann(mt.params[0])) if mt.params else ""}
            edits.append((mt.params_open + 1, mt.params_close, tmpl.format(**fmt)))
            nb = re.sub(r"\bsuper\.%s\(([^()]*(?:\([^()]*\)[^()]*)*)\)" % name, lambda q: f"super.{name}({sup.format(**fmt)})", body)
            if nb != body:
                edits.append((mt.body_open, mt.body_close + 1, nb))
            res.count["sigs"] += 1
        for name, want, drop, prefix in SIG_DROPS:
            if mt.name != name or not _types_match(mt.params, want):
                continue
            names = [pname(p) for p in mt.params]
            dn = names[drop]
            no_super = re.sub(r"\bsuper\.%s\(([^()]*(?:\([^()]*\)[^()]*)*)\)" % name, "", body)
            if re.search(r"(?<![\w$.])%s(?![\w$])" % re.escape(dn), no_super):
                # the body reads the dropped parameter (e.g. looting): say so, never guess its replacement
                res.flags["sigs"].append(f"{path}:{line_of(text, mt.start)} {name}: the body reads `{dn}`, which "
                                         "1.21.1 no longer passes; port it by hand")
                continue
            ps = [p for i, p in enumerate(mt.params) if i != drop]
            args = [n for i, n in enumerate(names) if i != drop]
            if prefix:
                new = fresh(prefix[1], body + " ".join(names))
                ps, args = [f"{prefix[0]} {new}"] + ps, [new] + args
            edits.append((mt.params_open + 1, mt.params_close, ", ".join(ps)))
            nb = re.sub(r"\bsuper\.%s\(([^()]*(?:\([^()]*\)[^()]*)*)\)" % name,
                        lambda q: f"super.{name}({', '.join(args)})", body)
            if nb != body:
                edits.append((mt.body_open, mt.body_close + 1, nb))
            res.count["sigs"] += 1
    text = edit_spans(text, edits)

    # CALL sites whose argument list changed and whose new argument the caller has in scope
    def calls(t, name):
        for m in re.finditer(r"(?<![\w$])%s\(" % re.escape(name), t):
            e = match(t, m.end() - 1)
            if e > 0:
                yield m, e
    # finalizeSpawn(level, difficulty, reason, data, null) -> drop the trailing CompoundTag (catalogue #50)
    out, last = [], 0
    for m, e in list(calls(text, "finalizeSpawn")):
        args = split_args(text[m.end():e])
        if len(args) == 5 and re.fullmatch(r"\s*(?:null|\(\s*CompoundTag\s*\)\s*null)\s*", args[4]):
            out.append((m.end(), e, ",".join(args[:4]).strip()))
            res.count["sigs"] += 1
    text = edit_spans(text, out)
    # JigsawPlacement.addPieces gained pool aliases, dimension padding and liquid settings (R17c): vanilla's own
    # defaults for a structure that names none of them
    out = []
    for m, e in list(calls(text, "JigsawPlacement.addPieces")):
        if len(split_args(text[m.end():e])) == 8:
            out.append((e, e, ", net.minecraft.world.level.levelgen.structure.pools.alias.PoolAliasLookup.EMPTY, "
                        "net.minecraft.world.level.levelgen.structure.pools.DimensionPadding.ZERO, "
                        "net.minecraft.world.level.levelgen.structure.templatesystem.LiquidSettings.APPLY_WATERLOGGING"))
            res.count["sigs"] += 1
    text = edit_spans(text, out)
    # populateDefaultEquipmentEnchantments(random, difficulty) inside a method holding a ServerLevelAccessor
    out = []
    for mt in methods(text):
        lvl = next((pname(p) for p in mt.params if re.search(r"\b(ServerLevelAccessor|ServerLevel)\b", ptype(_strip_ann(p)))), None)
        body = text[mt.body_open:mt.body_close + 1]
        for m in re.finditer(r"(?<![\w$])populateDefaultEquipmentEnchantments\(", body):
            e = match(body, m.end() - 1)
            args = split_args(body[m.end():e])
            if len(args) != 2:
                continue
            if not lvl:
                res.flags["sigs"].append(f"{path}:{line_of(text, mt.body_open + m.start())} populateDefaultEquipmentEnchantments: "
                                         "no ServerLevelAccessor in scope; pass one as the new first argument")
                continue
            out.append((mt.body_open + m.end(), mt.body_open + e, f"{lvl}, " + ", ".join(a.strip() for a in args)))
            res.count["sigs"] += 1
    text = edit_spans(text, out)
    return text


# --------------------------------------------------------------------------- T10 code shapes a rename row cannot see

def _supplier_fields(src_texts):
    """Simple field names declared `Supplier<...>` anywhere in the tree (registry entries a recipe row turned
    from RegistryObject into Supplier): owner.FIELD passed where an ItemLike is wanted needs `.get()`."""
    out = set()
    for t in src_texts:
        out |= set(re.findall(r"\bSupplier<[^;=]*?>\s+(\w+)\s*=", t))
    return out


def t_misc(path, text, ctx, res):
    edits = []
    # Raider/AbstractIllager inner goals: `new X(this, this...)` is `this.new X(this...)`; the protected nested
    # class cannot be imported from another package, but a subclass names it by its simple name (catalogue #140).
    for g in ("RaiderOpenDoorGoal", "HoldGroundAttackGoal"):
        t2 = re.sub(r"(?<![\w$.])new\s+(?:(?:AbstractIllager|Raider)\.)?%s\(\s*this\s*,\s*" % g, f"this.new {g}(", text)
        if t2 != text:
            t2 = re.sub(r"(?m)^import\s+net\.minecraft\.world\.entity\.(?:monster\.AbstractIllager|raid\.Raider)\.%s\s*;[ \t]*\n" % g, "", t2)
            res.count["misc"] += 1
            text = t2
    # A mixin casting `this` to its target must go through Object (catalogue §H #41 / #162)
    if re.search(r"@Mixin\b", text):
        t2, n = re.subn(r"\(\s*\(\s*([A-Z][\w.]*(?:<[^<>()]*>)?)\s*\)\s*this\s*\)", r"((\1)(Object)this)", text)
        t2, n2 = re.subn(r"(?<![\w$)])\(\s*([A-Z][\w.]*)\s*\)\s*this\b(?!\s*\))", r"(\1)(Object)this", t2)
        t2 = t2.replace("(Object)(Object)this", "(Object)this")
        if t2 != text:
            res.count["misc"] += n + n2
            text = t2
    # BuildCreativeModeTabContentsEvent.accept takes an ItemLike: a Supplier field needs .get()
    sup = ctx.get("supplier_fields") or set()
    if sup and "accept(" in text:
        def fix(m):
            fld = m.group(2).rsplit(".", 1)[-1]
            if fld in sup:
                res.count["misc"] += 1
                return f"{m.group(1)}({m.group(2)}.get())"
            return m.group(0)
        text = re.sub(r"(\b\w+\.accept)\(\s*([\w.]+)\s*\)", fix, text)
    # AttributeInstance.hasModifier takes the modifier's id now: a field declared AttributeModifier needs .id()
    mods = set(re.findall(r"\bAttributeModifier\s+(\w+)\s*=", text))
    if mods:
        t2 = re.sub(r"\.hasModifier\(\s*(%s)\s*\)" % "|".join(map(re.escape, mods)), r".hasModifier(\1.id())", text)
        if t2 != text:
            res.count["misc"] += 1
            text = t2
    # A MobType getter override is gone: the entity type goes in the matching entity-type tag (catalogue #30)
    for mt in reversed(methods(text)):
        if mt.name == "getMobType" and not mt.params:
            body = text[mt.body_open:mt.body_close + 1]
            tag = re.search(r"return\s+MobType\.(UNDEAD|ARTHROPOD|ILLAGER|WATER|UNDEFINED)\s*;", body)
            if not tag or body.count(";") != 1:
                res.flags["misc"].append(f"{path}:{line_of(text, mt.start)} getMobType: not a constant return; port by hand")
                continue
            s = decl_span_start(text, mt.start)
            text = text[:s] + text[mt.body_close + 1:]
            if tag.group(1) != "UNDEFINED":   # the data half: the entity joins the vanilla tag (not guessed here)
                res.flags["misc"].append(f"{path}: getMobType() returned {tag.group(1)}; add this entity to "
                                         f"#minecraft:{TAG_OF_MOBTYPE[tag.group(1)].lower()} (data/minecraft/tags/entity_type/)")
            res.count["misc"] += 1
    # A Structure's codec is a MapCodec (catalogue #102): the 1.20 `mapCodec(...).codec()` / `create(...)`
    # stored as a Codec becomes the MapCodec itself, so StructureType.codec() can return it
    cls = re.search(r"\bclass\s+(\w+)[^{]*\bextends\s+(?:net\.minecraft\.world\.level\.levelgen\.structure\.)?Structure\b", text)
    if cls:
        me = cls.group(1)
        for m in reversed(list(re.finditer(r"\bCodec<%s>(\s+\w+\s*=\s*)RecordCodecBuilder\.(mapCodec|create)\(" % me, text))):
            e = match(text, m.end() - 1)
            if e < 0:
                continue
            tail = re.match(r"\s*\.codec\(\)", text[e + 1:])
            if m.group(2) == "mapCodec" and not tail:
                continue
            new_tail = "" if tail else ""
            end = e + 1 + (tail.end() if tail else 0)
            text = (text[:m.start()] + f"com.mojang.serialization.MapCodec<{me}>{m.group(1)}RecordCodecBuilder.mapCodec("
                    + text[m.end():e + 1] + new_tail + text[end:])
            res.count["misc"] += 1
    text = remove_import_if_unused(text, "net.minecraft.world.entity.MobType")
    return text


TAG_OF_MOBTYPE = {"UNDEAD": "UNDEAD", "ARTHROPOD": "ARTHROPOD", "ILLAGER": "ILLAGER", "WATER": "AQUATIC"}

TRANSFORMS = [("modctor", t_modctor), ("tick", t_tick), ("dist", t_dist), ("attrmod", t_attrmod), ("nbt", t_nbt), ("hooks", t_hooks),
              ("geo", t_geo), ("vc", t_vc), ("source", t_source),
              ("sigs", t_sigs), ("misc", t_misc)]


# --------------------------------------------------------------------------- driver

def root_package(src):
    pkgs = []
    for f in src.rglob("*.java"):
        m = re.search(r"(?m)^package\s+([\w.]+)\s*;", f.read_text(encoding="utf-8", errors="replace"))
        if m:
            pkgs.append(m.group(1).split("."))
    if not pkgs:
        return ""
    pre = pkgs[0]
    for p in pkgs[1:]:
        k = 0
        while k < min(len(pre), len(p)) and pre[k] == p[k]:
            k += 1
        pre = pre[:k]
    return ".".join(pre)


INLINE_NBT = [   # helper call -> the native 1.21 expression (only shapes whose meaning is unchanged inline)
    (r"ItemNbt\.hasTag\(([^()]+)\)", r"\1.has(DataComponents.CUSTOM_DATA)"),
    (r"ItemNbt\.getTag\(([^()]+)\)(?=\.)", r"\1.get(DataComponents.CUSTOM_DATA).copyTag()"),
    (r"ItemNbt\.getOrCreateTag\(([^()]+)\)", r"\1.getOrDefault(DataComponents.CUSTOM_DATA, CustomData.EMPTY).copyTag()"),
    (r"ItemNbt\.update\(([^(),]+),", r"CustomData.update(DataComponents.CUSTOM_DATA, \1,"),
]


def inline_small_nbt(src, nbt_fqn, res):
    """A helper class serving fewer than three call sites is more design than the port needs (review principle
    P3): rewrite those sites to the native expressions and generate no helper. True when inlined."""
    users = {f: f.read_text(encoding="utf-8") for f in pathlib.Path(src).rglob("*.java")}
    users = {f: t for f, t in users.items() if "ItemNbt." in t}
    sites = sum(t.count("ItemNbt.") for t in users.values())
    if sites >= 3 or any(re.search(r"ItemNbt\.(setTag|getTagElement)\(|ItemNbt\.getTag\([^()]+\)(?!\.)", t)
                         for t in users.values()):
        return False
    for f, t in users.items():
        for pat, rep in INLINE_NBT:
            t = re.sub(pat, rep, t)
        t = remove_import_if_unused(t, nbt_fqn)
        t = add_import(t, "net.minecraft.core.component.DataComponents")
        if "CustomData." in t:
            t = add_import(t, "net.minecraft.world.item.component.CustomData")
        f.write_text(t, encoding="utf-8")
    res.count["nbt"] += 0
    return True


def run(src, modid, only=None, dry=False):
    src = pathlib.Path(src)
    res = Result()
    root = root_package(src)
    nbt_fqn = f"{root}.ItemNbt" if root else "ItemNbt"
    need_helper = False
    changed = 0
    supplier_fields = _supplier_fields(f.read_text(encoding="utf-8", errors="replace") for f in src.rglob("*.java"))
    for f in sorted(src.rglob("*.java")):
        text = f.read_text(encoding="utf-8")
        orig = text
        ctx = {"modid": modid, "imports": set(), "nbt_fqn": nbt_fqn, "supplier_fields": supplier_fields}
        rel = f.relative_to(src).as_posix()
        for name, fn in TRANSFORMS:
            if only and name not in only:
                continue
            text = fn(rel, text, ctx, res)
        if text != orig:
            for fq in sorted(ctx["imports"]):
                text = add_import(text, fq)
            changed += 1
            need_helper |= bool(ctx.get("need_nbt_helper"))
            if not dry:
                f.write_text(text, encoding="utf-8")
    if need_helper and not dry:
        need_helper = not inline_small_nbt(src, nbt_fqn, res)
    if need_helper:
        hp = src / (nbt_fqn.replace(".", "/") + ".java")
        if not hp.exists() and not dry:
            hp.parent.mkdir(parents=True, exist_ok=True)
            hp.write_text(NBT_HELPER.format(pkg=nbt_fqn.rsplit(".", 1)[0]), encoding="utf-8")
    return res, changed


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src"); ap.add_argument("--modid"); ap.add_argument("--only")
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--json", action="store_true")
    ap.add_argument("--sites", type=int, default=6); ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not (a.src and a.modid):
        ap.error("--src and --modid are required")
    only = set(a.only.split(",")) if a.only else None
    res, changed = run(a.src, a.modid, only, a.dry_run)
    if a.json:
        print(json.dumps({"files": changed, "rewrites": dict(res.count), "flags": {k: v for k, v in res.flags.items()}}))
        return 0
    print(f"forge-shapes: {changed} file(s) changed" + (" (dry run)" if a.dry_run else ""))
    for name, _fn in TRANSFORMS:
        if only and name not in only:
            continue
        fl = res.flags.get(name, [])
        print(f"  {name:8s} {res.count[name]:4d} rewrite(s)" + (f", {len(fl)} left by name:" if fl else "")
              + ("   (matched nothing)" if not res.count[name] and not fl else ""))
        for s in fl[:a.sites]:
            print("      " + s)
        if len(fl) > a.sites:
            print(f"      ... and {len(fl) - a.sites} more")
    if res.flags.get("internal"):
        print("  INTERNAL: " + "; ".join(res.flags["internal"][:5]))
        return 1
    return 0


# --------------------------------------------------------------------------- self-check

CASES = [
    ("sigs", """package a;
class A extends AbstractIllager {
    public void applyRaidBuffs(int wave, boolean unused) { super.applyRaidBuffs(wave, unused); }
    protected void dropCustomDeathLoot(DamageSource source, int looting, boolean hit) { super.dropCustomDeathLoot(source, looting, hit); }
    public SpawnGroupData finalizeSpawn(ServerLevelAccessor lvl, DifficultyInstance d, MobSpawnType r, SpawnGroupData g, CompoundTag t) {
        this.populateDefaultEquipmentEnchantments(this.random, d);
        return super.finalizeSpawn(lvl, d, r, g, t);
    }
    void s(Mob m, ServerLevel l) { m.finalizeSpawn(l, d, MobSpawnType.EVENT, null, null); }
}
""", ["applyRaidBuffs(net.minecraft.server.level.ServerLevel serverLevel, int wave, boolean unused)",
      "super.applyRaidBuffs(serverLevel, wave, unused)",
      "dropCustomDeathLoot(net.minecraft.server.level.ServerLevel level, DamageSource source, boolean hit)",
      "super.dropCustomDeathLoot(level, source, hit)", "finalizeSpawn(ServerLevelAccessor lvl, DifficultyInstance d, MobSpawnType r, SpawnGroupData g)",
      "super.finalizeSpawn(lvl, d, r, g)", "populateDefaultEquipmentEnchantments(lvl, this.random, d)",
      "m.finalizeSpawn(l, d, MobSpawnType.EVENT, null)"], ["CompoundTag t"]),
    ("misc", """package a;
import net.minecraft.world.entity.monster.AbstractIllager.RaiderOpenDoorGoal;
import net.minecraft.world.entity.MobType;
@Mixin(DamageSources.class)
class A extends AbstractIllager {
    static final AttributeModifier SPEED = null;
    void g() { this.goalSelector.addGoal(2, new RaiderOpenDoorGoal(this, this));
        cir.setReturnValue(((DamageSources)this).source(x));
        if (!inst.hasModifier(SPEED)) {} }
    public MobType getMobType() { return MobType.ILLAGER; }
}
""", ["this.new RaiderOpenDoorGoal(this)", "((DamageSources)(Object)this).source(x)", "hasModifier(SPEED.id())"],
     ["import net.minecraft.world.entity.monster.AbstractIllager.RaiderOpenDoorGoal;", "getMobType", "import net.minecraft.world.entity.MobType;"]),
    ("tick", """package a;
import net.neoforged.neoforge.event.TickEvent;
class A {
    @SubscribeEvent
    public static void onTick(TickEvent.ServerTickEvent event) {
        if (event.phase != TickEvent.Phase.END || ++ticks < 20) {
            return;
        }
        go();
    }
    public static void p(TickEvent.PlayerTickEvent event) {
        if (event.phase == TickEvent.Phase.END) {
            event.player.tick();
        }
    }
    public static void q(TickEvent.ClientTickEvent event) {
        if (event.phase != TickEvent.Phase.START) return;
        x();
    }
}
""", ["ServerTickEvent.Post event", "if (++ticks < 20) {", "PlayerTickEvent.Post event", "        event.getEntity().tick();\n    }",
      "ClientTickEvent.Pre event", "import net.neoforged.neoforge.event.tick.ServerTickEvent;"], ["TickEvent.Phase", "import net.neoforged.neoforge.event.TickEvent;"]),
    ("modctor", """package a;
import net.neoforged.fml.javafmlmod.FMLJavaModLoadingContext;
@Mod(MyMod.MOD_ID)
public class MyMod {
    public MyMod() {
        Net.register();
        FMLJavaModLoadingContext.get().getModEventBus().addListener(this::setup);
        ModLoadingContext.get().registerConfig(Type.COMMON, SPEC);
    }
}
""", ["public MyMod(IEventBus modEventBus, ModContainer modContainer) {", "modEventBus.addListener(this::setup);",
      "modContainer.registerConfig(Type.COMMON, SPEC);", "import net.neoforged.bus.api.IEventBus;"], ["FMLJavaModLoadingContext"]),
    ("tick", """package a;
class S {
    @SubscribeEvent
    public void onLevelTick(TickEvent.LevelTickEvent event) {
        if (!(event.level instanceof ServerLevel sl)) {
            return;
        }
        if (event.phase == TickEvent.Phase.START) {
            check(sl);
            return;
        }
        if (event.phase == TickEvent.Phase.END) {
            tick(sl);
        }
    }
}
""", ["public void onLevelTickPre(LevelTickEvent.Pre event) {", "public void onLevelTick(LevelTickEvent.Post event) {",
      "event.getLevel() instanceof ServerLevel sl",
      "        tick(sl);\n    }", "        check(sl);\n    }"], ["Phase", "true", "false", "return;\n    }\n\n    @Sub"]),
    ("tick", """package a;
class B {
    public static void l(LivingEvent.LivingTickEvent event) {
        if (event.getEntity().level().isClientSide()) return;
        M.handle(event.getEntity());
    }
}
""", ["EntityTickEvent.Pre event", "instanceof net.minecraft.world.entity.LivingEntity living)) return;",
      "if (living.level().isClientSide()) return;", "M.handle(living);"], []),
    ("dist", """package a;
class C {
    void h() {
        ctx.enqueueWork(() -> DistExecutor.unsafeRunWhenOn(Dist.CLIENT,
                () -> () -> ClientRef.apply(msg, f(1, 2))));
        DistExecutor.unsafeRunWhenOn(Dist.CLIENT, () -> ClientRef::open);
    }
}
""", ["ctx.enqueueWork(() -> { if (FMLEnvironment.dist == Dist.CLIENT) ClientRef.apply(msg, f(1, 2)); });",
      "if (FMLEnvironment.dist == Dist.CLIENT) ClientRef.open();", "import net.neoforged.fml.loading.FMLEnvironment;"],
     ["DistExecutor"]),
    ("attrmod", """package a;
class D {
    static final UUID SPEED_UUID = UUID.fromString("x");
    void a(AttributeInstance i) {
        i.addPermanentModifier(new AttributeModifier(SPEED_UUID, "Dimension Explorer Speed", 0.5,
                AttributeModifier.Operation.ADD_VALUE));
        i.removeModifier(SPEED_UUID);
    }
    public Multimap<Attribute, AttributeModifier> m(SlotContext c, ResourceLocation uuid, ItemStack s) {
        m.put(Attributes.ARMOR, new AttributeModifier(uuid, "ring", 6.0, Operation.ADD_VALUE));
    }
}
""", ['new AttributeModifier(ResourceLocation.fromNamespaceAndPath("mymod", "dimension_explorer_speed"), 0.5,',
      'i.removeModifier(ResourceLocation.fromNamespaceAndPath("mymod", "dimension_explorer_speed"));',
      "new AttributeModifier(uuid, 6.0, Operation.ADD_VALUE)", "Multimap<Holder<Attribute>, AttributeModifier>"], []),
    ("nbt", """package a;
class E {
    int f(ItemStack stack, Handle h) {
        if (stack.hasTag() && stack.getTag().contains("k")) {
            stack.getOrCreateTag().putInt(K, g(1));
            stack.getTag().remove(K);
        }
        return h.getTag() == Opcodes.H_INVOKESTATIC ? 1 : player.getMainHandItem().getTag().getInt("x");
    }
}
""", ["ItemNbt.hasTag(stack) && ItemNbt.getTag(stack).contains", "ItemNbt.update(stack, t -> t.putInt(K, g(1)));",
      "ItemNbt.update(stack, t -> t.remove(K));", "h.getTag() == Opcodes", "ItemNbt.getTag(player.getMainHandItem()).getInt"], []),
    ("nbt", """package a;
class E2 {
    String f(ItemStack stack) {
        return stack.hasTag() ? "n=" + stack.getTag().getAllKeys().size() : null;
    }
}
""", ["stack.has(DataComponents.CUSTOM_DATA) ? \"n=\" + stack.get(DataComponents.CUSTOM_DATA).copyTag().getAllKeys().size()",
      "import net.minecraft.core.component.DataComponents;"], ["ItemNbt"]),
    ("hooks", """package a;
class F extends BlockEntity {
    @Override
    public boolean mouseScrolled(double mouseX, double mouseY, double delta) {
        return super.mouseScrolled(mouseX, mouseY, delta);
    }
    @Override
    public SpawnGroupData finalizeSpawn(ServerLevelAccessor w, DifficultyInstance d, MobSpawnType r, @Nullable SpawnGroupData g, @Nullable CompoundTag tag) {
        return super.finalizeSpawn(w, d, r, g, tag);
    }
    @Override
    protected float getStandingEyeHeight(Pose pose, EntityDimensions dimensions) {
        return dimensions.height * 0.85f;
    }
    @Override
    public MobType getMobType() { return MobType.UNDEFINED; }
    @Override
    public @NotNull Packet<ClientGamePacketListener> getAddEntityPacket() {
        return NetworkHooks.getEntitySpawningPacket(this);
    }
    @Override
    protected void saveAdditional(CompoundTag tag) {
        super.saveAdditional(tag);
        ContainerHelper.saveAllItems(tag, this.items);
    }
    @Override
    public void load(CompoundTag tag) {
        super.load(tag);
    }
    @Override
    public CompoundTag getUpdateTag() { return this.saveWithFullMetadata(); }
    public void render(GuiGraphics g, int mx, int my, float pt) {
        this.renderBackground(g);
    }
}
""", ["double mouseY, double scrollX, double delta", "super.mouseScrolled(mouseX, mouseY, scrollX, delta)",
      "@Nullable SpawnGroupData g) {", "super.finalizeSpawn(w, d, r, g)",
      "protected EntityDimensions getDefaultDimensions(Pose pose) {",
      "return dimensions.withEyeHeight(dimensions.height() * 0.85f);",
      "saveAdditional(CompoundTag tag, HolderLookup.Provider registries)", "ContainerHelper.saveAllItems(tag, this.items, registries)",
      "public void loadAdditional(CompoundTag tag, HolderLookup.Provider registries)", "super.loadAdditional(tag, registries)",
      "getUpdateTag(HolderLookup.Provider registries) { return this.saveWithFullMetadata(registries); }",
      "this.renderBackground(g, mx, my, pt);"], ["getMobType", "MobType.UNDEFINED", "NetworkHooks", "@Override\n    @Override", "@Override\n    @Override\n"]),
    ("geo", """package a;
// a geckolib renderer
class G {
    @Override
    public void preRender(PoseStack p, T e, BakedGeoModel m, MultiBufferSource b, VertexConsumer v, boolean r, float pt, int pl, int po, float red, float green, float blue, float alpha) {
        super.preRender(p, e, m, b, v, r, pt, pl, po, red, green, blue, alpha);
    }
    @Override
    public int getPackedOverlay(T e, float u) { return 0; }
}
""", ["int pl, int po, int colour)", "super.preRender(p, e, m, b, v, r, pt, pl, po, colour);",
      "getPackedOverlay(T e, float u, float partialTick)"], []),
    ("vc", """package a;
class H implements VertexConsumer {
    @Override
    public VertexConsumer vertex(double x, double y, double z) {
        direct().vertex(x, y, z);
        return this;
    }
    @Override
    public void endVertex() {
        direct();
    }
}
""", ["public VertexConsumer addVertex(float x, float y, float z) {", "direct().addVertex(x, y, z);"], ["endVertex"]),
    ("source", """package a;
import net.neoforged.neoforge.registries.ForgeRegistries;
@EventBusSubscriber(modid = "x")
class I {}
""", ["import net.neoforged.fml.common.EventBusSubscriber;"], ["ForgeRegistries"]),
]


def self_check():
    import tempfile
    bad = []
    for name, src, want, gone in CASES:
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "my" / "X.java"
            p.parent.mkdir(parents=True)
            p.write_text(src.replace("package a;", "package my;"), encoding="utf-8")
            res, _ = run(d, "mymod", {name})
            out = p.read_text(encoding="utf-8")
            res2, _ = run(d, "mymod", {name})          # idempotent
            again = p.read_text(encoding="utf-8")
            miss = [w for w in want if w not in out] + [f"still has {g!r}" for g in gone if g in out]
            if again != out:
                miss.append("not idempotent")
            if miss:
                bad.append((name, miss, out))
    for name, miss, out in bad:
        print(f"FAIL {name}: {miss}\n{out}")
    print("self-check:", "OK" if not bad else f"FAIL ({len(bad)})")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
