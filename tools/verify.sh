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
#
# Path → checks:
#   documentation (*.md, *.rst, ...)     only the store listing's and the app's own checks (--all runs every check)
#   the app's Swift (mac.app, mac.tests, mac.format outside mac.core), mac.projectSpec
#                                          xcodegen (fails if project.pbxproj was stale; --staged generates from the
#                                          index), swift-format lint, xcodebuild test, and the iOS tests (mac.iosScheme)
#                                          on a simulator of their own
#   mac.core, conformance/, the pin        swift test on the core package
#   mac.core sources and Conformance/      laching/tools/conformance.sh (fails if the vectors were stale)
#   strings (app Swift, .xcstrings, landing i18n, companion.json, the pin)   l10n.py check, the .resw and Android
#                                          strings exports are current; phrases.py check with a phrase bank
#   voices, voice config, catalog, phrases.json     voice.py check, the voices pointer is pushed
#   characters.design, its outputs         character.py --check (the art and character vectors match the drawings)
#   sounds.recipes, sounds.out/*.wav       synth.py --check (the sounds match their recipes)
#   windows/**/*.cs                        dotnet format whitespace, dotnet test (windows.coreTests)
#   windows.app, windows.appTests          check-winui.py; dotnet test (windows.appTests, any OS); the app builds on
#                                          Windows, or on a Mac in the Parallels VM (windows/vm.sh build)
#   windows.assets (the icon, Assets/)     windows/assets.py --check (the MSIX images match the icon)
#   android.dir/**/*.kt(s)                 ktlint; ./gradlew :<android.core>:test for android.core and conformance/
#                                          (JDK 17: JAVA_HOME, on a Mac Homebrew's openjdk@17 unless set; ANDROID_HOME too)
#   landing/, phrases/settings/catalog vectors   <landing.tool> check and build
#   store.dir/*.md                         store/check.py (the listing's field limits)
#   always                                 the Laching pins agree (submodule tag = laching.tag = SwiftPM = XcodeGen; NuGet = each package's version in it);
#                                          companion.py conform (Laching's tools, workflows and skills, not copies of them)
#   verify.checks in companion.json        the app's own checks, each when its paths change (slow ones not with --fast)
#
# Prints one line per check and the tail of the log for failures. Exit 1 if anything failed.
set -uo pipefail

TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
env_file="$(python3 "$TOOLS/companion.py" env)" || exit 2
eval "$env_file"
cd "$ROOT"

