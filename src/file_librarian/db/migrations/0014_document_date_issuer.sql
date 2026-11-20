-- 0014: what category layouts file by (#86).
--
-- files.document_date is the date the document refers to (YYYY-MM-DD), as the
-- model read it; files.issuer the organisation it comes from. Both arrive
-- with the answer and change with it. A layout's {year} and {month} come
-- from document_date, its {issuer} from issuer. NULL on rows filed before
-- this migration, and whenever the model gave none.
--
-- index_changes may now journal both, like the other answer columns, so
-- `flib undo` gives back what a reprocess changed. SQLite cannot change a
-- CHECK constraint in place, so the table is rebuilt with the same rows and
-- ids (as in 0010).

ALTER TABLE files ADD COLUMN document_date TEXT;
ALTER TABLE files ADD COLUMN issuer TEXT;

CREATE TEMP TABLE index_changes_before_0014 AS SELECT * FROM index_changes;

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
                               'files.issuer', 'corrections.old_value',
                               'corrections.new_value', 'operations.finder_tag')),
  row_id     INTEGER NOT NULL,
  old_value  TEXT,
  new_value  TEXT,
  status     TEXT NOT NULL CHECK (status IN ('done', 'undone')),
  created_at TEXT NOT NULL
);

INSERT INTO index_changes (id, run_id, target, row_id, old_value, new_value, status, created_at)
SELECT id, run_id, target, row_id, old_value, new_value, status, created_at
FROM index_changes_before_0014;

DROP TABLE index_changes_before_0014;

CREATE INDEX index_changes_run ON index_changes(run_id);
