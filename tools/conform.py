"""Whether a companion app uses Laching's tools rather than copies of them (`companion.py conform`), and where the apps
next to this checkout stand against its releases (`companion.py apps`). Standard library only.

conform fails, one line per problem, when the app:
  - keeps a file named like one of Laching's tools (smoke-package.ps1, check-winui.py, push.py, import.py,
    store-screenshots.*, verify.sh, ...: every script under tools/, by name without its extension, and the names the
    tools replaced, such as store-packages.ps1) that isn't a thin wrapper: at most WRAPPER_LINES lines of code (comments
    and blank lines aside) that name `<laching.path>/tools/`;
  - builds or bundles its Store packages in a workflow of its own (GenerateAppxPackageOnBuild, makeappx, smoke-package)
    instead of calling Laching's reusable windows-release.yml and windows-app.yml, or calls them at another release than
    the one it pins (`laching.tag`); with `windows.app`, it must call both;
  - has a skill in .agents/skills named like one of tools/skills that isn't a link into `<laching.path>/tools/skills/`
    (the app's own words for a skill go in .agents/notes/<skill>.md);
  - has a `store.microsoft` the schema refuses.
`conform.allow` in companion.json lists the files that are the app's own on purpose, each with why.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import companion  # noqa: E402

WRAPPER_LINES = 12
SCRIPT_SUFFIXES = {".py", ".sh", ".ps1"}
# Names apps gave what a tool now does, by the tool that does it.
RETIRED = {"store-packages": "windows/store-release.ps1"}
# Where an app's workflow builds its own Store packages.
FORKED_BUILD = re.compile(r"GenerateAppxPackageOnBuild|makeappx|smoke-package")
REUSABLE = re.compile(r"uses:\s*['\"]?[\w.-]+/laching/\.github/workflows/(windows-release|windows-app)\.yml@([\w.-]+)")
SKIPPED_DIRS = {"node_modules", "build", ".git"}


def tool_names(tools: Path = TOOLS) -> dict[str, str]:
    """Every tool script's name without its extension → its path under tools/ (tests, skills and the landing package
    aside), plus the retired names."""
    names = dict(RETIRED)
    for path in sorted(tools.rglob("*")):
        parts = path.relative_to(tools).parts
        if path.suffix in SCRIPT_SUFFIXES and parts[0] not in ("tests", "skills", "landing") and path.is_file():
            names.setdefault(path.stem, path.relative_to(tools).as_posix())
    return names


def tracked(root: Path) -> list[str]:
    """The app's files, root-relative with forward slashes: what git tracks or would (submodules are one entry each), else every
    file outside the usual build folders."""
    done = companion.git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if done.returncode == 0:
        return [name for name in done.stdout.split("\0") if name]
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*")
                  if path.is_file() and not SKIPPED_DIRS & set(path.relative_to(root).parts))


def code_lines(text: str, suffix: str) -> list[str]:
    """The lines that do something: no blank lines, comments, PowerShell comment blocks or a Python docstring."""
    lines, block = [], None
    for line in text.splitlines():
        stripped = line.strip()
        if block:
            if block in stripped:
                block = None
            continue
        if not stripped or stripped.startswith("#") and not stripped.startswith("#>"):
            continue
        if suffix == ".ps1" and stripped.startswith("<#"):
            block = None if "#>" in stripped[2:] else "#>"
            continue
        if suffix == ".py" and stripped[:3] in ('"""', "'''") and not lines:
            quote = stripped[:3]
            block = None if stripped.count(quote) >= 2 and len(stripped) > 3 else quote
            continue
        lines.append(stripped)
    return lines


def delegates(text: str, laching: str) -> bool:
    """Whether a script names Laching's tools folder: laching/tools/... with either slash, or os.path.join's
    "laching", "tools"."""
    flat = re.sub(r"""["']\s*,\s*["']""", "/", text.replace("\\", "/"))
    return f"{laching.strip('/')}/tools/" in flat


