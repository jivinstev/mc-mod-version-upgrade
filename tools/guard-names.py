#!/usr/bin/env python3
"""Set up the forbidden-names guard for the mods a session works on (issue #107): one command instead of a manual setup.

    python3 tools/guard-names.py ensure --mod-repo DIR [--mod-repo DIR ...] [--jar JAR ...] [--dry-run]
    python3 tools/guard-names.py check  --mod-repo DIR [--jar JAR ...]
    python3 tools/guard-names.py --self-check

`ensure`, for the mod being worked on AND every fork it depends on (one --mod-repo / --jar each):
  1. derives the names that identify each mod: neoforge.mods.toml / mods.toml `modId`, `displayName`, `authors`;
     gradle.properties `mod_id`, `mod_name`, `mod_authors`, `mod_group_id`, `maven_group`, `archives_name`; the root Java
     package (dotted and slashed); the checkout's GitHub `owner/repo` (origin and upstream remotes; the owner of THIS
     repository's origin -- the maintainer, named in its README -- is never added). A --jar gives its toml and package.
  2. a short id (under check-no-ip.py's 6-character substring floor) or an ordinary word becomes a `re:\\b<id>\\b` line,
     matched as a whole word. An ordinary word that would still match this repository's own text is kept only in its
     mod-specific forms (`<id>:` resource locations, `assets/<id>/`...); an ordinary-word display name or author that
     would is left out. Both are reported, so a person can decide.
  3. MERGES what is new into the list at FORBIDDEN_NAMES_FILE (the environment, else this repo's .env.local; default
     ~/.mmvu/forbidden-names.txt): appended at the end, every existing line kept as it is and in its order; a name an
     existing line already matches is not added again. A list inside any git repository is refused.
  4. writes FORBIDDEN_NAMES_FILE=<list> into this repo's .env.local (git-ignored) when neither sets it;
  5. runs tools/install-hooks.sh when .git/hooks/pre-push or pre-commit is missing or differs from what it writes;
  6. finishes with check-no-ip.py --strict --names <list> over this repository, so a name already in it surfaces now.
  It prints what it added -- to the session only, never to a file in a repository. --dry-run writes nothing (no list,
  no .env.local, no hooks): it prints what it would add and runs step 6 against a scratch copy of the merged list.

`check` changes nothing: exit 0 when the list exists and already names every mod given, 1 when a name is missing
(run `ensure`), 2 when there is no list. A skill about to commit or push to this repository runs it first.
Exit codes of `ensure`: 0 done and the tree is clean, 1 the tree names a listed name, 2 refused / could not run.
Standard library only.
"""
import argparse, importlib.util, os, pathlib, re, shutil, subprocess, sys, tempfile, zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))   # tools/gitbash.py (Git Bash on Windows)
DEFAULT_LIST = "~/.mmvu/forbidden-names.txt"
_s = importlib.util.spec_from_file_location("check_no_ip", ROOT / "tools/check-no-ip.py")
cni = importlib.util.module_from_spec(_s); _s.loader.exec_module(cni)

# Never a name on their own: platform and build words every mod shares, and generic package roots.
GENERIC = {"mod", "mods", "team", "the", "and", "minecraft", "neoforge", "forge", "fabric", "quilt", "common", "api",
           "core", "lib", "library", "main", "java", "src", "example", "com", "net", "org", "io", "dev", "me", "github",
           "contributors", "others", "author", "authors", "unknown", "none", "null", "client", "server", "addon"}
# A few ordinary words mod ids are made of. A word found as a word in ORDINARY_FILES or more of this repository's own
# files counts as ordinary too (this repository's text is the best dictionary of what the guard must not break).
ORDINARY = {"arena", "magic", "spells", "combat", "storage", "farming", "food", "tools", "weapons", "armor", "armour",
            "dragons", "bosses", "mobs", "creatures", "pets", "quests", "biomes", "structures", "dungeons", "loot",
            "backpacks", "furniture", "decor", "lights", "nature", "ores", "gems", "potions", "enchantments", "trades",
            "villagers", "cooking", "delight", "create", "better", "more", "simple", "extra", "ultimate", "epic"}
