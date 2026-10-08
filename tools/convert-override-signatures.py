#!/usr/bin/env python3
"""Move overrides of vanilla hooks whose SIGNATURE changed in 26.2, keeping every body byte-identical.

    python3 tools/convert-override-signatures.py --src src/main/java [--dry-run]
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

THE TABLE (HOOKS below; `--list` prints it).  Each row was read off the 26.2 jar and the 1.21.1 sources:
  Item.appendHoverText(ItemStack, TooltipContext, List<Component>, TooltipFlag)            void
     -> (ItemStack, TooltipContext, TooltipDisplay, Consumer<Component>, TooltipFlag)       void
     the copy fills a list; the override forwards each line to the consumer, in order.
  Item.releaseUsing(ItemStack, Level, LivingEntity, int)        void -> boolean   (false = vanilla's default)
  Item.hurtEnemy(ItemStack, LivingEntity, LivingEntity)         boolean -> void   (the value only fed a stat)
  Item.inventoryTick(ItemStack, Level, Entity, int, boolean)    -> (ItemStack, ServerLevel, Entity, EquipmentSlot)
     isSelected becomes `slot == MAINHAND`; the inventory slot INDEX has no 26.2 value (-1 is passed) and 26.2
     calls this on the SERVER only -- a body that reads the index or branches on isClientSide is NOTED.

Matched by method name AND the exact old parameter types (annotations, `final` and package prefixes ignored), in
a class that extends or implements something; an override already in the 26.2 shape does not match.  REFUSED and
named: a `super.hook(...)` used as a value where the new super returns void, and a body that is abstract.
Idempotent.  Standard library only.
"""
import argparse, pathlib, re, sys, tempfile

COMPONENT = "net.minecraft.network.chat.Component"
HOOKS = [
    {"name": "appendHoverText", "old_ret": "void",
     "old": ["ItemStack", "TooltipContext", "List<Component>", "TooltipFlag"],
     "new_ret": "void",
     "new": ["{t0} {n0}", "{t1} {n1}", "net.minecraft.world.item.component.TooltipDisplay portedDisplay",
             f"java.util.function.Consumer<{COMPONENT}> portedTooltip", "{t3} {n3}"],
     "body": ["java.util.List<" + COMPONENT + "> portedLines = new java.util.ArrayList<>();",
              "this.ported$appendHoverText({n0}, {n1}, portedLines, {n3}, portedDisplay);",
              "portedLines.forEach(portedTooltip);"],
     "extras": ["net.minecraft.world.item.component.TooltipDisplay portedDisplay"],
     "super": "super.appendHoverText({a0}, {a1}, portedDisplay, ({a2})::add, {a3})", "super_void": True},
    {"name": "releaseUsing", "old_ret": "void",
     "old": ["ItemStack", "Level", "LivingEntity", "int"],
     "new_ret": "boolean", "new": ["{t0} {n0}", "{t1} {n1}", "{t2} {n2}", "{t3} {n3}"],
     "body": ["this.ported$releaseUsing({n0}, {n1}, {n2}, {n3});", "return false;"],
     "extras": [], "super": "super.releaseUsing({a0}, {a1}, {a2}, {a3})", "super_void": False},
    {"name": "hurtEnemy", "old_ret": "boolean",
     "old": ["ItemStack", "LivingEntity", "LivingEntity"],
     "new_ret": "void", "new": ["{t0} {n0}", "{t1} {n1}", "{t2} {n2}"],
     "body": ["this.ported$hurtEnemy({n0}, {n1}, {n2});"],
     "extras": [], "super": "super.hurtEnemy({a0}, {a1}, {a2})", "super_void": True, "super_return": "true"},
    {"name": "inventoryTick", "old_ret": "void",
     "old": ["ItemStack", "Level", "Entity", "int", "boolean"],
     "new_ret": "void",
     "new": ["{t0} {n0}", "net.minecraft.server.level.ServerLevel portedLevel", "{t2} {n2}",
             "net.minecraft.world.entity.EquipmentSlot portedSlot"],
     "body": ["this.ported$inventoryTick({n0}, portedLevel, {n2}, -1, "
              "portedSlot == net.minecraft.world.entity.EquipmentSlot.MAINHAND, portedLevel, portedSlot);"],
     "extras": ["net.minecraft.server.level.ServerLevel portedLevel", "net.minecraft.world.entity.EquipmentSlot portedSlot"],
     "super": "super.inventoryTick({a0}, portedLevel, {a2}, portedSlot)", "super_void": True,
     "notes": [("{n3}", "reads the inventory slot index, which 26.2 does not pass (-1 now)"),
               ("isClientSide", "branches on isClientSide; 26.2 calls inventoryTick on the server only")]},
]
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
                refused.append(f"super.{hook['name']}(...) used as a value; 26.2's returns void")
                return None
        out.append(body[pos:m.start()])
        out.append(new)
        pos = close + 1
    out.append(body[pos:])
    return "".join(out)


