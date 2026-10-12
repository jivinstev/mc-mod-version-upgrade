#!/usr/bin/env python3
"""Port Forge 1.20 capabilities to NeoForge 1.21.1 through a mod-local compat layer, keeping the mod's own code.

    python3 tools/convert-capabilities.py --src src/main/java --modid <modid> [--dry-run] [--self-check]

NeoForge 1.21.1 removed Forge's capability system outright (CATALOG §D13, §C12e): no LazyOptional, no Capability,
no AttachCapabilitiesEvent, no BlockEntity#getCapability. Swept across the popularity list it was the largest single
Forge bucket left after every other converter (X146: 37 mods, LazyOptional alone on ~6300 error lines). A faithful
rewrite to BlockCapability / data attachments is per-mod design work; most of that code, though, only USES the API
-- `be.getCapability(ForgeCapabilities.ITEM_HANDLER, side).ifPresent(...)`, a block entity answering `cap ==
ForgeCapabilities.ITEM_HANDLER ? handler.cast() : super.getCapability(cap, side)` -- and keeps working unchanged if
the API is still there. So this converter puts it back, in the mod's own package, and wires it into NeoForge:

  <root>.compat.caps   LazyOptional, Capability, CapabilityToken, CapabilityManager, ForgeCapabilities,
                       ICapabilityProvider, ICapabilitySerializable, ICapabilityItem, AttachCapabilitiesEvent,
                       NonNull{Supplier,Consumer,Function,Predicate} -- the Forge API's shape, standard library inside;
                       Caps -- the glue: Caps.get(holder, cap[, side]) answers what `holder.getCapability(...)` did.
  imports              the Forge capability types -> the compat ones (Forge and codemodded-NeoForge spellings).
  providers            a class declaring getCapability(Capability<T>, Direction) implements ICapabilityProvider; its
                       @Override goes; `super.getCapability(..)` becomes LazyOptional.empty() unless a MOD superclass
                       declares it. invalidateCaps/reviveCaps lose @Override and their vanilla super calls.
                       An Item overriding initCapabilities(ItemStack, CompoundTag) implements ICapabilityItem.
  consumers            `x.getCapability(args)` -> `Caps.get(x, args)`, which asks the holder itself, then the
                       capabilities attached to it, then NeoForge's own (Capabilities.ItemHandler.BLOCK etc.).
  attachers            AttachCapabilitiesEvent handlers stop being bus listeners (the event no longer exists): a
                       static @SubscribeEvent handler is listed in Caps.registerAttachers(); a
                       `bus.addGenericListener(Entity.class, ref)` / `addListener(ref)` becomes `Caps.attach(...)` in
                       place. Attached providers live in ONE data attachment per holder (Entity, BlockEntity, Level,
                       LevelChunk), created lazily and saved through ICapabilitySerializable.
  wiring               `Caps.register(modBus)` in the @Mod constructor: the attachment type, and a
                       RegisterCapabilitiesEvent listener that exposes every mod block entity's / item's own
                       ITEM_HANDLER, FLUID_HANDLER(_ITEM) and ENERGY to NeoForge -- so hoppers and pipes see them.

What it cannot do it LISTS: instance @SubscribeEvent attachers, ItemStack attachers (an ItemStack is not an
attachment holder in 1.21.1 -- its caps would not persist), a getCapability method reference, a mod with providers but
no IEventBus in its @Mod constructor. Forge's old saves kept capability data under "ForgeCaps"; it is not migrated.
Standard library only.
"""
import argparse, collections, importlib.util, pathlib, re, sys

_spec = importlib.util.spec_from_file_location("forge_shapes", pathlib.Path(__file__).resolve().parent / "forge-shapes.py")
fs = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(fs)
match, split_args, methods, add_import, remove_import_if_unused = (fs.match, fs.split_args, fs.methods, fs.add_import,
                                                                   fs.remove_import_if_unused)

FORGE_PREFIXES = (r"net\.minecraftforge", r"net\.neoforged\.neoforge")
CAP_TYPES = ["Capability", "CapabilityManager", "CapabilityToken", "ICapabilityProvider", "ICapabilitySerializable",
             "ForgeCapabilities", "CapabilityProvider"]
UTIL_TYPES = ["LazyOptional", "NonNullSupplier", "NonNullConsumer", "NonNullFunction", "NonNullPredicate"]
NEO_RCE = "net.neoforged.neoforge.capabilities.RegisterCapabilitiesEvent"


def _rewrite_names(text, pkg):
    """Forge capability types -> the compat package, in imports and inline FQNs alike."""
    n = 0
    for pre in FORGE_PREFIXES:
        for t in CAP_TYPES:
            text, k = re.subn(r"\b%s\.common\.capabilities\.%s\b" % (pre, t), f"{pkg}.{t}", text); n += k
        for t in UTIL_TYPES:
            text, k = re.subn(r"\b%s\.common\.util\.%s\b" % (pre, t), f"{pkg}.{t}", text); n += k
        text, k = re.subn(r"\b%s\.event\.AttachCapabilitiesEvent\b" % pre, f"{pkg}.AttachCapabilitiesEvent", text); n += k
        text, k = re.subn(r"\b%s\.common\.capabilities\.RegisterCapabilitiesEvent\b" % pre, NEO_RCE, text); n += k
        text, k = re.subn(r"(?m)^import\s+%s\.common\.capabilities\.\*\s*;" % pre, f"import {pkg}.*;", text); n += k
        text, k = re.subn(r"(?m)^import\s+%s\.common\.capabilities\.AutoRegisterCapability\s*;[ \t]*\n" % pre, "", text); n += k
    text = re.sub(r"(?m)^[ \t]*@(?:[\w.]+\.)?AutoRegisterCapability\b[ \t]*\n?", "", text)
    # an import of the compat type the file already has twice (Forge + codemodded spelling) collapses to one
    seen, out = set(), []
    for line in text.split("\n"):
        if line.startswith("import ") and line in seen:
            continue
        if line.startswith("import "):
            seen.add(line)
        out.append(line)
    return "\n".join(out), n


