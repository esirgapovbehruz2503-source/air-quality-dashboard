from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from src.collector import collect_due_slots
from src.database import (
    format_utc,
    generate_collection_schedule,
    get_connection,
    initialize_database,
    seed_locations,
)
from src.queries import (
    get_collection_monitoring_summary,
    get_consecutive_pm25_changes,
    get_daily_averages,
    get_highest_pm25_estimates,
    get_latest_estimates,
    get_missing_or_incomplete_intervals,
)


class _FakeResp:
    def __init__(self, payload: dict):
        self.status_code = 200
        self.url = "https://example.test/api"
        self.text = json.dumps(payload)


class _MultiCitySession:
    def __init__(self, valid_utc: str, pm2_5: float = 14.0):
        self.valid_utc = valid_utc
        self.pm2_5 = pm2_5

    def get(self, url, params=None, timeout=15):
        return _FakeResp(
            {
                "latitude": params["latitude"],
                "longitude": params["longitude"],
                "hourly": {
                    "time": [self.valid_utc[:16]],
                    "pm2_5": [self.pm2_5],
                    "pm10": [20.0],
                    "european_aqi": [30],
                },
            }
        )


def _setup_db(tmp_path: Path):
    db_file = initialize_database(tmp_path / "queries_test.db")
    conn = get_connection(db_file)
    seed_locations(conn)
    start = datetime(2026, 11, 14, 0, 0, tzinfo=timezone.utc)
    generate_collection_schedule(conn, start_date_utc=start, days=2, delay_minutes=30)
    return conn


def _insert_obs(conn, slot_row, pm2_5: float, is_primary: int = 1, rev: int = 1):
    req_id = conn.execute(
        """
        INSERT INTO api_requests (
            slot_id, request_url, requested_at_utc, responded_at_utc, http_status, response_hash, request_status
        ) VALUES (?, 'https://example.test', ?, ?, 200, 'h', 'success')
        """,
        (slot_row["slot_id"], slot_row["scheduled_collection_utc"], slot_row["scheduled_collection_utc"]),
    ).lastrowid

    collected_ts = (
        slot_row["scheduled_collection_utc"]
        if is_primary == 1
        else "2026-11-15T12:05:00Z"
    )
    conn.execute(
        """
        INSERT INTO observations (
            slot_id, request_id, location_id, valid_utc, collected_at_utc,
            returned_latitude, returned_longitude, pm2_5, pm10, european_aqi,
            response_hash, revision_number, is_primary, within_delay_window
        ) VALUES (?, ?, ?, ?, ?, 51.5, -0.1, ?, 20.0, 30.0, 'h', ?, ?, ?)
        """,
        (
            slot_row["slot_id"],
            req_id,
            slot_row["location_id"],
            slot_row["required_valid_utc"],
            collected_ts,
            pm2_5,
            rev,
            is_primary,
            1 if is_primary == 1 else 0,
        ),
    )
    if is_primary == 1:
        conn.execute("UPDATE collection_slots SET slot_status = 'collected' WHERE slot_id = ?", (slot_row["slot_id"],))


def test_latest_and_highest_queries_ignore_revisions(tmp_path: Path):
    conn = _setup_db(tmp_path)
    slot = conn.execute(
        "SELECT * FROM collection_slots WHERE location_id = 1 ORDER BY scheduled_collection_utc LIMIT 1"
    ).fetchone()
    _insert_obs(conn, slot, pm2_5=15.0, is_primary=1, rev=1)
    _insert_obs(conn, slot, pm2_5=99.0, is_primary=0, rev=2)
    conn.commit()

    latest = get_latest_estimates(conn)
    highest = get_highest_pm25_estimates(conn, limit=5)
    daily = get_daily_averages(conn)

    assert len(latest) == 1
    assert latest[0]["pm2_5"] == 15.0
    assert highest[0]["pm2_5"] == 15.0
    assert daily[0]["avg_pm2_5"] == 15.0
    conn.close()


def test_consecutive_changes_skips_missing_gap(tmp_path: Path):
    conn = _setup_db(tmp_path)
    slots = conn.execute(
        "SELECT * FROM collection_slots WHERE location_id = 1 ORDER BY scheduled_collection_utc LIMIT 4"
    ).fetchall()

    # Collect 00:00, skip 06:00, collect 12:00 and 18:00
    _insert_obs(conn, slots[0], pm2_5=10.0)
    conn.execute("UPDATE collection_slots SET slot_status = 'missed' WHERE slot_id = ?", (slots[1]["slot_id"],))
    _insert_obs(conn, slots[2], pm2_5=16.0)
    _insert_obs(conn, slots[3], pm2_5=20.0)
    conn.commit()

    changes = get_consecutive_pm25_changes(conn)
    missing = get_missing_or_incomplete_intervals(conn)

    assert len(changes) == 1
    assert changes[0]["pm2_5_change_6h"] == 4.0
    assert len(missing) == 1
    assert missing[0]["slot_status"] == "missed"
    conn.close()


def test_collect_due_slots_collects_all_five_cities_and_marks_expired_missed(tmp_path: Path):
    conn = _setup_db(tmp_path)
    # 06:10 UTC: 00:00 slots (5) are expired -> missed; 06:00 slots (5) are due -> collected
    now_dt = datetime(2026, 11, 14, 6, 10, tzinfo=timezone.utc)
    session = _MultiCitySession(valid_utc="2026-11-14T06:00:00Z", pm2_5=13.5)

    outcome = collect_due_slots(conn, archive_dir=tmp_path / "raw", now_utc=now_dt, session=session)
    summary = get_collection_monitoring_summary(conn)

    assert outcome["missed_marked"] == 5
    assert len(outcome["due_collected"]) == 5
    assert summary["collected_slots"] == 5
    assert summary["missed_slots"] == 5
    assert summary["primary_observations"] == 5
    conn.close()