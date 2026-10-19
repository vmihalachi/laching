#!/bin/sh
# Renders the Mac app's windows in light and dark to PNGs, with sample state and throwaway settings: a Debug build run
# in screenshot mode (Laching's ScreenshotCapture, started by `mac.screenshotEnv`). Your real settings aren't touched.
# Usage: laching/tools/screenshots.sh [--only name,name] [--hidden|--front] [--skin id] [--themes light,dark] [--no-build]
#        [output-dir] [language]   (defaults: screenshots.out, the source language)
#   --only      just the shots whose file names start with one of the names, for checking a change: the app renders
#               those, and only those are copied. Pair it with a scratch output-dir so the committed screenshots stay as
#               they are. Implies --hidden.
#   --hidden    the windows open transparent, behind everything and without taking the focus, so you can keep working;
#               the shots show them inactive (grey traffic lights and controls). For checks, never the committed shots.
#   --front     the windows come forward and are shot key, as in use (the default without --only); leave the Mac alone.
#   --skin      the skin the run wears (a skin id: auguste), owning in the sample store whatever unlocks it; the app's
#               default skin otherwise. Pair it with an output-dir of its own: the file names don't carry the skin.
#   --themes    the appearances to shoot, in that order (light, dark; both by default): a run asked for one never draws
#               the other.
#   --no-build  run the last build (check-layout.sh builds once, then runs the languages side by side).
#   --build-only  just build.
# Fails, after copying the shots, when the run reports problems (ScreenshotCapture's problems.txt, left next to the
# shots): a window that couldn't be drawn, a shot that wasn't written, content taller than its window. check-layout.sh
# runs it per language.
set -e
ONLY=""
HIDDEN=""
SKIN=""
THEMES=""
BUILD=1
RUN=1
while [ $# -gt 0 ]; do
  case "$1" in
    --only) ONLY="$2"; [ -n "$HIDDEN" ] || HIDDEN=1; shift 2 ;;
    --hidden) HIDDEN=1; shift ;;
    --front) HIDDEN=0; shift ;;
    --skin) SKIN="$2"; shift 2 ;;
    --themes) THEMES="$2"; shift 2 ;;
    --no-build) BUILD=""; shift ;;
    --build-only) RUN=""; shift ;;
    *) break ;;
  esac
done
[ "$HIDDEN" = 1 ] || HIDDEN=0
TOOLS=$(cd "$(dirname "$0")" && pwd)
env_file=$(python3 "$TOOLS/companion.py" env)
eval "$env_file"
OUT="${1:-$ROOT/${SCREENSHOTS_OUT:?companion.json has no screenshots.out: pass an output-dir}}"
LANG_CODE="${2:-$SOURCE_LANGUAGE}"
APP="$MAC_APP_NAME"
ENV_NAME="$SCREENSHOT_ENV"
cd "$ROOT/$MAC_DIR"
DERIVED="build/screenshots.noindex"

if [ -n "$BUILD" ]; then
  xcodebuild -project "$MAC_PROJECT" -scheme "$MAC_SCHEME" -configuration Debug \
    -destination 'platform=macOS' -derivedDataPath "$DERIVED" build -quiet $XCODE_CACHE_MAPPED 2>&1 | grep -v 'warning:' || true
fi
[ -n "$RUN" ] || exit 0
# A sandboxed app renders into its container's temp folder and prints that path last.
SRC=$(env "$ENV_NAME=${ONLY:-all}" LACHING_SCREENSHOT_HIDDEN="$HIDDEN" LACHING_SCREENSHOT_SKIN="$SKIN" LACHING_SCREENSHOT_THEMES="$THEMES" \
  "$DERIVED/Build/Products/Debug/$APP.app/Contents/MacOS/$APP" -AppleLanguages "($LANG_CODE)" -AppleLocale "$LANG_CODE" | tail -1)
[ -d "$SRC" ] || { echo "No screenshots were rendered" >&2; exit 1; }
mkdir -p "$OUT"
rm -f "$OUT/problems.txt"
COUNT=0
for file in "$SRC"/*.png; do
  [ -e "$file" ] || continue
  name=$(basename "$file")
  if [ -n "$ONLY" ]; then
    # An app may render more than it was asked for (a demo that makes all its shots in one go): keep only those.
    match=""
    for prefix in $(echo "$ONLY" | tr ',' ' '); do
      case "$name" in "$prefix"*) match=1 ;; esac
    done
    [ -n "$match" ] || continue
  fi
  cp "$file" "$OUT"/
  echo "$OUT/$name"
  COUNT=$((COUNT + 1))
done
[ "$COUNT" -gt 0 ] || { echo "No screenshots matched --only $ONLY" >&2; exit 1; }
echo "$COUNT screenshots in $OUT"
PROBLEMS=""
if [ -s "$SRC/problems.txt" ]; then
  PROBLEMS=$(sed 's/^/  /' "$SRC/problems.txt")
  cp "$SRC/problems.txt" "$OUT/problems.txt"
fi
# Each run renders into a folder of its own in the app's temporary folder; one named by LACHING_SCREENSHOT_DIR stays.
[ -n "$LACHING_SCREENSHOT_DIR" ] || rm -rf "$SRC"
if [ -n "$PROBLEMS" ]; then
  printf 'Problems (%s):\n%s\n' "$LANG_CODE" "$PROBLEMS" >&2
  exit 1
fi
