"""Approval stage: deterministic policy engine, Approval Agent draft, Reflection Agent critique loop, and a
finalizer that lets the LLM be stricter than policy but never more lenient."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from .. import config
from ..models import ApprovalDraft, Critique, Finding, Outcome, ReasonCode, Severity
from ..prompts import APPROVER_SYSTEM, CRITIC_SYSTEM, context_block
from .state import Deps, PipelineState


def apply_policy(findings: list[Finding], amount_usd: float | None) -> dict:
    """The company's approval rules as code. The LLM layers judgement on top; it cannot loosen these."""
    reject = [f for f in findings if f.severity == Severity.REJECT]
    review = [f for f in findings if f.severity == Severity.REVIEW]
    flags = [f for f in findings if f.severity == Severity.FLAG]
    strong_fraud = [f for f in flags if f.code == ReasonCode.FRAUD_SIGNAL and f.data.get("strong")]
    vp_review = amount_usd is not None and amount_usd > config.VP_REVIEW_THRESHOLD_USD

    if reject:
        outcome, reasons = Outcome.REJECTED, reject
    elif len(strong_fraud) >= config.STRONG_FRAUD_SIGNALS_TO_REJECT:
        outcome, reasons = Outcome.REJECTED, strong_fraud
    elif review:
        outcome, reasons = Outcome.NEEDS_HUMAN_REVIEW, review
    elif vp_review and flags:
        outcome, reasons = Outcome.NEEDS_HUMAN_REVIEW, flags
    else:
        outcome, reasons = Outcome.APPROVED, []

    # Strong fraud signals always count as a reason when the invoice is rejected for any cause.
    if outcome == Outcome.REJECTED and len(strong_fraud) >= config.STRONG_FRAUD_SIGNALS_TO_REJECT:
        reasons = reasons + [f for f in strong_fraud if f not in reasons]
    return {"outcome": outcome.value, "vp_review": vp_review,
            "reasons": [{"code": f.code.value, "message": f.message} for f in reasons],
            "strong_fraud_signals": len(strong_fraud)}


def policy(state: PipelineState, deps: Deps) -> dict:
    decision = apply_policy(state["findings"], state.get("amount_usd"))
    return {"policy": decision, "reflection_rounds": 0, "draft": None, "critique": None,
            "_audit": {"policy_outcome": decision["outcome"], "vp_review": decision["vp_review"],
                       "reasons": [r["code"] for r in decision["reasons"]]}}


def _context(state: PipelineState) -> dict:
    return {
        "source_file": state["source_file"],
        "invoice": state["invoice"].model_dump(),
        "amount_usd": state.get("amount_usd"),
        "fx": state.get("fx"),
        "findings": [f.model_dump(mode="json") for f in state["findings"]],
        "policy": state["policy"],
        "validation_summary": state.get("validation_summary"),
        "round": state.get("reflection_rounds", 0) + 1,
    }


def approve_draft(state: PipelineState, deps: Deps) -> dict:
    payload = _context(state)
    if state.get("draft"):
        payload["previous_draft"] = state["draft"]
        payload["critique_issues"] = (state.get("critique") or {}).get("issues", [])
    messages = [SystemMessage(APPROVER_SYSTEM), HumanMessage(context_block(payload))]
    try:
        draft = deps.llm.with_structured_output(ApprovalDraft).invoke(messages)
        if draft is None:
            raise ValueError("model returned no structured output")
    except Exception as exc:
        return {"draft": None, "_audit": {"error": str(exc)[:500]}}
    return {"draft": draft.model_dump(mode="json"),
            "_audit": {"round": payload["round"], "draft_outcome": draft.outcome.value,
                       "confidence": draft.confidence}}


def critique(state: PipelineState, deps: Deps) -> dict:
    payload = {**_context(state), "draft": state["draft"]}
    messages = [SystemMessage(CRITIC_SYSTEM), HumanMessage(context_block(payload))]
    rounds = state.get("reflection_rounds", 0) + 1
    try:
        result = deps.llm.with_structured_output(Critique).invoke(messages)
        if result is None:
            raise ValueError("model returned no structured output")
    except Exception as exc:  # no critique available: accept the draft rather than loop
        return {"critique": {"verdict": "accept", "issues": [], "error": str(exc)[:500]},
                "reflection_rounds": rounds, "_audit": {"round": rounds, "error": str(exc)[:500]}}
    return {"critique": result.model_dump(), "reflection_rounds": rounds,
            "_audit": {"round": rounds, "verdict": result.verdict, "issues": result.issues}}


def route_after_draft(state: PipelineState) -> str:
    return "critique" if state.get("draft") else "finalize"


def route_after_critique(state: PipelineState) -> str:
    verdict = (state.get("critique") or {}).get("verdict")
    if verdict == "revise" and state.get("reflection_rounds", 0) < config.MAX_REFLECTION_ROUNDS:
        return "revise"
    return "finalize"


def _policy_rationale(policy_decision: dict) -> str:
    reasons = " ".join(r["message"] for r in policy_decision["reasons"])
    return {
        "APPROVED": "All policy checks passed.",
        "REJECTED": f"Rejected by policy. {reasons}",
        "NEEDS_HUMAN_REVIEW": f"Held for human review by policy. {reasons}",
    }[policy_decision["outcome"]]


def finalize(state: PipelineState, deps: Deps) -> dict:
    policy_outcome = Outcome(state["policy"]["outcome"])
    draft = state.get("draft")
    if not draft:
        return {"outcome": policy_outcome.value, "llm_outcome": None, "llm_policy_disagreement": False,
                "rationale": _policy_rationale(state["policy"]) + " (Approval agent unavailable; policy applied.)",
                "_audit": {"final": policy_outcome.value, "llm": None}}

    llm_outcome = Outcome(draft["outcome"])
    final = max(policy_outcome, llm_outcome, key=lambda o: o.strictness)
    disagreement = llm_outcome != policy_outcome
    rationale = draft["rationale"]
    if llm_outcome.strictness < policy_outcome.strictness:
        rationale = f"{_policy_rationale(state['policy'])} Policy overrides the agent's {llm_outcome.value} " \
                    f"recommendation. Agent's note: {draft['rationale']}"
    return {"outcome": final.value, "llm_outcome": llm_outcome.value, "llm_policy_disagreement": disagreement,
            "rationale": rationale,
            "_audit": {"final": final.value, "policy": policy_outcome.value, "llm": llm_outcome.value,
                       "llm_policy_disagreement": disagreement}}


def route_outcome(state: PipelineState) -> str:
    return {"APPROVED": "pay", "REJECTED": "reject", "NEEDS_HUMAN_REVIEW": "review"}[state["outcome"]]
