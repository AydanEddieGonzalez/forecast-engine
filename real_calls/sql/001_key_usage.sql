-- real and sim API calls table

CREATE SCHEMA IF NOT EXISTS token_router;

CREATE TABLE IF NOT EXISTS token_router.key_usage (
    id            SERIAL PRIMARY KEY,
    ts            TIMESTAMPTZ DEFAULT now(),
    key_alias     TEXT NOT NULL,
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    input_tokens  INT,
    output_tokens INT,
    cost_usd      NUMERIC(10, 6),
    latency_ms    INT,
    status        TEXT,
    source        TEXT DEFAULT 'real'
);
