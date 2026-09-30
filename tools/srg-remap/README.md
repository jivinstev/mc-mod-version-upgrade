# srg-remap — the linchpin of decompile-based migration

A compiled **Forge 1.20.x** mod jar references Minecraft with **official class
names but SRG member names**: methods and fields look like `m_20615_`, `f_19853_`.
Vineflower decompiles this faithfully, so the raw `.java` is full of SRG names and
**will not compile against NeoForge**, which uses Mojang official names
(`create`, `level`, `getInstance`). (a ~750-file boss mod: **83k** SRG
references across 486 files.)

SRG member ids are **globally unique**, so a flat `{srg: official}` dictionary
applied as a text replacement over the decompiled sources is safe and complete —
no per-class disambiguation needed. That is what these two scripts do.

```bash
# 1) Build the dict for a Minecraft version (downloads Mojang + MCPConfig mappings,
#    joins them on the obfuscated names). One file, self-contained.
python3 build_mapping.py 1.20.1 srg2official-1.20.1.json

# 2) Rewrite m_*/f_* -> official names across a decompiled source tree.
python3 apply_mapping.py srg2official-1.20.1.json ../../mods/<modid>/src/main/java
```
Unmapped ids (a few Forge-injected members not in the vanilla mapping) are left
untouched and show up as ordinary `cannot find symbol` errors to fix by hand.

`forge_import_codemod.pl` is the companion mechanical pass for the *loader* side —
`net.minecraftforge.*` → `net.neoforged.*` package/class renames. Apply it with:
```bash
find <src> -name '*.java' -print0 | xargs -0 perl -i -p forge_import_codemod.pl
```
(Do the SRG remap and this import codemod first; then the semantic buckets —
capabilities, networking, data components — surface as real, bounded errors.)

Data sources:
- Mojang official mappings: Minecraft version manifest → `client_mappings`.
- MCPConfig `joined.tsrg`: `maven.neoforged.net/.../de/oceanlabs/mcp/mcp_config/<ver>`.
