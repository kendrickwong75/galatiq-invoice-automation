"""LangGraph wiring for the four-stage pipeline, plus single-invoice and batch runners.

load -> extract -> check_extraction --(errors)--> extract            [self-correction]
                        |--(unreadable)--> extraction_failed -> enqueue_review
                        v
              validator_agent <-> validator_tools                     [tool calling]
                        v
                      guard -> policy -> approve_draft -> critique --(revise)--> approve_draft   [reflection]
                                                             v
                                                         finalize -> pay | record_rejection | enqueue_review

Every node except validator_tools (LangGraph's ToolNode) is wrapped by _audited, which times it, writes one
audit event and reports start/end to the optional Deps.on_progress observer (used by the web UI).
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Callable

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from . import audit, config, db
from .agents import approver, extractor, payer, validator
from .agents.state import Deps, PipelineState
from .llm import get_llm, model_name, resolve_provider
from .models import Finding, InvoiceResult, Outcome, ReasonCode, Severity


def build_deps(provider: str | None = None, db_path: str | Path = config.DEFAULT_DB_PATH,
               output_dir: str | Path = config.DEFAULT_OUTPUT_DIR, reset_db: bool = False) -> Deps:
    provider = resolve_provider(provider)
    llm = get_llm(provider)
    db_path = db.init_db(db_path, reset=reset_db)
    output_dir = Path(output_dir)
    audit.configure(output_dir)
    return Deps(llm=llm, db_path=db_path, output_dir=output_dir, provider=provider,
                model=model_name(provider), known_skus=db.list_skus(db_path))


def _notify(deps: Deps, event: dict) -> None:
    """Send a progress event to the optional observer; an observer failure never affects processing."""
    if deps.on_progress is None:
        return
    try:
        deps.on_progress(event)
    except Exception:
        pass


def _audited(name: str, fn: Callable, deps: Deps) -> Callable[[PipelineState], dict]:
    """Wrap a node: time it, write one audit event, and append that event to the state's trail."""
    def run(state: PipelineState) -> dict:
        started = time.perf_counter()
        base = {"run_id": state.get("run_id"), "invoice": state.get("source_file"), "node": name,
                "provider": deps.provider, "model": deps.model}
        _notify(deps, {**base, "phase": "start"})
        try:
            update = fn(state, deps) or {}
        except Exception as exc:
            error = {**base, "status": "error", "error": repr(exc),
                     "latency_ms": round((time.perf_counter() - started) * 1000)}
            audit.event(**error)
            _notify(deps, {**error, "phase": "error"})
            raise
        event = {**base, "status": "ok", "latency_ms": round((time.perf_counter() - started) * 1000),
                 **update.pop("_audit", {})}
        audit.event(**event)
        _notify(deps, {**event, "phase": "end"})
        update["trail"] = [event]
        return update
    return run


def build_graph(deps: Deps):
    check_tools = validator.make_tools(deps)
    bound = deps.llm.bind_tools(check_tools)
    tool_node = ToolNode(check_tools)

    g = StateGraph(PipelineState)
    nodes = {
        "load": extractor.load,
        "extract": extractor.extract,
        "check_extraction": extractor.check_extraction,
        "extraction_failed": extractor.extraction_failed,
        "validator_agent": lambda s, d: validator.validator_agent(s, d, bound),
        "guard": validator.guard,
        "policy": approver.policy,
        "approve_draft": approver.approve_draft,
        "critique": approver.critique,
        "finalize": approver.finalize,
        "pay": payer.pay,
        "record_rejection": payer.record_rejection,
        "enqueue_review": payer.enqueue_review,
    }
    for name, fn in nodes.items():
        g.add_node(name, _audited(name, fn, deps))
    g.add_node("validator_tools", tool_node)

    g.add_edge(START, "load")
    g.add_edge("load", "extract")
    g.add_edge("extract", "check_extraction")
    g.add_conditional_edges("check_extraction", extractor.route_after_check,
                            {"retry": "extract", "failed": "extraction_failed", "validate": "validator_agent"})
    g.add_edge("extraction_failed", "enqueue_review")
    g.add_conditional_edges("validator_agent", validator.route_validator,
                            {"tools": "validator_tools", "guard": "guard"})
    g.add_edge("validator_tools", "validator_agent")
    g.add_edge("guard", "policy")
    g.add_edge("policy", "approve_draft")
    g.add_conditional_edges("approve_draft", approver.route_after_draft,
                            {"critique": "critique", "finalize": "finalize"})
    g.add_conditional_edges("critique", approver.route_after_critique,
                            {"revise": "approve_draft", "finalize": "finalize"})
    g.add_conditional_edges("finalize", approver.route_outcome,
                            {"pay": "pay", "reject": "record_rejection", "review": "enqueue_review"})
    for terminal in ("pay", "record_rejection", "enqueue_review"):
        g.add_edge(terminal, END)
    return g.compile()


