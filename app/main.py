from __future__ import annotations

import json
import io
import os
import shutil
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> bool:
        return False
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backup import BackupError, create_backup

try:
    import qrcode
except ImportError:
    qrcode = None

load_dotenv()

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
    STATIC_ROOT = Path(getattr(sys, "_MEIPASS", ROOT)) / "app" / "static"
    FAVICON_ROOT = Path(getattr(sys, "_MEIPASS", ROOT)) / "app"
else:
    ROOT = Path(__file__).resolve().parent.parent
    STATIC_ROOT = Path(__file__).resolve().parent / "static"
    FAVICON_ROOT = Path(__file__).resolve().parent
APP_DATA_DIR = Path.home() / "Documents" / "Hesabyar"
DB_PATH = APP_DATA_DIR / "accounting.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
LEGACY_DB_PATH = ROOT / "data" / "accounting.db"
PREVIOUS_DB_PATH = Path.home() / "Hesabyar" / "accounting.db"
if not DB_PATH.exists():
    source_db = next(
        (path for path in (PREVIOUS_DB_PATH, LEGACY_DB_PATH) if path.exists()),
        None,
    )
    if source_db:
        shutil.copy2(source_db, DB_PATH)

app = FastAPI(title="حساب‌یار انبار", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize() -> None:
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                sku TEXT NOT NULL UNIQUE,
                quantity REAL NOT NULL DEFAULT 0,
                min_quantity REAL NOT NULL DEFAULT 0,
                purchase_price_irr REAL NOT NULL DEFAULT 0,
                dollar_price REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_number TEXT NOT NULL UNIQUE,
                customer_name TEXT NOT NULL DEFAULT '',
                discount_irr REAL NOT NULL DEFAULT 0,
                total_irr REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS invoice_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
                product_id INTEGER NOT NULL REFERENCES products(id),
                product_name TEXT NOT NULL,
                sku TEXT NOT NULL,
                quantity REAL NOT NULL,
                unit_price_irr REAL NOT NULL,
                unit_cost_irr REAL NOT NULL DEFAULT 0,
                total_irr REAL NOT NULL
            );
            """
        )
        if db.execute("SELECT 1 FROM settings WHERE key = 'usd_irr'").fetchone() is None:
            db.execute(
                "INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?)",
                ("usd_irr", "0", now()),
            )
        if db.execute("SELECT 1 FROM settings WHERE key = 'exchange_rate_api_url'").fetchone() is None:
            db.execute(
                "INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?)",
                ("exchange_rate_api_url", os.getenv("EXCHANGE_RATE_API_URL", ""), now()),
            )
        if db.execute("SELECT 1 FROM settings WHERE key = 'store_name'").fetchone() is None:
            db.execute(
                "INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?)",
                ("store_name", "", now()),
            )
        for key, environment_key in (
            ("backup_chat_id", "TELEGRAM_CHAT_ID"),
            ("backup_socks_proxy", "SOCKS_PROXY"),
        ):
            if db.execute("SELECT 1 FROM settings WHERE key = ?", (key,)).fetchone() is None:
                db.execute(
                    "INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?)",
                    (key, os.getenv(environment_key, ""), now()),
                )
        invoice_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(invoice_items)").fetchall()
        }
        if "unit_cost_irr" not in invoice_columns:
            db.execute(
                "ALTER TABLE invoice_items ADD COLUMN unit_cost_irr REAL NOT NULL DEFAULT 0"
            )


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProductInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    sku: str | None = Field(default=None, min_length=1, max_length=80)
    quantity: float = Field(default=0, ge=0)
    min_quantity: float = Field(default=0, ge=0)
    purchase_price_irr: float = Field(default=0, ge=0)
    dollar_price: float = Field(default=0, ge=0)


class RateInput(BaseModel):
    rate: float = Field(gt=0)


class ApiUrlInput(BaseModel):
    url: str | None = Field(default=None, max_length=1000)
    store_name: str | None = Field(default=None, max_length=200)


class BackupSettingsInput(BaseModel):
    chat_id: str | None = Field(default=None, max_length=200)
    socks_proxy: str | None = Field(default=None, max_length=500)


class InvoiceItemInput(BaseModel):
    product_id: int = Field(gt=0)
    quantity: float = Field(gt=0, multiple_of=1)
    unit_price_irr: float | None = Field(default=None, ge=0)


class InvoiceInput(BaseModel):
    customer_name: str = Field(default="", max_length=200)
    discount_irr: float = Field(default=0, ge=0)
    items: list[InvoiceItemInput] = Field(min_length=1)


def current_rate(db: sqlite3.Connection) -> float:
    row = db.execute("SELECT value FROM settings WHERE key = 'usd_irr'").fetchone()
    return float(row["value"]) if row else 0


def product_view(row: sqlite3.Row, rate: float) -> dict[str, Any]:
    item = dict(row)
    item["market_value_irr"] = round(item["quantity"] * item["dollar_price"] * rate)
    item["purchase_value_irr"] = round(item["quantity"] * item["purchase_price_irr"])
    item["difference_irr"] = item["market_value_irr"] - item["purchase_value_irr"]
    item["market_unit_price_irr"] = round(item["dollar_price"] * rate)
    item["difference_per_unit_irr"] = item["market_unit_price_irr"] - round(item["purchase_price_irr"])
    item["low_stock"] = item["quantity"] <= item["min_quantity"]
    item["barcode_value"] = item["sku"]
    return item


def next_sku(db: sqlite3.Connection) -> str:
    row = db.execute(
        "SELECT MAX(CAST(SUBSTR(sku, 3) AS INTEGER)) AS last_number "
        "FROM products WHERE sku LIKE 'K-%' AND SUBSTR(sku, 3) GLOB '[0-9]*'"
    ).fetchone()
    number = int(row["last_number"] or 0) + 1
    return f"K-{number:06d}"


@app.on_event("startup")
def startup() -> None:
    initialize()


@app.get("/")
def home() -> FileResponse:
    return FileResponse(STATIC_ROOT / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon_ico() -> FileResponse:
    return FileResponse(FAVICON_ROOT / "favicon.ico", media_type="image/x-icon")


@app.get("/favicon.png", include_in_schema=False)
def favicon_png() -> FileResponse:
    return FileResponse(FAVICON_ROOT / "favicon.png", media_type="image/png")


@app.get("/api/qrcode")
def product_qrcode(value: str) -> StreamingResponse:
    if qrcode is None:
        raise HTTPException(status_code=503, detail="کتابخانه QR نصب نشده است")
    image = qrcode.make(value)
    output = io.BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return StreamingResponse(output, media_type="image/png")


@app.get("/api/products")
def list_products() -> list[dict[str, Any]]:
    with connect() as db:
        rate = current_rate(db)
        rows = db.execute("SELECT * FROM products ORDER BY name COLLATE NOCASE").fetchall()
        return [product_view(row, rate) for row in rows]


@app.post("/api/products", status_code=201)
def create_product(payload: ProductInput) -> dict[str, Any]:
    timestamp = now()
    try:
        with connect() as db:
            sku = next_sku(db)
            cursor = db.execute(
                """
                INSERT INTO products
                (name, sku, quantity, min_quantity, purchase_price_irr, dollar_price, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (payload.name, sku, payload.quantity, payload.min_quantity,
                 payload.purchase_price_irr, payload.dollar_price, timestamp, timestamp),
            )
            row = db.execute("SELECT * FROM products WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return product_view(row, current_rate(db))
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="کد کالا تکراری است")


