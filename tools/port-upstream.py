#!/usr/bin/env python3
"""Port a mod IN ITS OWN REPOSITORY (an upstream fork), stage by stage, and say what each stage cost.

    python3 tools/port-upstream.py --repo <clone> --modid <id> [--branch neoforge-<mc>] [--base <ref>]
                                   [--permission <url>] [--from <stage>] [--only <stage>] [--budget 20]
    python3 tools/port-upstream.py --repo <clone> --modid <id> --record <stage>=<usd>   # a stage run by hand
    python3 tools/port-upstream.py --repo <clone> --modid <id> --report                 # the cost table

The source-first counterpart of tools/port.py (which starts from a jar). The deliverable is a branch in
the author's own layout with the smallest diff that ports it, so every stage edits the repo in place and
the test harnesses stay OUTSIDE it (templates/upstream-harness/gates.init.gradle wires them in per run).

The TARGET comes from --branch (`neoforge-1.21.1`, the default, or `neoforge-26.2`; tools/targets.py is the one
table of what each needs). 1.21.1 ports the author's Forge 1.20.1 tree. 26.2 is an era hop from OUR finished
`neoforge-1.21.1` branch of the same fork (--base defaults to it): the build is a version bump, the mechanical
stage generates and applies the 1.21.1 -> 26.2 class moves and runs the 26.x converters instead of the Forge
codemods, and the GameTest harness is wired by registration because 26.x has no @GameTest. Still a single-target,
least-diff build -- tools/era-hop.py's maps and transforms are reused, its multi-version frame is not.

Stages, in order (each records its dollars and seconds in the state file; --from resumes):
  license     FIRST, before anything is spent: the repo's LICENSE file and mods.toml `license` must agree;
              all-rights-reserved or unknown stops here unless --permission names the author's written
              permission. Bundled jars in lib/ are listed with whatever licence they declare.
  designer    a read-only model pass that reads the repo and writes DESIGN.md (build plan, metadata,
              risks ranked with the gate that catches each, scope, order).
  branch      cut --branch from --base.
  build       1.21.1: a model pass that converts the build to ModDevGradle following DESIGN.md. 26.2: a
              deterministic bump of the existing ModDevGradle build (versions, Java, Parchment, GeckoLib from
              Modrinth + interface injection), the model only for what a post-check still finds missing.
              Both checked by compileJava actually reaching javac (tools/burndown-count.sh), not by "BUILD SUCCESSFUL".
  metadata    mods.toml -> neoforge.mods.toml IN PLACE (the author's file, not a template), dependency ranges, mixin
              configs (refmap, compatibility level), pack.mcmeta -- every value from the target table.
  mechanical  1.21.1: SRG strings, the recipe pack, the access transformer (remap + override widening),
              forge-shapes, convert-simplechannel, then compile + fix-holders until it converges.
              26.2: class-move and colour maps from the two real classpaths, the composed rename table applied in
              place, data-format transforms, the access transformer, member renames where javac names the owner,
              then the 26.x converters (GUI hooks, ValueInput/Output, render types, core shaders, entity render
              state, GeckoLib 5); a compile count after each step.
  burndown    tools/file-loop.py on what is left.
  gates       Gate A (mixin-config integrity, asserting tests actually ran) and Gate B (tools/gate-loop.py,
              the GameTest server, fixing what fails) with the external harness (on 26.2 its tests are wired by
              a generated registrar, and the harness sources get the same rename table).
  normalise   tools/normalise-imports.py: inline names the port wrote become imports.
  reviewer    a model pass over the diff against --base for completeness, the author's style and least
              diff; it fixes what is mechanical and lists what needs a person. Compile + Gate A re-run.
  provenance  the diff must not touch LICENSE*, drop an author copyright line or add a foreign one.
  report      the cost table, also written as the body of the final commit.

Stops at the first stage that fails, with the reason; nothing is pushed. Standard library only; the
model stages need the `claude` CLI.
"""
import argparse, json, os, pathlib, re, shutil, subprocess, sys, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
import importlib.util  # noqa: E402
def _load_tool(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / file)
    mod = importlib.util.module_from_spec(spec); sys.modules.setdefault(name, mod); spec.loader.exec_module(mod)
    return mod


srcsets = _load_tool("srcsets", "srcsets.py")
targets = _load_tool("targets", "targets.py")
_pg = _load_tool("port_gates", "port_gates.py")   # the gate harness, shared with tools/ci-gates.py
Fail, sh, gradle, tgt, author_unlisted_mixins, harness, read_patch, apply_patch, harness_registration, \
    gate_env, gate_a = (_pg.Fail, _pg.sh, _pg.gradle, _pg.tgt, _pg.author_unlisted_mixins, _pg.harness,
                        _pg.read_patch, _pg.apply_patch, _pg.harness_registration, _pg.gate_env, _pg.gate_a)
STAGES = ["license", "deps", "designer", "branch", "build", "metadata", "mechanical", "burndown", "gates", "author-build", "normalise",
          "reviewer", "provenance", "report"]
SONNET = "claude-sonnet-5-5"
PERMISSIVE = {"MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "ISC", "Zlib", "Unlicense", "CC0-1.0", "MPL-2.0",
              "LGPL-2.1", "LGPL-3.0", "GPL-3.0", "GPL-2.0"}   # all permit a public fork; copyleft is noted, not blocked
LICENCE_SIGNS = [("MIT", r"\bMIT License\b|Permission is hereby granted, free of charge"),
                 ("Apache-2.0", r"Apache License,?\s+Version 2\.0"), ("GPL-3.0", r"GNU GENERAL PUBLIC LICENSE\s+Version 3"),
                 ("LGPL-3.0", r"GNU LESSER GENERAL PUBLIC LICENSE\s+Version 3"), ("LGPL-2.1", r"Lesser General Public License.*2\.1"),
                 ("MPL-2.0", r"Mozilla Public License,?\s+(?:Version|v\.)\s*2\.0"), ("BSD-3-Clause", r"Redistributions of source code.*Neither the name"),
                 ("CC0-1.0", r"CC0 1\.0"), ("Unlicense", r"This is free and unencumbered software")]


def claude(prompt, cwd, tools, model=SONNET, timeout=2400):
    """One headless worker; returns (result text, dollars)."""
    r = subprocess.run(["claude", "-p", prompt, "--model", model, "--output-format", "json", "--allowedTools", tools],
                       cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                       stdin=subprocess.DEVNULL)
    out = r.stdout
    try:
        d = json.loads(out[out.index("{"):])
    except (ValueError, json.JSONDecodeError):
        raise Fail(f"worker returned no result: {(r.stderr or out)[-400:]}")
    return d.get("result", ""), float(d.get("total_cost_usd") or 0)


def compile_count(repo, log):
    gradle(repo, ["-I", str(ROOT / "tools/maxerrs.init.gradle"), *srcsets.compile_tasks(repo), "--continue"], log)
    r = sh(["bash", str(ROOT / "tools/burndown-count.sh"), str(log)])
    m = re.search(r"errors = (\d+)", r.stdout)
    if not m or "compileJava: yes" not in r.stdout:
        return None, r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "no count"
    return int(m.group(1)), r.stdout.strip().splitlines()[-1]


def tool(name, *args, cwd=None):
    r = sh([sys.executable, str(ROOT / "tools" / name), *map(str, args)], cwd=cwd)
    if r.returncode not in (0, 1):
        raise Fail(f"{name} failed: {(r.stderr or r.stdout)[-600:]}")
    return r.stdout.strip()


# --------------------------------------------------------------------------- stages

def st_license(c):
    repo, notes = c["repo"], []
    files = sorted(p for p in repo.iterdir() if re.match(r"(?i)(license|licence|copying)(\.\w+)?$", p.name))
    text = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in files)
    found = next((sid for sid, pat in LICENCE_SIGNS if re.search(pat, text, re.S | re.I)), None)
    holders = re.findall(r"(?im)^.*copyright\s*(?:\(c\)|©)?\s*[^\n]{0,80}$", text)
    toml = next((p for p in repo.glob("src/main/resources/META-INF/*mods.toml")), None)
    declared = (re.search(r'(?m)^\s*license\s*=\s*"([^"]+)"', toml.read_text(encoding="utf-8")) or [None, None])[1] if toml else None
    props_file = repo / "gradle.properties"           # mods.toml is often templated: license="${mod_license}"
    props = dict(re.findall(r"(?m)^\s*([\w.]+)\s*=\s*(.*?)\s*$", props_file.read_text(encoding="utf-8"))) if props_file.exists() else {}
    if declared:
        declared = re.sub(r"\$\{([\w.]+)\}", lambda m: props.get(m.group(1), m.group(0)), declared)
    if found and declared and found.split("-")[0].lower() not in declared.lower():
        notes.append(f"LICENSE file reads as {found} but mods.toml says '{declared}'")
    kind = found or (declared if declared in PERMISSIVE else None)
    jars = []
    for j in sorted(repo.glob("lib/*.jar")):
        names = sh(["unzip", "-Z1", str(j)]).stdout
        lic = re.search(r"(?im)^(?:META-INF/)?(LICEN[SC]E[^\n]*|NOTICE[^\n]*)$", names)
        mt = sh(["unzip", "-p", str(j), "META-INF/mods.toml"]).stdout
        dl = (re.search(r'(?m)^\s*license\s*=\s*"([^"]+)"', mt) or [None, None])[1]
        jars.append(f"{j.name}: " + (f"declares {dl}" if dl else (f"ships {lic.group(1)}" if lic else "NO licence file")))
    result = {"licence": kind, "file": [p.name for p in files], "declared": declared, "holders": holders[:5],
              "bundled_jars": jars, "permission": c["args"].permission, "notes": notes}
    if kind is None and not c["args"].permission:
        raise Fail("no licence found (all rights reserved by default): record the author's written permission "
                   "with --permission <url> before porting")
    if notes and not c["args"].permission:
        raise Fail("; ".join(notes) + " -- resolve or pass --permission")
    return result


def st_deps(c):
    """Dependency preflight (tools/port-deps.py): find missing or blocked dependencies before any model is
    spent, and fill the local maven from the registry where the author's host is unreachable."""
    t = tgt(c)
    log = c["dir"] / "deps.log"
    r = sh([sys.executable, str(ROOT / "tools/port-deps.py"), "--repo", str(c["repo"]), "--mc", t.mc,
            "--fill-local", "--apply-moves", "--api", "--json", str(c["dir"] / "deps.json")]
           + [x for p in (c["args"].provide or []) for x in ("--provide", p)]
           + [x for t in (c["args"].lib_tree or []) for x in ("--api-tree", t)], timeout=1800, log=log)
    if r.returncode:
        why = {2: "a registry lookup failed", 3: "a required dependency has no build for the target",
               4: "a dependency sits on a blocked host with no registry source"}.get(r.returncode, "port-deps failed")
        raise Fail(f"dependency preflight: {why} (see {log})")
    return {"mc": t.mc, "report": str(c["dir"] / "deps.json")}

