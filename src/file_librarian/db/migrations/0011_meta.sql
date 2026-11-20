-- 0011: a small key-value table for flib's own bookkeeping (#38).
--
-- The first key is 'summary.last_at': when the periodic summary of what waits
-- in the review folder or failed was last written (UTC, ISO 8601), so it
-- comes at most once every summary.every_days.

CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
