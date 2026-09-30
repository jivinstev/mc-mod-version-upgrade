#!/usr/bin/env bash
# Install the local git hooks. Run once after cloning.
#
# The pre-push hook does two things CI cannot: it refuses a direct push to `main` (this repository
# takes pull requests only), and it runs the IP gate BEFORE anything leaves the machine. CI is the
# authority, but by the time CI speaks the content is already on GitHub -- and for the one thing this
# gate guards, that is too late.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p .git/hooks
cat > .git/hooks/pre-push <<'HOOK'
#!/usr/bin/env bash
set -uo pipefail
root="$(git rev-parse --show-toplevel)"
while read -r local_ref _ remote_ref _; do
  case "$remote_ref" in
    refs/heads/main)
      echo "pre-push: REFUSED — this repository takes pull requests only." >&2
      echo "          Push a branch and open a PR:  git push -u origin HEAD" >&2
      echo "          (bootstrap only, and deliberately: git push --no-verify)" >&2
      exit 1 ;;
  esac
done
echo "pre-push: running the IP gate before anything leaves this machine..."
python3 "$root/tools/check-no-ip.py" --root "$root" || {
  echo "pre-push: REFUSED — the IP gate failed. Nothing has been pushed." >&2
  exit 1
}
HOOK
chmod +x .git/hooks/pre-push
echo "installed: .git/hooks/pre-push  (refuses pushes to main; runs check-no-ip.py)"
