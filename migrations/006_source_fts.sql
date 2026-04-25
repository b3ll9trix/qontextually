-- ============================================================================
-- Qontext — full-text search over sources
-- ============================================================================
-- External-content FTS5 table over sources(raw_text, document_path).
-- External-content means the index references sources rows by rowid instead
-- of duplicating the text; the three sync triggers keep it in lockstep with
-- INSERT / DELETE / UPDATE on sources.
--
-- Tokenizer: porter (English stemming) layered on unicode61 (Unicode case
-- and diacritic folding). Covers "manage / managed / manages" matching and
-- handles accented names.
--
-- Search pattern from app code:
--   SELECT s.* FROM source_fts f JOIN sources s ON s.id = f.rowid
--   WHERE source_fts MATCH ? ORDER BY rank;
-- ============================================================================

BEGIN;

CREATE VIRTUAL TABLE IF NOT EXISTS source_fts USING fts5(
    raw_text,
    document_path,
    content='sources',
    content_rowid='id',
    tokenize = "porter unicode61 remove_diacritics 2"
);

-- Backfill any rows already in sources (idempotent: rebuild fully).
INSERT INTO source_fts(source_fts) VALUES('rebuild');

-- AFTER INSERT: mirror new row into the index
CREATE TRIGGER IF NOT EXISTS trg_sources_fts_ai
AFTER INSERT ON sources
BEGIN
    INSERT INTO source_fts(rowid, raw_text, document_path)
    VALUES (new.id, new.raw_text, new.document_path);
END;

-- AFTER DELETE: use the FTS 'delete' command so the index drops cleanly
CREATE TRIGGER IF NOT EXISTS trg_sources_fts_ad
AFTER DELETE ON sources
BEGIN
    INSERT INTO source_fts(source_fts, rowid, raw_text, document_path)
    VALUES ('delete', old.id, old.raw_text, old.document_path);
END;

-- AFTER UPDATE: delete-then-insert is the FTS5 canonical pattern
CREATE TRIGGER IF NOT EXISTS trg_sources_fts_au
AFTER UPDATE ON sources
BEGIN
    INSERT INTO source_fts(source_fts, rowid, raw_text, document_path)
    VALUES ('delete', old.id, old.raw_text, old.document_path);
    INSERT INTO source_fts(rowid, raw_text, document_path)
    VALUES (new.id, new.raw_text, new.document_path);
END;

COMMIT;
