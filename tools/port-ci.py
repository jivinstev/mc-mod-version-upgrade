#!/usr/bin/env python3
"""Install a port's CI (and tag-driven release) into a fork's port branch, as a commit of its own.

    python3 tools/port-ci.py --repo <fork checkout> --modid <modid> [--upstream <author repo URL>]
                             [--dep owner/repo@branch ...] [--gatec launch,spawn,battle,gauntlet] [--no-commit]

The last step of every port: the fork's branch then shows, on every push, that the port builds with the
author's own Gradle build and passes the gates it was held to (tools/ci-gates.py, no model involved); and a
tag `<mod>-<version>-mc<minecraft>` publishes the author-built JARs as a GitHub pre-release once the gates
pass. Everything is read from the repository rather than guessed: the target from gradle.properties, the
Java toolchain from build.gradle, the author's branch from origin/HEAD (else `git ls-remote --symref origin HEAD`,
else `main` with a warning). --dep names a sibling port this one
compiles against (published to mavenLocal by its own build first, as during the port).

Several mods in one repository (opt-in; without it the workflow is byte-for-byte what it was):
  --mod-dir DIR                one mod of a repository that ships several, each a folder with its own settings.gradle,
                               build.gradle and gradle.properties: writes .github/workflows/port-ci-<NAME>.yml, which
                               builds and gates DIR (the nearest gradlew above it when it has none), releases
                               <NAME>-<mod_version>-mc<mc> from DIR/gradle.properties, promotes only <NAME>-* tags and
                               publishes with port-publish.py --mod NAME. --modid defaults to DIR's mod_id. Run per mod.
  --tag-name NAME              the mod's name in its tags and workflow file (default: DIR's folder name).
  --also-path PATH (repeat)    a folder or file the mod builds from (a shared folder): a push to it runs this workflow.
  `promote --mod NAME` dispatches port-ci-<NAME>.yml.

A release gate of the fork's own (opt-in, a file rather than an option; without it the workflow is byte-for-byte what it
was): .github/port-release-gate.json, {"schema": 1, "script": "<path>.py|.sh", "args": [...], "timeout_minutes": N},
read here and refused when bad (release_gate), adds a `release-gate` job that runs the script as an argv on the release
commit; the release needs it to succeed (release_gate_values).

Per-platform jars (both opt-in; without them the workflow is byte-for-byte what it was, tools/golden/port-ci-*.yml):
  --variant-jar GLOB (repeat)  a mod whose build ships several jars (e.g. '*_mr.jar', '*_cf.jar'): a `shipped jars`
                               job builds once and boots EACH in a real NeoForge server (tools/shipped-gate.py, the
                               --dep-jar jars as --deps); the release needs it like the gates.
  --dep-jar-variant SPEC       a dependent of such a mod: another asset of a --dep-jar dependency (same repo, tag and
                               G:A:V), sha256-pinned; one more leg `gates (full, <label>)` runs the build and every
                               gate with it in mavenLocal instead of the default asset.

    python3 tools/port-ci.py check --repo <fork checkout> [--kit-version N]

`check` warns -- never blocks, exit 0 -- when the checkout's installed workflow pins a Port CI kit older than the newest
published (a fork only gets a new gate from the first kit that ships it), its push trigger ignores none of the round's
records, or it was rendered before the from_run / dep_override workflow_dispatch inputs, or it does not run the release gate
the checkout's .github/port-release-gate.json names (or runs one it no longer has), and prints the command that regenerates it at the newest kit with its own options read back (on a version-branch
fork, tools/fork-kind.py detect, `fork-branches.py ci --head --run` instead). It is the check
tools/fork-branches.py ci-check makes for a version-branch fork's head branch (stale_workflow), for a single-version
fork's port branch.

    python3 tools/port-ci.py promote --fork <owner/repo> --tag <release tag> --played-by <github login>
                             [--notes "<what was played>"] [--branch <port branch>] [--print]
                             [--with-dependency <owner/repo> ...] [--repo <local clone>]

`promote` turns a pre-release into a full Release once a person has played it. It does not edit the release itself:
it dispatches the fork's own Port CI workflow with promote=<tag>, which checks the tag is a pre-release that
workflow published from that branch and rewrites "Pre-release until played by a person." with who played it and
when. It runs `gh workflow run` when gh is logged in; otherwise (or with --print) it prints the dispatch, which an
agent can send through its GitHub connector (workflow port-ci.yml, ref = the branch, inputs promote / played_by /
played_notes). A nightly or unattended run must never promote: promotion is the "a person played it" gate.
Don't exceed your dependency: a dependent may cut pre-releases pinned to a dependency's pre-release (the pair is what
a person plays), but `promote` refuses while a dependency the branch's workflow pins (--dep-jar) is still a
pre-release, unless --with-dependency names it -- the person promotes that dependency together with this one. The
check is best effort: it reads the workflow with `gh api` when gh is logged in, else from a local clone of the fork
(--repo, default the current directory); when neither can, it says so and dispatches as before.

The workflow downloads the Port CI kit (tools/port-ci-kit.py) -- a release asset holding only the gate runner and
what it needs -- pinned by version and sha256, rather than checking this repository out. Standard library only.
"""
import argparse, json, os, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates/upstream-harness/port-ci.yml"
SHIPPED_TEMPLATE = ROOT / "templates/upstream-harness/port-ci-shipped.yml"
GOLDEN = ROOT / "tools/golden"
WORKFLOW = ".github/workflows/port-ci.yml"


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def props(repo):
    p = pathlib.Path(repo) / "gradle.properties"
    return dict(re.findall(r"(?m)^\s*([\w.]+)\s*=\s*(.*?)\s*$", p.read_text(encoding="utf-8"))) if p.exists() else {}


def java_version(repo, mc, project=None):
    # a fork's version branch (tools/fork-branches.py) sets its toolchain in .port/<mc>/branch.properties (branch.gradle),
    # at the repository root; else the Gradle project's (one mod's folder, --mod-dir) build.gradle
    bp = pathlib.Path(repo) / ".port" / mc / "branch.properties"
    jv = re.search(r"(?m)^java_version\s*=\s*(\d+)\s*$", bp.read_text(encoding="utf-8")) if bp.is_file() else None
    if jv:
        return jv.group(1)
    g = (pathlib.Path(project or repo) / "build.gradle").read_text(encoding="utf-8", errors="replace")
    m = re.search(r"JavaLanguageVersion\.of\(\s*(\d+)\s*\)", g)
    return m.group(1) if m else ("21" if mc.startswith("1.21") else "25")


def migrator_repo():
    url = git(ROOT, "remote", "get-url", "origin").stdout.strip()
    m = re.search(r"github\.com[/:]([\w.-]+/[\w.-]+?)(?:\.git)?$", url)
    if not m:
        sys.exit(f"cannot read the migrator's GitHub repository from {url!r}")
    return m.group(1)


def _get(url, accept=None):
    import urllib.request
    headers = {"User-Agent": "port-ci", **({"Accept": accept} if accept else {})}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
        return r.read().decode("utf-8")


