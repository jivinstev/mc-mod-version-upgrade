# Supported migration targets

`SUPPORTED_VERSIONS.tsv` is the single list; `./setup`, the migrate-mod skill and the reviewer read it
through `tools/supported-versions.py`, the only place the rule is computed.

| status | rule | what you should expect |
|---|---|---|
| **tested** | **10 different mods** ported there with **Gate C passing** (a real client booted, spawned, exercised items) | the catalogue, templates and tools are proven there |
| **reported** | at least one such mod, fewer than 10 | it has worked; setup and the migrate skill show "reported (N of 10)" so you can decide; expect gaps |
| *untested* | not listed | the tools may still work; expect API changes the catalogue does not know, and budget for them |

Porting to any version is allowed: setup and the skills show the status, they do not block you. Such a
port is exactly how a version moves up.

## How a version moves up (automatically)

1. A port finishes with Gate C passing. The migrate-mod skill runs
   `tools/propose-learnings.py ... --gate-c passed`, and the learnings PR records the port's exact target
   (`minecraft_version`, NeoForge version) and that Gate C passed.
2. The reviewer, on merging, runs `python3 tools/supported-versions.py record <minecraft> neoforge "PR #N"`
   once per distinct mod. At 10 the status becomes `tested` with no further decision.

**The owner can override** a row by hand (the `override` column, with the reason in `evidence`): pin a
version `tested` early, or hold one at `reported` (for example while its loader is still a `-beta` build).
