#!/usr/bin/env python3
"""Make a Forge 1.20.1 access transformer valid for NeoForge: official names, and override-aware.

    python3 tools/fix-access-transformer.py --work <gradle project> [--srg-map srg2official.json]
                                            [--overrides-from <compile log>]

Two things break when a mod's own SOURCE (not a decompile) carries its Forge access transformer across:

  1. Forge 1.20.1 ATs name members by SRG id (`m_6475_`, `f_19853_`). NeoForge runs on Mojang's official
     names, so those lines match nothing. --srg-map rewrites them with the same mapping the Java remap uses
     (tools/srg-remap/build_mapping.py). A decompile never carries an AT in that form, which is why the
     Java remap alone was enough until source-first ports.
  2. An entry that widens a METHOD vanilla subclasses override (`public-f LivingEntity actuallyHurt`) makes
     NeoForge's recompile of Minecraft fail: each `protected` override now "assigns weaker access". ForgeGradle
     tolerated it. --overrides-from reads those errors out of a compile log and widens the same method on
     each overriding class -- the faithful fix, since the author wanted the base method public. Run the
     compile again afterwards; repeat until the log has none (a subclass of a subclass appears one round
     later).

Never removes an entry. Prints what it changed. Standard library only.
"""
import argparse, json, pathlib, re, sys

OVERRIDE = re.compile(r"(?P<full>\S*/transformed/(?P<path>net/(?:minecraft|neoforged)/\S+?)\.java):(?P<line>\d+): error: (?P<m>[\w$]+)\([^)]*\) in [\w$.]+ "
                      r"cannot override (?P=m)\([^)]*\) in (?P<base>[\w$.]+)")
# NeoForge's own classes are recompiled in the same step (a widened SpawnEggItem method breaks NeoForge's
# DeferredSpawnEggItem override), so their overrides need widening exactly like Minecraft's.
# ModDevGradle (the only toolchain for 26.x) reports the same javac error with the class's FQN instead of a path
OVERRIDE_MDG = re.compile(r"ERROR Line: \d+, (?P<m>[\w$]+)\([^)]*\) in (?P<fqn>net\.(?:minecraft|neoforged)\.[\w$.]+) "
                          r"cannot override (?P=m)\([^)]*\) in (?P<base>[\w$.]+)")


def remap_classes(text, moves):
    """Rewrite moved class names in AT entries: the owner column (dotted, $ for nested types) and the
    L...; references inside method descriptors. A version hop that moves packages (1.21.1 -> 26.x moved
    hundreds) otherwise leaves every entry for a moved class pointing at nothing."""
    slash = {a.replace(".", "/"): b.replace(".", "/") for a, b in moves.items()}
    n = 0

    def owner(m):
        nonlocal n
        cls = m.group(2)
        outer, _, inner = cls.partition("$")
        if outer in moves:
            n += 1
            return m.group(1) + moves[outer] + ("$" + inner if inner else "")
        return m.group(0)

    def desc(m):
        nonlocal n
        outer, _, inner = m.group(1).partition("$")
        if outer in slash:
            n += 1
            return "L" + slash[outer] + ("$" + inner if inner else "") + ";"
        return m.group(0)
    out = []
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("#"):
            out.append(line); continue
        line = re.sub(r"^(\s*[\w-]+\s+)([\w.$]+)", owner, line, count=1)
        line = re.sub(r"L([\w/$]+);", desc, line)
        out.append(line)
    return "".join(out), n


def at_files(work):
    return sorted(pathlib.Path(work).glob("src/*/resources/META-INF/accesstransformer.cfg"))


def remap_srg(text, mapping):
    n = 0

    def sub(m):
        nonlocal n
        v = mapping.get(m.group(0))
        if v:
            n += 1
            return v
        return m.group(0)
    return re.sub(r"\b[fm]_\d+_\b", sub, text), n


