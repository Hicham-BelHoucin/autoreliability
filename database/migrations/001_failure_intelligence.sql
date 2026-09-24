-- Additive migration for existing databases. It leaves vehicle_reliability and
-- its scoring columns untouched. New databases receive the same definitions
-- from database/schema.sql.
CREATE TABLE IF NOT EXISTS raw_nhtsa_complaints (
    id BIGSERIAL PRIMARY KEY, vehicle_id BIGINT NOT NULL, vehicle_year INT NOT NULL,
    odi_number VARCHAR(40) NOT NULL, component_categories TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    narrative TEXT, odometer_miles INT, crash BOOLEAN NOT NULL DEFAULT FALSE,
    fire BOOLEAN NOT NULL DEFAULT FALSE, injury_count INT NOT NULL DEFAULT 0 CHECK (injury_count >= 0),
    raw_payload JSONB NOT NULL, source_updated_at TIMESTAMP WITH TIME ZONE,
    fetched_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(), UNIQUE (vehicle_id, vehicle_year, odi_number),
    FOREIGN KEY (vehicle_id, vehicle_year) REFERENCES vehicle_reliability (id, year) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_raw_nhtsa_complaints_vehicle_component ON raw_nhtsa_complaints (vehicle_id, vehicle_year);
CREATE INDEX IF NOT EXISTS idx_raw_nhtsa_complaints_components_gin ON raw_nhtsa_complaints USING GIN (component_categories);
CREATE TABLE IF NOT EXISTS nhtsa_complaint_embeddings (
    complaint_id BIGINT NOT NULL REFERENCES raw_nhtsa_complaints (id) ON DELETE CASCADE,
    embedding_model VARCHAR(100) NOT NULL, content_hash CHAR(64) NOT NULL,
    embedding JSONB NOT NULL CHECK (jsonb_typeof(embedding) = 'array'),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(), PRIMARY KEY (complaint_id, embedding_model, content_hash)
);
CREATE TABLE IF NOT EXISTS nhtsa_tsbs (
    id BIGSERIAL PRIMARY KEY, vehicle_id BIGINT NOT NULL, vehicle_year INT NOT NULL,
    tsb_id VARCHAR(120) NOT NULL, summary TEXT NOT NULL, source_url TEXT, raw_payload JSONB,
    UNIQUE (vehicle_id, vehicle_year, tsb_id),
    FOREIGN KEY (vehicle_id, vehicle_year) REFERENCES vehicle_reliability (id, year) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS vehicle_failure_clusters (
    id BIGSERIAL PRIMARY KEY, vehicle_id BIGINT NOT NULL, vehicle_year INT NOT NULL,
    component_category VARCHAR(120) NOT NULL, failure_title VARCHAR(240) NOT NULL,
    complaint_count INT NOT NULL CHECK (complaint_count >= 1), crash_count INT NOT NULL DEFAULT 0 CHECK (crash_count >= 0),
    fire_count INT NOT NULL DEFAULT 0 CHECK (fire_count >= 0), injury_count INT NOT NULL DEFAULT 0 CHECK (injury_count >= 0),
    mileage_p25 INT, mileage_median INT, mileage_p75 INT, symptoms TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    inspection_advice TEXT NOT NULL, matched_tsb_id VARCHAR(120), geo_summary TEXT NOT NULL,
    representative_complaint_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    generated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    FOREIGN KEY (vehicle_id, vehicle_year) REFERENCES vehicle_reliability (id, year) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_vehicle_failure_clusters_vehicle ON vehicle_failure_clusters (vehicle_id, vehicle_year, component_category);
