from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from db.db import get_connection
from lib.schemas import Entity, ExtractionResult, Triple

log = logging.getLogger(__name__)


@dataclass
class WriteSummary:
    """What happened when we wrote one ExtractionResult to the DB."""

    source_id: Optional[int] = None
    entities_inserted: int = 0
    entities_matched: int = 0
    aliases_added: int = 0
    triples_inserted: int = 0
    triples_linked_to_existing: int = 0
    conflicts_created: int = 0
    new_predicates: list[str] = field(default_factory=list)
    new_entity_types: list[str] = field(default_factory=list)
    predicates_inline_merged: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _new_entity_id() -> str:
    return f"e_{uuid.uuid4().hex[:12]}"


ENTITY_TYPE_COERCIONS: dict[str, str] = {
    "Employee": "Person",
    "Staff": "Person",
    "Human": "Person",
    "Customer": "Person",
    "Contact": "Person",
    "User": "Person",
    "Company": "Organization",
    "Team": "Organization",
    "Department": "Organization",
    "Group": "Organization",
    "Client": "Organization",
    "Vendor": "Organization",
}


def _canonical_entity_type(raw: str) -> str:
    """Coerce LLM-coined near-synonyms to the seeded type.

    Merges Employee/Staff/Human/Customer into Person, Company/Team/Department
    into Organization. Keeps rare, genuinely-distinct types (Ticket, Policy,
    Project, Event) untouched.
    """
    s = raw.strip()
    return ENTITY_TYPE_COERCIONS.get(s, s)


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _relativize(document_path: str) -> str:
    """If document_path is absolute and inside the project, store as repo-relative
    so the DB stays portable across machines and clones. Anything else
    (already-relative, or outside the project) is stored as-is."""
    if not document_path.startswith("/"):
        return document_path
    try:
        return str(Path(document_path).resolve().relative_to(_PROJECT_ROOT))
    except ValueError:
        return document_path


def _upsert_source(
    conn: sqlite3.Connection,
    *,
    document_path: str,
    source_type: str,
    raw_text: str,
    properties: dict,
) -> int:
    cur = conn.execute(
        "INSERT INTO sources (document_path, source_type, raw_text, properties_json) "
        "VALUES (?, ?, ?, ?)",
        (_relativize(document_path), source_type, raw_text, json.dumps(properties, ensure_ascii=False)),
    )
    return cur.lastrowid


def _ensure_entity_type(
    conn: sqlite3.Connection, etype: str, summary: WriteSummary
) -> None:
    """Register a new entity type if not already present."""
    existing = conn.execute(
        "SELECT 1 FROM entity_types WHERE type = ?", (etype,)
    ).fetchone()
    if existing:
        return
    conn.execute(
        "INSERT OR IGNORE INTO entity_types (type, description) VALUES (?, ?)",
        (etype, f"Auto-discovered from extractor"),
    )
    summary.new_entity_types.append(etype)


def _normalize_predicate(raw: str) -> str:
    """Case-fold and whitespace-collapse predicate names.

    Prevents has_current_POC_product and has_current_poc_product from being
    stored as two distinct predicates. Whitespace and dashes collapse to
    underscores.
    """
    s = raw.strip().lower()
    s = "_".join(s.split())
    s = s.replace("-", "_")
    return s


def _ensure_predicate(
    conn: sqlite3.Connection, predicate: str, summary: WriteSummary
) -> str:
    """Register a new predicate with auto_added=1 if not present, or bump occurrence_count.

    For freshly-inserted predicates, immediately runs inline tier-2 resolution
    (resolve_one). If the new predicate is semantically close to an existing
    canonical, it gets merged on the spot and callers get the canonical name
    back; otherwise it stays as its own canonical. This keeps vocabulary from
    sprawling during live ingest.

    Returns the name callers should use on the triple (either the normalized
    predicate or its canonical after inline merge).
    """
    normalized = _normalize_predicate(predicate)
    row = conn.execute(
        "SELECT auto_added, canonical_name FROM predicates WHERE name = ?", (normalized,)
    ).fetchone()

    if row is None:
        conn.execute(
            "INSERT INTO predicates (name, is_functional, occurrence_count, auto_added, description) "
            "VALUES (?, 0, 1, 1, ?)",
            (normalized, f"Auto-discovered from extractor"),
        )
        summary.new_predicates.append(normalized)

        try:
            from lib.builder.resolver import resolve_one
            decision = resolve_one(normalized, conn=conn)
            if decision and decision.get("action") == "auto_merged":
                canonical = decision["into"]
                summary.predicates_inline_merged = summary.predicates_inline_merged + 1
                return canonical
        except Exception as exc:
            log.warning("inline resolve_one failed for %r: %s", normalized, exc)
    else:
        if row["canonical_name"]:
            return row["canonical_name"]
        conn.execute(
            "UPDATE predicates SET occurrence_count = occurrence_count + 1 WHERE name = ?",
            (normalized,),
        )
    return normalized


