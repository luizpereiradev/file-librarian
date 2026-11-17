-- 0008: how far a move to another volume got (RF-33): 'copying', 'copied' or
-- 'placed' while it runs, NULL otherwise. Crash recovery reads it to finish
-- or roll back the copy; on a 'done' entry it marks an undo cut off mid-copy.
-- copy_key names the copy's staging and trash paths (the entry's id and a
-- random suffix, so a reset database never reuses them). copy_src_dev and
-- copy_dst_dev are the devices of the copy's two folders when it started, so
-- recovery tells an unmounted volume from a folder deleted on a mounted one.

ALTER TABLE operations ADD COLUMN copy_phase TEXT;
ALTER TABLE operations ADD COLUMN copy_key TEXT;
ALTER TABLE operations ADD COLUMN copy_src_dev INTEGER;
ALTER TABLE operations ADD COLUMN copy_dst_dev INTEGER;