def entries(text):
    """(access, class fqn, member, descriptor or '') for every entry line."""
    out = []
    for line in text.splitlines():
        s = line.split("#", 1)[0].strip()
        p = s.split()
        if len(p) >= 3:
            m = re.match(r"([\w$<>]+)(\(.*)?$", p[2])
            if m:
                out.append((p[0], p[1], m.group(1), m.group(2) or ""))
    return out


PRIM = {"void": "V", "boolean": "Z", "byte": "B", "char": "C", "short": "S", "int": "I", "long": "J", "float": "F",
        "double": "D"}


def source_desc(path, line, member, base_desc):
    """The OVERRIDING method's own descriptor, read from the recompiled source javac named. A subclass that overrides a
    generic method (`scale(AbstractClientPlayer, ..)` for `scale(T, ..)`) has a narrower descriptor than the base; an
    AT line written with the base's descriptor widens only the compiler's bridge method, and the same errors come back
    (X90). The return type is taken from the base (covariant returns are rare here). -> descriptor, or None"""
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = text.split("\n")
    chunk = "\n".join(lines[int(line) - 1:int(line) + 6])
    m = re.search(r"\b" + re.escape(member) + r"\s*\(([^)]*)\)", chunk)
    if not m:
        return None
    pkg = (re.search(r"^package\s+([\w.]+);", text, re.M) or [None, ""])[1]
    imports = {i.rsplit(".", 1)[-1]: i for i in re.findall(r"^import\s+([\w.]+);", text, re.M)}
    bounds = {}
    for tp in re.findall(r"\b(?:class|interface)\s+\w+\s*<(.*?)>\s*(?:extends|implements|\{)", text):
        depth, cur, parts = 0, "", []
        for ch in tp:
            depth += ch == "<"; depth -= ch == ">"
            if ch == "," and depth == 0:
                parts.append(cur); cur = ""
            else:
                cur += ch
        for p_ in parts + [cur]:
            bm = re.match(r"\s*(\w+)(?:\s+extends\s+([\w.]+))?", p_)
            if bm:
                bounds[bm.group(1)] = bm.group(2) or "Object"

    def fq(t):
        t = bounds.get(t, t)
        if t in PRIM:
            return PRIM[t]
        head, _, rest = t.partition(".")
        if head in imports:
            name = imports[head] + ("$" + rest.replace(".", "$") if rest else "")
        elif t in ("String", "Object", "Integer", "Long", "Boolean", "Float", "Double", "Class", "Iterable"):
            name = "java.lang." + t
        elif "." in t and t[0].islower():
            name = t
        else:
            name = (pkg + "." if pkg else "") + t.replace(".", "$")
        return "L" + name.replace(".", "/") + ";"

    out = []
    for param in [x for x in re.split(r",(?![^<]*>)", m.group(1)) if x.strip()]:
        param = re.sub(r"@[\w.]+(?:\([^)]*\))?\s*", "", param).replace("final ", "").strip()
        t = re.sub(r"<.*>", "", param.rsplit(None, 1)[0] if " " in param else param).strip()
        dims = t.count("[]") + (1 if t.endswith("...") else 0)
        t = t.replace("[]", "").replace("...", "").strip()
        out.append("[" * dims + fq(t))
    return "(" + "".join(out) + ")" + base_desc.rsplit(")", 1)[1]


