#!/usr/bin/env python3
"""The handoff page: one section per ported fork, built from each port's OFFER.md + manual-tests.json.

    python3 tools/offer-page.py --mods mods.json --out page.html [--offline]
    python3 tools/offer-page.py --self-check

mods.json is a list of {"work": <dir with OFFER.md, as port-offer.py writes it>, "fork": "owner/repo",
"tag": <release tag or null>, "pending": <note or null>, "label": <optional, e.g. "release build">}; a relative "work" is under ~/.mc-mod-upgrade/upstream.
Each section: links, install steps, size, verified checks, the TOP-10 MANUAL TESTS with full steps (what no gate
reaches -- tools/manual-tests.py), then the draft words. Publish the result as an Artifact for the person handing
the ports over. --offline skips the release-asset lookup (GitHub API).
"""
import argparse, html, json, pathlib, re, sys, tempfile, urllib.request
U = pathlib.Path.home() / ".mc-mod-upgrade/upstream"
def sec(md, name):
    m = re.search(r"^## " + re.escape(name) + r"\n\n(.*?)(?=^## |\Z)", md, re.S | re.M)
    return m.group(1).strip() if m else ""
def link(u, t=None): return f'<a href="{html.escape(u)}">{html.escape(t or u)}</a>'
def linkify(t):
    return re.sub(r"https://\S+?(?=[)\s]|$)", lambda m: link(m.group(0)), html.escape(t))
