-- 0008: how far a move to another volume got (RF-33): 'copying', 'copied' or
-- 'placed' while it runs, NULL otherwise. Crash recovery reads it to finish
-- or roll back the copy; on a 'done' entry it marks an undo cut off mid-copy.

ALTER TABLE operations ADD COLUMN copy_phase TEXT;