ORDINARY_FILES = 3
PLATFORM_PKG = re.compile(r"^(?:net\.minecraft|net\.neoforged|net\.minecraftforge|com\.mojang|java|javax)\b")


def sh(*cmd, cwd=None):
    try:
        r = subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError:
        return 1, ""
    return r.returncode, r.stdout.strip()


# ------------------------------------------------------------------------------------------------- derivation

TOML_KEYS = ("modId", "displayName", "authors")
PROP_KEYS = ("mod_id", "mod_name", "mod_authors", "mod_group_id", "maven_group", "archives_name", "archives_base_name")


def _toml_values(text):
    """[(key, value)] for the [[mods]] keys we read; `${...}` placeholders (a templated toml) are skipped. Only the
    [[mods]] tables (and keys before any table): a [[dependencies.x]] table's modId names ANOTHER mod."""
    keep, cur = [], "[[mods]]"
    for line in text.splitlines():
        h = re.match(r"^\s*(\[\[?[^\]]+\]\]?)", line)
        if h:
            cur = h.group(1).replace(" ", "")
        if cur == "[[mods]]":
            keep.append(line)
    text, out = "\n".join(keep), []
    for k in TOML_KEYS:
        for m in re.finditer(r'(?m)^\s*%s\s*=\s*(?:"([^"\n]*)"|\'([^\'\n]*)\'|"""(.*?)""")' % k, text, re.S):
            v = (m.group(1) or m.group(2) or m.group(3) or "").strip()
            if v and "${" not in v:
                out.append((k, v))
    return out


def _split_people(v):
    return [p.strip() for p in re.split(r",|;|&|\band\b|\n", v) if p.strip()]


def _common_package(pkgs):
    pkgs = [p.split(".") for p in pkgs if p and not PLATFORM_PKG.match(p)]
    if not pkgs:
        return None
    pre = pkgs[0]
    for p in pkgs[1:]:
        n = 0
        while n < min(len(pre), len(p)) and pre[n] == p[n]:
            n += 1
        pre = pre[:n]
    return ".".join(pre) if len(pre) >= 2 else None


SKIP_DIRS = {"build", ".gradle", "run", "runs", ".git", "node_modules", "out", "bin"}


def _files(repo, pattern):
    for p in repo.rglob(pattern):
        if not any(d in SKIP_DIRS for d in p.relative_to(repo).parts[:-1]):
            yield p


def root_packages(repo):
    """The @Mod classes' packages (what a mod's identity lives under); else the common package of every source file."""
    mods, every = set(), set()
    for f in _files(repo, "*.java"):
        if "src" not in f.relative_to(repo).parts:
            continue
        t = f.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^package\s+([\w.]+)\s*;", t, re.M)
        if not m:
            continue
        every.add(m.group(1))
        if re.search(r"^\s*@Mod\b", t, re.M):
            mods.add(m.group(1))
    found = sorted(p for p in mods if not PLATFORM_PKG.match(p) and len(p.split(".")) >= 2)
    if found:
        return found
    c = _common_package(sorted(every))
    return [c] if c else []


def github_slugs(repo):
    """[(owner, name)] from the checkout's origin and upstream remotes."""
    out = []
    for remote in ("origin", "upstream"):
        rc, url = sh("git", "-C", str(repo), "remote", "get-url", remote)
        m = re.search(r"[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", url) if rc == 0 else None
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def derive_repo(repo):
    """-> [(kind, value)] from a mod checkout."""
    repo = pathlib.Path(repo).resolve()
    out = []
    for f in sorted(_files(repo, "*mods.toml")):
        if f.name in ("mods.toml", "neoforge.mods.toml"):
            out += _toml_values(f.read_text(encoding="utf-8", errors="replace"))
    for f in sorted(_files(repo, "gradle.properties")):
        t = f.read_text(encoding="utf-8", errors="replace")
        for k in PROP_KEYS:
            for m in re.finditer(r"(?m)^\s*%s\s*[=:]\s*(.+?)\s*$" % k, t):
                v = m.group(1)
                if v and "${" not in v:
                    out.append((k, v))
    for p in root_packages(repo):
        out.append(("package", p))
    for owner, name in github_slugs(repo):
        out.append(("github", f"{owner}/{name}"))
        out.append(("github_repo", name))
        out.append(("github_owner", owner))
    return out


