// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.mojang.blaze3d.pipeline.BlendFunction;
import com.mojang.blaze3d.pipeline.RenderPipeline;
import com.mojang.blaze3d.platform.DestFactor;
import com.mojang.blaze3d.platform.SourceFactor;
import com.mojang.blaze3d.preprocessor.GlslPreprocessor;
import com.mojang.blaze3d.shaders.ShaderType;
import com.mojang.blaze3d.shaders.UniformType;
import com.mojang.blaze3d.buffers.GpuBuffer;
import com.mojang.blaze3d.systems.RenderPass;
import com.mojang.blaze3d.textures.GpuTexture;
import com.mojang.blaze3d.textures.GpuTextureView;
import com.mojang.blaze3d.vertex.VertexFormat;
import com.mojang.logging.LogUtils;
import net.minecraft.FileUtil;
import net.minecraft.client.renderer.texture.AbstractTexture;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.packs.resources.ResourceProvider;
import org.apache.commons.io.IOUtils;
import org.slf4j.Logger;

import java.io.IOException;
import java.io.Reader;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 1.21.1's ShaderInstance on 1.21.8, where a program is no longer bound and fed by hand: drawing goes through a
 * RenderPipeline (fixed blend/depth/cull/mask state + shader ids + declared uniform BUFFERS and samplers) and a
 * RenderPass. This keeps the 1.21.1 surface the mod's code was written against -- built from
 * {@code shaders/core/<name>.json} through any ResourceProvider (a mod's generated-shader provider included),
 * {@code getUniform(name).set(...)}, {@code setSampler} -- and derives a pipeline per draw state ({@link #pipeline}),
 * compiled from THIS instance's sources through {@code GpuDevice.precompilePipeline} (see {@link LegacyGl#draw}).
 *
 * <p>1.21.8 has no plain GLSL uniforms (1.21.5 still had them): a pipeline declares uniform blocks, and the vanilla
 * ones are {@code DynamicTransforms} (ModelViewMat, ColorModulator, TextureMat, ModelOffset, LineWidth),
 * {@code Projection}, {@code Fog}, {@code Globals} (ScreenSize, GlintAlpha, GameTime) and {@code Lighting}. So the
 * shader SOURCE is rewritten when it is read ({@link #legacyUniforms}): a 1.21.1 built-in uniform
 * ({@code uniform mat4 ModelViewMat;}) becomes the vanilla block that now carries it, filled from RenderSystem exactly
 * as vanilla's own draws fill it; every other uniform becomes a one-member block of its own ({@link Uniform#block}),
 * filled from the value the mod set. As on 1.21.5, only COLOR_MODULATOR of the built-ins honours a value the mod wrote.
 *
 * <p>Ids are made unique per instance: the device caches compiled shaders by id, so a mod that rebuilds a shader under
 * the same name (a live shader editor) would otherwise keep drawing the first version it compiled.
 */
public class ShaderInstance implements AutoCloseable {
    private static final Logger LOGGER = LogUtils.getLogger();
    private static final AtomicInteger SERIAL = new AtomicInteger();

    // 1.21.1's public built-in uniform fields: null when the shader's JSON does not declare them. Only COLOR_MODULATOR
    // is honoured when the mod overwrites it (LegacyGl.draw); the others are read from RenderSystem, as vanilla does.
    public Uniform MODEL_VIEW_MATRIX, PROJECTION_MATRIX, TEXTURE_MATRIX, SCREEN_SIZE, COLOR_MODULATOR, LIGHT0_DIRECTION,
            LIGHT1_DIRECTION, GLINT_ALPHA, FOG_START, FOG_END, FOG_COLOR, FOG_SHAPE, LINE_WIDTH, GAME_TIME;

    private final String name;
    private final VertexFormat format;
    private final ResourceLocation vertexId;
    private final ResourceLocation fragmentId;
    private final String vertexSource;
    private final String fragmentSource;
    private final List<String> samplers = new ArrayList<>();
    private final Map<String, Uniform> uniforms = new LinkedHashMap<>();
    /** Uniforms the GLSL declares and the JSON does not: carried (zero) so the block is not left unbound, but not
     *  exposed -- 1.21.1's getUniform answered null for them. */
    private final Map<String, Uniform> hidden = new LinkedHashMap<>();
    private final Map<String, Uniform.Kind> declared = new LinkedHashMap<>();
    private final Map<String, GpuTextureView> boundSamplers = new HashMap<>();
    private final BlendFunction blend;
    private final Map<List<Object>, RenderPipeline> pipelines = new HashMap<>();
    private final int serial = SERIAL.incrementAndGet();
    private int lastProgramId = -1;

    public ShaderInstance(ResourceProvider provider, ResourceLocation location, VertexFormat format) throws IOException {
        this.name = location.toString();
        this.format = format;
        JsonObject json = readConfig(provider, location);
        ResourceLocation vertex = ResourceLocation.parse(json.get("vertex").getAsString());
        ResourceLocation fragment = ResourceLocation.parse(json.get("fragment").getAsString());
        this.vertexSource = source(provider, vertex, ShaderType.VERTEX);
        this.fragmentSource = source(provider, fragment, ShaderType.FRAGMENT);
        this.vertexId = vertex.withSuffix("/mv_" + serial);
        this.fragmentId = fragment.withSuffix("/mv_" + serial);
        if (json.has("samplers")) {
            for (JsonElement e : json.getAsJsonArray("samplers")) {
                samplers.add(e.getAsJsonObject().get("name").getAsString());
            }
        }
        if (json.has("uniforms")) {
            for (JsonElement e : json.getAsJsonArray("uniforms")) {
                JsonObject u = e.getAsJsonObject();
                String n = u.get("name").getAsString();
                Uniform.Kind type = type(u.get("type").getAsString(), u.has("count") ? u.get("count").getAsInt() : 1);
                if (type == null) {
                    LOGGER.warn("[mv] {}: uniform {} has a type the 1.21.8 block carrier cannot declare ({}); it is left out", name, n, u);
                    continue;
                }
                float[] defaults = null;
                if (u.has("values")) {
                    JsonArray v = u.getAsJsonArray("values");
                    defaults = new float[v.size()];
                    for (int i = 0; i < defaults.length; i++) defaults[i] = v.get(i).getAsFloat();
                }
                uniforms.put(n, new Uniform(n, type, defaults));
            }
        }
        MODEL_VIEW_MATRIX = uniforms.get("ModelViewMat");
        PROJECTION_MATRIX = uniforms.get("ProjMat");
        TEXTURE_MATRIX = uniforms.get("TextureMat");
        SCREEN_SIZE = uniforms.get("ScreenSize");
        COLOR_MODULATOR = uniforms.get("ColorModulator");
        LIGHT0_DIRECTION = uniforms.get("Light0_Direction");
        LIGHT1_DIRECTION = uniforms.get("Light1_Direction");
        GLINT_ALPHA = uniforms.get("GlintAlpha");
        FOG_START = uniforms.get("FogStart");
        FOG_END = uniforms.get("FogEnd");
        FOG_COLOR = uniforms.get("FogColor");
        FOG_SHAPE = uniforms.get("FogShape");
        LINE_WIDTH = uniforms.get("LineWidth");
        GAME_TIME = uniforms.get("GameTime");
        for (Map.Entry<String, Uniform.Kind> e : declared.entrySet()) {
            if (!uniforms.containsKey(e.getKey()) && !isBuiltIn(e.getKey())) {
                hidden.put(e.getKey(), new Uniform(e.getKey(), e.getValue(), null));
            }
        }
        this.blend = json.has("blend") ? blend(json.getAsJsonObject("blend")) : null;
    }

    public String getName() { return name; }

    /** The GL program of the pipeline this shader last drew with (-1 before the first draw). */
    public int getId() { return lastProgramId; }

    public VertexFormat getVertexFormat() { return format; }

    /** 1.21.1 returned null for a uniform the program does not declare; so does this. */
    public Uniform getUniform(String name) { return uniforms.get(name); }

    public Uniform safeGetUniform(String name) { return uniforms.getOrDefault(name, Uniform.DUMMY); }

    /** 1.21.1 accepted a texture object for any sampler; 1.21.8 binds a GpuTextureView. */
    public void setSampler(String sampler, Object texture) {
        if (texture instanceof AbstractTexture t) {
            boundSamplers.put(sampler, t.getTextureView());
        } else if (texture instanceof GpuTextureView v) {
            boundSamplers.put(sampler, v);
        } else if (texture instanceof GpuTexture g) {
            boundSamplers.put(sampler, com.mojang.blaze3d.systems.RenderSystem.getDevice().createTextureView(g));
        } else if (texture != null) {
            LOGGER.warn("[mv] {}: sampler {} given a {}, which 1.21.8 cannot bind", name, sampler, texture.getClass().getName());
        }
    }

    public void apply() {}

    public void clear() {}

    @Override
    public void close() {}

    /** The JSON's own "blend", or null; LegacyGl prefers state the caller set explicitly. */
    BlendFunction blend() { return blend; }

    List<String> samplers() { return samplers; }

    String sourceFor(ResourceLocation id, ShaderType type) {
        if (type == ShaderType.VERTEX && id.equals(vertexId)) return vertexSource;
        if (type == ShaderType.FRAGMENT && id.equals(fragmentId)) return fragmentSource;
        return null;
    }

    RenderPipeline pipeline(VertexFormat format, VertexFormat.Mode mode, LegacyGl.Key key) {
        return pipelines.computeIfAbsent(List.of(key, format, mode), l -> {
            LegacyGl.Key k = key;
            RenderPipeline.Builder b = RenderPipeline.builder()
                    .withLocation(vertexId.withSuffix("_p" + pipelines.size()))
                    .withVertexShader(vertexId)
                    .withFragmentShader(fragmentId)
                    .withVertexFormat(format, mode)
                    // vanilla's own draws declare it; Projection/Fog/Globals/Lighting are found in the program
                    // (GlProgram.BUILT_IN_UNIFORMS) and bound by RenderSystem.bindDefaultUniforms
                    .withUniform("DynamicTransforms", UniformType.UNIFORM_BUFFER);
            for (String s : samplers) b.withSampler(s);
            for (Uniform u : blockUniforms()) b.withUniform(u.blockName(), UniformType.UNIFORM_BUFFER);
            return k.apply(b).build();
        });
    }

    /** The uniforms that travel as blocks of their own: everything that is not a vanilla block's member. */
    private List<Uniform> blockUniforms() {
        List<Uniform> out = new ArrayList<>();
        for (Uniform u : uniforms.values()) if (!isBuiltIn(u.getName())) out.add(u);
        out.addAll(hidden.values());
        return out;
    }

    void bind(RenderPass pass, int programId, List<GpuBuffer> temps) {
        lastProgramId = programId;
        for (Uniform u : blockUniforms()) u.applyTo(pass, temps);
        for (String s : samplers) {
            GpuTextureView t = boundSamplers.get(s);
            if (t == null) t = LegacyGl.slotTexture(s);
            if (t != null) pass.bindSampler(s, t);
        }
    }

    // ---- 1.21.1 plain uniforms -> 1.21.8 uniform blocks ----------------------------------------------------------
    // The vanilla blocks as 1.21.8's include/*.glsl declare them (member ORDER is the std140 layout, so it must match
    // exactly). They are spelled out here rather than #moj_import-ed so a ResourceProvider that only knows the mod's
    // own shaders (a generated-shader provider) still compiles.
    private static final String DYNAMIC_BLOCK = "layout(std140) uniform DynamicTransforms {\n    mat4 ModelViewMat;\n"
            + "    vec4 ColorModulator;\n    vec3 ModelOffset;\n    mat4 TextureMat;\n    float LineWidth;\n};\n";
    private static final String PROJECTION_BLOCK = "layout(std140) uniform Projection {\n    mat4 ProjMat;\n};\n";
    private static final String GLOBALS_BLOCK = "layout(std140) uniform Globals {\n    vec2 ScreenSize;\n"
            + "    float GlintAlpha;\n    float GameTime;\n    int MenuBlurRadius;\n};\n";
    private static final String LIGHTING_BLOCK = "layout(std140) uniform Lighting {\n    vec3 Light0_Direction;\n"
            + "    vec3 Light1_Direction;\n};\n";
    private static final String FOG_BLOCK = "layout(std140) uniform Fog {\n    vec4 FogColor;\n"
            + "    float FogEnvironmentalStart;\n    float FogEnvironmentalEnd;\n    float FogRenderDistanceStart;\n"
            + "    float FogRenderDistanceEnd;\n    float FogSkyEnd;\n    float FogCloudsEnd;\n};\n";

    private static final Set<String> DYNAMIC = Set.of("ModelViewMat", "ColorModulator", "TextureMat", "LineWidth", "ModelOffset");
    private static final Set<String> GLOBALS = Set.of("ScreenSize", "GlintAlpha", "GameTime");
    private static final Set<String> LIGHTING = Set.of("Light0_Direction", "Light1_Direction");
    /** 1.21.1's fog uniforms that 1.21.8 spells differently: a {@code #define} keeps the shader's own code unchanged. */
    private static final Map<String, String> FOG_RENAMED = Map.of("FogStart", "FogEnvironmentalStart",
            "FogEnd", "FogEnvironmentalEnd", "FogShape", "0");

    /** A name a vanilla block carries (or 1.21.8 dropped): not a block of this helper's own. */
    static boolean isBuiltIn(String n) {
        return DYNAMIC.contains(n) || GLOBALS.contains(n) || LIGHTING.contains(n) || FOG_RENAMED.containsKey(n)
                || n.equals("ProjMat") || n.equals("FogColor");
    }

    private static final Pattern UNIFORM_DECL = Pattern.compile(
            "(?m)^[ \\t]*uniform[ \\t]+(?:(?:highp|mediump|lowp)[ \\t]+)?(\\w+)[ \\t]+(\\w+)[ \\t]*(\\[[^\\]]*\\])?[ \\t]*;[^\\n]*?(?=\\r?$)");

    /**
     * Rewrites a 1.21.1 shader's plain {@code uniform} declarations for 1.21.8 (see the class doc) and records the type of
     * every one it turned into a block of its own. Samplers stay as they are: a sampler is still a plain uniform.
     */
    private String legacyUniforms(String text, ResourceLocation id) {
        boolean dynamic = false, projection = false, globals = false, lighting = false, fog = false;
        Matcher m = UNIFORM_DECL.matcher(text);
        StringBuilder out = new StringBuilder();
        while (m.find()) {
            String type = m.group(1), n = m.group(2);
            String replacement;
            if (type.contains("sampler")) {
                continue;                       // appendReplacement is skipped: the line is copied unchanged
            }
            if (m.group(3) != null) {
                LOGGER.warn("[mv] {}: uniform array {}{} in {} stays a plain uniform; 1.21.8 cannot set it", name, n, m.group(3), id);
                continue;
            }
            if (DYNAMIC.contains(n)) { dynamic = true; replacement = ""; }
            else if (n.equals("ProjMat")) { projection = true; replacement = ""; }
            else if (GLOBALS.contains(n)) { globals = true; replacement = ""; }
            else if (LIGHTING.contains(n)) { lighting = true; replacement = ""; }
            else if (n.equals("FogColor")) { fog = true; replacement = ""; }
            else if (FOG_RENAMED.containsKey(n)) { fog = true; replacement = "#define " + n + " " + FOG_RENAMED.get(n); }
            else {
                Uniform.Kind kind = Uniform.Kind.ofGlsl(type);
                if (kind == null) {
                    LOGGER.warn("[mv] {}: uniform {} {} in {} has a type 1.21.8 cannot carry; left as it is", name, type, n, id);
                    continue;
                }
                declared.put(n, kind);
                replacement = "layout(std140) uniform " + Uniform.block(n) + " { " + type + " " + n + "; };";
            }
            m.appendReplacement(out, Matcher.quoteReplacement(replacement));
        }
        m.appendTail(out);
        String header = (dynamic ? DYNAMIC_BLOCK : "") + (projection ? PROJECTION_BLOCK : "") + (globals ? GLOBALS_BLOCK : "")
                + (lighting ? LIGHTING_BLOCK : "") + (fog ? FOG_BLOCK : "");
        if (header.isEmpty()) return out.toString();
        // the blocks go straight after #version, which GLSL wants first
        Matcher v = Pattern.compile("(?m)^[ \\t]*#version[^\\n]*\\n").matcher(out);
        return v.find() ? out.insert(v.end(), header).toString() : header + out;
    }

    private static Uniform.Kind type(String type, int count) {
        return switch (type.toLowerCase(Locale.ROOT)) {
            case "float" -> switch (count) {
                case 1 -> Uniform.Kind.FLOAT; case 2 -> Uniform.Kind.VEC2; case 3 -> Uniform.Kind.VEC3; case 4 -> Uniform.Kind.VEC4;
                default -> null;
            };
            case "int" -> switch (count) {
                case 1 -> Uniform.Kind.INT; case 2 -> Uniform.Kind.IVEC2; case 3 -> Uniform.Kind.IVEC3; case 4 -> Uniform.Kind.IVEC4;
                default -> null;
            };
            case "matrix4x4" -> Uniform.Kind.MAT4;
            default -> null;
        };
    }

    private static JsonObject readConfig(ResourceProvider provider, ResourceLocation location) throws IOException {
        ResourceLocation file = location.withPath(p -> "shaders/core/" + p + ".json");
        try (Reader reader = provider.openAsReader(file)) {
            return JsonParser.parseReader(reader).getAsJsonObject();
        }
    }

    private String source(ResourceProvider provider, ResourceLocation id, ShaderType type) throws IOException {
        ResourceLocation file = id.withPath(p -> "shaders/core/" + p + (type == ShaderType.VERTEX ? ".vsh" : ".fsh"));
        String text;
        try (Reader reader = provider.openAsReader(file)) {
            text = IOUtils.toString(reader);
        }
        text = legacyUniforms(text, id);
        ResourceLocation dir = file.withPath(FileUtil::getFullResourcePath);
        Set<ResourceLocation> imported = new HashSet<>();
        GlslPreprocessor preprocessor = new GlslPreprocessor() {
            @Override
            public String applyImport(boolean relative, String path) {
                ResourceLocation target = relative
                        ? dir.withPath(p -> FileUtil.normalizeResourcePath(p + path))
                        : ResourceLocation.parse(path).withPrefix("shaders/include/");
                if (!imported.add(target)) return null;
                try (Reader reader = provider.openAsReader(target)) {
                    return IOUtils.toString(reader);
                } catch (IOException e) {
                    return "#error " + e.getMessage();
                }
            }
        };
        return String.join("", preprocessor.process(text));
    }

    /** The "blend" object of a 1.21.1 shader JSON. 1.21.8 blends with ADD only; another "func" is logged and ignored. */
    private BlendFunction blend(JsonObject o) {
        String func = o.has("func") ? o.get("func").getAsString() : "add";
        if (!"add".equalsIgnoreCase(func)) {
            LOGGER.warn("[mv] {}: blend func {} has no 1.21.8 pipeline equivalent; ADD is used", name, func);
        }
        SourceFactor src = src(o, "srcrgb", SourceFactor.ONE);
        DestFactor dst = dst(o, "dstrgb", DestFactor.ZERO);
        boolean separate = o.has("srcalpha") || o.has("dstalpha");
        return separate ? new BlendFunction(src, dst, src(o, "srcalpha", SourceFactor.ONE), dst(o, "dstalpha", DestFactor.ZERO))
                : new BlendFunction(src, dst);
    }

    /** 1.21.1's BlendMode vocabulary: 0/zero, 1/one, [1-]srccolor, [1-]dstcolor, [1-]srcalpha, [1-]dstalpha. */
    private static String factor(JsonObject o, String key) {
        if (!o.has(key)) return null;
        String f = o.get(key).getAsString().trim().toLowerCase(Locale.ROOT).replace(" ", "").replace("_", "");
        boolean minus = f.startsWith("1-");
        String base = minus ? f.substring(2) : f;
        String e = switch (base) {
            case "0", "zero" -> "ZERO";
            case "1", "one" -> "ONE";
            case "srccolor" -> "SRC_COLOR";
            case "dstcolor" -> "DST_COLOR";
            case "srcalpha" -> "SRC_ALPHA";
            case "dstalpha" -> "DST_ALPHA";
            default -> null;
        };
        return e == null ? null : (minus ? "ONE_MINUS_" + e : e);
    }

    private static SourceFactor src(JsonObject o, String key, SourceFactor dflt) {
        String f = factor(o, key);
        try { return f == null ? dflt : SourceFactor.valueOf(f); } catch (IllegalArgumentException e) { return dflt; }
    }

    private static DestFactor dst(JsonObject o, String key, DestFactor dflt) {
        String f = factor(o, key);
        try { return f == null ? dflt : DestFactor.valueOf(f); } catch (IllegalArgumentException e) { return dflt; }
    }
}
