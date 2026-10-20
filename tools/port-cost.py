#!/usr/bin/env python3
"""Record what a port cost: price tier, effort, every token type, dollars, time, and the port's size.

    python3 tools/port-cost.py <modid>              # writes mods/<modid>/COST.json + MIGRATION.md "## Cost"
    python3 tools/port-cost.py <modid> --print      # show it, write nothing
    python3 tools/port-cost.py --self-check         # fixture transcripts: attribution and the default path
    python3 tools/port-cost.py round-table --since TIME --fork LABEL=DIR [--fork ...] [--projects DIR]
                                                    # a keep-current round's per-fork, per-version cost and time table
    python3 tools/port-cost.py round-table --since TIME --session DIR | --session-cwd DIR [--marks round/marks.tsv]
                                                    # the round's own session, split per version at its marks
    python3 tools/port-cost.py mark --fork LABEL --version V [--phase upstream|backlog|review|overhead] [--file round/marks.tsv]
                                                    # the round starts V (or that kind of work on V) now

NO MODEL IDENTIFIER IS WRITTEN. Records go into fork repositories (.port/<v>/COST.md, a layer's COST.md) and the
public corpus, so requests are grouped by a neutral PRICE TIER instead (from the list input price in
tools/model-prices.tsv: top, mid or small; `unpriced` for a model with no row): tokens, dollars and wall time stay exact. Records written
before this change keep their `model:` lines as history.

tools/finish-port.py runs this for you. Numbers are read from Claude Code's own transcripts
(~/.claude/projects, or $CLAUDE_CONFIG_DIR/projects). Tokens are never estimated; dollars are
estimated from them only when Claude Code recorded none (see `dollars` below).

WHICH REQUESTS COUNT
    Every session whose tool calls touch `mods/<modid>`, WHOLE (a port session's triage and setup are
    part of the port), plus that session's subagents (<session>/subagents/*.jsonl). Each API request is
    counted ONCE: usage is repeated on every content block of a response, so counting lines doubles it.
    A session that also WORKED on other mods (edits, commands -- not just reading one for reference) is
    listed under `contamination`, so a mixed session is visible rather than silently inflating this port.

ONE TARGET OF A FORK LAYER (--path DIR --target T)
    A session that works on several targets of a fork, or on two forks, at once is NOT charged in full to each:
    every request is charged to at most one target, by what its own tool calls touched (attribute() below):
      - a request naming a target of this checkout (.multiversion/targets/<t>, build/mc<t>, -mc<t>, --target/--mc/
        -Pmc <t>, "target <t>", build.sh <t>) is that target's;
      - one touching a path in another git work tree (another fork, another worktree) and not the checkout is
        `elsewhere`;
      - one touching the checkout without naming a target, or touching nothing (thinking, reading the migrator,
        a scratch file), keeps the label of the request before it in the SAME transcript; with nothing before it in this
        checkout it is `shared` (this fork's base, orchestration);
      - each transcript (a session, each subagent, each loop worker) starts from what its first prompt names, so a
        subagent sent to fix one target is charged to that target, never to every target its parent touched.
    The record counts only this target's requests (dollars estimated from their tokens); the shared requests are
    reported on their own line and are not in the total, so the records of concurrent targets add up to at most
    the real spend. Without --target nothing of this applies: every request of a matching session counts.

WHAT IS RECORDED (so costs can be normalised later)
    per tier    requests, input, output (and the thinking part of it), cache reads, cache writes (5-minute
                and 1-hour), web searches/fetches -- per price tier, never per model name
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


TIERS = ((3.0, "top"), (1.5, "mid"), (0.0, "small"))     # by list input price, US$ per million tokens


def tier_of(model, prices):
    """The neutral price-tier label a record carries instead of the model's name: from its list input price in
    tools/model-prices.tsv (top from $3, mid from $1.50, small below), `unpriced` for a model with no row."""
    p = prices.get(model) or prices.get(re.sub(r"-\d{8}$", "", model))
    if not p:
        return "unpriced"
    return next(name for floor, name in TIERS if p["input"] >= floor)


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


def port_size(port, target=None):
    java = list((port / "src").rglob("*.java")) if (port / "src").is_dir() else []
    if target is not None:                 # a layer target's own work is its overlay files
        java = list((port / ".multiversion" / "targets" / target).rglob("*.java"))
    props = {}
    # A fork layer (.multiversion/) states each target's versions as `prop.<key>=` in its target.properties.
    layer = sorted((port / ".multiversion" / "targets").glob("*/target.properties")) if target is None \
        else [port / ".multiversion" / "targets" / target / "target.properties"]
    files = [port / "gradle.properties"] + sorted((port / "versions").glob("*.properties"))
    if target is not None:
        files = []                         # a single layer target: its own versions, not the base's
    for f in files + layer:
        if f.is_file():
            for k, v in re.findall(r"(?m)^\s*([\w.]+)\s*=\s*(\S*)", f.read_text(errors="replace", encoding="utf-8")):
                props.setdefault(k[len("prop."):] if k.startswith("prop.") else k, []).append(v)
    return {
        "minecraft": sorted(set(props.get("minecraft_version", []))),
        "neoforge": sorted(set(props.get("neo_version", []))),
        "java_files": sum(1 for p in java if "/test/" not in str(p)),
        "mixin_classes": sum(1 for p in java if re.search(r"/mixins?/", str(p))),
        "geckolib": any(v == "true" for v in props.get("uses_geckolib", [])),
    }


def collect(modid, projects, path=None, labels=(), attr=None):
    """`path`: match sessions by a checkout path instead of mods/<modid> (an upstream fork lives elsewhere).
    `labels`: [(name, path)] -- each request is labelled with the first path its tool calls touch, and a request
    touching none inherits the previous one's label (its reading and thinking belong to that work).
    `attr`: an attribute() labeller -- replaces `labels`; each transcript file is labelled on its own."""
    # the tool calls are matched as JSON text, where a Windows path's backslashes are doubled: match both forms
    rx = re.compile("|".join(re.escape(f) for f in sorted({path, json.dumps(path)[1:-1]}, key=len, reverse=True))
                    if path else r"mods/" + re.escape(modid) + r"(?:/|\b)")
    other_rx = re.compile(r"mods/([A-Za-z0-9_-]+)(?=/)")
    files = collections.defaultdict(list)                    # session -> [jsonl files]
    for f in projects.rglob("*.jsonl"):
        sid = f.parent.parent.name if f.parent.name == "subagents" else f.stem
        files[sid].append(f)
    sessions = {}
    for sid, fs in files.items():
        reqs, cost, touches, others = {}, None, False, collections.Counter()
        lab, current = {}, "other"
        versions, entry = set(), set()
        for f in fs:
            prev = None                                      # attr: each transcript starts from its own prompt
            for line in open(f, errors="replace", encoding="utf-8"):
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") == "cost-state" and f.parent.name != "subagents":
                    cost = max(cost or 0, d.get("totalCostUSD") or 0)
                if attr and prev is None and d.get("type") == "user":
                    prev = attr(json.dumps(first_prompt(d)), d.get("cwd"), None)
                if d.get("type") != "assistant":
                    continue
                m = d.get("message") or {}
                uses = [c for c in m.get("content") or [] if isinstance(c, dict) and c.get("type") == "tool_use"]
                calls = json.dumps([c.get("input") for c in uses])
                if rx.search(calls):
                    touches = True
                if attr:
                    if uses:
                        prev = attr(calls, d.get("cwd"), prev)
                    current = prev or "elsewhere"
                for name, lp in labels:
                    if lp in calls:
                        current = name
                        break
                # only WORK on another mod makes a session mixed: reading a finished port for reference
                # (Read/Grep/Glob) is part of this port, not a second one
                work = json.dumps([c.get("input") for c in uses if c.get("name") not in READ_ONLY_TOOLS])
                for o in set(other_rx.findall(work)) - {modid}:
                    others[o] += 1
                versions.add(d.get("version"))
                entry.add(d.get("entrypoint"))
                rid = (f.name, d.get("requestId") or d.get("uuid"))
                if rid not in reqs and m.get("model") != "<synthetic>":
                    lab[rid] = current
                    reqs[rid] = (ts(d["timestamp"]), m.get("model", "?"), d.get("effort") or "unset",
                                 m.get("usage") or {}, f.parent.name == "subagents")
        if touches:
            sessions[sid] = dict(reqs=list(reqs.values()), labels=[lab[r] for r in reqs], cost=cost, others=others,
                                 versions=versions - {None}, entry=entry - {None})
    return sessions


NEUTRAL = ("/dev/", "/proc/", "/usr/", "/bin/", "/etc/")


def _in_other_repo(p, home, cache={}):
    """True when the path lies in a git work tree (another fork, another worktree) -- the evidence that a request worked
    somewhere else. A scratch file, a config or a path that no longer exists is no evidence either way."""
    q = pathlib.Path(p)
    for d in [q] + list(q.parents):
        k = str(d)
        if k not in cache:
            try:
                cache[k] = (d / ".git").exists()
            except OSError:
                cache[k] = False
        if cache[k]:
            return k != home
    return False
ABS_RX = re.compile(r"(?<![\w.~/:\\-])(/[\w.@+-]+(?:/[\w.@+-]*)*|[A-Za-z]:(?:\\\\|/)[^\s\"']*)")


def _target_rx(t):
    """A target named in a tool call: its overlay or build dir, its jar suffix, or a target argument."""
    T, B = re.escape(t), r"(?![0-9]|\.[0-9])"
    return re.compile(r"(?:\.multiversion[/\\]+targets[/\\]+" + T + r"|(?<![A-Za-z])mc" + T
                      + r"|(?:--mc|-Pmc|(?<![A-Za-z])[Tt]arget)[= :]+" + T + r"|build\.sh\s+" + T + ")" + B)


def attribute(checkout, targets, migrator=ROOT):
    """-> label(text, cwd, prev) -> label, for one checkout of a fork layer. Labels: 'target:<t>', 'shared' (this
    checkout, no target named), 'elsewhere' (only paths outside it). `prev` is the label of the request before it in
    the same transcript (None at its start). See the docstring, ONE TARGET OF A FORK LAYER."""
    home = str(checkout).rstrip("/\\")
    forms = {home, json.dumps(home)[1:-1]}
    here_rx = re.compile("(?:" + "|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True)) + r")(?![\w.-])")
    neutral = NEUTRAL + tuple(f + "/" for f in {str(migrator).rstrip("/\\")})
    trx = [(t, _target_rx(t)) for t in targets]

    def label(text, cwd, prev):
        here = bool(here_rx.search(text))
        foreign = any(not here_rx.match(p) and not (p + "/").startswith(neutral) and _in_other_repo(p, home)
                      for p in ABS_RX.findall(text))
        in_cwd = bool(cwd) and bool(here_rx.match(str(cwd)))
        if not here:
            if foreign:
                return "elsewhere"
            if not (in_cwd or (prev or "elsewhere") != "elsewhere"):
                return prev or "elsewhere"     # relative paths, or none, outside the checkout: no evidence
        named = [(m.start(), t) for t, rx in trx for m in [rx.search(text)] if m]
        if named:
            return "target:" + min(named)[1]
        if prev and prev.startswith("target:"):
            return prev                        # reading the base or the migrator while working on a target
        return "shared" if (here or in_cwd) else (prev or "elsewhere")
    return label


def first_prompt(d):
    """The text of a transcript's first user message (a session's task, a subagent's or worker's prompt)."""
    c = (d.get("message") or {}).get("content")
    if isinstance(c, str):
        return c
    return " ".join(x.get("text", "") for x in c or [] if isinstance(x, dict) and x.get("type") == "text")


def summarise(modid, sessions, port, target=None):
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
    prices, per_tier = load_prices(), collections.defaultdict(collections.Counter)
    for model, c in per_model.items():
        per_tier[tier_of(model, prices)].update(c)
    day = lambda x: datetime.datetime.fromtimestamp(x, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    return {
        "schema": 1,
        "modid": modid,
        "recorded_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "sessions": len(sessions),
        "start": day(times[0]) if times else None,
        "end": day(times[-1]) if times else None,
        "wall_hours": round((times[-1] - times[0]) / 3600, 2) if times else 0,
        "active_hours": round(sum(min(b - a, GAP) for a, b in zip(times, times[1:])) / 3600, 2),
        "usd": usd,
        "usd_source": usd_source,
        "usd_note": usd_note,
        "tiers": {k: dict(v) for k, v in sorted(per_tier.items())},
        "totals": dict(totals),
        "effort": dict(effort),
        "claude_code": sorted({v for s in sessions.values() for v in s["versions"]}),
        "entrypoint": sorted({e for s in sessions.values() for e in s["entry"]}),
        "contamination": {sid[:8]: dict(s["others"].most_common(5)) for sid, s in sessions.items() if s["others"]},
        "size": port_size(port, target),
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
        "geckolib": "yes" if s["geckolib"] else "no", "models": "+".join(c.get("tiers") or c.get("models") or {}),
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
    tiers = ", ".join(f"{m} ({v['requests']} req)" for m, v in c["tiers"].items())
    effort = ", ".join(f"{k} {v}" for k, v in sorted(c["effort"].items()))
    size = c["size"]
    lines = [
        "## Cost (recorded by tools/port-cost.py -- do not edit by hand)",
        f"- {usd} over {c['sessions']} session(s): {c['active_hours']} h active, {c['wall_hours']} h wall "
        f"({c['start']} to {c['end']})",
        f"- price tier: {tiers}; effort: {effort}",
        f"- tokens: output {t.get('output', 0):,} (thinking {t.get('thinking', 0):,}), input {t.get('input', 0):,}, "
        f"cache read {t.get('cache_read', 0):,}, cache write {t.get('cache_write', 0):,} "
        f"(5m {t.get('cache_write_5m', 0):,} / 1h {t.get('cache_write_1h', 0):,})",
        f"- size: {size['java_files']} Java files, {size['mixin_classes']} mixin classes"
        f"{', GeckoLib' if size['geckolib'] else ''}; target Minecraft {', '.join(size['minecraft']) or '?'}",
    ]
    if c["contamination"]:
        lines.append(f"- ⚠ these sessions also touched other mods: {c['contamination']} -- the cost includes that work")
    return "\n".join(lines) + "\n"


def attributed(sessions, want):
    """Split attribute()-labelled sessions -> (only the `want` requests, priced from tokens; {kind: [requests, usd]}
    for this target, 'shared', 'other' targets of the checkout and 'elsewhere')."""
    mine, rest = {}, collections.defaultdict(list)
    for k, v in sessions.items():
        keep = [r for r, l in zip(v["reqs"], v["labels"]) if l == want]
        for r, l in zip(v["reqs"], v["labels"]):
            if l != want:
                rest["shared" if l == "shared" else "elsewhere" if l == "elsewhere" else "other"].append((k, r))
        if keep:   # a session's recorded total covers ALL of it: price only the requests charged here
            mine[k] = dict(v, reqs=keep, labels=[want] * len(keep), cost=None)
    last = max((r[0] for v in mine.values() for r in v["reqs"]), default=None)
    split = {"target": [sum(len(v["reqs"]) for v in mine.values()), None],
             "end": None if last is None else datetime.datetime.fromtimestamp(last, datetime.timezone.utc)
             .isoformat(timespec="milliseconds").replace("+00:00", "Z")}
    for kind in ("shared", "other", "elsewhere"):
        part = collections.defaultdict(list)
        for k, r in rest.get(kind, []):
            part[k].append(r)
        usd = None
        if part:
            per = summarise("", {k: dict(sessions[k], reqs=rs, cost=None) for k, rs in part.items()},
                            pathlib.Path(os.devnull) / "none")   # dollars only: no port to size
            usd = per["usd"]
        split[kind] = [sum(len(x) for x in part.values()), usd]
    return mine, split


def attribution_lines(c, split, since):
    """The COST.md lines that say how this record was attributed and what window it covers."""
    usd = lambda x: "unpriced" if x[1] is None else f"≈ ${x[1]:.2f}"
    window = f"from {since}" if since else "from the first transcript on this machine"
    return (f"- attribution: each request is charged to one target at most, by the paths and target its own tool calls "
            f"name (a request naming none keeps the label of the one before it in the same transcript; a subagent or "
            f"loop worker starts from its prompt). Not in the total above: this checkout's shared/orchestration "
            f"requests {split['shared'][0]} ({usd(split['shared'])}), other targets' {split['other'][0]}, "
            f"outside this checkout {split['elsewhere'][0]}. Records of concurrent targets add up.\n"
            f"- window: requests {window} to {split['end']} (requests before it are not in this record)\n")


# ---------------------------------------------------------------- a keep-current round's table (issue #135, part 2a)

RECORD = re.compile(r"(?m)^- (.*?) over \d+ session\(s\): ([\d.]+) h active, ([\d.]+) h wall \((\S+) to (\S+)\)")


def cost_records(text):
    """[(usd or None, estimated, wall hours, start, end)] of the records in a COST.md (or a MIGRATION.md Cost section)."""
    out = []
    for m in RECORD.finditer(text or ""):
        usd = re.search(r"\$([\d,]+\.\d+)", m.group(1))
        out.append((float(usd.group(1).replace(",", "")) if usd else None, "≈" in m.group(1), float(m.group(3)),
                    m.group(4), m.group(5)))
    return out


def _fb():
    import importlib.util
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("fork_branches_cost", ROOT / "tools/fork-branches.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def round_rows(forks, since, projects=None):
    """[(fork, version, usd or None, estimated, wall hours, records)]: a version-branch fork's rows from each version
    branch's .port/<v>/COST.md records that end at or after `since`; a single-version fork's one row measured from the
    transcripts that touched its checkout since then (it keeps no .port/ records)."""
    import subprocess
    fb, rows = _fb(), []
    cut = since if ("+" in since[10:] or since.endswith("Z")) else since + ("T00:00" if len(since) == 10 else "") + "+00:00"
    for label, d in forks:
        vb = fb.version_branches(d)
        if vb:
            for v in sorted(vb, key=fb.mc_key):
                r = subprocess.run(["git", "-C", str(d), "show", f"{vb[v]}:.port/{v}/COST.md"], capture_output=True,
                                   text=True, encoding="utf-8", errors="replace")
                recs = [x for x in cost_records(r.stdout if r.returncode == 0 else "") if ts(x[4]) >= ts(cut)]
                usd = None if any(x[0] is None for x in recs) or not recs else round(sum(x[0] for x in recs), 2)
                rows.append((label, v, usd if recs else 0.0, any(x[1] for x in recs), round(sum(x[2] for x in recs), 2),
                             len(recs)))
            continue
        proj = pathlib.Path(projects).expanduser() if projects else projects_dir()
        sessions = collect("", proj, os.path.abspath(d)) if proj.is_dir() else {}
        for k in list(sessions):
            v = sessions[k]
            v["reqs"] = [x for x in v["reqs"] if x[0] >= ts(cut)]
            v["cost"] = None
            if not v["reqs"]:
                del sessions[k]
        if sessions:
            c = summarise("", sessions, pathlib.Path(os.devnull) / "none")
            rows.append((label, "head", c["usd"], c.get("usd_source") == "estimated", c["wall_hours"], len(sessions)))
        else:
            rows.append((label, "head", 0.0, False, 0.0, 0))
    return rows


def round_table(rows):
    money = lambda u, est: "unpriced" if u is None else f"{'≈ ' if est else ''}${u:.2f}"
    if rows and len(rows[0]) == 7:
        return phased_table(rows, money)
    out = ["| Fork | Version | Cost | Wall time |", "|---|---|---|---|"]
    grand, gw, gest, gnone = 0.0, 0.0, False, False
    for fork in dict.fromkeys(r[0] for r in rows):
        mine = [r for r in rows if r[0] == fork]
        for _f, v, u, est, wall, n in mine:
            out.append(f"| {fork} | {v} | {money(u, est) if n else '-'} | {wall:.2f} h |")
        none = any(r[2] is None for r in mine)
        tot = round(sum(r[2] or 0 for r in mine), 2)
        est = any(r[3] for r in mine)
        wall = round(sum(r[4] for r in mine), 2)
        out.append(f"| **{fork}** | **total** | **{money(None if none else tot, est)}**"
                   + (" (some unpriced)" if none and tot else "") + f" | **{wall:.2f} h** |")
        grand += tot; gw += wall; gest |= est; gnone |= none
    out.append(f"| **all forks** | | **{money(None if gnone else round(grand, 2), gest)}** | **{round(gw, 2):.2f} h** |")
    return "\n".join(out) + "\n"


PHASES = ("upstream", "backlog", "review", "overhead")
# upstream: the authors' cascade and resolving its conflicts; backlog: the selected known-issue fixes; review: fresh
# reviews and the fixes they cause; overhead: pins, gates, CI waits, records. A mark without a phase: "-".


def phased_table(rows, money):
    """round_table for rows that carry a phase (a marks file with --phase marks): one row per fork x version x phase,
    each version's total (what the unphased table shows for it), each fork's, all forks', then the per-phase totals --
    so the cost of taking the authors' code (upstream) can be compared week over week."""
    tot = lambda rs: (None if any(r[2] is None for r in rs) else round(sum(r[2] for r in rs), 2), any(r[3] for r in rs),
                      round(sum(r[4] for r in rs), 2))
    cell = lambda rs: (lambda u, e, w: f"**{money(u, e)}** | **{w:.2f} h**")(*tot(rs))
    out = ["| Fork | Version | Phase | Cost | Wall time |", "|---|---|---|---|---|"]
    for fork in dict.fromkeys(r[0] for r in rows):
        mine = [r for r in rows if r[0] == fork]
        for v in dict.fromkeys(r[1] for r in mine):
            vs = [r for r in mine if r[1] == v]
            for _f, _v, u, est, wall, n, ph in vs:
                out.append(f"| {fork} | {v} | {ph or '-'} | {money(u, est) if n else '-'} | {wall:.2f} h |")
            if len(vs) > 1:
                out.append(f"| {fork} | {v} | **all phases** | {cell(vs)} |")
        out.append(f"| **{fork}** | **total** | | {cell(mine)} |")
    out.append(f"| **all forks** | | | {cell(rows)} |")
    out += ["", "| Phase | Cost | Wall time |", "|---|---|---|"]
    for ph in [p_ for p_ in PHASES + ("",) if any(r[6] == p_ for r in rows)]:
        out.append(f"| {ph or '-'} | {cell([r for r in rows if r[6] == ph])} |")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- the round's own session, split by its marks
