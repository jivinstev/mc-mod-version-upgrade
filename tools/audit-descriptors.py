#!/usr/bin/env python3
"""Vanilla descriptors kept as STRING literals outside the mixins -- coremods, ASM transformers, reflection -- checked
against the target version's sources jar (CATALOG V118, M65).

    python3 tools/audit-descriptors.py <repo> [sources.jar] [--sources=DIR[,DIR...]]
    python3 tools/audit-descriptors.py --self-check

A transformer names what it patches in strings: `"net/minecraft/world/level/Level"`, `"(Lnet/minecraft/...;)V"`,
`"Lnet/minecraft/...Level;getMaxBuildHeight()I"`, `Class.forName("net.minecraft....")`. No compiler sees them, the
class-move rows rewrite dotted names in code (never a slash descriptor inside a string), and a clean merge carries
them from the version below unchanged -- so a stale one fails only when the code runs (a client that loads the class:
Gate C), or silently when the transformer just matches nothing. tools/audit-mixin-targets.py checks the mixins' own
annotations; this checks every other Java file (a file with `@Mixin(` is left to it), with its machinery: the same
sources jar (the build's own, `find_sources_jar`), the same descriptor parsing and signature comparison.

Each literal that is a JVM internal class name, a descriptor, a member reference `Lowner;name(desc)ret` or a dotted
vanilla class name is checked when its package root (`net/minecraft/`, `com/mojang/`) is one the jar ships: the class
must exist, and a member must be declared, with that signature, in the owner or a supertype the jar has. A literal of
any other shape, or naming a mod's or library's class, is not checked. Report only: it never edits.
Exit: 0 every checked literal matches, 1 stale literals, 2 cannot run (no sources jar).
"""
import importlib.util, os, pathlib, re, sys, zipfile

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("audit_mixin_targets", HERE / "audit-mixin-targets.py")
amt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(amt)

LITERAL = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
MEMBER = re.compile(r"^L([\w/$]+);([\w$<>]+)(\(.*\).+)$")
INTERNAL = re.compile(r"^[a-z][\w]*(?:/[\w$]+)+$")
DOTTED = re.compile(r"^[a-z][\w]*(?:\.[a-z][\w]*)+\.[A-Z][\w$]*$")
DESC = re.compile(r"^\(.*\).+$|^(?:\[*L[\w/$]+;)+$")
EXTENDS = re.compile(r"\b(?:class|interface|record|enum)\s+\w+(?:<[^{]*?>)?\s*(?:extends\s+([^{]+?))?\s*(?:implements\s+([^{]+?))?\s*\{")


def blank_comments(text):
    """`text` with comments blanked (strings kept, offsets kept): a commented-out literal is not code."""
    out, i, n, q = list(text), 0, len(text), None
    while i < n:
        c = text[i]
        if q:
            if c == "\\":
                i += 2; continue
            if c == q or c == "\n":
                q = None
            i += 1; continue
        if c in "\"'":
            q = c; i += 1; continue
        if text.startswith("//", i):
            while i < n and text[i] != "\n":
                out[i] = " "; i += 1
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if text[k] != "\n":
                    out[k] = " "
            i = j; continue
        i += 1
    return "".join(out)


