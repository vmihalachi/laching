<#
Renders a companion app's windows to PNGs, in light and dark (or in the contrast theme alone, while Windows is in one;
-Contrast adds it): the app's screenshot run (Laching.Windows.Companion's ScreenshotCapture) over sample state, never the user's
settings. Windows only. Reads companion.json (found from the current folder up, then from this script's, or
$env:COMPANION_ROOT) for the app's project (windows.dir + windows.app) and where the shots go (windows.screenshots).

Usage: laching/tools/windows/screenshots.ps1 [-Only name,name] [-Hidden|-Front] [-Out folder] [-Language tag]
                                              [-Skin id] [-Themes light,dark] [-Contrast] [-Platform x64|ARM64]
                                              [-Configuration Debug|Release] [-NoBuild|-BuildOnly]
                                              [-Project path\to\App.csproj]
  -Only           just the shots whose file names start with one of the names (home, settings, onboarding, …), for
                  checking a change: the app renders those, and only those are copied (an app may render more). Pair it
                  with a scratch -Out so the committed screenshots stay as they are. Implies -Hidden.
  -Hidden         the windows open cloaked (drawn and captured, never on screen) and never take the focus, so you can
                  keep working, and each is shot once it has drawn rather than after a fixed wait; the shots show them
                  inactive (grey caption buttons). For checks, never the committed shots. An app on a Laching.Windows.Companion
                  older than 0.10.5 opens its windows in front, as before.
  -Front          the windows come forward and are shot active, as in use (the default without -Only); leave the PC alone.
  -Language       the language to shoot in ("it"); the app's own choice otherwise.
  -Skin           the skin the run wears (a skin id: auguste), owning in the sample store whatever unlocks it; the app's
                  default skin otherwise. Pair it with an -Out of its own: the file names don't carry the skin.
  -Themes         the themes to shoot, in that order (light, dark; both by default): a run asked for one never draws
                  the other. Ignored while Windows is in a contrast theme, which the run shoots alone.
  -Contrast       also the contrast theme (<name>-contrast.png), which an app can't turn on for itself: after the light and
                  dark run, Windows' contrast theme is turned on (the one last used; Settings > Accessibility > Contrast
                  themes picks it), the app runs again, and the theme is turned off, whatever happens. That repaints the
                  whole screen for a minute and any screenshot run on this PC meanwhile would shoot it too, so it first
                  waits for the other runs it can see (screenshots.ps1, check-layout.ps1) to end, and holds a lock that
                  another -Contrast run waits for. While Windows is already in a contrast theme there's nothing to turn
                  on: the one run shoots it.
  -Configuration  Debug (the default) builds and runs the Debug app. A packaged one (WindowsPackageType MSIX, for
                  package identity) is registered from its build output as a development package, as Visual Studio's F5
                  does (Developer Mode; other development registrations of it are removed first, a Store install never),
                  and started in it; since that start passes no environment, the run's settings arrive as arguments
                  instead (LACHING_SCREENSHOT_DIR=<folder> and so on), which ScreenshotCapture reads too. Release publishes the Native AOT app
                  unpackaged with screenshot mode compiled in (-p:ScreenshotMode=true), so the shots come from the build
                  that ships: Native AOT breaks some things a JIT build hides. It builds from scratch in
                  <project>\obj\screenshots\<platform>, emptied first, never in the obj\ and bin\ a packaged build uses.
  -NoBuild        run the last build (check-layout.ps1 builds once, then runs the languages side by side).
  -BuildOnly      just build.
  -Project        another WinUI project than companion.json's (Laching's fixture app).
