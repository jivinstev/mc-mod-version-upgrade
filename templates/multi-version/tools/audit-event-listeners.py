#!/usr/bin/env python3
"""Fail when an event listener names a type the bus will refuse at runtime.

Why this exists: NeoForge resolves a listener by its PARAMETER TYPE, and javac has no opinion
about whether that type is one the bus accepts. So an event that became ABSTRACT under a mod --
26.2 turned `RenderLevelStageEvent` into an abstract event with one subclass per stage -- leaves
source that compiles on both targets and dies at client load with

    IllegalArgumentException: Cannot register listeners for abstract class
    net.neoforged.neoforge.client.event.RenderLevelStageEvent.
    Register a listener to one of its subclasses instead!

That is X14's blind spot in its purest form: a burn-down reaching zero says nothing about
wiring, and the runtime "check" is the crash. It is also the third compile-clean registration
failure in one port (§R1 the class with no handlers, §R25 the unset id, and now this), which is
why it gets a sweep rather than a lesson.

Reads the PREPARED tree for a target (renames applied, overlays merged) and asks that target's own
jars whether each listener's parameter type is abstract -- the same discipline as
audit-mixin-targets.py: ask the jar, never remember.

    python3 tools/audit-event-listeners.py mods/<modid> --mc=<target>

Exits 1 on a finding, 2 if it checked NOTHING (X11: a check whose value depends on normally
reading zero must distinguish "I looked and found nothing" from "I looked at nothing").
"""
import glob
import os
import re
import subprocess
import sys
import zipfile

LISTENER = re.compile(
    r'@SubscribeEvent\b\s*(?:\((?:[^()]|\([^()]*\))*\))?\s*'
    r'(?:@[\w.]+\s*(?:\((?:[^()]|\([^()]*\))*\))?\s*)*'
    r'\s*(?:public|protected|private)?\s*(?:static\s+)?[@\w.<>\[\], ?]+\s+\w+\s*\(\s*'
    r'(?:final\s+)?([@\w.$]+)[\w<>\[\], ?]*\s+\w+\s*\)')
ADD_LISTENER = re.compile(r'addListener\(\s*\(\s*(?:final\s+)?([\w.$]+)\s+\w+\s*\)\s*->')


def strip_comments(src):
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c in '"\'':
            q, j = c, i + 1
            while j < n and src[j] != q:
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:min(j + 1, n)]); i = j + 1
        elif src.startswith("//", i):
            j = src.find("\n", i); j = n if j < 0 else j
            out.append(" " * (j - i)); i = j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2); j = n if j < 0 else j + 2
            out.append("".join(ch if ch == "\n" else " " for ch in src[i:j])); i = j
        else:
            out.append(c); i += 1
    return "".join(out)


