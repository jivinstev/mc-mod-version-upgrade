#!/usr/bin/env python3
"""Convert 1.21 GUI hook overrides to 26.x: render* -> extract*, and input handlers onto event records.

    python3 tools/convert-gui-hooks.py --src src/main/java [--dry-run]

26.x records GUI drawing instead of issuing it, and renamed every hook to match (CATALOG §V53):
    render(g, mx, my, pt)            -> extractRenderState(g, mx, my, pt)     (Screen, Renderable, widgets)
    renderBackground(g, mx, my, pt)  -> extractBackground(g, mx, my, pt)
    renderWidget(g, mx, my, pt)      -> extractWidgetRenderState(g, mx, my, pt)
    renderLabels(g, mx, my)          -> extractLabels(g, mx, my)
    renderTooltip(g, mx, my)         -> extractTooltip(g, mx, my)
    renderBg(g, pt, mx, my)          -> extractBackground(g, mx, my, pt) after super.extractBackground(...)
                                        (renderBg is GONE, its place is extractBackground -- and the
                                        argument ORDER differs, so this is not a rename)
and the input handlers take event records:
    mouseClicked(double x, double y, int b)            -> mouseClicked(MouseButtonEvent event, boolean doubleClick)
    mouseReleased(double x, double y, int b)           -> mouseReleased(MouseButtonEvent event)
    mouseDragged(double x, double y, int b, dx, dy)    -> mouseDragged(MouseButtonEvent event, dx, dy)
    keyPressed/keyReleased(int key, int scan, int mod) -> keyPressed/keyReleased(KeyEvent event)
    charTyped(char c, int mod)                          -> charTyped(CharacterEvent event)

Only an @Override with exactly the 1.21 parameter types is converted, so a mod's own method of the same name
is never touched. The body is KEPT: the old parameter names become locals read from the event on the first
line, so nothing below the header changes. Inside a converted handler, a call to the same hook (super.*,
a child widget) passing the handler's own parameters forwards the event; one passing other values builds a
new event from them. A render-hook CALL is renamed only when its first argument is the enclosing method's
GuiGraphicsExtractor parameter, which keeps model/entity `render(...)` calls untouched (§V53).
charTyped's modifiers argument no longer exists in 26.x; a body that read it gets 0, and the tool says where.
Idempotent; standard library only.
"""
import argparse, importlib.util, pathlib, re, sys

_here = pathlib.Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location("fs", _here / "forge-shapes.py")
fs = importlib.util.module_from_spec(_s); _s.loader.exec_module(fs)
_n = importlib.util.spec_from_file_location("ni", _here / "normalise-imports.py")
ni = importlib.util.module_from_spec(_n); _n.loader.exec_module(ni)

G = "GuiGraphicsExtractor"
RENDER = {("render", (G, "int", "int", "float")): "extractRenderState",
          ("renderBackground", (G, "int", "int", "float")): "extractBackground",
          ("renderWidget", (G, "int", "int", "float")): "extractWidgetRenderState",
          ("renderLabels", (G, "int", "int")): "extractLabels",
          ("renderTooltip", (G, "int", "int")): "extractTooltip"}
INPUT = {("mouseClicked", ("double", "double", "int")), ("mouseReleased", ("double", "double", "int")),
         ("mouseDragged", ("double", "double", "int", "double", "double")),
         ("keyPressed", ("int", "int", "int")), ("keyReleased", ("int", "int", "int")), ("charTyped", ("char", "int"))}
IMPORTS = {"MouseButtonEvent": "net.minecraft.client.input.MouseButtonEvent",
           "MouseButtonInfo": "net.minecraft.client.input.MouseButtonInfo",
           "KeyEvent": "net.minecraft.client.input.KeyEvent", "CharacterEvent": "net.minecraft.client.input.CharacterEvent"}


def ptype(p):
    return " ".join(w for w in re.sub(r"@\w+(?:\([^)]*\))?", "", p).split()[:-1] if w != "final")


def overridden(text, m):
    return "@Override" in text[fs.decl_span_start(text, m.start):m.start]


