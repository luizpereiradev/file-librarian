-- 0001: initial schema. Never edit a published migration; add a new numbered file.

CREATE TABLE runs (
  id           INTEGER PRIMARY KEY,
  mode         TEXT NOT NULL CHECK (mode IN ('run', 'simulate')),
  trigger      TEXT NOT NULL CHECK (trigger IN ('schedule', 'manual', 'mcp')),
  started_at   TEXT NOT NULL,
  finished_at  TEXT,
  files_seen   INTEGER,
  files_moved  INTEGER,
  files_review INTEGER,
  errors       INTEGER
);

CREATE TABLE files (
  id            INTEGER PRIMARY KEY,
  sha256        TEXT NOT NULL UNIQUE,
  original_name TEXT NOT NULL,
  original_path TEXT NOT NULL,
  current_name  TEXT NOT NULL,
  current_path  TEXT NOT NULL,
  extension     TEXT,
  size_bytes    INTEGER,
  source_url    TEXT,            -- download origin, read from macOS
  category      TEXT,
  description   TEXT,
  tags          TEXT,            -- JSON array
  confidence    REAL,
  status        TEXT NOT NULL
                CHECK (status IN ('organized', 'review', 'skipped', 'missing', 'failed', 'sensitive')),
  attempts      INTEGER NOT NULL DEFAULT 0,
  last_error    TEXT,
  provider      TEXT,
  model         TEXT,
  run_id        INTEGER REFERENCES runs(id),
  processed_at  TEXT NOT NULL
);

CREATE INDEX files_status ON files(status);
CREATE INDEX files_current_path ON files(current_path);

CREATE TABLE operations (
  id         INTEGER PRIMARY KEY,
  run_id     INTEGER NOT NULL REFERENCES runs(id),
  file_id    INTEGER NOT NULL REFERENCES files(id),
  src_path   TEXT NOT NULL,
  dst_path   TEXT NOT NULL,
  status     TEXT NOT NULL CHECK (status IN ('planned', 'done', 'undone', 'failed', 'skipped')),
  created_at TEXT NOT NULL
);

CREATE INDEX operations_run ON operations(run_id);
CREATE INDEX operations_file ON operations(file_id);

CREATE TABLE corrections (
  id         INTEGER PRIMARY KEY,
  file_id    INTEGER NOT NULL REFERENCES files(id),
  field      TEXT NOT NULL CHECK (field IN ('category', 'name')),
  old_value  TEXT,
  new_value  TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- Full-text index over files. remove_diacritics makes "orcamento" match "orçamento".
CREATE VIRTUAL TABLE files_fts USING fts5(
  original_name, current_name, description, tags,
  content='files', content_rowid='id',
  tokenize='unicode61 remove_diacritics 2'
);

-- Keep the external-content FTS index in step with files.
CREATE TRIGGER files_fts_insert AFTER INSERT ON files BEGIN
  INSERT INTO files_fts(rowid, original_name, current_name, description, tags)
  VALUES (new.id, new.original_name, new.current_name, new.description, new.tags);
END;

CREATE TRIGGER files_fts_delete AFTER DELETE ON files BEGIN
  INSERT INTO files_fts(files_fts, rowid, original_name, current_name, description, tags)
  VALUES ('delete', old.id, old.original_name, old.current_name, old.description, old.tags);
END;

CREATE TRIGGER files_fts_update AFTER UPDATE ON files BEGIN
  INSERT INTO files_fts(files_fts, rowid, original_name, current_name, description, tags)
  VALUES ('delete', old.id, old.original_name, old.current_name, old.description, old.tags);
  INSERT INTO files_fts(rowid, original_name, current_name, description, tags)
  VALUES (new.id, new.original_name, new.current_name, new.description, new.tags);
END;
