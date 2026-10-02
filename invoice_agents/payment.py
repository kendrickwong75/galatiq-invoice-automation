"""Payment stage: the README's mock banking stub plus a guarded wrapper.

Payment is a deterministic graph node, never a tool bound to an LLM: the approval agent can recommend
approval, but only this code path can move money.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .models import PaymentResult


# Provided by the case README, copied verbatim. This is the simulated bank; do not modify.
# Its print() is redirected to stderr by main.py in --json mode so stdout stays valid JSON.
def mock_payment(vendor, amount):
    print(f"Paid {amount} to {vendor}")
    return {"status": "success"}


def execute_payment(
    *,
    db_path: str | Path,
    dedupe_key: str,
    vendor: str | None,
    amount_usd: float | None,
    original_amount: float | None,
    currency: str | None,
) -> PaymentResult:
    """Pre-flight checks, call the bank stub, verify its response. Never raises."""
    base = dict(vendor=vendor, amount_usd=amount_usd, original_amount=original_amount, currency=currency)

    if not vendor:
        return PaymentResult(status="failed", error="pre-flight: vendor is empty", **base)
    if amount_usd is None or amount_usd <= 0:
        return PaymentResult(status="failed", error=f"pre-flight: invalid amount {amount_usd!r}", **base)
    if any(rec["status"] == "PAID" for rec in db.ledger_lookup(db_path, dedupe_key)):
        return PaymentResult(status="failed", error="pre-flight: invoice already paid", **base)

    amount = round(amount_usd, 2)
    try:
        response = mock_payment(vendor, amount)
    except Exception as exc:  # the bank call is the one place we expect external failure
        return PaymentResult(status="failed", error=f"bank error: {exc!r}", **base)

    if not isinstance(response, dict) or response.get("status") != "success":
        return PaymentResult(status="failed", error=f"bank returned {response!r}", **base)

    return PaymentResult(
        status="success",
        payment_ref=f"PAY-{uuid.uuid4().hex[:12].upper()}",
        paid_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **{**base, "amount_usd": amount},
    )
