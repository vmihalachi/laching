#!/usr/bin/env python3
"""Reads a companion app's `companion.json`, the one file every tool in `tools/` takes its names and paths from.

    python3 laching/tools/companion.py get mac.scheme       # a value: strings as they are, the rest as JSON
    python3 laching/tools/companion.py path voice.dir       # a path from the file, made absolute
    python3 laching/tools/companion.py root                 # the app's root folder
    python3 laching/tools/companion.py validate [FILE…]     # check against companion.schema.json (exit 1 on problems)
    python3 laching/tools/companion.py env                  # names, paths and path patterns as shell assignments
    python3 laching/tools/companion.py pins                 # the Laching version everywhere it's pinned (exit 1 on a mismatch)
    python3 laching/tools/companion.py bump X.Y.Z           # every pin, the submodule and the landing page to vX.Y.Z
    python3 laching/tools/companion.py link [LACHING]      # build against a Laching checkout (../laching) until unlink
    python3 laching/tools/companion.py unlink               # back to the pinned release, byte for byte
    python3 laching/tools/companion.py conform              # the app uses Laching's tools, not copies (exit 1 on drift)
    python3 laching/tools/companion.py checks               # the app's own verify.sh checks (verify.checks), tab-separated
    python3 laching/tools/companion.py apps [APP…]          # the apps next to this checkout: their pins and conform (exit 1 on drift)

The root is the folder holding `companion.json`: `$COMPANION_ROOT` when set, else the nearest one walking up from the
current directory. Paths in the file are relative to it. Other tools import this module:

    import companion
    config = companion.load()                 # validated; exits with the problems otherwise
    config.get("voice.voices")                # None for an absent or null value
    config.path("l10n.catalog")               # an absolute Path, or None

Standard library only. The schema (`companion.schema.json`) is the source of truth for every section.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

FILE = "companion.json"
SCHEMA = Path(__file__).resolve().with_name("companion.schema.json")


class ConfigError(Exception):
    pass


def find_root(start: Path | None = None) -> Path:
    """`$COMPANION_ROOT`, else the nearest folder with a companion.json from `start` (the current directory) up."""
    env = os.environ.get("COMPANION_ROOT")
    if env:
        root = Path(env).expanduser().resolve()
        if not (root / FILE).is_file():
            raise ConfigError(f"$COMPANION_ROOT is {root}, which has no {FILE}")
        return root
    folder = (start or Path.cwd()).resolve()
    for candidate in [folder, *folder.parents]:
        if (candidate / FILE).is_file():
            return candidate
    raise ConfigError(f"no {FILE} in {folder} or any folder above it (or set $COMPANION_ROOT)")


class Config:
    """A loaded companion.json: dotted lookups and paths resolved against the root."""

    def __init__(self, data: dict, root: Path):
        self.data = data
        self.root = root

    def get(self, dotted: str, default=None):
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return default if node is None else node

    def require(self, dotted: str):
        value = self.get(dotted)
        if value is None:
            raise ConfigError(f"{FILE} needs {dotted}")
        return value

    def path(self, dotted: str, base: str | None = None) -> Path | None:
        """The path at `dotted`, absolute; relative to the folder at `base` (another dotted key) instead of the root."""
        value = self.get(dotted)
        if value is None:
            return None
        folder = self.path(base) if base else self.root
        return (folder / value).resolve()

    def require_path(self, dotted: str, base: str | None = None) -> Path:
        self.require(dotted)
        return self.path(dotted, base)  # type: ignore[return-value]

    # Names several tools need.

    @property
    def source_language(self) -> str:
        return self.require("languages.source")

    @property
    def languages(self) -> list[str]:
        return list(self.require("languages.all"))

    @property
    def mac_dir(self) -> Path:
        return self.require_path("mac.dir")

    @property
    def derived_data(self) -> Path:
        return self.path("mac.derivedData") or self.mac_dir / "build" / "dd.noindex"

    @property
    def work_dir(self) -> Path:
        """Scratch output for the tools (work files, voice renders), ignored by git."""
        return self.mac_dir / "build"

    @property
    def phrases_file(self) -> Path | None:
        """`conformance.dir`/phrases.json, or None for an app without a conformance section (no phrase bank)."""
        folder = self.path("conformance.dir")
        return folder / "phrases.json" if folder else None

    def require_phrases_file(self) -> Path:
        """The phrases file, exiting with a clear message when the app has no conformance section."""
        path = self.phrases_file
        if path is None:
            sys.exit(f"companion: {self.root / FILE} has no conformance section, so there is no phrase bank to read")
        return path


# MARK: Helpers the tools share


def utf8_console() -> None:
    """Writes stdout and stderr as UTF-8, whatever the console's code page: Windows Python encodes a redirected or
    remote console (ssh, the VM, a pipe) in cp1252, where the tools' arrows and ellipses raise UnicodeEncodeError
    midway through a command. Each tool calls it first thing under `__main__`."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


# Where Xcode keeps its compilation cache when a build doesn't name a derived-data folder.
COMPILATION_CACHE = Path.home() / "Library" / "Developer" / "Xcode" / "DerivedData" / "CompilationCache.noindex"


def xcode_cache_settings(*, mapped: bool = False) -> list[str]:
    """The xcodebuild build settings that turn on Xcode's compilation caching (Xcode 26 and later) in one cache that
    every derived-data folder shares. Without COMPILATION_CACHE_CAS_PATH, a build given `-derivedDataPath` keeps its
    cache inside that folder, so a second folder starts cold. With `mapped`, the prefix-mapping settings also take the
    SDK, project, derived-data and build-products paths out of the cache keys, so a build into another folder reuses the
    first one's compiles too: a second cold build of mybackhurts drops from 40 s to 8 s on the Mac and from 33 s to 9 s
    for the Simulator. It also writes those paths as `/^src`, `/^derived`… into the debug info, `#filePath` and the
    `.stringsdata`, so it's only for builds that are run, never tested, debugged or read. Empty when
    LACHING_COMPILATION_CACHE=0."""
    if os.environ.get("LACHING_COMPILATION_CACHE") == "0":
        return []
    settings = ["COMPILATION_CACHE_ENABLE_CACHING=YES", f"COMPILATION_CACHE_CAS_PATH={COMPILATION_CACHE}"]
    if mapped:
        settings += [f"{language}_ENABLE_{paths}PREFIX_MAPPING=YES"
                     for language in ("SWIFT", "CLANG") for paths in ("", "PROJECT_")]
    return settings


def fail(message: str) -> None:
    """Prints `error: message` and exits 2, the tools' usage-or-setup error."""
    print(f"error: {message}", file=sys.stderr)
    sys.exit(2)


def rel(path: Path | str, root: Path) -> str:
    """`path` from `root` with forward slashes on every OS (so the path patterns match on Windows too); a path outside
    `root` as given, with forward slashes."""
    try:  # resolved: a relative path, `..`, a symlink (macOS's /var is /private/var)
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except (ValueError, OSError):
        return str(path).replace("\\", "/")


def base_language(code: str) -> str:
    """"pt-BR" and "pt_BR" → "pt", as `Phrase.baseLanguage` compares them."""
    return re.split(r"[-_]", code, maxsplit=1)[0].lower()


def git(folder: Path, *args: str, check: bool = False, text: bool = True,
        timeout: float | None = None) -> subprocess.CompletedProcess:
    """git in `folder`'s repository, whichever repository a hook exported (`git_env`), output captured. As text it's
    UTF-8 whatever the locale (cp1252 on a Western Windows); `text=False` gives bytes. `check` raises on a failure, as
    `subprocess.run` does."""
    return subprocess.run(["git", "-C", str(folder), *args], capture_output=True, check=check, timeout=timeout,
                          encoding="utf8" if text else None, env=git_env())


