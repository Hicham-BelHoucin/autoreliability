CREATE TABLE IF NOT EXISTS vehicle_reliability (
    id BIGSERIAL NOT NULL,
    make VARCHAR(60) NOT NULL,
    model VARCHAR(60) NOT NULL,
    year INT NOT NULL CHECK (year BETWEEN 1886 AND 2200),
    reliability_score NUMERIC(3,1) NOT NULL CHECK (reliability_score BETWEEN 1.0 AND 10.0),
    total_complaints INT NOT NULL DEFAULT 0 CHECK (total_complaints >= 0),
    crash_reports INT NOT NULL DEFAULT 0 CHECK (crash_reports >= 0),
    fire_reports INT NOT NULL DEFAULT 0 CHECK (fire_reports >= 0),
    primary_failure_component VARCHAR(120),
    component_breakdown JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(component_breakdown) = 'array'),
    ai_summary TEXT,
    last_synced_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id, year),
    UNIQUE (make, model, year)
) PARTITION BY RANGE (year);

CREATE TABLE IF NOT EXISTS vehicle_reliability_pre2010
    PARTITION OF vehicle_reliability FOR VALUES FROM (MINVALUE) TO (2010);
CREATE TABLE IF NOT EXISTS vehicle_reliability_2010_2020
    PARTITION OF vehicle_reliability FOR VALUES FROM (2010) TO (2021);
CREATE TABLE IF NOT EXISTS vehicle_reliability_post2020
    PARTITION OF vehicle_reliability FOR VALUES FROM (2021) TO (MAXVALUE);

CREATE INDEX IF NOT EXISTS idx_vehicle_reliability_make_model_year
    ON vehicle_reliability (make, model, year);
CREATE INDEX IF NOT EXISTS idx_vehicle_reliability_components_gin
    ON vehicle_reliability USING GIN (component_breakdown jsonb_path_ops);

CREATE TABLE IF NOT EXISTS ingestion_dlq (
    id SERIAL PRIMARY KEY,
    source VARCHAR(50) NOT NULL,
    payload JSONB NOT NULL,
    error_message TEXT NOT NULL,
    failed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ingestion_dlq_failed_at ON ingestion_dlq (failed_at DESC);

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_top_unreliable_vehicles AS
SELECT
    make,
    model,
    year,
    total_complaints,
    crash_reports,
    fire_reports,
    reliability_score,
    primary_failure_component,
    ROW_NUMBER() OVER (ORDER BY total_complaints DESC, crash_reports DESC, make, model, year) AS rank
FROM vehicle_reliability;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_top_unreliable_vehicles_unique
    ON mv_top_unreliable_vehicles (make, model, year);
CREATE INDEX IF NOT EXISTS idx_mv_top_unreliable_vehicles_rank
    ON mv_top_unreliable_vehicles (rank);