def override_lines(log_text, text):
    """New entries widening each overriding subclass's method, from 'cannot override' errors."""
    have = {(c, m, d) for _a, c, m, d in entries(text)}
    adds = []
    hits = [(e.group("path").replace("/", "."), e) for e in OVERRIDE.finditer(log_text)]
    hits += [(e.group("fqn"), e) for e in OVERRIDE_MDG.finditer(log_text)]
    # `public a.B *()` widens EVERY method of B: answer each conflicting subclass with the same wildcard (X92)
    wild = {p[1].split(".")[-1].split("$")[-1]: p[1] for p in (l.split("#", 1)[0].split() for l in text.splitlines())
            if len(p) >= 3 and p[2] == "*()" and p[0].startswith("public")}
    for sub_cls, e in hits:
        base_simple = e.group("base").split(".")[-1]
        if base_simple in wild:
            if (sub_cls, "*", "()") not in have:
                have.add((sub_cls, "*", "()"))
                adds.append(f"public {sub_cls} *() # overrides methods of {wild[base_simple]}, widened there by wildcard")
            continue
        for _a, cls, member, desc in entries(text):
            if member == e.group("m") and desc and cls.split(".")[-1].split("$")[-1] == base_simple:
                full = e.groupdict().get("full")
                own = source_desc(full, e.group("line"), member, desc) if full else None
                for d in dict.fromkeys([own or desc, desc]):     # the method itself, and its bridge
                    key = (sub_cls, member, d)
                    if key not in have:
                        have.add(key)
                        adds.append(f"public {sub_cls} {member}{d} # overrides {cls}.{member}, widened there")
                break
    return adds


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work"); ap.add_argument("--srg-map"); ap.add_argument("--overrides-from")
    ap.add_argument("--class-map", help="old<TAB>new class moves (tools/build-class-move-map.py output)")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    files = at_files(a.work)
    if not files:
        print("fix-access-transformer: no accesstransformer.cfg; nothing to do"); return 0
    mapping = json.loads(pathlib.Path(a.srg_map).read_text(encoding="utf-8")) if a.srg_map else {}
    log_text = pathlib.Path(a.overrides_from).read_text(encoding="utf-8", errors="replace") if a.overrides_from else ""
    moves = {}
    if a.class_map:
        for l in pathlib.Path(a.class_map).read_text(encoding="utf-8").splitlines():
            if "\t" in l and not l.startswith("#"):
                x, y = l.split("\t")[:2]
                moves[x.strip()] = y.strip()
    for f in files:
        text = f.read_text(encoding="utf-8")
        n = 0
        if mapping:
            text, n = remap_srg(text, mapping)
        moved = 0
        if moves:
            text, moved = remap_classes(text, moves)
        adds = override_lines(log_text, text) if log_text else []
        if adds:
            text = text.rstrip("\n") + "\n" + "\n".join(adds) + "\n"
        f.write_text(text, encoding="utf-8")
        left = len(re.findall(r"\b[fm]_\d+_\b", text))
        print(f"fix-access-transformer: {f.relative_to(a.work)}: {n} SRG name(s) remapped"
              + (f", {left} left unmapped" if left else "") + (f", {moved} moved class name(s) rewritten" if moves else "") + f", {len(adds)} override widening(s) added")
    return 0


