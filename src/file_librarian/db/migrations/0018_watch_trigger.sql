-- 0018: watch mode (#90).
--
-- Runs launchd starts because something arrived in an input folder get their
-- own trigger, 'watch', so `flib status` can tell the last watch run apart and
-- the daily cost cap can add up what automatic runs ('schedule' and 'watch')
-- spent today. SQLite cannot change a CHECK constraint in place, so `runs` is
-- rebuilt with the same rows and ids, as in 0009; foreign keys from files,
-- operations, index_changes and the rest are checked at commit, once every
-- run is back.

PRAGMA defer_foreign_keys = ON;

CREATE TEMP TABLE runs_before_0018 AS SELECT * FROM runs;

DROP TABLE runs;

CREATE TABLE runs (
  id               INTEGER PRIMARY KEY,
  mode             TEXT NOT NULL CHECK (mode IN ('run', 'simulate')),
  trigger          TEXT NOT NULL
                   CHECK (trigger IN ('schedule', 'manual', 'mcp', 'reprocess', 'watch')),
  started_at       TEXT NOT NULL,
  finished_at      TEXT,
  files_seen       INTEGER,
  files_moved      INTEGER,
  files_review     INTEGER,
  errors           INTEGER,
  cost_usd         REAL,
  files_duplicates INTEGER
);

INSERT INTO runs (id, mode, trigger, started_at, finished_at, files_seen, files_moved,
                  files_review, errors, cost_usd, files_duplicates)
SELECT id, mode, trigger, started_at, finished_at, files_seen, files_moved, files_review, errors,
       cost_usd, files_duplicates
FROM runs_before_0018;

DROP TABLE runs_before_0018;
