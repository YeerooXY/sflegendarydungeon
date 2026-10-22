param(
    [ValidateRange(0, 1000000)][int]$Budget = 1000,
    [ValidateSet(450, 900, 2400)][int]$Pace = 900,
    [ValidateRange(0, 2000000000)][int]$Seed = (Get-Random -Minimum 100000 -Maximum 1000000000)
)
$ErrorActionPreference = 'Stop'
$watchExe = Join-Path $PSScriptRoot 'node_modules\electron\dist\electron.exe'
if (-not (Test-Path -LiteralPath $watchExe)) { throw 'Run npm ci in the desktop directory first.' }
Start-Process -FilePath $watchExe -ArgumentList @('watch.cjs', "--budget=$Budget", "--pace=$Pace", "--seed=$Seed") -WorkingDirectory $PSScriptRoot -WindowStyle Normal
