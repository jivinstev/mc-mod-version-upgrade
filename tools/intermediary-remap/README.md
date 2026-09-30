# intermediary-remap — the Fabric twin of `tools/srg-remap`

A compiled **Fabric** mod jar is remapped to the **intermediary** namespace
(`META-INF/MANIFEST.MF: Fabric-Mapping-Namespace: intermediary`). Every Minecraft
reference in it looks like `net.minecraft.class_1799`, `method_7909`, `field_8125`.
Vineflower decompiles that faithfully, so the raw `.java` will NOT compile against
NeoForge (which uses Mojang official names) — and `tools/srg-remap` is a **no-op**,
because there are no SRG `m_*/f_*` ids to replace.

Intermediary ids are **globally unique**, so a flat dictionary applied as a text
replacement over the decompiled sources is safe and complete.

```bash
# 1) Build the dict for a Minecraft version (downloads Mojang official mappings +
#    Fabric's intermediary tiny-v2, joins them on the OBFUSCATED names).
python3 build_mapping.py 1.20.1 intermediary2official-1.20.1.json
#    -> [1.20.1] intermediary->official: classes 7388, members 67048

# 2) Rewrite class_*/method_*/field_* -> official names across a decompiled tree.
python3 apply_mapping.py intermediary2official-1.20.1.json ../../mods/<modid>/src/main/java

# 3) Verify
grep -rc '\b\(class\|method\|field\)_[0-9]\+' ../../mods/<modid>/src/main/java   # expect 0
```

Three ordered passes (order matters):
1. fully-qualified `net.minecraft.class_A[.class_B…]` -> the official FQ dotted name;
2. bare `class_N` -> the official **simple** name;
3. `method_N` / `field_N` -> the official member name.

**Inner classes are the one subtlety.** Intermediary writes them `class_759$class_5773`,
but Vineflower emits them **dotted** (`import net.minecraft.class_1322.class_1323;`), so
pass 1 accepts a dotted chain and keys the lookup on the `$` form. Pass 2 then handles
the bare inner id via a separate simple-name table.

Unmapped ids are left untouched and show up as ordinary `cannot find symbol` errors.

Data sources:
- Mojang official mappings: version manifest -> `client_mappings` (official <-> obf).
- Fabric intermediary: `maven.fabricmc.net/net/fabricmc/intermediary/<ver>/intermediary-<ver>-v2.jar`
  (obf <-> intermediary, tiny v2).

Companion knowledge: repo `CLAUDE.md` -> **Migration Pattern Catalog §P** (Fabric -> NeoForge).
