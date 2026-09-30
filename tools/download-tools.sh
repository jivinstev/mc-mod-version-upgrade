#!/usr/bin/env bash
# Fetch the external tools the migration pipeline depends on: the two decompilers.
#
# They are DOWNLOADED rather than committed on purpose. Both licences would allow redistribution
# (Vineflower is Apache-2.0, CFR is MIT), but a repository that ships no third-party jars keeps the
# simplest rule in tools/check-no-ip.py intact -- "no .jar except Gradle's wrapper, by sha1" -- and
# owes no redistribution notices for something it never distributed.
#
# Every download is PINNED BY SHA1 and verified before it is kept, like every other download path in
# this project. The pins were checked against the upstream release assets, not copied from a local
# file. A mismatch deletes the download and exits non-zero: a decompiler that is not the one we
# pinned is not a decompiler we should be running over anyone's code.
#
# Idempotent: a present jar is re-verified, not re-downloaded. Run from anywhere.
set -euo pipefail
cd "$(dirname "$0")"

# name | version | sha1 | url
TOOLS=(
  "vineflower.jar|1.10.1|4f48c5947b21f9ebc743e7c80215ee839d3dc668|https://github.com/Vineflower/vineflower/releases/download/1.10.1/vineflower-1.10.1.jar"
  # CFR is the fallback decompiler: catalogue section A recovers a method Vineflower could not
  # decompile with it. Without this entry that step silently stops working.
  "cfr.jar|0.152|48ef4892cfe8feffddbbd0ff077735140557db74|https://github.com/leibnitz27/cfr/releases/download/0.152/cfr-0.152.jar"
)

sha1_of() {
  if command -v sha1sum >/dev/null 2>&1; then sha1sum "$1" | cut -d' ' -f1
  else shasum -a 1 "$1" | cut -d' ' -f1; fi          # macOS ships shasum, not sha1sum
}

fail=0
for row in "${TOOLS[@]}"; do
  IFS='|' read -r name version want url <<<"$row"
  if [[ -f "$name" ]]; then
    got=$(sha1_of "$name")
    if [[ "$got" == "$want" ]]; then
      echo "$name $version present, sha1 OK"
      continue
    fi
    echo "$name is present but its sha1 is $got, not the pinned $want -- replacing it." >&2
    rm -f "$name"
  fi
  echo "Downloading $name $version ..."
  tmp="$name.part"
  rm -f "$tmp"
  if ! curl -fsSL -o "$tmp" "$url"; then
    echo "  FAILED to download $url" >&2
    rm -f "$tmp"; fail=1; continue
  fi
  got=$(sha1_of "$tmp")
  if [[ "$got" != "$want" ]]; then
    echo "  REFUSED: sha1 $got does not match the pinned $want. Nothing was kept." >&2
    rm -f "$tmp"; fail=1; continue
  fi
  mv "$tmp" "$name"
  echo "  sha1 OK"
done

if [[ $fail -ne 0 ]]; then
  echo "download-tools: one or more tools could not be fetched and verified." >&2
  exit 1
fi

# Running them is a second, cheaper check (a JDK is required for the migration path anyway).
if command -v java >/dev/null 2>&1; then
  java -jar vineflower.jar --help >/dev/null 2>&1 && echo "vineflower runs" \
    || { echo "vineflower.jar did not run" >&2; exit 1; }
  java -jar cfr.jar --help >/dev/null 2>&1 && echo "cfr runs" \
    || { echo "cfr.jar did not run" >&2; exit 1; }
else
  echo "(no java on PATH -- skipped the run check; the migration path needs a JDK anyway)"
fi
echo "Tools ready in $(pwd)."