def st_designer(c):
    prompt = targets.designer_prompt((ROOT / "templates/upstream-harness/designer-prompt.md").read_text(encoding="utf-8"),
                                     tgt(c), c["args"].branch)
    text, usd = claude(prompt, c["repo"], "Read,Grep,Glob,Bash(ls:*),Bash(unzip -l:*),Bash(find:*)")
    (c["dir"] / "DESIGN.md").write_text(text, encoding="utf-8")
    return {"usd": usd, "design": str(c["dir"] / "DESIGN.md")}


def st_branch(c):
    repo, a = c["repo"], c["args"]
    if sh(["git", "rev-parse", "--verify", a.branch], cwd=repo).returncode == 0:
        sh(["git", "checkout", "-q", a.branch], cwd=repo)
        return {"branch": a.branch, "existed": True}
    r = sh(["git", "checkout", "-q", "-b", a.branch, a.base], cwd=repo)
    if r.returncode:
        raise Fail(r.stderr)
    return {"branch": a.branch}


MACHINE = re.compile(r"/tmp/|/home/\w|/root/|\.mc-mod-upgrade|\bfile:/(?!/\$\{)")


def provide_pairs(c):
    """--provide OLD=NEW: a dependency whose port exists only as a sibling fork (no release for the target yet),
    published to mavenLocal. OLD is the author's coordinate prefix, NEW the group:artifact:version to use."""
    return [tuple(x.split("=", 1)) for x in (c["args"].provide or [])]


def provide_text(c):
    pairs = provide_pairs(c)
    if not pairs:
        return ""
    lines = [f"- replace the dependency on `{o}` (any version) with `{n}`" for o, n in pairs]
    return ("DEPENDENCIES PORTED ALONGSIDE THIS MOD (they have no release for the target; each is published to "
            "mavenLocal from its own port, so add `mavenLocal()` to repositories):\n" + "\n".join(lines) + "\n\n")


def loader_in_jar_name(repo):
    """The loader word in the JAR's name follows the port: an author who names their jars
    `version = "${mc}-forge-${v}"` (or archivesName / archives_base_name likewise) gets `-neoforge-`. Only the
    lines that name the artifact; `forge.logging.*` run properties are NeoForge's names too and stay.
    Measured: a port shipped `<mod>-1.21.1-forge-<v>.jar` as its NeoForge release. Idempotent. -> lines changed."""
    out = []
    for name in ("build.gradle", "gradle.properties"):
        f = repo / name
        if not f.is_file():
            continue
        t = f.read_text(encoding="utf-8")
        def fix(m):
            new = re.sub(r"(?<![A-Za-z])forge(?![A-Za-z.])", "neoforge", m.group(0))
            if new != m.group(0):
                out.append(f"{name}: {new.strip()}")
            return new
        t2 = re.sub(r"(?m)^\s*(?:version|archivesName|archives_base_name|archivesBaseName|base\.archivesName)\s*=.*$",
                    fix, t)
        if t2 != t:
            f.write_text(t2, encoding="utf-8")
    return out


def provide_nontransitive(repo, pairs):
    """Declare each --provide'd sibling NON-transitive. The registry artifact it replaces (maven.modrinth,
    cursemaven) carries no dependencies; a sibling published with `from components.java` carries all of its
    own, from hosts the author's build never named. Measured: Gate A's test classpath failed resolving the
    sibling's GeckoLib from a blocked maven, when the mod declares its own GeckoLib. Idempotent."""
    f = repo / "build.gradle"
    if not f.is_file():
        return []
    t, done = f.read_text(encoding="utf-8"), []
    for _old, new in pairs:
        pat = re.compile(r'(?m)^(\s*)(\w+)\s*\(?\s*(["\'])' + re.escape(new) + r'\3\s*\)?\s*$')
        t2 = pat.sub(lambda m: f'{m.group(1)}{m.group(2)}("{new}") {{ transitive = false }}', t)
        if t2 != t:
            t = t2; done.append(new)
    if done:
        f.write_text(t, encoding="utf-8")
    return done


def provide_unmet(c):
    g = (c["repo"] / "build.gradle").read_text(encoding="utf-8", errors="replace")
    return [(o, n) for o, n in provide_pairs(c) if o in g or n not in g or "mavenLocal()" not in g]


def neo_floor(repo):
    """The highest NeoForge minimum any mod on the compile classpath declares (its neoforge.mods.toml
    versionRange), or None. Measured: the build stage picked 21.1.172 while a dependency required 21.1.228,
    which compiled clean and refused to load at Gate B."""
    import zipfile
    spec = importlib.util.spec_from_file_location("fl", ROOT / "tools/file-loop.py")
    fl = importlib.util.module_from_spec(spec); spec.loader.exec_module(fl)
    best = None
    for j in fl.compile_classpath(repo):
        if j.suffix != ".jar" or not j.exists():
            continue
        try:
            with zipfile.ZipFile(j) as z:
                t = z.read("META-INF/neoforge.mods.toml").decode("utf-8", "replace")
        except (KeyError, zipfile.BadZipFile):
            continue
        for blk in re.findall(r'modId\s*=\s*"neoforge"(.*?)(?=\[\[|\Z)', t, re.S):
            m = re.search(r'versionRange\s*=\s*"[\[(]([\d.]+)', blk)
            if m:
                v = tuple(int(x) for x in m.group(1).split("."))
                best = max(best, v) if best else v
    return best


def raise_neo_floor(c):
    """Raise gradle.properties neo_version to what the dependencies require; True if it changed."""
    gp = c["repo"] / "gradle.properties"
    t = gp.read_text(encoding="utf-8")
    m = re.search(r"(?m)^neo_version\s*=\s*([\d.]+)\s*$", t)
    floor = neo_floor(c["repo"]) if m else None
    have = tuple(int(x) for x in m.group(1).split(".")) if m else None
    if not floor or not have or have >= floor:
        return False
    want = ".".join(map(str, floor))
    gp.write_text(t[:m.start(1)] + want + t[m.end(1):], encoding="utf-8")
    c.setdefault("notes", []).append(f"neo_version {m.group(1)} -> {want}: a dependency requires it")
    return True


def bump_build_files(c):
    """The 26.2 build change, deterministically (tools/targets.py bump_build): versions, toolchain, Parchment,
    GeckoLib. -> the notes of what changed. Edits only build.gradle and gradle.properties."""
    repo, t = c["repo"], tgt(c)
    bg, gp = repo / "build.gradle", repo / "gradle.properties"
    build, props = bg.read_text(encoding="utf-8"), gp.read_text(encoding="utf-8")
    nb, np_, notes = targets.bump_build(build, props, t, uses_geckolib(repo))
    if nb != build:
        bg.write_text(nb, encoding="utf-8")
    if np_ != props:
        gp.write_text(np_, encoding="utf-8")
    wp = repo / "gradle/wrapper/gradle-wrapper.properties"
    if wp.exists():
        wtext, wnotes = targets.bump_wrapper(wp.read_text(encoding="utf-8"), t)
        if wnotes:
            wp.write_text(wtext, encoding="utf-8")
            notes += wnotes
    return notes


def uses_geckolib(repo):
    """Does the mod compile against GeckoLib (either package), judged from its build and sources."""
    bg = repo / "build.gradle"
    if targets.uses_geckolib(bg.read_text(encoding="utf-8", errors="replace") if bg.exists() else ""):
        return True
    return any(targets.uses_geckolib(f.read_text(encoding="utf-8", errors="replace"))
               for d in srcsets.java_dirs(repo) for f in d.rglob("*.java"))


def build_unmet(c):
    """What the build still lacks for the target (sentences); empty when ready. Only for a hop (a conversion
    from ForgeGradle is judged by compileJava reaching javac, as it always was)."""
    repo, t = c["repo"], tgt(c)
    if t.mechanical != "era":
        return []
    wp = repo / "gradle/wrapper/gradle-wrapper.properties"
    return targets.build_unmet((repo / "build.gradle").read_text(encoding="utf-8", errors="replace"),
                               (repo / "gradle.properties").read_text(encoding="utf-8", errors="replace"), t, uses_geckolib(repo),
                               wp.read_text(encoding="utf-8", errors="replace") if wp.exists() else None)


def apply_dep_versions(repo, deps_json):
    """Put the target builds the deps stage resolved into the build, mechanically: each dependency's
    `suggested.change` from deps.json (`gradle.properties: KEY=old -> new`, or a literal coordinate in
    build.gradle). Measured: a build model kept the author's 1.20.1 file ids for 13 dependencies while the
    preflight had already named every replacement. Idempotent. -> the changes made."""
    try:
        deps = json.loads(pathlib.Path(deps_json).read_text(encoding="utf-8")).get("dependencies") or []
    except (OSError, ValueError):
        return []
    done = []
    for d in deps:
        ch, coord = (d.get("suggested") or {}).get("change"), (d.get("suggested") or {}).get("coord")
        if not ch or not coord or d.get("provided"):
            continue
        m = re.match(r"gradle\.properties: ([\w.-]+)=(.*) -> (.*)$", ch)
        if m:
            f = repo / "gradle.properties"
            if f.is_file():
                t = f.read_text(encoding="utf-8")
                n = re.sub(r"(?m)^(\s*" + re.escape(m.group(1)) + r"\s*=\s*)" + re.escape(m.group(2)) + r"\s*$",
                           lambda x: x.group(1) + m.group(3), t)
                if n != t:
                    f.write_text(n, encoding="utf-8"); done.append(f"{m.group(1)}={m.group(3)}")
            continue
        m = re.match(r"(?:build\.gradle )?literal: (.*) -> (.*)$", ch)
        f = repo / "build.gradle"
        if m and f.is_file():
            t = f.read_text(encoding="utf-8")
            old = f"{d['group']}:{d['artifact']}:{m.group(1)}"
            if old in t:
                f.write_text(t.replace(old, coord), encoding="utf-8"); done.append(f"{old} -> {coord}")
    return done