def resolve(simple, src, pkg):
    """A simple (or partly qualified) type name -> a fully qualified one, from the file's imports."""
    head = simple.split(".", 1)[0]
    m = re.search(r'^import\s+(?:static\s+)?([\w.]*\.%s);' % re.escape(head), src, re.M)
    if m:
        return m.group(1) + simple[len(head):]
    return simple if "." in simple else pkg + "." + simple


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a.split("=", 1)[0]: a.split("=", 1)[1] if "=" in a else True
             for a in sys.argv[1:] if a.startswith("--")}
    ws = args[0] if args else "."
    if not os.path.isdir(ws):
        sys.exit("no such port directory: %s -- this is NOT a pass.\n"
                 "A port's DIRECTORY is not always its modId: a port in mods/spacemod may declare space_mod,\n"
                 "so a task wired with ${mod_id} rather than ${projectDir.name} lands here." % ws)
    target = flags.get("--mc")
    targets = sorted(os.path.basename(p)[:-len(".properties")]
                     for p in glob.glob(os.path.join(ws, "versions/*.properties")))
    if targets and not isinstance(target, str):
        sys.exit("%s is a multi-version port -- audit one target at a time:\n  %s"
                 % (ws, "\n  ".join("python3 tools/audit-event-listeners.py %s --mc=%s" % (ws, t)
                                    for t in targets)))

    props = {}
    if isinstance(target, str):
        path = os.path.join(ws, "versions", target + ".properties")
        if not os.path.exists(path):
            sys.exit("no such target: %s\nknown: %s" % (path, " ".join(targets)))
        for line in open(path, encoding="utf-8"):
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                props[k.strip()] = v.strip()
        root = os.path.join(ws, "build/generated/sources", props.get("overlay", ""), "java")
    else:
        root = os.path.join(ws, "src/main/java")
    if not os.path.isdir(root):
        sys.exit("no prepared tree at %s -- run a build for this target first" % root)

    # THE CLASSPATH IS GRADLE'S OWN, never reconstructed -- X25b-ii, and this tool proved the rule
    # again on its first A/B: a glob over ~/.gradle/caches picked up BOTH NeoForge versions, kept
    # whichever sorted first, and resolved the 26.2 tree against the 1.21.1 jar. Every listener then
    # looked fine, including the abstract one this exists to catch. A screen that is wrong in the
    # reassuring direction is worse than no screen, so there is deliberately no fallback: if the
    # cache is missing, run quick-typecheck.sh (which writes it) rather than guessing.
    cp_file = os.path.join(ws, "build", "qtc-classpath-%s.txt" % target) if isinstance(target, str) else None
    if not cp_file or not os.path.exists(cp_file):
        sys.exit("no resolved classpath at %s\n"
                 "  run  tools/quick-typecheck.sh <mod> %s  first (it caches Gradle's own), or a build.\n"
                 "  This tool will NOT reconstruct one: two targets share a module cache, so a\n"
                 "  hand-built path resolves the new target against the old jars and reports\n"
                 "  all-clear (catalogue X25b-ii)." % (cp_file, target))
    jars = [e.strip() for e in re.split(r"[%s\n]" % os.pathsep, open(cp_file, encoding="utf-8").read())
        if e.strip().endswith(".jar")]
    index = {}
    for j in jars:
        try:
            with zipfile.ZipFile(j) as z:
                for n in z.namelist():
                    if n.endswith(".class"):
                        index.setdefault(n[:-len(".class")].replace("/", "."), j)
        except (zipfile.BadZipFile, FileNotFoundError):
            pass
    for canary in ("net.minecraft.world.item.Item",
                   "net.neoforged.neoforge.common.NeoForge",
                   "net.neoforged.bus.api.SubscribeEvent"):
        if canary not in index:
            sys.exit("the %s classpath is missing %s -- scope is wrong, refusing to report."
                     % (target, canary))

    abstract_cache = {}

    def is_abstract(fqn):
        if fqn in abstract_cache:
            return abstract_cache[fqn]
        jar = index.get(fqn) or index.get(fqn.replace(".", "$", fqn.count(".") - 1))
        if not jar:
            abstract_cache[fqn] = None
            return None
        out = subprocess.run(["javap", "-cp", jar, fqn], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
        decl = next((l for l in out.splitlines() if " class " in l or " interface " in l), "")
        abstract_cache[fqn] = " abstract class " in decl
        return abstract_cache[fqn]

    checked, problems = 0, []
    for path in glob.glob(os.path.join(root, "**/*.java"), recursive=True):
        s = strip_comments(open(path, encoding="utf-8").read())
        pkg = (re.search(r'^package\s+([\w.]+);', s, re.M) or [None, ""])[1]
        rel = os.path.relpath(path, root)
        for simple in [m.group(1) for m in LISTENER.finditer(s)] + ADD_LISTENER.findall(s):
            fqn = resolve(simple, s, pkg)
            if not fqn.startswith(("net.neoforged.", "net.minecraft.")):
                continue          # the mod's own events are its own business
            checked += 1
            verdict = is_abstract(fqn)
            if verdict:
                problems.append((rel, simple, fqn))

    print("checked %d event listener(s) in %s" % (checked, os.path.relpath(root, ws)))
    if checked == 0:
        print("\nNO LISTENERS CHECKED -- this is NOT a pass.\n"
              "  Nothing under %s named a net.neoforged/net.minecraft event type.\n"
              "  If the mod really subscribes to none, skip this gate deliberately;\n"
              "  otherwise the scope is wrong (prepared tree, package, or target)." % root)
        return 2
    if not problems:
        print("\nno listener names an abstract event")
        return 0
    print()
    for rel, simple, fqn in problems:
        print("  %-46s %s" % (rel, simple))
        print("      %s is ABSTRACT -- the bus refuses it at load;" % fqn)
        print("      name one of its subclasses instead.")
    print("\n%d LISTENER(S) THE BUS WILL REFUSE" % len(problems))
    return 1


if __name__ == "__main__":
    sys.exit(main())