def _classes(text):
    """[(name, header_start, body_open, body_close)] of every NAMED class/interface/enum/record."""
    out = []
    for m in re.finditer(r"\b(?:class|interface|enum|record)\s+(\w+)\b[^;{]*\{", text):
        if text[max(0, m.start() - 1)] in ".$\"":
            continue
        b0 = m.end() - 1; b1 = match(text, b0)
        if b1 > 0:
            out.append((m.group(1), m.start(), b0, b1))
    return out


def _direct_owner(text, pos):
    """The innermost named class whose body directly (depth 1) contains pos, or None (an anonymous class, a lambda
    body, a nested block)."""
    best = None
    for c in _classes(text):
        if c[2] < pos < c[3] and (best is None or c[2] > best[2]):
            best = c
    if best is None:
        return None
    depth, i = 0, best[2] + 1
    while i < pos:
        k = fs._skip(text, i)
        if k != i:
            i = k; continue
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    return best if depth == 0 else None


def _class_header(text, cls):
    return text[cls[1]:cls[2]]


def _add_implements(text, cls, iface):
    """Append iface to cls's implements list (or add one); interfaces get `extends`."""
    head = _class_header(text, cls)
    simple = iface.rsplit(".", 1)[-1]
    if re.search(r"\b%s\b" % re.escape(simple), head) or (simple == "ICapabilityProvider" and "ICapabilitySerializable" in head):
        return text, False
    kw = "extends" if re.match(r"interface\b", head) else "implements"
    if re.search(r"\b%s\b" % kw, head) and not (kw == "extends" and False):
        m = list(re.finditer(r"\b%s\b" % kw, head))[-1]
        if kw == "implements" or re.match(r"interface\b", head):
            new_head = head[:m.end()] + f" {iface}," + head[m.end():]
        else:
            new_head = head
    else:
        new_head = head.rstrip() + f" {kw} {iface} "
    return text[:cls[1]] + new_head + text[cls[2]:], True


def _strip_override(text, mth):
    s = fs.decl_span_start(text, mth.start)
    seg = text[s:mth.start]
    seg2 = re.sub(r"(?m)^[ \t]*@Override[ \t]*\n", "", seg)
    seg2 = re.sub(r"@Override\s+", "", seg2)
    return text[:s] + seg2 + text[mth.start:], len(seg) - len(seg2)


def _receiver_start(text, dot):
    """Start of the expression ending just before text[dot] == '.'."""
    i = dot
    while True:
        j = i - 1
        while j >= 0 and text[j] in " \t\n":
            j -= 1
        if j < 0:
            return i
        c = text[j]
        if c in ")]":
            depth, k = 0, j
            while k >= 0:
                if text[k] in ")]":
                    depth += 1
                elif text[k] in "([":
                    depth -= 1
                    if depth == 0:
                        break
                k -= 1
            i = k
            m = re.search(r"[\w$]+\s*$", text[:i])        # a call: the method name before its parenthesis
            if m and m.group(0).strip() not in ("return", "if", "while", "for", "switch", "new"):
                i = m.start()
            j = i - 1
            while j >= 0 and text[j] in " \t\n":
                j -= 1
            if j >= 0 and text[j] == ".":
                i = j; continue
            return i
        if re.match(r"[\w$]", c):
            m = re.search(r"[\w$]+$", text[:j + 1])
            i = m.start()
            if m.group(0) in ("return", "new", "throw", "else", "case"):
                return j + 1
            j = i - 1
            while j >= 0 and text[j] in " \t\n":
                j -= 1
            if j >= 0 and text[j] == ".":
                i = j; continue
            return i
        return i


def _mod_supers(files):
    """{simple class name: (superclass simple name, declares getCapability, declares invalidateCaps, declares reviveCaps)}."""
    out = {}
    for f, t in files.items():
        for c in _classes(t):
            head = _class_header(t, c)
            sm = re.search(r"\bextends\s+([\w.]+)", head)
            body = t[c[2]:c[3]]
            out[c[0]] = ((sm.group(1).rsplit(".", 1)[-1] if sm else None),
                         bool(re.search(r"LazyOptional<\w+>\s+getCapability\s*\(", body)),
                         bool(re.search(r"\bvoid\s+invalidateCaps\s*\(", body)),
                         bool(re.search(r"\bvoid\s+reviveCaps\s*\(", body)))
    return out


def _super_declares(supers, cls_name, idx):
    seen, cur = set(), supers.get(cls_name, (None,))[0]
    while cur and cur in supers and cur not in seen:
        seen.add(cur)
        if supers[cur][idx]:
            return True
        cur = supers[cur][0]
    return False


def _type_fqn(text, simple, pkg_of_file, own_types):
    if simple in ("?", "Object", ""):
        return "java.lang.Object"
    m = re.search(r"(?m)^import\s+([\w.]+\.%s)\s*;" % re.escape(simple), text)
    if m:
        return m.group(1)
    if simple in own_types:
        return own_types[simple]
    if re.fullmatch(r"[\w.]+\.[A-Z]\w*", simple):
        return simple
    return {"Entity": "net.minecraft.world.entity.Entity", "Player": "net.minecraft.world.entity.player.Player",
            "BlockEntity": "net.minecraft.world.level.block.entity.BlockEntity", "Level": "net.minecraft.world.level.Level",
            "LevelChunk": "net.minecraft.world.level.chunk.LevelChunk", "ItemStack": "net.minecraft.world.item.ItemStack"
            }.get(simple, f"{pkg_of_file}.{simple}" if pkg_of_file else simple)


