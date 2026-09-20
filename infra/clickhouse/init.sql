CREATE DATABASE IF NOT EXISTS nomore500s;

CREATE TABLE IF NOT EXISTS nomore500s.traces
(
    trace_id UUID,
    project_id LowCardinality(String),
    name String,
    started_at DateTime64(3, 'UTC'),
    duration_ms Float64,
    total_tokens UInt64,
    total_cost_usd Decimal64(8),
    status LowCardinality(String),
    attributes_json String
)
ENGINE = MergeTree
ORDER BY (project_id, started_at, trace_id);

CREATE TABLE IF NOT EXISTS nomore500s.spans
(
    trace_id UUID,
    span_id UUID,
    parent_span_id Nullable(UUID),
    type LowCardinality(String),
    name String,
    started_at DateTime64(3, 'UTC'),
    duration_ms Float64,
    status LowCardinality(String),
    token_count UInt64,
    cost_usd Decimal64(8),
    input_json String,
    output_json String,
    attributes_json String
)
ENGINE = MergeTree
ORDER BY (trace_id, started_at, span_id);

