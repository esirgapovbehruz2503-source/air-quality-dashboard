PRAGMA foreign_keys = ON;

-- 1. Five fixed study locations
CREATE TABLE IF NOT EXISTS locations (
    location_id INTEGER PRIMARY KEY AUTOINCREMENT,
    city_name TEXT NOT NULL UNIQUE,
    requested_latitude REAL NOT NULL CHECK (requested_latitude BETWEEN -90.0 AND 90.0),
    requested_longitude REAL NOT NULL CHECK (requested_longitude BETWEEN -180.0 AND 180.0),
    model_domain TEXT NOT NULL DEFAULT 'cams_global' CHECK (model_domain = 'cams_global'),
    created_at_utc TEXT NOT NULL CHECK (length(created_at_utc) = 20 AND created_at_utc LIKE '____-__-__T__:__:__Z')
);

-- 2. Scheduled 6-hourly collection slots (5 locations x 4 slots x 21 days = 420)
CREATE TABLE IF NOT EXISTS collection_slots (
    slot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id INTEGER NOT NULL,
    scheduled_collection_utc TEXT NOT NULL CHECK (length(scheduled_collection_utc) = 20 AND scheduled_collection_utc LIKE '____-__-__T__:__:__Z'),
    required_valid_utc TEXT NOT NULL CHECK (length(required_valid_utc) = 20 AND required_valid_utc LIKE '____-__-__T__:__:__Z'),
    acceptance_deadline_utc TEXT NOT NULL CHECK (length(acceptance_deadline_utc) = 20 AND acceptance_deadline_utc LIKE '____-__-__T__:__:__Z'),
    slot_status TEXT NOT NULL DEFAULT 'pending' CHECK (slot_status IN ('pending', 'collected', 'missed')),
    CHECK (acceptance_deadline_utc > scheduled_collection_utc),
    FOREIGN KEY (location_id) REFERENCES locations(location_id),
    UNIQUE (location_id, scheduled_collection_utc),
    UNIQUE (location_id, required_valid_utc)
);

-- 3. Every API request attempt (successes, errors, timeouts, late rejections)
CREATE TABLE IF NOT EXISTS api_requests (
    request_id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id INTEGER NOT NULL,
    request_url TEXT NOT NULL,
    requested_at_utc TEXT NOT NULL CHECK (length(requested_at_utc) = 20 AND requested_at_utc LIKE '____-__-__T__:__:__Z'),
    responded_at_utc TEXT CHECK (responded_at_utc IS NULL OR (length(responded_at_utc) = 20 AND responded_at_utc LIKE '____-__-__T__:__:__Z')),
    http_status INTEGER,
    response_hash TEXT,
    raw_response_path TEXT,
    request_status TEXT NOT NULL CHECK (request_status IN ('success', 'http_error', 'network_error', 'validation_error', 'late_rejected')),
    error_message TEXT,
    FOREIGN KEY (slot_id) REFERENCES collection_slots(slot_id)
);

-- 4. Accepted observations (is_primary = 1) and later revisions (is_primary = 0)
CREATE TABLE IF NOT EXISTS observations (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id INTEGER NOT NULL,
    request_id INTEGER NOT NULL,
    location_id INTEGER NOT NULL,
    valid_utc TEXT NOT NULL CHECK (length(valid_utc) = 20 AND valid_utc LIKE '____-__-__T__:__:__Z'),
    collected_at_utc TEXT NOT NULL CHECK (length(collected_at_utc) = 20 AND collected_at_utc LIKE '____-__-__T__:__:__Z'),
    returned_latitude REAL NOT NULL CHECK (returned_latitude BETWEEN -90.0 AND 90.0),
    returned_longitude REAL NOT NULL CHECK (returned_longitude BETWEEN -180.0 AND 180.0),
    pm2_5 REAL NOT NULL CHECK (pm2_5 >= 0.0),
    pm10 REAL CHECK (pm10 IS NULL OR pm10 >= 0.0),
    european_aqi REAL CHECK (european_aqi IS NULL OR european_aqi >= 0.0),
    response_hash TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    is_primary INTEGER NOT NULL CHECK (is_primary IN (0, 1)),
    within_delay_window INTEGER NOT NULL CHECK (within_delay_window IN (0, 1)),
    FOREIGN KEY (slot_id) REFERENCES collection_slots(slot_id),
    FOREIGN KEY (request_id) REFERENCES api_requests(request_id),
    FOREIGN KEY (location_id) REFERENCES locations(location_id),
    UNIQUE (slot_id, revision_number)
);

-- Guarantees at most ONE primary observation per scheduled slot
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_primary_obs_per_slot
ON observations(slot_id)
WHERE is_primary = 1;

CREATE INDEX IF NOT EXISTS idx_observations_loc_valid
ON observations(location_id, valid_utc);

-- 5. Exact T -> T+6h prediction opportunities per location
CREATE TABLE IF NOT EXISTS prediction_opportunities (
    opportunity_id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id INTEGER NOT NULL,
    source_slot_id INTEGER NOT NULL,
    target_slot_id INTEGER NOT NULL,
    source_valid_utc TEXT NOT NULL CHECK (length(source_valid_utc) = 20 AND source_valid_utc LIKE '____-__-__T__:__:__Z'),
    target_valid_utc TEXT NOT NULL CHECK (length(target_valid_utc) = 20 AND target_valid_utc LIKE '____-__-__T__:__:__Z'),
    CHECK (source_slot_id != target_slot_id),
    CHECK (datetime(target_valid_utc) = datetime(source_valid_utc, '+6 hours')),
    FOREIGN KEY (location_id) REFERENCES locations(location_id),
    FOREIGN KEY (source_slot_id) REFERENCES collection_slots(slot_id),
    FOREIGN KEY (target_slot_id) REFERENCES collection_slots(slot_id),
    UNIQUE (source_slot_id, target_slot_id),
    UNIQUE (location_id, source_valid_utc)
);

