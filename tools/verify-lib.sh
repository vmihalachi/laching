#!/usr/bin/env bash
# Shared verification execution for app-owned check registration. Bash 3.2 or newer.
# check/skip/summary/finish retain failures and notes; discover_changes fills $changed from
# $mode/$base/$paths and optionally expands a landing submodule. Functions run from the repo root.
# LACHING_CHECK_SECONDS (default 1800), LACHING_PROGRESS_SECONDS (10), LACHING_PROGRESS_INTERVAL (300)
# control bounded checks. LACHING_VERIFY_TIMING=1 adds elapsed time.
# check <label> locked <name> <command...> serializes builds under LACHING_VERIFY_LOCK_DIR (default $PWD/build/verify-locks).

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  echo "verify-lib.sh is sourced by verify.sh, not run" >&2
  exit 2
fi

verify_tools="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
verify_root="$PWD"
failed=() notes=()
verify_active="" verify_lock_pid=""
verify_limit="${LACHING_CHECK_SECONDS:-1800}"
verify_progress="${LACHING_PROGRESS_SECONDS:-10}"
verify_interval="${LACHING_PROGRESS_INTERVAL:-300}"
for verify_number in "$verify_limit" "$verify_progress" "$verify_interval"; do
  case "$verify_number" in ''|*[!0-9]*|0) echo 'Verification time limits must be positive integer seconds.' >&2; exit 2 ;; esac
done
logdir="$(mktemp -d "${TMPDIR:-/tmp}/verify.XXXXXX")"
exec 3>&1
has() { command -v "$1" >/dev/null 2>&1; }

# Each check gets its own process group, including shell functions and their descendants.
# Only groups started by this harness are signalled; another verifier or unrelated job is untouched.
verify_stop() {
  [ -n "$verify_active" ] || return 0
  if ! kill -TERM -- "-$verify_active" 2>/dev/null; then verify_active=""; return 0; fi
  sleep 0.2
  kill -KILL -- "-$verify_active" 2>/dev/null || true
  wait "$verify_active" 2>/dev/null || true
  verify_active=""
}
# The lock helper belongs to the verifier, outside the command group. Release it only after
# command teardown, including the KILL fallback for descendants that ignore TERM.
verify_unlock() {
  [ -n "$verify_lock_pid" ] || return 0
  kill "$verify_lock_pid" 2>/dev/null || true
  wait "$verify_lock_pid" 2>/dev/null || true
  verify_lock_pid=""
}
verify_cleanup() {
  # Repeated interrupts must not abort the TERM-to-KILL grace period or release a live build's lock.
  trap '' INT TERM
  verify_stop
  verify_unlock
  rm -rf "$logdir"
}
trap verify_cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

check() {
  local name="$1"; shift
  local log="$logdir/${name//[^A-Za-z0-9]/_}.log" started=$SECONDS since status=0 next="$verify_progress"
  local ready="" lock folder launched=0
  since="$(date +%H:%M)"
  : >"$log"
  if [ "${1:-}" = locked ]; then
    shift
    lock="${1:-}"; shift
    case "$lock" in
      ''|*[!A-Za-z0-9_.-]*|.|..) echo "Invalid verification lock name: $lock" >>"$log"; status=2 ;;
      *)
        folder="${LACHING_VERIFY_LOCK_DIR:-$verify_root/build/verify-locks}"
        ready="$logdir/lock.$lock.$RANDOM.ready"
        if mkdir -p "$folder"; then
          # A separate group keeps TERM/KILL of the build from releasing its OS lock early.
          set -m
          python3 "$verify_tools/verify-lock.py" "$folder/$lock.lock" "$ready" "$$" >>"$log" 2>&1 &
          verify_lock_pid=$!
          set +m
        else status=1; fi ;;
    esac
  fi
  while [ "$status" = 0 ]; do
    if [ -n "$ready" ] && [ ! -f "$ready" ]; then
      if ! kill -0 "$verify_lock_pid" 2>/dev/null; then
        wait "$verify_lock_pid" 2>/dev/null || true
        echo "Build lock helper exited before acquiring $lock" >>"$log"
        status=1
        break
      fi
    elif [ "$launched" = 0 ]; then
      set -m
      "$@" >>"$log" 2>&1 &
      verify_active=$!
      set +m
      launched=1
    elif ! kill -0 "$verify_active" 2>/dev/null; then
      wait "$verify_active" 2>/dev/null; status=$?
      break
    fi
    if [ $((SECONDS - started)) -ge "$verify_limit" ]; then
      verify_stop
      verify_unlock
      printf 'FAIL  %s: stopped after %d s (LACHING_CHECK_SECONDS=%d); the end of its log:\n' "$name" "$verify_limit" "$verify_limit"
      tail -n 30 "$log" | sed 's/^/      /'
      failed+=("$name")
      return 0
    fi
    if [ $((SECONDS - started)) -ge "$next" ]; then
      printf '...   %s, running %d min %d s (since %s)\n' "$name" $(((SECONDS - started) / 60)) $(((SECONDS - started) % 60)) "$since"
      next=$((next + verify_interval))
    fi
    sleep 0.1
  done
  # A command may leave descendants after exiting; retain its lock through their teardown too.
  verify_stop
  verify_unlock
  if [ "$status" = 0 ]; then printf 'ok    %s' "$name"
  else printf 'FAIL  %s' "$name"; failed+=("$name"); fi
  if [ "${LACHING_VERIFY_TIMING:-0}" = 1 ]; then printf ' (%d s)' $((SECONDS - started)); fi
  printf '\n'
  if [ "$status" != 0 ]; then tail -n 30 "$log" | sed 's/^/      /'; fi
}

