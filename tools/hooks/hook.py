#!/usr/bin/env python3
"""Agent hooks for a companion app: Claude Code (.claude/settings.json), Codex (.codex/config.toml) and GitHub Copilot
(.github/hooks/*.json, pre and post, and cloud-stop for the cloud coding agent) all call this file,
`python3 laching/tools/hooks/hook.py <mode>`.

  hook.py pre    PreToolUse:  refuse to load huge or generated files and to edit generated ones (the String Catalogs,
                               .resw, the Android strings, project.pbxproj, the voice manifest and clips, the vectors,
                               character.py's art, synth.py's sounds, the Dev icon, hooks.guards and
                               hooks.guardedExtra), and to read any file over hooks.bigFileLines lines (600) whole:
                               Read without offset/limit, `cat`, `sed -n` without a range. Refuse a foreground shell
                               command that only waits: nothing but a `sleep` of hooks.foregroundSleepSeconds (30) or
                               longer, or a loop around `sleep` that waits for something (`while`/`until`, a `for`
                               that `break`s); the same in the background passes.
  hook.py post   PostToolUse: remember what this session edited and ran; format the Swift, C# or Kotlin file just
                               edited (ktlint -F, when ktlint is on PATH); regenerate the Xcode project when Swift files
                               come or go or the XcodeGen spec changes.
  hook.py stop   Stop:        run the app's verify.sh --fast on this session's edits, unless they passed it and
                               nothing changed since (the edited files, the script, companion.json, the app's and
                               Laching's working trees as Git sees them; a failure is never remembered), and remind
                               about the slow checks (xcodebuild test, conformance, swift/dotnet test, the Windows
                               app's model tests, the WinUI app build, the Kotlin core tests, the Android app build,
                               the landing build, hooks.slow) not run since the last edit, each with the edited files
                               that ask for it and verify.sh on those alone as what to run. A check ran when a command
                               of the session's ran it (the check at the start of one of its parts, its exit status
                               the command's: no `grep` that names it, no `swift test | tail`), or verify.sh did, which
                               notes the slow checks that ran and passed (naming the script runs nothing); remind once
                               about porting Mac UI changes to Windows and Android (hooks.macUI/windowsUI/androidUI);
                               say once per session that the app is linked to a Laching checkout (companion.py link);
                               tell the user once per compaction cycle when Claude's context is past
                               hooks.longSessionTokens (130k).
  hook.py resume SessionStart (compact|resume): print where this session was, as the agent's context after a
                               compaction: the files it edited, the slow checks it ran, the branch, git status and
                               the recent commits; nothing when the tree is clean and the session has no log.
  hook.py cloud-stop  Copilot agentStop: inside GitHub Actions only (the cloud coding agent, which has no session
                               log), run the app's verify.sh on the branch's changes and ask for fixes.

Every name and path comes from the app's companion.json. The payload is Claude's shape; `normalize` maps the other
agents onto it (Codex's apply_patch, Copilot's string tool_input and `path`). Exit code 2 blocks the action and shows
stderr to the agent. Everything else fails open (Copilot denies on any other non-zero exit, so errors exit 0),
including a missing or invalid companion.json.
"""
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections import namedtuple
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import companion  # noqa: E402

# Set by `setup` from companion.json.
ROOT = Path(".")
CONFIG = None
NAMES: dict = {}
L10N = ""
GUARDED: list = []  # (pattern, why, blocks reads, blocks edits)
I18N = None
SLOW: list = []  # (ran-kind, path pattern, reminder)
RAN: list = []  # (ran-kind, command pattern) of the app's own slow checks (hooks.slow)
MAC_UI = None
WINDOWS_UI = None
ANDROID_UI = None
BIG_LINES = 600  # hooks.bigFileLines; 0 disables
LONG_TOKENS = 130_000  # hooks.longSessionTokens; 0 disables
WAIT_SECONDS = 30  # hooks.foregroundSleepSeconds; 0 disables

READERS = re.compile(r"(^|[|;&(]\s*|\s)(cat|less|more|head|tail|bat|nl|xxd|od|strings|jq|view|awk|sed|python3?\s+-c)\b")
CONTEXT = re.compile(r"\s-(?:[ABC]\s*\d+|[a-zA-Z]*[ABC]\d*)\b|--(?:after|before)?-?context\b")
WHOLE = re.compile(r"(^|[|;&(]\s*|\s)(cat|less|more|bat|nl|view)\b")  # readers that print a file whole
SED_RANGE = re.compile(r"(\d+|/[^/]*/)\s*,\s*(\d+|\$|\+\d+|/[^/]*/)\s*[pP]|\b\d+\s*[pP]\b|/[^/]*/\s*[pP]")
OUTLINE = "grep -n '^\\(func\\|class\\|struct\\|extension\\|public\\|private\\|internal\\|##\\)'"
NEVER = "^$a"
SLOW_LOG = "laching-slow-checks.log"  # in the Git folder: the slow checks verify.sh ran and passed (`verify_ran`)
UNJUDGED = "unjudged:"  # in the session log, before the kind of a slow check that ran where its failure fails nothing


def setup(root=None):
    """Reads companion.json at `root` ($CLAUDE_PROJECT_DIR, else found from the current directory)."""
    global ROOT, CONFIG, NAMES, L10N, GUARDED, I18N, SLOW, RAN, MAC_UI, WINDOWS_UI, ANDROID_UI, BIG_LINES, LONG_TOKENS
    global WAIT_SECONDS
    root = Path(root or os.environ.get("CLAUDE_PROJECT_DIR") or companion.find_root())
    CONFIG = companion.load(root)
    ROOT = CONFIG.root
    NAMES = companion.derived(CONFIG)
    ere = companion.ere
    tools = f"{NAMES['LACHING_PATH'] or 'laching'}/tools"
    L10N = (f"use `python3 {tools}/l10n.py status -v`, or `grep -n '\"<key>\"' <file>` without context lines "
            "(localization skill)")
    voice, conformance = NAMES["VOICE_DIR"], NAMES["CONFORMANCE_DIR"]
    GUARDED = [
        (r"\.xcstrings$", f"String Catalog: {L10N}. Change strings through l10n.py export/apply.", True, True),
        (r"\.resw$", f"generated by `{tools}/l10n.py export-windows`; never read or edit it.", True, True),
        (r"values[^/]*/strings\.xml$|res/xml/locales_config\.xml$",
         f"generated by `{tools}/l10n.py export-android`; never read or edit it.", True, True),
        (r"project\.pbxproj$", f"generated: edit {NAMES['MAC_SPEC'] or 'the XcodeGen spec'} (the Xcode project "
                               "regenerates automatically).", True, True),
        (r"package-lock\.json$", "not needed for code changes; use `npm ls <pkg>`.", True, False),
    ]
    if voice:
        GUARDED += [
            (rf"^{ere(voice)}/Voice/manifest\.json$", f"generated by {tools}/voice.py; use `python3 {tools}/voice.py status`.",
             True, True),
            (rf"^{ere(voice)}/Voice/.+\.m4a$", f"recorded lines, generated by `{tools}/voice.py render`; never edit.",
             False, True),
            (rf"^{ere(voice)}/(README\.md|LICENSE|index\.html|robots\.txt|review/.+)$",
             f"the voices repo's site, generated by `{tools}/voice.py site`; regenerate it.", False, True),
        ]
    GUARDED += generators(tools, conformance)
    if conformance:
        # What the app writes by hand or records (pose recordings, say) is left out: `conformance.handEdited`, a file
        # exactly and a folder (ending in /) with everything under it.
        kept = "".join(f"|{ere(path)}{'' if path.endswith('/') else '$'}"
                       for path in CONFIG.get("conformance.handEdited", []))
        GUARDED.append((rf"^{ere(conformance)}/(?!README\.md${kept})[^ ]+$",
                        f"generated by {tools}/conformance.sh: change the Swift code or its conformance cases and "
                        "regenerate.", False, True))
    for guard in CONFIG.get("hooks.guards", []):
        GUARDED.append((guard["pattern"], guard["why"], bool(guard.get("reads")), True))
    for pattern in CONFIG.get("hooks.guardedExtra", []):
        GUARDED.append((pattern, "guarded by companion.json (hooks.guardedExtra): don't edit it by hand.", False, True))
    # Last, so the app's own guard on the same file is the reason shown.
    unguarded = CONFIG.get("hooks.unguarded", [])
    for name, guards in baseline(tools).items():
        GUARDED += [] if name in unguarded else guards
    I18N = re.compile(rf"^{ere(NAMES['LANDING_I18N'])}/[^/]+\.json$") if NAMES["LANDING_I18N"] else None
    landing = NAMES["LANDING_TOOL"] or "the landing tool"
    # How the reminder names each built-in check. Their paths are companion.SLOW_CHECKS's patterns, which verify.sh
    # runs them for too (with the paths `hooks.slow` gives one, below and in `companion.slow_asked`).
    labels = {
        "xcodebuild-test": "macOS tests",
        "conformance": f"conformance vectors ({tools}/conformance.sh)",
        "swift-test": f"core package tests (swift test --package-path {NAMES['MAC_CORE']})",
        "dotnet-test": "Windows Core tests",
        "dotnet-app-test":  # from the Windows folder: its global.json picks the SDK
            f"Windows app model tests (cd {NAMES['WINDOWS_DIR']} && dotnet test {NAMES['WINDOWS_APP_TESTS']})",
        "landing-build": f"landing build ({landing} build)",
        "winui-build": f"WinUI app build ({tools}/windows/vm.sh build in the Parallels VM, or dotnet build on Windows)",
        "gradle-core-test": f"Kotlin core tests (cd {NAMES['ANDROID_DIR']} && ./gradlew :{NAMES['ANDROID_CORE']}:test)",
        "android-build":
            f"Android app build (cd {NAMES['ANDROID_DIR']} && ./gradlew :{NAMES['ANDROID_APP']}:assembleDebug)",
    }
    SLOW = [(kind, NAMES[default], labels[kind]) for kind, default in companion.SLOW_CHECKS.items()]
    RAN = []
    for check in CONFIG.get("hooks.slow", []):
        builtin = next((i for i, (kind, _, _) in enumerate(SLOW) if kind == check["name"]), None)
        if builtin is not None:
            SLOW[builtin] = (check["name"], check["paths"], check.get("label") or SLOW[builtin][2])
        else:
            SLOW.append((check["name"], check["paths"], check.get("label") or check["name"]))
        if check.get("ran"):
            RAN.append((check["name"], check["ran"]))
    SLOW = [(kind, pattern, label) for kind, pattern, label in SLOW if pattern != NEVER]
    MAC_UI = re.compile(CONFIG.get("hooks.macUI")) if CONFIG.get("hooks.macUI") else None
    WINDOWS_UI = re.compile(CONFIG.get("hooks.windowsUI")) if CONFIG.get("hooks.windowsUI") else None
    ANDROID_UI = re.compile(CONFIG.get("hooks.androidUI")) if CONFIG.get("hooks.androidUI") else None
    BIG_LINES = int(CONFIG.get("hooks.bigFileLines", BIG_LINES) or 0)
    LONG_TOKENS = int(CONFIG.get("hooks.longSessionTokens", LONG_TOKENS) or 0)
    WAIT_SECONDS = int(CONFIG.get("hooks.foregroundSleepSeconds", WAIT_SECONDS) or 0)


