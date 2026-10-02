"""Typed data contracts shared by every agent, tool and report."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Outcome(str, Enum):
    APPROVED = "APPROVED"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"
    REJECTED = "REJECTED"

    @property
    def strictness(self) -> int:
        return {"APPROVED": 0, "NEEDS_HUMAN_REVIEW": 1, "REJECTED": 2}[self.value]


class Severity(str, Enum):
    REJECT = "REJECT"  # policy rejects the invoice
    REVIEW = "REVIEW"  # policy routes the invoice to a human
    FLAG = "FLAG"  # risk signal for the approval agent to weigh
    INFO = "INFO"  # context only


class ReasonCode(str, Enum):
    UNKNOWN_ITEM = "UNKNOWN_ITEM"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    STOCK_EXCEEDED = "STOCK_EXCEEDED"
    NEGATIVE_QTY = "NEGATIVE_QTY"
    TOTAL_MISMATCH = "TOTAL_MISMATCH"
    INVALID_AMOUNT = "INVALID_AMOUNT"
    MISSING_FIELD = "MISSING_FIELD"
    DUPLICATE = "DUPLICATE"
    PREVIOUSLY_PROCESSED = "PREVIOUSLY_PROCESSED"
    UNSUPPORTED_CURRENCY = "UNSUPPORTED_CURRENCY"
    CURRENCY_CONVERTED = "CURRENCY_CONVERTED"
    PRICE_VARIANCE = "PRICE_VARIANCE"
    FRAUD_SIGNAL = "FRAUD_SIGNAL"
    VENDOR_RENAMED = "VENDOR_RENAMED"
    NEAR_THRESHOLD = "NEAR_THRESHOLD"
    DUE_DATE_INCONSISTENT = "DUE_DATE_INCONSISTENT"
    VP_REVIEW = "VP_REVIEW"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    PAYMENT_FAILED = "PAYMENT_FAILED"


class Finding(BaseModel):
    code: ReasonCode
    severity: Severity
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """What every validation check returns: findings plus any computed values."""

    findings: list[Finding] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)


# --- Extraction schema (also the LLM's structured-output schema) ------------


class LineItem(BaseModel):
    item: str = Field(description="Product name only, as written, e.g. 'WidgetA' or 'Widget A'. "
                                  "Put qualifiers such as 'rush order' in note.")
    quantity: float = Field(description="Quantity exactly as written (may be negative if the document says so).")
    unit_price: float | None = Field(default=None, description="Unit price as a number, no currency symbol.")
    amount: float | None = Field(default=None, description="Line total as written on the document, if shown.")
    note: str | None = Field(default=None, description="Line qualifier, e.g. 'rush order', 'Volume discount'.")
    description: str | None = Field(default=None, description="Leave null; filled in by normalization.")


class Charge(BaseModel):
    label: str = Field(description="e.g. 'Shipping', 'Handling'. Never tax.")
    amount: float


class Invoice(BaseModel):
    """Structured invoice extracted from any source document."""

    invoice_number: str | None = Field(default=None, description="Invoice number as written, e.g. 'INV-1001'.")
    vendor: str | None = Field(default=None, description="Vendor (seller) legal name only, without 'formerly ...'.")
    vendor_address: str | None = None
    invoice_date: str | None = Field(default=None, description="Invoice date as written.")
    due_date: str | None = Field(default=None, description="Due date exactly as written, even if it is a word "
                                                           "like 'yesterday'. Null if absent.")
    currency: str = Field(default="USD", description="ISO currency code.")
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: float | None = None
    tax_rate: float | None = Field(default=None, description="Decimal fraction, e.g. 0.05 for 5%.")
    tax_amount: float | None = None
    other_charges: list[Charge] = Field(default_factory=list, description="Shipping, handling and other fees.")
    total: float | None = Field(default=None, description="Total amount due as written.")
    payment_terms: str | None = None
    notes: str | None = Field(default=None, description="Any free-text notes, including payment instructions "
                                                        "and vendor name history such as 'formerly X'.")


# --- Approval agent schemas ------------------------------------------------


class ApprovalDraft(BaseModel):
    """The approval agent's decision on one invoice."""

    outcome: Outcome = Field(description="APPROVED, NEEDS_HUMAN_REVIEW or REJECTED.")
    rationale: str = Field(description="2-5 sentences a finance stakeholder can act on, citing evidence.")
    cited_codes: list[str] = Field(default_factory=list, description="Reason codes the decision relies on.")
    confidence: float = Field(ge=0, le=1, description="Confidence in the decision, 0-1.")


class Critique(BaseModel):
    """The reflection agent's review of an approval draft."""

    verdict: Literal["accept", "revise"]
    issues: list[str] = Field(default_factory=list, description="Specific problems the drafter must fix.")


# --- Final result ----------------------------------------------------------


class PaymentResult(BaseModel):
    status: Literal["success", "failed", "skipped"]
    vendor: str | None = None
    amount_usd: float | None = None
    original_amount: float | None = None
    currency: str | None = None
    payment_ref: str | None = None
    paid_at: str | None = None
    error: str | None = None


class InvoiceResult(BaseModel):
    source_file: str
    run_id: str
    outcome: Outcome
    rationale: str
    reason_codes: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    invoice: Invoice | None = None
    amount_usd: float | None = None
    fx_rate: float | None = None
    policy_outcome: Outcome | None = None
    llm_outcome: Outcome | None = None
    llm_policy_disagreement: bool = False
    vp_review: bool = False
    payment: PaymentResult | None = None
    extraction_method: str | None = None
    extraction_attempts: int = 0
    reflection_rounds: int = 0
    validator_tool_calls: list[str] = Field(default_factory=list)
    guard_invoked: list[str] = Field(default_factory=list)
    validation_summary: str | None = None
    provider: str | None = None
    duration_ms: int | None = None
    error: str | None = None
