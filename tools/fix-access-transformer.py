#!/usr/bin/env python3
"""Make a Forge 1.20.1 access transformer valid for NeoForge: official names, and override-aware.

    python3 tools/fix-access-transformer.py --work <gradle project> [--srg-map srg2official.json]
                                            [--overrides-from <compile log>]

Two things break when a mod's own SOURCE (not a decompile) carries its Forge access transformer across:

  1. Forge 1.20.1 ATs name members by SRG id (`m_6475_`, `f_19853_`). NeoForge runs on Mojang's official
     names, so those lines match nothing. --srg-map rewrites them with the same mapping the Java remap uses
     (tools/srg-remap/build_mapping.py). A decompile never carries an AT in that form, which is why the
     Java remap alone was enough until source-first ports.
  2. An entry that widens a METHOD vanilla subclasses override (`public-f LivingEntity actuallyHurt`) makes
     NeoForge's recompile of Minecraft fail: each `protected` override now "assigns weaker access". ForgeGradle
     tolerated it. --overrides-from reads those errors out of a compile log and widens the same method on
     each overriding class -- the faithful fix, since the author wanted the base method public. Run the
     compile again afterwards; repeat until the log has none (a subclass of a subclass appears one round
     later).

Never removes an entry. Prints what it changed. Standard library only.
"""
import argparse, json, pathlib, re, sys

OVERRIDE = re.compile(r"/transformed/(?P<path>net/minecraft/\S+?)\.java:\d+: error: (?P<m>[\w$]+)\([^)]*\) in [\w$.]+ "
                      r"cannot override (?P=m)\([^)]*\) in (?P<base>[\w$.]+)")


def at_files(work):
    return sorted(pathlib.Path(work).glob("src/*/resources/META-INF/accesstransformer.cfg"))


def remap_srg(text, mapping):
    n = 0

    def sub(m):
        nonlocal n
        v = mapping.get(m.group(0))
        if v:
            n += 1
            return v
        return m.group(0)
    return re.sub(r"\b[fm]_\d+_\b", sub, text), n


def entries(text):
    """(access, class fqn, member, descriptor or '') for every entry line."""
    out = []
    for line in text.splitlines():
        s = line.split("#", 1)[0].strip()
        p = s.split()
        if len(p) >= 3:
            m = re.match(r"([\w$<>]+)(\(.*)?$", p[2])
            if m:
                out.append((p[0], p[1], m.group(1), m.group(2) or ""))
    return out


def override_lines(log_text, text):
    """New entries widening each overriding subclass's method, from 'cannot override' errors."""
    have = {(c, m, d) for _a, c, m, d in entries(text)}
    adds = []
    for e in OVERRIDE.finditer(log_text):
        sub_cls = e.group("path").replace("/", ".")
        base_simple = e.group("base").split(".")[-1]
        for _a, cls, member, desc in entries(text):
            if member == e.group("m") and desc and cls.split(".")[-1].split("$")[-1] == base_simple:
                key = (sub_cls, member, desc)
                if key not in have:
                    have.add(key)
                    adds.append(f"public {sub_cls} {member}{desc} # overrides {cls}.{member}, widened there")
                break
    return adds


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work"); ap.add_argument("--srg-map"); ap.add_argument("--overrides-from")
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    files = at_files(a.work)
    if not files:
        print("fix-access-transformer: no accesstransformer.cfg; nothing to do"); return 0
    mapping = json.loads(pathlib.Path(a.srg_map).read_text(encoding="utf-8")) if a.srg_map else {}
    log_text = pathlib.Path(a.overrides_from).read_text(encoding="utf-8", errors="replace") if a.overrides_from else ""
    for f in files:
        text = f.read_text(encoding="utf-8")
        n = 0
        if mapping:
            text, n = remap_srg(text, mapping)
        adds = override_lines(log_text, text) if log_text else []
        if adds:
            text = text.rstrip("\n") + "\n" + "\n".join(adds) + "\n"
        f.write_text(text, encoding="utf-8")
        left = len(re.findall(r"\b[fm]_\d+_\b", text))
        print(f"fix-access-transformer: {f.relative_to(a.work)}: {n} SRG name(s) remapped"
              + (f", {left} left unmapped" if left else "") + f", {len(adds)} override widening(s) added")
    return 0


def self_check():
    at = ("public-f net.minecraft.world.entity.LivingEntity m_6475_(Lnet/minecraft/world/damagesource/DamageSource;F)V\n"
          "public net.minecraft.world.entity.Entity f_19853_\n")
    t, n = remap_srg(at, {"m_6475_": "actuallyHurt", "f_19853_": "level"})
    ok = n == 2 and "actuallyHurt(Lnet" in t and "Entity level" in t
    log = ("/x/steps/transformSource/transformed/net/minecraft/world/entity/player/Player.java:1025: error: "
           "actuallyHurt(DamageSource,float) in Player cannot override actuallyHurt(DamageSource,float) in LivingEntity\n"
           "  attempting to assign weaker access privileges; was public\n")
    adds = override_lines(log, t)
    ok &= adds == ["public net.minecraft.world.entity.player.Player actuallyHurt(Lnet/minecraft/world/damagesource/"
                   "DamageSource;F)V # overrides net.minecraft.world.entity.LivingEntity.actuallyHurt, widened there"]
    ok &= override_lines(log, t + "\n" + adds[0]) == []       # idempotent
    print("self-check:", "OK" if ok else f"FAIL {adds}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
