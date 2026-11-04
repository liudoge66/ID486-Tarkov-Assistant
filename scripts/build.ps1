$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot
$taskPython = Join-Path $repoRoot '.venv/Scripts/python.exe'
if (!(Test-Path -LiteralPath $taskPython)) { throw 'Create .venv and install scripts/requirements-build.txt first.' }
& $taskPython "$PSScriptRoot/build.py"
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
