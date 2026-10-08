#!/usr/bin/env python3
"""Convert 1.21.1 entity/block-entity persistence and `hurt` overrides to 26.2 (ValueOutput/ValueInput, hurtServer).

    python3 tools/convert-valueio.py --src src/main/java [--dry-run] [--summary-only]
    python3 tools/convert-valueio.py --self-check

Run it on a tree that is being moved to 26.2 (a 26.2-only tree, or the source an overlay is cut from). The
rewrites are NOT valid on 1.21.1, so in a two-target tree they belong in the prepared 26.2 copy, never in the
shared canonical source.

PERSISTENCE (CATALOG §V31).  Only a body that touches the tag parameter through scalar calls is converted; the
parameter NAME is kept, so every statement that is not a getter stays byte-identical:
    Entity.addAdditionalSaveData(CompoundTag t)          -> addAdditionalSaveData(ValueOutput t)
    Entity.readAdditionalSaveData(CompoundTag t)         -> readAdditionalSaveData(ValueInput t)
    BlockEntity.saveAdditional(CompoundTag t, Provider)  -> saveAdditional(ValueOutput t)     (provider GONE)
    BlockEntity.loadAdditional(CompoundTag t, Provider)  -> loadAdditional(ValueInput t)
    super.saveAdditional(t, p) / super.loadAdditional(t, p) -> super.saveAdditional(t) / super.loadAdditional(t)
  ValueOutput keeps putInt/putLong/putFloat/putDouble/putBoolean/putString/putByte/putShort/putIntArray, so
  those are untouched.  The ValueInput getters are Optional-returning or take an explicit default (§V12), so:
    t.getInt(k)      -> t.getIntOr(k, 0)               t.getLong(k)    -> t.getLongOr(k, 0L)
    t.getFloat(k)    -> t.getFloatOr(k, 0.0F)          t.getDouble(k)  -> t.getDoubleOr(k, 0.0D)
    t.getBoolean(k)  -> t.getBooleanOr(k, false)       t.getString(k)  -> t.getStringOr(k, "")
    t.getByte(k)     -> t.getByteOr(k, (byte) 0)       t.getShort(k)   -> (short) t.getShortOr(k, (short) 0)
    t.getIntArray(k) -> t.getIntArray(k).orElse(new int[0])
    t.contains(k)    -> t.keySet().contains(k)         (NeoForge's ValueInputExtension.keySet())
    t.remove(k)      -> t.discard(k)                   (save methods only)
  These defaults are exactly what the 1.21.1 getters returned for an absent key.  ANYTHING else on the tag
  (getCompound, getList, put(k, tag), UUID, ItemStack, passing the tag to a helper, using the provider, a local
  CompoundTag/ListTag) is REFUSED and left untouched: it needs the §V31 treatment (child()/list()/store(), a
  compat pair, or the NeoForge ValueOutput.store(CompoundTag) bridge), which is a design decision.

HURT (CATALOG §V38/§V71).  `public boolean hurt(DamageSource s, float a)` on a class that extends something becomes
`public boolean hurtServer(ServerLevel level, DamageSource s, float a)`, and `super.hurt(args)` becomes
`super.hurtServer(level, args)`.  The level parameter is named `level` unless the method already uses that
identifier (then serverLevel / hurtLevel).  REFUSED: any `isClientSide` in the body (the client/server split is a
judgement -- hurtClient), a self-call `hurt(...)`/`this.hurt(...)`, a mixin file, and a body calling `super.hurt`
when the class extends Entity DIRECTLY (hurtServer is abstract there; use the §V71 base-class pair).

CALL SITES.  `recv.hurt(source, amount)` is only REPORTED (never rewritten): the faithful mapping is
`hurtOrSimulate` everywhere, or `hurtServer(level, ...)` where a ServerLevel is already in scope.

Idempotent (converted methods no longer match); a method already taking ValueOutput/ValueInput is skipped.
Standard library only.
"""
import argparse, collections, importlib.util, pathlib, re, sys, tempfile

_here = pathlib.Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location("fs", _here / "forge-shapes.py")
fs = importlib.util.module_from_spec(_s); _s.loader.exec_module(fs)
_n = importlib.util.spec_from_file_location("ni", _here / "normalise-imports.py")
ni = importlib.util.module_from_spec(_n); _n.loader.exec_module(ni)

