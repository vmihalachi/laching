<#
The launch check of one platform's Store package in CI, the one step every Laching workflow that builds a package runs
(windows-app.yml's app job, windows-release.yml's package jobs), so what a runner can check is decided here once:
store-release.ps1 publishes the unsigned Native AOT package, checks it native and within windows.release.maxMB
(smoke-package.ps1 -CheckOnly), then installs it fresh and starts it where the runner can run it. Windows PowerShell 5.1
or PowerShell 7.

Usage: laching/tools/windows/ci-package.ps1 -Platform x64|ARM64 [-Hosted] [-Out folder]
                                             [-Screenshots folder [-Languages en,de]]
  -Platform     the package's platform.
  -Hosted       GitHub's runner (windows-latest, x64): Developer Mode turned on first (reg add; the runner's user is an
                admin), then the x64 package installed and started; the ARM64 one is checked only (native, its size), as
                an x64 PC can't run it: start it on an ARM64 PC before uploading. Without it, the self-hosted runner:
                Developer Mode is on by hand there (its service has no admin rights to turn it on), and each package is
                started next to the Store copy installed there (store-release.ps1 -SideBySide).
  -Out          where the package goes; $env:RUNNER_TEMP\AppPackages by default.
  -Screenshots  also the screenshot run from the package, in -Languages, into <folder>/<language> (store-release.ps1).
Never bundles (the release's bundle job does, with -BundleFrom) and takes any version (-AnyVersion: the release's
version job refused a released one before this runs; a build check isn't a release).
#>
param(
    [Parameter(Mandatory)][ValidateSet("x64", "ARM64")][string] $Platform,
    [switch] $Hosted,
    [string] $Out = "",
    [string] $Screenshots = "",
    [string[]] $Languages = @()
)
$ErrorActionPreference = "Stop"

if (-not $Out) {
    if (-not $env:RUNNER_TEMP) { throw "-Out, or RUNNER_TEMP (the runner's temporary folder): where the package goes." }
    $Out = Join-Path $env:RUNNER_TEMP "AppPackages"
}
# GitHub's runner is x64: an ARM64 package can't start there.
$start = -not $Hosted -or $Platform -eq "x64"
$arguments = @{ Platforms = $Platform; Out = $Out; NoBundle = $true; AnyVersion = $true }
if (-not $start) { $arguments.NoSmoke = $true }
if (-not $Hosted) { $arguments.SideBySide = $true }
if ($Screenshots) {
    $arguments.Screenshots = $Screenshots
    if ($Languages) { $arguments.Languages = $Languages }
}
if ($Hosted -and ($start -or $Screenshots)) {
    # The install needs Developer Mode: the package is unsigned.
    reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock" /t REG_DWORD /f /v AllowDevelopmentWithoutDevLicense /d 1 | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Turning on Developer Mode failed (reg exit $LASTEXITCODE)." }
}
& (Join-Path $PSScriptRoot "store-release.ps1") @arguments
