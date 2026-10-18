#!/usr/bin/env bash
# Moves companion apps to Laching X.Y.Z: `companion.py bump X.Y.Z` in each, then its `scripts/verify.sh --fast`.
# Commits nothing: read each app's diff and commit there (the landing page's change inside its own repository). Runs
# this checkout's companion.py, so an app whose submodule predates `bump` moves too. The apps are the folders with a
# companion.json next to this Laching checkout (not a linked git worktree: its main checkout is the app), unless named.
#
#   laching/tools/bump-apps.sh 0.9.1                     # ../*/companion.json, next to this checkout
#   laching/tools/bump-apps.sh 0.9.1 ../app-a ../app-b
#   laching/tools/bump-apps.sh --report [../app-a ...]  # each app's pin against the releases, and conform (companion.py apps)
set -uo pipefail

TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "${1:-}" in
  -h|--help) sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
  "") sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2; exit 2 ;;
  --report) shift; exec python3 "$TOOLS/companion.py" apps "$@" ;;
esac
version="$1"
shift

apps=()
if [ $# -gt 0 ]; then
  for app in "$@"; do
    [ -d "$app" ] || { echo "No such folder: $app" >&2; exit 2; }
    apps+=("$(cd "$app" && pwd)")
  done
else
  siblings="$(dirname "$(dirname "$TOOLS")")"
  for manifest in "$siblings"/*/companion.json; do
    [ -f "$manifest" ] || continue
    if [ -f "$(dirname "$manifest")/.git" ]; then
      echo "Skipping $(basename "$(dirname "$manifest")"): a git worktree (name it to move it)"
      continue
    fi
    apps+=("$(dirname "$manifest")")
  done
  [ ${#apps[@]} -gt 0 ] || { echo "No companion app (a folder with a companion.json) in $siblings" >&2; exit 2; }
fi

summary=()
failed=0
for app in "${apps[@]}"; do
  name="$(basename "$app")"
  echo "== $name"
  if ! COMPANION_ROOT="$app" python3 "$TOOLS/companion.py" bump "$version"; then
    summary+=("bump failed    $name")
    failed=1
    continue
  fi
  verify="$app/scripts/verify.sh"
  [ -x "$verify" ] || verify="$TOOLS/verify.sh"
  if (cd "$app" && COMPANION_ROOT="$app" "$verify" --fast); then
    summary+=("ok             $name")
  else
    summary+=("verify failed  $name")
    failed=1
  fi
done

echo
echo "Laching $version (nothing committed):"
printf '  %s\n' "${summary[@]}"
echo
python3 "$TOOLS/companion.py" apps "${apps[@]}"
exit "$failed"
