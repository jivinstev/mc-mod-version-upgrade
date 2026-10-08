#!/usr/bin/env python3
"""Fix 1.21's Holder-wrapping errors, one compile round at a time, from javac's own caret positions.

    python3 tools/fix-holders.py --src src/main/java --log build.log [--dry-run] [--self-check]

1.21 wraps registry objects in Holder<T> at most API boundaries (CATALOG §42/§62/§131): an effect,
an attribute, an enchantment, a sound. A Forge 1.20 mod passes the raw object (`X.get()`) or, for
enchantments, a now-data-driven `Enchantments.FOO` ResourceKey. The fix is mechanical per site but
depends on what the offending EXPRESSION is, which only the compiler knows -- so this reads each
`incompatible types` error, takes the expression under javac's caret, and applies one rule:

  A  `[(T)] REG.FOO.get()` where a Holder<T> is wanted -> `REG.FOO`, and the registry field is declared
     DeferredHolder<Base, T> (it is a Holder and still a Supplier, so other `.get()` calls keep working).
  B  a method parameter of type T where Holder<T> is wanted -> the parameter becomes Holder<T>.
  C  a mod method's T parameter receiving a Holder<T> -> that parameter becomes Holder<T> (instead of
     unwrapping at the caller, which would only move the error inside the method).
  D  a Holder<T> where T is wanted, anything else -> `.value()`.
  E  a ResourceKey<Enchantment> (`Enchantments.FOO`) where a Holder is wanted -> `ModHolders.enchantment(..)`,
     a generated helper that resolves it against the registries of the CALLING thread (§R24: a holder
     from the other side's registry copy encodes fine and disconnects the player).
  F  a built-in-registry T value (local, field) where Holder<T> is wanted -> `BuiltInRegistries.X.wrapAsHolder(..)`.

Anything else under the caret is LISTED, not guessed. Run, recompile, run again: B and C move the
next error one call level, and stop when the types agree. Standard library only.
"""
import argparse, collections, pathlib, re, sys

HOLDER_TYPES = {"MobEffect": "MOB_EFFECT", "Attribute": "ATTRIBUTE", "Potion": "POTION", "SoundEvent": "SOUND_EVENT",
                "Enchantment": None, "EntityType<?>": "ENTITY_TYPE"}
# not ArmorMaterial: an armour material must be REGISTERED (its holder is synced to clients), so wrapping
# an anonymous one compiles and fails to encode -- that is the §115 record template's job, not a rewrite.
RETYPE_PARAMS = {"MobEffect", "Attribute", "Potion", "Enchantment"}

ERR = re.compile(r"^(?P<file>/\S+?\.java):(?P<line>\d+): error: (?P<msg>.*)$")
CONV = re.compile(r"incompatible types: (?P<a>.+?) cannot be converted to (?P<b>.+)$")

HELPER = """package {pkg};

import net.minecraft.core.Holder;
import net.minecraft.core.HolderLookup;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.ResourceKey;
import net.minecraft.server.MinecraftServer;
import net.minecraft.world.item.enchantment.Enchantment;
import net.neoforged.api.distmarker.Dist;
import net.neoforged.fml.loading.FMLEnvironment;
import net.neoforged.neoforge.server.ServerLifecycleHooks;

/**
 * Resolves the data-driven registry entries 1.21 made into keys (NeoForge port).
 * Holders come from the registries of the CALLING thread: the integrated server and the client keep
 * separate copies, and a holder from the wrong one fails to encode when the client sends it.
 */
public final class ModHolders {{
    private ModHolders() {{}}

    public static Holder<Enchantment> enchantment(ResourceKey<Enchantment> key) {{
        return registries().lookupOrThrow(Registries.ENCHANTMENT).getOrThrow(key);
    }}

    public static HolderLookup.Provider registries() {{
        MinecraftServer server = ServerLifecycleHooks.getCurrentServer();
        if (server != null && server.isSameThread()) return server.registryAccess();
        if (FMLEnvironment.dist == Dist.CLIENT) {{
            HolderLookup.Provider client = Client.registries();
            if (client != null) return client;
        }}
        if (server != null) return server.registryAccess();
        throw new IllegalStateException("no registries yet: called before a world was loaded");
    }}

    private static final class Client {{
        static HolderLookup.Provider registries() {{
            net.minecraft.client.multiplayer.ClientLevel level = net.minecraft.client.Minecraft.getInstance().level;
            return level == null ? null : level.registryAccess();
        }}
    }}
}}
"""


