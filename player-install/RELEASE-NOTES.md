**Player install @TAG@**: one command to install a repository's mod, its dependencies and NeoForge into your own Minecraft.

macOS / Linux:

```
curl -fsSL https://github.com/jivinstev/mc-mod-version-upgrade/releases/download/@TAG@/install.sh | bash -s -- OWNER/REPO
```

Windows (PowerShell):

```
& ([scriptblock]::Create((irm https://github.com/jivinstev/mc-mod-version-upgrade/releases/download/@TAG@/install.ps1))) OWNER/REPO
```

Add `--yes` to take every default. `OWNER/REPO:MOD` picks one mod of a repository that releases several. The bootstrap
checks this release's `player-install.zip` against the sha256 written into it (also in SHA256SUMS) before running it.
Built by `tools/player-install.py --bundle`.
