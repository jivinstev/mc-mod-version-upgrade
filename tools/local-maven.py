#!/usr/bin/env python3
"""Put a mod jar a blocked maven would have served into the local maven tools/central-mirror.init.gradle reads.

    python3 tools/local-maven.py <group:artifact:version[:classifier]> --provider curseforge|modrinth --id <project> --file <fileId>

Some machines' egress refuses a mod's own maven (cursemaven, an author's maven). The build is right; the
machine cannot fetch. This downloads the same file through tools/mod-registry/modreg.py (its sha1 checked),
and lays it out under ~/.mc-mod-upgrade/local-maven/<group>/<artifact>/<version>/ with a minimal POM, so the
author's build resolves it unchanged. A classifier (e.g. `:api`) gets the full jar, which is a superset.
Standard library only.
"""
import argparse, hashlib, json, pathlib, shutil, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCAL = pathlib.Path.home() / ".mc-mod-upgrade/local-maven"


def place(coord, jar, root=LOCAL):
    parts = coord.split(":")
    if len(parts) not in (3, 4):
        sys.exit(f"local-maven: {coord!r} is not group:artifact:version[:classifier]")
    g, a, v = parts[:3]
    d = root / g.replace(".", "/") / a / v
    d.mkdir(parents=True, exist_ok=True)
    name = f"{a}-{v}" + (f"-{parts[3]}" if len(parts) == 4 else "") + ".jar"
    shutil.copyfile(jar, d / name)
    if len(parts) == 4 and not (d / f"{a}-{v}.jar").exists():
        shutil.copyfile(jar, d / f"{a}-{v}.jar")
    (d / f"{a}-{v}.pom").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n<project><modelVersion>4.0.0</modelVersion><groupId>{g}</groupId>'
        f"<artifactId>{a}</artifactId><version>{v}</version></project>\n", encoding="utf-8")
    return d / name


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("coord", nargs="?"); ap.add_argument("--provider"); ap.add_argument("--id"); ap.add_argument("--file")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    with tempfile.TemporaryDirectory() as d:
        r = subprocess.run([sys.executable, str(ROOT / "tools/mod-registry/modreg.py"), "download", "--provider", a.provider,
                            "--id", a.id, "--file", a.file, "--out", d], capture_output=True, text=True, encoding="utf-8")
        jars = list(pathlib.Path(d).glob("*.jar"))
        if r.returncode or not jars:
            sys.exit(f"local-maven: download failed: {(r.stderr or r.stdout)[-400:]}")
        out = place(a.coord, jars[0])
    print(f"local-maven: {a.coord} -> {out} (sha1 {hashlib.sha1(out.read_bytes()).hexdigest()})")
    return 0


def self_check():
    with tempfile.TemporaryDirectory() as d:
        j = pathlib.Path(d) / "x.jar"; j.write_bytes(b"PK")
        out = place("a.b:c:1.0+x:api", j, pathlib.Path(d) / "m")
        ok = out.name == "c-1.0+x-api.jar" and (out.parent / "c-1.0+x.jar").exists() and (out.parent / "c-1.0+x.pom").exists()
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
