-- ============================================================================
-- Qontext — entity aliases (name variants for Tier 1 exact match)
-- ============================================================================
-- One row per (entity_id, alias, alias_type). Every time Tier 2 embedding
-- resolution confirms a new name variant for an existing entity, the result
-- is written here so that future mentions hit Tier 1 (cheap) instead of
-- re-paying the embedding cost.
--
-- alias_type is free text following a registry convention:
--   'name'      canonical full-name strings
--   'email'     email addresses
--   'username'  login handles, e.g. 'jsmith'
--   'handle'    chat / social handles, e.g. '@john'
--   'initials'  'J.S.', 'JRR'
--   'nickname'  'Johnny'
--   'other'     fallback
--
-- source_id is optional: aliases may come from a source span or from a
-- heuristic rule (e.g. initials derived from the primary name).
--
-- is_primary = 1 marks the alias chosen as the canonical display name.
-- Enforced as at-most-one-per-entity via a partial unique index.
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS entity_aliases (
    entity_id   TEXT NOT NULL,
    alias       TEXT NOT NULL,
    alias_type  TEXT NOT NULL DEFAULT 'name',
    confidence  REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0 AND confidence <= 1),
    is_primary  INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
    source_id   INTEGER,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (entity_id, alias, alias_type),
    FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE,
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE SET NULL
);

-- Hot path: Tier 1 exact-match lookup by lowercased alias
CREATE INDEX IF NOT EXISTS idx_entity_aliases_lookup
    ON entity_aliases (lower(alias), alias_type);

-- Reverse lookup: get all aliases for an entity
CREATE INDEX IF NOT EXISTS idx_entity_aliases_entity
    ON entity_aliases (entity_id);

-- At most one primary alias per entity
CREATE UNIQUE INDEX IF NOT EXISTS ux_entity_aliases_primary
    ON entity_aliases (entity_id) WHERE is_primary = 1;

COMMIT;
