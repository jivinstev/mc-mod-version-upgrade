# mc-mod-version-upgrade

> ⚠️ **Under construction.** The repository skeleton and its safety gates are in place; the tools,
> skills and migration catalogue land next. Not yet usable — please don't rely on it until this
> notice goes away.

Two things in one repo, and most people only want the first:

### Install mods
Search Modrinth and CurseForge behind one interface, resolve dependencies recursively, check whether
a build for your Minecraft version already exists, verify downloads by checksum, and deploy into the
right mods folder. **Needs only Python 3 and a network connection.**

### Migrate a mod to a newer Minecraft version
When your favourite mod is stuck on an old version, port it: decompile, scaffold, rewrite through a
catalogue of known API changes, build, test, deploy. **Needs a JDK and several GB of disk**, takes
hours rather than seconds, and sometimes does not succeed.

---

## Safety gates

Two checks run on every pull request, and both must pass:

| gate | question it answers |
|---|---|
| `tools/check-no-ip.py` | are we about to publish somebody else's work? |
| `tools/check-catalog-fidelity.py` | did we quietly lose a migration rule? |

Run them locally the same way CI does:

```bash
python3 tools/check-no-ip.py
python3 tools/check-catalog-fidelity.py
```

`check-no-ip.py` scans the **committed tree** rather than the diff, because a file added in an
earlier commit and pushed later is still a publication. It treats "could not run" as a failure: a
gate that quietly did not run reports identically to one that passed, and what this one guards
cannot be recalled.

## Contributing

`main` takes pull requests only. Fork, branch, open a PR, and let the gates run:

```bash
gh repo fork jivinstev/mc-mod-version-upgrade --clone
```

**Never include mod source, jars, or decompiler output** — not even in a test fixture. The migration
workspace lives outside the repository by default; please keep it there.

## Licence

Not yet chosen — deliberately. The licence boundary depends on which files the tooling copies into
users' own projects, and that set is an output of the build rather than something to guess in
advance. It will be settled, and stated here, before this repository is made public.
