#!/usr/bin/env python3
"""Refuse to publish anyone else's work. Runs over the COMMITTED tree, not the diff.

WHY THIS EXISTS
    This repository is the public half of a two-repo split. The private half holds thousands of
    files of other people's mod source, and this tool's whole job is migrating such code -- so a
    working directory here routinely contains decompiled third-party Java. Exactly one mistake
    (a stray `git add -A`) turns that into a publication, and a publication cannot be recalled.

    So the gate is not a lint. It is the thing standing between a scratch directory and somebody
    else's copyrighted source appearing under an open-source licence with our name on it.

WHY IT SCANS THE TREE AND NOT THE DIFF
    A file added in an earlier commit and pushed later is still a publication. Diff-scoped checks
    pass on exactly that case, which is the one most likely to happen during a repo split.

EXIT CODES     0 clean    1 violation found    2 could not run
A "could not run" is a FAILURE, never a pass. A gate that quietly did not run reports identically
to one that passed, and this tool guards something unrecoverable.
"""
import argparse, hashlib, pathlib, re, subprocess, sys

# --- 1. binaries -------------------------------------------------------------------------------
# A jar IS the thing we must never ship. Class files are compiled third-party code. Minecraft
# structure/sound assets are Mojang's.
BINARY_SUFFIXES = {".jar", ".class", ".zip", ".nbt", ".ogg", ".mca", ".dat", ".war", ".aar", ".so",
                   ".dll", ".dylib", ".exe"}
# Allowlisted binaries, BY SHA1 AND NOT BY NAME. Naming a file `gradle-wrapper.jar` is one rename
# away from smuggling a mod jar through a filename check, which is why the allowlist is content.
ALLOWED_BINARY_SHA1 = {
    # (populated when a gradle wrapper is actually added, with its sha1 recorded here on purpose)
}

# --- 2. third-party package roots --------------------------------------------------------------
# Allowed roots: Minecraft, the loaders, the build tooling, and our own. Anything else in an
# `import` or a path is somebody's mod until proven otherwise.
ALLOWED_PACKAGE_ROOTS = (
    "net.minecraft", "com.mojang", "net.neoforged", "net.minecraftforge", "net.fabricmc",
    "cpw.mods", "org.spongepowered",                      # loaders + mixin
    "java.", "javax.", "jdk.", "sun.", "org.jetbrains", "org.junit", "org.gradle",
    "com.google", "org.apache", "org.slf4j", "it.unimi", "org.lwjgl", "io.netty", "com.electronwill",
    "org.objectweb", "org.joml", "oshi",
)
IMPORT_RE = re.compile(r'^\s*import\s+(?:static\s+)?([a-z][A-Za-z0-9_.]*)\.[A-Z][A-Za-z0-9_]*\s*;', re.M)

# --- 3. decompiler fingerprints ----------------------------------------------------------------
# Decompiled source is the highest-risk content there is, and it announces itself.
# NOTE ON THE ODD CONSTRUCTION BELOW. Each pattern is assembled from fragments so that the literal
# marker never appears in this file's own source. The first run of this gate FAILED ON ITSELF -- it
# found Vineflower's failure marker in its own pattern list -- and the tempting fix, exempting this
# file from the scan, is the wrong one: it would create the single blind spot in the repository,
# in the file most worth hiding something in. Assembling the strings keeps this file fully scanned by its own rules.
_D = "$"
DECOMPILER_MARKERS = [
    (re.compile(re.escape(_D) + r'VF\s*:'),        "Vineflower failure marker"),
    (re.compile(r'//\s*' + re.escape(_D) + r'FF:'), "FernFlower marker"),
    (re.compile(r'/\*\s*synthet' + r'ic\s*\*/'),   "compiler-generated member comment"),
    (re.compile(r'\bsynthet' + r'ic\s+class\b'),   "compiler-generated class declaration"),
    (re.compile(re.escape(_D) + r'SwitchMap' + re.escape(_D)), "compiler-generated switch map"),
    (re.compile(r'Decompiled' + r' with CFR'),     "CFR banner"),
    (re.compile(r'was decom' + r'piled', re.I),    "decompiler banner"),
]

