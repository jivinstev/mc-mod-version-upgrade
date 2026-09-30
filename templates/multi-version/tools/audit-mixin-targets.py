#!/usr/bin/env python3
"""
Audit every mixin's targets against the real vanilla sources, BEFORE booting a client.

Why this exists
---------------
A mixin whose target moved is a HARD load failure (`required: true`, which is the norm), and the
compiler cannot see it: `@Inject(method = "...")` and `@Shadow` are strings and abstract stubs, so a
mixin can be completely stale and still compile clean. Gate B catches the common-side ones. The
CLIENT-side ones only surface in Gate C — one crash per client boot, several minutes each.

This finds them all in one pass, in seconds.

The trap it was written for
---------------------------
A first cut compared only method NAMES and reported "all clear" while three client mixins were
broken. Every real failure in a space-exploration mod's 1.20.4 -> 1.21.1 port was a *signature* change with the
name unchanged:

    LevelRenderer.renderClouds        one matrix   -> frustumMatrix + projectionMatrix
    EntityRenderDispatcher.renderHitbox  trailing partialTick -> four colour floats
    Camera.move / getMaxZoom          double       -> float
    PlayerRenderer.setupRotations     signature changed

So this compares PARAMETER TYPES, not names.

MC 26.x note: vanilla now writes `@Nullable` INLINE on the return type
(`public @Nullable Entity getEntityOrPart(int id)`), so the return-type character class has to admit
`@`. Without it the declaration does not parse and the tool reports "no such method in vanilla" for a
method that is right there -- a false positive, which on a check whose value depends on normally
reading zero is as corrosive as a false negative (catalogue X10).

Usage
-----
    python3 tools/audit-mixin-targets.py mods/<modid> [path/to/sources.jar] [--sources=DIR]
    python3 tools/audit-mixin-targets.py mods/<modid> --mc=<target>      # multi-version port

The sources jar defaults to the workspace's own staged Minecraft — ModDevGradle's
`build/moddev/artifacts/*-sources.jar`, else NeoGradle's `build/neoForm/*/sources.jar`.

On a MULTI-VERSION port (catalogue W) run it once PER TARGET, each against its own jar and its own
prepared tree, because the two disagree about exactly the things this tool checks:

    python3 tools/audit-mixin-targets.py mods/examplemod \
        mods/examplemod/build/moddev/artifacts/minecraft-patched-26.2.0.75-sources.jar \
        --sources=mods/examplemod/build/generated/sources/mc26/java
"""
import glob
import os
import re
import subprocess
import sys
import zipfile

# JVM descriptor -> simple type name, for comparing an @Inject descriptor against a Java declaration.
PRIMITIVES = {"V": "void", "Z": "boolean", "B": "byte", "C": "char",
              "S": "short", "I": "int", "J": "long", "F": "float", "D": "double"}


def parse_one_type(body, i):
    """Read one JVM type starting at `body[i]` -> (simple name, next index)."""
    arrays = 0
    while i < len(body) and body[i] == "[":
        arrays += 1
        i += 1
    if i >= len(body):
        return None, i
    if body[i] == "L":
        end = body.index(";", i)
        name = body[i + 1:end].split("/")[-1].split("$")[-1]
        i = end + 1
    else:
        name = PRIMITIVES.get(body[i], body[i])
        i += 1
    return name + "[]" * arrays, i


def parse_return(desc):
    """'(Lnet/minecraft/X;)Lnet/minecraft/Y;' -> 'Y'; no ')' -> None (name-only target)."""
    if ")" not in desc:
        return None
    tail = desc[desc.rindex(")") + 1:]
    if not tail:
        return None
    name, _ = parse_one_type(tail, 0)
    return name


def parse_descriptor(desc):
    """'(Lnet/minecraft/X;FDD)V' -> ['X', 'float', 'double', 'double']."""
    params, i = [], 0
    body = desc[desc.index("(") + 1:desc.rindex(")")] if "(" in desc else desc
    while i < len(body):
        arrays = 0
        while i < len(body) and body[i] == "[":
            arrays += 1
            i += 1
        if i >= len(body):
            break
        if body[i] == "L":
            end = body.index(";", i)
            name = body[i + 1:end].split("/")[-1].split("$")[-1]
            i = end + 1
        else:
            name = PRIMITIVES.get(body[i], body[i])
            i += 1
        params.append(name + "[]" * arrays)
    return params


def simple(java_type):
    """'net.minecraft.world.Foo<Bar>[]' -> 'Foo[]'; strips annotations and generics.

    The final .strip() is load-bearing and was missing. Java 8 TYPE_USE annotations sit INSIDE
    a qualified name -- 26.x vanilla writes `Direction.@Nullable Axis` -- so removing the
    annotation leaves 'Direction. Axis', and splitting on '.' yields ' Axis' with a leading
    space. That compares unequal to a mixin's plain 'Axis', which is a FALSE POSITIVE: the tool
    reports a mismatch whose two sides read identically in its own output, and the reader is
    invited to "fix" a mixin that was already right. A checker's noise costs the same trust its
    silence does (X10).
    """
    t = re.sub(r'@\w+(\([^)]*\))?', '', java_type).strip()
    arrays = t.count("[]")
    t = t.replace("[]", "").split("<")[0].strip().split(".")[-1].strip()
    return t + "[]" * arrays


def last_type(text):
    """The trailing type in a declaration head: 'public static @Nullable Map<A, B> ' -> 'Map<A, B>'.

    Walks backwards so a generic argument list containing spaces or commas stays whole -- a
    `text.split()` would return 'B>' and report every generic-returning method as a mismatch.
    """
    t = text.rstrip()
    end = len(t)
    if t.endswith("]"):
        while t[:end].rstrip().endswith("]"):
            end = t.rindex("[", 0, end)
    if t[:end].rstrip().endswith(">"):
        end = t.rindex(">", 0, end) + 1
        depth = 0
        i = end - 1
        while i >= 0:
            if t[i] == ">":
                depth += 1
            elif t[i] == "<":
                depth -= 1
                if depth == 0:
                    end = i
                    break
            i -= 1
    i = end
    while i > 0 and (t[i - 1].isalnum() or t[i - 1] in "_.$"):
        i -= 1
    return t[i:].strip()


def is_type_var(t):
    """A bare single-uppercase-letter type is a generic variable; it erases to its bound."""
    return len(t) == 1 and t.isupper()


