CREATE TABLE scenes (
  id TEXT PRIMARY KEY,
  profile_name TEXT NOT NULL,
  flow_project_id TEXT NOT NULL,
  flow_scene_id TEXT NOT NULL,
  total_duration REAL,
  source TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(profile_name) REFERENCES profiles(name),
  UNIQUE(profile_name, flow_scene_id)
);

CREATE TABLE scene_clips (
  id TEXT PRIMARY KEY,
  scene_id TEXT NOT NULL,
  position INTEGER NOT NULL,
  flow_instance_workflow_id TEXT NOT NULL,
  flow_source_workflow_id TEXT,
  flow_media_id TEXT,
  start_time REAL NOT NULL,
  end_time REAL NOT NULL,
  total_duration REAL NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(scene_id) REFERENCES scenes(id),
  UNIQUE(scene_id, position)
);

CREATE INDEX idx_scenes_profile_flow ON scenes(profile_name, flow_scene_id);
CREATE INDEX idx_scene_clips_scene ON scene_clips(scene_id, position);
