# mod-registry — dual-backend mod index CLI

A backend-agnostic front-end over **Modrinth** and **CurseForge** for the
`install-mod` skill. It answers: *does a mod exist, is it compatible with our
target, what does it depend on, and can we download it?* — over both indexes.

Everything prints **JSON to stdout**. Nothing is written to disk (see ToS below).

## Usage
```bash
# find candidates across BOTH backends (deduped, sorted by downloads)
python3 modreg.py search --query "giant squid" [--limit 20]

# classify a project against the target (default neoforge / 1.21.1)
python3 modreg.py versions --provider curseforge --id 123456 [--loader neoforge --mc 1.21.1]
#   classification: native | needs-migrate | needs-downport | none

# required/optional dependencies of a specific file
python3 modreg.py deps --provider modrinth --id geckolib --file <versionId>

# download a file (verifies sha1); exit 3 = author opt-out, exit 4 = hash mismatch
python3 modreg.py download --provider modrinth --id geckolib --file <versionId> --out /path/x.jar

# introspect a local jar (no network): modid(s), loader, MC range, mixins, jarjar
python3 modreg.py resolve-modid --jar /path/to/mod.jar
```

`--provider` is `modrinth` or `curseforge`. `--id` is the Modrinth slug or the
CurseForge numeric mod id (both are returned by `search`). `--file` is the
Modrinth version id or the CurseForge file id (returned by `versions`).

## Compatibility classification
`versions` compares every available build against the target `(loader, mc)`:
- **native** — a build for exactly the target loader+MC exists → download only.
- **needs-migrate** — best source is an **older** MC (e.g. Forge 1.20.1) → hand to `migrate-mod`.
- **needs-downport** — only **newer** builds exist (e.g. 1.21.4) → ask the user, then downport.
- **none** — nothing usable.

`chosen` is the recommended source file; `older_mcs` / `newer_mcs` let the caller
raise the right question (downport? which interpretation?).

## Requirements
- Python 3 (stdlib only — `urllib`, `zipfile`; no pip installs). Works on 3.9+.
- `CURSEFORGE_API_KEY` in `.env.local` (walked up from cwd), **or** in the process
  environment — `.env.local` wins, the environment is the fallback that makes an
  ephemeral container work. Modrinth needs no key. Without the key CurseForge is
  skipped with a warning and you silently get Modrinth-only results, so check
  `warnings` in `search` output.

## ToS / security contract (read before extending)
- **No persistent cache of catalog data.** The CurseForge API terms forbid saving
  or caching API responses. This tool makes **live** calls and prints results;
  it never writes a catalog to disk. (An in-process memo within one invocation is
  fine — it dies with the process.) The `install-mod` PLAN artifact stores only
  **decisions + stable identifiers** (provider, id, fileId, filename, hash) needed
  to re-query live on resume — not a searchable copy of the catalog.
- **Honor the author download opt-out.** When CurseForge returns a null
  `downloadUrl` (the author disabled third-party distribution), `download` returns
  `{"status":"opt-out","websiteUrl":...}` and exits non-zero. Never scrape around it.
- **Never print the API key.** It is read from `.env.local` (else the environment)
  and sent only in the `x-api-key` header — never logged, echoed, or included in
  output or errors.
- **Redistribution.** Downloaded jars are for the user's own instance; porting for
  personal use is fine, public redistribution of a ported jar is not (mod license).
