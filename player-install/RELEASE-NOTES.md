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

**No Python needed.** The installer runs on Python 3.9+ and uses one that is already on the machine. When there is
none (most Windows PCs; a Mac without Apple's developer tools, whose `python3` is only a stub the bootstrap never runs),
the bootstrap says so in one line, downloads a portable Python into a temporary folder, checks its sha256, runs the
installer with it and removes it afterwards; nothing is installed system-wide and no admin rights are needed. Add
`--keep-python` to keep it (under `~/.mmvu/python/`, or `%LOCALAPPDATA%\mmvu\python\` on Windows) for the next run.
Pinned downloads (platform, size in MB, sha256, URL): python-build-standalone's CPython builds on macOS and Linux,
python.org's embeddable CPython on Windows.

```
@PYTHON_PINS@
```

Built by `tools/player-install.py --bundle`.