VALUE_OUT = "net.minecraft.world.level.storage.ValueOutput"
VALUE_IN = "net.minecraft.world.level.storage.ValueInput"
SERVER_LEVEL = "net.minecraft.server.level.ServerLevel"
PUTS = {"putInt", "putLong", "putFloat", "putDouble", "putBoolean", "putString", "putByte", "putShort", "putIntArray"}
GETS = {  # old getter -> (new name, default argument text or None, wrap)
    "getInt": ("getIntOr", "0"), "getLong": ("getLongOr", "0L"), "getFloat": ("getFloatOr", "0.0F"),
    "getDouble": ("getDoubleOr", "0.0D"), "getBoolean": ("getBooleanOr", "false"), "getString": ("getStringOr", '""'),
    "getByte": ("getByteOr", "(byte) 0"), "getShort": ("getShortOr", "(short) 0"),
}
NESTED = re.compile(r"(?<![\w$.])(CompoundTag|ListTag|Tag|NbtUtils|NbtOps|ItemStack|ContainerHelper|UUID|"
                    r"HolderLookup|RegistryAccess)(?![\w$])")
PROVIDER_TYPES = ("HolderLookup.Provider", "net.minecraft.core.HolderLookup.Provider", "Provider")
TAG_TYPES = ("CompoundTag", "net.minecraft.nbt.CompoundTag")
HOOKS = {  # method -> (kind, arity)
    "addAdditionalSaveData": ("save", 1), "readAdditionalSaveData": ("load", 1),
    "saveAdditional": ("save", 2), "loadAdditional": ("load", 2),
}
V31 = "needs the CATALOG §V31 treatment"


def ptype(p):
    return " ".join(w for w in re.sub(r"@\w+(?:\([^)]*\))?", "", p).split()[:-1] if w != "final")


def ident(name, text):
    return re.search(r"(?<![\w$])%s(?![\w$])" % re.escape(name), text) is not None


def ensure_import(text, fqn):
    """Add the import; return (text, usable-simple-name?).  A clashing import means the caller stays qualified."""
    simple = fqn.rsplit(".", 1)[1]
    if re.search(r"(?m)^import\s+%s\s*;" % re.escape(fqn), text):
        return text, True
    if re.search(r"(?m)^import\s+[\w.]+\.%s\s*;" % simple, text):
        return text, False
    return ni.add_import(text, fqn), True


def enclosing_class_super(masked, pos):
    """Simple name of the superclass of the innermost class containing pos, or None (no extends / not found)."""
    best = None
    for m in re.finditer(r"(?<![\w$.])class\s+\w+(?:\s*<[^{]*?>)?\s*(?:extends\s+([\w.$]+)(?:\s*<[^{]*?>)?)?"
                         r"(?:\s+implements\s+[^{]*)?\s*\{", masked):
        ob = m.end() - 1
        cb = fs.match(masked, ob)
        if ob < pos < cb:
            best = m
    if best is None:
        return None, False
    sup = best.group(1)
    return (sup.rsplit(".", 1)[-1] if sup else None), True


# --------------------------------------------------------------------------- persistence

