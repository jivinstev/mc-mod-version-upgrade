#!/usr/bin/env python3
"""Derive VENDORED.tsv -- the files our scaffolders copy into a user's own mod project -- and keep
their SPDX headers honest.

    python3 tools/gen-vendored.py            # rewrite VENDORED.tsv from the scaffolders
    python3 tools/gen-vendored.py --stamp    # also add the SPDX header wherever it is missing
    python3 tools/gen-vendored.py --check    # the GATE: change nothing, fail on any drift

WHY THIS EXISTS
    The licence boundary for this repository is not a directory, it is this manifest: whatever ends up
    COPIED into someone else's mod carries a licence with no ongoing obligation, and everything else
    carries the repository's main licence. That set cannot be guessed -- an earlier plan drew it at
    `templates/**` and was wrong in both directions -- so it is DERIVED from the code that does the
    copying, and the gate fails the moment the two disagree.

WHERE THE COPY SET COMES FROM (each rule reads the scaffolder, not a list kept beside it)
    1. The migrate-mod pipeline copies the whole single-target scaffold:
           cp -R templates/neoforge-mod/. mods/$MODID/
       read out of .claude/skills/migrate-mod/references/pipeline.md.
    2. tools/make-multiversion.sh copies individual files out of templates/multi-version/ -- every
       `"$TPL/<path>"` it names, plus the build template it renders (`{tpl}/<path>`).
    Paths a rule names that do not exist as files (a per-target table built at run time) are skipped.

THE LICENCE COLUMN
    MIT          ours, vendored -> must carry `SPDX-License-Identifier: MIT` near the top
    upstream:... not ours (Gradle's wrapper) -> must carry NO header of ours, and is never stamped
    The whole repository is MIT (LICENSE). The root LICENSE file does not travel with a copied file, so
    every MIT row carries its own two-line header -- the licence identifier and the copyright holder --
    and a copy stays self-describing wherever it lands. The holder must match LICENSE's copyright line exactly.

EXIT CODES   0 clean   1 drift or a header problem   2 could not run (a rule matched nothing)
"""
import argparse, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "VENDORED.tsv"
PIPELINE = ROOT / ".claude/skills/migrate-mod/references/pipeline.md"
MAKE_MV = ROOT / "tools/make-multiversion.sh"

def _holder():
    """(year, holder) from LICENSE's `Copyright (c) <year> <holder>` line: the one place the name lives."""
    m = re.search(r"(?m)^Copyright \(c\) (\d{4}) (.+?)\s*$", (ROOT / "LICENSE").read_text()) \
        if (ROOT / "LICENSE").is_file() else None
    if not m:
        sys.exit("gen-vendored: LICENSE has no `Copyright (c) <year> <holder>` line")
    return m.group(1), m.group(2)


YEAR, OWNER = _holder()
SPDX_MIT = "SPDX-License-Identifier: MIT"
HEADER_WINDOW = 6          # lines from the top (after a shebang) in which the header must appear

# Files that belong to Gradle, not to us. Copied with the scaffold, never relicensed or stamped.
UPSTREAM = {
    "gradlew": "upstream:Apache-2.0 (Gradle wrapper script)",
    "gradlew.bat": "upstream:Apache-2.0 (Gradle wrapper script)",
    "gradle/wrapper/gradle-wrapper.jar": "upstream:Apache-2.0 (Gradle wrapper)",
    "gradle/wrapper/gradle-wrapper.properties": "upstream:Apache-2.0 (Gradle wrapper config)",
}

COMMENT = {  # suffix -> line-comment prefix
    ".py": "#", ".sh": "#", ".command": "#", ".properties": "#", ".toml": "#",
    ".tsv": "#", ".gitignore": "#", ".gradle": "//", ".java": "//", ".example": "//", ".template": "//", ".groovy": "//",
}


def tracked():
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True,
                             text=True, check=True).stdout
    except (OSError, subprocess.SubprocessError) as e:
        # "Could not run" must never look like "found a problem" -- or like a pass.
        print(f"gen-vendored: cannot list tracked files ({e})", file=sys.stderr)
        sys.exit(2)
    return sorted(p for p in out.split("\0") if p)


def copy_set(files):
    """(path, origin) for every file a scaffolder copies. Exits 2 if a rule matches nothing."""
    rows = {}
    # Rule 1: the cp -R of the single-target scaffold.
    text = PIPELINE.read_text()
    trees = re.findall(r'cp -R (templates/[A-Za-z0-9_-]+)/\. ', text)
    if not trees:
        print("gen-vendored: pipeline.md no longer contains `cp -R templates/<x>/. ` -- the copy "
              "rule is stale, so the manifest cannot be derived.", file=sys.stderr)
        sys.exit(2)
    for tree in trees:
        hits = [f for f in files if f.startswith(tree + "/")]
        if not hits:
            print(f"gen-vendored: `cp -R {tree}` copies nothing that is tracked.", file=sys.stderr)
            sys.exit(2)
        for f in hits:
            rows[f] = f"cp -R {tree} (migrate-mod pipeline)"
    # Rule 2: individual files make-multiversion.sh copies or renders.
    mv = MAKE_MV.read_text()
    named = set(re.findall(r'"\$TPL/([^"$]+)"', mv)) | set(re.findall(r'\{tpl\}/([^"\s]+)', mv))
    # A per-target path ("$TPL/versions/$T.renames.hand.tsv") names every file that fits it.
    for pat in re.findall(r'"\$TPL/([^"]*\$T[^"]*)"', mv):
        rx = re.compile(re.escape(pat).replace(re.escape("$T"), r"[^/]+") + "$")
        named |= {f[len("templates/multi-version/"):] for f in files
                  if f.startswith("templates/multi-version/")
                  and rx.fullmatch(f[len("templates/multi-version/"):])}
    found = 0
    for rel in sorted(named):
        rel = rel.strip('"')
        path = f"templates/multi-version/{rel}"
        if path in files:
            rows[path] = "tools/make-multiversion.sh"
            found += 1
    if not found:
        print("gen-vendored: make-multiversion.sh names no tracked template file -- the copy rule "
              "is stale.", file=sys.stderr)
        sys.exit(2)
    return rows


