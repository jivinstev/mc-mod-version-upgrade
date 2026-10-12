#!/usr/bin/env bash
# Build this fork for another Minecraft version: .multiversion/build.sh <target> [gradle tasks ...]
# Passes the target's versions as -P (they override gradle.properties) plus -Pmc=<target>.
set -euo pipefail
cd "$(dirname "$0")/.."
T="${1:?usage: .multiversion/build.sh <target> [tasks...]}"; shift
mapfile -t P < <(python3 .multiversion/tools/layer.py props --target "$T")
exec bash ./gradlew "${P[@]}" "${@:-build}"