def convert_persistence(text, m, kind, arity):
    """Return (new_text, None) or (None, (reason-key, detail))."""
    params = m.params
    ptypes = [ptype(p) for p in params]
    names = [fs.pname(p) for p in params]
    if len(params) != arity or ptypes[0] not in TAG_TYPES:
        return None, None                                           # not a 1.21.1 shape (maybe already converted)
    if arity == 2 and ptypes[1] not in PROVIDER_TYPES:
        return None, None
    t = names[0]
    prov = names[1] if arity == 2 else None
    open_, close = m.body_open + 1, m.body_close
    body = text[open_:close]
    masked = ni.code_spans(body)
    mm = NESTED.search(masked)
    if mm:
        return None, ("nested", f"`{mm.group(1)}` in the body; {V31}")
    edits = []                                                      # (start, end, replacement) relative to body
    handled = []                                                    # spans of rewritten super calls
    for sm in re.finditer(r"(?<![\w$.])super\s*\.\s*(\w+)\s*\(", masked):
        if sm.group(1) != m.name:
            continue
        pc = fs.match(masked, sm.end() - 1)
        args = [a.strip() for a in fs.split_args(body[sm.end():pc])]
        if args != names:
            return None, ("super-call", f"super.{m.name}({', '.join(args)}) is not the plain forward; {V31}")
        if arity == 2:
            edits.append((sm.end(), pc, t))
        handled.append((sm.start(), pc + 1))
    in_handled = lambda i: any(a <= i < b for a, b in handled)
    if prov:
        for pm in re.finditer(r"(?<![\w$.])%s(?![\w$])" % re.escape(prov), masked):
            if not in_handled(pm.start()):
                return None, ("provider", f"HolderLookup provider `{prov}` is used in the body; ValueOutput carries none, "
                                          f"ValueInput.lookup() does -- {V31}")
    for om in re.finditer(r"(?<![\w$.])%s(?![\w$])" % re.escape(t), masked):
        if in_handled(om.start()):
            continue
        cm = re.match(r"\s*\.\s*(\w+)\s*\(", masked[om.end():])
        if not cm:
            return None, ("passed", f"tag `{t}` is used as a value (passed on / assigned), not through a scalar call; {V31}")
        name = cm.group(1)
        po = om.end() + cm.end() - 1
        pc = fs.match(masked, po)
        args = [a.strip() for a in fs.split_args(body[po + 1:pc])]
        nstart = om.end() + masked[om.end():].index(name)
        if kind == "save":
            if name in PUTS and len(args) == 2:
                continue
            if name == "remove" and len(args) == 1:
                edits.append((nstart, nstart + len("remove"), "discard"))
                continue
            return None, ("save-call", f"`{t}.{name}(...)` has no scalar ValueOutput form; {V31}")
        if name in GETS and len(args) == 1:
            new, dflt = GETS[name]
            call = f"{new}({args[0]}, {dflt})"
            if name == "getShort":
                edits.append((om.end() - len(t), pc + 1, f"(short) {t}.{call}"))   # getShortOr returns int
            else:
                edits.append((nstart, pc + 1, call))
        elif name == "getIntArray" and len(args) == 1:
            edits.append((pc + 1, pc + 1, ".orElse(new int[0])"))
        elif name == "contains" and len(args) == 1:
            edits.append((nstart, nstart + len("contains"), "keySet().contains"))
        elif name == "contains":
            return None, ("contains-type", f"`{t}.contains(k, type)` is type-sensitive; {V31}")
        else:
            return None, ("load-call", f"`{t}.{name}(...)` has no scalar ValueInput form; {V31}")
    edits.sort()
    for (a, b, _), (c, d, _) in zip(edits, edits[1:]):
        if c < b:
            return None, ("nested-access", f"a tag access sits inside another tag access's arguments; do it by hand")
    new_body = body
    for a, b, r in sorted(edits, key=lambda e: -e[0]):
        new_body = new_body[:a] + r + new_body[b:]
    # signature: swap the type token, drop the provider parameter (offsets are of the ORIGINAL text; the
    # import is added to the rebuilt result afterwards)
    p0 = m.params_open + 1
    raw = text[p0:m.params_close]
    first_at = raw.index(m.params[0])
    tname = VALUE_OUT if kind == "save" else VALUE_IN
    _, short = ensure_import(text, tname)
    new_first = m.params[0].replace(ptypes[0], tname.rsplit(".", 1)[1] if short else tname, 1)
    tail = "" if arity == 2 else raw[first_at + len(m.params[0]):]
    new_params = raw[:first_at] + new_first + tail
    out = text[:p0] + new_params + text[m.params_close:open_] + new_body + text[close:]
    out, _ = ensure_import(out, tname)
    return out, None


def drop_unused_imports(text):
    for fq in ("net.minecraft.nbt.CompoundTag", "net.minecraft.core.HolderLookup", "net.minecraft.core.HolderLookup.Provider"):
        text = fs.remove_import_if_unused(text, fq)
    return text


# --------------------------------------------------------------------------- hurt

