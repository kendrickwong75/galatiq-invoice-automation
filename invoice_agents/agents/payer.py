"""Payment stage: pay approved invoices, record rejections, queue reviews. Every path writes the ledger."""

from __future__ import annotations

from datetime import datetime, timezone

from .. import audit, db
from ..ingestion.normalize import dedupe_key
from ..models import Finding, ReasonCode, Severity
from ..payment import execute_payment
from .state import Deps, PipelineState


def _key(state: PipelineState) -> str:
    inv = state["invoice"]
    return state.get("dedupe_key") or dedupe_key(inv.vendor, inv.invoice_number)


def _record(state: PipelineState, deps: Deps, status: str, payment_ref: str | None = None) -> None:
    inv = state.get("invoice")
    if inv is None:  # nothing identifiable to dedupe against
        return
    db.ledger_record(deps.db_path, dedupe_key=_key(state), source_file=state["source_file"], status=status,
                     vendor=inv.vendor, invoice_number=inv.invoice_number, amount_usd=state.get("amount_usd"),
                     invoice=inv.model_dump(), payment_ref=payment_ref)


def _queue_for_review(state: PipelineState, deps: Deps, findings, rationale: str) -> None:
    inv = state.get("invoice")
    audit.append_jsonl(deps.output_dir / "review_queue.jsonl", {
        "queued_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_file": state["source_file"],
        "vendor": inv.vendor if inv else None,
        "invoice_number": inv.invoice_number if inv else None,
        "amount_usd": state.get("amount_usd"),
        "reasons": [f.code.value for f in findings if f.severity in (Severity.REJECT, Severity.REVIEW, Severity.FLAG)],
        "rationale": rationale,
    })


def pay(state: PipelineState, deps: Deps) -> dict:
    inv, fx = state["invoice"], state.get("fx") or {}
    result = execute_payment(db_path=deps.db_path, dedupe_key=_key(state), vendor=inv.vendor,
                             amount_usd=state.get("amount_usd"), original_amount=fx.get("original_amount"),
                             currency=fx.get("currency"))
    if result.status == "success":
        _record(state, deps, "PAID", result.payment_ref)
        return {"payment": result.model_dump(), "_audit": {"payment": "success", "payment_ref": result.payment_ref,
                                                           "amount_usd": result.amount_usd}}

    failure = Finding(code=ReasonCode.PAYMENT_FAILED, severity=Severity.REVIEW,
                      message=f"Payment not executed: {result.error}", data={"error": result.error})
    findings = state["findings"] + [failure]
    rationale = f"{state['rationale']} Payment failed ({result.error}); held for human review."
    _record(state, deps, "REVIEW")
    _queue_for_review(state, deps, findings, rationale)
    return {"payment": result.model_dump(), "outcome": "NEEDS_HUMAN_REVIEW", "findings": findings,
            "rationale": rationale, "_audit": {"payment": "failed", "error": result.error}}


def record_rejection(state: PipelineState, deps: Deps) -> dict:
    _record(state, deps, "REJECTED")
    return {"payment": {"status": "skipped"}, "_audit": {"ledger": "REJECTED"}}


def enqueue_review(state: PipelineState, deps: Deps) -> dict:
    _record(state, deps, "REVIEW")
    _queue_for_review(state, deps, state.get("findings", []), state.get("rationale", ""))
    return {"payment": {"status": "skipped"}, "_audit": {"ledger": "REVIEW", "queued": True}}
