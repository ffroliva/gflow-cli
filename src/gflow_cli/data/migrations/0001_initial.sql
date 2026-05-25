CREATE TABLE schema_migrations (
  version TEXT PRIMARY KEY,
  filename TEXT NOT NULL,
  checksum TEXT NOT NULL,
  applied_at TEXT NOT NULL
);

CREATE TABLE profiles (
  name TEXT PRIMARY KEY,
  profile_dir TEXT,
  first_seen_at TEXT NOT NULL,
  last_used_at TEXT
);

CREATE TABLE projects (
  id TEXT PRIMARY KEY,
  profile_name TEXT NOT NULL,
  flow_project_id TEXT NOT NULL,
  title TEXT,
  source TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(profile_name) REFERENCES profiles(name),
  UNIQUE(profile_name, flow_project_id)
);

CREATE TABLE assets (
  id TEXT PRIMARY KEY,
  profile_name TEXT NOT NULL,
  flow_project_id TEXT,
  flow_media_id TEXT NOT NULL,
  flow_workflow_id TEXT,
  flow_media_generation_id TEXT,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  model TEXT,
  aspect_ratio TEXT,
  width INTEGER,
  height INTEGER,
  duration_seconds REAL,
  seed INTEGER,
  created_at TEXT NOT NULL,
  metadata_json TEXT,
  FOREIGN KEY(profile_name) REFERENCES profiles(name),
  UNIQUE(profile_name, flow_media_id)
);

CREATE TABLE operations (
  id TEXT PRIMARY KEY,
  profile_name TEXT NOT NULL,
  flow_project_id TEXT,
  command TEXT,
  mode TEXT NOT NULL,
  prompt TEXT,
  prompt_hash TEXT,
  prompt_redacted INTEGER NOT NULL DEFAULT 0,
  model TEXT,
  aspect_ratio TEXT,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  error_type TEXT,
  error_detail TEXT,
  flow_operation_id TEXT,
  flow_batch_id TEXT,
  FOREIGN KEY(profile_name) REFERENCES profiles(name)
);

CREATE TABLE operation_assets (
  operation_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  role TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY(operation_id) REFERENCES operations(id),
  FOREIGN KEY(asset_id) REFERENCES assets(id),
  UNIQUE(operation_id, role, position),
  PRIMARY KEY(operation_id, asset_id, role, position)
);

CREATE TABLE local_files (
  id TEXT PRIMARY KEY,
  profile_name TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  path TEXT NOT NULL,
  sha256 TEXT,
  bytes INTEGER,
  media_type TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(asset_id) REFERENCES assets(id),
  UNIQUE(asset_id, path)
);

CREATE INDEX idx_projects_profile_flow ON projects(profile_name, flow_project_id);
CREATE INDEX idx_assets_profile_media ON assets(profile_name, flow_media_id);
CREATE INDEX idx_assets_project_created ON assets(profile_name, flow_project_id, created_at);
CREATE INDEX idx_assets_kind_created ON assets(kind, created_at);
CREATE INDEX idx_operations_profile_created ON operations(profile_name, started_at);
CREATE INDEX idx_operations_project_created ON operations(profile_name, flow_project_id, started_at);
CREATE INDEX idx_operation_assets_asset ON operation_assets(asset_id);
CREATE INDEX idx_local_files_asset ON local_files(profile_name, asset_id);
CREATE INDEX idx_local_files_path ON local_files(path);
