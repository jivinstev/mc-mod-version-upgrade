// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import com.mojang.renderpearl.api.pipeline.PrimitiveTopology;
import com.mojang.blaze3d.vertex.BufferBuilder;
import com.mojang.blaze3d.vertex.ByteBufferBuilder;
import com.mojang.renderpearl.api.vertex.VertexFormat;

/**
 * 1.21's {@code com.mojang.blaze3d.vertex.Tesselator}, which 26.2 removed: one shared, growable byte buffer that
 * {@link #begin} starts a {@code BufferBuilder} on, for immediate geometry the caller builds and draws at once
 * ({@code LegacyGl.drawWithShader(builder.buildOrThrow())}). As in 1.21 only one builder is live at a time: a second
 * {@code begin} before the first mesh is drawn and closed reuses the same memory.
 */
public final class Tesselator {
    private static final int DEFAULT_SIZE = 786432;   // 1.21's Tesselator buffer size
    private static Tesselator instance;

    private final ByteBufferBuilder buffer;

    public Tesselator(int size) {
        this.buffer = new ByteBufferBuilder(size);
    }

    public Tesselator() {
        this(DEFAULT_SIZE);
    }

    public static void init() {
        if (instance == null) instance = new Tesselator();
    }

    public static Tesselator getInstance() {
        init();
        return instance;
    }

    public BufferBuilder begin(PrimitiveTopology mode, VertexFormat format) {
        return new BufferBuilder(buffer, mode, format);
    }

    public void clear() {
        buffer.clear();
    }
}
