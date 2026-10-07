#!/usr/bin/env python3
"""Look at a port's Gate C photographs: script checks first, then ONE cheap model request.

    python3 tools/visual-review.py --work <gradle project> [--model haiku] [--out visual-review.json]

The Gate C harness (templates/neoforge-mod/test-templates/ClientBootSmokeTest.java.example) saves frames
as <run dir>/screenshots/gatec-<phase>-<what>.png. Gate C itself only proves nothing CRASHED; a frame can
still show the missing-texture cube, an invisible mob, a black world or a GUI drawn wrong.

1. Script, no model: each frame's brightness, flatness and share of missing-texture magenta, with the
   thresholds tools/compare-gatec-shots.py uses. dark/flat/magenta frames are findings outright.
2. Model: one request (Haiku by default, no thinking) with Read on the frames only. It gets what the
   mod claims to contain (its display names from the language file, its description) and what the
   harness logged for each step, and answers one JSON line per frame: ok | suspect, and why.

A suspect frame is a FINDING for a person or a fix worker, never an automatic failure: the model can be
wrong, and a modded scene can look odd on purpose. Exits 0 when nothing is suspect, 1 when something is,
2 when there are no frames to look at (a review of nothing is not a pass). Standard library only; needs
the `claude` CLI.
"""
import argparse, importlib.util, json, os, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
_s = importlib.util.spec_from_file_location("shots", ROOT / "tools/compare-gatec-shots.py")
shots = importlib.util.module_from_spec(_s); _s.loader.exec_module(shots)
MODELS = {"haiku": "claude-haiku-4-5-20251001", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}

SYSTEM = ("You review screenshots from an automated Minecraft client test of a ported mod. You read the image "
          "files you are given and answer only in the format asked. You do not edit anything.")
PROMPT = """These frames come from an automated client test of the Minecraft mod `{modid}` after porting it to
{target}. The test spawns the mod's creatures, makes them fight, draws every item, wears every armour piece,
places every block, uses every item, applies every effect and opens the creative menu. It already knows
nothing CRASHED. Your job is to spot frames where the port is visibly WRONG.

What the mod says it contains (display names from its language file):
{names}
Description: {description}

What the test logged around each frame:
{log}

Script measurements per frame (mean brightness 0-255, brightness spread, share of missing-texture magenta):
{stats}

Read each file with the Read tool:
{files}

Flag a frame as suspect only for something a player would call broken: the purple-and-black missing-texture
pattern on the mod's items, blocks, armour or mobs; a mob or block that should be visible but is not there or
is drawn as a plain box; garbled or untextured GUI; a screen that is black or empty where the log says
something was drawn; text that shows a raw translation key like item.{modid}.x. Ordinary terrain, vanilla
UI, the sky, an odd camera angle or a mob facing away are fine.

Answer with exactly one JSON object per line, one per frame, and nothing else:
{{"frame": "<file name>", "verdict": "ok" | "suspect", "why": "<one sentence, what you see>"}}"""


def run_dirs(work):
    """Every screenshots dir under the project's run directories (per target, per run name)."""
    return sorted({p.parent for p in pathlib.Path(work).glob("run*/**/screenshots/gatec-*.png")}
                  | {p.parent for p in pathlib.Path(work).glob("run*/screenshots/gatec-*.png")})


def frames(work):
    out = {}
    for d in run_dirs(work):
        for p in sorted(d.glob("gatec-*.png")):
            if p.name not in out or p.stat().st_mtime > out[p.name].stat().st_mtime:
                out[p.name] = p
    return [out[k] for k in sorted(out)]


def measure(path):
    """-> (stats dict, script finding or None)."""
    try:
        f = shots.Frame(str(path))
    except Exception as e:   # noqa: BLE001 -- an unreadable frame is itself a finding
        return {"error": str(e)}, f"unreadable: {e}"
    st = {"mean": round(f.mean, 1), "spread": round(f.stddev, 1), "magenta": round(f.magenta, 4)}
    if f.mean < shots.DARK_MEAN:
        return st, "dark: the frame is essentially black"
    if f.stddev < shots.FLAT_STDDEV:
        return st, "flat: the frame is one colour, nothing was drawn"
    if f.magenta > MAGENTA_FINDING:
        return st, f"magenta: {f.magenta:.1%} of the frame is the missing-texture colour"
    return st, None


MAGENTA_FINDING = 0.003   # vanilla scenes measure ~0; one missing-texture item on screen is already ~0.1%


