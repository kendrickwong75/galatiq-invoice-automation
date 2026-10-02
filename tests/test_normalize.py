import pytest

from invoice_agents.ingestion.normalize import (dedupe_key, fix_ocr_digits, normalize_sku, parse_date,
                                                parse_money, to_iso_date)

SKUS = ["WidgetA", "WidgetB", "GadgetX", "FakeItem"]


def test_ocr_digit_fix_only_touches_o_next_to_digits():
    assert fix_ocr_digits("26-Jan-2O26") == "26-Jan-2026"
    assert fix_ocr_digits("$3,500.O0") == "$3,500.00"
    assert fix_ocr_digits("Oct FOO") == "Oct FOO"


@pytest.mark.parametrize("raw, expected", [
    ("$3,500.O0", 3500.0), ("1890.00", 1890.0), ("$15,000.00", 15000.0), (250, 250.0), ("", None), (None, None),
])
def test_parse_money(raw, expected):
    assert parse_money(raw) == expected


@pytest.mark.parametrize("raw, iso", [
    ("2026-01-15", "2026-01-15"), ("01/28/2026", "2026-01-28"), ("26-Jan-2O26", "2026-01-26"),
    ("Jan 30 2026", "2026-01-30"), ("January 27, 2026", "2026-01-27"),
])
def test_dates(raw, iso):
    assert parse_date(raw).isoformat() == iso


def test_relative_due_date_is_preserved_for_fraud_check():
    assert parse_date("yesterday") is None
    assert to_iso_date("yesterday") == "yesterday"


@pytest.mark.parametrize("raw, sku", [
    ("Widget A", "WidgetA"), ("WidgetA (rush order)", "WidgetA"), ("Gadget X", "GadgetX"),
    ("widgetb", "WidgetB"), ("Super Gizmo", "SuperGizmo"),
])
def test_normalize_sku(raw, sku):
    assert normalize_sku(raw, SKUS) == sku


def test_dedupe_key_ignores_formatting():
    assert dedupe_key("QuickShip Distributers", "INV 1012") == dedupe_key("QuickShip Distributers", "INV-1012")
    assert dedupe_key("Widgets Inc.", "INV-1001") == dedupe_key("Widgets, Inc", "1001")
    assert dedupe_key("Widgets Inc.", "INV-1001") != dedupe_key("Widgets Inc.", "INV-1016")
