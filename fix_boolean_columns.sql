-- Fix boolean column type mismatch in PostgreSQL
-- Run this to convert INTEGER columns to BOOLEAN

-- has_document: drop default, change type, set new default
ALTER TABLE rag_threads ALTER COLUMN has_document DROP DEFAULT;
ALTER TABLE rag_threads ALTER COLUMN has_document TYPE BOOLEAN USING has_document::int::boolean;
ALTER TABLE rag_threads ALTER COLUMN has_document SET DEFAULT false;

-- lesson_finalized: drop default, change type, set new default
ALTER TABLE rag_threads ALTER COLUMN lesson_finalized DROP DEFAULT;
ALTER TABLE rag_threads ALTER COLUMN lesson_finalized TYPE BOOLEAN USING lesson_finalized::int::boolean;
ALTER TABLE rag_threads ALTER COLUMN lesson_finalized SET DEFAULT false;