Fails, after copying the shots, when the run reports problems (problems.txt, left next to the shots): a window that
didn't open or couldn't be drawn, one captured before it drew (one flat colour, taken again), a shot that wasn't
written, text that doesn't fit (LayoutCheck); or when the app didn't finish the run (it crashed or was closed partway,
so some windows have no shot). check-layout.ps1 runs it per language.
#>
param(
    [string[]] $Only = @(),
    [string] $Out = "",
    [string] $Language = "",
    [string] $Skin = "",
    [string] $Themes = "",
    [ValidateSet("x64", "ARM64")][string] $Platform = $(if ([Runtime.InteropServices.RuntimeInformation]::OSArchitecture -eq "Arm64") { "ARM64" } else { "x64" }),
    [ValidateSet("Debug", "Release")][string] $Configuration = "Debug",
    [string] $Project = "",
    [switch] $Hidden,
    [switch] $Front,
    [switch] $Contrast,
    [switch] $NoBuild,
    [switch] $BuildOnly
)
$ErrorActionPreference = "Stop"
# -Only home,settings arrives as two strings.
$Only = @($Only | Where-Object { $_ }) -join ","

# companion.json as a dictionary: Windows PowerShell's ConvertFrom-Json refuses keys that differ only in case (a
# character's glyph table has them), PowerShell 7's keeps only one of them without -AsHashtable.
function Read-Companion([string] $file) {
    $text = Get-Content $file -Raw -Encoding UTF8
    if ($PSVersionTable.PSVersion.Major -ge 6) { return $text | ConvertFrom-Json -AsHashtable }
    Add-Type -AssemblyName System.Web.Extensions
    $serializer = New-Object System.Web.Script.Serialization.JavaScriptSerializer
    $serializer.MaxJsonLength = [int]::MaxValue
    $serializer.DeserializeObject($text)
}

function Find-Root {
    if ($env:COMPANION_ROOT) { return (Resolve-Path $env:COMPANION_ROOT).Path }
    # The current folder up, then this script's (an app's submodule copy, run from anywhere).
    foreach ($start in (Get-Location).Path, $PSScriptRoot) {
        $dir = $start
        while ($dir) {
            if (Test-Path (Join-Path $dir "companion.json")) { return $dir }
            $parent = Split-Path $dir
            if ($parent -eq $dir) { break }
            $dir = $parent
        }
    }
    throw "No companion.json here, above, or above this script; set COMPANION_ROOT."
}

if (-not $Project) {
    $root = Find-Root
    $config = Read-Companion (Join-Path $root "companion.json")
    if (-not $config.windows -or -not $config.windows.app) { throw "companion.json has no windows.app." }
    $Project = Join-Path $root "$($config.windows.dir)/$($config.windows.app)/$($config.windows.app).csproj"
    if (-not $Out) {
        $folder = if ($config.windows.screenshots) { $config.windows.screenshots } else { "screenshots/windows" }
        $Out = Join-Path $root $folder
    }
}
$Project = (Resolve-Path $Project).Path
$name = [IO.Path]::GetFileNameWithoutExtension($Project)
if (-not $Out) { $Out = Join-Path ([IO.Path]::GetTempPath()) "$name-shots" }
$Out = [IO.Path]::GetFullPath($Out)
$hide = -not $Front -and ($Hidden -or $Only)

