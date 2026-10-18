#!/bin/sh
# Laching's releases, made on demand: release.yml runs these (dispatch it, dry_run first), and they work the same by hand
# from a checkout with its tags (git fetch --tags). One tag per release; Swift and Kotlin take the tag, NuGet (every
# package at LachingVersion in dotnet/Directory.Build.props; the web packages at LachingWebVersion in
# web/Directory.Build.props) and npm (@laching/landing) a new version only when their code changed since their last one.
# Usage: scripts/release.sh plan [--bump auto|patch|minor|major]
#                                   `release X.Y.Z (why)`, or `none (why)` when nothing was committed since the last
#                                   tag. auto: the next minor while 0.x (major from 1.0) when a pull request merged since
#                                   the last tag is labelled `breaking`, else the next patch (the labels come from gh).
#        scripts/release.sh nuget X.Y.Z    `ship (why)` or `skip (why)`: the NuGet packages ship in X.Y.Z when
#                                   dotnet/Directory.Build.props or a packable project's folder changed since
#                                   v<LachingVersion> (or LachingVersion is X.Y.Z already: after bump).
#        scripts/release.sh projects       the NuGet packages: dotnet/'s packable projects, one per line.
#        scripts/release.sh web X.Y.Z      `ship (why)` or `skip (why)`, the same for the web packages: web/src/ or
#                                   web/'s Directory.* files changed since v<LachingWebVersion>.
#        scripts/release.sh web-projects   the web packages: web/src/'s packable projects, one per line.
#        scripts/release.sh landing        `yes (why)` or `no (why)`: @laching/landing ships when tools/landing changed
#                                   since the version npm has.
#        scripts/release.sh bump X.Y.Z     puts X.Y.Z in the strings that carry the tag (Laching.swift,
#                                   kotlin/gradle.properties, README.md, site/index.html) and, when NuGet ships, in
#                                   LachingVersion (LachingWebVersion when the web packages ship), so
#                                   scripts/verify.sh --version X.Y.Z passes. Committing and tagging are the caller's.
set -eu
cd "$(dirname "$0")/.."

usage() { sed -n 's/^# \{0,1\}//; 2,22p' "$0" >&2; exit 2; }
die() { echo "release.sh: $*" >&2; exit 1; }
by_version() { sort -t. -k1,1n -k2,2n -k3,3n; }
current() { sed -n 's/.*public static let version = "\([^"]*\)".*/\1/p' Sources/LachingCore/Laching.swift; }
# The NuGet packages: the projects in dotnet/ with <IsPackable>true</IsPackable>, one name per line.
projects() { grep -l '<IsPackable>true</IsPackable>' dotnet/*/*.csproj | sed 's#^dotnet/\([^/]*\)/.*#\1#'; }
nuget_version() { sed -n 's/.*<LachingVersion[^>]*>\([^<]*\)<.*/\1/p' dotnet/Directory.Build.props; }
# The web packages: the projects in web/src/ with <IsPackable>true</IsPackable>.
web_projects() { grep -l '<IsPackable>true</IsPackable>' web/src/*/*.csproj | sed 's#^web/src/\([^/]*\)/.*#\1#'; }
web_version() { sed -n 's/.*<LachingWebVersion[^>]*>\([^<]*\)<.*/\1/p' web/Directory.Build.props; }
tagged() { git rev-parse -q --verify "refs/tags/v$1" >/dev/null; }

# The merged pull requests GitHub associates with commit $2 of repository $1, one per line: number, tab, labels.
pulls() {
  if ! gh api "repos/$1/commits/$2/pulls" --jq '.[] | select(.merged_at) | "\(.number)\t\([.labels[].name] | join(","))"' \
    2>"$errors"; then
    grep -q 'No commit found' "$errors" && return 0  # not pushed yet (by hand): no pull request brought it
    die "couldn't ask GitHub for $2's pull request: $(cat "$errors")
Give gh a token (GH_TOKEN, or gh auth login), or pass --bump patch|minor|major."
  fi
}

plan() {
  previous=$(git tag --merged HEAD --list 'v*' | sed -n 's/^v\([0-9]*\.[0-9]*\.[0-9]*\)$/\1/p' | by_version | tail -n 1)
  [ -n "$previous" ] || die "HEAD contains no vX.Y.Z tag: fetch the tags (git fetch --tags)"
  [ "$(current)" = "$previous" ] ||
    die "Laching.swift says $(current), but the last release tag is v$previous:" \
      "the version strings change only in a release's commit"
  count=$(git rev-list --count "v$previous..HEAD")
  [ "$count" -gt 0 ] || { echo "none (no commits since v$previous)"; return; }
  since="$count commit$([ "$count" -eq 1 ] || echo s) since v$previous"
  level=$1 why="$1: asked for"
  if [ "$level" = auto ]; then
    command -v gh >/dev/null ||
      die "plan reads the pull requests' labels with gh: install it, or pass --bump patch|minor|major"
    slug=${GITHUB_REPOSITORY:-$(git remote get-url origin | sed -E 's#(\.git)?/*$##; s#.*[/:]([^/:]+/[^/:]+)$#\1#')}
    found=$(mktemp) errors=$(mktemp)
    for commit in $(git rev-list "v$previous..HEAD"); do pulls "$slug" "$commit" >> "$found"; done
    prs=$(cut -f1 "$found" | sort -u | wc -l | tr -d ' ')
    breaking=$(awk -F'\t' '{ n = split($2, l, ","); for (i = 1; i <= n; i++) if (l[i] == "breaking") print $1 }' "$found" |
      sort -un | sed 's/^/#/' | paste -sd, - | sed 's/,/, /g')
    rm -f "$found" "$errors"
    if [ -n "$breaking" ]; then
      level=minor why="minor: $breaking labelled breaking (a minor while 0.x)"
      case $previous in 0.*) ;; *) level=major why="major: $breaking labelled breaking" ;; esac
    elif [ "$prs" -gt 0 ]; then
      level=patch why="patch: none of the $prs pull request$([ "$prs" -eq 1 ] || echo s) merged is labelled breaking"
    else
      level=patch why="patch: no pull request merged, only commits pushed to master"
    fi
  fi
  version=$(echo "$previous" | awk -F. -v level="$level" '{
    if (level == "major") print ($1 + 1) ".0.0"; else if (level == "minor") print $1 "." ($2 + 1) ".0"
    else print $1 "." $2 "." ($3 + 1) }')
  ! tagged "$version" || die "v$version exists already (tags never move): fetch and look at what tagged it"
  echo "release $version ($why; $since)"
}

