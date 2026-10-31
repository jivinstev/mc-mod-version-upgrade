#!/usr/bin/env python3
"""A tool tier with 0 uses fed to durability(): an "unbreakable" tool that breaks on its first hit (CATALOG 177).

    python3 tools/audit-zero-uses.py <repo or source dir> [...]
    python3 tools/audit-zero-uses.py --self-check

On Forge 1.20.1 a `Tier` whose `getUses()` is 0 made an unbreakable tool. From 1.21.1 `TieredItem` passes it to
`Item.Properties.durability(0)`, and from 1.21.2 a `ToolMaterial` with durability 0 does the same: the stack gets
MAX_DAMAGE 0 and DAMAGE 0, is damageable, and the first hurtAndBreak destroys it (fine in creative). This reports, in
Java sources with comments ignored:
  - `getUses()` returning 0, or a constant this file sets to 0 (`return USES;` with `USES = 0`);
  - an enum tier's constant whose uses argument is 0 (`INF(4, 0, 9f, ...)`, `getUses()` returning the field the
    constructor sets from that parameter);
  - `new ForgeTier(level, 0, ...)`, `new SimpleTier(tag, 0, ...)`, `new ToolMaterial(tag, 0, ...)`;
  - `durability(0)` (or a 0 constant).
A file whose code uses `DataComponents.UNBREAKABLE` is skipped (the mod handles it), and a hit whose line or the line
above it carries `zero-uses-ok` (a comment saying why) is left out. Report only: whether 0 meant unbreakable is the
mod's intent. One implementation for every route: the catalogue sweep (catalog-scans.md), ci-gates.py's report row
(every kind of fork, and its Port CI) and fork-port.py's audit stage (version branches) all run this.
Output: `audit-zero-uses: <file>:<line>: <what>` per hit, then `audit-zero-uses: N finding(s)`.
Exit: 0 nothing found, 1 findings, 2 no Java sources. Standard library only.
"""
import pathlib, re, sys

MARK = "zero-uses-ok"
INT_CONST = re.compile(r"\bint\s+([A-Za-z_]\w*)\s*=\s*(-?\d+)\s*[;,]")
GET_USES = re.compile(r"\bgetUses\s*\(\s*\)\s*\{\s*return\s+([^;{}]+?)\s*;")
NEW_TIER = re.compile(r"\bnew\s+(ForgeTier|SimpleTier|ToolMaterial)\s*\(")
DURABILITY = re.compile(r"\.durability\s*\(")
ENUM = re.compile(r"\benum\s+(\w+)[^{;]*\{")
UNBREAKABLE = re.compile(r"\bDataComponents\s*\.\s*UNBREAKABLE\b")


