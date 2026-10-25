#!/usr/bin/env python3
"""Wiring a port dropped: event subscribers and capability registrations present before, absent after (CATALOG 12e).

    python3 tools/audit-wiring.py --repo DIR --against REF [--json]
    python3 tools/audit-wiring.py --self-check

Static, no Gradle and no Minecraft: the work tree's Java sources (src/*/java) against the same sources at REF -- a
version branch's parent branch, or the authors' source for the head (ci-gates.py's --base). It reports, never fixes:

  * a class with `@SubscribeEvent` methods that was WIRED at REF (an `@EventBusSubscriber` / `@Mod.EventBusSubscriber`
    annotation, or a `register(<Class>.class)` / `register(new <Class>(` / `addListener(<Class>::...)` call) and is
    not wired in the work tree -- its handlers no longer run, and every gate stays green;
  * a capability exposure at REF -- a Forge `getCapability(...)` / `initCapabilities(...)` override, or a NeoForge
    `event.registerBlockEntity / registerItem / registerEntity(...)` -- with no registration naming that class (or its
    registered type) in the work tree: hoppers, pipes and menus see no inventory;
  * a registration whose provider answers NULL for a null side (`side == null ? null`): a menu that binds through
    `getCapability(..., pos, null)` is detached (12e's augment).

Classes are matched by simple name, so a package move between REF and the tree is followed. A finding is a candidate to
read, not a verdict (a handler moved on purpose into another class is reported too).
Exit: 0 nothing found, 1 findings, 2 cannot run (no such ref, no sources).
"""
import argparse, json, pathlib, re, subprocess, sys

SUBSCRIBE = re.compile(r"@SubscribeEvent\b")
BUS_ANNOT = re.compile(r"@(?:Mod\.)?EventBusSubscriber\b")
CLASS_DECL = re.compile(r"(?m)^\s*(?:public\s+|final\s+|abstract\s+)*(?:class|interface|enum|record)\s+([A-Z][\w$]*)")
FORGE_CAP = re.compile(r"\b(?:getCapability|initCapabilities)\s*\(")
FORGE_CAP_HINT = re.compile(r"ForgeCapabilities|CapabilityItemHandler|CapabilityEnergy|CapabilityFluidHandler|"
                            r"ICapabilityProvider|LazyOptional")
NEO_REG = re.compile(r"\bregister(BlockEntity|Item|Entity|Block)\s*\(")
NULL_SIDE = re.compile(r"\(\s*\w+\s*,\s*(\w+)\s*\)\s*->\s*\1\s*==\s*null\s*\?\s*null\b")


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def call_args(text, i):
    """The text of the call's parenthesised arguments starting at the '(' at or after i."""
    j = text.find("(", i)
    if j < 0:
        return ""
    depth = 0
    for k in range(j, len(text)):
        c = text[k]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[j + 1:k]
    return text[j + 1:]


def tree_files(repo, ref=None):
    """{path: text} of every src/*/java/**/*.java file, in the work tree (ref None) or at a git ref."""
    repo = pathlib.Path(repo)
    if ref is not None and ref.startswith("-"):
        raise LookupError(f"{ref!r} is not a ref (it would be read as an option)")
    if ref is None:
        return {p.relative_to(repo).as_posix(): p.read_text(encoding="utf-8", errors="replace")
                for d in sorted(repo.glob("src/*/java")) for p in sorted(d.rglob("*.java"))}
    r = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "--name-only", ref, "--", "src"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise LookupError(f"git ls-tree {ref}: {r.stderr.strip()[-200:]}")
    paths = [p for p in r.stdout.splitlines() if re.match(r"^src/[^/]+/java/.+\.java$", p)]
    if not paths:
        return {}
    inp = "".join(f"{ref}:{p}\n" for p in paths)
    b = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch"], input=inp.encode("utf-8"), capture_output=True)
    out, data, pos = {}, b.stdout, 0
    for p in paths:
        nl = data.index(b"\n", pos)
        head = data[pos:nl].split()
        if len(head) < 3 or head[1] != b"blob":
            pos = nl + 1
            continue
        n = int(head[2])
        out[p] = data[nl + 1:nl + 1 + n].decode("utf-8", "replace")
        pos = nl + 1 + n + 1
    return out


def facts(files):
    """What the sources wire: {"subscribers": {class: wired?}, "caps": {class: how}, "regs": [registration args],
    "mentions": text of every registration, "null_side": [(path, line)]}."""
    texts = {p: strip_comments(t) for p, t in files.items()}
    allt = "\n".join(texts.values())
    subs, caps, regs, null_side = {}, {}, [], []
    for p, t in texts.items():
        m = CLASS_DECL.search(t)
        if not m:
            continue
        cls = m.group(1)
        if SUBSCRIBE.search(t):
            wired = bool(BUS_ANNOT.search(t)) or bool(re.search(
                rf"\b(?:register|addListener)\s*\(\s*(?:new\s+)?{re.escape(cls)}\b(?:\s*\.class|\s*\(|\s*::)", allt)) or \
                bool(re.search(rf"\baddListener\s*\([^)]*\b{re.escape(cls)}::", allt))
            subs[cls] = wired
        if FORGE_CAP.search(t) and FORGE_CAP_HINT.search(t) and re.search(r"@Override[\s\S]{0,200}?(?:getCapability|initCapabilities)\s*\(", t):
            caps[cls] = "a Forge getCapability/initCapabilities override"
        for rm in NEO_REG.finditer(t):
            a = call_args(t, rm.end() - 1)
            regs.append(re.sub(r"\s+", " ", a))
            for nm in NULL_SIDE.finditer(a):
                null_side.append((p, t.count("\n", 0, rm.start()) + 1))
    return {"subscribers": subs, "caps": caps, "regs": regs, "null_side": null_side}


