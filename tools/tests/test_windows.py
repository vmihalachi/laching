#!/usr/bin/env python3
"""Tests for tools/windows/: check-winui.py's rules (the collection expressions of CsWinRT1032 on a sample that
builds), assets.py's images from the fixture app's icon, smoke-package.ps1's package check and the PowerShell scripts
parsing (where PowerShell is installed), companion.targets as MSBuild XML, dotnet-sdk.ps1's answer and the workflows
that run it, check-sdk.py's answer on a self-hosted runner and the workflows asking it first, the app workflows
running dotnet in windows.dir, and screenshots.ps1's second attempt of a pass that stopped early, read as text."""

from __future__ import annotations

import io
import json
import os
import re
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
from support import DOTNET_HOST, NO_SHEBANG, REPO, TOOLS, FixtureAppTestCase, fixture_app, git, search_path  # noqa: E402

WINDOWS = TOOLS / "windows"
CHECK_WINUI = WINDOWS / "check-winui.py"
CHECK_SDK = WINDOWS / "check-sdk.py"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
PWSH = shutil.which("pwsh")
"""The shell of the workflows' `shell: pwsh` steps (ConvertFrom-Json -AsHashtable), which Windows PowerShell isn't."""
PIN = '{"sdk": {"version": "11.0.999", "rollForward": "disable"}}'
"""A windows/global.json pinning an SDK no machine has."""


def workflow_steps(name: str, job: str) -> list[dict[str, str]]:
    """The steps of `job` in Laching's workflow `name`, each its keys and values as written: a nested key as
    `with.<key>` or `env.<key>`, a block scalar (`run: |`) as its lines. Enough YAML for the workflows' block style, as
    Python comes with no parser."""
    lines = (REPO / ".github/workflows" / name).read_text("utf8").splitlines()
    steps: list[dict[str, str]] = []
    key, block = "", False
    for line in lines[lines.index("    steps:", lines.index(f"  {job}:")) + 1:]:
        indent = len(line) - len(line.lstrip())
        if line.strip() and indent <= 4:
            break
        if line.startswith("      - "):
            steps.append({})
            line, indent = " " * 8 + line[8:], 8
        if block and (indent > 8 or not line.strip()):
            steps[-1][key] += line[10:] + "\n"
        elif line.strip() and not line.lstrip().startswith("#"):
            name, _, value = line.strip().partition(":")
            if indent == 8:
                key, block = name, value.strip() == "|"
                steps[-1][key] = "" if block else value.strip()
            else:
                steps[-1][f"{key}.{name}"] = value.strip()
    return steps


def step(steps: list[dict[str, str]], **match: str) -> dict[str, str]:
    """The one step with each of `match`'s keys at that value."""
    found = [s for s in steps if all(s.get(key) == value for key, value in match.items())]
    assert len(found) == 1, (match, found)
    return found[0]


def expand(text: str, values: dict[str, str]) -> str:
    """`text` with each ${{ expression }} replaced by its value in `values`, as the runner does before a step runs."""
    return re.sub(r"\$\{\{\s*(.*?)\s*\}\}", lambda match: values[match.group(1)], text)


def outputs(path: Path) -> dict[str, str]:
    """A step's outputs, from the $GITHUB_OUTPUT file it wrote."""
    return dict(line.split("=", 1) for line in path.read_text("utf-8-sig").splitlines() if line)


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


