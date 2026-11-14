from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from src.collector import collect_slot
from src.database import (
    format_utc,
    generate_collection_schedule,
    get_connection,
    initialize_database,
    seed_locations,
)


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | str, url: str = "https://example.test/api"):
        self.status_code = status_code
        self.url = url
        self.text = payload if isinstance(payload, str) else json.dumps(payload)


class FakeSession:
    def __init__(self, responses: list[FakeResponse]):
        self._responses = list(responses)

    def get(self, url, params=None, timeout=15):
        return self._responses.pop(0)


@pytest.fixture
def future_slot_db(tmp_path: Path):
    """Create a test DB with an open slot whose acceptance deadline is in the future."""
    db_file = initialize_database(tmp_path / "collector_test.db")
    conn = get_connection(db_file)
    seed_locations(conn)
    now = datetime.now(timezone.utc)
    generate_collection_schedule(conn, start_date_utc=now, days=2, delay_minutes=30)

    future_deadline = format_utc(now + timedelta(hours=12))
    slot = conn.execute(
        "SELECT * FROM collection_slots WHERE acceptance_deadline_utc >= ? ORDER BY slot_id LIMIT 1",
        (future_deadline,),
    ).fetchone()
    yield conn, slot, tmp_path / "raw"
    conn.close()


def _make_payload(valid_utc: str, pm2_5: float) -> dict:
    return {
        "latitude": 51.5,
        "longitude": -0.1,
        "hourly": {
            "time": [valid_utc[:16]],
            "pm2_5": [pm2_5],
            "pm10": [20.0],
            "european_aqi": [35],
        },
    }


def test_collect_slot_saves_primary_and_skips_duplicate(future_slot_db):
    conn, slot, raw_dir = future_slot_db
    payload = _make_payload(slot["required_valid_utc"], 12.5)
    session = FakeSession([FakeResponse(200, payload), FakeResponse(200, payload)])

    first = collect_slot(conn, slot["slot_id"], archive_dir=raw_dir, session=session)
    second = collect_slot(conn, slot["slot_id"], archive_dir=raw_dir, session=session)

    assert first["status"] == "collected_primary"
    assert second["status"] == "duplicate_skipped"
    count = conn.execute("SELECT COUNT(*) FROM observations WHERE slot_id = ?", (slot["slot_id"],)).fetchone()[0]
    assert count == 1


def test_collect_slot_preserves_primary_when_revision_changes(future_slot_db):
    conn, slot, raw_dir = future_slot_db
    p1 = _make_payload(slot["required_valid_utc"], 12.5)
    p2 = _make_payload(slot["required_valid_utc"], 16.4)
    session = FakeSession([FakeResponse(200, p1), FakeResponse(200, p2)])

    first = collect_slot(conn, slot["slot_id"], archive_dir=raw_dir, session=session)
    second = collect_slot(conn, slot["slot_id"], archive_dir=raw_dir, session=session)

    assert first["status"] == "collected_primary"
    assert second["status"] == "recorded_revision"
    assert second["revision_number"] == 2

    primary = conn.execute(
        "SELECT pm2_5 FROM observations WHERE slot_id = ? AND is_primary = 1", (slot["slot_id"],)
    ).fetchone()
    assert primary["pm2_5"] == 12.5


def test_collect_slot_logs_http_error(future_slot_db):
    conn, slot, raw_dir = future_slot_db
    session = FakeSession([FakeResponse(500, "Internal Server Error")])

    result = collect_slot(conn, slot["slot_id"], archive_dir=raw_dir, session=session)
    assert result["status"] == "http_error"

    req = conn.execute("SELECT request_status, http_status FROM api_requests WHERE slot_id = ?", (slot["slot_id"],)).fetchone()
    assert req["request_status"] == "http_error"
    assert req["http_status"] == 500