-- Add cloud storage columns to local_files.
--
-- Strategy: ADD COLUMN only (no table rebuild) — for cloud-only files the
-- `path` column holds the cloud URI as the UNIQUE key (satisfying the
-- existing NOT NULL constraint).  Use `storage_provider IS NOT NULL` in
-- application code to distinguish cloud rows from local rows.
--
-- Backward compatibility: all existing rows keep storage_provider = NULL,
-- which is treated as "local" throughout the application.

ALTER TABLE local_files ADD COLUMN storage_provider TEXT;
-- "gcs", "s3", or NULL (= local)

ALTER TABLE local_files ADD COLUMN cloud_uri TEXT;
-- Full URI, e.g. gs://my-bucket/gflow/images/2026-05-27/abc_1.jpg
-- NULL for local-only rows.

CREATE INDEX idx_local_files_cloud_uri
    ON local_files(cloud_uri)
    WHERE cloud_uri IS NOT NULL;
