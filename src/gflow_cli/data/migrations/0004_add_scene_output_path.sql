-- Persist the local path of a scene's rendered extended video (server-side
-- concat output). The concat result is ephemeral on Flow's side (no media id),
-- so the local file is the sole artifact — record it for discovery + recovery.
-- Duration is already captured by scenes.total_duration (sum of trimmed clips).
ALTER TABLE scenes ADD COLUMN output_path TEXT;
