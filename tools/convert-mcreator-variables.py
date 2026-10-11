#!/usr/bin/env python3
"""Convert MCreator's player-variables CAPABILITY to a NeoForge data attachment, keeping every call site.

    python3 tools/convert-mcreator-variables.py --src src/main/java --modid <modid> [--dry-run] [--self-check]

Every MCreator mod with player variables carries the same generated `<Mod>Variables` class (CATALOG X100):

    public static final Capability<PlayerVariables> PLAYER_VARIABLES_CAPABILITY = CapabilityManager.get(new CapabilityToken<>(){});
    @SubscribeEvent public static void init(RegisterCapabilitiesEvent event) { event.register(PlayerVariables.class); }
    @EventBusSubscriber public static class PlayerVariables { fields; syncPlayerVariables(Entity); Tag writeNBT(); void readNBT(Tag); }
    @EventBusSubscriber private static class PlayerVariablesProvider implements ICapabilitySerializable<Tag> { ...AttachCapabilitiesEvent... }

and its procedures read and write through `entity.getCapability(X.PLAYER_VARIABLES_CAPABILITY, null)` followed by
`.orElse(new X.PlayerVariables())` or `.ifPresent(cap -> {...})` -- often hundreds of times. NeoForge 1.21 has no entity
capabilities; the state is a data attachment (CATALOG §D13). The conversion keeps the class's own writeNBT/readNBT as
the serializer (the saved layout is unchanged) and replaces the capability with:

    ATTACHMENT_TYPES  a DeferredRegister, registered on the mod bus from the @Mod constructor
    PLAYER_VARIABLES  AttachmentType<PlayerVariables> built on writeNBT/readNBT
    vars(Entity)      Optional<PlayerVariables>, present for a player -- what the capability's LazyOptional was

so every call site becomes `X.vars(recv)` and its `.orElse`/`.ifPresent` keep working unchanged. The provider class and
the RegisterCapabilitiesEvent subscriber go; PlayerVariables loses an @EventBusSubscriber that has no handler (R1).
Death copying stays with MCreator's own clonePlayer handler (it copies only the variables marked persistent).
Standard library only.
"""
import argparse, importlib.util, pathlib, re, sys, tempfile

_spec = importlib.util.spec_from_file_location("forge_shapes", pathlib.Path(__file__).resolve().parent / "forge-shapes.py")
fs = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(fs)
match, methods = fs.match, fs.methods

CAP_DECL = re.compile(r"(?m)^[ \t]*public\s+static\s+final\s+Capability<\s*(?:[\w.]+\.)?PlayerVariables\s*>\s+PLAYER_VARIABLES_CAPABILITY\s*=")
DEAD = ("Capability", "CapabilityManager", "CapabilityToken", "ICapabilitySerializable", "ICapabilityProvider",
        "LazyOptional", "AttachCapabilitiesEvent", "RegisterCapabilitiesEvent", "FakePlayer")


def stmt_end(t, i):
    """Index just past the `;` ending the statement that starts at i (balanced brackets, strings skipped)."""
    while i < len(t):
        k = fs._skip(t, i)
        if k != i:
            i = k; continue
        if t[i] in "({[":
            i = match(t, i) + 1; continue
        if t[i] == ";":
            return i + 1
        i += 1
    return -1


def block_with_annotations(t, start, end):
    """Extend [start, end) to whole lines, including the annotation lines directly above."""
    s = t.rfind("\n", 0, start) + 1
    while s > 0:
        p = t.rfind("\n", 0, s - 1) + 1
        if not re.match(r"[ \t]*@", t[p:s]):
            break
        s = p
    e = end + (1 if t[end:end + 1] == "\n" else 0)
    return s, e


def receiver_start(t, dot):
    """Start of the primary expression ending just before t[dot] == '.' (identifiers, dots, calls, parens, casts)."""
    i = dot
    while True:
        j = i - 1
        while j >= 0 and t[j] in " \t\n":
            j -= 1
        if j >= 0 and t[j] in ")]":
            depth, k = 0, j
            while k >= 0:
                if t[k] in ")]":
                    depth += 1
                elif t[k] in "([":
                    depth -= 1
                    if depth == 0:
                        break
                k -= 1
            j = k - 1
            while j >= 0 and (t[j].isalnum() or t[j] in "_$"):
                j -= 1
            i = j + 1
        elif j >= 0 and (t[j].isalnum() or t[j] in "_$"):
            while j >= 0 and (t[j].isalnum() or t[j] in "_$"):
                j -= 1
            i = j + 1
        else:
            return i
        k = i - 1
        while k >= 0 and t[k] in " \t\n":
            k -= 1
        if k >= 0 and t[k] == ".":
            i = k
            continue
        return i


