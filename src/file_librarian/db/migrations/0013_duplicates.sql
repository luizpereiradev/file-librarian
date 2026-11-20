-- 0013: duplicates (RF-21).
--
-- A file with the same content as one already filed is moved to the
-- duplicates folder and gets its own row, with status 'duplicate' and
-- duplicate_of pointing at the row of the file it is a copy of. Content is no
-- longer unique across every row: it stays unique among the rows that are not
-- duplicates (files_sha256, a partial index), so the index still has exactly
-- one row per content for the scanner, reconcile and undo to find. A
-- duplicate's row keeps duplicate_of whatever its status becomes later
-- (skipped after an undo, missing after reconcile).
--
-- SQLite cannot change a CHECK constraint or drop a UNIQUE one in place, so
-- `files` is rebuilt with the same rows and ids; foreign keys from
-- operations, corrections and embeddings are checked at commit, once every
-- row is back. Dropping `files` would delete the embeddings through their ON
-- DELETE CASCADE, so they are kept aside and put back unchanged.
-- The full-text index keeps its rows (same ids, same text); only its
-- triggers, dropped with the old table, are made again.

PRAGMA defer_foreign_keys = ON;

CREATE TEMP TABLE files_before_0013 AS SELECT * FROM files;
CREATE TEMP TABLE embeddings_before_0013 AS SELECT * FROM embeddings;

DROP TABLE files;

CREATE TABLE files (
  id                 INTEGER PRIMARY KEY,
  sha256             TEXT NOT NULL,
  original_name      TEXT NOT NULL,
  original_path      TEXT NOT NULL,
  current_name       TEXT NOT NULL,
  current_path       TEXT NOT NULL,
  extension          TEXT,
  size_bytes         INTEGER,
  source_url         TEXT,            -- download origin, read from macOS
  category           TEXT,
  description        TEXT,
  tags               TEXT,            -- JSON array
  confidence         REAL,
  status             TEXT NOT NULL
                     CHECK (status IN ('organized', 'review', 'skipped', 'missing', 'failed',
                                       'sensitive', 'duplicate')),
  attempts           INTEGER NOT NULL DEFAULT 0,
  last_error         TEXT,
  provider           TEXT,
  model              TEXT,
  run_id             INTEGER REFERENCES runs(id),
  processed_at       TEXT NOT NULL,
  is_unit            INTEGER NOT NULL DEFAULT 0,
  name_choice        TEXT,
  name_choice_reason TEXT,
  review_reason      TEXT
                     CHECK (review_reason IN ('low_confidence', 'unknown_category',
                                              'category_removed', 'moved_by_hand')),
  review_category    TEXT,
  duplicate_of       INTEGER REFERENCES files(id),
  CHECK (status != 'duplicate' OR duplicate_of IS NOT NULL)
);

INSERT INTO files (id, sha256, original_name, original_path, current_name, current_path,
                   extension, size_bytes, source_url, category, description, tags, confidence,
                   status, attempts, last_error, provider, model, run_id, processed_at, is_unit,
                   name_choice, name_choice_reason, review_reason, review_category)
SELECT id, sha256, original_name, original_path, current_name, current_path, extension,
       size_bytes, source_url, category, description, tags, confidence, status, attempts,
       last_error, provider, model, run_id, processed_at, is_unit, name_choice,
       name_choice_reason, review_reason, review_category
FROM files_before_0013;

DROP TABLE files_before_0013;

INSERT OR REPLACE INTO embeddings (file_id, model, dimension, text_hash, vector, created_at)
SELECT file_id, model, dimension, text_hash, vector, created_at FROM embeddings_before_0013;

DROP TABLE embeddings_before_0013;

CREATE UNIQUE INDEX files_sha256 ON files(sha256) WHERE duplicate_of IS NULL;
CREATE INDEX files_status ON files(status);
CREATE INDEX files_current_path ON files(current_path);
CREATE INDEX files_duplicate_of ON files(duplicate_of);

CREATE TRIGGER files_fts_insert AFTER INSERT ON files BEGIN
  INSERT INTO files_fts(rowid, original_name, current_name, description, tags)
  VALUES (new.id, new.original_name, new.current_name, new.description, new.tags);
END;

CREATE TRIGGER files_fts_delete AFTER DELETE ON files BEGIN
  INSERT INTO files_fts(files_fts, rowid, original_name, current_name, description, tags)
  VALUES ('delete', old.id, old.original_name, old.current_name, old.description, old.tags);
END;

CREATE TRIGGER files_fts_update AFTER UPDATE ON files BEGIN
  INSERT INTO files_fts(files_fts, rowid, original_name, current_name, description, tags)
  VALUES ('delete', old.id, old.original_name, old.current_name, old.description, old.tags);
  INSERT INTO files_fts(rowid, original_name, current_name, description, tags)
  VALUES (new.id, new.original_name, new.current_name, new.description, new.tags);
END;