@app.put("/api/products/{product_id}")
def update_product(product_id: int, payload: ProductInput) -> dict[str, Any]:
    with connect() as db:
        existing = db.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="کالا پیدا نشد")
        try:
            db.execute(
                """
                UPDATE products SET name=?, quantity=?, min_quantity=?,
                purchase_price_irr=?, dollar_price=?, updated_at=? WHERE id=?
                """,
                (payload.name, payload.quantity, payload.min_quantity,
                 payload.purchase_price_irr, payload.dollar_price, now(), product_id),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="کد کالا تکراری است")
        row = db.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        return product_view(row, current_rate(db))


@app.delete("/api/products/{product_id}")
def delete_product(product_id: int) -> dict[str, bool]:
    with connect() as db:
        cursor = db.execute("DELETE FROM products WHERE id = ?", (product_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="کالا پیدا نشد")
    return {"deleted": True}


@app.get("/api/invoices")
def list_invoices() -> list[dict[str, Any]]:
    with connect() as db:
        invoices = db.execute("SELECT * FROM invoices ORDER BY id DESC").fetchall()
        result = []
        for invoice in invoices:
            items = db.execute(
                "SELECT * FROM invoice_items WHERE invoice_id = ? ORDER BY id",
                (invoice["id"],),
            ).fetchall()
            result.append({**dict(invoice), "items": [dict(item) for item in items]})
        return result


@app.post("/api/invoices", status_code=201)
def create_invoice(payload: InvoiceInput) -> dict[str, Any]:
    timestamp = now()
    with connect() as db:
        rate = current_rate(db)
        prepared: list[dict[str, Any]] = []
        gross_total = 0.0
        requested_totals: dict[int, float] = {}
        for requested in payload.items:
            requested_totals[requested.product_id] = (
                requested_totals.get(requested.product_id, 0) + requested.quantity
            )
        for requested in payload.items:
            product = db.execute(
                "SELECT * FROM products WHERE id = ?", (requested.product_id,)
            ).fetchone()
            if not product:
                raise HTTPException(status_code=404, detail="یکی از کالاها پیدا نشد")
            if product["quantity"] < requested_totals[requested.product_id]:
                raise HTTPException(
                    status_code=400,
                    detail=f"موجودی «{product['name']}» کافی نیست",
                )
            unit_price = (
                requested.unit_price_irr
                if requested.unit_price_irr is not None
                else product["dollar_price"] * rate
            )
            line_total = requested.quantity * unit_price
            gross_total += line_total
            prepared.append(
                {
                    "product": product,
                    "quantity": requested.quantity,
                    "unit_price": unit_price,
                    "total": line_total,
                }
            )
        total = max(0, gross_total - payload.discount_irr)
        today = datetime.now().strftime("%Y%m%d")
        count = db.execute(
            "SELECT COUNT(*) AS count FROM invoices WHERE invoice_number LIKE ?",
            (f"INV-{today}-%",),
        ).fetchone()["count"]
        invoice_number = f"INV-{today}-{count + 1:04d}"
        cursor = db.execute(
            """
            INSERT INTO invoices(invoice_number, customer_name, discount_irr, total_irr, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (invoice_number, payload.customer_name, payload.discount_irr, total, timestamp),
        )
        invoice_id = cursor.lastrowid
        for item in prepared:
            product = item["product"]
            db.execute(
                """
                INSERT INTO invoice_items
                (invoice_id, product_id, product_name, sku, quantity, unit_price_irr, unit_cost_irr, total_irr)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    invoice_id,
                    product["id"],
                    product["name"],
                    product["sku"],
                    item["quantity"],
                    item["unit_price"],
                    product["purchase_price_irr"],
                    item["total"],
                ),
            )
            db.execute(
                "UPDATE products SET quantity = quantity - ?, updated_at = ? WHERE id = ?",
                (item["quantity"], timestamp, product["id"]),
            )
        invoice = db.execute(
            "SELECT * FROM invoices WHERE id = ?", (invoice_id,)
        ).fetchone()
        items = db.execute(
            "SELECT * FROM invoice_items WHERE invoice_id = ?", (invoice_id,)
        ).fetchall()
        return {**dict(invoice), "items": [dict(item) for item in items]}


