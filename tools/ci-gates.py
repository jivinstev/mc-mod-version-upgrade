#!/usr/bin/env python3
"""Run a finished port's gates in CI -- no model, no workers, a plain pass/fail with the evidence.

    python3 tools/ci-gates.py --repo . --modid <modid> --base <author's ref> [--gatec launch,spawn]
                              [--summary SUMMARY.md] [--work-dir DIR]

The fork's own CI calls this (templates/upstream-harness/port-ci.yml), so anyone can see on the branch that
the port PROVABLY loads and runs -- not only that it compiles. Measured: none of the four mods ported this
way shipped a test suite of its own, so "the author's tests pass" proves only that the JAR builds.

Same gates the port was held to, with the same code (tools/port-upstream.py's harness and Gate A,
tools/gate-loop.py --no-workers for Gate B and Gate C), so CI cannot judge a port more leniently than the
pipeline did:
  - the author's own `build` (and every Jar task they registered), with none of the harness;
  - Gate A: the mixin-config integrity test (every config entry compiled; every port-added mixin listed);
  - Gate B: a headless dedicated server loads the mod and runs the baseline GameTests;
  - Gate C: a real client under Xvfb + Mesa (software GL): reach the title screen, then spawn every entity.
The harness is generated OUTSIDE the author's tree (--work-dir, default a temp dir) and wired in with
--init-script, exactly as during the port. The summary (markdown) lists what ran and what passed, and is
what the release notes quote. Standard library only.
"""
import argparse, importlib.util, json, os, pathlib, re, subprocess, sys, tempfile, types

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / file)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def green_detail(text):
    """What a green gate log proves, in one line: the GameTest count, or the client's PASS line and spawns."""
    m = re.search(r"All (\d+) required tests passed", text) or re.search(r"BOOT_TEST: (PASS[^\n]*)", text)
    spawned = re.search(r"BOOT_TEST: spawned (\d+) creature type", text)
    head = (f"{m.group(1)} required test(s) passed" if m and m.group(1).isdigit() else
            (m.group(1)[:160] if m else "green"))
    return head + (f"; {spawned.group(1)} entity types spawned" if spawned else "")


def self_check():
    ok = green_detail("[x] All 4 required tests passed :)") == "4 required test(s) passed"
    ok &= green_detail("M_BOOT_TEST: spawned 40 creature type(s): [a]\nM_BOOT_TEST: PASS — ticked 200").startswith(
        "PASS — ticked 200; 40 entity types spawned")
    ok &= green_detail("") == "green"
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    if "--self-check" in sys.argv:
        return self_check()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default="."); ap.add_argument("--modid", required=True)
    ap.add_argument("--base", help="the author's ref (mixins they left unlisted stay exempt from Gate A)")
    ap.add_argument("--gatec", default="launch,spawn", help="Gate C phases; '' skips Gate C")
    ap.add_argument("--summary", default="ci-gates-summary.md"); ap.add_argument("--work-dir")
    ap.add_argument("--skip-author-build", action="store_true")
    a = ap.parse_args()
    repo = pathlib.Path(a.repo).resolve()
    work = pathlib.Path(a.work_dir or tempfile.mkdtemp(prefix="port-ci-")).resolve()
    work.mkdir(parents=True, exist_ok=True)
    pu, ss = _load("pu", "port-upstream.py"), _load("srcsets", "srcsets.py")
    c = {"repo": repo, "dir": work, "args": types.SimpleNamespace(modid=a.modid, base=a.base)}
    rows, ok = [], True

    def record(name, passed, detail):
        nonlocal ok
        ok &= passed
        rows.append(f"| {name} | {'PASS' if passed else 'FAIL'} | {detail} |")
        print(f"[ci-gates] {name}: {'PASS' if passed else 'FAIL'} -- {detail}", flush=True)

    if not a.skip_author_build:
        jars = ss.author_jar_tasks(repo)
        r = pu.gradle(repo, ["build", *jars], work / "author-build.log")
        built = sorted(p.name for p in (repo / "build/libs").glob("*.jar")) if (repo / "build/libs").is_dir() else []
        record("author's own build (`build" + "".join(f" {j}" for j in jars) + "`)", r.returncode == 0 and bool(built),
               ", ".join(built) or "no jar produced")

    if ok:
        try:
            n = pu.gate_a(c, work / "gateA.log")
            record("Gate A -- mixin-config integrity", True, f"{n} test(s)")
        except pu.Fail as e:
            record("Gate A -- mixin-config integrity", False, str(e)[:300])

    env = dict(os.environ, **pu.gate_env(c))
    for label, extra in [("Gate B -- headless server loads the mod, GameTests", [])] + \
            [(f"Gate C -- real client, `{p}`", ["--gatec", p]) for p in filter(None, a.gatec.split(","))]:
        if not ok:
            rows.append(f"| {label} | not run | an earlier gate failed |")
            continue
        log = work / ("gate-" + (extra[1] if extra else "b") + ".jsonl")
        r = subprocess.run([sys.executable, str(ROOT / "tools/gate-loop.py"), "--work", str(repo), "--namespace",
                            a.modid, "--no-workers", "--log", str(log), *extra], env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=3 * 3600)
        detail = "green"
        if r.returncode:
            m = re.search(r"GATE \S+ FAILED:\n(.*)", r.stdout, re.S)
            detail = (m.group(1) if m else (r.stdout + r.stderr)[-600:]).strip().splitlines()[0][:300]
            print(r.stdout[-6000:], flush=True)
        else:
            gl = pathlib.Path(env["PORT_LOG_DIR"]) / f"gate-loop{'-' + extra[1] if extra else ''}.log"
            detail = green_detail(gl.read_text(encoding="utf-8", errors="replace") if gl.exists() else "")
        record(label, r.returncode == 0, detail)

    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()
    md = [f"Gates for `{a.modid}` at `{head}` -- run by CI with no model and no fixes applied:", "",
          "| check | result | detail |", "|---|---|---|", *rows, "",
          "Not covered by any automated gate: gameplay by a person."]
    pathlib.Path(a.summary).write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