def split_params(text):
    """Split a parameter list on the commas that are OUTSIDE angle brackets.

    A plain `text.split(",")` turns `EntityTypeTest<Entity, T> type` into two parameters and reports
    a correct mixin as a mismatch — a false positive, which on a check whose value depends on
    normally reading zero is as corrosive as a false negative (X10).
    """
    parts, depth, current = [], 0, []
    for ch in text:
        if ch == '<':
            depth += 1
        elif ch == '>':
            depth -= 1
        if ch == ',' and depth == 0:
            parts.append(''.join(current))
            current = []
        else:
            current.append(ch)
    parts.append(''.join(current))
    return [p.strip() for p in parts if p.strip()]


def same(v, w):
    """Equal, or either side is a generic type variable (which erases to its bound)."""
    return v == w or is_type_var(v) or is_type_var(w)


def matches(vanilla, want, want_return=None):
    """Same arity, each parameter equal, and — when the mixin pinned one — the RETURN type too.

    The return type is not decoration. Mixin resolves an `@Inject(method = "name(...)DESC")` by the
    WHOLE descriptor, so a vanilla method that keeps its name and parameters but changes what it
    returns is a hard apply failure that a parameters-only comparison reports as all clear. Measured:
    26.2's `SoundManager.play` went `void` -> `SoundEngine.PlayResult`, and this tool said "all
    targets match" over a mixin that killed the client at load (catalogue R17).
    """
    vanilla_return, vanilla_params = vanilla
    if len(vanilla_params) != len(want) or not all(same(v, w) for v, w in zip(vanilla_params, want)):
        return False
    return want_return is None or same(vanilla_return, want_return)


def show(decl):
    """A vanilla declaration as it reads in a report line."""
    return "%s (%s)" % (decl[0], ", ".join(decl[1]))


def strip_owner(spec):
    """Mixin's fully-qualified member form is `Lowner/internal/Name;name(args)ret`.

    ⚠ FAULT #15 (§X27, the fifteenth): `name, desc = spec.split("(", 1)` reads that as a method
    called `Lnet/minecraft/client/renderer/ItemBlockRenderTypes;getRenderLayer`, which matches
    nothing -- so a PERFECTLY GOOD mixin is reported as `no such method in vanilla`. That is the
    seventh fault's shape again (a false POSITIVE), and it costs the same trust as silence: the
    obvious next move is to "fix" a mixin that was already right. Measured on a small rendering library, whose
    ItemBlockRenderTypesMixin pins exactly that form and whose target `getRenderLayer(FluidState)`
    is present in 1.21.1 (`javap` says so).

    The owner is deliberately DISCARDED rather than checked: an explicit owner that disagrees with
    the @Mixin target is a different question, and being loose about it errs toward a false
    negative rather than crying wolf -- which is the trade §X27's ninth fault already chose.
    """
    if spec.startswith("L") and ";" in spec:
        return spec.split(";", 1)[1]
    return spec


def declarations(src, name):
    """Every declaration of `name` in vanilla -> list of (return type, parameter types)."""
    out = []
    # ⚠ FAULT #19: the access modifier is OPTIONAL. It used to be required, which made every
    # PACKAGE-PRIVATE vanilla method invisible -- `boolean isFlowerValid(BlockPos p_27897_)` on
    # Bee is declared with no modifier at all, so an @Inject into it reported "no such method in
    # vanilla" on a target that is perfectly fine. A false positive costs the same trust silence
    # does (X10), and this one had been latent since the tool was written: it only surfaced once
    # fault #18's widening reached a package-private target.
    #
    # Anchored at the START OF A LINE instead, which is what keeps it from matching a CALL:
    # Mojang-mapped sources always declare a member after nothing but indentation.
    for m in re.finditer(
            r'(?m)^[ \t]*(?:(?:public|protected|private)\s+)?'
            r'(?:static\s+|final\s+|abstract\s+|synchronized\s+|native\s+|default\s+)*'
            r'(?:<[^>]+>\s*)?([@\w.<>\[\], ?]+)\s+%s\s*\(([^)]*)\)' % re.escape(name), src):
        out.append((simple(last_type(m.group(1))),
                    [simple(p.rsplit(" ", 1)[0]) for p in split_params(m.group(2))]))
    if not out:
        out.extend(record_components(src, name))   # FAULT #21 -- see record_components
    return out


def record_components(src, name):
    """A record ACCESSOR has no declaration to find -- recover it from the record header.

    ⚠ FAULT #21, and it is a FALSE POSITIVE, which costs exactly the trust silence does (X10).
    26.2 made `ArmorMaterial` a record, so `public float toughness()` exists at runtime and is
    generated rather than written; a source-text scan finds nothing and the tool reports
    `no such method in vanilla` over a mixin that is perfectly correct. It will fire on every port
    with a mixin into a vanilla record, and the era jump turned a great many classes into records.

    Returns the same (return type, parameter types) shape `declarations` does -- a component is a
    no-argument accessor whose return type is the component's own.
    """
    out = []
    for m in re.finditer(r'(?m)^[ \t]*(?:public\s+)?record\s+\w+(?:<[^>]*>)?\s*\(([^)]*)\)', src):
        for comp in split_params(m.group(1)):
            parts = comp.strip().rsplit(" ", 1)
            if len(parts) == 2 and parts[1] == name:
                out.append((simple(last_type(parts[0])), []))
    return out


