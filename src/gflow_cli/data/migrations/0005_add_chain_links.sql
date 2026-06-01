-- Chain-link correlation for the sequential last-frame I2V chain orchestrator.
-- One row per completed link, written BEFORE the next link's frame extraction
-- (record-before-extract). This lets `gflow video chain --resume-from` skip
-- already-paid links and lets a crash in the download->extract gap resume at
-- extraction rather than regenerating the (already paid-for) clip:
--   * a row with seed_frame_path SET    -> link fully done (clip + seed frame)
--   * a row with seed_frame_path NULL   -> clip on disk, restart at extraction
--     (also the legitimate state of the FINAL link, which seeds nothing)
-- Keyed by (chain_id, link_index): the orchestrator owns chain_id; link_index
-- is the 0-based position. Additive only — no existing table is touched.
CREATE TABLE chain_links (
  id TEXT PRIMARY KEY,
  profile_name TEXT NOT NULL,
  chain_id TEXT NOT NULL,
  link_index INTEGER NOT NULL,
  flow_project_id TEXT,
  flow_media_id TEXT NOT NULL,
  flow_operation_id TEXT,
  prompt TEXT,
  local_path TEXT NOT NULL,
  seed_frame_path TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(profile_name) REFERENCES profiles(name),
  UNIQUE(profile_name, chain_id, link_index)
);

CREATE INDEX idx_chain_links_chain ON chain_links(profile_name, chain_id, link_index);