def _skip(text, i):
    c = text[i]
    if c in "\"'":
        j = i + 1
        while j < len(text) and text[j] != c:
            j += 2 if text[j] == "\\" else 1
        return j + 1
    return i


def close_of(text, i):
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack, j = [pairs[text[i]]], i + 1
    while j < len(text):
        k = _skip(text, j)
        if k != j:
            j = k; continue
        c = text[j]
        if c in pairs:
            stack.append(pairs[c])
        elif c in ")]}":
            if stack.pop() != c:
                return -1
            if not stack:
                return j
        j += 1
    return -1


def expr_at(line, col):
    """(start, end) of the primary expression starting at col: an optional cast, then a dotted chain of
    names, calls and indexes."""
    j = col
    m = re.match(r"\(\s*[\w.<>?]+\s*\)\s*", line[j:])
    if m and not re.match(r"\(\s*\w+\s*\)\s*[-+*/%&|^<>=!?:,;)]", line[j:]):
        j += m.end()
    m = re.match(r"(?:new\s+)?[\w$]+", line[j:])
    if not m:
        return None
    j += m.end()
    while j < len(line):
        if line[j] == "(" or line[j] == "[":
            k = close_of(line, j)
            if k < 0:
                return None
            j = k + 1
        elif re.match(r"\s*\.\s*[\w$]+", line[j:]):
            j += re.match(r"\s*\.\s*[\w$]+", line[j:]).end()
        else:
            break
    return col, j


def arg_span(line, col):
    """(start, end) of the whole expression around javac's caret: out to the nearest top-level
    `(` `,` `=` `return` on the left and `,` `)` `;` on the right. javac puts the caret on a call's
    method name, not on the expression's first character, so the expression is recovered from the
    context the error is about (an argument, an assignment, a return)."""
    depth, j = 0, col
    while j > 0:
        c = line[j - 1]
        if c in ")]}":
            depth += 1
        elif c in "([{":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and (c == "," or (c == "=" and line[j - 2:j - 1] not in "=!<>" and line[j:j + 1] != "=")
                             or c == "?" or c == ":"):
            break
        elif depth == 0 and line[:j].endswith("return "):
            break
        j -= 1
    depth, k = 0, col
    while k < len(line):
        c = line[k]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and c in ",;?":
            break
        k += 1
    s = j + len(line[j:k]) - len(line[j:k].lstrip())
    e = k - (len(line[j:k]) - len(line[j:k].rstrip()))
    return (s, e) if e > s else None


def errors(log_text):
    """Unique (file, line, msg, caret column) from a javac/gradle log."""
    lines = log_text.splitlines()
    out, seen = [], set()
    for i, l in enumerate(lines):
        m = ERR.match(l)
        if not m or (m.group("file"), m.group("line")) in seen:
            continue
        seen.add((m.group("file"), m.group("line")))
        col = None
        for k in range(i + 1, min(i + 8, len(lines))):
            if re.fullmatch(r"\s*\^\s*", lines[k]):
                col = lines[k].index("^"); break
            if ERR.match(lines[k]):
                break
        out.append((m.group("file"), int(m.group("line")), m.group("msg"), col))
    return out


def base_type(t):
    t = re.sub(r"\s+", "", t)
    m = re.fullmatch(r"(?:net\.minecraft\.[\w.]+\.)?Holder<(.+)>", t)
    return m.group(1) if m else None


