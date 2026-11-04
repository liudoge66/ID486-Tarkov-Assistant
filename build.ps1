$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (!(Test-Path -LiteralPath $taskPython)) { throw 'Create .venv and install requirements-build.txt first.' }
& $taskPython "$PSScriptRoot/build.py"
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
