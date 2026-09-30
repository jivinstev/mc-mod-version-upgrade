# install-mod — backends (Modrinth + CurseForge), ToS, opt-out, key hygiene

Both backends sit behind `tools/mod-registry/` (see its README for the raw API detail). This file is
the **policy** the skill must follow.

## Which backend, when
- **Search:** query BOTH (widest coverage). Modrinth has cleaner data but partial coverage; CurseForge
  is authoritative and where many niche/mob mods live (often CF-only). `search` merges + dedupes by name.
- **Download:** prefer **Modrinth** (unrestricted, direct CDN + sha, no per-author opt-out). Fall back to
  **CurseForge** only when Modrinth lacks the mod/version. This minimizes hitting the opt-out wall.
- **Version/deps:** whichever backend the chosen mod lives on. If a mod is on both, prefer the one with a
  compatible (or closest) build; for downloads still prefer Modrinth.

## CurseForge ToS — non-negotiable
- **No persistent cache of catalog data.** `modreg.py` queries live and prints; it never writes a catalog
  to disk. `plan.json` stores only **decisions + stable identifiers** (provider, id, fileId, filename,
  hash) needed to re-query on resume — NOT a searchable copy. Do not build a local index.
- **Honor the author download opt-out.** A null `downloadUrl` (→ `modreg download` exits 3,
  `{"status":"opt-out","websiteUrl":...}`) means the author disabled third-party distribution. Do NOT
  scrape or work around it. This is the **cf-opt-out gate**: give the user the `websiteUrl`, ask them to
  download the jar manually into `installs/<slug>/jars/`, then continue the pipeline from that jar
  (`resolve-modid` it, classify by its toml, proceed to migrate/test/install).
- **Never print the API key.** It lives in `.env.local` (`CURSEFORGE_API_KEY`) or, failing that, the
  process environment; it is read by the provider and sent only in the `x-api-key` header. Never echo it, log it, put it in `plan.json`, or `cat`
  `.env.local`. (The CLI is already built this way — verified the key is absent from output.)
- **Commercial/quota:** personal use is free; a written licensing agreement only applies above a quota.

## Redistribution (mod licenses, separate from the API ToS)
Downloaded + migrated jars are for the **user's own instance**. Porting for personal use is fine; do NOT
publicly redistribute a ported jar without the author's permission (many mods are all-rights-reserved).
This is already noted in the CATALOG.md legality section and migrate-mod.

## Identifiers cheat-sheet
- **provider:** `modrinth` | `curseforge`.
- **id:** Modrinth slug (e.g. `geckolib`) OR CurseForge numeric mod id (e.g. `123456`) — both from `search`.
- **file:** Modrinth version id OR CurseForge file id — from `versions` (`chosen.fileId`).
- CurseForge loader enum (search filter + file metadata): 1=Forge, 4=Fabric, 5=Quilt, 6=NeoForge.
