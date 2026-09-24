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

-- Additive source retention and enrichment tables.  The composite reference is
-- intentional: vehicle_reliability is partitioned and its primary key is (id, year).
CREATE TABLE IF NOT EXISTS raw_nhtsa_complaints (
    id BIGSERIAL PRIMARY KEY,
    vehicle_id BIGINT NOT NULL,
    vehicle_year INT NOT NULL,
    odi_number VARCHAR(40) NOT NULL,
    component_categories TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    narrative TEXT,
    odometer_miles INT,
    crash BOOLEAN NOT NULL DEFAULT FALSE,
    fire BOOLEAN NOT NULL DEFAULT FALSE,
    injury_count INT NOT NULL DEFAULT 0 CHECK (injury_count >= 0),
    raw_payload JSONB NOT NULL,
    source_updated_at TIMESTAMP WITH TIME ZONE,
    fetched_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    UNIQUE (vehicle_id, vehicle_year, odi_number),
    FOREIGN KEY (vehicle_id, vehicle_year) REFERENCES vehicle_reliability (id, year) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_raw_nhtsa_complaints_vehicle_component
    ON raw_nhtsa_complaints (vehicle_id, vehicle_year);
CREATE INDEX IF NOT EXISTS idx_raw_nhtsa_complaints_components_gin
    ON raw_nhtsa_complaints USING GIN (component_categories);

CREATE TABLE IF NOT EXISTS nhtsa_complaint_embeddings (
    complaint_id BIGINT NOT NULL REFERENCES raw_nhtsa_complaints (id) ON DELETE CASCADE,
    embedding_model VARCHAR(100) NOT NULL,
    content_hash CHAR(64) NOT NULL,
    embedding JSONB NOT NULL CHECK (jsonb_typeof(embedding) = 'array'),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    PRIMARY KEY (complaint_id, embedding_model, content_hash)
);

CREATE TABLE IF NOT EXISTS nhtsa_tsbs (
    id BIGSERIAL PRIMARY KEY,
    vehicle_id BIGINT NOT NULL,
    vehicle_year INT NOT NULL,
    tsb_id VARCHAR(120) NOT NULL,
    summary TEXT NOT NULL,
    source_url TEXT,
    raw_payload JSONB,
    UNIQUE (vehicle_id, vehicle_year, tsb_id),
    FOREIGN KEY (vehicle_id, vehicle_year) REFERENCES vehicle_reliability (id, year) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS vehicle_failure_clusters (
    id BIGSERIAL PRIMARY KEY,
    vehicle_id BIGINT NOT NULL,
    vehicle_year INT NOT NULL,
    component_category VARCHAR(120) NOT NULL,
    failure_title VARCHAR(240) NOT NULL,
    complaint_count INT NOT NULL CHECK (complaint_count >= 1),
    crash_count INT NOT NULL DEFAULT 0 CHECK (crash_count >= 0),
    fire_count INT NOT NULL DEFAULT 0 CHECK (fire_count >= 0),
    injury_count INT NOT NULL DEFAULT 0 CHECK (injury_count >= 0),
    mileage_p25 INT,
    mileage_median INT,
    mileage_p75 INT,
    symptoms TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    inspection_advice TEXT NOT NULL,
    matched_tsb_id VARCHAR(120),
    geo_summary TEXT NOT NULL,
    representative_complaint_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    generated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    FOREIGN KEY (vehicle_id, vehicle_year) REFERENCES vehicle_reliability (id, year) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_vehicle_failure_clusters_vehicle
    ON vehicle_failure_clusters (vehicle_id, vehicle_year, component_category);

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