def names(work, modid, cap=60):
    out = []
    for p in pathlib.Path(work).glob(f"src/main/resources/assets/{modid}/lang/en_us.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        for k, v in d.items():
            if re.match(rf'^(item|block|entity|effect)\.{re.escape(modid)}\.[a-z0-9_/]+$', k) and isinstance(v, str):
                out.append(f"{k.split('.')[0]}: {v}")
    return "\n".join(out[:cap]) + (f"\n... and {len(out) - cap} more" if len(out) > cap else "") if out else "(none found)"


def description(work):
    for p in pathlib.Path(work).glob("src/main/resources/META-INF/neoforge.mods.toml"):
        m = re.search(r"description\s*=\s*'''(.*?)'''|description\s*=\s*\"(.*?)\"", p.read_text(encoding="utf-8"), re.S)
        if m:
            return (m.group(1) or m.group(2) or "").strip()[:600]
    return "(none)"


def log_lines(work, modid):
    """The harness's own lines from the Gate C logs, so the reviewer knows what each frame should show."""
    tag = modid.upper() + "_BOOT_TEST"
    out = []
    for p in sorted(pathlib.Path(work).glob("gate-loop-*.log")):
        out += [l.strip()[len(tag) + 2:][:160] for l in p.read_text(encoding="utf-8", errors="replace").splitlines()
                if l.strip().startswith(tag) and ("[GAUNTLET]" in l or "[SHOT]" in l or "PASS" in l or "spawn" in l.lower())]
    return "\n".join(out[-80:]) or "(no harness log found)"


def parse(answer):
    out = {}
    for l in answer.splitlines():
        l = l.strip().strip("`")
        if l.startswith("{"):
            try:
                d = json.loads(l)
            except ValueError:
                continue
            if "frame" in d and d.get("verdict") in ("ok", "suspect"):
                out[pathlib.Path(d["frame"]).name] = d
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work"); ap.add_argument("--model", default="haiku", choices=MODELS)
    ap.add_argument("--target", default="NeoForge 1.21.1"); ap.add_argument("--out")
    ap.add_argument("--no-model", action="store_true", help="script checks only")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not a.work:
        ap.error("--work is required")
    work = pathlib.Path(a.work).resolve()
    modid = next((m.group(1) for m in re.finditer(r'^mod_id\s*=\s*(\S+)', (work / "gradle.properties")
                  .read_text(encoding="utf-8"), re.M)), work.name)
    fr = frames(work)
    report = {"modid": modid, "frames": len(fr), "findings": [], "usd": 0.0}
    if not fr:
        print("visual-review: NO FRAMES under run*/screenshots -- nothing was reviewed, this is not a pass")
        (pathlib.Path(a.out) if a.out else work / "visual-review.json").write_text(json.dumps(report, indent=1))
        return 2
    stats = {}
    for p in fr:
        st, why = measure(p)
        stats[p.name] = st
        if why:
            report["findings"].append({"frame": p.name, "by": "script", "why": why})
    if not a.no_model:
        prompt = PROMPT.format(modid=modid, target=a.target, names=names(work, modid), description=description(work),
                               log=log_lines(work, modid), stats="\n".join(f"{k}: {v}" for k, v in stats.items()),
                               files="\n".join(str(p) for p in fr))
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD"}
        env["MAX_THINKING_TOKENS"] = "0"
        r = subprocess.run(["claude", "-p", "--model", MODELS[a.model], "--system-prompt", SYSTEM,
                            "--tools", "Read", "--allowedTools", "Read", "--output-format", "json",
                            *[x for d in {p.parent for p in fr} for x in ("--add-dir", str(d))]],
                           input=prompt, cwd=work, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=900)
        try:
            d = json.loads(r.stdout)
        except ValueError:
            d = {"result": (r.stdout + r.stderr)[-400:], "total_cost_usd": 0}
        report["usd"] = d.get("total_cost_usd") or 0
        report["usage"] = d.get("usage")
        verdicts = parse(d.get("result") or "")
        report["reviewed"] = len(verdicts)
        for p in fr:
            v = verdicts.get(p.name)
            if v is None:
                report["findings"].append({"frame": p.name, "by": "model", "why": "the reviewer gave no verdict for it"})
            elif v["verdict"] == "suspect":
                report["findings"].append({"frame": p.name, "by": "model", "why": v.get("why", "")})
        report["verdicts"] = verdicts
    out = pathlib.Path(a.out) if a.out else work / "visual-review.json"
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"visual-review: {len(fr)} frame(s), {len(report['findings'])} finding(s), ${report['usd']:.4f}"
          + "".join(f"\n  {f['frame']} [{f['by']}]: {f['why']}" for f in report["findings"]))
    return 1 if report["findings"] else 0


def self_check():
    import tempfile, zlib, struct

    def png(path, rgb):
        w = h = 8
        rows = b"".join(b"\x00" + b"".join(bytes(rgb(x, y)) for x in range(w)) for y in range(h))
        chunk = lambda k, b: struct.pack(">I", len(b)) + k + b + struct.pack(">I", zlib.crc32(k + b) & 0xffffffff)
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                         + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        png(d / "black.png", lambda x, y: (0, 0, 0))
        png(d / "scene.png", lambda x, y: ((x * 30) % 256, (y * 30) % 256, 90))
        png(d / "purple.png", lambda x, y: (250, 0, 250) if (x + y) % 2 else (0, 0, 0))
        ok = measure(d / "black.png")[1].startswith("dark") and measure(d / "scene.png")[1] is None
        ok &= measure(d / "purple.png")[1].startswith("magenta")
        ans = ('{"frame": "a.png", "verdict": "ok", "why": "fine"}\nnoise\n'
               '```\n{"frame": "/x/b.png", "verdict": "suspect", "why": "purple cube"}\n```')
        v = parse(ans)
        ok &= v["a.png"]["verdict"] == "ok" and v["b.png"]["verdict"] == "suspect"
        w = d / "work"; (w / "run/client/screenshots").mkdir(parents=True)
        png(w / "run/client/screenshots/gatec-spawn-world.png", lambda x, y: (x * 9, y * 9, 40))
        ok &= [p.name for p in frames(w)] == ["gatec-spawn-world.png"]
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
