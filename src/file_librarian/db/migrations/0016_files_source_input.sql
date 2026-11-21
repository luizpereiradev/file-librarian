-- 0016: which input folder each file was taken from, and how it was filed (#89).
--
-- files.source_input is the input folder (as configured, NFC) the run that
-- first indexed the file found it in. It is kept from then on: a file dragged
-- into another input folder later has its place in current_path, not here.
-- `flib status` counts files per input with it. NULL on rows filed before
-- this migration: their input is the folder of original_path, since flib only
-- ever reads the top level of an input.
--
-- files.filed_by_rule is 1 when the file was filed by its input's fixed
-- category, with no AI answer; `flib reprocess` never asks the AI about those.
-- A correction (`flib fix`, or a move reconcile records) clears it.
--
-- files.sensitive_by says what made a sensitive file sensitive: 'name' (its
-- name alone, looked at again on every scan, so a renamed file or a changed
-- rule lets it through) or 'content' (a secret in it: it stays sensitive);
-- files.sensitive_reason says it in words, for `flib status --sensitive`.
-- NULL on rows from before this migration and on files that are not sensitive.

ALTER TABLE files ADD COLUMN source_input TEXT;
ALTER TABLE files ADD COLUMN filed_by_rule INTEGER NOT NULL DEFAULT 0;
ALTER TABLE files ADD COLUMN sensitive_by TEXT CHECK (sensitive_by IN ('name', 'content'));
ALTER TABLE files ADD COLUMN sensitive_reason TEXT;