def generators(tools, conformance):
    """The guards on what Laching's generators write, from their sections of companion.json: character.py's vectors,
    Swift art and raster assets, synth.py's sounds and dev-icon.swift's Dev icon. Each names the command that
    rewrites the file. They come before the vectors' guard, so the character vectors name character.py."""
    ere, guards = companion.ere, []
    design, art = NAMES["CHARACTERS_DESIGN"], NAMES["CHARACTERS_SWIFT"]
    if design:
        why = f"generated by `python3 {tools}/character.py` from {design}/: change the drawing or the rig there and rerun it."
        outputs = [
            rf"^{ere(conformance)}/characters/" if conformance else "",
            rf"^{ere(art)}/[^/]+Art\.swift$" if art else "",
            rf"^{ere(NAMES['CHARACTERS_ASSETS'])}/[^/]+\.imageset/" if NAMES["CHARACTERS_ASSETS"] else "",
            rf"^{ere(NAMES['CHARACTERS_WINDOWS'])}/[^/]+\.scale-\d+\.png$" if NAMES["CHARACTERS_WINDOWS"] else "",
        ]
        guards += [(pattern, why, False, True) for pattern in outputs if pattern]
    if NAMES["SOUNDS_OUT"]:
        guards.append((rf"^{ere(NAMES['SOUNDS_OUT'])}/[^/]+\.wav$",
                       f"generated by `python3 {tools}/synth.py` from the recipes in {NAMES['SOUNDS_RECIPES']}: change "
                       "a recipe there and rerun it.", False, True))
    if NAMES["MAC_DIR"]:
        # dev-icon.swift always writes this set, next to the AppIcon it starts from.
        guards.append((rf"^{ere(NAMES['MAC_DIR'])}/(.+/)?AppIcon-Dev\.appiconset/[^/]+\.png$",
                       f"the Debug build's icon, generated from AppIcon by `swift {tools}/dev-icon.swift "
                       "<the Assets.xcassets>`: change AppIcon, then rerun it.", False, True))
    return guards


def baseline(tools):
    """The guards every app has without a line in its companion.json, by the name `hooks.unguarded` turns one off
    with: the folders the screenshot runs fill, the app icon's sizes, the Windows images assets.py renders from it and
    the Gradle wrapper's scripts. Every folder comes from companion.json, so an app without the key has no such guard,
    and what the app keeps beside them (a README.md, an icon set's Contents.json, a picture of its own) stays its to
    edit."""
    ere = companion.ere
    windows_shots = CONFIG.path("windows.screenshots")
    runs = [(NAMES["SCREENSHOTS_OUT"], f"{tools}/screenshots.sh", f"{tools}/screenshots.sh <folder>"),
            (rel(windows_shots) if windows_shots else "", f"{tools}/windows/screenshots.ps1",
             f"{tools}/windows/vm.sh screenshots <folder>")]
    mac, assets, android = NAMES["MAC_DIR"], NAMES["WINDOWS_ASSETS"], NAMES["ANDROID_DIR"]
    logos = "Square44x44Logo|Square150x150Logo|Wide310x150Logo|StoreLogo|SplashScreen"  # assets.py's `images`
    return {
        "screenshots": [
            (rf"^{ere(folder)}/(?!README\.md$)",
             f"the committed screenshots, written by `{writer}`: never read or edit one. To look at the app, capture "
             f"into a scratch folder (`{scratch}`).", True, True)
            for folder, writer, scratch in runs if folder],
        "appIcon": [(rf"^{ere(mac)}/(.+/)?AppIcon\.appiconset/[^/]+\.png$",
                     "the app icon's sizes, rendered by the app's own icon script: change the drawing and rerun it.",
                     False, True)] if mac else [],
        "windowsAssets": [(rf"^{ere(assets)}/(({logos})\.[^/]+\.png|(Dev/)?AppIcon\.ico)$",
                           f"generated from the app icon by `python3 {tools}/windows/assets.py`: change the icon, then "
                           "rerun it.", False, True)] if assets else [],
        "gradleWrapper": [(rf"^{ere(android)}/gradlew(\.bat)?$",
                           "the Gradle wrapper's script: `./gradlew wrapper --gradle-version <version>` rewrites it; "
                           "never edit it by hand.", False, True)] if android else [],
    }


def rel(path):
    """Root-relative with forward slashes on every OS, so the path patterns match on Windows too."""
    return companion.rel(path, ROOT)


def guarded(path, for_edit):
    path = rel(path)
    for pattern, why, reads, edits in GUARDED:
        if re.search(pattern, path) and (edits if for_edit else reads):
            return f"{path}: {why}"
    return None


def big(path):
    """The line count of `path` when it is a text file over BIG_LINES lines, else 0."""
    if not BIG_LINES:
        return 0
    for candidate in (Path(path), ROOT / path):
        try:
            if not candidate.is_file() or candidate.stat().st_size < BIG_LINES:
                continue
            data = candidate.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:  # binary
            return 0
        lines = data.count(b"\n")
        return lines if lines > BIG_LINES else 0
    return 0


def whole_read(path, lines):
    path = rel(path)
    return (f"{path} is {lines} lines: read its outline first (`{OUTLINE} {path}`), then only the range you need "
            f"(Read with offset and limit, or `sed -n 'A,Bp'`), never the whole file.")


def deny(reason):
    reason = f"Blocked by laching/tools/hooks/hook.py — {reason}"
    print(json.dumps({"permissionDecision": "deny", "permissionDecisionReason": reason}))  # Copilot
    print(reason, file=sys.stderr)
    sys.exit(2)


# --- What a shell command runs ---------------------------------------------------------------------------------------

Part = namedtuple("Part", "words keywords kept captured background")
Part.__doc__ = """One simple command of a shell command line (`shell_parts`).

words       its words with the quotes removed and without redirections: the program and its arguments, after any
            variable assignments and wrappers (`run_words` drops those).
keywords    the shell keywords in front of it (`if`, `then`, `while`, `do`, `!`, `{` ...); `done`, `fi` and `}` are
            parts with keywords and no words.
kept        its failure is the whole command's: nothing after it replaces its exit status, or it ends the shell
            (`|| exit 1`, `set -e`).
captured    it runs inside `$(...)`, backquotes or `<(...)`.
background  it runs behind an `&`: its own, its subshell's, or that of the loop, `if` or `{ }` it is in.
"""