def st_build(c):
    repo, t = c["repo"], tgt(c)
    design = (c["dir"] / "DESIGN.md").read_text(encoding="utf-8") if (c["dir"] / "DESIGN.md").exists() else ""
    prompt, bumped = targets.build_prompt(t, provide_text(c), design), []
    if t.mechanical == "era":
        bumped = bump_build_files(c)   # a version bump needs no model; one is asked only for what is still missing
        prompt = None
    usd, asks, applied = 0.0, 0, []
    while True:
        if prompt is not None:
            if asks == 2:
                break
            _t, u = claude(prompt, repo, "Read,Edit,Write,Grep,Glob")
            usd += u; asks += 1
        applied += apply_dep_versions(repo, c["dir"] / "deps.json")
        applied += [f"{x} (non-transitive)" for x in provide_nontransitive(repo, provide_pairs(c))]
        applied += loader_in_jar_name(repo)         # every target is NeoForge; an existing `neoforge` is left alone    # never left to the model (see the function)
        n, line = compile_count(repo, c["dir"] / "build-check.log")
        if n is not None and raise_neo_floor(c):          # a dependency needs a newer NeoForge: recount on it
            n, line = compile_count(repo, c["dir"] / "build-check.log")
        unmet = provide_unmet(c)
        wrong_target = build_unmet(c)
        local = [f"{f.name}: {l.strip()}" for f in (repo / "build.gradle", repo / "settings.gradle", repo / "gradle.properties")
                 if f.exists() for l in f.read_text(encoding="utf-8").splitlines() if MACHINE.search(l)]
        if local:      # caught here, not by provenance after the whole port (measured: a pinned local-maven repo)
            prompt = ("The build files name paths on THIS machine, which the author's build cannot have: "
                      + "; ".join(local[:5]) + ". Remove them -- the harness supplies its own repositories from "
                      "outside the tree. Fix the build files only.")
            continue
        if n is not None and not unmet and not wrong_target:
            res = {"usd": usd, "first_count": n, "dep_versions": applied}
            if t.mechanical == "era":
                res["bumped"] = bumped
            return res
        if wrong_target:
            prompt = (f"The build is not yet ready for Minecraft {t.mc} / NeoForge {t.neo_version}:\n- "
                      + "\n- ".join(wrong_target) + "\nFix the build files only, keeping the author's structure.")
            continue
        if unmet:
            prompt = (f"build.gradle must depend on {unmet[0][1]} in place of {unmet[0][0]} (resolved from "
                      "mavenLocal(), which must be in repositories). Fix the build files only.")
            continue
        log = (c["dir"] / "build-check.log").read_text(encoding="utf-8", errors="replace")
        wrong = re.search(r"\* What went wrong:\n(.*?)(?:\n\* Try:|\Z)", log, re.S)   # the reason, not the last line
        prompt = (f"The build you wrote does not reach javac. Gradle says:\n{(wrong.group(1) if wrong else line)[:2000]}\n"
                  "Fix the build files only.")
    raise Fail(f"build still does not reach javac: {line}" if not wrong_target else
               f"the build is not ready for {t.name}: " + "; ".join(wrong_target), usd=usd)


def st_metadata(c):
    repo, modid, done, t = c["repo"], c["args"].modid, [], tgt(c)
    ranges = []
    for toml in sorted(repo.glob("src/*/resources/META-INF/*mods.toml")):     # mods.toml (Forge) or neoforge.mods.toml (a hop)
        orig = toml.read_text(encoding="utf-8")
        text = targets.retarget_toml(orig, t)
        for cfg in [f.name for f in srcsets.mixin_configs(repo) if f.parent == toml.parent.parent]:
            if not re.search(r'(?m)^\s*config\s*=\s*"%s"' % re.escape(cfg), text):
                text = text.rstrip("\n") + f'\n\n[[mixins]]\nconfig = "{cfg}"\n'
        new = toml.with_name("neoforge.mods.toml")
        if toml != new:
            sh(["git", "mv", str(toml), str(new)], cwd=repo)
        if toml != new or text != orig:
            new.write_text(text, encoding="utf-8"); done.append(str(new.relative_to(repo)))
        ranges += targets.dependency_ranges(text)
    for mj in srcsets.mixin_configs(repo):
        text = mj.read_text(encoding="utf-8")
        t2 = targets.retarget_mixin(text, t)
        if t2 != text:
            mj.write_text(t2, encoding="utf-8"); done.append(str(mj.relative_to(repo)))
    fmt, fmt_src = targets.derive_pack(t, [repo])         # the game's own pack_version, the table's as a fallback
    for pm in repo.glob("src/*/resources/pack.mcmeta"):
        text = pm.read_text(encoding="utf-8")
        t2 = targets.retarget_pack(text, t, fmt)
        if t2 != text:
            pm.write_text(t2, encoding="utf-8"); done.append(str(pm.relative_to(repo)))
    tool("fix-datapack-layout.py", repo, "--apply")
    moved = client_side_mixins(repo)
    lost = srcsets.undeclared_mixin_configs(repo)
    if lost:      # caught here, before the burn-down, not at Gate A after it
        raise Fail(f"mixin config(s) still declared in no neoforge.mods.toml [[mixins]]: {lost}")
    res = {"files": done, "moved_to_client": moved}
    if t.mechanical == "era":
        res["pack_formats"] = {"resources": list(fmt["resources"]), "data": list(fmt["data"]), "from": fmt_src}
        res["dependency_ranges_to_review"] = [f"{m} {r}" for m, r in dict.fromkeys(ranges)]   # a hop leaves them as written
    return res


CLIENT_ONLY = re.compile(r"^(net\.minecraft\.client\.|com\.mojang\.blaze3d\.|net\.neoforged\.neoforge\.client\.|"
                         r"(?:software\.bernie\.geckolib|com\.geckolib)\.(renderer|model|cache|loading)\.|[\w.]*\.client\.)")


def client_side_mixins(repo):
    """Move a mixin listed in the COMMON array to "client" when its @Mixin target is a client-only class: a dedicated
    server cannot load the target, so the mixin fails there (measured: a GeckoLib renderer mixin listed as common,
    found by Gate C after every other gate was green). Decided from the target's import, never guessed."""
    moved = []
    for cfg in srcsets.mixin_configs(repo):
        t = cfg.read_text(encoding="utf-8"); d = json.loads(t)
        pdirs = [jd / d["package"].replace(".", "/") for jd in srcsets.java_dirs(repo)]
        go = []
        for name in d.get("mixins") or []:
            f = next((pd / (name.replace(".", "/") + ".java") for pd in pdirs
                      if (pd / (name.replace(".", "/") + ".java")).exists()), None)
            if not f:
                continue
            src = f.read_text(encoding="utf-8", errors="replace")
            imports = dict((m.group(2), m.group(1)) for m in re.finditer(r"(?m)^import\s+([\w.]+\.(\w+))\s*;", src))
            targets = re.findall(r"@Mixin\s*\(\s*(?:value\s*=\s*)?\{?([^)]*)", src)
            fqns = [imports.get(x, x) for grp in targets for x in re.findall(r"([\w.]+)\.class", grp)]
            fqns += [x.replace("$", ".") for grp in targets for x in re.findall(r'"([\w.$]+)"', grp)]
            if fqns and all(CLIENT_ONLY.match(x) for x in fqns):
                go.append(name)
        if go:
            d["mixins"] = [n for n in d["mixins"] if n not in go]
            d["client"] = (d.get("client") or []) + go
            one_line = "\n" not in t.strip()
            cfg.write_text((json.dumps(d, separators=(",", ":")) if one_line else json.dumps(d, indent=2)) + "\n",
                           encoding="utf-8")
            moved += [f"{cfg.name}:{n}" for n in go]
    return moved


def at_loop(c):
    """Override widenings appear one subclass level per compile: compile, widen what the log names, repeat."""
    repo = c["repo"]
    for k in range(4):                         # override widenings appear one subclass level per compile
        n, _ = compile_count(repo, c["dir"] / f"at-{k}.log")
        if n is not None:
            return n
        log = (c["dir"] / f"at-{k}.log").read_text(encoding="utf-8", errors="replace")
        unresolved = sorted(set(re.findall(r"Could not resolve ([\w.\-]+:[\w.\-]+:[^\s.]+[^\s]*)\.", log)))
        if unresolved:                            # a dependency, not Minecraft: say so instead of blaming the AT
            raise Fail("dependencies do not resolve: " + ", ".join(unresolved[:6]) + " -- wrong coordinates for the "
                       "target, or a maven this machine cannot reach (tools/local-maven.py); see " + f"at-{k}.log")
        out = tool("fix-access-transformer.py", "--work", repo, "--overrides-from", c["dir"] / f"at-{k}.log")
        if " 0 override" in out:
            raise Fail("Minecraft's recompile fails and no access-transformer override explains it (see at-*.log)")
    return None


def mechanical_forge(c):
    repo, a, src, rep = c["repo"], c["args"], c["repo"] / "src/main/java", {}
    srg = pathlib.Path(os.path.expanduser("~/.mc-mod-upgrade/work/srg2official-1.20.1.json"))
    if srg.exists():
        for d in repo.glob("src/*/java"):
            rep[f"srg:{d.parent.name}"] = tool("srg-remap/apply_mapping.py", srg, d).splitlines()[0:1]
    pack = ROOT / tgt(c).recipe_pack
    dirs = srcsets.java_dirs(repo)                # every source set the author builds, not just main
    for d in dirs:                                # Forge -> NeoForge packages and the 1.21 renames, as port.py does
        files = [str(f) for f in d.rglob("*.java")]
        for i in range(0, len(files), 200):
            sh(["perl", "-pi", str(ROOT / "tools/srg-remap/forge_import_codemod.pl"), *files[i:i + 200]])
        tool("srg-remap/mc121_codemod.py", d)
    rep["pack"] = [l for d in dirs for l in tool("apply-recipes.py", "--src", d, "--recipes", pack).splitlines()
                   if l.startswith("auto")]
    rep["at"] = tool("fix-access-transformer.py", "--work", repo, *(["--srg-map", srg] if srg.exists() else []))
    at_loop(c)
    rep["shapes"] = [tool("forge-shapes.py", "--src", d, "--modid", a.modid, "--sites", 0) for d in dirs]
    rep["net"] = [tool("convert-simplechannel.py", "--src", d, "--modid", a.modid) for d in dirs]
    counts = []
    for r in range(4):
        n, line = compile_count(repo, c["dir"] / f"mech-{r}.log")
        if n is None:
            raise Fail(f"compile did not run: {line}")
        counts.append(n)
        outs = [tool("fix-holders.py", "--src", d, "--log", c["dir"] / f"mech-{r}.log", "--sites", 0) for d in dirs]
        if all(" 0 site" in o.splitlines()[0] for o in outs):
            break
    rep["errors"] = counts
    return rep


# ------------------------------------------------------------------ mechanical, Minecraft 26.2 (an era hop)

def run_tool(name, *args):
    """A tools/ script -> (exit code, stdout+stderr). Unlike tool(), the caller decides what a code means."""
    r = sh([sys.executable, str(ROOT / "tools" / name), *map(str, args)])
    return r.returncode, (r.stdout + r.stderr).strip()


