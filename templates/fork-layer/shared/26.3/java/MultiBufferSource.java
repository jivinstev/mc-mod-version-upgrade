// OVERLAY-ONLY: shared fork-layer helper from mc-mod-version-upgrade templates/fork-layer/shared (not the authors' code).
package @MVPKG@;

import com.mojang.blaze3d.pipeline.RenderTarget;
import com.mojang.blaze3d.systems.RenderSystem;
import com.mojang.blaze3d.vertex.BufferBuilder;
import com.mojang.blaze3d.vertex.ByteBufferBuilder;
import com.mojang.blaze3d.vertex.MeshData;
import com.mojang.blaze3d.vertex.VertexConsumer;
import com.mojang.renderpearl.api.commands.RenderPass;
import com.mojang.renderpearl.api.textures.GpuTextureView;
import net.minecraft.client.Minecraft;
import net.minecraft.client.renderer.StagedVertexBuffer;
import net.minecraft.client.renderer.rendertype.RenderType;
import net.neoforged.api.distmarker.Dist;
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.fml.common.EventBusSubscriber;
import net.neoforged.neoforge.client.event.RenderFrameEvent;
import net.neoforged.neoforge.client.event.RenderLevelStageEvent;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.OptionalDouble;
import java.util.SequencedMap;

/**
 * 26.2 and 26.3 have no {@code MultiBufferSource} (geometry is recorded into the frame's staged buffer, CATALOG §V100). This is
 * the 1.21 shape for fork code that still asks a buffer source for a {@link VertexConsumer} per render type and ends
 * the batch itself: the vertices are collected in {@link ByteBufferBuilder}s and each type's mesh is drawn on
 * {@code endBatch} -- a fork-layer {@link LegacyRenderType} through its own draw (into its own output target), a
 * vanilla type through {@code prepare().drawFromBuffer(info, pass)} in a pass this opens (26.3 copy, §V101: a vanilla
 * type names no target and there is no output override any more, so the pass goes, as {@code LegacyGl.draw}'s does,
 * to the main target's own views, with no depth view when it has no depth). {@link #main()} stands in for
 * {@code renderBuffers().bufferSource()} and is flushed by {@link Flush}.
 *
 * <p>26.3 posts most {@code RenderLevelStageEvent} stages inside the main render pass, where no other pass can be opened
 * ({@link RenderPasses}). So {@link BufferSource#endBatch(RenderPass)} draws into a pass that is already open (an in-pass
 * stage event's {@code getRenderPass()}: {@link Flush} ends the main batch at AfterTranslucentFeatures that way), and a
 * batch ended while a pass is open without one (a type switch in {@code getBuffer}, the mod's own {@code endBatch()} from
 * an in-pass stage) is DEFERRED: its mesh is kept and drawn at the next flush outside a pass (AfterLevel, the frame's
 * end). A {@link LegacyRenderType} with an output target of its own is not the open pass's, so it is deferred too.
 */
// MV-REQUIRES: LegacyRenderType
// MV-REQUIRES: LegacyGl
// MV-REQUIRES: RenderPasses
public interface MultiBufferSource {
    VertexConsumer getBuffer(RenderType type);

    /** A source whose every type shares {@code shared}. */
    static BufferSource immediate(ByteBufferBuilder shared) {
        return new BufferSource(shared, new LinkedHashMap<>());
    }

    /** A source where the types in {@code fixed} write into their own buffer and every other type shares {@code shared}. */
    static BufferSource immediateWithBuffers(SequencedMap<RenderType, ByteBufferBuilder> fixed, ByteBufferBuilder shared) {
        return new BufferSource(shared, fixed);
    }

    /** Draws one finished mesh of {@code type} now (the mesh is closed afterwards). */
    static void draw(RenderType type, MeshData mesh) {
        BufferSource.draw(type, mesh);
    }

    /** The client-wide source ({@code renderBuffers().bufferSource()} on 1.21). */
    static BufferSource main() {
        return Main.INSTANCE;
    }

