#!/usr/bin/env python3
"""Install a port's CI (and tag-driven release) into a fork's port branch, as a commit of its own.

    python3 tools/port-ci.py --repo <fork checkout> --modid <modid> [--upstream <author repo URL>]
                             [--dep owner/repo@branch ...] [--gatec launch,spawn,battle,gauntlet] [--no-commit]

The last step of every port: the fork's branch then shows, on every push, that the port builds with the
author's own Gradle build and passes the gates it was held to (tools/ci-gates.py, no model involved); and a
tag `<mod>-<version>-mc<minecraft>` publishes the author-built JARs as a GitHub pre-release once the gates
pass. Everything is read from the repository rather than guessed: the target from gradle.properties, the
Java toolchain from build.gradle, the author's branch from origin/HEAD. --dep names a sibling port this one
compiles against (published to mavenLocal by its own build first, as during the port).

Per-platform jars (both opt-in; without them the workflow is byte-for-byte what it was, tools/golden/port-ci-*.yml):
  --variant-jar GLOB (repeat)  a mod whose build ships several jars (e.g. '*_mr.jar', '*_cf.jar'): a `shipped jars`
                               job builds once and boots EACH in a real NeoForge server (tools/shipped-gate.py, the
                               --dep-jar jars as --deps); the release needs it like the gates.
  --dep-jar-variant SPEC       a dependent of such a mod: another asset of a --dep-jar dependency (same repo, tag and
                               G:A:V), sha256-pinned; one more leg `gates (full, <label>)` runs the build and every
                               gate with it in mavenLocal instead of the default asset.

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
import argparse, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates/upstream-harness/port-ci.yml"
LAYER_TEMPLATE = ROOT / "templates/upstream-harness/port-ci-layer.yml"
SHIPPED_TEMPLATE = ROOT / "templates/upstream-harness/port-ci-shipped.yml"
GOLDEN = ROOT / "tools/golden"
WORKFLOW = ".github/workflows/port-ci.yml"


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def props(repo):
    p = pathlib.Path(repo) / "gradle.properties"
    return dict(re.findall(r"(?m)^\s*([\w.]+)\s*=\s*(.*?)\s*$", p.read_text(encoding="utf-8"))) if p.exists() else {}


def java_version(repo, mc):
    # a fork's version branch (tools/fork-branches.py) sets its toolchain in .port/<mc>/branch.properties (branch.gradle)
    bp = pathlib.Path(repo) / ".port" / mc / "branch.properties"
    jv = re.search(r"(?m)^java_version\s*=\s*(\d+)\s*$", bp.read_text(encoding="utf-8")) if bp.is_file() else None
    if jv:
        return jv.group(1)
    g = (pathlib.Path(repo) / "build.gradle").read_text(encoding="utf-8", errors="replace")
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


def kit_pin(repo, version=None, sha=None):
    """(version, sha256) of the Port CI kit the workflow downloads: the newest published kit unless a version is
    given, and the sha256 its release's own SHA256SUMS states unless one is given. A fork's CI then trusts a
    checksum, not a download (tools/port-ci-kit.py builds kits reproducibly, so `port-ci-kit.py sha` on the
    release commit cross-checks it)."""
    import json
    if version is None:
        rels = json.loads(_get(f"https://api.github.com/repos/{repo}/releases?per_page=100"))
        vs = [int(r["tag_name"].rsplit("v", 1)[1]) for r in rels
              if re.fullmatch(r"port-ci-kit-v\d+", r.get("tag_name", ""))]
        if not vs:
            sys.exit(f"{repo} has published no Port CI kit yet (merge to main publishes one)")
        version = max(vs)
    if sha is None:
        sums = _get(f"https://github.com/{repo}/releases/download/port-ci-kit-v{version}/SHA256SUMS")
        m = re.search(rf"^([0-9a-f]{{64}})\s+port-ci-kit-v{version}\.tar\.gz$", sums, re.M)
        if not m:
            sys.exit(f"port-ci-kit-v{version}'s SHA256SUMS names no port-ci-kit-v{version}.tar.gz")
        sha = m.group(1)
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        sys.exit(f"not a sha256: {sha!r}")
    return int(version), sha


def layer_values(values, layer_branch):
    """The two placeholders of the multi-version layer: the layer branch in the triggers, and the layer-* jobs (a
    second template, rendered with the same values). Without --layer-branch both are empty and the workflow is
    exactly the layer-free one."""
    if not layer_branch:
        return dict(values, LAYER_BRANCH_ITEM="", LAYER_JOBS="", RELEASE_NOT_LAYER="")
    if not re.fullmatch(r"[\w.\-/]+", layer_branch) or layer_branch == values["BRANCH"]:
        raise SystemExit(f"--layer-branch must be a branch name other than the port branch ({values['BRANCH']}), got {layer_branch!r}")
    v = dict(values, LAYER_BRANCH=layer_branch)
    jobs = render(LAYER_TEMPLATE.read_text(encoding="utf-8"), v) + render(layer_release_job(v), v)
    return dict(values, LAYER_BRANCH_ITEM=f', "{layer_branch}"', LAYER_JOBS=jobs,
                RELEASE_NOT_LAYER=f" && github.ref_name != '{layer_branch}'")


def layer_release_job(v):
    """The `release` job of the main template, made the layer branch's: it also waits for the layer's compile and gate
    jobs and puts every target's jar (layer-jars-<t>) beside the base jars, so ONE pre-release holds the file
    publish-port's layer path uploads per target. Derived from the release job's own text, so the release script has
    one home; the main release job steps aside on the layer branch (RELEASE_NOT_LAYER)."""
    t = TEMPLATE.read_text(encoding="utf-8")
    job = t[t.index("\n  release:\n    needs:") + 1:]          # the JOB (the first `release:` is the trigger)
    nxt = re.search(r"\n\n  (?:#|[a-z][\w-]*:\n)", job)       # the next job (or its comment), not a blank line in a script
    job = job[:nxt.start() + 1] if nxt else job
    needs = [x.strip() for x in v["RELEASE_NEEDS"].strip("[]").split(",")]
    ok = " && ".join(f"needs.{n}.result == 'success'" for n in needs)
    job = job.replace("  release:\n", "  layer-release:\n    name: layer release (the base jars and one jar per layer target)\n", 1)
    # every layer job that can fail is a need: isolation and inertness (the base build untouched) must pass; the jobs
    # a fork may legitimately not run (no middle target, no full_gates= list) may be skipped, never failed
    layer = ["layer-plan", "layer-isolation", "layer-inertness", "layer-compile", "layer-gates", "layer-gates-extra"]
    job = re.sub(r"(?m)^    needs: .*$", "    needs: [" + ", ".join(needs + layer) + "]", job, count=1)
    job = re.sub(r"(?m)^    if: .*$", "    if: ${{ !cancelled() && " + ok + " && needs.layer-plan.outputs.layer == 'true'"
                 " && needs.layer-isolation.result == 'success' && needs.layer-inertness.result == 'success'"
                 " && needs.layer-gates.result == 'success' && (needs.layer-compile.result == 'success'"
                 " || needs.layer-compile.result == 'skipped') && (needs.layer-gates-extra.result == 'success'"
                 " || needs.layer-gates-extra.result == 'skipped') && github.ref_name == '{{LAYER_BRANCH}}' && inputs.release }}",
                 job, count=1)
    jars = "      - uses: actions/download-artifact@v4\n        with:\n          name: jars\n          path: jars\n"
    if jars not in job:
        raise SystemExit("port-ci: the release job no longer downloads `jars`; layer_release_job needs updating")
    job = job.replace(jars, jars + "      - uses: actions/download-artifact@v4\n        with:\n          pattern: layer-jars-*\n"
                                   "          merge-multiple: true\n          path: jars\n", 1)
    # a preview target (preview=true in its target.properties; layer-ci.py plan lists them) keeps its jar on the
    # pre-release, and the notes say it is a preview that publish-port will not upload. The list reaches the script
    # through env only, never spliced into it.
    env = "        env:\n          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n"
    built = '            echo "Built from'
    if job.count(env) != 1 or job.count(built) != 1:
        raise SystemExit("port-ci: the release job's notes changed shape; layer_release_job's preview note needs updating")
    job = job.replace(env, env + "          PREVIEW: ${{ needs.layer-plan.outputs.preview }}\n", 1)
    job = job.replace(built, '            if [ -n "$PREVIEW" ]; then\n'
                             '              echo "**Preview** (built and gated, not published to Modrinth or CurseForge): Minecraft ${PREVIEW//,/, }."\n'
                             '              echo\n'
                             '            fi\n' + built, 1)
    return "\n" + job


def render(text, values):
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


def variant_values(values, variant_jars=(), dep_variants=(), dep_specs=(), sha_of=None):
    """The placeholders of the two opt-in variant options. With neither, every one is empty (RELEASE_NEEDS stays
    `gates`) and the workflow is byte-for-byte the variant-free one (self-check: tools/golden/port-ci-*.yml).
      --variant-jar GLOB: a `shipped jars` job (templates/upstream-harness/port-ci-shipped.yml) that boots each built
        jar as shipped; the release needs it.
      --dep-jar-variant SPEC: one more gates leg per alternate dependency asset, `gates (full, <label>)`."""
    v = dict(values, GATES_VARIANTS="", GATES_VARIANT_NAME="", GATES_VARIANT_ARTIFACT="", GATES_VARIANT_JARS_IF="",
             VARIANT_STEPS="", SHIPPED_JOB="", RELEASE_NEEDS="gates")
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
                 RELEASE_NEEDS="[gates, shipped]")
    return v


def recover_args(text):
    """The port-ci.py arguments an installed workflow was generated with, read back from its text: what a re-pin or a
    new option regenerates from, so nothing else moves. Not arguments (read from the checkout instead): the port
    branch (HEAD), the authors' branch (origin/HEAD), the target (gradle.properties) and the Java toolchain."""
    out = []
    one = lambda rx: (re.search(rx, text) or [None, None])[1]
    out += ["--modid", one(r'ci-gates\.py --repo \. --modid "([^"]+)"')]
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
    mc_suffix = re.search(r'tags: \["\*-mc([\d.]+)([^"]*)"\]', text)
    if mc_suffix and mc_suffix.group(2):
        out.append(f"--tag-suffix={mc_suffix.group(2)}")
    layer = one(r'push:\n    branches: \["[^"]+", "([^"]+)"\]')
    if layer:
        out += ["--layer-branch", layer]
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


