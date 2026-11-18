-- 0009: runs started by `flib reprocess` (RF-37) get their own trigger,
-- 'reprocess', so the journal tells them apart (crash recovery finishes their
-- moves as reprocess moves, and `flib undo` reverts the whole reprocess).
-- SQLite cannot change a CHECK constraint in place, so the table is rebuilt
-- with the same rows and ids. Foreign keys from files and operations are
-- checked at commit, once every run is back.

PRAGMA defer_foreign_keys = ON;

CREATE TEMP TABLE runs_before_0009 AS SELECT * FROM runs;

DROP TABLE runs;

CREATE TABLE runs (
  id           INTEGER PRIMARY KEY,
  mode         TEXT NOT NULL CHECK (mode IN ('run', 'simulate')),
  trigger      TEXT NOT NULL CHECK (trigger IN ('schedule', 'manual', 'mcp', 'reprocess')),
  started_at   TEXT NOT NULL,
  finished_at  TEXT,
  files_seen   INTEGER,
  files_moved  INTEGER,
  files_review INTEGER,
  errors       INTEGER
);

INSERT INTO runs (id, mode, trigger, started_at, finished_at, files_seen, files_moved,
                  files_review, errors)
SELECT id, mode, trigger, started_at, finished_at, files_seen, files_moved, files_review, errors
FROM runs_before_0009;

DROP TABLE runs_before_0009;