KEYWORDS = frozenset(("if", "then", "elif", "else", "fi", "while", "until", "do", "done", "esac", "!", "{", "}"))
WRAPPERS = frozenset(("time", "env", "command", "exec", "builtin", "noglob", "nice", "nohup", "caffeinate", "timeout",
                      "gtimeout", "arch", "xcrun", "stdbuf", "npx"))
COMPOUNDS = {"if": "fi", "while": "done", "until": "done", "for": "done", "select": "done", "case": "esac", "{": "}"}
SHELLS = frozenset(("sh", "bash", "zsh", "dash"))
INTERPRETERS = SHELLS | {"python", "python3", "node", "ruby", "perl", "pwsh"}
ASSIGNMENT = re.compile(r"[A-Za-z_]\w*(\[[^\]]*\])?\+?=")
REDIRECTION = re.compile(r"&>>?|<<<|<<-?|>>|>&|>\||<&|<>|[<>](?!\()")


class ShellText:
    """A shell command read just far enough to tell what it runs from what it only mentions: quotes, heredoc bodies,
    comments and redirections are no commands, and a substitution's commands are kept apart from the command they sit
    in. Not a shell: what it can't read (a `case` pattern, a function body) comes out as parts nobody recognizes."""

    def __init__(self, text):
        self.text, self.pos, self.heredocs = text, 0, []  # heredocs: (end word, quoted) of those the line opened

    def commands(self, closer=""):
        """The commands up to `closer` (`)` or a backquote; without one, the end of the text), each a dict: its `words`,
        the command lists of the substitutions in them (`subs`), its own list when it is a subshell (`group`) and the
        separator after it (`sep`: && || | |& ; ;; & or a newline, empty at the end)."""
        text, found = self.text, []
        command = {"words": [], "subs": [], "group": None, "sep": ""}

        def close(sep):
            nonlocal command
            if command["words"] or command["subs"] or command["group"] is not None:
                command["sep"] = sep
                found.append(command)
                command = {"words": [], "subs": [], "group": None, "sep": ""}

        while self.pos < len(text):
            c, two = text[self.pos], text[self.pos:self.pos + 2]
            if c in " \t\r":
                self.pos += 1
            elif two == "\\\n":  # a line continuation: the command goes on
                self.pos += 2
            elif c == closer:
                self.pos += 1
                break
            elif c == "\n":
                self.pos += 1
                close("\n")
                self.skip_heredocs()
            elif c == "#":
                end = text.find("\n", self.pos)
                self.pos = len(text) if end < 0 else end
            elif two in ("&&", "||", ";;", "|&"):
                self.pos += 2
                close(two)
            elif two in ("<(", ">("):
                self.pos += 2
                command["subs"].append(self.commands(")"))
            elif redirection := REDIRECTION.match(text, self.pos):
                self.pos = redirection.end()
                while text[self.pos:self.pos + 1] in (" ", "\t"):
                    self.pos += 1
                start = self.pos
                target = self.word(None, closer)  # what it reads or writes is no word of the command
                if redirection.group() in ("<<", "<<-") and target is not None:
                    self.heredocs.append((target, text[start:self.pos] != target))
            elif c in "|;&":
                self.pos += 1
                close(c)
            elif c == "(" and all(word in KEYWORDS for word in command["words"]):
                self.pos += 1
                command["group"] = self.commands(")")
            elif c == "(":  # an array, a function's `()`, a test's grouping
                self.balanced()
            elif c == ")":  # a `case` pattern's
                self.pos += 1
                close(";")
            else:
                self.word(command, closer)
        close("")
        return found

    def word(self, command, closer=""):
        """Reads one word, quotes removed, onto `command`'s words (None for a redirection's target), and returns it:
        None when there is none here. A file descriptor in front of a redirection (`2>`) is no word."""
        text, start, value, subs = self.text, self.pos, [], []
        while self.pos < len(text):
            c = text[self.pos]
            if c in " \t\r\n;&|<>()" or (c == closer and c == "`"):
                break
            self.pos += 1
            if c == "\\":
                if text[self.pos:self.pos + 1] != "\n":  # a line continuation joins the word's two halves
                    value.append(text[self.pos:self.pos + 1])
                self.pos += 1
            elif c == "'":
                end = text.find("'", self.pos)
                end = len(text) if end < 0 else end
                value.append(text[self.pos:end])
                self.pos = end + 1
            elif c == '"':
                self.quoted(value, subs)
            elif c == "`":
                subs.append(self.commands("`"))
                value.append("$()")
            elif c == "$" and text[self.pos:self.pos + 1] in ("(", "{", "'"):
                self.dollar(value, subs)
            else:
                value.append(c)
        if self.pos == start:
            return None
        word = "".join(value)
        if command is not None:
            command["subs"] += subs
            if not (word.isdigit() and text[start:self.pos] == word and text[self.pos:self.pos + 1] in ("<", ">")):
                command["words"].append(word)
        return word

    def quoted(self, value, subs):
        """The rest of a double-quoted string: text, except for the substitutions in it."""
        text = self.text
        while self.pos < len(text):
            c = text[self.pos]
            self.pos += 1
            if c == '"':
                return
            if c == "\\":
                if text[self.pos:self.pos + 1] != "\n":
                    value.append(text[self.pos:self.pos + 1])
                self.pos += 1
            elif c == "`":
                subs.append(self.commands("`"))
                value.append("$()")
            elif c == "$" and text[self.pos:self.pos + 1] in ("(", "{"):
                self.dollar(value, subs)
            else:
                value.append(c)

    def dollar(self, value, subs):
        """After a `$`: a command substitution's commands go to `subs`; arithmetic, `${...}` and `$'...'` are text."""
        text, start = self.text, self.pos
        if text.startswith("((", start):
            self.balanced()
            value.append("$(())")
        elif text.startswith("(", start):
            self.pos += 1
            subs.append(self.commands(")"))
            value.append("$()")
        elif text.startswith("{", start):
            self.balanced("{", "}")
            value.append("$" + text[start:self.pos])
        else:
            self.pos += 1
            while self.pos < len(text) and text[self.pos] != "'":
                self.pos += 2 if text[self.pos] == "\\" else 1
            value.append(text[start + 1:self.pos])
            self.pos += 1

    def balanced(self, opener="(", closing=")"):
        """Past the bracket here and its match."""
        depth = 0
        while self.pos < len(self.text):
            c = self.text[self.pos]
            self.pos += 1
            depth += (c == opener) - (c == closing)
            if depth <= 0:
                return

    def skip_heredocs(self):
        """Past the bodies of the heredocs opened on the line that just ended: text being written, nothing that runs.
        In the body of one whose end word is unquoted a line continuation joins two lines before the shell looks for
        that word, so `\\` at the end of a line hides the next one's end word; a quoted one's body is taken as it is."""
        text = self.text
        for delimiter, quoted in self.heredocs:
            line = ""
            while self.pos < len(text):
                end = text.find("\n", self.pos)
                end = len(text) if end < 0 else end
                line, self.pos = line + text[self.pos:end], min(end + 1, len(text))
                if not quoted and (len(line) - len(line.rstrip("\\"))) % 2:
                    line = line[:-1]
                    continue
                if line.strip() == delimiter:
                    break
                line = ""
        self.heredocs = []


def shell_parts(command):
    """`command` as the simple commands it runs, in the order written (a substitution's before the command it sits
    in): a list of `Part`. `;`, `&&`, `||`, `|`, `&` and newlines separate them; heredoc bodies, quoted strings,
    comments and redirections are left out; a line continuation joins, but not in single quotes, a comment or a quoted
    heredoc's body; a subshell's commands and the script of `bash -c '...'` are parts too. `part.kept` says whether a
    failure of that command fails the whole line: not when `|`, `||` or `&` follows it, or `;` or a newline with
    another command after (the `}` that closes its group is none), or it is a condition (`if`, `while`, `!`) or in a
    loop's or an `if`'s body; `&&` keeps it, and so does a pipe under `set -o pipefail`. A failure that ends the shell
    keeps it whatever follows, in a loop too: `|| exit 1` (or `|| { ...; exit 1; }`) after it, or `set -e` before it.
    Use `run_words(part.words)` to see the program a part runs."""
    parts = []
    try:
        flatten(ShellText(str(command)).commands(), parts)
    except RecursionError:
        return []
    # A compound command behind an `&` runs there whole: `while ...; do ...; done &`, `{ ...; } &`.
    opened = []
    for index, part in enumerate(parts):
        for word in [*part.keywords, *part.words[:1]]:
            if word in COMPOUNDS:
                opened.append((COMPOUNDS[word], index))
            elif opened and opened[-1][0] == word:
                start = opened.pop()[1]
                if part.background:
                    parts[start:index] = [earlier._replace(background=True, kept=False)
                                          for earlier in parts[start:index]]
    return parts


CLOSERS = frozenset(COMPOUNDS.values())


