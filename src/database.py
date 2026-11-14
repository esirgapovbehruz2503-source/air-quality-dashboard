from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "database" / "schema.sql"
DEV_DB_PATH = PROJECT_ROOT / "data" / "development" / "air_quality_dev.db"
ASSESSED_DB_PATH = PROJECT_ROOT / "data" / "assessed" / "air_quality_assessed.db"

APPROVED_LOCATIONS = [
    ("London", 51.5074, -0.1278),
    ("Paris", 48.8566, 2.3522),
    ("Berlin", 52.5200, 13.4050),
    ("Madrid", 40.4168, -3.7038),
    ("Rome", 41.9028, 12.4964),
]

COLLECTION_HOURS_UTC = (0, 6, 12, 18)


def format_utc(dt: datetime) -> str:
    """Format a timezone-aware datetime as YYYY-MM-DDTHH:MM:SSZ."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def next_six_hour_utc(dt: datetime) -> datetime:
    """Return the exact 6-hour UTC slot at or immediately after dt."""
    utc_dt = dt.astimezone(timezone.utc)
    floored = utc_dt.replace(hour=(utc_dt.hour // 6) * 6, minute=0, second=0, microsecond=0)
    if utc_dt > floored:
        return floored + timedelta(hours=6)
    return floored


def get_connection(db_path: Path | str) -> sqlite3.Connection:
    """Open a SQLite connection with foreign-key enforcement enabled."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def initialize_database(
    db_path: Path | str,
    schema_path: Path | str = DEFAULT_SCHEMA_PATH,
) -> Path:
    """Create the SQLite database file and apply schema.sql."""
    target = Path(db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    schema_sql = Path(schema_path).read_text(encoding="utf-8")

    with get_connection(target) as conn:
        conn.executescript(schema_sql)
    return target


def seed_locations(
    conn: sqlite3.Connection,
    locations: Iterable[tuple[str, float, float]] = APPROVED_LOCATIONS,
) -> None:
    """Insert the fixed study locations if they do not already exist."""
    now_utc = format_utc(datetime.now(timezone.utc))
    conn.executemany(
        """
        INSERT OR IGNORE INTO locations (
            city_name, requested_latitude, requested_longitude, model_domain, created_at_utc
        ) VALUES (?, ?, ?, 'cams_global', ?)
        """,
        [(city, lat, lon, now_utc) for city, lat, lon in locations],
    )
    conn.commit()


def generate_collection_schedule(
    conn: sqlite3.Connection,
    start_date_utc: datetime,
    days: int = 21,
    delay_minutes: int = 30,
) -> tuple[int, int]:
    """
    Generate (days * 4) consecutive 6-hourly slots per location and T -> T+6h
    prediction opportunities. Returns (slots_created, opportunities_created).
    """
    first_slot = next_six_hour_utc(start_date_utc)
    total_steps = days * len(COLLECTION_HOURS_UTC)
    location_rows = conn.execute("SELECT location_id FROM locations ORDER BY location_id").fetchall()

    slots_before = conn.execute("SELECT COUNT(*) FROM collection_slots").fetchone()[0]
    opps_before = conn.execute("SELECT COUNT(*) FROM prediction_opportunities").fetchone()[0]

    for loc in location_rows:
        loc_id = loc["location_id"]
        slot_times: list[datetime] = []

        for step in range(total_steps):
            scheduled_dt = first_slot + timedelta(hours=6 * step)
            deadline_dt = scheduled_dt + timedelta(minutes=delay_minutes)
            slot_times.append(scheduled_dt)

            conn.execute(
                """
                INSERT OR IGNORE INTO collection_slots (
                    location_id, scheduled_collection_utc, required_valid_utc, acceptance_deadline_utc
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    loc_id,
                    format_utc(scheduled_dt),
                    format_utc(scheduled_dt),
                    format_utc(deadline_dt),
                ),
            )

        for source_dt in slot_times:
            target_dt = source_dt + timedelta(hours=6)
            if target_dt not in slot_times:
                continue
            source_str = format_utc(source_dt)
            target_str = format_utc(target_dt)

            source_row = conn.execute(
                "SELECT slot_id FROM collection_slots WHERE location_id = ? AND required_valid_utc = ?",
                (loc_id, source_str),
            ).fetchone()
            target_row = conn.execute(
                "SELECT slot_id FROM collection_slots WHERE location_id = ? AND required_valid_utc = ?",
                (loc_id, target_str),
            ).fetchone()

            if source_row and target_row:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO prediction_opportunities (
                        location_id, source_slot_id, target_slot_id, source_valid_utc, target_valid_utc
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (loc_id, source_row["slot_id"], target_row["slot_id"], source_str, target_str),
                )

    conn.commit()
    slots_after = conn.execute("SELECT COUNT(*) FROM collection_slots").fetchone()[0]
    opps_after = conn.execute("SELECT COUNT(*) FROM prediction_opportunities").fetchone()[0]
    return slots_after - slots_before, opps_after - opps_before


if __name__ == "__main__":
    db_file = initialize_database(DEV_DB_PATH)
    with get_connection(db_file) as connection:
        seed_locations(connection)
        added_slots, added_opps = generate_collection_schedule(
            connection,
            start_date_utc=datetime.now(timezone.utc),
            days=21,
            delay_minutes=30,
        )
        loc_count = connection.execute("SELECT COUNT(*) FROM locations").fetchone()[0]
        slot_count = connection.execute("SELECT COUNT(*) FROM collection_slots").fetchone()[0]
        opp_count = connection.execute("SELECT COUNT(*) FROM prediction_opportunities").fetchone()[0]

    print(f"Database ready: {db_file}")
    print(f"Locations: {loc_count} | Slots: {slot_count} (+{added_slots}) | Opportunities: {opp_count} (+{added_opps})")