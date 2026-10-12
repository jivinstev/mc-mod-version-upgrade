#!/usr/bin/env python3
"""Park the code a port cannot compile against: optional integrations, and the build-time datagen.

    python3 tools/park-optional.py --work mods/<modid> [--dry-run] [--self-check]

A decompiled mod carries two kinds of code the port does not need in order to load:

  * OPTIONAL INTEGRATIONS -- a JEI/EMI/REI plugin, CraftTweaker/KubeJS bindings, a Jade provider -- written
    against another mod's API. That API is not on the port's classpath, so every line of it is a compile
    error a worker would be paid to "fix" by guessing at an API it cannot see (measured on a 1.21.1 food
    mod: ~425 of 1707 starting errors were three integration packages). CATALOG §N: the first pass parks
    them and records it; re-porting one is its own step, against the viewer's real API.
  * DATAGEN -- the providers behind a GatherDataEvent. They ran when the author built the jar and their
    output ships in the jar's data/ and assets/; the game never runs them (CATALOG §M14).

A file is an integration when it imports a package from neither the platform (Minecraft, NeoForge, the
JDK, the libraries every NeoForge game ships) nor the mod itself, nor a REQUIRED dependency's package.
When such a file sits under an `integration`/`compat`/`addon(s)`/`plugin(s)` package, that whole
sub-package goes (an integration's helpers import nothing foreign themselves). Datagen is the package
that holds the GatherDataEvent subscriber, when it is a dedicated data/datagen package; otherwise only
that file. A datagen CLASS is also found by its TYPE, wherever it lives (authors often keep the subscriber in
event/ and the providers in data/ or api/data/): it extends or implements a datagen base -- a provider, a
recipe/model builder, DataProvider -- imported from a datagen package (net.minecraft.data.* except
net.minecraft.data.worldgen, whose helpers mods call at runtime; NeoForge's/Forge's common.data and
client.model.generators), or another such class of the mod. A helper that merely imports a datagen package
joins them only when every class that names it is already datagen. Nothing that main code names is parked --
except through the datagen WIRING itself: when main code (often the mod's main class) holds a GatherDataEvent
handler or registers a datagen subscriber class, that handler method / registration line is cut out of it
(and recorded), so it no longer keeps every provider in the build. Parked files move to mods/<modid>/parked/ with their paths kept, and every one is listed in
MIGRATION.md, so nothing is dropped silently. A remaining file that imports a parked class is listed too,
and the parked class it names is left behind as a no-op STUB (X168): the same name, its non-private members whose
signatures use no type from the parked API, each doing nothing -- so a guarded call compiles and the integration is
still recorded as dropped. Standard library.
"""
import argparse, json, pathlib, re, shutil, sys

# net.minecraftforge.* is the SOURCE loader's own API, not an optional integration: the recipe pack and
# forge-shapes port it. Parking it hid a main-class dependency (measured: a config-screen handler).

PLATFORM = ("java.", "javax.", "jdk.", "sun.", "net.minecraft.", "net.neoforged.", "net.minecraftforge.", "com.mojang.", "org.spongepowered.",
            "org.jetbrains.", "org.intellij.", "com.google.", "it.unimi.", "org.apache.", "com.llamalad7.", "org.joml.",
            "org.slf4j.", "io.netty.", "org.lwjgl.", "com.electronwill.", "org.objectweb.", "cpw.mods.", "org.checkerframework.",
            "org.jspecify.", "net.jodah.", "com.ibm.icu.", "oshi.", "com.sun.", "org.w3c.", "org.xml.", "org.antlr.",
            # also on every 1.21+ classpath (Minecraft's and NeoForge's own libraries): parking a mod's config class
            # for `import joptsimple.internal.Strings` cost one mod 100 errors (X74)
            "joptsimple.", "net.jpountz.", "com.machinezoo.", "io.github.llamalad7.", "org.jline.")
OTHER_LOADERS = ("fabric", "quilt")
INTEGRATION_DIR = re.compile(r"^(integration|integrations|compat|compatibility|addon|addons|plugin|plugins)$", re.I)
DATAGEN_DIR = re.compile(r"^(data|datagen|datagenerator|datagenerators|gen|generators?)$", re.I)
IMPORT = re.compile(r"(?m)^\s*import\s+(?:static\s+)?([\w.]+)\s*;")
DATAGEN_PKG = re.compile(r"^(?:net\.minecraft\.data\.(?!worldgen\b)|net\.minecraft\.data\.[A-Z]"
                         r"|net\.(?:neoforged\.neoforge|minecraftforge)\.(?:common\.data|client\.model\.generators)\.)")
ENTRY = re.compile(r"(?m)^\s*@(?:net\.neoforged\.fml\.common\.|net\.minecraftforge\.fml\.common\.)?Mod\s*\(")
DECL = re.compile(r"\b(?:class|interface|record)\s+(\w+)[^{;]*?\b(?:extends|implements)\s+([^{]+)\{", re.S)
GATHER_METHOD = re.compile(r"(?m)^[ \t]*(?:@[\w.]+(?:\([^)]*\))?\s*)*(?:(?:public|private|protected|static|final"
                           r"|synchronized)\s+)*void\s+(\w+)\s*\(\s*(?:final\s+)?(?:[\w.]+\.)?GatherDataEvent(?:\.\w+)?"
                           r"\s+\w+\s*\)\s*(?:throws[^{]*)?\{")


