#!/usr/bin/env bash
# Runs the checks that apply to a set of changed paths in a companion app, so nobody has to remember which ones. Every
# path and name comes from the app's companion.json (`companion.py env`); the app keeps a two-line scripts/verify.sh
# that execs this one.
#
#   scripts/verify.sh                    # working-tree changes vs HEAD, plus untracked files
#   scripts/verify.sh --staged           # staged files (the pre-commit hook)
#   scripts/verify.sh --base origin/master
#   scripts/verify.sh -- path/a path/b   # explicit paths (the agent Stop hook)
#   scripts/verify.sh --fast ...         # only the checks that take seconds (no xcodebuild, swift/dotnet test, landing build)
#   scripts/verify.sh --all              # every check, regardless of what changed
#   scripts/verify.sh --no-vm ...        # no WinUI build in the Parallels VM (or LACHING_NO_VM=1) while a session holds it
#   scripts/verify.sh --both-platforms   # the WinUI build in the VM for x64 and ARM64, not the VM's own platform alone
#   scripts/verify.sh --shutdown-simulator   # shut the iOS tests' simulator down around their run, not leave it booted
#   scripts/verify.sh --serial ...       # the slow checks one after another, each printed as it ends (or
#                                        # LACHING_VERIFY_SERIAL=1): to debug a check that fails beside another
#
# Path → checks:
#   documentation (*.md, *.rst, ...)     only the store listing's and the app's own checks marked `docs` (--all: all)
#   the app's Swift (mac.app, mac.tests, mac.format outside mac.core), mac.projectSpec
#                                          xcodegen (fails if project.pbxproj was stale; --staged generates from the
#                                          index), swift-format lint, xcodebuild test, and the iOS tests (mac.iosScheme)
#                                          on a simulator of their own, left booted for the next run. With an iOS
#                                          scheme, a path under <mac.dir>/macOS asks for the macOS tests alone and one
#                                          under <mac.dir>/iOS for the iOS tests alone; every other path for both
#   mac.core, conformance/, the pin        swift test on the core package
#   mac.core sources and Conformance/      laching/tools/conformance.sh (fails if the vectors were stale)
#   strings (app Swift, .xcstrings, landing i18n, companion.json, the pin)   l10n.py check, the .resw and Android
#                                          strings exports are current; phrases.py check with a phrase bank
#   voices, voice config, catalog, phrases.json     voice.py check, the voices pointer is pushed
#   characters.design, its outputs         character.py --check (the art and character vectors match the drawings)
#   sounds.recipes, sounds.out/*.wav       synth.py --check (the sounds match their recipes)
#   windows/**/*.cs                        dotnet format whitespace
#   windows.core, windows.coreTests, conformance/, the shared settings (windows/Directory.{Build,Packages}.props,
#   global.json: a bump moves the Laching.* versions there)    dotnet test (windows.coreTests)
#   windows.app, windows.appTests          check-winui.py; dotnet test (windows.appTests, any OS); the app builds on
#                                          Windows, or on a Mac in the Parallels VM (windows/vm.sh build: the VM's own
#                                          platform, the app's Windows workflow builds both; skipped, not waited for,
#                                          while another run holds the VM); the tests
#                                          and the build also for the shared settings; dotnet runs in windows.dir,
#                                          so its global.json picks the SDK, as in CI, and a pin that isn't met fails
#   windows.assets (the icon, Assets/)     windows/assets.py --check (the MSIX images match the icon)
#   android.dir/**/*.kt(s)                 ktlint; ./gradlew :<android.core>:test for android.core, conformance/ and
#                                          the pin (JDK 17: JAVA_HOME, on a Mac Homebrew's openjdk@17 unless set;
#                                          ANDROID_HOME too)
#   landing/, phrases/settings/catalog vectors   <landing.tool> check and build
#   store.dir/*.md                         store/check.py (the listing's field limits)
#   always                                 the Laching pins agree (submodule tag = laching.tag = SwiftPM = XcodeGen; NuGet = each package's version in it);
#                                          while the app is linked to a Laching checkout (companion.py link) that's a
#                                          warning, and a failure with --staged and in CI;
#                                          companion.py conform (Laching's tools, workflows and skills, not copies of them)
#   verify.checks in companion.json        the app's own checks, each when its paths change (slow ones not with --fast)
#
# Prints one line per check and the tail of the log for failures. Exit 1 if anything failed.
# The slow checks that share no build folder or device run side by side: the macOS tests, the iOS tests, dotnet, the VM
# build, the Windows images, Gradle with the app's own slow checks, the landing build; those of one group in sequence.
# Each one's lines are held back and printed in the order above, so the output and the exit status are a serial run's.
# A run that regenerates the vectors does that (and the core package's tests) first, the others after: they read them.
# A slow check that ran and passed is noted in the Git folder (laching-slow-checks.log: when it started, what it ran),
# which is how the agent Stop hook knows what was run since an edit; a failed or skipped check leaves no note.
set -uo pipefail

TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
env_file="$(python3 "$TOOLS/companion.py" env)" || exit 2
eval "$env_file"
cd "$ROOT"

fast=0 all=0 mode=worktree base="" paths=() both_platforms=0 shutdown_simulator=0
novm=""; case "${LACHING_NO_VM:-0}" in '' | 0) ;; *) novm="LACHING_NO_VM=$LACHING_NO_VM" ;; esac
while [ $# -gt 0 ]; do
  case "$1" in
    --fast) fast=1 ;;
    --all) all=1 ;;
    --no-vm) novm=--no-vm ;;
    --both-platforms) both_platforms=1 ;;
    --shutdown-simulator) shutdown_simulator=1 ;;
    --serial) LACHING_VERIFY_SERIAL=1 ;;
    --staged) mode=staged ;;
    --base) [ $# -ge 2 ] || { echo "--base needs a Git ref" >&2; exit 2; }; mode=base; base="$2"; shift ;;
    --) shift; mode=paths; paths=("$@"); break ;;
    -h|--help) sed -n '2,/^set -/p' "$TOOLS/verify.sh" | sed '$d; s/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

. "$TOOLS/verify-lib.sh"
discover_changes "$ROOT" "$LANDING_DIR"

# Documentation beside code, vectors or resources doesn't affect the apps, so the app checks leave it out; the store
# listing's check and the app's own checks marked `docs` see every path. Text and data formats (.txt, .json, .xml, .svg) can be inputs.
app_changed="$(printf '%s\n' "$changed" | grep -Eiv '\.(md|markdown|rst|adoc)$' || true)"

