#!/usr/bin/env bash
# Run the mandated catalogue sweep (.claude/skills/migrate-mod/references/catalog-scans.md) over a
# port, without copying the fenced block out by hand.
#
#   tools/run-catalog-scans.sh [PORT_DIR]      default: the current directory
#
# The sweep itself is the ```bash block in catalog-scans.md, so there is exactly one copy of it.
# Every path it prints is a HIT to fix or explain (see that file). Exit status: 0 when the sweep
# ran, 2 when it could not (the block is missing or is not valid shell).
set -uo pipefail
here="$(cd -P "$(dirname "$0")/.." && pwd)"   # -P: through the workspace's tools symlink to the checkout
doc="$here/.claude/skills/migrate-mod/references/catalog-scans.md"
port="${1:-.}"
[ -d "$port/src" ] || { echo "run-catalog-scans: $port has no src/ -- run it from mods/<modid>/ or pass the port dir" >&2; exit 2; }
script="$(awk '/^```bash/{f=1;next} /^```/{if(f)exit} f' "$doc")"
[ -n "$script" ] || { echo "run-catalog-scans: no \`\`\`bash block found in $doc" >&2; exit 2; }
bash -n <(printf '%s\n' "$script") || { echo "run-catalog-scans: the sweep in $doc is not valid shell" >&2; exit 2; }
cd "$port" && bash <(printf '%s\n' "$script")
