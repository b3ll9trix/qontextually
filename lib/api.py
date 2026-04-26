"""FastAPI backend for Qontextually.

Serves two audiences: the Lovable UI (REST) and future MCP clients (via
lib/mcp_server.py which wraps this module's query helpers). All endpoints
read directly from the SQLite graph; writes (conflict resolve, predicate
merge/promote/dismiss) go through the same builder package used by ingest.

No auth. Local tool.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from db.db import DEFAULT_DB_PATH, get_connection
from lib.builder.resolver import _merge_predicate

log = logging.getLogger(__name__)


SOURCE_AUTHORITY: dict[str, float] = {
    "hr": 1.0,
    "crm": 0.8,
    "policy": 0.7,
    "ticket": 0.5,
    "email": 0.4,
    "chat": 0.3,
    "unknown": 0.5,
}


def _json_or_empty(s: Optional[str]) -> dict:
    if not s:
        return {}
    try:
        return json.loads(s)
    except Exception:
        return {}


def _snippet(text: Optional[str], limit: int = 200) -> str:
    if not text:
        return ""
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "\u2026"


app = FastAPI(title="Qontextually", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "db_path": DEFAULT_DB_PATH}


@app.get("/stats")
def stats() -> dict[str, Any]:
    conn = get_connection()
    try:
        entities_total = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        entities_by_type = [
            {"type": r["type"], "count": r["n"]}
            for r in conn.execute(
                "SELECT type, COUNT(*) AS n FROM entities GROUP BY type ORDER BY n DESC"
            )
        ]
        triples_total = conn.execute("SELECT COUNT(*) FROM triples WHERE status='active'").fetchone()[0]
        sources_total = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]

        pred_row = conn.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(auto_added = 0) AS seeded,
              SUM(auto_added = 1 AND canonical_name IS NULL) AS auto_canonical,
              SUM(canonical_name IS NOT NULL) AS merged
            FROM predicates
            """
        ).fetchone()

        conflicts_pending = conn.execute(
            "SELECT COUNT(*) FROM conflicts WHERE status='pending'"
        ).fetchone()[0]

        vocab_pending = conn.execute(
            "SELECT COUNT(*) FROM predicates WHERE auto_added=1 AND canonical_name IS NULL"
        ).fetchone()[0]

        ts_rows = conn.execute("SELECT COUNT(*) AS n FROM triple_sources").fetchone()["n"]
        avg_sources = round(ts_rows / triples_total, 2) if triples_total else 0.0

        last_extraction = conn.execute(
            "SELECT MAX(created_at) AS t FROM audit_log WHERE actor='extractor'"
        ).fetchone()["t"]

        return {
            "entities_total": entities_total,
            "entities_by_type": entities_by_type,
            "triples_total": triples_total,
            "sources_total": sources_total,
            "predicates_total": pred_row["total"] or 0,
            "predicates_seeded": pred_row["seeded"] or 0,
            "predicates_auto_canonical": pred_row["auto_canonical"] or 0,
            "predicates_merged": pred_row["merged"] or 0,
            "conflicts_pending": conflicts_pending,
            "vocabulary_pending_review": vocab_pending,
            "avg_sources_per_triple": avg_sources,
            "last_extraction_at": last_extraction,
        }
    finally:
        conn.close()


def _source_dict(row: sqlite3.Row, *, include_raw_text: bool = False) -> dict:
    props = _json_or_empty(row["properties_json"])
    out = {
        "source_id": row["id"],
        "document_path": row["document_path"],
        "source_type": row["source_type"],
        "authority": SOURCE_AUTHORITY.get(row["source_type"], 0.5),
        "extracted_at": row["extracted_at"],
        "snippet": _snippet(row["raw_text"]),
        "properties": props,
    }
    if include_raw_text:
        out["raw_text"] = row["raw_text"]
    return out


def _entity_summary(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "type": row["type"],
        "name": row["name"],
        "properties": _json_or_empty(row["properties_json"]),
    }