    final class Main {
        static final BufferSource INSTANCE = new BufferSource(new ByteBufferBuilder(786432), new LinkedHashMap<>());

        private Main() {
        }
    }

    class BufferSource implements MultiBufferSource {
        /** Backs every type without a buffer of its own; holds one type's vertices at a time. */
        protected final ByteBufferBuilder sharedBuffer;
        /** Types that always write into their own buffer, drawn in this order by {@link #endBatch()}. */
        protected final SequencedMap<RenderType, ByteBufferBuilder> fixedBuffers;
        /** The builders begun since their type was last drawn. */
        protected final Map<RenderType, BufferBuilder> startedBuilders = new HashMap<>();
        /** The type currently writing into {@link #sharedBuffer}, or null. */
        protected RenderType lastSharedType;

        protected BufferSource(ByteBufferBuilder sharedBuffer, SequencedMap<RenderType, ByteBufferBuilder> fixedBuffers) {
            this.sharedBuffer = sharedBuffer;
            this.fixedBuffers = fixedBuffers;
        }

        @Override
        public VertexConsumer getBuffer(RenderType type) {
            BufferBuilder open = startedBuilders.get(type);
            if (open != null) {
                if (type.canConsolidateConsecutiveGeometry()) {
                    return open;
                }
                // a type whose consecutive geometry must stay apart (strips, fans, line loops): draw what this
                // builder holds now, so the next caller's vertices start a new primitive instead of joining it
                endBatch(type);
            }
            ByteBufferBuilder own = fixedBuffers.get(type);
            ByteBufferBuilder target;
            if (own != null) {
                target = own;
            } else {
                // the shared buffer holds one type at a time: finish whatever was writing into it first
                if (lastSharedType != null) {
                    endBatch(lastSharedType);
                }
                target = sharedBuffer;
                lastSharedType = type;
            }
            BufferBuilder fresh = new BufferBuilder(target, type.primitiveTopology(), type.format());
            startedBuilders.put(type, fresh);
            return fresh;
        }

        /** Draws the type currently on the shared buffer, if any. */
        public void endLastBatch() {
            if (lastSharedType != null) {
                endBatch(lastSharedType);
            }
        }

        /** Draws everything begun: the shared-buffer type, then the fixed-buffer types in order, then any other. */
        public void endBatch() {
            endBatch((RenderPass) null);
        }

        /** As {@link #endBatch()}, INTO {@code into}, a pass that is already open (null: each draw opens its own). */
        public void endBatch(RenderPass into) {
            if (into == null) {
                Deferred.drawIfNoPassOpen();   // what an earlier in-pass end left, first: it was written first
            }
            if (lastSharedType != null) {
                endBatch(lastSharedType, into);
            }
            for (RenderType type : List.copyOf(fixedBuffers.keySet())) {
                endBatch(type, into);
            }
            for (RenderType type : new ArrayList<>(startedBuilders.keySet())) {
                endBatch(type, into);
            }
        }

        /** Draws what was written for {@code type}, if anything. */
        public void endBatch(RenderType type) {
            endBatch(type, null);
        }

        /** Draws what was written for {@code type}, if anything, into {@code into} (an open pass; null: a pass of its own). */
        public void endBatch(RenderType type, RenderPass into) {
            BufferBuilder builder = startedBuilders.remove(type);
            if (type == lastSharedType) {
                lastSharedType = null;
            }
            if (builder == null) {
                return;
            }
            MeshData mesh = builder.build();   // null when nothing was written
            if (mesh == null) {
                return;
            }
            if (type.sortOnUpload()) {
                // translucent types: order the quads back to front for the current projection before drawing; the
                // sorted index data is written into the same buffer the vertices were collected in
                ByteBufferBuilder written = fixedBuffers.getOrDefault(type, sharedBuffer);
                mesh.sortQuads(written, RenderSystem.getProjectionType().vertexSorting());
            }
            draw(type, mesh, into);
        }

        static void draw(RenderType type, MeshData mesh) {
            draw(type, mesh, null);
        }