fast=0 all=0 mode=worktree base="" paths=()
novm=""; case "${LACHING_NO_VM:-0}" in '' | 0) ;; *) novm="LACHING_NO_VM=$LACHING_NO_VM" ;; esac
while [ $# -gt 0 ]; do
  case "$1" in
    --fast) fast=1 ;;
    --all) all=1 ;;
    --no-vm) novm=--no-vm ;;
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
# listing's and the app's own checks see every path. Text and data formats (.txt, .json, .xml, .svg) can be inputs.
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
# screenshot runs share: a test host one of those left running jams the next run. Shut down after each run, and
# before it in case one was stopped.
ios_tests() {
  local dest status
  dest="$(python3 "$TOOLS/ios-destination.py" --own "$NAME verify")" || return 1
  xcrun simctl shutdown "${dest##*id=}" 2>/dev/null
  # shellcheck disable=SC2086
  (cd "$MAC_DIR" && xcodebuild -project "$MAC_PROJECT" -scheme "$MAC_IOS_SCHEME" -destination "$dest" \
    -derivedDataPath "$ROOT/${DERIVED%.noindex}-ios.noindex" -quiet $XCODE_CACHE test)
  status=$?
  xcrun simctl shutdown "${dest##*id=}" 2>/dev/null
  return $status
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
  python3 "$TOOLS/l10n.py" "export-$1" --out "$logdir/$1" >/dev/null &&
    diff -r --strip-trailing-cr "$logdir/$1" "$2"
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

csharp_format() {
  local files
  changed_files "$RE_CSHARP"
  if [ "$all" = 1 ]; then
    dotnet format whitespace "$WINDOWS_DIR" --folder --verify-no-changes
  elif [ ${#files[@]} -gt 0 ]; then
    dotnet format whitespace "$WINDOWS_DIR" --folder --verify-no-changes --include "${files[@]#"$WINDOWS_DIR"/}"
  fi
}

# The app's own checks (verify.checks): speed, paths, name and command per line.
app_speed=() app_paths=() app_name=() app_run=()
while IFS=$'	' read -r speed paths name run; do
  [ -n "$speed" ] || continue
  app_speed+=("$speed"); app_paths+=("$paths"); app_name+=("$name"); app_run+=("$run")
done <<< "$(python3 "$TOOLS/companion.py" checks)"
app_checks() {
  local i
  [ ${#app_speed[@]} -gt 0 ] || return 0
  for i in "${!app_speed[@]}"; do
    if [ "${app_speed[$i]}" = "$1" ] && touched_any "${app_paths[$i]}"; then
      check "${app_name[$i]}" bash -c "${app_run[$i]}" </dev/null
    fi
  done
}

# --- always ------------------------------------------------------------------------------------

check "Laching pins agree" python3 "$TOOLS/companion.py" pins
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
if [ -n "$WINDOWS_STRINGS" ] && [ -d "$WINDOWS_STRINGS" ] && touched "\.xcstrings$|^$WINDOWS_STRINGS/"; then
  check ".resw files match the catalog" export_current windows "$WINDOWS_STRINGS"
fi
if [ -n "$ANDROID_STRINGS" ] && [ -d "$ANDROID_STRINGS" ] && touched "\.xcstrings$|^$ANDROID_STRINGS/"; then
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

if [ "$fast" = 0 ]; then
  if [ -n "$MAC_CORE" ] && touched "$RE_COREPKG" "${CONFORMANCE_DIR:+^$CONFORMANCE_DIR/}" "$RE_PIN"; then
    if has swift; then check "swift test ($MAC_CORE)" swift test --package-path "$MAC_CORE"
    else skip "swift test ($MAC_CORE): needs a Swift toolchain"; fi
  fi
  if touched "$RE_PURE"; then
    if [ "$darwin" = 1 ]; then check "conformance vectors current" conformance_current
    else skip "conformance.sh: needs macOS + Xcode"; fi
  fi
  if touched "$RE_SWIFT" "${CONFORMANCE_DIR:+^$CONFORMANCE_DIR/}"; then
    if [ "$darwin" = 1 ]; then
      check "xcodebuild test" locked xcodebuild-macos bash -c "cd \"$MAC_DIR\" && xcodebuild -project \"$MAC_PROJECT\" -scheme \"$MAC_SCHEME\" \
        -destination 'platform=macOS' -derivedDataPath \"$ROOT/$DERIVED\" -quiet $XCODE_CACHE test"
      if [ -n "$MAC_IOS_SCHEME" ]; then check "xcodebuild test (iOS)" locked xcodebuild-ios ios_tests; fi
    else skip "xcodebuild test: needs macOS + Xcode"; fi
  fi
  if touched "$RE_WINDOWS_CORE" && [ -n "$WINDOWS_CORE_TESTS" ]; then
    if has dotnet; then
      check "dotnet test ($WINDOWS_CORE_TESTS)" dotnet test "$WINDOWS_DIR/$WINDOWS_CORE_TESTS/$WINDOWS_CORE_TESTS.csproj" -v q --nologo
    else skip "dotnet test: needs the .NET SDK"; fi
  fi
  if touched "$RE_WINDOWS_APP_TESTS" && [ -n "$WINDOWS_APP_TESTS" ]; then
    if has dotnet; then
      check "dotnet test ($WINDOWS_APP_TESTS)" dotnet test "$WINDOWS_DIR/$WINDOWS_APP_TESTS/$WINDOWS_APP_TESTS.csproj" -v q --nologo
    else skip "dotnet test ($WINDOWS_APP_TESTS): needs the .NET SDK"; fi
  fi
  if touched "$RE_WINDOWS_APP"; then
    vm="left alone: $novm"
    if [ "$windows" = 1 ] && has dotnet; then
      # Debug, unpackaged, for this machine's architecture, named for a project without companion.targets.
      case "${PROCESSOR_ARCHITECTURE:-}" in ARM64) platform=ARM64 ;; *) platform=x64 ;; esac
      check "WinUI app builds ($WINDOWS_APP)" dotnet build "$WINDOWS_DIR/$WINDOWS_APP/$WINDOWS_APP.csproj" \
        -p:Platform=$platform -v q --nologo
    elif [ -z "$novm" ] && vm="$("$TOOLS/windows/vm.sh" status 2>&1)"; then
      # On a Mac with the Parallels Windows VM: this working tree, Debug, x64 and ARM64.
      check "WinUI app builds in the VM ($WINDOWS_APP, x64 + ARM64)" "$TOOLS/windows/vm.sh" build
    else skip "WinUI app: builds on Windows or in the Parallels VM (${vm#vm.sh: }); push the branch and run the Windows workflow"; fi
  fi
  if touched "$RE_WINDOWS_ASSETS"; then
    check "Windows images match the icon (windows/assets.py)" python3 "$TOOLS/windows/assets.py" --check
  fi
  if touched "$RE_ANDROID_CORE" && [ -n "$ANDROID_DIR" ]; then
    # The pure Kotlin module replays the vectors; the app module needs the Android SDK, so it builds in kotlin.yml.
    if [ -x "$ANDROID_DIR/gradlew" ] && { has java || [ -n "${JAVA_HOME:-}" ]; }; then
      check "gradlew :$ANDROID_CORE:test" bash -c "cd \"$ANDROID_DIR\" && ./gradlew :$ANDROID_CORE:test -q --console=plain"
    else skip "gradlew :$ANDROID_CORE:test: needs $ANDROID_DIR/gradlew and a JDK 17 (setup-linux.sh)"; fi
  fi
  if touched "$RE_LANDING" && [ -n "$LANDING_DIR" ] && [ -d "$ROOT/$LANDING_DIR" ] && [ -n "$landing_tool" ] &&
    { has "$landing_tool" || [ -x "$landing_tool" ]; }; then
    check "landing: $LANDING_TOOL build" bash -c "cd \"$LANDING_DIR\" && \"$landing_tool\" build"
  fi
  app_checks slow
fi

summary
finish
