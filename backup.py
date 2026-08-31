"""Create a local SQLite backup and optionally send it to Telegram."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> bool:
        return False

load_dotenv()


class BackupError(RuntimeError):
    """Raised when the local backup succeeds but Telegram upload fails."""


@dataclass(frozen=True)
class BackupResult:
    target: Path
    telegram_sent: bool


def create_backup(
    *,
    database_path: Path,
    backup_dir: Path,
    telegram_token: str | None = None,
    chat_id: str | None = None,
    socks_proxy: str | None = None,
) -> BackupResult:
    """Create a backup and optionally upload it to Telegram."""
    database_path = Path(database_path)
    backup_dir = Path(backup_dir)
    if not database_path.exists():
        raise FileNotFoundError(f"Database not found: {database_path}")

    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"accounting-{datetime.now():%Y%m%d-%H%M%S}.db"
    shutil.copy2(database_path, target)

    token = (telegram_token if telegram_token is not None else os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
    destination = (
        chat_id if chat_id is not None else os.getenv("TELEGRAM_CHAT_ID", "")
    ).strip()
    proxy = (
        socks_proxy if socks_proxy is not None else os.getenv("SOCKS_PROXY", "")
    ).strip()

    if not token or not destination:
        return BackupResult(target=target, telegram_sent=False)

    proxies = {"http": proxy, "https": proxy} if proxy else None
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    try:
        with target.open("rb") as file:
            response = requests.post(
                url,
                data={"chat_id": destination},
                files={"document": (target.name, file, "application/octet-stream")},
                proxies=proxies,
                timeout=30,
            )
        response.raise_for_status()
        telegram_response = response.json()
        if telegram_response.get("ok") is False:
            raise BackupError(
                telegram_response.get("description", "Telegram rejected the backup")
            )
    except (requests.RequestException, ValueError) as exc:
        raise BackupError(f"Telegram upload failed: {exc}") from exc

    return BackupResult(target=target, telegram_sent=True)


def default_database_path() -> Path:
    data_dir = Path.home() / "Documents" / "Hesabyar"
    data_dir.mkdir(parents=True, exist_ok=True)
    database_path = data_dir / "accounting.db"
    if not database_path.exists():
        root = Path(__file__).resolve().parent
        previous_database_path = Path.home() / "Hesabyar" / "accounting.db"
        legacy_database_path = root / "data" / "accounting.db"
        source = next(
            (
                path
                for path in (previous_database_path, legacy_database_path)
                if path.exists()
            ),
            None,
        )
        if source:
            shutil.copy2(source, database_path)
    return database_path


if __name__ == "__main__":
    result = create_backup(
        database_path=default_database_path(),
        backup_dir=Path(__file__).resolve().parent / "backups",
    )
    print(f"Backup created: {result.target}")
    if result.telegram_sent:
        print("Backup sent to Telegram.")
