#!/usr/bin/env python3
"""Convert 1.21-style core shader programs (JSON + GLSL 150 loose uniforms) to 26.x uniform blocks.

    python3 tools/convert-core-shaders.py --shaders <src/main/resources/assets/NS/shaders> --ns NS --block NsUniforms [--check]

Up to 1.21 a mod's core shader is a JSON program naming its vertex/fragment files, samplers and uniforms, and
the GLSL declares each uniform loosely (`uniform float GameTime;`); the game set them one by one. 26.x has no
loose uniforms: every non-sampler value lives in a std140 uniform BLOCK that a RenderPass binds by name, and
the JSON program is no longer read (a RenderPipeline names the shaders). This rewrites the GLSL so a port can
keep the author's shader bodies byte for byte:

  * `#version 150` becomes `#version 330`;
  * the loose non-sampler `uniform` lines are removed;
  * one generated include, `include/<block in snake case>.glsl`, declares
      - the vanilla blocks every RenderType draw binds -- DynamicTransforms (ModelViewMat), Projection (ProjMat)
        and Globals (GameTime, ScreenSize, GlintAlpha); members the mod does not take from vanilla are renamed
        `Mc*` so they cannot collide with its own names (std140 binds by offset, not name);
      - the mod's own block, `<block>`: the union of every other uniform any program declares, in a fixed
        order. One layout for the whole mod keeps the Java writer trivial and a shared .fsh valid for every
        program that uses it; a member a program does not use costs nothing.
    and each converted file imports it with `#moj_import <NS:<include>.glsl>`.

Which names bind to vanilla is decided by what 1.21 did, not by the name: ShaderInstance.setDefaultUniforms ran at
draw AFTER a mod's setupRenderState, so a mod's own GameTime/ScreenSize/GlintAlpha were overwritten and the shader
always saw vanilla's -- which 26.x's Globals block still carries (same formula). ColorModulator stays the mod's:
26.x's DynamicTransforms copy is always white for a RenderType draw, while 1.21 let a mod override it after.
The JSON programs are left in place as the author's data; the Java side reads the defaults and samplers from
them and the member order from the include (whose block lines are `<type> <name>;`, one per line).
Idempotent; --check exits 1 if anything would change. Standard library only.
"""
import argparse, json, pathlib, re, sys

# what 1.21 vanilla set itself at draw (ShaderInstance.setDefaultUniforms, AFTER the mod's own setup ran) and
# 26.x still provides in a vanilla block -- so binding these to vanilla is the faithful port
VANILLA = {"ModelViewMat": "DynamicTransforms", "ProjMat": "Projection", "GameTime": "Globals", "ScreenSize": "Globals",
           "GlintAlpha": "Globals"}
VANILLA_BLOCKS = {"DynamicTransforms": ["mat4 ModelViewMat", "vec4 ColorModulator", "vec3 ModelOffset", "mat4 TextureMat"],
                  "Projection": ["mat4 ProjMat"],
                  "Globals": ["ivec3 CameraBlockPos", "vec3 CameraOffset", "vec2 ScreenSize", "float GlintAlpha",
                              "float GameTime", "int MenuBlurRadius", "int UseRgss"]}
GLSL = {("float", 1): "float", ("float", 2): "vec2", ("float", 3): "vec3", ("float", 4): "vec4",
        ("int", 1): "int", ("int", 2): "ivec2", ("int", 3): "ivec3", ("int", 4): "ivec4",
        ("matrix4x4", 16): "mat4", ("matrix3x3", 9): "mat3"}
LOOSE = re.compile(r"(?m)^[ \t]*uniform[ \t]+(?!sampler)(\w+)[ \t]+(\w+)[ \t]*;[ \t]*\r?\n")