def self_check():
    at = ("public-f net.minecraft.world.entity.LivingEntity m_6475_(Lnet/minecraft/world/damagesource/DamageSource;F)V\n"
          "public net.minecraft.world.entity.Entity f_19853_\n")
    t, n = remap_srg(at, {"m_6475_": "actuallyHurt", "f_19853_": "level"})
    ok = n == 2 and "actuallyHurt(Lnet" in t and "Entity level" in t
    log = ("/x/steps/transformSource/transformed/net/minecraft/world/entity/player/Player.java:1025: error: "
           "actuallyHurt(DamageSource,float) in Player cannot override actuallyHurt(DamageSource,float) in LivingEntity\n"
           "  attempting to assign weaker access privileges; was public\n")
    adds = override_lines(log, t)
    ok &= adds == ["public net.minecraft.world.entity.player.Player actuallyHurt(Lnet/minecraft/world/damagesource/"
                   "DamageSource;F)V # overrides net.minecraft.world.entity.LivingEntity.actuallyHurt, widened there"]
    ok &= override_lines(log, t + "\n" + adds[0]) == []       # idempotent
    mv = {"net.minecraft.world.entity.animal.horse.AbstractHorse": "net.minecraft.world.entity.animal.equine.AbstractHorse",
          "net.minecraft.world.entity.vehicle.Boat": "net.minecraft.world.entity.vehicle.boat.Boat"}
    at2 = ("public net.minecraft.world.entity.animal.horse.AbstractHorse m(Lnet/minecraft/world/entity/vehicle/Boat;)V\n"
           "public net.minecraft.world.entity.animal.horse.AbstractHorse$Inner f\n# net.minecraft.world.entity.vehicle.Boat kept\n")
    r2, k = remap_classes(at2, mv)
    ok &= k == 3 and "animal.equine.AbstractHorse m(Lnet/minecraft/world/entity/vehicle/boat/Boat;)V" in r2 \
        and "equine.AbstractHorse$Inner f" in r2 and "# net.minecraft.world.entity.vehicle.Boat kept" in r2
    mdg = (" ERROR Line: 929, actuallyHurt(net.minecraft.world.damagesource.DamageSource,float) in "
           "net.minecraft.world.entity.animal.equine.AbstractHorse cannot override actuallyHurt(net.minecraft.world.damagesource."
           "DamageSource,float) in net.minecraft.world.entity.LivingEntity\n")
    ok &= override_lines(mdg, t)[0].startswith("public net.minecraft.world.entity.animal.equine.AbstractHorse actuallyHurt(")
    neo = ("/x/transformed/net/neoforged/neoforge/common/DeferredSpawnEggItem.java:56: error: getDefaultType() in "
           "DeferredSpawnEggItem cannot override getDefaultType() in SpawnEggItem\n")
    egg = "public net.minecraft.world.item.SpawnEggItem getDefaultType()Lnet/minecraft/world/entity/EntityType;\n"
    ok &= override_lines(neo, egg) == ["public net.neoforged.neoforge.common.DeferredSpawnEggItem getDefaultType()"
                                       "Lnet/minecraft/world/entity/EntityType; # overrides net.minecraft.world.item."
                                       "SpawnEggItem.getDefaultType, widened there"]
    adds3 = override_lines("/x/transformed/net/minecraft/world/entity/vehicle/ChestBoat.java:47: error: getMaxPassengers() in "
                           "ChestBoat cannot override getMaxPassengers() in Boat\n"
                           "/x/transformed/net/minecraft/world/entity/vehicle/ChestBoat.java:57: error: tick() in "
                           "ChestBoat cannot override tick() in Boat\n",
                           "public net.minecraft.world.entity.vehicle.Boat *()\n")
    ok = ok and adds3 == ["public net.minecraft.world.entity.vehicle.ChestBoat *() # overrides methods of "
                          "net.minecraft.world.entity.vehicle.Boat, widened there by wildcard"]
    import tempfile
    with tempfile.TemporaryDirectory() as d:    # a generic override: the subclass's OWN descriptor, plus the bridge
        f = pathlib.Path(d, "steps/transformSource/transformed/net/minecraft/client/r/HorseR.java")
        f.parent.mkdir(parents=True)
        f.write_text("package net.minecraft.client.r;\nimport net.minecraft.world.entity.animal.horse.AbstractHorse;\n"
                     "public class HorseR<T extends AbstractHorse, M> extends LivingR<T, M> {\n"
                     "    protected void scale(T e, PoseStack p, float f) {}\n}\n", encoding="utf-8")
        pathlib.Path(d, "steps/transformSource/transformed/net/minecraft/client/r/PoseStack.java").write_text("", encoding="utf-8")
        log2 = f"{f}:4: error: scale(T#1,PoseStack,float) in HorseR cannot override scale(T#2,PoseStack,float) in LivingR\n"
        at2 = "public net.minecraft.client.r.LivingR scale(Lnet/minecraft/world/entity/LivingEntity;Lcom/mojang/blaze3d/vertex/PoseStack;F)V\n"
        adds2 = override_lines(log2, at2)
        ok = ok and any("HorseR scale(Lnet/minecraft/world/entity/animal/horse/AbstractHorse;Lnet/minecraft/client/r/PoseStack;F)V" in a
                        for a in adds2) and any("HorseR scale(Lnet/minecraft/world/entity/LivingEntity;" in a for a in adds2)
    print("self-check:", "OK" if ok else f"FAIL {adds}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