def constructors(src, cls):
    """Every CONSTRUCTOR of `cls` in vanilla -> list of (None, parameter types).

    X27 fault #14, and a FALSE POSITIVE rather than a silent pass: `@Invoker("<init>")` is how a
    mixin reaches a constructor, and declarations() cannot ever match one -- it requires a return
    type before the name, which a constructor has not got. So every constructor invoker in the
    corpus reported "no such method in vanilla", on the CANONICAL target, over mixins shipping in a
    working jar. A checker's noise costs the same trust its silence does (X10).
    """
    out = []
    # ⚠ The modifier is OPTIONAL, and that is the whole point rather than a loosening: a
    # PACKAGE-PRIVATE constructor is exactly the thing an @Invoker exists to reach (fault #9's
    # argument, one member kind over), and requiring public|protected|private read
    # `ArrayVoxelShape(DiscreteVoxelShape, DoubleList, DoubleList, DoubleList)` -- vanilla's
    # package-private overload -- as absent while matching only its protected double[] sibling,
    # so the report named a real constructor and printed the wrong signature beside it.
    # Anchored at line start so `new Foo(...)` and `this(...)`/`super(...)` cannot match, and a
    # local `Foo x = ...` cannot either (a declaration has a name between the type and the paren).
    # ⚠ X27 fault #15, and it is a FALSE POSITIVE in the other direction from #7: an ENUM
    # constructor's real JVM descriptor is NOT the one written in the source. javac prefixes it
    # with the synthetic `(String name, int ordinal)` pair, which is exactly what an
    # `@Invoker("<init>")` on an enum must declare in order to bind -- so the mixin is right and
    # a source-only reading of the constructor calls it a mismatch. Measured on a large boss mod's
    # NoteBlockInstrumentMixin: mixin `(String, int, String, Holder, Type)` against a vanilla
    # `(String, Holder, Type)` that IS the same constructor. The prefixed form is offered
    # ALONGSIDE the plain one rather than instead of it, because a mixin is free to declare
    # either and only the ordinal-carrying form is what the JVM actually holds.
    is_enum = re.search(r'(?m)^[ \t]*(?:public\s+)?enum\s+%s\b' % re.escape(cls), src) is not None
    for m in re.finditer(
            r'(?m)^[ \t]*(?:(?:public|protected|private)\s+)?(?:<[^>]+>\s*)?%s\s*\(([^)]*)\)\s*(?:throws [\w., ]+)?\{'
            % re.escape(cls), src):
        params = [simple(q.rsplit(" ", 1)[0]) for q in split_params(m.group(1))]
        out.append((None, params))
        if is_enum:
            out.append((None, ["String", "int"] + params))
    return out


def field_types(src, name):
    """Every declaration of the FIELD `name` in vanilla -> list of type strings.

    An @Accessor is NOT free to widen: Mixin resolves a field accessor by name AND descriptor, so
    a field whose TYPE changed under a stable name fails at APPLY exactly as a renamed one does --
    `No candidates were found matching lastHurtByPlayer:L.../Player;`. This exists because the
    accessor sweep used to check only that the name was present and said so in a comment, which is
    how LivingEntity.lastHurtByPlayer (Player -> EntityReference<Player> on 26.2, the same change
    ShulkerBullet.finalTarget took) reached a Gate A run behind an "all targets match".
    """
    out = []
    for m in re.finditer(
            r'^[ \t]*(?:(?:public|protected|private|static|final|transient|volatile)\s+'
            r'|@[\w.]+(?:\([^)]*\))?\s+)*'
            r'([@\w.<>\[\], ?]+?)\s+%s\s*(?=[;=])' % re.escape(name), src, re.M):
        out.append(simple(last_type(m.group(1))))
    return out



def generic(java_type):
    """Like `simple()`, but KEEPS the generic arguments (each itself simplified).

    Generics ERASE, so a @Shadow of `List<String>` binds perfectly to a vanilla `List<Component>` --
    same descriptor `Ljava/util/List;` -- the mixin applies, and nothing anywhere complains. What
    then happens is whatever you DO with it: a large boss mod's splash mixin called `add` on a field
    that 26.2 had turned into an immutable `List<Component>`, which threw from inside a reload
    listener, failed the whole initial resource reload, and left the client rendering the title
    screen forever with no crash and no log line naming the mod (CLAUDE.md V70).

    So the erasure check above answers "will it BIND"; this one answers "is it the same thing".
    """
    t = last_type(java_type).strip()
    if not t.endswith(">"):
        return simple(t)
    depth, cut = 0, None
    for k in range(len(t) - 1, -1, -1):
        if t[k] == ">":
            depth += 1
        elif t[k] == "<":
            depth -= 1
            if depth == 0:
                cut = k
                break
    if cut is None:
        return simple(t)
    base, inner = simple(t[:cut]), t[cut + 1:-1]
    args, buf, d = [], "", 0
    for ch in inner:
        if ch == "<":
            d += 1
        elif ch == ">":
            d -= 1
        if ch == "," and d == 0:
            args.append(buf)
            buf = ""
        else:
            buf += ch
    if buf.strip():
        args.append(buf)
    return "%s<%s>" % (base, ", ".join(generic(a) for a in args))


def field_generics(src, name):
    """Every declaration of the FIELD `name` in vanilla -> list of GENERIC type strings."""
    out = []
    for m in re.finditer(
            r'^[ \t]*(?:(?:public|protected|private|static|final|transient|volatile)\s+'
            r'|@[\w.]+(?:\([^)]*\))?\s+)*'
            r'([@\w.<>\[\], ?]+?)\s+%s\s*(?=[;=])' % re.escape(name), src, re.M):
        out.append(generic(m.group(1)))
    return out

def find_sources_jar(workspace):
    """Every layout this repo builds under, newest toolchain first.

    ModDevGradle (the only toolchain that builds 26.x — catalogue V2) stages Minecraft under
    `build/moddev/artifacts/`, so a tool that only knows NeoGradle's `build/neoForm/*/sources.jar`
    exits "no sources.jar found" on every multi-version port. That is not a red gate, it is a
    MISSING one, and the two look identical from a task list (X15).
    """
    for pattern in ("build/moddev/artifacts/minecraft-patched-*-sources.jar",
                    "build/moddev/artifacts/neoforge-*-sources.jar",
                    "build/neoForm/*/sources.jar"):
        found = sorted(glob.glob(os.path.join(workspace, pattern)))
        if found:
            return found[0]
    return None