#
# A scheduled cloud session runs the whole round from one working directory: its transcripts touch the fork checkouts
# through worktrees, subagents and relative paths, and no COST.md record is written when a run stops before its cost
# stage -- so the table read $0.00 for every version. With --session DIR (the session's transcript directory under
# ~/.claude/projects) or --session-cwd DIR (every transcript whose records ran in DIR or below it), the table charges
# that session's own requests instead, split per version by the round's own timestamps: `port-cost.py mark` appends
# "<ISO time>\t<fork>\t<version>" to round/marks.tsv as the round starts each version; a request belongs to the last
# mark at or before it (before the first: the round's setup row). Wall time is from a mark to the next.

MARKS_DEFAULT = pathlib.Path("round/marks.tsv")


def read_marks(path):
    """[(time, fork, version)] sorted by time, from a marks file ("" lines and # comments skipped); a mark written with
    --phase has a fourth cell and comes back as (time, fork, version, phase). A file of the older three-column form reads
    exactly as before."""
    out = []
    p = pathlib.Path(path)
    for line in p.read_text(encoding="utf-8").splitlines() if p.is_file() else []:
        cells = line.split("\t")
        if len(cells) >= 3 and not line.startswith("#"):
            try:
                m = (ts(cells[0].strip()), cells[1].strip(), cells[2].strip())
            except ValueError:
                continue
            out.append(m + (cells[3].strip(),) if len(cells) >= 4 and cells[3].strip() else m)
    return sorted(out, key=lambda m: m[0])


