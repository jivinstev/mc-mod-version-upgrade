#!/usr/bin/env python3
"""Move overrides of vanilla hooks whose SIGNATURE changed after 1.21.1, keeping every body byte-identical.

    python3 tools/convert-override-signatures.py --src src/main/java [--crossing FROM..TO] [--dry-run]
    python3 tools/convert-override-signatures.py --list
    python3 tools/convert-override-signatures.py --self-check

THE SHAPE (CATALOG §V76, generalised).  An override written against a 1.21.1 hook stops overriding anything on
26.2 when the hook's return type or parameters change, and the error is a wall of `method does not override`.
Rewriting each body is per-site judgement; adapting at the BOUNDARY is not.  So each matched override becomes two
methods:

    @Override                                              <- the override 26.2 calls (annotations kept here)
    public <new return> hook(<new params>) {
        <pre>; [return] this.ported$hook(<the 1.21.1 arguments, built from the new ones>, <extras>); <post>
    }

    private <old return> ported$hook(<old params>, <extras>) { <the original body, unchanged> }

Inside the private copy, `super.hook(<old args>)` is rewritten to the 26.2 super call; nothing else is touched.
`$` cannot collide with a name the mod or vanilla declares.

THE TABLE (HOOKS below; `--list` prints it).  Each row was read off the 1.21.1 sources and the jars, and carries the
Minecraft version its new signature LANDS in (javap of the 1.21.1, 1.21.3, 1.21.4, 1.21.5 and 26.2 merged jars:
absent in the one below, present from it on; 1.21.2 is not staged, its rows were first seen in 1.21.3's jar):
  1.21.5  Item.appendHoverText(ItemStack, TooltipContext, List<Component>, TooltipFlag)    void
             -> (ItemStack, TooltipContext, TooltipDisplay, Consumer<Component>, TooltipFlag)  void
             the copy fills a list; the override forwards each line to the consumer, in order.
  1.21.2  Item.releaseUsing(ItemStack, Level, LivingEntity, int)        void -> boolean   (false = vanilla's default)
  1.21.5  Item.hurtEnemy(ItemStack, LivingEntity, LivingEntity)         boolean -> void
             On 1.21.1 the value gated ItemStack.postHurtEnemy in Player.attack -- where SwordItem / DiggerItem take
             their durability damage -- and the ITEM_USED stat.  From 1.21.5 ItemStack.hurtEnemy answers "has a WEAPON
             component" and ItemStack.postHurtEnemy damages the stack by that component after Item.postHurtEnemy, so
             no override can veto it (javap of 21.1.238, 21.5.98 and 26.2.0.90).  A body that can return anything but
             `true` / `super.hurtEnemy(..)` (a `return false;` that spared the durability, a computed veto) is
             REFUSED and named; `return super.hurtEnemy(..)` becomes `super.hurtEnemy(..); return true`, NOTED when
             the class also overrides postHurtEnemy (1.21.1 skipped it when Item's default returned false).
  1.21.5  Item.inventoryTick(ItemStack, Level, Entity, int, boolean)    -> (ItemStack, ServerLevel, Entity, EquipmentSlot)
             isSelected becomes `slot == MAINHAND`; the inventory slot INDEX has no value from 1.21.5 (-1 is passed)
             and the hook runs on the SERVER only -- a body that reads the index or branches on isClientSide is NOTED.
  1.21.2  Mob.customServerAiStep()                                      -> (ServerLevel)   (it ran server-side already;
             six shipped ports made this edit by hand)

WHICH ROWS RUN.  Without --crossing every row runs: the era route (tools/mechanical-hop.py --stage era) moves a
1.21.1 tree to 26.x in one step.  `--crossing FROM..TO` runs only the rows whose version lands in the hop
(FROM < version <= TO): a version-branch hop (tools/fork-port.py on neoforge-<TO>, FROM its parent's version), so a
1.21.4 -> 1.21.5 hop moves the three 1.21.5 hooks and leaves the rest, which its branch already has in their new
shape or which do not exist yet.

Matched by method name AND the exact old parameter types (annotations, `final` and package prefixes ignored), in
a class that extends or implements something; an override already in the new shape does not match, and an old-shape
override in a file that already declares the hook in its new shape (a mergeup's twin) is left and NOTED.  REFUSED and
named: a `super.hook(...)` used as a value where the new super returns void, and a body that is abstract.
Idempotent (with or without --crossing).  Standard library only.
"""
import argparse, pathlib, re, sys, tempfile