def convert_hurt(text, m, masked_text):
    if len(m.params) != 2 or [ptype(p) for p in m.params] != ["DamageSource", "float"]:
        if len(m.params) == 2 and ptype(m.params[0]).endswith("DamageSource") and ptype(m.params[1]) == "float":
            pass
        else:
            return None, None
    if m.ret.split()[-1] != "boolean":
        return None, None
    sup, found = enclosing_class_super(masked_text, m.start)
    if not found or sup is None:
        return None, None
    if "@Mixin" in text:
        return None, ("mixin", "file is a mixin; the injected handler is not an override -- leave to the audit")
    open_, close = m.body_open + 1, m.body_close
    body = text[open_:close]
    masked = ni.code_spans(body)
    if ident("isClientSide", masked):
        return None, ("client-split", "body reads isClientSide: the server/client split (hurtServer vs hurtClient) is a judgement (§V38)")
    if re.search(r"(?<![\w$.])(?:this\s*\.\s*)?hurt\s*\(", masked):
        return None, ("self-call", "body calls hurt(...) on itself; the callee's shape decides (hurtOrSimulate vs hurtServer)")
    supers = list(re.finditer(r"(?<![\w$.])super\s*\.\s*hurt\s*\(", masked))
    if supers and sup == "Entity":
        return None, ("direct-entity-super", "super.hurt on a DIRECT Entity subclass: hurtServer is abstract there, "
                                             "use the §V71 per-target base class (isInvulnerableToBase + markHurt)")
    scope = " ".join(m.params) + " " + masked
    lv = next((n for n in ("level", "serverLevel", "hurtLevel") if not ident(n, scope)), None)
    if lv is None:
        return None, ("no-free-name", "level/serverLevel/hurtLevel are all taken in the method")
    edits = []
    for sm in supers:
        nm = masked.index("hurt", sm.start())
        po = sm.end()
        edits.append((nm, po, "hurtServer(" + lv + ", "))
    new_body = body
    for a, b, r in sorted(edits, key=lambda e: -e[0]):
        new_body = new_body[:a] + r + new_body[b:]
    header = text[m.start:m.params_open]
    k = header.rindex("hurt")
    header = header[:k] + "hurtServer" + header[k + 4:]
    text2, short = ensure_import(text, SERVER_LEVEL)
    stype = "ServerLevel" if short else SERVER_LEVEL
    # rebuild from the ORIGINAL pieces; ensure_import is re-run on the result so offsets stay valid
    out = (text[:m.start] + header + "(" + f"{stype} {lv}, " + text[m.params_open + 1:open_] + new_body + text[close:])
    out, _ = ensure_import(out, SERVER_LEVEL)
    return out, None


# --------------------------------------------------------------------------- driver

def call_sites(text):
    """Report-only: recv.hurt(<damage-source-looking>, amount) calls (not super.hurt)."""
    masked = ni.code_spans(text)
    out = []
    ms = fs.methods(text)
    for m in re.finditer(r"(?<![\w$])(?!super\b)([\w$.()]+?)\s*\.\s*hurt\s*\(", masked):
        po = m.end() - 1
        pc = fs.match(masked, po)
        if pc < 0:
            continue
        args = [a.strip() for a in fs.split_args(text[po + 1:pc])]
        if len(args) != 2 or re.fullmatch(r"[\d.]+[fFdDlL]?", args[0]):
            continue
        enc = [x for x in ms if x.body_open < m.start() < x.body_close]
        scope = text[enc[-1].start:enc[-1].body_close] if enc else ""
        has_level = bool(re.search(r"ServerLevel\s+\w+|\(\s*ServerLevel\s*\)|instanceof\s+ServerLevel", ni.code_spans(scope)))
        out.append((fs.line_of(text, m.start()), m.group(0).strip().rstrip("("), has_level))
    return out


def convert_text(text):
    """-> (new_text, converted: Counter, refused: [(line, key, detail)], sites: [(line, expr, level_in_scope)])"""
    conv, refused = collections.Counter(), []
    sites = call_sites(text)
    done = set()
    skipped_value = 0
    while True:
        ms = sorted(fs.methods(text), key=lambda x: x.start)
        masked_text = ni.code_spans(text)
        changed = False
        for idx in reversed(range(len(ms))):
            if idx in done:
                continue
            m = ms[idx]
            res = None
            if m.name in HOOKS and ptype(m.params[0]) if m.params else False:
                kind, arity = HOOKS[m.name]
                res = convert_persistence(text, m, kind, arity)
                what = "persistence"
            elif m.name == "hurt":
                res = convert_hurt(text, m, masked_text)
                what = "hurt"
            else:
                done.add(idx)
                continue
            done.add(idx)
            new, why = res
            if new is not None:
                text = new
                conv[what] += 1
                changed = True
                break
            if why:
                refused.append((fs.line_of(text, m.start), why[0], f"{m.name}: {why[1]}"))
        if not changed:
            break
    if conv:
        text = drop_unused_imports(text)
    return text, conv, sorted(set(refused)), sites


