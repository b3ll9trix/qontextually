-- ============================================================================
-- Qontext — entity merges audit
-- ============================================================================
-- Dedicated history of every entity merge. 'from' was absorbed into 'into';
-- the absorbed entity's row in entities typically transitions to
-- status='merged' while 'into' remains active. Rows here survive even if the
-- involved entities are later hard-deleted (FK uses SET NULL), so the merge
-- history is durable for provenance and "show me every merge" queries.
--
-- method values (free text, registry convention):
--   'tier1_exact'      alias / lowercase / email exact match
--   'tier2_embedding'  cosine similarity above threshold
--   'human'            reviewer action in the conflict UI
--   'rule'             custom rule, e.g. same email domain + same last name
--
-- conflict_id links back to conflicts.id when this merge resolved a queued
-- entity_match conflict (Tier 3, ambiguous 0.75-0.90 band).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS entity_merges (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    from_entity_id      TEXT,
    into_entity_id      TEXT,
    method              TEXT NOT NULL,
    confidence          REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0 AND confidence <= 1),
    source_of_decision  TEXT,
    conflict_id         INTEGER,
    merged_at           TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (from_entity_id) REFERENCES entities(id) ON DELETE SET NULL,
    FOREIGN KEY (into_entity_id) REFERENCES entities(id) ON DELETE SET NULL,
    FOREIGN KEY (conflict_id)    REFERENCES conflicts(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_entity_merges_from
    ON entity_merges (from_entity_id);
CREATE INDEX IF NOT EXISTS idx_entity_merges_into
    ON entity_merges (into_entity_id);
CREATE INDEX IF NOT EXISTS idx_entity_merges_method_time
    ON entity_merges (method, merged_at DESC);

COMMIT;
