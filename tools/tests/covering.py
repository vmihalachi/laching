"""Which tools tests cover which files, so `verify.sh --fast` runs only those (`run.py --covering PATH...`).

A test covers a file when it imports it, runs it, or runs a tool that does. The fast run leaves out what only an app's
verify.sh exercises end to end (test_scripts' `verify.sh --all --fast` runs l10n.py, phrases.py, voice.py and the rest
on the fixture app): `verify.sh` without `--fast`, and CI, run every test. Standard library only, and runs on any
python3, as verify.sh calls it before run.py picks one.
"""

from __future__ import annotations

from fnmatch import fnmatchcase

ALL = None
"""The files every test leans on: a change runs them all."""

EVERY = ("test_scripts.SharedFilesTests",)
"""What reads every file under tools/: no app is named, every skill is well formed."""

SHARED = (
    "tools/companion.py",
    "tools/companion.schema.json",  # companion.py checks the fixture app against it
    "tools/verify-lib.sh",
    "tools/tests/run.py",
    "tools/tests/support.py",
    "tools/tests/fixture-app/*",
)

# fnmatch patterns (`*` crosses folders), relative to the repository's root → the tests that cover them.
COVERS: dict[str, tuple[str, ...]] = {
    "tools/tests/covering.py": ("test_covering",),
    "tools/tests/sunnysays.companion.json": ("test_companion", "test_scripts"),
    "tools/avd.py": ("test_screens", "test_scripts"),  # check-layout-android.py
    "tools/bump-apps.sh": ("test_pins",),
    "tools/character.py": ("test_character", "test_scripts"),
    "tools/check-layout-android.py": ("test_scripts",),
    "tools/check-layout-ios.py": ("test_scripts",),
    "tools/check-layout.sh": ("test_scripts",),
    "tools/conform.py": ("test_conform", "test_pins"),
    "tools/conformance.sh": ("test_scripts",),
    "tools/dev-icon.swift": ("test_scripts",),  # install-dev.sh
    "tools/fonts.py": ("test_scripts",),
    "tools/hooks/*": ("test_hook",),
    "tools/install-dev.sh": ("test_scripts",),
    "tools/l10n.py": ("test_l10n",),
    "tools/layoutcheck.py": ("test_screens", "test_scripts"),  # check-layout-ios.py and -android.py
    "tools/phrases.py": ("test_l10n",),
    "tools/screens.py": ("test_screens", "test_scripts"),  # screenshots.sh, check-layout-android.py
    "tools/screenshots.sh": ("test_scripts",),
    "tools/sim.py": ("test_screens", "test_scripts"),  # check-layout-ios.py
    "tools/store-screenshots.py": ("test_scripts",),
    "tools/store/*": ("test_store", "test_push_microsoft"),
    "tools/synth.py": ("test_synth", "test_scripts"),
    "tools/verify.sh": ("test_scripts", "test_hook"),
    "tools/voice.py": ("test_voice",),
    "tools/voice-review.html": ("test_voice",),
    "tools/windows/vm.py": ("test_vm",),
    "tools/windows/vm.sh": ("test_vm", "test_scripts"),
    "tools/windows/vm-guest.ps1": ("test_vm",),
    "tools/windows/screenshots.ps1": ("test_windows", "test_scripts", "test_vm"),
    "tools/windows/check-layout.ps1": ("test_windows", "test_vm"),
    "tools/windows/*": ("test_windows",),
    # Read by no test but EVERY's.
    "tools/README.md": (),
    "tools/setup-linux.sh": (),
    "tools/skills/*": (),
    "tools/landing/*": (),  # verify.sh's @laching/landing check tests it
    # The repository's fixture skins, which test_character compiles.
    "Tests/Fixtures/*": ("test_character",),
}


def covering(paths: list[str]) -> list[str] | None:
    """The tests (module names, or module.Class) that cover `paths` (relative to the root, forward slashes), in a
    stable order; ALL when one is shared or has no mapping. A test file covers itself."""
    names: list[str] = []
    for path in paths:
        if any(fnmatchcase(path, pattern) for pattern in SHARED):
            return ALL
        tests = test_file(path)
        if tests is None:
            tests = next((covers for pattern, covers in COVERS.items() if fnmatchcase(path, pattern)), None)
        if tests is None:
            return ALL
        if path.startswith("tools/"):
            tests = (*tests, *EVERY)
        names += [name for name in tests if name not in names]
    whole = {name for name in names if "." not in name}
    return sorted(name for name in names if "." not in name or name.split(".")[0] not in whole)


def test_file(path: str) -> tuple[str, ...] | None:
    """A test file's own module, for tools/tests/test_*.py; None for any other path."""
    folder, _, name = path.rpartition("/")
    if folder == "tools/tests" and name.startswith("test_") and name.endswith(".py"):
        return (name[: -len(".py")],)
    return None
