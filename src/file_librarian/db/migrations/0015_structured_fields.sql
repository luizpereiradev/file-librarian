-- 0015: structured fields of a document (#88).
--
-- What the model read in the document, from its answer, NULL when the
-- document does not state it and on rows filed before this migration:
--
-- files.amount_minor  the total or amount due, in files.currency's minor
--                     units (cents), so sums are exact
-- files.currency      its ISO 4217 code
-- files.due_date      when it is due (YYYY-MM-DD)
-- files.period        what the document refers to (YYYY-MM or YYYY)
-- files.kind          what sort of document it is, from a closed list
--
-- They arrive with the answer and change with it, like files.document_date
-- and files.issuer, so index_changes may now journal them and `flib undo`
-- gives back what a reprocess changed. SQLite cannot change a CHECK
-- constraint in place, so the table is rebuilt with the same rows and ids
-- (as in 0014). Full-text search does not cover them.

ALTER TABLE files ADD COLUMN amount_minor INTEGER CHECK (amount_minor >= 0);
ALTER TABLE files ADD COLUMN currency TEXT CHECK (length(currency) = 3);
ALTER TABLE files ADD COLUMN due_date TEXT;
ALTER TABLE files ADD COLUMN period TEXT;
ALTER TABLE files ADD COLUMN kind TEXT
  CHECK (kind IN ('bill', 'receipt', 'invoice', 'statement', 'contract', 'payslip', 'tax',
                  'ticket', 'policy', 'report', 'form', 'letter', 'manual', 'other'));

CREATE TEMP TABLE index_changes_before_0015 AS SELECT * FROM index_changes;

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
                               'files.kind', 'corrections.old_value',
                               'corrections.new_value', 'operations.finder_tag')),
  row_id     INTEGER NOT NULL,
  old_value  TEXT,
  new_value  TEXT,
  status     TEXT NOT NULL CHECK (status IN ('done', 'undone')),
  created_at TEXT NOT NULL
);

INSERT INTO index_changes (id, run_id, target, row_id, old_value, new_value, status, created_at)
SELECT id, run_id, target, row_id, old_value, new_value, status, created_at
FROM index_changes_before_0015;

DROP TABLE index_changes_before_0015;

CREATE INDEX index_changes_run ON index_changes(run_id);
