#!/usr/bin/env python3
"""Write the Gate C client harness into a port from the template, with no model involved.

    python3 tools/scaffold-gatec.py --work <gradle project> [--force]

The template (templates/neoforge-mod/test-templates/ClientBootSmokeTest.java.example) says what to change
for a new mod: the package and the "examplemod" literals. Its OPEN_GUIS step is already a no-op until
someone points it at the mod's own screens, so the copy compiles as written. Reads mod_id and
mod_group_id from gradle.properties and writes src/main/java/<group path>/test/ClientBootSmokeTest.java.
Refuses to overwrite an existing harness without --force. Checks that build.gradle passes -Pboottest and
-Ptestmode through to the client (without that wiring the harness never starts and the client just sits
there, pipeline.md §6e). Standard library only.
"""
import argparse, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates/neoforge-mod/test-templates/ClientBootSmokeTest.java.example"


def props(work):
    out = {}
    for l in (work / "gradle.properties").read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r'\s*([\w.]+)\s*=\s*(.*?)\s*$', l)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def render(text, modid, package):
    text = text.replace("package com.example.examplemod.test;", f"package {package}.test;")
    text = text.replace("com.example.examplemod", package)
    text = text.replace("EXAMPLEMOD_BOOT_TEST", modid.upper() + "_BOOT_TEST")
    text = text.replace("MODID_BOOT_TEST", modid.upper() + "_BOOT_TEST")
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
        print("scaffold-gatec: gradle.properties has no mod_id / mod_group_id", file=sys.stderr); return 2
    gradle = (work / "build.gradle").read_text(encoding="utf-8", errors="replace")
    if "boottest" not in gradle or "testmode" not in gradle:
        print("scaffold-gatec: build.gradle does not pass -Pboottest/-Ptestmode to the client run; "
              "without it the harness never starts (pipeline.md §6e)", file=sys.stderr); return 2
    existing = [f for f in (work / "src/main/java").rglob("ClientBootSmokeTest.java")]
    if existing and not a.force:
        print(f"scaffold-gatec: already present: {existing[0]}"); return 0
    out = work / "src/main/java" / pathlib.Path(*group.split(".")) / "test/ClientBootSmokeTest.java"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(TEMPLATE.read_text(encoding="utf-8"), modid, group), encoding="utf-8")
    print(f"scaffold-gatec: wrote {out.relative_to(work)}")
    return 0


def self_check():
    t = render(TEMPLATE.read_text(encoding="utf-8"), "my_mod", "org.me.mymod")
    ok = ("package org.me.mymod.test;" in t and "examplemod" not in t and "EXAMPLEMOD" not in t
          and "MY_MOD_BOOT_TEST: PASS" in t and 'System.getProperty("my_mod.boottest")' in t
          and "MODID_BOOT_TEST" not in t)
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
