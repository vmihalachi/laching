<#
Builds a companion app's Microsoft Store release, the one routine every app and its CI run: for each platform an
unsigned Native AOT package (the Store signs), checked native, installed fresh and started; then one .msixbundle of
them, ready for Partner Center; then, when asked, the push to Partner Center (store/push-microsoft.py) and the
win-<version> tag. Windows PowerShell 5.1 or PowerShell 7. Reads companion.json (found from the current folder up, then
from this script's, or $env:COMPANION_ROOT): the project (windows.dir + windows.app), and windows.release for the
platforms and the bundle's name. The version is the Identity Version of the project's Package.appxmanifest.

Usage: laching/tools/windows/store-release.ps1 [-Out folder] [-Platforms x64,ARM64] [-NoSmoke] [-SideBySide]
                                                [-Screenshots folder [-Languages en,de]] [-NoBundle]
                                                [-Store none|draft|submit [-Notes file]] [-Tag] [-AnyVersion]
       laching/tools/windows/store-release.ps1 -BundleFrom folder [-Out folder] [-Store ...] [-Tag]
       laching/tools/windows/store-release.ps1 -VersionOnly [-AnyVersion]
       laching/tools/windows/store-release.ps1 -TagOnly
  -Out          where the packages and the bundle go; build/store-release/<version> under the app by default.
  -Platforms    windows.release.platforms, else x64 and ARM64.
  -NoSmoke      no install check (it needs Developer Mode; without it the script refuses). An ARM64 package can't run on
                an x64 PC: there it is checked native only, and the script says so. An x64 package on an ARM64 PC runs
                emulated and gets 60 seconds to open its window instead of 20.
  -SideBySide   the install check runs next to a Store copy of the app (smoke-package.ps1 -SideBySide).
  -Screenshots  also every window under Native AOT, from the package: publishes this PC's platform once more with
                screenshot mode compiled in (-p:ScreenshotMode=true), installs it and runs the screenshot run in each of
                -Languages (en by default) into <folder>/<language> (smoke-package.ps1 -Screenshots). Outside AppData.
  -NoBundle     the packages only (CI builds each platform in its own job, then bundles with -BundleFrom).
  -BundleFrom   no build: bundles the one .msix per platform found under the folder, after checking each is native.
  -Store        draft: a new Partner Center submission with the bundle and the listing text, saved but not sent; submit:
                sent to certification too. Needs the Partner Center credentials (tools/store/api-setup.md).
  -Tag          tags HEAD win-<version> and pushes the tag once everything passed (and, with -Store, was uploaded).
                Without it the script asks after a successful upload (in a console), or prints the command.
  -AnyVersion   no refusal of a version already tagged win-<version> (a build check, not a release).
  -VersionOnly  prints the version (and writes version=<it> to $env:GITHUB_OUTPUT when set), after the tag check.
  -TagOnly      tags HEAD win-<version> and pushes the tag, after an upload made by hand.
Every upload needs a higher version than any before it (Partner Center refuses a lower one), so the script refuses a
version whose win-<version> tag exists, locally or on origin: bump Package.appxmanifest first.
The packages are Release builds without UapAppxPackageBuildMode=StoreUpload: that mode's .msixupload step fails under the
dotnet CLI (System.Security.Permissions), and the bundle is what Partner Center takes.
#>
param(
    [string] $Out = "",
    [string[]] $Platforms = @(),
    [switch] $NoSmoke,
    [switch] $SideBySide,
    [string] $Screenshots = "",
    [string[]] $Languages = @(),
    [switch] $NoBundle,
    [string] $BundleFrom = "",
    [ValidateSet("none", "draft", "submit")][string] $Store = "none",
    [string] $Notes = "",
    [switch] $Tag,
    [switch] $AnyVersion,
    [switch] $VersionOnly,
    [switch] $TagOnly
)
$ErrorActionPreference = "Stop"
# -Platforms x64,ARM64 arrives as one string from cmd and CI, as two from PowerShell.
$Platforms = @($Platforms | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$Languages = @($Languages | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$tools = Split-Path $PSScriptRoot
$smoke = Join-Path $PSScriptRoot "smoke-package.ps1"

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

$root = Find-Root
$config = Read-Companion (Join-Path $root "companion.json")
if (-not $config.windows -or -not $config.windows.app) { throw "companion.json has no windows.app." }
$appDir = Join-Path $root (Join-Path $config.windows.dir $config.windows.app)
$project = Join-Path $appDir "$($config.windows.app).csproj"
$release = $config.windows.release
if (-not $Platforms) { $Platforms = if ($release -and $release.platforms) { @($release.platforms) } else { @("x64", "ARM64") } }
foreach ($platform in $Platforms) {
    if ($platform -notin "x64", "ARM64") { throw "Platform ${platform}: the Store packages are x64 and ARM64." }
}

$manifestPath = Join-Path $appDir "Package.appxmanifest"
if (-not (Test-Path $manifestPath)) { throw "No $manifestPath." }
$version = ([xml](Get-Content $manifestPath -Raw -Encoding UTF8)).Package.Identity.Version
if ($version -notmatch '^\d+\.\d+\.\d+\.\d+$') { throw "No Identity Version in $manifestPath." }
$tagName = "win-$version"

# git with its errors kept quiet: Windows PowerShell turns a native command's redirected stderr into errors, which
# stop the script.
function Invoke-Git {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { git -C $root @args 2>$null } finally { $ErrorActionPreference = $previous }
}

# A line for the GitHub Actions step's outputs, UTF-8 without the BOM Windows PowerShell's Add-Content writes.
function Write-Output-Value([string] $line) {
    if ($env:GITHUB_OUTPUT) { [IO.File]::AppendAllText($env:GITHUB_OUTPUT, "$line`n") }
}

function Test-Released {
    # The tag on origin, where every release pushes it; this clone's tags when origin can't be asked.
    $null = Invoke-Git ls-remote --exit-code --tags origin "refs/tags/$tagName"
    if ($LASTEXITCODE -eq 0) { return $true }
    if ($LASTEXITCODE -eq 2) { return $false }
    Write-Warning "Couldn't ask origin for $tagName (git exit $LASTEXITCODE): checked this clone's tags only."
    $null = Invoke-Git rev-parse -q --verify "refs/tags/$tagName"
    return $LASTEXITCODE -eq 0
}

function Push-Tag {
    $null = Invoke-Git rev-parse -q --verify "refs/tags/$tagName"
    if ($LASTEXITCODE -ne 0) {
        git -C $root tag $tagName
        if ($LASTEXITCODE -ne 0) { throw "git tag $tagName failed." }
    }
    git -C $root push origin "refs/tags/$tagName"
    if ($LASTEXITCODE -ne 0) { throw "Pushing $tagName failed." }
    "Tagged $(git -C $root rev-parse --short HEAD) $tagName and pushed it."
}

if ($TagOnly) {
    if (Test-Released) { throw "$tagName exists already." }
    Push-Tag
    exit 0
}
if (-not $AnyVersion -and (Test-Released)) {
    throw "$tagName exists: bump the Identity Version in $manifestPath first (every upload needs a higher version)."
}
if ($VersionOnly) {
    Write-Output-Value "version=$version"
    $version
    exit 0
}

if (-not $Out) { $Out = Join-Path $root "build/store-release/$version" }
$Out = [IO.Path]::GetFullPath($Out)
New-Item -ItemType Directory -Force $Out | Out-Null
$hostArch = try { "$([Runtime.InteropServices.RuntimeInformation]::OSArchitecture)" } catch { "" }
if (-not $hostArch) { $hostArch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE } }
$hostArm = $hostArch -match '^(Arm64|ARM64)$'
$interactive = [Environment]::UserInteractive -and -not $env:CI -and -not $env:GITHUB_ACTIONS -and $Host.Name -eq "ConsoleHost"

# What gets uploaded should be what's on the default branch, committed.
$branch = Invoke-Git rev-parse --abbrev-ref HEAD
$dirty = Invoke-Git status --porcelain --untracked-files=no
if ($branch -notin "master", "main", "HEAD" -or $dirty) {
    Write-Warning "Building from $branch$(if ($dirty) { ' with uncommitted changes' })."
}

# smoke-package.ps1 in this PowerShell, its failure a terminating error.
function Invoke-Smoke([hashtable] $arguments) {
    & $smoke @arguments
}

# A package's architecture from its file name (<Name>_<version>_<arch>.msix).
function Get-Arch([IO.FileInfo] $msix) {
    $msix.BaseName.Substring($msix.BaseName.LastIndexOf("_") + 1)
}

# The app's packages under a folder: one .msix per platform, never the Windows App Runtime's under Dependencies\, the
# screenshot-mode package or an earlier bundle's copies.
function Find-Packages([string] $folder) {
    @(Get-ChildItem $folder -Recurse -Filter "*_${version}_*.msix" -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -notmatch '[\\/](Dependencies|screenshot-mode|bundle-input)[\\/]' })
}

function Publish([string] $platform, [string] $folder, [string[]] $extra) {
    # A trailing / and never \: a quoted argument ending in \" would swallow the quote.
    $dir = ($folder -replace '\\', '/').TrimEnd('/') + "/"
    $arguments = @("publish", $project, "-c", "Release", "-p:Platform=$platform", "-p:GenerateAppxPackageOnBuild=true",
        "-p:AppxPackageSigningEnabled=false", "-p:AppxBundle=Never", "-p:AppxPackageDir=$dir") + $extra
    & dotnet @arguments | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "The $platform publish failed." }
    $arch = $platform.ToLowerInvariant()
    $msix = @(Find-Packages $folder | Where-Object { (Get-Arch $_) -eq $arch } | Sort-Object LastWriteTime | Select-Object -Last 1)
    if (-not $msix) { throw "No ${platform} .msix of version $version under $folder." }
    $msix[0]
}

