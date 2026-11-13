-- Forward-only. Nullable for legacy questions until metadata is recovered.
ALTER TABLE questions ADD COLUMN IF NOT EXISTS domain VARCHAR(255);
ALTER TABLE questions ADD COLUMN IF NOT EXISTS topic_label VARCHAR(255);
ALTER TABLE questions ADD COLUMN IF NOT EXISTS cognitive_level VARCHAR(100);
