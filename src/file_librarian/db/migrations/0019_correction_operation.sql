-- 0019: which journal entry recorded a correction (#115).
--
-- corrections.operation_id is the `flib fix` move that recorded it; NULL for
-- corrections `flib reconcile` found (moves made by hand) and for older rows.
-- Undoing that move deletes the corrections it recorded, so an undone fix is
-- never sent to the classifier as an example, and never hides an older
-- correction of the same file that still holds. A batch of fixes (the
-- interface's "accept all") is one run: its identical corrections are sent
-- as one example (`classifier.correction_examples`).

ALTER TABLE corrections ADD COLUMN operation_id INTEGER REFERENCES operations(id);

CREATE INDEX corrections_operation ON corrections(operation_id);
