"""Sweep the existing graph for functional-predicate conflicts not yet in
the conflicts table. Writer now detects these automatically going forward;
this script catches the ones that were ingested before detection was added.

Capped at --max conflicts per predicate to keep the demo-relevant queue
manageable (the graph has ~10k+ natural functional-predicate disagreements
after 97.7% extraction; surfacing all of them would drown the reviewer).

Safe to re-run. Idempotent: skips pairs already in conflicts table.
"""
from __future__ import annotations

import argparse
import json
import sys

from db.db import get_connection
from lib.builder.writer import _source_score_for_triple


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-per-predicate", type=int, default=15, help="Cap conflicts per functional predicate")
    parser.add_argument("--only", nargs="*", help="Limit to specific predicate names (e.g. has_title reports_to)")
    args = parser.parse_args()

    conn = get_connection()

    before = conn.execute("SELECT COUNT(*) FROM conflicts").fetchone()[0]
    print(f"conflicts before backfill: {before}")

    functional_preds = [
        r["name"] for r in conn.execute(
            "SELECT name FROM predicates WHERE is_functional = 1"
        )
    ]
    if args.only:
        functional_preds = [p for p in functional_preds if p in args.only]
    print(f"scanning {len(functional_preds)} functional predicates (cap={args.max_per_predicate} each)...")

    existing_pairs = {
        tuple(sorted([r["a"], r["b"]]))
        for r in conn.execute(
            "SELECT triple_a_id AS a, triple_b_id AS b FROM conflicts WHERE conflict_type = 'fact'"
        )
    }

    created = 0
    for pred in functional_preds:
        rows = list(conn.execute(
            """
            SELECT subject_id, COUNT(DISTINCT COALESCE(object_id, object_value)) AS n
            FROM triples
            WHERE predicate = ? AND status = 'active'
            GROUP BY subject_id HAVING n > 1
            LIMIT ?
            """,
            (pred, args.max_per_predicate * 3),
        ))
        if not rows:
            continue

        pred_created = 0
        for subj in rows:
            if pred_created >= args.max_per_predicate:
                break
            triples = list(conn.execute(
                """
                SELECT t.id, t.object_id, t.object_value, t.object_is_entity
                FROM triples t
                WHERE t.subject_id = ? AND t.predicate = ? AND t.status = 'active'
                ORDER BY (SELECT COUNT(*) FROM triple_sources ts WHERE ts.triple_id = t.id) DESC
                LIMIT 2
                """,
                (subj["subject_id"], pred),
            ))
            if len(triples) < 2:
                continue

            a, b = triples[0], triples[1]
            a_val = a["object_id"] if a["object_is_entity"] else a["object_value"]
            b_val = b["object_id"] if b["object_is_entity"] else b["object_value"]
            if a_val == b_val:
                continue

            pair_key = tuple(sorted([a["id"], b["id"]]))
            if pair_key in existing_pairs:
                continue

            score_a = _source_score_for_triple(conn, a["id"])
            score_b = _source_score_for_triple(conn, b["id"])

            if score_a["total"] > score_b["total"]:
                hint_winner, hint_reason = "a", f"{score_a['source_type']} authority {score_a['authority']} beats {score_b['source_type']} {score_b['authority']}"
            elif score_b["total"] > score_a["total"]:
                hint_winner, hint_reason = "b", f"{score_b['source_type']} authority {score_b['authority']} beats {score_a['source_type']} {score_a['authority']}"
            else:
                hint_winner, hint_reason = None, "scores tied; human decision required"

            scores = {"a": score_a, "b": score_b, "hint": {"winner": hint_winner, "reason": hint_reason}}

            conn.execute(
                """
                INSERT INTO conflicts (conflict_type, triple_a_id, triple_b_id, status, scores_json)
                VALUES ('fact', ?, ?, 'pending', ?)
                """,
                (a["id"], b["id"], json.dumps(scores)),
            )
            conn.execute(
                "UPDATE triples SET status='conflicted' WHERE id IN (?, ?)",
                (a["id"], b["id"]),
            )
            existing_pairs.add(pair_key)
            created += 1
            pred_created += 1
        print(f"  {pred}: +{pred_created}")

    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM conflicts").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM conflicts WHERE status='pending'").fetchone()[0]
    print(f"conflicts after: {after}  (+{created} new)")
    print(f"pending: {pending}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
