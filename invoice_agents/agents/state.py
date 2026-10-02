"""Shared graph state and dependencies."""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph.message import add_messages

from ..ingestion.loaders import RawDocument
from ..models import Finding, Invoice


def merge_dicts(a: dict | None, b: dict | None) -> dict:
    return {**(a or {}), **(b or {})}


@dataclass
class Deps:
    llm: BaseChatModel
    db_path: Path
    output_dir: Path
    provider: str
    model: str
    known_skus: list[str] = field(default_factory=list)


class PipelineState(TypedDict, total=False):
    run_id: str
    source_file: str
    path: str
    document: RawDocument
    # ingestion
    invoice: Invoice | None
    extraction_method: str
    extraction_attempts: int
    extraction_errors: list[str]
    extraction_warnings: list[str]
    previous_extraction: dict | None
    # validation
    messages: Annotated[list, add_messages]
    validator_turns: int
    tool_results: Annotated[dict, merge_dicts]
    validator_tool_calls: Annotated[list, operator.add]
    guard_invoked: list[str]
    validation_summary: str
    findings: list[Finding]
    amount_usd: float | None
    fx: dict[str, Any]
    dedupe_key: str
    # approval
    policy: dict[str, Any]
    draft: dict | None
    critique: dict | None
    reflection_rounds: int
    outcome: str
    llm_outcome: str | None
    llm_policy_disagreement: bool
    rationale: str
    # payment
    payment: dict | None
    # observability
    trail: Annotated[list, operator.add]
