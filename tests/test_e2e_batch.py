"""The whole sample set as one batch, offline, against the expected decision for every invoice."""

import json
from pathlib import Path

import pytest
import yaml

from invoice_agents.graph import invoice_files, run_batch

from .conftest import INVOICES

EXPECTED = yaml.safe_load((Path(__file__).parent / "fixtures" / "expected_outcomes.yaml").read_text())


@pytest.fixture(scope="module")
def batch(tmp_path_factory):
    from invoice_agents.graph import build_deps

    tmp = tmp_path_factory.mktemp("batch")
    deps = build_deps("mock", db_path=tmp / "b.db", output_dir=tmp / "out", reset_db=True)
    return deps, {r.source_file: r for r in run_batch(INVOICES, deps)}


def test_every_sample_has_an_expectation():
    assert sorted(EXPECTED) == [p.name for p in invoice_files(INVOICES)]


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_expected_outcome(batch, name):
    _, results = batch
    result, expected = results[name], EXPECTED[name]
    assert result.outcome.value == expected["outcome"], result.rationale
    assert set(expected["codes"]) <= set(result.reason_codes)
    if "amount_usd" in expected:
        assert result.amount_usd == pytest.approx(expected["amount_usd"])
    if "extraction_attempts" in expected:
        assert result.extraction_attempts == expected["extraction_attempts"]


def test_validator_agent_called_every_check_itself(batch):
    _, results = batch
    assert all(r.guard_invoked == [] for r in results.values())


def test_vp_invoices_get_a_reflection_revision(batch):
    _, results = batch
    vp = [r for r in results.values() if r.vp_review]
    assert vp and all(r.reflection_rounds == 2 for r in vp)


def test_outputs_written(batch):
    deps, results = batch
    assert len(list((deps.output_dir / "results").glob("*.json"))) == len(results)
    queue = [json.loads(line) for line in (deps.output_dir / "review_queue.jsonl").read_text().splitlines()]
    assert {q["source_file"] for q in queue} == {n for n, e in EXPECTED.items() if e["outcome"] == "NEEDS_HUMAN_REVIEW"}
    nodes = {json.loads(line)["node"] for line in (deps.output_dir / "audit.jsonl").read_text().splitlines()}
    assert {"extract", "validator_agent", "guard", "policy", "approve_draft", "critique", "finalize", "pay"} <= nodes