class Index:
    """Registry fields and method signatures across the source tree."""

    def __init__(self, src):
        self.src = pathlib.Path(src).resolve()
        self.files = {f: f.read_text(encoding="utf-8", errors="replace") for f in self.src.rglob("*.java")}
        self.regs = {}            # DeferredRegister field name -> base type
        for t in self.files.values():
            for m in re.finditer(r"DeferredRegister(?:\.\w+)?<\s*([\w.]+)\s*>\s+(\w+)\s*=", t):
                self.regs[m.group(2)] = m.group(1).rsplit(".", 1)[-1]
            for m in re.finditer(r"DeferredRegister\.(Items|Blocks)\s+(\w+)\s*=", t):
                self.regs[m.group(2)] = m.group(1)[:-1]

    def field_decl(self, owner, name):
        """(file, match) of `<Type> name = REG.register(` in class `owner`."""
        for f, t in self.files.items():
            if owner and f.stem != owner:
                continue
            m = re.search(r"\b(Supplier|RegistryObject|DeferredHolder)<([^=;]*?)>\s+%s\s*=\s*(\w+)\s*\.\s*register\w*\("
                          % re.escape(name), t)
            if m:
                return f, m
        return None, None

    def methods_named(self, name):
        for f, t in self.files.items():
            for m in re.finditer(r"(?<![\w.])(?:static\s+)?[\w<>\[\],.? ]+\s+%s\s*\(" % re.escape(name), t):
                if re.match(r"\s*(return|new|throw|else)\b", t[m.start():]):
                    continue
                p0 = m.end() - 1
                p1 = close_of(t, p0)
                if p1 > 0 and re.match(r"\s*(throws[^{;]*)?\{", t[p1 + 1:]):
                    yield f, p0, p1


def split_args(s):
    out, depth, angle, cur = [], 0, 0, []
    j = 0
    while j < len(s):
        k = _skip(s, j)
        if k != j:
            cur.append(s[j:k]); j = k; continue
        c = s[j]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "<" and j and (s[j - 1].isalnum() or s[j - 1] == "_"):
            angle += 1
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


def call_context(text, pos):
    """(callee name, argument index) of the call whose argument list contains pos, or (None, None)."""
    depth, j = 0, pos - 1
    while j >= 0:
        c = text[j]
        if c in ")]}":
            depth += 1
        elif c in "([{":
            if depth == 0:
                if c != "(":
                    return None, None
                m = re.search(r"([\w$]+)\s*$", text[:j])
                if not m or m.group(1) in ("if", "while", "for", "switch", "catch", "return"):
                    return None, None
                idx = len(split_args(text[j + 1:pos])) - (0 if text[j + 1:pos].rstrip().endswith(",") else 1)
                idx = max(idx, 0) if text[j + 1:pos].strip() else 0
                return m.group(1), idx
            depth -= 1
        elif c == ";" and depth == 0:
            return None, None
        j -= 1
    return None, None