def default_branch(repo, fallback="main", say=None, ref=False):
    """The name of origin's default branch (the authors' base): refs/remotes/origin/HEAD, which a fresh clone in a
    cloud session may lack (weekly round 2026-10-19: it fell back to `main`, and the first PR run failed); then
    `git ls-remote --symref origin HEAD`; only then `fallback`, with a warning naming the fix. ref: a found branch
    as `origin/<name>` (the fallback as given)."""
    say = say or (lambda m: print(m, file=sys.stderr))
    r = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip() if ref else r.stdout.strip().split("/", 1)[-1]
    try:
        r = subprocess.run(["git", "-C", str(repo), "ls-remote", "--symref", "origin", "HEAD"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=60,
                           env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
        m = re.search(r"(?m)^ref: refs/heads/(\S+)\s+HEAD$", r.stdout) if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        m = None
    if m:
        say(f"port-ci: this clone has no origin/HEAD; origin's default branch, read with git ls-remote: {m.group(1)} "
            "(git remote set-head origin -a records it)")
        return f"origin/{m.group(1)}" if ref else m.group(1)
    say(f"port-ci: WARNING: cannot tell origin's default branch (no origin/HEAD, and git ls-remote --symref origin HEAD "
        f"named none): assuming {fallback}; fix: git remote set-head origin -a")
    return fallback


KIT_FLOOR = 1          # where the probe starts when no workflow pins a kit yet


def pinned_kits(texts):
    """The Port CI kit versions the workflow texts download (port-ci-kit-vN)."""
    return sorted({int(k) for t in texts for k in re.findall(r"port-ci-kit-v(\d+)", t or "")})


def probe_newest_kit(repo, floor, get=None, limit=200):
    """The newest Port CI kit found by its release download (port-ci-kit-vN/SHA256SUMS), counting up from `floor`
    (the highest kit a workflow already pins): for a session that can download github.com releases but not read
    api.github.com (403). -> N, or None when not even `floor` downloads."""
    get = get or _get
    start, best = max(int(floor or KIT_FLOOR), 1), None
    for n in range(start, start + limit):
        try:
            get(f"https://github.com/{repo}/releases/download/port-ci-kit-v{n}/SHA256SUMS")
        except Exception:      # noqa: BLE001 -- the first kit above the newest found ends the count
            if best is not None or n >= start + 2:
                break
            continue
        best = n
    return best


def kit_pin(repo, version=None, sha=None, floor=None, get=None, say=None):
    """(version, sha256) of the Port CI kit the workflow downloads: the newest published kit unless a version is
    given, and the sha256 its release's own SHA256SUMS states unless one is given. A fork's CI then trusts a
    checksum, not a download (tools/port-ci-kit.py builds kits reproducibly, so `port-ci-kit.py sha` on the
    release commit cross-checks it). When the release list cannot be read (the API refused: weekly round 2026-10-19,
    a 403 where release downloads worked), the newest kit is found by probing the downloads upward from `floor` (the
    highest kit the fork's workflows pin), and `say` is told so."""
    import json
    get = get or _get
    say = say or (lambda m: print(m, file=sys.stderr))
    if version is None:
        try:
            rels = json.loads(get(f"https://api.github.com/repos/{repo}/releases?per_page=100"))
        except Exception as e:      # noqa: BLE001 -- said, then the download probe
            found = probe_newest_kit(repo, floor, get)
            if found is None:
                sys.exit(f"cannot list {repo}'s releases ({e}), and no port-ci-kit-vN/SHA256SUMS downloads from kit "
                         f"{floor or KIT_FLOOR} up: pass --kit-version N")
            say(f"port-ci: FALLBACK: cannot list {repo}'s releases ({e}); the newest Port CI kit was found by probing "
                f"its release downloads upward from kit {floor or KIT_FLOOR}: kit v{found} (pass --kit-version N to choose)")
            rels = [{"tag_name": f"port-ci-kit-v{found}"}]
        vs = [int(r["tag_name"].rsplit("v", 1)[1]) for r in rels
              if re.fullmatch(r"port-ci-kit-v\d+", r.get("tag_name", ""))]
        if not vs:
            sys.exit(f"{repo} has published no Port CI kit yet (merge to main publishes one)")
        version = max(vs)
    if sha is None:
        sums = get(f"https://github.com/{repo}/releases/download/port-ci-kit-v{version}/SHA256SUMS")
        m = re.search(rf"^([0-9a-f]{{64}})\s+port-ci-kit-v{version}\.tar\.gz$", sums, re.M)
        if not m:
            sys.exit(f"port-ci-kit-v{version}'s SHA256SUMS names no port-ci-kit-v{version}.tar.gz")
        sha = m.group(1)
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        sys.exit(f"not a sha256: {sha!r}")
    return int(version), sha


def newest_kit(texts, say=None):
    """The newest published Port CI kit (kit_pin), its download probe starting at the highest kit `texts` (workflow
    texts) pin. Raises (or exits) when it cannot be read: the caller says so and compares nothing else on the kit."""
    return kit_pin(migrator_repo(), floor=max(pinned_kits(texts), default=None), say=say)[0]


RECORD_PUSHES = (".port/KNOWN-ISSUES.json", "README.md")     # the head's round commits: the ledger, the Ports block


def ignores_records(wf, need=None):
    """Does the workflow's push trigger paths-ignore the head's round records (the ledger and the root README; `need`:
    other paths, a version branch's records)? A workflow port-ci.py rendered before that line existed runs the full Port
    CI for every ledger or Ports-block commit."""
    push = wf.split("\n  push:\n", 1)[1].split("\n  pull_request:", 1)[0] if "\n  push:\n" in wf else ""
    if re.search(r"(?m)^\s*paths: \[", push) and not re.search(r"(?m)^\s*paths-ignore:", push):
        return True     # one mod of several (--mod-dir): its push runs only for its own paths, never the root records
    m = re.search(r"(?m)^\s*paths-ignore: (\[.*\])\s*$", push)
    try:
        ign = json.loads(m.group(1)) if m else []
    except ValueError:
        ign = []
    return all(f in ign for f in (need if need is not None else RECORD_PUSHES))


REUSE_INPUTS = ("from_run", "dep_override")     # workflow_dispatch inputs every current workflow has


def missing_inputs(wf):
    """The REUSE_INPUTS the workflow's workflow_dispatch does not declare: a workflow rendered before them can neither
    release an earlier run's jars (from_run) nor run the dependent gate (dep_override)."""
    disp = wf.split("\n  workflow_dispatch:\n", 1)[1].split("\npermissions:", 1)[0] if "\n  workflow_dispatch:\n" in wf else ""
    return [i for i in REUSE_INPUTS if f"\n      {i}:\n" not in disp]


JAR_DIGESTS_MARK = "::notice title=jar sha256::"
DIGESTS_STALE = ("it annotates no jar sha256 (rendered before the jar digests): a cloud round, whose proxy denies the "
                 "artifact storage, cannot name the library's candidate jar to its dependent's CI")


def missing_digests(wf):
    """A workflow with the dispatch inputs but rendered before its gates job annotated each jar's sha256 (round.py
    run_jar_digests reads them over the API; the artifact storage is unreachable from a cloud session)."""
    return "\n  workflow_dispatch:\n" in wf and JAR_DIGESTS_MARK not in wf


def stale_workflow(wf, newest, gate=None):
    """Why a port branch's installed workflow is out of date -- the one check for a single-version fork's port branch
    (`port-ci.py check`) and a version-branch fork's head (tools/fork-branches.py ci-check): it pins a Port CI kit older
    than `newest` (None: not compared), or its push trigger ignores none of the round's records (every known-issues
    ledger or README Ports-block commit is then a full Port CI run), or it lacks the from_run / dep_override
    workflow_dispatch inputs (missing_inputs) or the jar digest annotations (missing_digests), or -- when `gate` is the
    text of the fork's .github/port-release-gate.json ("" when it has none; None: not known, not compared) -- it does
    not run that release gate (release_gate_stale). -> [reasons], [] when current."""
    why = []
    kits = [int(k) for k in re.findall(r"port-ci-kit-v(\d+)", wf)]
    if newest is not None and (not kits or min(kits) < newest):
        why.append(f"its workflow pins Port CI kit {min(kits) if kits else '?'}, older than kit {newest}")
    if not ignores_records(wf):
        why.append("its push trigger ignores no records, so every known-issues ledger or README Ports-block commit runs "
                   "the full Port CI there")
    if missing_inputs(wf):
        why.append("it was rendered before the from_run/dep_override inputs (no " + ", ".join(missing_inputs(wf))
                   + "): it can neither release an earlier run's jars nor run the dependent gate")
    elif missing_digests(wf):
        why.append(DIGESTS_STALE)
    if gate is not None:
        why += release_gate_stale(wf, gate)
    return why


def check(repo, file=None, kit_version=None, say=print):
    """`port-ci.py check`: warn (never block: exit 0) when the checkout's installed workflow is out of date
    (stale_workflow), with the command that regenerates it at the newest kit -- the workflow's own options read back
    (recover_args), only the kit moved. kit_version: the kit to compare with (default: the newest published)."""
    repo = pathlib.Path(repo)
    f = pathlib.Path(file) if file else repo / WORKFLOW
    if not f.is_file():
        say(f"port-ci: check: {f} does not exist (the port-fork skill installs it)"); return 0
    wf = f.read_text(encoding="utf-8", errors="replace")
    newest = kit_version
    if newest is None:
        try:
            newest = newest_kit([wf], say=say)
        except (Exception, SystemExit) as e:    # noqa: BLE001 -- offline: say so, compare the records ignore only
            say(f"port-ci: check: cannot read the newest Port CI kit ({e}); pass --kit-version N")
    gf = repo / RELEASE_GATE_FILE
    why = stale_workflow(wf, newest, gf.read_text(encoding="utf-8", errors="replace") if gf.is_file() else "")
    branch = git(repo, "symbolic-ref", "-q", "--short", "HEAD").stdout.strip() or "the port branch"
    hand = hand_gradle_args(wf, repo, props(repo).get("minecraft_version"))
    if hand:
        say(f"port-ci: check: WARNING {branch}: {hand}")
    if not why:
        say(f"port-ci: check: {branch}: its workflow is current" + (f" (kit {newest})" if newest else "")); return 0
    if _load_tool("fork_kind", "fork-kind.py").detect(repo)[0] == "version-branches":
        # a version-branch fork's head (or a version branch): fork-branches regenerates it from itself, branch-aware
        say(f"port-ci: check: WARNING {branch}: " + "; ".join(why) + f" -- regenerate: python3 tools/fork-branches.py "
            f"ci --repo {repo} --head --run (the head; a version branch: ci --repo {repo} --all --modid <modid> --run), "
            "then push it")
        return 0
    try:
        rec = recover_args(wf)
        args = [x for i, x in enumerate(rec) if x not in ("--kit-version", "--kit-sha")
                and (i == 0 or rec[i - 1] not in ("--kit-version", "--kit-sha"))]
        import shlex
        how = " -- regenerate: " + shlex.join(["python3", "tools/port-ci.py", "--repo", str(repo), *args]) + ", then push it"   # exec-ok: printed for a person's shell
    except SystemExit:
        how = " -- regenerate it with tools/port-ci.py (its options could not be read back)"
    say(f"port-ci: check: WARNING {branch}: " + "; ".join(why) + how)
    return 0


GATE_ARGS_FILE = "gate-gradle-args.txt"     # tools/port_gates.py GATE_GRADLE_ARGS


def lower_gate_args(repo, mc):
    """A lower version's .port/<v>/gate-gradle-args.txt, which this version inherits (tools/port_gates.py port_file),
    or None."""
    key = lambda v: tuple(int(x) for x in v.split("."))
    d0 = pathlib.Path(repo) / ".port"
    if not mc or not re.fullmatch(r"\d+(\.\d+)+", str(mc)) or not d0.is_dir():
        return None
    lower = sorted((key(d.name), d / GATE_ARGS_FILE) for d in d0.glob("*")
                   if re.fullmatch(r"\d+(\.\d+)+", d.name) and key(d.name) < key(str(mc)) and (d / GATE_ARGS_FILE).is_file())
    return lower[-1][1] if lower else None


def hand_gradle_args(wf, repo, mc):
    """A warning when the installed workflow sets PORT_GRADLE_ARGS itself (a hand edit: the template never does) and the
    fork has no .port/<mc>/gate-gradle-args.txt: regenerating drops the edit, and the gates would run the whole suite.
    The committed file, read by every Port CI run (ci-gates.py --port-ci), is where those arguments go. None otherwise."""
    m = re.search(r"(?m)^\s*PORT_GRADLE_ARGS:\s*(.*)$", wf)
    rel = f".port/{mc}/{GATE_ARGS_FILE}"
    if not m or (pathlib.Path(repo) / rel).is_file() or lower_gate_args(repo, mc):
        return None
    return (f"the installed workflow sets PORT_GRADLE_ARGS by hand ({m.group(1).strip() or 'a block'}); a regenerated "
            f"workflow does not: put each argument on its own line in {rel} and commit it, so every Port CI gate run "
            "reads it")


# The player-install bootstrap the player-install job runs (a release of the migrator repository, never replaced): the
# tag and its install.sh's sha256. Move both together, to a release whose acceptance passed (player-install.yml).
PLAYER_INSTALL = {"PLAYER_INSTALL_TAG": "player-install-v4", "PLAYER_INSTALL_SHA256":
                  "924d562230ab32a39dd3f20f9d1ff54887085c82a67dc986727ec2a625e6e102"}


def render(text, values):
    values = {**PLAYER_INSTALL, **values}
    out = re.sub(r"(?<!\$)\{\{([A-Z0-9_]+)\}\}", lambda m: values.get(m.group(1), m.group(0)), text)
    left = re.findall(r"(?<!\$)\{\{[A-Z0-9_]+\}\}", out)
    if left:
        raise SystemExit(f"unfilled template placeholders: {left}")
    return out


def pre_build(deps):
    steps = []
    for d in deps:
        m = re.fullmatch(r"([\w.-]+/([\w.-]+))@([\w./-]+)", d)
        if not m:
            raise SystemExit(f"--dep must be owner/repo@branch, got {d!r}")
        full, name, branch = m.groups()
        steps += [f"      - uses: actions/checkout@v4",
                  f"        with:",
                  f"          repository: {full}",
                  f"          ref: {branch}",
                  f"          path: .port-deps/{name}",
                  f"      - name: Build {name} ({branch}) into mavenLocal",
                  f"        run: cd .port-deps/{name} && bash gradlew --console=plain publishToMavenLocal"]
    return "\n".join(steps)


DEP_JAR = re.compile(r"([\w.-]+/[\w.-]+)@([^/=]+)/([^/=]+\.jar)=([\w.-]+):([\w.-]+):([\w.+-]+)")


def dep_jars(specs, sha_of=None):
    """Steps that put a sibling port's RELEASED jar into mavenLocal, pinned by sha256 like the kit:
    owner/repo@tag/asset.jar=group:artifact:version. Prefer this to --dep once the dependency has a release: the
    dependent's CI then builds against exactly the jar people download, not a branch that can move under it --
    and not against whatever artifactId the dependency's build happens to publish (a build with no
    rootProject.name publishes under its checkout directory's name)."""
    steps = []
    for d in specs:
        m = DEP_JAR.fullmatch(d)
        if not m:
            raise SystemExit(f"--dep-jar must be owner/repo@tag/asset.jar=group:artifact:version, got {d!r}")
        repo, tag, asset, g, art, ver = m.groups()
        url = f"https://github.com/{repo}/releases/download/{tag}/{asset}"
        sha = (sha_of or _sha_url)(url)
        dest = "$HOME/.m2/repository/" + g.replace(".", "/") + f"/{art}/{ver}"
        pom = (f'<project><modelVersion>4.0.0</modelVersion><groupId>{g}</groupId><artifactId>{art}</artifactId>'
               f'<version>{ver}</version><packaging>jar</packaging></project>')
        steps += [f"      - name: {art} {ver} from the {tag} release into mavenLocal (pinned by sha256)",
                  f"        run: |",
                  f'          mkdir -p "{dest}"',
                  f'          curl -fsSL -o "{dest}/{art}-{ver}.jar" "{url}"',
                  f'          echo "{sha}  {dest}/{art}-{ver}.jar" | sha256sum -c -',
                  f"          echo '{pom}' > \"{dest}/{art}-{ver}.pom\""]
    return "\n".join(steps)


VARIANT_GLOB = re.compile(r"[\w.*?+\[\]-]+\.jar")
SAFE_ASSET = re.compile(r"[\w.+-]+\.jar")


def _m2_jar(g, art, ver):
    return ".m2/repository/" + g.replace(".", "/") + f"/{art}/{ver}/{art}-{ver}.jar"


def variant_label(default_asset, asset):
    """A short, stable name for an alternate asset: what its file name has that the default's does not
    (lib-1.0_mr.jar / lib-1.0_cf.jar -> "cf"), so the check name survives a re-pin to a new version."""
    a, b = default_asset[:-4], asset[:-4]
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    j = 0
    while j < min(len(a), len(b)) - i and a[-1 - j] == b[-1 - j]:
        j += 1
    return b[i:len(b) - j].strip("_.-+") or b


def dep_jar_variants(variants, dep_specs, sha_of=None):
    """--dep-jar-variant: [(label, steps)] -- per alternate asset of a --dep-jar dependency, a step that overwrites the
    default jar in mavenLocal (same coordinates, so the build and every gate run against it) on that matrix leg only.
    Pinned by sha256 exactly as dep_jars() pins the default. The curl line is written URL-first on purpose:
    tools/port-offer.py reads the DEFAULT pins (`curl -fsSL -o ... URL`) as the sibling jar to install."""
    defaults = [DEP_JAR.fullmatch(d).groups() for d in dep_specs if DEP_JAR.fullmatch(d)]
    out, seen = [], {}
    for d in variants:
        m = DEP_JAR.fullmatch(d)
        if not m:
            raise SystemExit(f"--dep-jar-variant must be owner/repo@tag/asset.jar=group:artifact:version, got {d!r}")
        repo, tag, asset, g, art, ver = m.groups()
        if not SAFE_ASSET.fullmatch(asset):
            raise SystemExit(f"--dep-jar-variant: asset {asset!r} has characters outside [A-Za-z0-9_.+-]")
        same = [x for x in defaults if (x[0], x[3], x[4], x[5]) == (repo, g, art, ver)]
        if not same:
            raise SystemExit(f"--dep-jar-variant {d}: no --dep-jar for {repo} with coordinates {g}:{art}:{ver}; a variant "
                             "names an ALTERNATE asset of a dependency already given with --dep-jar")
        if same[0][1] != tag:
            raise SystemExit(f"--dep-jar-variant {d}: the tag {tag!r} is not the --dep-jar's ({same[0][1]!r}); "
                             "re-pin both to the same release")
        if same[0][2] == asset:
            raise SystemExit(f"--dep-jar-variant {d}: {asset} is already the default --dep-jar asset; name the other one")
        if (repo, asset) in seen:
            raise SystemExit(f"--dep-jar-variant {d}: given twice")
        label = variant_label(same[0][2], asset)
        if label in seen.values():          # two libraries with a "_cf" variant each: fall back to the asset's name
            label = asset[:-4]
        if not re.fullmatch(r"[\w.+-]+", label) or label in seen.values():
            raise SystemExit(f"--dep-jar-variant {d}: cannot name its matrix leg uniquely (got {label!r})")
        seen[(repo, asset)] = label
        url = f"https://github.com/{repo}/releases/download/{tag}/{asset}"
        sha = (sha_of or _sha_url)(url)
        jar = "$HOME/" + _m2_jar(g, art, ver)
        out.append((label, "\n".join([
            f"      - name: {art} {ver} -- the {asset} variant from the {tag} release instead (pinned by sha256)",
            f"        if: matrix.variant == '{label}'",
            f"        run: |",
            f'          curl -fsSL "{url}" -o "{jar}"',
            f'          echo "{sha}  {jar}" | sha256sum -c -'])))
    return out


# A version branch's round records (the fork-versions skill: .port/<v>/GATES.md, REVIEW.md, COST.md, DEPS.md, DRIFT.md,
# TEST-ITEMS.md, RESIDUE.md, ...; .port/DECISIONS.md): Markdown outside src/, so never jar content (fork-branches.py
# GATES_DOC, port-publish.py GATES_DOC) and never read by a gate (ci-gates.py writes them; GATE_OUTPUTS). A push of
# only those -- a version's cost record, its manual-test items -- then runs no full Port CI, as a ledger-only push of
# the head does not. GitHub's `*` stops at `/`, so `.port/*/*.md` is one version's directory and never a deeper
# `.port/<v>/<mod>/` file, and src/ is never matched. Only on a version branch (the checkout holds
# .port/<mc>/branch.properties, fork-branches.py's version_branches rule -- a single-version fork may keep
# .port/<mc>/shipped-expect-<variant>.txt and is not one): a single-version fork's workflow stays byte-for-byte what it
# was (tools/golden/port-ci-single-*.yml); pull_request and tag runs are untouched (GitHub applies paths-ignore to
# branch pushes only), and workflow_dispatch -- the release -- always runs every gate.
VERSION_RECORDS_IGNORE = (".port/*.md", ".port/*/*.md")


def kind_values(values, owned=False):
    """The words that differ between a port and an OWNED repository (the mod author's own mod: `"own_mod": true` in
    .github/port-publish.json, fork-kind.py is_owned). A port's are unchanged byte for byte; an owned repository's
    pre-release says what it is, with no "unofficial port", no original and no author to disclaim."""
    t = values["TARGET"]
    if owned:
        intro = (f'            echo "**{values["MODID"]}** for {t}, built by this repository\'s own CI."\n'
                 '            echo "Same licence and credits as the source in this repository."\n')
        return dict(values, KIND_NOUN="mod", NOTES_INTRO=intro, TITLE_KIND=t)
    intro = (f'            echo "**Unofficial port** of {values["UPSTREAM"]} to {t}."\n'
             '            echo "Same licence and credits as the original; not an official release by the author."\n')
    return dict(values, KIND_NOUN="unofficial port", NOTES_INTRO=intro, TITLE_KIND=f"unofficial {t} port")


def records_values(values, version_branch=False):
    """RECORDS_IGNORE: the extra paths-ignore entries of a version branch's push trigger (VERSION_RECORDS_IGNORE);
    empty, so nothing moves, on a single-version fork's port branch."""
    return dict(values, RECORDS_IGNORE="".join(f', "{p}"' for p in VERSION_RECORDS_IGNORE) if version_branch else "")


def is_version_branch(repo, mc):
    """The checkout is a version branch: it holds .port/<mc>/branch.properties for its own Minecraft version
    (fork-branches.py version_branches). Not port_branch()'s looser .port/<mc>/ test: a single-version fork may keep
    .port/<mc>/shipped-expect-<variant>.txt there (port-fork, shipped-gate.py) and must not get the records ignore."""
    return bool(mc) and (pathlib.Path(repo) / ".port" / mc / "branch.properties").is_file()


# The dependent gate (workflow_dispatch input dep_override): a library's candidate jar built and gated against in place
# of the pinned one with the same group:artifact:version -- after PRE_BUILD (dep_jars, pre_build) and the variant steps
# (dep_jar_variants) put the pinned jars into mavenLocal, so it applies on every gates leg and in the shipped job. An
# entry is either form, space-separated:
#   owner/repo@tag/asset.jar=group:artifact:version@sha256          a release asset
#   owner/repo#RUN_ID/ARTIFACT/asset.jar=group:artifact:version@sha256   a jar in an Actions artifact of a workflow run of
#                                                                   that (public) repository, read with the run's token
# Each is validated strictly, downloaded and sha256-checked; an entry whose coordinates no pinned jar has is an error.
# The later steps get PORT_CI_DEP_OVERRIDE=group:artifact[,group:artifact...], the coordinates it replaced, so a build
# that checks its pinned jar's sha256 itself can accept exactly those (and say so) on this run only.
# Off (the step skipped) when the input is empty; a run with it never releases (the release job refuses, and a from_run
# release takes no workflow_dispatch run). The input reaches the script only through env, and the download URL is
# assembled from shell variables, so no tool that reads a workflow's pins (pinned_releases, port-offer.py,
# port-manifest.py, real-install.py) mistakes it for one.
DEP_OVERRIDE_STEP = r"""
      - name: Dependent gate -- candidate dependency jars instead of the pinned ones (dep_override)
        if: ${{ inputs.dep_override }}
        env:
          DEP_OVERRIDE: ${{ inputs.dep_override }}
          GH_TOKEN: ${{ github.token }}
        run: |
          set -euo pipefail
          set -f                       # the entries are split on spaces, never globbed
          id='[A-Za-z0-9][A-Za-z0-9_.-]*'
          gav="=([A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*):(${id}):([A-Za-z0-9][A-Za-z0-9_.+-]*)@([0-9a-f]{64})$"
          jar='([A-Za-z0-9][A-Za-z0-9._+-]*\.jar)'
          rel="^(${id}/${id})@([A-Za-z0-9][A-Za-z0-9_.+-]*)/${jar}${gav}"
          art="^(${id}/${id})#([0-9]{1,20})/([A-Za-z0-9._-]+)/${jar}${gav}"
          tmp="$RUNNER_TEMP/dep-override"; mkdir -p "$tmp"
          n=0
          for e in $DEP_OVERRIDE; do
            if [[ "$e" =~ $rel ]]; then
              repo=${BASH_REMATCH[1]}; tag=${BASH_REMATCH[2]}; run=""; an=""; asset=${BASH_REMATCH[3]}
              g=${BASH_REMATCH[4]}; a=${BASH_REMATCH[6]}; v=${BASH_REMATCH[7]}; sha=${BASH_REMATCH[8]}
            elif [[ "$e" =~ $art ]]; then
              repo=${BASH_REMATCH[1]}; tag=""; run=${BASH_REMATCH[2]}; an=${BASH_REMATCH[3]}; asset=${BASH_REMATCH[4]}
              g=${BASH_REMATCH[5]}; a=${BASH_REMATCH[7]}; v=${BASH_REMATCH[8]}; sha=${BASH_REMATCH[9]}
            else
              echo "::error::dep_override: an entry is neither owner/repo@tag/asset.jar=group:artifact:version@sha256 nor owner/repo#RUN_ID/ARTIFACT/asset.jar=group:artifact:version@sha256 (64 lower-case hex)"; exit 1
            fi
            dir="$HOME/.m2/repository/${g//.//}/$a/$v"
            if [ ! -f "$dir/$a-$v.jar" ]; then
              echo "::error::dep_override $g:$a:$v: no pinned dependency jar has these coordinates (it replaces one, never adds one)"; exit 1
            fi
            rm -f "$tmp/candidate.jar"
            if [ -z "$run" ]; then
              url="https://github.com/${repo}/releases/download/${tag}/${asset}"
              curl -fsSL "$url" -o "$tmp/candidate.jar"
              from="$repo@$tag"
            else
              if ! gh api "repos/$repo/actions/runs/$run/artifacts" --paginate --jq '.artifacts[] | [.id, .name, .expired] | @tsv' > "$tmp/artifacts.tsv"; then
                echo "::error::dep_override: the run's token could not read $repo's artifacts (run $run)"; exit 1
              fi
              aid=$(awk -F '\t' -v n="$an" '$2 == n && $3 == "false" { print $1; exit }' "$tmp/artifacts.tsv")
              if [ -z "$aid" ]; then
                echo "::error::dep_override: run $run of $repo has no unexpired artifact named $an"; exit 1
              fi
              if ! gh api "repos/$repo/actions/artifacts/$aid/zip" > "$tmp/artifact.zip"; then
                echo "::error::dep_override: the run's token could not read $repo's artifacts (artifact $an of run $run)"; exit 1
              fi
              if ! python3 -c 'import shutil, sys, zipfile
          z = zipfile.ZipFile(sys.argv[1]); m = [i for i in z.infolist() if i.filename.rsplit("/", 1)[-1] == sys.argv[2]]
          sys.exit(1) if len(m) != 1 else shutil.copyfileobj(z.open(m[0]), open(sys.argv[3], "wb"))' "$tmp/artifact.zip" "$asset" "$tmp/candidate.jar"; then
                echo "::error::dep_override: artifact $an of $repo's run $run holds no single $asset"; exit 1
              fi
              from="$repo run $run artifact $an"
            fi
            echo "$sha  $tmp/candidate.jar" | sha256sum -c -
            mv -f "$tmp/candidate.jar" "$dir/$a-$v.jar"
            [ -f "$dir/$a-$v.pom" ] || echo "<project><modelVersion>4.0.0</modelVersion><groupId>$g</groupId><artifactId>$a</artifactId><version>$v</version><packaging>jar</packaging></project>" > "$dir/$a-$v.pom"
            echo "Dependent gate: $g:$a:$v is $asset from $from (sha256 $sha), not the pinned jar" >> "$GITHUB_STEP_SUMMARY"
            ga="${ga:+$ga,}$g:$a"
            n=$((n+1))
          done
          [ "$n" -gt 0 ] || { echo "::error::dep_override names no entry"; exit 1; }
          echo "PORT_CI_DEP_OVERRIDE=$ga" >> "$GITHUB_ENV"
""".rstrip("\n")

# The release job's condition on the shipped job, with --variant-jar: it must succeed, or be skipped by from_run.
RELEASE_SHIPPED_IF = " && (needs.shipped.result == 'success' || (inputs.from_run && needs.shipped.result == 'skipped'))"


def variant_values(values, variant_jars=(), dep_variants=(), dep_specs=(), sha_of=None):
    """The placeholders of the two opt-in variant options. With neither, every one is empty (RELEASE_NEEDS stays
    `gates`) and the workflow is byte-for-byte the variant-free one (self-check: tools/golden/port-ci-*.yml).
      --variant-jar GLOB: a `shipped jars` job (templates/upstream-harness/port-ci-shipped.yml) that boots each built
        jar as shipped; the release needs it.
      --dep-jar-variant SPEC: one more gates leg per alternate dependency asset, `gates (full, <label>)`."""
    v = dict(values, GATES_VARIANTS="", GATES_VARIANT_NAME="", GATES_VARIANT_ARTIFACT="", GATES_VARIANT_JARS_IF="",
             VARIANT_STEPS="", SHIPPED_JOB="", RELEASE_NEEDS="gates", RELEASE_SHIPPED_IF="",
             DEP_OVERRIDE_STEP=DEP_OVERRIDE_STEP)
    legs = dep_jar_variants(dep_variants, dep_specs, sha_of)
    if legs:
        v.update(GATES_VARIANTS='\n        variant: [""]\n        include:' + "".join(
                     f'\n          - env: full\n            variant: "{label}"' for label, _ in legs),
                 GATES_VARIANT_NAME="${{ matrix.variant && format(', {0}', matrix.variant) || '' }}",
                 GATES_VARIANT_ARTIFACT="${{ matrix.variant && format('-{0}', matrix.variant) || '' }}",
                 GATES_VARIANT_JARS_IF=" && !matrix.variant",
                 VARIANT_STEPS="".join("\n" + steps for _, steps in legs))
    if variant_jars:
        for gl in variant_jars:
            if not VARIANT_GLOB.fullmatch(gl):
                raise SystemExit(f"--variant-jar must be a file glob under build/libs ending in .jar, of [A-Za-z0-9_.*?+[]-] "
                                 f"(e.g. '*_mr.jar'), got {gl!r}")
        if len(set(variant_jars)) != len(variant_jars):
            raise SystemExit("--variant-jar: the same glob given twice")
        deps = " ".join(_m2_jar(*DEP_JAR.fullmatch(d).groups()[3:]) for d in dep_specs if DEP_JAR.fullmatch(d))
        sv = dict(v, VARIANT_JARS=" ".join(variant_jars), SHIPPED_DEPS=deps)
        v.update(SHIPPED_JOB="\n\n" + render(SHIPPED_TEMPLATE.read_text(encoding="utf-8"), sv).rstrip("\n"),
                 RELEASE_NEEDS="[gates, shipped]", RELEASE_SHIPPED_IF=RELEASE_SHIPPED_IF)
    return v



# The fork's own release gate (issue #306), opt-in by a file in the fork, read when the workflow is generated:
#   .github/port-release-gate.json  {"schema": 1, "script": "tools/gate/gate.py", "args": ["release-gate"],
#                                    "timeout_minutes": 180}
# With it a `release-gate` job runs that script (python3 for .py, bash for .sh: an argv, never a shell string; every
# argument a quoted literal validated here) between the gates and the release, which needs it to SUCCEED: a failed,
# skipped or cancelled gate releases nothing. It holds contents: read and actions: write (to find or start a workflow
# run of its own), never the release's contents: write. Without the file every placeholder is empty and the workflow is
# byte for byte what it was (tools/golden/port-ci-*.yml).
RELEASE_GATE_FILE = ".github/port-release-gate.json"
RELEASE_GATE_ARG = re.compile(r"[A-Za-z0-9_.:=/@+-]+")
RELEASE_GATE_KEYS = {"schema", "script", "args", "timeout_minutes"}
RELEASE_GATE_TIMEOUT = (1, 360, 60)                 # minutes: least, most, default
RELEASE_GATE_IF = " && needs.release-gate.result == 'success'"
# the release-gate job's condition: the release's trigger (RELEASE_IF's run kinds) without any job's result -- it runs IN
# PARALLEL with the gates (no needs: a fork's full-tier gate can be long, a sharded run it starts and waits for), and
# the release needs both to succeed -- and never on a dep_override run (which never releases)
RELEASE_GATE_RUNS = ("!cancelled() && github.event_name != 'release' && (startsWith(github.ref, 'refs/tags/') || "
                     "inputs.release || inputs.from_run) && !inputs.dep_override")


def release_gate(repo=None, text=None):
    """The fork's release gate: None when it has no .github/port-release-gate.json, else {script, args,
    timeout_minutes}, validated -- SystemExit naming the file and what is wrong. `text`: the file's text (default: read
    from `repo`); `repo` None: the script's existence is not checked (stale_workflow may read the file at a git ref)."""
    if text is None:
        f = pathlib.Path(repo) / RELEASE_GATE_FILE
        if not f.is_file():
            return None
        text = f.read_text(encoding="utf-8", errors="replace")

    def bad(why):
        return SystemExit(f"{RELEASE_GATE_FILE}: {why} -- expected "
                          '{"schema": 1, "script": "<path>.py|.sh", "args": ["..."], "timeout_minutes": N}')
    try:
        cfg = json.loads(text)
    except ValueError as e:
        raise bad(f"not JSON ({e})")
    if not isinstance(cfg, dict):
        raise bad("not a JSON object")
    if set(cfg) - RELEASE_GATE_KEYS:
        raise bad("unknown key(s) " + ", ".join(sorted(set(cfg) - RELEASE_GATE_KEYS)))
    if cfg.get("schema") != 1 or isinstance(cfg.get("schema"), bool):
        raise bad(f"schema must be 1, got {cfg.get('schema')!r}")
    script = cfg.get("script")
    if not isinstance(script, str) or not script.endswith((".py", ".sh")):
        raise bad(f"script must be a .py or .sh file in the repository, got {script!r}")
    if not REL_PATH.fullmatch(script) or any(x in (".", "..") or x.startswith("-") for x in script.split("/")):
        raise bad(f"script must be a path inside the repository, relative to its root (no '..', no component "
                  f"starting with '-'), got {script!r}")
    if repo is not None and not (pathlib.Path(repo) / script).is_file():
        raise bad(f"script {script}: no such file in the repository")
    args = cfg.get("args", [])
    if not isinstance(args, list) or len(args) > 32:
        raise bad("args must be a list of at most 32 strings")
    for x in args:
        if not isinstance(x, str) or len(x) > 200 or not RELEASE_GATE_ARG.fullmatch(x):
            raise bad(f"each of args must be 1-200 characters of [A-Za-z0-9_.:=/@+-], got {x!r}")
    lo, hi, default = RELEASE_GATE_TIMEOUT
    t = cfg.get("timeout_minutes", default)
    if not isinstance(t, int) or isinstance(t, bool) or not lo <= t <= hi:
        raise bad(f"timeout_minutes must be a whole number of minutes from {lo} to {hi}, got {t!r}")
    return {"script": script, "args": list(args), "timeout_minutes": t}


def release_gate_argv(gate):
    """The gate step's command line: the interpreter, the script and its arguments, each a single-quoted literal (the
    charsets release_gate allows have no quote, `$`, `{` or space, so nothing in it is shell or an expression)."""
    return " ".join(["python3" if gate["script"].endswith(".py") else "bash",
                     *(f"'{x}'" for x in [gate["script"], *gate["args"]])])


def release_gate_values(values, gate=None):
    """RELEASE_GATE_JOB / RELEASE_GATE_IF, and the release's needs: empty and unchanged without a gate (byte for byte the
    old workflow); with one, the `release-gate` job -- no needs: it starts with the gates and runs alongside them, on
    every run that would release -- and the release needing both it and what it already needed to succeed. After
    variant_values, which sets RELEASE_NEEDS."""
    if not gate:
        return dict(values, RELEASE_GATE_JOB="", RELEASE_GATE_IF="")
    needs = values["RELEASE_NEEDS"]
    runs = RELEASE_GATE_RUNS
    s = gate["script"]
    job = f"""

  release-gate:
    # The fork's own check before a release ({RELEASE_GATE_FILE}, read when this workflow was
    # generated: regenerate it after changing that file). Its script is the fork's own code, trusted like its Gradle
    # build; it runs as an argv with the commit being released (GATE_SHA: on a from_run release this dispatch's commit,
    # the tree released) and may find or start a workflow run of this repository (actions: write), never holding the
    # release's contents: write. The release needs it to succeed: a failure, a skip or a cancel here releases nothing.
    # It runs on every run that would release (never a dep_override run), in parallel with the gates: no needs, so a
    # long gate (a sharded full tier it starts and waits for) does not wait for them, and the release needs both.
    if: ${{{{ {runs} }}}}
    runs-on: ubuntu-24.04
    timeout-minutes: {gate["timeout_minutes"]}
    permissions:
      contents: read
      actions: write
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{{{ github.sha }}}}
      - name: Release gate ({s})
        env:
          GATE_SHA: ${{{{ github.sha }}}}
          GATE_REF: ${{{{ github.ref_name }}}}
          GH_TOKEN: ${{{{ github.token }}}}
        run: |
          set +e                       # GITHUB_REPOSITORY: the runner's own
          log="$RUNNER_TEMP/release-gate.log"
          {release_gate_argv(gate)} 2>&1 | tee "$log"
          rc=${{PIPESTATUS[0]}}
          if [ "$rc" -ne 0 ]; then
            {{
              echo "### Release gate failed: {s} exited $rc, nothing released"
              echo
              echo '```'
              tail -n 50 "$log"
              echo '```'
            }} >> "$GITHUB_STEP_SUMMARY"
            echo "::error::release gate {s} exited $rc: nothing released"
            exit "$rc"
          fi
          echo "Release gate passed: {s} at $GATE_SHA" >> "$GITHUB_STEP_SUMMARY\""""
    return dict(values, RELEASE_GATE_JOB=job, RELEASE_GATE_IF=RELEASE_GATE_IF,
                RELEASE_NEEDS="[" + needs.strip("[]") + ", release-gate]")


def release_gate_stale(wf, text):
    """Why the workflow does not run the release gate the fork's .github/port-release-gate.json (its text: "" when the
    fork has none) describes -- [] when it does. A re-render reads the file again, so the regenerate command fixes it."""
    has = "\n  release-gate:\n" in wf
    if not text:
        return ["its workflow has a release-gate job but the fork has no " + RELEASE_GATE_FILE] if has else []
    try:
        gate = release_gate(None, text)
    except SystemExit as e:
        return [f"its {e}"]
    if not has:
        return [f"the fork has {RELEASE_GATE_FILE} but its workflow has no release-gate job"]
    job = wf.split("\n  release-gate:\n", 1)[1].split("\n\n  release:\n", 1)[0]
    if (f"\n    timeout-minutes: {gate['timeout_minutes']}\n" not in job
            or f"\n          {release_gate_argv(gate)} 2>&1 | tee" not in job):
        return [f"its release-gate job does not run what {RELEASE_GATE_FILE} says (script, args or timeout)"]
    return []


# A repository that ships SEVERAL mods (each a folder with its own settings.gradle, build.gradle and gradle.properties,
# one gradlew at the root, a shared folder they build from) gets one workflow per mod: --mod-dir DIR [--tag-name NAME]
# [--also-path PATH ...]. Without --mod-dir every placeholder below is what the single-mod workflow always had, byte for
# byte (tools/golden/port-ci-*.yml). The push trigger's paths-ignore (the template's, which tools/known-issues.py
# checks) becomes the mod's `paths` after rendering (mod_push): GitHub takes one of the two per event, never both.
PUSH_IGNORE = re.compile(r"(?m)^    # the known-issues ledger \(tools/known-issues\.py\).*\n    # those runs nothing.*\n"
                         r"    paths-ignore: \[.*\]$")
MOD_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
REL_PATH = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*")


def mod_workflow(name=None):
    """The workflow file port-ci.py writes: .github/workflows/port-ci.yml, or port-ci-<NAME>.yml for one mod of
    several (--mod-dir)."""
    return WORKFLOW if not name else f".github/workflows/port-ci-{name}.yml"


def rel_path(repo, path, what):
    """`path` as a clean path relative to the repository root (a folder or file that exists in it), or SystemExit."""
    p = str(path).replace("\\", "/").strip("/")
    if p.startswith("./"):
        p = p[2:]
    if not REL_PATH.fullmatch(p) or any(x in (".", "..") for x in p.split("/")):
        raise SystemExit(f"{what} must be a path inside the repository, relative to its root (e.g. examplemod), got {path!r}")
    if not (pathlib.Path(repo) / p).exists():
        raise SystemExit(f"{what} {p}: no such folder or file in {repo}")
    return p


def mod_values(values, mod_dir=None, name=None, also=()):
    """`also`: the push path patterns of the folders and files the mod builds from (--also-path: `shared/**` for a
    folder, the path itself for a file). The placeholders that make the workflow one mod's of several (--mod-dir): its name, push paths, tag series,
    build folder, artifacts, promote check and publish config. With no mod_dir every one is the single-mod text."""
    if not mod_dir:
        return dict(values, MOD_TITLE="", TAG_GLOB="", MOD_DIR=".", LOGS="gate-logs-", JARS="jars", LIBS="build/libs",
                    MOD_PROPS="gradle.properties", TAG_NAME=values["MODID"], PROMOTE_TAG_CHECK="", PUBLISH_IF="",
                    PUBLISH_MOD="", MOD_PUSH="")
    paths = [f"{mod_dir}/**", *also, mod_workflow(name)]
    push = ("    # one mod of several: a push runs this mod's workflow only when it touches the mod's folder, a folder it\n"
            "    # builds from or this workflow (GitHub evaluates path filters on branch pushes only, so a release tag still runs)\n"
            "    paths: [" + ", ".join(json.dumps(x) for x in paths) + "]")
    check = (f'          case "$TAG" in\n'
             f'            "{name}-"[0-9]*) ;;\n'
             f'            *) echo "::error::$TAG is not a release of {name} ({name}-<version>-mc<minecraft>): promote it with '
             f'its own workflow, port-ci-<mod>.yml"; exit 1 ;;\n'
             f'          esac\n')
    # a mod's releases are `<name>-<digit>...`: never by prefix alone, so a mod `a` never takes mod `a-b`'s tags
    mine = " || ".join(f"startsWith(github.event.release.tag_name, '{name}-{n}')" for n in range(10))
    return dict(values, MOD_TITLE=f" {name}", TAG_GLOB=f"{name}-[0-9]", MOD_DIR=mod_dir, LOGS=f"gate-logs-{name}-",
                JARS=f"jars-{name}", LIBS=f"{mod_dir}/build/libs", MOD_PROPS=f"{mod_dir}/gradle.properties",
                TAG_NAME=name, PROMOTE_TAG_CHECK=check,
                PUBLISH_IF=f" && (github.event_name != 'release' || {mine})",
                PUBLISH_MOD=f' --mod "{name}"', MOD_PUSH=push)


def mod_push(text, values):
    """The rendered workflow with its push trigger's paths-ignore replaced by the mod's `paths` (mod_values MOD_PUSH);
    unchanged for a single mod."""
    if not values.get("MOD_PUSH"):
        return text
    out, n = PUSH_IGNORE.subn(lambda _m: values["MOD_PUSH"], text, count=1)
    if n != 1:
        raise SystemExit("port-ci: the template's push paths-ignore was not found: cannot make it one mod's")
    return out


def port_branch(repo, mc, explicit=None):
    """The branch the workflow's triggers name: --branch when given; on a version branch -- the checkout holds
    `.port/<mc>/` for its own Minecraft version -- `neoforge-<mc>`, whatever branch is checked out (a keep-current/*
    work branch merges into it); else the checked-out branch, as a single-version fork always had."""
    if explicit:
        if not re.fullmatch(r"[\w.\-/]+", explicit):
            raise SystemExit(f"--branch must be a branch name, got {explicit!r}")
        return explicit
    if mc and (pathlib.Path(repo) / ".port" / mc).is_dir():
        return f"neoforge-{mc}"
    return git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def recover_args(text):
    """The port-ci.py arguments an installed workflow was generated with, read back from its text: what a re-pin or a
    new option regenerates from, so nothing else moves. Not arguments (read from the checkout instead): the port
    branch (port_branch: HEAD, or neoforge-<mc> on a version branch), the authors' branch (origin/HEAD), the target (gradle.properties) and the Java toolchain."""
    out = []
    one = lambda rx: (re.search(rx, text) or [None, None])[1]
    out += ["--modid", one(r'ci-gates\.py --repo \S+ --modid "([^"]+)"')]
    mod_dir = one(r'ci-gates\.py --repo (\S+) --modid ')
    if mod_dir and mod_dir != ".":                 # one mod of several (--mod-dir): its name, and what it builds from
        out += ["--mod-dir", mod_dir]
        name = one(r'tags: \["([A-Za-z0-9][\w.-]*?)-(?:\[0-9\])?\*-mc')
        if name and name != mod_dir.rsplit("/", 1)[-1]:
            out += ["--tag-name", name]
        paths = re.search(r"(?m)^    paths: (\[.*\])\s*$", text)
        for x in (json.loads(paths.group(1)) if paths else [])[1:-1]:
            out += ["--also-path", x[:-3] if x.endswith("/**") else x]
    up = one(r'echo "\*\*Unofficial port\*\* of (.*) to [^"]*\."')
    if up and up != "the original":
        out += ["--upstream", up]
    seen = []
    for m in re.finditer(r'- name: ([\w.-]+) ([\w.+-]+) from the [^\n]* release into mavenLocal \(pinned by sha256\)\n'
                         r'        run: \|\n          mkdir -p "\$HOME/\.m2/repository/([^"]+)"\n'
                         r'          curl -fsSL -o "[^"]+" "https://github\.com/([^/"]+/[^/"]+)/releases/download/([^/"]+)/([^/"]+)"', text):
        art, ver, path, repo, tag, asset = m.groups()
        spec = f"{repo}@{tag}/{asset}=" + path[:-len(f"/{art}/{ver}")].replace("/", ".") + f":{art}:{ver}"
        if spec not in seen:
            seen.append(spec); out += ["--dep-jar", spec]
    seen = []
    for m in re.finditer(r'- name: ([\w.-]+) ([\w.+-]+) -- the ([^ ]+) variant from the [^\n]* release instead[^\n]*\n'
                         r'        if: [^\n]*\n        run: \|\n'
                         r'          curl -fsSL "https://github\.com/([^/"]+/[^/"]+)/releases/download/([^/"]+)/[^"]+" '
                         r'-o "\$HOME/\.m2/repository/([^"]+)/[^/"]+/[^/"]+/[^/"]+\.jar"', text):
        art, ver, asset, repo, tag, gpath = m.groups()
        spec = f"{repo}@{tag}/{asset}=" + gpath.replace("/", ".") + f":{art}:{ver}"
        if spec not in seen:
            seen.append(spec); out += ["--dep-jar-variant", spec]
    vj = one(r'\n          VARIANT_JARS: "([^"]*)"\n')
    for g in (vj or "").split():
        out += ["--variant-jar", g]
    seen = []
    for m in re.finditer(r'\n          repository: (\S+)\n          ref: (\S+)\n          path: \.port-deps/', text):
        d = f"{m.group(1)}@{m.group(2)}"
        if d not in seen:
            seen.append(d); out += ["--dep", d]
    out += ["--gatec", one(r'--gatec "([^"]*)"')]
    mc_suffix = re.search(r'tags: \["(?:[\w.-]+-(?:\[0-9\])?)?\*-mc([\d.]+)([^"]*)"\]', text)
    if mc_suffix and mc_suffix.group(2):
        out.append(f"--tag-suffix={mc_suffix.group(2)}")
    # a workflow generated with the removed --layer-branch also triggers on the layer branch: that is dropped here, so a
    # regenerated workflow has no layer jobs
    out += ["--kit-version", one(r"port-ci-kit-v(\d+)\.tar\.gz"), "--kit-sha", one(r'echo "([0-9a-f]{64})  \$RUNNER_TEMP/kit\.tar\.gz"')]
    if None in out:
        raise SystemExit("this does not look like a workflow port-ci.py generated (no --modid, --gatec or kit pin found)")
    return out


def _sha_url(url):
    import hashlib, urllib.request
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "port-ci"}), timeout=120) as r:
        return hashlib.sha256(r.read()).hexdigest()


