-- #1198: `gflow cancel <id>` and MCP tasks/cancel share one local abort.
--
-- Rebuilds generation_queue to widen 0009's status CHECK (SQLite cannot ALTER a
-- CHECK constraint in place). Nothing references generation_queue, so the
-- create-copy-drop-rename rebuild is safe; every column is copied.
-- New statuses:
--   cancelled -- aborted while still pending: never submitted, nothing charged
--   abandoned -- aborted after a worker claimed it: gflow stopped watching, but a
--                generation Flow accepted keeps running and may still be charged.
--                Not final: the worker running it may still record its outcome.

CREATE TABLE generation_queue_new (
  task_id TEXT PRIMARY KEY,
  profile_name TEXT NOT NULL,
  task_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK(
    status IN (
      'pending', 'processing', 'completed', 'failed', 'indeterminate',
      'cancelled', 'abandoned'
    )
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
  task_id, profile_name, task_type, payload_json, status, flow_media_id,
  error_json, claimant, claimed_at, checkpoint_json, created_at, updated_at
)
SELECT
  task_id, profile_name, task_type, payload_json, status, flow_media_id,
  error_json, claimant, claimed_at, checkpoint_json, created_at, updated_at
FROM generation_queue;

DROP TABLE generation_queue;

ALTER TABLE generation_queue_new RENAME TO generation_queue;

CREATE INDEX idx_generation_queue_profile_status
  ON generation_queue(profile_name, status, created_at);
