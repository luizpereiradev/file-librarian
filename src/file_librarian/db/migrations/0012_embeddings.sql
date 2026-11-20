-- 0012: vectors for semantic search (RF-15, #36), only with search.semantic.enabled.
--
-- One row per file: the embedding of what the index already stores about it
-- (names, category, tags, description), never its content, and never for a
-- sensitive file. vector is float32, little-endian, normalized to length 1, so
-- cosine similarity is a dot product. model and text_hash say what it was made
-- from: a vector whose model differs from search.semantic.model, or whose
-- text_hash differs from the hash of the file's text today, is stale and made
-- again by the next run or `flib search --reindex`.

CREATE TABLE embeddings (
  file_id    INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
  model      TEXT NOT NULL,
  dimension  INTEGER NOT NULL CHECK (dimension > 0),
  text_hash  TEXT NOT NULL,
  vector     BLOB NOT NULL,
  created_at TEXT NOT NULL
);
