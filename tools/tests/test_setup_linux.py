#!/usr/bin/env python3
"""Tests for tools/setup-linux.sh over the fixture app, with fake `git`, `dotnet`, `java`, `apt-get`, `sudo`, `curl`,
`node` and `npm` on a PATH that has nothing else of theirs, and a temporary HOME."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support import NO_SHEBANG, TOOLS, WINDOWS, FixtureAppTestCase  # noqa: E402

# What the script and the fakes need of the real system; dotnet, java, node, npm, ktlint and the rest stay off PATH.
REAL = ("bash", "cat", "chmod", "dirname", "grep", "mkdir", "sed")

# The fakes log their arguments to $LOG and keep what they installed under $STATE.
FAKES = {
    "git": """echo "git $*" >>"$LOG"
[ "$1 $2" = "ls-files -s" ] && [ -e "$STATE/gitlink" ] && printf '160000 %040d 0\\tlanding\\n' 0
exit 0
""",
    "dotnet": """echo "dotnet $*" >>"$LOG"
case "$1" in
  --list-sdks) echo "${FAKE_SDK:-10.0.100} [/fake/sdk]" ;;
  --version) echo "${FAKE_SDK:-10.0.100}" ;;
esac
""",
    # dotnet-install.sh ($INSTALLER, which `bash` runs) and ktlint.
    "curl": """echo "curl $*" >>"$LOG"
while [ $# -gt 1 ]; do [ "$1" = -o ] && out="$2"; shift; done
case "$out" in
  *dotnet-install.sh) cat "$INSTALLER" >"$out" ;;
  *) echo ktlint >"$out" ;;
esac
""",
    "java": """[ -e "$STATE/jdk" ] && v=17.0.12 || v=11.0.24
echo "openjdk version \\"$v\\" 2024-07-16" >&2
""",
    # The first install fails until an update ran, as on a container whose package lists are empty.
    "apt-get": """echo "apt-get $*" >>"$LOG"
case "$1" in
  update) : >"$STATE/updated" ;;
  install) [ -e "$STATE/updated" ] || exit 100; : >"$STATE/jdk" ;;
esac
""",
    "sudo": """echo "sudo $*" >>"$LOG"
[ "$1" = -n ] && shift
exec "$@"
""",
    "id": """[ "$1" = -u ] && echo "${FAKE_UID:-1000}"
""",
    "node": """case "$1" in
  -p) echo "${FAKE_NODE:-24}" ;;
  -v) echo "v${FAKE_NODE:-24}.11.0" ;;
esac
""",
    "npm": """echo "npm $*" >>"$LOG"
