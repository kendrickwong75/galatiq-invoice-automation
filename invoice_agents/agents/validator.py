"""Validation Agent: an LLM tool-calling loop over the deterministic checks, plus a guard that runs any
required check the agent skipped, so findings never depend on the model remembering to call a tool.

Tools read the extracted invoice from graph state (InjectedState) rather than taking it as arguments,
so a model can't alter the numbers it is checking.
"""

from __future__ import annotations

import difflib
import json
from typing import Annotated, Callable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from .. import config, db, tools
from ..models import Finding, Invoice, ToolResult
from ..prompts import VALIDATOR_SYSTEM, context_block
from .state import Deps, PipelineState

REQUIRED_CHECKS = ("check_required_fields", "check_inventory", "verify_arithmetic", "check_catalog_prices",
                   "check_duplicate", "convert_to_usd", "fraud_signals")


def _check_functions(deps: Deps) -> dict[str, Callable[[PipelineState], ToolResult]]:
    return {
        "check_required_fields": lambda s: tools.check_required_fields(s["invoice"]),
        "check_inventory": lambda s: tools.check_inventory(s["invoice"], deps.db_path),
        "verify_arithmetic": lambda s: tools.verify_arithmetic(s["invoice"]),
        "check_catalog_prices": lambda s: tools.check_catalog_prices(s["invoice"], deps.db_path),
        "check_duplicate": lambda s: tools.check_duplicate(s["invoice"], deps.db_path),
        "convert_to_usd": lambda s: tools.convert_to_usd(s["invoice"]),
        "fraud_signals": lambda s: tools.fraud_signals(s["invoice"], s["document"].text),
    }


_DESCRIPTIONS = {
    "check_required_fields": "Check vendor, invoice number, line items, total and due date are present.",
    "check_inventory": "Compare billed quantities (summed per item) with stock in the inventory database.",
    "verify_arithmetic": "Recompute line totals, subtotal, tax, other charges and total; flag mismatches.",
    "check_catalog_prices": "Flag unit prices more than 10% above the catalog price (after FX).",
    "check_duplicate": "Check the ledger for a paid or pending invoice with the same vendor and number.",
    "convert_to_usd": "Convert the amount due to USD with the treasury FX table.",
    "fraud_signals": "Scan for fraud red flags: pressure language, wire demands, bad due dates, renames.",
}


def make_tools(deps: Deps) -> list:
    checks = _check_functions(deps)

    def make_check_tool(name: str):
        def _run(state: Annotated[dict, InjectedState],
                 tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
            result = checks[name](state)
            return Command(update={
                "tool_results": {name: result},
                "validator_tool_calls": [name],
                "messages": [ToolMessage(content=result.model_dump_json(), tool_call_id=tool_call_id, name=name)],
            })
        return tool(name, description=_DESCRIPTIONS[name])(_run)

    out = [make_check_tool(name) for name in REQUIRED_CHECKS]

    @tool
    def lookup_inventory_item(name: str) -> str:
        """Look up an item by name in the inventory database; returns the record or the closest matches."""
        skus = db.list_skus(deps.db_path)
        hit = db.get_item(deps.db_path, name)
        if hit:
            return json.dumps(hit)
        return json.dumps({"found": False, "closest": difflib.get_close_matches(name, skus, n=3, cutoff=0.5)})

    out.append(lookup_inventory_item)
    return out


def validator_agent(state: PipelineState, deps: Deps, bound_llm) -> dict:
    history = list(state.get("messages") or [])
    new = []
    if not history:
        inv: Invoice = state["invoice"]
        payload = {"source_file": state["source_file"], "invoice": inv.model_dump(),
                   "extraction_warnings": state.get("extraction_warnings", [])}
        new = [SystemMessage(VALIDATOR_SYSTEM), HumanMessage(context_block(payload))]
    turns = state.get("validator_turns", 0) + 1
    try:
        response = bound_llm.invoke(history + new)
    except Exception as exc:  # the guard still runs every check
        response = AIMessage(content=f"Validation agent unavailable: {exc}")
    calls = [c["name"] for c in getattr(response, "tool_calls", []) or []]
    return {"messages": new + [response], "validator_turns": turns,
            "_audit": {"turn": turns, "tool_calls_requested": calls}}


def route_validator(state: PipelineState) -> str:
    last = (state.get("messages") or [None])[-1]
    if isinstance(last, AIMessage) and last.tool_calls and state.get("validator_turns", 0) < config.MAX_VALIDATOR_TURNS:
        return "tools"
    return "guard"


def guard(state: PipelineState, deps: Deps) -> dict:
    """Run any required check the agent skipped, then assemble the authoritative findings."""
    checks = _check_functions(deps)
    results = dict(state.get("tool_results") or {})
    missing = [n for n in REQUIRED_CHECKS if n not in results]
    for name in missing:
        results[name] = checks[name](state)

    findings: list[Finding] = [f for name in REQUIRED_CHECKS for f in results[name].findings]
    fx = results["convert_to_usd"].data
    summary = next((m.content for m in reversed(state.get("messages") or [])
                    if isinstance(m, AIMessage) and not m.tool_calls and isinstance(m.content, str)), "")
    return {
        "tool_results": {n: results[n] for n in missing},
        "guard_invoked": missing,
        "findings": findings,
        "amount_usd": fx.get("amount_usd"),
        "fx": fx,
        "dedupe_key": results["check_duplicate"].data.get("dedupe_key"),
        "validation_summary": summary,
        "_audit": {"guard_ran": missing, "agent_called": sorted(set(state.get("validator_tool_calls") or [])),
                   "findings": [f"{f.severity.value}:{f.code.value}" for f in findings],
                   "amount_usd": fx.get("amount_usd")},
    }