def free(name, body):
    return name if not re.search(r"(?<![\w$])%s(?![\w$])" % name, body) else name + "Event"


def call_rewrites(body, hook, names, ev, dc, keep=frozenset()):
    """Rewrite `<recv>.hook(args)` and bare `hook(args)` calls inside a converted handler body. A hook in `keep`
    is one the mod also declares itself in the 1.21 shape (its own widget hierarchy): a call on a child may reach
    that method, so only super.* calls are rewritten -- the old locals stay in scope and the call still compiles."""
    masked = ni.code_spans(body)
    out, last, notes = [], 0, []
    for m in re.finditer(r"(?<![\w$])%s\s*\(" % hook, masked):
        if masked[:m.start()].rstrip().endswith(("boolean", "void")):
            continue
        if hook in keep and not re.search(r"\bsuper\s*\.\s*$", masked[:m.start()]):
            continue
        close = fs.match(masked, m.end() - 1)
        args = [a.strip() for a in fs.split_args(body[m.end():close])]
        if hook in ("mouseClicked", "mouseReleased", "mouseDragged") and len(args) >= 3:
            same = args[:3] == names[:3]
            evx = ev if same else f"new MouseButtonEvent({args[0]}, {args[1]}, new MouseButtonInfo({args[2]}, {ev}.modifiers()))"
            new = [evx] + ([dc] if hook == "mouseClicked" else []) + args[3:]
        elif hook in ("keyPressed", "keyReleased") and len(args) == 3:
            new = [ev] if args == names else [f"new KeyEvent({', '.join(args)})"]
        elif hook == "charTyped" and len(args) == 2:
            new = [ev] if args[0] == names[0] else [f"new CharacterEvent({args[0]})"]
        else:
            continue
        out.append(body[last:m.end()] + ", ".join(new))
        last = close
    out.append(body[last:])
    return "".join(out)


def own_old_hooks(texts):
    """Input hooks the mod declares ITSELF in the 1.21 shape (no @Override): its own widget base classes."""
    keep = set()
    for text in texts:
        for m in fs.methods(text):
            if (m.name, tuple(ptype(p) for p in m.params)) in INPUT and not overridden(text, m):
                keep.add(m.name)
    return frozenset(keep)


