# Deriving test targets from churn (which paths a migration must actually test)

A migrated (decompiled) mod has almost **no pure-unit-testable surface** — there are no clean
Minecraft-free helper classes to `assertEquals` against, so the only true JUnit test is
`MixinConfigIntegrityTest`. That does **not** mean "don't test." It means the testable unit is a
**runtime behavior or a serialization symmetry**, reached by a GameTest, and the question is *which*
ones. The answer is not taste — it's **churn**: test the code you changed the most, because that's
where the migration risk concentrated.

This doc is the standard, data-driven way to pick Gate-A/B/C test targets. Run it as part of **Step 4b
(the compile-clean retrospective)** — the same pass that harvests catalog patterns also harvests test
targets, from the same inputs (every commit + every agent report).

## The method (reusable — run it every migration)

1. **Rank files by churn** — how many migration commits touched each:
   ```bash
   git log --name-only --pretty=format: --grep='<modid>' \
     | grep -E 'src/main/java/.*\.java$' | sed 's#.*/<pkg>/##' \
     | sort | uniq -c | sort -rn | head -40
   ```
2. **Bucket the top files by subsystem** (entities/AI, packets, block entities, capability/SavedData,
   registration, events, client render). The heavy buckets are where the port did the most surgery.
3. **Measure each bucket's blast radius** — how many files a single pattern touched:
   ```bash
   grep -rln 'StreamCodec' src/main/java        | wc -l   # packet round-trip
   grep -rln 'MapCodec<\|Codec<.*> CODEC' src    | wc -l   # serializer round-trip
   grep -rln 'saveAdditional\|addAdditionalSaveData' src | wc -l  # persistence round-trip
   grep -rln 'SavedData\|AttachmentType' src     | wc -l   # capability/attachment round-trip
   ```
4. **Map each high-churn bucket to the cheapest gate + oracle that can reach it** (table below), and
   **prefer ONE parameterized test over a category** to a bespoke test of one file. This is the core
   principle:

   > **The highest-frequency migration pattern deserves the highest-coverage test.** A change applied
   > across N files (e.g. `StreamCodec` on 38 payloads) is proven by one parameterized round-trip that
   > iterates all N — worth far more than a hand-written test of a single entity. Churn tells you N.

5. **Write the ranked list into `MIGRATION.md`** (P0/P1/P2, target → gate → oracle → blast radius) so
   the next session builds tests against risk, not against whatever the generic templates happen to hit.

## Target → gate → oracle (the mapping)

| Churn bucket | Gate | Oracle (what makes it a test, not a smoke check) | Shape |
|---|---|---|---|
| Networking payloads | B (GameTest) | **encode → decode → encode is byte-identical** | parameterize over *every* `CustomPacketPayload` via `BuiltInRegistries`; use a `RegistryFriendlyByteBuf`. Catches R7 (decoder must fully drain buffer) + asymmetric codecs. |
| Serializers (recipe / particle / entity-data / advancement `Codec`) | B | **serialize → deserialize → serialize round-trips** under the datapack `RegistryOps` | one test per registry, iterate all entries |
| Entity/BE persistence (`saveAdditional`/`load`) | B | **save → load → re-save equal**, incl. the ownerless/default instance normal play never saves | namespace-driven `PersistenceGameTest` — covers *all* serializable types with zero per-mod edits. Catches R12. |
| Capability → `SavedData`/data-attachment | B | **write state → NBT → read → equal** | one round-trip per attachment/SavedData type |
| Custom registry + `EntityDataSerializer` | B | registry **populates**; the data serializer **encode/decode** returns an equal value | spawn + read the synched value back |
| Entity ctor / AI / attributes / raycasts | B + C | **spawn every mob and let it tick**; then **battle** so mobs land hits | catches R5/R8/R9/R10 — none reachable without a mob actually constructing + fighting |
| Mixin config | **A (JUnit)** | every name in `<modid>.mixins.json` has a compiled `.class` | `MixinConfigIntegrityTest` — the one true unit test |
| Client render (models/renderers/layers, `renderToBuffer`/`VertexConsumer`) | **C only** | no headless oracle — must render a frame | `gauntlet`/`battle` client modes; there is no substitute |

## Worked example — a large boss mod (609 files), from its own commit history

Churn (migration commits touching each file, top of the list): the boss-fight stage controller 11,
a summoned minion entity 11, the boss entity 11, `TentacleEntity` 10, a second boss-part entity 10,
**the boss's per-level state manager (a capability) 10**, `WorldUtil` 9, the boss's head entity 9,
the main mod class 9, then the whole infected-mob-variant roster + the `*Message` packet cluster at 5–7 each.
Blast radius: **38 packet files, 37 serializable entities/BEs, 24 `Codec` fields, 9 `StreamCodec`s,
7 advancement criteria, 6 recipe serializers, 31 custom-registry/data-serializer files, 3 SavedData.**

Prioritized target list (build in this order; none built yet):

- **P0 — widest blast radius, cheapest pure oracle:**
  1. **Packet `StreamCodec` round-trip** — parameterize over **all 38** `common/packet/*` payloads (the
     packet cluster is heavily churned and `StreamCodec` was a top-frequency pattern). ⚠️ *Coverage flag
     found while measuring:* only **9 of 38** packet files currently carry a `StreamCodec` — verify the
     other 29 are actually migrated (not silently half-ported) before trusting this gate.
  2. **Persistence round-trip** — the namespace-driven `PersistenceGameTest` over all **37** entities/BEs
     (every one rewrote `saveAdditional` with the new `HolderLookup.Provider`). Highest value-per-line.
  3. **Serializer round-trip** — the **24** `Codec`/`MapCodec` fields: 6 recipes, 7 advancement criteria,
     particles, the entity-data serializer — each hand-rewritten from JSON serializers (catalog §K #87/#88/#90).
- **P1 — most-churned stateful subsystems, need a behavior probe:**
  4. **Capability → `SavedData` NBT round-trip** for the boss's per-level state manager — the single most-churned
     non-entity file (10 commits; the whole capability system was rewritten to data attachments, §D).
  5. **Spawn-every-entity + tick** (`SubsystemsTest`) then **battle** — the entity/AI cluster is the top
     churn bucket (the boss entity and its parts/`Tentacle`/`Head`/the infected-mob variants,
     6–11 commits each). This is exactly the gate that would have caught the 7 R10 `ClipContext` NPEs the
     retrospective just fixed by hand.
  6. **Custom `SpellType` registry + `EntityDataSerializer`** populate/encode/decode (§K #86/#87; 31 files).
- **P2 — client/visual, Gate C only:**
  7. **Render cluster** (§L) — ~40 renderer/model/layer files, all mechanically rewritten
     (`renderToBuffer` ARGB int + `VertexConsumer` chain). No headless oracle. **Specifically exercise the
     8 stubbed `*BodyModel.createBodyModel`** (they compile to empty geometry — a frame must be rendered to
     see they're wrong).

The through-line: every P0/P1 target is a bucket the commit history shows we rewrote across many files,
paired with a symmetry oracle (encode↔decode, save↔load) that a parameterized test checks in one shot.
