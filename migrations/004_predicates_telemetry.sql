-- ============================================================================
-- Qontext — predicates telemetry columns
-- ============================================================================
-- Extends predicates registry with usage tracking so the human-in-loop UI can
-- show "discovered predicates" (auto_added=1) sorted by occurrence_count and
-- let a reviewer promote them to functional or rename them.
-- ============================================================================

BEGIN;

ALTER TABLE predicates ADD COLUMN occurrence_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE predicates ADD COLUMN auto_added       INTEGER NOT NULL DEFAULT 0;

-- Existing 14 rows are all from the seed; mark them explicitly as not auto-added.
UPDATE predicates SET auto_added = 0;

COMMIT;
