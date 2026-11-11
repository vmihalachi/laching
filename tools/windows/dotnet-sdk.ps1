<#
Whether the runner image's .NET SDK will do, so a GitHub-hosted job can skip actions/setup-dotnet. setup-dotnet
downloads the newest .NET 11 SDK (300 MB, 30-odd seconds) whenever the image is a patch behind; the image's own SDK does
when it is in the newest feature band (11.0.1xx, 11.0.2xx…), the latitude a global.json's latestPatch gives. Asked in the
current folder, which is the one whose global.json picks the SDK (the .NET host takes the nearest from the working
folder up): the step's working-directory, where the job then runs dotnet (the app's windows.dir in windows-app.yml,
web/ in web.yml). A global.json there must accept the image's SDK too: one it doesn't meet fails dotnet --version, and
the answer is false. Writes use=true or use=false to $GITHUB_OUTPUT and always exits 0: with no dotnet, or no answer
from builds.dotnet.microsoft.com, it says false and setup-dotnet runs.

Usage: a step with id sdk, then setup-dotnet with if: steps.sdk.outputs.use != 'true'
  pwsh tools/windows/dotnet-sdk.ps1      (Laching's own workflows; web.yml runs ../tools/windows/dotnet-sdk.ps1 in web/,
                                          windows-app.yml the app's submodule's copy, if it has it, by its full path)
  -Newest 11.0.1xx stands in for the download (the tests).
Never for a build that ships: a Native AOT app ships the SDK's runtime, so windows-release.yml always runs setup-dotnet.
#>
param([string] $Newest = "")
$band = { param($v) if ($v -match '^11\.0\.(\d)\d\d$') { $Matches[1] } }
$image = dotnet --version 2>$null
if ($LASTEXITCODE -ne 0) { $image = $null }
if (-not $Newest) {
    $index = 'https://builds.dotnet.microsoft.com/dotnet/release-metadata/releases-index.json'
    $Newest = try {
        (Invoke-RestMethod $index).'releases-index' | Where-Object 'channel-version' -eq '11.0' | ForEach-Object 'latest-sdk'
    } catch { }
}
$use = $null -ne (& $band $image) -and (& $band $image) -eq (& $band $Newest)
"The image's SDK: $image; the newest: $Newest; using the image's: $use"
if ($env:GITHUB_OUTPUT) { "use=$("$use".ToLower())" >> $env:GITHUB_OUTPUT }
exit 0
