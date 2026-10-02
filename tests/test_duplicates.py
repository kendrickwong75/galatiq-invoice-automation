from invoice_agents import db
from invoice_agents.graph import build_graph, run_invoice
from invoice_agents.models import Outcome

from .conftest import INVOICES


def test_same_invoice_in_two_formats_is_paid_once(deps):
    graph = build_graph(deps)
    first = run_invoice(INVOICES / "invoice_1011.pdf", deps, graph)
    second = run_invoice(INVOICES / "invoice_1011.txt", deps, graph)
    assert first.outcome == Outcome.APPROVED
    assert second.outcome == Outcome.NEEDS_HUMAN_REVIEW and "DUPLICATE" in second.reason_codes
    assert second.payment is None
    [dup] = [f for f in second.findings if f.code.value == "DUPLICATE"]
    assert dup.data["kind"] == "identical copy"


def test_revision_is_held_with_a_diff(deps):
    graph = build_graph(deps)
    run_invoice(INVOICES / "invoice_1004.json", deps, graph)
    revised = run_invoice(INVOICES / "invoice_1004_revised.json", deps, graph)
    [dup] = [f for f in revised.findings if f.code.value == "DUPLICATE"]
    assert dup.data["diff"]["line_changes"] == [{"item": "GadgetX", "before": 0, "after": 5.0}]
    assert dup.data["diff"]["total_delta"] == 4050.0
    assert "GadgetX" in revised.rationale


def test_rejected_invoice_is_re_evaluated_not_blocked(deps):
    graph = build_graph(deps)
    run_invoice(INVOICES / "invoice_1013.json", deps, graph)
    again = run_invoice(INVOICES / "invoice_1013.pdf", deps, graph)
    assert "DUPLICATE" not in again.reason_codes
    assert any(f.code.value == "PREVIOUSLY_PROCESSED" for f in again.findings)


def test_ledger_records_every_decision(deps):
    graph = build_graph(deps)
    for name in ("invoice_1001.txt", "invoice_1016.json"):
        run_invoice(INVOICES / name, deps, graph)
    statuses = {r["source_file"]: r["status"] for key in ("widgets|1001", "widgets|1016")
                for r in db.ledger_lookup(deps.db_path, key)}
    assert statuses == {"invoice_1001.txt": "PAID", "invoice_1016.json": "REJECTED"}
