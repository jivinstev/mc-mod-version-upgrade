#!/usr/bin/env python3
"""Profile a mod's source by the port patterns that drive cost, and say which tool covers each.

    python3 tools/port-profile.py <repo>[@ref] [<repo>[@ref] ...]

Run it BEFORE porting: the table says which converters to run, which buckets are hand work, and -- across
several mods -- which missing converter would save the most. Counts are FILES that contain the pattern (a
file is the unit of hand work). Reads a git ref without checking it out, so a fork's original branch can be
profiled while a port branch is checked out. Standard library only.
"""
import collections, pathlib, re, subprocess, sys

PAT=[ # (name, regex over file text, covered-by)
 ('vanilla entity renderer', r'extends\s+(Mob|Living|Humanoid|Entity|AgeableMob|Projectile|Arrow|ThrownItem)\w*Renderer\b', 'none (proposed: convert-entity-renderstate)'),
 ('vanilla entity model', r'extends\s+(Hierarchical|Entity|Humanoid|Agean|Ageable|Listed|Quadruped)\w*Model\b', 'none (proposed)'),
 ('vanilla render layer', r'extends\s+RenderLayer\b', 'none (proposed)'),
 ('GeckoLib renderer/layer/model', r'extends\s+(Geo\w*Renderer|GeoRenderLayer|\w*GeoLayer|GeoModel|DefaultedEntityGeoModel|DefaultedBlockGeoModel)', 'renames only (proposed: convert-geckolib)'),
 ('GeckoLib animatable', r'implements\s+[^{]*GeoEntity|GeoBlockEntity|GeoItem|registerControllers\s*\(', 'renames only (proposed)'),
 ('custom RenderType', r'RenderType\.create\s*\(|CompositeState', 'convert-rendertypes'),
 ('custom shader program', r'ShaderInstance|RegisterShadersEvent', 'convert-core-shaders / runtime seam'),
 ('GUI screen/widget', r'extends\s+(Abstract\w*)?(Screen|Widget|ContainerScreen|SelectionList)\b', 'convert-gui-hooks'),
 ('particle class', r'extends\s+(TextureSheet|SimpleAnimated|\w*)Particle\b', 'none'),
 ('capability', r'ICapabilityProvider|ICapabilitySerializable|LazyOptional|AttachCapabilitiesEvent|CapabilityToken', 'forge-shapes partial; worker'),
 ('SimpleChannel packet', r'SimpleChannel|NetworkEvent\.Context', 'convert-simplechannel'),
 ('mixin', r'@Mixin\s*\(', 'audit-mixin-targets (verify only)'),
 ('coremod/agent', r'ITransformationService|ITransformer<|Instrumentation\b', 'none'),
 ('tick event phase', r'TickEvent\.\w+Event', 'forge-shapes tick'),
 ('item NBT', r'getOrCreateTag|getTag\(\)|setTag\(', 'forge-shapes nbt'),
 ('armor/tool tier', r'implements\s+(ArmorMaterial|Tier)\b|extends\s+(ArmorItem|SwordItem|PickaxeItem|DiggerItem|ShieldItem|TieredItem)', 'none'),
 ('entity hurt/save', r'boolean\s+hurt\s*\(DamageSource|addAdditionalSaveData|readAdditionalSaveData', 'none (26.2 ValueIO)'),
 ('SavedData', r'extends\s+SavedData', 'none'),
 ('config spec', r'ForgeConfigSpec', 'recipes'),
]


def files(path, ref):
    out = subprocess.run(["git", "-C", path, "ls-tree", "-r", "--name-only", ref], capture_output=True, text=True).stdout.split()
    return [f for f in out if f.endswith(".java") and f.startswith("src/")]


def profile(path, ref):
    texts = {f: subprocess.run(["git", "-C", path, "show", f"{ref}:{f}"], capture_output=True, text=True,
                               errors="replace").stdout for f in files(path, ref)}
    return len(texts), {name: sum(1 for t in texts.values() if re.search(rx, t)) for name, rx, _ in PAT}


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__); return 0
    if argv[0] == "--self-check":
        return self_check()
    cols = []
    for a in argv:
        path, _, ref = a.partition("@")
        n, counts = profile(path, ref or "HEAD")
        cols.append((pathlib.Path(path).name, n, counts))
    print(f"{'pattern':32s}" + "".join(f"{c[0][:10]:>11s}" for c in cols) + "   covered by")
    print(f"{'(java files)':32s}" + "".join(f"{c[1]:11d}" for c in cols))
    for name, _rx, cov in PAT:
        print(f"{name:32s}" + "".join(f"{c[2][name]:11d}" for c in cols) + "   " + cov)
    return 0


def self_check():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d)
        (p / "src/main/java").mkdir(parents=True)
        (p / "src/main/java/A.java").write_text("class A extends MobRenderer<X, M> { RenderType.create(x); }\n", encoding="utf-8")
        (p / "src/main/java/B.java").write_text("class B extends GeoEntityRenderer<X> {}\n", encoding="utf-8")
        for c in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"]):
            subprocess.run(["git", "-C", d, *c], check=True)
        n, c = profile(d, "HEAD")
        ok = n == 2 and c["vanilla entity renderer"] == 1 and c["custom RenderType"] == 1 and c["GeckoLib renderer/layer/model"] == 1
    print("self-check:", "OK" if ok else f"FAIL {n} {c}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