def wrapper_problems(config: companion.Config, files: list[str], allowed: set[str]) -> list[str]:
    names = tool_names()
    laching = config.get("laching.path", "laching")
    problems = []
    for name in files:
        path = Path(name)
        if path.suffix not in SCRIPT_SUFFIXES and path.stem not in RETIRED:
            continue
        if path.stem not in names or name in allowed or name.startswith(laching.rstrip("/") + "/"):
            continue
        file = config.root / name
        if not file.is_file():
            continue
        try:
            text = file.read_text("utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        # store-listing/microsoft/push.py is push-microsoft.py's copy, not push.py's.
        tool = f"{laching}/tools/{names.get(f'{path.stem}-{path.parent.name}', names[path.stem])}"
        if not delegates(text, laching):
            problems.append(f"{name}: a copy of {tool}: delete it and run the tool, make it a wrapper that calls it, "
                            "or, when it is the app's own and not a copy, list it in conform.allow with why")
        elif len(code_lines(text, path.suffix)) > WRAPPER_LINES:
            problems.append(f"{name}: {len(code_lines(text, path.suffix))} lines of code around {tool}: a wrapper only "
                            f"calls the tool (at most {WRAPPER_LINES}); move the rest into the tool, or into "
                            "conform.allow with why")
    return problems


def workflow_problems(config: companion.Config, files: list[str]) -> list[str]:
    tag = config.get("laching.tag")
    problems, called = [], set()
    for name in files:
        if not re.match(r"\.github/workflows/[^/]+\.ya?ml$", name):
            continue
        try:
            text = (config.root / name).read_text("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        if match := FORKED_BUILD.search(code):
            problems.append(f"{name}: builds the Store packages itself ({match.group(0)}): call Laching's "
                            "windows-release.yml or windows-app.yml (uses: vmihalachi/laching/.github/workflows/...)")
        for workflow, ref in REUSABLE.findall(code):
            called.add(workflow)
            if tag and ref != tag:
                problems.append(f"{name}: calls {workflow}.yml@{ref}, but the app pins Laching {tag}: call it @{tag}")
    if config.get("windows.app"):
        for workflow in ("windows-release", "windows-app"):
            if workflow not in called:
                problems.append(f".github/workflows: no workflow calls Laching's {workflow}.yml (the app has windows.app)")
    return problems


def link_target(root: Path, name: str) -> str | None:
    """Where the skill at `name` links: git's record of the link (mode 120000), else the file system's (a symlink, or
    the text file a checkout without symlinks leaves); None for a folder of its own."""
    done = companion.git(root, "ls-files", "-s", "--", name)
    entries = [line.split() for line in done.stdout.splitlines()] if done.returncode == 0 else []
    if entries:
        if len(entries) == 1 and entries[0][0] == "120000" and entries[0][3] == name:
            blob = companion.git(root, "cat-file", "-p", entries[0][1])
            return blob.stdout.strip()
        return None
    path = root / name
    if path.is_symlink():
        return Path(path.readlink()).as_posix()
    if path.is_file() and path.stat().st_size < 256:
        return path.read_text("utf-8").strip()
    return None


def skill_problems(config: companion.Config) -> list[str]:
    folder = config.root / ".agents" / "skills"
    laching = config.get("laching.path", "laching").strip("/")
    ours = {path.name for path in (TOOLS / "skills").iterdir() if (path / "SKILL.md").is_file()}
    problems = []
    if not folder.is_dir():
        return problems
    for entry in sorted(folder.iterdir()):
        if entry.name not in ours:
            continue
        name = f".agents/skills/{entry.name}"
        target = link_target(config.root, name)
        want = f"../../{laching}/tools/skills/{entry.name}"
        if target is None:
            problems.append(f"{name}: a copy of {laching}/tools/skills/{entry.name}: link it (ln -s {want} {name}) and "
                            f"move what is the app's own to .agents/notes/{entry.name}.md")
        elif target.rstrip("/") != want:
            problems.append(f"{name}: links to {target}, not {want}")
    return problems


def store_problems(config: companion.Config) -> list[str]:
    microsoft = config.get("store.microsoft")
    if microsoft is None:
        return []
    schema = json.loads(companion.SCHEMA.read_text("utf8"))
    part = schema["properties"]["store"]["properties"]["microsoft"]
    return [f"companion.json: {problem}" for problem in companion.validate(microsoft, part, "store.microsoft")]


def problems(config: companion.Config) -> list[str]:
    """Every way the app forks or drifts from Laching's tools, one message each."""
    allowed = {entry["path"] for entry in config.get("conform.allow", [])}
    files = tracked(config.root)
    return (wrapper_problems(config, files, allowed) + workflow_problems(config, files) + skill_problems(config)
            + store_problems(config))


def check(config: companion.Config) -> int:
    found = problems(config)
    for problem in found:
        print(f"conform: {problem}")
    if found:
        print(f"{len(found)} problem(s): the app should use Laching's tools, not copies (laching/tools/conform.py says "
              "what each rule wants)", file=sys.stderr)
        return 1
    print("conform: ok")
    return 0


# MARK: apps


def _tags(checkout: Path) -> list[str]:
    done = companion.git(checkout, "tag", "--list", "v*", "--sort=-v:refname")
    return [tag for tag in done.stdout.split() if re.fullmatch(r"v\d+\.\d+\.\d+", tag)]


def app_folders(laching: Path) -> list[Path]:
    """The companion apps next to this Laching checkout (a folder with a companion.json; not a git worktree)."""
    return [manifest.parent for manifest in sorted(laching.parent.glob(f"*/{companion.FILE}"))
            if (manifest.parent / ".git").is_dir()]


def apps(folders: list[Path], laching: Path = TOOLS.parent) -> int:
    """One line per app: the release it pins, how far behind the newest that is, and whether it conforms; 1 when the
    apps pin different releases or one is behind."""
    tags = _tags(laching)
    latest = tags[0] if tags else None
    if latest:
        ahead = companion.git(laching, "rev-list", "--count", f"{latest}..HEAD").stdout.strip()
        print(f"Laching: newest release {latest}, this checkout {ahead or '?'} commit(s) past it")
    pins, drift = {}, False
    for folder in folders:
        try:
            config = companion.load(folder, check=False)
        except SystemExit as error:
            print(f"  {folder.name}: {error}")
            drift = True
            continue
        tag = config.get("laching.tag") or "?"
        pins[folder.name] = tag
        disagree = not companion.pins_agree(companion.pins(config))
        behind = tags.index(tag) if tag in tags else None
        conform = problems(config)
        notes = [f"{behind} release(s) behind" if behind else "newest" if behind == 0 else "not a release tag"]
        if disagree:
            notes.append("its pins disagree (companion.py pins)")
        notes.append(f"{len(conform)} conform problem(s)" if conform else "conforms")
        print(f"  {folder.name}: {tag} ({', '.join(notes)})")
        drift |= bool(behind) or behind is None or disagree
    if len(set(pins.values())) > 1:
        print("The apps pin different releases: tools/bump-apps.sh X.Y.Z moves them together.")
        drift = True
    return 1 if drift else 0