def _block_end(t, i):
    """Index just past the brace block opening at t[i] == '{', skipping strings, chars and comments."""
    depth, n = 0, len(t)
    while i < n:
        c = t[i]
        if t.startswith("//", i):
            i = t.find("\n", i); i = n if i < 0 else i
        elif t.startswith("/*", i):
            i = t.find("*/", i + 2); i = n if i < 0 else i + 2; continue
        elif c in "\"'":
            j = i + 1
            while j < n and t[j] != c:
                j += 2 if t[j] == "\\" else 1
            i = j
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def has_members(t, cls):
    """Does this class still declare anything once its GatherDataEvent handlers are cut -- a field, or a method
    other than a no-argument constructor? (Bodies are skipped by brace depth, so only the class's own members count.)"""
    m = re.search(r"\b(?:class|record|enum|interface)\s+%s\b[^{]*\{" % re.escape(cls), t)
    if not m:
        return True
    depth, i, top = 1, m.end(), []
    while i < len(t) and depth:
        c = t[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif depth == 1:
            top.append(c)
        i += 1
    decls = [x.strip() for x in re.split(r"[;)]", re.sub(r"//[^\n]*|/\*.*?\*/|@\w+(?:\([^)]*\))?", "", "".join(top), flags=re.S)) if x.strip()]
    return any(not re.fullmatch(r"(?:public|protected|private)?\s*%s\s*\(" % re.escape(cls), x) for x in decls)


def cut_registrations(t, stems):
    """Drop the lines that only REGISTER a datagen subscriber class (addListener(Gen::gather),
    EVENT_BUS.register(Gen.class)) and the imports left unused -> (new text, [stems cut])."""
    hit = []
    for st in sorted(stems):
        pat = re.compile(r"(?m)^[ \t]*[\w.()]*(?:addListener\(\s*(?:[\w.]+\.)?%s::\w+\s*\)"
                         r"|register\(\s*(?:[\w.]+\.)?%s\.class\s*\)"
                         r"|register\(\s*new\s+(?:[\w.]+\.)?%s\s*\(\s*\)\s*\))\s*;[ \t]*\n" % (st, st, st))
        t2 = pat.sub("", t)
        if t2 != t:
            hit.append(st); t = t2
    if hit:
        body = IMPORT.sub("", t)
        for imp in IMPORT.findall(t):
            if imp.rsplit(".", 1)[1] in hit and not re.search(r"\b%s\b" % re.escape(imp.rsplit(".", 1)[1]), body):
                t = re.sub(r"(?m)^\s*import\s+%s\s*;[ \t]*\n" % re.escape(imp), "", t)
    return t, hit


def cut_datagen_hooks(t, stem):
    """X122: main code that only HOOKS datagen -- `implements ICustomItemModel` and the
    `generateCustomItemModel(ItemModelGenerator g, ...)` that interface asks for -- keeps the whole datagen package in the
    build. Cut the methods whose parameters name `stem`, the `implements` entry naming it, and the imports left unused
    (datagen-package ones included). -> the new text, or None when nothing was cut."""
    new, n = t, 0
    hdr = re.compile(r"(?m)^[ \t]*(?:@[\w.]+(?:\([^)]*\))?\s*)*(?:(?:public|protected|private|static|final|default|abstract"
                     r"|synchronized)\s+)*[\w<>\[\],.? ]+?\s+\w+\s*\(([^()]*)\)\s*(?:throws[^{;]*)?\{")
    while True:
        m = next((m for m in hdr.finditer(new) if re.search(r"\b%s\b" % re.escape(stem), m.group(1))), None)
        if not m:
            break
        end = _block_end(new, m.end() - 1)
        while end < len(new) and new[end] in " \t":
            end += 1
        if new[end:end + 1] == "\n":
            end += 1
        new = new[:m.start()] + new[end:]
        n += 1

    def drop_impl(cm):
        nonlocal n
        parts = [p.strip() for p in cm.group(2).split(",")]
        keep = [p for p in parts if re.sub(r"<.*", "", p).rsplit(".", 1)[-1] != stem]
        if len(keep) == len(parts):
            return cm.group(0)
        n += 1
        return (cm.group(1) + ", ".join(keep) + " " if keep else "") + "{"
    new = re.sub(r"(\bimplements\s+)([^{]+?)\s*\{", drop_impl, new, count=1)
    if not n:
        return None
    body = IMPORT.sub("", new)
    for imp in IMPORT.findall(new):
        simple = imp.rsplit(".", 1)[1]
        if (simple == stem or DATAGEN_PKG.match(imp)) and not re.search(r"\b%s\b" % re.escape(simple), body):
            new = re.sub(r"(?m)^\s*import\s+%s\s*;[ \t]*\n" % re.escape(imp), "", new)
    return new


def cut_gather(t):
    """A class that main code needs but that also wires datagen (a mod's main class registering its own
    GatherDataEvent handler is common): remove each GatherDataEvent handler METHOD, the line that registers
    it (addListener(this::m) / addListener(X::m)), and the imports left unused. -> (new text, [method names])."""
    names = []
    while True:
        m = GATHER_METHOD.search(t)
        if not m:
            break
        end = _block_end(t, m.end() - 1)
        if end < 0:
            break
        names.append(m.group(1))
        start = t.rfind("\n", 0, m.start()) + 1
        t = t[:start] + t[end:].lstrip(" \t").removeprefix("\n")
    for n in names:
        t = re.sub(r"(?m)^[ \t]*[\w.()]*addListener\(\s*[\w.]+::%s\s*\)\s*;[ \t]*\n" % re.escape(n), "", t)
    if names:
        body = IMPORT.sub("", t)
        for imp in IMPORT.findall(t):
            simple = imp.rsplit(".", 1)[1]
            if not re.search(r"\b%s\b" % re.escape(simple), body):
                t = re.sub(r"(?m)^\s*import\s+%s\s*;[ \t]*\n" % re.escape(imp), "", t)
    return t, names


def required_roots(work):
    """Package hints for REQUIRED non-platform dependencies: their modId, which a mod's own package
    almost always contains. Their API is meant to be on the classpath, so their imports are kept."""
    toml = work / "src/main/resources/META-INF/neoforge.mods.toml"
    t = toml.read_text(encoding="utf-8", errors="replace") if toml.exists() else ""
    out = set()
    for block in re.split(r"\[\[dependencies\.", t)[1:]:
        mid = re.search(r'modId\s*=\s*"([^"]+)"', block)
        kind = re.search(r'type\s*=\s*"(\w+)"', block) or re.search(r"mandatory\s*=\s*(\w+)", block)
        if mid and kind and kind.group(1) in ("required", "true") and mid.group(1) not in ("minecraft", "neoforge"):
            out.add(mid.group(1).replace("-", "").replace("_", "").lower())
    return out


def classpath_packages(work):
    """Packages of every jar in libs/<mc>/ (the wired dependencies, transitive ones included) and their nested jars."""
    import io, zipfile
    out = set()

    def scan(z):
        for n in z.namelist():
            if n.endswith(".class") and "/" in n and not n.startswith("META-INF/"):
                out.add(n.rsplit("/", 1)[0].replace("/", "."))
            elif n.endswith(".jar") and n.startswith("META-INF/jarjar/"):
                try:
                    scan(zipfile.ZipFile(io.BytesIO(z.read(n))))
                except zipfile.BadZipFile:
                    pass
    for j in pathlib.Path(work).glob("libs/*/*.jar"):
        try:
            scan(zipfile.ZipFile(j))
        except (zipfile.BadZipFile, OSError):
            pass
    return out


def foreign(imp, own, req, tree=frozenset()):
    if imp.startswith(PLATFORM) or any(imp.startswith(o + ".") for o in own):
        return False
    parts = imp.split(".")                  # a package present in the decompiled tree is the mod's own (a second
    if any(".".join(parts[:k]) in tree for k in range(2, len(parts) + 1)):   # root package, a shaded library)
        return False
    flat = imp.replace("_", "").lower()
    return not any(r in flat.split(".") or r in flat for r in req)


def plan(work, group, also=()):
    java = work / "src/main/java"
    files = sorted(java.rglob("*.java"))
    own = {group, group.rsplit(".", 1)[0]} if group else set()
    tree = {f.relative_to(java).parent.as_posix().replace("/", ".") for f in files}
    tree |= classpath_packages(work)      # a dependency jar the port compiles against: its API is there, nothing to park
    req = required_roots(work) | {m.replace("-", "").replace("_", "").lower() for m in also if m}
    park, why, cuts = {}, {}, {}
    # a jar that carries one tree PER LOADER (`fabric/...` beside `neoforge/...`, or `x.fabric` beside `x.neoforge`):
    # the other loader's copy is never run on NeoForge, and parking only its files that import Fabric leaves the rest
    # of it to fail (X78). Park the whole sibling tree.
    for d in sorted({p for f in files for p in f.relative_to(java).parents} - {pathlib.Path(".")}):
        if d.name in OTHER_LOADERS and any((java / d.parent / n).is_dir() for n in ("neoforge", "forge")):
            for g in (java / d).rglob("*.java"):
                park[g] = f"other loader: {d.as_posix()}/ (this jar keeps a tree per loader; the port targets NeoForge)"
    for f in files:
        if f in park:
            continue
        t = f.read_text(encoding="utf-8", errors="replace")
        bad = sorted({i for i in IMPORT.findall(t) if foreign(i, own, req, tree)})
        if bad and ENTRY.search(t):
            continue          # the @Mod entry class is never parked: its integration references are compile-loop work
        if bad:
            rel = f.relative_to(java)
            parts = rel.parts
            hit = next((k for k, p in enumerate(parts[:-1]) if INTEGRATION_DIR.match(p)), None)
            if hit is not None and hit + 1 < len(parts) - 1:   # integration/<name>/... -> the whole <name>
                unit = pathlib.Path(*parts[:hit + 2])
                why[unit.as_posix()] = f"integration: imports {bad[0].rsplit('.', 1)[0]}"
                for g in (java / unit).rglob("*.java"):
                    park[g] = why[unit.as_posix()]
            else:
                park[f] = f"integration: imports {bad[0]}"
        if re.search(r"\bGatherDataEvent\b", t):
            rel = f.relative_to(java)
            d = rel.parent
            named = re.compile(r"\b" + re.escape(f.stem) + r"\b")
            new, names = cut_gather(t)
            # main code that ALSO wires datagen keeps the class minus its handlers -- but a class that is NOTHING
            # but handlers (a `Generators` registered with bus.register(new Generators())), or that sits in a datagen
            # package, is the subscriber itself: park it with its package, and cut_registrations drops the line that
            # registers it. Keeping it hid a whole data/gen package whose providers extend a LIBRARY's datagen base,
            # which the type rule cannot see (X92).
            if names and not DATAGEN_DIR.match(d.name) and has_members(new, f.stem) \
                    and not re.search(r"\bGatherDataEvent\b", IMPORT.sub("", new)) and any(
                    g != f and named.search(g.read_text(encoding="utf-8", errors="replace")) for g in files):
                cuts[f] = (new, names)                   # main code wiring datagen: cut the handler, keep the class
            elif DATAGEN_DIR.match(d.name):
                for g in (java / d).rglob("*.java"):
                    park[g] = f"datagen: {d.as_posix()} (GatherDataEvent)"
            else:
                park[f] = "datagen: GatherDataEvent subscriber"
    text = {f: f.read_text(encoding="utf-8", errors="replace") for f in files}
    text.update({f: new for f, (new, _) in cuts.items()})
    # main code that only REGISTERS a separate datagen subscriber class must not keep it (and so everything
    # it names) in the build: cut the registration line, and let the subscriber park as datagen
    subs = {f.stem for f, why in park.items() if why.startswith("datagen")}
    for f in files:
        if f in park or not subs:
            continue
        new, hit = cut_registrations(text[f], subs)
        if hit:
            text[f] = new
            cuts[f] = (new, cuts.get(f, (None, []))[1] + [f"register {h}" for h in hit])
    park.update(datagen_by_type(java, files, park, text))
    # a datagen PACKAGE can hold runtime code too (a mod's tag constants under datagen/tags, named by its items
    # and blocks): never park a datagen-reason file that a file staying in the build still names
    # an integration-reason file OUTSIDE an integration package that main code leans on is a hub, not a plugin
    # (a mod's 40-KB client facade and its "safe class" helper, guarding optional APIs at runtime): parking it cost one
    # mod ~200 errors to save a handful. Keep a file at least 3 staying files name; its optional imports become
    # compile-loop work (X85).
    changed = True
    while changed:
        changed = False
        for f in [f for f, why in park.items() if why.startswith("integration: imports")
                  and not any(INTEGRATION_DIR.match(p) for p in f.relative_to(java).parts[:-1])]:
            who = re.compile(r"\b" + re.escape(f.stem) + r"\b")
            if sum(1 for g, u in text.items() if g != f and g not in park and who.search(u)) >= 3:
                del park[f]
                changed = True
    # X122: a staying class that only HOOKS a parked datagen type (implements its interface, has a method taking its
    # generator) loses the hook, so the datagen type can stay parked instead of pulling its package back in
    for f in [f for f, why in park.items() if why.startswith("datagen")]:
        who = re.compile(r"\b" + re.escape(f.stem) + r"\b")
        for g in [g for g in files if g not in park and g != f and who.search(text[g])]:
            new = cut_datagen_hooks(text[g], f.stem)
            if new is not None and not who.search(IMPORT.sub("", new)):
                text[g] = new
                cuts[g] = (new, cuts.get(g, (None, []))[1] + [f"datagen hook {f.stem}"])
    changed = True
    while changed:
        changed = False
        for f in [f for f, why in park.items() if why.startswith("datagen")]:
            who = re.compile(r"\b" + re.escape(f.stem) + r"\b")
            if any(g not in park and who.search(u) for g, u in text.items() if g != f):
                del park[f]
                changed = True
    return park, cuts


def datagen_by_type(java, files, already, text=None):
    """-> {file: reason} for datagen classes found by type, plus helpers only datagen names (see the docstring)."""
    text = text or {f: f.read_text(encoding="utf-8", errors="replace") for f in files}
    dg_imports = {f: {i.rsplit(".", 1)[1] for i in IMPORT.findall(t) if DATAGEN_PKG.match(i)} for f, t in text.items()}
    simple = {f: f.stem for f in files}
    found, changed = {}, True
    while changed:                                   # transitive: a class extending one of the mod's providers
        changed = False
        dg_names = {simple[f] for f in found} | {simple[f] for f in already}
        for f, t in text.items():
            if f in found or f in already:
                continue
            for m in DECL.finditer(t):
                if m.group(1) != simple[f]:
                    continue
                bases = set(re.findall(r"\b([A-Z]\w*)\b", m.group(2)))
                hit = (bases & dg_imports[f]) or (bases & dg_names)
                if hit:
                    found[f] = f"datagen: {sorted(hit)[0]} subclass"
                    changed = True
                break
    changed = True
    while changed:                                   # helpers named by datagen classes only
        changed = False
        dg = set(found) | set(already)
        for f, t in text.items():
            if f in dg or not dg_imports[f]:
                continue
            who = re.compile(r"\b" + re.escape(simple[f]) + r"\b")
            users = [g for g, u in text.items() if g != f and who.search(u)]
            if users and all(g in dg for g in users):
                found[f] = "datagen: helper used only by datagen"
                changed = True
    # never park what main code names: drop any found class a non-datagen, non-parked class refers to
    changed = True
    while changed:
        changed = False
        dg = set(found) | set(already)
        for f in list(found):
            who = re.compile(r"\b" + re.escape(simple[f]) + r"\b")
            if any(g not in dg and who.search(u) for g, u in text.items() if g != f):
                del found[f]
                changed = True
    return found


def class_name(java, f):
    return f.relative_to(java).with_suffix("").as_posix().replace("/", ".")


def prune_mixin_configs(work, parked_names):
    """Drop parked classes from every *.mixins.json: Mixin fails at load on an entry whose class is gone (X78).
    -> [config: entry, ...]"""
    out = []
    for cfg in sorted((work / "src/main/resources").glob("*.json")):
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or "package" not in data:
            continue
        pkg, changed = data["package"], False
        for key in ("mixins", "client", "server"):
            entries = data.get(key)
            if isinstance(entries, list):
                keep = [e for e in entries if f"{pkg}.{e}" not in parked_names]
                if len(keep) != len(entries):
                    out += [f"{cfg.name}: {e}" for e in entries if e not in keep]
                    data[key], changed = keep, True
        if changed:
            cfg.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return out


# --------------------------------------------------------------------------- stubs (X168)
# Parking an integration leaves the main code that calls it naming a class that is gone: measured on 77 of 387 swept
# mods. Main code is never parked, so a STUB keeps the name: the class's non-private constructors, methods and literal
# constants whose signatures use no type from the parked API, each a no-op returning its type's default. The
# integration is as dropped as it was -- the stub is recorded in MIGRATION.md -- but the guarded call sites compile.

DEFAULTS = {"void": None, "boolean": "false", "char": "'\\0'", "byte": "0", "short": "0", "int": "0", "long": "0L",
            "float": "0.0F", "double": "0.0D"}
MODS = r"(?:(?:public|protected|private|static|final|synchronized|abstract|native|strictfp)\s+)*"


def _flatten(body):
    """The class body with every nested {...} (method bodies, nested classes, initialisers) blanked out."""
    out, depth = [], 0
    for c in body:
        if c == "{":
            depth += 1
            out.append(c if depth == 1 else " ")
        elif c == "}":
            out.append(c if depth == 1 else " ")
            depth -= 1
        else:
            out.append(c if depth == 0 else ("\n" if c == "\n" else " "))
    return "".join(out)


def stub_for(text, simple, unsafe):
    """Source of a no-op stub of top-level class `simple`, or None when it cannot be one (not a plain class)."""
    text = re.sub(r"/\*.*?\*/|//[^\n]*", lambda m: " " * len(m.group(0)), text, flags=re.S)
    m = re.search(r"(?m)^(?:public\s+)?(?:final\s+)?class\s+%s\b(?P<gen><[^{]*?>)?[^{]*\{" % re.escape(simple), text)
    if not m:
        return None
    pkg = re.search(r"(?m)^\s*package\s+([\w.]+)\s*;", text)
    i, depth = m.end() - 1, 0
    for j in range(i, len(text)):
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        if depth == 0:
            break
    flat = _flatten(text[i + 1:j])
    # a class that carries STATE others read (a config POJO: `public boolean eatMelons = true;`) cannot be a no-op stand-in:
    # its values ARE its behaviour, and defaults would change them silently. Leave it unstubbed (X168 regression, 37 -> 212).
    if re.search(r"(?m)^\s*(?:@[\w.]+(?:\([^()]*\))?\s*)*(?:public|protected)\s+(?!static\b)(?:final\s+)?[\w.<>\[\], ?]+\s+\w+\s*[=;]", flat):
        return None
    tok = lambda s: not (set(re.findall(r"[A-Za-z_]\w*", s)) & unsafe)
    members = []
    for mm in re.finditer(r"(?:@[\w.]+(?:\([^()]*\))?\s*)*(?P<mods>" + MODS + r")(?P<tp><[^(){};=]*?>\s+)?"
                          r"(?:(?P<ret>[\w.$]+(?:<[^(){};=]*?>)?(?:\[\])*)\s+)?(?P<name>\w+)\s*\((?P<params>[^(){};]*)\)\s*"
                          r"(?:throws\s+[\w.,\s]+)?\{", flat):
        mods, ret, name, params = mm.group("mods"), mm.group("ret"), mm.group("name"), mm.group("params")
        if "private" in mods or "abstract" in mods or name in ("if", "for", "while", "switch", "catch", "synchronized"):
            continue
        sig = (ret or "") + " " + params + " " + (mm.group("tp") or "")
        if not tok(sig):
            continue
        mods = re.sub(r"\b(synchronized|native|strictfp|final)\s+", "", mods)
        if ret is None:
            if name != simple:
                continue
            members.append(f"    {mods}{simple}({params.strip()}) {{\n    }}\n")
        else:
            d = DEFAULTS.get(ret, "null")
            body = "" if ret == "void" else f"\n        return {d};"
            members.append(f"    {mods}{mm.group('tp') or ''}{ret} {name}({params.strip()}) {{{body}\n    }}\n")
    for fm in re.finditer(r"(?m)^\s*(?P<mods>(?:(?:public|protected|static|final)\s+)+)(?P<ty>[\w.]+)\s+(?P<n>\w+)\s*=\s*"
                          r"(?P<v>\"(?:[^\"\\]|\\.)*\"|-?[\d.]+[FfDdLl]?|true|false)\s*;", flat):
        if "static" in fm.group("mods") and "final" in fm.group("mods") and tok(fm.group("ty")):
            members.insert(0, f"    {fm.group('mods').strip()} {fm.group('ty')} {fm.group('n')} = {fm.group('v')};\n")
    imports = [im for im in re.findall(r"(?m)^\s*import\s+(?:static\s+)?[\w.*]+\s*;", text)
               if im.strip().split()[-1].rstrip(";").split(".")[-1] not in unsafe]
    head = f"package {pkg.group(1)};\n\n" if pkg else ""
    return (head + "\n".join(x.strip() for x in imports) + ("\n\n" if imports else "")
            + "/** Parked-integration stub (tools/park-optional.py, CATALOG X168): the real class is under parked/ and every\n"
            + " *  member here does nothing. Re-port the integration against the other mod's API to restore it. */\n"
            + f"public class {simple}{m.group('gen') or ''} {{\n" + "".join(members) + "}\n")


def unsafe_names(java, park_texts, own, req, tree, stubbed):
    """Simple names a stub must not mention: every foreign import of a parked file, and every parked class
    that is not itself stubbed."""
    out = set()
    for f, text in park_texts.items():
        for imp in IMPORT.findall(text):
            if foreign(imp, own, req, tree):
                out.add(imp.rsplit(".", 1)[-1])
        name = class_name(java, f).rsplit(".", 1)[-1]
        if name not in stubbed:
            out.add(name)
    return out


def _same_pkg_user(java, f, park, now):
    """A main-code file in the same package names the class without an import."""
    simple = f.stem
    return any(g.parent == f.parent and g not in park and re.search(r"(?<![\w.])%s\b" % re.escape(simple), now(g))
               for g in f.parent.glob("*.java"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work"); ap.add_argument("--group", help="the mod's root package (default: from gradle.properties)")
    ap.add_argument("--also-required", default="", help="mod ids to treat as required whatever the toml says (port.py "
                    "passes optional dependencies the code uses throughout, and geckolib when the build supplies it)")
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not a.work:
        ap.error("--work is required")
    work = pathlib.Path(a.work).resolve()
    group = a.group
    gp = work / "gradle.properties"
    if not group and gp.exists():
        group = (re.findall(r"(?m)^mod_group_id\s*=\s*(\S+)", gp.read_text(encoding="utf-8")) or [""])[0]
    if not group:
        print("park-optional: no root package (pass --group); refusing to guess what is the mod's own code")
        return 2
    java = work / "src/main/java"
    park, cuts = plan(work, group, [m for m in a.also_required.split(",") if m])
    parked_names = {class_name(java, f) for f in park}
    def now(f):
        return cuts[f][0] if f in cuts else f.read_text(encoding="utf-8", errors="replace")
    still = sorted({class_name(java, f) for f in java.rglob("*.java") if f not in park
                    and any(i in parked_names for i in IMPORT.findall(now(f)))})
    units = sorted(set(park.values()))
    print(f"park-optional: {len(park)} file(s) in {len(units)} unit(s)" + (" (dry run)" if a.dry_run else ""))
    for u in units:
        print(f"  {sum(1 for v in park.values() if v == u):4d}  {u}")
    for f, (_, names) in sorted(cuts.items()):
        print(f"  cut datagen wiring ({', '.join(names)}) out of {f.relative_to(java).as_posix()} (main code; kept)")
    if still:
        print(f"  {len(still)} remaining file(s) import a parked class (main code; left for the compile loop): "
              + ", ".join(s.rsplit('.', 1)[-1] for s in still[:8]))
    if a.dry_run or not (park or cuts):
        return 0
    for f, (new, _) in cuts.items():
        f.write_text(new, encoding="utf-8")
    dest = work / "parked/src/main/java"
    texts = {f: now(f) for f in park}
    stubbed = {}
    for f, text in texts.items():           # X168: main code still names it -> a no-op stub keeps the name
        if class_name(java, f) in {i for g in java.rglob("*.java") if g not in park for i in IMPORT.findall(now(g))} \
                or _same_pkg_user(java, f, park, now):
            stubbed[f] = class_name(java, f).rsplit(".", 1)[-1]
    own = {group, group.rsplit(".", 1)[0]}
    tree = {g.relative_to(java).parent.as_posix().replace("/", ".") for g in java.rglob("*.java") if g not in park}
    tree |= classpath_packages(work)
    req = required_roots(work) | {m.replace("-", "").replace("_", "").lower() for m in a.also_required.split(",") if m}
    unsafe = unsafe_names(java, texts, own, req, tree, set(stubbed.values()))
    stubs = {f: stub_for(texts[f], s, unsafe) for f, s in stubbed.items()}
    for f in park:
        t = dest / f.relative_to(java)
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), str(t))
    for d in sorted(java.rglob("*"), reverse=True):     # drop the directories parking emptied
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    made = []
    for f, src in stubs.items():
        if src:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(src, encoding="utf-8")
            made.append(class_name(java, f))
    if made:
        print(f"  {len(made)} parked class(es) main code names left as no-op stubs (X168): "
              + ", ".join(s.rsplit('.', 1)[-1] for s in made[:8]))
    pruned = prune_mixin_configs(work, parked_names)
    mig = work / "MIGRATION.md"
    lines = ["", "## Parked (tools/park-optional.py)", "",
             "Not compiled into this port; kept under `parked/` with their paths. Re-port an integration against",
             "the other mod's real API when it is wanted (CATALOG §N); datagen output already ships in resources/.", ""]
    lines += [f"- {u}: {sum(1 for v in park.values() if v == u)} file(s)" for u in units]
    lines += [f"- datagen wiring ({', '.join(n)}) cut out of {f.relative_to(java).as_posix()}, which main code needs "
              f"(the original is in decompiled-raw/)" for f, (_, n) in sorted(cuts.items())]
    if still:
        lines += ["", "Main-code files that imported a parked class: " + ", ".join(still)]
    if made:
        lines += ["", "Left as no-op STUBS so that main code compiles (X168) -- the integration still does nothing until "
                  "re-ported: " + ", ".join(made)]
    if pruned:
        lines += ["", "Parked mixins dropped from their configs (a listed mixin with no class fails at load): "
                  + ", ".join(pruned)]
        print(f"  {len(pruned)} parked mixin(s) dropped from their mixin configs")
    with open(mig, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return 0


def self_check():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        w = pathlib.Path(d)
        j = w / "src/main/java/com/ex/mymod"
        for rel, body in {
            "MyMod.java": "package com.ex.mymod;\nimport net.minecraft.world.item.Item;\nimport com.ex.mymod.integration.jei.Plug;\nclass MyMod {}",
            "integration/jei/Plug.java": "package com.ex.mymod.integration.jei;\nimport mezz.jei.api.IModPlugin;\nclass Plug {}",
            "Entry.java": "package com.ex.mymod;\nimport top.theillusivec4.curios.api.CuriosApi;\n@Mod(\"mymod\")\nclass Entry {}",
            "../../core/lib/Thing.java": "package com.core.lib;\nclass Thing {}",
            "UsesCore.java": "package com.ex.mymod;\nimport com.core.lib.Thing;\nclass UsesCore {}",
            "integration/jei/Helper.java": "package com.ex.mymod.integration.jei;\nimport net.minecraft.world.item.Item;\nclass Helper {}",
            "integration/Shared.java": "package com.ex.mymod.integration;\nclass Shared {}",
            "data/Gen.java": "package com.ex.mymod.data;\nimport net.neoforged.neoforge.data.event.GatherDataEvent;\nclass Gen {}",
            "data/Recipes.java": "package com.ex.mymod.data;\nimport net.minecraft.data.recipes.RecipeProvider;\nclass Recipes {}",
            "lib/UsesLib.java": "package com.ex.mymod.lib;\nimport dev.reqlib.api.Thing;\nclass UsesLib {}",
            # the subscriber in event/, the providers elsewhere: found by type
            "event/DataGenEvents.java": "package com.ex.mymod.event;\nimport net.neoforged.neoforge.data.event.GatherDataEvent;\nimport com.ex.mymod.gen.ModItems;\nclass DataGenEvents { void g(GatherDataEvent e){ new ModItems(); new ModTags(); } }",
            "gen/ModItems.java": "package com.ex.mymod.gen;\nimport net.neoforged.neoforge.client.model.generators.ItemModelProvider;\npublic class ModItems extends ItemModelProvider {}",
            "gen/ModTags.java": "package com.ex.mymod.gen;\npublic class ModTags extends BaseTags {}",
            "gen/BaseTags.java": "package com.ex.mymod.gen;\nimport net.minecraft.data.tags.TagsProvider;\npublic abstract class BaseTags extends TagsProvider {}",
            "api/data/RecipeHelper.java": "package com.ex.mymod.api.data;\nimport net.minecraft.data.recipes.RecipeOutput;\npublic class RecipeHelper {}",
            "gen/ModRecipes.java": "package com.ex.mymod.gen;\nimport net.minecraft.data.recipes.RecipeProvider;\nclass ModRecipes extends RecipeProvider { void r(){ RecipeHelper.x(); } }",
            # runtime code: a builder named by a client model is NOT parked, nor is the model
            "item/CoatBuilder.java": "package com.ex.mymod.item;\nimport net.neoforged.neoforge.client.model.generators.ModelBuilder;\npublic class CoatBuilder extends ModelBuilder {}",
            "client/CoatModel.java": "package com.ex.mymod.client;\nimport com.ex.mymod.item.CoatBuilder;\nclass CoatModel { CoatBuilder b; }",
            "Reg.java": "package com.ex.mymod;\nimport com.ex.mymod.client.CoatModel;\nclass Reg { CoatModel m; }",
            # runtime constants inside the datagen package stay: main code names them
            "data/ModTagKeys.java": "package com.ex.mymod.data;\npublic class ModTagKeys { public static Object GEMS; }",
            "item/Gem.java": "package com.ex.mymod.item;\nimport com.ex.mymod.data.ModTagKeys;\nclass Gem { Object t = ModTagKeys.GEMS; }",
            # the mod's main class wires its own datagen: cut the handler, park the providers it named
            "Main.java": "package com.ex.mymod;\nimport com.ex.mymod.gen.Blocks;\nimport net.neoforged.neoforge.data.event.GatherDataEvent;\n"
                         "class Main {\n   Main(Bus b) {\n      b.addListener(this::gen);\n      Reg r = null;\n   }\n\n"
                         "   private void gen(GatherDataEvent event) {\n      String s = \"}\";\n      event.add(new Blocks());\n   }\n}\n",
            "Boot.java": "package com.ex.mymod;\nclass Boot { Main m; }",
            "gen/Blocks.java": "package com.ex.mymod.gen;\nimport net.neoforged.neoforge.client.model.generators.BlockStateProvider;\npublic class Blocks extends BlockStateProvider {}",
            # ...or registers a separate datagen subscriber: cut the registration, park the subscriber
            "Setup.java": "package com.ex.mymod;\nimport com.ex.mymod.datagen.DataGenerators;\nclass Setup {\n   void init(Bus bus) {\n"
                          "      bus.addListener(DataGenerators::gather);\n      bus.addListener(this::common);\n   }\n}\n",
            "Wire.java": "package com.ex.mymod;\nclass Wire { Setup s; }",
            "datagen/DataGenerators.java": "package com.ex.mymod.datagen;\nimport net.neoforged.neoforge.data.event.GatherDataEvent;\n"
                                           "public class DataGenerators { public static void gather(GatherDataEvent e) {} }",
            # worldgen helpers are runtime: not datagen
            "world/Feats.java": "package com.ex.mymod.world;\nimport net.minecraft.data.worldgen.placement.PlacementUtils;\nclass Feats extends PlacementUtils {}",
        }.items():
            (j / rel).parent.mkdir(parents=True, exist_ok=True)
            (j / rel).write_text(body, encoding="utf-8")
        (w / "src/main/resources/META-INF").mkdir(parents=True)
        (w / "src/main/resources/META-INF/neoforge.mods.toml").write_text(
            '[[dependencies.mymod]]\nmodId="reqlib"\ntype="required"\n[[dependencies.mymod]]\nmodId="jei"\ntype="optional"\n',
            encoding="utf-8")
        import zipfile
        (w / "libs/1.21.1").mkdir(parents=True)
        with zipfile.ZipFile(w / "libs/1.21.1/flw.jar", "w") as z:     # a wired dependency: its API is on the classpath
            z.writestr("dev/flw/api/Visual.class", b"\xca\xfe")
        (j / "client/Rend.java").write_text("package com.ex.mymod.client;\nimport dev.flw.api.Visual;\nclass Rend {}",
                                           encoding="utf-8")
        park, cuts = plan(w, "com.ex.mymod")
        got = {f.relative_to(w / "src/main/java").as_posix() for f in park}
        main = {f.name: c for f, c in cuts.items()}.get("Main.java")
        setup = {f.name: c for f, c in cuts.items()}.get("Setup.java")
        ok = setup is not None and "DataGenerators" not in setup[0] and "this::common" in setup[0] and main is not None and main[1] == ["gen"] and "GatherDataEvent" not in main[0] and "addListener" not in main[0] \
            and "gen.Blocks" not in main[0] and "Reg r = null;" in main[0] and main[0].rstrip().endswith("}") and got == {
                     "com/ex/mymod/gen/Blocks.java", "com/ex/mymod/datagen/DataGenerators.java", "com/ex/mymod/integration/jei/Plug.java", "com/ex/mymod/integration/jei/Helper.java",
                     "com/ex/mymod/data/Gen.java", "com/ex/mymod/data/Recipes.java",
                     "com/ex/mymod/event/DataGenEvents.java", "com/ex/mymod/gen/ModItems.java",
                     "com/ex/mymod/gen/ModTags.java", "com/ex/mymod/gen/BaseTags.java",
                     "com/ex/mymod/gen/ModRecipes.java", "com/ex/mymod/api/data/RecipeHelper.java"}
    with tempfile.TemporaryDirectory() as d:        # a tree per loader: the Fabric copy goes whole, NeoForge's stays
        w = pathlib.Path(d); j = w / "src/main/java"
        for rel, body in {"neoforge/com/ex/m/Entry.java": "package neoforge.com.ex.m;\n@Mod(\"m\")\nclass Entry {}",
                          "fabric/com/ex/m/Init.java": "package fabric.com.ex.m;\nimport net.fabricmc.api.ModInitializer;\nclass Init {}",
                          "fabric/com/ex/m/net/Packet.java": "package fabric.com.ex.m.net;\nclass Packet {}",
                          "com/ex/m/Common.java": "package com.ex.m;\nclass Common {}"}.items():
            (j / rel).parent.mkdir(parents=True, exist_ok=True); (j / rel).write_text(body, encoding="utf-8")
        park2, _ = plan(w, "neoforge.com.ex.m")
        got2 = {f.relative_to(j).as_posix() for f in park2}
        ok = ok and got2 == {"fabric/com/ex/m/Init.java", "fabric/com/ex/m/net/Packet.java"}
        for rel, body in {"com/ex/m/client/Hub.java": "package com.ex.m.client;\nimport net.irisshaders.iris.Iris;\nclass Hub {}",
                          "com/ex/m/A1.java": "package com.ex.m;\nclass A1 { Hub h; }", "com/ex/m/A2.java": "package com.ex.m;\nclass A2 { Hub h; }",
                          "com/ex/m/A3.java": "package com.ex.m;\nclass A3 { Hub h; }",
                          "com/ex/m/compat/iris/Plug.java": "package com.ex.m.compat.iris;\nimport net.irisshaders.iris.Iris;\nclass Plug {}",
                          "com/ex/m/B1.java": "package com.ex.m;\nclass B1 { Plug p; }", "com/ex/m/B2.java": "package com.ex.m;\nclass B2 { Plug p; }",
                          "com/ex/m/B3.java": "package com.ex.m;\nclass B3 { Plug p; }"}.items():
            (j / rel).parent.mkdir(parents=True, exist_ok=True); (j / rel).write_text(body, encoding="utf-8")
        park4, _ = plan(w, "com.ex.m")
        got4 = {f.relative_to(j).as_posix() for f in park4}
        ok = ok and "com/ex/m/client/Hub.java" not in got4 and "com/ex/m/compat/iris/Plug.java" in got4
        res = w / "src/main/resources"; res.mkdir(parents=True)
        (res / "m.mixins.json").write_text(json.dumps({"package": "com.ex.m.mixin", "mixins": ["A", "jei.B"],
                                                        "client": ["jei.C"]}), encoding="utf-8")
        got3 = prune_mixin_configs(w, {"com.ex.m.mixin.jei.B", "com.ex.m.mixin.jei.C"})
        cfg = json.loads((res / "m.mixins.json").read_text(encoding="utf-8"))
        ok = ok and cfg["mixins"] == ["A"] and cfg["client"] == [] and len(got3) == 2
    with tempfile.TemporaryDirectory() as d:     # X92: a handler-only subscriber registered by INSTANCE, in data/gen,
        w = pathlib.Path(d)                      # whose providers extend a LIBRARY's datagen base
        j = w / "src/main/java"
        for rel, body in {
                "com/ex/r/proxy/Proxy.java": "package com.ex.r.proxy;\nimport com.ex.r.data.gen.Generators;\nclass Proxy {\n"
                                             "  void a(IEventBus bus) {\n    bus.register(new Generators());\n    bus.register(this);\n  }\n}\n",
                "com/ex/r/data/gen/Generators.java": "package com.ex.r.data.gen;\nimport net.minecraftforge.data.event.GatherDataEvent;\n"
                                                    "public class Generators {\n  @SubscribeEvent\n  public void gatherData(GatherDataEvent e) {\n"
                                                    "    new Items();\n  }\n}\n",
                "com/ex/r/data/gen/Items.java": "package com.ex.r.data.gen;\nimport com.lib.core.data.client.LibItemModelProvider;\n"
                                               "class Items extends LibItemModelProvider {}\n",
                # main code that ALSO wires datagen keeps its class
                "com/ex/r/Main.java": "package com.ex.r;\nimport net.minecraftforge.data.event.GatherDataEvent;\npublic class Main {\n"
                                      "  public static final String ID = \"r\";\n  void gather(GatherDataEvent e) {\n  }\n}\n",
                "com/ex/r/Uses.java": "package com.ex.r;\nclass Uses { String s = Main.ID; }\n"}.items():
            (j / rel).parent.mkdir(parents=True, exist_ok=True); (j / rel).write_text(body, encoding="utf-8")
        park5, cuts5 = plan(w, "com.ex.r")
        got5 = {f.relative_to(j).as_posix() for f in park5}
        proxy = cuts5.get(j / "com/ex/r/proxy/Proxy.java", ("", []))[0]
        ok5 = ({"com/ex/r/data/gen/Generators.java", "com/ex/r/data/gen/Items.java"} <= got5
               and "com/ex/r/Main.java" not in got5 and "new Generators" not in proxy and "register(this)" in proxy
               and "gather(" not in cuts5.get(j / "com/ex/r/Main.java", ("gather(", []))[0])
        if not ok5:
            print("self-check X92:", sorted(got5), proxy)
        ok = ok and ok5
    with tempfile.TemporaryDirectory() as d:     # X122: a main item that only HOOKS datagen loses the hook
        w = pathlib.Path(d)
        j = w / "src/main/java"
        for rel, body in {
                "com/ex/w/data/DataGen.java": "package com.ex.w.data;\nimport net.minecraftforge.data.event.GatherDataEvent;\n"
                                              "public class DataGen {\n  public static void g(GatherDataEvent e) {\n  }\n}\n",
                "com/ex/w/data/ItemModels.java": "package com.ex.w.data;\nimport net.minecraftforge.client.model.generators.ItemModelProvider;\n"
                                                 "public class ItemModels extends ItemModelProvider {}\n",
                "com/ex/w/data/ICustomModel.java": "package com.ex.w.data;\npublic interface ICustomModel {\n"
                                                   "  void gen(ItemModels g, String name);\n}\n",
                "com/ex/w/item/Wand.java": "package com.ex.w.item;\nimport com.ex.w.data.ICustomModel;\nimport com.ex.w.data.ItemModels;\n"
                                           "import net.minecraftforge.client.model.generators.ModelFile;\n"
                                           "public class Wand extends Item implements ICustomModel {\n  int use() { return 1; }\n\n"
                                           "  @Override\n  public void gen(ItemModels g, String name) {\n    ModelFile f = null;\n  }\n}\n",
                "com/ex/w/Items.java": "package com.ex.w;\nimport com.ex.w.item.Wand;\nclass Items { Object w = new Wand(); }\n"}.items():
            (j / rel).parent.mkdir(parents=True, exist_ok=True); (j / rel).write_text(body, encoding="utf-8")
        park6, cuts6 = plan(w, "com.ex.w")
        got6 = {f.relative_to(j).as_posix() for f in park6}
        wand = cuts6.get(j / "com/ex/w/item/Wand.java", ("", []))[0]
        ok6 = ({"com/ex/w/data/ItemModels.java", "com/ex/w/data/ICustomModel.java"} <= got6
               and "public class Wand extends Item {" in wand and "gen(" not in wand and "ModelFile" not in wand
               and "int use()" in wand)
        if not ok6:
            print("self-check X122:", sorted(got6), wand)
        ok = ok and ok6
    with tempfile.TemporaryDirectory() as d:     # X168: a parked class main code still names becomes a no-op stub
        w = pathlib.Path(d)
        (w / "gradle.properties").write_text("mod_group_id=com.ex.t\n", encoding="utf-8")
        j = w / "src/main/java/com/ex/t"
        for rel, body in {
            "Main.java": "package com.ex.t;\nimport com.ex.t.terra.TerraInit;\nclass Main { void s() { if (on) TerraInit.enable(); } }\n",
            "terra/TerraInit.java": "package com.ex.t.terra;\nimport terrablender.api.Regions;\nimport java.util.List;\n"
                                    "public class TerraInit {\n   private static final String P = \"x\";\n"
                                    "   public static final int MAX = 3;\n   public static void enable() {\n      Regions.register(new Reg());\n   }\n"
                                    "   public static boolean on(List<String> l) { return true; }\n"
                                    "   public static void add(Regions r) {}\n   private static void helper() {}\n}\n",
            "terra/Reg.java": "package com.ex.t.terra;\nimport terrablender.api.Region;\nclass Reg extends Region {}\n",
            "Cfg.java": "package com.ex.t;\nimport com.ex.t.conf.Conf;\nclass Cfg { boolean b = Conf.INSTANCE.eat; }\n",
            "conf/Conf.java": "package com.ex.t.conf;\nimport me.shedaniel.autoconfig.ConfigData;\npublic class Conf implements ConfigData {\n"
                              "   public static Conf INSTANCE;\n   public boolean eat = true;\n}\n"}.items():
            (j / rel).parent.mkdir(parents=True, exist_ok=True); (j / rel).write_text(body, encoding="utf-8")
        import subprocess
        subprocess.run([sys.executable, __file__, "--work", str(w)], capture_output=True, text=True, encoding="utf-8", errors="replace")
        stub = (j / "terra/TerraInit.java").read_text(encoding="utf-8") if (j / "terra/TerraInit.java").exists() else ""
        ok7 = ("public static void enable() {\n    }" in stub and "public static boolean on(List<String> l) {\n        return false;" in stub
               and "public static final int MAX = 3;" in stub and "Regions" not in stub and "helper" not in stub
               and not (j / "terra/Reg.java").exists() and (w / "parked/src/main/java/com/ex/t/terra/TerraInit.java").exists()
               and "X168" in (w / "MIGRATION.md").read_text(encoding="utf-8")
               and not (j / "conf/Conf.java").exists())     # a stateful (config) class is never a no-op stub
        if not ok7:
            print("self-check X168:", stub)
        ok = ok and ok7
    print("self-check:", "OK" if ok else f"FAIL {sorted(got)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
