"""Clean-up for messy values: OCR artefacts, money strings, date formats, product names."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Iterable

from ..models import Invoice

_OCR_O_NEAR_DIGIT = re.compile(r"(?<=\d)[Oo]|[Oo](?=\d)")
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%d-%b-%Y",
    "%d-%B-%Y",
    "%b %d %Y",
    "%b %d, %Y",
    "%B %d %Y",
    "%B %d, %Y",
    "%d %b %Y",
    "%d %B %Y",
)
_VENDOR_SUFFIXES = re.compile(r"\b(inc|incorporated|ltd|limited|llc|co|corp|corporation|company|gmbh)\b\.?")


def fix_ocr_digits(text: str) -> str:
    """'2O26' -> '2026', '$3,500.O0' -> '$3,500.00'. Only touches O/o next to a digit."""
    return _OCR_O_NEAR_DIGIT.sub("0", text)


def parse_money(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = fix_ocr_digits(str(value)).replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(m.group()) if m else None


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    text = re.sub(r"\s+", " ", fix_ocr_digits(str(value)).strip())
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def to_iso_date(value: Any) -> str | None:
    """ISO date if parseable, otherwise the original text (so 'yesterday' survives for the fraud check)."""
    if value is None or str(value).strip() == "":
        return None
    parsed = parse_date(value)
    return parsed.isoformat() if parsed else str(value).strip()


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def normalize_sku(name: str, known_skus: Iterable[str]) -> str:
    """'Widget A' -> 'WidgetA', 'WidgetA (rush order)' -> 'WidgetA'; unknown names are just de-spaced."""
    base = re.sub(r"\(.*?\)", "", name).strip()
    compact = _compact(base)
    for sku in known_skus:
        if compact == _compact(sku):
            return sku
    return re.sub(r"\s+", "", base) or name.strip()


def normalize_vendor_key(vendor: str | None) -> str:
    if not vendor:
        return "unknown-vendor"
    text = re.sub(r"\(.*?\)", "", vendor.lower())
    text = _VENDOR_SUFFIXES.sub("", text)
    return re.sub(r"[^a-z0-9]", "", text) or "unknown-vendor"


def normalize_invoice_number(number: str | None) -> str:
    if not number:
        return "unknown-number"
    text = re.sub(r"[^A-Z0-9]", "", fix_ocr_digits(number).upper())
    return re.sub(r"^INV(OICE)?(NO)?", "", text) or text


def dedupe_key(vendor: str | None, invoice_number: str | None) -> str:
    return f"{normalize_vendor_key(vendor)}|{normalize_invoice_number(invoice_number)}"


def normalize_invoice(inv: Invoice, known_skus: Iterable[str]) -> Invoice:
    """Canonical form used by every downstream check, whichever extractor produced it."""
    known = list(known_skus)
    inv = inv.model_copy(deep=True)

    for li in inv.line_items:
        sku = normalize_sku(li.item, known)
        if sku != li.item.strip():
            li.description = li.description or li.item.strip()
        if not li.note:
            m = re.search(r"\((.*?)\)", li.item)
            li.note = m.group(1) if m else None
        li.item = sku

    if inv.vendor:
        vendor = inv.vendor.strip()
        m = re.search(r"\((formerly|previously|fka)[^)]*\)", vendor, re.I)
        if m:
            inv.notes = f"{inv.notes or ''} {m.group(0)}".strip()
            vendor = vendor.replace(m.group(0), "").strip()
        inv.vendor = vendor
    if inv.invoice_number:
        inv.invoice_number = fix_ocr_digits(inv.invoice_number.strip())
    inv.invoice_date = to_iso_date(inv.invoice_date)
    inv.due_date = to_iso_date(inv.due_date)
    inv.currency = (inv.currency or "USD").strip().upper()
    if inv.tax_rate is not None and inv.tax_rate > 1:
        inv.tax_rate = inv.tax_rate / 100
    return inv
