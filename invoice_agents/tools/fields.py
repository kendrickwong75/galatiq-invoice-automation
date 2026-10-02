from __future__ import annotations

from ..models import Finding, Invoice, ReasonCode, Severity, ToolResult


def check_required_fields(inv: Invoice) -> ToolResult:
    """Vendor, invoice number, line items and an amount are mandatory; a missing due date is a flag."""
    findings = []
    for field, label in (("vendor", "vendor name"), ("invoice_number", "invoice number")):
        if not (getattr(inv, field) or "").strip():
            findings.append(Finding(code=ReasonCode.MISSING_FIELD, severity=Severity.REJECT,
                                    message=f"Missing {label}; cannot identify who to pay.",
                                    data={"field": field}))
    if not inv.line_items:
        findings.append(Finding(code=ReasonCode.MISSING_FIELD, severity=Severity.REJECT,
                                message="No line items; nothing to validate against inventory.",
                                data={"field": "line_items"}))
    if inv.total is None and not inv.line_items:
        findings.append(Finding(code=ReasonCode.MISSING_FIELD, severity=Severity.REJECT,
                                message="No total amount and no line items to compute one from.",
                                data={"field": "total"}))
    if not inv.due_date:
        findings.append(Finding(code=ReasonCode.MISSING_FIELD, severity=Severity.FLAG,
                                message="No due date on the invoice.", data={"field": "due_date"}))
    if not (inv.payment_terms or "").strip():
        findings.append(Finding(code=ReasonCode.MISSING_FIELD, severity=Severity.INFO,
                                message="No payment terms stated.", data={"field": "payment_terms"}))
    return ToolResult(findings=findings)