def run(src, log_text, dry=False):
    idx = Index(src)
    edits = collections.defaultdict(list)       # file -> [(start, end, repl)]
    report = collections.Counter()
    flags = []
    root = common_root(idx)
    helper_fqn = f"{root}.ModHolders" if root else "ModHolders"
    need_helper, need_imports = False, collections.defaultdict(set)

    def offset(f, line, col):
        t = idx.files[f]
        starts = [0] + [m.end() for m in re.finditer("\n", t)]
        return starts[line - 1] + col

    def retype_param(f, p0, p1, i, holder_of):
        t = idx.files[f]
        args = split_args(t[p0 + 1:p1])
        if i >= len(args):
            return False
        a = args[i]
        m = re.search(r"(?<![\w.<])(%s)(\s+[\w$]+\s*)$" % re.escape(holder_of), a)
        if not m:
            return False
        start = p0 + 1 + sum(len(x) + 1 for x in args[:i]) + m.start(1)
        edits[f].append((start, start + len(holder_of), f"Holder<{holder_of}>"))
        need_imports[f].add("net.minecraft.core.Holder")
        return True

    for file, line, msg, col in errors(log_text):
        f = pathlib.Path(file).resolve()
        if f not in idx.files or col is None:
            continue
        cm = CONV.search(msg)
        if not cm:
            continue
        a, b = cm.group("a").strip(), cm.group("b").strip()
        src_line = idx.files[f].splitlines()[line - 1]
        span = arg_span(src_line, col)
        if not span:
            flags.append(f"{f.name}:{line} no expression under the caret"); continue
        e = src_line[span[0]:span[1]]
        s0 = offset(f, line, span[0]); s1 = s0 + (span[1] - span[0])
        want, have = base_type(b), base_type(a)
        # E: enchantment keys
        if want == "Enchantment" and re.fullmatch(r"ResourceKey<(?:net\.[\w.]+\.)?Enchantment>", a.replace(" ", "")):
            edits[f].append((s0, s1, f"ModHolders.enchantment({e})"))
            need_imports[f].add(helper_fqn); need_helper = True; report["E enchantment key"] += 1; continue
        if want and a.split(".")[-1] == want.split(".")[-1]:
            m = re.fullmatch(r"(?:\(\s*[\w.]+\s*\)\s*)?(?:(\w+)\.)?(\w+)\s*\.\s*get\(\)", e)
            if m:                                                                    # A
                owner, name = m.group(1), m.group(2)
                df, dm = idx.field_decl(owner, name)
                if df is None:
                    flags.append(f"{f.name}:{line} {e}: no registry field declaration found"); continue
                edits[f].append((s0, s1, f"{owner + '.' if owner else ''}{name}"))
                if dm.group(1) != "DeferredHolder":
                    base = idx.regs.get(dm.group(3))
                    if not base:
                        flags.append(f"{f.name}:{line} {e}: register target {dm.group(3)} has no DeferredRegister<T>")
                        continue
                    targs = dm.group(2).strip()
                    edits[df].append((dm.start(1), dm.end(2) + 1, f"DeferredHolder<{base}, {targs}>"))
                    need_imports[df].add("net.neoforged.neoforge.registries.DeferredHolder")
                report["A drop .get()"] += 1; continue
            if re.fullmatch(r"[\w$]+", e):                                           # B
                t = idx.files[f]
                decl = list(re.finditer(r"(?<![\w.<])%s(\s+)%s\s*[,)]" % (re.escape(want), re.escape(e)), t[:s0]))
                if decl:
                    d = decl[-1]
                    edits[f].append((d.start(), d.start() + len(want), f"Holder<{want}>"))
                    need_imports[f].add("net.minecraft.core.Holder")
                    report["B retype parameter"] += 1; continue
            reg = HOLDER_TYPES.get(want)
            if reg:                                                                  # F
                edits[f].append((s0, s1, f"BuiltInRegistries.{reg}.wrapAsHolder({e})"))
                need_imports[f].add("net.minecraft.core.registries.BuiltInRegistries")
                report["F wrapAsHolder"] += 1; continue
        if have and b.split(".")[-1] == have.split(".")[-1]:
            callee, ai = call_context(idx.files[f], s0)
            done = False
            if callee and have in RETYPE_PARAMS:                                    # C
                for mf, p0, p1 in idx.methods_named(callee):
                    if retype_param(mf, p0, p1, ai, have):
                        done = True
                if done:
                    report["C retype callee parameter"] += 1; continue
            edits[f].append((s1, s1, ".value()"))                                    # D
            report["D .value()"] += 1; continue
        if "Holder" in a or "Holder" in b or "Enchantment" in a:
            flags.append(f"{f.name}:{line} {a} -> {b}: `{e}` not a shape this fixes")
    # apply
    changed = 0
    for f, es in edits.items():
        t = idx.files[f]
        seen, uniq = set(), []
        for e in sorted(es, key=lambda x: -x[0]):
            if (e[0], e[1]) in seen or any(e[0] < s1_ and e[1] > s0_ for s0_, s1_ in seen):
                continue
            seen.add((e[0], e[1])); uniq.append(e)
        for s, e2, r in uniq:
            t = t[:s] + r + t[e2:]
        for fq in sorted(need_imports[f]):
            t = add_import(t, fq)
        if t != idx.files[f]:
            changed += 1
            if not dry:
                f.write_text(t, encoding="utf-8")
    if need_helper and not dry:
        hp = pathlib.Path(src) / (helper_fqn.replace(".", "/") + ".java")
        if not hp.exists():
            hp.parent.mkdir(parents=True, exist_ok=True)
            hp.write_text(HELPER.format(pkg=helper_fqn.rsplit(".", 1)[0]), encoding="utf-8")
    return report, flags, changed


def common_root(idx):
    pkgs = [m.group(1).split(".") for t in idx.files.values() for m in [re.search(r"(?m)^package\s+([\w.]+)\s*;", t)] if m]
    if not pkgs:
        return ""
    pre = pkgs[0]
    for p in pkgs[1:]:
        k = 0
        while k < min(len(pre), len(p)) and pre[k] == p[k]:
            k += 1
        pre = pre[:k]
    return ".".join(pre)


