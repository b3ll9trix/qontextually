-- ============================================================================
-- Qontext context base — schema v2
-- ============================================================================
-- Changes in this migration:
--   1. Drop registry FKs: triples.predicate, entities.type, sources.source_type
--      Registries (predicates, entity_types, source_types) become behavioral
--      hints, not constraints. The extractor can auto-register new vocabulary
--      without an INSERT-first dance.
--   2. Extend predicates with occurrence_count + auto_added
--   3. (handled in db/setup.py) entity_embeddings virtual table — sqlite-vec
--   4. Add source_fts virtual table (FTS5 porter) + sync triggers
--   5. Add entity_aliases table
--   6. Add entity_merges audit table
--   7. Add properties_json column to sources
--   8. Add vocabulary_discovered view
-- ============================================================================

PRAGMA foreign_keys = OFF;

BEGIN;

-- ----------------------------------------------------------------------------
-- Change 1 — Rebuild entities, triples, sources without registry FKs
-- ----------------------------------------------------------------------------
-- entities: drops FK to entity_types(type). Structural FK (none on entities
-- itself) unchanged.

CREATE TABLE entities_new (
    id              TEXT PRIMARY KEY,
    type            TEXT NOT NULL,
    name            TEXT NOT NULL,
    properties_json TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'active',
        -- 'active' | 'pending_match' | 'merged' | 'deleted'
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO entities_new (id, type, name, properties_json, status, created_at, updated_at)
SELECT id, type, name, properties_json, status, created_at, updated_at
FROM entities;

DROP TABLE entities;
ALTER TABLE entities_new RENAME TO entities;

CREATE INDEX IF NOT EXISTS idx_entities_type_name
    ON entities (type, lower(name));
CREATE INDEX IF NOT EXISTS idx_entities_status
    ON entities (status);

-- sources: drops FK to source_types(type). Also adds properties_json (change 7)
-- while we're rebuilding.

CREATE TABLE sources_new (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    document_path   TEXT NOT NULL,
    source_type     TEXT NOT NULL,
    span_start      INTEGER,
    span_end        INTEGER,
    raw_text        TEXT,
    properties_json TEXT NOT NULL DEFAULT '{}',
        -- email headers, thread ids, cc list, chat channel, etc.
    extracted_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO sources_new (id, document_path, source_type, span_start, span_end, raw_text, extracted_at)
SELECT id, document_path, source_type, span_start, span_end, raw_text, extracted_at
FROM sources;

DROP TABLE sources;
ALTER TABLE sources_new RENAME TO sources;

CREATE INDEX IF NOT EXISTS idx_sources_path
    ON sources (document_path);

-- triples: drops FK to predicates(name). KEEPS structural FKs to entities
-- (subject_id and object_id) — those are real relationships, not vocabulary.

CREATE TABLE triples_new (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id       TEXT NOT NULL,
    predicate        TEXT NOT NULL,
    object_id        TEXT,
    object_value     TEXT,
    object_is_entity INTEGER NOT NULL,
    status           TEXT NOT NULL DEFAULT 'active',
        -- 'active' | 'conflicted' | 'superseded' | 'orphaned' | 'retracted'
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (subject_id) REFERENCES entities(id) ON DELETE CASCADE,
    FOREIGN KEY (object_id)  REFERENCES entities(id) ON DELETE SET NULL,
    CHECK (
        (object_is_entity = 1 AND object_id IS NOT NULL AND object_value IS NULL) OR
        (object_is_entity = 0 AND object_value IS NOT NULL AND object_id IS NULL)
    )
);

INSERT INTO triples_new (id, subject_id, predicate, object_id, object_value, object_is_entity, status, created_at, updated_at)
SELECT id, subject_id, predicate, object_id, object_value, object_is_entity, status, created_at, updated_at
FROM triples;

DROP TABLE triples;
ALTER TABLE triples_new RENAME TO triples;

CREATE INDEX IF NOT EXISTS idx_triples_subject_predicate
    ON triples (subject_id, predicate);
CREATE INDEX IF NOT EXISTS idx_triples_status
    ON triples (status);
CREATE INDEX IF NOT EXISTS idx_triples_object_id
    ON triples (object_id) WHERE object_id IS NOT NULL;

-- Recreate updated_at triggers (they were attached to the old tables)
DROP TRIGGER IF EXISTS trg_entities_updated;
CREATE TRIGGER trg_entities_updated
AFTER UPDATE ON entities
FOR EACH ROW
BEGIN
    UPDATE entities SET updated_at = datetime('now') WHERE id = OLD.id;
END;

DROP TRIGGER IF EXISTS trg_triples_updated;
CREATE TRIGGER trg_triples_updated
AFTER UPDATE ON triples
FOR EACH ROW
BEGIN
    UPDATE triples SET updated_at = datetime('now') WHERE id = OLD.id;
END;

-- Integrity check — must return no rows
-- (Can't SELECT inside executescript meaningfully; we rely on python-side check)

COMMIT;

PRAGMA foreign_keys = ON;
