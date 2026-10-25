#!/usr/bin/env bash
# Install the local git hooks. Run once after cloning.
#
# The pre-push hook does three things CI cannot: it refuses a direct push to `main` (this repository
# takes pull requests only), it runs the IP gate BEFORE anything leaves the machine, and it compares the
# pushed template Java with the third-party sources on this machine (tools/check-similarity.py), which CI
# cannot hold. The pre-commit hook runs that last check on each commit, and before it the portability checks
# (tools/check-compat.py and the system bash -n -- bash 3.2 on a Mac) on the staged .py and .sh. CI is the
# authority, but by the time CI speaks the content is already on GitHub -- and for the one thing this
# gate guards, that is too late.
set -uo pipefail
cd -P "$(dirname "$0")/.."   # -P: resolve a symlinked tools/ to the real checkout
mkdir -p .git/hooks
cat > .git/hooks/pre-push <<'HOOK'
#!/usr/bin/env bash
set -uo pipefail
root="$(git rev-parse --show-toplevel)"
pushes=""
while read -r local_ref local_sha remote_ref remote_sha; do
  pushes="$pushes$local_sha $remote_sha"$'\n'
  case "$remote_ref" in
    refs/heads/main)
      echo "pre-push: REFUSED — this repository takes pull requests only." >&2
      echo "          Push a branch and open a PR:  git push -u origin HEAD" >&2
      echo "          (bootstrap only, and deliberately: git push --no-verify)" >&2
      exit 1 ;;
  esac
done
echo "pre-push: running the IP gate before anything leaves this machine..."
# The forbidden-name list lives OUTSIDE this repository (it must never be published). If .env.local
# names a file (FORBIDDEN_NAMES_FILE=...), the name check runs in --strict mode and a missing file
# fails the push; otherwise every other check still runs and the skip is announced.
names="${FORBIDDEN_NAMES_FILE:-}"   # the environment wins; every agent working on forks sets it (CLAUDE.md)
[ -z "$names" ] && [ -f "$root/.env.local" ] && names="$(sed -n 's/^FORBIDDEN_NAMES_FILE=//p' "$root/.env.local" | tail -1)"
names="${names/#\~/$HOME}"
set -- --root "$root"
[ -n "$names" ] && set -- "$@" --names "$names" --strict
. "$root/tools/python.sh" || exit 1   # python3 on Windows too
python3 "$root/tools/check-no-ip.py" "$@" || {
  echo "pre-push: REFUSED — the IP gate failed. Nothing has been pushed." >&2
  exit 1
}
# With a name list, also check what THIS push publishes: commit messages and added lines of the pushed
# commits (the tree scan above cannot see a message, or a name added and removed within the push).
if [ -n "$names" ]; then
  zero="0000000000000000000000000000000000000000"
  while read -r lsha rsha; do
    [ -z "$lsha" ] || [ "$lsha" = "$zero" ] && continue
    if [ "$rsha" = "$zero" ]; then range=("$lsha" --not --remotes); else range=("$rsha..$lsha"); fi
    python3 "$root/tools/check-no-ip.py" --root "$root" --names "$names" --push-range "${range[@]}" || {
      echo "pre-push: REFUSED — the push names a forbidden name (see above). Nothing has been pushed." >&2
      exit 1
    }
  done <<< "$pushes"
fi
# Similarity to third-party sources: the template Java this push publishes, compared with the NeoForge, Minecraft and
# GeckoLib sources on THIS machine (tools/check-similarity.py; CI cannot hold them, so this hook is where it runs).
# 1 refuses; 2 (no reference corpus here) refuses only with MMVU_SIMILARITY_STRICT=1, else the skip is announced.
if [ -f "$root/tools/check-similarity.py" ]; then
  strict="${MMVU_SIMILARITY_STRICT:-}"
  [ -z "$strict" ] && [ -f "$root/.env.local" ] && strict="$(sed -n 's/^MMVU_SIMILARITY_STRICT=//p' "$root/.env.local" | tail -1)"
  zero="0000000000000000000000000000000000000000"
  while read -r lsha rsha; do
    [ -z "$lsha" ] || [ "$lsha" = "$zero" ] && continue
    if [ "$rsha" = "$zero" ]; then sel=(--unpushed "$lsha"); else sel=(--range "$rsha..$lsha"); fi
    python3 "$root/tools/check-similarity.py" "${sel[@]}"; rc=$?
    if [ "$rc" = 2 ]; then
      if [ "$strict" = 1 ]; then
        echo "pre-push: REFUSED — the similarity check could not run and MMVU_SIMILARITY_STRICT=1. Nothing has been pushed." >&2
        exit 1
      fi
      echo "pre-push: WARNING — the similarity check did NOT run (see above): this push's template Java was NOT compared" >&2
      echo "          with third-party sources. Set MMVU_SIMILARITY_STRICT=1 to refuse instead." >&2
    elif [ "$rc" != 0 ]; then
      echo "pre-push: REFUSED — template Java looks transcribed from third-party source (see above). Nothing has been pushed." >&2
      exit 1
    fi
  done <<< "$pushes"