def headline(out, stem, label):
    """The line that says what a tool did: the last `<stem>...: <something>` line it printed, else (GeckoLib's
    converter prints a table) its `N files changed` row, else its last line."""
    lines = [l for l in out.splitlines() if l.strip()]
    own = [l.strip() for l in lines if l.startswith(stem) and not l.rstrip().endswith(":")]
    if own:
        return own[-1]
    m = next((re.match(r"\s+(\d+)\s+files? changed", l) for l in lines if re.match(r"\s+(\d+)\s+files? changed", l)), None)
    if m:
        return f"{label}: {m.group(1)} file(s) changed"
    return lines[-1].strip() if lines else f"{label}: no output"


def era_maps(c):
    """The class-move and colour maps for 1.21.1 -> the target, from the two REAL compile classpaths
    (tools/build-class-move-map.py, tools/gen-color-renames.py), cached per machine under the migrator
    workspace as era-hop.py does -- they are Mojang's names and never published. The target's classpath is
    this repo's (its build is already bumped); 1.21.1's is the base branch's, resolved in a throwaway
    worktree. -> (moves, colours, 'cached' | 'generated')"""
    repo, t, hop = c["repo"], tgt(c), _load_tool("era_hop", "era-hop.py")
    ws = hop.ws_moves()
    moves, colours = ws / f"moves-1.21.1-to-{t.mc}.tsv", ws / f"colors-1.21.1-to-{t.mc}.tsv"
    if moves.exists() and colours.exists():
        return moves, colours, "cached"
    ws.mkdir(parents=True, exist_ok=True)
    wt = c["dir"] / "base-worktree"
    try:
        new_cp = hop.gradle_classpath(repo, t.mc, c["dir"] / f"era-cp-{t.mc}.txt")
        sh(["git", "worktree", "prune"], cwd=repo)
        r = sh(["git", "worktree", "add", "--detach", str(wt), c["args"].base], cwd=repo)
        if r.returncode:
            raise Fail(f"cannot check out {c['args'].base} to resolve the 1.21.1 classpath: {r.stderr.strip()[-300:]}")
        old_cp = hop.gradle_classpath(wt, "1.21.1", c["dir"] / "era-cp-1.21.1.txt")
        # the classpaths name jars inside both build directories, so the maps are made before the worktree goes
        for script, out in (("build-class-move-map.py", moves), ("gen-color-renames.py", colours)):
            rc, o = run_tool(script, "--from-cp", old_cp, "--to-cp", new_cp, "--out", out)
            if rc or not out.exists():
                raise Fail(f"{script} failed: {o[-400:]}")
    except (RuntimeError, OSError) as e:
        raise Fail(f"cannot resolve a classpath to build the class-move map: {e}")
    except Fail:
        for f in (moves, colours):                     # never leave a half-made map for the next run to trust
            f.unlink(missing_ok=True)
        raise
    finally:
        if wt.exists():
            sh(["git", "worktree", "remove", "--force", str(wt)], cwd=repo)
    return moves, colours, "generated"


def sync_tree(src, dst):
    """Copy src over dst, writing only files whose bytes differ (so git, Gradle and mtimes see only the
    real change). -> number of files written."""
    n = 0
    for f in sorted(src.rglob("*")):
        if not f.is_file():
            continue
        to = dst / f.relative_to(src)
        data = f.read_bytes()
        if not to.exists() or to.read_bytes() != data:
            to.parent.mkdir(parents=True, exist_ok=True)
            to.write_bytes(data)
            n += 1
    return n


