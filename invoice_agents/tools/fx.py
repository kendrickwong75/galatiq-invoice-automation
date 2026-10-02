from __future__ import annotations

from .. import config
from ..models import Finding, Invoice, ReasonCode, Severity, ToolResult
from .arithmetic import invoice_amount


def fx_rate(currency: str | None) -> float | None:
    return config.FX_RATES_TO_USD.get((currency or "USD").upper())


def convert_to_usd(inv: Invoice) -> ToolResult:
    """Simulated FX service: static rates so the $10K rule and payment always work in USD."""
    amount = invoice_amount(inv)
    currency = (inv.currency or "USD").upper()
    rate = fx_rate(currency)
    if rate is None:
        return ToolResult(
            findings=[Finding(code=ReasonCode.UNSUPPORTED_CURRENCY, severity=Severity.REVIEW,
                              message=f"No FX rate for {currency}; a human must confirm the USD amount.",
                              data={"currency": currency, "amount": amount})],
            data={"amount_usd": None, "currency": currency, "original_amount": amount, "rate": None},
        )
    amount_usd = round(amount * rate, 2)
    findings = []
    if currency != "USD":
        findings.append(Finding(code=ReasonCode.CURRENCY_CONVERTED, severity=Severity.INFO,
                                message=f"{amount:,.2f} {currency} converted at {rate} = ${amount_usd:,.2f} "
                                        "(static mock rate).",
                                data={"currency": currency, "rate": rate, "amount_usd": amount_usd}))
    return ToolResult(findings=findings, data={"amount_usd": amount_usd, "currency": currency,
                                               "original_amount": amount, "rate": rate})