        /** Draws one mesh of {@code type} into {@code into} (an open pass) or, with none, in a pass of its own -- deferred
         *  to the next flush outside a pass when a pass is open (the encoder would refuse to open another). */
        static void draw(RenderType type, MeshData mesh, RenderPass into) {
            if (mesh == null) {
                return;
            }
            if (into == null ? LegacyGl.inRenderPass() : type instanceof LegacyRenderType l && !l.drawsIntoMainTarget()) {
                Deferred.add(type, mesh);
                return;
            }
            if (type instanceof LegacyRenderType legacy) {
                if (into == null) {
                    legacy.draw(mesh);   // closes the mesh
                } else {
                    legacy.draw(mesh, into);
                }
                return;
            }
            if (into != null) {
                try (MeshData m = mesh) {
                    LegacyGl.Uploaded up = LegacyGl.Uploaded.inPass(m);
                    type.prepare().drawFromBuffer(new StagedVertexBuffer.ExecuteInfo(up.vertices(), up.indices(),
                            up.indexType(), up.baseVertex(), up.firstIndex(), up.indexCount(), m.drawState().primitiveTopology()), into);
                }
                return;
            }
            try (MeshData m = mesh) {
                RenderTarget target = Minecraft.getInstance().gameRenderer.mainRenderTarget();
                GpuTextureView colorView = target.getColorTextureView();
                if (colorView == null) {
                    return;
                }
                GpuTextureView depthView = target.getDepthTextureView();   // null: the target has no depth
                LegacyGl.Uploaded up = LegacyGl.Uploaded.of(m);
                StagedVertexBuffer.ExecuteInfo info = new StagedVertexBuffer.ExecuteInfo(up.vertices(), up.indices(),
                        up.indexType(), 0, 0, up.indexCount(), m.drawState().primitiveTopology());
                try (RenderPass pass = RenderSystem.getDevice().createCommandEncoder().createRenderPass(
                        () -> "mv buffer source " + type, colorView, Optional.empty(), depthView, OptionalDouble.empty())) {
                    type.prepare().drawFromBuffer(info, pass);
                }
            }
        }
    }

    /** Meshes whose batch ended while a render pass was open and could not go into it: drawn, in the order they were
     *  ended, at the next flush outside a pass. */
    final class Deferred {
        private static final List<Map.Entry<RenderType, MeshData>> MESHES = new ArrayList<>();

        private Deferred() {
        }

        static void add(RenderType type, MeshData mesh) {
            MESHES.add(Map.entry(type, mesh));
        }

        /** How many meshes wait for a flush outside a pass. */
        public static int pending() {
            return MESHES.size();
        }

        /** Draws what waits, unless a pass is (still) open. */
        public static void drawIfNoPassOpen() {
            if (MESHES.isEmpty() || LegacyGl.inRenderPass()) {
                return;
            }
            List<Map.Entry<RenderType, MeshData>> now = new ArrayList<>(MESHES);
            MESHES.clear();
            for (Map.Entry<RenderType, MeshData> e : now) {
                BufferSource.draw(e.getKey(), e.getValue(), null);
            }
        }
    }

    /**
     * 26.2's level renderer no longer ends the main batch: end it after the translucent features, the level and the frame.
     * 26.3 posts AfterTranslucentFeatures inside the main render pass, so that end draws into the event's pass; AfterLevel
     * and the frame's end are outside any pass and also draw what an in-pass end deferred.
     */
    @EventBusSubscriber(modid = "@MODID@", value = Dist.CLIENT)
    final class Flush {
        private Flush() {
        }

        @SubscribeEvent
        public static void afterTranslucentFeatures(RenderLevelStageEvent.AfterTranslucentFeatures event) {
            main().endBatch(event.getRenderPass());
        }

        @SubscribeEvent
        public static void afterLevel(RenderLevelStageEvent.AfterLevel event) {
            main().endBatch();
        }

        @SubscribeEvent
        public static void frameEnd(RenderFrameEvent.Post event) {
            main().endBatch();
        }
    }
}
