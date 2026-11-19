-- 0010: why a file is in review, what each run cost, and when a move was undone (#68).
--
-- files.review_reason is a closed set of codes, set while the file is in the
-- review folder and NULL otherwise: 'low_confidence' (below
-- confidence_threshold), 'unknown_category' (the model chose a category the
-- configuration does not have), 'category_removed' (`flib reprocess
-- --remove-category --to-review`) or 'moved_by_hand' (`flib reconcile` found it
-- there). files.review_category is the category the model suggested, if any.
--
-- runs.cost_usd is the estimated provider cost of the run; NULL when the
-- provider's prices are unknown, and on runs recorded before this migration.
--
-- operations.undone_at is when the move was undone (UTC, ISO 8601); NULL on
-- moves never undone and on those undone before this migration.
--
-- index_changes may now journal the review reason too, so `flib undo` gives
-- back the reason a reprocess or a fix cleared. SQLite cannot change a CHECK
-- constraint in place, so the table is rebuilt with the same rows and ids.

ALTER TABLE files ADD COLUMN review_reason TEXT
  CHECK (review_reason IN ('low_confidence', 'unknown_category', 'category_removed',
                           'moved_by_hand'));
ALTER TABLE files ADD COLUMN review_category TEXT;

ALTER TABLE runs ADD COLUMN cost_usd REAL;

ALTER TABLE operations ADD COLUMN undone_at TEXT;

CREATE TEMP TABLE index_changes_before_0010 AS SELECT * FROM index_changes;

DROP TABLE index_changes;

CREATE TABLE index_changes (
  id         INTEGER PRIMARY KEY,
  run_id     INTEGER NOT NULL REFERENCES runs(id),
  target     TEXT NOT NULL
             CHECK (target IN ('files.category', 'files.description', 'files.tags',
                               'files.confidence', 'files.provider', 'files.model',
                               'files.name_choice', 'files.name_choice_reason',
                               'files.processed_at', 'files.review_reason',
                               'files.review_category', 'corrections.old_value',
                               'corrections.new_value', 'operations.finder_tag')),
  row_id     INTEGER NOT NULL,
  old_value  TEXT,
  new_value  TEXT,
  status     TEXT NOT NULL CHECK (status IN ('done', 'undone')),
  created_at TEXT NOT NULL
);

INSERT INTO index_changes (id, run_id, target, row_id, old_value, new_value, status, created_at)
SELECT id, run_id, target, row_id, old_value, new_value, status, created_at
FROM index_changes_before_0010;

DROP TABLE index_changes_before_0010;

CREATE INDEX index_changes_run ON index_changes(run_id);