def read_properties(path):
    out = {}
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def resolve_target(workspace, target):
    """A multi-version port's (prepared source root, sources jar) for one target.

    Both halves have to move together. Pointing the CANONICAL tree at the NEW target's jar is not a
    weaker check, it is a different one, and it reports every version difference in the mod as a
    broken mixin -- 18 of them here, all noise, on a check whose whole value is normally reading
    zero (X10). So this refuses to guess.
    """
    props_path = os.path.join(workspace, "versions", target + ".properties")
    if not os.path.exists(props_path):
        known = sorted(os.path.basename(p)[:-len(".properties")]
                       for p in glob.glob(os.path.join(workspace, "versions/*.properties")))
        sys.exit("unknown target %r — this port has: %s" % (target, ", ".join(known) or "(none)"))
    props = read_properties(props_path)
    mc = props.get("minecraft_version", target)
    overlay = props.get("overlay", target)
    root = os.path.join(workspace, "build/generated/sources", overlay, "java")
    if not os.path.isdir(root):
        sys.exit("no prepared sources for %s at %s — run `./gradlew -Pmc=%s compileJava` first"
                 % (target, root, target))
    # ModDevGradle names the two targets' artifacts on DIFFERENT axes -- 26.2 is
    # `minecraft-patched-26.2.0.75-sources.jar` (the Minecraft version) and 1.21.1 is
    # `neoforge-21.1.228-sources.jar` (the NeoForge version). Matching on the Minecraft version
    # alone finds one and not the other, so try both keys the properties file already carries.
    keys = [k for k in (mc, props.get("neo_version")) if k]
    jars = [j for j in glob.glob(os.path.join(workspace, "build/moddev/artifacts/*-sources.jar"))
            if any(k in os.path.basename(j) for k in keys)]
    if not jars:
        sys.exit("no sources jar for %s (looked for %s) in build/moddev/artifacts — build %s first"
                 % (target, "/".join(keys), target))
    return root, sorted(jars)[0]


def paren_units(text, opener):
    """The text inside each `<opener> ... )`, with the parens balanced QUOTE-AWARE.

    Both callers need this and neither can use a plain regex: a mixin annotation's `target` carries
    a METHOD DESCRIPTOR, so the parentheses that matter are inside a string literal --
    `target = "Lowner;name(Lx;)V"`.

    ⚠ The outer annotation blocks elsewhere in this file end on `\)\s*\n`, which is fine for
    reading `method =` and WRONG for reading `@At`s: a `@Redirect` written with a bare
    `at = @At(\n value = "INVOKE",\n target = "..."\n )` has its FIRST `)`-then-newline inside
    the @At, so `ann` stops short and the @At is never seen. Measured: two real findings in
    a large boss mod vanished when the kind check was added, purely from that truncation.
    """
    out = []
    for m in re.finditer(opener, text):
        i, depth, in_str = m.end(), 1, False
        while i < len(text) and depth:
            c = text[i]
            if in_str:
                if c == "\\":
                    i += 1
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            i += 1
        if depth == 0:
            out.append(text[m.end():i - 1])
    return out


def at_units(ann):
    """The text inside each `@At( ... )` in an annotation, one string per @At.

    Hand-rolled rather than a regex because an @At's own `target` carries a METHOD DESCRIPTOR, so
    the parentheses that matter are nested inside a string literal -- `target =
    "Lowner;name(Lx;)V"`. A balanced-paren scan that ignores anything between double quotes is the
    only thing that splits these correctly, and splitting them is what makes the kind check below
    possible: `value` and `target` have to be read from the SAME @At.
    """
    return paren_units(ann, r"@At\s*\(")


def class_jar_for(sources_jar):
    """The CLASS jar beside a sources jar — ModDevGradle stages them as a matched pair."""
    guess = re.sub(r"-sources\.jar$", "-merged.jar", sources_jar)
    return guess if os.path.exists(guess) else None


_BYTECODE_CACHE = {}


