#!/usr/bin/env python3
"""Turn a migration's lessons into a pull request against this repository's catalogue.

    python3 tools/propose-learnings.py --modid <modid>              apply on a branch, run the gates, commit
    python3 tools/propose-learnings.py --modid <modid> --push       ... push, and open the PR if `gh` is here
    python3 tools/propose-learnings.py --modid <modid> --dry-run    show the catalogue diff, change nothing

THE LOOP THIS CLOSES
    Every port teaches something.  The migrate-mod skill's retrospective (Step 4b / Step 6) writes those
    lessons to $MIGRATE_WORKSPACE/catalog-additions.md.  This tool moves ONLY the lesson into CATALOG.md
    -- never the mod's code, name, or anything from the workspace -- and proposes it for review.

THE ADDITIONS FILE -- one block per lesson, each opened by a `### ` line:

    ### new R
    R99. **Short title** · **Pattern:** <old code shape> · **Runtime:** <crash / log line> · **Fix:** <the fix>

    ### augment M6
    · **AUGMENT — <what was missing>:** <the extra case, stated by its symptom>

    `new <SECTION>` appends to the end of that catalogue section (a letter such as `R`, or the start of
    the heading title, e.g. `N. Re-porting`, where a letter is used twice).  A new entry must carry
    **Pattern:**, **Fix:** and one of **Error:** / **Runtime:** / **Symptom:** -- that is what makes it
    findable from the symptom a porter is actually looking at.
    `augment <ENTRY-ID>` appends to an existing entry; the text must start `· **AUGMENT`.

IDENTITY IS REFUSED, NOT TRUSTED
    The public name gate knows nothing about the mod YOU just ported, so this tool learns it from the
    workspace (mod id, display name, Java package, jar name, the workspace path) and refuses additions
    that contain any of them.  Describe the mod ("a small MCreator food mod"), never name it.

GATES (the same ones a reviewer runs): check-no-ip.py --strict, check-catalog-fidelity.py.

FROM A WEEKLY ROUND (a worklist, never a writer)
    python3 tools/propose-learnings.py --from-plan owner/repo#N [--comments-file FILE] [--out FILE]

    The keep-current round cannot write this repository, so its closing comment on the plan issue carries a
    `### Lessons for the migrator` table: | version | symptom | fork commit | why it looks general |.  This reads the
    newest comment of issue N that has that block (GitHub as known-issues.py reads it: GITHUB_TOKEN, GH_TOKEN, gh,
    then anonymous REST; or a saved `gh api .../comments` JSON with --comments-file) and prints one checklist item
    per row: the evidence link (https://github.com/<owner/repo>/commit/<sha>), the catalogue entries its symptom
    already matches (an AUGMENT candidate) or none (a new entry), and a warning when the row names a mod from the
    FORBIDDEN_NAMES_FILE list.  It changes nothing here: the curator verifies each row from the diff and the
    version's sources, writes the lesson in placeholder code as `### new` / `### augment` blocks, and applies them
    (the --modid flow above, or by hand).  --out writes the checklist to a file outside this checkout.
    Exit codes: 0 worklist printed (also when every row is `none`)  1 no Lessons block on the issue  2 could not run.

Standard library only.  Exit codes: 0 done  1 refused (gate or format)  2 could not run.
"""
import argparse, json, datetime, os, pathlib, re, shutil, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
CATALOG = ROOT / "CATALOG.md"


def read_env(path):
    env = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def git(*args, check=True):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=check, encoding="utf-8", errors="replace")


def parse_additions(text):
    blocks, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^###\s+(new|augment)\s+(.+?)\s*$", line)
        if m:
            cur = {"kind": m.group(1), "key": m.group(2), "body": []}
            blocks.append(cur)
        elif cur is not None:
            cur["body"].append(line)
        elif line.strip() and not line.startswith("#") and not line.startswith("<!--"):
            raise ValueError(f"text before the first `### new|augment` line: {line[:60]!r}")
    for b in blocks:
        b["body"] = "\n".join(b["body"]).strip("\n")
        if not b["body"].strip():
            raise ValueError(f"`### {b['kind']} {b['key']}` has no text")
        if b["kind"] == "new":
            labels = entry_labels(b["body"])
            has = lambda w: w in labels
            missing = [f"**{k}:**" for k in ("Pattern", "Fix") if not has(k)]
            if not any(has(k) for k in ("Error", "Runtime", "Symptom")):
                missing.append("**Error:** / **Runtime:** / **Symptom:**")
            if missing:
                raise ValueError(f"new entry under {b['key']} lacks {', '.join(missing)}")
        elif not b["body"].lstrip().startswith("· **AUGMENT"):
            raise ValueError(f"augment {b['key']} must start with `· **AUGMENT`")
    if not blocks:
        raise ValueError("no `### new <SECTION>` or `### augment <ENTRY-ID>` blocks found")
    return blocks


