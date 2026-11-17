-- 0007: which name the model judged better when flib filed a file (#59):
-- 'original' (its subject was kept) or 'proposed' (the model's new name), with
-- the model's short reason. NULL when no rename was considered (no readable
-- content, review, folder units) and on rows written before this migration.

ALTER TABLE files ADD COLUMN name_choice TEXT;
ALTER TABLE files ADD COLUMN name_choice_reason TEXT;
