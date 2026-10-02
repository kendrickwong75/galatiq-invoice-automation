"""--json mode: stdout must be pure, parseable JSON; everything else goes to stderr."""

import json

import main

from .conftest import INVOICES


def run_cli(tmp_path, capsys, *target):
    code = main.main(["--provider", "mock", "--reset-db", "--json",
                      "--db-path", str(tmp_path / "cli.db"), "--output-dir", str(tmp_path / "out"), *target])
    out, err = capsys.readouterr()
    return code, json.loads(out), err


def test_single_invoice_json(tmp_path, capsys):
    code, result, err = run_cli(tmp_path, capsys, f"--invoice_path={INVOICES / 'invoice_1014.xml'}")
    assert code == 0
    assert (result["outcome"], result["amount_usd"]) == ("APPROVED", 4455.0)
    assert result["payment"]["currency"] == "EUR"
    assert "Paid 4455.0 to TechParts International" in err  # stub print redirected to stderr


def test_batch_json(tmp_path, capsys):
    code, payload, _ = run_cli(tmp_path, capsys, f"--invoice_dir={INVOICES}")
    assert code == 0
    assert len(payload["results"]) == payload["summary"]["invoices"] == 20
    assert payload["summary"]["approved"] == 8
