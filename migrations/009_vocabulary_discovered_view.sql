-- ============================================================================
-- Qontext — vocabulary_discovered view (human-in-loop promotion panel)
-- ============================================================================
-- Lists every auto-added predicate (predicates.auto_added = 1) with usage
-- stats and a sample triple so a reviewer can decide whether to promote
-- (mark functional), rename (merge into an existing predicate), or ignore.
--
-- Columns:
--   predicate        the discovered name
--   is_functional    current functional flag (reviewer toggles this)
--   occurrence_count telemetry counter from the extractor
--   actual_count     count recomputed from triples (sanity check vs telemetry)
--   last_used        most recent triple using this predicate
--   sample_subject   a subject entity id that uses this predicate (arbitrary)
--   sample_object    the object for that sample — either an entity name or
--                    a literal value
--
-- Ordering: highest-usage first, then most-recent.
-- ============================================================================

BEGIN;

DROP VIEW IF EXISTS vocabulary_discovered;

CREATE VIEW vocabulary_discovered AS
SELECT
    p.name                                AS predicate,
    p.is_functional                       AS is_functional,
    p.occurrence_count                    AS occurrence_count,
    COALESCE(usage.actual_count, 0)       AS actual_count,
    usage.last_used                       AS last_used,
    usage.sample_subject                  AS sample_subject,
    usage.sample_object                   AS sample_object,
    p.description                         AS description
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
ORDER BY p.occurrence_count DESC, usage.last_used DESC;

COMMIT;