-- 6. Recorded predictions with exact inputs, model version, and settings at prediction time
CREATE TABLE IF NOT EXISTS predictions (
    prediction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id INTEGER NOT NULL,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    model_settings_json TEXT NOT NULL,
    predicted_at_utc TEXT NOT NULL CHECK (length(predicted_at_utc) = 20 AND predicted_at_utc LIKE '____-__-__T__:__:__Z'),
    input_features_json TEXT NOT NULL,
    predicted_pm2_5 REAL NOT NULL CHECK (predicted_pm2_5 >= 0.0),
    FOREIGN KEY (opportunity_id) REFERENCES prediction_opportunities(opportunity_id),
    UNIQUE (opportunity_id, model_name, model_version)
);

-- 7. Links each prediction strictly to the primary T+6h target observation
CREATE TABLE IF NOT EXISTS prediction_outcomes (
    outcome_id INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id INTEGER NOT NULL UNIQUE,
    target_observation_id INTEGER NOT NULL,
    actual_pm2_5 REAL NOT NULL CHECK (actual_pm2_5 >= 0.0),
    recorded_at_utc TEXT NOT NULL CHECK (length(recorded_at_utc) = 20 AND recorded_at_utc LIKE '____-__-__T__:__:__Z'),
    FOREIGN KEY (prediction_id) REFERENCES predictions(prediction_id),
    FOREIGN KEY (target_observation_id) REFERENCES observations(observation_id)
);

-- =====================================================================
-- TRIGGERS: Provenance, 30-Minute Rule, Exact Matching & Immutability
-- =====================================================================

-- Validate primary observation matches slot valid_utc and 30-minute delay window
CREATE TRIGGER IF NOT EXISTS trg_validate_primary_observation
BEFORE INSERT ON observations
FOR EACH ROW
WHEN NEW.is_primary = 1
BEGIN
    SELECT RAISE(ABORT, 'Primary observation must have revision_number = 1')
    WHERE NEW.revision_number != 1;

    SELECT RAISE(ABORT, 'Primary observation must be within the 30-minute delay window')
    WHERE NEW.within_delay_window != 1;

    SELECT RAISE(ABORT, 'Observation collected_at_utc exceeds slot acceptance_deadline_utc')
    FROM collection_slots cs
    WHERE cs.slot_id = NEW.slot_id
      AND NEW.collected_at_utc > cs.acceptance_deadline_utc;

    SELECT RAISE(ABORT, 'Observation valid_utc does not match slot required_valid_utc')
    FROM collection_slots cs
    WHERE cs.slot_id = NEW.slot_id
      AND NEW.valid_utc != cs.required_valid_utc;

    SELECT RAISE(ABORT, 'Observation location_id does not match slot location_id')
    FROM collection_slots cs
    WHERE cs.slot_id = NEW.slot_id
      AND NEW.location_id != cs.location_id;
END;

-- Prevent modifying or deleting primary observations
CREATE TRIGGER IF NOT EXISTS trg_protect_primary_obs_update
BEFORE UPDATE ON observations
FOR EACH ROW
WHEN OLD.is_primary = 1
BEGIN
    SELECT RAISE(ABORT, 'Primary observations are immutable and cannot be updated');
END;

CREATE TRIGGER IF NOT EXISTS trg_protect_primary_obs_delete
BEFORE DELETE ON observations
FOR EACH ROW
WHEN OLD.is_primary = 1
BEGIN
    SELECT RAISE(ABORT, 'Primary observations are immutable and cannot be deleted');
END;

-- Validate prediction_outcomes only link to the primary observation of the exact target_slot_id
CREATE TRIGGER IF NOT EXISTS trg_validate_prediction_outcome
BEFORE INSERT ON prediction_outcomes
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'Outcome must reference a primary observation (is_primary = 1)')
    FROM observations o
    WHERE o.observation_id = NEW.target_observation_id
      AND o.is_primary != 1;

    SELECT RAISE(ABORT, 'Outcome observation does not belong to the exact T+6h target_slot_id')
    FROM predictions p
    JOIN prediction_opportunities po ON p.opportunity_id = po.opportunity_id
    JOIN observations o ON o.observation_id = NEW.target_observation_id
    WHERE p.prediction_id = NEW.prediction_id
      AND o.slot_id != po.target_slot_id;
END;

-- Prevent modifying or deleting recorded predictions or outcomes
CREATE TRIGGER IF NOT EXISTS trg_protect_predictions_update
BEFORE UPDATE ON predictions
BEGIN
    SELECT RAISE(ABORT, 'Recorded predictions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_protect_predictions_delete
BEFORE DELETE ON predictions
BEGIN
    SELECT RAISE(ABORT, 'Recorded predictions cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS trg_protect_outcomes_update
BEFORE UPDATE ON prediction_outcomes
BEGIN
    SELECT RAISE(ABORT, 'Primary prediction outcomes are immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_protect_outcomes_delete
BEFORE DELETE ON prediction_outcomes
BEGIN
    SELECT RAISE(ABORT, 'Primary prediction outcomes cannot be deleted');
END;