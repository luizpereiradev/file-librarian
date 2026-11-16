-- 0004: remember whether a row is a folder unit (identified by its manifest
-- digest) or a single file (identified by its content hash), so reconcile
-- knows how to look for it after it was moved by hand.

ALTER TABLE files ADD COLUMN is_unit INTEGER NOT NULL DEFAULT 0;
