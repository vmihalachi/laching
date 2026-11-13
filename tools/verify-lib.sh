#!/usr/bin/env bash
# Shared verification execution for app-owned check registration. Bash 3.2 or newer.
# check/skip/summary/finish retain failures and notes; discover_changes fills $changed from
# $mode/$base/$paths and optionally expands a landing submodule. Functions run from the repo root.
# LACHING_CHECK_SECONDS (default 1800), LACHING_PROGRESS_SECONDS (10), LACHING_PROGRESS_INTERVAL (300)
# control bounded checks. LACHING_VERIFY_TIMING=1 adds elapsed time.
# check <label> locked <name> <command...> serializes builds under LACHING_VERIFY_LOCK_DIR (default $PWD/build/verify-locks).
# queue <group> <label> [locked <name>] <command...> keeps a check for run_queued. The checks of one group share a build
# folder or a device and run in sequence; the groups run side by side, each check's lines held back and printed in the
# order queued, so the output, the failures and the exit status are a serial run's. LACHING_VERIFY_SERIAL=1 runs them
# one after another, each printed as it ends; that is the default on Windows (Git Bash), where 0 asks for side by side.

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  echo "verify-lib.sh is sourced by verify.sh, not run" >&2
  exit 2
fi

verify_tools="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
verify_root="$PWD"
failed=() notes=()
verify_active="" verify_lock_pid=""
# In a group's runner (run_queued): verify_runner is set and verify_tag keeps its checks' files apart from another group's.
verify_runners=() verify_runner="" verify_tag="" verify_looked=""
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
# The groups' runners stop their own check and release its lock (their EXIT trap) before they end: wait for each.
verify_stop_runners() {
  local pid
  for pid in "${verify_runners[@]+"${verify_runners[@]}"}"; do kill -TERM "$pid" 2>/dev/null || true; done
  for pid in "${verify_runners[@]+"${verify_runners[@]}"}"; do wait "$pid" 2>/dev/null || true; done
  verify_runners=()
}
# In a runner: the verifier is gone, or was killed outright and waits as a zombie for its parent (one look a second).
verify_orphaned() {
  kill -0 "$$" 2>/dev/null || return 0
  [ "$verify_looked" != "$SECONDS" ] || return 1
  verify_looked=$SECONDS
  case "$(ps -o stat= -p "$$" 2>/dev/null)" in *Z*) return 0 ;; esac
  return 1
}
verify_cleanup() {
  # Repeated interrupts must not abort the TERM-to-KILL grace period or release a live build's lock.
  trap '' INT TERM
  verify_stop
  verify_unlock
  verify_stop_runners
  rm -rf "$logdir"
}
trap verify_cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# verify_skip_status: the exit status with which a check's command says it skipped itself (verify.sh sets 77 around the
# app's own checks); empty, every status but 0 is a failure. Such a check is listed under `skip` with the last line it
# printed, isn't a failure, and leaves verify_skipped=1 for the caller.
verify_skip_status="" verify_skipped=0
check() {
  local name="$1"; shift
  local log="$logdir/$verify_tag${name//[^A-Za-z0-9]/_}.log" started=$SECONDS since status=0 next="$verify_progress"
  local ready="" lock folder launched=0 why
  verify_skipped=0
  since="$(date +%H:%M)"
  : >"$log"
  if [ "${1:-}" = locked ]; then
    shift
    lock="${1:-}"; shift
    case "$lock" in
      ''|*[!A-Za-z0-9_.-]*|.|..) echo "Invalid verification lock name: $lock" >>"$log"; status=2 ;;
      *)
        folder="${LACHING_VERIFY_LOCK_DIR:-$verify_root/build/verify-locks}"
        ready="$logdir/lock.$lock.$verify_tag$RANDOM.ready"
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
      # For the verifier, should this runner be killed before it can stop its check.
      [ -z "$verify_runner" ] || echo "$verify_active" >"$logdir/queue.${verify_tag}group"
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
    if [ -n "$verify_runner" ]; then
      # The verifier reports a runner's progress (queue_report). Without a verifier (killed outright, its terminal
      # closed) nobody reads this check: stop it, as the verifier's own cleanup would have.
      ! verify_orphaned || exit 143
    elif [ $((SECONDS - started)) -ge "$next" ]; then
      printf '...   %s, running %d min %d s (since %s)\n' "$name" $(((SECONDS - started) / 60)) $(((SECONDS - started) % 60)) "$since"
      next=$((next + verify_interval))
    fi
    sleep 0.1
  done
  # A command may leave descendants after exiting; retain its lock through their teardown too.
  verify_stop
  verify_unlock
  if [ -n "$verify_skip_status" ] && [ "$status" = "$verify_skip_status" ] && [ "$launched" = 1 ]; then
    why="$(awk 'NF { sub(/^[ \t]+/, ""); line = $0 } END { print line }' "$log")"
    skip "$name${why:+: $why}"
    verify_skipped=1
    return 0
  fi
  if [ "$status" = 0 ]; then printf 'ok    %s' "$name"
  else printf 'FAIL  %s' "$name"; failed+=("$name"); fi
  if [ "${LACHING_VERIFY_TIMING:-0}" = 1 ]; then printf ' (%d s)' $((SECONDS - started)); fi
  printf '\n'
  if [ "$status" != 0 ]; then tail -n 30 "$log" | sed 's/^/      /'; fi
}

