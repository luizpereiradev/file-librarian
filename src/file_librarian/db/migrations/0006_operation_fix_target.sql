-- 0006: what a `flib fix` (RF-29) sets on its file, so crash recovery can
-- finish the whole correction (status, category, corrections, Finder tag) and
-- not only the move. NULL on moves made by a run.

ALTER TABLE operations ADD COLUMN fix_status TEXT;
ALTER TABLE operations ADD COLUMN fix_category TEXT;
