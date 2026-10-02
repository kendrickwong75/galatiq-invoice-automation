from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .. import db
from ..ingestion.normalize import dedupe_key
from ..models import Finding, Invoice, ReasonCode, Severity, ToolResult


def diff_invoices(prior: dict, current: Invoice) -> dict:
    """What changed between a previously processed copy and this one (by SKU quantity and total)."""
    def qty_by_sku(items):
        out = defaultdict(float)
        for li in items:
            out[li["item"]] += li["quantity"]
        return out

    before = qty_by_sku(prior.get("line_items", []))
    after = qty_by_sku([li.model_dump() for li in current.line_items])
    changes = []
    for sku in sorted(set(before) | set(after)):
        b, a = before.get(sku, 0), after.get(sku, 0)
        if b != a:
            changes.append({"item": sku, "before": b, "after": a})
    prior_total, total = prior.get("total"), current.total
    delta = round(total - prior_total, 2) if total is not None and prior_total is not None else None
    return {"line_changes": changes, "prior_total": prior_total, "new_total": total, "total_delta": delta,
            "identical": not changes and (delta in (None, 0))}


def check_duplicate(inv: Invoice, db_path: str | Path) -> ToolResult:
    """Block anything already paid or awaiting a human; re-evaluate resubmissions of rejected invoices."""
    key = dedupe_key(inv.vendor, inv.invoice_number)
    history = db.ledger_lookup(db_path, key)
    blocking = [r for r in history if r["status"] in ("PAID", "REVIEW")]
    if blocking:
        prior = blocking[-1]
        diff = diff_invoices(prior["invoice"] or {}, inv)
        kind = "identical copy" if diff["identical"] else "revised version"
        state = "already paid" if prior["status"] == "PAID" else "already awaiting human review"
        return ToolResult(
            findings=[Finding(code=ReasonCode.DUPLICATE, severity=Severity.REVIEW,
                              message=f"{kind.capitalize()} of {inv.invoice_number} from {prior['source_file']}, "
                                      f"which is {state}.",
                              data={"prior_source": prior["source_file"], "prior_status": prior["status"],
                                    "prior_payment_ref": prior.get("payment_ref"), "kind": kind, "diff": diff})],
            data={"dedupe_key": key, "history": [{k: r[k] for k in ("source_file", "status")} for r in history]},
        )
    findings = []
    if history:
        findings.append(Finding(code=ReasonCode.PREVIOUSLY_PROCESSED, severity=Severity.INFO,
                                message=f"Seen before ({', '.join(r['source_file'] + ': ' + r['status'] for r in history)}); "
                                        "re-evaluated from scratch because no copy was paid.",
                                data={"history": [r["source_file"] for r in history]}))
    return ToolResult(findings=findings, data={"dedupe_key": key})