[ "$1" = ci ] && [ "$2" = --prefix ] && mkdir -p "$3/node_modules"
exit 0
""",
}

# A .NET 10 SDK in --install-dir.
INSTALLER = r"""echo "dotnet-install $*" >>"$LOG"
mkdir -p "$HOME/.dotnet"
printf '#!/bin/sh\necho 10.0.100\n' >"$HOME/.dotnet/dotnet"
chmod +x "$HOME/.dotnet/dotnet"
"""


@unittest.skipIf(WINDOWS, NO_SHEBANG)
class SetupLinuxTests(FixtureAppTestCase):
    def setUp(self):
        super().setUp()
        self.home = self.root.parent / "home"
        self.state = self.root.parent / "state"
        self.bin = self.root.parent / "bin"
        for folder in (self.home, self.state, self.bin, self.root.parent / "tmp"):
            folder.mkdir()
        self.log = self.root.parent / "calls.log"
        self.env_file = self.root.parent / "claude.env"
        self.github_path = self.root.parent / "github-path"
        self.log.touch()
        self.installer = self.root.parent / "dotnet-install.sh"
        self.installer.write_text(INSTALLER, "utf8")
        for name, body in FAKES.items():
            if name != "dotnet":  # none on PATH: the script installs one
                self.fake(name, body)
        for name in REAL:
            (self.bin / name).symlink_to(shutil.which(name))
        (self.bin / "python3").symlink_to(sys.executable)
        # Windows with its core tests, Android and a landing page kept in a submodule: every step has work.
        def every_step(config):
            config["windows"]["coreTests"] = "Fixture.Core.Tests"
            config.update(android={"dir": "android"}, landing={"dir": "landing"})
        self.config(every_step)
        tests = self.root / "windows/Fixture.Core.Tests/Fixture.Core.Tests.csproj"
        tests.parent.mkdir(parents=True)
        tests.write_text("<Project />\n", "utf8")
        (self.root / "landing").mkdir()
        (self.state / "gitlink").touch()

    def fake(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body, "utf8")
        path.chmod(0o755)

    def config(self, change) -> None:
        path = self.root / "companion.json"
        config = json.loads(path.read_text("utf8"))
        change(config)
        path.write_text(json.dumps(config, indent=2) + "\n", "utf8")

    def run_setup(self, **env: str):
        # JAVA_HOME too: a GitHub runner's JAVA_HOME_17_X64 would stand in for the fake JDK.
        keep = {key: value for key, value in os.environ.items()
                if not key.startswith(("CLAUDE", "GITHUB", "DOTNET", "FAKE", "JAVA_HOME"))}
        environment = {
            **keep, "PATH": str(self.bin), "HOME": str(self.home), "TMPDIR": str(self.root.parent / "tmp"),
            "LOG": str(self.log), "STATE": str(self.state), "INSTALLER": str(self.installer),
            "CLAUDE_ENV_FILE": str(self.env_file),
            # A runner's own, which the script may add to: never the real one of the job running these tests.
            "GITHUB_PATH": str(self.github_path), **env,
        }
        self.log.write_text("", "utf8")
        return subprocess.run([str(TOOLS / "setup-linux.sh")], cwd=self.root, env=environment,
                              capture_output=True, encoding="utf8")

    def exports(self) -> str:
        """What the run handed the rest of the session in CLAUDE_ENV_FILE, "" when it wrote nothing there."""
        return self.env_file.read_text("utf8") if self.env_file.exists() else ""

    def calls(self) -> list[str]:
        return self.log.read_text("utf8").splitlines()

    def test_a_first_run_installs_everything_and_a_second_says_only_done(self):
        first = self.run_setup()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stderr, "")
        calls = self.calls()
        home = str(self.home)
        for call in (
            "git submodule update --init laching voices",
            "git submodule update --init landing",
            "git config submodule.recurse true",
            "git config push.recurseSubmodules on-demand",
            f"curl -fsSL https://dot.net/v1/dotnet-install.sh -o {self.root.parent / 'tmp'}/dotnet-install.sh",
            f"dotnet-install --channel 10.0 --install-dir {home}/.dotnet",
            "sudo -n apt-get install -y -qq openjdk-17-jdk-headless",
            "sudo -n apt-get update -qq",
            f"curl -fsSL https://github.com/pinterest/ktlint/releases/download/1.8.0/ktlint -o {home}/.local/bin/ktlint",
            "npm ci --prefix landing --no-audit --no-fund",
        ):
            self.assertIn(call, calls)
        self.assertFalse(any("core.hooksPath" in call for call in calls), calls)
        # The JDK is installed again after the update.
        installs = [index for index, call in enumerate(calls) if call.startswith("sudo -n apt-get install")]
        self.assertEqual(len(installs), 2, calls)
        self.assertLess(installs[0], calls.index("sudo -n apt-get update -qq"), calls)
        self.assertLess(calls.index("sudo -n apt-get update -qq"), installs[1], calls)
        self.assertTrue(os.access(self.home / ".local/bin/ktlint", os.X_OK))
        self.assertTrue((self.root / "landing/node_modules").is_dir())
        exports = self.exports()
        for line in (f'export DOTNET_ROOT="{home}/.dotnet"', 'export DOTNET_CLI_TELEMETRY_OPTOUT="1"',
                     f'export PATH="{home}/.dotnet:$PATH"'):
            self.assertIn(line, exports)
        self.assertIn(f'export PATH="{home}/.local/bin:$PATH"', exports)
        self.assertEqual(first.stdout.splitlines(), [
            "Installing the .NET 10 SDK into ~/.dotnet",
            "Installing OpenJDK 17",
            "Installing ktlint into ~/.local/bin",
            "Installing landing dependencies",
            "Linux setup done: 10.0.100, node v24.11.0, java 17.0.12",
        ])

        second = self.run_setup()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout, "Linux setup done: 10.0.100, node v24.11.0, java 17.0.12\n")
        self.assertEqual(second.stderr, "")
        self.assertFalse([call for call in self.calls() if call.startswith(("curl", "apt-get", "sudo", "npm"))])

    def test_hooks_are_set_only_with_a_githooks_folder(self):
        (self.root / ".githooks").mkdir()
        self.run_setup()
        self.assertIn("git config core.hooksPath .githooks", self.calls())

    def test_a_landing_page_in_the_app_is_not_fetched(self):
        (self.state / "gitlink").unlink()
        self.run_setup()
        self.assertIn("git ls-files -s -- landing", self.calls())
        self.assertNotIn("git submodule update --init landing", self.calls())

    def test_without_a_windows_section_dotnet_is_left_alone(self):
        self.config(lambda config: config.pop("windows"))
        result = self.run_setup()
        self.assertFalse([call for call in self.calls() if "dotnet" in call], self.calls())
        self.assertNotIn("DOTNET_ROOT", self.exports())
        self.assertTrue(result.stdout.endswith("Linux setup done: no dotnet, node v24.11.0, java 17.0.12\n"),
                        result.stdout)

    def test_a_dotnet_10_sdk_is_not_downloaded_and_restores_the_core_tests(self):
        self.fake("dotnet", FAKES["dotnet"])
        result = self.run_setup()
        self.assertNotIn("Installing the .NET 10 SDK", result.stdout)
        self.assertFalse([call for call in self.calls() if "dotnet-install" in call])
        self.assertIn("dotnet restore windows/Fixture.Core.Tests/Fixture.Core.Tests.csproj -v q --nologo", self.calls())
        self.assertNotIn("DOTNET_ROOT", self.exports())

    def test_an_older_dotnet_sdk_gets_10(self):
        self.fake("dotnet", FAKES["dotnet"])
        result = self.run_setup(FAKE_SDK="8.0.400")
        self.assertIn("dotnet --list-sdks", self.calls())
        self.assertIn("Installing the .NET 10 SDK into ~/.dotnet", result.stdout)

    def test_without_an_android_section_no_jdk_or_ktlint(self):
        self.config(lambda config: config.pop("android"))
        result = self.run_setup()
        self.assertFalse([call for call in self.calls() if call.startswith(("sudo", "apt-get")) or "ktlint" in call])
        self.assertFalse((self.home / ".local/bin/ktlint").exists())
        self.assertNotIn("java", result.stdout)

    def test_root_installs_the_jdk_without_sudo(self):
        self.run_setup(FAKE_UID="0")
        self.assertIn("apt-get install -y -qq openjdk-17-jdk-headless", self.calls())
        self.assertFalse([call for call in self.calls() if call.startswith("sudo")])

    def test_without_apt_get_it_only_warns(self):
        (self.bin / "apt-get").unlink()
        result = self.run_setup()
        self.assertEqual(result.returncode, 0)
        self.assertIn("warning: no JDK 17 and no apt-get; gradlew :core:test won't run", result.stderr)
        self.assertNotIn("Installing OpenJDK 17", result.stdout)
        self.assertTrue(result.stdout.endswith("java 11.0.24\n"), result.stdout)

    def test_a_jdk_17_is_kept(self):
        (self.state / "jdk").touch()
        result = self.run_setup()
        self.assertNotIn("Installing OpenJDK 17", result.stdout)
        self.assertFalse([call for call in self.calls() if "apt-get" in call])

    def test_an_existing_node_modules_is_never_touched(self):
        modules = self.root / "landing/node_modules"
        modules.mkdir()
        (modules / "kept").write_text("mine\n", "utf8")
        result = self.run_setup()
        self.assertNotIn("Installing landing dependencies", result.stdout)
        self.assertFalse([call for call in self.calls() if call.startswith("npm")])
        self.assertEqual([path.name for path in modules.iterdir()], ["kept"])

    def test_a_node_older_than_24_warns(self):
        result = self.run_setup(FAKE_NODE="22")
        self.assertIn("warning: Node v22.11.0 is older than Node 24", result.stderr)

    def test_without_node_it_warns(self):
        (self.bin / "node").unlink()
        result = self.run_setup()
        self.assertIn("warning: Node isn't installed; the landing checks won't run", result.stderr)
        self.assertIn("node none", result.stdout)


if __name__ == "__main__":
    unittest.main()
