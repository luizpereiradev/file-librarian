-- 0009: `flib reprocess` (RF-37).
--
-- Runs it starts get their own trigger, 'reprocess', so the journal tells them
-- apart (crash recovery finishes their moves as reprocess moves). SQLite
-- cannot change a CHECK constraint in place, so `runs` is rebuilt with the
-- same rows and ids; foreign keys from files and operations are checked at
-- commit, once every run is back.
--
-- A reprocess move also records the status and category the file had before
-- (prev_status, prev_category), so undo gives exactly those back, whatever
-- the configuration says by then. NULL on other moves.
--
-- index_changes journals what a run changed in the index without moving a
-- file (a category renamed in rows and corrections, a Finder tag renamed in
-- the journal, a file's description and the rest of a new classification),
-- so `flib undo` reverts it together with the run's moves. Values are kept
-- as text; NULL is NULL.

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

ALTER TABLE operations ADD COLUMN prev_status TEXT;
ALTER TABLE operations ADD COLUMN prev_category TEXT;

CREATE TABLE index_changes (
  id         INTEGER PRIMARY KEY,
  run_id     INTEGER NOT NULL REFERENCES runs(id),
  target     TEXT NOT NULL
             CHECK (target IN ('files.category', 'files.description', 'files.tags',
                               'files.confidence', 'files.provider', 'files.model',
                               'files.name_choice', 'files.name_choice_reason',
                               'files.processed_at', 'corrections.old_value',
                               'corrections.new_value', 'operations.finder_tag')),
  row_id     INTEGER NOT NULL,
  old_value  TEXT,
  new_value  TEXT,
  status     TEXT NOT NULL CHECK (status IN ('done', 'undone')),
  created_at TEXT NOT NULL
);

CREATE INDEX index_changes_run ON index_changes(run_id);
