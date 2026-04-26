"""Materialize pending conflict rows from naturally-occurring disagreements.

The graph already has cases where the same subject has two different values
for a functional predicate (e.g. different titles for the same Person across
HR record vs email). These are real conflicts; we just never populated the
`conflicts` table during ingest. This script fills that gap for the demo
without inventing any fake data.

Run: .venv/bin/python -m scripts.seed_demo_conflicts
"""
from __future__ import annotations

import json
import sys
from db.db import get_connection

AUTHORITY = {
    "hr": 1.0, "crm": 0.8, "policy": 0.7, "ticket": 0.5,
    "email": 0.4, "chat": 0.3, "unknown": 0.5,
}

FUNCTIONAL_PREDICATES = (
    "has_title",
    "works_at",
    "has_status",
    "reports_to",
    "has_email",
)

MAX_CONFLICTS = 12


def _best_source(conn, triple_id: int) -> dict:
    row = conn.execute(
        """
        SELECT s.source_type, s.extracted_at, ts.confidence
        FROM triple_sources ts JOIN sources s ON s.id = ts.source_id
        WHERE ts.triple_id = ?
        ORDER BY ts.extracted_at DESC LIMIT 1
        """,
        (triple_id,),
    ).fetchone()
    if row is None:
        return {"authority": 0.5, "confidence": 1.0, "recency": 1.0, "total": 0.5, "source_type": "unknown"}
    authority = AUTHORITY.get(row["source_type"], 0.5)
    confidence = row["confidence"] or 1.0
    recency = 0.9
    return {
        "authority": authority,
        "confidence": confidence,
        "recency": recency,
        "total": round(authority * confidence * recency, 3),
        "source_type": row["source_type"],
        "extracted_at": row["extracted_at"],
    }


def main() -> int:
    conn = get_connection()
    existing = conn.execute("SELECT COUNT(*) FROM conflicts").fetchone()[0]
    if existing > 0:
        print(f"conflicts table already has {existing} rows; skipping seed")
        return 0

    seeded = 0
    for predicate in FUNCTIONAL_PREDICATES:
        if seeded >= MAX_CONFLICTS:
            break

        rows = list(conn.execute(
            """
            SELECT t.subject_id, se.name AS subject_name, se.type AS subject_type
            FROM triples t
            JOIN entities se ON se.id = t.subject_id
            WHERE t.predicate = ? AND t.status = 'active'
            GROUP BY t.subject_id
            HAVING COUNT(DISTINCT COALESCE(t.object_id, t.object_value)) > 1
            LIMIT 20
            """,
            (predicate,),
        ))

        for subj_row in rows:
            if seeded >= MAX_CONFLICTS:
                break
            subj_id = subj_row["subject_id"]
            triples = list(conn.execute(
                """
                SELECT t.id, t.object_id, t.object_value, t.object_is_entity, oe.name AS obj_name
                FROM triples t
                LEFT JOIN entities oe ON oe.id = t.object_id
                WHERE t.subject_id = ? AND t.predicate = ? AND t.status = 'active'
                ORDER BY (SELECT COUNT(*) FROM triple_sources ts WHERE ts.triple_id = t.id) DESC
                LIMIT 2
                """,
                (subj_id, predicate),
            ))
            if len(triples) < 2:
                continue

            a, b = triples[0], triples[1]
            a_val = a["obj_name"] if a["object_is_entity"] else a["object_value"]
            b_val = b["obj_name"] if b["object_is_entity"] else b["object_value"]
            if a_val is None or b_val is None or a_val == b_val:
                continue

            score_a = _best_source(conn, a["id"])
            score_b = _best_source(conn, b["id"])

            if score_a["total"] > score_b["total"]:
                hint_winner, hint_reason = "a", f"{score_a['source_type']} authority {score_a['authority']} beats {score_b['source_type']} {score_b['authority']}"
            elif score_b["total"] > score_a["total"]:
                hint_winner, hint_reason = "b", f"{score_b['source_type']} authority {score_b['authority']} beats {score_a['source_type']} {score_a['authority']}"
            else:
                hint_winner, hint_reason = None, "scores tied; human decision required"

            scores = {"a": score_a, "b": score_b, "hint": {"winner": hint_winner, "reason": hint_reason}}

            conn.execute(
                """
                INSERT INTO conflicts
                  (conflict_type, triple_a_id, triple_b_id, status, scores_json)
                VALUES ('fact', ?, ?, 'pending', ?)
                """,
                (a["id"], b["id"], json.dumps(scores)),
            )
            conn.execute("UPDATE triples SET status='conflicted' WHERE id IN (?, ?)", (a["id"], b["id"]))

            seeded += 1
            print(f"  {subj_row['subject_name'][:40]:40s} {predicate:12s}  A={a_val!s:30.30s}  B={b_val!s:30.30s}  hint={hint_winner}")

    conn.commit()
    conn.close()
    print(f"\nSeeded {seeded} conflicts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
