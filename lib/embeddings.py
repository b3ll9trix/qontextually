from __future__ import annotations

import logging
import os
import random
import sqlite3
import struct
import time
from typing import Iterable, Sequence

import requests

from db.db import EMBED_DIM, get_connection, vec_available

log = logging.getLogger(__name__)

OPENROUTER_URL = os.environ.get(
    "QONTEXT_EMBED_URL", "https://openrouter.ai/api/v1/embeddings"
)
EMBED_MODEL = os.environ.get(
    "QONTEXT_EMBED_MODEL", "openai/text-embedding-3-small"
)
API_KEY_ENV = "OPENROUTER_API_KEY"

REQUEST_TIMEOUT_S = 30
MAX_RETRIES = 3
BACKOFF_BASE_S = 1.0


class EmbeddingError(RuntimeError):
    pass


class OutOfCreditsError(EmbeddingError):
    pass


def _api_key() -> str:
    key = os.environ.get(API_KEY_ENV)
    if not key:
        raise EmbeddingError(
            f"{API_KEY_ENV} not set. Add it to .env or export it in the shell."
        )
    return key


def _post_with_retries(payload: dict) -> dict:
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.post(
                OPENROUTER_URL, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_S
            )
        except requests.RequestException as exc:
            last_exc = exc
            if attempt == MAX_RETRIES:
                raise EmbeddingError(f"network error: {exc}") from exc
        else:
            if resp.status_code == 402:
                raise OutOfCreditsError(
                    "OpenRouter returned 402: out of credits. Top up at "
                    "https://openrouter.ai/credits"
                )
            if resp.status_code < 400:
                return resp.json()
            if resp.status_code in (429, 500, 502, 503, 504):
                last_exc = EmbeddingError(
                    f"HTTP {resp.status_code}: {resp.text[:200]}"
                )
            else:
                raise EmbeddingError(f"HTTP {resp.status_code}: {resp.text[:500]}")

        if attempt == MAX_RETRIES:
            break
        sleep_s = BACKOFF_BASE_S * (2 ** attempt) + random.uniform(0, 0.25)
        log.warning("embedding retry %d/%d in %.1fs", attempt + 1, MAX_RETRIES, sleep_s)
        time.sleep(sleep_s)

    raise EmbeddingError(f"exhausted retries: {last_exc}")


def embed_text(text: str | Sequence[str]) -> list[list[float]]:
    single = isinstance(text, str)
    inputs = [text] if single else list(text)
    if not inputs:
        return []

    data = _post_with_retries({"model": EMBED_MODEL, "input": inputs})
    items = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
    vectors = [list(item["embedding"]) for item in items]

    if len(vectors) != len(inputs):
        raise EmbeddingError(
            f"expected {len(inputs)} vectors, got {len(vectors)}"
        )
    return vectors


def _pack(vec: Sequence[float], dim: int) -> bytes:
    if len(vec) != dim:
        raise EmbeddingError(
            f"vector dim {len(vec)} does not match expected {dim} "
            f"(set QONTEXT_EMBED_DIM to change)"
        )
    return struct.pack(f"{dim}f", *vec)


def _write_one(
    conn: sqlite3.Connection,
    entity_id: str,
    vec: Sequence[float],
    *,
    dim: int,
    model: str,
    vec_ok: bool,
) -> None:
    blob = _pack(vec, dim)
    conn.execute(
        "INSERT OR REPLACE INTO entity_embeddings "
        "(entity_id, model, dim, embedding) VALUES (?, ?, ?, ?)",
        (entity_id, model, dim, blob),
    )
    if vec_ok:
        conn.execute("DELETE FROM entity_embeddings_vec WHERE entity_id = ?", (entity_id,))
        conn.execute(
            "INSERT INTO entity_embeddings_vec (entity_id, embedding) VALUES (?, ?)",
            (entity_id, blob),
        )


def embed_entity(entity_id: str, text: str, *, conn: sqlite3.Connection | None = None) -> None:
    owned = conn is None
    c = conn or get_connection()
    try:
        vec = embed_text(text)[0]
        with c:
            _write_one(
                c, entity_id, vec,
                dim=EMBED_DIM, model=EMBED_MODEL, vec_ok=vec_available()
            )
    finally:
        if owned:
            c.close()


def embed_entities_bulk(
    items: Iterable[tuple[str, str]],
    *,
    conn: sqlite3.Connection | None = None,
    batch_size: int = 64,
) -> int:
    owned = conn is None
    c = conn or get_connection()
    vec_ok = vec_available()
    total = 0
    batch: list[tuple[str, str]] = []

    def flush(rows: list[tuple[str, str]]) -> int:
        if not rows:
            return 0
        ids, texts = zip(*rows)
        vectors = embed_text(list(texts))
        with c:
            for eid, v in zip(ids, vectors):
                _write_one(
                    c, eid, v,
                    dim=EMBED_DIM, model=EMBED_MODEL, vec_ok=vec_ok,
                )
        return len(rows)

    try:
        for pair in items:
            batch.append(pair)
            if len(batch) >= batch_size:
                total += flush(batch)
                batch = []
        total += flush(batch)
    finally:
        if owned:
            c.close()
    return total
