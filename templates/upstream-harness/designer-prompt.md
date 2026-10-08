You are the DESIGNER for a source-first port of the Minecraft mod in the current directory (its author's own repo, Forge 1.20.1, ForgeGradle) to NeoForge 1.21.1. You do not port anything. You read the repo and write the plan the porting pipeline will follow. Read-only: do not edit any file.

House rules for the result (it becomes a branch in a community fork; the original author will not be asked to do anything):
- Least diff: keep the author's layout, package names, source sets, file names, code style and comments. Change only what the new loader/version requires.
- The branch is named `{BRANCH}`, cut from the author's default branch; later a `neoforge-26.2` branch follows, so prefer a build that can also target 26.2 (ModDevGradle 2.x builds both NeoForge 21.1.x and 26.2).
- Our test harnesses (GameTest, client boot test) must NOT be committed into this repo: they run from an external workspace that consumes the built jar.

Investigate (build.gradle, settings.gradle, gradle.properties, every source set under src/, META-INF contents, the lib/ folder, mixin configs, services files) and answer, concisely:
1. BUILD: what each part of build.gradle does today (source sets, jar/shadow/jarJar tasks, manifest entries, agent/coremod packaging, lib/ usage, publishing), and the exact target build: plugin + version, repositories, dependencies (with coordinates), how each source set and special jar step maps over. Note anything with no NeoForge equivalent.
2. METADATA: mods.toml -> neoforge.mods.toml changes; mixin config changes; services files that must change name (e.g. modlauncher ITransformationService and FML ImmediateWindowProvider service names on NeoForge 1.21.1); access transformer location.
3. RISKS ranked: things that compile but can fail at load on NeoForge 1.21.1 (coremod/transformation service API, java agent self-attach, JVMTI native code, reflection into Forge internals, mixins into changed classes). For each say how we will detect it (which gate) and the fallback if it cannot be ported.
4. SCOPE: which source sets/features are in the 1.21.1 port, which are deferred (with reason), and the files expected to change outside src/main/java.
5. ORDER: the ordered steps for the pipeline.
Output Markdown, at most ~700 words. Name files by path. Do not paste large code.
