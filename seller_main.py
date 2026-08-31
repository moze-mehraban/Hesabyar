from __future__ import annotations

import ipaddress
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
STATIC_ROOT = ROOT / "seller_static"
PORT = 8001
REMOTE_PORT = 8000
remote_base_url = ""

app = FastAPI(title="حساب‌یار فروشنده", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")


class ConnectionInput(BaseModel):
    host: str = Field(min_length=1, max_length=255)


def normalize_host(value: str) -> str:
    host = value.strip()
    if "://" in host:
        parsed = urllib.parse.urlparse(host)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError
        host = parsed.hostname
        if parsed.port and parsed.port != REMOTE_PORT:
            raise ValueError
    elif host.count(":") == 1:
        candidate, port = host.rsplit(":", 1)
        if port.isdigit():
            if int(port) != REMOTE_PORT:
                raise ValueError
            host = candidate
    host = host.split("/", 1)[0].strip()
    if not host or any(char.isspace() for char in host):
        raise ValueError
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r"[A-Za-z0-9.-]+", host):
            raise ValueError
    return host


def require_connection() -> str:
    if not remote_base_url:
        raise HTTPException(status_code=409, detail="ابتدا IP سیستم مرکزی را وارد کنید")
    return remote_base_url


def remote_get(path: str) -> Any:
    base = require_connection()
    request = urllib.request.Request(f"{base}{path}", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", "خطا در سیستم مرکزی")
        except (json.JSONDecodeError, UnicodeDecodeError):
            detail = "خطا در سیستم مرکزی"
        raise HTTPException(status_code=exc.code, detail=detail) from exc
    except (OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail="اتصال به سیستم مرکزی برقرار نشد") from exc


def remote_json(path: str, method: str, payload: Any) -> Any:
    base = require_connection()
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", "خطا در سیستم مرکزی")
        except (json.JSONDecodeError, UnicodeDecodeError):
            detail = "خطا در سیستم مرکزی"
        raise HTTPException(status_code=exc.code, detail=detail) from exc
    except (OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail="اتصال به سیستم مرکزی برقرار نشد") from exc


@app.get("/")
def home() -> FileResponse:
    return FileResponse(STATIC_ROOT / "index.html")


@app.get("/api/connection")
def get_connection() -> dict[str, str]:
    return {"host": remote_base_url.removeprefix("http://").removesuffix(f":{REMOTE_PORT}") if remote_base_url else ""}


@app.post("/api/connection")
def set_connection(payload: ConnectionInput) -> dict[str, str]:
    global remote_base_url
    try:
        host = normalize_host(payload.host)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="IP یا نام سیستم معتبر نیست") from exc
    remote_base_url = f"http://{host}:{REMOTE_PORT}"
    # Check connectivity now so a typo is reported before the user enters the app.
    remote_get("/api/exchange-rate")
    return {"host": host}


@app.get("/api/seller-data")
def seller_data() -> dict[str, Any]:
    invoices = remote_get("/api/invoices")
    products = remote_get("/api/products")
    rate = remote_get("/api/exchange-rate")
    settings = remote_get("/api/exchange-rate/settings")
    return {
        "invoices": invoices,
        "products": products,
        "rate": rate,
        "store_name": settings.get("store_name", ""),
    }


@app.get("/api/invoices")
def list_seller_invoices() -> Any:
    return remote_get("/api/invoices")


@app.get("/api/products")
def list_seller_products() -> Any:
    return remote_get("/api/products")


@app.get("/api/dashboard")
def seller_dashboard() -> Any:
    return remote_get("/api/dashboard")


@app.get("/api/exchange-rate")
def seller_exchange_rate() -> Any:
    return remote_get("/api/exchange-rate")


@app.get("/api/exchange-rate/settings")
def seller_exchange_rate_settings() -> Any:
    return remote_get("/api/exchange-rate/settings")


@app.post("/api/invoices", status_code=201)
def create_invoice_from_seller(payload: dict[str, Any]) -> Any:
    return remote_json("/api/invoices", "POST", payload)


@app.put("/api/invoices/{invoice_id}")
def update_invoice_from_seller(invoice_id: int, payload: dict[str, Any]) -> Any:
    return remote_json(f"/api/invoices/{invoice_id}", "PUT", payload)


@app.post("/api/seller-invoices", status_code=201)
def create_seller_invoice(payload: dict[str, Any]) -> Any:
    return remote_json("/api/invoices", "POST", payload)
