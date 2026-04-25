-- ============================================================================
-- Qontext — entity embeddings (source-of-truth table)
-- ============================================================================
-- Stores one vector per entity as raw BLOB plus metadata. This table exists
-- unconditionally; it does not require the sqlite-vec extension.
--
-- The companion vec0 virtual table (entity_embeddings_vec) is created at
-- runtime in db/db.py IFF sqlite-vec loads. If it doesn't, inserts and reads
-- here still work; only KNN similarity search is disabled. The app reports
-- a clear warning instead of crashing — the graceful fallback of the plan.
--
-- dim is stored per row so a future migration can swap embedding models
-- without a schema change. The vec0 mirror is always one fixed dim per
-- instance though (sqlite-vec constraint).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS entity_embeddings (
    entity_id  TEXT PRIMARY KEY,
    model      TEXT NOT NULL,
    dim        INTEGER NOT NULL,
    embedding  BLOB NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_entity_embeddings_model
    ON entity_embeddings (model);

COMMIT;
