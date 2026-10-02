import pytest

from invoice_agents import payment
from invoice_agents.graph import build_graph, run_invoice
from invoice_agents.models import Outcome

from .conftest import INVOICES


@pytest.fixture
def bank(monkeypatch):
    calls, response = [], {"value": {"status": "success"}}

    def fake(vendor, amount):
        calls.append((vendor, amount))
        return response["value"]

    monkeypatch.setattr(payment, "mock_payment", fake)
    return calls, response


def test_readme_stub_is_unchanged(capsys):
    assert payment.mock_payment("Acme", 10) == {"status": "success"}
    assert capsys.readouterr().out == "Paid 10 to Acme\n"


def test_approved_invoice_pays_once_in_usd(deps, bank):
    calls, _ = bank
    result = run_invoice(INVOICES / "invoice_1014.xml", deps)
    assert calls == [("TechParts International", 4455.0)]
    assert result.payment.status == "success" and result.payment.payment_ref.startswith("PAY-")
    assert (result.payment.original_amount, result.payment.currency) == (4125.0, "EUR")


@pytest.mark.parametrize("name", ["invoice_1016.json", "invoice_1003.txt"])
def test_rejected_invoice_never_reaches_the_bank(deps, bank, name):
    calls, _ = bank
    assert run_invoice(INVOICES / name, deps).outcome == Outcome.REJECTED
    assert calls == []


def test_duplicate_never_reaches_the_bank(deps, bank):
    calls, _ = bank
    graph = build_graph(deps)
    run_invoice(INVOICES / "invoice_1011.pdf", deps, graph)
    run_invoice(INVOICES / "invoice_1011.txt", deps, graph)
    assert len(calls) == 1


def test_bank_failure_goes_to_review(deps, bank):
    _, response = bank
    response["value"] = {"status": "failed"}
    result = run_invoice(INVOICES / "invoice_1001.txt", deps)
    assert result.outcome == Outcome.NEEDS_HUMAN_REVIEW and "PAYMENT_FAILED" in result.reason_codes
    assert (deps.output_dir / "review_queue.jsonl").exists()
