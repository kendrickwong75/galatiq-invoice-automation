from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .. import db
from ..models import Finding, Invoice, ReasonCode, Severity, ToolResult


def check_inventory(inv: Invoice, db_path: str | Path) -> ToolResult:
    """Compare requested quantities with stock, summing repeated SKUs first.

    Aggregation matters: INV-1013 lists WidgetA on three lines (15 + 5 + 2). Each line fits the 15 in
    stock on its own, but the invoice as a whole bills for 22.
    """
    requested: dict[str, float] = defaultdict(float)
    for li in inv.line_items:
        requested[li.item] += li.quantity

    findings, checked = [], {}
    for sku, qty in requested.items():
        row = db.get_item(db_path, sku)
        checked[sku] = {"requested": qty, "in_stock": row["stock"] if row else None}
        if row is None:
            findings.append(Finding(code=ReasonCode.UNKNOWN_ITEM, severity=Severity.REJECT,
                                    message=f"'{sku}' is not in the inventory system.",
                                    data={"item": sku, "requested": qty}))
        elif row["stock"] <= 0:
            findings.append(Finding(code=ReasonCode.OUT_OF_STOCK, severity=Severity.REJECT,
                                    message=f"'{sku}' has zero stock but {qty:g} units are billed.",
                                    data={"item": sku, "requested": qty, "in_stock": row["stock"]}))
        elif qty > row["stock"]:
            lines = sum(1 for li in inv.line_items if li.item == sku)
            across = f" across {lines} lines" if lines > 1 else ""
            findings.append(Finding(code=ReasonCode.STOCK_EXCEEDED, severity=Severity.REJECT,
                                    message=f"'{sku}': {qty:g} units billed{across}, only {row['stock']} in stock.",
                                    data={"item": sku, "requested": qty, "in_stock": row["stock"], "lines": lines}))
    return ToolResult(findings=findings, data={"items": checked})