fi
# A skill change is a README change, and a single-version route change says so (CLAUDE.md): compared with origin/main
# when this clone has it.
base=""; git -C "$root" rev-parse -q --verify origin/main >/dev/null && base="--base origin/main"
python3 "$root/tools/check-readme.py" --root "$root" $base || {
  echo "pre-push: REFUSED — the README no longer describes the skills, or a single-version route change says nothing (see above). Nothing has been pushed." >&2
  exit 1
}
HOOK
chmod +x .git/hooks/pre-push
echo "installed: .git/hooks/pre-push  (refuses pushes to main; runs check-no-ip.py, check-similarity.py and check-readme.py)"
# The pre-commit hook runs the similarity check on what the commit records, so an agent finds out at commit time rather
# than at push time. A commit with no template Java staged returns before Python starts.
cat > .git/hooks/pre-commit <<'HOOK'
#!/usr/bin/env bash
set -uo pipefail
root="$(git rev-parse --show-toplevel)"
# Portability, on every commit (about a second): the staged .py and .sh through tools/check-compat.py (Python 3.9
# syntax, GNU-only shell, a bare "bash" argv...), and each staged script through the SYSTEM bash -n -- on a Mac that
# is bash 3.2, the shell CI never runs.
staged="$(git diff --cached --name-only --diff-filter=AMRC -- '*.py' '*.sh')"
if [ -n "$staged" ] && [ -f "$root/tools/check-compat.py" ]; then
  . "$root/tools/python.sh" || exit 1
  # shellcheck disable=SC2086
  (cd "$root" && python3 tools/check-compat.py $staged) >/dev/null 2>&1 || {
    (cd "$root" && python3 tools/check-compat.py $staged) | grep -v '^check-compat: '
    echo "pre-commit: REFUSED — a portability problem (above; CLAUDE.md \"Portability\"). Nothing was committed." >&2
    exit 1
  }
  sysbash=/bin/bash; [ -x "$sysbash" ] || sysbash=bash
  for f in $staged; do
    case "$f" in *.sh) "$sysbash" -n "$root/$f" || {
      echo "pre-commit: REFUSED — $f does not parse with $("$sysbash" -c 'echo bash $BASH_VERSION'). Nothing was committed." >&2
      exit 1; } ;;
    esac
  done
fi
[ -f "$root/tools/check-similarity.py" ] || exit 0   # a branch from before the gate
git diff --cached --name-only --diff-filter=AMRC -- templates/ | grep -qE '\.java(\.example|\.template)?$' || exit 0
. "$root/tools/python.sh" || exit 1   # python3 on Windows too
strict="${MMVU_SIMILARITY_STRICT:-}"
[ -z "$strict" ] && [ -f "$root/.env.local" ] && strict="$(sed -n 's/^MMVU_SIMILARITY_STRICT=//p' "$root/.env.local" | tail -1)"
python3 "$root/tools/check-similarity.py" --staged; rc=$?
if [ "$rc" = 2 ]; then
  if [ "$strict" = 1 ]; then
    echo "pre-commit: REFUSED — the similarity check could not run and MMVU_SIMILARITY_STRICT=1. Nothing was committed." >&2
    exit 1
  fi
  echo "pre-commit: WARNING — the similarity check did NOT run (see above): the staged template Java was NOT compared" >&2
  echo "            with third-party sources. Set MMVU_SIMILARITY_STRICT=1 to refuse instead." >&2
elif [ "$rc" != 0 ]; then
  echo "pre-commit: REFUSED — staged template Java looks transcribed from third-party source (see above). Nothing was committed." >&2
  exit 1
fi
exit 0
HOOK
chmod +x .git/hooks/pre-commit
echo "installed: .git/hooks/pre-commit  (check-compat.py and the system bash -n on staged .py/.sh; check-similarity.py --staged when template Java is staged)"
