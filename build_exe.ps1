$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Create the virtual environment first: python -m venv .venv"
}
& $python -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
& $python -m PyInstaller --noconfirm --clean --onefile --noconsole --icon "app\favicon.ico" --name Hesabyar --add-data "app\static;app\static" --add-data "app\favicon.ico;app" --add-data "app\favicon.png;app" run_app.py
Write-Host "Executable created at dist\Hesabyar.exe"
