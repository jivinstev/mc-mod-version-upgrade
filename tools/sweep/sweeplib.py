"""Shared helpers for the zero-model sweep tools: find each mod's record and its final compile log, in either layout
(a live run, <run>/results/<mod>/, or an archived one, <archive>/runs/<run>/<mod>/ with final-*.log.gz)."""
import gzip, json, os, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the migrator checkout


def mod_dirs(*runs):
    """{mod slug: dir}, a later run overriding an earlier one."""
    out = {}
    for r in runs:
        r = pathlib.Path(r)
        base = r / "results" if (r / "results").is_dir() else r
        for s in sorted(base.glob("*/summary.json")):
            out[s.parent.name] = s.parent
    return out


def summary(d):
    return json.loads((pathlib.Path(d) / "summary.json").read_text(encoding="utf-8"))


def final_log(d):
    """Text of the last compile log of the mechanical stage that has errors, or None."""
    d = pathlib.Path(d)
    gz = sorted(d.glob("final-*.log.gz"))
    if gz:
        return gzip.open(gz[-1], "rt", encoding="utf-8", errors="replace").read()
    logs = sorted((d / "mechanical-hop").glob("*.log"), key=os.path.getmtime) if (d / "mechanical-hop").is_dir() else []
    texts = [l.read_text(encoding="utf-8", errors="replace") for l in logs]
    texts = [t for t in texts if "error:" in t]
    return texts[-1] if texts else None
