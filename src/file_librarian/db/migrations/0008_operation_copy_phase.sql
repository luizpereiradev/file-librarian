-- 0008: how far a move to another volume got (RF-33): 'copying', 'copied' or
-- 'placed' while it runs, NULL otherwise. Crash recovery reads it to finish
-- or roll back the copy; on a 'done' entry it marks an undo cut off mid-copy.
-- copy_key names the copy's staging and trash paths (the entry's id and a
-- random suffix, so a reset database never reuses them).

ALTER TABLE operations ADD COLUMN copy_phase TEXT;
ALTER TABLE operations ADD COLUMN copy_key TEXT;
