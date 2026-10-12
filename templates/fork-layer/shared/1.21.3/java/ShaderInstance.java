// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.mojang.blaze3d.platform.GlStateManager;
import com.mojang.blaze3d.preprocessor.GlslPreprocessor;
import com.mojang.blaze3d.shaders.CompiledShader;
import com.mojang.blaze3d.systems.RenderSystem;
import com.mojang.blaze3d.vertex.VertexFormat;
import com.mojang.serialization.JsonOps;
import net.minecraft.FileUtil;
import net.minecraft.client.renderer.CompiledShaderProgram;
import net.minecraft.client.renderer.ShaderManager;
import net.minecraft.client.renderer.ShaderProgramConfig;
import net.minecraft.client.renderer.texture.AbstractTexture;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.packs.resources.ResourceProvider;
import org.apache.commons.io.IOUtils;
import org.lwjgl.opengl.GL14;

import java.io.IOException;
import java.io.Reader;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;

/**
 * 1.21.1's ShaderInstance, rebuilt on 1.21.2's CompiledShaderProgram so the mod's shader code keeps its shape:
 * a program is made from {@code <ns>:shaders/core/<name>.json} through whatever ResourceProvider the caller passes
 * (the mod's preset provider included), in the 1.21.1 JSON format, with its "blend" applied on {@link #apply()}.
 * The vanilla ShaderManager in 1.21.2 cannot do this: it only compiles programs it found itself and has no
 * blend state. The constructor CompiledShaderProgram(int) is widened by this target's access transformer.
 */
public class ShaderInstance extends CompiledShaderProgram {
    private final String name;
    private final Blend blend;

    public ShaderInstance(ResourceProvider provider, ResourceLocation location, VertexFormat format) throws IOException {
        this(provider, location, format, readConfig(provider, location));
    }

    private ShaderInstance(ResourceProvider provider, ResourceLocation location, VertexFormat format, JsonObject json)
            throws IOException {
        super(link(provider, location, format, json));
        this.name = location.toString();
        ShaderProgramConfig config = ShaderProgramConfig.CODEC.parse(JsonOps.INSTANCE, json)
                .getOrThrow(msg -> new IOException("Invalid shader config " + location + ": " + msg));
        setupUniforms(config.uniforms(), config.samplers());
        this.blend = json.has("blend") ? Blend.parse(json.getAsJsonObject("blend")) : null;
    }

    public String getName() {
        return name;
    }

    public int getId() {
        return getProgramId();
    }

    /** 1.21.1 accepted a texture object or an id for any sampler. */
    public void setSampler(String sampler, Object texture) {
        if (texture instanceof AbstractTexture t) {
            bindSampler(sampler, t.getId());
        } else if (texture instanceof Integer id) {
            bindSampler(sampler, id);
        }
    }

    @Override
    public void apply() {
        if (blend != null) {
            blend.apply();
        }
        super.apply();
    }

    private static JsonObject readConfig(ResourceProvider provider, ResourceLocation location) throws IOException {
        ResourceLocation file = location.withPath(p -> "shaders/core/" + p + ".json");
        try (Reader reader = provider.openAsReader(file)) {
            return JsonParser.parseReader(reader).getAsJsonObject();
        }
    }

    private static int link(ResourceProvider provider, ResourceLocation location, VertexFormat format, JsonObject json)
            throws IOException {
        RenderSystem.assertOnRenderThread();
        CompiledShader vertex = compile(provider, ResourceLocation.parse(json.get("vertex").getAsString()), CompiledShader.Type.VERTEX);
        CompiledShader fragment = compile(provider, ResourceLocation.parse(json.get("fragment").getAsString()), CompiledShader.Type.FRAGMENT);
        try {
            int program = GlStateManager.glCreateProgram();
            if (program <= 0) {
                throw new IOException("Could not create shader program for " + location);
            }
            format.bindAttributes(program);
            GlStateManager.glAttachShader(program, vertex.getShaderId());
            GlStateManager.glAttachShader(program, fragment.getShaderId());
            GlStateManager.glLinkProgram(program);
            if (GlStateManager.glGetProgrami(program, 35714) == 0) {
                String log = GlStateManager.glGetProgramInfoLog(program, 32768);
                GlStateManager.glDeleteProgram(program);
                throw new IOException("Could not link shader program " + location + ": " + log);
            }
            return program;
        } finally {
            vertex.close();
            fragment.close();
        }
    }

