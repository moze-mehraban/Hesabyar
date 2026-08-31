$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Create the virtual environment first: python -m venv .venv"
}
& $python -m PyInstaller --noconfirm --clean --onefile --noconsole --icon "app\favicon.ico" --name HesabyarSellerEdit --add-data "seller_static;seller_static" --add-data "app\favicon.ico;app" --add-data "app\favicon.png;app" run_seller.py
Write-Host "Executable created at dist\HesabyarSellerEdit.exe"
