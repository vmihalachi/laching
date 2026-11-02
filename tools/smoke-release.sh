#!/bin/bash
# Launches a Release build of the Mac app and checks it's still running 20 s later with no crash report: the build App
# Store review and customers get (hardened runtime, the Store bundle ID, Release-only strings), which Debug runs and the
# tests never start. A Windows port once shipped three versions that crashed at launch that way. macos-app.yml runs it
# (`ci-macos.py smoke`) with `hosted` and `release`; see the release-mac skill. The executable and the crash report's
# name are companion.json's mac.app.
#
#   laching/tools/smoke-release.sh <path to the Release .app> [seconds]
#
# GitHub-hosted CI only (it refuses to run without CI set, or on a self-hosted runner): a Release build has the App
# Store bundle ID, the same app and data container as an App Store install, so on your Mac it would open with your
# real data. Locally, test the TestFlight build instead.
set -euo pipefail

app="${1:?usage: smoke-release.sh <path to the Release .app> [seconds]}"
seconds="${2:-20}"
if [[ -z "${CI:-}" ]]; then
  echo "smoke-release.sh runs in CI only: a Release build shares the App Store app's data." >&2
  exit 2
fi
if [[ "${RUNNER_ENVIRONMENT:-}" == "self-hosted" ]]; then
  echo "smoke-release.sh runs on GitHub-hosted runners only: a self-hosted runner is your Mac (run with hosted)." >&2
  exit 2
fi
TOOLS=$(cd "$(dirname "$0")" && pwd)
env_file=$(python3 "$TOOLS/companion.py" env)
eval "$env_file"
if [[ -z "$MAC_APP_NAME" ]]; then
  echo "companion.json has no mac.app: the executable's name" >&2
  exit 2
fi
binary="$app/Contents/MacOS/$MAC_APP_NAME"
[[ -x "$binary" ]] || { echo "No app at $binary" >&2; exit 1; }

reports="$HOME/Library/Logs/DiagnosticReports"
marker="$(mktemp)"
log="$(mktemp)"
"$binary" >"$log" 2>&1 &
pid=$!
sleep "$seconds"

status=0
if kill -0 "$pid" 2>/dev/null; then
  echo "Running after $seconds s."
  kill "$pid"
  wait "$pid" 2>/dev/null || true
else
  wait "$pid" && code=0 || code=$?
  echo "The app quit within $seconds s (exit status $code)." >&2
  status=1
fi
# A crash leaves a report even when the process is already gone.
if crash="$(find "$reports" -newer "$marker" -name "$MAC_APP_NAME*" 2>/dev/null | head -1)" && [[ -n "$crash" ]]; then
  echo "Crash report: $crash" >&2
  head -60 "$crash" >&2
  status=1
fi
if [[ "$status" != 0 ]]; then
  echo "--- app output ---" >&2
  tail -40 "$log" >&2
fi
rm -f "$marker" "$log"
exit "$status"
