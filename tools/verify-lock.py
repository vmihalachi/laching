#!/usr/bin/env python3
"""Hold a named verification build lock until signalled or its verifier exits.

The shell owns command execution (including shell functions); this process owns only the OS lock.
Persistent lock files avoid unlink/recreate races. PID contents are diagnostic, never ownership authority.
"""

import os
from pathlib import Path
import signal
import sys
import time


def hold(path: Path, ready: Path, owner: int) -> None:
    if os.name == "nt":
        import msvcrt
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        parent = os.getppid()  # Git Bash's shell PID and the native process ID can differ.

        def alive():
            process = kernel.OpenProcess(0x1000, False, parent)
            if not process:
                raise ProcessLookupError(parent)
            try:
                code = wintypes.DWORD()
                if not kernel.GetExitCodeProcess(process, ctypes.byref(code)) or code.value != 259:
                    raise ProcessLookupError(parent)
            finally:
                kernel.CloseHandle(process)

        def acquire(handle):
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        def alive():
            os.kill(owner, 0)

        def acquire(handle):
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def stop(*_):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    # Open without truncating or append mode: a waiting process must not change the current owner's metadata.
    # OS byte-range locks can cover an empty file on Windows too.
    with os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT, 0o600), "r+b") as handle:
        while True:
            try:
                acquire(handle)
                break
            except (BlockingIOError, PermissionError):
                pass
            alive()
            time.sleep(0.1)
        handle.seek(0)
        handle.write(f"{owner}\n".encode("ascii"))
        handle.truncate()
        handle.flush()
        ready.touch()
        while True:
            alive()
            time.sleep(0.2)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf8")
    try:
        hold(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]))
    except ProcessLookupError:
        pass