def split_keywords(command):
    """A command of `ShellText.commands` as the shell keywords in front of it and its own words."""
    words = command["words"]
    count = next((i for i, word in enumerate(words) if word not in KEYWORDS), len(words))
    return words[:count], words[count:]


def set_options(words, options):
    """What `set <words>` turns on or off of the options that decide whether a failure is kept, into `options`:
    `errexit` (`-e`, `-o errexit`) and `pipefail` (`-o pipefail`)."""
    if words[:1] != ["set"]:
        return
    rest = iter(words[1:])
    for word in rest:
        if not re.fullmatch(r"[-+][A-Za-z]+", word):
            break
        if "e" in word[1:]:
            options["errexit"] = word[0] == "-"
        if word.endswith("o"):
            name = next(rest, "")
            if name in ("errexit", "pipefail"):
                options[name] = word[0] == "-"


def flatten(commands, parts, kept=True, captured=False, background=False, options=None):
    """Appends the `Part`s of `commands` (`ShellText.commands`) to `parts`. `options`: those of `set_options` in force
    where they run."""
    options, last = dict(options or {}), len(commands) - 1
    # The compound commands each command is in, innermost last: for each, whether the command is in its condition (a
    # loop's header, all of a `case`), and the compound as [its keyword, its closing word, whether a condition is being
    # read, the command that opens it, the command that closes it].
    stack, around = [], []
    for index, command in enumerate(commands):
        keywords, words = split_keywords(command)
        for word in keywords + words[:1]:
            if word in COMPOUNDS:
                stack.append([word, COMPOUNDS[word], word != "{", index, None])
            elif word in ("then", "do", "else") and stack:
                stack[-1][2] = False
            elif word == "elif" and stack:
                stack[-1][2] = True
            elif stack and word == stack[-1][1]:
                stack.pop()[4] = index
        around.append([(entry, entry[2]) for entry in stack])

    def closes(index):
        """Whether the command at `index` only closes compounds (`}`, `done`, `fi`): it is no later command."""
        words = commands[index]["words"]
        return bool(words) and all(word in CLOSERS for word in words) and not commands[index]["subs"]

    def exits(index):
        """Whether the command at `index`, run after one that failed, ends the shell with a failure: `exit 1`, or
        `exit` and `exit $?` straight after it, or a `{ ...; }` group that ends in `exit 1`."""
        if index > last:
            return False
        keywords, words = split_keywords(commands[index])
        straight = True
        if keywords[:1] == ["{"]:
            closer = next((entry[4] for entry, _ in around[index] if entry[3] == index), None)
            if closer is None or closer == index:
                return False
            if closer - 1 != index:  # `{ echo failed; exit; }` exits with echo's status
                keywords, words = split_keywords(commands[closer - 1])
                straight = False
        if words[:1] != ["exit"] or (keywords and keywords != ["{"]):
            return False
        if len(words) == 1 or words[1] == "$?":
            return straight
        return words[1].isdigit() and int(words[1]) % 256 != 0

    # From the last command back: whether a failure is still the exit status once everything after it ran, and the
    # separator that ends the `&&`, `||` and `|` chain a command is in (`&`: it runs in the background). The command
    # that closes a group is none of what runs after: `{ swift test; } 2>&1` keeps it.
    lasting, ends = [True] * len(commands), [""] * len(commands)
    for index in range(last, -1, -1):
        sep = commands[index]["sep"]
        if sep in ("&", ";", ";;", "\n"):
            closed = sep != "&" and index < last and closes(index + 1)
            lasting[index] = (lasting[index + 1] and commands[index + 1]["sep"] not in ("|", "|&") if closed
                              else sep != "&" and index == last)
            ends[index] = sep
        elif index < last:
            lasting[index], ends[index] = sep != "||" and lasting[index + 1], ends[index + 1]
        else:
            lasting[index] = sep != "||"
    for index, command in enumerate(commands):
        keywords, words = split_keywords(command)
        sep, outer = command["sep"], [entry for entry, _ in around[index]]
        piped = sep in ("|", "|&") and not options.get("pipefail")  # a pipe's exit status is its last command's
        end = index
        while end < last and commands[end]["sep"] in ("|", "|&"):
            end += 1
        closed = all(entry[4] is not None for entry in outer)

        def outer_seps(*seps):
            return not any(commands[entry[4]]["sep"] in seps for entry in outer)

        errexit = False
        if "!" in keywords or any(condition for _, condition in around[index]):
            ok = False  # its status is a condition's, or turned around
        elif sep == "||" and exits(index + 1) and ends[index] != "&":
            ok = closed and outer_seps("|", "|&", "&")  # `|| exit 1`: its failure ends the shell, in a loop too
        elif options.get("errexit") and not piped and commands[end]["sep"] not in ("&&", "||", "&"):
            ok = errexit = closed and outer_seps("&&", "||", "|", "|&", "&")  # `set -e`: likewise
        else:
            # Not in a loop (a later round decides) nor an `if` (which runs it, not whether it fails, is the question)
            ok = closed and all(entry[0] == "{" for entry in outer) and not piped and lasting[index]
        ok = kept and ok
        behind = background or ends[index] == "&"
        for sub in command["subs"]:
            flatten(sub, parts, False, True, behind)
        group = command["group"]
        script = shell_script(run_words(words)) if group is None else None
        if group is not None or script is not None:  # a subshell, or `bash -c '...'`: its commands stand for it
            if keywords:
                parts.append(Part([], keywords, False, captured, behind))
            inner = group if group is not None else ShellText(script).commands()
            # A subshell has the options; it ignores `set -e` where the shell would for it. `bash -c` has its own.
            flatten(inner, parts, ok, captured, behind, {**options, "errexit": errexit} if group is not None else None)
        elif words or keywords:
            parts.append(Part(words, keywords, ok, captured, behind))
        set_options(words, options)


def run_words(words):
    """`words` from the program they run: without the variable assignments in front and the wrappers (`time`,
    `env X=1`, `timeout 600`, `xcrun` ...) with their options."""
    words = list(words)
    while words:
        if ASSIGNMENT.match(words[0]):
            words.pop(0)
        elif program(words) in WRAPPERS:
            words.pop(0)
            while words and (words[0].startswith("-") or ASSIGNMENT.match(words[0])
                             or re.fullmatch(r"\d+(\.\d+)?[smhd]?", words[0])):
                words.pop(0)
        else:
            break
    return words


def program(words):
    """The name of the program `words` start with, without its folder."""
    return words[0].rsplit("/", 1)[-1] if words else ""


def shell_script(words):
    """The script of `bash -c '...'` (`sh`, `zsh`; `-lc` and the like), None for any other command."""
    if program(words) not in SHELLS:
        return None
    rest = iter(words[1:])
    for word in rest:
        if word in ("-o", "+o", "-O", "+O"):
            next(rest, None)
        elif re.fullmatch(r"-[A-Za-z]*c", word):
            return next(rest, None)
        elif not word.startswith(("-", "+")):
            break
    return None


PATCHED = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)\s*$", re.M)


def normalize(event):
    """One Claude-shaped event per file the tool touches: Codex's apply_patch edits several files in one call, and
    Copilot sends tool_input as a JSON string and names the file `path`."""
    if not isinstance(event, dict):
        return []
    tool, args = event.get("tool_name", ""), event.get("tool_input") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {"command": args}
    if not isinstance(args, dict):
        args = {}
    if tool == "apply_patch":
        patch = args.get("command") or args.get("input") or ""
        if isinstance(patch, list):
            patch = "\n".join(map(str, patch))
        # Its paths are relative to the session's directory (`cwd`), which may be below the root.
        cwd = event.get("cwd")
        return [{**event, "tool_name": "Edit", "tool_input": {"file_path": str(Path(cwd) / p) if cwd else p}}
                for p in PATCHED.findall(patch)]
    if tool in ("Read", "Edit", "Write") and "file_path" not in args and "path" in args:
        args = {**args, "file_path": args["path"]}
    if tool == "Read" and isinstance(vr := args.get("view_range"), list) and len(vr) == 2:
        start, end = vr
        if isinstance(start, int) and isinstance(end, int) and (end > 0 or start > 1):
            args = {**args, "offset": start, **({"limit": max(1, end - start + 1)} if end > 0 else {})}
    if tool == "Bash" and isinstance(args.get("command"), list):  # Codex sends argv
        argv = list(map(str, args["command"]))
        script = shell_script(argv)  # `bash -lc '<the command>'`: the command is the one to judge
        args = {**args, "command": shlex.join(argv) if script is None else script}
    return [{**event, "tool_name": tool, "tool_input": args}]


def pre(event):
    for one in normalize(event):
        pre_one(one)