nuget() {
  at=$(nuget_version)
  [ -n "$at" ] || die "dotnet/Directory.Build.props has no LachingVersion"
  [ "$at" != "$1" ] || { echo "ship (LachingVersion is $1 already)"; return; }
  tagged "$at" || die "LachingVersion is $at, which has no tag v$at here: fetch the tags"
  # The packages' folders; their tests, Fixture.App and the README don't count.
  if git diff --quiet "v$at" HEAD -- dotnet/Directory.Build.props $(projects | sed 's#^#dotnet/#; s#$#/#'); then
    echo "skip (the packages are unchanged since $at, their version on NuGet)"
  else
    echo "ship (the packages changed since $at, their version on NuGet)"
  fi
}

web() {
  at=$(web_version)
  [ -n "$at" ] || die "web/Directory.Build.props has no LachingWebVersion"
  [ "$at" != "$1" ] || { echo "ship (LachingWebVersion is $1 already)"; return; }
  tagged "$at" || die "LachingWebVersion is $at, which has no tag v$at here: fetch the tags"
  # The packages' sources and the build files they pack with; their tests, the Tailwind sample and the README don't count.
  if git diff --quiet "v$at" HEAD -- web/src web/Directory.Build.props web/Directory.Build.targets \
    web/Directory.Packages.props; then
    echo "skip (the web packages are unchanged since $at, their version on NuGet)"
  else
    echo "ship (the web packages changed since $at, their version on NuGet)"
  fi
}

landing() {
  published=$(npm view @laching/landing version 2>/dev/null || true)
  if [ -z "$published" ]; then echo "yes (npm has no version of it, or didn't answer)"
  elif ! tagged "$published"; then echo "yes (npm's $published has no tag here)"
  elif git diff --quiet "v$published" HEAD -- tools/landing; then
    echo "no (tools/landing is unchanged since $published, the version on npm)"
  else echo "yes (tools/landing changed since $published, the version on npm)"; fi
}

bump() {
  new=$1 old=$(current)
  echo "$new" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || die "bump takes X.Y.Z, not $new"
  [ "$new" != "$old" ] && [ "$(printf '%s\n' "$old" "$new" | by_version | tail -n 1)" = "$new" ] ||
    die "bump goes forward: $new isn't after $old"
  ! tagged "$new" || die "v$new exists already (tags never move)"
  ships=$(nuget "$new") web_ships=$(web "$new")
  re=$(printf '%s' "$old" | sed 's/[.]/\\./g')
  # "X.Y.Z" in the sources and the install snippets, vX.Y.Z in the site's label and the README's latest release,
  # lachingVersion=X.Y.Z in kotlin/gradle.properties.
  for file in Sources/LachingCore/Laching.swift kotlin/gradle.properties README.md site/index.html; do
    sed "s/\"$re\"/\"$new\"/g; s/v$re\([^0-9]\)/v$new\1/g; s/v$re\$/v$new/
         s/^lachingVersion=$re\$/lachingVersion=$new/" "$file" > "$file.new"
    mv "$file.new" "$file"
  done
  case $ships in
    ship*)
      sed "s/\(<LachingVersion[^>]*>\)[^<]*</\1$new</" dotnet/Directory.Build.props > dotnet/Directory.Build.props.new
      mv dotnet/Directory.Build.props.new dotnet/Directory.Build.props ;;
  esac
  case $web_ships in
    ship*)
      sed "s/\(<LachingWebVersion[^>]*>\)[^<]*</\1$new</" web/Directory.Build.props > web/Directory.Build.props.new
      mv web/Directory.Build.props.new web/Directory.Build.props ;;
  esac
  echo "Bumped $old to $new; NuGet: $ships; web: $web_ships"
}

case "${1:-}" in
  plan)
    case "$#:${2:-}" in
      1:) plan auto ;;
      3:--bump)
        case $3 in
          auto | patch | minor | major) plan "$3" ;;
          *) die "--bump takes auto, patch, minor or major, not $3" ;;
        esac ;;
      *) usage ;;
    esac ;;
  nuget) [ $# -eq 2 ] || usage; nuget "$2" ;;
  projects) [ $# -eq 1 ] || usage; projects ;;
  web) [ $# -eq 2 ] || usage; web "$2" ;;
  web-projects) [ $# -eq 1 ] || usage; web_projects ;;
  landing) [ $# -eq 1 ] || usage; landing ;;
  bump) [ $# -eq 2 ] || usage; bump "$2" ;;
  -h | --help) sed -n 's/^# \{0,1\}//; 2,22p' "$0"; exit 0 ;;
  *) usage ;;
esac
