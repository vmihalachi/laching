<#
Renders the WinUI app's windows in each language and fails when one has a problem: text that doesn't fit its box
(LayoutCheck), a window that didn't open, couldn't be drawn or was captured before it drew, a shot that wasn't written
(the checks screenshots.ps1 fails on). Longer translations are where text runs out of room, so run it after changing text or a window's layout.
Windows only; the Mac's is check-layout.sh.

Usage: laching/tools/windows/check-layout.ps1 [-Only name,name] [-Jobs n] [-Platform x64|ARM64] [-Project path] [-Out folder]
                                                [language ...]
       (default: every language in companion.json's languages.all)
Shots go to <windows.dir>\build\layout-check\<language> (with -Project, <name>-layout-check\<language> in the temporary
folder; with -Out, <folder>\<language>), with the language's problems.txt when it has problems, to look at what
failed. It builds once, then runs the languages side by side (-Jobs at a time: $env:LAYOUT_JOBS, default 4; a packaged
app's one at a time, since it runs one copy) with the windows hidden (screenshots.ps1 -Hidden): you can keep working
meanwhile, and the shots show the windows inactive.
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [string[]] $Only = @(),
    [int] $Jobs = $(if ($env:LAYOUT_JOBS) { [int]$env:LAYOUT_JOBS } else { 4 }),
    [ValidateSet("x64", "ARM64")][string] $Platform = $(if ([Runtime.InteropServices.RuntimeInformation]::OSArchitecture -eq "Arm64") { "ARM64" } else { "x64" }),
    [string] $Project = "",
    [string] $Out = "",
    [Parameter(ValueFromRemainingArguments = $true)][string[]] $Languages = @()
)
$ErrorActionPreference = "Stop"
$screenshots = Join-Path $PSScriptRoot "screenshots.ps1"

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
    $config = Get-Content (Join-Path $root "companion.json") -Raw -Encoding UTF8 | ConvertFrom-Json
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
    "The app is packaged: one language at a time."
    $Jobs = 1
}

# Each language in a PowerShell of its own (the run's settings travel in environment variables), the same one this runs
# in. Its output is read in the console's encoding, which is the one it writes in.
$shell = (Get-Process -Id $PID).Path
function Quote([string] $argument) { '"' + ($argument -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"' }
function Start-Language([string] $language) {
    # Only this run's shots, so the folder shows what failed now.
    Remove-Item (Join-Path (Join-Path $Out $language) "*.png"), (Join-Path (Join-Path $Out $language) "problems.txt") -ErrorAction SilentlyContinue
    $arguments = @("-NoProfile", "-NonInteractive", "-File", $screenshots, "-NoBuild", "-Hidden", "-Project", $Project,
        "-Platform", $Platform, "-Language", $language, "-Out", (Join-Path $Out $language))
    if ($Only) { $arguments += @("-Only", ($Only -join ",")) }
    $info = New-Object Diagnostics.ProcessStartInfo $shell
    $info.Arguments = ($arguments | ForEach-Object { Quote $_ }) -join " "
    $info.UseShellExecute = $false
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = [Console]::OutputEncoding
    $info.StandardErrorEncoding = [Console]::OutputEncoding
    $process = [Diagnostics.Process]::Start($info)
    @{ Process = $process; Out = $process.StandardOutput.ReadToEndAsync(); Err = $process.StandardError.ReadToEndAsync() }
}

$waiting = New-Object System.Collections.Queue
$Languages | ForEach-Object { $waiting.Enqueue($_) }
$running = @{}
$results = @{}
while ($waiting.Count -gt 0 -or $running.Count -gt 0) {
    while ($waiting.Count -gt 0 -and $running.Count -lt [Math]::Max(1, $Jobs)) {
        $language = $waiting.Dequeue()
        $running[$language] = Start-Language $language
    }
    foreach ($language in @($running.Keys)) {
        $run = $running[$language]
        if (-not $run.Process.HasExited) { continue }
        $run.Process.WaitForExit()
        $results[$language] = @{ Code = $run.Process.ExitCode; Err = $run.Err.Result }
        $running.Remove($language)
    }
    Start-Sleep -Milliseconds 100
}

$failed = @()
foreach ($language in $Languages) {
    "== $language"
    if ($results[$language].Code -ne 0) {
        $failed += $language
        [Console]::Error.Write($results[$language].Err)
    }
}
if ($failed.Count -gt 0) {
    [Console]::Error.WriteLine("Layout problems in: $($failed -join ' ') (shots in $Out)")
    exit 1
}
"Every window fits in: $($Languages -join ' ')"
