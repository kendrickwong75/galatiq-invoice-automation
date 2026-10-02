"""LLM provider factory. Every backend is a LangChain chat model, so agents call .bind_tools() and
.with_structured_output() identically whichever one is active.

    LLM_PROVIDER=grok    (default) xAI Grok via langchain-xai, needs XAI_API_KEY
    LLM_PROVIDER=mock    deterministic offline model with scripted responses; no key, no network
"""

from __future__ import annotations

import os

from langchain_core.language_models.chat_models import BaseChatModel

from . import config


class LLMConfigError(RuntimeError):
    pass


def resolve_provider(provider: str | None = None) -> str:
    return (provider or os.getenv("LLM_PROVIDER") or config.DEFAULT_PROVIDER).strip().lower()


def _require_key(env_var: str, provider: str) -> str:
    key = os.getenv(env_var)
    if not key:
        raise LLMConfigError(
            f"LLM_PROVIDER={provider} needs {env_var}, which is not set. Add it to .env, "
            "or run fully offline with LLM_PROVIDER=mock (or --provider mock)."
        )
    return key


def model_name(provider: str) -> str:
    return {
        "grok": os.getenv("GROK_MODEL", config.DEFAULT_GROK_MODEL),
        "mock": "mock-scripted",
    }.get(provider, "unknown")


def get_llm(provider: str | None = None) -> BaseChatModel:
    provider = resolve_provider(provider)
    if provider == "grok":
        key = _require_key("XAI_API_KEY", provider)
        from langchain_xai import ChatXAI

        return ChatXAI(model=model_name(provider), api_key=key, temperature=0, max_retries=2, timeout=120)
    if provider == "mock":
        from .mock_llm import MockChatModel

        return MockChatModel()
    raise LLMConfigError(f"Unknown LLM_PROVIDER {provider!r}; expected grok or mock.")
