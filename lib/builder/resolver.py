from __future__ import annotations

import argparse
import json
import logging
import struct
import sys
from dataclasses import dataclass, field
from typing import Optional

from db.db import EMBED_DIM, get_connection, vec_available
from lib.embeddings import embed_text

log = logging.getLogger(__name__)

AUTO_MERGE_THRESHOLD = 0.95
HUMAN_REVIEW_LOW = 0.75
SAMPLE_USAGE_TRIPLES = 3


@dataclass
class ResolutionSummary:
    """Per-run summary of what tiered predicate resolution did."""

    predicates_considered: int = 0
    predicates_embedded: int = 0
    predicates_auto_merged: int = 0
    predicates_queued_for_human: int = 0
    predicates_kept_as_canonical: int = 0
    triples_rewritten: int = 0
    merge_decisions: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def pretty(self) -> str:
        lines = [
            f"  predicates considered: {self.predicates_considered}",
            f"  predicates embedded:   {self.predicates_embedded}",
            f"  auto-merged:           {self.predicates_auto_merged}",
            f"  human-review queue:    {self.predicates_queued_for_human}",
            f"  kept as canonical:     {self.predicates_kept_as_canonical}",
            f"  triples rewritten:     {self.triples_rewritten}",
            f"  errors:                {len(self.errors)}",
        ]
        return "\n".join(lines)


def _sample_usage_context(conn, predicate: str, limit: int = SAMPLE_USAGE_TRIPLES) -> str:
    """Build a natural-language summary of how this predicate is used."""
    rows = conn.execute(
        """
        SELECT se.name AS subj, se.type AS subj_type,
               COALESCE(oe.name, t.object_value) AS obj,
               CASE WHEN t.object_is_entity=1 THEN oe.type ELSE 'literal' END AS obj_type
        FROM triples t
        JOIN entities se ON se.id = t.subject_id
        LEFT JOIN entities oe ON oe.id = t.object_id
        WHERE t.predicate = ?
        ORDER BY t.created_at DESC
        LIMIT ?
        """,
        (predicate, limit),
    ).fetchall()

    if not rows:
        return "no usage in graph yet."

    examples = [
        f"{r['subj']} ({r['subj_type']}) {predicate} {r['obj']} ({r['obj_type']})"
        for r in rows
    ]
    return " | ".join(examples)


def _pack_vector(vec: list[float], dim: int) -> bytes:
    return struct.pack(f"{dim}f", *vec)


def _unpack_vector(blob: bytes, dim: int) -> list[float]:
    return list(struct.unpack(f"{dim}f", blob))


def _build_embedding_input(predicate: str, description: str | None, usage: str) -> str:
    desc = description or "no description"
    return f"Predicate: {predicate}. Description: {desc}. Usage: {usage}"


def _embed_and_store_predicate(conn, predicate: str, dim: int, model: str) -> bool:
    """Compute embedding + usage context, write to both tables. True on success.

    Idempotent fast-path: if a row already exists in `predicate_embeddings` with
    the same (model, dim), skip the API call. Lets `resolve_all()` re-run cheaply
    after a threshold change without re-paying 2,631 embedding calls."""
    existing = conn.execute(
        "SELECT 1 FROM predicate_embeddings WHERE predicate = ? AND model = ? AND dim = ?",
        (predicate, model, dim),
    ).fetchone()
    if existing is not None:
        return True

    row = conn.execute(
        "SELECT description FROM predicates WHERE name = ?", (predicate,)
    ).fetchone()
    description = row["description"] if row else None
    usage = _sample_usage_context(conn, predicate)
    text = _build_embedding_input(predicate, description, usage)

    try:
        vecs = embed_text(text)
    except Exception as exc:
        log.warning("embedding failed for predicate %r: %s", predicate, exc)
        return False

    if not vecs or len(vecs[0]) != dim:
        log.warning("predicate %r got unexpected dim %d", predicate, len(vecs[0]) if vecs else -1)
        return False

    blob = _pack_vector(vecs[0], dim)
    conn.execute(
        """
        INSERT OR REPLACE INTO predicate_embeddings
          (predicate, model, dim, embedding, usage_context)
        VALUES (?, ?, ?, ?, ?)
        """,
        (predicate, model, dim, blob, usage),
    )
    if vec_available():
        conn.execute(
            "DELETE FROM predicate_embeddings_vec WHERE predicate = ?", (predicate,)
        )
        conn.execute(
            "INSERT INTO predicate_embeddings_vec (predicate, embedding) VALUES (?, ?)",
            (predicate, blob),
        )
    return True