def run(src, modid, dry=False):
    src = pathlib.Path(src).resolve()
    files = {f: f.read_text(encoding="utf-8", errors="replace") for f in sorted(src.rglob("*.java"))}
    root = fs.root_package(src) or modid
    pkg = f"{root}.compat.caps"
    pkg_dir = src / pkg.replace(".", "/")
    files = {f: t for f, t in files.items() if pkg_dir not in f.parents}
    report, flags = collections.Counter(), []
    if not any(re.search(r"common\.capabilities\.|common\.util\.LazyOptional|event\.AttachCapabilitiesEvent", t)
               for t in files.values()):
        return report, flags, 0
    new = {}
    for f, t in files.items():
        t2, n = _rewrite_names(t, pkg)
        new[f] = t2
        report["imports"] += n
    own_types = {}
    for f, t in new.items():
        pm = re.search(r"(?m)^package\s+([\w.]+)\s*;", t)
        for c in _classes(t):
            own_types.setdefault(c[0], (pm.group(1) + "." if pm else "") + c[0])
    supers = _mod_supers(new)
    attachers, providers_be = [], 0
    uses = lambda t: re.search(r"\b(?:LazyOptional|Capability|ForgeCapabilities|ICapabilityProvider|ICapabilitySerializable|"
                               r"AttachCapabilitiesEvent|CapabilityManager)\b", t) or ".getCapability(" in t
    for f in list(new):
        t = new[f]
        if not uses(t):
            continue
        rel = f.relative_to(src).as_posix()
        pm = re.search(r"(?m)^package\s+([\w.]+)\s*;", t)
        fpkg = pm.group(1) if pm else ""
        # -- providers: getCapability(Capability<T>, Direction) declarations
        for mth in sorted(methods(t), key=lambda x: -x.start):
            if mth.name != "getCapability" or not mth.params or "Capability<" not in mth.params[0] or "LazyOptional" not in mth.ret:
                continue
            owner = _direct_owner(t, mth.start)
            body = t[mth.body_open:mth.body_close]
            if owner and not _super_declares(supers, owner[0], 1):
                body2, k = re.subn(r"\bsuper\s*\.\s*getCapability\s*\((?:[^()]|\([^()]*\))*\)", "LazyOptional.empty()", body)
                if k:
                    t = t[:mth.body_open] + body2 + t[mth.body_close:]
                    report["super calls"] += k
            t, k = _strip_override(t, mth)
            if owner:
                owner = _direct_owner(t, mth.start)
            if owner and len(mth.params) == 2 and not _super_declares(supers, owner[0], 1):
                t, added = _add_implements(t, owner, f"{pkg}.ICapabilityProvider")
                report["providers"] += 1
                providers_be += 1
        # -- invalidateCaps / reviveCaps
        for name, idx in (("invalidateCaps", 2), ("reviveCaps", 3)):
            for mth in sorted(methods(t), key=lambda x: -x.start):
                if mth.name != name or mth.params:
                    continue
                owner = _direct_owner(t, mth.start)
                if owner and not _super_declares(supers, owner[0], idx):
                    body = t[mth.body_open:mth.body_close]
                    body2, k = re.subn(r"(?m)^[ \t]*super\s*\.\s*%s\s*\(\s*\)\s*;[ \t]*\n?" % name, "", body)
                    t = t[:mth.body_open] + body2 + t[mth.body_close:]
                    report["cap lifecycle"] += k
                t, _k = _strip_override(t, mth)
            # a call on ANOTHER object (`event.getOriginal().reviveCaps();`): the method no longer exists there
            t, k = re.subn(r"(?m)^([ \t]*)(?!this\b|super\b)([\w.()]+?)\s*\.\s*%s\s*\(\s*\)\s*;" % name,
                           r"\1/* 1.21 MIGRATION (X146): \2.%s() -- capabilities are no longer invalidated */" % name, t)
            report["cap lifecycle"] += k
        # -- items: initCapabilities(ItemStack, CompoundTag)
        for mth in sorted(methods(t), key=lambda x: -x.start):
            if mth.name == "initCapabilities" and len(mth.params) == 2 and "ItemStack" in mth.params[0]:
                t, _k = _strip_override(t, mth)
                owner = _direct_owner(t, mth.start)
                if owner:
                    t, _a = _add_implements(t, owner, f"{pkg}.ICapabilityItem")
                    report["item providers"] += 1
                    providers_be += 1
        # -- Forge's RegisterCapabilitiesEvent#register(Class): nothing to register any more
        t, k = re.subn(r"(?m)^[ \t]*\w+\s*\.\s*register\(\s*[\w.]+\.class\s*\)\s*;[ \t]*\n(?=(?:(?!\n\s*(?:public|private|protected)\b)[\s\S])*?\})",
                       "", t) if "RegisterCapabilitiesEvent" in t else (t, 0)
        report["capability registrations dropped"] += k
        # -- attachers
        for mth in sorted(methods(t), key=lambda x: -x.start):
            if len(mth.params) != 1 or "AttachCapabilitiesEvent" not in mth.params[0]:
                continue
            gm = re.search(r"AttachCapabilitiesEvent\s*<\s*([\w.?]+)", mth.params[0])
            tp = _type_fqn(t, gm.group(1) if gm else "?", fpkg, own_types)
            s = fs.decl_span_start(t, mth.start)
            if "@SubscribeEvent" not in t[s:mth.start]:
                continue
            static = bool(re.search(r"\bstatic\b", t[mth.start:mth.params_open]))
            owner = _direct_owner(t, mth.start)
            if tp.endswith(".ItemStack"):
                flags.append(f"{rel}:{fs.line_of(t, mth.start)} {mth.name} attaches to ItemStack: not supported (no persistence); left")
                continue
            if not static or owner is None:
                flags.append(f"{rel}:{fs.line_of(t, mth.start)} {mth.name} is an instance @SubscribeEvent attacher; register it "
                             f"with Caps.attach({tp.rsplit('.', 1)[-1]}.class, this::{mth.name}) by hand")
                continue
            seg = re.sub(r"(?m)^[ \t]*@SubscribeEvent(?:\([^)]*\))?[ \t]*\n", "", t[s:mth.start])
            t = t[:s] + seg + t[mth.start:]
            attachers.append((tp, f"{fpkg + '.' if fpkg else ''}{owner[0]}", mth.name))
            report["attachers"] += 1
        # `bus.addGenericListener(Entity.class, ref);` / `bus.addListener(ref)` naming an attacher -> Caps.attach in place
        for m in reversed(list(re.finditer(r"(?m)^([ \t]*)[\w.()]+\s*\.\s*addGenericListener\(\s*([\w.]+)\.class\s*,\s*([\w.]+::\w+)\s*\)\s*;", t))):
            ref = m.group(3)
            mname = ref.split("::")[1]
            if not any(mm.name == mname and mm.params and "AttachCapabilitiesEvent" in mm.params[0]
                       for tt in new.values() for mm in methods(tt) if mname in tt):
                continue
            t = t[:m.start()] + f"{m.group(1)}{pkg}.Caps.attach({m.group(2)}.class, {ref});" + t[m.end():]
            report["attachers"] += 1
        new[f] = t
    # second pass: consumers + R1 safety (after every provider is known)
    provider_classes = {c[0] for t in new.values() for c in _classes(t)
                        if re.search(r"\bICapabilityProvider\b", _class_header(t, c))}
    for f in list(new):
        t = new[f]
        if ".getCapability(" not in t and not re.search(r"(?<![\w$.])getCapability\s*\(", t):
            continue
        rel = f.relative_to(src).as_posix()
        out, i = [], 0
        for m in re.finditer(r"\.\s*getCapability\s*\(", t):
            if m.start() < i:
                continue
            p0 = m.end() - 1; p1 = match(t, p0)
            if p1 < 0:
                continue
            args = t[p0 + 1:p1]
            if re.search(r"(?<![\w$])Capabilities\.", args.split(",")[0]) or not args.strip():
                continue
            rs = _receiver_start(t, m.start())
            recv = t[rs:m.start()].strip()
            if recv in ("this", "super") or not recv:
                continue
            out.append(t[i:rs] + f"Caps.get({recv}, {args})")
            i = p1 + 1
            report["consumers"] += 1
        out.append(t[i:])
        t2 = "".join(out)
        # bare calls in a class that does not declare its own getCapability
        def bare(mm):
            pre = t2[:mm.start()].rstrip()
            if not pre or pre[-1] not in "(=,!&|?:;{}" and not pre.endswith("return"):
                return mm.group(0)
            owner = _direct_owner(t2, mm.start())
            if owner is None:
                return mm.group(0)
            if owner[0] in provider_classes or re.search(r"LazyOptional<\w+>\s+getCapability\s*\(", t2[owner[2]:owner[3]]):
                return mm.group(0)
            report["consumers"] += 1
            return "Caps.get(this, "
        t2 = re.sub(r"(?<![\w$.])getCapability\s*\((?=\s*\S)", bare, t2)
        if "::getCapability" in t2:
            flags.append(f"{rel}: a getCapability method reference; left")
        if "Caps.get(" in t2:
            t2 = add_import(t2, f"{pkg}.Caps")
        new[f] = t2
    # R1: a subscriber class left with no @SubscribeEvent crashes NeoForge at registration
    for f, t in new.items():
        if t != files[f] and re.search(r"@(?:Mod\.)?EventBusSubscriber\b", t) and "@SubscribeEvent" not in t:
            new[f] = re.sub(r"(?m)^[ \t]*@(?:[\w.]+\.)?(?:Mod\.)?EventBusSubscriber\b(?:\((?:[^()]|\([^()]*\))*\))?[ \t]*\n", "", t)
            report["emptied subscribers"] += 1
    # wiring: Caps.register(modBus) in the @Mod constructor
    wired = False
    for f, t in new.items():
        if not re.search(r"@(?:[\w.]+\.)?Mod\s*\(", t):
            continue
        cm = re.search(r"@(?:[\w.]+\.)?Mod\s*\([^)]*\)\s*(?:public\s+)?(?:final\s+)?class\s+(\w+)", t)
        if not cm:
            continue
        km = re.search(r"\b%s\s*\(([^)]*\bIEventBus\s+(\w+)[^)]*)\)\s*\{" % re.escape(cm.group(1)), t)
        if km:
            if f"{pkg}.Caps.register(" not in t:
                t = t[:km.end()] + f"\n      {pkg}.Caps.register({km.group(2)});" + t[km.end():]
                new[f] = t
            wired = True
            break
    if not wired and (attachers or providers_be):
        flags.append("no @Mod constructor with an IEventBus parameter: call "
                     f"{pkg}.Caps.register(modBus) from it by hand, or attached capabilities have no storage")
    # shims
    shims = shim_sources(pkg, modid, attachers)
    changed = 0
    for f, t in new.items():
        if t != files[f]:
            changed += 1
            if not dry:
                f.write_text(t, encoding="utf-8")
    if not dry:
        pkg_dir.mkdir(parents=True, exist_ok=True)
        for name, body in shims.items():
            (pkg_dir / f"{name}.java").write_text(body, encoding="utf-8")
    report["shim classes"] = len(shims)
    return report, flags, changed