def write_outputs(files: dict[Path, bytes | str], check: bool, root: Path | None = None) -> list[Path]:
    """Brings generated files in line with `files` (a path, relative to `root` when given, to its content) and returns
    those that differed from the disk, missing ones included. With `check` it writes nothing. Text is written as UTF-8
    with LF line endings on every OS and compared ignoring CRLFs (a Windows checkout's core.autocrlf, which git takes
    back out on commit); bytes are written and compared as they are."""
    stale = []
    for name, content in files.items():
        path = root / name if root else Path(name)
        data = content.encode("utf8") if isinstance(content, str) else content
        current = path.read_bytes() if path.is_file() else None
        if current is not None and isinstance(content, str):
            current = current.replace(b"\r\n", b"\n")
        if current == data:
            continue
        stale.append(path)
        if not check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    return stale


# MARK: Derived names (verify.sh and the hook share these)


def ere(text: str) -> str:
    """`text` as a literal in a regular expression both Python and `grep -E` read the same way."""
    return re.sub(r"([.^$*+?()\[\]{}|\\])", r"\\\1", text)


def derived(config: Config) -> dict[str, str]:
    """Paths relative to the root (POSIX, "" when absent) and the path patterns of the checks, by name."""

    def rel(dotted: str, base: str | None = None) -> str:
        path = config.path(dotted, base)
        return path.relative_to(config.root).as_posix() if path else ""

    mac, core, app, tests = rel("mac.dir"), rel("mac.core", "mac.dir"), rel("mac.app", "mac.dir"), rel("mac.tests", "mac.dir")
    windows, conformance = rel("windows.dir"), rel("conformance.dir")
    win_core, win_core_tests = config.get("windows.core", ""), config.get("windows.coreTests", "")
    win_app_tests = config.get("windows.appTests", "")
    landing, landing_i18n = rel("landing.dir"), rel("l10n.landing")
    store = rel("store.dir")
    android = rel("android.dir")
    android_core = config.get("android.core", "core") if android else ""
    android_app = config.get("android.app", "app") if android else ""
    values = {
        "ROOT": str(config.root),
        "TOOLS": str(Path(__file__).resolve().parent),
        "NAME": config.require("name"),
        "MAC_DIR": mac,
        "MAC_PROJECT": config.get("mac.project", ""),
        "MAC_SCHEME": config.get("mac.scheme", ""),
        "MAC_IOS_SCHEME": config.get("mac.iosScheme", ""),
        "MAC_SPEC": rel("mac.projectSpec", "mac.dir"),
        "MAC_APP_NAME": config.get("mac.app", ""),
        "SCREENSHOT_ENV": config.get("mac.screenshotEnv", ""),
        "SCREENSHOTS_OUT": rel("screenshots.out"),
        "SOURCE_LANGUAGE": config.get("languages.source", ""),
        "LANGUAGES": " ".join(config.get("languages.all", [])),  # space-separated: `for lang in $LANGUAGES`
        "MAC_APP": app,
        "MAC_TESTS": tests,
        "MAC_CORE": core,
        "DERIVED": config.derived_data.relative_to(config.root).as_posix(),
        # xcodebuild's compilation-cache settings, space-separated: `xcodebuild … $XCODE_CACHE` (xcode_cache_settings).
        "XCODE_CACHE": " ".join(xcode_cache_settings()),
        "XCODE_CACHE_MAPPED": " ".join(xcode_cache_settings(mapped=True)),
        "CATALOG": rel("l10n.catalog"),
        "LANDING_DIR": landing,
        "LANDING_I18N": landing_i18n,
        "LANDING_TOOL": config.get("landing.tool", ""),
        "STORE_DIR": store,
        # The source language's listing, which store/check.py reads every other language against.
        "STORE_LISTING": f"{store}/{config.get('languages.source', '')}.md" if store else "",
        "WINDOWS_DIR": windows,
        "WINDOWS_CORE": win_core,
        "WINDOWS_CORE_TESTS": win_core_tests,
        "WINDOWS_APP": config.get("windows.app", ""),
        "WINDOWS_APP_TESTS": win_app_tests,
        "WINDOWS_ASSETS": rel("windows.assets.out"),
        "WINDOWS_STRINGS": rel("windows.strings"),
        "ANDROID_DIR": android,
        "ANDROID_CORE": android_core,
        "ANDROID_APP": android_app,
        "ANDROID_APP_ID": (config.get("android.applicationId") or config.get("bundleId", "")) if android else "",
        "ANDROID_DEV_APP_ID": (config.get("android.devApplicationId") or config.get("devBundleId", "")) if android else "",
        # `android.strings`, or the older `l10n.androidStrings` it replaced.
        "ANDROID_STRINGS": rel("android.strings") or rel("l10n.androidStrings"),
        "CONFORMANCE_DIR": conformance,
        "CONFORMANCE_FLAG": config.get("conformance.writeFlag", ""),
        "CORE_PACKAGE": config.get("conformance.corePackagePath", ""),
        "VOICE_DIR": rel("voice.dir"),
        # character.py's input and outputs; the raster ones only for raster skins.
        "CHARACTERS_DESIGN": rel("characters.design"),
        "CHARACTERS_SWIFT": rel("characters.swiftOut"),
        "CHARACTERS_PALETTE": rel("characters.paletteFile"),
        "CHARACTERS_ASSETS": rel("characters.assets") if config.get("characters.mode") == "raster" else "",
        "CHARACTERS_WINDOWS": rel("characters.windowsAssets") if config.get("characters.mode") == "raster" else "",
        "SOUNDS_OUT": rel("sounds.out"),
        "SOUNDS_RECIPES": rel("sounds.recipes"),
        "LACHING_PATH": rel("laching.path"),
        "LACHING_TAG": config.get("laching.tag", ""),
        "GIT_LOCAL_ENV": " ".join(GIT_LOCAL_ENV),  # verify.sh unsets them for git in a submodule
    }
    never = "^$a"  # matches no path: a section the app doesn't have
    values["RE_NEVER"] = never  # verify.sh --all skips a check whose patterns are all this one
    # The app's own Swift: mac.app, mac.tests and `mac.format`'s folders outside the core package (shared and platform
    # sources, an app split across several), or all of mac.dir without a core package.
    formatted = [f"{mac}/{entry}".rstrip("/") for entry in config.get("mac.format", []) if not entry.endswith(".swift")]
    outside = [d for d in formatted if not (d == core or d.startswith(f"{core}/"))]
    swift_roots = list(dict.fromkeys([app, *outside, tests])) if core else [mac, app, tests]
    swift_dirs = "|".join(ere(d) for d in swift_roots if d)
    values["RE_SWIFT"] = (
        f"^({swift_dirs})/.*\\.swift$" + (f"|^{ere(values['MAC_SPEC'])}$" if values["MAC_SPEC"] else "")
        if swift_dirs else never
    )
    values["RE_COREPKG"] = f"^{ere(core)}/" if core else never
    values["RE_PURE"] = f"^{ere(core)}/Sources/|^{ere(core)}/Tests/[^/]+/Conformance/" if core else never
    # The Swift that shows text (the app's and the core's sources, not tests), the catalogs, and what l10n.py reads
    # besides: companion.json's languages and, on a pin bump, the tool itself.
    str_roots = [d for d in swift_roots if d and d != tests] + [f"{core}/Sources"] if core else [mac or app]
    pin = [f"^{ere(values['LACHING_PATH'])}$"] if values["LACHING_PATH"] else []
    values["RE_PIN"] = pin[0] if pin else never
    values["RE_STRINGS"] = "|".join(part for part in [
        f"^({'|'.join(ere(d) for d in str_roots if d)})/.*\\.swift$" if any(str_roots) else "", "\\.xcstrings$",
        f"^{ere(landing_i18n)}/" if landing_i18n else "", "^companion\\.json$", *pin,
    ] if part)
    values["RE_CSHARP"] = f"^{ere(windows)}/.*\\.cs$" if windows else never
    values["RE_WINDOWS_CORE"] = (
        f"^{ere(windows)}/{ere(win_core)}(\\.Tests)?/|^{ere(conformance)}/" if windows and win_core else never
    )
    values["RE_WINDOWS_APP"] = f"^{ere(windows)}/{ere(values['WINDOWS_APP'])}/" if windows and values["WINDOWS_APP"] else never
    # The WinUI build: the app, the core it references and the shared build settings and SDK pin.
    values["RE_WINDOWS_BUILD"] = "|".join(part for part in [
        values["RE_WINDOWS_APP"] if windows and values["WINDOWS_APP"] else "",
        f"^{ere(windows)}/{ere(win_core)}/" if windows and win_core and values["WINDOWS_APP"] else "",
        f"^{ere(windows)}/(Directory\\.(Build|Packages)\\.props|global\\.json)$" if windows and values["WINDOWS_APP"] else "",
    ] if part) or never
    # The app's model tests compile the app's own files, so either side changing runs them.
    values["RE_WINDOWS_APP_TESTS"] = (
        f"^{ere(windows)}/({ere(values['WINDOWS_APP'])}|{ere(win_app_tests)})/" if windows and win_app_tests else never
    )
    asset_sources = [rel(key) for key in ("windows.assets.source", "windows.assets.devSource") if config.get(key)]
    values["RE_WINDOWS_ASSETS"] = (
        "|".join([f"^{ere(source)}$" for source in asset_sources] + [f"^{ere(values['WINDOWS_ASSETS'])}/"])
        if values["WINDOWS_ASSETS"] else never
    )
    values["RE_KOTLIN"] = f"^{ere(android)}/.*\\.kts?$" if android else never
    values["RE_ANDROID_CORE"] = f"^{ere(android)}/{ere(android_core)}/|^{ere(conformance)}/" if android else never
    values["RE_ANDROID_APP"] = f"^{ere(android)}/{ere(android_app)}/" if android else never
    values["RE_LANDING"] = (
        f"^{ere(landing)}/|^{ere(conformance)}/(phrases|settings|catalog)\\.json$|Localizable\\.xcstrings$" if landing else never
    )
    values["RE_STORE"] = f"^{ere(store)}/[^/]+\\.md$" if store else never
    # phrases.py check only for an app with a phrase bank.
    phrases = config.phrases_file
    values["RE_PHRASES"] = (
        f"^{ere(conformance)}/phrases\\.json$|Localizable\\.xcstrings$" if phrases and phrases.is_file() else never
    )
    values["RE_VOICE"] = (
        f"Localizable\\.xcstrings$|^{ere(values['VOICE_DIR'])}($|/)|^{ere(conformance)}/phrases\\.json$"
        + (f"|^{ere(rel('voice.config'))}$" if config.get("voice.config") else "")
        if values["VOICE_DIR"] else never
    )
    # The generators' inputs and outputs: an edit to either side reruns the --check. A pin bump can change the
    # generator itself, so the submodule counts too.
    character_paths = [
        f"^{ere(values['CHARACTERS_DESIGN'])}/",
        f"^{ere(conformance)}/characters/" if conformance else "",
        f"^{ere(values['CHARACTERS_SWIFT'])}/[^/]+Art\\.swift$" if values["CHARACTERS_SWIFT"] else "",
        f"^{ere(values['CHARACTERS_PALETTE'])}$" if values["CHARACTERS_PALETTE"] else "",
        f"^{ere(values['CHARACTERS_ASSETS'])}/" if values["CHARACTERS_ASSETS"] else "",
        f"^{ere(values['CHARACTERS_WINDOWS'])}/" if values["CHARACTERS_WINDOWS"] else "",
    ]
    values["RE_CHARACTERS"] = (
        "|".join([path for path in character_paths if path] + pin) if values["CHARACTERS_DESIGN"] else never
    )
    values["RE_SOUNDS"] = (
        "|".join([f"^{ere(values['SOUNDS_OUT'])}/[^/]+\\.wav$", f"^{ere(values['SOUNDS_RECIPES'])}$"] + pin)
        if values["SOUNDS_OUT"] and values["SOUNDS_RECIPES"] else never
    )
    return values


