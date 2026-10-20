<#
Renders the WinUI app's windows in each language and fails when one has a problem: text that doesn't fit its box
(LayoutCheck), a window that didn't open, couldn't be drawn or was captured before it drew, a shot that wasn't written
(the checks screenshots.ps1 fails on). Longer translations are where text runs out of room, so run it after changing text or a window's layout.
Windows only; the Mac's is check-layout.sh.

Usage: laching/tools/windows/check-layout.ps1 [-Only name,name] [-Themes light,dark] [-Jobs n] [-Platform x64|ARM64]
                                                [-Project path] [-Out folder] [language ...]
       (default: every language in companion.json's languages.all)
Shots go to <windows.dir>\build\layout-check\<language> (with -Project, <name>-layout-check\<language> in the temporary
folder; with -Out, <folder>\<language>), with the language's problems.txt when it has problems, to look at what
failed. It builds once, then shares the languages among -Jobs runs side by side ($env:LAYOUT_JOBS, default 4; a packaged
app gets one, since it runs one copy), each launching the app once for all of its languages (screenshots.ps1 -Language
it,de; an app on an older Laching, once per language), with the windows hidden (screenshots.ps1 -Hidden): you can keep
working meanwhile, and the shots show the windows inactive. -Themes (light, dark; both by default) passes through to
screenshots.ps1, so a check of one theme never draws the other.
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [string[]] $Only = @(),
    [string[]] $Themes = @(),
    [int] $Jobs = $(if ($env:LAYOUT_JOBS) { [int]$env:LAYOUT_JOBS } else { 4 }),
    [ValidateSet("x64", "ARM64")][string] $Platform = $(if ([Runtime.InteropServices.RuntimeInformation]::OSArchitecture -eq "Arm64") { "ARM64" } else { "x64" }),
    [string] $Project = "",
    [string] $Out = "",
    [Parameter(ValueFromRemainingArguments = $true)][string[]] $Languages = @()
)
$ErrorActionPreference = "Stop"
$screenshots = Join-Path $PSScriptRoot "screenshots.ps1"

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
    $dir = (Get-Location).Path
    while ($dir) {
        if (Test-Path (Join-Path $dir "companion.json")) { return $dir }
        $parent = Split-Path $dir
        if ($parent -eq $dir) { break }
        $dir = $parent
    }
    throw "No companion.json here or above; set COMPANION_ROOT, or pass -Project and the languages."
}

if ($Project) {
    $Project = (Resolve-Path $Project).Path
    if (-not $Out) { $Out = Join-Path ([IO.Path]::GetTempPath()) "$([IO.Path]::GetFileNameWithoutExtension($Project))-layout-check" }
}
if (-not $Project -or $Languages.Count -eq 0) {
    $root = Find-Root
    $config = Read-Companion (Join-Path $root "companion.json")
    if ($Languages.Count -eq 0) { $Languages = @($config.languages.all) }
    if (-not $Project) {
        if (-not $config.windows -or -not $config.windows.app) { throw "companion.json has no windows.app." }
        $Project = (Resolve-Path (Join-Path $root "$($config.windows.dir)/$($config.windows.app)/$($config.windows.app).csproj")).Path
        if (-not $Out) { $Out = Join-Path $root "$($config.windows.dir)/build/layout-check" }
    }
}
$Out = [IO.Path]::GetFullPath($Out)

& $screenshots -BuildOnly -Project $Project -Platform $Platform
if ($LASTEXITCODE) { exit 1 }
# A packaged app (WindowsPackageType MSIX) runs from its one development registration, one copy at a time: a second
# language's run would find the first one's copy running, or close it by registering the package again.
$packageType = (dotnet msbuild $Project -p:Platform=$Platform -p:Configuration=Debug -getProperty:WindowsPackageType | Out-String).Trim()
if ($Jobs -gt 1 -and $Languages.Count -gt 1 -and $packageType -and $packageType -ne "None") {
    "The app is packaged: one run for every language."
    $Jobs = 1
}

# The languages dealt out among the runs in turn, so each run launches the app once for its share.
$groups = @(for ($index = 0; $index -lt [Math]::Min([Math]::Max(1, $Jobs), $Languages.Count); $index++) { , @() })
for ($index = 0; $index -lt $Languages.Count; $index++) { $groups[$index % $groups.Count] += $Languages[$index] }

# Each run in a PowerShell of its own (the run's settings travel in environment variables), the same one this runs in,
# all at once. Its output is read in the console's encoding, which is the one it writes in.
$shell = (Get-Process -Id $PID).Path
function Quote([string] $argument) { '"' + ($argument -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"' }
function Start-Group([string[]] $group) {
    # Only this run's shots, so the folder shows what failed now.
    foreach ($language in $group) {
        Remove-Item (Join-Path (Join-Path $Out $language) "*.png"), (Join-Path (Join-Path $Out $language) "problems.txt") -ErrorAction SilentlyContinue
    }
    # One language goes straight into its folder; several, each into its own under $Out.
    $arguments = @("-NoProfile", "-NonInteractive", "-File", $screenshots, "-NoBuild", "-Hidden", "-Project", $Project,
        "-Platform", $Platform, "-Language", ($group -join ","), "-Out", $(if ($group.Count -gt 1) { $Out } else { Join-Path $Out $group[0] }))
    if ($Only) { $arguments += @("-Only", ($Only -join ",")) }
    if ($Themes) { $arguments += @("-Themes", ($Themes -join ",")) }
    $info = New-Object Diagnostics.ProcessStartInfo $shell
    $info.Arguments = ($arguments | ForEach-Object { Quote $_ }) -join " "
    $info.UseShellExecute = $false
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = [Console]::OutputEncoding
    $info.StandardErrorEncoding = [Console]::OutputEncoding
    $process = [Diagnostics.Process]::Start($info)
    @{ Languages = $group; Process = $process; Out = $process.StandardOutput.ReadToEndAsync(); Err = $process.StandardError.ReadToEndAsync() }
}

$runs = @(foreach ($group in $groups) { Start-Group $group })
foreach ($run in $runs) { $run.Process.WaitForExit() }

# A language failed when its folder has problems, or its run failed without saying which (the app stopped).
$failed = @()
foreach ($run in $runs) {
    "== $($run.Languages -join ' ')"
    if ($run.Process.ExitCode -ne 0) {
        [Console]::Error.Write($run.Err.Result)
        $named = @($run.Languages | Where-Object { Test-Path (Join-Path (Join-Path $Out $_) "problems.txt") })
        $failed += $(if ($named.Count) { $named } else { $run.Languages })
    }
}
if ($failed.Count -gt 0) {
    [Console]::Error.WriteLine("Layout problems in: $($failed -join ' ') (shots in $Out)")
    exit 1
}
"Every window fits in: $($Languages -join ' ')"
