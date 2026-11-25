#!/usr/bin/env python3
"""Self-test of templates/upstream-harness/gates.init.gradle: the gate harness is wired into a fork's own build for both
plugins a NeoForge mod builds with, ModDevGradle (net.neoforged.moddev) and NeoGradle userdev
(net.neoforged.gradle.userdev, #298).

Static (the default; no Gradle, under a second): each plugin's block adds the harness's java/resources/test source
dirs, JUnit and the project-dir test working directory (Gate A), points the client run at <harness>/run-client with
the <modid>.boottest/<modid>.testmode switches (Gate C's verdict line), and gives a gameTestServer run <harness>/run and
the GameTest namespace (Gate B). Every check is shown to FAIL on a copy with that piece of wiring removed.

--build DIR (opt-in: it decompiles Minecraft 1.21.1 through NeoGradle, minutes and network on a cold cache): writes a
minimal userdev mod (examplemod, one client mixin, single-version) to DIR/mod with an author's build that points
every run at its own run/ directory, generates the harness the pipeline generates (tools/port_gates.py), and runs
  - Gate A as tools/ci-gates.py runs it (port_gates.gate_a): the harness's MixinConfigIntegrityTest must run;
  - the run tasks' real configuration, read off the JavaExec tasks NeoGradle made (runClient -Pboottest
    -Ptestmode=launch, runGameTestServer): the harness's directories and switches, over the author's run/.
The task names are the ones tools/gate-loop.py launches (runClient, runGameTestServer).
"""
import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import types
import importlib.util

ROOT = pathlib.Path(__file__).resolve().parent.parent
INIT = ROOT / "templates/upstream-harness/gates.init.gradle"
PLUGINS = ("net.neoforged.moddev", "net.neoforged.gradle.userdev")


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / file)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def block(text, plugin):
    """The body of `withPlugin('<plugin>') { ... }`, by brace matching, or None."""
    m = re.search(r"withPlugin\('%s'\)\s*\{" % re.escape(plugin), text)
    if not m:
        return None
    depth, i = 1, m.end()
    while i < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[m.end():i - 1] if not depth else None


# (what it wires, a regex every plugin's block must match). Shared: the source dirs, JUnit, the test working dir.
SHARED = [
    ("the harness's java sources", r'sourceSets\.main\.java\.srcDir\("\$\{harness\}/java"\)'),
    ("the harness's resources", r'sourceSets\.main\.resources\.srcDir\("\$\{harness\}/resources"\)'),
    ("the harness's unit tests", r'sourceSets\.test\.java\.srcDir\("\$\{harness\}/test"\)'),
    ("JUnit Jupiter", r"'testImplementation', 'org\.junit\.jupiter:junit-jupiter:"),
    ("the JUnit platform launcher", r"'testRuntimeOnly', 'org\.junit\.platform:junit-platform-launcher'"),
    ("the JUnit platform and the project-dir working directory",
     r"withType\(Test\)\.configureEach \{ useJUnitPlatform\(\); workingDir = p\.projectDir \}"),
    ("the boottest switch", r'"\$\{modid\}\.boottest"'),
    ("the testmode switch", r'"\$\{modid\}\.testmode"'),
    ("the client's run directory", r'"\$\{harness\}/run-client"'),
    ("the gameTestServer's run directory", r'"\$\{harness\}/run"'),
    ("the GameTest namespace", r"'neoforge\.enabledGameTestNamespaces', modid"),
]
# Each plugin's own DSL for the runs (ModDevGradle: neoForge.runs + gameDirectory; NeoGradle: runs + workingDirectory)
OWN = {
    "net.neoforged.moddev": [
        ("the client run", r"neoForge\.runs\.matching \{ it\.name == 'client' \}"),
        ("the client's game directory", r'r\.gameDirectory = p\.file\("\$\{harness\}/run-client"\)'),
        ("a gameTestServer run", r"neoForge\.runs\.create\('gameTestServer'\)"),
    ],
    "net.neoforged.gradle.userdev": [
        ("the client run", r"runs\.findByName\('client'\)"),
        ("the client's working (= game) directory", r'client\.workingDirectory\.set\(p\.file\("\$\{harness\}/run-client"\)\)'),
        ("a gameTestServer run, made when the author has none",
         r"runs\.findByName\('gameTestServer'\) \?: runs\.create\('gameTestServer'\)"),
        ("the gameTestServer's working directory", r'gts\.workingDirectory\.set\(p\.file\("\$\{harness\}/run"\)\)'),
        ("after the author's build script", r"p\.afterEvaluate \{"),
    ],
}


