#!/usr/bin/env python3
"""Take a finished NeoForge 1.21.1 port to a 26.x target, deterministically, before any worker runs.

    python3 tools/era-hop.py --work <port> [--target 26.2] [--keep-old]

Run it on a port whose 1.21.1 compile is clean and whose Gate B is green (tools/port.py does): the
26.2 rename table removed 59% of start errors on finished 1.21.1 ports (docs/EVALS.md), and that is the
only state it has been measured on.

1. Frame: tools/make-multiversion.sh turns the single-target build into the ModDevGradle multi-version
   one (CATALOG §W), with a versions/<target>.properties and the composed rename table.
2. Maps: the class-move and colour maps for 1.21.1 -> <target> are generated from the two REAL compile
   classpaths (tools/build-class-move-map.py, tools/gen-color-renames.py) into
   $MIGRATE_WORKSPACE/moves/ when they are not there yet, and the table is composed again with them.
   They are Mojang's names, so they stay on this machine and are never published.
3. Flatten (the default): the table is applied to src/main/java and src/test/java IN PLACE, the target
   becomes the build's default (`mc=<target>` in gradle.properties) and the 1.21.1 target is removed,
   so the compile loop edits the files it compiles. --keep-old leaves a two-target workspace instead
   (shared tree + table + overlays, §W), which needs per-site compat decisions a worker cannot make
   reliably yet.

Prints one line per step and the rename report's dead-rule count. Exit 0 when the workspace is ready
for the compile loop; non-zero, with the failing step, otherwise. Standard library only.
"""
import argparse, os, pathlib, re, shutil, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TPL = ROOT / "templates/multi-version"


def ws_moves():
    return pathlib.Path(os.environ.get("MIGRATE_WORKSPACE") or pathlib.Path.home() / ".mc-mod-upgrade/work") / "moves"


