-- 0003: remember recent classifications, so `flib run` right after
-- `flib simulate` does not pay for the same answers twice.
-- rules_key identifies everything that shaped the answer (provider, model,
-- effort, categories and hints, language, prompt version).

CREATE TABLE classification_cache (
  sha256     TEXT NOT NULL,
  rules_key  TEXT NOT NULL,
  answer     TEXT NOT NULL,   -- Classification as JSON
  created_at TEXT NOT NULL,
  PRIMARY KEY (sha256, rules_key)
);