COMPONENT = "net.minecraft.network.chat.Component"
HOOKS = [
    {"name": "appendHoverText", "since": "1.21.5", "old_ret": "void",
     "old": ["ItemStack", "TooltipContext", "List<Component>", "TooltipFlag"],
     "new_ret": "void",
     "new": ["{t0} {n0}", "{t1} {n1}", "net.minecraft.world.item.component.TooltipDisplay portedDisplay",
             f"java.util.function.Consumer<{COMPONENT}> portedTooltip", "{t3} {n3}"],
     "body": ["java.util.List<" + COMPONENT + "> portedLines = new java.util.ArrayList<>();",
              "this.ported$appendHoverText({n0}, {n1}, portedLines, {n3}, portedDisplay);",
              "portedLines.forEach(portedTooltip);"],
     "extras": ["net.minecraft.world.item.component.TooltipDisplay portedDisplay"],
     "super": "super.appendHoverText({a0}, {a1}, portedDisplay, ({a2})::add, {a3})", "super_void": True},
    {"name": "releaseUsing", "since": "1.21.2", "old_ret": "void",
     "old": ["ItemStack", "Level", "LivingEntity", "int"],
     "new_ret": "boolean", "new": ["{t0} {n0}", "{t1} {n1}", "{t2} {n2}", "{t3} {n3}"],
     "body": ["this.ported$releaseUsing({n0}, {n1}, {n2}, {n3});", "return false;"],
     "extras": [], "super": "super.releaseUsing({a0}, {a1}, {a2}, {a3})", "super_void": False},
    {"name": "hurtEnemy", "since": "1.21.5", "old_ret": "boolean",
     "old": ["ItemStack", "LivingEntity", "LivingEntity"],
     "new_ret": "void", "new": ["{t0} {n0}", "{t1} {n1}", "{t2} {n2}"],
     "body": ["this.ported$hurtEnemy({n0}, {n1}, {n2});"],
     "extras": [], "super": "super.hurtEnemy({a0}, {a1}, {a2})", "super_void": True, "super_return": "true",
     # a return other than these was a veto of postHurtEnemy (the durability damage) that 1.21.5 cannot express
     "refuse_return": (r"\breturn\s+(?!true\s*;)(?!super\s*\.\s*hurtEnemy\s*\()[^;]*;",
                       "hurtEnemy: the body can return a value other than true -- on 1.21.1 that skipped postHurtEnemy "
                       "(the durability damage); from 1.21.5 the WEAPON component damages the stack whatever the hook "
                       "returns, so no boundary adapter keeps it: port by hand (CATALOG V96)"),
     "file_notes": [(r"\bvoid\s+postHurtEnemy\s*\(", r"\breturn\s+super\s*\.\s*hurtEnemy\s*\(",
                     "hurtEnemy: returns super's value and the class overrides postHurtEnemy -- 1.21.1 skipped that "
                     "override when Item's default returned false; from 1.21.5 it always runs")]},
    {"name": "inventoryTick", "since": "1.21.5", "old_ret": "void",
     "old": ["ItemStack", "Level", "Entity", "int", "boolean"],
     "new_ret": "void",
     "new": ["{t0} {n0}", "net.minecraft.server.level.ServerLevel portedLevel", "{t2} {n2}",
             "net.minecraft.world.entity.EquipmentSlot portedSlot"],
     "body": ["this.ported$inventoryTick({n0}, portedLevel, {n2}, -1, "
              "portedSlot == net.minecraft.world.entity.EquipmentSlot.MAINHAND, portedLevel, portedSlot);"],
     "extras": ["net.minecraft.server.level.ServerLevel portedLevel", "net.minecraft.world.entity.EquipmentSlot portedSlot"],
     "super": "super.inventoryTick({a0}, portedLevel, {a2}, portedSlot)", "super_void": True,
     "notes": [("{n3}", "reads the inventory slot index, which 1.21.5+ does not pass (-1 now)"),
               ("isClientSide", "branches on isClientSide; 1.21.5+ calls inventoryTick on the server only")]},
    {"name": "customServerAiStep", "since": "1.21.2", "old_ret": "void", "old": [],
     "new_ret": "void", "new": ["net.minecraft.server.level.ServerLevel portedLevel"],
     "body": ["this.ported$customServerAiStep(portedLevel);"],
     "extras": ["net.minecraft.server.level.ServerLevel portedLevel"],
     "super": "super.customServerAiStep(portedLevel)", "super_void": True},
]
def mc_key(v):
    return tuple(int(x) for x in re.findall(r"\d+", v))