if ($Configuration -eq "Release") {
    # Native AOT links with the Visual Studio tools, which it finds through vswhere: put it on the path when the
    # installer's copy isn't there (a plain shell, a hosted runner).
    $installer = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer"
    if (-not (Get-Command vswhere.exe -ErrorAction SilentlyContinue) -and (Test-Path (Join-Path $installer "vswhere.exe"))) {
        $env:PATH = "$installer;$env:PATH"
    }
    # Everything this build writes (restore, intermediate, output, publish) goes to a folder of its own per platform under
    # the project's obj\, emptied first. In the project's own obj\ and bin\, after a packaged publish
    # (GenerateAppxPackageOnBuild), the incremental build kept what it had made for the package and the app died at
    # start (a XAML parse failure, 0xC000027B); a publish folder kept between runs mixed in the other platform's files.
    # Right after a run, Windows can hold one of the app's DLLs for a few seconds (the first time an x64 build runs from
    # that folder on ARM64; nothing has it loaded), so the removal tries again for up to a minute.
    $artifacts = Join-Path (Split-Path $Project) "obj\screenshots\$Platform"
    $publish = Join-Path $artifacts "app"
    if (-not $NoBuild) {
        for ($try = 1; Test-Path $artifacts; $try++) {
            try { Remove-Item -LiteralPath $artifacts -Recurse -Force -ErrorAction Stop }
            catch { if ($try -ge 60) { throw }; Start-Sleep -Seconds 1 }
        }
        # The output only shows when it failed: the compiler's errors, not just that it did.
        $log = dotnet publish $Project -c Release -p:Platform=$Platform -p:WindowsPackageType=None -p:ScreenshotMode=true --artifacts-path $artifacts -o $publish -v quiet -nologo
        if ($LASTEXITCODE -ne 0) { $log | Write-Host; throw "The app didn't publish." }
    }
    $folder = $publish
    $assembly = (dotnet msbuild $Project -getProperty:AssemblyName).Trim()
}
else {
    if (-not $NoBuild) {
        $log = dotnet build $Project -p:Platform=$Platform -c Debug -v quiet -nologo
        if ($LASTEXITCODE -ne 0) { $log | Write-Host; throw "The app didn't build." }
    }
    # One evaluation for all three (each costs about a second).
    $properties = (dotnet msbuild $Project -p:Platform=$Platform -p:Configuration=Debug -getProperty:TargetDir -getProperty:AssemblyName -getProperty:WindowsPackageType | Out-String | ConvertFrom-Json).Properties
    $folder = $properties.TargetDir
    $assembly = $properties.AssemblyName
    # The build output's own manifest, not its AppX folder's: only Visual Studio's deploy step refreshes that one.
    $manifest = Join-Path $folder "AppxManifest.xml"
    $packaged = $properties.WindowsPackageType -and $properties.WindowsPackageType -ne "None" -and (Test-Path $manifest)
}
$exe = Join-Path $folder "$assembly.exe"
if (-not (Test-Path $exe)) { throw "No $exe$(if ($NoBuild) { '; build it first (without -NoBuild)' } else { ' after the build' })." }
if ($BuildOnly) { exit 0 }