# Collection expressions as check-winui.py reads them: `// 1032` ends each line the Native AOT publish of a WinUI
# project reports (CsWinRT1032), and every other line is one the rule must leave alone. The two files build as they
# are (a net11.0 library with implicit usings, Proj referencing Lib).
LIBRARY = """\
namespace Lib;

public sealed record Pack(string Name, IReadOnlyList<string> Moves);

public static class Library
{
    public static void Show(int count, IReadOnlyList<string> names) { }
    public static void Either(IReadOnlyList<string> names) { }
    public static void Either(List<string> names) { }
    public static bool Among(this string word, IEnumerable<string> words) => words.Contains(word);
}

public static class Formats
{
    public static string List(IReadOnlyList<string> items) => string.Join(", ", items);
}
"""
SHAPES = """\
using Lib;

namespace Proj.Views;

public sealed class Shapes
{
    private static readonly IReadOnlyList<int> Sizes = [20, 30]; // 1032
    private static readonly IReadOnlyList<int> None = [];
    private static readonly IList<int> Listed = [1, 2];
    private static readonly ICollection<int> Gathered = [1, 2];
    private static readonly List<int> Made = [1, 2];
    private static readonly int[] Plain = [1, 2];
    private readonly Dictionary<string, int[]> _byName = new() { ["a"] = [1] };
    private IReadOnlyCollection<string> _names = new List<string>();
    private IEnumerable<string>? _later;

    public IReadOnlyList<string> Tags { get; } = ["a"]; // 1032
    public IEnumerable<char> Letters => ['a']; // 1032
    public IReadOnlyList<string> Same => [.. _names]; // 1032
    public IList<string> Fine => ["a"];
    public IReadOnlyCollection<string> Read
    {
        get { return ["a"]; } // 1032
    }

    public Shapes(bool dark, List<string> named, IReadOnlyList<string>? given)
    {
        _names = ["x", "y"]; // 1032
        _later ??= ["z"]; // 1032
        IReadOnlyList<string> themes = dark ? ["dark"] : ["light"]; // 1032
        IReadOnlyList<string> kept = named.Count > 0 ? named : ["light", "dark"];
        IReadOnlyList<string> other = given ?? ["a"];
        Take(["a"]); // 1032
        Take(names: ["a"], count: 2); // 1032
        Library.Show(1, ["a"]); // 1032
        Library.Either(["a"]);
        _ = "a".Among(["a", "b"]); // 1032
        Pack pack = new("Core", ["plank"]); // 1032
        var made = new Pack("Core", ["plank"]); // 1032
        Later(() => ["a"]); // 1032
        var holder = new Holder { Tags = ["a"] };
        int[] numbers = [1, 2];
        _ = numbers[0] + Sizes[1] + None.Count + Listed.Count + Gathered.Count + Made.Count + Plain.Length + _byName.Count;
        _names = ["quiet"]; // winui-check: ok, the debug window alone
        _ = (themes, kept, other, pack, made, holder);
    }

    private static void Take(IReadOnlyList<string> names, int count = 1) { }
    private static void Later(Func<IEnumerable<string>> make) { }

    public IReadOnlyList<string> Pick(int kind)
    {
        if (kind == 0)
        {
            return ["zero"]; // 1032
        }
        try
        {
            return Load();
        }
        catch (InvalidOperationException)
        {
            return ["failed"]; // 1032
        }
    }

    public IReadOnlyList<string> Kind(int kind) => kind switch
    {
        0 => ["zero"], // 1032
        1 => throw new ArgumentException("one"),
        _ => [],
    };

    public IReadOnlyList<string> Beside(int kind) => kind switch
    {
        0 => ["zero"],
        _ => Load(),
    };

    public async Task<IReadOnlyList<string>> LoadAsync()
    {
        await Task.Yield();
        return ["a"]; // 1032
    }

    public IEnumerable<int[]> Each()
    {
        yield return [1];
    }

    private List<string> Load() => ["a"];

    public Shapes? Next { get; set; }

    private void Add(IReadOnlyList<int> items) { }

    // A method reached through its receiver's type: List<T>.Add and List<T>'s constructor are not the ones declared
    // here, and what Make() gives isn't read.
    private void Receivers(Shapes peer, List<int[]> arrays, IReadOnlyList<string> names)
    {
        arrays.Add([1, 2]);
        _ = new List<string>([.. names]);
        peer.Add([1]); // 1032
        this.Add([2]); // 1032
        Add([3]); // 1032
        peer.Next!.Add([4]); // 1032
        var copy = new Shapes(false, [], null);
        copy.Next?.Add([5]); // 1032
        Make().Add([6]);
        _ = Formats.List(["a"]); // 1032
    }

    private static Shapes Make() => new(false, [], null);

    private sealed class Holder
    {
        public List<string> Tags { get; set; } = [];
    }
}
"""
REFERENCE = '<Project>\n  <ItemGroup>\n    <ProjectReference Include="..\\Lib\\Lib.csproj" />\n  </ItemGroup>\n</Project>\n'


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

    def collection_expressions(self, project: str, *paths: str) -> tuple[list[int], str]:
        """The lines of Shapes.cs that the rule reports, checking `paths` of a Proj with this project file."""
        self.write("Lib/Lib.csproj", "<Project />\n")
        self.write("Lib/Library.cs", LIBRARY)
        self.write("Proj/Proj.csproj", project)
        self.write("Proj/Views/Shapes.cs", SHAPES)
        done = self.lint(*(self.root / path for path in paths))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        lines = [int(number) for number in re.findall(r"Shapes\.cs:(\d+): a collection expression for ", done.stdout)]
        self.assertIn(f"\n{len(lines)} WinUI problem(s)", done.stdout)
        return lines, done.stdout

    def test_a_collection_expression_for_a_read_only_list_interface(self):
        marked = [number for number, line in enumerate(SHAPES.splitlines(), 1) if line.endswith("// 1032")]
        self.assertEqual(len(marked), 25)
        for paths in (("Proj",), ("Proj/Views/Shapes.cs",)):  # the app's folder, and one changed file as verify.sh passes it
            with self.subTest(paths=paths):
                lines, said = self.collection_expressions(REFERENCE, *paths)
                self.assertEqual(lines, marked)
        for words in (
            "Shapes.cs:7: a collection expression for IReadOnlyList<int> has no class of its own under Native AOT "
            "(CsWinRT1032); build an array or a List\n    private static readonly IReadOnlyList<int> Sizes = [20, 30]; // 1032\n",
            "Shapes.cs:18: a collection expression for IEnumerable<char> has",
            "Shapes.cs:23: a collection expression for IReadOnlyCollection<string> has",
            "Shapes.cs:83: a collection expression for Task<IReadOnlyList<string>> has",
        ):
            self.assertIn(words, said)

    def test_an_argument_is_read_from_what_the_project_and_its_references_declare(self):
        # Without the reference nothing says what Library.Show, Among, Pack and Formats.List take, so those calls
        # aren't reported.
        elsewhere = [number for number, line in enumerate(SHAPES.splitlines(), 1)
                     if line.endswith("// 1032") and re.search(r"Library\.|\.Among\(|\bPack\b|Formats\.", line)]
        self.assertEqual(len(elsewhere), 5)
        with_it, _ = self.collection_expressions(REFERENCE, "Proj")
        without, _ = self.collection_expressions("<Project />\n", "Proj")
        self.assertEqual(sorted(set(with_it) - set(without)), elsewhere)
        self.assertFalse(set(without) - set(with_it))

    def test_a_line_is_what_ends_at_a_newline(self):
        # A form feed in a comment ends no line for the compiler, nor for the numbers and the quoted lines
        # (str.splitlines() breaks there).
        done = self.lint(self.write("Odd.cs", "// a\fb c\nvar s = (Style)Application.Current.Resources[\"Card\"];\n"
                                              "IReadOnlyList<int> sizes = [1, 2];\n"))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("Odd.cs:2: casts an untyped XAML object; use XamlCast.As<T>\n"
                      "    var s = (Style)Application.Current.Resources[\"Card\"];\n", done.stdout)
        self.assertIn("Odd.cs:3: a collection expression for IReadOnlyList<int> has no class of its own under Native AOT "
                      "(CsWinRT1032); build an array or a List\n    IReadOnlyList<int> sizes = [1, 2];\n", done.stdout)

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


