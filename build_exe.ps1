$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "ابتدا محیط مجازی را بسازید: python -m venv .venv"
}
& $python -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
& $python -m PyInstaller --noconfirm --clean --onefile --name Hesabyar --add-data "app\static;app\static" run_app.py
Write-Host "فایل اجرایی در dist\Hesabyar.exe ساخته شد."
