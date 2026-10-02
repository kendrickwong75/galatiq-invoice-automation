import pytest

from invoice_agents.ingestion.loaders import UnsupportedDocument, load_document

from .conftest import INVOICES, structured_invoice


@pytest.mark.parametrize("name, number, vendor, items, total, currency", [
    ("invoice_1004.json", "INV-1004", "Precision Parts Ltd.", 2, 1890.0, "USD"),  # total given as a string
    ("invoice_1004_revised.json", "INV-1004", "Precision Parts Ltd.", 3, 5940.0, "USD"),
    ("invoice_1005.json", "INV-1005", "Global Supply Chain Partners", 3, 15225.0, "USD"),
    ("invoice_1006.csv", "INV-1006", "Acme Industrial Supplies", 2, 2750.0, "USD"),  # key/value CSV
    ("invoice_1007.csv", "INV-1007", "MegaWidgets Corp", 3, 15525.0, "USD"),  # row CSV + summary rows
    ("invoice_1009.json", "INV-1009", "", 2, -250.0, "USD"),
    ("invoice_1013.json", "INV-1013", "Atlas Industrial Supply", 8, 22562.8, "USD"),
    ("invoice_1014.xml", "INV-1014", "TechParts International", 2, 4125.0, "EUR"),
    ("invoice_1015.csv", "INV-1015", "Reliable Components Inc.", 3, 6500.0, "USD"),
    ("invoice_1016.json", "INV-1016", "Widgets Inc.", 3, 3233.0, "USD"),
])
def test_structured_parsers(name, number, vendor, items, total, currency):
    inv = structured_invoice(name)
    assert (inv.invoice_number, inv.vendor, len(inv.line_items), inv.total, inv.currency) == \
        (number, vendor, items, total, currency)


def test_csv_dates_are_normalised_to_iso():
    inv = structured_invoice("invoice_1007.csv")
    assert (inv.invoice_date, inv.due_date) == ("2026-01-28", "2026-02-28")
    assert inv.tax_rate == pytest.approx(0.06)


@pytest.mark.parametrize("name", ["invoice_1011.pdf", "invoice_1012.pdf", "invoice_1013.pdf"])
def test_pdf_text_extraction(name):
    doc = load_document(INVOICES / name)
    assert doc.fmt == "pdf" and "INV" in doc.text


def test_unsupported_file(tmp_path):
    bad = tmp_path / "invoice.docx"
    bad.write_text("x")
    with pytest.raises(UnsupportedDocument):
        load_document(bad)
