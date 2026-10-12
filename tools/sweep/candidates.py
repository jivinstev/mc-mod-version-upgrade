#!/usr/bin/env python3
"""Draw a sweep's mod list from Modrinth, most-downloaded first, applying the October 2026 selection rules
(docs/POPULARITY_SWEEP.md):

  * Forge 1.20.1 -> NeoForge 1.21.1: a mod with a Forge 1.20.1 build and NO NeoForge 1.21.1 build;
  * NeoForge 1.21.1 -> 26.2: a mod with a NeoForge 1.21.1 build and NO build for any 26.x at all (a mod already on
    26.x is maintained by its authors and is not a porting candidate).

    python3 tools/sweep/candidates.py [--per-target 20] [--offset 0] [--skip seen.txt] > batch.args

Prints `Title|target` lines, the two targets interleaved, for tools/sweep/sweep.py. --skip names slugs (one per
line) already swept or already ported. Library mods are kept: they are often what a dependent port waits on.
Standard library only; needs network access to api.modrinth.com.
"""
import argparse, json, sys, time, urllib.parse, urllib.request

API = "https://api.modrinth.com/v2"
UA = {"User-Agent": "mc-mod-version-upgrade/sweep-candidates"}


def get(path, **q):
    url = API + path + ("?" + urllib.parse.urlencode({k: (v if isinstance(v, (str, int)) else json.dumps(v)) for k, v in q.items()}) if q else "")
    for t in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.load(r)
        except Exception as e:     # rate limits and transient failures: back off and retry
            err = e
            time.sleep(2 * (t + 1))
    raise err


def versions(pid, loader):
    return {g for v in get(f"/project/{pid}/version", loaders=[loader]) for g in v.get("game_versions", [])}


HOPS = (("forge", "1.20.1", "1.21.1", lambda gv: "1.21.1" in gv, "neoforge"),
        ("neoforge", "1.21.1", "26.2", lambda gv: any(g.startswith("26.") for g in gv), "neoforge"))


def candidates(loader, src, tgt, has_target, tgt_loader, want, offset, skip):
    out, off = [], offset
    while len(out) < want:
        page = get("/search", facets=[[f"categories:{loader}"], [f"versions:{src}"], ["project_type:mod"]],
                   index="downloads", offset=off, limit=100)
        hits = page.get("hits", [])
        if not hits:
            break
        for h in hits:
            if h["slug"] in skip:
                continue
            if has_target(versions(h["project_id"], tgt_loader)):
                continue
            out.append(f"{h['title']}|{tgt}")
            if len(out) >= want:
                break
        off += 100
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--per-target", type=int, default=20); ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--skip", help="file of slugs to leave out")
    a = ap.parse_args()
    skip = set(open(a.skip, encoding="utf-8").read().split()) if a.skip else set()
    lists = [candidates(*h, a.per_target, a.offset, skip) for h in HOPS]
    for pair in zip(*lists):
        print("\n".join(pair))
    for rest in (lists[0][len(lists[1]):], lists[1][len(lists[0]):]):
        if rest:
            print("\n".join(rest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