def remove_import(t, simple):
    body = re.sub(r"(?m)^\s*import\s+[^;]+;", "", t)
    if re.search(r"\b%s\b" % re.escape(simple), body):
        return t
    return re.sub(r"(?m)^\s*import\s+[\w.]+\.%s\s*;[ \t]*\n" % re.escape(simple), "", t)


def convert_owner(t, owner, modid):
    """Rewrite the <Mod>Variables class itself. -> (text, ok, notes)."""
    notes = []
    cm = CAP_DECL.search(t)
    e = stmt_end(t, cm.end())
    ind = re.match(r"[ \t]*", t[cm.start():]).group(0)
    pv = f"{owner}.PlayerVariables"
    att = "net.neoforged.neoforge.attachment"
    new = (f"{ind}public static final net.neoforged.neoforge.registries.DeferredRegister<{att}.AttachmentType<?>> ATTACHMENT_TYPES =\n"
           f"{ind}   net.neoforged.neoforge.registries.DeferredRegister.create(net.neoforged.neoforge.registries.NeoForgeRegistries.Keys.ATTACHMENT_TYPES, \"{modid}\");\n"
           f"{ind}public static final java.util.function.Supplier<{att}.AttachmentType<{pv}>> PLAYER_VARIABLES = ATTACHMENT_TYPES.register(\n"
           f"{ind}   \"player_variables\", () -> {att}.AttachmentType.builder(() -> new {pv}())\n"
           f"{ind}      .serialize(new {att}.IAttachmentSerializer<net.minecraft.nbt.CompoundTag, {pv}>() {{\n"
           f"{ind}         @Override\n"
           f"{ind}         public {pv} read({att}.IAttachmentHolder holder, net.minecraft.nbt.CompoundTag tag, net.minecraft.core.HolderLookup.Provider provider) {{\n"
           f"{ind}            {pv} v = new {pv}();\n{ind}            v.readNBT(tag);\n{ind}            return v;\n{ind}         }}\n\n"
           f"{ind}         @Override\n"
           f"{ind}         public net.minecraft.nbt.CompoundTag write({pv} v, net.minecraft.core.HolderLookup.Provider provider) {{\n"
           f"{ind}            return (net.minecraft.nbt.CompoundTag) v.writeNBT();\n{ind}         }}\n{ind}      }}).build());\n\n"
           f"{ind}/** The capability's LazyOptional, as an Optional: present for a player (CATALOG X100). */\n"
           f"{ind}public static java.util.Optional<{pv}> vars(net.minecraft.world.entity.Entity entity) {{\n"
           f"{ind}   return entity instanceof net.minecraft.world.entity.player.Player\n"
           f"{ind}      ? java.util.Optional.of(entity.getData(PLAYER_VARIABLES)) : java.util.Optional.empty();\n{ind}}}\n")
    t = t[:cm.start()] + new + t[e:].lstrip(" \t").removeprefix("\n")
    # the RegisterCapabilitiesEvent subscriber
    for mt in sorted(methods(t), key=lambda x: -x.start):
        if re.search(r"\bRegisterCapabilitiesEvent\b", t[mt.params_open:mt.params_close]):
            s, e2 = block_with_annotations(t, mt.start, mt.body_close + 1)
            t = t[:s] + t[e2:]
    # the provider class
    pm = re.search(r"(?m)^[ \t]*(?:(?:public|private|protected|static|final)\s+)*class\s+PlayerVariablesProvider\b[^{]*\{", t)
    if pm:
        s, e2 = block_with_annotations(t, pm.start(), match(t, pm.end() - 1) + 1)
        t = t[:s] + t[e2:]
    else:
        notes.append("no PlayerVariablesProvider class (left as is)")
    # PlayerVariables carries @EventBusSubscriber with no handler: NeoForge throws at registration (R1)
    vm = re.search(r"(?m)^([ \t]*@(?:Mod\.)?EventBusSubscriber\b(?:\([^)]*\))?[ \t]*\n)([ \t]*public\s+static\s+class\s+PlayerVariables\b[^{]*\{)", t)
    if vm:
        body = t[vm.end(2):match(t, vm.end(2) - 1)]
        if not re.search(r"@SubscribeEvent\b", body):
            t = t[:vm.start(1)] + t[vm.end(1):]
    return t, True, notes