@app.put("/api/invoices/{invoice_id}")
def update_invoice(invoice_id: int, payload: InvoiceInput) -> dict[str, Any]:
    timestamp = now()
    with connect() as db:
        rate = current_rate(db)
        invoice = db.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if not invoice:
            raise HTTPException(status_code=404, detail="فاکتور پیدا نشد")

        old_items = db.execute(
            "SELECT product_id, quantity FROM invoice_items WHERE invoice_id = ?",
            (invoice_id,),
        ).fetchall()
        for old in old_items:
            db.execute(
                "UPDATE products SET quantity = quantity + ?, updated_at = ? WHERE id = ?",
                (old["quantity"], timestamp, old["product_id"]),
            )

        prepared: list[dict[str, Any]] = []
        gross_total = 0.0
        requested_totals: dict[int, float] = {}
        for requested in payload.items:
            requested_totals[requested.product_id] = (
                requested_totals.get(requested.product_id, 0) + requested.quantity
            )
        for requested in payload.items:
            product = db.execute(
                "SELECT * FROM products WHERE id = ?", (requested.product_id,)
            ).fetchone()
            if not product:
                raise HTTPException(status_code=404, detail="یکی از کالاها پیدا نشد")
            if product["quantity"] < requested_totals[requested.product_id]:
                raise HTTPException(status_code=400, detail=f"موجودی «{product['name']}» کافی نیست")
            unit_price = (
                requested.unit_price_irr
                if requested.unit_price_irr is not None
                else product["dollar_price"] * rate
            )
            line_total = requested.quantity * unit_price
            gross_total += line_total
            prepared.append({"product": product, "quantity": requested.quantity, "unit_price": unit_price, "total": line_total})

        total = max(0, gross_total - payload.discount_irr)
        db.execute("DELETE FROM invoice_items WHERE invoice_id = ?", (invoice_id,))
        db.execute(
            "UPDATE invoices SET customer_name=?, discount_irr=?, total_irr=? WHERE id=?",
            (payload.customer_name, payload.discount_irr, total, invoice_id),
        )
        for item in prepared:
            product = item["product"]
            db.execute(
                """
                INSERT INTO invoice_items
                (invoice_id, product_id, product_name, sku, quantity, unit_price_irr, unit_cost_irr, total_irr)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (invoice_id, product["id"], product["name"], product["sku"],
                 item["quantity"], item["unit_price"], product["purchase_price_irr"], item["total"]),
            )
            db.execute(
                "UPDATE products SET quantity = quantity - ?, updated_at = ? WHERE id = ?",
                (item["quantity"], timestamp, product["id"]),
            )
        updated = db.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        items = db.execute("SELECT * FROM invoice_items WHERE invoice_id = ?", (invoice_id,)).fetchall()
        return {**dict(updated), "items": [dict(item) for item in items]}


@app.get("/api/exchange-rate")
def get_exchange_rate() -> dict[str, Any]:
    with connect() as db:
        row = db.execute(
            "SELECT value, updated_at FROM settings WHERE key = 'usd_irr'"
        ).fetchone()
        return {"rate": float(row["value"]), "updated_at": row["updated_at"]}


@app.post("/api/exchange-rate")
def set_exchange_rate(payload: RateInput) -> dict[str, Any]:
    with connect() as db:
        db.execute(
            "UPDATE settings SET value=?, updated_at=? WHERE key='usd_irr'",
            (str(payload.rate), now()),
        )
    return get_exchange_rate()


@app.get("/api/exchange-rate/settings")
def get_exchange_rate_settings() -> dict[str, str]:
    with connect() as db:
        api_row = db.execute(
            "SELECT value FROM settings WHERE key = 'exchange_rate_api_url'"
        ).fetchone()
        store_row = db.execute(
            "SELECT value FROM settings WHERE key = 'store_name'"
        ).fetchone()
        return {
            "url": api_row["value"] if api_row else "",
            "store_name": store_row["value"] if store_row else "",
        }


@app.post("/api/exchange-rate/settings")
def set_exchange_rate_settings(payload: ApiUrlInput) -> dict[str, str]:
    with connect() as db:
        if payload.url is not None:
            db.execute(
                "UPDATE settings SET value=?, updated_at=? WHERE key='exchange_rate_api_url'",
                (payload.url.strip(), now()),
            )
        if payload.store_name is not None:
            db.execute(
                "UPDATE settings SET value=?, updated_at=? WHERE key='store_name'",
                (payload.store_name.strip(), now()),
            )
    return get_exchange_rate_settings()


@app.get("/api/backup/settings")
def get_backup_settings() -> dict[str, str]:
    with connect() as db:
        rows = db.execute(
            "SELECT key, value FROM settings "
            "WHERE key IN ('backup_chat_id', 'backup_socks_proxy')"
        ).fetchall()
    values = {row["key"]: row["value"] for row in rows}
    return {
        "chat_id": values.get("backup_chat_id", ""),
        "socks_proxy": values.get("backup_socks_proxy", ""),
    }


@app.post("/api/backup/settings")
def set_backup_settings(payload: BackupSettingsInput) -> dict[str, str]:
    with connect() as db:
        if payload.chat_id is not None:
            db.execute(
                "UPDATE settings SET value=?, updated_at=? "
                "WHERE key='backup_chat_id'",
                (payload.chat_id.strip(), now()),
            )
        if payload.socks_proxy is not None:
            db.execute(
                "UPDATE settings SET value=?, updated_at=? "
                "WHERE key='backup_socks_proxy'",
                (payload.socks_proxy.strip(), now()),
            )
    return get_backup_settings()


@app.post("/api/backup")
def run_backup() -> dict[str, Any]:
    settings = get_backup_settings()
    try:
        result = create_backup(
            database_path=DB_PATH,
            backup_dir=ROOT / "backups",
            telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            chat_id=settings["chat_id"],
            socks_proxy=settings["socks_proxy"],
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="فایل دیتابیس پیدا نشد")
    except BackupError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "filename": result.target.name,
        "telegram_sent": result.telegram_sent,
    }


@app.post("/api/exchange-rate/refresh")
def refresh_exchange_rate() -> dict[str, Any]:
    api_url = get_exchange_rate_settings()["url"].strip() or os.getenv("EXCHANGE_RATE_API_URL", "").strip()
    if not api_url:
        raise HTTPException(status_code=400, detail="آدرس API نرخ ارز تنظیم نشده است")
    try:
        with urllib.request.urlopen(api_url, timeout=100) as response:
            data = json.loads(response.read().decode("utf-8"))
        candidates = []
        if isinstance(data.get("usd"), dict) and "value" in data["usd"]:
            candidates.append(data["usd"]["value"])
        candidates.extend(data[key] for key in ("rate", "price", "value", "usd_irr") if key in data)
        value = next(iter(candidates), None)
        rate = float(value)*10
        if rate <= 0:
            raise ValueError
    except (OSError, ValueError, TypeError, json.JSONDecodeError, StopIteration):
        raise HTTPException(status_code=502, detail="دریافت نرخ ارز از API ناموفق بود")
    return set_exchange_rate(RateInput(rate=rate))


@app.get("/api/dashboard")
def dashboard() -> dict[str, Any]:
    products = list_products()
    with connect() as db:
        weekly = db.execute(
            """
            WITH invoice_totals AS (
                SELECT
                    i.id,
                    i.total_irr,
                    COALESCE(SUM(ii.quantity * CASE
                        WHEN ii.unit_cost_irr > 0 THEN ii.unit_cost_irr
                        ELSE COALESCE(p.purchase_price_irr, 0)
                    END), 0) AS cost_irr
                FROM invoices i
                LEFT JOIN invoice_items ii ON ii.invoice_id = i.id
                LEFT JOIN products p ON p.id = ii.product_id
                WHERE datetime(i.created_at) >= datetime('now', '-7 days')
                GROUP BY i.id
            )
            SELECT
                COUNT(*) AS invoice_count,
                COALESCE(SUM(total_irr), 0) AS sales_irr,
                COALESCE(SUM(total_irr - cost_irr), 0) AS profit_irr
            FROM invoice_totals
            """
        ).fetchone()
    return {
        "product_count": len(products),
        "total_quantity": sum(item["quantity"] for item in products),
        "purchase_total_irr": sum(item["purchase_value_irr"] for item in products),
        "market_total_irr": sum(item["market_value_irr"] for item in products),
        "difference_total_irr": sum(item["difference_irr"] for item in products),
        "low_stock_count": sum(item["low_stock"] for item in products),
        "weekly_invoice_count": weekly["invoice_count"],
        "weekly_sales_irr": round(weekly["sales_irr"]),
        "weekly_profit_irr": round(weekly["profit_irr"]),
    }
