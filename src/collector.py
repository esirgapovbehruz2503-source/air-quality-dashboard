from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

import requests

from src.database import (
    DEV_DB_PATH,
    format_utc,
    generate_collection_schedule,
    get_connection,
    initialize_database,
    seed_locations,
)
from src.validation import ValidationError, parse_open_meteo_payload

OPEN_METEO_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
DEFAULT_DEV_ARCHIVE_DIR = DEV_DB_PATH.parent / "raw_responses"


def compute_response_hash(raw_text: str) -> str:
    """Return SHA-256 hex digest of the raw API response body."""
    return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()


def build_request_params(latitude: float, longitude: float) -> dict[str, Any]:
    """Build deterministic Open-Meteo Air Quality query parameters."""
    return {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": "pm2_5,pm10,european_aqi",
        "domains": "cams_global",
        "timezone": "UTC",
        "past_days": 1,
        "forecast_days": 2,
    }


def collect_slot(
    conn: sqlite3.Connection,
    slot_id: int,
    archive_dir: Path | None = None,
    timeout_seconds: int = 15,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """
    Fetch, validate, and persist an observation for a scheduled collection_slot.
    Preserves primary observations and records later changes as non-primary revisions.
    """
    slot = conn.execute(
        """
        SELECT cs.*, l.city_name, l.requested_latitude, l.requested_longitude
        FROM collection_slots cs
        JOIN locations l ON cs.location_id = l.location_id
        WHERE cs.slot_id = ?
        """,
        (slot_id,),
    ).fetchone()
    if slot is None:
        raise ValueError(f"Unknown slot_id: {slot_id}")

    http_client = session or requests
    params = build_request_params(slot["requested_latitude"], slot["requested_longitude"])
    requested_at_utc = format_utc(datetime.now(timezone.utc))

    try:
        response = http_client.get(OPEN_METEO_URL, params=params, timeout=timeout_seconds)
    except requests.RequestException as exc:
        conn.execute(
            """
            INSERT INTO api_requests (
                slot_id, request_url, requested_at_utc, request_status, error_message
            ) VALUES (?, ?, ?, 'network_error', ?)
            """,
            (slot_id, OPEN_METEO_URL, requested_at_utc, str(exc)),
        )
        conn.commit()
        return {"slot_id": slot_id, "status": "network_error", "error": str(exc)}

    responded_at_utc = format_utc(datetime.now(timezone.utc))
    raw_text = response.text
    resp_hash = compute_response_hash(raw_text)

    if response.status_code != 200:
        conn.execute(
            """
            INSERT INTO api_requests (
                slot_id, request_url, requested_at_utc, responded_at_utc,
                http_status, response_hash, request_status, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, 'http_error', ?)
            """,
            (
                slot_id,
                response.url,
                requested_at_utc,
                responded_at_utc,
                response.status_code,
                resp_hash,
                raw_text[:300],
            ),
        )
        conn.commit()
        return {"slot_id": slot_id, "status": "http_error", "http_status": response.status_code}

    archive_path_str = None
    if archive_dir is not None:
        archive_dir.mkdir(parents=True, exist_ok=True)
        ts_safe = responded_at_utc.replace(":", "-")
        archive_file = archive_dir / f"slot_{slot_id}_{slot['city_name']}_{ts_safe}.json"
        archive_file.write_text(raw_text, encoding="utf-8")
        archive_path_str = str(archive_file)

    try:
        payload = json.loads(raw_text)
        parsed = parse_open_meteo_payload(payload, slot["required_valid_utc"])
    except (json.JSONDecodeError, ValidationError) as exc:
        conn.execute(
            """
            INSERT INTO api_requests (
                slot_id, request_url, requested_at_utc, responded_at_utc,
                http_status, response_hash, raw_response_path, request_status, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'validation_error', ?)
            """,
            (
                slot_id,
                response.url,
                requested_at_utc,
                responded_at_utc,
                response.status_code,
                resp_hash,
                archive_path_str,
                str(exc),
            ),
        )
        conn.commit()
        return {"slot_id": slot_id, "status": "validation_error", "error": str(exc)}

    within_delay = 1 if responded_at_utc <= slot["acceptance_deadline_utc"] else 0
    existing_primary = conn.execute(
        "SELECT observation_id FROM observations WHERE slot_id = ? AND is_primary = 1",
        (slot_id,),
    ).fetchone()

    if existing_primary is None and within_delay == 0:
        conn.execute(
            """
            INSERT INTO api_requests (
                slot_id, request_url, requested_at_utc, responded_at_utc,
                http_status, response_hash, raw_response_path, request_status, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'late_rejected', 'Response received after 30-minute deadline')
            """,
            (
                slot_id,
                response.url,
                requested_at_utc,
                responded_at_utc,
                response.status_code,
                resp_hash,
                archive_path_str,
            ),
        )
        conn.execute(
            "UPDATE collection_slots SET slot_status = 'missed' WHERE slot_id = ? AND slot_status = 'pending'",
            (slot_id,),
        )
        conn.commit()
        return {"slot_id": slot_id, "status": "late_rejected"}

    req_cursor = conn.execute(
        """
        INSERT INTO api_requests (
            slot_id, request_url, requested_at_utc, responded_at_utc,
            http_status, response_hash, raw_response_path, request_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'success')
        """,
        (
            slot_id,
            response.url,
            requested_at_utc,
            responded_at_utc,
            response.status_code,
            resp_hash,
            archive_path_str,
        ),
    )
    request_id = req_cursor.lastrowid

    latest_obs = conn.execute(
        """
        SELECT revision_number, pm2_5, pm10, european_aqi
        FROM observations
        WHERE slot_id = ?
        ORDER BY revision_number DESC
        LIMIT 1
        """,
        (slot_id,),
    ).fetchone()

    if latest_obs is not None:
        values_unchanged = (
            latest_obs["pm2_5"] == parsed["pm2_5"]
            and latest_obs["pm10"] == parsed["pm10"]
            and latest_obs["european_aqi"] == parsed["european_aqi"]
        )
        if values_unchanged:
            conn.commit()
            return {"slot_id": slot_id, "status": "duplicate_skipped"}

    if existing_primary is None:
        revision_number = 1
        is_primary = 1
        action_status = "collected_primary"
    else:
        revision_number = (latest_obs["revision_number"] if latest_obs else 1) + 1
        is_primary = 0
        action_status = "recorded_revision"

    conn.execute(
        """
        INSERT INTO observations (
            slot_id, request_id, location_id, valid_utc, collected_at_utc,
            returned_latitude, returned_longitude, pm2_5, pm10, european_aqi,
            response_hash, revision_number, is_primary, within_delay_window
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            slot_id,
            request_id,
            slot["location_id"],
            parsed["valid_utc"],
            responded_at_utc,
            parsed["returned_latitude"],
            parsed["returned_longitude"],
            parsed["pm2_5"],
            parsed["pm10"],
            parsed["european_aqi"],
            resp_hash,
            revision_number,
            is_primary,
            within_delay,
        ),
    )

    if is_primary == 1:
        conn.execute("UPDATE collection_slots SET slot_status = 'collected' WHERE slot_id = ?", (slot_id,))

    conn.commit()
    return {
        "slot_id": slot_id,
        "city": slot["city_name"],
        "valid_utc": parsed["valid_utc"],
        "pm2_5": parsed["pm2_5"],
        "status": action_status,
        "revision_number": revision_number,
    }


def run_mvp_demo(db_path: Path = DEV_DB_PATH, archive_dir: Path = DEFAULT_DEV_ARCHIVE_DIR) -> None:
    """Collect and retrieve one genuine live record for the next open 6-hour UTC slot."""
    initialize_database(db_path)
    now = datetime.now(timezone.utc)
    now_str = format_utc(now)

    with get_connection(db_path) as conn:
        seed_locations(conn)
        generate_collection_schedule(conn, start_date_utc=now, days=21, delay_minutes=30)

        slot = conn.execute(
            """
            SELECT cs.slot_id, l.city_name, cs.required_valid_utc
            FROM collection_slots cs
            JOIN locations l ON cs.location_id = l.location_id
            WHERE cs.acceptance_deadline_utc >= ?
            ORDER BY cs.scheduled_collection_utc ASC, l.location_id ASC
            LIMIT 1
            """,
            (now_str,),
        ).fetchone()

        if slot is None:
            print("No open scheduled slot found.")
            return

        result = collect_slot(conn, slot["slot_id"], archive_dir=archive_dir)
        print("Collection result:", result)

        retrieved = conn.execute(
            """
            SELECT l.city_name, o.valid_utc, o.collected_at_utc, o.pm2_5, o.pm10, o.european_aqi,
                   o.returned_latitude, o.returned_longitude, o.is_primary, o.revision_number
            FROM observations o
            JOIN locations l ON o.location_id = l.location_id
            WHERE o.slot_id = ?
            ORDER BY o.revision_number
            """,
            (slot["slot_id"],),
        ).fetchall()

        for row in retrieved:
            print("Retrieved record:", dict(row))

if __name__ == "__main__":
    run_mvp_demo()