param([switch]$Practice)
$ErrorActionPreference = 'Stop'
$companionRoot = $PSScriptRoot
$companionExe = Join-Path $companionRoot 'node_modules\electron\dist\electron.exe'
if (-not (Test-Path -LiteralPath $companionExe)) { throw 'Run npm install in the desktop directory first.' }
$companionArgs = @('.')
if ($Practice) { $companionArgs += '--practice' }
Start-Process -FilePath $companionExe -ArgumentList $companionArgs -WorkingDirectory $companionRoot -WindowStyle Normal
