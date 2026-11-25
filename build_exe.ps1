$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$ExePath = Join-Path $ProjectRoot "dist\PhotoBox.exe"
$Running = Get-Process -Name "PhotoBox" -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $ExePath }
if ($Running) {
    throw "Cannot overwrite $ExePath while PhotoBox.exe is running (PID: $($Running.Id -join ', ')). Please close PhotoBox.exe first."
}

Write-Host "Building PhotoBox.exe via PyInstaller..." -ForegroundColor Cyan
python -m PyInstaller --noconfirm --onefile --windowed --name "PhotoBox" --icon "assets\icons\photobox.ico" --add-data "assets\icons;assets\icons" --add-data "assets\fonts;assets\fonts" main.py
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed with exit code $LASTEXITCODE."
}

if (-not (Test-Path $ExePath)) {
    throw "Build failed: $ExePath was not created."
}

$SizeMB = [Math]::Round(((Get-Item $ExePath).Length / 1MB), 2)
Write-Host "Build succeeded: $ExePath ($SizeMB MB)" -ForegroundColor Green
