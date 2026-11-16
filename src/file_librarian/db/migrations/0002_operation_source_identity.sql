-- 0002: remember which inode was moved, so crash recovery recognises a moved
-- file even if it was edited before the next run (a rename keeps the inode).

ALTER TABLE operations ADD COLUMN src_dev INTEGER;
ALTER TABLE operations ADD COLUMN src_ino INTEGER;
