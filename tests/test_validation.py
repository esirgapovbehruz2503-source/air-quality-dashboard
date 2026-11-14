import pytest

from src.validation import ValidationError, parse_open_meteo_payload


def _sample_payload() -> dict:
    return {
        "latitude": 51.5,
        "longitude": -0.1,
        "hourly": {
            "time": [
                "2026-11-14T00:00",
                "2026-11-14T06:00",
                "2026-11-14T12:00",
            ],
            "pm2_5": [10.4, 15.2, 18.7],
            "pm10": [14.0, 19.5, 22.1],
            "european_aqi": [25, 40, 54],
        },
    }


def test_parse_valid_open_meteo_payload():
    parsed = parse_open_meteo_payload(_sample_payload(), "2026-11-14T06:00:00Z")
    assert parsed["valid_utc"] == "2026-11-14T06:00:00Z"
    assert parsed["pm2_5"] == 15.2
    assert parsed["pm10"] == 19.5
    assert parsed["european_aqi"] == 40.0
    assert parsed["returned_latitude"] == 51.5
    assert parsed["returned_longitude"] == -0.1


def test_rejects_missing_target_timestamp():
    with pytest.raises(ValidationError, match="not found"):
        parse_open_meteo_payload(_sample_payload(), "2026-11-14T18:00:00Z")


def test_rejects_negative_or_missing_pm25():
    bad_negative = _sample_payload()
    bad_negative["hourly"]["pm2_5"][1] = -3.0
    with pytest.raises(ValidationError, match="non-negative"):
        parse_open_meteo_payload(bad_negative, "2026-11-14T06:00:00Z")

    bad_missing = _sample_payload()
    bad_missing["hourly"]["pm2_5"][1] = None
    with pytest.raises(ValidationError, match="Missing required field"):
        parse_open_meteo_payload(bad_missing, "2026-11-14T06:00:00Z")