def run(src, dry=False, quiet=False):
    total, refused_all, site_all = collections.Counter(), [], []
    for f in sorted(pathlib.Path(src).rglob("*.java")):
        t = f.read_text(encoding="utf-8")
        if not re.search(r"\b(?:addAdditionalSaveData|readAdditionalSaveData|saveAdditional|loadAdditional|hurt)\s*\(", t):
            continue
        out, conv, refused, sites = convert_text(t)
        total.update(conv)
        refused_all += [(f, *r) for r in refused]
        site_all += [(f, *s) for s in sites]
        if out != t and not dry:
            f.write_text(out, encoding="utf-8")
    return total, refused_all, site_all


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src"); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--summary-only", action="store_true"); ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not a.src:
        ap.error("--src is required")
    total, refused, sites = run(a.src, a.dry_run)
    print(f"convert-valueio: converted {total['persistence']} persistence method(s), {total['hurt']} hurt override(s)"
          + (" (dry run)" if a.dry_run else ""))
    reasons = collections.Counter(k for _, _, k, _ in refused)
    print(f"  refused {len(refused)}: " + (", ".join(f"{k} {n}" for k, n in reasons.most_common()) or "none"))
    withlvl = sum(1 for s in sites if s[3])
    print(f"  hurt() call sites (report only, hurtOrSimulate / hurtServer): {len(sites)}, {withlvl} with a ServerLevel in scope")
    if not a.summary_only:
        for f, line, key, detail in refused:
            print(f"  REFUSE {f}:{line}: {detail}")
        for f, line, expr, lvl in sites:
            print(f"  SITE   {f}:{line}: {expr}.hurt(...)" + (" [ServerLevel in scope]" if lvl else ""))
    return 0


# --------------------------------------------------------------------------- self-check

FIXTURE = '''package demo;

import net.minecraft.core.HolderLookup;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.world.damagesource.DamageSource;

class Thing extends Mob {
    private int count;
    @Override
    public void addAdditionalSaveData(CompoundTag tag) {
        super.addAdditionalSaveData(tag);
        tag.putInt("Count", this.count);
        tag.remove("Old");
    }

    @Override
    public void readAdditionalSaveData(CompoundTag tag) {
        super.readAdditionalSaveData(tag);
        this.count = tag.getInt("Count");
        if (tag.contains("Name")) { this.name = tag.getString("Name"); }
        short s = tag.getShort("S");
        int[] a = tag.getIntArray("A");
        boolean b = tag.getBoolean("B") && tag.getFloat("F") > 0;
    }

    @Override
    public void addAdditionalSaveData(CompoundTag t2) {
        CompoundTag inner = new CompoundTag();
        t2.put("X", inner);
    }

    @Override
    public void readAdditionalSaveData(CompoundTag t3) {
        Helper.read(t3);
    }

    @Override
    public boolean hurt(DamageSource source, float amount) {
        if (source.isFire()) return false;
        return super.hurt(source, amount);
    }
}

class Be extends BlockEntity {
    @Override
    protected void saveAdditional(CompoundTag tag, HolderLookup.Provider registries) {
        super.saveAdditional(tag, registries);
        tag.putString("K", "v");
    }

    @Override
    protected void loadAdditional(CompoundTag tag, HolderLookup.Provider registries) {
        super.loadAdditional(tag, registries);
        this.v = tag.getString("K");
    }
}

class Be2 extends BlockEntity {
    @Override
    protected void loadAdditional(CompoundTag tag, HolderLookup.Provider registries) {
        this.v = tag.getInt("K");
        this.stack = Parse.of(registries);
    }
}

class Clashy extends Mob {
    @Override
    public boolean hurt(DamageSource s, float a) {
        Level level = this.level();
        return super.hurt(s, a * 2);
    }
}

class Split extends Mob {
    @Override
    public boolean hurt(DamageSource s, float a) {
        if (this.level().isClientSide) return false;
        return super.hurt(s, a);
    }
}

class Direct extends Entity {
    @Override
    public boolean hurt(DamageSource s, float a) {
        return super.hurt(s, a);
    }
}

class Plain {
    public boolean hurt(DamageSource s, float a) { return true; }

    void go(Mob m, ServerLevel lv, DamageSource src) {
        m.hurt(src, 1.0F);
        other.hurt(3, 4);
    }
}
'''


