#!/usr/bin/env python3
"""Find methods that silently STOPPED overriding anything -- the compile-clean behaviour change.

    python3 tools/override-probe.py mods/<modid>                    # uses ./gradlew compileJava
    python3 tools/override-probe.py mods/<modid> --compile "javac -d /tmp/o @sources.txt"

WHY
    A decompiler drops @Override. When the target Minecraft changes a method's parameters, the port's
    old-shaped method still compiles -- it is just a new method that nothing calls -- and vanilla's
    default runs instead. No error, no crash, wrong behaviour. Measured on a blind replay of a small
    MCreator mod downported 1.21.4 -> 1.21.1: 16 methods in 8 blocks (light-transparency overrides)
    were dead, and the original port had shipped with all of them.

HOW
    Temporarily prefixes `@Override` to every public/protected instance method that lacks it, compiles,
    collects each "method does not override or implement a method from a supertype", then RESTORES every
    file (always, even on Ctrl-C). Each hit is either a method that should override something and no
    longer does (fix its signature), or one that never meant to (an event handler, a new helper): the
    list is for a human to read, not to apply.

LIMITS
    Only single-line method headers are probed (the decompilers used here emit them); a multi-line
    header is skipped and counted, so the report says how much it did not look at.
    Exit codes: 0 nothing orphaned, 1 candidates found, 2 could not run (compile failed for other reasons
    too many times to trust, or no sources).
"""
import argparse, pathlib, re, shlex, shutil, subprocess, sys, tempfile

HEADER = re.compile(
    r'^(?P<indent>[ \t]+)(?P<mods>(?:(?:public|protected|final|synchronized)\s+)+)'
    r'(?!static\b|abstract\b|class\b|interface\b|enum\b|record\b|new\b|return\b)'
    r'(?P<type>[\w$<>\[\]?,. ]+?)\s+(?P<name>[\w$]+)\s*\((?P<params>[^;{}]*)\)\s*(?:throws\s+[\w$., ]+)?\s*\{.*$')
SKIP_ANNOTATIONS = ("@Override", "@SubscribeEvent", "@Inject", "@Redirect", "@ModifyArg", "@ModifyVariable",
                    "@ModifyConstant", "@Overwrite", "@Shadow", "@Accessor", "@Invoker", "@WrapOperation",
                    "@GameTest", "@Test")
NO_OVERRIDE = re.compile(r'^(?P<file>.+?\.java):(?P<line>\d+): error: method does not override or implement a method from a supertype')


def preceding_annotations(lines, i):
    out, j = [], i - 1
    while j >= 0 and (lines[j].strip().startswith("@") or lines[j].strip() == "" or lines[j].strip().startswith("//")):
        out.append(lines[j].strip())
        j -= 1
    return " ".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port")
    ap.add_argument("--src", default="src/main/java")
    ap.add_argument("--compile", default="./gradlew compileJava --console=plain",
                    help="command run from the port dir (default: ./gradlew compileJava)")
    a = ap.parse_args()

    port = pathlib.Path(a.port).resolve()
    src = port / a.src
    files = sorted(src.rglob("*.java"))
    if not files:
        print(f"override-probe: no Java sources under {src}", file=sys.stderr)
        return 2

    backup = pathlib.Path(tempfile.mkdtemp(prefix="override-probe-"))
    probed, skipped_multiline, changed = {}, 0, []
    try:
        for f in files:
            text = f.read_text(errors="replace", encoding="utf-8")
            # a class with no supertype cannot override anything (beyond Object): probing it only adds
            # noise that buries the real hits. Anonymous subclasses (`new X(...) {`) count as a supertype.
            if not re.search(r'\b(extends|implements)\b|\bnew\s+[\w$.<>]+\s*\([^;]*\)\s*\{', text):
                continue
            lines = text.split("\n")
            touched = False
            for i, line in enumerate(lines):
                m = HEADER.match(line)
                if not m:
                    if re.match(r'^\s+(public|protected)\s+[^=;]*\($', line) or re.match(r'^\s+(public|protected)\s+[^=;]*\([^)]*$', line):
                        skipped_multiline += 1
                    continue
                if "@Override" in line or any(an in preceding_annotations(lines, i) for an in SKIP_ANNOTATIONS):
                    continue
                if m.group("type").strip() in ("void",) and m.group("name") == "<init>":
                    continue
                lines[i] = m.group("indent") + "@Override " + line.lstrip()
                probed[(str(f.resolve()), i + 1)] = f"{f.relative_to(port)}:{i + 1}  {m.group('name')}({m.group('params').strip()})"
                touched = True
            if touched:
                dest = backup / f.relative_to(src)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)
                changed.append(f)
                f.write_text("\n".join(lines), encoding="utf-8")
        print(f"override-probe: probing {len(probed)} method(s) in {len(changed)} file(s)"
              + (f"; {skipped_multiline} multi-line header(s) NOT probed" if skipped_multiline else ""))
        if not probed:
            return 0
        r = subprocess.run(shlex.split(a.compile), cwd=port, capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = r.stdout + r.stderr
    finally:
        for f in changed:
            shutil.copy2(backup / f.relative_to(src), f)
        shutil.rmtree(backup, ignore_errors=True)

    hits, seen = [], set()
    for line in out.splitlines():
        m = NO_OVERRIDE.match(line.strip())
        if m:
            key = (str((port / m.group("file")).resolve()), int(m.group("line")))
            if key in probed and key not in seen:
                seen.add(key)
                hits.append(probed[key])
    other = len([l for l in out.splitlines() if ": error:" in l and "method does not override" not in l])
    if other:
        print(f"override-probe: WARNING -- {other} other compile error line(s); fix those first, the compiler "
              "may have stopped before reporting every override.")
    if hits:
        print(f"override-probe: {len(hits)} method(s) override NOTHING on this target:")
        for h in hits:
            print("  " + h)
        print("  Each is a signature that changed under it (fix it and add @Override) or a method that never meant\n"
              "  to override (leave it). All files have been restored.")
        return 1
    if r.returncode and not other:
        print("override-probe: the compile failed without reporting errors; cannot trust a clean result:\n"
              + "\n".join(out.splitlines()[-10:]), file=sys.stderr)
        return 2
    print("override-probe: no orphaned overrides. All files restored.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
