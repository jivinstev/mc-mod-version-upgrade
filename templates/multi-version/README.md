# Multi-version template — one source tree, several Minecraft targets

Use this when a mod must keep running on the **old** Minecraft while gaining the **new** one.
The catalog sections are the reasoning; this directory is the moving parts.

* **Read first:** `CLAUDE.md` §W (the architecture) and §X (codemod hygiene — a rewrite that
  matches nothing is silent, and that is how three bugs shipped in one afternoon).
* **The era-jump API deltas** are §V.

## Layout it expects

```
src/main/java/          the shared tree, written in the CANONICAL dialect
src/<overlay>/java/     per-target overlay: whole files that REPLACE a shared file
src/<overlay>/resources/
versions/<target>.properties     per-target build data (see versions/README-example.properties)
versions/<target>.renames.tsv    per-target mechanical rename table
```

## The pipeline

`shared tree → apply this target's rename table → overlay replaces whole files → compile`

```
python3 tools/prepare-sources.py \
    --src src/main/java \
    --overlay src/mc26/java \
    --renames versions/26.2.renames.tsv \
    --out build/generated/sources/mc26/java \
    [--gametest-adapter <modid> --gametest-pkg com.<modid>.compat --gametest-structure empty_test]
```

`sourceSets.main.java.srcDirs = [preparedJava]`, and `compileJava dependsOn prepareSources`.
**Run the canonical target through this too** (§W2) — with an empty table its output is
byte-identical to `src/main/java`, which is what keeps the mechanism from rotting.

## The rename table

TSV, `from<TAB>to`, three kinds of row:

| kind | matches | for |
|---|---|---|
| `Foo	Bar` | identifier boundaries, **never after a dot** | a TYPE rename |
| `member:foo	bar` | **only** after a dot | a method/field rename |
| `re:<pattern>	<replacement>` | raw regex | a whole call SHAPE |

Regex rules run **before** token rules, and token rules longest-key-first (§X4, §V11).
Generate the type rows from the two real compile classpaths with
`tools/build-class-move-map.py` (repo root) — not from memory.
Anything that is **not** a pure rename belongs in the overlay, not here.

A rule that matches nothing is reported as a warning. Mark deliberately-sparse generated
blocks `#!exhaustive` so that warning stays readable.

## The GameTest adapter

26.x deleted the `@GameTest` annotation (§V5). `tools/gametest_adapter.py` runs over the
**prepared** tree, strips the three dead annotations plus their imports, and generates a
registrar (`TestFunctionLoader` + `RegisterGameTestsEvent`) from what those annotations said —
so the shared tree stays annotated for the old target and there is one source of truth (§V20).

## The resource generators — both are for defects the compiler cannot see

Two `check`-wired tools, because from 1.21.2 a mod's items stop being bound to their models by
convention and start being bound by DATA. Neither failure crashes, logs an error, or fails any
Java gate; both simply render the magenta missing cube.

**`gen-client-items.py --ns <namespace>`** writes `assets/<ns>/items/<id>.json`, the binding
itself (§V42b). A mod that ships none renders **every** item as the missing cube on 26.x while
1.21.1 is perfect — measured at 231 items on one mod, with the whole board green, because every
check in the repo asked whether the MODEL resource exists rather than whether anything still
points at it. Run it on BOTH targets: the files are inert on 1.21.1, and a resource nobody reads
today is a resource that rots by the next release.

**`gen-spawn-egg-art.py --ns <namespace>`** draws the two greyscale spawn-egg layers (§V42c).
26.x deleted vanilla's egg template, both its textures, and any spawn-egg tint source, so every
modded egg is the missing cube until the mod supplies its own. Point the shared egg models at
`<ns>:item/template_spawn_egg` and overlay **only that template** — the old target parents it
straight back to vanilla's (byte-identical behaviour on the version people are playing), the new
one gives it these two layers. The tool asserts every overlay carries a template, so a third
target that forgets one fails by path rather than silently un-tinting every egg.