def md_inline(t):
    t = html.escape(t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
def md_block(md):
    out, mode = [], None
    for line in md.splitlines():
        m = re.match(r"^(\d+)\. (.*)$", line) or None
        b = re.match(r"^- (.*)$", line)
        want = "ol" if m else "ul" if b else None
        if want != mode:
            if mode: out.append(f"</{mode}>")
            if want: out.append(f'<{want} class="install">')
            mode = want
        if m: out.append(f"<li>{md_inline(m.group(2))}</li>")
        elif b: out.append(f"<li>{md_inline(b.group(1))}</li>")
        elif line.strip(): out.append(f"<p>{md_inline(line)}</p>")
    if mode: out.append(f"</{mode}>")
    return "".join(out)
def copy(id_, text, label):
    return (f'<div class="copy"><div class="copy-head"><span>{label}</span>'
            f'<button type="button" data-copy="{id_}">Copy</button></div>'
            f'<pre id="{id_}">{html.escape(text)}</pre></div>')


def build(mods, out, offline=False):
    cards = []
    for i, m in enumerate(mods):
        wd, fork, tag, pending = m["work"], m["fork"], m.get("tag"), m.get("pending")
        f = (U / wd) / "OFFER.md"
        if pending or not f.exists():
            cards.append(f'<section class="mod pending"><h2>{fork.split("/")[1]}</h2><p class="status">Pending</p>'
                         f'<p>{html.escape(pending or "No offer yet.")}</p></section>'); continue
        md = f.read_text(encoding="utf-8")
        name = re.match(r"# Offering the (.+?) port", md).group(1) + (f" — {m['label']}" if m.get("label") else "")
        links = dict(re.findall(r"^- \*?\*?(.+?)\*?\*?[^:]*?: (https://\S+)", sec(md, "Links"), re.M))
        lk = re.findall(r"https://\S+", sec(md, "Links"))
        diff, branch, pr = lk[0], lk[1], lk[2]
        rel = {"html_url": f"https://github.com/{fork}/releases/tag/{tag}", "assets": []}
        if tag and not offline:
            rel = json.loads(urllib.request.urlopen(urllib.request.Request(
                f"https://api.github.com/repos/{fork}/releases/tags/{tag}", headers={"User-Agent": "x"}), timeout=20).read())
        assets = "".join(f"<li>{link(a['browser_download_url'], a['name'])}</li>" for a in rel["assets"])
        mt = (U / wd) / "manual-tests.json"
        tests = json.loads(mt.read_text(encoding="utf-8")) if mt.exists() else []
        manual = "".join(
            f'<details class="test"><summary><span class="n">{n}</span> {html.escape(t["title"])} '
            f'<span class="cat">{html.escape(t["category"])}</span></summary>'
            + (f'<p class="warn"><strong>Warning:</strong> {md_inline(t["warning"])}</p>' if t.get("warning") else "")
            + f'<p class="muted">{md_inline(t["why"])}</p><ol class="install">'
            + "".join(f"<li>{md_inline(s)}</li>" for s in t["steps"])
            + f'</ol><p><strong>Expect:</strong> {md_inline(t["expect"])}</p><p class="muted"><code>{html.escape(t["where"])}</code></p></details>'
            for n, t in enumerate(tests, 1))
        if tests:
            manual += f'<details class="test"><summary>If a test fails</summary>{md_block(sec(md, "Manual tests (the " + str(len(tests)) + " highest-value)").split("### If a test fails", 1)[-1])}</details>' 
        size = sec(md, "Size of the change")
        sizeline = size.split("\n")[0]
        commits = re.findall(r"^- `(\w+)` (.+)$", size, re.M)
        left = re.search(r"Left out of the offer: (.+)", size)
        ver = [l[2:] for l in sec(md, "Verified").splitlines() if l.startswith("- ")]
        msg = sec(md, "Draft message to the authors")
        prd = sec(md, "Draft PR description (only if they ask for a PR)")
        cards.append(f'''<section class="mod" id="m{i}">
      <h2>{html.escape(name)}</h2>
      <p class="status ok">Released · CI green</p>
      <dl class="links">
        <dt>Smoke test on your Mac</dt><dd>{link(rel["html_url"], tag)}<ul class="assets">{assets}</ul></dd>
        <dt>The diff to send</dt><dd>{link(diff)}</dd>
        <dt>Branch as authors would clone it</dt><dd>{link(branch)}</dd>
        <dt>Propose a PR (opens a form, sends nothing)</dt><dd>{link(pr, "Pre-filled PR on the authors' repository")}</dd>
      </dl>
      <h3>How to install (what CI tested)</h3>{md_block(sec(md, "How to install"))}
      {(lambda pv: f'<h3>Distance from the author&#39;s release</h3>{md_block(pv)}' if pv else "")(sec(md, "Distance from the author's release"))}
  <h3>Size</h3><p>{html.escape(sizeline)}</p>
      <ul class="commits">{"".join(f"<li><code>{s}</code> {html.escape(t)}</li>" for s, t in commits)}</ul>
      {f'<p class="muted">{html.escape("Left out: " + left.group(1))}</p>' if left else ""}
      <h3>Verified</h3><ul class="checks">{"".join(f"<li>{linkify(v)}</li>" for v in ver)}</ul>
      {f'<h3>Manual tests -- the {len(tests)} that matter most</h3><p class="muted">What no automated gate reaches. A few minutes each.</p>{manual}' if tests else ""}
      <h3>Words</h3>
      {copy(f"msg{i}", msg, "Message to the authors")}
      {copy(f"pr{i}", prd, "PR description, only if they ask")}
    </section>''')
    out.write_text(f'''<title>Fork Port Offers</title>
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@400;600&display=swap">
    <style>
    /* layout: one narrow reading column; each fork is a section with its links first, words last */
    :root {{ --bg:#f7f8f6; --surface:#ffffff; --fg:#1d2321; --muted:#5d6764; --line:#d9dedb; --accent:#2f6b4f;
      --ok:#2f6b4f; --pend:#9a6b15; --code:#eef1ef;
      --sans:"IBM Plex Sans", system-ui, sans-serif; --mono:"IBM Plex Mono", ui-monospace, monospace; }}
    @media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#151a18; --surface:#1d2421; --fg:#e4e9e6;
      --muted:#9aa6a1; --line:#33403b; --accent:#7cc3a0; --ok:#7cc3a0; --pend:#e0b25c; --code:#26302c; color-scheme:dark }} }}
    :root[data-theme="dark"] {{ --bg:#151a18; --surface:#1d2421; --fg:#e4e9e6; --muted:#9aa6a1; --line:#33403b;
      --accent:#7cc3a0; --ok:#7cc3a0; --pend:#e0b25c; --code:#26302c; color-scheme:dark }}
    body {{ background:var(--bg); color:var(--fg); font:15px/1.55 var(--sans); padding-inline:16px; padding-block:28px 48px }}
    main {{ max-width:760px; margin:0 auto; display:grid; gap:28px }}
    h1 {{ font-size:26px; margin:0; text-wrap:balance }} .lede {{ color:var(--muted); margin:6px 0 0 }}
    .mod {{ background:var(--surface); border:1px solid var(--line); border-radius:8px; padding:20px; min-width:0 }}
    .mod h2 {{ margin:0; font-size:20px }} h3 {{ font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:22px 0 6px }}
    .status {{ font:600 12px var(--mono); margin:4px 0 14px; color:var(--pend) }} .status.ok {{ color:var(--ok) }}
    .links {{ display:grid; grid-template-columns:minmax(0,13em) minmax(0,1fr); gap:8px 16px; margin:0 }}
    .links dt {{ color:var(--muted); font-size:13px }} .links dd {{ margin:0; min-width:0; overflow-wrap:anywhere }}
    @media (max-width:560px) {{ .links {{ grid-template-columns:1fr }} .links dd {{ margin-bottom:6px }} }}
    a {{ color:var(--accent) }} a:focus-visible, button:focus-visible {{ outline:2px solid var(--accent); outline-offset:2px }}
    .assets {{ margin:4px 0 0; padding-left:18px; font:13px var(--mono) }}
    .commits, .checks {{ margin:0; padding-left:18px }} .commits li, .checks li {{ overflow-wrap:anywhere }}
    code {{ font:12.5px var(--mono); background:var(--code); padding:1px 4px; border-radius:3px }} .muted {{ color:var(--muted); font-size:13px }}
    .warn {{ border-left:3px solid var(--pend); padding:6px 10px; margin:8px 0; background:var(--code) }}
    .copy {{ border:1px solid var(--line); border-radius:6px; margin-top:10px; overflow:hidden }}
    .copy-head {{ display:flex; justify-content:space-between; align-items:center; padding:6px 10px; background:var(--code); font-size:13px }}
    .copy button {{ font:600 12px var(--sans); color:var(--fg); background:var(--surface); border:1px solid var(--line); border-radius:4px; padding:3px 10px; cursor:pointer }}
    pre {{ margin:0; padding:12px; white-space:pre-wrap; overflow-wrap:anywhere; font:13px/1.5 var(--mono) }}
    .pending {{ border-style:dashed }}
    .install {{ margin:6px 0; padding-left:20px }} .install li {{ overflow-wrap:anywhere; margin:2px 0 }}
    .test {{ border:1px solid var(--line); border-radius:6px; padding:8px 12px; margin-top:8px }}
    .test summary {{ cursor:pointer; font-weight:600; overflow-wrap:anywhere }} .test .n {{ font:600 12px var(--mono); color:var(--muted) }}
    .test .cat {{ font:12px var(--mono); color:var(--muted); font-weight:400 }}
    </style>
    <main>
      <header><h1>Fork Port Offers</h1>
      <p class="lede">NeoForge 1.21.1 ports of MIT-licensed mods, one section per fork. Smoke-test the release first, then send the diff link if you want. Nothing here has been sent.</p></header>
      {"".join(cards)}
    </main>
    <script>
    document.querySelectorAll("[data-copy]").forEach(b => b.addEventListener("click", () => {{
      const el = document.getElementById(b.dataset.copy);
      const done = () => {{ b.textContent = "Copied"; setTimeout(() => b.textContent = "Copy", 1500); }};
      const pick = () => {{ const r = document.createRange(); r.selectNodeContents(el); const s = getSelection(); s.removeAllRanges(); s.addRange(r); b.textContent = "Selected"; }};
      try {{ navigator.clipboard.writeText(el.textContent).then(done, pick); }} catch (e) {{ pick(); }}
    }}));
    </script>
    ''', encoding="utf-8")
    return len(cards)


def self_check():
    global U
    with tempfile.TemporaryDirectory() as d:
        U = pathlib.Path(d)
        w = U / "ex"; w.mkdir()
        (w / "OFFER.md").write_text("# Offering the Example port to its authors\n\n## Links\n\n- **The diff** x: https://a/1\n"
            "- branch: https://a/2\n- PR: https://a/3\n\n## Size of the change\n\n1 of 2 files changed.\n\n- `abc` Port\n\n"
            "## Verified\n\n- Gate B passed\n\n## Manual tests (the 1 highest-value)\n\n### 1. X\n\n### If a test fails\n\n"
            "1. Note it.\n\n## Draft message to the authors\n\nHi\n\n## Draft PR description (only if they ask for a PR)\n\nPorts\n",
            encoding="utf-8")
        (w / "manual-tests.json").write_text(json.dumps([{"category": "music", "title": "Music: theme", "why": "ear",
            "steps": ["set **Music** up", "`/summon ex:boss`"], "expect": "it plays", "where": "a.java"}]), encoding="utf-8")
        out = U / "p.html"
        n = build([{"work": "ex", "fork": "o/r", "tag": "t1"}, {"work": "none", "fork": "o/q", "pending": "later"}], out, True)
        page = out.read_text(encoding="utf-8")
        ok = n == 2 and "Music: theme" in page and "<code>/summon ex:boss</code>" in page and "If a test fails" in page \
            and "Note it." in page and "Pending" in page and "Gate B passed" in page
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mods"); ap.add_argument("--out"); ap.add_argument("--offline", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not (a.mods and a.out):
        ap.error("--mods and --out are required")
    n = build(json.loads(pathlib.Path(a.mods).read_text(encoding="utf-8")), pathlib.Path(a.out), a.offline)
    print(f"offer-page: {n} section(s) -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