def check(text):
    """-> [missing wiring], per plugin."""
    bad = []
    for plugin in PLUGINS:
        b = block(text, plugin)
        if b is None:
            bad.append(f"{plugin}: no withPlugin block")
            continue
        for what, rx in SHARED + OWN[plugin]:
            if not re.search(rx, b):
                bad.append(f"{plugin}: {what}")
    return bad


def static():
    text = INIT.read_text(encoding="utf-8")
    bad = check(text)
    if bad:
        print("gates-init: FAIL -- missing: " + "; ".join(bad))
        return 1
    # a check never seen failing is decoration: remove each piece of wiring from one block, the check must name it
    missed = []
    for plugin in PLUGINS:
        b = block(text, plugin)
        for what, rx in SHARED + OWN[plugin]:
            planted = text.replace(b, re.sub(rx, "", b, count=1))
            if f"{plugin}: {what}" not in check(planted):
                missed.append(f"{plugin}: {what}")
    if "net.neoforged.gradle.userdev: no withPlugin block" not in check(text.replace("gradle.userdev'", "gradle.other'")):
        missed.append("the userdev block removed")
    if missed:
        print("gates-init: FAIL -- a planted removal went unseen: " + "; ".join(missed))
        return 1
    n = sum(len(SHARED) + len(OWN[p]) for p in PLUGINS)
    print(f"gates-init: OK ({n} wiring checks over {len(PLUGINS)} plugins, each seen failing on a planted removal)")
    return 0


# ---- the opt-in real build --------------------------------------------------------------------------------------
NEO = "21.1.228"     # the NeoForge 1.21.1 build and NeoGradle version templates/neoforge-mod pins
NG = "7.1.36"
FIXTURE = {
    "settings.gradle": """pluginManagement {
    repositories {
        maven { url = 'https://maven.neoforged.net/releases' }
        gradlePluginPortal()
    }
}
plugins { id 'org.gradle.toolchains.foojay-resolver-convention' version '1.0.0' }
rootProject.name = 'examplemod'
""",
    # an author's build as NeoGradle's MDK writes it, with every run in its own run/: the harness must win over it
    "build.gradle": """plugins {
    id 'java-library'
    id 'net.neoforged.gradle.userdev' version '%s'
}
version = '0.0.0'
group = 'com.example.mod'
java.toolchain.languageVersion = JavaLanguageVersion.of(21)
repositories {
    maven { url = 'https://maven.neoforged.net/releases' }
    mavenCentral()
}
runs {
    configureEach {
        workingDirectory project.file('run')
        modSource project.sourceSets.main
    }
    client { }
    server { argument '--nogui' }
}
dependencies {
    implementation "net.neoforged:neoforge:%s"
}
""" % (NG, NEO),
    "gradle.properties": "org.gradle.jvmargs=-Xmx3G\nminecraft_version=1.21.1\nneo_version=%s\n" % NEO,
    "src/main/java/com/example/mod/ExampleMod.java": """package com.example.mod;

import net.neoforged.fml.common.Mod;

@Mod("examplemod")
public class ExampleMod {
    public ExampleMod() {}
}
""",
    "src/main/java/com/example/mod/mixin/ExampleMixin.java": """package com.example.mod.mixin;

import net.minecraft.client.Minecraft;
import org.spongepowered.asm.mixin.Mixin;

@Mixin(Minecraft.class)
public class ExampleMixin {
}
""",
    "src/main/resources/examplemod.mixins.json": """{
  "required": true,
  "package": "com.example.mod.mixin",
  "compatibilityLevel": "JAVA_21",
  "client": ["ExampleMixin"],
  "injectors": { "defaultRequire": 1 }
}
""",
    "src/main/resources/META-INF/neoforge.mods.toml": """modLoader="javafml"
loaderVersion="[1,)"
license="MIT"
[[mods]]
modId="examplemod"
version="0.0.0"
displayName="Example Mod"
description="A fixture."
[[mixins]]
config="examplemod.mixins.json"
[[dependencies.examplemod]]
modId="neoforge"
type="required"
versionRange="[21.1,)"
ordering="NONE"
side="BOTH"
[[dependencies.examplemod]]
modId="minecraft"
type="required"
versionRange="[1.21.1,1.21.2)"
ordering="NONE"
side="BOTH"
""",
}
# prints what the run tasks NeoGradle made will really launch with
PRINT_RUNS = """allprojects { p ->
    p.tasks.register('portPrintRuns') {
        doLast {
            ['runClient', 'runGameTestServer'].each { n ->
                def t = p.tasks.getByName(n)
                println "PORT_RUN ${n} workingDir=${t.workingDir.toPath().toRealPath()}"
                t.systemProperties.findAll { k, v -> k.startsWith('examplemod.') || k == 'neoforge.enabledGameTestNamespaces' }
                    .sort().each { k, v -> println "PORT_RUN ${n} ${k}=${v}" }
            }
        }
    }
}
"""


