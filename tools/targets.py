#!/usr/bin/env python3
"""Everything the upstream-fork porting pipeline needs to know about a TARGET, in one table.

    python3 tools/targets.py                 # the table
    python3 tools/targets.py --branch neoforge-26.2
    python3 tools/targets.py --self-check

tools/port-upstream.py reads the target from the branch it is asked to cut (`neoforge-<minecraft version>`)
and takes every version-specific fact from here, so a fact is stated once and the pipeline has no
`if "1.21.1"` of its own. Two targets exist today:

  1.21.1   from the author's Forge 1.20.1 tree (the source-first port; this is the original behaviour)
  26.2     from OUR finished `neoforge-1.21.1` branch of the same fork (NeoForge -> NeoForge, one era hop)

A fork port stays a single-target, least-diff build: the 26.2 branch is the 1.21.1 branch plus the hop,
not a multi-version workspace (tools/era-hop.py builds that; its maps and transforms are reused, its
frame is not). Standard library only.
"""
import dataclasses, json, pathlib, re, sys, zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent


class UnknownTarget(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class Target:
    mc: str                       # Minecraft version, also the branch suffix
    neo_version: str              # the NeoForge build the pipeline builds against
    neo_range: str                # neoforge.mods.toml: the `neoforge` dependency's versionRange
    mc_range: str                 # ... and the `minecraft` dependency's
    loader_range: str             # ... and loaderVersion
    java: int                     # toolchain
    mdg: str                      # ModDevGradle plugin version
    gradle: str                   # the Gradle wrapper the build needs at least ("" = whatever the build has)
    mixin_compat: str             # compatibilityLevel a mixin config should declare
    mixin_legacy: tuple           # levels a port from an older Java rewrites to mixin_compat
    pack_style: str               # "pack_format" (one number) | "formats" (min_format/max_format ranges)
    pack_resources: tuple         # (major, minor) fallback when the Minecraft jar cannot be read
    pack_data: tuple
    world_version: int            # DataVersion of the structure NBT the harness writes (fallback)
    parchment: bool               # does the build still layer Parchment mappings
    geckolib: dict                # GeckoLib: coordinates, repository, whether its interface injections must be wired ("inject")
    source_dialect: str           # what the tree being ported is written in
    source_desc: str              # ... in words, for prompts
    default_base: str             # the ref the branch is cut from when --base is not given ("" = origin/HEAD)
    mechanical: str               # "forge-1.20.1" | "era" : which mechanical stage runs
    recipe_pack: str              # the mechanical recipe pack (repo-relative), "" for a hop (renames + converters instead)
    rename_table: str             # a hop's hand-written rename table, composed with the generated class-move maps
    members_table: str            # member renames, applied only where javac names the owner type
    client_patch: str             # the call shapes the rename table cannot express in the client harness
    gametest: str                 # "annotation" (@GameTest discovery) | "registration" (26.x: generated registrar)
    note: str                     # one sentence for the designer on the target's build

    @property
    def name(self):
        return f"NeoForge {self.mc}"

    @property
    def branch(self):
        return f"neoforge-{self.mc}"


TARGETS = {
    "1.21.1": Target(
        mc="1.21.1", neo_version="21.1.228", neo_range="[21.1,)", mc_range="[1.21.1,1.22)", loader_range="[4,)",
        java=21, mdg="2.0.146", gradle="", mixin_compat="JAVA_21", mixin_legacy=("JAVA_17", "JAVA_8"),
        pack_style="pack_format", pack_resources=(34, 0), pack_data=(48, 0), world_version=3955,
        parchment=True,
        # the GeckoLib the 1.21.1 port builds against (informational: a first port keeps the author's coordinate)
        geckolib={"version": "4.8.4", "release": "4.8.4", "group": "software.bernie.geckolib",
                  "artifact": "geckolib-neoforge-1.21.1", "repository": "https://dl.cloudsmith.io/public/geckolib3/geckolib/maven/",
                  "repo_group": "software.bernie.geckolib", "inject": False},
        source_dialect="forge-1.20.1", source_desc="Forge 1.20.1, ForgeGradle",
        default_base="", mechanical="forge-1.20.1", gametest="annotation",
        recipe_pack="tools/recipes/forge-1.20-to-neoforge-1.21.1.recipes.tsv", rename_table="", members_table="", client_patch="",
        note="ModDevGradle 2.x builds both NeoForge 21.1.x and 26.2, so the same build can be bumped later."),
    "26.2": Target(
        mc="26.2", neo_version="26.2.0.75", neo_range="[26.2,)", mc_range="[26.2,26.3)", loader_range="[4,)",
        java=25, mdg="2.0.146", gradle="8.14.5", mixin_compat="JAVA_21", mixin_legacy=("JAVA_17", "JAVA_8"),
        pack_style="formats", pack_resources=(88, 0), pack_data=(107, 1), world_version=4903,
        parchment=False,
        # GeckoLib 5.5.x publishes its 26.2 build on Modrinth only; the "version" is Modrinth's version id, and
        # its generics only close with its interface injections applied at compile time (CATALOG V10, V18b).
        geckolib={"version": "IEGPh4CJ", "release": "5.5.4", "group": "maven.modrinth", "artifact": "geckolib",
                  "repository": "https://api.modrinth.com/maven", "repo_group": "maven.modrinth", "inject": True},
        source_dialect="neoforge-1.21.1", source_desc="already ported by us to NeoForge 1.21.1 on the base branch, ModDevGradle",
        default_base="neoforge-1.21.1", mechanical="era", gametest="registration", recipe_pack="",
        rename_table="templates/multi-version/versions/26.2.renames.hand.tsv",
        members_table="tools/recipes/era-26.2-members.tsv", client_patch="templates/upstream-harness/clientboot-26.2.patch.txt",
        note="This is a NeoForge-to-NeoForge era hop: the 1.21.1 build is already ModDevGradle, so the build changes "
             "are a version bump, not a conversion."),
}

# What each field was checked against (a fact here without a source is a guess):
#  neo_version / neo_range / mc_range  the shipped 26.2 port of a library fork (neoforge.mods.toml, gradle.properties)
#  java 25                             the 26.2 version manifest: javaVersion.majorVersion
#  mixin_compat JAVA_21                Mixin 0.17.3 (what NeoForge 26.2 requires) accepts JAVA_6..JAVA_25, and NeoForge's
#                                      own mixin config declares none; JAVA_21 is also what the shipped 26.2 port uses
#  pack formats                        version.json of the Minecraft jar (see derive_pack), NeoForge's own pack.mcmeta
#                                      for 26.2 is a min_format/max_format range
#  parchment                           none exists for the unobfuscated 26.x
#  gradle 8.14.5                       the wrapper of the shipped 26.2 port; measured: with the 1.21.1 port's Gradle 8.8
#                                      the compile classpath fails with "Multiple incompatible variants of
#                                      net.neoforged:neoforge:26.2.0.75 were selected" (jvm.version=25)


def known():
    return sorted(TARGETS, key=lambda v: tuple(int(x) for x in v.split(".")))


def target_of(branch):
    """The Target for a branch named `neoforge-<minecraft version>` (a `feature/` style prefix is allowed)."""
    m = re.search(r"(?:^|/)neoforge-(\d+(?:\.\d+)+)$", branch or "")
    if not m:
        raise UnknownTarget(f"cannot read a target from branch {branch!r}: name it neoforge-<minecraft version>; "
                            f"known targets: {', '.join(known())}")
    if m.group(1) not in TARGETS:
        raise UnknownTarget(f"no pipeline target for Minecraft {m.group(1)} (branch {branch!r}); "
                            f"known targets: {', '.join(known())}")
    return TARGETS[m.group(1)]


def target_of_repo(repo):
    """The Target a checked-out port is on, from gradle.properties' minecraft_version."""
    p = pathlib.Path(repo) / "gradle.properties"
    props = dict(re.findall(r"(?m)^\s*([\w.]+)\s*=\s*(.*?)\s*$", p.read_text(encoding="utf-8", errors="replace"))) \
        if p.exists() else {}
    mc = props.get("minecraft_version")
    if mc not in TARGETS:
        raise UnknownTarget(f"gradle.properties minecraft_version={mc!r} is not a known target ({', '.join(known())})")
    return TARGETS[mc]


def default_base(t, have):
    """`have(ref) -> bool` says whether the fork has that ref. 1.21.1 ports from the author's default branch
    (None: the caller asks origin/HEAD); 26.2 ports from our 1.21.1 branch, and says so when it is absent."""
    if not t.default_base:
        return None
    for ref in (t.default_base, f"origin/{t.default_base}"):
        if have(ref):
            return ref
    raise UnknownTarget(f"{t.branch} is ported from the fork's {t.default_base} branch and it does not exist here: "
                        f"finish the earlier port first, or pass --base <ref>")


# --------------------------------------------------------------------------------------------- pack formats
def _version_json_pack(text):
    d = json.loads(text)
    pv = d.get("pack_version") or {}
    if "resource_major" in pv:
        res, dat = (pv["resource_major"], pv.get("resource_minor", 0)), (pv["data_major"], pv.get("data_minor", 0))
    elif "resource" in pv:
        res, dat = (pv["resource"], 0), (pv["data"], 0)
    else:
        return None
    return {"id": d.get("id"), "resources": res, "data": dat, "world_version": d.get("world_version")}


def derive_pack(t, roots=()):
    """The target's pack and world formats, read from the Minecraft jar's version.json -- the game's own answer
    -- in the first jar that has one for this version (the staged ModDevGradle artifacts under each root, then
    the NeoForm runtime cache). Falls back to the table's values. -> ({resources, data, world_version}, source)"""
    cands = []
    for r in roots:
        r = pathlib.Path(r)
        cands += sorted(r.glob("build/moddev/artifacts/*.jar")) + sorted(r.glob("*.jar"))
    cands += sorted((pathlib.Path.home() / ".gradle/caches/neoformruntime/artifacts").glob(f"minecraft_{t.mc}_client.jar"))
    for j in cands:
        try:
            with zipfile.ZipFile(j) as z:
                info = _version_json_pack(z.read("version.json").decode("utf-8"))
        except (KeyError, zipfile.BadZipFile, OSError, ValueError):
            continue
        if info and info["id"] == t.mc:
            return {"resources": info["resources"], "data": info["data"],
                    "world_version": info["world_version"] or t.world_version}, str(j)
    return {"resources": t.pack_resources, "data": t.pack_data, "world_version": t.world_version}, "table"


def retarget_pack(text, t, fmt):
    """pack.mcmeta -> the target's format keys. `pack_format` style rewrites the number (the original
    behaviour: the resource-pack number); `formats` style (26.x) replaces pack_format / supported_formats with
    min_format/max_format spanning the resource format to the data format, which one mod pack serves both as.
    Idempotent; text that is not recognisable is returned unchanged."""
    if t.pack_style == "pack_format":
        return re.sub(r'("pack_format"\s*:\s*)\d+', r"\g<1>%d" % fmt["resources"][0], text)
    lo, hi = sorted([tuple(fmt["resources"]), tuple(fmt["data"])])
    want = {"min_format": list(lo), "max_format": list(hi)}
    try:
        doc = json.loads(text)
    except ValueError:
        return text
    pack = doc.get("pack") if isinstance(doc, dict) else None
    if not isinstance(pack, dict):
        return text
    if "pack_format" not in pack and "supported_formats" not in pack and \
            all(pack.get(k) == v for k, v in want.items()):
        return text
    m = re.search(r'(?m)^([ \t]*)"pack_format"\s*:\s*\d+,?[ \t]*$', text)
    if m and "supported_formats" not in pack and "min_format" not in pack and "max_format" not in pack:
        ind = m.group(1)          # the common case: replace the one line, leave everything else byte-identical
        comma = "," if m.group(0).rstrip().endswith(",") else ""
        return text[:m.start()] + f'{ind}"min_format": [{lo[0]}, {lo[1]}],\n{ind}"max_format": [{hi[0]}, {hi[1]}]{comma}' \
            + text[m.end():]
    ind = "\t" if re.search(r'(?m)^\t"', text) else " " * (len(re.search(r'(?m)^( +)"', text).group(1)) if re.search(r'(?m)^( +)"', text) else 2)
    for k in ("pack_format", "supported_formats"):
        pack.pop(k, None)
    pack.update(want)
    return json.dumps(doc, indent=ind, ensure_ascii=False) + ("\n" if text.endswith("\n") else "")


# --------------------------------------------------------------------------------------------- metadata text
def retarget_toml(text, t):
    """neoforge.mods.toml / mods.toml text -> the target's loader, dependency ranges and `type =` keys. The
    1.21.1 rules are the original ones, unchanged; on a toml that is already NeoForge's they are no-ops."""
    text = re.sub(r'(?m)^loaderVersion\s*=\s*"[^"]*"', 'loaderVersion = "%s"' % t.loader_range, text)
    text = re.sub(r'(modId\s*=\s*)"forge"', r'\1"neoforge"', text)
    text = re.sub(r'(?m)^(\s*)mandatory\s*=\s*true', r'\1type = "required"', text)
    text = re.sub(r'(?m)^(\s*)mandatory\s*=\s*false', r'\1type = "optional"', text)
    text = re.sub(r'(modId\s*=\s*"neoforge"[^\[]*?versionRange\s*=\s*)"[^"]*"', lambda m: m.group(1) + '"%s"' % t.neo_range,
                  text, flags=re.S)
    text = re.sub(r'(modId\s*=\s*"minecraft"[^\[]*?versionRange\s*=\s*)"[^"]*"', lambda m: m.group(1) + '"%s"' % t.mc_range,
                  text, flags=re.S)
    return text


def retarget_mixin(text, t):
    """A mixin config's refmap (NeoForge runs on official names) and an old compatibility level."""
    out = re.sub(r'\s*"refmap"\s*:\s*"[^"]*"\s*,', "", text)
    out = re.sub(r',\s*"refmap"\s*:\s*"[^"]*"(\s*\})', r"\1", out)
    for lvl in t.mixin_legacy:
        out = out.replace(f'"{lvl}"', f'"{t.mixin_compat}"')
    return out


def dependency_ranges(text):
    """[(modId, versionRange)] of the dependencies that are neither the loader nor Minecraft: the ranges a
    version hop leaves as they were, listed so a person can look (a library range often names the old version)."""
    out = []
    for blk in re.findall(r"\[\[dependencies[^\]]*\]\](.*?)(?=\n\[\[|\Z)", text, re.S):
        mid = re.search(r'modId\s*=\s*"([^"]+)"', blk)
        rng = re.search(r'versionRange\s*=\s*"([^"]*)"', blk)
        if mid and rng and mid.group(1) not in ("neoforge", "minecraft", "forge"):
            out.append((mid.group(1), rng.group(1)))
    return out


# --------------------------------------------------------------------------------------------- build files
def _ver(s):
    return tuple(int(x) for x in re.findall(r"\d+", s))


def _block_end(text, open_brace):
    """Index just past the brace that closes the one at open_brace (strings and // comments skipped)."""
    depth, i, n = 0, open_brace, len(text)
    while i < n:
        ch = text[i]
        if text.startswith("//", i):
            i = text.find("\n", i) if text.find("\n", i) >= 0 else n
            continue
        if ch in "\"'":
            q = ch; i += 1
            while i < n and text[i] != q:
                i += 2 if text[i] == "\\" else 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _gecko_dep(t):
    g = t.geckolib
    return f"{g['group']}:{g['artifact']}"


GECKO = re.compile(r"\b(?:software\.bernie\.geckolib|com\.geckolib)\b|geckolib-neoforge|maven\.modrinth:geckolib|geckolib_version")


def uses_geckolib(text):
    """Does this source or build text compile against GeckoLib (a package, a coordinate or its version property)."""
    return bool(GECKO.search(text))


def bump_build(build, props, t, gecko):
    """The 1.21.1 -> t build bump for an existing ModDevGradle build, as text. Only what the target needs:
    toolchain, plugin, Parchment, GeckoLib. -> (build, props, notes). Idempotent."""
    notes = []
    jv = str(t.java)

    def sub(pattern, repl, text, what, flags=0):
        new, n = re.subn(pattern, repl, text, flags=flags)
        if n and new != text:
            notes.append(what)
        return new
    build = sub(r"(JavaLanguageVersion\.of\(\s*)\d+(\s*\))", lambda m: m.group(1) + jv + m.group(2), build, f"toolchain -> Java {jv}")
    build = sub(r"(JavaVersion\.VERSION_)\d+", lambda m: m.group(1) + jv, build, f"source/target compatibility -> {jv}")
    build = sub(r"(options\.release(?:\s*=\s*|\.set\(\s*))\d+", lambda m: m.group(1) + jv, build, f"javac release -> {jv}")

    def plugin(m):
        return m.group(0) if _ver(m.group(2)) >= _ver(t.mdg) else m.group(1) + t.mdg + m.group(3)
    build = sub(r"""(id\s*\(?\s*['"]net\.neoforged\.moddev['"]\s*\)?\s*version\s*\(?\s*['"])([\d.]+)(['"])""", plugin, build,
                f"ModDevGradle -> {t.mdg}")
    if not t.parchment:
        m = re.search(r"(?m)^([ \t]*)parchment\s*\{", build)
        if m:
            end = _block_end(build, build.index("{", m.start()))
            start = m.start()
            lines = build[:start].split("\n")           # the comment block written for it goes with it
            keep = len(lines)
            while keep > 1 and lines[keep - 2].lstrip().startswith("//"):
                keep -= 1
            start = len("\n".join(lines[:keep - 1])) + (1 if keep > 1 else 0)
            head, tail = build[:start], build[end:]
            tail = tail[1:] if tail.startswith("\n") else tail
            if head.endswith("\n\n") and tail.startswith("\n"):
                tail = tail[1:]                       # do not leave two blank lines where the block was
            build = head + tail
            notes.append("Parchment block removed (no mappings layer exists for the unobfuscated target)")
    if gecko and t.geckolib.get("inject"):
        g = t.geckolib
        prop = re.search(r"(?m)^geckolib_version\s*=", props) is not None
        dep_re = (r"""(['"])software\.bernie\.geckolib:geckolib-neoforge-(?:\$\{[^}]+\}|[\d.]+):"""
                  r"""(\$\{[^}]+\}|[\w.+-]+)\1""")
        # the author's own spelling of the version (`${geckolib_version}`) is kept; a literal becomes the id
        new = re.sub(dep_re, lambda m: m.group(1) + f"{_gecko_dep(t)}:" + (m.group(2) if m.group(2).startswith("$")
                                                                           else g["version"]) + m.group(1), build)
        if new != build:
            notes.append(f"GeckoLib dependency -> {_gecko_dep(t)}:{g['version']} (GeckoLib {g['release']} from Modrinth)")
        build = new
        if g["repository"] not in build:
            m = re.search(r"(?m)^([ \t]*)repositories\s*\{", build)
            if m:
                end = _block_end(build, build.index("{", m.start()))
                ind = m.group(1) + "    "
                add = (f"{ind}// GeckoLib publishes its {t.mc} build on Modrinth only; the version is Modrinth's version id\n"
                       f"{ind}maven {{ name = \"Modrinth\"; url = \"{g['repository']}\"; "
                       f"content {{ includeGroup \"{g['repo_group']}\" }} }}\n")
                build = build[:end - 1].rstrip(" \t") + ("" if build[:end - 1].endswith("\n") else "\n") + add + m.group(1) + build[end - 1:]
                notes.append("Modrinth repository added")
        if "interfaceInjectionData" not in build:
            m = re.search(r"(?m)^([ \t]*)neoForge\s*\{", build)
            if m:
                vm = re.compile(r"(?m)^[ \t]*version\s*=.*$").search(build, m.end())
                at = vm.end() if vm else m.end()
                ind = m.group(1) + "    "
                coord = f"{_gecko_dep(t)}:" + ("${project.geckolib_version}" if prop else g["version"])
                snippet = (f"\n{ind}// GeckoLib 5's generics only close with its interface injections applied at compile time\n"
                           f"{ind}interfaceInjectionData.from(provider {{\n"
                           f"{ind}    def gecko = configurations.detachedConfiguration(dependencies.create(\"{coord}\"))\n"
                           f"{ind}    gecko.transitive = false\n"
                           f"{ind}    zipTree(gecko.singleFile).matching {{ include 'META-INF/interface_injections.json' }}.files\n"
                           f"{ind}}})")
                build = build[:at] + snippet + build[at:]
                notes.append("interfaceInjectionData added")
        if prop:
            def gv(m):
                if m.group(2) == g["version"]:
                    return m.group(0)
                notes.append(f"geckolib_version {m.group(2)} -> {g['version']}")
                return m.group(1) + g["version"]
            props = re.sub(r"(?m)^(geckolib_version\s*=\s*)(\S+)", gv, props)

    def setprop(name, want, props, force=False):
        m = re.search(r"(?m)^(%s\s*=\s*)(\S*)\s*$" % re.escape(name), props)
        if not m or m.group(2) == want:
            return props
        if not force and name == "neo_version" and _ver(m.group(2)) >= _ver(want) and \
                m.group(2).split(".")[:2] == want.split(".")[:2]:
            return props                                  # already on the line, at or past the pinned build
        notes.append(f"{name} {m.group(2)} -> {want}")
        return props[:m.start(2)] + want + props[m.end(2):]
    props = setprop("minecraft_version", t.mc, props)
    props = setprop("neo_version", t.neo_version, props)
    props = setprop("minecraft_version_range", t.mc_range, props)
    props = setprop("neo_version_range", t.neo_range, props)
    return build, props, notes


def bump_wrapper(text, t):
    """gradle-wrapper.properties -> a Gradle at least t.gradle. -> (text, notes). A pinned checksum would no longer
    match the new distribution, so it goes with the URL it vouched for."""
    if not t.gradle:
        return text, []
    m = re.search(r"(distributionUrl=.*?gradle-)([\d.]+)(-(?:bin|all)\.zip)", text)
    if not m or _ver(m.group(2)) >= _ver(t.gradle):
        return text, []
    text = text[:m.start(2)] + t.gradle + text[m.end(2):]
    notes = [f"Gradle wrapper {m.group(2)} -> {t.gradle}"]
    if "distributionSha256Sum" in text:
        text = re.sub(r"(?m)^distributionSha256Sum=.*\n?", "", text)
        notes.append("distributionSha256Sum dropped (it vouched for the old distribution)")
    return text, notes


def build_unmet(build, props, t, gecko, wrapper=None):
    """What a build still lacks for the target, as sentences (empty = ready). The deterministic bump's
    post-check, and the model's brief when it is not enough."""
    out = []
    p = dict(re.findall(r"(?m)^\s*([\w.]+)\s*=\s*(\S*)\s*$", props))
    if p.get("minecraft_version") != t.mc:
        out.append(f"gradle.properties minecraft_version must be {t.mc} (is {p.get('minecraft_version')})")
    nv = p.get("neo_version", "")
    if nv.split(".")[:2] != t.neo_version.split(".")[:2] or _ver(nv) < _ver(t.neo_version):
        out.append(f"gradle.properties neo_version must be {t.neo_version} or later on the same line (is {nv or 'missing'})")
    for m in re.finditer(r"JavaLanguageVersion\.of\(\s*(\d+)", build):
        if m.group(1) != str(t.java):
            out.append(f"the Java toolchain must be {t.java} (is {m.group(1)})")
    for m in re.finditer(r"JavaVersion\.VERSION_(\d+)|options\.release(?:\s*=\s*|\.set\(\s*)(\d+)", build):
        v = m.group(1) or m.group(2)
        if v != str(t.java):
            out.append(f"source/target/release must be {t.java} (is {v})")
    m = re.search(r"""net\.neoforged\.moddev['"]\s*\)?\s*version\s*\(?\s*['"]([\d.]+)""", build)
    if m and _ver(m.group(1)) < _ver(t.mdg):
        out.append(f"ModDevGradle must be {t.mdg} or later (is {m.group(1)})")
    if not t.parchment and re.search(r"(?m)^\s*parchment\s*\{", build):
        out.append("the parchment { } block must go: no Parchment mappings exist for this Minecraft")
    if wrapper is not None and t.gradle and bump_wrapper(wrapper, t)[1]:
        out.append(f"gradle/wrapper/gradle-wrapper.properties must name Gradle {t.gradle} or later")
    if gecko and t.geckolib.get("inject"):
        g = t.geckolib
        if re.search(r"software\.bernie\.geckolib:geckolib-neoforge", build):
            out.append(f"the GeckoLib dependency must be {_gecko_dep(t)}:<version id> (GeckoLib {g['release']} from Modrinth)")
        elif _gecko_dep(t) not in build:
            out.append(f"no {_gecko_dep(t)} dependency (GeckoLib {g['release']})")
        if g["repository"] not in build:
            out.append(f"repositories must include {g['repository']} (includeGroup \"{g['repo_group']}\")")
        if "interfaceInjectionData" not in build:
            out.append("neoForge { } needs interfaceInjectionData from the GeckoLib jar's META-INF/interface_injections.json "
                       "(its generics do not close without it)")
        if re.search(r"(?m)^geckolib_version\s*=\s*(\S+)", props) and p.get("geckolib_version") != g["version"]:
            out.append(f"gradle.properties geckolib_version must be {g['version']} (the Modrinth version id)")
    return out


# --------------------------------------------------------------------------------------------- prompts
def designer_prompt(template, t, branch):
    """The designer prompt for this target: the template's {SOURCE}, {TARGET}, {MC}, {CUT_FROM} and {BRANCH_NOTE} filled in.
    For 1.21.1 the result is the text this pipeline always sent (the placeholders were cut out of it)."""
    later = ("later a `neoforge-26.2` branch follows, so prefer a build that can also target 26.2 (ModDevGradle 2.x builds "
             "both NeoForge 21.1.x and 26.2)" if t.mc == "1.21.1" else
             "the base is our finished NeoForge 1.21.1 port of this repo, so the build is already ModDevGradle and the work is "
             "the era hop to Minecraft 26.2 (Java 25, GeckoLib 5.5.x from Modrinth, Mojang's package reorganisation)")
    return (template.replace("{BRANCH}", branch).replace("{SOURCE}", t.source_desc)
            .replace("{TARGET}", f"NeoForge {t.mc}" if t.mc == "1.21.1" else f"Minecraft {t.mc} / NeoForge {t.neo_version}")
            .replace("{CUT_FROM}", "the author's default branch" if not t.default_base else f"`{t.default_base}`")
            .replace("{MC}", t.mc).replace("{BRANCH_NOTE}", later))


def build_prompt(t, provide_text, design):
    if t.mechanical == "forge-1.20.1":
        return ("Convert this mod's Gradle build to NeoForge 1.21.1 with ModDevGradle (id 'net.neoforged.moddev' version "
                "'2.0.146'), following the BUILD section of the design below. Edit only build.gradle, settings.gradle, "
                "gradle.properties and gradle/wrapper/*. Keep the author's structure: the same source sets, task names, "
                "comments and order; change only what the new toolchain requires (drop reobf/refmap/ForgeGradle-only "
                "pieces, map jarJar). Do not touch Java sources.\n\n" + provide_text + design)
    return (f"This mod's Gradle build already targets NeoForge 1.21.1 with ModDevGradle. Bump it to Minecraft {t.mc} / "
            f"NeoForge {t.neo_version}: Java {t.java} toolchain, ModDevGradle {t.mdg}. Edit only build.gradle, "
            "settings.gradle, gradle.properties and gradle/wrapper/*. Keep the author's structure: the same source sets, "
            "task names, comments and order; change only what the new version requires. Do not touch Java sources.\n\n"
            + provide_text + design)


# --------------------------------------------------------------------------------------------- self-check
def self_check():
    ok = True

    def chk(name, cond):
        nonlocal ok
        if not cond:
            print("  FAIL", name)
        ok &= bool(cond)

    chk("both targets known", known() == ["1.21.1", "26.2"])
    # only in a full checkout: the Port CI kit (tools/port-ci-kit.py) carries the files its gates use, not the
    # pipeline's recipe packs and member tables, and it marks itself with a MANIFEST.txt at its root
    in_kit = (ROOT / "MANIFEST.txt").is_file() and not (ROOT / "CATALOG.md").is_file()
    chk("every file the table names exists", in_kit or all((ROOT / f).is_file() for tt in TARGETS.values()
        for f in (tt.recipe_pack, tt.rename_table, tt.members_table, tt.client_patch) if f))
    chk("a hop names its tables, a first port its pack", TARGETS["26.2"].rename_table and TARGETS["26.2"].members_table
        and TARGETS["26.2"].client_patch and not TARGETS["26.2"].recipe_pack and TARGETS["1.21.1"].recipe_pack
        and TARGETS["1.21.1"].geckolib["inject"] is False)
    chk("a first port's build is never rewritten for GeckoLib", bump_build("repositories {\n}\n", "geckolib_version=4.8.4\n", TARGETS["1.21.1"], True)[2] == [])
    chk("branch -> target", target_of("neoforge-26.2").java == 25 and target_of("neoforge-1.21.1").java == 21
        and target_of("feature/neoforge-26.2").mc == "26.2")
    for bad in ("main", "neoforge-1.20.1", "neoforge-9.9", ""):
        try:
            target_of(bad); chk("unknown rejected " + bad, False)
        except UnknownTarget as e:
            chk("error lists the known targets", "1.21.1" in str(e) and "26.2" in str(e))
    chk("26.2 is cut from the 1.21.1 branch", default_base(TARGETS["26.2"], lambda r: r == "origin/neoforge-1.21.1") == "origin/neoforge-1.21.1"
        and default_base(TARGETS["26.2"], lambda r: r == "neoforge-1.21.1") == "neoforge-1.21.1"
        and default_base(TARGETS["1.21.1"], lambda r: False) is None)
    try:
        default_base(TARGETS["26.2"], lambda r: False); chk("missing 1.21.1 branch is said", False)
    except UnknownTarget as e:
        chk("missing 1.21.1 branch is said", "neoforge-1.21.1" in str(e) and "--base" in str(e))
    # --- metadata text: the 1.21.1 rules are the original ones; the 26.2 ones move the same keys
    forge = 'loaderVersion="[47,)"\n[[dependencies.m]]\nmodId="forge"\nmandatory=true\nversionRange="[47,)"\n' \
            '[[dependencies.m]]\nmodId="minecraft"\nmandatory=true\nversionRange="[1.20.1,1.21)"\n'
    t1, t2 = TARGETS["1.21.1"], TARGETS["26.2"]
    o = retarget_toml(forge, t1)
    chk("1.21.1 toml", 'loaderVersion = "[4,)"' in o and 'modId="neoforge"' in o and 'versionRange="[21.1,)"' in o
        and 'versionRange="[1.21.1,1.22)"' in o and 'type = "required"' in o and "mandatory" not in o)
    o2 = retarget_toml(o, t2)
    chk("26.2 toml hop", 'versionRange="[26.2,)"' in o2 and 'versionRange="[26.2,26.3)"' in o2 and 'modId="neoforge"' in o2)
    chk("toml idempotent", retarget_toml(o2, t2) == o2)
    chk("dependency ranges listed", dependency_ranges(o2 + '[[dependencies.m]]\nmodId="lib"\nversionRange="[4.7,)"\n') == [("lib", "[4.7,)")])
    mx = '{"refmap": "a.refmap.json", "compatibilityLevel": "JAVA_17", "mixins": []}'
    chk("mixin config", '"refmap"' not in retarget_mixin(mx, t1) and '"JAVA_21"' in retarget_mixin(mx, t2)
        and retarget_mixin('{"compatibilityLevel":"JAVA_21"}', t2) == '{"compatibilityLevel":"JAVA_21"}')
    # --- pack.mcmeta
    pm = '{\n  "pack": {\n    "description": "x",\n    "pack_format": 34\n  }\n}\n'
    chk("1.21.1 keeps pack_format", retarget_pack(pm, t1, {"resources": (34, 0), "data": (48, 0)}) == pm)
    p2 = retarget_pack(pm, t2, {"resources": (88, 0), "data": (107, 1)})
    chk("26.2 pack uses a format range", json.loads(p2)["pack"] == {"description": "x", "min_format": [88, 0], "max_format": [107, 1]}
        and p2.endswith("\n") and p2.count("\n") == pm.count("\n") + 1)
    chk("pack idempotent", retarget_pack(p2, t2, {"resources": (88, 0), "data": (107, 1)}) == p2)
    sup = '{"pack": {"pack_format": 15, "supported_formats": [15, 34], "description": "d"}}'
    chk("supported_formats is dropped, not left to contradict", "supported_formats" not in retarget_pack(
        sup, t2, {"resources": (88, 0), "data": (107, 1)}) and retarget_pack("not json", t2, {"resources": (88, 0), "data": (107, 1)}) == "not json")
    # --- derive_pack reads the jar's own answer, in either version.json shape
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        j = pathlib.Path(d) / "build/moddev/artifacts"; j.mkdir(parents=True)
        with zipfile.ZipFile(j / "minecraft-26.2-merged.jar", "w") as z:
            z.writestr("version.json", json.dumps({"id": "26.2", "world_version": 4903, "pack_version": {
                "resource_major": 88, "resource_minor": 0, "data_major": 107, "data_minor": 1}}))
        got, src = derive_pack(t2, [d])
        chk("pack derived from the staged jar", got["resources"] == (88, 0) and got["data"] == (107, 1) and src.endswith(".jar"))
        with zipfile.ZipFile(j / "neoforge-merged.jar", "w") as z:
            z.writestr("version.json", json.dumps({"id": "1.21.1", "world_version": 3955, "pack_version": {"resource": 34, "data": 48}}))
        got, _s = derive_pack(t1, [d])
        chk("old-shape version.json", got["resources"] == (34, 0) and got["data"] == (48, 0) and got["world_version"] == 3955)
    fb, src = derive_pack(t2, ["/nonexistent"])
    chk("fallback is the table", src in ("table",) or src.endswith("minecraft_26.2_client.jar"))
    # --- build bump
    build = """plugins {
    id 'net.neoforged.moddev' version '2.0.120'
}
java.toolchain.languageVersion = JavaLanguageVersion.of(21)
neoForge {
    version = neo_version

    // Parchment mappings are layered on top.
    // Simply re-run setup.
    parchment {
        minecraftVersion = minecraft_version
        mappingsVersion = mapping_version
    }
    runs { client { client() } }
}
repositories {
    mavenCentral()
    maven {
        url = "https://dl.cloudsmith.io/public/geckolib3/geckolib/maven/"
    }
}
dependencies {
    implementation("software.bernie.geckolib:geckolib-neoforge-${minecraft_version}:${geckolib_version}")
}
java { sourceCompatibility = JavaVersion.VERSION_21 }
tasks.withType(JavaCompile).configureEach { it.options.release = 21 }
"""
    props = "minecraft_version=1.21.1\nminecraft_version_range=[1.21.1,1.22)\nneo_version=21.1.172\nneo_version_range=[21.1,)\n" \
            "mapping_version=2024.11.17\ngeckolib_version=4.7.1\n"
    nb, np_, notes = bump_build(build, props, t2, True)
    chk("26.2 build is complete", build_unmet(nb, np_, t2, True) == [] and len(notes) >= 8)
    chk("the build before the bump is not", len(build_unmet(build, props, t2, True)) >= 8)
    chk("bump kept the author's text", "mavenCentral()" in nb and "runs { client { client() } }" in nb
        and "// Parchment" not in nb and "parchment" not in nb.replace("Parchment mappings", "")
        and nb.count("\n") > build.count("\n") - 8)
    chk("gecko wiring", 'implementation("maven.modrinth:geckolib:${geckolib_version}")' in nb and "api.modrinth.com/maven" in nb
        and "interfaceInjectionData.from(provider {" in nb and "geckolib_version=IEGPh4CJ" in np_
        and "minecraft_version=26.2" in np_ and "neo_version=26.2.0.75" in np_ and "neo_version_range=[26.2,)" in np_
        and "minecraft_version_range=[26.2,26.3)" in np_)
    wr = "distributionBase=GRADLE_USER_HOME\ndistributionUrl=https\\://services.gradle.org/distributions/gradle-8.8-bin.zip\ndistributionSha256Sum=abc\n"
    w2, wn = bump_wrapper(wr, t2)
    chk("wrapper bump", "gradle-8.14.5-bin.zip" in w2 and "Sha256" not in w2 and len(wn) == 2 and bump_wrapper(w2, t2) == (w2, [])
        and bump_wrapper(wr, t1) == (wr, []) and bump_wrapper(w2.replace("8.14.5", "9.3.1"), t2)[1] == []
        and build_unmet(nb, np_, t2, True, wr) == [f"gradle/wrapper/gradle-wrapper.properties must name Gradle {t2.gradle} or later"]
        and build_unmet(nb, np_, t2, True, w2) == [])
    chk("bump idempotent", bump_build(nb, np_, t2, True)[:2] == (nb, np_) and bump_build(nb, np_, t2, True)[2] == [])
    nb2, np2, _n = bump_build(build, props.replace("neo_version=21.1.172", "neo_version=26.2.0.80"), t2, False)
    chk("a newer NeoForge on the line is kept; no gecko, no gecko edits", "neo_version=26.2.0.80" in np2
        and "interfaceInjectionData" not in nb2 and build_unmet(nb2, np2, t2, False) == [])
    chk("a brace-bearing string does not end the parchment block", _block_end('a { "}" { } } b', 2) == len('a { "}" { } }'))
    d = designer_prompt("a {SOURCE} -> {TARGET} {BRANCH} {CUT_FROM}; {BRANCH_NOTE}", t2, "neoforge-26.2")
    chk("designer prompt names both ends", "NeoForge 1.21.1" in d and "26.2.0.75" in d and "{" not in d)
    chk("build prompt per target", "ForgeGradle" in build_prompt(t1, "", "") and "already targets NeoForge 1.21.1" in build_prompt(t2, "", "")
        and "Java 25" in build_prompt(t2, "", ""))
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    if "--self-check" in sys.argv:
        return self_check()
    br = sys.argv[sys.argv.index("--branch") + 1] if "--branch" in sys.argv else None
    for v in ([target_of(br).mc] if br else known()):
        t = TARGETS[v]
        print(f"{v}: {t.name}\n" + "\n".join(f"  {k:16} {val}" for k, val in dataclasses.asdict(t).items() if k not in ("mc", "note")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