def derive_jar(jar):
    out = []
    with zipfile.ZipFile(jar) as z:
        names = z.namelist()
        for n in names:
            if n in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
                out += _toml_values(z.read(n).decode("utf-8", "replace"))
        pkgs = sorted({n.rsplit("/", 1)[0].replace("/", ".") for n in names
                       if n.endswith(".class") and "/" in n and not n.startswith("META-INF/")})
    c = _common_package(pkgs)
    if not c:   # shaded libraries beside the mod: the 3-part prefix holding the most classes
        counts = {}
        for p in pkgs:
            k = ".".join(p.split(".")[:3])
            if len(k.split(".")) >= 2 and not PLATFORM_PKG.match(k):
                counts[k] = counts.get(k, 0) + 1
        c = max(counts, key=counts.get) if counts else None
    if c:
        out.append(("package", c))
    return out


def candidates(kinds_values, maintainer=None):
    """[(kind, value)] -> the strings to forbid, most identifying first, de-duplicated (case-insensitive)."""
    order = {"modId": 0, "mod_id": 0, "package": 1, "mod_group_id": 1, "maven_group": 1, "github": 2,
             "github_repo": 2, "archives_name": 3, "archives_base_name": 3, "displayName": 4, "mod_name": 4,
             "authors": 5, "mod_authors": 5, "github_owner": 5}
    out, seen = [], set()
    for kind, v in sorted(kinds_values, key=lambda kv: order.get(kv[0], 9)):
        vals = _split_people(v) if kind in ("authors", "mod_authors") else [v.strip().strip('"\'')]
        if kind == "package" or kind in ("mod_group_id", "maven_group"):
            vals = [x for x in vals if len(x.split(".")) >= 2] + [x.replace(".", "/") for x in vals if len(x.split(".")) >= 2]
        for x in vals:
            if kind in ("mod_group_id", "maven_group") and x.lower().replace("/", ".") in ("com.example", "net.example"):
                continue
            if kind == "github_owner" and maintainer and x.lower() == maintainer.lower():
                continue
            if kind == "github" and maintainer and x.split("/")[0].lower() == maintainer.lower():
                pass   # the fork's own slug names the mod's repository: kept
            if len(x) < 3 or x.lower() in GENERIC or not re.search(r"[A-Za-z]", x):
                continue
            if x.lower() not in seen:
                seen.add(x.lower()); out.append((kind, x))
    return out


# ------------------------------------------------------------------------------------------------- the list

def word_files(texts, word):
    rx = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(word) + r"(?![A-Za-z0-9_])", re.I)
    return sum(1 for t in texts if rx.search(t))


def is_ordinary(word, texts):
    w = word.lower()
    return w in ORDINARY or (re.fullmatch(r"[a-z]+", w) is not None and word_files(texts, w) >= ORDINARY_FILES)


def entry_for(kind, x, texts):
    """-> ([list lines], note or None): the line(s) for one candidate, given this repository's own texts."""
    single = re.fullmatch(r"[A-Za-z0-9_-]+", x) is not None
    if single and (len(x) < cni.SUBSTRING_MIN_LEN or is_ordinary(x, texts)):
        line = r"re:\b%s\b" % re.escape(x.lower())
    else:
        line = x
    rx = cni.load_names_lines([line])[0][1]
    hits = sum(1 for t in texts if rx.search(t))
    if not hits:
        return [line], None
    words = re.findall(r"[A-Za-z]+", x)
    if single and is_ordinary(x, texts):
        e = re.escape(x.lower())
        ctx = [r"re:\b%s:[a-z0-9_/.]" % e, r"re:(?:assets|data|mods)/%s\b" % e]
        if not any(cni.load_names_lines([c])[0][1].search(t) for c in ctx for t in texts):
            return ctx, f"an ordinary word ({hits} file(s) here use it): kept only as `<id>:` and `assets|data|mods/<id>/`"
        return [], f"an ordinary word ({hits} file(s) here use it, even in mod-specific forms): left out"
    if kind in ("displayName", "mod_name", "authors", "mod_authors", "github_owner") and words \
            and all(is_ordinary(w, texts) or w.lower() in GENERIC for w in words):
        return [], f"ordinary words ({hits} file(s) here use them): left out"
    return [line], f"ALREADY IN THIS REPOSITORY ({hits} file(s)): added, and the check below names the files"


