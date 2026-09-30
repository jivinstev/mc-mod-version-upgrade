# install-mod — exact commands per stage

All paths relative to the repo root (`minecraft-forge-upgrade/`). `REG=tools/mod-registry`.
`SLUG` = a short kebab id for the request (e.g. `giant-squid`). `DIR=installs/$SLUG`.
Target defaults: `LOADER=neoforge MC=1.21.1`. Everything under `installs/` is gitignored.

## Setup (once per request)
```bash
mkdir -p installs/$SLUG/jars
# plan.py has no clock — pass an ISO timestamp you generate:
python3 $REG/plan.py init --dir installs/$SLUG --request "<the user's request>" --loader $LOADER --mc $MC --now "<ISO8601>"
```
After ANY change to `plan.json`, re-render the human mirror:
```bash
python3 $REG/plan.py render --dir installs/$SLUG
python3 $REG/plan.py show   --dir installs/$SLUG      # what's the next actionable node? (resume)
```

## (a) SEARCH
```bash
python3 $REG/search --query "<feature or mod name>" --limit 20    # -> both backends, deduped
# (from the mod-registry dir, or: python3 $REG/modreg.py search ...)
```
Present the results (name · provider(s) · downloads · url). Open-ended request → extract the key
term (e.g. "giant squid") and search that. **ambiguous-search gate:** user picks provider + id.

## (b) VERSION CHECK
```bash
python3 $REG/modreg.py versions --provider <p> --id <id> --loader $LOADER --mc $MC
```
Read `classification`: `native | needs-migrate | needs-downport | none`. `chosen` = the source file
(fileId + srcLoader + srcMC). `newer_mcs`/`older_mcs` inform the gates. `needs-downport` → gate.

## (c) DEPENDENCY CHECK (recursive)
```bash
python3 $REG/modreg.py scan-instance                       # modIds already in MINECRAFT_MODS_DIR
python3 $REG/modreg.py deps --provider <p> --id <id> --file <fileId>
```
For each **required** dep: if its modId ∈ scan-instance → node `compat=present-in-instance` (skip
download+migrate); else `versions`-classify it, add a `dependency` node with `requiredBy:[parent]`,
and recurse `deps` on ITS chosen file. Dedup by modId; guard cycles. Write nodes deps-first.
See `references/resolution.md` for the algorithm + node schema.

## (d) DOWNLOAD
```bash
python3 $REG/modreg.py download --provider <p> --id <id> --file <fileId> --out installs/$SLUG/jars/<name>.jar
# exit 3 = CF author opt-out (-> cf-opt-out gate; hand user the websiteUrl); exit 4 = sha1 mismatch
```
Record `jarPath` + `selection.sha1` in the node.

## (e) MIGRATE (deps-first; only needs-migrate / needs-downport nodes)
Invoke the **migrate-mod skill** (see SKILL.md handoff). For the default Forge-1.20.1 case, no params.
Otherwise pass `SRC_LOADER SRC_MC DST_LOADER DST_MC`. migrate-mod produces
`mods/<modid>/build/libs/<modid>-<ver>.jar`; record it as `migrate.outputJar`, set `migrate.status=built`.

## (g) SMOKE-TEST in isolation (before install)
Collect the final jars (native downloads + migrated outputs) and their target modIds (from
`modreg.py resolve-modid --jar <jar>`), then run the harness.

**⚠️ Include REQUIRED DEP jars in `-Psmokejars`, even `present-in-instance` ones.** The harness is
isolated from the real instance, so a dep that's already installed there (e.g. geckolib) is NOT on the
harness classpath — omitting it fails the boot with `NoClassDefFoundError`/`ClassNotFoundException` for
the dep's classes. `present-in-instance` means "don't re-download/install it," NOT "skip it in the test."
Pass every required dep jar (grab the installed one from `MINECRAFT_MODS_DIR`, or the one you downloaded)
alongside the mod: `-Psmokejars="<mod>.jar,<dep1>.jar,<dep2>.jar"`. (Only `-Psmokens` = the mod's own
namespace(s) to drive.) In-place is fine (its `run/` + `build/`
are gitignored); copy it under `installs/$SLUG/` if you need isolation from a concurrent run.
```bash
JARS="/abs/a.jar,/abs/b.jar"          # comma-separated absolute paths
NS="modid1,modid2"                     # the target modIds (NOT smokeharness)
cd templates/smoke-harness

# headless load gate (no display; agent runs it): boots all mods, exits non-zero on a load crash
./gradlew runGameTestServer -Psmokejars="$JARS" --no-daemon --console=plain
#   grep the log for "Found mod file" per jar + BUILD SUCCESSFUL; a load crash fails the task.

# client boot gate (needs a display): macOS self-launch via LaunchServices, watch the signal dir.
#   PHASES DEPEND ON COMPAT:
#     native-1.21.1 (downloaded, not migrated):  PHASES="launch spawn"            (light smoke)
#     migrated (needs-migrate/needs-downport):   PHASES="launch spawn battle gauntlet"  (FULL Gate C)
#   Write tools/smoke.env (open won't forward env):
cat > templates/smoke-harness/tools/smoke.env <<EOT
SMOKEJARS="$JARS"          # mod + EVERY required dep jar (deps needed even if present-in-instance)
SMOKENS="$NS"              # the mod's own namespace(s) to drive
PHASES="launch spawn battle gauntlet"   # or "launch spawn" for a native download
EOT
SIG=/tmp/smokeharness-clientloop; rm -rf "$SIG"; mkdir -p "$SIG"
open templates/smoke-harness/tools/run-gatec.command
#   Each phase runs `runClient -Pboottest -Ptestmode=<phase> -Psmokejars=.. -Psmokens=..`.
#   MONITOR with the script (do NOT hand-roll a poll loop ending in `[ -f crash.ready ]` — it exits 1
#   on the PASS path, mislabeling a green run "failed"). It exits 0=all-done, 1=crash, 3=timeout:
SIG_DIR="$SIG" templates/smoke-harness/tools/watch-gatec.sh 900   # background it; re-run after each fix
#   On RESULT=CRASH: read $SIG/crash.log (names the phase) -> fix the MOD SOURCE -> REBUILD the mod jar
#   (`cd mods/<modid> && ./gradlew build`; harness loads it as an external jar at the same path) ->
#   touch $SIG/fix.done to relaunch -> re-run watch-gatec.sh. RESULT=ALL-DONE (exit 0) -> all phases PASS.
#   Catalog every new §R pattern (battle/gauntlet are where compile-clean-but-crash bugs live).
```
The harness `client-boot-loop.sh` forwards `SMOKEJARS`/`SMOKENS` (env) → `-Psmokejars`/`-Psmokens` on each
`runClient`, so exporting them before `open run-gatec.command` is enough. Headless is the must-pass gate;
client boot is the deeper gate (rendering/client-mixins). If you can't run a display at all, record the
client gate as `skipped` in the node and rely on headless + migrate-mod's own Gate C for migrated nodes.

## (f) INSTALL (last; outward change — confirm first)
```bash
cp installs/$SLUG/jars/<final>.jar "$MINECRAFT_MODS_DIR"/      # atomic; safe while game closed
# for a migrated node, the final jar is mods/<modid>/build/libs/<modid>-<ver>.jar
```
Or use each migrated mod's `./gradlew deployToMods`. Mark nodes `install.status=deployed`, `status=done`;
final `plan.py render`.

## Resume
Re-invoke on the same `$SLUG`: `plan.py show` → the next non-done node → re-query live (nothing cached)
→ continue. A mid-migration dep resumes inside migrate-mod (its MIGRATION.md), not here.
```
