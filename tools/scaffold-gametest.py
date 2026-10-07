#!/usr/bin/env python3
"""Write a port's Gate B baseline GameTest and its structure, with no model involved.

    python3 tools/scaffold-gametest.py --work <gradle project> [--force]

Writes src/main/java/<group path>/test/BaselineGameTest.java from
templates/neoforge-mod/test-templates/BaselineGameTest.java.example (package + modid substitution only:
the template walks the mod's registries and names none of its classes) and the empty floored structure
it uses, data/<modid>/structure/empty_test.nbt. Leaves an existing BaselineGameTest alone unless
--force, and never touches a port's own hand-written GameTests. Checks that build.gradle has a
gameTestServer run with the mod's namespace enabled; without it nothing runs. Standard library only.
"""
import argparse, gzip, importlib.util, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates/neoforge-mod/test-templates/BaselineGameTest.java.example"
_s = importlib.util.spec_from_file_location("ges", ROOT / "tools/gen-empty-structure.py")
ges = importlib.util.module_from_spec(_s); _s.loader.exec_module(ges)
DATAVERSION = {"1.21.1": 3955}


def props(work):
    out = {}
    for f in (work / "gradle.properties",):
        for l in f.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r'\s*([\w.]+)\s*=\s*(.*?)\s*$', l)
            if m:
                out[m.group(1)] = m.group(2)
    return out


def render(text, modid, package):
    text = text.replace("package com.example.examplemod.test;", f"package {package}.test;")
    text = text.replace("EXAMPLEMOD_BASELINE", modid.upper() + "_BASELINE")
    return text.replace("examplemod", modid)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work"); ap.add_argument("--force", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not a.work:
        ap.error("--work is required")
    work = pathlib.Path(a.work)
    p = props(work)
    modid, group = p.get("mod_id"), p.get("mod_group_id")
    if not modid or not group:
        print("scaffold-gametest: gradle.properties has no mod_id / mod_group_id", file=sys.stderr); return 2
    gradle = (work / "build.gradle").read_text(encoding="utf-8", errors="replace")
    if "gameTestServer" not in gradle or "enabledGameTestNamespaces" not in gradle:
        print("scaffold-gametest: build.gradle has no gameTestServer run with enabledGameTestNamespaces; "
              "without it no GameTest runs (pipeline.md §6b)", file=sys.stderr); return 2
    out = work / "src/main/java" / pathlib.Path(*group.split(".")) / "test/BaselineGameTest.java"
    if out.exists() and not a.force:
        print(f"scaffold-gametest: already present: {out.relative_to(work)}")
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render(TEMPLATE.read_text(encoding="utf-8"), modid, group), encoding="utf-8")
        print(f"scaffold-gametest: wrote {out.relative_to(work)}")
    nbt = work / f"src/main/resources/data/{modid}/structure/empty_test.nbt"
    if not nbt.exists():
        nbt.parent.mkdir(parents=True, exist_ok=True)
        dv = DATAVERSION.get(p.get("minecraft_version", "1.21.1"), 3955)
        with gzip.open(nbt, "wb") as f:
            f.write(ges.build(modid, "empty_test", 9, dv))
        print(f"scaffold-gametest: wrote {nbt.relative_to(work)}")
    return 0


def self_check():
    t = render(TEMPLATE.read_text(encoding="utf-8"), "my_mod", "org.me.mymod")
    ok = ("package org.me.mymod.test;" in t and "examplemod" not in t and "EXAMPLEMOD" not in t
          and '@GameTestHolder("my_mod")' in t and "MY_MOD_BASELINE" in t)
    ok &= t.count("@GameTest(") == 4
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
