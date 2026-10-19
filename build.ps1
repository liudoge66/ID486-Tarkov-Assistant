$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (!(Test-Path -LiteralPath $taskPython)) { throw 'Create .venv and install requirements-build.txt first.' }
& $taskPython -m PyInstaller --noconfirm --onefile --windowed --name 'ID486模拟助手' --icon "$PSScriptRoot/assets/subaru_clean.ico" --hidden-import dxcam.processor._numpy_kernels --add-data "$PSScriptRoot/assets;assets" monitor.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
