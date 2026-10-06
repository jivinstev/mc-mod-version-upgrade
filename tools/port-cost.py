#!/usr/bin/env python3
"""Record what a port cost: model, effort, every token type, dollars, time, and the port's size.

    python3 tools/port-cost.py <modid>              # writes mods/<modid>/COST.json + MIGRATION.md "## Cost"
    python3 tools/port-cost.py <modid> --print      # show it, write nothing

tools/finish-port.py runs this for you. Numbers are read from Claude Code's own transcripts
(~/.claude/projects, or $CLAUDE_CONFIG_DIR/projects). Tokens are never estimated; dollars are
estimated from them only when Claude Code recorded none (see `dollars` below).

WHICH REQUESTS COUNT
    Every session whose tool calls touch `mods/<modid>`, WHOLE (a port session's triage and setup are
    part of the port), plus that session's subagents (<session>/subagents/*.jsonl). Each API request is
    counted ONCE: usage is repeated on every content block of a response, so counting lines doubles it.
    A session that also WORKED on other mods (edits, commands -- not just reading one for reference) is
    listed under `contamination`, so a mixed session is visible rather than silently inflating this port.

WHAT IS RECORDED (so costs can be normalised later)
    per model   requests, input, output (and the thinking part of it), cache reads, cache writes (5-minute
                and 1-hour), web searches/fetches
    effort      requests per effort level (low/medium/high/...)
    time        wall (first to last request) and active (gaps capped at 10 minutes) hours
    dollars     the session's own recorded total (`cost-state` in the transcript) when the Claude Code
                build writes one (usd_source=recorded). A build that writes none (the desktop/CLI build
                records tokens, not dollars) gets an ESTIMATE from the exact tokens and the dated list
                prices in tools/model-prices.tsv (usd_source=estimated). A model with no price row leaves
                the dollars null with the reason -- never a guess.
    size        target Minecraft + NeoForge, Java files, mixin classes, GeckoLib, from the port itself
    context     Claude Code version(s), entry point (cli / cloud / ...), session count
"""
import argparse, collections, datetime, json, os, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
GAP = 600
READ_ONLY_TOOLS = {"Read", "Grep", "Glob", "LS", "NotebookRead"}
PRICES = ROOT / "tools/model-prices.tsv"


def load_prices(path=PRICES):
    """model -> {column: float, 'as_of': str} from tools/model-prices.tsv ('#' lines are comments)."""
    rows, head = {}, None
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        cells = line.split("\t")
        if head is None:
            head = cells
            continue
        r = dict(zip(head, cells))
        rows[r["model"]] = {k: (v if k in ("model", "as_of") else float(v)) for k, v in r.items()}
    return rows


def estimate_usd(per_model, prices):
    """Dollars from exact token counts at list prices, or (None, reason) if any model is unpriced.
    Thinking is inside output. A cache write with no 5m/1h split is priced as 5-minute."""
    total, dates = 0.0, set()
    for model, c in per_model.items():
        p = prices.get(model) or prices.get(re.sub(r"-\d{8}$", "", model))   # dated ids share a price
        if p is None:
            return None, f"no price for {model} in tools/model-prices.tsv"
        unsplit = max(0, c.get("cache_write", 0) - c.get("cache_write_5m", 0) - c.get("cache_write_1h", 0))
        total += (c.get("input", 0) * p["input"] + c.get("output", 0) * p["output"]
                  + c.get("cache_read", 0) * p["cache_read"]
                  + (c.get("cache_write_5m", 0) + unsplit) * p["cache_write_5m"]
                  + c.get("cache_write_1h", 0) * p["cache_write_1h"]) / 1e6
        total += c.get("web_search", 0) * p.get("web_search_each", 0)
        dates.add(p["as_of"])
    return round(total, 2), "estimated from tokens at list prices as of " + ", ".join(sorted(dates))


def ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def projects_dir():
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return pathlib.Path(base).expanduser() / "projects" if base else pathlib.Path.home() / ".claude/projects"


def read_env():
    env = {}
    f = ROOT / ".env.local"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def port_size(port):
    java = list((port / "src").rglob("*.java")) if (port / "src").is_dir() else []
    props = {}
    for f in [port / "gradle.properties"] + sorted((port / "versions").glob("*.properties")):
        if f.is_file():
            for k, v in re.findall(r"(?m)^\s*([\w.]+)\s*=\s*(\S*)", f.read_text(errors="replace", encoding="utf-8")):
                props.setdefault(k, []).append(v)
    return {
        "minecraft": sorted(set(props.get("minecraft_version", []))),
        "neoforge": sorted(set(props.get("neo_version", []))),
        "java_files": sum(1 for p in java if "/test/" not in str(p)),
        "mixin_classes": sum(1 for p in java if re.search(r"/mixins?/", str(p))),
        "geckolib": any(v == "true" for v in props.get("uses_geckolib", [])),
    }


def collect(modid, projects):
    rx = re.compile(r"mods/" + re.escape(modid) + r"(?:/|\b)")
    other_rx = re.compile(r"mods/([A-Za-z0-9_-]+)(?=/)")
    files = collections.defaultdict(list)                    # session -> [jsonl files]
    for f in projects.rglob("*.jsonl"):
        sid = f.parent.parent.name if f.parent.name == "subagents" else f.stem
        files[sid].append(f)
    sessions = {}
    for sid, fs in files.items():
        reqs, cost, touches, others = {}, None, False, collections.Counter()
        versions, entry = set(), set()
        for f in fs:
            for line in open(f, errors="replace", encoding="utf-8"):
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") == "cost-state" and f.parent.name != "subagents":
                    cost = max(cost or 0, d.get("totalCostUSD") or 0)
                if d.get("type") != "assistant":
                    continue
                m = d.get("message") or {}
                uses = [c for c in m.get("content") or [] if isinstance(c, dict) and c.get("type") == "tool_use"]
                calls = json.dumps([c.get("input") for c in uses])
                if rx.search(calls):
                    touches = True
                # only WORK on another mod makes a session mixed: reading a finished port for reference
                # (Read/Grep/Glob) is part of this port, not a second one
                work = json.dumps([c.get("input") for c in uses if c.get("name") not in READ_ONLY_TOOLS])
                for o in set(other_rx.findall(work)) - {modid}:
                    others[o] += 1
                versions.add(d.get("version"))
                entry.add(d.get("entrypoint"))
                rid = (f.name, d.get("requestId") or d.get("uuid"))
                if rid not in reqs and m.get("model") != "<synthetic>":
                    reqs[rid] = (ts(d["timestamp"]), m.get("model", "?"), d.get("effort") or "unset",
                                 m.get("usage") or {}, f.parent.name == "subagents")
        if touches:
            sessions[sid] = dict(reqs=list(reqs.values()), cost=cost, others=others,
                                 versions=versions - {None}, entry=entry - {None})
    return sessions


