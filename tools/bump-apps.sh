#!/usr/bin/env bash
# Moves companion apps to Laching X.Y.Z: `companion.py bump X.Y.Z` in each, then its `scripts/verify.sh --fast`, while
# its pure suites run in the background (swift test on mac.core, dotnet test on windows.coreTests, Gradle's
# :<android.core>:test: those this machine has the tools for; unlocked, as scripts/verify.sh runs them). Then the apps'
# report (`companion.py apps`), which fails the run when an app is behind this checkout's newest release, linked, or its
# pins disagree, and the command that runs each moved app's slow checks, the ones --fast leaves out: start it in the
# background next.
# Commits nothing: read each app's diff and commit there (the landing page's change inside its own repository). Runs
# this checkout's companion.py, so an app whose submodule predates `bump` moves too. The apps are the folders with a
# companion.json next to this Laching checkout (not a linked git worktree: its main checkout is the app), unless named.
# The report places a release by this checkout's tags, so a vX.Y.Z it hasn't fetched yet (the Release run tagged it on
# GitHub) is fetched from its origin first; one origin hasn't either refuses the run, exit 2, before any app moves.
# Then it waits once until NuGet.org lists every package their bumps pin (`companion.py bump --wait-only`), so each
# bump finds them listed; --no-wait, anywhere among the arguments, doesn't ask. One run moves an app at a time: each
# holds a lock in the app's Git folder (laching-bump-apps.lock) and refuses, exit 2, an app whose lock another run
# holds; a lock whose run has ended (its pid gone, or no longer a bump-apps.sh) is taken over.
#
#   laching/tools/bump-apps.sh 0.9.1                     # ../*/companion.json, next to this checkout
#   laching/tools/bump-apps.sh 0.9.1 ../app-a ../app-b
#   laching/tools/bump-apps.sh --no-wait 0.9.1 [../app-a ...]  # without asking NuGet.org
#   laching/tools/bump-apps.sh --report [../app-a ...]  # each app's pin against the releases, and conform
#                                                       # (companion.py apps)
set -uo pipefail

TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
usage() { awk 'NR > 1 { if (!/^#/) exit; sub(/^# ?/, ""); print }' "${BASH_SOURCE[0]}"; }
case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  --report) shift; exec python3 "$TOOLS/companion.py" apps "$@" ;;
esac
no_wait=""
args=()
for arg in "$@"; do
  if [ "$arg" = --no-wait ]; then no_wait=1; else args+=("$arg"); fi