def blank_comments(text):
    """`text` with // and /* */ comments and the insides of string and char literals blanked (newlines kept): offsets
    and line numbers stay right, and an example in a comment or a string is not code."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            out.append(c + " " * (min(j, n) - i - 1) + text[j:j + 1]); i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i)); i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join(ch if ch == "\n" else " " for ch in text[i:j])); i = j
        else:
            out.append(c); i += 1
    return "".join(out)


def call_args(text, i):
    """The top-level arguments of the call whose `(` is at text[i] (nested calls, brackets and literals kept whole).
    -> ([argument text], index after the `)`), or (None, i) when it never closes."""
    args, depth, start, j, n = [], 0, i + 1, i, len(text)
    while j < n:
        c = text[j]
        if c in "\"'":
            k = j + 1
            while k < n and text[k] != c:
                k += 2 if text[k] == "\\" else 1
            j = k + 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                args.append(text[start:j])
                return [a.strip() for a in args if a.strip() or len(args) > 1], j + 1
        elif c == "," and depth == 1:
            args.append(text[start:j]); start = j + 1
        j += 1
    return None, i


def scan_text(text):
    """[(offset, what)] for the 0-use shapes in one Java source (comments blanked)."""
    code = blank_comments(text)
    if UNBREAKABLE.search(code):
        return []
    zeros = {name for name, v in INT_CONST.findall(code) if int(v) == 0}

    def zero(expr):
        e = expr.strip()
        last = e.rsplit(".", 1)[-1]
        return e == "0" or (re.fullmatch(r"[\w.]+", e) is not None and last in zeros)

    hits = []
    for m in GET_USES.finditer(code):
        if zero(m.group(1)):
            hits.append((m.start(), "getUses() returns 0"))
            continue
        field = re.fullmatch(r"(?:this\.)?([A-Za-z_]\w*)", m.group(1).strip())
        if field:
            hits += enum_hits(code, field.group(1), zero)
    for m in NEW_TIER.finditer(code):
        args, _ = call_args(code, m.end() - 1)
        if args and len(args) > 1 and zero(args[1]):
            hits.append((m.start(), f"new {m.group(1)}(..., 0, ...): 0 uses"))
    for m in DURABILITY.finditer(code):
        args, _ = call_args(code, m.end() - 1)
        if args and len(args) == 1 and zero(args[0]):
            hits.append((m.start(), "durability(0)"))
    return sorted(set(hits))


def enum_hits(code, field, zero):
    """An enum tier whose getUses() returns `field`: the constants whose argument for the constructor parameter that
    sets `field` is 0."""
    out = []
    for e in ENUM.finditer(code):
        name, body_at = e.group(1), e.end()
        ctor = re.search(r"\b" + re.escape(name) + r"\s*\(([^()]*)\)\s*\{", code[body_at:])
        if not ctor:
            continue
        params = [p.strip().split()[-1] for p in ctor.group(1).split(",") if p.strip()]
        set_from = re.search(r"\bthis\s*\.\s*" + re.escape(field) + r"\s*=\s*([A-Za-z_]\w*)\s*;",
                             code[body_at + ctor.end():body_at + ctor.end() + 2000])
        src = set_from.group(1) if set_from else field
        if src not in params:
            continue
        idx = params.index(src)
        j, n = body_at, len(code)
        while j < n:                                    # the constants: `A(...), B(...);` before the first top-level ;
            m = re.compile(r"\s*([A-Z_][A-Z0-9_]*)\s*\(").match(code, j)
            if not m:
                break
            args, end = call_args(code, m.end() - 1)
            if args is None:
                break
            if len(args) > idx and zero(args[idx]):
                out.append((m.start(1), f"enum {name}.{m.group(1)}: 0 uses"))
            k = re.compile(r"\s*(\{)?").match(code, end)
            if k and k.group(1):                       # a constant with a class body
                _, end = call_args(code, k.end() - 1)
            sep = re.compile(r"\s*([,;])").match(code, end)
            if not sep or sep.group(1) == ";":
                break
            j = sep.end()
    return out


def scan(root):
    """[(path relative to root, line, what)] over the Java sources under `root` (its src/*/java trees when it is a
    repository, else every .java below it), sorted; a hit marked `zero-uses-ok` on its line or the one above is left out."""
    root = pathlib.Path(root)
    trees = sorted(p for p in root.glob("src/*/java") if p.is_dir()) or [root]
    out = []
    for tree in trees:
        for f in sorted(tree.rglob("*.java")):
            text = f.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            for off, what in scan_text(text):
                ln = text.count("\n", 0, off) + 1
                near = lines[max(0, ln - 2):ln]
                if any(MARK in x for x in near):
                    continue
                out.append((f.relative_to(root).as_posix(), ln, what))
    return out


def self_check():
    import tempfile
    bad = []

    def expect(c, msg):
        if not c:
            bad.append(msg)

    cases = {
        "getUses() returns 0": ("class A implements Tier {\n  public int getUses() { return 0; }\n}\n", [2]),
        "getUses() over lines": ("class A implements Tier {\n  @Override\n  public int getUses()\n  {\n    return 0;\n  }\n}\n", [3]),
        "a 0 constant": ("class A implements Tier {\n  static final int USES = 0;\n  public int getUses() { return USES; }\n}\n", [3]),
        "an enum tier constant": (
            "public enum Tiers implements Tier {\n  IRON(2, 250, 6f, 2f, 14, () -> Ingredient.of(Items.IRON_INGOT)),\n"
            "  INF(4, 0, 9f, 4f, 15, () -> Ingredient.of(Items.NETHERITE_INGOT));\n"
            "  private final int level, uses;\n  Tiers(int level, int uses, float speed, float damage, int ench, Supplier<Ingredient> r) {\n"
            "    this.level = level;\n    this.uses = uses;\n  }\n  public int getUses() { return this.uses; }\n}\n", [3]),
        "new ForgeTier": ("class A {\n  static final Tier T = new ForgeTier(4, 0, 9f, 4f, 15, BlockTags.X, () -> Ingredient.EMPTY);\n}\n", [2]),
        "new SimpleTier": ("class A {\n  static final Tier T = new SimpleTier(BlockTags.INCORRECT_FOR_NETHERITE_TOOL, 0, 9f, 4f, 15,\n"
                           "      () -> Ingredient.EMPTY);\n}\n", [2]),
        "new ToolMaterial, a nested first argument": (
            "class A {\n  static final ToolMaterial M = new ToolMaterial(TagKey.create(Registries.BLOCK,\n"
            "      ResourceLocation.parse(\"a:b\")), 0, 9f, 4f, 15, ItemTags.X);\n}\n", [2]),
        "durability(0)": ("class A {\n  Item.Properties p = new Item.Properties().durability(0);\n}\n", [2]),
        "512 uses": ("class A implements Tier {\n  public int getUses() { return 512; }\n  static final int USES = 512;\n"
                     "  Tier t = new ForgeTier(4, USES, 9f, 4f, 15, BlockTags.X, () -> Ingredient.EMPTY);\n"
                     "  Item.Properties p = new Item.Properties().durability(512);\n}\n", []),
        "an enum with real uses": (
            "enum Tiers implements Tier {\n  IRON(2, 250);\n  private final int uses;\n  Tiers(int level, int uses) { this.uses = uses; }\n"
            "  public int getUses() { return uses; }\n}\n", []),
        "comments": ("class A {\n  // public int getUses() { return 0; }\n  /* new Item.Properties().durability(0)\n"
                     "     new ForgeTier(4, 0, 9f) */\n  String s = \"getUses() { return 0; }\";\n}\n", []),
        "a file using DataComponents.UNBREAKABLE": (
            "class A implements Tier {\n  public int getUses() { return 0; }\n"
            "  Item.Properties p = new Item.Properties().component(DataComponents.UNBREAKABLE, new Unbreakable(false));\n}\n", []),
        "a zero-uses-ok marker": ("class A implements Tier {\n  // zero-uses-ok: the item sets no durability itself\n"
                                  "  public int getUses() { return 0; }\n  Item.Properties p = new Item.Properties().durability(0); // zero-uses-ok\n}\n", []),
    }
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        for i, (name, (src, want)) in enumerate(sorted(cases.items())):
            f = root / f"case{i}" / "src/main/java/com/example/mod/A.java"
            f.parent.mkdir(parents=True)
            f.write_text(src, encoding="utf-8")
            got = [ln for _p, ln, _w in scan(f.parents[6])]
            expect(got == want, f"{name}: lines {got}, want {want}")
        hits = scan(root / "case0")
        expect(all(p.startswith("src/main/java/") for p, _l, _w in hits), f"paths relative to the repository: {hits}")
        expect(main([str(root / "nothing-here")]) == 2, "no Java sources: exit 2")
    print("audit-zero-uses self-check: " + ("OK" if not bad else "FAIL\n  " + "\n  ".join(bad)))
    return 0 if not bad else 1


def main(argv):
    if argv[:1] == ["--self-check"]:
        return self_check()
    if not argv:
        print(__doc__.split("\n\n")[1])
        return 2
    found, any_java = [], False
    for a in argv:
        root = pathlib.Path(a)
        any_java |= root.is_dir() and any(root.rglob("*.java"))
        found += scan(root) if root.is_dir() else []
    if not any_java:
        print("audit-zero-uses: no Java sources")
        return 2
    for p, ln, what in found:
        print(f"audit-zero-uses: {p}:{ln}: {what} -- breaks on its first hit unless it has no durability components "
              "(CATALOG 177)")
    print(f"audit-zero-uses: {len(found)} finding(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