def _find_best_canonical_match(
    conn, candidate: str, candidate_occurrences: int
) -> Optional[tuple[str, float]]:
    """KNN-search for the best canonical predicate to merge candidate into.

    Rules:
      - Only merges INTO a predicate that is currently canonical (canonical_name IS NULL)
      - Never merges into a predicate with fewer occurrences than candidate (prevents flip)
      - Returns (canonical, distance) where distance is sqlite-vec L2; caller converts.
    """
    if not vec_available():
        return None

    blob_row = conn.execute(
        "SELECT embedding FROM predicate_embeddings WHERE predicate = ?",
        (candidate,),
    ).fetchone()
    if blob_row is None:
        return None
    q = blob_row["embedding"]

    rows = conn.execute(
        """
        SELECT v.predicate AS predicate, v.distance AS distance
        FROM predicate_embeddings_vec v
        WHERE v.embedding MATCH ?
          AND k = 10
          AND v.predicate != ?
        """,
        (q, candidate),
    ).fetchall()

    seeded_match = None
    auto_match = None
    for r in rows:
        other = r["predicate"]
        pred_row = conn.execute(
            "SELECT canonical_name, occurrence_count, auto_added FROM predicates WHERE name = ?",
            (other,),
        ).fetchone()
        if pred_row is None:
            continue
        if pred_row["canonical_name"] is not None:
            continue
        if pred_row["occurrence_count"] < candidate_occurrences:
            continue
        if (
            pred_row["occurrence_count"] == candidate_occurrences
            and other >= candidate
        ):
            continue
        if pred_row["auto_added"] == 0 and seeded_match is None:
            seeded_match = (other, r["distance"])
        elif pred_row["auto_added"] == 1 and auto_match is None:
            auto_match = (other, r["distance"])
        if seeded_match is not None:
            break

    return seeded_match or auto_match


def _l2_to_cosine(l2_distance: float) -> float:
    """Approximate cosine sim for unit-length vectors: cos \u2248 1 - L2^2/2."""
    return max(0.0, min(1.0, 1.0 - (l2_distance ** 2) / 2.0))


