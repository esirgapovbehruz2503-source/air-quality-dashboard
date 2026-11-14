from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest

from src.database import (
    generate_collection_schedule,
    get_connection,
    initialize_database,
    seed_locations,
)


@pytest.fixture
def db_conn(tmp_path: Path):
    """Create a fresh temporary SQLite database for each test."""
    db_file = initialize_database(tmp_path / "test_air_quality.db")
    conn = get_connection(db_file)
    seed_locations(conn)
    generate_collection_schedule(
        conn,
        start_date_utc=datetime(2026, 11, 14, 0, 0, tzinfo=timezone.utc),
        days=21,
        delay_minutes=30,
    )
    yield conn
    conn.close()


def _insert_success_request(conn: sqlite3.Connection, slot_id: int, requested_at: str, responded_at: str) -> int:
    cursor = conn.execute(
        """
        INSERT INTO api_requests (
            slot_id, request_url, requested_at_utc, responded_at_utc, http_status, response_hash, request_status
        ) VALUES (?, 'https://air-quality-api.open-meteo.com/v1/air-quality', ?, ?, 200, 'abc123hash', 'success')
        """,
        (slot_id, requested_at, responded_at),
    )
    return cursor.lastrowid


def test_schema_seeds_five_locations_and_420_slots(db_conn: sqlite3.Connection):
    loc_count = db_conn.execute("SELECT COUNT(*) FROM locations").fetchone()[0]
    slot_count = db_conn.execute("SELECT COUNT(*) FROM collection_slots").fetchone()[0]
    opp_count = db_conn.execute("SELECT COUNT(*) FROM prediction_opportunities").fetchone()[0]

    assert loc_count == 5
    assert slot_count == 420
    assert opp_count == 415


def test_rejects_negative_pm25_and_invalid_utc_format(db_conn: sqlite3.Connection):
    slot = db_conn.execute("SELECT * FROM collection_slots LIMIT 1").fetchone()
    req_id = _insert_success_request(
        db_conn, slot["slot_id"], slot["scheduled_collection_utc"], slot["scheduled_collection_utc"]
    )

    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            """
            INSERT INTO observations (
                slot_id, request_id, location_id, valid_utc, collected_at_utc,
                returned_latitude, returned_longitude, pm2_5, pm10, european_aqi,
                response_hash, revision_number, is_primary, within_delay_window
            ) VALUES (?, ?, ?, ?, ?, 51.5, -0.1, -1.5, 10.0, 20.0, 'h1', 1, 1, 1)
            """,
            (slot["slot_id"], req_id, slot["location_id"], slot["required_valid_utc"], slot["scheduled_collection_utc"]),
        )

    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            """
            INSERT INTO observations (
                slot_id, request_id, location_id, valid_utc, collected_at_utc,
                returned_latitude, returned_longitude, pm2_5, pm10, european_aqi,
                response_hash, revision_number, is_primary, within_delay_window
            ) VALUES (?, ?, ?, '2026-11-14 00:00:00', ?, 51.5, -0.1, 12.0, 10.0, 20.0, 'h1', 1, 1, 1)
            """,
            (slot["slot_id"], req_id, slot["location_id"], slot["scheduled_collection_utc"]),
        )


