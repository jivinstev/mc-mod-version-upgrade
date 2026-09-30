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
Standard library only.  Exit codes: 0 done  1 refused (gate or format)  2 could not run.
"""
import argparse, datetime, os, pathlib, re, shutil, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
CATALOG = ROOT / "CATALOG.md"


def read_env(path):
    env = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def git(*args, check=True):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=check)


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
            missing = [k for k in ("**Pattern:**", "**Fix:**") if k not in b["body"]]
            if not any(k in b["body"] for k in ("**Error:**", "**Runtime:**", "**Symptom:**")):
                missing.append("**Error:** / **Runtime:** / **Symptom:**")
            if missing:
                raise ValueError(f"new entry under {b['key']} lacks {', '.join(missing)}")
        elif not b["body"].lstrip().startswith("· **AUGMENT"):
            raise ValueError(f"augment {b['key']} must start with `· **AUGMENT`")
    if not blocks:
        raise ValueError("no `### new <SECTION>` or `### augment <ENTRY-ID>` blocks found")
    return blocks


HEADING = re.compile(r"^(#{2,3})\s")


def section_span(lines, key):
    """-> (start, end) line indexes of the `## <key>` section; end = next level-2/3 heading."""
    hits = [i for i, l in enumerate(lines)
            if l.startswith("## ") and (l[3:].startswith(key + ".") or l[3:].startswith(key))]
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


def identity_tokens(port, ws):
    toks = set()
    toks.add(port.name)
    for toml in list(port.glob("src/main/resources/META-INF/*mods.toml")) + list(port.glob("src/*/resources/META-INF/*mods.toml")):
        # only the [[mods]] block names THIS mod; [[dependencies.x]] blocks name minecraft, neoforge, ...
        text = toml.read_text(errors="replace")
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
        for m in re.finditer(r"^(mod_id|mod_name|mod_group_id)\s*=\s*(.+)$", props.read_text(errors="replace"), re.M):
            toks.add(m.group(2).strip())
    # generic words would make the check useless; the migrator's own placeholder is never an identity
    platform = {"minecraft", "neoforge", "forge", "fabric", "fabricloader", "quilt", "java", "examplemod",
                "com.example.examplemod"}
    return sorted(t for t in toks if len(t) >= 4 and "${" not in t and t.lower() not in platform)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modid", required=True, help="the port the lessons came from (its identity is refused)")
    ap.add_argument("--additions", help="default: $MIGRATE_WORKSPACE/catalog-additions.md")
    ap.add_argument("--workspace", help="default: MIGRATE_WORKSPACE from .env.local")
    ap.add_argument("--title", help="PR title (default: 'Catalogue: lessons from a <date> migration')")
    ap.add_argument("--base", help="branch point for the PR (default: origin/main, else main)")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--env", default=str(ROOT / ".env.local"))
    a = ap.parse_args()

    env = read_env(pathlib.Path(a.env))
    ws_raw = a.workspace or env.get("MIGRATE_WORKSPACE", "")
    if not ws_raw:
        print("propose-learnings: no workspace (run ./setup --path migrate, or pass --workspace)", file=sys.stderr)
        return 2
    ws = pathlib.Path(os.path.expanduser(ws_raw)).resolve()
    port = ws / "mods" / a.modid
    additions = pathlib.Path(a.additions).expanduser() if a.additions else ws / "catalog-additions.md"
    if not additions.is_file():
        print(f"propose-learnings: {additions} not found -- the skill's retrospective writes it", file=sys.stderr)
        return 2
    try:
        blocks = parse_additions(additions.read_text())
        new_text = apply(CATALOG.read_text(), blocks)
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
    lowered = additions.read_text().lower()
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
        print("propose-learnings: no FORBIDDEN_NAMES_FILE configured -- the IP gate runs without the private "
              "name list (the reviewer and CI run it with the list).")

    if a.dry_run:
        tmp = pathlib.Path(tempfile.mkdtemp()) / "CATALOG.md"
        tmp.write_text(new_text)
        d = subprocess.run(["diff", "-u", str(CATALOG), str(tmp)], capture_output=True, text=True).stdout
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

    CATALOG.write_text(new_text)
    ip = subprocess.run([sys.executable, str(ROOT / "tools/check-no-ip.py"), "--root", str(ROOT),
                         *ip_args], capture_output=True, text=True)
    print(ip.stdout[-2000:], end="")
    if ip.returncode:
        print(ip.stderr[-2000:], file=sys.stderr, end="")
        return abandon("the IP gate failed")
    fid = subprocess.run([sys.executable, str(ROOT / "tools/check-catalog-fidelity.py"), "--root", str(ROOT)],
                         capture_output=True, text=True)
    print(fid.stdout[-1500:], end="")
    if fid.returncode:
        return abandon("the fidelity gate failed (an existing entry was lost)")
    subprocess.run([sys.executable, str(ROOT / "tools/check-catalog-fidelity.py"), "--root", str(ROOT), "--update"],
                   capture_output=True, text=True, check=True)

    kinds = ", ".join(f"{b['kind']} {b['key']}" for b in blocks)
    title = a.title or f"Catalogue: lessons from a {datetime.date.today().isoformat()} migration"
    body = (f"{title}\n\nLessons proposed by tools/propose-learnings.py: {kinds}.\n\n"
            "Generated from a migration's retrospective. The IP gate ran with the ported mod's own identity\n"
            "added to the forbidden names, and the fidelity gate confirmed no existing entry was lost.")
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
                             "--body", body.split("\n\n", 1)[1]], cwd=ROOT, capture_output=True, text=True)
        print(pr.stdout or pr.stderr)
    else:
        url = git("remote", "get-url", "origin").stdout.strip()
        m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
        print(f"propose-learnings: pushed. Open the PR at: "
              f"{'https://github.com/' + m.group(1) + '/compare/main...' + branch if m else url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
