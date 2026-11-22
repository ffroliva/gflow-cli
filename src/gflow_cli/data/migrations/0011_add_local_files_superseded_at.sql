-- #1299: Track superseded local files from in-place clip extends.
--
-- Adds superseded_at column to local_files so stale copies can be invalidated
-- without NULLing sha256 (keeping doctor checks clean), and filtered out from
-- catalog queries and path resolution.

ALTER TABLE local_files ADD COLUMN superseded_at TEXT;

CREATE INDEX idx_local_files_superseded_at
    ON local_files(superseded_at)
    WHERE superseded_at IS NOT NULL;
