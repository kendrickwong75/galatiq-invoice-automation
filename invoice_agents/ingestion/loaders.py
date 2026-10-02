"""Read any supported invoice file into raw text (+ parsed data for structured formats)."""

from __future__ import annotations

import csv
import io
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import config


class UnsupportedDocument(Exception):
    pass


@dataclass
class RawDocument:
    path: Path
    fmt: str  # text | pdf | json | csv | xml
    text: str
    data: Any = field(default=None)  # parsed structure for json/csv/xml

    @property
    def is_structured(self) -> bool:
        return self.fmt in {"json", "csv", "xml"}


def load_document(path: str | Path) -> RawDocument:
    path = Path(path)
    if not path.is_file():
        raise UnsupportedDocument(f"file not found: {path}")
    ext = path.suffix.lower()
    if ext not in config.SUPPORTED_EXTENSIONS:
        raise UnsupportedDocument(f"unsupported file type {ext!r}")

    if ext == ".pdf":
        import pdfplumber

        with pdfplumber.open(path) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        if not text.strip():
            raise UnsupportedDocument("PDF has no extractable text (scanned image?)")
        return RawDocument(path, "pdf", text)

    text = path.read_text(encoding="utf-8-sig")
    if ext == ".txt":
        return RawDocument(path, "text", text)
    if ext == ".json":
        return RawDocument(path, "json", text, json.loads(text))
    if ext == ".csv":
        return RawDocument(path, "csv", text, list(csv.reader(io.StringIO(text))))
    return RawDocument(path, "xml", text, ET.fromstring(text))