def add_import(text, fqn):
    if re.search(r"(?m)^import\s+%s\s*;" % re.escape(fqn), text):
        return text
    pkg = (re.search(r"(?m)^package\s+([\w.]+)\s*;", text) or [None, ""])[1]
    if fqn.rsplit(".", 1)[0] == pkg or re.search(r"(?m)^import\s+[\w.]+\.%s\s*;" % re.escape(fqn.rsplit(".", 1)[1]), text):
        return text
    imps = list(re.finditer(r"(?m)^import\s+[\w.*]+\s*;[ \t]*\n", text))
    at = imps[-1].end() if imps else (re.search(r"(?m)^package[^\n]*\n", text) or re.match("", text)).end()
    return text[:at] + f"import {fqn};\n" + text[at:]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src"); ap.add_argument("--log"); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sites", type=int, default=8); ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not (a.src and a.log):
        ap.error("--src and --log are required")
    report, flags, changed = run(a.src, pathlib.Path(a.log).read_text(encoding="utf-8", errors="replace"), a.dry_run)
    print(f"fix-holders: {changed} file(s) changed, {sum(report.values())} site(s)" + (" (dry run)" if a.dry_run else ""))
    for k, v in sorted(report.items()):
        print(f"  {v:4d}  {k}")
    if flags:
        print(f"  {len(flags)} Holder-shaped error(s) left by name:")
        for s in flags[:a.sites]:
            print("      " + s)
    return 0


def self_check():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d) / "my"
        root.mkdir()
        (root / "ModEffects.java").write_text("""package my;
public class ModEffects {
    public static final DeferredRegister<MobEffect> EFFECTS = DeferredRegister.create(Registries.MOB_EFFECT, "m");
    public static final Supplier<MobEffect> PHASING = EFFECTS.register("phasing", Phasing::new);
}
""", encoding="utf-8")
        (root / "Use.java").write_text("""package my;
class Use {
    void a(LivingEntity e, ItemStack s) {
        if (!e.hasEffect((MobEffect) ModEffects.PHASING.get())) {
            s.enchant(Enchantments.MENDING, 1);
        }
        strip(e, Attributes.MAX_HEALTH);
        MobEffect eff = inst.getEffect();
    }
    void strip(LivingEntity e, Attribute attribute) {
        e.getAttribute(attribute);
    }
}
""", encoding="utf-8")
        u = (root / "Use.java").read_text().splitlines()
        def at(ln, needle):
            return u[ln - 1].index(needle)
        F = str(root / "Use.java")
        log = "\n".join([
            f"{F}:4: error: incompatible types: MobEffect cannot be converted to Holder<MobEffect>",
            u[3], " " * at(4, ".get()") + "^",
            f"{F}:5: error: incompatible types: ResourceKey<Enchantment> cannot be converted to Holder<Enchantment>",
            u[4], " " * at(5, "Enchantments") + "^",
            f"{F}:7: error: incompatible types: Holder<Attribute> cannot be converted to Attribute",
            u[6], " " * at(7, "Attributes") + "^",
            f"{F}:8: error: incompatible types: Holder<MobEffect> cannot be converted to MobEffect",
            u[7], " " * at(8, "inst") + "^",
            f"{F}:11: error: incompatible types: Attribute cannot be converted to Holder<Attribute>",
            u[10], " " * at(11, "attribute") + "^",
        ])
        report, flags, _ = run(d, log)
        out = (root / "Use.java").read_text(); eff = (root / "ModEffects.java").read_text()
        want = ["e.hasEffect(ModEffects.PHASING)", "s.enchant(ModHolders.enchantment(Enchantments.MENDING), 1)",
                "MobEffect eff = inst.getEffect().value();", "void strip(LivingEntity e, Holder<Attribute> attribute)",
                "import net.minecraft.core.Holder;"]
        miss = [w for w in want if w not in out]
        if "DeferredHolder<MobEffect, MobEffect> PHASING" not in eff:
            miss.append("field not retyped")
        if not (root / "ModHolders.java").exists():
            miss.append("helper not written")
        if "Holder<Holder<" in out:
            miss.append("double retype")
        ok = not miss and not flags
    print("self-check:", "OK" if ok else f"FAIL {miss} {flags}\n{out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
