from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.environ.get(
    "QONTEXT_DB", str(Path(__file__).resolve().parent / "qontextually.db")
)

EMBED_DIM = int(os.environ.get("QONTEXT_EMBED_DIM", "1536"))

_vec_available: bool | None = None


def _try_load_sqlite_vec(conn: sqlite3.Connection) -> bool:
    try:
        import sqlite_vec
    except ImportError:
        return False

    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except (sqlite3.OperationalError, AttributeError) as exc:
        log.warning("sqlite-vec present but failed to load: %s", exc)
        return False


def _ensure_vec_table(conn: sqlite3.Connection, dim: int) -> None:
    conn.execute(
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS entity_embeddings_vec
        USING vec0(
            entity_id TEXT PRIMARY KEY,
            embedding FLOAT[{dim}]
        )
        """
    )
    conn.execute(
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS predicate_embeddings_vec
        USING vec0(
            predicate TEXT PRIMARY KEY,
            embedding FLOAT[{dim}]
        )
        """
    )


def get_connection(
    db_path: str | None = None, *, dim: int = EMBED_DIM
) -> sqlite3.Connection:
    global _vec_available

    path = db_path or DEFAULT_DB_PATH
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row

    vec_loaded = _try_load_sqlite_vec(conn)

    if vec_loaded:
        try:
            _ensure_vec_table(conn, dim)
        except sqlite3.OperationalError as exc:
            log.warning(
                "sqlite-vec loaded but CREATE VIRTUAL TABLE failed: %s. "
                "KNN search disabled for this connection.",
                exc,
            )
            vec_loaded = False

    if _vec_available is None:
        if vec_loaded:
            log.info("sqlite-vec loaded — KNN similarity enabled (dim=%d)", dim)
        else:
            log.warning(
                "sqlite-vec unavailable — Tier 2 similarity resolution will "
                "fall back to exact match only. Install with: pip install sqlite-vec"
            )
        _vec_available = vec_loaded

    return conn


def vec_available() -> bool:
    if _vec_available is None:
        get_connection().close()
    return bool(_vec_available)