def self_check():
    out, conv, refused, sites = convert_text(FIXTURE)
    want = [
        "public void addAdditionalSaveData(ValueOutput tag) {\n        super.addAdditionalSaveData(tag);\n        tag.putInt(\"Count\", this.count);\n        tag.discard(\"Old\");",
        "public void readAdditionalSaveData(ValueInput tag) {",
        "this.count = tag.getIntOr(\"Count\", 0);",
        "if (tag.keySet().contains(\"Name\")) { this.name = tag.getStringOr(\"Name\", \"\"); }",
        "short s = (short) tag.getShortOr(\"S\", (short) 0);",
        "int[] a = tag.getIntArray(\"A\").orElse(new int[0]);",
        "boolean b = tag.getBooleanOr(\"B\", false) && tag.getFloatOr(\"F\", 0.0F) > 0;",
        "protected void saveAdditional(ValueOutput tag) {\n        super.saveAdditional(tag);",
        "protected void loadAdditional(ValueInput tag) {\n        super.loadAdditional(tag);\n        this.v = tag.getStringOr(\"K\", \"\");",
        "public boolean hurtServer(ServerLevel level, DamageSource source, float amount) {",
        "return super.hurtServer(level, source, amount);",
        "public boolean hurtServer(ServerLevel serverLevel, DamageSource s, float a) {\n        Level level",
        "return super.hurtServer(serverLevel, s, a * 2);",
        "import net.minecraft.world.level.storage.ValueOutput;", "import net.minecraft.world.level.storage.ValueInput;",
        "import net.minecraft.server.level.ServerLevel;",
        # refused / untouched
        "public void addAdditionalSaveData(CompoundTag t2) {", "public void readAdditionalSaveData(CompoundTag t3) {",
        "protected void loadAdditional(CompoundTag tag, HolderLookup.Provider registries) {\n        this.v = tag.getInt(\"K\");",
        "public boolean hurt(DamageSource s, float a) {\n        if (this.level().isClientSide)",
        "class Direct extends Entity {\n    @Override\n    public boolean hurt(DamageSource s, float a) {",
        "class Plain {\n    public boolean hurt(DamageSource s, float a) { return true; }",
    ]
    miss = [w for w in want if w not in out]
    # the CompoundTag / HolderLookup imports must stay: refused methods still use them
    if "import net.minecraft.nbt.CompoundTag;" not in out:
        miss.append("CompoundTag import dropped while a refused method uses it")
    keys = sorted(k for _, k, _ in refused)
    if keys != sorted(["nested", "passed", "provider", "client-split", "direct-entity-super"]):
        miss.append(f"refusal keys {keys}")
    if conv["persistence"] != 4 or conv["hurt"] != 2:
        miss.append(f"counts {dict(conv)}")
    if [(s[1], s[2]) for s in sites] != [("m.hurt", True)]:
        miss.append(f"call sites {sites}")
    again = convert_text(out)
    if again[0] != out or again[1]:
        miss.append("not idempotent")
    # a tree where every method converts: the CompoundTag/HolderLookup imports are dropped
    small = ('package d;\n\nimport net.minecraft.core.HolderLookup;\nimport net.minecraft.nbt.CompoundTag;\n\n'
             'class B extends BlockEntity {\n    @Override\n    protected void saveAdditional(CompoundTag t, HolderLookup.Provider p) {\n'
             '        super.saveAdditional(t, p);\n        t.putInt("a", 1);\n    }\n}\n')
    o2 = convert_text(small)[0]
    if "CompoundTag" in o2 or "HolderLookup" in o2 or "import net.minecraft.world.level.storage.ValueOutput;" not in o2:
        miss.append(f"imports not tidied:\n{o2}")
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "Thing.java"
        p.write_text(FIXTURE, encoding="utf-8")
        run(d, dry=True)
        if p.read_text(encoding="utf-8") != FIXTURE:
            miss.append("--dry-run wrote a file")
        run(d)
        if p.read_text(encoding="utf-8") != out:
            miss.append("run() differs from convert_text")
    print("self-check:", "OK" if not miss else "FAIL " + "\n".join(map(str, miss)) + "\n" + out)
    return 0 if not miss else 1


if __name__ == "__main__":
    sys.exit(main())