def pre_one(event):
    tool, args = event.get("tool_name", ""), event.get("tool_input") or {}
    if tool == "Read":
        path = args.get("file_path", "")
        if reason := guarded(path, for_edit=False):
            deny(reason)
        if I18N and I18N.search(rel(path)) and not args.get("limit"):
            deny(f"{rel(path)} is large: `grep -n '\"<key>\"' {rel(path)}`, or Read with a small limit.")
        if not args.get("limit") and not args.get("offset") and (lines := big(path)):
            deny(whole_read(path, lines))
    elif tool in ("Edit", "MultiEdit", "Write", "NotebookEdit"):
        path = args.get("file_path") or args.get("notebook_path") or ""
        if reason := guarded(path, for_edit=True):
            deny(reason)
    elif tool == "Grep":
        path = args.get("path", "") + " " + args.get("glob", "")
        wants_context = any(args.get(k) for k in ("-A", "-B", "-C", "context"))
        if wants_context and any(re.search(p, path.strip()) for p, _, reads, _ in GUARDED if reads):
            deny(f"no context lines on generated files: {L10N}.")
    elif tool == "Bash":
        command = args.get("command", "")
        if not args.get("run_in_background") and (reason := foreground_wait(command)):
            deny(reason)
        # Heredoc bodies are text being written, not files being read.
        command = re.sub(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n.*?^\s*\1\s*$", "", command, flags=re.S | re.M)
        # Judge each simple command on its own: `git status -- x.xcstrings | head` is fine.
        for segment in re.split(r"\|\||&&|[|;\n]", command):
            segment = re.sub(r">>?\s*\S+", "", segment)  # a redirect's target is written, not read
            whole = WHOLE.search(segment) or (re.search(r"(^|\s)sed\b", segment) and not SED_RANGE.search(segment)
                                              and not re.search(r"\s-[a-zA-Z]*i\b|--in-place", segment))
            for word in re.findall(r"[^\s'\"`|;&<>()]+", segment):
                if not re.search(r"[\w*-]\.\w+$", word):  # a file name, not a bare extension in prose
                    continue
                reason = guarded(word, for_edit=False)
                if reason and (READERS.search(segment) or CONTEXT.search(segment) or re.search(
                        r"\bgit\s+(diff|show|log\s+-p)\b(?!.*--(stat|name-only|numstat|shortstat))", segment)):
                    deny(reason)
                if whole and (lines := big(word)):
                    deny(whole_read(word, lines))


FILLERS = frozenset(("echo", "printf", "true", ":", "date"))  # beside a `sleep`, they leave the command a wait
SLEEP_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
WAIT_INSTEAD = ("Run the long command itself in the background (Claude Code: the Bash tool's `run_in_background`) and "
                "go on with other work: the harness tells you when it exits. Watch a CI run with one background "
                "`gh run watch <id> --exit-status`.")


def sleep_seconds(words):
    """How long `sleep <words>` sleeps, in seconds: its numbers added up, as GNU sleep does (`1m 30s`). `infinity` is
    forever; a word that is no number (`$DELAY`) counts for nothing."""
    total = 0.0
    for word in words:
        if word.lower() in ("infinity", "inf"):
            return float("inf")
        if found := re.fullmatch(r"(\d+(?:\.\d*)?|\.\d+)([smhd]?)", word):
            total += float(found.group(1)) * SLEEP_UNITS[found.group(2)]
    return total


def foreground_wait(command):
    """Why a foreground shell command is refused as a wait, None when it isn't one. A wait is a command that is nothing
    but `sleep` for hooks.foregroundSleepSeconds or longer (an `echo` beside it changes nothing), or a loop around a
    `sleep` that waits for something: a `while`/`until`, or a `for`/`select` that leaves on a `break` (or `exit`) once
    it saw it and would sleep that long or longer in all its rounds (`$(seq 60)` rounds: no telling, so long). The agent
    sits in it and learns nothing until it ends. A `sleep` counts where the command runs it (`shell_parts`): not in a
    heredoc, a quoted string or a script being written, and not behind an `&`. A pause between two steps
    (`start-server & sleep 5; curl ...`), a few short retries, a `for` loop that waits for nothing and a `while read`
    over lines pass."""
    if not WAIT_SECONDS or "sleep" not in str(command):
        return None
    # For each loop open here: [its keyword, it polls, a sleep runs in it, the seconds one round sleeps, its rounds].
    loops, slept, only = [], 0.0, True
    for part in shell_parts(command):
        words = run_words(part.words)
        name = program(words)
        for keyword in part.keywords:
            if keyword in ("while", "until"):
                loops.append([keyword, name != "read", False, 0.0, float("inf")])
            elif keyword == "done" and loops:
                keyword, polls, sleeps, seconds, rounds = loops.pop()
                if polls and sleeps and (keyword in ("while", "until") or seconds * rounds >= WAIT_SECONDS):
                    return ("a foreground loop around `sleep` that waits for something (`while`/`until`, or a `for` "
                            "that `break`s) only waits. " + WAIT_INSTEAD + " A wait with no command of its own (a "
                            "server coming up) is this loop, run in the background.")
        if name in ("for", "select"):
            loops.append([name, False, False, 0.0, loop_rounds(words) if name == "for" else float("inf")])
        if part.background or not words:
            continue
        if name == "sleep":
            seconds = sleep_seconds(words[1:])
            slept += seconds
            for loop in loops:
                loop[2], loop[3] = True, loop[3] + seconds
        elif name in ("break", "exit") and loops:
            if loops[-1][0] in ("for", "select"):  # a `while read` that leaves early still reads only its lines
                loops[-1][1] = True
        elif name not in FILLERS:
            only = False
    if only and slept >= WAIT_SECONDS:
        how_long = "forever" if slept == float("inf") else f"for {slept:g} seconds"
        return (f"a foreground `sleep` {how_long} only waits. {WAIT_INSTEAD} A pause under {WAIT_SECONDS} seconds between "
                "two steps is fine (hooks.foregroundSleepSeconds).")
    return None


def loop_rounds(words):
    """How many rounds `for <words>` runs: one for each word after `in`, a `{1..40}` range's numbers; infinity when
    the words are no list to count (`$(seq 60)`, a glob, `for ((...))`)."""
    if words[2:3] != ["in"]:
        return float("inf")
    rounds = 0
    for word in words[3:]:
        if found := re.fullmatch(r"\{(-?\d+)\.\.(-?\d+)(?:\.\.(-?\d+))?\}", word):
            first, last, step = int(found.group(1)), int(found.group(2)), abs(int(found.group(3) or 1)) or 1
            rounds += abs(last - first) // step + 1
        elif re.search(r"[$*?\[{`]", word):
            return float("inf")
        else:
            rounds += 1
    return rounds


def state_file(event):
    session = re.sub(r"[^A-Za-z0-9_-]", "", event.get("session_id") or "default")
    folder = Path(os.environ.get("TMPDIR", "/tmp")) / f"{NAMES.get('NAME', 'companion')}-agent"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{session}.log"


def record(event, kind, value):
    with state_file(event).open("a") as f:
        f.write(f"{time.time()}\t{kind}\t{value}\n")


def xcode_project_stale():
    """True when a Swift file on disk isn't in project.pbxproj or vice versa, or the spec is newer."""
    mac = ROOT / NAMES["MAC_DIR"]
    project = mac / NAMES["MAC_PROJECT"] / "project.pbxproj"
    spec = ROOT / NAMES["MAC_SPEC"]
    if not project.exists():
        return True
    if spec.exists() and spec.stat().st_mtime > project.stat().st_mtime:
        return True
    text = project.read_text("utf8", errors="replace")
    listed = set(re.findall(r"/\* ([^*/]+\.swift) \*/", text))
    swift_folders = (
        NAMES["MAC_APP"],
        NAMES["MAC_TESTS"],
        f"{NAMES['MAC_DIR']}/Shared",
        f"{NAMES['MAC_DIR']}/macOS",
        f"{NAMES['MAC_DIR']}/iOS",
    )
    on_disk = {p.name for d in swift_folders if d and (ROOT / d).is_dir() for p in (ROOT / d).rglob("*.swift")}
    return listed != on_disk


def regenerate_project():
    if sys.platform != "darwin" or not NAMES["MAC_SPEC"] or not xcode_project_stale():
        return None
    mac = ROOT / NAMES["MAC_DIR"]
    try:
        done = subprocess.run(["xcodegen", "generate", "--quiet"], cwd=mac, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"The Xcode project is out of date and `xcodegen generate` failed to run ({error}); run it in {NAMES['MAC_DIR']}/."
    if done.returncode:
        return f"`xcodegen generate` failed:\n{done.stderr[-2000:]}"
    return (f"Regenerated {NAMES['MAC_DIR']}/{NAMES['MAC_PROJECT']} with xcodegen (Swift files or the spec changed); "
            "commit it with your change.")


def format_file(path):
    """Formats a Swift, C# or Kotlin file in place, so nobody spends effort matching the style by hand."""
    if not (ROOT / path).is_file():
        return
    mac, windows, android = NAMES["MAC_DIR"], NAMES["WINDOWS_DIR"], NAMES["ANDROID_DIR"]
    swift_format = ROOT / mac / ".swift-format"
    # Kotlin under the Android project, or under kotlin/ (Laching's own Kotlin build, when the hook runs there).
    kotlin = path.endswith((".kt", ".kts")) and ((android and path.startswith(f"{android}/")) or path.startswith("kotlin/"))
    cwd = ROOT
    if mac and path.startswith(f"{mac}/") and path.endswith(".swift") and sys.platform == "darwin" and swift_format.exists():
        command = ["xcrun", "swift-format", "format", "-i", "--configuration", str(swift_format), path]
    elif windows and path.startswith(f"{windows}/") and path.endswith(".cs"):
        # In the Windows folder, so the SDK is the one its global.json pins (the host reads the working directory's).
        cwd = ROOT / windows
        command = ["dotnet", "format", "whitespace", ".", "--folder", "--include", path[len(windows) + 1:]]
    elif kotlin and shutil.which("ktlint"):
        command = ["ktlint", "-F", path]  # ktlint_official, the project's .editorconfig
    else:
        return
    try:
        subprocess.run(command, cwd=cwd, capture_output=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired):
        pass


def post(event):
    notes = list(filter(None, (post_one(one) for one in normalize(event))))
    if notes:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": notes[0]}}))


def post_one(event):
    tool, args = event.get("tool_name", ""), event.get("tool_input") or {}
    note = None
    if tool in ("Edit", "MultiEdit", "Write", "NotebookEdit"):
        path = rel(args.get("file_path") or args.get("notebook_path") or "")
        record(event, "edit", path)
        format_file(path)
        if path == NAMES["MAC_SPEC"] or (NAMES["MAC_DIR"] and path.startswith(f"{NAMES['MAC_DIR']}/") and path.endswith(".swift")):
            note = regenerate_project()
    elif tool == "Bash":
        command = args.get("command", "")
        # Run in the background, or sent there when it took too long: the hook hears it started, not how it ended.
        response = event.get("tool_response") if isinstance(event.get("tool_response"), dict) else {}
        later = args.get("run_in_background") or any(
            response.get(key) for key in ("backgroundTaskId", "timedOutAfterMs", "backgroundedToDeliverMessage"))
        ran = [] if later else ran_kinds(command)
        for kind in ran:
            record(event, "ran", kind)
        for kind in ran_kinds(command, passed=True):  # run, but behind a pipe or the like: the stop reminder says so
            if kind not in ran:
                record(event, "ran", kind if kind.startswith(UNJUDGED) else UNJUDGED + kind)
        if re.search(r"\.swift\b|project\.yml", command) and re.search(r"\b(rm|mv|git\s+rm|git\s+mv|touch|cp)\b", command):
            note = regenerate_project()
    return note


def ran_kinds(command, passed=False):
    """The slow checks a shell command runs: the built-in kinds, and the app's own by their `ran` (hooks.slow). A check
    counts where the command runs it, not where it names it: at the start of one of its commands (`shell_parts`), so
    not in a `grep`'s pattern, an `echo`, a commit message or a heredoc. And only when its failure would have failed
    the whole command (`Part.kept`): the hook hears of a command that succeeded, and `dotnet test | tail` succeeds
    whatever the tests did. `passed` says somebody else judged the outcome (verify.sh's note of a check that passed),
    so every command in it counts. verify.sh isn't a check: a command that names it may be a --help or a run that
    failed, so the script says what it ran itself (`verify_ran`)."""
    kinds, went = [], []
    for part in shell_parts(command):
        words = run_words(part.words)
        if words[:1] in (["cd"], ["pushd"]):
            went += words[1:]
        elif words and (part.kept or passed):
            kinds += part_kinds(words, " ".join(went))
    return list(dict.fromkeys(kinds))


def part_kinds(words, went=""):
    """The slow checks one command is: `words` from its program on (`run_words`), `went` the folders the command line
    changed to before it. A run of only some of a check's tests (`--filter`, `--tests`, `-only-testing`) is that check
    as `UNJUDGED`: it ran, but it doesn't count; a listing of them (`--list-tests`) is none."""
    # The app's own: its pattern has to match from the command's first word, or from the script an interpreter runs
    # (`python3 scripts/check.py`), not somewhere in the arguments.
    head = 1
    if program(words) in INTERPRETERS:
        head += next((i for i, word in enumerate(words[1:], 1) if not word.startswith("-")), 0)
    line, start = " ".join(words), len(" ".join(words[:head]))
    kinds = [kind for kind, pattern in RAN if (found := re.search(pattern, line)) and found.start() < start]
    if program(words) in SHELLS:  # `bash laching/tools/conformance.sh`: the script is the program
        words = words[head - 1:]
    text = " ".join([program(words), *words[1:]])
    if re.match(r"xcodebuild\s(.*\s)?test(-without-building)?(\s|$)", text):
        kinds.append(part_of(r"\s-(only|skip)-testing\b", text, "xcodebuild-test"))
    if program(words).endswith("conformance.sh"):
        kinds.append("conformance")
    if re.match(r"dotnet(\.exe)?\s+test\b", text) and not re.search(r"\s(--list-tests|-t)(\s|$)", text):
        # A solution-wide run includes the core's and the app's model tests; a test project's run its own.
        core = NAMES["WINDOWS_CORE_TESTS"] or (f"{NAMES['WINDOWS_CORE']}.Tests" if NAMES["WINDOWS_CORE"] else "")
        core_tests, app_tests = project_name(core), project_name(NAMES["WINDOWS_APP_TESTS"])
        where = f"{text} {went}"
        solution = not re.search(r"\.Tests\b", where)
        if solution or (core_tests and core_tests in where):
            kinds.append(part_of(r"\s--filter\b", text, "dotnet-test"))
        if solution or (app_tests and app_tests in where):
            kinds.append(part_of(r"\s--filter\b", text, "dotnet-app-test"))
    if re.match(r"swift\s+test\b", text) and not re.match(r"swift\s+test\s+(.*\s)?(list|--list-tests)(\s|$)", text):
        kinds.append(part_of(r"\s--(filter|skip)(\s|=|$)", text, "swift-test"))
    # The WinUI app built: in the Parallels VM (vm.sh's build, and screenshots and smoke, which build it), or by
    # dotnet on Windows. vm.sh test runs the same test projects as dotnet test.
    app = NAMES["WINDOWS_APP"]
    if re.match(r"vm\.sh\s+(build|screenshots|smoke)\b", text) or (
            app and re.match(rf"dotnet(\.exe)?\s+(build|publish)\b.*{re.escape(app)}", text)):
        kinds.append("winui-build")
    if re.match(r"vm\.sh\s+test\b", text):
        kinds += ["dotnet-test", "dotnet-app-test"]
    # Gradle: the core module's tests by name, or a whole-build `test`/`check`/`build`; the app's assemble likewise.
    if re.match(r"gradlew?(\.bat)?\s", text):
        core, app = NAMES["ANDROID_CORE"], NAMES["ANDROID_APP"]
        whole = not re.search(r"\s:\w+:", text)  # no module-qualified task: every module
        if re.search(r"\s(-x|--exclude-task)[\s=]\S*test\b", text):
            pass  # `./gradlew build -x test`: the build without its tests
        elif (core and f":{core}:test" in text) or (whole and re.search(r"\s(test|check|build)\b", text)):
            kinds.append(part_of(r"\s--tests[\s=]", text, "gradle-core-test"))
        if (app and re.search(rf":{re.escape(app)}:(assemble|build|install)", text)) or (
                whole and re.search(r"\s(assemble\w*|build)\b", text)):
            kinds.append("android-build")
    tool_name = NAMES["LANDING_TOOL"]
    if tool_name and re.match(rf"{re.escape(tool_name)}\s+(run\s+)?build\b", text):
        kinds.append("landing-build")
    return kinds


def project_name(path):
    """A test project's name from `windows.coreTests` or `windows.appTests`, which may name its folder or its .csproj."""
    return re.sub(r"\.csproj$", "", path.rstrip("/").rsplit("/", 1)[-1]) if path else ""


def part_of(pattern, text, kind):
    """`kind`, or `UNJUDGED` and `kind` when `pattern` finds an option in `text` that runs only some of its tests."""
    return UNJUDGED + kind if re.search(pattern, text) else kind


def verify_ran():
    """When verify.sh last ran each slow check in this checkout, as {kind: epoch seconds}. The script leaves a line in
    the Git folder for every slow check that ran and passed: when it started, the kinds it stands for and its command.
    One of the app's own checks (verify.checks) names no kind, so its command says which, as an agent's would
    (`ran_kinds`, every command of it: the script saw it pass); `ran` of hooks.slow is matched against every note. A
    check that failed, was skipped or never started (--help, --fast, --no-vm for the WinUI build) left no line, so its
    reminder stays. The script's stamps are whole seconds, rounded down: a check counts from the end of its second,
    since the edit made in that second came before it."""
    folder = git_lines("rev-parse", "--absolute-git-dir")
    try:
        lines = (Path(folder[0]) / SLOW_LOG).read_text("utf8", errors="replace").splitlines() if folder else []
    except OSError:
        lines = []
    last, own = {}, {kind for kind, _ in RAN}
    for line in lines:
        parts = line.split("\t", 2)
        if len(parts) != 3 or not parts[0].isdigit():
            continue
        # An app's check is noted as `bash -c <its run>`, the quotes around the script gone.
        named, ran = parts[1].split(), ran_kinds(re.sub(r"^bash -c ", "", parts[2]), passed=True)
        for kind in named + [kind for kind in ran if kind in own] if named else ran:
            if not kind.startswith(UNJUDGED):  # a run of some of the tests, which its note can't make the whole check
                last[kind] = max(last.get(kind, 0), float(parts[0]) + 1)
    return last


def parity_reminder(event, last_edit, last_ran):
    """Once per batch of Mac UI edits with no Windows (and, with hooks.androidUI, Android) edit since: ask for the
    port or a reason. Names the ports still missing."""
    if MAC_UI is None:
        return None
    mac = [p for p in last_edit if MAC_UI.search(p)]
    if not mac:
        return None
    newest_mac = max(last_edit[p] for p in mac)
    ports = [("Windows", WINDOWS_UI,
              f"{NAMES['WINDOWS_DIR']}/{NAMES['WINDOWS_APP']}" if NAMES["WINDOWS_APP"] else "the Windows app")]
    if ANDROID_UI is not None:
        ports.append(("Android", ANDROID_UI,
                      f"{NAMES['ANDROID_DIR']}/{NAMES['ANDROID_APP']}" if NAMES["ANDROID_DIR"] else "the Android app"))
    missing = [(name, where) for name, pattern, where in ports
               if max((t for p, t in last_edit.items() if pattern and pattern.search(p)), default=0) < newest_mac]
    if not missing or last_ran.get("parity-reminded", 0) >= newest_mac:
        return None
    record(event, "ran", "parity-reminded")
    names = " and ".join(name for name, _ in missing)
    where = " or ".join(where for _, where in missing)
    ports_word = "ports" if len(missing) > 1 else "port"
    skill = " (windows-port skill)" if any(name == "Windows" for name, _ in missing) else ""
    return (f"You changed Mac UI code ({', '.join(sorted(mac)[:5])}) and nothing under {where} since. "
            f"User-visible changes get the {names} {ports_word} in the same change{skill}. "
            f"If this one isn't user-visible or has no {names} counterpart, tell the user that in one line.")


def verify_script():
    """The app's scripts/verify.sh wrapper when it has one, else Laching's."""
    wrapper = ROOT / "scripts" / "verify.sh"
    return wrapper if wrapper.exists() else TOOLS / "verify.sh"


def file_stamp(path):
    """A file's size and times, as the fast checks' memo keys it; `gone` for one that isn't there, so deleting a file
    changes the key."""
    try:
        status = os.stat(path)
    except OSError:
        return "gone"
    return f"{status.st_size} {status.st_mtime_ns} {status.st_ctime_ns}"


def tree_state(folder, outer=None, until=None):
    """The working tree of the repository `folder` is in, as Git sees it: the commit checked out, then every path that
    differs from it or from the index (staged, changed, deleted, untracked, in conflict) as `git status` prints it and
    with its `file_stamp`, and the same for a repository among them (a submodule with changes in it). Bytes, for a
    hash; None when Git can't say (no repository, no git) or hasn't by `until` (`time.monotonic()`; 20 s a command
    without one). What Git ignores is in neither."""
    def left():
        return 20 if until is None else max(0.1, until - time.monotonic())

    try:
        top = companion.git(folder, "rev-parse", "--show-toplevel", timeout=left())
        if top.returncode or not top.stdout.strip():
            return None
        top = Path(top.stdout.strip())
        if top == outer:  # a folder with a `.git` that is no repository: its files are the outer one's
            return b""
        done = companion.git(top, "--no-optional-locks", "status", "--porcelain=v2", "--branch", "-z",
                             "--untracked-files=all", "--ignore-submodules=none", text=False, timeout=left())
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode:
        return None
    # With -z an entry ends in NUL and its path, the last field, is as it is on disk. The fields before a path, by the
    # entry's kind: changed, renamed (the old path is the next entry), in conflict, untracked.
    before = {b"1": 8, b"2": 9, b"u": 10, b"?": 1}
    state, entries = [], iter(done.stdout.split(b"\0"))
    for entry in entries:
        if entry.startswith(b"# branch.oid "):  # not the branch's name or how far it is from its upstream
            state.append(entry)
        if entry[:1] not in before:
            continue
        path = top / os.fsdecode(entry.split(b" ", before[entry[:1]])[-1])
        state += [entry, file_stamp(path).encode()]
        if entry[:1] == b"2":
            state.append(next(entries, b""))
        if (path / ".git").exists():
            inner = tree_state(path, top, until)
            if inner is None:
                return None
            state.append(inner)
    return b"\0".join(state)


def fast_state(script, edited):
    """The key of the fast checks' memo: a hash of what their result depends on, as far as the hook can see. The files
    the session edited, each with its `file_stamp`; the app's verify script and its companion.json; whether the app is
    linked to a Laching checkout; and the working trees of the app and of Laching (its tools, this hook among them) as
    `tree_state` reads them, since the checks read far more than the edited files: a commit, a checkout, a pin bump or
    a file a command rewrote changes the key. None when a part can't be read (an app outside Git, a Git too slow for
    the few seconds the stop has to spare beside the checks' own 170), and nothing is remembered then."""
    until = time.monotonic() + 8
    try:
        parts = ["\n".join(f"{path}\t{file_stamp(ROOT / path)}" for path in edited).encode("utf8", "replace"),
                 script.read_bytes(), (ROOT / companion.FILE).read_bytes(),
                 str(companion.linked_to(CONFIG)).encode("utf8", "replace")]
        for folder in (ROOT, TOOLS):
            tree = tree_state(folder, until=until)
            if tree is None:
                return None
            parts.append(tree)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return None
    digest = hashlib.sha256()
    for part in parts:  # each as its own hash, so where one part ends is never in doubt
        digest.update(hashlib.sha256(part).digest())
    return digest.hexdigest()


def context_size(transcript):
    """The context of the session's last model call in tokens, and how many compactions came before it, from Claude's
    transcript; (0, 0) when there is none (Codex, Copilot) or it has no usage yet."""
    try:
        data = Path(transcript).read_bytes()
    except (OSError, TypeError):
        return 0, 0
    tokens = 0
    for line in reversed(data[-1_000_000:].splitlines()):
        if b'"usage"' not in line or b'"isSidechain":true' in line:
            continue
        try:
            usage = json.loads(line)["message"]["usage"]
        except (ValueError, KeyError, TypeError):
            continue
        tokens = sum(usage.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        break
    return tokens, data.count(b'"subtype":"compact_boundary"')


def long_session(event):
    """Once per compaction cycle past hooks.longSessionTokens: tell the user the session is long. Every call resends
    the whole context, so one long session costs far more than the same work split across fresh ones."""
    if not LONG_TOKENS:
        return None
    tokens, compactions = context_size(event.get("transcript_path"))
    marker = f"long-session-{compactions}"
    if tokens < LONG_TOKENS:
        return None
    try:
        if f"\tran\t{marker}" in state_file(event).read_text():
            return None
    except OSError:
        pass
    record(event, "ran", marker)
    after = f" after {compactions} compaction{'s' if compactions != 1 else ''}" if compactions else ""
    return (f"This session's context is {tokens // 1000}k tokens{after}, and every call resends it. Start a new session "
            "for the next task, or /compact at the next milestone.")


def link_reminder(event):
    """Once per session while the app is linked to a Laching checkout (`companion.py link`, the trial of an unreleased
    Laching): say so. The pins pass with a warning meanwhile, so the link isn't a failed check at every stop; the
    pre-commit hook and CI still refuse it."""
    try:
        linked = companion.linked_to(CONFIG)
    except (OSError, ValueError, KeyError):
        return None
    if not linked:
        return None
    marker = "link-reminded"
    try:
        if f"\tran\t{marker}" in state_file(event).read_text():
            return None
    except OSError:
        pass
    record(event, "ran", marker)
    tools = f"{NAMES['LACHING_PATH'] or 'laching'}/tools"
    return (f"{ROOT.name} is linked to {linked}, not to Laching's pinned release: "
            f"`python3 {tools}/companion.py unlink` before committing.")


def stop(event):
    if event.get("stop_hook_active"):
        return
    long, link = long_session(event), link_reminder(event)
    problems = stop_problems(event)
    if problems:
        if link:  # the agent goes on anyway, so it reads the reminder itself
            problems.append(link)
        if long:  # and the user hears this one from the agent
            problems.append(f"Then tell the user in one line: {long}")
        print("\n\n".join(problems), file=sys.stderr)
        sys.exit(2)
    if long or link:  # shown to the user; costs the agent nothing and blocks no stop
        print(json.dumps({"systemMessage": " ".join(filter(None, (long, link)))}))


def stop_problems(event):
    """What the agent must still do before stopping: failed fast checks, slow checks not run, the parity reminder.

    The fast checks don't run again over a state that passed them: a pass is remembered in the session's log under
    its `fast_state`, read before the run, and the next stop with the same key runs nothing. A failure, a timeout and
    a state the hook can't read are never remembered, so only a run that passed can stand for a later one."""
    try:
        lines = state_file(event).read_text().splitlines()
    except OSError:
        return []
    last_edit, last_ran, passed = {}, {}, None
    for line in lines:
        stamp, kind, value = line.split("\t", 2)
        if kind == "passed":
            passed = value
        else:
            (last_edit if kind == "edit" else last_ran)[value] = float(stamp)
    edited = sorted(p for p in last_edit if not p.startswith("/"))
    if not edited:
        return []

    problems = []
    script = verify_script()
    command = companion.shell_command(script)
    if command is None:
        problems.append(f"{rel(script)} didn't run: Windows starts no shell script itself and there's no Git for "
                        "Windows bash here, so the fast checks on your edits were skipped. Tell the user.")
    elif (state := fast_state(script, edited)) is None or state != passed:
        try:
            # UTF-8 as the checks print it, whatever the locale: a byte cp1252 lacks would raise and skip the report.
            done = subprocess.run([*command, "--fast", "--", *edited], cwd=ROOT, capture_output=True, text=True,
                                  encoding="utf8", errors="replace", timeout=170)
            if done.returncode:
                problems.append("Fast checks on the files you edited failed:\n"
                                + done.stdout[-4000:] + done.stderr[-1000:])
            elif state:
                record(event, "passed", state)
        except (OSError, subprocess.TimeoutExpired):
            pass

    for kind, stamp in verify_ran().items():
        last_ran[kind] = max(last_ran.get(kind, 0), stamp)
    stale = {}  # the edited files that ask for a check, to the checks they ask for
    for kind, pattern, label in SLOW:
        asked = tuple(sorted(p for p, t in last_edit.items()
                             if t > ran_over(last_ran, kind, p) and re.search(pattern, p)))
        if asked:
            unjudged = all(ran_over(last_ran, UNJUDGED + kind, p) >= last_edit[p] for p in asked)
            stale.setdefault(asked, []).append(
                label + (" [it ran, but that run doesn't count: in the background, only some of its tests (`--filter`, "
                         "`--tests`, `-only-testing`), or where its failure doesn't fail the command (behind `|`, `;`, "
                         "`||` or `&`, in `$(...)`)]" if unjudged else ""))
    if reminder := parity_reminder(event, last_edit, last_ran):
        problems.append(reminder)
    if stale:
        # Only the files whose edit a check is waiting on: with every file of the session the command would start the
        # slow checks of all of them, and late in a session that is dozens of paths.
        listed = "".join(f"- {', '.join(labels)}, for {' '.join(asked)}\n" for asked, labels in stale.items())
        files = " ".join(shlex.quote(p) for p in sorted({p for asked in stale for p in asked}))
        problems.append(f"Not run since your last edit:\n{listed}"
                        f"Run `{rel(script)} -- {files}` (it runs only what applies), "
                        "or tell the user why you're skipping it.")
    return problems


def ran_over(last_ran, kind, path):
    """When check `kind` last ran over `path`, from `last_ran` ({kind: stamp}): a run of all of it, or one of a part that
    verify.sh noted as `<kind>@<path prefix>` (the Mac scheme's tests alone: `xcodebuild-test@macos/macOS/`) when the
    path starts with that prefix. A part never stands for a path outside it: a shared file still asks for the whole."""
    scoped = (stamp for key, stamp in last_ran.items()
              if key.startswith(f"{kind}@") and path.startswith(key[len(kind) + 1:]))
    return max(last_ran.get(kind, 0), *scoped, 0)


def git_lines(*args):
    """A git command's stdout lines in the app's repository; none when it fails (no repository, no commit yet)."""
    try:
        done = companion.git(ROOT, *args, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return done.stdout.splitlines() if done.returncode == 0 else []


def resume(event):
    """SessionStart after a compaction or a resume: where this session was, printed as the agent's context. The files it
    edited (each once, as first edited, at most 20), the slow checks it ran, then the branch, `git status --short` (at
    most 30 lines) and the last five commits. Nothing at all when the tree is clean and the session has no log."""
    edited, ran = [], []
    try:
        lines = state_file(event).read_text().splitlines()
    except OSError:
        lines = []
    for line in lines:
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        _, kind, value = parts
        bucket = edited if kind == "edit" else ran if kind == "ran" else None
        if bucket is not None and value not in bucket:
            bucket.append(value)
    ran = [kind for kind in ran  # without the reminders' markers
           if kind not in ("parity-reminded", "link-reminded") and not kind.startswith(("long-session-", UNJUDGED))]
    # And what verify.sh ran since the session began, which it notes itself.
    began = None
    for line in lines:
        try:
            began = float(line.split("\t", 1)[0])
            break
        except ValueError:
            continue
    if began is not None:  # a check verify.sh ran over a part of it (`<kind>@<prefix>`) is named with that part
        ran += [name for name in dict.fromkeys(
            kind if "@" not in kind else "{} ({} only)".format(*kind.split("@", 1))
            for kind, stamp in verify_ran().items() if stamp >= began) if name not in ran]
    status = git_lines("status", "--short")
    if not edited and not ran and not status:
        return
    branch = git_lines("rev-parse", "--abbrev-ref", "HEAD")
    out = [f"Where this session was in {ROOT.name}" + (f" (branch {branch[0]})" if branch else "") + ":"]
    if edited:
        out.append("Files this session edited (the Stop hook checks them):")
        out += [f"  {path}" for path in edited[:20]]
        if len(edited) > 20:
            out.append(f"  ... and {len(edited) - 20} more")
    if ran:
        out.append("Slow checks this session ran: " + ", ".join(ran))
    if status:
        out.append("git status --short:")
        out += [f"  {line}" for line in status[:30]]
        if len(status) > 30:
            out.append(f"  ... and {len(status) - 30} more")
    if log := git_lines("log", "--oneline", "-5"):
        out.append("Recent commits:")
        out += [f"  {entry}" for entry in log]
    print("\n".join(out))


def default_branch():
    """origin's default branch (origin/HEAD), else origin/main or origin/master, else None."""
    head = subprocess.run(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=ROOT, capture_output=True,
                          text=True)
    candidates = [head.stdout.strip()] if head.returncode == 0 and head.stdout.strip() else []
    for branch in [*candidates, "origin/main", "origin/master"]:
        if subprocess.run(["git", "merge-base", branch, "HEAD"], cwd=ROOT, capture_output=True).returncode == 0:
            return branch
    return None


def cloud_stop(event):
    """Copilot's cloud coding agent: check the whole branch, since there's no per-session log of edits. Copilot CLI on
    a laptop reads .claude/settings.json and gets `stop`, so this does nothing outside GitHub Actions."""
    if os.environ.get("GITHUB_ACTIONS") != "true" or event.get("stop_hook_active"):
        print("{}")
        return
    branch = default_branch()
    base = ["--base", branch] if branch else []  # shallow clone or no remote branch: working-tree changes only
    script = verify_script()
    command = companion.shell_command(script)
    try:
        if command is None:
            raise OSError("no bash to run verify.sh")
        done = subprocess.run([*command, *base], cwd=ROOT, capture_output=True, text=True, encoding="utf8",
                              errors="replace", timeout=1500)
    except (OSError, subprocess.TimeoutExpired):
        print("{}")
        return
    if done.returncode:
        reason = (f"{rel(script)} failed on this branch's changes. Fix them, or explain in the pull request why a check "
                  "can't pass here:\n" + done.stdout[-4000:] + done.stderr[-1000:])
        print(json.dumps({"decision": "block", "reason": reason}))
    else:
        print("{}")


if __name__ == "__main__":
    companion.utf8_console()
    sys.stdin.reconfigure(encoding="utf-8")  # the agents send UTF-8 JSON, not the code page
    try:
        mode = sys.argv[1]
        payload = json.load(sys.stdin)
        setup()
        {"pre": pre, "post": post, "stop": stop, "resume": resume, "cloud-stop": cloud_stop}[mode](payload)
    except SystemExit as stop_code:
        # Only a deliberate block (2) passes through; companion.load's exits on a bad file fail open.
        if stop_code.code == 2:
            raise
        sys.exit(0)
    except Exception as error:  # fail open: a hook bug must not block every tool call
        print(f"hook.py {sys.argv[1:]} failed open: {error!r}", file=sys.stderr)
        sys.exit(0)