$smokeNotes = @()
if (-not $BundleFrom) {
    # Native AOT links with the Visual Studio tools, found through vswhere, which the Build Tools installer doesn't put
    # on PATH (a plain shell, an ARM64 PC, a hosted runner).
    $installer = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer"
    if (-not (Get-Command vswhere.exe -ErrorAction SilentlyContinue) -and (Test-Path (Join-Path $installer "vswhere.exe"))) {
        $env:PATH = "$installer;$env:PATH"
    }
    if (-not $NoSmoke -or $Screenshots) {
        $unlock = Get-ItemProperty HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock -ErrorAction SilentlyContinue
        if (-not $unlock -or $unlock.AllowDevelopmentWithoutDevLicense -ne 1) {
            throw "The install check needs Developer Mode (Settings > System > For developers), or pass -NoSmoke."
        }
    }

    $packages = @()
    foreach ($platform in $Platforms) {
        $arch = $platform.ToLowerInvariant()
        # An earlier run's package of this platform must not stand in for this one.
        Get-ChildItem $Out -Directory -Filter "*_${arch}_Test" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
        Write-Host "== ${platform}: publishing $version"
        $msix = Publish $platform $Out @()
        Invoke-Smoke @{ Package = $msix.FullName; CheckOnly = $true }
        if (-not $NoSmoke) {
            if ($arch -eq "arm64" -and -not $hostArm) {
                $smokeNotes += "$($msix.Name) wasn't started: an x64 PC can't run ARM64. Start it on an ARM64 PC before uploading."
            }
            else {
                $seconds = if ($hostArm -and $arch -ne "arm64") { 60 } else { 20 }
                Invoke-Smoke @{ Package = $msix.FullName; Seconds = $seconds; SideBySide = [bool]$SideBySide }
            }
        }
        $packages += $msix
    }

    if ($Screenshots) {
        $platform = if ($hostArm) { "ARM64" } else { "x64" }
        $shotPackages = Join-Path $Out "screenshot-mode"
        if (Test-Path $shotPackages) { Remove-Item $shotPackages -Recurse -Force }
        Write-Host "== ${platform}: publishing with screenshot mode"
        $msix = Publish $platform $shotPackages @("-p:ScreenshotMode=true")
        if (-not $Languages) { $Languages = @("en") }
        $failed = @()
        foreach ($language in $Languages) {
            try {
                Invoke-Smoke @{ Package = $msix.FullName; Screenshots = (Join-Path $Screenshots $language); Language = $language; SideBySide = [bool]$SideBySide }
            }
            catch { Write-Host $_; $failed += $language }
        }
        if ($failed) { throw "The screenshot run failed in: $($failed -join ', ')" }
    }
}
else {
    $packages = Find-Packages $BundleFrom
    foreach ($msix in $packages) { Invoke-Smoke @{ Package = $msix.FullName; CheckOnly = $true } }
}