def hooks_crossing(frm, to):
    """The rows whose new signature lands in the hop frm -> to (frm < since <= to)."""
    return [h for h in HOOKS if mc_key(frm) < mc_key(h["since"]) <= mc_key(to)]


def parse_crossing(spec):
    """'FROM..TO' -> (FROM, TO); raises ValueError on anything else."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)+)\s*\.\.\s*(\d+(?:\.\d+)+)\s*", spec or "")
    if not m or mc_key(m.group(1)) >= mc_key(m.group(2)):
        raise ValueError(f"--crossing wants FROM..TO with FROM below TO (e.g. 1.21.4..1.21.5), not {spec!r}")
    return m.group(1), m.group(2)


HEADER = re.compile(r"(?P<mods>(?:public|protected)\s+(?:final\s+)?)(?P<ret>[\w.<>\[\]]+)\s+(?P<name>\w+)\s*\(")


def norm_type(t):
    t = re.sub(r"@[\w.]+(?:\([^)]*\))?\s*", "", t)
    t = re.sub(r"\bfinal\s+", "", t).strip()
    t = re.sub(r"\b(?:[a-z_][\w]*\.)+(?=[A-Z])", "", t)          # package prefixes
    t = re.sub(r"\bItem\.TooltipContext\b", "TooltipContext", t)
    return re.sub(r"\s+", "", t)


def split_top(s):
    out, depth, cur = [], 0, ""
    for c in s:
        depth += c in "<([" and 1 or 0
        depth -= c in ">)]" and 1 or 0
        if c == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += c
    return [x for x in out + [cur] if x.strip()]


def matching(text, i):
    """Index of the bracket closing the one at i (skips strings, chars and comments)."""
    open_c = text[i]
    close_c = {"(": ")", "{": "}"}[open_c]
    depth, j = 0, i
    while j < len(text):
        c = text[j]
        if c in "\"'":
            q, j = c, j + 1
            while j < len(text) and text[j] != q:
                j += 2 if text[j] == "\\" else 1
        elif text.startswith("//", j):
            j = text.find("\n", j)
            if j < 0:
                return None
        elif text.startswith("/*", j):
            j = text.find("*/", j) + 1
        elif c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return None


def rewrite_supers(body, hook, refused):
    out, pos = [], 0
    for m in re.finditer(r"\bsuper\s*\.\s*" + hook["name"] + r"\s*\(", body):
        close = matching(body, m.end() - 1)
        if close is None:
            continue
        args = split_top(body[m.end():close])
        if len(args) != len(hook["old"]):
            continue
        new = hook["super"]
        for i, a in enumerate(args):
            new = new.replace("{a%d}" % i, a.strip())
        prefix = body[pos:m.start()]
        ret = re.search(r"\breturn\s+$", prefix)
        if hook.get("super_void"):
            if ret and hook.get("super_return"):            # `return super.x(..)` -> `super.x(..); return <v>`
                out.append(prefix[:ret.start()])
                out.append(new + "; return " + hook["super_return"])
                pos = close + 1
                continue
            statement = re.search(r"(^|[;{}])\s*$", body[:m.start()]) and body[close + 1:].lstrip().startswith(";")
            if not statement:
                refused.append(f"super.{hook['name']}(...) used as a value; from {hook['since']} it returns void")
                return None
        out.append(body[pos:m.start()])
        out.append(new)
        pos = close + 1
    out.append(body[pos:])
    return "".join(out)


def declares_new(text, hook):
    """True when the file already declares `hook` in its new shape: the ported$ copy, or a same-named method with the
    new return type and the new parameter types (a mergeup can bring an old-shape twin back beside it)."""
    if re.search(r"\bported\$" + re.escape(hook["name"]) + r"\s*\(", text):
        return True
    want = [norm_type(x.rsplit(" ", 1)[0]) for x in hook["new"] if not x.startswith("{")]
    fixed = [i for i, x in enumerate(hook["new"]) if not x.startswith("{")]
    for m in HEADER.finditer(text):
        if m.group("name") != hook["name"] or m.group("ret").replace(" ", "") != hook["new_ret"]:
            continue
        pc = matching(text, m.end() - 1)
        params = split_top(text[m.end():pc]) if pc is not None else None
        if params is None or len(params) != len(hook["new"]):
            continue
        types = [norm_type(re.sub(r"\s+\w+\s*$", "", p)) for p in params]
        if [types[i] for i in fixed] == want and (hook["new_ret"] != hook["old_ret"] or want):
            return True
    return False


def convert_text(text, hooks=None):
    """(new text, [converted hook names], [notes], [refused]); hooks: the rows to apply (default all)."""
    if not re.search(r"\b(extends|implements)\b", text):
        return text, [], [], []
    done, notes, refused = [], [], []
    names = {h["name"]: h for h in (HOOKS if hooks is None else hooks)}
    pos, out = 0, []
    for m in HEADER.finditer(text):
        hook = names.get(m.group("name"))
        if not hook or m.start() < pos or m.group("ret").replace(" ", "") != hook["old_ret"]:
            continue
        if declares_new(text, hook):
            # converting again would declare the new override and ported$ twice: leave the old-shape twin, say so
            notes.append((text.count("\n", 0, m.start()) + 1, f"{hook['name']}: the file already declares it in its "
                          "new shape; this old-shape override was left as it is (merge it into that one by hand)"))
            continue
        po = m.end() - 1
        pc = matching(text, po)
        if pc is None:
            continue
        params = split_top(text[po + 1:pc])
        typed = []
        for p in params:
            pm = re.match(r"\s*(.*\S)\s+(\w+)\s*$", re.sub(r"@[\w.]+(?:\([^)]*\))?\s*", "", p), re.S)
            if not pm:
                typed = None
                break
            typed.append((re.sub(r"\bfinal\s+", "", pm.group(1)).strip(), pm.group(2)))
        if typed is None or [norm_type(t) for t, _ in typed] != hook["old"]:
            continue
        rest = text[pc + 1:]
        bm = re.match(r"\s*(?:throws\s+[\w.,\s]+)?\{", rest)
        if not bm:
            continue
        bo = pc + 1 + bm.end() - 1
        bc = matching(text, bo)
        if bc is None:
            continue
        body = text[bo:bc + 1]
        rr = hook.get("refuse_return")
        if rr and re.search(rr[0], re.sub(r"//[^\n]*|/\*.*?\*/", "", body, flags=re.S)):
            refused.append(rr[1])
            continue
        new_body = rewrite_supers(body, hook, refused)
        if new_body is None:
            continue
        line = text.count("\n", 0, m.start()) + 1
        fill = {}
        for i, (t, n) in enumerate(typed):
            fill[f"t{i}"], fill[f"n{i}"] = t, n
        for in_file, in_body, msg in hook.get("file_notes", []):
            if re.search(in_file, text) and re.search(in_body, body):
                notes.append((line, msg))
        for needle, msg in hook.get("notes", []):
            needle = needle.format(**fill)
            if re.search(r"\b" + re.escape(needle) + r"\b", body):
                notes.append((line, f"{hook['name']}: {msg}"))
        ind = re.match(r"[ \t]*", text[text.rfind("\n", 0, m.start()) + 1:]).group(0)
        inner = ind + ("\t" if ind.startswith("\t") else "    ")
        new_params = ", ".join(x.format(**fill) for x in hook["new"])
        old_params = ", ".join([f"{t} {n}" for t, n in typed] + list(hook["extras"]))
        stmts = "".join(f"\n{inner}{s.format(**fill)}" for s in hook["body"])
        out.append(text[pos:m.start()])
        out.append(f"{m.group('mods')}{hook['new_ret']} {hook['name']}({new_params}) {{{stmts}\n{ind}}}\n\n"
                   f"{ind}private {hook['old_ret']} ported${hook['name']}({old_params}) {new_body}")
        pos = bc + 1
        done.append(hook["name"])
    out.append(text[pos:])
    return "".join(out), done, notes, refused


def run(src, dry=False, hooks=None, crossing=None):
    changed, counts, all_notes, all_refused = 0, {}, [], []
    for f in sorted(pathlib.Path(src).rglob("*.java")):
        t = f.read_text(encoding="utf-8")
        new, done, notes, refused = convert_text(t, hooks)
        rel = f.relative_to(src)
        all_notes += [f"  NOTE {rel}:{ln}: {msg}" for ln, msg in notes]
        all_refused += [f"  REFUSED {rel}: {msg}" for msg in refused]
        for d in done:
            counts[d] = counts.get(d, 0) + 1
        if new != t:
            changed += 1
            if not dry:
                f.write_text(new, encoding="utf-8")
    print(f"convert-override-signatures: {sum(counts.values())} override(s) in {changed} file(s)"
          + "".join(f"; {k} {v}" for k, v in sorted(counts.items()))
          + (f" (rows landing in {crossing[0]}..{crossing[1]}: {', '.join(h['name'] for h in hooks) or 'none'})"
             if crossing else ""))
    for line in all_refused + all_notes:
        print(line)
    return 1 if all_refused else 0


FIXTURE = """package com.example;