class DotnetSdkTests(unittest.TestCase):
    # The check exists once: every workflow that sets up .NET on a GitHub-hosted Windows runner asks the script first,
    # but the Store release, whose Native AOT package ships the SDK's runtime.
    def test_the_workflows_run_the_one_check(self):
        workflows = REPO / ".github/workflows"
        for name, run in (("dotnet.yml", "pwsh tools/windows/dotnet-sdk.ps1\n"),
                          ("web.yml", "pwsh ../tools/windows/dotnet-sdk.ps1\n"),
                          ("release.yml", "pwsh tools/windows/dotnet-sdk.ps1\n"),
                          # By its full path, as it runs in windows.dir. A Laching without the script falls through to
                          # setup-dotnet; the input never reaches run.
                          ("windows-app.yml", "|\n          $script = Join-Path $env:GITHUB_WORKSPACE "
                           "\"$env:LACHING_PATH/tools/windows/dotnet-sdk.ps1\"\n"
                           "          if (Test-Path $script) { pwsh $script } else {")):
            with self.subTest(workflow=name):
                text = (workflows / name).read_text("utf8")
                self.assertIn("        id: sdk\n", text)
                self.assertIn(f"        run: {run}", text)
                self.assertIn("steps.sdk.outputs.use != 'true'", text)
        release = (workflows / "windows-release.yml").read_text("utf8")
        self.assertNotIn("id: sdk", release)
        self.assertIn("      - uses: actions/setup-dotnet@v6\n        if: inputs.hosted\n", release)
        for workflow in sorted(workflows.glob("*.yml")):
            with self.subTest(copy=workflow.name):
                self.assertNotIn("releases-index", workflow.read_text("utf8"))


@unittest.skipIf(os.name == "nt", NO_SHEBANG)
class CheckSdkTests(FixtureAppTestCase):
    """check-sdk.py in the fixture app, whose one Windows project targets net11.0, on a machine whose dotnet is support's
    DOTNET_HOST with one SDK installed."""

    def setUp(self):
        super().setUp()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.bin = Path(temp.name)

    def check(self, sdk: str | None, actions: str = "") -> subprocess.CompletedProcess:
        """The check with this SDK as the machine's only one; None: no dotnet on PATH at all."""
        if sdk:
            (self.bin / "dotnet").write_text("#!/bin/sh\n" + DOTNET_HOST, "utf8")
            (self.bin / "dotnet").chmod(0o755)
        env = {**os.environ, "PATH": search_path(self.bin) if sdk else str(self.bin), "FAKE_SDK": sdk or "",
               "GITHUB_ACTIONS": actions}
        return subprocess.run([sys.executable, str(CHECK_SDK)], cwd=self.root, env=env, capture_output=True, encoding="utf8")

    def test_an_sdk_as_new_as_the_target_framework_passes(self):
        for sdk in ("11.0.100", "11.0.402", "12.0.100-preview.1.26070.3"):
            with self.subTest(sdk=sdk):
                done = self.check(sdk)
                self.assertEqual((done.returncode, done.stdout, done.stderr), (0, f"ok: the .NET SDK {sdk} builds net11.0\n", ""))
        (self.root / "windows/global.json").write_text(PIN.replace("11.0.999", "11.0.100"), "utf8")
        done = self.check("11.0.100")
        self.assertEqual((done.returncode, done.stdout),
                         (0, "ok: the .NET SDK 11.0.100 builds net11.0 (windows/global.json picks it)\n"), done.stderr)

    def test_an_older_sdk_fails_with_the_fix_in_the_message(self):
        said = ("The .NET SDK here is 10.0.203 and windows/Fixture.Core/Fixture.Core.csproj targets net11.0 "
                "(the build stops at NETSDK1045): update the runner's SDK to 11.0.")
        done = self.check("10.0.203")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (1, "", f"error: {said}\n"))
        done = self.check("10.0.203", actions="true")  # an annotation on the run's page
        self.assertEqual((done.returncode, done.stdout, done.stderr), (1, f"::error::{said}\n", ""))

    def test_a_pin_that_no_installed_sdk_meets_fails_with_the_pinned_version(self):
        (self.root / "windows/global.json").write_text(PIN, "utf8")
        done = self.check("11.0.100")
        self.assertEqual((done.returncode, done.stdout), (1, ""))
        self.assertEqual(done.stderr, "error: No installed .NET SDK meets windows/global.json (11.0.999, rollForward disable); "
                                      "this machine has 11.0.100: update the runner's SDK to 11.0.999. "
                                      "dotnet says: A compatible .NET SDK was not found.\n")

    def test_no_dotnet_at_all(self):
        done = self.check(None)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertRegex(done.stderr, r"^error: dotnet doesn't start here \(.+\): update the runner's SDK to 11\.0\.\n$")

    def test_the_newest_target_framework_of_the_projects_counts(self):
        windows = self.root / "windows"
        (windows / "Directory.Build.props").write_text(
            "<Project>\n  <PropertyGroup>\n    <!-- <TargetFramework>net14.0</TargetFramework> -->\n"
            "    <TargetFramework Condition=\"'$(TargetFramework)' == ''\">net11.0</TargetFramework>\n"
            "  </PropertyGroup>\n</Project>\n", "utf8")
        app = windows / "Fixture.App"
        (app / "obj").mkdir(parents=True)
        (app / "obj/Generated.props").write_text("<Project><PropertyGroup><TargetFramework>net13.0</TargetFramework></PropertyGroup></Project>\n", "utf8")
        (app / "Fixture.App.csproj").write_text(
            "<Project>\n  <PropertyGroup>\n"
            "    <TargetFrameworks>netstandard2.0; net12.0-windows10.0.26100.0;net48</TargetFrameworks>\n"
            "  </PropertyGroup>\n</Project>\n", "utf8")
        done = self.check("11.0.402")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("The .NET SDK here is 11.0.402 and windows/Fixture.App/Fixture.App.csproj targets net12.0 ", done.stderr)
        self.assertIn(": update the runner's SDK to 12.0.\n", done.stderr)
        self.assertEqual(self.check("12.0.100").returncode, 0)

    def test_the_first_job_asks_on_a_self_hosted_runner_before_anything_builds(self):
        # The first job of each workflow, and in it before the first step that runs dotnet or a tool that builds. A
        # release's package jobs ask again, right after the checkout: each may land on another runner with the labels.
        release = 'python "${{ inputs.laching-path }}/tools/windows/check-sdk.py"'
        for name, job, run, builds, mentions in (
            ("windows-app.yml", "app", 'python "${{ inputs.laching-path }}/tools/windows/check-sdk.py"', "WinUI traps", 2),
            ("windows-core.yml", "test", 'python3 "./$LACHING_PATH/tools/windows/check-sdk.py"', "Check strings and phrase keys", 2),
            ("windows-release.yml", "version", release, "Read the package version (and refuse one released before)", 3),
            ("windows-release.yml", "package", release, "The app's global.json", 3),
        ):
            with self.subTest(workflow=name, job=job):
                text = (REPO / ".github/workflows" / name).read_text("utf8")
                self.assertEqual(re.search(r"^jobs:\n  ([\w-]+):$", text, re.M).group(1), "version" if job == "package" else job)
                self.assertEqual(text.count("check-sdk.py"), mentions, "its comment and a step a job")
                if job == "package":
                    steps = workflow_steps(name, job)
                    self.assertEqual(steps.index(step(steps, run=run)) - 1,
                                     steps.index(step(steps, name="Check out ${{ inputs.laching-path }}/")))
                steps = workflow_steps(name, job)
                check = step(steps, run=run)
                self.assertEqual(check["if"], "${{ !inputs.hosted }}", "setup-dotnet installs the SDK on GitHub's runners")
                self.assertEqual(steps.index(check) + 1, steps.index(step(steps, name=builds)))
                later = [s.get("name", s.get("uses")) for s in steps[steps.index(check):]
                         if s is not check and re.search(r"\bdotnet (?!--version)|store-release|ci-windows-core\.py\" (?!pins)", s.get("run", ""))]
                earlier = [s.get("name", s.get("uses")) for s in steps[:steps.index(check)]
                           if re.search(r"^dotnet |store-release|ci-windows-core\.py\" (?!pins)", s.get("run", ""), re.M)]
                self.assertEqual(earlier, [], "nothing builds before the check")
                self.assertTrue(later or job == "version", "what builds comes after it")