def write_fixture(mod):
    """The fixture mod at `mod`, a one-commit git repository, with the template's Gradle wrapper."""
    for rel, body in FIXTURE.items():
        f = mod / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
    tpl = ROOT / "templates/neoforge-mod"
    shutil.copytree(tpl / "gradle", mod / "gradle")
    for w in ("gradlew", "gradlew.bat"):
        shutil.copy2(tpl / w, mod / w)
    g = lambda *a: subprocess.run(["git", *a], cwd=mod, check=True, capture_output=True, text=True, encoding="utf-8")
    g("init", "-q"); g("config", "core.autocrlf", "false")
    g("add", "-A"); g("-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "fixture")


def build(d):
    d = pathlib.Path(d).resolve()
    if d.exists():
        shutil.rmtree(d)
    mod, work = d / "mod", d / "work"
    work.mkdir(parents=True)
    write_fixture(mod)
    for k in ("PORT_BRANCH_MC", "PORT_BRANCH_BASE", "PORT_GRADLE_ARGS", "PORT_RUNTIME_EXCLUDE"):
        os.environ.pop(k, None)              # a single-version fork: no version-branch harness
    pg = _load("port_gates", "port_gates.py")
    c = {"repo": mod, "dir": work, "args": types.SimpleNamespace(modid="examplemod", base=None)}
    h = pg.harness(c)
    bad = []
    try:
        n = pg.gate_a(c, work / "gateA.log")
    except pg.Fail as e:
        print(f"gates-init build: FAIL -- Gate A: {e}")
        return 1
    res = sorted(p.name for p in (mod / "build/test-results/test").glob("*.xml"))
    print(f"Gate A: {n} test(s): {', '.join(res)}")
    if not any("MixinConfigIntegrityTest" in r for r in res):
        bad.append("Gate A did not run the harness's MixinConfigIntegrityTest")
    pr = work / "print-runs.gradle"
    pr.write_text(PRINT_RUNS, encoding="utf-8")
    env = dict(os.environ, **pg.gate_env(c))
    log = work / "runs.log"
    r = pg.gradle(mod, ["portPrintRuns", "-Pboottest", "-Ptestmode=launch", "-q"], log,
                  extra_init=[env["PORT_GRADLE_INIT"], pr], env=env)
    lines = [l for l in log.read_text(encoding="utf-8", errors="replace").splitlines() if l.startswith("PORT_RUN ")]
    print("\n".join(lines))
    want = [f"PORT_RUN runClient workingDir={(h / 'run-client').resolve()}",
            "PORT_RUN runClient examplemod.boottest=true",
            "PORT_RUN runClient examplemod.testmode=launch",
            f"PORT_RUN runGameTestServer workingDir={(h / 'run').resolve()}",
            "PORT_RUN runGameTestServer neoforge.enabledGameTestNamespaces=examplemod"]
    if r.returncode:
        bad.append(f"printing the runs failed (see {log.as_posix()})")
    # compare paths as resolved paths (macOS /var -> /private/var, Windows short names), the rest as text
    got = set()
    for l in lines:
        m = re.match(r"(PORT_RUN \S+ workingDir=)(.*)$", l)
        got.add(m.group(1) + str(pathlib.Path(m.group(2)).resolve()) if m else l)
    bad += [f"missing: {w}" for w in want if w not in got]
    if bad:
        print("gates-init build: FAIL -- " + "; ".join(bad))
        return 1
    print("gates-init build: OK")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--build", metavar="DIR", help="also build the NeoGradle userdev fixture in DIR (deleted first)")
    a = ap.parse_args()
    rc = static()
    if a.build:
        rc |= build(a.build)
    return rc


if __name__ == "__main__":
    sys.exit(main())
