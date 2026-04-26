from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import requests
from pydantic import ValidationError

from db.db import get_connection
from lib.prompts import render_system_prompt
from lib.schemas import ExtractionResult

log = logging.getLogger(__name__)

OPENROUTER_URL = os.environ.get(
    "QONTEXT_EXTRACT_URL", "https://openrouter.ai/api/v1/chat/completions"
)
PRIMARY_MODEL = os.environ.get("QONTEXT_EXTRACT_MODEL", "mistralai/mistral-nemo")
FALLBACK_MODEL = os.environ.get(
    "QONTEXT_EXTRACT_MODEL_FALLBACK", "qwen/qwen3-30b-a3b-instruct-2507"
)
STRICT_MODEL = os.environ.get(
    "QONTEXT_EXTRACT_MODEL_STRICT", "anthropic/claude-haiku-4.5"
)
REPLAY_DIR = os.environ.get("QONTEXT_REPLAY_DIR")
API_KEY_ENV = "OPENROUTER_API_KEY"

REQUEST_TIMEOUT_S = 120
MAX_HTTP_RETRIES = 3
BACKOFF_BASE_S = 1.0
MAX_TOKENS = 4096
TEMPERATURE = 0.1


class ExtractionError(RuntimeError):
    pass


class OutOfCreditsError(ExtractionError):
    pass


@dataclass
class ExtractionOutcome:
    """What actually happened during extraction \u2014 useful for audit_log."""

    result: Optional[ExtractionResult]
    model_used: Optional[str]
    attempts: list[dict] = field(default_factory=list)
    total_cost: float = 0.0
    total_tokens: int = 0

    @property
    def ok(self) -> bool:
        return self.result is not None


def _api_key() -> str:
    key = os.environ.get(API_KEY_ENV)
    if not key:
        raise ExtractionError(
            f"{API_KEY_ENV} not set. Add it to .env or export it in the shell."
        )
    return key


def _strip_unsupported_schema_keys(schema: dict) -> dict:
    """Anthropic rejects `minimum`/`maximum` on number types.

    Recursively drop those keys so Haiku accepts the schema as-is. The pydantic
    CHECK CONSTRAINTS on confidence still run client-side when we validate the
    response, so we don't lose enforcement.
    """
    if isinstance(schema, dict):
        return {
            k: _strip_unsupported_schema_keys(v)
            for k, v in schema.items()
            if k not in ("minimum", "maximum")
        }
    if isinstance(schema, list):
        return [_strip_unsupported_schema_keys(v) for v in schema]
    return schema


_EXTRACTION_SCHEMA = ExtractionResult.model_json_schema()
_EXTRACTION_SCHEMA_ANTHROPIC = _strip_unsupported_schema_keys(_EXTRACTION_SCHEMA)


def _response_format_for(model: str) -> dict:
    if model.startswith("anthropic/"):
        schema = _EXTRACTION_SCHEMA_ANTHROPIC
    else:
        schema = _EXTRACTION_SCHEMA
    return {
        "type": "json_schema",
        "json_schema": {"name": "ExtractionResult", "schema": schema, "strict": True},
    }


def _call_openrouter(
    model: str,
    messages: list[dict],
    *,
    response_format: Optional[dict] = None,
) -> dict:
    """Single HTTP call with retry on transient errors. Returns parsed JSON body."""
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/b3ll9trix/qontextually",
        "X-Title": "Qontextually",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
    }
    if response_format is not None:
        payload["response_format"] = response_format

    last_body = ""
    for attempt in range(MAX_HTTP_RETRIES + 1):
        try:
            resp = requests.post(
                OPENROUTER_URL, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_S
            )
        except requests.RequestException as exc:
            last_body = f"network: {exc}"
            if attempt == MAX_HTTP_RETRIES:
                raise ExtractionError(f"network error: {exc}") from exc
        else:
            if resp.status_code == 402:
                raise OutOfCreditsError(
                    "OpenRouter returned 402: out of credits. "
                    "Top up at https://openrouter.ai/credits"
                )
            if resp.status_code < 400:
                return resp.json()
            last_body = resp.text[:500]
            if resp.status_code in (408, 429, 500, 502, 503, 504):
                pass
            else:
                raise ExtractionError(
                    f"HTTP {resp.status_code} from {model}: {last_body}"
                )

        if attempt == MAX_HTTP_RETRIES:
            break
        sleep_s = BACKOFF_BASE_S * (2 ** attempt) + random.uniform(0, 0.25)
        log.warning(
            "extract retry %d/%d for %s in %.1fs (last: %s)",
            attempt + 1, MAX_HTTP_RETRIES, model, sleep_s, last_body[:120],
        )
        time.sleep(sleep_s)

    raise ExtractionError(f"exhausted retries for {model}: {last_body}")