def bytecode_refs(class_jar, fqcn):
    """method name -> the set of member references its BYTECODE actually makes.

    Needed because an `@At(target = "Lowner;name(desc)ret")` names the INVOKE INSTRUCTION's owner,
    which is the receiver's STATIC type at that call site — not "a class that declares the method".
    No source-level check can answer it: 1.21.1's `FoodData.tick(Player)` calls
    `Player.heal(F)V` and 26.2's `tick(ServerPlayer)` calls `ServerPlayer.heal(F)V`, and `heal` is
    declared on `LivingEntity` in both. A declaration check that walks superclasses says "fine" on
    both targets and misses it; one that does not walk them cries wolf on both. Only the bytecode
    distinguishes them, so this reads the bytecode.

    Returns {} when javap cannot be run or the class is absent — the caller then SKIPS rather than
    reporting, because a checker that is wrong in the accusing direction costs the same trust its
    silence does (X10/X27).
    """
    key = (class_jar, fqcn)
    if key in _BYTECODE_CACHE:
        return _BYTECODE_CACHE[key]
    try:
        out = subprocess.run(["javap", "-c", "-p", "-cp", class_jar, fqcn],
                             capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        _BYTECODE_CACHE[key] = {}
        return {}
    simple_name = fqcn.rsplit(".", 1)[-1]
    refs, current, has_code = {}, None, False
    for line in out.stdout.splitlines():
        # javap lays a class out as: member DECLARATIONS at indent 2, each optionally followed by
        # `    Code:` and the instruction listing at indent 4+. A member with no Code (abstract,
        # native, or a field) must NOT be indexed, or every @At into it reports "no such call"
        # against an empty set -- a false positive, which costs the same trust silence does (X10).
        if re.match(r"^  static \{\};\s*$", line):
            current, has_code = "<clinit>", False
            continue
        m = re.match(r"^  [^=]*?([\w$.]+)\s*\([^()]*\)\s*(?:throws [\w., ]+)?;\s*$", line)
        if m:
            name = m.group(1).rsplit(".", 1)[-1]
            current = "<init>" if name == simple_name else name
            has_code = False
            continue
        if re.match(r"^  \S", line):
            current, has_code = None, False   # a field declaration or the class footer
            continue
        if current is None:
            continue
        if line.strip() == "Code:":
            has_code = True
            refs.setdefault(current, set())
            continue
        if not has_code:
            continue
        m = re.search(r"//\s+(?:Method|Field|InterfaceMethod)\s+(\S+)", line)
        if m:
            refs[current].add(m.group(1).replace('"', ""))
    _BYTECODE_CACHE[key] = refs
    return refs


def strip_comments(src):
    """Java source with comments blanked out (newlines kept, so nothing else shifts).

    Every scan below is a regex over the raw text, and a mixin's own javadoc talks ABOUT the
    annotations it uses -- a `{@code @Shadow}` in a class comment was read as a real shadow of a
    field named `Shadow`, so the tool reported a mismatch against a mixin that was already correct.
    A checker's noise costs the same trust its silence does (X27), so comments come out first.
    String and char literals are respected, because a pinned descriptor is full of slashes.
    """
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c == '"' or c == "'":
            q, j = c, i + 1
            while j < n and src[j] != q:
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:min(j + 1, n)]); i = j + 1
        elif src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i)); i = j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join(ch if ch == "\n" else " " for ch in src[i:j])); i = j
        else:
            out.append(c); i += 1
    return "".join(out)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a.split("=", 1)[0]: a.split("=", 1)[1] if "=" in a else True
             for a in sys.argv[1:] if a.startswith("--")}
    workspace = args[0] if args else "."
    targets = sorted(os.path.basename(p)[:-len(".properties")]
                     for p in glob.glob(os.path.join(workspace, "versions/*.properties")))
    if flags.get("--mc"):
        root_override, jar = resolve_target(workspace, flags["--mc"])
    else:
        root_override = None
        if targets and not flags.get("--sources"):
            sys.exit("%s is a multi-version port — audit one target at a time:\n  %s"
                     % (workspace, "\n  ".join(
                         "python3 tools/audit-mixin-targets.py %s --mc=%s" % (workspace, t)
                         for t in targets)))
        if len(args) > 1:
            jar = args[1]
        else:
            jar = find_sources_jar(workspace)
            if not jar:
                sys.exit("no sources.jar found — pass one, or run a build first")

    # A multi-version tree (catalogue W) writes each target's real source into
    # build/generated/sources/<overlay>/java: renames applied, overlays merged. That prepared tree
    # is what actually compiles and what the mixin config actually loads, so it is what to audit --
    # `src/main/java` is written in the CANONICAL version's dialect and says nothing about the other
    # target's mixins at all.
    root = flags.get("--sources") or root_override or os.path.join(workspace, "src/main/java")

    z = zipfile.ZipFile(jar)
    have = set(z.namelist())
    class_jar = class_jar_for(jar)
    problems, checked = [], 0

    # Both package spellings are in the wild -- `mixin` (Fabric/Yarn convention, and what
    # a config library uses) and `mixins`. Globbing only the plural checked ZERO files on a
    # singular-package mod and still printed "all targets match" (X27 shape #5: a scope
    # nobody asserted). The empty-scope guard below is the real fix; this is the coverage.
    candidates = set()
    for seg in ("mixin", "mixins"):
        candidates.update(glob.glob(os.path.join(root, "**/%s/**/*.java" % seg), recursive=True))
        candidates.update(glob.glob(os.path.join(root, "**/%s/*.java" % seg), recursive=True))
    for path in sorted(candidates):
        s = strip_comments(open(path).read())
        m = re.search(r'@Mixin\(\{?([\w.]+)\.class', s)
        if m:
            target = m.group(1)
            # ⚠ X27 fault #13: a target may be written FULLY QUALIFIED inline —
            # `@Mixin({net.minecraft.world.entity.animal.cow.AbstractCow.class})`. There is then no
            # import to find, so the old code `continue`d and skipped the whole FILE in silence.
            # That is not an exotic spelling: §V19/§V21 tell a §W rename table to write its
            # replacements fully qualified (a table that rewrites expressions cannot add an import),
            # so EVERY multi-version port produces this shape the moment a @Mixin target moves.
            # Measured on a ~390-file mob mod: the 26.2 tree checked 17 targets against 1.21.1's 19, and
            # `CowEntityMixin @Inject createAttributes` — the very target §V44 was fixed for — was
            # one of the two that had silently stopped being checked.
            # A package segment starts lowercase, which is what separates a fully-qualified name
            # from a nested one written `Outer.Inner.class`.
            if "." in target and target.split(".", 1)[0][:1].islower():
                entry = target.replace(".", "/") + ".java"
            else:
                imp = re.search(r'import ([\w.]+\.%s);' % re.escape(target), s)
                if not imp:
                    continue              # same-package (a mod's own class) — not a vanilla target
                entry = imp.group(1).replace(".", "/") + ".java"
                # ⚠ FAULT #20: a NESTED class named via a dotted import.
                # `@Mixin({Properties.class})` + `import ...BlockBehaviour.Properties;` resolves to
                # `.../BlockBehaviour/Properties.java`, which is not a file -- so the old code fell
                # through to the `entry not in have` guard and skipped the WHOLE FILE in silence.
                # §X27 fault #9 covers the `targets = "Outer$Inner"` STRING form; this is the
                # import form, and it is at least as common: a mixin into a vanilla Properties,
                # Builder or nested goal is written exactly this way. Measured on a ~660-file GeckoLib mob mod,
                # where BlockBehaviourMixin was the only surviving 26.2 mixin, so the tool printed
                # `checked 0 ... NO TARGETS CHECKED` -- the §X27 guard firing correctly over a
                # blindness one layer up.
                # A nested segment starts UPPERCASE, so collapse from the right while that holds
                # and search the outermost class's file, exactly as the `targets=` branch does.
                while entry not in have and "/" in entry:
                    head, tail = entry.rsplit("/", 1)
                    if not head.rsplit("/", 1)[-1][:1].isupper():
                        break
                    entry = head + ".java"
        else:
            # `@Mixin(targets = {"a.b.Outer$Inner"})` — the ONLY way to name a package-private or
            # inner class, so it is not an exotic spelling: a mod that touches a vanilla goal or a
            # nested renderer state uses it. Skipping it silently is X27's shape again (a scope
            # nobody asserted), and it cost a Gate A run here: Bee$BeePollinateGoal's shadowed
            # findNearestBlock is inlined away on 26.2, which is a mixin APPLY failure the sweep
            # reported "all targets match" over.
            #
            # The string is already fully qualified, so there is no import to resolve. We read the
            # OUTER class's file and search all of it — deliberately loose, because the member
            # could be declared on either the inner class or its enclosing one, and being loose
            # here errs toward a false NEGATIVE rather than crying wolf.
            m = re.search(r'@Mixin\s*\(\s*targets\s*=\s*\{?\s*"([\w.$]+)"', s)
            if not m:
                continue
            entry = m.group(1).split("$", 1)[0].replace(".", "/") + ".java"
        if entry not in have:
            continue                      # not a vanilla class
        src = z.read(entry).decode("utf-8", "replace")
        rel = re.split(r'/mixins?/', path)[-1]

        # ── @Inject whose target is named WITHOUT a descriptor ──
        # This used to `continue` with the comment "mixin resolves it loosely", which is half true
        # and was the tool's widest blind spot: a name-only target IS resolved by name, and Mixin
        # then validates the CALLBACK's own parameter list against the target's -- a mismatch is a
        # hard `InvalidInjectionException: Invalid descriptor ... Expected (...) but found (...)`.
        # So a name-only @Inject is exactly as checkable as a pinned one; the descriptor to compare
        # is simply the handler's parameters up to its CallbackInfo. Measured on a large boss mod:
        # 49 of its 53 @Inject targets are name-only, so this was auditing 8% of them and printing
        # "all targets match" over a 26.2 client that died at load on GameRenderer.bobView
        # (1.21.1 `(PoseStack, float)` -> 26.2 `(CameraRenderState, PoseStack)`).
        for mm in re.finditer(
                r'@Inject\b(?P<ann>(?:[^{}]|\{[^{}]*\})*?)\)\s*\n'
                r'(?:\s*@[^\n]*\n)*'
                r'\s*(?:public|protected|private)?\s*(?:static\s+)?'
                # ⚠ `[\w$]+` for the HANDLER NAME, not `\w+` -- FAULT #18, and it is fault #6 one
                # annotation over. Python's `\w` excludes `$`, and Mixin's own convention is to
                # prefix a handler `<modid>$` to avoid collisions, so the RECOMMENDED spelling was
                # the one this regex could not match. Measured on a large boss mod: 18 of its mixin
                # files use the prefix, and one injection it hid took `(DamageSource, CallbackInfo)`
                # against a target of `(ServerLevel, DamageSource)` -- a DEAD injection, silent
                # because Mixin's default `require` is 0 unless a config sets defaultRequire.
                r'[@\w.<>\[\], ?]+\s+[\w$]+\s*\((?P<params>[^)]*)\)', s):
            ann = mm.group("ann")
            spec = re.search(r'method\s*=\s*\{?\s*"([^"]+)"', ann)
            if not spec or "(" in spec.group(1):
                continue                  # no target, or a pinned descriptor the block below reads
            name = strip_owner(spec.group(1))
            # `<clinit>` joins `<init>` here: a class initializer is not a declared method in
            # source, so `declarations()` can never find one and every @Inject into a static
            # block would report "no such method in vanilla". A false positive costs the same
            # trust a false negative does (X10).
            if (name.startswith("lambda$") or name in ("<init>", "<clinit>")
                    or "*" in name):
                continue
            params = split_params(mm.group("params"))
            # Everything from the CallbackInfo onward belongs to Mixin, not to the target: the
            # CallbackInfo itself, and — with `locals = CAPTURE_*` — the captured locals after it.
            want = []
            for prm in params:
                ty = simple(prm.rsplit(" ", 1)[0])
                if ty in ("CallbackInfo", "CallbackInfoReturnable"):
                    break
                want.append(ty)
            else:
                continue                  # no CallbackInfo at all: not a shape this can read
            # ⚠ A handler that takes ONLY the CallbackInfo used to `continue` here, and that
            # skipped TWO questions where only one of them is unanswerable. "Do the parameters
            # match" genuinely needs a `want`; "is there such a method at all" does not, and it
            # is the one that costs a mod-load crash. Measured on a ~390-file mob mod: 26.2 moved
            # Cow.createAttributes UP into the new AbstractCow superclass (§V44 -- a sub-package
            # move that changed the hierarchy), and an @Inject resolves on the TARGET class and
            # never its supers, so `CowEntityMixin @Inject createAttributes` was a hard
            # `InvalidInjectionException: could not find any targets matching 'createAttributes'
            # in net/minecraft/world/entity/animal/cow/Cow` -- over a run of this tool that had
            # printed "all targets match". Same invariant as every other fault in §X27: assert
            # the SCOPE, not only the findings.
            checked += 1
            decls = declarations(src, name)
            if not decls:
                problems.append((rel, "@Inject " + name, "no such method in vanilla", ""))
            elif not want:
                pass                      # exists, and its parameters are not comparable from a
                                          # handler that carries only the CallbackInfo -- Mixin's
                                          # own leniency there is not worth guessing at
            elif not any(matches(d, want) for d in decls):
                problems.append((rel, "@Inject " + name, "(%s)" % ", ".join(want),
                                 " | ".join(show(d) for d in decls)))

        # ── @Inject / @Redirect / @ModifyVariable targets carrying an explicit descriptor ──
        # ⚠ The pattern here was `r'method = \{?"([^"]+)"'` -- single literal spaces, and the
        # quote required to follow the brace immediately. That is §X10's "a rule written against
        # the shape you IMAGINED, not the shape the formatter produced", and it is precisely the
        # shape a §W overlay produces: a decompiled shared file keeps the decompiler's one-line
        # `method = {"..."}`, while a hand-written per-target overlay wraps the descriptor onto its
        # own line. Measured on a ~320-file boss mod: the 26.2 tree checked 7 targets against
        # 1.21.1's 8, and the missing one was `ExplosionMixin @ModifyVariable explode` -- the very
        # overlay written because that descriptor changed. The name-only block above already had
        # `\s*` at every link; this one did not, and the asymmetry inside one file is the tell.
        for spec in re.findall(r'method\s*=\s*\{?\s*"([^"]+)"', s):
            if "(" not in spec:
                continue                  # name-only: handled by the block above
            name, desc = strip_owner(spec).split("(", 1)
            if name.startswith("lambda$") or name == "<init>" or "*" in name:
                continue                  # synthetic/ctor: not declared in source form
            checked += 1
            want = parse_descriptor("(" + desc)
            want_return = parse_return("(" + desc)
            decls = declarations(src, name)
            if not decls:
                problems.append((rel, name, "no such method in vanilla", ""))
            elif not any(matches(d, want, want_return) for d in decls):
                problems.append((rel, name,
                                 "%s (%s)" % (want_return or "?", ", ".join(want)),
                                 " | ".join(show(d) for d in decls)))

        # ── name-only targets of the OTHER injector annotations ──
        # FAULT #16 (§X27, the sixteenth), and it is the tenth fault one annotation over: the
        # name-only block above matches `@Inject` ALONE, so a `@ModifyVariable` / `@Redirect` /
        # `@ModifyArg` / `@ModifyConstant` named without a descriptor fell through every branch and
        # was never checked. Measured on a small rendering library: its 26.2 tree has exactly one mixin left
        # after a drop, that mixin's only annotation is a name-only `@ModifyVariable`, and the tool
        # reported `checked 0` -- the invariant firing, but only because nothing else happened to
        # be there. On a mod with a dozen other mixins the same blind spot reads as a full pass.
        #
        # Only EXISTENCE is asserted here, deliberately. These handlers do not mirror the target's
        # parameter list the way an @Inject callback does -- a @ModifyVariable handler takes the
        # VALUE first and a @Redirect handler takes the redirected call's arguments -- so comparing
        # them would manufacture the false positives fault #15 was just fixed for. "Is there such a
        # method at all" needs none of that and is the question that costs a mod-load crash, which
        # is the same split the CallbackInfo-only @Inject note above already makes.
        for mm in re.finditer(
                r'@(?:ModifyVariable|Redirect|ModifyArg|ModifyArgs|ModifyConstant|ModifyReturnValue|ModifyExpressionValue)\b'
                r'(?P<ann>(?:[^{}]|\{[^{}]*\})*?)\)\s*\n', s):
            spec = re.search(r'method\s*=\s*\{?\s*"([^"]+)"', mm.group("ann"))
            if not spec or "(" in spec.group(1):
                continue                  # no target, or a pinned descriptor the block above reads
            name = strip_owner(spec.group(1))
            if name.startswith("lambda$") or name == "<init>" or "*" in name:
                continue
            checked += 1
            if not declarations(src, name):
                problems.append((rel, "@Modify/@Redirect " + name,
                                 "no such method in vanilla", ""))

        # ── @At(target = "Lowner;name(desc)ret"): the INJECTION POINT, not the target method ──
        # FAULT #17 (§X27). Every block above asks "does the METHOD I am injecting into still
        # exist"; none of them asks "does the CALL I am injecting AT still happen, from that
        # owner". That is a separate hard failure -- `Critical injection failure: ... Scanned 0
        # target(s)` at mixin APPLY -- and it is the shape an era jump produces most quietly,
        # because the class named in the @At need not have changed at all.
        #
        # Measured, and it is the case that cost a Gate B run on a large boss mod: 26.2 narrowed
        # `FoodData.tick(Player)` to `tick(ServerPlayer)`, so the invoke inside it went from
        # `Player.heal:(F)V` to `ServerPlayer.heal:(F)V`. `heal` is declared on LivingEntity on
        # both targets and `Player` still exists, so the tool reported "all targets match".
        #
        # This reads the BYTECODE of the target method rather than the source, because that is the
        # only thing that knows the invoke's owner (see bytecode_refs). javap-absent, class-absent
        # and unparsed-shape all SKIP rather than report: a checker that is wrong in the accusing
        # direction costs the same trust its silence does (X10).
        if class_jar:
            fqcn = entry[:-len(".java")].replace("/", ".")
            refs = bytecode_refs(class_jar, fqcn)
            owner_internal = entry[:-len(".java")]
            for ann in paren_units(
                    s,
                    r'@(?:Inject|Redirect|ModifyVariable|ModifyArg|ModifyArgs|ModifyConstant'
                    r'|ModifyReturnValue|ModifyExpressionValue|WrapOperation|WrapWithCondition)'
                    r'\s*\('):
                spec = re.search(r'method\s*=\s*\{?\s*"([^"]+)"', ann)
                if not spec:
                    continue
                host = strip_owner(spec.group(1)).split("(", 1)[0]
                if host not in refs:
                    continue              # the target method itself is checked above; skip here
                for unit in at_units(ann):
                    # ⚠ Only SOME injection points consult `target` at all. `HEAD`, `RETURN`,
                    # `TAIL` and `CONSTANT` ignore it, and authors do leave a decorative one there
                    # -- a space mod's ItemInHandRendererMixin writes `value = "HEAD"` beside the
                    # target method's OWN descriptor, which this reported as a missing call until
                    # the kind was read. A checker's noise costs the same trust its silence does
                    # (X10), so the kind is read from the SAME @At the target came from.
                    kind = re.search(r'value\s*=\s*"([A-Z_]+)"', unit) \
                        or re.match(r'\s*"([A-Z_]+)"', unit)
                    if not kind or kind.group(1) not in (
                            "INVOKE", "INVOKE_ASSIGN", "INVOKE_STRING", "FIELD"):
                        continue
                    at = re.search(r'target\s*=\s*"(L[^"]+)"', unit)
                    if not at:
                        continue
                    # `Lowner;name(desc)ret` is a method; `Lowner;name:desc` is a field.
                    body = at.group(1)[1:]
                    if ";" not in body:
                        continue
                    at_owner, member = body.split(";", 1)
                    if "(" in member:
                        want = "%s.%s" % (at_owner, member.replace("(", ":(", 1))
                    elif ":" in member:
                        want = "%s.%s" % (at_owner, member)
                    else:
                        continue
                    checked += 1
                    # javap omits the owner for a reference to the target class's OWN field, so a
                    # same-class FIELD target is accepted in its bare form too.
                    bare = want.split(".", 1)[1] if at_owner == owner_internal else None
                    if want in refs[host] or (bare and bare in refs[host]):
                        continue
                    same_name = sorted(r for r in refs[host]
                                       if r.rsplit("/", 1)[-1].split(".", 1)[-1].split(":", 1)[0]
                                       == member.split("(", 1)[0].split(":", 1)[0])
                    problems.append((rel, "@At " + host,
                                     want + "  (no such call in the target's bytecode)",
                                     " | ".join(same_name) if same_name
                                     else "(no call of that name anywhere in " + host + ")"))

        # ── @Shadow FIELDS: compare the declared type against vanilla's ──
        # The same blind spot as the name-only @Inject above, one member kind over. A @Shadow field
        # is resolved by name AND descriptor exactly as an @Accessor is, so a field that moved class
        # or changed type is `InvalidMixinException: @Shadow field <name> was not located in the
        # target class` -- a hard APPLY failure, i.e. the client dies at start. Measured: 26.2 split
        # Gui into Gui + Hud and moved `level` off LevelRenderer, and this tool said "all targets
        # match" over both because it only ever read @Shadow *methods*.
        for mm in re.finditer(
                r'@Shadow\b[^\n]*\n(?:\s*@[^\n(]*\n)*'
                r'\s*(?:public|protected|private)?\s*(?:static\s+)?(?:final\s+)?'
                r'([@\w.<>\[\], ?]+?)\s+(\w+)\s*(?:=[^;]*)?;', s):
            want, name = simple(last_type(mm.group(1))), mm.group(2)
            if want in ("abstract",):
                continue
            checked += 1
            found = field_types(src, name)
            if not found:
                problems.append((rel, "@Shadow " + name, "no such field in vanilla", ""))
            elif not any(same(f, want) for f in found):
                problems.append((rel, "@Shadow " + name, want, " | ".join(found)))
            else:
                # The erasures agree, so it BINDS. Ask the second question: is it the same type?
                want_g = generic(mm.group(1))
                found_g = field_generics(src, name)
                if found_g and want_g not in found_g:
                    problems.append((rel, "@Shadow " + name + " (generics; binds, but is not the same type)",
                                     want_g, " | ".join(found_g)))

        # ── @Shadow abstract methods: compare declared parameter types ──
        for mm in re.finditer(
                r'@Shadow\b[^\n]*\n(?:\s*@[^\n]*\n)*\s*(?:public|protected|private)?\s*'
                r'(?:static\s+)?abstract\s+[@\w.<>\[\], ?]+\s+(\w+)\s*\(([^)]*)\)\s*;', s):
            name, params = mm.group(1), mm.group(2)
            checked += 1
            want = [simple(p.rsplit(" ", 1)[0]) for p in split_params(params)]
            decls = declarations(src, name)
            if not decls:
                problems.append((rel, "@Shadow " + name, "no such method in vanilla", ""))
            elif not any(matches(d, want) for d in decls):
                problems.append((rel, "@Shadow " + name, "(%s)" % ", ".join(want),
                                 " | ".join(show(d) for d in decls)))

        # ── @Invoker / @Accessor: the same blind spot one layer over, and it is worse ──
        # A stale @Inject at least NAMES the method it wants. An @Invoker names it only by
        # convention (invokeFoo -> foo) or by an annotation value, and an @Accessor by a field --
        # so a rename in vanilla leaves a mixin that compiles, reads plausibly, and fails at
        # ACCESSOR apply, taking mod loading with it. Found the hard way: SkullBlockEntity's
        # fetchGameProfile, one Gate A run after the @Inject sweep had reported all clear.
        for mm in re.finditer(
                r'@Invoker(?:\("([^"]*)"\))?\s*\n(?:\s*@[^\n]*\n)*'
                r'\s*(?:public|protected|private)?\s*(?:static\s+)?'
                r'[@\w.<>\[\], ?]+\s+([\w$]+)\s*\(([^)]*)\)\s*[;{]', s):
            explicit, declared, params = mm.group(1), mm.group(2), mm.group(3)
            declared = re.sub(r'^\w+\$', '', declared)   # strip a `<modid>$` mixin prefix
            name = explicit or re.sub(r'^(?:invoke|call)', '', declared)
            name = name[:1].lower() + name[1:] if not explicit else name
            checked += 1
            want = [simple(p.rsplit(" ", 1)[0]) for p in split_params(params)]
            if name == "<init>":
                decls = constructors(src, entry.rsplit("/", 1)[-1][:-5])
                missing = "no such constructor in vanilla"
            else:
                decls = declarations(src, name)
                missing = "no such method in vanilla"
            if not decls:
                problems.append((rel, "@Invoker " + name, missing, ""))
            elif not any(matches(d, want) for d in decls):
                problems.append((rel, "@Invoker " + name, "(%s)" % ", ".join(want),
                                 " | ".join(show(d) for d in decls)))

        for mm in re.finditer(
                r'@Accessor(?:\("([^"]*)"\))?\s*\n(?:\s*@[^\n]*\n)*'
                r'\s*(?:public|protected|private)?\s*(?:static\s+)?'
                r'([@\w.<>\[\], ?]+)\s+([\w$]+)\s*\(([^)]*)\)\s*[;{]', s):
            explicit, ret, declared, params = mm.group(1), mm.group(2), mm.group(3), mm.group(4)
            declared = re.sub(r'^\w+\$', '', declared)   # strip a `<modid>$` mixin prefix
            field = explicit or re.sub(r'^(?:get|set|is)', '', declared)
            if not explicit:
                field = field[:1].lower() + field[1:]
            checked += 1
            # A SETTER's field type is its single parameter; a GETTER's is its return type.
            args = split_params(params)
            want = simple(args[0].rsplit(" ", 1)[0]) if args else simple(last_type(ret))
            found = field_types(src, field)
            if not found:
                problems.append((rel, "@Accessor " + field, "no such field in vanilla", ""))
            elif want not in found:
                # NOT "deliberately loose about the type": Mixin resolves a field accessor by name
                # AND descriptor, so a stable name over a changed type is a hard APPLY failure.
                problems.append((rel, "@Accessor " + field, want, " | ".join(found)))
            else:
                # The erasures agree, so it BINDS -- and a stale GENERIC still compiles, because every
                # caller sees the accessor's declared type, never vanilla's. Fault #12 asked this of
                # @Shadow fields only; an accessor is the same question one member kind over.
                # Measured: a large boss mod's Brain accessor still said Map<.., Optional<..>> on 26.2,
                # whose map holds MemorySlot, so the mod put an Optional into it and the next brain
                # tick threw ClassCastException -- a SERVER crash for any world with a villager.
                want_g = generic(args[0].rsplit(" ", 1)[0]) if args else generic(ret)
                found_g = field_generics(src, field)
                if found_g and want_g not in found_g:
                    problems.append((rel, "@Accessor " + field + " (generics; binds, but is not the same type)",
                                     want_g, " | ".join(found_g)))

    print("checked %d mixin targets in %s against %s\n"
          % (checked, os.path.relpath(root, workspace) if root.startswith(workspace) else root,
             os.path.basename(jar)))
    for rel, name, mine, vanilla in problems:
        print("  %-46s %-26s" % (rel, name))
        print("      mixin:   %s" % mine)
        if vanilla:
            print("      vanilla: %s" % vanilla)
    if problems:
        print("\n%d MISMATCH(ES)" % len(problems))
        return 1
    if checked == 0:
        # A green over an empty scope is the failure this tool exists to stop being blind
        # to -- see CLAUDE.md X27. Say so, and fail, rather than printing a pass.
        print("\nNO TARGETS CHECKED -- this is NOT a pass.\n"
              "  Nothing under %s matched a mixin package with a vanilla @Mixin target.\n"
              "  If the mod really ships no mixins, skip this gate deliberately;\n"
              "  otherwise the scope is wrong (package name, --sources root, or wrong target)."
              % (os.path.relpath(root, workspace) if root.startswith(workspace) else root))
        return 2
    print("\nall targets match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