LOGIN = re.compile(r"[A-Za-z0-9](?:-?[A-Za-z0-9]){0,38}")
TAG = re.compile(r"(?P<modid>[\w.+-]+?)-(?P<ver>[^/]+)-mc(?P<mc>\d+\.\d+(?:\.\d+)?)(?P<suffix>-release)?(?:-r\d+)?")


def branch_for(tag):
    """The port branch a release tag was built on: <mod>-<ver>-mc<mc>[-release][-rN] -> neoforge-<mc>[-release]."""
    m = TAG.fullmatch(tag)
    if not m:
        raise SystemExit(f"{tag!r} is not a Port CI release tag (<mod>-<version>-mc<minecraft>[-release][-rN]); "
                         "pass --branch")
    return f"neoforge-{m['mc']}{m['suffix'] or ''}"


def promote_dispatch(fork, tag, who, notes="", branch=None):
    """The workflow dispatch that promotes `tag`: (ref, inputs). Refuses what the workflow would refuse, earlier."""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", fork or ""):
        raise SystemExit(f"--fork must be owner/repo, got {fork!r}")
    if not who or not LOGIN.fullmatch(who):
        raise SystemExit("--played-by must be the GitHub login of the person who played this build: promotion "
                         "records who did, and nothing promotes without one")
    if "\n" in notes:
        raise SystemExit("--notes is one line")
    return branch or branch_for(tag), {"promote": tag, "played_by": who, "played_notes": notes}


PIN_RE = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/releases/download/([^/\"'\s]+)/")


def pinned_releases(workflow_text):
    """[(owner/repo, tag)] of the dependency release assets a Port CI workflow pins (--dep-jar, --dep-jar-variant: the
    jars it installs into mavenLocal; the Port CI kit's own download is not a dependency)."""
    return sorted({m for ln in (workflow_text or "").splitlines() if ".m2/repository" in ln for m in PIN_RE.findall(ln)})


PRERELEASE_MARK = "**Library pre-release (not done):**"    # tools/layer-deps.py writes it into a version's DEPS.md
PRERELEASE_KEY = "port_jar_prerelease."                       # ... and beside the pin in its branch.properties


def prerelease_pin_notes(tag, read):
    """What the person promoting `tag` should know when its build is pinned to a library PRE-RELEASE that was not done
    when it was pinned (tools/layer-deps.py / round.py: DEPS.md's "Library pre-release (not done)" mark, or
    port_jar_prerelease. in .port/<mc>/branch.properties at the tag). read(path) -> the file at the tag, or None.
    Not a refusal: a person who played the pair may promote it (the library release with it, or first --
    dependency_blockers still refuses a pinned pre-release without --with-dependency), and port-publish.py publishes it
    only with that exact library release promoted and published. Its version stays not done for the round."""
    m = TAG.fullmatch(tag)          # the release tag's own form: <mod>-<ver>-mc<mc>[-release][-r<N>]
    if not m:
        return []
    mc = m.group("mc")
    deps, props = read(f".port/{mc}/DEPS.md") or "", read(f".port/{mc}/branch.properties") or ""
    if PRERELEASE_MARK in deps or re.search(r"(?m)^" + re.escape(PRERELEASE_KEY), props):
        return [f"{tag} is built against a library pre-release whose {mc} was not done (.port/{mc}/DEPS.md \"Library "
                "pre-release (not done)\"): promote it only if the person played this pair; it publishes once that "
                "exact library release is promoted and published, and its version stays not done for the round"]
    return []


def tag_file(repo, fork, tag, path):
    """`path` at `tag`: from the local clone when it has the tag -- a path its tree does not list is None with no
    request -- else from GitHub (raw.githubusercontent; None when absent or unreadable). When git itself errors (no
    such tag here, or a blob-filtered clone whose lazy fetch fails) it falls back to GitHub too."""
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, encoding="utf-8")
    if run("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}").returncode == 0:
        listed = run("ls-tree", "--name-only", tag, "--", path)       # the tree only: no blob is fetched
        if listed.returncode == 0 and not listed.stdout.strip():
            return None
        if listed.returncode == 0:
            shown = run("show", f"{tag}:{path}")
            if shown.returncode == 0:
                return shown.stdout
    try:
        return _get(f"https://raw.githubusercontent.com/{fork}/{tag}/{path}")
    except Exception:                  # noqa: BLE001 -- absent at the tag (or unreadable): no mark read
        return None


def dependency_blockers(workflow_text, release_of, with_deps=(), own=None):
    """(blockers, notes) for promoting: each pinned dependency release that is still a pre-release blocks, unless it
    is named in with_deps (promoted together); one that cannot be read is a note (the check is best effort).
    release_of(repo, tag) -> the GitHub release JSON, or raises. A workflow pinning nothing (a fork with no fork-port
    dependency) never waits."""
    out, notes, named = [], [], {d.lower() for d in with_deps}
    for repo, tag in pinned_releases(workflow_text):
        if (own and repo.lower() == own.lower()) or repo.lower() in named:
            continue
        try:
            rel = release_of(repo, tag)
        except Exception as e:                              # noqa: BLE001 -- best effort: said, not refused
            notes.append(f"{repo}@{tag}: could not tell whether it is a full Release ({e}); check it before promoting")
            continue
        if rel.get("prerelease") or rel.get("draft"):
            out.append(f"{repo}@{tag} is still a pre-release")
    return out, notes


def gh_logged_in():
    import shutil
    return bool(shutil.which("gh")) and subprocess.run(["gh", "auth", "status"], capture_output=True).returncode == 0


def fork_workflow(fork, ref, repo_dir=None, gh=None, workflow=WORKFLOW):
    """The fork's port-ci.yml on `ref`, best effort: `gh api` when gh is logged in (a private fork reads too), else
    `git show` from a local clone of that fork (--repo, default the current directory) -> (text or None, how)."""
    gh = gh_logged_in() if gh is None else gh
    if gh:
        r = subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github.raw",
                            f"repos/{fork}/contents/{workflow}?ref={ref}"],
                           capture_output=True, text=True, encoding="utf-8")
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout, "gh api"
    d = repo_dir or "."
    url = subprocess.run(["git", "-C", d, "remote", "get-url", "origin"], capture_output=True, text=True,
                         encoding="utf-8").stdout.strip()
    if re.search(rf"[/:]{re.escape(fork)}(\.git)?/?$", url, re.I):
        for rev in (f"origin/{ref}", ref):
            r = subprocess.run(["git", "-C", d, "show", f"{rev}:{workflow}"], capture_output=True,
                               text=True, encoding="utf-8")
            if r.returncode == 0:
                return r.stdout, f"git show {rev}"
    return None, "neither gh nor a local clone could read it"


def promote(argv):
    ap = argparse.ArgumentParser(prog="port-ci.py promote", description="promote a played pre-release to a Release")
    ap.add_argument("--fork", required=True); ap.add_argument("--tag", required=True)
    ap.add_argument("--played-by", required=True); ap.add_argument("--notes", default="")
    ap.add_argument("--branch"); ap.add_argument("--print", action="store_true")
    ap.add_argument("--repo", help="a local clone of the fork, read when gh is not logged in (default: the current directory)")
    ap.add_argument("--with-dependency", action="append", default=[], metavar="OWNER/REPO",
                    help="a pinned dependency still a pre-release that the person promotes together with this one")
    ap.add_argument("--mod", metavar="NAME", help="one mod of a repository that ships several: dispatch its own "
                    "workflow, port-ci-<NAME>.yml (port-ci.py --mod-dir); the tag must be <NAME>-...")
    a = ap.parse_args(argv)
    wfile = mod_workflow(a.mod)
    if a.mod and (not MOD_NAME.fullmatch(a.mod) or not re.match(re.escape(a.mod) + r"-[0-9]", a.tag)):
        sys.exit(f"--mod {a.mod}: {a.tag} is not one of its releases ({a.mod}-<version>-mc<minecraft>)")
    wname = wfile.rsplit("/", 1)[-1]
    ref, inputs = promote_dispatch(a.fork, a.tag, a.played_by, a.notes, a.branch)
    import json
    # best effort, never a reason a fork without fork-port pins cannot promote: the workflow is read with gh or from a
    # local clone; when neither can, a note and the dispatch as before
    gh = gh_logged_in()
    wf, how = fork_workflow(a.fork, ref, a.repo, gh, wfile)
    if wf is None:
        print(f"note: could not read {a.fork}'s {wname} on {ref} ({how}): the pinned dependencies are not checked "
              "here -- make sure none is still a pre-release (a dependent is never promoted ahead of its dependency)")
    else:
        def release_of(r_, t_):
            if gh:
                x = subprocess.run(["gh", "api", f"repos/{r_}/releases/tags/{t_}"], capture_output=True, text=True,
                                   encoding="utf-8")
                if x.returncode == 0:
                    return json.loads(x.stdout)
            return json.loads(_get(f"https://api.github.com/repos/{r_}/releases/tags/{t_}"))
        why, notes = dependency_blockers(wf, release_of, a.with_dependency, a.fork)

        notes = notes + prerelease_pin_notes(a.tag, lambda path: tag_file(a.repo or ".", a.fork, a.tag, path))
        for n in notes:
            print("note: " + n)
        if why:
            sys.exit("not promoting " + a.tag + ": a dependent is never a full Release ahead of its dependency --\n  "
                     + "\n  ".join(why) + "\npromote the dependency first, or promote both together: pass "
                     "--with-dependency <owner/repo> for each one the person promotes alongside (and promote it too)")
    for d in a.with_dependency:
        print(f"note: {d} must be promoted together with {a.tag} (port-ci.py promote --fork {d} ...)")
    try:
        rel = json.loads(_get(f"https://api.github.com/repos/{a.fork}/releases/tags/{a.tag}"))
    except Exception as e:                                  # no network, or no such release: say which
        rel = None
        print(f"(could not read release {a.tag} from GitHub: {e}; the workflow checks it anyway)")
    if rel is not None and not rel.get("prerelease"):
        sys.exit(f"{a.tag} is already a full Release")
    cmd = ["gh", "workflow", "run", wname, "--repo", a.fork, "--ref", ref]
    for k, v in inputs.items():
        cmd += ["-f", f"{k}={v}"]
    import shlex
    logged_in = gh
    if a.print or not logged_in:
        print(f"Dispatch the fork's Port CI workflow ({wname}) on ref", ref, "with inputs", json.dumps(inputs))
        print("  " + shlex.join(cmd))
        if not logged_in and not a.print:
            print("(gh is not logged in here: run that, use Actions -> Port CI -> Run workflow, or let an agent "
                  "dispatch it through its GitHub connector)")
        return 0
    r = subprocess.run(cmd)
    if r.returncode == 0:
        print(f"dispatched: watch https://github.com/{a.fork}/actions/workflows/{wname}")
    return r.returncode


GOLDEN_CASES = (("single-nodeps", False), ("single-deps", True))
FIX_DEP = "o/lib@lib-1.0-mc1.21.1/lib-1.0_mr.jar=com.example.lib:lib:1.0"


def fixture(deps=True, variant_jars=(), dep_variants=(), version_branch=False, owned=False, mod=None, gate=None):
    """The workflow for fixed, invented values (no network: every sha256 is fake). The two option-free cases are
    recorded in tools/golden/port-ci-<case>.yml, captured from port-ci.py BEFORE --variant-jar/--dep-jar-variant
    existed: an option a fork does not use must not change one byte of its workflow."""
    sha = lambda u: ("c" if "_cf" in u else "a") * 64
    pre = "\n".join(x for x in (dep_jars([FIX_DEP], sha_of=sha) if deps else "",
                                pre_build(["o/other@neoforge-1.21.1"]) if deps else "") if x)
    values = {"TARGET": "NeoForge 21.1.100 (Minecraft 1.21.1)", "BRANCH": "neoforge-1.21.1", "MC": "1.21.1",
              "MODID": "examplemod", "BASE": "master", "JAVA": "21", "MIGRATOR_REPO": "owner/migrator", "KIT_VERSION": "7",
              "KIT_SHA256": "b" * 64, "GATEC": "launch,spawn", "TAG_SUFFIX": "", "PRE_BUILD": pre,
              "UPSTREAM": "https://github.com/a/examplemod"}
    values = variant_values(values, list(variant_jars), list(dep_variants), [FIX_DEP] if deps else [], sha)
    values = release_gate_values(values, gate)      # gate: release_gate()'s dict, None for none
    values = records_values(kind_values(values, owned), version_branch)
    values = mod_values(values, *(mod or ()))       # mod: (mod_dir, name, also) for one mod of several
    return mod_push(render(TEMPLATE.read_text(encoding="utf-8"), values), values)


