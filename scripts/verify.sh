#!/usr/bin/env bash
# Runs the checks that apply to a set of changed paths.
#
#   scripts/verify.sh                    # working-tree changes vs HEAD, plus untracked files
#   scripts/verify.sh --staged           # staged files
#   scripts/verify.sh --all              # every check, regardless of what changed
#   scripts/verify.sh --fast ...         # only the checks that take seconds (no swift test, dotnet test, gradlew)
#   scripts/verify.sh --version X.Y.Z    # only release.yml's gate: the version strings are X.Y.Z
#
# Path → checks:
#   Package.swift, Sources/, Tests/     swift-format lint, swift build + test (macOS), vectors current
#   dotnet/**, conformance/**           dotnet format whitespace, dotnet build + test
#   kotlin/**                           ktlint (the version kotlin.yml pins)
#   kotlin/**, conformance/**           ./gradlew :core:test :testing:test on a JDK 17 (JAVA_HOME, else Homebrew's
#                                       openjdk@17, else the java on PATH)
#   tools/**, Tests/Fixtures/**,        the tools' Python tests (with --fast, the ones that cover the changed paths:
#   scripts/{release,verify,            tools/tests/covering.py), fixture art current
#   fixture-art}.sh, release.yml
#   tools/landing/**                    @laching/landing: types, lint, format, tests (Node 24)
#   web/**                              dotnet format (web's solution), its tests (the Jobs tests only with PostgreSQL:
#                                       LACHING_WEB_TEST_POSTGRES, or localhost:5432), the Tailwind sample
#   version sources, site/**, README.md  every version string agrees, LachingVersion at or below it (always)
set -uo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

