#!/usr/bin/env python3
"""Tests for tools/windows/: check-winui.py's rules, assets.py's images from the fixture app's icon, smoke-package.ps1's
package check and the PowerShell scripts parsing (where PowerShell is installed), companion.targets as MSBuild XML."""

from __future__ import annotations

import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ElementTree
import zipfile
import zlib
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "windows"))
import assets  # noqa: E402
from support import TOOLS, fixture_app, git  # noqa: E402

WINDOWS = TOOLS / "windows"
CHECK_WINUI = WINDOWS / "check-winui.py"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


def icon_png(path: Path, size: int, margin: int, rgba=(200, 30, 40, 255)) -> None:
    """A square icon: an opaque block with a transparent `margin` around it, as a Mac app icon leaves."""
    inside = range(margin, size - margin)
    raw = b"".join(
        b"\x00" + b"".join(bytes(rgba) if x in inside and y in inside else b"\0\0\0\0" for x in range(size))
        for y in range(size))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class CheckWinUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def lint(self, *paths: Path) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(CHECK_WINUI), *map(str, paths)],
                              capture_output=True, encoding="utf8")

    def write(self, name: str, text: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, "utf8")
        return path

    def test_untyped_xaml_casts(self):
        for line in (
            "var s = (Style)Application.Current.Resources[\"Card\"];",
            "var p = GetTemplateChild(\"Part\") as Border;",
            "if (VisualTreeHelper.GetParent(e) is Grid g) {}",
            "if (window.AppWindow.Presenter is OverlappedPresenter p) {}",
            "var b = (Brush)e.NewValue;",
            "var f = (FontFamily?)GetValue(FontProperty);",
        ):
            with self.subTest(line=line):
                done = self.lint(self.write("A.cs", line + "\n"))
                self.assertEqual(done.returncode, 1, done.stdout)
                self.assertIn("XamlCast", done.stdout)

    def test_fine_csharp(self):
        path = self.write("B.cs", "\n".join((
            "var s = XamlCast.As<Style>(Application.Current.Resources[\"Card\"]);",
            "if (GetTemplateChild(\"Part\") is null) return;",
            "var n = (double)e.NewValue;",
            "var b = (Brush)e.NewValue; // winui-check: ok, set in code to a SolidColorBrush",
        )))
        done = self.lint(path)
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("ok: 1 WinUI files", done.stdout)

    def test_xaml_rules(self):
        for line, words in (
            ('<Setter Property="Data" Value="M0,0 L1,1" />', "set it on each element"),
            ('<Setter Property="Shapes.StrokeDashArray" Value="2 2" />', "set it on each element"),
            ('<Grid x:Name="Language" />', "hides FrameworkElement.Language"),
            ('<Border Margin="{x:Bind local:Inset.Of(Model, 12)}" />', "WMC9999"),
            ('<Border Margin="{x:Bind Inset(-0.5)}" />', "WMC9999"),
        ):
            with self.subTest(line=line):
                done = self.lint(self.write("Proj/Views/A.xaml", line + "\n"))
                self.assertEqual(done.returncode, 1, done.stdout)
                self.assertIn(words, done.stdout)
        fine = self.write("Proj/Views/B.xaml", '<Border Margin="{x:Bind local:Inset.Card}" Tag="{x:Bind Name(Model)}" />\n')
        self.assertEqual(self.lint(fine).returncode, 0)

    def test_the_app_xaml_sits_next_to_the_project(self):
        app = '<shell:CompanionApp x:Class="Fixture.App" xmlns:shell="using:Laching.Windows.Companion">\n</shell:CompanionApp>\n'
        self.write("Proj/Proj.csproj", "<Project />\n")
        nested = self.write("Proj/App/App.xaml", app)
        done = self.lint(nested)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("must sit next to the project file", done.stdout)
        self.assertEqual(self.lint(self.write("Proj/App.xaml", app)).returncode, 0)

    def test_a_folder_skips_build_output_and_no_arguments_read_companion_json(self):
        self.write("Proj/Views/A.cs", "var x = 1;\n")
        self.write("Proj/obj/Generated.cs", "var s = (Style)Resources[\"x\"];\n")
        done = self.lint(self.root / "Proj")
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("ok: 1 WinUI files", done.stdout)
        with fixture_app() as app:
            bad = app / "windows/Fixture.App/Views/Home.cs"
            bad.parent.mkdir(parents=True)
            bad.write_text("var s = (Style)Resources[\"x\"];\n", "utf8")
            done = subprocess.run([sys.executable, str(CHECK_WINUI)], capture_output=True, encoding="utf8", cwd=app)
            self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
            self.assertIn("Home.cs:1", done.stdout)