class Jar:
    def __init__(self, path):
        self.z = zipfile.ZipFile(path)
        self.have = set(self.z.namelist())
        self._src = {}

    def covers(self, internal):
        return "/".join(internal.split("/")[:2]) + "/" in amt.roots_of(self.have)

    def entry(self, internal):
        e = internal.split("$", 1)[0] + ".java"
        return e if e in self.have else None

    def source(self, internal):
        e = self.entry(internal)
        if e is None:
            return None
        if e not in self._src:
            self._src[e] = self.z.read(e).decode("utf-8", "replace")
        return self._src[e]

    def supertypes(self, internal):
        """The internal names of the supertypes the owner's source declares that the jar has (imports, same package)."""
        src = self.source(internal)
        if not src:
            return []
        m = EXTENDS.search(amt.strip_comments(src))
        if not m:
            return []
        names = re.findall(r"\b([A-Z][\w]*)\b(?:<[^,{]*?>)?", " ".join(x or "" for x in m.groups()))
        pkg = internal.rsplit("/", 1)[0]
        out = []
        for n in names:
            imp = re.search(r"(?m)^import\s+([\w.]+\.%s)\s*;" % re.escape(n), src)
            cand = imp.group(1).replace(".", "/") if imp else f"{pkg}/{n}"
            if self.entry(cand):
                out.append(cand)
        return out

    def declarations(self, owner, name, depth=0, seen=None):
        """[(return, params)] of `name` in owner and its supertypes in the jar (6 levels at most)."""
        seen = seen if seen is not None else set()
        if owner in seen or depth > 6:
            return []
        seen.add(owner)
        src = self.source(owner)
        if src is None:
            return []
        simple = owner.rsplit("/", 1)[-1].split("$")[-1]
        if name == "<init>":
            got = amt.constructors(src, simple)
        else:
            got = amt.declarations(src, name)
        if got:
            return got
        return [d for s in self.supertypes(owner) for d in self.declarations(s, name, depth + 1, seen)]


def check_literal(lit, jar):
    """None (fine, or not checked), or why the literal is stale for this jar."""
    m = MEMBER.match(lit)
    if m:
        owner, name, desc = m.groups()
        if not jar.covers(owner):
            return None
        if jar.entry(owner) is None:
            return f"class {owner.replace('/', '.')} is not in this version"
        gone = amt.missing_classes(desc, jar.have)
        if gone:
            return f"its descriptor names {', '.join(gone)}, not in this version"
        if name == "<clinit>":
            return None
        decls = jar.declarations(owner, name)
        if not decls:
            return f"no method {name} in {owner.rsplit('/', 1)[-1]} or its supertypes in this version"
        want, ret = amt.parse_descriptor(desc), amt.parse_return(desc)
        if not any(amt.matches(d, want, ret) for d in decls):
            return (f"{name}{desc} matches no declaration: this version has "
                    + "; ".join(amt.show(d) for d in decls[:3]))
        return None
    if INTERNAL.match(lit) and "/" in lit:
        if not jar.covers(lit) or not lit.rsplit("/", 1)[-1][:1].isupper():
            return None
        return None if jar.entry(lit) else f"class {lit.replace('/', '.')} is not in this version"
    if DOTTED.match(lit):
        internal = lit.replace(".", "/")
        if not jar.covers(internal):
            return None
        return None if jar.entry(internal) else f"class {lit} is not in this version"
    if DESC.match(lit) and "L" in lit:
        gone = amt.missing_classes(lit, jar.have)
        return f"names {', '.join(gone)}, not in this version" if gone else None
    return None


def audit(roots, jar):
    """([(path, line, literal, why)], number of literals checked)."""
    out, checked = [], 0
    for root in roots:
        for p in sorted(pathlib.Path(root).rglob("*.java")):
            text = p.read_text(encoding="utf-8", errors="replace")
            if re.search(r"@Mixin\s*\(", text):
                continue                       # audit-mixin-targets.py's
            body = blank_comments(text)
            for m in LITERAL.finditer(body):
                lit = m.group(1)
                if not (MEMBER.match(lit) or (INTERNAL.match(lit) and "/" in lit) or DOTTED.match(lit)
                        or (DESC.match(lit) and "L" in lit)):
                    continue
                checked += 1
                why = check_literal(lit, jar)
                if why:
                    out.append((str(p), body.count("\n", 0, m.start()) + 1, lit, why))
    return out, checked


def main():
    if "--self-check" in sys.argv:
        return self_check()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a.split("=", 1)[0]: a.split("=", 1)[1] if "=" in a else True for a in sys.argv[1:] if a.startswith("--")}
    repo = args[0] if args else "."
    jar = args[1] if len(args) > 1 else amt.find_sources_jar(repo)
    if not jar or not os.path.isfile(jar):
        print("audit-descriptors: no sources jar (pass one, or build first)"); return 2
    roots = flags["--sources"].split(",") if isinstance(flags.get("--sources"), str) else \
        sorted(str(d) for d in pathlib.Path(repo).glob("src/*/java") if d.is_dir())
    found, checked = audit(roots, Jar(jar))
    for p, ln, lit, why in found:
        print(f"audit-descriptors: {os.path.relpath(p, repo)}:{ln}: \"{lit}\": {why}")
    print(f"audit-descriptors: {len(found)} stale of {checked} vanilla descriptor literal(s) checked against "
          f"{os.path.basename(jar)}")
    return 1 if found else 0


