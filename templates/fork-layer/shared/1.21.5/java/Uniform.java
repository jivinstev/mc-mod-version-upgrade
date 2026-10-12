// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import com.mojang.blaze3d.shaders.UniformType;
import com.mojang.blaze3d.systems.RenderPass;
import org.joml.Matrix4f;
import org.joml.Vector3f;
import org.joml.Vector4f;

/**
 * A 1.21.1 shader uniform as the mod's code sees it ({@code shader.getUniform("X").set(...)}), on 1.21.5 where a program
 * no longer holds values: this keeps the value and {@link LegacyGl} hands it to each RenderPass by name.
 */
public class Uniform {
    static final Uniform DUMMY = new Uniform("dummy", UniformType.FLOAT, null);

    private final String name;
    private final UniformType type;
    private float[] floats;
    private int[] ints;

    Uniform(String name, UniformType type, float[] defaults) {
        this.name = name;
        this.type = type;
        if (type == UniformType.INT || type == UniformType.IVEC3) {
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
    public UniformType getType() { return type; }

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
        if (ints != null) {
            for (int i = 0; i < Math.min(ints.length, v.length); i++) ints[i] = (int) v[i];
        } else if (floats != null) {
            System.arraycopy(v, 0, floats, 0, Math.min(floats.length, v.length));
        }
    }

    private void putInts(int... v) {
        if (floats != null) {
            for (int i = 0; i < Math.min(floats.length, v.length); i++) floats[i] = v[i];
        } else if (ints != null) {
            System.arraycopy(v, 0, ints, 0, Math.min(ints.length, v.length));
        }
    }

    void applyTo(RenderPass pass) {
        if (floats != null) pass.setUniform(name, floats.clone());
        else if (ints != null) pass.setUniform(name, ints.clone());
    }
}
