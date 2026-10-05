"""SQLite inventory (simulated ERP) and processed-invoice ledger.

The inventory is read-only at runtime: paying an invoice does not decrement stock, so each provided test
invoice is validated against the stock levels the case brief specifies, regardless of processing order.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory (
    item        TEXT PRIMARY KEY,
    stock       INTEGER NOT NULL,
    unit_price  REAL            -- catalog price; extension beyond the case brief's spec
);
CREATE TABLE IF NOT EXISTS processed_invoices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key      TEXT NOT NULL,
    vendor          TEXT,
    invoice_number  TEXT,
    source_file     TEXT NOT NULL,
    status          TEXT NOT NULL,   -- PAID | REJECTED | REVIEW
    amount_usd      REAL,
    payment_ref     TEXT,
    invoice_json    TEXT,
    decided_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_processed_key ON processed_invoices(dedupe_key);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str | Path = config.DEFAULT_DB_PATH, reset: bool = False) -> Path:
    """Create and seed the database. Idempotent; reset=True drops inventory and ledger first."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(db_path)) as conn, conn:
        if reset:
            conn.executescript("DROP TABLE IF EXISTS inventory; DROP TABLE IF EXISTS processed_invoices;")
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT OR IGNORE INTO inventory (item, stock, unit_price) VALUES (?, ?, ?)",
            config.INVENTORY_SEED,
        )
    return db_path


def get_item(db_path: str | Path, sku: str) -> dict[str, Any] | None:
    with closing(connect(db_path)) as conn:
        row = conn.execute("SELECT item, stock, unit_price FROM inventory WHERE item = ?", (sku,)).fetchone()
    return dict(row) if row else None


def list_skus(db_path: str | Path) -> list[str]:
    with closing(connect(db_path)) as conn:
        return [r["item"] for r in conn.execute("SELECT item FROM inventory ORDER BY item")]


def ledger_lookup(db_path: str | Path, dedupe_key: str) -> list[dict[str, Any]]:
    """All prior decisions for this vendor + invoice number, oldest first."""
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM processed_invoices WHERE dedupe_key = ? ORDER BY id", (dedupe_key,)
        ).fetchall()
    out = []
    for r in rows:
        rec = dict(r)
        rec["invoice"] = json.loads(rec.pop("invoice_json")) if rec.get("invoice_json") else None
        out.append(rec)
    return out


def ledger_record(
    db_path: str | Path,
    *,
    dedupe_key: str,
    source_file: str,
    status: str,
    vendor: str | None,
    invoice_number: str | None,
    amount_usd: float | None,
    invoice: dict | None,
    payment_ref: str | None = None,
) -> None:
    with closing(connect(db_path)) as conn, conn:
        conn.execute(
            "INSERT INTO processed_invoices (dedupe_key, vendor, invoice_number, source_file, status, amount_usd,"
            " payment_ref, invoice_json, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                dedupe_key,
                vendor,
                invoice_number,
                source_file,
                status,
                amount_usd,
                payment_ref,
                json.dumps(invoice) if invoice is not None else None,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
