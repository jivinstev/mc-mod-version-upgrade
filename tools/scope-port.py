#!/usr/bin/env python3
"""Scope an era-jump port: diff a mod's OWN import set against a target's real classpath.

This is §V8's measurement ("count imports, not lines"), with §X25's classpath-assembly
fix and §X25b's canaries. It answers, before any code is written:

    unchanged  — costs nothing
    moved      — one row in the rename table, from the generated move map
    removed    — the actual port

WHY THE CANARIES (§X25b). X25 said to build the classpath as a SET over every jar and
assert it, and offered "count of net/neoforged classes > 0" as the check. That check
PASSES on an incomplete classpath and did: a set holding neoforge-universal and fmlloader
counted 2056 classes while the eventbus artifact was missing entirely, so SubscribeEvent,
IEventBus, Event and ICancellableEvent all read as REMOVED. Five phantom names on the work
list, from the check written to stop exactly that.

A count says the package prefix is represented. A canary says WHICH jar is missing.

    python3 tools/scope-port.py mods/<modid> --mc 26.2
"""
import argparse
import collections
import pathlib
import os
import re
import sys
import zipfile

# One known-present class per artifact of the stack. A missing canary names the jar.
CANARIES = {
    "net/minecraft/world/item/Item.class": "Minecraft (neoforge-merged / minecraft-patched)",
    "net/neoforged/neoforge/common/NeoForge.class": "neoforge-*-universal",
    "net/neoforged/fml/loading/FMLEnvironment.class": "fmlloader / loader-*",
    "net/neoforged/bus/api/SubscribeEvent.class": "bus-* (eventbus)",
    "net/neoforged/api/distmarker/Dist.class": "mergetool-*-api (distmarker)",
}

IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?((?:net\.minecraft|net\.neoforged)[\w.$]*)\s*;", re.M)


def present(imp, classes):
    """Is this import on the classpath?

    A NESTED class is Outer$Inner.class, not Outer/Inner.class, so a straight
    dot->slash conversion reports every nested import as REMOVED. Measured on
    a large boss mod's own version: 95 phantoms, of which EntityRendererProvider.Context
    alone was 33 -- and every one of them in the alarming direction, on a tool whose
    whole job is to size the work. So walk the trailing dots into '$' before giving up.
    (§P #146's inner-class subtlety, arriving in a different tool.)
    """
    parts = imp.split(".")
    for cut in range(len(parts), 0, -1):
        cand = "/".join(parts[:cut]) + ("$" + "$".join(parts[cut:]) if cut < len(parts) else "")
        if cand + ".class" in classes:
            return True
    return False


def target_versions(mod: pathlib.Path):
    """{target id -> (minecraft_version, neo_version)} from versions/*.properties."""
    out = {}
    vd = mod / "versions"
    if not vd.is_dir():
        return out
    for f in sorted(vd.glob("*.properties")):
        kv = {}
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                kv[k.strip()] = v.strip()
        out[f.name[:-len(".properties")]] = (kv.get("minecraft_version"), kv.get("neo_version"))
    return out


def classpath_jars(mod: pathlib.Path, mc: str, vers: dict):
    """Every jar the compile uses FOR THIS TARGET — and none from another target.

    Two independent traps, both of which fail SILENTLY:

    §X25 — the two toolchain layouts package differently. ModDevGradle stages 1.21.1 as ONE
    neoforge-*-merged.jar (Minecraft AND NeoForge) and 26.2 as minecraft-patched-*-merged.jar
    (Minecraft ONLY), with NeoForge arriving separately from the Gradle module cache. Both are
    called "merged"; they do not merge the same things. So glob widely.

    ...and then NARROW, because build/moddev/artifacts/ holds EVERY target ever built in this
    workspace — measured, both minecraft-patched-26.2.0.75-merged.jar and
    neoforge-21.1.228-merged.jar side by side. Unioned, a 26.2 scope resolves against 1.21.1's
    classes and reports almost nothing as removed: a silent UNDER-scope, the one direction
    §X25c says not to count on getting lucky about.

    ⚠ The two targets are named on DIFFERENT AXES (§X27 fault 2): 26.2 by the Minecraft version,
    1.21.1 by the NeoForge version. Match on either key this target's properties file carries.
    """
    mine = [v for v in vers.get(mc, ()) if v]
    if not mine:
        sys.exit(f"unknown target {mc}; known: {sorted(vers)}")
    others = {v for t, pair in vers.items() if t != mc for v in pair if v}
    others -= set(mine)

    cand = []
    for pat in ("build/moddev/artifacts/*.jar", "build/neoForm/*/steps/*/outputs.jar",
                "build/neoForm/*/*.jar"):
        cand += [p for p in mod.glob(pat) if not p.name.endswith("-sources.jar")]
    ng = pathlib.Path.home() / ".gradle/caches/modules-2/files-2.1"
    for grp in ("net.neoforged", "net.neoforged.fancymodloader"):
        g = ng / grp
        if g.is_dir():
            cand += [p for p in g.glob("**/*.jar")
                     if not p.name.endswith(("-sources.jar", "-javadoc.jar"))]

    keep, leaked = [], []
    for j in cand:
        hay = str(j)
        if any(o in hay for o in others) and not any(m in hay for m in mine):
            leaked.append(j)
            continue
        keep.append(j)
    return sorted(set(keep)), sorted(set(leaked))


