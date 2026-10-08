#!/usr/bin/env python3
"""Port a mod IN ITS OWN REPOSITORY (an upstream fork), stage by stage, and say what each stage cost.

    python3 tools/port-upstream.py --repo <clone> --modid <id> [--branch neoforge-1.21.1] [--base <ref>]
                                   [--permission <url>] [--from <stage>] [--only <stage>] [--budget 20]
    python3 tools/port-upstream.py --repo <clone> --modid <id> --record <stage>=<usd>   # a stage run by hand
    python3 tools/port-upstream.py --repo <clone> --modid <id> --report                 # the cost table

The source-first counterpart of tools/port.py (which starts from a jar). The deliverable is a branch in
the author's own layout with the smallest diff that ports it, so every stage edits the repo in place and
the test harnesses stay OUTSIDE it (templates/upstream-harness/gates.init.gradle wires them in per run).

Stages, in order (each records its dollars and seconds in the state file; --from resumes):
  license     FIRST, before anything is spent: the repo's LICENSE file and mods.toml `license` must agree;
              all-rights-reserved or unknown stops here unless --permission names the author's written
              permission. Bundled jars in lib/ are listed with whatever licence they declare.
  designer    a read-only model pass that reads the repo and writes DESIGN.md (build plan, metadata,
              risks ranked with the gate that catches each, scope, order).
  branch      cut --branch from --base.
  build       a model pass that converts the build to ModDevGradle following DESIGN.md, checked by
              compileJava actually reaching javac (tools/burndown-count.sh), not by "BUILD SUCCESSFUL".
  metadata    mods.toml -> neoforge.mods.toml IN PLACE (the author's file, not a template), mixin configs
              (refmap, compatibility level), pack_format.
  mechanical  SRG strings, the recipe pack, the access transformer (remap + override widening),
              forge-shapes, convert-simplechannel, then compile + fix-holders until it converges.
  burndown    tools/file-loop.py on what is left.
  gates       Gate A (mixin-config integrity, asserting tests actually ran) and Gate B (tools/gate-loop.py,
              the GameTest server, fixing what fails) with the external harness.
  normalise   tools/normalise-imports.py: inline names the port wrote become imports.
  reviewer    a model pass over the diff against --base for completeness, the author's style and least
              diff; it fixes what is mechanical and lists what needs a person. Compile + Gate A re-run.
  provenance  the diff must not touch LICENSE*, drop an author copyright line or add a foreign one.
  report      the cost table, also written as the body of the final commit.

Stops at the first stage that fails, with the reason; nothing is pushed. Standard library only; the
model stages need the `claude` CLI.
"""
import argparse, json, os, pathlib, re, subprocess, sys, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
STAGES = ["license", "designer", "branch", "build", "metadata", "mechanical", "burndown", "gates", "normalise",
          "reviewer", "provenance", "report"]
SONNET = "claude-sonnet-5-5"
PERMISSIVE = {"MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "ISC", "Zlib", "Unlicense", "CC0-1.0", "MPL-2.0",
              "LGPL-2.1", "LGPL-3.0", "GPL-3.0", "GPL-2.0"}   # all permit a public fork; copyleft is noted, not blocked
LICENCE_SIGNS = [("MIT", r"\bMIT License\b|Permission is hereby granted, free of charge"),
                 ("Apache-2.0", r"Apache License,?\s+Version 2\.0"), ("GPL-3.0", r"GNU GENERAL PUBLIC LICENSE\s+Version 3"),
                 ("LGPL-3.0", r"GNU LESSER GENERAL PUBLIC LICENSE\s+Version 3"), ("LGPL-2.1", r"Lesser General Public License.*2\.1"),
                 ("MPL-2.0", r"Mozilla Public License,?\s+(?:Version|v\.)\s*2\.0"), ("BSD-3-Clause", r"Redistributions of source code.*Neither the name"),
                 ("CC0-1.0", r"CC0 1\.0"), ("Unlicense", r"This is free and unencumbered software")]


class Fail(Exception):
    pass