$bundle = $null
if (-not $NoBundle -and ($BundleFrom -or $Platforms.Count -gt 1)) {
    $archs = @($packages | ForEach-Object { Get-Arch $_ })
    if ($archs.Count -lt 2 -or @($archs | Select-Object -Unique).Count -ne $archs.Count) {
        throw "A bundle takes one package per platform; found $($packages.Count): $(($packages | ForEach-Object { $_.Name }) -join ', ')."
    }
    # makeappx comes with the Windows SDK and with the Microsoft.Windows.SDK.BuildTools NuGet the build restores; this
    # PC's own architecture first, else x64 (which an ARM64 PC runs emulated).
    $kits = @(Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"; Join-Path $HOME ".nuget\packages\microsoft.windows.sdk.buildtools")
    $found = @($kits | Where-Object { Test-Path $_ } | ForEach-Object { Get-ChildItem $_ -Recurse -Filter makeappx.exe -ErrorAction SilentlyContinue })
    $preferred = if ($hostArm) { "arm64", "x64" } else { "x64" }
    $makeappx = $null
    foreach ($dir in $preferred) {
        if (-not $makeappx) { $makeappx = $found | Where-Object { $_.Directory.Name -eq $dir } | Sort-Object FullName | Select-Object -Last 1 }
    }
    if (-not $makeappx) { throw "makeappx.exe isn't in the Windows SDK or the Microsoft.Windows.SDK.BuildTools NuGet." }

    $name = if ($release -and $release.bundle) { $release.bundle } else {
        # The executable's name, from the first package's manifest.
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $zip = [IO.Compression.ZipFile]::OpenRead($packages[0].FullName)
        try {
            $reader = New-Object IO.StreamReader($zip.GetEntry("AppxManifest.xml").Open())
            try { $exe = (@(([xml]$reader.ReadToEnd()).Package.Applications.Application)[0]).Executable } finally { $reader.Dispose() }
        } finally { $zip.Dispose() }
        [IO.Path]::GetFileNameWithoutExtension($exe)
    }
    $bundleInput = Join-Path $Out "bundle-input"
    if (Test-Path $bundleInput) { Remove-Item $bundleInput -Recurse -Force }
    New-Item -ItemType Directory -Force $bundleInput | Out-Null
    $packages | Copy-Item -Destination $bundleInput
    $bundle = Join-Path $Out "${name}_${version}_$(($archs | ForEach-Object { $_.ToLowerInvariant() } | Sort-Object { $_ -ne 'x64' }, { $_ }) -join '_').msixbundle"
    & $makeappx.FullName bundle /d $bundleInput /p $bundle /bv $version /o | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "makeappx failed ($LASTEXITCODE)." }
    Write-Output-Value "bundle=$bundle"
}

