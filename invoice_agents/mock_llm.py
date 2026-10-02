"""Deterministic offline stand-in for Grok, so anyone can run the full pipeline without a key or network.

It implements bind_tools(), so LangChain's with_structured_output() works on it exactly as on ChatXAI;
agent code is identical across providers. Which agent is calling is inferred from the bound tool/schema
names, and inputs come from the <context> envelope in the prompt.

- Extraction of free-text/PDF invoices replays scripted outputs from mock_fixtures/<file>.json. A fixture
  may list several attempts; invoice_1012.pdf scripts a misread first attempt so the self-correction
  loop runs offline.
- Validation calls every required tool, then summarises the tool results.
- Approval and reflection follow the policy outcome and write templated rationales; for invoices above
  the VP threshold the critic asks for one revision, so the reflection loop also runs offline.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field

from . import config
from .prompts import read_context

FIXTURES_DIR = Path(__file__).parent / "mock_fixtures"
VALIDATOR_TOOLS = ("check_required_fields", "check_inventory", "verify_arithmetic", "check_catalog_prices",
                   "check_duplicate", "convert_to_usd", "fraud_signals")


class MockFixtureMissing(RuntimeError):
    pass


def _tool_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:10]}", "type": "tool_call"}


class MockChatModel(BaseChatModel):
    tool_names: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "mock"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # noqa: D401 - LangChain signature
        names = [convert_to_openai_tool(t)["function"]["name"] for t in tools]
        return self.model_copy(update={"tool_names": names})

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:
        ctx = read_context(messages)
        names = set(self.tool_names)
        if "Invoice" in names:
            msg = AIMessage(content="", tool_calls=[_tool_call("Invoice", self._extract(ctx))])
        elif "ApprovalDraft" in names:
            msg = AIMessage(content="", tool_calls=[_tool_call("ApprovalDraft", _draft(ctx))])
        elif "Critique" in names:
            msg = AIMessage(content="", tool_calls=[_tool_call("Critique", _critique(ctx))])
        elif names & set(VALIDATOR_TOOLS):
            msg = _validate(messages)
        else:
            msg = AIMessage(content="(mock model: no scripted response for this call)")
        return ChatResult(generations=[ChatGeneration(message=msg)])

    @staticmethod
    def _extract(ctx: dict[str, Any]) -> dict[str, Any]:
        source = ctx.get("source_file", "")
        path = FIXTURES_DIR / f"{source}.json"
        if not path.is_file():
            raise MockFixtureMissing(
                f"No mock extraction fixture for {source!r}. The mock model only knows the provided sample "
                "invoices; use LLM_PROVIDER=grok for new free-text/PDF invoices."
            )
        attempts = json.loads(path.read_text(encoding="utf-8"))["attempts"]
        attempt = int(ctx.get("attempt", 1))
        return attempts[min(attempt, len(attempts)) - 1]


def _validate(messages: list[BaseMessage]) -> AIMessage:
    tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
    if not tool_msgs:
        return AIMessage(content="Running all validation checks.",
                         tool_calls=[_tool_call(n, {}) for n in VALIDATOR_TOOLS])
    bullets = []
    for m in tool_msgs:
        try:
            body = json.loads(m.content)
        except (TypeError, ValueError):
            continue
        for f in body.get("findings", []):
            if f["severity"] != "INFO":
                bullets.append(f"- [{f['severity']}] {f['code']}: {f['message']}")
    return AIMessage(content="\n".join(bullets) or "- All checks passed with no findings.")


def _fmt_usd(amount: float | None) -> str:
    return f"${amount:,.2f}" if amount is not None else "an unknown amount"


def _draft(ctx: dict[str, Any]) -> dict[str, Any]:
    policy = ctx.get("policy", {})
    outcome = policy.get("outcome", "NEEDS_HUMAN_REVIEW")
    findings = ctx.get("findings", [])
    amount = ctx.get("amount_usd")
    by_sev = lambda sev: [f for f in findings if f["severity"] == sev]  # noqa: E731
    flags = by_sev("FLAG")

    if outcome == "REJECTED":
        blocking = by_sev("REJECT") or [f for f in flags if f.get("data", {}).get("strong")]
        text = "Rejected. " + " ".join(f["message"] for f in blocking)
    elif outcome == "NEEDS_HUMAN_REVIEW":
        parts = []
        for f in by_sev("REVIEW"):
            parts.append(f["message"])
            diff = f.get("data", {}).get("diff")
            if diff and diff.get("line_changes"):
                changes = "; ".join(f"{c['item']} {c['before']:g} -> {c['after']:g}" for c in diff["line_changes"])
                parts.append(f"Changes vs the paid copy: {changes}; total {_fmt_usd(diff.get('prior_total'))} -> "
                             f"{_fmt_usd(diff.get('new_total'))}. Confirm the amendment with purchasing before "
                             "paying the difference.")
            elif diff:
                parts.append("Content is identical; do not pay twice.")
        if not parts:
            parts = [f["message"] for f in flags]
        text = "Held for human review. " + " ".join(parts)
    else:
        text = f"Approved for payment of {_fmt_usd(amount)}. Items are in stock and the arithmetic reconciles."
        if flags:
            text += " Noted, not blocking: " + " ".join(f["message"] for f in flags)

    if policy.get("vp_review") and ctx.get("critique_issues"):
        text += (f" VP review: {_fmt_usd(amount)} exceeds the ${config.VP_REVIEW_THRESHOLD_USD:,.0f} threshold; "
                 "each blocking finding above was confirmed against inventory and recomputed totals.")
    codes = sorted({f["code"] for f in findings if f["severity"] != "INFO"})
    return {"outcome": outcome, "rationale": text, "cited_codes": codes, "confidence": 0.9}


def _critique(ctx: dict[str, Any]) -> dict[str, Any]:
    if ctx.get("policy", {}).get("vp_review") and int(ctx.get("round", 1)) == 1:
        return {"verdict": "revise",
                "issues": ["Amount exceeds the VP threshold: state the amount against the threshold and confirm "
                           "each blocking finding explicitly."]}
    return {"verdict": "accept", "issues": []}