def build_index(jars):
    classes = set()
    used = []
    for j in jars:
        try:
            with zipfile.ZipFile(j) as z:
                names = [n for n in z.namelist() if n.endswith(".class")]
        except Exception:
            continue
        if names:
            classes.update(names)
            used.append(j)
    return classes, used


def rename_table_names(mod: pathlib.Path, mc: str):
    """The set of names THIS TARGET's rename table already handles.

    Without this the "removed" bucket double-counts: a name the table renames is reported as work
    to do, and on a port that inherits `templates/multi-version/versions/<mc>.renames.tsv` (698+
    rows) that is most of the list. Measured on a ~660-file GeckoLib mob mod -- 43 names reported, 8 of them already
    covered, so the honest work list was 35. An instrument that OVERSTATES the work is the same
    class of fault as X25's phantom-removed jar, just in the safe-looking direction: it does not
    hide work, it hides the fact that some of it is finished.

    Its limit, stated so nobody over-reads the number: a name handled by an OVERLAY or a compat
    pair is still counted as removed, because the tool reads the SHARED tree and the table. That is
    the right scope for planning a port that has not started, and it means a FINISHED port still
    reports a small non-zero "removed" (a large boss mod: 9 names / 21 uses, all of them overlay-handled).

    Deliberately name-based rather than clever: a row's `from` field either IS the fully-qualified
    name, or the simple name (a type row), or names it inside a `member:`/`re:` pattern.
    """
    tsv = mod / "versions" / f"{mc}.renames.tsv"
    if not tsv.exists():
        return set(), None
    fq, simple = set(), set()
    for line in tsv.read_text(errors="replace", encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        f = line.split("\t")[0]
        if f.startswith("re:") or f.startswith("member:"):
            simple.add(f)          # matched by substring below
        elif "." in f:
            fq.add(f)
        else:
            simple.add(f)
    return (fq, simple), tsv


def covered_by_table(imp: str, table):
    fq, simple = table
    if imp in fq:
        return True
    name = imp.split(".")[-1]
    return any(p == name or (p.startswith(("re:", "member:")) and name in p) for p in simple)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mod")
    ap.add_argument("--mc", required=True, help="target id, e.g. 26.2 (for the report only)")
    ap.add_argument("--moves", help="TSV move map (old<TAB>new); default: the checked-in 1.21.1->26.2 map")
    ap.add_argument("--list", choices=["removed", "moved", "renamed", "unchanged"], help="print one bucket and exit")
    # WHICH TREE you scope answers a different question, and both are wanted (§X27 fault 3).
    # The SHARED tree is the whole job, and is the only honest answer before a mod is converted.
    # The PREPARED tree is what is LEFT after the rename table has run -- the burn-down's
    # import-level progress. Scoping the shared tree mid-port reports rows the table already
    # fixed (ResourceLocation alone is 97 files here), which reads as work still to do.
    ap.add_argument("--prepared", metavar="OVERLAY",
                    help="scope build/generated/sources/<OVERLAY>/java instead of src/main/java")
    a = ap.parse_args()

    mod = pathlib.Path(a.mod).resolve()
    tree = (mod / f"build/generated/sources/{a.prepared}/java") if a.prepared else (mod / "src/main/java")
    if not tree.is_dir():
        sys.exit(f"no source tree at {tree}"
                 + ("\n  Build the target first so prepare-sources materialises it." if a.prepared else ""))

    vers = target_versions(mod)
    jars, leaked = classpath_jars(mod, a.mc, vers)
    classes, used = build_index(jars)
    if not classes:
        sys.exit(f"no classes found on the classpath for {mod}\n"
                 f"  Build the target first: (cd {mod} && ./gradlew compileJava -Pmc={a.mc})")

    # ── ASSERT THE SCOPE, NOT A SYMPTOM OF IT (§X25b / §X11) ──
    missing = {c: who for c, who in CANARIES.items() if c not in classes}
    if missing:
        print(f"INCOMPLETE CLASSPATH -- this is NOT a scope, it is a wrong one.", file=sys.stderr)
        print(f"  {len(classes)} classes over {len(used)} jars, but these are absent:", file=sys.stderr)
        for c, who in missing.items():
            print(f"    {c.replace('/', '.')[:-6]}   <- {who}", file=sys.stderr)
        print("  Every import from a missing jar would read as REMOVED (a phantom on the "
              "work list).", file=sys.stderr)
        sys.exit(2)

    moves = {}
    # The move map is NOT shipped with this repository (it is derived from Mojang's mapping data);
    # generate it once with tools/build-class-move-map.py and it is cached in the workspace.
    ws = pathlib.Path(os.environ.get("MIGRATE_WORKSPACE", "~/.mc-mod-upgrade/work")).expanduser()
    mp = pathlib.Path(a.moves) if a.moves else ws / "moves" / "moves-1.21.1-to-26.2.tsv"
    if mp.is_file():
        for line in mp.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or "\t" not in line:
                continue
            old, new = line.split("\t")[:2]
            moves[old.strip()] = new.strip()

    imports = collections.Counter()
    for f in tree.rglob("*.java"):
        for m in IMPORT_RE.finditer(f.read_text(errors="replace", encoding="utf-8")):
            imports[m.group(1)] += 1

    table, tsv = rename_table_names(mod, a.mc)
    buckets = {"unchanged": [], "moved": [], "renamed": [], "removed": []}
    for imp in sorted(imports):
        if present(imp, classes):
            buckets["unchanged"].append(imp)
        elif table and covered_by_table(imp, table):
            # BEFORE the move-map check on purpose. A name can be both "the map knows where it
            # went" and "a row already says so"; reporting it as `moved` reads as work to do.
            buckets["renamed"].append(imp)
        elif imp in moves:
            buckets["moved"].append(imp)
        else:
            buckets["removed"].append(imp)

    if a.list:
        for i in buckets[a.list]:
            print(f"{imports[i]:5d}  {i}" + (f"  ->  {moves[i]}" if a.list == "moved" else ""))
        return

    total = sum(len(v) for v in buckets.values())
    files = sum(1 for _ in tree.rglob("*.java"))
    print(f"{mod.name} -> MC {a.mc}" + (f"   [PREPARED tree: what is LEFT]" if a.prepared else ""))
    print(f"  classpath: {len(classes)} classes over {len(used)} jars, all {len(CANARIES)} canaries present"
          + (f"; {len(leaked)} jar(s) from another target excluded" if leaked else ""))
    print(f"  {files} source files, {total} distinct net.minecraft/net.neoforged imports\n")
    for b, what in (("unchanged", "nothing"), ("moved", "one rename-table row each"),
                    ("renamed", f"already covered by {tsv.name}" if tsv else ""),
                    ("removed", "THE PORT")):
        if b == "renamed" and not buckets[b]:
            continue
        n = len(buckets[b])
        print(f"  {b:<10} {n:4d}  {100*n//total if total else 0:3d}%   {what}")
    if buckets["removed"]:
        print(f"  {'':<10} {'':4}       {sum(imports[i] for i in buckets['removed'])} uses")
    rem = buckets["removed"]
    if rem:
        by = collections.Counter("client/render" if ".client." in i else
                                 "neoforge" if i.startswith("net.neoforged") else "vanilla"
                                 for i in rem)
        print("\n  removed, by area: " + ", ".join(f"{k} {v}" for k, v in by.most_common()))
        print("  (--list removed for the names; --list moved to review the rename rows)")


if __name__ == "__main__":
    main()