def _load_tool(name, file):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / file)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def multimod_repo(d):
    """A repository that ships two mods (issue #274): folders alpha/ and beta/, each with its own settings.gradle,
    build.gradle and gradle.properties, a shared/ folder both apply, ONE gradlew at the root (a stub that records the
    directory it ran in), on a version branch (.port/1.21.4/branch.properties at the root). Invented names only."""
    r = pathlib.Path(d)
    for name, modid in (("alpha", "examplemod"), ("beta", "examplelib")):
        m = r / name
        (m / "src/main/resources/META-INF").mkdir(parents=True)
        (m / "settings.gradle").write_text(f"rootProject.name = '{name}'\n", encoding="utf-8")
        (m / "build.gradle").write_text("apply from: '../shared/level.gradle'\n", encoding="utf-8")
        (m / "gradle.properties").write_text(f"mod_id={modid}\nmod_version=2.0.{len(name)}\nminecraft_version=1.21.4\n"
                                             "neo_version=21.4.100\n", encoding="utf-8")
        (m / "src/main/resources/META-INF/neoforge.mods.toml").write_text(
            f'license="MIT"\n[[mods]]\nmodId="{modid}"\nversion="1"\ndisplayName="Example {name}"\n', encoding="utf-8")
    (r / "shared").mkdir()
    (r / "shared/level.gradle").write_text("// shared\n", encoding="utf-8")
    (r / ".port/1.21.4").mkdir(parents=True)
    (r / ".port/1.21.4/branch.properties").write_text("parent=main\njava_version=21\n", encoding="utf-8")
    (r / "gradlew").write_text('#!/usr/bin/env bash\npwd > "$(dirname "$0")/gradlew-ran-in.txt"\n', encoding="utf-8")
    run = lambda *a: subprocess.run(["git", "-C", str(r), *a], capture_output=True, text=True, encoding="utf-8")
    run("init", "-q", "-b", "neoforge-1.21.4"); run("config", "core.autocrlf", "false")
    run("-c", "user.name=t", "-c", "user.email=t@example.invalid", "add", "-A")
    run("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "fixture")
    return r


def multimod_check():
    """--mod-dir / --tag-name / --also-path (issue #274 items 1-2): one workflow per mod of a repository that ships
    several, each building, gating, releasing, promoting and publishing its own folder only; the single-mod workflow
    unchanged (the goldens, checked elsewhere)."""
    import tempfile
    ok = True
    single = fixture(False)
    wf = fixture(False, mod=("alpha", "alpha", ["shared/**"]))
    has = lambda case, s, text=wf: want(case, s in text, s, "(absent)")
    ok &= has("mod: workflow name", "name: Port CI alpha (NeoForge")
    ok &= has("mod: push paths", '    paths: ["alpha/**", "shared/**", ".github/workflows/port-ci-alpha.yml"]')
    ok &= want("mod: no paths-ignore beside paths", "paths-ignore" not in wf, "none", "paths-ignore")
    ok &= has("mod: gates run on its folder", 'ci-gates.py --repo alpha --modid "examplemod"')
    ok &= has("mod: jar digests from its build/libs", "for j in alpha/build/libs/*.jar; do")
    ok &= has("mod: jars uploaded from its build/libs", "            alpha/build/libs/*.jar\n            !alpha/build/libs/*-sources.jar")
    ok &= want("mod: jars artifact per mod", wf.count("name: jars-alpha\n") == 2, 2, wf.count("name: jars-alpha\n"))
    ok &= has("mod: gate logs per mod", "name: gate-logs-alpha-${{ matrix.env }}")
    ok &= has("mod: gate logs downloaded per mod", "pattern: gate-logs-alpha-*")
    ok &= has("mod: gate summary assets keep their names", 'gate-assets/gates-${n#gate-logs-alpha-}.md')
    ok &= want("mod: no single-mod artifact name left", "gate-logs-$e" not in wf and "logs/gate-logs-*/" not in wf
               and "name: jars\n" not in wf, "none", "a single-mod artifact name")
    ok &= has("mod: release reads its gradle.properties", "sparse-checkout: alpha/gradle.properties")
    ok &= has("mod: tag from its gradle.properties", "//p' alpha/gradle.properties | head -1)\n            TAG=\"alpha-${v}-mc1.21.1\"")
    ok &= has("mod: publish only its own releases", "startsWith(github.event.release.tag_name, 'alpha-0') || "
              "startsWith(github.event.release.tag_name, 'alpha-1')")
    ok &= want("mod: no bare name- prefix test", "tag_name, 'alpha-')" not in wf, "none", "a bare prefix")
    ok &= has("mod: its tags only, a version after the name", 'tags: ["alpha-[0-9]*-mc1.21.1"]')
    ok &= has("mod: publish with its config", 'port-publish.py publish --repo . --tag "$TAG" --mod "alpha" ${MANUAL:+--manual}')
    ok &= has("mod: publish setup with its config", 'port-publish.py modrinth-create --repo . --mod "alpha"')
    # the differences from the single-mod workflow are only these (no line of the single-mod one moves otherwise)
    import difflib
    changed = [l for l in difflib.unified_diff(single.splitlines(), wf.splitlines(), lineterm="", n=0)
               if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    ok &= want("mod: lines changed from the single-mod workflow", len(changed) == 56, 56, len(changed))
    ok &= has("mod: a player's install runs for its own releases only",
              "(needs.promote.result == 'success' || github.event_name == 'release') && (github.event_name != 'release' "
              "|| startsWith(github.event.release.tag_name, 'alpha-0')")
    # the promote job refuses another mod's tag, run as the job runs it
    mark = "          set -euo pipefail\n          case \"$TAG\" in\n"
    ok &= has("mod: promote checks the tag first", mark)
    blk = wf.split(mark, 1)[-1].split("          esac\n", 1)[0] if mark in wf else "*) exit 9 ;;\n"
    script = 'case "$TAG" in\n' + blk + "esac\n"
    for tag, rc in (("alpha-2.0.5-mc1.21.1", 0), ("beta-2.0.4-mc1.21.1", 1), ("alphabet-1-mc1.21.1", 1),
                    ("alpha-extra-1.0-mc1.21.1", 1)):
        r = subprocess.run([_load_tool("gitbash", "gitbash.py").BASH, "-c", script], env=dict(os.environ, TAG=tag),
                           capture_output=True, text=True, encoding="utf-8")
        ok &= want(f"mod: promote {tag}", r.returncode == rc, rc, (r.returncode, r.stdout))
    # mods `a` and `a-b` side by side: each workflow's tag trigger, promote check and publish test take only its own
    wa, wab = fixture(False, mod=("a", "a", [])), fixture(False, mod=("a-b", "a-b", []))
    ok &= want("mods a and a-b: tag triggers", 'tags: ["a-[0-9]*-mc1.21.1"]' in wa and 'tags: ["a-b-[0-9]*-mc1.21.1"]' in wab,
               "a-[0-9]* / a-b-[0-9]*", "other")
    for wf_, tag, rc in ((wa, "a-1.0-mc1.21.1", 0), (wa, "a-b-1.0-mc1.21.1", 1), (wab, "a-b-1.0-mc1.21.1", 0),
                         (wab, "a-1.0-mc1.21.1", 1)):
        blk_ = wf_.split(mark, 1)[-1].split("          esac\n", 1)[0] if mark in wf_ else "*) exit 9 ;;\n"
        r = subprocess.run([_load_tool("gitbash", "gitbash.py").BASH, "-c", 'case "$TAG" in\n' + blk_ + "esac\n"],
                           env=dict(os.environ, TAG=tag), capture_output=True, text=True, encoding="utf-8")
        ok &= want(f"mods a and a-b: promote {tag}", r.returncode == rc, rc, (r.returncode, r.stdout))
    ok &= want("mods a and a-b: publish tests", "tag_name, 'a-0')" in wa and "tag_name, 'a-b')" not in wa
               and "tag_name, 'a-b-0')" in wab, "a-<digit> / a-b-<digit>", "other")
    # read back: the arguments it was generated with (args / check)
    rec = recover_args(wf)
    ok &= want("mod: read back", rec[:2] == ["--modid", "examplemod"] and rec[2:6] == ["--mod-dir", "alpha", "--also-path", "shared"],
               "--modid examplemod --mod-dir alpha --also-path shared", rec[:6])
    named = fixture(False, mod=("mods/alpha", "examplealpha", []))
    ok &= want("mod: --tag-name read back", recover_args(named)[2:6] == ["--mod-dir", "mods/alpha", "--tag-name", "examplealpha"],
               "--mod-dir mods/alpha --tag-name examplealpha", recover_args(named)[2:6])
    ok &= want("mod: no stale records warning", ignores_records(wf) and stale_workflow(wf, None) == [], [], stale_workflow(wf, None))
    # the CLI on a real two-mod checkout: each mod's props, the root's version branch, a file of its own per mod
    with tempfile.TemporaryDirectory() as d:
        r = multimod_repo(d)
        outs = {}
        for name in ("alpha", "beta"):
            p_ = subprocess.run([sys.executable, str(ROOT / "tools/port-ci.py"), "--repo", str(r), "--mod-dir", name,
                                 "--also-path", "shared", "--kit-version", "7", "--kit-sha", "b" * 64, "--no-commit"],
                                capture_output=True, text=True, encoding="utf-8")
            f = r / f".github/workflows/port-ci-{name}.yml"
            ok &= want(f"mod cli: {name} wrote its workflow", p_.returncode == 0 and f.is_file(), 0, p_.stdout + p_.stderr)
            outs[name] = f.read_text(encoding="utf-8") if f.is_file() else ""
        a_, b_ = outs["alpha"], outs["beta"]
        ok &= want("mod cli: no port-ci.yml", not (r / WORKFLOW).exists(), "absent", "written")
        ok &= has("mod cli: mod id from its gradle.properties", 'ci-gates.py --repo beta --modid "examplelib"', b_)
        ok &= has("mod cli: version branch from the root's .port/<mc>/", 'branches: ["neoforge-1.21.4"]', a_)
        ok &= has("mod cli: version branch records ignored on nothing", '"alpha/**", "shared/**"', a_)
        ok &= has("mod cli: its tag", 'TAG="beta-${v}-mc1.21.4"', b_)
        ok &= has("mod cli: target from its gradle.properties", "NeoForge 21.4.100 (Minecraft 1.21.4)", a_)
        # the release step's version, as the job reads it
        line = next((l for l in a_.splitlines() if "sed -n 's/^mod_version" in l), "")
        v = subprocess.run([_load_tool("gitbash", "gitbash.py").BASH, "-c", line.strip() + '; echo "$v"'], cwd=str(r),
                           capture_output=True, text=True, encoding="utf-8").stdout.strip()
        ok &= want("mod cli: release version from its folder", v == "2.0.5", "2.0.5", v)
        bad = subprocess.run([sys.executable, str(ROOT / "tools/port-ci.py"), "--repo", str(r), "--mod-dir", "../x",
                              "--kit-version", "7", "--kit-sha", "b" * 64, "--print"], capture_output=True, text=True, encoding="utf-8")
        ok &= want("mod cli: a folder outside refused", bad.returncode != 0 and "--mod-dir" in bad.stderr, "refused", bad.stderr[-200:])
        bad = subprocess.run([sys.executable, str(ROOT / "tools/port-ci.py"), "--repo", str(r), "--modid", "m",
                              "--also-path", "shared", "--kit-version", "7", "--kit-sha", "b" * 64, "--print"],
                             capture_output=True, text=True, encoding="utf-8")
        ok &= want("mod cli: --also-path needs --mod-dir", bad.returncode != 0, "refused", bad.returncode)
    print(f"port-ci self-check: multi-mod --mod-dir: {'OK' if ok else 'FAIL'}")
    return ok


def regen_golden():
    for name, deps in GOLDEN_CASES:
        (GOLDEN / f"port-ci-{name}.yml").write_text(fixture(deps), encoding="utf-8")
        print(f"wrote tools/golden/port-ci-{name}.yml")
    return 0


def want(case, good, expected, actual):
    """One named self-check assertion; a failure prints what was expected and what came."""
    if not good:
        print(f"port-ci self-check FAIL [{case}]: expected {expected!r}, got {actual!r}")
    return bool(good)


def _gh_glob(pattern):
    """A GitHub Actions path filter as a regex: `**` crosses directories, `*` does not (tools/known-issues.py's)."""
    out = ""
    for tok in re.split(r"(\*\*/?|\*)", pattern):
        out += {"**/": "(?:.*/)?", "**": ".*", "*": "[^/]*"}.get(tok, re.escape(tok))
    return re.compile(f"^{out}$")


def records_check():
    """A version branch's push trigger also ignores its round records (VERSION_RECORDS_IGNORE); a single-version fork's
    workflow does not move by one byte -- checked on the fixtures AND through main() on two real git checkouts, a
    single-version port branch and a version branch (plus a version-branch fork's head, which is single-version-shaped)."""
    import tempfile
    ok = True
    for name, deps in GOLDEN_CASES:            # the default: byte for byte the golden, every fork kind
        ok &= want(f"records: {name} unchanged", fixture(deps) == (GOLDEN / f"port-ci-{name}.yml").read_text(encoding="utf-8"),
                   "the golden", "a diff")
    single, vb = fixture(True), fixture(True, version_branch=True)
    line = lambda w: next(l for l in w.splitlines() if "paths-ignore:" in l)
    ok &= want("records: only the paths-ignore line differs", [a for a in zip(single.splitlines(), vb.splitlines()) if a[0] != a[1]]
               == [(line(single), line(vb))] and len(single.splitlines()) == len(vb.splitlines()), "one line", line(vb))
    ign = json.loads(line(vb).split("paths-ignore:", 1)[1])
    ok &= want("records: the version branch's list", ign == json.loads(line(single).split("paths-ignore:", 1)[1])
               + list(VERSION_RECORDS_IGNORE), "base + records", ign)
    hit = lambda f: any(_gh_glob(g).match(f) for g in ign)
    recs = [".port/1.21.4/GATES.md", ".port/1.21.4/REVIEW.md", ".port/26.3/COST.md", ".port/26.3/DEPS.md",
            ".port/26.3/TEST-ITEMS.md", ".port/26.3/DRIFT.md", ".port/DECISIONS.md", ".port/KNOWN-ISSUES.json", "README.md"]
    code = [".port/26.3/branch.properties", ".port/26.3/renames.tsv", ".port/26.3/STUBS.json", ".port/26.3/REMERGE.json",
            ".port/26.3/shipped-expect-cf.txt", ".port/branch.gradle", ".port/26.3/examplemod/NOTES.md", "src/main/resources/README.md",
            "src/main/resources/assets/examplemod/x.md", "docs/README.md", "build.gradle", "gradle.properties", ".github/workflows/port-ci.yml"]
    ok &= want("records: what is ignored", all(hit(f) for f in recs), recs, [f for f in recs if not hit(f)])
    ok &= want("records: what still runs Port CI", not any(hit(f) for f in code), [], [f for f in code if hit(f)])
    ok &= want("records: push only", vb.count("paths-ignore") == 1 and "paths" not in vb.split("  pull_request:", 1)[1].split("jobs:", 1)[0],
               "one paths-ignore, under push", vb.count("paths-ignore"))
    # main() itself, on real checkouts (no network: the kit pinned by version and sha)
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        outs = {}
        for case, mc, files in (("single", "1.21.1", {".port/KNOWN-ISSUES.json": "{}\n"}),
                                # a single-version fork that keeps a shipped-jar expectation in .port/<mc>/ (port-fork):
                                # still single-version, the golden's line (review of the cost PR, S3)
                                ("single-shipped", "1.21.1", {".port/KNOWN-ISSUES.json": "{}\n",
                                                              ".port/1.21.1/shipped-expect-cf.txt": "A.log:cf ran\n"}),
                                ("version-branch", "1.21.4", {".port/1.21.4/branch.properties": "minecraft_version=1.21.4\n",
                                                              ".port/1.21.4/GATES.md": "# Gates\n"})):
            r = d / case
            r.mkdir()
            g = lambda *a, r=r: subprocess.run(["git", "-c", "gc.auto=0", "-c", "maintenance.auto=false", "-C", str(r), *a],
                                               capture_output=True, text=True, encoding="utf-8")
            g("init", "-q", "-b", f"neoforge-{mc}")
            (r / "gradle.properties").write_text(f"minecraft_version={mc}\nneo_version=21.1.100\n", encoding="utf-8")
            (r / "build.gradle").write_text("// no toolchain line\n", encoding="utf-8")
            for f, text in files.items():
                (r / f).parent.mkdir(parents=True, exist_ok=True)
                (r / f).write_text(text, encoding="utf-8")
            g("add", "-A")
            g("-c", "user.email=x@example.com", "-c", "user.name=x", "commit", "-q", "-m", "x")
            res = subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve()), "--repo", str(r), "--modid", "examplemod",
                                  "--gatec", "launch", "--kit-version", "7", "--kit-sha", "b" * 64, "--print"],
                                 capture_output=True, text=True, encoding="utf-8",
                                 env=dict(os.environ, GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="gc.auto", GIT_CONFIG_VALUE_0="0"))
            outs[case] = res.stdout if res.returncode == 0 else ""
            ok &= want(f"records: main() renders the {case} checkout", res.returncode == 0, 0, res.stderr[-300:])
        if all(outs.values()):
            s, v_ = outs["single"], outs["version-branch"]
            for c_ in ("single", "single-shipped"):
                ok &= want(f"records: main() on a {c_} checkout: the base list only", line(outs[c_]).strip()
                           == 'paths-ignore: [".port/KNOWN-ISSUES.json", ".port/*/KNOWN-ISSUES.json", "README.md"]',
                           "the base list", line(outs[c_]))
            ok &= want("records: main() on a version branch: the records too", json.loads(line(v_).split("paths-ignore:", 1)[1])
                       == ign and 'branches: ["neoforge-1.21.4"]' in v_, ign, line(v_))
    return ok


def workflow_check_check():
    """`port-ci.py check` on a single-version fork's port branch (#161 U1, E1): a workflow pinning an older kit, or one
    whose push trigger ignores no records, is warned about with the command that regenerates it at the newest kit
    (never blocking: exit 0); a current one says so; the same stale_workflow the version-branch head's ci-check uses."""
    import io, tempfile
    ok = True
    with tempfile.TemporaryDirectory() as d:
        r = pathlib.Path(d)
        subprocess.run(["git", "-C", d, "init", "-q", "-b", "neoforge-1.21.1"], check=True)
        (r / ".github/workflows").mkdir(parents=True)
        cur = fixture(False)                                  # kit 7, the records ignore of a single-version workflow
        (r / WORKFLOW).write_text(cur, encoding="utf-8")
        said = []
        ok &= want("check: a current single-version workflow", check(r, kit_version=7, say=said.append) == 0
                   and len(said) == 1 and "is current (kit 7)" in said[0], "current", said)
        said = []
        rc = check(r, kit_version=9, say=said.append)
        ok &= want("check: an older kit warns, exit 0, with the regenerate command at the newest kit", rc == 0
                   and said[0].startswith("port-ci: check: WARNING neoforge-1.21.1: its workflow pins Port CI kit 7, older "
                                          "than kit 9") and "--modid examplemod" in said[0] and "--kit-version" not in said[0]
                   and "--gatec launch,spawn" in said[0], "WARNING ... kit 7 ... regenerate", said)
        old = re.sub(r"(?m)^\s*paths-ignore: .*\n", "", cur)
        (r / WORKFLOW).write_text(old, encoding="utf-8")
        said = []
        check(r, kit_version=7, say=said.append)
        ok &= want("check: no records ignore warns", "ignores no records" in said[0] and "kit 7, older" not in said[0],
                   "records warning", said)
        ok &= want("check: stale_workflow is the head's check", stale_workflow(cur, 7) == [] and len(stale_workflow(old, 9)) == 2
                   and stale_workflow(cur, None) == [], "[] / 2 reasons / []", (stale_workflow(cur, 7), stale_workflow(old, 9)))
        # a hand-added PORT_GRADLE_ARGS (the gates' subset) is warned about until .port/<mc>/gate-gradle-args.txt holds it
        (r / "gradle.properties").write_text("minecraft_version=1.21.1\n", encoding="utf-8")
        hand = cur.replace("      - name: Build and gates\n", '      - name: Build and gates\n        env:\n'
                           '          PORT_GRADLE_ARGS: "-Pshard=ci"\n', 1)
        (r / WORKFLOW).write_text(hand, encoding="utf-8")
        said = []
        check(r, kit_version=7, say=said.append)
        ok &= want("check: a hand-added PORT_GRADLE_ARGS warns, naming the file", hand != cur and len(said) == 2 and
                   'PORT_GRADLE_ARGS by hand ("-Pshard=ci")' in said[0] and ".port/1.21.1/gate-gradle-args.txt" in said[0]
                   and "is current" in said[1], "hand warning + current", said)
        (r / ".port/1.21.1").mkdir(parents=True)
        (r / ".port/1.21.1/gate-gradle-args.txt").write_text("-Pshard=ci\n", encoding="utf-8")
        said = []
        check(r, kit_version=7, say=said.append)
        ok &= want("check: the file present: no hand warning", len(said) == 1 and "is current" in said[0], "current", said)
        ok &= want("check: a lower version's file (inherited) also silences it", hand_gradle_args(hand, r, "1.21.4") is None
                   and hand_gradle_args(hand, r, "1.21.0") is not None, "None / a warning", "the lower file ignored")
        ok &= want("check: the template never sets PORT_GRADLE_ARGS", hand_gradle_args(cur, d, "1.21.4") is None
                   and hand_gradle_args(fixture(True), d, "1.21.4") is None, None, "a template PORT_GRADLE_ARGS")
        (r / WORKFLOW).write_text(old, encoding="utf-8")       # the cases below read the records-less workflow
        said = []
        def no_kit(*a, **k):
            raise SystemExit("offline")
        real, globals()["kit_pin"] = globals()["kit_pin"], no_kit
        try:
            check(r, say=said.append)
        finally:
            globals()["kit_pin"] = real
        ok &= want("check: offline, said, the records still compared", "cannot read the newest Port CI kit" in said[0]
                   and "ignores no records" in said[1], "offline then records", said)
        # a version-branch fork's head: the version-branch regenerate command, not port-ci.py's (review S4)
        (r / WORKFLOW).write_text(cur, encoding="utf-8")
        g = lambda *a: subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@example.invalid", *a],
                                      check=True, capture_output=True)
        g("add", "-A"); g("commit", "-qm", "head")
        g("checkout", "-q", "-b", "neoforge-1.21.4"); (r / ".port/1.21.4").mkdir(parents=True)
        (r / ".port/1.21.4/branch.properties").write_text("port_branch=neoforge-1.21.1\n", encoding="utf-8")
        g("add", "-A"); g("commit", "-qm", "1.21.4"); g("checkout", "-q", "neoforge-1.21.1")
        said = []
        check(r, kit_version=9, say=said.append)
        ok &= want("check: a version-branch head names fork-branches.py ci --head --run",
                   said[0].startswith("port-ci: check: WARNING neoforge-1.21.1: its workflow pins Port CI kit 7, older than kit 9")
                   and f"fork-branches.py ci --repo {r} --head --run" in said[0] and "tools/port-ci.py --repo" not in said[0],
                   "fork-branches.py ci --head --run", said)
        (r / WORKFLOW).unlink()
        said = []
        ok &= want("check: no workflow: said, exit 0", check(r, kit_version=7, say=said.append) == 0 and "does not exist" in said[0],
                   "does not exist", said)
    return ok


def kit_probe_check():
    """kit_pin when the release list is refused (a 403 from api.github.com; downloads work): the newest kit by probing
    SHA256SUMS upward from the highest pinned one, said as a FALLBACK; a readable list is used as before."""
    import urllib.error
    have, said, ok = {7, 8, 9}, [], True
    sums = lambda n: f"{'c' * 64}  port-ci-kit-v{n}.tar.gz\n"

    def get(url, accept=None):
        if "api.github.com" in url:
            raise urllib.error.HTTPError(url, 403, "Forbidden", None, None)
        n = int(re.search(r"port-ci-kit-v(\d+)/SHA256SUMS$", url).group(1))
        if n not in have:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        return sums(n)
    got = kit_pin("o/m", floor=7, get=get, say=said.append)
    ok &= want("kit_pin falls back to probing the downloads", got == (9, "c" * 64), (9, "c" * 64), got)
    ok &= want("the fallback is said", len(said) == 1 and "FALLBACK" in said[0] and "403" in said[0] and "kit v9" in said[0],
               "one FALLBACK line naming the 403 and kit v9", said)
    ok &= want("probe from the floor", probe_newest_kit("o/m", 8, get) == 9 and probe_newest_kit("o/m", None, get) is None
               and probe_newest_kit("o/m", 6, get) == 9 and probe_newest_kit("o/m", 10, get) is None, "9, None, 9, None",
               [probe_newest_kit("o/m", f, get) for f in (8, None, 6, 10)])
    said.clear()
    listed = lambda url, accept=None: (json.dumps([{"tag_name": "port-ci-kit-v8"}, {"tag_name": "x"}]) if "api." in url
                                       else sums(8))
    ok &= want("a readable release list is used, nothing said", kit_pin("o/m", floor=7, get=listed, say=said.append) == (8, "c" * 64)
               and not said, "(8, sha), no line", said)
    ok &= want("pinned_kits reads the workflows", pinned_kits(["x port-ci-kit-v7.tar.gz", "port-ci-kit-v12/S", None]) == [7, 12],
               [7, 12], pinned_kits(["x port-ci-kit-v7.tar.gz", "port-ci-kit-v12/S", None]))
    return ok


def default_branch_check():
    """default_branch: origin/HEAD; without it, git ls-remote --symref; only then the fallback, with a warning."""
    import tempfile
    ok = True
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        run = lambda *a, cwd=d: subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
        run("init", "-q", "-b", "develop", "up")
        run("-c", "user.email=x@example.com", "-c", "user.name=x", "commit", "-q", "--allow-empty", "-m", "x", cwd=d / "up")
        run("clone", "-q", str(d / "up"), "co")
        said = []
        ok &= want("origin/HEAD", default_branch(d / "co", say=said.append) == "develop" and not said
                   and default_branch(d / "co", ref=True) == "origin/develop", "develop", said)
        run("remote", "set-head", "origin", "-d", cwd=d / "co")
        got = default_branch(d / "co", say=said.append)
        ok &= want("no origin/HEAD: ls-remote", got == "develop" and len(said) == 1 and "ls-remote" in said[0]
                   and default_branch(d / "co", say=said.append, ref=True) == "origin/develop",
                   "develop, said", (got, said))
        said.clear()
        run("remote", "set-url", "origin", str(d / "gone"), cwd=d / "co")
        got = default_branch(d / "co", "master", say=said.append)
        ok &= want("unreachable origin: the fallback, warned", got == "master" and "WARNING" in said[0]
                   and "set-head origin -a" in said[0], "master + warning", (got, said))
    return ok


def variant_check():
    """--variant-jar / --dep-jar-variant: off by default (byte-identical goldens), and what each adds when given."""
    ok = True
    for name, deps in GOLDEN_CASES:            # neither option: today's workflow, byte for byte
        ok &= fixture(deps) == (GOLDEN / f"port-ci-{name}.yml").read_text(encoding="utf-8")
    cf = FIX_DEP.replace("_mr.jar", "_cf.jar")
    base = fixture(True)
    # --variant-jar: one more job, the release needs it, globs only through env, the dep jar passed as --deps
    s = fixture(True, variant_jars=["*_mr.jar", "*_cf.jar"])
    job = s.split("\n  shipped:\n", 1)[1].split("\n  release:\n", 1)[0]
    ok &= s.count("\n  shipped:\n") == 1 and "    name: shipped jars\n" in job
    ok &= "needs: [gates, shipped]" in s and "needs: gates\n" not in s.split("\n  release:\n", 1)[1].split("steps:", 1)[0]
    ok &= 'VARIANT_JARS: "*_mr.jar *_cf.jar"' in job and 'DEP_JARS: ".m2/repository/com/example/lib/lib/1.0/lib-1.0.jar"' in job
    ok &= "shipped-gate.py" in job and "--deps" in job and "log_tails" in job and "::error::shipped jar" in job
    ok &= "!inputs.promote" in job.split("steps:", 1)[0] and "github.event_name != 'release'" in job.split("steps:", 1)[0]
    ok &= "a" * 64 + "  " in job and "b" * 64 + "  " in job          # dependency and kit sha256-checked here too
    ok &= "${{" not in job.split("run: |\n          python3", 1)[1].split("\n      - name: Shipped-jar summary", 1)[0]
    ok &= s.replace(s[s.index("\n\n  shipped:\n"):s.index("\n\n  release:\n")], "").replace(
        "needs: [gates, shipped]", "needs: gates").replace(
        RELEASE_SHIPPED_IF, "") == base        # nothing else moved
    # --dep-jar-variant: one more leg, named; default legs keep their check and artifact names; sha256 pinned
    d = fixture(True, dep_variants=[cf])
    g = d.split("\n  gates:\n", 1)[1].split("\n  release:\n", 1)[0]
    ok &= 'env: [full, minimal]\n        variant: [""]\n        include:\n          - env: full\n            variant: "cf"\n' in g
    ok &= "name: gates (${{ matrix.env }}${{ matrix.variant && format(', {0}', matrix.variant) || '' }})" in g
    ok &= "name: gate-logs-${{ matrix.env }}${{ matrix.variant && format('-{0}', matrix.variant) || '' }}" in g
    ok &= "if: matrix.env == 'full' && !matrix.variant" in g
    step = g.split("the lib-1.0_cf.jar variant", 1)[1].split("\n      - ", 1)[0]
    ok &= "if: matrix.variant == 'cf'" in step and "c" * 64 + "  $HOME/.m2/repository/com/example/lib/lib/1.0/lib-1.0.jar" in step
    ok &= "sha256sum -c -" in step and "releases/download/lib-1.0-mc1.21.1/lib-1.0_cf.jar" in step and "${{" not in step
    ok &= g.index("lib-1.0_mr.jar") < g.index("lib-1.0_cf.jar") < g.index("- name: Build and gates")
    ok &= "needs: gates\n" in d and "\n  shipped:\n" not in d
    both = fixture(True, variant_jars=["*_mr.jar"], dep_variants=[cf])
    ok &= "\n  shipped:\n" in both and 'variant: "cf"' in both and "needs: [gates, shipped]" in both
    # tools/port-offer.py reads the default pin only: the variant's curl is written URL-first
    d = fixture(True, dep_variants=[cf])
    ok &= [m.group(1) for m in re.finditer(r'curl -fsSL -o "[^"]*/([\w.+-]+\.jar)" "(https://github\.com/[^"]+/releases/download/[^"]+)"', d)
           if "o/lib" in m.group(2)] == ["lib-1.0.jar"]
    ok &= variant_label("lib-1.0_mr.jar", "lib-1.0_cf.jar") == "cf" and variant_label("lib-2.3.jar", "lib-2.3-fabric.jar") == "fabric"
    sha = lambda u: "a" * 64
    for bad in ([FIX_DEP], [cf.replace("@lib-1.0-mc1.21.1/", "@lib-1.1-mc1.21.1/")],      # same asset; another tag
                [cf.replace("o/lib@", "o/other@")], [cf.replace(":1.0", ":1.1")], ["not-a-spec"], [cf, cf],
                [cf.replace("_cf.jar", "_c f.jar")]):
        try:
            dep_jar_variants(bad, [FIX_DEP], sha); ok = False
        except SystemExit:
            pass
    try:
        dep_jar_variants([cf], [], sha); ok = False                 # a variant of no --dep-jar
    except SystemExit:
        pass
    for bad in (["*_mr.jar", "*_mr.jar"], ['*"; rm -rf ~; ".jar'], ["$X.jar"], ["libs/*.jar"], ["*_mr"]):
        try:
            variant_values({}, bad); ok = False
        except SystemExit:
            pass
    two = dep_jar_variants([cf, "o/b@b-1-mc1.21.1/b-1_cf.jar=com.example.b:b:1"],
                           [FIX_DEP, "o/b@b-1-mc1.21.1/b-1_mr.jar=com.example.b:b:1"], sha)
    ok &= [l for l, _ in two] == ["cf", "b-1_cf"]                 # two libraries with a "_cf" each: distinct legs
    # `args` reads every option back from a generated workflow, both fork kinds, with and without the variant options
    kit = ["--gatec", "launch,spawn"]
    pin = ["--kit-version", "7", "--kit-sha", "b" * 64]
    deps = ["--dep-jar", FIX_DEP]
    ok &= recover_args(fixture(False)) == ["--modid", "examplemod", "--upstream", "https://github.com/a/examplemod", *kit, *pin]
    ok &= recover_args(fixture(True)) == ["--modid", "examplemod", "--upstream", "https://github.com/a/examplemod", *deps,
                                          "--dep", "o/other@neoforge-1.21.1", *kit, *pin]
    ok &= recover_args(fixture(True, ["*_mr.jar", "*_cf.jar"], [cf])) == [
        "--modid", "examplemod", "--upstream", "https://github.com/a/examplemod", *deps, "--dep-jar-variant", cf,
        "--variant-jar", "*_mr.jar", "--variant-jar", "*_cf.jar", "--dep", "o/other@neoforge-1.21.1", *kit, *pin]
    # a workflow made with the removed --layer-branch (its push trigger also names the layer branch): read back
    # without it, so regenerating it drops the layer jobs
    old = fixture(False).replace('branches: ["neoforge-1.21.1"]', 'branches: ["neoforge-1.21.1", "neoforge-1.21.x"]')
    ok &= old != fixture(False) and recover_args(old) == recover_args(fixture(False))
    sfx = render(TEMPLATE.read_text(encoding="utf-8"), mod_values(variant_values(dict(
        {k: "x" for k in re.findall(r"(?<!\$)\{\{([A-Z0-9_]+)\}\}", TEMPLATE.read_text(encoding="utf-8"))},
        MC="1.21.1", TAG_SUFFIX="-release", MODID="m", GATEC="launch", KIT_VERSION="3", KIT_SHA256="d" * 64,
        UPSTREAM="the original", PRE_BUILD="", BRANCH="neoforge-1.21.1", KIND_NOUN="unofficial port",
        TITLE_KIND="unofficial x port", NOTES_INTRO='            echo "**Unofficial port** of the original to x."\n'))))
    ok &= recover_args(sfx) == ["--modid", "m", "--gatec", "launch", "--tag-suffix=-release", "--kit-version", "3", "--kit-sha", "d" * 64]
    try:
        recover_args("name: something else\n"); ok = False
    except SystemExit:
        pass
    # an OWNED repository (own_mod): the same workflow with the port's words left out -- no "unofficial", no original
    own, port = fixture(False, owned=True), fixture(False)
    ok &= "nofficial" not in own and "the original" not in own and "**examplemod** for NeoForge" in own
    ok &= "Pre-release until played by a person." in own          # promote still finds its mark
    ok &= [l for l in own.splitlines() if "nofficial" not in l and "original" not in l and "examplemod** for" not in l
           and "own CI" not in l and "credits as the source" not in l and "--title" not in l and "proves this mod" not in l] == \
          [l for l in port.splitlines() if "nofficial" not in l and "original" not in l and "--title" not in l]
    ok &= recover_args(own) == ["--modid", "examplemod", "--gatec", "launch,spawn", "--kit-version", "7",
                                "--kit-sha", "b" * 64]
    return ok and shipped_script_check() and manifest_script_check() and release_script_check()


