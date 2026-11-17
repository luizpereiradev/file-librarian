-- 0005: the Finder tag a move added (RF-36), so undo removes exactly that tag
-- and never one the user had already set. NULL when no tag was added.

ALTER TABLE operations ADD COLUMN finder_tag TEXT;