def licence_for(path):
    for tail, lic in UPSTREAM.items():
        if path.endswith("/" + tail):
            return lic
    return "MIT"


def comment_for(path):
    name = pathlib.PurePosixPath(path).name
    for suf, c in COMMENT.items():
        if name.endswith(suf):
            return c
    return None


def has_mit(path):
    try:
        lines = (ROOT / path).read_text(errors="replace").splitlines()
    except (OSError, UnicodeDecodeError):
        return False
    start = 1 if lines and lines[0].startswith("#!") else 0
    return any(SPDX_MIT in l for l in lines[start:start + HEADER_WINDOW])


COPYRIGHT_RX = re.compile(r"SPDX-FileCopyrightText:\s*(.*?)\s*$")


def copyright_of(path):
    """The SPDX copyright holder line in a file's header window, or None."""
    try:
        lines = (ROOT / path).read_text(errors="replace").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    start = 1 if lines and lines[0].startswith("#!") else 0
    for l in lines[start:start + HEADER_WINDOW]:
        m = COPYRIGHT_RX.search(l)
        if m:
            return m.group(1)
    return None


def stamp(path):
    """Add the header, or correct a copyright line that names the wrong holder."""
    c = comment_for(path)
    if c is None:
        return f"{path}: no known comment syntax -- add a COMMENT entry or mark it upstream"
    p = ROOT / path
    lines = p.read_text().splitlines(keepends=True)
    at = 1 if lines and lines[0].startswith("#!") else 0
    want = f"SPDX-FileCopyrightText: {YEAR} {OWNER}"
    for i in range(at, min(at + HEADER_WINDOW, len(lines))):
        if "SPDX-FileCopyrightText:" in lines[i]:
            lines[i] = re.sub(r"SPDX-FileCopyrightText:.*", want, lines[i].rstrip("\n")) + "\n"
            p.write_text("".join(lines))
            return None
    header = f"{c} {SPDX_MIT}\n{c} {want}\n"
    lines.insert(at, header)
    p.write_text("".join(lines))
    return None


def render(rows):
    out = ["# VENDORED.tsv -- GENERATED by tools/gen-vendored.py; do not edit by hand.",
           "# Every file a scaffolder copies into a user's own mod project. This manifest IS the licence",
           "# The repository is MIT (LICENSE); each MIT row carries its own SPDX header so a copy stays",
           "# self-describing. upstream rows are Gradle's and carry no header of ours.",
           "path\tlicence\tcopied_by"]
    for path in sorted(rows):
        out.append(f"{path}\t{licence_for(path)}\t{rows[path]}")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="change nothing; fail on any drift")
    ap.add_argument("--stamp", action="store_true", help="add missing SPDX headers to MIT rows")
    a = ap.parse_args()

    files = tracked()
    rows = copy_set(files)
    want = render(rows)
    problems = []

    if a.check:
        have = MANIFEST.read_text() if MANIFEST.is_file() else ""
        if have != want:
            problems.append("VENDORED.tsv is out of date with what the scaffolders copy -- "
                            "run: python3 tools/gen-vendored.py")
    else:
        MANIFEST.write_text(want)

    mit_rows = {p for p in rows if licence_for(p) == "MIT"}
    want_holder = f"{YEAR} {OWNER}"
    for p in sorted(mit_rows):
        bad = None
        if not has_mit(p):
            bad = f"{p}: vendored under MIT but has no `{SPDX_MIT}` header"
        elif copyright_of(p) != want_holder:
            bad = f"{p}: copyright line says {copyright_of(p)!r}, expected {want_holder!r}"
        if bad:
            if a.stamp and not a.check:
                err = stamp(p)
                if err:
                    problems.append(err)
            else:
                problems.append(bad + " (run: python3 tools/gen-vendored.py --stamp)")
    for p in files:                                   # a copied file must never claim to be Gradle's
        if p in rows and licence_for(p) != "MIT" and has_mit(p):
            problems.append(f"{p}: an upstream (Gradle) file carries our MIT header -- remove it")

    print(f"gen-vendored: {len(rows)} vendored file(s), {len(mit_rows)} MIT, "
          f"{len(rows) - len(mit_rows)} upstream")
    if problems:
        print(f"gen-vendored: FAIL -- {len(problems)} problem(s):", file=sys.stderr)
        for pr in problems:
            print(f"  {pr}", file=sys.stderr)
        return 1
    print("gen-vendored: PASS" if a.check else "gen-vendored: wrote VENDORED.tsv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
