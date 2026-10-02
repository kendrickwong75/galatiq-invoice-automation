from __future__ import annotations

import re

from .. import config
from ..ingestion.normalize import parse_date
from ..models import Finding, Invoice, ReasonCode, Severity, ToolResult
from .fx import convert_to_usd

_URGENCY = re.compile(r"\burgent\b|immediately|avoid penalt|final notice|act now|within 24 hours", re.I)
_PAYMENT_CHANNEL = re.compile(r"wire transfer|western union|gift card|bitcoin|crypto", re.I)
_SUSPICIOUS_VENDOR = re.compile(r"fraud|scam|fake|phish|test vendor", re.I)
_RENAMED = re.compile(r"\b(formerly|previously known as|fka|f/k/a)\b", re.I)
_NET_TERMS = re.compile(r"net\s*(\d+)", re.I)


def _strong(signal: str, message: str) -> Finding:
    return Finding(code=ReasonCode.FRAUD_SIGNAL, severity=Severity.FLAG, message=message,
                   data={"signal": signal, "strong": True})


def fraud_signals(inv: Invoice, raw_text: str) -> ToolResult:
    """Heuristic red flags. Individually they are FLAGs for the approval agent; policy rejects at 2+ strong ones."""
    findings: list[Finding] = []
    text = raw_text or ""

    if m := _URGENCY.search(text):
        findings.append(_strong("urgent_language", f"Pressure language: '{m.group(0)}'."))
    if m := _PAYMENT_CHANNEL.search(text):
        findings.append(_strong("unusual_payment_channel", f"Requests payment via '{m.group(0)}'."))
    if inv.vendor and (m := _SUSPICIOUS_VENDOR.search(inv.vendor)):
        findings.append(_strong("suspicious_vendor_name", f"Vendor name '{inv.vendor}' contains '{m.group(0)}'."))

    invoice_day, due_day = parse_date(inv.invoice_date), parse_date(inv.due_date)
    if inv.due_date and due_day is None:
        findings.append(_strong("invalid_due_date", f"Due date '{inv.due_date}' is not a calendar date."))
    elif invoice_day and due_day and due_day < invoice_day:
        findings.append(_strong("due_before_issue", f"Due date {due_day} is before invoice date {invoice_day}."))
    elif invoice_day and due_day and due_day == invoice_day:
        terms = _NET_TERMS.search(inv.payment_terms or "")
        if terms and int(terms.group(1)) > 0:
            findings.append(Finding(code=ReasonCode.DUE_DATE_INCONSISTENT, severity=Severity.FLAG,
                                    message=f"Due on the invoice date despite '{inv.payment_terms}' terms.",
                                    data={"terms": inv.payment_terms}))

    if m := _RENAMED.search(f"{inv.vendor or ''} {inv.notes or ''} {text}"):
        findings.append(Finding(code=ReasonCode.VENDOR_RENAMED, severity=Severity.FLAG,
                                message=f"Vendor indicates a name change ('{m.group(0)}'); confirm bank details "
                                        "match the vendor on file.", data={}))

    amount_usd = convert_to_usd(inv).data.get("amount_usd")
    threshold = config.VP_REVIEW_THRESHOLD_USD
    if amount_usd and threshold * config.NEAR_THRESHOLD_BAND <= amount_usd <= threshold:
        findings.append(Finding(code=ReasonCode.NEAR_THRESHOLD, severity=Severity.FLAG,
                                message=f"${amount_usd:,.2f} is just under the ${threshold:,.0f} VP-review "
                                        "threshold (possible threshold avoidance).",
                                data={"amount_usd": amount_usd}))
    return ToolResult(findings=findings,
                      data={"strong_signals": sum(1 for f in findings if f.data.get("strong"))})