def _attempt_model(
    model: str,
    *,
    prompt: str,
    previous_invalid: Optional[str] = None,
    previous_error: Optional[str] = None,
) -> tuple[Optional[ExtractionResult], dict]:
    """One call against one model, optionally with a corrective retry payload.

    Transport errors (bad model slug, 4xx/5xx after retries, auth failures) are
    caught here and converted into attempt_meta with ok=False, so the caller
    can continue cascading. OutOfCreditsError is the one exception \u2014 it
    re-raises because no fallback can help when the account is out of funds.
    """
    messages: list[dict] = [{"role": "system", "content": prompt}]
    if previous_invalid is not None:
        messages.extend([
            {"role": "assistant", "content": previous_invalid[:4000]},
            {
                "role": "user",
                "content": (
                    "Your previous response did not match the required schema: "
                    f"{previous_error}. Emit ONLY valid JSON that matches the schema. "
                    "Do not include any commentary outside the JSON."
                ),
            },
        ])

    attempt_meta: dict = {
        "model": model,
        "corrective_retry": previous_invalid is not None,
    }

    t0 = time.time()
    try:
        body = _call_openrouter(model, messages, response_format=_response_format_for(model))
    except OutOfCreditsError:
        raise
    except ExtractionError as exc:
        attempt_meta.update({
            "ok": False,
            "elapsed_s": round(time.time() - t0, 2),
            "tokens": 0,
            "cost": 0.0,
            "content_chars": 0,
            "error": f"transport: {str(exc)[:240]}",
        })
        return None, attempt_meta

    elapsed = time.time() - t0
    usage = body.get("usage", {}) or {}
    content = body["choices"][0]["message"].get("content", "")

    attempt_meta.update({
        "elapsed_s": round(elapsed, 2),
        "tokens": usage.get("total_tokens"),
        "cost": usage.get("cost") or 0.0,
        "content_chars": len(content),
    })

    try:
        result = ExtractionResult.model_validate_json(content)
        attempt_meta["ok"] = True
        return result, attempt_meta
    except ValidationError as exc:
        attempt_meta["ok"] = False
        attempt_meta["error"] = str(exc).splitlines()[0][:240]
        attempt_meta["raw_sample"] = content[:600]
        return None, attempt_meta


def _content_hash(chunk_text: str) -> str:
    return hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()[:16]


def _try_replay(
    chunk_text: str, chunk_meta: str
) -> Optional[tuple[ExtractionResult, dict]]:
    """Load a pre-recorded ExtractionResult if one exists for this chunk.

    Lookup order in QONTEXT_REPLAY_DIR:
      1. fixture keyed by exact content hash: <dir>/by_hash/<sha16>.json
      2. fixture named after the source file path: <dir>/<basename>.extraction.json

    Returns (result, attempt_meta) if a fixture hit, else None.
    """
    if not REPLAY_DIR:
        return None

    root = Path(REPLAY_DIR)
    if not root.exists():
        return None

    candidates: list[Path] = []
    h = _content_hash(chunk_text)
    candidates.append(root / "by_hash" / f"{h}.json")

    for token in chunk_meta.split():
        if token.startswith("file="):
            fname = token[len("file="):]
            candidates.append(root / f"{fname}.extraction.json")
            candidates.append(root / f"{Path(fname).stem}.extraction.json")
            break

    for path in candidates:
        if not path.exists():
            continue
        try:
            data = path.read_text(encoding="utf-8")
            result = ExtractionResult.model_validate_json(data)
        except Exception as exc:
            log.warning("replay fixture %s unreadable: %s", path, exc)
            continue
        meta = {
            "model": f"replay:{path.name}",
            "ok": True,
            "elapsed_s": 0.0,
            "tokens": 0,
            "cost": 0.0,
            "content_chars": len(data),
            "corrective_retry": False,
        }
        log.info("replay hit: %s", path)
        return result, meta

    return None