def gradle_classpath(work, target, out):
    """One target's resolved compile classpath, via Gradle (never a hand-built one, X25b-ii). ModDevGradle
    lists its Minecraft jars on the classpath before it has staged them, so they are created first, and
    a classpath naming a file that does not exist is refused: a map built from it is silently partial."""
    subprocess.run(["./gradlew", "-I", str(ROOT / "tools/central-mirror.init.gradle"), "-q",
                    "createMinecraftArtifacts", f"-Pmc={target}", "--console=plain"], cwd=work,
                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
    r = subprocess.run(["./gradlew", "-I", str(ROOT / "tools/qtc-init.gradle"), "-I",
                        str(ROOT / "tools/central-mirror.init.gradle"), "-q", "qtcClasspath", f"-Pmc={target}",
                        "--console=plain"], cwd=work, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=3600)
    m = re.search(r"QTC_CLASSPATH_BEGIN\n(.*?)QTC_CLASSPATH_END", r.stdout, re.S)
    if not m or not m.group(1).strip():
        raise RuntimeError(f"could not resolve the {target} classpath: {(r.stdout + r.stderr)[-600:]}")
    missing = [l for l in m.group(1).splitlines() if l.strip() and not pathlib.Path(l.strip()).exists()]
    if missing:
        raise RuntimeError(f"the {target} classpath names {len(missing)} file(s) that do not exist, e.g. {missing[0]}")
    out.write_text(m.group(1), encoding="utf-8")
    return out


def step(msg):
    print(f"era-hop: {msg}", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work"); ap.add_argument("--target", default="26.2")
    ap.add_argument("--keep-old", action="store_true", help="keep 1.21.1 as a second target (no flatten)")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not a.work:
        ap.error("--work is required")
    work = pathlib.Path(a.work).resolve()
    T = a.target
    if (work / "versions/1.21.1.properties").exists() or (work / f"versions/{T}.properties").exists():
        step("already multi-version; nothing to frame");
    else:
        r = subprocess.run(["bash", str(ROOT / "tools/make-multiversion.sh"), str(work), T],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print(r.stdout[-1500:] + r.stderr[-800:]); step("FAILED building the multi-version frame"); return 3
        step(f"frame built (ModDevGradle, targets 1.21.1 + {T})")
    moves, colors = ws_moves() / f"moves-1.21.1-to-{T}.tsv", ws_moves() / f"colors-1.21.1-to-{T}.tsv"
    if not moves.exists() or not colors.exists():
        moves.parent.mkdir(parents=True, exist_ok=True)
        step(f"generating the class-move and colour maps from both classpaths (once per machine) into {moves.parent}")
        try:
            old = gradle_classpath(work, "1.21.1", work / "build/era-cp-1.21.1.txt")
            new = gradle_classpath(work, T, work / f"build/era-cp-{T}.txt")
        except RuntimeError as e:
            print(e); step("FAILED resolving a classpath"); return 4
        for tool, out in (("build-class-move-map.py", moves), ("gen-color-renames.py", colors)):
            r = subprocess.run([sys.executable, str(ROOT / "tools" / tool), "--from-cp", str(old), "--to-cp", str(new),
                                "--out", str(out)], capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0 or not out.exists():
                print(r.stdout[-800:] + r.stderr[-800:])
                for f in (moves, colors):   # never leave a half-made map behind: the next run would trust it
                    f.unlink(missing_ok=True)
                step(f"FAILED {tool}"); return 4
    table = work / f"versions/{T}.renames.tsv"
    hand = TPL / f"versions/{T}.renames.hand.tsv"
    r = subprocess.run([sys.executable, str(ROOT / "tools/compose-renames.py"), "--hand", str(hand),
                        "--generated", str(moves), "--generated", str(colors), "--out", str(table)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stdout[-800:] + r.stderr[-800:]); step("FAILED composing the rename table"); return 5
    rows = sum(1 for l in table.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#"))
    step(f"rename table: {rows} rows ({table.relative_to(work)})")
    if a.keep_old:
        step(f"--keep-old: two-target workspace left as is; compile with -Pmc={T}")
        return 0
    prep = ROOT / "templates/multi-version/tools/prepare-sources.py"
    log = work / "era-renames.log"
    with open(log, "w", encoding="utf-8") as fh:
        for tree in ("src/main/java", "src/test/java"):
            if not (work / tree).is_dir():
                continue
            out = work / (tree + ".era")
            shutil.rmtree(out, ignore_errors=True)
            r = subprocess.run([sys.executable, str(prep), "--src", str(work / tree), "--renames", str(table),
                                "--out", str(out)], stdout=fh, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
            if r.returncode != 0:
                step(f"FAILED applying the table to {tree} (see {log.name})"); return 6
            shutil.rmtree(work / tree); out.rename(work / tree)
    text = log.read_text(encoding="utf-8", errors="replace")
    rewrites = sum(int(x) for x in re.findall(r"renameRewrites[=:]\s*(\d+)", text)) or text.count("rewrote")
    step(f"applied in place to src/main/java and src/test/java; report in {log.name}"
         + (f" ({rewrites} rewrites)" if rewrites else ""))
    # the target is now the canonical one: an empty table, the default target, and no 1.21.1 build
    table.write_text(f"# {T} is this port's only target: its renames were applied to src/ by tools/era-hop.py.\n",
                     encoding="utf-8")
    for f in ("versions/1.21.1.properties", "versions/1.21.1.renames.tsv"):
        (work / f).unlink(missing_ok=True)
    shutil.rmtree(work / "src/mc21", ignore_errors=True)
    gp = work / "gradle.properties"
    g = re.sub(r"(?m)^mc=.*\n?", "", gp.read_text(encoding="utf-8"))
    gp.write_text(g.rstrip() + f"\n# the build's target (tools/era-hop.py): ./gradlew picks versions/{T}.properties\nmc={T}\n",
                  encoding="utf-8")
    step(f"flattened: {T} is now the only target (mc={T}); ready for the compile loop")
    return 0


def self_check():
    import tempfile
    ok = True
    with tempfile.TemporaryDirectory() as d:
        os.environ["MIGRATE_WORKSPACE"] = d
        ok &= ws_moves() == pathlib.Path(d) / "moves"
    ok &= (TPL / "versions/26.2.renames.hand.tsv").is_file() and (ROOT / "tools/make-multiversion.sh").is_file()
    ok &= (ROOT / "templates/multi-version/tools/prepare-sources.py").is_file()
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