def snake(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def stage_file(core, ref, ext):
    return core / (ref.split(":", 1)[-1] + ext)


def load(shaders):
    core = shaders / "core"
    programs = sorted(core.glob("*.json"))
    order, types, files = [], {}, set()
    for p in programs:
        j = json.loads(p.read_text(encoding="utf-8"))
        files.add(stage_file(core, j["vertex"], ".vsh")); files.add(stage_file(core, j["fragment"], ".fsh"))
        for u in j.get("uniforms", []):
            name, key = u["name"], (u["type"], u.get("count", 1))
            if name in VANILLA:
                continue
            if key not in GLSL:
                sys.exit(f"convert-core-shaders: {p.name}: uniform {name} has type {key}, which has no mapping")
            if name in types and types[name] != GLSL[key]:
                sys.exit(f"convert-core-shaders: uniform {name} is {types[name]} in one program and {GLSL[key]} in "
                         f"{p.name}; one block cannot hold both")
            if name not in types:
                types[name] = GLSL[key]; order.append(name)
    return programs, order, types, sorted(files)


def include_text(block, order, types):
    lines = ["#version 330", "", "// Generated from this mod's core/*.json programs; the Java side reads the member order below."]
    for name, members in VANILLA_BLOCKS.items():
        lines.append(f"layout(std140) uniform {name} {{")
        for m in members:
            typ, field = m.split()
            lines.append(f"    {typ} {field if VANILLA.get(field) == name else 'Mc' + field};")
        lines += ["};", ""]
    lines.append(f"layout(std140) uniform {block} {{")
    lines += [f"    {types[n]} {n};" for n in order] + ["};", ""]
    return "\n".join(lines)


def convert(text, ns, inc, known):
    if f"#moj_import <{ns}:{inc}>" in text:
        return text, []
    unknown = [n for _, n in LOOSE.findall(text) if n not in known]
    text = LOOSE.sub(lambda m: "" if m.group(2) in known else m.group(0), text)
    text, n = re.subn(r"(?m)^#version\s+150[^\n]*$", f"#version 330\n\n#moj_import <{ns}:{inc}>", text, count=1)
    if not n:
        unknown.append("<no #version 150 line>")
    return text, unknown


def run(shaders, ns, block, check=False):
    shaders = pathlib.Path(shaders)
    programs, order, types, files = load(shaders)
    if not programs:
        sys.exit(f"convert-core-shaders: no core/*.json programs under {shaders} -- nothing checked, NOT a pass")
    inc = snake(block) + ".glsl"
    known = set(order) | set(VANILLA)
    changed, problems = [], []
    want = {shaders / "include" / inc: include_text(block, order, types)}
    for f in files:
        if not f.exists():
            problems.append(f"{f.name}: named by a program but missing"); continue
        new, unknown = convert(f.read_text(encoding="utf-8"), ns, inc, known)
        problems += [f"{f.name}: {u} is declared but no program lists it" for u in unknown]
        want[f] = new
    for f, t in want.items():
        if not f.exists() or f.read_text(encoding="utf-8") != t:
            changed.append(f)
            if not check:
                f.parent.mkdir(parents=True, exist_ok=True); f.write_text(t, encoding="utf-8")
    return len(programs), len(files), order, changed, problems


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--shaders"); ap.add_argument("--ns"); ap.add_argument("--block")
    ap.add_argument("--check", action="store_true"); ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not (a.shaders and a.ns and a.block):
        ap.error("--shaders, --ns and --block are required")
    n, nf, order, changed, problems = run(a.shaders, a.ns, a.block, a.check)
    print(f"convert-core-shaders: {n} program(s), {nf} stage file(s), block {a.block} = {len(order)} member(s); "
          f"{len(changed)} file(s) {'would change' if a.check else 'written'}")
    for p in problems:
        print("  PROBLEM " + p)
    return 1 if problems or (a.check and changed) else 0


def self_check():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        core = pathlib.Path(d) / "core"; core.mkdir()
        prog = lambda name, extra: {"vertex": "m:a", "fragment": "m:a", "samplers": [{"name": "Sampler0"}],
                                    "uniforms": [{"name": "ModelViewMat", "type": "matrix4x4", "count": 16},
                                                 {"name": "ProjMat", "type": "matrix4x4", "count": 16},
                                                 {"name": "ColorModulator", "type": "float", "count": 4}] + extra}
        (core / "a.json").write_text(json.dumps(prog("a", [{"name": "GameTime", "type": "float", "count": 1}])), encoding="utf-8")
        (core / "a_block.json").write_text(json.dumps(prog("b", [{"name": "Glow", "type": "float", "count": 1}])), encoding="utf-8")
        (core / "a.vsh").write_text("#version 150\n\nin vec3 Position;\nuniform mat4 ModelViewMat;\nuniform mat4 ProjMat;\n"
                                    "void main(){ gl_Position = ProjMat * ModelViewMat * vec4(Position,1.0); }\n", encoding="utf-8")
        (core / "a.fsh").write_text("#version 150\nuniform vec4 ColorModulator;\nuniform sampler2D Sampler0;\n"
                                    "uniform float GameTime;\nuniform float Glow;\nout vec4 fragColor;\n"
                                    "void main(){ fragColor = ColorModulator * GameTime * Glow; }\n", encoding="utf-8")
        n, nf, order, changed, problems = run(d, "m", "ModUniforms")
        inc = (pathlib.Path(d) / "include/mod_uniforms.glsl").read_text(encoding="utf-8")
        fsh = (core / "a.fsh").read_text(encoding="utf-8")
        ok = (n == 2 and order == ["ColorModulator", "Glow"] and not problems
              and "uniform ModUniforms {\n    vec4 ColorModulator;\n    float Glow;\n};" in inc
              and "vec4 McColorModulator;" in inc and "    float GameTime;" in inc and "int McMenuBlurRadius;" in inc
              and fsh.startswith("#version 330\n\n#moj_import <m:mod_uniforms.glsl>\n")
              and "uniform sampler2D Sampler0;" in fsh and "uniform float" not in fsh and "uniform vec4" not in fsh)
        again = run(d, "m", "ModUniforms", check=True)
        ok &= not again[3]                                                   # idempotent
        (core / "a.fsh").write_text("#version 150\nuniform float Stray;\nvoid main(){}\n", encoding="utf-8")
        ok &= any("Stray" in p for p in run(d, "m", "ModUniforms")[4])       # a GLSL-only uniform is refused
    print("self-check:", "OK" if ok else f"FAIL order={order} problems={problems}\n{inc}\n{fsh}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