**An egg needs no tint source of its own.** Its two colours are fixed literals per item, which is
exactly what vanilla's `minecraft:constant` source is for — so `--egg-tints <java>` reads them out
of the registration call that already carries them and emits two constant tints per egg. No Java,
no registration, no client-side wiring: the whole fix is data, and the colours keep exactly one
home, so `--check` fails if one is edited without regenerating.

## The mixin config is DERIVED too, and for the same reason

**`gen-mixin-config.py --ns <namespace>`** writes `src/<overlay>/resources/<ns>.mixins.json` as
the shared config minus every mixin named in that target's `versions/<t>.drops.txt`. A dropped
file is not compiled, so a mixin the config still names is a class that does not exist — and
Mixin does not shrug at that: it is a hard failure at APPLY, i.e. the client dies at start, on
the one target whose gates get run least (§X15).

The alternative is a hand-written second copy of a 60-entry list that has to be edited every time
a mixin is added, dropped or renamed, with nothing anywhere to say it had drifted. Deriving it
makes the drops list the single source of truth and makes `--check` a `check` task, which is §S2
applied to a resource: the compile loop has a forcing function and a file you must remember to
update does not.

Two rules it enforces that are easy to get wrong by hand. A target that drops **no** mixins must
have **no** overlay copy — an identical second list only shadows the shared one, so a later edit
to the shared config would never reach that target. And a duplicate entry is dropped rather than
carried across: harmless in the shared file, but in a generated one it reads as deliberate.

⚠ **The mixin-config integrity test must read the EFFECTIVE config**, not `src/main/resources`.
Once a target drops a mixin those are different files, and a test pinned to the shared one fails
on a perfectly good build while saying nothing about the file the game actually loads. Pass the
overlay name to the test JVM alongside the project directory (§W9) and resolve overlay-first,
exactly as `build.gradle` orders the resource dirs.

⚠ `--tint` on the client-item generator is **opt-in and is a correctness rule, not a
convenience**: naming a tint source the mod does not register is not an inert extra field, it is
an unknown registry key at data load. Do not copy the flag across from a mod that has one.

## Gate C is in the OTHER template, and it has to be told which target

There is no client launcher here on purpose — `templates/neoforge-mod/tools/client-boot-loop.sh`
and `client-validate.sh` are the ones to copy, and they are multi-version aware. What is worth
saying out loud is *why they had to be*, because the failure is §X15's and it is silent:

- **`MC=<target>` must be FORWARDED to Gradle as `-Pmc`, not merely consumed.** A launcher that
  reads the variable and does not pass it builds the default target and reports a pass under the
  name you asked for — a right-looking result for the wrong version, which nothing downstream can
  detect. Same omission as a release script that honours `MC` for the output folder and not for the
  build (§W10).
- **The signal directory is per-TARGET**, for the reason `build.gradle` keeps one `run/` per target
  (§W11): otherwise the second target's crash digests, launch logs and screenshots replace the
  first's, and a cross-version comparison is exactly what cannot survive that.
- **Do not hardcode `JAVA_HOME`.** The two targets can want different JDK majors (1.21.1 wants 21,
  26.x wants 25 — §V1/§W4), so a pinned path is wrong for one of them by construction. Let the
  build's own toolchain resolve it from `versions/<target>.properties`.
- **A headless Linux box runs Gate C fine** under `xvfb-run` with `LIBGL_ALWAYS_SOFTWARE=1` — Mesa's
  llvmpipe gives OpenGL 4.5 core against MC 1.21's 3.2. Gate the wrapper on `uname = Linux` AND an
  empty `DISPLAY` so macOS and a Linux desktop are untouched, and **exit** when `xvfb-run` is
  missing rather than launching something that cannot open a window: a Gate C that quietly did not
  run is indistinguishable from one not yet reached.