skip() { notes+=("$1"); }

# --- queued checks -----------------------------------------------------------------------------

# One entry per queued check: its group, label, `check` arguments (a slice of queue_args) and the verify_skip_status it
# was queued under. run_queued fills queue_result (ok, fail or skip) and queue_started (epoch seconds), and calls
# $queue_after <index> as each check is reported, in the order queued: where the caller notes what ran.
queue_group=() queue_name=() queue_first=() queue_count=() queue_args=() queue_skip=()
queue_result=() queue_started=() queue_pid=() queue_next=0 queue_after=""
queue() {
  queue_group+=("$1"); shift
  queue_name+=("$1")
  queue_first+=("${#queue_args[@]}"); queue_count+=("$#")
  queue_args+=("$@")
  queue_skip+=("$verify_skip_status")
  queue_result+=(""); queue_started+=("")
}

# queue_check <index> [<its start>]: the queued check, run here with `check`, away from the verifier's input.
queue_check() {
  local i="$1" before=${#failed[@]} status="$verify_skip_status"
  verify_skip_status="${queue_skip[$i]}"
  queue_started[$i]="${2:-$(date +%s)}"
  check "${queue_args[@]:${queue_first[$i]}:${queue_count[$i]}}" </dev/null
  verify_skip_status="$status"
  if [ "$verify_skipped" = 1 ]; then queue_result[$i]=skip
  elif [ ${#failed[@]} -gt "$before" ]; then queue_result[$i]=fail
  else queue_result[$i]=ok; fi
}

# queue_runner <group> <first> <end>: that group's checks among the queued ones, in sequence, in a subshell with a
# process group of its own. Each check's lines go to a file the verifier prints in its turn; its result (and the note
# of one that skipped itself) is moved into place whole, so the verifier never reads half of one. A failed check
# doesn't stop the ones after it. The verifier's EXIT trap isn't this subshell's: the logs are the verifier's to remove.
queue_runner() {
  local group="$1" i stamp
  set +m
  verify_runner=1 verify_runners=()
  trap 'trap "" INT TERM; verify_stop; verify_unlock' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  for ((i = $2; i < $3; i++)); do
    [ "${queue_group[$i]}" = "$group" ] || continue
    ! verify_orphaned || exit 143
    verify_tag="$i." notes=()
    stamp="$(date '+%s %H:%M')"
    printf '%s\n' "$stamp" >"$logdir/queue.$i.tmp" && mv -f "$logdir/queue.$i.tmp" "$logdir/queue.$i.started"
    queue_check "$i" "${stamp%% *}" >"$logdir/queue.$i.out"
    rm -f "$logdir/queue.$i.group" # the check's process group is stopped and gone: its number may be another's now
    printf '%s\n' "${queue_result[$i]}" "${queue_started[$i]}" "${notes[@]+"${notes[@]}"}" >"$logdir/queue.$i.tmp" &&
      mv -f "$logdir/queue.$i.tmp" "$logdir/queue.$i.done"
  done
  exit 0 # an EXIT trap doesn't run when a background function only returns (Bash 3.2)
}

# queue_report <index>: waits for a runner's check, saying how long it has run as `check` does, then prints its lines
# and keeps its failure or its skip note. A runner that ended without the result is a failure, never a silent pass.
queue_report() {
  local i="$1" name="${queue_name[$1]}" done="$logdir/queue.$1.done" began="" since="" next="$verify_progress"
  local gone=0 stamp result="" started="" note="" group=""
  while [ ! -f "$done" ]; do
    if [ "$gone" = 1 ]; then
      if [ -f "$logdir/queue.$i.started" ]; then
        # A runner killed outright stopped nothing: its check's process group is this verifier's to stop.
        [ ! -f "$logdir/queue.$i.group" ] || read -r group <"$logdir/queue.$i.group" || true
        case "$group" in '' | *[!0-9]*) ;; *) verify_active=$group; verify_stop ;; esac
        printf 'FAIL  %s: its run stopped without a result; the end of its log:\n' "$name"
        tail -n 30 "$logdir/$i.${name//[^A-Za-z0-9]/_}.log" 2>/dev/null | sed 's/^/      /'
      else printf 'FAIL  %s: never ran, the run of its group stopped\n' "$name"; fi
      failed+=("$name")
      queue_result[$i]=fail
      return 0
    fi
    # The result lands before its runner ends: look once more for it after the runner is gone.
    if ! kill -0 "${queue_pid[$i]}" 2>/dev/null; then gone=1; continue; fi
    if [ -z "$began" ] && [ -f "$logdir/queue.$i.started" ]; then
      read -r stamp since <"$logdir/queue.$i.started" || true
      case "$stamp" in '' | *[!0-9]*) ;; *) began=$((SECONDS - $(date +%s) + stamp)) ;; esac
    fi
    if [ -n "$began" ] && [ $((SECONDS - began)) -ge "$next" ]; then
      printf '...   %s, running %d min %d s (since %s)\n' "$name" $(((SECONDS - began) / 60)) $(((SECONDS - began) % 60)) "$since"
      # A check that ran behind another's wait is already past several intervals: one line for them all.
      while [ "$next" -le $((SECONDS - began)) ]; do next=$((next + verify_interval)); done
    fi
    sleep 0.1
  done
  cat "$logdir/queue.$i.out" 2>/dev/null
  { IFS= read -r result; IFS= read -r started; IFS= read -r note; } <"$done" || true
  queue_started[$i]="$started"
  case "$result" in
    ok) ;;
    skip) notes+=("$note") ;;
    fail) failed+=("$name") ;;
    *) printf 'FAIL  %s: its result is unreadable\n' "$name"; result=fail; failed+=("$name") ;;
  esac
  queue_result[$i]="$result"
}

