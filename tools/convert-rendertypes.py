#!/usr/bin/env python3
"""Convert 1.21 `RenderType.create(... CompositeState ...)` into 26.x `RenderSetup` + `RenderPipeline` state.

    python3 tools/convert-rendertypes.py --src src/main/java --state-type <fqn> --state-factory <fqn.method> [--dry-run]

1.21:  RenderType.create(name, format, mode, bufferSize, affectsCrumbling, sortOnUpload,
           RenderType.CompositeState.builder().setShaderState(S).setTransparencyState(...)...createCompositeState(outline))
26.x:  a RenderType is a name plus a RenderSetup; everything the GPU state shards used to switch (blend, depth
       test and write, colour mask, culling, depth bias, vertex format, topology) moved into an immutable
       RenderPipeline, and what is bound per draw (textures, lightmap, overlay) stays on the RenderSetup.

The vanilla shard -> pipeline table is fixed and exact; anything outside it is REFUSED by name rather than
guessed (1.21 is LEQUAL depth, 26.x is reverse-Z GREATER_THAN_OR_EQUAL -- a guess there renders nothing).
A custom shader shard (`new RenderStateShard.ShaderStateShard(X::getShader) { setupRenderState() {...} }`) has
no vanilla meaning, so the port supplies one: --state-type names the type the shard field becomes and
--state-factory the call that builds it from the shader supplier and the old setup body, and the type must
offer `RenderPipeline pipeline(String name, UnaryOperator<RenderPipeline.Builder> state)`. A shader shard that
is a method parameter keeps its name and takes the new type.
Texture shards become the Identifier they wrapped (`withTexture("Sampler0", id)`); the 1.21 blur/mipmap flags
are dropped -- 26.x samples with the texture's own sampler -- and the tool says so per site.
Standard library only.
"""
import argparse, importlib.util, pathlib, re, sys

_here = pathlib.Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location("ni", _here / "normalise-imports.py")
ni = importlib.util.module_from_spec(_s); _s.loader.exec_module(ni)

TRANSPARENCY = {"NO_TRANSPARENCY": None, "TRANSLUCENT_TRANSPARENCY": "TRANSLUCENT", "ADDITIVE_TRANSPARENCY": "ADDITIVE",
                "LIGHTNING_TRANSPARENCY": "LIGHTNING", "GLINT_TRANSPARENCY": "GLINT", "OVERLAY_TRANSPARENCY": "OVERLAY"}
DEPTH = {"NO_DEPTH_TEST": "ALWAYS_PASS", "LEQUAL_DEPTH_TEST": "GREATER_THAN_OR_EQUAL", "EQUAL_DEPTH_TEST": "EQUAL",
         "GREATER_DEPTH_TEST": "LESS_THAN"}                                   # 26.x is reverse-Z
WRITE = {"COLOR_DEPTH_WRITE": ("WRITE_ALL", True), "COLOR_WRITE": ("WRITE_ALL", False), "DEPTH_WRITE": ("WRITE_NONE", True)}
LAYERING = {"NO_LAYERING": None, "POLYGON_OFFSET_LAYERING": "bias", "VIEW_OFFSET_Z_LAYERING": "VIEW_OFFSET_Z_LAYERING"}
OUTPUT = {"MAIN_TARGET": None, "OUTLINE_TARGET": "OUTLINE_TARGET", "WEATHER_TARGET": "WEATHER_TARGET",
          "ITEM_ENTITY_TARGET": "ITEM_ENTITY_TARGET"}
