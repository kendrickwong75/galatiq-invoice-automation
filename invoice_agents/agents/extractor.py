"""Ingestion Agent: deterministic parsing for structured files, Grok structured output for free text and
PDFs, with a self-correction loop that sends specific errors back for a re-read."""

from __future__ import annotations

import re

from langchain_core.messages import HumanMessage, SystemMessage

from .. import config
from ..ingestion.loaders import load_document
from ..ingestion.normalize import _compact, fix_ocr_digits, normalize_invoice
from ..ingestion.parsers import ParseError, parse_structured
from ..models import Finding, Invoice, ReasonCode, Severity
from ..prompts import EXTRACTION_SYSTEM, context_block
from .state import Deps, PipelineState

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def load(state: PipelineState, deps: Deps) -> dict:
    doc = load_document(state["path"])
    return {"document": doc, "_audit": {"format": doc.fmt, "chars": len(doc.text)}}


def extract(state: PipelineState, deps: Deps) -> dict:
    doc = state["document"]
    attempt = state.get("extraction_attempts", 0) + 1

    fallback_note = None
    if attempt == 1 and doc.is_structured:
        try:
            invoice = normalize_invoice(parse_structured(doc), deps.known_skus)
            return {"invoice": invoice, "extraction_method": "deterministic", "extraction_attempts": attempt,
                    "extraction_errors": [], "_audit": {"method": "deterministic", "attempt": attempt}}
        except ParseError as exc:
            fallback_note = str(exc)

    payload = {"source_file": state["source_file"], "attempt": attempt,
               "previous_errors": state.get("extraction_errors", [])}
    messages = [
        SystemMessage(EXTRACTION_SYSTEM),
        HumanMessage(f"{context_block(payload)}\n\nDocument ({doc.fmt}):\n```\n{doc.text}\n```"),
    ]
    method = "llm_fallback" if doc.is_structured else "llm"
    try:
        raw = deps.llm.with_structured_output(Invoice).invoke(messages)
        if raw is None:
            raise ValueError("model returned no structured output")
        invoice = normalize_invoice(raw, deps.known_skus)
    except Exception as exc:
        return {"invoice": None, "extraction_method": method, "extraction_attempts": attempt,
                "extraction_errors": [f"LLM extraction failed: {exc}"],
                "_audit": {"method": method, "attempt": attempt, "error": str(exc)[:500]}}
    return {"invoice": invoice, "extraction_method": method, "extraction_attempts": attempt,
            "_audit": {"method": method, "attempt": attempt, "parse_fallback": fallback_note}}


def _document_numbers(text: str) -> set[float]:
    return {abs(float(n.replace(",", ""))) for n in _NUMBER.findall(fix_ocr_digits(text))}


def extraction_errors(inv: Invoice, raw_text: str) -> list[str]:
    """Errors worth a re-read: values not grounded in the document, and lines that don't add up."""
    errors: list[str] = []
    numbers = _document_numbers(raw_text)
    compact_text = _compact(raw_text)

    def grounded(value: float | None) -> bool:
        return value is None or any(abs(abs(value) - n) < 0.005 for n in numbers)

    for li in inv.line_items:
        name = li.description or li.item
        if _compact(name) not in compact_text and _compact(li.item) not in compact_text:
            errors.append(f"Item '{name}' does not appear in the document; re-read the item names.")
        for field in ("quantity", "unit_price", "amount"):
            value = getattr(li, field)
            if not grounded(value):
                errors.append(f"Line '{name}' {field} {value:g} does not appear in the document.")
        if li.unit_price is not None and li.amount is not None and abs(li.quantity * li.unit_price - li.amount) > 0.01:
            errors.append(f"Line '{name}': quantity {li.quantity:g} x unit price {li.unit_price:,.2f} = "
                          f"{li.quantity * li.unit_price:,.2f}, but the line amount is {li.amount:,.2f}. "
                          "Re-read this line's quantity, price and amount.")
    for field in ("subtotal", "tax_amount", "total"):
        if not grounded(getattr(inv, field)):
            errors.append(f"{field} {getattr(inv, field):g} does not appear in the document.")
    if inv.subtotal is not None and inv.line_items:
        lines = sum(li.quantity * (li.unit_price or 0) for li in inv.line_items)
        if abs(lines - inv.subtotal) > 0.01:
            errors.append(f"Line items sum to {lines:,.2f} but subtotal is {inv.subtotal:,.2f}; "
                          "check for a misread or missed line.")
    if inv.vendor and _compact(inv.vendor) not in compact_text:
        errors.append(f"Vendor '{inv.vendor}' does not appear in the document.")
    return errors


def check_extraction(state: PipelineState, deps: Deps) -> dict:
    """Decide whether the extraction needs a re-read (self-correction) or can move on."""
    inv, attempts = state.get("invoice"), state.get("extraction_attempts", 0)
    if inv is None:
        errors = state.get("extraction_errors", [])
    elif state.get("extraction_method") == "deterministic":
        errors = []
    else:
        errors = extraction_errors(inv, state["document"].text)

    snapshot = inv.model_dump() if inv else None
    confirmed = bool(errors) and snapshot is not None and snapshot == state.get("previous_extraction")
    audit = {"attempt": attempts, "errors": errors, "confirmed_by_reread": confirmed}

    if errors and not confirmed and attempts < config.MAX_EXTRACTION_ATTEMPTS:
        return {"extraction_errors": errors, "previous_extraction": snapshot, "_audit": {**audit, "action": "retry"}}
    if errors and inv is not None:
        # Re-read confirmed the values (or retries ran out): the document itself is inconsistent, and
        # validation will report it. These are warnings, not extraction failures.
        return {"extraction_errors": [], "extraction_warnings": errors, "_audit": {**audit, "action": "proceed"}}
    return {"extraction_errors": errors if inv is None else [], "_audit": {**audit, "action": "proceed"}}


def route_after_check(state: PipelineState) -> str:
    if state.get("invoice") is None:
        return "retry" if state.get("extraction_attempts", 0) < config.MAX_EXTRACTION_ATTEMPTS else "failed"
    return "retry" if state.get("extraction_errors") else "validate"


def extraction_failed(state: PipelineState, deps: Deps) -> dict:
    errors = state.get("extraction_errors", [])
    finding = Finding(code=ReasonCode.EXTRACTION_FAILED, severity=Severity.REVIEW,
                      message=f"Could not extract the invoice after {state.get('extraction_attempts', 0)} attempts.",
                      data={"errors": errors})
    return {"findings": [finding], "outcome": "NEEDS_HUMAN_REVIEW",
            "rationale": "The document could not be read reliably, so a person needs to key it in. "
                         + (errors[-1] if errors else ""),
            "_audit": {"errors": errors}}