# What `git rev-parse --local-env-vars` lists, less the `-c` settings: the variables that tell git which repository to
# use. A git hook exports them for the app's repository and they win over `-C`.
GIT_LOCAL_ENV = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG", "GIT_OBJECT_DIRECTORY", "GIT_DIR", "GIT_WORK_TREE",
    "GIT_IMPLICIT_WORK_TREE", "GIT_GRAFT_FILE", "GIT_INDEX_FILE", "GIT_NO_REPLACE_OBJECTS", "GIT_REPLACE_REF_BASE",
    "GIT_PREFIX", "GIT_SHALLOW_FILE", "GIT_COMMON_DIR",
)


def git_env() -> dict[str, str]:
    """The environment for git in another repository than the app's (a submodule: `laching/`, the voices): the
    current one without `GIT_LOCAL_ENV`. Under the pre-commit hook `git -C laching describe` would otherwise ask the
    app's repository, always so in a linked worktree, where the exported GIT_DIR is absolute."""
    return {key: value for key, value in os.environ.items() if key not in GIT_LOCAL_ENV}


def git_bash() -> str | None:
    """Git for Windows' bash, which runs the shell scripts on Windows: `bash` on PATH unless it's System32's (WSL's,
    which runs the script in Linux instead), else Git\\bin\\bash.exe beside git.exe (Git\\cmd\\git.exe). None when
    there's none."""
    found = shutil.which("bash")
    if found and "system32" not in found.lower():
        return found
    git = shutil.which("git")
    for folder in Path(git).resolve().parents if git else []:
        if (folder / "bin" / "bash.exe").is_file():
            return str(folder / "bin" / "bash.exe")
    return None


def shell_command(script: Path, windows: bool = os.name == "nt") -> list[str] | None:
    """The command that runs the shell script `script`: the script itself on macOS and Linux; on Windows, which starts
    no `.sh` itself, `git_bash` with the script's path in forward slashes. None on a Windows without that bash."""
    if not windows:
        return [str(script)]
    bash = git_bash()
    return [bash, script.as_posix()] if bash else None


# MARK: Pins: where the Laching release is pinned, `bump`, `link` and `unlink`


class PinError(Exception):
    """Why `bump`, `link` or `unlink` refused, and what to do about it."""


class Pin(NamedTuple):
    """A place the Laching release is pinned and the version it names. A version written in a file carries the file and
    where the version sits in its text, which is what `bump` rewrites."""

    label: str
    version: str
    file: Path | None = None
    span: tuple[int, int] | None = None
    package: str | None = None  # a `Laching.*` NuGet reference's package


def read_text(path: Path) -> str:
    """A file's text with its line endings as they are (a `.csproj` may use CRLF), so a rewrite changes only its edit."""
    return path.read_bytes().decode("utf8")


