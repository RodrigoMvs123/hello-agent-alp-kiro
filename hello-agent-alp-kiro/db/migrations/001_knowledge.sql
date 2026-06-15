-- Enable pgvector
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    chunk_id      TEXT PRIMARY KEY,
    source_type   TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    source_name   TEXT NOT NULL,
    chunk_index   INTEGER NOT NULL,
    text          TEXT NOT NULL,
    metadata      JSONB DEFAULT '{}',
    embedding     vector(3072),
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- No vector index — sequential scan works fine for small datasets
-- (Both ivfflat and hnsw are limited to 2000 dims max)

CREATE INDEX IF NOT EXISTS knowledge_chunks_source_type_idx
    ON knowledge_chunks (source_type);

CREATE INDEX IF NOT EXISTS knowledge_chunks_source_id_idx
    ON knowledge_chunks (source_id);

CREATE OR REPLACE FUNCTION match_knowledge(
    query_embedding   vector(3072),
    match_count       int   DEFAULT 3,
    filter_source     text  DEFAULT NULL
)
RETURNS TABLE (
    chunk_id      text,
    source_type   text,
    source_id     text,
    source_name   text,
    chunk_index   int,
    text          text,
    metadata      jsonb,
    score         float
)
LANGUAGE sql STABLE
AS $$
    SELECT
        chunk_id, source_type, source_id, source_name,
        chunk_index, text, metadata,
        1 - (embedding <=> query_embedding) AS score
    FROM knowledge_chunks
    WHERE filter_source IS NULL OR source_type = filter_source
    ORDER BY embedding <=> query_embedding
    LIMIT match_count;
$$;
