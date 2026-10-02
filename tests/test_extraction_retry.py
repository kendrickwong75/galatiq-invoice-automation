import json

from invoice_agents.agents.extractor import extraction_errors
from invoice_agents.graph import run_invoice
from invoice_agents.ingestion.loaders import load_document
from invoice_agents.ingestion.normalize import normalize_invoice
from invoice_agents.mock_llm import FIXTURES_DIR
from invoice_agents.models import Invoice, Outcome

from .conftest import INVOICES, SKUS


def test_misread_line_is_detected():
    first_attempt = json.loads((FIXTURES_DIR / "invoice_1012.pdf.json").read_text())["attempts"][0]
    inv = normalize_invoice(Invoice(**first_attempt), SKUS)
    errors = extraction_errors(inv, load_document(INVOICES / "invoice_1012.pdf").text)
    assert any("quantity 2" in e and "line amount is 3,000.00" in e for e in errors)
    assert any("subtotal" in e for e in errors)


def test_self_correction_loop_recovers(deps):
    result = run_invoice(INVOICES / "invoice_1012.pdf", deps)
    assert result.extraction_attempts == 2
    assert [li.quantity for li in result.invoice.line_items] == [12, 7, 4]
    assert result.outcome == Outcome.APPROVED

    events = [json.loads(line) for line in (deps.output_dir / "audit.jsonl").read_text().splitlines()]
    checks = [e for e in events if e["node"] == "check_extraction"]
    assert [c["action"] for c in checks] == ["retry", "proceed"]


def test_hallucinated_value_is_not_grounded():
    doc = load_document(INVOICES / "invoice_1001.txt")
    inv = Invoice(invoice_number="INV-1001", vendor="Widgets Inc.", total=5500.0,
                  line_items=[{"item": "WidgetA", "quantity": 10, "unit_price": 250.0}])
    assert any("total 5500" in e for e in extraction_errors(inv, doc.text))
