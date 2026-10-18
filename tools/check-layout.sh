#!/bin/sh
# Renders the Mac app's windows in each language and fails when one doesn't fit: content taller than its window, a
# window that couldn't be drawn, a shot that wasn't written (the checks screenshots.sh fails on). Longer translations
# are where a window runs out of room, so run it after changing text or a window's layout.
# Usage: laching/tools/check-layout.sh [--only name,name] [language ...]   (default: every language in languages.all)
# Shots go to <mac.dir>/build/layout-check/<language>, to look at what failed. It builds once, then runs the languages
# side by side (LAYOUT_JOBS at a time, default 4) with the windows hidden (screenshots.sh --hidden): you can keep
# working meanwhile, and the shots show the windows inactive.
TOOLS=$(cd "$(dirname "$0")" && pwd)
env_file=$(python3 "$TOOLS/companion.py" env) || exit 2
eval "$env_file"
ONLY=""
if [ "$1" = "--only" ]; then
  ONLY="$2"
  shift 2
fi
# shellcheck disable=SC2086
[ $# -gt 0 ] || set -- $LANGUAGES
OUT="$ROOT/$MAC_DIR/build/layout-check"
JOBS="${LAYOUT_JOBS:-4}"
LOGS=$(mktemp -d)
trap 'rm -rf "$LOGS"' EXIT

"$TOOLS/screenshots.sh" --build-only || exit 1
# A pool rather than batches: the next language starts as soon as any one ends (sh has no `wait -n`, xargs -P does it).
export TOOLS OUT ONLY LOGS
# shellcheck disable=SC2016
printf '%s\n' "$@" | xargs -P "$JOBS" -I {} sh -c '
  lang=$1
  if [ -n "$ONLY" ]; then set -- --only "$ONLY"; else set --; fi
  "$TOOLS/screenshots.sh" "$@" --hidden --no-build "$OUT/$lang" "$lang" > /dev/null 2> "$LOGS/$lang.err"
  echo $? > "$LOGS/$lang.status"' _ {}
FAILED=""
for lang in "$@"; do
  echo "== $lang"
  if [ "$(cat "$LOGS/$lang.status" 2> /dev/null)" != 0 ]; then
    FAILED="$FAILED $lang"
    cat "$LOGS/$lang.err" >&2
  fi
done
if [ -n "$FAILED" ]; then
  echo "Layout problems in:$FAILED (shots in $OUT)" >&2
  exit 1
fi
echo "Every window fits in: $*"
