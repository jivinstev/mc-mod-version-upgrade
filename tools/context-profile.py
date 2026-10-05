#!/usr/bin/env python3
"""Where a port's tokens went, and what it would have cost with less of it.

    python3 tools/context-profile.py <modid>                       # profile + the standard what-ifs
    python3 tools/context-profile.py <modid> --top 20              # also list the 20 most expensive results
    python3 tools/context-profile.py <modid> --json out.json       # machine-readable, for comparing runs

Reads the same transcripts as tools/port-cost.py (every session whose tool calls touch mods/<modid>, plus
its subagents) and runs no model. It only reads, so it is safe to run on a port at any time.

WHY: a port's cost is mostly CACHE READS -- every request re-sends the whole conversation so far, so a
tool result is paid for again on every request after it. A 40k-token file read near the start of a
300-request session costs ~12M cache-read tokens; the same read near the end costs almost nothing. So
this tool charges each tool result for the requests that CARRIED it ("carried tokens"), which is the
number that says what to cut.

WHAT IT REPORTS
    requests        per model, and the context size of each (floor = first request, median, max)
    by category     every tool result, sized (chars / 4) and charged for the requests after it, grouped:
                    catalogue (CATALOG.md), docs (skills, references, README), source (the port's Java),
                    build (Gradle/javac output), search (Grep/Glob), other
    what-ifs        the port's cost re-computed with: a category removed, every tool result capped at N
                    tokens, and a fraction of the requests removed (what deterministic recipes would do)

LIMITS (stated, not hidden): result sizes are estimated (chars / 4); a large drop in context is treated as
a compaction that stops earlier results being carried; the request-reduction what-if scales every token
type evenly, which is a first-order model. Dollars use tools/model-prices.tsv.
"""
import argparse, collections, importlib.util, json, os, pathlib, re, statistics, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("pc", ROOT / "tools/port-cost.py")
pc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pc)

CATEGORIES = ["catalogue", "docs", "source", "build", "search", "other"]
COMPACTION_DROP = 0.5          # context falling below half of the previous request = compaction


def categorise(name, inp):
    """A tool call -> one of CATEGORIES, from the tool name and its input (never from the result)."""
    inp = inp or {}
    path = str(inp.get("file_path") or inp.get("path") or inp.get("notebook_path") or "")
    cmd = str(inp.get("command") or "")
    blob = path + " " + cmd + " " + str(inp.get("pattern") or "")
    if "CATALOG.md" in blob:
        return "catalogue"
    if name in ("Grep", "Glob", "LS"):
        return "search"
    if name == "Bash" and re.search(r"gradlew|javac|\bgradle\b|burndown-count|quick-typecheck", cmd):
        return "build"
    if re.search(r"\.md\b", path) or re.search(r"\b(cat|head|sed|less)\b[^|]*\.md\b", cmd):
        return "docs"
    if re.search(r"\.(java|json|toml|gradle|mcmeta|properties)\b", path) or \
       re.search(r"\b(cat|head|sed|grep|find)\b.*(src/|decompiled)", cmd):
        return "source"
    return "other"


def result_text(c):
    x = c.get("content")
    if isinstance(x, list):
        return "".join(str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in x)
    return str(x or "")


def session_files(projects, sids):
    out = collections.defaultdict(list)
    for f in projects.rglob("*.jsonl"):
        sid = f.parent.parent.name if f.parent.name == "subagents" else f.stem
        if sid in sids:
            out[sid].append(f)
    return out