fast=0 all=0 mode=worktree version=
while [ $# -gt 0 ]; do
  case "$1" in
    --fast) fast=1 ;;
    --all) all=1 ;;
    --staged) mode=staged ;;
    --version) [ $# -ge 2 ] || { echo "--version needs X.Y.Z (see --help)" >&2; exit 2; }; version="$2"; shift ;;
    -h|--help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done
. tools/verify-lib.sh
base="" paths=()
discover_changes "$root"
touched() { [ "$all" = 1 ] || printf '%s\n' "$changed" | grep -Eq "$1"; }
darwin=0; [ "$(uname)" = Darwin ] && darwin=1
swift='^Package\.swift$|^Sources/|^Tests/|^\.swift-format$'
dotnet='^dotnet/|^conformance/|^Tests/LachingCompanionTests/Resources/|^Tests/LachingShellTests/Resources/'
kotlin='^kotlin/'
# release.sh, verify.sh and release.yml are what test_release runs; fixture-art.sh is what the fixture art check runs.
tools='^tools/|^Tests/Fixtures/|^scripts/(release|verify|fixture-art)\.sh$|^\.github/workflows/release\.yml$'
web='^web/'
tailwind='^web/(src/Laching\.Web\.Tailwind/|tests/Laching\.Web\.Tailwind\.Sample/|scripts/tailwind-sample\.sh$)'

# True when the Jobs tests' PostgreSQL answers (LACHING_WEB_TEST_POSTGRES's host and port, else localhost:5432).
web_postgres() {
  local server="${LACHING_WEB_TEST_POSTGRES:-}" host port
  host="$(printf '%s' "$server" | sed -n 's/.*Host=\([^;]*\).*/\1/p')"
  port="$(printf '%s' "$server" | sed -n 's/.*Port=\([0-9]*\).*/\1/p')"
  (exec 3<>"/dev/tcp/${host:-localhost}/${port:-5432}") 2>/dev/null
}

vectors_current() {
  scripts/conformance.sh || return 1
  if ! git diff --quiet -- conformance || [ -n "$(git ls-files --others --exclude-standard -- conformance)" ]; then
    git status --short -- conformance
    echo "The vectors were stale and have been regenerated. Commit them with your change."
    return 1
  fi
}

# Every tools test, or with --fast only the ones that cover the changed paths (tools/tests/covering.py), deleted ones too.
tools_tests() {
  local paths=() path tests
  if [ "$fast" = 1 ] && [ "$all" = 0 ]; then
    while read -r path; do [ -n "$path" ] && paths+=("$path"); done <<< "$(printf '%s\n' "$changed" | grep -E "$tools")"
    tests="$(python3 tools/tests/run.py --covering "${paths[@]+"${paths[@]}"}")" || tests=all
  fi
  if [ "${tests:-all}" = all ]; then check "tools tests" python3 tools/tests/run.py
  else
    # shellcheck disable=SC2086 # the test names, one word each
    check "tools tests ($tests)" python3 tools/tests/run.py $tests
  fi
}

# kotlin.yml's ktlint step: the same files (the version is checked before).
kotlin_lint() { (cd kotlin && ktlint "**/*.kt" "**/*.kts" "!**/build/**"); }

# Gradle on a JDK 17, the toolchain :core and :testing ask for (kotlin/build.gradle.kts). jdk17 prints the first home
# that is one: JAVA_HOME, Homebrew's openjdk@17 (keg-only, so nothing else finds it), then "" for the java on PATH.
jdk17() {
  local home
  for home in "${JAVA_HOME:-}" /opt/homebrew/opt/openjdk@17 ""; do
    if "${home:+$home/bin/}java" -version 2>&1 | grep -q 'version "17[."]'; then printf '%s' "$home"; return 0; fi
  done
  return 1
}
kotlin_tests() { (cd kotlin && JAVA_HOME="$1" ./gradlew :core:test :testing:test --console=plain); }

landing_check() {
  (
    cd tools/landing || exit 1
    [ -d node_modules ] || npm ci --silent || exit 1
    npx tsc --noEmit && npx vp check && npx vp test run
  )
}

# With an argument, the agreed version must also be that one (release.yml checks the release's against it).
versions_agree() {
  local expected="${1:-}" found distinct nuget web
  found="$( {
    sed -n 's/.*public static let version = "\([^"]*\)".*/\1/p' Sources/LachingCore/Laching.swift
    sed -n 's/^lachingVersion=\(.*\)$/\1/p' kotlin/gradle.properties
    grep -oE 'class="version"><a [^>]*>v[0-9]+\.[0-9]+\.[0-9]+|exact: (<span class="s">)?"[0-9]+\.[0-9]+\.[0-9]+"' site/index.html |
      grep -oE '[0-9]+\.[0-9]+\.[0-9]+'
    grep -oE 'exact: "[0-9]+\.[0-9]+\.[0-9]+"|latest release is `v[0-9]+\.[0-9]+\.[0-9]+' README.md |
      grep -oE '[0-9]+\.[0-9]+\.[0-9]+'
  } )"
  # Seven: Laching.swift, kotlin/gradle.properties, the site's version label and two install snippets, and the README's
  # install pin and latest-release line (LachingInfo.Version and Laching.VERSION are generated at build time).
  if [ "$(printf '%s\n' "$found" | grep -c .)" -ne 7 ]; then
    echo "expected 7 version strings, found:"; printf '%s\n' "$found" | sed 's/^/  /'; return 1
  fi
  distinct="$(printf '%s\n' "$found" | sort -u)"
  if [ "$(printf '%s\n' "$distinct" | wc -l)" -ne 1 ]; then
    echo "version strings disagree (Laching.swift, kotlin/gradle.properties, site/index.html, README.md):"
    printf '%s\n' "$found" | sort | uniq -c | sed 's/^/  /'; return 1
  fi
  if [ -n "$expected" ] && [ "$distinct" != "$expected" ]; then
    echo "the version strings say $distinct, not $expected (Laching.swift, kotlin/gradle.properties, site/index.html," \
      "README.md)"
    return 1
  fi
  # LachingVersion: the NuGet packages' version, which trails the tag when they didn't change but is never ahead of it.
  nuget="$(sed -n 's/.*<LachingVersion[^>]*>\([^<]*\)<.*/\1/p' dotnet/Directory.Build.props)"
  if [ -z "$nuget" ] || [ "$(printf '%s\n' "$nuget" "$distinct" | sort -t. -k1,1n -k2,2n -k3,3n | tail -n 1)" != "$distinct" ]; then
    echo "dotnet/Directory.Build.props' LachingVersion (${nuget:-none}) must be at or below $distinct"; return 1
  fi
  # LachingWebVersion: the web packages' version, the same way.
  web="$(sed -n 's/.*<LachingWebVersion[^>]*>\([^<]*\)<.*/\1/p' web/Directory.Build.props)"
  if [ -z "$web" ] || [ "$(printf '%s\n' "$web" "$distinct" | sort -t. -k1,1n -k2,2n -k3,3n | tail -n 1)" != "$distinct" ]; then
    echo "web/Directory.Build.props' LachingWebVersion (${web:-none}) must be at or below $distinct"; return 1
  fi
}

