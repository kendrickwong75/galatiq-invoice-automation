import re
import warnings

import pytest

from invoice_agents.llm import LLMConfigError, get_llm
from invoice_agents.mock_llm import MockChatModel
from invoice_agents.models import ApprovalDraft, Critique, Invoice

from .conftest import ROOT


def test_missing_key_fails_fast_with_mock_hint(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    with pytest.raises(LLMConfigError, match="LLM_PROVIDER=mock"):
        get_llm("grok")


def test_grok_is_the_default_provider(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    with pytest.raises(LLMConfigError, match="XAI_API_KEY"):
        get_llm()


def test_grok_constructs_chatxai_and_binds_agent_schemas(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "test-key-not-real")
    from langchain_xai import ChatXAI

    llm = get_llm("grok")
    assert isinstance(llm, ChatXAI)
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any "this will fail at runtime" warning fails the test
        for schema in (Invoice, ApprovalDraft, Critique):
            llm.with_structured_output(schema)


def test_unknown_provider():
    with pytest.raises(LLMConfigError, match="expected grok or mock"):
        get_llm("ollama")


def test_mock_supports_structured_output_like_real_providers():
    llm = get_llm("mock")
    assert isinstance(llm, MockChatModel)
    result = llm.with_structured_output(Critique).invoke("<context>{\"round\": 1}</context>")
    assert result == Critique(verdict="accept", issues=[])


def test_our_code_never_imports_openai():
    pattern = re.compile(r"^\s*(import openai|from openai\b)", re.M)
    offenders = [p for p in [ROOT / "main.py", *(ROOT / "invoice_agents").rglob("*.py")]
                 if pattern.search(p.read_text(encoding="utf-8"))]
    assert offenders == []