def fork_workflow(fork, ref, repo_dir=None, gh=None):
    """The fork's port-ci.yml on `ref`, best effort: `gh api` when gh is logged in (a private fork reads too), else
    `git show` from a local clone of that fork (--repo, default the current directory) -> (text or None, how)."""
    gh = gh_logged_in() if gh is None else gh
    if gh:
        r = subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github.raw",
                            f"repos/{fork}/contents/.github/workflows/port-ci.yml?ref={ref}"],
                           capture_output=True, text=True, encoding="utf-8")
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout, "gh api"
    d = repo_dir or "."
    url = subprocess.run(["git", "-C", d, "remote", "get-url", "origin"], capture_output=True, text=True,
                         encoding="utf-8").stdout.strip()
    if re.search(rf"[/:]{re.escape(fork)}(\.git)?/?$", url, re.I):
        for rev in (f"origin/{ref}", ref):
            r = subprocess.run(["git", "-C", d, "show", f"{rev}:.github/workflows/port-ci.yml"], capture_output=True,
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
    a = ap.parse_args(argv)
    ref, inputs = promote_dispatch(a.fork, a.tag, a.played_by, a.notes, a.branch)
    import json
    # best effort, never a reason a fork without fork-port pins cannot promote: the workflow is read with gh or from a
    # local clone; when neither can, a note and the dispatch as before
    gh = gh_logged_in()
    wf, how = fork_workflow(a.fork, ref, a.repo, gh)
    if wf is None:
        print(f"note: could not read {a.fork}'s port-ci.yml on {ref} ({how}): the pinned dependencies are not checked "
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
    cmd = ["gh", "workflow", "run", "port-ci.yml", "--repo", a.fork, "--ref", ref]
    for k, v in inputs.items():
        cmd += ["-f", f"{k}={v}"]
    import shlex
    logged_in = gh
    if a.print or not logged_in:
        print("Dispatch the fork's Port CI workflow (port-ci.yml) on ref", ref, "with inputs", json.dumps(inputs))
        print("  " + shlex.join(cmd))
        if not logged_in and not a.print:
            print("(gh is not logged in here: run that, use Actions -> Port CI -> Run workflow, or let an agent "
                  "dispatch it through its GitHub connector)")
        return 0
    r = subprocess.run(cmd)
    if r.returncode == 0:
        print(f"dispatched: watch https://github.com/{a.fork}/actions/workflows/port-ci.yml")
    return r.returncode


GOLDEN_CASES = (("single-nodeps", False, None), ("single-deps", True, None), ("layer-deps", True, "neoforge-1.21.x"))
FIX_DEP = "o/lib@lib-1.0-mc1.21.1/lib-1.0_mr.jar=com.example.lib:lib:1.0"


def fixture(deps=True, layer=None, variant_jars=(), dep_variants=()):
    """The workflow for fixed, invented values (no network: every sha256 is fake). The three option-free cases are
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
    return render(TEMPLATE.read_text(encoding="utf-8"), layer_values(values, layer))


def regen_golden():
    for name, deps, layer in GOLDEN_CASES:
        (GOLDEN / f"port-ci-{name}.yml").write_text(fixture(deps, layer), encoding="utf-8")
        print(f"wrote tools/golden/port-ci-{name}.yml")
    return 0


def variant_check():
    """--variant-jar / --dep-jar-variant: off by default (byte-identical goldens), and what each adds when given."""
    ok = True
    for name, deps, layer in GOLDEN_CASES:            # neither option: today's workflow, byte for byte, both fork kinds
        ok &= fixture(deps, layer) == (GOLDEN / f"port-ci-{name}.yml").read_text(encoding="utf-8")
    cf = FIX_DEP.replace("_mr.jar", "_cf.jar")
    for layer in (None, "neoforge-1.21.x"):
        base = fixture(True, layer)
        # --variant-jar: one more job, the release needs it, globs only through env, the dep jar passed as --deps
        s = fixture(True, layer, variant_jars=["*_mr.jar", "*_cf.jar"])
        job = s.split("\n  shipped:\n", 1)[1].split("\n  release:\n", 1)[0]
        ok &= s.count("\n  shipped:\n") == 1 and "    name: shipped jars\n" in job
        ok &= "needs: [gates, shipped]" in s and "needs: gates\n" not in s.split("\n  release:\n", 1)[1].split("steps:", 1)[0]
        ok &= 'VARIANT_JARS: "*_mr.jar *_cf.jar"' in job and 'DEP_JARS: ".m2/repository/com/example/lib/lib/1.0/lib-1.0.jar"' in job
        ok &= "shipped-gate.py" in job and "--deps" in job and "log_tails" in job and "::error::shipped jar" in job
        ok &= "!inputs.promote" in job.split("steps:", 1)[0] and "github.event_name != 'release'" in job.split("steps:", 1)[0]
        ok &= "a" * 64 + "  " in job and "b" * 64 + "  " in job          # dependency and kit sha256-checked here too
        ok &= "${{" not in job.split("run: |\n          python3", 1)[1].split("\n      - name: Shipped-jar summary", 1)[0]
        ok &= s.replace(s[s.index("\n\n  shipped:\n"):s.index("\n\n  release:\n")], "").replace(
            "needs: [gates, shipped]", "needs: gates").replace(          # (the layer release waits for it too)
            "needs: [gates, shipped, layer-plan", "needs: [gates, layer-plan").replace(
            " && needs.shipped.result == 'success'", "") == base        # nothing else moved
        # --dep-jar-variant: one more leg, named; default legs keep their check and artifact names; sha256 pinned
        d = fixture(True, layer, dep_variants=[cf])
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
        both = fixture(True, layer, variant_jars=["*_mr.jar"], dep_variants=[cf])
        ok &= "\n  shipped:\n" in both and 'variant: "cf"' in both and "needs: [gates, shipped]" in both
    # tools/port-offer.py reads the default pin only: the variant's curl is written URL-first
    d = fixture(True, None, dep_variants=[cf])
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
    ok &= recover_args(fixture(True, "neoforge-1.21.x", ["*_mr.jar", "*_cf.jar"], [cf])) == [
        "--modid", "examplemod", "--upstream", "https://github.com/a/examplemod", *deps, "--dep-jar-variant", cf,
        "--variant-jar", "*_mr.jar", "--variant-jar", "*_cf.jar", "--dep", "o/other@neoforge-1.21.1", *kit,
        "--layer-branch", "neoforge-1.21.x", *pin]
    sfx = render(TEMPLATE.read_text(encoding="utf-8"), layer_values(variant_values(dict(
        {k: "x" for k in re.findall(r"(?<!\$)\{\{([A-Z0-9_]+)\}\}", TEMPLATE.read_text(encoding="utf-8"))},
        MC="1.21.1", TAG_SUFFIX="-release", MODID="m", GATEC="launch", KIT_VERSION="3", KIT_SHA256="d" * 64,
        UPSTREAM="the original", PRE_BUILD="", BRANCH="neoforge-1.21.1")), None))
    ok &= recover_args(sfx) == ["--modid", "m", "--gatec", "launch", "--tag-suffix=-release", "--kit-version", "3", "--kit-sha", "d" * 64]
    try:
        recover_args("name: something else\n"); ok = False
    except SystemExit:
        pass
    return ok and shipped_script_check() and manifest_script_check()


def manifest_script_check():
    """Run the promote job's manifest-refresh script on a fake fork (a bare origin, a stand-in port-manifest.py), for
    a single-version and a layer fork's workflow: a changed manifest is committed on top of the port branch and
    pushed there (the tag stays in its history); a push the branch moved under is retried on the new tip; an
    unchanged manifest pushes nothing; a tag that is not a tag name never reaches git."""
    import os, tempfile
    sys.path.insert(0, str(ROOT / "tools"))
    from gitbash import BASH
    ok = True
    for layer in (None, "neoforge-1.21.x"):
        wf = fixture(True, layer)
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
                    print(f"manifest script check ({'layer' if layer else 'single-version'}, {case}): exit {r.returncode}, "
                          f"{len(calls)} call(s), origin has {pushed!r}\n{(r.stdout + r.stderr)[-1500:]}")
                ok &= good
    return ok


def shipped_script_check():
    """Run the `shipped jars` job's own script on a fake fork: a gradlew that builds x_mr.jar and x_cf.jar, and a
    stand-in shipped-gate.py that boots _mr and fails _cf. The job must build once, pass the dependency as --deps,
    fail naming the failing variant with its server log tail, and refuse a glob that matches no jar."""
    import os, shutil, tempfile
    wf =fixture(True, None, variant_jars=["*_mr.jar", "*_cf.jar", "*_none.jar"])
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
        (tools / "shipped-gate.py").write_text(
            "import pathlib, sys\n"
            "a = sys.argv\n"
            "w = pathlib.Path(a[a.index('--work-dir') + 1]); (w / 'server/logs').mkdir(parents=True)\n"
            "(w / 'server/logs/latest.log').write_text('server log line\\n', encoding='utf-8')\n"
            "print('[shipped-gate] deps ' + ' '.join(a[a.index('--deps') + 1:]))\n"
            "bad = a[a.index('--jar') + 1].endswith('_cf.jar')\n"
            "print('[shipped-gate] ' + ('FAIL boot -- exited before Done' if bad else 'PASS boot -- started'))\n"
            "sys.exit(1 if bad else 0)\n", encoding="utf-8")
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
        ok &= r.returncode != 0 and len(calls) == 1 and " build cfJar " in f" {calls[0]} "
        ok &= "| *_mr.jar | PASS | x_mr.jar: 1 check(s) green |" in md
        ok &= "| *_cf.jar | FAIL | x_cf.jar: FAIL boot -- exited before Done |" in md
        ok &= "| *_none.jar | FAIL | 0 built jar(s) match *_none.jar in build/libs (none); exactly one must |" in md
        ok &= "::error::shipped jar *_cf.jar (x_cf.jar) failed" in out and "tail of latest.log" in out
        ok &= "deps " + str(home / ".m2/repository/com/example/lib/lib/1.0/lib-1.0.jar") in out
        ok &= "shipped jars failed: *_cf.jar, *_none.jar" in out
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
    v = variant_values(v)
    plain = render(t, layer_values(v, None))
    out = plain
    lay = render(t, layer_values(v, "neoforge-1.21.x"))
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
    # exists, its tag validated, and a failure never stops publish (both fork kinds: the layer file is the same job)
    for w in (plain, lay):
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
    # layer branch: without --layer-branch the workflow has no layer text at all; with it the SAME file gains the
    # triggers and six layer-* jobs, each skipped on the port branch and without .multiversion/
    ok &= "layer-plan" not in plain and "layer-gates" not in plain and "neoforge-1.21.x" not in plain and 'branches: ["neoforge-1.21.1"]' in plain
    ok &= plain.endswith("modrinth-create --repo .\n") and "{{" not in plain.replace("${{", "")
    ok &= "{{" not in lay.replace("${{", "") and lay.count("\n  gates:\n") == 1
    ok &= lay.count('branches: ["neoforge-1.21.1", "neoforge-1.21.x"]') == 2
    for j in ("layer-plan", "layer-isolation", "layer-inertness", "layer-compile", "layer-gates", "layer-gates-extra"):
        ok &= f"\n  {j}:\n" in lay
    ljobs = lay.split("\n  layer-plan:\n", 1)[1]
    ok &= "github.ref_name == 'neoforge-1.21.x' || github.base_ref == 'neoforge-1.21.x'" in ljobs.split("steps:", 1)[0]
    for j in ("layer-isolation", "layer-inertness", "layer-compile", "layer-gates", "layer-gates-extra"):
        body = ljobs.split(f"\n  {j}:\n", 1)[1].split("\n  layer-", 1)[0]
        ok &= "needs: layer-plan" in body and "needs.layer-plan.outputs.layer == 'true'" in body and not any("always()" in l for l in body.splitlines() if l.startswith("    if:"))
    # layer-release: the release job made the layer branch's -- base jars plus every target's, after every layer gate;
    # the main release steps aside there; each layer job fetches its pinned sibling jars first
    rel = lay.split("\n  layer-release:\n", 1)[1]
    ok &= ("needs: [gates, layer-plan, layer-isolation, layer-inertness, layer-compile, layer-gates, layer-gates-extra]" in rel
           and "github.ref_name == 'neoforge-1.21.x' && inputs.release" in rel)
    # every layer job gates the release: isolation/inertness/gates must succeed; compile and gates-extra may be skipped
    rif = next(l for l in rel.splitlines() if l.startswith("    if:"))
    ok &= all(f"needs.{j}.result == 'success'" in rif for j in ("layer-isolation", "layer-inertness", "layer-gates"))
    ok &= all(f"(needs.{j}.result == 'success' || needs.{j}.result == 'skipped')" in rif for j in ("layer-compile", "layer-gates-extra"))
    ok &= "needs.layer-isolation.result == 'skipped'" not in rif and "needs.layer-inertness.result == 'skipped'" not in rif
    ok &= "pattern: layer-jars-*" in rel and "merge-multiple: true" in rel and "gh release create" in rel
    # preview targets (issue #76): the plan's list reaches the notes through env, never spliced into the script
    ok &= "      preview: ${{ steps.plan.outputs.preview }}\n" in ljobs.split("\n  layer-isolation:\n", 1)[0]
    ok &= "PREVIEW: ${{ needs.layer-plan.outputs.preview }}" in rel and 'if [ -n "$PREVIEW" ]; then' in rel
    ok &= "${{ needs.layer-plan.outputs.preview }}" not in rel.split("run: |", 1)[1]
    ok &= "PREVIEW" not in plain and "preview" not in plain.lower()
    ok &= "github.ref_name != 'neoforge-1.21.x'" in lay.split("\n  release:\n    needs:", 1)[1].split("steps:", 1)[0]
    ok &= "github.ref_name !=" not in plain and "layer-release" not in plain and "layer-deps.py" not in plain
    ok &= lay.count("layer-deps.py fetch --repo . --target") == 3 and lay.count("name: layer-jars-") == 2
    # a DEPENDENT fork's layer (#78; tools/golden/port-ci-layer-deps.yml is one: --dep-jar on a library's release): each
    # job that builds a target first puts the library's 1.21.1 jar in mavenLocal, then fetches the library's per-target
    # jars its target.properties pins (url + sha256, from the library's layer-release), the target only through env --
    # and only then builds; the single-version workflow fetches nothing
    dep = fixture(True, "neoforge-1.21.x")
    for j, build in (("layer-compile", "layer-ci.py compile"), ("layer-gates", "ci-gates.py --repo . --modid"),
                     ("layer-gates-extra", "ci-gates.py --repo . --modid")):
        body = dep.split(f"\n  {j}:\n", 1)[1].split("\n\n  ", 1)[0]
        i_lib = body.find("releases/download/lib-1.0-mc1.21.1/lib-1.0_mr.jar")
        i_fetch = body.find('layer-deps.py fetch --repo . --target "$TARGET"')
        ok &= 0 < i_lib < i_fetch < body.find(build) and "sha256sum -c -" in body[i_lib:i_fetch]
        ok &= "--target ${{" not in body and "TARGET: ${{ " in body[:i_fetch]
    ok &= "layer-deps.py" not in fixture(True, None) and "layer-deps.py" not in fixture(False, None)
    ok &= "python3 .multiversion/tools/layer.py isolation --base \"origin/neoforge-1.21.1\"" in lay
    ok &= "layer-ci.py inertness" in lay and "layer-ci.py compile" in lay and "--mc \"$NEWEST\"" in lay
    extra = ljobs.split("\n  layer-gates-extra:\n", 1)[1]
    ok &= "has_full == 'true'" in extra.split("steps:", 1)[0] and "fromJson(needs.layer-plan.outputs.full_matrix)" in extra
    ok &= "fromJson(needs.layer-plan.outputs.matrix)" in lay and "java-version: ${{ matrix.java }}" in lay
    # every needs.<job>.outputs.<name> a job reads is one <job> declares: GitHub reads an undeclared output as '', so a
    # missing line silently skips its job (layer-gates-extra never ran while has_full/full_matrix were undeclared)
    for wf in (plain, lay, dep):
        for job, name in set(re.findall(r"needs\.([\w-]+)\.outputs\.(\w+)", wf)):
            head = wf.split(f"\n  {job}:\n", 1)[1].split("\n    steps:", 1)[0]
            ok &= f"\n      {name}: ${{{{ steps." in head
    ok &= "run: |" in ljobs and "${{ needs" not in "\n".join(l for l in ljobs.splitlines() if l.lstrip().startswith(("python3", "extra=", "git fetch", "curl")))   # exec-ok: line prefixes to match, not a command
    for bad in ("neoforge-1.21.1", "x y"):         # the port branch itself, or not a branch name
        try:
            layer_values(v, bad); ok = False
        except SystemExit:
            pass
    ok &= variant_check()
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    if "--self-check" in sys.argv:
        return self_check()
    if "--regen-golden" in sys.argv:         # only after reading the diff: the goldens are what forks get today
        return regen_golden()
    if sys.argv[1:2] == ["promote"]:
        return promote(sys.argv[2:])
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
    ap.add_argument("--repo", required=True); ap.add_argument("--modid", required=True)
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
    ap.add_argument("--layer-branch", help="the fork's multi-version layer branch (e.g. neoforge-1.21.x): the same workflow "
                    "then also runs on it and carries the layer-* jobs (isolation, inertness, compile-only middle "
                    "targets, full gates on the newest target); skipped on the port branch")
    ap.add_argument("--trailer", action="append", default=[])
    ap.add_argument("--kit-version", type=int, help="Port CI kit to pin (default: the newest published)")
    ap.add_argument("--kit-sha", help="its sha256 (default: read from the release's SHA256SUMS)")
    ap.add_argument("--print", action="store_true", help="print the rendered workflow and change nothing")
    a = ap.parse_args()
    repo = pathlib.Path(a.repo).resolve()
    p = props(repo)
    mc, neo = p.get("minecraft_version"), p.get("neo_version")
    if not mc or not neo:
        sys.exit("gradle.properties has no minecraft_version / neo_version")
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    base = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").stdout.strip().split("/", 1)[-1] or "main"
    mrepo = migrator_repo()
    kver, ksha = kit_pin(mrepo, a.kit_version, a.kit_sha)
    values = {"TARGET": f"NeoForge {neo} (Minecraft {mc})", "BRANCH": branch, "MC": mc, "MODID": a.modid,
              "BASE": base, "JAVA": java_version(repo, mc), "MIGRATOR_REPO": mrepo, "KIT_VERSION": str(kver), "KIT_SHA256": ksha,
              "GATEC": a.gatec, "TAG_SUFFIX": a.tag_suffix, "PRE_BUILD": "\n".join(x for x in (dep_jars(a.dep_jar), pre_build(a.dep)) if x), "UPSTREAM": a.upstream or "the original"}
    values = variant_values(values, a.variant_jar, a.dep_jar_variant, a.dep_jar)
    out = render(TEMPLATE.read_text(encoding="utf-8"), layer_values(values, a.layer_branch))
    if a.print:
        print(out); return 0
    f = repo / WORKFLOW
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(out, encoding="utf-8")
    if a.no_commit:
        print(f"wrote {f}"); return 0
    git(repo, "add", WORKFLOW)
    msg = (f"CI: build, gate and release this port ({values['TARGET']})\n\n"
           "Runs the author's own Gradle build, then the gates the port was held to -- mixin-config\n"
           "integrity, a headless server running GameTests, and a real client under Xvfb -- with no\n"
           "model and no automatic fixes. A tag <mod>-<version>-mc" + mc + " publishes the author-built\n"
           "JARs as a pre-release once every gate passes. The gate harness is not added to this\n"
           f"repository: it comes from {mrepo}'s Port CI kit v{kver} (pinned by sha256), outside the tree.\n"
           "This commit stands alone so it can be dropped from anything offered upstream.\n")
    if a.trailer:
        msg += "\n" + "\n".join(a.trailer) + "\n"
    r = git(repo, "commit", "-q", "-m", msg)
    print(r.stdout + r.stderr or f"committed {WORKFLOW} on {branch}")
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
