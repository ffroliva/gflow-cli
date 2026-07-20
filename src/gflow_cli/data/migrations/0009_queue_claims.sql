-- Task C3 (design spec §4): atomic task claims + versioned checkpoint document.
--
-- Additive-only: this migration supersedes 0007's status CHECK by rebuilding
-- the table (SQLite cannot ALTER a CHECK constraint in place). Nothing
-- references generation_queue, so the create-copy-drop-rename rebuild is safe.
-- New columns:
--   claimant        -- identity that holds the pending->processing claim
--   claimed_at      -- ISO-8601 timestamp the claim was taken
--   checkpoint_json -- versioned, redacted checkpoint doc (NO prompts/secrets)
-- Status machine extended: pending -> processing -> completed | failed | indeterminate.

CREATE TABLE generation_queue_new (
  task_id TEXT PRIMARY KEY,
  profile_name TEXT NOT NULL,
  task_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK(
    status IN ('pending', 'processing', 'completed', 'failed', 'indeterminate')
  ),
  flow_media_id TEXT,
  error_json TEXT,
  claimant TEXT,
  claimed_at TEXT,
  checkpoint_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(profile_name) REFERENCES profiles(name)
);

INSERT INTO generation_queue_new (
  task_id, profile_name, task_type, payload_json, status,
  flow_media_id, error_json, created_at, updated_at
)
SELECT
  task_id, profile_name, task_type, payload_json, status,
  flow_media_id, error_json, created_at, updated_at
FROM generation_queue;

DROP TABLE generation_queue;

ALTER TABLE generation_queue_new RENAME TO generation_queue;

CREATE INDEX idx_generation_queue_profile_status
  ON generation_queue(profile_name, status, created_at);