# Runs the checks queued since the last call. A single group, or LACHING_VERIFY_SERIAL, runs them here one after
# another, as `check` calls would; so does Windows (Git Bash) unless LACHING_VERIFY_SERIAL=0, the side-by-side run
# being untried there.
run_queued() {
  local first=$queue_next end=${#queue_group[@]} i group groups=() known pid serial="${LACHING_VERIFY_SERIAL:-}"
  queue_next=$end
  [ "$first" -lt "$end" ] || return 0
  for ((i = first; i < end; i++)); do
    known=0
    for group in "${groups[@]+"${groups[@]}"}"; do [ "$group" != "${queue_group[$i]}" ] || known=1; done
    [ "$known" = 1 ] || groups+=("${queue_group[$i]}")
  done
  if [ -z "$serial" ]; then case "${OSTYPE:-}" in msys* | cygwin* | win32*) serial=1 ;; *) serial=0 ;; esac; fi
  [ "$serial" = 0 ] || groups=(serial)
  if [ ${#groups[@]} -lt 2 ]; then
    for ((i = first; i < end; i++)); do
      queue_check "$i"
      [ -z "$queue_after" ] || "$queue_after" "$i"
    done
    return 0
  fi
  for group in "${groups[@]}"; do
    # Its own process group, so a terminal's interrupt reaches the verifier alone, which then stops each runner.
    set -m
    queue_runner "$group" "$first" "$end" </dev/null >&2 &
    pid=$!
    set +m
    verify_runners+=("$pid")
    for ((i = first; i < end; i++)); do [ "${queue_group[$i]}" != "$group" ] || queue_pid[$i]=$pid; done
  done
  for ((i = first; i < end; i++)); do
    queue_report "$i"
    [ -z "$queue_after" ] || "$queue_after" "$i"
  done
  for pid in "${verify_runners[@]}"; do wait "$pid" 2>/dev/null || true; done
  verify_runners=()
}
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
