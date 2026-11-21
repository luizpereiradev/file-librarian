-- 0017: what is due, and what was already paid or done (#91).
--
-- files.settled_at is when the user marked a document with a due date as
-- paid or done (`flib fix PATH --done`, UTC, ISO 8601); NULL while it is
-- open. `flib due` leaves settled documents out unless asked for --all.
-- Setting or clearing it is journaled in index_changes with a run of its own,
-- so `flib undo` gives back what it was.
--
-- files.due_reminded_for is the due date the periodic summary last counted
-- the document in a due reminder for (YYYY-MM-DD): at most one reminder per
-- document and due date. A due date that changes (a reprocess) may be
-- reminded once more. NULL until then.
--
-- SQLite cannot change a CHECK constraint in place, so index_changes is
-- rebuilt with the same rows and ids (as in 0015).

ALTER TABLE files ADD COLUMN settled_at TEXT;
ALTER TABLE files ADD COLUMN due_reminded_for TEXT;

CREATE TEMP TABLE index_changes_before_0017 AS SELECT * FROM index_changes;

DROP TABLE index_changes;

CREATE TABLE index_changes (
  id         INTEGER PRIMARY KEY,
  run_id     INTEGER NOT NULL REFERENCES runs(id),
  target     TEXT NOT NULL
             CHECK (target IN ('files.category', 'files.description', 'files.tags',
                               'files.confidence', 'files.provider', 'files.model',
                               'files.name_choice', 'files.name_choice_reason',
                               'files.processed_at', 'files.review_reason',
                               'files.review_category', 'files.document_date',
                               'files.issuer', 'files.layout_date', 'files.amount_minor',
                               'files.currency', 'files.due_date', 'files.period',
                               'files.kind', 'files.settled_at', 'corrections.old_value',
                               'corrections.new_value', 'operations.finder_tag')),
  row_id     INTEGER NOT NULL,
  old_value  TEXT,
  new_value  TEXT,
  status     TEXT NOT NULL CHECK (status IN ('done', 'undone')),
  created_at TEXT NOT NULL
);

INSERT INTO index_changes (id, run_id, target, row_id, old_value, new_value, status, created_at)
SELECT id, run_id, target, row_id, old_value, new_value, status, created_at
FROM index_changes_before_0017;

DROP TABLE index_changes_before_0017;

CREATE INDEX index_changes_run ON index_changes(run_id);
