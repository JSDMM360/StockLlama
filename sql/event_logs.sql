-- Event log table. Created automatically on first start (CREATE TABLE IF NOT EXISTS),
-- so you normally never need to run this by hand. Lives in the same database as the
-- inventory tables (DB_CONFIG in config.py).
CREATE TABLE IF NOT EXISTS event_logs (
    id                SERIAL PRIMARY KEY,                -- the "Event ID"
    event_uid         VARCHAR(32) NOT NULL UNIQUE,       -- client-generated id (used to attach later events)
    logged_at         TIMESTAMP   NOT NULL DEFAULT NOW(),-- date and time of the input
    source            VARCHAR(20) NOT NULL DEFAULT 'typed', -- typed | voice | file | manual
    user_input        TEXT        NOT NULL,              -- the input text / payload
    model_output      TEXT,                              -- raw model response(s)
    model_name        VARCHAR(120),                      -- model identifier, e.g. tinyllama:latest
    inference_ms      INTEGER,                           -- total inference time (ms)
    prompt_tokens     INTEGER,                           -- tokens in the prompt(s)
    completion_tokens INTEGER,                           -- tokens generated
    meta              JSONB       NOT NULL DEFAULT '{}'::jsonb,
    events            JSONB       NOT NULL DEFAULT '[]'::jsonb  -- timeline: parsed, saved, discarded, errors ...
);
CREATE INDEX IF NOT EXISTS idx_event_logs_logged_at ON event_logs (logged_at DESC);

-- Handy queries
-- Token usage per model:
--   SELECT model_name, count(*), sum(prompt_tokens) AS prompt, sum(completion_tokens) AS completion,
--          round(avg(inference_ms)) AS avg_ms FROM event_logs WHERE model_name IS NOT NULL GROUP BY 1;
-- Slowest requests:
--   SELECT id, logged_at, inference_ms, left(user_input, 60) FROM event_logs ORDER BY inference_ms DESC NULLS LAST LIMIT 10;
