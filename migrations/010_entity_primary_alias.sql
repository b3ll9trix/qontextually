-- ============================================================================
-- Qontext — auto-populate primary alias for every entity
-- ============================================================================
-- Ensures every entity has its canonical name mirrored into entity_aliases
-- so Tier 1 exact-match resolution never misses a single-mention entity.
--
-- AFTER INSERT: write the primary alias. INSERT OR IGNORE guards against a
-- caller having already inserted one explicitly.
--
-- AFTER UPDATE OF name: replace the stale primary alias. Alias PK is
-- (entity_id, alias, alias_type) so a rename requires delete-then-insert.
-- The WHEN clause prevents firing on unrelated updates (e.g. status changes
-- or the cascaded updated_at triggered by trg_entities_updated).
--
-- Backfill: insert primary aliases for any entities already present and
-- missing one. Safe to re-run.
-- ============================================================================

BEGIN;

CREATE TRIGGER IF NOT EXISTS trg_entities_primary_alias_ai
AFTER INSERT ON entities
BEGIN
    INSERT OR IGNORE INTO entity_aliases (entity_id, alias, alias_type, is_primary)
    VALUES (new.id, new.name, 'name', 1);
END;

CREATE TRIGGER IF NOT EXISTS trg_entities_primary_alias_au
AFTER UPDATE OF name ON entities
WHEN OLD.name IS NOT NEW.name
BEGIN
    DELETE FROM entity_aliases
    WHERE entity_id = OLD.id AND alias_type = 'name' AND is_primary = 1;
    INSERT OR IGNORE INTO entity_aliases (entity_id, alias, alias_type, is_primary)
    VALUES (NEW.id, NEW.name, 'name', 1);
END;

INSERT OR IGNORE INTO entity_aliases (entity_id, alias, alias_type, is_primary)
SELECT e.id, e.name, 'name', 1
FROM entities e
WHERE NOT EXISTS (
    SELECT 1 FROM entity_aliases a
    WHERE a.entity_id = e.id AND a.is_primary = 1
);

COMMIT;