skip() { notes+=("$1"); }
changed_files() {
  local f re list="$changed"
  files=()
  for re in "$@"; do list="$(printf '%s\n' "$list" | grep -E "$re")"; done
  while IFS= read -r f; do [ -n "$f" ] && [ -f "$f" ] && files+=("$f"); done <<< "$list"
}

# Hooks export repository-local Git variables. Remove every one, including GIT_COMMON_DIR and GIT_OBJECT_DIRECTORY,
# before asking a submodule about its own state.
subgit() {
  local dir="$1" vars; shift
  vars="$(git rev-parse --local-env-vars)"
  ( unset $vars; git -C "$dir" "$@" )
}
discover_changes() {
  local root="$1" landing="${2:-}" recorded
  case "$mode" in
    worktree) changed="$(git -c core.quotepath=false diff --name-only HEAD; git -c core.quotepath=false ls-files --others --exclude-standard)" ;;
    staged) changed="$(git -c core.quotepath=false diff --cached --name-only)" ;;
    base) changed="$(git -c core.quotepath=false diff --name-only "$base"...HEAD; git -c core.quotepath=false diff --name-only HEAD; git -c core.quotepath=false ls-files --others --exclude-standard)" ;;
    paths) changed="$(printf '%s\n' "${paths[@]+"${paths[@]}"}")"
      local path normalized=""
      while IFS= read -r path; do normalized="$normalized
${path#"$root"/}"; done <<< "$changed"
      changed="$normalized" ;;
  esac
  if [ "$mode" != paths ] && [ -n "$landing" ] && [ -e "$landing/.git" ] &&
    printf '%s\n' "$changed" | grep -qxF "$landing"; then
    recorded="$(git rev-parse -q --verify "${base:-HEAD}:$landing" 2>/dev/null || git rev-parse -q --verify "HEAD:$landing")"
    changed="$changed
$({ subgit "$landing" -c core.quotepath=false diff --name-only "$recorded" 2>/dev/null; subgit "$landing" -c core.quotepath=false ls-files --others --exclude-standard; } | while IFS= read -r path; do printf '%s/%s\n' "$landing" "$path"; done)"
  fi
  changed="$(printf '%s\n' "$changed" | sed '/^$/d' | sort -u)"
}

# Prints an executable name/path; callers may pass the result directly or use it in a build subshell.
find_tool() {
  [ -n "$1" ] || return 1
  if has "$1"; then printf '%s\n' "$1"
  elif [ -x "$2/node_modules/.bin/$1" ]; then printf '%s\n' "$2/node_modules/.bin/$1"
  else return 1; fi
}
summary() {
  local n
  for n in "${notes[@]+"${notes[@]}"}"; do printf 'skip  %s\n' "$n"; done
  if [ -z "$changed" ] && [ "$all" = 0 ]; then echo "No changed paths."; fi
  if [ "${LACHING_VERIFY_TIMING:-0}" = 1 ]; then printf 'Checks took %d min %d s.\n' $((SECONDS / 60)) $((SECONDS % 60)); fi
}
finish() {
  if [ ${#failed[@]} -gt 0 ]; then echo "${#failed[@]} check(s) failed."; exit 1; fi
  exit 0
}