def extract_from_chunk(
    chunk_text: str,
    *,
    chunk_meta: str = "",
    entity_types: Optional[list[str]] = None,
    predicates: Optional[list[str]] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> ExtractionOutcome:
    """Run the full extraction chain on one chunk.

    Flow: replay fixture (if QONTEXT_REPLAY_DIR set and matching file exists)
          -> mistral-nemo (with one corrective retry)
          -> paid qwen
          -> haiku.
    Logs each attempt to audit_log with actor='extractor'.
    """
    outcome = ExtractionOutcome(result=None, model_used=None)

    replay_hit = _try_replay(chunk_text, chunk_meta)
    if replay_hit is not None:
        result, meta = replay_hit
        outcome.result = result
        outcome.model_used = meta["model"]
        outcome.attempts.append(meta)
        owned = conn is None
        c = conn or get_connection()
        try:
            c.execute(
                "INSERT INTO audit_log (actor, action, target_kind, target_id, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    "extractor",
                    "extract_chunk",
                    "source",
                    chunk_meta or None,
                    json.dumps(
                        {
                            "model_used": outcome.model_used,
                            "total_cost": 0.0,
                            "total_tokens": 0,
                            "attempts": outcome.attempts,
                            "replay": True,
                        }
                    ),
                ),
            )
            c.commit()
        finally:
            if owned:
                c.close()
        return outcome

    prompt = render_system_prompt(
        chunk_text,
        chunk_meta=chunk_meta or "(none)",
        entity_types=entity_types,
        predicates=predicates,
    )

    chain = [PRIMARY_MODEL, FALLBACK_MODEL, STRICT_MODEL]

    for i, model in enumerate(chain):
        result, meta = _attempt_model(model, prompt=prompt)
        outcome.attempts.append(meta)
        outcome.total_cost += meta.get("cost") or 0.0
        outcome.total_tokens += meta.get("tokens") or 0

        if result is not None:
            outcome.result = result
            outcome.model_used = model
            break

        if i == 0:
            raw = meta.get("raw_sample", "")
            err = meta.get("error", "schema mismatch")
            result_retry, meta_retry = _attempt_model(
                model, prompt=prompt, previous_invalid=raw, previous_error=err
            )
            outcome.attempts.append(meta_retry)
            outcome.total_cost += meta_retry.get("cost") or 0.0
            outcome.total_tokens += meta_retry.get("tokens") or 0
            if result_retry is not None:
                outcome.result = result_retry
                outcome.model_used = model
                break

    owned = conn is None
    c = conn or get_connection()
    try:
        c.execute(
            "INSERT INTO audit_log (actor, action, target_kind, target_id, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "extractor",
                "extract_chunk" if outcome.ok else "extract_chunk_failed",
                "source",
                chunk_meta or None,
                json.dumps(
                    {
                        "model_used": outcome.model_used,
                        "total_cost": outcome.total_cost,
                        "total_tokens": outcome.total_tokens,
                        "attempts": outcome.attempts,
                    }
                ),
            ),
        )
        c.commit()
    finally:
        if owned:
            c.close()

    return outcome


def extract_documents(
    docs: Iterable[tuple[str, str]],
    *,
    entity_types: Optional[list[str]] = None,
    predicates: Optional[list[str]] = None,
    conn: Optional[sqlite3.Connection] = None,
    stop_on_out_of_credits: bool = True,
) -> Iterable[tuple[str, ExtractionOutcome]]:
    """Yield (chunk_meta, outcome) for each input. Streams so callers can write as we go."""
    for chunk_meta, chunk_text in docs:
        try:
            outcome = extract_from_chunk(
                chunk_text,
                chunk_meta=chunk_meta,
                entity_types=entity_types,
                predicates=predicates,
                conn=conn,
            )
        except OutOfCreditsError:
            if stop_on_out_of_credits:
                raise
            outcome = ExtractionOutcome(result=None, model_used=None)
        yield chunk_meta, outcome