HEADING = re.compile(r"^(#{2,3})\s")


def entry_labels(body):
    """The field labels an entry uses. Accepts every shape existing entries use:
    **Pattern:**, a qualified **Pattern (1.21.2+):**, and a combined **Pattern → Error → Fix:**
    (one bold span naming several fields). A title such as **Pattern matching breaks** is not a label."""
    out = set()
    # a span must START with a label word: pairing every `**` breaks on a title that contains `*`
    for span in re.findall(r"\*\*(?=(?:Pattern|Error|Runtime|Symptom|Fix)\b)([^*\n]{1,120}?)\*\*", body):
        span = re.sub(r"\s*\([^)]*\)", "", span).strip().rstrip(":").strip()
        parts = [p.strip().rstrip(":").strip() for p in re.split(r"\s*(?:→|->)\s*", span)]
        if len(parts) > 1 or span != span.rstrip(":") or parts[0] in ("Pattern", "Fix", "Error", "Runtime", "Symptom"):
            out.update(parts)
    return out


def section_span(lines, key):
    """-> (start, end) line indexes of the `## <key>` section; end = next level-2/3 heading."""
    # a bare section letter must match `## D.` only -- `startswith("D")` also matched `## Deeper clusters`
    hits = [i for i, l in enumerate(lines) if l.startswith("## ") and l[3:].startswith(key + ".")]
    if not hits:   # otherwise the key is the start of a title (`§A augment — MCreator`)
        hits = [i for i, l in enumerate(lines)
                if l.startswith("## ") and l[3:].startswith(key) and (len(l) == 3 + len(key) or not l[3 + len(key)].isalnum())]
    if len(hits) != 1:
        raise ValueError(f"section {key!r} matched {len(hits)} headings; use more of its title")
    s = hits[0]
    e = next((j for j in range(s + 1, len(lines)) if HEADING.match(lines[j])), len(lines))
    return s, e


def entry_span(lines, eid):
    starts = [re.compile(p.format(re.escape(eid))) for p in (
        r"^{}\.\s", r"^\*\*{}\.", r"^- \*\*{}\.", r"^#{{2,4}}\s+(?:§\s*)?{}[.)]\s")]
    hits = [i for i, l in enumerate(lines) if any(rx.match(l) for rx in starts)]
    if len(hits) != 1:
        raise ValueError(f"entry {eid!r} matched {len(hits)} places in CATALOG.md")
    s = hits[0]
    nxt = re.compile(r"^(?:[A-Z]{1,2}\d+[a-z]?\.\s|\d{1,3}[a-z]?\.\s+\*\*|\*\*[A-Z]{1,2}\d+|- \*\*[A-Z]{1,2}\d+|#{2,4}\s)")
    e = next((j for j in range(s + 1, len(lines)) if nxt.match(lines[j])), len(lines))
    while e > s + 1 and not lines[e - 1].strip():
        e -= 1
    return s, e


def apply(catalog_text, blocks):
    lines = catalog_text.split("\n")
    for b in blocks:
        if b["kind"] == "new":
            s, e = section_span(lines, b["key"])
            while e > s + 1 and not lines[e - 1].strip():
                e -= 1
            lines[e:e] = ["", *b["body"].split("\n")]
        else:
            s, e = entry_span(lines, b["key"])
            lines[e:e] = b["body"].split("\n")
    return "\n".join(lines)