"`n$version, ready for Partner Center:"
$packages | ForEach-Object { "  $($_.FullName)  ($([math]::Round($_.Length / 1MB, 1)) MB)" }
if ($bundle) { "  $bundle  ($([math]::Round((Get-Item $bundle).Length / 1MB, 1)) MB)" }
$smokeNotes | ForEach-Object { Write-Warning $_ }

$uploaded = $false
if ($Store -ne "none") {
    if (-not $bundle) { throw "-Store pushes the bundle: build both platforms (or pass -BundleFrom)." }
    $python = @("python", "python3", "py") | Where-Object { Get-Command $_ -ErrorAction SilentlyContinue } | Select-Object -First 1
    if (-not $python) { throw "The Partner Center push needs Python 3 on PATH." }
    $pusher = Join-Path $tools "store/push-microsoft.py"
    $prepare = @($pusher, "prepare", "--bundle", $bundle, "--only", "package,text", "--apply")
    if ($Notes) { $prepare += @("--notes", $Notes) }
    $env:COMPANION_ROOT = $root
    & $python @prepare
    if ($LASTEXITCODE -ne 0) { throw "The Partner Center push failed." }
    if ($Store -eq "submit") {
        & $python $pusher commit
        if ($LASTEXITCODE -ne 0) { throw "Sending the submission to certification failed." }
    }
    $uploaded = $true
}

if ($Tag) {
    Push-Tag
}
elseif ($bundle) {
    $answer = ""
    if ($uploaded -and $interactive) { $answer = Read-Host "Tag $(git -C $root rev-parse --short HEAD) $tagName and push the tag? [y/N]" }
    if ($answer -match '^(y|yes)$') { Push-Tag }
    else { "Once it is uploaded, tag it: $($MyInvocation.MyCommand.Path) -TagOnly" }
}
if ($interactive -and -not $BundleFrom -and $Store -eq "none") { Start-Process explorer.exe $Out }
