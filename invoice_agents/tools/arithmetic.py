from __future__ import annotations

from .. import config
from ..models import Finding, Invoice, ReasonCode, Severity, ToolResult

TOL = config.MONEY_TOLERANCE


def _differs(a: float, b: float) -> bool:
    return abs(round(a - b, 2)) > TOL


def lines_total(inv: Invoice) -> float:
    return round(sum(li.quantity * (li.unit_price or 0) for li in inv.line_items), 2)


def invoice_amount(inv: Invoice) -> float:
    """The amount we would pay: the stated total, or a computed one if the invoice omits it."""
    if inv.total is not None:
        return inv.total
    subtotal = inv.subtotal if inv.subtotal is not None else lines_total(inv)
    tax = inv.tax_amount or (subtotal * inv.tax_rate if inv.tax_rate else 0)
    return round(subtotal + tax + sum(c.amount for c in inv.other_charges), 2)


def verify_arithmetic(inv: Invoice) -> ToolResult:
    """Recompute every figure: line amounts, subtotal, tax, other charges and total."""
    findings: list[Finding] = []

    def mismatch(what: str, stated: float, computed: float) -> None:
        findings.append(Finding(code=ReasonCode.TOTAL_MISMATCH, severity=Severity.REJECT,
                                message=f"{what}: invoice states {stated:,.2f}, computed {computed:,.2f} "
                                        f"(difference {stated - computed:+,.2f}).",
                                data={"field": what, "stated": stated, "computed": computed}))

    for li in inv.line_items:
        if li.quantity <= 0:
            findings.append(Finding(code=ReasonCode.NEGATIVE_QTY, severity=Severity.REJECT,
                                    message=f"'{li.item}' has non-positive quantity {li.quantity:g}.",
                                    data={"item": li.item, "quantity": li.quantity}))
        if li.unit_price is not None and li.amount is not None and _differs(li.amount, li.quantity * li.unit_price):
            mismatch(f"Line total for {li.item}", li.amount, round(li.quantity * li.unit_price, 2))

    computed_subtotal = lines_total(inv)
    if inv.subtotal is not None and _differs(inv.subtotal, computed_subtotal):
        mismatch("Subtotal", inv.subtotal, computed_subtotal)

    base = inv.subtotal if inv.subtotal is not None else computed_subtotal
    tax = inv.tax_amount
    if inv.tax_rate is not None:
        expected_tax = round(base * inv.tax_rate, 2)
        if tax is not None and _differs(tax, expected_tax):
            mismatch(f"Tax at {inv.tax_rate:.0%}", tax, expected_tax)
        tax = expected_tax if tax is None else tax
    charges = sum(c.amount for c in inv.other_charges)
    computed_total = round(base + (tax or 0) + charges, 2)

    if inv.total is not None and _differs(inv.total, computed_total):
        mismatch("Total", inv.total, computed_total)

    amount = invoice_amount(inv)
    if amount <= 0:
        findings.append(Finding(code=ReasonCode.INVALID_AMOUNT, severity=Severity.REJECT,
                                message=f"Amount due is {amount:,.2f}; nothing payable.",
                                data={"amount": amount}))
    return ToolResult(findings=findings, data={"computed_subtotal": computed_subtotal,
                                               "computed_total": computed_total, "amount_due": amount})
