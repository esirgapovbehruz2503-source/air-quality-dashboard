from datetime import datetime, timezone
import math
from typing import Any


class ValidationError(ValueError):
    """Raised when an API payload fails schema, timestamp, or value checks."""


def normalize_utc_timestamp(raw_ts: str) -> str:
    """Normalize an ISO-8601 UTC timestamp string to YYYY-MM-DDTHH:MM:SSZ."""
    if not isinstance(raw_ts, str) or not raw_ts.strip():
        raise ValidationError("Timestamp must be a non-empty string.")
    cleaned = raw_ts.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError as exc:
        raise ValidationError(f"Invalid ISO timestamp: {raw_ts}") from exc

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_non_negative_number(value: Any, field_name: str, required: bool) -> float | None:
    if value is None:
        if required:
            raise ValidationError(f"Missing required field: {field_name}")
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"Field '{field_name}' must be numeric, got {type(value).__name__}")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0.0:
        raise ValidationError(f"Field '{field_name}' must be finite and non-negative, got {value}")
    return numeric


def parse_open_meteo_payload(payload: dict[str, Any], required_valid_utc: str) -> dict[str, Any]:
    """
    Validate an Open-Meteo Air Quality JSON response and extract the record
    matching required_valid_utc exactly.
    """
    if not isinstance(payload, dict):
        raise ValidationError("Payload must be a JSON object.")

    try:
        ret_lat = float(payload["latitude"])
        ret_lon = float(payload["longitude"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError("Missing or invalid returned grid coordinates.") from exc

    if not (-90.0 <= ret_lat <= 90.0) or not (-180.0 <= ret_lon <= 180.0):
        raise ValidationError(f"Returned coordinates out of range: ({ret_lat}, {ret_lon})")

    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        raise ValidationError("Missing 'hourly' object in payload.")

    times = hourly.get("time")
    pm2_5_list = hourly.get("pm2_5")
    pm10_list = hourly.get("pm10")
    aqi_list = hourly.get("european_aqi")

    if not isinstance(times, list) or not isinstance(pm2_5_list, list):
        raise ValidationError("Missing 'hourly.time' or 'hourly.pm2_5' arrays.")

    target_norm = normalize_utc_timestamp(required_valid_utc)
    matched_idx = None
    for idx, raw_t in enumerate(times):
        if normalize_utc_timestamp(str(raw_t)) == target_norm:
            matched_idx = idx
            break

    if matched_idx is None:
        raise ValidationError(f"Required valid time {target_norm} not found in API response.")

    if matched_idx >= len(pm2_5_list):
        raise ValidationError("API 'pm2_5' array is shorter than 'time' array.")

    pm2_5 = _validate_non_negative_number(pm2_5_list[matched_idx], "pm2_5", required=True)
    pm10 = (
        _validate_non_negative_number(pm10_list[matched_idx], "pm10", required=False)
        if isinstance(pm10_list, list) and matched_idx < len(pm10_list)
        else None
    )
    european_aqi = (
        _validate_non_negative_number(aqi_list[matched_idx], "european_aqi", required=False)
        if isinstance(aqi_list, list) and matched_idx < len(aqi_list)
        else None
    )

    return {
        "valid_utc": target_norm,
        "returned_latitude": ret_lat,
        "returned_longitude": ret_lon,
        "pm2_5": pm2_5,
        "pm10": pm10,
        "european_aqi": european_aqi,
    }