def test_preserves_primary_observation_and_allows_non_primary_revisions(db_conn: sqlite3.Connection):
    slot = db_conn.execute("SELECT * FROM collection_slots LIMIT 1").fetchone()
    req_id = _insert_success_request(
        db_conn, slot["slot_id"], slot["scheduled_collection_utc"], slot["scheduled_collection_utc"]
    )

    db_conn.execute(
        """
        INSERT INTO observations (
            slot_id, request_id, location_id, valid_utc, collected_at_utc,
            returned_latitude, returned_longitude, pm2_5, pm10, european_aqi,
            response_hash, revision_number, is_primary, within_delay_window
        ) VALUES (?, ?, ?, ?, ?, 51.5, -0.1, 14.2, 22.0, 25.0, 'hash_v1', 1, 1, 1)
        """,
        (slot["slot_id"], req_id, slot["location_id"], slot["required_valid_utc"], slot["scheduled_collection_utc"]),
    )

    # Second primary observation for the same slot must fail
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            """
            INSERT INTO observations (
                slot_id, request_id, location_id, valid_utc, collected_at_utc,
                returned_latitude, returned_longitude, pm2_5, pm10, european_aqi,
                response_hash, revision_number, is_primary, within_delay_window
            ) VALUES (?, ?, ?, ?, ?, 51.5, -0.1, 15.0, 22.0, 25.0, 'hash_v2', 1, 1, 1)
            """,
            (slot["slot_id"], req_id, slot["location_id"], slot["required_valid_utc"], slot["scheduled_collection_utc"]),
        )

    # Updating or deleting the primary observation must fail
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute("UPDATE observations SET pm2_5 = 99.9 WHERE slot_id = ? AND is_primary = 1", (slot["slot_id"],))

    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute("DELETE FROM observations WHERE slot_id = ? AND is_primary = 1", (slot["slot_id"],))

    # Storing a later revision (is_primary = 0, revision_number = 2) must succeed
    db_conn.execute(
        """
        INSERT INTO observations (
            slot_id, request_id, location_id, valid_utc, collected_at_utc,
            returned_latitude, returned_longitude, pm2_5, pm10, european_aqi,
            response_hash, revision_number, is_primary, within_delay_window
        ) VALUES (?, ?, ?, ?, ?, 51.5, -0.1, 16.8, 24.0, 28.0, 'hash_v2', 2, 0, 0)
        """,
        (slot["slot_id"], req_id, slot["location_id"], slot["required_valid_utc"], "2026-11-14T06:05:00Z"),
    )

    primary = db_conn.execute(
        "SELECT pm2_5 FROM observations WHERE slot_id = ? AND is_primary = 1", (slot["slot_id"],)
    ).fetchone()
    assert primary["pm2_5"] == 14.2


def test_enforces_30_minute_delay_allowance(db_conn: sqlite3.Connection):
    slot = db_conn.execute(
        "SELECT * FROM collection_slots WHERE scheduled_collection_utc = '2026-11-14T00:00:00Z' LIMIT 1"
    ).fetchone()
    late_time = "2026-11-14T00:31:00Z"
    req_id = _insert_success_request(db_conn, slot["slot_id"], late_time, late_time)

    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            """
            INSERT INTO observations (
                slot_id, request_id, location_id, valid_utc, collected_at_utc,
                returned_latitude, returned_longitude, pm2_5, pm10, european_aqi,
                response_hash, revision_number, is_primary, within_delay_window
            ) VALUES (?, ?, ?, ?, ?, 51.5, -0.1, 11.0, 18.0, 20.0, 'hash_late', 1, 1, 1)
            """,
            (slot["slot_id"], req_id, slot["location_id"], slot["required_valid_utc"], late_time),
        )