def _merge_predicate(
    conn,
    *,
    from_pred: str,
    into_pred: str,
    method: str,
    confidence: float,
    source_of_decision: str,
) -> int:
    """Merge from_pred into into_pred. Returns count of triples rewritten."""
    cur = conn.execute(
        "UPDATE triples SET predicate = ? WHERE predicate = ?",
        (into_pred, from_pred),
    )
    n_rewritten = cur.rowcount

    conn.execute(
        "UPDATE predicates SET canonical_name = ?, merge_method = ? WHERE name = ?",
        (into_pred, method, from_pred),
    )

    conn.execute(
        """
        INSERT INTO predicate_merges
          (from_predicate, into_predicate, method, confidence, source_of_decision, affected_triples)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (from_pred, into_pred, method, confidence, source_of_decision, n_rewritten),
    )

    conn.execute(
        "UPDATE predicates SET occurrence_count = occurrence_count + ? WHERE name = ?",
        (n_rewritten, into_pred),
    )

    return n_rewritten


def resolve_all(
    *,
    dry_run: bool = False,
    only_auto_added: bool = True,
    min_occurrences: int = 1,
    conn=None,
) -> ResolutionSummary:
    """Run tiered predicate resolution over all candidates.

    Use this as a batch cleanup pass (e.g. after bulk ingest). For per-insert
    inline resolution during live extraction, see `resolve_one()`.

    Args:
      dry_run: if True, compute decisions but do not merge or write.
      only_auto_added: only consider auto_added=1 predicates as merge candidates.
      min_occurrences: skip predicates used fewer than N times.
    """
    summary = ResolutionSummary()
    owned = conn is None
    c = conn or get_connection()

    if not vec_available():
        summary.errors.append("sqlite-vec unavailable; cannot run tier-2 resolution")
        if owned:
            c.close()
        return summary

    candidates = list(c.execute(
        """
        SELECT name, occurrence_count
        FROM predicates
        WHERE (? = 0 OR auto_added = 1)
          AND canonical_name IS NULL
          AND occurrence_count >= ?
        ORDER BY occurrence_count ASC
        """,
        (1 if only_auto_added else 0, min_occurrences),
    ))

    canonicals = list(c.execute(
        """
        SELECT name, occurrence_count, description
        FROM predicates
        WHERE canonical_name IS NULL
          AND (auto_added = 0 OR occurrence_count > 0)
        """,
    ))

    summary.predicates_considered = len(candidates)

    log.info("predicate resolver: embedding %d canonicals + %d candidates", len(canonicals), len(candidates))
    seen_embedded: set[str] = set()

    from lib.embeddings import EMBED_MODEL

    for row in canonicals:
        if _embed_and_store_predicate(c, row["name"], EMBED_DIM, EMBED_MODEL):
            summary.predicates_embedded += 1
            seen_embedded.add(row["name"])
        if not dry_run:
            c.commit()

    for row in candidates:
        if row["name"] in seen_embedded:
            continue
        if _embed_and_store_predicate(c, row["name"], EMBED_DIM, EMBED_MODEL):
            summary.predicates_embedded += 1
        if not dry_run:
            c.commit()

    for row in candidates:
        cand = row["name"]
        occ = row["occurrence_count"]
        match = _find_best_canonical_match(c, cand, occ)

        if match is None:
            summary.predicates_kept_as_canonical += 1
            continue

        canonical, l2 = match
        cos = _l2_to_cosine(l2)
        decision: dict = {
            "from": cand,
            "into": canonical,
            "l2_distance": l2,
            "cosine": cos,
            "occurrence_count": occ,
        }

        if cos >= AUTO_MERGE_THRESHOLD:
            decision["action"] = "auto_merged"
            if not dry_run:
                n = _merge_predicate(
                    c,
                    from_pred=cand,
                    into_pred=canonical,
                    method="embedding",
                    confidence=cos,
                    source_of_decision=f"cosine={cos:.4f} l2={l2:.4f} threshold={AUTO_MERGE_THRESHOLD}",
                )
                summary.triples_rewritten += n
                decision["triples_rewritten"] = n
                c.commit()
            summary.predicates_auto_merged += 1
        elif cos >= HUMAN_REVIEW_LOW:
            decision["action"] = "human_review"
            summary.predicates_queued_for_human += 1
        else:
            decision["action"] = "kept_as_canonical"
            summary.predicates_kept_as_canonical += 1

        summary.merge_decisions.append(decision)

    if owned:
        c.close()
    return summary


def resolve_one(
    predicate: str,
    *,
    conn=None,
) -> Optional[dict]:
    """Resolve a single predicate inline. Designed for the writer to call when
    it encounters a new auto-discovered predicate during extraction.

    Cheaper than resolve_all() for the one-predicate case: one embedding API
    call, one KNN search, one optional merge. Returns a decision dict like
    resolve_all's merge_decisions entries (action, cosine, from, into) or
    None if resolution was skipped (e.g. vec unavailable, predicate is seeded).

    Note: only resolves predicates with canonical_name IS NULL and auto_added=1.
    Seeded predicates are never merged.
    """
    owned = conn is None
    c = conn or get_connection()

    if not vec_available():
        if owned:
            c.close()
        return None

    row = c.execute(
        "SELECT auto_added, canonical_name, occurrence_count FROM predicates WHERE name = ?",
        (predicate,),
    ).fetchone()
    if row is None or row["auto_added"] == 0 or row["canonical_name"] is not None:
        if owned:
            c.close()
        return None

    from lib.embeddings import EMBED_MODEL

    if not _embed_and_store_predicate(c, predicate, EMBED_DIM, EMBED_MODEL):
        if owned:
            c.close()
        return None

    occ = row["occurrence_count"] or 0
    match = _find_best_canonical_match(c, predicate, occ)
    if match is None:
        if owned:
            c.close()
        return {"from": predicate, "action": "kept_as_canonical"}

    canonical, l2 = match
    cos = _l2_to_cosine(l2)
    decision = {
        "from": predicate,
        "into": canonical,
        "l2_distance": l2,
        "cosine": cos,
        "occurrence_count": occ,
    }

    if cos >= AUTO_MERGE_THRESHOLD:
        n = _merge_predicate(
            c,
            from_pred=predicate,
            into_pred=canonical,
            method="embedding",
            confidence=cos,
            source_of_decision=f"cosine={cos:.4f} l2={l2:.4f} inline",
        )
        decision["action"] = "auto_merged"
        decision["triples_rewritten"] = n
        c.commit()
    elif cos >= HUMAN_REVIEW_LOW:
        decision["action"] = "human_review"
    else:
        decision["action"] = "kept_as_canonical"

    if owned:
        c.close()
    return decision


def main() -> int:
    parser = argparse.ArgumentParser(description="Run tiered predicate resolution")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--all", action="store_true", help="Consider seeded predicates too (default: auto_added only)")
    parser.add_argument("--min-occurrences", type=int, default=1)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)

    summary = resolve_all(
        dry_run=args.dry_run,
        only_auto_added=not args.all,
        min_occurrences=args.min_occurrences,
    )

    print("\n=== Predicate resolution summary ===")
    print(summary.pretty())

    if summary.merge_decisions:
        merged = [d for d in summary.merge_decisions if d["action"] == "auto_merged"]
        human = [d for d in summary.merge_decisions if d["action"] == "human_review"]
        print(f"\n=== Auto-merged (top 10) ===")
        for d in sorted(merged, key=lambda x: -x["cosine"])[:10]:
            triples = d.get("triples_rewritten", "?")
            print(f"  {d['from']:35s} -> {d['into']:20s}  cos={d['cosine']:.3f}  triples={triples}")
        if human:
            print(f"\n=== Queued for human review (top 10) ===")
            for d in sorted(human, key=lambda x: -x["cosine"])[:10]:
                print(f"  {d['from']:35s} ~ {d['into']:20s}  cos={d['cosine']:.3f}")

    return 0


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(".env")
    sys.exit(main())
