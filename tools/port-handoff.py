#!/usr/bin/env python3
"""Write a port's phase hand-off into its MIGRATION.md, and print the prompt that resumes it.

    python3 tools/port-handoff.py <mod-dir> --done compile --next "Gate A: ./gradlew test" [--log /tmp/build.log]

Why: in the measured ports (docs/EVALS.md, Stage 0) the conversation itself was 44-60% of what every
request re-read, and it only grows. Starting each phase -- setup, the build-error loop, the gates,
delivery -- in a FRESH context (a subagent, or a new session) caps that growth, provided the next
context gets everything it needs from a short, fixed-size hand-off rather than from the history.

This assembles that hand-off from the workspace itself, not from memory:
  * the phase just finished and the next concrete action (from you: --done / --next / --blocker);
  * the compile state (tools/compile-summary.py on --log, top groups and files only);
  * the port's local git history (last commits) and any uncommitted files;
  * whether catalog-additions.md has entries waiting for Step 7;
  * MIGRATION.md's own Scope section (the user's choice, which must survive the hand-off).
It replaces the previous `## Hand-off` section of MIGRATION.md (one section, always current) and
prints the resume prompt. Standard library only.
"""
import argparse, datetime, os, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PHASES = {   # what each finished phase hands to the next, by SKILL.md step
    "setup": ("Steps 0-3b (triage, scaffold, decompile, metadata, scope)", "Step 4: the build-error loop"),
    "compile": ("Step 4 + 4b (build-error loop to 0, retrospective)", "Step 5: Gate A, then Gate B"),
    "compile-partial": ("part of Step 4 (the error count is not yet 0)", "Step 4: continue the build-error loop"),
    "gates": ("Step 5 (Gates A, B and C)", "Steps 6-7: final sweep, record, deliver"),
    "deliver": ("Steps 6-7 (sweep, record, deliver)", "nothing -- the port is delivered"),
}
HEADING = "## Hand-off"


def run(cmd, cwd):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout


def section(md, title):
    m = re.search(rf'^##\s+{re.escape(title)}\b.*?$(.*?)(?=^##\s|\Z)', md, re.M | re.S)
    return m.group(1).strip() if m else ""


def compile_state(log, lines=14):
    if not log:
        return None
    code, out = run([sys.executable, str(ROOT / "tools/compile-summary.py"), str(log), "--no-state",
                     "--top", "5", "--files", "5"], ROOT)
    keep = out.strip().splitlines()
    return keep[:lines] + ([f"... ({len(keep) - lines} more lines: run tools/compile-summary.py {log})"]
                           if len(keep) > lines else [])


def build(mod, done, nxt, blockers, log):
    mod = pathlib.Path(mod).resolve()
    md_path = mod / "MIGRATION.md"
    md = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
    did, default_next = PHASES[done]
    out = [HEADING, "",
           f"_Written by tools/port-handoff.py, {datetime.date.today().isoformat()}. "
           "This section is the whole context the next phase starts from; keep it current, keep it short._", "",
           f"- **Finished:** {did}",
           f"- **Next action:** {nxt or default_next}"]
    for b in blockers:
        out.append(f"- **Blocker:** {b}")
    scope = section(md, "Scope")
    out.append(f"- **Scope (the user's choice -- honour it):** "
               + (scope.splitlines()[0][:200] + (" (see ## Scope)" if len(scope.splitlines()) > 1 else "")
                  if scope else "not recorded -- full port"))
    cs = compile_state(log)
    if cs:
        out += ["", "Compile state (`tools/compile-summary.py " + str(log) + "`):", "```", *cs, "```"]
    if (mod / ".git").exists():
        _c, lg = run(["git", "log", "--oneline", "-8"], mod)
        _c, st = run(["git", "status", "--porcelain"], mod)
        dirty = [l for l in st.splitlines() if l.strip()]
        out += ["", "Local history (last 8):", "```", *lg.strip().splitlines(), "```"]
        if dirty:
            out.append(f"Uncommitted: {len(dirty)} path(s) -- commit before the next phase starts.")
    ws = os.environ.get("MIGRATE_WORKSPACE")
    adds = pathlib.Path(ws, "catalog-additions.md") if ws else mod.parent / "catalog-additions.md"
    if adds.exists():
        n = len(re.findall(r'^###\s', adds.read_text(encoding="utf-8"), re.M))
        out.append(f"catalog-additions.md: {n} entr{'y' if n == 1 else 'ies'} waiting for Step 7.")
    block = "\n".join(out) + "\n"
    if HEADING in md:
        md = re.sub(rf'^{re.escape(HEADING)}\b.*?(?=^##\s|\Z)', lambda _m: block + "\n", md, count=1, flags=re.M | re.S)
    else:
        md = md.rstrip() + ("\n\n" if md.strip() else "") + block
    return md, block, mod


def prompt(mod, done, nxt):
    return (f"Continue the port in {mod} with the migrate-mod skill. Read ONLY {mod}/MIGRATION.md "
            f"(its `{HEADING}` section is your starting state) and the CATALOG.md entries it names; do not "
            f"re-read the decompiled tree or old logs wholesale. Do: {nxt or PHASES[done][1]}. When that phase "
            f"ends, run tools/port-handoff.py again and stop with a 5-line report.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mod", nargs="?")
    ap.add_argument("--done", choices=sorted(PHASES))
    ap.add_argument("--next", help="the next concrete action (default: the next phase's first step)")
    ap.add_argument("--blocker", action="append", default=[])
    ap.add_argument("--log", help="the latest compile log (adds the compile state)")
    ap.add_argument("--print-only", action="store_true", help="print the hand-off, do not write MIGRATION.md")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not (a.mod and a.done):
        ap.error("<mod-dir> and --done are required")
    md, block, mod = build(a.mod, a.done, a.next, a.blocker, a.log)
    if a.print_only:
        print(block)
    else:
        (mod / "MIGRATION.md").write_text(md, encoding="utf-8")
        print(f"wrote {HEADING} to {mod / 'MIGRATION.md'} ({len(block.splitlines())} lines)")
    print("\nResume prompt (give it to a fresh subagent or session):\n" + prompt(mod, a.done, a.next))
    return 0


def self_check():
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        mod = pathlib.Path(t, "m")
        mod.mkdir()
        (mod / "MIGRATION.md").write_text("# m\n\n## Scope\nMinimal port: integration:JEI left out (4%).\n\n"
                                          "## Status\n12 errors\n", encoding="utf-8")
        md, block, _ = build(mod, "compile-partial", None, ["mixin X target gone"], None)
        (mod / "MIGRATION.md").write_text(md, encoding="utf-8")
        md2, _b, _ = build(mod, "compile", "Gate A", [], None)   # a second hand-off REPLACES the first
        ok = (md.count(HEADING) == 1 and md2.count(HEADING) == 1 and "## Status\n12 errors" in md2
              and "Minimal port: integration:JEI left out" in block and "**Blocker:** mixin X target gone" in block
              and "**Next action:** Gate A" in md2 and "continue the build-error loop" not in md2
              and len(block.splitlines()) < 40)
    print("self-check:", "OK" if ok else "FAIL\n" + md2)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