    private static CompiledShader compile(ResourceProvider provider, ResourceLocation id, CompiledShader.Type type) throws IOException {
        ResourceLocation file = id.withPath(p -> "shaders/core/" + p + (type == CompiledShader.Type.VERTEX ? ".vsh" : ".fsh"));
        String source;
        try (Reader reader = provider.openAsReader(file)) {
            source = IOUtils.toString(reader);
        }
        ResourceLocation dir = file.withPath(FileUtil::getFullResourcePath);
        Set<ResourceLocation> imported = new HashSet<>();
        GlslPreprocessor preprocessor = new GlslPreprocessor() {
            @Override
            public String applyImport(boolean relative, String path) {
                ResourceLocation target = relative
                        ? dir.withPath(p -> FileUtil.normalizeResourcePath(p + path))
                        : ResourceLocation.parse(path).withPrefix("shaders/include/");
                if (!imported.add(target)) {
                    return null;
                }
                try (Reader reader = provider.openAsReader(target)) {
                    return IOUtils.toString(reader);
                } catch (IOException e) {
                    return "#error " + e.getMessage();
                }
            }
        };
        try {
            return CompiledShader.compile(id, type, String.join("", preprocessor.process(source)));
        } catch (ShaderManager.CompilationException e) {
            throw new IOException("Could not compile " + file + ": " + e.getMessage(), e);
        }
    }

    /** The "blend" object of a 1.21.1 shader JSON, as 1.21.1's BlendMode applied it. */
    private record Blend(boolean opaque, boolean separate, int srcRgb, int dstRgb, int srcAlpha, int dstAlpha, int func) {
        static Blend parse(JsonObject o) {
            String func = string(o, "func", "add");
            int src = factor(string(o, "srcrgb", "1"));
            int dst = factor(string(o, "dstrgb", "0"));
            boolean separate = o.has("srcalpha") || o.has("dstalpha");
            int srcA = separate ? factor(string(o, "srcalpha", "1")) : src;
            int dstA = separate ? factor(string(o, "dstalpha", "0")) : dst;
            boolean opaque = src == 1 && dst == 0 && srcA == 1 && dstA == 0 && func.equals("add");
            return new Blend(opaque, separate, src, dst, srcA, dstA, equation(func));
        }

        void apply() {
            if (opaque) {
                RenderSystem.disableBlend();
                return;
            }
            RenderSystem.enableBlend();
            RenderSystem.blendEquation(func);
            if (separate) {
                RenderSystem.blendFuncSeparate(srcRgb, dstRgb, srcAlpha, dstAlpha);
            } else {
                RenderSystem.blendFunc(srcRgb, dstRgb);
            }
        }

        private static String string(JsonObject o, String key, String fallback) {
            JsonElement e = o.get(key);
            return e == null ? fallback : e.getAsString();
        }

        private static int equation(String s) {
            return switch (s.trim().toLowerCase(Locale.ROOT)) {
                case "subtract" -> 32778;
                case "reversesubtract", "reverse_subtract" -> 32779;
                case "min" -> 32775;
                case "max" -> 32776;
                default -> 32774;
            };
        }

        private static int factor(String s) {
            String f = s.trim().toLowerCase(Locale.ROOT).replace("_", "").replace("one", "1").replace("zero", "0")
                    .replace("minus", "-");
            return switch (f) {
                case "0" -> 0;
                case "1" -> 1;
                case "srccolor" -> 768;
                case "1-srccolor" -> 769;
                case "dstcolor" -> 774;
                case "1-dstcolor" -> 775;
                case "srcalpha" -> 770;
                case "1-srcalpha" -> 771;
                case "dstalpha" -> 772;
                case "1-dstalpha" -> 773;
                default -> GL14.GL_ONE;
            };
        }
    }
}
