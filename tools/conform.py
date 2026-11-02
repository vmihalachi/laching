"""Whether a companion app uses Laching's tools rather than copies of them (`companion.py conform`), and where the apps
next to this checkout stand against its releases (`companion.py apps`). Standard library only.

conform fails, one line per problem, when the app:
  - keeps a file named like one of Laching's tools (smoke-package.ps1, check-winui.py, push.py, import.py,
    store-screenshots.*, verify.sh, ...: every script under tools/, by name without its extension, and the names the
    tools replaced, such as store-packages.ps1) that isn't a thin wrapper: at most WRAPPER_LINES lines of code (comments
    and blank lines aside) that name `<laching.path>/tools/`;
  - has a wrapper that doesn't name its own tool, set COMPANION_ROOT, forward every argument and keep the exit status
    (WRAPPER_FORM), or a setup-linux.sh that runs Laching's before checking out the submodule a fresh clone lacks;
  - builds or bundles its Store packages in a workflow of its own (GenerateAppxPackageOnBuild, makeappx, smoke-package)
    instead of calling Laching's reusable windows-release.yml and windows-app.yml, or calls any shared workflow at another release than
    the one it pins (`laching.tag`); with `windows.app`, it must call both;
  - keeps a workflow Laching ships, by file name, instead of using Laching's: its .github/workflows/core.yml must call
    core-linux.yml, its regenerate-vectors.yml vectors-linux.yml, and its copilot-setup-steps.yml must run
    setup-linux.sh (which Claude Code's cloud sessions run too);
  - has a skill in .agents/skills named like one of tools/skills that isn't a link into `<laching.path>/tools/skills/`
    (the app's own words for a skill go in .agents/notes/<skill>.md);
  - lacks a link Claude Code, Copilot or Junie finds the skills through: .claude/skills/<skill> to
    ../../.agents/skills/<skill> for each skill, and .github/skills and .junie/skills to ../.agents/skills;
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
REUSABLE = re.compile(r"uses:\s*['\"]?[\w.-]+/laching/\.github/workflows/([\w.-]+)\.ya?ml@([\w.-]+)")
SKIPPED_DIRS = {"node_modules", "build", ".git"}
# An app's workflows named like the ones Laching ships (by name without the extension) → the reusable workflow it calls.
CALLERS = {"core": "core-linux", "regenerate-vectors": "vectors-linux"}
# Copilot's setup job can't call a reusable workflow, so it runs the setup Claude Code's cloud sessions run.
COPILOT_SETUP = "copilot-setup-steps"
SETUP_LINUX = re.compile(r"setup-linux\.sh\b")
# What a wrapper does besides calling its tool, by kind: (what's missing, the code that does it). It sets the app's root,
# so the tool works from any folder, forwards every argument and keeps the exit status.
WRAPPER_FORM = {
    ".sh": (("set COMPANION_ROOT", r"COMPANION_ROOT="), ('forward its arguments ("$@")', r'"\$@"'),
            ("keep the exit status (exec)", r"(^|[\s;&|])exec\s")),
    ".py": (("set COMPANION_ROOT", r"COMPANION_ROOT"), ("forward its arguments (sys.argv[1:])", r"sys\.argv\[1:\]"),
            ("keep the exit status (os.execv, or sys.exit(subprocess.call(...)))",
             r"os\.exec\w*\(|(sys\.exit|SystemExit)\((subprocess\.call\(|.*\.returncode)")),
    ".ps1": (("set $env:COMPANION_ROOT", r"\$env:COMPANION_ROOT\s*="), ("forward its arguments (@args)", r"@args\b"),
             ("keep the exit status (exit $LASTEXITCODE)", r"exit\s+\$LASTEXITCODE")),
}
# setup-linux.sh's first step in a clone made without its submodules.
BOOTSTRAP = re.compile(r"\bgit\b.*\bsubmodule\s+update\b.*--init")


def tool_paths(tools: Path = TOOLS) -> list[str]:
    """Every tool script's path under tools/ (tests, skills and the landing package aside)."""
    return [path.relative_to(tools).as_posix() for path in sorted(tools.rglob("*"))
            if path.suffix in SCRIPT_SUFFIXES and path.relative_to(tools).parts[0] not in ("tests", "skills", "landing")
            and path.is_file()]


def tool_names(tools: Path = TOOLS) -> dict[str, str]:
    """Every tool script's name without its extension → its path under tools/, plus the retired names."""
    names = dict(RETIRED)
    for path in tool_paths(tools):
        names.setdefault(Path(path).stem, path)
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


def flat(text: str) -> str:
    """The script with forward slashes and os.path.join's "laching", "tools" as laching/tools."""
    return re.sub(r"""["']\s*,\s*["']""", "/", text.replace("\\", "/"))


def delegates(text: str, laching: str) -> bool:
    """Whether a script names Laching's tools folder: laching/tools/... with either slash, or os.path.join's
    "laching", "tools"."""
    return f"{laching.strip('/')}/tools/" in flat(text)


def form_problems(code: list[str], suffix: str, tool: str, laching: str) -> list[str]:
    """What a wrapper's code (code_lines) leaves out of WRAPPER_FORM, its own tool's name first."""
    text = "\n".join(code)
    stem = re.escape(Path(tool).stem)
    missing = [] if re.search(rf"{re.escape(laching.strip('/'))}/tools/([\w.-]+/)*{stem}\.(py|sh|ps1)\b", flat(text)) \
        else [f"call {tool}"]
    return missing + [what for what, pattern in WRAPPER_FORM[suffix] if not re.search(pattern, text, re.MULTILINE)]


