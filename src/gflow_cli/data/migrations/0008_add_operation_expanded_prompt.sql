-- Add expanded_prompt column to operations to persist the Gemini "Creative
-- Director" expansion (see tools/expander.py) alongside the user's original
-- prompt. NULL for all legacy rows and for runs without --expand; populated only
-- when prompt expansion ran AND history_prompts='store' (redacted mode withholds
-- the expanded text just like the original prompt).
ALTER TABLE operations ADD COLUMN expanded_prompt TEXT;
