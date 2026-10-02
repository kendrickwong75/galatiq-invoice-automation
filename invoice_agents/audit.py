"""Structured audit trail: one JSON line per graph node, plus per-invoice result files."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOGGER_NAME = "invoice_agents.audit"


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds")}
        payload.update(getattr(record, "event", {}))
        return json.dumps(payload, default=str)


def configure(output_dir: str | Path) -> Path:
    """Route audit events to <output_dir>/audit.jsonl. Safe to call repeatedly (e.g. in tests)."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "audit.jsonl"
    logger = logging.getLogger(_LOGGER_NAME)
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(_JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return path


def event(**fields: Any) -> dict[str, Any]:
    logging.getLogger(_LOGGER_NAME).info(fields.get("node", "event"), extra={"event": fields})
    return fields


def write_json(path: str | Path, data: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return path


def append_jsonl(path: str | Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(data, default=str) + "\n")
