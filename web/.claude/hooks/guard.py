#!/usr/bin/env python3
"""PreToolUse guard for Claude Code, Codex and Copilot: refuses to read a big or generated file whole, so the agent
reads the outline and then a range instead (agent-setup/README.md, *Token budget*). Fails open on any error.

A repository keeps a copy at .claude/hooks/guard.py (scaffold.sh adds it) and edits only GENERATED and BIG_LINES.
Wired three times with the same command, `python3 .claude/hooks/guard.py`:
  .claude/settings.json      PreToolUse, matcher "Read|Bash"          (Claude Code and the Copilot CLI)
  .codex/config.toml         [[hooks.PreToolUse]] matcher = "^Bash$"  (Codex reads files with its shell)
  .github/hooks/<repo>.json  PreToolUse                               (the Copilot coding agent)
Exit code 2 blocks the call in Claude Code and Codex; the JSON line blocks it in Copilot.
"""
import json
import re
import sys
from pathlib import Path

BIG_LINES = 600  # a text file over this many lines is read by outline and range; 0 disables
GENERATED = []  # regexes on the path: generated files read by grep or a range whatever their size, e.g. r"\.Designer\.cs$"
OUTLINE = r"grep -n '^\(func\|class\|struct\|extension\|public\|##\)'"
WHOLE = re.compile(r"(^|[|;&(]\s*|\s)(cat|less|more|bat|nl|view)\b")  # readers that print a file whole
SED_RANGE = re.compile(r"(\d+|/[^/]*/)\s*,\s*(\d+|\$|\+\d+|/[^/]*/)\s*[pP](?!\w)|\b\d+\s*[pP]\b|/[^/]*/\s*[pP](?!\w)")


def deny(reason):
    reason = f"Blocked by .claude/hooks/guard.py: {reason}"
    print(json.dumps({"permissionDecision": "deny", "permissionDecisionReason": reason}))  # Copilot
    print(reason, file=sys.stderr)  # Claude Code and Codex show stderr with exit 2
    sys.exit(2)


def big(path, cwd):
    """The line count when `path` is a text file over BIG_LINES lines or a GENERATED one, else 0."""
    candidate = Path(path) if Path(path).is_absolute() else Path(cwd) / path
    generated = any(re.search(pattern, path.replace("\\", "/")) for pattern in GENERATED)
    try:
        if not candidate.is_file() or (not generated and candidate.stat().st_size < BIG_LINES):
            return 0
        data = candidate.read_bytes()
    except OSError:
        return 0
    if b"\0" in data[:4096]:
        return 0
    lines = data.count(b"\n")
    return lines if (BIG_LINES and lines > BIG_LINES) or generated else 0


def reason(path, lines):
    what = f"is generated ({lines:,} lines)" if any(re.search(g, path.replace("\\", "/")) for g in GENERATED) else f"is {lines:,} lines"
    return (f"{path} {what}: outline it first ({OUTLINE} {path}), then read a range "
            f"(Read with offset and limit, or `sed -n 'A,Bp'`), never the whole file.")


def main():
    event = json.load(sys.stdin)
    tool = event.get("tool_name") or event.get("toolName") or ""
    args = event.get("tool_input") or event.get("toolArgs") or {}
    if isinstance(args, str):  # Copilot sends the arguments as a JSON string
        try:
            args = json.loads(args)
        except ValueError:
            args = {"command": args}
    cwd = event.get("cwd") or "."
    if tool == "Read":
        path = args.get("file_path") or args.get("path") or ""
        if path and not args.get("limit") and not args.get("offset") and (lines := big(path, cwd)):
            deny(reason(path, lines))
    elif tool in ("Bash", "shell"):
        command = args.get("command", "")
        if isinstance(command, list):  # Codex sends argv
            command = " ".join(map(str, command))
        command = re.sub(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n.*?^\s*\1\s*$", "", command, flags=re.S | re.M)
        for segment in re.split(r"\|\||&&|[|;\n]", command):  # judge each simple command on its own
            whole = WHOLE.search(segment) or (re.search(r"\bsed\b", segment) and not SED_RANGE.search(segment))
            if not whole:
                continue
            previous = ""
            for word in segment.split():
                word = word.strip("'\"")
                if not word.startswith("-") and not previous.startswith(">") and (lines := big(word, cwd)):
                    deny(reason(word, lines))
                previous = word


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 - a broken guard must not block work
        print(f"guard.py failed open: {error!r}", file=sys.stderr)
