-- ============================================================================
-- Qontext context base — schema
-- ============================================================================
-- A graph stored in SQLite. Entities and triples form the graph. Sources
-- preserve provenance. Triple_sources is the many-to-many that lets a single
-- fact be supported by multiple files. tracked_files drives the rolling
-- update polling loop.
-- ============================================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

-- ----------------------------------------------------------------------------
-- Vocabulary tables — drive behavior, editable as data
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS source_types (
    type        TEXT PRIMARY KEY,
    authority   REAL NOT NULL CHECK (authority >= 0 AND authority <= 1),
    description TEXT
);

CREATE TABLE IF NOT EXISTS predicates (
    name           TEXT PRIMARY KEY,
    is_functional  INTEGER NOT NULL DEFAULT 0,  -- 1 = single-valued (works_at), 0 = multi (mentions)
    description    TEXT
);

CREATE TABLE IF NOT EXISTS entity_types (
    type        TEXT PRIMARY KEY,
    description TEXT
);

-- ----------------------------------------------------------------------------
-- Core graph
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS entities (
    id              TEXT PRIMARY KEY,
    type            TEXT NOT NULL,
    name            TEXT NOT NULL,
    properties_json TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'active',
        -- 'active' | 'pending_match' | 'merged' | 'deleted'
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (type) REFERENCES entity_types(type)
);

CREATE INDEX IF NOT EXISTS idx_entities_type_name
    ON entities (type, lower(name));
CREATE INDEX IF NOT EXISTS idx_entities_status
    ON entities (status);

-- ----------------------------------------------------------------------------
-- Triples — facts in the graph
-- ----------------------------------------------------------------------------
-- object_is_entity = 1 → object_id refers to entities(id)
-- object_is_entity = 0 → object_value is a literal string (e.g. a job title)

CREATE TABLE IF NOT EXISTS triples (
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
    FOREIGN KEY (predicate)  REFERENCES predicates(name),
    CHECK (
        (object_is_entity = 1 AND object_id IS NOT NULL AND object_value IS NULL) OR
        (object_is_entity = 0 AND object_value IS NOT NULL AND object_id IS NULL)
    )
);

-- Conflict detection scans this constantly
CREATE INDEX IF NOT EXISTS idx_triples_subject_predicate
    ON triples (subject_id, predicate);
CREATE INDEX IF NOT EXISTS idx_triples_status
    ON triples (status);
CREATE INDEX IF NOT EXISTS idx_triples_object_id
    ON triples (object_id) WHERE object_id IS NOT NULL;

-- ----------------------------------------------------------------------------
-- Sources — files and the spans within them that supplied facts
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sources (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    document_path TEXT NOT NULL,
    source_type   TEXT NOT NULL,
    span_start    INTEGER,
    span_end      INTEGER,
    raw_text      TEXT,
    extracted_at  TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (source_type) REFERENCES source_types(type)
);

CREATE INDEX IF NOT EXISTS idx_sources_path
    ON sources (document_path);

-- ----------------------------------------------------------------------------
-- Triple ↔ Source — many-to-many evidence links
-- ----------------------------------------------------------------------------
-- Deleting a source removes its rows here. A triple becomes orphaned when
-- it has zero rows in this table.

CREATE TABLE IF NOT EXISTS triple_sources (
    triple_id    INTEGER NOT NULL,
    source_id    INTEGER NOT NULL,
    confidence   REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0 AND confidence <= 1),
    extracted_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (triple_id, source_id),
    FOREIGN KEY (triple_id) REFERENCES triples(id) ON DELETE CASCADE,
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_triple_sources_source
    ON triple_sources (source_id);

-- ----------------------------------------------------------------------------
-- Tracked files — drives the rolling update polling loop
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS tracked_files (
    path         TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    mtime        REAL NOT NULL,
    last_seen_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ----------------------------------------------------------------------------
-- Conflicts queue — both fact conflicts and entity-match conflicts
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS conflicts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    conflict_type  TEXT NOT NULL,
        -- 'fact' | 'entity_match'
    triple_a_id    INTEGER,
    triple_b_id    INTEGER,
    entity_a_id    TEXT,
    entity_b_id    TEXT,
    status         TEXT NOT NULL DEFAULT 'pending',
        -- 'pending' | 'auto_resolved' | 'human_resolved' | 'dismissed'
    winner_ref     TEXT,
        -- which side won, e.g. 'a' | 'b' | 'neither'
    resolution_note TEXT,
    scores_json    TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at    TEXT,
    FOREIGN KEY (triple_a_id) REFERENCES triples(id) ON DELETE CASCADE,
    FOREIGN KEY (triple_b_id) REFERENCES triples(id) ON DELETE CASCADE,
    FOREIGN KEY (entity_a_id) REFERENCES entities(id) ON DELETE CASCADE,
    FOREIGN KEY (entity_b_id) REFERENCES entities(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_conflicts_status
    ON conflicts (status);
CREATE INDEX IF NOT EXISTS idx_conflicts_type_status
    ON conflicts (conflict_type, status);

-- ----------------------------------------------------------------------------
-- Audit log — every mutation, for debugging and demo
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    actor       TEXT NOT NULL,             -- 'extractor' | 'heuristic' | 'human' | 'cleanup'
    action      TEXT NOT NULL,             -- 'insert_triple' | 'resolve_conflict' | 'delete_source' | ...
    target_kind TEXT,                      -- 'triple' | 'entity' | 'source' | 'conflict'
    target_id   TEXT,
    payload_json TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_target
    ON audit_log (target_kind, target_id);
CREATE INDEX IF NOT EXISTS idx_audit_created
    ON audit_log (created_at DESC);

-- ----------------------------------------------------------------------------
-- Triggers — keep updated_at fresh
-- ----------------------------------------------------------------------------

CREATE TRIGGER IF NOT EXISTS trg_entities_updated
AFTER UPDATE ON entities
FOR EACH ROW
BEGIN
    UPDATE entities SET updated_at = datetime('now') WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_triples_updated
AFTER UPDATE ON triples
FOR EACH ROW
BEGIN
    UPDATE triples SET updated_at = datetime('now') WHERE id = OLD.id;
END;