def _result_from_state(state: dict, deps: Deps, run_id: str, source: str, ms: int) -> InvoiceResult:
    findings: list[Finding] = state.get("findings") or []
    policy = state.get("policy") or {}
    codes = [f.code.value for f in findings if f.severity != Severity.INFO]
    if policy.get("vp_review"):
        codes.append(ReasonCode.VP_REVIEW.value)
    fx = state.get("fx") or {}
    return InvoiceResult(
        source_file=source, run_id=run_id, outcome=Outcome(state["outcome"]), rationale=state.get("rationale", ""),
        reason_codes=sorted(set(codes)), findings=findings, invoice=state.get("invoice"),
        amount_usd=state.get("amount_usd"), fx_rate=fx.get("rate"),
        policy_outcome=Outcome(policy["outcome"]) if policy else None,
        llm_outcome=Outcome(state["llm_outcome"]) if state.get("llm_outcome") else None,
        llm_policy_disagreement=bool(state.get("llm_policy_disagreement")), vp_review=bool(policy.get("vp_review")),
        payment=state.get("payment") if (state.get("payment") or {}).get("status") != "skipped" else None,
        extraction_method=state.get("extraction_method"), extraction_attempts=state.get("extraction_attempts", 0),
        reflection_rounds=state.get("reflection_rounds", 0),
        validator_tool_calls=state.get("validator_tool_calls") or [], guard_invoked=state.get("guard_invoked") or [],
        validation_summary=state.get("validation_summary"), provider=deps.provider, duration_ms=ms,
    )


def run_invoice(path: str | Path, deps: Deps, graph=None) -> InvoiceResult:
    """Process one invoice end to end. Never raises: failures become NEEDS_HUMAN_REVIEW results."""
    path = Path(path)
    graph = graph or build_graph(deps)
    run_id = uuid.uuid4().hex[:8]
    started = time.perf_counter()
    try:
        state = graph.invoke({"run_id": run_id, "source_file": path.name, "path": str(path), "messages": []},
                             config={"recursion_limit": config.GRAPH_RECURSION_LIMIT})
        result = _result_from_state(state, deps, run_id, path.name, round((time.perf_counter() - started) * 1000))
    except Exception as exc:
        audit.event(run_id=run_id, invoice=path.name, node="pipeline", status="error", error=repr(exc))
        result = InvoiceResult(
            source_file=path.name, run_id=run_id, outcome=Outcome.NEEDS_HUMAN_REVIEW,
            rationale=f"Processing error; routed to a human. {exc}", reason_codes=[ReasonCode.EXTRACTION_FAILED.value],
            provider=deps.provider, error=repr(exc), duration_ms=round((time.perf_counter() - started) * 1000),
        )
        audit.append_jsonl(deps.output_dir / "review_queue.jsonl",
                           {"source_file": path.name, "reasons": result.reason_codes, "rationale": result.rationale})
    audit.write_json(deps.output_dir / "results" / f"{path.name}.json", result.model_dump(mode="json"))
    return result


def invoice_files(directory: str | Path) -> list[Path]:
    """Sorted so runs are reproducible (e.g. invoice_1004.json before invoice_1004_revised.json)."""
    return sorted(p for p in Path(directory).iterdir()
                  if p.is_file() and p.suffix.lower() in config.SUPPORTED_EXTENSIONS)


def run_batch(directory: str | Path, deps: Deps, on_result: Callable[[InvoiceResult], None] | None = None
              ) -> list[InvoiceResult]:
    graph = build_graph(deps)
    results = []
    for path in invoice_files(directory):
        result = run_invoice(path, deps, graph)
        results.append(result)
        if on_result:
            on_result(result)
    return results