def write_text(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf8"))


def _tail(result: subprocess.CompletedProcess, lines: int = 15) -> str:
    return "\n".join((result.stdout + result.stderr).strip().splitlines()[-lines:])


# A Swift manifest or an XcodeGen spec is read in blocks, cut before each `.package(` line and each YAML key that opens
# a mapping, so the version comes from the block that names Laching and not from another package's.
_BLOCK = re.compile(r"\n(?=[ \t]*(?:\.package|\w+:[ \t]*$))", re.M)
_PACKAGE_EXACT = r'exact:\s*"([^"]+)"'
_SPEC_EXACT = r"(?:exactVersion|exact):\s*\"?([0-9][^\s\"]*)"
_NUGET = re.compile(r'Include="(Laching\.[A-Za-z.]+)"\s+Version="([^"]+)"')
_COMPANION_TAG = re.compile(r'"laching"\s*:\s*\{[^{}]*?"tag"\s*:\s*"([^"]*)"')
_WORKFLOW_REF = re.compile(r"[\w.-]+/laching/\.github/workflows/([\w.-]+\.ya?ml)@([\w.-]+)")
_LANDING_PACKAGE = "@laching/landing"
_LANDING_DEPENDENCY = re.compile(r'"@laching/landing"\s*:\s*"[~^]?(\d+\.\d+\.\d+)"')
_LOCKFILES = ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock")


def _laching_block_match(text: str, pattern: str) -> re.Match | None:
    cuts = list(_BLOCK.finditer(text))
    compiled = re.compile(pattern)
    for start, end in zip([0, *(cut.end() for cut in cuts)], [*(cut.start() for cut in cuts), len(text)]):
        if re.search("laching", text[start:end], re.I) and (match := compiled.search(text, start, end)):
            return match
    return None


def _swift_manifests(config: Config) -> list[Path]:
    """The core package's Package.swift and the XcodeGen spec, those the app has."""
    core, spec = config.path("mac.core", "mac.dir"), config.path("mac.projectSpec", "mac.dir")
    return [file for file in (core / "Package.swift" if core else None, spec) if file and file.is_file()]


def _project_resolved(config: Config) -> Path | None:
    project = config.get("mac.project")
    return config.mac_dir / project / "project.xcworkspace/xcshareddata/swiftpm/Package.resolved" if project else None


def _resolved_files(config: Config) -> list[Path]:
    """Where SwiftPM records the resolved Laching commit: the core package's Package.resolved and the Xcode project's,
    present or not."""
    core = config.path("mac.core", "mac.dir")
    return [file for file in (core / "Package.resolved" if core else None, _project_resolved(config)) if file]


_GRADLE_VERSION = re.compile(r"^lachingVersion=(\S+)\s*$", re.M)


def _submodule(config: Config) -> Path | None:
    submodule = config.path("laching.path")
    return submodule if submodule and (submodule / ".git").exists() else None


def _submodule_tag(submodule: Path) -> str:
    return git(submodule, "describe", "--tags", "--exact-match").stdout.strip()


def pin_sites(config: Config) -> list[Pin]:
    """Every place the Laching release is pinned: the submodule's tag, companion.json's `laching.tag`, the `exact:` of
    the core package's Package.swift, the `exactVersion`/`exact` of the XcodeGen spec, every `Laching.*` NuGet
    reference under the Windows folder, every call of one of Laching's reusable workflows (`uses: .../laching/.github/
    workflows/windows-release.yml@vX.Y.Z`) and, for an app with an Android side, the `lachingVersion` of the submodule's
    `kotlin/gradle.properties` (the Gradle build the app includes). A place the app doesn't have is left out."""
    found: list[Pin] = []
    if submodule := _submodule(config):
        found.append(Pin(f"{config.get('laching.path')} (submodule tag)", _submodule_tag(submodule) or "(not on a tag)"))
    manifest = config.root / FILE
    match = _COMPANION_TAG.search(read_text(manifest))
    found.append(Pin("companion.json laching.tag", config.get("laching.tag", ""), manifest, match.span(1) if match else None))
    for file in _swift_manifests(config):
        match = _laching_block_match(read_text(file), _PACKAGE_EXACT if file.name == "Package.swift" else _SPEC_EXACT)
        version = match.group(1) if match else "(not pinned to a version)"
        found.append(Pin(rel(file, config.root), version, file, match.span(1) if match else None))
    windows = config.path("windows.dir")
    if windows and windows.is_dir():
        for file in sorted([*windows.rglob("*.csproj"), *windows.rglob("Directory.Packages.props")]):
            for match in _NUGET.finditer(read_text(file)):
                found.append(Pin(f"{rel(file, config.root)} {match.group(1)}", match.group(2), file, match.span(2),
                                 match.group(1)))
    workflows = config.root / ".github" / "workflows"
    if workflows.is_dir():
        for file in sorted([*workflows.glob("*.yml"), *workflows.glob("*.yaml")]):
            for match in _WORKFLOW_REF.finditer(read_text(file)):
                found.append(Pin(f"{rel(file, config.root)} {match.group(1)}@", match.group(2), file, match.span(2)))
    if config.get("android") and (gradle := _kotlin_properties(config)) and gradle.is_file():
        match = _GRADLE_VERSION.search(read_text(gradle))
        # No file of its own to rewrite: `bump` moves the submodule and this moves with it.
        found.append(Pin(f"{rel(gradle, config.root)} lachingVersion", match.group(1) if match else "(no lachingVersion)"))
    return found


def _kotlin_properties(config: Config) -> Path | None:
    """The submodule's `kotlin/gradle.properties`, None without a `laching.path`."""
    laching = config.path("laching.path")
    return laching / "kotlin" / "gradle.properties" if laching else None


_LACHING_VERSION = re.compile(r"<LachingVersion[^>]*>([^<]+)</LachingVersion>")


def _nuget_version(submodule: Path, tag: str | None = None) -> str | None:
    """LachingVersion, the one version of Laching's NuGet packages, from its `dotnet/Directory.Build.props` in the
    submodule's checkout or at `tag`: the release they last shipped in, which trails the tag when they didn't change
    since. None when there is none to read."""
    if tag is None:
        file = submodule / "dotnet" / "Directory.Build.props"
        text = read_text(file) if file.is_file() else ""
    else:
        text = git(submodule, "show", f"{tag}:dotnet/Directory.Build.props").stdout
    match = _LACHING_VERSION.search(text)
    return match.group(1) if match else None


def _released_package_version(config: Config, pin: Pin, version: str) -> str | None:
    """The version Laching `version`'s NuGet packages have (LachingVersion at the tag), for a NuGet pin; None for a pin
    of another kind."""
    submodule = _submodule(config)
    return _nuget_version(submodule, f"v{version}") if pin.package and submodule else None


def pins(config: Config) -> list[tuple[str, str]]:
    """Where the Laching release is pinned and the version each place names (`pin_sites`), plus, while the app is
    linked to a Laching checkout, a line that never agrees, so nothing linked gets committed. Every `Laching.*` NuGet
    reference names the release's NuGet version (LachingVersion in the submodule): when that trails the tag, its line
    stands for the release and says so; when a reference names another, its line never agrees."""
    submodule = _submodule(config)
    release = (_submodule_tag(submodule) or "(not on a tag)") if submodule else ""
    nuget = _nuget_version(submodule) if submodule else None
    found = []
    for pin in pin_sites(config):
        wanted = nuget if pin.package and nuget else pin.version
        if pin.version != wanted:
            found.append((f"{pin.label} (the release's NuGet packages are {wanted})", f"{pin.version}, not {wanted}"))
        elif pin.package and nuget and wanted != release.removeprefix("v"):
            found.append((f"{pin.label} {wanted} (NuGet's version in {release})", release))
        else:
            found.append((pin.label, pin.version))
    if linked := linked_to(config):
        found.append((f"linked to {linked}: `companion.py unlink` before committing", "(linked)"))
    return found


def pins_agree(found: list[tuple[str, str]]) -> bool:
    versions = {value.removeprefix("v") for _, value in found}
    return len(versions) == 1 and not next(iter(versions)).startswith("(")


def landing_pin(config: Config) -> Pin | None:
    """The landing page's `@laching/landing` version, None without one. Not one of the pins: the package ships only when
    it changed, so its versions skip releases, and `bump` moves it to npm's newest at or below the release."""
    folder = config.path("landing.dir")
    file = folder / "package.json" if folder else None
    if not file or not file.is_file() or not (match := _LANDING_DEPENDENCY.search(read_text(file))):
        return None
    return Pin(f"{rel(file, config.root)} {_LANDING_PACKAGE}", match.group(1), file, match.span(1))


def print_pins(config: Config) -> int:
    """`pins` for people: one line per place, the landing package below them; 1 when they disagree."""
    found = pins(config)
    agree = pins_agree(found)
    if not agree:
        print("The Laching version must be the same everywhere it's pinned:")
    for where, version in found:
        print(f"  {version:<24} {where}")
    if landing := landing_pin(config):
        print(f"  {landing.version:<24} {landing.label} (ships only when it changes: not compared)")
    return 0 if agree else 1


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _remote_tag(submodule: Path, tag: str) -> str:
    """The commit `tag` names on the submodule's origin; a PinError when origin has no such tag."""
    result = git(submodule, "ls-remote", "--tags", "origin", f"refs/tags/{tag}*")
    if result.returncode:
        raise PinError(f"couldn't list the tags on Laching's origin:\n{_tail(result)}")
    refs = dict(reversed(line.split("\t", 1)) for line in result.stdout.splitlines() if "\t" in line)
    commit = refs.get(f"refs/tags/{tag}^{{}}") or refs.get(f"refs/tags/{tag}")  # an annotated tag's commit, peeled
    if not commit:
        raise PinError(f"{tag} isn't a tag on Laching's origin: release it first")
    return commit


def _repin_resolved(text: str, version: str, commit: str) -> str:
    """A Package.resolved with its `laching` pin at `version` and `commit`."""

    def state(match: re.Match) -> str:
        body = re.sub(r'("revision"\s*:\s*")[^"]*(")', lambda m: m.group(1) + commit + m.group(2), match.group(0))
        return re.sub(r'("version"\s*:\s*")[^"]*(")', lambda m: m.group(1) + version + m.group(2), body)

    return re.sub(r'"identity"\s*:\s*"laching"[^{}]*"state"\s*:\s*\{[^{}]*\}', state, text)


def regenerate_project(config: Config) -> str:
    """`xcodegen generate` for the app's spec; a line for the summary."""
    spec = config.path("mac.projectSpec", "mac.dir")
    if not spec or not spec.is_file():
        return ""
    if not shutil.which("xcodegen"):
        return f"xcodegen isn't installed: run `xcodegen generate` in {rel(spec.parent, config.root)}"
    result = subprocess.run(["xcodegen", "generate", "--quiet", "--spec", str(spec)], cwd=spec.parent,
                            capture_output=True, text=True)
    if result.returncode:
        raise PinError(f"xcodegen generate failed:\n{_tail(result)}")
    return f"regenerated {config.get('mac.project') or 'the Xcode project'}"


def xcode_resolves(config: Config) -> bool:
    """Whether `resolve_packages` can run: a Mac with Xcode, and an Xcode project with a Package.resolved."""
    resolved = _project_resolved(config)
    return bool(resolved and resolved.is_file() and sys.platform == "darwin" and shutil.which("xcodebuild"))


def resolve_packages(config: Config) -> str:
    """`xcodebuild -resolvePackageDependencies`, which writes the Xcode project's Package.resolved the way a build would.
    `bump` leaves that file to it: its originHash covers the checkout's path, which only SwiftPM computes, and SwiftPM
    rewrites the file only when its pin no longer fits the project's requirement."""
    command = ["xcodebuild", "-resolvePackageDependencies", "-project", config.require("mac.project"),
               "-derivedDataPath", str(config.derived_data)]
    if scheme := config.get("mac.scheme"):
        command += ["-scheme", scheme]
    result = subprocess.run(command, cwd=config.mac_dir, capture_output=True, text=True)
    if result.returncode:
        raise PinError(f"xcodebuild -resolvePackageDependencies failed:\n{_tail(result)}")
    return "resolved the Xcode project's packages"


def npm_versions(package: str) -> list[str]:
    """Every version of `package` on npm."""
    # The path `which` found: on Windows npm is npm.cmd, which the bare name doesn't start.
    npm = shutil.which("npm")
    if not npm:
        raise PinError("npm isn't installed")
    try:
        result = subprocess.run([npm, "view", package, "versions", "--json"], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PinError(f"couldn't run npm: {error}") from error
    if result.returncode:
        raise PinError(f"npm didn't answer:\n{_tail(result, 5)}")
    versions = json.loads(result.stdout)
    return versions if isinstance(versions, list) else [versions]


def _modified(folder: Path) -> set[Path]:
    """The tracked files git reports changed under `folder` (none outside a repository)."""
    top = git(folder, "rev-parse", "--show-toplevel")
    status = git(folder, "status", "--porcelain", "--untracked-files=no", "--", ".")
    if top.returncode or status.returncode:
        return set()
    return {(Path(top.stdout.strip()) / line[3:]).resolve() for line in status.stdout.splitlines()}


def _bump_landing(config: Config, version: str) -> tuple[list[str], list[str]]:
    """Moves `@laching/landing` to npm's newest version at or below `version`, and its lockfile with the landing tool's
    `install` when that's safe: the tool is installed and neither file has uncommitted changes to mix in. What changed,
    and notes."""
    pin = landing_pin(config)
    if pin is None or pin.file is None or pin.span is None:
        return [], []
    try:
        published = [v for v in npm_versions(_LANDING_PACKAGE) if re.fullmatch(r"\d+\.\d+\.\d+", v)]
    except PinError as error:
        return [], [f"{_LANDING_PACKAGE} stays at {pin.version}: {error}"]
    target = max((v for v in published if _version_key(v) <= _version_key(version)), key=_version_key, default=None)
    if target is None or _version_key(target) <= _version_key(pin.version):
        return [], [f"{_LANDING_PACKAGE} stays at {pin.version}: npm has nothing newer at or below {version} "
                    "(it ships only when it changes)"]
    folder = pin.file.parent
    lockfile = next((folder / name for name in _LOCKFILES if (folder / name).is_file()), None)
    files = [pin.file, *([lockfile] if lockfile else [])]
    status = git(folder, "status", "--porcelain", "--", *(file.name for file in files))
    dirty = status.returncode == 0 and status.stdout.strip()
    originals = {file: file.read_bytes() for file in files}
    before = _modified(folder)
    text = read_text(pin.file)
    write_text(pin.file, text[: pin.span[0]] + target + text[pin.span[1]:])
    changed = [f"{pin.label}: {pin.version} → {target}"]
    tool = config.get("landing.tool")
    where = rel(folder, config.root)
    found = shutil.which(tool) if tool else None
    if not found:
        return changed, [f"update the lockfile: `{tool or 'npm'} install` in {where}"]
    if dirty:
        return changed, [f"{where} has uncommitted dependency changes, so the lockfile is yours: `{tool} install` there"]
    # The path `which` found: on Windows the tool is a .cmd (pnpm.cmd, npm.cmd), which the bare name doesn't start.
    result = subprocess.run([found, "install"], cwd=folder, capture_output=True, text=True)
    if result.returncode:
        for file, data in originals.items():
            file.write_bytes(data)
        return [], [f"`{tool} install` in {where} failed, so {_LANDING_PACKAGE} stays at {pin.version}:\n{_tail(result)}"]
    # The lockfile, and whatever else the install rewrote (pnpm adds a fresh release to minimumReleaseAgeExclude).
    installed = _modified(folder) - before - {pin.file.resolve()}
    if lockfile and lockfile.read_bytes() != originals[lockfile]:
        installed.add(lockfile.resolve())
    changed += [f"{rel(file, config.root)} (`{tool} install`)" for file in sorted(installed)]
    return changed, []


def bump(config: Config, version: str) -> int:
    """Moves the app to Laching `version`: every pin `pin_sites` finds, the Package.resolved files, the submodule (to the
    tag) and the landing page's package. Refuses while linked, or when origin has no such tag. Prints what changed and
    the pins; 1 when they still disagree."""
    version = version.removeprefix("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise PinError(f"{version!r} isn't a version (X.Y.Z)")
    if linked := linked_to(config):
        raise PinError(f"linked to {linked}: run `companion.py unlink` first")
    tag = f"v{version}"
    submodule = _submodule(config)
    if submodule is None:
        raise PinError(f"bump needs the Laching submodule at {config.get('laching.path') or 'laching.path'}")
    commit = _remote_tag(submodule, tag)
    moved = _move_submodule(config, submodule, tag)
    edited = _bump_pins(config, version)
    by_xcode = (_project_resolved(config) if xcode_resolves(config) and set(edited) & set(_swift_manifests(config))
                else None)
    changed, notes = _bump_resolved(config, version, commit, edited, by_xcode)
    changed = [rel(file, config.root) for file in edited] + changed + moved
    if config.path("mac.projectSpec", "mac.dir") in edited:
        notes.append(regenerate_project(config))
    if by_xcode:
        before = by_xcode.read_bytes()
        notes.append(resolve_packages(config))
        changed += [rel(by_xcode, config.root)] if by_xcode.read_bytes() != before else []
        if _repin_resolved(read_text(by_xcode), version, commit) != read_text(by_xcode):
            notes.append(f"{rel(by_xcode, config.root)} doesn't name {tag} yet: build once in Xcode")
    landing_changed, landing_notes = _bump_landing(config, version)
    print(f"Laching {tag}:" if changed + landing_changed else f"Laching {tag}: every pin already names it, nothing changed")
    for line in changed + landing_changed:
        print(f"  changed  {line}")
    for note in filter(None, notes + landing_notes):
        print(f"  note     {note}")
    return print_pins(load(config.root))  # companion.json may have changed


def _move_submodule(config: Config, submodule: Path, tag: str) -> list[str]:
    """Fetches `tag` into the submodule and checks it out, refusing when the submodule has uncommitted changes; the
    line for the summary, none when it's already there."""
    where = rel(submodule, config.root)
    if _submodule_tag(submodule) == tag:
        return []
    if git(submodule, "status", "--porcelain").stdout.strip():
        raise PinError(f"{where} has uncommitted changes, which would ride along to {tag}")
    fetch = git(submodule, "fetch", "--quiet", "origin", "tag", tag)
    if fetch.returncode:
        raise PinError(f"couldn't fetch {tag} into {where}:\n{_tail(fetch)}")
    checkout = git(submodule, "checkout", "--quiet", tag)
    if checkout.returncode:
        raise PinError(f"couldn't check out {tag} in {where}:\n{_tail(checkout)}")
    return [f"{where} → {tag}"]


def _bump_pins(config: Config, version: str) -> list[Path]:
    """Rewrites every pin `pin_sites` finds in a file to `version` (keeping a `v` prefix); the files it changed."""
    edits: dict[Path, list[tuple[tuple[int, int], str]]] = {}
    for pin in pin_sites(config):
        if pin.file is None:
            continue
        if pin.span is None:
            raise PinError(f"{pin.label} names no version to move ({pin.version or 'empty'})")
        new = ("v" if pin.version.startswith("v") else "") + version
        new = _released_package_version(config, pin, version) or new
        if pin.version != new:
            edits.setdefault(pin.file, []).append((pin.span, new))
    for file, replacements in edits.items():
        text = read_text(file)
        for (start, end), new in sorted(replacements, reverse=True):
            text = text[:start] + new + text[end:]
        write_text(file, text)
    return list(edits)


def _bump_resolved(config: Config, version: str, commit: str, edited: list[Path],
                   by_xcode: Path | None) -> tuple[list[str], list[str]]:
    """Repins Laching in the Package.resolved files (all but `by_xcode`, which Xcode rewrites), the core package's with
    its manifest's originHash; what changed, and notes."""
    core = config.path("mac.core", "mac.dir")
    swift_moved = bool(set(edited) & set(_swift_manifests(config)))
    changed: list[str] = []
    notes: list[str] = []
    for file in _resolved_files(config):
        if not file.is_file() or file == by_xcode:
            continue
        text = read_text(file)
        repinned = _repin_resolved(text, version, commit)
        if file == _project_resolved(config) and swift_moved:
            notes.append(f"{rel(file, config.root)}: run `xcodebuild -resolvePackageDependencies` on a Mac for its originHash")
        if core and file == core / "Package.resolved" and core / "Package.swift" in edited:
            # A package's originHash is the SHA-256 of its manifest. The Xcode project's also covers the checkout's
            # path, so that one is left to Xcode (resolve_packages).
            digest = hashlib.sha256((core / "Package.swift").read_bytes()).hexdigest()
            repinned = re.sub(r'("originHash"\s*:\s*")[0-9a-f]*(")', lambda m: m.group(1) + digest + m.group(2),
                              repinned, count=1)
        if repinned != text:
            write_text(file, repinned)
            changed.append(rel(file, config.root))
    return changed, notes


# The link: the files `link` changed and what they held, kept in the repository's git folder (out of the work tree, one
# per worktree) so `unlink` can put them back.
_LINK_DIR = "laching-link"
LINK_MARKER = "laching link"


class _LinkEdit(NamedTuple):
    file: Path
    start: int
    end: int
    new: str


def _link_dir(config: Config) -> Path:
    result = git(config.root, "rev-parse", "--git-path", _LINK_DIR)
    if result.returncode == 0 and result.stdout.strip():
        path = Path(result.stdout.strip())
        return path if path.is_absolute() else (config.root / path).resolve()
    return config.root / f".{_LINK_DIR}"


def linked_to(config: Config) -> str | None:
    """The Laching checkout the app is linked to (`link`), None when it builds against the pinned release."""
    state = _link_dir(config) / "state.json"
    if state.is_file():
        return json.loads(state.read_text("utf8"))["path"]
    windows = config.path("windows.dir")
    targets = windows / "Directory.Build.targets" if windows else None
    if targets and targets.is_file() and LINK_MARKER in read_text(targets):
        return "a Laching checkout (Directory.Build.targets)"
    android = config.path("android.dir")
    properties = android / "local.properties" if android else None
    if properties and properties.is_file() and LINK_MARKER in read_text(properties):
        return "a Laching checkout (local.properties)"
    return None


def linked_dotnet(config: Config) -> str | None:
    """The folder of Laching's .NET projects the Windows projects build while linked, as the link's
    Directory.Build.targets names it: relative to the Windows folder (`../../laching/dotnet/`) or absolute. None when
    they build the pinned packages."""
    windows = config.path("windows.dir")
    targets = windows / "Directory.Build.targets" if windows else None
    if not targets or not targets.is_file() or LINK_MARKER not in (text := read_text(targets)):
        return None
    match = re.search(r"<LachingLinkedDotnet>\s*(.*?)\s*</LachingLinkedDotnet>", text)
    return match[1].removeprefix("$(MSBuildThisFileDirectory)") if match else None


def _link_path(target: Path, base: Path) -> str:
    """`target` relative to `base` when they share a folder below the root (a sibling checkout: ../../laching), else
    absolute."""
    if os.path.commonpath([target, base]) == os.path.dirname(os.path.commonpath([target, base])):
        return target.as_posix()
    return os.path.relpath(target, base).replace(os.sep, "/")


def _link_msbuild(laching: Path, windows: Path) -> str:
    """The MSBuild that swaps each `Laching.*` PackageReference for a ProjectReference to the checkout's project of the
    same name. A Directory.Build.targets is read after the project's own items, so the projects stay as they are."""
    dotnet = laching / "dotnet"
    names = sorted(file.stem for file in dotnet.glob("*/*.csproj")
                   if file.stem == file.parent.name and "<IsPackable>true</IsPackable>" in read_text(file))
    if not names:
        raise PinError(f"{dotnet} has no packable Laching project")
    path = _link_path(dotnet, windows)
    prefix = "" if os.path.isabs(path) else "$(MSBuildThisFileDirectory)"
    return (
        f"  <!-- {LINK_MARKER}: Laching's packages are the projects in {path}, not NuGet's, until\n"
        "       `companion.py unlink` takes this out. The pin check fails while it's here. -->\n"
        "  <PropertyGroup>\n"
        f"    <LachingLinkedDotnet>{prefix}{path}/</LachingLinkedDotnet>\n"
        "  </PropertyGroup>\n"
        "  <ItemGroup>\n"
        f'    <_LachingLinkable Include="{";".join(names)}" />\n'
        '    <_LachingNotLinked Include="@(PackageReference)" Exclude="@(_LachingLinkable)" />\n'
        '    <_LachingLinked Include="@(PackageReference)" Exclude="@(_LachingNotLinked)" />\n'
        '    <PackageReference Remove="@(_LachingLinked)" />\n'
        "    <ProjectReference Include=\"@(_LachingLinked->'$(LachingLinkedDotnet)%(Identity)/%(Identity).csproj')\" />\n"
        "  </ItemGroup>\n"
    )


def _link_properties(laching: Path, android: Path) -> str:
    """The local.properties lines that point the Android build's `includeBuild` at the checkout's `kotlin/`: the app's
    settings.gradle.kts reads `laching.kotlin.dir` from there before falling back to the submodule's."""
    return (
        f"# {LINK_MARKER}: Laching's Kotlin build is this checkout, not the pinned release, until `companion.py unlink`\n"
        f"laching.kotlin.dir={_link_path(laching / 'kotlin', android)}\n"
    )


def link(config: Config, checkout: Path | None = None) -> int:
    """Switches the app from the pinned release to a Laching checkout (`../laching` next to the app by default): the
    XcodeGen spec's and the core package's Laching become `path:` packages (the project regenerated), the Windows
    projects build Laching's projects instead of its NuGet packages, and the Android build includes the checkout's
    `kotlin/` (`laching.kotlin.dir` in its git-ignored local.properties). The pins fail until `unlink`."""
    laching = (checkout or config.root.parent / "laching").expanduser().resolve()
    manifest = laching / "Package.swift"
    if not manifest.is_file() or 'name: "Laching"' not in read_text(manifest):
        raise PinError(f"{laching} isn't a Laching checkout (no Package.swift naming Laching)")
    if linked := linked_to(config):
        raise PinError(f"already linked to {linked}: `unlink` first")
    edits: list[_LinkEdit] = []
    spec = config.path("mac.projectSpec", "mac.dir")
    if spec and spec.is_file():
        # \r?: a checkout with CRLF line endings (Windows, core.autocrlf); the version line keeps its \r.
        match = re.search(r"^(?P<indent>[ \t]+)url:[ \t]*\S*(?i:laching)\S*[ \t]*\r?\n(?P=indent)"
                          r"(?:exactVersion|exact|version|from|minorVersion|majorVersion):[ \t]*\S+[ \t]*(?=\r?$)",
                          read_text(spec), re.M)
        if not match:
            raise PinError(f"{rel(spec, config.root)}: no Laching package with a url and a version to switch")
        edits.append(_LinkEdit(spec, *match.span(), f"{match['indent']}path: {_link_path(laching, spec.parent)}"))
    core = config.path("mac.core", "mac.dir")
    if core and (core / "Package.swift").is_file():
        match = re.search(r'\.package\(\s*url:\s*"[^"]*(?i:laching)[^"]*"\s*,\s*exact:\s*"[^"]*"\s*\)',
                          read_text(core / "Package.swift"))
        if not match:
            raise PinError(f"{rel(core / 'Package.swift', config.root)}: no Laching `.package(url:…, exact:…)` to switch")
        # Named: SwiftPM calls a path package after its folder, and a worktree's isn't `laching`.
        edits.append(_LinkEdit(core / "Package.swift", *match.span(),
                               f'.package(name: "laching", path: "{_link_path(laching, core)}")'))
    windows = config.path("windows.dir")
    if windows and any(pin.file and pin.file.is_relative_to(windows) for pin in pin_sites(config)):
        targets = windows / "Directory.Build.targets"
        block = _link_msbuild(laching, windows)
        if targets.is_file():
            text = read_text(targets)
            if (at := text.rfind("</Project>")) < 0:
                raise PinError(f"{rel(targets, config.root)} has no </Project> to add the link before")
            edits.append(_LinkEdit(targets, at, at, block.replace("\n", "\r\n") if "\r\n" in text else block))
        else:
            edits.append(_LinkEdit(targets, 0, 0, f"<Project>\n{block}</Project>\n"))
    android = config.path("android.dir")
    if android and android.is_dir() and (laching / "kotlin" / "settings.gradle.kts").is_file():
        properties = android / "local.properties"
        text = read_text(properties) if properties.is_file() else ""
        lead = "" if not text or text.endswith("\n") else "\n"
        edits.append(_LinkEdit(properties, len(text), len(text), lead + _link_properties(laching, android)))
    if not edits:
        raise PinError("nothing here pins Laching to link")

    # What SwiftPM and XcodeGen derive from the edited files: put back as they were, whatever the linked builds wrote.
    project = config.get("mac.project")
    derived = [*_resolved_files(config), *([config.mac_dir / project / "project.pbxproj"] if project and spec else [])]
    folder = _link_dir(config)
    entries = []
    for file in [*(edit.file for edit in edits), *derived]:
        if file.is_file():
            saved = folder / "files" / rel(file, config.root)
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(file.read_bytes())
    for edit in edits:
        text = read_text(edit.file) if edit.file.is_file() else ""
        linked = text[: edit.start] + edit.new + text[edit.end:]
        entries.append({"path": rel(edit.file, config.root), "original": text[edit.start: edit.end], "linked": edit.new,
                        "linkedHash": hashlib.sha256(linked.encode("utf8")).hexdigest()})
        write_text(edit.file, linked)
    entries += [{"path": rel(file, config.root), "derived": True} for file in derived]
    (folder / "state.json").write_text(json.dumps({"path": str(laching), "files": entries}, indent=2) + "\n", "utf8")

    print(f"Linked to {laching}:")
    for edit in edits:
        print(f"  changed  {rel(edit.file, config.root)}")
    if spec and (note := regenerate_project(config)):
        print(f"  note     {note}")
    if laching.name.lower() != "laching":
        print(f"  note     SwiftPM names a local package after its folder, so the products' package \"laching\" needs "
              f"the checkout's folder named laching, not {laching.name}")
    print("Build and test as usual; `companion.py unlink` before committing.")
    print_pins(config)
    return 0


def unlink(config: Config) -> int:
    """Undoes `link`: each file it changed gets its bytes back (only the link's own edit is undone in a file changed
    since), the files SwiftPM and XcodeGen derive are put back, and the project is regenerated."""
    folder = _link_dir(config)
    state_file = folder / "state.json"
    if not state_file.is_file():
        if linked := linked_to(config):
            raise PinError(f"linked to {linked}, but there's no record of the link to undo: put the files back by hand")
        print("Not linked: nothing to undo.")
        return 0
    state = json.loads(state_file.read_text("utf8"))
    problems: list[str] = []
    restored: list[str] = []
    for entry in state["files"]:
        file = config.root / entry["path"]
        saved = folder / "files" / entry["path"]
        original = saved.read_bytes() if saved.is_file() else None
        current = file.read_bytes() if file.is_file() else None
        if entry.get("derived") or (current is not None and hashlib.sha256(current).hexdigest() == entry["linkedHash"]):
            if current == original:
                continue
            if original is None:
                file.unlink()
                restored.append(f"{entry['path']} (removed)")
            else:
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(original)
                restored.append(entry["path"])
        elif current is not None and entry["linked"] and current.decode("utf8").count(entry["linked"]) == 1:
            # Changed since the link (another edit to the same file): take out only the link's edit.
            text = current.decode("utf8").replace(entry["linked"], entry["original"], 1)
            if original is None and not text.strip():
                file.unlink()
            else:
                write_text(file, text)
            restored.append(f"{entry['path']} (the link's edit only: the file changed since)")
        else:
            problems.append(f"{entry['path']}: the link's edit is gone, so put back by hand:\n{entry['original']}")
    shutil.rmtree(folder)
    print("Unlinked:" if restored else "Unlinked: every file was already back")
    for line in restored:
        print(f"  restored {line}")
    spec = config.path("mac.projectSpec", "mac.dir")
    if spec and any(entry["path"] == rel(spec, config.root) for entry in state["files"]):
        try:
            note = regenerate_project(config)
        except PinError as error:
            problems.append(str(error))
        else:
            if note:
                print(f"  note     {note}")
    for problem in problems:
        print(f"  problem  {problem}")
    status = print_pins(config)
    return 1 if problems else status


# MARK: Validation (the subset of JSON Schema the schema uses)

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "null": type(None),
}


def _is(value, kind: str) -> bool:
    if kind in ("number", "integer") and isinstance(value, bool):
        return False
    return isinstance(value, _TYPES[kind])


def validate(data, schema: dict | None = None, where: str = "") -> list[str]:
    """Every way `data` breaks `schema` (companion.schema.json by default), one message each."""
    if schema is None:
        schema = json.loads(SCHEMA.read_text("utf8"))
    label = where or "(root)"
    problems: list[str] = []
    kinds = schema.get("type")
    if kinds is not None:
        kinds = [kinds] if isinstance(kinds, str) else kinds
        if not any(_is(data, kind) for kind in kinds):
            return [f"{label}: expected {' or '.join(kinds)}, got {type(data).__name__}"]
    if "enum" in schema and data not in schema["enum"]:
        problems.append(f"{label}: {data!r} is not one of {schema['enum']}")
    if isinstance(data, str) and "pattern" in schema and not re.search(schema["pattern"], data):
        problems.append(f"{label}: {data!r} doesn't match {schema['pattern']}")
    if isinstance(data, dict):
        for key in schema.get("required", []):
            if key not in data:
                problems.append(f"{label}: missing {key}")
        properties = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, value in data.items():
            path = f"{where}.{key}" if where else key
            if key in properties:
                problems += validate(value, properties[key], path)
            elif extra is False:
                problems.append(f"{label}: unknown key {key}")
            elif isinstance(extra, dict):
                problems += validate(value, extra, path)
    if isinstance(data, list):
        if len(data) < schema.get("minItems", 0):
            problems.append(f"{label}: needs at least {schema['minItems']} items")
        if "items" in schema:
            for index, item in enumerate(data):
                problems += validate(item, schema["items"], f"{where}[{index}]")
    return problems


def consistency(data: dict) -> list[str]:
    """Checks the schema can't express: the source and default voice are among the lists that name them."""
    problems = []
    languages = data.get("languages", {})
    if languages.get("source") not in languages.get("all", []):
        problems.append("languages.source must be in languages.all")
    voice = data.get("voice") or {}
    if voice and voice.get("defaultVoice") not in voice.get("voices", []):
        problems.append("voice.defaultVoice must be in voice.voices")
    for lang in (data.get("l10n") or {}).get("pluralCategories", {}):
        if lang not in languages.get("all", []):
            problems.append(f"l10n.pluralCategories names {lang}, which isn't in languages.all")
    return problems


def load(root: Path | None = None, check: bool = True) -> Config:
    """The companion.json at `root` (found with `find_root` when None). Exits with the problems when it's invalid."""
    try:
        root = root or find_root()
        data = json.loads((root / FILE).read_text("utf8"))
    except (ConfigError, json.JSONDecodeError) as error:
        sys.exit(f"companion: {error}")
    if check:
        problems = validate(data) + consistency(data)
        if problems:
            sys.exit("companion: " + str(root / FILE) + " is invalid:\n  " + "\n  ".join(problems))
    return Config(data, root)


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 2
    command, rest = argv[0], argv[1:]
    if command == "apps":
        import conform

        laching = Path(__file__).resolve().parent.parent
        return conform.apps([Path(app).resolve() for app in rest] or conform.app_folders(laching), laching)
    if command == "validate":
        files = [Path(f) for f in rest] or [find_root() / FILE]
        status = 0
        for file in files:
            data = json.loads(file.read_text("utf8"))
            problems = validate(data) + consistency(data)
            print(f"{file}: " + ("ok" if not problems else "\n  " + "\n  ".join(problems)))
            status |= bool(problems)
        return status
    # conform reports what the schema refuses with the rest of its problems, and bump moves an app whose companion.json
    # the release's schema refuses, so the app's verify.sh says what to change.
    config = load(check=command not in ("conform", "bump"))
    if command == "root":
        print(config.root)
    elif command == "env":
        import shlex

        for key, value in derived(config).items():
            print(f"{key}={shlex.quote(value)}")
    elif command == "pins":
        return print_pins(config)
    elif command == "conform":
        import conform

        return conform.check(config)
    elif command == "checks":
        for entry in config.get("verify.checks", []):
            print("	".join(["slow" if entry.get("slow") else "fast", "docs" if entry.get("docs") else "code",
                             entry["paths"], entry["name"], entry["run"]]))
    elif (command, len(rest)) in (("bump", 1), ("link", 0), ("link", 1), ("unlink", 0)):
        try:
            if command == "bump":
                return bump(config, rest[0])
            if command == "link":
                return link(config, Path(rest[0]) if rest else None)
            return unlink(config)
        except PinError as error:
            print(f"companion {command}: {error}", file=sys.stderr)
            return 1
    elif command in ("get", "path") and len(rest) == 1:
        if command == "path":
            value = config.path(rest[0])
            if value is None:
                return 1
            print(value)
            return 0
        value = config.get(rest[0])
        if value is None:
            return 1
        print(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    utf8_console()
    sys.exit(main(sys.argv[1:]))