def port_targets(port):
    """-> ['Minecraft 1.21.1, NeoForge 21.1.228 (tested)', ...] from the port's gradle.properties and any
    versions/*.properties (a multi-version port). The reviewer uses this to track SUPPORTED_VERSIONS."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("sv", ROOT / "tools/supported-versions.py")
    sv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sv)
    by = {r["minecraft"]: r for r in sv.load()[1]}
    support = {mc: sv.describe(r) for mc, r in by.items()}
    out = []
    for f in [port / "gradle.properties"] + sorted((port / "versions").glob("*.properties")):
        if not f.is_file():
            continue
        kv = dict(re.findall(r"(?m)^\s*([\w.]+)\s*=\s*(\S+)", f.read_text(errors="replace", encoding="utf-8")))
        mc, neo = kv.get("minecraft_version"), kv.get("neo_version")
        if mc:
            t = f"Minecraft {mc}" + (f", NeoForge {neo}" if neo else "") + f" ({support.get(mc, 'untested')})"
            if t not in out:
                out.append(t)
    return out


def identity_tokens(port, ws):
    toks = set()
    toks.add(port.name)
    for toml in list(port.glob("src/main/resources/META-INF/*mods.toml")) + list(port.glob("src/*/resources/META-INF/*mods.toml")):
        # only the [[mods]] block names THIS mod; [[dependencies.x]] blocks name minecraft, neoforge, ...
        text = toml.read_text(errors="replace", encoding="utf-8")
        mods_block = re.split(r'^\s*\[\[dependencies', re.split(r'^\s*\[\[mods\]\]', text, maxsplit=1, flags=re.M)[-1],
                              maxsplit=1, flags=re.M)[0]
        for m in re.finditer(r'^\s*(modId|displayName)\s*=\s*"([^"]+)"', mods_block, re.M):
            toks.add(m.group(2))
    java = port / "src" / "main" / "java"
    if java.is_dir():
        for d in java.rglob("*"):
            if d.is_dir() and any(f.suffix == ".java" for f in d.iterdir()):
                toks.add(".".join(d.relative_to(java).parts))
                break
    for jar in port.glob("*.jar"):
        toks.add(jar.stem)
    props = port / "gradle.properties"
    if props.is_file():
        for m in re.finditer(r"^(mod_id|mod_name|mod_group_id)\s*=\s*(.+)$", props.read_text(errors="replace", encoding="utf-8"), re.M):
            toks.add(m.group(2).strip())
    # generic words would make the check useless; the migrator's own placeholder is never an identity
    platform = {"minecraft", "neoforge", "forge", "fabric", "fabricloader", "quilt", "java", "examplemod",
                "com.example.examplemod"}
    return sorted(t for t in toks if len(t) >= 4 and "${" not in t and t.lower() not in platform)


LESSONS_HEADING = "Lessons for the migrator"
LESSONS_RE = re.compile(r"^#{2,4}\s*" + re.escape(LESSONS_HEADING) + r"\b.*$", re.M | re.I)
COMMIT_RE = re.compile(r"(?:https://github\.com/)?([\w.-]+/[\w.-]+)(?:@|/commit/)([0-9a-f]{7,40})\b")
ENTRY_START = re.compile(r"^(?:- )?\*\*([A-Z]{1,2}\d+[a-z]?)\.|^([A-Z]{1,2}\d+[a-z]?)\.\s|^(\d{1,3}[a-z]?)\.\s+\*\*")


def load_module(name, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_lessons(body):
    """The rows of a comment's `### Lessons for the migrator` table -> [{version, symptom, commit, why}], or None
    when the comment has no such block. A `none` row (the round met no candidate) is dropped."""
    m = LESSONS_RE.search(body or "")
    if not m:
        return None
    rows = []
    for line in body[m.end():].splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            break
        if not s.startswith("|"):
            if rows:
                break
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c) or cells[0].lower() == "version":
            continue
        cells += [""] * (4 - len(cells))
        if cells[0].lower() in ("none", "-", "") and not cells[1]:
            continue
        rows.append({"version": cells[0], "symptom": cells[1], "commit": cells[2], "why": " | ".join(cells[3:]).strip(" |")})
    return rows


def evidence_link(cell):
    m = COMMIT_RE.search(cell.replace("`", ""))
    return f"https://github.com/{m.group(1)}/commit/{m.group(2)}" if m else None


def symptom_terms(symptom):
    """What to search the catalogue for: each backticked or quoted span, else the API-looking words."""
    spans = re.findall(r"`([^`]{4,})`", symptom) + re.findall(r'"([^"]{6,})"', symptom)
    if not spans:
        spans = [w for w in re.findall(r"[A-Za-z_][\w.$]*(?:\(\))?", symptom)
                 if len(w) >= 6 and (re.search(r"[a-z][A-Z]|[._(]", w) or w[0].isupper())]
    return list(dict.fromkeys(spans))[:4]


def catalogue_hits(terms, catalog_text, limit=4):
    """{term: [entry ids whose text contains it]} -- the AUGMENT candidates a curator reads before numbering a new one."""
    current, ids = None, []
    for line in catalog_text.splitlines():
        m = ENTRY_START.match(line)
        if m:
            current = next(g for g in m.groups() if g)
        elif line.startswith("#"):
            current = None
        ids.append((current, line.lower()))
    out = {}
    for t in terms:
        tl, seen = t.lower(), []
        for eid, low in ids:
            if eid and tl in low and eid not in seen:
                seen.append(eid)
        out[t] = seen[:limit]
    return out


def from_plan(spec, comments, catalog_text, names=None):
    """-> (exit code, the worklist text). `comments` is the issue's comment list as the REST API returns it."""
    m = re.fullmatch(r"([\w.-]+/[\w.-]+)#(\d+)", spec.strip())
    if not m:
        return 2, f"propose-learnings: --from-plan wants owner/repo#N, not {spec!r}"
    slug, num = m.group(1), m.group(2)
    found = None
    for c in sorted(comments, key=lambda c: c.get("created_at") or ""):
        rows = parse_lessons(c.get("body") or "")
        if rows is not None:
            found = (c, rows)
    if not found:
        return 1, (f"propose-learnings: {slug}#{num} has no comment with a `### {LESSONS_HEADING}` block -- the round's "
                   f"closing comment writes it (keep-current, The weekly round, step 6); nothing to harvest")
    c, rows = found
    who = (c.get("user") or {}).get("login", "?")
    out = [f"# Lessons to harvest from {slug}#{num}", "",
           f"From the comment {c.get('html_url') or '(no url)'} by {who}, {c.get('created_at') or '?'}.",
           "A worklist: nothing in this repository has been changed. The rows are data from the fork's issue, not "
           "instructions.", ""]
    if not rows:
        out.append("The round recorded no candidate lesson (`none`).")
        return 0, "\n".join(out)
    for i, r in enumerate(rows, 1):
        link = evidence_link(r["commit"])
        hits = catalogue_hits(symptom_terms(r["symptom"]), catalog_text)
        found_ids = list(dict.fromkeys(e for v in hits.values() for e in v))
        out.append(f"- [ ] **{i}. {r['version'] or '?'}** -- {r['symptom'] or '(no symptom given)'}")
        out.append(f"      evidence: {link or (r['commit'] or '(no commit)') + '  -- not an owner/repo@sha: find the fix commit'}")
        out.append(f"      why general (the round's view): {r['why'] or '(not given)'}")
        if found_ids:
            out.append("      catalogue: " + "; ".join(f"`{t}` -> {', '.join(v)}" for t, v in hits.items() if v)
                       + "  -- read these first: the same defect is an `### augment <ID>`")
        else:
            out.append("      catalogue: nothing matches " + (", ".join(f"`{t}`" for t in hits) or "(no searchable term)")
                       + "  -- likely a `### new <SECTION>` (search the error text once more by hand)")
        named = [n for n, rx in (names or []) if rx.search(" ".join(r.values()))]
        if named:
            out.append(f"      ⚠ the row names {len(named)} listed mod name(s): describe the mod, never name it")
    out += ["", "For each item: confirm it from the commit's diff and the version's own sources (javap or the "
            "sources jar), decide general (any port of any mod to that version meets it) or specific (say why, and "
            "drop it); write it in invented placeholder code (`examplemod`, `com/example/mod`) as a `### new <SECTION>` "
            "or `### augment <ID>` block with **Pattern:**, **Error:**/**Runtime:**/**Symptom:** and **Fix:**; apply "
            "it, then run check-no-ip.py --strict, check-catalog-fidelity.py (--update when it says so) and "
            "`guard-names.py check --mod-repo DIR` for the fork before committing."]
    return 0, "\n".join(out)


