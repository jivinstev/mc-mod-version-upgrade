#!/usr/bin/env python3
"""Record what a port cost: model, effort, every token type, dollars, time, and the port's size.

    python3 tools/port-cost.py <modid>              # writes mods/<modid>/COST.json + MIGRATION.md "## Cost"
    python3 tools/port-cost.py <modid> --print      # show it, write nothing
    python3 tools/port-cost.py --self-check         # fixture transcripts: attribution and the default path

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
    here_rx = re.compile("|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True)) + r"(?![\w.-])")
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
        "models": {k: dict(v) for k, v in sorted(per_model.items())},
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
    sys.exit(self_check() if "--self-check" in sys.argv[1:] else main())