# matches <paths> <extended regex>...: true when one of the paths matches one of them. With --all, true unless each is
# empty or $RE_NEVER (companion.py's pattern for a section the app doesn't have), so --all skips a missing section.
matches() {
  local list="$1" re live=()
  shift
  for re in "$@"; do [ -n "$re" ] && [ "$re" != "$RE_NEVER" ] && live+=("$re"); done
  [ ${#live[@]} -gt 0 ] || return 1
  [ "$all" = 1 ] && return 0
  local IFS='|'
  printf '%s\n' "$list" | grep -Eq "${live[*]}"
}
touched() { matches "$app_changed" "$@"; }    # the app checks: documentation left out
touched_any() { matches "$changed" "$@"; }    # every changed path

darwin=0; [ "$(uname)" = Darwin ] && darwin=1
windows=0; case "$(uname -s)" in MINGW* | MSYS* | CYGWIN*) windows=1 ;; esac
landing_tool="$(find_tool "$LANDING_TOOL" "$ROOT/$LANDING_DIR")" || landing_tool="$LANDING_TOOL"
# Gradle needs a JDK 17 and the Android SDK: on a Mac, Homebrew's openjdk@17 and Android Studio's SDK unless set.
if [ "$darwin" = 1 ]; then
  jdk=/opt/homebrew/opt/openjdk@17
  [ -n "${JAVA_HOME:-}" ] || [ ! -x "$jdk/bin/java" ] || export JAVA_HOME="$jdk"
  [ -n "${ANDROID_HOME:-}" ] || [ ! -d "$HOME/Library/Android/sdk" ] || export ANDROID_HOME="$HOME/Library/Android/sdk"
fi

xcodegen_in_sync() {
  [ "$mode" = staged ] && { xcodegen_staged_in_sync; return; }
  (cd "$MAC_DIR" && xcodegen generate --quiet) || return 1
  if ! git diff --quiet -- "$MAC_DIR/$MAC_PROJECT"; then
    echo "project.pbxproj was stale and has been regenerated; include it in your commit."
    git diff --stat -- "$MAC_DIR/$MAC_PROJECT"
    git diff -- "$MAC_DIR/$MAC_PROJECT" | grep '^+' | named_after_checkout
  fi
  return 0
}

# XcodeGen groups a source outside mac.dir (../voices/Voice) under a group named after the checkout's folder, so a
# worktree or a clone in another folder generates another project. Reads the lines the regeneration added.
named_after_checkout() {
  local folder; folder="$(basename "$ROOT")"
  if grep -qF -e "name = $folder;" -e "name = \"$folder\";"; then
    echo "A group is named after this checkout's folder ($folder): give each source outside $MAC_DIR (../…) a"
    echo "fixed group in $MAC_SPEC (\`group: <name>\`) and regenerate, so every checkout generates the same project."
  fi
}

# The pre-commit hook generates the project from the index, in a scratch copy, so what other sessions left untracked
# or unstaged in a shared checkout doesn't count. The copy, in a folder named as the checkout, holds the index's mac.dir
# and conformance.dir; every other entry of the root links to the checkout's, so a source outside mac.dir
# (../voices/Voice) still resolves.
xcodegen_staged_in_sync() {
  local copy="$logdir/staged/${ROOT##*/}" entry file stale=0
  mkdir -p "$copy"
  git ls-files -z -- "$MAC_DIR" ${CONFORMANCE_DIR:+"$CONFORMANCE_DIR"} |
    git checkout-index -z --stdin --prefix="$copy/" || return 1
  for entry in "$ROOT"/* "$ROOT"/.[!.]*; do
    [ -e "$entry" ] && [ ! -e "$copy/${entry##*/}" ] && ln -s "$entry" "$copy/${entry##*/}"
  done
  (cd "$copy/$MAC_DIR" && xcodegen generate --quiet) || return 1
  while IFS= read -r -d '' file; do
    git cat-file blob ":$file" | cmp -s - "$copy/$file" && continue
    echo "stale: $file"
    stale=1
    git cat-file blob ":$file" | diff - "$copy/$file" | grep '^>' | named_after_checkout
  done < <(git ls-files -z -- "$MAC_DIR/$MAC_PROJECT")
  [ "$stale" = 0 ] && return 0
  echo "The staged project doesn't match $MAC_SPEC and the staged sources: xcodegen generate in $MAC_DIR, and stage it."
  return 1
}

# The iOS tests on a simulator of their own (ios-destination.py --own), not the iPhone the Simulator panel and the
# screenshot runs share: a test host one of those left running jams the next run. It stays booted, so the next run
# starts sooner; --shutdown-simulator shuts it down after the run, and before it in case one was stopped.
ios_tests() {
  local dest status
  dest="$(python3 "$TOOLS/ios-destination.py" --own "$NAME verify")" || return 1
  [ "$shutdown_simulator" = 0 ] || xcrun simctl shutdown "${dest##*id=}" 2>/dev/null
  # shellcheck disable=SC2086
  (cd "$MAC_DIR" && xcodebuild -project "$MAC_PROJECT" -scheme "$MAC_IOS_SCHEME" -destination "$dest" \
    -derivedDataPath "$ROOT/${DERIVED%.noindex}-ios.noindex" -quiet $XCODE_CACHE test)
  status=$?
  [ "$shutdown_simulator" = 0 ] || xcrun simctl shutdown "${dest##*id=}" 2>/dev/null
  return $status
}

# Which Xcode tests a change asks for, into xcode_mac and xcode_ios. With an iOS scheme, a path under <mac.dir>/macOS
# is the Mac app's alone and one under <mac.dir>/iOS the iOS app's (the layout `swift-format --all` assumes too), so
# it asks for its own platform's tests; every other path (shared sources, the tests, the core, the project spec, the
# vectors) asks for both, and so does --all.
xcode_asks() { # <paths>: they ask for the Xcode tests, by the app's Swift paths or by its hooks.slow
  local list="${1%$'\n'}"
  [ -n "$list" ] || return 1
  matches "$(printf '%s\n' "$list" | grep -Eiv '\.(md|markdown|rst|adoc)$' || true)" "$RE_SWIFT" "${CONFORMANCE_DIR:+^$CONFORMANCE_DIR/}" && return 0
  case " $(printf '%s\n' "$list" | python3 "$TOOLS/companion.py" slow) " in *" xcodebuild-test "*) return 0 ;; esac
  return 1
}
xcode_split() {
  local path mac="" ios="" rest="" prefix="$MAC_DIR/"
  [ -n "$MAC_DIR" ] && [ "$MAC_DIR" != . ] || prefix=""
  xcode_mac=1 xcode_ios=0
  [ -n "$MAC_IOS_SCHEME" ] || return 0
  xcode_ios=1
  [ "$all" = 0 ] || return 0
  while IFS= read -r path; do
    case "$path" in
      "$prefix"macOS/*) mac="$mac$path"$'\n' ;;
      "$prefix"iOS/*) ios="$ios$path"$'\n' ;;
      *) rest="$rest$path"$'\n' ;;
    esac
  done <<< "$changed"
  xcode_asks "$rest" && return 0
  xcode_asks "$mac" || xcode_mac=0
  xcode_asks "$ios" || xcode_ios=0
  [ "$xcode_mac$xcode_ios" != 00 ] || xcode_mac=1 xcode_ios=1 # something asked for them: never neither
}

conformance_current() {
  local before after
  snapshot() { { git diff -- "$CONFORMANCE_DIR"; git ls-files --others --exclude-standard -- "$CONFORMANCE_DIR"; } | shasum; }
  before="$(snapshot)"
  "$TOOLS/conformance.sh" || return 1
  after="$(snapshot)"
  if [ "$before" != "$after" ]; then
    git status --short -- "$CONFORMANCE_DIR"
    echo "The vectors were stale and have been regenerated. Commit them with your change,"
    echo "then run the Windows Core tests to see what the port needs."
    return 1
  fi
  return 0
}

# The voices pointer this commit records (staged, or the work tree's) must already be on GitHub, or every other clone
# breaks: `voice.py publish` pushes before it stages. Offline: it asks the local remote-tracking refs.
voices_pointer_pushed() {
  local sha
  sha="$(git ls-files --stage "$VOICE_DIR" | awk '{print $2}')"
  [ -n "$sha" ] || return 0
  [ -e "$VOICE_DIR/.git" ] || { echo "$VOICE_DIR/ isn't checked out: git submodule update --init"; return 1; }
  subgit "$VOICE_DIR" fetch -q origin 2>/dev/null || true
  if [ -z "$(subgit "$VOICE_DIR" branch -r --contains "$sha" 2>/dev/null)" ]; then
    echo "$VOICE_DIR pointer ${sha:0:7} isn't pushed: run python3 $LACHING_PATH/tools/voice.py publish -m \"…\""
    return 1
  fi
}

# export_current <windows|android> <folder>: l10n.py export-<kind> into the logs matches the committed folder.
export_current() {
  local out="$logdir/$1" f rc=0 names=()
  python3 "$TOOLS/l10n.py" "export-$1" --out "$out" >/dev/null || return
  # Only the files the export writes: an Android res/ folder holds the app's drawables and themes beside them. A file of
  # one of those names that the export no longer writes (a dropped language) is stale.
  while IFS= read -r f; do
    if [ -f "$2/$f" ]; then diff --strip-trailing-cr "$out/$f" "$2/$f" || rc=1
    else echo "missing: $2/$f"; rc=1; fi
    names+=(-o -name "${f##*/}")
  done < <(cd "$out" && find . -type f | sed 's|^\./||')
  [ ${#names[@]} -gt 0 ] || return $rc
  while IFS= read -r f; do
    [ -f "$out/$f" ] || { echo "stale: $2/$f"; rc=1; }
  done < <(cd "$2" && find . -type f \( "${names[@]:1}" \) | sed 's|^\./||')
  return $rc
}

# Kotlin style (ktlint_official, the Android project's .editorconfig) of the changed Kotlin files, or all of them.
# Fix with: ktlint -F <files>
kotlin_lint() {
  if [ "$all" = 1 ]; then
    (cd "$ANDROID_DIR" && ktlint "**/*.kt" "**/*.kts" "!**/build/**")
    return
  fi
  local files
  changed_files "$RE_KOTLIN"
  [ ${#files[@]} -gt 0 ] || return 0
  ktlint "${files[@]}"
}

swift_format() {
  local config="$MAC_DIR/.swift-format"
  # Fix with: xcrun swift-format format -i --configuration <mac.dir>/.swift-format <files>
  if [ "$all" = 1 ]; then
    local dirs=()
    for d in "$MAC_APP" "$MAC_TESTS" "$MAC_DIR/Shared" "$MAC_DIR/macOS" "$MAC_DIR/iOS" "$MAC_CORE/Sources" "$MAC_CORE/Tests"; do [ -n "$d" ] && [ -d "$d" ] && dirs+=("$d"); done
    xcrun swift-format lint -r --strict --configuration "$config" "${dirs[@]}"
    return
  fi
  local files
  changed_files "^$MAC_DIR/.*\.swift$"
  [ ${#files[@]} -gt 0 ] || return 0
  xcrun swift-format lint --strict --configuration "$config" "${files[@]}"
}

# The WinUI traps (untyped XAML casts under Native AOT, XAML compiler crashes) in the changed app files, or all of them.
winui_check() {
  if [ "$all" = 1 ]; then
    python3 "$TOOLS/windows/check-winui.py"
    return
  fi
  local files
  changed_files "$RE_WINDOWS_APP" '\.(cs|xaml)$'
  [ ${#files[@]} -gt 0 ] || return 0
  python3 "$TOOLS/windows/check-winui.py" "${files[@]}"
}

# dotnet in windows.dir, its paths relative to it: the host picks the SDK from the global.json nearest the working
# directory, not the project's, so the app's windows/global.json counts only from there (a pin it can't meet fails).
windows_dotnet() {
  [ -d "$WINDOWS_DIR" ] || { echo "windows.dir ($WINDOWS_DIR) isn't a folder: check companion.json"; return 1; }
  (cd "$WINDOWS_DIR" && dotnet "$@")
}

# The workspace is windows.dir itself; --include is relative to it.
csharp_format() {
  local files
  changed_files "$RE_CSHARP"
  if [ "$all" = 1 ]; then
    windows_dotnet format whitespace . --folder --verify-no-changes
  elif [ ${#files[@]} -gt 0 ]; then
    windows_dotnet format whitespace . --folder --verify-no-changes --include "${files[@]#"$WINDOWS_DIR"/}"
  fi
}

# The agent Stop hook reminds about the slow checks not run since the last edit, and learns what ran from here, not from
# the command line (a --help, a grep of this file and a run that failed all name the script too): each slow check that
# ran and passed leaves a line in the Git folder, <its start, epoch seconds> TAB <the hook's kinds> TAB <its command>.
# A check that failed or was skipped leaves none, and nothing is noted outside a Git repository.
ran_log="$(git rev-parse --absolute-git-dir 2>/dev/null)" && ran_log="$ran_log/laching-slow-checks.log" || ran_log=""
ran_record() { # <started> <kinds> <command>
  [ -n "$ran_log" ] || return 0
  { printf '%s\t%s\t%s\n' "$1" "$2" "$(printf '%s' "$3" | tr '\n\t' '  ')" >>"$ran_log"; } 2>/dev/null || return 0
  if [ $(($(wc -l <"$ran_log"))) -gt 400 ]; then # the hook wants the newest line of each kind: the file stays short
    tail -n 200 "$ran_log" >"$ran_log.tmp" 2>/dev/null && mv -f "$ran_log.tmp" "$ran_log" 2>/dev/null
  fi
  return 0
}
# slow <kinds> <group> <label> [locked <name>] <command...>: a check queued for run_queued (verify-lib.sh), which runs
# the groups side by side and the checks of one group in sequence; slow_note notes it when it passed. No kind for a
# check the hook has no name for (the app's own): the hook reads its command.
slow_kinds=() slow_command=() xcode_checks=() xcode_command="" xcode_kind=""
slow() {
  local kinds="$1" i=${#queue_group[@]}; shift
  queue "$@"
  shift 2; if [ "${1:-}" = locked ]; then shift 2; fi
  slow_kinds[$i]="$kinds" slow_command[$i]="$*"
}
# slow_note <index>: run_queued calls it as each check is reported, in the order queued, so the notes are a serial
# run's. A check is noted with its own start. The Xcode test runs (xcode_checks) are one kind for the hook: noted when
# the last of them is reported and each passed, from the start of the first, as xcode_kind.
slow_note() {
  local i="$1" j last started=""
  if [ -n "${slow_command[$i]-}" ]; then
    [ "${queue_result[$i]}" != ok ] || ran_record "${queue_started[$i]}" "${slow_kinds[$i]-}" "${slow_command[$i]}"
    return 0
  fi
  [ ${#xcode_checks[@]} -gt 0 ] || return 0
  last="${xcode_checks[$((${#xcode_checks[@]} - 1))]}"
  [ "$i" = "$last" ] || return 0
  for j in "${xcode_checks[@]}"; do
    [ "${queue_result[$j]}" = ok ] || return 0
    case "${queue_started[$j]}" in '' | *[!0-9]*) return 0 ;; esac
    if [ -z "$started" ] || [ "${queue_started[$j]}" -lt "$started" ]; then started="${queue_started[$j]}"; fi
  done
  [ -z "$xcode_kind" ] || ran_record "$started" "$xcode_kind" "$xcode_command"
}
queue_after=slow_note

# The app's own checks (verify.checks): speed, whether documentation counts, paths, name and command per line. One that
# can't run here (a missing toolchain) exits 77: it's listed under `skip` with the last line it printed, and not noted.
# The slow ones run in sequence in Gradle's group, after the core module's tests and beside the other groups: one that
# builds in a folder another group's check uses must not be an app check yet.
app_speed=() app_docs=() app_paths=() app_name=() app_run=()
while IFS=$'	' read -r speed docs paths name run; do
  [ -n "$speed" ] || continue
  app_speed+=("$speed"); app_docs+=("$docs"); app_paths+=("$paths"); app_name+=("$name"); app_run+=("$run")
done <<< "$(python3 "$TOOLS/companion.py" checks)"
app_checks() {
  local i
  [ ${#app_speed[@]} -gt 0 ] || return 0
  for i in "${!app_speed[@]}"; do
    [ "${app_speed[$i]}" = "$1" ] || continue
    if { [ "${app_docs[$i]}" = docs ] && touched_any "${app_paths[$i]}"; } || touched "${app_paths[$i]}"; then
      verify_skip_status=77
      if [ "$1" = slow ]; then slow "" gradle "${app_name[$i]}" bash -c "${app_run[$i]}"
      else check "${app_name[$i]}" bash -c "${app_run[$i]}" </dev/null; fi
      verify_skip_status=""
    fi
  done
}

# --- always ------------------------------------------------------------------------------------

# While the app is linked to a Laching checkout (companion.py link: the trial of an unreleased Laching), the pins pass
# with a warning, shown under the check; with --staged (the pre-commit hook) and in CI the link fails them, so nothing
# linked is committed or released.
pins_agree() {
  local out status=0
  out="$(python3 "$TOOLS/companion.py" pins ${1:+"$1"} 2>&1)" || status=$?
  printf '%s\n' "$out"
  printf '%s\n' "$out" | sed -n 's/^warning: //p' >"$logdir/pins.warning"
  return "$status"
}
if [ "$mode" = staged ]; then check "Laching pins agree" pins_agree --staged
else check "Laching pins agree" pins_agree; fi
if [ -s "$logdir/pins.warning" ]; then sed 's/^/warn  /' "$logdir/pins.warning"; fi
check "Laching's tools, not copies (companion.py conform)" python3 "$TOOLS/companion.py" conform

# --- fast checks -------------------------------------------------------------------------------

if touched "$RE_SWIFT" "$RE_COREPKG"; then
  if [ -n "$MAC_SPEC" ]; then
    if [ "$darwin" = 1 ] && has xcodegen; then check "xcodegen (project.pbxproj in sync)" xcodegen_in_sync
    else skip "xcodegen: needs macOS with xcodegen"; fi
  fi
  if [ "$darwin" = 1 ] && [ -f "$MAC_DIR/.swift-format" ]; then check "swift-format lint" swift_format; fi
fi

if touched "$RE_STRINGS"; then
  check "l10n.py check" python3 "$TOOLS/l10n.py" check
fi
if touched "$RE_PHRASES"; then
  check "phrases.py check" python3 "$TOOLS/phrases.py" check
fi
if [ -n "$VOICE_DIR" ]; then
  # Pending lines are fine (the app never plays them); this fails only on a broken manifest, orphans or a missing
  # required clip of a shipping language, and prints the pending summary either way.
  if touched "^$VOICE_DIR\$"; then
    check "voices pointer pushed" voices_pointer_pushed
  fi
  if touched "$RE_VOICE"; then
    check "voice.py check" python3 "$TOOLS/voice.py" check
  fi
fi
# The generators' --check: their inputs, their outputs or the generator (a pin bump) changed.
if touched "$RE_CHARACTERS"; then
  check "character.py --check" python3 "$TOOLS/character.py" --check
fi
if touched "$RE_SOUNDS"; then
  check "synth.py --check" python3 "$TOOLS/synth.py" --check
fi
if [ -n "$WINDOWS_STRINGS" ] && [ -d "$WINDOWS_STRINGS" ] && touched "\.xcstrings$|^$WINDOWS_STRINGS/|^companion\.json$|$RE_PIN"; then
  check ".resw files match the catalog" export_current windows "$WINDOWS_STRINGS"
fi
if [ -n "$ANDROID_STRINGS" ] && [ -d "$ANDROID_STRINGS" ] && touched "\.xcstrings$|^$ANDROID_STRINGS/|^companion\.json$|$RE_PIN"; then
  check "Android strings match the catalog" export_current android "$ANDROID_STRINGS"
fi

if touched "$RE_KOTLIN"; then
  if has ktlint; then check "ktlint" kotlin_lint
  else skip "ktlint: brew install ktlint, or setup-linux.sh"; fi
fi

if touched "$RE_CSHARP"; then
  if has dotnet; then check "dotnet format whitespace" csharp_format
  else skip "dotnet format: needs the .NET SDK"; fi
fi

if touched "$RE_WINDOWS_APP"; then
  check "check-winui.py" winui_check
fi

if touched_any "$RE_STORE"; then
  if [ -f "$STORE_LISTING" ]; then check "store listing (store/check.py)" python3 "$TOOLS/store/check.py"
  else skip "store listing: no $STORE_LISTING yet"; fi
fi
if touched "$RE_LANDING" && [ -n "$LANDING_DIR" ] && [ -d "$ROOT/$LANDING_DIR" ]; then
  if [ -n "$landing_tool" ] && { has "$landing_tool" || [ -x "$landing_tool" ]; }; then
    check "landing: $LANDING_TOOL check" bash -c "cd \"$LANDING_DIR\" && \"$landing_tool\" check"
  else skip "landing: needs $LANDING_TOOL"; fi
fi

app_checks fast

# --- slow checks -------------------------------------------------------------------------------

# asked <kind>: true when the app's hooks.slow gives that check of the Stop hook's own a changed path. The hook reminds
# of the check for those paths and names this script as what to run, so each runs for them as well as for its own
# paths. companion.py matches them, as the hook does: they're Python regular expressions (a lookahead isn't grep -E's).
asked() { case " $slow_asked " in *" $1 "*) return 0 ;; esac; return 1; }

if [ "$fast" = 0 ]; then
  slow_asked="$(printf '%s\n' "$changed" | python3 "$TOOLS/companion.py" slow)" || exit 2
  if [ -n "$MAC_CORE" ] && { touched "$RE_COREPKG" "${CONFORMANCE_DIR:+^$CONFORMANCE_DIR/}" "$RE_PIN" || asked swift-test; }; then
    if has swift; then slow swift-test core "swift test ($MAC_CORE)" swift test --package-path "$MAC_CORE"
    else skip "swift test ($MAC_CORE): needs a Swift toolchain"; fi
  fi
  if touched "$RE_PURE" || asked conformance; then
    if [ "$darwin" = 1 ]; then
      slow conformance core "conformance vectors current" conformance_current
      # The regeneration empties the vectors' folders and writes each file again, and every check below reads them
      # (the Xcode, dotnet and Gradle tests, the landing build, the copy into the VM): it ends before they start.
      run_queued
    else skip "conformance.sh: needs macOS + Xcode"; fi
  fi
  if touched "$RE_SWIFT" "${CONFORMANCE_DIR:+^$CONFORMANCE_DIR/}" || asked xcodebuild-test; then
    if [ "$darwin" = 1 ]; then
      # One kind for the hook (slow_note): noted when each of the test runs these paths ask for passed. Their build
      # folders and devices are their own (the Mac's DerivedData; the iOS one and its simulator), so they run side by side.
      xcode_split
      xcode_schemes=""
      if [ "$xcode_mac" = 1 ]; then
        xcode_checks+=("${#queue_group[@]}") xcode_schemes="$MAC_SCHEME"
        queue xcode-macos "xcodebuild test" locked xcodebuild-macos bash -c "cd \"$MAC_DIR\" && xcodebuild -project \"$MAC_PROJECT\" -scheme \"$MAC_SCHEME\" \
          -destination 'platform=macOS' -derivedDataPath \"$ROOT/$DERIVED\" -quiet $XCODE_CACHE test"
      fi
      if [ "$xcode_ios" = 1 ]; then
        xcode_checks+=("${#queue_group[@]}") xcode_schemes="${xcode_schemes:+$xcode_schemes and }$MAC_IOS_SCHEME"
        queue xcode-ios "xcodebuild test (iOS)" locked xcodebuild-ios ios_tests
      fi
      xcode_command="cd $MAC_DIR && xcodebuild -project $MAC_PROJECT -scheme $xcode_schemes test"
      # One scheme of two covers only its own folder's paths: noted as <kind>@<folder>/ (the hook's reading), so a
      # shared file or the other scheme's still asks for a run. A folder with a space in it isn't noted.
      xcode_kind=xcodebuild-test
      if [ -n "$MAC_IOS_SCHEME" ] && [ "$xcode_mac$xcode_ios" != 11 ]; then
        xcode_prefix="$MAC_DIR/"
        [ -n "$MAC_DIR" ] && [ "$MAC_DIR" != . ] || xcode_prefix=""
        if [ "$xcode_mac" = 1 ]; then xcode_kind="xcodebuild-test@${xcode_prefix}macOS/"
        else xcode_kind="xcodebuild-test@${xcode_prefix}iOS/"; fi
        case "$xcode_kind" in *[[:space:]]*) xcode_kind="" ;; esac
      fi
    else skip "xcodebuild test: needs macOS + Xcode"; fi
  fi
  if { touched "$RE_WINDOWS_CORE" || asked dotnet-test; } && [ -n "$WINDOWS_CORE_TESTS" ]; then
    if has dotnet; then
      slow dotnet-test dotnet "dotnet test ($WINDOWS_CORE_TESTS)" windows_dotnet test "$WINDOWS_CORE_TESTS/$WINDOWS_CORE_TESTS.csproj" -v q --nologo
    else skip "dotnet test: needs the .NET SDK"; fi
  fi
  if { touched "$RE_WINDOWS_APP_TESTS" || asked dotnet-app-test; } && [ -n "$WINDOWS_APP_TESTS" ]; then
    if has dotnet; then
      slow dotnet-app-test dotnet "dotnet test ($WINDOWS_APP_TESTS)" windows_dotnet test "$WINDOWS_APP_TESTS/$WINDOWS_APP_TESTS.csproj" -v q --nologo
    else skip "dotnet test ($WINDOWS_APP_TESTS): needs the .NET SDK"; fi
  fi
  if touched "$RE_WINDOWS_BUILD" || asked winui-build; then
    if [ "$windows" = 1 ] && has dotnet; then
      # Debug, unpackaged, for this machine's architecture, named for a project without companion.targets.
      case "${PROCESSOR_ARCHITECTURE:-}" in ARM64) platform=ARM64 ;; *) platform=x64 ;; esac
      slow winui-build dotnet "WinUI app builds ($WINDOWS_APP)" windows_dotnet build "$WINDOWS_APP/$WINDOWS_APP.csproj" \
        -p:Platform=$platform -v q --nologo
    elif [ -n "$novm" ]; then
      skip "WinUI app: builds on Windows or in the Parallels VM (left alone: $novm); push the branch and run the Windows workflow"
    else
      # On a Mac with the Parallels Windows VM: this working tree, Debug, for the VM's own platform (a compile error
      # shows on either, and the app's Windows workflow builds both); --both-platforms builds x64 and ARM64. The copy
      # in the VM is its own, so it builds beside dotnet on the Mac. A VM another run holds is skipped, not waited for.
      vm="$("$TOOLS/windows/vm.sh" status 2>&1)"
      case $? in
        0)
          if [ "$both_platforms" = 1 ]; then
            slow winui-build vm "WinUI app builds in the VM ($WINDOWS_APP, x64 + ARM64)" "$TOOLS/windows/vm.sh" build x64 ARM64
          else slow winui-build vm "WinUI app builds in the VM ($WINDOWS_APP)" "$TOOLS/windows/vm.sh" build; fi ;;
        4) vm="${vm#vm.sh: }"
          skip "WinUI app build skipped: the VM is busy (${vm#*is busy with }); run this again when it's free, or push the branch and run the Windows workflow" ;;
        *) skip "WinUI app: builds on Windows or in the Parallels VM (${vm#vm.sh: }); push the branch and run the Windows workflow" ;;
      esac
    fi
  fi
  if touched "$RE_WINDOWS_ASSETS"; then
    slow "" assets "Windows images match the icon (windows/assets.py)" python3 "$TOOLS/windows/assets.py" --check
  fi
  if { touched "$RE_ANDROID_CORE" || asked gradle-core-test; } && [ -n "$ANDROID_DIR" ]; then
    # The pure Kotlin module replays the vectors; the app module needs the Android SDK, so it builds in kotlin.yml.
    if [ -x "$ANDROID_DIR/gradlew" ] && { has java || [ -n "${JAVA_HOME:-}" ]; }; then
      slow gradle-core-test gradle "gradlew :$ANDROID_CORE:test" bash -c "cd \"$ANDROID_DIR\" && ./gradlew :$ANDROID_CORE:test -q --console=plain"
    else skip "gradlew :$ANDROID_CORE:test: needs $ANDROID_DIR/gradlew and a JDK 17 (setup-linux.sh)"; fi
  fi
  if { touched "$RE_LANDING" || asked landing-build; } && [ -n "$LANDING_DIR" ] && [ -d "$ROOT/$LANDING_DIR" ] && [ -n "$landing_tool" ] &&
    { has "$landing_tool" || [ -x "$landing_tool" ]; }; then
    slow landing-build landing "landing: $LANDING_TOOL build" bash -c "cd \"$LANDING_DIR\" && \"$landing_tool\" build"
  fi
  app_checks slow
  run_queued
fi

summary
finish
