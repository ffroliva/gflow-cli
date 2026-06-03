-- Add metadata_json column to operations to support feature-specific payloads
-- (e.g. character entity_id, workflow_ids, primary_media_ids) without schema
-- proliferation. Mirrors the existing assets.metadata_json column. NULL for
-- all legacy rows; populated only by recorder methods that need extra fields.
ALTER TABLE operations ADD COLUMN metadata_json TEXT;
