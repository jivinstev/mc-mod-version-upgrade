#!/usr/bin/env python3
"""Where did this member GO? — a grep over the real TARGET classpath, not over memory.

An era jump (catalog §V) is mostly "the name is gone, what replaced it".  Answering that by
recall is how §V6 got a plausible, confident, wrong cause; answering it from the jar takes
seconds.  Give it a member name and it prints every class in the target's own Minecraft jar
that declares something matching.

    MC_JAR=<mod>/build/moddev/artifacts/minecraft-patched-<ver>-merged.jar \\
        python3 tools/find-member.py setDayTime
    python3 tools/find-member.py --jar <any.jar> getMaxBuildHeight

The jar to point at is whatever the target actually compiles against: ModDevGradle stages it
under build/moddev/artifacts/, NeoGradle under build/neoForm/**/outputs.jar. Both work.
"""
import os, re, subprocess, sys, zipfile, concurrent.futures as cf, pathlib

DEFAULT_JAR = os.environ.get("MC_JAR", "")


def classes(jar, prefixes=("net/minecraft/", "net/neoforged/")):
    with zipfile.ZipFile(jar) as z:
        for n in z.namelist():
            if n.endswith(".class") and n.startswith(prefixes) and "$" not in n:
                yield n[:-6].replace("/", ".")


def dump(jar, batch):
    try:
        return subprocess.run(["javap", "-p", "-cp", jar, *batch],
                              capture_output=True, text=True, timeout=180, encoding="utf-8", errors="replace").stdout
    except Exception:
        return ""


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    jar = DEFAULT_JAR
    if "--jar" in sys.argv:
        jar = sys.argv[sys.argv.index("--jar") + 1]
        args = [a for a in args if a != jar]
    if not args:
        raise SystemExit(__doc__)
    needle = args[0]
    if not pathlib.Path(jar).exists():
        raise SystemExit(f"no such jar: {jar!r}\n"
                         "Pass --jar, or set MC_JAR to the target's own Minecraft jar.")

    names = list(classes(jar))
    batches = [names[i:i + 300] for i in range(0, len(names), 300)]
    pat = re.compile(rf"\b{re.escape(needle)}\b")
    hits = []
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for out in ex.map(lambda b: dump(jar, b), batches):
            cur = None
            for line in out.splitlines():
                if line and not line.startswith(" ") and ("class " in line or "interface " in line):
                    cur = line.rstrip(" {")
                elif pat.search(line):
                    hits.append((cur, line.strip()))
    if not hits:
        print(f"'{needle}' is not declared anywhere in {jar} — it is GONE, not moved.")
        print("Look for the concept, not the name (e.g. day time -> DayTimeFraction/PerTick).")
        return 1
    for owner, member in hits[:40]:
        print(f"{owner}\n    {member}")
    if len(hits) > 40:
        print(f"... and {len(hits)-40} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