def convert_text(text):
    """(new text, [converted hook names], [notes], [refused])."""
    if not re.search(r"\b(extends|implements)\b", text):
        return text, [], [], []
    done, notes, refused = [], [], []
    names = {h["name"]: h for h in HOOKS}
    pos, out = 0, []
    for m in HEADER.finditer(text):
        hook = names.get(m.group("name"))
        if not hook or m.start() < pos or m.group("ret").replace(" ", "") != hook["old_ret"]:
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
        new_body = rewrite_supers(body, hook, refused)
        if new_body is None:
            continue
        line = text.count("\n", 0, m.start()) + 1
        fill = {}
        for i, (t, n) in enumerate(typed):
            fill[f"t{i}"], fill[f"n{i}"] = t, n
        for needle, msg in hook.get("notes", []):
            needle = needle.format(**fill)
            if re.search(r"\b" + re.escape(needle) + r"\b", body):
                notes.append((line, f"{hook['name']}: {msg}"))
        ind = re.match(r"[ \t]*", text[text.rfind("\n", 0, m.start()) + 1:]).group(0)
        inner = ind + ("\t" if ind.startswith("\t") else "    ")
        new_params = ", ".join(x.format(**fill) for x in hook["new"])
        old_params = ", ".join(f"{t} {n}" for t, n in typed) + "".join(", " + e for e in hook["extras"])
        stmts = "".join(f"\n{inner}{s.format(**fill)}" for s in hook["body"])
        out.append(text[pos:m.start()])
        out.append(f"{m.group('mods')}{hook['new_ret']} {hook['name']}({new_params}) {{{stmts}\n{ind}}}\n\n"
                   f"{ind}private {hook['old_ret']} ported${hook['name']}({old_params}) {new_body}")
        pos = bc + 1
        done.append(hook["name"])
    out.append(text[pos:])
    return "".join(out), done, notes, refused


def run(src, dry=False):
    changed, counts, all_notes, all_refused = 0, {}, [], []
    for f in sorted(pathlib.Path(src).rglob("*.java")):
        t = f.read_text(encoding="utf-8")
        new, done, notes, refused = convert_text(t)
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
          + "".join(f"; {k} {v}" for k, v in sorted(counts.items())))
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
    chk("no extends: untouched", convert_text("class A { public void releaseUsing(ItemStack s, Level l, "
                                              "LivingEntity u, int t) {} }")[1] == [])
    bad = convert_text("class B extends Item { public boolean hurtEnemy(ItemStack s, LivingEntity t, LivingEntity a) "
                       "{ boolean r = super.hurtEnemy(s, t, a); return r; } }")
    chk("super used as a value: refused", bad[1] == [] and bad[3])
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if a.list:
        for h in HOOKS:
            print(f"{h['name']}({', '.join(h['old'])}) {h['old_ret']}  ->  {h['new_ret']}")
        return 0
    if not a.src:
        ap.error("--src is required")
    return run(a.src, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