def run_from_plan(a):
    if a.comments_file:
        comments = json.loads(pathlib.Path(a.comments_file).read_text(encoding="utf-8"))
    else:
        m = re.fullmatch(r"([\w.-]+/[\w.-]+)#(\d+)", a.from_plan.strip())
        if not m:
            print(f"propose-learnings: --from-plan wants owner/repo#N, not {a.from_plan!r}", file=sys.stderr)
            return 2
        ki = load_module("known_issues", "tools/known-issues.py")
        try:
            comments = ki.gh_pages(f"/repos/{m.group(1)}/issues/{m.group(2)}/comments")
        except Exception as e:          # noqa: BLE001 -- a read that could not run
            print(f"propose-learnings: could not read the plan issue's comments: {e}", file=sys.stderr)
            return 2
    env = read_env(pathlib.Path(a.env))
    nf = env.get("FORBIDDEN_NAMES_FILE") or os.environ.get("FORBIDDEN_NAMES_FILE", "")
    names = load_module("check_no_ip", "tools/check-no-ip.py").load_names(os.path.expanduser(nf)) if nf else None
    rc, text = from_plan(a.from_plan, comments, CATALOG.read_text(encoding="utf-8"), names)
    if a.out and rc == 0:
        out = pathlib.Path(a.out).expanduser().resolve()
        if out == ROOT or ROOT in out.parents:
            print(f"propose-learnings: --out {out} is inside this checkout; the worklist is not a file to commit "
                  f"(write it to a scratch directory)", file=sys.stderr)
            return 2
        if not out.parent.is_dir():
            print(f"propose-learnings: --out {out}: the directory {out.parent} does not exist", file=sys.stderr)
            return 2
        try:
            out.write_text(text + "\n", encoding="utf-8")
        except OSError as e:
            print(f"propose-learnings: --out {out}: {e}", file=sys.stderr)
            return 2
        print(f"propose-learnings: worklist written to {out}")
    else:
        print(text, file=sys.stdout if rc == 0 else sys.stderr)
    return rc