def shim_sources(pkg, modid, attachers):
    H = f"package {pkg};\n\n// Generated by tools/convert-capabilities.py (CATALOG X146): Forge's capability API, kept for this port.\n"
    s = {}
    s["NonNullSupplier"] = H + "@FunctionalInterface\npublic interface NonNullSupplier<T> extends java.util.function.Supplier<T> {}\n"
    s["NonNullConsumer"] = H + "@FunctionalInterface\npublic interface NonNullConsumer<T> extends java.util.function.Consumer<T> {}\n"
    s["NonNullFunction"] = H + "@FunctionalInterface\npublic interface NonNullFunction<T, R> extends java.util.function.Function<T, R> {}\n"
    s["NonNullPredicate"] = H + "@FunctionalInterface\npublic interface NonNullPredicate<T> extends java.util.function.Predicate<T> {}\n"
    s["LazyOptional"] = H + """
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.function.Consumer;
import java.util.function.Function;
import java.util.function.Predicate;
import java.util.function.Supplier;

public final class LazyOptional<T> {
    private static final LazyOptional<Object> EMPTY = new LazyOptional<>(null);
    private final Supplier<? extends T> supplier;
    private T value;
    private boolean resolved;
    private boolean valid = true;
    private final List<Consumer<LazyOptional<T>>> listeners = new ArrayList<>();

    private LazyOptional(Supplier<? extends T> supplier) { this.supplier = supplier; }

    public static <T> LazyOptional<T> of(Supplier<T> supplier) { return supplier == null ? empty() : new LazyOptional<>(supplier); }
    @SuppressWarnings("unchecked") public static <T> LazyOptional<T> empty() { return (LazyOptional<T>) EMPTY; }
    @SuppressWarnings("unchecked") public <X> LazyOptional<X> cast() { return (LazyOptional<X>) this; }

    private T getValue() {
        if (!valid || supplier == null) return null;
        if (!resolved) { value = supplier.get(); resolved = true; }
        return value;
    }
    public boolean isPresent() { return getValue() != null; }
    public void ifPresent(Consumer<? super T> consumer) { T v = getValue(); if (v != null) consumer.accept(v); }
    public <U> LazyOptional<U> lazyMap(Function<? super T, ? extends U> mapper) {
        return isPresent() ? LazyOptional.of(() -> mapper.apply(getValue())) : empty();
    }
    public <U> Optional<U> map(Function<? super T, ? extends U> mapper) { return resolve().map(mapper); }
    public Optional<T> filter(Predicate<? super T> predicate) { return resolve().filter(predicate); }
    public Optional<T> resolve() { return Optional.ofNullable(getValue()); }
    public T orElse(T other) { T v = getValue(); return v != null ? v : other; }
    public T orElseGet(Supplier<? extends T> other) { T v = getValue(); return v != null ? v : other.get(); }
    public T orElseThrow() { T v = getValue(); if (v == null) throw new java.util.NoSuchElementException("No value present"); return v; }
    public <X extends Throwable> T orElseThrow(Supplier<? extends X> exception) throws X {
        T v = getValue(); if (v == null) throw exception.get(); return v;
    }
    public void addListener(Consumer<LazyOptional<T>> listener) { if (isPresent()) listeners.add(listener); else listener.accept(this); }
    public void removeListener(Consumer<LazyOptional<T>> listener) { listeners.remove(listener); }
    public void invalidate() {
        if (valid) { valid = false; for (Consumer<LazyOptional<T>> l : new ArrayList<>(listeners)) l.accept(this); }
    }
}
"""
    s["Capability"] = H + """
public final class Capability<T> {
    private final String name;
    Capability(String name) { this.name = name; }
    public String getName() { return name; }
    public boolean isRegistered() { return true; }
    public <R> LazyOptional<R> orEmpty(Capability<R> toCheck, LazyOptional<T> inst) {
        return this == toCheck ? inst.cast() : LazyOptional.empty();
    }
    @Override public String toString() { return name; }
}
"""
    s["CapabilityToken"] = H + """
public abstract class CapabilityToken<T> {
    protected final String getType() {
        java.lang.reflect.Type t = getClass().getGenericSuperclass();
        return t instanceof java.lang.reflect.ParameterizedType p ? p.getActualTypeArguments()[0].getTypeName() : getClass().getName();
    }
    @Override public String toString() { return "CapabilityToken[" + getType() + "]"; }
}
"""
    s["CapabilityManager"] = H + """
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

public final class CapabilityManager {
    private static final Map<String, Capability<?>> BY_TYPE = new ConcurrentHashMap<>();
    private CapabilityManager() {}
    /** One Capability per TYPE, as Forge's was: two tokens naming the same interface are the same capability. */
    public static <T> Capability<T> get(CapabilityToken<T> token) { return byType(token.getType()); }
    @SuppressWarnings("unchecked")
    static <T> Capability<T> byType(String type) { return (Capability<T>) BY_TYPE.computeIfAbsent(type, Capability::new); }
}
"""
    s["ForgeCapabilities"] = H + """
import net.neoforged.neoforge.energy.IEnergyStorage;
import net.neoforged.neoforge.fluids.capability.IFluidHandler;
import net.neoforged.neoforge.fluids.capability.IFluidHandlerItem;
import net.neoforged.neoforge.items.IItemHandler;

public final class ForgeCapabilities {
    public static final Capability<IEnergyStorage> ENERGY = CapabilityManager.byType(IEnergyStorage.class.getName());
    public static final Capability<IFluidHandler> FLUID_HANDLER = CapabilityManager.byType(IFluidHandler.class.getName());
    public static final Capability<IFluidHandlerItem> FLUID_HANDLER_ITEM = CapabilityManager.byType(IFluidHandlerItem.class.getName());
    public static final Capability<IItemHandler> ITEM_HANDLER = CapabilityManager.byType(IItemHandler.class.getName());
    private ForgeCapabilities() {}
}
"""
    s["ICapabilityProvider"] = H + """
import net.minecraft.core.Direction;
import org.jetbrains.annotations.Nullable;

public interface ICapabilityProvider {
    <T> LazyOptional<T> getCapability(Capability<T> cap, @Nullable Direction side);
    default <T> LazyOptional<T> getCapability(Capability<T> cap) { return getCapability(cap, null); }
}
"""
    s["ICapabilitySerializable"] = H + """
import net.minecraft.nbt.Tag;

public interface ICapabilitySerializable<T extends Tag> extends ICapabilityProvider {
    T serializeNBT();
    void deserializeNBT(T nbt);
}
"""
    s["ICapabilityItem"] = H + """
import net.minecraft.nbt.CompoundTag;
import net.minecraft.world.item.ItemStack;
import org.jetbrains.annotations.Nullable;

/** An Item that overrode Forge's initCapabilities: Caps asks it for the stack's provider. */
public interface ICapabilityItem {
    @Nullable ICapabilityProvider initCapabilities(ItemStack stack, @Nullable CompoundTag nbt);
}
"""
    s["AttachCapabilitiesEvent"] = H + """
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import net.minecraft.resources.ResourceLocation;

/** Not a bus event any more: Caps runs the mod's attachers itself when a holder's capabilities are first asked for. */
public class AttachCapabilitiesEvent<T> {
    private final T object;
    private final Map<ResourceLocation, ICapabilityProvider> caps = new LinkedHashMap<>();
    private final List<Runnable> listeners = new ArrayList<>();
    public AttachCapabilitiesEvent(T object) { this.object = object; }
    public T getObject() { return object; }
    public void addCapability(ResourceLocation key, ICapabilityProvider cap) {
        if (caps.containsKey(key)) throw new IllegalStateException("Duplicate Capability Key: " + key + " " + cap);
        caps.put(key, cap);
    }
    public Map<ResourceLocation, ICapabilityProvider> getCapabilities() { return Collections.unmodifiableMap(caps); }
    public void addListener(Runnable listener) { listeners.add(listener); }
    public List<Runnable> getListeners() { return Collections.unmodifiableList(listeners); }
}
"""
    att = "".join(f"        attach({tp}.class, {cls}::{m});\n" for tp, cls, m in attachers)
    s["Caps"] = H + f"""
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;
import java.util.function.Supplier;
import net.minecraft.core.Direction;
import net.minecraft.core.HolderLookup;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.nbt.Tag;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.entity.BlockEntity;
import net.minecraft.world.level.block.entity.BlockEntityType;
import net.neoforged.bus.api.IEventBus;
import net.neoforged.neoforge.attachment.AttachmentType;
import net.neoforged.neoforge.attachment.IAttachmentHolder;
import net.neoforged.neoforge.attachment.IAttachmentSerializer;
import net.neoforged.neoforge.capabilities.Capabilities;
import net.neoforged.neoforge.capabilities.RegisterCapabilitiesEvent;
import net.neoforged.neoforge.registries.DeferredHolder;
import net.neoforged.neoforge.registries.DeferredRegister;
import net.neoforged.neoforge.registries.NeoForgeRegistries;
import org.jetbrains.annotations.Nullable;

/** The glue between Forge-shaped capability code and NeoForge 1.21.1. */
public final class Caps {{
    private static final String MODID = "{modid}";
    private record Attacher<T>(Class<T> type, Consumer<? super AttachCapabilitiesEvent<T>> handler) {{}}
    private static final List<Attacher<?>> ATTACHERS = new ArrayList<>();
    private static final DeferredRegister<AttachmentType<?>> ATTACHMENTS = DeferredRegister.create(NeoForgeRegistries.Keys.ATTACHMENT_TYPES, MODID);
    private static final DeferredHolder<AttachmentType<?>, AttachmentType<Store>> STORE =
        ATTACHMENTS.register("forge_capabilities", () -> AttachmentType.builder(Store::new).serialize(new StoreSerializer()).build());
    private Caps() {{}}

    /** From the @Mod constructor: the attachment that holds attached capabilities, and NeoForge registration. */
    public static void register(IEventBus modBus) {{
        ATTACHMENTS.register(modBus);
        modBus.addListener(Caps::onRegisterCapabilities);
        registerAttachers();
    }}

    private static void registerAttachers() {{
{att}    }}

    /** What AttachCapabilitiesEvent<T> listeners were: run for every holder of type T, the first time it is asked. */
    public static <T> void attach(Class<T> type, Consumer<? super AttachCapabilitiesEvent<T>> handler) {{
        ATTACHERS.add(new Attacher<>(type, handler));
    }}

    public static <T> LazyOptional<T> get(@Nullable Object holder, Capability<T> cap) {{ return get(holder, cap, null); }}

    /** What `holder.getCapability(cap, side)` answered under Forge. */
    @SuppressWarnings("unchecked")
    public static <T> LazyOptional<T> get(@Nullable Object holder, @Nullable Capability<T> cap, @Nullable Direction side) {{
        if (holder == null || cap == null) return LazyOptional.empty();
        if (holder instanceof ICapabilityProvider p) {{
            LazyOptional<T> r = p.getCapability(cap, side);
            if (r.isPresent()) return r;
        }}
        if (holder instanceof ItemStack stack && stack.getItem() instanceof ICapabilityItem ci) {{
            ICapabilityProvider p = ci.initCapabilities(stack, null);
            if (p != null) {{
                LazyOptional<T> r = p.getCapability(cap, side);
                if (r.isPresent()) return r;
            }}
        }}
        if (holder instanceof IAttachmentHolder h && !ATTACHERS.isEmpty() && STORE.isBound()) {{
            for (ICapabilityProvider p : h.getData(STORE).providers.values()) {{
                LazyOptional<T> r = p.getCapability(cap, side);
                if (r.isPresent()) return r;
            }}
        }}
        Object v = builtin(holder, cap, side);
        return v == null ? LazyOptional.empty() : LazyOptional.of(() -> (T) v);
    }}

    @Nullable
    private static Object builtin(Object holder, Capability<?> cap, @Nullable Direction side) {{
        if (holder instanceof BlockEntity be) {{
            if (be.getLevel() == null) return null;
            if (cap == ForgeCapabilities.ITEM_HANDLER) return be.getLevel().getCapability(Capabilities.ItemHandler.BLOCK, be.getBlockPos(), be.getBlockState(), be, side);
            if (cap == ForgeCapabilities.FLUID_HANDLER) return be.getLevel().getCapability(Capabilities.FluidHandler.BLOCK, be.getBlockPos(), be.getBlockState(), be, side);
            if (cap == ForgeCapabilities.ENERGY) return be.getLevel().getCapability(Capabilities.EnergyStorage.BLOCK, be.getBlockPos(), be.getBlockState(), be, side);
        }} else if (holder instanceof ItemStack stack) {{
            if (cap == ForgeCapabilities.ITEM_HANDLER) return stack.getCapability(Capabilities.ItemHandler.ITEM);
            if (cap == ForgeCapabilities.FLUID_HANDLER_ITEM || cap == ForgeCapabilities.FLUID_HANDLER) return stack.getCapability(Capabilities.FluidHandler.ITEM);
            if (cap == ForgeCapabilities.ENERGY) return stack.getCapability(Capabilities.EnergyStorage.ITEM);
        }} else if (holder instanceof Entity e) {{
            if (cap == ForgeCapabilities.ITEM_HANDLER) return side == null ? e.getCapability(Capabilities.ItemHandler.ENTITY) : e.getCapability(Capabilities.ItemHandler.ENTITY_AUTOMATION, side);
            if (cap == ForgeCapabilities.FLUID_HANDLER) return e.getCapability(Capabilities.FluidHandler.ENTITY, side);
            if (cap == ForgeCapabilities.ENERGY) return e.getCapability(Capabilities.EnergyStorage.ENTITY, side);
        }}
        return null;
    }}

    @Nullable
    private static <T> T own(Object provider, Capability<T> cap, @Nullable Direction side) {{
        return provider instanceof ICapabilityProvider p ? p.getCapability(cap, side).orElse(null) : null;
    }}

    @Nullable
    private static <T> T ownItem(ItemStack stack, Capability<T> cap) {{
        if (!(stack.getItem() instanceof ICapabilityItem ci)) return null;
        ICapabilityProvider p = ci.initCapabilities(stack, null);
        return p == null ? null : p.getCapability(cap, null).orElse(null);
    }}

    /** Every block entity and item of this mod exposes its own Forge-style capabilities to NeoForge. */
    @SuppressWarnings("unchecked")
    private static void onRegisterCapabilities(RegisterCapabilitiesEvent event) {{
        for (BlockEntityType<?> type : BuiltInRegistries.BLOCK_ENTITY_TYPE) {{
            ResourceLocation id = BuiltInRegistries.BLOCK_ENTITY_TYPE.getKey(type);
            if (id == null || !MODID.equals(id.getNamespace())) continue;
            BlockEntityType<BlockEntity> t = (BlockEntityType<BlockEntity>) type;
            event.registerBlockEntity(Capabilities.ItemHandler.BLOCK, t, (be, side) -> own(be, ForgeCapabilities.ITEM_HANDLER, side));
            event.registerBlockEntity(Capabilities.FluidHandler.BLOCK, t, (be, side) -> own(be, ForgeCapabilities.FLUID_HANDLER, side));
            event.registerBlockEntity(Capabilities.EnergyStorage.BLOCK, t, (be, side) -> own(be, ForgeCapabilities.ENERGY, side));
        }}
        for (Item item : BuiltInRegistries.ITEM) {{
            if (!(item instanceof ICapabilityItem)) continue;
            ResourceLocation id = BuiltInRegistries.ITEM.getKey(item);
            if (!MODID.equals(id.getNamespace())) continue;
            event.registerItem(Capabilities.ItemHandler.ITEM, (stack, v) -> ownItem(stack, ForgeCapabilities.ITEM_HANDLER), item);
            event.registerItem(Capabilities.FluidHandler.ITEM, (stack, v) -> ownItem(stack, ForgeCapabilities.FLUID_HANDLER_ITEM), item);
            event.registerItem(Capabilities.EnergyStorage.ITEM, (stack, v) -> ownItem(stack, ForgeCapabilities.ENERGY), item);
        }}
    }}

    /** The capabilities attached to one holder, in attach order, keyed as the mod keyed them. */
    public static final class Store {{
        final Map<ResourceLocation, ICapabilityProvider> providers = new LinkedHashMap<>();
        @SuppressWarnings({{"unchecked", "rawtypes"}})
        Store(IAttachmentHolder holder) {{
            AttachCapabilitiesEvent<Object> event = new AttachCapabilitiesEvent<>(holder);
            for (Attacher<?> a : ATTACHERS) {{
                if (a.type().isInstance(holder)) ((Consumer) a.handler()).accept(event);
            }}
            providers.putAll(event.getCapabilities());
        }}
    }}

    private static final class StoreSerializer implements IAttachmentSerializer<CompoundTag, Store> {{
        @Override
        @SuppressWarnings({{"unchecked", "rawtypes"}})
        public Store read(IAttachmentHolder holder, CompoundTag tag, HolderLookup.Provider lookup) {{
            Store store = new Store(holder);
            store.providers.forEach((key, p) -> {{
                if (p instanceof ICapabilitySerializable s && tag.contains(key.toString())) s.deserializeNBT(tag.get(key.toString()));
            }});
            return store;
        }}

        @Override
        public CompoundTag write(Store store, HolderLookup.Provider lookup) {{
            CompoundTag tag = new CompoundTag();
            store.providers.forEach((key, p) -> {{
                if (p instanceof ICapabilitySerializable<?> s) {{
                    Tag t = s.serializeNBT();
                    if (t != null) tag.put(key.toString(), t);
                }}
            }});
            return tag;
        }}
    }}
}}
"""
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src"); ap.add_argument("--modid"); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sites", type=int, default=8); ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not (a.src and a.modid):
        ap.error("--src and --modid are required")
    report, flags, changed = run(a.src, a.modid, a.dry_run)
    print(f"convert-capabilities: {changed} file(s) changed" + (" (dry run)" if a.dry_run else "")
          + "".join(f", {v} {k}" for k, v in sorted(report.items())))
    for fl in flags[:a.sites]:
        print("  left: " + fl)
    if len(flags) > a.sites:
        print(f"  ... and {len(flags) - a.sites} more")
    return 0