def import_owner(t, owner, files):
    """Import class `owner` (a mod file's stem) into t when it lives in another package (X103)."""
    of = next((g for g in files if g.stem == owner), None)
    if of is None:
        return t
    pm = re.search(r"(?m)^package\s+([\w.]+)\s*;", files[of]); tm = re.search(r"(?m)^package\s+([\w.]+)\s*;", t)
    if not pm or (tm and tm.group(1) == pm.group(1)):
        return t
    fqn = f"{pm.group(1)}.{owner}"
    if re.search(r"(?m)^import\s+%s\s*;" % re.escape(fqn), t):
        return t
    return fs.add_import(t, fqn)


def register_on_bus(files, owner):
    """Add `Owner.ATTACHMENT_TYPES.register(bus);` to the @Mod class constructor that has the mod bus."""
    for f, t in files.items():
        if not re.search(r"(?m)^@Mod\(", t):
            continue
        for cm in re.finditer(r"(?m)^[ \t]*(?:(?:public|protected|private)\s+)?%s\s*\(" % re.escape(f.stem), t):
            p1 = match(t, cm.end() - 1)
            b = re.match(r"\s*\{", t[p1 + 1:])
            if p1 < 0 or not b:
                continue
            b0 = p1 + b.end(); b1 = match(t, b0)
            scope = t[cm.end() - 1:b1]
            bm = re.search(r"\bIEventBus\s+(\w+)", scope)
            if bm:
                ind = re.match(r"[ \t]*", t[t.rfind("\n", 0, b1) + 1:]).group(0)
                t = t[:b1] + f"   {owner}.ATTACHMENT_TYPES.register({bm.group(1)});\n{ind}" + t[b1:]
                files[f] = import_owner(t, owner, files)
                return f
    return None


def run(src, modid, dry=False):
    src = pathlib.Path(src).resolve()
    files = {f: f.read_text(encoding="utf-8", errors="replace") for f in src.rglob("*.java")}
    orig = dict(files)
    report, flags = {"variables classes": 0, "call sites": 0}, []
    owners = [f for f, t in files.items() if CAP_DECL.search(t)]
    for of in owners:
        owner = of.stem
        rel = of.relative_to(src).as_posix()
        files[of], ok, notes = convert_owner(files[of], owner, modid)
        flags += [f"{rel}: {n}" for n in notes]
        if not register_on_bus(files, owner):
            flags.append(f"{rel}: no @Mod constructor with the mod bus; add `{owner}.ATTACHMENT_TYPES.register(modBus)`")
        report["variables classes"] += 1
        pat = re.compile(r"\.\s*getCapability\(\s*(?:(?:[\w.]+\.)?%s\.)?PLAYER_VARIABLES_CAPABILITY\s*,\s*null\s*\)" % re.escape(owner))
        for f in list(files):
            t = files[f]
            if "PLAYER_VARIABLES_CAPABILITY" not in t:
                continue
            for m in reversed(list(pat.finditer(t))):
                rs = receiver_start(t, m.start())
                recv = t[rs:m.start()].strip()
                if not recv:
                    flags.append(f"{f.relative_to(src).as_posix()}:{fs.line_of(t, m.start())} getCapability with no receiver"); continue
                t = t[:rs] + f"{owner}.vars({recv})" + t[m.end():]
                report["call sites"] += 1
            if "PLAYER_VARIABLES_CAPABILITY" in t:
                flags.append(f"{f.relative_to(src).as_posix()}: PLAYER_VARIABLES_CAPABILITY still referenced")
            files[f] = t
    changed = 0
    for f, t in files.items():
        if t != orig[f]:
            for d in DEAD:
                t = remove_import(t, d)
            changed += 1
            if not dry:
                f.write_text(t, encoding="utf-8")
    return report, flags, changed