FORMATS = {"NEW_ENTITY": "ENTITY"}
SHEETS = {"BLOCK_SHEET_MIPPED", "BLOCK_SHEET"}
IMPORTS = {"RenderSetup": "net.minecraft.client.renderer.rendertype.RenderSetup",
           "PrimitiveTopology": "com.mojang.blaze3d.PrimitiveTopology",
           "ColorTargetState": "com.mojang.blaze3d.pipeline.ColorTargetState",
           "BlendFunction": "com.mojang.blaze3d.pipeline.BlendFunction",
           "DepthStencilState": "com.mojang.blaze3d.pipeline.DepthStencilState",
           "CompareOp": "com.mojang.blaze3d.platform.CompareOp",
           "GpuFormat": "com.mojang.blaze3d.GpuFormat",
           "Optional": "java.util.Optional",
           "LayeringTransform": "net.minecraft.client.renderer.rendertype.LayeringTransform",
           "OutputTarget": "net.minecraft.client.renderer.rendertype.OutputTarget",
           "TextureAtlas": "net.minecraft.client.renderer.texture.TextureAtlas"}
OWNER = r"(?:(?:RenderType|RenderStateShard)\.)?"


def close_of(masked, open_i):
    depth = 0
    for i in range(open_i, len(masked)):
        c = masked[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def split_top(text, masked):
    out, depth, start = [], 0, 0
    for i, c in enumerate(masked):
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            out.append(text[start:i]); start = i + 1
    out.append(text[start:])
    return [a.strip() for a in out]


def chain_calls(text, masked):
    """`.setXState(arg)` calls of a builder chain, in order, with their raw argument text."""
    calls, i = [], 0
    for m in re.finditer(r"\.(\w+)\s*\(", masked):
        if m.start() < i:
            continue
        end = close_of(masked, m.end() - 1)
        calls.append((m.group(1), text[m.end():end].strip())); i = end
    return calls


def const(expr, table, what):
    m = re.fullmatch(OWNER + r"([A-Z_]+)", expr)
    if not m or m.group(1) not in table:
        raise ValueError(f"{what} {expr!r} is not in the table")
    return table[m.group(1)]


def texture(expr):
    expr = expr.strip()
    m = re.fullmatch(OWNER + r"([A-Z_]+)", expr)
    if m and m.group(1) in SHEETS:
        return "TextureAtlas.LOCATION_BLOCKS", "block sheet"
    if m and m.group(1) == "NO_TEXTURE":
        return None, None
    m = re.fullmatch(r"new\s+(?:RenderStateShard\.)?TextureStateShard\s*\((.*)\)", expr, re.S)
    if m:
        args = split_top(m.group(1), ni.code_spans(m.group(1)))
        return args[0], (None if args[1:] == ["false", "false"] else f"blur/mipmap flags {args[1:]} dropped")
    if re.fullmatch(r"[a-z]\w*", expr):                                     # a TextureStateShard parameter
        return expr, None
    raise ValueError(f"texture {expr!r} is not a texture shard this tool understands")


def convert_call(args, name_expr, ind="        "):
    fmt, mode, _size, crumb, sort, comp = args
    cm = ni.code_spans(comp)
    calls = chain_calls(comp, cm)
    if not calls or calls[-1][0] != "createCompositeState" or "CompositeState.builder" not in comp:
        raise ValueError("last argument is not a CompositeState builder chain")
    st = {"shader": None, "tex": None, "blend": None, "depth": "GREATER_THAN_OR_EQUAL", "write": ("WRITE_ALL", True),
          "cull": True, "lightmap": False, "overlay": False, "layer": None, "output": None}
    notes = []
    for meth, arg in calls[:-1]:
        if meth == "builder":
            continue
        if meth == "setShaderState":
            st["shader"] = arg
        elif meth == "setTextureState":
            st["tex"], note = texture(arg)
            if note and note != "block sheet":
                notes.append(note)
        elif meth == "setTransparencyState":
            st["blend"] = const(arg, TRANSPARENCY, "transparency")
        elif meth == "setDepthTestState":
            st["depth"] = const(arg, DEPTH, "depth test")
        elif meth == "setWriteMaskState":
            st["write"] = const(arg, WRITE, "write mask")
        elif meth == "setCullState":
            st["cull"] = {"CULL": True, "NO_CULL": False}[const(arg, {"CULL": "CULL", "NO_CULL": "NO_CULL"}, "cull")]
        elif meth == "setLightmapState":
            st["lightmap"] = const(arg, {"LIGHTMAP": True, "NO_LIGHTMAP": False}, "lightmap")
        elif meth == "setOverlayState":
            st["overlay"] = const(arg, {"OVERLAY": True, "NO_OVERLAY": False}, "overlay")
        elif meth == "setLayeringState":
            st["layer"] = const(arg, LAYERING, "layering")
        elif meth == "setOutputState":
            st["output"] = const(arg, OUTPUT, "output target")
        else:
            raise ValueError(f"shard {meth} has no 26.x mapping in this tool")
    if not st["shader"] or re.fullmatch(OWNER + r"[A-Z_]+_SHADER", st["shader"]):
        raise ValueError(f"shader {st['shader']!r} is vanilla or missing; map it to a RenderPipelines snippet by hand")
    outline = calls[-1][1]
    f = re.sub(r"DefaultVertexFormat\.(\w+)", lambda m: "DefaultVertexFormat." + FORMATS.get(m.group(1), m.group(1)), fmt)
    topo = re.sub(r"^(?:com\.mojang\.blaze3d\.vertex\.)?VertexFormat\.Mode\.", "PrimitiveTopology.", mode)
    if not topo.startswith("PrimitiveTopology."):
        raise ValueError(f"mode {mode!r} is not a VertexFormat.Mode constant")
    i1, i2 = ind + "    ", ind + "        "
    pipe = [f".withVertexBinding(0, {f})", f".withPrimitiveTopology({topo})"]
    mask, dwrite = st["write"]
    if st["blend"] or mask != "WRITE_ALL":
        blend = f"Optional.of(BlendFunction.{st['blend']})" if st["blend"] else "Optional.empty()"
        pipe.append(f".withColorTargetState(new ColorTargetState(BlendFunction.{st['blend']}))" if mask == "WRITE_ALL"
                    else f".withColorTargetState(new ColorTargetState({blend}, GpuFormat.RGBA8_UNORM, ColorTargetState.{mask}))")
    bias = ", -1.0F, -10.0F" if st["layer"] == "bias" else ""                  # 1.21's polygonOffset(-1, -10)
    pipe.append(f".withDepthStencilState(new DepthStencilState(CompareOp.{st['depth']}, {'true' if dwrite else 'false'}{bias}))")
    if not st["cull"]:
        pipe.append(".withCull(false)")
    head = f"RenderSetup.builder({st['shader']}.pipeline({name_expr}, pb -> pb" + "".join("\n" + i2 + x for x in pipe) + "))"
    setup = []
    if st["tex"]:
        setup.append(f".withTexture(\"Sampler0\", {st['tex']})")
    if st["lightmap"]:
        setup.append(".useLightmap()")
    if st["overlay"]:
        setup.append(".useOverlay()")
    if crumb == "true":
        setup.append(".affectsCrumbling()")
    if sort == "true":
        setup.append(".sortOnUpload()")
    if st["layer"] and st["layer"] != "bias":
        setup.append(f".setLayeringTransform(LayeringTransform.{st['layer']})")
    if st["output"]:
        setup.append(f".setOutputTarget(OutputTarget.{st['output']})")
    if outline == "true":
        setup.append(".setOutline(RenderSetup.OutlineProperty.AFFECTS_OUTLINE)")
    elif outline != "false":
        raise ValueError(f"createCompositeState({outline}) is not a literal")
    return head + "".join("\n" + i1 + x for x in setup + [".createRenderSetup()"]), notes


def ref_and_import(fqn, member=False):
    """`p.q.Outer.Inner` -> ("Outer.Inner", "p.q.Outer"); a trailing method name stays on the reference."""
    parts = fqn.split(".")
    k = next((i for i, s in enumerate(parts) if s[:1].isupper()), None)
    if k is None:
        return fqn, None
    return ".".join(parts[k:]), (".".join(parts[:k + 1]) if k else None)


SHARD_NEW = re.compile(r"new\s+(?:RenderStateShard\.)?ShaderStateShard\s*\(")


def convert_shards(text, factory):
    """`new ShaderStateShard(sup) { setupRenderState() { super...; BODY } }` -> `factory(sup, () -> { BODY })`."""
    out, n = text, 0
    while True:
        masked = ni.code_spans(out)
        m = SHARD_NEW.search(masked)
        if not m:
            return out, n
        ac = close_of(masked, m.end() - 1)
        sup = out[m.end():ac].strip()
        rest = re.match(r"\s*\{", masked[ac + 1:])
        end, setup = ac + 1, "null"
        if rest:
            bo = ac + 1 + rest.end() - 1
            bc = close_of(masked, bo)
            body = out[bo + 1:bc]
            bm = ni.code_spans(body)
            mm = re.search(r"(?:@Override\s*)?(?:public\s+)?void\s+setupRenderState\s*\(\s*\)\s*\{", bm)
            mc = close_of(bm, mm.end() - 1) if mm else -1
            if not mm or (bm[:mm.start()] + bm[mc + 1:]).strip():
                raise ValueError("a ShaderStateShard subclass overrides more than setupRenderState()")
            inner = body[mm.end():mc]
            inner = re.sub(r"^\s*super\.setupRenderState\(\)\s*;", "", inner, count=1).strip()
            ref = re.fullmatch(r"([A-Z][\w.]*)\.(\w+)\(\)\s*;", inner)
            setup = (f"{ref.group(1)}::{ref.group(2)}" if ref else
                     f"() -> {inner.rstrip(';')}" if inner.count(";") == 1 else "() -> {\n" + inner + "\n}")
            end = bc + 1
        out = out[:m.start()] + f"{factory}({sup}, {setup})" + out[end:]
        n += 1


def convert_file(text, state_type, factory):
    """Return (new text, converted count, refusals, notes)."""
    st_simple, st_import = ref_and_import(state_type)
    fac_call, fac_import = ref_and_import(factory, member=True)
    refusals, notes, n = [], [], 0
    out = text
    pos = 0
    while True:
        masked = ni.code_spans(out)
        m = re.compile(r"(?<![\w.])RenderType\.create\s*\(").search(masked, pos)
        if not m:
            break
        close = close_of(masked, m.end() - 1)
        args = split_top(out[m.end():close], masked[m.end():close])
        line = out.count("\n", 0, m.start()) + 1
        if len(args) != 7:
            pos = close; continue                                            # already 26.x-shaped, or another overload
        inner = out[m.end():close]
        im = re.search(r"\n([ \t]*)\S", inner)
        ind = im.group(1) if im else re.match(r"[ \t]*", out[out.rfind("\n", 0, m.start()) + 1:]).group(0) + "    "
        try:
            setup, nn = convert_call(args[1:], args[0], ind)
        except (ValueError, KeyError) as e:
            refusals.append(f"line {line}: {e}"); pos = close; continue
        notes += [f"line {line}: {x}" for x in nn]
        first = masked.index(",", m.end())                                   # args[0] has no top-level comma
        tail = re.search(r"\s*$", inner).group(0)
        sep = "\n" + ind if "\n" in inner[:len(inner) - len(tail)] else " "
        new = out[m.start():first + 1] + sep + setup + tail + ")"
        out = out[:m.start()] + new + out[close + 1:]
        pos = m.start() + len(new); n += 1
    converted = n or ".pipeline(" in out and "RenderSetup.builder(" in out     # this run's, or an earlier run's
    if converted or SHARD_NEW.search(ni.code_spans(out)):
        try:
            out, k = convert_shards(out, fac_call)
        except ValueError as e:
            refusals.append(str(e)); k = 0
        out = re.sub(r"(?<![\w.])(?:RenderStateShard\.)?ShaderStateShard(?=\s+\w)", st_simple, out)
        out = re.sub(r"(?<![\w.])(?:RenderStateShard\.)?(?:Empty)?TextureStateShard(?=\s+\w)", "Identifier", out)
        out = re.sub(r"(?<![\w.])(?:RenderType|RenderStateShard)\.(?:%s)\b(?!\s*\()" % "|".join(SHEETS),
                     "TextureAtlas.LOCATION_BLOCKS", out)
        out = re.sub(r"new\s+(?:RenderStateShard\.)?TextureStateShard\s*\(([^,()]+),\s*(?:true|false)\s*,\s*(?:true|false)\s*\)",
                     r"\1", out)
        if converted:
            used = {k for k in IMPORTS if re.search(r"(?<![\w.])%s\b" % k, ni.code_spans(out))}
            for k in sorted(used):
                if not re.search(r"(?m)^import\s+[\w.]*\.%s\s*;" % k, out):
                    out = ni.add_import(out, IMPORTS[k])
            own_pkg = re.search(r"(?m)^package\s+([\w.]+)\s*;", out)
            for fq in {st_import, fac_import}:
                if fq and not re.search(r"(?m)^import\s+%s\s*;" % re.escape(fq), out) \
                        and not (own_pkg and fq.rsplit(".", 1)[0] == own_pkg.group(1)):
                    out = ni.add_import(out, fq)
            if "Identifier" in out and not re.search(r"(?m)^import\s+[\w.]*\.Identifier\s*;", out):
                out = ni.add_import(out, "net.minecraft.resources.Identifier")
        body = ni.code_spans(re.sub(r"(?m)^import[^\n]*\n", "", out))
        for fq in ("net.minecraft.client.renderer.RenderStateShard", "com.mojang.blaze3d.vertex.VertexFormat"):
            if not re.search(r"(?<![\w.])%s\b" % fq.rsplit(".", 1)[1], body):  # only what this tool emptied
                out = re.sub(r"(?m)^import\s+%s\s*;[ \t]*\r?\n" % re.escape(fq), "", out)
        if "TextureAtlas.LOCATION_BLOCKS" in out and not re.search(r"(?m)^import\s+[\w.]*\.TextureAtlas\s*;", out):
            out = ni.add_import(out, IMPORTS["TextureAtlas"])
    return out, n, refusals, notes


def run(src, state_type, factory, dry=False):
    total, report = 0, []
    for f in sorted(pathlib.Path(src).rglob("*.java")):
        t = f.read_text(encoding="utf-8")
        if "CompositeState" not in t and "ShaderStateShard" not in t and "RenderSetup.builder(" not in t:
            continue
        out, n, refusals, notes = convert_file(t, state_type, factory)
        total += n
        if refusals or notes or n:
            report.append((f, n, refusals, notes))
        if out != t and not dry:
            f.write_text(out, encoding="utf-8")
    return total, report


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src"); ap.add_argument("--state-type"); ap.add_argument("--state-factory")
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if not (a.src and a.state_type and a.state_factory):
        ap.error("--src, --state-type and --state-factory are required")
    total, report = run(a.src, a.state_type, a.state_factory, a.dry_run)
    refused = sum(len(r) for _, _, r, _ in report)
    print(f"convert-rendertypes: {total} RenderType(s) converted, {refused} refused" + (" (dry run)" if a.dry_run else ""))
    for f, n, refusals, notes in report:
        for r in refusals:
            print(f"  REFUSED {f.name} {r}")
        for x in notes:
            print(f"  note    {f.name} {x}")
    return 0


def self_check():
    t = '''package m;

import com.mojang.blaze3d.vertex.DefaultVertexFormat;
import com.mojang.blaze3d.vertex.VertexFormat;
import net.minecraft.client.renderer.RenderStateShard;
import net.minecraft.client.renderer.rendertype.RenderType;
import net.minecraft.resources.Identifier;

class R {
    private static final RenderStateShard.ShaderStateShard SHADER_STATE = new RenderStateShard.ShaderStateShard(S::getShader) {
        @Override
        public void setupRenderState() {
            super.setupRenderState();
            S.applyUniforms();
        }
    };
    public static final RenderType A = RenderType.create("a",
        DefaultVertexFormat.NEW_ENTITY, VertexFormat.Mode.QUADS, 256, true, false,
        RenderType.CompositeState.builder()
            .setShaderState(SHADER_STATE)
            .setTextureState(new RenderStateShard.TextureStateShard(TEX, false, false))
            .setTransparencyState(RenderType.TRANSLUCENT_TRANSPARENCY)
            .setDepthTestState(RenderType.NO_DEPTH_TEST)
            .setCullState(RenderType.NO_CULL)
            .setLightmapState(RenderType.LIGHTMAP)
            .setWriteMaskState(RenderType.COLOR_WRITE)
            .createCompositeState(true));
    static RenderType b(String n, RenderStateShard.ShaderStateShard shaderState, RenderStateShard.TextureStateShard textureState) {
        return RenderType.create(n, DefaultVertexFormat.BLOCK, VertexFormat.Mode.QUADS, 256, false, true,
            RenderType.CompositeState.builder().setShaderState(shaderState).setTextureState(textureState)
                .setLayeringState(RenderType.POLYGON_OFFSET_LAYERING).createCompositeState(false));
    }
    static final RenderType BAD = RenderType.create("x", DefaultVertexFormat.BLOCK, VertexFormat.Mode.QUADS, 256, false, false,
        RenderType.CompositeState.builder().setShaderState(RenderType.RENDERTYPE_SOLID_SHADER).createCompositeState(false));
}
'''
    out, n, refusals, notes = convert_file(t, "p.q.Prog.State", "p.q.Prog.state")
    want = ["private static final Prog.State SHADER_STATE = Prog.state(S::getShader, S::applyUniforms)",
            'RenderSetup.builder(SHADER_STATE.pipeline("a", pb -> pb.withVertexBinding(0, DefaultVertexFormat.ENTITY)'
            '.withPrimitiveTopology(PrimitiveTopology.QUADS).withColorTargetState(new ColorTargetState(BlendFunction.TRANSLUCENT))'
            '.withDepthStencilState(new DepthStencilState(CompareOp.ALWAYS_PASS, false)).withCull(false)))'
            '.withTexture("Sampler0", TEX).useLightmap().affectsCrumbling()'
            '.setOutline(RenderSetup.OutlineProperty.AFFECTS_OUTLINE).createRenderSetup()',
            "Prog.State shaderState, Identifier textureState", "shaderState.pipeline(n, pb -> pb",
            "new DepthStencilState(CompareOp.GREATER_THAN_OR_EQUAL, true, -1.0F, -10.0F)",
            ".withTexture(\"Sampler0\", textureState).sortOnUpload().createRenderSetup()",
            "import com.mojang.blaze3d.PrimitiveTopology;", "import p.q.Prog;"]
    flat = re.sub(r"\s+", "", out)
    miss = [w for w in want if re.sub(r"\s+", "", w) not in flat]
    if n != 2 or len(refusals) != 1 or "RENDERTYPE_SOLID_SHADER" not in refusals[0]:
        miss.append(f"n={n} refusals={refusals}")
    if "import com.mojang.blaze3d.vertex.VertexFormat;" not in out:              # BAD still needs it
        miss.append("VertexFormat import removed while still used")
    if "import net.minecraft.client.renderer.RenderStateShard;" in out:
        miss.append("unused RenderStateShard import left")
    again = convert_file(out, "p.q.Prog.State", "p.q.Prog.state")
    if again[1] != 0 or again[0] != out:
        miss.append("not idempotent")
    print("self-check:", "OK" if not miss else f"FAIL {miss}\n{out}")
    return 0 if not miss else 1


if __name__ == "__main__":
    sys.exit(main())