def test_prediction_outcome_requires_primary_target_observation(db_conn: sqlite3.Connection):
    opp = db_conn.execute("SELECT * FROM prediction_opportunities LIMIT 1").fetchone()
    target_slot = db_conn.execute(
        "SELECT * FROM collection_slots WHERE slot_id = ?", (opp["target_slot_id"],)
    ).fetchone()
    wrong_slot = db_conn.execute(
        "SELECT * FROM collection_slots WHERE location_id = ? AND slot_id != ? AND slot_id != ? LIMIT 1",
        (opp["location_id"], opp["source_slot_id"], opp["target_slot_id"]),
    ).fetchone()

    req_target = _insert_success_request(
        db_conn, target_slot["slot_id"], target_slot["scheduled_collection_utc"], target_slot["scheduled_collection_utc"]
    )
    req_wrong = _insert_success_request(
        db_conn, wrong_slot["slot_id"], wrong_slot["scheduled_collection_utc"], wrong_slot["scheduled_collection_utc"]
    )

    # Primary and revised observations for target slot
    cur_primary = db_conn.execute(
        """
        INSERT INTO observations (
            slot_id, request_id, location_id, valid_utc, collected_at_utc,
            returned_latitude, returned_longitude, pm2_5, pm10, european_aqi,
            response_hash, revision_number, is_primary, within_delay_window
        ) VALUES (?, ?, ?, ?, ?, 51.5, -0.1, 18.5, 25.0, 30.0, 'h_t1', 1, 1, 1)
        """,
        (target_slot["slot_id"], req_target, target_slot["location_id"], target_slot["required_valid_utc"], target_slot["scheduled_collection_utc"]),
    )
    primary_obs_id = cur_primary.lastrowid

    cur_rev = db_conn.execute(
        """
        INSERT INTO observations (
            slot_id, request_id, location_id, valid_utc, collected_at_utc,
            returned_latitude, returned_longitude, pm2_5, pm10, european_aqi,
            response_hash, revision_number, is_primary, within_delay_window
        ) VALUES (?, ?, ?, ?, ?, 51.5, -0.1, 21.0, 27.0, 32.0, 'h_t2', 2, 0, 0)
        """,
        (target_slot["slot_id"], req_target, target_slot["location_id"], target_slot["required_valid_utc"], "2026-11-14T12:05:00Z"),
    )
    revised_obs_id = cur_rev.lastrowid

    cur_wrong = db_conn.execute(
        """
        INSERT INTO observations (
            slot_id, request_id, location_id, valid_utc, collected_at_utc,
            returned_latitude, returned_longitude, pm2_5, pm10, european_aqi,
            response_hash, revision_number, is_primary, within_delay_window
        ) VALUES (?, ?, ?, ?, ?, 51.5, -0.1, 9.0, 12.0, 15.0, 'h_w1', 1, 1, 1)
        """,
        (wrong_slot["slot_id"], req_wrong, wrong_slot["location_id"], wrong_slot["required_valid_utc"], wrong_slot["scheduled_collection_utc"]),
    )
    wrong_obs_id = cur_wrong.lastrowid

    cur_pred = db_conn.execute(
        """
        INSERT INTO predictions (
            opportunity_id, model_name, model_version, model_settings_json,
            predicted_at_utc, input_features_json, predicted_pm2_5
        ) VALUES (?, 'persistence', 'v1', '{}', ?, '{"pm2_5_t": 17.0}', 17.0)
        """,
        (opp["opportunity_id"], opp["source_valid_utc"]),
    )
    pred_id = cur_pred.lastrowid

    # Linking to a non-primary revision must fail
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO prediction_outcomes (prediction_id, target_observation_id, actual_pm2_5, recorded_at_utc) VALUES (?, ?, 21.0, ?)",
            (pred_id, revised_obs_id, target_slot["scheduled_collection_utc"]),
        )

    # Linking to a wrong slot's primary observation must fail
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO prediction_outcomes (prediction_id, target_observation_id, actual_pm2_5, recorded_at_utc) VALUES (?, ?, 9.0, ?)",
            (pred_id, wrong_obs_id, wrong_slot["scheduled_collection_utc"]),
        )

    # Linking to the exact T+6h primary observation must succeed
    db_conn.execute(
        "INSERT INTO prediction_outcomes (prediction_id, target_observation_id, actual_pm2_5, recorded_at_utc) VALUES (?, ?, 18.5, ?)",
        (pred_id, primary_obs_id, target_slot["scheduled_collection_utc"]),
    )
    saved = db_conn.execute("SELECT actual_pm2_5 FROM prediction_outcomes WHERE prediction_id = ?", (pred_id,)).fetchone()
    assert saved["actual_pm2_5"] == 18.5