def cwd_form(x):
    """A working directory in one form on every OS: forward slashes, no trailing one, the drive letter lower-case. A
    path rooted at / or \\ counts as absolute as given (Python 3.13's ntpath.isabs says no to a drive-less one)."""
    x = str(x)
    if not re.match(r"^(?:[A-Za-z]:)?[/\\]", x):
        x = os.path.abspath(x)
    x = x.replace("\\", "/").rstrip("/")
    return x[0].lower() + x[1:] if re.match(r"^[A-Za-z]:", x) else x


def session_transcripts(projects=None, session_dir=None, cwd=None):
    """{session id: session} (collect()'s form, every request labelled "round") of the transcripts in session_dir, or
    of every transcript under `projects` whose records ran in `cwd` or a directory below it."""
    files = collections.defaultdict(list)
    roots = [pathlib.Path(session_dir).expanduser()] if session_dir else [pathlib.Path(projects).expanduser() if projects
                                                                           else projects_dir()]
    want = cwd_form(cwd) if cwd else None
    for root in roots:
        for f in sorted(root.rglob("*.jsonl")) if root.is_dir() else []:
            sid = f.parent.parent.name if f.parent.name == "subagents" else f.stem
            files[sid].append(f)
    sessions, seen = {}, set()       # one request once, across every file: a resumed session copies the earlier history
    norm = cwd_form
    for sid, fs in files.items():
        reqs, hit = {}, want is None
        for f in fs:
            for line in open(f, errors="replace", encoding="utf-8"):
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                c = d.get("cwd")
                if want and isinstance(c, str) and (norm(c) == want or norm(c).startswith(want + "/")):
                    hit = True
                m = d.get("message") or {}
                if d.get("type") != "assistant" or m.get("model") == "<synthetic>" or not d.get("timestamp"):
                    continue
                rid = d.get("requestId") or (f.name, d.get("uuid"))
                if rid in seen:
                    continue
                seen.add(rid)
                reqs[rid] = (ts(d["timestamp"]), m.get("model", "?"), d.get("effort") or "unset", m.get("usage") or {},
                             f.parent.name == "subagents")
        if hit and reqs:
            sessions[sid] = dict(reqs=list(reqs.values()), labels=["round"] * len(reqs), cost=None,
                                 others=collections.Counter(), versions=set(), entry=set())
    return sessions