SKIP_DIRS = {".git", "__pycache__", ".gradle", "build", "node_modules", ".venv"}
TEXT_SUFFIXES = {".py", ".sh", ".md", ".java", ".json", ".toml", ".gradle", ".tsv", ".txt", ".yml",
                 ".yaml", ".properties", ".kts", ".cfg", ".command", ".mcmeta", ""}


def sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def tracked_files(root):
    """Files git knows about. Falls back to a walk when git is unavailable -- but says so, because
    silently scanning a different set than intended is how a gate becomes theatre."""
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                             capture_output=True, text=True, timeout=60, check=True).stdout
        return [root / p for p in out.split("\0") if p], "git ls-files"
    except (OSError, subprocess.SubprocessError):
        files = [p for p in root.rglob("*")
                 if p.is_file() and not any(d in SKIP_DIRS for d in p.relative_to(root).parts)]
        return files, "filesystem walk (git unavailable)"


def load_names(path):
    if not path:
        return None
    p = pathlib.Path(path)
    if not p.is_file():
        return None
    out = []
    for line in p.read_text(errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line.lower())
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--names", default=None,
                    help="file of third-party mod names to forbid (lives in the PRIVATE repo). "
                         "Required when --strict is set.")
    ap.add_argument("--strict", action="store_true",
                    help="fail if the name list is unavailable. Use on any push to main/CI.")
    a = ap.parse_args()

    root = pathlib.Path(a.root).resolve()
    if not root.is_dir():
        print(f"check-no-ip: no such directory: {root}", file=sys.stderr)
        return 2

    files, how = tracked_files(root)
    files = [f for f in files if f.is_file()]
    print(f"check-no-ip: {len(files)} file(s) via {how}")

    violations = []

    # 1 -------------------------------------------------------------------------------------
    for f in files:
        if f.suffix.lower() in BINARY_SUFFIXES:
            digest = sha1(f)
            if digest not in ALLOWED_BINARY_SHA1:
                violations.append((f, f"binary artefact ({f.suffix}); sha1 {digest[:12]} not allowlisted"))

    # 2 + 3 --------------------------------------------------------------------------------
    for f in files:
        if f.suffix.lower() in BINARY_SUFFIXES or f.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        for pkg in set(IMPORT_RE.findall(text)):
            if not pkg.startswith(ALLOWED_PACKAGE_ROOTS):
                violations.append((f, f"import of a non-allowlisted package root: {pkg}"))
        for rx, why in DECOMPILER_MARKERS:
            if rx.search(text):
                violations.append((f, f"decompiler fingerprint: {why}"))
                break

    # 4 -------------------------------------------------------------------------------------
    for f in files:
        parts = {p.lower() for p in f.relative_to(root).parts}
        if parts & {"decompiled", "decompiled-raw", "original", "work", "workspace"}:
            violations.append((f, "path looks like a migration working directory"))

    # 5 -------------------------------------------------------------------------------------
    names = load_names(a.names)
    if names is None:
        msg = ("name check SKIPPED — no --names list available. Pass --names <file> "
               "(the list lives in the private repo).")
        if a.strict:
            print(f"check-no-ip: FAIL — {msg}", file=sys.stderr)
            print("             --strict is set, and a skipped check must never read as a pass.",
                  file=sys.stderr)
            return 2
        print(f"check-no-ip: WARNING — {msg}")
    else:
        for f in files:
            if f.suffix.lower() in BINARY_SUFFIXES:
                continue
            try:
                low = f.read_text(errors="replace").lower()
            except OSError:
                continue
            for n in names:
                if re.search(r'\b' + re.escape(n) + r'\b', low):
                    violations.append((f, f"names a third-party mod: {n!r}"))
        print(f"check-no-ip: name check ran against {len(names)} forbidden name(s)")

    if violations:
        print(f"\ncheck-no-ip: FAIL — {len(violations)} violation(s):", file=sys.stderr)
        for f, why in violations[:40]:
            try:
                rel = f.relative_to(root)
            except ValueError:
                rel = f
            print(f"  {rel}: {why}", file=sys.stderr)
        if len(violations) > 40:
            print(f"  ... and {len(violations) - 40} more", file=sys.stderr)
        return 1

    print("check-no-ip: PASS — no third-party code, binaries, decompiler output or working dirs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
