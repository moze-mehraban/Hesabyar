"""ساخت بکاپ از SQLite و ارسال اختیاری آن به تلگرام.

برای اجرای روزانه، این فایل را با Task Scheduler ویندوز یا cron اجرا کنید.
"""
from __future__ import annotations

import os
import shutil
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> bool:
        return False

load_dotenv()
root = Path(__file__).resolve().parent
db_path = Path(os.getenv("DATABASE_PATH", "data/accounting.db"))
if not db_path.is_absolute():
    db_path = root / db_path
backup_dir = root / "backups"
backup_dir.mkdir(exist_ok=True)
target = backup_dir / f"accounting-{datetime.now():%Y%m%d-%H%M%S}.db"
shutil.copy2(db_path, target)

token = os.getenv("TELEGRAM_BOT_TOKEN", "")
chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
if token and chat_id:
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    with target.open("rb") as file:
        boundary = "----accountingbackup"
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{target.name}\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + file.read() + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    urllib.request.urlopen(request, timeout=30).read()

print(f"Backup created: {target}")
