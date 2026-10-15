// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
// MV-REQUIRES: LegacyGl
package @MVPKG@;

import com.mojang.renderpearl.api.buffers.GpuBuffer;
import com.mojang.renderpearl.api.commands.RenderPass;
import com.mojang.blaze3d.systems.RenderSystem;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.List;
import org.joml.Matrix4f;
import org.joml.Vector3f;
import org.joml.Vector4f;

/**
 * A 1.21.1 shader uniform as the mod's code sees it ({@code shader.getUniform("X").set(...)}), on 1.21.8 where a program
 * no longer holds values and a pipeline can declare nothing but uniform BUFFERS: this keeps the value, and
 * {@link LegacyGl} uploads it as a one-member std140 block named {@link #block} on every draw. {@link ShaderInstance}
 * rewrites the shader's {@code uniform vec4 X;} into the matching {@code uniform MV_X { vec4 X; };}.
 */
public class Uniform {
    /** The 1.21.1 uniform shapes; 1.21.8's UniformType only knows uniform/texel buffers. */
    public enum Kind {
        FLOAT(1, "float"), VEC2(2, "vec2"), VEC3(3, "vec3"), VEC4(4, "vec4"),
        INT(1, "int"), IVEC2(2, "ivec2"), IVEC3(3, "ivec3"), IVEC4(4, "ivec4"), MAT4(16, "mat4");
        final int count;
        final String glsl;
        Kind(int count, String glsl) { this.count = count; this.glsl = glsl; }
        int count() { return count; }
        boolean isInt() { return this == INT || this == IVEC2 || this == IVEC3 || this == IVEC4; }

        /** The kind a GLSL type name declares, or null for one a std140 block of this helper cannot carry. */
        static Kind ofGlsl(String type) {
            for (Kind k : values()) if (k.glsl.equals(type)) return k;
            return null;
        }
    }

    /** The name of the one-member uniform block that carries uniform {@code name} (a block name of its own, so it
     *  never collides with the member). Pipeline declaration, GLSL rewrite and upload all use this. */
    static String block(String name) { return "MV_" + name; }

    static final Uniform DUMMY = new Uniform("dummy", Kind.FLOAT, null);

    private final String name;
    private final Kind type;
    private float[] floats;
    private int[] ints;
    private boolean touched;

    Uniform(String name, Kind type, float[] defaults) {
        this.name = name;
        this.type = type;
        if (type.isInt()) {
            ints = new int[type.count()];
            if (defaults != null) {
                for (int i = 0; i < Math.min(ints.length, defaults.length); i++) ints[i] = (int) defaults[i];
            }
        } else {
            floats = new float[type.count()];
            if (defaults != null) System.arraycopy(defaults, 0, floats, 0, Math.min(floats.length, defaults.length));
        }
    }

    public String getName() { return name; }

    String blockName() { return block(name); }

    /** True once the mod has written a value (a built-in uniform is then the mod's override, see LegacyGl.draw). */
    boolean touched() { return touched; }

    float[] floats() { return floats; }

    void consume() { touched = false; }
    public Kind getType() { return type; }

    public void set(float x) { put(x); }
    public void set(float x, float y) { put(x, y); }
    public void set(float x, float y, float z) { put(x, y, z); }
    public void set(float x, float y, float z, float w) { put(x, y, z, w); }
    public void set(float[] v) { put(v); }
    public void set(Vector3f v) { put(v.x(), v.y(), v.z()); }
    public void set(Vector4f v) { put(v.x(), v.y(), v.z(), v.w()); }
    public void set(Matrix4f m) { put(m.get(new float[16])); }
    public void setSafe(float x, float y, float z, float w) { put(x, y, z, w); }
    public void setMat4x4(float... v) { put(v); }

    public void set(int x) { putInts(x); }
    public void set(int x, int y) { putInts(x, y); }
    public void set(int x, int y, int z) { putInts(x, y, z); }
    public void set(int x, int y, int z, int w) { putInts(x, y, z, w); }

    /** 1.21.1 uploaded here; values now travel with the draw, so there is nothing to do. */
    public void upload() {}

    private void put(float... v) {
        touched = true;
        if (ints != null) {
            for (int i = 0; i < Math.min(ints.length, v.length); i++) ints[i] = (int) v[i];
        } else if (floats != null) {
            System.arraycopy(v, 0, floats, 0, Math.min(floats.length, v.length));
        }
    }

    private void putInts(int... v) {
        touched = true;
        if (floats != null) {
            for (int i = 0; i < Math.min(floats.length, v.length); i++) floats[i] = v[i];
        } else if (ints != null) {
            System.arraycopy(v, 0, ints, 0, Math.min(ints.length, v.length));
        }
    }

    void applyTo(RenderPass pass, List<GpuBuffer> temps) {
        int n = floats != null ? floats.length : ints != null ? ints.length : 0;
        if (n == 0) return;
        // std140: a lone scalar/vecN/mat4 member starts the block at offset 0 and is read tightly; the buffer is
        // padded to a 16-byte multiple
        int size = Math.max(16, (n * 4 + 15) / 16 * 16);
        ByteBuffer bb = ByteBuffer.allocateDirect(size).order(ByteOrder.nativeOrder());
        for (int i = 0; i < n; i++) {
            if (floats != null) bb.putFloat(i * 4, floats[i]);
            else bb.putInt(i * 4, ints[i]);
        }
        bb.clear();
        GpuBuffer buffer = RenderSystem.getDevice().createBuffer(() -> "mv uniform " + name, GpuBuffer.USAGE_UNIFORM, bb);
        temps.add(buffer);
        pass.setUniform(blockName(), buffer);
    }
}