def session_rows(sessions, since, marks, until=None):
    """[(fork, version, usd, estimated, wall hours, requests)]: the sessions' requests since `since`, each charged to the
    last mark at or before it (before the first mark: ("(round)", "setup")); wall time from each mark to the next (the
    last one: to its last request, or `until`). With any phased mark: phased_rows, one more cell per row."""
    if any(len(m) == 4 for m in marks):
        return phased_rows(sessions, since, marks, until)
    cut = ts(since)
    reqs = sorted(((r, sid) for sid, v in sessions.items() for r in v["reqs"] if r[0] >= cut), key=lambda x: x[0][0])
    bounds = [(cut, "(round)", "setup")] + [m for m in marks if m[0] >= cut]
    buckets = collections.OrderedDict(((f, v), []) for _t, f, v in bounds)
    for r, sid in reqs:
        key = [(f, v) for t0, f, v in bounds if t0 <= r[0]][-1]
        buckets[key].append((r, sid))
    ends = {}
    for i, (t0, f, v) in enumerate(bounds):
        nxt = bounds[i + 1][0] if i + 1 < len(bounds) else None
        last = max((r[0][0] for r in buckets[(f, v)]), default=t0)
        ends[(f, v)] = (t0, nxt if nxt is not None else (ts(until) if until else last))
    rows = []
    for (f, v), got in buckets.items():
        if (f, v) == ("(round)", "setup") and not got:
            continue
        part = collections.defaultdict(list)
        for r, sid in got:
            part[sid].append(r)
        c = summarise("", {k: dict(sessions[k], reqs=rs) for k, rs in part.items()}, pathlib.Path(os.devnull) / "none") \
            if part else {"usd": 0.0, "usd_source": None}
        t0, t1 = ends[(f, v)]
        rows.append((f, v, c["usd"], c.get("usd_source") == "estimated", round(max(0.0, t1 - t0) / 3600, 2), len(got)))
    return rows


