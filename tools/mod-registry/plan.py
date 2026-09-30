#!/usr/bin/env python3
"""
Plan-artifact helper for the install-mod pipeline.

The resumable state of an install lives in `installs/<slug>/plan.json` (the machine
source of truth). This tool keeps a human-readable `PLAN.md` mirror in sync so the
two never drift, and can scaffold a fresh plan. The install-mod skill edits plan.json
(it's plain JSON the agent can read/write) and calls `plan.py render` after each change.

Usage:
  python3 plan.py init   --dir installs/<slug> --request "<text>" [--loader neoforge --mc 1.21.1]
  python3 plan.py render --dir installs/<slug>          # regenerate PLAN.md from plan.json
  python3 plan.py show   --dir installs/<slug>          # print the next actionable node

plan.json schema (see references/resolution.md for the authoritative copy):
  {
    "request": str, "created": str|null, "updated": str|null,
    "target": {"loader": str, "mcVersion": str},
    "nodes": [ {
      "id": str,                 # stable slug / modid
      "role": "root"|"dependency",
      "requiredBy": [str],       # parent node ids
      "selection": {"provider","projectId","fileId","fileName","sha1","srcLoader","srcMC"},
      "compat": "native-1.21.1"|"needs-migrate"|"needs-downport"|"present-in-instance"|"awaiting-user",
      "jarPath": str|null,
      "migrate": {"needed":bool,"src":[loader,mc],"dst":[loader,mc],"workspace":str|null,
                  "status":"pending|in-progress|built|deployed","outputJar":str|null},
      "install": {"status":"pending|deployed","deployedPath":str|null},
      "test": {"headless":"pending|pass|fail","client":"pending|pass|fail|skipped"},
      "status": "resolved|downloaded|migrating|migrated|installed|tested|done|blocked|awaiting-user",
      "notes": str
    } ],
    "questions": [ {"nodeId","kind","prompt","answer"} ]
  }
"""
import argparse
import json
import os
import sys

STATUS_ORDER = ["awaiting-user", "resolved", "downloaded", "migrating", "migrated",
                "installed", "tested", "done", "blocked"]


def _plan_path(d):
    return os.path.join(d, "plan.json")


def load(d):
    with open(_plan_path(d), "r", encoding="utf-8") as f:
        return json.load(f)


def save(d, plan):
    os.makedirs(d, exist_ok=True)
    with open(_plan_path(d), "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2)
        f.write("\n")


def cmd_init(args):
    plan = {
        "request": args.request,
        "created": args.now, "updated": args.now,
        "target": {"loader": args.loader, "mcVersion": args.mc},
        "nodes": [], "questions": [],
    }
    save(args.dir, plan)
    render(args.dir, plan)
    print(f"initialized {_plan_path(args.dir)}")


def _tree_lines(plan):
    """Render the dependency graph as an indented tree from roots down."""
    nodes = {n["id"]: n for n in plan["nodes"]}
    children = {}
    roots = []
    for n in plan["nodes"]:
        if n.get("role") == "root" or not n.get("requiredBy"):
            roots.append(n["id"])
        for parent in n.get("requiredBy", []):
            children.setdefault(parent, []).append(n["id"])
    lines = []
    seen = set()

    def walk(nid, depth):
        if nid in seen:
            lines.append("  " * depth + f"- {nid} (…already shown)")
            return
        seen.add(nid)
        n = nodes.get(nid, {"id": nid, "compat": "?", "status": "?"})
        badge = f"[{n.get('compat','?')} / {n.get('status','?')}]"
        lines.append("  " * depth + f"- **{nid}** {badge}")
        for c in children.get(nid, []):
            walk(c, depth + 1)

    for r in roots:
        walk(r, 0)
    return lines or ["- (no nodes yet)"]


def render(d, plan=None):
    plan = plan or load(d)
    t = plan["target"]
    L = []
    L.append(f"# Install plan — {plan.get('request','(request)')}")
    L.append("")
    L.append(f"**Target:** {t['loader']} {t['mcVersion']}  ·  **updated:** {plan.get('updated','?')}")
    L.append("")
    pending_q = [q for q in plan.get("questions", []) if q.get("answer") in (None, "")]
    if pending_q:
        L.append("## ⚠️ Pending questions (blocking)")
        for q in pending_q:
            L.append(f"- **[{q.get('kind')}]** ({q.get('nodeId')}) — {q.get('prompt')}")
        L.append("")
    L.append("## Dependency tree")
    L.extend(_tree_lines(plan))
    L.append("")
    L.append("## Nodes — stage checklist")
    L.append("")
    L.append("| node | compat | download | migrate | install | headless | client | status |")
    L.append("|------|--------|----------|---------|---------|----------|--------|--------|")
    for n in plan["nodes"]:
        mig = n.get("migrate", {})
        migcell = "n/a" if not mig.get("needed") else mig.get("status", "pending")
        dl = "ok" if n.get("jarPath") else ("skip" if n.get("compat") == "present-in-instance" else "pending")
        tst = n.get("test", {})
        L.append("| {id} | {compat} | {dl} | {mig} | {inst} | {hl} | {cl} | {st} |".format(
            id=n["id"], compat=n.get("compat", "?"), dl=dl, mig=migcell,
            inst=n.get("install", {}).get("status", "pending"),
            hl=tst.get("headless", "pending"), cl=tst.get("client", "pending"),
            st=n.get("status", "?")))
    L.append("")
    with open(os.path.join(d, "PLAN.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


def cmd_render(args):
    render(args.dir)
    print(f"rendered {os.path.join(args.dir, 'PLAN.md')}")


def cmd_show(args):
    plan = load(args.dir)
    pending_q = [q for q in plan.get("questions", []) if q.get("answer") in (None, "")]
    if pending_q:
        print(json.dumps({"blocked_on_questions": pending_q}, indent=2))
        return
    for status in STATUS_ORDER:
        if status in ("done", "blocked"):
            continue
        nxt = [n for n in plan["nodes"] if n.get("status") == status]
        if nxt:
            print(json.dumps({"next": nxt[0]}, indent=2))
            return
    print(json.dumps({"next": None, "note": "all nodes done or blocked"}, indent=2))


def main():
    ap = argparse.ArgumentParser(prog="plan")
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init")
    i.add_argument("--dir", required=True)
    i.add_argument("--request", required=True)
    i.add_argument("--loader", default="neoforge")
    i.add_argument("--mc", default="1.21.1")
    i.add_argument("--now", default=None, help="ISO timestamp (caller supplies; scripts have no clock)")
    i.set_defaults(func=cmd_init)

    r = sub.add_parser("render")
    r.add_argument("--dir", required=True)
    r.set_defaults(func=cmd_render)

    s = sub.add_parser("show")
    s.add_argument("--dir", required=True)
    s.set_defaults(func=cmd_show)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