# A folder per run, so runs in several languages at once (check-layout.ps1) don't share one; removed once copied. A
# packaged app's new files under AppData go to its own private copy, so its run writes under the project's obj\ instead.
$work = if ($packaged) { Join-Path (Split-Path $Project) "obj\screenshots-$PID.run" } else { Join-Path ([IO.Path]::GetTempPath()) "$name-screenshots-$PID.run" }
$settings = [ordered]@{
    LACHING_SCREENSHOT_MODE     = if ($Only) { $Only } else { "all" }
    LACHING_SCREENSHOT_DIR      = $work
    LACHING_SCREENSHOT_LANGUAGE = $Language
    LACHING_SCREENSHOT_HIDDEN   = if ($hide) { "1" } else { "0" }
    LACHING_SCREENSHOT_SKIN     = $Skin
    LACHING_SCREENSHOT_THEMES   = $Themes
}
if ($packaged) {
    # A copy already running from this build would take the launch (an app is usually single-instance), and the
    # registration below would close it.
    if (Get-Process -Name $assembly -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe }) {
        throw "$assembly is running from $folder; quit it first."
    }
    $xml = [xml](Get-Content $manifest -Raw)
    $identity = $xml.Package.Identity.Name
    $application = @($xml.Package.Applications.Application)[0].Id
    # A development registration from another folder (the other platform's build) blocks this one. Never a Store install.
    Get-AppxPackage -Name $identity | Where-Object { $_.IsDevelopmentMode -and $_.InstallLocation -ne $folder.TrimEnd("\") } |
        Remove-AppxPackage -PreserveApplicationData
    Add-AppxPackage -Register $manifest -ForceApplicationShutdown
    $package = Get-AppxPackage -Name $identity | Where-Object IsDevelopmentMode | Select-Object -First 1
}

# One run of the app into $work: its process, once it has exited.
function Invoke-Run {
    if ($packaged) {
        # NAME="value" stays one argument, quotes removed, as Windows splits a command line.
        $arguments = ($settings.GetEnumerator() | ForEach-Object { "$($_.Key)=`"$($_.Value)`"" }) -join " "
        $null = Invoke-CommandInDesktopPackage -PackageFamilyName $package.PackageFamilyName -AppId $application -Command $exe -Args $arguments
        $deadline = (Get-Date).AddSeconds(30)
        while (-not ($run = Get-Process -Name $assembly -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe } | Select-Object -First 1) -and
            (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 250 }
        if (-not $run) { throw "The app didn't start in its package." }
        $null = $run.Handle # held, so its exit code can be read once it has exited
        if (-not $run.WaitForExit(600000)) { $run.Kill(); throw "The screenshot run didn't finish in 10 minutes." }
    }
    else {
        foreach ($setting in $settings.GetEnumerator()) { [Environment]::SetEnvironmentVariable($setting.Key, $setting.Value) }
        try {
            $run = Start-Process $exe -PassThru
            if (-not $run.WaitForExit(600000)) { $run.Kill(); throw "The screenshot run didn't finish in 10 minutes." }
        }
        finally {
            $settings.Keys | ForEach-Object { [Environment]::SetEnvironmentVariable($_, $null) }
        }
    }
    $run
}

# Windows' contrast theme, on and off as Settings does it (SPI_SETHIGHCONTRAST, the scheme last used), and the window
# color, which changes once the theme has reached the screen (a few seconds after the call returns).
function Use-ContrastApi {
    if ('LachingContrastTheme' -as [type]) { return }
    Add-Type -TypeDefinition @"
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class LachingContrastTheme {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct HighContrast { public int Size; public int Flags; public IntPtr Scheme; }
    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool SystemParametersInfo(uint action, uint param, ref HighContrast value, uint winIni);
    [DllImport("user32.dll")]
    public static extern uint GetSysColor(int index);
    private static HighContrast Read() {
        var value = new HighContrast { Size = Marshal.SizeOf(typeof(HighContrast)) };
        if (!SystemParametersInfo(0x42, (uint)value.Size, ref value, 0)) throw new Win32Exception();
        return value;
    }
    public static bool IsOn { get { return (Read().Flags & 1) != 0; } }
    public static void Set(bool on) {
        var value = Read();
        value.Flags = on ? value.Flags | 1 : value.Flags & ~1;
        // SPIF_UPDATEINIFILE | SPIF_SENDCHANGE, as Settings does.
        if (!SystemParametersInfo(0x43, (uint)value.Size, ref value, 3)) throw new Win32Exception();
    }
}
"@
}

# Turns the contrast theme on or off, then waits for it to reach the screen (the window color changes, then the shell and
# the apps repaint).
function Set-ContrastTheme([bool] $on) {
    $color = [LachingContrastTheme]::GetSysColor(5)
    [LachingContrastTheme]::Set($on)
    $deadline = (Get-Date).AddSeconds(20)
    while ([LachingContrastTheme]::GetSysColor(5) -eq $color -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 250 }
    Start-Sleep -Seconds 3
}

# The other screenshot runs on this PC that a contrast theme would reach: PowerShell running screenshots.ps1 or
# check-layout.ps1 (by -File, or in an -EncodedCommand, as vm.sh starts them), apart from this one and its parents.
function Get-OtherRuns {
    $processes = @(Get-CimInstance Win32_Process)
    $mine = @()
    for ($id = $PID; $id -and $mine -notcontains $id; $id = ($processes | Where-Object ProcessId -eq $id).ParentProcessId) { $mine += $id }
    $processes | Where-Object { ($_.Name -in @("powershell.exe", "pwsh.exe")) -and ($mine -notcontains $_.ProcessId) } | Where-Object {
        $line = "$($_.CommandLine)"
        if ($line -match '-(?:e|ec|enc|encodedcommand)\s+(\S+)') {
            try { $line += [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($Matches[1])) } catch { }
        }
        $line -match '(screenshots|check-layout)\.ps1'
    }
}

$runs = @(@{ Work = $work; Run = (Invoke-Run) })
$noContrast = $null # why a -Contrast run has no contrast shots
if ($Contrast) {
    Use-ContrastApi
    if ([LachingContrastTheme]::IsOn) {
        "Windows is in a contrast theme: the run shot it, not light and dark."
    }
    else {
        # One contrast pass at a time on this PC, and none while another screenshot run it can see is going.
        $lock = New-Object Threading.Mutex($false, "Local\LachingScreenshotContrast")
        try { $held = $lock.WaitOne([TimeSpan]::FromMinutes(20)) } catch [Threading.AbandonedMutexException] { $held = $true }
        if (-not $held) { $noContrast = "Another -Contrast run kept the contrast theme for 20 minutes: no contrast shots." }
        else {
            try {
                $deadline = (Get-Date).AddMinutes(20)
                while (-not $noContrast -and ($others = @(Get-OtherRuns)).Count -gt 0) {
                    if ((Get-Date) -gt $deadline) { $noContrast = "Other screenshot runs kept going for 20 minutes (process $($others.ProcessId -join ', ')): no contrast shots." }
                    else {
                        "Waiting for another screenshot run to end (process $($others.ProcessId -join ', ')) before the contrast theme."
                        Start-Sleep -Seconds 10
                    }
                }
                if (-not $noContrast) {
                    $contrastWork = "$work-contrast"
                    $settings.LACHING_SCREENSHOT_DIR = $contrastWork
                    Set-ContrastTheme $true
                    try { $runs += @{ Work = $contrastWork; Run = (Invoke-Run) } }
                    finally { Set-ContrastTheme $false }
                }
            }
            finally { $lock.ReleaseMutex(); $lock.Dispose() }
        }
    }
}

# An app may render more than it was asked for (all of a demo's shots in one go): keep only those.
$shots = @($runs | ForEach-Object { Get-ChildItem $_.Work -Filter *.png -ErrorAction SilentlyContinue } | Where-Object {
        $shot = $_.Name
        -not $Only -or @($Only -split "," | Where-Object { $shot.StartsWith($_.Trim(), [StringComparison]::Ordinal) }).Count -gt 0
    })
if ($shots.Count -eq 0) {
    $runs | ForEach-Object { Remove-Item -LiteralPath $_.Work -Recurse -Force -ErrorAction SilentlyContinue }
    throw "No screenshots were rendered (exit code $(($runs | ForEach-Object { $_.Run.ExitCode }) -join ', '))."
}
New-Item -ItemType Directory -Force $Out | Out-Null
Remove-Item (Join-Path $Out "problems.txt") -ErrorAction SilentlyContinue
foreach ($shot in $shots) {
    Copy-Item $shot.FullName $Out -Force
}
"$($shots.Count) screenshots in $Out"
$found = @(foreach ($run in $runs) {
        $problems = Join-Path $run.Work "problems.txt"
        if (Test-Path $problems) { Get-Content $problems -Encoding UTF8 }
        Remove-Item -LiteralPath $run.Work -Recurse -Force -ErrorAction SilentlyContinue
    })
if ($found.Count -gt 0) {
    $found | Set-Content (Join-Path $Out "problems.txt") -Encoding UTF8
    [Console]::Error.WriteLine("Problems$(if ($Language) { " ($Language)" }):")
    $found | ForEach-Object { [Console]::Error.WriteLine("  $_") }
    exit 1
}
if ($noContrast) {
    [Console]::Error.WriteLine($noContrast)
    exit 1
}
if ($stopped = @($runs | Where-Object { $_.Run.ExitCode -ne 0 })) {
    [Console]::Error.WriteLine("The app stopped before the end of the run (exit code $(($stopped | ForEach-Object { $_.Run.ExitCode }) -join ', ')), so some windows have no shot.")
    exit 1
}