def self_check():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "m"; p.mkdir()
        (p / "MMod.java").write_text(
            "package m;\nimport net.neoforged.bus.api.IEventBus;\n@Mod(\"m\")\npublic class MMod {\n   public MMod() {\n"
            "      IEventBus bus = FMLJavaModLoadingContext.get().getModEventBus();\n      Things.REGISTRY.register(bus);\n   }\n}\n",
            encoding="utf-8")
        (p / "MModVariables.java").write_text(
            "package m;\nimport net.neoforged.neoforge.common.capabilities.Capability;\nimport net.neoforged.neoforge.common.capabilities.CapabilityManager;\n"
            "import net.neoforged.neoforge.common.capabilities.CapabilityToken;\nimport net.neoforged.neoforge.common.capabilities.ICapabilitySerializable;\n"
            "import net.neoforged.neoforge.common.util.LazyOptional;\n@EventBusSubscriber(bus = Bus.MOD)\npublic class MModVariables {\n"
            "   public static final Capability<MModVariables.PlayerVariables> PLAYER_VARIABLES_CAPABILITY = CapabilityManager.get(\n"
            "      new CapabilityToken<MModVariables.PlayerVariables>() {\n      }\n   );\n\n"
            "   @SubscribeEvent\n   public static void init(RegisterCapabilitiesEvent event) {\n      event.register(MModVariables.PlayerVariables.class);\n   }\n\n"
            "   @SubscribeEvent\n   public static void clonePlayer(Clone event) {\n"
            "      MModVariables.PlayerVariables original = (MModVariables.PlayerVariables)event.getOriginal()\n"
            "         .getCapability(MModVariables.PLAYER_VARIABLES_CAPABILITY, null).orElse(new MModVariables.PlayerVariables());\n   }\n\n"
            "   @EventBusSubscriber\n   public static class PlayerVariables {\n      public double sanity = 0.0;\n"
            "      public Tag writeNBT() { CompoundTag nbt = new CompoundTag(); nbt.putDouble(\"sanity\", this.sanity); return nbt; }\n"
            "      public void readNBT(Tag tag) { this.sanity = ((CompoundTag)tag).getDouble(\"sanity\"); }\n   }\n\n"
            "   @EventBusSubscriber\n   private static class PlayerVariablesProvider implements ICapabilitySerializable<Tag> {\n"
            "      private final LazyOptional<MModVariables.PlayerVariables> instance = LazyOptional.of(() -> new MModVariables.PlayerVariables());\n"
            "      @SubscribeEvent\n      public static void onAttachCapabilities(AttachCapabilitiesEvent<Entity> event) { }\n   }\n}\n",
            encoding="utf-8")
        (p / "Proc.java").write_text(
            "package m;\nclass Proc {\n   static void execute(Entity entity) {\n"
            "      if (((MModVariables.PlayerVariables)((Entity)entity).getCapability(MModVariables.PLAYER_VARIABLES_CAPABILITY, null)"
            ".orElse(new MModVariables.PlayerVariables())).sanity > 1) {\n         double _setval = 0;\n"
            "         entity.getCapability(MModVariables.PLAYER_VARIABLES_CAPABILITY, null).ifPresent(capability -> {\n"
            "            capability.sanity = _setval;\n         });\n      }\n   }\n}\n", encoding="utf-8")
        report, flags, _ = run(d, "m")
        v = (p / "MModVariables.java").read_text(encoding="utf-8")
        pr = (p / "Proc.java").read_text(encoding="utf-8")
        mm = (p / "MMod.java").read_text(encoding="utf-8")
        ok = (report == {"variables classes": 1, "call sites": 3} and not flags
              and "PLAYER_VARIABLES_CAPABILITY" not in v + pr and "PlayerVariablesProvider" not in v
              and "RegisterCapabilitiesEvent" not in v and "AttachmentType<MModVariables.PlayerVariables>>" in v
              and "v.readNBT(tag)" in v and "import net.neoforged.neoforge.common.capabilities" not in v
              and "@EventBusSubscriber\n   public static class PlayerVariables" not in v
              and "@EventBusSubscriber(bus = Bus.MOD)" in v
              and "MModVariables.vars(((Entity)entity)).orElse(" in pr and "MModVariables.vars(entity).ifPresent(" in pr
              and "MModVariables.vars(event.getOriginal())" in v
              and "MModVariables.ATTACHMENT_TYPES.register(bus);" in mm)
    print("self-check:", "OK" if ok else f"FAIL {report} {flags}\n{v}\n{pr}\n{mm}")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src"); ap.add_argument("--modid"); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not (a.src and a.modid):
        ap.error("--src and --modid are required")
    report, flags, changed = run(a.src, a.modid, a.dry_run)
    print(f"convert-mcreator-variables: {changed} file(s) changed" + (" (dry run)" if a.dry_run else "")
          + "".join(f", {v} {k}" for k, v in report.items()))
    for fl in flags[:12]:
        print("  left by name:", fl)
    if len(flags) > 12:
        print(f"  ... and {len(flags) - 12} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
