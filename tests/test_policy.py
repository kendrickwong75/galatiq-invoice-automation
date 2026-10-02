from invoice_agents.agents.approver import apply_policy, finalize
from invoice_agents.models import Finding, ReasonCode, Severity


def f(code, sev, **data):
    return Finding(code=code, severity=sev, message=code.value, data=data)


def test_reject_beats_everything():
    assert apply_policy([f(ReasonCode.STOCK_EXCEEDED, Severity.REJECT), f(ReasonCode.DUPLICATE, Severity.REVIEW)],
                        500)["outcome"] == "REJECTED"


def test_two_strong_fraud_signals_reject():
    strong = [f(ReasonCode.FRAUD_SIGNAL, Severity.FLAG, strong=True, signal=s) for s in ("a", "b")]
    assert apply_policy(strong, 500)["outcome"] == "REJECTED"
    assert apply_policy(strong[:1], 500)["outcome"] == "APPROVED"


def test_vp_threshold_with_flags_goes_to_review():
    flag = [f(ReasonCode.PRICE_VARIANCE, Severity.FLAG)]
    assert apply_policy(flag, 10_000.00)["outcome"] == "APPROVED"  # threshold is "over $10K"
    decision = apply_policy(flag, 10_000.01)
    assert decision["outcome"] == "NEEDS_HUMAN_REVIEW" and decision["vp_review"]
    assert apply_policy([], 25_000)["outcome"] == "APPROVED"


def _state(policy_outcome, llm_outcome):
    return {"policy": {"outcome": policy_outcome, "reasons": [{"code": "X", "message": "blocking issue"}]},
            "draft": {"outcome": llm_outcome, "rationale": "agent view", "cited_codes": [], "confidence": 0.8}}


def test_llm_cannot_loosen_a_policy_reject():
    out = finalize(_state("REJECTED", "APPROVED"), deps=None)
    assert out["outcome"] == "REJECTED" and out["llm_policy_disagreement"]
    assert "Policy overrides" in out["rationale"]


def test_llm_can_escalate():
    out = finalize(_state("APPROVED", "NEEDS_HUMAN_REVIEW"), deps=None)
    assert out["outcome"] == "NEEDS_HUMAN_REVIEW" and out["rationale"] == "agent view"


def test_missing_draft_falls_back_to_policy():
    out = finalize({"policy": {"outcome": "APPROVED", "reasons": []}, "draft": None}, deps=None)
    assert out["outcome"] == "APPROVED" and "policy applied" in out["rationale"]
