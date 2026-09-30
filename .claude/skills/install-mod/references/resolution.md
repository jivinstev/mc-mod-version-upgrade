# install-mod — recursive resolution, compat classifier, plan schema, resume

## Compatibility classifier (stage b)
`modreg.py versions --provider <p> --id <id> --loader <L> --mc <MC>` returns `classification`:
- **native** — a build for exactly `(L, MC)` exists → download only.
- **needs-migrate** — best source is an **older** MC (e.g. Forge 1.20.1) → migrate. `chosen` is the newest
  older build, preferring the target loader, then Forge (the filled corpus).
- **needs-downport** — only **newer** builds exist (e.g. 1.21.4) → ask (gate), then migrate as a downport
  with `SRC_MC` = the newer version. `chosen` is the closest newer build.
- **none** — nothing usable.

`chosen` carries `{fileId, fileName, loader (srcLoader), mc (srcMC), sha1, downloadUrl}`;
`older_mcs`/`newer_mcs` let you phrase the gate precisely.

## Recursive dependency algorithm (stage c)
```
installed = set(modreg scan-instance)          # modIds already in MINECRAFT_MODS_DIR
queue = [ root ]                                 # the user-chosen mod
seen  = {}                                       # modId -> node (dedup + cycle guard)
while queue:
    node = queue.pop()
    if node.modId in seen: continue
    seen[node.modId] = node
    classify(node)                               # stage b
    for dep in modreg deps(node.chosen):         # REQUIRED deps only (list optionals separately)
        if dep.modId in installed:
            add node(dep, compat="present-in-instance", status="done")   # skip download+migrate
            continue
        if dep.modId in seen: continue           # cycle/dup
        depNode = classify(dep); depNode.requiredBy=[node.id]
        add depNode; queue.push(depNode)         # recurse into ITS deps
order plan.nodes DEPS-FIRST (topological: a node after everything in its requiredBy chain)
```
Notes: a dep's own required deps are resolved the same way (full recursion). "present-in-instance" is by
**modId**, matched against `scan-instance` (which reads each installed jar's toml). Optional deps: surface
them to the user; only add nodes if they opt in. A dep that is itself `needs-migrate` gets its own
migrate-mod run — order guarantees it's built before the mod that needs it.

## plan.json schema (authoritative; `installs/<slug>/plan.json`)
```jsonc
{
  "request": "install a mod that adds a giant squid boss",
  "created": "<ISO>", "updated": "<ISO>",
  "target": { "loader": "neoforge", "mcVersion": "1.21.1" },
  "nodes": [{
    "id": "giant-squid",               // stable slug / modId
    "role": "root",                     // root | dependency
    "requiredBy": [],                   // parent node ids (the dependency graph edges)
    "selection": { "provider":"curseforge", "projectId":"123456", "fileId":"7654321",
                   "fileName":"...", "sha1":null, "srcLoader":"forge", "srcMC":"1.19.4" },
    "compat": "needs-migrate",          // native-1.21.1 | needs-migrate | needs-downport
                                        //   | present-in-instance | awaiting-user
    "jarPath": "installs/giant-squid/jars/x.jar",
    "migrate": { "needed": true, "src":["forge","1.19.4"], "dst":["neoforge","1.21.1"],
                 "workspace":"mods/giantsquid", "status":"pending", "outputJar":null },
                 //   status: pending | in-progress | built | deployed
    "install": { "status":"pending", "deployedPath":null },   // pending | deployed
    "test": { "headless":"pending", "client":"pending" },     // pending | pass | fail | skipped
    "status": "resolved",               // resolved|downloaded|migrating|migrated|installed|tested|done|blocked|awaiting-user
    "notes": ""
  }],
  "questions": [ { "nodeId":"...", "kind":"needs-downport|cf-opt-out|ambiguous-search",
                  "prompt":"...", "answer": null } ]
}
```
Edit `plan.json` directly (it's plain JSON), then `plan.py render` to refresh `PLAN.md`. Never store a
searchable copy of catalog data here — only these decisions + stable identifiers (ToS; see backends.md).

## Resume semantics
`plan.py show` returns the next actionable node (or the blocking questions). On re-invocation:
1. Load `plan.json`; if any `questions[]` has `answer==null`, resolve those first.
2. Re-query live for the next non-`done` node (nothing is cached — re-run `versions`/`deps` as needed).
3. A node in `migrate.status=in-progress` resumes **inside** migrate-mod — invoke it again; it reads its
   own `mods/<modid>/MIGRATION.md` + branch and continues. install-mod only tracks the coarse status.
4. Continue deps-first until every node is `done`.
