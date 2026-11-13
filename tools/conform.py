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
  - has `mac` but no workflow that calls Laching's macos-app.yml;
  - keeps a workflow Laching ships, by file name, instead of using Laching's: its .github/workflows/core.yml must call
    core-linux.yml, its regenerate-vectors.yml vectors-linux.yml, and its copilot-setup-steps.yml must run
    setup-linux.sh (which Claude Code's cloud sessions run too);
  - has a workflow that runs only on workflow_dispatch and sets `cancel-in-progress: true`: no push supersedes a run
    there, so a second dispatch on the same branch would only cancel a run that is nearly done;
  - has a .claude/settings.json whose hooks block lacks one of Laching's hooks (HOOKS: the event, its matcher, the
    command and the timeout; the app's own hooks, its permissions and every other key are the app's), or calls
    `<laching.path>/tools/hooks/hook.py` itself, there or in Codex's and Copilot's hooks (.codex/config.toml,
    .github/hooks/*.json), instead of through the app's one wrapper, .claude/hooks/hook.py;
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
REUSABLE = re.compile(r"uses:\s*['\"]?[\w.-]+/laching/\.github/workflows/([\w.-]+)\.ya?ml@([\w./-]+)")
SKIPPED_DIRS = {"node_modules", "build", ".git"}
# An app's workflows named like the ones Laching ships (by name without the extension) → the reusable workflow it calls.
CALLERS = {"core": "core-linux", "regenerate-vectors": "vectors-linux"}
# Copilot's setup job can't call a reusable workflow, so it runs the setup Claude Code's cloud sessions run.
COPILOT_SETUP = "copilot-setup-steps"
SETUP_LINUX = re.compile(r"setup-linux\.sh\b")
# What a wrapper does besides calling its tool, by kind: (what's missing, the code that does it). It hands the tool the
# app's root, so the tool works from any folder, forwards every argument and keeps the exit status. A shell variable
# reaches the tool only exported, or as an assignment in front of the exec itself (not one ended by ;, && or ||).
RUNPY = r"""runpy\.run_path\((.|\n)*?run_name\s*=\s*["']__main__["']"""
WRAPPER_FORM = {
    ".sh": (("export COMPANION_ROOT", r"^\s*export\s+([^#\n]*\s)?COMPANION_ROOT\b"
             r"|^[ \t]*COMPANION_ROOT=(?!.*(;|&&|\|\|)[ \t]*exec\b).*[ \t]exec[ \t]"),
            ('forward its arguments ("$@")', r'"\$@"'),
            ("keep the exit status (exec)", r"(^|[\s;&|])exec\s")),
    # runpy.run_path(..., run_name="__main__") runs the tool in this process: sys.argv[1:] and its SystemExit carry over.
    # Without run_name the tool's main block never runs, and the wrapper quietly does nothing.
    ".py": (("set os.environ[\"COMPANION_ROOT\"]", r"""os\.environ\[\s*["']COMPANION_ROOT["']\s*\]\s*=(?!=)"""
             r"""|os\.environ\.(setdefault|update)\(.*COMPANION_ROOT|["']COMPANION_ROOT["']\s*:"""),
            ('forward its arguments (sys.argv[1:], or runpy.run_path(..., run_name="__main__"))',
             rf"sys\.argv\[1:\]|{RUNPY}"),
            ('keep the exit status (os.execv, sys.exit(subprocess.call(...)) or runpy.run_path(..., run_name="__main__"))',
             rf"os\.exec\w*\(|(sys\.exit|SystemExit)\((subprocess\.call\(|.*\.returncode)|{RUNPY}")),
    # PowerShell's names ignore case.
    ".ps1": (("set $env:COMPANION_ROOT", r"(?i)\$env:COMPANION_ROOT\s*="), ("forward its arguments (@args)", r"(?i)@args\b"),
             ("keep the exit status (exit $LASTEXITCODE)", r"(?i)exit\s+\$LASTEXITCODE")),
}
# setup-linux.sh's first step in a clone made without its submodules.
BOOTSTRAP = re.compile(r"\bgit\b.*\bsubmodule\s+update\b.*--init")
# A literal true, block or flow style: an expression (${{ !inputs.commit }}) decides per run and passes.
CANCELS = re.compile(r"""^\s*(concurrency\s*:\s*\{[^}\n]*)?['"]?cancel-in-progress['"]?\s*:\s*['"]?true\b""", re.I | re.M)
# Claude Code's settings, the app's wrapper of tools/hooks/hook.py that every agent's hooks call, and where Codex and
# Copilot keep theirs.
SETTINGS = ".claude/settings.json"
HOOK_WRAPPER = ".claude/hooks/hook.py"
HOOK_CALLERS = re.compile(r"\.codex/config\.toml|\.github/hooks/[^/]+\.json")
_HOOK = f'python3 "$CLAUDE_PROJECT_DIR/{HOOK_WRAPPER}" '
# The hooks block of an app's .claude/settings.json, the same in every app: (event, matcher, command, timeout). A change
# here reaches the apps with their next bump, where conform asks for it.
HOOKS = (
    ("SessionStart", "compact|resume", _HOOK + "resume", 10),
    ("PreToolUse", "Read|Edit|MultiEdit|Write|NotebookEdit|Grep|Bash", _HOOK + "pre", 10),
    ("PostToolUse", "Edit|MultiEdit|Write|NotebookEdit|Bash", _HOOK + "post", 60),
    ("Stop", None, _HOOK + "stop", 180),
)
# With a scripts/setup-linux.sh: what a Claude Code cloud session installs as it starts.
SETUP_LINUX_SCRIPT = "scripts/setup-linux.sh"
SETUP_HOOK = ("SessionStart", "startup",
              f'[ "$CLAUDE_CODE_REMOTE" != true ] || "$CLAUDE_PROJECT_DIR/{SETUP_LINUX_SCRIPT}"', 600)


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


def form_problems(code: list[str], suffix: str, own: str, laching: str) -> list[str]:
    """What a wrapper's code (code_lines) leaves out of WRAPPER_FORM, the call of its own tool (`own`, its path under
    tools/: windows/screenshots.ps1, not screenshots.sh) first."""
    text = "\n".join(code)
    tool = f"{laching.strip('/')}/tools/{own}"
    missing = [] if re.search(rf"{re.escape(tool)}(?![\w.-])", flat(text)) else [f"call {laching}/tools/{own}"]
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
        elif path.suffix in WRAPPER_FORM and (missing := form_problems(code, path.suffix, own, laching)):
            said = missing[0] if len(missing) == 1 else f"{', '.join(missing[:-1])} or {missing[-1]}"
            problems.append(f"{name}: a wrapper of {tool} that doesn't {said}: see the templates in "
                            f"{laching}/tools/README.md, *Staying on Laching's tools*")
        if path.stem == "setup-linux" and delegates(text, laching) and not bootstrapped(code_lines(text, path.suffix), laching):
            problems.append(f"{name}: runs {tool} before checking out {laching}/, which a clone made without its "
                            f"submodules lacks: check it out first ([ -f ... ] || git -C \"$COMPANION_ROOT\" submodule "
                            f"update --init {laching})")
    return problems


def workflow_code(root: Path, name: str) -> str | None:
    """A workflow without its comment lines; None for a file that can't be read."""
    try:
        text = (root / name).read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def workflow_problems(config: companion.Config, files: list[str]) -> list[str]:
    tag = config.get("laching.tag")
    problems, called = [], set()
    for name in files:
        if not re.match(r"\.github/workflows/[^/]+\.ya?ml$", name) or (code := workflow_code(config.root, name)) is None:
            continue
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
    if config.get("mac") and "macos-app" not in called:
        problems.append(".github/workflows: no workflow calls Laching's macos-app.yml (the app has mac)")
    return problems


def run_lines(code: str) -> list[str]:
    """The shell of a workflow's `run:` steps, line by line (a block's too), without comments: not a step's name."""
    lines, indent = [], None
    for line in code.splitlines():
        if indent is not None and (not line.strip() or len(line) - len(line.lstrip()) > indent):
            lines.append(line.strip())
            continue
        indent = None
        if match := re.match(r"(\s*)(-\s+)?run:\s*(.*)", line):
            if re.fullmatch(r"[|>][-+]?\s*(#.*)?", match.group(3)):
                indent = len(match.group(1)) + len(match.group(2) or "")
            else:
                lines.append(match.group(3))
    return [re.sub(r"(^|\s)#.*", "", line) for line in lines]


def shared_workflow_problems(config: companion.Config, files: list[str], allowed: set[str]) -> list[str]:
    """The app's core.yml, regenerate-vectors.yml and copilot-setup-steps.yml, by file name, each a copy unless it uses
    Laching's (a renamed copy passes)."""
    tag = config.get("laching.tag") or "vX.Y.Z"
    problems = []
    for name in files:
        match = re.fullmatch(r"\.github/workflows/([^/]+)\.ya?ml", name)
        if not match or name in allowed or match.group(1) not in (*CALLERS, COPILOT_SETUP):
            continue
        if (code := workflow_code(config.root, name)) is None:
            continue
        if match.group(1) == COPILOT_SETUP:
            if not any(SETUP_LINUX.search(line) for line in run_lines(code)):
                problems.append(f"{name}: sets up its own tools: its steps are actions/checkout with submodules, "
                                "actions/setup-node for Node 24 and `run: scripts/setup-linux.sh`, which installs what "
                                "Claude Code's cloud sessions get")
        elif (reusable := CALLERS[match.group(1)]) not in {workflow for workflow, _ in REUSABLE.findall(code)}:
            problems.append(f"{name}: a copy of Laching's {reusable}.yml: call it instead "
                            f"(uses: vmihalachi/laching/.github/workflows/{reusable}.yml@{tag})")
    return problems


def triggers(code: str) -> set[str] | None:
    """The events that start a workflow, from its top-level `on:` as a word, a one-line list, or a block of events (a
    mapping or a list); None for a workflow without one, or with one written another way."""
    lines = code.splitlines()
    for at, line in enumerate(lines):
        if not (match := re.match(r"""(?:on|"on"|'on')\s*:(.*)""", line)):
            continue
        value = re.sub(r"(^|\s)#.*", "", match.group(1)).strip()
        if value.startswith("["):
            return set(re.findall(r"[\w-]+", value)) if value.endswith("]") else None
        if value:
            return {value.strip("'\"")} if re.fullmatch(r"""['"]?[\w-]+['"]?""", value) else None
        events, indent = set(), None
        for child in lines[at + 1:]:
            depth = len(child) - len(child.lstrip())
            if not child.strip():
                continue
            if depth == 0 and not child.startswith("-"):
                break
            indent = depth if indent is None else indent
            if depth == indent and (event := re.match(r"""\s*(?:-\s+)?['"]?([\w-]+)""", child)):
                events.add(event.group(1))
        return events or None
    return None


def dispatch_problems(config: companion.Config, files: list[str], allowed: set[str]) -> list[str]:
    """The workflows only a dispatch starts that cancel the run in progress."""
    problems = []
    for name in files:
        if name in allowed or not re.fullmatch(r"\.github/workflows/[^/]+\.ya?ml", name):
            continue
        code = workflow_code(config.root, name)
        if code is not None and triggers(code) == {"workflow_dispatch"} and CANCELS.search(code):
            problems.append(f"{name}: runs only on workflow_dispatch and sets cancel-in-progress: true: no push supersedes "
                            "a run there, so a second dispatch on the same branch would only cancel a run that is nearly "
                            "done: set it to false")
    return problems


def hook_calls(settings: object) -> list[tuple[str, str | None, dict]]:
    """Every hook in a settings.json's hooks block: (its event, its group's matcher, the hook). What isn't the shape
    Claude Code reads is left out."""
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    calls = []
    for event, groups in (hooks.items() if isinstance(hooks, dict) else ()):
        for group in (groups if isinstance(groups, list) else ()):
            entries = group.get("hooks") if isinstance(group, dict) else None
            calls += [(event, group.get("matcher") or None, hook) for hook in (entries if isinstance(entries, list) else ())
                      if isinstance(hook, dict)]
    return calls


def hook_problems(config: companion.Config, files: list[str], allowed: set[str]) -> list[str]:
    """The agents' hooks: Laching's hooks (HOOKS) in the app's .claude/settings.json, and one form of call in Claude
    Code's, Codex's and Copilot's, the app's wrapper. An app without a .claude/settings.json has no hooks to check."""
    laching = config.get("laching.path", "laching").strip("/")
    tool = f"{laching}/tools/hooks/hook.py"
    # Either slash, and JSON's doubled backslash.
    direct = re.compile("/+".join(map(re.escape, tool.split("/"))) + r"(?![\w.-])")
    problems, settings = [], None
    for name in files:
        if name in allowed or not (name == SETTINGS or HOOK_CALLERS.fullmatch(name)):
            continue
        try:
            text = (config.root / name).read_text("utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        if name == SETTINGS:
            try:
                settings = json.loads(text)
            except ValueError as error:
                problems.append(f"{name}: not JSON ({error}): its hooks block can't be checked")
                continue
            commands = [str(hook.get("command", "")) for _, _, hook in hook_calls(settings)]
        else:
            commands = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
        if count := sum(len(direct.findall(flat(command))) for command in commands):
            problems.append(f"{name}: calls {tool} itself ({count} hook(s)): call the app's wrapper, {HOOK_WRAPPER}, which "
                            "hands the hook the app's root (COMPANION_ROOT), as every agent's hooks do")
            # The block is checked once its calls are the wrapper's: one line for one cause.
            settings = None if name == SETTINGS else settings
    if settings is not None:
        found = [(event, matcher, hook.get("type"), hook.get("command"), hook.get("timeout"))
                 for event, matcher, hook in hook_calls(settings)]
        for event, matcher, command, timeout in (*HOOKS, *([SETUP_HOOK] if SETUP_LINUX_SCRIPT in files else [])):
            if (event, matcher, "command", command, timeout) not in found:
                group = {**({"matcher": matcher} if matcher else {}),
                         "hooks": [{"type": "command", "command": command, "timeout": timeout}]}
                problems.append(f"{SETTINGS}: hooks.{event} lacks Laching's hook (the matcher, the command and the timeout "
                                f"as here): {json.dumps(group)}")
    if SETTINGS in files and not {SETTINGS, HOOK_WRAPPER} & allowed and HOOK_WRAPPER not in files:
        problems.append(f"{HOOK_WRAPPER}: missing: the wrapper of {tool} the agents' hooks call (the Python template in "
                        f"{laching}/tools/README.md, *Staying on Laching's tools*, with ../.. for the app's root)")
    return problems


def link_target(root: Path, name: str) -> str | None:
    """Where the skill at `name` links: git's record of the link (mode 120000), else the file system's (a symlink, or,
    outside a git checkout, the one-line text file a copy without symlinks leaves); None for a folder or a file of its
    own. In a checkout git records every link, so an untracked file (a README) is never one."""
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
    if done.returncode != 0 and path.is_file() and path.stat().st_size < 256:
        text = path.read_text("utf-8", errors="replace").strip()
        return text if re.fullmatch(r"[\w./-]+", text) else None
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
            + shared_workflow_problems(config, files, allowed) + dispatch_problems(config, files, allowed)
            + hook_problems(config, files, allowed) + skill_problems(config)
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
    """One line per app: the release it pins, how far behind the newest that is, the Laching checkout it is linked to
    (`companion.py link`), whether the pins that are left agree and whether it conforms; 1 when the apps pin different
    releases, or one is behind, linked or pinned to more than one release."""
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
        # A linked app isn't on a release, which is drift of its own: the link is named, and only the pins `link` left
        # alone are compared, as `companion.py pins` compares them there.
        linked = companion.linked_to(config)
        disagree = not companion.pins_agree(companion.pins(config, linked=False))
        behind = tags.index(tag) if tag in tags else None
        conform = problems(config)
        notes = [f"{behind} release(s) behind" if behind else "newest" if behind == 0 else "not a release tag"]
        if linked:
            notes.append(f"linked to {linked} (companion.py unlink)")
        if disagree:
            notes.append("its pins disagree (companion.py pins)")
        notes.append(f"{len(conform)} conform problem(s)" if conform else "conforms")
        print(f"  {folder.name}: {tag} ({', '.join(notes)})")
        drift |= bool(behind) or behind is None or disagree or bool(linked)
    if len(set(pins.values())) > 1:
        print("The apps pin different releases: tools/bump-apps.sh X.Y.Z moves them together.")
        drift = True
    return 1 if drift else 0