def _tier1_resolve(
    conn: sqlite3.Connection,
    entity: Entity,
) -> Optional[str]:
    """Tier 1 exact-match alias lookup.

    Returns an existing db entity_id if any of (name, aliases) match a row in
    entity_aliases for an entity of the same type. Case- and whitespace-insensitive.
    Entity type is canonicalized (Employee -> Person) before matching.
    None if no match - caller should insert a new entity.
    """
    candidates = {entity.name.strip().lower(), *(a.strip().lower() for a in entity.aliases)}
    candidates.discard("")

    if not candidates:
        return None

    canonical_type = _canonical_entity_type(entity.type)
    placeholders = ",".join("?" for _ in candidates)
    row = conn.execute(
        f"""
        SELECT a.entity_id
        FROM entity_aliases a
        JOIN entities e ON e.id = a.entity_id
        WHERE lower(a.alias) IN ({placeholders})
          AND e.type = ?
          AND e.status = 'active'
        ORDER BY a.is_primary DESC, a.confidence DESC
        LIMIT 1
        """,
        (*candidates, canonical_type),
    ).fetchone()

    return row["entity_id"] if row else None


def _merge_entity_updates(
    conn: sqlite3.Connection,
    entity_id: str,
    entity: Entity,
    summary: WriteSummary,
    source_id: int,
) -> None:
    """Absorb new aliases and properties from this extraction into the existing entity."""
    row = conn.execute(
        "SELECT properties_json FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()
    if row is None:
        return

    existing_props: dict = json.loads(row["properties_json"] or "{}")
    merged = dict(existing_props)
    changed = False
    for k, v in entity.properties.items():
        if k not in merged or not merged[k]:
            merged[k] = v
            changed = True
    if changed:
        conn.execute(
            "UPDATE entities SET properties_json = ? WHERE id = ?",
            (json.dumps(merged, ensure_ascii=False), entity_id),
        )

    for alias in entity.aliases:
        if not alias.strip():
            continue
        alias_type = _guess_alias_type(alias)
        cur = conn.execute(
            "INSERT OR IGNORE INTO entity_aliases "
            "(entity_id, alias, alias_type, confidence, is_primary, source_id) "
            "VALUES (?, ?, ?, 1.0, 0, ?)",
            (entity_id, alias.strip(), alias_type, source_id),
        )
        if cur.rowcount > 0:
            summary.aliases_added += 1


def _guess_alias_type(alias: str) -> str:
    a = alias.strip()
    if "@" in a and "." in a.split("@", 1)[1]:
        return "email"
    if a.startswith("@"):
        return "handle"
    if a.startswith("emp_") or a.startswith("cust_") or a.startswith("cli_"):
        return "username"
    if len(a) == 36 and a.count("-") == 4:
        return "external_id"
    if all(c.isupper() or c == "." for c in a.replace(" ", "")) and len(a.replace(".", "").replace(" ", "")) <= 6:
        return "initials"
    return "name"


def _insert_entity(
    conn: sqlite3.Connection,
    entity: Entity,
    source_id: int,
    summary: WriteSummary,
) -> str:
    canonical_type = _canonical_entity_type(entity.type)
    _ensure_entity_type(conn, canonical_type, summary)
    entity_id = _new_entity_id()
    conn.execute(
        "INSERT INTO entities (id, type, name, properties_json, status) "
        "VALUES (?, ?, ?, ?, 'active')",
        (entity_id, canonical_type, entity.name.strip(), json.dumps(entity.properties, ensure_ascii=False)),
    )
    for alias in entity.aliases:
        if not alias.strip():
            continue
        alias_type = _guess_alias_type(alias)
        cur = conn.execute(
            "INSERT OR IGNORE INTO entity_aliases "
            "(entity_id, alias, alias_type, confidence, is_primary, source_id) "
            "VALUES (?, ?, ?, 1.0, 0, ?)",
            (entity_id, alias.strip(), alias_type, source_id),
        )
        if cur.rowcount > 0:
            summary.aliases_added += 1
    return entity_id


SOURCE_AUTHORITY: dict[str, float] = {
    "hr": 1.0, "crm": 0.8, "policy": 0.7, "ticket": 0.5,
    "email": 0.4, "chat": 0.3, "unknown": 0.5,
}


def _source_score_for_triple(conn: sqlite3.Connection, triple_id: int) -> dict:
    """Compute authority x confidence x recency score for the best-scoring source
    supporting this triple. Used to generate auto-resolution hints.
    """
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
    authority = SOURCE_AUTHORITY.get(row["source_type"], 0.5)
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


def _detect_and_record_conflict(
    conn: sqlite3.Connection,
    *,
    subject_id: str,
    predicate: str,
    new_triple_id: int,
    summary: WriteSummary,
) -> None:
    """Check whether this newly-inserted triple contradicts an existing active
    triple for the same functional-predicate slot. If so, mark both conflicted
    and create a conflicts row with authority-weighted scoring hint.

    Only fires for predicates flagged is_functional=1. Non-functional predicates
    can legitimately have many values per subject (e.g. Person mentions Person)
    so multiple active triples are not conflicts.
    """
    functional = conn.execute(
        "SELECT is_functional FROM predicates WHERE name = ?", (predicate,)
    ).fetchone()
    if functional is None or not functional["is_functional"]:
        return

    other = conn.execute(
        """
        SELECT id FROM triples
        WHERE subject_id = ? AND predicate = ? AND status = 'active' AND id != ?
        ORDER BY id ASC LIMIT 1
        """,
        (subject_id, predicate, new_triple_id),
    ).fetchone()
    if other is None:
        return

    a_id, b_id = other["id"], new_triple_id
    score_a = _source_score_for_triple(conn, a_id)
    score_b = _source_score_for_triple(conn, b_id)

    if score_a["total"] > score_b["total"]:
        hint_winner = "a"
        hint_reason = f"{score_a['source_type']} authority {score_a['authority']} beats {score_b['source_type']} {score_b['authority']}"
    elif score_b["total"] > score_a["total"]:
        hint_winner = "b"
        hint_reason = f"{score_b['source_type']} authority {score_b['authority']} beats {score_a['source_type']} {score_a['authority']}"
    else:
        hint_winner, hint_reason = None, "scores tied; human decision required"

    scores = {"a": score_a, "b": score_b, "hint": {"winner": hint_winner, "reason": hint_reason}}

    conn.execute(
        """
        INSERT INTO conflicts (conflict_type, triple_a_id, triple_b_id, status, scores_json)
        VALUES ('fact', ?, ?, 'pending', ?)
        """,
        (a_id, b_id, json.dumps(scores)),
    )
    conn.execute(
        "UPDATE triples SET status = 'conflicted' WHERE id IN (?, ?)",
        (a_id, b_id),
    )
    conn.execute(
        "INSERT INTO audit_log (actor, action, target_kind, target_id, payload_json) VALUES (?, ?, ?, ?, ?)",
        ("writer", "detect_conflict", "conflict", None, json.dumps({"triple_a": a_id, "triple_b": b_id, "predicate": predicate, "subject_id": subject_id})),
    )
    summary.conflicts_created += 1


def _upsert_triple(
    conn: sqlite3.Connection,
    *,
    subject_id: str,
    predicate: str,
    object_id: Optional[str],
    object_value: Optional[str],
    confidence: float,
    source_id: int,
    summary: WriteSummary,
) -> None:
    """Insert triple or link existing triple to this source for multi-source provenance.

    On new-triple insert, if the predicate is functional and the subject already
    has a different-value active triple for it, create a pending conflict row
    with both triples flagged conflicted.
    """
    if object_id is not None:
        existing = conn.execute(
            """
            SELECT id FROM triples
            WHERE subject_id = ? AND predicate = ? AND object_id = ?
              AND object_is_entity = 1 AND status = 'active'
            LIMIT 1
            """,
            (subject_id, predicate, object_id),
        ).fetchone()
    else:
        existing = conn.execute(
            """
            SELECT id FROM triples
            WHERE subject_id = ? AND predicate = ? AND object_value = ?
              AND object_is_entity = 0 AND status = 'active'
            LIMIT 1
            """,
            (subject_id, predicate, object_value),
        ).fetchone()

    if existing is not None:
        triple_id = existing["id"]
        cur = conn.execute(
            "INSERT OR IGNORE INTO triple_sources (triple_id, source_id, confidence) "
            "VALUES (?, ?, ?)",
            (triple_id, source_id, confidence),
        )
        if cur.rowcount > 0:
            summary.triples_linked_to_existing += 1
        return

    cur = conn.execute(
        """
        INSERT INTO triples (subject_id, predicate, object_id, object_value, object_is_entity, status)
        VALUES (?, ?, ?, ?, ?, 'active')
        """,
        (
            subject_id,
            predicate,
            object_id,
            object_value,
            1 if object_id is not None else 0,
        ),
    )
    triple_id = cur.lastrowid
    conn.execute(
        "INSERT INTO triple_sources (triple_id, source_id, confidence) VALUES (?, ?, ?)",
        (triple_id, source_id, confidence),
    )
    summary.triples_inserted += 1

    _detect_and_record_conflict(
        conn,
        subject_id=subject_id,
        predicate=predicate,
        new_triple_id=triple_id,
        summary=summary,
    )


def write_extraction(
    result: ExtractionResult,
    *,
    document_path: str,
    source_type: str,
    raw_text: str,
    properties: Optional[dict] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> WriteSummary:
    """Persist one ExtractionResult into the graph with provenance.

    Flow:
      1. Insert a sources row for this chunk
      2. For each entity: Tier 1 resolve -> existing db id; else insert new
      3. For each triple: resolve refs -> db ids; upsert triple; link triple_sources
      4. Auto-register new entity_types and predicates with auto_added=1
      5. All within a single transaction per chunk
    """
    summary = WriteSummary()
    properties = properties or {}

    owned = conn is None
    c = conn or get_connection()

    try:
        c.execute("BEGIN")

        source_id = _upsert_source(
            c,
            document_path=document_path,
            source_type=source_type,
            raw_text=raw_text,
            properties=properties,
        )
        summary.source_id = source_id

        ref_to_db: dict[str, str] = {}
        for ent in result.entities:
            db_id = _tier1_resolve(c, ent)
            if db_id is not None:
                summary.entities_matched += 1
                _merge_entity_updates(c, db_id, ent, summary, source_id)
            else:
                db_id = _insert_entity(c, ent, source_id, summary)
                summary.entities_inserted += 1
            ref_to_db[ent.ref] = db_id

        for t in result.triples:
            subject_id = ref_to_db.get(t.subject_ref)
            if subject_id is None:
                summary.errors.append(
                    f"triple subject_ref={t.subject_ref!r} unresolved (extractor bug)"
                )
                continue
            if t.object_is_entity:
                object_id = ref_to_db.get(t.object_ref)
                if object_id is None:
                    summary.errors.append(
                        f"triple object_ref={t.object_ref!r} unresolved (extractor bug)"
                    )
                    continue
                object_value = None
            else:
                object_id = None
                object_value = t.object_value

            predicate = _ensure_predicate(c, t.predicate, summary)
            _upsert_triple(
                c,
                subject_id=subject_id,
                predicate=predicate,
                object_id=object_id,
                object_value=object_value,
                confidence=t.confidence,
                source_id=source_id,
                summary=summary,
            )

        c.commit()
    except Exception as exc:
        c.rollback()
        summary.errors.append(f"transaction failed: {exc}")
        log.exception("write_extraction failed")
    finally:
        if owned:
            c.close()

    return summary
