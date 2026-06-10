-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Universal knowledge chunks table
-- One row = one semantic chunk from any source type (json, audio, video)
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    chunk_id      TEXT PRIMARY KEY,
    source_type   TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    source_name   TEXT NOT NULL,
    chunk_index   INTEGER NOT NULL,
    text          TEXT NOT NULL,
    metadata      JSONB DEFAULT '{}',
    embedding     vector(768),
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- IVFFlat index for fast cosine similarity search
CREATE INDEX IF NOT EXISTS knowledge_chunks_embedding_idx
    ON knowledge_chunks
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 10);

-- Indexes for filtering by source type and source id
CREATE INDEX IF NOT EXISTS knowledge_chunks_source_type_idx
    ON knowledge_chunks (source_type);

CREATE INDEX IF NOT EXISTS knowledge_chunks_source_id_idx
    ON knowledge_chunks (source_id);

-- RPC function called by search_knowledge_db tool
CREATE OR REPLACE FUNCTION match_knowledge(
    query_embedding   vector(768),
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