def inside_git_repo(path):
    p = pathlib.Path(path).expanduser().resolve()
    for d in [p.parent, *p.parent.parents]:
        if (d / ".git").exists():
            return d
    rc, top = sh("git", "-C", str(p.parent if p.parent.is_dir() else p.parent.parent), "rev-parse", "--show-toplevel")
    return pathlib.Path(top) if rc == 0 and top else None


def expected_hooks(root):
    """{hook name: content} as tools/install-hooks.sh writes them (its heredocs), or {} when it cannot be read."""
    t = (root / "tools/install-hooks.sh")
    if not t.is_file():
        return {}
    return {m.group(1): m.group(2) for m in
            re.finditer(r"cat > \.git/hooks/([\w-]+) <<'HOOK'\n(.*?\n)HOOK\n", t.read_text(encoding="utf-8"), re.S)}


def stale_hooks(root):
    hooks = root / ".git/hooks"
    return [n for n, body in expected_hooks(root).items()
            if not (hooks / n).is_file() or (hooks / n).read_text(encoding="utf-8", errors="replace") != body]


def maintainer_of(root):
    s = github_slugs(root)
    return s[0][0] if s else None


def repo_texts(root):
    files, _how = cni.tracked_files(root)
    out = []
    for f in files:
        if f.is_file() and f.suffix.lower() in cni.TEXT_SUFFIXES:
            try:
                out.append(f"{f.relative_to(root).as_posix()}\n" + f.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    return out


def plan(sources, root, existing_lines):
    """-> (added lines, notes): what is new for these mods, given the list's current lines."""
    texts = repo_texts(root)
    maint = maintainer_of(root)
    covered = cni.load_names_lines(existing_lines)
    added, notes = [], []
    for kind, x in candidates(sources, maint):
        if any(rx.search(x) for _n, rx in covered):
            continue                                    # an existing (or just-added) line already matches it
        lines, note = entry_for(kind, x, texts)
        lines = [l for l in lines if l.lower() not in {e.lower() for e in existing_lines + added}]
        if note:
            notes.append(f"{x} ({kind}): {note}")
        added += lines
        covered += cni.load_names_lines(lines)
    return added, notes


def list_path(root):
    path, where = cni.default_names(root)
    return (pathlib.Path(os.path.expanduser(path)) if path else pathlib.Path(os.path.expanduser(DEFAULT_LIST))), where


def gather(a):
    src = []
    for d in a.mod_repo or []:
        if not pathlib.Path(d).is_dir():
            sys.exit(f"guard-names: no such checkout: {d}")
        src += derive_repo(d)
    for j in a.jar or []:
        src += derive_jar(j)
    if not src:
        sys.exit("guard-names: nothing derived: pass --mod-repo DIR (a mod checkout) or --jar JAR")
    return src


def run_check(root, names, quiet=False):
    r = subprocess.run([sys.executable, str(ROOT / "tools/check-no-ip.py"), "--root", str(root), "--strict",
                        "--names", str(names)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if not quiet or r.returncode:
        print((r.stdout + r.stderr).rstrip())
    return r.returncode


def ensure(a, root):
    src = gather(a)
    lst, where = list_path(root)
    inside = inside_git_repo(lst)
    if inside:
        print(f"guard-names: REFUSED -- the list {lst} is inside the git repository {inside}; it must live outside "
              f"every repository (default {DEFAULT_LIST})", file=sys.stderr)
        return 2
    existing = lst.read_text(encoding="utf-8").splitlines() if lst.is_file() else []
    added, notes = plan(src, root, [l.strip() for l in existing if l.strip() and not l.strip().startswith("#")])
    verb = "would add" if a.dry_run else "added"
    print(f"guard-names: list {lst} ({where or 'default'}): {len([l for l in existing if l.strip() and not l.startswith('#')])} "
          f"existing name(s); {verb} {len(added)}")
    for l in added:
        print(f"  + {l}")
    for n in notes:
        print(f"  note: {n}")
    if a.dry_run:
        with tempfile.TemporaryDirectory() as d:
            scratch = pathlib.Path(d) / "names.txt"
            scratch.write_text("\n".join(existing + added) + "\n", encoding="utf-8")
            env_line = "unchanged" if where else f"would write FORBIDDEN_NAMES_FILE={lst}"
            print(f"guard-names: .env.local {env_line}; hooks {'would be installed: ' + ', '.join(stale_hooks(root)) if stale_hooks(root) else 'current'}")
            return 1 if run_check(root, scratch, a.quiet) else 0
    if added:
        lst.parent.mkdir(parents=True, exist_ok=True)
        body = lst.read_text(encoding="utf-8") if lst.is_file() else ""
        if body and not body.endswith("\n"):
            body += "\n"
        lst.write_text(body + "\n".join(added) + "\n", encoding="utf-8")
    elif not lst.is_file():
        lst.parent.mkdir(parents=True, exist_ok=True)
        lst.write_text("# forbidden names (tools/guard-names.py); one per line, re:<regex> for context matches\n",
                       encoding="utf-8")
    if not where:
        env = root / ".env.local"
        rc, _ = sh("git", "-C", str(root), "check-ignore", "-q", ".env.local")
        if rc != 0 and (root / ".git").exists():
            print(f"guard-names: REFUSED to write {env}: it is not git-ignored here (add `.env.local` to .gitignore)",
                  file=sys.stderr)
            return 2
        body = env.read_text(encoding="utf-8") if env.is_file() else ""
        env.write_text(body + ("" if not body or body.endswith("\n") else "\n") + f"FORBIDDEN_NAMES_FILE={lst}\n",
                       encoding="utf-8")
        print(f"guard-names: wrote FORBIDDEN_NAMES_FILE={lst} into {env}")
    stale = stale_hooks(root) if (root / ".git").is_dir() else []
    if stale:
        from gitbash import BASH   # Git Bash on Windows, where a bare "bash" is WSL's launcher (#109)
        r = subprocess.run([BASH, str(root / "tools/install-hooks.sh")], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        print(f"guard-names: hooks {', '.join(stale)} were missing or stale: ran tools/install-hooks.sh "
              f"({'ok' if r.returncode == 0 else 'FAILED: ' + (r.stdout + r.stderr)[-300:]})")
        if r.returncode:
            return 2
    return 1 if run_check(root, lst, a.quiet) else 0


def check(a, root):
    src = gather(a)
    lst, _where = list_path(root)
    if not lst.is_file():
        print(f"guard-names: no list at {lst}: run `python3 tools/guard-names.py ensure` first", file=sys.stderr)
        return 2
    existing = [l.strip() for l in lst.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    added, _notes = plan(src, root, existing)
    if added:
        print(f"guard-names: the list does not name {len(added)} of these mods' names: run "
              f"`python3 tools/guard-names.py ensure` with the same arguments", file=sys.stderr)
        return 1
    print(f"guard-names: the list names every mod given ({len(existing)} line(s))")
    return 0


# ------------------------------------------------------------------------------------------------- self-check

def self_check():
    import contextlib, io
    with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
        ok = _self_check()
    if not ok:
        print(out.getvalue()[-4000:])
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def _self_check():
    ok = True

    def expect(c, msg):
        nonlocal ok
        if not c:
            ok = False; print("FAIL:", msg, file=sys.__stdout__)
    tmp = pathlib.Path(tempfile.mkdtemp())
    try:
        # a scratch "this repository": a git repo with the two scripts ensure runs, and a README using ordinary words
        mm = tmp / "mmvu"; (mm / "tools").mkdir(parents=True)
        for f in ("install-hooks.sh", "check-no-ip.py", "python.sh"):
            shutil.copy(ROOT / "tools" / f, mm / "tools" / f)
        (mm / ".gitignore").write_text(".env.local\n", encoding="utf-8")
        (mm / "README.md").write_text("An arena mod port.\n", encoding="utf-8")
        for i in range(3):
            (mm / f"doc{i}.md").write_text("the arena is a word here\n", encoding="utf-8")
        sh("git", "init", "-q", str(mm)); sh("git", "-C", str(mm), "add", "-A")
        sh("git", "-C", str(mm), "remote", "add", "origin", "https://github.com/maintainer/mmvu")
        # the mod: an examplemod fixture, plus a dependency whose id is the ordinary word "arena"
        mod = tmp / "examplemod"; (mod / "src/main/resources/META-INF").mkdir(parents=True)
        (mod / "src/main/java/com/example/examplemod").mkdir(parents=True)
        (mod / "src/main/resources/META-INF/neoforge.mods.toml").write_text(
            '[[mods]]\nmodId="examplemod"\ndisplayName="Example Mod Deluxe"\nauthors="Ann Example, Exa"\n', encoding="utf-8")
        (mod / "gradle.properties").write_text("mod_id=examplemod\nmod_group_id=com.example.examplemod\n"
                                               "archives_name=examplemod-neoforge\n", encoding="utf-8")
        (mod / "src/main/java/com/example/examplemod/ExampleMod.java").write_text(
            "package com.example.examplemod;\n@Mod(\"examplemod\")\npublic class ExampleMod {}\n", encoding="utf-8")
        sh("git", "init", "-q", str(mod))
        sh("git", "-C", str(mod), "remote", "add", "origin", "https://github.com/maintainer/ExampleModFork.git")
        dep = tmp / "arena"; (dep / "src/main/resources/META-INF").mkdir(parents=True)
        (dep / "src/main/resources/META-INF/mods.toml").write_text(
            '[[mods]]\nmodId="arena"\ndisplayName="Arena"\n[[dependencies.arena]]\nmodId="zzotherlib"\n', encoding="utf-8")
        expect(("modId", "zzotherlib") not in derive_repo(dep), "a dependency table's modId is another mod: not derived")
        got = candidates(derive_repo(mod), "maintainer")
        vals = {v for _k, v in got}
        expect({"examplemod", "com.example.examplemod", "com/example/examplemod", "Example Mod Deluxe", "Ann Example",
                "Exa", "maintainer/ExampleModFork", "ExampleModFork", "examplemod-neoforge"} <= vals,
               f"derivation from toml, gradle.properties, package and remote: {sorted(vals)}")
        expect("maintainer" not in {v.lower() for v in vals}, "this repository's maintainer is never a name")
        lst = tmp / "private" / "names.txt"
        lst.parent.mkdir()
        lst.write_text("# hand-made\nzzkeepme\nre:\\bqqold\\b\n", encoding="utf-8")
        old_env = os.environ.get("FORBIDDEN_NAMES_FILE")
        os.environ.pop("FORBIDDEN_NAMES_FILE", None)
        ns = argparse.Namespace(mod_repo=[str(mod), str(dep)], jar=[], dry_run=False, quiet=True)
        try:
            # 1. the list sits inside a repository: refused, nothing written
            inrepo = mod / "names.txt"
            (mm / ".env.local").write_text(f"FORBIDDEN_NAMES_FILE={inrepo}\n", encoding="utf-8")
            expect(ensure(ns, mm) == 2 and not inrepo.exists(), "a list inside a git repository is refused")
            # 2. dry run: nothing written anywhere
            (mm / ".env.local").unlink()
            os.environ["FORBIDDEN_NAMES_FILE"] = str(lst)
            before = lst.read_text(encoding="utf-8")
            ns.dry_run = True
            ensure(ns, mm)
            expect(lst.read_text(encoding="utf-8") == before and not (mm / ".git/hooks/pre-push").exists(),
                   "--dry-run writes no list and installs no hook")
            ns.dry_run = False
            # 3. the real run: merged at the end, hook installed, .env.local untouched (the environment names the list)
            rc = ensure(ns, mm)
            body = lst.read_text(encoding="utf-8")
            lines = body.splitlines()
            expect(body.startswith(before), "every existing line kept, in its order, at the top")
            expect("examplemod" in lines and "com.example.examplemod" not in lines,
                   f"a name an earlier line already matches is not added again: {lines}")
            expect(r"re:\bexa\b" in lines, f"a short id becomes a re:\\b..\\b line: {lines}")
            expect(r"re:\barena\b" not in lines and r"re:\barena:[a-z0-9_/.]" in lines,
                   f"an ordinary word this repo uses is kept only in mod-specific forms: {lines}")
            expect(not any(l.lower() == "arena" for l in lines), "an ordinary-word display name is left out")
            expect((mm / ".git/hooks/pre-push").is_file() and not stale_hooks(mm), "the hook is installed when missing")
            expect(rc == 0, f"the scratch tree is clean under the merged list (exit {rc})")
            expect(not (mm / ".env.local").exists(), ".env.local untouched when the environment names the list")
            # 4. idempotent
            rc2 = ensure(ns, mm)
            expect(lst.read_text(encoding="utf-8") == body and rc2 == 0, "a second run adds nothing")
            expect(check(ns, mm) == 0, "check: the list names every mod given")
            # 5. no list configured: the default path, and .env.local gets the line
            os.environ.pop("FORBIDDEN_NAMES_FILE")
            home = tmp / "home"
            global DEFAULT_LIST
            saved, DEFAULT_LIST = DEFAULT_LIST, str(home / ".mmvu/forbidden-names.txt")
            try:
                ns.mod_repo = [str(mod)]
                expect(check(ns, mm) == 2, "check without a list exits 2")
                ensure(ns, mm)
                expect((home / ".mmvu/forbidden-names.txt").is_file() and
                       f"FORBIDDEN_NAMES_FILE={home / '.mmvu/forbidden-names.txt'}" in (mm / ".env.local").read_text(encoding="utf-8"),
                       "unset: the default list is created and .env.local names it")
                # a stale hook is replaced
                (mm / ".git/hooks/pre-push").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                expect(stale_hooks(mm) == ["pre-push"], "an edited hook is stale")
                ensure(ns, mm)
                expect(not stale_hooks(mm), "a stale hook is reinstalled")
            finally:
                DEFAULT_LIST = saved
            # 6. a name already in the tree surfaces: exit 1
            (mm / "leak.md").write_text("ported examplemod\n", encoding="utf-8"); sh("git", "-C", str(mm), "add", "-A")
            os.environ["FORBIDDEN_NAMES_FILE"] = str(lst)
            expect(ensure(ns, mm) == 1, "a listed name already in the tree fails the final check")
        finally:
            if old_env is None:
                os.environ.pop("FORBIDDEN_NAMES_FILE", None)
            else:
                os.environ["FORBIDDEN_NAMES_FILE"] = old_env
        # a jar gives its toml and package
        jar = tmp / "x.jar"
        with zipfile.ZipFile(jar, "w") as z:
            z.writestr("META-INF/neoforge.mods.toml", 'modId="examplemod"\n')
            z.writestr("com/example/examplemod/A.class", b"")
            z.writestr("com/example/examplemod/b/B.class", b"")
        expect(("package", "com.example.examplemod") in derive_jar(jar) and ("modId", "examplemod") in derive_jar(jar),
               "--jar: toml and package")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", nargs="?", choices=("ensure", "check"))
    ap.add_argument("--mod-repo", action="append", help="a mod checkout (repeat for every fork it depends on)")
    ap.add_argument("--jar", action="append", help="a mod jar, when there is no checkout")
    ap.add_argument("--dry-run", action="store_true", help="write nothing; print what would be added")
    ap.add_argument("--quiet", action="store_true", help="print check-no-ip's output only when it fails")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not a.cmd:
        ap.error("ensure or check")
    return ensure(a, ROOT) if a.cmd == "ensure" else check(a, ROOT)


if __name__ == "__main__":
    sys.exit(main())
