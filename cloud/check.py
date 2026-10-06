#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Is this Claude Code cloud session ready to build and test Minecraft mods?

    python3 cloud/check.py

Checks that every host in cloud/allowed-domains.txt is reachable, then makes sure the two
tools a cloud session lacks are installed (cloud/ensure.sh: waits for the background install
the session-start hook began, or installs them now). Says exactly what to fix. Exit 0 ready, 1 not.
"""
import os, pathlib, shutil, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
# One real URL per allowed domain: a wildcard is checked through a host it covers.
PROBES = {
    'maven.neoforged.net': 'https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml',
    '*.mojang.com': 'https://piston-meta.mojang.com/mc/game/version_manifest_v2.json',
    '*.minecraft.net': 'https://libraries.minecraft.net/',
    'packages.adoptium.net': 'https://packages.adoptium.net/artifactory/api/gpg/key/public',
    'api.modrinth.com': 'https://api.modrinth.com/v2/',
    'cdn.modrinth.com': 'https://cdn.modrinth.com/',
    'api.curseforge.com': 'https://api.curseforge.com/',
    '*.forgecdn.net': 'https://edge.forgecdn.net/',
    'maven.parchmentmc.org': 'https://maven.parchmentmc.org/',
    'maven.fabricmc.net': 'https://maven.fabricmc.net/',
    'maven.blamejared.com': 'https://maven.blamejared.com/',
    'maven.ithundxr.dev': 'https://maven.ithundxr.dev/',
    'dl.cloudsmith.io': 'https://dl.cloudsmith.io/',
    'thedarkcolour.github.io': 'https://thedarkcolour.github.io/KotlinForForge/',
}


def reachable(url):
    """Any HTTP answer counts (even 404): it means the proxy let us through."""
    p = subprocess.run(['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}', '--max-time', '15', url],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.stdout.strip() not in ('', '000')


def java_versions():
    found = set()
    for d in pathlib.Path('/usr/lib/jvm').glob('*'):
        for v in ('21', '25'):
            if v in d.name and (d / 'bin' / 'javac').exists():
                found.add(v)
    return found


def main():
    in_cloud = os.environ.get('CLAUDE_CODE_REMOTE') == 'true'
    if not in_cloud:
        print('cloud/check.py: this is not a Claude Code cloud session; nothing to check.')
        return 0
    wanted = [l.strip() for l in (ROOT / 'cloud/allowed-domains.txt').read_text(encoding="utf-8").splitlines() if l.strip()]
    problems = []
    blocked = [d for d in wanted if d in PROBES and not reachable(PROBES[d])]
    for d in blocked:
        problems.append('cannot reach %s: add it to the environment\'s Allowed domains' % d)
    if not blocked:
        # Only once the hosts are reachable: the installs download from them.
        print('cloud/check.py: making sure xvfb and Java 25 are installed (first time: a minute or two)...', flush=True)
        subprocess.run(['bash', str(ROOT / 'cloud/ensure.sh')])
    if not shutil.which('xvfb-run'):
        problems.append('xvfb-run is missing: run bash cloud/ensure.sh and read its error')
    jv = java_versions()
    for v in ('21', '25'):
        if v not in jv:
            problems.append('Java %s is missing: run bash cloud/ensure.sh and read its error' % v)
    checked = len([d for d in wanted if d in PROBES])
    if problems:
        print('cloud/check.py: NOT READY (%d of %d hosts reachable)' % (checked - len(blocked), checked))
        for p in problems:
            print('  - ' + p)
        print('Fix it in the cloud environment settings (environment menu in the session title bar, then Edit).')
        print('Steps: https://github.com/jivinstev/mc-mod-version-upgrade#quick-start-in-the-cloud')
        return 1
    print('cloud/check.py: ready. %d hosts reachable, xvfb and Java 21 + 25 installed.' % checked)
    return 0


if __name__ == '__main__':
    sys.exit(main())
