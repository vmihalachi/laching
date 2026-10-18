#!/usr/bin/env python3
"""Record what a session edits, check it before the session ends, and say where the session was after a compaction.

scaffold.sh installs this file as <repo>/.claude/hooks/validate.py and wires it three times:

  .claude/settings.json       Claude Code: PostToolUse on Edit|Write|MultiEdit -> record,
                              Stop -> check, SessionStart on compact|resume -> resume (Claude only)
  .codex/config.toml          Codex: PostToolUse on apply_patch -> record, Stop -> check
  .github/hooks/<repo>.json   Copilot: postToolUse -> record, agentStop -> check

  validate.py record   append the edited files that match `patterns` to this session's list
  validate.py check    when this session edited something since its last passing run: run `checks`, then `format`
                       with the edited files; a failure blocks the stop with the output's tail, or only warns when
                       the previous stop already blocked (stop_hook_active), so a problem the agent cannot fix
                       never loops
  validate.py resume   print where the session was: the files it edited, git status, recent commits

The repo specifics live in validate.json next to this file:

  {"patterns": ["^src/.*\\.cs$"],                 edited paths worth checking (relative to the root, forward slashes)
   "exclude": ["/(bin|obj)/"],                   optional: paths skipped even when they match
   "checks": [["scripts/verify.sh", "--fast"]],  commands run from the root, in order, stopping at the first failure
   "format": ["dotnet", "format", "X.sln", "--verify-no-changes", "--include"],  optional: the edited files follow
   "format_patterns": ["\\.cs$"]}                optional: which edited files `format` gets (default: all of them)

State lives in <git-dir>/agent-validate/<session>.files and .passed, so parallel sessions in one checkout only
check their own edits and a worktree keeps its own. Standard library only, runs with python3 -I, on Windows too.
Anything unexpected exits 0 without output: a hook must never break the session.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]  # <root>/.claude/hooks/validate.py
PATCHED = re.compile(r"^\*\*\* (?:Add File|Update File|Move to): (.+?)\s*$", re.M)
TAIL = 40
KEEP_DAYS = 7


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, errors="replace")


def load_config():
    try:
        return json.loads((HERE / "validate.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return {"error": f"validate.json could not be read ({error}), so nothing was checked."}


def state_dir():
    out = git("rev-parse", "--absolute-git-dir")
    if out.returncode:
        return None
    folder = Path(out.stdout.strip()) / "agent-validate"
    folder.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - KEEP_DAYS * 86400
    for old in folder.iterdir():
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass
    return folder


def session_of(event):
    raw = str(event.get("session_id") or event.get("sessionId") or "")
    return re.sub(r"[^A-Za-z0-9_-]", "", raw) or "default"


def edited_paths(event):
    """The files the tool touched, relative to the root with forward slashes. Codex's apply_patch edits several
    files in one call, with paths relative to its cwd; Copilot sends tool_input as a JSON string and calls the
    file `path`."""
    tool = event.get("tool_name") or event.get("toolName") or ""
    args = event.get("tool_input", event.get("toolArgs")) or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {"command": args}
    if not isinstance(args, dict):
        return []
    if tool == "apply_patch":
        patch = args.get("command") or args.get("input") or ""
        if isinstance(patch, list):
            patch = "\n".join(map(str, patch))
        base = Path(event.get("cwd") or ROOT)
        raw = [base / p for p in PATCHED.findall(str(patch))]
    else:
        raw = [Path(str(args[key])) for key in ("file_path", "path", "notebook_path") if args.get(key)]
    out = []
    for path in raw:
        if not path.is_absolute():
            path = ROOT / path
        try:
            out.append(path.resolve().relative_to(ROOT.resolve()).as_posix())
        except ValueError:
            continue  # outside this repository
    return out


def listed(state, session):
    listing = state / f"{session}.files"
    if not listing.exists():
        return []
    files = []
    for line in listing.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and line not in files:
            files.append(line)
    return files


def matches(path, patterns):
    return any(re.search(p, path) for p in patterns)


def record(event, cfg, state, session):
    hits = [p for p in edited_paths(event)
            if matches(p, cfg.get("patterns", [])) and not matches(p, cfg.get("exclude", []))]
    if hits:
        with (state / f"{session}.files").open("a", encoding="utf-8") as f:
            f.writelines(p + "\n" for p in hits)


def report(event, message, block):
    message = f"Stop hook ({ROOT.name}/.claude/hooks/validate.py): {message}"
    if block and not event.get("stop_hook_active"):
        print(json.dumps({"decision": "block", "reason": message}))
    else:
        print(json.dumps({"systemMessage": message}))


def executable(name):
    if "/" in name or os.sep in name:
        path = ROOT / name
        return str(path) if path.exists() else None
    return shutil.which(name)


def check(event, cfg, state, session):
    files = [f for f in listed(state, session) if (ROOT / f).is_file()]
    if not files:
        return
    hashed = git("hash-object", "--", *files).stdout
    fingerprint = hashlib.sha1(hashed.encode()).hexdigest()
    passed = state / f"{session}.passed"
    if passed.exists() and passed.read_text(encoding="utf-8").strip() == fingerprint:
        return
    if cfg.get("error"):
        report(event, cfg["error"], block=False)
        return
    commands = [(" ".join(c), list(c)) for c in cfg.get("checks", [])]
    if cfg.get("format"):
        targets = [f for f in files if matches(f, cfg.get("format_patterns") or [""])]
        if targets:
            commands.append((" ".join(cfg["format"]) + f" ({len(targets)} edited files)", list(cfg["format"]) + targets))
    for label, argv in commands:
        exe = executable(argv[0])
        if exe is None:
            report(event, f"`{argv[0]}` is not available here, so {'`' + label + '`' if label != argv[0] else 'it'} did not run.", block=False)
            return
        try:
            run = subprocess.run([exe, *argv[1:]], cwd=ROOT, capture_output=True, text=True, errors="replace")
        except OSError as error:
            report(event, f"`{label}` could not start: {error}", block=False)
            return
        if run.returncode:
            tail = "\n".join((run.stdout + run.stderr).splitlines()[-TAIL:])
            report(event, f"`{label}` failed:\n{tail}\nFix what this session changed. If the errors are in files this "
                          "session did not edit, another session owns them: report that and do not change them.",
                   block=True)
            return
    passed.write_text(fingerprint, encoding="utf-8")


def resume(event, cfg, state, session):
    edited = listed(state, session)
    status = git("status", "--short").stdout.splitlines()
    if not edited and not status:
        return
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    lines = [f"Where this session was in {ROOT.name} (branch {branch}):"]
    if edited:
        lines.append("Files this session edited (the Stop hook checks them):")
        lines += [f"  {p}" for p in edited[:20]]
        if len(edited) > 20:
            lines.append(f"  ... and {len(edited) - 20} more")
    if status:
        lines.append("git status --short:")
        lines += [f"  {s}" for s in status[:30]]
        if len(status) > 30:
            lines.append(f"  ... and {len(status) - 30} more")
    log = git("log", "--oneline", "-5").stdout.splitlines()
    if log:
        lines.append("Recent commits:")
        lines += [f"  {entry}" for entry in log]
    print("\n".join(lines))


MODES = {"record": record, "check": check, "resume": resume}


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        event = json.load(sys.stdin)
    except ValueError:
        event = {}
    if not isinstance(event, dict):
        event = {}
    state = state_dir()
    if mode in MODES and state is not None:
        MODES[mode](event, load_config(), state, session_of(event))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # fail open: a hook must never break the session
        print(f"validate.py: {error}", file=sys.stderr)
    sys.exit(0)
