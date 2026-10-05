#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Runs at the start of every Claude Code session (see .claude/settings.json).
# In a cloud session it points Gradle at Google's mirror of Maven Central: the real one
# rate-limits cloud IPs (HTTP 429), and Gradle doesn't fall back to another repository.
# On your own computer it does nothing.
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0
init="${GRADLE_USER_HOME:-$HOME/.gradle}/init.d/central-mirror.gradle"
[ -f "$init" ] && exit 0
mkdir -p "$(dirname "$init")"
cat > "$init" <<'GRADLE'
// Written by cloud/session_start.sh (mc-buddy-builder / mc-mod-version-upgrade). Maven Central, through Google's mirror of it.
def mirror = 'https://maven-central.storage-download.googleapis.com/maven2/'
def central = { r -> r instanceof MavenArtifactRepository &&
        (r.url.toString().contains('repo.maven.apache.org') || r.url.toString().contains('repo1.maven.org')) }
def point = { repos -> repos.configureEach { r -> if (central(r)) r.url = mirror } }
beforeSettings { s -> point(s.pluginManagement.repositories); point(s.dependencyResolutionManagement.repositories) }
allprojects { p -> point(p.repositories); p.buildscript { point(repositories) } }
GRADLE
