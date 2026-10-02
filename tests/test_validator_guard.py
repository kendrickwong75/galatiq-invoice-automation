"""If the validation agent forgets (or fails) to call its tools, the guard still runs every check."""

from langchain_core.messages import AIMessage

from invoice_agents.agents.validator import REQUIRED_CHECKS
from invoice_agents.graph import run_invoice
from invoice_agents.mock_llm import MockChatModel
from invoice_agents.models import Outcome

from .conftest import INVOICES


class LazyValidator(MockChatModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if "check_inventory" in self.tool_names:
            from langchain_core.outputs import ChatGeneration, ChatResult

            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Looks fine to me."))])
        return super()._generate(messages, stop, run_manager, **kwargs)


def test_guard_runs_skipped_checks(deps):
    deps.llm = LazyValidator()
    result = run_invoice(INVOICES / "invoice_1016.json", deps)
    assert result.guard_invoked == list(REQUIRED_CHECKS)
    assert result.outcome == Outcome.REJECTED and "UNKNOWN_ITEM" in result.reason_codes


class BrokenModel(MockChatModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise ConnectionError("provider down")


def test_llm_outage_on_structured_invoice_degrades_to_policy(deps):
    deps.llm = BrokenModel()
    result = run_invoice(INVOICES / "invoice_1015.csv", deps)
    assert result.outcome == Outcome.APPROVED  # deterministic parse + guard + policy still decide
    assert "Approval agent unavailable" in result.rationale


def test_llm_outage_on_free_text_invoice_goes_to_human(deps):
    deps.llm = BrokenModel()
    result = run_invoice(INVOICES / "invoice_1001.txt", deps)
    assert result.outcome == Outcome.NEEDS_HUMAN_REVIEW and "EXTRACTION_FAILED" in result.reason_codes