def summarise(modid, sessions, port):
    per_model = collections.defaultdict(collections.Counter)
    effort, times = collections.Counter(), []
    for s in sessions.values():
        for t, model, eff, u, sub in s["reqs"]:
            c = per_model[model]
            cc = u.get("cache_creation") or {}
            c["requests"] += 1
            c["subagent_requests"] += sub
            c["input"] += u.get("input_tokens", 0)
            c["output"] += u.get("output_tokens", 0)
            c["thinking"] += (u.get("output_tokens_details") or {}).get("thinking_tokens", 0)
            c["cache_read"] += u.get("cache_read_input_tokens", 0)
            c["cache_write"] += u.get("cache_creation_input_tokens", 0)
            c["cache_write_5m"] += cc.get("ephemeral_5m_input_tokens", 0)
            c["cache_write_1h"] += cc.get("ephemeral_1h_input_tokens", 0)
            st = u.get("server_tool_use") or {}
            c["web_search"] += st.get("web_search_requests", 0)
            c["web_fetch"] += st.get("web_fetch_requests", 0)
            effort[eff] += 1
            times.append(t)
    times.sort()
    costs = [s["cost"] for s in sessions.values()]
    usd = round(sum(costs), 2) if costs and all(c is not None for c in costs) else None
    usd_source, usd_note = ("recorded", None) if usd is not None else (None, None)
    if usd is None and per_model:
        usd, why = estimate_usd(per_model, load_prices())
        usd_source = "estimated" if usd is not None else None
        usd_note = why if usd is not None else (
            "not recorded: this Claude Code build writes no cost-state to its transcripts (tokens are exact), "
            "and " + why)
    if usd is None and usd_note is None:
        usd_note = "not recorded: this Claude Code build writes no cost-state to its transcripts (tokens are exact)"
    totals = collections.Counter()
    for c in per_model.values():
        totals.update(c)
    day = lambda x: datetime.datetime.utcfromtimestamp(x).strftime("%Y-%m-%dT%H:%MZ")
    return {
        "schema": 1,
        "modid": modid,
        "recorded_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"),
        "sessions": len(sessions),
        "start": day(times[0]) if times else None,
        "end": day(times[-1]) if times else None,
        "wall_hours": round((times[-1] - times[0]) / 3600, 2) if times else 0,
        "active_hours": round(sum(min(b - a, GAP) for a, b in zip(times, times[1:])) / 3600, 2),
        "usd": usd,
        "usd_source": usd_source,
        "usd_note": usd_note,
        "models": {k: dict(v) for k, v in sorted(per_model.items())},
        "totals": dict(totals),
        "effort": dict(effort),
        "claude_code": sorted({v for s in sessions.values() for v in s["versions"]}),
        "entrypoint": sorted({e for s in sessions.values() for e in s["entry"]}),
        "contamination": {sid[:8]: dict(s["others"].most_common(5)) for sid, s in sessions.items() if s["others"]},
        "size": port_size(port),
    }


CORPUS_COLS = ["month", "minecraft", "java_files", "mixin_classes", "geckolib", "models", "effort", "sessions",
               "requests", "subagent_requests", "active_hours", "wall_hours", "input", "output", "thinking",
               "cache_read", "cache_write", "cache_write_5m", "cache_write_1h", "usd", "usd_source", "claude_code", "entrypoint",
               "mixed_session", "source"]


def two_sig(n):
    """Round a size to two significant figures: exact counts + a version would identify a mod."""
    return 0 if not n else int(float(f"{n:.2g}"))


def corpus_row(c):
    """The row that goes into the PUBLIC corpus (docs/port-costs.tsv): no modid, no names, no paths."""
    t, s = c["totals"], c["size"]
    return {
        "month": (c["start"] or "")[:7], "minecraft": "+".join(s["minecraft"]),
        "java_files": two_sig(s["java_files"]), "mixin_classes": two_sig(s["mixin_classes"]),
        "geckolib": "yes" if s["geckolib"] else "no", "models": "+".join(c["models"]),
        "effort": max(c["effort"], key=c["effort"].get) if c["effort"] else "",
        "sessions": c["sessions"], "requests": t.get("requests", 0), "subagent_requests": t.get("subagent_requests", 0),
        "active_hours": c["active_hours"], "wall_hours": c["wall_hours"], "input": t.get("input", 0),
        "output": t.get("output", 0), "thinking": t.get("thinking", 0), "cache_read": t.get("cache_read", 0),
        "cache_write": t.get("cache_write", 0), "cache_write_5m": t.get("cache_write_5m", 0), "cache_write_1h": t.get("cache_write_1h", 0),
        "usd": "" if c["usd"] is None else f"{c['usd']:.2f}", "usd_source": c.get("usd_source") or "",
        "claude_code": "+".join(c["claude_code"]),
        "entrypoint": "+".join(c["entrypoint"]), "mixed_session": "yes" if c["contamination"] else "no",
        "source": "transcript (port-cost.py)",
    }