def _words(name):
    """`DragonTableBlockEntity` / `DRAGON_TABLE` -> {"dragon", "table"} (the words a type constant shares with its class)."""
    w = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", name)
    return {x.lower() for x in w} - {"block", "entity", "item", "tile", "be", "te"}


def _registered(cls, regs):
    want = _words(cls)
    for a in regs:
        if re.search(rf"\b{re.escape(cls)}\b", a):
            return True
        for tok in re.findall(r"\b[A-Z][A-Za-z0-9_]*\b", a):
            if want and _words(tok) >= want:
                return True
    return False


def _norm_reg(a):
    return re.sub(r"\s+", "", a)


def audit(before, after):
    """[finding lines] for the sources before (REF) and after (the work tree)."""
    b, a = facts(before), facts(after)
    out = []
    for cls, wired in sorted(b["subscribers"].items()):
        if wired and cls in a["subscribers"] and not a["subscribers"][cls]:
            out.append(f"event wiring lost: {cls} has @SubscribeEvent methods and was registered before (an "
                       "@EventBusSubscriber or a register/addListener call); nothing registers it now, so its handlers "
                       "never run (CATALOG 12e)")
    for cls, how in sorted(b["caps"].items()):
        if not _registered(cls, a["regs"]):
            out.append(f"capability not registered: {cls} exposed one before ({how}); no registerBlockEntity / "
                       "registerItem / registerEntity names it or its type now (CATALOG 12e)")
    have = {_norm_reg(x) for x in a["regs"]}
    for r in b["regs"]:
        if _norm_reg(r) not in have:
            first = r.split(",")[0].strip()
            rest = ",".join(r.split(",")[1:3]).strip()
            if not any(first in x and all(t in x for t in re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", rest)) for x in a["regs"]):
                out.append(f"capability registration gone: register*({r[:120]}) is not in the tree any more (CATALOG 12e)")
    for p, ln in a["null_side"]:
        out.append(f"null side answered with null: {p}:{ln} -- a menu that binds through getCapability(..., pos, null) "
                   "is detached from the block (CATALOG 12e augment)")
    return out


def _refuse_layer(repo, tool):
    """tools/fork-kind.py's refusal of a checkout with the removed .multiversion/ layer at HEAD: None, or the message
    (print it and exit 2)."""
    import importlib.util as _ilu
    s = _ilu.spec_from_file_location("fork_kind", pathlib.Path(__file__).resolve().parent / "fork-kind.py")
    m = _ilu.module_from_spec(s); s.loader.exec_module(m)
    return m.refuse_layer(repo, tool)


def main():
    if "--self-check" in sys.argv:
        return self_check()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--against", required=True, metavar="REF", help="the sources to compare with (a git ref)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    why = _refuse_layer(a.repo, "audit-wiring")
    if why:
        print(why)
        return 2
    try:
        before = tree_files(a.repo, a.against)
    except LookupError as e:
        print(f"audit-wiring: {e}"); return 2
    after = tree_files(a.repo)
    if not before or not after:
        print(f"audit-wiring: no Java sources {'at ' + a.against if not before else 'in the tree'}"); return 2
    found = audit(before, after)
    if a.json:
        print(json.dumps({"against": a.against, "findings": found}, indent=1))
    else:
        for f in found:
            print(f"audit-wiring: {f}")
        print(f"audit-wiring: {len(found)} finding(s) against {a.against} ({len(before)} file(s) before, "
              f"{len(after)} now)")
    return 1 if found else 0


def self_check():
    ok = True

    def expect(c, msg):
        nonlocal ok
        if not c:
            ok = False
            print("FAIL:", msg)
    sub = ("package com.example.mod;\n@Mod.EventBusSubscriber(modid = \"examplemod\")\npublic class ExampleEvents {\n"
           "    @SubscribeEvent\n    public static void onTick(TickEvent e) {}\n}\n")
    reg_sub = "package com.example.mod;\npublic class Other {\n    @SubscribeEvent\n    public void on(Object e) {}\n}\n"
    main_ = "package com.example.mod;\npublic class ExampleMod {\n    ExampleMod() { MinecraftForge.EVENT_BUS.register(new Other()); }\n}\n"
    be = ("package com.example.mod;\npublic class ExampleChestBlockEntity extends BlockEntity {\n    @Override\n"
          "    public <T> LazyOptional<T> getCapability(Capability<T> cap, Direction side) {\n"
          "        return cap == ForgeCapabilities.ITEM_HANDLER ? handler.cast() : super.getCapability(cap, side);\n    }\n}\n")
    before = {"src/main/java/com/example/mod/ExampleEvents.java": sub, "src/main/java/com/example/mod/Other.java": reg_sub,
              "src/main/java/com/example/mod/ExampleMod.java": main_,
              "src/main/java/com/example/mod/ExampleChestBlockEntity.java": be}
    # a clean port: the annotation renamed, the register call kept, the capability registered by its type
    good = dict(before)
    good["src/main/java/com/example/mod/ExampleEvents.java"] = sub.replace("@Mod.EventBusSubscriber", "@EventBusSubscriber")
    good["src/main/java/com/example/mod/ExampleChestBlockEntity.java"] = \
        "package com.example.mod;\npublic class ExampleChestBlockEntity extends BlockEntity {\n}\n"
    good["src/main/java/com/example/mod/Caps.java"] = (
        "package com.example.mod;\nclass Caps {\n  static void on(RegisterCapabilitiesEvent event) {\n"
        "    event.registerBlockEntity(Capabilities.ItemHandler.BLOCK, ModBlockEntities.EXAMPLE_CHEST.get(),\n"
        "        (be, side) -> be.getHandler(side));\n  }\n}\n")
    expect(audit(before, good) == [], f"a clean port: nothing reported: {audit(before, good)}")
    # the annotation dropped, the register call dropped, the capability never registered
    bad = dict(before)
    bad["src/main/java/com/example/mod/ExampleEvents.java"] = sub.replace("@Mod.EventBusSubscriber(modid = \"examplemod\")\n", "")
    bad["src/main/java/com/example/mod/ExampleMod.java"] = "package com.example.mod;\npublic class ExampleMod {\n}\n"
    bad["src/main/java/com/example/mod/ExampleChestBlockEntity.java"] = good["src/main/java/com/example/mod/ExampleChestBlockEntity.java"]
    got = audit(before, bad)
    expect(len(got) == 3 and sum("event wiring lost: ExampleEvents" in x for x in got) == 1
           and sum("event wiring lost: Other" in x for x in got) == 1
           and sum("capability not registered: ExampleChestBlockEntity" in x for x in got) == 1,
           f"a dropped annotation, a dropped register call and an unregistered capability: each reported: {got}")
    # a commented-out annotation is not wiring; a NeoForge registration lost between versions; a null side
    c_ = dict(good)
    c_["src/main/java/com/example/mod/ExampleEvents.java"] = sub.replace("@Mod.EventBusSubscriber", "// @EventBusSubscriber")
    c_["src/main/java/com/example/mod/Caps.java"] = good["src/main/java/com/example/mod/Caps.java"].replace(
        "(be, side) -> be.getHandler(side)", "(be, side) -> side == null ? null : be.getHandler(side)")
    got = audit(before, c_)
    expect(len(got) == 2 and "event wiring lost: ExampleEvents" in got[0] and "null side answered with null" in got[1]
           and "Caps.java:4" in got[1], f"a commented-out annotation, a null side: {got}")
    lost = dict(good)
    lost["src/main/java/com/example/mod/Caps.java"] = "package com.example.mod;\nclass Caps {}\n"
    got = audit(good, lost)
    expect(len(got) == 1 and got[0].startswith("capability registration gone: register*(Capabilities.ItemHandler.BLOCK"),
           f"a version branch that lost its parent's registration: reported: {got}")
    # tree_files at a git ref and in the work tree (a fixture repository)
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        r = pathlib.Path(d)
        g = lambda *a_: subprocess.run(["git", "-C", str(r), *a_], capture_output=True, text=True, encoding="utf-8",
                                       check=True)
        g("init", "-q"); g("config", "user.email", "a@b"); g("config", "user.name", "a"); g("config", "gc.auto", "0")
        for p, t in before.items():
            (r / p).parent.mkdir(parents=True, exist_ok=True)
            (r / p).write_text(t, encoding="utf-8")
        g("add", "-A"); g("commit", "-qm", "before")
        (r / "src/main/java/com/example/mod/ExampleMod.java").write_text(bad["src/main/java/com/example/mod/ExampleMod.java"],
                                                                         encoding="utf-8")
        try:
            tree_files(r, "--output=x"); expect(False, "a ref starting with - is refused")
        except LookupError:
            pass
        tf = tree_files(r, "HEAD")
        expect(tf == before and tree_files(r)["src/main/java/com/example/mod/ExampleMod.java"]
               == bad["src/main/java/com/example/mod/ExampleMod.java"],
               "tree_files: the sources at a ref (git cat-file --batch) and in the work tree")
        got = audit(tf, tree_files(r))
        expect(len(got) == 2 and "event wiring lost: Other" in got[0] and "capability not registered" in got[1],
               f"the work tree against HEAD: the dropped register call; the override nothing registers: {got}")
    print("self-check:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
