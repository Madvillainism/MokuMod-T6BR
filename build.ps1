# Compila el HUD a un .exe autónomo (sin Python ni Node instalados).
#
#   .\build.ps1              # carpeta dist\FRAMEMOD\FRAMEMOD.exe (arranque rápido)
#   .\build.ps1 -OneFile     # un único .exe (más lento al arrancar)
#
# Requisitos: Python 3.10+ en PATH.  PyInstaller se instala solo si falta.
param(
    [switch]$OneFile,
    [switch]$NoInstall
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "== FRAMEMOD build ==" -ForegroundColor Cyan

python -c "import sys; print('python', sys.version.split()[0])"
if ($LASTEXITCODE -ne 0) { throw "Python no está en PATH" }

python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    if ($NoInstall) { throw "Falta PyInstaller (pip install pyinstaller)" }
    Write-Host "Instalando PyInstaller..." -ForegroundColor Yellow
    python -m pip install --upgrade pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "No se pudo instalar PyInstaller" }
}

if ($OneFile) {
    python -m PyInstaller --noconfirm --clean --onefile `
        --name FRAMEMOD `
        --paths tools `
        --add-data "data;data" `
        --console `
        tools/hud.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller falló" }
    Write-Host "OK -> dist\FRAMEMOD.exe" -ForegroundColor Green
} else {
    python -m PyInstaller --noconfirm --clean framemod.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller falló" }
    Write-Host "OK -> dist\FRAMEMOD\FRAMEMOD.exe" -ForegroundColor Green
}

Write-Host ""
Write-Host "Siguiente paso:" -ForegroundColor Cyan
Write-Host "  1. Abre PPSSPP y Tekken 6 en Practice"
Write-Host "  2. Ejecuta el .exe (opcional: --p1-char bryan --learn)"
Write-Host "  3. F8 abre el panel de ajustes (posición, opacidad, fuente)"
