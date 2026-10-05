"""The optional progress hook: events arrive in pipeline order, and a broken observer changes nothing."""

from invoice_agents.graph import run_invoice
from invoice_agents.models import Outcome

from .conftest import INVOICES


def test_progress_events_follow_the_pipeline(deps):
    events = []
    deps.on_progress = events.append
    result = run_invoice(INVOICES / "invoice_1001.txt", deps)

    assert result.outcome == Outcome.APPROVED
    starts = [e["node"] for e in events if e["phase"] == "start"]
    ends = [e["node"] for e in events if e["phase"] == "end"]
    assert starts == ends  # every node that starts also finishes
    order = ["load", "extract", "check_extraction", "validator_agent", "guard", "policy", "approve_draft",
             "critique", "finalize", "pay"]
    assert list(dict.fromkeys(starts)) == order  # first appearances; validator_agent runs twice (tools, then summary)
    assert starts[0] == "load" and starts[-1] == "pay"
    assert all(e["invoice"] == "invoice_1001.txt" for e in events)


def test_failing_observer_never_breaks_processing(deps):
    def broken(event):
        raise RuntimeError("UI bug")

    deps.on_progress = broken
    result = run_invoice(INVOICES / "invoice_1016.json", deps)
    assert result.outcome == Outcome.REJECTED and result.error is None