def convert_text(text, keep=frozenset()):
    notes, n = [], 0
    while True:
        changed = False
        for m in sorted(fs.methods(text), key=lambda x: -x.start):
            types = tuple(ptype(p) for p in m.params)
            names = [fs.pname(p) for p in m.params]
            key = (m.name, types)
            if not overridden(text, m):
                continue
            body = text[m.body_open + 1:m.body_close]
            line = fs.line_of(text, m.start)
            if key in RENDER:
                text = text[:m.start] + text[m.start:m.params_open].replace(m.name, RENDER[key], 1) + text[m.params_open:]
                n += 1; changed = True; break
            if m.name == "renderBg" and types == (G, "float", "int", "int"):
                if re.search(r"void\s+(?:extractBackground|renderBackground)\s*\(", text):
                    notes.append(f"line {line}: renderBg next to an extractBackground override -- merge by hand")
                    continue
                g, pt, mx, my = names
                header = text[m.start:m.params_open].replace("renderBg", "extractBackground", 1)
                params = f"{m.params[0]}, {m.params[2]}, {m.params[3]}, {m.params[1]}"
                text = (text[:m.start] + header + "(" + params + ")" + text[m.params_close + 1:m.body_open + 1]
                        + f"\n        super.extractBackground({g}, {mx}, {my}, {pt});" + text[m.body_open + 1:])
                n += 1; changed = True; break
            if key not in INPUT:
                continue
            ev = free("event", body)
            dc = free("doubleClick", body)
            if m.name in ("mouseClicked", "mouseReleased", "mouseDragged"):
                params = [f"MouseButtonEvent {ev}"] + ([f"boolean {dc}"] if m.name == "mouseClicked" else []) + m.params[3:]
                pro = [f"double {names[0]} = {ev}.x();", f"double {names[1]} = {ev}.y();", f"int {names[2]} = {ev}.button();"]
            elif m.name in ("keyPressed", "keyReleased"):
                params = [f"KeyEvent {ev}"]
                pro = [f"int {names[0]} = {ev}.key();", f"int {names[1]} = {ev}.scancode();", f"int {names[2]} = {ev}.modifiers();"]
            else:
                params = [f"CharacterEvent {ev}"]
                pro = [f"char {names[0]} = (char) {ev}.codepoint();", f"int {names[1]} = 0;"]
            indent = re.match(r"\n?([ \t]*)", body).group(1) or "        "
            new_body = call_rewrites(body, m.name, names, ev, dc, keep)
            used = ni.code_spans(new_body)                               # forwarded calls no longer name them
            pro = [p for p in pro if re.search(r"(?<![\w$])%s(?![\w$])" % p.split()[1], used)]
            if m.name == "charTyped" and any(p.startswith("int ") for p in pro):
                notes.append(f"line {line}: charTyped body reads its modifiers ({names[1]}); 26.x has none, it is 0 now")
            prologue = "".join(f"\n{indent}{p}" for p in pro)
            text = text[:m.params_open] + "(" + ", ".join(params) + ")" + text[m.params_close + 1:m.body_open + 1] \
                + prologue + new_body + text[m.body_close:]
            n += 1; changed = True; break
        if not changed:
            break
    # render-hook calls: first argument is a GuiGraphicsExtractor parameter of some method in the file
    # and only with the hook's own arity, so a mod's render(g, source, target, ...) helper keeps its name
    gnames = set(re.findall(r"%s\s+(\w+)\s*[,)]" % G, text))
    gparams = {fs.pname(p) for mm in fs.methods(text) if any(ptype(p) == G for p in mm.params) for p in mm.params}
    arity = {old: len(types) for (old, types) in RENDER}
    for old, new in (("renderBackground", "extractBackground"), ("renderWidget", "extractWidgetRenderState"),
                     ("renderLabels", "extractLabels"), ("renderTooltip", "extractTooltip"), ("render", "extractRenderState")):
        for g in gnames:
            masked = ni.code_spans(text)
            for m in reversed(list(re.finditer(r"(?<![\w$])%s(?=\s*\(\s*%s\s*,)" % (old, re.escape(g)), masked))):
                op = masked.index("(", m.end())
                args = [a.strip() for a in fs.split_args(text[op + 1:fs.match(masked, op)])]
                recv = re.search(r"(\w+)\s*\.\s*$", masked[:m.start()])
                own = recv is None or recv.group(1) in ("super", "this")
                if len(args) == arity[old] and (own or all(a in gparams for a in args[1:])):
                    text = text[:m.start()] + new + text[m.end():]
                    n += 1
    if n:
        body = ni.code_spans(re.sub(r"(?m)^import[^\n]*\n", "", text))
        for simple, fq in IMPORTS.items():
            if re.search(r"(?<![\w.])%s\b" % simple, body) and not re.search(r"(?m)^import\s+[\w.]*\.%s\s*;" % simple, text):
                text = ni.add_import(text, fq)
    return text, n, notes


def run(src, dry=False):
    total, report = 0, []
    keep = own_old_hooks(f.read_text(encoding="utf-8") for f in pathlib.Path(src).rglob("*.java"))
    for f in sorted(pathlib.Path(src).rglob("*.java")):
        t = f.read_text(encoding="utf-8")
        if not re.search(r"\b(?:render\w*|renderBg|mouse(?:Clicked|Released|Dragged)|key(?:Pressed|Released)|charTyped)\s*\(", t):
            continue
        out, n, notes = convert_text(t, keep)
        total += n
        if notes:
            report.append((f, notes))
        if out != t and not dry:
            f.write_text(out, encoding="utf-8")
    return total, report


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src"); ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not a.src:
        ap.error("--src is required")
    total, report = run(a.src, a.dry_run)
    print(f"convert-gui-hooks: {total} hook(s) and call(s) converted" + (" (dry run)" if a.dry_run else ""))
    for f, notes in report:
        for x in notes:
            print(f"  note {f.name} {x}")
    return 0


