from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any, Iterable

from llama_index.core import Document, SimpleDirectoryReader
from llama_index.core.readers.base import BaseReader

log = logging.getLogger(__name__)


class JSONRecordReader(BaseReader):
    """Emit one Document per element when the file contains a JSON array.

    For non-array JSON (object or scalar) falls back to a single Document with
    the whole content as text.

    Each Document carries metadata:
        - record_index: position in the array (0-based)
        - record_id: best-effort id from common fields (id, *_id, Id) if present
    """

    ID_FIELD_CANDIDATES = (
        "id", "Id", "ID",
        "email_id", "thread_id",
        "employee_id", "emp_id",
        "client_id", "customer_id", "product_id",
        "conversation_id", "post_id", "ticket_id",
    )

    def load_data(self, file: Path, extra_info: dict | None = None) -> list[Document]:
        extra_info = dict(extra_info or {})
        raw = Path(file).read_text(encoding="utf-8", errors="replace")
        raw = raw.replace("\r\n", "\n")

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.warning("JSON parse failed for %s: %s; falling back to raw text", file, exc)
            return [Document(text=raw, metadata={**extra_info, "json_fallback": True})]

        if isinstance(parsed, list):
            docs: list[Document] = []
            for idx, record in enumerate(parsed):
                text = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=False)
                rec_id = self._extract_id(record) if isinstance(record, dict) else None
                meta = {
                    **extra_info,
                    "record_index": idx,
                    "record_count": len(parsed),
                }
                if rec_id is not None:
                    meta["record_id"] = rec_id
                docs.append(Document(text=text, metadata=meta))
            return docs

        text = json.dumps(parsed, ensure_ascii=False, indent=2)
        return [Document(text=text, metadata={**extra_info, "record_count": 1})]

    @classmethod
    def _extract_id(cls, record: dict[str, Any]) -> str | None:
        for field in cls.ID_FIELD_CANDIDATES:
            if field in record and record[field] not in (None, ""):
                return str(record[field])
        return None


class CSVRecordReader(BaseReader):
    """Emit one Document per row, preserving column names.

    Each row is rendered as `key: value` pairs (one per line) so downstream
    extraction sees structured text with labeled fields. Metadata carries:
        - row_index: 1-based (0 is the header)
        - row_count: total data rows
        - columns: comma-joined column names (useful for debugging)
    """

    def load_data(self, file: Path, extra_info: dict | None = None) -> list[Document]:
        extra_info = dict(extra_info or {})
        with open(file, newline="", encoding="utf-8", errors="replace") as fh:
            reader = csv.DictReader(fh)
            columns = reader.fieldnames or []
            rows = list(reader)

        docs: list[Document] = []
        for i, row in enumerate(rows, start=1):
            lines = [f"{k.strip()}: {(v or '').strip()}" for k, v in row.items()]
            text = "\n".join(lines)
            docs.append(
                Document(
                    text=text,
                    metadata={
                        **extra_info,
                        "row_index": i,
                        "row_count": len(rows),
                        "columns": ",".join(columns),
                    },
                )
            )
        return docs


DEFAULT_FILE_EXTRACTOR = {
    ".json": JSONRecordReader(),
    ".jsonl": JSONRecordReader(),
    ".csv": CSVRecordReader(),
}


def qontext_reader(
    input_dir: str | Path,
    *,
    recursive: bool = True,
    exclude: Iterable[str] = (
        "**/.gitattributes",
        "**/README.md",
        "**/tasks.jsonl",
    ),
) -> SimpleDirectoryReader:
    """Build the standard SimpleDirectoryReader wired with our JSON/CSV handlers."""
    return SimpleDirectoryReader(
        input_dir=str(input_dir),
        recursive=recursive,
        filename_as_id=True,
        exclude=list(exclude),
        exclude_hidden=True,
        file_extractor=dict(DEFAULT_FILE_EXTRACTOR),
    )
