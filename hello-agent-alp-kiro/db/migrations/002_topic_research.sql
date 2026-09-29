-- Migration 002: Topic research — entity timeline tracking
-- Adds a companion table for longitudinal topic/entity monitoring.
-- knowledge_chunks remains unchanged; topic_events stores one row per
-- observed status change per entity, preserving full history.

CREATE TABLE IF NOT EXISTS topic_events (
    id              BIGSERIAL PRIMARY KEY,
    topic           TEXT        NOT NULL,          -- e.g. 'brazil-rbc'
    entity          TEXT        NOT NULL,          -- stable id, e.g. 'sp-pl-107-2023'
    status          TEXT        NOT NULL,          -- e.g. 'first_discussion_approved'
    category        TEXT,                          -- 'A', 'B', 'C', or topic-defined
    event_date      DATE,                          -- when the underlying event happened
    source_url      TEXT,
    source_title    TEXT,
    source_type     TEXT,                          -- official_primary / official_secondary / secondary / discovery
    official_source BOOLEAN     DEFAULT FALSE,
    summary         TEXT,                          -- short human-readable description of this event
    raw_text        TEXT,                          -- full extracted text / snippet stored for reference
    chunk_id        TEXT REFERENCES knowledge_chunks(chunk_id) ON DELETE SET NULL,
    last_verified_at TIMESTAMPTZ DEFAULT NOW(),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- One row per entity per status transition — never update, always insert.
-- Query latest status per entity:
--   SELECT DISTINCT ON (entity) * FROM topic_events
--   WHERE topic = 'brazil-rbc'
--   ORDER BY entity, created_at DESC;

CREATE INDEX IF NOT EXISTS topic_events_topic_idx    ON topic_events (topic);
CREATE INDEX IF NOT EXISTS topic_events_entity_idx   ON topic_events (entity);
CREATE INDEX IF NOT EXISTS topic_events_verified_idx ON topic_events (last_verified_at);
CREATE INDEX IF NOT EXISTS topic_events_topic_entity ON topic_events (topic, entity, created_at DESC);

-- Disable RLS so the service_role key can read/write freely
ALTER TABLE topic_events DISABLE ROW LEVEL SECURITY;
