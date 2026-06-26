CREATE TABLE generation_queue (
  task_id TEXT PRIMARY KEY,
  profile_name TEXT NOT NULL,
  task_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('pending', 'processing', 'completed', 'failed')),
  flow_media_id TEXT,
  error_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(profile_name) REFERENCES profiles(name)
);

CREATE INDEX idx_generation_queue_profile_status ON generation_queue(profile_name, status, created_at);