@app.get("/entities")
def list_entities(
    type: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Fuzzy match on name/aliases"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    conn = get_connection()
    try:
        where = ["e.status = 'active'"]
        params: list[Any] = []
        if type:
            where.append("e.type = ?")
            params.append(type)
        if q:
            where.append(
                """
                (lower(e.name) LIKE ? OR e.id IN (
                    SELECT entity_id FROM entity_aliases WHERE lower(alias) LIKE ?
                ))
                """
            )
            needle = f"%{q.lower()}%"
            params.extend([needle, needle])
        where_sql = " AND ".join(where)

        total = conn.execute(
            f"SELECT COUNT(*) FROM entities e WHERE {where_sql}", params
        ).fetchone()[0]

        rows = list(
            conn.execute(
                f"""
                SELECT e.id, e.type, e.name, e.properties_json
                FROM entities e
                WHERE {where_sql}
                ORDER BY e.name
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            )
        )

        if rows:
            ids = [r["id"] for r in rows]
            id_ph = ",".join("?" for _ in ids)

            counts: dict[str, int] = {r[0]: r[1] for r in conn.execute(
                f"""
                SELECT subject_id, COUNT(*) FROM triples
                WHERE subject_id IN ({id_ph}) AND status='active'
                GROUP BY subject_id
                """,
                ids,
            )}

            alias_map: dict[str, list[dict]] = {i: [] for i in ids}
            for a in conn.execute(
                f"""
                SELECT entity_id, alias, alias_type, is_primary
                FROM entity_aliases
                WHERE entity_id IN ({id_ph})
                ORDER BY is_primary DESC, confidence DESC
                """,
                ids,
            ):
                if len(alias_map[a["entity_id"]]) < 5:
                    alias_map[a["entity_id"]].append(
                        {"alias": a["alias"], "alias_type": a["alias_type"]}
                    )
        else:
            counts = {}
            alias_map = {}

        items = []
        for r in rows:
            summary = _entity_summary(r)
            summary["triple_count"] = counts.get(r["id"], 0)
            summary["aliases"] = alias_map.get(r["id"], [])
            items.append(summary)

        return {"total": total, "items": items}
    finally:
        conn.close()


@app.get("/entities/{entity_id}")
def get_entity(entity_id: str) -> dict[str, Any]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, type, name, properties_json FROM entities WHERE id = ?",
            (entity_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"entity {entity_id} not found")

        out = _entity_summary(row)
        out["aliases"] = [
            {"alias": a["alias"], "alias_type": a["alias_type"], "is_primary": bool(a["is_primary"])}
            for a in conn.execute(
                "SELECT alias, alias_type, is_primary FROM entity_aliases WHERE entity_id = ? ORDER BY is_primary DESC, confidence DESC",
                (entity_id,),
            )
        ]

        out["outgoing_triples"] = [
            {
                "triple_id": t["id"],
                "predicate": t["predicate"],
                "object_is_entity": bool(t["object_is_entity"]),
                "object_id": t["object_id"],
                "object_name": t["obj_name"],
                "object_value": t["object_value"],
                "source_count": t["src_count"],
                "status": t["status"],
            }
            for t in conn.execute(
                """
                SELECT t.id, t.predicate, t.object_is_entity, t.object_id, t.object_value, t.status,
                       oe.name AS obj_name,
                       (SELECT COUNT(*) FROM triple_sources ts WHERE ts.triple_id = t.id) AS src_count
                FROM triples t
                LEFT JOIN entities oe ON oe.id = t.object_id
                WHERE t.subject_id = ? AND t.status = 'active'
                ORDER BY src_count DESC, t.id
                LIMIT 200
                """,
                (entity_id,),
            )
        ]

        out["incoming_triples"] = [
            {
                "triple_id": t["id"],
                "subject_id": t["subject_id"],
                "subject_name": t["subj_name"],
                "predicate": t["predicate"],
                "status": t["status"],
            }
            for t in conn.execute(
                """
                SELECT t.id, t.subject_id, t.predicate, t.status, se.name AS subj_name
                FROM triples t
                JOIN entities se ON se.id = t.subject_id
                WHERE t.object_id = ? AND t.object_is_entity = 1 AND t.status = 'active'
                ORDER BY t.id LIMIT 200
                """,
                (entity_id,),
            )
        ]

        return out
    finally:
        conn.close()


@app.get("/triples/{triple_id}/provenance")
def triple_provenance(triple_id: int) -> dict[str, Any]:
    conn = get_connection()
    try:
        t = conn.execute(
            """
            SELECT t.id, t.subject_id, t.predicate, t.object_id, t.object_value, t.object_is_entity,
                   se.name AS subj_name, se.type AS subj_type,
                   oe.name AS obj_name, oe.type AS obj_type
            FROM triples t
            JOIN entities se ON se.id = t.subject_id
            LEFT JOIN entities oe ON oe.id = t.object_id
            WHERE t.id = ?
            """,
            (triple_id,),
        ).fetchone()
        if t is None:
            raise HTTPException(404, f"triple {triple_id} not found")

        sources = []
        for r in conn.execute(
            """
            SELECT s.id, s.document_path, s.source_type, s.extracted_at, s.raw_text, s.properties_json,
                   ts.confidence
            FROM triple_sources ts
            JOIN sources s ON s.id = ts.source_id
            WHERE ts.triple_id = ?
            ORDER BY ts.extracted_at ASC
            """,
            (triple_id,),
        ):
            sources.append(
                {
                    "source_id": r["id"],
                    "document_path": r["document_path"],
                    "source_type": r["source_type"],
                    "authority": SOURCE_AUTHORITY.get(r["source_type"], 0.5),
                    "confidence": r["confidence"],
                    "extracted_at": r["extracted_at"],
                    "raw_text": r["raw_text"],
                    "snippet_around_fact": _snippet(r["raw_text"], 400),
                }
            )

        return {
            "triple_id": t["id"],
            "subject": {"id": t["subject_id"], "name": t["subj_name"], "type": t["subj_type"]},
            "predicate": t["predicate"],
            "object": (
                {"id": t["object_id"], "name": t["obj_name"], "type": t["obj_type"]}
                if t["object_is_entity"]
                else None
            ),
            "object_value": t["object_value"],
            "object_is_entity": bool(t["object_is_entity"]),
            "sources": sources,
        }
    finally:
        conn.close()


@app.get("/conflicts")
def list_conflicts(
    status: str = Query("pending"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    conn = get_connection()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM conflicts WHERE status = ?", (status,)
        ).fetchone()[0]

        items = []
        rows = list(
            conn.execute(
                """
                SELECT c.id, c.conflict_type, c.triple_a_id, c.triple_b_id,
                       c.entity_a_id, c.entity_b_id, c.scores_json, c.created_at,
                       c.winner_ref, c.resolution_note
                FROM conflicts c
                WHERE c.status = ?
                ORDER BY c.created_at DESC
                LIMIT ? OFFSET ?
                """,
                (status, limit, offset),
            )
        )

        for c in rows:
            scores = _json_or_empty(c["scores_json"])
            item = {
                "conflict_id": c["id"],
                "conflict_type": c["conflict_type"],
                "created_at": c["created_at"],
                "winner_ref": c["winner_ref"],
                "resolution_note": c["resolution_note"],
            }

            if c["conflict_type"] == "fact":
                a = _candidate_detail(conn, c["triple_a_id"]) if c["triple_a_id"] else None
                b = _candidate_detail(conn, c["triple_b_id"]) if c["triple_b_id"] else None
                subject_entity = None
                if a and a["triple_id"]:
                    subj_row = conn.execute(
                        """
                        SELECT se.id, se.name, se.type FROM triples t JOIN entities se ON se.id = t.subject_id
                        WHERE t.id = ?
                        """,
                        (a["triple_id"],),
                    ).fetchone()
                    if subj_row:
                        subject_entity = dict(subj_row)
                predicate = None
                if c["triple_a_id"]:
                    pr = conn.execute(
                        "SELECT predicate FROM triples WHERE id = ?", (c["triple_a_id"],)
                    ).fetchone()
                    if pr:
                        predicate = pr["predicate"]
                sb_a = scores.get("a") if isinstance(scores, dict) else None
                sb_b = scores.get("b") if isinstance(scores, dict) else None
                if a is not None:
                    a["score_breakdown"] = sb_a
                if b is not None:
                    b["score_breakdown"] = sb_b
                item["subject_entity"] = subject_entity
                item["predicate"] = predicate
                item["candidate_a"] = a
                item["candidate_b"] = b
                item["score_breakdown_a"] = sb_a
                item["score_breakdown_b"] = sb_b
                item["auto_resolution_hint"] = scores.get("hint") if isinstance(scores, dict) else None

            else:
                item["entity_a_id"] = c["entity_a_id"]
                item["entity_b_id"] = c["entity_b_id"]

            items.append(item)

        return {"total": total, "items": items}
    finally:
        conn.close()


def _candidate_detail(conn: sqlite3.Connection, triple_id: int) -> dict:
    t = conn.execute(
        """
        SELECT t.id, t.predicate, t.object_id, t.object_value, t.object_is_entity,
               oe.name AS obj_name
        FROM triples t
        LEFT JOIN entities oe ON oe.id = t.object_id
        WHERE t.id = ?
        """,
        (triple_id,),
    ).fetchone()
    if t is None:
        return {}

    value = t["obj_name"] if t["object_is_entity"] else t["object_value"]
    sources = []
    for r in conn.execute(
        """
        SELECT s.id, s.document_path, s.source_type, s.extracted_at, s.raw_text,
               ts.confidence
        FROM triple_sources ts JOIN sources s ON s.id = ts.source_id
        WHERE ts.triple_id = ? ORDER BY ts.extracted_at ASC
        """,
        (triple_id,),
    ):
        sources.append(
            {
                "source_id": r["id"],
                "document_path": r["document_path"],
                "source_type": r["source_type"],
                "authority": SOURCE_AUTHORITY.get(r["source_type"], 0.5),
                "confidence": r["confidence"],
                "extracted_at": r["extracted_at"],
                "snippet": _snippet(r["raw_text"]),
            }
        )

    return {
        "triple_id": t["id"],
        "value": value,
        "object_is_entity": bool(t["object_is_entity"]),
        "sources": sources,
    }


class ConflictResolveBody(BaseModel):
    winner: str
    note: Optional[str] = None


@app.post("/conflicts/{conflict_id}/resolve")
def resolve_conflict(conflict_id: int, body: ConflictResolveBody) -> dict[str, Any]:
    if body.winner not in ("a", "b", "neither"):
        raise HTTPException(400, "winner must be 'a', 'b', or 'neither'")

    conn = get_connection()
    try:
        c = conn.execute(
            "SELECT id, triple_a_id, triple_b_id FROM conflicts WHERE id = ?", (conflict_id,)
        ).fetchone()
        if c is None:
            raise HTTPException(404, f"conflict {conflict_id} not found")

        conn.execute("BEGIN")
        conn.execute(
            """
            UPDATE conflicts
            SET status='human_resolved', winner_ref=?, resolution_note=?, resolved_at=datetime('now')
            WHERE id = ?
            """,
            (body.winner, body.note, conflict_id),
        )

        if body.winner == "a" and c["triple_b_id"]:
            conn.execute("UPDATE triples SET status='superseded' WHERE id = ?", (c["triple_b_id"],))
            if c["triple_a_id"]:
                conn.execute("UPDATE triples SET status='active' WHERE id = ?", (c["triple_a_id"],))
        elif body.winner == "b" and c["triple_a_id"]:
            conn.execute("UPDATE triples SET status='superseded' WHERE id = ?", (c["triple_a_id"],))
            if c["triple_b_id"]:
                conn.execute("UPDATE triples SET status='active' WHERE id = ?", (c["triple_b_id"],))
        elif body.winner == "neither":
            for tid in (c["triple_a_id"], c["triple_b_id"]):
                if tid:
                    conn.execute("UPDATE triples SET status='retracted' WHERE id = ?", (tid,))

        conn.execute(
            "INSERT INTO audit_log (actor, action, target_kind, target_id, payload_json) VALUES (?,?,?,?,?)",
            ("human", "resolve_conflict", "conflict", str(conflict_id), json.dumps(body.dict())),
        )
        conn.commit()

        return {"conflict_id": conflict_id, "status": "human_resolved", "winner": body.winner}
    finally:
        conn.close()


@app.get("/vocabulary/discovered")
def vocabulary_discovered(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    min_occurrences: int = Query(1, ge=0),
) -> dict[str, Any]:
    conn = get_connection()
    try:
        total = conn.execute(
            """
            SELECT COUNT(*) FROM predicates
            WHERE auto_added = 1 AND canonical_name IS NULL AND occurrence_count >= ?
            """,
            (min_occurrences,),
        ).fetchone()[0]

        rows = list(
            conn.execute(
                """
                SELECT name AS predicate, occurrence_count, description
                FROM predicates
                WHERE auto_added = 1 AND canonical_name IS NULL AND occurrence_count >= ?
                ORDER BY occurrence_count DESC LIMIT ? OFFSET ?
                """,
                (min_occurrences, limit, offset),
            )
        )

        predicates = [r["predicate"] for r in rows]
        samples_by_pred: dict[str, list[dict]] = {p: [] for p in predicates}
        nearest_by_pred: dict[str, Optional[dict]] = {p: None for p in predicates}

        if predicates:
            placeholders = ",".join("?" for _ in predicates)
            for s in conn.execute(
                f"""
                SELECT t.predicate, se.name AS subj, COALESCE(oe.name, t.object_value) AS obj,
                       ROW_NUMBER() OVER (PARTITION BY t.predicate ORDER BY t.created_at DESC) AS rn
                FROM triples t
                JOIN entities se ON se.id = t.subject_id
                LEFT JOIN entities oe ON oe.id = t.object_id
                WHERE t.predicate IN ({placeholders}) AND t.status='active'
                """,
                predicates,
            ):
                if s["rn"] <= 3 and len(samples_by_pred[s["predicate"]]) < 3:
                    samples_by_pred[s["predicate"]].append(
                        {"subject_name": s["subj"], "object": s["obj"]}
                    )

            for pm in conn.execute(
                f"""
                SELECT pm.from_predicate, pm.into_predicate, pm.confidence, p.occurrence_count
                FROM predicate_merges pm
                LEFT JOIN predicates p ON p.name = pm.into_predicate
                WHERE pm.from_predicate IN ({placeholders})
                """,
                predicates,
            ):
                if nearest_by_pred.get(pm["from_predicate"]) is None:
                    nearest_by_pred[pm["from_predicate"]] = {
                        "predicate": pm["into_predicate"],
                        "cosine": pm["confidence"],
                        "occurrence_count": pm["occurrence_count"] or 0,
                    }

        items = []
        for r in rows:
            pred = r["predicate"]
            items.append(
                {
                    "predicate": pred,
                    "occurrence_count": r["occurrence_count"],
                    "last_used": None,
                    "sample_triples": samples_by_pred[pred],
                    "nearest_canonical": nearest_by_pred[pred],
                    "description": r["description"],
                }
            )

        return {"total": total, "items": items}
    finally:
        conn.close()


class VocabMergeBody(BaseModel):
    into: str
    reason: Optional[str] = None


@app.post("/vocabulary/{predicate}/merge")
def vocabulary_merge(predicate: str, body: VocabMergeBody) -> dict[str, Any]:
    conn = get_connection()
    try:
        src = conn.execute("SELECT name FROM predicates WHERE name = ?", (predicate,)).fetchone()
        dst = conn.execute("SELECT name FROM predicates WHERE name = ?", (body.into,)).fetchone()
        if src is None:
            raise HTTPException(404, f"predicate {predicate} not found")
        if dst is None:
            raise HTTPException(404, f"target predicate {body.into} not found")

        conn.execute("BEGIN")
        n_rewritten = _merge_predicate(
            conn,
            from_pred=predicate,
            into_pred=body.into,
            method="human",
            confidence=1.0,
            source_of_decision=body.reason or "human merge via API",
        )
        conn.execute(
            "INSERT INTO audit_log (actor, action, target_kind, target_id, payload_json) VALUES (?,?,?,?,?)",
            ("human", "merge_predicate", "predicate", predicate, json.dumps({"into": body.into, "triples_rewritten": n_rewritten, "reason": body.reason})),
        )
        conn.commit()
        return {"triples_rewritten": n_rewritten, "merged_at": conn.execute("SELECT datetime('now')").fetchone()[0]}
    finally:
        conn.close()


class VocabPromoteBody(BaseModel):
    is_functional: bool = False
    description: Optional[str] = None


@app.post("/vocabulary/{predicate}/promote")
def vocabulary_promote(predicate: str, body: VocabPromoteBody) -> dict[str, Any]:
    conn = get_connection()
    try:
        row = conn.execute("SELECT name FROM predicates WHERE name = ?", (predicate,)).fetchone()
        if row is None:
            raise HTTPException(404, f"predicate {predicate} not found")

        conn.execute("BEGIN")
        updates = ["auto_added = 0"]
        params: list[Any] = []
        if body.is_functional:
            updates.append("is_functional = 1")
        if body.description:
            updates.append("description = ?")
            params.append(body.description)
        params.append(predicate)
        conn.execute(f"UPDATE predicates SET {', '.join(updates)} WHERE name = ?", params)
        conn.execute(
            "INSERT INTO audit_log (actor, action, target_kind, target_id, payload_json) VALUES (?,?,?,?,?)",
            ("human", "promote_predicate", "predicate", predicate, json.dumps(body.dict())),
        )
        conn.commit()
        return {"predicate": predicate, "promoted": True}
    finally:
        conn.close()


class VocabDismissBody(BaseModel):
    reason: Optional[str] = None


@app.post("/vocabulary/{predicate}/dismiss")
def vocabulary_dismiss(predicate: str, body: VocabDismissBody) -> dict[str, Any]:
    conn = get_connection()
    try:
        row = conn.execute("SELECT name FROM predicates WHERE name = ?", (predicate,)).fetchone()
        if row is None:
            raise HTTPException(404, f"predicate {predicate} not found")

        conn.execute("BEGIN")
        conn.execute("UPDATE predicates SET auto_added = 0 WHERE name = ?", (predicate,))
        conn.execute(
            "INSERT INTO audit_log (actor, action, target_kind, target_id, payload_json) VALUES (?,?,?,?,?)",
            ("human", "dismiss_predicate", "predicate", predicate, json.dumps({"reason": body.reason})),
        )
        conn.commit()
        return {"predicate": predicate, "dismissed": True}
    finally:
        conn.close()


@app.get("/sources")
def list_sources(
    source_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    conn = get_connection()
    try:
        where = []
        params: list[Any] = []
        if source_type:
            where.append("source_type = ?")
            params.append(source_type)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""

        total = conn.execute(
            f"SELECT COUNT(*) FROM sources {where_sql}", params
        ).fetchone()[0]

        rows = list(
            conn.execute(
                f"""
                SELECT s.id, s.document_path, s.source_type, s.extracted_at, s.raw_text, s.properties_json,
                       (SELECT COUNT(*) FROM triple_sources ts WHERE ts.source_id = s.id) AS triple_count
                FROM sources s
                {where_sql}
                ORDER BY s.extracted_at DESC
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            )
        )

        items = []
        for r in rows:
            items.append(
                {
                    "source_id": r["id"],
                    "document_path": r["document_path"],
                    "source_type": r["source_type"],
                    "authority": SOURCE_AUTHORITY.get(r["source_type"], 0.5),
                    "extracted_at": r["extracted_at"],
                    "triple_count": r["triple_count"],
                    "snippet": _snippet(r["raw_text"]),
                }
            )

        return {"total": total, "items": items}
    finally:
        conn.close()


@app.get("/sources/{source_id}")
def get_source(source_id: int) -> dict[str, Any]:
    conn = get_connection()
    try:
        r = conn.execute(
            "SELECT id, document_path, source_type, extracted_at, raw_text, properties_json FROM sources WHERE id = ?",
            (source_id,),
        ).fetchone()
        if r is None:
            raise HTTPException(404, f"source {source_id} not found")

        triples = []
        entity_ids: set[str] = set()
        for t in conn.execute(
            """
            SELECT t.id, t.predicate, t.subject_id, se.name AS subj_name, t.object_id, oe.name AS obj_name, t.object_value, t.object_is_entity
            FROM triple_sources ts
            JOIN triples t ON t.id = ts.triple_id
            JOIN entities se ON se.id = t.subject_id
            LEFT JOIN entities oe ON oe.id = t.object_id
            WHERE ts.source_id = ?
            """,
            (source_id,),
        ):
            triples.append(
                {
                    "triple_id": t["id"],
                    "subject_id": t["subject_id"],
                    "subject_name": t["subj_name"],
                    "predicate": t["predicate"],
                    "object_display": t["obj_name"] if t["object_is_entity"] else t["object_value"],
                }
            )
            entity_ids.add(t["subject_id"])
            if t["object_id"]:
                entity_ids.add(t["object_id"])

        entities = []
        if entity_ids:
            placeholders = ",".join("?" for _ in entity_ids)
            for e in conn.execute(
                f"SELECT id, name, type FROM entities WHERE id IN ({placeholders})",
                list(entity_ids),
            ):
                entities.append({"id": e["id"], "name": e["name"], "type": e["type"]})

        return {
            "source_id": r["id"],
            "document_path": r["document_path"],
            "source_type": r["source_type"],
            "authority": SOURCE_AUTHORITY.get(r["source_type"], 0.5),
            "extracted_at": r["extracted_at"],
            "properties": _json_or_empty(r["properties_json"]),
            "raw_text": r["raw_text"],
            "contributed_triples": triples,
            "contributed_entities": entities,
        }
    finally:
        conn.close()


@app.get("/graph/subgraph")
def subgraph(
    center: Optional[str] = Query(None),
    depth: int = Query(2, ge=1, le=3),
    max_nodes: int = Query(300, ge=10, le=500),
) -> dict[str, Any]:
    """Pre-sampled subgraph. Capped at max_nodes; use center to focus.

    Without center: picks top-N entities by degree, computes degrees from
    triples once (GROUP BY), then fetches connecting edges. Uses one-shot
    SQL rather than per-node subqueries.
    """
    conn = get_connection()
    try:
        if center:
            node_ids: set[str] = {center}
            for _ in range(depth):
                if len(node_ids) >= max_nodes:
                    break
                placeholders = ",".join("?" for _ in node_ids)
                new_ids: set[str] = set()
                for row in conn.execute(
                    f"""
                    SELECT t.object_id AS other FROM triples t
                    WHERE t.subject_id IN ({placeholders}) AND t.object_is_entity = 1 AND t.status='active'
                    UNION
                    SELECT t.subject_id AS other FROM triples t
                    WHERE t.object_id IN ({placeholders}) AND t.object_is_entity = 1 AND t.status='active'
                    """,
                    (*node_ids, *node_ids),
                ):
                    if row["other"] and row["other"] not in node_ids:
                        new_ids.add(row["other"])
                remaining = max_nodes - len(node_ids)
                node_ids.update(list(new_ids)[:remaining])
                if not new_ids:
                    break
        else:
            rows = list(
                conn.execute(
                    """
                    SELECT e.id, e.name, e.type, d.degree
                    FROM (
                        SELECT subject_id AS id, COUNT(*) AS degree
                        FROM triples WHERE status='active' GROUP BY subject_id
                    ) d
                    JOIN entities e ON e.id = d.id
                    WHERE e.status='active'
                    ORDER BY d.degree DESC LIMIT ?
                    """,
                    (max_nodes,),
                )
            )
            node_ids = {r["id"] for r in rows}

        if not node_ids:
            total_in_graph = conn.execute("SELECT COUNT(*) FROM entities WHERE status='active'").fetchone()[0]
            return {"nodes": [], "edges": [], "meta": {"total_nodes_in_graph": total_in_graph, "sampled_nodes": 0, "sampled_edges": 0}}

        placeholders = ",".join("?" for _ in node_ids)
        nodes = {}
        for e in conn.execute(
            f"SELECT id, name, type FROM entities WHERE id IN ({placeholders})",
            list(node_ids),
        ):
            nodes[e["id"]] = {
                "id": e["id"],
                "name": e["name"],
                "type": e["type"],
                "degree": 0,
                "is_center": (center is not None and e["id"] == center),
            }

        edges = []
        for r in conn.execute(
            f"""
            SELECT t.subject_id AS source, t.object_id AS target, t.predicate,
                   COUNT(ts.source_id) AS src_count
            FROM triples t
            LEFT JOIN triple_sources ts ON ts.triple_id = t.id
            WHERE t.subject_id IN ({placeholders})
              AND t.object_id IN ({placeholders})
              AND t.object_is_entity = 1
              AND t.status='active'
            GROUP BY t.id
            """,
            (*node_ids, *node_ids),
        ):
            edges.append({"source": r["source"], "target": r["target"], "predicate": r["predicate"], "source_count": r["src_count"]})
            if r["source"] in nodes:
                nodes[r["source"]]["degree"] += 1
            if r["target"] in nodes:
                nodes[r["target"]]["degree"] += 1

        total_in_graph = conn.execute("SELECT COUNT(*) FROM entities WHERE status='active'").fetchone()[0]

        return {
            "nodes": list(nodes.values()),
            "edges": edges,
            "meta": {
                "total_nodes_in_graph": total_in_graph,
                "sampled_nodes": len(nodes),
                "sampled_edges": len(edges),
            },
        }
    finally:
        conn.close()