class AppWorkflowTests(unittest.TestCase):
    """The app workflows ask for the SDK, and run dotnet, in the app's windows.dir: the .NET host takes the SDK from the
    global.json nearest its working folder, never the project's, so there the app's windows/global.json picks it (and
    refuses to run without it), and setup-dotnet installs the SDK it pins. Their pwsh steps run in PowerShellTests."""

    def test_setup_dotnet_installs_the_sdk_windows_dir_pins(self):
        for name, job in (("windows-app.yml", "app"), ("windows-core.yml", "test"), ("windows-release.yml", "package")):
            with self.subTest(workflow=name):
                steps = workflow_steps(name, job)
                setup = step(steps, uses="actions/setup-dotnet@v6")
                self.assertLess(steps.index(step(steps, id="app")), steps.index(setup), "its global.json found first")
                self.assertEqual(setup["with.global-json-file"], "${{ steps.app.outputs.global-json }}")
                self.assertEqual(setup["with.dotnet-version"], '"11.0.x"', "beside the newest .NET 11")

    def test_windows_app_asks_and_tests_in_windows_dir(self):
        steps = workflow_steps("windows-app.yml", "app")
        settings, sdk = step(steps, id="app"), step(steps, id="sdk")
        self.assertLess(steps.index(settings), steps.index(sdk), "the settings name the folder the SDK is asked in")
        self.assertEqual(sdk["working-directory"], "${{ steps.app.outputs.windows-dir }}")
        tests = [s for s in steps if s.get("run", "").startswith("dotnet ")]
        self.assertEqual([s["name"] for s in tests], ["Core tests on Windows", "Model tests on Windows"])
        for test in tests:
            with self.subTest(step=test["name"]):
                self.assertEqual(test["working-directory"], "${{ steps.app.outputs.windows-dir }}")

    @unittest.skipIf(os.name == "nt", "the step runs on GitHub's Linux runner")
    def test_windows_core_finds_the_global_json_in_windows_dir(self):
        run = step(workflow_steps("windows-core.yml", "test"), id="app")["run"]
        with fixture_app() as root, tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            # python3 is the tests' own Python, not an older one on PATH.
            (folder / "python3").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', "utf8")
            (folder / "python3").chmod(0o755)
            (folder / "step.sh").write_text(run, "utf8")
            output = folder / "output"
            env = {**os.environ, "PATH": search_path(folder), "COMPANION_ROOT": str(root),
                   "LACHING_PATH": os.path.relpath(REPO, root), "GITHUB_OUTPUT": str(output)}

            def look() -> subprocess.CompletedProcess:
                output.write_text("")
                return subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", str(folder / "step.sh")],
                                      cwd=root, env=env, capture_output=True, text=True)

            done = look()
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertEqual(outputs(output), {}, "no global.json: the newest .NET 11 alone")
            (root / "windows" / "global.json").write_text(PIN, "utf8")
            done = look()
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertEqual(outputs(output), {"global-json": "windows/global.json"})

            config = root / "companion.json"
            text = config.read_text("utf8")
            for edit, said in (('"dir": "win", ', "companion.json's windows.dir is win, which isn't a folder."),
                               ("", "companion.json has no windows.dir, or couldn't be read (see above)."),
                               ('"dir": "windows", "unknown": 1, ',
                                "companion.json has no windows.dir, or couldn't be read (see above).")):
                with self.subTest(edit=edit):
                    config.write_text(text.replace('"dir": "windows", ', edit), "utf8")
                    done = look()
                    self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
                    self.assertIn(f"::error::{said}", done.stdout)


