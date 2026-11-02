#!/usr/bin/env bash
# Prepares a fresh Linux container (Claude Code on the web, Copilot's coding agent, a dev container) to run what a
# companion app can run off a Mac: the Windows Core tests (`dotnet test`), C# formatting, the Kotlin core tests and
# ktlint, the l10n and voice checks and the landing build. Names come from companion.json. Idempotent and quiet when
# everything is already there.
#
#   laching/tools/setup-linux.sh        (the app's scripts/setup-linux.sh checks the submodule out and execs it)
#
# Claude Code runs it at session start in cloud sessions (CLAUDE_CODE_REMOTE=true, .claude/settings.json), and the
# app's copilot-setup-steps.yml as a step, after actions/setup-node for Node 24. It never touches an existing landing
# node_modules, so it's safe in a shared checkout.
set -uo pipefail
TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
has() { command -v "$1" >/dev/null 2>&1; }
# Hands a variable or a PATH entry on to what runs next: the rest of a Claude Code session (SessionStart hooks get
# CLAUDE_ENV_FILE) or the later steps of a GitHub Actions job, such as Copilot's setup (GITHUB_ENV, GITHUB_PATH).
hand_on() {
  export "$1=$2"
  [ -n "${CLAUDE_ENV_FILE:-}" ] && echo "export $1=\"$2\"" >>"$CLAUDE_ENV_FILE"
  [ -n "${GITHUB_ENV:-}" ] && echo "$1=$2" >>"$GITHUB_ENV"
  return 0
}
add_path() {
  export PATH="$1:$PATH"
  [ -n "${CLAUDE_ENV_FILE:-}" ] && echo "export PATH=\"$1:\$PATH\"" >>"$CLAUDE_ENV_FILE"
  [ -n "${GITHUB_PATH:-}" ] && echo "$1" >>"$GITHUB_PATH"
  return 0
}
has python3 || { echo "error: python3 is required (the tools are Python)" >&2; exit 1; }
eval "$(python3 "$TOOLS/companion.py" env)" || exit 1
cd "$ROOT"

# The Laching tools and the recorded voices are submodules.
git submodule update --init ${LACHING_PATH:+"$LACHING_PATH"} ${VOICE_DIR:+"$VOICE_DIR"} >/dev/null 2>&1 ||
  echo "warning: couldn't fetch the submodules; voice.py check won't run" >&2
# A landing page kept in its own repository is a submodule too.
if [ -n "$LANDING_DIR" ] && git ls-files -s -- "$LANDING_DIR" | grep -q '^160000'; then
  git submodule update --init "$LANDING_DIR" >/dev/null 2>&1 || echo "warning: couldn't fetch $LANDING_DIR/" >&2
fi
git config submodule.recurse true; git config push.recurseSubmodules on-demand
[ -d .githooks ] && git config core.hooksPath .githooks

# .NET 10 SDK, into ~/.dotnet.
if [ -n "$WINDOWS_DIR" ]; then
  # An earlier run's SDK, which a fresh shell doesn't have on PATH.
  [ -x "$HOME/.dotnet/dotnet" ] && export PATH="$HOME/.dotnet:$PATH"
  if ! has dotnet || ! dotnet --list-sdks 2>/dev/null | grep -q '^10\.'; then
    echo "Installing the .NET 10 SDK into ~/.dotnet"
    curl -fsSL https://dot.net/v1/dotnet-install.sh -o "${TMPDIR:-/tmp}/dotnet-install.sh" &&
      bash "${TMPDIR:-/tmp}/dotnet-install.sh" --channel 10.0 --install-dir "$HOME/.dotnet" >/dev/null ||
      echo "warning: .NET install failed; dotnet test and dotnet format won't run" >&2
  fi
  if [ -x "$HOME/.dotnet/dotnet" ]; then
    hand_on DOTNET_ROOT "$HOME/.dotnet"
    hand_on DOTNET_CLI_TELEMETRY_OPTOUT 1
    add_path "$HOME/.dotnet"
  fi
  tests="$WINDOWS_DIR/$WINDOWS_CORE_TESTS/$WINDOWS_CORE_TESTS.csproj"
  [ -n "$WINDOWS_CORE_TESTS" ] && [ -f "$tests" ] && has dotnet && dotnet restore "$tests" -v q --nologo >/dev/null 2>&1
fi

# JDK 17 and ktlint, for `./gradlew :<android.core>:test` and the Kotlin style check. The app module itself needs the
# Android SDK: it builds on a Mac or in CI (kotlin.yml), not here.
if [ -n "$ANDROID_DIR" ]; then
  as_root() { if [ "$(id -u)" = 0 ]; then "$@"; else sudo -n "$@"; fi; }
  if ! has java || ! java -version 2>&1 | grep -q 'version "17'; then
    if has apt-get; then
      echo "Installing OpenJDK 17"
      { as_root apt-get install -y -qq openjdk-17-jdk-headless ||
        { as_root apt-get update -qq && as_root apt-get install -y -qq openjdk-17-jdk-headless; }; } >/dev/null 2>&1 ||
        echo "warning: JDK 17 install failed; gradlew :$ANDROID_CORE:test won't run" >&2
    else
      echo "warning: no JDK 17 and no apt-get; gradlew :$ANDROID_CORE:test won't run" >&2
    fi
  fi
  if ! has ktlint && [ ! -x "$HOME/.local/bin/ktlint" ]; then
    echo "Installing ktlint into ~/.local/bin"
    mkdir -p "$HOME/.local/bin" &&
      curl -fsSL "https://github.com/pinterest/ktlint/releases/download/1.8.0/ktlint" -o "$HOME/.local/bin/ktlint" &&
      chmod +x "$HOME/.local/bin/ktlint" || echo "warning: ktlint install failed; the Kotlin style check won't run" >&2
  fi
  has ktlint || [ ! -x "$HOME/.local/bin/ktlint" ] || add_path "$HOME/.local/bin"
fi

# Node 24 and the landing dependencies, which bring its build tool.
if [ -n "$LANDING_DIR" ] && [ -d "$LANDING_DIR" ]; then
  if ! has node; then
    echo "warning: Node isn't installed; the landing checks won't run" >&2
  elif [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 24 ]; then
    echo "warning: Node $(node -v) is older than Node 24" >&2
  fi
  if has npm && [ ! -d "$LANDING_DIR/node_modules" ]; then
    echo "Installing landing dependencies"
    npm ci --prefix "$LANDING_DIR" --no-audit --no-fund >/dev/null || echo "warning: npm ci failed in $LANDING_DIR/" >&2
  fi
fi

echo "Linux setup done: $(has dotnet && dotnet --version || echo 'no dotnet'), node $(node -v 2>/dev/null || echo none)\
${ANDROID_DIR:+, java $(java -version 2>&1 | sed -n 's/.*version "\([^"]*\)".*/\1/p' | grep . || echo none)}"