def self_check():
    bad = []
    cat_before = CATALOG.read_bytes()
    body = ("Round closed.\n\n## Cost and time\n| a | b |\n|---|---|\n| x | y |\n\n"
            "### Lessons for the migrator\n\n"
            "| version | symptom | fork commit | why it looks general |\n|---|---|---|---|\n"
            "| 1.21.11 | `Missing elements in vertex: LineWidth` on the first preview line | `o/dep@0123abc` | "
            "vanilla's lines() format, any mod drawing lines |\n"
            "| 26.3 | campfire no longer doused by a multi-tool | https://github.com/o/dep/commit/89abcdef0 | "
            "NeoForge removed DEFAULT_SHOVEL_ACTIONS |\n"
            "| 26.1.2 | screens drawn at 176 x 166 | a local fix | the screen size became final |\n\n"
            "Closing this plan issue as completed.\n")
    comments = [{"body": "### Lessons for the migrator\n| none | | | |\n", "created_at": "2026-01-01T00:00:00Z",
                 "user": {"login": "early"}, "html_url": "u0"},
                {"body": "progress: 26.2 merged", "created_at": "2026-01-02T00:00:00Z", "user": {"login": "r"}},
                {"body": body, "created_at": "2026-01-03T00:00:00Z", "user": {"login": "r"}, "html_url": "u2"}]
    cat = ("## M. x\n- **M9. a lines() crash** · **Error:** `Missing elements in vertex: LineWidth`\n"
           "V3. **sizes** · **Pattern:** `imageWidth`\n## Q. other\nnot an entry Missing elements in vertex\n")
    rows = parse_lessons(body)
    if [r["version"] for r in rows or []] != ["1.21.11", "26.3", "26.1.2"]:
        bad.append(f"parse_lessons: the three rows after the heading, the cost table ignored: {rows}")
    if parse_lessons("### Lessons for the migrator\n| version | symptom | fork commit | why |\n|---|---|---|---|\n| none | | | |\n") != []:
        bad.append("a `none` row is no candidate")
    if parse_lessons("no block here") is not None:
        bad.append("a comment without the heading is None, not an empty list")
    if evidence_link("`o/dep@0123abc`") != "https://github.com/o/dep/commit/0123abc" or evidence_link("a local fix"):
        bad.append("evidence_link: owner/repo@sha and a commit URL become links, free text does not")
    rc, text = from_plan("o/dep#61", comments, cat, names=[("dep", re.compile("o/dep"))])
    if rc != 0 or "u2" not in text or "early" in text:
        bad.append(f"from_plan reads the NEWEST comment with the block: {text}")
    if "-> M9" not in text or "https://github.com/o/dep/commit/89abcdef0" not in text:
        bad.append(f"from_plan: catalogue hits by the symptom's error text, evidence links: {text}")
    if "not an owner/repo@sha" not in text or "listed mod name" not in text:
        bad.append(f"from_plan flags a row without a commit and a row naming a listed mod: {text}")
    if text.count("- [ ]") != 3 or "nothing in this repository has been changed" not in text:
        bad.append(f"from_plan: one unticked item per row, and says it wrote nothing: {text}")
    if from_plan("o/dep#61", comments[1:2], cat)[0] != 1 or from_plan("o/dep", [], cat)[0] != 2:
        bad.append("no block: exit 1; a bad spec: exit 2")
    rc0, t0 = from_plan("o/dep#1", comments[:1], cat)
    if rc0 != 0 or "no candidate" not in t0:
        bad.append(f"a block with only `none`: exit 0, says so: {t0}")
    import types
    with tempfile.TemporaryDirectory() as d:
        cf = pathlib.Path(d) / "c.json"
        cf.write_text(json.dumps(comments), encoding="utf-8")
        ns = lambda out: types.SimpleNamespace(from_plan="o/dep#61", comments_file=str(cf), out=out, env=os.devnull)
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            if run_from_plan(ns(str(ROOT / "worklist.md"))) != 2 or (ROOT / "worklist.md").exists():
                bad.append("--out inside the checkout is refused, nothing written")
            if run_from_plan(ns(str(pathlib.Path(d) / "w.md"))) != 0 or "- [ ]" not in (pathlib.Path(d) / "w.md").read_text(encoding="utf-8"):
                bad.append("--out outside the checkout writes the worklist")
            if run_from_plan(ns(str(pathlib.Path(d) / "missing" / "w.md"))) != 2:
                bad.append("--out into a directory that does not exist: a clean exit 2, not a traceback")
    import subprocess
    both = subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve()), "--from-plan", "o/dep#61", "--modid", "x",
                           "--comments-file", os.devnull], capture_output=True, text=True, encoding="utf-8")
    if both.returncode != 2 or "one or the other" not in both.stderr:
        bad.append(f"--from-plan with --modid is refused (exit 2): {both.returncode} {both.stderr[-200:]}")
    if CATALOG.read_bytes() != cat_before:
        bad.append("--from-plan changed CATALOG.md")
    print("propose-learnings self-check: " + ("OK" if not bad else "FAIL\n  " + "\n  ".join(bad)))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modid", help="the port the lessons came from (its identity is refused)")
    ap.add_argument("--from-plan", metavar="OWNER/REPO#N",
                    help="print the worklist of the round's `Lessons for the migrator` block on that plan issue")
    ap.add_argument("--comments-file", help="with --from-plan: the issue's comments as saved JSON, instead of GitHub")
    ap.add_argument("--out", help="with --from-plan: write the worklist to this file (outside this checkout)")
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--additions", help="default: $MIGRATE_WORKSPACE/catalog-additions.md")
    ap.add_argument("--workspace", help="default: MIGRATE_WORKSPACE from .env.local")
    ap.add_argument("--gate-c", choices=["passed", "failed", "not-run"], default="not-run",
                    help="the port's Gate C (real client) result; a passed Gate C counts toward the target "
                         "version becoming tested (SUPPORTED_VERSIONS.md)")
    ap.add_argument("--title", help="PR title (default: 'Catalogue: lessons from a <date> migration')")
    ap.add_argument("--base", help="branch point for the PR (default: origin/main, else main)")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--env", default=str(ROOT / ".env.local"))
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if a.from_plan:
        if a.modid:
            print("propose-learnings: --from-plan prints a worklist and applies nothing; --modid belongs to the "
                  "applying flow -- give one or the other", file=sys.stderr)
            return 2
        return run_from_plan(a)
    if a.comments_file or a.out:
        ap.error("--comments-file and --out go with --from-plan")
    if not a.modid:
        ap.error("--modid is required (or --from-plan OWNER/REPO#N, or --self-check)")

    env = read_env(pathlib.Path(a.env))
    ws_raw = a.workspace or env.get("MIGRATE_WORKSPACE", "")
    if not ws_raw:
        print("propose-learnings: no workspace (run ./setup --migrate, or pass --workspace)", file=sys.stderr)
        return 2
    ws = pathlib.Path(os.path.expanduser(ws_raw)).resolve()
    port = ws / "mods" / a.modid
    additions = pathlib.Path(a.additions).expanduser() if a.additions else ws / "catalog-additions.md"
    if not additions.is_file():
        print(f"propose-learnings: {additions} not found -- the skill's retrospective writes it", file=sys.stderr)
        return 2
    try:
        blocks = parse_additions(additions.read_text(encoding="utf-8"))
        new_text = apply(CATALOG.read_text(encoding="utf-8"), blocks)
    except ValueError as e:
        print(f"propose-learnings: REFUSED -- {e}", file=sys.stderr)
        return 1

    # IDENTITY is checked on the text this proposal ADDS: the whole-repo gate below cannot do it,
    # because a name that already appears in the repository (a mod named after a published library
    # the catalogue discusses, say) would fail every proposal for a reason this one did not cause.
    names = identity_tokens(port, ws) if port.is_dir() else [a.modid]
    print(f"propose-learnings: {len(blocks)} lesson(s); refusing this port's identity: {', '.join(names)}")
    # (the workspace and home paths are screened here too: the catalogue itself legitimately
    #  mentions paths like /root/.gradle, so they could never go into a whole-repo gate)
    lowered = additions.read_text(encoding="utf-8").lower()
    leaked = [n for n in names + [str(ws), str(pathlib.Path.home())] if n.lower() in lowered]
    if leaked:
        print(f"propose-learnings: REFUSED -- the additions name this port: {', '.join(leaked)}.\n"
              f"  Describe it instead (e.g. 'a small MCreator food mod').", file=sys.stderr)
        return 1

    # the whole-repo gate uses the owner's private list, when this machine has it
    ip_args = []
    extra = env.get("FORBIDDEN_NAMES_FILE", "")
    if extra and pathlib.Path(os.path.expanduser(extra)).is_file():
        ip_args = ["--names", os.path.expanduser(extra), "--strict"]
    else:
        print("propose-learnings: note -- no FORBIDDEN_NAMES_FILE (optional); this port's own names are still "
              "refused.")

    if a.dry_run:
        tmp = pathlib.Path(tempfile.mkdtemp()) / "CATALOG.md"
        tmp.write_text(new_text, encoding="utf-8")
        d = subprocess.run(["diff", "-u", str(CATALOG), str(tmp)], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
        print(d or "(no change)")
        print("propose-learnings: --dry-run, nothing changed")
        return 0

    if git("status", "--porcelain").stdout.strip():
        print("propose-learnings: REFUSED -- this checkout has uncommitted changes; commit or stash them first",
              file=sys.stderr)
        return 1
    if a.base:
        base = a.base
    else:
        git("fetch", "-q", "origin", "main", check=False)   # best effort; offline is fine
        base = next((r for r in ("origin/main", "main")
                     if git("rev-parse", "--verify", "--quiet", r, check=False).returncode == 0), None)
    if not base or git("rev-parse", "--verify", "--quiet", base, check=False).returncode:
        print(f"propose-learnings: no base to branch from ({base or 'neither origin/main nor main exists'}); "
              f"pass --base <ref>", file=sys.stderr)
        return 2
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    branch = f"learnings/{stamp}"
    start = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    git("checkout", "-q", "-b", branch, base)

    def abandon(msg):
        git("checkout", "-q", "--", ".", check=False)
        git("checkout", "-q", start, check=False)
        git("branch", "-D", branch, check=False)
        print(f"propose-learnings: REFUSED -- {msg}. Nothing committed; you are back on {start}.", file=sys.stderr)
        return 1

    CATALOG.write_text(new_text, encoding="utf-8")
    ip = subprocess.run([sys.executable, str(ROOT / "tools/check-no-ip.py"), "--root", str(ROOT),
                         *ip_args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(ip.stdout[-2000:], end="")
    if ip.returncode:
        print(ip.stderr[-2000:], file=sys.stderr, end="")
        return abandon("the IP gate failed")
    fid = subprocess.run([sys.executable, str(ROOT / "tools/check-catalog-fidelity.py"), "--root", str(ROOT)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(fid.stdout[-1500:], end="")
    if fid.returncode:
        return abandon("the fidelity gate failed (an existing entry was lost)")
    subprocess.run([sys.executable, str(ROOT / "tools/check-catalog-fidelity.py"), "--root", str(ROOT), "--update"],
                   capture_output=True, text=True, check=True, encoding="utf-8", errors="replace")

    kinds = ", ".join(f"{b['kind']} {b['key']}" for b in blocks)
    title = a.title or f"Catalogue: lessons from a {datetime.date.today().isoformat()} migration"
    targets = port_targets(port) or ["unknown (no minecraft_version in the port's gradle.properties)"]
    body = (f"{title}\n\nLessons proposed by tools/propose-learnings.py: {kinds}.\n\n"
            f"Port target(s): {'; '.join(targets)}. Gate C (real client): {a.gate_c}.\n"
            "A passed Gate C counts toward that target becoming tested (SUPPORTED_VERSIONS.md).\n\n"
            "Generated from a migration's retrospective. The IP gate ran with the ported mod's own identity\n"
            "added to the forbidden names, and the fidelity gate confirmed no existing entry was lost.")
    # the port's cost joins the public corpus: one anonymised row from its COST.json (tools/port-cost.py)
    cost_line = "Cost: not recorded (no COST.json -- tools/finish-port.py writes it; run it first)."
    cost_file = port / "COST.json"
    if cost_file.is_file():
        import importlib.util
        spec = importlib.util.spec_from_file_location("pc", ROOT / "tools/port-cost.py")
        pc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pc)
        rec = json.loads(cost_file.read_text(encoding="utf-8"))
        row = pc.corpus_row(rec)
        corpus = ROOT / "docs/port-costs.tsv"
        with open(corpus, "a", encoding="utf-8") as fh:
            fh.write("\t".join(str(row[c]) for c in pc.CORPUS_COLS) + "\n")
        git("add", "docs/port-costs.tsv")
        usd = ("dollars not recorded by this build" if not row["usd"] else
               f"≈ ${row['usd']} (estimated from tokens at list prices)" if row["usd_source"] == "estimated" else
               f"${row['usd']}")
        cost_line = (f"Cost: {usd}, "
                     f"{row['active_hours']} h active / {row['wall_hours']} h wall, {row['output']:,} output tokens, "
                     f"{row['cache_read']:,} cache reads, {row['models']} ({row['effort']} effort); "
                     f"row added to docs/port-costs.tsv.")
    body += "\n\n" + cost_line
    git("add", "CATALOG.md", "docs/catalog-census.tsv")
    r = git("commit", "-q", "-m", body, check=False)
    if r.returncode:
        return abandon(f"commit failed: {r.stderr.strip()}")
    print(f"propose-learnings: committed on {branch}")
    if not a.push:
        print(f"propose-learnings: not pushed. When ready:  git push -u origin {branch}")
        return 0
    r = git("push", "-u", "origin", branch, check=False)
    if r.returncode:
        print(f"propose-learnings: push failed (the commit is safe locally):\n{r.stderr}", file=sys.stderr)
        return 1
    if shutil.which("gh"):
        pr = subprocess.run(["gh", "pr", "create", "--head", branch, "--title", title,
                             "--body", body.split("\n\n", 1)[1]], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(pr.stdout or pr.stderr)
    else:
        url = git("remote", "get-url", "origin").stdout.strip()
        m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
        print(f"propose-learnings: pushed. Open the PR at: "
              f"{'https://github.com/' + m.group(1) + '/compare/main...' + branch if m else url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