class ScreenshotRetryTests(unittest.TestCase):
    """screenshots.ps1 launches a pass that stopped early once more. Read as text: the run itself needs Windows and
    the app, so these hold the shape of it (one more launch, never a loop; what counts as stopping early; what is kept
    and said), and test_the_scripts_parse reads the script where PowerShell is installed."""

    @classmethod
    def setUpClass(cls):
        cls.text = (WINDOWS / "screenshots.ps1").read_text("utf8")
        # The code alone: no comment line, and nothing of the help above param(.
        cls.code = "\n".join(line for line in cls.text[cls.text.index("\nparam("):].splitlines()
                             if not line.lstrip().startswith("#"))

    def function(self, name: str) -> str:
        """A function of the script, from its name to the closing brace in the first column."""
        start = self.code.index(f"\nfunction {name}(")
        return self.code[start:self.code.index("\n}", start)]

    def test_a_pass_that_stopped_early_is_launched_once_more(self):
        once = self.function("Invoke-Pass")
        self.assertEqual(once.count("Invoke-Run "), 2, "a launch and one more")
        first, second = (match.start() for match in re.finditer("Invoke-Run ", once))
        fine = once.index("if (-not $why) { return $launch }")
        self.assertTrue(first < once.index("$why = Get-EarlyStop $launch") < fine < second, "only after an early stop")
        self.assertIn('$again = Invoke-Run "$into-retry" $languages', once,
                      "into a folder of its own, the same languages")
        self.assertNotRegex(once, r"\b(?:for|foreach|while|do|until|try|catch)\b", "never a loop; a hang isn't caught")
        # Every launch of a run is a pass: nothing else starts the app.
        self.assertEqual(len(re.findall(r"Invoke-Run\b", self.code)), 3, "its definition and Invoke-Pass's two")
        self.assertEqual(len(re.findall(r"Invoke-Pass\b(?!es)", self.code)), 2, "its definition and Invoke-Passes's")
        self.assertIn("Invoke-Pass (Join-Path $work", self.function("Invoke-Passes"))
        self.assertEqual(re.findall(r'Invoke-Passes "(\w+)"', self.code), ["run", "contrast"])

    def test_what_stopping_early_is(self):
        early = self.function("Get-EarlyStop")
        self.assertIn("$code = $launch.Run.ExitCode", early)
        left, nothing = early.index("if ($code -ne 0) {"), early.index("elseif ($drawn.Count -eq 0) {")
        self.assertLess(left, nothing)
        self.assertIn('"The app stopped before the end of the run (exit code $code) after $($drawn.Count) shots$last"',
                      early[left:nothing])
        self.assertIn('"No screenshots were rendered (exit code 0)"', early[nothing:])
        # A launch that hangs is killed and stops the run in Invoke-Run, which Invoke-Pass doesn't catch; so does an
        # app that doesn't start in its package, and the help says why neither is tried again.
        run = self.function("Invoke-Run")
        self.assertEqual(run.count('$run.Kill(); throw "The screenshot run didn\'t finish in $minutes minutes."'), 2)
        self.assertIn('if (-not $run) { throw "The app didn\'t start in its package." }', run)
        help_ = " ".join(self.text[:self.text.index("\nparam(")].split())
        self.assertIn("An app that doesn't start in its package at all (no process 30 seconds after "
                      "Invoke-CommandInDesktopPackage) stops the run at once, as a launch that hangs until its time is "
                      "up does: neither was among the runs that failed by chance", help_)

    def test_the_second_attempt_alone_counts_and_the_first_is_kept(self):
        once = self.function("Invoke-Pass")
        # The pass is its second launch (its folder, its exit code), carrying why the first stopped and where it drew.
        self.assertTrue(once.endswith("\n    $again.First = @{ Why = $why; Work = $into }\n    $again"), once)
        # What it prints begins with why the first stopped and with the note, never with "Problems" or an indent: a
        # capture's log is read for its last line starting with "Problems" and the indented lines after it.
        self.assertIn('\n    Write-Host "$why$(', once, "said when it happens, and not as the function's output")
        rest = self.code[self.code.index('\n$runs = @(Invoke-Passes "run")'):]
        self.assertIn('Remove-Item (Join-Path $to "first-attempt.txt") -ErrorAction SilentlyContinue', rest,
                      "a run that needed none leaves no old one")
        kept = rest.index('$kept = Join-Path $to "first-attempt.txt"')
        self.assertLess(rest.index("if ($target.First.Count -gt 0) {"), kept)
        self.assertIn("}) | Set-Content $kept -Encoding UTF8", rest[kept:])
        note = next(line.strip() for line in rest.splitlines() if line.strip().startswith("$note = "))
        self.assertTrue(note.startswith('$note = "The screenshot run needed a second attempt'), note)
        self.assertIn('\n        "$note What the first attempt left: $kept"\n', rest)
        summary = ('\n        if ($env:GITHUB_STEP_SUMMARY) {\n'
                   '            try { [IO.File]::AppendAllText($env:GITHUB_STEP_SUMMARY, "$note`n`n") } '
                   'catch { Write-Warning "The run\'s summary page didn\'t take the note: $_" }\n        }\n')
        self.assertIn(summary, rest, "on the run's summary page; a page that can't be written loses the note, not the shots")
        # The run fails as before, on what the launches in $runs left: the second ones.
        self.assertIn("if ($stopped = @($runs | Where-Object { $_.Run.ExitCode -ne 0 })) {", rest)
        self.assertIn('", in the second attempt too"', rest)

    def test_the_workflow_runs_each_language_once(self):
        """The retry is the script's: the step still calls it once per language and fails on the languages that
        failed."""
        run = step(workflow_steps("windows-app.yml", "app"), name="Open every window")["run"]
        self.assertEqual(run.count("screenshots.ps1"), 1)
        self.assertIn("if ($LASTEXITCODE -ne 0) { $failed += $language }", run)
        self.assertIn('if ($failed) { throw "The screenshot run failed in: $($failed -join \', \')" }', run)