def self_check():
    t = '''package m;

import net.minecraft.client.gui.GuiGraphicsExtractor;

class S extends Screen {
    @Override
    public void render(GuiGraphicsExtractor g, int mx, int my, float pt) {
        this.renderBackground(g, mx, my, pt);
        super.render(g, mx, my, pt);
        model.render(pose, buffer, light, overlay);
        Helper.render(g, source, 1, 2, 3);
        command.render(g, font, 4, 5);
        child.render(g, mx, my, pt);
    }

    @Override
    protected void renderBg(GuiGraphicsExtractor g, float pt, int mx, int my) {
        g.blit(T, 0, 0);
    }

    @Override
    public boolean mouseClicked(double mouseX, double mouseY, int button) {
        if (child.mouseClicked(mouseX - 4, mouseY, button)) return true;
        return super.mouseClicked(mouseX, mouseY, button);
    }

    @Override
    public boolean keyPressed(int keyCode, int scanCode, int modifiers) {
        if (keyCode == 256) return true;
        return super.keyPressed(keyCode, scanCode, modifiers);
    }

    @Override
    public boolean charTyped(char c, int mods) {
        return box.charTyped(c, mods);
    }

    public boolean mouseClicked(double a, double b) { return false; }
}
'''
    out, n, notes = convert_text(t)
    want = ["public void extractRenderState(GuiGraphicsExtractor g, int mx, int my, float pt)",
            "this.extractBackground(g, mx, my, pt);", "super.extractRenderState(g, mx, my, pt);",
            "model.render(pose, buffer, light, overlay);", "Helper.render(g, source, 1, 2, 3);", "command.render(g, font, 4, 5);",
            "child.extractRenderState(g, mx, my, pt);",
            "protected void extractBackground(GuiGraphicsExtractor g, int mx, int my, float pt) {\n        super.extractBackground(g, mx, my, pt);",
            "public boolean mouseClicked(MouseButtonEvent event, boolean doubleClick) {\n        double mouseX = event.x();\n        double mouseY = event.y();\n        int button = event.button();",
            "child.mouseClicked(new MouseButtonEvent(mouseX - 4, mouseY, new MouseButtonInfo(button, event.modifiers())), doubleClick)",
            "return super.mouseClicked(event, doubleClick);",
            "public boolean keyPressed(KeyEvent event) {\n        int keyCode = event.key();\n        if (keyCode == 256)",
            "return super.keyPressed(event);",
            "public boolean charTyped(CharacterEvent event) {\n        return box.charTyped(event);", "public boolean mouseClicked(double a, double b) { return false; }",
            "import net.minecraft.client.input.KeyEvent;", "import net.minecraft.client.input.MouseButtonInfo;"]
    miss = [w for w in want if w not in out]
    if "int scanCode = event.scancode();" in out:
        miss.append("unused local declared")
    if notes:
        miss.append(f"unexpected notes {notes}")
    kept, _, _ = convert_text(t, frozenset({"mouseClicked"}))   # the mod has its own old-shape mouseClicked
    if "child.mouseClicked(mouseX - 4, mouseY, button)" not in kept or "return super.mouseClicked(event, doubleClick);" not in kept:
        miss.append("own-hook guard: a child call to a mod-declared old-shape hook was rewritten (or super was not)")
    base = "class Base {\n    public boolean mouseClicked(double x, double y, int b) { return false; }\n}\n"
    if own_old_hooks([t]) or own_old_hooks([t, base]) != frozenset({"mouseClicked"}):
        miss.append(f"own_old_hooks: {sorted(own_old_hooks([t]))} / {sorted(own_old_hooks([t, base]))}")
    again = convert_text(out)
    if again[1] or again[0] != out:
        miss.append("not idempotent")
    print("self-check:", "OK" if not miss else f"FAIL {miss}\n{out}")
    return 0 if not miss else 1


if __name__ == "__main__":
    sys.exit(main())