def self_check():
    import tempfile
    ok = True

    def expect(c, msg):
        nonlocal ok
        if not c:
            ok = False
            print("FAIL:", msg)
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        jp = d / "minecraft-sources.jar"
        with zipfile.ZipFile(jp, "w") as z:
            z.writestr("net/minecraft/world/level/BaseLevel.java",
                       "package net.minecraft.world.level;\npublic abstract class BaseLevel {\n    public int getHeight() {\n"
                       "        return 1;\n    }\n}\n")
            z.writestr("net/minecraft/world/level/Level.java",
                       "package net.minecraft.world.level;\nimport net.minecraft.core.BlockPos;\n"
                       "public abstract class Level extends BaseLevel {\n    public int getMaxY() {\n        return 0;\n    }\n"
                       "    public boolean isLoaded(BlockPos pos) {\n        return true;\n    }\n}\n")
            z.writestr("net/minecraft/core/BlockPos.java", "package net.minecraft.core;\npublic class BlockPos {\n}\n")
        src = d / "src/main/java/com/example/mod/coremod"
        src.mkdir(parents=True)
        (src / "ExampleTransformer.java").write_text(
            "package com.example.mod.coremod;\nclass ExampleTransformer {\n"
            "    static final String OWNER = \"net/minecraft/world/level/Level\";\n"                       # ok
            "    static final String STALE = \"Lnet/minecraft/world/level/Level;getMaxBuildHeight()I\";\n"  # gone
            "    static final String SUPER = \"Lnet/minecraft/world/level/Level;getHeight()I\";\n"          # supertype
            "    static final String SIG = \"Lnet/minecraft/world/level/Level;getMaxY(I)I\";\n"            # signature
            "    static final String MOVED = \"(Lnet/minecraft/world/level/OldPos;)V\";\n"                  # missing class
            "    static final String OK2 = \"Lnet/minecraft/world/level/Level;isLoaded(Lnet/minecraft/core/BlockPos;)Z\";\n"
            "    static final String OWN = \"com/example/mod/Own\";\n"                                     # not checked
            "    static final String LIB = \"Lcom/example/lib/Thing;run()V\";\n"                           # not checked
            "    static final Class<?> C = load(\"net.minecraft.world.level.Gone\");\n"                     # dotted, gone
            "    // \"net/minecraft/world/level/Commented\" in a comment: not code\n"
            "    static Class<?> load(String n) { return null; }\n}\n", encoding="utf-8")
        (src / "SomeMixin.java").write_text("package com.example.mod.coremod;\n@Mixin(Level.class)\nclass SomeMixin {\n"
                                            "  String s = \"net/minecraft/world/level/Gone2\";\n}\n", encoding="utf-8")
        found, checked = audit([str(d / "src/main/java")], Jar(str(jp)))
        by = {lit: why for _p, _l, lit, why in found}
        expect(checked == 9 and set(by) == {"Lnet/minecraft/world/level/Level;getMaxBuildHeight()I",
                                             "Lnet/minecraft/world/level/Level;getMaxY(I)I",
                                             "(Lnet/minecraft/world/level/OldPos;)V", "net.minecraft.world.level.Gone"}
               and "no method getMaxBuildHeight in Level" in by["Lnet/minecraft/world/level/Level;getMaxBuildHeight()I"]
               and "matches no declaration: this version has int ()" in by["Lnet/minecraft/world/level/Level;getMaxY(I)I"]
               and "OldPos" in by["(Lnet/minecraft/world/level/OldPos;)V"],
               f"stale literals: a removed method, a changed signature, a moved class, a gone dotted class; a supertype's "
               f"method, a mod's own and a library's names, a comment and a mixin file left alone: {checked} {by}")
        expect([ln for _p, ln, lit, _w in found if "getMaxBuildHeight" in lit] == [4],
               f"the finding names its line: {found}")
    print("self-check:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
