import argparse
from pathlib import Path
import sqlite3
from typing import Any

from src.database import DEV_DB_PATH, get_connection


def get_latest_estimates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Query 1: Latest primary PM2.5, PM10, and European AQI estimate per location."""
    rows = conn.execute(
        """
        SELECT
            l.city_name,
            o.valid_utc,
            o.collected_at_utc,
            o.pm2_5,
            o.pm10,
            o.european_aqi,
            o.returned_latitude,
            o.returned_longitude
        FROM observations o
        JOIN locations l ON o.location_id = l.location_id
        WHERE o.is_primary = 1
          AND o.valid_utc = (
              SELECT MAX(o2.valid_utc)
              FROM observations o2
              WHERE o2.location_id = o.location_id
                AND o2.is_primary = 1
          )
        ORDER BY l.city_name
        """
    ).fetchall()
    return [dict(r) for r in rows]


def get_daily_averages(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Query 2: Daily average PM2.5 by location and UTC date, with slot counts."""
    rows = conn.execute(
        """
        SELECT
            l.city_name,
            substr(o.valid_utc, 1, 10) AS utc_date,
            COUNT(*) AS slots_collected,
            ROUND(AVG(o.pm2_5), 3) AS avg_pm2_5,
            ROUND(MIN(o.pm2_5), 3) AS min_pm2_5,
            ROUND(MAX(o.pm2_5), 3) AS max_pm2_5
        FROM observations o
        JOIN locations l ON o.location_id = l.location_id
        WHERE o.is_primary = 1
        GROUP BY l.city_name, substr(o.valid_utc, 1, 10)
        ORDER BY utc_date ASC, l.city_name ASC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def get_highest_pm25_estimates(conn: sqlite3.Connection, limit: int = 10) -> list[dict[str, Any]]:
    """Query 3: Highest primary PM2.5 estimates across the collected period."""
    rows = conn.execute(
        """
        SELECT
            l.city_name,
            o.valid_utc,
            o.pm2_5,
            o.pm10,
            o.european_aqi
        FROM observations o
        JOIN locations l ON o.location_id = l.location_id
        WHERE o.is_primary = 1
        ORDER BY o.pm2_5 DESC, o.valid_utc ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_missing_or_incomplete_intervals(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Query 4: Scheduled slots marked missed or past-due without a primary observation."""
    rows = conn.execute(
        """
        SELECT
            l.city_name,
            cs.scheduled_collection_utc,
            cs.required_valid_utc,
            cs.acceptance_deadline_utc,
            cs.slot_status
        FROM collection_slots cs
        JOIN locations l ON cs.location_id = l.location_id
        LEFT JOIN observations o
          ON o.slot_id = cs.slot_id AND o.is_primary = 1
        WHERE cs.slot_status = 'missed'
           OR (o.observation_id IS NULL AND cs.slot_status != 'pending')
        ORDER BY cs.scheduled_collection_utc ASC, l.city_name ASC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def get_consecutive_pm25_changes(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """
    Query 5: PM2.5 changes between consecutive 6-hour valid-time primary observations.
    Only computes differences when the previous observation is exactly 6 hours earlier.
    """
    rows = conn.execute(
        """
        WITH ordered AS (
            SELECT
                l.city_name,
                o.valid_utc,
                o.pm2_5,
                LAG(o.valid_utc) OVER (PARTITION BY o.location_id ORDER BY o.valid_utc) AS prev_valid_utc,
                LAG(o.pm2_5) OVER (PARTITION BY o.location_id ORDER BY o.valid_utc) AS prev_pm2_5
            FROM observations o
            JOIN locations l ON o.location_id = l.location_id
            WHERE o.is_primary = 1
        )
        SELECT
            city_name,
            prev_valid_utc,
            valid_utc AS curr_valid_utc,
            prev_pm2_5,
            pm2_5 AS curr_pm2_5,
            ROUND(pm2_5 - prev_pm2_5, 3) AS pm2_5_change_6h
        FROM ordered
        WHERE prev_valid_utc IS NOT NULL
          AND datetime(prev_valid_utc) = datetime(valid_utc, '-6 hours')
        ORDER BY valid_utc ASC, city_name ASC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def get_collection_monitoring_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Summarize collection progress, revisions, and API request health."""
    slot_counts = conn.execute(
        """
        SELECT
            COUNT(*) AS total_slots,
            SUM(CASE WHEN slot_status = 'collected' THEN 1 ELSE 0 END) AS collected_slots,
            SUM(CASE WHEN slot_status = 'missed' THEN 1 ELSE 0 END) AS missed_slots,
            SUM(CASE WHEN slot_status = 'pending' THEN 1 ELSE 0 END) AS pending_slots
        FROM collection_slots
        """
    ).fetchone()

    obs_counts = conn.execute(
        """
        SELECT
            SUM(CASE WHEN is_primary = 1 THEN 1 ELSE 0 END) AS primary_observations,
            SUM(CASE WHEN is_primary = 0 THEN 1 ELSE 0 END) AS revision_observations
        FROM observations
        """
    ).fetchone()

    req_errors = conn.execute(
        """
        SELECT COUNT(*) AS failed_requests
        FROM api_requests
        WHERE request_status != 'success'
        """
    ).fetchone()

    return {
        "total_slots": slot_counts["total_slots"] or 0,
        "collected_slots": slot_counts["collected_slots"] or 0,
        "missed_slots": slot_counts["missed_slots"] or 0,
        "pending_slots": slot_counts["pending_slots"] or 0,
        "primary_observations": obs_counts["primary_observations"] or 0,
        "revision_observations": obs_counts["revision_observations"] or 0,
        "failed_requests": req_errors["failed_requests"] or 0,
    }


def print_monitoring_report(db_path: Path) -> None:
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return
    with get_connection(db_path) as conn:
        summary = get_collection_monitoring_summary(conn)
        print("=== Collection Monitoring Summary ===")
        for k, v in summary.items():
            print(f"  {k}: {v}")

        print("\n=== Latest Primary Estimates ===")
        for row in get_latest_estimates(conn):
            print(" ", row)

        print("\n=== Top PM2.5 Estimates ===")
        for row in get_highest_pm25_estimates(conn, limit=5):
            print(" ", row)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run stakeholder SQL queries and monitoring report.")
    parser.add_argument("--db", type=Path, default=DEV_DB_PATH, help="Path to SQLite database.")
    args = parser.parse_args()
    print_monitoring_report(args.db)