def walk(f):
    """One transcript file -> (requests, results) in order. A request is (model, context, usage);
    a result is (category, tokens, label, index of the next request)."""
    uses, reqs, results, seen = {}, [], [], {}
    for line in open(f, errors="replace"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        m = d.get("message") or {}
        if d.get("type") == "assistant":
            for c in m.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    uses[c.get("id")] = (c.get("name"), c.get("input"))
            rid = d.get("requestId") or d.get("uuid")
            if rid in seen or m.get("model") == "<synthetic>":
                continue
            u = m.get("usage") or {}
            seen[rid] = True
            ctx = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
            reqs.append((m.get("model", "?"), ctx, u))
        elif d.get("type") == "user":
            for c in m.get("content") or [] if isinstance(m.get("content"), list) else []:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    name, inp = uses.get(c.get("tool_use_id"), ("?", {}))
                    label = str((inp or {}).get("file_path") or (inp or {}).get("command") or (inp or {}).get("pattern") or name)
                    results.append((categorise(name, inp), len(result_text(c)) // 4, f"{name}: {label[:90]}", len(reqs)))
    return reqs, results


def profile(modid, projects):
    sessions = pc.collect(modid, projects)
    if not sessions:
        return None
    reqs_all, by_cat, items = [], collections.Counter(), []
    carried_cat, sizes_cat = collections.Counter(), collections.Counter()
    for sid, files in session_files(projects, set(sessions)).items():
        for f in files:
            reqs, results = walk(f)
            reqs_all += reqs
            ctx = [r[1] for r in reqs]
            # a compaction (context drops sharply) ends the carrying of everything before it
            cut = [i for i in range(1, len(ctx)) if ctx[i] < COMPACTION_DROP * ctx[i - 1]]
            for cat, tok, label, start in results:
                end = next((i for i in cut if i > start), len(reqs))
                carried = tok * max(0, end - start)
                carried_cat[cat] += carried
                sizes_cat[cat] += tok
                by_cat[cat] += 1
                items.append((carried, tok, cat, label))
    return dict(sessions=len(sessions), reqs=reqs_all, carried=carried_cat, sizes=sizes_cat,
                counts=by_cat, items=sorted(items, reverse=True))


def per_model(reqs, scale=1.0, minus_cache_read=0):
    """Token totals per model (the shape port-cost.estimate_usd wants), optionally scaled/reduced."""
    out = collections.defaultdict(collections.Counter)
    for model, _, u in reqs:
        cc = u.get("cache_creation") or {}
        c = out[model]
        c["input"] += u.get("input_tokens", 0) * scale
        c["output"] += u.get("output_tokens", 0) * scale
        c["cache_read"] += u.get("cache_read_input_tokens", 0) * scale
        c["cache_write"] += u.get("cache_creation_input_tokens", 0) * scale
        c["cache_write_5m"] += cc.get("ephemeral_5m_input_tokens", 0) * scale
        c["cache_write_1h"] += cc.get("ephemeral_1h_input_tokens", 0) * scale
    if minus_cache_read and out:
        total = sum(c["cache_read"] for c in out.values()) or 1
        for c in out.values():
            c["cache_read"] = max(0, c["cache_read"] - minus_cache_read * c["cache_read"] / total)
    return out


def usd(pm, prices):
    v, _ = pc.estimate_usd(pm, prices)
    return v


def whatifs(p, prices, caps=(8000, 2000), fractions=(0.5, 0.9)):
    base = usd(per_model(p["reqs"]), prices)
    rows = [("as run", base)]
    for cat in CATEGORIES:
        if p["carried"][cat]:
            rows.append((f"without {cat} results", usd(per_model(p["reqs"], minus_cache_read=p["carried"][cat]), prices)))
    for cap in caps:
        saved = sum(carried * (1 - cap / tok) for carried, tok, _, _ in p["items"] if tok > cap)
        rows.append((f"every tool result capped at {cap:,} tokens", usd(per_model(p["reqs"], minus_cache_read=saved), prices)))
    floor_ctx = p["reqs"][0][1]
    for target in (100_000, 40_000):
        if floor_ctx > target:
            cut = (floor_ctx - target) * len(p["reqs"])
            rows.append((f"starting context {target // 1000}k instead of {floor_ctx // 1000}k",
                         usd(per_model(p["reqs"], minus_cache_read=cut), prices)))
    for fr in fractions:
        rows.append((f"{int(fr * 100)}% fewer requests", usd(per_model(p["reqs"], scale=1 - fr), prices)))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("modid")
    ap.add_argument("--projects", help="transcript directory (default ~/.claude/projects)")
    ap.add_argument("--top", type=int, default=10, help="list the N most expensive tool results (default 10)")
    ap.add_argument("--json", help="also write the profile here")
    a = ap.parse_args()
    projects = pathlib.Path(a.projects).expanduser() if a.projects else pc.projects_dir()
    if not projects.is_dir():
        print(f"context-profile: no transcripts at {projects}", file=sys.stderr)
        return 2
    p = profile(a.modid, projects)
    if not p or not p["reqs"]:
        print(f"context-profile: no session in {projects} touched mods/{a.modid}", file=sys.stderr)
        return 2
    prices = pc.load_prices()
    ctx = [r[1] for r in p["reqs"]]
    total_read = sum(r[2].get("cache_read_input_tokens", 0) for r in p["reqs"])
    print(f"context-profile: {a.modid} -- {p['sessions']} session(s), {len(ctx)} requests, "
          f"{', '.join(sorted({r[0] for r in p['reqs']}))}")
    print(f"  context per request: first {ctx[0]:,}, median {int(statistics.median(ctx)):,}, max {max(ctx):,} tokens")
    floor = min(ctx[0] * len(ctx), total_read)
    print(f"  FLOOR: the first request's context ({ctx[0]:,}: system prompt, tools, CLAUDE.md, skills) is re-read by "
          f"every request -- about {floor:,} tokens, {100 * floor / max(total_read, 1):.0f}% of all cache reads")
    print(f"  cache reads {total_read:,} tokens; the tool results below account for "
          f"{sum(p['carried'].values()):,} of them (the rest is the system prompt, tools and the conversation)")
    print("\n  category    results   size (tok)   carried (tok)   share of cache reads")
    for cat in CATEGORIES:
        if p["counts"][cat]:
            print(f"  {cat:<10}{p['counts'][cat]:>9}{p['sizes'][cat]:>13,}{p['carried'][cat]:>16,}"
                  f"{100 * p['carried'][cat] / max(total_read, 1):>14.0f}%")
    print(f"\n  most expensive tool results (carried tokens):")
    for carried, tok, cat, label in p["items"][:a.top]:
        print(f"  {carried:>13,}  {tok:>7,} tok  {cat:<9} {label}")
    rows = whatifs(p, prices)
    print("\n  what-if (dollars at tools/model-prices.tsv list prices)")
    base = rows[0][1]
    for name, v in rows:
        if v is None:
            print(f"  {name:<44} (unpriced model)")
        else:
            print(f"  {name:<44} ${v:>8.2f}" + (f"   {base / v:>5.1f}x cheaper" if base and v and name != 'as run' else ""))
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(dict(
            modid=a.modid, requests=len(ctx), context=dict(first=ctx[0], median=statistics.median(ctx), max=max(ctx)),
            cache_read=total_read, categories={c: dict(results=p["counts"][c], size=p["sizes"][c], carried=p["carried"][c])
                                               for c in CATEGORIES},
            top=[dict(carried=c, size=t, category=k, label=l) for c, t, k, l in p["items"][:a.top]],
            whatifs=[dict(scenario=n, usd=v) for n, v in rows]), indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