def release_script_check():
    """Run the release job's `Publish pre-release` script on a
    fake fork: a bare origin with tags and a stand-in gh. The -rN count-up skips a name whose release OR tag exists (a
    tag with no release made gh release create reuse that tag's commit and ignore --target); a tag at another commit
    than the build is refused, nothing created."""
    import os, tempfile
    sys.path.insert(0, str(ROOT / "tools"))
    from gitbash import BASH
    ok = True
    wf = fixture(False)
    for job in [wf]:
        body = job.split("      - name: Publish pre-release\n", 1)[1].split("        run: |\n", 1)[1]
        body = body.split("\n\n", 1)[0]
        script = "\n".join(l[10:] for l in body.splitlines())
        # (ref_type, ref_name, releases that exist, tags {name: "build"|"other"}) -> (exit, created tag or None)
        cases = {"fresh": (("branch", "neoforge-1.21.1", [], {}), (0, "examplemod-1.0-mc1.21.1")),
                 "a release": (("branch", "neoforge-1.21.1", ["examplemod-1.0-mc1.21.1"],
                                {"examplemod-1.0-mc1.21.1": "other"}), (0, "examplemod-1.0-mc1.21.1-r2")),
                 "a tag, no release": (("branch", "neoforge-1.21.1", [], {"examplemod-1.0-mc1.21.1": "other"}),
                                       (0, "examplemod-1.0-mc1.21.1-r2")),
                 "tag and release, then a bare tag": (("branch", "neoforge-1.21.1", ["examplemod-1.0-mc1.21.1"],
                                                       {"examplemod-1.0-mc1.21.1": "other",
                                                        "examplemod-1.0-mc1.21.1-r2": "other"}),
                                                      (0, "examplemod-1.0-mc1.21.1-r3")),
                 "a pushed tag at the build": (("tag", "examplemod-1.0-mc1.21.1", [], {"examplemod-1.0-mc1.21.1": "build"}),
                                               (0, "examplemod-1.0-mc1.21.1")),
                 "a tag at another commit": (("tag", "examplemod-1.0-mc1.21.1", [], {"examplemod-1.0-mc1.21.1": "other"}),
                                             (1, None))}
        for case, ((rtype, rname, rels, tags), want) in cases.items():
            with tempfile.TemporaryDirectory() as d:
                d = pathlib.Path(d)
                origin, repo = d / "origin.git", d / "repo"
                def g(*a, cwd=repo):
                    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                                          errors="replace")
                g("init", "-q", "--bare", str(origin), cwd=d)
                g("init", "-q", str(repo), cwd=d)
                for k, v in (("user.email", "t@e"), ("user.name", "t"), ("core.autocrlf", "false")):
                    g("config", k, v)
                (repo / "gradle.properties").write_text("mod_version=1.0\n", encoding="utf-8")
                g("add", "-A"); g("commit", "-qm", "older"); other = g("rev-parse", "HEAD").stdout.strip()
                (repo / "x").write_text("x\n", encoding="utf-8")
                g("add", "-A"); g("commit", "-qm", "build"); build = g("rev-parse", "HEAD").stdout.strip()
                for name, at in tags.items():
                    g("tag", "-a", "-m", "t", name, build if at == "build" else other)   # annotated: peeled
                g("remote", "add", "origin", str(origin)); g("push", "-q", "origin", "HEAD:refs/heads/neoforge-1.21.1", "--tags")
                (repo / "jars").mkdir(); (repo / "jars/x.jar").write_bytes(b"")
                # the stand-in gh is a shell function ahead of the script: no PATH lookup of an extension-less
                # file, which Windows' Git Bash would not do the same way
                # the gate summaries the gates legs uploaded: each becomes an asset gates-<suffix>.md, byte for byte
                sums = {"full": b"| gate | full |\r\nPASS\n", "minimal": b"| gate | minimal |\n", "full-cf": b"cf leg\n"}
                for sfx, body_ in sums.items():
                    (repo / "logs" / f"gate-logs-{sfx}").mkdir(parents=True)
                    (repo / "logs" / f"gate-logs-{sfx}" / "gates.md").write_bytes(body_)
                    (repo / "logs" / f"gate-logs-{sfx}" / "x.log").write_bytes(b"log\n")
                fake_gh = ('gh() { if [ "$1 $2" = "release view" ]; then for r in $RELEASES; do [ "$r" = "$3" ] && return 0; done; '
                           'return 1; fi; if [ "$1 $2" = "release create" ]; then echo "$3" >> "$CREATED"; '
                           'shift 3; echo "$*" >> "$ASSETS"; return 0; fi; return 3; }\n')
                run = fake_gh + (script.replace("${{ github.ref_type }}", rtype).replace("${{ github.ref_name }}", rname)
                       .replace("${{ github.repository }}", "o/examplemod").replace("${{ github.sha }}", build))
                env = dict(os.environ, RELEASES=" ".join(rels), CREATED=(d / "created.txt").as_posix(), PREVIEW="",
                           ASSETS=(d / "assets.txt").as_posix())
                env.pop("FROM_RUN", None)
                r = subprocess.run([BASH, "-c", run], cwd=repo, env=env, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace")
                made = (d / "created.txt").read_text(encoding="utf-8").split() if (d / "created.txt").exists() else []
                good = r.returncode == want[0] and made == ([want[1]] if want[1] else [])
                good &= want[0] == 0 or "already points at" in r.stdout + r.stderr
                if want[1]:     # the jars, then gates-full-cf.md, gates-full.md, gates-minimal.md: the exact files
                    sent = (d / "assets.txt").read_text(encoding="utf-8").split()
                    names = ["gate-assets/gates-full-cf.md", "gate-assets/gates-full.md", "gate-assets/gates-minimal.md"]
                    good &= sent[:4] == ["jars/x.jar", *names] and sent[4] == "--repo"
                    good &= all((repo / n).read_bytes() == sums[n[len("gate-assets/gates-"):-3]] for n in names)
                    good &= "(jars and gates of run" not in (repo / "notes.md").read_text(encoding="utf-8")
                if not good:
                    print(f"release script check ({case}): exit {r.returncode}, "
                          f"created {made}, want {want}\n{(r.stdout + r.stderr)[-1500:]}")
                ok &= good
    return ok


def manifest_script_check():
    """Run the promote job's manifest-refresh script on a fake fork (a bare origin, a stand-in port-manifest.py), for
    a fork's workflow: a changed manifest is committed on top of the port branch and
    pushed there (the tag stays in its history); a push the branch moved under is retried on the new tip; an
    unchanged manifest pushes nothing; a tag that is not a tag name never reaches git."""
    import os, tempfile
    sys.path.insert(0, str(ROOT / "tools"))
    from gitbash import BASH
    ok = True
    wf = fixture(True)
    body = wf.split("      - name: Refresh the install manifest\n", 1)[1].split("        run: |\n", 1)[1]
    body = body.split("\n\n", 1)[0]
    script = "\n".join(l[10:] for l in body.splitlines()).replace("python3 ", f'"{pathlib.Path(sys.executable).as_posix()}" ')
    for case in ("changed", "race", "unchanged", "bad-tag"):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            origin, repo, tmp = d / "origin.git", d / "repo", d / "tmp"
            tmp.mkdir()
            def g(*a, cwd=repo):
                return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            g("init", "-q", "--bare", str(origin), cwd=d)
            g("init", "-q", str(repo), cwd=d)
            for k, v in (("user.email", "t@e"), ("user.name", "t"), ("core.autocrlf", "false")):
                g("config", k, v)
            (repo / ".github").mkdir()
            (repo / ".github/port-install.json").write_text('{"v": 1}\n', encoding="utf-8")
            g("add", "-A"); g("commit", "-qm", "port"); g("tag", "m-1-mc1.21.1")
            g("remote", "add", "origin", str(origin)); g("push", "-q", "origin", "HEAD:refs/heads/neoforge-1.21.1")
            tools = repo / ".port-tools/tools"
            tools.mkdir(parents=True)
            (tools / "port-manifest.py").write_text(
                "import pathlib, subprocess, sys\n"
                "calls = pathlib.Path(sys.argv[0]).with_name('calls.txt')\n"
                "calls.write_text(calls.read_text(encoding='utf-8') + ' '.join(sys.argv[1:]) + '\\n' if calls.exists() "
                "else ' '.join(sys.argv[1:]) + '\\n', encoding='utf-8')\n"
                f"if {case != 'unchanged'}:\n"
                "    pathlib.Path('.github/port-install.json').write_text('{\"v\": 2}\\n', encoding='utf-8')\n"
                "    subprocess.run(['git', 'commit', '-qam', 'Install manifest [skip ci]'], check=True)\n",
                encoding="utf-8")
            if case == "race":       # the branch moves once under the first push: rejected, then refreshed again
                hook = origin / "hooks/pre-receive"
                hook.write_text('#!/bin/sh\nif [ ! -f "$GIT_DIR/raced" ]; then touch "$GIT_DIR/raced"; '
                                'echo "moved" >&2; exit 1; fi\nexit 0\n', encoding="utf-8")
                hook.chmod(0o755)
            env = dict(os.environ, RUNNER_TEMP=str(tmp), GITHUB_STEP_SUMMARY=(tmp / "summary.md").as_posix(),
                       TAG="m-1-mc1.21.1; touch pwned" if case == "bad-tag" else "m-1-mc1.21.1")
            r = subprocess.run([BASH, "-c", script], cwd=repo, env=env, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            calls = (tools / "calls.txt").read_text(encoding="utf-8").splitlines() if (tools / "calls.txt").exists() else []
            tip = g("rev-parse", "neoforge-1.21.1", cwd=origin).stdout.strip()
            pushed = g("show", f"{tip}:.github/port-install.json", cwd=origin).stdout
            anc = g("merge-base", "--is-ancestor", "m-1-mc1.21.1", tip).returncode == 0
            want = {"changed": (0, 1, '{"v": 2}\n'), "race": (0, 2, '{"v": 2}\n'),
                    "unchanged": (0, 1, '{"v": 1}\n'), "bad-tag": (1, 0, '{"v": 1}\n')}[case]
            good = (r.returncode, len(calls), pushed) == want and anc and not (repo / "pwned").exists()
            good &= all(c == "refresh --repo . --tag m-1-mc1.21.1 --commit" for c in calls)
            if not good:
                print(f"manifest script check ({case}): exit {r.returncode}, "
                      f"{len(calls)} call(s), origin has {pushed!r}\n{(r.stdout + r.stderr)[-1500:]}")
            ok &= good
    return ok


def shipped_script_check():
    """Run the `shipped jars` job's own script on a fake fork: a gradlew that builds x_mr.jar and x_cf.jar, and a
    stand-in shipped-gate.py (the real one, its main replaced) that boots _mr and fails _cf. The job must build once,
    pass the dependency as --deps, pass each variant its own .port/<mc>/shipped-expect[-<variant>].txt as
    --expect-file (_cf its suffixed file, _mr the unsuffixed one, none when there is none), fail naming the failing
    variant with its server log tail, and refuse a glob that matches no jar."""
    import os, shutil, tempfile
    wf =fixture(True, variant_jars=["*_mr.jar", "*_cf.jar", "*_none.jar"])
    body = wf.split("python3 - <<'PY'\n", 1)[1].split("\n          PY\n", 1)[0]
    script = "\n".join(l[10:] for l in body.splitlines())
    ok = True
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        repo, tools, home, tmp = d / "repo", d / "repo/.port-tools/tools", d / "home", d / "tmp"
        for p in (tools, home, tmp):
            p.mkdir(parents=True)
        # the kit's own files, as the job loads them; port_gates runs gradlew through gitbash's BASH (Git Bash on
        # Windows, where plain "bash" is WSL's launcher), so this fake fork's shell-script gradlew runs on every OS
        for f in ("port_gates.py", "srcsets.py", "targets.py", "gitbash.py", "ci-gates.py", "central-mirror.init.gradle"):
            shutil.copy(ROOT / "tools" / f, tools / f)
        (repo / "build.gradle").write_text("tasks.register('cfJar', Jar) {}\n", encoding="utf-8")
        (repo / "gradlew").write_text('echo "$@" >> gradle-calls.txt; mkdir -p build/libs; '
                                      'echo x > build/libs/x_mr.jar; echo x > build/libs/x_cf.jar\n', encoding="utf-8")
        real = (ROOT / "tools/shipped-gate.py").read_text(encoding="utf-8")
        main_call = 'if __name__ == "__main__":\n    sys.exit(main())\n'
        if main_call not in real:
            print("shipped-jars script check: tools/shipped-gate.py no longer ends with its main call")
            return False
        (tools / "shipped-gate.py").write_text(real.replace(main_call, "".join("    " * bool(i) + l + "\n" for i, l in enumerate((
            'if __name__ == "__main__":',
            "a = sys.argv",
            "print('[shipped-gate] expect ' + pathlib.Path(a[a.index('--jar') + 1]).name + ' '"
            " + (a[a.index('--expect-file') + 1] if '--expect-file' in a else 'none'))",
            "w = pathlib.Path(a[a.index('--work-dir') + 1]); (w / 'server/logs').mkdir(parents=True)",
            "(w / 'server/logs/latest.log').write_text('server log line\\n', encoding='utf-8')",
            "print('[shipped-gate] deps ' + ' '.join(a[a.index('--deps') + 1:]))",
            "bad = a[a.index('--jar') + 1].endswith('_cf.jar')",
            "print('[shipped-gate] ' + ('FAIL boot -- exited before Done' if bad else 'PASS boot -- started'))",
            "sys.exit(1 if bad else 0)")))), encoding="utf-8")
        (repo / "gradle.properties").write_text("minecraft_version=1.21.4\n", encoding="utf-8")
        (repo / ".port/1.21.4").mkdir(parents=True)
        for n in ("shipped-expect.txt", "shipped-expect-cf.txt"):
            (repo / ".port/1.21.4" / n).write_text("A.log:x\n", encoding="utf-8")
        env = dict(os.environ, RUNNER_TEMP=str(tmp), HOME=str(home), USERPROFILE=str(home), VARIANT_JARS="*_mr.jar *_cf.jar *_none.jar",
                   DEP_JARS=".m2/repository/com/example/lib/lib/1.0/lib-1.0.jar")
        env.pop("PORT_GRADLE_ARGS", None)
        r = subprocess.run([sys.executable, "-"], input=script, cwd=repo, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        out = r.stdout + r.stderr
        gc, mdf = repo / "gradle-calls.txt", tmp / "shipped.md"
        if not gc.is_file() or not mdf.is_file():   # the fake gradlew never ran (Windows: WSL's bash, not Git Bash)
            print("shipped-jars script check: the job's script did not build or report:\n" + out[-3000:])
            return False
        calls = gc.read_text(encoding="utf-8").splitlines()
        md = mdf.read_text(encoding="utf-8")

        def want(case, good, expected, actual):
            """One named assertion; a failure prints what was expected and what came, so a red CI log names it."""
            if not good:
                print(f"shipped-jars script check FAIL [{case}]: expected {expected!r}, got {actual!r}")
            return bool(good)

        def expects(text):
            """{jar name: the --expect-file the job passed it, resolved, or 'none'}. Compared as resolved paths:
            the job resolves the checkout, and on Windows that turns an 8.3 temp path (RUNNER~1) into the long one."""
            got = {}
            for m in re.finditer(r"^\[shipped-gate\] expect (\S+) (.+?)\s*$", text, re.M):
                got[m.group(1)] = m.group(2) if m.group(2) == "none" else str(pathlib.Path(m.group(2)).resolve())
            return got

        ok &= want("one build, failing", r.returncode != 0 and len(calls) == 1 and " build cfJar " in f" {calls[0]} ",
                   "exit != 0 and one `build cfJar` call", (r.returncode, calls))
        for row in ("| *_mr.jar | PASS | x_mr.jar: 1 check(s) green |",
                    "| *_cf.jar | FAIL | x_cf.jar: FAIL boot -- exited before Done |",
                    "| *_none.jar | FAIL | 0 built jar(s) match *_none.jar in build/libs (none); exactly one must |"):
            ok &= want("summary row", row in md, row, md)
        for line in ("::error::shipped jar *_cf.jar (x_cf.jar) failed", "tail of latest.log",
                     "deps " + str(home / ".m2/repository/com/example/lib/lib/1.0/lib-1.0.jar"),
                     "shipped jars failed: *_cf.jar, *_none.jar"):
            ok &= want("job output", line in out, line, out[-3000:])
        pe = (repo / ".port/1.21.4").resolve()
        exp = {"x_cf.jar": str(pe / "shipped-expect-cf.txt"), "x_mr.jar": str(pe / "shipped-expect.txt")}
        ok &= want("expect-file per variant (_cf its own, _mr the unsuffixed)", expects(out) == exp, exp, expects(out))
        (pe / "shipped-expect.txt").unlink()
        for f in (tmp / "shipped").glob("*"):
            shutil.rmtree(f) if f.is_dir() else f.unlink()
        r = subprocess.run([sys.executable, "-"], input=script, cwd=repo, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        out = r.stdout + r.stderr                                              # _mr with no file: no expectations
        exp = {"x_cf.jar": str(pe / "shipped-expect-cf.txt"), "x_mr.jar": "none"}
        ok &= want("expect-file, no unsuffixed file (_mr: none)", expects(out) == exp, exp, expects(out) or out[-3000:])
    return ok


# The two option-free goldens as port-ci.py rendered them before the reuse inputs (gate-summary assets, from_run,
# dep_override): with exactly what that change added taken out again (_without_reuse), the workflow is that text.
PRE_REUSE_SHA256 = {"single-deps": "fd672caf828154562fa0f53da179caf6d5b444985a9e44b8d9b48e5a0dd782e4",
                    "single-nodeps": "2360c97f5565ae81ddcfed6c2b94e0bd9e320eaaabedc2c16846bcf8d6a2fe71"}
RELEASE_IF = ("    if: ${{{{ !cancelled() && github.event_name != 'release' && (startsWith(github.ref, 'refs/tags/') || "
              "inputs.release || inputs.from_run) && (needs.gates.result == 'success' || (inputs.from_run && "
              "needs.gates.result == 'skipped')){} }}}}\n")


def _cut(text, start, end):
    i = text.index(start)
    return text[:i] + text[text.index(end, i):]


def _without_reuse(wf):
    """The workflow with every piece the reuse change added taken out and every line it changed put back (and the
    later player-install job, appended after it, taken off the end)."""
    if "\n\n  player-install:\n" in wf:
        wf = wf[:wf.index("\n\n  player-install:\n") + 1]
    w = _cut(wf, "#\n# Every pre-release carries", "name: Port CI (")
    w = _cut(w, "      from_run:\n", "\npermissions:")
    w = w.replace(" && !inputs.from_run }}", " }}").replace(DEP_OVERRIDE_STEP, "")
    w = w.replace(RELEASE_IF.format(""), "    if: github.event_name != 'release' && (startsWith(github.ref, "
                  "'refs/tags/') || inputs.release)\n")
    for gone in ('          git rev-parse "HEAD^{tree}" > "$RUNNER_TEMP/built-tree.txt"   # the tree this run built: a '
                 'from_run release checks it\n', "            ${{ runner.temp }}/built-tree.txt\n",
                 "      actions: read               # from_run: the earlier run and its artifacts\n",
                 "    permissions:\n      contents: read\n      actions: read               # dep_override: a candidate "
                 "jar in another repository's run artifact\n",
                 "          run-id: ${{ inputs.from_run || github.run_id }}\n",
                 "          github-token: ${{ inputs.from_run && github.token || '' }}\n",
                 "          FROM_RUN: ${{ inputs.from_run }}\n",
                 "            ${{ runner.temp }}/port-ci/gate-c-out/\n",    # a later change: a fork's Gate C evidence (#318)
                 "${FROM_RUN:+ (jars and gates of run $FROM_RUN, the same tree)}",
                 ' ${gates[@]+"${gates[@]}"}',
                 ' --port-ci'):     # a later change: the gates run reads .port/<mc>/gate-gradle-args.txt (ci-gates.py)
        w = w.replace(gone, "")
    w = _cut(w, "    # from_run: the gates (and shipped) jobs", "    needs: ")
    w = _cut(w, "      - name: Jar digests", "      - uses: actions/upload-artifact@v4\n        if: matrix.env == 'full'")
    w = _cut(w, "      - name: Refuse a dependent-gate run\n", "      - uses: actions/checkout@v4\n        with:\n          sparse")
    w = _cut(w, "      - name: Check the earlier run (from_run)", "      - uses: actions/download-artifact@v4")
    w = _cut(w, "      - name: Check the earlier run's gate logs", "      - name: Publish pre-release")
    return _cut(w, "          # each environment's gate summary", "          gh release create")


def _step_script(wf, name):
    """The run script of the step named `name`, dedented as the runner runs it."""
    body = wf.split(f"      - name: {name}\n", 1)[1].split("        run: |\n", 1)[1]
    lines = []
    for ln in body.splitlines():
        if ln.strip() and not ln.startswith("          "):
            break
        lines.append(ln[10:])
    return "\n".join(lines) + "\n"


def reuse_check():
    """Gate summaries as release assets, from_run (release an earlier run's jars and gate logs) and dep_override (the
    dependent gate): (a) without them the workflow is today's, byte for byte, but for what they add; (b) the release
    attaches gates-*.md (release_script_check runs it); (c) from_run skips the gates and shipped jobs, the release accepts
    a skipped one only then, downloads from that run, and its checks pass the same tree only; (d) dep_override is
    validated, applied after PRE_BUILD and the variant steps (and in the shipped job), and blocks the release; (e) no
    input reaches a script but through env; (f) a workflow without the new inputs is stale (stale_workflow); (g) the
    gates job annotates each uploaded jar's sha256."""
    import hashlib, os, tempfile
    sys.path.insert(0, str(ROOT / "tools"))
    from gitbash import BASH
    py = f'"{pathlib.Path(sys.executable).as_posix()}" '
    ok = True
    for name, deps in GOLDEN_CASES:                                               # (a)
        un = _without_reuse(fixture(deps))
        ok &= want(f"reuse: {name} minus the reuse change is the old workflow",
                   hashlib.sha256(un.encode("utf-8")).hexdigest() == PRE_REUSE_SHA256[name], PRE_REUSE_SHA256[name], un[-400:])
    cf = FIX_DEP.replace("_mr.jar", "_cf.jar")
    full = fixture(True, variant_jars=["*_mr.jar"], dep_variants=[cf])
    plain = fixture(False)
    job = lambda wf, j, nxt: ("\n" + wf.split("\njobs:\n", 1)[1]).split(f"\n  {j}:\n", 1)[1].split(f"\n  {nxt}:\n", 1)[0]
    gates, shipped, rel = job(full, "gates", "shipped"), job(full, "shipped", "release"), job(full, "release", "promote")
    # (c) from_run
    for j in (gates, shipped):
        ok &= want("reuse: from_run skips gates/shipped", "&& !inputs.publish_setup && !inputs.from_run }}" in j.split("steps:", 1)[0],
                   "!inputs.from_run", j[:600])
    ok &= want("reuse: release if (shipped)", RELEASE_IF.format(RELEASE_SHIPPED_IF) in rel and "needs: [gates, shipped]" in rel,
               RELEASE_IF.format(RELEASE_SHIPPED_IF), rel[:900])
    ok &= want("reuse: release if (no shipped)", RELEASE_IF.format("") in job(plain, "release", "promote")
               and "needs.shipped" not in plain, RELEASE_IF.format(""), job(plain, "release", "promote")[:900])
    ok &= rel.count("          run-id: ${{ inputs.from_run || github.run_id }}\n"
                    "          github-token: ${{ inputs.from_run && github.token || '' }}\n") == 2
    ok &= "    permissions:\n      contents: write\n      actions: read " in rel
    order = ["- name: Refuse a dependent-gate run", "- uses: actions/checkout@v4", "- name: Check the earlier run (from_run)",
             "name: jars", "pattern: gate-logs-*", "- name: Check the earlier run's gate logs", "- name: Publish pre-release"]
    ok &= want("reuse: release step order", [rel.index(s) for s in order] == sorted(rel.index(s) for s in order), order, rel[:3000])
    ok &= "if: ${{ inputs.dep_override }}\n        run: |\n" in rel.split("- uses: actions/checkout@v4", 1)[0] and "exit 1" in \
        rel.split("- uses: actions/checkout@v4", 1)[0]                           # (d) the release refuses dep_override
    ok &= 'git rev-parse "$head^{tree}"' in rel and 'git rev-parse "$GITHUB_SHA^{tree}"' in rel
    # (d) the override after every pinned jar and variant step, before the build, in both jobs
    for j, build, last in ((gates, "- name: Build and gates", "the lib-1.0_cf.jar variant"),
                           (shipped, "- name: Author's build", "- name: Build other (neoforge-1.21.1) into mavenLocal")):
        i = j.index("- name: Dependent gate")
        ok &= want("reuse: dep_override after PRE_BUILD/variants", j.index(last) < i < j.index(build) and
                   j.index("from the lib-1.0-mc1.21.1 release into mavenLocal") < i and "actions: read" in j.split("steps:", 1)[0],
                   "after", j[:200])
    # (e) inputs reach a script only through env: never in a run block, and on a line only as if:/env/with values
    for wf in (full, plain, fixture(True)):
        ok &= want("reuse: no input in a run script", not any("inputs." in b for b in run_blocks(wf)), "none",
                   [b[:200] for b in run_blocks(wf) if "inputs." in b])
        bad = [ln for ln in wf.splitlines() if re.search(r"inputs\.(from_run|dep_override)", ln) and not
               re.match(r"\s*(if|needs|run-id|github-token|[A-Z_]+): ", ln)]
        ok &= want("reuse: inputs only in if/env/with", bad == [], [], bad)
    # (f) a workflow from before the new inputs is stale; today's is not
    old = _cut(plain, "      from_run:\n", "\npermissions:")
    ok &= want("reuse: stale without the inputs", missing_inputs(old) == ["from_run", "dep_override"] and missing_inputs(plain) == []
               and any("rendered before the from_run/dep_override inputs" in w_ for w_ in stale_workflow(old, 7))
               and stale_workflow(plain, 7) == [], "stale / current", (stale_workflow(old, 7), stale_workflow(plain, 7)))
    no_dig = _cut(plain, "      - name: Jar digests", "      - uses: actions/upload-artifact@v4\n        if: matrix.env == 'full'")
    ok &= want("reuse: stale without the jar digests", missing_digests(no_dig) and not missing_digests(plain)
               and stale_workflow(no_dig, 7) == [DIGESTS_STALE], "stale / current", stale_workflow(no_dig, 7))

    def g(*a, cwd):
        return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    # (c) the from_run checks, run: a fake gh serving the two runs' JSON; origin has A and C with one tree, B another
    check_run = _step_script(rel, "Check the earlier run (from_run) built this tree").replace("python3 ", py)
    check_logs = _step_script(rel, "Check the earlier run's gate logs record this tree").replace("python3 ", py)
    # the summaries' rewrite to this commit is that from_run-only step's, never the publish step's
    ok &= want("reuse: the header rewrite only under from_run", "tested as" in check_logs and "tested as" not in
               _step_script(rel, "Publish pre-release") and rel.split("- name: Check the earlier run's gate logs", 1)[1]
               .startswith(" record this tree\n        if: ${{ inputs.from_run }}\n"), "from_run step", check_logs[-600:])
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        origin, repo, tmp = d / "origin.git", d / "repo", d / "tmp"
        tmp.mkdir()
        g("init", "-q", "--bare", str(origin), cwd=d)
        g("config", "uploadpack.allowAnySHA1InWant", "true", cwd=origin)
        g("init", "-q", str(repo), cwd=d)
        for k, v in (("user.email", "t@e"), ("user.name", "t"), ("core.autocrlf", "false")):
            g("config", k, v, cwd=repo)
        shas = []
        for content in ("one\n", "two\n", "one\n"):
            (repo / "gradle.properties").write_text(content, encoding="utf-8")
            g("add", "-A", cwd=repo); g("commit", "-qm", content.strip(), "--allow-empty", cwd=repo)
            shas.append(g("rev-parse", "HEAD", cwd=repo).stdout.strip())
        g("remote", "add", "origin", str(origin), cwd=repo); g("push", "-q", "origin", "HEAD:refs/heads/neoforge-1.21.1", cwd=repo)
        a_, b_, c_ = shas
        tree = g("rev-parse", f"{c_}^{{tree}}", cwd=repo).stdout.strip()
        fake_gh = ('gh() { echo "$*" >> "$GH_CALLS"; [ "$1" = api ] || return 3; case "$2" in '
                   '"repos/o/examplemod/actions/runs/$GITHUB_RUN_ID") cat "$THIS_RUN" ;; '
                   '"repos/o/examplemod/actions/runs/$FROM_RUN") [ -f "$FROM_JSON" ] && cat "$FROM_JSON" ;; '
                   '"repos/o/examplemod/actions/runs/$FROM_RUN/jobs?filter=latest&per_page=100") '
                   '[ -f "$JOBS_JSON" ] && cat "$JOBS_JSON" ;; *) return 1 ;; esac; }\n')
        good_run = {"repository": {"full_name": "o/examplemod"}, "head_repository": {"full_name": "o/examplemod"},
                    "workflow_id": 5, "status": "completed", "conclusion": "success", "event": "pull_request", "head_sha": a_}
        green = [{"name": n, "conclusion": "success"} for n in ("gates (full)", "gates (minimal)", "gates (full, cf)",
                                                                  "shipped jars")] + [{"name": "release", "conclusion": "skipped"}]
        push = {"event": "push", "head_sha": c_}      # the push run of the very commit being released (issue #280)

        def recolor(name, c):
            return [dict(j, conclusion=c) if j["name"] == name else j for j in green]
        # case: (run patch, its jobs, from_run, release, exit, words the refusal must say)
        cases = {"the same tree": ({}, green, "12", "yes", 0, ""),
                 "a push run of the same tree": (push, green, "12", "yes", 0, ""),
                 "a push run of another commit, the same tree": (dict(push, head_sha=a_), green, "12", "yes", 0, ""),
                 "another tree": ({"head_sha": b_}, green, "12", "yes", 1, "not the tree of"),
                 "a push run of another tree": (dict(push, head_sha=b_), green, "12", "yes", 1, "not the tree of"),
                 "a failed run": ({"conclusion": "failure"}, green, "12", "yes", 1, "did not succeed"),
                 "a failed push run": (dict(push, conclusion="failure"), recolor("gates (minimal)", "failure"), "12", "yes", 1,
                                       "did not succeed"),
                 "a push run with a gates leg skipped": (push, recolor("gates (full, cf)", "skipped"), "12", "yes", 1,
                                                         "gates (full, cf) skipped"),
                 "a push run with the shipped jars cancelled": (push, recolor("shipped jars", "cancelled"), "12", "yes", 1,
                                                                "shipped jars cancelled"),
                 "a push run with no gates job": (push, [{"name": "release", "conclusion": "success"}], "12", "yes", 1,
                                                  "ran no gates job"),
                 "jobs not listed": (push, None, "12", "yes", 1, "cannot list run 12's jobs"),
                 "a running push run": (dict(push, status="in_progress", conclusion=None), green, "12", "yes", 1,
                                        "not completed"),
                 "a dispatch run (maybe dep_override)": ({"event": "workflow_dispatch"}, green, "12", "yes", 1,
                                                         "not a push or pull_request run"),
                 "a schedule run": (dict(push, event="schedule"), green, "12", "yes", 1, "not a push or pull_request run"),
                 "a pull request from another repository": ({"head_repository": {"full_name": "x/examplemod"}}, green, "12",
                                                            "yes", 1, "head commit came from x/examplemod"),
                 "another workflow": ({"workflow_id": 6}, green, "12", "yes", 1, "another workflow"),
                 "another repository": ({"repository": {"full_name": "x/y"}}, green, "12", "yes", 1, "not a run of"),
                 "running": ({"status": "in_progress"}, green, "12", "yes", 1, "not completed"),
                 "no such run": (None, green, "12", "yes", 1, "no workflow run"),
                 "not digits": ({}, green, "12; touch pwned", "yes", 1, "digits only"),
                 "without release": ({}, green, "12", "", 1, "release = true")}
        for case, (patch, jobs_, fr, release, rc_want, says) in cases.items():
            (tmp / "calls.txt").write_text("", encoding="utf-8")
            fj, jj = tmp / "from.json", tmp / "jobs.json"
            for f_ in (fj, jj, tmp / "from-run-sha.txt", tmp / "from-run-event.txt", tmp / "summary.md"):
                if f_.exists():
                    f_.unlink()
            if patch is not None:
                fj.write_text(json.dumps(dict(good_run, **patch)), encoding="utf-8")
            if jobs_ is not None:
                jj.write_text(json.dumps({"total_count": len(jobs_), "jobs": jobs_}), encoding="utf-8")
            (tmp / "this.json").write_text(json.dumps({"workflow_id": 5}), encoding="utf-8")
            env = dict(os.environ, RUNNER_TEMP=tmp.as_posix(), GITHUB_STEP_SUMMARY=(tmp / "summary.md").as_posix(),
                       GITHUB_REPOSITORY="o/examplemod", GITHUB_RUN_ID="99", GITHUB_SHA=c_, FROM_RUN=fr, RELEASE=release,
                       THIS_RUN=(tmp / "this.json").as_posix(), FROM_JSON=fj.as_posix(), JOBS_JSON=jj.as_posix(),
                       GH_CALLS=(tmp / "calls.txt").as_posix())
            r = subprocess.run([BASH, "-c", fake_gh + check_run], cwd=repo, env=env, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            calls = (tmp / "calls.txt").read_text(encoding="utf-8")
            out_ = r.stdout + r.stderr
            good = r.returncode == rc_want and not (repo / "pwned").exists() and (rc_want == 0 or "::error::" in out_)
            good &= says in out_ and (rc_want == 0 or "nothing released" in out_ or case in ("not digits", "without release"))
            good &= case != "not digits" or calls == ""
            if rc_want == 0:      # what the logs step reads (the run's event), and the summary names the run released
                ev_ = dict(good_run, **patch)["event"]
                good &= (tmp / "from-run-event.txt").read_text(encoding="utf-8").strip() == ev_
                good &= "Releasing the jars and gate logs of run 12" in (tmp / "summary.md").read_text(encoding="utf-8")
            if not good:
                print(f"reuse: from_run check ({case}): exit {r.returncode}, want {rc_want}\n{(r.stdout + r.stderr)[-1200:]}")
            ok &= good
        # the gate logs must record this commit's tree, for every leg; jars and both environments must be there
        for case, (legs, jars, rc_want, event) in {
                "recorded": ({"full": tree, "minimal": tree, "full-cf": tree}, True, 0, "pull_request"),
                "recorded by a push run": ({"full": tree, "minimal": tree, "full-cf": tree}, True, 0, "push"),
                "a leg built another tree": ({"full": tree, "minimal": tree, "full-cf": "0" * 40}, True, 1, "pull_request"),
                "a push run's leg built another tree": ({"full": tree, "minimal": "0" * 40}, True, 1, "push"),
                "no record (an older workflow)": ({"full": "", "minimal": tree}, True, 1, "pull_request"),
                "no minimal": ({"full": tree}, True, 1, "pull_request"),
                "a push run with no minimal gate log": ({"full": tree}, True, 1, "push"),
                "no jars": ({"full": tree, "minimal": tree}, False, 1, "pull_request"),
                "a push run with no jars": ({"full": tree, "minimal": tree}, False, 1, "push")}.items():
            import shutil
            for x in ("logs", "jars"):
                shutil.rmtree(repo / x, ignore_errors=True)
            head_ = (b"Gates for `examplemod` at `abc1234` for Minecraft 1.21.1 (full environment) -- run by CI with no "
                     b"model and no fixes applied:")
            rest_ = b"\r\n| gate | result |\n| A | PASS |\n"
            for leg, t_ in legs.items():
                (repo / "logs" / f"gate-logs-{leg}").mkdir(parents=True)
                (repo / "logs" / f"gate-logs-{leg}" / "gates.md").write_bytes(head_ + rest_)
                if t_:
                    (repo / "logs" / f"gate-logs-{leg}" / "built-tree.txt").write_text(t_ + "\n", encoding="utf-8")
            if jars:
                (repo / "jars").mkdir(); (repo / "jars/x.jar").write_bytes(b"")
            (tmp / "from-run-event.txt").write_text(event + "\n", encoding="utf-8")
            r = subprocess.run([BASH, "-c", check_logs], cwd=repo, env=dict(os.environ, GITHUB_SHA=c_, FROM_RUN="12",
                               RUNNER_TEMP=tmp.as_posix()), capture_output=True, text=True, encoding="utf-8", errors="replace")
            if rc_want == 0:      # first line: this commit (the reader's `at `<sha>`` and "for Minecraft"), what was tested
                new_ = (f"Gates for `examplemod` at `{c_}` for Minecraft 1.21.1 (full environment) -- run by CI with no model "
                        f"and no fixes applied: -- tested as abc1234 ({event} run 12), the same tree").encode("utf-8")
                got_ = [(repo / "logs" / f"gate-logs-{leg}" / "gates.md").read_bytes() for leg in legs]
                if got_ != [new_ + rest_] * len(legs):
                    print(f"reuse: gate summary header rewrite: {got_[:1]!r}"); ok = False
            if r.returncode != rc_want:
                print(f"reuse: gate-log tree check ({case}): exit {r.returncode}, want {rc_want}\n{(r.stdout + r.stderr)[-800:]}")
                ok = False
    # (d) the dependent gate's step, run: a pinned jar in a fake mavenLocal, stand-in curl, gh and sha256sum
    step = _step_script(gates, "Dependent gate -- candidate dependency jars instead of the pinned ones (dep_override)")
    step = step.replace("python3 ", py)
    import zipfile
    cand = b"candidate jar"
    sha = hashlib.sha256(cand).hexdigest()
    prelude = ('curl() { echo "$2" >> "$CALLS"; cp "$SRC/${2##*/}" "$4"; }\n'
               'gh() { echo "gh $*" >> "$CALLS"; case "$2" in '
               'repos/o/lib/actions/runs/42/artifacts) printf "6\\tjars\\ttrue\\n7\\tjars\\tfalse\\n8\\tgate-logs-full\\tfalse\\n" ;; '
               'repos/o/lib/actions/artifacts/7/zip) cat "$SRC/jars.zip" ;; *) return 1 ;; esac; }\n'
               'sha256sum() { read -r s f; [ "$(' + py + '-c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], \'rb\')'
               '.read()).hexdigest())" "$f")" = "$s" ] || { echo "$f: FAILED"; return 1; }; }\n')
    rel_e = f"o/lib@lib-1.1-rc1/lib-1.1-rc1_mr.jar=com.example.lib:lib:1.0@{sha}"
    art_e = f"o/lib#42/jars/lib-1.1-rc1_mr.jar=com.example.lib:lib:1.0@{sha}"
    cases = {"a release asset": (rel_e, 0, ["https://github.com/o/lib/releases/download/lib-1.1-rc1/lib-1.1-rc1_mr.jar"]),
             "a run artifact": (art_e, 0, ["gh api repos/o/lib/actions/runs/42/artifacts", "gh api repos/o/lib/actions/artifacts/7/zip"]),
             "a wrong sha256": (rel_e[:-64] + "0" * 64, 1, None),
             "an artifact the token cannot read": (art_e.replace("o/lib#42", "o/private#42"), 1, None),
             "no such artifact": (art_e.replace("/jars/", "/other/"), 1, None),
             "an asset the artifact lacks": (art_e.replace("lib-1.1-rc1_mr.jar", "lib-9_mr.jar"), 1, None),
             "coordinates no pin has": (rel_e.replace(":1.0@", ":2.0@"), 1, []),
             "no sha256": (rel_e.rsplit("@", 1)[0], 1, []), "an upper-case sha256": (rel_e.upper(), 1, []),
             "shell in it": (rel_e.replace("lib-1.1-rc1_mr.jar=", "$(touch${IFS}pwned).jar="), 1, []),
             "a path in the tag": (rel_e.replace("@lib-1.1-rc1/", "@../x/"), 1, []),
             "a run id not digits": (art_e.replace("#42/", "#4x2/"), 1, []),
             "only spaces": ("   ", 1, [])}
    for case, (entry, rc_want, want_calls) in cases.items():
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            home, src, tmp = d / "home", d / "src", d / "tmp"
            for x in (src, tmp):
                x.mkdir()
            m2 = home / ".m2/repository/com/example/lib/lib/1.0"
            m2.mkdir(parents=True)
            (m2 / "lib-1.0.jar").write_bytes(b"pinned")          # no .pom: the step writes one
            (src / "lib-1.1-rc1_mr.jar").write_bytes(cand)
            with zipfile.ZipFile(src / "jars.zip", "w") as z:
                z.writestr("lib-1.1-rc1_mr.jar", cand); z.writestr("lib-1.1-rc1-sources.jar", b"x")
            env = dict(os.environ, HOME=home.as_posix(), RUNNER_TEMP=tmp.as_posix(), SRC=src.as_posix(),
                       CALLS=(d / "calls.txt").as_posix(), GITHUB_STEP_SUMMARY=(d / "summary.md").as_posix(),
                       GITHUB_ENV=(d / "env").as_posix(), DEP_OVERRIDE=entry)
            r = subprocess.run([BASH, "-c", prelude + step], cwd=d, env=env, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            calls = (d / "calls.txt").read_text(encoding="utf-8").splitlines() if (d / "calls.txt").exists() else []
            jar = (m2 / "lib-1.0.jar").read_bytes()
            good = r.returncode == rc_want and not (d / "pwned").exists() and jar == (cand if rc_want == 0 else b"pinned")
            good &= want_calls is None or [c.split(" --")[0] for c in calls] == want_calls
            good &= rc_want == 0 or "::error::" in r.stdout + r.stderr or "FAILED" in r.stdout + r.stderr
            if rc_want == 0:
                good &= "<artifactId>lib</artifactId>" in (m2 / "lib-1.0.pom").read_text(encoding="utf-8")
                good &= "Dependent gate: com.example.lib:lib:1.0" in (d / "summary.md").read_text(encoding="utf-8")
                # the later steps learn which coordinates it replaced (a build checking its own pin's sha256 reads it)
                good &= (d / "env").read_text(encoding="utf-8").splitlines() == ["PORT_CI_DEP_OVERRIDE=com.example.lib:lib"]
            if case == "an artifact the token cannot read":
                good &= "the run's token could not read o/private's artifacts" in r.stdout + r.stderr
            if not good:
                print(f"reuse: dep_override ({case}): exit {r.returncode}, want {rc_want}, calls {calls}\n"
                      f"{(r.stdout + r.stderr)[-1200:]}")
            ok &= good
    # (g) the jar digests a round's dependent gate reads over the API (round.py run_jar_digests), sources jar left out;
    # an empty build/libs writes none and still exits 0
    step = _step_script(gates, "Jar digests (a keep-current round's dependent gate reads them over the API)")
    for jars_ in ({"lib-1.0_mr.jar": b"mr", "lib-1.0_cf.jar": b"cf", "lib-1.0-sources.jar": b"src"}, {}):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            (d / "build/libs").mkdir(parents=True)
            for n_, b_ in jars_.items():
                (d / "build/libs" / n_).write_bytes(b_)
            r = subprocess.run([BASH, "-c", step], cwd=d, capture_output=True, text=True, encoding="utf-8", errors="replace")
            want_ = sorted(f"::notice title=jar sha256::{n_} {hashlib.sha256(b_).hexdigest()}" for n_, b_ in jars_.items()
                           if not n_.endswith("-sources.jar"))
            ok &= want(f"reuse: jar digests ({len(jars_)} jars)", (r.returncode, sorted(r.stdout.splitlines())),
                       (0, want_), (r.returncode, r.stdout + r.stderr))
    return ok



def _gh_if(expr, ctx):
    """A workflow `if:` evaluated as GitHub would for the operators the release conditions use (!, &&, ||, ==, !=,
    parentheses, cancelled(), startsWith(github.ref, 'refs/tags/')); `ctx`: {"needs.gates.result": ..., "inputs.release":
    ..., "github.event_name": ..., "github.ref": ..., "cancelled": bool}. A context value not given is '' (GitHub's
    null)."""
    e = expr.strip()
    if e.startswith("${{") and e.endswith("}}"):
        e = e[3:-2]
    e = e.replace("startsWith(github.ref, 'refs/tags/')", "TAG_REF").replace("cancelled()", "C['cancelled']")
    e = re.sub(r"\b((?:needs|inputs|github)\.[\w.-]*\w)", lambda m: f"S('{m.group(1)}')", e)
    e = re.sub(r"\bfalse\b", "False", e)
    e = e.replace("TAG_REF", "S('github.ref').startswith('refs/tags/')")
    e = e.replace("&&", " and ").replace("||", " or ")
    e = re.sub(r"!(?!=)", " not ", e)
    if re.search(r"[^\w\s.()'=!<>-]", re.sub(r"'[^']*'", "''", e).replace("C['']", "")):
        raise ValueError(f"an operator _gh_if does not know: {expr}")
    return bool(eval(e, {"__builtins__": {}}, {"S": lambda k: ctx.get(k, ""), "C": ctx}))   # exec-ok: a test's own expression


GATE_FIX = {"script": "tools/gate/gate.py", "args": ["release-gate", "--wait", "150", "--repo=o/examplemod",
                                                     "a.b:c@d+e"], "timeout_minutes": 180}


def release_gate_check():
    """The fork's release gate (issue #306, .github/port-release-gate.json): (a) without the file the workflow is the
    golden, byte for byte, on both fork kinds; (b) with it, a release-gate job with its needs, permissions, timeout,
    checkout, env and argv, and the release needing it; (c) every bad file refused at generation, by name; (d) the
    release condition: a skipped, failed or cancelled gate never releases (from_run included, where the gates are
    skipped), and the gate runs exactly when the release would; (e) the gate step's own shell, run with a fake script
    exiting 0 and 1; (f) stale_workflow sees a workflow that does not run the fork's gate; (g) main() on a
    single-version and a version-branch checkout."""
    import tempfile
    sys.path.insert(0, str(ROOT / "tools"))
    from gitbash import BASH
    ok = True
    job = lambda wf, j: (("\n" + wf.split("\njobs:\n", 1)[1]).split(f"\n  {j}:\n", 1) + [""])[1].split("\n\n  ", 1)[0]
    cond = lambda j: (j.split("\n    if: ", 1) + ["${{ false }}"])[1].split("\n", 1)[0]   # no job: never runs
    # (a) no file: the golden, byte for byte, and no trace of the gate
    for name, deps in GOLDEN_CASES:
        g_ = (GOLDEN / f"port-ci-{name}.yml").read_text(encoding="utf-8")
        ok &= want(f"gate: no file, {name} is the golden", fixture(deps) == g_ and fixture(deps, gate=None) == g_
                   and "release-gate" not in g_, "the golden", "a diff")
    # (b) the job
    wf = fixture(False, gate=GATE_FIX)
    rg, rel = job(wf, "release-gate"), job(wf, "release")
    argv = "python3 'tools/gate/gate.py' 'release-gate' '--wait' '150' '--repo=o/examplemod' 'a.b:c@d+e'"
    def needs_graph(w_):            # every job -> the jobs it needs, read off the generated workflow
        out = {}
        jobs_ = w_.split("\njobs:\n", 1)[1]
        heads = list(re.finditer(r"(?m)^  ([a-z][a-z0-9-]*):\n", jobs_))
        for i_, m_ in enumerate(heads):
            blk = jobs_[m_.end():heads[i_ + 1].start() if i_ + 1 < len(heads) else len(jobs_)]
            n_ = re.search(r"(?m)^    needs: (\[[^\]]*\]|\S+)$", blk)
            out[m_.group(1)] = [x.strip() for x in (n_.group(1).strip("[]").split(",") if n_ else []) if x.strip()]
        return out

    def upstream_of(g, j, seen=None):
        seen = set() if seen is None else seen
        for n in g.get(j, []):
            if n not in seen:
                seen.add(n)
                upstream_of(g, n, seen)
        return seen
    for tag_, w_, rel_needs in (("one jar", fixture(False, gate=GATE_FIX), {"gates", "release-gate"}),
                                ("--variant-jar", fixture(True, gate=dict(GATE_FIX, script="ci/gate.sh", args=[]),
                                                          variant_jars=["*_mr.jar"]), {"gates", "shipped", "release-gate"})):
        g = needs_graph(w_)
        ok &= want(f"gate: needs graph ({tag_}): release-gate parallel to the gates, the release after all",
                   "gates" in g and g.get("release-gate") == [] and not upstream_of(g, "release-gate")
                   and set(g.get("release", [])) == rel_needs and "release-gate" not in upstream_of(g, "gates"),
                   f"release-gate: [], release: {sorted(rel_needs)}", g)
    ok &= want("gate: runs in parallel with the gates (no needs)", "\n    needs:" not in rg
               and "needs.gates" not in rg and "needs.shipped" not in rg, "no needs", rg[:900])
    for case, s in (("permissions", "\n    permissions:\n      contents: read\n      actions: write\n    steps:\n"),
                    ("timeout", "\n    timeout-minutes: 180\n"),
                    ("checks out the release commit", "      - uses: actions/checkout@v4\n        with:\n          ref: ${{ github.sha }}\n"),
                    ("env", "        env:\n          GATE_SHA: ${{ github.sha }}\n          GATE_REF: ${{ github.ref_name }}\n"
                            "          GH_TOKEN: ${{ github.token }}\n        run: |\n"),
                    ("argv", f"\n          {argv} 2>&1 | tee \"$log\"\n"),
                    ("if", f"\n    if: ${{{{ {RELEASE_GATE_RUNS} }}}}\n")):
        ok &= want(f"gate: job {case}", s in rg, s, rg[:1500])
    code = "\n".join(ln for ln in rg.splitlines() if not ln.lstrip().startswith("#"))
    ok &= want("gate: no contents: write", "contents: write" not in code and "secrets." not in code, "absent", code)
    ok &= want("gate: release needs it", "\n    needs: [gates, release-gate]\n" in rel, "[gates, release-gate]", rel[:400])
    ok &= want("gate: release condition", RELEASE_IF.format(RELEASE_GATE_IF) in rel, RELEASE_IF.format(RELEASE_GATE_IF), rel[:900])
    ok &= want("gate: nothing but the gate added", wf.replace(rg, "").replace("\n\n  release-gate:\n", "")
               .replace("[gates, release-gate]", "gates").replace(RELEASE_GATE_IF, "") == fixture(False), "the golden", "a diff")
    ok &= want("gate: nothing spliced into its script", not any("${{" in b for b in run_blocks(rg)) and len(run_blocks(rg)) == 1,
               "env only", run_blocks(rg))
    sh = fixture(True, gate=dict(GATE_FIX, script="ci/gate.sh", args=[]), variant_jars=["*_mr.jar"])
    rg_s, rel_s = job(sh, "release-gate"), job(sh, "release")
    ok &= want("gate: .sh runs with bash", "\n          bash 'ci/gate.sh' 2>&1 | tee" in rg_s, "bash 'ci/gate.sh'", rg_s[-900:])
    ok &= want("gate: with --variant-jar, still parallel; the release needs all three", "\n    needs:" not in rg_s
               and f"if: ${{{{ {RELEASE_GATE_RUNS} }}}}" in rg_s
               and "\n    needs: [gates, shipped, release-gate]\n" in rel_s
               and RELEASE_IF.format(RELEASE_SHIPPED_IF + RELEASE_GATE_IF) in rel_s, "shipped too", rg_s[:900])
    # the other readers of a workflow are not disturbed by the job
    ok &= want("gate: read back the same", recover_args(wf) == recover_args(fixture(False)) and
               recover_args(sh) == recover_args(fixture(True, variant_jars=["*_mr.jar"])), "same args", recover_args(wf))
    ok &= want("gate: inputs only in if/env/with", not [ln for ln in wf.splitlines() if re.search(
        r"inputs\.(from_run|dep_override)", ln) and not re.match(r"\s*(if|needs|run-id|github-token|[A-Z_]+): ", ln)], [], "an input")
    # (c) refusals, each naming the file and the field
    with tempfile.TemporaryDirectory() as d:
        r = pathlib.Path(d)
        (r / "tools/gate").mkdir(parents=True)
        (r / "tools/gate/gate.py").write_text("pass\n", encoding="utf-8")
        (r / "tools/gate/dir.py").mkdir()
        (r / "tools/gate/notes.txt").write_text("x\n", encoding="utf-8")
        ok &= want("gate: no file, no gate", release_gate(r) is None, None, release_gate(r))
        good = {"schema": 1, "script": "tools/gate/gate.py", "args": ["release-gate"]}
        ok &= want("gate: the default timeout", release_gate(r, json.dumps(good))
                   == {"script": "tools/gate/gate.py", "args": ["release-gate"], "timeout_minutes": 60}, 60, release_gate(r, json.dumps(good)))
        ok &= want("gate: the cap", (release_gate(r, json.dumps(dict(good, timeout_minutes=360))) or {}).get("timeout_minutes") == 360, 360, "other")
        for case, text, says in (
                ("not JSON", "{schema: 1", "not JSON"), ("not an object", "[]", "not a JSON object"),
                ("unknown key", json.dumps(dict(good, shell="x")), "unknown key(s) shell"),
                ("no schema", json.dumps({k: v for k, v in good.items() if k != "schema"}), "schema must be 1"),
                ("schema 2", json.dumps(dict(good, schema=2)), "schema must be 1"),
                ("schema true", json.dumps(dict(good, schema=True)), "schema must be 1"),
                ("no script", json.dumps({"schema": 1}), "script must be a .py or .sh"),
                ("script .txt", json.dumps(dict(good, script="tools/gate/notes.txt")), "script must be a .py or .sh"),
                ("script outside (..)", json.dumps(dict(good, script="../gate.py")), "inside the repository"),
                ("script ../ inside", json.dumps(dict(good, script="tools/../tools/gate/gate.py")), "inside the repository"),
                ("script absolute", json.dumps(dict(good, script="/tmp/gate.py")), "inside the repository"),
                ("script ./", json.dumps(dict(good, script="./tools/gate/gate.py")), "inside the repository"),
                ("script with a space", json.dumps(dict(good, script="tools/gate/my gate.py")), "inside the repository"),
                ("script an option", json.dumps(dict(good, script="-c.py")), "starting with '-'"),
                ("script missing", json.dumps(dict(good, script="tools/gate/none.py")), "no such file"),
                ("script a folder", json.dumps(dict(good, script="tools/gate/dir.py")), "no such file"),
                ("args a string", json.dumps(dict(good, args="release-gate")), "args must be a list"),
                ("args too many", json.dumps(dict(good, args=["x"] * 33)), "args must be a list"),
                ("arg a number", json.dumps(dict(good, args=[150])), "each of args"),
                ("arg empty", json.dumps(dict(good, args=[""])), "each of args"),
                ("arg a space", json.dumps(dict(good, args=["a b"])), "each of args"),
                ("arg a $", json.dumps(dict(good, args=["$HOME"])), "each of args"),
                ("arg an expression", json.dumps(dict(good, args=["${{ github.token }}"])), "each of args"),
                ("arg a quote", json.dumps(dict(good, args=["a'b"])), "each of args"),
                ("arg a semicolon", json.dumps(dict(good, args=["a;b"])), "each of args"),
                ("arg a newline", json.dumps(dict(good, args=["a\nb"])), "each of args"),
                ("arg too long", json.dumps(dict(good, args=["a" * 201])), "each of args"),
                ("timeout 0", json.dumps(dict(good, timeout_minutes=0)), "timeout_minutes must be"),
                ("timeout over the cap", json.dumps(dict(good, timeout_minutes=361)), "timeout_minutes must be"),
                ("timeout a string", json.dumps(dict(good, timeout_minutes="60")), "timeout_minutes must be"),
                ("timeout a float", json.dumps(dict(good, timeout_minutes=1.5)), "timeout_minutes must be"),
                ("timeout true", json.dumps(dict(good, timeout_minutes=True)), "timeout_minutes must be")):
            try:
                got = release_gate(r, text)
                ok &= want(f"gate: refuses {case}", False, "refused", got)
            except SystemExit as e:
                ok &= want(f"gate: refuses {case}", str(e).startswith(RELEASE_GATE_FILE + ": ") and says in str(e), says, str(e))
    # (d) the release condition, evaluated: every event x gates result x gate outcome
    for shipped, w_ in ((False, wf), (True, sh)):
        rel_if, gate_if = cond(job(w_, "release")), cond(job(w_, "release-gate"))
        events = {"tag push": {"github.event_name": "push", "github.ref": "refs/tags/examplemod-1.0-mc1.21.1"},
                  "branch push": {"github.event_name": "push", "github.ref": "refs/heads/neoforge-1.21.1"},
                  "pull request": {"github.event_name": "pull_request", "github.ref": "refs/pull/3/merge"},
                  "release = true": {"github.event_name": "workflow_dispatch", "inputs.release": True},
                  "from_run": {"github.event_name": "workflow_dispatch", "inputs.release": True, "inputs.from_run": "123"},
                  "dep_override": {"github.event_name": "workflow_dispatch", "inputs.release": True,
                                   "inputs.dep_override": "o/l@t/l.jar=g:a:1@" + "a" * 64},
                  "promote": {"github.event_name": "workflow_dispatch", "inputs.promote": "x-1-mc1.21.1"},
                  "released event": {"github.event_name": "release", "github.ref": "refs/tags/x"}}
        for ev, base in events.items():
            for gates in ("success", "failure", "skipped", "cancelled"):
                for cancelled in (False, True):
                    ctx = dict(base, cancelled=cancelled, **{"needs.gates.result": gates, "needs.shipped.result": gates})
                    old = _gh_if(RELEASE_IF.format(RELEASE_SHIPPED_IF if shipped else "").strip()[len("if: "):], ctx)
                    runs = _gh_if(gate_if, ctx)
                    tag = f"{'shipped, ' if shipped else ''}{ev}, gates {gates}{', cancelled' if cancelled else ''}"
                    # the gate runs on every run that would release once its gates pass -- whatever they say yet: it is
                    # parallel to them -- except on dep_override (which never releases)
                    trig = _gh_if(RELEASE_IF.format(RELEASE_SHIPPED_IF if shipped else "").strip()[len("if: "):],
                                  dict(ctx, **{"needs.gates.result": "success", "needs.shipped.result": "success"}))
                    ok &= want(f"gate: runs on every run that would release ({tag})", runs == (trig and ev != "dep_override"),
                               trig, runs)
                    outcomes = ("success", "failure", "cancelled") if runs else ("skipped",)
                    for out in outcomes:
                        rel_runs = _gh_if(rel_if, dict(ctx, **{"needs.release-gate.result": out}))
                        ok &= want(f"gate: release with gate {out} ({tag})", rel_runs == (old and out == "success"),
                                   old and out == "success", rel_runs)
        # the cases the issue names, spelt out: from_run (gates skipped) and a skipped or failed gate
        fr = dict(events["from_run"], cancelled=False, **{"needs.gates.result": "skipped", "needs.shipped.result": "skipped"})
        ok &= want("gate: from_run runs the gate", _gh_if(gate_if, fr), True, False)
        for out in ("skipped", "failure", "cancelled", ""):
            ok &= want(f"gate: from_run, gate {out or 'absent'}: no release",
                       not _gh_if(rel_if, dict(fr, **{"needs.release-gate.result": out})), False, True)
        ok &= want("gate: from_run, gate green: release", _gh_if(rel_if, dict(fr, **{"needs.release-gate.result": "success"})), True, False)
        ok &= want("gate: the old condition lets a skipped gate through (why it is checked)",
                   _gh_if(RELEASE_IF.format("").strip()[len("if: "):], fr), True, False)
    # (e) the gate step, run: a fake gate script exiting 0, then 1; a .sh one
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        repo, tmp = d / "repo", d / "tmp"
        (repo / "tools/gate").mkdir(parents=True); tmp.mkdir()
        (repo / "tools/gate/gate.py").write_text(
            "import json, os, sys\n"
            "json.dump({'argv': sys.argv[1:], 'env': {k: os.environ.get(k) for k in ('GATE_SHA', 'GATE_REF', "
            "'GITHUB_REPOSITORY', 'GH_TOKEN')}}, open(os.path.join(os.environ['RUNNER_TEMP'], 'seen.json'), 'w'))\n"
            "for i in range(1, 61):\n    print('gate line', i)\n"
            "sys.exit(int(os.environ.get('FAKE_RC', '0')))\n", encoding="utf-8")
        (repo / "ci").mkdir()
        (repo / "ci/gate.sh").write_text('echo "sh gate $# $GATE_REF"\nexit "${FAKE_RC:-0}"\n', encoding="utf-8")
        py = f'"{pathlib.Path(sys.executable).as_posix()}" '
        for label, w_, step, rc in (("py 0", wf, "Release gate (tools/gate/gate.py)", 0),
                                    ("py 1", wf, "Release gate (tools/gate/gate.py)", 1),
                                    ("py 3", wf, "Release gate (tools/gate/gate.py)", 3),
                                    ("sh 0", sh, "Release gate (ci/gate.sh)", 0), ("sh 1", sh, "Release gate (ci/gate.sh)", 1)):
            summary = tmp / "summary.md"
            summary.write_text("", encoding="utf-8")
            (tmp / "seen.json").unlink() if (tmp / "seen.json").exists() else None
            script = _step_script(w_, step).replace("python3 ", py, 1) if f"- name: {step}\n" in w_ else "exit 99\n"
            env = dict(os.environ, RUNNER_TEMP=tmp.as_posix(), GITHUB_STEP_SUMMARY=summary.as_posix(), GATE_SHA="c" * 40,
                       GATE_REF="neoforge-1.21.1", GITHUB_REPOSITORY="o/examplemod", GH_TOKEN="t0ken", FAKE_RC=str(rc))
            res = subprocess.run([BASH, "-e", "-o", "pipefail", "-c", script], cwd=str(repo), env=env,   # Actions' own bash -e
                                 capture_output=True, text=True, encoding="utf-8")
            s_ = summary.read_text(encoding="utf-8")
            if label.startswith("py"):
                seen = json.loads((tmp / "seen.json").read_text(encoding="utf-8")) if (tmp / "seen.json").exists() else {}
                ok &= want(f"gate step {label}: argv and env", seen == {"argv": GATE_FIX["args"], "env": {
                    "GATE_SHA": "c" * 40, "GATE_REF": "neoforge-1.21.1", "GITHUB_REPOSITORY": "o/examplemod", "GH_TOKEN": "t0ken"}},
                           GATE_FIX["args"], seen)
                ok &= want(f"gate step {label}: output shown", "gate line 60" in res.stdout, "the gate's output", res.stdout[-200:])
            if rc == 0:
                ok &= want(f"gate step {label}: passes", res.returncode == 0 and "Release gate passed" in s_
                           and "failed" not in s_, 0, (res.returncode, s_, res.stderr[-300:]))
            else:
                ok &= want(f"gate step {label}: fails with its exit code", res.returncode == rc
                           and f"exited {rc}: nothing released" in res.stdout, rc, (res.returncode, res.stdout[-300:], res.stderr[-300:]))
                tail = ("gate line 60" in s_ and "gate line 11\n" in s_ and "gate line 10\n" not in s_) if label.startswith("py") \
                    else "sh gate 0 neoforge-1.21.1" in s_
                ok &= want(f"gate step {label}: the last lines in the summary", "### Release gate failed" in s_ and tail
                           and "passed" not in s_, "the tail", s_[-600:])
    # (f) stale: the workflow does not run the gate the fork's file names
    text = json.dumps({"schema": 1, **GATE_FIX})
    plain = fixture(False)
    for case, w_, t_, want_ in (("no file, no job", plain, "", []), ("not compared", plain, None, []),
                                ("file, the job", wf, text, []),
                                ("file, no job", plain, text, [f"the fork has {RELEASE_GATE_FILE} but its workflow has no release-gate job"]),
                                ("no file, a job", wf, "", [f"its workflow has a release-gate job but the fork has no {RELEASE_GATE_FILE}"]),
                                ("other args", wf, json.dumps({"schema": 1, **dict(GATE_FIX, args=["x"])}),
                                 [f"its release-gate job does not run what {RELEASE_GATE_FILE} says (script, args or timeout)"]),
                                ("other timeout", wf, json.dumps({"schema": 1, **dict(GATE_FIX, timeout_minutes=60)}),
                                 [f"its release-gate job does not run what {RELEASE_GATE_FILE} says (script, args or timeout)"])):
        ok &= want(f"gate: stale, {case}", stale_workflow(w_, 7, t_) == want_, want_, stale_workflow(w_, 7, t_))
    bad = stale_workflow(wf, 7, '{"schema": 2}')
    ok &= want("gate: stale, a bad file", len(bad) == 1 and bad[0].startswith(f"its {RELEASE_GATE_FILE}: schema must be 1"), "named", bad)
    # (g) main() on a single-version checkout and a version branch: the file read from the checkout, a bad one refused,
    # and `check` naming a workflow rendered before the file
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        for case, mc, files in (("single", "1.21.1", {}),
                                ("version-branch", "1.21.4", {".port/1.21.4/branch.properties": "minecraft_version=1.21.4\n"})):
            r = d / case
            (r / "tools/gate").mkdir(parents=True)
            g = lambda *a, r=r: subprocess.run(["git", "-c", "gc.auto=0", "-C", str(r), *a], capture_output=True, text=True,
                                               encoding="utf-8")
            g("init", "-q", "-b", f"neoforge-{mc}")
            (r / "gradle.properties").write_text(f"minecraft_version={mc}\nneo_version=21.1.100\n", encoding="utf-8")
            (r / "build.gradle").write_text("// no toolchain line\n", encoding="utf-8")
            (r / "tools/gate/gate.py").write_text("pass\n", encoding="utf-8")
            for f, t_ in files.items():
                (r / f).parent.mkdir(parents=True, exist_ok=True)
                (r / f).write_text(t_, encoding="utf-8")
            g("add", "-A"); g("-c", "user.email=x@example.com", "-c", "user.name=x", "commit", "-q", "-m", "x")
            run = lambda *extra, r=r: subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve()), "--repo", str(r),
                                                      "--modid", "examplemod", "--gatec", "launch", "--kit-version", "7",
                                                      "--kit-sha", "b" * 64, *extra], capture_output=True, text=True, encoding="utf-8")
            before = run("--print")
            (r / ".github").mkdir()
            (r / RELEASE_GATE_FILE).write_text(text, encoding="utf-8")
            after = run("--print")
            ok &= want(f"gate: main() on the {case} checkout", before.returncode == 0 and after.returncode == 0
                       and "release-gate" not in before.stdout and "\n  release-gate:\n" in after.stdout
                       and f"\n          {argv} 2>&1 | tee" in after.stdout and "needs: [gates, release-gate]" in after.stdout,
                       "the gate with the file only", (before.stderr[-300:], after.stderr[-300:]))
            ok &= want(f"gate: main() on the {case} checkout adds only the gate", after.stdout.replace(
                job(after.stdout, "release-gate"), "").replace("\n\n  release-gate:\n", "").replace("[gates, release-gate]", "gates")
                       .replace(RELEASE_GATE_IF, "") == before.stdout, "before + the gate", "a diff")
            # a re-render keeps it: the file is read again; and the regenerate command needs no option for it
            ok &= want(f"gate: {case} read back needs no option for it", recover_args(after.stdout) == recover_args(before.stdout),
                       recover_args(before.stdout), recover_args(after.stdout))
            (r / RELEASE_GATE_FILE).write_text(json.dumps({"schema": 1, "script": "../gate.py"}), encoding="utf-8")
            refused = run("--print")
            ok &= want(f"gate: main() refuses a bad file ({case})", refused.returncode != 0 and RELEASE_GATE_FILE in refused.stderr
                       and "inside the repository" in refused.stderr and not refused.stdout, "refused", refused.stderr[-300:])
            if case == "single":            # port-ci.py check: the installed workflow predates the file
                (r / RELEASE_GATE_FILE).write_text(text, encoding="utf-8")
                (r / WORKFLOW).parent.mkdir(parents=True)
                (r / WORKFLOW).write_text(before.stdout, encoding="utf-8")
                said = []
                check(r, kit_version=7, say=said.append)
                ok &= want("gate: check names the missing job", any("has no release-gate job" in x for x in said), "a warning", said)
                (r / WORKFLOW).write_text(after.stdout, encoding="utf-8")
                said = []
                check(r, kit_version=7, say=said.append)
                ok &= want("gate: check, current", any("its workflow is current" in x for x in said), "current", said)
    print(f"port-ci self-check: release gate: {'OK' if ok else 'FAIL'}")
    return ok


def run_blocks(text):
    """The body of every `run: |` script in a workflow fragment: inputs reach a script only through env, so none of
    these may contain `${{ }}`."""
    out, cur, ind = [], None, 0
    for line in text.splitlines():
        m = re.match(r"( *)(?:- )?run: \|$", line)
        if m:
            cur, ind = [], len(m.group(1))
            out.append(cur)
        elif cur is not None and line.strip() and len(line) - len(line.lstrip()) <= ind:
            cur = None
        elif cur is not None:
            cur.append(line)
    return ["\n".join(b) for b in out]


def self_check():
    t = TEMPLATE.read_text(encoding="utf-8")
    v = {k: f"<{k}>" for k in set(re.findall(r"(?<!\$)\{\{([A-Z0-9_]+)\}\}", t))}
    v["PRE_BUILD"] = pre_build(["owner/lib@neoforge-1.21.1"])
    v["BRANCH"], v["BASE"] = "neoforge-1.21.1", "master"
    v = mod_values(records_values(release_gate_values(variant_values(v))))
    plain = render(t, v)
    out = plain
    ok = "${{ github.sha }}" in out and "{{" not in out.replace("${{", "")    # GitHub's own ${{ }} survive
    ok &= "repository: owner/lib" in out and "path: .port-deps/lib" in out and "publishToMavenLocal" in out
    import tempfile
    with tempfile.TemporaryDirectory() as d:        # the toolchain the workflow installs is the build's, per target
        for build, mc, want in (("java.toolchain.languageVersion = JavaLanguageVersion.of(25)\n", "26.2", "25"),
                                ("java.toolchain.languageVersion = JavaLanguageVersion.of(21)\n", "1.21.1", "21"),
                                ("// no toolchain line\n", "26.2", "25"), ("// no toolchain line\n", "1.21.1", "21")):
            (pathlib.Path(d) / "build.gradle").write_text(build, encoding="utf-8")
            ok &= java_version(d, mc) == want
    try:
        render("x {{NOPE}}", {}); ok = False
    except (SystemExit, KeyError):
        pass
    j = dep_jars(["o/lib@lib-1.0-mc1.21.1/lib-1.0.jar=net.x.lib:lib:1.0"], sha_of=lambda u: "a" * 64)
    ok &= ('/net/x/lib/lib/1.0/lib-1.0.jar" "https://github.com/o/lib/releases/download/lib-1.0-mc1.21.1/lib-1.0.jar"'
           in j and "a" * 64 + "  " in j and "<artifactId>lib</artifactId>" in j)
    try:
        dep_jars(["o/lib@tag=net.x:lib:1.0"], sha_of=lambda u: "a" * 64); ok = False
    except SystemExit:
        pass
    try:
        pre_build(["not-a-dep"]); ok = False
    except SystemExit:
        pass
    # promote: the job exists, the gates do not run for it, and its inputs reach the script only through env
    ok &= "\n  promote:\n" in out and "!inputs.promote" in out.split("\n  gates:\n", 1)[1].split("steps:", 1)[0]
    ok &= "if: ${{ inputs.promote }}" in out
    job = out.split("\n  promote:\n", 1)[-1].split("\n  publish:\n", 1)[0]
    ok &= "TAG: ${{ inputs.promote }}" in job and "--prerelease=false --latest" in job
    ok &= len(run_blocks(job)) == 3 and not any("${{" in b for b in run_blocks(job))   # nothing spliced into a script
    # then the install manifest: refreshed for the promoted tag and pushed to the PORT branch only, only where one
    # exists, its tag validated, and a failure never stops publish
    for w in (plain,):
        pj = w.split("\n  promote:\n", 1)[-1].split("\n  publish:\n", 1)[0]
        refresh = pj.split("- name: Refresh the install manifest\n", 1)[-1]
        ok &= "    permissions:\n      contents: write\n" in pj and "continue-on-error: true" in refresh
        ok &= pj.count("if: ${{ github.ref_name == 'neoforge-1.21.1' && hashFiles('.github/port-install.json') != '' }}") == 2
        ok &= 'port-manifest.py refresh --repo . --tag "$TAG" --commit' in refresh and "grep -Eq '^[A-Za-z0-9._+-]+$'" in refresh
        ok &= 'git push -q origin "HEAD:refs/heads/neoforge-1.21.1"' in refresh and "refs/heads/neoforge-1.21.x" not in w
    ok &= "Pre-release until played by a person." in out          # the marker promote rewrites
    # publish: runs in the promote's own run (GITHUB_TOKEN edits start no other workflow), never gates on a
    # release event, takes its tag only through env, and the tokens reach only that job
    pub = out.split("\n  publish:\n", 1)[-1].split("\n  publish-setup:\n", 1)[0]
    ok &= "needs: promote" in pub and "github.event_name == 'release'" in pub and "types: [released]" in out
    ok &= "github.event_name != 'release'" in out.split("\n  gates:\n", 1)[1].split("steps:", 1)[0]
    ok &= "run: |" in pub and "${{" not in pub.rsplit("run: |", 1)[1] and "port-publish.py publish" in pub
    ok &= out.count("secrets.MODRINTH_TOKEN") == 2 and out.count("secrets.CURSEFORGE_TOKEN") == 1
    # player-install: after a promote (or a release promoted in the UI), never on a private repository, read-only, its
    # tag only through env, the bootstrap pinned by sha256 before it runs, and it installs exactly that tag
    pi = out.split("\n  player-install:\n", 1)[-1]
    ok &= "needs: promote" in pi and "!github.event.repository.private" in pi and "github.event_name == 'release'" in pi
    ok &= "    permissions:\n      contents: read\n" in pi and "secrets.MODRINTH" not in pi and "contents: write" not in pi
    ok &= "TAG: ${{ inputs.promote || github.event.release.tag_name }}" in pi and not any("${{" in b for b in run_blocks(pi))
    ok &= pi.index("sha256sum -c -") < pi.index('bash "$RUNNER_TEMP/install.sh"')
    ok &= '"$GITHUB_REPOSITORY" --release "$TAG"' in pi and "--boot-test" in pi and "--yes" in pi
    real = render(t, {k: x for k, x in v.items() if k not in PLAYER_INSTALL})       # the pinned values, as generated
    ok &= (f"/releases/download/{PLAYER_INSTALL['PLAYER_INSTALL_TAG']}/install.sh" in real
           and re.fullmatch(r"[0-9a-f]{64}", PLAYER_INSTALL["PLAYER_INSTALL_SHA256"]) is not None)
    ok &= branch_for("mymod-1.0-fix-mc1.21.1-r4") == "neoforge-1.21.1"
    ok &= branch_for("x-1.0-mc26.2") == "neoforge-26.2"
    ok &= branch_for("x_y-2.0+b3-mc1.21.1-release-r2") == "neoforge-1.21.1-release"
    ok &= promote_dispatch("o/r", "x-1.0-mc1.21.1", "some-one", "beat the boss") == (
        "neoforge-1.21.1", {"promote": "x-1.0-mc1.21.1", "played_by": "some-one", "played_notes": "beat the boss"})
    # don't exceed your dependency: the release (a pre-release) may pin a dependency's pre-release -- the workflow
    # renders it like any pin, and nothing on the release side reads the dependency's status -- but promote waits
    # while a pinned dependency is a pre-release, unless the person promotes it alongside (--with-dependency). A fork
    # whose workflow pins nothing (the single-version fork with no fork-port dependency) is unchanged: no blocker.
    pre_pin = fixture(deps=True).replace("lib-1.0-mc1.21.1", "lib-1.1-mc1.21.1")
    ok &= "o/lib/releases/download/lib-1.1-mc1.21.1/" in pre_pin and "--prerelease" in pre_pin
    rels = {("o/lib", "lib-1.1-mc1.21.1"): {"prerelease": True}, ("o/lib", "lib-1.0-mc1.21.1"): {"prerelease": False}}
    look = lambda r_, t_: rels[(r_, t_)]
    ok &= pinned_releases(pre_pin) == [("o/lib", "lib-1.1-mc1.21.1")]
    ok &= dependency_blockers(pre_pin, look) == (["o/lib@lib-1.1-mc1.21.1 is still a pre-release"], [])
    ok &= dependency_blockers(pre_pin, look, ["o/lib"]) == ([], [])
    # a version built against a library pre-release that was not done is noted to the person promoting (it publishes
    # only with that exact library release promoted and published: port-publish.py); a tag with no -mc<v> and a
    # re-resolved pin get no note
    tree = {".port/1.21.4/DEPS.md": "x\n**Library pre-release (not done):** `com.example:lib` ...\n"}
    ok &= len(prerelease_pin_notes("dep-1.0-mc1.21.4", tree.get)) == 1
    ok &= len(prerelease_pin_notes("dep-1.0-mc1.21.4",
                                      {".port/1.21.4/branch.properties": "port_jar_prerelease.com.example\\:lib=x\n"}.get)) == 1
    ok &= prerelease_pin_notes("dep-1.0-mc1.21.1", {}.get) == [] and prerelease_pin_notes("dep-1.0", tree.get) == []
    ok &= prerelease_pin_notes("dep-1.0-mc1.21.4", {".port/1.21.4/DEPS.md": "pinned to lib-1.0 (done)\n"}.get) == []
    # the tag's suffixes the release workflow accepts (TAG): -r<N> and -release are judged too
    ok &= all(len(prerelease_pin_notes(t_, tree.get)) == 1 for t_ in ("dep-1.0-mc1.21.4-r2", "dep-1.0-mc1.21.4-release",
                                                                          "dep-1.0-mc1.21.4-release-r3"))
    # wired into promote: a note before the dispatch, not a refusal; and a tag the local clone has is read only there
    import contextlib, io
    saved = {k: globals()[k] for k in ("gh_logged_in", "fork_workflow", "tag_file", "_get")}
    asked = []
    try:
        globals().update(gh_logged_in=lambda: False, fork_workflow=lambda *a_: (fixture(deps=False), "a stub"),
                         tag_file=lambda repo, fork, tag, path: (asked.append(path), tree.get(path))[1],
                         _get=lambda *a_, **k_: (_ for _ in ()).throw(AssertionError("no request expected")))
        out_ = io.StringIO()
        try:
            with contextlib.redirect_stdout(out_):
                promote(["--fork", "o/dep", "--tag", "dep-1.0-mc1.21.4-r2", "--played-by", "someone", "--print"])
            refused = ""
        except SystemExit as e:
            refused = str(e)
    finally:
        globals().update(saved)
    ok &= not refused and "note: dep-1.0-mc1.21.4-r2 is built against a library pre-release" in out_.getvalue() \
        and ".port/1.21.4/DEPS.md" in asked
    with tempfile.TemporaryDirectory() as d_:
        g_ = lambda *a_: subprocess.run(["git", "-C", d_, "-c", "user.name=t", "-c", "user.email=t@example.invalid", *a_],
                                        capture_output=True, text=True, encoding="utf-8", check=True)
        g_("init", "-q"); pathlib.Path(d_, "x").write_text("x\n", encoding="utf-8"); g_("add", "-A"); g_("commit", "-qm", "x")
        g_("tag", "solo-1.0-mc1.21.1")
        # the workflow's branch: a single-version fork (no .port/) keeps the checked-out branch, work branch or not;
        # a version branch checked out on a keep-current/* work branch still names neoforge-<mc>, not that work branch
        g_("checkout", "-q", "-b", "keep-current/2026-10-20")
        ok &= port_branch(d_, "1.21.1") == "keep-current/2026-10-20"
        g_("checkout", "-q", "-b", "neoforge-1.21.1"); ok &= port_branch(d_, "1.21.1") == "neoforge-1.21.1"
        g_("checkout", "-q", "keep-current/2026-10-20")
        for v_ in ("1.21.3", "1.21.4"):
            pathlib.Path(d_, ".port", v_).mkdir(parents=True); pathlib.Path(d_, ".port", v_, "branch.properties").write_text("x=1\n", encoding="utf-8")
        ok &= port_branch(d_, "1.21.4") == "neoforge-1.21.4" and port_branch(d_, "1.21.4", "neoforge-x") == "neoforge-x"
        import shutil; shutil.rmtree(pathlib.Path(d_, ".port"))
        pathlib.Path(d_, ".port/1.21.4").mkdir(parents=True)
        pathlib.Path(d_, ".port/1.21.4/DEPS.md").write_text("deps at the tag\n", encoding="utf-8")
        g_("add", "-A"); g_("commit", "-qm", "deps"); g_("tag", "dep-1.0-mc1.21.4")
        real_get, calls_ = globals()["_get"], []
        globals()["_get"] = lambda url, *a_, **k_: (calls_.append(url), "from GitHub\n")[1]
        try:
            absent = tag_file(d_, "o/solo", "solo-1.0-mc1.21.1", ".port/1.21.1/DEPS.md")
            no_call = list(calls_)
            local = tag_file(d_, "o/dep", "dep-1.0-mc1.21.4", ".port/1.21.4/DEPS.md")
            after_local = list(calls_)
            remote = tag_file(d_, "o/dep", "dep-9.9-mc1.21.4", ".port/1.21.4/DEPS.md")      # not a tag of this clone
            after_remote = list(calls_)
            # a blob the clone lacks (a blob-filtered clone whose lazy fetch fails): git errors, GitHub is asked
            pathlib.Path(d_, ".port/1.21.5").mkdir(parents=True)
            pathlib.Path(d_, ".port/1.21.5/DEPS.md").write_text("blob not here\n", encoding="utf-8")
            g_("add", "-A"); g_("commit", "-qm", "gone"); g_("tag", "dep-1.0-mc1.21.5")
            oid = g_("rev-parse", "dep-1.0-mc1.21.5:.port/1.21.5/DEPS.md").stdout.strip()
            loose = pathlib.Path(d_, ".git/objects", oid[:2], oid[2:])
            loose.chmod(0o644); loose.unlink()        # git writes loose objects read-only: Windows refuses the unlink
            del calls_[:]
            lazy = tag_file(d_, "o/dep", "dep-1.0-mc1.21.5", ".port/1.21.5/DEPS.md")
        finally:
            globals()["_get"] = real_get
        ok &= absent is None and no_call == []                       # absent at a local tag: None, no request
        ok &= local == "deps at the tag\n" and after_local == []     # present at a local tag: from git, no request
        ok &= remote == "from GitHub\n" and after_remote == ["https://raw.githubusercontent.com/o/dep/dep-9.9-mc1.21.4/.port/1.21.4/DEPS.md"]
        ok &= lazy == "from GitHub\n" and calls_ == ["https://raw.githubusercontent.com/o/dep/dep-1.0-mc1.21.5/.port/1.21.5/DEPS.md"]
    ok &= dependency_blockers(fixture(deps=True), look) == ([], [])
    ok &= dependency_blockers(fixture(deps=False), look) == ([], []) and pinned_releases(fixture(deps=False)) == []
    unread = dependency_blockers(pre_pin, lambda r_, t_: (_ for _ in ()).throw(OSError("offline")))
    ok &= unread[0] == [] and unread[1][0].startswith("o/lib@lib-1.1-mc1.21.1: could not tell")
    # t10 (a single-version fork, no pins, its workflow unreadable -- private, rate-limited, offline): promote prints
    # the dispatch exactly as before the check existed; nothing exits
    import contextlib, io, urllib.error
    saved_get, saved_fw, saved_gh = _get, fork_workflow, gh_logged_in
    def no_net(url, accept=None):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    globals().update(_get=no_net, gh_logged_in=lambda: False)
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc_ = promote(["--fork", "o/private-fork", "--tag", "x-1.0-mc1.21.1", "--played-by", "someone", "--print",
                           "--repo", str(pathlib.Path(__file__).resolve().parent)])
        ok &= rc_ == 0 and "gh workflow run port-ci.yml --repo o/private-fork" in buf.getvalue() and "note: could not read" in buf.getvalue()
        # a readable workflow pinning a pre-release still refuses, and --with-dependency lets it through
        globals().update(fork_workflow=lambda *a_, **k_: (pre_pin, "test"), _get=lambda url, accept=None: (
            '{"prerelease": true}' if "/o/lib/" in url else '{"prerelease": true}'))
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                promote(["--fork", "o/r", "--tag", "x-1.0-mc1.21.1", "--played-by", "someone", "--print"])
            ok = False
        except SystemExit as e:
            ok &= "still a pre-release" in str(e)
        with contextlib.redirect_stdout(io.StringIO()):
            ok &= promote(["--fork", "o/r", "--tag", "x-1.0-mc1.21.1", "--played-by", "someone", "--print",
                           "--with-dependency", "o/lib"]) == 0
    finally:
        globals().update(_get=saved_get, fork_workflow=saved_fw, gh_logged_in=saved_gh)
    for bad in (("o/r", "x-1.0-mc1.21.1", ""), ("o/r", "x-1.0-mc1.21.1", "a b"), ("o/r", "x-1.0-mc1.21.1", "-x"),
                ("nope", "x-1.0-mc1.21.1", "me"), ("o/r", "not-a-tag", "me")):
        try:
            promote_dispatch(*bad); ok = False
        except SystemExit:
            pass
    # no layer text: the workflow triggers on the port branch alone and has no layer jobs (the layer route was removed)
    ok &= not re.search(r"(?<!p)layer", plain.lower()) and 'branches: ["neoforge-1.21.1"]' in plain   # (not "player")
    ok &= "modrinth-create --repo .\n\n  player-install:\n" in plain and "{{" not in plain.replace("${{", "")
    ok &= "PREVIEW" not in plain and "preview" not in plain.lower() and "github.ref_name !=" not in plain
    ok &= "layer-deps.py" not in fixture(True) and "layer-deps.py" not in fixture(False)
    # every needs.<job>.outputs.<name> a job reads is one <job> declares: GitHub reads an undeclared output as '', so a
    # missing line silently skips its job
    for wf in (plain, fixture(True)):
        for job, name in set(re.findall(r"needs\.([\w-]+)\.outputs\.(\w+)", wf)):
            head = wf.split(f"\n  {job}:\n", 1)[1].split("\n    steps:", 1)[0]
            ok &= f"\n      {name}: ${{{{ steps." in head
    ok &= variant_check()
    ok &= records_check()
    ok &= kit_probe_check()
    ok &= workflow_check_check()
    ok &= reuse_check()
    ok &= default_branch_check()
    ok &= multimod_check()
    ok &= release_gate_check()
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    if "--self-check" in sys.argv:
        return self_check()
    if "--regen-golden" in sys.argv:         # only after reading the diff: the goldens are what forks get today
        return regen_golden()
    if sys.argv[1:2] == ["promote"]:
        return promote(sys.argv[2:])
    if sys.argv[1:2] == ["check"]:
        ap = argparse.ArgumentParser(prog="port-ci.py check", description="warn when the installed workflow pins an "
                                     "older Port CI kit or ignores no round records (never blocks: exit 0)")
        ap.add_argument("--repo", default="."); ap.add_argument("--file", help=f"default: <repo>/{WORKFLOW}")
        ap.add_argument("--kit-version", type=int, help="the kit to compare with (default: the newest published)")
        a = ap.parse_args(sys.argv[2:])
        why = _load_tool("fork_kind", "fork-kind.py").refuse_layer(pathlib.Path(a.repo).resolve(), "port-ci")
        if why:
            print(why); return 2
        return check(a.repo, a.file, a.kit_version)
    if sys.argv[1:2] == ["args"]:
        ap = argparse.ArgumentParser(prog="port-ci.py args", description="print the port-ci.py command that regenerates "
                                     "a fork's installed workflow (add or change one option, then --print and diff)")
        ap.add_argument("--repo", required=True); ap.add_argument("--file", help=f"default: <repo>/{WORKFLOW}")
        a = ap.parse_args(sys.argv[2:])
        import shlex
        f = pathlib.Path(a.file) if a.file else pathlib.Path(a.repo) / WORKFLOW
        print(shlex.join(["python3", "tools/port-ci.py", "--repo", a.repo, *recover_args(f.read_text(encoding="utf-8"))]))   # exec-ok: a command printed for a person's shell (setup's python3 shim)
        return 0
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--modid", help="the mod id (required, except with --mod-dir: then read from its gradle.properties' "
                                    "mod_id when not given)")
    ap.add_argument("--mod-dir", metavar="DIR", help="one mod of a repository that ships several: the folder (relative "
                    "to the root) holding its settings.gradle, build.gradle and gradle.properties; writes "
                    ".github/workflows/port-ci-<NAME>.yml, which builds, gates, releases <NAME>-<mod_version>-mc<mc>, "
                    "promotes and publishes that mod only. Run once per mod")
    ap.add_argument("--tag-name", metavar="NAME", help="with --mod-dir: the mod's name in its tags and workflow file "
                    "(default: the folder's name)")
    ap.add_argument("--also-path", action="append", default=[], metavar="PATH",
                    help="with --mod-dir: a folder or file the mod builds from outside its folder (e.g. a shared folder); "
                         "a push touching it runs this mod's workflow too. Repeat")
    ap.add_argument("--upstream", default="", help="the author's repository URL, for the release notes")
    ap.add_argument("--dep", action="append", default=[], help="owner/repo@branch built into mavenLocal first")
    ap.add_argument("--dep-jar", action="append", default=[], metavar="OWNER/REPO@TAG/ASSET.jar=G:A:V",
                    help="a sibling port's released jar, put into mavenLocal (pinned by sha256) -- preferred to --dep")
    ap.add_argument("--dep-jar-variant", action="append", default=[], metavar="OWNER/REPO@TAG/ASSET.jar=G:A:V",
                    help="an ALTERNATE asset of a --dep-jar dependency (same repo, tag and coordinates; e.g. its other "
                         "platform's jar): one more gates leg, `gates (full, <label>)`, runs everything against it")
    ap.add_argument("--variant-jar", action="append", default=[], metavar="GLOB",
                    help="a jar the author's build ships (a glob under build/libs, e.g. '*_mr.jar'; repeat per platform): "
                         "a `shipped jars` job boots each in a real NeoForge server, and the release needs it")
    ap.add_argument("--gatec", default="launch,spawn,battle,gauntlet"); ap.add_argument("--no-commit", action="store_true")
    ap.add_argument("--tag-suffix", default="", help="a tag series of its own, e.g. -release for a release-aligned "
                    "branch, so its JARs never share tags with the development port's")
    ap.add_argument("--branch", help="the branch the workflow runs on (default: neoforge-<mc> when the checkout has "
                    ".port/<mc>/, a version branch; else the checked-out branch)")
    ap.add_argument("--trailer", action="append", default=[])
    ap.add_argument("--kit-version", type=int, help="Port CI kit to pin (default: the newest published)")
    ap.add_argument("--kit-sha", help="its sha256 (default: read from the release's SHA256SUMS)")
    ap.add_argument("--print", action="store_true", help="print the rendered workflow and change nothing")
    a = ap.parse_args()
    repo = pathlib.Path(a.repo).resolve()
    why = _load_tool("fork_kind", "fork-kind.py").refuse_layer(repo, "port-ci")
    if why:
        print(why)
        sys.exit(2)
    mod_dir, name, also = None, None, []
    if a.mod_dir:
        mod_dir = rel_path(repo, a.mod_dir, "--mod-dir")
        if not (repo / mod_dir / "gradle.properties").is_file():
            sys.exit(f"--mod-dir {mod_dir}: no gradle.properties there")
        name = a.tag_name or mod_dir.rsplit("/", 1)[-1]
        if not MOD_NAME.fullmatch(name):
            sys.exit(f"--tag-name must be a name for tags and a file ([A-Za-z0-9_.-], starting with a letter or digit), got {name!r}")
        if a.variant_jar or a.dep_jar_variant:
            sys.exit("--variant-jar and --dep-jar-variant are not supported with --mod-dir yet")
        for x in a.also_path:
            x = rel_path(repo, x, "--also-path")
            also.append(f"{x}/**" if (repo / x).is_dir() else x)
    elif a.tag_name or a.also_path:
        sys.exit("--tag-name and --also-path go with --mod-dir (one mod of a repository that ships several)")
    proj = repo / mod_dir if mod_dir else repo      # the Gradle project: gradle.properties, build.gradle
    p = props(proj)
    a.modid = a.modid or (p.get("mod_id") if mod_dir else None)
    if not a.modid:
        sys.exit("--modid is required" + (f" ({mod_dir}/gradle.properties names no mod_id)" if mod_dir else ""))
    mc, neo = p.get("minecraft_version"), p.get("neo_version")
    if not mc or not neo:
        sys.exit(f"{mod_dir + '/' if mod_dir else ''}gradle.properties has no minecraft_version / neo_version")
    branch = port_branch(repo, mc, a.branch)
    base = default_branch(repo)
    mrepo = migrator_repo()
    wfs = sorted((repo / ".github/workflows").glob("*.y*ml")) if (repo / ".github/workflows").is_dir() else []
    kver, ksha = kit_pin(mrepo, a.kit_version, a.kit_sha,
                         floor=max(pinned_kits(f.read_text(encoding="utf-8", errors="replace") for f in wfs), default=None))
    values = {"TARGET": f"NeoForge {neo} (Minecraft {mc})", "BRANCH": branch, "MC": mc, "MODID": a.modid,
              "BASE": base, "JAVA": java_version(repo, mc, proj), "MIGRATOR_REPO": mrepo, "KIT_VERSION": str(kver), "KIT_SHA256": ksha,
              "GATEC": a.gatec, "TAG_SUFFIX": a.tag_suffix, "PRE_BUILD": "\n".join(x for x in (dep_jars(a.dep_jar), pre_build(a.dep)) if x), "UPSTREAM": a.upstream or "the original"}
    values = variant_values(values, a.variant_jar, a.dep_jar_variant, a.dep_jar)
    values = release_gate_values(values, release_gate(repo))     # .github/port-release-gate.json, refused when bad
    fk = _load_tool("fork_kind", "fork-kind.py")
    owned = fk.is_owned(repo, config=fk.publish_config(name)) if name else fk.is_owned(repo)
    values = records_values(kind_values(values, owned), is_version_branch(repo, mc))
    values = mod_values(values, mod_dir, name, also)
    out = mod_push(render(TEMPLATE.read_text(encoding="utf-8"), values), values)
    if a.print:
        print(out); return 0
    wfile = mod_workflow(name)
    f = repo / wfile
    hand = hand_gradle_args(f.read_text(encoding="utf-8", errors="replace"), repo, mc) if f.is_file() else None
    if hand:
        print(f"port-ci: WARNING: {hand}")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(out, encoding="utf-8")
    if a.no_commit:
        print(f"wrote {f}"); return 0
    git(repo, "add", wfile)
    msg = (f"CI: build, gate and release {'this port' if not name else name} ({values['TARGET']})\n\n"
           "Runs the author's own Gradle build, then the gates the port was held to -- mixin-config\n"
           "integrity, a headless server running GameTests, and a real client under Xvfb -- with no\n"
           "model and no automatic fixes. A tag <mod>-<version>-mc" + mc + " publishes the author-built\n"
           "JARs as a pre-release once every gate passes. The gate harness is not added to this\n"
           f"repository: it comes from {mrepo}'s Port CI kit v{kver} (pinned by sha256), outside the tree.\n"
           "This commit stands alone so it can be dropped from anything offered upstream.\n")
    if a.trailer:
        msg += "\n" + "\n".join(a.trailer) + "\n"
    r = git(repo, "commit", "-q", "-m", msg)
    print(r.stdout + r.stderr or f"committed {wfile} on {branch}")
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