def self_check():
    import tempfile
    ok = True
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d) / "com/x/m"
        (root / "be").mkdir(parents=True)
        (root / "M.java").write_text(
            "package com.x.m;\n@Mod(\"m\")\npublic class M {\n   public M(IEventBus modEventBus, ModContainer c) {\n"
            "      modEventBus.addListener(this::setup);\n   }\n}\n", encoding="utf-8")
        (root / "be/Tank.java").write_text(
            "package com.x.m.be;\nimport net.neoforged.neoforge.common.capabilities.Capability;\n"
            "import net.neoforged.neoforge.common.capabilities.ForgeCapabilities;\nimport net.neoforged.neoforge.common.util.LazyOptional;\n"
            "public class Tank extends BlockEntity {\n   private final LazyOptional<IFluidHandler> holder = LazyOptional.of(() -> this.tank);\n"
            "   @Override\n   public <T> LazyOptional<T> getCapability(@Nonnull Capability<T> cap, @Nullable Direction side) {\n"
            "      return cap == ForgeCapabilities.FLUID_HANDLER ? holder.cast() : super.getCapability(cap, side);\n   }\n"
            "   @Override\n   public void invalidateCaps() {\n      super.invalidateCaps();\n      holder.invalidate();\n   }\n"
            "   void pull(BlockEntity other, Direction dir) {\n"
            "      LazyOptional<IItemHandler> c = other.getCapability(ForgeCapabilities.ITEM_HANDLER, dir.getOpposite());\n"
            "      if (((Tank)level.getBlockEntity(worldPosition)).getCapability(ForgeCapabilities.FLUID_HANDLER, null).isPresent()) {}\n"
            "   }\n}\n", encoding="utf-8")
        (root / "be/SubTank.java").write_text(
            "package com.x.m.be;\nimport net.neoforged.neoforge.common.capabilities.Capability;\nimport net.neoforged.neoforge.common.util.LazyOptional;\n"
            "public class SubTank extends Tank {\n   @Override\n   public <T> LazyOptional<T> getCapability(Capability<T> cap, Direction side) {\n"
            "      return super.getCapability(cap, side);\n   }\n}\n", encoding="utf-8")
        (root / "Data.java").write_text(
            "package com.x.m;\nimport net.minecraftforge.common.capabilities.*;\nimport net.minecraftforge.common.util.LazyOptional;\n"
            "import net.minecraftforge.event.AttachCapabilitiesEvent;\nimport net.minecraft.world.entity.Entity;\n"
            "@Mod.EventBusSubscriber(modid = \"m\")\npublic class Data implements ICapabilitySerializable<CompoundTag> {\n"
            "   public static final Capability<Data> CAP = CapabilityManager.get(new CapabilityToken<Data>() {});\n"
            "   @SubscribeEvent\n   public static void onAttach(AttachCapabilitiesEvent<Entity> e) {\n"
            "      if (e.getObject() instanceof Player) e.addCapability(new ResourceLocation(\"m\", \"d\"), new Data());\n   }\n"
            "   @Override\n   public <T> LazyOptional<T> getCapability(Capability<T> cap, Direction side) { return CAP.orEmpty(cap, LazyOptional.of(() -> this)); }\n"
            "   public CompoundTag serializeNBT() { return new CompoundTag(); }\n   public void deserializeNBT(CompoundTag t) {}\n"
            "   static void use(Player p, PlayerEvent.Clone ev) {\n      ev.getOriginal().reviveCaps();\n"
            "      p.getCapability(CAP).ifPresent(d -> {});\n   }\n}\n", encoding="utf-8")
        run(d, "m")
        tank = (root / "be/Tank.java").read_text(encoding="utf-8")
        sub = (root / "be/SubTank.java").read_text(encoding="utf-8")
        data = (root / "Data.java").read_text(encoding="utf-8")
        mod = (root / "M.java").read_text(encoding="utf-8")
        caps = (root / "compat/caps/Caps.java").read_text(encoding="utf-8")
        checks = {
            "imports": "import com.x.m.compat.caps.Capability;" in tank and "import com.x.m.compat.caps.LazyOptional;" in tank,
            "provider": "class Tank extends BlockEntity implements com.x.m.compat.caps.ICapabilityProvider" in tank
                        and "@Override\n   public <T> LazyOptional<T> getCapability" not in tank,
            "super->empty": "holder.cast() : LazyOptional.empty();" in tank,
            "mod super kept": "return super.getCapability(cap, side);" in sub,
            "lifecycle": "super.invalidateCaps();" not in tank and "public void invalidateCaps()" in tank,
            "consumer": "Caps.get(other, ForgeCapabilities.ITEM_HANDLER, dir.getOpposite())" in tank
                        and "Caps.get(((Tank)level.getBlockEntity(worldPosition)), ForgeCapabilities.FLUID_HANDLER, null)" in tank,
            "wildcard": "import com.x.m.compat.caps.*;" in data,
            "attacher": "@SubscribeEvent" not in data and "attach(net.minecraft.world.entity.Entity.class, com.x.m.Data::onAttach);" in caps,
            "r1": "EventBusSubscriber" not in data,
            "revive": "/* 1.21 MIGRATION (X146): ev.getOriginal().reviveCaps()" in data,
            "bare": "Caps.get(p, CAP).ifPresent" in data,
            "wiring": "com.x.m.compat.caps.Caps.register(modEventBus);" in mod,
            "shims": all((root / f"compat/caps/{n}.java").exists() for n in ("LazyOptional", "ForgeCapabilities", "ICapabilityItem")),
        }
        bad = [k for k, v in checks.items() if not v]
        before = {p: p.read_text(encoding="utf-8") for p in root.rglob("*.java")}
        run(d, "m")
        if any(p.read_text(encoding="utf-8") != t for p, t in before.items()):
            bad.append("idempotent")
        if bad:
            ok = False
            print("FAIL", bad, "\n" + tank + "\n" + sub + "\n" + data + "\n" + mod)
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