def bootstrapped(code: list[str], laching: str) -> bool:
    """Whether setup-linux.sh checks out the submodule no later than the first line that names Laching's tools."""
    first = next((i for i, line in enumerate(code) if delegates(line, laching)), len(code))
    return any(BOOTSTRAP.search(line) for line in code[:first + 1])


def wrapper_problems(config: companion.Config, files: list[str], allowed: set[str]) -> list[str]:
    names = tool_names()
    # screenshots.ps1 wraps windows/screenshots.ps1, not screenshots.sh.
    alike = {Path(path).name: path for path in reversed(tool_paths())}
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
        # store-listing/microsoft/push.py is push-microsoft.py's copy, not push.py's (google/push.py push-google.py's).
        own = names.get(f"{path.stem}-{path.parent.name}") or alike.get(path.name) or names[path.stem]
        tool = f"{laching}/tools/{own}"
        if not delegates(text, laching):
            problems.append(f"{name}: a copy of {tool}: delete it and run the tool, make it a wrapper that calls it, "
                            "or, when it is the app's own and not a copy, list it in conform.allow with why")
        elif len(code := code_lines(text, path.suffix)) > WRAPPER_LINES:
            problems.append(f"{name}: {len(code)} lines of code around {tool}: a wrapper only "
                            f"calls the tool (at most {WRAPPER_LINES}); move the rest into the tool, or into "
                            "conform.allow with why")
        elif path.suffix in WRAPPER_FORM and (missing := form_problems(code, path.suffix, tool, laching)):
            said = missing[0] if len(missing) == 1 else f"{', '.join(missing[:-1])} or {missing[-1]}"
            problems.append(f"{name}: a wrapper of {tool} that doesn't {said}: see the templates in "
                            f"{laching}/tools/README.md, *Staying on Laching's tools*")
        if path.stem == "setup-linux" and delegates(text, laching) and not bootstrapped(code_lines(text, path.suffix), laching):
            problems.append(f"{name}: runs {tool} before checking out {laching}/, which a clone made without its "
                            f"submodules lacks: check it out first ([ -f ... ] || git -C \"$COMPANION_ROOT\" submodule "
                            f"update --init {laching})")
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


def shared_workflow_problems(config: companion.Config, files: list[str], allowed: set[str]) -> list[str]:
    """The app's core.yml, regenerate-vectors.yml and copilot-setup-steps.yml, by file name, each a copy unless it uses
    Laching's (a renamed copy passes)."""
    tag = config.get("laching.tag") or "vX.Y.Z"
    problems = []
    for name in files:
        match = re.fullmatch(r"\.github/workflows/([^/]+)\.ya?ml", name)
        if not match or name in allowed or match.group(1) not in (*CALLERS, COPILOT_SETUP):
            continue
        try:
            text = (config.root / name).read_text("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        if match.group(1) == COPILOT_SETUP:
            if not SETUP_LINUX.search(code):
                problems.append(f"{name}: sets up its own tools: its steps are actions/checkout with submodules, "
                                "actions/setup-node for Node 24 and `run: scripts/setup-linux.sh`, which installs what "
                                "Claude Code's cloud sessions get")
        elif (reusable := CALLERS[match.group(1)]) not in {workflow for workflow, _ in REUSABLE.findall(code)}:
            problems.append(f"{name}: a copy of Laching's {reusable}.yml: call it instead "
                            f"(uses: vmihalachi/laching/.github/workflows/{reusable}.yml@{tag})")
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


def present(root: Path, name: str) -> bool:
    """Whether the app has `name`, as a file, folder or link, on disk or in git."""
    path = root / name
    if path.exists() or path.is_symlink():
        return True
    done = companion.git(root, "ls-files", "--", name)
    return done.returncode == 0 and bool(done.stdout.strip())


def skill_link_problems(config: companion.Config, allowed: set[str]) -> list[str]:
    """The links the agents find .agents/skills through: per skill from .claude/skills, the whole folder from
    .github/skills (Copilot) and .junie/skills."""
    folder = config.root / ".agents" / "skills"
    if not folder.is_dir():
        return []
    want = {name: "../.agents/skills" for name in (".github/skills", ".junie/skills")}
    for entry in sorted(folder.iterdir()):
        if entry.is_dir() or entry.is_symlink() or link_target(config.root, f".agents/skills/{entry.name}"):
            want[f".claude/skills/{entry.name}"] = f"../../.agents/skills/{entry.name}"
    problems = []
    for name, target in want.items():
        if name in allowed:
            continue
        if not present(config.root, name):
            problems.append(f"{name}: missing: ln -s {target} {name}")
        elif (found := link_target(config.root, name)) is None:
            problems.append(f"{name}: a folder of its own: make it a link (ln -s {target} {name})")
        elif found.rstrip("/") != target:
            problems.append(f"{name}: links to {found}, not {target}")
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
    return (wrapper_problems(config, files, allowed) + workflow_problems(config, files)
            + shared_workflow_problems(config, files, allowed) + skill_problems(config)
            + skill_link_problems(config, allowed) + store_problems(config))


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
