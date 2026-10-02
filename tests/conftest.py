from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from invoice_agents import config  # noqa: E402
from invoice_agents.graph import build_deps  # noqa: E402
from invoice_agents.ingestion.loaders import load_document  # noqa: E402
from invoice_agents.ingestion.normalize import normalize_invoice  # noqa: E402
from invoice_agents.ingestion.parsers import parse_structured  # noqa: E402

INVOICES = ROOT / "data" / "invoices"
SKUS = [s for s, _, _ in config.INVENTORY_SEED]


@pytest.fixture
def deps(tmp_path):
    return build_deps("mock", db_path=tmp_path / "test.db", output_dir=tmp_path / "out", reset_db=True)


@pytest.fixture
def db_path(deps):
    return deps.db_path


def structured_invoice(name: str):
    return normalize_invoice(parse_structured(load_document(INVOICES / name)), SKUS)
