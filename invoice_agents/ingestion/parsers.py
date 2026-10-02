"""Deterministic parsers for structured formats (JSON, CSV, XML).

Structured files don't need an LLM to read them: parsing them in code is faster, free and exact. The LLM
path is the fallback when a structured file doesn't match any known layout.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

from ..models import Charge, Invoice, LineItem
from .loaders import RawDocument
from .normalize import parse_money


class ParseError(Exception):
    pass


def parse_structured(doc: RawDocument) -> Invoice:
    try:
        if doc.fmt == "json":
            return _parse_json(doc.data)
        if doc.fmt == "csv":
            return _parse_csv(doc.data)
        if doc.fmt == "xml":
            return _parse_xml(doc.data)
    except ParseError:
        raise
    except Exception as exc:  # malformed structure -> let the LLM try
        raise ParseError(f"{doc.fmt} parse failed: {exc!r}") from exc
    raise ParseError(f"no deterministic parser for {doc.fmt}")


def _tax_rate_from_label(label: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", label)
    return float(m.group(1)) / 100 if m else None


# --- JSON ------------------------------------------------------------------


def _parse_json(data: Any) -> Invoice:
    if not isinstance(data, dict) or "line_items" not in data:
        raise ParseError("JSON lacks line_items")
    vendor = data.get("vendor")
    vendor_name, vendor_address = (
        (vendor.get("name"), vendor.get("address")) if isinstance(vendor, dict) else (vendor, None)
    )
    items = [
        LineItem(
            item=str(li.get("item") or li.get("name") or ""),
            quantity=float(parse_money(li.get("quantity")) or 0),
            unit_price=parse_money(li.get("unit_price")),
            amount=parse_money(li.get("amount")),
            note=li.get("note"),
        )
        for li in data["line_items"]
    ]
    notes = data.get("notes")
    if data.get("revision"):
        notes = f"Revision {data['revision']}. {notes or ''}".strip()
    return Invoice(
        invoice_number=data.get("invoice_number"),
        vendor=vendor_name,
        vendor_address=vendor_address,
        invoice_date=data.get("date") or data.get("invoice_date"),
        due_date=data.get("due_date"),
        currency=data.get("currency") or "USD",
        line_items=items,
        subtotal=parse_money(data.get("subtotal")),
        tax_rate=parse_money(data.get("tax_rate")),
        tax_amount=parse_money(data.get("tax_amount") if "tax_amount" in data else data.get("tax")),
        total=parse_money(data.get("total")),
        payment_terms=data.get("payment_terms") or None,
        notes=notes,
    )


# --- CSV -------------------------------------------------------------------


def _parse_csv(rows: list[list[str]]) -> Invoice:
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        raise ParseError("empty CSV")
    header = [c.strip().lower() for c in rows[0]]
    if header[:2] == ["field", "value"]:
        return _parse_csv_key_value(rows[1:])
    if "item" in header and ("qty" in header or "quantity" in header):
        return _parse_csv_rows(header, rows[1:])
    raise ParseError(f"unrecognised CSV header {rows[0]}")


def _parse_csv_key_value(rows: list[list[str]]) -> Invoice:
    """Layout: field,value pairs; each line item is a repeated item/quantity/unit_price group (INV-1006)."""
    fields: dict[str, str] = {}
    items: list[dict[str, str]] = []
    for row in rows:
        key, value = row[0].strip().lower(), (row[1].strip() if len(row) > 1 else "")
        if key == "item":
            items.append({"item": value})
        elif key in {"quantity", "qty", "unit_price", "amount"} and items:
            items[-1][key] = value
        else:
            fields[key] = value
    return Invoice(
        invoice_number=fields.get("invoice_number"),
        vendor=fields.get("vendor"),
        invoice_date=fields.get("date") or fields.get("invoice_date"),
        due_date=fields.get("due_date"),
        currency=fields.get("currency") or "USD",
        line_items=[
            LineItem(
                item=i["item"],
                quantity=float(parse_money(i.get("quantity") or i.get("qty")) or 0),
                unit_price=parse_money(i.get("unit_price")),
                amount=parse_money(i.get("amount")),
            )
            for i in items
        ],
        subtotal=parse_money(fields.get("subtotal")),
        tax_rate=parse_money(fields.get("tax_rate")),
        tax_amount=parse_money(fields.get("tax") or fields.get("tax_amount")),
        total=parse_money(fields.get("total")),
        payment_terms=fields.get("payment_terms"),
    )


def _parse_csv_rows(header: list[str], rows: list[list[str]]) -> Invoice:
    """Layout: one row per line item, then summary rows with a 'Label:' cell and a value (INV-1007/1015)."""
    col = {name: i for i, name in enumerate(header)}

    def cell(row: list[str], *names: str) -> str:
        for n in names:
            if n in col and col[n] < len(row) and row[col[n]].strip():
                return row[col[n]].strip()
        return ""

    items, summary, first = [], {}, None
    for row in rows:
        item_name = cell(row, "item", "description")
        if item_name:
            first = first or row
            items.append(
                LineItem(
                    item=item_name,
                    quantity=float(parse_money(cell(row, "qty", "quantity")) or 0),
                    unit_price=parse_money(cell(row, "unit price", "unit_price", "price")),
                    amount=parse_money(cell(row, "line total", "amount", "total")),
                )
            )
            continue
        cells = [c.strip() for c in row]
        for i, c in enumerate(cells):
            if c.endswith(":") and i + 1 < len(cells):
                summary[c[:-1].strip().lower()] = (c, cells[i + 1])
    if first is None:
        raise ParseError("CSV has no line items")

    tax_label, tax_value = next(((lbl, v) for k, (lbl, v) in summary.items() if k.startswith("tax")), (None, None))
    total = next((v for k, (_, v) in summary.items() if "total" in k and "sub" not in k), None)
    return Invoice(
        invoice_number=cell(first, "invoice number", "invoice_number", "invoice"),
        vendor=cell(first, "vendor"),
        invoice_date=cell(first, "date", "invoice date"),
        due_date=cell(first, "due date", "due_date") or None,
        currency=cell(first, "currency") or "USD",
        line_items=items,
        subtotal=parse_money(summary.get("subtotal", (None, None))[1]),
        tax_rate=_tax_rate_from_label(tax_label) if tax_label else None,
        tax_amount=parse_money(tax_value),
        total=parse_money(total),
    )


# --- XML -------------------------------------------------------------------


def _parse_xml(root: ET.Element) -> Invoice:
    def text(tag: str) -> str | None:
        el = root.find(f".//{tag}")
        return el.text.strip() if el is not None and el.text else None

    items = []
    for el in root.findall(".//line_items/item"):
        items.append(
            LineItem(
                item=(el.findtext("name") or el.findtext("item") or "").strip(),
                quantity=float(parse_money(el.findtext("quantity")) or 0),
                unit_price=parse_money(el.findtext("unit_price")),
                amount=parse_money(el.findtext("amount")),
            )
        )
    if not items:
        raise ParseError("XML has no line_items/item elements")
    charges = [Charge(label=lbl, amount=v) for lbl in ("shipping", "handling") if (v := parse_money(text(lbl)))]
    return Invoice(
        invoice_number=text("invoice_number"),
        vendor=text("vendor") or text("vendor_name"),
        invoice_date=text("date") or text("invoice_date"),
        due_date=text("due_date"),
        currency=text("currency") or "USD",
        line_items=items,
        subtotal=parse_money(text("subtotal")),
        tax_rate=parse_money(text("tax_rate")),
        tax_amount=parse_money(text("tax_amount")),
        other_charges=charges,
        total=parse_money(text("total")),
        payment_terms=text("payment_terms"),
    )
