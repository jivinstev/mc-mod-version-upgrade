#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# The cloud environment's setup script: paste this file's contents into the environment's
# "Setup script" box. It runs as root on Ubuntu 24.04 once, and the result is cached.
set -euo pipefail
apt-get update
# A virtual screen and software OpenGL, for the real-client test (Gate C).
apt-get install -y xvfb mesa-utils libgl1-mesa-dri
# Java 25, which Minecraft 26.x needs. Java 21 is already installed.
install -d /etc/apt/keyrings
curl -fsSL https://packages.adoptium.net/artifactory/api/gpg/key/public | gpg --dearmor -o /etc/apt/keyrings/adoptium.gpg
echo "deb [signed-by=/etc/apt/keyrings/adoptium.gpg] https://packages.adoptium.net/artifactory/deb noble main" > /etc/apt/sources.list.d/adoptium.list
apt-get update
apt-get install -y temurin-25-jdk