public class Wand extends Item {
    @Override
    public void appendHoverText(ItemStack stack, Item.TooltipContext ctx, List<Component> tooltip, TooltipFlag flag) {
        tooltip.add(Component.literal("a"));
        if (flag.isAdvanced()) {
            return;
        }
        super.appendHoverText(stack, ctx, tooltip, flag);
    }

    @Override
    public void releaseUsing(ItemStack s, Level level, LivingEntity user, int left) {
        super.releaseUsing(s, level, user, left);
        user.stopUsingItem();
    }

    @Override
    public boolean hurtEnemy(ItemStack s, LivingEntity target, LivingEntity attacker) {
        target.igniteForSeconds(2);
        return super.hurtEnemy(s, target, attacker);
    }

    @Override
    public void inventoryTick(ItemStack s, Level level, Entity e, int slot, boolean selected) {
        if (selected && !level.isClientSide) e.setGlowingTag(true);
    }

    @Override
    public boolean releaseUsing(ItemStack s, Level level, LivingEntity user, int left, String already26) {
        return true;
    }
}
"""


def self_check():
    ok = True

    def chk(label, cond):
        nonlocal ok
        if not cond:
            print("FAIL:", label)
            ok = False

    new, done, notes, refused = convert_text(FIXTURE)
    chk("four hooks converted", sorted(done) == ["appendHoverText", "hurtEnemy", "inventoryTick", "releaseUsing"])
    chk("tooltip override", "public void appendHoverText(ItemStack stack, Item.TooltipContext ctx, "
        "net.minecraft.world.item.component.TooltipDisplay portedDisplay, java.util.function.Consumer<"
        "net.minecraft.network.chat.Component> portedTooltip, TooltipFlag flag) {" in new)
    chk("tooltip lines forwarded after the copy runs", "this.ported$appendHoverText(stack, ctx, portedLines, flag, "
        "portedDisplay);\n        portedLines.forEach(portedTooltip);" in new)
    chk("copy keeps the early return and body", "private void ported$appendHoverText(ItemStack stack, Item.TooltipContext "
        "ctx, List<Component> tooltip, TooltipFlag flag, net.minecraft.world.item.component.TooltipDisplay portedDisplay) {"
        "\n        tooltip.add(Component.literal(\"a\"));\n        if (flag.isAdvanced()) {\n            return;" in new)
    chk("tooltip super rewritten", "super.appendHoverText(stack, ctx, portedDisplay, (tooltip)::add, flag);" in new)
    chk("releaseUsing returns false", "this.ported$releaseUsing(s, level, user, left);\n        return false;" in new)
    chk("hurtEnemy: return super -> super; return true", "super.hurtEnemy(s, target, attacker); return true;" in new
        and "public void hurtEnemy(" in new)
    chk("inventoryTick: selected from the slot", "portedSlot == net.minecraft.world.entity.EquipmentSlot.MAINHAND" in new)
    chk("inventoryTick: isClientSide noted", any("isClientSide" in m for _, m in notes))
    chk("an already-26.2 overload untouched", "int left, String already26) {\n        return true;" in new)
    chk("@Override stays on the public method", "@Override\n    public void appendHoverText(" in new
        and "@Override\n    private" not in new)
    chk("idempotent", convert_text(new)[1] == [])
    # a mergeup brought an old-shape twin back beside the converted hook: converting it again would declare it twice
    twin = new.replace("\n}\n", "\n    @Override\n    public void releaseUsing(ItemStack s, Level level, LivingEntity u, "
                       "int left) {\n        u.stopUsingItem();\n    }\n}\n")
    tw = convert_text(twin)
    chk("a hook already declared in its new shape: skipped and noted", tw[0] == twin and tw[1] == []
        and any("already declares" in m for _, m in tw[2]))
    hand = ("class H extends Mob {\n    public void customServerAiStep(ServerLevel level) {}\n"
            "    public void customServerAiStep() { tick(); }\n}\n")
    chk("a hand-written new shape (no ported$ copy) counts too", convert_text(hand)[1] == [])
    bridge = ("class W extends Item {\n    @Override\n    public void appendHoverText(ItemStack s, TooltipContext c, "
              "TooltipDisplay d, Consumer<Component> out, TooltipFlag f) {\n        List<Component> l = new ArrayList<>();\n"
              "        appendHoverText(s, c, l, f);\n        l.forEach(out);\n    }\n\n    public void appendHoverText(ItemStack s, "
              "TooltipContext c, List<Component> l, TooltipFlag f) {\n        l.add(Component.literal(\"x\"));\n    }\n}\n")
    chk("a hand-written 1.21.5 Consumer bridge keeps its List helper (no duplicate)",
        convert_text(bridge, hooks_crossing("1.21.4", "1.21.5"))[0] == bridge)
    chk("no extends: untouched", convert_text("class A { public void releaseUsing(ItemStack s, Level l, "
                                              "LivingEntity u, int t) {} }")[1] == [])
    bad = convert_text("class B extends Item { public boolean hurtEnemy(ItemStack s, LivingEntity t, LivingEntity a) "
                       "{ boolean r = super.hurtEnemy(s, t, a); return r; } }")
    chk("super used as a value: refused", bad[1] == [] and bad[3])
    # --crossing: a version-branch hop runs only the rows that land in it
    names = lambda frm, to: sorted(h["name"] for h in hooks_crossing(frm, to))
    chk("1.21.4..1.21.5 crosses the three 1.21.5 rows", names("1.21.4", "1.21.5") == ["appendHoverText", "hurtEnemy", "inventoryTick"])
    chk("1.21.1..1.21.3 crosses the two 1.21.2 rows", names("1.21.1", "1.21.3") == ["customServerAiStep", "releaseUsing"])
    chk("1.21.5..1.21.8 crosses none", names("1.21.5", "1.21.8") == [])
    chk("a direct 1.21.1..26.2 jump crosses all", len(hooks_crossing("1.21.1", "26.2")) == len(HOOKS))
    hop = convert_text(FIXTURE, hooks_crossing("1.21.4", "1.21.5"))
    chk("a 1.21.5 hop converts the 1.21.5 hooks only", sorted(hop[1]) == ["appendHoverText", "hurtEnemy", "inventoryTick"]
        and "public void releaseUsing(ItemStack s, Level level, LivingEntity user, int left) {\n        super.releaseUsing(" in hop[0])
    chk("a hop that crosses nothing changes nothing", convert_text(FIXTURE, hooks_crossing("1.21.5", "1.21.8"))[0] == FIXTURE)
    chk("idempotent per hop", convert_text(hop[0], hooks_crossing("1.21.4", "1.21.5"))[0] == hop[0])
    step = convert_text(convert_text(FIXTURE, hooks_crossing("1.21.1", "1.21.3"))[0], hooks_crossing("1.21.4", "1.21.5"))[0]
    chk("the ladder's hops add up to the era route's one step", step == new)
    # a hurtEnemy veto (a false that spared postHurtEnemy's durability damage on 1.21.1) is refused, not converted
    veto = ("class V extends SwordItem {\n    public boolean hurtEnemy(ItemStack s, LivingEntity t, LivingEntity a) {\n"
            "        if (t.isBlocking()) {\n            return false;\n        }\n        return super.hurtEnemy(s, t, a);\n    }\n}\n")
    vr = convert_text(veto)
    chk("a hurtEnemy that can return false: refused, untouched", vr[0] == veto and vr[1] == [] and any("postHurtEnemy" in r for r in vr[3]))
    chk("a computed hurtEnemy result: refused", convert_text(veto.replace("return false;", "return t.isAlive();"))[3])
    chk("a false only in a comment does not refuse", convert_text(veto.replace("return false;", "// return false;"))[1] == ["hurtEnemy"])
    post = veto.replace("        if (t.isBlocking()) {\n            return false;\n        }\n", "") \
        .replace("}\n}\n", "}\n    public void postHurtEnemy(ItemStack s, LivingEntity t, LivingEntity a) { s.shrink(1); }\n}\n")
    pr = convert_text(post)
    chk("return super + an own postHurtEnemy: converted and noted", pr[1] == ["hurtEnemy"] and any("postHurtEnemy" in m for _, m in pr[2]))
    try:
        parse_crossing("1.21.5..1.21.4")
        chk("a backwards --crossing is refused", False)
    except ValueError:
        pass
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src")
    ap.add_argument("--crossing", metavar="FROM..TO", help="only the rows landing in this hop (FROM < row <= TO): a version-branch hop")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if a.list:
        for h in HOOKS:
            print(f"{h['since']:<8}{h['name']}({', '.join(h['old'])}) {h['old_ret']}  ->  {h['new_ret']}")
        return 0
    if not a.src:
        ap.error("--src is required")
    if a.crossing:
        try:
            crossing = parse_crossing(a.crossing)
        except ValueError as e:
            ap.error(str(e))
        return run(a.src, a.dry_run, hooks_crossing(*crossing), crossing)
    return run(a.src, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
