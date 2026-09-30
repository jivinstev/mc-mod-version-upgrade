# Supported migration targets

`SUPPORTED_VERSIONS.tsv` is the single list; `./setup` and the migrate-mod skill read it.

| status | meaning | what you should expect |
|---|---|---|
| **tested** | many full ports there have passed every gate (build, GameTest, client) | the catalogue, templates and tools were built for it |
| **reported** | at least one port there has passed the gates, but fewer than the bar below | it has worked; gaps are likely and your port will probably find some |
| *untested* (not listed) | nobody has shown a port there yet | the tools may work; expect to discover API changes the catalogue does not know, and budget for it |

Porting to an untested version is allowed and useful. It is how a version becomes tested.

## How a version moves up

1. **Untested → reported.** A port to that version is delivered with the evidence in its learnings PR:
   the target (`minecraft_version` + NeoForge version), and Gate A and Gate B passing on it (build,
   unit tests, `runGameTestServer`). The reviewer adds a `reported` row whose evidence names that PR.
2. **Reported → tested.** Once **3 different mods** have been ported there with that evidence, and at
   least one of them also passed Gate C (a real client), the row becomes `tested`.

A beta loader (for example a NeoForge `-beta` build) can be `reported`, never `tested`: the API is still
moving under it.