def sh(cmd, cwd=None, env=None, timeout=None, log=None):
    r = subprocess.run(cmd, cwd=cwd, env=env, timeout=timeout, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", shell=isinstance(cmd, str))
    if log:
        pathlib.Path(log).write_text(r.stdout + r.stderr, encoding="utf-8")
    return r


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


def gradle(repo, args, log, extra_init=(), env=None, timeout=2400):
    cmd = ["bash", "gradlew", "--console=plain", "-I", str(ROOT / "tools/central-mirror.init.gradle")]
    for i in extra_init:
        cmd += ["-I", str(i)]
    return sh(cmd + list(args) + ["-Dorg.gradle.jvmargs=-Xmx6g"], cwd=repo, env=env, timeout=timeout, log=log)


def compile_count(repo, log):
    gradle(repo, ["-I", str(ROOT / "tools/maxerrs.init.gradle"), "compileJava"], log)
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


def st_designer(c):
    prompt = (ROOT / "templates/upstream-harness/designer-prompt.md").read_text(encoding="utf-8")
    prompt = prompt.replace("{BRANCH}", c["args"].branch)
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


def st_build(c):
    repo = c["repo"]
    design = (c["dir"] / "DESIGN.md").read_text(encoding="utf-8") if (c["dir"] / "DESIGN.md").exists() else ""
    prompt = ("Convert this mod's Gradle build to NeoForge 1.21.1 with ModDevGradle (id 'net.neoforged.moddev' version "
              "'2.0.146'), following the BUILD section of the design below. Edit only build.gradle, settings.gradle, "
              "gradle.properties and gradle/wrapper/*. Keep the author's structure: the same source sets, task names, "
              "comments and order; change only what the new toolchain requires (drop reobf/refmap/ForgeGradle-only "
              "pieces, map jarJar). Do not touch Java sources.\n\n" + design)
    usd = 0.0
    for attempt in range(2):
        _t, u = claude(prompt, repo, "Read,Edit,Write,Grep,Glob")
        usd += u
        n, line = compile_count(repo, c["dir"] / "build-check.log")
        if n is not None:
            return {"usd": usd, "first_count": n}
        prompt = f"The build you wrote does not reach javac. Gradle says:\n{line}\nFix the build files only."
    raise Fail(f"build still does not reach javac: {line}")


def st_metadata(c):
    repo, modid, done = c["repo"], c["args"].modid, []
    for toml in repo.glob("src/*/resources/META-INF/mods.toml"):
        t = toml.read_text(encoding="utf-8")
        t = re.sub(r'(?m)^loaderVersion\s*=\s*"[^"]*"', 'loaderVersion = "[4,)"', t)
        t = re.sub(r'(modId\s*=\s*)"forge"', r'\1"neoforge"', t)
        t = re.sub(r'(?m)^(\s*)mandatory\s*=\s*true', r'\1type = "required"', t)
        t = re.sub(r'(?m)^(\s*)mandatory\s*=\s*false', r'\1type = "optional"', t)
        t = re.sub(r'(modId\s*=\s*"neoforge"[^\[]*?versionRange\s*=\s*)"[^"]*"', r'\1"[21.1,)"', t, flags=re.S)
        t = re.sub(r'(modId\s*=\s*"minecraft"[^\[]*?versionRange\s*=\s*)"[^"]*"', r'\1"[1.21.1,1.22)"', t, flags=re.S)
        cfg = f"{modid}.mixins.json"
        if (repo / "src/main/resources" / cfg).exists() and "[[mixins]]" not in t:
            t = t.rstrip("\n") + f'\n\n[[mixins]]\nconfig = "{cfg}"\n'
        new = toml.with_name("neoforge.mods.toml")
        sh(["git", "mv", str(toml), str(new)], cwd=repo)
        new.write_text(t, encoding="utf-8"); done.append(str(new.relative_to(repo)))
    for mj in repo.glob("src/*/resources/*.mixins*.json"):
        t = mj.read_text(encoding="utf-8")
        t2 = re.sub(r'\n\s*"refmap"\s*:\s*"[^"]*",?', "", t).replace('"JAVA_17"', '"JAVA_21"').replace('"JAVA_8"', '"JAVA_21"')
        if t2 != t:
            mj.write_text(t2, encoding="utf-8"); done.append(str(mj.relative_to(repo)))
    for pm in repo.glob("src/*/resources/pack.mcmeta"):
        t = pm.read_text(encoding="utf-8")
        t2 = re.sub(r'("pack_format"\s*:\s*)\d+', r"\g<1>34", t)
        if t2 != t:
            pm.write_text(t2, encoding="utf-8"); done.append(str(pm.relative_to(repo)))
    tool("fix-datapack-layout.py", repo, "--apply")
    return {"files": done}


def st_mechanical(c):
    repo, a, src, rep = c["repo"], c["args"], c["repo"] / "src/main/java", {}
    srg = pathlib.Path(os.path.expanduser("~/.mc-mod-upgrade/work/srg2official-1.20.1.json"))
    if srg.exists():
        for d in repo.glob("src/*/java"):
            rep[f"srg:{d.parent.name}"] = tool("srg-remap/apply_mapping.py", srg, d).splitlines()[0:1]
    pack = ROOT / "tools/recipes/forge-1.20-to-neoforge-1.21.1.recipes.tsv"
    rep["pack"] = [l for l in tool("apply-recipes.py", "--src", src, "--recipes", pack).splitlines() if l.startswith("auto")]
    rep["at"] = tool("fix-access-transformer.py", "--work", repo, *(["--srg-map", srg] if srg.exists() else []))
    for k in range(4):                         # override widenings appear one subclass level per compile
        n, _ = compile_count(repo, c["dir"] / f"at-{k}.log")
        if n is not None:
            break
        out = tool("fix-access-transformer.py", "--work", repo, "--overrides-from", c["dir"] / f"at-{k}.log")
        if " 0 override" in out:
            raise Fail("Minecraft's recompile fails and no access-transformer override explains it (see at-*.log)")
    rep["shapes"] = tool("forge-shapes.py", "--src", src, "--modid", a.modid, "--sites", 0)
    rep["net"] = tool("convert-simplechannel.py", "--src", src, "--modid", a.modid)
    counts = []
    for r in range(4):
        n, line = compile_count(repo, c["dir"] / f"mech-{r}.log")
        if n is None:
            raise Fail(f"compile did not run: {line}")
        counts.append(n)
        out = tool("fix-holders.py", "--src", src, "--log", c["dir"] / f"mech-{r}.log", "--sites", 0)
        if " 0 site" in out.splitlines()[0]:
            break
    rep["errors"] = counts
    return rep


def st_burndown(c):
    log = c["dir"] / "file-loop.jsonl"
    r = sh([sys.executable, str(ROOT / "tools/file-loop.py"), "--work", str(c["repo"]), "--budget",
            str(c["args"].budget), "--log", str(log)], env=dict(os.environ, PORT_LOG_DIR=str(c["dir"])),
           timeout=4 * 3600)
    end = next((json.loads(l) for l in reversed(r.stdout.splitlines()) if '"event": "end"' in l), None)
    if not end:
        raise Fail(f"file-loop ended without a result: {r.stdout[-600:]}")
    if end["errors"]:
        raise Fail(f"file-loop stopped at {end['errors']} errors (${end['spent']:.2f}); see {log}")
    return {"usd": end["spent"], "stubs": end.get("stubs"), "by_model": end.get("by_model")}


def harness(c):
    """The external harness: a baseline GameTest, its structure, the mixin integrity test."""
    h, modid = c["dir"] / "harness", c["args"].modid
    if h.exists():
        return h
    pkg = (re.search(r"(?m)^package\s+([\w.]+)\s*;", next((c["repo"] / "src/main/java").rglob("*.java")).read_text(
        encoding="utf-8")) or [None, modid])[1].split(".")
    pkg = ".".join(pkg[:2]) if len(pkg) > 1 else pkg[0]
    gt = (ROOT / "templates/neoforge-mod/test-templates/BaselineGameTest.java.example").read_text(encoding="utf-8")
    gt = re.sub(r"(?m)^package\s+[\w.]+;", f"package {pkg}.gatetest;", gt).replace('"examplemod"', f'"{modid}"')
    (h / "java" / pkg.replace(".", "/") / "gatetest").mkdir(parents=True)
    (h / "java" / pkg.replace(".", "/") / "gatetest/BaselineGameTest.java").write_text(gt, encoding="utf-8")
    sd = h / "resources/data" / modid / "structure"; sd.mkdir(parents=True)
    import gzip, importlib.util
    spec = importlib.util.spec_from_file_location("ges", ROOT / "tools/gen-empty-structure.py")
    ges = importlib.util.module_from_spec(spec); spec.loader.exec_module(ges)
    (sd / "empty_test.nbt").write_bytes(gzip.compress(ges.build(modid, "empty_test", 9, 3955)))
    sg = importlib.util.spec_from_file_location("sgc", ROOT / "tools/scaffold-gatec.py")
    sgc = importlib.util.module_from_spec(sg); sg.loader.exec_module(sgc)
    (h / "java" / pkg.replace(".", "/") / "test").mkdir(parents=True, exist_ok=True)
    (h / "java" / pkg.replace(".", "/") / "test/ClientBootSmokeTest.java").write_text(
        sgc.render(sgc.TEMPLATE.read_text(encoding="utf-8"), modid, pkg), encoding="utf-8")
    cfg = f"{modid}.mixins.json"
    if (c["repo"] / "src/main/resources" / cfg).exists():
        t = (ROOT / "templates/neoforge-mod/test-templates/MixinConfigIntegrityTest.java.template").read_text(encoding="utf-8")
        t = t.replace("PACKAGE_PLACEHOLDER", pkg).replace("MODID.mixins.json", cfg)
        (h / "test" / pkg.replace(".", "/")).mkdir(parents=True)
        (h / "test" / pkg.replace(".", "/") / "MixinConfigIntegrityTest.java").write_text(t, encoding="utf-8")
    return h


def gate_env(c):
    env = dict(os.environ, PORT_HARNESS_DIR=str(harness(c)), PORT_MODID=c["args"].modid, PORT_LOG_DIR=str(c["dir"]),
               PORT_GRADLE_INIT=str(ROOT / "templates/upstream-harness/gates.init.gradle"))
    return env


def gate_a(c, log):
    env = gate_env(c)
    r = gradle(c["repo"], ["test"], log, extra_init=[env["PORT_GRADLE_INIT"]], env=env)
    res = list((c["repo"] / "build/test-results/test").glob("*.xml"))
    ran = sum(int(m) for f in res for m in re.findall(r'tests="(\d+)"', f.read_text(encoding="utf-8")))
    bad = sum(int(m) for f in res for m in re.findall(r'(?:failures|errors)="(\d+)"', f.read_text(encoding="utf-8")))
    if r.returncode or bad:
        raise Fail(f"Gate A failed ({bad} failure(s)); see {log}")
    if not ran:
        raise Fail("Gate A ran no tests -- that is not a pass")
    return ran


def st_gates(c):
    ran = gate_a(c, c["dir"] / "gateA.log")
    env = gate_env(c)
    log = c["dir"] / "gate-loop.jsonl"
    r = sh([sys.executable, str(ROOT / "tools/gate-loop.py"), "--work", str(c["repo"]), "--namespace", c["args"].modid,
            "--budget", str(min(c["args"].budget, 8)), "--log", str(log)], env=env, timeout=4 * 3600)
    end = next((json.loads(l) for l in reversed(r.stdout.splitlines())
                if re.search(r'"event": "(green|stuck|budget|max-runs)"', l)), None)
    if not end or end["event"] != "green":
        raise Fail(f"Gate B not green ({end and end['event']}): {(r.stdout or r.stderr)[-800:]}")
    return {"gateA_tests": ran, "usd": end.get("spent", 0), "gateB_runs": end.get("run")}


def st_normalise(c):
    return {"out": tool("normalise-imports.py", "--src", c["repo"] / "src/main/java", "--base", c["args"].base)}


def st_reviewer(c):
    repo, base = c["repo"], c["args"].base
    stat = sh(["git", "diff", "--stat", "-M", base, "--", "."], cwd=repo).stdout
    prompt = (ROOT / "templates/upstream-harness/reviewer-prompt.md").read_text(encoding="utf-8")
    prompt = prompt.replace("{BASE}", base).replace("{STAT}", stat[-6000:])
    text, usd = claude(prompt, repo, "Read,Edit,Grep,Glob,Bash(git diff:*),Bash(git show:*),Bash(git log:*)")
    (c["dir"] / "REVIEW.md").write_text(text, encoding="utf-8")
    n, line = compile_count(repo, c["dir"] / "review-compile.log")
    if n:
        raise Fail(f"the reviewer's edits broke the compile ({n} errors); see REVIEW.md and review-compile.log")
    gate_a(c, c["dir"] / "gateA-after-review.log")
    return {"usd": usd, "review": str(c["dir"] / "REVIEW.md")}


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
        usd = e.get("usd") or e.get("result", {}).get("usd") or 0.0
        total += usd
        rows.append(f"| {s} | ${usd:.2f} | {e.get('secs', 0) // 60} min |")
    lic = c["state"].get("license", {}).get("result", {})
    md = ["| stage | model cost | wall time |", "|---|---|---|", *rows, f"| **total** | **${total:.2f}** | |", "",
          f"Licence: {lic.get('licence')} ({', '.join(lic.get('file') or [])}); notices retained."
          + (f" Author's permission: {lic['permission']}" if lic.get("permission") else "")]
    (c["dir"] / "COST.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return {"total_usd": round(total, 2), "table": "\n".join(md)}


FUNCS = {s: globals()["st_" + s] for s in STAGES}


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
    a = ap.parse_args()
    repo = pathlib.Path(a.repo).resolve()
    if a.base is None:
        a.base = sh(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=repo).stdout.strip() or "master"
    d = pathlib.Path(os.path.expanduser(f"~/.mc-mod-upgrade/upstream/{repo.name}-{a.branch}"))
    d.mkdir(parents=True, exist_ok=True)
    sf = d / "state.json"
    state = json.loads(sf.read_text(encoding="utf-8")) if sf.exists() else {}
    c = {"repo": repo, "args": a, "dir": d, "state": state}
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
            state[s] = {"status": "failed", "why": str(e), "secs": int(time.time() - t0)}; save()
            print(f"[port-upstream] {s} FAILED: {e}\n  state: {sf}", flush=True)
            return 1
        state[s] = {"status": "done", "secs": int(time.time() - t0), "usd": (res or {}).get("usd", 0.0), "result": res}
        save()
        print(f"[port-upstream] {s} done ({state[s]['secs']}s, ${state[s]['usd']:.2f})", flush=True)
    if "report" in todo:
        print(state["report"]["result"]["table"])
    return 0


def self_check():
    """The two stages that must never pass wrongly: licence (first) and provenance (last)."""
    import tempfile, types
    ok = True
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
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
