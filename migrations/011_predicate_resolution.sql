-- ============================================================================
-- Qontext \u2014 tiered predicate resolution
-- ============================================================================
-- Enables the same Tier 1/2/3 resolution pattern we use for entities, but for
-- predicates. Mirrors entity_merges + entity_embeddings.
--
-- Flow:
--   1. canonical_name on predicates: points to the canonical predicate this
--      one has been merged INTO. NULL means "this predicate IS canonical."
--   2. predicate_merges audit table: durable record of every merge with
--      method (seed|normalized|embedding|human|rule) and confidence.
--   3. predicate_embeddings: one vector per predicate, derived from
--      name + description + most common usage context. Fed into the vec0
--      KNN index at runtime (created in db/db.py).
--
-- Important: merging a predicate rewrites triples.predicate to the canonical
-- name. The stale predicate row stays (with canonical_name set) so historical
-- reasoning (\"why did we merge X into Y on this date?\") remains answerable.
-- ============================================================================

BEGIN;

ALTER TABLE predicates ADD COLUMN canonical_name TEXT;
ALTER TABLE predicates ADD COLUMN merge_method   TEXT;

CREATE INDEX IF NOT EXISTS idx_predicates_canonical
    ON predicates (canonical_name) WHERE canonical_name IS NOT NULL;

CREATE TABLE IF NOT EXISTS predicate_merges (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    from_predicate      TEXT NOT NULL,
    into_predicate      TEXT NOT NULL,
    method              TEXT NOT NULL,
        -- 'normalized' | 'embedding' | 'human' | 'rule'
    confidence          REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0 AND confidence <= 1),
    source_of_decision  TEXT,
    affected_triples    INTEGER NOT NULL DEFAULT 0,
    merged_at           TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (from_predicate) REFERENCES predicates(name) ON DELETE SET NULL,
    FOREIGN KEY (into_predicate) REFERENCES predicates(name) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_predicate_merges_from
    ON predicate_merges (from_predicate);
CREATE INDEX IF NOT EXISTS idx_predicate_merges_into
    ON predicate_merges (into_predicate);

CREATE TABLE IF NOT EXISTS predicate_embeddings (
    predicate     TEXT PRIMARY KEY,
    model         TEXT NOT NULL,
    dim           INTEGER NOT NULL,
    embedding     BLOB NOT NULL,
    usage_context TEXT,
        -- e.g. "Person works_at Organization (most common)"
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (predicate) REFERENCES predicates(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_predicate_embeddings_model
    ON predicate_embeddings (model);

DROP VIEW IF EXISTS vocabulary_discovered;
CREATE VIEW vocabulary_discovered AS
SELECT
    p.name                          AS predicate,
    p.is_functional                 AS is_functional,
    p.occurrence_count              AS occurrence_count,
    COALESCE(usage.actual_count, 0) AS actual_count,
    usage.last_used                 AS last_used,
    usage.sample_subject            AS sample_subject,
    usage.sample_object             AS sample_object,
    p.description                   AS description,
    p.canonical_name                AS canonical_name,
    p.merge_method                  AS merge_method
FROM predicates p
LEFT JOIN (
    SELECT
        t.predicate                                   AS predicate,
        COUNT(*)                                      AS actual_count,
        MAX(t.created_at)                             AS last_used,
        (SELECT t2.subject_id FROM triples t2
         WHERE t2.predicate = t.predicate
         ORDER BY t2.created_at DESC LIMIT 1)         AS sample_subject,
        (SELECT COALESCE(e.name, t2.object_value)
         FROM triples t2
         LEFT JOIN entities e ON e.id = t2.object_id
         WHERE t2.predicate = t.predicate
         ORDER BY t2.created_at DESC LIMIT 1)         AS sample_object
    FROM triples t
    GROUP BY t.predicate
) AS usage ON usage.predicate = p.name
WHERE p.auto_added = 1
  AND p.canonical_name IS NULL
ORDER BY p.occurrence_count DESC, usage.last_used DESC;

COMMIT;
