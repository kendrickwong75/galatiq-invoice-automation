from invoice_agents import tools
from invoice_agents.models import Charge, Invoice, LineItem, ReasonCode, Severity

from .conftest import structured_invoice


def codes(result):
    return {f.code for f in result.findings}


def make(items, **kw):
    return Invoice(invoice_number="INV-1", vendor="Northwind Supply Co", invoice_date="2026-01-01",
                   due_date="2026-02-01", payment_terms="Net 30",
                   line_items=[LineItem(item=i, quantity=q, unit_price=p) for i, q, p in items], **kw)


def test_inventory_sums_repeated_items(db_path):
    result = tools.check_inventory(structured_invoice("invoice_1013.json"), db_path)
    exceeded = {f.data["item"]: f.data["requested"] for f in result.findings if f.code == ReasonCode.STOCK_EXCEEDED}
    assert exceeded == {"WidgetA": 22, "WidgetB": 18, "GadgetX": 9}


def test_inventory_unknown_and_zero_stock(db_path):
    assert codes(tools.check_inventory(structured_invoice("invoice_1016.json"), db_path)) == {ReasonCode.UNKNOWN_ITEM}
    assert codes(tools.check_inventory(make([("FakeItem", 1, 10)]), db_path)) == {ReasonCode.OUT_OF_STOCK}
    assert codes(tools.check_inventory(make([("WidgetA", 15, 250)]), db_path)) == set()


def test_arithmetic_catches_wrong_total():
    result = tools.verify_arithmetic(structured_invoice("invoice_1007.csv"))
    [f] = [f for f in result.findings if f.code == ReasonCode.TOTAL_MISMATCH]
    assert (f.data["stated"], f.data["computed"]) == (15525.0, 15635.0)


def test_arithmetic_catches_hidden_50_dollars_in_1013():
    [f] = tools.verify_arithmetic(structured_invoice("invoice_1013.json")).findings
    assert f.data["stated"] - f.data["computed"] == 50.0


def test_arithmetic_negative_quantity_and_amount():
    assert {ReasonCode.NEGATIVE_QTY, ReasonCode.TOTAL_MISMATCH, ReasonCode.INVALID_AMOUNT} <= \
        codes(tools.verify_arithmetic(structured_invoice("invoice_1009.json")))


def test_arithmetic_includes_other_charges():
    inv = make([("WidgetA", 2, 250)], subtotal=500, tax_amount=25, other_charges=[Charge(label="Shipping", amount=10)],
               total=535)
    assert tools.verify_arithmetic(inv).findings == []


def test_fx_conversion_and_unsupported_currency():
    result = tools.convert_to_usd(structured_invoice("invoice_1014.xml"))
    assert result.data["amount_usd"] == 4455.0 and result.data["rate"] == 1.08
    jpy = tools.convert_to_usd(make([("WidgetA", 1, 250)], total=250, currency="JPY"))
    assert codes(jpy) == {ReasonCode.UNSUPPORTED_CURRENCY} and jpy.findings[0].severity == Severity.REVIEW


def test_price_variance_flags_markups_not_discounts(db_path):
    inv = make([("WidgetA", 1, 300), ("WidgetB", 1, 480)])
    [f] = tools.check_catalog_prices(inv, db_path).findings
    assert f.code == ReasonCode.PRICE_VARIANCE and f.data["item"] == "WidgetA" and f.severity == Severity.FLAG


def test_fraud_signals_on_obvious_fraud():
    inv = make([("FakeItem", 100, 1000)], total=100000, vendor_address=None)
    inv.vendor, inv.due_date = "Fraudster LLC", "yesterday"
    text = 'URGENT - Pay immediately to avoid penalties!!! Wire transfer preferred.'
    result = tools.fraud_signals(inv, text)
    signals = {f.data["signal"] for f in result.findings if f.code == ReasonCode.FRAUD_SIGNAL}
    assert signals == {"urgent_language", "unusual_payment_channel", "suspicious_vendor_name", "invalid_due_date"}


def test_near_threshold_and_rename_flags():
    inv = make([("WidgetA", 1, 9975)], total=9975)
    result = tools.fraud_signals(inv, "QuickShip Distributers (formerly FastShip Ltd.)")
    assert codes(result) == {ReasonCode.NEAR_THRESHOLD, ReasonCode.VENDOR_RENAMED}


def test_required_fields():
    inv = make([("WidgetA", 1, 250)], total=250)
    inv.vendor, inv.due_date = "", None
    result = tools.check_required_fields(inv)
    severities = {f.data["field"]: f.severity for f in result.findings}
    assert severities["vendor"] == Severity.REJECT and severities["due_date"] == Severity.FLAG