def phased_rows(sessions, since, marks, until=None):
    """[(fork, version, usd, estimated, wall hours, requests, phase)]: as session_rows, keyed by fork x version x phase (a
    mark without one: ""), in order of first appearance; a key that recurs (review, overhead, review) sums its segments'
    requests and wall times. Each version's rows sum to what the unphased split charges it."""
    cut = ts(since)
    reqs = sorted(((r, sid) for sid, v in sessions.items() for r in v["reqs"] if r[0] >= cut), key=lambda x: x[0][0])
    bounds = [(cut, "(round)", "setup", "")] + [(m + ("",))[:4] for m in marks if m[0] >= cut]
    got = collections.OrderedDict(((b[1], b[2], b[3]), []) for b in bounds)
    wall = collections.defaultdict(float)
    seg = [[] for _ in bounds]
    for r, sid in reqs:
        seg[[i for i, b in enumerate(bounds) if b[0] <= r[0]][-1]].append((r, sid))
    for i, b in enumerate(bounds):
        key = (b[1], b[2], b[3])
        got[key] += seg[i]
        nxt = bounds[i + 1][0] if i + 1 < len(bounds) else None
        end = nxt if nxt is not None else (ts(until) if until else max((r[0][0] for r in seg[i]), default=b[0]))
        wall[key] += max(0.0, end - b[0])
    rows = []
    for (f, v, ph), g_ in got.items():
        if (f, v) == ("(round)", "setup") and not g_:
            continue
        part = collections.defaultdict(list)
        for r, sid in g_:
            part[sid].append(r)
        c = summarise("", {k: dict(sessions[k], reqs=rs) for k, rs in part.items()}, pathlib.Path(os.devnull) / "none") \
            if part else {"usd": 0.0, "usd_source": None}
        rows.append((f, v, c["usd"], c.get("usd_source") == "estimated", round(wall[(f, v, ph)] / 3600, 2), len(g_), ph))
    return rows


def mark_main(argv):
    ap = argparse.ArgumentParser(prog="port-cost.py mark", description="record that the round starts work on a fork's "
                                 "version now (round-table --session splits the session's cost at these marks)")
    ap.add_argument("--fork", required=True); ap.add_argument("--version", required=True)
    ap.add_argument("--file", default=str(MARKS_DEFAULT), help="default round/marks.tsv")
    ap.add_argument("--at", help="the time (ISO; default now)")
    ap.add_argument("--phase", choices=PHASES, help="the kind of work that starts now (round-table then splits each version "
                    "by it): upstream = the authors' cascade and its conflicts, backlog = the selected known-issue fixes, "
                    "review = fresh reviews and the fixes they cause, overhead = pins, gates, CI waits, records")
    a = ap.parse_args(argv)
    at = a.at or datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    ts(at)
    f = pathlib.Path(a.file)
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(f"{at}\t{a.fork}\t{a.version}" + (f"\t{a.phase}" if a.phase else "") + "\n")
    print(f"port-cost: mark {at} {a.fork} {a.version}" + (f" ({a.phase})" if a.phase else "") + f" -> {f}")
    return 0


def round_table_main(argv):
    ap = argparse.ArgumentParser(prog="port-cost.py round-table", description="a keep-current round's cost and time, "
                                 "per fork and per version, with totals (Markdown, for the plan issue's closing comment)")
    ap.add_argument("--since", required=True, help="the round's start (ISO; UTC unless it says otherwise)")
    ap.add_argument("--fork", action="append", default=[], metavar="LABEL=DIR", help="a fork's label and checkout")
    ap.add_argument("--projects", help="transcript directory (default ~/.claude/projects)")
    ap.add_argument("--session", metavar="DIR", help="this session's transcript directory: charge its own requests, split "
                    "per version at the round's marks (--marks), instead of the COST.md records")
    ap.add_argument("--session-cwd", metavar="DIR", help="the same for every transcript under --projects whose records ran "
                    "in DIR or below it (a scheduled session's working directory)")
    ap.add_argument("--marks", default=str(MARKS_DEFAULT), help="the round's marks (port-cost.py mark; default round/marks.tsv)")
    a = ap.parse_args(argv)
    since = a.since if ("+" in a.since[10:] or a.since.endswith("Z")) else a.since + ("T00:00" if len(a.since) == 10 else "") + "+00:00"
    if a.session or a.session_cwd:
        sessions = session_transcripts(a.projects, a.session, a.session_cwd)
        marks = read_marks(a.marks)
        print(round_table(session_rows(sessions, since, marks)), end="")
        print(f"\nFrom this session's own transcripts ({len(sessions)} session(s), "
              f"{a.session or 'cwd ' + a.session_cwd}), split at {len(marks)} mark(s) of {a.marks}; the forks' COST.md "
              "records are not added (they measure the same requests).")
        return 0
    if not a.fork:
        ap.error("--fork LABEL=DIR (or --session / --session-cwd)")
    forks = [tuple(x.split("=", 1)) for x in a.fork]
    if any(len(f) != 2 for f in forks):
        ap.error("--fork LABEL=DIR")
    print(round_table(round_rows(forks, a.since, a.projects)), end="")
    return 0