def apply_renames(c, roots, table, log):
    """prepare-sources (the engine a multi-version build runs) over each source root, IN PLACE: the target is
    this branch's only dialect. Line endings are kept, so only real changes show in the diff.
    -> (files written, rewrites, the dead-rule line)"""
    prep = ROOT / "templates/multi-version/tools/prepare-sources.py"
    hits, files = c["dir"] / "era-rule-hits.txt", 0
    hits.unlink(missing_ok=True)
    with open(log, "w", encoding="utf-8") as fh:
        for i, d in enumerate(roots):
            out = c["dir"] / "era-prep" / str(i)
            shutil.rmtree(out, ignore_errors=True)
            extra = [] if len(roots) == 1 else (["--hits-out", hits] if i < len(roots) - 1 else ["--hits-in", hits])
            r = subprocess.run([sys.executable, str(prep), "--src", str(d), "--renames", str(table), "--out", str(out),
                                *map(str, extra)], stdout=fh, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
            if r.returncode:
                raise Fail(f"applying the rename table to {d.relative_to(c['repo'])} failed; see {log.name}")
            files += sync_tree(out, d)
    text = log.read_text(encoding="utf-8", errors="replace")
    rewrites = sum(int(x) for x in re.findall(r"renameRewrites=(\d+)", text))
    dead = re.findall(r"dead-rule check: (checked=\d+ exempt=\d+ dead=\d+)", text)
    shutil.rmtree(c["dir"] / "era-prep", ignore_errors=True)
    return files, rewrites, dead[-1] if dead else ""


def era_advisories(c):
    """What a hop cannot do deterministically, found by looking: each is a person's or a model's job."""
    repo, out = c["repo"], []
    res = repo / "src/main/resources"
    for sub, why in (("dimension_type", "dimension types gained required fields and moved flags into attributes"),
                     ("worldgen/noise_settings", "the noise router replaced initial_density_without_jaggedness"),
                     ("worldgen/biome", "carvers is a list, not a map")):
        n = len(list(res.glob(f"data/*/{sub}/*.json")))
        if n:
            out.append(f"{n} {sub} file(s): {why} (CATALOG V45)")
    tests = [f for d in (repo / "src").glob("*/java") for f in d.rglob("*.java")
             if "@GameTest" in f.read_text(encoding="utf-8", errors="replace")]
    if tests:
        out.append(f"{len(tests)} source file(s) carry the author's own @GameTest: 26.x has no such annotation "
                   "(templates/multi-version/tools/gametest_adapter.py wires it; not applied to the author's tree)")
    if list(repo.glob("src/*/resources/META-INF/services/*")):
        out.append("META-INF/services files: a transformation-service or coremod provider needs its 26.x API checked by hand")
    return out


def mechanical_era(c):
    repo, a, t = c["repo"], c["args"], tgt(c)
    dirs = srcsets.java_dirs(repo)
    roots = dirs + ([repo / "src/test/java"] if (repo / "src/test/java").is_dir() else [])
    steps, rep = [], {"steps": []}
    rep["steps"] = steps
    last = {"n": None}

    def record(name, summary, count=True):
        n = last["n"]
        if count:
            n, _ = compile_count(repo, c["dir"] / f"era-{len(steps)}-{name}.log")
            if n is None:
                raise Fail(f"compile did not run after {name}: see era-{len(steps)}-{name}.log")
        last["n"] = n
        steps.append({"step": name, "summary": summary, "errors": n})
        print(f"[port-upstream]   {name}: {summary}" + (f" -> {n} errors" if count else " (no recount)"), flush=True)
        return n

    record("start", f"{a.base} + the bumped build ({t.name})")
    moves, colours, how = era_maps(c)
    table = c["dir"] / f"renames-{t.mc}.tsv"
    rc, out = run_tool("compose-renames.py", "--hand", ROOT / t.rename_table,
                       "--generated", moves, "--generated", colours, "--out", table)
    if rc:
        raise Fail(f"composing the rename table failed: {out[-400:]}")
    rows = sum(1 for l in table.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#"))
    files, rewrites, dead = apply_renames(c, roots, table, c["dir"] / "era-renames.log")
    hop = _load_tool("era_hop", "era-hop.py")
    changed, refused, other = hop.transform_resources(repo, t.mc)
    items = hop.client_items(repo, hop.mod_namespaces(repo))
    _rc, js = run_tool("fix-json-strict.py", "--work", repo)
    rep["renames"] = {"maps": how, "rows": rows, "files_changed": files, "rewrites": rewrites, "dead_rules": dead}
    rep["data"] = {"rewritten": len(changed), "refused": refused[:20], "other_mod": other[:20], "client_items": items,
                   "json_strict": headline(js, "fix-json-strict", "fix-json-strict")}
    rep["at"] = run_tool("fix-access-transformer.py", "--work", repo, "--class-map", moves)[1]
    n = at_loop(c)          # the access transformer has to be valid before Minecraft recompiles at all
    if n is None:
        raise Fail("Minecraft's recompile never succeeded after the access-transformer passes (see at-*.log)")
    last["n"] = n
    steps.append({"step": "renames", "errors": n, "summary":
                  f"{rows} rows ({how} maps), {rewrites} rewrite(s) in {files} file(s); {len(changed)} data file(s) rewritten, "
                  f"{len(refused)} refused, {items} client item definition(s)"})
    print(f"[port-upstream]   renames: {steps[-1]['summary']} -> {n} errors", flush=True)

    members = ROOT / t.members_table

    def member_loop(label):
        fixed, n = 0, last["n"]
        for r in range(6):
            log = c["dir"] / f"era-{label}-{r}.log"
            n, _ = compile_count(repo, log)
            if n is None:
                raise Fail(f"compile did not run during {label}")
            if n == 0:
                break
            got = 0
            for d in dirs:
                _rc, o = run_tool("fix-missing-members.py", "--src", d, "--log", log, "--table", members)
                got += sum(int(x) for x in re.findall(r"(\d+) site\(s\) rewritten", o))
            fixed += got
            if not got:
                break
        last["n"] = n
        steps.append({"step": label, "errors": n, "summary": f"{fixed} member site(s) renamed where javac named the owner"})
        print(f"[port-upstream]   {label}: {steps[-1]['summary']} -> {n} errors", flush=True)

    member_loop("members")

    fp = [tree_fingerprint(repo)]
    libs = a.lib_tree or []
    ctx = [x for lib in libs for x in ("--context", lib)]
    idx = [x for lib in libs for x in ("--index", lib)]
    text = "\n".join(f.read_text(encoding="utf-8", errors="replace") for d in dirs for f in d.rglob("*.java"))
    plan = [("gui-hooks", "convert-gui-hooks.py", lambda d, i: ["--src", d]),
            ("valueio", "convert-valueio.py", lambda d, i: ["--src", d])]
    advisories = []
    if "CompositeState" in text:
        custom = re.search(r"new\s+(?:RenderStateShard\.)?ShaderStateShard\s*\(", text)
        if custom and not (a.render_state_type and a.render_state_factory):
            advisories.append("custom ShaderStateShard in the render types: convert-rendertypes needs the port's own "
                              "state type and factory (--render-state-type / --render-state-factory); not run")
        else:
            st, sf = (a.render_state_type, a.render_state_factory) if custom else ("unused.State", "unused.state")
            plan.append(("rendertypes", "convert-rendertypes.py", lambda d, i: ["--src", d, "--state-type", st, "--state-factory", sf]))
    sh_dir = repo / f"src/main/resources/assets/{a.modid}/shaders"
    if sh_dir.is_dir() and any(sh_dir.glob("core/*.json")):
        if a.shader_block:
            plan.append(("core-shaders", "convert-core-shaders.py",
                         lambda d, i: ["--shaders", sh_dir, "--ns", a.modid, "--block", a.shader_block] if i == 0 else None))
        else:
            advisories.append("core shader programs present: pass --shader-block <UniformBlockName> to run convert-core-shaders "
                              "(26.x reads no program JSON and binds uniform blocks; the Java that fills the block is the port's to write)")
    plan.append(("entity-renderstate", "convert-entity-renderstate.py", lambda d, i: ["--src", d, *ctx]))
    if uses_geckolib(repo):
        assets = repo / "src/main/resources/assets"
        plan.append(("geckolib", "convert-geckolib.py",
                     lambda d, i: ["--src", d, *(["--assets", assets] if i == 0 and assets.is_dir() else []), *idx]))
    for label, script, args_for in plan:
        outs = []
        for i, d in enumerate(dirs):
            args = args_for(d, i)
            if args is None:
                continue
            rc, o = run_tool(script, *args)
            if rc not in (0, 1):
                raise Fail(f"{script} failed: {o[-600:]}")
            outs.append(o)
        joined = "\n".join(outs)
        heads = list(dict.fromkeys(headline(o, "convert-", script[:-3]) for o in outs))
        (c["dir"] / f"era-{label}.out").write_text(joined + "\n", encoding="utf-8")
        now = tree_fingerprint(repo)
        changed_tree, fp[0] = now != fp[0], now
        nref = sum(1 for l in joined.splitlines() if "REFUSED" in l and not re.search(r"REFUSED \d+ site", l))
        record(label, " | ".join(heads) + (f"; {nref} REFUSED (see era-{label}.out)" if nref else ""),
               count=changed_tree)
    member_loop("members-after-converters")
    rep["advisories"] = advisories + era_advisories(c)
    rep["errors"] = [s["errors"] for s in steps if s["errors"] is not None]
    return rep


def st_mechanical(c):
    return (mechanical_era if tgt(c).mechanical == "era" else mechanical_forge)(c)


def st_burndown(c):
    log = c["dir"] / "file-loop.jsonl"
    r = sh([sys.executable, str(ROOT / "tools/file-loop.py"), "--work", str(c["repo"]), "--budget",
            str(c["args"].budget), "--log", str(log)], env=dict(os.environ, PORT_LOG_DIR=str(c["dir"])),
           timeout=4 * 3600)
    end = next((json.loads(l) for l in reversed(r.stdout.splitlines()) if '"event": "end"' in l), None)
    if not end:
        raise Fail(f"file-loop ended without a result: {r.stdout[-600:]}")
    if end["errors"]:
        raise Fail(f"file-loop stopped at {end['errors']} errors (${end['spent']:.2f}); see {log}", usd=end["spent"])
    return {"usd": end["spent"], "stubs": end.get("stubs"), "by_model": end.get("by_model"),
            "mixins_registered": register_new_mixins(c["repo"], c["args"].base)}


def register_new_mixins(repo, base):
    """Register every @Mixin class the PORT added (absent at the base commit) in the config whose package holds
    it: javac cannot see an unregistered mixin, and the first cast to it is a runtime ClassCastException
    (catalog H#43). Client-side if it imports client classes, else common. Upstream mixins are never touched."""
    added = []
    for cfg in srcsets.mixin_configs(repo):
        t = cfg.read_text(encoding="utf-8")
        d = json.loads(t)
        listed = {n for k in ("mixins", "client", "server") for n in d.get(k) or []}
        for jd in srcsets.java_dirs(repo):
            pdir = jd / d["package"].replace(".", "/")
            for f in sorted(pdir.rglob("*.java")) if pdir.is_dir() else []:
                rel = f.relative_to(repo).as_posix()
                if base and sh(["git", "cat-file", "-e", f"{base}:{rel}"], cwd=repo).returncode == 0:
                    continue
                src = f.read_text(encoding="utf-8", errors="replace")
                name = f.relative_to(pdir).with_suffix("").as_posix().replace("/", ".")
                if "@Mixin" not in src or name in listed:
                    continue
                key = "client" if re.search(r"(?m)^import\s+(net\.minecraft\.client|com\.mojang\.blaze3d)\.", src) else "mixins"
                m = re.search(r'("%s"\s*:\s*\[)(.*?)(\s*)\]' % key, t, re.S)
                if m and m.group(2).strip():
                    ind = re.search(r"\n([ \t]*)\"[^\"]*\"\s*$", m.group(2))
                    ins = ',\n%s"%s"' % (ind.group(1) if ind else "    ", name)
                    t = t[:m.end(2)] + ins + t[m.end(2):]
                else:
                    d = json.loads(t); d.setdefault(key, []).append(name); t = json.dumps(d, indent=2) + "\n"
                listed.add(name); added.append(f"{cfg.name}:{key}:{name}")
        cfg.write_text(t, encoding="utf-8")
    return added


def st_gates(c):
    ran = gate_a(c, c["dir"] / "gateA.log")
    env = gate_env(c)
    log = c["dir"] / "gate-loop.jsonl"
    r = sh([sys.executable, str(ROOT / "tools/gate-loop.py"), "--work", str(c["repo"]), "--namespace", c["args"].modid,
            "--budget", str(min(c["args"].budget, 8)), "--log", str(log)], env=env, timeout=4 * 3600)
    end = next((json.loads(l) for l in reversed(r.stdout.splitlines())
                if re.search(r'"event": "(green|stuck|budget|max-runs)"', l)), None)
    if not end or end["event"] != "green":
        raise Fail(f"Gate B not green ({end and end['event']}): {(r.stdout or r.stderr)[-800:]}",
                   usd=(end or {}).get("spent", 0))
    return {"gateA_tests": ran, "usd": end.get("spent", 0), "gateB_runs": end.get("run")}


def st_author_build(c):
    """The author's own `build` plus every Jar task they registered, with NONE of the port's harness -- what a
    contributor runs after cloning. Our gates compile and test what we wired; this proves the author's build
    still works (measured: a port green on every gate failed `build` in two unported variant source sets).
    The Central mirror init script is the one addition: it only rewrites repository URLs."""
    jars = srcsets.author_jar_tasks(c["repo"])
    log = c["dir"] / "author-build.log"
    r = gradle(c["repo"], ["build", *jars, "--continue"], log)
    if r.returncode:
        n, line = compile_count(c["repo"], c["dir"] / "author-build-count.log")
        raise Fail(f"the author's build fails ({line}); see {log}")
    built = sorted(p.name for p in (c["repo"] / "build/libs").glob("*.jar")) if (c["repo"] / "build/libs").is_dir() else []
    return {"tasks": ["build", *jars], "jars": built}


def st_normalise(c):
    return {"out": [tool("normalise-imports.py", "--src", d, "--base", c["args"].base) for d in srcsets.java_dirs(c["repo"])]}


def tree_fingerprint(repo):
    """The working tree's content, committed or not: diff against HEAD plus untracked files."""
    import hashlib
    d = sh(["git", "diff", "HEAD", "--", "."], cwd=repo).stdout
    u = sh(["git", "ls-files", "-o", "--exclude-standard", "-z"], cwd=repo).stdout
    h = hashlib.sha1((d + u).encode("utf-8", "replace"))
    for f in sorted(u.split("\0")):
        if f and (repo / f).is_file():
            h.update((repo / f).read_bytes())
    return h.hexdigest()


def st_reviewer(c):
    repo, base = c["repo"], c["args"].base
    before = tree_fingerprint(repo)
    metrics = tool("review-metrics.py", "--repo", repo, "--base", base, "--fix", "--sites", 12)
    stat = sh(["git", "diff", "--stat", "-M", base, "--", "."], cwd=repo).stdout
    principles = (ROOT / "templates/upstream-harness/REVIEW_PRINCIPLES.md").read_text(encoding="utf-8")
    principles = principles[principles.index("| #"):principles.index("P6 and P7 are gates")]
    prompt = (ROOT / "templates/upstream-harness/reviewer-prompt.md").read_text(encoding="utf-8")
    tg = tgt(c)
    note = ("" if tg.mechanical == "forge-1.20.1" else
            f" `{base}` is our finished NeoForge 1.21.1 port of the author's code, so the diff is only the hop to {tg.mc}: judge "
            "what the hop changed, against that branch, and do not reopen the 1.21.1 port.")
    prompt = (prompt.replace("{BASE_NOTE}", note).replace("{BASE}", base).replace("{TARGET}", srcsets.target(c["repo"]) or tg.name)
              .replace("{STAT}", stat[-6000:]).replace("{PRINCIPLES}", principles)
              .replace("{METRICS}", metrics[-6000:] or "(nothing found)"))
    text, usd = claude(prompt, repo, "Read,Edit,Grep,Glob,Bash(git diff:*),Bash(git show:*),Bash(git log:*)")
    (c["dir"] / "REVIEW.md").write_text(text, encoding="utf-8")
    n, line = compile_count(repo, c["dir"] / "review-compile.log")
    if n:
        raise Fail(f"the reviewer's edits broke the compile ({n} errors); see REVIEW.md and review-compile.log")
    gate_a(c, c["dir"] / "gateA-after-review.log")
    regated = None
    if tree_fingerprint(repo) != before:
        # the reviewer cannot run Gradle, so its edits reach no gate unless we run them: Gate A alone missed
        # nothing here only by luck; a renamed hook or moved registration fails at load, i.e. Gate B
        r = sh([sys.executable, str(ROOT / "tools/gate-loop.py"), "--work", str(repo), "--namespace", c["args"].modid,
                "--budget", "2", "--log", str(c["dir"] / "gate-loop-after-review.jsonl")], env=gate_env(c), timeout=2 * 3600)
        end = next((json.loads(l) for l in reversed(r.stdout.splitlines())
                    if re.search(r'"event": "(green|stuck|budget|max-runs)"', l)), None)
        if not end or end["event"] != "green":
            raise Fail(f"Gate B is not green after the reviewer's edits ({end and end['event']})")
        usd_b = end.get("spent", 0); regated = {"gateB_runs": end.get("run"), "usd": usd_b}
    scores = re.findall(r"(?m)^\W*(P\d)\W.*?(✅|⚠️|❌)", text)
    return {"usd": usd + ((regated or {}).get("usd") or 0), "review": str(c["dir"] / "REVIEW.md"),
            "scores": dict(scores), "metrics": metrics, "regated_after_review": regated}


def st_provenance(c):
    repo, base, lic = c["repo"], c["args"].base, c["state"].get("license", {}).get("result", {})
    names = sh(["git", "diff", "--name-status", base, "--", "."], cwd=repo).stdout
    bad = [l for l in names.splitlines() if re.search(r"(?i)\b(licen[cs]e|copying|notice)[^/]*$", l)]
    diff = sh(["git", "diff", "-U0", base, "--", "src"], cwd=repo).stdout
    removed = [l for l in diff.splitlines() if l.startswith("-") and re.search(r"(?i)copyright|SPDX", l)]
    added = [l for l in diff.splitlines() if l.startswith("+") and re.search(r"(?i)copyright|SPDX-", l)]
    # nothing of the porting machinery may reach the author's repo: logs, harness files, machine paths,
    # session details, or the names of this repository's tools and catalogue
    allowed = re.compile(r"^(src/.+|build\.gradle|settings\.gradle|gradle\.properties|gradle/wrapper/.+)$")
    added_files = [l.split("\t")[-1] for l in names.splitlines() if l[:1] in "AR"]
    stray = [f for f in added_files if not allowed.match(f)]
    leak = re.compile(r"/tmp/|/home/\w|/root/|\.mc-mod-upgrade|claude\.ai|Claude-Session|JAVA_TOOL_OPTIONS|proxyHost"
                      r"|CATALOG|§[A-Z]?\d|forge-shapes|fix-holders|convert-simplechannel|port-upstream|file-loop|gate-loop"
                      r"|BaselineGameTest|ClientBootSmokeTest|mc-mod-version-upgrade")
    extra = [l.strip() for l in pathlib.Path(c["args"].leak_names).read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.startswith("#")] if getattr(c["args"], "leak_names", None) else []
    full = sh(["git", "diff", "-U0", base, "--", "."], cwd=repo).stdout
    leaks = [l for l in full.splitlines() if l.startswith("+") and not l.startswith("+++")
             and (leak.search(l) or any(x.lower() in l.lower() for x in extra))]
    problems = []
    if stray:
        problems.append("files added outside the source and build files: " + ", ".join(stray[:6]))
    if leaks:
        problems.append(f"{len(leaks)} added line(s) carry porting-machine details, e.g. {leaks[0][:120]}")
    if bad:
        problems.append("licence files changed: " + "; ".join(bad))
    if removed:
        problems.append(f"{len(removed)} copyright/SPDX line(s) removed, e.g. {removed[0][:100]}")
    if added:
        problems.append(f"{len(added)} copyright/SPDX line(s) added, e.g. {added[0][:100]}")
    if problems:
        raise Fail(" | ".join(problems))
    return {"licence": lic.get("licence"), "files_kept": lic.get("file")}


def st_report(c):
    rows, total = [], 0.0
    for s in STAGES[:-1]:
        e = c["state"].get(s)
        if not e:
            continue
        usd = e.get("usd_all", e.get("usd") or (e.get("result") or {}).get("usd") or 0.0)
        total += usd
        runs = f" ({e['runs']} runs)" if e.get("runs", 1) > 1 else ""
        rows.append(f"| {s}{runs} | ${usd:.2f} | {e.get('secs_all', e.get('secs', 0)) // 60} min |")
    lic = c["state"].get("license", {}).get("result", {})
    md = ["| stage | model cost | wall time |", "|---|---|---|", *rows, f"| **total** | **${total:.2f}** | |", "",
          f"Licence: {lic.get('licence')} ({', '.join(lic.get('file') or [])}); notices retained."
          + (f" Author's permission: {lic['permission']}" if lic.get("permission") else "")]
    # P8: each claim comes from a stage result, and what did not run is said, not omitted
    res = lambda s: (c["state"].get(s) or {}).get("result") or {}
    g, ab = res("gates"), res("author-build")
    md += ["", "Verified:",
           f"- Gate A: {g['gateA_tests']} test(s) passed" if g.get("gateA_tests") else "- Gate A: NOT RUN",
           f"- Gate B: green after {g.get('gateB_runs')} run(s)" if g else "- Gate B: NOT RUN",
           *([f"- Gate A and Gate B re-run after the reviewer's edits: green"] if res("reviewer").get("regated_after_review") else []),
           (f"- the author's own `{' '.join(ab['tasks'])}` succeeds with none of the port's harness, its own tests "
            f"included ({len(ab.get('jars', []))} jar(s))") if ab else "- the author's own build: NOT RUN",
           "Not verified here: Gate C (real client) unless recorded separately; gameplay by a person."]
    (c["dir"] / "COST.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    msg = commit_message(c, md)
    (c["dir"] / "COMMIT_MSG.md").write_text(msg, encoding="utf-8")
    sha = None
    if not c["args"].no_commit:                      # the record goes where the author will read it: the commit
        sh(["git", "-C", str(c["repo"]), "add", "-A", "--", "src", "build.gradle", "settings.gradle", "gradle.properties",
            "gradle"])
        if sh(["git", "-C", str(c["repo"]), "diff", "--cached", "--quiet"]).returncode:
            r = sh(["git", "-C", str(c["repo"]), "commit", "-q", "-F", str(c["dir"] / "COMMIT_MSG.md")])
            if r.returncode:
                raise Fail(f"final commit failed: {r.stderr or r.stdout}")
        sha = sh(["git", "-C", str(c["repo"]), "rev-parse", "--short", "HEAD"]).stdout.strip()
    return {"total_usd": round(total, 2), "table": "\n".join(md), "commit": sha}


def commit_message(c, md):
    """The final commit's message: what changed, the review, what was verified and what was not, the cost."""
    res = lambda s: (c["state"].get(s) or {}).get("result") or {}
    target = srcsets.target(c["repo"]) or "the target"
    stat = sh(["git", "-C", str(c["repo"]), "diff", "--shortstat", c["args"].base or "HEAD"]).stdout.strip()
    mech, burn = res("mechanical"), res("burndown")
    counts = mech.get("errors") or []
    hop = tgt(c).mechanical == "era"
    lines = [f"Port to {target}", "",
             (f"Era hop of our NeoForge 1.21.1 port of the author's code; {stat or 'see the diff'}." if hop else
              f"Source-first port of the author's own code; {stat or 'see the diff'}.")]
    if counts:
        lines.append(f"Compile errors after the deterministic tools: {counts[-1]}; burn-down to 0 by workers"
                     + (f" ({burn.get('stubs')} stub(s))" if burn.get("stubs") is not None else "") + ".")
    review = c["dir"] / "REVIEW.md"
    if review.exists():
        text = review.read_text(encoding="utf-8")
        for head in ("Scores", "Needs a person"):
            m = re.search(r"(?ms)^## %s\s*\n(.*?)(?=^## |\Z)" % re.escape(head), text)
            if m and m.group(1).strip():
                lines += ["", f"Review -- {head.lower()}:", m.group(1).strip()]
    lines += ["", *md]
    lines += ["", *[f"{t_}" for t_ in (c["args"].trailer or [])]] if c["args"].trailer else []
    return "\n".join(lines).rstrip() + "\n"


FUNCS = {s: globals()["st_" + s.replace("-", "_")] for s in STAGES}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    if "--self-check" in sys.argv:
        return self_check()
    ap.add_argument("--repo", required=True); ap.add_argument("--modid", required=True)
    ap.add_argument("--branch", default="neoforge-1.21.1"); ap.add_argument("--base", default=None)
    ap.add_argument("--permission"); ap.add_argument("--budget", type=float, default=20)
    ap.add_argument("--leak-names", help="file of further names (one per line) that must not appear in the diff, "
                                         "e.g. private repositories; kept out of this public repository")
    ap.add_argument("--from", dest="start", choices=STAGES); ap.add_argument("--only", choices=STAGES)
    ap.add_argument("--record", help="STAGE=USD for a stage run by hand"); ap.add_argument("--report", action="store_true")
    ap.add_argument("--no-commit", action="store_true", help="write COMMIT_MSG.md but do not make the final commit")
    ap.add_argument("--provide", action="append", metavar="OLD=NEW",
                    help="a dependency ported alongside this mod: author's coordinate prefix = mavenLocal coordinate")
    ap.add_argument("--trailer", action="append", help="a line appended to the final commit (attribution trailers)")
    ap.add_argument("--lib-tree", action="append", metavar="DIR",
                    help="26.2: a dependency's ported source tree (its src/main/java on the target branch), read only, "
                         "so entity-renderstate and GeckoLib conversion can resolve classes it supplies (repeatable)")
    ap.add_argument("--render-state-type", help="26.2: the type a custom ShaderStateShard field becomes (convert-rendertypes)")
    ap.add_argument("--render-state-factory", help="26.2: the call that builds it (convert-rendertypes)")
    ap.add_argument("--shader-block", help="26.2: run convert-core-shaders with this uniform block name")
    a = ap.parse_args()
    repo = pathlib.Path(a.repo).resolve()
    try:
        target = targets.target_of(a.branch)
        if a.base is None:           # a hop is cut from our previous port branch; a first port from the author's default
            a.base = targets.default_base(target, lambda ref: sh(["git", "rev-parse", "--verify", "-q", ref + "^{commit}"],
                                                                 cwd=repo).returncode == 0)
    except targets.UnknownTarget as e:
        print(f"[port-upstream] {e}", file=sys.stderr)
        return 2
    if a.base is None:
        a.base = sh(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=repo).stdout.strip() or "master"
    d = pathlib.Path(os.path.expanduser(f"~/.mc-mod-upgrade/upstream/{repo.name}-{a.branch}"))
    d.mkdir(parents=True, exist_ok=True)
    sf = d / "state.json"
    state = json.loads(sf.read_text(encoding="utf-8")) if sf.exists() else {}
    c = {"repo": repo, "args": a, "dir": d, "state": state, "target": target}
    save = lambda: sf.write_text(json.dumps(state, indent=1), encoding="utf-8")
    if a.record:
        s, usd = a.record.split("=")
        state[s] = {"status": "done", "usd": float(usd), "secs": 0, "by_hand": True}; save()
        print(f"recorded {s} = ${float(usd):.2f}"); return 0
    todo = [a.only] if a.only else (["report"] if a.report else STAGES[STAGES.index(a.start):] if a.start else
                                     [s for s in STAGES if state.get(s, {}).get("status") != "done"])
    for s in todo:
        t0 = time.time()
        print(f"[port-upstream] {s} ...", flush=True)
        try:
            res = FUNCS[s](c)
        except Fail as e:
            prev, secs = state.get(s) or {}, int(time.time() - t0)
            # a failed attempt still cost money; the next successful run adds to it
            state[s] = {"status": "failed", "why": str(e), "secs": secs, "runs": prev.get("runs", 0) + 1,
                        "usd": e.usd, "usd_all": prev.get("usd_all", prev.get("usd", 0.0)) + e.usd,
                        "secs_all": prev.get("secs_all", prev.get("secs", 0)) + secs}; save()
            print(f"[port-upstream] {s} FAILED: {e}\n  state: {sf}", flush=True)
            return 1
        prev = state.get(s) or {}
        usd, secs = (res or {}).get("usd", 0.0), int(time.time() - t0)
        # a re-run ADDS to what the stage already cost: overwriting dropped a gate loop's $1.48 from the table
        state[s] = {"status": "done", "secs": secs, "usd": usd, "result": res, "runs": prev.get("runs", 0) + 1,
                    "usd_all": prev.get("usd_all", prev.get("usd", 0.0)) + usd,
                    "secs_all": prev.get("secs_all", prev.get("secs", 0)) + secs}
        save()
        print(f"[port-upstream] {s} done ({state[s]['secs']}s, ${state[s]['usd']:.2f})", flush=True)
    if "report" in todo:
        print(state["report"]["result"]["table"])
    return 0


def self_check():
    """The two stages that must never pass wrongly: licence (first) and provenance (last)."""
    import tempfile, types
    ok = True
    with tempfile.TemporaryDirectory() as d:      # deps.json's resolved target builds land in the build files
        d = pathlib.Path(d)
        (d / "build.gradle").write_text('implementation "curse.maven:lib-1:100"\nimplementation "a:b:${b_v}"\n',
                                        encoding="utf-8")
        (d / "gradle.properties").write_text("b_v=1.0+1.20.1\n", encoding="utf-8")
        (d / "deps.json").write_text(json.dumps({"dependencies": [
            {"group": "curse.maven", "artifact": "lib-1", "suggested": {"coord": "curse.maven:lib-1:200",
                                                                       "change": "build.gradle literal: 100 -> 200"}},
            {"group": "a", "artifact": "b", "suggested": {"coord": "a:b:2.0", "change": "gradle.properties: b_v=1.0+1.20.1 -> 2.0+1.21.1"}},
            {"group": "x", "artifact": "y", "provided": "z:y:1", "suggested": {"coord": "z:y:1", "change": "literal: 1 -> 2"}}]}),
            encoding="utf-8")
        first = apply_dep_versions(d, d / "deps.json")
        ok &= len(first) == 2 and apply_dep_versions(d, d / "deps.json") == []
        ok &= '"curse.maven:lib-1:200"' in (d / "build.gradle").read_text(encoding="utf-8")
        ok &= "b_v=2.0+1.21.1" in (d / "gradle.properties").read_text(encoding="utf-8")
        (d / "build.gradle").write_text('dependencies {\n    implementation "net.x:lib:1.0"\n    implementation "a:b:1"\n}\n',
                                        encoding="utf-8")
        ok &= provide_nontransitive(d, [("old:x", "net.x:lib:1.0")]) == ["net.x:lib:1.0"]
        ok &= provide_nontransitive(d, [("old:x", "net.x:lib:1.0")]) == []
        ok &= 'implementation("net.x:lib:1.0") { transitive = false }' in (d / "build.gradle").read_text(encoding="utf-8")
        (d / "build.gradle").write_text('version = "${mc}-forge-${v}"\n'
                                        "systemProperty 'forge.logging.markers', 'REGISTRIES'\n", encoding="utf-8")
        (d / "gradle.properties").write_text("archives_base_name=mymod-forge\nloader=forge\n", encoding="utf-8")
        ok &= len(loader_in_jar_name(d)) == 2 and loader_in_jar_name(d) == []
        bg, gp = (d / "build.gradle").read_text(encoding="utf-8"), (d / "gradle.properties").read_text(encoding="utf-8")
        ok &= 'version = "${mc}-neoforge-${v}"' in bg and "'forge.logging.markers'" in bg
        ok &= "archives_base_name=mymod-neoforge" in gp and "loader=forge" in gp
    with tempfile.TemporaryDirectory() as d:
        r = pathlib.Path(d)
        (r / "src/main/resources/META-INF").mkdir(parents=True)
        mit = "The MIT License (MIT)\nCopyright (c) 2026 Someone\n\nPermission is hereby granted, free of charge, ..."
        (r / "LICENSE.txt").write_text(mit, encoding="utf-8")
        (r / "src/main/resources/META-INF/mods.toml").write_text('license = "MIT"\n', encoding="utf-8")
        c = {"repo": r, "args": types.SimpleNamespace(permission=None, base="HEAD"), "dir": r, "state": {}}
        ok &= st_license(c)["licence"] == "MIT"
        (r / "src/main/resources/META-INF/mods.toml").write_text('license = "All rights reserved"\n', encoding="utf-8")
        try:
            st_license(c); ok = False                     # file says MIT, toml says ARR: must stop
        except Fail:
            pass
        (r / "LICENSE.txt").unlink()
        try:
            st_license(c); ok = False                     # nothing found: all rights reserved, must stop
        except Fail:
            pass
        c["args"].permission = "https://example.invalid/issue/1"
        ok &= st_license(c)["permission"] is not None     # a recorded permission lets it through
        # provenance: a licence file edited, an author copyright line removed
        (r / "LICENSE.txt").write_text(mit, encoding="utf-8")
        (r / "src/main/java").mkdir(parents=True)
        (r / "src/main/java/A.java").write_text("// Copyright 2026 Someone\nclass A {}\n", encoding="utf-8")
        for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                    ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"]):
            subprocess.run(cmd, cwd=r, check=True)
        c["state"] = {"license": {"result": {"licence": "MIT", "file": ["LICENSE.txt"]}}}
        ok &= st_provenance(c)["licence"] == "MIT"        # untouched tree passes
        (r / "file-loop-compile.log").write_text("log\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=r, check=True)
        try:
            st_provenance(c); ok = False                  # a tool's log committed into the author's tree must stop
        except Fail:
            pass
        subprocess.run(["git", "rm", "-q", "--cached", "file-loop-compile.log"], cwd=r, check=True)
        (r / "file-loop-compile.log").unlink()
        (r / "src/main/java/A.java").write_text("class A {}\n", encoding="utf-8")
        try:
            st_provenance(c); ok = False                  # an author copyright line removed must stop
        except Fail:
            pass
    with tempfile.TemporaryDirectory() as d:                 # register_new_mixins: only port-added, side by imports
        r = pathlib.Path(d); md = r / "src/main/java/a/mixin"; md.mkdir(parents=True)
        (r / "src/main/resources").mkdir(parents=True)
        cfg = r / "src/main/resources/mixins.x.json"
        cfg.write_text('{\n  "package": "a.mixin",\n  "mixins": [\n    "Old"\n  ]\n}\n', encoding="utf-8")
        (md / "Old.java").write_text("@Mixin(X.class) class Old {}", encoding="utf-8")
        (md / "Stray.java").write_text("@Mixin(X.class) class Stray {}", encoding="utf-8")
        sh("git init -q && git add -A && git -c user.email=a@b -c user.name=a commit -qm base", cwd=r)
        (md / "NewAcc.java").write_text("@Mixin(Y.class) interface NewAcc {}", encoding="utf-8")
        (md / "NewCli.java").write_text("import net.minecraft.client.Minecraft;\n@Mixin(Z.class) class NewCli {}", encoding="utf-8")
        got = register_new_mixins(r, "HEAD")
        dj = json.loads(cfg.read_text(encoding="utf-8"))
        ok &= dj["mixins"] == ["Old", "NewAcc"] and dj["client"] == ["NewCli"] and len(got) == 2
        ok &= register_new_mixins(r, "HEAD") == []
    with tempfile.TemporaryDirectory() as d:     # metadata: declare a non-<modid> config; one-line refmap; client move
        r = pathlib.Path(d); res = r / "src/main/resources"; (res / "META-INF").mkdir(parents=True)
        (res / "META-INF/mods.toml").write_text('modLoader="javafml"\nloaderVersion="[47,)"\n', encoding="utf-8")
        (res / "mixins.foo.json").write_text('{"package":"a.mixin","refmap":"x.refmap.json","compatibilityLevel":"JAVA_17",'
                                             '"mixins":["Common","Gfx"]}', encoding="utf-8")
        md = r / "src/main/java/a/mixin"; md.mkdir(parents=True)
        (md / "Common.java").write_text("import net.minecraft.world.entity.Mob;\n@Mixin(Mob.class) class Common {}", encoding="utf-8")
        (md / "Gfx.java").write_text("import software.bernie.geckolib.renderer.GeoEntityRenderer;\n"
                                     "@Mixin(GeoEntityRenderer.class) class Gfx {}", encoding="utf-8")
        sh("git init -q && git add -A && git -c user.email=a@b -c user.name=a commit -qm base", cwd=r)
        global tool
        real_tool, tool = tool, (lambda *a, **k: "")
        try:
            out = st_metadata({"repo": r, "args": types.SimpleNamespace(modid="foo"), "target": targets.TARGETS["1.21.1"]})
        finally:
            tool = real_tool
        toml = (res / "META-INF/neoforge.mods.toml").read_text(encoding="utf-8")
        dj = json.loads((res / "mixins.foo.json").read_text(encoding="utf-8"))
        ok &= ('config = "mixins.foo.json"' in toml and "refmap" not in dj and dj["compatibilityLevel"] == "JAVA_21"
               and dj["mixins"] == ["Common"] and dj["client"] == ["Gfx"] and out["moved_to_client"] == ["mixins.foo.json:Gfx"])
    ok &= self_check_targets()
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


class patched:
    """Swap module-level functions for the duration of a with-block (the stages call them by global name)."""
    def __init__(self, **kw):
        self.kw = kw

    def __enter__(self):
        self.old = {k: globals()[k] for k in self.kw}
        globals().update(self.kw)

    def __exit__(self, *exc):
        globals().update(self.old)


def self_check_targets():
    """The target-aware paths, model-free: 1.21.1 is what it always was, 26.2 reads its facts from the table."""
    import tempfile, types
    ok = True

    def chk(name, cond):
        nonlocal ok
        if not cond:
            print("  FAIL", name)
        ok &= bool(cond)

    def git(r, *a):
        return subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=a", *a], cwd=r, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    t1, t2 = targets.TARGETS["1.21.1"], targets.TARGETS["26.2"]
    # the prompts a 1.21.1 run sends are the ones it always sent
    chk("1.21.1 build prompt", targets.build_prompt(t1, "", "D").startswith("Convert this mod's Gradle build to NeoForge 1.21.1 "
        "with ModDevGradle (id 'net.neoforged.moddev' version '2.0.146'), following the BUILD section") and
        targets.build_prompt(t1, "P\n\n", "D").endswith("Do not touch Java sources.\n\nP\n\nD"))
    tpl = (ROOT / "templates/upstream-harness/designer-prompt.md").read_text(encoding="utf-8")
    d1, d2 = targets.designer_prompt(tpl, t1, "neoforge-1.21.1"), targets.designer_prompt(tpl, t2, "neoforge-26.2")
    chk("designer prompt names the real source and target", "Forge 1.20.1, ForgeGradle) to NeoForge 1.21.1." in d1
        and "later a `neoforge-26.2` branch follows" in d1 and "already ported by us to NeoForge 1.21.1" in d2
        and "to Minecraft 26.2 / NeoForge 26.2.0.75." in d2 and "{" not in d1 + d2)
    # metadata on a 26.2 hop: the neoforge.mods.toml is rewritten in place (no git mv), formats and ranges from the table
    with tempfile.TemporaryDirectory() as d:
        r = pathlib.Path(d); res = r / "src/main/resources"; (res / "META-INF").mkdir(parents=True)
        (res / "META-INF/neoforge.mods.toml").write_text('loaderVersion = "[4,)"\n[[dependencies.m]]\nmodId="neoforge"\n'
            'type = "required"\nversionRange="[21.1,)"\n[[dependencies.m]]\nmodId="minecraft"\nversionRange="[1.21.1,1.22)"\n'
            '[[dependencies.m]]\nmodId="lib"\nversionRange="[4.7,)"\n', encoding="utf-8")
        (res / "m.mixins.json").write_text('{"package":"a.mixin","compatibilityLevel":"JAVA_21","mixins":[]}', encoding="utf-8")
        (res / "pack.mcmeta").write_text('{\n  "pack": {\n    "pack_format": 34\n  }\n}\n', encoding="utf-8")
        (r / "src/main/java/a/mixin").mkdir(parents=True)
        git(r, "init", "-q"); git(r, "add", "-A"); git(r, "commit", "-qm", "base")
        with patched(tool=lambda *a, **k: ""):
            out = st_metadata({"repo": r, "args": types.SimpleNamespace(modid="m"), "target": t2})
        toml = (res / "META-INF/neoforge.mods.toml").read_text(encoding="utf-8")
        pack = json.loads((res / "pack.mcmeta").read_text(encoding="utf-8"))["pack"]
        chk("26.2 metadata", 'versionRange="[26.2,)"' in toml and 'versionRange="[26.2,26.3)"' in toml
            and '[[mixins]]\nconfig = "m.mixins.json"' in toml and pack["min_format"] and "pack_format" not in pack
            and out["dependency_ranges_to_review"] == ["lib [4.7,)"] and out["pack_formats"]["data"][0] == 107)
        # a second run changes nothing
        with patched(tool=lambda *a, **k: ""):
            again = st_metadata({"repo": r, "args": types.SimpleNamespace(modid="m"), "target": t2})
        chk("26.2 metadata is idempotent", again["files"] == [])
    # the build stage: a version bump needs no model; what is still missing is what the model is asked
    with tempfile.TemporaryDirectory() as d:
        r = pathlib.Path(d); (r / "src/main/java").mkdir(parents=True)
        (r / "src/main/java/A.java").write_text("import software.bernie.geckolib.animatable.GeoEntity;\nclass A {}\n", encoding="utf-8")
        (r / "build.gradle").write_text("plugins { id 'net.neoforged.moddev' version '2.0.146' }\n"
            "java.toolchain.languageVersion = JavaLanguageVersion.of(21)\nneoForge {\n    version = neo_version\n}\n"
            "repositories {\n    mavenCentral()\n}\ndependencies {\n"
            "    implementation(\"software.bernie.geckolib:geckolib-neoforge-${minecraft_version}:${geckolib_version}\")\n}\n", encoding="utf-8")
        (r / "gradle.properties").write_text("minecraft_version=1.21.1\nneo_version=21.1.172\ngeckolib_version=4.7.1\n", encoding="utf-8")
        asked = []
        c = {"repo": r, "args": types.SimpleNamespace(provide=None), "dir": r, "state": {}, "target": t2}
        with patched(compile_count=lambda repo, log: (1500, "errors = 1500"), claude=lambda *a, **k: asked.append(a) or ("", 0.25),
                     raise_neo_floor=lambda c: False):
            res = st_build(c)
        chk("26.2 build: deterministic, no model", not asked and res["usd"] == 0 and res["first_count"] == 1500
            and "interfaceInjectionData" in (r / "build.gradle").read_text(encoding="utf-8") and len(res["bumped"]) >= 5)
        (r / "build.gradle").write_text((r / "build.gradle").read_text(encoding="utf-8").replace("of(25)", "of(21)"), encoding="utf-8")
        c2 = dict(c); c2["state"] = {}
        calls = []

        def fix(prompt, *a, **k):
            calls.append(prompt)
            (r / "build.gradle").write_text((r / "build.gradle").read_text(encoding="utf-8").replace("of(21)", "of(25)"), encoding="utf-8")
            return "", 0.5
        with patched(compile_count=lambda repo, log: (1500, "errors = 1500"), claude=fix, raise_neo_floor=lambda c: False,
                     bump_build_files=lambda c: []):
            res = st_build(c2)
        chk("26.2 build: the model is asked only what the post-check names", len(calls) == 1 and "Java toolchain must be 25" in calls[0]
            and res["usd"] == 0.5)
    # mechanical, 26.2: renames in place with line endings kept, stages in order, a count after each step
    with tempfile.TemporaryDirectory() as d:
        r = pathlib.Path(d) / "repo"; w = pathlib.Path(d) / "work"; w.mkdir()
        java = r / "src/main/java/p"; java.mkdir(parents=True)
        (java / "A.java").write_bytes(b"package p;\r\nimport net.minecraft.resources.ResourceLocation;\r\nclass A { ResourceLocation x; }\r\n")
        (java / "B.java").write_bytes(b"package p;\nclass B { }\n")
        (r / "src/main/resources").mkdir(parents=True)
        (r / "gradle.properties").write_text("minecraft_version=26.2\nneo_version=26.2.0.75\nmod_id=m\n", encoding="utf-8")
        (r / "build.gradle").write_text("// no geckolib\n", encoding="utf-8")
        mv = pathlib.Path(d) / "moves.tsv"; mv.write_text("net.minecraft.world.entity.monster.Zombie\tnet.minecraft.world.entity.monster.zombie.Zombie\n", encoding="utf-8")
        cv = pathlib.Path(d) / "colors.tsv"; cv.write_text("", encoding="utf-8")
        git(r, "init", "-q"); git(r, "add", "-A"); git(r, "commit", "-qm", "base")
        counts = iter([900, 700, 650, 640, 640, 640, 640, 640, 640, 640, 640, 640, 640])
        c = {"repo": r, "args": types.SimpleNamespace(modid="m", base="HEAD", lib_tree=None, render_state_type=None,
             render_state_factory=None, shader_block=None), "dir": w, "state": {}, "target": t2}
        import contextlib, io
        with patched(compile_count=lambda repo, log: (next(counts), "x"), at_loop=lambda c: 700,
                     era_maps=lambda c: (mv, cv, "cached")), contextlib.redirect_stdout(io.StringIO()):
            rep = mechanical_era(c)
        a_bytes = (java / "A.java").read_bytes()
        chk("26.2 mechanical applies the table in place, CRLF kept", b"Identifier" in a_bytes and b"ResourceLocation" not in a_bytes
            and a_bytes.count(b"\r\n") == 3 and (java / "B.java").read_bytes() == b"package p;\nclass B { }\n")
        names = [s["step"] for s in rep["steps"]]
        chk("26.2 mechanical order", names[:4] == ["start", "renames", "members", "gui-hooks"] and names[-1] == "members-after-converters"
            and "valueio" in names and "entity-renderstate" in names and "geckolib" not in names, )
        chk("26.2 mechanical reports counts", rep["errors"][0] == 900 and rep["errors"][1] == 700 and rep["renames"]["rewrites"] >= 1)
    # the patch format, and the shipped patch against the shipped client harness template: every block matches once
    chk("patch blocks parse", read_patch("# c\n@@ old\na\nb\n@@ new\nc\n@@\n") == [("a\nb", "c")])
    for bad in ("x\n", "@@ old\na\n", "@@ new\n"):
        try:
            read_patch(bad); chk("malformed patch rejected", False)
        except ValueError:
            pass
    try:
        apply_patch("a a", [("a", "b")]); chk("an ambiguous block is refused", False)
    except Fail:
        pass
    try:
        apply_patch("q", [("a", "b")]); chk("a dead block is refused", False)
    except Fail:
        pass
    # the harness for a target without @GameTest discovery is wired by registration and renamed, never the author's tree
    with tempfile.TemporaryDirectory() as d:
        r = pathlib.Path(d) / "repo"; (r / "src/main/java/q").mkdir(parents=True)
        (r / "src/main/java/q/Mod.java").write_text("package com.example.mod;\nclass Mod {}\n", encoding="utf-8")
        (r / "gradle.properties").write_text("minecraft_version=26.2\n", encoding="utf-8")
        h = pathlib.Path(d) / "h"
        j = h / "java/com/example"; j.mkdir(parents=True)
        (j / "T.java").write_text("package com.example;\nimport net.minecraft.gametest.framework.GameTest;\n"
                                  "import net.minecraft.resources.ResourceLocation;\n"
                                  "class T {\n @GameTest(template = \"empty_test\")\n public static void a(GameTestHelper h) { ResourceLocation r = null; } }\n",
                                  encoding="utf-8")
        harness_registration({"repo": r, "args": types.SimpleNamespace(modid="m"), "target": t2}, h, "com.example")
        gen = (h / "java/com/example/gatetest/GeneratedGameTests.java")
        body = (j / "T.java").read_text(encoding="utf-8")
        chk("26.2 harness: renamed, annotation gone, registrar generated", gen.exists() and (h / "java/com/example/gatetest/DirectGameTest.java").exists()
            and "Identifier" in body and "@GameTest" not in body and "com.example.T::a" in gen.read_text(encoding="utf-8"))
        chk("target is read from the repo for a caller with no arguments", tgt({"repo": r}).mc == "26.2")
        # ... and the real client harness template survives the renames plus the shipped patch (each block matches once)
        sgc = _load_tool("sgc_check", "scaffold-gatec.py")
        h2 = pathlib.Path(d) / "h2"; (h2 / "java/com/example/test").mkdir(parents=True)
        (h2 / "java/com/example/test/ClientBootSmokeTest.java").write_text(
            sgc.render(sgc.TEMPLATE.read_text(encoding="utf-8"), "m", "com.example"), encoding="utf-8")
        (h2 / "java/com/example/T.java").write_text("package com.example;\nclass T {\n @GameTest(template = \"empty_test\")\n"
                                                    " public static void a(GameTestHelper h) { }\n}\n", encoding="utf-8")
        try:
            harness_registration({"repo": r, "args": types.SimpleNamespace(modid="m"), "target": t2}, h2, "com.example")
            cb = (h2 / "java/com/example/test/ClientBootSmokeTest.java").read_text(encoding="utf-8")
            chk("26.2 client harness: renamed and patched", "ArmorItem" not in cb and "new GameRules" not in cb
                and "mainRenderTarget()" in cb and "DifficultySettings" in cb and "popMatrix" in cb)
        except Fail as e:
            chk(f"26.2 client harness patch: {e}", False)
    return ok


if __name__ == "__main__":
    sys.exit(main())