if [ -n "$version" ]; then
  check "version strings are $version" versions_agree "$version"
  finish
fi
check "versions agree" versions_agree

if touched "$swift"; then
  if [ "$darwin" = 1 ]; then
    check "swift-format lint" xcrun swift-format lint -r --strict --configuration .swift-format Sources Tests
  else skip "swift-format: needs macOS"; fi
fi
if touched "$dotnet"; then
  if has dotnet; then check "dotnet format whitespace" dotnet format whitespace dotnet --folder --verify-no-changes
  else skip "dotnet format: needs the .NET SDK"; fi
fi
if touched "$web"; then
  if has dotnet; then check "dotnet format whitespace (web)" dotnet format whitespace web --folder --verify-no-changes
  else skip "dotnet format (web): needs the .NET SDK"; fi
fi
if touched "$kotlin"; then
  pinned="$(sed -n 's/^ *KTLINT_VERSION: "\(.*\)"$/\1/p' .github/workflows/kotlin.yml)"
  if ! has ktlint; then skip "ktlint: needs ktlint $pinned, the version kotlin.yml pins (brew install ktlint)"
  else
    found="$(ktlint --version 2>&1 | head -n 1)"
    if [ "$found" = "ktlint version $pinned" ]; then check "ktlint" kotlin_lint
    else skip "ktlint: needs ktlint $pinned, the version kotlin.yml pins; this one says: $found"; fi
  fi
fi
if touched "$tools"; then
  if has python3; then
    tools_tests
    check "fixture art current" scripts/fixture-art.sh --check
  else skip "tools tests: needs python3"; fi
fi
if touched '^tools/landing/'; then
  if has npm; then check "@laching/landing (types, lint, tests)" landing_check
  else skip "@laching/landing: needs Node 24 and npm"; fi
fi
if [ "$fast" = 0 ]; then
  if touched "$swift|^conformance/"; then
    if [ "$darwin" = 1 ]; then
      check "swift build + test" swift test
      check "conformance vectors current" vectors_current
    else skip "swift test: needs macOS (push and run swift.yml)"; fi
  fi
  if touched "$dotnet"; then
    if has dotnet; then
      # One restore and build for the four, their test runs side by side: about 7 s instead of about 16 s.
      check "dotnet build + test (Core, Companion, Companion.Shell, Testing)" \
        dotnet test dotnet/Laching.Tests.slnf -v q --nologo
    else skip "dotnet test: needs the .NET SDK"; fi
  fi
  if touched "$kotlin|^conformance/"; then
    if jdk="$(jdk17)"; then check "gradlew :core:test :testing:test" kotlin_tests "$jdk"
    else skip "gradlew :core:test :testing:test: needs a JDK 17 (JAVA_HOME, or brew install openjdk@17)"; fi
  fi
  if touched "$web"; then
    if has dotnet; then
      check "dotnet format (web)" dotnet format web/Laching.Web.sln --verify-no-changes
      # The whole solution in one run, the Jobs tests filtered out (still built) when PostgreSQL isn't there.
      if web_postgres; then
        check "dotnet build + test (web)" dotnet test web/Laching.Web.sln -v q --nologo
      else
        skip "Laching.Web.Jobs.Tests: needs PostgreSQL (LACHING_WEB_TEST_POSTGRES, or docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:16)"
        check "dotnet build + test (web, without Jobs)" \
          dotnet test web/Laching.Web.sln -v q --nologo --filter 'FullyQualifiedName!~Laching.Web.Jobs.Tests'
      fi
      if touched "$tailwind"; then check "Tailwind sample" web/scripts/tailwind-sample.sh; fi
    else skip "dotnet test (web): needs the .NET SDK"; fi
  fi
fi
summary
finish