def self_check():
    """Fixture transcripts: one session drives two targets of a layer fork (and a second fork), with one subagent per
    target; a single-version checkout is matched as before. -> 0 when every case holds."""
    import contextlib, io, tempfile
    ok = True

    def expect(c, msg):
        nonlocal ok
        if not c:
            ok = False
            print("FAIL:", msg)

    def run(*argv):
        out, err = io.StringIO(), io.StringIO()
        old = sys.argv
        sys.argv = ["port-cost.py", *argv]
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main()
        finally:
            sys.argv = old
        return code, out.getvalue() + err.getvalue()

    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        fork, lib, sv = d / "fork", d / "fork-lib", d / "single"
        for t in ("1.21.4", "1.21.5"):
            (fork / ".multiversion/targets" / t).mkdir(parents=True)
            (fork / ".multiversion/targets" / t / "target.properties").write_text(f"prop.minecraft_version={t}\n", encoding="utf-8")
        (lib / ".multiversion/targets/1.21.4").mkdir(parents=True)
        (sv / "src").mkdir(parents=True)
        for r in (fork, lib, sv):
            (r / ".git").mkdir()
        proj = d / "projects" / "p"
        (proj / "S1" / "subagents").mkdir(parents=True)
        n = [0]

        def req(sid, path, out, cwd=None, tool="Edit", key="file_path"):
            n[0] += 1
            inp = {key: path} if path is not None else None
            content = [{"type": "tool_use", "name": tool, "input": inp}] if inp else [{"type": "text", "text": "thinking"}]
            return json.dumps({"type": "assistant", "sessionId": sid, "requestId": f"r{n[0]}", "cwd": cwd,
                               "timestamp": f"2026-10-01T10:{n[0]:02d}:00Z", "message": {"model": "claude-opus-5-5",
                               "content": content, "usage": {"input_tokens": 0, "output_tokens": out}}}) + "\n"

        def prompt(text, cwd=None):
            return json.dumps({"type": "user", "cwd": cwd, "message": {"role": "user", "content": text}}) + "\n"

        o4, o5 = f"{fork}/.multiversion/targets/1.21.4/A.java", f"{fork}/.multiversion/targets/1.21.5/A.java"
        main_s = (prompt("port the fork to 1.21.4 and 1.21.5")
                  + req("S1", o4, 100) + req("S1", None, 10) + req("S1", o5, 1000)
                  + req("S1", f"cd {fork} && .multiversion/build.sh 1.21.4 compileJava", 200, tool="Bash", key="command")
                  + req("S1", f"{fork}/src/main/java/B.java", 20, tool="Read")       # base source while on 1.21.4
                  + req("S1", f"{lib}/.multiversion/targets/1.21.4/C.java", 5000)    # the other fork's 1.21.4: not ours
                  + req("S1", None, 7))                                                # thinking about that one
        (proj / "S1.jsonl").write_text(main_s, encoding="utf-8")
        (proj / "S1/subagents/a4.jsonl").write_text(prompt(f"fix target 1.21.4 in {fork}") + req("S1", None, 3)
                                                   + req("S1", "ls build/mc1.21.4/x", 30, cwd=str(fork), tool="Bash", key="command"),
                                                   encoding="utf-8")
        (proj / "S1/subagents/a5.jsonl").write_text(prompt(f"fix target 1.21.5 in {fork}") + req("S1", "git status", 300,
                                                   cwd=str(fork), tool="Bash", key="command"), encoding="utf-8")
        (proj / "S2.jsonl").write_text(prompt(f"tidy {fork}") + req("S2", f"git -C {fork} log", 4, tool="Bash", key="command")
                                       + req("S2", f"{sv}/src/D.java", 9), encoding="utf-8")
        P = ["--projects", str(d / "projects")]
        out = {}
        for t in ("1.21.4", "1.21.5"):
            code, text = run("examplemod", "--path", str(fork), "--target", t, "--print", *P)
            out[t] = text
            expect(code == 0, f"{t}: exit {code}: {text}")
        sess = collect("examplemod", d / "projects", str(fork))
        whole = summarise("examplemod", sess, fork)["totals"]
        attr = attribute(str(fork), ["1.21.4", "1.21.5"])
        lab = collect("examplemod", d / "projects", str(fork), (), attr)
        per = collections.Counter()
        for v in lab.values():
            for r, l in zip(v["reqs"], v["labels"]):
                per[l] += r[3].get("output_tokens", 0)
        expect(per["target:1.21.4"] == 100 + 10 + 200 + 20 + 3 + 30 and per["target:1.21.5"] == 1000 + 300,
               f"each request goes to the target it named (a subagent to its prompt's): {dict(per)}")
        expect(per["elsewhere"] == 5000 + 7 + 9 and per["shared"] == 4, f"other fork / shared: {dict(per)}")
        expect(sum(per.values()) == whole["output"], f"the labels partition the whole: {sum(per.values())} vs {whole['output']}")
        expect("output 363 " in out["1.21.4"] and "output 1,300 " in out["1.21.5"],
               f"COST.md counts only the target's own requests: {out}")
        expect(all("- attribution: " in x and "- window: requests from the first transcript" in x for x in out.values())
               and "shared/orchestration requests 1 " in out["1.21.4"], f"the record says how it was attributed: {out}")
        expect(out["1.21.4"].startswith("## Cost (recorded by tools/port-cost.py"), "the record's format is kept")
        code, text = run("examplemod", "--path", str(fork), "--target", "1.21.5", "--since", "2026-10-01T10:04", "--print", *P)
        expect(code == 0 and "output 300 " in text and "from 2026-10-01T10:04" in text, f"--since still cuts: {text}")
        # single-version (no --target): every request of a matching session counts, exactly as before
        code, text = run("examplemod", "--path", str(sv), "--print", *P)
        expect(code == 0 and "output 13 " in text and "attribution" not in text and "window" not in text,
               f"single-version: the whole matching session, no attribution lines: {text}")
        code, text = run("examplemod", "--path", str(fork), "--print", *P)
        expect(code == 0 and f"output {whole['output']:,} " in text and "attribution" not in text,
               f"a layer checkout without --target is counted whole, as before: {text}")
        # no model identifier in a record (#137 item 1): a neutral price tier, tokens, dollars and wall time
        used = {json.loads(l)["message"]["model"] for l in main_s.splitlines() if '"assistant"' in l}
        expect(used and all(not any(m in x for m in used) and "- model:" not in x and "- price tier: top (" in x
                            for x in out.values())
               and not any(m in json.dumps(summarise("examplemod", sess, fork)) for m in used),
               f"records name a price tier, never the model: {out}")
        prices = load_prices()
        want = lambda inp: "top" if inp >= 3 else "mid" if inp >= 1.5 else "small"
        expect(prices and all(tier_of(m, prices) == want(r["input"]) for m, r in prices.items())
               and tier_of("some-unknown", prices) == "unpriced", "the tiers come from the list input prices")
        # the round's table: a version-branch fork's .port/<v>/COST.md records since the round's start, a single-version
        # fork measured from its transcripts, totals per fork and in all
        import subprocess
        vb = d / "vb"
        g = lambda *a: subprocess.run(["git", "-C", str(vb), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *a],
                                      capture_output=True, text=True, encoding="utf-8", check=True)
        vb.mkdir(); g("init", "-q", "-b", "neoforge-1.21.1")
        (vb / "x").write_text("x\n", encoding="utf-8"); g("add", "-A"); g("commit", "-qm", "port")
        g("checkout", "-q", "-b", "neoforge-1.21.4")
        (vb / ".port/1.21.4").mkdir(parents=True)
        (vb / ".port/1.21.4/branch.properties").write_text("minecraft_version=1.21.4\n", encoding="utf-8")
        (vb / ".port/1.21.4/COST.md").write_text(
            "## fork-port.py run since a\n\n## Cost (recorded by tools/port-cost.py -- do not edit by hand)\n"
            "- **$5.00** over 1 session(s): 1.0 h active, 2.0 h wall (2026-09-01T10:00Z to 2026-09-01T12:00Z)\n"
            "- model: an-old-record (3 req); effort: high\n\n## fork-port.py run since b\n\n"
            "## Cost (recorded by tools/port-cost.py -- do not edit by hand)\n"
            "- **≈ $1.25** (estimated from tokens) over 1 session(s): 0.5 h active, 0.75 h wall "
            "(2026-10-01T10:00Z to 2026-10-01T10:45Z)\n", encoding="utf-8")
        g("add", "-A"); g("commit", "-qm", "1.21.4")
        rows = round_rows([("o/lib", vb), ("o/dep", sv)], "2026-10-01", d / "projects")
        expect(rows[0][:5] == ("o/lib", "1.21.4", 1.25, True, 0.75) and rows[1][0:2] == ("o/dep", "head") and rows[1][5] == 1,
               f"round rows: only the records since the round's start; the single-version fork measured: {rows}")
        tab = round_table(rows)
        # the round's own session (weekly-round dry run, 2026-10-18: a scheduled session's transcripts touch no checkout
        # path, so the table read $0.00): its requests by its working directory, split at the round's marks
        model = sorted(load_prices())[0]
        sp = d / "projects2" / "-round"
        (sp / "R1" / "subagents").mkdir(parents=True)

        def rq(i, at, out_, cwd="/work/round"):
            return json.dumps({"type": "assistant", "requestId": f"q{i}", "cwd": cwd, "timestamp": at,
                               "message": {"model": model, "content": [], "usage": {"input_tokens": 0,
                                                                                    "output_tokens": out_}}}) + "\n"
        (sp / "R1.jsonl").write_text(rq(1, "2026-10-18T06:00:00Z", 100000) + rq(2, "2026-10-18T06:40:00Z", 200000)
                                     + rq(3, "2026-10-18T07:50:00Z", 400000), encoding="utf-8")
        (sp / "R1/subagents/rev.jsonl").write_text(rq(4, "2026-10-18T06:50:00Z", 800000), encoding="utf-8")
        # a resumed session: a new transcript that begins with the earlier one's history (the same request ids)
        (sp / "R2.jsonl").write_text(rq(2, "2026-10-18T06:40:00Z", 200000), encoding="utf-8")
        (d / "projects2" / "-other").mkdir()
        (d / "projects2" / "-other" / "X.jsonl").write_text(rq(5, "2026-10-18T06:45:00Z", 5000, cwd="/elsewhere"), encoding="utf-8")
        marks = d / "marks.tsv"
        for at, v in (("2026-10-18T06:30:00Z", "1.21.10"), ("2026-10-18T07:30:00Z", "1.21.11")):
            with contextlib.redirect_stdout(io.StringIO()) as buf_:
                code = mark_main(["--fork", "o/lib", "--version", v, "--file", str(marks), "--at", at])
            text = buf_.getvalue()
            expect(code == 0, f"mark: {text}")
        sess = session_transcripts(d / "projects2", cwd="/work/round")
        rows2 = session_rows(sess, "2026-10-18T05:59:00Z", read_marks(marks))
        by = {(r[0], r[1]): r for r in rows2}
        price = lambda n: estimate_usd({model: collections.Counter(output=n)}, load_prices())[0]
        expect(sorted(sess) == ["R1"] and [r[:2] for r in rows2] == [("(round)", "setup"), ("o/lib", "1.21.10"), ("o/lib", "1.21.11")]
               and by[("o/lib", "1.21.10")][5] == 2 and by[("o/lib", "1.21.11")][5] == 1
               and by[("o/lib", "1.21.10")][4] == 1.0 and by[("(round)", "setup")][4] == 0.52
               and abs(by[("o/lib", "1.21.10")][2] - round(price(1000000), 2)) < 0.011
               and abs(sum(r[2] for r in rows2) - round(price(1500000), 2)) < 0.03,
               f"the session by its cwd, split at the marks (its subagent included, another cwd not): {rows2}")
        with contextlib.redirect_stdout(io.StringIO()) as buf_:
            code = round_table_main(["--since", "2026-10-18T05:59:00Z", "--session-cwd", "/work/round",
                                     "--projects", str(d / "projects2"), "--marks", str(marks)])
        text = buf_.getvalue()
        expect(code == 0 and "| o/lib | 1.21.10 |" in text and "split at 2 mark(s)" in text and "| o/lib | 1.21.10 | $0.00" not in text and "≈ $0.00 |" not in text,
               f"round-table --session-cwd: {text}")
        # --phase (the round's cost split by kind of work): an old three-column marks file renders exactly as before
        old_tab = round_table(rows2)
        u_ = lambda k: f"≈ ${by[k][2]:.2f}"            # (prices from tools/model-prices.tsv; the shape is today's, byte for byte)
        s_, a_, b_ = ("(round)", "setup"), ("o/lib", "1.21.10"), ("o/lib", "1.21.11")
        expect(old_tab == ("| Fork | Version | Cost | Wall time |\n|---|---|---|---|\n"
                           f"| (round) | setup | {u_(s_)} | 0.52 h |\n| **(round)** | **total** | **{u_(s_)}** | **0.52 h** |\n"
                           f"| o/lib | 1.21.10 | {u_(a_)} | 1.00 h |\n| o/lib | 1.21.11 | {u_(b_)} | 0.33 h |\n"
                           f"| **o/lib** | **total** | **≈ ${round(by[a_][2] + by[b_][2], 2):.2f}** | **1.33 h** |\n"
                           f"| **all forks** | | **≈ ${round(by[s_][2] + round(by[a_][2] + by[b_][2], 2), 2):.2f}** | **1.85 h** |\n")
               and all(len(m) == 3 for m in read_marks(marks)),
               f"marks without a phase: today's table, no Phase column: {old_tab}")
        pm = d / "phased.tsv"
        for at, v, ph in (("2026-10-18T06:30:00Z", "1.21.10", "upstream"), ("2026-10-18T06:45:00Z", "1.21.10", "review"),
                          ("2026-10-18T07:00:00Z", "1.21.10", "upstream"), ("2026-10-18T07:30:00Z", "1.21.11", "overhead")):
            with contextlib.redirect_stdout(io.StringIO()):
                expect(mark_main(["--fork", "o/lib", "--version", v, "--phase", ph, "--file", str(pm), "--at", at]) == 0, "mark --phase")
        expect(pm.read_text(encoding="utf-8").splitlines()[0] == "2026-10-18T06:30:00Z\to/lib\t1.21.10\tupstream"
               and marks.read_text(encoding="utf-8").splitlines()[0].count("\t") == 2,
               "mark --phase writes a fourth column; without it the line is as before")
        rows3 = session_rows(sess, "2026-10-18T05:59:00Z", read_marks(pm))
        b3 = {(r[0], r[1], r[6]): r for r in rows3}
        v10 = [r for r in rows3 if r[1] == "1.21.10"]
        expect([r[:2] + (r[6],) for r in rows3] == [("(round)", "setup", ""), ("o/lib", "1.21.10", "upstream"),
                                                     ("o/lib", "1.21.10", "review"), ("o/lib", "1.21.11", "overhead")]
               and b3[("o/lib", "1.21.10", "upstream")][4] == 0.75 and b3[("o/lib", "1.21.10", "review")][4] == 0.25
               and b3[("o/lib", "1.21.10", "upstream")][5] == 1 and b3[("o/lib", "1.21.10", "review")][5] == 1
               and abs(sum(r[2] for r in v10) - by[("o/lib", "1.21.10")][2]) < 0.011
               and round(sum(r[4] for r in v10), 2) == by[("o/lib", "1.21.10")][4]
               and b3[("o/lib", "1.21.11", "overhead")][2] == by[("o/lib", "1.21.11")][2],
               f"phased marks: fork x version x phase, a recurring phase summed, each version's rows summing to its unphased "
               f"row: {rows3}")
        ptab = round_table(rows3)
        expect(ptab.startswith("| Fork | Version | Phase | Cost | Wall time |") and "| o/lib | 1.21.10 | **all phases** |" in ptab
               and "| o/lib | 1.21.10 | upstream |" in ptab and "| Phase | Cost | Wall time |" in ptab
               and "| upstream | **" in ptab and "| review | **" in ptab and "| overhead | **" in ptab and "| - | **" in ptab
               and "| **all forks** | | |" in ptab and "**1.00 h** |" in ptab,
               f"the phased table: per phase, each version's total, per-phase totals: {ptab}")
        expect(session_rows(session_transcripts(session_dir=sp), "2026-10-18T05:59:00Z", [])[0][:2] == ("(round)", "setup"),
               "--session DIR with no marks: one row for the whole round")
        expect(cwd_form("C:\\Work\\Round\\") == "c:/Work/Round" and cwd_form("c:/Work/Round") == "c:/Work/Round"
               and cwd_form("/work/round/") == "/work/round" and cwd_form("\\work\\round") == "/work/round",
               "cwd_form: one form whatever the separators, the drive letter's case, or a drive-less rooted path")
        expect("| o/lib | 1.21.4 | ≈ $1.25 | 0.75 h |" in tab and "| **o/lib** | **total** | **≈ $1.25** | **0.75 h** |" in tab
               and "| **all forks** |" in tab and "an-old-record" not in tab, f"the table: {tab}")
    print("self-check: OK" if ok else "self-check: FAILED")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("modid")
    ap.add_argument("--workspace")
    ap.add_argument("--projects", help="transcript directory (default ~/.claude/projects)")
    ap.add_argument("--print", action="store_true", help="print the record, write nothing")
    ap.add_argument("--since", help="count only requests at or after this time (ISO, e.g. 2026-10-07T18:00 or "
                                    "2026-10-07; UTC unless it says otherwise): one replay, not every session "
                                    "that ever touched the port")
    ap.add_argument("--session", help="count only sessions whose id starts with this")
    ap.add_argument("--path", help="match sessions by this checkout path instead of mods/<modid> (upstream ports)")
    ap.add_argument("--target", help="a fork layer target (.multiversion/targets/<t>): size its overlays and versions")
    ap.add_argument("--label", action="append", default=[], metavar="NAME=PATH",
                    help="split the cost by which path each request worked in, e.g. port=/src/fork tools=/src/migrator")
    a = ap.parse_args()
    ws = pathlib.Path(os.path.expanduser(a.workspace or read_env().get("MIGRATE_WORKSPACE", "") or "."))
    port = pathlib.Path(a.path) if a.path else ws / "mods" / a.modid
    projects = pathlib.Path(a.projects).expanduser() if a.projects else projects_dir()
    if not projects.is_dir():
        print(f"port-cost: no transcripts at {projects} -- nothing recorded", file=sys.stderr)
        return 2
    labels = [tuple(x.split("=", 1)) for x in a.label]
    attr = None
    if a.target and a.path:             # a fork layer target: charge each request to one target at most
        tdir = port / ".multiversion" / "targets"
        known = sorted({a.target} | ({d.name for d in tdir.iterdir() if d.is_dir()} if tdir.is_dir() else set()))
        attr = attribute(os.path.abspath(a.path), known)
    sessions = collect(a.modid, projects, a.path, labels, attr)
    if a.session:
        sessions = {k: v for k, v in sessions.items() if k.startswith(a.session)}
    if a.since:
        since = a.since if ("+" in a.since[10:] or a.since.endswith("Z")) else a.since + ("T00:00" if len(a.since) == 10 else "") + "+00:00"
        cut = ts(since)
        for k in list(sessions):
            v = sessions[k]
            keep = [i for i, r in enumerate(v["reqs"]) if r[0] >= cut]
            v["reqs"] = [v["reqs"][i] for i in keep]
            v["labels"] = [v["labels"][i] for i in keep]
            # a session's recorded total covers ALL of it; with a time cut, price the kept requests instead
            v["cost"] = None
            if not v["reqs"]:
                del sessions[k]
    if not sessions:
        print(f"port-cost: no session in {projects} touched mods/{a.modid} -- nothing recorded", file=sys.stderr)
        return 2
    split = None
    if attr:
        sessions, split = attributed(sessions, "target:" + a.target)
        if not sessions:
            print(f"port-cost: no request since {a.since or 'the first transcript'} worked on target {a.target} "
                  f"(shared {split['shared'][0]}, other targets {split['other'][0]}, elsewhere {split['elsewhere'][0]} "
                  f"request(s)) -- nothing recorded", file=sys.stderr)
            return 2
    c = summarise(a.modid, sessions, port, a.target)
    sec = migration_section(c)
    if split:
        c["attribution"] = split
        sec += attribution_lines(c, split, a.since)
    print(sec, end="")
    if labels:
        print("\nBy where the work happened (label: requests, est. USD):")
        for name in [n for n, _ in labels] + ["other"]:
            part = {k: dict(v, reqs=[r for r, l in zip(v["reqs"], v["labels"]) if l == name], cost=None)
                    for k, v in sessions.items()}
            part = {k: v for k, v in part.items() if v["reqs"]}
            if part:
                s = summarise(a.modid, part, port, a.target)
                print(f"  {name}: {s['totals'].get('requests', 0)} requests, "
                      f"{'$%.2f' % s['usd'] if s['usd'] is not None else 'unpriced'}")
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
    if sys.argv[1:2] == ["round-table"]:
        sys.exit(round_table_main(sys.argv[2:]))
    if sys.argv[1:2] == ["mark"]:
        sys.exit(mark_main(sys.argv[2:]))
    sys.exit(self_check() if "--self-check" in sys.argv[1:] else main())
