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
#
# Path → checks:
#   mac.app/tests Swift, mac.projectSpec   xcodegen (fails if project.pbxproj was stale), swift-format lint, xcodebuild test
#   mac.core                               swift test on the core package
#   mac.core sources and Conformance/      laching/tools/conformance.sh (fails if the vectors were stale)
#   strings (app Swift, .xcstrings, landing i18n)   l10n.py check, phrases.py check, the .resw and Android strings exports are current
#   voices, voice config, catalog, phrases.json     voice.py check, the voices pointer is pushed
#   characters.design, its outputs         character.py --check (the art and character vectors match the drawings)
#   sounds.recipes, sounds.out/*.wav       synth.py --check (the sounds match their recipes)
#   windows/**/*.cs                        dotnet format whitespace, dotnet test (windows.coreTests)
#   windows.app, windows.appTests          check-winui.py; dotnet test (windows.appTests, any OS); the app builds on
#                                          Windows, or on a Mac in the Parallels VM (windows/vm.sh build)
#   windows.assets (the icon, Assets/)     windows/assets.py --check (the MSIX images match the icon)
#   android.dir/**/*.kt(s)                 ktlint; ./gradlew :<android.core>:test for android.core and conformance/
#   landing/, phrases/settings vectors     <landing.tool> check and build
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
while [ $# -gt 0 ]; do
  case "$1" in
    --fast) fast=1 ;;
    --all) all=1 ;;
    --staged) mode=staged ;;
    --base) mode=base; base="$2"; shift ;;
    --) shift; mode=paths; paths=("$@"); break ;;
    -h|--help) sed -n '2,31p' "$TOOLS/verify.sh" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

case "$mode" in
  worktree) changed="$(git diff --name-only HEAD 2>/dev/null; git ls-files --others --exclude-standard 2>/dev/null)" ;;
  staged) changed="$(git diff --cached --name-only)" ;;
  base) changed="$(git diff --name-only "$base"...HEAD; git diff --name-only HEAD; git ls-files --others --exclude-standard)" ;;
  paths) changed="$(printf '%s\n' "${paths[@]}" | sed "s|^$ROOT/||")" ;;
esac
# subgit <dir> <args…>: git in a submodule. A pre-commit hook exports GIT_DIR, GIT_INDEX_FILE… for the app's
# repository, and they win over -C (GIT_LOCAL_ENV, from companion.py, lists them).
subgit() { local dir="$1"; shift; ( unset $GIT_LOCAL_ENV; git -C "$dir" "$@" ); }

# A landing page kept in its own repository is a submodule: the parent only reports its path, so list the files
# changed inside it since the commit the parent records (commits not recorded yet, and uncommitted work).
if [ "$mode" != paths ] && [ -n "$LANDING_DIR" ] && [ -e "$LANDING_DIR/.git" ] &&
  printf '%s\n' "$changed" | grep -qxF "$LANDING_DIR"; then
  sub() { subgit "$LANDING_DIR" "$@"; }
  recorded="$(git rev-parse -q --verify "${base:-HEAD}:$LANDING_DIR" 2>/dev/null || git rev-parse -q --verify "HEAD:$LANDING_DIR")"
  changed="$changed
$({ sub diff --name-only "$recorded" 2>/dev/null; sub ls-files --others --exclude-standard; } | sed "s|^|$LANDING_DIR/|")"
fi
changed="$(printf '%s\n' "$changed" | sed '/^$/d' | sort -u)"

# touched <extended regex>...: true when a changed path matches one of them. With --all, true unless each is empty or
# $RE_NEVER (companion.py's pattern for a section the app doesn't have), so --all skips the checks of a missing section.
touched() {
  local re live=()
  for re in "$@"; do [ -n "$re" ] && [ "$re" != "$RE_NEVER" ] && live+=("$re"); done
  [ ${#live[@]} -gt 0 ] || return 1
  [ "$all" = 1 ] && return 0
  local IFS='|'
  printf '%s\n' "$changed" | grep -Eq "${live[*]}"
}

darwin=0; [ "$(uname)" = Darwin ] && darwin=1
windows=0; case "$(uname -s)" in MINGW* | MSYS* | CYGWIN*) windows=1 ;; esac
# shellcheck source=verify-lib.sh
. "$TOOLS/verify-lib.sh"
# The landing tool from PATH, or the one its node_modules brings (CI, cloud containers: setup-linux.sh).
landing_tool="$LANDING_TOOL"
if [ -n "$landing_tool" ] && ! has "$landing_tool" && [ -x "$ROOT/$LANDING_DIR/node_modules/.bin/$landing_tool" ]; then
  landing_tool="$ROOT/$LANDING_DIR/node_modules/.bin/$landing_tool"
fi
xcodegen_in_sync() {
  (cd "$MAC_DIR" && xcodegen generate --quiet) || return 1
  if ! git diff --quiet -- "$MAC_DIR/$MAC_PROJECT"; then
    echo "project.pbxproj was stale and has been regenerated; include it in your commit."
    git diff --stat -- "$MAC_DIR/$MAC_PROJECT"
    # XcodeGen groups a source outside mac.dir (../voices/Voice) under a group named after the checkout's folder, so
    # a worktree or a clone in another folder generates another project.
    local folder; folder="$(basename "$ROOT")"
    if git diff -- "$MAC_DIR/$MAC_PROJECT" | grep '^+' | grep -qF -e "name = $folder;" -e "name = \"$folder\";"; then
      echo "A group is named after this checkout's folder ($folder): give each source outside $MAC_DIR (../…) a"
      echo "fixed group in $MAC_SPEC (\`group: <name>\`) and regenerate, so every checkout generates the same project."
    fi
    [ "$mode" = staged ] && return 1
  fi
  return 0
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
    if [ "${app_speed[$i]}" = "$1" ] && touched "${app_paths[$i]}"; then
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

if touched "$RE_STORE"; then
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
  if touched "$RE_COREPKG"; then
    if has swift; then check "swift test ($MAC_CORE)" swift test --package-path "$MAC_CORE"
    else skip "swift test ($MAC_CORE): needs a Swift toolchain"; fi
  fi
  if touched "$RE_PURE"; then
    if [ "$darwin" = 1 ]; then check "conformance vectors current" conformance_current
    else skip "conformance.sh: needs macOS + Xcode"; fi
  fi
  if touched "$RE_SWIFT" "${CONFORMANCE_DIR:+^$CONFORMANCE_DIR/}"; then
    if [ "$darwin" = 1 ]; then
      check "xcodebuild test" bash -c "cd \"$MAC_DIR\" && xcodebuild -project \"$MAC_PROJECT\" -scheme \"$MAC_SCHEME\" \
        -destination 'platform=macOS' -derivedDataPath \"$ROOT/$DERIVED\" -quiet $XCODE_CACHE test"
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
    if [ "$windows" = 1 ] && has dotnet; then
      # Debug, unpackaged, for this machine's architecture (companion.targets picks it when no platform is named).
      check "WinUI app builds ($WINDOWS_APP)" dotnet build "$WINDOWS_DIR/$WINDOWS_APP/$WINDOWS_APP.csproj" -v q --nologo
    elif vm="$("$TOOLS/windows/vm.sh" status 2>&1)"; then
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