done
set -- ${args[@]+"${args[@]}"}  # bash 3.2 calls an empty array unbound
[ $# -gt 0 ] || { usage >&2; exit 2; }
version="${1#v}"  # X.Y.Z, its v optional, as companion.py bump takes it: a typo refuses here, not as a tag to release
shift
if [[ ! $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "'$version' isn't a version (X.Y.Z), as companion.py bump takes it" >&2
  exit 2
fi

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

# The apps' report counts an app on a tag this checkout lacks as on no release: fetch it, or refuse before anything
# moves when origin hasn't it either (not released yet, or offline: then each bump's ls-remote would fail too).
laching="$(dirname "$TOOLS")" tag="v$version"
if ! git -C "$laching" rev-parse -q --verify "refs/tags/$tag" >/dev/null 2>&1; then
  if git -C "$laching" fetch -q --no-tags origin "refs/tags/$tag:refs/tags/$tag" 2>/dev/null; then
    echo "Fetched $tag into $laching, for the apps' report"
  else
    echo "$tag isn't a tag in $laching, and fetching it from its origin failed: release it first," >&2
    echo "or fetch it (git -C $laching fetch --tags origin), so the apps' report can place it." >&2
    exit 2
  fi
fi

# One run moves an app at a time: its lock is a folder holding the run's pid, in the app's Git folder (in the app
# itself outside Git). Every app's is taken before NuGet.org is asked, so a second run refuses at once, not after the
# wait. A lock whose pid has ended, or is now another program's, is a run that died, and is taken over under a second
# lock (<lock>.takeover), so of two runs that find it so, one takes it. On exit (an INT, a TERM or a HUP too), the run
# lets go of the locks it took, stops the pure suites still running (each one's process group: the swift, dotnet or
# Gradle it started too) and keeps their logs only when one failed.
locks=() suite_pids=() logs="" keep_logs=""
let_go() {
  local pid lock
  for pid in ${suite_pids[@]+"${suite_pids[@]}"}; do
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
    wait "$pid" 2>/dev/null  # quietly: no "Terminated" line for a suite stopped here
  done
  for lock in ${locks[@]+"${locks[@]}"}; do rm -rf "$lock"; done
  [ -z "$logs" ] || [ -n "$keep_logs" ] || rm -rf "$logs"
}
trap let_go EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP  # the terminal gone: the suites' own process groups never hear it, so let_go stops them
running() { # <pid>: true while it is a bump-apps.sh (ps sees other users' too; where ps can't say, while it lives)
  local command
  command="$(ps -p "$1" -o command= 2>/dev/null)" || { kill -0 "$1" 2>/dev/null; return; }
  case "$command" in *bump-apps*) return 0 ;; *) return 1 ;; esac
}
lock_app() { # <app>: takes its lock, or says which run holds it and fails
  local app="$1" lock pid taken=""
  if [ -e "$app/.git" ]; then
    lock="$(git -C "$app" rev-parse --absolute-git-dir)/laching-bump-apps.lock" || return 1
  else
    lock="$app/.laching-bump-apps.lock"
  fi
  if ! mkdir "$lock" 2>/dev/null; then
    pid="$(cat "$lock/pid" 2>/dev/null)"
    [ "$pid" != $$ ] || return 0  # named twice
    if [ -z "$pid" ]; then
      echo "A bump-apps.sh is starting on $app, or died as it did: its lock $lock has no pid." >&2
      echo "Remove that folder if no run is moving the app." >&2
      return 1
    fi
    if running "$pid"; then
      echo "Another bump-apps.sh (pid $pid) is moving $app: wait for it to end (its lock: $lock)" >&2
      return 1
    fi
    if ! mkdir "$lock.takeover" 2>/dev/null; then
      echo "Another bump-apps.sh is taking over $app's lock ($lock), or died doing it: remove $lock.takeover" >&2
      echo "and $lock if no run is moving the app." >&2
      return 1
    fi
    # Only the run holding <lock>.takeover is here: the lock is still the dead run's, or another run took it since.
    if [ "$(cat "$lock/pid" 2>/dev/null)" = "$pid" ] && rm -rf "$lock" && mkdir "$lock" 2>/dev/null; then
      echo "Took over $app's lock from pid $pid, a run that ended without letting go of it"
      taken=1
    fi
    rmdir "$lock.takeover"
    [ -n "$taken" ] || { echo "Another bump-apps.sh took $app's lock ($lock) as this one did" >&2; return 1; }
  fi
  locks+=("$lock")
  echo $$ >"$lock/pid"
}
for app in "${apps[@]}"; do
  lock_app "$app" || exit 2
done

# pure <app> <log>: the app's pure suites one after another, as scripts/verify.sh runs them, each one this machine has
# the tools for: swift test on mac.core, dotnet test on windows.coreTests (in windows.dir, so its global.json picks the
# SDK), Gradle's :<android.core>:test (JDK 17 and the Android SDK: on a Mac, Homebrew's openjdk@17 and Android Studio's
# SDK unless set). Their output goes to <log>, the name of each that ran to <log>.ran and of each that failed to
# <log>.failed; the status is 1 when one failed.
pure() {
  local app="$1" log="$2" env_file
  : >"$log.ran"
  : >"$log.failed"
  if ! env_file="$(COMPANION_ROOT="$app" python3 "$TOOLS/companion.py" env)"; then
    echo "companion.py env" >>"$log.failed"
    return 1
  fi
  eval "$env_file"
  cd "$ROOT" || return 1
  if [ "$(uname)" = Darwin ]; then
    local jdk=/opt/homebrew/opt/openjdk@17 sdk="$HOME/Library/Android/sdk"
    [ -n "${JAVA_HOME:-}" ] || [ ! -x "$jdk/bin/java" ] || export JAVA_HOME="$jdk"
    [ -n "${ANDROID_HOME:-}" ] || [ ! -d "$sdk" ] || export ANDROID_HOME="$sdk"
  fi
  if [ -n "$MAC_CORE" ] && command -v swift >/dev/null 2>&1; then
    suite "$log" "swift test" swift test --package-path "$MAC_CORE"
  fi
  if [ -n "$WINDOWS_CORE_TESTS" ] && command -v dotnet >/dev/null 2>&1; then
    suite "$log" "dotnet test" core_dotnet
  fi
  if [ -n "$ANDROID_CORE" ] && [ -x "$ANDROID_DIR/gradlew" ] \
    && { command -v java >/dev/null 2>&1 || [ -n "${JAVA_HOME:-}" ]; }; then
    suite "$log" "gradlew :$ANDROID_CORE:test" core_gradle
  fi
  [ ! -s "$log.failed" ]
}
core_dotnet() { (cd "$WINDOWS_DIR" && dotnet test "$WINDOWS_CORE_TESTS/$WINDOWS_CORE_TESTS.csproj" -v q --nologo); }
core_gradle() { (cd "$ANDROID_DIR" && ./gradlew ":$ANDROID_CORE:test" -q --console=plain); }
suite() { # <log> <name> <command...>
  local log="$1" name="$2" code=0
  shift 2
  echo "$name" >>"$log.ran"
  echo "== $name" >>"$log"
  "$@" >>"$log" 2>&1 </dev/null || code=$?
  [ "$code" = 0 ] || { echo "$name failed (exit $code)" >>"$log"; echo "$name" >>"$log.failed"; }
}
joined() { # <file>: its lines, comma-separated
  local line out=""
  while IFS= read -r line; do out="${out:+$out, }$line"; done <"$1"
  printf '%s' "$out"
}

# One wait for them all: each bump waiting on its own would add up, one app after another, when NuGet.org never lists
# the release. Each bump still asks, once, and goes on.
if [ -z "$no_wait" ]; then
  python3 "$TOOLS/companion.py" bump --wait-only "$version" "${apps[@]}" || exit $?
fi

summary=() slow=() suite_logs=() suite_names=() tails=()
failed=0
for app in "${apps[@]}"; do
  name="$(basename "$app")"
  echo "== $name"
  if ! COMPANION_ROOT="$app" python3 "$TOOLS/companion.py" bump ${no_wait:+--no-wait} "$version"; then
    summary+=("bump failed    $name")
    failed=1
    continue
  fi
  # The pure suites start as the app moves, side by side with its fast checks and the next apps' bumps.
  [ -n "$logs" ] || logs="$(mktemp -d "${TMPDIR:-/tmp}/laching-bump-apps.XXXXXX")" || exit 2
  log="$logs/${#suite_pids[@]}-$name.log"
  set -m  # its own process group, so stopping it on exit stops the swift, dotnet or Gradle under it too
  pure "$app" "$log" >>"$log" 2>&1 </dev/null &
  suite_pids+=($!) suite_logs+=("$log") suite_names+=("$name")
  set +m
  verify="$app/scripts/verify.sh" shown=scripts/verify.sh
  [ -x "$verify" ] || verify="$TOOLS/verify.sh" shown="$TOOLS/verify.sh"
  slow+=("$(printf 'cd %q && %q' "$app" "$shown")")
  if (cd "$app" && COMPANION_ROOT="$app" "$verify" --fast); then
    summary+=("ok             $name")
  else
    summary+=("verify failed  $name")
    failed=1
  fi
done

[ ${#suite_pids[@]} -eq 0 ] || { echo; echo "== the pure suites, started in the background as each app moved"; }
i=0
while [ "$i" -lt ${#suite_pids[@]} ]; do
  log="${suite_logs[$i]}" name="${suite_names[$i]}"
  if wait "${suite_pids[$i]}"; then
    if [ -s "$log.ran" ]; then summary+=("pure ok        $name: $(joined "$log.ran")")
    else summary+=("pure skipped   $name: no toolchain here for its pure suites"); fi
  else
    summary+=("pure failed    $name: $(joined "$log.failed") (log: $log)")
    tails+=("$log")
    keep_logs=1 failed=1
  fi
  i=$((i + 1))
done
suite_pids=()

echo
echo "Laching $version (nothing committed):"
printf '  %s\n' "${summary[@]}"
for log in ${tails[@]+"${tails[@]}"}; do
  echo
  echo "The end of $log:"
  tail -n 30 "$log" | sed 's/^/  /'
done
echo
if ! python3 "$TOOLS/companion.py" apps "${apps[@]}"; then
  echo "The apps' report above found drift: an app behind this checkout's newest release, linked, its pins disagreeing,"
  echo "the apps on different releases, or one on a tag this checkout lacks (git -C $laching fetch --tags origin)."
  failed=1
fi
if [ ${#slow[@]} -gt 0 ]; then
  echo
  echo "Left: each moved app's slow checks (its builds and the tests --fast leaves out). Start them in the background:"
  printf '  %s\n' "${slow[@]}"
fi
exit "$failed"
