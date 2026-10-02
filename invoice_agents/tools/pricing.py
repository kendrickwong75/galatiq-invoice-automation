from __future__ import annotations

from pathlib import Path

from .. import config, db
from ..models import Finding, Invoice, ReasonCode, Severity, ToolResult
from .fx import fx_rate


def check_catalog_prices(inv: Invoice, db_path: str | Path) -> ToolResult:
    """Flag unit prices more than the tolerance above catalog (after FX). Discounts are not flagged.

    This is an extension: the README's inventory spec has no prices, so this is a FLAG, never a reject.
    """
    rate = fx_rate(inv.currency)
    if rate is None:
        return ToolResult(data={"skipped": "unknown currency"})
    findings = []
    for li in inv.line_items:
        row = db.get_item(db_path, li.item)
        if not row or row["unit_price"] is None or li.unit_price is None:
            continue
        price_usd = round(li.unit_price * rate, 2)
        limit = row["unit_price"] * (1 + config.PRICE_VARIANCE_TOLERANCE)
        if price_usd > limit:
            pct = price_usd / row["unit_price"] - 1
            note = f" ({li.note})" if li.note else ""
            findings.append(Finding(code=ReasonCode.PRICE_VARIANCE, severity=Severity.FLAG,
                                    message=f"'{li.item}'{note} billed at ${price_usd:,.2f}, "
                                            f"{pct:+.0%} vs catalog ${row['unit_price']:,.2f}.",
                                    data={"item": li.item, "unit_price_usd": price_usd,
                                          "catalog": row["unit_price"], "variance": round(pct, 4)}))
    return ToolResult(findings=findings)