class AssetsTests(unittest.TestCase):
    def run_assets(self, *args: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = assets.main(list(args))
        return code, out.getvalue() + err.getvalue()

    def test_the_set_from_the_icon_and_the_check(self):
        with fixture_app() as root:
            icon_png(root / "macos/Fixture/Resources/AppIcon.png", 96, margin=16)
            icon_png(root / "macos/Fixture/Resources/AppIcon-Dev.png", 96, margin=16, rgba=(30, 30, 200, 255))
            code, said = self.run_assets("--check")
            self.assertEqual(code, 1, "nothing written yet")
            self.assertIn("StoreLogo.scale-100.png", said)

            self.assertEqual(self.run_assets()[0], 0)
            out = root / "windows/Fixture.App/Assets"
            names = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
            self.assertIn("Square44x44Logo.targetsize-24_altform-unplated.png", names)
            self.assertIn("Wide310x150Logo.scale-200.png", names)
            self.assertIn("Dev/AppIcon.ico", names)
            self.assertEqual(len(names), 22)

            # The trimmed icon fills a plain logo edge to edge; the wide one is centred with clear sides.
            small = assets.read_png(out / "Square44x44Logo.targetsize-16.png")
            self.assertEqual((small.width, small.height), (16, 16))
            self.assertGreater(small.pixels[3], 250, "the corner is drawn: the margin was trimmed")
            wide = assets.read_png(out / "Wide310x150Logo.scale-100.png")
            self.assertEqual((wide.width, wide.height), (310, 150))
            self.assertEqual(wide.pixels[3], 0)
            middle = 4 * (75 * 310 + 155)
            self.assertGreater(wide.pixels[middle + 3], 250)

            ico = (out / "AppIcon.ico").read_bytes()
            _, kind, count = struct.unpack("<HHH", ico[:6])
            self.assertEqual((kind, count), (1, len(assets.ICO_SIZES)))
            sides = [ico[6 + 16 * i] or 256 for i in range(count)]
            self.assertEqual(sides, assets.ICO_SIZES)
            self.assertNotEqual(ico, (out / "Dev/AppIcon.ico").read_bytes())

            self.assertEqual(self.run_assets("--check")[0], 0)
            (out / "StoreLogo.scale-200.png").write_bytes(b"changed")
            code, said = self.run_assets("--check")
            self.assertEqual(code, 1)
            self.assertIn("StoreLogo.scale-200.png", said)
            self.assertNotIn("StoreLogo.scale-100.png", said)

    def test_needs_the_section(self):
        with fixture_app() as root:
            config = root / "companion.json"
            data = json.loads(config.read_text("utf8"))
            del data["windows"]["assets"]
            config.write_text(json.dumps(data), "utf8")
            code, said = self.run_assets()
            self.assertEqual(code, 1)
            self.assertIn("no windows.assets", said)


class TargetsTests(unittest.TestCase):
    def test_companion_targets_is_msbuild(self):
        project = ElementTree.parse(WINDOWS / "companion.targets").getroot()
        self.assertEqual(project.tag, "Project")
        targets = {t.get("Name") for t in project.iter("Target")}
        self.assertIn("CompanionRemoveRuntimeFilesFromNativePackage", targets)
        self.assertIn("CompanionKeepVoiceOutOfPri", targets)


@unittest.skipUnless(POWERSHELL, "needs PowerShell")
class PowerShellTests(unittest.TestCase):
    def powershell(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", *args], capture_output=True, text=True, env=env)

    def layout_tools(self, folder: Path, failing: str = "") -> None:
        """check-layout.ps1 beside a screenshots.ps1 that records each call (a JSON file per call, since the languages
        run at once) and reports a problem in the `failing` language, as screenshots.ps1 does."""
        shutil.copy(WINDOWS / "check-layout.ps1", folder / "check-layout.ps1")
        (folder / "screenshots.ps1").write_text(
            "param([switch] $BuildOnly, [switch] $NoBuild, [switch] $Hidden, [string] $Project, [string] $Platform,\n"
            "      [string] $Language, [string] $Out, [string[]] $Only)\n"
            "@{ BuildOnly = [bool]$BuildOnly; NoBuild = [bool]$NoBuild; Hidden = [bool]$Hidden; Language = $Language;\n"
            "   Out = $Out; Only = ($Only -join ',') } | ConvertTo-Json -Compress |\n"
            "   Set-Content -LiteralPath (Join-Path $PSScriptRoot \"call-$([guid]::NewGuid()).json\")\n"
            f"if ($Language -eq '{failing}') {{\n"
            "    [Console]::Error.WriteLine(\"Problems ($Language):\")\n"
            "    [Console]::Error.WriteLine('  panel-light.png: \"Settings\" is cut off with an ellipsis')\n"
            "    exit 1\n"
            "}\n", "utf8")

    def test_check_layout_builds_once_then_runs_the_languages_hidden(self):
        with fixture_app() as root, tempfile.TemporaryDirectory() as temp:
            project = root / "windows" / "Fixture.App" / "Fixture.App.csproj"
            project.parent.mkdir(parents=True)
            project.write_text("<Project />", "utf8")
            tools = Path(temp)
            self.layout_tools(tools, failing="de")

            def calls() -> list[dict]:
                found = [json.loads(path.read_text("utf-8-sig")) for path in tools.glob("call-*.json")]
                for path in tools.glob("call-*.json"):
                    path.unlink()
                return found

            done = self.powershell("-File", str(tools / "check-layout.ps1"))
            self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
            self.assertEqual([line for line in done.stdout.splitlines() if line.startswith("==")], ["== en", "== it", "== de"])
            self.assertIn("panel-light.png: \"Settings\" is cut off", done.stderr)
            self.assertIn("Layout problems in: de", done.stderr)
            made = calls()
            self.assertEqual(sum(call["BuildOnly"] for call in made), 1, "one build for every language")
            runs = sorted((call for call in made if not call["BuildOnly"]), key=lambda call: call["Language"])
            self.assertEqual([call["Language"] for call in runs], ["de", "en", "it"], "languages.all")
            for call in runs:
                self.assertTrue(call["NoBuild"] and call["Hidden"], call)
                self.assertEqual(Path(call["Out"]), root / "windows" / "build" / "layout-check" / call["Language"])

            done = self.powershell("-File", str(tools / "check-layout.ps1"), "-Only", "panel,home", "-Jobs", "1", "en", "it")
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("Every window fits in: en it", done.stdout)
            runs = [call for call in calls() if not call["BuildOnly"]]
            self.assertEqual([(call["Language"], call["Only"]) for call in runs], [("en,it", "panel,home")], "one run for both")
            self.assertEqual(Path(runs[0]["Out"]), root / "windows" / "build" / "layout-check")

    def test_screenshots_build_once_or_not_at_all(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            project = folder / "Fixture.App.csproj"
            project.write_text("<Project />", "utf8")
            app = folder / "bin"
            app.mkdir()
            props = folder / "props.json"
            props.write_text(json.dumps({"Properties": {"TargetDir": str(app) + os.sep, "AssemblyName": "Fixture.App"}}), "utf8")
            log = folder / "dotnet.log"
            fake = folder / "fake"
            fake.mkdir()
            # dotnet logs its command and answers the property query.
            if os.name == "nt":
                (fake / "dotnet.cmd").write_text(f'@echo off\r\necho %1>> "{log}"\r\nif "%1"=="msbuild" type "{props}"\r\n', "utf8")
            else:
                (fake / "dotnet").write_text(f'#!/bin/sh\necho "$1" >> "{log}"\n[ "$1" != msbuild ] || cat "{props}"\n', "utf8")
                (fake / "dotnet").chmod(0o755)
            env = {**os.environ, "PATH": str(fake) + os.pathsep + os.environ["PATH"]}

            def shots(*args: str) -> tuple[subprocess.CompletedProcess, list[str]]:
                log.unlink(missing_ok=True)
                done = self.powershell("-File", str(WINDOWS / "screenshots.ps1"), "-Project", str(project), *args, env=env)
                return done, log.read_text().split() if log.exists() else []

            done, _ = shots("-NoBuild")
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("build it first", done.stdout + done.stderr)
            (app / "Fixture.App.exe").write_bytes(b"")
            done, ran = shots("-BuildOnly")
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertEqual(ran, ["build", "msbuild"], "one property query for the folder and the name")
            done, ran = shots("-BuildOnly", "-NoBuild")
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertEqual(ran, ["msbuild"])

    def test_the_scripts_parse(self):
        for script in sorted(WINDOWS.glob("*.ps1")):
            with self.subTest(script=script.name):
                done = self.powershell("-Command", (
                    "$errors = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
                    f"'{script}', [ref]$null, [ref]$errors); $errors | ForEach-Object {{ $_.ToString() }}"))
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(done.stdout.strip(), "")

    def package(self, folder: Path, *names: str) -> Path:
        manifest = ('<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10">'
                    '<Identity Name="Fixture" Publisher="CN=Fixture" Version="1.0.0.0" ProcessorArchitecture="x64" />'
                    '<Applications><Application Id="App" Executable="Fixture.exe" /></Applications></Package>')
        path = folder / "Fixture.msix"
        with zipfile.ZipFile(path, "w") as package:
            package.writestr("AppxManifest.xml", manifest)
            for name in names:
                package.writestr(name, b"x")
        return path

    def test_smoke_package_check_only(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)

            def check(*names: str) -> subprocess.CompletedProcess:
                package = self.package(folder, *names)
                return self.powershell("-File", str(WINDOWS / "smoke-package.ps1"), "-Package", str(package), "-CheckOnly")

            done = check("Fixture.exe", "Microsoft.WindowsAppRuntime.Bootstrap.dll")
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertIn("native, 3 files", done.stdout)
            done = check("Fixture.exe", "Fixture.runtimeconfig.json")
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("runtimeconfig.json", done.stdout + done.stderr)
            done = check("Other.exe")
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("has no Fixture.exe", done.stdout + done.stderr)

    def test_smoke_package_screenshots_stay_out_of_appdata(self):
        if not os.environ.get("LOCALAPPDATA"):
            self.skipTest("no LOCALAPPDATA here")
        with tempfile.TemporaryDirectory() as temp:
            package = self.package(Path(temp), "Fixture.exe")
            done = self.powershell("-File", str(WINDOWS / "smoke-package.ps1"), "-Package", str(package),
                                   "-Screenshots", str(Path(os.environ["LOCALAPPDATA"]) / "shots"))
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("outside AppData", done.stdout + done.stderr)

    def release_app(self, root: Path, version: str = "1.0.3.0") -> None:
        """The fixture app as a repository with an origin, its WinUI project's manifest at `version`."""
        app = root / "windows" / "Fixture.App"
        app.mkdir(parents=True, exist_ok=True)
        (app / "Package.appxmanifest").write_text(
            '<?xml version="1.0" encoding="utf-8"?><Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/'
            f'windows10"><Identity Name="Fixture" Publisher="CN=Fixture" Version="{version}" /></Package>', "utf8")
        origin = root.parent / "origin.git"
        git(root.parent, "init", "-q", "--bare", str(origin))
        git(root, "init", "-q")
        git(root, "remote", "add", "origin", str(origin))
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "app")

    def release(self, root: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        return self.powershell("-File", str(WINDOWS / "store-release.ps1"), *args,
                               env={**os.environ, "COMPANION_ROOT": str(root), **(env or {})})

    def test_store_release_version_and_tag(self):
        with fixture_app() as root, tempfile.TemporaryDirectory() as temp:
            # Keys that differ only in case (a character's glyph table), which Windows PowerShell's ConvertFrom-Json refuses.
            config = root / "companion.json"
            config.write_text(config.read_text("utf8").replace('"name": "fixture",', '"name": "fixture", "glyphs": {"A": 1, "a": 2},'), "utf8")
            self.release_app(root)
            output = Path(temp) / "github-output"
            done = self.release(root, "-VersionOnly", env={"GITHUB_OUTPUT": str(output)})
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertEqual(done.stdout.strip(), "1.0.3.0")
            self.assertEqual(output.read_bytes(), b"version=1.0.3.0\n", "no BOM, which GitHub would read as part of the name")

            # -TagOnly tags HEAD and pushes the tag; from then on the version is refused, unless -AnyVersion.
            done = self.release(root, "-TagOnly")
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertIn("win-1.0.3.0", git(root, "ls-remote", "--tags", "origin"))
            done = self.release(root, "-VersionOnly")
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("win-1.0.3.0 exists: bump the Identity Version", done.stdout + done.stderr)
            done = self.release(root, "-VersionOnly", "-AnyVersion")
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            done = self.release(root, "-TagOnly")
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("exists already", done.stdout + done.stderr)

    def test_store_release_refuses_what_it_cant_build(self):
        with fixture_app() as root:
            self.release_app(root, version="1.0")
            done = self.release(root, "-VersionOnly")
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("No Identity Version", done.stdout + done.stderr)
            done = self.release(root, "-VersionOnly", "-Platforms", "x64,x86")
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("Platform x86: the Store packages are x64 and ARM64", done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