def migration_section(c):
    t = c["totals"]
    if c["usd"] is None:
        usd = "**dollars not recorded by this Claude Code build**"
    elif c.get("usd_source") == "estimated":
        usd = f"**≈ ${c['usd']:.2f}** ({c['usd_note']})"
    else:
        usd = f"**${c['usd']:.2f}**"
    models = ", ".join(f"{m} ({v['requests']} req)" for m, v in c["models"].items())
    effort = ", ".join(f"{k} {v}" for k, v in sorted(c["effort"].items()))
    size = c["size"]
    lines = [
        "## Cost (recorded by tools/port-cost.py -- do not edit by hand)",
        f"- {usd} over {c['sessions']} session(s): {c['active_hours']} h active, {c['wall_hours']} h wall "
        f"({c['start']} to {c['end']})",
        f"- model: {models}; effort: {effort}",
        f"- tokens: output {t.get('output', 0):,} (thinking {t.get('thinking', 0):,}), input {t.get('input', 0):,}, "
        f"cache read {t.get('cache_read', 0):,}, cache write {t.get('cache_write', 0):,} "
        f"(5m {t.get('cache_write_5m', 0):,} / 1h {t.get('cache_write_1h', 0):,})",
        f"- size: {size['java_files']} Java files, {size['mixin_classes']} mixin classes"
        f"{', GeckoLib' if size['geckolib'] else ''}; target Minecraft {', '.join(size['minecraft']) or '?'}",
    ]
    if c["contamination"]:
        lines.append(f"- ⚠ these sessions also touched other mods: {c['contamination']} -- the cost includes that work")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("modid")
    ap.add_argument("--workspace")
    ap.add_argument("--projects", help="transcript directory (default ~/.claude/projects)")
    ap.add_argument("--print", action="store_true", help="print the record, write nothing")
    a = ap.parse_args()
    ws = pathlib.Path(os.path.expanduser(a.workspace or read_env().get("MIGRATE_WORKSPACE", "") or "."))
    port = ws / "mods" / a.modid
    projects = pathlib.Path(a.projects).expanduser() if a.projects else projects_dir()
    if not projects.is_dir():
        print(f"port-cost: no transcripts at {projects} -- nothing recorded", file=sys.stderr)
        return 2
    sessions = collect(a.modid, projects)
    if not sessions:
        print(f"port-cost: no session in {projects} touched mods/{a.modid} -- nothing recorded", file=sys.stderr)
        return 2
    c = summarise(a.modid, sessions, port)
    sec = migration_section(c)
    print(sec, end="")
    if a.print:
        return 0
    if not port.is_dir():
        print(f"port-cost: {port} does not exist -- printed only", file=sys.stderr)
        return 2
    cost = port / "COST.json"
    try:   # same figures as last time: keep its recorded_at, so a re-run changes no file (and finish-port
        old = json.loads(cost.read_text(encoding="utf-8"))   # makes no commit, even across a minute)
        if {k: v for k, v in old.items() if k != "recorded_at"} == {k: v for k, v in c.items() if k != "recorded_at"}:
            c["recorded_at"] = old.get("recorded_at", c["recorded_at"])
    except (OSError, ValueError, AttributeError):
        pass
    cost.write_text(json.dumps(c, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    mig = port / "MIGRATION.md"
    text = mig.read_text(encoding="utf-8") if mig.exists() else f"# {a.modid} migration\n"
    text = re.sub(r"(?ms)^## Cost \(recorded by tools/port-cost\.py.*?(?=^## |\Z)", "", text).rstrip("\n")
    mig.write_text(text + "\n\n" + sec, encoding="utf-8")
    print(f"port-cost: wrote {port / 'COST.json'} and the Cost section of MIGRATION.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