@unittest.skipUnless(POWERSHELL, "needs PowerShell")
class PowerShellTests(unittest.TestCase):
    def powershell(self, *args: str, env: dict[str, str] | None = None,
                   cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", *args], capture_output=True, text=True,
                              env=env, cwd=cwd)

    def fake_dotnet(self, folder: Path, answer: Path | None = None) -> tuple[dict[str, str], Path]:
        """An environment with a dotnet first on PATH that logs each call's command and working folder to the log it
        returns, and answers a property query (msbuild) with `answer`'s text. In a folder with a global.json it fails
        as the host does when the SDK that pins isn't installed: the host looks for it from the working folder up."""
        log = folder / "dotnet.log"
        fake = folder / "fake"
        fake.mkdir()
        if os.name == "nt":
            (fake / "dotnet.cmd").write_text(
                f'@echo off\r\n>> "{log}" echo %1 %CD%\r\n'
                "if exist global.json (\r\n  echo A compatible .NET SDK was not found. 1>&2\r\n  exit /b 155\r\n)\r\n"
                + (f'if "%1"=="msbuild" type "{answer}"\r\n' if answer else ""), "utf8")
        else:
            (fake / "dotnet").write_text(
                f'#!/bin/sh\necho "$1 $(pwd -P)" >> "{log}"\n'
                'if [ -f global.json ]; then echo "A compatible .NET SDK was not found." >&2; exit 155; fi\n'
                + (f'[ "$1" != msbuild ] || cat "{answer}"\n' if answer else ""), "utf8")
            (fake / "dotnet").chmod(0o755)
        return {**os.environ, "PATH": str(fake) + os.pathsep + os.environ["PATH"]}, log

    @staticmethod
    def dotnet_calls(log: Path) -> list[tuple[str, Path]]:
        """The fake dotnet's calls since the last look, (command, working folder) each."""
        lines = log.read_text().splitlines() if log.exists() else []
        log.unlink(missing_ok=True)
        return [(command, Path(folder)) for command, folder in (line.split(" ", 1) for line in lines)]

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
            env, log = self.fake_dotnet(tools)

            def calls() -> list[dict]:
                found = [json.loads(path.read_text("utf-8-sig")) for path in tools.glob("call-*.json")]
                for path in tools.glob("call-*.json"):
                    path.unlink()
                return found

            def queried_in_windows_dir() -> None:
                # Its package type query runs in windows.dir, whose global.json picks the SDK, though run from the root.
                ran = self.dotnet_calls(log)
                self.assertEqual([command for command, _ in ran], ["msbuild"])
                self.assertTrue(ran[0][1].samefile(root / "windows"), ran)

            done = self.powershell("-File", str(tools / "check-layout.ps1"), env=env, cwd=root)
            self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
            queried_in_windows_dir()
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

            done = self.powershell("-File", str(tools / "check-layout.ps1"), "-Only", "panel,home", "-Jobs", "1", "en", "it",
                                   env=env, cwd=root)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("Every window fits in: en it", done.stdout)
            queried_in_windows_dir()
            runs = [call for call in calls() if not call["BuildOnly"]]
            self.assertEqual([(call["Language"], call["Only"]) for call in runs], [("en,it", "panel,home")], "one run for both")
            self.assertEqual(Path(runs[0]["Out"]), root / "windows" / "build" / "layout-check")

            # Without windows.dir, it says so.
            config = root / "companion.json"
            config.write_text(config.read_text("utf8").replace('"dir": "windows", ', ""), "utf8")
            done = self.powershell("-File", str(tools / "check-layout.ps1"), env=env, cwd=root)
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("companion.json has no windows.dir.", done.stdout + done.stderr)

    @staticmethod
    def built_app(folder: Path) -> tuple[Path, Path]:
        """Where a Debug build of Fixture.App puts its exe (not made) under `folder`, and the file with the property
        query's answer that names it, for the fake dotnet."""
        app = folder / "bin"
        app.mkdir()
        props = folder / "props.json"
        props.write_text(json.dumps({"Properties": {"TargetDir": str(app) + os.sep, "AssemblyName": "Fixture.App"}}), "utf8")
        return app / "Fixture.App.exe", props

    def test_screenshots_build_once_or_not_at_all(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            # Laching's fixture app's layout: dotnet runs in the folder above the project's (dotnet/), as in an app's
            # windows.dir.
            project = folder / "dotnet" / "Fixture.App" / "Fixture.App.csproj"
            project.parent.mkdir(parents=True)
            project.write_text("<Project />", "utf8")
            exe, props = self.built_app(folder)
            env, log = self.fake_dotnet(folder, answer=props)

            def shots(*args: str) -> tuple[subprocess.CompletedProcess, list[str]]:
                done = self.powershell("-File", str(WINDOWS / "screenshots.ps1"), "-Project", str(project), *args, env=env)
                ran = self.dotnet_calls(log)
                for command, cwd in ran:
                    self.assertTrue(cwd.samefile(folder / "dotnet"), f"dotnet {command} ran in {cwd}")
                return done, [command for command, _ in ran]

            done, _ = shots("-NoBuild")
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("build it first", done.stdout + done.stderr)
            exe.write_bytes(b"")
            done, ran = shots("-BuildOnly")
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertEqual(ran, ["build", "msbuild"], "one property query for the folder and the name")
            done, ran = shots("-BuildOnly", "-NoBuild")
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertEqual(ran, ["msbuild"])

    def test_screenshots_build_in_windows_dir(self):
        # Run from the app's root, dotnet runs in windows.dir: the global.json there picks the SDK, and one it pins that
        # isn't installed fails the build.
        with fixture_app() as root, tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            project = root / "windows" / "Fixture.App" / "Fixture.App.csproj"
            project.parent.mkdir(parents=True)
            project.write_text("<Project />", "utf8")
            exe, props = self.built_app(folder)
            exe.write_bytes(b"")
            env, log = self.fake_dotnet(folder, answer=props)

            def build() -> subprocess.CompletedProcess:
                return self.powershell("-File", str(WINDOWS / "screenshots.ps1"), "-BuildOnly", env=env, cwd=root)

            done = build()
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            ran = self.dotnet_calls(log)
            self.assertEqual([command for command, _ in ran], ["build", "msbuild"])
            for command, cwd in ran:
                self.assertTrue(cwd.samefile(root / "windows"), f"dotnet {command} ran in {cwd}")

            pin = root / "windows" / "global.json"
            pin.write_text('{"sdk": {"version": "11.0.999", "rollForward": "disable"}}', "utf8")
            done = build()
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("A compatible .NET SDK was not found", done.stdout + done.stderr)
            self.assertEqual([command for command, _ in self.dotnet_calls(log)], ["build"], "it stops at the build")
            # Without the build, the property query is what fails, and says so.
            done = self.powershell("-File", str(WINDOWS / "screenshots.ps1"), "-BuildOnly", "-NoBuild", env=env,
                                   cwd=root)
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("Couldn't read the app's build properties (dotnet msbuild exit 155).",
                          done.stdout + done.stderr)
            self.assertEqual([command for command, _ in self.dotnet_calls(log)], ["msbuild"])

            # Without windows.dir, it says so.
            pin.unlink()
            config = root / "companion.json"
            config.write_text(config.read_text("utf8").replace('"dir": "windows", ', ""), "utf8")
            done = build()
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("companion.json has no windows.dir.", done.stdout + done.stderr)
            self.assertEqual(self.dotnet_calls(log), [])

    def test_the_scripts_parse(self):
        for script in sorted(WINDOWS.glob("*.ps1")):
            with self.subTest(script=script.name):
                done = self.powershell("-Command", (
                    "$errors = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
                    f"'{script}', [ref]$null, [ref]$errors); $errors | ForEach-Object {{ $_.ToString() }}"))
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(done.stdout.strip(), "")

    def test_dotnet_sdk_uses_the_image_in_the_newest_band(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            output = folder / "output"

            def ask(image: str | None, newest: str) -> str:
                # A fake dotnet first on PATH: it prints `image`, or fails as one that can't run here does.
                (folder / "dotnet").write_text(f"#!/bin/sh\necho {image}\n" if image else "#!/bin/sh\nexit 1\n")
                (folder / "dotnet").chmod(0o755)
                (folder / "dotnet.cmd").write_text(f"@echo {image}\n" if image else "@exit /b 1\n")
                output.write_text("")
                env = {**os.environ, "PATH": f"{folder}{os.pathsep}{os.environ['PATH']}", "GITHUB_OUTPUT": str(output)}
                done = self.powershell("-File", str(WINDOWS / "dotnet-sdk.ps1"), "-Newest", newest, env=env)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                return output.read_text().strip()

            self.assertEqual(ask("11.0.105", "11.0.111"), "use=true", "a patch behind in the newest band")
            self.assertEqual(ask("11.0.111", "11.0.111"), "use=true")
            self.assertEqual(ask("11.0.105", "11.0.201"), "use=false", "a band behind")
            self.assertEqual(ask("10.0.306", "11.0.111"), "use=false", "not .NET 11")
            self.assertEqual(ask(None, "11.0.111"), "use=false", "no SDK that runs here")

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

    def release(self, root: Path, *args: str, env: dict[str, str] | None = None,
                cwd: Path | None = None) -> subprocess.CompletedProcess:
        return self.powershell("-File", str(WINDOWS / "store-release.ps1"), *args,
                               env={**os.environ, "COMPANION_ROOT": str(root), **(env or {})}, cwd=cwd)

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
            config = root / "companion.json"
            config.write_text(config.read_text("utf8").replace('"dir": "windows", ', ""), "utf8")
            done = self.release(root, "-VersionOnly")
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("companion.json has no windows.dir.", done.stdout + done.stderr)

    def test_store_release_publishes_in_windows_dir(self):
        # Run from the app's root, dotnet publish runs in windows.dir: the global.json there picks the SDK, and one it
        # pins that isn't installed fails the publish.
        with fixture_app() as root, tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            project = root / "windows" / "Fixture.App" / "Fixture.App.csproj"
            project.parent.mkdir(parents=True)
            project.write_text("<Project />", "utf8")
            self.release_app(root)
            env, log = self.fake_dotnet(folder)
            if os.name != "nt":
                env["ProgramFiles(x86)"] = str(folder)  # where it looks for the Visual Studio installer's vswhere

            def publish() -> subprocess.CompletedProcess:
                return self.release(root, "-Platforms", "x64", "-NoSmoke", "-NoBundle", "-Out", str(folder / "out"),
                                    env=env, cwd=root)

            done = publish()
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("No x64 .msix of version 1.0.3.0", done.stdout + done.stderr, "the fake publish makes none")
            ran = self.dotnet_calls(log)
            self.assertEqual([command for command, _ in ran], ["publish"])
            self.assertTrue(ran[0][1].samefile(root / "windows"), ran)

            (root / "windows" / "global.json").write_text('{"sdk": {"version": "11.0.999", "rollForward": "disable"}}',
                                                          "utf8")
            done = publish()
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("The x64 publish failed (exit 155)", done.stdout + done.stderr)
            self.assertIn("A compatible .NET SDK was not found", done.stdout + done.stderr)

    @staticmethod
    def run_step(run: str, folder: Path, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
        """A workflow's pwsh step as the runner runs it: stopping at the first error, exiting with the last native
        command's code."""
        script = folder / "step.ps1"
        script.write_text("$ErrorActionPreference = 'stop'\n" + run
                          + "\nif ((Test-Path -LiteralPath variable:\\LASTEXITCODE)) { exit $LASTEXITCODE }\n", "utf8")
        return subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(script)], capture_output=True,
                              text=True, env=env, cwd=cwd)

    @unittest.skipUnless(PWSH, "the step is shell: pwsh")
    def test_windows_app_tests_in_windows_dir(self):
        steps = workflow_steps("windows-app.yml", "app")
        settings = expand(step(steps, id="app")["run"], {"inputs.screenshot-run": "release"})
        model = step(steps, name="Model tests on Windows")
        with fixture_app() as root, tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            project = root / "windows" / "Fixture.App.Tests" / "Fixture.App.Tests.csproj"
            project.parent.mkdir(parents=True)
            project.write_text("<Project />", "utf8")
            env, log = self.fake_dotnet(folder)
            output = folder / "output"
            env["GITHUB_OUTPUT"] = str(output)

            def settle() -> tuple[subprocess.CompletedProcess, dict[str, str]]:
                output.write_text("")
                return self.run_step(settings, folder, root, env), outputs(output)

            def test(found: dict[str, str]) -> subprocess.CompletedProcess:
                values = {f"steps.app.outputs.{key}": value for key, value in found.items()}
                cwd = root / expand(model["working-directory"], values)
                self.assertTrue((cwd / found["app-tests"]).is_file(), "the project is relative to windows.dir")
                return self.run_step(expand(model["run"], values), folder, cwd, env)

            done, found = settle()
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertEqual(found, {"windows-dir": "windows", "global-json": "", "assets": "True", "core-tests": "",
                                     "app-tests": "Fixture.App.Tests/Fixture.App.Tests.csproj"})
            done = test(found)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            ran = self.dotnet_calls(log)
            self.assertEqual([command for command, _ in ran], ["test"])
            self.assertTrue(ran[0][1].samefile(root / "windows"), ran)

            # A pin the runner's SDK doesn't meet: setup-dotnet is handed it, and without it the tests fail.
            (root / "windows" / "global.json").write_text(PIN, "utf8")
            done, found = settle()
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertEqual(found["global-json"], "windows/global.json")
            done = test(found)
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("A compatible .NET SDK was not found", done.stdout + done.stderr)

            config = root / "companion.json"
            text = config.read_text("utf8")
            for edit, said in (('"dir": "win", ', "companion.json's windows.dir is win, which isn't a folder."),
                               ("", "companion.json has no windows.dir.")):
                with self.subTest(said=said):
                    config.write_text(text.replace('"dir": "windows", ', edit), "utf8")
                    done, _ = settle()
                    self.assertNotEqual(done.returncode, 0)
                    self.assertIn(said, done.stdout + done.stderr)

    @unittest.skipUnless(PWSH, "the step is shell: pwsh")
    def test_windows_release_finds_the_global_json_in_windows_dir(self):
        run = step(workflow_steps("windows-release.yml", "package"), id="app")["run"]
        with fixture_app() as root, tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            output = folder / "output"
            env = {**os.environ, "GITHUB_OUTPUT": str(output)}

            def look() -> subprocess.CompletedProcess:
                output.write_text("")
                return self.run_step(run, folder, root, env)

            done = look()
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertEqual(outputs(output), {"global-json": ""}, "no global.json: the newest .NET 11 alone")
            (root / "windows" / "global.json").write_text(PIN, "utf8")
            done = look()
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertEqual(outputs(output), {"global-json": "windows/global.json"})

            config = root / "companion.json"
            config.write_text(config.read_text("utf8").replace('"dir": "windows", ', ""), "utf8")
            done = look()
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("companion.json has no windows.dir.", done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
